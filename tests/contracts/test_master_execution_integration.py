"""Offline handoff from a planning session to execution inspection.

These tests do not call a specialist, a model, AWS, or a dispatcher.
Object identity checks registry membership inside this process. It is not
production authentication.
"""

from __future__ import annotations

from dataclasses import fields, replace

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_execution_integration import (
    MASTER_EXECUTION_INTEGRATION_SCHEMA_VERSION,
    MasterExecutionHandoffInspection,
    inspect_master_execution_handoff,
)
from wilvor_ai.master_execution_preflight import MasterExecutionPreflightState
from wilvor_ai.master_execution_scope import (
    MasterExecutionDenialCategory,
    MasterExecutionPrerequisite,
)
from wilvor_ai.master_gate_resolution import (
    MasterGateEffectiveState,
    MasterGateResolutionLimitation,
    MasterGateResolutionReason,
)
from wilvor_ai.master_planning_session import (
    MasterPlanningSessionRecord,
    MasterPlanningSessionRegistry,
    MasterPlanningSessionState,
)
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRequestCoverage,
    MasterRouteDecision,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRouteOutcome,
    MasterRoutePlan,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
)
from wilvor_ai.master_specialist_adapters import (
    MasterAdapterExecutionState,
    MasterSpecialistAdapterDescriptor,
)


TEXT = "Which aircraft are currently affected by hazards, and what recorded risks apply?"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
ALTITUDE = AgentCapability.ISSUE_ALTITUDE_INSTRUCTIONS
VISUALIZATION = AgentCapability.GENERATE_VISUALIZATION_SPECIFICATIONS


def _context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-handoff-1",
        "correlation_id": "corr-handoff-1",
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


def _live(step_id: str = "live-1", **overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "step_id": step_id,
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "capability": LIVE,
        "temporal_intent": MasterRouteTemporalIntent.CURRENT,
    }
    values.update(overrides)
    return MasterProposedStep(**values)  # type: ignore[arg-type]


def _history(step_id: str = "hist-1", **overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "step_id": step_id,
        "specialist": MasterSpecialist.HISTORICAL_ANALYTICS,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "capability": HISTORY,
        "temporal_intent": MasterRouteTemporalIntent.HISTORICAL,
        "historical_start_utc": "2026-01-01T00:00:00Z",
        "historical_end_utc": "2026-01-02T00:00:00Z",
    }
    values.update(overrides)
    return MasterProposedStep(**values)  # type: ignore[arg-type]


def _decision(step_id: str = "dec-1") -> MasterProposedStep:
    return MasterProposedStep(
        step_id=step_id,
        specialist=MasterSpecialist.DECISION,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=RISK,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
    )


def _open_selected(
    *steps: MasterProposedStep,
    select: str,
    **context_overrides: object,
) -> tuple[MasterPlanningSessionRegistry, MasterPlanningSessionRecord]:
    registry = MasterPlanningSessionRegistry()
    record = registry.open_session(_proposal(*steps), _context(**context_overrides))
    plan = registry.plan_for(record)
    selected = next(step for step in plan.steps if step.step_id == select)
    registry.associate_invocation(record, selected.invocation)
    return registry, record


def _assert_execution_unavailable(inspection: MasterExecutionHandoffInspection) -> None:
    assert inspection.schema_version == MASTER_EXECUTION_INTEGRATION_SCHEMA_VERSION
    assert inspection.execution_authorized is False
    assert inspection.executable is False
    assert inspection.dispatch_permitted is False
    assert inspection.provenance_authenticated is False
    assert inspection.factual_response_available is False
    assert inspection.multi_invocation_execution_supported is False
    assert inspection.stops_before_execution_authorization is True
    assert inspection.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert inspection.production_authority is MasterProductionAuthority.DENIED
    assert inspection.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert inspection.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert inspection.decision.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert inspection.session.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert inspection.session.production_authority is MasterProductionAuthority.DENIED
    assert inspection.session.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    names = {item.name for item in fields(inspection)}
    assert "evidence_inputs" not in names
    assert "comparison_requests" not in names
    assert "dispatch" not in names
    assert "factual_response" not in names
    assert callable(inspection) is False
    assert not hasattr(inspection, "dispatch")
    assert not hasattr(inspection, "execute")
    assert not hasattr(MasterExecutionHandoffInspection, "dispatch")


