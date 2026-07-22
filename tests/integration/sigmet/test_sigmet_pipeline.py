from __future__ import annotations

import hashlib
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
    recursively_contains,
    utc_now,
    wait_for_item,
    wait_for_s3_json,
    wait_for_weather_event,
    weather_events,
)


pytestmark = pytest.mark.integration


def stable_hash(value) -> str:
    serialized = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def expected_hazard_id(properties: dict) -> str:
    identity_string = "|".join(
        [
            properties.get("icaoId") or "",
            properties.get("airSigmetType") or "",
            properties.get("alphaChar") or "",
            properties.get("seriesId") or "",
            properties.get("creationTime") or "",
            properties.get("validTimeFrom") or "",
            properties.get("validTimeTo") or "",
        ]
    )
    return f"sigmet-{stable_hash(identity_string)[:24]}"


def build_sigmet_raw_event(
    *,
    correlation_id: str,
) -> tuple[dict, str]:
    now = utc_now()
    issued_at = now - timedelta(minutes=2)
    valid_from = now - timedelta(minutes=1)
    valid_to = now + timedelta(hours=2)

    properties = {
        "icaoId": "KZIT",
        "airSigmetType": "SIGMET",
        "alphaChar": f"I{uuid.uuid4().hex[:5].upper()}",
        "seriesId": uuid.uuid4().hex[:10],
        "creationTime": iso_utc(issued_at),
        "validTimeFrom": iso_utc(valid_from),
        "validTimeTo": iso_utc(valid_to),
        "hazard": "TURBULENCE",
        "severity": "MOD",
        "rawAirSigmet": (
            f"WILVOR INTEGRATION SIGMET {correlation_id}"
        ),
    }

    hazard_id = expected_hazard_id(properties)

    return (
        {
            "schema_version": "raw.noaa.sigmet.v1",
            "source": "NOAA_AVIATION_WEATHER",
            "product_type": "SIGMET",
            "ingestion_type": "RAW_SIGMET_FEATURE",
            "poll_id": correlation_id,
            "received_at": iso_utc(now),
            "raw_s3_bucket": "integration-fixture",
            "raw_s3_key": f"integration/{correlation_id}.json.gz",
            "record_index": 0,
            "feature": {
                "type": "Feature",
                "properties": properties,
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-123.0, 37.0],
                            [-122.0, 37.0],
                            [-122.0, 38.0],
                            [-123.0, 38.0],
                            [-123.0, 37.0],
                        ]
                    ],
                },
            },
        },
        hazard_id,
    )


def _cleanup_sigmet(
    dynamodb_resource,
    *,
    active_table: str,
    cells_table: str,
    hazard_id: str,
) -> None:
    item = get_item(
        dynamodb_resource,
        table_name=active_table,
        key={"hazard_id": hazard_id},
    )

    if item:
        table = dynamodb_resource.Table(cells_table)

        for cell_id in item.get("h3_cells", []):
            table.delete_item(
                Key={
                    "cell_id": cell_id,
                    "hazard_id": hazard_id,
                }
            )

    delete_item(
        dynamodb_resource,
        table_name=active_table,
        key={"hazard_id": hazard_id},
    )


