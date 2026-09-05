from copy import deepcopy
from types import SimpleNamespace

from insight_repository import (
    InsightRepository,
    canonical_fingerprint,
)
from model_client import ModelResponse
from service import AiCopilotService


class FakeTable:
    def __init__(self, items=None):
        self.items = items or []
        self.puts = []

    def query(self, **_):
        return {"Items": self.items[:1]}

    def put_item(self, **kwargs):
        self.puts.append(kwargs["Item"])


class FakeBuilders:
    def build_network_context(self):
        return {
            "contextVersion": "1.0",
            "generatedAt": "volatile",
            "subject": {
                "type": "NETWORK",
                "id": "CURRENT",
            },
            "overview": {
                "topRisks": [
                    {"risk_id": "r1", "risk_level": "HIGH"}
                ]
            },
            "limitations": ["Fuel unavailable."],
            "dataFreshnessWarnings": [],
            "evidenceCatalog": [
                {
                    "evidenceId": "risk.r1.level",
                    "label": "Risk level",
                }
            ],
        }


class FakeModel:
    model_id = "fake-model"

    def __init__(self):
        self.calls = 0

    def converse(self, *_args, **_kwargs):
        self.calls += 1
        return ModelResponse(
            message={
                "role": "assistant",
                "content": [],
            },
            text=(
                '{"answer":"High risk exists.",'
                '"evidence":[{"evidenceId":"risk.r1.level",'
                '"label":"Risk"}],"confidence":"HIGH",'
                '"limitations":[],"dataFreshnessWarnings":[]}'
            ),
            input_tokens=12,
            output_tokens=8,
            latency_ms=10,
        )


class FakeInsightRepository:
    def __init__(self):
        self.cached = None
        self.stored = []

    def get_cached(self, **_):
        return deepcopy(self.cached)

    def store(self, **kwargs):
        self.stored.append(kwargs)
        self.cached = deepcopy(kwargs["output"])


def settings():
    return SimpleNamespace(
        bedrock_model_id="fake-model",
        prompt_version="wilvor-ai-v1",
        cache_ttl_seconds=300,
        insight_retention_seconds=3600,
        max_context_bytes=131072,
    )


def test_fingerprint_changes_only_for_material_inputs():
    base = {"risk": {"risk_level": "HIGH"}}
    original = canonical_fingerprint(
        insight_type="AIRCRAFT_RISK_EXPLANATION",
        material_context=base,
        model_id="m1",
        prompt_version="p1",
    )
    assert original == canonical_fingerprint(
        insight_type="AIRCRAFT_RISK_EXPLANATION",
        material_context=deepcopy(base),
        model_id="m1",
        prompt_version="p1",
    )
    for changed in (
        {"risk": {"risk_level": "MEDIUM"}},
        {"recommendation": {"id": "new"}},
    ):
        assert original != canonical_fingerprint(
            insight_type="AIRCRAFT_RISK_EXPLANATION",
            material_context=changed,
            model_id="m1",
            prompt_version="p1",
        )
    assert original != canonical_fingerprint(
        insight_type="AIRCRAFT_RISK_EXPLANATION",
        material_context=base,
        model_id="m2",
        prompt_version="p1",
    )
    assert original != canonical_fingerprint(
        insight_type="AIRCRAFT_RISK_EXPLANATION",
        material_context=base,
        model_id="m1",
        prompt_version="p2",
    )


def test_repository_cache_requires_fingerprint_and_validity():
    table = FakeTable(
        [
            {
                "context_fingerprint": "same",
                "cache_valid_until_epoch": 200,
                "output": {"answer": "cached"},
            }
        ]
    )
    repository = InsightRepository(
        "unused",
        table=table,
    )
    assert repository.get_cached(
        subject_type="NETWORK",
        subject_id="CURRENT",
        insight_type="NETWORK_SUMMARY",
        fingerprint="same",
        now_epoch=100,
    ) == {"answer": "cached"}
    assert repository.get_cached(
        subject_type="NETWORK",
        subject_id="CURRENT",
        insight_type="NETWORK_SUMMARY",
        fingerprint="changed",
        now_epoch=100,
    ) is None


def test_fixed_workflow_is_cached_and_forces_safety():
    model = FakeModel()
    insights = FakeInsightRepository()
    service = AiCopilotService(
        settings=settings(),
        builders=FakeBuilders(),
        model_client=model,
        insight_repository=insights,
        agent=None,
    )

    first = service.network_summary().payload
    second = service.network_summary().payload

    assert model.calls == 1
    assert first["advisoryOnly"] is True
    assert first["humanReviewRequired"] is True
    assert first["limitations"] == [
        "Fuel unavailable."
    ]
    assert second["cache"]["hit"] is True
