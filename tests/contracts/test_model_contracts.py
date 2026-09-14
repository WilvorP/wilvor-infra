"""Phase 3A-preflight model decision and provider protocol tests."""

from __future__ import annotations

from dataclasses import fields

import pytest

from wilvor_ai import (
    ContractValidationError,
    ExactCountClaim,
    ModelDecision,
    ModelDecisionKind,
    ModelProvider,
    ModelTurnRequest,
    ProposedToolCall,
    UnsupportedReason,
    VerifiedZeroClaim,
)
from wilvor_ai.model_contracts import FORBIDDEN_DECISION_PROSE_KEYS


TOOL_CALL = "tool-call-model-001"


def assert_validation_error(expected_error: str, factory) -> None:
    with pytest.raises(ContractValidationError) as exc_info:
        factory()
    assert expected_error in exc_info.value.errors


def _tool_calls() -> tuple[ProposedToolCall, ...]:
    return (
        ProposedToolCall(
            name="summarize_historical_encounters",
            arguments={"start_utc": "2026-09-11T00:00:00Z"},
        ),
    )


def _claims() -> tuple[VerifiedZeroClaim, ...]:
    return (VerifiedZeroClaim(tool_call_id=TOOL_CALL),)


def test_proposed_tool_call_round_trip():
    call = ProposedToolCall(
        name="list_historical_encounters",
        arguments={"aircraft_id": "abc123", "limit": 20},
    )
    assert call.to_dict() == {
        "name": "list_historical_encounters",
        "arguments": {"aircraft_id": "abc123", "limit": 20},
    }
    assert ProposedToolCall.from_dict(call.to_dict()) == call
    assert_validation_error(
        "invalid_name",
        lambda: ProposedToolCall(name="list historical", arguments={}),
    )
    assert_validation_error(
        "invalid_arguments",
        lambda: ProposedToolCall(name="list_historical_encounters", arguments=["x"]),
    )


def test_model_decision_tool_calls_round_trip():
    decision = ModelDecision(
        kind=ModelDecisionKind.TOOL_CALLS,
        tool_calls=_tool_calls(),
    )
    payload = decision.to_dict()
    assert payload["kind"] == "TOOL_CALLS"
    assert "claims" not in payload
    assert "unsupported_reason" not in payload
    assert "answer" not in payload
    assert ModelDecision.from_dict(payload) == decision


def test_model_decision_final_claims_round_trip():
    decision = ModelDecision(
        kind=ModelDecisionKind.FINAL_CLAIMS,
        claims=(
            ExactCountClaim(
                tool_call_id=TOOL_CALL,
                metric_id="encounter_count",
                value=2,
            ),
        ),
    )
    payload = decision.to_dict()
    assert payload["kind"] == "FINAL_CLAIMS"
    assert "tool_calls" not in payload
    assert "answer" not in payload
    assert ModelDecision.from_dict(payload) == decision


def test_model_decision_unsupported_round_trip():
    decision = ModelDecision(
        kind=ModelDecisionKind.UNSUPPORTED,
        unsupported_reason=UnsupportedReason.CURRENT_STATE,
    )
    payload = decision.to_dict()
    assert payload == {
        "schema_version": decision.schema_version,
        "kind": "UNSUPPORTED",
        "unsupported_reason": "CURRENT_STATE",
    }
    assert ModelDecision.from_dict(payload) == decision


def test_model_decision_refusal_round_trip():
    bare = ModelDecision(kind=ModelDecisionKind.REFUSAL)
    assert "refusal_code" not in bare.to_dict()
    assert ModelDecision.from_dict(bare.to_dict()) == bare
    coded = ModelDecision(kind=ModelDecisionKind.REFUSAL, refusal_code="MODEL_REFUSED")
    assert ModelDecision.from_dict(coded.to_dict()) == coded


