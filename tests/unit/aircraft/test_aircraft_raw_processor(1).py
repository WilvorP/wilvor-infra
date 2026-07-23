from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


@pytest.fixture
def raw_processor(load_repo_module, monkeypatch):
    module = load_repo_module(
        "unit_aircraft_raw_processor_app",
        "functions/aircraft_raw_processor/app.py",
    )

    monkeypatch.setenv("AIRCRAFT_ARCHIVE_BUCKET", "test-aircraft-archive")
    monkeypatch.setenv("AIRCRAFT_CLEAN_STREAM_NAME", "test-aircraft-clean")
    return module


def test_decode_kinesis_record(raw_processor, kinesis_record_factory):
    record = kinesis_record_factory({"hello": "world"}, "101")

    assert raw_processor.decode_kinesis_record(record) == {
        "hello": "world"
    }
    assert raw_processor.get_sequence_number(record) == "101"


def test_validate_raw_event_envelope_accepts_expected_contract(
    raw_processor,
    raw_opensky_event,
):
    assert raw_processor.validate_raw_event_envelope(raw_opensky_event) == []


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("not-an-object", ["raw_event_not_object"]),
        (
            {
                "source": "opensky",
                "raw_state_vector": [],
            },
            ["invalid_or_missing_schema_version"],
        ),
        (
            {
                "schema_version": "opensky_aircraft_raw.v1",
                "source": "other",
                "raw_state_vector": [],
            },
            ["invalid_or_missing_source"],
        ),
        (
            {
                "schema_version": "opensky_aircraft_raw.v1",
                "source": "opensky",
            },
            ["missing_raw_state_vector"],
        ),
    ],
)
def test_validate_raw_event_envelope_rejects_invalid_contract(
    raw_processor,
    payload,
    expected,
):
    assert raw_processor.validate_raw_event_envelope(payload) == expected


def test_build_clean_kinesis_record_uses_icao24_partition_key(
    raw_processor,
    clean_aircraft_record,
):
    result = raw_processor.build_clean_kinesis_record(
        clean_record=clean_aircraft_record,
        sequence_number="55",
    )

    assert result["PartitionKey"] == "abc123"
    assert result["_source_sequence_number"] == "55"
    assert json.loads(result["Data"].decode("utf-8")) == clean_aircraft_record


def test_archive_bad_records_skips_s3_when_empty(raw_processor):
    assert (
        raw_processor.archive_bad_records(
            bad_records=[],
            invocation_id="request-1",
        )
        is None
    )


def test_archive_bad_records_writes_structured_batch(
    raw_processor,
    monkeypatch,
):
    captured = {}

    class FakeS3:
        def put_object(self, **kwargs):
            captured.update(kwargs)

    fixed_time = datetime(2026, 7, 18, 12, 30, tzinfo=timezone.utc)

    monkeypatch.setattr(raw_processor, "s3", FakeS3())
    monkeypatch.setattr(raw_processor, "now_utc", lambda: fixed_time)
    monkeypatch.setattr(
        raw_processor,
        "now_utc_iso",
        lambda: fixed_time.isoformat(),
    )

    bad_record = {
        "schema_version": "aircraft_bad_record.v1",
        "reasons": ["decode_failed"],
    }

    key = raw_processor.archive_bad_records(
        bad_records=[bad_record],
        invocation_id="request-123",
    )

    assert key == (
        "bad-records/source=opensky/"
        "year=2026/month=07/day=18/hour=12/request-123.json"
    )
    assert captured["Bucket"] == "test-aircraft-archive"
    assert captured["Key"] == key
    assert captured["ContentType"] == "application/json"

    body = json.loads(captured["Body"].decode("utf-8"))
    assert body["schema_version"] == "aircraft_bad_record_batch.v1"
    assert body["record_count"] == 1
    assert body["records"] == [bad_record]


