from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  TEST  ", "TEST"),
        ("", None),
        (None, None),
        (123, "123"),
        ({"b": 2, "a": 1}, '{"a": 1, "b": 2}'),
    ],
)
def test_clean_string(taf_processor, value, expected):
    assert taf_processor.clean_string(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            1_752_840_000,
            datetime(2025, 7, 18, 12, 0, tzinfo=timezone.utc),
        ),
        (
            1_752_840_000_000,
            datetime(2025, 7, 18, 12, 0, tzinfo=timezone.utc),
        ),
        (
            "1752840000",
            datetime(2025, 7, 18, 12, 0, tzinfo=timezone.utc),
        ),
        (
            "2026-07-18T12:00:00Z",
            datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
        ),
        (None, None),
        ("bad", None),
        ({}, None),
    ],
)
def test_parse_time(taf_processor, value, expected):
    assert taf_processor.parse_time(value) == expected


def test_require_time_rejects_invalid(taf_processor):
    with pytest.raises(
        taf_processor.PermanentRecordError,
        match="issueTime is missing or invalid",
    ):
        taf_processor.require_time(None, "issueTime")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (12, 12),
        (12.5, 12.5),
        (Decimal("10.0"), 10),
        (" 7 ", 7),
        ("7.5", 7.5),
        ("bad", None),
        (True, None),
        (None, None),
    ],
)
def test_normalize_number(taf_processor, value, expected):
    assert taf_processor.normalize_number(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        ("yes", True),
        ("0", False),
        (1, True),
        (0, False),
        ("bad", None),
    ],
)
def test_normalize_bool(taf_processor, value, expected):
    assert taf_processor.normalize_bool(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (10, {"visibility_sm": 10, "visibility_qualifier": None}),
        (
            "6+",
            {
                "visibility_sm": 6,
                "visibility_qualifier": "GREATER_THAN_OR_EQUAL",
            },
        ),
        (
            "P6SM",
            {
                "visibility_sm": None,
                "visibility_qualifier": "P6SM",
            },
        ),
        (
            None,
            {"visibility_sm": None, "visibility_qualifier": None},
        ),
    ],
)
def test_normalize_visibility(taf_processor, value, expected):
    assert taf_processor.normalize_visibility(value) == expected


def test_normalize_wind_direction(taf_processor):
    assert taf_processor.normalize_wind_direction("VRB") == (None, True)
    assert taf_processor.normalize_wind_direction("220") == (220, False)


def test_normalize_clouds_returns_ceiling(taf_processor):
    clouds, ceiling = taf_processor.normalize_clouds(
        [
            {"cover": "SCT", "base": 2000},
            {"cover": "BKN", "base": 4500},
            {"cover": "OVC", "base": 1200},
            "bad",
        ]
    )

    assert clouds == [
        {"cover": "SCT", "base_ft": 2000, "cloud_type": None},
        {"cover": "BKN", "base_ft": 4500, "cloud_type": None},
        {"cover": "OVC", "base_ft": 1200, "cloud_type": None},
    ]
    assert ceiling == 1200


def test_decode_kinesis_record(
    taf_processor,
    taf_raw_event,
    taf_kinesis_record_factory,
):
    record = taf_kinesis_record_factory(taf_raw_event)
    assert taf_processor.decode_kinesis_record(record) == taf_raw_event


@pytest.mark.parametrize(
    ("record", "message"),
    [
        ({}, "missing kinesis.data"),
        (
            {
                "kinesis": {
                    "data": base64.b64encode(b"\xff").decode("ascii")
                }
            },
            "not valid base64 UTF-8",
        ),
        (
            {
                "kinesis": {
                    "data": base64.b64encode(b"not-json").decode("ascii")
                }
            },
            "not valid JSON",
        ),
        (
            {
                "kinesis": {
                    "data": base64.b64encode(b"[]").decode("ascii")
                }
            },
            "not a JSON object",
        ),
    ],
)
def test_decode_kinesis_record_rejects_bad_records(
    taf_processor,
    record,
    message,
):
    with pytest.raises(
        taf_processor.PermanentRecordError,
        match=message,
    ):
        taf_processor.decode_kinesis_record(record)


