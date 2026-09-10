"""Fail-open durable collection-gap and staging-incident writer.

Producers and control Lambdas share this helper. An S3 write failure must
never change a producer's original success/failure path.

Operational producers do not discover collection_epoch_id. They persist
UNBOUND_COLLECTION_INCIDENT records under metadata/incidents/. coverage_control
binds those incidents to the current epoch as COLLECTION_GAP records.

Direct S3 historical CONTROL-PLANE writes are create-once: PutObject uses
IfNoneMatch='*' so a versioned bucket cannot mint a new version merely to
re-state an immutable record. 412 + identical identity is idempotent
success. 412 + conflicting content fails closed. 409 retries the
conditional put; it is never a silent success.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import ClientError

from wilvor_historical.coverage_contracts import (
    CollectionGapRecord,
    CollectionGapResolutionRecord,
    UnboundCollectionIncident,
)
from wilvor_historical.time import parse_utc_datetime


LOGGER = logging.getLogger(__name__)

CREATED = "CREATED"
ALREADY_PERSISTED = "ALREADY_PERSISTED"
_WRITE_TIME_FIELDS = frozenset({"created_at_utc", "detected_at_utc"})
_MAX_CONDITIONAL_RETRIES = 3


class ImmutableControlWriteError(RuntimeError):
    """Raised when a create-once control write cannot complete safely."""


class ImmutableControlConflictError(ImmutableControlWriteError):
    """Existing object at a deterministic key does not match the expected record."""


@dataclass(frozen=True)
class ImmutablePutResult:
    key: str
    outcome: str


def historical_facts_bucket_name() -> str:
    return (os.environ.get("HISTORICAL_FACTS_BUCKET_NAME") or "").strip()


def gap_object_key(gap: CollectionGapRecord) -> str:
    created = parse_utc_datetime(gap.created_at_utc)
    return (
        "metadata/gaps/"
        f"year={created.year:04d}/month={created.month:02d}/day={created.day:02d}/"
        f"{gap.dedup_id}.json"
    )


def incident_object_key(incident: UnboundCollectionIncident) -> str:
    created = parse_utc_datetime(incident.created_at_utc)
    return (
        "metadata/incidents/"
        f"year={created.year:04d}/month={created.month:02d}/day={created.day:02d}/"
        f"{incident.dedup_id}.json"
    )


def resolution_object_key(
    resolution: CollectionGapResolutionRecord,
    *,
    partition_utc: str,
) -> str:
    created = parse_utc_datetime(partition_utc)
    return (
        "metadata/resolutions/"
        f"year={created.year:04d}/month={created.month:02d}/day={created.day:02d}/"
        f"{resolution.dedup_id}.json"
    )


def canonical_control_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _client_error_status(exc: ClientError) -> tuple[int | None, str]:
    response = getattr(exc, "response", None) or {}
    error = response.get("Error") or {}
    meta = response.get("ResponseMetadata") or {}
    code = str(error.get("Code") or "")
    status = meta.get("HTTPStatusCode")
    try:
        parsed = int(status) if status is not None else None
    except (TypeError, ValueError):
        parsed = None
    if parsed is None and code.isdigit():
        parsed = int(code)
    return parsed, code


def is_precondition_failed(exc: BaseException) -> bool:
    if not isinstance(exc, ClientError):
        return False
    status, code = _client_error_status(exc)
    return status == 412 or code in {"PreconditionFailed", "412"}


def is_conditional_request_conflict(exc: BaseException) -> bool:
    if not isinstance(exc, ClientError):
        return False
    status, code = _client_error_status(exc)
    return status == 409 or code in {"ConditionalRequestConflict", "Conflict"}


def _read_body(body: Any) -> bytes:
    if hasattr(body, "read"):
        data = body.read()
    else:
        data = body
    if isinstance(data, str):
        return data.encode("utf-8")
    return bytes(data)


def _parse_json_bytes(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"))


def _without_write_time(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in _WRITE_TIME_FIELDS}


def immutable_records_equivalent(expected: Any, existing: Any) -> bool:
    """True when an existing object is the same immutable control record.

    Coverage intervals and collection gaps may be retried later with a new
    control-clock created_at/detected_at. Those write-time fields are not
    identity. Every other field must match. Staging incidents, epoch,
    activation, deactivation, and pending probes compare equal in full.
    """

    if expected == existing:
        return True
    if not isinstance(expected, dict) or not isinstance(existing, dict):
        return False
    record_type = expected.get("record_type")
    if record_type != existing.get("record_type"):
        return False
    if record_type in {
        "COVERAGE_INTERVAL",
        "COLLECTION_GAP",
        "COLLECTION_GAP_RESOLUTION",
    }:
        return _without_write_time(expected) == _without_write_time(existing)
    return False


def put_immutable_json(
    *,
    s3_client: Any,
    bucket_name: str,
    key: str,
    payload: dict[str, Any],
    max_conflict_retries: int = _MAX_CONDITIONAL_RETRIES,
) -> ImmutablePutResult:
    """Atomically create a control-plane JSON object, or confirm it already exists."""

    if not bucket_name:
        raise ImmutableControlWriteError("historical facts bucket is required")
    if s3_client is None:
        raise ImmutableControlWriteError("s3_client is required")
    body = canonical_control_body(payload)
    expected = _parse_json_bytes(body)
    retries = 0
    while True:
        try:
            s3_client.put_object(
                Bucket=bucket_name,
                Key=key,
                Body=body,
                ContentType="application/json",
                IfNoneMatch="*",
            )
            return ImmutablePutResult(key=key, outcome=CREATED)
        except ClientError as exc:
            if is_conditional_request_conflict(exc):
                retries += 1
                if retries > max_conflict_retries:
                    raise ImmutableControlWriteError(
                        f"conditional put retries exhausted key={key}"
                    ) from exc
                continue
            if not is_precondition_failed(exc):
                raise
            try:
                response = s3_client.get_object(Bucket=bucket_name, Key=key)
                existing_raw = _read_body(response["Body"])
                existing = _parse_json_bytes(existing_raw)
            except Exception as read_exc:
                raise ImmutableControlWriteError(
                    f"412 for key={key} but existing object could not be read"
                ) from read_exc
            if immutable_records_equivalent(expected, existing):
                return ImmutablePutResult(key=key, outcome=ALREADY_PERSISTED)
            raise ImmutableControlConflictError(
                f"immutable control record conflict key={key}"
            ) from exc


def _put_json_fail_open(
    *,
    key: str,
    payload: dict[str, Any],
    s3_client: Any,
    bucket_name: str | None,
) -> str | None:
    bucket = bucket_name if bucket_name is not None else historical_facts_bucket_name()
    if not bucket:
        return None
    try:
        client = s3_client
        if client is None:
            import boto3

            client = boto3.client("s3")
        return put_immutable_json(
            s3_client=client,
            bucket_name=bucket,
            key=key,
            payload=payload,
        ).key
    except Exception:
        LOGGER.exception("fail-open historical control write failed key=%s", key)
        return None


def write_collection_gap_fail_open(
    gap: CollectionGapRecord,
    *,
    s3_client: Any = None,
    bucket_name: str | None = None,
) -> str | None:
    return _put_json_fail_open(
        key=gap_object_key(gap),
        payload=gap.to_dict(),
        s3_client=s3_client,
        bucket_name=bucket_name,
    )


def write_unbound_incident_fail_open(
    incident: UnboundCollectionIncident,
    *,
    s3_client: Any = None,
    bucket_name: str | None = None,
) -> str | None:
    return _put_json_fail_open(
        key=incident_object_key(incident),
        payload=incident.to_dict(),
        s3_client=s3_client,
        bucket_name=bucket_name,
    )


def write_bound_gap(
    gap: CollectionGapRecord,
    *,
    s3_client: Any,
    bucket_name: str,
) -> ImmutablePutResult:
    """Hard create-once bind used by coverage_control. Failure must propagate."""

    return put_immutable_json(
        s3_client=s3_client,
        bucket_name=bucket_name,
        key=gap_object_key(gap),
        payload=gap.to_dict(),
    )


def write_gap_resolution(
    resolution: CollectionGapResolutionRecord,
    *,
    s3_client: Any,
    bucket_name: str,
    partition_utc: str,
) -> ImmutablePutResult:
    """Hard create-once resolution. Original gap objects are never mutated."""

    return put_immutable_json(
        s3_client=s3_client,
        bucket_name=bucket_name,
        key=resolution_object_key(resolution, partition_utc=partition_utc),
        payload=resolution.to_dict(),
    )
