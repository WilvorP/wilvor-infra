from __future__ import annotations

import gzip
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

try:
    from .models import (
        NormalizedRunway,
        ParseResult,
        RejectedRecord,
    )
    from .source_loader import sanitize_cycle_for_key
    from .validation import canonical_physical_runway_id
except ImportError:
    from models import (
        NormalizedRunway,
        ParseResult,
        RejectedRecord,
    )
    from source_loader import sanitize_cycle_for_key
    from validation import canonical_physical_runway_id


CONTROL_AIRPORT_ID = "SYSTEM"
CONTROL_RECORD_ID = "SOURCE#FAA_NASR"

CONTROL_SCHEMA_VERSION = "internal.runway-control.v1"
AIRPORT_META_SCHEMA_VERSION = "internal.runway-airport-meta.v1"


@dataclass(frozen=True)
class DeletionProtection:
    protected_record_ids: dict[str, set[str]] = field(
        default_factory=dict
    )
    protect_all_airports: set[str] = field(
        default_factory=set
    )


@dataclass(frozen=True)
class AirportSnapshotPlan:
    new_runways: list[NormalizedRunway]
    updated_runways: list[NormalizedRunway]
    unchanged_runways: list[NormalizedRunway]
    deleted_record_ids: list[str]


@dataclass
class SnapshotStats:
    airports_loaded: int = 0
    runways_loaded: int = 0
    runways_new: int = 0
    runways_updated: int = 0
    runways_deleted: int = 0
    runways_unchanged: int = 0
    changed_airport_ids: list[str] = field(
        default_factory=list
    )

    @property
    def materially_changed_runways(self) -> int:
        return (
            self.runways_new
            + self.runways_updated
            + self.runways_deleted
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "airports_loaded": self.airports_loaded,
            "runways_loaded": self.runways_loaded,
            "runways_new": self.runways_new,
            "runways_updated": self.runways_updated,
            "runways_deleted": self.runways_deleted,
            "runways_unchanged": self.runways_unchanged,
            "materially_changed_runways": (
                self.materially_changed_runways
            ),
            "changed_airport_ids": self.changed_airport_ids,
        }


def to_dynamodb_value(value: Any) -> Any:
    """
    DynamoDB does not accept Python float values through boto3.

    Convert floats to Decimal recursively before writing.
    """

    if isinstance(value, float):
        return Decimal(str(value))

    if isinstance(value, dict):
        return {
            key: to_dynamodb_value(item)
            for key, item in value.items()
            if item is not None
        }

    if isinstance(value, list):
        return [
            to_dynamodb_value(item)
            for item in value
        ]

    if isinstance(value, tuple):
        return [
            to_dynamodb_value(item)
            for item in value
        ]

    if isinstance(value, set):
        return {
            to_dynamodb_value(item)
            for item in value
        }

    return value


def get_control_item(
    table: Any,
) -> dict[str, Any] | None:
    response = table.get_item(
        Key={
            "airport_id": CONTROL_AIRPORT_ID,
            "record_id": CONTROL_RECORD_ID,
        },
        ConsistentRead=True,
    )

    item = response.get("Item")
    return dict(item) if item else None


def put_control_item(
    table: Any,
    item: Mapping[str, Any],
) -> None:
    table.put_item(
        Item=to_dynamodb_value(dict(item))
    )


def mark_load_started(
    *,
    table: Any,
    current_control: Mapping[str, Any] | None,
    load_id: str,
    source_cycle: str,
    source_hash: str,
    started_at_utc: str,
    raw_s3_uri: str,
    decision: str,
) -> dict[str, Any]:
    item = dict(current_control or {})

    item.update(
        {
            "airport_id": CONTROL_AIRPORT_ID,
            "record_id": CONTROL_RECORD_ID,
            "load_status": "IN_PROGRESS",
            "active_load_id": load_id,
            "attempted_source_cycle": source_cycle,
            "attempted_source_zip_sha256": source_hash,
            "load_started_at_utc": started_at_utc,
            "attempted_raw_s3_uri": raw_s3_uri,
            "last_cycle_decision": decision,
            "schema_version": CONTROL_SCHEMA_VERSION,
        }
    )

    put_control_item(table, item)
    return item


