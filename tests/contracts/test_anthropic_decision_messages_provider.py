"""Decision Anthropic adapter. Fake client only. No network."""

from __future__ import annotations

import json

import pytest

from tests.fakes.fake_anthropic_messages import (
    FakeAnthropicMessages,
    end_turn_response,
    stop_reason_response,
    tool_use_response,
)
from wilvor_ai.contracts import (
    ConfidenceLevel,
    Evidence,
    FreshnessStatus,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.decision_claims import (
    AircraftIdentityClaim,
    CapabilityGapClaim,
    ChainGapClaim,
    CurrentPersistedLinkClaim,
    DecisionClaimKind,
    EncounterSetClaim,
    EvaluationStateClaim,
    LimitationClaim,
    PersistedAssessmentStatus,
    PersistedCandidateClaim,
    PersistedCandidateCollectionReason,
    PersistedCandidateStatusClaim,
    PersistedCapabilityEvidenceStatus,
    PersistedEvaluationClaim,
    RecommendationActionClaim,
    RecommendationSetClaim,
    RiskAbsentClaim,
    RiskPresentClaim,
    ToolStatusClaim,
)
from wilvor_ai.decision_contracts import (
    DecisionAdvisoryAuthority,
    DecisionCapabilityGap,
    DecisionChainGap,
    DecisionEvaluationState,
    DecisionEvidence,
    DecisionEvidenceKind,
    DecisionLimitationCode,
    DecisionReportedLinkState,
    DecisionRiskEvidence,
    PersistedEvaluationScope,
    RecommendationActionType,
    RiskPresence,
    StoredRiskLevel,
)
from wilvor_ai.decision_model_contracts import (
    DECISION_MODEL_DECISION_SCHEMA_VERSION,
    DECISION_MODEL_TOOL_NAMES,
    DecisionEvidenceSnapshot,
    DecisionModelTurnRequest,
    DecisionUnsupportedReason,
)
from wilvor_ai.decision_specialist import DecisionSpecialist
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionRuntimeErrorCode,
    DecisionSpecialistRequest,
    DecisionSpecialistRunStatus,
    DecisionTargetMode,
)
from wilvor_ai.decision_tool_projection import project_decision_tool_result
from wilvor_ai.decision_tools import (
    DecisionToolInvocation,
    DecisionToolsRuntime,
    decision_tool_schemas,
)
from wilvor_ai.model_contracts import (
    ModelDecisionKind,
    ValidationFeedback,
    ValidationFeedbackCode,
)
from wilvor_ai.providers import anthropic_messages as historical_provider
from wilvor_ai.providers.anthropic_decision_messages import (
    APPROVED_MODEL_IDS,
    DEFAULT_MODEL_ID,
    MAX_TOKENS,
    REFUSAL_PROVIDER,
    TEMPERATURE,
    AnthropicDecisionMessagesProvider,
    build_decision_messages_kwargs,
    decision_terminal_json_schema,
    parse_decision_messages_response,
)
from wilvor_ai.providers.decision_instructions import (
    DECISION_SPECIALIST_INSTRUCTION_REF,
    DECISION_SPECIALIST_V1_INSTRUCTION,
)
from wilvor_ai.providers.errors import (
    ModelProviderContextLengthError,
    ModelProviderMalformedDecisionError,
)
from wilvor_ai.specialist_contracts import VerifierOutcome
from wilvor_ai.decision_answer_renderer import DecisionRenderOutcome


