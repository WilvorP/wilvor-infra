"""Offline safety matrix for the frozen Master handoff.

These tests call the production contract functions. They do not call AWS,
a deployed specialist, or a model provider. MA5 and MA6 modules are absent
on this branch and are not imported.
"""

from __future__ import annotations

from dataclasses import fields, replace

import pytest

from tests.fixtures import ma7_master_safety_cases as cases
from tests.fixtures.ma7_master_safety_cases import (
    LIVE_CAPABILITY,
    REQUIRED_SCENARIO_IDS,
    RISK_CAPABILITY,
    SAFETY_CASES,
    USER_TEXT,
    MasterSafetyCase,
    SafetyClassification,
    another_master_request_inputs,
    application_time_mismatch_inputs,
    authorized_historical,
    authorized_live,
    california_comparison,
    consistent_historical_input,
    correlation_mismatch_inputs,
    decision_step,
    duplicate_comparison_inputs,
    empty_plan_bundle,
    five_live_steps,
    five_plan_steps,
    forged_answer_input,
    hazard_version_comparison,
    historical_subset_comparison,
    inconsistent_claim_input,
    live_and_historical_bundle,
    live_bundle,
    live_public_input,
    lower_bound_comparison,
    missing_historical_run_input,
    missing_risk_comparison,
    missing_sigmet_comparison,
    observation_time_comparison,
    oversized_comparison_requests,
    partial_network_comparison,
    pilot_instruction_comparison,
    rejected_live_input,
    same_aircraft_comparison,
    two_decision_plan_steps,
    unavailable_passed_input,
    unplanned_result_inputs,
    unresolved_geography_comparison,
    unresolved_historical_input,
    verified_zero_comparison,
    wrong_specialist_inputs,
)
from wilvor_ai.contracts import ContractValidationError, ToolResult
from wilvor_ai.live_ops_public_contracts import (
    LiveOpsEvidenceReference,
    LiveOpsPublicCountMode,
    LiveOpsPublicVerificationOutcome,
)
from wilvor_ai.master_contracts import MasterProductionAuthority, MasterSpecialist
from wilvor_ai.master_evidence_admission import (
    MasterAdmissionStatus,
    MasterBindingDefect,
    MasterSourceAuthenticity,
    admit_master_evidence,
)
from wilvor_ai.master_evidence_comparability import (
    MasterComparisonAssessment,
    assess_master_evidence_comparability,
)
from wilvor_ai.master_evidence_corroboration import (
    MasterCorroborationStatus,
    corroborate_master_evidence,
)
from wilvor_ai.master_parallel_contracts import (
    MasterExecutionPreflightReport,
    MasterExecutionPreflightState,
    MasterParallelContractBundle,
    MasterProvenanceRequirement,
    MasterResponseLimitation,
    MasterResponsePolicyHandoff,
    MasterResponsePolicyState,
    MasterSafetyHandoff,
    execution_preflight_for,
    historical_run_if_supplied,
    live_ops_public_sources,
    response_policy_for,
)
from wilvor_ai.master_routing import (
    MasterProposedCapability,
    MasterRequirement,
    MasterRouteExecutionAuthority,
    MasterRouteOutcome,
    MasterRoutePlan,
    MasterRouteProposal,
)
from wilvor_ai.specialist_contracts import (
    ExactCountClaim,
    LowerBoundCountClaim,
    VerifiedZeroClaim,
    VerifierOutcome,
)


EXERCISED: list[str] = []


def safety_case(scenario_id: str):
    def decorate(fn):
        EXERCISED.append(scenario_id)
        return fn

    return decorate


def _case(scenario_id: str) -> MasterSafetyCase:
    case = cases.case_by_id(scenario_id)
    assert case.scenario_id == scenario_id
    return case


def _error_code(func) -> str:
    with pytest.raises(ContractValidationError) as caught:
        func()
    assert len(caught.value.errors) == 1
    return caught.value.errors[0]


def _reason_values(items) -> set[str]:
    return {item.value for item in items}


def _assert_reasons(case: MasterSafetyCase, observed: set[str]) -> None:
    assert set(case.expected_reasons) <= observed


def _not_authenticated(result) -> None:
    assert result.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert result.evidence_verified is False
    assert result.production_authority is MasterProductionAuthority.DENIED
    assert result.end_to_end_fulfilled is False