def test_raw_stream_updates_hazard_indexes_emits_event_and_is_idempotent(
    terraform_outputs,
    aws_client,
    aws_resource,
    integration_timeout,
    integration_interval,
    cleanup_registry,
):
    correlation_id = f"it-sigmet-{uuid.uuid4().hex}"
    started_at = utc_now()
    raw_event, hazard_id = build_sigmet_raw_event(
        correlation_id=correlation_id
    )

    active_table = terraform_outputs["active_hazards_table_name"]
    cells_table = terraform_outputs["hazard_cells_table_name"]

    cleanup_registry.add(
        lambda: _cleanup_sigmet(
            aws_resource("dynamodb"),
            active_table=active_table,
            cells_table=cells_table,
            hazard_id=hazard_id,
        )
    )

    put_json_record(
        aws_client("kinesis"),
        stream_name=terraform_outputs["sigmet_raw_stream_name"],
        partition_key=hazard_id,
        payload=raw_event,
    )

    item = wait_for_item(
        aws_resource("dynamodb"),
        table_name=active_table,
        key={"hazard_id": hazard_id},
        predicate=lambda candidate: (
            candidate.get("poll_id") == correlation_id
            and candidate.get("last_published_source_version")
            == candidate.get("source_version")
        ),
        timeout_seconds=integration_timeout,
        interval_seconds=integration_interval,
    )

    assert item["schema_version"] == "internal.sigmet.v1"
    assert item["change_type"] == "NEW"
    assert item["hazard_type"] == "TURBULENCE"
    assert item["status"] == "ACTIVE"
    assert int(item["h3_cell_count"]) > 0
    assert len(item["h3_cells"]) == int(item["h3_cell_count"])

    cells_table_resource = aws_resource("dynamodb").Table(
        cells_table
    )

    for cell_id in item["h3_cells"]:
        cell = cells_table_resource.get_item(
            Key={
                "cell_id": cell_id,
                "hazard_id": hazard_id,
            },
            ConsistentRead=True,
        ).get("Item")

        assert cell is not None
        assert cell["hazard_type"] == "TURBULENCE"

    log_group = terraform_outputs[
        "weather_changed_log_group_name"
    ]
    event = wait_for_weather_event(
        aws_client("logs"),
        log_group_name=log_group,
        started_at=started_at,
        token=hazard_id,
        predicate=lambda detail: (
            detail.get("product_type") == "SIGMET"
            and detail.get("hazard_id") == hazard_id
            and detail.get("change_type") == "NEW"
        ),
        timeout_seconds=integration_timeout,
        interval_seconds=integration_interval,
    )

    assert event["detail"]["h3_cell_count"] == int(
        item["h3_cell_count"]
    )
    assert len(
        weather_events(
            aws_client("logs"),
            log_group_name=log_group,
            started_at=started_at,
            token=hazard_id,
        )
    ) == 1

    original_updated_at = item["updated_at"]
    original_last_seen = item["last_seen_at"]

    duplicate_result = invoke_lambda(
        aws_client("lambda"),
        function_name=terraform_outputs[
            "sigmet_processor_lambda_name"
        ],
        event=kinesis_event(raw_event),
    )

    assert duplicate_result == {"batchItemFailures": []}

    after = get_item(
        aws_resource("dynamodb"),
        table_name=active_table,
        key={"hazard_id": hazard_id},
    )

    assert after is not None
    assert after["source_version"] == item["source_version"]
    assert after["updated_at"] == original_updated_at
    assert after["last_seen_at"] != original_last_seen

    time.sleep(max(10.0, integration_interval * 2))
    assert len(
        weather_events(
            aws_client("logs"),
            log_group_name=log_group,
            started_at=started_at,
            token=hazard_id,
        )
    ) == 1


def test_invalid_sigmet_record_is_archived(
    terraform_outputs,
    aws_client,
    lambda_environment,
    integration_timeout,
    integration_interval,
    cleanup_registry,
):
    marker = f"it-sigmet-bad-{uuid.uuid4().hex}"
    started_at = utc_now()

    invalid_event = {
        "schema_version": "raw.noaa.sigmet.v1",
        "product_type": "SIGMET",
        "poll_id": marker,
        "received_at": iso_utc(started_at),
        "feature": {
            "type": "Feature",
            "properties": {
                "icaoId": "KZIT",
                "airSigmetType": "SIGMET",
                "seriesId": marker,
                "creationTime": iso_utc(started_at),
                "validTimeFrom": iso_utc(started_at),
                "validTimeTo": iso_utc(
                    started_at + timedelta(hours=1)
                ),
            },
            # Point is intentionally unsupported by the SIGMET processor.
            "geometry": {
                "type": "Point",
                "coordinates": [-122.0, 37.0],
            },
        },
    }

    result = invoke_lambda(
        aws_client("lambda"),
        function_name=terraform_outputs[
            "sigmet_processor_lambda_name"
        ],
        event=kinesis_event(invalid_event),
    )

    assert result == {"batchItemFailures": []}

    environment = lambda_environment(
        "sigmet_processor_lambda_name"
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

    assert body["schema_version"] == "bad_record.v1"
    assert body["service"] == "sigmet_processor"
    assert "Unsupported geometry type: Point" in (
        body["error_message"]
    )
