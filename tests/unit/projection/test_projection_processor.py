import importlib.util
import os
from decimal import Decimal
from pathlib import Path


os.environ["AWS_DEFAULT_REGION"] = "us-west-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_EC2_METADATA_DISABLED"] = "true"

os.environ["AIRCRAFT_CURRENT_STATE_TABLE_NAME"] = "test-aircraft"
os.environ["IMPACT_CELLS_TABLE_NAME"] = "test-impact"
os.environ["ACTIVE_HAZARDS_TABLE_NAME"] = "test-hazards"

APP_PATH = (
    Path(__file__).resolve().parents[3]
    / "functions"
    / "projection"
    / "processor"
    / "app.py"
)

spec = importlib.util.spec_from_file_location(
    "projection_processor_app",
    APP_PATH,
)

app = importlib.util.module_from_spec(spec)

os.environ.setdefault(
    "AIRCRAFT_PROJECTION_TABLE_NAME",
    "test-aircraft-projection",
)

os.environ.setdefault(
    "AIRCRAFT_PROJECTION_POINTS_TABLE_NAME",
    "test-aircraft-projection-points",
)

os.environ.setdefault(
    "AIRCRAFT_PROJECTION_CELLS_TABLE_NAME",
    "test-aircraft-projection-cells",
)

os.environ.setdefault(
    "PROJECTION_ALGORITHM_VERSION",
    "wilvor.projection.constant_velocity.v1",
)

os.environ.setdefault(
    "PROJECTION_CONFIG_VERSION",
    "wilvor.projection.config.v1",
)

os.environ.setdefault(
    "PROJECTION_SCHEMA_VERSION",
    "wilvor.aircraft_projection.v4.0",
)

os.environ.setdefault(
    "PROJECTION_POINTS_SCHEMA_VERSION",
    "wilvor.aircraft_projection_points.v4.0",
)

os.environ.setdefault(
    "PROJECTION_CELLS_SCHEMA_VERSION",
    "wilvor.aircraft_projection_cells.v4.0",
)

os.environ.setdefault(
    "EVENT_BUS_NAME",
    "default",
)

os.environ.setdefault(
    "PROJECTION_HORIZONS_MIN",
    "5,10,15,30",
)

os.environ.setdefault(
    "CORRIDOR_GRID_DISTANCES",
    "0,0,1,1",
)

os.environ.setdefault(
    "PROJECTION_RETENTION_SECONDS",
    "3600",
)

os.environ.setdefault(
    "MAX_CORRIDOR_CELLS",
    "2000",
)

os.environ.setdefault(
    "MAX_TRIGGER_HAZARDS",
    "25",
)



spec.loader.exec_module(app)


NOW = 1_700_000_000


def aircraft_state():
    return {
        "aircraft_id": "abc123",
        "state_version": "state-v1",
        "has_position": True,
        "current_h3_cell": "8428309ffffffff",
        "h3_resolution": 4,
        "freshness_status": "FRESH",
        "position_time_epoch": NOW - 20,
        "on_ground": False,
        "ground_speed_kt": 450,
        "track_deg": 90,
        "correlation_id": "corr-1",
    }


def impact_cell():
    return {
        "h3_cell": "8428309ffffffff",
        "hazard_version_key": "hazard-1#v1",
        "hazard_id": "hazard-1",
        "hazard_source_version": "v1",
        "h3_resolution": 4,
        "impact_scope": "PROJECTION_TRIGGER_AREA",
        "valid_from_utc": "2023-11-14T22:00:00Z",
        "valid_to_utc": "2023-11-14T23:00:00Z",
        "materialization_id": "mat-1",
    }


def active_hazard():
    return {
        "hazard_id": "hazard-1",
        "source_version": "v1",
        "status": "ACTIVE",
        "materialization_status": "READY",
        "materialization_id": "mat-1",
        "valid_from_epoch": NOW - 600,
        "valid_to_epoch": NOW + 3600,
    }


def detail(state_version="state-v1"):
    return {
        "aircraft_id": "abc123",
        "state_version": state_version,
        "correlation_id": "corr-event-1",
    }


