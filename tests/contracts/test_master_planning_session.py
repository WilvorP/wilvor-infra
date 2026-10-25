"""Offline tests for the process-local planning session.

The session opener below is a test helper. It is not an authentication
entry point. Object identity in these tests checks registry membership
inside this process. It is not production authentication.
"""

from __future__ import annotations

import inspect
import threading
from dataclasses import replace

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.master_contracts import MasterInvocationAuthorization, MasterPurpose, MasterRequirement, MasterSpecialist
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_planning_session import (
    MASTER_PLANNING_SESSION_SCHEMA_VERSION,
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
    MasterRouteOutcome,
    MasterRoutePlan,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)


TEXT = "Which aircraft are currently affected by hazards, and what recorded risks apply?"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
ALTITUDE = AgentCapability.ISSUE_ALTITUDE_INSTRUCTIONS


def _context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "correlation_id": "corr-1",
        "application_request_time_utc": "2026-01-15T00:00:01Z",
        "canonical_user_text": TEXT,
    }
    values.update(overrides)
    return MasterPlanningContext(**values)  # type: ignore[arg-type]


def _work(*capabilities: AgentCapability) -> tuple[MasterProposedCapability, ...]:
    return tuple(MasterProposedCapability(item, MasterRequirement.REQUIRED) for item in capabilities)


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


def _live(step_id: str = "live-1") -> MasterProposedStep:
    return _step(
        step_id,
        MasterSpecialist.LIVE_OPS,
        MasterPurpose.ANSWER,
        LIVE,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
    )


def _history(step_id: str = "hist-1") -> MasterProposedStep:
    return _step(
        step_id,
        MasterSpecialist.HISTORICAL_ANALYTICS,
        MasterPurpose.ANSWER,
        HISTORY,
        temporal_intent=MasterRouteTemporalIntent.HISTORICAL,
        historical_start_utc="2026-01-01T00:00:00Z",
        historical_end_utc="2026-01-02T00:00:00Z",
    )


def _proposal(
    capabilities: tuple[MasterProposedCapability, ...],
    steps: tuple[MasterProposedStep, ...] = (),
    user_text: str = TEXT,
) -> MasterRouteProposal:
    return MasterRouteProposal(user_text=user_text, capabilities=capabilities, steps=steps)


def _open_live(
    registry: MasterPlanningSessionRegistry | None = None,
) -> tuple[MasterPlanningSessionRegistry, MasterPlanningSessionRecord, MasterPlanningContext, MasterRouteProposal]:
    """Test-only opener. This helper does not authenticate a caller."""

    registry = MasterPlanningSessionRegistry() if registry is None else registry
    context = _context()
    proposal = _proposal(_work(LIVE), (_live(),))
    return registry, registry.open_session(proposal, context), context, proposal


def test_schema_and_denial_posture_are_fixed():
    assert MASTER_PLANNING_SESSION_SCHEMA_VERSION == "wilvor.ai.master_planning_session.v1"
    registry, record, _, _ = _open_live()
    assert record.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert record.production_authority.value == "DENIED"
    assert record.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert record.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert not hasattr(MasterRouteExecutionAuthority, "PRODUCTION")
    assert not hasattr(MasterPlanningSessionRegistry, "open_authenticated")
    assert not hasattr(MasterPlanningSessionRegistry, "from_dict")
    assert not hasattr(MasterPlanningSessionRecord, "from_dict")
    assert not hasattr(registry, "dispatch")
    decision = registry.decision_for(record)
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert decision.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED


