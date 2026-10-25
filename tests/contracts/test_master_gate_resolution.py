"""Offline gate inspection. Observations do not authorize execution."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError, TemporalScope
from wilvor_ai.decision_specialist_runtime_contracts import DecisionSpecialistRunStatus
from wilvor_ai.live_ops_public_contracts import LiveOpsRunStatus
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_gate_resolution import (
    MASTER_GATE_RESOLUTION_LIMITATIONS,
    PROPOSED_INTERFACES,
    MasterDependencyCandidate,
    MasterDependencyOrigin,
    MasterDependencyVerdict,
    MasterDependencyVerdictReason,
    MasterDependentEffect,
    MasterGateCandidateObservation,
    MasterGateEffectiveState,
    MasterGateResolutionLimitation,
    MasterGateResolutionReason,
    MasterPlanningOmissionNote,
    MasterTrustedGateEvidenceWriter,
    dependency_verdict,
    inspect_route_gates,
)
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRouteHints,
    MasterRouteOutcome,
    MasterRoutePlan,
    MasterRouteProposal,
    MasterRouteStepPlan,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.specialist_contracts import SpecialistStatus
from wilvor_ai.specialist_runtime_contracts import SpecialistRunStatus


TEXT = "Which aircraft are currently affected by hazards, and what recorded risks apply?"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "functions"
    / "shared"
    / "wilvor_ai"
    / "master_gate_resolution.py"
)

def _row(status: object, verdict: MasterDependencyVerdict, reason: MasterDependencyVerdictReason) -> tuple[object, MasterDependencyVerdict, MasterDependencyVerdictReason]:
    return (status, verdict, reason)


EXPECTED_VERDICT_ROWS = (
    _row(SpecialistStatus.ANSWERED, MasterDependencyVerdict.COMPLETED_WITH_INSUFFICIENT_EVIDENCE, MasterDependencyVerdictReason.HISTORICAL_ANSWERED_WITHOUT_TRUSTED_EVIDENCE),
    _row(SpecialistStatus.PARTIAL, MasterDependencyVerdict.PARTIAL, MasterDependencyVerdictReason.NATIVE_PARTIAL),
    _row(SpecialistStatus.UNSUPPORTED, MasterDependencyVerdict.UNSUPPORTED, MasterDependencyVerdictReason.NATIVE_UNSUPPORTED),
    _row(SpecialistStatus.UNAVAILABLE, MasterDependencyVerdict.UNAVAILABLE, MasterDependencyVerdictReason.NATIVE_UNAVAILABLE),
    _row(SpecialistStatus.INVALID_REQUEST, MasterDependencyVerdict.FAILED, MasterDependencyVerdictReason.NATIVE_FAILED),
    _row(SpecialistStatus.PROVIDER_FAILED, MasterDependencyVerdict.PROVIDER_FAILED, MasterDependencyVerdictReason.NATIVE_PROVIDER_FAILED),
    _row(SpecialistRunStatus.PROPOSED_CLAIMS, MasterDependencyVerdict.COMPLETED_WITH_INSUFFICIENT_EVIDENCE, MasterDependencyVerdictReason.PROPOSED_CLAIMS_ARE_NOT_TRUSTED_COMPLETION),
    _row(SpecialistRunStatus.UNSUPPORTED, MasterDependencyVerdict.UNSUPPORTED, MasterDependencyVerdictReason.NATIVE_UNSUPPORTED),
    _row(SpecialistRunStatus.INVALID_REQUEST, MasterDependencyVerdict.FAILED, MasterDependencyVerdictReason.NATIVE_FAILED),
    _row(SpecialistRunStatus.UNAVAILABLE, MasterDependencyVerdict.UNAVAILABLE, MasterDependencyVerdictReason.NATIVE_UNAVAILABLE),
    _row(SpecialistRunStatus.PROVIDER_FAILED, MasterDependencyVerdict.PROVIDER_FAILED, MasterDependencyVerdictReason.NATIVE_PROVIDER_FAILED),
    _row(DecisionSpecialistRunStatus.COMPLETED, MasterDependencyVerdict.COMPLETED_WITH_INSUFFICIENT_EVIDENCE, MasterDependencyVerdictReason.NATIVE_SUCCESS_WITHOUT_TRUSTED_EVIDENCE),
    _row(DecisionSpecialistRunStatus.PARTIAL, MasterDependencyVerdict.PARTIAL, MasterDependencyVerdictReason.NATIVE_PARTIAL),
    _row(DecisionSpecialistRunStatus.UNSUPPORTED, MasterDependencyVerdict.UNSUPPORTED, MasterDependencyVerdictReason.NATIVE_UNSUPPORTED),
    _row(DecisionSpecialistRunStatus.INVALID_REQUEST, MasterDependencyVerdict.FAILED, MasterDependencyVerdictReason.NATIVE_FAILED),
    _row(DecisionSpecialistRunStatus.UNAVAILABLE, MasterDependencyVerdict.UNAVAILABLE, MasterDependencyVerdictReason.NATIVE_UNAVAILABLE),
    _row(DecisionSpecialistRunStatus.PROVIDER_FAILED, MasterDependencyVerdict.PROVIDER_FAILED, MasterDependencyVerdictReason.NATIVE_PROVIDER_FAILED),
    _row(LiveOpsRunStatus.COMPLETED, MasterDependencyVerdict.COMPLETED_WITH_INSUFFICIENT_EVIDENCE, MasterDependencyVerdictReason.NATIVE_SUCCESS_WITHOUT_TRUSTED_EVIDENCE),
    _row(LiveOpsRunStatus.UNSUPPORTED, MasterDependencyVerdict.UNSUPPORTED, MasterDependencyVerdictReason.NATIVE_UNSUPPORTED),
    _row(LiveOpsRunStatus.REFUSED, MasterDependencyVerdict.FAILED, MasterDependencyVerdictReason.NATIVE_FAILED),
    _row(LiveOpsRunStatus.INVALID_REQUEST, MasterDependencyVerdict.FAILED, MasterDependencyVerdictReason.NATIVE_FAILED),
    _row(LiveOpsRunStatus.TOOL_FAILED, MasterDependencyVerdict.FAILED, MasterDependencyVerdictReason.NATIVE_FAILED),
    _row(LiveOpsRunStatus.PROVIDER_FAILED, MasterDependencyVerdict.PROVIDER_FAILED, MasterDependencyVerdictReason.NATIVE_PROVIDER_FAILED),
    _row(LiveOpsRunStatus.PROVIDER_TIMEOUT, MasterDependencyVerdict.TIMED_OUT, MasterDependencyVerdictReason.NATIVE_TIMED_OUT),
    _row(LiveOpsRunStatus.RUNTIME_TIMEOUT, MasterDependencyVerdict.TIMED_OUT, MasterDependencyVerdictReason.NATIVE_TIMED_OUT),
    _row(LiveOpsRunStatus.BUDGET_EXHAUSTED, MasterDependencyVerdict.FAILED, MasterDependencyVerdictReason.NATIVE_FAILED),
    _row(LiveOpsRunStatus.CANCELLED, MasterDependencyVerdict.CANCELLED, MasterDependencyVerdictReason.NATIVE_CANCELLED),
    _row(LiveOpsRunStatus.UNAVAILABLE, MasterDependencyVerdict.UNAVAILABLE, MasterDependencyVerdictReason.NATIVE_UNAVAILABLE),
)


def _context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "correlation_id": "corr-1",
        "application_request_time_utc": "2026-01-15T00:00:01Z",
        "canonical_user_text": TEXT,
    }
    values.update(overrides)
    return MasterPlanningContext(**values)  # type: ignore[arg-type]


def _step(
    step_id: str,
    specialist: MasterSpecialist,
    purpose: MasterPurpose,
    capability: AgentCapability,
    **overrides: object,
) -> MasterProposedStep:
    values: dict[str, object] = {
        "step_id": step_id,
        "specialist": specialist,
        "purpose": purpose,
        "requirement": MasterRequirement.REQUIRED,
        "capability": capability,
    }
    values.update(overrides)
    return MasterProposedStep(**values)  # type: ignore[arg-type]


def _live(step_id: str = "live-1", **overrides: object) -> MasterProposedStep:
    return _step(
        step_id,
        MasterSpecialist.LIVE_OPS,
        MasterPurpose.ANSWER,
        LIVE,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
        **overrides,
    )


def _history(step_id: str = "hist-1", **overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "temporal_intent": MasterRouteTemporalIntent.HISTORICAL,
        "historical_start_utc": "2026-01-01T00:00:00Z",
        "historical_end_utc": "2026-01-02T00:00:00Z",
    }
    values.update(overrides)
    return _step(
        step_id,
        MasterSpecialist.HISTORICAL_ANALYTICS,
        MasterPurpose.ANSWER,
        HISTORY,
        **values,
    )


def _decision(step_id: str = "dec-1", **overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "capability": RISK,
        "temporal_intent": MasterRouteTemporalIntent.CURRENT,
    }
    values.update(overrides)
    capability = values.pop("capability")
    return _step(
        step_id,
        MasterSpecialist.DECISION,
        MasterPurpose.ANSWER,
        capability,  # type: ignore[arg-type]
        **values,
    )


def _proposal(*steps: MasterProposedStep) -> MasterRouteProposal:
    capabilities = []
    seen = set()
    for step in steps:
        if step.capability not in seen:
            capabilities.append(MasterProposedCapability(step.capability, MasterRequirement.REQUIRED))
            seen.add(step.capability)
    return MasterRouteProposal(user_text=TEXT, capabilities=tuple(capabilities), steps=steps)


def _plan(*steps: MasterProposedStep, **context: object) -> MasterRoutePlan:
    decision = authorize_route(_proposal(*steps), _context(**context))
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    return decision.plan


def _by_id(plan: MasterRoutePlan) -> dict[str, MasterRouteStepPlan]:
    return {step.step_id: step for step in plan.steps}


def _candidate(plan: MasterRoutePlan, step_id: str, status: object) -> MasterDependencyCandidate:
    step = _by_id(plan)[step_id]
    return MasterDependencyCandidate(
        dependency_step_id=step.step_id,
        invocation_id=step.invocation.invocation_id,
        native_status=status,  # type: ignore[arg-type]
    )


def _finding(inspection: object, step_id: str, gate: MasterRouteGate):
    matches = [
        item
        for item in inspection.findings  # type: ignore[attr-defined]
        if item.step_id == step_id and item.original_gate is gate
    ]
    assert len(matches) == 1
    return matches[0]


def _edge(inspection: object, dependent_id: str, dependency_id: str):
    matches = [
        item
        for item in inspection.dependency_edges  # type: ignore[attr-defined]
        if item.dependent_step_id == dependent_id and item.dependency_step_id == dependency_id
    ]
    assert len(matches) == 1
    return matches[0]


def _hand_plan(gate: MasterRouteGate) -> MasterRoutePlan:
    temporal = gate is MasterRouteGate.TEMPORAL_SCOPE_UNRESOLVED
    specialist = MasterSpecialist.DECISION if temporal else MasterSpecialist.LIVE_OPS
    capability = RISK if temporal else LIVE
    scopes = () if temporal else (TemporalScope.CURRENT,)
    invocation = MasterInvocationAuthorization(
        master_request_id="master-req-gate",
        invocation_id="inv-gate-1",
        specialist=specialist,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        specialist_request_id="spec-gate-1",
        correlation_id="corr-gate-1",
        application_request_time_utc="2026-01-15T00:00:01Z",
    )
    step = MasterRouteStepPlan(
        step_id="step-1",
        invocation=invocation,
        capability=capability,
        gates=(gate,),
        ready_to_execute=gate is MasterRouteGate.NOT_REQUIRED,
        temporal_scopes=scopes,
        depends_on=(),
    )
    return MasterRoutePlan(
        master_request_id="master-req-gate",
        canonical_user_text=TEXT,
        steps=(step,),
    )


@pytest.mark.parametrize(
    ("status", "expected_verdict", "expected_reason"),
    EXPECTED_VERDICT_ROWS,
    ids=[f"{type(status).__name__}-{status.value}" for status, _, _ in EXPECTED_VERDICT_ROWS],
)
@pytest.mark.parametrize(
    "requirement",
    [MasterRequirement.REQUIRED, MasterRequirement.OPTIONAL],
)
def test_dependency_verdict_classifies_every_native_status(
    status: object,
    expected_verdict: MasterDependencyVerdict,
    expected_reason: MasterDependencyVerdictReason,
    requirement: MasterRequirement,
):
    result = dependency_verdict(status, requirement)  # type: ignore[arg-type]
    assert result.verdict is expected_verdict
    assert result.reason is expected_reason
    assert result.native_status is status
    assert result.verdict is not MasterDependencyVerdict.COMPLETED
    assert result.evidence_sufficient is False
    assert result.native_status_is_sufficient_evidence is False
    assert result.planning_omission is False
    assert result.classification is MasterDependencyOrigin.RUNTIME_OBSERVATION
    if requirement is MasterRequirement.REQUIRED:
        assert result.optional_unmet is False
    else:
        assert result.runtime_failure is False


def test_native_status_catalog_is_complete():
    catalog = (
        list(SpecialistStatus)
        + list(SpecialistRunStatus)
        + list(DecisionSpecialistRunStatus)
        + list(LiveOpsRunStatus)
    )
    covered = [(type(status).__name__, status.value) for status, _, _ in EXPECTED_VERDICT_ROWS]
    expected = [(type(status).__name__, status.value) for status in catalog]
    assert sorted(covered) == sorted(expected)


def test_completed_and_answered_are_distinct_from_authoritative_completion():
    assert MasterDependencyVerdict.COMPLETED is not MasterDependencyVerdict.COMPLETED_WITH_INSUFFICIENT_EVIDENCE
    completed = dependency_verdict(DecisionSpecialistRunStatus.COMPLETED, MasterRequirement.REQUIRED)
    answered = dependency_verdict(SpecialistStatus.ANSWERED, MasterRequirement.REQUIRED)
    live = dependency_verdict(LiveOpsRunStatus.COMPLETED, MasterRequirement.OPTIONAL)
    assert completed.verdict is MasterDependencyVerdict.COMPLETED_WITH_INSUFFICIENT_EVIDENCE
    assert answered.verdict is MasterDependencyVerdict.COMPLETED_WITH_INSUFFICIENT_EVIDENCE
    assert answered.reason is MasterDependencyVerdictReason.HISTORICAL_ANSWERED_WITHOUT_TRUSTED_EVIDENCE
    assert live.effect is MasterDependentEffect.OPTIONAL_INSUFFICIENT_EVIDENCE
    assert completed.effect is MasterDependentEffect.REQUIRED_INSUFFICIENT_EVIDENCE
    assert completed.runtime_failure is False


def test_required_and_optional_terminal_effects_differ():
    required = dependency_verdict(LiveOpsRunStatus.PROVIDER_FAILED, MasterRequirement.REQUIRED)
    optional = dependency_verdict(LiveOpsRunStatus.PROVIDER_FAILED, MasterRequirement.OPTIONAL)
    assert required.verdict is MasterDependencyVerdict.PROVIDER_FAILED
    assert required.effect is MasterDependentEffect.REQUIRED_TERMINAL_FAILURE
    assert required.runtime_failure is True
    assert required.optional_unmet is False
    assert optional.effect is MasterDependentEffect.OPTIONAL_TERMINAL_FAILURE
    assert optional.runtime_failure is False
    assert optional.optional_unmet is True
    assert required.verdict is optional.verdict


def test_partial_is_not_completion_or_terminal_failure():
    required = dependency_verdict(DecisionSpecialistRunStatus.PARTIAL, MasterRequirement.REQUIRED)
    optional = dependency_verdict(SpecialistStatus.PARTIAL, MasterRequirement.OPTIONAL)
    assert required.verdict is MasterDependencyVerdict.PARTIAL
    assert required.effect is MasterDependentEffect.REQUIRED_PARTIAL
    assert required.runtime_failure is False
    assert optional.effect is MasterDependentEffect.OPTIONAL_PARTIAL
    assert required.verdict is not MasterDependencyVerdict.COMPLETED
    assert required.verdict is not MasterDependencyVerdict.FAILED


def test_timeout_cancelled_and_budget_stay_distinct():
    timeout = dependency_verdict(LiveOpsRunStatus.PROVIDER_TIMEOUT, MasterRequirement.REQUIRED)
    runtime = dependency_verdict(LiveOpsRunStatus.RUNTIME_TIMEOUT, MasterRequirement.REQUIRED)
    budget = dependency_verdict(LiveOpsRunStatus.BUDGET_EXHAUSTED, MasterRequirement.REQUIRED)
    cancelled = dependency_verdict(LiveOpsRunStatus.CANCELLED, MasterRequirement.OPTIONAL)
    assert timeout.verdict is MasterDependencyVerdict.TIMED_OUT
    assert runtime.verdict is MasterDependencyVerdict.TIMED_OUT
    assert timeout.native_status is LiveOpsRunStatus.PROVIDER_TIMEOUT
    assert runtime.native_status is LiveOpsRunStatus.RUNTIME_TIMEOUT
    assert budget.verdict is MasterDependencyVerdict.FAILED
    assert budget.verdict is not MasterDependencyVerdict.TIMED_OUT
    assert budget.verdict is not MasterDependencyVerdict.PROVIDER_FAILED
    assert cancelled.verdict is MasterDependencyVerdict.CANCELLED
    assert cancelled.effect is MasterDependentEffect.OPTIONAL_TERMINAL_FAILURE


def test_pending_dependencies_are_not_runtime_failures():
    required = dependency_verdict(None, MasterRequirement.REQUIRED)
    optional = dependency_verdict(None, MasterRequirement.OPTIONAL)
    assert required.verdict is None
    assert required.effect is MasterDependentEffect.REQUIRED_PENDING_TRUSTED_EVIDENCE
    assert optional.effect is MasterDependentEffect.OPTIONAL_PENDING_TRUSTED_EVIDENCE
    assert required.classification is MasterDependencyOrigin.PLANNED_EDGE
    assert required.runtime_failure is False
    assert optional.runtime_failure is False


@pytest.mark.parametrize("requirement", [MasterRequirement.REQUIRED, MasterRequirement.OPTIONAL])
def test_planning_omission_is_not_a_runtime_failure(requirement: MasterRequirement):
    result = dependency_verdict(None, requirement, planning_omission=True)
    assert result.classification is MasterDependencyOrigin.PLANNING_TIME_OMISSION
    assert result.effect is MasterDependentEffect.PLANNING_OMISSION_NOT_RUNTIME_FAILURE
    assert result.reason is MasterDependencyVerdictReason.PLANNING_TIME_OMISSION
    assert result.verdict is None
    assert result.runtime_failure is False
    assert result.optional_unmet is False
    assert result.requirement is requirement


def test_planning_omission_cannot_carry_a_runtime_status():
    with pytest.raises(ContractValidationError, match="planning_omission_is_not_runtime_outcome"):
        dependency_verdict(
            LiveOpsRunStatus.CANCELLED,
            MasterRequirement.REQUIRED,
            planning_omission=True,
        )


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("COMPLETED", "invalid_native_status"),
        ("ANSWERED", "invalid_native_status"),
        (True, "invalid_native_status"),
        (MasterDependencyVerdict.COMPLETED, "invalid_native_status"),
    ],
)
def test_string_and_bool_statuses_are_not_native_evidence(status: object, code: str):
    with pytest.raises(ContractValidationError, match=code):
        dependency_verdict(status, MasterRequirement.REQUIRED)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"evidence_sufficient": True}, "trusted_gate_evidence_not_available"),
        ({"verifier_passed": True}, "verifier_pass_is_not_trusted_gate_evidence"),
        ({"claimed_satisfied": True}, "authoritative_satisfaction_not_available"),
    ],
)
def test_caller_cannot_assert_sufficient_evidence(kwargs: dict[str, bool], code: str):
    with pytest.raises(ContractValidationError, match=code):
        dependency_verdict(DecisionSpecialistRunStatus.COMPLETED, MasterRequirement.REQUIRED, **kwargs)


def test_every_planned_gate_has_one_non_satisfied_resolution():
    seen = set()
    for gate in MasterRouteGate:
        plan = _hand_plan(gate)
        before = plan.to_dict()
        inspection = inspect_route_gates(plan)
        assert plan.to_dict() == before
        assert inspection.plan is plan
        assert len(inspection.findings) == 1
        finding = inspection.findings[0]
        assert finding.original_gate is gate
        assert finding.authoritative_satisfaction is False
        assert finding.candidate_trusted is False
        assert finding.effective_state is not MasterGateEffectiveState.NOT_REQUIRED or gate is MasterRouteGate.NOT_REQUIRED
        seen.add(gate)
        if gate is MasterRouteGate.APPLICATION_TARGET_CANDIDATE_PRESENT:
            assert finding.effective_state is MasterGateEffectiveState.UNRESOLVED_EXECUTION_POLICY
            assert (
                finding.reason
                is MasterGateResolutionReason.APPLICATION_TARGET_CANDIDATE_IS_UNRESOLVED_EXECUTION_POLICY
            )
        if gate is MasterRouteGate.TRUSTED_IDENTITY_VALIDATION_PENDING:
            assert finding.reason is MasterGateResolutionReason.DECISION_IDENTITY_PENDING
        if gate is MasterRouteGate.CURRENTNESS_VALIDATION_PENDING:
            assert finding.reason is MasterGateResolutionReason.DECISION_CURRENTNESS_PENDING
        if gate is MasterRouteGate.REGION_VALIDATION_PENDING:
            assert (
                finding.reason
                is MasterGateResolutionReason.REGION_VALIDATION_PENDING_WITHOUT_AUTHORITATIVE_GEOMETRY
            )
        if gate is MasterRouteGate.TEMPORAL_SCOPE_UNRESOLVED:
            assert finding.reason is MasterGateResolutionReason.TEMPORAL_SCOPE_UNRESOLVED_PENDING
            assert plan.steps[0].temporal_scopes == ()
        if gate is MasterRouteGate.DEPENDENCY_BLOCKED:
            assert (
                finding.reason
                is MasterGateResolutionReason.DEPENDENCY_PENDING_WITHOUT_TRUSTED_EXECUTION_EVIDENCE
            )
        if gate is not MasterRouteGate.NOT_REQUIRED:
            assert finding.missing_trusted_evidence is True
            assert finding.effective_state is not MasterGateEffectiveState.NOT_REQUIRED
    assert seen == set(MasterRouteGate)
    assert "SATISFIED" not in {item.value for item in MasterGateEffectiveState}


def test_live_readiness_is_not_a_dispatch_permit():
    plan = _plan(_live())
    inspection = inspect_route_gates(plan)
    assert plan.steps[0].ready_to_execute is True
    assert plan.steps[0].gates == (MasterRouteGate.NOT_REQUIRED,)
    assert inspection.dispatch_permitted is False
    assert inspection.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert inspection.production_authority is MasterProductionAuthority.DENIED
    assert inspection.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert inspection.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert inspection.trusted_gate_evidence_writer is MasterTrustedGateEvidenceWriter.ABSENT
    assert inspection.implemented_cross_module_interfaces == ()
    assert inspection.proposed_interfaces == PROPOSED_INTERFACES
    assert all(item.startswith("PROPOSED:") for item in inspection.proposed_interfaces)
    assert inspection.limitations == MASTER_GATE_RESOLUTION_LIMITATIONS
    assert MasterGateResolutionLimitation.STRUCTURAL_READINESS_IS_NOT_A_DISPATCH_PERMIT in inspection.limitations
    finding = inspection.findings[0]
    assert finding.effective_state is MasterGateEffectiveState.NOT_REQUIRED
    assert finding.authoritative_satisfaction is False


def test_application_target_is_recorded_and_not_execution_authority():
    plan = _plan(_decision(), target_candidate_aircraft_id="ac-1")
    step = plan.steps[0]
    before_gates = step.gates
    inspection = inspect_route_gates(
        plan,
        gate_observations=(
            MasterGateCandidateObservation(
                step_id=step.step_id,
                invocation_id=step.invocation.invocation_id,
                gate=MasterRouteGate.APPLICATION_TARGET_CANDIDATE_PRESENT,
            ),
        ),
    )
    assert step.gates is before_gates
    assert MasterRouteGate.APPLICATION_TARGET_CANDIDATE_PRESENT in step.gates
    assert len(inspection.findings) == len(step.gates)
    finding = _finding(inspection, step.step_id, MasterRouteGate.APPLICATION_TARGET_CANDIDATE_PRESENT)
    identity = _finding(inspection, step.step_id, MasterRouteGate.TRUSTED_IDENTITY_VALIDATION_PENDING)
    currentness = _finding(inspection, step.step_id, MasterRouteGate.CURRENTNESS_VALIDATION_PENDING)
    assert finding.effective_state is MasterGateEffectiveState.UNRESOLVED_EXECUTION_POLICY
    assert finding.candidate_observed is True
    assert finding.authoritative_satisfaction is False
    assert finding.missing_trusted_evidence is True
    assert identity.reason is MasterGateResolutionReason.DECISION_IDENTITY_PENDING
    assert identity.effective_state is MasterGateEffectiveState.UNRESOLVED
    assert currentness.reason is MasterGateResolutionReason.DECISION_CURRENTNESS_PENDING
    assert currentness.effective_state is MasterGateEffectiveState.UNRESOLVED
    assert step.invocation.expected_aircraft_id == "ac-1"
    assert step.target_authorization_granted is False
    assert inspection.dispatch_permitted is False


def test_region_and_temporal_gates_stay_pending():
    region_plan = _plan(_live(hints=MasterRouteHints(region_text="KSFO area")))
    temporal_plan = _plan(_decision(temporal_intent=MasterRouteTemporalIntent.UNSPECIFIED))
    region_step = region_plan.steps[0]
    temporal_step = temporal_plan.steps[0]
    region = inspect_route_gates(
        region_plan,
        gate_observations=(
            MasterGateCandidateObservation(
                step_id=region_step.step_id,
                invocation_id=region_step.invocation.invocation_id,
                gate=MasterRouteGate.REGION_VALIDATION_PENDING,
            ),
        ),
    )
    temporal = inspect_route_gates(temporal_plan)
    region_finding = _finding(region, region_step.step_id, MasterRouteGate.REGION_VALIDATION_PENDING)
    temporal_finding = _finding(
        temporal,
        temporal_step.step_id,
        MasterRouteGate.TEMPORAL_SCOPE_UNRESOLVED,
    )
    assert (
        region_finding.reason
        is MasterGateResolutionReason.REGION_VALIDATION_PENDING_WITHOUT_AUTHORITATIVE_GEOMETRY
    )
    assert region_finding.effective_state is MasterGateEffectiveState.UNRESOLVED
    assert region_step.region_text == "KSFO area"
    assert temporal_finding.reason is MasterGateResolutionReason.TEMPORAL_SCOPE_UNRESOLVED_PENDING
    assert temporal_step.temporal_scopes == ()
    assert temporal_plan.steps[0].temporal_scopes == ()


@pytest.mark.parametrize(
    ("field_name", "code"),
    [
        ("claimed_satisfied", "authoritative_satisfaction_not_available"),
        ("authoritative_geometry", "authoritative_geometry_not_available"),
        ("identity_validated", "decision_identity_not_validated"),
        ("currentness_validated", "decision_currentness_not_validated"),
        ("temporal_scope_resolved", "temporal_scope_not_resolved"),
        ("execution_policy_cleared", "execution_policy_not_cleared"),
    ],
)
def test_gate_observation_cannot_claim_resolution(field_name: str, code: str):
    with pytest.raises(ContractValidationError, match=code):
        MasterGateCandidateObservation(
            step_id="dec-1",
            invocation_id="master-req-1-1",
            gate=MasterRouteGate.APPLICATION_TARGET_CANDIDATE_PRESENT,
            **{field_name: True},
        )


def test_matching_success_without_provenance_does_not_satisfy_a_dependency():
    plan = _plan(_live("live-1"), _history("hist-1", depends_on=("live-1",)))
    before = plan.to_dict()
    inspection = inspect_route_gates(
        plan,
        observations=(
            _candidate(plan, "live-1", LiveOpsRunStatus.COMPLETED),
            _candidate(plan, "hist-1", SpecialistStatus.ANSWERED),
        ),
    )
    assert plan.to_dict() == before
    live_edge = _edge(inspection, "hist-1", "live-1")
    assert live_edge.identity_matched is True
    assert live_edge.evidence_sufficient is False
    assert live_edge.trusted_execution_evidence is False
    assert live_edge.verdict is MasterDependencyVerdict.COMPLETED_WITH_INSUFFICIENT_EVIDENCE
    assert live_edge.effect is MasterDependentEffect.REQUIRED_INSUFFICIENT_EVIDENCE
    assert live_edge.origin is MasterDependencyOrigin.RUNTIME_OBSERVATION
    finding = _finding(inspection, "hist-1", MasterRouteGate.DEPENDENCY_BLOCKED)
    assert finding.effective_state is MasterGateEffectiveState.UNRESOLVED
    assert finding.missing_trusted_evidence is True
    assert finding.candidate_observed is True
    assert finding.authoritative_satisfaction is False
    assert finding.original_gate is MasterRouteGate.DEPENDENCY_BLOCKED
    assert inspection.authoritative_satisfaction is False
    historical = next(item for item in inspection.observed_candidates if item.step_id == "hist-1")
    assert historical.identity_matches_plan is True
    assert historical.authoritative is False
    assert historical.trust.value == "UNTRUSTED_CANDIDATE"
    assert historical.native_status_value == "ANSWERED"


def test_foreign_invocation_evidence_is_not_applied():
    plan = _plan(_live("live-1"), _decision("dec-1", depends_on=("live-1",)))
    inspection = inspect_route_gates(
        plan,
        observations=(
            MasterDependencyCandidate(
                dependency_step_id="live-1",
                invocation_id="foreign-invocation",
                native_status=LiveOpsRunStatus.COMPLETED,
            ),
        ),
    )
    edge = _edge(inspection, "dec-1", "live-1")
    assert edge.verdict is None
    assert edge.reason is MasterDependencyVerdictReason.NO_RUNTIME_OBSERVATION
    assert edge.origin is MasterDependencyOrigin.PLANNED_EDGE
    assert edge.identity_matched is False
    finding = _finding(inspection, "dec-1", MasterRouteGate.DEPENDENCY_BLOCKED)
    assert finding.foreign_candidate is True
    assert finding.candidate_observed is False
    assert finding.effective_state is MasterGateEffectiveState.UNRESOLVED
    foreign = inspection.observed_candidates[0]
    assert foreign.identity_matches_plan is False
    assert foreign.applied is False
    assert foreign.native_status_value == "COMPLETED"
    identity = _finding(inspection, "dec-1", MasterRouteGate.TRUSTED_IDENTITY_VALIDATION_PENDING)
    assert identity.effective_state is MasterGateEffectiveState.UNRESOLVED


def test_wrong_specialist_success_is_not_applied():
    plan = _plan(_history())
    inspection = inspect_route_gates(
        plan,
        observations=(_candidate(plan, "hist-1", DecisionSpecialistRunStatus.COMPLETED),),
    )
    candidate = inspection.observed_candidates[0]
    assert candidate.identity_matches_plan is True
    assert candidate.applied is False
    assert candidate.authoritative is False
    assert inspection.findings[0].authoritative_satisfaction is False
    assert inspection.dispatch_permitted is False


def test_incomplete_dependencies_stay_pending_and_are_not_omissions():
    plan = _plan(
        _live("live-1"),
        _history("hist-1"),
        _decision("dec-1", depends_on=("live-1", "hist-1")),
    )
    inspection = inspect_route_gates(
        plan,
        observations=(_candidate(plan, "live-1", LiveOpsRunStatus.COMPLETED),),
    )
    live_edge = _edge(inspection, "dec-1", "live-1")
    history_edge = _edge(inspection, "dec-1", "hist-1")
    assert live_edge.effect is MasterDependentEffect.REQUIRED_INSUFFICIENT_EVIDENCE
    assert history_edge.effect is MasterDependentEffect.REQUIRED_PENDING_TRUSTED_EVIDENCE
    assert history_edge.origin is MasterDependencyOrigin.PLANNED_EDGE
    assert history_edge.verdict is None
    finding = _finding(inspection, "dec-1", MasterRouteGate.DEPENDENCY_BLOCKED)
    assert finding.effective_state is MasterGateEffectiveState.UNRESOLVED
    assert finding.dependency_terminal is False
    assert inspection.planning_omissions == ()
    assert len(plan.steps) == 3


def test_partial_and_failed_dependencies_stay_distinct_for_a_required_dependent():
    plan = _plan(
        _live("live-1"),
        _history("hist-1"),
        _decision("dec-1", depends_on=("live-1", "hist-1")),
    )
    inspection = inspect_route_gates(
        plan,
        observations=(
            _candidate(plan, "live-1", LiveOpsRunStatus.TOOL_FAILED),
            _candidate(plan, "hist-1", SpecialistStatus.PARTIAL),
        ),
    )
    failed = _edge(inspection, "dec-1", "live-1")
    partial = _edge(inspection, "dec-1", "hist-1")
    assert failed.verdict is MasterDependencyVerdict.FAILED
    assert failed.effect is MasterDependentEffect.REQUIRED_TERMINAL_FAILURE
    assert partial.verdict is MasterDependencyVerdict.PARTIAL
    assert partial.effect is MasterDependentEffect.REQUIRED_PARTIAL
    assert partial.verdict is not failed.verdict
    finding = _finding(inspection, "dec-1", MasterRouteGate.DEPENDENCY_BLOCKED)
    assert finding.effective_state is MasterGateEffectiveState.TERMINAL_FAILURE
    assert finding.reason is MasterGateResolutionReason.DEPENDENCY_TERMINAL_FAILURE
    assert finding.original_gate is MasterRouteGate.DEPENDENCY_BLOCKED
    assert MasterRouteGate.DEPENDENCY_BLOCKED in _by_id(plan)["dec-1"].gates
    identity = _finding(inspection, "dec-1", MasterRouteGate.TRUSTED_IDENTITY_VALIDATION_PENDING)
    assert identity.effective_state is MasterGateEffectiveState.UNRESOLVED
    assert inspection.dispatch_permitted is False
    assert inspection.authoritative_satisfaction is False


def test_optional_terminal_dependency_is_not_a_required_failure():
    plan = _plan(
        _live("live-1"),
        _history("hist-1", depends_on=("live-1",), requirement=MasterRequirement.OPTIONAL),
    )
    inspection = inspect_route_gates(
        plan,
        observations=(_candidate(plan, "live-1", LiveOpsRunStatus.PROVIDER_FAILED),),
    )
    edge = _edge(inspection, "hist-1", "live-1")
    assert edge.dependent_requirement is MasterRequirement.OPTIONAL
    assert edge.effect is MasterDependentEffect.OPTIONAL_TERMINAL_FAILURE
    assert edge.verdict is MasterDependencyVerdict.PROVIDER_FAILED
    finding = _finding(inspection, "hist-1", MasterRouteGate.DEPENDENCY_BLOCKED)
    assert finding.effective_state is MasterGateEffectiveState.UNRESOLVED
    assert finding.dependency_terminal is False
    assert finding.missing_trusted_evidence is True
    assert len(plan.steps) == 2


def test_planning_omission_does_not_rewrite_the_plan_or_become_runtime_failure():
    plan = _plan(_live("live-1"), _history("hist-1", depends_on=("live-1",)))
    before = plan.to_dict()
    omissions = plan.unsupported_omissions
    inspection = inspect_route_gates(
        plan,
        planning_omissions=(
            MasterPlanningOmissionNote(
                dependent_step_id="hist-1",
                omitted_dependency_id="live-1",
                dependent_requirement=MasterRequirement.OPTIONAL,
            ),
            MasterPlanningOmissionNote(
                dependent_step_id="hist-1",
                omitted_dependency_id="dropped-1",
                dependent_requirement=MasterRequirement.REQUIRED,
            ),
        ),
    )
    assert plan.to_dict() == before
    assert plan.unsupported_omissions == omissions
    assert [step.step_id for step in plan.steps] == [step.step_id for step in inspection.plan.steps]
    assert _edge(inspection, "hist-1", "live-1").origin is MasterDependencyOrigin.PLANNED_EDGE
    assert _edge(inspection, "hist-1", "live-1").effect is MasterDependentEffect.REQUIRED_PENDING_TRUSTED_EVIDENCE
    still_planned, absent = inspection.planning_omissions
    assert still_planned.dependency_still_planned is True
    assert still_planned.removed_from_plan is False
    assert still_planned.applied_as_runtime_failure is False
    assert still_planned.effect is MasterDependentEffect.PLANNING_OMISSION_NOT_RUNTIME_FAILURE
    assert still_planned.requirement is MasterRequirement.REQUIRED
    assert still_planned.note_requirement_ignored is True
    assert absent.dependency_still_planned is False
    assert absent.removed_from_plan is False
    assert absent.applied_as_runtime_failure is False
    assert absent.requirement_source.value == "PLAN"
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "_without_omitted_dependencies" not in source


def test_inspection_does_not_mutate_the_original_plan():
    plan = _plan(
        _live("live-1"),
        _decision("dec-1", depends_on=("live-1",)),
        target_candidate_aircraft_id="ac-1",
    )
    before = plan.to_dict()
    gate_identity = plan.steps[1].gates
    step_identity = plan.steps
    inspection = inspect_route_gates(
        plan,
        observations=(_candidate(plan, "live-1", DecisionSpecialistRunStatus.COMPLETED),),
        gate_observations=(
            MasterGateCandidateObservation(
                step_id="dec-1",
                invocation_id=_by_id(plan)["dec-1"].invocation.invocation_id,
                gate=MasterRouteGate.TRUSTED_IDENTITY_VALIDATION_PENDING,
            ),
        ),
    )
    assert inspection.plan is plan
    assert plan.steps is step_identity
    assert plan.steps[1].gates is gate_identity
    assert plan.to_dict() == before
    assert inspect_route_gates(plan).to_dict() == inspect_route_gates(plan).to_dict()
    with pytest.raises(FrozenInstanceError):
        plan.steps[1].gates = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        inspection.dispatch_permitted = True  # type: ignore[misc]
    mismatched = inspection.observed_candidates[0]
    assert mismatched.applied is False
    assert _finding(
        inspection,
        "dec-1",
        MasterRouteGate.TRUSTED_IDENTITY_VALIDATION_PENDING,
    ).effective_state is MasterGateEffectiveState.UNRESOLVED


def test_forged_authority_and_mutable_inputs_are_rejected():
    plan = _plan(_live())
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        inspect_route_gates(plan, production_authority=MasterProductionAuthority.PRODUCTION)
    with pytest.raises(ContractValidationError, match="execution_authority_not_granted"):
        inspect_route_gates(plan, execution_authority=MasterProductionAuthority.PRODUCTION)
    with pytest.raises(ContractValidationError, match="source_authenticity_not_established"):
        inspect_route_gates(plan, source_authenticity="ESTABLISHED")
    with pytest.raises(ContractValidationError, match="authoritative_satisfaction_not_available"):
        inspect_route_gates(plan, authoritative_satisfaction=True)
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        inspect_route_gates(plan, dispatch_permitted=True)
    with pytest.raises(TypeError, match="plan is required"):
        inspect_route_gates({"steps": []})  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="invalid_observations"):
        inspect_route_gates(plan, observations=[])  # type: ignore[arg-type]


def test_duplicate_observations_are_rejected():
    plan = _plan(_live("live-1"), _history("hist-1", depends_on=("live-1",)))
    candidate = _candidate(plan, "live-1", LiveOpsRunStatus.UNAVAILABLE)
    with pytest.raises(ContractValidationError, match="duplicate_dependency_observation"):
        inspect_route_gates(plan, observations=(candidate, candidate))


def test_gate_observation_cannot_add_a_gate_to_the_plan():
    plan = _plan(_live())
    before = plan.steps[0].gates
    inspection = inspect_route_gates(
        plan,
        gate_observations=(
            MasterGateCandidateObservation(
                step_id=plan.steps[0].step_id,
                invocation_id=plan.steps[0].invocation.invocation_id,
                gate=MasterRouteGate.APPLICATION_TARGET_CANDIDATE_PRESENT,
            ),
        ),
    )
    assert plan.steps[0].gates is before
    assert [item.original_gate for item in inspection.findings] == [MasterRouteGate.NOT_REQUIRED]
    assert inspection.observed_candidates[0].applied is False
    assert inspection.authoritative_satisfaction is False


def test_public_surface_has_no_execution_entrypoint():
    import wilvor_ai.master_gate_resolution as module

    forbidden = {
        "authorize_route",
        "dispatch",
        "satisfy_gate",
        "build_planning_session",
        "DispatchPermit",
        "PlanningSession",
        "SpecialistRuntime",
    }
    assert forbidden.isdisjoint(set(dir(module)))
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "_without_omitted_dependencies" not in source
    assert "langgraph" not in source
    assert "boto3" not in source
