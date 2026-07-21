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
        (
            1_752_840_000,
            datetime(2025, 7, 18, 12, 0, tzinfo=timezone.utc),
        ),
        (
            "2026-07-18T12:00:00Z",
            datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
        ),
        (
            "2026-07-18T12:00:00",
            datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
        ),
    ],
)
def test_parse_time_accepts_supported_values(
    metar_processor,
    value,
    expected,
):
    assert metar_processor.parse_time(value) == expected


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (None, "missing observation time"),
        ("", "empty observation time"),
        ("bad", "invalid observation time"),
        ({}, "unsupported observation time type"),
    ],
)
def test_parse_time_rejects_invalid_values(
    metar_processor,
    value,
    message,
):
    with pytest.raises(
        metar_processor.PermanentRecordError,
        match=message,
    ):
        metar_processor.parse_time(value)


def test_first_present_returns_first_non_empty(metar_processor):
    source = {"a": None, "b": "", "c": 0, "d": "later"}
    assert metar_processor.first_present(
        source,
        ["a", "b", "c", "d"],
    ) == 0


def test_to_decimal_converts_nested_floats_and_removes_none(
    metar_processor,
):
    result = metar_processor.to_decimal(
        {
            "temperature": 25.5,
            "nested": {"value": 1.25, "none": None},
            "items": [1.5, None, {"x": 2.5}],
        }
    )

    assert result == {
        "temperature": Decimal("25.5"),
        "nested": {"value": Decimal("1.25")},
        "items": [Decimal("1.5"), {"x": Decimal("2.5")}],
    }


def test_decode_kinesis_record(
    metar_processor,
    metar_raw_event,
    metar_kinesis_record_factory,
):
    record = metar_kinesis_record_factory(metar_raw_event)
    assert metar_processor.decode_kinesis_record(record) == metar_raw_event


def test_decode_kinesis_record_rejects_invalid_json(metar_processor):
    record = {
        "kinesis": {
            "data": base64.b64encode(b"not-json").decode("ascii")
        }
    }

    with pytest.raises(
        metar_processor.PermanentRecordError,
        match="invalid JSON",
    ):
        metar_processor.decode_kinesis_record(record)


def test_extract_feature_supports_direct_feature(metar_processor):
    feature = {"type": "Feature", "properties": {"icaoId": "KJFK"}}
    extracted, metadata = metar_processor.extract_feature(feature)

    assert extracted == feature
    assert metadata == {
        "poll_id": None,
        "received_at": None,
        "raw_s3_key": None,
    }


@pytest.mark.parametrize("wrapper", ["feature", "metar", "record", "data"])
def test_extract_feature_supports_wrappers(
    metar_processor,
    wrapper,
):
    feature = {"type": "Feature", "properties": {"icaoId": "KJFK"}}
    payload = {
        "poll_id": "poll-1",
        "received_at": "2026-07-18T12:05:00Z",
        "raw_s3_key": "raw/key",
        wrapper: feature,
    }

    extracted, metadata = metar_processor.extract_feature(payload)

    assert extracted == feature
    assert metadata["poll_id"] == "poll-1"


def test_extract_feature_supports_properties_fallback(
    metar_processor,
):
    payload = {
        "properties": {"icaoId": "KJFK"},
        "geometry": {"type": "Point", "coordinates": [-73.0, 40.0]},
    }

    feature, _ = metar_processor.extract_feature(payload)

    assert feature["type"] == "Feature"
    assert feature["properties"]["icaoId"] == "KJFK"


def test_extract_feature_rejects_unknown_payload(metar_processor):
    with pytest.raises(
        metar_processor.PermanentRecordError,
        match="could not find METAR GeoJSON feature",
    ):
        metar_processor.extract_feature({"unexpected": True})


def test_normalize_clouds_filters_invalid_entries(metar_processor):
    result = metar_processor.normalize_clouds(
        [
            {"cover": "SCT", "base": 2000},
            {"coverage": "BKN", "baseFeet": 4500},
            "bad",
            {"cover": None, "base": None},
        ]
    )

    assert result == [
        {"cover": "SCT", "base_ft": 2000},
        {"cover": "BKN", "base_ft": 4500},
        {},
    ]
    assert metar_processor.normalize_clouds("bad") == []


