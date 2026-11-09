from __future__ import annotations

import base64
import json
import threading
import time
from decimal import Decimal
from typing import Any

import pytest
from botocore.exceptions import ClientError


def make_valid_record(writer, **overrides):
    aircraft_id = str(overrides.get("aircraft_id", "abc123"))
    position_time = int(overrides.get("position_time_epoch", 1_700_000_000))
    has_position = bool(overrides.get("has_position", True))
    state_version = f"{aircraft_id}#{position_time}"

    record = {
        "aircraft_id": aircraft_id,
        "position_time_epoch": position_time,
        "position_time_utc": "2026-11-09T00:00:00+00:00",
        "last_contact_epoch": position_time + 1,
        "last_contact_utc": "2026-11-09T00:00:01+00:00",
        "on_ground": False,
        "has_position": has_position,
        "latitude": 37.6189 if has_position else None,
        "longitude": -122.375 if has_position else None,
        "current_h3_cell": "8428309ffffffff" if has_position else None,
        "h3_resolution": 4 if has_position else None,
        "position_age_seconds": 2,
        "freshness_status": "FRESH",
        "state_version": state_version,
        "idempotency_key": state_version,
        "source_system": "OPEN_SKY",
        "source_event_time_utc": "2026-11-09T00:00:00+00:00",
        "received_at_utc": "2026-11-09T00:00:02+00:00",
        "processed_at_utc": "2026-11-09T00:00:03+00:00",
        "correlation_id": "poll-test-001",
        "raw_s3_uri": "s3://bucket/raw/poll-test-001.json",
        "schema_version": writer.AIRCRAFT_CURRENT_STATE_SCHEMA_VERSION,
        "expires_at_epoch": position_time + 1800,
    }
    record.update(overrides)

    if (
        "state_version" not in overrides
        and "idempotency_key" not in overrides
    ):
        version = (
            f"{record['aircraft_id']}#{int(record['position_time_epoch'])}"
        )
        record["state_version"] = version
        record["idempotency_key"] = version

    return record


class FakeDynamoClient:
    def __init__(self, put_item):
        self.put_item = put_item


def _ddb_value(attribute: dict[str, Any]):
    if "S" in attribute:
        return attribute["S"]
    if "N" in attribute:
        number = attribute["N"]
        return int(number) if "." not in number else number
    if "BOOL" in attribute:
        return attribute["BOOL"]
    raise AssertionError(attribute)


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
    monkeypatch.delenv("AIRCRAFT_WRITER_MAX_WORKERS", raising=False)
    monkeypatch.setenv("EVENT_BUS_NAME", "test-bus")
    return module


def test_validate_clean_record_accepts_valid_record(
    current_state_writer,
):
    record = make_valid_record(current_state_writer)

    assert current_state_writer.validate_clean_record(record) == []


def test_validate_clean_record_accepts_record_without_position(
    current_state_writer,
):
    record = make_valid_record(
        current_state_writer,
        has_position=False,
        latitude=None,
        longitude=None,
        current_h3_cell=None,
        h3_resolution=None,
    )

    assert current_state_writer.validate_clean_record(record) == []


