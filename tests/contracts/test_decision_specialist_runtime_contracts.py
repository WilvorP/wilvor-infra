"""Structural contracts for the Decision specialist run."""

from __future__ import annotations

import pytest

from wilvor_ai.contracts import (
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.decision_answer_renderer import render_decision_verification
from wilvor_ai.decision_claims import AircraftIdentityClaim
from wilvor_ai.decision_contracts import (
    DecisionEvidence,
    DecisionEvidenceKind,
    DecisionEvaluationState,
    DecisionReportedLinkState,
    DecisionRiskEvidence,
    RiskPresence,
    StoredRiskLevel,
)
from wilvor_ai.decision_evidence_verifier import (
    DecisionEvidenceBinding,
    verify_decision_evidence,
)
from wilvor_ai.decision_model_contracts import DecisionEvidenceSnapshot
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionCollectionPartialReason,
    DecisionDispatchOrigin,
    DecisionRuntimeErrorCode,
    DecisionSpecialistRequest,
    DecisionSpecialistRunResult,
    DecisionSpecialistRunStatus,
    DecisionTargetMode,
    DecisionToolInvocationAudit,
)
from wilvor_ai.decision_tool_projection import project_decision_tool_result
from wilvor_ai.model_contracts import ModelDecisionKind


AS_OF = "2026-09-29T00:00:00Z"


def _errors(factory) -> list[str]:
    with pytest.raises(ContractValidationError) as caught:
        factory()
    return caught.value.errors


