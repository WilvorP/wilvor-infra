from __future__ import annotations

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
    recursively_contains,
    utc_now,
    wait_for_item,
    wait_for_s3_json,
    wait_for_weather_event,
    weather_events,
)


pytestmark = pytest.mark.integration


def build_metar_raw_event(
    *,
    station_id: str,
    correlation_id: str,
) -> dict:
    received_at = utc_now()
    observed_at = received_at - timedelta(minutes=1)

    return {
        "schema_version": "raw.noaa.metar.v1",
        "source": "NOAA_AVIATION_WEATHER",
        "product_type": "METAR",
        "ingestion_type": "RAW_METAR_FEATURE",
        "poll_id": correlation_id,
        "received_at": iso_utc(received_at),
        "raw_s3_bucket": "integration-fixture",
        "raw_s3_key": f"integration/{correlation_id}.json.gz",
        "record_index": 0,
        "feature": {
            "type": "Feature",
            "properties": {
                "icaoId": station_id.lower(),
                "name": "Wilvor Integration Airport",
                "obsTime": iso_utc(observed_at),
                "temp": 21.5,
                "dewp": 12.0,
                "wdir": 240,
                "wspd": 11,
                "wgst": 18,
                "visib": 8.0,
                "altim": 1015.2,
                "wxString": "-RA",
                "fltCat": "MVFR",
                "clouds": [
                    {"cover": "SCT", "base": 1800},
                    {"cover": "BKN", "base": 3500},
                ],
                "rawOb": (
                    f"{station_id} TEST METAR "
                    f"{correlation_id}"
                ),
            },
            "geometry": {
                "type": "Point",
                "coordinates": [-122.375, 37.618],
            },
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
    station_id = f"ITM{uuid.uuid4().hex[:8]}".upper()
    correlation_id = f"it-metar-{uuid.uuid4().hex}"
    started_at = utc_now()
    raw_event = build_metar_raw_event(
        station_id=station_id,
        correlation_id=correlation_id,
    )

    table_name = terraform_outputs["metar_latest_table_name"]
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
        stream_name=terraform_outputs["metar_raw_stream_name"],
        partition_key=station_id,
        payload=raw_event,
    )

    item = wait_for_item(
        aws_resource("dynamodb"),
        table_name=table_name,
        key=key,
        predicate=lambda candidate: (
            candidate.get("poll_id") == correlation_id
            and candidate.get(
                "last_event_published_source_version"
            )
            == candidate.get("source_version")
        ),
        timeout_seconds=integration_timeout,
        interval_seconds=integration_interval,
    )

    assert item["schema_version"] == "metar_latest.v1"
    assert item["change_type"] == "NEW"
    assert item["flight_category"] == "MVFR"
    assert float(item["temperature_c"]) == pytest.approx(21.5)
    assert "event_publish_pending" not in item

    log_group = terraform_outputs[
        "weather_changed_log_group_name"
    ]
    event = wait_for_weather_event(
        aws_client("logs"),
        log_group_name=log_group,
        started_at=started_at,
        token=correlation_id,
        predicate=lambda detail: (
            detail.get("product_type") == "METAR"
            and detail.get("station_id") == station_id
            and detail.get("change_type") == "NEW"
        ),
        timeout_seconds=integration_timeout,
        interval_seconds=integration_interval,
    )

    assert event["detail"]["source_version"] == item["source_version"]

    initial_events = weather_events(
        aws_client("logs"),
        log_group_name=log_group,
        started_at=started_at,
        token=correlation_id,
    )
    assert len(initial_events) == 1

    original_updated_at = item["updated_at_utc"]
    duplicate_result = invoke_lambda(
        aws_client("lambda"),
        function_name=terraform_outputs[
            "metar_processor_lambda_name"
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


def test_invalid_metar_record_is_archived(
    terraform_outputs,
    aws_client,
    lambda_environment,
    integration_timeout,
    integration_interval,
    cleanup_registry,
):
    marker = f"it-metar-bad-{uuid.uuid4().hex}"
    started_at = utc_now()

    invalid_event = {
        "schema_version": "raw.noaa.metar.v1",
        "product_type": "METAR",
        "poll_id": marker,
        "received_at": iso_utc(started_at),
        "feature": {
            "type": "Feature",
            "properties": {
                "icaoId": f"BAD{uuid.uuid4().hex[:6]}",
                # obsTime is deliberately missing.
            },
            "geometry": {
                "type": "Point",
                "coordinates": [-122.0, 37.0],
            },
        },
    }

    result = invoke_lambda(
        aws_client("lambda"),
        function_name=terraform_outputs[
            "metar_processor_lambda_name"
        ],
        event=kinesis_event(invalid_event),
    )

    assert result == {"batchItemFailures": []}

    environment = lambda_environment(
        "metar_processor_lambda_name"
    )
    bucket = environment["BAD_RECORDS_BUCKET_NAME"]
    prefix = environment["BAD_RECORDS_PREFIX"].rstrip("/") + "/"

    key, body = wait_for_s3_json(
        aws_client("s3"),
        bucket=bucket,
        prefix=prefix,
        started_at=started_at,
        predicate=lambda candidate: recursively_contains(
            candidate,
            marker,
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

    assert "missing observation time" in body["error"]
    assert body["event_source"] == "metar_processor"
    assert body["payload"]["poll_id"] == marker
