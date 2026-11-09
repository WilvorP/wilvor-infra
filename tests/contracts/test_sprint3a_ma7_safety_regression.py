"""Baseline safety regressions across the existing Master modules.

These tests call the current MA1-MA6 contracts. They do not call a specialist,
a model provider, AWS, or a Sprint 3A module that is not on this branch.
Matching identifiers, structural readiness, process-local membership, and
caller-supplied artifacts stay untrusted.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from wilvor_ai.contracts import (
    AgentCapability,
    ContractValidationError,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionSpecialistRunResult,
    DecisionSpecialistRunStatus,
    DecisionTargetMode,
)
from wilvor_ai.decision_model_contracts import DecisionUnsupportedReason
from wilvor_ai.live_ops_public_contracts import LiveOpsRunStatus
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import (
    MasterAdmissionReason,
    MasterAdmissionStatus,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
    admit_master_evidence,
)
from wilvor_ai.master_evidence_comparability import (
    MasterComparisonCategory,
    MasterComparisonRequest,
)
from wilvor_ai.master_evidence_corroboration import MasterEvidenceCorroborationInput
from wilvor_ai.master_execution_preflight import (
    bind_preflight_report,
    evaluate_execution_preflight,
)
from wilvor_ai.master_execution_scope import (
    MasterExecutionDenialCategory,
    MasterExecutionPrerequisite,
    bind_master_execution_scope,
)
from wilvor_ai.master_decision_binding import prepare_master_decision_binding
from wilvor_ai.master_gate_resolution import (
    MasterDependencyCandidate,
    MasterDependencyVerdict,
    MasterDependentEffect,
    MasterGateEffectiveState,
    MasterGateEvidenceTrust,
    MasterGateResolutionLimitation,
    dependency_verdict,
    inspect_route_gates,
)
from wilvor_ai.master_historical_binding import (
    prepare_master_historical_binding,
    refuse_historical_binding_evidence,
)
from wilvor_ai.master_live_ops_binding import (
    MasterLiveOpsBindingLimitation,
    prepare_master_live_ops_binding,
)
from wilvor_ai.master_local_capability import (
    LocalCapabilityRegistry,
    LocalCapabilityScope,
    LocalCapabilityState,
    LocalConsumptionOutcome,
    LocalRejectionReason,
    LocalTestClock,
)
from wilvor_ai.master_orchestration import SyntheticOutcome, SyntheticWorkerResult
from wilvor_ai.master_parallel_contracts import (
    MasterExecutionPreflightReport,
    MasterExecutionPreflightState,
    MasterParallelContractBundle,
    MasterProvenanceRequirement,
    MasterResponseLimitation,
    MasterResponsePolicyState,
)
from wilvor_ai.master_planning_session import (
    MasterPlanningSessionRecord,
    MasterPlanningSessionRegistry,
)
from wilvor_ai.master_provenance_trace import (
    ProvenanceAuditLimitation,
    ProvenanceRequirementStatus,
    classify_master_provenance,
)
from wilvor_ai.master_response_policy import evaluate_master_response_policy
from wilvor_ai.master_response_renderer import render_master_response
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.master_specialist_adapters import (
    MasterAdapterExecutionState,
    describe_planned_adapters,
    refuse_adapter_evidence,
)
from wilvor_ai.specialist_contracts import (
    SpecialistResult,
    SpecialistStatus,
    VerifiedZeroClaim,
    VerifierOutcome,
)
from wilvor_ai.specialist_runtime_contracts import (
    HistoricalSpecialistRunResult,
    SpecialistRunStatus,
)


TEXT = "Which aircraft are currently affected by hazards, and what recorded risks apply?"
ZERO_TEXT = "How many aircraft were recorded? A matching empty payload is not a verified zero."
AS_OF = "2026-01-15T00:00:01Z"
NOW = datetime(2026, 1, 15, 0, 0, 1, tzinfo=timezone.utc)
LATER = datetime(2026, 1, 16, 0, 0, 1, tzinfo=timezone.utc)
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
CLOSED_PROVENANCE = {
    ProvenanceRequirementStatus.UNRESOLVED,
    ProvenanceRequirementStatus.UNSATISFIED,
}


def _errors(func) -> list[str]:
    with pytest.raises(ContractValidationError) as caught:
        func()
    return list(caught.value.errors)


def _context(text: str = TEXT, **overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "correlation_id": "corr-1",
        "application_request_time_utc": AS_OF,
        "canonical_user_text": text,
    }
    values.update(overrides)
    return MasterPlanningContext(**values)  # type: ignore[arg-type]


def _proposed(
    step_id: str,
    specialist: MasterSpecialist,
    capability: AgentCapability,
    **overrides: object,
) -> MasterProposedStep:
    values: dict[str, object] = {
        "step_id": step_id,
        "specialist": specialist,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "capability": capability,
    }
    values.update(overrides)
    return MasterProposedStep(**values)  # type: ignore[arg-type]


def _live(step_id: str = "live-1", **overrides: object) -> MasterProposedStep:
    return _proposed(
        step_id,
        MasterSpecialist.LIVE_OPS,
        LIVE,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
        **overrides,
    )


def _history(step_id: str = "hist-1", **overrides: object) -> MasterProposedStep:
    return _proposed(
        step_id,
        MasterSpecialist.HISTORICAL_ANALYTICS,
        HISTORY,
        temporal_intent=MasterRouteTemporalIntent.HISTORICAL,
        historical_start_utc="2026-01-01T00:00:00Z",
        historical_end_utc="2026-01-02T00:00:00Z",
        **overrides,
    )


def _decision(step_id: str = "dec-1", **overrides: object) -> MasterProposedStep:
    return _proposed(
        step_id,
        MasterSpecialist.DECISION,
        RISK,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
        **overrides,
    )


def _authorize(
    context: MasterPlanningContext,
    *steps: MasterProposedStep,
) -> tuple[MasterRouteProposal, object]:
    capabilities = []
    seen: set[AgentCapability] = set()
    for step in steps:
        if step.capability not in seen:
            seen.add(step.capability)
            capabilities.append(MasterProposedCapability(step.capability, MasterRequirement.REQUIRED))
    proposal = MasterRouteProposal(
        user_text=context.canonical_user_text,
        capabilities=tuple(capabilities),
        steps=steps,
    )
    decision = authorize_route(proposal, context)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert decision.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    return proposal, decision


def _step(plan, step_id: str):
    found = [item for item in plan.steps if item.step_id == step_id]
    assert len(found) == 1
    return found[0]


def _bundle(context, plan, evidence=(), comparisons=()):
    return MasterParallelContractBundle(
        context,
        plan,
        evidence,
        comparisons,
    )


def _adapters(bundle):
    described = describe_planned_adapters(bundle)
    return {
        item.invocation_id: item
        for item in described.descriptors
    }


def _open(context, proposal):
    registry = MasterPlanningSessionRegistry()
    return registry, registry.open_session(proposal, context)


def _intermediate_run() -> HistoricalSpecialistRunResult:
    return HistoricalSpecialistRunResult(
        status=SpecialistRunStatus.UNAVAILABLE,
        tool_results=(),
        tool_result_projections=(),
        executed_invocations=(),
        proposed_claims=(),
        unsupported_reason=None,
        evaluated_as_of_utc=None,
        runtime_errors=(),
        provider_turn_count=0,
        executed_tool_call_count=0,
        validation_feedback=(),
    )


def _historical_answer(**overrides: object) -> SpecialistResult:
    values: dict[str, object] = {
        "status": SpecialistStatus.ANSWERED,
        "answer": "Historical specialist artifact for structural admission.",
        "temporal_scope": TemporalScope.HISTORICAL,
        "evaluated_as_of_utc": AS_OF,
        "tool_results": (),
        "verified_claims": (),
        "limitations": (),
        "unsupported_reason": None,
        "verifier_outcome": VerifierOutcome.NOT_RUN,
    }
    values.update(overrides)
    return SpecialistResult(**values)  # type: ignore[arg-type]


def _decision_artifact(aircraft_id: str = "ac-1") -> DecisionSpecialistRunResult:
    return DecisionSpecialistRunResult(
        status=DecisionSpecialistRunStatus.UNSUPPORTED,
        mode=DecisionTargetMode.AIRCRAFT,
        aircraft_id=aircraft_id,
        recommendation_id=None,
        tool_results=(),
        evidence_snapshots=(),
        evidence_bindings=(),
        invocations=(),
        proposed_claims=(),
        verification=None,
        render=None,
        unsupported_reason=DecisionUnsupportedReason.INSUFFICIENT_EVIDENCE,
        runtime_errors=(),
        collection_partial_reason=None,
        validation_feedback=(),
        provider_turn_count=0,
        executed_tool_call_count=0,
        executed_persisted_call_count=0,
        terminal_kind=None,
    )


def _tool_result(correlation_id: str, **overrides: object) -> ToolResult:
    values: dict[str, object] = {
        "tool_name": "describe_hazard",
        "tool_call_id": "tool-call-1",
        "status": ToolResultStatus.NOT_FOUND,
        "temporal_scope": TemporalScope.CURRENT,
        "data": {
            "source_authenticated": True,
            "aircraft_count": 0,
            "verified_zero": True,
        },
        "evidence": (),
        "as_of_utc": AS_OF,
        "limitations": (),
        "correlation_id": correlation_id,
    }
    values.update(overrides)
    return ToolResult(**values)  # type: ignore[arg-type]


def _closed_authority(result) -> None:
    assert result.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert result.production_authority is MasterProductionAuthority.DENIED
    assert result.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert result.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert result.end_to_end_fulfilled is False


def _nonfactual(handoff) -> None:
    rendered = render_master_response(handoff)
    assert handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert handoff.claims_admitted is False
    assert rendered.claims_admitted is False
    assert rendered.status_text == "Supplied evidence is not sufficient for an operational claim."
    assert not hasattr(handoff, "answer")
    assert not hasattr(rendered, "answer")
    assert MasterResponseLimitation.NO_OPERATIONAL_CLAIM in handoff.limitations
    assert MasterResponseLimitation.EXECUTION_DENIED in handoff.limitations
    assert MasterResponseLimitation.SOURCE_AUTHENTICITY_NOT_ESTABLISHED in handoff.limitations
    _closed_authority(handoff)
    return rendered


def test_matching_identifiers_are_not_authentication():
    context = _context()
    proposal, decision = _authorize(context, _live(), _history(depends_on=("live-1",)))
    plan = decision.plan
    live = _step(plan, "live-1")
    historical = _step(plan, "hist-1")
    copied = replace(historical.invocation)
    assert copied == historical.invocation
    assert copied is not historical.invocation

    registry, record = _open(context, proposal)
    retained = registry.plan_for(record)
    retained_live = _step(retained, "live-1")
    assert retained is not plan
    assert retained_live.invocation == live.invocation
    assert retained_live.invocation is not live.invocation
    assert _errors(lambda: registry.associate_invocation(record, live.invocation)) == [
        "copied_invocation_identifier"
    ]
    assert _errors(lambda: registry.associate_invocation(record, copied)) == [
        "copied_invocation_identifier"
    ]
    selected = registry.associate_invocation(record, retained_live.invocation)
    assert selected is retained_live.invocation
    assert record.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert record.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED

    run = _intermediate_run()
    bundle = _bundle(
        context,
        plan,
        (
            MasterEvidenceCorroborationInput(
                MasterEvidenceAdmissionInput(copied, run),
            ),
        ),
    )
    admission = admit_master_evidence(tuple(item.admission_input for item in bundle.evidence_inputs))
    assert admission.admissions[0].admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.HISTORICAL_RUN_NOT_FINAL_ARTIFACT in admission.admissions[0].reason_codes
    assert admission.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert admission.evidence_verified is False

    scope = bind_master_execution_scope(
        context,
        plan,
        live,
        _adapters(bundle)[live.invocation.invocation_id],
    )
    assert scope.master_request_id == context.master_request_id
    assert scope.invocation_id == live.invocation.invocation_id
    assert scope.correlation_id == context.correlation_id
    assert MasterExecutionDenialCategory.MATCHING_IDENTIFIER_NOT_TRUST in scope.denial_categories
    assert scope.dispatch_permitted is False
    assert scope.execution_permission_granted is False

    inspection = inspect_route_gates(
        plan,
        (
            MasterDependencyCandidate(
                dependency_step_id=live.step_id,
                invocation_id=live.invocation.invocation_id,
                native_status=LiveOpsRunStatus.COMPLETED,
            ),
        ),
    )
    edge = inspection.dependency_edges[0]
    assert edge.identity_matched is True
    assert edge.verdict is MasterDependencyVerdict.COMPLETED_WITH_INSUFFICIENT_EVIDENCE
    assert edge.authoritative_terminal is False
    assert edge.trusted_execution_evidence is False
    assert inspection.authoritative_satisfaction is False
    assert inspection.dispatch_permitted is False
    assert MasterGateResolutionLimitation.MATCHING_IDS_ARE_NOT_TRUST in inspection.limitations
    assert all(
        item.trust is MasterGateEvidenceTrust.UNTRUSTED_CANDIDATE
        for item in inspection.observed_candidates
    )

    classified = classify_master_provenance(
        _tool_result(context.correlation_id),
        authorization=live.invocation,
    )
    assert classified.dispatch_permitted is False
    assert classified.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert all(item.status in CLOSED_PROVENANCE for item in classified.findings)
    assert (
        ProvenanceAuditLimitation.MATCHING_IDENTIFIERS_NOT_ORIGIN
        in classified.finding(MasterProvenanceRequirement.SOURCE_READ_PROVENANCE).limitations
    )


def test_structurally_ready_plan_cannot_execute():
    context = _context()
    proposal, decision = _authorize(context, _live())
    plan = decision.plan
    step = plan.steps[0]
    assert step.ready_to_execute is True
    assert step.gates == (MasterRouteGate.NOT_REQUIRED,)
    assert not hasattr(MasterRouteExecutionAuthority, "GRANTED")
    assert not hasattr(MasterRouteExecutionAuthority, "PRODUCTION")

    registry, record = _open(context, proposal)
    assert registry.plan_for(record).steps[0].ready_to_execute is True
    assert record.execution_authority is MasterRouteExecutionAuthority.DENIED

    bundle = _bundle(context, plan)
    descriptor = _adapters(bundle)[step.invocation.invocation_id]
    assert descriptor.execution_state is MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
    scope = bind_master_execution_scope(context, plan, step, descriptor)
    assert scope.structural_ready_to_execute is True
    assert scope.dispatch_permitted is False
    assert scope.redeemable is False
    assert scope.execution_permission_granted is False
    assert MasterExecutionDenialCategory.STRUCTURAL_READINESS_NOT_PERMISSION in scope.denial_categories
    assert scope.missing_prerequisites == tuple(MasterExecutionPrerequisite)

    evaluation = evaluate_execution_preflight(bundle)
    assert evaluation.report.state is MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION
    assert evaluation.report.state is not MasterExecutionPreflightState.CONTRACT_READY_FOR_FUTURE_INTEGRATION
    assert evaluation.dispatch_permitted is False
    assert evaluation.stops_before_execution_authorization is True
    assert evaluation.report.executable is False
    assert evaluation.report.missing_provenance == tuple(MasterProvenanceRequirement)
    named = MasterExecutionPreflightReport(
        workflow=bundle,
        state=MasterExecutionPreflightState.CONTRACT_READY_FOR_FUTURE_INTEGRATION,
        missing_provenance=tuple(MasterProvenanceRequirement),
    )
    bound = bind_preflight_report(bundle, named)
    assert bound.report.state is MasterExecutionPreflightState.CONTRACT_READY_FOR_FUTURE_INTEGRATION
    assert bound.dispatch_permitted is False
    assert bound.report.executable is False
    assert _errors(
        lambda: evaluate_execution_preflight(
            bundle,
            production_authority=MasterProductionAuthority.PRODUCTION,
        )
    ) == ["production_authority_not_issued"]

    binding = prepare_master_live_ops_binding(bundle, descriptor)
    assert binding.structural_ready_to_execute is True
    assert binding.dispatch_permitted is False
    assert binding.executable is False
    assert binding.aircraft_impact_count is None
    assert binding.verified_zero is False
    assert binding.trusted_context_created is False
    assert binding.admission_sources_created is False

    synthetic = SyntheticWorkerResult(
        master_request_id=context.master_request_id,
        correlation_id=context.correlation_id,
        invocation_id=step.invocation.invocation_id,
        specialist_request_id=step.invocation.specialist_request_id,
        specialist=step.invocation.specialist,
        outcome=SyntheticOutcome.SYNTHETIC_COMPLETED,
        note="synthetic completion is not a production read",
    )
    assert synthetic.evidence_verified is False
    assert synthetic.production_authority.value == "DENIED"
    assert _errors(lambda: replace(synthetic, evidence_verified=True)) == [
        "synthetic_evidence_not_verified"
    ]
    assert _errors(lambda: refuse_adapter_evidence(descriptor, synthetic)) == [
        "synthetic_result_not_evidence"
    ]
    assert _errors(
        lambda: _bundle(
            context,
            plan,
            (MasterEvidenceCorroborationInput(MasterEvidenceAdmissionInput(step.invocation, synthetic)),),
        )
    ) == ["synthetic_result_not_evidence"]
    rendered = _nonfactual(evaluate_master_response_policy(bundle))
    assert "synthetic" not in rendered.status_text


def test_process_local_session_is_not_an_authenticated_principal():
    context = _context()
    proposal, decision = _authorize(context, _live())
    registry, record = _open(context, proposal)
    plan = decision.plan
    assert record.session_id != context.master_request_id
    assert record.session_id != context.correlation_id
    assert record.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert record.production_authority is MasterProductionAuthority.DENIED
    assert record.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert not hasattr(registry, "open_authenticated")
    assert not hasattr(registry, "dispatch")
    assert _errors(lambda: MasterPlanningSessionRecord(record.session_id, "principal-1")) == [
        "session_principal_not_accepted"
    ]
    copied = MasterPlanningSessionRecord(record.session_id, object())
    assert _errors(lambda: registry.plan_for(copied)) == ["copied_session_identifier"]

    bundle = _bundle(context, plan)
    scope = bind_master_execution_scope(
        context,
        plan,
        plan.steps[0],
        _adapters(bundle)[plan.steps[0].invocation.invocation_id],
    )
    assert MasterExecutionPrerequisite.AUTHENTICATED_APPLICATION_PRINCIPAL in scope.missing_prerequisites
    assert MasterExecutionPrerequisite.TRUSTED_PLANNING_SESSION_ORIGIN in scope.missing_prerequisites
    assert scope.execution_permission_granted is False

    capabilities = LocalCapabilityRegistry(LocalTestClock(NOW))
    handle = capabilities.issue(
        scope=LocalCapabilityScope(
            specialist=MasterSpecialist.LIVE_OPS,
            purpose=MasterPurpose.ANSWER,
            requirement=MasterRequirement.REQUIRED,
            agent_capability=LIVE,
        ),
        invocation_id=plan.steps[0].invocation.invocation_id,
        consumer_id=record.session_id,
        session_id=record.session_id,
        expires_at=LATER,
    )
    assert capabilities.owns(handle) is True
    description = handle.description()
    assert description.authorizes_execution is False
    assert description.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert evaluate_execution_preflight(bundle).dispatch_permitted is False


def test_single_use_registry_transition_is_not_a_dispatch_permit():
    context = _context()
    proposal, decision = _authorize(context, _live(), _history(depends_on=("live-1",)))
    plan = decision.plan
    live = _step(plan, "live-1")
    registry, record = _open(context, proposal)
    retained_live = _step(registry.plan_for(record), "live-1")
    assert retained_live.invocation == live.invocation
    assert retained_live.invocation is not live.invocation
    registry.associate_invocation(record, retained_live.invocation)
    capabilities = LocalCapabilityRegistry(LocalTestClock(NOW))
    scope = LocalCapabilityScope(
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        agent_capability=LIVE,
    )
    handle = capabilities.issue(
        scope=scope,
        invocation_id=live.invocation.invocation_id,
        consumer_id="consumer-1",
        session_id=record.session_id,
        expires_at=LATER,
    )
    consumed = capabilities.consume(
        handle,
        scope=scope,
        invocation_id=live.invocation.invocation_id,
        consumer_id="consumer-1",
    )
    assert consumed.outcome is LocalConsumptionOutcome.TRANSITION_RECORDED
    assert consumed.local_state_transition_only is True
    assert bool(consumed) is False
    assert consumed.dispatch_permitted is False
    assert consumed.execution_permitted is False
    assert consumed.specialist_dispatched is False
    assert capabilities.observed_state(handle) is LocalCapabilityState.CONSUMED
    replay = capabilities.consume(
        handle,
        scope=scope,
        invocation_id=live.invocation.invocation_id,
        consumer_id="consumer-1",
    )
    assert replay.outcome is LocalConsumptionOutcome.REJECTED
    assert replay.reason is LocalRejectionReason.REPLAY
    assert replay.dispatch_permitted is False
    failed = capabilities.record_operation_failure(handle)
    assert failed.reason is LocalRejectionReason.OPERATION_FAILED
    assert failed.dispatch_permitted is False
    assert capabilities.observed_state(handle) is LocalCapabilityState.OPERATION_FAILED

    other = capabilities.issue(
        scope=scope,
        invocation_id="inv-other-1",
        consumer_id="consumer-1",
        session_id=record.session_id,
        expires_at=LATER,
    )
    revocation = capabilities.close_session(record.session_id)
    assert bool(revocation) is False
    assert revocation.dispatch_permitted is False
    assert revocation.execution_permitted is False
    assert capabilities.observed_state(other) is LocalCapabilityState.REVOKED
    registry.close(record)
    assert record.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert _errors(lambda: registry.selected_invocation(record)) == ["session_closed"]
    assert evaluate_execution_preflight(_bundle(context, plan)).dispatch_permitted is False
    inspection = inspect_route_gates(plan)
    assert inspection.dispatch_permitted is False
    assert inspection.authoritative_satisfaction is False


def test_caller_reported_specialist_failure_is_not_an_authoritative_gate_outcome():
    context = _context()
    _proposal, decision = _authorize(context, _live(), _history(depends_on=("live-1",)))
    plan = decision.plan
    live = _step(plan, "live-1")
    historical = _step(plan, "hist-1")
    assert historical.ready_to_execute is False
    assert MasterRouteGate.DEPENDENCY_BLOCKED in historical.gates
    before = tuple((step.step_id, step.gates, step.ready_to_execute) for step in plan.steps)

    classified = dependency_verdict(
        LiveOpsRunStatus.TOOL_FAILED,
        historical.invocation.requirement,
    )
    assert classified.verdict is MasterDependencyVerdict.FAILED
    assert classified.effect is MasterDependentEffect.REQUIRED_TERMINAL_FAILURE
    assert classified.evidence_sufficient is False
    assert classified.native_status_is_sufficient_evidence is False
    assert _errors(
        lambda: dependency_verdict(
            LiveOpsRunStatus.TOOL_FAILED,
            historical.invocation.requirement,
            evidence_sufficient=True,
        )
    ) == ["trusted_gate_evidence_not_available"]
    assert _errors(
        lambda: MasterDependencyCandidate(
            dependency_step_id=live.step_id,
            invocation_id=live.invocation.invocation_id,
            native_status=LiveOpsRunStatus.TOOL_FAILED,
            claimed_satisfied=True,
        )
    ) == ["authoritative_satisfaction_not_available"]

    inspection = inspect_route_gates(
        plan,
        (
            MasterDependencyCandidate(
                dependency_step_id=live.step_id,
                invocation_id=live.invocation.invocation_id,
                native_status=LiveOpsRunStatus.TOOL_FAILED,
            ),
        ),
    )
    edge = inspection.dependency_edges[0]
    assert edge.identity_matched is True
    assert edge.native_status is LiveOpsRunStatus.TOOL_FAILED
    assert edge.verdict is MasterDependencyVerdict.FAILED
    assert edge.effect is MasterDependentEffect.REQUIRED_PENDING_TRUSTED_EVIDENCE
    assert edge.authoritative_terminal is False
    assert edge.trusted_execution_evidence is False
    finding = next(
        item
        for item in inspection.findings
        if item.step_id == historical.step_id and item.original_gate is MasterRouteGate.DEPENDENCY_BLOCKED
    )
    assert finding.effective_state is MasterGateEffectiveState.UNRESOLVED
    assert finding.dependency_terminal is False
    assert finding.authoritative_satisfaction is False
    assert finding.candidate_trusted is False
    assert inspection.dispatch_permitted is False
    assert inspection.authoritative_satisfaction is False
    assert inspection.plan is plan
    assert before == tuple((step.step_id, step.gates, step.ready_to_execute) for step in plan.steps)
    assert (
        MasterGateResolutionLimitation.CALLER_OBSERVATION_CANNOT_SATISFY_GATE
        in inspection.limitations
    )
    assert (
        MasterGateResolutionLimitation.UNTRUSTED_CANDIDATE_CANNOT_TERMINATE_A_GATE
        in inspection.limitations
    )
    evaluation = evaluate_execution_preflight(_bundle(context, plan))
    assert evaluation.report.state is MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION
    assert evaluation.dispatch_permitted is False
    _nonfactual(evaluate_master_response_policy(_bundle(context, plan)))


def test_supplied_tool_results_do_not_establish_source_authenticity():
    context = _context()
    _proposal, decision = _authorize(context, _live())
    plan = decision.plan
    step = plan.steps[0]
    tool_result = _tool_result(context.correlation_id)
    assert tool_result.data["source_authenticated"] is True
    assert tool_result.data["aircraft_count"] == 0

    admission = admit_master_evidence((MasterEvidenceAdmissionInput(step.invocation, tool_result),))
    assert admission.admissions[0].admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.UNSUPPORTED_ARTIFACT in admission.admissions[0].reason_codes
    assert admission.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert admission.evidence_verified is False
    assert admission.production_authority is MasterProductionAuthority.DENIED
    assert _errors(
        lambda: _bundle(
            context,
            plan,
            (MasterEvidenceCorroborationInput(MasterEvidenceAdmissionInput(step.invocation, tool_result)),),
        )
    ) == ["unsupported_artifact"]

    classified = classify_master_provenance(tool_result, authorization=step.invocation)
    assert classified.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert classified.dispatch_permitted is False
    assert classified.production_authority is MasterProductionAuthority.DENIED
    assert all(item.status in CLOSED_PROVENANCE for item in classified.findings)
    source = classified.finding(MasterProvenanceRequirement.SOURCE_READ_PROVENANCE)
    assert ProvenanceAuditLimitation.AUTHENTICATED_SOURCE_READ_NOT_AVAILABLE in source.limitations
    assert not hasattr(ProvenanceRequirementStatus, "SATISFIED")
    population = classified.finding(MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS)
    assert population.status in CLOSED_PROVENANCE
    assert ProvenanceAuditLimitation.POPULATION_SCOPE_NOT_ESTABLISHED in population.limitations
    rendered = _nonfactual(evaluate_master_response_policy(_bundle(context, plan)))
    assert "0" not in rendered.status_text
    assert "source_authenticated" not in rendered.status_text


def test_historical_intermediate_results_are_not_final_evidence():
    context = _context()
    _proposal, decision = _authorize(context, _history(), _decision(depends_on=("hist-1",)))
    plan = decision.plan
    historical = _step(plan, "hist-1")
    decision_step = _step(plan, "dec-1")
    assert historical.ready_to_execute is True
    assert decision_step.ready_to_execute is False
    run = _intermediate_run()
    assert run.status is SpecialistRunStatus.UNAVAILABLE

    bundle = _bundle(
        context,
        plan,
        (MasterEvidenceCorroborationInput(MasterEvidenceAdmissionInput(historical.invocation, run)),),
    )
    admission = admit_master_evidence((bundle.evidence_inputs[0].admission_input,))
    assert admission.admissions[0].admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.HISTORICAL_RUN_NOT_FINAL_ARTIFACT in admission.admissions[0].reason_codes
    assert MasterAdmissionReason.HISTORICAL_RUN_CANNOT_REPRODUCE_VERIFICATION in admission.admissions[0].reason_codes
    assert admission.evidence_verified is False
    handoff = evaluate_master_response_policy(bundle)
    assert handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert handoff.state is not MasterResponsePolicyState.UNAVAILABLE
    rendered = _nonfactual(handoff)
    assert "UNAVAILABLE" not in rendered.status_text

    descriptor = _adapters(bundle)[historical.invocation.invocation_id]
    binding = prepare_master_historical_binding(bundle, descriptor)
    assert binding.historical_run is None
    assert binding.specialist_result is None
    assert binding.evaluated_as_of_utc is None
    assert binding.inferred_count is None
    assert binding.failure_interpreted_as_zero is False
    assert binding.dispatch_permitted is False
    assert binding.execution_state is MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
    assert _errors(lambda: refuse_historical_binding_evidence(run)) == [
        "historical_run_not_final_artifact"
    ]
    assert _errors(lambda: refuse_historical_binding_evidence(_historical_answer())) == [
        "verified_evidence_not_issued"
    ]

    inspection = inspect_route_gates(
        plan,
        (
            MasterDependencyCandidate(
                dependency_step_id=historical.step_id,
                invocation_id=historical.invocation.invocation_id,
                native_status=SpecialistRunStatus.PROPOSED_CLAIMS,
            ),
        ),
    )
    edge = inspection.dependency_edges[0]
    assert edge.verdict is MasterDependencyVerdict.INTERMEDIATE
    assert edge.effect is MasterDependentEffect.REQUIRED_INTERMEDIATE
    assert edge.authoritative_terminal is False
    assert edge.effect is not MasterDependentEffect.REQUIRED_TERMINAL_FAILURE
    assert (
        MasterGateResolutionLimitation.HISTORICAL_PROPOSED_CLAIMS_ARE_NOT_FINAL
        in inspection.limitations
    )
    assert evaluate_execution_preflight(bundle).dispatch_permitted is False


def test_decision_comparison_candidates_cannot_authorize_targets():
    context = _context(target_candidate_aircraft_id="ac-1")
    _proposal, decision = _authorize(context, _live(), _decision(depends_on=("live-1",)))
    plan = decision.plan
    live = _step(plan, "live-1")
    decision_step = _step(plan, "dec-1")
    assert decision_step.invocation.expected_aircraft_id == "ac-1"
    assert decision_step.target_authorization_granted is False
    assert decision_step.ready_to_execute is False
    assert MasterRouteGate.APPLICATION_TARGET_CANDIDATE_PRESENT in decision_step.gates

    artifact = _decision_artifact("ac-1")
    assert artifact.aircraft_id == decision_step.invocation.expected_aircraft_id
    comparison = MasterComparisonRequest(
        master_request_id=context.master_request_id,
        left_invocation_id=live.invocation.invocation_id,
        right_invocation_id=decision_step.invocation.invocation_id,
        category=MasterComparisonCategory.CALLSIGN_DISCOVERY_VS_DECISION_TARGET,
        aircraft_id_hint="ac-1",
    )
    bundle = _bundle(
        context,
        plan,
        (
            MasterEvidenceCorroborationInput(
                MasterEvidenceAdmissionInput(decision_step.invocation, artifact),
            ),
        ),
        (comparison,),
    )
    admission = admit_master_evidence((bundle.evidence_inputs[0].admission_input,))
    assert admission.admissions[0].admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
    assert (
        MasterAdmissionReason.EXPECTED_AIRCRAFT_IS_COMPARISON_CANDIDATE
        in admission.admissions[0].reason_codes
    )
    assert admission.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert admission.evidence_verified is False

    descriptor = _adapters(bundle)[decision_step.invocation.invocation_id]
    binding = prepare_master_decision_binding(bundle, descriptor)
    assert binding.comparison_candidate_aircraft_id == "ac-1"
    assert binding.target_authorization_granted is False
    assert binding.decision_target_trusted is False
    assert binding.trusted_aircraft_id is None
    assert binding.authorization_token is None
    assert binding.dispatch_permitted is False
    assert binding.ready_to_execute is False
    assert _errors(
        lambda: prepare_master_decision_binding(
            bundle,
            descriptor,
            comparison_candidates_authorize_target=True,
        )
    ) == ["target_authorization_not_granted"]

    inspection = inspect_route_gates(plan)
    candidate_gate = next(
        item
        for item in inspection.findings
        if item.original_gate is MasterRouteGate.APPLICATION_TARGET_CANDIDATE_PRESENT
    )
    assert candidate_gate.effective_state is MasterGateEffectiveState.UNRESOLVED_EXECUTION_POLICY
    assert candidate_gate.authoritative_satisfaction is False
    assert inspection.dispatch_permitted is False
    handoff = evaluate_master_response_policy(bundle)
    rendered = _nonfactual(handoff)
    assert MasterResponseLimitation.COMPARABILITY_NOT_OPERATIONAL_AGREEMENT in handoff.limitations
    assert "ac-1" not in rendered.status_text
    scope = bind_master_execution_scope(context, plan, decision_step, descriptor)
    assert scope.target_authorization_granted is False
    assert (
        MasterExecutionPrerequisite.SEPARATELY_AUTHORIZED_DECISION_TARGET
        in scope.missing_prerequisites
    )


def test_verified_zero_cannot_be_fabricated():
    context = _context(ZERO_TEXT)
    _proposal, decision = _authorize(context, _history())
    plan = decision.plan
    step = plan.steps[0]
    claim = VerifiedZeroClaim(tool_call_id="tool-call-1")
    answer = "Verified zero: 0 aircraft."
    artifact = _historical_answer(answer=answer, verified_claims=(claim,))
    bundle = _bundle(
        context,
        plan,
        (MasterEvidenceCorroborationInput(MasterEvidenceAdmissionInput(step.invocation, artifact)),),
    )
    admission = admit_master_evidence((bundle.evidence_inputs[0].admission_input,))
    assert MasterAdmissionReason.VERIFIED_ZERO_CLAIM_NOT_AUTHENTICATED in admission.admissions[0].reason_codes
    assert admission.admissions[0].admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
    assert admission.evidence_verified is False
    assert admission.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    handoff = evaluate_master_response_policy(bundle)
    assert handoff.state is not MasterResponsePolicyState.UNAVAILABLE
    rendered = _nonfactual(handoff)
    assert answer not in rendered.status_text
    assert "0" not in rendered.status_text
    assert MasterResponseLimitation.HISTORICAL_RUN_NOT_FABRICATED in handoff.limitations

    binding = prepare_master_historical_binding(bundle, _adapters(bundle)[step.invocation.invocation_id])
    assert binding.inferred_count is None
    assert binding.executed_tool_call_count is None
    assert binding.failure_interpreted_as_zero is False
    assert binding.evaluated_as_of_utc is None

    tool_result = _tool_result(context.correlation_id)
    classified = classify_master_provenance(
        tool_result,
        authorization=step.invocation,
        verified_zero_claim=claim,
    )
    assert classified.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert classified.dispatch_permitted is False
    population = classified.finding(MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS)
    assert ProvenanceAuditLimitation.FABRICATED_VERIFIED_ZERO in population.limitations
    assert population.status in CLOSED_PROVENANCE

    live_context = _context(ZERO_TEXT, master_request_id="master-req-2", correlation_id="corr-2")
    _live_proposal, live_decision = _authorize(live_context, _live())
    live_bundle = _bundle(live_context, live_decision.plan)
    live_step = live_decision.plan.steps[0]
    live_binding = prepare_master_live_ops_binding(
        live_bundle,
        _adapters(live_bundle)[live_step.invocation.invocation_id],
    )
    assert live_binding.verified_zero is False
    assert live_binding.aircraft_impact_count is None
    assert MasterLiveOpsBindingLimitation.VERIFIED_ZERO_NOT_ESTABLISHED in live_binding.limitations
    assert MasterLiveOpsBindingLimitation.NO_AUTHORITATIVE_AIRCRAFT_IMPACT_COUNT in live_binding.limitations
    assert _errors(
        lambda: SyntheticWorkerResult(
            master_request_id=context.master_request_id,
            correlation_id=context.correlation_id,
            invocation_id=step.invocation.invocation_id,
            specialist_request_id=step.invocation.specialist_request_id,
            specialist=step.invocation.specialist,
            outcome=SyntheticOutcome.SYNTHETIC_COMPLETED,
            note="fabricated verified zero",
            evidence_verified=True,
        )
    ) == ["synthetic_evidence_not_verified"]


def test_untrusted_evidence_produces_no_operational_factual_response():
    context = _context(target_candidate_aircraft_id="ac-1")
    _proposal, decision = _authorize(
        context,
        _live(),
        _history(depends_on=("live-1",)),
        _decision(depends_on=("live-1",)),
    )
    plan = decision.plan
    historical = _step(plan, "hist-1")
    decision_step = _step(plan, "dec-1")
    claim = VerifiedZeroClaim(tool_call_id="tool-call-1")
    historical_artifact = _historical_answer(
        answer="Verified zero: 0 aircraft are exposed to the SIGMET.",
        verified_claims=(claim,),
        verifier_outcome=VerifierOutcome.PASSED,
    )
    decision_artifact = _decision_artifact("ac-1")
    bundle = _bundle(
        context,
        plan,
        (
            MasterEvidenceCorroborationInput(
                MasterEvidenceAdmissionInput(historical.invocation, historical_artifact),
            ),
            MasterEvidenceCorroborationInput(
                MasterEvidenceAdmissionInput(decision_step.invocation, decision_artifact),
            ),
        ),
        (
            MasterComparisonRequest(
                master_request_id=context.master_request_id,
                left_invocation_id=historical.invocation.invocation_id,
                right_invocation_id=decision_step.invocation.invocation_id,
                category=MasterComparisonCategory.HISTORICAL_VERIFIED_ZERO_VS_LIVE_OPS_UNKNOWN,
                aircraft_id_hint="ac-1",
            ),
        ),
    )
    handoff = evaluate_master_response_policy(bundle)
    rendered = _nonfactual(handoff)
    assert handoff.state is not MasterResponsePolicyState.UNAVAILABLE
    assert handoff.state is not MasterResponsePolicyState.UNSUPPORTED
    assert MasterResponseLimitation.SUPPLIED_EVIDENCE_NOT_AUTHENTICATED in handoff.limitations
    assert MasterResponseLimitation.COMPARABILITY_NOT_OPERATIONAL_AGREEMENT in handoff.limitations
    for prohibited in ("SIGMET", "aircraft", "0", "ac-1", "risk", "hazard"):
        assert prohibited.casefold() not in rendered.status_text.casefold()
    assert evaluate_execution_preflight(bundle).dispatch_permitted is False
    assert plan.end_to_end_fulfilled is False
    assert decision.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