def test_valid_current_impact_match_is_eligible(monkeypatch):
    monkeypatch.setattr(app, "now_epoch", lambda: NOW)

    monkeypatch.setattr(
        app,
        "get_aircraft_state",
        lambda aircraft_id: aircraft_state(),
    )

    monkeypatch.setattr(
        app,
        "query_impact_cells",
        lambda h3_cell: [impact_cell()],
    )

    monkeypatch.setattr(
        app,
        "get_active_hazard",
        lambda hazard_id: active_hazard(),
    )

    result = app.evaluate_eligibility(detail())

    assert result["eligible"] is True
    assert result["reason"] == "CURRENT_IMPACT_MATCH"
    assert result["aircraft_id"] == "abc123"
    assert result["aircraft_state_version"] == "state-v1"

    assert result["matched_impact_cells"] == [
        "8428309ffffffff"
    ]

    assert result["trigger_hazard_ids"] == [
        "hazard-1"
    ]

    assert result["trigger_hazard_version_keys"] == [
        "hazard-1#v1"
    ]


def test_stale_aircraft_event_is_rejected(monkeypatch):
    monkeypatch.setattr(
        app,
        "get_aircraft_state",
        lambda aircraft_id: aircraft_state(),
    )

    def should_not_query_impact_cells(h3_cell):
        raise AssertionError(
            "ImpactCells must not be queried for stale events"
        )

    monkeypatch.setattr(
        app,
        "query_impact_cells",
        should_not_query_impact_cells,
    )

    result = app.evaluate_eligibility(
        detail("old-state-version")
    )

    assert result["eligible"] is False
    assert result["reason"] == "STALE_EVENT_VERSION"
    assert result["current_state_version"] == "state-v1"


def test_no_impact_cell_match_is_not_eligible(monkeypatch):
    monkeypatch.setattr(app, "now_epoch", lambda: NOW)

    monkeypatch.setattr(
        app,
        "get_aircraft_state",
        lambda aircraft_id: aircraft_state(),
    )

    monkeypatch.setattr(
        app,
        "query_impact_cells",
        lambda h3_cell: [],
    )

    result = app.evaluate_eligibility(detail())

    assert result["eligible"] is False
    assert result["reason"] == "NO_CURRENT_IMPACT_MATCH"
    assert result["impact_candidates_found"] == 0


def test_building_hazard_is_not_eligible(monkeypatch):
    monkeypatch.setattr(app, "now_epoch", lambda: NOW)

    monkeypatch.setattr(
        app,
        "get_aircraft_state",
        lambda aircraft_id: aircraft_state(),
    )

    monkeypatch.setattr(
        app,
        "query_impact_cells",
        lambda h3_cell: [impact_cell()],
    )

    hazard = active_hazard()
    hazard["materialization_status"] = "BUILDING"

    monkeypatch.setattr(
        app,
        "get_active_hazard",
        lambda hazard_id: hazard,
    )

    result = app.evaluate_eligibility(detail())

    assert result["eligible"] is False
    assert result["reason"] == "NO_CURRENT_IMPACT_MATCH"
    assert result["impact_candidates_found"] == 1


def test_ground_aircraft_is_not_eligible(monkeypatch):
    monkeypatch.setattr(app, "now_epoch", lambda: NOW)

    state = aircraft_state()
    state["on_ground"] = True

    monkeypatch.setattr(
        app,
        "get_aircraft_state",
        lambda aircraft_id: state,
    )

    result = app.evaluate_eligibility(detail())

    assert result["eligible"] is False
    assert result["reason"] == "AIRCRAFT_ON_GROUND"


def test_projection_parent_stores_altitude_as_decimal():
    state = aircraft_state()
    state["baro_altitude_ft"] = 35000.4

    parent = app.build_projection_parent(
        state=state,
        eligibility={
            "eligibility_checked_at_utc": "2023-11-14T22:13:20Z",
            "matched_impact_cells": ["8428309ffffffff"],
            "trigger_hazard_ids": ["hazard-1"],
            "projection_trigger_reason": "CURRENT_IMPACT_MATCH",
            "correlation_id": "corr-1",
        },
        projection_id="proj-1",
        idempotency_key="idemp-1",
        generated_at_epoch=NOW,
        points=[{"confidence": "HIGH"}],
        corridor_cells=["8428309ffffffff"],
    )

    assert isinstance(parent["current_altitude_ft"], Decimal)
    assert not isinstance(parent["current_altitude_ft"], float)
    assert parent["current_altitude_ft"] == Decimal("35000.4")
    assert parent["freshness_status"] == "FRESH"