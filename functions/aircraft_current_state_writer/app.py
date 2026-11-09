import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.config import Config
from botocore.exceptions import ClientError

from wilvor_aircraft.monitoring import emit_metric
from wilvor_aircraft.schemas import AIRCRAFT_CURRENT_STATE_SCHEMA_VERSION


# One clean batch is 100 records. Each successful write also publishes one
# EventBridge event. Different aircraft are independent and run concurrently.
# Records for one aircraft stay on one thread, in batch order, and the
# DynamoDB condition still rejects an older position.
#
# Clients are created once and shared. A boto3 Session or resource per
# worker reloads the DynamoDB service model on every batch, which is
# CPU-heavy, uses hundreds of MB, and leaves the workers waiting on that
# setup instead of overlapping the network calls. Low-level clients are
# thread-safe.
DEFAULT_WRITE_WORKERS = 10
MAX_WRITE_WORKERS = 20

_CLIENT_CONFIG = Config(max_pool_connections=MAX_WRITE_WORKERS + 4)
_serializer = TypeSerializer()

dynamodb = boto3.client("dynamodb", config=_CLIENT_CONFIG)
events = boto3.client("events", config=_CLIENT_CONFIG)


def decode_kinesis_record(record: dict[str, Any]) -> dict[str, Any]:
    encoded_data = record["kinesis"]["data"]
    decoded_bytes = base64.b64decode(encoded_data)
    return json.loads(decoded_bytes.decode("utf-8"))


def get_sequence_number(record: dict[str, Any]) -> str:
    return record["kinesis"]["sequenceNumber"]


def validate_clean_record(item: Any) -> list[str]:
    if not isinstance(item, dict):
        return ["clean_record_not_object"]

    reasons: list[str] = []

    if item.get("schema_version") != AIRCRAFT_CURRENT_STATE_SCHEMA_VERSION:
        reasons.append("invalid_schema_version")

    required_fields = [
        "aircraft_id",
        "position_time_epoch",
        "position_time_utc",
        "last_contact_epoch",
        "last_contact_utc",
        "on_ground",
        "has_position",
        "position_age_seconds",
        "freshness_status",
        "state_version",
        "idempotency_key",
        "source_system",
        "source_event_time_utc",
        "received_at_utc",
        "processed_at_utc",
        "correlation_id",
        "raw_s3_uri",
        "schema_version",
        "expires_at_epoch",
    ]

    for field in required_fields:
        if item.get(field) is None:
            reasons.append(f"missing_{field}")

    if item.get("source_system") != "OPEN_SKY":
        reasons.append("invalid_source_system")

    if item.get("freshness_status") not in {
        "FRESH",
        "ACCEPTABLE",
        "STALE",
        "UNAVAILABLE",
    }:
        reasons.append("invalid_freshness_status")

    has_position = item.get("has_position")

    if not isinstance(has_position, bool):
        reasons.append("invalid_has_position")

    if has_position is True:
        conditional_position_fields = [
            "latitude",
            "longitude",
            "current_h3_cell",
            "h3_resolution",
        ]

        for field in conditional_position_fields:
            if item.get(field) is None:
                reasons.append(f"missing_{field}_when_has_position")

    if has_position is False:
        if item.get("current_h3_cell") is not None:
            reasons.append("unexpected_h3_cell_without_position")

        if item.get("h3_resolution") is not None:
            reasons.append("unexpected_h3_resolution_without_position")

    expected_version = None

    if item.get("aircraft_id") and item.get("position_time_epoch") is not None:
        expected_version = (
            f"{item['aircraft_id']}#{int(item['position_time_epoch'])}"
        )

    if expected_version and item.get("state_version") != expected_version:
        reasons.append("invalid_state_version")

    if expected_version and item.get("idempotency_key") != expected_version:
        reasons.append("invalid_idempotency_key")

    return sorted(set(reasons))


def convert_for_dynamodb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))

    if isinstance(value, dict):
        return {
            key: convert_for_dynamodb(inner_value)
            for key, inner_value in value.items()
            if inner_value is not None
        }

    if isinstance(value, list):
        return [convert_for_dynamodb(item) for item in value]

    return value


