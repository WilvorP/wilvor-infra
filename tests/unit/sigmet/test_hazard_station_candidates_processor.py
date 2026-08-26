import importlib.util
import os
import sys
from decimal import Decimal
from pathlib import Path


os.environ.setdefault("HAZARD_COORDINATES_TABLE_NAME", "test-hazard-coordinates")
os.environ.setdefault("STATION_REFERENCE_TABLE_NAME", "test-station-reference")
os.environ.setdefault("HAZARD_STATION_CANDIDATES_TABLE_NAME", "test-hazard-station-candidates")
os.environ.setdefault("SCHEMA_VERSION", "wilvor.hazard_station_candidates.v4.0")
os.environ.setdefault("EVENT_BUS_NAME", "default")
os.environ.setdefault("SELECTION_RADIUS_NM", "15")
os.environ.setdefault("SELECTION_CONFIG_VERSION", "hazard-station-selection-v1")

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = (
    REPO_ROOT
    / "functions"
    / "weather"
    / "sigmet"
    / "hazard_station_candidates_processor"
    / "app.py"
)

spec = importlib.util.spec_from_file_location(
    "hazard_station_candidates_processor_app",
    MODULE_PATH,
)

app = importlib.util.module_from_spec(spec)
sys.modules["hazard_station_candidates_processor_app"] = app
spec.loader.exec_module(app)


class FakeBatchWriter:
    def __init__(self, table):
        self.table = table

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def put_item(self, Item):
        self.table.items.append(Item)

    def delete_item(self, Key):
        self.table.deleted.append(Key)


class FakeTable:
    def __init__(self, query_items=None, scan_items=None):
        self.query_items = query_items or []
        self.scan_items = scan_items or []
        self.items = []
        self.deleted = []

    def query(self, **kwargs):
        if "IndexName" in kwargs:
            return {"Items": self.scan_items}

        return {"Items": self.query_items}

    def scan(self, **kwargs):
        return {"Items": self.scan_items}

    def batch_writer(self, overwrite_by_pkeys=None):
        return FakeBatchWriter(self)


class FakeEventsClient:
    def __init__(self):
        self.entries = []

    def put_events(self, Entries):
        self.entries.extend(Entries)
        return {"FailedEntryCount": 0, "Entries": [{"EventId": "test-event"}]}


def coord(sequence, lat, lon):
    return {
        "hazard_version_key": "hazard-1#version-1",
        "coordinate_key": f"P#0000#R#0000#S#{sequence:06d}",
        "hazard_id": "hazard-1",
        "source_version": "version-1",
        "hazard_type": "TURBULENCE",
        "severity": "SEVERE",
        "valid_from_utc": "2026-08-10T20:00:00+00:00",
        "valid_to_utc": "2026-08-10T23:00:00+00:00",
        "expires_at_epoch": Decimal("1786420500"),
        "polygon_index": Decimal("0"),
        "ring_index": Decimal("0"),
        "sequence_number": Decimal(str(sequence)),
        "latitude": Decimal(str(lat)),
        "longitude": Decimal(str(lon)),
        "geometry_hash": "geometry-hash-1",
        "correlation_id": "corr-1",
    }