def mark_load_succeeded(
    *,
    table: Any,
    current_control: Mapping[str, Any] | None,
    load_id: str,
    source_cycle: str,
    source_hash: str,
    completed_at_utc: str,
    raw_s3_uri: str,
    stats: SnapshotStats,
    invalid_record_count: int,
) -> dict[str, Any]:
    item = dict(current_control or {})

    item.update(
        {
            "airport_id": CONTROL_AIRPORT_ID,
            "record_id": CONTROL_RECORD_ID,
            "current_source_cycle": source_cycle,
            "source_zip_sha256": source_hash,
            "last_successful_load_at_utc": completed_at_utc,
            "last_successful_load_id": load_id,
            "load_status": "SUCCEEDED",
            "raw_s3_uri": raw_s3_uri,
            "total_airports_loaded": stats.airports_loaded,
            "total_runways_loaded": stats.runways_loaded,
            "invalid_record_count": invalid_record_count,
            "runways_new": stats.runways_new,
            "runways_updated": stats.runways_updated,
            "runways_deleted": stats.runways_deleted,
            "runways_unchanged": stats.runways_unchanged,
            "schema_version": CONTROL_SCHEMA_VERSION,
        }
    )

    for transient_key in (
        "active_load_id",
        "attempted_source_cycle",
        "attempted_source_zip_sha256",
        "attempted_raw_s3_uri",
    ):
        item.pop(transient_key, None)

    put_control_item(table, item)
    return item


def mark_load_failed(
    *,
    table: Any,
    current_control: Mapping[str, Any] | None,
    load_id: str,
    source_cycle: str,
    failed_at_utc: str,
    error: Exception,
) -> dict[str, Any]:
    item = dict(current_control or {})

    item.update(
        {
            "airport_id": CONTROL_AIRPORT_ID,
            "record_id": CONTROL_RECORD_ID,
            "load_status": "FAILED",
            "last_failed_load_id": load_id,
            "last_failed_source_cycle": source_cycle,
            "last_failed_at_utc": failed_at_utc,
            "last_error_type": error.__class__.__name__,
            "last_error_message": str(error)[:1000],
            "schema_version": CONTROL_SCHEMA_VERSION,
        }
    )

    put_control_item(table, item)
    return item


def mark_duplicate_checked(
    *,
    table: Any,
    current_control: Mapping[str, Any],
    checked_at_utc: str,
) -> dict[str, Any]:
    item = dict(current_control)

    item.update(
        {
            "last_checked_at_utc": checked_at_utc,
            "last_cycle_decision": "DUPLICATE",
            "schema_version": CONTROL_SCHEMA_VERSION,
        }
    )

    put_control_item(table, item)
    return item