def test_build_source_version_is_stable_and_content_sensitive(
    metar_processor,
):
    item = {
        "station_id": "KJFK",
        "observed_at_utc": "2026-07-18T12:00:00+00:00",
        "temperature_c": 25,
    }

    first = metar_processor.build_source_version(item)
    second = metar_processor.build_source_version(dict(item))

    changed = dict(item)
    changed["temperature_c"] = 26

    assert first == second
    assert len(first) == 64
    assert metar_processor.build_source_version(changed) != first


def test_normalize_feature_builds_latest_item(
    metar_processor,
    metar_feature,
    fixed_metar_time,
    monkeypatch,
):
    monkeypatch.setattr(
        metar_processor,
        "utc_now",
        lambda: fixed_metar_time,
    )

    item = metar_processor.normalize_feature(
        metar_feature,
        {
            "poll_id": "poll-1",
            "received_at": "2026-07-18T12:05:00+00:00",
            "raw_s3_key": "raw/metar.json.gz",
        },
    )

    assert item["station_id"] == "KJFK"
    assert item["observed_at_epoch"] == int(
        datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc).timestamp()
    )
    assert item["data_freshness_seconds"] == 300
    assert item["latitude"] == 40.6413
    assert item["longitude"] == -73.7781
    assert item["clouds"][1] == {"cover": "BKN", "base_ft": 4500}
    assert item["source_system"] == "NOAA_AviationWeather_METAR"
    assert item["schema_version"] == "metar_latest.v1"
    assert len(item["source_version"]) == 64


@pytest.mark.parametrize(
    ("feature", "message"),
    [
        ({"type": "Feature", "properties": []}, "missing properties"),
        (
            {
                "type": "Feature",
                "properties": {"obsTime": "2026-07-18T12:00:00Z"},
            },
            "missing station id",
        ),
        (
            {
                "type": "Feature",
                "properties": {"icaoId": "KJFK"},
            },
            "missing observation time",
        ),
    ],
)
def test_normalize_feature_rejects_required_field_errors(
    metar_processor,
    feature,
    message,
):
    with pytest.raises(
        metar_processor.PermanentRecordError,
        match=message,
    ):
        metar_processor.normalize_feature(feature, {})


@pytest.mark.parametrize(
    ("old_item", "new_epoch", "new_version", "expected"),
    [
        (None, 200, "v1", "NEW"),
        ({"observed_at_epoch": 100, "source_version": "v0"}, 200, "v1", "UPDATED"),
        ({"observed_at_epoch": 300, "source_version": "v0"}, 200, "v1", "STALE"),
        ({"observed_at_epoch": 200, "source_version": "v0"}, 200, "v1", "CORRECTED"),
        ({"observed_at_epoch": 200, "source_version": "v1"}, 200, "v1", "UNCHANGED"),
    ],
)
def test_classify_change(
    metar_processor,
    old_item,
    new_epoch,
    new_version,
    expected,
):
    assert metar_processor.classify_change(
        {
            "observed_at_epoch": new_epoch,
            "source_version": new_version,
        },
        old_item,
    ) == expected


@pytest.mark.parametrize("change_type", ["UNCHANGED", "STALE"])
def test_write_latest_skips_non_writable_changes(
    metar_processor,
    change_type,
):
    assert metar_processor.write_latest(
        {
            "station_id": "KJFK",
            "observed_at_epoch": 1,
            "source_version": "v1",
        },
        change_type,
    ) is False


