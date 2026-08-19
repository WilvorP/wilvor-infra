import importlib.util
import os
from pathlib import Path


os.environ["AWS_DEFAULT_REGION"] = "us-west-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_EC2_METADATA_DISABLED"] = "true"

os.environ["AIRCRAFT_PROJECTION_TABLE_NAME"] = "test-projection"
os.environ["AIRCRAFT_PROJECTION_CELLS_TABLE_NAME"] = "test-projection-cells"
os.environ["AIRCRAFT_PROJECTION_CELLS_H3_INDEX_NAME"] = "h3_cell-projection_id-index"
os.environ["HAZARD_CELLS_TABLE_NAME"] = "test-hazard-cells"
os.environ["HAZARD_CELLS_HAZARD_VERSION_INDEX_NAME"] = "hazard_version_key-h3_cell-index"
os.environ["ACTIVE_HAZARDS_TABLE_NAME"] = "test-active-hazards"
os.environ["HAZARD_COORDINATES_TABLE_NAME"] = "test-hazard-coordinates"
os.environ["AIRCRAFT_HAZARD_ENCOUNTER_TABLE_NAME"] = "test-ahe"
os.environ["EVENT_BUS_NAME"] = "default"
os.environ["AHE_SCHEMA_VERSION"] = "wilvor.aircraft_hazard_encounter.v4.0"
os.environ["AHE_RETENTION_SECONDS"] = "3600"
os.environ["MAX_MATCHED_H3_CELLS"] = "200"

APP_PATH = (
    Path(__file__).resolve().parents[3]
    / "functions"
    / "encounter"
    / "processor"
    / "app.py"
)

spec = importlib.util.spec_from_file_location(
    "encounter_processor_app",
    APP_PATH,
)

app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


NOW = 1_700_000_000


def projection():
    return {
        "projection_id": "proj-1",
        "aircraft_id": "abc123",
        "aircraft_state_version": "state-v1",
        "projection_status": "READY",
        "generated_at_epoch": NOW - 60,
        "generated_at_utc": "2023-11-14T22:12:20Z",
        "valid_until_epoch": NOW + 1800,
        "valid_until_utc": "2023-11-14T22:43:20Z",
        "current_aircraft_h3_cell": "8428309ffffffff",
        "confidence": "MEDIUM",
        "correlation_id": "corr-1",
    }


def projection_cell():
    return {
        "projection_id": "proj-1",
        "h3_cell": "8428309ffffffff",
        "aircraft_id": "abc123",
        "h3_resolution": 4,
    }


def hazard_cell():
    return {
        "h3_cell": "8428309ffffffff",
        "hazard_version_key": "hazard-1#v1",
        "hazard_id": "hazard-1",
        "hazard_source_version": "v1",
        "materialization_id": "mat-1",
    }


def hazard():
    return {
        "hazard_id": "hazard-1",
        "source_version": "v1",
        "status": "ACTIVE",
        "materialization_status": "READY",
        "materialization_id": "mat-1",
        "valid_from_epoch": NOW - 600,
        "valid_to_epoch": NOW + 3600,
        "valid_from_utc": "2023-11-14T22:00:00Z",
        "valid_to_utc": "2023-11-14T23:13:20Z",
        "hazard_type": "CONVECTION",
        "severity": "SEVERE",
        "geometry_hash": "geom-1",
    }


def coordinates():
    return [
        {
            "hazard_version_key": "hazard-1#v1",
            "coordinate_key": "p0000#r0000#s0000",
            "polygon_index": 0,
            "ring_index": 0,
            "sequence_number": 0,
            "latitude": 37.0,
            "longitude": -123.0,
        },
        {
            "hazard_version_key": "hazard-1#v1",
            "coordinate_key": "p0000#r0000#s0001",
            "polygon_index": 0,
            "ring_index": 0,
            "sequence_number": 1,
            "latitude": 38.0,
            "longitude": -123.0,
        },
        {
            "hazard_version_key": "hazard-1#v1",
            "coordinate_key": "p0000#r0000#s0002",
            "polygon_index": 0,
            "ring_index": 0,
            "sequence_number": 2,
            "latitude": 38.0,
            "longitude": -122.0,
        },
        {
            "hazard_version_key": "hazard-1#v1",
            "coordinate_key": "p0000#r0000#s0003",
            "polygon_index": 0,
            "ring_index": 0,
            "sequence_number": 3,
            "latitude": 37.0,
            "longitude": -122.0,
        },
    ]


