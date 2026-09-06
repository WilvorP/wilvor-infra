import importlib.util
import os
from pathlib import Path


os.environ["AWS_DEFAULT_REGION"] = "us-west-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
os.environ["RECOMMENDATIONS_TABLE_NAME"] = "test-recommendations"
os.environ["RISK_RESULTS_TABLE_NAME"] = "test-risk-results"
os.environ["ACTIVE_ALERTS_TABLE_NAME"] = "test-alerts"
os.environ["EVENT_BUS_NAME"] = "default"

APP_PATH = (
    Path(__file__).resolve().parents[3]
    / "functions"
    / "active_alerts"
    / "processor"
    / "app.py"
)

spec = importlib.util.spec_from_file_location(
    "active_alert_processor_app",
    APP_PATH,
)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def recommendation(**overrides):
    item = {
        "recommendation_id": "rec-1",
        "aircraft_id": "abc123",
        "hazard_id": "hazard-1",
        "hazard_source_version": "v1",
        "risk_id": "risk-1",
        "risk_level": "MEDIUM",
        "risk_score": 45,
        "primary_action_type": "MONITOR_AND_PREPARE_OPTIONS",
        "valid_until_utc": "2023-11-14T23:13:20Z",
        "correlation_id": "corr-1",
        "expires_at_epoch": 1_700_003_600,
    }
    item.update(overrides)
    return item


def test_fingerprint_is_stable_across_recommendation_ids():
    first = app.fingerprint(recommendation())
    second = app.fingerprint(
        recommendation(recommendation_id="rec-2", risk_id="risk-2")
    )
    assert first == second


def test_same_fingerprint_with_new_recommendation_updates_one_row(monkeypatch):
    rec = recommendation(recommendation_id="rec-2")
    existing = {
        "fingerprint": "alertfp#1",
        "alert_id": "alert#1",
        "recommendation_id": "rec-1",
        "alert_state": "NEW",
        "notification_count": 1,
    }
    updates = []

    monkeypatch.setattr(app, "get_recommendation", lambda recommendation_id: rec)
    monkeypatch.setattr(app, "fingerprint", lambda item: "alertfp#1")
    monkeypatch.setattr(
        app.alerts_table,
        "get_item",
        lambda **kwargs: {"Item": existing},
    )
    monkeypatch.setattr(
        app.alerts_table,
        "update_item",
        lambda **kwargs: updates.append(kwargs) or {"Attributes": {**existing, "alert_state": "UPDATED"}},
    )
    monkeypatch.setattr(app, "publish", lambda *args, **kwargs: None)

    result = app.handle_recommendation({"recommendation_id": "rec-2"})

    assert result["alert_state"] == "UPDATED"
    assert len(updates) == 1


def test_encounter_resolve_resolves_active_alerts(monkeypatch):
    resolved = []

    monkeypatch.setattr(
        app,
        "risks_for_encounter",
        lambda encounter_id: [{"risk_id": "risk-1", "aircraft_id": "abc123"}],
    )
    monkeypatch.setattr(
        app,
        "resolve_risk",
        lambda risk_id, aircraft_id=None: resolved.append((risk_id, aircraft_id)) or 1,
    )

    result = app.lambda_handler(
        {
            "detail-type": "encounter.resolved",
            "detail": {"encounter_id": "proj-1#hazard-1#v1"},
        },
        None,
    )

    assert result["resolved_alert_count"] == 1
    assert resolved == [("risk-1", "abc123")]


def test_resolve_item_replay_is_idempotent():
    assert (
        app.resolve_item(
            {
                "fingerprint": "alertfp#1",
                "alert_state": "RESOLVED",
            },
            "replay",
        )
        is False
    )
