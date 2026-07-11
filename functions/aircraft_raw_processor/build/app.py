import base64
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any
import sys
import boto3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_CODE_DIR = REPO_ROOT / "functions" / "shared"
sys.path.insert(0, str(SHARED_CODE_DIR))

from wilvor_aircraft.bad_records import build_bad_record
from wilvor_aircraft.monitoring import emit_metric
from wilvor_aircraft.opensky_mapper import map_raw_event_to_current_state
from wilvor_aircraft.schemas import OPENSKY_RAW_SCHEMA_VERSION


kinesis = boto3.client("kinesis")
s3 = boto3.client("s3")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_utc_iso() -> str:
    return now_utc().isoformat()


def chunked(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def decode_kinesis_record(record: dict[str, Any]) -> dict[str, Any]:
    encoded_data = record["kinesis"]["data"]
    decoded_bytes = base64.b64decode(encoded_data)
    return json.loads(decoded_bytes.decode("utf-8"))


def get_sequence_number(record: dict[str, Any]) -> str:
    return record["kinesis"]["sequenceNumber"]


def validate_raw_event_envelope(raw_event: Any) -> list[str]:
    if not isinstance(raw_event, dict):
        return ["raw_event_not_object"]

    reasons: list[str] = []

    if raw_event.get("schema_version") != OPENSKY_RAW_SCHEMA_VERSION:
        reasons.append("invalid_or_missing_schema_version")

    if raw_event.get("source") != "opensky":
        reasons.append("invalid_or_missing_source")

    if "raw_state_vector" not in raw_event:
        reasons.append("missing_raw_state_vector")

    return reasons


def archive_bad_records(
    *,
    bad_records: list[dict[str, Any]],
    invocation_id: str,
) -> str | None:
    if not bad_records:
        return None

    bucket = os.environ["AIRCRAFT_ARCHIVE_BUCKET"]
    now = now_utc()

    key = (
        "bad-records/source=opensky/"
        f"year={now.year:04d}/"
        f"month={now.month:02d}/"
        f"day={now.day:02d}/"
        f"hour={now.hour:02d}/"
        f"{invocation_id}.json"
    )

    body = {
        "schema_version": "aircraft_bad_record_batch.v1",
        "source": "opensky",
        "archived_at_utc": now_utc_iso(),
        "record_count": len(bad_records),
        "records": bad_records,
    }

    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(body).encode("utf-8"),
        ContentType="application/json",
    )

    return key


def build_decode_bad_record(
    *,
    record: dict[str, Any],
    reasons: list[str],
) -> dict[str, Any]:
    return build_bad_record(
        source="opensky",
        poll_id=None,
        raw_index=None,
        reasons=reasons,
        raw_record={
            "sequence_number": get_sequence_number(record),
            "encoded_data": record.get("kinesis", {}).get("data"),
        },
        stage="aircraft_raw_processor.decode",
    )


def build_validation_bad_record(
    *,
    raw_event: dict[str, Any],
    reasons: list[str],
) -> dict[str, Any]:
    return build_bad_record(
        source=raw_event.get("source", "opensky"),
        poll_id=raw_event.get("poll_id"),
        raw_index=raw_event.get("raw_index"),
        reasons=reasons,
        raw_record=raw_event,
        stage="aircraft_raw_processor.validate_and_map",
    ) 


def build_clean_kinesis_record(
    *,
    clean_record: dict[str, Any],
    sequence_number: str,
) -> dict[str, Any]:
    partition_key = clean_record.get("icao24") or clean_record.get("aircraft_id")

    return {
        "PartitionKey": str(partition_key),
        "Data": json.dumps(clean_record).encode("utf-8"),
        "_source_sequence_number": sequence_number,
    }


def publish_clean_records(
    *,
    clean_records: list[dict[str, Any]],
) -> list[str]:
    if not clean_records:
        return []

    stream_name = os.environ["AIRCRAFT_CLEAN_STREAM_NAME"]
    failed_sequence_numbers: list[str] = []

    for batch in chunked(clean_records, 500):
        put_records_payload = [
            {
                "PartitionKey": record["PartitionKey"],
                "Data": record["Data"],
            }
            for record in batch
        ]

        result = kinesis.put_records(
            StreamName=stream_name,
            Records=put_records_payload,
        )

        result_records = result.get("Records", [])

        for original_record, result_record in zip(batch, result_records):
            if "ErrorCode" in result_record:
                failed_sequence_numbers.append(
                    original_record["_source_sequence_number"]
                )

    return failed_sequence_numbers