def query_airport_items(
    table: Any,
    airport_id: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    query_arguments: dict[str, Any] = {
        "KeyConditionExpression": (
            "airport_id = :airport_id"
        ),
        "ExpressionAttributeValues": {
            ":airport_id": airport_id,
        },
        "ConsistentRead": True,
    }

    while True:
        response = table.query(**query_arguments)

        items.extend(
            dict(item)
            for item in response.get("Items", [])
        )

        last_evaluated_key = response.get(
            "LastEvaluatedKey"
        )

        if not last_evaluated_key:
            break

        query_arguments["ExclusiveStartKey"] = (
            last_evaluated_key
        )

    return items


def plan_airport_snapshot(
    *,
    existing_items: Sequence[Mapping[str, Any]],
    incoming_runways: Sequence[NormalizedRunway],
    protected_record_ids: set[str] | None = None,
    protect_all_deletes: bool = False,
) -> AirportSnapshotPlan:
    protected = protected_record_ids or set()

    existing = {
        str(item["record_id"]): item
        for item in existing_items
        if str(
            item.get("record_id", "")
        ).startswith("RUNWAY#")
    }

    incoming = {
        runway.record_id: runway
        for runway in incoming_runways
    }

    new_runways: list[NormalizedRunway] = []
    updated_runways: list[NormalizedRunway] = []
    unchanged_runways: list[NormalizedRunway] = []

    for record_id, runway in sorted(
        incoming.items()
    ):
        current = existing.get(record_id)

        if current is None:
            new_runways.append(runway)

        elif str(
            current.get("source_record_hash", "")
        ) != str(
            runway.source_record_hash or ""
        ):
            updated_runways.append(runway)

        else:
            unchanged_runways.append(runway)

    deleted_record_ids: list[str] = []

    if not protect_all_deletes:
        deleted_record_ids = sorted(
            record_id
            for record_id in existing
            if (
                record_id not in incoming
                and record_id not in protected
            )
        )

    return AirportSnapshotPlan(
        new_runways=new_runways,
        updated_runways=updated_runways,
        unchanged_runways=unchanged_runways,
        deleted_record_ids=deleted_record_ids,
    )


def _raw_value(
    raw_record: Mapping[str, Any],
    *names: str,
) -> str | None:
    normalized = {
        str(key).upper(): value
        for key, value in raw_record.items()
    }

    for name in names:
        value = normalized.get(name.upper())

        if (
            value is not None
            and str(value).strip()
        ):
            return str(value).strip().upper()

    return None


def build_deletion_protection(
    parse_result: ParseResult,
) -> DeletionProtection:
    """
    Do not delete an existing runway when its incoming
    replacement was rejected by validation.

    This preserves the last-known-good runway record.
    """

    protected: dict[str, set[str]] = defaultdict(set)
    protect_all: set[str] = set()

    for rejected in parse_result.rejected_records:
        faa_id = _raw_value(
            rejected.raw_record,
            "ARPT_ID",
            "FAA_ID",
            "LOC_ID",
        )

        if not faa_id:
            continue

        airport = parse_result.airport_references.get(
            faa_id
        )

        if airport is None:
            continue

        runway_id = _raw_value(
            rejected.raw_record,
            "RWY_ID",
            "RUNWAY_ID",
        )

        if not runway_id:
            protect_all.add(airport.icao_id)
            continue

        try:
            canonical = canonical_physical_runway_id(
                runway_id
            )
        except Exception:
            protect_all.add(airport.icao_id)
            continue

        record_id = (
            "RUNWAY#"
            + canonical.replace("/", "-").replace(" ", "")
        )

        protected[airport.icao_id].add(record_id)

    return DeletionProtection(
        protected_record_ids=dict(protected),
        protect_all_airports=protect_all,
    )


def _catalog_hash(
    runways: Sequence[NormalizedRunway],
) -> str:
    runway_hashes = sorted(
        str(runway.source_record_hash or "")
        for runway in runways
    )

    payload = json.dumps(
        runway_hashes,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()


def _airport_references_by_icao(
    parse_result: ParseResult,
) -> dict[str, Any]:
    return {
        reference.icao_id: reference
        for reference
        in parse_result.airport_references.values()
    }


def apply_runway_snapshot(
    *,
    table: Any,
    parse_result: ParseResult,
    supported_airport_ids: set[str],
    source_cycle: str,
    source_zip_hash: str,
    raw_s3_uri: str,
    ingested_at_utc: str,
    load_id: str,
) -> SnapshotStats:
    runways_by_airport: dict[
        str,
        list[NormalizedRunway],
    ] = defaultdict(list)

    for runway in parse_result.runways:
        runways_by_airport[
            runway.airport_id
        ].append(runway)

    references_by_icao = (
        _airport_references_by_icao(parse_result)
    )

    protection = build_deletion_protection(
        parse_result
    )

    airports_to_process = sorted(
        airport_id
        for airport_id in supported_airport_ids
        if airport_id in references_by_icao
    )

    stats = SnapshotStats()

    for airport_id in airports_to_process:
        incoming_runways = sorted(
            runways_by_airport.get(
                airport_id,
                [],
            ),
            key=lambda runway: runway.record_id,
        )

        existing_items = query_airport_items(
            table,
            airport_id,
        )

        plan = plan_airport_snapshot(
            existing_items=existing_items,
            incoming_runways=incoming_runways,
            protected_record_ids=(
                protection.protected_record_ids.get(
                    airport_id,
                    set(),
                )
            ),
            protect_all_deletes=(
                airport_id
                in protection.protect_all_airports
            ),
        )

        reference = references_by_icao[airport_id]

        meta_item = {
            "airport_id": airport_id,
            "record_id": "META",
            "icao_id": airport_id,
            "faa_id": reference.faa_id,
            "airport_name": reference.airport_name,
            "facility_status": reference.airport_status,
            "runway_count": len(incoming_runways),
            "catalog_hash": _catalog_hash(
                incoming_runways
            ),
            "source": "FAA_NASR",
            "source_cycle": source_cycle,
            "source_zip_sha256": source_zip_hash,
            "raw_s3_uri": raw_s3_uri,
            "schema_version": (
                AIRPORT_META_SCHEMA_VERSION
            ),
            "ingested_at_utc": ingested_at_utc,
            "load_id": load_id,
        }

        with table.batch_writer(
            overwrite_by_pkeys=[
                "airport_id",
                "record_id",
            ]
        ) as batch:
            runways_to_write = [
                *plan.new_runways,
                *plan.updated_runways,
            ]

            for runway in runways_to_write:
                item = runway.to_item(
                    source_cycle=source_cycle,
                    raw_s3_uri=raw_s3_uri,
                    ingested_at_utc=ingested_at_utc,
                )

                item["load_id"] = load_id

                batch.put_item(
                    Item=to_dynamodb_value(item)
                )

            for record_id in plan.deleted_record_ids:
                batch.delete_item(
                    Key={
                        "airport_id": airport_id,
                        "record_id": record_id,
                    }
                )

            batch.put_item(
                Item=to_dynamodb_value(meta_item)
            )

        stats.airports_loaded += 1
        stats.runways_loaded += len(incoming_runways)
        stats.runways_new += len(plan.new_runways)
        stats.runways_updated += len(
            plan.updated_runways
        )
        stats.runways_deleted += len(
            plan.deleted_record_ids
        )
        stats.runways_unchanged += len(
            plan.unchanged_runways
        )

        if (
            plan.new_runways
            or plan.updated_runways
            or plan.deleted_record_ids
        ):
            stats.changed_airport_ids.append(
                airport_id
            )

    return stats


def build_bad_record_key(
    *,
    bad_prefix: str,
    source_cycle: str,
    load_id: str,
) -> str:
    prefix = bad_prefix.strip("/")
    cycle = sanitize_cycle_for_key(source_cycle)

    return (
        f"{prefix}/"
        f"cycle={cycle}/"
        f"load={load_id}/"
        f"invalid-records.json.gz"
    )


def archive_rejected_records(
    *,
    s3_client: Any,
    archive_bucket_name: str,
    bad_prefix: str,
    source_cycle: str,
    load_id: str,
    rejected_records: Iterable[RejectedRecord],
    raw_s3_uri: str,
    created_at_utc: str,
) -> str | None:
    records = [
        record.to_dict()
        for record in rejected_records
    ]

    if not records:
        return None

    key = build_bad_record_key(
        bad_prefix=bad_prefix,
        source_cycle=source_cycle,
        load_id=load_id,
    )

    payload = {
        "schema_version": (
            "internal.runway-bad-record-batch.v1"
        ),
        "source": "FAA_NASR",
        "source_cycle": source_cycle,
        "load_id": load_id,
        "created_at_utc": created_at_utc,
        "raw_s3_uri": raw_s3_uri,
        "record_count": len(records),
        "records": records,
    }

    body = gzip.compress(
        json.dumps(
            payload,
            default=str,
        ).encode("utf-8")
    )

    s3_client.put_object(
        Bucket=archive_bucket_name,
        Key=key,
        Body=body,
        ContentType="application/json",
        ContentEncoding="gzip",
    )

    return key


def publish_reference_data_changed(
    *,
    events_client: Any,
    event_bus_name: str,
    source_cycle: str,
    load_id: str,
    loaded_at_utc: str,
    stats: SnapshotStats,
    schema_version: str,
    cycle_decision: str,
) -> None:
    detail = {
        "reference_type": "RUNWAY",
        "source": "FAA_NASR",
        "source_cycle": source_cycle,
        "load_id": load_id,
        "load_timestamp": loaded_at_utc,
        "schema_version": schema_version,
        "cycle_decision": cycle_decision,
        "airports_loaded": stats.airports_loaded,
        "changed_airport_ids": (
            stats.changed_airport_ids
        ),
        "changed_runway_count": (
            stats.materially_changed_runways
        ),
        "runways_new": stats.runways_new,
        "runways_updated": stats.runways_updated,
        "runways_deleted": stats.runways_deleted,
    }

    response = events_client.put_events(
        Entries=[
            {
                "Source": "wilvor.reference-data",
                "DetailType": (
                    "ReferenceData.changed"
                ),
                "Detail": json.dumps(detail),
                "EventBusName": event_bus_name,
            }
        ]
    )

    if int(
        response.get("FailedEntryCount", 0)
    ) > 0:
        raise RuntimeError(
            "EventBridge failed to publish "
            "ReferenceData.changed: "
            f"{response.get('Entries')}"
        )