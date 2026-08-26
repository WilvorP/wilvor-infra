import importlib.util
import os
from decimal import Decimal
from pathlib import Path


os.environ["AWS_DEFAULT_REGION"] = "us-west-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_EC2_METADATA_DISABLED"] = "true"

os.environ[
    "AIRCRAFT_HAZARD_ENCOUNTER_TABLE_NAME"
] = "test-aircraft-hazard-encounter"

os.environ[
    "RISK_RESULTS_TABLE_NAME"
] = "test-risk-results"

os.environ[
    "EVENT_BUS_NAME"
] = "default"

os.environ[
    "RISK_SCHEMA_VERSION"
] = "wilvor.risk_results.v4.0"

os.environ[
    "SCORING_RULESET_VERSION"
] = "wilvor.risk.ruleset.v1"

os.environ[
    "SCORING_CONFIG_VERSION"
] = "wilvor.risk.config.dev.v1"

os.environ[
    "RISK_RETENTION_SECONDS"
] = "86400"


APP_PATH = (
    Path(__file__).resolve().parents[3]
    / "functions"
    / "risk"
    / "processor"
    / "app.py"
)

spec = importlib.util.spec_from_file_location(
    "risk_processor_app",
    APP_PATH,
)

app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


NOW = 1_700_000_000


def base_encounter():
    return {
        "encounter_id": (
            "proj-1#hazard-1#v1"
        ),
        "aircraft_id": "abc123",
        "projection_id": "proj-1",
        "hazard_id": "hazard-1",
        "hazard_source_version": "v1",
        "hazard_type": "CONVECTION",
        "severity": "SEVERE",
        "geometry_overlap_status": (
            "INSIDE_NOW"
        ),
        "time_overlap_status": "OVERLAP",
        "altitude_overlap_status": "OVERLAP",
        "inside_now": True,
        "centerline_intersects": False,
        "corridor_intersects": True,
        "exact_intersection_confirmed": True,
        "first_intersection_horizon_min": (
            Decimal("3")
        ),
        "trajectory_confidence": "HIGH",
        "encounter_confidence": "HIGH",
        "freshness_status": "FRESH",
        "encounter_state": "DETECTED",
        "valid_from_utc": (
            "2023-11-14T22:00:00Z"
        ),
        "valid_to_utc": (
            "2023-11-14T23:13:20Z"
        ),
        "correlation_id": "corr-1",
        "schema_version": (
            "wilvor.aircraft_hazard_encounter.v4.0"
        ),
    }


def test_high_risk_for_confirmed_near_term_encounter(
    monkeypatch,
):
    monkeypatch.setattr(
        app,
        "now_epoch",
        lambda: NOW,
    )

    item = app.build_risk_result(
        base_encounter()
    )

    assert item["risk_score"] >= 70
    assert item["risk_level"] == "HIGH"
    assert item["confidence"] == "HIGH"

    assert (
        item["hazard_component_score"]
        > 0
    )

    assert (
        item["geometry_component_score"]
        > 0
    )

    assert (
        item["time_component_score"]
        > 0
    )

    assert (
        item["altitude_component_score"]
        > 0
    )


def test_unknown_altitude_is_not_classified_low(
    monkeypatch,
):
    monkeypatch.setattr(
        app,
        "now_epoch",
        lambda: NOW,
    )

    encounter = base_encounter()

    encounter["hazard_type"] = "UNKNOWN"
    encounter["severity"] = None

    encounter[
        "geometry_overlap_status"
    ] = "NO_INTERSECTION"

    encounter[
        "time_overlap_status"
    ] = "NO_OVERLAP"

    encounter[
        "altitude_overlap_status"
    ] = "UNKNOWN"

    encounter["inside_now"] = False
    encounter["corridor_intersects"] = False

    encounter.pop(
        "first_intersection_horizon_min"
    )

    item = app.build_risk_result(
        encounter
    )

    assert item["risk_score"] < 40
    assert item["risk_level"] == "UNKNOWN"
    assert item["confidence"] == "LOW"

    assert any(
        "altitude" in limitation.lower()
        for limitation in item[
            "limitations"
        ]
    )


def test_missing_freshness_reduces_confidence(
    monkeypatch,
):
    monkeypatch.setattr(
        app,
        "now_epoch",
        lambda: NOW,
    )

    encounter = base_encounter()

    encounter.pop(
        "freshness_status"
    )

    item = app.build_risk_result(
        encounter
    )

    assert (
        item["freshness_status"]
        == "UNAVAILABLE"
    )

    assert item["confidence"] == "LOW"


def test_resolved_encounter_creates_zero_risk(
    monkeypatch,
):
    monkeypatch.setattr(
        app,
        "now_epoch",
        lambda: NOW,
    )

    encounter = base_encounter()

    encounter[
        "encounter_state"
    ] = "RESOLVED"

    item = app.build_risk_result(
        encounter
    )

    assert item["risk_score"] == 0
    assert item["risk_level"] == "LOW"


def test_same_input_produces_same_risk_id(
    monkeypatch,
):
    monkeypatch.setattr(
        app,
        "now_epoch",
        lambda: NOW,
    )

    encounter = base_encounter()

    first = app.build_risk_result(
        encounter
    )

    second = app.build_risk_result(
        encounter
    )

    assert (
        first["risk_id"]
        == second["risk_id"]
    )


def test_lambda_handler_publishes_after_persist(
    monkeypatch,
):
    encounter = base_encounter()

    monkeypatch.setattr(
        app,
        "now_epoch",
        lambda: NOW,
    )

    monkeypatch.setattr(
        app,
        "get_encounter",
        lambda encounter_id: encounter,
    )

    stored = app.build_risk_result(
        encounter
    )

    calls = []

    def fake_persist(item):
        calls.append("persist")
        return item, True

    def fake_publish(
        *,
        item,
        encounter_state,
    ):
        calls.append("publish")
        return "risk.updated"

    monkeypatch.setattr(
        app,
        "persist_risk_result",
        fake_persist,
    )

    monkeypatch.setattr(
        app,
        "publish_risk_event",
        fake_publish,
    )

    monkeypatch.setattr(
        app,
        "emit_metrics",
        lambda **kwargs: calls.append(
            "metrics"
        ),
    )

    result = app.lambda_handler(
        {
            "detail-type": (
                "encounter.updated"
            ),
            "detail": {
                "encounter_id": (
                    encounter[
                        "encounter_id"
                    ]
                )
            },
        },
        None,
    )

    assert result["processed"] is True
    assert result["published_event"] == (
        "risk.updated"
    )

    assert calls == [
        "persist",
        "publish",
        "metrics",
    ]