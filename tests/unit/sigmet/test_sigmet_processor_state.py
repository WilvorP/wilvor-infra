from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest


def test_build_active_hazard_item(
    sigmet_processor,
    sigmet_raw_event,
    sigmet_feature,
    fixed_sigmet_time,
    monkeypatch,
):
    monkeypatch.setattr(
        sigmet_processor,
        "now_utc",
        lambda: fixed_sigmet_time,
    )
    monkeypatch.setattr(
        sigmet_processor,
        "now_utc_iso",
        lambda: fixed_sigmet_time.isoformat(),
    )

    item = sigmet_processor.build_active_hazard_item(
        sigmet_raw_event,
        sigmet_feature,
        ["cell-a", "cell-b"],
    )

    assert item["hazard_id"].startswith("sigmet-")
    assert item["product_type"] == "SIGMET"
    assert item["hazard_type"] == "TURBULENCE"
    assert item["status"] == "ACTIVE"
    assert item["source_icao_id"] == "KZNY"
    assert item["h3_resolution"] == 4
    assert item["h3_cells"] == ["cell-a", "cell-b"]
    assert item["h3_cell_count"] == 2
    assert item["source"] == "NOAA AviationWeather"
    assert item["schema_version"] == "internal.sigmet.v1"
    assert item["poll_id"] == "poll-sigmet-001"
    assert item["raw_s3_uri"].startswith("s3://test-sigmet-archive/")
    assert json.loads(item["geometry_json"]) == sigmet_feature["geometry"]


def test_build_active_hazard_item_marks_expired(
    sigmet_processor,
    sigmet_raw_event,
    sigmet_feature,
    monkeypatch,
):
    monkeypatch.setattr(
        sigmet_processor,
        "now_utc",
        lambda: datetime(
            2026,
            7,
            19,
            0,
            0,
            tzinfo=timezone.utc,
        ),
    )

    item = sigmet_processor.build_active_hazard_item(
        sigmet_raw_event,
        sigmet_feature,
        ["cell-a"],
    )

    assert item["status"] == "EXPIRED"


@pytest.mark.parametrize(
    ("existing", "source_version", "expected"),
    [
        (None, "v1", ("NEW", True, True)),
        (
            {"source_version": "v0"},
            "v1",
            ("UPDATED", True, True),
        ),
        (
            {
                "source_version": "v1",
                "change_type": "NEW",
                "last_published_source_version": None,
            },
            "v1",
            ("NEW", False, True),
        ),
        (
            {
                "source_version": "v1",
                "change_type": "UPDATED",
                "last_published_source_version": "v0",
            },
            "v1",
            ("UPDATED", False, True),
        ),
        (
            {
                "source_version": "v1",
                "change_type": "UPDATED",
                "last_published_source_version": "v1",
            },
            "v1",
            ("UNCHANGED", False, False),
        ),
    ],
)
def test_determine_change_type(
    sigmet_processor,
    existing,
    source_version,
    expected,
):
    assert sigmet_processor.determine_change_type(
        existing,
        {"source_version": source_version},
    ) == expected


def test_get_existing_hazard_returns_item_or_none(
    sigmet_processor,
    monkeypatch,
):
    class FakeTable:
        def __init__(self, response):
            self.response = response
            self.keys = []

        def get_item(self, **kwargs):
            self.keys.append(kwargs["Key"])
            return self.response

    present = FakeTable({"Item": {"hazard_id": "hazard-1"}})
    monkeypatch.setattr(
        sigmet_processor,
        "active_hazards_table",
        present,
    )

    assert sigmet_processor.get_existing_hazard("hazard-1") == {
        "hazard_id": "hazard-1"
    }
    assert present.keys == [{"hazard_id": "hazard-1"}]

    missing = FakeTable({})
    monkeypatch.setattr(
        sigmet_processor,
        "active_hazards_table",
        missing,
    )

    assert sigmet_processor.get_existing_hazard("hazard-2") is None


def test_sync_hazard_cells_puts_new_and_deletes_removed(
    sigmet_processor,
    monkeypatch,
):
    operations = []

    class FakeBatch:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def delete_item(self, **kwargs):
            operations.append(("delete", kwargs))

        def put_item(self, **kwargs):
            operations.append(("put", kwargs))

    class FakeTable:
        def batch_writer(self, **kwargs):
            assert kwargs["overwrite_by_pkeys"] == [
                "cell_id",
                "hazard_id",
            ]
            return FakeBatch()

    monkeypatch.setattr(
        sigmet_processor,
        "hazard_cells_table",
        FakeTable(),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "now_utc_iso",
        lambda: "2026-07-18T12:30:00+00:00",
    )

    written, removed = sigmet_processor.sync_hazard_cells(
        hazard_id="hazard-1",
        hazard_type="TURBULENCE",
        valid_from="from",
        valid_to="to",
        expires_at=12345,
        h3_cells=["cell-b", "cell-c"],
        previous_h3_cells=["cell-a", "cell-b"],
    )

    assert (written, removed) == (2, 1)
    assert operations[0] == (
        "delete",
        {
            "Key": {
                "cell_id": "cell-a",
                "hazard_id": "hazard-1",
            }
        },
    )

    put_items = [
        operation[1]["Item"]
        for operation in operations
        if operation[0] == "put"
    ]
    assert [item["cell_id"] for item in put_items] == [
        "cell-b",
        "cell-c",
    ]