def _comparison(case: MasterSafetyCase, assessment: MasterComparisonAssessment) -> None:
    assert case.classification is SafetyClassification.COMPARISON_NOT_AGREEMENT or (
        case.classification is SafetyClassification.RESPONSE_NONFACTUAL
    )
    assert assessment.eligibility.value == case.expected_outcome
    _assert_reasons(case, _reason_values(assessment.reason_codes))
    _not_authenticated(assessment)
    assert assessment.established_prerequisites == ()
    assert assessment.request_coverage.value == "NOT_ESTABLISHED"


def test_catalog_is_the_required_scenario_set():
    assert tuple(case.scenario_id for case in SAFETY_CASES) == REQUIRED_SCENARIO_IDS
    assert len(SAFETY_CASES) == len(set(REQUIRED_SCENARIO_IDS))
    for case in SAFETY_CASES:
        assert case.invariant.strip()
        assert case.native_input.strip()
        assert case.danger.strip()
        assert case.expected_outcome.strip()
        assert isinstance(case.classification, SafetyClassification)
        assert callable(getattr(cases, case.builder))


def test_every_catalog_case_has_an_exercise():
    assert tuple(EXERCISED) == REQUIRED_SCENARIO_IDS


@safety_case("evidence-from-another-master-request")
def test_evidence_from_another_master_request():
    case = _case("evidence-from-another-master-request")
    context, plan, evidence_item = another_master_request_inputs()
    assert evidence_item.admission_input.authorization.master_request_id != context.master_request_id
    assert _error_code(
        lambda: MasterParallelContractBundle(context, plan, (evidence_item,))
    ) == case.expected_outcome


@safety_case("duplicate-invocation-ids")
def test_duplicate_invocation_ids():
    case = _case("duplicate-invocation-ids")
    bundle = live_and_historical_bundle()
    duplicated = (bundle.evidence_inputs[0], bundle.evidence_inputs[0])
    assert _error_code(lambda: replace(bundle, evidence_inputs=duplicated)) == case.expected_outcome


@safety_case("unplanned-specialist-result")
def test_unplanned_specialist_result():
    case = _case("unplanned-specialist-result")
    bundle, evidence_item = unplanned_result_inputs()
    assert evidence_item.admission_input.authorization.invocation_id not in {
        step.invocation.invocation_id for step in bundle.plan.steps
    }
    assert _error_code(
        lambda: replace(bundle, evidence_inputs=(evidence_item,))
    ) == case.expected_outcome


@safety_case("wrong-specialist-for-invocation")
def test_wrong_specialist_for_invocation():
    case = _case("wrong-specialist-for-invocation")
    context, plan, evidence_item, admission = wrong_specialist_inputs()
    assert plan.steps[0].invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    assert _error_code(
        lambda: MasterParallelContractBundle(context, plan, (evidence_item,))
    ) == case.expected_outcome
    admitted = admit_master_evidence((admission,))
    assert admitted.admissions[0].admission_status is MasterAdmissionStatus.REJECTED
    _not_authenticated(admitted.admissions[0])
    _assert_reasons(case, _reason_values(admitted.admissions[0].reason_codes) | {"REJECTED"})


@safety_case("different-correlation-ids")
def test_different_correlation_ids():
    case = _case("different-correlation-ids")
    context, plan, evidence = correlation_mismatch_inputs()
    assert plan.steps[0].invocation.correlation_id != context.correlation_id
    assert _error_code(
        lambda: MasterParallelContractBundle(context, plan, evidence)
    ) == case.expected_outcome


@safety_case("different-application-request-timestamps")
def test_different_application_request_timestamps():
    case = _case("different-application-request-timestamps")
    context, plan, evidence = application_time_mismatch_inputs()
    assert plan.steps[0].invocation.application_request_time_utc != context.application_request_time_utc
    assert _error_code(
        lambda: MasterParallelContractBundle(context, plan, evidence)
    ) == case.expected_outcome


@safety_case("multiple-decision-invocations")
def test_multiple_decision_invocations():
    case = _case("multiple-decision-invocations")
    proposal_code = _error_code(
        lambda: MasterRouteProposal(
            user_text=USER_TEXT,
            capabilities=(MasterProposedCapability(RISK_CAPABILITY, MasterRequirement.REQUIRED),),
            steps=(decision_step("dec-ma7-1"), decision_step("dec-ma7-2")),
        )
    )
    context, steps = two_decision_plan_steps()
    plan_code = _error_code(
        lambda: MasterRoutePlan(
            master_request_id=context.master_request_id,
            canonical_user_text=context.canonical_user_text,
            steps=steps,
        )
    )
    assert proposal_code == plan_code == case.expected_outcome