def test_extract_taf(taf_processor, taf_raw_event, taf_record):
    assert taf_processor.extract_taf(taf_raw_event) == taf_record


def test_extract_taf_rejects_missing_object(taf_processor):
    with pytest.raises(
        taf_processor.PermanentRecordError,
        match="valid taf object",
    ):
        taf_processor.extract_taf({})


def test_normalize_period(
    taf_processor,
):
    result = taf_processor.normalize_period(
        {
            "timeFrom": "2026-07-18T12:00:00Z",
            "timeTo": "2026-07-18T18:00:00Z",
            "timeBec": "2026-07-18T14:00:00Z",
            "fcstChange": "tempo",
            "probability": "30",
            "wdir": "VRB",
            "wspd": "12",
            "wgst": 20,
            "visib": "6+",
            "wxString": "-RA BR",
            "clouds": [{"cover": "BKN", "base": 1500}],
            "wshearHgt": 2000,
            "wshearDir": 240,
            "wshearSpd": 35,
        },
        2,
    )

    assert result["sequence_number"] == 2
    assert result["change_type"] == "TEMPO"
    assert result["probability_pct"] == 30
    assert result["wind_direction_variable"] is True
    assert result["visibility_sm"] == 6
    assert result["visibility_qualifier"] == "GREATER_THAN_OR_EQUAL"
    assert result["weather_codes"] == ["-RA", "BR"]
    assert result["ceiling_ft"] == 1500
    assert result["low_level_wind_shear"] == {
        "height_ft": 2000,
        "direction_deg": 240,
        "speed_kt": 35,
    }


def test_normalize_period_rejects_reversed_range(taf_processor):
    with pytest.raises(
        taf_processor.PermanentRecordError,
        match="timeFrom >= timeTo",
    ):
        taf_processor.normalize_period(
            {
                "timeFrom": "2026-07-18T18:00:00Z",
                "timeTo": "2026-07-18T12:00:00Z",
            },
            0,
        )


def test_stable_hash_is_deterministic(taf_processor):
    assert taf_processor.stable_hash({"a": 1, "b": 2}) == (
        taf_processor.stable_hash({"b": 2, "a": 1})
    )


def test_normalize_taf_builds_latest_state(
    taf_processor,
    taf_raw_event,
    taf_record,
    fixed_taf_time,
    monkeypatch,
):
    monkeypatch.setattr(taf_processor, "now_utc", lambda: fixed_taf_time)

    item = taf_processor.normalize_taf(taf_raw_event, taf_record)

    assert item["station_id"] == "KJFK"
    assert item["issued_at_epoch"] == int(
        datetime(2026, 7, 18, 11, 30, tzinfo=timezone.utc).timestamp()
    )
    assert item["period_count"] == 2
    assert item["forecast_periods"][0]["change_type"] == "BASE"
    assert item["forecast_periods"][1]["change_type"] == "TEMPO"
    assert item["has_undecoded_content"] is True
    assert item["source"] == "NOAA_AVIATION_WEATHER"
    assert item["schema_version"] == "internal.taf.v1"
    assert item["raw_s3_uri"].startswith("s3://test-weather-archive/")
    assert len(item["source_version"]) == 32
    assert item["expires_at"] == int(
        datetime(2026, 7, 20, 6, 0, tzinfo=timezone.utc).timestamp()
    )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"icaoId": None}, "missing icaoId"),
        ({"issueTime": None}, "issueTime is missing or invalid"),
        ({"validTimeFrom": "2026-07-20T00:00:00Z"}, "must be before"),
        ({"rawTAF": ""}, "missing rawTAF"),
        ({"fcsts": []}, "non-empty list"),
        ({"fcsts": ["bad"]}, "non-object period"),
    ],
)
def test_normalize_taf_rejects_required_contract_errors(
    taf_processor,
    taf_raw_event,
    taf_record,
    change,
    message,
):
    changed = dict(taf_record)
    changed.update(change)

    with pytest.raises(
        taf_processor.PermanentRecordError,
        match=message,
    ):
        taf_processor.normalize_taf(taf_raw_event, changed)