@pytest.mark.parametrize(
    ("change", "expected_reason"),
    [
        ({"schema_version": "wrong.v1"}, "invalid_schema_version"),
        ({"aircraft_id": None}, "missing_aircraft_id"),
        ({"last_contact_epoch": None}, "missing_last_contact_epoch"),
        ({"latitude": None}, "missing_latitude_when_has_position"),
        ({"longitude": None}, "missing_longitude_when_has_position"),
        ({"source_system": "OTHER"}, "invalid_source_system"),
        ({"freshness_status": "OLD"}, "invalid_freshness_status"),
        ({"state_version": "abc123#1"}, "invalid_state_version"),
        (
            {"current_h3_cell": "cell", "has_position": False},
            "unexpected_h3_cell_without_position",
        ),
    ],
)
def test_validate_clean_record_reports_required_contract_failures(
    current_state_writer,
    change,
    expected_reason,
):
    record = make_valid_record(current_state_writer)
    record.update(change)

    reasons = current_state_writer.validate_clean_record(record)

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

    def put_item(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        current_state_writer,
        "dynamodb",
        FakeDynamoClient(put_item),
    )

    result = current_state_writer.put_current_state_item(
        clean_aircraft_record
    )

    assert result == "written"
    assert captured["TableName"] == "test-aircraft-current-state"
    assert captured["Item"]["latitude"] == {"N": "37.6189"}
    assert captured["Item"]["last_contact_epoch"] == {
        "N": str(clean_aircraft_record["last_contact_epoch"])
    }
    assert captured["ConditionExpression"] == (
        "attribute_not_exists(#position_time_epoch) OR "
        "#position_time_epoch < :incoming_position_time"
    )
    assert captured["ExpressionAttributeNames"] == {
        "#position_time_epoch": "position_time_epoch",
    }
    assert captured["ExpressionAttributeValues"][
        ":incoming_position_time"
    ] == {"N": str(clean_aircraft_record["position_time_epoch"])}