AS_OF = "2026-09-29T00:00:00Z"
AUDIT_CALL_ID = "audit-call-secret-99"
_FORBIDDEN_SCHEMA_KEYS = frozenset(
    {
        "minimum",
        "maximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "oneOf",
    }
)
_FORBIDDEN_PROPERTY_NAMES = frozenset(
    {
        "tool_call_id",
        "correlation_id",
        "rank",
        "score",
        "scores",
        "distance",
        "eta",
        "best",
        "preferred",
        "winner",
        "safest",
        "selected_airport",
        "selected_encounter",
        "route",
        "waypoint",
        "flight_plan",
        "answer",
        "factual_text",
        "candidate_answer",
    }
)
_CLAIM_FIELDS = {
    DecisionClaimKind.AIRCRAFT_IDENTITY: frozenset(
        {"kind", "evidence_ref", "evidence_scope", "aircraft_id"}
    ),
    DecisionClaimKind.EVALUATION_STATE: frozenset(
        {"kind", "evidence_ref", "evidence_scope", "evaluation_state"}
    ),
    DecisionClaimKind.ENCOUNTER_SET: frozenset(
        {"kind", "evidence_ref", "evidence_scope", "encounter_ids"}
    ),
    DecisionClaimKind.RISK_ABSENT: frozenset(
        {"kind", "evidence_ref", "evidence_scope", "encounter_id"}
    ),
    DecisionClaimKind.RISK_PRESENT: frozenset(
        {
            "kind",
            "evidence_ref",
            "evidence_scope",
            "encounter_id",
            "risk_id",
            "risk_level",
            "risk_score",
        }
    ),
    DecisionClaimKind.RECOMMENDATION_SET: frozenset(
        {"kind", "evidence_ref", "evidence_scope", "recommendation_ids", "absence_state"}
    ),
    DecisionClaimKind.RECOMMENDATION_ACTION: frozenset(
        {
            "kind",
            "evidence_ref",
            "evidence_scope",
            "recommendation_id",
            "primary_action_type",
            "advisory_authority",
        }
    ),
    DecisionClaimKind.CHAIN_GAP: frozenset(
        {"kind", "evidence_ref", "evidence_scope", "chain_gap"}
    ),
    DecisionClaimKind.CAPABILITY_GAP: frozenset(
        {"kind", "evidence_ref", "evidence_scope", "capability_gap"}
    ),
    DecisionClaimKind.PERSISTED_EVALUATION: frozenset(
        {
            "kind",
            "evidence_ref",
            "evidence_scope",
            "recommendation_id",
            "airport_evaluation_id",
            "evaluation_scope",
        }
    ),
    DecisionClaimKind.PERSISTED_CANDIDATE: frozenset(
        {
            "kind",
            "evidence_ref",
            "evidence_scope",
            "recommendation_id",
            "airport_id",
            "airport_assessment_id",
            "assessment_status",
            "route_safety_status",
            "runway_evidence_status",
            "congestion_evidence_status",
        }
    ),
    DecisionClaimKind.PERSISTED_CANDIDATE_STATUS: frozenset(
        {
            "kind",
            "evidence_ref",
            "evidence_scope",
            "recommendation_id",
            "candidate_count",
            "collection_reason",
        }
    ),
    DecisionClaimKind.TOOL_STATUS: frozenset(
        {"kind", "evidence_ref", "evidence_scope", "status"}
    ),
    DecisionClaimKind.LIMITATION: frozenset(
        {"kind", "evidence_ref", "evidence_scope", "limitation_code"}
    ),
    DecisionClaimKind.CURRENT_PERSISTED_LINK: frozenset(
        {
            "kind",
            "current_evidence_ref",
            "persisted_evidence_ref",
            "recommendation_id",
        }
    ),
}
_NULLABLE_FIELDS = {
    DecisionClaimKind.RISK_ABSENT: "encounter_id",
    DecisionClaimKind.RISK_PRESENT: "encounter_id",
    DecisionClaimKind.PERSISTED_EVALUATION: "airport_evaluation_id",
    DecisionClaimKind.PERSISTED_CANDIDATE_STATUS: "collection_reason",
}


def _turn(**overrides) -> DecisionModelTurnRequest:
    values = {
        "user_text": "status",
        "instruction_ref": DECISION_SPECIALIST_INSTRUCTION_REF,
        "tools": decision_tool_schemas(),
    }
    values.update(overrides)
    return DecisionModelTurnRequest(**values)


def _packet(tool_call_id: str) -> Evidence:
    return Evidence(
        source="wilvor.decision.provider.test",
        source_records=(
            SourceRecord(
                record_id="ac-1",
                source_version=None,
                event_timestamp_utc=None,
            ),
        ),
        query_timestamp_utc=AS_OF,
        freshness_status=FreshnessStatus.FRESH,
        confidence=ConfidenceLevel.UNKNOWN,
        limitations=(),
        tool_call_id=tool_call_id,
        temporal_scope=TemporalScope.CURRENT,
    )


