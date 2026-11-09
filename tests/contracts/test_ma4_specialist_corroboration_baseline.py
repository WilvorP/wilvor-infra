"""OFFLINE_TEST_FIXTURE baseline for specialist corroboration.

These tests call the existing deterministic verifiers and Master
classification. A passing result is an in-memory contract outcome. It does
not prove an Athena query, an AWS read, or production execution authority.
"""

from __future__ import annotations

import inspect

import pytest

from wilvor_ai.contracts import ContractValidationError, ToolResultStatus
from wilvor_ai.decision_evidence_verifier import (
    DecisionClaimRejectionCode,
    verify_decision_evidence,
)
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionSpecialistRunResult,
)
from wilvor_ai.historical_analytics_mapping import RESULT_TRUNCATED_LIMITATION
from wilvor_ai.historical_evidence_verifier import (
    ClaimRejectionCode,
    verify_historical_specialist_run,
)
from wilvor_ai.live_ops_public_contracts import (
    LIVE_OPS_ANSWER_NONE_VERIFIED,
    LIVE_OPS_COMPLETENESS_NOTICE,
    LIVE_OPS_ENTITY_AUTHORITY,
    LiveOpsAnswerOrigin,
    LiveOpsClaimDiagnostic,
    LiveOpsEvidenceReference,
    LiveOpsPublicClaimKind,
    LiveOpsPublicClaimVerdict,
    LiveOpsPublicRenderingPosture,
    LiveOpsPublicVerificationOutcome,
)
from wilvor_ai.master_contracts import (
    MasterBindingDefect,
    MasterCallsignObservation,
    MasterCondition,
    MasterInvocationAuthorization,
    MasterInvocationRecord,
    MasterProductionAuthority,
    MasterReportedShape,
    MasterVerification,
    classify_decision_result,
    classify_historical_result,
    classify_live_ops_result,
    observe_callsign,
)
from wilvor_ai.master_orchestration import (
    SyntheticOutcome,
    SyntheticProductionAuthority,
    SyntheticWorkerResult,
)
from wilvor_ai.specialist_contracts import VerifierOutcome
from tests.fixtures.ma4_specialist_evidence_examples import (
    DECISION_AS_OF,
    FRESHNESS_NOT_ESTABLISHED,
    HISTORICAL_AS_OF,
    OFFLINE_CORRELATION_ID,
    OFFLINE_REQUEST_ID,
    OFFLINE_TEST_FIXTURE,
    OTHER_AIRCRAFT_ID,
    decision_absent_called_low_case,
    decision_absent_risk_case,
    decision_authorization,
    decision_conflicting_risk_case,
    decision_distinct_scope_case,
    decision_incomplete_chain_case,
    decision_offline_consistent_run,
    decision_present_risk_case,
    decision_scope_rejected_risk_claim,
    decision_unknown_evidence_ref_case,
    decision_valid_identity_case,
    decision_wrong_aircraft_case,
    historical_authorization,
    historical_contradictory_claims_run,
    historical_duplicate_claims_run,
    historical_exact_count_run,
    historical_forged_count_run,
    historical_incomplete_coverage_run,
    historical_missing_tool_call_run,
    historical_non_historical_scope_run,
    historical_truncated_called_exact_run,
    historical_truncated_lower_bound_run,
    historical_unknown_run,
    historical_unverified_passed_result,
    historical_verified_zero_run,
    historical_verified_zero_without_evaluable_coverage_run,
    historical_wrong_window_run,
    historical_zero_claim_without_tools,
    live_ops_all_verified_result,
    live_ops_authorization,
    live_ops_context,
    live_ops_correlation_mismatch_result,
    live_ops_none_verified_result,
    live_ops_partially_verified_result,
    live_ops_public_evidence_reference,
    live_ops_request,
    live_ops_request_mismatch_result,
    live_ops_unique_callsign_result,
)


def test_historical_supported_exact_count_passes_without_proving_athena() -> None:
    run = historical_exact_count_run()
    verification = verify_historical_specialist_run(run)

    assert verification.outcome is VerifierOutcome.PASSED
    assert verification.verified_claims == run.proposed_claims
    assert verification.evaluated_as_of_utc == HISTORICAL_AS_OF
    source = inspect.getsource(verify_historical_specialist_run)
    assert "AthenaExecutor" not in source
    assert "boto3" not in source