def test_registry_mints_a_session_id_and_retains_the_exact_result():
    registry, record, context, proposal = _open_live()
    assert record.session_id != context.master_request_id
    assert record.session_id != context.correlation_id
    assert len(record.session_id) == 32
    assert registry.state(record) is MasterPlanningSessionState.OPEN
    assert registry.context_for(record) is context
    assert registry.proposal_for(record) is proposal
    decision = registry.decision_for(record)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert registry.plan_for(record) is decision.plan
    assert registry.require_retained_decision(record, decision) is decision
    assert registry.require_retained_plan(record, decision.plan) is decision.plan
    again = registry.open_session(proposal, context)
    assert again is not record
    assert again.session_id != record.session_id
    assert registry.decision_for(record) is decision
    assert registry.decision_for(again) is not decision


def test_selected_invocation_is_the_retained_object_and_stays_execution_denied():
    registry, record, _, _ = _open_live()
    plan = registry.plan_for(record)
    invocation = plan.steps[0].invocation
    assert plan.steps[0].ready_to_execute is True
    selected = registry.associate_invocation(record, invocation)
    assert selected is invocation
    assert registry.selected_invocation(record) is invocation
    assert registry.decision_for(record).execution_authority is MasterRouteExecutionAuthority.DENIED
    assert plan.steps[0].target_authorization_granted is False
    assert record.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert record.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED


def test_blocked_decision_step_can_be_selected_without_granting_execution():
    registry = MasterPlanningSessionRegistry()
    proposal = _proposal(
        _work(RISK),
        (
            _step(
                "dec-1",
                MasterSpecialist.DECISION,
                MasterPurpose.ANSWER,
                RISK,
                temporal_intent=MasterRouteTemporalIntent.CURRENT,
            ),
        ),
    )
    record = registry.open_session(proposal, _context(target_candidate_aircraft_id="ac-1"))
    step = registry.plan_for(record).steps[0]
    assert step.ready_to_execute is False
    assert step.target_authorization_granted is False
    selected = registry.associate_invocation(record, step.invocation)
    assert selected is step.invocation
    assert selected.expected_aircraft_id == "ac-1"
    assert registry.decision_for(record).execution_authority is MasterRouteExecutionAuthority.DENIED
    assert record.production_authority.value == "DENIED"


def test_second_plan_does_not_replace_the_retained_result():
    registry, record, context, proposal = _open_live()
    retained = registry.decision_for(record)
    other = _proposal(_work(LIVE, HISTORY), (_live(), _history()))
    with pytest.raises(ContractValidationError, match="retained_plan_not_replaced"):
        registry.retain_route(record, other, context)
    with pytest.raises(ContractValidationError, match="retained_plan_not_replaced"):
        registry.retain_route(record, proposal, context)
    assert registry.decision_for(record) is retained
    assert registry.proposal_for(record) is proposal
    assert registry.context_for(record) is context
    assert [step.invocation.specialist for step in retained.plan.steps] == [MasterSpecialist.LIVE_OPS]


def test_failed_authorization_does_not_create_a_session():
    registry = MasterPlanningSessionRegistry()
    proposal = _proposal(_work(LIVE), (_live(),), user_text="A different operational question")
    with pytest.raises(ContractValidationError, match="user_text_mismatch"):
        registry.open_session(proposal, _context())
    assert registry._sessions == {}
    assert registry._known_ids == set()


def test_forged_plan_object_is_rejected():
    registry, record, _, _ = _open_live()

    class ForgedPlan:
        execution_authority = "PRODUCTION"
        master_request_id = "master-req-1"

    with pytest.raises(ContractValidationError, match="forged_plan_rejected"):
        registry.require_retained_plan(record, ForgedPlan())
    with pytest.raises(ContractValidationError, match="forged_decision_rejected"):
        registry.require_retained_decision(record, object())
    assert registry.decision_for(record).execution_authority is MasterRouteExecutionAuthority.DENIED