@safety_case("excessive-route-steps")
def test_excessive_route_steps():
    case = _case("excessive-route-steps")
    proposal_code = _error_code(
        lambda: MasterRouteProposal(
            user_text=USER_TEXT,
            capabilities=(MasterProposedCapability(LIVE_CAPABILITY, MasterRequirement.REQUIRED),),
            steps=five_live_steps(),
        )
    )
    context, steps = five_plan_steps()
    plan_code = _error_code(
        lambda: MasterRoutePlan(
            master_request_id=context.master_request_id,
            canonical_user_text=context.canonical_user_text,
            steps=steps,
        )
    )
    assert proposal_code == plan_code == case.expected_outcome


@safety_case("historical-unavailable-with-passed-verifier")
def test_historical_unavailable_with_passed_verifier():
    case = _case("historical-unavailable-with-passed-verifier")
    item, artifact = unavailable_passed_input()
    assert artifact.status.value == "UNAVAILABLE"
    assert artifact.verifier_outcome is VerifierOutcome.PASSED
    assert artifact.tool_results == ()
    assert historical_run_if_supplied(item) is None
    admitted = admit_master_evidence((item.admission_input,))
    corroborated = corroborate_master_evidence((item,))
    result = corroborated.results[0]
    assert result.corroboration_status.value == case.expected_outcome
    assert admitted.admissions[0].admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
    _not_authenticated(result)
    _assert_reasons(
        case,
        _reason_values(result.reason_codes) | _reason_values(admitted.admissions[0].reason_codes),
    )


@safety_case("forged-historical-final-answer")
def test_forged_historical_final_answer():
    case = _case("forged-historical-final-answer")
    item = forged_answer_input()
    result = corroborate_master_evidence((item,)).results[0]
    assert result.corroboration_status.value == case.expected_outcome
    _not_authenticated(result)
    _assert_reasons(case, _reason_values(result.reason_codes))
    assert "99" in item.admission_input.artifact.answer


@safety_case("historical-lower-bound-is-not-exact")
def test_historical_lower_bound_is_not_exact():
    case = _case("historical-lower-bound-is-not-exact")
    request, left, right = lower_bound_comparison()
    assert any(isinstance(claim, ExactCountClaim) for claim in left.artifact.verified_claims)
    assert any(isinstance(claim, LowerBoundCountClaim) for claim in right.artifact.verified_claims)
    _comparison(case, assess_master_evidence_comparability(request, left, right))


@safety_case("historical-verified-zero-is-not-unknown")
def test_historical_verified_zero_is_not_unknown():
    case = _case("historical-verified-zero-is-not-unknown")
    request, left, right = verified_zero_comparison()
    assert any(isinstance(claim, VerifiedZeroClaim) for claim in left.artifact.verified_claims)
    assert right.artifact.result.verification_outcome is LiveOpsPublicVerificationOutcome.NONE_VERIFIED
    _comparison(case, assess_master_evidence_comparability(request, left, right))


@safety_case("missing-historical-original-run")
def test_missing_historical_original_run():
    case = _case("missing-historical-original-run")
    item = missing_historical_run_input()
    assert historical_run_if_supplied(item) is None
    assert item.admission_input.artifact.status.value != "UNAVAILABLE"
    result = corroborate_master_evidence((item,)).results[0]
    assert result.corroboration_status.value == case.expected_outcome
    _not_authenticated(result)
    _assert_reasons(case, _reason_values(result.reason_codes))


@safety_case("live-ops-public-evidence-without-tool-results")
def test_live_ops_public_evidence_without_tool_results():
    case = _case("live-ops-public-evidence-without-tool-results")
    item = live_public_input()
    sources = live_ops_public_sources(item.admission_input.artifact)
    assert sources is item.admission_input.artifact
    assert sources.result.evidence_references
    assert all(isinstance(ref, LiveOpsEvidenceReference) for ref in sources.result.evidence_references)
    assert not any(isinstance(ref, ToolResult) for ref in sources.result.evidence_references)
    admitted = admit_master_evidence((item.admission_input,))
    result = corroborate_master_evidence((item,)).results[0]
    assert result.corroboration_status.value == case.expected_outcome
    _not_authenticated(result)
    _assert_reasons(
        case,
        _reason_values(result.reason_codes) | _reason_values(admitted.admissions[0].reason_codes),
    )