def _risk_payload() -> dict:
    evidence = DecisionEvidence(
        kind=DecisionEvidenceKind.RISK_EVIDENCE,
        evaluation_state=DecisionEvaluationState.ESTABLISHED,
        aircraft_in_current_set=True,
        aircraft_id="ac-1",
        projection_state=DecisionReportedLinkState.PRESENT,
        projection_id="proj-1",
        risk=DecisionRiskEvidence(
            presence=RiskPresence.PRESENT,
            risk_id="risk-1",
            risk_level=StoredRiskLevel.HIGH,
            risk_score=80,
        ),
        chain_gaps=(),
        limitation_codes=(),
    )
    return evidence.to_dict()


def _raw(tool_call_id: str) -> ToolResult:
    return ToolResult(
        tool_name="get_current_risk_evidence",
        tool_call_id=tool_call_id,
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.CURRENT,
        data=_risk_payload(),
        evidence=(_packet(tool_call_id),),
        as_of_utc=AS_OF,
        limitations=(),
    )


def _snapshot(ref: str = "de-1") -> DecisionEvidenceSnapshot:
    return DecisionEvidenceSnapshot(ref, project_decision_tool_result(_raw(AUDIT_CALL_ID)))


def _final(claim: dict) -> dict:
    return {
        "schema_version": DECISION_MODEL_DECISION_SCHEMA_VERSION,
        "kind": ModelDecisionKind.FINAL_CLAIMS.value,
        "claims": [claim],
    }


