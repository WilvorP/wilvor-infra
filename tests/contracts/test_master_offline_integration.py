"""Offline integration of the merged MA5, MA6, and MA7 contracts.

These tests pass one Master bundle through response policy, rendering,
execution preflight, adapter description, and the shared safety handoff.
A passing test is a closed denial or a contract rejection. It is not
operational validation, source authentication, or permission to dispatch.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tests.fixtures.live_ops_public_examples import ledger_not_authorized, unsupported_request
from tests.fixtures.ma7_master_safety_cases import (
    USER_TEXT,
    another_master_request_inputs,
    authorized,
    decision_offline_consistent_run,
    decision_step,
    historical_exact_count_run,
    history_step,
    live_ops_all_verified_result,
    live_ops_context,
    live_ops_request,
    live_step,
    planning_context,
    unplanned_result_inputs,
    wrong_specialist_inputs,
)
from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.historical_answer_renderer import finalize_historical_specialist_run
from wilvor_ai.live_ops_public_contracts import LiveOpsRunStatus
from wilvor_ai.master_contracts import MasterProductionAuthority, MasterRequirement, MasterSpecialist
from wilvor_ai.master_evidence_admission import (
    LiveOpsAdmissionSources,
    MasterAdmissionReason,
    MasterAdmissionStatus,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
    admit_master_evidence,
)
from wilvor_ai.master_evidence_comparability import (
    MasterComparisonCategory,
    MasterComparisonEligibility,
    MasterComparisonReason,
    MasterComparisonRequest,
    assess_master_evidence_comparability,
)
from wilvor_ai.master_evidence_corroboration import (
    MasterCorroborationReason,
    MasterCorroborationStatus,
    MasterEvidenceCorroborationInput,
    corroborate_master_evidence,
)
from wilvor_ai.master_execution_preflight import evaluate_execution_preflight
from wilvor_ai.master_parallel_contracts import (
    MasterExecutionPreflightState,
    MasterParallelContractBundle,
    MasterProvenanceRequirement,
    MasterResponseLimitation,
    MasterResponsePolicyState,
    MasterSafetyHandoff,
    historical_run_if_supplied,
)
from wilvor_ai.master_response_policy import evaluate_master_response_policy
from wilvor_ai.master_response_renderer import render_master_response
from wilvor_ai.master_routing import (
    MasterProposedCapability,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteOutcome,
    MasterRouteProposal,
    authorize_route,
)
from wilvor_ai.master_specialist_adapters import (
    MasterAdapterExecutionState,
    MasterSpecialistAdapterSet,
    accept_planned_adapters,
    describe_planned_adapters,
    refuse_adapter_evidence,
)
from wilvor_ai.specialist_contracts import SpecialistResult


APPROVED_STATUS_TEXT = frozenset(
    {
        "Response policy has not been evaluated.",
        "The request is unsupported. No operational conclusion is available.",
        "Required evidence is unavailable. No count or zero is inferred.",
        "Supplied evidence is not sufficient for an operational claim.",
    }
)
FACT_TOKENS = ("sigmet", "aircraft", "hazard", "risk", "heading", "runway", "route")
REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
PRODUCTION_MODULES = (
    SHARED_DIR / "wilvor_ai" / "master_response_policy.py",
    SHARED_DIR / "wilvor_ai" / "master_response_renderer.py",
    SHARED_DIR / "wilvor_ai" / "master_execution_preflight.py",
    SHARED_DIR / "wilvor_ai" / "master_specialist_adapters.py",
)
FORBIDDEN_MODULES = {
    "aiohttp",
    "anthropic",
    "boto3",
    "botocore",
    "httpx",
    "langgraph",
    "openai",
    "requests",
    "urllib3",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.master_orchestration",
    "wilvor_ai.providers.anthropic_decision_messages",
    "wilvor_ai.providers.anthropic_messages",
    "wilvor_ai.providers.bedrock_converse",
    "wilvor_operational",
}


def _required_plan(*steps):
    context, plan, decision = authorized(*steps)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    return context, plan


def _optional_historical_plan():
    steps = (
        live_step(),
        replace(history_step(), requirement=MasterRequirement.OPTIONAL),
    )
    context = planning_context()
    capabilities = tuple(
        dict.fromkeys(
            MasterProposedCapability(step.capability, step.requirement) for step in steps
        )
    )
    decision = authorize_route(
        MasterRouteProposal(user_text=USER_TEXT, capabilities=capabilities, steps=steps),
        context,
    )
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED
    return context, decision.plan


def _bundle(context, plan, evidence=(), comparisons=()):
    return MasterParallelContractBundle(
        context=context,
        plan=plan,
        evidence_inputs=tuple(evidence),
        comparison_requests=tuple(comparisons),
    )


def _step_for(plan, specialist: MasterSpecialist):
    return next(step for step in plan.steps if step.invocation.specialist is specialist)


def _planned_ids(plan) -> tuple[str, ...]:
    return tuple(step.invocation.invocation_id for step in plan.steps)


def _live_input(authorization, result) -> MasterEvidenceCorroborationInput:
    request_id = authorization.specialist_request_id
    assert isinstance(request_id, str)
    request = replace(live_ops_request(), request_id=request_id)
    context = replace(live_ops_context(), correlation_id=authorization.correlation_id)
    aligned = replace(
        result,
        request_id=request_id,
        correlation_id=authorization.correlation_id,
    )
    return MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(
            authorization,
            LiveOpsAdmissionSources(request, context, aligned),
        )
    )


def _historical_input(authorization, *, with_run: bool) -> MasterEvidenceCorroborationInput:
    run = historical_exact_count_run()
    artifact = finalize_historical_specialist_run(run)
    return MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(authorization, artifact),
        historical_run=run if with_run else None,
    )


def _assert_denial(obj) -> None:
    assert obj.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert obj.production_authority is MasterProductionAuthority.DENIED
    assert obj.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert obj.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert obj.end_to_end_fulfilled is False


def _assert_preflight_closed(bundle) -> None:
    evaluation = evaluate_execution_preflight(bundle)
    assert evaluation.bundle is bundle
    assert evaluation.report.workflow is bundle
    assert evaluation.dispatch_permitted is False
    assert evaluation.stops_before_execution_authorization is True
    assert evaluation.report.executable is False
    assert evaluation.report.state is MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION
    assert evaluation.report.missing_provenance == tuple(MasterProvenanceRequirement)
    _assert_denial(evaluation.report)
    return evaluation


def _assert_nonfactual(policy):
    rendered = render_master_response(policy)
    assert policy.claims_admitted is False
    assert rendered.claims_admitted is False
    assert rendered.state is policy.state
    assert rendered.status_text == policy.status_text
    assert rendered.status_text in APPROVED_STATUS_TEXT
    assert rendered.limitation_codes == tuple(
        item.value for item in MasterResponseLimitation if item in policy.limitations
    )
    lowered = rendered.status_text.casefold()
    for token in FACT_TOKENS:
        assert token not in lowered
    _assert_denial(policy)
    return rendered


def test_a_same_bundle_keeps_one_master_request():
    context, plan = _required_plan(live_step(), history_step())
    bundle = _bundle(context, plan)
    planned_ids = _planned_ids(plan)
    assert len(planned_ids) >= 2
    assert len(set(planned_ids)) == len(planned_ids)

    policy = evaluate_master_response_policy(bundle)
    evaluation = _assert_preflight_closed(bundle)
    adapters = describe_planned_adapters(bundle)
    handoff = MasterSafetyHandoff(
        workflow=bundle,
        response_policy=policy,
        execution_preflight=evaluation.report,
    )

    assert policy.workflow is bundle
    assert adapters.bundle is bundle
    assert handoff.workflow is bundle
    assert handoff.response_policy is policy
    assert handoff.execution_preflight is evaluation.report
    assert policy.master_request_id == context.master_request_id == plan.master_request_id
    assert policy.invocation_ids == planned_ids
    assert tuple(item.invocation_id for item in adapters.descriptors) == planned_ids
    assert tuple(item.authorization.invocation_id for item in bundle.bindings()) == planned_ids
    for descriptor, step in zip(adapters.descriptors, plan.steps, strict=True):
        assert descriptor.master_request_id == context.master_request_id
        assert descriptor.step_id == step.step_id
        assert descriptor.invocation_id == step.invocation.invocation_id
        assert descriptor.specialist is step.invocation.specialist
        assert descriptor.purpose is step.invocation.purpose
        assert descriptor.requirement is step.invocation.requirement
        assert descriptor.specialist_request_id == step.invocation.specialist_request_id
        assert (
            descriptor.execution_state
            is MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
        )

    copied = _bundle(context, plan)
    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        MasterSafetyHandoff(
            workflow=bundle,
            response_policy=policy,
            execution_preflight=evaluate_execution_preflight(copied).report,
        )


def _assert_missing_required_specialist(result, native_status: LiveOpsRunStatus) -> None:
    context, plan = _required_plan(live_step(), history_step())
    live = _step_for(plan, MasterSpecialist.LIVE_OPS)
    historical = _step_for(plan, MasterSpecialist.HISTORICAL_ANALYTICS)
    assert live.invocation.requirement is MasterRequirement.REQUIRED
    assert historical.invocation.requirement is MasterRequirement.REQUIRED
    evidence = (_live_input(live.invocation, result),)
    assert evidence[0].admission_input.artifact.result.execution_status is native_status
    bundle = _bundle(context, plan, evidence)
    represented = {item.admission_input.authorization.invocation_id for item in bundle.evidence_inputs}
    assert represented == {live.invocation.invocation_id}
    assert historical.invocation.invocation_id not in represented
    assert all(item.historical_run is None for item in bundle.evidence_inputs)
    assert not any(isinstance(item.admission_input.artifact, SpecialistResult) for item in bundle.evidence_inputs)

    checked = corroborate_master_evidence(evidence).results[0]
    assert checked.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert checked.corroboration_status is not MasterCorroborationStatus.INCONSISTENT
    assert checked.operationally_eligible is False
    assert checked.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED

    policy = evaluate_master_response_policy(bundle)
    assert bundle.evidence_inputs == evidence
    assert policy.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert policy.status_text == "Supplied evidence is not sufficient for an operational claim."
    _assert_nonfactual(policy)
    described = describe_planned_adapters(bundle)
    assert tuple(item.invocation_id for item in described.descriptors) == _planned_ids(plan)
    assert all(
        item.execution_state is MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
        for item in described.descriptors
    )
    _assert_preflight_closed(bundle)


def test_b_missing_required_historical_blocks_unavailable():
    _assert_missing_required_specialist(ledger_not_authorized(), LiveOpsRunStatus.UNAVAILABLE)


def test_b_missing_required_historical_blocks_unsupported():
    _assert_missing_required_specialist(unsupported_request(), LiveOpsRunStatus.UNSUPPORTED)


def test_c_complete_required_coverage_is_not_authenticated():
    context, plan = _required_plan(live_step(), history_step())
    live = _step_for(plan, MasterSpecialist.LIVE_OPS)
    historical = _step_for(plan, MasterSpecialist.HISTORICAL_ANALYTICS)
    evidence = (
        _live_input(live.invocation, live_ops_all_verified_result()),
        _historical_input(historical.invocation, with_run=True),
    )
    required = {
        step.invocation.invocation_id
        for step in plan.steps
        if step.invocation.requirement is MasterRequirement.REQUIRED
    }
    assert required == {item.admission_input.authorization.invocation_id for item in evidence}
    bundle = _bundle(context, plan, evidence)

    admitted = admit_master_evidence(tuple(item.admission_input for item in evidence)).admissions
    corroborated = corroborate_master_evidence(evidence).results
    by_specialist = {item.specialist: item for item in corroborated}
    live_check = by_specialist[MasterSpecialist.LIVE_OPS]
    historical_check = by_specialist[MasterSpecialist.HISTORICAL_ANALYTICS]
    live_admission = next(item for item in admitted if item.specialist is MasterSpecialist.LIVE_OPS)
    historical_admission = next(
        item for item in admitted if item.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    )

    assert live_admission.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert live_check.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert live_check.corroboration_status is MasterCorroborationStatus.NOT_RECHECKABLE
    assert MasterAdmissionReason.LIVE_OPS_ALL_VERIFIED_NOT_SOURCE_AUTHENTICATION in live_admission.reason_codes
    assert MasterAdmissionReason.LIVE_OPS_EVIDENCE_REFS_ARE_NOT_TOOL_RESULTS in live_admission.reason_codes
    assert MasterCorroborationReason.LIVE_OPS_NOT_RECHECKABLE in live_check.reason_codes
    assert historical_admission.admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
    assert historical_check.admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
    assert historical_check.corroboration_status is MasterCorroborationStatus.CONSISTENT_WITH_SUPPLIED_EVIDENCE
    assert MasterAdmissionReason.NATIVE_CORRELATION_ABSENT in historical_admission.reason_codes
    assert MasterAdmissionReason.NATIVE_SPECIALIST_REQUEST_ID_ABSENT in historical_admission.reason_codes
    assert MasterAdmissionReason.HISTORICAL_VERIFIER_NOT_SOURCE_AUTHENTICATION in historical_admission.reason_codes
    assert MasterCorroborationReason.ADMISSION_BINDING_UNRESOLVED in historical_check.reason_codes
    assert MasterCorroborationReason.SUPPLIED_EVIDENCE_ONLY in historical_check.reason_codes
    for item in admitted:
        assert item.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
        assert item.evidence_verified is False
        assert item.production_authority is MasterProductionAuthority.DENIED
    for item in corroborated:
        assert item.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
        assert item.evidence_verified is False
        assert item.production_authority is MasterProductionAuthority.DENIED
        assert item.operationally_eligible is False

    policy = evaluate_master_response_policy(bundle)
    rendered = _assert_nonfactual(policy)
    historical_answer = evidence[1].admission_input.artifact.answer
    live_answer = evidence[0].admission_input.artifact.result.answer
    assert policy.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert historical_answer not in rendered.status_text
    assert live_answer not in rendered.status_text
    assert MasterResponseLimitation.SUPPLIED_EVIDENCE_NOT_AUTHENTICATED in policy.limitations
    _assert_preflight_closed(bundle)


def test_d_optional_historical_omission_does_not_authorize_execution():
    context, plan = _optional_historical_plan()
    live = _step_for(plan, MasterSpecialist.LIVE_OPS)
    historical = _step_for(plan, MasterSpecialist.HISTORICAL_ANALYTICS)
    assert live.invocation.requirement is MasterRequirement.REQUIRED
    assert historical.invocation.requirement is MasterRequirement.OPTIONAL
    evidence = (_live_input(live.invocation, ledger_not_authorized()),)
    bundle = _bundle(context, plan, evidence)
    policy = evaluate_master_response_policy(bundle)
    assert historical.invocation.invocation_id not in {
        item.admission_input.authorization.invocation_id for item in bundle.evidence_inputs
    }
    assert policy.state is MasterResponsePolicyState.UNAVAILABLE
    assert policy.status_text == "Required evidence is unavailable. No count or zero is inferred."
    _assert_denial(policy)
    _assert_denial(bundle)

    described = describe_planned_adapters(bundle)
    assert tuple(item.invocation_id for item in described.descriptors) == _planned_ids(plan)
    live_descriptor = next(item for item in described.descriptors if item.invocation_id == live.invocation.invocation_id)
    partial = accept_planned_adapters(bundle, (live_descriptor,))
    assert tuple(item.invocation_id for item in partial.descriptors) == (live.invocation.invocation_id,)
    assert historical.invocation.invocation_id not in {item.invocation_id for item in partial.descriptors}
    assert all(
        item.execution_state is MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
        for item in partial.descriptors
    )
    assert "dispatch_permitted" not in MasterSpecialistAdapterSet.__dataclass_fields__
    assert historical.invocation.invocation_id in _planned_ids(plan)
    _assert_preflight_closed(bundle)


def test_e_adapter_subset_does_not_complete_execution():
    context, plan = _required_plan(live_step(), history_step(), decision_step())
    bundle = _bundle(context, plan)
    planned_ids = _planned_ids(plan)
    assert len(planned_ids) == 3
    assert all(step.invocation.requirement is MasterRequirement.REQUIRED for step in plan.steps)
    full = describe_planned_adapters(bundle)
    assert tuple(item.invocation_id for item in full.descriptors) == planned_ids

    subset = accept_planned_adapters(bundle, (full.descriptors[0],))
    assert len(subset.descriptors) == 1
    assert subset.descriptors[0] == full.descriptors[0]
    assert subset.descriptors[0].invocation_id in planned_ids
    assert set(item.invocation_id for item in subset.descriptors) < set(planned_ids)
    assert (
        subset.descriptors[0].execution_state
        is MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
    )
    assert subset.descriptors[0].source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert subset.descriptors[0].production_authority is MasterProductionAuthority.DENIED
    assert subset.descriptors[0].execution_authority is MasterRouteExecutionAuthority.DENIED
    assert "executable" not in MasterSpecialistAdapterSet.__dataclass_fields__
    assert _planned_ids(bundle.plan) == planned_ids
    _assert_denial(bundle)
    _assert_preflight_closed(bundle)

    invented = replace(full.descriptors[0], invocation_id="inv-ma6-subset-extra")
    with pytest.raises(ContractValidationError, match="invocation_not_in_plan"):
        accept_planned_adapters(bundle, (invented,))
    assert _planned_ids(bundle.plan) == planned_ids


def test_f_other_master_request_is_rejected_by_the_bundle():
    context, plan, foreign = another_master_request_inputs()
    assert foreign.admission_input.authorization.master_request_id != context.master_request_id
    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        _bundle(context, plan, (foreign,))
    assert plan.master_request_id == context.master_request_id


def test_f_unplanned_invocation_cannot_join_the_bundle():
    bundle, foreign = unplanned_result_inputs()
    original_ids = _planned_ids(bundle.plan)
    assert foreign.admission_input.authorization.invocation_id not in original_ids
    with pytest.raises(ContractValidationError, match="invocation_not_in_plan"):
        replace(bundle, evidence_inputs=(foreign,))
    assert _planned_ids(bundle.plan) == original_ids
    policy = evaluate_master_response_policy(bundle)
    assert policy.invocation_ids == original_ids
    assert foreign.admission_input.authorization.invocation_id not in policy.invocation_ids


def test_f_mismatched_specialist_artifact_is_rejected():
    context, plan, foreign, admission = wrong_specialist_inputs()
    assert plan.steps[0].invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    assert isinstance(admission.artifact, LiveOpsAdmissionSources)
    with pytest.raises(ContractValidationError, match="unsupported_artifact"):
        _bundle(context, plan, (foreign,))
    admitted = admit_master_evidence((admission,)).admissions[0]
    assert admitted.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.SPECIALIST_ARTIFACT_MISMATCH in admitted.reason_codes
    assert admitted.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert admitted.evidence_verified is False


def test_f_unresolved_historical_binding_stays_nonfactual():
    context, plan = _required_plan(history_step())
    authorization = plan.steps[0].invocation
    evidence = (_historical_input(authorization, with_run=True),)
    bundle = _bundle(context, plan, evidence)
    admitted = admit_master_evidence((evidence[0].admission_input,)).admissions[0]
    checked = corroborate_master_evidence(evidence).results[0]
    assert admitted.admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
    assert checked.admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
    assert MasterAdmissionReason.NATIVE_CORRELATION_ABSENT in admitted.reason_codes
    assert MasterAdmissionReason.NATIVE_SPECIALIST_REQUEST_ID_ABSENT in admitted.reason_codes
    assert checked.operationally_eligible is False
    assert checked.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    policy = evaluate_master_response_policy(bundle)
    assert policy.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    _assert_nonfactual(policy)
    _assert_preflight_closed(bundle)


def test_f_unresolved_decision_binding_stays_nonfactual():
    context, plan = _required_plan(decision_step())
    authorization = plan.steps[0].invocation
    assert authorization.expected_aircraft_id is None
    evidence = (
        MasterEvidenceCorroborationInput(
            MasterEvidenceAdmissionInput(authorization, decision_offline_consistent_run())
        ),
    )
    bundle = _bundle(context, plan, evidence)
    admitted = admit_master_evidence((evidence[0].admission_input,)).admissions[0]
    checked = corroborate_master_evidence(evidence).results[0]
    assert admitted.admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
    assert checked.admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
    assert MasterAdmissionReason.DECISION_TARGET_CANDIDATE_ABSENT in admitted.reason_codes
    assert checked.operationally_eligible is False
    assert checked.evidence_verified is False
    policy = evaluate_master_response_policy(bundle)
    assert policy.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    _assert_nonfactual(policy)
    _assert_preflight_closed(bundle)


def test_f_missing_historical_run_is_not_fabricated():
    context, plan = _required_plan(history_step())
    evidence = (_historical_input(plan.steps[0].invocation, with_run=False),)
    assert evidence[0].historical_run is None
    assert historical_run_if_supplied(evidence[0]) is None
    bundle = _bundle(context, plan, evidence)
    checked = corroborate_master_evidence(evidence).results[0]
    assert checked.corroboration_status is MasterCorroborationStatus.NOT_RECHECKABLE
    assert MasterCorroborationReason.HISTORICAL_RUN_ABSENT in checked.reason_codes
    assert checked.operationally_eligible is False
    policy = evaluate_master_response_policy(bundle)
    assert policy.workflow.evidence_inputs[0].historical_run is None
    assert MasterResponseLimitation.HISTORICAL_RUN_NOT_FABRICATED in policy.limitations
    assert policy.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    _assert_nonfactual(policy)


def test_f_incompatible_comparison_is_not_operational_agreement():
    context, plan = _required_plan(live_step(), history_step())
    live = _step_for(plan, MasterSpecialist.LIVE_OPS)
    historical = _step_for(plan, MasterSpecialist.HISTORICAL_ANALYTICS)
    live_evidence = _live_input(live.invocation, live_ops_all_verified_result())
    historical_evidence = _historical_input(historical.invocation, with_run=True)
    request = MasterComparisonRequest(
        context.master_request_id,
        live.invocation.invocation_id,
        historical.invocation.invocation_id,
        MasterComparisonCategory.CURRENT_SIGMET_VS_HISTORICAL_HAZARD_VERSIONS,
    )
    assessment = assess_master_evidence_comparability(
        request,
        live_evidence.admission_input,
        historical_evidence.admission_input,
    )
    assert assessment.eligibility is MasterComparisonEligibility.UNSUPPORTED
    assert assessment.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert assessment.evidence_verified is False
    assert assessment.production_authority is MasterProductionAuthority.DENIED
    assert assessment.established_prerequisites == ()
    assert MasterComparisonReason.SIGMET_GEOMETRY_NOT_PRESENT in assessment.reason_codes
    assert MasterComparisonReason.SIGMET_EXPOSURE_IS_NOT_RISK_CLASSIFICATION in assessment.reason_codes
    assert MasterComparisonReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED in assessment.reason_codes

    bundle = _bundle(context, plan, (live_evidence, historical_evidence), (request,))
    policy = evaluate_master_response_policy(bundle)
    rendered = _assert_nonfactual(policy)
    assert policy.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert policy.state is not MasterResponsePolicyState.UNSUPPORTED
    assert MasterResponseLimitation.COMPARABILITY_NOT_OPERATIONAL_AGREEMENT in policy.limitations
    assert "SIGMET" not in rendered.status_text
    checked = corroborate_master_evidence(bundle.evidence_inputs).results
    assert any(item.admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED for item in checked)


def test_f_missing_comparison_input_is_not_assessed(monkeypatch):
    def _forbidden(*_args, **_kwargs):
        raise AssertionError("comparability must not run without both evidence inputs")

    monkeypatch.setattr(
        "wilvor_ai.master_response_policy.assess_master_evidence_comparability",
        _forbidden,
    )
    context, plan = _optional_historical_plan()
    live = _step_for(plan, MasterSpecialist.LIVE_OPS)
    historical = _step_for(plan, MasterSpecialist.HISTORICAL_ANALYTICS)
    evidence = (_live_input(live.invocation, ledger_not_authorized()),)
    request = MasterComparisonRequest(
        context.master_request_id,
        live.invocation.invocation_id,
        historical.invocation.invocation_id,
        MasterComparisonCategory.CURRENT_SIGMET_VS_HISTORICAL_HAZARD_VERSIONS,
    )
    bundle = _bundle(context, plan, evidence, (request,))
    policy = evaluate_master_response_policy(bundle)
    assert historical.invocation.invocation_id not in {
        item.admission_input.authorization.invocation_id for item in bundle.evidence_inputs
    }
    assert policy.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert policy.state is not MasterResponsePolicyState.UNAVAILABLE
    assert MasterResponseLimitation.COMPARABILITY_NOT_OPERATIONAL_AGREEMENT in policy.limitations
    _assert_nonfactual(policy)


def test_g_renderer_and_preflight_stay_nonfactual():
    context, plan = _required_plan(live_step(), history_step())
    bundle = _bundle(context, plan)
    policy = evaluate_master_response_policy(bundle)
    rendered = _assert_nonfactual(policy)
    evaluation = _assert_preflight_closed(bundle)
    adapters = describe_planned_adapters(bundle)
    handoff = MasterSafetyHandoff(
        workflow=bundle,
        response_policy=policy,
        execution_preflight=evaluation.report,
    )
    assert rendered.claims_admitted is False
    assert policy.claims_admitted is False
    assert handoff.response_policy.claims_admitted is False
    assert evaluation.report.executable is False
    assert evaluation.dispatch_permitted is False
    assert evaluation.report.missing_provenance == tuple(MasterProvenanceRequirement)
    assert adapters.descriptors
    assert all(
        item.execution_state is MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
        for item in adapters.descriptors
    )
    _assert_denial(handoff.workflow)
    _assert_denial(handoff.response_policy)
    _assert_denial(handoff.execution_preflight)


def test_h_offline_workflow_is_deterministic():
    def _evaluate(bundle):
        policy = evaluate_master_response_policy(bundle)
        rendered = render_master_response(policy)
        evaluation = evaluate_execution_preflight(bundle)
        adapters = describe_planned_adapters(bundle)
        handoff = MasterSafetyHandoff(
            workflow=bundle,
            response_policy=policy,
            execution_preflight=evaluation.report,
        )
        return policy, rendered, evaluation, adapters, handoff

    context, plan = _required_plan(live_step(), history_step())
    bundle = _bundle(context, plan)
    first = _evaluate(bundle)
    second = _evaluate(bundle)
    assert first == second

    rebuilt = _bundle(*_required_plan(live_step(), history_step()))
    third = _evaluate(rebuilt)
    assert third[0].state is first[0].state
    assert third[0].limitations == first[0].limitations
    assert third[0].status_text == first[0].status_text
    assert third[1].status_text == first[1].status_text
    assert third[1].limitation_codes == first[1].limitation_codes
    assert third[2].report.state is first[2].report.state
    assert third[2].report.missing_provenance == first[2].report.missing_provenance
    assert third[2].dispatch_permitted is first[2].dispatch_permitted is False
    assert tuple(
        (item.invocation_id, item.specialist, item.step_id, item.specialist_request_id)
        for item in third[3].descriptors
    ) == tuple(
        (item.invocation_id, item.specialist, item.step_id, item.specialist_request_id)
        for item in first[3].descriptors
    )
    assert third[4].response_policy.state is first[4].response_policy.state
    assert third[4].execution_preflight.state is first[4].execution_preflight.state
    assert third[4].execution_preflight.executable is False
    _assert_denial(third[4].workflow)


def test_i_synthetic_result_is_not_master_evidence():
    from wilvor_ai.master_orchestration import (
        SyntheticOutcome,
        SyntheticTerminalStatus,
        SyntheticWorkerResult,
        SyntheticWorkflowResult,
    )

    context, plan = _required_plan(history_step())
    authorization = plan.steps[0].invocation
    worker = SyntheticWorkerResult(
        master_request_id=context.master_request_id,
        correlation_id=context.correlation_id,
        invocation_id=authorization.invocation_id,
        specialist_request_id=authorization.specialist_request_id or "specialist-synthetic",
        specialist=authorization.specialist,
        outcome=SyntheticOutcome.NO_RESULT,
        note="synthetic-not-evidence",
    )
    workflow = SyntheticWorkflowResult(
        terminal_status=SyntheticTerminalStatus.NOT_RUN,
        reasons=(),
        decision=None,
        step_statuses=(),
        results=(),
        limitations=(),
        dispatch_count=0,
        dispatch_batches=(),
        executed_nodes=(),
    )
    assert worker.evidence_verified is False
    assert workflow.evidence_verified is False
    admission = MasterEvidenceAdmissionInput(authorization, worker)
    with pytest.raises(ContractValidationError, match="synthetic_result_not_evidence"):
        _bundle(context, plan, (MasterEvidenceCorroborationInput(admission),))
    admitted = admit_master_evidence((admission,)).admissions[0]
    assert admitted.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.UNSUPPORTED_ARTIFACT in admitted.reason_codes
    assert admitted.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert admitted.evidence_verified is False
    assert admitted.production_authority is MasterProductionAuthority.DENIED
    descriptor = describe_planned_adapters(_bundle(context, plan)).descriptors[0]
    for artifact in (worker, workflow):
        with pytest.raises(ContractValidationError, match="synthetic_result_not_evidence"):
            refuse_adapter_evidence(descriptor, artifact)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_j_ma5_ma6_import_isolation_is_process_local():
    for path in PRODUCTION_MODULES:
        assert FORBIDDEN_MODULES.isdisjoint(_imported_modules(path))
        source = path.read_text(encoding="utf-8")
        assert "langgraph" not in source
        assert "boto3" not in source
        assert "AthenaExecutor" not in source
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    forbidden = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_execution_preflight
import wilvor_ai.master_response_policy
import wilvor_ai.master_response_renderer
import wilvor_ai.master_specialist_adapters
loaded = [name for name in {forbidden!r} if name in sys.modules]
assert loaded == [], loaded
assert 'langgraph' not in sys.modules
import wilvor_ai.master_orchestration
assert 'wilvor_ai.master_orchestration' in sys.modules
assert 'langgraph' in sys.modules
assert 'wilvor_ai.master_response_policy' in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