def test_historical_forged_count_fails() -> None:
    verification = verify_historical_specialist_run(historical_forged_count_run())

    assert verification.outcome is VerifierOutcome.FAILED
    assert verification.verified_claims == ()
    assert ClaimRejectionCode.EXACT_COUNT_VALUE_MISMATCH in verification.rejected_claim_codes


def test_historical_missing_tool_call_id_fails() -> None:
    verification = verify_historical_specialist_run(historical_missing_tool_call_run())

    assert verification.outcome is VerifierOutcome.FAILED
    assert ClaimRejectionCode.UNKNOWN_TOOL_CALL_ID in verification.rejected_claim_codes


def test_historical_duplicate_claims_fail() -> None:
    verification = verify_historical_specialist_run(historical_duplicate_claims_run())

    assert verification.outcome is VerifierOutcome.FAILED
    assert verification.verified_claims == ()
    assert ClaimRejectionCode.DUPLICATE_CLAIM in verification.rejected_claim_codes


def test_historical_contradictory_claims_fail() -> None:
    verification = verify_historical_specialist_run(historical_contradictory_claims_run())

    assert verification.outcome is VerifierOutcome.FAILED
    assert ClaimRejectionCode.CONTRADICTORY_CLAIMS in verification.rejected_claim_codes


def test_historical_wrong_window_fails() -> None:
    verification = verify_historical_specialist_run(historical_wrong_window_run())

    assert verification.outcome is VerifierOutcome.FAILED
    assert ClaimRejectionCode.WINDOW_MISMATCH in verification.rejected_claim_codes


def test_historical_non_historical_scope_fails() -> None:
    verification = verify_historical_specialist_run(historical_non_historical_scope_run())

    assert verification.outcome is VerifierOutcome.FAILED
    assert ClaimRejectionCode.NON_HISTORICAL_TOOL in verification.rejected_claim_codes


def test_historical_incomplete_coverage_cannot_prove_exact_count() -> None:
    run = historical_incomplete_coverage_run()
    verification = verify_historical_specialist_run(run)

    assert run.tool_results[0].status is ToolResultStatus.SUCCESS
    assert verification.outcome is VerifierOutcome.FAILED
    assert ClaimRejectionCode.COMPLETENESS_NOT_EVALUABLE in verification.rejected_claim_codes


def test_historical_verified_zero_requires_evaluable_exact_cardinality() -> None:
    passed = verify_historical_specialist_run(historical_verified_zero_run())
    failed = verify_historical_specialist_run(
        historical_verified_zero_without_evaluable_coverage_run()
    )

    assert passed.outcome is VerifierOutcome.PASSED
    assert passed.verified_claims[0].tool_call_id
    assert not hasattr(passed.verified_claims[0], "value")
    assert failed.outcome is VerifierOutcome.FAILED
    assert ClaimRejectionCode.VERIFIED_ZERO_PROOF_FAILED in failed.rejected_claim_codes


def test_historical_unknown_is_not_verified_zero() -> None:
    run = historical_unknown_run()
    verification = verify_historical_specialist_run(run)

    assert run.tool_results[0].status is ToolResultStatus.UNKNOWN
    assert verification.outcome is VerifierOutcome.FAILED
    assert verification.verified_claims == ()
    assert ClaimRejectionCode.VERIFIED_ZERO_PROOF_FAILED in verification.rejected_claim_codes


def test_historical_truncated_lower_bound_is_not_exact_count() -> None:
    bounded = verify_historical_specialist_run(historical_truncated_lower_bound_run())
    exact = verify_historical_specialist_run(historical_truncated_called_exact_run())
    tool = historical_truncated_lower_bound_run().tool_results[0]

    assert bounded.outcome is VerifierOutcome.PASSED
    assert bounded.derived_specialist_status.value == "PARTIAL"
    assert RESULT_TRUNCATED_LIMITATION in bounded.mandatory_limitations
    assert bounded.verified_claims[0].minimum_value == 6
    assert len(tool.data["result"]["records"]) == 1
    assert tool.evidence[0].match_cardinality.is_exact is False
    assert exact.outcome is VerifierOutcome.FAILED
    assert ClaimRejectionCode.EXACT_COUNT_STATUS_UNSUPPORTED in exact.rejected_claim_codes