def _claim_examples() -> dict[DecisionClaimKind, tuple[type, dict]]:
    current = TemporalScope.CURRENT.value
    persisted = TemporalScope.PERSISTED.value
    return {
        DecisionClaimKind.AIRCRAFT_IDENTITY: (
            AircraftIdentityClaim,
            {
                "kind": DecisionClaimKind.AIRCRAFT_IDENTITY.value,
                "evidence_ref": "de-1",
                "evidence_scope": current,
                "aircraft_id": "ac-1",
            },
        ),
        DecisionClaimKind.EVALUATION_STATE: (
            EvaluationStateClaim,
            {
                "kind": DecisionClaimKind.EVALUATION_STATE.value,
                "evidence_ref": "de-1",
                "evidence_scope": current,
                "evaluation_state": DecisionEvaluationState.ESTABLISHED.value,
            },
        ),
        DecisionClaimKind.ENCOUNTER_SET: (
            EncounterSetClaim,
            {
                "kind": DecisionClaimKind.ENCOUNTER_SET.value,
                "evidence_ref": "de-1",
                "evidence_scope": current,
                "encounter_ids": ["enc-1"],
            },
        ),
        DecisionClaimKind.RISK_ABSENT: (
            RiskAbsentClaim,
            {
                "kind": DecisionClaimKind.RISK_ABSENT.value,
                "evidence_ref": "de-1",
                "evidence_scope": current,
                "encounter_id": None,
            },
        ),
        DecisionClaimKind.RISK_PRESENT: (
            RiskPresentClaim,
            {
                "kind": DecisionClaimKind.RISK_PRESENT.value,
                "evidence_ref": "de-1",
                "evidence_scope": current,
                "encounter_id": None,
                "risk_id": "risk-1",
                "risk_level": StoredRiskLevel.HIGH.value,
                "risk_score": 80,
            },
        ),
        DecisionClaimKind.RECOMMENDATION_SET: (
            RecommendationSetClaim,
            {
                "kind": DecisionClaimKind.RECOMMENDATION_SET.value,
                "evidence_ref": "de-1",
                "evidence_scope": current,
                "recommendation_ids": ["rec-1"],
                "absence_state": DecisionReportedLinkState.PRESENT.value,
            },
        ),
        DecisionClaimKind.RECOMMENDATION_ACTION: (
            RecommendationActionClaim,
            {
                "kind": DecisionClaimKind.RECOMMENDATION_ACTION.value,
                "evidence_ref": "de-1",
                "evidence_scope": current,
                "recommendation_id": "rec-1",
                "primary_action_type": RecommendationActionType.MONITOR.value,
                "advisory_authority": DecisionAdvisoryAuthority.ADVISORY_ONLY.value,
            },
        ),
        DecisionClaimKind.CHAIN_GAP: (
            ChainGapClaim,
            {
                "kind": DecisionClaimKind.CHAIN_GAP.value,
                "evidence_ref": "de-1",
                "evidence_scope": current,
                "chain_gap": DecisionChainGap.AIRCRAFT_NOT_IN_CURRENT_SET.value,
            },
        ),
        DecisionClaimKind.CAPABILITY_GAP: (
            CapabilityGapClaim,
            {
                "kind": DecisionClaimKind.CAPABILITY_GAP.value,
                "evidence_ref": "de-1",
                "evidence_scope": current,
                "capability_gap": (
                    DecisionCapabilityGap.ROUTE_ALTERNATIVE_NOT_IMPLEMENTED.value
                ),
            },
        ),
        DecisionClaimKind.PERSISTED_EVALUATION: (
            PersistedEvaluationClaim,
            {
                "kind": DecisionClaimKind.PERSISTED_EVALUATION.value,
                "evidence_ref": "de-1",
                "evidence_scope": persisted,
                "recommendation_id": "rec-1",
                "airport_evaluation_id": None,
                "evaluation_scope": (
                    PersistedEvaluationScope.PERSISTED_EVALUATION_EVIDENCE.value
                ),
            },
        ),
        DecisionClaimKind.PERSISTED_CANDIDATE: (
            PersistedCandidateClaim,
            {
                "kind": DecisionClaimKind.PERSISTED_CANDIDATE.value,
                "evidence_ref": "de-1",
                "evidence_scope": persisted,
                "recommendation_id": "rec-1",
                "airport_id": "KSEA",
                "airport_assessment_id": "assess-1",
                "assessment_status": PersistedAssessmentStatus.COMPLETE.value,
                "route_safety_status": PersistedCapabilityEvidenceStatus.UNAVAILABLE.value,
                "runway_evidence_status": (
                    PersistedCapabilityEvidenceStatus.UNAVAILABLE.value
                ),
                "congestion_evidence_status": (
                    PersistedCapabilityEvidenceStatus.UNAVAILABLE.value
                ),
            },
        ),
        DecisionClaimKind.PERSISTED_CANDIDATE_STATUS: (
            PersistedCandidateStatusClaim,
            {
                "kind": DecisionClaimKind.PERSISTED_CANDIDATE_STATUS.value,
                "evidence_ref": "de-1",
                "evidence_scope": persisted,
                "recommendation_id": "rec-1",
                "candidate_count": 1,
                "collection_reason": None,
            },
        ),
        DecisionClaimKind.TOOL_STATUS: (
            ToolStatusClaim,
            {
                "kind": DecisionClaimKind.TOOL_STATUS.value,
                "evidence_ref": "de-1",
                "evidence_scope": current,
                "status": ToolResultStatus.SUCCESS.value,
            },
        ),
        DecisionClaimKind.LIMITATION: (
            LimitationClaim,
            {
                "kind": DecisionClaimKind.LIMITATION.value,
                "evidence_ref": "de-1",
                "evidence_scope": current,
                "limitation_code": DecisionLimitationCode.NO_SNAPSHOT_LIMITATION.value,
            },
        ),
        DecisionClaimKind.CURRENT_PERSISTED_LINK: (
            CurrentPersistedLinkClaim,
            {
                "kind": DecisionClaimKind.CURRENT_PERSISTED_LINK.value,
                "current_evidence_ref": "de-1",
                "persisted_evidence_ref": "de-2",
                "recommendation_id": "rec-1",
            },
        ),
    }


def _claim_branches() -> dict[str, dict]:
    schema = decision_terminal_json_schema()
    branches = schema["anyOf"][0]["properties"]["claims"]["items"]["anyOf"]
    return {branch["properties"]["kind"]["const"]: branch for branch in branches}


def _walk(node, visit) -> None:
    if isinstance(node, dict):
        visit(node)
        for value in node.values():
            _walk(value, visit)
    elif isinstance(node, list):
        for item in node:
            _walk(item, visit)


def _envelope(**overrides) -> dict:
    payload = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": DEFAULT_MODEL_ID,
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "{}"}],
    }
    payload.update(overrides)
    return payload


