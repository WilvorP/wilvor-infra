"""Baseline proof that current Master contracts cannot authorize execution.

These tests call MA1-MA7, Sprint 2, and Sprint 3A contracts that are already
on the baseline. They do not import the Sprint 3B modules other agents are
still building, and they do not run a specialist, a model provider, AWS, or
the synthetic graph.

Matching identifiers, a gateway-shaped mapping, a planning-session record,
structural readiness, a policy-looking object, local capability consumption,
a public tool name, a complete-looking trace, a native specialist result, a
callsign, a missing or stale source, a timeout, a synthetic result, and an
MA5 status sentence stay short of execution authorization and authenticated
aviation evidence.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from wilvor_ai.contracts import (
    AgentCapability,
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    MatchCardinality,
    SourceCompleteness,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.decision_model_contracts import DecisionUnsupportedReason
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionSpecialistRunResult,
    DecisionSpecialistRunStatus,
    DecisionTargetMode,
)
from wilvor_ai.live_ops_public_contracts import (
    LiveOpsDiscovery,
    LiveOpsDiscoveryStatus,
    LiveOpsEntityHint,
)
from wilvor_ai.master_contracts import (
    MasterDisposition,
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
    observe_callsign,
    observe_typed_aircraft_id,
)
from wilvor_ai.master_decision_execution_readiness import (
    DecisionTargetMaterialKind,
    DecisionTargetReadinessStatus,
    assess_master_decision_execution_readiness,
)
from wilvor_ai.master_evidence_admission import (
    MasterAdmissionReason,
    MasterAdmissionStatus,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
    admit_master_evidence,
)
from wilvor_ai.master_evidence_corroboration import MasterEvidenceCorroborationInput
from wilvor_ai.master_execution_integration import inspect_master_execution_handoff
from wilvor_ai.master_execution_policy_boundary import (
    READ_ONLY_TOOL_NAMES_BY_SPECIALIST,
    MasterExecutionPolicyCandidate,
    MasterExecutionPolicyDenialReason,
    MasterExecutionPolicyRequirement,
    MasterExecutionPolicyStatus,
    assess_master_execution_policy,
)
from wilvor_ai.master_execution_preflight import evaluate_execution_preflight
from wilvor_ai.master_execution_scope import (
    MasterExecutionDenialCategory,
    bind_master_execution_scope,
)
from wilvor_ai.master_historical_binding import prepare_master_historical_binding
from wilvor_ai.master_historical_execution_readiness import (
    assess_master_historical_execution_readiness,
)
from wilvor_ai.master_live_ops_execution_readiness import (
    LIVE_OPS_READ_ONLY_TOOL_NAMES,
    LIVE_OPS_REAL_INVOCATIONS,
    assess_master_live_ops_execution_readiness,
)
from wilvor_ai.master_local_capability import (
    LocalCapabilityRegistry,
    LocalCapabilityScope,
    LocalConsumptionOutcome,
    LocalRejectionReason,
    LocalTestClock,
)
from wilvor_ai.master_orchestration import (
    SyntheticOutcome,
    SyntheticProductionAuthority,
    SyntheticWorkerResult,
)
from wilvor_ai.master_parallel_contracts import (
    MasterExecutionPreflightReport,
    MasterExecutionPreflightState,
    MasterParallelContractBundle,
    MasterProvenanceRequirement,
    MasterResponseLimitation,
    MasterResponsePolicyHandoff,
    MasterResponsePolicyState,
    response_policy_for,
)
from wilvor_ai.master_planning_session import (
    MasterPlanningSessionRecord,
    MasterPlanningSessionRegistry,
)
from wilvor_ai.master_provenance_trace import (
    ProvenanceAuditLimitation,
    ProposedDispatcherExecutionEvent,
    ProposedPopulationScope,
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
    MasterRouteHints,
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.master_session_origin_boundary import (
    AuthenticationClaimKind,
    assess_session_origin,
    propose_invocation_fanout,
    reject_authentication_claim,
)
from wilvor_ai.master_source_read_trace_boundary import (
    SourceReadTraceObligation,
    SourceReadTracePresence,
    UntrustedAbsenceKind,
    assess_source_read_trace,
)
from wilvor_ai.master_specialist_adapters import (
    MasterAdapterExecutionState,
    describe_planned_adapters,
)
from wilvor_ai.specialist_contracts import (
    SpecialistResult,
    SpecialistStatus,
    UnsupportedReason,
    VerifiedZeroClaim,
    VerifierOutcome,
)
from wilvor_ai.specialist_runtime_contracts import (
    ExecutedToolInvocation,
    HistoricalSpecialistRunResult,
    SpecialistRunStatus,
)


TEXT = "Which aircraft are currently affected by hazards, and what recorded risks apply?"
AS_OF = "2026-01-15T00:00:01Z"
NOW = datetime(2026, 1, 15, 0, 0, 1, tzinfo=timezone.utc)
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
FACTUAL_ANSWER = "Aircraft N777WX is inside SIGMET WS01. Recorded risk is HIGH. Affected count is 0."
FIXED_STATUS_TEXT = {
    "Response policy has not been evaluated.",
    "The request is unsupported. No operational conclusion is available.",
    "Required evidence is unavailable. No count or zero is inferred.",
    "Supplied evidence is not sufficient for an operational claim.",
}
Reason = MasterExecutionPolicyDenialReason
Requirement = MasterExecutionPolicyRequirement


def _errors(func) -> list[str]:
    with pytest.raises(ContractValidationError) as caught:
        func()
    return list(caught.value.errors)


def _context(text: str = TEXT, **overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-3b-1",
        "correlation_id": "corr-3b-1",
        "application_request_time_utc": AS_OF,
        "canonical_user_text": text,
    }
    values.update(overrides)
    return MasterPlanningContext(**values)  # type: ignore[arg-type]


def _proposed(step_id: str, specialist: MasterSpecialist, capability: object, **overrides: object) -> MasterProposedStep:
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


def _proposal(context: MasterPlanningContext, *steps: MasterProposedStep) -> MasterRouteProposal:
    capabilities = []
    seen: set[object] = set()
    for step in steps:
        if step.capability not in seen:
            seen.add(step.capability)
            capabilities.append(MasterProposedCapability(step.capability, MasterRequirement.REQUIRED))
    return MasterRouteProposal(
        user_text=context.canonical_user_text,
        capabilities=tuple(capabilities),
        steps=steps,
    )


def _authorize(context: MasterPlanningContext, *steps: MasterProposedStep):
    proposal = _proposal(context, *steps)
    decision = authorize_route(proposal, context)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert decision.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert list(MasterRouteExecutionAuthority) == [MasterRouteExecutionAuthority.DENIED]
    return proposal, decision


def _open(context: MasterPlanningContext, *steps: MasterProposedStep):
    proposal, caller_decision = _authorize(context, *steps)
    registry = MasterPlanningSessionRegistry()
    record = registry.open_session(proposal, context)
    return registry, record, caller_decision


def _bundle(context: MasterPlanningContext, plan, evidence=()):
    return MasterParallelContractBundle(
        context=context,
        plan=plan,
        evidence_inputs=tuple(evidence),
    )


def _descriptor(bundle, specialist: MasterSpecialist):
    matches = [
        item
        for item in describe_planned_adapters(bundle).descriptors
        if item.specialist is specialist
    ]
    assert len(matches) == 1
    return matches[0]


def _scope(context, plan, step, bundle):
    return bind_master_execution_scope(
        context,
        plan,
        step,
        _descriptor(bundle, step.invocation.specialist),
    )


def _assert_closed(value) -> None:
    """Check the denial fields the object actually carries.

    Admission results omit execution authority. Capability consumption omits
    request coverage. A preflight report and a response handoff omit dispatch.
    Every field that is present must stay denied.
    """

    expected = {
        "execution_authority": MasterRouteExecutionAuthority.DENIED,
        "production_authority": MasterProductionAuthority.DENIED,
        "source_authenticity": MasterSourceAuthenticity.NOT_ESTABLISHED,
        "request_coverage": MasterRequestCoverage.NOT_ESTABLISHED,
        "dispatch_permitted": False,
        "execution_permitted": False,
        "executable": False,
        "evidence_verified": False,
        "end_to_end_fulfilled": False,
        "execution_authorized": False,
        "specialist_dispatched": False,
        "distributed_production_authorization": False,
    }
    present = [name for name in expected if hasattr(value, name)]
    assert present
    for name in present:
        assert getattr(value, name) is expected[name]


def _record(record_id: str = "rec-1", **overrides: object) -> SourceRecord:
    values: dict[str, object] = {
        "record_id": record_id,
        "source_version": "v1",
        "event_timestamp_utc": AS_OF,
    }
    values.update(overrides)
    return SourceRecord(**values)  # type: ignore[arg-type]


def _evidence(**overrides: object) -> Evidence:
    values: dict[str, object] = {
        "source": "fixture",
        "source_records": (_record(),),
        "query_timestamp_utc": AS_OF,
        "freshness_status": FreshnessStatus.FRESH,
        "confidence": ConfidenceLevel.UNKNOWN,
        "limitations": (),
        "tool_call_id": "tool-call-1",
        "temporal_scope": TemporalScope.CURRENT,
    }
    values.update(overrides)
    return Evidence(**values)  # type: ignore[arg-type]


def _tool_result(evidence: Evidence | None = None, **overrides: object) -> ToolResult:
    item = _evidence() if evidence is None else evidence
    values: dict[str, object] = {
        "tool_name": "search_current_hazards",
        "tool_call_id": item.tool_call_id,
        "status": ToolResultStatus.SUCCESS,
        "temporal_scope": TemporalScope.CURRENT,
        "data": {"source_authenticated": True, "aircraft_count": 0, "aircraft_id": "N777WX"},
        "evidence": (item,),
        "as_of_utc": AS_OF,
        "limitations": (),
        "correlation_id": "corr-3b-1",
    }
    values.update(overrides)
    return ToolResult(**values)  # type: ignore[arg-type]


def _event(authorization, **overrides: object) -> ProposedDispatcherExecutionEvent:
    values: dict[str, object] = {
        "master_request_id": authorization.master_request_id,
        "master_invocation_id": authorization.invocation_id,
        "specialist_invocation_id": authorization.specialist_request_id,
        "correlation_id": authorization.correlation_id,
        "permit_consumption_ref": "permit-3b-1",
        "adapter_identity": "adapter-live-ops",
        "tool_name": "search_current_hazards",
        "tool_call_id": "tool-call-1",
        "tool_arguments": (("region", "KZOA"),),
        "source_response_ref": "response-3b-1",
        "observation_time_utc": AS_OF,
        "evaluation_time_utc": AS_OF,
        "source_version": "v1",
        "entity_ids": ("N777WX",),
        "population_scope": ProposedPopulationScope.CLAIMED_COMPLETE,
        "completeness": SourceCompleteness(
            status="COMPLETE",
            reason="caller-supplied",
            epoch_ids=("epoch-1",),
            evaluated_as_of_utc=AS_OF,
        ),
        "retrieval_limits": (),
        "limitation_notes": ("journal complete",),
        "freshness_status": FreshnessStatus.FRESH,
        "execution_trace_ref": "trace-3b-1",
    }
    values.update(overrides)
    return ProposedDispatcherExecutionEvent(**values)  # type: ignore[arg-type]


def _executed() -> ExecutedToolInvocation:
    arguments = {"region": "KZOA"}
    return ExecutedToolInvocation(
        tool_name="search_current_hazards",
        tool_call_id="tool-call-1",
        requested_arguments=arguments,
        executed_arguments=dict(arguments),
        list_limit_default_applied=False,
        canonical_key="canonical-3b-1",
    )


def _historical_result(**overrides: object) -> SpecialistResult:
    values: dict[str, object] = {
        "status": SpecialistStatus.ANSWERED,
        "answer": FACTUAL_ANSWER,
        "temporal_scope": TemporalScope.HISTORICAL,
        "evaluated_as_of_utc": AS_OF,
        "tool_results": (),
        "verified_claims": (VerifiedZeroClaim(tool_call_id="tool-call-1"),),
        "limitations": (),
        "unsupported_reason": None,
        "verifier_outcome": VerifierOutcome.PASSED,
    }
    values.update(overrides)
    return SpecialistResult(**values)  # type: ignore[arg-type]


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


def _decision_artifact(aircraft_id: str = "N777WX") -> DecisionSpecialistRunResult:
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


def _synthetic(authorization, outcome: SyntheticOutcome) -> SyntheticWorkerResult:
    return SyntheticWorkerResult(
        master_request_id=authorization.master_request_id,
        correlation_id=authorization.correlation_id,
        invocation_id=authorization.invocation_id,
        specialist_request_id=authorization.specialist_request_id,
        specialist=authorization.specialist,
        outcome=outcome,
        note="synthetic worker output is not an operational read",
    )


def _gateway_event() -> dict[str, object]:
    return {
        "version": "2.0",
        "routeKey": "POST /ai/query",
        "rawPath": "/ai/query",
        "requestContext": {
            "http": {"method": "POST", "path": "/ai/query", "sourceIp": "203.0.113.10"},
            "requestId": "gw-request-1",
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": "user-1",
                        "iss": "https://issuer.example",
                        "aud": "wilvor-ai",
                        "scope": "wilvor/ai.query",
                        "exp": "1893456000",
                    },
                    "scopes": ["wilvor/ai.query"],
                }
            },
        },
        "headers": {"authorization": "Bearer forged-token"},
        "body": TEXT,
    }


def test_matching_identifiers_are_not_authentication():
    context = _context()
    registry, record, caller_decision = _open(context, _live())
    retained = registry.decision_for(record)
    caller_invocation = caller_decision.plan.steps[0].invocation
    retained_invocation = retained.plan.steps[0].invocation
    assert caller_decision == retained
    assert caller_decision is not retained
    assert caller_invocation == retained_invocation
    assert caller_invocation is not retained_invocation
    assert _errors(
        lambda: registry.associate_invocation(record, caller_invocation)
    ) == ["copied_invocation_identifier"]
    copied = replace(retained_invocation)
    assert copied == retained_invocation
    assert copied is not retained_invocation
    assert _errors(lambda: registry.associate_invocation(record, copied)) == [
        "copied_invocation_identifier"
    ]
    assert _errors(
        lambda: registry.associate_invocation(record, retained_invocation.invocation_id)
    ) == ["copied_invocation_identifier"]

    claim = reject_authentication_claim(retained_invocation.invocation_id)
    assert claim.kind is AuthenticationClaimKind.MATCHING_IDENTIFIER
    assert claim.authenticated is False
    assert claim.establishes_principal is False
    _assert_closed(claim)

    origin = assess_session_origin(
        registry,
        record,
        authentication_claim={
            "master_request_id": context.master_request_id,
            "correlation_id": context.correlation_id,
            "invocation_id": retained_invocation.invocation_id,
        },
    )
    assert origin.authenticated_application_principal is False
    assert origin.claim_rejection is not None
    assert origin.claim_rejection.authenticated is False
    _assert_closed(origin)

    scope = _scope(context, retained.plan, retained.plan.steps[0], _bundle(context, retained.plan))
    assessment = assess_master_execution_policy(
        scope,
        MasterExecutionPolicyCandidate(
            master_request_id=scope.master_request_id,
            invocation_id=scope.invocation_id,
            specialist_request_id=scope.specialist_request_id,
            correlation_id=scope.correlation_id,
        ),
    )
    assert Reason.MATCHING_IDENTITY_NOT_AUTHENTICATION in assessment.denial_reasons
    assert assessment.execution_permitted is False
    _assert_closed(assessment)

    trace = assess_source_read_trace(_event(retained_invocation), authorization=retained_invocation)
    source_read = trace.classification.finding(MasterProvenanceRequirement.SOURCE_READ_PROVENANCE)
    assert ProvenanceAuditLimitation.MATCHING_IDENTIFIERS_NOT_ORIGIN in source_read.limitations
    assert trace.matching_request_id_authenticates_read is False
    assert trace.observed_trusted_runtime_events == ()
    _assert_closed(trace)


def test_api_gateway_payload_is_not_verified_ingress():
    context = _context()
    registry, record, _caller = _open(context, _live())
    payload = _gateway_event()
    claim = reject_authentication_claim(payload)
    assert claim.kind is AuthenticationClaimKind.MATCHING_IDENTIFIER
    assert claim.authenticated is False
    assert claim.establishes_execution_session is False
    _assert_closed(claim)

    header = reject_authentication_claim(payload["headers"]["authorization"])
    assert header.kind is AuthenticationClaimKind.USER_TEXT
    assert header.authenticated is False

    origin = assess_session_origin(
        registry,
        record,
        presented_context=payload,
        authentication_claim=payload,
    )
    assert origin.presented_context_is_retained is False
    assert origin.authenticated_application_principal is False
    assert origin.authorized_multi_invocation_execution_session is False
    assert origin.claim_rejection is not None
    assert origin.claim_rejection.kind is AuthenticationClaimKind.MATCHING_IDENTIFIER
    _assert_closed(origin)
    assert _errors(lambda: assess_session_origin(registry, payload)) == [
        "invalid_session"
    ]
    assert _errors(lambda: MasterPlanningSessionRecord(record.session_id, "user-1")) == [
        "session_principal_not_accepted"
    ]

    scope = _scope(context, origin.route_plan, origin.route_plan.steps[0], _bundle(context, origin.route_plan))
    policy = assess_master_execution_policy(
        scope,
        MasterExecutionPolicyCandidate(
            principal_label="user-1",
            policy_decision="AUTHORIZED",
            execution_permitted=True,
            master_request_id="gw-request-1",
            invocation_id=scope.invocation_id,
        ),
    )
    assert Reason.PRINCIPAL_LABEL_NOT_AUTHENTICATION in policy.denial_reasons
    assert Reason.POLICY_SPOOF_NOT_AUTHORITY in policy.denial_reasons
    assert Reason.FOREIGN_INVOCATION in policy.denial_reasons
    assert policy.caller_candidate_is_authority is False
    assert policy.execution_status is MasterExecutionPolicyStatus.DENIED
    _assert_closed(policy)


def test_planning_session_record_does_not_authorize_multiple_executions():
    context = _context()
    registry, record, _caller = _open(context, _live(), _history())
    plan = registry.plan_for(record)
    first, second = plan.steps[0].invocation, plan.steps[1].invocation
    assert len(plan.steps) == 2
    assert registry.associate_invocation(record, first) is first
    assert record.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert _errors(lambda: registry.associate_invocation(record, second)) == [
        "multiple_invocations_rejected"
    ]
    assert _errors(lambda: registry.associate_invocation(record, (first, second))) == [
        "multiple_invocations_rejected"
    ]
    assert registry.selected_invocation(record) is first

    fanout = propose_invocation_fanout(registry, record)
    assert fanout.fanout_authorized is False
    assert fanout.executes_every_planned_invocation is False
    assert fanout.route_plan is plan
    assert all(item.requirement_satisfied is False for item in fanout.requirements)
    _assert_closed(fanout)

    handoff = inspect_master_execution_handoff(registry, record)
    assert handoff.planning_succeeded is True
    assert handoff.execution_authorized is False
    assert handoff.selected_invocation_count == 1
    assert handoff.selected_invocation is first
    assert handoff.multi_invocation_execution_supported is False
    assert handoff.planned_step_ids == tuple(step.step_id for step in plan.steps)
    assert handoff.factual_response_available is False
    assert second.invocation_id in tuple(step.invocation.invocation_id for step in plan.steps)
    assert handoff.selected_invocation is not second
    _assert_closed(handoff)
    assert _errors(
        lambda: inspect_master_execution_handoff(
            registry,
            record,
            multi_invocation_execution_supported=True,
        )
    ) == ["multiple_invocations_rejected"]


def test_structural_readiness_cannot_authorize_dispatch():
    context = _context()
    registry, record, _caller = _open(context, _live())
    plan = registry.plan_for(record)
    step = plan.steps[0]
    assert step.ready_to_execute is True
    assert step.gates == (MasterRouteGate.NOT_REQUIRED,)
    bundle = _bundle(context, plan)
    descriptor = _descriptor(bundle, MasterSpecialist.LIVE_OPS)
    assert descriptor.execution_state is MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
    scope = bind_master_execution_scope(context, plan, step, descriptor)
    assert scope.structural_ready_to_execute is True
    assert scope.execution_permission_granted is False
    assert scope.redeemable is False
    assert MasterExecutionDenialCategory.STRUCTURAL_READINESS_NOT_PERMISSION in scope.denial_categories
    _assert_closed(scope)

    policy = assess_master_execution_policy(scope)
    gate = next(item for item in policy.findings if item.requirement is Requirement.UNRESOLVED_ROUTE_GATES)
    assert Reason.STRUCTURAL_READINESS_NOT_PERMISSION in gate.reasons
    assert Reason.NO_UNRESOLVED_GATE_IS_NOT_AUTHORIZATION in gate.reasons
    assert policy.execution_permitted is False
    _assert_closed(policy)

    preflight = evaluate_execution_preflight(bundle)
    assert preflight.report.state is MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION
    assert preflight.report.executable is False
    assert preflight.dispatch_permitted is False
    named = MasterExecutionPreflightReport(
        workflow=bundle,
        state=MasterExecutionPreflightState.CONTRACT_READY_FOR_FUTURE_INTEGRATION,
        missing_provenance=tuple(MasterProvenanceRequirement),
    )
    assert named.executable is False
    assert named.state is MasterExecutionPreflightState.CONTRACT_READY_FOR_FUTURE_INTEGRATION
    _assert_closed(named)

    readiness = assess_master_live_ops_execution_readiness(bundle, descriptor)
    assert readiness.structural_ready_to_execute is True
    assert readiness.execution_blocked is True
    assert readiness.source_read_authorized is False
    assert readiness.specialist_invoked is False
    assert readiness.verified_zero is False
    _assert_closed(readiness)

    registry.associate_invocation(record, step.invocation)
    handoff = inspect_master_execution_handoff(registry, record)
    assert handoff.execution_scope.structural_ready_to_execute is True
    assert handoff.execution_authorized is False
    assert handoff.preflight.report.state is MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION
    _assert_closed(handoff)


def test_caller_can_forge_policy_looking_objects():
    context = _context()
    _proposal_unused, decision = _authorize(context, _live())
    plan = decision.plan
    bundle = _bundle(context, plan)
    scope = _scope(context, plan, plan.steps[0], bundle)
    forged = {
        "policy_decision": "ALLOW",
        "execution_permitted": True,
        "dispatch_permitted": True,
        "production_authority": "PRODUCTION",
        "principal": "user-1",
    }
    assert _errors(lambda: assess_master_execution_policy(scope, forged)) == [
        "invalid_policy_candidate"
    ]
    candidate = MasterExecutionPolicyCandidate(
        principal_label="user-1",
        policy_decision="ALLOW",
        execution_permitted=True,
        dispatch_permitted=True,
        unrestricted_table_access=True,
        region_label="KZOA",
        region_authoritative=True,
        deadline_utc="2026-01-15T00:00:31Z",
        runtime_bound_seconds=30,
    )
    assessment = assess_master_execution_policy(scope, candidate)
    assert assessment.caller_candidate_is_authority is False
    assert assessment.execution_status is MasterExecutionPolicyStatus.DENIED
    assert Reason.POLICY_SPOOF_NOT_AUTHORITY in assessment.denial_reasons
    assert Reason.CALLER_POLICY_DECISION_NOT_AUTHORITY in assessment.denial_reasons
    assert Reason.UNRESTRICTED_TABLE_ACCESS in assessment.denial_reasons
    assert Reason.UNTRUSTED_REGION_LABEL in assessment.denial_reasons
    assert assessment.execution_permitted is False
    assert assessment.specialist_dispatched is False
    _assert_closed(assessment)
    assert _errors(
        lambda: assess_master_execution_policy(scope, candidate, execution_permitted=True)
    ) == ["execution_not_granted"]
    assert _errors(
        lambda: assess_master_execution_policy(
            scope,
            candidate,
            production_authority=MasterProductionAuthority.PRODUCTION,
        )
    ) == ["production_authority_not_issued"]


def test_local_capability_consumption_is_not_a_production_grant():
    context = _context()
    _proposal_unused, decision = _authorize(context, _live())
    invocation = decision.plan.steps[0].invocation
    scope = LocalCapabilityScope(
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        agent_capability=LIVE,
    )
    clock = LocalTestClock(NOW)
    registry = LocalCapabilityRegistry(clock)
    handle = registry.issue(
        scope=scope,
        invocation_id=invocation.invocation_id,
        consumer_id="consumer-3b",
        session_id="local-session-3b",
        expires_at=NOW + timedelta(seconds=30),
    )
    consumed = registry.consume(
        handle,
        scope=scope,
        invocation_id=invocation.invocation_id,
        consumer_id="consumer-3b",
    )
    assert consumed.outcome is LocalConsumptionOutcome.TRANSITION_RECORDED
    assert bool(consumed) is False
    assert consumed.distributed_production_authorization is False
    assert consumed.execution_permitted is False
    assert consumed.specialist_dispatched is False
    _assert_closed(consumed)
    replay = registry.consume(
        handle,
        scope=scope,
        invocation_id=invocation.invocation_id,
        consumer_id="consumer-3b",
    )
    assert replay.outcome is LocalConsumptionOutcome.REJECTED
    assert replay.reason is LocalRejectionReason.REPLAY
    assert replay.distributed_production_authorization is False
    _assert_closed(replay)

    trace = assess_source_read_trace(
        _event(invocation, permit_consumption_ref="local-session-3b"),
        authorization=invocation,
        executed_invocation=_executed(),
    )
    assert trace.capability_consumption_permits_dispatch is False
    execution = trace.classification.finding(MasterProvenanceRequirement.AUTHORIZED_EXECUTION_CONTEXT)
    assert ProvenanceAuditLimitation.PERMIT_REFERENCE_NOT_CONSUMPTION in execution.limitations
    _assert_closed(trace)


def test_public_tool_name_is_not_permission_to_read():
    context = _context()
    _proposal_unused, decision = _authorize(context, _live())
    plan = decision.plan
    bundle = _bundle(context, plan)
    scope = _scope(context, plan, plan.steps[0], bundle)
    catalog_name = "search_current_hazards"
    assert catalog_name in READ_ONLY_TOOL_NAMES_BY_SPECIALIST[MasterSpecialist.LIVE_OPS]
    assert catalog_name in LIVE_OPS_READ_ONLY_TOOL_NAMES
    interfaces = [
        item
        for item in LIVE_OPS_REAL_INVOCATIONS
        if item.qualname == catalog_name
    ]
    assert interfaces
    assert all(item.authorized_to_invoke is False for item in interfaces)
    assert all(item.authority_mode in {None, "READ_ONLY_ADVISORY"} for item in interfaces)

    assessment = assess_master_execution_policy(
        scope,
        MasterExecutionPolicyCandidate(
            resource_names=(catalog_name, "AircraftCurrent", "*"),
            claimed_capability=LIVE,
        ),
    )
    resource = next(
        item
        for item in assessment.findings
        if item.requirement is Requirement.APPROVED_READ_ONLY_RESOURCE_SCOPE
    )
    assert Reason.RECOGNIZED_TOOL_NAME_NOT_A_GRANT in resource.reasons
    assert Reason.RESOURCE_ESCALATION in resource.reasons
    assert Reason.UNRESTRICTED_TABLE_ACCESS in resource.reasons
    assert Reason.READ_RESOURCE_SCOPE_NOT_ESTABLISHED in resource.reasons
    assert assessment.dispatch_permitted is False
    _assert_closed(assessment)

    readiness = assess_master_live_ops_execution_readiness(
        bundle,
        _descriptor(bundle, MasterSpecialist.LIVE_OPS),
    )
    assert readiness.source_read_authorized is False
    assert readiness.selectors_authorized is False
    assert readiness.tool_result_created is False
    assert readiness.geospatial_coverage_established is False
    _assert_closed(readiness)


def test_apparently_complete_journal_is_not_source_authentication():
    context = _context()
    _proposal_unused, decision = _authorize(context, _live())
    authorization = decision.plan.steps[0].invocation
    event = _event(authorization)
    assessment = assess_source_read_trace(
        event,
        authorization=authorization,
        executed_invocation=_executed(),
        disposition=MasterDisposition.EXECUTED,
    )
    assert assessment.observed_trusted_runtime_events == ()
    assert assessment.trusted_dispatcher_implemented is False
    assert assessment.trusted_runtime_observation_available is False
    assert assessment.caller_supplied_trace_authenticates_read is False
    assert assessment.matching_timestamp_authenticates_read is False
    assert assessment.matching_source_version_authenticates_read is False
    assert bool(assessment) is False
    assert assessment.missing_provenance == tuple(MasterProvenanceRequirement)
    for item in assessment.obligations:
        assert item.trusted_runtime_observed is False
        assert item.presence in {
            SourceReadTracePresence.NOT_PROPOSED,
            SourceReadTracePresence.PROPOSED_UNOBSERVED,
        }
    assert (
        assessment.obligation(SourceReadTraceObligation.EXECUTED_TOOL).presence
        is SourceReadTracePresence.PROPOSED_UNOBSERVED
    )
    assert (
        assessment.obligation(SourceReadTraceObligation.SOURCE_VERSION).presence
        is SourceReadTracePresence.PROPOSED_UNOBSERVED
    )
    source_read = assessment.classification.finding(MasterProvenanceRequirement.SOURCE_READ_PROVENANCE)
    assert ProvenanceAuditLimitation.FORGED_EXECUTION_TRACE in source_read.limitations
    assert ProvenanceAuditLimitation.AUTHENTICATED_SOURCE_READ_NOT_AVAILABLE in source_read.limitations
    _assert_closed(assessment)
    assert assessment.verified_zero_established is False


def test_native_specialist_result_is_not_automatically_master_admitted_evidence():
    context = _context()
    _proposal_unused, decision = _authorize(context, _history(), _decision())
    plan = decision.plan
    historical = next(step for step in plan.steps if step.invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS)
    decision_step = next(step for step in plan.steps if step.invocation.specialist is MasterSpecialist.DECISION)
    native = _historical_result()
    intermediate = _intermediate_run()
    decision_result = _decision_artifact()

    historical_admission = admit_master_evidence(
        (MasterEvidenceAdmissionInput(historical.invocation, native),)
    ).admissions[0]
    assert historical_admission.admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
    assert historical_admission.evidence_verified is False
    assert MasterAdmissionReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED in historical_admission.reason_codes
    assert MasterAdmissionReason.HISTORICAL_VERIFIER_NOT_SOURCE_AUTHENTICATION in historical_admission.reason_codes
    assert MasterAdmissionReason.VERIFIED_ZERO_CLAIM_NOT_AUTHENTICATED in historical_admission.reason_codes
    assert MasterAdmissionReason.NATIVE_CORRELATION_ABSENT in historical_admission.reason_codes
    _assert_closed(historical_admission)

    run_admission = admit_master_evidence(
        (MasterEvidenceAdmissionInput(historical.invocation, intermediate),)
    ).admissions[0]
    assert run_admission.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.HISTORICAL_RUN_NOT_FINAL_ARTIFACT in run_admission.reason_codes
    assert run_admission.evidence_verified is False
    _assert_closed(run_admission)

    decision_admission = admit_master_evidence(
        (MasterEvidenceAdmissionInput(decision_step.invocation, decision_result),)
    ).admissions[0]
    assert decision_admission.admission_status is not MasterAdmissionStatus.CONTRACT_ONLY
    assert decision_admission.evidence_verified is False
    assert MasterAdmissionReason.DECISION_VERIFIER_NOT_SOURCE_AUTHENTICATION in decision_admission.reason_codes
    assert MasterAdmissionReason.NATIVE_CORRELATION_ABSENT in decision_admission.reason_codes
    _assert_closed(decision_admission)

    bundle = _bundle(
        context,
        plan,
        (
            MasterEvidenceCorroborationInput(
                MasterEvidenceAdmissionInput(historical.invocation, native),
                historical_run=intermediate,
            ),
        ),
    )
    handoff = evaluate_master_response_policy(bundle)
    rendered = render_master_response(handoff)
    assert rendered.claims_admitted is False
    assert "N777WX" not in rendered.status_text
    assert "SIGMET" not in rendered.status_text
    assert FACTUAL_ANSWER not in rendered.status_text
    _assert_closed(handoff)


def test_callsign_or_discovered_aircraft_does_not_authorize_decision_execution():
    discovery = LiveOpsDiscovery(
        callsign="UAL100",
        status=LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH,
        aircraft_ids=("N777WX",),
        evidence_tool_call_id="discovery-tool-1",
        reference_epoch=1_700_000_000,
        identity_resolved=True,
        selection_authorized=False,
    )
    context = _context(target_candidate_aircraft_id="N777WX")
    _proposal_unused, decision = _authorize(
        context,
        _decision(
            hints=MasterRouteHints(
                callsign="UAL100",
                typed_aircraft_id="N777WX",
                region_text="west sector",
            )
        ),
    )
    bundle = _bundle(context, decision.plan)
    report = assess_master_decision_execution_readiness(
        bundle,
        _descriptor(bundle, MasterSpecialist.DECISION),
        typed_aircraft_observations=(observe_typed_aircraft_id("N777WX"),),
        callsign_observations=(observe_callsign(discovery),),
        discoveries=(discovery,),
        entity_hints=(LiveOpsEntityHint(entity_kind="AIRCRAFT", entity_id="N777WX", callsign="UAL100"),),
    )
    assert report.status is DecisionTargetReadinessStatus.TRUSTED_TARGET_UNAVAILABLE
    assert report.selected_aircraft_id is None
    assert report.trusted_decision_target_available is False
    assert report.decision_invocation_authorized is False
    assert report.target_authorization_granted is False
    assert report.decision_target_trusted is False
    assert report.callsign_is_aircraft_identity is False
    assert report.live_ops_discovery_is_trusted_target is False
    assert report.comparison_candidates_authorize_target is False
    assert report.ready_to_execute is False
    assert report.recorded_risk_level is None
    assert discovery.selection_authorized is False
    assert all(item.authorizes_decision_invocation is False for item in report.materials)
    assert DecisionTargetMaterialKind.CALLSIGN in {item.kind for item in report.materials}
    assert DecisionTargetMaterialKind.LIVE_OPS_DISCOVERY_CANDIDATE in {item.kind for item in report.materials}
    trusted = [
        item
        for item in report.materials
        if item.kind is DecisionTargetMaterialKind.TRUSTED_DECISION_TARGET
    ]
    assert len(trusted) == 1
    assert trusted[0].available is False
    _assert_closed(report)
    assert _errors(
        lambda: assess_master_decision_execution_readiness(
            bundle,
            _descriptor(bundle, MasterSpecialist.DECISION),
            callsign_is_aircraft_identity=True,
        )
    ) == ["callsign_not_authenticated_aircraft"]


def test_missing_or_stale_source_records_cannot_support_verified_zero():
    context = _context()
    _proposal_unused, decision = _authorize(context, _live(), _history())
    plan = decision.plan
    live = next(step for step in plan.steps if step.invocation.specialist is MasterSpecialist.LIVE_OPS)
    historical = next(
        step for step in plan.steps if step.invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    )
    missing = assess_source_read_trace(
        _tool_result(_evidence(source_records=()), data={"rows": [], "aircraft_count": 0})
    )
    assert missing.verified_zero_claim_minted is False
    assert missing.verified_zero_established is False
    assert missing.empty_result_creates_verified_zero is False
    assert missing.complete_population_absence_available is False
    assert missing.untrusted_absence_indication[0].kind is UntrustedAbsenceKind.MISSING_SOURCE_RECORDS
    assert missing.untrusted_absence_indication[0].establishes_verified_zero is False
    _assert_closed(missing)

    stale = assess_source_read_trace(
        _tool_result(
            _evidence(freshness_status=FreshnessStatus.STALE, source_records=()),
            status=ToolResultStatus.STALE,
            limitations=("stale fixture",),
        ),
        verified_zero_claim=VerifiedZeroClaim(tool_call_id="tool-call-1"),
    )
    clocks = stale.classification.finding(MasterProvenanceRequirement.OBSERVATION_EVALUATION_TIME)
    assert ProvenanceAuditLimitation.STALE_DATA in clocks.limitations
    assert stale.verified_zero_established is False
    source_read = stale.classification.finding(MasterProvenanceRequirement.SOURCE_READ_PROVENANCE)
    assert ProvenanceAuditLimitation.FABRICATED_VERIFIED_ZERO in source_read.limitations
    _assert_closed(stale)

    exact = assess_source_read_trace(
        _tool_result(
            _evidence(
                match_cardinality=MatchCardinality(exact_count=0, is_exact=True),
                completeness=SourceCompleteness(status="COMPLETE", evaluated_as_of_utc=AS_OF),
            )
        )
    )
    assert any(
        item.kind is UntrustedAbsenceKind.CALLER_REPORTED_EXACT_ZERO
        for item in exact.untrusted_absence_indication
    )
    assert exact.verified_zero_established is False
    _assert_closed(exact)

    binding = prepare_master_historical_binding(
        _bundle(context, plan),
        _descriptor(_bundle(context, plan), MasterSpecialist.HISTORICAL_ANALYTICS),
    )
    readiness = assess_master_historical_execution_readiness(binding)
    assert readiness.observed_final_result is False
    assert readiness.failure_interpreted_as_zero is False
    assert readiness.incomplete_coverage_interpreted_as_zero is False
    assert readiness.inferred_count is None
    assert readiness.evidence_issued is False
    _assert_closed(readiness)

    admission = admit_master_evidence(
        (MasterEvidenceAdmissionInput(historical.invocation, _historical_result()),)
    ).admissions[0]
    assert MasterAdmissionReason.VERIFIED_ZERO_CLAIM_NOT_AUTHENTICATED in admission.reason_codes
    assert admission.evidence_verified is False
    _assert_closed(admission)
    assert live.invocation.specialist is MasterSpecialist.LIVE_OPS


def test_timeout_cancellation_and_partial_coverage_are_not_complete_success():
    context = _context()
    _proposal_unused, decision = _authorize(context, _live())
    authorization = decision.plan.steps[0].invocation
    assert list(MasterRequestCoverage) == [MasterRequestCoverage.NOT_ESTABLISHED]
    for outcome in (
        SyntheticOutcome.TIMED_OUT,
        SyntheticOutcome.CANCELLED,
        SyntheticOutcome.PARTIAL,
        SyntheticOutcome.SYNTHETIC_COMPLETED,
    ):
        assert _errors(
            lambda outcome=outcome: SyntheticWorkerResult(
                master_request_id=authorization.master_request_id,
                correlation_id=authorization.correlation_id,
                invocation_id=authorization.invocation_id,
                specialist_request_id=authorization.specialist_request_id,
                specialist=authorization.specialist,
                outcome=outcome,
                note="synthetic outcome is not complete success",
                evidence_verified=True,
            )
        ) == ["synthetic_evidence_not_verified"]
        synthetic = _synthetic(authorization, outcome)
        assert synthetic.evidence_verified is False
        assert synthetic.production_authority is SyntheticProductionAuthority.DENIED
        admission = admit_master_evidence(
            (MasterEvidenceAdmissionInput(authorization, synthetic),)
        ).admissions[0]
        assert admission.admission_status is MasterAdmissionStatus.REJECTED
        assert MasterAdmissionReason.UNSUPPORTED_ARTIFACT in admission.reason_codes
        assert admission.evidence_verified is False
        assert admission.end_to_end_fulfilled is False
        _assert_closed(admission)

    partial = assess_source_read_trace(
        _tool_result(
            _evidence(
                completeness=SourceCompleteness(status="PARTIAL", reason="truncated", evaluated_as_of_utc=AS_OF),
            ),
            status=ToolResultStatus.PARTIAL,
            limitations=("partial fixture",),
        ),
        authorization=authorization,
        disposition=MasterDisposition.PROVIDER_TIMEOUT,
    )
    assert (
        partial.obligation(SourceReadTraceObligation.TIMEOUT_OUTCOME).presence
        is SourceReadTracePresence.PROPOSED_UNOBSERVED
    )
    assert partial.verified_zero_established is False
    assert partial.end_to_end_fulfilled is False
    _assert_closed(partial)

    disagreed = assess_source_read_trace(
        _tool_result(status=ToolResultStatus.SUCCESS),
        authorization=authorization,
        disposition=MasterDisposition.PROVIDER_TIMEOUT,
    )
    assert disagreed.proposed_identity_conflict is True
    assert (
        disagreed.obligation(SourceReadTraceObligation.TIMEOUT_OUTCOME).presence
        is SourceReadTracePresence.PROPOSED_UNOBSERVED
    )
    assert disagreed.end_to_end_fulfilled is False
    _assert_closed(disagreed)

    cancelled = assess_source_read_trace(
        _tool_result(status=ToolResultStatus.SUCCESS),
        disposition=MasterDisposition.CANCELLED,
    )
    assert (
        cancelled.obligation(SourceReadTraceObligation.CANCELLATION_OUTCOME).presence
        is SourceReadTracePresence.PROPOSED_UNOBSERVED
    )
    assert cancelled.end_to_end_fulfilled is False
    _assert_closed(cancelled)

    runtime = assess_source_read_trace(
        _tool_result(),
        disposition=MasterDisposition.RUNTIME_TIMEOUT,
    )
    assert (
        runtime.obligation(SourceReadTraceObligation.TIMEOUT_OUTCOME).presence
        is SourceReadTracePresence.PROPOSED_UNOBSERVED
    )
    _assert_closed(runtime)

    bundle = _bundle(context, decision.plan)
    preflight = evaluate_execution_preflight(bundle)
    assert preflight.report.state is MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION
    assert preflight.dispatch_permitted is False
    handoff = evaluate_master_response_policy(bundle)
    rendered = render_master_response(handoff)
    assert rendered.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert rendered.status_text == "Supplied evidence is not sufficient for an operational claim."
    assert "complete" not in rendered.status_text.casefold()
    assert rendered.claims_admitted is False


def test_synthetic_ma3_results_cannot_become_ma4_operational_evidence():
    context = _context()
    _proposal_unused, decision = _authorize(context, _history())
    authorization = decision.plan.steps[0].invocation
    synthetic = _synthetic(authorization, SyntheticOutcome.SYNTHETIC_COMPLETED)
    assert synthetic.outcome is SyntheticOutcome.SYNTHETIC_COMPLETED
    assert synthetic.evidence_verified is False
    admission = admit_master_evidence(
        (MasterEvidenceAdmissionInput(authorization, synthetic),)
    )
    assert admission.evidence_verified is False
    assert admission.admissions[0].admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.UNSUPPORTED_ARTIFACT in admission.admissions[0].reason_codes
    assert "answer" not in admission.admissions[0].__dataclass_fields__
    _assert_closed(admission)
    assert _errors(
        lambda: _bundle(
            context,
            decision.plan,
            (
                MasterEvidenceCorroborationInput(
                    MasterEvidenceAdmissionInput(authorization, synthetic)
                ),
            ),
        )
    ) == ["synthetic_result_not_evidence"]
    assert _errors(
        lambda: assess_source_read_trace(synthetic, authorization=authorization)
    ) == ["unsupported_provenance_subject"]
    readiness = assess_master_historical_execution_readiness(
        prepare_master_historical_binding(
            _bundle(context, decision.plan),
            _descriptor(_bundle(context, decision.plan), MasterSpecialist.HISTORICAL_ANALYTICS),
        )
    )
    assert readiness.evidence_issued is False
    assert readiness.finalization_performed is False
    assert readiness.specialist_result is None
    _assert_closed(readiness)


def test_ma5_cannot_produce_unsupported_factual_aviation_answers():
    context = _context()
    _proposal_unused, decision = _authorize(context, _history())
    authorization = decision.plan.steps[0].invocation
    factual = _historical_result(
        status=SpecialistStatus.UNSUPPORTED,
        answer=FACTUAL_ANSWER,
        verified_claims=(),
        unsupported_reason=UnsupportedReason.INSUFFICIENT_EVIDENCE,
        verifier_outcome=VerifierOutcome.NOT_RUN,
    )
    bundle = _bundle(
        context,
        decision.plan,
        (MasterEvidenceCorroborationInput(MasterEvidenceAdmissionInput(authorization, factual)),),
    )
    handoff = evaluate_master_response_policy(bundle)
    rendered = render_master_response(handoff)
    assert rendered.claims_admitted is False
    assert rendered.status_text in FIXED_STATUS_TEXT
    assert rendered.status_text != FACTUAL_ANSWER
    assert "N777WX" not in rendered.status_text
    assert "SIGMET" not in rendered.status_text
    assert "HIGH" not in rendered.status_text
    assert MasterResponseLimitation.NO_OPERATIONAL_CLAIM in handoff.limitations
    assert MasterResponseLimitation.EXECUTION_DENIED in handoff.limitations
    _assert_closed(handoff)

    unsupported = response_policy_for(bundle, MasterResponsePolicyState.UNSUPPORTED)
    unsupported_render = render_master_response(unsupported)
    assert unsupported_render.status_text == (
        "The request is unsupported. No operational conclusion is available."
    )
    assert "N777WX" not in unsupported_render.status_text
    assert unsupported_render.claims_admitted is False
    assert _errors(
        lambda: MasterResponsePolicyHandoff(
            workflow=bundle,
            state=MasterResponsePolicyState.UNSUPPORTED,
            limitations=unsupported.limitations,
            status_text=FACTUAL_ANSWER,
        )
    ) == ["invalid_status_text"]
    assert _errors(
        lambda: replace(unsupported_render, claims_admitted=True)
    ) == ["operational_claim_not_admitted"]