def test_deserialized_and_reconstructed_plans_are_not_substitutes():
    registry, record, context, proposal = _open_live()
    retained = registry.decision_for(record)
    plan = retained.plan
    assert plan is not None
    payload = retained.to_dict()
    with pytest.raises(ContractValidationError, match="deserialized_decision_rejected"):
        registry.require_retained_decision(record, payload)
    with pytest.raises(ContractValidationError, match="deserialized_plan_rejected"):
        registry.require_retained_plan(record, plan.to_dict())
    deserialized = MasterRouteDecision.from_dict(payload)
    deserialized_plan = MasterRoutePlan.from_dict(plan.to_dict())
    assert deserialized.to_dict() == retained.to_dict()
    assert deserialized_plan.to_dict() == plan.to_dict()
    with pytest.raises(ContractValidationError, match="reconstructed_decision_rejected"):
        registry.require_retained_decision(record, deserialized)
    with pytest.raises(ContractValidationError, match="reconstructed_plan_rejected"):
        registry.require_retained_plan(record, deserialized_plan)
    rebuilt = authorize_route(proposal, context)
    assert rebuilt.to_dict() == retained.to_dict()
    assert rebuilt is not retained
    with pytest.raises(ContractValidationError, match="reconstructed_decision_rejected"):
        registry.require_retained_decision(record, rebuilt)
    reconstructed_plan = replace(plan)
    assert reconstructed_plan is not plan
    assert reconstructed_plan.to_dict() == plan.to_dict()
    with pytest.raises(ContractValidationError, match="reconstructed_plan_rejected"):
        registry.require_retained_plan(record, reconstructed_plan)
    assert registry.decision_for(record) is retained


def test_copied_session_identifier_is_not_membership():
    registry, record, _, _ = _open_live()
    retained = registry.decision_for(record)
    copied_id = MasterPlanningSessionRecord(record.session_id, object())
    with pytest.raises(ContractValidationError, match="copied_session_identifier"):
        registry.decision_for(copied_id)
    copied_marker = MasterPlanningSessionRecord("another-label", record._membership)
    with pytest.raises(ContractValidationError, match="copied_session_identifier"):
        registry.state(copied_marker)
    record._session_id = "mutated-label"
    assert registry.decision_for(record) is retained
    assert registry.decision_for(record).execution_authority is MasterRouteExecutionAuthority.DENIED
    with pytest.raises(AttributeError):
        record.execution_authority = MasterRouteExecutionAuthority.DENIED  # type: ignore[misc]


def test_caller_supplied_string_is_not_a_principal():
    with pytest.raises(ContractValidationError, match="session_principal_not_accepted"):
        MasterPlanningSessionRecord("master-req-1", "caller-1")
    with pytest.raises(ContractValidationError, match="session_principal_not_accepted"):
        MasterPlanningSessionRecord("master-req-1", b"caller-1")
    registry = MasterPlanningSessionRegistry()
    forged = MasterPlanningSessionRecord("not-a-member", object())
    with pytest.raises(ContractValidationError, match="foreign_session"):
        registry.decision_for(forged)
    with pytest.raises(ContractValidationError, match="invalid_session"):
        registry.state(object())


def test_foreign_session_is_rejected():
    registry, record, context, proposal = _open_live()
    invocation = registry.plan_for(record).steps[0].invocation
    other = MasterPlanningSessionRegistry()
    foreign = other.open_session(proposal, context)
    with pytest.raises(ContractValidationError, match="foreign_session"):
        registry.decision_for(foreign)
    with pytest.raises(ContractValidationError, match="foreign_session"):
        registry.associate_invocation(foreign, invocation)
    assert other.decision_for(foreign).execution_authority is MasterRouteExecutionAuthority.DENIED


