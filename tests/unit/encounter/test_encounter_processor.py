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


def stub_current_projection(monkeypatch, current=None):
    monkeypatch.setattr(
        app,
        "projection_is_operationally_current",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        app,
        "supersede_stale_encounters",
        lambda **kwargs: 0,
    )


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

    stub_current_projection(monkeypatch)
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

    stub_current_projection(monkeypatch)
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
    stub_current_projection(monkeypatch)
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


def test_unknown_altitude_does_not_confirm_or_erase_horizontal_encounter():
    geometry = {
        "geometry_overlap_status": "CORRIDOR_ONLY_INTERSECTION",
        "corridor_intersects": True,
        "centerline_intersects": False,
        "inside_now": False,
        "exact_intersection_confirmed": True,
    }

    item = app.build_encounter_item(
        projection=projection(),
        hazard=hazard(),
        candidate={
            "hazard_version_key": "hazard-1#v1",
            "hazard_id": "hazard-1",
        },
        matched_h3_cells=["8428309ffffffff"],
        geometry_result=geometry,
        detected_epoch=NOW,
    )

    assert item["altitude_overlap_status"] == "UNKNOWN"
    assert item["exact_intersection_confirmed"] is True
    assert item["encounter_state"] == "DETECTED"


def test_altitude_overlap_and_no_overlap_from_available_bands():
    proj = projection()
    proj["current_altitude_ft"] = 28000
    overlapping = hazard()
    overlapping["minimum_lower_altitude_ft"] = 20000
    overlapping["maximum_upper_altitude_ft"] = 35000
    separated = hazard()
    separated["minimum_lower_altitude_ft"] = 1000
    separated["maximum_upper_altitude_ft"] = 5000

    assert (
        app.altitude_overlap_status(projection=proj, hazard=overlapping)
        == "OVERLAP"
    )
    assert (
        app.altitude_overlap_status(projection=proj, hazard=separated)
        == "NO_OVERLAP"
    )
    assert (
        app.altitude_overlap_status(projection=projection(), hazard=overlapping)
        == "UNKNOWN"
    )


def test_stale_projection_is_not_evaluated(monkeypatch):
    monkeypatch.setattr(app, "now_epoch", lambda: NOW)
    monkeypatch.setattr(app, "get_projection", lambda projection_id: projection())
    monkeypatch.setattr(
        app,
        "projection_is_operationally_current",
        lambda *args, **kwargs: False,
    )

    result = app.evaluate_projection("proj-1")

    assert result["processed"] is False
    assert result["reason"] == "PROJECTION_NOT_CURRENT"
    assert result["encounters_written"] == 0


def test_newer_projection_supersedes_only_same_hazard(monkeypatch):
    old = {
        "encounter_id": "proj-old#hazard-1#v1",
        "aircraft_id": "abc123",
        "projection_id": "proj-old",
        "hazard_id": "hazard-1",
        "encounter_state": "DETECTED",
        "detected_at_epoch": NOW - 60,
        "schema_version": "wilvor.aircraft_hazard_encounter.v4.0",
        "correlation_id": "corr-old",
        "aircraft_state_version": "state-v0",
        "hazard_source_version": "v1",
        "hazard_version_key": "hazard-1#v1",
        "geometry_overlap_status": "CORRIDOR_ONLY_INTERSECTION",
        "time_overlap_status": "OVERLAP",
        "altitude_overlap_status": "UNKNOWN",
        "exact_intersection_confirmed": True,
    }
    other = {
        **old,
        "encounter_id": "proj-1#hazard-2#v1",
        "projection_id": "proj-1",
        "hazard_id": "hazard-2",
        "hazard_version_key": "hazard-2#v1",
        "hazard_source_version": "v1",
    }
    published = []
    written = []

    monkeypatch.setattr(
        app,
        "query_encounters_for_aircraft",
        lambda aircraft_id: [old, other],
    )
    monkeypatch.setattr(
        app,
        "persist_resolved_encounter",
        lambda item: written.append(item) or True,
    )
    monkeypatch.setattr(
        app,
        "publish_encounter_event",
        lambda **kwargs: published.append(kwargs),
    )

    resolved = app.supersede_stale_encounters(
        aircraft_id="abc123",
        current_projection_id="proj-1",
        written_encounter_ids={"proj-1#hazard-2#v1"},
        current_epoch=NOW,
        full_evaluation=True,
    )

    assert resolved == 1
    assert written[0]["encounter_state"] == "SUPERSEDED"
    assert written[0]["encounter_id"] == "proj-old#hazard-1#v1"
    assert other["encounter_state"] == "DETECTED"
    assert published[0]["detail_type"] == "encounter.resolved"


def test_resolve_replay_is_idempotent(monkeypatch):
    existing = {
        "encounter_id": "proj-1#hazard-1#v1",
        "encounter_state": "SUPERSEDED",
        "aircraft_id": "abc123",
        "projection_id": "proj-1",
        "hazard_id": "hazard-1",
        "hazard_source_version": "v1",
        "hazard_version_key": "hazard-1#v1",
        "geometry_overlap_status": "CORRIDOR_ONLY_INTERSECTION",
        "time_overlap_status": "OVERLAP",
        "altitude_overlap_status": "UNKNOWN",
        "exact_intersection_confirmed": True,
        "detected_at_epoch": NOW,
        "schema_version": "v",
        "correlation_id": "c",
        "aircraft_state_version": "s",
    }
    published = []

    monkeypatch.setattr(
        app,
        "publish_encounter_event",
        lambda **kwargs: published.append(kwargs),
    )

    assert (
        app.resolve_encounter(
            existing,
            encounter_state="SUPERSEDED",
            reason="replay",
            current_epoch=NOW,
        )
        is False
    )
    assert published == []