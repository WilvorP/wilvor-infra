"""Offline tests for the shared MA5/MA6/MA7 interface. Nothing here executes."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.fixtures.ma4_specialist_evidence_examples import (
    decision_offline_consistent_run,
    historical_exact_count_run,
    live_ops_all_verified_result,
    live_ops_context,
    live_ops_request,
)
from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    AgentCapability,
    ContractValidationError,
    ToolResult,
)
from wilvor_ai.historical_answer_renderer import finalize_historical_specialist_run
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import (
    LiveOpsAdmissionSources,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
    admit_master_evidence,
)
from wilvor_ai.master_evidence_comparability import (
    MasterComparisonCategory,
    MasterComparisonEligibility,
    MasterComparisonRequest,
    assess_master_evidence_comparability,
)
from wilvor_ai.master_evidence_corroboration import (
    MasterCorroborationStatus,
    MasterEvidenceCorroborationInput,
    corroborate_master_evidence,
)
from wilvor_ai.master_orchestration import SyntheticOutcome, SyntheticWorkerResult
from wilvor_ai.master_parallel_contracts import (
    MASTER_PARALLEL_CONTRACTS_SCHEMA_VERSION,
    MAX_COMPARISON_REQUESTS,
    MasterExecutionPreflightReport,
    MasterExecutionPreflightState,
    MasterInvocationBinding,
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
    MAX_DECISION_STEPS,
    MAX_ROUTE_STEPS,
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRouteOutcome,
    MasterRoutePlan,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.specialist_contracts import SpecialistResult
from wilvor_ai.specialist_runtime_contracts import HistoricalSpecialistRunResult


TEXT = "Which aircraft are currently affected by hazards, and what recorded history applies?"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK


def _context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-parallel-1",
        "correlation_id": "corr-parallel-1",
        "application_request_time_utc": "2026-01-15T00:00:01Z",
        "canonical_user_text": TEXT,
    }
    values.update(overrides)
    return MasterPlanningContext(**values)  # type: ignore[arg-type]


def _proposal(*steps: MasterProposedStep) -> MasterRouteProposal:
    capabilities = tuple(
        dict.fromkeys(
            MasterProposedCapability(step.capability, MasterRequirement.REQUIRED) for step in steps
        )
    )
    return MasterRouteProposal(user_text=TEXT, capabilities=capabilities, steps=steps)


def _live(step_id: str = "live-1") -> MasterProposedStep:
    return MasterProposedStep(
        step_id=step_id,
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=LIVE,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
    )


def _history(step_id: str = "hist-1") -> MasterProposedStep:
    return MasterProposedStep(
        step_id=step_id,
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=HISTORY,
        temporal_intent=MasterRouteTemporalIntent.HISTORICAL,
        historical_start_utc="2026-01-01T00:00:00Z",
        historical_end_utc="2026-01-02T00:00:00Z",
    )


def _decision(step_id: str = "dec-1") -> MasterProposedStep:
    return MasterProposedStep(
        step_id=step_id,
        specialist=MasterSpecialist.DECISION,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=RISK,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
    )


def _authorized(*steps: MasterProposedStep, **context_overrides: object):
    context = _context(**context_overrides)
    decision = authorize_route(_proposal(*steps), context)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED
    return context, decision.plan


def _historical_input(authorization: MasterInvocationAuthorization, *, with_run: bool):
    run = historical_exact_count_run()
    artifact = finalize_historical_specialist_run(run)
    return MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(authorization, artifact),
        historical_run=run if with_run else None,
    )


def _live_input(authorization: MasterInvocationAuthorization) -> MasterEvidenceCorroborationInput:
    return MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(
            authorization,
            LiveOpsAdmissionSources(
                live_ops_request(),
                live_ops_context(),
                live_ops_all_verified_result(),
            ),
        )
    )


def _bundle(*steps: MasterProposedStep, evidence=None, comparisons=()):
    context, plan = _authorized(*steps)
    if evidence is None:
        built = []
        for step in plan.steps:
            if step.invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
                built.append(_historical_input(step.invocation, with_run=True))
            elif step.invocation.specialist is MasterSpecialist.LIVE_OPS:
                built.append(_live_input(step.invocation))
            else:
                built.append(
                    MasterEvidenceCorroborationInput(
                        MasterEvidenceAdmissionInput(
                            step.invocation,
                            decision_offline_consistent_run(),
                        )
                    )
                )
        evidence = tuple(built)
    return MasterParallelContractBundle(
        context=context,
        plan=plan,
        evidence_inputs=evidence,
        comparison_requests=comparisons,
    )


def _deny(bundle: MasterParallelContractBundle) -> None:
    assert bundle.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert bundle.production_authority is MasterProductionAuthority.DENIED
    assert bundle.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert bundle.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert bundle.end_to_end_fulfilled is False
    assert bundle.schema_version == MASTER_PARALLEL_CONTRACTS_SCHEMA_VERSION


def test_one_request_binds_multiple_specialists_without_authority():
    bundle = _bundle(_live(), _history())
    assert bundle.context.master_request_id == bundle.plan.master_request_id
    assert [item.authorization.specialist for item in bundle.bindings()] == [
        step.invocation.specialist for step in bundle.plan.steps
    ]
    assert len({item.authorization.invocation_id for item in bundle.bindings()}) == 2
    assert len(bundle.evidence_inputs) == 2
    _deny(bundle)
    preflight = execution_preflight_for(bundle)
    assert preflight.state is MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION
    assert preflight.executable is False
    assert preflight.production_authority is MasterProductionAuthority.DENIED


def test_mismatched_master_request_is_rejected():
    context, plan = _authorized(_live())
    other = _context(master_request_id="master-req-parallel-2")
    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        MasterParallelContractBundle(
            context=other,
            plan=plan,
            evidence_inputs=(),
        )
    foreign = replace(
        plan.steps[0].invocation,
        master_request_id="master-req-parallel-2",
        invocation_id="inv-foreign",
    )
    with pytest.raises(ContractValidationError, match="master_request_mismatch|invocation_not_in_plan"):
        MasterParallelContractBundle(
            context=context,
            plan=plan,
            evidence_inputs=(_live_input(foreign),),
        )


def test_duplicate_invocation_id_is_rejected():
    bundle = _bundle(_live(), _history())
    duplicated = (bundle.evidence_inputs[0], bundle.evidence_inputs[0])
    with pytest.raises(ContractValidationError, match="duplicate_invocation_id"):
        replace(bundle, evidence_inputs=duplicated)


def test_more_than_one_decision_invocation_is_rejected():
    context, plan = _authorized(_decision())
    first = plan.steps[0]
    second_authorization = replace(
        first.invocation,
        invocation_id="master-req-parallel-1-extra",
        specialist_request_id="master-req-parallel-1-specialist-extra",
    )
    second = replace(first, step_id="dec-2", invocation=second_authorization)
    with pytest.raises(ContractValidationError, match="decision_fanout_not_permitted"):
        MasterRoutePlan(
            master_request_id=context.master_request_id,
            canonical_user_text=context.canonical_user_text,
            steps=(first, second),
        )
    assert MAX_DECISION_STEPS == 1


def test_more_than_four_invocations_are_rejected():
    context, plan = _authorized(_live())
    steps = []
    for index in range(MAX_ROUTE_STEPS + 1):
        authorization = replace(
            plan.steps[0].invocation,
            invocation_id=f"master-req-parallel-1-{index}",
            specialist_request_id=f"master-req-parallel-1-specialist-{index}",
        )
        steps.append(replace(plan.steps[0], step_id=f"step-{index}", invocation=authorization))
    with pytest.raises(ContractValidationError, match="excessive_plan_size"):
        MasterRoutePlan(
            master_request_id=context.master_request_id,
            canonical_user_text=context.canonical_user_text,
            steps=tuple(steps),
        )
    evidence = tuple(_live_input(plan.steps[0].invocation) for _ in range(MAX_ROUTE_STEPS + 1))
    with pytest.raises(ContractValidationError, match="excessive_plan_size"):
        MasterParallelContractBundle(
            context=context,
            plan=plan,
            evidence_inputs=evidence,
        )


def test_unknown_artifact_is_rejected():
    context, plan = _authorized(_live())
    foreign = MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(plan.steps[0].invocation, object())
    )
    with pytest.raises(ContractValidationError, match="unsupported_artifact"):
        MasterParallelContractBundle(context=context, plan=plan, evidence_inputs=(foreign,))


def test_synthetic_ma3_result_cannot_become_production_evidence():
    context, plan = _authorized(_history())
    synthetic = SyntheticWorkerResult(
        master_request_id=context.master_request_id,
        correlation_id=context.correlation_id,
        invocation_id=plan.steps[0].invocation.invocation_id,
        specialist_request_id=plan.steps[0].invocation.specialist_request_id or "specialist-synthetic",
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        outcome=SyntheticOutcome.NO_RESULT,
        note="synthetic-not-evidence",
    )
    assert synthetic.evidence_verified is False
    item = MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(plan.steps[0].invocation, synthetic)
    )
    with pytest.raises(ContractValidationError, match="synthetic_result_not_evidence"):
        MasterParallelContractBundle(context=context, plan=plan, evidence_inputs=(item,))
    admitted = admit_master_evidence((item.admission_input,))
    assert admitted.admissions[0].admission_status.value == "REJECTED"
    assert admitted.production_authority is MasterProductionAuthority.DENIED
    assert admitted.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED


def test_historical_run_as_final_artifact_stays_an_admission_rejection():
    context, plan = _authorized(_history())
    run = historical_exact_count_run()
    item = MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(plan.steps[0].invocation, run)
    )
    bundle = MasterParallelContractBundle(context=context, plan=plan, evidence_inputs=(item,))
    admitted = admit_master_evidence((bundle.evidence_inputs[0].admission_input,))
    assert admitted.admissions[0].admission_status.value == "REJECTED"
    assert admitted.production_authority is MasterProductionAuthority.DENIED
    _deny(bundle)


def test_consistent_supplied_evidence_does_not_authenticate_the_source():
    bundle = _bundle(_history())
    corroborated = corroborate_master_evidence(bundle.evidence_inputs)
    result = corroborated.results[0]
    assert result.corroboration_status is MasterCorroborationStatus.CONSISTENT_WITH_SUPPLIED_EVIDENCE
    assert result.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert result.production_authority is MasterProductionAuthority.DENIED
    assert result.evidence_verified is False
    assert result.operationally_eligible is False
    _deny(bundle)


def test_comparability_does_not_establish_operational_agreement():
    bundle = _bundle(_live(), _history())
    left = next(
        item.admission_input
        for item in bundle.evidence_inputs
        if item.admission_input.authorization.specialist is MasterSpecialist.LIVE_OPS
    )
    right = next(
        item.admission_input
        for item in bundle.evidence_inputs
        if item.admission_input.authorization.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    )
    request = MasterComparisonRequest(
        master_request_id=bundle.context.master_request_id,
        left_invocation_id=left.authorization.invocation_id,
        right_invocation_id=right.authorization.invocation_id,
        category=MasterComparisonCategory.CURRENT_SIGMET_VS_HISTORICAL_HAZARD_VERSIONS,
        aircraft_id_hint="ac-untrusted",
    )
    bound = replace(bundle, comparison_requests=(request,))
    assessed = assess_master_evidence_comparability(request, left, right)
    assert assessed.eligibility in {
        MasterComparisonEligibility.BLOCKED,
        MasterComparisonEligibility.UNSUPPORTED,
        MasterComparisonEligibility.NOT_ESTABLISHED,
        MasterComparisonEligibility.CONTRACT_CHECKABLE_ONLY,
    }
    assert assessed.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert assessed.evidence_verified is False
    assert assessed.production_authority is MasterProductionAuthority.DENIED
    assert "answer" not in assessed.__dataclass_fields__
    assert bound.comparison_requests[0].aircraft_id_hint == "ac-untrusted"
    _deny(bound)


def test_wrong_specialist_pair_cannot_become_a_factual_comparison():
    bundle = _bundle(_live(), _history())
    live = bundle.evidence_inputs[0].admission_input
    historical = bundle.evidence_inputs[1].admission_input
    if live.authorization.specialist is not MasterSpecialist.LIVE_OPS:
        live, historical = historical, live
    request = MasterComparisonRequest(
        master_request_id=bundle.context.master_request_id,
        left_invocation_id=live.authorization.invocation_id,
        right_invocation_id=historical.authorization.invocation_id,
        category=MasterComparisonCategory.LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT,
    )
    replace(bundle, comparison_requests=(request,))
    assessed = assess_master_evidence_comparability(request, live, historical)
    assert assessed.eligibility in {
        MasterComparisonEligibility.BLOCKED,
        MasterComparisonEligibility.UNSUPPORTED,
    }
    assert assessed.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert assessed.evidence_verified is False
    assert assessed.production_authority is MasterProductionAuthority.DENIED


def test_missing_historical_run_is_not_fabricated():
    context, plan = _authorized(_history())
    item = _historical_input(plan.steps[0].invocation, with_run=False)
    bundle = MasterParallelContractBundle(context=context, plan=plan, evidence_inputs=(item,))
    assert bundle.evidence_inputs[0].historical_run is None
    assert historical_run_if_supplied(bundle.evidence_inputs[0]) is None
    assert not isinstance(bundle.evidence_inputs[0].historical_run, HistoricalSpecialistRunResult)


def test_live_ops_public_refs_do_not_become_tool_results():
    bundle = _bundle(_live())
    artifact = bundle.evidence_inputs[0].admission_input.artifact
    sources = live_ops_public_sources(artifact)
    assert isinstance(sources, LiveOpsAdmissionSources)
    assert sources.result.evidence_references == artifact.result.evidence_references
    assert not isinstance(artifact, ToolResult)
    assert "tool_results" not in MasterParallelContractBundle.__dataclass_fields__
    context, plan = _authorized(_live())
    historical_artifact = finalize_historical_specialist_run(historical_exact_count_run())
    assert isinstance(historical_artifact, SpecialistResult)
    with pytest.raises(ContractValidationError, match="unsupported_artifact"):
        MasterParallelContractBundle(
            context=context,
            plan=plan,
            evidence_inputs=(
                MasterEvidenceCorroborationInput(
                    MasterEvidenceAdmissionInput(plan.steps[0].invocation, historical_artifact)
                ),
            ),
        )


def test_unresolved_binding_cannot_become_authenticated():
    bundle = _bundle(_history())
    admitted = admit_master_evidence((bundle.evidence_inputs[0].admission_input,))
    admission = admitted.admissions[0]
    assert admission.admission_status.value == "BINDING_UNRESOLVED"
    assert admission.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert admission.evidence_verified is False
    assert admission.production_authority is MasterProductionAuthority.DENIED
    _deny(bundle)


def test_response_policy_cannot_carry_an_operational_claim():
    bundle = _bundle(_live(), _history())
    policy = response_policy_for(bundle, MasterResponsePolicyState.UNAVAILABLE)
    assert policy.master_request_id == bundle.context.master_request_id
    assert policy.invocation_ids == tuple(step.invocation.invocation_id for step in bundle.plan.steps)
    assert "No count or zero is inferred." in policy.status_text
    assert policy.claims_admitted is False
    assert "answer" not in MasterResponsePolicyHandoff.__dataclass_fields__
    assert policy.production_authority is MasterProductionAuthority.DENIED
    with pytest.raises(ContractValidationError, match="invalid_status_text"):
        replace(policy, status_text="Aircraft N12345 should divert around SIGMET A12.")
    with pytest.raises(ContractValidationError, match="operational_claim_not_admitted"):
        replace(policy, claims_admitted=True)


def test_preflight_states_do_not_grant_execution():
    bundle = _bundle(_live())
    generated = execution_preflight_for(bundle)
    assert generated.state is MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION
    assert generated.missing_provenance == tuple(MasterProvenanceRequirement)
    assert generated.executable is False
    ready = MasterExecutionPreflightReport(
        workflow=bundle,
        state=MasterExecutionPreflightState.CONTRACT_READY_FOR_FUTURE_INTEGRATION,
        missing_provenance=tuple(MasterProvenanceRequirement),
    )
    assert ready.executable is False
    assert ready.production_authority is MasterProductionAuthority.DENIED
    assert ready.execution_authority is MasterRouteExecutionAuthority.DENIED
    blocked = replace(
        ready,
        state=MasterExecutionPreflightState.BLOCKED_BY_MISSING_PROVENANCE,
    )
    assert blocked.state is MasterExecutionPreflightState.BLOCKED_BY_MISSING_PROVENANCE
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(ready, executable=True)
    with pytest.raises(ContractValidationError, match="provenance_requirement_not_satisfied"):
        replace(ready, missing_provenance=())
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        replace(ready, production_authority=MasterProductionAuthority.PRODUCTION)
    assert set(MasterRouteExecutionAuthority) == {MasterRouteExecutionAuthority.DENIED}
    assert set(MasterSourceAuthenticity) == {MasterSourceAuthenticity.NOT_ESTABLISHED}


def test_safety_handoff_keeps_identity_and_denial_together():
    bundle = _bundle(_live(), _history())
    policy = response_policy_for(bundle, MasterResponsePolicyState.INSUFFICIENT_EVIDENCE)
    preflight = execution_preflight_for(bundle)
    handoff = MasterSafetyHandoff(
        workflow=bundle,
        response_policy=policy,
        execution_preflight=preflight,
    )
    assert handoff.workflow.context.master_request_id == policy.master_request_id
    assert handoff.execution_preflight.production_authority is MasterProductionAuthority.DENIED
    assert MasterResponseLimitation.NO_OPERATIONAL_CLAIM in handoff.response_policy.limitations
    other = _bundle(_live())
    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        MasterSafetyHandoff(
            workflow=other,
            response_policy=policy,
            execution_preflight=preflight,
        )


def test_constructor_rejects_mistyped_and_contradictory_fields():
    bundle = _bundle(_live())
    policy = response_policy_for(bundle, MasterResponsePolicyState.NOT_EVALUATED)
    with pytest.raises(ContractValidationError, match="invalid_evidence_inputs"):
        replace(bundle, evidence_inputs=[])
    with pytest.raises(ContractValidationError, match="invalid_comparison_requests"):
        replace(bundle, comparison_requests="not-a-request")
    with pytest.raises(ContractValidationError, match="invalid_limitations"):
        replace(policy, limitations=list(policy.limitations))
    with pytest.raises(ContractValidationError, match="invalid_response_policy_state"):
        replace(policy, state="UNSUPPORTED")
    with pytest.raises(ContractValidationError, match="source_authenticity_not_established"):
        replace(bundle, source_authenticity="AUTHENTICATED")
    with pytest.raises(ContractValidationError, match="invocation_not_in_plan"):
        request = MasterComparisonRequest(
            master_request_id=bundle.context.master_request_id,
            left_invocation_id=bundle.plan.steps[0].invocation.invocation_id,
            right_invocation_id="inv-not-planned",
            category=MasterComparisonCategory.QUERY_TIME_VS_OBSERVATION_TIME,
        )
        replace(bundle, comparison_requests=(request,))
    assert MasterRouteGate.NOT_REQUIRED.value == "NOT_REQUIRED"


def _comparison_requests(bundle: MasterParallelContractBundle, count: int):
    categories = tuple(MasterComparisonCategory)
    assert count <= len(categories)
    left = bundle.plan.steps[0].invocation.invocation_id
    right = bundle.plan.steps[1].invocation.invocation_id
    return tuple(
        MasterComparisonRequest(
            master_request_id=bundle.context.master_request_id,
            left_invocation_id=left,
            right_invocation_id=right,
            category=categories[index],
        )
        for index in range(count)
    )


def test_comparison_request_budget_is_the_ordered_pairs_of_a_four_step_route():
    assert MAX_COMPARISON_REQUESTS == MAX_ROUTE_STEPS * (MAX_ROUTE_STEPS - 1) == 12
    bundle = _bundle(_live(), _history())
    empty = replace(bundle, comparison_requests=())
    assert empty.comparison_requests == ()
    permitted = replace(bundle, comparison_requests=_comparison_requests(bundle, 2))
    assert len(permitted.comparison_requests) == 2
    exact = replace(bundle, comparison_requests=_comparison_requests(bundle, MAX_COMPARISON_REQUESTS))
    assert len(exact.comparison_requests) == MAX_COMPARISON_REQUESTS
    oversized = _comparison_requests(bundle, MAX_COMPARISON_REQUESTS) + (object(),)
    with pytest.raises(ContractValidationError, match="excessive_comparison_requests"):
        replace(bundle, comparison_requests=oversized)
    duplicated = (permitted.comparison_requests[0], permitted.comparison_requests[0])
    with pytest.raises(ContractValidationError, match="duplicate_comparison_request"):
        replace(bundle, comparison_requests=duplicated)
    unknown = MasterComparisonRequest(
        master_request_id=bundle.context.master_request_id,
        left_invocation_id=bundle.plan.steps[0].invocation.invocation_id,
        right_invocation_id="inv-not-on-the-plan",
        category=MasterComparisonCategory.QUERY_TIME_VS_OBSERVATION_TIME,
    )
    with pytest.raises(ContractValidationError, match="invocation_not_in_plan"):
        replace(bundle, comparison_requests=(unknown,))
    _deny(exact)


def test_step_id_must_be_a_provenance_token():
    _, plan = _authorized(_live())
    authorization = plan.steps[0].invocation
    valid = MasterInvocationBinding(plan.steps[0].step_id, authorization)
    assert valid.step_id == plan.steps[0].step_id
    rejected = (
        1,
        None,
        "",
        "   ",
        f" {plan.steps[0].step_id}",
        f"{plan.steps[0].step_id} ",
        "x" * (PROVENANCE_TOKEN_MAX_LENGTH + 1),
    )
    for step_id in rejected:
        with pytest.raises(ContractValidationError, match="invalid_step_id"):
            MasterInvocationBinding(step_id, authorization)


def test_received_result_cannot_invent_a_route_step():
    context, plan = _authorized(_live())
    extra = replace(
        plan.steps[0].invocation,
        invocation_id="master-req-parallel-1-unplanned",
        specialist_request_id="master-req-parallel-1-specialist-unplanned",
    )
    with pytest.raises(ContractValidationError, match="invocation_not_in_plan"):
        MasterParallelContractBundle(
            context=context,
            plan=plan,
            evidence_inputs=(_live_input(extra),),
        )
