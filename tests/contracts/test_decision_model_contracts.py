"""DE0 Decision model-turn and terminal-decision contract tests."""

from __future__ import annotations

import json

import pytest

from wilvor_ai.contracts import (
    ConfidenceLevel,
    ContractValidationError,
    FreshnessStatus,
    TemporalScope,
    ToolInputField,
    ToolInputValueType,
    ToolResultStatus,
)
from wilvor_ai.decision_claims import AircraftIdentityClaim, decision_claim_from_dict
from wilvor_ai.decision_model_contracts import (
    DECISION_MODEL_DECISION_SCHEMA_VERSION,
    DECISION_MODEL_TOOL_NAMES,
    DecisionEvidenceSnapshot,
    DecisionModelDecision,
    DecisionModelTurnRequest,
    DecisionUnsupportedReason,
)
from wilvor_ai.decision_tool_projection import (
    DecisionProjectedEvidence,
    DecisionToolProjection,
)
from wilvor_ai.decision_tools import DECISION_TOOLS
from wilvor_ai.model_contracts import (
    MODEL_DECISION_SCHEMA_VERSION,
    ModelDecisionKind,
    ProposedToolCall,
    ValidationFeedback,
    ValidationFeedbackCode,
)
from wilvor_ai.specialist_contracts import UnsupportedReason
from wilvor_ai.tool_schema import ToolSchema


def assert_validation_error(expected_error: str, factory) -> None:
    with pytest.raises(ContractValidationError) as exc_info:
        factory()
    assert expected_error in exc_info.value.errors


def _projection() -> DecisionToolProjection:
    return DecisionToolProjection(
        tool_name="get_current_decision_context",
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.CURRENT,
        as_of_utc="2026-09-30T00:00:00Z",
        data={"aircraft_id": "N123"},
        limitations=(),
        evidence=(
            DecisionProjectedEvidence(
                source="wilvor.decision.context",
                source_records=(),
                temporal_scope=TemporalScope.CURRENT,
                freshness_status=FreshnessStatus.UNKNOWN,
                confidence=ConfidenceLevel.UNKNOWN,
                limitations=(),
            ),
        ),
    )


def _snapshot(evidence_ref: str = "de-1") -> DecisionEvidenceSnapshot:
    return DecisionEvidenceSnapshot(evidence_ref=evidence_ref, projection=_projection())


def _field(name: str) -> ToolInputField:
    return ToolInputField(
        name=name,
        required=True,
        value_type=ToolInputValueType.STRING,
        description="Stored identity",
    )


def _schema(name: str, argument: str) -> ToolSchema:
    return ToolSchema(
        name=name,
        description="Closed decision tool",
        input_fields=(_field(argument),),
    )


def _catalog() -> tuple[ToolSchema, ...]:
    return (
        _schema("get_current_decision_context", "aircraft_id"),
        _schema("get_current_risk_evidence", "aircraft_id"),
        _schema("get_current_recommendation", "aircraft_id"),
        _schema("get_persisted_airport_candidate_evidence", "recommendation_id"),
    )


def _claim() -> AircraftIdentityClaim:
    parsed = decision_claim_from_dict(
        {
            "kind": "AIRCRAFT_IDENTITY",
            "evidence_ref": "de-1",
            "evidence_scope": "CURRENT",
            "aircraft_id": "N123",
        }
    )
    assert isinstance(parsed, AircraftIdentityClaim)
    return parsed


def test_unsupported_reason_is_the_closed_decision_set() -> None:
    assert {item.value for item in DecisionUnsupportedReason} == {
        "LIVE_OPS",
        "HISTORICAL_ANALYTICS",
        "ROUTE_GENERATION_NOT_IMPLEMENTED",
        "SELECTED_DIVERSION_NOT_SUPPORTED",
        "ACTION_REQUEST",
        "INSUFFICIENT_EVIDENCE",
        "OUT_OF_CATALOG",
    }


def test_advertised_tool_names_match_the_closed_catalog() -> None:
    assert DECISION_MODEL_TOOL_NAMES == tuple(item.name for item in DECISION_TOOLS)


def test_snapshot_round_trip_hides_audit_fields() -> None:
    snapshot = _snapshot("de-2")
    restored = DecisionEvidenceSnapshot.from_dict(snapshot.to_dict())
    assert restored == snapshot
    encoded = json.dumps(snapshot.to_dict())
    for banned in (
        "tool_call_id",
        "correlation_id",
        "tables",
        "now_epoch",
        "query_timestamp_utc",
    ):
        assert banned not in encoded
    assert set(snapshot.to_dict()) == {"evidence_ref", "projection"}
    assert isinstance(restored.projection, DecisionToolProjection)


