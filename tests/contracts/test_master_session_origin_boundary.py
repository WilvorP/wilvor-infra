"""Origin and fanout diagnostics. They do not authenticate a principal."""

from __future__ import annotations

from dataclasses import replace

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
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.master_session_origin_boundary import (
    AUTHENTICATED_APPLICATION_PRINCIPAL_AVAILABLE,
    AUTHORIZED_MULTI_INVOCATION_EXECUTION_AVAILABLE,
    INVOCATION_SELECTION_LIMIT,
    MASTER_SESSION_ORIGIN_BOUNDARY_SCHEMA_VERSION,
    SESSION_ORIGIN_LIMITATIONS,
    ApplicationOwnedSessionAuthorityRequirement,
    AuthenticationClaimKind,
    DecisionIdentityStanding,
    InvocationSelectionStanding,
    PerInvocationSelectionRequirement,
    ProposedInvocationFanout,
    RegistryMembershipStanding,
    SessionOriginConcept,
    SessionOriginLimitation,
    SessionOriginStanding,
    assess_session_origin,
    classify_presented_invocation,
    propose_invocation_fanout,
    reject_authentication_claim,
    unmet_application_session_authority,
)
from wilvor_ai.model_contracts import ModelDecision, ModelDecisionKind, ModelTurnRequest


TEXT = "Which aircraft are currently affected by hazards, and what recorded risks apply?"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
ALTITUDE = AgentCapability.ISSUE_ALTITUDE_INSTRUCTIONS

