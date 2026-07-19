from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError


def test_write_bad_record_writes_expected_payload(
    sigmet_processor,
    monkeypatch,
    fixed_sigmet_time,
    sigmet_kinesis_record_factory,
):
    captured = {}

    class FakeS3:
        def put_object(self, **kwargs):
            captured.update(kwargs)

    record = sigmet_kinesis_record_factory(
        {"bad": "payload"},
        sequence_number="seq-100",
    )

    monkeypatch.setattr(sigmet_processor, "s3", FakeS3())
    monkeypatch.setattr(
        sigmet_processor,
        "now_utc",
        lambda: fixed_sigmet_time,
    )

    uri = sigmet_processor.write_bad_record(
        record=record,
        error_type="PermanentRecordError",
        error_message="invalid feature",
        decoded_payload={"bad": "payload"},
        raw_base64=record["kinesis"]["data"],
    )

    assert uri.startswith(
        "s3://test-sigmet-archive/"
        "bad-records/source=sigmet_processor/"
    )
    assert captured["Bucket"] == "test-sigmet-archive"
    assert captured["ContentType"] == "application/json"

    body = json.loads(captured["Body"].decode("utf-8"))

    assert body["schema_version"] == "bad_record.v1"
    assert body["service"] == "sigmet_processor"
    assert body["sequence_number"] == "seq-100"
    assert body["decoded_payload"] == {"bad": "payload"}
    assert body["raw_base64"] is None


def test_write_bad_record_keeps_raw_base64_when_decode_failed(
    sigmet_processor,
    monkeypatch,
    fixed_sigmet_time,
):
    captured = {}

    class FakeS3:
        def put_object(self, **kwargs):
            captured.update(kwargs)

    record = {
        "kinesis": {
            "sequenceNumber": "seq-raw",
            "data": "not-valid",
        }
    }

    monkeypatch.setattr(sigmet_processor, "s3", FakeS3())
    monkeypatch.setattr(
        sigmet_processor,
        "now_utc",
        lambda: fixed_sigmet_time,
    )

    sigmet_processor.write_bad_record(
        record=record,
        error_type="PermanentRecordError",
        error_message="decode failed",
        decoded_payload=None,
        raw_base64="not-valid",
    )

    body = json.loads(captured["Body"].decode("utf-8"))
    assert body["decoded_payload"] is None
    assert body["raw_base64"] == "not-valid"


def test_write_bad_record_requires_bucket(
    sigmet_processor,
    monkeypatch,
):
    monkeypatch.setattr(
        sigmet_processor,
        "BAD_RECORDS_BUCKET_NAME",
        None,
    )

    with pytest.raises(
        RuntimeError,
        match="BAD_RECORDS_BUCKET_NAME is not configured",
    ):
        sigmet_processor.write_bad_record(
            record={},
            error_type="Error",
            error_message="bad",
            decoded_payload=None,
            raw_base64=None,
        )