@safety_case("rejected-native-admission")
def test_rejected_native_admission():
    case = _case("rejected-native-admission")
    admission = rejected_live_input()
    admitted = admit_master_evidence((admission,))
    result = admitted.admissions[0]
    assert result.admission_status.value == case.expected_outcome
    assert MasterBindingDefect.REQUEST_ID_MISMATCH in result.binding_defects
    corroborated = corroborate_master_evidence(
        (cases.MasterEvidenceCorroborationInput(admission),)
    ).results[0]
    assert corroborated.corroboration_status is MasterCorroborationStatus.ADMISSION_REJECTED
    _not_authenticated(result)
    _not_authenticated(corroborated)
    _assert_reasons(
        case,
        _reason_values(result.binding_defects)
        | _reason_values(corroborated.reason_codes)
        | {result.admission_status.value},
    )


@safety_case("unresolved-native-binding")
def test_unresolved_native_binding():
    case = _case("unresolved-native-binding")
    admission = unresolved_historical_input()
    result = admit_master_evidence((admission,)).admissions[0]
    assert result.admission_status.value == case.expected_outcome
    assert result.admission_status is not MasterAdmissionStatus.REJECTED
    _not_authenticated(result)
    _assert_reasons(case, _reason_values(result.reason_codes))


@safety_case("inconsistent-supplied-evidence")
def test_inconsistent_supplied_evidence():
    case = _case("inconsistent-supplied-evidence")
    item, finalized = inconsistent_claim_input()
    assert finalized.verified_claims
    assert item.admission_input.artifact.verified_claims == ()
    result = corroborate_master_evidence((item,)).results[0]
    assert result.corroboration_status.value == case.expected_outcome
    _not_authenticated(result)
    _assert_reasons(case, _reason_values(result.reason_codes))


@safety_case("same-aircraft-label-is-not-authenticated-identity")
def test_same_aircraft_label_is_not_authenticated_identity():
    case = _case("same-aircraft-label-is-not-authenticated-identity")
    request, left, right = same_aircraft_comparison()
    assert right.artifact.aircraft_id == "AC-1"
    assert any(claim.aircraft_id == "AC-1" for claim in left.artifact.result.verified_claims)
    _comparison(case, assess_master_evidence_comparability(request, left, right))


@safety_case("different-observation-timestamps")
def test_different_observation_timestamps():
    case = _case("different-observation-timestamps")
    request, left, right = observation_time_comparison()
    _comparison(case, assess_master_evidence_comparability(request, left, right))


@safety_case("same-hazard-different-source-versions")
def test_same_hazard_different_source_versions():
    case = _case("same-hazard-different-source-versions")
    request, left, right = hazard_version_comparison()
    _comparison(case, assess_master_evidence_comparability(request, left, right))


@safety_case("missing-sigmet-geometry")
def test_missing_sigmet_geometry():
    case = _case("missing-sigmet-geometry")
    request, left, right = missing_sigmet_comparison()
    _comparison(case, assess_master_evidence_comparability(request, left, right))


@safety_case("california-text-is-not-authenticated-geography")
def test_california_text_is_not_authenticated_geography():
    case = _case("california-text-is-not-authenticated-geography")
    request, left, right = california_comparison()
    assert request.region_text_hint == "California"
    _comparison(case, assess_master_evidence_comparability(request, left, right))


@safety_case("historical-count-is-not-live-ops-subset")
def test_historical_count_is_not_live_ops_subset():
    case = _case("historical-count-is-not-live-ops-subset")
    request, left, right = historical_subset_comparison()
    assert any(isinstance(claim, ExactCountClaim) for claim in left.artifact.verified_claims)
    assert any(
        claim.count_mode is LiveOpsPublicCountMode.CONFIRMED_SUBSET
        for claim in right.artifact.result.verified_claims
    )
    _comparison(case, assess_master_evidence_comparability(request, left, right))


@safety_case("missing-risk-is-not-low")
def test_missing_risk_is_not_low():
    case = _case("missing-risk-is-not-low")
    request, left, right, corroboration = missing_risk_comparison()
    assessment = assess_master_evidence_comparability(request, left, right)
    _comparison(case, assessment)
    corroborated = corroborate_master_evidence((corroboration,)).results[0]
    _not_authenticated(corroborated)
    assert "RECORDED_RISK_ABSENCE_NOT_LOW" in _reason_values(corroborated.reason_codes)
    assert corroborated.operationally_eligible is False