def handler(event, context):
    invocation_id = getattr(context, "aws_request_id", None) or str(uuid.uuid4())

    batch_item_failures: list[dict[str, str]] = []
    clean_records: list[dict[str, Any]] = []
    bad_records: list[dict[str, Any]] = []
    bad_record_sequence_numbers: list[str] = []

    total_records = 0
    decoded_records = 0
    valid_records = 0
    rejected_records = 0

    for record in event.get("Records", []):
        total_records += 1
        sequence_number = get_sequence_number(record)

        try:
            raw_event = decode_kinesis_record(record)
            decoded_records += 1
        except Exception as exc:
            rejected_records += 1
            bad_records.append(
                build_decode_bad_record(
                    record=record,
                    reasons=[
                        "decode_failed",
                        type(exc).__name__,
                        str(exc),
                    ],
                )
            )
            bad_record_sequence_numbers.append(sequence_number)
            continue

        envelope_reasons = validate_raw_event_envelope(raw_event)
        if envelope_reasons:
            rejected_records += 1
            bad_records.append(
                build_validation_bad_record(
                    raw_event=raw_event,
                    reasons=envelope_reasons,
                )
            )
            bad_record_sequence_numbers.append(sequence_number)
            continue

        clean_record, mapping_reasons = map_raw_event_to_current_state(raw_event)

        if mapping_reasons:
            rejected_records += 1
            bad_records.append(
                build_validation_bad_record(
                    raw_event=raw_event,
                    reasons=mapping_reasons,
                )
            )
            bad_record_sequence_numbers.append(sequence_number)
            continue

        valid_records += 1
        clean_records.append(
            build_clean_kinesis_record(
                clean_record=clean_record,
                sequence_number=sequence_number,
            )
        )

    bad_records_s3_key = None
    bad_records_archive_failed = 0
    
    try:
        bad_records_s3_key = archive_bad_records(
            bad_records=bad_records,
            invocation_id=invocation_id,
        )
    except Exception as exc:
        bad_records_archive_failed = 1
    
        print(
            json.dumps(
                {
                    "event": "aircraft_bad_records_archive_failed",
                    "invocation_id": invocation_id,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "bad_records_count": len(bad_records),
                }
            )
        )

        for sequence_number in bad_record_sequence_numbers:
            batch_item_failures.append({"itemIdentifier": sequence_number})
    
    failed_clean_sequence_numbers = publish_clean_records(clean_records=clean_records)
    
    for sequence_number in failed_clean_sequence_numbers:
        batch_item_failures.append({"itemIdentifier": sequence_number})
    
    clean_records_failed = len(failed_clean_sequence_numbers)
    clean_records_published = valid_records - clean_records_failed
    
    summary = {
        "event": "aircraft_raw_batch_processed",
        "invocation_id": invocation_id,
        "total_records": total_records,
        "decoded_records": decoded_records,
        "valid_records": valid_records,
        "rejected_records": rejected_records,
        "clean_records_published": clean_records_published,
        "clean_records_failed": clean_records_failed,
        "bad_records_archived": len(bad_records),
        "bad_records_archive_failed": bad_records_archive_failed,
        "bad_records_s3_key": bad_records_s3_key,
        "batch_item_failures": len(batch_item_failures),
    }

    print(json.dumps(summary))
    
    emit_metric(
        pipeline="aircraft",
        component="raw_processor",
        stage="raw_to_clean",
        metrics={
            "TotalRecords": total_records,
            "DecodedRecords": decoded_records,
            "ValidRecords": valid_records,
            "RejectedRecords": rejected_records,
            "CleanRecordsPublished": clean_records_published,
            "CleanRecordsFailed": clean_records_failed,
            "BadRecordsArchived": len(bad_records),
            "BadRecordsArchiveFailed": bad_records_archive_failed,
            "BatchItemFailures": len(batch_item_failures),
        },
        properties={
            "event": "aircraft_raw_processor_metrics",
            "invocation_id": invocation_id,
        },
    )
    
    return {"batchItemFailures": batch_item_failures}    