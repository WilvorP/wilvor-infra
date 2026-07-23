from __future__ import annotations

from decimal import Decimal

import pytest
from botocore.exceptions import ClientError


@pytest.fixture
def current_state_writer(load_repo_module, monkeypatch):
    module = load_repo_module(
        "unit_aircraft_current_state_writer_app",
        "functions/aircraft_current_state_writer/app.py",
    )

    monkeypatch.setenv(
        "AIRCRAFT_CURRENT_STATE_TABLE_NAME",
        "test-aircraft-current-state",
    )
    return module


def test_validate_clean_record_accepts_valid_record(
    current_state_writer,
    clean_aircraft_record,
):
    assert (
        current_state_writer.validate_clean_record(clean_aircraft_record)
        == []
    )


@pytest.mark.parametrize(
    ("change", "expected_reason"),
    [
        ({"schema_version": "wrong.v1"}, "invalid_schema_version"),
        ({"icao24": ""}, "missing_icao24"),
        ({"last_contact_epoch": None}, "missing_last_contact_epoch"),
        ({"latitude": None}, "missing_latitude"),
        ({"longitude": None}, "missing_longitude"),
    ],
)
def test_validate_clean_record_reports_required_contract_failures(
    current_state_writer,
    clean_aircraft_record,
    change,
    expected_reason,
):
    clean_aircraft_record.update(change)

    reasons = current_state_writer.validate_clean_record(
        clean_aircraft_record
    )

    assert expected_reason in reasons


def test_validate_clean_record_rejects_non_object(current_state_writer):
    assert current_state_writer.validate_clean_record([]) == [
        "clean_record_not_object"
    ]


def test_convert_for_dynamodb_converts_nested_floats_and_removes_dict_none(
    current_state_writer,
):
    source = {
        "latitude": 37.5,
        "quality": {
            "score": 0.75,
            "unused": None,
        },
        "points": [1.5, {"altitude": 10_000.25}],
        "optional": None,
    }

    result = current_state_writer.convert_for_dynamodb(source)

    assert result == {
        "latitude": Decimal("37.5"),
        "quality": {
            "score": Decimal("0.75"),
        },
        "points": [
            Decimal("1.5"),
            {"altitude": Decimal("10000.25")},
        ],
    }


def test_put_current_state_item_returns_written(
    current_state_writer,
    clean_aircraft_record,
    monkeypatch,
):
    captured = {}

    class FakeTable:
        def put_item(self, **kwargs):
            captured.update(kwargs)

    class FakeDynamoDb:
        def Table(self, table_name):
            assert table_name == "test-aircraft-current-state"
            return FakeTable()

    monkeypatch.setattr(current_state_writer, "dynamodb", FakeDynamoDb())

    result = current_state_writer.put_current_state_item(
        clean_aircraft_record
    )

    assert result == "written"
    assert captured["Item"]["latitude"] == Decimal("37.6189")
    assert captured["Item"]["last_contact_epoch"] == Decimal(
        str(clean_aircraft_record["last_contact_epoch"])
    )
    assert "ConditionExpression" in captured


def test_put_current_state_item_returns_skipped_stale_on_condition_failure(
    current_state_writer,
    clean_aircraft_record,
    monkeypatch,
):
    error = ClientError(
        {
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": "stale record",
            }
        },
        "PutItem",
    )

    class FakeTable:
        def put_item(self, **kwargs):
            raise error

    class FakeDynamoDb:
        def Table(self, table_name):
            return FakeTable()

    monkeypatch.setattr(current_state_writer, "dynamodb", FakeDynamoDb())

    assert (
        current_state_writer.put_current_state_item(clean_aircraft_record)
        == "skipped_stale"
    )


def test_put_current_state_item_reraises_unexpected_client_error(
    current_state_writer,
    clean_aircraft_record,
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

    class FakeTable:
        def put_item(self, **kwargs):
            raise error

    class FakeDynamoDb:
        def Table(self, table_name):
            return FakeTable()

    monkeypatch.setattr(current_state_writer, "dynamodb", FakeDynamoDb())

    with pytest.raises(ClientError):
        current_state_writer.put_current_state_item(
            clean_aircraft_record
        )


def test_handler_processes_written_and_stale_records(
    current_state_writer,
    clean_aircraft_record,
    kinesis_record_factory,
    monkeypatch,
):
    outcomes = iter(["written", "skipped_stale"])
    metrics = []

    monkeypatch.setattr(
        current_state_writer,
        "put_current_state_item",
        lambda item: next(outcomes),
    )
    monkeypatch.setattr(
        current_state_writer,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    result = current_state_writer.handler(
        {
            "Records": [
                kinesis_record_factory(clean_aircraft_record, "seq-1"),
                kinesis_record_factory(clean_aircraft_record, "seq-2"),
            ]
        },
        None,
    )

    assert result == {"batchItemFailures": []}
    assert metrics[0]["metrics"]["WrittenRecords"] == 1
    assert metrics[0]["metrics"]["SkippedStaleRecords"] == 1
    assert metrics[0]["metrics"]["ValidRecords"] == 2


def test_handler_rejects_invalid_record_without_retrying(
    current_state_writer,
    clean_aircraft_record,
    kinesis_record_factory,
    monkeypatch,
):
    clean_aircraft_record["latitude"] = None
    metrics = []

    monkeypatch.setattr(
        current_state_writer,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    result = current_state_writer.handler(
        {
            "Records": [
                kinesis_record_factory(clean_aircraft_record, "seq-bad")
            ]
        },
        None,
    )

    assert result == {"batchItemFailures": []}
    assert metrics[0]["metrics"]["RejectedRecords"] == 1
    assert metrics[0]["metrics"]["ValidRecords"] == 0


def test_handler_returns_item_failure_when_dynamodb_write_fails(
    current_state_writer,
    clean_aircraft_record,
    kinesis_record_factory,
    monkeypatch,
):
    metrics = []

    monkeypatch.setattr(
        current_state_writer,
        "put_current_state_item",
        lambda item: (_ for _ in ()).throw(RuntimeError("DynamoDB down")),
    )
    monkeypatch.setattr(
        current_state_writer,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    result = current_state_writer.handler(
        {
            "Records": [
                kinesis_record_factory(clean_aircraft_record, "seq-77")
            ]
        },
        None,
    )

    assert result == {
        "batchItemFailures": [{"itemIdentifier": "seq-77"}]
    }
    assert metrics[0]["metrics"]["FailedRecords"] == 1