def test_publish_clean_records_reports_failed_source_sequences(
    raw_processor,
    monkeypatch,
):
    calls = []

    class FakeKinesis:
        def put_records(self, **kwargs):
            calls.append(kwargs)
            return {
                "FailedRecordCount": 1,
                "Records": [
                    {"SequenceNumber": "1", "ShardId": "shard-1"},
                    {
                        "ErrorCode": "ProvisionedThroughputExceededException",
                        "ErrorMessage": "throttled",
                    },
                ],
            }

    monkeypatch.setattr(raw_processor, "kinesis", FakeKinesis())

    records = [
        {
            "PartitionKey": "abc",
            "Data": b"{}",
            "_source_sequence_number": "source-1",
        },
        {
            "PartitionKey": "def",
            "Data": b"{}",
            "_source_sequence_number": "source-2",
        },
    ]

    failures = raw_processor.publish_clean_records(
        clean_records=records
    )

    assert failures == ["source-2"]
    assert len(calls) == 1
    assert calls[0]["StreamName"] == "test-aircraft-clean"
    assert all(
        "_source_sequence_number" not in record
        for record in calls[0]["Records"]
    )


def test_handler_processes_valid_record(
    raw_processor,
    raw_opensky_event,
    clean_aircraft_record,
    kinesis_record_factory,
    monkeypatch,
):
    archived = []
    published = []
    metrics = []

    monkeypatch.setattr(
        raw_processor,
        "map_raw_event_to_current_state",
        lambda event: (clean_aircraft_record, []),
    )
    monkeypatch.setattr(
        raw_processor,
        "archive_bad_records",
        lambda **kwargs: archived.append(kwargs) or None,
    )
    monkeypatch.setattr(
        raw_processor,
        "publish_clean_records",
        lambda **kwargs: published.extend(kwargs["clean_records"]) or [],
    )
    monkeypatch.setattr(
        raw_processor,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    event = {
        "Records": [
            kinesis_record_factory(raw_opensky_event, "seq-1")
        ]
    }

    result = raw_processor.handler(
        event,
        SimpleNamespace(aws_request_id="request-1"),
    )

    assert result == {"batchItemFailures": []}
    assert archived[0]["bad_records"] == []
    assert len(published) == 1
    assert published[0]["PartitionKey"] == "abc123"
    assert metrics[0]["metrics"]["ValidRecords"] == 1
    assert metrics[0]["metrics"]["RejectedRecords"] == 0


def test_handler_archives_decode_failure_without_retry(
    raw_processor,
    monkeypatch,
):
    archived = []
    metrics = []

    invalid_record = {
        "kinesis": {
            "sequenceNumber": "seq-bad",
            "data": base64.b64encode(b"not-json").decode("ascii"),
        }
    }

    monkeypatch.setattr(
        raw_processor,
        "archive_bad_records",
        lambda **kwargs: archived.extend(kwargs["bad_records"])
        or "bad-records/test.json",
    )
    monkeypatch.setattr(
        raw_processor,
        "publish_clean_records",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        raw_processor,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    result = raw_processor.handler(
        {"Records": [invalid_record]},
        SimpleNamespace(aws_request_id="request-2"),
    )

    assert result == {"batchItemFailures": []}
    assert archived[0]["stage"] == "aircraft_raw_processor.decode"
    assert "decode_failed" in archived[0]["reasons"]
    assert metrics[0]["metrics"]["RejectedRecords"] == 1
    assert metrics[0]["metrics"]["BadRecordsArchived"] == 1
    assert metrics[0]["metrics"]["BatchItemFailures"] == 0


def test_handler_returns_failed_clean_publish_sequences(
    raw_processor,
    raw_opensky_event,
    clean_aircraft_record,
    kinesis_record_factory,
    monkeypatch,
):
    monkeypatch.setattr(
        raw_processor,
        "map_raw_event_to_current_state",
        lambda event: (clean_aircraft_record, []),
    )
    monkeypatch.setattr(
        raw_processor,
        "archive_bad_records",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        raw_processor,
        "publish_clean_records",
        lambda **kwargs: ["seq-9"],
    )
    monkeypatch.setattr(raw_processor, "emit_metric", lambda **kwargs: None)

    result = raw_processor.handler(
        {
            "Records": [
                kinesis_record_factory(raw_opensky_event, "seq-9")
            ]
        },
        SimpleNamespace(aws_request_id="request-3"),
    )

    assert result == {
        "batchItemFailures": [{"itemIdentifier": "seq-9"}]
    }