def test_hand_built_passed_specialist_result_is_not_source_authentication() -> None:
    hand_built = historical_unverified_passed_result()
    classified = classify_historical_result(historical_authorization(), hand_built)
    rerun = verify_historical_specialist_run(historical_zero_claim_without_tools())

    assert hand_built.verifier_outcome is VerifierOutcome.PASSED
    assert hand_built.tool_results == ()
    assert classified.record.verification is MasterVerification.PASSED
    assert classified.record.production_authority is MasterProductionAuthority.DENIED
    assert rerun.outcome is VerifierOutcome.FAILED
    assert ClaimRejectionCode.UNKNOWN_TOOL_CALL_ID in rerun.rejected_claim_codes


def test_source_observation_time_is_not_inferred_from_query_time() -> None:
    run = historical_exact_count_run()
    verification = verify_historical_specialist_run(run)
    tool = run.tool_results[0]
    finalized = historical_unverified_passed_result()
    classified_from_query = classify_historical_result(
        historical_authorization(),
        type(finalized)(
            status=finalized.status,
            answer=finalized.answer,
            temporal_scope=finalized.temporal_scope,
            evaluated_as_of_utc=tool.as_of_utc,
            tool_results=run.tool_results,
            verified_claims=verification.verified_claims,
            limitations=verification.mandatory_limitations,
            unsupported_reason=None,
            verifier_outcome=verification.outcome,
        ),
    )

    assert verification.outcome is VerifierOutcome.PASSED
    assert tool.evidence[0].query_timestamp_utc == tool.as_of_utc == HISTORICAL_AS_OF
    assert classified_from_query.record.evidence_evaluation_time_utc == tool.evidence[0].query_timestamp_utc
    assert classified_from_query.record.source_observation_time_utc is None
    assert MasterCondition.FRESHNESS_UNKNOWN in classified_from_query.record.condition_flags


def test_decision_claims_are_checked_against_supplied_bindings() -> None:
    claims, bindings = decision_valid_identity_case()
    verification = verify_decision_evidence(claims, bindings)

    assert verification.outcome is VerifierOutcome.PASSED
    assert verification.verified_claims[0].aircraft_id == "ac-1"
    assert verification.verified_claims[0].evidence_ref == bindings[0].evidence_ref
    assert bindings[0].raw_tool_result.evidence[0].freshness_status.value == "UNKNOWN"
    source = inspect.getsource(verify_decision_evidence)
    assert "boto3" not in source


def test_decision_unknown_evidence_reference_fails() -> None:
    claims, bindings = decision_unknown_evidence_ref_case()
    verification = verify_decision_evidence(claims, bindings)

    assert verification.outcome is VerifierOutcome.FAILED
    assert verification.verified_claims == ()
    assert (
        DecisionClaimRejectionCode.UNKNOWN_EVIDENCE_REF.value
        in verification.rejected_claim_codes
    )


def test_decision_mismatched_evidence_reference_is_rejected_on_run_construction() -> None:
    run = decision_offline_consistent_run()
    payload = run.to_dict()
    payload["evidence_bindings"][0]["evidence_ref"] = "de-2"

    with pytest.raises(ContractValidationError) as caught:
        DecisionSpecialistRunResult.from_dict(payload)

    assert (
        "evidence_ref_mismatch" in caught.value.errors
        or "verification_integrity_mismatch" in caught.value.errors
    )


def test_decision_conflicting_risk_claims_fail() -> None:
    claims, bindings = decision_conflicting_risk_case()
    verification = verify_decision_evidence(claims, bindings)

    assert verification.outcome is VerifierOutcome.FAILED
    assert (
        DecisionClaimRejectionCode.CONTRADICTORY_CLAIMS.value
        in verification.rejected_claim_codes
    )


