"""Offline execution-policy boundary. A plan and a candidate are not permission."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from wilvor_ai.contracts import (
    ALLOWED_V1_CAPABILITIES,
    PROHIBITED_V1_CAPABILITIES,
    AgentAuthorityMode,
    AgentCapability,
    ContractValidationError,
)
from wilvor_ai.decision_tools import DECISION_TOOLS
from wilvor_ai.historical_analytics import HISTORICAL_ANALYTICS_TOOLS
from wilvor_ai.live_ops import LIVE_OPS_TOOLS
from wilvor_ai.live_ops_public_contracts import LIVE_OPS_MAX_TOOL_CALLS
from wilvor_ai.master_contracts import MasterPurpose, MasterRequirement, MasterSpecialist
from wilvor_ai.master_execution_policy_boundary import (
    AUTHENTICATED_PRINCIPAL_AVAILABLE,
    MASTER_EXECUTION_POLICY_LIMITATIONS,
    MASTER_EXECUTION_POLICY_SCHEMA_VERSION,
    MAX_DESCRIPTIVE_RESOURCE_CANDIDATES,
    MAX_HISTORICAL_WINDOW,
    PRODUCTION_POLICY_ENGINE_AVAILABLE,
    PROHIBITED_CAPABILITIES,
    READ_ONLY_TOOL_NAMES_BY_SPECIALIST,
    SPECIALIST_CAPABILITY_PAIRS,
    SUPPORTED_CAPABILITIES,
    MasterExecutionPolicyCandidate,
    MasterExecutionPolicyDenialReason,
    MasterExecutionPolicyRequirement,
    MasterExecutionPolicyRequirementStatus,
    MasterExecutionPolicyStatus,
    assess_master_execution_policy,
)
from wilvor_ai.master_execution_scope import (
    MasterExecutionPrerequisite,
    bind_master_execution_scope,
)
from wilvor_ai.master_parallel_contracts import MasterParallelContractBundle
from wilvor_ai.master_routing import (
    MAX_HISTORICAL_WINDOW as ROUTE_MAX_HISTORICAL_WINDOW,
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRouteHints,
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.master_specialist_adapters import describe_planned_adapters


TEXT = "Which aircraft are currently affected by hazards?"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
RECOMMENDATIONS = AgentCapability.EXPLAIN_EXISTING_RECOMMENDATIONS
SUMMARIZE = AgentCapability.SUMMARIZE_OPERATIONAL_STATE
MUTATE = AgentCapability.MUTATE_OPERATIONAL_STATE
REQUEST_TIME = "2026-01-15T00:00:01Z"

Reason = MasterExecutionPolicyDenialReason
Requirement = MasterExecutionPolicyRequirement


def _context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-policy-1",
        "correlation_id": "corr-policy-1",
        "application_request_time_utc": REQUEST_TIME,
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


def _decision(step_id: str = "dec-1", **overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "step_id": step_id,
        "specialist": MasterSpecialist.DECISION,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "capability": RISK,
        "temporal_intent": MasterRouteTemporalIntent.CURRENT,
    }
    values.update(overrides)
    return MasterProposedStep(**values)  # type: ignore[arg-type]


def _scope(*steps: MasterProposedStep, **context_overrides: object):
    context = _context(**context_overrides)
    decision = authorize_route(_proposal(*steps), context)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    assert decision.plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    bundle = MasterParallelContractBundle(context=context, plan=decision.plan, evidence_inputs=())
    step = decision.plan.steps[0]
    descriptor = next(
        item for item in describe_planned_adapters(bundle).descriptors if item.step_id == step.step_id
    )
    scope = bind_master_execution_scope(context, decision.plan, step, descriptor)
    return scope


def _finding(assessment, requirement: Requirement):
    return next(item for item in assessment.findings if item.requirement is requirement)


def _assert_denied(assessment, scope) -> None:
    assert assessment.schema_version == MASTER_EXECUTION_POLICY_SCHEMA_VERSION
    assert assessment.execution_status is MasterExecutionPolicyStatus.DENIED
    assert assessment.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert assessment.execution_permitted is False
    assert assessment.dispatch_permitted is False
    assert assessment.specialist_dispatched is False
    assert assessment.caller_candidate_is_authority is False
    assert assessment.stops_before_execution_authorization is True
    assert assessment.missing_requirements == tuple(Requirement)
    assert assessment.missing_prerequisites == tuple(MasterExecutionPrerequisite)
    assert assessment.missing_prerequisites == scope.missing_prerequisites
    assert assessment.limitations == MASTER_EXECUTION_POLICY_LIMITATIONS
    assert assessment.master_request_id == scope.master_request_id
    assert assessment.invocation_id == scope.invocation_id
    assert scope.execution_permission_granted is False
    assert list(MasterExecutionPolicyStatus) == [MasterExecutionPolicyStatus.DENIED]
    assert list(MasterExecutionPolicyRequirementStatus) == [
        MasterExecutionPolicyRequirementStatus.MISSING,
        MasterExecutionPolicyRequirementStatus.DENIED,
    ]


def test_vocabularies_and_read_only_catalogs_are_the_existing_ones():
    assert SUPPORTED_CAPABILITIES is ALLOWED_V1_CAPABILITIES
    assert PROHIBITED_CAPABILITIES is PROHIBITED_V1_CAPABILITIES
    assert MAX_HISTORICAL_WINDOW is ROUTE_MAX_HISTORICAL_WINDOW
    assert MAX_DESCRIPTIVE_RESOURCE_CANDIDATES == LIVE_OPS_MAX_TOOL_CALLS
    assert SPECIALIST_CAPABILITY_PAIRS == {
        (MasterSpecialist.LIVE_OPS, MasterPurpose.DISCOVERY): frozenset({LIVE}),
        (MasterSpecialist.LIVE_OPS, MasterPurpose.ANSWER): frozenset({LIVE}),
        (MasterSpecialist.HISTORICAL_ANALYTICS, MasterPurpose.ANSWER): frozenset({HISTORY}),
        (MasterSpecialist.DECISION, MasterPurpose.ANSWER): frozenset({RISK, RECOMMENDATIONS}),
    }
    specialist_capabilities = set().union(*SPECIALIST_CAPABILITY_PAIRS.values())
    assert specialist_capabilities <= SUPPORTED_CAPABILITIES
    assert specialist_capabilities.isdisjoint(PROHIBITED_CAPABILITIES)
    assert SUMMARIZE in SUPPORTED_CAPABILITIES
    assert SUMMARIZE not in specialist_capabilities
    assert MUTATE in PROHIBITED_CAPABILITIES
    catalogs = {
        MasterSpecialist.LIVE_OPS: LIVE_OPS_TOOLS,
        MasterSpecialist.HISTORICAL_ANALYTICS: HISTORICAL_ANALYTICS_TOOLS,
        MasterSpecialist.DECISION: DECISION_TOOLS,
    }
    assert set(READ_ONLY_TOOL_NAMES_BY_SPECIALIST) == set(catalogs)
    for specialist, tools in catalogs.items():
        assert READ_ONLY_TOOL_NAMES_BY_SPECIALIST[specialist] == frozenset(item.name for item in tools)
        assert {item.authority_mode for item in tools} == {AgentAuthorityMode.READ_ONLY_ADVISORY}
    assert AUTHENTICATED_PRINCIPAL_AVAILABLE is False
    assert PRODUCTION_POLICY_ENGINE_AVAILABLE is False


def test_ready_live_ops_plan_still_lacks_execution_authorization():
    scope = _scope(_live())
    assert scope.structural_ready_to_execute is True
    assert scope.gates == (MasterRouteGate.NOT_REQUIRED,)
    assessment = assess_master_execution_policy(scope)
    _assert_denied(assessment, scope)
    assert assessment.denied_requirements == (
        Requirement.EXECUTION_DEADLINE_AND_BOUNDED_RUNTIME,
    )
    assert _finding(assessment, Requirement.AUTHENTICATED_PRINCIPAL).reasons == (
        Reason.PRINCIPAL_NOT_AUTHENTICATED,
    )
    assert _finding(assessment, Requirement.ALLOWED_SPECIALIST_CAPABILITY).reasons == (
        Reason.CAPABILITY_NOT_PERMISSION,
    )
    assert _finding(assessment, Requirement.APPROVED_READ_ONLY_RESOURCE_SCOPE).reasons == (
        Reason.READ_RESOURCE_SCOPE_NOT_ESTABLISHED,
    )
    assert _finding(assessment, Requirement.REGION_AUTHORITY).reasons == (
        Reason.REGION_AUTHORITY_NOT_ESTABLISHED,
    )
    assert _finding(assessment, Requirement.TEMPORAL_SCOPE_AND_HISTORICAL_INTERVAL).reasons == (
        Reason.TEMPORAL_SCOPE_NOT_AN_EXECUTION_GRANT,
    )
    assert _finding(assessment, Requirement.EXECUTION_DEADLINE_AND_BOUNDED_RUNTIME).reasons == (
        Reason.DEADLINE_MISSING,
        Reason.UNCONTROLLED_EXECUTION,
    )
    assert _finding(assessment, Requirement.REQUEST_AND_INVOCATION_IDENTITY).reasons == (
        Reason.MATCHING_IDENTITY_NOT_AUTHENTICATION,
    )
    assert _finding(assessment, Requirement.DECISION_TARGET_AUTHORIZATION).reasons == (
        Reason.TARGET_NOT_AUTHORIZED,
    )
    assert _finding(assessment, Requirement.UNRESOLVED_ROUTE_GATES).reasons == (
        Reason.NO_UNRESOLVED_GATE_IS_NOT_AUTHORIZATION,
        Reason.STRUCTURAL_READINESS_NOT_PERMISSION,
    )
    assert Reason.POLICY_SPOOF_NOT_AUTHORITY not in assessment.denial_reasons
    assert Reason.FOREIGN_INVOCATION not in assessment.denial_reasons
    assert Reason.RESOURCE_ESCALATION not in assessment.denial_reasons
    assert Reason.PROHIBITED_CAPABILITY not in assessment.denial_reasons


def test_complete_descriptive_candidate_still_lacks_execution_authorization():
    scope = _scope(_live())
    candidate = MasterExecutionPolicyCandidate(
        resource_names=("search_current_hazards",),
        deadline_utc="2026-01-15T00:00:31Z",
        runtime_bound_seconds=30,
        master_request_id=scope.master_request_id,
        invocation_id=scope.invocation_id,
        specialist_request_id=scope.specialist_request_id,
        correlation_id=scope.correlation_id,
        claimed_capability=LIVE,
    )
    assessment = assess_master_execution_policy(scope, candidate)
    _assert_denied(assessment, scope)
    assert assessment.denied_requirements == ()
    assert Reason.RECOGNIZED_TOOL_NAME_NOT_A_GRANT in assessment.denial_reasons
    assert Reason.CALLER_DEADLINE_NOT_AUTHORITY in assessment.denial_reasons
    assert Reason.CALLER_RUNTIME_BOUND_NOT_AUTHORITY in assessment.denial_reasons
    assert Reason.MATCHING_IDENTITY_NOT_AUTHENTICATION in assessment.denial_reasons
    assert Reason.CAPABILITY_NOT_PERMISSION in assessment.denial_reasons
    assert Reason.DEADLINE_EXPIRED not in assessment.denial_reasons
    assert Reason.UNCONTROLLED_EXECUTION not in assessment.denial_reasons
    assert Reason.FOREIGN_INVOCATION not in assessment.denial_reasons
    assert Reason.RESOURCE_ESCALATION not in assessment.denial_reasons
    assert Reason.POLICY_SPOOF_NOT_AUTHORITY not in assessment.denial_reasons


def test_policy_spoof_does_not_authenticate_a_principal_or_permit_execution():
    scope = _scope(_live())
    candidate = MasterExecutionPolicyCandidate(
        principal_label="admin",
        policy_decision="ALLOW",
        execution_permitted=True,
        dispatch_permitted=True,
        deadline_utc="2026-01-15T00:00:31Z",
        runtime_bound_seconds=30,
    )
    assessment = assess_master_execution_policy(scope, candidate)
    _assert_denied(assessment, scope)
    principal = _finding(assessment, Requirement.AUTHENTICATED_PRINCIPAL)
    assert principal.status is MasterExecutionPolicyRequirementStatus.DENIED
    assert principal.reasons == (
        Reason.PRINCIPAL_NOT_AUTHENTICATED,
        Reason.PRINCIPAL_LABEL_NOT_AUTHENTICATION,
        Reason.CALLER_POLICY_DECISION_NOT_AUTHORITY,
        Reason.POLICY_SPOOF_NOT_AUTHORITY,
    )
    assert principal.prerequisite is MasterExecutionPrerequisite.AUTHENTICATED_APPLICATION_PRINCIPAL
    assert not hasattr(assess_master_execution_policy, "authenticate")


def test_rebinding_authority_constants_does_not_permit_execution(monkeypatch):
    scope = _scope(_live())
    monkeypatch.setattr(
        "wilvor_ai.master_execution_policy_boundary.AUTHENTICATED_PRINCIPAL_AVAILABLE",
        True,
    )
    with pytest.raises(ContractValidationError) as caught:
        assess_master_execution_policy(scope)
    assert "policy_authority_not_available" in caught.value.errors


def test_promoted_status_argument_is_rejected():
    scope = _scope(_live())
    with pytest.raises(ContractValidationError) as caught:
        assess_master_execution_policy(scope, execution_permitted=True)
    assert "execution_not_granted" in caught.value.errors


def test_assessment_cannot_be_rewritten_into_permission():
    scope = _scope(_live())
    assessment = assess_master_execution_policy(scope)
    with pytest.raises(FrozenInstanceError):
        assessment.execution_permitted = True  # type: ignore[misc]
    with pytest.raises(ContractValidationError) as caught:
        replace(assessment, execution_permitted=True)
    assert "execution_not_granted" in caught.value.errors
    with pytest.raises(ContractValidationError):
        replace(assessment, execution_status="ALLOWED")  # type: ignore[arg-type]


def test_missing_and_expired_deadlines_stay_denied():
    scope = _scope(_live())
    missing = assess_master_execution_policy(
        scope,
        MasterExecutionPolicyCandidate(runtime_bound_seconds=30),
    )
    _assert_denied(missing, scope)
    assert _finding(missing, Requirement.EXECUTION_DEADLINE_AND_BOUNDED_RUNTIME).reasons == (
        Reason.DEADLINE_MISSING,
        Reason.CALLER_RUNTIME_BOUND_NOT_AUTHORITY,
    )
    expired = assess_master_execution_policy(
        scope,
        MasterExecutionPolicyCandidate(
            deadline_utc="2026-01-14T00:00:00Z",
            runtime_bound_seconds=30,
        ),
    )
    _assert_denied(expired, scope)
    assert Reason.DEADLINE_EXPIRED in expired.denial_reasons
    assert Reason.CALLER_DEADLINE_NOT_AUTHORITY not in expired.denial_reasons
    observed = assess_master_execution_policy(
        scope,
        MasterExecutionPolicyCandidate(
            deadline_utc="2026-01-15T00:01:00Z",
            observed_utc="2026-01-15T00:02:00Z",
            runtime_bound_seconds=10,
        ),
    )
    assert Reason.DEADLINE_EXPIRED in observed.denial_reasons
    assert observed.execution_permitted is False
    unbounded = assess_master_execution_policy(
        scope,
        MasterExecutionPolicyCandidate(
            deadline_utc="2026-01-15T00:00:31Z",
            runtime_bound_seconds=30,
            unbounded_runtime=True,
        ),
    )
    assert Reason.UNCONTROLLED_EXECUTION in unbounded.denial_reasons
    assert unbounded.execution_status is MasterExecutionPolicyStatus.DENIED


def test_foreign_invocation_is_denied():
    scope = _scope(_live())
    assessment = assess_master_execution_policy(
        scope,
        MasterExecutionPolicyCandidate(invocation_id="foreign-invocation"),
    )
    _assert_denied(assessment, scope)
    assert _finding(assessment, Requirement.REQUEST_AND_INVOCATION_IDENTITY).reasons == (
        Reason.FOREIGN_INVOCATION,
    )
    assert assessment.invocation_id == scope.invocation_id
    assert assessment.invocation_id != "foreign-invocation"


def test_untrusted_region_label_is_not_region_authority():
    scope = _scope(_live(hints=MasterRouteHints(region_text="California")))
    assert MasterRouteGate.REGION_VALIDATION_PENDING in scope.gates
    assert scope.structural_ready_to_execute is False
    assessment = assess_master_execution_policy(
        scope,
        MasterExecutionPolicyCandidate(region_label="CONUS", region_authoritative=True),
    )
    _assert_denied(assessment, scope)
    assert _finding(assessment, Requirement.REGION_AUTHORITY).reasons == (
        Reason.UNTRUSTED_REGION_LABEL,
        Reason.POLICY_SPOOF_NOT_AUTHORITY,
    )
    assert assessment.unresolved_gates == scope.gates
    assert Reason.UNRESOLVED_ROUTE_GATE in assessment.denial_reasons
    assert Reason.REGION_AUTHORITY_NOT_ESTABLISHED not in assessment.denial_reasons


def test_resource_escalation_and_unrestricted_tables_are_denied():
    scope = _scope(_live())
    assessment = assess_master_execution_policy(
        scope,
        MasterExecutionPolicyCandidate(
            resource_names=("*", "aircraft", "search_current_hazards"),
            unrestricted_table_access=True,
        ),
    )
    _assert_denied(assessment, scope)
    assert _finding(assessment, Requirement.APPROVED_READ_ONLY_RESOURCE_SCOPE).reasons == (
        Reason.READ_RESOURCE_SCOPE_NOT_ESTABLISHED,
        Reason.UNRESTRICTED_TABLE_ACCESS,
        Reason.RESOURCE_ESCALATION,
        Reason.RECOGNIZED_TOOL_NAME_NOT_A_GRANT,
    )
    cross_specialist = assess_master_execution_policy(
        scope,
        MasterExecutionPolicyCandidate(resource_names=("summarize_historical_encounters",)),
    )
    assert Reason.RESOURCE_ESCALATION in cross_specialist.denial_reasons
    assert Reason.RECOGNIZED_TOOL_NAME_NOT_A_GRANT not in cross_specialist.denial_reasons
    assert cross_specialist.execution_permitted is False
    with pytest.raises(ContractValidationError) as caught:
        MasterExecutionPolicyCandidate(resource_names=("table-1",) * (LIVE_OPS_MAX_TOOL_CALLS + 1))
    assert "excessive_resource_candidates" in caught.value.errors


def test_ambiguous_decision_target_is_not_authorized():
    scope = _scope(
        _decision(hints=MasterRouteHints(population_aircraft_ids=("n123", "n456")))
    )
    assert MasterRouteGate.TARGET_AMBIGUOUS in scope.gates
    assert scope.target_authorization_granted is False
    assessment = assess_master_execution_policy(
        scope,
        MasterExecutionPolicyCandidate(
            target_aircraft_id="n123",
            target_recommendation_id="rec-9",
        ),
    )
    _assert_denied(assessment, scope)
    assert _finding(assessment, Requirement.DECISION_TARGET_AUTHORIZATION).reasons == (
        Reason.TARGET_NOT_AUTHORIZED,
        Reason.TARGET_AMBIGUOUS,
    )
    assert (
        _finding(assessment, Requirement.DECISION_TARGET_AUTHORIZATION).prerequisite
        is MasterExecutionPrerequisite.SEPARATELY_AUTHORIZED_DECISION_TARGET
    )
    assert Reason.UNRESOLVED_ROUTE_GATE in assessment.denial_reasons


def test_application_target_candidate_is_not_authorization():
    scope = _scope(_decision(), target_candidate_aircraft_id="n123")
    assert MasterRouteGate.APPLICATION_TARGET_CANDIDATE_PRESENT in scope.gates
    assessment = assess_master_execution_policy(scope)
    _assert_denied(assessment, scope)
    assert Reason.APPLICATION_TARGET_CANDIDATE_NOT_AUTHORIZATION in assessment.denial_reasons
    assert Reason.TARGET_NOT_AUTHORIZED in assessment.denial_reasons
    assert assessment.execution_permitted is False


def test_arbitrary_historical_interval_is_denied_and_a_bounded_window_is_not_a_grant():
    scope = _scope(_history())
    bounded = assess_master_execution_policy(
        scope,
        MasterExecutionPolicyCandidate(
            historical_start_utc="2026-01-01T00:00:00Z",
            historical_end_utc="2026-01-02T00:00:00Z",
            deadline_utc="2026-01-15T00:00:31Z",
            runtime_bound_seconds=30,
        ),
    )
    _assert_denied(bounded, scope)
    assert _finding(bounded, Requirement.TEMPORAL_SCOPE_AND_HISTORICAL_INTERVAL).reasons == (
        Reason.TEMPORAL_SCOPE_NOT_AN_EXECUTION_GRANT,
    )
    arbitrary = assess_master_execution_policy(
        scope,
        MasterExecutionPolicyCandidate(
            historical_start_utc="2026-01-01T00:00:00Z",
            historical_end_utc="2026-01-09T00:00:00Z",
        ),
    )
    _assert_denied(arbitrary, scope)
    assert Reason.ARBITRARY_HISTORICAL_INTERVAL in arbitrary.denial_reasons
    assert arbitrary.execution_permitted is False
    widened = assess_master_execution_policy(
        _scope(_live()),
        MasterExecutionPolicyCandidate(
            historical_start_utc="2026-01-01T00:00:00Z",
            historical_end_utc="2026-01-02T00:00:00Z",
        ),
    )
    assert Reason.ARBITRARY_HISTORICAL_INTERVAL in widened.denial_reasons


def test_prohibited_and_unsupported_capabilities_are_denied():
    scope = _scope(_live())
    prohibited = assess_master_execution_policy(
        scope,
        MasterExecutionPolicyCandidate(claimed_capability=MUTATE),
    )
    _assert_denied(prohibited, scope)
    assert Reason.PROHIBITED_CAPABILITY in prohibited.denial_reasons
    assert Reason.CAPABILITY_NOT_PERMISSION in prohibited.denial_reasons
    unsupported = assess_master_execution_policy(
        scope,
        MasterExecutionPolicyCandidate(claimed_capability=SUMMARIZE),
    )
    assert Reason.UNSUPPORTED_CAPABILITY in unsupported.denial_reasons
    assert unsupported.execution_permitted is False
    mismatch = assess_master_execution_policy(
        scope,
        MasterExecutionPolicyCandidate(claimed_capability=HISTORY),
    )
    assert Reason.CAPABILITY_SPECIALIST_MISMATCH in mismatch.denial_reasons
    assert mismatch.execution_status is MasterExecutionPolicyStatus.DENIED


def test_candidate_and_scope_substitutes_are_rejected():
    scope = _scope(_live())
    with pytest.raises(ContractValidationError) as caught:
        assess_master_execution_policy(scope, scope)  # type: ignore[arg-type]
    assert "invalid_policy_candidate" in caught.value.errors
    with pytest.raises(ContractValidationError) as caught:
        assess_master_execution_policy(object())  # type: ignore[arg-type]
    assert "invalid_execution_scope" in caught.value.errors
