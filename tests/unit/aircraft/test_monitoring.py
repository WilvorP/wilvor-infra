from __future__ import annotations

import json

from wilvor_aircraft import monitoring


def test_emit_metric_prints_valid_emf_payload(monkeypatch, capsys):
    monkeypatch.setenv("ENVIRONMENT", "unit-test")
    monkeypatch.setattr(monitoring, "now_ms", lambda: 1_234_567_890)

    monitoring.emit_metric(
        pipeline="aircraft",
        component="raw_processor",
        stage="raw_to_clean",
        metrics={
            "ValidRecords": 3,
            "RejectedRecords": 1,
        },
        properties={
            "event": "aircraft_raw_processor_metrics",
            "invocation_id": "request-123",
        },
    )

    payload = json.loads(capsys.readouterr().out)

    assert payload["Environment"] == "unit-test"
    assert payload["Pipeline"] == "aircraft"
    assert payload["Component"] == "raw_processor"
    assert payload["Stage"] == "raw_to_clean"
    assert payload["ValidRecords"] == 3
    assert payload["RejectedRecords"] == 1
    assert payload["event"] == "aircraft_raw_processor_metrics"
    assert payload["_aws"]["Timestamp"] == 1_234_567_890

    definitions = payload["_aws"]["CloudWatchMetrics"][0]["Metrics"]
    assert definitions == [
        {"Name": "ValidRecords", "Unit": "Count"},
        {"Name": "RejectedRecords", "Unit": "Count"},
    ]
