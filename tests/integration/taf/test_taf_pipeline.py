from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import timedelta

import pytest

from integration_support import (
    delete_item,
    get_item,
    invoke_lambda,
    iso_utc,
    kinesis_event,
    put_json_record,
    utc_now,
    wait_for_item,
    wait_for_s3_json,
    wait_for_weather_event,
    weather_events,
)


pytestmark = pytest.mark.integration


def build_taf_raw_event(
    *,
    station_id: str,
    correlation_id: str,
) -> dict:
    now = utc_now()
    issued_at = now - timedelta(minutes=2)
    valid_from = now - timedelta(minutes=1)
    midpoint = now + timedelta(hours=2)
    valid_to = now + timedelta(hours=6)

    return {
        "schema_version": "raw.noaa.taf.v1",
        "source": "NOAA_AVIATION_WEATHER",
        "product_type": "TAF",
        "ingestion_type": "RAW_TAF_RECORD",
        "poll_id": correlation_id,
        "received_at": iso_utc(now),
        "raw_s3_bucket": "integration-fixture",
        "raw_s3_key": f"integration/{correlation_id}.json.gz",
        "record_index": 0,
        "taf": {
            "icaoId": station_id.lower(),
            "name": "Wilvor Integration Airport",
            "issueTime": iso_utc(issued_at),
            "bulletinTime": iso_utc(
                issued_at - timedelta(minutes=1)
            ),
            "validTimeFrom": iso_utc(valid_from),
            "validTimeTo": iso_utc(valid_to),
            "mostRecent": True,
            "remarks": correlation_id,
            "lat": 37.618,
            "lon": -122.375,
            "elev": 4,
            "rawTAF": (
                f"TAF {station_id} INTEGRATION "
                f"{correlation_id}"
            ),
            "fcsts": [
                {
                    "timeFrom": iso_utc(valid_from),
                    "timeTo": iso_utc(midpoint),
                    "wdir": "VRB",
                    "wspd": 6,
                    "visib": "6+",
                    "clouds": [
                        {"cover": "SCT", "base": 2000},
                        {"cover": "BKN", "base": 5000},
                    ],
                },
                {
                    "timeFrom": iso_utc(midpoint),
                    "timeTo": iso_utc(valid_to),
                    "fcstChange": "TEMPO",
                    "probability": 30,
                    "wdir": 240,
                    "wspd": 12,
                    "wgst": 20,
                    "visib": 4,
                    "wxString": "-RA BR",
                    "clouds": [
                        {"cover": "OVC", "base": 1200},
                    ],
                },
            ],
        },
    }