def test_decision_absent_risk_is_not_low() -> None:
    absent_claims, bindings, evidence_payload = decision_absent_risk_case()
    low_claims, low_bindings, low_evidence = decision_absent_called_low_case()
    proved = verify_decision_evidence(absent_claims, bindings)
    rejected = verify_decision_evidence(low_claims, low_bindings)

    assert evidence_payload.risk.presence.value == "ABSENT"
    assert evidence_payload.risk.risk_level is None
    assert low_evidence.risk.risk_level is None
    assert proved.outcome is VerifierOutcome.PASSED
    assert rejected.outcome is VerifierOutcome.FAILED
    assert (
        DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value
        in rejected.rejected_claim_codes
    )


def test_decision_current_and_persisted_scopes_stay_distinct() -> None:
    claims, bindings = decision_distinct_scope_case()
    verification = verify_decision_evidence(claims, bindings)

    assert verification.outcome is VerifierOutcome.PASSED
    assert verification.verified_temporal_scopes == (
        claims[0].evidence_scope,
        claims[1].evidence_scope,
    )
    assert claims[0].evidence_scope.value == "CURRENT"
    assert claims[1].evidence_scope.value == "PERSISTED"
    with pytest.raises(ContractValidationError) as caught:
        decision_scope_rejected_risk_claim()
    assert "invalid_evidence_scope" in caught.value.errors


def test_decision_verified_claim_cannot_introduce_a_new_target() -> None:
    claims, bindings = decision_wrong_aircraft_case()
    verification = verify_decision_evidence(claims, bindings)
    consistent = decision_offline_consistent_run()
    matched = classify_decision_result(
        decision_authorization(expected_aircraft_id="ac-1"),
        consistent,
    )
    mismatched = classify_decision_result(
        decision_authorization(expected_aircraft_id=OTHER_AIRCRAFT_ID),
        consistent,
    )

    assert verification.outcome is VerifierOutcome.FAILED
    assert verification.verified_claims == ()
    assert (
        DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value
        in verification.rejected_claim_codes
    )
    assert matched.record.verification is MasterVerification.PASSED
    assert matched.record.production_authority is MasterProductionAuthority.DENIED
    assert MasterBindingDefect.TARGET_MISMATCH in mismatched.record.binding_defects
    assert mismatched.record.reported_shape is MasterReportedShape.DOES_NOT_QUALIFY
    assert "expected_aircraft_id" not in MasterInvocationRecord.__dataclass_fields__


def test_decision_consistent_fixture_remains_an_offline_tool_result() -> None:
    run = decision_offline_consistent_run()
    raw = run.evidence_bindings[0].raw_tool_result
    verification = verify_decision_evidence(run.proposed_claims, run.evidence_bindings)

    assert run.verification is not None
    assert run.verification.outcome is VerifierOutcome.PASSED
    assert verification == run.verification
    assert raw.evidence[0].freshness_status.value == "UNKNOWN"
    assert FRESHNESS_NOT_ESTABLISHED in raw.evidence[0].limitations
    assert raw.evidence[0].query_executions == ()
    assert "production_authority" not in raw.to_dict()
    assert raw.as_of_utc == DECISION_AS_OF
    assert run.verification.current_as_of_utc == DECISION_AS_OF


def test_decision_run_construction_rejects_tampered_verification() -> None:
    original = decision_offline_consistent_run()
    other = decision_offline_consistent_run(
        aircraft_id="ac-2",
        tool_call_id="call-offline-run-2",
    )
    payload = original.to_dict()
    payload["verification"] = other.verification.to_dict()

    assert other.verification.outcome is VerifierOutcome.PASSED
    assert other.verification != original.verification
    with pytest.raises(ContractValidationError) as caught:
        DecisionSpecialistRunResult.from_dict(payload)
    assert "verification_integrity_mismatch" in caught.value.errors


def test_decision_incomplete_chain_stays_partial() -> None:
    claims, bindings, raw = decision_incomplete_chain_case()
    verification = verify_decision_evidence(claims, bindings)

    assert raw.status is ToolResultStatus.PARTIAL
    assert verification.outcome is VerifierOutcome.PASSED
    assert verification.verified_claims[0].chain_gap.value == "INCOMPLETE_CURRENT_CHAIN"
    assert raw.status is not ToolResultStatus.SUCCESS