def test_to_dynamodb_converts_nested_floats(taf_processor):
    assert taf_processor.to_dynamodb(
        {
            "latitude": 40.5,
            "nested": {"value": 1.25, "none": None},
            "items": [2.5, {"x": 3.5}],
        }
    ) == {
        "latitude": Decimal("40.5"),
        "nested": {"value": Decimal("1.25")},
        "items": [Decimal("2.5"), {"x": Decimal("3.5")}],
    }


@pytest.mark.parametrize(
    ("existing", "incoming", "expected"),
    [
        (None, {"source_version": "v1", "issued_at_epoch": 100}, "NEW"),
        (
            {"source_version": "v1", "issued_at_epoch": 100},
            {"source_version": "v1", "issued_at_epoch": 200},
            "UNCHANGED",
        ),
        (
            {"source_version": "v0", "issued_at_epoch": 100},
            {"source_version": "v1", "issued_at_epoch": 200},
            "UPDATED",
        ),
        (
            {"source_version": "v0", "issued_at_epoch": 200},
            {"source_version": "v1", "issued_at_epoch": 200},
            "CORRECTED",
        ),
        (
            {"source_version": "v0", "issued_at_epoch": 300},
            {"source_version": "v1", "issued_at_epoch": 200},
            "STALE",
        ),
    ],
)
def test_classify_change(
    taf_processor,
    existing,
    incoming,
    expected,
):
    assert taf_processor.classify_change(existing, incoming) == expected


def test_put_latest_writes_conditional_item(
    taf_processor,
    monkeypatch,
):
    captured = {}

    class FakeTable:
        def put_item(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        taf_processor,
        "taf_latest_table",
        FakeTable(),
    )

    assert taf_processor.put_latest(
        {
            "station_id": "KJFK",
            "issued_at_epoch": 100,
            "source_version": "v1",
            "latitude": 40.5,
        }
    ) is True
    assert captured["Item"]["latitude"] == Decimal("40.5")


def test_put_latest_returns_false_on_condition_failure(
    taf_processor,
    monkeypatch,
):
    error = ClientError(
        {
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": "condition failed",
            }
        },
        "PutItem",
    )

    class FakeTable:
        def put_item(self, **kwargs):
            raise error

    monkeypatch.setattr(
        taf_processor,
        "taf_latest_table",
        FakeTable(),
    )

    assert taf_processor.put_latest(
        {
            "station_id": "KJFK",
            "issued_at_epoch": 100,
            "source_version": "v1",
        }
    ) is False


def test_publish_weather_changed(
    taf_processor,
    monkeypatch,
):
    captured = {}

    class FakeEvents:
        def put_events(self, **kwargs):
            captured.update(kwargs)
            return {"FailedEntryCount": 0, "Entries": [{}]}

    monkeypatch.setattr(taf_processor, "events", FakeEvents())

    item = {
        "station_id": "KJFK",
        "issued_at_utc": "issued",
        "valid_from_utc": "from",
        "valid_to_utc": "to",
        "source": "NOAA_AVIATION_WEATHER",
        "schema_version": "internal.taf.v1",
        "source_version": "v1",
        "updated_at_utc": "updated",
        "period_count": 2,
        "has_undecoded_content": False,
    }

    taf_processor.publish_weather_changed(item, "NEW")

    entry = captured["Entries"][0]
    detail = json.loads(entry["Detail"])

    assert entry["EventBusName"] == "test-weather-events"
    assert entry["DetailType"] == "Weather.changed"
    assert detail["product_type"] == "TAF"
    assert detail["station_id"] == "KJFK"
    assert detail["change_type"] == "NEW"


def test_publish_weather_changed_raises_on_failed_entry(
    taf_processor,
    monkeypatch,
):
    class FakeEvents:
        def put_events(self, **kwargs):
            return {
                "FailedEntryCount": 1,
                "Entries": [{"ErrorCode": "InternalFailure"}],
            }

    monkeypatch.setattr(taf_processor, "events", FakeEvents())

    with pytest.raises(RuntimeError, match="PutEvents failed"):
        taf_processor.publish_weather_changed(
            {
                "station_id": "KJFK",
                "issued_at_utc": "issued",
                "valid_from_utc": "from",
                "valid_to_utc": "to",
                "source": "NOAA",
                "schema_version": "v1",
                "source_version": "source-v1",
                "updated_at_utc": "updated",
                "period_count": 1,
                "has_undecoded_content": False,
            },
            "UPDATED",
        )