def test_write_latest_puts_conditional_item(
    metar_processor,
    fixed_metar_time,
    monkeypatch,
):
    captured = {}

    class FakeTable:
        def put_item(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(metar_processor, "table", FakeTable())
    monkeypatch.setattr(
        metar_processor,
        "utc_now",
        lambda: fixed_metar_time,
    )

    item = {
        "station_id": "KJFK",
        "observed_at_epoch": 100,
        "source_version": "v1",
        "temperature_c": 25.5,
    }

    assert metar_processor.write_latest(item, "NEW") is True
    assert captured["Item"]["temperature_c"] == Decimal("25.5")
    assert captured["Item"]["change_type"] == "NEW"
    assert captured["Item"]["event_publish_pending"] is True


def test_write_latest_returns_false_for_condition_failure(
    metar_processor,
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

    monkeypatch.setattr(metar_processor, "table", FakeTable())

    assert metar_processor.write_latest(
        {
            "station_id": "KJFK",
            "observed_at_epoch": 100,
            "source_version": "v1",
        },
        "NEW",
    ) is False


def test_get_weather_changed_event_context_for_new_write(
    metar_processor,
):
    item = {"source_version": "v1"}

    assert metar_processor.get_weather_changed_event_context(
        new_item=item,
        existing_item=None,
        change_type="NEW",
        wrote=True,
    ) == (True, item, "NEW")


def test_get_weather_changed_event_context_retries_pending_event(
    metar_processor,
):
    incoming = {"source_version": "v1"}
    existing = {
        "source_version": "v1",
        "event_publish_pending": True,
        "change_type": "CORRECTED",
    }

    assert metar_processor.get_weather_changed_event_context(
        new_item=incoming,
        existing_item=existing,
        change_type="UNCHANGED",
        wrote=False,
    ) == (True, existing, "CORRECTED")


def test_publish_weather_changed_event(
    metar_processor,
    monkeypatch,
):
    captured = {}

    class FakeEvents:
        def put_events(self, **kwargs):
            captured.update(kwargs)
            return {"FailedEntryCount": 0, "Entries": [{}]}

    monkeypatch.setattr(metar_processor, "events", FakeEvents())

    metar_processor.publish_weather_changed_event(
        {
            "station_id": "KJFK",
            "observed_at_utc": "2026-07-18T12:00:00+00:00",
            "source_version": "v1",
            "schema_version": "metar_latest.v1",
            "flight_category": "MVFR",
        },
        "NEW",
    )

    entry = captured["Entries"][0]
    detail = json.loads(entry["Detail"])

    assert entry["EventBusName"] == "test-weather-events"
    assert entry["DetailType"] == "Weather.changed"
    assert detail["product_type"] == "METAR"
    assert detail["station_id"] == "KJFK"
    assert detail["change_type"] == "NEW"


def test_publish_weather_changed_event_raises_on_failed_entry(
    metar_processor,
    monkeypatch,
):
    class FakeEvents:
        def put_events(self, **kwargs):
            return {"FailedEntryCount": 1, "Entries": [{}]}

    monkeypatch.setattr(metar_processor, "events", FakeEvents())

    with pytest.raises(RuntimeError, match="PutEvents failed"):
        metar_processor.publish_weather_changed_event(
            {"station_id": "KJFK"},
            "UPDATED",
        )


def test_mark_weather_changed_event_published(
    metar_processor,
    fixed_metar_time,
    monkeypatch,
):
    captured = {}

    class FakeTable:
        def update_item(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(metar_processor, "table", FakeTable())
    monkeypatch.setattr(
        metar_processor,
        "utc_now",
        lambda: fixed_metar_time,
    )

    metar_processor.mark_weather_changed_event_published(
        "KJFK",
        "v1",
    )

    assert captured["Key"] == {"station_id": "KJFK"}
    assert "REMOVE event_publish_pending" in captured["UpdateExpression"]
    assert captured["ExpressionAttributeValues"][":source_version"] == "v1"


def test_archive_bad_record_writes_s3(
    metar_processor,
    fixed_metar_time,
    monkeypatch,
):
    captured = {}

    class FakeS3:
        def put_object(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(metar_processor, "s3", FakeS3())
    monkeypatch.setattr(
        metar_processor,
        "utc_now",
        lambda: fixed_metar_time,
    )
    monkeypatch.setattr(
        metar_processor.uuid,
        "uuid4",
        lambda: "bad-fixed",
    )

    metar_processor.archive_bad_record(
        {"kinesis": {"sequenceNumber": "seq-1"}},
        "bad METAR",
        {"decoded": True},
    )

    assert captured["Bucket"] == "test-weather-archive"
    assert captured["Key"].endswith("bad-record-bad-fixed.json")
    body = json.loads(captured["Body"].decode("utf-8"))
    assert body["error"] == "bad METAR"
    assert body["sequence_number"] == "seq-1"


def test_process_record_new_writes_and_publishes(
    metar_processor,
    metar_raw_event,
    metar_kinesis_record_factory,
    monkeypatch,
):
    calls = []

    class FakeTable:
        def get_item(self, **kwargs):
            return {}

    monkeypatch.setattr(metar_processor, "table", FakeTable())
    monkeypatch.setattr(
        metar_processor,
        "normalize_feature",
        lambda feature, metadata: {
            "station_id": "KJFK",
            "observed_at_utc": "2026-07-18T12:00:00+00:00",
            "observed_at_epoch": 100,
            "source_version": "v1",
        },
    )
    monkeypatch.setattr(
        metar_processor,
        "write_latest",
        lambda item, change_type: True,
    )
    monkeypatch.setattr(
        metar_processor,
        "publish_weather_changed_event",
        lambda item, change_type: calls.append(("publish", change_type)),
    )
    monkeypatch.setattr(
        metar_processor,
        "mark_weather_changed_event_published",
        lambda station_id, source_version: calls.append(
            ("mark", station_id, source_version)
        ),
    )

    result = metar_processor.process_record(
        metar_kinesis_record_factory(metar_raw_event)
    )

    assert result == {
        "station_id": "KJFK",
        "observed_at_utc": "2026-07-18T12:00:00+00:00",
        "change_type": "NEW",
        "wrote": True,
        "event_published": True,
    }
    assert calls == [
        ("publish", "NEW"),
        ("mark", "KJFK", "v1"),
    ]


def test_lambda_handler_handles_new_and_temporary_failure(
    metar_processor,
    metar_kinesis_record_factory,
    monkeypatch,
):
    outcomes = iter(
        [
            {
                "change_type": "NEW",
                "wrote": True,
                "event_published": True,
            },
            RuntimeError("DynamoDB unavailable"),
        ]
    )
    metrics = []

    def fake_process(record):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(
        metar_processor,
        "process_record",
        fake_process,
    )
    monkeypatch.setattr(
        metar_processor,
        "emit_metrics",
        lambda values: metrics.append(values),
    )

    result = metar_processor.lambda_handler(
        {
            "Records": [
                metar_kinesis_record_factory({"a": 1}, "seq-1"),
                metar_kinesis_record_factory({"b": 2}, "seq-2"),
            ]
        },
        None,
    )

    assert result == {
        "batchItemFailures": [{"itemIdentifier": "seq-2"}]
    }
    assert metrics[0]["RecordsNew"] == 1
    assert metrics[0]["DynamoDBWrites"] == 1
    assert metrics[0]["WeatherChangedEventsPublished"] == 1
    assert metrics[0]["ProcessingFailures"] == 1


def test_lambda_handler_archives_permanent_error_without_retry(
    metar_processor,
    metar_kinesis_record_factory,
    monkeypatch,
):
    archived = []
    metrics = []

    monkeypatch.setattr(
        metar_processor,
        "process_record",
        lambda record: (_ for _ in ()).throw(
            metar_processor.PermanentRecordError("invalid station")
        ),
    )
    monkeypatch.setattr(
        metar_processor,
        "archive_bad_record",
        lambda record, error_message, payload=None: archived.append(
            (error_message, payload)
        ),
    )
    monkeypatch.setattr(
        metar_processor,
        "emit_metrics",
        lambda values: metrics.append(values),
    )

    result = metar_processor.lambda_handler(
        {
            "Records": [
                metar_kinesis_record_factory(
                    {"valid": "json"},
                    "seq-bad",
                )
            ]
        },
        None,
    )

    assert result == {"batchItemFailures": []}
    assert archived[0][0] == "invalid station"
    assert archived[0][1] == {"valid": "json"}
    assert metrics[0]["BadRecordsWritten"] == 1
    assert metrics[0]["ProcessingFailures"] == 1