def test_snapshot_rejects_tool_call_id_inside_projection() -> None:
    payload = _snapshot().to_dict()
    payload["projection"]["tool_call_id"] = "decision-call-1"
    assert_validation_error(
        "forbidden_decision_field",
        lambda: DecisionEvidenceSnapshot.from_dict(payload),
    )


def test_turn_request_round_trip_and_catalog_rules() -> None:
    request = DecisionModelTurnRequest(
        user_text="What is the current stored risk?",
        instruction_ref="wilvor.decision.specialist.v1",
        tools=_catalog(),
        evidence_snapshots=(_snapshot("de-1"), _snapshot("de-2")),
        validation_feedback=ValidationFeedback(
            code=ValidationFeedbackCode.UNKNOWN_TOOL,
            tool_name="search_current_hazards",
        ),
    )
    assert DecisionModelTurnRequest.from_dict(request.to_dict()) == request
    empty = DecisionModelTurnRequest(user_text="Risk for N123?")
    assert DecisionModelTurnRequest.from_dict(empty.to_dict()) == empty
    reversed_tools = tuple(reversed(_catalog()))
    DecisionModelTurnRequest(user_text="Risk for N123?", tools=reversed_tools)
    assert_validation_error(
        "invalid_decision_tools",
        lambda: DecisionModelTurnRequest(
            user_text="Risk for N123?",
            tools=_catalog()[:3],
        ),
    )
    assert_validation_error(
        "invalid_decision_tools",
        lambda: DecisionModelTurnRequest(
            user_text="Risk for N123?",
            tools=_catalog() + (_schema("search_current_hazards", "aircraft_id"),),
        ),
    )
    assert_validation_error(
        "duplicate_evidence_ref",
        lambda: DecisionModelTurnRequest(
            user_text="Risk for N123?",
            evidence_snapshots=(_snapshot("de-1"), _snapshot("de-1")),
        ),
    )


def test_turn_rejects_vendor_and_trusted_fields() -> None:
    base = {"user_text": "Risk for N123?"}
    for key in (
        "role",
        "messages",
        "system",
        "assistant",
        "tool_use_id",
        "content",
        "chat_completions",
    ):
        assert_validation_error(
            "unexpected_vendor_turn_field",
            lambda key=key: DecisionModelTurnRequest.from_dict({**base, key: "x"}),
        )
    for key in (
        "tables",
        "now_epoch",
        "query_timestamp_utc",
        "correlation_id",
        "tool_call_id",
        "as_of_utc",
        "operations",
        "sql",
    ):
        assert_validation_error(
            "trusted_field_forbidden",
            lambda key=key: DecisionModelTurnRequest.from_dict({**base, key: "x"}),
        )
    assert_validation_error(
        "unexpected_turn_field",
        lambda: DecisionModelTurnRequest.from_dict({**base, "note": "x"}),
    )
    assert_validation_error(
        "unexpected_decision_prose",
        lambda: DecisionModelTurnRequest.from_dict({**base, "answer": "low"}),
    )


