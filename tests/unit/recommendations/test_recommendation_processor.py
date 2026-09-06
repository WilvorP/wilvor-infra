import importlib.util
import os
from decimal import Decimal
from pathlib import Path


os.environ["AWS_DEFAULT_REGION"] = "us-west-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
os.environ["RISK_RESULTS_TABLE_NAME"] = "test-risk-results"
os.environ["AIRPORT_ASSESSMENT_TABLE_NAME"] = "test-airport-assessment"
os.environ["RECOMMENDATIONS_TABLE_NAME"] = "test-recommendations"
os.environ["EVENT_BUS_NAME"] = "default"

APP_PATH = (
    Path(__file__).resolve().parents[3]
    / "functions"
    / "recommendations"
    / "processor"
    / "app.py"
)

spec = importlib.util.spec_from_file_location(
    "recommendation_processor_app",
    APP_PATH,
)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def risk(**overrides):
    item = {
        "risk_id": "risk#1",
        "aircraft_id": "abc123",
        "hazard_id": "hazard-1",
        "hazard_source_version": "v1",
        "risk_level": "LOW",
        "risk_score": Decimal("20"),
        "confidence": "MEDIUM",
        "reasons": ["Projected corridor intersects an active hazard."],
        "limitations": ["Altitude relationship is unknown."],
        "valid_until_utc": "2023-11-14T23:13:20Z",
        "schema_version": "wilvor.risk_results.v4.0",
        "correlation_id": "corr-1",
    }
    item.update(overrides)
    return item


def test_low_risk_is_monitor():
    assert app.action_for("LOW") == "MONITOR"
    item = app.build_recommendation(risk(), None, [])
    assert item["primary_action_type"] == "MONITOR"
    assert "evaluate diversion" not in item["primary_action_details"]["advisory"].lower()


def test_medium_risk_prepares_options():
    item = app.build_recommendation(risk(risk_level="MEDIUM"), None, [])
    assert item["primary_action_type"] == "MONITOR_AND_PREPARE_OPTIONS"


def test_high_without_airport_evidence_does_not_divert():
    item = app.build_recommendation(risk(risk_level="HIGH"), None, [])
    assert item["primary_action_type"] == "MONITOR_AND_PREPARE_OPTIONS"
    assert "evaluate diversion" not in item["primary_action_details"]["advisory"].lower()
    assert any("diversion is not proposed" in reason.lower() for reason in item["reasons"])
    assert "human operational review" in item["advisory_notice"].lower()


def test_high_with_complete_assessment_evaluates_diversion():
    assessments = [
        {
            "assessment_status": "COMPLETE",
            "rank": 1,
            "total_airport_score": 80,
            "airport_id": "KDEN",
            "airport_assessment_id": "assess-1",
        }
    ]
    item = app.build_recommendation(
        risk(risk_level="HIGH"),
        "eval-1",
        assessments,
    )
    assert item["primary_action_type"] == "EVALUATE_DIVERSION"
    assert item["primary_action_details"]["advisory"].startswith("Evaluate diversion")
    assert "Divert to" not in item["primary_action_details"]["advisory"]


def test_equivalent_risk_context_reuses_recommendation_id():
    first = app.build_recommendation(risk(), None, [])
    second = app.build_recommendation(risk(), None, [])
    assert first["recommendation_id"] == second["recommendation_id"]