def test_model_settings_match_the_approved_copy():
    assert DEFAULT_MODEL_ID == "claude-sonnet-4-6"
    assert APPROVED_MODEL_IDS == frozenset({"claude-sonnet-4-6"})
    assert MAX_TOKENS == 2048
    assert TEMPERATURE == 0
    assert REFUSAL_PROVIDER == "refusal"
    assert DEFAULT_MODEL_ID == historical_provider.DEFAULT_MODEL_ID
    assert APPROVED_MODEL_IDS == historical_provider.APPROVED_MODEL_IDS
    assert MAX_TOKENS == historical_provider.MAX_TOKENS
    assert TEMPERATURE == historical_provider.TEMPERATURE
    assert REFUSAL_PROVIDER == historical_provider.REFUSAL_PROVIDER
    with pytest.raises(ValueError, match="unknown_model_id"):
        AnthropicDecisionMessagesProvider(
            client=FakeAnthropicMessages(()),
            model_id="claude-other",
        )


def test_request_uses_four_strict_decision_tools_and_terminal_schema():
    kwargs = build_decision_messages_kwargs(_turn(), DEFAULT_MODEL_ID)
    assert kwargs["model"] == DEFAULT_MODEL_ID
    assert kwargs["max_tokens"] == MAX_TOKENS
    assert kwargs["temperature"] == TEMPERATURE
    assert kwargs["system"] == DECISION_SPECIALIST_V1_INSTRUCTION
    assert kwargs["tool_choice"] == {"type": "auto"}
    assert "top_p" not in kwargs
    assert "stop_sequences" not in kwargs
    assert "thinking" not in kwargs
    tools = kwargs["tools"]
    assert [item["name"] for item in tools] == list(DECISION_MODEL_TOOL_NAMES)
    for tool in tools:
        assert tool["strict"] is True
        schema = tool["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert set(schema["properties"]) == set(schema["required"])
        assert set(schema["properties"]) <= {"aircraft_id", "recommendation_id"}
        assert set(schema["properties"]).isdisjoint(
            {
                "tables",
                "now_epoch",
                "query_timestamp_utc",
                "correlation_id",
                "tool_call_id",
            }
        )
    assert kwargs["output_config"] == {
        "format": {
            "type": "json_schema",
            "schema": decision_terminal_json_schema(),
        }
    }


def test_snapshots_keep_evidence_ref_and_drop_audit_fields():
    feedback = ValidationFeedback(
        code=ValidationFeedbackCode.UNKNOWN_ARGUMENT,
        tool_name="get_current_risk_evidence",
        argument_name="aircraft_id",
    )
    kwargs = build_decision_messages_kwargs(
        _turn(evidence_snapshots=(_snapshot(),), validation_feedback=feedback),
        DEFAULT_MODEL_ID,
    )
    encoded = json.dumps(kwargs)
    messages = kwargs["messages"]
    assert len(messages) == 3
    snapshot_text = messages[1]["content"][0]["text"]
    feedback_text = messages[2]["content"][0]["text"]
    assert ", " not in snapshot_text
    assert ": " not in snapshot_text
    loaded = json.loads(snapshot_text)
    assert loaded["evidence_snapshots"][0]["evidence_ref"] == "de-1"
    assert "tool_call_id" not in snapshot_text
    assert AUDIT_CALL_ID not in encoded
    assert "request_id" not in encoded
    assert "req-should-not-appear" not in encoded
    assert "wilvor.ai.tool_result" not in encoded
    assert "validation_feedback" in feedback_text
    assert "UNKNOWN_ARGUMENT" in feedback_text


def test_terminal_schema_is_the_three_decision_kinds_and_fifteen_claims():
    schema = decision_terminal_json_schema()
    kinds = [branch["properties"]["kind"]["const"] for branch in schema["anyOf"]]
    assert kinds == [
        ModelDecisionKind.FINAL_CLAIMS.value,
        ModelDecisionKind.UNSUPPORTED.value,
        ModelDecisionKind.REFUSAL.value,
    ]
    assert ModelDecisionKind.TOOL_CALLS.value not in json.dumps(schema)
    branches = _claim_branches()
    assert set(branches) == {item.value for item in DecisionClaimKind}
    assert len(branches) == 15
    for kind, fields in _CLAIM_FIELDS.items():
        branch = branches[kind.value]
        assert set(branch["properties"]) == fields
        assert set(branch["required"]) == fields
        assert branch["additionalProperties"] is False
    evaluation = branches[DecisionClaimKind.EVALUATION_STATE.value]
    assert "aircraft_id" not in evaluation["properties"]
    link = branches[DecisionClaimKind.CURRENT_PERSISTED_LINK.value]
    assert "evidence_ref" not in link["properties"]
    assert "evidence_scope" not in link["properties"]
    unsupported = schema["anyOf"][1]["properties"]["unsupported_reason"]["enum"]
    assert set(unsupported) == {item.value for item in DecisionUnsupportedReason}
    refusal = schema["anyOf"][2]
    assert refusal["required"] == ["schema_version", "kind"]
    assert "refusal_code" not in refusal["required"]
    schema_keys: set[str] = set()
    property_names: set[str] = set()
    object_count = 0
    union_count = 0

    def visit(node: dict) -> None:
        nonlocal object_count, union_count
        schema_keys.update(node)
        if node.get("type") == "object":
            object_count += 1
            assert node.get("additionalProperties") is False
            names = set(node.get("properties", {}))
            property_names.update(names)
            union_count += sum(
                1
                for name, value in node["properties"].items()
                if isinstance(value, dict) and "anyOf" in value
            )
        if "enum" in node:
            assert "HYBRID" not in node["enum"]
            assert "HISTORICAL" not in node["enum"]

    _walk(schema, visit)
    assert object_count > 0
    assert schema_keys.isdisjoint(_FORBIDDEN_SCHEMA_KEYS)
    assert property_names.isdisjoint(_FORBIDDEN_PROPERTY_NAMES)
    assert union_count == 4


@pytest.mark.parametrize("kind", list(DecisionClaimKind))
def test_each_claim_kind_parses_to_its_typed_claim(kind: DecisionClaimKind):
    claim_type, payload = _claim_examples()[kind]
    decision = parse_decision_messages_response(
        end_turn_response(_final(payload)),
        DEFAULT_MODEL_ID,
    )
    assert decision.kind is ModelDecisionKind.FINAL_CLAIMS
    assert isinstance(decision.claims[0], claim_type)
    assert decision.claims[0].to_dict()["kind"] == kind.value


def test_nullable_claim_fields_stay_required():
    branches = _claim_branches()
    for kind, field_name in _NULLABLE_FIELDS.items():
        branch = branches[kind.value]
        assert field_name in branch["required"]
        options = branch["properties"][field_name]["anyOf"]
        assert {"type": "null"} in options
        assert len(options) == 2


def test_native_tool_use_preserves_order_and_discards_vendor_ids():
    response = tool_use_response(
        ("get_current_decision_context", {"aircraft_id": "ac-1"}),
        ("get_current_risk_evidence", {"aircraft_id": "ac-1"}),
        ("get_current_recommendation", {"aircraft_id": "ac-1"}),
        (
            "get_persisted_airport_candidate_evidence",
            {"recommendation_id": "rec-1"},
        ),
        extra_text="incidental prose must not become an answer",
    )
    decision = parse_decision_messages_response(response, DEFAULT_MODEL_ID)
    assert decision.kind is ModelDecisionKind.TOOL_CALLS
    assert [call.name for call in decision.tool_calls] == list(DECISION_MODEL_TOOL_NAMES)
    encoded = json.dumps(decision.to_dict())
    assert "toolu_vendor_" not in encoded
    assert "incidental prose" not in encoded
    unknown = parse_decision_messages_response(
        tool_use_response(("not_a_decision_tool", {"aircraft_id": "ac-1"})),
        DEFAULT_MODEL_ID,
    )
    assert unknown.tool_calls[0].name == "not_a_decision_tool"


def test_terminal_json_mixed_with_tool_use_is_contradictory():
    response = tool_use_response(
        ("get_current_risk_evidence", {"aircraft_id": "ac-1"}),
        terminal_json=_final(_claim_examples()[DecisionClaimKind.AIRCRAFT_IDENTITY][1]),
    )
    with pytest.raises(
        ModelProviderMalformedDecisionError,
        match="contradictory_terminal_decision",
    ):
        parse_decision_messages_response(response, DEFAULT_MODEL_ID)


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (_envelope(type="other"), "unexpected_response_type"),
        (_envelope(role="user"), "unexpected_response_role"),
        (_envelope(model="claude-other"), "unexpected_response_model"),
        (_envelope(id=""), "malformed_message_id"),
        (_envelope(content="text"), "malformed_content"),
        (
            _envelope(content=[{"type": "image", "text": "x"}]),
            "unexpected_content_block",
        ),
        (
            _envelope(stop_reason="tool_use", content=[]),
            "tool_use_without_tool_use_blocks",
        ),
        (
            _envelope(
                content=[
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "get_current_risk_evidence",
                        "input": {"aircraft_id": "ac-1"},
                    }
                ]
            ),
            "end_turn_contains_tool_use",
        ),
        (_envelope(content=[{"type": "text", "text": ""}]), "malformed_terminal_json"),
        (
            _envelope(
                content=[
                    {"type": "text", "text": "{}"},
                    {"type": "text", "text": "{}"},
                ]
            ),
            "malformed_terminal_json",
        ),
        (
            _envelope(content=[{"type": "text", "text": "```json\n{}\n```"}]),
            "malformed_terminal_json",
        ),
        (
            _envelope(content=[{"type": "text", "text": "not json"}]),
            "malformed_terminal_json",
        ),
        (
            end_turn_response(
                {
                    "schema_version": "wilvor.ai.model_decision.v1",
                    "kind": "FINAL_CLAIMS",
                    "claims": [
                        {
                            "kind": "EXACT_COUNT",
                            "tool_call_id": "call-1",
                            "metric_id": "n",
                            "value": 1,
                        }
                    ],
                }
            ),
            "invalid_model_decision",
        ),
        (
            end_turn_response(
                _final(
                    {
                        "kind": "AIRCRAFT_IDENTITY",
                        "evidence_ref": "de-1",
                        "evidence_scope": "CURRENT",
                        "aircraft_id": "ac-1",
                        "tool_call_id": "call-1",
                    }
                )
            ),
            "invalid_model_decision",
        ),
        (
            end_turn_response(
                {
                    "schema_version": "wilvor.ai.decision_model_decision.v0",
                    "kind": "UNSUPPORTED",
                    "unsupported_reason": "OUT_OF_CATALOG",
                }
            ),
            "invalid_model_decision",
        ),
        (
            end_turn_response(
                _final(
                    {
                        "kind": "EVALUATION_STATE",
                        "evidence_ref": "de-1",
                        "evidence_scope": "CURRENT",
                        "evaluation_state": "NOPE",
                    }
                )
            ),
            "invalid_model_decision",
        ),
        (
            end_turn_response(
                {
                    "schema_version": DECISION_MODEL_DECISION_SCHEMA_VERSION,
                    "kind": "TOOL_CALLS",
                    "tool_calls": [
                        {
                            "name": "get_current_risk_evidence",
                            "arguments": {"aircraft_id": "ac-1"},
                        }
                    ],
                }
            ),
            "invalid_model_decision",
        ),
    ],
)
def test_malformed_outputs_fail_closed(payload, code):
    with pytest.raises(ModelProviderMalformedDecisionError, match=code):
        parse_decision_messages_response(payload, DEFAULT_MODEL_ID)