def test_decision_kinds_are_exclusive() -> None:
    call = ProposedToolCall(
        name="get_current_risk_evidence",
        arguments={"aircraft_id": "N123"},
    )
    claims = (_claim(),)
    tool_decision = DecisionModelDecision(
        kind=ModelDecisionKind.TOOL_CALLS,
        tool_calls=(call,),
    )
    assert DecisionModelDecision.from_dict(tool_decision.to_dict()) == tool_decision
    final = DecisionModelDecision(
        kind=ModelDecisionKind.FINAL_CLAIMS,
        claims=claims,
    )
    assert DecisionModelDecision.from_dict(final.to_dict()) == final
    unsupported = DecisionModelDecision(
        kind=ModelDecisionKind.UNSUPPORTED,
        unsupported_reason=DecisionUnsupportedReason.LIVE_OPS,
    )
    assert DecisionModelDecision.from_dict(unsupported.to_dict()) == unsupported
    refusal = DecisionModelDecision(
        kind=ModelDecisionKind.REFUSAL,
        refusal_code="provider_refusal",
    )
    assert DecisionModelDecision.from_dict(refusal.to_dict()) == refusal
    bare_refusal = DecisionModelDecision(kind=ModelDecisionKind.REFUSAL)
    assert DecisionModelDecision.from_dict(bare_refusal.to_dict()) == bare_refusal
    unknown_tool = DecisionModelDecision(
        kind=ModelDecisionKind.TOOL_CALLS,
        tool_calls=(
            ProposedToolCall(name="search_current_hazards", arguments={}),
        ),
    )
    assert unknown_tool.tool_calls[0].name == "search_current_hazards"
    assert_validation_error(
        "tool_calls_forbids_claims",
        lambda: DecisionModelDecision(
            kind=ModelDecisionKind.TOOL_CALLS,
            tool_calls=(call,),
            claims=claims,
        ),
    )
    assert_validation_error(
        "claims_required",
        lambda: DecisionModelDecision(kind=ModelDecisionKind.FINAL_CLAIMS),
    )
    assert_validation_error(
        "final_claims_forbids_tool_calls",
        lambda: DecisionModelDecision(
            kind=ModelDecisionKind.FINAL_CLAIMS,
            claims=claims,
            tool_calls=(call,),
        ),
    )
    assert_validation_error(
        "unsupported_requires_reason",
        lambda: DecisionModelDecision(kind=ModelDecisionKind.UNSUPPORTED),
    )
    assert_validation_error(
        "unsupported_forbids_claims",
        lambda: DecisionModelDecision(
            kind=ModelDecisionKind.UNSUPPORTED,
            unsupported_reason=DecisionUnsupportedReason.OUT_OF_CATALOG,
            claims=claims,
        ),
    )
    assert_validation_error(
        "refusal_forbids_claims",
        lambda: DecisionModelDecision(
            kind=ModelDecisionKind.REFUSAL,
            claims=claims,
        ),
    )


def test_historical_claim_and_unsupported_reason_fail() -> None:
    assert_validation_error(
        "forbidden_decision_field",
        lambda: DecisionModelDecision.from_dict(
            {
                "schema_version": DECISION_MODEL_DECISION_SCHEMA_VERSION,
                "kind": "FINAL_CLAIMS",
                "claims": [
                    {
                        "kind": "EXACT_COUNT",
                        "tool_call_id": "historical-call-1",
                        "metric_id": "physical_record_count",
                        "value": 1,
                    }
                ],
            }
        ),
    )
    assert_validation_error(
        "invalid_kind",
        lambda: DecisionModelDecision.from_dict(
            {
                "schema_version": DECISION_MODEL_DECISION_SCHEMA_VERSION,
                "kind": "FINAL_CLAIMS",
                "claims": [
                    {
                        "kind": "EXACT_COUNT",
                        "metric_id": "physical_record_count",
                        "value": 1,
                    }
                ],
            }
        ),
    )
    assert_validation_error(
        "invalid_unsupported_reason",
        lambda: DecisionModelDecision.from_dict(
            {
                "schema_version": DECISION_MODEL_DECISION_SCHEMA_VERSION,
                "kind": "UNSUPPORTED",
                "unsupported_reason": UnsupportedReason.CURRENT_STATE.value,
            }
        ),
    )


def test_schema_version_and_unknown_decision_fields() -> None:
    assert DECISION_MODEL_DECISION_SCHEMA_VERSION == "wilvor.ai.decision_model_decision.v1"
    assert DECISION_MODEL_DECISION_SCHEMA_VERSION != MODEL_DECISION_SCHEMA_VERSION
    assert_validation_error(
        "invalid_schema_version",
        lambda: DecisionModelDecision.from_dict(
            {
                "schema_version": MODEL_DECISION_SCHEMA_VERSION,
                "kind": "REFUSAL",
            }
        ),
    )
    assert_validation_error(
        "unexpected_decision_field",
        lambda: DecisionModelDecision.from_dict(
            {
                "schema_version": DECISION_MODEL_DECISION_SCHEMA_VERSION,
                "kind": "REFUSAL",
                "note": "extra",
            }
        ),
    )
    assert_validation_error(
        "unexpected_decision_prose",
        lambda: DecisionModelDecision.from_dict(
            {
                "schema_version": DECISION_MODEL_DECISION_SCHEMA_VERSION,
                "kind": "REFUSAL",
                "answer": "Stored risk is low.",
            }
        ),
    )
    assert_validation_error(
        "unexpected_decision_field",
        lambda: DecisionModelDecision.from_dict(
            {
                "schema_version": DECISION_MODEL_DECISION_SCHEMA_VERSION,
                "kind": "TOOL_CALLS",
                "tool_calls": [
                    {
                        "name": "get_current_risk_evidence",
                        "arguments": {"aircraft_id": "N123"},
                    }
                ],
                "claims": [],
            }
        ),
    )