def test_raw_stream_updates_latest_state_emits_event_and_is_idempotent(
    terraform_outputs,
    aws_client,
    aws_resource,
    integration_timeout,
    integration_interval,
    cleanup_registry,
):
    station_id = f"ITT{uuid.uuid4().hex[:8]}".upper()
    correlation_id = f"it-taf-{uuid.uuid4().hex}"
    started_at = utc_now()
    raw_event = build_taf_raw_event(
        station_id=station_id,
        correlation_id=correlation_id,
    )

    table_name = terraform_outputs["taf_latest_table_name"]
    key = {"station_id": station_id}

    cleanup_registry.add(
        lambda: delete_item(
            aws_resource("dynamodb"),
            table_name=table_name,
            key=key,
        )
    )

    put_json_record(
        aws_client("kinesis"),
        stream_name=terraform_outputs["taf_raw_stream_name"],
        partition_key=station_id,
        payload=raw_event,
    )

    item = wait_for_item(
        aws_resource("dynamodb"),
        table_name=table_name,
        key=key,
        predicate=lambda candidate: (
            candidate.get("poll_id") == correlation_id
            and candidate.get("last_published_source_version")
            == candidate.get("source_version")
        ),
        timeout_seconds=integration_timeout,
        interval_seconds=integration_interval,
    )

    assert item["schema_version"] == "internal.taf.v1"
    assert item["change_type"] == "NEW"
    assert int(item["period_count"]) == 2
    assert item["forecast_periods"][1]["change_type"] == "TEMPO"

    log_group = terraform_outputs[
        "weather_changed_log_group_name"
    ]
    event = wait_for_weather_event(
        aws_client("logs"),
        log_group_name=log_group,
        started_at=started_at,
        token=correlation_id,
        predicate=lambda detail: (
            detail.get("product_type") == "TAF"
            and detail.get("station_id") == station_id
            and detail.get("change_type") == "NEW"
        ),
        timeout_seconds=integration_timeout,
        interval_seconds=integration_interval,
    )

    assert event["detail"]["source_version"] == item["source_version"]
    assert len(
        weather_events(
            aws_client("logs"),
            log_group_name=log_group,
            started_at=started_at,
            token=correlation_id,
        )
    ) == 1

    original_updated_at = item["updated_at_utc"]
    duplicate_result = invoke_lambda(
        aws_client("lambda"),
        function_name=terraform_outputs[
            "taf_processor_lambda_name"
        ],
        event=kinesis_event(raw_event),
    )

    assert duplicate_result == {"batchItemFailures": []}
    time.sleep(max(10.0, integration_interval * 2))

    after = get_item(
        aws_resource("dynamodb"),
        table_name=table_name,
        key=key,
    )

    assert after is not None
    assert after["source_version"] == item["source_version"]
    assert after["updated_at_utc"] == original_updated_at
    assert len(
        weather_events(
            aws_client("logs"),
            log_group_name=log_group,
            started_at=started_at,
            token=correlation_id,
        )
    ) == 1


def _taf_bad_record_contains_marker(
    body: dict,
    marker: str,
) -> bool:
    encoded = (
        body.get("record", {})
        .get("kinesis", {})
        .get("data")
    )

    if not isinstance(encoded, str):
        return False

    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
        payload = json.loads(decoded)
    except Exception:
        return False

    return payload.get("poll_id") == marker


def test_invalid_taf_record_is_archived(
    terraform_outputs,
    aws_client,
    lambda_environment,
    integration_timeout,
    integration_interval,
    cleanup_registry,
):
    marker = f"it-taf-bad-{uuid.uuid4().hex}"
    started_at = utc_now()

    invalid_event = {
        "schema_version": "raw.noaa.taf.v1",
        "product_type": "TAF",
        "poll_id": marker,
        "received_at": iso_utc(started_at),
        "taf": {
            "icaoId": f"BAD{uuid.uuid4().hex[:6]}",
            "issueTime": iso_utc(started_at),
            "validTimeFrom": iso_utc(started_at),
            "validTimeTo": iso_utc(
                started_at + timedelta(hours=2)
            ),
            # rawTAF and fcsts are deliberately missing.
        },
    }

    result = invoke_lambda(
        aws_client("lambda"),
        function_name=terraform_outputs[
            "taf_processor_lambda_name"
        ],
        event=kinesis_event(invalid_event),
    )

    assert result == {"batchItemFailures": []}

    environment = lambda_environment(
        "taf_processor_lambda_name"
    )
    bucket = environment["BAD_RECORDS_BUCKET_NAME"]
    prefix = environment["BAD_RECORDS_PREFIX"].rstrip("/") + "/"

    key, body = wait_for_s3_json(
        aws_client("s3"),
        bucket=bucket,
        prefix=prefix,
        started_at=started_at,
        predicate=lambda candidate: (
            _taf_bad_record_contains_marker(
                candidate,
                marker,
            )
        ),
        timeout_seconds=integration_timeout,
        interval_seconds=integration_interval,
    )

    cleanup_registry.add(
        lambda: aws_client("s3").delete_object(
            Bucket=bucket,
            Key=key,
        )
    )

    assert body["schema_version"] == "taf_bad_record.v1"
    assert "missing rawTAF" in body["failure_reason"]