def configured_write_workers(group_count: int) -> int:
    if group_count <= 0:
        return 0

    if group_count == 1:
        return 1

    raw_value = os.environ.get("AIRCRAFT_WRITER_MAX_WORKERS", "")

    try:
        requested = int(raw_value) if raw_value else DEFAULT_WRITE_WORKERS
    except ValueError:
        requested = DEFAULT_WRITE_WORKERS

    requested = max(1, min(requested, MAX_WRITE_WORKERS))
    return min(requested, group_count)


def _to_dynamodb_item(item: dict[str, Any]) -> dict[str, Any]:
    converted = convert_for_dynamodb(item)
    return {
        key: _serializer.serialize(value)
        for key, value in converted.items()
    }


def put_current_state_item(item: dict[str, Any]) -> str:
    incoming_position_time = Decimal(
        str(item["position_time_epoch"])
    )
    names = {
        "#position_time_epoch": "position_time_epoch",
    }
    values = {
        ":incoming_position_time": _serializer.serialize(
            incoming_position_time
        ),
    }
    condition_expression = (
        "attribute_not_exists(#position_time_epoch) OR "
        "#position_time_epoch < :incoming_position_time"
    )

    # A record without a usable position may create a new no-position item,
    # or replace another no-position item, but it must not overwrite a valid
    # positioned aircraft record.
    if item["has_position"] is False:
        names["#has_position"] = "has_position"
        values[":stored_has_position_false"] = {"BOOL": False}
        condition_expression = (
            f"({condition_expression}) AND ("
            "attribute_not_exists(#has_position) OR "
            "#has_position = :stored_has_position_false)"
        )

    try:
        dynamodb.put_item(
            TableName=os.environ["AIRCRAFT_CURRENT_STATE_TABLE_NAME"],
            Item=_to_dynamodb_item(item),
            ConditionExpression=condition_expression,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

        return "written"

    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")

        if error_code == "ConditionalCheckFailedException":
            return "skipped_stale_duplicate_or_position_protected"

        raise


def _failure_outcome(
    sequence_number: str,
    exc: Exception,
) -> dict[str, str]:
    print(
        json.dumps(
            {
                "event": "aircraft_current_state_write_failed",
                "sequence_number": sequence_number,
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        )
    )

    return {
        "outcome": "failed",
        "sequence_number": sequence_number,
    }


def apply_clean_record(
    sequence_number: str,
    clean_record: dict[str, Any],
) -> dict[str, str]:
    try:
        result = put_current_state_item(clean_record)

        if result == "written":
            publish_aircraft_state_updated(clean_record)
            return {
                "outcome": "written",
                "sequence_number": sequence_number,
            }

        if result == "skipped_stale_duplicate_or_position_protected":
            return {
                "outcome": "skipped",
                "sequence_number": sequence_number,
            }

        raise RuntimeError(
            f"unexpected_current_state_write_result:{result}"
        )

    except Exception as exc:
        return _failure_outcome(sequence_number, exc)


def apply_aircraft_group(
    records: list[tuple[str, dict[str, Any]]],
) -> list[dict[str, str]]:
    # One aircraft is applied in arrival order so an older position in this
    # batch cannot race ahead of a newer one. Kinesis partial-batch retry
    # restarts at the lowest failed sequence and includes every later
    # record. The less-than condition skips an equal or older position, so
    # a sibling written in this batch is not republished and an older state
    # is not restored. A record whose PutItem succeeded and whose PutEvents
    # then failed is skipped on that retry as well, and its event is not sent.
    return [
        apply_clean_record(sequence_number, clean_record)
        for sequence_number, clean_record in records
    ]


def handler(event, context):
    batch_item_failures: list[dict[str, str]] = []

    total_records = 0
    decoded_records = 0
    valid_records = 0
    written_records = 0
    skipped_stale_records = 0
    rejected_records = 0
    failed_records = 0
    published_events = 0
    groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}

    for record in event.get("Records", []):
        total_records += 1
        sequence_number = get_sequence_number(record)

        try:
            clean_record = decode_kinesis_record(record)
            decoded_records += 1

            validation_reasons = validate_clean_record(clean_record)

            if validation_reasons:
                rejected_records += 1
                print(
                    json.dumps(
                        {
                            "event": "aircraft_current_state_record_rejected",
                            "sequence_number": sequence_number,
                            "reasons": validation_reasons,
                            "record": clean_record,
                        }
                    )
                )
                continue

            valid_records += 1
            aircraft_id = str(clean_record["aircraft_id"])
            groups.setdefault(aircraft_id, []).append(
                (sequence_number, clean_record)
            )

        except Exception as exc:
            failed_records += 1
            batch_item_failures.append(
                {"itemIdentifier": sequence_number}
            )
            print(
                json.dumps(
                    {
                        "event": "aircraft_current_state_write_failed",
                        "sequence_number": sequence_number,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                )
            )

    write_workers = configured_write_workers(len(groups))

    if groups:
        with ThreadPoolExecutor(max_workers=write_workers) as executor:
            futures = {
                executor.submit(apply_aircraft_group, group): group
                for group in groups.values()
            }

            for future in as_completed(futures):
                group = futures[future]

                try:
                    outcomes = future.result()
                except Exception as exc:
                    outcomes = [
                        _failure_outcome(sequence_number, exc)
                        for sequence_number, _clean_record in group
                    ]

                for outcome in outcomes:
                    if outcome["outcome"] == "written":
                        written_records += 1
                        published_events += 1
                    elif outcome["outcome"] == "skipped":
                        skipped_stale_records += 1
                    else:
                        failed_records += 1
                        batch_item_failures.append(
                            {
                                "itemIdentifier": outcome[
                                    "sequence_number"
                                ]
                            }
                        )

    summary = {
        "event": "aircraft_current_state_batch_processed",
        "total_records": total_records,
        "decoded_records": decoded_records,
        "valid_records": valid_records,
        "written_records": written_records,
        "skipped_stale_records": skipped_stale_records,
        "rejected_records": rejected_records,
        "failed_records": failed_records,
        "batch_item_failures": len(batch_item_failures),
        "published_events": published_events,
        "write_workers": write_workers,
    }

    print(json.dumps(summary))

    emit_metric(
        pipeline="aircraft",
        component="current_state_writer",
        stage="clean_to_dynamodb",
        metrics={
            "TotalRecords": total_records,
            "DecodedRecords": decoded_records,
            "ValidRecords": valid_records,
            "WrittenRecords": written_records,
            "SkippedStaleRecords": skipped_stale_records,
            "RejectedRecords": rejected_records,
            "FailedRecords": failed_records,
            "BatchItemFailures": len(batch_item_failures),
            "published_events": published_events,
        },
        properties={
            "event": "aircraft_current_state_writer_metrics",
        },
    )

    return {
        "batchItemFailures": batch_item_failures,
    } 

def publish_aircraft_state_updated(
    item: dict[str, Any],
) -> str:
    detail = {
        "aircraft_id": item["aircraft_id"],
        "state_version": item["state_version"],
        "idempotency_key": item["idempotency_key"],

        "position_time_epoch": item["position_time_epoch"],
        "position_time_utc": item["position_time_utc"],

        "has_position": item["has_position"],
        "current_h3_cell": item.get("current_h3_cell"),
        "h3_resolution": item.get("h3_resolution"),

        "freshness_status": item["freshness_status"],

        "correlation_id": item["correlation_id"],
        "schema_version": item["schema_version"],
    }

    response = events.put_events(
        Entries=[
            {
                "EventBusName": os.environ.get(
                    "EVENT_BUS_NAME",
                    "default",
                ),
                "Source": "wilvor.aircraft",
                "DetailType": "aircraft.state.updated",
                "Detail": json.dumps(detail),
            }
        ]
    )

    if int(response.get("FailedEntryCount", 0)) > 0:
        raise RuntimeError(
            "EventBridge failed to publish aircraft.state.updated: "
            f"{response.get('Entries')}"
        )

    return response["Entries"][0]["EventId"]