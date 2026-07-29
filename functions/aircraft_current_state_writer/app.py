import base64
import json
import os
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

from wilvor_aircraft.monitoring import emit_metric
from wilvor_aircraft.schemas import AIRCRAFT_CURRENT_STATE_SCHEMA_VERSION


dynamodb = boto3.resource("dynamodb")
events = boto3.client("events")


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


def put_current_state_item(item: dict[str, Any]) -> str:
    table_name = os.environ["AIRCRAFT_CURRENT_STATE_TABLE_NAME"]
    table = dynamodb.Table(table_name)

    dynamodb_item = convert_for_dynamodb(item)
    incoming_position_time = Decimal(
        str(item["position_time_epoch"])
    )

    ordering_condition = (
        Attr("position_time_epoch").not_exists()
        | Attr("position_time_epoch").lt(incoming_position_time)
    )

    condition_expression = ordering_condition

    # A record without a usable position may create a new no-position item,
    # or replace another no-position item, but it must not overwrite a valid
    # positioned aircraft record.
    if item["has_position"] is False:
        position_protection = (
            Attr("has_position").not_exists()
            | Attr("has_position").eq(False)
        )

        condition_expression = (
            ordering_condition & position_protection
        )

    try:
        table.put_item(
            Item=dynamodb_item,
            ConditionExpression=condition_expression,
        )

        return "written"

    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")

        if error_code == "ConditionalCheckFailedException":
            return "skipped_stale_duplicate_or_position_protected"

        raise


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
            result = put_current_state_item(clean_record)

            if result == "written":
                publish_aircraft_state_updated(clean_record)
                written_records += 1
                published_events += 1

            elif result == "skipped_stale_duplicate_or_position_protected":
                skipped_stale_records += 1

        except Exception as exc:
            failed_records += 1
            batch_item_failures.append({"itemIdentifier": sequence_number})

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