@pytest.mark.parametrize(
    ("stop_reason", "error_type", "code"),
    [
        ("max_tokens", ModelProviderMalformedDecisionError, "max_tokens"),
        (
            "stop_sequence",
            ModelProviderMalformedDecisionError,
            "stop_sequence_not_configured",
        ),
        ("pause_turn", ModelProviderMalformedDecisionError, "pause_turn"),
        ("other", ModelProviderMalformedDecisionError, "unexpected_stop_reason"),
        (
            "model_context_window_exceeded",
            ModelProviderContextLengthError,
            "model_context_window_exceeded",
        ),
    ],
)
def test_non_terminal_stop_reasons_fail_closed(stop_reason, error_type, code):
    with pytest.raises(error_type, match=code):
        parse_decision_messages_response(
            stop_reason_response(stop_reason),
            DEFAULT_MODEL_ID,
        )


def test_provider_refusal_is_not_an_unsupported_capability():
    refusal = parse_decision_messages_response(
        stop_reason_response("refusal"),
        DEFAULT_MODEL_ID,
    )
    assert refusal.kind is ModelDecisionKind.REFUSAL
    assert refusal.refusal_code == "refusal"
    unsupported = parse_decision_messages_response(
        end_turn_response(
            {
                "schema_version": DECISION_MODEL_DECISION_SCHEMA_VERSION,
                "kind": "UNSUPPORTED",
                "unsupported_reason": "OUT_OF_CATALOG",
            }
        ),
        DEFAULT_MODEL_ID,
    )
    assert unsupported.kind is ModelDecisionKind.UNSUPPORTED
    assert unsupported.unsupported_reason is DecisionUnsupportedReason.OUT_OF_CATALOG
    assert refusal.kind is not unsupported.kind