def test_hazard_station_candidates_inside_and_near(monkeypatch):
    coordinates = [
        coord(0, 0.0, 0.0),
        coord(1, 0.0, 1.0),
        coord(2, 1.0, 1.0),
        coord(3, 1.0, 0.0),
    ]

    stations = [
        {
            "station_id": "KINS",
            "station_name": "Inside Station",
            "latitude": Decimal("0.5"),
            "longitude": Decimal("0.5"),
            "airport_id": "KINS",
            "active": True,
        },
        {
            "station_id": "KNEAR",
            "station_name": "Near Station",
            "latitude": Decimal("0.5"),
            "longitude": Decimal("1.1"),
            "airport_id": "KNEAR",
            "active": True,
        },
        {
            "station_id": "KFAR",
            "station_name": "Far Station",
            "latitude": Decimal("5.0"),
            "longitude": Decimal("5.0"),
            "airport_id": "KFAR",
            "active": True,
        },
    ]

    fake_coordinates = FakeTable(query_items=coordinates)
    fake_stations = FakeTable(scan_items=stations)
    fake_candidates = FakeTable(query_items=[])
    fake_events = FakeEventsClient()

    monkeypatch.setattr(app, "hazard_coordinates_table", fake_coordinates)
    monkeypatch.setattr(app, "station_reference_table", fake_stations)
    monkeypatch.setattr(app, "hazard_station_candidates_table", fake_candidates)
    monkeypatch.setattr(app, "events", fake_events)

    event = {
        "detail-type": "HazardCoordinates.materialized",
        "detail": {
            "hazard_version_key": "hazard-1#version-1",
            "hazard_id": "hazard-1",
            "source_version": "version-1",
            "hazard_type": "TURBULENCE",
            "severity": "SEVERE",
            "valid_from_utc": "2026-08-10T20:00:00+00:00",
            "valid_to_utc": "2026-08-10T23:00:00+00:00",
            "expires_at_epoch": 1786420500,
            "correlation_id": "corr-1",
            "geometry_hash": "geometry-hash-1",
        },
    }

    result = app.lambda_handler(event, None)

    assert result["ok"] is True
    assert result["candidate_count"] == 2
    assert result["change_type"] == "CREATED"

    by_station = {
        item["station_id"]: item
        for item in fake_candidates.items
    }

    assert by_station["KINS"]["spatial_relationship"] == "INSIDE"
    assert by_station["KINS"]["reason"] == "STATION_INSIDE_SIGMET"

    assert by_station["KNEAR"]["spatial_relationship"] == "NEAR"
    assert by_station["KNEAR"]["reason"] == "STATION_NEAR_SIGMET"

    expected_columns = {
        "hazard_version_key",
        "station_id",
        "candidate_id",
        "hazard_id",
        "hazard_source_version",
        "hazard_type",
        "valid_from_utc",
        "valid_to_utc",
        "station_latitude",
        "station_longitude",
        "spatial_relationship",
        "distance_to_hazard_nm",
        "selection_radius_nm",
        "reason",
        "selection_config_version",
        "created_at_utc",
        "updated_at_utc",
        "correlation_id",
        "schema_version",
        "expires_at_epoch",
    }

    assert expected_columns.issubset(by_station["KINS"].keys())

    assert fake_events.entries[0]["DetailType"] == "hazard.stations.ready"
    assert "hazard.stations.ready" in fake_events.entries[0]["Detail"]
    assert "CREATED" in fake_events.entries[0]["Detail"]


def test_no_h3_station_candidates_publishes_no_candidates_ready(monkeypatch):
    coordinates = [
        coord(0, 0.0, 0.0),
        coord(1, 0.0, 1.0),
        coord(2, 1.0, 1.0),
        coord(3, 1.0, 0.0),
    ]

    fake_coordinates = FakeTable(query_items=coordinates)
    fake_stations = FakeTable(scan_items=[])
    fake_candidates = FakeTable(query_items=[])
    fake_events = FakeEventsClient()

    monkeypatch.setattr(app, "hazard_coordinates_table", fake_coordinates)
    monkeypatch.setattr(app, "station_reference_table", fake_stations)
    monkeypatch.setattr(app, "hazard_station_candidates_table", fake_candidates)
    monkeypatch.setattr(app, "events", fake_events)

    event = {
        "detail-type": "HazardCoordinates.materialized",
        "detail": {
            "hazard_version_key": "hazard-1#version-1",
        },
    }

    result = app.lambda_handler(event, None)

    assert result["ok"] is True
    assert result["status"] == "READY"
    assert result["change_type"] == "NO_CANDIDATES"
    assert result["candidate_count"] == 0
    assert result["eventbridge_events_published"] == 1
    assert fake_candidates.items == []

    assert len(fake_events.entries) == 1
    assert fake_events.entries[0]["DetailType"] == "hazard.stations.ready"
    assert "NO_CANDIDATES" in fake_events.entries[0]["Detail"]