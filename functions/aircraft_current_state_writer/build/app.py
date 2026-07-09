import base64
import json
import os
import time
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError


dynamodb = boto3.resource("dynamodb")


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

    if item.get("schema_version") != "aircraft_current_state.v1":
        reasons.append("invalid_schema_version")

    if not item.get("icao24"):
        reasons.append("missing_icao24")

    if item.get("last_contact_epoch") is None:
        reasons.append("missing_last_contact_epoch")

    if item.get("latitude") is None:
        reasons.append("missing_latitude")

    if item.get("longitude") is None:
        reasons.append("missing_longitude")

    return reasons


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
    incoming_last_contact = Decimal(str(item["last_contact_epoch"]))

    try:
        table.put_item(
            Item=dynamodb_item,
            ConditionExpression=(
                Attr("last_contact_epoch").not_exists()
                | Attr("last_contact_epoch").lte(incoming_last_contact)
            ),
        )
        return "written"

    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")

        if error_code == "ConditionalCheckFailedException":
            return "skipped_stale"

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
                written_records += 1
            elif result == "skipped_stale":
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

    print(
        json.dumps(
            {
                "event": "aircraft_current_state_batch_processed",
                "total_records": total_records,
                "decoded_records": decoded_records,
                "valid_records": valid_records,
                "written_records": written_records,
                "skipped_stale_records": skipped_stale_records,
                "rejected_records": rejected_records,
                "failed_records": failed_records,
                "batch_item_failures": len(batch_item_failures),
            }
        )
    )

    return {
        "batchItemFailures": batch_item_failures,
    }    