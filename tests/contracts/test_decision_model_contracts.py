"""DE0 Decision model-turn and terminal-decision contract tests."""

from __future__ import annotations

import json

import pytest

from wilvor_ai.contracts import (
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    SourceRecord,
    TemporalScope,
    ToolInputField,
    ToolInputValueType,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.decision_claims import AircraftIdentityClaim, decision_claim_from_dict
from wilvor_ai.decision_contracts import (
    DecisionEvaluationState,
    DecisionEvidence,
    DecisionEvidenceKind,
    DecisionReportedLinkState,
    DecisionRiskEvidence,
    RiskPresence,
)
from wilvor_ai.decision_model_contracts import (
    DECISION_MODEL_DECISION_SCHEMA_VERSION,
    DECISION_MODEL_TOOL_NAMES,
    DECISION_MODEL_TOOL_SIGNATURES,
    DecisionEvidenceSnapshot,
    DecisionModelDecision,
    DecisionModelTurnRequest,
    DecisionUnsupportedReason,
)
from wilvor_ai.decision_tool_projection import (
    DecisionProjectedEvidence,
    DecisionToolProjection,
    project_decision_tool_result,
)
from wilvor_ai.decision_tools import DECISION_TOOLS, decision_tool_schemas
from wilvor_ai.persisted_airport_contracts import (
    PersistedAirportCandidate,
    PersistedAirportEvidence,
)
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


def _current_data(kind: DecisionEvidenceKind) -> dict:
    risk = None
    if kind is not DecisionEvidenceKind.DECISION_CONTEXT:
        risk = DecisionRiskEvidence(presence=RiskPresence.ABSENT)
    return DecisionEvidence(
        kind=kind,
        evaluation_state=DecisionEvaluationState.NOT_ESTABLISHED,
        aircraft_in_current_set=False,
        projection_state=DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES,
        chain_gaps=(),
        limitation_codes=(),
        risk=risk,
    ).to_dict()


def _projected_evidence(scope: TemporalScope = TemporalScope.CURRENT) -> DecisionProjectedEvidence:
    return DecisionProjectedEvidence(
        source="wilvor.decision.context",
        source_records=(),
        temporal_scope=scope,
        freshness_status=FreshnessStatus.UNKNOWN,
        confidence=ConfidenceLevel.UNKNOWN,
        limitations=(),
    )


def _projection(
    tool_name: str = "get_current_decision_context",
    scope: TemporalScope = TemporalScope.CURRENT,
    data: dict | None = None,
    evidence_scope: TemporalScope | None = None,
) -> DecisionToolProjection:
    if data is None:
        data = _current_data(DecisionEvidenceKind.DECISION_CONTEXT)
    return DecisionToolProjection(
        tool_name=tool_name,
        status=ToolResultStatus.SUCCESS,
        temporal_scope=scope,
        as_of_utc="2026-09-30T00:00:00Z",
        data=data,
        limitations=(),
        evidence=(_projected_evidence(scope if evidence_scope is None else evidence_scope),),
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


def _mapping_keys(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        found.update(str(key) for key in value)
        for item in value.values():
            found.update(_mapping_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_mapping_keys(item))
    return found


def _persisted_snapshot() -> DecisionEvidenceSnapshot:
    candidate = PersistedAirportCandidate(
        airport_id="KDEN",
        airport_assessment_id="aa-1",
        risk_id="risk-1",
        assessment_status="COMPLETE",
        route_safety_status="UNAVAILABLE",
        runway_evidence_status="UNAVAILABLE",
        congestion_evidence_status="UNAVAILABLE",
        created_at_utc="2026-09-30T00:00:00Z",
        created_at_epoch=1,
        expires_at_epoch=2,
        evaluation_version="ruleset-1",
        assessment_ruleset_version="ruleset-1",
        schema_version="wilvor.airport_assessment.v1",
        distance_nm=40,
        eta_minutes=20,
        candidate_reason="Within diversion search radius.",
        known_limitations=("Route hazard evaluation is not implemented yet.",),
        rank=1,
        total_airport_score=80,
        distance_score=70,
        weather_score=60,
        taf_score=50,
    )
    payload = PersistedAirportEvidence(
        recommendation_id="rec-1",
        airport_evaluation_id="eval-1",
        candidates=(candidate,),
    )
    result = ToolResult(
        tool_name="get_persisted_airport_candidate_evidence",
        tool_call_id="call-1",
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.PERSISTED,
        data=payload.to_dict(),
        evidence=(
            Evidence(
                source="query_airport_assessments_for_evaluation",
                source_records=(
                    SourceRecord(
                        record_id="aa-1",
                        source_version="wilvor.airport_assessment.v1",
                        event_timestamp_utc="2026-09-30T00:00:00Z",
                    ),
                ),
                query_timestamp_utc="2026-09-30T00:00:00Z",
                freshness_status=FreshnessStatus.UNKNOWN,
                confidence=ConfidenceLevel.UNKNOWN,
                limitations=(
                    "Persisted airport evidence is not current suitability.",
                ),
                tool_call_id="call-1",
                temporal_scope=TemporalScope.PERSISTED,
            ),
        ),
        as_of_utc=None,
        limitations=(),
        correlation_id="corr-1",
    )
    return DecisionEvidenceSnapshot(
        evidence_ref="de-4",
        projection=project_decision_tool_result(result),
    )


def _candidate_claim() -> dict:
    return {
        "kind": "PERSISTED_CANDIDATE",
        "evidence_ref": "de-4",
        "evidence_scope": "PERSISTED",
        "recommendation_id": "rec-1",
        "airport_id": "KDEN",
        "airport_assessment_id": "aa-1",
        "assessment_status": "COMPLETE",
        "route_safety_status": "UNAVAILABLE",
        "runway_evidence_status": "UNAVAILABLE",
        "congestion_evidence_status": "UNAVAILABLE",
    }


def test_persisted_projection_facts_round_trip_but_stay_unclaimable() -> None:
    snapshot = _persisted_snapshot()
    restored = DecisionEvidenceSnapshot.from_dict(snapshot.to_dict())
    assert restored == snapshot
    candidate = restored.projection.data["candidates"][0]
    assert candidate["rank"] == 1
    assert candidate["total_airport_score"] == 80
    assert candidate["distance_nm"] == 40
    assert candidate["eta_minutes"] == 20
    assert candidate["distance_score"] == 70
    request = DecisionModelTurnRequest(
        user_text="What persisted airport evidence is stored?",
        tools=_catalog(),
        evidence_snapshots=(restored,),
    )
    turned = DecisionModelTurnRequest.from_dict(request.to_dict())
    assert turned == request
    turned_candidate = turned.evidence_snapshots[0].projection.data["candidates"][0]
    assert turned_candidate["rank"] == 1
    assert turned_candidate["total_airport_score"] == 80
    assert turned_candidate["distance_nm"] == 40
    assert turned_candidate["eta_minutes"] == 20
    assert turned.evidence_snapshots[0].projection.temporal_scope is TemporalScope.PERSISTED
    assert turned.evidence_snapshots[0].projection.temporal_scope is not TemporalScope.CURRENT
    hidden = _mapping_keys(turned.evidence_snapshots[0].to_dict())
    assert "tool_call_id" not in hidden
    assert "correlation_id" not in hidden
    for field_name, value in (
        ("rank", 1),
        ("total_airport_score", 80),
        ("distance_nm", 40),
        ("eta_minutes", 20),
    ):
        assert_validation_error(
            "forbidden_decision_field",
            lambda field_name=field_name, value=value: decision_claim_from_dict(
                {**_candidate_claim(), field_name: value}
            ),
        )


def test_snapshot_scope_and_kind_are_closed() -> None:
    assert_validation_error(
        "invalid_decision_tool_name",
        lambda: DecisionEvidenceSnapshot.from_dict(
            {
                "evidence_ref": "de-1",
                "projection": _projection(tool_name="search_current_hazards").to_dict(),
            }
        ),
    )
    persisted = _persisted_snapshot().to_dict()
    persisted["projection"]["temporal_scope"] = "CURRENT"
    persisted["projection"]["evidence"][0]["temporal_scope"] = "CURRENT"
    assert_validation_error(
        "invalid_projection_temporal_scope",
        lambda: DecisionEvidenceSnapshot.from_dict(persisted),
    )
    for tool_name in (
        "get_current_decision_context",
        "get_current_risk_evidence",
        "get_current_recommendation",
    ):
        payload = _snapshot().to_dict()
        payload["projection"]["tool_name"] = tool_name
        payload["projection"]["temporal_scope"] = "PERSISTED"
        payload["projection"]["evidence"][0]["temporal_scope"] = "PERSISTED"
        if tool_name == "get_current_risk_evidence":
            payload["projection"]["data"] = _current_data(DecisionEvidenceKind.RISK_EVIDENCE)
        elif tool_name == "get_current_recommendation":
            payload["projection"]["data"] = _current_data(
                DecisionEvidenceKind.RECOMMENDATION_EVIDENCE
            )
        assert_validation_error(
            "invalid_projection_temporal_scope",
            lambda payload=payload: DecisionEvidenceSnapshot.from_dict(payload),
        )
    for scope in ("HISTORICAL", "HYBRID"):
        payload = _snapshot().to_dict()
        payload["projection"]["temporal_scope"] = scope
        payload["projection"]["evidence"][0]["temporal_scope"] = scope
        assert_validation_error(
            "invalid_projection_temporal_scope",
            lambda payload=payload: DecisionEvidenceSnapshot.from_dict(payload),
        )
    wrong_kind = _snapshot().to_dict()
    wrong_kind["projection"]["data"] = _current_data(DecisionEvidenceKind.RISK_EVIDENCE)
    assert_validation_error(
        "invalid_projection_evidence_kind",
        lambda: DecisionEvidenceSnapshot.from_dict(wrong_kind),
    )
    disagreed = _snapshot().to_dict()
    disagreed["projection"]["evidence"][0]["temporal_scope"] = "PERSISTED"
    assert_validation_error(
        "invalid_projected_evidence_scope",
        lambda: DecisionEvidenceSnapshot.from_dict(disagreed),
    )


def test_snapshot_rejects_audit_fields_at_each_envelope() -> None:
    snapshot_payload = _snapshot().to_dict()
    snapshot_payload["correlation_id"] = "corr-1"
    assert_validation_error(
        "forbidden_decision_field",
        lambda: DecisionEvidenceSnapshot.from_dict(snapshot_payload),
    )
    projection_payload = _snapshot().to_dict()
    projection_payload["projection"]["tables"] = ["aircraft"]
    assert_validation_error(
        "forbidden_decision_field",
        lambda: DecisionEvidenceSnapshot.from_dict(projection_payload),
    )
    evidence_payload = _snapshot().to_dict()
    evidence_payload["projection"]["evidence"][0]["query_timestamp_utc"] = (
        "2026-09-30T00:00:00Z"
    )
    assert_validation_error(
        "forbidden_decision_field",
        lambda: DecisionEvidenceSnapshot.from_dict(evidence_payload),
    )
    persisted = _persisted_snapshot().to_dict()
    persisted["projection"]["data"]["candidates"][0]["now_epoch"] = 1
    assert_validation_error(
        "forbidden_decision_field",
        lambda: DecisionEvidenceSnapshot.from_dict(persisted),
    )


def _catalog_replacing(name: str, fields: tuple[ToolInputField, ...]) -> tuple[ToolSchema, ...]:
    replaced = []
    for schema in _catalog():
        if schema.name == name:
            replaced.append(
                ToolSchema(
                    name=name,
                    description=schema.description,
                    input_fields=fields,
                )
            )
        else:
            replaced.append(schema)
    return tuple(replaced)


def test_advertised_signatures_match_the_closed_catalog() -> None:
    assert tuple(DECISION_MODEL_TOOL_SIGNATURES) == DECISION_MODEL_TOOL_NAMES
    for spec in DECISION_TOOLS:
        assert len(spec.input_fields) == 1
        field = spec.input_fields[0]
        assert DECISION_MODEL_TOOL_SIGNATURES[spec.name] == field.name
        assert field.required is True
        assert field.value_type is ToolInputValueType.STRING
    request = DecisionModelTurnRequest(
        user_text="Risk for N123?",
        tools=decision_tool_schemas(),
    )
    assert DecisionModelTurnRequest.from_dict(request.to_dict()) == request


def test_advertised_tool_signatures_reject_the_wrong_shape() -> None:
    cases = (
        ("get_current_risk_evidence", (_field("hazard_id"),)),
        ("get_current_recommendation", (_field("recommendation_id"),)),
        ("get_persisted_airport_candidate_evidence", (_field("aircraft_id"),)),
        ("get_current_risk_evidence", (_field("aircraft_id"), _field("hazard_id"))),
        (
            "get_current_decision_context",
            (
                ToolInputField(
                    name="aircraft_id",
                    required=False,
                    value_type=ToolInputValueType.STRING,
                    description="Stored identity",
                ),
            ),
        ),
        (
            "get_current_decision_context",
            (
                ToolInputField(
                    name="aircraft_id",
                    required=True,
                    value_type=ToolInputValueType.INTEGER,
                    description="Stored identity",
                ),
            ),
        ),
    )
    for name, fields in cases:
        assert_validation_error(
            "invalid_decision_tool_signature",
            lambda name=name, fields=fields: DecisionModelTurnRequest(
                user_text="Risk for N123?",
                tools=_catalog_replacing(name, fields),
            ),
        )


def test_turn_rejects_nested_runtime_fields() -> None:
    request = DecisionModelTurnRequest(
        user_text="Risk for N123?",
        tools=_catalog(),
        evidence_snapshots=(_snapshot(),),
        validation_feedback=ValidationFeedback(
            code=ValidationFeedbackCode.UNKNOWN_TOOL,
            tool_name="search_current_hazards",
        ),
    )
    tool_payload = request.to_dict()
    tool_payload["tools"][0]["tool_call_id"] = "call-1"
    assert_validation_error(
        "forbidden_decision_field",
        lambda: DecisionModelTurnRequest.from_dict(tool_payload),
    )
    input_payload = request.to_dict()
    input_payload["tools"][0]["input_fields"][0]["tables"] = "aircraft"
    assert_validation_error(
        "forbidden_decision_field",
        lambda: DecisionModelTurnRequest.from_dict(input_payload),
    )
    feedback_payload = request.to_dict()
    feedback_payload["validation_feedback"]["sql"] = "select 1"
    assert_validation_error(
        "forbidden_decision_field",
        lambda: DecisionModelTurnRequest.from_dict(feedback_payload),
    )
    extra_payload = request.to_dict()
    extra_payload["tools"][0]["note"] = "ignored"
    assert_validation_error(
        "unexpected_tool_schema_field",
        lambda: DecisionModelTurnRequest.from_dict(extra_payload),
    )