@pytest.mark.parametrize(
    ("specialist", "ready"),
    [
        (MasterSpecialist.LIVE_OPS, True),
        (MasterSpecialist.HISTORICAL_ANALYTICS, True),
        (MasterSpecialist.DECISION, False),
    ],
)
def test_each_specialist_stays_execution_denied(specialist: MasterSpecialist, ready: bool):
    if specialist is MasterSpecialist.LIVE_OPS:
        steps = (_live(),)
        select = "live-1"
        context: dict[str, object] = {}
    elif specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
        steps = (_history(),)
        select = "hist-1"
        context = {}
    else:
        steps = (_decision(),)
        select = "dec-1"
        context = {"target_candidate_aircraft_id": "ac-1"}
    registry, record = _open_selected(*steps, select=select, **context)
    plan = registry.plan_for(record)
    selected = registry.selected_invocation(record)
    inspection = inspect_master_execution_handoff(registry, record, invocation=selected)

    assert inspection.planning_succeeded is True
    assert inspection.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert inspection.plan is plan
    assert inspection.decision.plan is plan
    assert inspection.selected_invocation is selected
    assert inspection.selected_invocation_count == 1
    assert inspection.planned_step_ids == tuple(step.step_id for step in plan.steps)
    assert inspection.execution_scope is not None
    assert inspection.execution_scope.specialist is specialist
    assert inspection.execution_scope.structural_ready_to_execute is ready
    assert inspection.execution_scope.structural_ready_to_execute is plan.steps[0].ready_to_execute
    assert inspection.execution_scope.execution_permission_granted is False
    assert inspection.execution_scope.planned_step.invocation is selected
    assert inspection.descriptor is not None
    assert inspection.descriptor.specialist is specialist
    assert (
        inspection.descriptor.execution_state
        is MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
    )
    assert inspection.preflight is not None
    assert inspection.preflight.bundle.evidence_inputs == ()
    assert inspection.preflight.bundle.comparison_requests == ()
    assert inspection.preflight.bundle.plan is plan
    assert inspection.preflight.report.state is MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION
    assert inspection.preflight.report.executable is False
    assert inspection.preflight.dispatch_permitted is False
    assert inspection.gate_inspection is not None
    assert inspection.gate_inspection.plan is plan
    assert inspection.gate_inspection.dispatch_permitted is False
    assert inspection.gate_inspection.authoritative_satisfaction is False
    assert inspection.gate_inspection.observed_candidates == ()
    assert (
        MasterGateResolutionLimitation.STRUCTURAL_READINESS_IS_NOT_A_DISPATCH_PERMIT
        in inspection.gate_inspection.limitations
    )
    assert (
        MasterExecutionDenialCategory.STRUCTURAL_READINESS_NOT_PERMISSION
        in inspection.execution_scope.denial_categories
    )
    assert (
        MasterExecutionPrerequisite.REVIEWED_EXECUTION_AUTHORITY
        in inspection.execution_scope.missing_prerequisites
    )
    assert inspection.execution_scope.target_authorization_granted is False
    _assert_execution_unavailable(inspection)
    assert registry.state(record) is MasterPlanningSessionState.OPEN
    assert registry.decision_for(record) is inspection.decision
    assert registry.selected_invocation(record) is selected


