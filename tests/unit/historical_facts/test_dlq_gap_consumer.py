"""Domain-2 DLQ consumer copies to S3 before the message can be deleted."""

from __future__ import annotations

from types import ModuleType
from typing import Any, Callable

import pytest


class FakeS3:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.puts: list[dict[str, Any]] = []

    def put_object(self, **kwargs):
        if self.fail:
            raise RuntimeError("s3 down")
        self.puts.append(kwargs)
        return {}


@pytest.fixture
def consumer(
    load_repo_module: Callable[[str, str], ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> ModuleType:
    monkeypatch.setenv("HISTORICAL_FACTS_BUCKET_NAME", "test-historical")
    return load_repo_module(
        "historical_facts_dlq_gap_consumer_app",
        "functions/historical_facts/dlq_gap_consumer/app.py",
    )


def test_successful_write_returns_written_count(consumer, monkeypatch):
    monkeypatch.setattr(
        consumer,
        "write_unbound_incident_fail_open",
        lambda incident, **kwargs: (
            "metadata/incidents/year=2026/month=07/day=18/domain2.json"
        ),
    )
    result = consumer.lambda_handler(
        {
            "Records": [
                {"messageId": "m1", "body": '{"detail-type":"encounter.updated"}'},
            ]
        }
    )
    assert result["written"] == 1


def test_failed_write_leaves_message(consumer, monkeypatch):
    monkeypatch.setattr(
        consumer,
        "write_unbound_incident_fail_open",
        lambda incident, **kwargs: None,
    )
    with pytest.raises(RuntimeError, match="leaving SQS message"):
        consumer.lambda_handler({"Records": [{"messageId": "m1", "body": "{}"}]})


def test_incident_is_domain_2_staging_without_epoch(consumer):
    incident = consumer.incident_from_dlq_record(
        {"messageId": "m-22", "body": "raw-event"},
        now_utc="2026-07-18T12:00:00Z",
    )
    payload = incident.to_dict()
    assert payload["record_type"] == "UNBOUND_COLLECTION_INCIDENT"
    assert payload["staging_state"] == "STAGING"
    assert "collection_epoch_id" not in payload
    assert payload["gap_domain"] == "DOMAIN_2"
    assert payload["uncertainty_class"] == "KNOWN_MISSING"
    assert payload["reason"] == "EVENTBRIDGE_TARGET_FAILURE"
    assert "m-22" in payload["evidence_refs"]
    assert payload["affected_datasets"] == [
        "encounter",
        "risk",
        "hazard_version",
    ]