def test_put_without_position_keeps_position_protection(
    current_state_writer,
    monkeypatch,
):
    captured = {}
    record = make_valid_record(
        current_state_writer,
        has_position=False,
        latitude=None,
        longitude=None,
        current_h3_cell=None,
        h3_resolution=None,
    )

    def put_item(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        current_state_writer,
        "dynamodb",
        FakeDynamoClient(put_item),
    )

    assert current_state_writer.put_current_state_item(record) == "written"

    expression = captured["ConditionExpression"]
    assert "attribute_not_exists(#position_time_epoch)" in expression
    assert "#position_time_epoch < :incoming_position_time" in expression
    assert "attribute_not_exists(#has_position)" in expression
    assert "#has_position = :stored_has_position_false" in expression
    assert " AND " in expression
    assert captured["ExpressionAttributeValues"][
        ":stored_has_position_false"
    ] == {"BOOL": False}


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

    def put_item(**kwargs):
        raise error

    monkeypatch.setattr(
        current_state_writer,
        "dynamodb",
        FakeDynamoClient(put_item),
    )

    assert (
        current_state_writer.put_current_state_item(clean_aircraft_record)
        == "skipped_stale_duplicate_or_position_protected"
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

    def put_item(**kwargs):
        raise error

    monkeypatch.setattr(
        current_state_writer,
        "dynamodb",
        FakeDynamoClient(put_item),
    )

    with pytest.raises(ClientError):
        current_state_writer.put_current_state_item(
            clean_aircraft_record
        )


def test_configured_write_workers_defaults_and_caps(
    current_state_writer,
    monkeypatch,
):
    monkeypatch.delenv("AIRCRAFT_WRITER_MAX_WORKERS", raising=False)

    assert current_state_writer.configured_write_workers(0) == 0
    assert current_state_writer.configured_write_workers(1) == 1
    assert current_state_writer.configured_write_workers(100) == 10

    monkeypatch.setenv("AIRCRAFT_WRITER_MAX_WORKERS", "4")
    assert current_state_writer.configured_write_workers(100) == 4
    assert current_state_writer.configured_write_workers(2) == 2

    monkeypatch.setenv("AIRCRAFT_WRITER_MAX_WORKERS", "100")
    assert current_state_writer.configured_write_workers(100) == 20

    monkeypatch.setenv("AIRCRAFT_WRITER_MAX_WORKERS", "nope")
    assert current_state_writer.configured_write_workers(100) == 10


def test_handler_processes_written_and_stale_records(
    current_state_writer,
    kinesis_record_factory,
    monkeypatch,
):
    outcomes = iter(
        [
            "written",
            "skipped_stale_duplicate_or_position_protected",
        ]
    )
    metrics = []
    published = []

    monkeypatch.setattr(
        current_state_writer,
        "put_current_state_item",
        lambda item: next(outcomes),
    )
    monkeypatch.setattr(
        current_state_writer,
        "publish_aircraft_state_updated",
        lambda item: published.append(item["aircraft_id"]) or "event-1",
    )
    monkeypatch.setattr(
        current_state_writer,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    older = make_valid_record(
        current_state_writer,
        position_time_epoch=1_700_000_000,
    )
    newer = make_valid_record(
        current_state_writer,
        position_time_epoch=1_700_000_100,
    )

    result = current_state_writer.handler(
        {
            "Records": [
                kinesis_record_factory(older, "seq-1"),
                kinesis_record_factory(newer, "seq-2"),
            ]
        },
        None,
    )

    assert result == {"batchItemFailures": []}
    assert metrics[0]["metrics"]["WrittenRecords"] == 1
    assert metrics[0]["metrics"]["SkippedStaleRecords"] == 1
    assert metrics[0]["metrics"]["ValidRecords"] == 2
    assert metrics[0]["metrics"]["published_events"] == 1
    assert published == ["abc123"]


def test_handler_rejects_invalid_record_without_retrying(
    current_state_writer,
    kinesis_record_factory,
    monkeypatch,
):
    record = make_valid_record(current_state_writer)
    record["latitude"] = None
    metrics = []
    put_calls = []

    monkeypatch.setattr(
        current_state_writer,
        "put_current_state_item",
        lambda item: put_calls.append(item) or "written",
    )
    monkeypatch.setattr(
        current_state_writer,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    result = current_state_writer.handler(
        {
            "Records": [
                kinesis_record_factory(record, "seq-bad")
            ]
        },
        None,
    )

    assert result == {"batchItemFailures": []}
    assert metrics[0]["metrics"]["RejectedRecords"] == 1
    assert metrics[0]["metrics"]["ValidRecords"] == 0
    assert put_calls == []


def test_handler_returns_item_failure_when_dynamodb_write_fails(
    current_state_writer,
    kinesis_record_factory,
    monkeypatch,
):
    metrics = []
    published = []

    def fail_put(item):
        raise RuntimeError("DynamoDB down")

    monkeypatch.setattr(
        current_state_writer,
        "put_current_state_item",
        fail_put,
    )
    monkeypatch.setattr(
        current_state_writer,
        "publish_aircraft_state_updated",
        lambda item: published.append(item["aircraft_id"]),
    )
    monkeypatch.setattr(
        current_state_writer,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    result = current_state_writer.handler(
        {
            "Records": [
                kinesis_record_factory(
                    make_valid_record(current_state_writer),
                    "seq-77",
                )
            ]
        },
        None,
    )

    assert result == {
        "batchItemFailures": [{"itemIdentifier": "seq-77"}]
    }
    assert metrics[0]["metrics"]["FailedRecords"] == 1
    assert published == []


def test_handler_eventbridge_failure_retries_without_dropping_sibling(
    current_state_writer,
    kinesis_record_factory,
    monkeypatch,
):
    published = []

    def put_item(item):
        if item["aircraft_id"] == "write-failed":
            raise RuntimeError("DynamoDB down")
        if item["aircraft_id"] == "stale-aircraft":
            return "skipped_stale_duplicate_or_position_protected"
        return "written"

    def publish(item):
        if item["aircraft_id"] == "event-failed":
            raise RuntimeError("EventBridge down")
        published.append(item["aircraft_id"])
        return "event-1"

    monkeypatch.setattr(
        current_state_writer,
        "put_current_state_item",
        put_item,
    )
    monkeypatch.setattr(
        current_state_writer,
        "publish_aircraft_state_updated",
        publish,
    )
    monkeypatch.setattr(
        current_state_writer,
        "emit_metric",
        lambda **kwargs: None,
    )

    rejected = make_valid_record(
        current_state_writer,
        aircraft_id="bad-record",
    )
    rejected["latitude"] = None

    bad_payload = kinesis_record_factory({"unused": True}, "seq-decode")
    bad_payload["kinesis"]["data"] = base64.b64encode(b"not-json").decode(
        "ascii"
    )

    result = current_state_writer.handler(
        {
            "Records": [
                kinesis_record_factory(
                    make_valid_record(
                        current_state_writer,
                        aircraft_id="good-aircraft",
                    ),
                    "seq-ok",
                ),
                kinesis_record_factory(
                    make_valid_record(
                        current_state_writer,
                        aircraft_id="write-failed",
                    ),
                    "seq-write",
                ),
                kinesis_record_factory(
                    make_valid_record(
                        current_state_writer,
                        aircraft_id="stale-aircraft",
                    ),
                    "seq-stale",
                ),
                kinesis_record_factory(
                    make_valid_record(
                        current_state_writer,
                        aircraft_id="event-failed",
                    ),
                    "seq-event",
                ),
                kinesis_record_factory(rejected, "seq-rejected"),
                bad_payload,
            ]
        },
        None,
    )

    assert {
        item["itemIdentifier"] for item in result["batchItemFailures"]
    } == {"seq-write", "seq-event", "seq-decode"}
    assert published == ["good-aircraft"]


def test_same_aircraft_positions_stay_in_arrival_order(
    current_state_writer,
    kinesis_record_factory,
    monkeypatch,
):
    seen = []

    def put_item(item):
        seen.append(item["position_time_epoch"])
        time.sleep(0.01)
        return "written"

    monkeypatch.setenv("AIRCRAFT_WRITER_MAX_WORKERS", "8")
    monkeypatch.setattr(
        current_state_writer,
        "put_current_state_item",
        put_item,
    )
    monkeypatch.setattr(
        current_state_writer,
        "publish_aircraft_state_updated",
        lambda item: "event-1",
    )
    monkeypatch.setattr(
        current_state_writer,
        "emit_metric",
        lambda **kwargs: None,
    )

    epochs = [1_700_000_100, 1_700_000_300, 1_700_000_200]
    result = current_state_writer.handler(
        {
            "Records": [
                kinesis_record_factory(
                    make_valid_record(
                        current_state_writer,
                        aircraft_id="abc123",
                        position_time_epoch=epoch,
                    ),
                    f"seq-{epoch}",
                )
                for epoch in epochs
            ]
        },
        None,
    )

    assert result == {"batchItemFailures": []}
    assert seen == epochs


def test_different_aircraft_writes_run_concurrently(
    current_state_writer,
    kinesis_record_factory,
    monkeypatch,
):
    barrier = threading.Barrier(4, timeout=3)
    def put_item(item):
        barrier.wait()
        return "written"

    monkeypatch.setattr(
        current_state_writer,
        "put_current_state_item",
        put_item,
    )
    monkeypatch.setattr(
        current_state_writer,
        "publish_aircraft_state_updated",
        lambda item: "event-1",
    )
    monkeypatch.setattr(
        current_state_writer,
        "emit_metric",
        lambda **kwargs: None,
    )

    result = current_state_writer.handler(
        {
            "Records": [
                kinesis_record_factory(
                    make_valid_record(
                        current_state_writer,
                        aircraft_id=f"aircraft-{index}",
                    ),
                    f"seq-{index}",
                )
                for index in range(4)
            ]
        },
        None,
    )

    assert result == {"batchItemFailures": []}


def test_single_worker_does_not_overlap_aircraft(
    current_state_writer,
    kinesis_record_factory,
    monkeypatch,
):
    in_flight = 0
    max_in_flight = 0
    lock = threading.Lock()

    def put_item(item):
        nonlocal in_flight, max_in_flight
        with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        time.sleep(0.02)
        with lock:
            in_flight -= 1
        return "written"

    monkeypatch.setenv("AIRCRAFT_WRITER_MAX_WORKERS", "1")
    monkeypatch.setattr(
        current_state_writer,
        "put_current_state_item",
        put_item,
    )
    monkeypatch.setattr(
        current_state_writer,
        "publish_aircraft_state_updated",
        lambda item: "event-1",
    )
    monkeypatch.setattr(
        current_state_writer,
        "emit_metric",
        lambda **kwargs: None,
    )

    current_state_writer.handler(
        {
            "Records": [
                kinesis_record_factory(
                    make_valid_record(
                        current_state_writer,
                        aircraft_id=f"aircraft-{index}",
                    ),
                    f"seq-{index}",
                )
                for index in range(4)
            ]
        },
        None,
    )

    assert max_in_flight == 1


def test_worker_threads_share_one_dynamodb_client(
    current_state_writer,
    kinesis_record_factory,
    monkeypatch,
):
    sessions = []
    calls = []
    barrier = threading.Barrier(4, timeout=3)
    lock = threading.Lock()
    shared_client = FakeDynamoClient(None)

    def put_item(**kwargs):
        assert kwargs["TableName"] == "test-aircraft-current-state"
        barrier.wait()
        time.sleep(0.05)
        with lock:
            calls.append(threading.get_ident())

    shared_client.put_item = put_item

    def fail_if_session_created(*args, **kwargs):
        sessions.append(object())
        raise AssertionError("worker created a boto3 session")

    monkeypatch.setattr(
        current_state_writer.boto3.session,
        "Session",
        fail_if_session_created,
    )
    monkeypatch.setattr(current_state_writer, "dynamodb", shared_client)
    monkeypatch.setattr(
        current_state_writer,
        "publish_aircraft_state_updated",
        lambda item: "event-1",
    )
    monkeypatch.setattr(
        current_state_writer,
        "emit_metric",
        lambda **kwargs: None,
    )

    started = time.perf_counter()
    result = current_state_writer.handler(
        {
            "Records": [
                kinesis_record_factory(
                    make_valid_record(
                        current_state_writer,
                        aircraft_id=f"aircraft-{index}",
                    ),
                    f"seq-{index}",
                )
                for index in range(4)
            ]
        },
        None,
    )
    elapsed = time.perf_counter() - started

    assert result == {"batchItemFailures": []}
    assert sessions == []
    assert len(calls) == 4
    assert len(set(calls)) == 4
    assert elapsed < 0.2


def test_eventbridge_failure_after_write_is_not_replayed_on_retry(
    current_state_writer,
    kinesis_record_factory,
    monkeypatch,
):
    """Redelivery of the failed record alone does not emit another sibling event.

    Kinesis partial-batch retry is broader than this: Lambda replays the
    failed record and every record after it. That suffix is covered by
    test_kinesis_suffix_replay_after_eventbridge_failure.

    The failed record's event stays unpublished. PutItem uses a strict
    less-than condition, so the retry matches the stored position and is
    skipped instead of published again.
    """

    lock = threading.Lock()
    puts = []
    publishes = []

    class SharedBehaviorClient:
        def put_item(self, **kwargs):
            aircraft_id = _ddb_value(kwargs["Item"]["aircraft_id"])
            with lock:
                previous = puts.count(aircraft_id)
                puts.append(aircraft_id)
            if aircraft_id == "failed-event" and previous >= 1:
                raise ClientError(
                    {
                        "Error": {
                            "Code": "ConditionalCheckFailedException",
                            "Message": "position already stored",
                        }
                    },
                    "PutItem",
                )

    class FakeEvents:
        def put_events(self, **kwargs):
            detail = json.loads(kwargs["Entries"][0]["Detail"])
            aircraft_id = detail["aircraft_id"]
            with lock:
                publishes.append(aircraft_id)
            if aircraft_id == "failed-event":
                raise RuntimeError("EventBridge down")
            return {
                "FailedEntryCount": 0,
                "Entries": [{"EventId": f"event-{aircraft_id}"}],
            }

    monkeypatch.setattr(
        current_state_writer,
        "dynamodb",
        SharedBehaviorClient(),
    )
    monkeypatch.setattr(current_state_writer, "events", FakeEvents())
    monkeypatch.setattr(
        current_state_writer,
        "emit_metric",
        lambda **kwargs: None,
    )

    failed_record = make_valid_record(
        current_state_writer,
        aircraft_id="failed-event",
        position_time_epoch=1_700_000_100,
    )
    sibling_record = make_valid_record(
        current_state_writer,
        aircraft_id="sibling-event",
        position_time_epoch=1_700_000_100,
    )

    first = current_state_writer.handler(
        {
            "Records": [
                kinesis_record_factory(failed_record, "seq-failed"),
                kinesis_record_factory(sibling_record, "seq-sibling"),
            ]
        },
        None,
    )

    assert first["batchItemFailures"] == [
        {"itemIdentifier": "seq-failed"}
    ]
    assert puts.count("failed-event") == 1
    assert puts.count("sibling-event") == 1
    assert publishes.count("failed-event") == 1
    assert publishes.count("sibling-event") == 1

    retry = current_state_writer.handler(
        {
            "Records": [
                kinesis_record_factory(failed_record, "seq-failed"),
            ]
        },
        None,
    )

    assert retry == {"batchItemFailures": []}
    assert puts.count("failed-event") == 2
    assert puts.count("sibling-event") == 1
    assert publishes.count("failed-event") == 1
    assert publishes.count("sibling-event") == 1


def test_kinesis_suffix_replay_after_eventbridge_failure(
    current_state_writer,
    kinesis_record_factory,
    monkeypatch,
):
    """Lambda retries from the lowest failed sequence through the batch end.

    Records before that sequence are not replayed. Records that were
    already written are not republished, and an older position in the
    suffix cannot replace the newer stored state.

    The event for the record whose PutItem succeeded and whose PutEvents
    failed is not recovered. The retry sees the same position_time_epoch
    and the strict less-than condition skips it.
    """

    lock = threading.Lock()
    stored: dict[str, int] = {}
    puts: list[tuple[str, int]] = []
    publishes: list[tuple[str, int]] = []

    class RememberingClient:
        def put_item(self, **kwargs):
            assert kwargs["TableName"] == "test-aircraft-current-state"
            item = kwargs["Item"]
            aircraft_id = _ddb_value(item["aircraft_id"])
            incoming = _ddb_value(item["position_time_epoch"])
            with lock:
                puts.append((aircraft_id, incoming))
                current = stored.get(aircraft_id)
                if current is not None and current >= incoming:
                    raise ClientError(
                        {
                            "Error": {
                                "Code": "ConditionalCheckFailedException",
                                "Message": "older or duplicate position",
                            }
                        },
                        "PutItem",
                    )
                stored[aircraft_id] = incoming

    class FakeEvents:
        def put_events(self, **kwargs):
            detail = json.loads(kwargs["Entries"][0]["Detail"])
            publication = (
                detail["aircraft_id"],
                int(detail["position_time_epoch"]),
            )
            with lock:
                publishes.append(publication)
            if publication == ("event-gap", 2_000):
                raise RuntimeError("EventBridge down")
            return {
                "FailedEntryCount": 0,
                "Entries": [{"EventId": "event-1"}],
            }

    monkeypatch.setattr(
        current_state_writer,
        "dynamodb",
        RememberingClient(),
    )
    monkeypatch.setattr(current_state_writer, "events", FakeEvents())
    monkeypatch.setattr(
        current_state_writer,
        "emit_metric",
        lambda **kwargs: None,
    )

    def record(aircraft_id: str, position_time: int):
        return make_valid_record(
            current_state_writer,
            aircraft_id=aircraft_id,
            position_time_epoch=position_time,
        )

    ordered_records = [
        ("100", record("before-failure", 1_000)),
        ("200", record("event-gap", 2_000)),
        ("300", record("sibling", 3_000)),
        ("400", record("event-gap", 1_500)),
        ("500", record("sibling", 3_500)),
    ]

    first = current_state_writer.handler(
        {
            "Records": [
                kinesis_record_factory(payload, sequence_number)
                for sequence_number, payload in ordered_records
            ]
        },
        None,
    )

    assert {
        item["itemIdentifier"] for item in first["batchItemFailures"]
    } == {"200"}
    assert stored == {
        "before-failure": 1_000,
        "event-gap": 2_000,
        "sibling": 3_500,
    }
    assert sorted(publishes) == [
        ("before-failure", 1_000),
        ("event-gap", 2_000),
        ("sibling", 3_000),
        ("sibling", 3_500),
    ]
    sibling_events = [
        position
        for aircraft_id, position in publishes
        if aircraft_id == "sibling"
    ]
    assert sibling_events == [3_000, 3_500]
    assert ("event-gap", 1_500) not in publishes
    published_once = list(publishes)

    # Lambda checkpoints at the lowest failed sequence and replays that
    # record plus every later record. The earlier success is not included.
    suffix = [
        kinesis_record_factory(payload, sequence_number)
        for sequence_number, payload in ordered_records
        if sequence_number >= "200"
    ]
    retry = current_state_writer.handler(
        {"Records": suffix},
        None,
    )

    assert retry == {"batchItemFailures": []}
    assert stored == {
        "before-failure": 1_000,
        "event-gap": 2_000,
        "sibling": 3_500,
    }
    assert publishes == published_once
    assert ("event-gap", 1_500) not in publishes
    assert puts.count(("before-failure", 1_000)) == 1
    assert puts.count(("event-gap", 2_000)) == 2
    assert puts.count(("event-gap", 1_500)) == 2
    assert puts.count(("sibling", 3_000)) == 2
    assert puts.count(("sibling", 3_500)) == 2


def test_same_aircraft_events_stay_ordered_beside_other_aircraft(
    current_state_writer,
    kinesis_record_factory,
    monkeypatch,
):
    published = []
    lock = threading.Lock()

    def publish(item):
        if item["aircraft_id"] != "ordered-aircraft":
            time.sleep(0.02)
            return "event-other"
        with lock:
            published.append(item["position_time_epoch"])
        return "event-ordered"

    monkeypatch.setenv("AIRCRAFT_WRITER_MAX_WORKERS", "8")
    monkeypatch.setattr(
        current_state_writer,
        "put_current_state_item",
        lambda item: "written",
    )
    monkeypatch.setattr(
        current_state_writer,
        "publish_aircraft_state_updated",
        publish,
    )
    monkeypatch.setattr(
        current_state_writer,
        "emit_metric",
        lambda **kwargs: None,
    )

    epochs = [1_700_000_100, 1_700_000_300, 1_700_000_200]
    records = []
    for index, epoch in enumerate(epochs):
        records.append(
            kinesis_record_factory(
                make_valid_record(
                    current_state_writer,
                    aircraft_id=f"other-{index}",
                    position_time_epoch=epoch,
                ),
                f"seq-other-{index}",
            )
        )
        records.append(
            kinesis_record_factory(
                make_valid_record(
                    current_state_writer,
                    aircraft_id="ordered-aircraft",
                    position_time_epoch=epoch,
                ),
                f"seq-ordered-{index}",
            )
        )

    result = current_state_writer.handler(
        {"Records": records},
        None,
    )

    assert result == {"batchItemFailures": []}
    assert published == epochs


def test_publish_aircraft_state_updated_rejects_failed_entry(
    current_state_writer,
    monkeypatch,
):
    captured = {}

    class FakeEvents:
        def put_events(self, **kwargs):
            captured.update(kwargs)
            return {
                "FailedEntryCount": 1,
                "Entries": [
                    {
                        "ErrorCode": "InternalFailure",
                        "ErrorMessage": "unavailable",
                    }
                ],
            }

    monkeypatch.setattr(current_state_writer, "events", FakeEvents())
    record = make_valid_record(current_state_writer)

    with pytest.raises(RuntimeError, match="aircraft.state.updated"):
        current_state_writer.publish_aircraft_state_updated(record)

    entry = captured["Entries"][0]
    assert entry["Source"] == "wilvor.aircraft"
    assert entry["DetailType"] == "aircraft.state.updated"
    assert entry["EventBusName"] == "test-bus"
    detail = json.loads(entry["Detail"])
    assert detail["aircraft_id"] == record["aircraft_id"]
    assert detail["state_version"] == record["state_version"]
    assert detail["idempotency_key"] == record["idempotency_key"]
    assert detail["position_time_epoch"] == record["position_time_epoch"]