def test_structural_readiness_does_not_grant_execution_permission():
    registry, record = _open_selected(_live(), select="live-1")
    plan = registry.plan_for(record)
    assert plan.steps[0].ready_to_execute is True
    assert plan.steps[0].gates == (MasterRouteGate.NOT_REQUIRED,)
    inspection = inspect_master_execution_handoff(registry, record)
    scope = inspection.execution_scope
    assert scope is not None
    assert scope.structural_ready_to_execute is True
    assert inspection.planning_succeeded is True
    assert inspection.execution_authorized is False
    assert scope.execution_permission_granted is False
    assert scope.dispatch_permitted is False
    assert scope.executable is False
    assert inspection.preflight is not None
    assert inspection.preflight.report.state is MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION
    assert MasterExecutionDenialCategory.STRUCTURAL_READINESS_NOT_PERMISSION in scope.denial_categories
    _assert_execution_unavailable(inspection)


def test_repeated_specialists_do_not_enable_multi_invocation_execution():
    registry, record = _open_selected(_live("live-east"), _live("live-west"), select="live-east")
    plan = registry.plan_for(record)
    selected = registry.selected_invocation(record)
    inspection = inspect_master_execution_handoff(registry, record)

    assert [step.step_id for step in plan.steps] == ["live-east", "live-west"]
    assert {step.invocation.specialist for step in plan.steps} == {MasterSpecialist.LIVE_OPS}
    assert inspection.planned_step_ids == ("live-east", "live-west")
    assert inspection.planned_dependencies == ()
    assert len(inspection.planned_descriptors) == 2
    assert {item.specialist for item in inspection.planned_descriptors} == {MasterSpecialist.LIVE_OPS}
    assert len({item.invocation_id for item in inspection.planned_descriptors}) == 2
    assert inspection.selected_invocation_count == 1
    assert inspection.selected_invocation is selected
    assert inspection.execution_scope is not None
    assert inspection.execution_scope.step_id == "live-east"
    assert inspection.execution_scope.invocation_id == selected.invocation_id
    other = next(item for item in inspection.planned_descriptors if item is not inspection.descriptor)
    assert other.step_id == "live-west"
    assert (
        other.execution_state
        is MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
    )
    assert inspection.execution_scope.invocation_id != other.invocation_id
    assert inspection.multi_invocation_execution_supported is False
    assert inspection.planning_succeeded is True
    assert inspection.execution_authorized is False
    assert all(step.ready_to_execute is True for step in plan.steps)
    assert inspection.execution_scope.execution_permission_granted is False
    with pytest.raises(ContractValidationError, match="multiple_invocations_rejected"):
        inspect_master_execution_handoff(
            registry,
            record,
            invocation=(plan.steps[0].invocation, plan.steps[1].invocation),
        )
    assert registry.selected_invocation(record) is selected
    _assert_execution_unavailable(inspection)


def test_unresolved_dependency_stays_pending_without_execution_permission():
    registry, record = _open_selected(
        _history(),
        _live("live-dep", depends_on=("hist-1",)),
        select="live-dep",
    )
    plan = registry.plan_for(record)
    inspection = inspect_master_execution_handoff(registry, record)
    scope = inspection.execution_scope
    gates = inspection.gate_inspection
    assert scope is not None and gates is not None
    assert inspection.planned_step_ids == tuple(step.step_id for step in plan.steps)
    assert inspection.planned_dependencies == tuple(
        (step.step_id, dependency) for step in plan.steps for dependency in step.depends_on
    )
    assert ("live-dep", "hist-1") in inspection.planned_dependencies
    assert scope.depends_on == ("hist-1",)
    assert scope.structural_ready_to_execute is False
    assert MasterRouteGate.DEPENDENCY_BLOCKED in scope.gates
    blocked = [
        item
        for item in gates.findings
        if item.original_gate is MasterRouteGate.DEPENDENCY_BLOCKED
    ]
    assert len(blocked) == 1
    assert blocked[0].step_id == "live-dep"
    assert blocked[0].effective_state is MasterGateEffectiveState.UNRESOLVED
    assert (
        blocked[0].reason
        is MasterGateResolutionReason.DEPENDENCY_PENDING_WITHOUT_TRUSTED_EXECUTION_EVIDENCE
    )
    assert blocked[0].authoritative_satisfaction is False
    assert blocked[0].dependency_terminal is False
    assert gates.dependency_edges[0].dependency_step_id == "hist-1"
    assert gates.dependency_edges[0].authoritative_terminal is False
    assert gates.dependency_edges[0].trusted_execution_evidence is False
    assert gates.dependency_edges[0].evidence_sufficient is False
    assert gates.observed_candidates == ()
    assert inspection.selected_invocation_count == 1
    assert len(inspection.planned_step_ids) == 2
    assert inspection.multi_invocation_execution_supported is False
    assert inspection.execution_authorized is False
    assert scope.execution_permission_granted is False
    assert inspection.preflight is not None
    assert inspection.preflight.bundle.evidence_inputs == ()
    _assert_execution_unavailable(inspection)