@safety_case("partial-count-is-not-network-conclusion")
def test_partial_count_is_not_network_conclusion():
    case = _case("partial-count-is-not-network-conclusion")
    request, left, right = partial_network_comparison()
    assert left.artifact.result.verification_outcome is LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED
    assert right.artifact.result.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED
    _comparison(case, assess_master_evidence_comparability(request, left, right))


@safety_case("excessive-comparison-requests")
def test_excessive_comparison_requests():
    case = _case("excessive-comparison-requests")
    bundle, requests = oversized_comparison_requests()
    assert len(requests) == 13
    assert _error_code(
        lambda: replace(bundle, comparison_requests=requests)
    ) == case.expected_outcome


@safety_case("duplicate-comparison-requests")
def test_duplicate_comparison_requests():
    case = _case("duplicate-comparison-requests")
    bundle, requests = duplicate_comparison_inputs()
    assert requests[0] == requests[1]
    assert _error_code(
        lambda: replace(bundle, comparison_requests=requests)
    ) == case.expected_outcome


@safety_case("offline-handoff-cannot-emit-a-factual-response")
def test_offline_handoff_cannot_emit_a_factual_response():
    case = _case("offline-handoff-cannot-emit-a-factual-response")
    bundle = live_bundle()
    policy = response_policy_for(bundle, MasterResponsePolicyState.INSUFFICIENT_EVIDENCE)
    preflight = execution_preflight_for(bundle)
    handoff = MasterSafetyHandoff(
        workflow=bundle,
        response_policy=policy,
        execution_preflight=preflight,
    )
    assert MasterResponseLimitation.NO_OPERATIONAL_CLAIM.value in {
        item.value for item in policy.limitations
    }
    assert case.expected_outcome in {item.value for item in policy.limitations}
    assert policy.claims_admitted is False
    assert "answer" not in {item.name for item in fields(MasterResponsePolicyHandoff)}
    assert "instruction" not in {item.name for item in fields(MasterSafetyHandoff)}
    assert handoff.execution_preflight.executable is False
    assert policy.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert policy.production_authority is MasterProductionAuthority.DENIED
    assert policy.end_to_end_fulfilled is False
    assert _error_code(lambda: replace(policy, claims_admitted=True)) == case.expected_reasons[0]


@safety_case("fixed-status-text-cannot-carry-an-operational-claim")
def test_fixed_status_text_cannot_carry_an_operational_claim():
    case = _case("fixed-status-text-cannot-carry-an-operational-claim")
    policy = response_policy_for(live_bundle(), MasterResponsePolicyState.UNSUPPORTED)
    assert _error_code(
        lambda: replace(policy, status_text="Aircraft AC-1 is clear of hazards.")
    ) == case.expected_outcome


@safety_case("unavailable-count-is-not-zero")
def test_unavailable_count_is_not_zero():
    case = _case("unavailable-count-is-not-zero")
    policy = response_policy_for(live_bundle(), MasterResponsePolicyState.UNAVAILABLE)
    assert policy.status_text == case.expected_outcome
    assert policy.claims_admitted is False
    item, artifact = unavailable_passed_input()
    assert not any(isinstance(claim, VerifiedZeroClaim) for claim in artifact.verified_claims)
    admitted = admit_master_evidence((item.admission_input,)).admissions[0]
    _not_authenticated(admitted)
    _assert_reasons(case, _reason_values(admitted.reason_codes))


@safety_case("consistent-ma4-result-is-not-authenticated-evidence")
def test_consistent_ma4_result_is_not_authenticated_evidence():
    case = _case("consistent-ma4-result-is-not-authenticated-evidence")
    item = consistent_historical_input()
    result = corroborate_master_evidence((item,)).results[0]
    assert result.corroboration_status.value == case.expected_outcome
    assert result.operationally_eligible is False
    _not_authenticated(result)
    _assert_reasons(case, _reason_values(result.reason_codes))


@safety_case("unresolved-geography-is-not-a-confirmed-intersection")
def test_unresolved_geography_is_not_a_confirmed_intersection():
    case = _case("unresolved-geography-is-not-a-confirmed-intersection")
    request, left, right = unresolved_geography_comparison()
    assessment = assess_master_evidence_comparability(request, left, right)
    _comparison(case, assessment)
    assert "intersection" not in {item.name for item in fields(MasterComparisonAssessment)}


