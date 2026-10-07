"""Decision Expert instruction text. No model call."""

from __future__ import annotations

import pytest

from tests.fakes.fake_anthropic_messages import FakeAnthropicMessages
from wilvor_ai.decision_model_contracts import DecisionModelTurnRequest
from wilvor_ai.decision_specialist import DECISION_SPECIALIST_INSTRUCTION_REF
from wilvor_ai.decision_tools import decision_tool_schemas
from wilvor_ai.providers.anthropic_decision_messages import (
    AnthropicDecisionMessagesProvider,
)
from wilvor_ai.providers.decision_instructions import (
    DECISION_SPECIALIST_INSTRUCTION_REF as PROVIDER_INSTRUCTION_REF,
    DECISION_SPECIALIST_V1_INSTRUCTION,
    resolve_decision_instruction,
)
from wilvor_ai.providers.errors import ModelProviderError


_REQUIRED_PHRASES = (
    "only the four supplied Decision Tools",
    "DecisionEvidenceSnapshot",
    "evidence_ref",
    "de-1",
    "tool_call_id",
    "Do not fabricate tool results.",
    "Do not fabricate an evidence_ref.",
    "Never invent a different aircraft or recommendation identity.",
    "tables",
    "now_epoch",
    "query_timestamp_utc",
    "correlation_id",
    "Do not repeat a completed identical call.",
    "validation feedback",
    "You do not create the final factual answer.",
    "No stored risk is not LOW.",
    "No recommendation is not MONITOR.",
    "not proof that no safe airport exists",
    "NOT_FOUND",
    "UNKNOWN",
    "PERSISTED is not current airport suitability",
    "COMPLETE means persisted assessment scoring completed only.",
    "COMPLETE does not mean safe, current, selected, recommended, or cleared.",
    "Do not choose one encounter when multiple encounters exist.",
    "Do not choose one recommendation when multiple recommendations exist.",
    "Do not choose one airport when multiple persisted candidates exist.",
    "Lexical order is not ranking.",
    "EVALUATE_DIVERSION is advisory stored evidence only.",
    "not ATC clearance",
    "not a dispatch instruction",
    "not a selected diversion",
    "not a route",
    "not a flight plan",
    "not a landing instruction",
    "waypoints",
    "route safety",
    "one valid persisted recommendation id",
    "previous turn",
    "get_current_recommendation",
    "Do not issue multiple persisted triggers.",
    "Do not guess recommendation ids.",
    "capability/domain boundary",
    "LIVE_OPS",
    "HISTORICAL_ANALYTICS",
    "ROUTE_GENERATION_NOT_IMPLEMENTED",
    "SELECTED_DIVERSION_NOT_SUPPORTED",
    "ACTION_REQUEST",
    "INSUFFICIENT_EVIDENCE",
    "OUT_OF_CATALOG",
    "REFUSAL is reserved for genuine model/provider inability or refusal.",
    "outside Decision Expert capabilities",
    "DE3",
    "DE1",
    "DE2",
    "Prompt text is not the safety boundary.",
)


def test_instruction_ref_matches_the_de3_token():
    assert PROVIDER_INSTRUCTION_REF == "wilvor.decision.specialist.v1"
    assert PROVIDER_INSTRUCTION_REF == DECISION_SPECIALIST_INSTRUCTION_REF
    assert (
        resolve_decision_instruction(PROVIDER_INSTRUCTION_REF)
        == DECISION_SPECIALIST_V1_INSTRUCTION
    )


def test_instruction_states_the_closed_safety_rules():
    text = " ".join(DECISION_SPECIALIST_V1_INSTRUCTION.split())
    for phrase in _REQUIRED_PHRASES:
        assert phrase in text
    assert "catalog boundary" not in text.casefold()


def test_unknown_and_missing_refs_fail_closed_before_the_client():
    client = FakeAnthropicMessages(())
    provider = AnthropicDecisionMessagesProvider(client=client)
    tools = decision_tool_schemas()
    for instruction_ref in (None, "wilvor.decision.specialist.v9"):
        with pytest.raises(ModelProviderError, match="unknown_instruction_ref"):
            provider.complete(
                DecisionModelTurnRequest(
                    user_text="status",
                    instruction_ref=instruction_ref,
                    tools=tools,
                )
            )
    assert client.requests == []