@pytest.mark.parametrize(
    ("proposal_steps", "capabilities", "outcome"),
    [
        (
            (),
            (MasterProposedCapability(ALTITUDE, MasterRequirement.REQUIRED),),
            MasterRouteOutcome.REFUSED,
        ),
        (
            (),
            (MasterProposedCapability(VISUALIZATION, MasterRequirement.REQUIRED),),
            MasterRouteOutcome.UNSUPPORTED,
        ),
        (
            (
                MasterProposedStep(
                    step_id="hist-1",
                    specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
                    purpose=MasterPurpose.ANSWER,
                    requirement=MasterRequirement.REQUIRED,
                    capability=HISTORY,
                    temporal_intent=MasterRouteTemporalIntent.HISTORICAL,
                ),
            ),
            (MasterProposedCapability(HISTORY, MasterRequirement.REQUIRED),),
            MasterRouteOutcome.CLARIFICATION_REQUIRED,
        ),
    ],
)
def test_policy_outcomes_do_not_fabricate_a_plan(proposal_steps, capabilities, outcome):
    registry = MasterPlanningSessionRegistry()
    proposal = MasterRouteProposal(user_text=TEXT, capabilities=capabilities, steps=proposal_steps)
    record = registry.open_session(proposal, _context())
    decision = registry.decision_for(record)
    assert decision.outcome is outcome
    assert decision.plan is None
    inspection = inspect_master_execution_handoff(registry, record)
    assert inspection.outcome is outcome
    assert inspection.planning_succeeded is False
    assert inspection.plan is None
    assert inspection.selected_invocation is None
    assert inspection.selected_invocation_count == 0
    assert inspection.descriptor is None
    assert inspection.planned_descriptors == ()
    assert inspection.planned_step_ids == ()
    assert inspection.planned_dependencies == ()
    assert inspection.execution_scope is None
    assert inspection.gate_inspection is None
    assert inspection.preflight is None
    assert inspection.decision is decision
    _assert_execution_unavailable(inspection)

    other_registry, other_record = _open_selected(_live(), select="live-1")
    fabricated = other_registry.plan_for(other_record)
    with pytest.raises(ContractValidationError, match="policy_outcome_forbids_plan"):
        inspect_master_execution_handoff(registry, record, plan=fabricated)
    with pytest.raises(ContractValidationError, match="invalid_session_state"):
        inspect_master_execution_handoff(registry, record, invocation=fabricated.steps[0].invocation)
    assert registry.decision_for(record) is decision
    assert registry.decision_for(record).plan is None