def test_projection_ready_writes_encounter(monkeypatch):
    written = []
    events = []

    monkeypatch.setattr(app, "now_epoch", lambda: NOW)
    monkeypatch.setattr(app, "get_projection", lambda projection_id: projection())
    monkeypatch.setattr(app, "query_projection_cells", lambda projection_id: [projection_cell()])
    monkeypatch.setattr(app, "query_hazard_cells", lambda h3_cell: [hazard_cell()])
    monkeypatch.setattr(app, "get_active_hazard", lambda hazard_id: hazard())
    monkeypatch.setattr(app, "query_hazard_coordinates", lambda hazard_version_key: coordinates())
    monkeypatch.setattr(
        app,
        "evaluate_geometry_overlap",
        lambda **kwargs: {
            "geometry_overlap_status": "CORRIDOR_ONLY_INTERSECTION",
            "corridor_intersects": True,
            "centerline_intersects": False,
            "inside_now": False,
            "exact_intersection_confirmed": True,
        },
    )
    monkeypatch.setattr(app, "write_encounter", lambda item: written.append(item))
    monkeypatch.setattr(
        app,
        "publish_encounter_event",
        lambda **kwargs: events.append(kwargs),
    )

    result = app.evaluate_projection("proj-1")

    assert result["processed"] is True
    assert result["encounters_written"] == 1
    assert written[0]["encounter_id"] == "proj-1#hazard-1#v1"
    assert written[0]["aircraft_id"] == "abc123"
    assert written[0]["hazard_id"] == "hazard-1"
    assert written[0]["exact_intersection_confirmed"] is True
    assert written[0]["encounter_state"] == "DETECTED"
    assert events[0]["detail_type"] == "encounter.updated"


def test_building_hazard_is_skipped(monkeypatch):
    bad_hazard = hazard()
    bad_hazard["materialization_status"] = "BUILDING"

    monkeypatch.setattr(app, "now_epoch", lambda: NOW)
    monkeypatch.setattr(app, "get_projection", lambda projection_id: projection())
    monkeypatch.setattr(app, "query_projection_cells", lambda projection_id: [projection_cell()])
    monkeypatch.setattr(app, "query_hazard_cells", lambda h3_cell: [hazard_cell()])
    monkeypatch.setattr(app, "get_active_hazard", lambda hazard_id: bad_hazard)

    result = app.evaluate_projection("proj-1")

    assert result["processed"] is True
    assert result["encounters_written"] == 0
    assert result["skipped_candidates"] == 1


def test_no_hazard_cell_match_returns_no_candidates(monkeypatch):
    monkeypatch.setattr(app, "now_epoch", lambda: NOW)
    monkeypatch.setattr(app, "get_projection", lambda projection_id: projection())
    monkeypatch.setattr(app, "query_projection_cells", lambda projection_id: [projection_cell()])
    monkeypatch.setattr(app, "query_hazard_cells", lambda h3_cell: [])

    result = app.evaluate_projection("proj-1")

    assert result["processed"] is True
    assert result["reason"] == "NO_HAZARD_CELL_MATCH"
    assert result["encounters_written"] == 0


def test_expired_projection_is_rejected(monkeypatch):
    expired = projection()
    expired["valid_until_epoch"] = NOW - 1

    monkeypatch.setattr(app, "now_epoch", lambda: NOW)
    monkeypatch.setattr(app, "get_projection", lambda projection_id: expired)

    result = app.evaluate_projection("proj-1")

    assert result["processed"] is False
    assert result["reason"] == "PROJECTION_EXPIRED"


def test_hazard_materialized_finds_projection_ids(monkeypatch):
    monkeypatch.setattr(
        app,
        "query_hazard_cells_by_hazard_version",
        lambda hazard_version_key: [
            {
                "hazard_version_key": "hazard-1#v1",
                "h3_cell": "8428309ffffffff",
            }
        ],
    )

    monkeypatch.setattr(
        app,
        "query_projection_cells_by_h3",
        lambda h3_cell: [
            {
                "projection_id": "proj-1",
                "h3_cell": h3_cell,
            },
            {
                "projection_id": "proj-1",
                "h3_cell": h3_cell,
            },
            {
                "projection_id": "proj-2",
                "h3_cell": h3_cell,
            },
        ],
    )

    result = app.projection_ids_for_hazard_version("hazard-1#v1")

    assert result == ["proj-1", "proj-2"]