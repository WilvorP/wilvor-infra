"""FAA NASR runway metadata loader Lambda."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
from wilvor_weather.monitoring import emit_metric

try:
    from .cycle_control import (
        CycleDecisionType,
        decide_cycle,
    )
    from .faa_parser import (
        parse_faa_directory,
        parse_faa_zip,
    )
    from .models import RUNWAY_SCHEMA_VERSION
    from .persistence import (
        SnapshotStats,
        apply_runway_snapshot,
        archive_rejected_records,
        get_control_item,
        mark_duplicate_checked,
        mark_load_failed,
        mark_load_started,
        mark_load_succeeded,
        publish_reference_data_changed,
    )
    from .source_loader import (
        archive_source_artifact,
        prepare_source_artifact,
    )
except ImportError:
    from cycle_control import (
        CycleDecisionType,
        decide_cycle,
    )
    from faa_parser import (
        parse_faa_directory,
        parse_faa_zip,
    )
    from models import RUNWAY_SCHEMA_VERSION
    from persistence import (
        SnapshotStats,
        apply_runway_snapshot,
        archive_rejected_records,
        get_control_item,
        mark_duplicate_checked,
        mark_load_failed,
        mark_load_started,
        mark_load_succeeded,
        publish_reference_data_changed,
    )
    from source_loader import (
        archive_source_artifact,
        prepare_source_artifact,
    )


@dataclass(frozen=True)
class LoaderConfig:
    table_name: str
    archive_bucket_name: str
    supported_airport_ids: set[str]
    default_source_url: str | None
    default_source_cycle: str | None
    event_bus_name: str
    raw_prefix: str
    bad_prefix: str
    http_timeout_seconds: int
    work_directory: str

    @classmethod
    def from_environment(cls) -> "LoaderConfig":
        supported = _supported_airports()

        if not supported:
            raise RuntimeError(
                "SUPPORTED_AIRPORT_IDS_JSON must contain "
                "at least one ICAO airport ID"
            )

        return cls(
            table_name=os.environ[
                "RUNWAY_REFERENCE_TABLE_NAME"
            ],
            archive_bucket_name=os.environ[
                "ARCHIVE_BUCKET_NAME"
            ],
            supported_airport_ids=supported,
            default_source_url=(
                _optional_environment(
                    "FAA_APT_ZIP_URL"
                )
            ),
            default_source_cycle=(
                _optional_environment(
                    "DEFAULT_SOURCE_CYCLE"
                )
            ),
            event_bus_name=os.environ.get(
                "EVENT_BUS_NAME",
                "default",
            ),
            raw_prefix=os.environ.get(
                "RAW_PREFIX",
                "raw/source=faa-nasr",
            ),
            bad_prefix=os.environ.get(
                "BAD_PREFIX",
                "bad/source=faa-nasr",
            ),
            http_timeout_seconds=int(
                os.environ.get(
                    "HTTP_TIMEOUT_SECONDS",
                    "120",
                )
            ),
            work_directory=os.environ.get(
                "WORK_DIRECTORY",
                "/tmp/runway-loader",
            ),
        )


def _optional_environment(
    name: str,
) -> str | None:
    value = os.environ.get(name)

    if value and value.strip():
        return value.strip()

    return None


def _supported_airports() -> set[str]:
    raw = os.environ.get(
        "SUPPORTED_AIRPORT_IDS_JSON",
        "[]",
    )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "SUPPORTED_AIRPORT_IDS_JSON "
            "is not valid JSON"
        ) from exc

    if not isinstance(parsed, list):
        raise RuntimeError(
            "SUPPORTED_AIRPORT_IDS_JSON "
            "must be a JSON list"
        )

    return {
        str(value).strip().upper()
        for value in parsed
        if str(value).strip()
    }


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _load_id(
    event: dict[str, Any],
    context: Any,
) -> str:
    requested = str(
        event.get("load_id") or ""
    ).strip()

    if requested:
        return requested

    request_id = getattr(
        context,
        "aws_request_id",
        None,
    )

    return str(
        request_id or uuid.uuid4()
    )


def _source_cycle(
    event: dict[str, Any],
    config: LoaderConfig,
) -> str:
    value = str(
        event.get("source_cycle")
        or config.default_source_cycle
        or ""
    ).strip()

    if not value:
        raise RuntimeError(
            "source_cycle is required in the "
            "invocation event or DEFAULT_SOURCE_CYCLE"
        )

    return value


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    if value is None:
        return False

    normalized = str(value).strip().lower()

    if normalized in {
        "true",
        "1",
        "yes",
        "y",
    }:
        return True

    if normalized in {
        "false",
        "0",
        "no",
        "n",
        "",
    }:
        return False

    raise RuntimeError(
        f"cannot interpret {value!r} as a boolean"
    )


def _parse_only(
    event: dict[str, Any],
) -> dict[str, Any]:
    supported = _supported_airports()

    if event.get("source_directory"):
        result = parse_faa_directory(
            str(event["source_directory"]),
            supported_airport_ids=supported,
        )

    elif event.get("source_zip_path"):
        result = parse_faa_zip(
            str(event["source_zip_path"]),
            supported_airport_ids=supported,
        )

    else:
        raise RuntimeError(
            "source_directory or "
            "source_zip_path is required"
        )

    response = {
        "ok": True,
        "mode": "PARSE_ONLY",
        "runway_count": len(result.runways),
        "airport_count": len(
            {
                runway.airport_id
                for runway in result.runways
            }
        ),
        "rejected_record_count": len(
            result.rejected_records
        ),
        "runways": [
            runway.material_dict()
            | {
                "source_record_hash": (
                    runway.source_record_hash
                )
            }
            for runway in result.runways
        ],
        "rejected_records": [
            record.to_dict()
            for record in result.rejected_records
        ],
    }

    summary = {
        key: value
        for key, value in response.items()
        if key not in {
            "runways",
            "rejected_records",
        }
    }

    print(json.dumps(summary))
    return response


def _should_publish_event(
    decision: CycleDecisionType,
    stats: SnapshotStats,
) -> bool:
    return (
        decision
        in {
            CycleDecisionType.FIRST_LOAD,
            CycleDecisionType.NEW_CYCLE,
            CycleDecisionType.CORRECTED_PACKAGE,
        }
        or stats.materially_changed_runways > 0
    )


def run_loader(
    *,
    event: dict[str, Any],
    context: Any,
    config: LoaderConfig,
    s3_client: Any,
    table: Any,
    events_client: Any,
) -> dict[str, Any]:
    started_monotonic = time.monotonic()
    started_at = _now_utc()

    load_id = _load_id(event, context)
    source_cycle = _source_cycle(
        event,
        config,
    )

    current_control: dict[str, Any] | None = None
    active_control: dict[str, Any] | None = None
    raw_s3_uri: str | None = None
    source_hash: str | None = None
    source_bytes = 0

    try:
        artifact = prepare_source_artifact(
            event=event,
            s3_client=s3_client,
            work_directory=str(
                Path(config.work_directory)
                / load_id
            ),
            default_source_url=(
                config.default_source_url
            ),
            timeout_seconds=(
                config.http_timeout_seconds
            ),
        )

        source_hash = artifact.source_hash
        source_bytes = Path(
            artifact.local_zip_path
        ).stat().st_size

        raw_s3_key, raw_s3_uri = (
            archive_source_artifact(
                artifact=artifact,
                s3_client=s3_client,
                archive_bucket_name=(
                    config.archive_bucket_name
                ),
                raw_prefix=config.raw_prefix,
                source_cycle=source_cycle,
            )
        )

        current_control = get_control_item(
            table
        )

        decision = decide_cycle(
            current_cycle=(
                current_control or {}
            ).get("current_source_cycle"),
            current_source_hash=(
                current_control or {}
            ).get("source_zip_sha256"),
            incoming_cycle=source_cycle,
            incoming_source_hash=source_hash,
            force_reload=_as_bool(
                event.get(
                    "force_reload",
                    False,
                )
            ),
        )

        if not decision.should_process:
            assert current_control is not None

            checked_at = _now_utc().isoformat()

            mark_duplicate_checked(
                table=table,
                current_control=current_control,
                checked_at_utc=checked_at,
            )

            duration_ms = int(
                (
                    time.monotonic()
                    - started_monotonic
                )
                * 1000
            )

            emit_metric(
                pipeline="runway_metadata",
                component="runway_loader",
                stage="load",
                metrics={
                    "LoadStarted": 1,
                    "LoadSucceeded": 1,
                    "LoadFailed": 0,
                    "DuplicateCycleSkipped": 1,
                    "SourceDownloadBytes": (
                        source_bytes
                    ),
                    "LoadDurationMilliseconds": (
                        duration_ms
                    ),
                },
                properties={
                    "LoadId": load_id,
                    "SourceCycle": source_cycle,
                    "SourceHash": source_hash,
                    "RawS3Key": raw_s3_key,
                    "CycleDecision": (
                        decision.decision.value
                    ),
                },
            )

            response = {
                "ok": True,
                "skipped": True,
                "load_id": load_id,
                "source_cycle": source_cycle,
                "source_hash": source_hash,
                "cycle_decision": (
                    decision.decision.value
                ),
                "reason": decision.reason,
                "raw_s3_uri": raw_s3_uri,
            }

            print(json.dumps(response))
            return response

        active_control = mark_load_started(
            table=table,
            current_control=current_control,
            load_id=load_id,
            source_cycle=source_cycle,
            source_hash=source_hash,
            started_at_utc=(
                started_at.isoformat()
            ),
            raw_s3_uri=raw_s3_uri,
            decision=decision.decision.value,
        )

        parse_result = parse_faa_zip(
            artifact.local_zip_path,
            supported_airport_ids=(
                config.supported_airport_ids
            ),
        )

        if not parse_result.runways:
            raise RuntimeError(
                "FAA parser produced zero valid "
                "runways for the configured airports"
            )

        bad_record_key = (
            archive_rejected_records(
                s3_client=s3_client,
                archive_bucket_name=(
                    config.archive_bucket_name
                ),
                bad_prefix=config.bad_prefix,
                source_cycle=source_cycle,
                load_id=load_id,
                rejected_records=(
                    parse_result.rejected_records
                ),
                raw_s3_uri=raw_s3_uri,
                created_at_utc=(
                    _now_utc().isoformat()
                ),
            )
        )

        completed_at = (
            _now_utc().isoformat()
        )

        stats = apply_runway_snapshot(
            table=table,
            parse_result=parse_result,
            supported_airport_ids=(
                config.supported_airport_ids
            ),
            source_cycle=source_cycle,
            source_zip_hash=source_hash,
            raw_s3_uri=raw_s3_uri,
            ingested_at_utc=completed_at,
            load_id=load_id,
        )

        if stats.airports_loaded == 0:
            raise RuntimeError(
                "FAA source did not contain any "
                "configured supported airports"
            )

        event_published = False

        if _should_publish_event(
            decision.decision,
            stats,
        ):
            publish_reference_data_changed(
                events_client=events_client,
                event_bus_name=(
                    config.event_bus_name
                ),
                source_cycle=source_cycle,
                load_id=load_id,
                loaded_at_utc=completed_at,
                stats=stats,
                schema_version=(
                    RUNWAY_SCHEMA_VERSION
                ),
                cycle_decision=(
                    decision.decision.value
                ),
            )

            event_published = True

        mark_load_succeeded(
            table=table,
            current_control=active_control,
            load_id=load_id,
            source_cycle=source_cycle,
            source_hash=source_hash,
            completed_at_utc=completed_at,
            raw_s3_uri=raw_s3_uri,
            stats=stats,
            invalid_record_count=len(
                parse_result.rejected_records
            ),
        )

        duration_ms = int(
            (
                time.monotonic()
                - started_monotonic
            )
            * 1000
        )

        emit_metric(
            pipeline="runway_metadata",
            component="runway_loader",
            stage="load",
            metrics={
                "LoadStarted": 1,
                "LoadSucceeded": 1,
                "LoadFailed": 0,
                "DuplicateCycleSkipped": 0,
                "SourceDownloadBytes": (
                    source_bytes
                ),
                "AirportsLoaded": (
                    stats.airports_loaded
                ),
                "RunwaysLoaded": (
                    stats.runways_loaded
                ),
                "RunwaysNew": (
                    stats.runways_new
                ),
                "RunwaysUpdated": (
                    stats.runways_updated
                ),
                "RunwaysDeleted": (
                    stats.runways_deleted
                ),
                "RunwaysUnchanged": (
                    stats.runways_unchanged
                ),
                "InvalidRecords": len(
                    parse_result.rejected_records
                ),
                "ReferenceDataEventPublished": int(
                    event_published
                ),
                "LoadDurationMilliseconds": (
                    duration_ms
                ),
            },
            properties={
                "LoadId": load_id,
                "SourceCycle": source_cycle,
                "SourceHash": source_hash,
                "RawS3Key": raw_s3_key,
                "BadRecordS3Key": (
                    bad_record_key or ""
                ),
                "CycleDecision": (
                    decision.decision.value
                ),
            },
        )

        response = {
            "ok": True,
            "skipped": False,
            "load_id": load_id,
            "source_cycle": source_cycle,
            "source_hash": source_hash,
            "cycle_decision": (
                decision.decision.value
            ),
            "raw_s3_uri": raw_s3_uri,
            "bad_record_s3_key": (
                bad_record_key
            ),
            "invalid_record_count": len(
                parse_result.rejected_records
            ),
            "event_published": event_published,
            **stats.to_dict(),
        }

        print(json.dumps(response))
        return response

    except Exception as exc:
        failed_at = _now_utc().isoformat()

        if (
            current_control is not None
            or active_control is not None
        ):
            try:
                mark_load_failed(
                    table=table,
                    current_control=(
                        active_control
                        or current_control
                    ),
                    load_id=load_id,
                    source_cycle=source_cycle,
                    failed_at_utc=failed_at,
                    error=exc,
                )

            except Exception as control_exc:
                print(
                    json.dumps(
                        {
                            "message": (
                                "Failed to update runway "
                                "control item after load "
                                "failure"
                            ),
                            "load_id": load_id,
                            "error": str(
                                control_exc
                            ),
                        }
                    )
                )

        duration_ms = int(
            (
                time.monotonic()
                - started_monotonic
            )
            * 1000
        )

        emit_metric(
            pipeline="runway_metadata",
            component="runway_loader",
            stage="load",
            metrics={
                "LoadStarted": 1,
                "LoadSucceeded": 0,
                "LoadFailed": 1,
                "DuplicateCycleSkipped": 0,
                "SourceDownloadBytes": (
                    source_bytes
                ),
                "LoadDurationMilliseconds": (
                    duration_ms
                ),
            },
            properties={
                "LoadId": load_id,
                "SourceCycle": source_cycle,
                "SourceHash": (
                    source_hash or ""
                ),
                "RawS3Uri": raw_s3_uri or "",
                "ErrorType": (
                    exc.__class__.__name__
                ),
                "ErrorMessage": str(exc),
            },
        )

        print(
            json.dumps(
                {
                    "message": (
                        "Runway metadata load failed"
                    ),
                    "load_id": load_id,
                    "source_cycle": source_cycle,
                    "error_type": (
                        exc.__class__.__name__
                    ),
                    "error": str(exc),
                }
            )
        )

        raise


def lambda_handler(
    event: dict[str, Any],
    context: Any,
) -> dict[str, Any]:
    event = event or {}

    if (
        event.get("source_directory")
        or event.get("parse_only")
    ):
        return _parse_only(event)

    config = LoaderConfig.from_environment()

    s3_client = boto3.client("s3")
    events_client = boto3.client("events")

    table = boto3.resource(
        "dynamodb"
    ).Table(config.table_name)

    return run_loader(
        event=event,
        context=context,
        config=config,
        s3_client=s3_client,
        table=table,
        events_client=events_client,
    )