def test_copied_invocation_is_rejected():
    registry, record = _open_selected(_live(), select="live-1")
    selected = registry.selected_invocation(record)
    copied = MasterInvocationAuthorization.from_dict(selected.to_dict())
    assert copied == selected
    assert copied is not selected
    with pytest.raises(ContractValidationError, match="copied_invocation_identifier"):
        inspect_master_execution_handoff(registry, record, invocation=copied)
    with pytest.raises(ContractValidationError, match="copied_invocation_identifier"):
        inspect_master_execution_handoff(registry, record, invocation=selected.invocation_id)
    assert registry.selected_invocation(record) is selected
    inspection = inspect_master_execution_handoff(registry, record, invocation=selected)
    assert inspection.selected_invocation is selected
    assert inspection.execution_authorized is False


def test_foreign_and_copied_sessions_are_rejected():
    registry, record = _open_selected(_live(), select="live-1")
    context = registry.context_for(record)
    proposal = registry.proposal_for(record)
    other = MasterPlanningSessionRegistry()
    foreign = other.open_session(proposal, context)
    with pytest.raises(ContractValidationError, match="foreign_session"):
        inspect_master_execution_handoff(registry, foreign)
    copied = MasterPlanningSessionRecord(record.session_id, object())
    with pytest.raises(ContractValidationError, match="copied_session_identifier"):
        inspect_master_execution_handoff(registry, copied)
    assert other.decision_for(foreign).execution_authority is MasterRouteExecutionAuthority.DENIED
    assert registry.selected_invocation(record) is registry.plan_for(record).steps[0].invocation


def test_closed_session_is_rejected():
    registry, record = _open_selected(_live(), select="live-1")
    plan = registry.plan_for(record)
    registry.close(record)
    assert registry.state(record) is MasterPlanningSessionState.CLOSED
    with pytest.raises(ContractValidationError, match="session_closed"):
        inspect_master_execution_handoff(registry, record)
    assert plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    registry.invalidate(record)
    with pytest.raises(ContractValidationError, match="session_invalidated"):
        inspect_master_execution_handoff(registry, record)


def test_reconstructed_plan_and_decision_are_rejected():
    registry, record = _open_selected(_live(), select="live-1")
    decision = registry.decision_for(record)
    plan = registry.plan_for(record)
    assert plan is not None
    reconstructed_plan = replace(plan)
    deserialized_plan = MasterRoutePlan.from_dict(plan.to_dict())
    assert reconstructed_plan == plan
    assert reconstructed_plan is not plan
    assert deserialized_plan == plan
    assert deserialized_plan is not plan
    with pytest.raises(ContractValidationError, match="reconstructed_plan_rejected"):
        inspect_master_execution_handoff(registry, record, plan=reconstructed_plan)
    with pytest.raises(ContractValidationError, match="reconstructed_plan_rejected"):
        inspect_master_execution_handoff(registry, record, plan=deserialized_plan)
    with pytest.raises(ContractValidationError, match="deserialized_plan_rejected"):
        inspect_master_execution_handoff(registry, record, plan=plan.to_dict())
    rebuilt = MasterRouteDecision.from_dict(decision.to_dict())
    assert rebuilt.to_dict() == decision.to_dict()
    assert rebuilt is not decision
    with pytest.raises(ContractValidationError, match="reconstructed_decision_rejected"):
        inspect_master_execution_handoff(registry, record, decision=rebuilt)
    with pytest.raises(ContractValidationError, match="deserialized_decision_rejected"):
        inspect_master_execution_handoff(registry, record, decision=decision.to_dict())
    inspection = inspect_master_execution_handoff(registry, record, decision=decision, plan=plan)
    assert inspection.plan is plan
    assert inspection.decision is decision
    assert inspection.execution_authorized is False