def test_publish_weather_changed_sends_expected_event(
    sigmet_processor,
    monkeypatch,
):
    captured = {}

    class FakeEvents:
        def put_events(self, **kwargs):
            captured.update(kwargs)
            return {"FailedEntryCount": 0, "Entries": [{}]}

    monkeypatch.setattr(sigmet_processor, "events", FakeEvents())

    item = {
        "hazard_id": "hazard-1",
        "hazard_type": "TURBULENCE",
        "status": "ACTIVE",
        "valid_from": "from",
        "valid_to": "to",
        "h3_resolution": 4,
        "h3_cell_count": 2,
        "source": "NOAA AviationWeather",
        "schema_version": "internal.sigmet.v1",
        "source_version": "source-v1",
        "updated_at": "updated",
    }

    sigmet_processor.publish_weather_changed(item, "NEW")

    entry = captured["Entries"][0]
    detail = json.loads(entry["Detail"])

    assert entry["Source"] == "wilvor.weather"
    assert entry["DetailType"] == "Weather.changed"
    assert entry["EventBusName"] == "test-weather-events"
    assert detail["event_type"] == "weather.changed"
    assert detail["hazard_id"] == "hazard-1"
    assert detail["change_type"] == "NEW"


def test_publish_weather_changed_raises_on_failed_entry(
    sigmet_processor,
    monkeypatch,
):
    class FakeEvents:
        def put_events(self, **kwargs):
            return {
                "FailedEntryCount": 1,
                "Entries": [{"ErrorCode": "InternalFailure"}],
            }

    monkeypatch.setattr(sigmet_processor, "events", FakeEvents())

    with pytest.raises(
        RuntimeError,
        match="EventBridge PutEvents failed",
    ):
        sigmet_processor.publish_weather_changed(
            {
                "hazard_id": "hazard-1",
                "hazard_type": "TURBULENCE",
                "status": "ACTIVE",
                "h3_resolution": 4,
                "h3_cell_count": 1,
                "source": "NOAA",
                "schema_version": "internal.sigmet.v1",
                "source_version": "v1",
                "updated_at": "now",
            },
            "NEW",
        )


def test_process_decoded_record_new_hazard(
    sigmet_processor,
    sigmet_raw_event,
    monkeypatch,
):
    item = {
        "hazard_id": "hazard-1",
        "hazard_type": "TURBULENCE",
        "valid_from": "from",
        "valid_to": "to",
        "expires_at": 12345,
        "source_version": "v1",
        "updated_at": "updated",
    }
    put_items = []
    synced = []
    events = []
    marks = []

    class FakeActiveTable:
        def put_item(self, **kwargs):
            put_items.append(kwargs["Item"])

    monkeypatch.setattr(
        sigmet_processor,
        "active_hazards_table",
        FakeActiveTable(),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "geometry_to_h3_cells",
        lambda geometry, resolution: ["cell-a", "cell-b"],
    )
    monkeypatch.setattr(
        sigmet_processor,
        "build_active_hazard_item",
        lambda raw_event, feature, h3_cells: dict(item),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "get_existing_hazard",
        lambda hazard_id: None,
    )
    monkeypatch.setattr(
        sigmet_processor,
        "sync_hazard_cells",
        lambda **kwargs: synced.append(kwargs) or (2, 0),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "publish_weather_changed",
        lambda item, change_type: events.append(change_type),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "mark_event_published",
        lambda hazard_id, version: marks.append(
            (hazard_id, version)
        ),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "now_utc_iso",
        lambda: "now",
    )

    result = sigmet_processor.process_decoded_record(sigmet_raw_event)

    assert result == {
        "active_hazards_written": 1,
        "hazard_cells_written": 2,
        "hazard_cells_removed": 0,
        "eventbridge_events_published": 1,
        "new_records": 1,
        "updated_records": 0,
        "unchanged_records": 0,
    }
    assert put_items[0]["change_type"] == "NEW"
    assert put_items[0]["first_seen_at"] == "now"
    assert synced[0]["h3_cells"] == ["cell-a", "cell-b"]
    assert events == ["NEW"]
    assert marks == [("hazard-1", "v1")]


def test_process_decoded_record_unchanged_updates_last_seen_only(
    sigmet_processor,
    sigmet_raw_event,
    monkeypatch,
):
    updated = []

    monkeypatch.setattr(
        sigmet_processor,
        "geometry_to_h3_cells",
        lambda geometry, resolution: ["cell-a"],
    )
    monkeypatch.setattr(
        sigmet_processor,
        "build_active_hazard_item",
        lambda raw_event, feature, h3_cells: {
            "hazard_id": "hazard-1",
            "source_version": "v1",
        },
    )
    monkeypatch.setattr(
        sigmet_processor,
        "get_existing_hazard",
        lambda hazard_id: {
            "hazard_id": hazard_id,
            "source_version": "v1",
            "change_type": "NEW",
            "last_published_source_version": "v1",
        },
    )
    monkeypatch.setattr(
        sigmet_processor,
        "update_last_seen",
        lambda hazard_id, received_at: updated.append(
            (hazard_id, received_at)
        ),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "publish_weather_changed",
        lambda *args: pytest.fail("event should not publish"),
    )

    result = sigmet_processor.process_decoded_record(sigmet_raw_event)

    assert result["unchanged_records"] == 1
    assert result["active_hazards_written"] == 0
    assert result["eventbridge_events_published"] == 0
    assert updated == [
        ("hazard-1", sigmet_raw_event["received_at"])
    ]