def test_live_ops_public_result_can_be_constructed_offline() -> None:
    result = live_ops_all_verified_result()

    assert result.execution_status.value == "COMPLETED"
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED
    assert result.verified_claims
    assert result.completeness_notice == LIVE_OPS_COMPLETENESS_NOTICE
    assert "LO2" not in result.answer


def test_live_ops_all_verified_does_not_prove_freshness() -> None:
    result = live_ops_all_verified_result()
    classified = classify_live_ops_result(
        live_ops_authorization(),
        live_ops_request(),
        live_ops_context(),
        result,
    )

    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED
    assert "fresh" in result.completeness_notice
    assert "freshness_status" not in result.to_dict()
    assert MasterCondition.FRESHNESS_UNKNOWN in classified.record.condition_flags
    assert classified.record.production_authority is MasterProductionAuthority.DENIED
    assert classified.record.source_observation_time_utc is None


def test_live_ops_all_verified_does_not_prove_complete_network_coverage() -> None:
    result = live_ops_all_verified_result()
    classified = classify_live_ops_result(
        live_ops_authorization(),
        live_ops_request(),
        live_ops_context(),
        result,
    )

    assert "complete" in result.completeness_notice
    assert "complete_network_snapshot" not in result.to_dict()
    assert (
        MasterCondition.SOURCE_COMPLETENESS_NOT_ESTABLISHED
        in classified.record.condition_flags
    )
    assert classified.record.temporal_scopes == ()


def test_live_ops_partially_verified_remains_partial() -> None:
    result = live_ops_partially_verified_result()
    classified = classify_live_ops_result(
        live_ops_authorization(),
        live_ops_request(),
        live_ops_context(),
        result,
    )

    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED
    assert result.rendering_posture is (
        LiveOpsPublicRenderingPosture.INCOMPLETE_VERIFIED_SUBSET
    )
    assert result.verification_outcome is not LiveOpsPublicVerificationOutcome.ALL_VERIFIED
    assert MasterCondition.INCOMPLETE_INVESTIGATION in classified.record.condition_flags
    assert classified.record.production_authority is MasterProductionAuthority.DENIED


def test_live_ops_none_verified_cannot_become_a_factual_answer() -> None:
    result = live_ops_none_verified_result()
    classified = classify_live_ops_result(
        live_ops_authorization(),
        live_ops_request(),
        live_ops_context(),
        result,
    )

    assert result.verified_claims == ()
    assert result.answer_origin is LiveOpsAnswerOrigin.FIXED_SAFE_TEXT
    assert result.answer == LIVE_OPS_ANSWER_NONE_VERIFIED
    assert classified.record.verification is MasterVerification.NO_VERIFIED_FACTS
    assert classified.record.reported_shape is MasterReportedShape.DOES_NOT_QUALIFY
    assert result.answer_origin is not LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER


def test_live_ops_request_id_mismatch_is_classified() -> None:
    result = live_ops_request_mismatch_result()
    classified = classify_live_ops_result(
        live_ops_authorization(),
        live_ops_request(),
        live_ops_context(),
        result,
    )

    assert result.request_id == OFFLINE_REQUEST_ID
    assert MasterBindingDefect.REQUEST_ID_MISMATCH in classified.record.binding_defects
    assert classified.record.reported_shape is MasterReportedShape.DOES_NOT_QUALIFY
    assert classified.record.production_authority is MasterProductionAuthority.DENIED


def test_live_ops_correlation_mismatch_is_classified() -> None:
    result = live_ops_correlation_mismatch_result()
    classified = classify_live_ops_result(
        live_ops_authorization(),
        live_ops_request(),
        live_ops_context(),
        result,
    )

    assert result.correlation_id == OFFLINE_CORRELATION_ID
    assert MasterBindingDefect.CORRELATION_MISMATCH in classified.record.binding_defects
    assert classified.record.reported_shape is MasterReportedShape.DOES_NOT_QUALIFY