def _packet(tool_call_id: str) -> Evidence:
    return Evidence(
        source="wilvor.decision.specialist.test",
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


def _risk_payload(aircraft_id: str = "ac-1") -> dict:
    evidence = DecisionEvidence(
        kind=DecisionEvidenceKind.RISK_EVIDENCE,
        evaluation_state=DecisionEvaluationState.ESTABLISHED,
        aircraft_in_current_set=True,
        aircraft_id=aircraft_id,
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


def _raw(
    tool_call_id: str,
    tool_name: str = "get_current_risk_evidence",
    aircraft_id: str = "ac-1",
) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.CURRENT,
        data=_risk_payload(aircraft_id),
        evidence=(_packet(tool_call_id),),
        as_of_utc=AS_OF,
        limitations=(),
    )


def _snapshot(ref: str, raw: ToolResult) -> DecisionEvidenceSnapshot:
    projection = project_decision_tool_result(raw)
    return DecisionEvidenceSnapshot(ref, projection)


def _audit(
    raw: ToolResult,
    ref: str,
    *,
    tool_call_id: str | None = None,
) -> DecisionToolInvocationAudit:
    return DecisionToolInvocationAudit(
        tool_name=raw.tool_name,
        tool_call_id=raw.tool_call_id if tool_call_id is None else tool_call_id,
        evidence_ref=ref,
        requested_arguments={"aircraft_id": "ac-1"},
        executed_arguments={"aircraft_id": "ac-1"},
        canonical_key=f"{raw.tool_name}:{raw.tool_call_id}",
        dispatch_origin=DecisionDispatchOrigin.MODEL_REQUEST,
    )


def _failed_verification():
    claim = AircraftIdentityClaim("de-1", TemporalScope.CURRENT, "ac-1")
    verification = verify_decision_evidence((claim,), ())
    return (claim,), verification, render_decision_verification(verification)


def _result(**overrides) -> DecisionSpecialistRunResult:
    raw = _raw("call-1")
    values = {
        "status": DecisionSpecialistRunStatus.PROVIDER_FAILED,
        "mode": DecisionTargetMode.AIRCRAFT,
        "aircraft_id": "ac-1",
        "recommendation_id": None,
        "tool_results": (raw,),
        "evidence_snapshots": (_snapshot("de-1", raw),),
        "evidence_bindings": (DecisionEvidenceBinding("de-1", raw),),
        "invocations": (_audit(raw, "de-1"),),
        "proposed_claims": (),
        "verification": None,
        "render": None,
        "unsupported_reason": None,
        "runtime_errors": (DecisionRuntimeErrorCode.PROVIDER_EXCEPTION,),
        "collection_partial_reason": None,
        "validation_feedback": (),
        "provider_turn_count": 1,
        "executed_tool_call_count": 1,
        "executed_persisted_call_count": 0,
        "terminal_kind": None,
    }
    values.update(overrides)
    return DecisionSpecialistRunResult(**values)


def test_aircraft_request_rejects_whitespace_and_a_second_target():
    for aircraft_id in (" ac-1", "ac-1 ", "ac 1", "ac\t1"):
        assert "invalid_aircraft_id" in _errors(
            lambda aircraft_id=aircraft_id: DecisionSpecialistRequest(
                user_text="status",
                mode=DecisionTargetMode.AIRCRAFT,
                aircraft_id=aircraft_id,
            )
        )
    assert "invalid_recommendation_id" in _errors(
        lambda: DecisionSpecialistRequest(
            user_text="status",
            mode=DecisionTargetMode.DIRECT_PERSISTED,
            recommendation_id=" rec-1",
        )
    )
    assert "invalid_decision_target" in _errors(
        lambda: DecisionSpecialistRequest(
            user_text="status",
            mode=DecisionTargetMode.AIRCRAFT,
            aircraft_id="ac-1",
            recommendation_id="rec-1",
        )
    )


def test_request_round_trip_keeps_the_trusted_target():
    request = DecisionSpecialistRequest(
        user_text="status",
        mode=DecisionTargetMode.DIRECT_PERSISTED,
        recommendation_id="rec-1",
        request_id="req-1",
    )
    restored = DecisionSpecialistRequest.from_dict(request.to_dict())
    assert restored == request
    assert "tables" not in request.to_dict()


def test_provider_failed_may_keep_a_collection_reason_without_becoming_partial():
    result = _result(
        collection_partial_reason=DecisionCollectionPartialReason.PERSISTED_FANOUT_CAP
    )
    assert result.status is DecisionSpecialistRunStatus.PROVIDER_FAILED
    restored = DecisionSpecialistRunResult.from_dict(result.to_dict())
    assert restored == result
    assert restored.status is not DecisionSpecialistRunStatus.PARTIAL


def test_completed_with_failed_verification_is_structurally_valid():
    claims, verification, rendered = _failed_verification()
    result = _result(
        status=DecisionSpecialistRunStatus.COMPLETED,
        tool_results=(),
        evidence_snapshots=(),
        evidence_bindings=(),
        invocations=(),
        proposed_claims=claims,
        verification=verification,
        render=rendered,
        runtime_errors=(),
        provider_turn_count=1,
        executed_tool_call_count=0,
        terminal_kind=ModelDecisionKind.FINAL_CLAIMS,
    )
    assert result.verification.outcome.value == "FAILED"
    assert result.render.outcome.value == "VERIFICATION_FAILED"
    assert DecisionSpecialistRunResult.from_dict(result.to_dict()) == result


def test_completed_rejects_a_collection_partial_reason():
    claims, verification, rendered = _failed_verification()
    assert "completed_forbids_collection_partial_reason" in _errors(
        lambda: _result(
            status=DecisionSpecialistRunStatus.COMPLETED,
            tool_results=(),
            evidence_snapshots=(),
            evidence_bindings=(),
            invocations=(),
            proposed_claims=claims,
            verification=verification,
            render=rendered,
            runtime_errors=(),
            collection_partial_reason=(
                DecisionCollectionPartialReason.PERSISTED_FANOUT_BUDGET
            ),
            executed_tool_call_count=0,
            terminal_kind=ModelDecisionKind.FINAL_CLAIMS,
        )
    )


def test_partial_requires_a_collection_reason():
    claims, verification, rendered = _failed_verification()
    assert "partial_requires_collection_partial_reason" in _errors(
        lambda: _result(
            status=DecisionSpecialistRunStatus.PARTIAL,
            tool_results=(),
            evidence_snapshots=(),
            evidence_bindings=(),
            invocations=(),
            proposed_claims=claims,
            verification=verification,
            render=rendered,
            runtime_errors=(),
            executed_tool_call_count=0,
            terminal_kind=ModelDecisionKind.FINAL_CLAIMS,
        )
    )


def test_snapshot_ref_must_match_binding_ref():
    raw = _raw("call-1")
    assert "evidence_ref_mismatch" in _errors(
        lambda: _result(
            evidence_snapshots=(_snapshot("de-1", raw),),
            evidence_bindings=(DecisionEvidenceBinding("de-2", raw),),
            invocations=(_audit(raw, "de-1"),),
        )
    )


def test_audit_tool_call_id_must_match_raw_result():
    raw = _raw("call-1")
    assert "invocation_tool_call_id_mismatch" in _errors(
        lambda: _result(invocations=(_audit(raw, "de-1", tool_call_id="call-2"),))
    )


def test_duplicate_evidence_refs_are_rejected():
    first = _raw("call-1")
    second = _raw("call-2")
    assert "duplicate_evidence_ref" in _errors(
        lambda: _result(
            tool_results=(first, second),
            evidence_snapshots=(
                _snapshot("de-1", first),
                _snapshot("de-1", second),
            ),
            evidence_bindings=(
                DecisionEvidenceBinding("de-1", first),
                DecisionEvidenceBinding("de-1", second),
            ),
            invocations=(_audit(first, "de-1"), _audit(second, "de-1")),
            executed_tool_call_count=2,
        )
    )


def test_duplicate_raw_tool_call_ids_are_rejected():
    first = _raw("call-1")
    second = _raw("call-1")
    assert "duplicate_tool_call_id" in _errors(
        lambda: _result(
            tool_results=(first, second),
            evidence_snapshots=(_snapshot("de-1", first), _snapshot("de-2", second)),
            evidence_bindings=(
                DecisionEvidenceBinding("de-1", first),
                DecisionEvidenceBinding("de-2", second),
            ),
            invocations=(_audit(first, "de-1"), _audit(second, "de-2")),
            executed_tool_call_count=2,
        )
    )


def test_from_dict_enforces_the_same_ref_alignment():
    result = _result()
    payload = result.to_dict()
    payload["evidence_bindings"][0]["evidence_ref"] = "de-2"
    assert _errors(lambda: DecisionSpecialistRunResult.from_dict(payload))


def _factual_result(
    *,
    status: DecisionSpecialistRunStatus = DecisionSpecialistRunStatus.COMPLETED,
    aircraft_id: str = "ac-1",
    tool_call_id: str = "call-1",
) -> DecisionSpecialistRunResult:
    raw = _raw(tool_call_id, aircraft_id=aircraft_id)
    claim = AircraftIdentityClaim("de-1", TemporalScope.CURRENT, aircraft_id)
    binding = DecisionEvidenceBinding("de-1", raw)
    verification = verify_decision_evidence((claim,), (binding,))
    rendered = render_decision_verification(verification)
    return _result(
        status=status,
        aircraft_id="ac-1",
        tool_results=(raw,),
        evidence_snapshots=(_snapshot("de-1", raw),),
        evidence_bindings=(binding,),
        invocations=(_audit(raw, "de-1"),),
        proposed_claims=(claim,),
        verification=verification,
        render=rendered,
        runtime_errors=(),
        collection_partial_reason=(
            DecisionCollectionPartialReason.PERSISTED_FANOUT_CAP
            if status is DecisionSpecialistRunStatus.PARTIAL
            else None
        ),
        terminal_kind=ModelDecisionKind.FINAL_CLAIMS,
    )


def test_matching_final_result_round_trips():
    result = _factual_result()
    assert result.verification.outcome.value == "PASSED"
    assert result.render.outcome.value == "FACTUAL"
    assert DecisionSpecialistRunResult.from_dict(result.to_dict()) == result


def test_replaced_passed_verification_is_rejected():
    result = _factual_result()
    other = _factual_result(aircraft_id="ac-2", tool_call_id="call-2")
    assert other.verification.outcome.value == "PASSED"
    assert other.verification != result.verification
    payload = result.to_dict()
    payload["verification"] = other.verification.to_dict()
    assert "verification_integrity_mismatch" in _errors(
        lambda: DecisionSpecialistRunResult.from_dict(payload)
    )


def test_replaced_factual_answer_is_rejected():
    result = _factual_result()
    payload = result.to_dict()
    payload["render"]["answer"] = "The stored aircraft is ac-9."
    assert "render_integrity_mismatch" in _errors(
        lambda: DecisionSpecialistRunResult.from_dict(payload)
    )


def test_replaced_render_outcome_is_rejected():
    result = _factual_result()
    _claims, _verification, failed_render = _failed_verification()
    payload = result.to_dict()
    payload["render"] = {
        "outcome": failed_render.outcome.value,
        "answer": failed_render.answer,
    }
    assert _errors(lambda: DecisionSpecialistRunResult.from_dict(payload))


def test_failed_verification_with_matching_render_remains_valid():
    claims, verification, rendered = _failed_verification()
    result = _result(
        status=DecisionSpecialistRunStatus.COMPLETED,
        tool_results=(),
        evidence_snapshots=(),
        evidence_bindings=(),
        invocations=(),
        proposed_claims=claims,
        verification=verification,
        render=rendered,
        runtime_errors=(),
        executed_tool_call_count=0,
        terminal_kind=ModelDecisionKind.FINAL_CLAIMS,
    )
    assert result.verification.outcome.value == "FAILED"
    assert result.render.outcome.value == "VERIFICATION_FAILED"
    assert DecisionSpecialistRunResult.from_dict(result.to_dict()) == result


def test_partial_factual_result_remains_valid_when_artifacts_match():
    result = _factual_result(status=DecisionSpecialistRunStatus.PARTIAL)
    assert result.collection_partial_reason is (
        DecisionCollectionPartialReason.PERSISTED_FANOUT_CAP
    )
    assert result.verification.outcome.value == "PASSED"
    assert result.render.outcome.value == "FACTUAL"
    assert DecisionSpecialistRunResult.from_dict(result.to_dict()) == result


def test_provider_failed_does_not_recompute_verification(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "wilvor_ai.decision_specialist_runtime_contracts.verify_decision_evidence",
        lambda *args, **kwargs: calls.append(args),
    )
    monkeypatch.setattr(
        "wilvor_ai.decision_specialist_runtime_contracts.render_decision_verification",
        lambda *args, **kwargs: calls.append(args),
    )
    result = _result(
        collection_partial_reason=DecisionCollectionPartialReason.PERSISTED_FANOUT_CAP
    )
    assert result.status is DecisionSpecialistRunStatus.PROVIDER_FAILED
    assert result.verification is None
    assert result.render is None
    assert calls == []