def test_client_exceptions_propagate():
    client = FakeAnthropicMessages((RuntimeError("network down"),))
    provider = AnthropicDecisionMessagesProvider(client=client)
    with pytest.raises(RuntimeError, match="network down"):
        provider.complete(_turn())


def test_offline_specialist_reaches_verified_factual_claims(monkeypatch):
    class _StubAdapter:
        def __init__(self, runtime) -> None:
            self.runtime = runtime

        def invoke(self, tool_name, arguments, *, tool_call_id):
            raw = _raw(tool_call_id)
            return DecisionToolInvocation(
                raw_tool_result=raw,
                model_projection=project_decision_tool_result(raw),
            )

    monkeypatch.setattr(
        "wilvor_ai.decision_specialist.DecisionToolsAdapter",
        _StubAdapter,
    )
    client = FakeAnthropicMessages(
        (
            tool_use_response(
                ("get_current_risk_evidence", {"aircraft_id": "ac-1"})
            ),
            end_turn_response(
                _final(_claim_examples()[DecisionClaimKind.AIRCRAFT_IDENTITY][1])
            ),
        )
    )
    specialist = DecisionSpecialist(
        provider=AnthropicDecisionMessagesProvider(client=client)
    )
    result = specialist.run(
        DecisionSpecialistRequest(
            user_text="status",
            mode=DecisionTargetMode.AIRCRAFT,
            aircraft_id="ac-1",
        ),
        DecisionToolsRuntime(
            tables=object(),
            now_epoch=1,
            query_timestamp_utc=AS_OF,
        ),
    )
    assert result.status is DecisionSpecialistRunStatus.COMPLETED
    assert result.verification.outcome is VerifierOutcome.PASSED
    assert result.render.outcome is DecisionRenderOutcome.FACTUAL
    assert result.proposed_claims[0].evidence_ref == "de-1"
    snapshot_text = client.requests[1]["messages"][1]["content"][0]["text"]
    assert "de-1" in snapshot_text
    assert "tool_call_id" not in snapshot_text
    assert AUDIT_CALL_ID not in snapshot_text