def test_descriptor_mismatch_is_rejected():
    registry, record = _open_selected(_live("live-east"), _live("live-west"), select="live-east")
    inspection = inspect_master_execution_handoff(registry, record)
    assert inspection.descriptor is not None
    mismatched = replace(inspection.descriptor, step_id="live-west")
    assert mismatched != inspection.descriptor
    with pytest.raises(ContractValidationError, match="descriptor_mismatch"):
        inspect_master_execution_handoff(registry, record, descriptor=mismatched)
    other = next(item for item in inspection.planned_descriptors if item.step_id == "live-west")
    with pytest.raises(ContractValidationError, match="descriptor_mismatch"):
        inspect_master_execution_handoff(registry, record, descriptor=other)
    with pytest.raises(ContractValidationError, match="invalid_adapter_descriptor"):
        inspect_master_execution_handoff(registry, record, descriptor=object())
    accepted = inspect_master_execution_handoff(registry, record, descriptor=inspection.descriptor)
    assert accepted.descriptor == inspection.descriptor
    assert accepted.execution_scope is not None
    assert accepted.execution_scope.descriptor is accepted.descriptor
    assert accepted.execution_authorized is False


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"production_authority": MasterProductionAuthority.PRODUCTION}, "production_authority_not_issued"),
        ({"execution_authority": "GRANTED"}, "execution_authority_not_granted"),
        ({"source_authenticity": "AUTHENTICATED"}, "source_authenticity_not_established"),
        ({"request_coverage": "ESTABLISHED"}, "request_coverage_not_established"),
        ({"execution_authorized": True}, "execution_not_granted"),
        ({"executable": True}, "execution_not_granted"),
        ({"dispatch_permitted": True}, "execution_not_granted"),
        ({"execution_permission_granted": True}, "execution_permission_not_granted"),
        ({"redeemable": True}, "scope_not_redeemable"),
        ({"authoritative_satisfaction": True}, "authoritative_satisfaction_not_available"),
        ({"provenance_authenticated": True}, "source_authenticity_not_established"),
        ({"factual_response_available": True}, "operational_claim_not_admitted"),
        ({"multi_invocation_execution_supported": True}, "multiple_invocations_rejected"),
    ],
)
def test_authority_promotion_is_rejected(kwargs: dict[str, object], code: str):
    registry, record = _open_selected(_live(), select="live-1")
    plan = registry.plan_for(record)
    with pytest.raises(ContractValidationError, match=code):
        inspect_master_execution_handoff(registry, record, **kwargs)
    assert registry.plan_for(record) is plan
    inspection = inspect_master_execution_handoff(registry, record)
    assert inspection.planning_succeeded is True
    assert inspection.execution_authorized is False
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(inspection, execution_authorized=True)
    with pytest.raises(ContractValidationError, match="multiple_invocations_rejected"):
        replace(inspection, multi_invocation_execution_supported=True)


def test_unselected_plan_is_not_treated_as_an_execution_handoff():
    registry = MasterPlanningSessionRegistry()
    record = registry.open_session(_proposal(_live()), _context())
    assert registry.decision_for(record).outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    with pytest.raises(ContractValidationError, match="invalid_session_state"):
        inspect_master_execution_handoff(registry, record)
    assert registry.state(record) is MasterPlanningSessionState.OPEN


def test_repeated_handoff_is_deterministic_and_preserves_the_plan():
    registry, record = _open_selected(
        _history(),
        _live("live-dep", depends_on=("hist-1",)),
        select="hist-1",
    )
    plan = registry.plan_for(record)
    before = plan.to_dict()
    first = inspect_master_execution_handoff(registry, record)
    second = inspect_master_execution_handoff(registry, record)
    assert first == second
    assert first.plan is plan
    assert first.gate_inspection is not None
    assert first.gate_inspection.plan is plan
    assert plan.to_dict() == before
    assert first.planned_step_ids == tuple(step.step_id for step in plan.steps)
    assert first.selected_invocation_count == 1
    assert len(first.planned_step_ids) == 2
    assert first.multi_invocation_execution_supported is False
    assert first.planning_succeeded is True
    assert first.execution_authorized is False
    assert first.preflight is not None
    assert first.preflight.bundle.evidence_inputs == ()
    assert first.preflight.bundle.comparison_requests == ()
    _assert_execution_unavailable(first)