def test_model_decision_rejects_empty_and_mixed_kinds():
    assert_validation_error(
        "tool_calls_required",
        lambda: ModelDecision(kind=ModelDecisionKind.TOOL_CALLS),
    )
    assert_validation_error(
        "claims_required",
        lambda: ModelDecision(kind=ModelDecisionKind.FINAL_CLAIMS),
    )
    assert_validation_error(
        "unsupported_requires_reason",
        lambda: ModelDecision(kind=ModelDecisionKind.UNSUPPORTED),
    )
    assert_validation_error(
        "tool_calls_forbids_claims",
        lambda: ModelDecision(
            kind=ModelDecisionKind.TOOL_CALLS,
            tool_calls=_tool_calls(),
            claims=_claims(),
        ),
    )
    assert_validation_error(
        "tool_calls_forbids_unsupported_reason",
        lambda: ModelDecision(
            kind=ModelDecisionKind.TOOL_CALLS,
            tool_calls=_tool_calls(),
            unsupported_reason=UnsupportedReason.GEOGRAPHY,
        ),
    )
    assert_validation_error(
        "final_claims_forbids_unsupported_reason",
        lambda: ModelDecision(
            kind=ModelDecisionKind.FINAL_CLAIMS,
            claims=_claims(),
            unsupported_reason=UnsupportedReason.GEOGRAPHY,
        ),
    )
    assert_validation_error(
        "refusal_forbids_tool_calls",
        lambda: ModelDecision(
            kind=ModelDecisionKind.REFUSAL,
            tool_calls=_tool_calls(),
        ),
    )


def test_model_decision_from_dict_rejects_prose_and_unknown_kind():
    with pytest.raises(ContractValidationError) as prose:
        ModelDecision.from_dict(
            {
                "schema_version": "wilvor.ai.model_decision.v1",
                "kind": "UNSUPPORTED",
                "unsupported_reason": "GEOGRAPHY",
                "candidate_answer": "those aircraft were in California",
            }
        )
    assert "unexpected_decision_prose" in prose.value.errors
    with pytest.raises(ContractValidationError) as extra:
        ModelDecision.from_dict(
            {
                "schema_version": "wilvor.ai.model_decision.v1",
                "kind": "REFUSAL",
                "claims": [{"kind": "VERIFIED_ZERO", "tool_call_id": TOOL_CALL}],
            }
        )
    assert "unexpected_decision_field" in extra.value.errors
    with pytest.raises(ContractValidationError) as unknown:
        ModelDecision.from_dict(
            {
                "schema_version": "wilvor.ai.model_decision.v1",
                "kind": "ANSWER",
            }
        )
    assert "invalid_kind" in unknown.value.errors
    with pytest.raises(ContractValidationError) as empty:
        ModelDecision.from_dict({})
    assert "missing_kind" in empty.value.errors


def test_model_decision_has_no_factual_prose_fields():
    names = {item.name for item in fields(ModelDecision)}
    assert names.isdisjoint(
        {"answer", "candidate_answer", "answer_text", "factual_text"}
    )
    assert FORBIDDEN_DECISION_PROSE_KEYS == {
        "answer",
        "candidate_answer",
        "answer_text",
        "factual_text",
    }


def test_model_turn_request_keeps_preflight_shape():
    names = {item.name for item in fields(ModelTurnRequest)}
    assert names == {"user_text", "instruction_ref", "tools", "tool_results"}
    request = ModelTurnRequest(user_text="summarize encounters", instruction_ref="hist_v1")
    assert request.tools == ()
    assert request.tool_results == ()
    assert request.to_dict() == {
        "user_text": "summarize encounters",
        "instruction_ref": "hist_v1",
    }
    assert ModelTurnRequest.from_dict({"user_text": "summarize encounters"}) == (
        ModelTurnRequest(user_text="summarize encounters")
    )
    assert ModelTurnRequest.from_dict(request.to_dict()) == request
    assert_validation_error("invalid_user_text", lambda: ModelTurnRequest(user_text=" "))
    with pytest.raises(ContractValidationError) as exc_info:
        ModelTurnRequest.from_dict(
            {"user_text": "hello", "role": "user", "messages": []}
        )
    assert "unexpected_vendor_turn_field" in exc_info.value.errors


def test_model_provider_protocol_accepts_a_complete_callable():
    class ScriptedProvider:
        def complete(self, request: ModelTurnRequest) -> ModelDecision:
            assert request.user_text
            return ModelDecision(
                kind=ModelDecisionKind.UNSUPPORTED,
                unsupported_reason=UnsupportedReason.OUT_OF_CATALOG,
            )

    provider: ModelProvider = ScriptedProvider()
    decision = provider.complete(ModelTurnRequest(user_text="current hazards"))
    assert decision.kind is ModelDecisionKind.UNSUPPORTED