def test_copied_invocation_identifier_and_wrong_invocation_are_rejected():
    registry, record, context, proposal = _open_live()
    plan = registry.plan_for(record)
    invocation = plan.steps[0].invocation
    copied = MasterInvocationAuthorization.from_dict(invocation.to_dict())
    assert copied.invocation_id == invocation.invocation_id
    assert copied is not invocation
    with pytest.raises(ContractValidationError, match="copied_invocation_identifier"):
        registry.associate_invocation(record, copied)
    with pytest.raises(ContractValidationError, match="copied_invocation_identifier"):
        registry.associate_invocation(record, invocation.invocation_id)
    other = authorize_route(proposal, _context(master_request_id="master-req-2", correlation_id="corr-2"))
    assert other.plan is not None
    with pytest.raises(ContractValidationError, match="wrong_invocation"):
        registry.associate_invocation(record, other.plan.steps[0].invocation)
    with pytest.raises(ContractValidationError, match="forged_invocation_rejected"):
        registry.associate_invocation(record, object())
    assert registry.plan_for(record).steps[0].invocation is invocation
    with pytest.raises(ContractValidationError, match="invalid_session_state"):
        registry.selected_invocation(record)


def test_multiple_invocations_do_not_replace_the_selected_one():
    registry = MasterPlanningSessionRegistry()
    context = _context()
    proposal = _proposal(_work(LIVE, HISTORY), (_live(), _history()))
    record = registry.open_session(proposal, context)
    plan = registry.plan_for(record)
    assert len(plan.steps) == 2
    first, second = plan.steps[0].invocation, plan.steps[1].invocation
    assert first is not second
    selected = registry.associate_invocation(record, first)
    assert selected is first
    with pytest.raises(ContractValidationError, match="multiple_invocations_rejected"):
        registry.associate_invocation(record, second)
    with pytest.raises(ContractValidationError, match="multiple_invocations_rejected"):
        registry.associate_invocation(record, (first, second))
    assert registry.selected_invocation(record) is first
    assert registry.decision_for(record).execution_authority is MasterRouteExecutionAuthority.DENIED


def test_concurrent_association_keeps_a_single_invocation():
    registry = MasterPlanningSessionRegistry()
    record = registry.open_session(
        _proposal(_work(LIVE, HISTORY), (_live(), _history())),
        _context(),
    )
    plan = registry.plan_for(record)
    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, object]] = []

    def attempt(invocation: MasterInvocationAuthorization) -> None:
        barrier.wait()
        try:
            outcomes.append(("ok", registry.associate_invocation(record, invocation)))
        except ContractValidationError as exc:
            outcomes.append(("err", exc.errors))

    threads = [threading.Thread(target=attempt, args=(step.invocation,)) for step in plan.steps]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(item[0] for item in outcomes) == ["err", "ok"]
    errors = [item[1] for item in outcomes if item[0] == "err"]
    assert errors == [("multiple_invocations_rejected",)]
    assert registry.selected_invocation(record) in {step.invocation for step in plan.steps}
    assert registry.decision_for(record).plan is plan
    assert record.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_closed_session_rejects_use_of_the_retained_plan():
    registry, record, _, _ = _open_live()
    plan = registry.plan_for(record)
    invocation = plan.steps[0].invocation
    registry.close(record)
    assert registry.state(record) is MasterPlanningSessionState.CLOSED
    with pytest.raises(ContractValidationError, match="session_closed"):
        registry.decision_for(record)
    with pytest.raises(ContractValidationError, match="session_closed"):
        registry.require_retained_plan(record, plan)
    with pytest.raises(ContractValidationError, match="session_closed"):
        registry.associate_invocation(record, invocation)
    with pytest.raises(ContractValidationError, match="session_closed"):
        registry.retain_route(record, _proposal(_work(LIVE), (_live(),)), _context())
    with pytest.raises(ContractValidationError, match="invalid_session_state"):
        registry.close(record)
    assert plan.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_invalidation_drops_the_retained_result():
    registry, record, _, _ = _open_live()
    held = registry.decision_for(record)
    registry.associate_invocation(record, held.plan.steps[0].invocation)
    registry.close(record)
    registry.invalidate(record)
    assert registry.state(record) is MasterPlanningSessionState.INVALIDATED
    assert registry._sessions[id(record)].decision is None
    assert registry._sessions[id(record)].selected_invocation is None
    with pytest.raises(ContractValidationError, match="session_invalidated"):
        registry.decision_for(record)
    with pytest.raises(ContractValidationError, match="session_invalidated"):
        registry.plan_for(record)
    with pytest.raises(ContractValidationError, match="session_invalidated"):
        registry.selected_invocation(record)
    with pytest.raises(ContractValidationError, match="invalid_session_state"):
        registry.invalidate(record)
    with pytest.raises(ContractValidationError, match="invalid_session_state"):
        registry.close(record)
    assert held.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert held.plan.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_open_session_invalidation_and_invalid_outcomes():
    registry = MasterPlanningSessionRegistry()
    refused = registry.open_session(_proposal(_work(ALTITUDE)), _context())
    refusal = registry.decision_for(refused)
    assert refusal.outcome is MasterRouteOutcome.REFUSED
    assert refusal.plan is None
    assert refusal.execution_authority is MasterRouteExecutionAuthority.DENIED
    with pytest.raises(ContractValidationError, match="invalid_session_state"):
        registry.plan_for(refused)
    with pytest.raises(ContractValidationError, match="invalid_session_state"):
        registry.associate_invocation(refused, object())
    clarify = registry.open_session(
        _proposal(
            _work(HISTORY),
            (
                _step(
                    "hist-1",
                    MasterSpecialist.HISTORICAL_ANALYTICS,
                    MasterPurpose.ANSWER,
                    HISTORY,
                    temporal_intent=MasterRouteTemporalIntent.HISTORICAL,
                ),
            ),
        ),
        _context(),
    )
    assert registry.decision_for(clarify).outcome is MasterRouteOutcome.CLARIFICATION_REQUIRED
    with pytest.raises(ContractValidationError, match="invalid_session_state"):
        registry.plan_for(clarify)
    registry.invalidate(refused)
    assert registry.state(refused) is MasterPlanningSessionState.INVALIDATED
    with pytest.raises(ContractValidationError, match="session_invalidated"):
        registry.decision_for(refused)