def test_lambda_handler_success_aggregates_processor_metrics(
    sigmet_processor,
    sigmet_raw_event,
    sigmet_kinesis_record_factory,
    monkeypatch,
):
    metrics = []

    monkeypatch.setattr(
        sigmet_processor,
        "process_decoded_record",
        lambda payload: {
            "active_hazards_written": 1,
            "hazard_cells_written": 3,
            "hazard_cells_removed": 1,
            "eventbridge_events_published": 1,
            "new_records": 1,
            "updated_records": 0,
            "unchanged_records": 0,
        },
    )
    monkeypatch.setattr(
        sigmet_processor,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    result = sigmet_processor.lambda_handler(
        {
            "Records": [
                sigmet_kinesis_record_factory(
                    sigmet_raw_event,
                    "seq-1",
                ),
                sigmet_kinesis_record_factory(
                    sigmet_raw_event,
                    "seq-2",
                ),
            ]
        },
        SimpleNamespace(aws_request_id="request-1"),
    )

    assert result == {"batchItemFailures": []}
    emitted = metrics[0]["metrics"]
    assert emitted["RecordsReceived"] == 2
    assert emitted["RecordsProcessed"] == 2
    assert emitted["RecordsFailed"] == 0
    assert emitted["ActiveHazardsWritten"] == 2
    assert emitted["HazardCellsWritten"] == 6
    assert emitted["HazardCellsRemoved"] == 2
    assert emitted["EventBridgeEventsPublished"] == 2
    assert emitted["NewRecords"] == 2


def test_lambda_handler_quarantines_permanent_error_without_retry(
    sigmet_processor,
    sigmet_kinesis_record_factory,
    monkeypatch,
):
    metrics = []
    bad_uris = []

    record = sigmet_kinesis_record_factory(
        {"feature": {"type": "NotFeature"}},
        "seq-permanent",
    )

    monkeypatch.setattr(
        sigmet_processor,
        "process_decoded_record",
        lambda payload: (_ for _ in ()).throw(
            sigmet_processor.PermanentRecordError(
                "not a GeoJSON Feature"
            )
        ),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "write_bad_record",
        lambda **kwargs: bad_uris.append(kwargs)
        or "s3://bucket/bad.json",
    )
    monkeypatch.setattr(
        sigmet_processor,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    result = sigmet_processor.lambda_handler(
        {"Records": [record]},
        None,
    )

    assert result == {"batchItemFailures": []}
    assert bad_uris[0]["error_type"] == "PermanentRecordError"
    assert metrics[0]["metrics"]["RecordsProcessed"] == 1
    assert metrics[0]["metrics"]["RecordsFailed"] == 0
    assert metrics[0]["metrics"]["BadRecordsWritten"] == 1


def test_lambda_handler_retries_when_quarantine_write_fails(
    sigmet_processor,
    sigmet_kinesis_record_factory,
    monkeypatch,
):
    metrics = []

    record = sigmet_kinesis_record_factory(
        {"feature": {"type": "NotFeature"}},
        "seq-quarantine-failed",
    )

    monkeypatch.setattr(
        sigmet_processor,
        "process_decoded_record",
        lambda payload: (_ for _ in ()).throw(
            sigmet_processor.PermanentRecordError("bad record")
        ),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "write_bad_record",
        lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError("S3 unavailable")
        ),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    result = sigmet_processor.lambda_handler(
        {"Records": [record]},
        None,
    )

    assert result == {
        "batchItemFailures": [
            {"itemIdentifier": "seq-quarantine-failed"}
        ]
    }
    assert metrics[0]["metrics"]["RecordsProcessed"] == 0
    assert metrics[0]["metrics"]["RecordsFailed"] == 1
    assert metrics[0]["metrics"]["BadRecordsWritten"] == 0


def test_lambda_handler_retries_temporary_runtime_failure(
    sigmet_processor,
    sigmet_raw_event,
    sigmet_kinesis_record_factory,
    monkeypatch,
):
    metrics = []

    monkeypatch.setattr(
        sigmet_processor,
        "process_decoded_record",
        lambda payload: (_ for _ in ()).throw(
            RuntimeError("EventBridge unavailable")
        ),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    result = sigmet_processor.lambda_handler(
        {
            "Records": [
                sigmet_kinesis_record_factory(
                    sigmet_raw_event,
                    "seq-runtime",
                )
            ]
        },
        None,
    )

    assert result == {
        "batchItemFailures": [
            {"itemIdentifier": "seq-runtime"}
        ]
    }
    assert metrics[0]["metrics"]["RecordsFailed"] == 1
    assert metrics[0]["metrics"]["BatchItemFailures"] == 1


def test_lambda_handler_retries_boto_client_error(
    sigmet_processor,
    sigmet_raw_event,
    sigmet_kinesis_record_factory,
    monkeypatch,
):
    error = ClientError(
        {
            "Error": {
                "Code": "ProvisionedThroughputExceededException",
                "Message": "throttled",
            }
        },
        "PutItem",
    )

    monkeypatch.setattr(
        sigmet_processor,
        "process_decoded_record",
        lambda payload: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "emit_metric",
        lambda **kwargs: None,
    )

    result = sigmet_processor.lambda_handler(
        {
            "Records": [
                sigmet_kinesis_record_factory(
                    sigmet_raw_event,
                    "seq-client",
                )
            ]
        },
        None,
    )

    assert result == {
        "batchItemFailures": [
            {"itemIdentifier": "seq-client"}
        ]
    }


def test_lambda_handler_decode_failure_is_quarantined(
    sigmet_processor,
    monkeypatch,
):
    captured = []
    metrics = []

    invalid_record = {
        "kinesis": {
            "sequenceNumber": "seq-decode",
            "data": base64.b64encode(b"not-json").decode("ascii"),
        }
    }

    monkeypatch.setattr(
        sigmet_processor,
        "write_bad_record",
        lambda **kwargs: captured.append(kwargs)
        or "s3://bucket/bad.json",
    )
    monkeypatch.setattr(
        sigmet_processor,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    result = sigmet_processor.lambda_handler(
        {"Records": [invalid_record]},
        None,
    )

    assert result == {"batchItemFailures": []}
    assert captured[0]["decoded_payload"] is None
    assert captured[0]["raw_base64"] == invalid_record["kinesis"]["data"]
    assert metrics[0]["metrics"]["BadRecordsWritten"] == 1
    assert metrics[0]["metrics"]["RecordsProcessed"] == 1
