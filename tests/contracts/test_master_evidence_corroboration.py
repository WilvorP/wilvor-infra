"""Offline corroboration tests. Consistency with a fixture is not a production read."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.fixtures.ma4_specialist_evidence_examples import (
    DECISION_AS_OF,
    HISTORICAL_AS_OF,
    OFFLINE_REQUEST_ID,
    SYNTHETIC_CORRELATION_ID,
    SYNTHETIC_REQUEST_ID,
    _decision_audit,
    decision_absent_called_low_case,
    decision_absent_risk_case,
    decision_authorization,
    decision_conflicting_risk_case,
    decision_distinct_scope_case,
    decision_offline_consistent_run,
    decision_unknown_evidence_ref_case,
    historical_authorization,
    historical_duplicate_claims_run,
    historical_exact_count_run,
    historical_forged_count_run,
    historical_incomplete_coverage_run,
    historical_truncated_called_exact_run,
    historical_truncated_lower_bound_run,
    historical_unknown_run,
    historical_unverified_passed_result,
    historical_verified_zero_without_evaluable_coverage_run,
    historical_zero_claim_without_tools,
    live_ops_all_verified_result,
    live_ops_authorization,
    live_ops_context,
    live_ops_none_verified_result,
    live_ops_partially_verified_result,
    live_ops_request,
    live_ops_request_mismatch_result,
)
from wilvor_ai.contracts import ContractValidationError, TemporalScope
from wilvor_ai.decision_answer_renderer import render_decision_verification
from wilvor_ai.decision_evidence_verifier import verify_decision_evidence
from wilvor_ai.decision_model_contracts import DecisionEvidenceSnapshot
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionCollectionPartialReason,
    DecisionSpecialistRunResult,
    DecisionSpecialistRunStatus,
    DecisionTargetMode,
)
from wilvor_ai.decision_tool_projection import project_decision_tool_result
from wilvor_ai.historical_answer_renderer import finalize_historical_specialist_run
from wilvor_ai.historical_evidence_verifier import ClaimRejectionCode
from wilvor_ai.master_contracts import MasterProductionAuthority, MasterSpecialist
from wilvor_ai.master_evidence_admission import (
    LiveOpsAdmissionSources,
    MasterAdmissionStatus,
    MasterEvidenceAdmissionInput,
    MasterTemporalLimitation,
)
from wilvor_ai.master_evidence_corroboration import (
    MASTER_EVIDENCE_CORROBORATION_SCHEMA_VERSION,
    MasterCorroborationReason,
    MasterCorroborationStatus,
    MasterEvidenceCorroboration,
    MasterEvidenceCorroborationCollection,
    MasterEvidenceCorroborationInput,
    corroborate_master_evidence,
)
from wilvor_ai.master_orchestration import SyntheticOutcome, SyntheticWorkerResult
from wilvor_ai.model_contracts import ModelDecisionKind
from wilvor_ai.specialist_contracts import ExactCountClaim, LowerBoundCountClaim, SpecialistResult, VerifierOutcome


def _historical_input(run, artifact: SpecialistResult | None = None, **authorization: object):
    final = finalize_historical_specialist_run(run) if artifact is None else artifact
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "invocation_id": "inv-hist-1",
        "correlation_id": None,
        "specialist_request_id": None,
        "requested_evaluation_time_utc": final.evaluated_as_of_utc,
    }
    values.update(authorization)
    auth = historical_authorization(**values)
    return MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(auth, final),
        historical_run=run,
    )


def _one(item: MasterEvidenceCorroborationInput):
    collection = corroborate_master_evidence((item,))
    assert collection.evidence_verified is False
    assert collection.production_authority is MasterProductionAuthority.DENIED
    assert collection.operationally_eligible is False
    return collection.results[0]


def _untrusted(result) -> None:
    assert result.evidence_verified is False
    assert result.production_authority is MasterProductionAuthority.DENIED
    assert result.operationally_eligible is False
    assert result.request_coverage.value == "NOT_ESTABLISHED"
    assert result.end_to_end_fulfilled is False
    assert result.source_authenticity.value == "NOT_ESTABLISHED"
    assert "answer" not in MasterEvidenceCorroboration.__dataclass_fields__


def _decision_auth(**overrides: object):
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "invocation_id": "inv-dec-1",
        "correlation_id": None,
        "specialist_request_id": None,
        "requested_evaluation_time_utc": DECISION_AS_OF,
        "expected_aircraft_id": "ac-1",
    }
    values.update(overrides)
    return decision_authorization(**values)


def _pack(claims, bindings, **overrides: object) -> DecisionSpecialistRunResult:
    verification = verify_decision_evidence(claims, bindings)
    rendered = render_decision_verification(verification)
    tools = tuple(item.raw_tool_result for item in bindings)
    persisted = sum(
        1
        for item in tools
        if item.tool_name == "get_persisted_airport_candidate_evidence"
    )
    values: dict[str, object] = {
        "status": DecisionSpecialistRunStatus.COMPLETED,
        "mode": DecisionTargetMode.AIRCRAFT,
        "aircraft_id": "ac-1",
        "recommendation_id": None,
        "tool_results": tools,
        "evidence_snapshots": tuple(
            DecisionEvidenceSnapshot(item.evidence_ref, project_decision_tool_result(item.raw_tool_result))
            for item in bindings
        ),
        "evidence_bindings": bindings,
        "invocations": tuple(
            _decision_audit(item.raw_tool_result, item.evidence_ref, "ac-1") for item in bindings
        ),
        "proposed_claims": claims,
        "verification": verification,
        "render": rendered,
        "unsupported_reason": None,
        "runtime_errors": (),
        "collection_partial_reason": None,
        "validation_feedback": (),
        "provider_turn_count": 1,
        "executed_tool_call_count": len(tools),
        "executed_persisted_call_count": persisted,
        "terminal_kind": ModelDecisionKind.FINAL_CLAIMS,
    }
    values.update(overrides)
    return DecisionSpecialistRunResult(**values)  # type: ignore[arg-type]


def _decision_input(artifact: DecisionSpecialistRunResult, **authorization: object):
    return MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(_decision_auth(**authorization), artifact)
    )


def _live_input(result, **authorization: object):
    auth = live_ops_authorization(
        master_request_id="master-req-1",
        invocation_id="inv-live-1",
        requested_evaluation_time_utc=None,
        **authorization,
    )
    return MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(
            auth,
            LiveOpsAdmissionSources(live_ops_request(), live_ops_context(), result),
        )
    )


def test_matching_historical_run_is_consistent_only_with_supplied_evidence():
    run = historical_exact_count_run()
    result = _one(_historical_input(run))
    assert result.corroboration_status is MasterCorroborationStatus.CONSISTENT_WITH_SUPPLIED_EVIDENCE
    assert result.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert MasterCorroborationReason.SUPPLIED_EVIDENCE_ONLY in result.reason_codes
    assert result.rejected_claim_codes == ()
    _untrusted(result)


def test_matching_decision_run_is_consistent_only_with_supplied_evidence():
    artifact = decision_offline_consistent_run()
    result = _one(_decision_input(artifact))
    assert artifact.verification is not None
    assert artifact.verification.outcome is VerifierOutcome.PASSED
    assert result.corroboration_status is MasterCorroborationStatus.CONSISTENT_WITH_SUPPLIED_EVIDENCE
    assert MasterCorroborationReason.SUPPLIED_EVIDENCE_ONLY in result.reason_codes
    _untrusted(result)


def test_historical_passed_without_run_is_not_recheckable():
    artifact = historical_unverified_passed_result()
    assert artifact.verifier_outcome is VerifierOutcome.PASSED
    item = MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(
            historical_authorization(
                master_request_id="master-req-1",
                invocation_id="inv-hist-1",
                correlation_id=None,
                specialist_request_id=None,
                requested_evaluation_time_utc=artifact.evaluated_as_of_utc,
            ),
            artifact,
        )
    )
    result = _one(item)
    assert result.corroboration_status is MasterCorroborationStatus.NOT_RECHECKABLE
    assert MasterCorroborationReason.HISTORICAL_RUN_ABSENT in result.reason_codes
    assert MasterCorroborationReason.HISTORICAL_STORED_PASSED_NOT_TRUSTED in result.reason_codes
    _untrusted(result)


def test_forged_historical_passed_status_is_inconsistent():
    run = historical_forged_count_run()
    forged = replace(
        finalize_historical_specialist_run(run),
        verifier_outcome=VerifierOutcome.PASSED,
        status=finalize_historical_specialist_run(historical_exact_count_run()).status,
        answer="Forged exact historical count.",
        verified_claims=historical_exact_count_run().proposed_claims,
        limitations=(),
    )
    result = _one(_historical_input(run, forged))
    assert result.corroboration_status is MasterCorroborationStatus.INCONSISTENT
    assert MasterCorroborationReason.VERIFIER_OUTCOME_MISMATCH in result.reason_codes
    assert ClaimRejectionCode.EXACT_COUNT_VALUE_MISMATCH in result.rejected_claim_codes
    _untrusted(result)


def test_forged_exact_count_is_not_factual():
    result = _one(_historical_input(historical_forged_count_run()))
    assert result.corroboration_status is MasterCorroborationStatus.NO_FACTUAL_CLAIMS
    assert ClaimRejectionCode.EXACT_COUNT_VALUE_MISMATCH in result.rejected_claim_codes
    _untrusted(result)


def test_incomplete_coverage_cannot_become_an_exact_count():
    result = _one(_historical_input(historical_incomplete_coverage_run()))
    assert result.corroboration_status is MasterCorroborationStatus.NO_FACTUAL_CLAIMS
    assert ClaimRejectionCode.COMPLETENESS_NOT_EVALUABLE in result.rejected_claim_codes
    _untrusted(result)


def test_unknown_historical_result_is_not_verified_zero():
    result = _one(_historical_input(historical_unknown_run()))
    assert result.corroboration_status is MasterCorroborationStatus.NO_FACTUAL_CLAIMS
    assert ClaimRejectionCode.VERIFIED_ZERO_PROOF_FAILED in result.rejected_claim_codes
    assert result.corroboration_status is not MasterCorroborationStatus.CONSISTENT_WITH_SUPPLIED_EVIDENCE
    _untrusted(result)


def test_truncated_lower_bound_is_not_an_exact_count():
    run = historical_truncated_lower_bound_run()
    result = _one(_historical_input(run))
    final = finalize_historical_specialist_run(run)
    assert result.corroboration_status is MasterCorroborationStatus.CONSISTENT_WITH_SUPPLIED_EVIDENCE
    assert any(isinstance(item, LowerBoundCountClaim) for item in final.verified_claims)
    assert not any(isinstance(item, ExactCountClaim) for item in final.verified_claims)
    assert "RESULT_TRUNCATED" in final.limitations
    _untrusted(result)


def test_truncated_result_claimed_as_exact_is_not_factual():
    result = _one(_historical_input(historical_truncated_called_exact_run()))
    assert result.corroboration_status is MasterCorroborationStatus.NO_FACTUAL_CLAIMS
    assert ClaimRejectionCode.EXACT_COUNT_VALUE_MISMATCH in result.rejected_claim_codes or (
        ClaimRejectionCode.EXACT_COUNT_STATUS_UNSUPPORTED in result.rejected_claim_codes
    )
    _untrusted(result)


def test_historical_answer_mismatch_is_rejected():
    run = historical_exact_count_run()
    forged = replace(finalize_historical_specialist_run(run), answer="A different historical answer.")
    result = _one(_historical_input(run, forged))
    assert result.corroboration_status is MasterCorroborationStatus.INCONSISTENT
    assert MasterCorroborationReason.ANSWER_MISMATCH in result.reason_codes


def test_historical_claim_mismatch_is_rejected():
    run = historical_exact_count_run()
    forged = replace(finalize_historical_specialist_run(run), verified_claims=())
    result = _one(_historical_input(run, forged))
    assert result.corroboration_status is MasterCorroborationStatus.INCONSISTENT
    assert MasterCorroborationReason.CLAIM_MISMATCH in result.reason_codes


def test_historical_tool_result_mismatch_is_rejected():
    run = historical_exact_count_run()
    forged = replace(finalize_historical_specialist_run(run), tool_results=())
    result = _one(_historical_input(run, forged))
    assert result.corroboration_status is MasterCorroborationStatus.INCONSISTENT
    assert MasterCorroborationReason.TOOL_RESULT_MISMATCH in result.reason_codes


def test_historical_evaluation_time_mismatch_is_rejected():
    run = historical_exact_count_run()
    forged = replace(
        finalize_historical_specialist_run(run),
        evaluated_as_of_utc="2026-02-01T00:00:00Z",
    )
    result = _one(
        _historical_input(
            run,
            forged,
            requested_evaluation_time_utc="2026-02-01T00:00:00Z",
        )
    )
    assert forged.evaluated_as_of_utc != HISTORICAL_AS_OF
    assert result.corroboration_status is MasterCorroborationStatus.INCONSISTENT
    assert MasterCorroborationReason.EVALUATION_TIME_MISMATCH in result.reason_codes


def test_duplicate_historical_claim_identity_is_not_factual():
    result = _one(_historical_input(historical_duplicate_claims_run()))
    assert result.corroboration_status is MasterCorroborationStatus.NO_FACTUAL_CLAIMS
    assert ClaimRejectionCode.DUPLICATE_CLAIM in result.rejected_claim_codes


def test_zero_without_tool_proof_is_not_factual():
    result = _one(_historical_input(historical_zero_claim_without_tools()))
    assert result.corroboration_status is MasterCorroborationStatus.NO_FACTUAL_CLAIMS
    assert ClaimRejectionCode.VERIFIED_ZERO_PROOF_FAILED in result.rejected_claim_codes or (
        ClaimRejectionCode.UNKNOWN_TOOL_CALL_ID in result.rejected_claim_codes
    )


def test_invalid_decision_evidence_reference_is_not_factual():
    claims, bindings = decision_unknown_evidence_ref_case()
    result = _one(_decision_input(_pack(claims, bindings)))
    assert result.corroboration_status is MasterCorroborationStatus.NO_FACTUAL_CLAIMS
    assert "UNKNOWN_EVIDENCE_REF" in result.rejected_claim_codes
    _untrusted(result)


def test_tampered_decision_verification_is_inconsistent():
    original = decision_offline_consistent_run()
    other = decision_offline_consistent_run(aircraft_id="ac-2", tool_call_id="call-offline-run-2")
    assert other.verification != original.verification
    object.__setattr__(original, "verification", other.verification)
    result = _one(_decision_input(original))
    assert result.corroboration_status is MasterCorroborationStatus.INCONSISTENT
    assert MasterCorroborationReason.DECISION_VERIFICATION_MISMATCH in result.reason_codes
    _untrusted(result)


def test_current_and_persisted_scopes_stay_distinct():
    claims, bindings = decision_distinct_scope_case()
    artifact = _pack(claims, bindings)
    result = _one(_decision_input(artifact))
    assert result.corroboration_status is MasterCorroborationStatus.CONSISTENT_WITH_SUPPLIED_EVIDENCE
    assert MasterTemporalLimitation.DECISION_SCOPE_CURRENT in result.temporal_limitations
    assert MasterTemporalLimitation.DECISION_SCOPE_PERSISTED in result.temporal_limitations
    assert artifact.verification is not None
    assert TemporalScope.CURRENT in artifact.verification.verified_temporal_scopes
    assert TemporalScope.PERSISTED in artifact.verification.verified_temporal_scopes
    _untrusted(result)


def test_recorded_risk_absence_is_not_low():
    claims, bindings, _payload = decision_absent_risk_case()
    result = _one(_decision_input(_pack(claims, bindings)))
    assert result.corroboration_status is MasterCorroborationStatus.CONSISTENT_WITH_SUPPLIED_EVIDENCE
    assert MasterCorroborationReason.RECORDED_RISK_ABSENCE_NOT_LOW in result.reason_codes
    _untrusted(result)


def test_low_claim_against_recorded_absence_is_not_factual():
    claims, bindings, _payload = decision_absent_called_low_case()
    result = _one(_decision_input(_pack(claims, bindings)))
    assert result.corroboration_status is MasterCorroborationStatus.NO_FACTUAL_CLAIMS
    assert "CLAIM_VALUE_MISMATCH" in result.rejected_claim_codes
    _untrusted(result)


def test_conflicting_risk_claims_are_not_factual():
    claims, bindings = decision_conflicting_risk_case()
    result = _one(_decision_input(_pack(claims, bindings)))
    assert result.corroboration_status is MasterCorroborationStatus.NO_FACTUAL_CLAIMS
    assert "CONTRADICTORY_CLAIMS" in result.rejected_claim_codes


def test_decision_target_mismatch_blocks_corroboration():
    result = _one(
        _decision_input(
            decision_offline_consistent_run(),
            expected_aircraft_id="ac-other",
        )
    )
    assert result.corroboration_status is MasterCorroborationStatus.ADMISSION_REJECTED
    assert result.admission_status is MasterAdmissionStatus.REJECTED
    _untrusted(result)


def test_partial_decision_result_stays_limited():
    consistent = decision_offline_consistent_run()
    partial = _pack(
        consistent.proposed_claims,
        consistent.evidence_bindings,
        status=DecisionSpecialistRunStatus.PARTIAL,
        collection_partial_reason=DecisionCollectionPartialReason.PERSISTED_FANOUT_CAP,
    )
    result = _one(_decision_input(partial))
    assert result.corroboration_status is MasterCorroborationStatus.CONSISTENT_WITH_SUPPLIED_EVIDENCE
    assert MasterCorroborationReason.DECISION_COLLECTION_PARTIAL in result.reason_codes
    assert partial.collection_partial_reason is DecisionCollectionPartialReason.PERSISTED_FANOUT_CAP
    _untrusted(result)


def test_live_ops_all_verified_is_not_independently_recheckable():
    result = _one(_live_input(live_ops_all_verified_result()))
    assert result.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert result.corroboration_status is MasterCorroborationStatus.NOT_RECHECKABLE
    assert MasterCorroborationReason.LIVE_OPS_RAW_EVIDENCE_UNAVAILABLE in result.reason_codes
    _untrusted(result)


def test_live_ops_partially_verified_is_not_independently_recheckable():
    result = _one(_live_input(live_ops_partially_verified_result()))
    assert result.corroboration_status is MasterCorroborationStatus.NOT_RECHECKABLE
    assert MasterCorroborationReason.LIVE_OPS_NOT_RECHECKABLE in result.reason_codes
    _untrusted(result)


def test_live_ops_none_verified_has_no_factual_corroboration():
    result = _one(_live_input(live_ops_none_verified_result()))
    assert result.corroboration_status is MasterCorroborationStatus.NO_FACTUAL_CLAIMS
    assert MasterCorroborationReason.LIVE_OPS_NONE_VERIFIED_NO_FACTS in result.reason_codes
    assert MasterCorroborationReason.LIVE_OPS_RAW_EVIDENCE_UNAVAILABLE in result.reason_codes
    _untrusted(result)


def test_rejected_live_ops_admission_blocks_corroboration():
    result = _one(
        _live_input(
            live_ops_request_mismatch_result(),
            specialist_request_id=SYNTHETIC_REQUEST_ID,
        )
    )
    assert live_ops_request_mismatch_result().request_id == OFFLINE_REQUEST_ID
    assert result.corroboration_status is MasterCorroborationStatus.ADMISSION_REJECTED
    _untrusted(result)


def test_cross_request_mismatch_is_rejected():
    first = _historical_input(historical_exact_count_run())
    second_run = historical_exact_count_run()
    second = _historical_input(second_run, **{"invocation_id": "inv-hist-2"})
    second = MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(
            historical_authorization(
                master_request_id="master-req-2",
                invocation_id="inv-hist-2",
                correlation_id=None,
                specialist_request_id=None,
                requested_evaluation_time_utc=HISTORICAL_AS_OF,
            ),
            second.admission_input.artifact,
        ),
        historical_run=second_run,
    )
    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        corroborate_master_evidence((first, second))


def test_distinct_specialist_invocations_share_one_master_request():
    historical = _historical_input(historical_exact_count_run())
    live = _live_input(live_ops_none_verified_result())
    collection = corroborate_master_evidence((historical, live))
    assert [item.invocation_id for item in collection.results] == ["inv-hist-1", "inv-live-1"]
    assert collection.results[0].corroboration_status is MasterCorroborationStatus.CONSISTENT_WITH_SUPPLIED_EVIDENCE
    assert collection.results[1].corroboration_status is MasterCorroborationStatus.NO_FACTUAL_CLAIMS
    assert collection.evidence_verified is False
    assert collection.operationally_eligible is False


def test_source_authenticity_stays_unestablished():
    result = _one(_historical_input(historical_exact_count_run()))
    assert MasterCorroborationReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED in result.reason_codes
    assert result.schema_version == MASTER_EVIDENCE_CORROBORATION_SCHEMA_VERSION
    _untrusted(result)


def test_production_authority_cannot_be_granted():
    result = _one(_historical_input(historical_exact_count_run()))
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        replace(result, production_authority=MasterProductionAuthority.PRODUCTION)
    with pytest.raises(ContractValidationError, match="operational_use_not_granted"):
        replace(result, operationally_eligible=True)
    with pytest.raises(ContractValidationError, match="synthetic_evidence_not_verified"):
        replace(result, evidence_verified=True)


def test_corroboration_result_has_no_factual_answer():
    result = _one(_decision_input(decision_offline_consistent_run()))
    assert "answer" not in MasterEvidenceCorroboration.__dataclass_fields__
    assert not hasattr(result, "from_dict")
    _untrusted(result)


def test_synthetic_orchestration_cannot_become_evidence():
    artifact = SyntheticWorkerResult(
        master_request_id="master-req-1",
        correlation_id="corr-1",
        invocation_id="inv-hist-1",
        specialist_request_id="master-req-1-specialist-1",
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        outcome=SyntheticOutcome.SYNTHETIC_COMPLETED,
        note="synthetic",
    )
    item = MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(
            historical_authorization(
                master_request_id="master-req-1",
                invocation_id="inv-hist-1",
                correlation_id=None,
                specialist_request_id=None,
                requested_evaluation_time_utc=None,
            ),
            artifact,
        )
    )
    result = _one(item)
    assert result.corroboration_status is MasterCorroborationStatus.ADMISSION_REJECTED
    _untrusted(result)


def test_unevaluable_zero_is_not_factual():
    result = _one(_historical_input(historical_verified_zero_without_evaluable_coverage_run()))
    assert result.corroboration_status is MasterCorroborationStatus.NO_FACTUAL_CLAIMS
    assert ClaimRejectionCode.VERIFIED_ZERO_PROOF_FAILED in result.rejected_claim_codes


def test_collection_constructor_enforces_its_own_limits():
    sample = _one(_historical_input(historical_exact_count_run()))
    with pytest.raises(ContractValidationError, match="empty_corroboration"):
        MasterEvidenceCorroborationCollection(master_request_id="master-req-1", results=())
    with pytest.raises(ContractValidationError, match="duplicate_invocation_id"):
        MasterEvidenceCorroborationCollection(master_request_id="master-req-1", results=(sample, sample))
    other = replace(sample, master_request_id="master-req-2", invocation_id="inv-hist-2")
    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        MasterEvidenceCorroborationCollection(master_request_id="master-req-1", results=(sample, other))
    with pytest.raises(ContractValidationError, match="excessive_plan_size"):
        MasterEvidenceCorroborationCollection(
            master_request_id="master-req-1",
            results=tuple(replace(sample, invocation_id=f"inv-{index}") for index in range(5)),
        )
    decision = replace(sample, specialist=MasterSpecialist.DECISION, invocation_id="inv-dec-1")
    decision_two = replace(sample, specialist=MasterSpecialist.DECISION, invocation_id="inv-dec-2")
    with pytest.raises(ContractValidationError, match="decision_fanout_not_permitted"):
        MasterEvidenceCorroborationCollection(
            master_request_id="master-req-1",
            results=(decision, decision_two),
        )