def test_live_ops_public_evidence_reference_is_not_a_tool_result() -> None:
    reference = live_ops_public_evidence_reference()
    payload = reference.to_dict()

    assert isinstance(reference, LiveOpsEvidenceReference)
    assert set(payload) == {
        "tool_call_id",
        "tool_name",
        "as_of_epoch",
        "as_of_utc",
        "limitation_codes",
    }
    assert "data" not in payload
    assert "source_records" not in payload
    payload["source_records"] = []
    with pytest.raises(ContractValidationError) as caught:
        LiveOpsEvidenceReference.from_dict(payload)
    assert "unexpected_evidence_field" in caught.value.errors


def test_live_ops_model_text_is_not_operational_verification() -> None:
    request = live_ops_request()
    result = live_ops_all_verified_result()
    assistant_text = request.prior_turns[1].text

    assert request.prior_turns[1].role == "assistant"
    assert assistant_text not in result.answer
    assert all(assistant_text not in str(claim.to_dict()) for claim in result.verified_claims)
    with pytest.raises(ContractValidationError) as caught:
        LiveOpsClaimDiagnostic(
            claim_kind=LiveOpsPublicClaimKind.CONFIRMED_IMPACT,
            verdict=LiveOpsPublicClaimVerdict.VERIFIED,
            codes=("OFFLINE_TEST_FIXTURE",),
        )
    assert "invalid_verdict" in caught.value.errors


def test_unique_callsign_discovery_is_not_decision_authorization() -> None:
    result = live_ops_unique_callsign_result()
    discovery = result.discoveries[0]
    observation = observe_callsign(discovery)
    classified = classify_live_ops_result(
        live_ops_authorization(),
        live_ops_request(),
        live_ops_context(),
        result,
    )

    assert discovery.identity_resolved is True
    assert discovery.selection_authorized is False
    assert isinstance(observation, MasterCallsignObservation)
    assert not isinstance(observation, MasterInvocationAuthorization)
    assert "expected_aircraft_id" not in MasterCallsignObservation.__dataclass_fields__
    assert result.verified_entity_refs[0].authority == LIVE_OPS_ENTITY_AUTHORITY
    assert classified.record.production_authority is MasterProductionAuthority.DENIED
    assert "expected_aircraft_id" not in classified.record.to_dict()


def test_in_memory_tool_result_has_no_source_authentication_marker() -> None:
    tool = historical_exact_count_run().tool_results[0]
    payload = tool.to_dict()

    assert "production_authority" not in payload
    assert "source_authenticated" not in payload
    assert payload["evidence"][0]["freshness_status"] == "UNKNOWN"
    assert "source_authenticated" not in tool.evidence[0].to_dict()


def test_synthetic_worker_result_cannot_claim_verified_or_production_evidence() -> None:
    denied = SyntheticWorkerResult(
        master_request_id="master-req-offline-synthetic",
        correlation_id="corr-offline-synthetic",
        invocation_id="inv-offline-synthetic",
        specialist_request_id="specialist-req-offline-synthetic",
        specialist=live_ops_authorization().specialist,
        outcome=SyntheticOutcome.SYNTHETIC_COMPLETED,
        note=OFFLINE_TEST_FIXTURE,
        evidence_verified=False,
        production_authority=SyntheticProductionAuthority.DENIED,
    )

    assert denied.evidence_verified is False
    assert denied.production_authority is SyntheticProductionAuthority.DENIED
    assert not hasattr(denied, "tool_results")
    with pytest.raises(ContractValidationError) as caught:
        SyntheticWorkerResult(
            master_request_id="master-req-offline-synthetic",
            correlation_id="corr-offline-synthetic",
            invocation_id="inv-offline-synthetic",
            specialist_request_id="specialist-req-offline-synthetic",
            specialist=live_ops_authorization().specialist,
            outcome=SyntheticOutcome.SYNTHETIC_COMPLETED,
            note=OFFLINE_TEST_FIXTURE,
            evidence_verified=True,
        )
    assert "synthetic_evidence_not_verified" in caught.value.errors


def test_master_verification_enum_has_no_generic_verified_value() -> None:
    assert "VERIFIED" not in MasterVerification.__members__
    assert set(MasterVerification.__members__) == {
        "NOT_RUN",
        "PASSED",
        "FAILED",
        "NO_VERIFIED_FACTS",
    }