def test_publish_if_needed(
    taf_processor,
    monkeypatch,
):
    calls = []

    monkeypatch.setattr(
        taf_processor,
        "publish_weather_changed",
        lambda item, change_type: calls.append(("publish", change_type)),
    )
    monkeypatch.setattr(
        taf_processor,
        "mark_event_published",
        lambda station_id, source_version: calls.append(
            ("mark", station_id, source_version)
        ),
    )

    existing = {
        "station_id": "KJFK",
        "source_version": "v1",
        "last_published_source_version": "v0",
        "change_type": "CORRECTED",
    }

    assert taf_processor.publish_if_needed(existing) is True
    assert calls == [
        ("publish", "CORRECTED"),
        ("mark", "KJFK", "v1"),
    ]

    already_published = dict(existing)
    already_published["last_published_source_version"] = "v1"
    assert taf_processor.publish_if_needed(already_published) is False


def test_process_record_new_writes_and_publishes(
    taf_processor,
    taf_raw_event,
    taf_kinesis_record_factory,
    monkeypatch,
):
    calls = []

    monkeypatch.setattr(
        taf_processor,
        "normalize_taf",
        lambda raw_event, taf: {
            "station_id": "KJFK",
            "source_version": "v1",
            "issued_at_epoch": 100,
        },
    )
    monkeypatch.setattr(
        taf_processor,
        "get_existing",
        lambda station_id: None,
    )
    monkeypatch.setattr(
        taf_processor,
        "put_latest",
        lambda item: True,
    )
    monkeypatch.setattr(
        taf_processor,
        "publish_weather_changed",
        lambda item, change_type: calls.append(("publish", change_type)),
    )
    monkeypatch.setattr(
        taf_processor,
        "mark_event_published",
        lambda station_id, source_version: calls.append(
            ("mark", station_id, source_version)
        ),
    )

    status = taf_processor.process_record(
        taf_kinesis_record_factory(taf_raw_event)
    )

    assert status == "NEW"
    assert calls == [
        ("publish", "NEW"),
        ("mark", "KJFK", "v1"),
    ]


def test_process_record_unchanged_retries_pending_event(
    taf_processor,
    taf_raw_event,
    taf_kinesis_record_factory,
    monkeypatch,
):
    published = []
    incoming = {
        "station_id": "KJFK",
        "source_version": "v1",
        "issued_at_epoch": 100,
    }
    existing = {
        **incoming,
        "last_published_source_version": "v0",
        "change_type": "NEW",
    }

    monkeypatch.setattr(
        taf_processor,
        "normalize_taf",
        lambda raw_event, taf: incoming,
    )
    monkeypatch.setattr(
        taf_processor,
        "get_existing",
        lambda station_id: existing,
    )
    monkeypatch.setattr(
        taf_processor,
        "publish_if_needed",
        lambda item: published.append(item) or True,
    )

    assert taf_processor.process_record(
        taf_kinesis_record_factory(taf_raw_event)
    ) == "UNCHANGED"
    assert published == [existing]