def test_malformed_provider_output_fails_the_specialist_without_prose(monkeypatch):
    monkeypatch.setattr(
        "wilvor_ai.decision_specialist.DecisionToolsAdapter",
        lambda runtime: None,
    )
    client = FakeAnthropicMessages(
        (
            _envelope(
                content=[
                    {
                        "type": "text",
                        "text": "not-json SECRET_PROVIDER_PROSE",
                    }
                ]
            ),
        )
    )
    result = DecisionSpecialist(
        provider=AnthropicDecisionMessagesProvider(client=client)
    ).run(
        DecisionSpecialistRequest(
            user_text="status",
            mode=DecisionTargetMode.AIRCRAFT,
            aircraft_id="ac-1",
        ),
        DecisionToolsRuntime(
            tables=object(),
            now_epoch=1,
            query_timestamp_utc=AS_OF,
        ),
    )
    assert result.status is DecisionSpecialistRunStatus.PROVIDER_FAILED
    assert result.runtime_errors == (DecisionRuntimeErrorCode.PROVIDER_EXCEPTION,)
    assert result.render is None
    assert result.verification is None
    encoded = json.dumps(result.to_dict())
    assert "SECRET_PROVIDER_PROSE" not in encoded
    assert "malformed_terminal_json" not in encoded