_CONCEPTS = (
    SessionOriginConcept.CALLER_SUPPLIED_PLANNING_CONTEXT,
    SessionOriginConcept.PROCESS_LOCAL_REGISTRY_MEMBERSHIP,
    SessionOriginConcept.RETAINED_ROUTE_DECISION_IDENTITY,
    SessionOriginConcept.AUTHENTICATED_APPLICATION_PRINCIPAL,
    SessionOriginConcept.AUTHORIZED_MULTI_INVOCATION_EXECUTION_SESSION,
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


def _live(step_id: str = "live-1", purpose: MasterPurpose = MasterPurpose.ANSWER) -> MasterProposedStep:
    return _step(
        step_id,
        MasterSpecialist.LIVE_OPS,
        purpose,
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


def _decision(step_id: str = "dec-1") -> MasterProposedStep:
    return _step(
        step_id,
        MasterSpecialist.DECISION,
        MasterPurpose.ANSWER,
        RISK,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
    )


def _proposal(
    capabilities: tuple[MasterProposedCapability, ...],
    steps: tuple[MasterProposedStep, ...] = (),
) -> MasterRouteProposal:
    return MasterRouteProposal(user_text=TEXT, capabilities=capabilities, steps=steps)


def _open(
    steps: tuple[MasterProposedStep, ...],
    capabilities: tuple[MasterProposedCapability, ...],
    **context_overrides: object,
):
    registry = MasterPlanningSessionRegistry()
    context = _context(**context_overrides)
    proposal = _proposal(capabilities, steps)
    record = registry.open_session(proposal, context)
    return registry, record, context, proposal


def _finding(assessment: object, concept: SessionOriginConcept):
    findings = getattr(assessment, "findings")
    return next(item for item in findings if item.concept is concept)


def _assert_closed_authority(assessment: object) -> None:
    assert assessment.execution_authority is MasterRouteExecutionAuthority.DENIED  # type: ignore[attr-defined]
    assert assessment.production_authority is MasterProductionAuthority.DENIED  # type: ignore[attr-defined]
    assert assessment.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED  # type: ignore[attr-defined]
    assert assessment.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED  # type: ignore[attr-defined]
    assert assessment.authenticated_application_principal is False  # type: ignore[attr-defined]
    assert assessment.authorized_multi_invocation_execution_session is False  # type: ignore[attr-defined]
    assert assessment.dispatch_permitted is False  # type: ignore[attr-defined]
    assert assessment.end_to_end_fulfilled is False  # type: ignore[attr-defined]
    assert assessment.limitations == SESSION_ORIGIN_LIMITATIONS  # type: ignore[attr-defined]
    assert assessment.fanout.fanout_authorized is False  # type: ignore[attr-defined]
    assert assessment.fanout.trusted is False  # type: ignore[attr-defined]
    assert assessment.fanout.executes_every_planned_invocation is False  # type: ignore[attr-defined]
    assert assessment.fanout.preserves_original_planning_identity is True  # type: ignore[attr-defined]
    assert assessment.fanout.preserves_original_plan is True  # type: ignore[attr-defined]
    assert assessment.authority_requirement.authority_issued is False  # type: ignore[attr-defined]
    assert assessment.selection.selection_limit == 1  # type: ignore[attr-defined]
    assert assessment.selection.selection_authorizes_execution is False  # type: ignore[attr-defined]
    assert assessment.selection.multi_invocation_execution_available is False  # type: ignore[attr-defined]
    assert assessment.selection.second_selection_replaces_first is False  # type: ignore[attr-defined]
    principal = _finding(assessment, SessionOriginConcept.AUTHENTICATED_APPLICATION_PRINCIPAL)
    execution = _finding(
        assessment,
        SessionOriginConcept.AUTHORIZED_MULTI_INVOCATION_EXECUTION_SESSION,
    )
    assert principal.standing is SessionOriginStanding.NOT_ESTABLISHED
    assert execution.standing is SessionOriginStanding.NOT_ESTABLISHED
    assert principal.establishes_authenticated_principal is False
    assert execution.establishes_authorized_execution_session is False
    for concept in _CONCEPTS[:3]:
        finding = _finding(assessment, concept)
        assert finding.establishes_authenticated_principal is False
        assert finding.establishes_authorized_execution_session is False


def test_schema_and_denial_posture_are_fixed():
    assert MASTER_SESSION_ORIGIN_BOUNDARY_SCHEMA_VERSION == (
        "wilvor.ai.master_session_origin_boundary.v1"
    )
    assert AUTHENTICATED_APPLICATION_PRINCIPAL_AVAILABLE is False
    assert AUTHORIZED_MULTI_INVOCATION_EXECUTION_AVAILABLE is False
    assert INVOCATION_SELECTION_LIMIT == 1
    assert not hasattr(MasterRouteExecutionAuthority, "PRODUCTION")
    assert MasterRouteExecutionAuthority.DENIED.value == "DENIED"
    assert MasterProductionAuthority.DENIED.value == "DENIED"
    assert MasterSourceAuthenticity.NOT_ESTABLISHED.value == "NOT_ESTABLISHED"
    assert SessionOriginLimitation.ONE_INVOCATION_SELECTION_ONLY in SESSION_ORIGIN_LIMITATIONS
    assert SessionOriginLimitation.PROPOSED_FANOUT_IS_NOT_AUTHORIZATION in SESSION_ORIGIN_LIMITATIONS
    assert (
        SessionOriginLimitation.OBSERVED_ORIGIN_DOES_NOT_ESTABLISH_PRINCIPAL
        in SESSION_ORIGIN_LIMITATIONS
    )
    authority = unmet_application_session_authority()
    assert isinstance(authority, ApplicationOwnedSessionAuthorityRequirement)
    assert authority.authenticated_principal_required is True
    assert authority.caller_supplied_context_satisfies is False
    assert authority.process_local_membership_satisfies is False
    assert authority.retained_decision_identity_satisfies is False
    assert authority.authority_issued is False


def test_one_step_plan_observation_does_not_authenticate_or_execute():
    registry, record, context, _proposal_obj = _open((_live(),), _work(LIVE))
    plan_before = registry.plan_for(record)
    session_id = record.session_id
    assessment = assess_session_origin(
        registry,
        record,
        presented_context=context,
        presented_decision=registry.decision_for(record),
    )
    _assert_closed_authority(assessment)
    assert tuple(item.concept for item in assessment.findings) == _CONCEPTS
    assert _finding(
        assessment, SessionOriginConcept.CALLER_SUPPLIED_PLANNING_CONTEXT
    ).standing is SessionOriginStanding.OBSERVED
    assert _finding(
        assessment, SessionOriginConcept.PROCESS_LOCAL_REGISTRY_MEMBERSHIP
    ).standing is SessionOriginStanding.OBSERVED
    assert _finding(
        assessment, SessionOriginConcept.RETAINED_ROUTE_DECISION_IDENTITY
    ).standing is SessionOriginStanding.OBSERVED
    assert assessment.membership is RegistryMembershipStanding.MEMBER
    assert assessment.decision_identity is DecisionIdentityStanding.RETAINED
    assert assessment.presented_context_is_retained is True
    assert assessment.presented_decision_is_retained is True
    assert assessment.planning_context is context
    assert assessment.retained_decision is registry.decision_for(record)
    assert assessment.route_plan is plan_before
    assert assessment.session_label == session_id
    assert assessment.session_label != context.master_request_id
    assert plan_before.steps[0].ready_to_execute is True
    assert assessment.selection.planned_invocation_count == 1
    assert assessment.selection.selected_invocation_count == 0
    assert assessment.selection.repeated_specialist is False
    assert assessment.selection.unselected_invocation_ids == (
        plan_before.steps[0].invocation.invocation_id,
    )
    assert len(assessment.fanout.requirements) == 1
    requirement = assessment.fanout.requirements[0]
    assert requirement.step_id == "live-1"
    assert requirement.requirement_satisfied is False
    assert requirement.selected_for_execution is False
    assert requirement.execution_granted is False
    assert assessment.fanout.route_plan is plan_before
    again = assess_session_origin(registry, record)
    assert again.selection == assessment.selection
    assert again.findings == assessment.findings
    assert registry.plan_for(record) is plan_before
    assert record.session_id == session_id
    with pytest.raises(ContractValidationError, match="invalid_session_state"):
        registry.selected_invocation(record)


def test_one_step_candidate_is_not_selected_by_classification():
    registry, record, _, _ = _open((_live(),), _work(LIVE))
    invocation = registry.plan_for(record).steps[0].invocation
    presentation = classify_presented_invocation(registry, record, invocation)
    assert presentation.standing is InvocationSelectionStanding.ONE_CANDIDATE
    assert presentation.code == "one_selection_only"
    assert presentation.registry_would_select_one is True
    assert presentation.matches_plan_object is True
    assert presentation.execution_granted is False
    assert presentation.selected_for_execution is False
    with pytest.raises(ContractValidationError, match="invalid_session_state"):
        registry.selected_invocation(record)
    selected = registry.associate_invocation(record, invocation)
    assert selected is invocation
    assert registry.decision_for(record).execution_authority is MasterRouteExecutionAuthority.DENIED
    blocked = classify_presented_invocation(registry, record, invocation)
    assert blocked.standing is InvocationSelectionStanding.SECOND_SELECTION_REJECTED
    assert blocked.code == "multiple_invocations_rejected"
    assert blocked.selection_replaced is False
    assert blocked.registry_would_select_one is False
    assert registry.selected_invocation(record) is invocation


def test_blocked_decision_step_selection_does_not_authenticate_the_aircraft_id():
    registry, record, context, _ = _open(
        (_decision(),),
        _work(RISK),
        target_candidate_aircraft_id="ac-1",
    )
    step = registry.plan_for(record).steps[0]
    assert step.ready_to_execute is False
    assert step.invocation.expected_aircraft_id == "ac-1"
    assessment = assess_session_origin(
        registry,
        record,
        authentication_claim=step.invocation.expected_aircraft_id,
    )
    _assert_closed_authority(assessment)
    assert assessment.claim_rejection is not None
    assert assessment.claim_rejection.kind is AuthenticationClaimKind.MATCHING_IDENTIFIER
    assert assessment.claim_rejection.authenticated is False
    presentation = classify_presented_invocation(registry, record, step.invocation)
    assert presentation.registry_would_select_one is True
    assert presentation.execution_granted is False
    assert context.target_candidate_aircraft_id == "ac-1"
    assert assessment.authenticated_application_principal is False


def test_multi_step_plan_cannot_select_or_execute_every_invocation():
    registry, record, _, _ = _open((_live(), _history()), _work(LIVE, HISTORY))
    plan = registry.plan_for(record)
    session_id = record.session_id
    assert len(plan.steps) == 2
    first, second = plan.steps[0].invocation, plan.steps[1].invocation
    assessment = assess_session_origin(registry, record)
    fanout = propose_invocation_fanout(registry, record)
    _assert_closed_authority(assessment)
    assert assessment.selection.planned_invocation_count == 2
    assert assessment.selection.selected_invocation_count == 0
    assert assessment.selection.repeated_specialist is False
    assert assessment.selection.planned_invocation_ids == (
        first.invocation_id,
        second.invocation_id,
    )
    assert fanout == assessment.fanout
    assert fanout.fanout_authorized is False
    assert tuple(item.invocation_id for item in fanout.requirements) == (
        first.invocation_id,
        second.invocation_id,
    )
    assert all(item.requirement_satisfied is False for item in fanout.requirements)
    assert all(item.mints_additional_planning_identity is False for item in fanout.requirements)
    assert all(item.changes_original_plan is False for item in fanout.requirements)
    collection = classify_presented_invocation(registry, record, (first, second))
    assert collection.standing is InvocationSelectionStanding.MULTIPLE_REJECTED
    assert collection.code == "multiple_invocations_rejected"
    assert collection.registry_would_select_one is False
    single = classify_presented_invocation(registry, record, (first,))
    assert single.code == "multiple_invocations_rejected"
    assert record.session_id == session_id
    assert registry.plan_for(record) is plan
    with pytest.raises(ContractValidationError, match="invalid_session_state"):
        registry.selected_invocation(record)


def test_second_selection_does_not_replace_the_first_or_authorize_fanout():
    registry, record, _, _ = _open((_live(), _history()), _work(LIVE, HISTORY))
    plan = registry.plan_for(record)
    first, second = plan.steps[0].invocation, plan.steps[1].invocation
    assert registry.associate_invocation(record, first) is first
    assessment = assess_session_origin(registry, record)
    _assert_closed_authority(assessment)
    assert assessment.selection.selected_invocation_count == 1
    assert assessment.selection.selected_invocation_id == first.invocation_id
    assert assessment.selection.unselected_invocation_ids == (second.invocation_id,)
    assert assessment.fanout.executes_every_planned_invocation is False
    assert assessment.route_plan is plan
    rejected = classify_presented_invocation(registry, record, second)
    assert rejected.standing is InvocationSelectionStanding.SECOND_SELECTION_REJECTED
    assert rejected.matches_plan_object is True
    assert rejected.selection_replaced is False
    assert rejected.existing_selected_invocation_id == first.invocation_id
    replay = classify_presented_invocation(registry, record, first)
    assert replay.code == "multiple_invocations_rejected"
    text = classify_presented_invocation(registry, record, second.invocation_id)
    assert text.code == "multiple_invocations_rejected"
    assert registry.selected_invocation(record) is first
    assert registry.plan_for(record) is plan
    assert registry.decision_for(record).execution_authority is MasterRouteExecutionAuthority.DENIED


def test_repeated_specialist_remains_one_selection():
    registry, record, _, _ = _open(
        (_history("hist-1"), _history("hist-2")),
        _work(HISTORY),
    )
    plan = registry.plan_for(record)
    assert len(plan.steps) == 2
    assert {step.invocation.specialist for step in plan.steps} == {
        MasterSpecialist.HISTORICAL_ANALYTICS
    }
    assessment = assess_session_origin(registry, record)
    _assert_closed_authority(assessment)
    assert assessment.selection.repeated_specialist is True
    assert assessment.selection.planned_invocation_count == 2
    assert assessment.selection.selection_limit == 1
    assert assessment.fanout.repeated_specialist is True
    assert assessment.fanout.fanout_authorized is False
    both = classify_presented_invocation(
        registry,
        record,
        tuple(step.invocation for step in plan.steps),
    )
    assert both.standing is InvocationSelectionStanding.MULTIPLE_REJECTED
    live = classify_presented_invocation(registry, record, plan.steps[0].invocation)
    assert live.registry_would_select_one is True
    assert live.execution_granted is False
    assert registry.plan_for(record) is plan


def test_repeated_live_ops_purposes_do_not_create_a_trusted_fanout():
    registry, record, _, _ = _open(
        (_live("live-discovery", MasterPurpose.DISCOVERY), _live("live-answer")),
        _work(LIVE),
    )
    assessment = assess_session_origin(registry, record)
    _assert_closed_authority(assessment)
    assert assessment.selection.repeated_specialist is True
    assert assessment.selection.specialists == (
        MasterSpecialist.LIVE_OPS,
        MasterSpecialist.LIVE_OPS,
    )
    assert assessment.fanout.fanout_authorized is False
    assert all(
        item.specialist is MasterSpecialist.LIVE_OPS for item in assessment.fanout.requirements
    )


def test_additional_planning_identity_is_not_fanout_and_the_plan_is_not_replaced():
    registry, record, context, proposal = _open((_live(), _history()), _work(LIVE, HISTORY))
    retained = registry.decision_for(record)
    plan = retained.plan
    other = registry.open_session(proposal, context)
    assert other is not record
    assert other.session_id != record.session_id
    assert registry.decision_for(other) is not retained
    first = assess_session_origin(registry, record)
    second = assess_session_origin(registry, other)
    _assert_closed_authority(first)
    _assert_closed_authority(second)
    assert first.route_plan is plan
    assert second.route_plan is not plan
    assert first.session_label != second.session_label
    assert first.fanout.preserves_original_planning_identity is True
    assert second.authorized_multi_invocation_execution_session is False
    with pytest.raises(ContractValidationError, match="retained_plan_not_replaced"):
        registry.retain_route(record, proposal, context)
    assert registry.plan_for(record) is plan
    assert registry.decision_for(record) is retained


def test_copied_session_identifier_is_not_membership_or_a_principal():
    registry, record, _, _ = _open((_live(),), _work(LIVE))
    copied_id = MasterPlanningSessionRecord(record.session_id, object())
    copied = assess_session_origin(registry, copied_id)
    _assert_closed_authority(copied)
    assert copied.membership is RegistryMembershipStanding.COPIED_SESSION
    assert copied.session_label == record.session_id
    assert copied.planning_context is None
    assert copied.retained_decision is None
    assert _finding(
        copied, SessionOriginConcept.PROCESS_LOCAL_REGISTRY_MEMBERSHIP
    ).standing is SessionOriginStanding.REJECTED
    assert _finding(
        copied, SessionOriginConcept.CALLER_SUPPLIED_PLANNING_CONTEXT
    ).standing is SessionOriginStanding.REJECTED
    assert copied.decision_identity is DecisionIdentityStanding.NOT_AVAILABLE
    assert copied.decision_rejection_code == "copied_session_identifier"
    assert copied.fanout.planned_invocation_count == 0
    presentation = classify_presented_invocation(registry, copied_id, object())
    assert presentation.code == "copied_session_identifier"
    assert presentation.standing is InvocationSelectionStanding.NOT_SELECTABLE
    marker = MasterPlanningSessionRecord("another-label", record._membership)
    marked = assess_session_origin(registry, marker)
    assert marked.membership is RegistryMembershipStanding.COPIED_SESSION
    assert marked.authenticated_application_principal is False
    claim = reject_authentication_claim(record.session_id)
    assert claim.kind is AuthenticationClaimKind.MATCHING_IDENTIFIER
    assert claim.authenticated is False


def test_foreign_session_and_foreign_invocation_are_rejected():
    registry, record, context, proposal = _open((_live(),), _work(LIVE))
    invocation = registry.plan_for(record).steps[0].invocation
    other = MasterPlanningSessionRegistry()
    foreign = other.open_session(proposal, context)
    foreign_assessment = assess_session_origin(registry, foreign)
    _assert_closed_authority(foreign_assessment)
    assert foreign_assessment.membership is RegistryMembershipStanding.FOREIGN_SESSION
    assert foreign_assessment.decision_rejection_code == "foreign_session"
    assert foreign_assessment.planning_context is None
    presented = classify_presented_invocation(registry, foreign, invocation)
    assert presented.code == "foreign_session"
    separate = authorize_route(
        proposal,
        _context(master_request_id="master-req-2", correlation_id="corr-2"),
    )
    assert separate.plan is not None
    wrong = classify_presented_invocation(
        registry,
        record,
        separate.plan.steps[0].invocation,
    )
    assert wrong.standing is InvocationSelectionStanding.FOREIGN_INVOCATION
    assert wrong.code == "wrong_invocation"
    assert wrong.registry_would_select_one is False
    unknown = MasterPlanningSessionRecord("not-a-member", object())
    assert assess_session_origin(registry, unknown).membership is (
        RegistryMembershipStanding.FOREIGN_SESSION
    )


def test_copied_invocation_identifier_is_not_selection():
    registry, record, _, _ = _open((_live(),), _work(LIVE))
    invocation = registry.plan_for(record).steps[0].invocation
    copied = MasterInvocationAuthorization.from_dict(invocation.to_dict())
    assert copied.invocation_id == invocation.invocation_id
    assert copied is not invocation
    identifier = classify_presented_invocation(registry, record, copied)
    assert identifier.standing is InvocationSelectionStanding.COPIED_IDENTIFIER
    assert identifier.matches_plan_object is False
    assert identifier.matches_invocation_id is True
    assert identifier.registry_would_select_one is False
    text = classify_presented_invocation(registry, record, invocation.invocation_id)
    assert text.code == "copied_invocation_identifier"
    forged = classify_presented_invocation(registry, record, object())
    assert forged.standing is InvocationSelectionStanding.FORGED
    assert forged.code == "forged_invocation_rejected"
    frozen = classify_presented_invocation(registry, record, frozenset({invocation}))
    assert frozen.standing is InvocationSelectionStanding.FORGED
    with pytest.raises(ContractValidationError, match="invalid_session_state"):
        registry.selected_invocation(record)


def test_closed_and_invalidated_sessions_do_not_authorize_fanout():
    registry, record, _, _ = _open((_live(), _history()), _work(LIVE, HISTORY))
    plan = registry.plan_for(record)
    invocation = plan.steps[0].invocation
    registry.close(record)
    closed = assess_session_origin(registry, record)
    _assert_closed_authority(closed)
    assert registry.state(record) is MasterPlanningSessionState.CLOSED
    assert closed.membership is RegistryMembershipStanding.CLOSED
    assert _finding(
        closed, SessionOriginConcept.PROCESS_LOCAL_REGISTRY_MEMBERSHIP
    ).standing is SessionOriginStanding.OBSERVED
    assert _finding(
        closed, SessionOriginConcept.CALLER_SUPPLIED_PLANNING_CONTEXT
    ).standing is SessionOriginStanding.NOT_ESTABLISHED
    assert _finding(
        closed, SessionOriginConcept.RETAINED_ROUTE_DECISION_IDENTITY
    ).standing is SessionOriginStanding.NOT_ESTABLISHED
    assert closed.route_plan is None
    assert closed.fanout.membership_code == "session_closed"
    assert closed.fanout.fanout_authorized is False
    assert classify_presented_invocation(registry, record, invocation).code == "session_closed"
    registry.invalidate(record)
    invalidated = assess_session_origin(registry, record)
    _assert_closed_authority(invalidated)
    assert invalidated.membership is RegistryMembershipStanding.INVALIDATED
    assert invalidated.decision_rejection_code == "session_invalidated"
    assert invalidated.authorized_multi_invocation_execution_session is False
    assert plan.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_refused_outcome_has_no_invocations_to_execute():
    registry = MasterPlanningSessionRegistry()
    record = registry.open_session(_proposal(_work(ALTITUDE)), _context())
    refusal = registry.decision_for(record)
    assert refusal.outcome is MasterRouteOutcome.REFUSED
    assert refusal.plan is None
    assessment = assess_session_origin(registry, record, presented_decision=refusal)
    _assert_closed_authority(assessment)
    assert assessment.decision_identity is DecisionIdentityStanding.RETAINED
    assert assessment.presented_decision_is_retained is True
    assert assessment.route_plan is None
    assert assessment.selection.planned_invocation_count == 0
    assert assessment.selection.registry_code == "invalid_session_state"
    assert assessment.fanout.requirements == ()
    assert assessment.fanout.fanout_authorized is False
    assert classify_presented_invocation(registry, record, object()).code == "invalid_session_state"


def test_substitute_decision_and_copied_context_are_not_retained_identity():
    registry, record, context, proposal = _open((_live(),), _work(LIVE))
    decision = registry.decision_for(record)
    plan = registry.plan_for(record)
    rebuilt = MasterRouteDecision.from_dict(decision.to_dict())
    assert rebuilt is not decision
    reconstructed = assess_session_origin(registry, record, presented_decision=rebuilt)
    assert reconstructed.decision_identity is DecisionIdentityStanding.RECONSTRUCTED
    assert reconstructed.decision_rejection_code == "reconstructed_decision_rejected"
    assert _finding(
        reconstructed, SessionOriginConcept.RETAINED_ROUTE_DECISION_IDENTITY
    ).standing is SessionOriginStanding.REJECTED
    assert reconstructed.retained_decision is decision
    assert reconstructed.authenticated_application_principal is False
    deserialized = assess_session_origin(registry, record, presented_decision=decision.to_dict())
    assert deserialized.decision_identity is DecisionIdentityStanding.DESERIALIZED
    assert deserialized.decision_rejection_code == "deserialized_decision_rejected"
    forged = assess_session_origin(registry, record, presented_decision=plan)
    assert forged.decision_identity is DecisionIdentityStanding.FORGED
    assert forged.decision_rejection_code == "forged_decision_rejected"
    copied_context = replace(context)
    assert copied_context == context
    assert copied_context is not context
    copied = assess_session_origin(registry, record, presented_context=copied_context)
    assert copied.presented_context_is_retained is False
    assert copied.planning_context is context
    assert copied.authenticated_application_principal is False
    assert registry.decision_for(record) is decision
    assert registry.require_retained_plan(record, plan) is plan
    same = authorize_route(proposal, context)
    assert same is not decision
    assert assess_session_origin(registry, record, presented_decision=same).decision_identity is (
        DecisionIdentityStanding.RECONSTRUCTED
    )


def test_authentication_claims_from_ids_identity_text_and_model_output_are_rejected():
    registry, record, context, _ = _open((_live(),), _work(LIVE))
    decision = registry.decision_for(record)
    plan = decision.plan
    assert plan is not None
    invocation = plan.steps[0].invocation
    model = ModelDecision(kind=ModelDecisionKind.REFUSAL)
    turn = ModelTurnRequest(user_text=TEXT)
    claims = (
        (record.session_id, AuthenticationClaimKind.MATCHING_IDENTIFIER),
        (context.master_request_id, AuthenticationClaimKind.MATCHING_IDENTIFIER),
        ({"session_id": record.session_id}, AuthenticationClaimKind.MATCHING_IDENTIFIER),
        (record, AuthenticationClaimKind.OBJECT_IDENTITY),
        (decision, AuthenticationClaimKind.OBJECT_IDENTITY),
        (plan, AuthenticationClaimKind.OBJECT_IDENTITY),
        (invocation, AuthenticationClaimKind.OBJECT_IDENTITY),
        (context, AuthenticationClaimKind.OBJECT_IDENTITY),
        (TEXT, AuthenticationClaimKind.USER_TEXT),
        (model, AuthenticationClaimKind.MODEL_OUTPUT),
        (turn, AuthenticationClaimKind.MODEL_OUTPUT),
    )
    for claim, kind in claims:
        rejected = reject_authentication_claim(claim)
        assert rejected.kind is kind
        assert rejected.authenticated is False
        assert rejected.establishes_principal is False
        assert rejected.establishes_execution_session is False
        assert rejected.execution_authority is MasterRouteExecutionAuthority.DENIED
        assessed = assess_session_origin(registry, record, authentication_claim=claim)
        _assert_closed_authority(assessed)
        assert assessed.claim_rejection is not None
        assert assessed.claim_rejection.kind is kind
        assert _finding(
            assessed, SessionOriginConcept.AUTHENTICATED_APPLICATION_PRINCIPAL
        ).standing is SessionOriginStanding.NOT_ESTABLISHED
    with pytest.raises(ContractValidationError, match="session_principal_not_accepted"):
        assess_session_origin(registry, record.session_id)
    with pytest.raises(ContractValidationError, match="session_principal_not_accepted"):
        assess_session_origin(registry, b"caller-1")
    with pytest.raises(ContractValidationError, match="invalid_session"):
        assess_session_origin(registry, object())


def test_promotion_of_authority_fanout_and_selection_is_rejected():
    registry, record, _, _ = _open((_live(), _history()), _work(LIVE, HISTORY))
    assessment = assess_session_origin(registry, record)
    requirement = assessment.fanout.requirements[0]
    with pytest.raises(ContractValidationError, match="authenticated_principal_not_available"):
        replace(assessment, authenticated_application_principal=True)
    with pytest.raises(ContractValidationError, match="authorized_execution_session_not_available"):
        replace(assessment, authorized_multi_invocation_execution_session=True)
    with pytest.raises(ContractValidationError, match="execution_authority_not_granted"):
        replace(assessment, execution_authority="PRODUCTION")  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        replace(assessment, production_authority=MasterProductionAuthority.PRODUCTION)
    with pytest.raises(ContractValidationError, match="source_authenticity_not_established"):
        replace(assessment, source_authenticity="AUTHENTICATED")  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="request_coverage_not_established"):
        replace(assessment, request_coverage="ESTABLISHED")  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(assessment, dispatch_permitted=True)
    with pytest.raises(ContractValidationError, match="fanout_not_authorized"):
        replace(assessment.fanout, fanout_authorized=True)
    with pytest.raises(ContractValidationError, match="fanout_not_authorized"):
        replace(assessment.fanout, trusted=True)
    with pytest.raises(ContractValidationError, match="additional_planning_identity_not_fanout"):
        replace(assessment.fanout, preserves_original_planning_identity=False)
    with pytest.raises(ContractValidationError, match="retained_plan_not_replaced"):
        replace(assessment.fanout, preserves_original_plan=False)
    with pytest.raises(ContractValidationError, match="authorized_execution_session_not_available"):
        replace(assessment.fanout, executes_every_planned_invocation=True)
    with pytest.raises(ContractValidationError, match="selection_does_not_authorize_execution"):
        replace(requirement, selected_for_execution=True)
    with pytest.raises(ContractValidationError, match="authorized_execution_session_not_available"):
        replace(requirement, requirement_satisfied=True)
    with pytest.raises(ContractValidationError, match="execution_authority_not_granted"):
        replace(requirement, execution_granted=True)
    with pytest.raises(ContractValidationError, match="caller_context_not_principal"):
        replace(assessment.authority_requirement, caller_supplied_context_satisfies=True)
    with pytest.raises(ContractValidationError, match="registry_membership_not_authentication"):
        replace(assessment.authority_requirement, process_local_membership_satisfies=True)
    with pytest.raises(ContractValidationError, match="retained_decision_not_authentication"):
        replace(assessment.authority_requirement, retained_decision_identity_satisfies=True)
    with pytest.raises(ContractValidationError, match="matching_identifier_not_authentication"):
        replace(assessment.authority_requirement, matching_identifier_satisfies=True)
    with pytest.raises(ContractValidationError, match="object_identity_not_authentication"):
        replace(assessment.authority_requirement, object_identity_satisfies=True)
    with pytest.raises(ContractValidationError, match="user_text_not_authentication"):
        replace(assessment.authority_requirement, user_text_satisfies=True)
    with pytest.raises(ContractValidationError, match="model_output_not_authentication"):
        replace(assessment.authority_requirement, model_output_satisfies=True)
    with pytest.raises(ContractValidationError, match="authenticated_principal_not_available"):
        replace(assessment.authority_requirement, authority_issued=True)
    with pytest.raises(ContractValidationError, match="authenticated_principal_not_available"):
        replace(assessment.authority_requirement, authenticated_principal_required=False)
    with pytest.raises(ContractValidationError, match="multiple_invocations_rejected"):
        replace(assessment.selection, selected_invocation_count=2)
    with pytest.raises(ContractValidationError, match="session_origin_limitation_not_cleared"):
        replace(assessment, limitations=())
    assert isinstance(requirement, PerInvocationSelectionRequirement)
    assert isinstance(assessment.fanout, ProposedInvocationFanout)


def test_existing_decision_fanout_limit_is_not_bypassed():
    with pytest.raises(ContractValidationError, match="decision_fanout_not_permitted"):
        _proposal(_work(RISK), (_decision("dec-1"), _decision("dec-2")))
    registry, record, _, _ = _open((_decision(),), _work(RISK), target_candidate_aircraft_id="ac-1")
    assessment = assess_session_origin(registry, record)
    assert assessment.selection.planned_invocation_count == 1
    assert assessment.selection.specialists == (MasterSpecialist.DECISION,)
    assert assessment.fanout.fanout_authorized is False
    assert assessment.authorized_multi_invocation_execution_session is False