def test_archive_bad_record_writes_s3(
    taf_processor,
    fixed_taf_time,
    monkeypatch,
):
    captured = {}

    class FakeS3:
        def put_object(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(taf_processor, "s3", FakeS3())
    monkeypatch.setattr(taf_processor, "now_utc", lambda: fixed_taf_time)
    monkeypatch.setattr(
        taf_processor.uuid,
        "uuid4",
        lambda: "bad-fixed",
    )

    key = taf_processor.archive_bad_record(
        raw_record={"bad": True},
        sequence_number="seq-bad",
        reason="invalid TAF",
    )

    assert key.endswith("taf-bad-bad-fixed.json")
    assert captured["Bucket"] == "test-weather-archive"
    body = json.loads(captured["Body"].decode("utf-8"))
    assert body["schema_version"] == "taf_bad_record.v1"
    assert body["failure_reason"] == "invalid TAF"
    assert body["sequence_number"] == "seq-bad"


def test_record_identifier_prefers_sequence_then_event_id(
    taf_processor,
    monkeypatch,
):
    assert taf_processor.record_identifier(
        {
            "eventID": "event-1",
            "kinesis": {"sequenceNumber": "seq-1"},
        }
    ) == "seq-1"
    assert taf_processor.record_identifier(
        {"eventID": "event-1"}
    ) == "event-1"

    monkeypatch.setattr(taf_processor.uuid, "uuid4", lambda: "generated")
    assert taf_processor.record_identifier({}) == "generated"


def test_lambda_handler_aggregates_statuses(
    taf_processor,
    taf_kinesis_record_factory,
    monkeypatch,
):
    outcomes = iter(["NEW", "UNCHANGED"])
    metrics = []

    monkeypatch.setattr(
        taf_processor,
        "process_record",
        lambda record: next(outcomes),
    )
    monkeypatch.setattr(
        taf_processor,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    result = taf_processor.lambda_handler(
        {
            "Records": [
                taf_kinesis_record_factory({"a": 1}, "seq-1"),
                taf_kinesis_record_factory({"b": 2}, "seq-2"),
            ]
        },
        None,
    )

    assert result == {"batchItemFailures": []}
    assert metrics[0]["metrics"]["RecordsReceived"] == 2
    assert metrics[0]["metrics"]["RecordsNew"] == 1
    assert metrics[0]["metrics"]["RecordsUnchanged"] == 1


def test_lambda_handler_archives_permanent_error_without_retry(
    taf_processor,
    taf_kinesis_record_factory,
    monkeypatch,
):
    archived = []
    metrics = []

    monkeypatch.setattr(
        taf_processor,
        "process_record",
        lambda record: (_ for _ in ()).throw(
            taf_processor.PermanentRecordError("invalid TAF")
        ),
    )
    monkeypatch.setattr(
        taf_processor,
        "archive_bad_record",
        lambda **kwargs: archived.append(kwargs) or "bad/key",
    )
    monkeypatch.setattr(
        taf_processor,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    result = taf_processor.lambda_handler(
        {
            "Records": [
                taf_kinesis_record_factory({"bad": True}, "seq-bad")
            ]
        },
        None,
    )

    assert result == {"batchItemFailures": []}
    assert archived[0]["sequence_number"] == "seq-bad"
    assert metrics[0]["metrics"]["BadRecords"] == 1
    assert metrics[0]["metrics"]["ProcessingFailures"] == 0


def test_lambda_handler_retries_archive_failure(
    taf_processor,
    taf_kinesis_record_factory,
    monkeypatch,
):
    metrics = []

    monkeypatch.setattr(
        taf_processor,
        "process_record",
        lambda record: (_ for _ in ()).throw(
            taf_processor.PermanentRecordError("invalid TAF")
        ),
    )
    monkeypatch.setattr(
        taf_processor,
        "archive_bad_record",
        lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError("S3 unavailable")
        ),
    )
    monkeypatch.setattr(
        taf_processor,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    result = taf_processor.lambda_handler(
        {
            "Records": [
                taf_kinesis_record_factory(
                    {"bad": True},
                    "seq-archive",
                )
            ]
        },
        None,
    )

    assert result == {
        "batchItemFailures": [{"itemIdentifier": "seq-archive"}]
    }
    assert metrics[0]["metrics"]["ProcessingFailures"] == 1


def test_lambda_handler_retries_temporary_failure(
    taf_processor,
    taf_kinesis_record_factory,
    monkeypatch,
):
    metrics = []

    monkeypatch.setattr(
        taf_processor,
        "process_record",
        lambda record: (_ for _ in ()).throw(
            RuntimeError("DynamoDB unavailable")
        ),
    )
    monkeypatch.setattr(
        taf_processor,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    result = taf_processor.lambda_handler(
        {
            "Records": [
                taf_kinesis_record_factory({"a": 1}, "seq-temp")
            ]
        },
        None,
    )

    assert result == {
        "batchItemFailures": [{"itemIdentifier": "seq-temp"}]
    }
    assert metrics[0]["metrics"]["ProcessingFailures"] == 1
