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
] = "wilvor.risk.ruleset.v2"

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

    assert item["altitude_component_score"] == 0
    assert item["risk_level"] == "LOW"
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


def _score(monkeypatch, **overrides):
    monkeypatch.setattr(app, "now_epoch", lambda: NOW)
    encounter = base_encounter()
    encounter.update(overrides)
    return app.build_risk_result(encounter)


def test_unknown_altitude_contributes_zero_and_reduces_confidence(monkeypatch):
    item = _score(
        monkeypatch,
        altitude_overlap_status="UNKNOWN",
        inside_now=True,
        geometry_overlap_status="INSIDE_NOW",
        trajectory_confidence="HIGH",
        freshness_status="FRESH",
    )

    assert item["altitude_component_score"] == 0
    assert item["risk_level"] == "MEDIUM"
    assert item["confidence"] == "LOW"
    assert any("unknown" in reason.lower() for reason in item["reasons"])
    assert any("altitude" in limitation.lower() for limitation in item["limitations"])


def test_no_overlap_is_materially_lower_than_overlap(monkeypatch):
    overlap = _score(
        monkeypatch,
        altitude_overlap_status="OVERLAP",
        inside_now=True,
        geometry_overlap_status="INSIDE_NOW",
        trajectory_confidence="HIGH",
        freshness_status="FRESH",
    )
    separated = _score(
        monkeypatch,
        altitude_overlap_status="NO_OVERLAP",
        inside_now=True,
        geometry_overlap_status="INSIDE_NOW",
        trajectory_confidence="HIGH",
        freshness_status="FRESH",
    )

    assert overlap["risk_level"] == "HIGH"
    assert separated["risk_level"] == "LOW"
    assert separated["risk_score"] < overlap["risk_score"]
    assert separated["altitude_component_score"] == 0


def test_weak_long_horizon_is_low(monkeypatch):
    monkeypatch.setattr(app, "now_epoch", lambda: NOW)
    encounter = base_encounter()
    encounter.update(
        {
            "geometry_overlap_status": "CORRIDOR_ONLY_INTERSECTION",
            "inside_now": False,
            "corridor_intersects": True,
            "altitude_overlap_status": "UNKNOWN",
            "trajectory_confidence": "LOW",
            "encounter_confidence": "LOW",
            "freshness_status": "FRESH",
        }
    )
    encounter.pop("first_intersection_horizon_min", None)
    item = app.build_risk_result(encounter)

    assert item["risk_level"] == "LOW"
    assert item["geometry_component_score"] == 12


def test_meaningful_unknown_altitude_can_be_medium(monkeypatch):
    item = _score(
        monkeypatch,
        altitude_overlap_status="UNKNOWN",
        inside_now=True,
        geometry_overlap_status="INSIDE_NOW",
        trajectory_confidence="MEDIUM",
        freshness_status="FRESH",
    )

    assert item["risk_level"] == "MEDIUM"


def test_strong_supported_encounter_can_be_high(monkeypatch):
    item = _score(monkeypatch)

    assert item["risk_level"] == "HIGH"
    assert item["risk_score"] >= 70
    assert item["confidence"] == "HIGH"
    assert any("inside" in reason.lower() for reason in item["reasons"])


def test_low_confidence_prevents_high(monkeypatch):
    item = _score(
        monkeypatch,
        trajectory_confidence="LOW",
        encounter_confidence="LOW",
        altitude_overlap_status="OVERLAP",
        inside_now=True,
        geometry_overlap_status="INSIDE_NOW",
        freshness_status="FRESH",
    )

    assert item["risk_level"] != "HIGH"


def test_stale_data_cannot_increase_risk(monkeypatch):
    item = _score(
        monkeypatch,
        freshness_status="STALE",
        inside_now=True,
        geometry_overlap_status="INSIDE_NOW",
        altitude_overlap_status="OVERLAP",
        trajectory_confidence="HIGH",
    )

    assert item["risk_level"] == "LOW"
    assert item["freshness_component_score"] == 0


def test_equivalent_context_is_idempotent_without_detected_at(monkeypatch):
    monkeypatch.setattr(app, "now_epoch", lambda: NOW)
    first = base_encounter()
    second = dict(first)
    second["detected_at_epoch"] = NOW + 90
    second["detected_at_utc"] = "2023-11-14T22:14:50Z"

    assert (
        app.build_risk_result(first)["risk_id"]
        == app.build_risk_result(second)["risk_id"]
    )


def test_material_change_creates_new_risk_id(monkeypatch):
    monkeypatch.setattr(app, "now_epoch", lambda: NOW)
    first = app.build_risk_result(base_encounter())
    changed = base_encounter()
    changed["altitude_overlap_status"] = "UNKNOWN"
    second = app.build_risk_result(changed)

    assert first["risk_id"] != second["risk_id"]


def test_lambda_retry_does_not_republish_equivalent_risk(monkeypatch):
    encounter = base_encounter()
    stored = app.build_risk_result(encounter)
    calls = []

    monkeypatch.setattr(app, "now_epoch", lambda: NOW)
    monkeypatch.setattr(app, "get_encounter", lambda encounter_id: encounter)
    monkeypatch.setattr(
        app,
        "persist_risk_result",
        lambda item: (stored, False),
    )
    monkeypatch.setattr(
        app,
        "publish_risk_event",
        lambda **kwargs: calls.append("publish") or "risk.updated",
    )
    monkeypatch.setattr(app, "emit_metrics", lambda **kwargs: None)

    result = app.lambda_handler(
        {
            "detail-type": "encounter.updated",
            "detail": {"encounter_id": encounter["encounter_id"]},
        },
        None,
    )

    assert result["created"] is False
    assert result["published_event"] is None
    assert calls == []