@safety_case("response-handoff-cannot-create-a-pilot-or-atc-instruction")
def test_response_handoff_cannot_create_a_pilot_or_atc_instruction():
    case = _case("response-handoff-cannot-create-a-pilot-or-atc-instruction")
    request, left, right = pilot_instruction_comparison()
    _comparison(case, assess_master_evidence_comparability(request, left, right))
    policy = response_policy_for(live_bundle(), MasterResponsePolicyState.UNSUPPORTED)
    assert _error_code(
        lambda: replace(policy, status_text="Turn left heading 270. Cleared to descend.")
    ) == "invalid_status_text"


@safety_case("route-does-not-grant-production-execution")
def test_route_does_not_grant_production_execution():
    case = _case("route-does-not-grant-production-execution")
    context, plan, decision = authorized_live()
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.execution_authority.value == case.expected_outcome
    assert plan.execution_authority.value == case.expected_outcome
    assert set(MasterRouteExecutionAuthority) == {MasterRouteExecutionAuthority.DENIED}
    bundle = MasterParallelContractBundle(context, plan, cases.matching_evidence(plan))
    report = execution_preflight_for(bundle)
    assert report.executable is False
    assert report.state.value == case.expected_reasons[0]


@safety_case("preflight-does-not-permit-dispatch")
def test_preflight_does_not_permit_dispatch():
    case = _case("preflight-does-not-permit-dispatch")
    report = execution_preflight_for(live_bundle())
    assert report.executable is False
    assert report.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert "dispatch" not in {item.name for item in fields(MasterExecutionPreflightReport)}
    assert _error_code(lambda: replace(report, executable=True)) == case.expected_outcome


@safety_case("provenance-requirement-cannot-be-marked-satisfied")
def test_provenance_requirement_cannot_be_marked_satisfied():
    case = _case("provenance-requirement-cannot-be-marked-satisfied")
    report = execution_preflight_for(live_bundle())
    assert report.missing_provenance == tuple(MasterProvenanceRequirement)
    assert _error_code(
        lambda: replace(report, missing_provenance=report.missing_provenance[:-1])
    ) == case.expected_outcome


@safety_case("empty-plan-is-not-executable")
def test_empty_plan_is_not_executable():
    case = _case("empty-plan-is-not-executable")
    bundle = empty_plan_bundle()
    assert bundle.plan.steps == ()
    report = execution_preflight_for(bundle)
    assert report.state.value == case.expected_outcome
    assert report.executable is False
    assert report.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert report.missing_provenance == tuple(MasterProvenanceRequirement)


@safety_case("contract-ready-remains-non-executable")
def test_contract_ready_remains_non_executable():
    case = _case("contract-ready-remains-non-executable")
    bundle = live_bundle()
    ready = MasterExecutionPreflightReport(
        workflow=bundle,
        state=MasterExecutionPreflightState.CONTRACT_READY_FOR_FUTURE_INTEGRATION,
        missing_provenance=tuple(MasterProvenanceRequirement),
    )
    assert ready.state.value == case.expected_outcome
    assert ready.executable is False
    assert ready.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert ready.missing_provenance == tuple(MasterProvenanceRequirement)
    assert execution_preflight_for(bundle).state is MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION


@safety_case("synthetic-ma3-result-is-not-authentic-evidence")
def test_synthetic_ma3_result_is_not_authentic_evidence():
    case = _case("synthetic-ma3-result-is-not-authentic-evidence")
    from wilvor_ai.master_orchestration import SyntheticOutcome, SyntheticWorkerResult

    context, plan, _decision = authorized_historical()
    authorization = plan.steps[0].invocation
    synthetic = SyntheticWorkerResult(
        master_request_id=context.master_request_id,
        correlation_id=context.correlation_id,
        invocation_id=authorization.invocation_id,
        specialist_request_id=authorization.specialist_request_id or "specialist-synthetic",
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        outcome=SyntheticOutcome.NO_RESULT,
        note="synthetic-not-evidence",
    )
    assert synthetic.evidence_verified is False
    item = cases.MasterEvidenceCorroborationInput(
        cases.MasterEvidenceAdmissionInput(authorization, synthetic)
    )
    assert _error_code(
        lambda: MasterParallelContractBundle(context, plan, (item,))
    ) == case.expected_outcome
    admitted = admit_master_evidence((item.admission_input,)).admissions[0]
    assert admitted.admission_status is MasterAdmissionStatus.REJECTED
    _not_authenticated(admitted)
    _assert_reasons(case, _reason_values(admitted.reason_codes) | {admitted.admission_status.value})
