import importlib
import os
import sys
from pathlib import Path


os.environ.setdefault("HAZARD_COORDINATES_TABLE_NAME", "test-hazard-coordinates")
os.environ.setdefault("SCHEMA_VERSION", "wilvor.hazard_coordinates.v4.0")
os.environ.setdefault("EVENT_BUS_NAME", "default")
os.environ.setdefault("RETENTION_AFTER_VALID_TO_HOURS", "6")

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_DIR = REPO_ROOT / "functions" / "weather" / "sigmet" / "hazard_coordinates_processor"
sys.path.insert(0, str(MODULE_DIR))

app = importlib.import_module("app")


class FakeBatchWriter:
    def __init__(self, table):
        self.table = table

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def put_item(self, Item):
        self.table.items.append(Item)


class FakeHazardCoordinatesTable:
    def __init__(self, existing_count=0):
        self.existing_count = existing_count
        self.items = []

    def query(self, **kwargs):
        return {"Count": self.existing_count}

    def batch_writer(self, overwrite_by_pkeys):
        assert overwrite_by_pkeys == ["hazard_version_key", "coordinate_key"]
        return FakeBatchWriter(self)


class FakeEventsClient:
    def __init__(self):
        self.entries = []

    def put_events(self, Entries):
        self.entries.extend(Entries)
        return {"FailedEntryCount": 0, "Entries": [{"EventId": "test-event"}]}


def sample_raw_event(geometry):
    return {
        "schema_version": "raw.noaa.airsigmet.v1",
        "source": "NOAA_AVIATION_WEATHER",
        "product_type": "SIGMET",
        "ingestion_type": "RAW_SIGMET_FEATURE",
        "poll_id": "poll-123",
        "received_at": "2026-08-10T19:00:00+00:00",
        "raw_s3_bucket": "wilvor-test-sigmet-archive",
        "raw_s3_key": "raw/source=sigmet/test.json.gz",
        "record_index": 0,
        "feature": {
            "type": "Feature",
            "properties": {
                "icaoId": "KKCI",
                "airSigmetType": "SIGMET",
                "alphaChar": "A",
                "seriesId": "1",
                "hazard": "TURBULENCE",
                "severity": "SEV",
                "creationTime": "2026-08-10T18:45:00Z",
                "validTimeFrom": "2026-08-10T19:00:00Z",
                "validTimeTo": "2026-08-10T23:00:00Z",
                "rawAirSigmet": "TEST SIGMET",
            },
            "geometry": geometry,
        },
    }


def test_polygon_creates_required_coordinate_rows(monkeypatch):
    fake_table = FakeHazardCoordinatesTable(existing_count=0)
    fake_events = FakeEventsClient()

    monkeypatch.setattr(app, "hazard_coordinates_table", fake_table)
    monkeypatch.setattr(app, "events", fake_events)

    raw_event = sample_raw_event(
        {
            "type": "Polygon",
            "coordinates": [
                [
                    [-122.0, 37.0],
                    [-121.0, 37.0],
                    [-121.0, 38.0],
                    [-122.0, 37.0],
                ]
            ],
        }
    )

    result = app.process_decoded_record(raw_event)

    assert result["coordinate_rows_written"] == 3
    assert result["eventbridge_events_published"] == 1

    item = fake_table.items[0]

    expected_columns = {
        "hazard_version_key",
        "coordinate_key",
        "hazard_id",
        "source_version",
        "geometry_type",
        "polygon_index",
        "ring_index",
        "sequence_number",
        "latitude",
        "longitude",
        "materialization_id",
        "geometry_hash",
        "created_at_utc",
        "correlation_id",
        "schema_version",
        "expires_at_epoch",
    }

    assert expected_columns.issubset(item.keys())
    assert item["geometry_type"] == "POLYGON"
    assert item["polygon_index"] == 0
    assert item["ring_index"] == 0
    assert item["sequence_number"] == 0
    assert item["coordinate_key"] == "P#0000#R#0000#S#000000"

    # GeoJSON is lon/lat. DynamoDB item must be latitude/longitude.
    assert str(item["latitude"]) == "37.0"
    assert str(item["longitude"]) == "-122.0"

    event_detail = fake_events.entries[0]["Detail"]
    assert "hazard.coordinates.materialized" in event_detail
    assert fake_events.entries[0]["DetailType"] == "HazardCoordinates.materialized"


def test_multipolygon_preserves_polygon_ring_sequence(monkeypatch):
    fake_table = FakeHazardCoordinatesTable(existing_count=0)
    fake_events = FakeEventsClient()

    monkeypatch.setattr(app, "hazard_coordinates_table", fake_table)
    monkeypatch.setattr(app, "events", fake_events)

    raw_event = sample_raw_event(
        {
            "type": "MultiPolygon",
            "coordinates": [
                [
                    [
                        [-122.0, 37.0],
                        [-121.0, 37.0],
                        [-121.0, 38.0],
                        [-122.0, 37.0],
                    ]
                ],
                [
                    [
                        [-120.0, 35.0],
                        [-119.0, 35.0],
                        [-119.0, 36.0],
                        [-120.0, 35.0],
                    ]
                ],
            ],
        }
    )

    result = app.process_decoded_record(raw_event)

    assert result["coordinate_rows_written"] == 6

    keys = [item["coordinate_key"] for item in fake_table.items]

    assert keys == [
        "P#0000#R#0000#S#000000",
        "P#0000#R#0000#S#000001",
        "P#0000#R#0000#S#000002",
        "P#0001#R#0000#S#000000",
        "P#0001#R#0000#S#000001",
        "P#0001#R#0000#S#000002",
    ]

    assert all(item["geometry_type"] == "MULTIPOLYGON" for item in fake_table.items)


def test_existing_materialization_skips_write_but_still_publishes_event(monkeypatch):
    fake_table = FakeHazardCoordinatesTable(existing_count=3)
    fake_events = FakeEventsClient()

    monkeypatch.setattr(app, "hazard_coordinates_table", fake_table)
    monkeypatch.setattr(app, "events", fake_events)

    raw_event = sample_raw_event(
        {
            "type": "Polygon",
            "coordinates": [
                [
                    [-122.0, 37.0],
                    [-121.0, 37.0],
                    [-121.0, 38.0],
                    [-122.0, 37.0],
                ]
            ],
        }
    )

    result = app.process_decoded_record(raw_event)

    assert result["coordinate_rows_written"] == 0
    assert result["already_materialized"] == 1
    assert result["eventbridge_events_published"] == 1
    assert fake_table.items == []
    assert "ALREADY_EXISTS" in fake_events.entries[0]["Detail"]


def test_invalid_geometry_raises_permanent_error():
    raw_event = sample_raw_event(
        {
            "type": "LineString",
            "coordinates": [[-122.0, 37.0], [-121.0, 38.0]],
        }
    )

    try:
        app.process_decoded_record(raw_event)
    except app.PermanentRecordError as exc:
        assert "Unsupported geometry type" in str(exc)
    else:
        raise AssertionError("Expected PermanentRecordError")