def test_tampered_authority_invalidates_the_session():
    registry, record, _, _ = _open_live()
    decision = registry.decision_for(record)
    object.__setattr__(decision, "execution_authority", "PRODUCTION")
    with pytest.raises(ContractValidationError, match="execution_authority_not_granted"):
        registry.decision_for(record)
    assert registry.state(record) is MasterPlanningSessionState.INVALIDATED
    with pytest.raises(ContractValidationError, match="session_invalidated"):
        registry.plan_for(record)
    assert record.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert record.production_authority.value == "DENIED"
    assert record.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED


def test_evidence_bundles_are_not_retained():
    registry = MasterPlanningSessionRegistry()

    class MasterParallelContractBundle:
        evidence_inputs = ()

    class Carrier:
        comparison_requests = ()

    with pytest.raises(ContractValidationError, match="evidence_bundle_not_retained"):
        registry.open_session(MasterParallelContractBundle(), _context())  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="evidence_bundle_not_retained"):
        registry.open_session(_proposal(_work(LIVE), (_live(),)), Carrier())  # type: ignore[arg-type]
    _, record, _, _ = _open_live(registry)
    slot = registry._sessions[id(record)]
    assert type(slot.context).__name__ == "MasterPlanningContext"
    assert type(slot.proposal).__name__ == "MasterRouteProposal"
    assert type(slot.decision).__name__ == "MasterRouteDecision"
    assert not hasattr(slot, "evidence_inputs")
    assert slot.decision.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_open_session_takes_only_a_proposal_and_context():
    assert tuple(inspect.signature(MasterPlanningSessionRegistry.open_session).parameters) == (
        "self",
        "proposal",
        "context",
    )
    registry = MasterPlanningSessionRegistry()
    with pytest.raises(TypeError):
        registry.open_session(_proposal(_work(LIVE), (_live(),)), _context(), "caller-1")  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="proposal is required"):
        registry.open_session(None, _context())  # type: ignore[arg-type]
