from __future__ import annotations

import time
import uuid

import pytest

from integration_support import (
    decimal_to_native,
    delete_item,
    get_item,
    invoke_lambda,
    kinesis_event,
    put_json_record,
    recursively_contains,
    utc_now,
    wait_for_item,
    wait_for_s3_json,
)


pytestmark = pytest.mark.integration


def build_raw_aircraft_event(
    *,
    aircraft_id: str,
    correlation_id: str,
    last_contact: int,
) -> dict:
    return {
        "schema_version": "opensky_aircraft_raw.v1",
        "source": "opensky",
        "poll_id": correlation_id,
        "raw_index": 0,
        "raw_state_vector": [
            aircraft_id,
            f"IT{aircraft_id.upper()}",
            "Integration Test",
            last_contact - 1,
            last_contact,
            -122.375,
            37.618,
            1200.0,
            False,
            135.0,
            91.0,
            1.5,
            None,
            1250.0,
            "1200",
            False,
            0,
        ],
    }


def test_raw_stream_reaches_current_state_and_rejects_stale_state(
    terraform_outputs,
    aws_client,
    aws_resource,
    integration_timeout,
    integration_interval,
    cleanup_registry,
):
    aircraft_id = f"f{uuid.uuid4().hex[:5]}"
    correlation_id = f"it-aircraft-{uuid.uuid4().hex}"
    last_contact = int(time.time()) - 5

    raw_event = build_raw_aircraft_event(
        aircraft_id=aircraft_id,
        correlation_id=correlation_id,
        last_contact=last_contact,
    )

    table_name = terraform_outputs[
        "aircraft_current_state_table_name"
    ]
    key = {"icao24": aircraft_id}

    cleanup_registry.add(
        lambda: delete_item(
            aws_resource("dynamodb"),
            table_name=table_name,
            key=key,
        )
    )

    put_json_record(
        aws_client("kinesis"),
        stream_name=terraform_outputs[
            "aircraft_raw_stream_name"
        ],
        partition_key=aircraft_id,
        payload=raw_event,
    )

    item = wait_for_item(
        aws_resource("dynamodb"),
        table_name=table_name,
        key=key,
        predicate=lambda candidate: (
            int(candidate.get("last_contact_epoch", 0))
            == last_contact
        ),
        timeout_seconds=integration_timeout,
        interval_seconds=integration_interval,
    )

    assert item["schema_version"] == "aircraft_current_state.v1"
    assert item["icao24"] == aircraft_id
    assert item["callsign"] == f"IT{aircraft_id.upper()}"
    assert float(item["latitude"]) == pytest.approx(37.618)
    assert float(item["longitude"]) == pytest.approx(-122.375)
    assert item["has_position"] is True

    stale_item = decimal_to_native(item)
    stale_item["last_contact_epoch"] = last_contact - 60
    stale_item["last_contact_utc"] = None
    stale_item["position_time_epoch"] = last_contact - 61
    stale_item["position_time_utc"] = None

    result = invoke_lambda(
        aws_client("lambda"),
        function_name=terraform_outputs[
            "aircraft_current_state_writer_lambda_name"
        ],
        event=kinesis_event(stale_item),
    )

    assert result == {"batchItemFailures": []}

    after = get_item(
        aws_resource("dynamodb"),
        table_name=table_name,
        key=key,
    )

    assert after is not None
    assert int(after["last_contact_epoch"]) == last_contact


def test_invalid_raw_aircraft_record_is_archived(
    terraform_outputs,
    aws_client,
    integration_timeout,
    integration_interval,
    cleanup_registry,
):
    marker = f"it-aircraft-bad-{uuid.uuid4().hex}"
    started_at = utc_now()

    invalid_event = {
        "schema_version": "opensky_aircraft_raw.v1",
        "source": "opensky",
        "poll_id": marker,
        "raw_index": 0,
        "raw_state_vector": ["bad-vector"],
    }

    result = invoke_lambda(
        aws_client("lambda"),
        function_name=terraform_outputs[
            "aircraft_raw_processor_lambda_name"
        ],
        event=kinesis_event(invalid_event),
    )

    assert result == {"batchItemFailures": []}

    bucket = terraform_outputs["aircraft_archive_bucket_name"]
    key, body = wait_for_s3_json(
        aws_client("s3"),
        bucket=bucket,
        prefix="bad-records/source=opensky/",
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

    assert body["schema_version"] == (
        "aircraft_bad_record_batch.v1"
    )
    assert body["record_count"] == 1
    assert body["records"][0]["stage"] == (
        "aircraft_raw_processor.validate_and_map"
    )
