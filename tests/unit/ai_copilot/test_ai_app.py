import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from service import ServiceResult


APP_PATH = (
    Path(__file__).resolve().parents[3]
    / "functions"
    / "ai_copilot"
    / "app.py"
)
SPEC = importlib.util.spec_from_file_location(
    "unit_ai_copilot_app",
    APP_PATH,
)
assert SPEC and SPEC.loader
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)


class FakeService:
    def _result(self):
        return ServiceResult(
            {
                "answer": "ok",
                "evidence": [],
                "confidence": "LOW",
                "limitations": [],
                "dataFreshnessWarnings": [],
                "toolCalls": [],
                "advisoryOnly": True,
                "humanReviewRequired": True,
                "generatedAt": "now",
                "modelId": "fake",
                "promptVersion": "p1",
                "cache": {"hit": False},
            }
        )

    def network_summary(self):
        return self._result()

    def aircraft_explanation(self, _):
        return self._result()

    def airport_summary(self, _):
        return self._result()

    def recommendation_explanation(self, _):
        return self._result()

    def incident_summary(self, _):
        return self._result()

    def chat(self, _):
        return self._result()


class FakeInsights:
    def list_for_subject(self, **_):
        return []


def runtime(monkeypatch):
    monkeypatch.setattr(
        app,
        "_RUNTIME",
        {
            "settings": SimpleNamespace(
                max_request_bytes=1000,
                max_message_chars=100,
                max_history_items=2,
                max_history_item_chars=100,
            ),
            "service": FakeService(),
            "insights": FakeInsights(),
        },
    )


def http_event(method, path, body=None):
    return {
        "rawPath": path,
        "body": body,
        "requestContext": {
            "requestId": "request-123",
            "http": {"method": method},
        },
    }


def payload(result):
    return json.loads(result["body"])


def test_health_and_summary_routes(
    monkeypatch,
):
    runtime(monkeypatch)
    health = app.lambda_handler(
        http_event("GET", "/health"),
        None,
    )
    summary = app.lambda_handler(
        http_event(
            "POST",
            "/ai/summaries/network",
            "{}",
        ),
        None,
    )
    assert health["statusCode"] == 200
    assert payload(health)["requestId"] == "request-123"
    assert summary["statusCode"] == 200
    assert payload(summary)["advisoryOnly"] is True


def test_chat_validation(monkeypatch):
    runtime(monkeypatch)
    result = app.lambda_handler(
        http_event(
            "POST",
            "/ai/chat",
            json.dumps({"message": ""}),
        ),
        None,
    )
    assert result["statusCode"] == 400
    assert payload(result)["requestId"] == "request-123"


@pytest.mark.parametrize(
    "path",
    [
        "/ai/aircraft/a67928/explain",
        "/ai/airports/KSFO/summarize",
        "/ai/recommendations/rec%231/explain",
        "/ai/alerts/alert%231/incident-summary",
    ],
)
def test_fixed_subject_routes(monkeypatch, path):
    runtime(monkeypatch)
    result = app.lambda_handler(
        http_event("POST", path, "{}"),
        None,
    )
    assert result["statusCode"] == 200
    assert payload(result)["humanReviewRequired"] is True


def test_unknown_route(monkeypatch):
    runtime(monkeypatch)
    result = app.lambda_handler(
        http_event("GET", "/unknown"),
        None,
    )
    assert result["statusCode"] == 404


def test_verified_event_dispatch(monkeypatch):
    runtime(monkeypatch)
    result = app.lambda_handler(
        {
            "id": "event-1",
            "source": "wilvor.risk",
            "detail-type": "risk.updated",
            "detail": {"aircraft_id": "a67928"},
        },
        None,
    )
    assert result["advisoryOnly"] is True


def test_unsupported_event_is_ignored(monkeypatch):
    runtime(monkeypatch)
    result = app.lambda_handler(
        {
            "source": "wilvor.unknown",
            "detail-type": "unknown",
            "detail": {},
        },
        None,
    )
    assert result == {
        "processed": False,
        "reason": "UNSUPPORTED_EVENT",
    }
