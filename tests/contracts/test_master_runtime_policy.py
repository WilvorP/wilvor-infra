"""Deterministic runtime policy evaluation. An ALLOW is not a permit."""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_execution_policy_boundary import (
    AUTHENTICATED_PRINCIPAL_AVAILABLE,
    PRODUCTION_POLICY_ENGINE_AVAILABLE,
    SPECIALIST_CAPABILITY_PAIRS as BOUNDARY_PAIRS,
    MasterExecutionPolicyStatus,
)
from wilvor_ai.master_read_resource_guard import ReadOperation, server_read_budget
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRouteHints,
    MasterRouteOutcome,
    MasterRoutePlan,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.master_runtime_policy import (
    HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE,
    HOST_GRANT_ISSUER_AVAILABLE,
    POLICY_REVISION,
    SPECIALIST_CAPABILITY_PAIRS,
    MasterRuntimePolicyDecision,
    MasterRuntimePolicyDenialReason,
    MasterRuntimePolicyEnvelope,
    MasterRuntimePolicyEvaluation,
    MasterRuntimePolicyLimitation,
    PolicyAuthorityOrigin,
    TargetObligationKind,
    _evaluate_hypothetical_runtime_policy,
    evaluate_master_runtime_policy,
    promote_policy_evaluation_to_permit,
    retain_runtime_policy_session,
    seal_server_policy_inputs,
)


TEXT = "Which aircraft are currently affected by hazards?"
LIVE_CAP = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HIST_CAP = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
ANSWER = MasterPurpose.ANSWER
REQUIRED = MasterRequirement.REQUIRED
PRINCIPAL = "hypothetical-principal-1"
START = "2026-01-08T00:00:00Z"
END = "2026-01-15T00:00:00Z"
WIDE_START = "2026-01-01T00:00:00Z"
Reason = MasterRuntimePolicyDenialReason


def _proposal(step: MasterProposedStep) -> MasterRouteProposal:
    return MasterRouteProposal(
        user_text=TEXT,
        capabilities=(MasterProposedCapability(step.capability, REQUIRED),),
        steps=(step,),
    )


def _live(**overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "step_id": "live-1",
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": ANSWER,
        "requirement": REQUIRED,
        "capability": LIVE_CAP,
        "temporal_intent": MasterRouteTemporalIntent.CURRENT,
    }
    values.update(overrides)
    return MasterProposedStep(**values)  # type: ignore[arg-type]


def _history(**overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "step_id": "hist-1",
        "specialist": MasterSpecialist.HISTORICAL_ANALYTICS,
        "purpose": ANSWER,
        "requirement": REQUIRED,
        "capability": HIST_CAP,
        "temporal_intent": MasterRouteTemporalIntent.HISTORICAL,
        "historical_start_utc": START,
        "historical_end_utc": END,
    }
    values.update(overrides)
    return MasterProposedStep(**values)  # type: ignore[arg-type]


def _decision(**overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "step_id": "dec-1",
        "specialist": MasterSpecialist.DECISION,
        "purpose": ANSWER,
        "requirement": REQUIRED,
        "capability": RISK,
        "temporal_intent": MasterRouteTemporalIntent.CURRENT,
    }
    values.update(overrides)
    return MasterProposedStep(**values)  # type: ignore[arg-type]


def _retain(step: MasterProposedStep, **context_overrides: object):
    values: dict[str, object] = {
        "master_request_id": "master-req-policy-1",
        "correlation_id": "corr-policy-1",
        "application_request_time_utc": "2026-01-15T00:00:00Z",
        "canonical_user_text": TEXT,
    }
    values.update(context_overrides)
    context = MasterPlanningContext(**values)  # type: ignore[arg-type]
    decision = authorize_route(_proposal(step), context)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    assert decision.plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    session = retain_runtime_policy_session(context, decision.plan)
    planned = decision.plan.steps[0]
    return session, decision.plan, planned, planned.invocation


def _server(**overrides: object):
    values: dict[str, object] = {
        "evaluation_utc": "2026-01-15T00:00:00Z",
        "accepted_at_utc": "2026-01-15T00:00:00Z",
        "deadline_utc": "2026-01-15T00:05:00Z",
        "executions_used": 0,
        "model_turns_used": 0,
        "tool_calls_used": 0,
        "cancelled": False,
        "region_codes": frozenset(),
        "tool_name": "get_aircraft_operational_context",
        "resource_id": "aircraft",
        "operation": ReadOperation.GET_ITEM,
        "arguments": (("aircraft_id", "N123"),),
        "target_kind": TargetObligationKind.NOT_APPLICABLE,
        "target_id": None,
        "idempotency_key": "idem-policy-1",
    }
    values.update(overrides)
    return seal_server_policy_inputs(**values)  # type: ignore[arg-type]


def _historical_server(**overrides: object):
    values: dict[str, object] = {
        "tool_name": "summarize_historical_encounters",
        "resource_id": "historical.encounters",
        "operation": ReadOperation.QUERY,
        "arguments": (
            ("start_utc", START),
            ("end_utc", END),
            ("aircraft_id", "N123"),
        ),
    }
    values.update(overrides)
    return _server(**values)


def _envelope(session, step, invocation, server, **overrides: object) -> MasterRuntimePolicyEnvelope:
    _executions, turns, calls = server_read_budget(step.invocation.specialist)
    window = None
    if step.historical_start_utc is not None:
        window = (step.historical_start_utc, step.historical_end_utc)
    values: dict[str, object] = {
        "master_request_id": invocation.master_request_id,
        "planning_session_id": session.session_id,
        "step_id": step.step_id,
        "invocation_id": invocation.invocation_id,
        "specialist": invocation.specialist,
        "purpose": invocation.purpose,
        "capability": step.capability,
        "principal_ref": PRINCIPAL,
        "policy_revision": POLICY_REVISION,
        "resource_scope": server.resource_id,
        "region_scope": None,
        "historical_window": window,
        "decision_target_scope": None,
        "accepted_at_utc": server.accepted_at_utc,
        "deadline_utc": server.deadline_utc,
        "idempotency_key": server.idempotency_key,
        "max_model_turns": turns,
        "max_tool_calls": calls,
        "correlation_id": invocation.correlation_id,
    }
    values.update(overrides)
    return MasterRuntimePolicyEnvelope(**values)  # type: ignore[arg-type]


def _hypothetical(bundle, server, **overrides: object) -> MasterRuntimePolicyEvaluation:
    session, plan, step, invocation = bundle
    envelope = _envelope(session, step, invocation, server, **overrides)
    principal = overrides.get("principal_ref", PRINCIPAL)
    return _evaluate_hypothetical_runtime_policy(
        session,
        plan,
        step,
        invocation,
        server,
        envelope,
        principal_ref=principal,  # type: ignore[arg-type]
    )


def _production(bundle, server, **overrides: object) -> MasterRuntimePolicyEvaluation:
    session, plan, step, invocation = bundle
    envelope = _envelope(session, step, invocation, server, **overrides)
    return evaluate_master_runtime_policy(session, plan, step, invocation, server, envelope)


def _assert_not_a_permit(result: MasterRuntimePolicyEvaluation) -> None:
    assert result.execution_permitted is False
    assert result.dispatch_permitted is False
    assert result.specialist_executed is False
    assert result.grant_issued is False
    assert result.permit_issued is False
    assert result.production_principal_established is False
    assert result.production_authority is MasterProductionAuthority.DENIED
    assert result.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert result.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert result.tool_execution_recheck_required is True
    assert result.allows_specialist_execution() is False
    assert result.policy_revision == POLICY_REVISION
    assert result.evaluation_id == result.policy_fingerprint
    assert len(result.policy_fingerprint) == 64
    assert MasterRuntimePolicyLimitation.EVALUATION_IS_NOT_A_GRANT in result.limitations
    assert MasterRuntimePolicyLimitation.DESCRIPTIVE_ALLOW_IS_NOT_HOST_ISSUED in result.limitations


def test_bounded_live_and_historical_evaluations_allow_without_a_grant():
    live = _hypothetical(_retain(_live()), _server())
    historical = _hypothetical(_retain(_history()), _historical_server())
    for result, specialist in (
        (live, MasterSpecialist.LIVE_OPS),
        (historical, MasterSpecialist.HISTORICAL_ANALYTICS),
    ):
        _executions, turns, calls = server_read_budget(specialist)
        assert result.decision is MasterRuntimePolicyDecision.ALLOW
        assert result.denial_reasons == ()
        assert result.unresolved_gates == ()
        assert result.authority_origin is PolicyAuthorityOrigin.HYPOTHETICAL_TEST_HOST
        assert result.membership_established is True
        assert result.principal_ref == PRINCIPAL
        assert result.specialist is specialist
        assert result.max_model_turns == turns
        assert result.max_tool_calls == calls
        assert _executions == 1
        _assert_not_a_permit(result)
    assert historical.historical_window == (START, END)
    assert live.operation is ReadOperation.GET_ITEM
    assert live.resource_scope == "aircraft"


def test_production_denies_the_same_bounded_inputs():
    bundle = _retain(_live())
    server = _server()
    allowed = _hypothetical(bundle, server)
    denied = _production(bundle, server, principal_ref=None)
    assert allowed.decision is MasterRuntimePolicyDecision.ALLOW
    assert denied.decision is MasterRuntimePolicyDecision.DENY
    assert denied.denial_reasons == (Reason.PRINCIPAL_NOT_HOST_AUTHENTICATED,)
    assert denied.authority_origin is PolicyAuthorityOrigin.NOT_ESTABLISHED
    assert denied.principal_ref is None
    assert denied.policy_fingerprint != allowed.policy_fingerprint
    _assert_not_a_permit(denied)


def test_wrong_specialist_denies():
    result = _hypothetical(
        _retain(_live()),
        _server(),
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
    )
    assert result.decision is MasterRuntimePolicyDecision.DENY
    assert Reason.WRONG_SPECIALIST in result.denial_reasons
    assert Reason.WRONG_PURPOSE not in result.denial_reasons
    assert result.specialist is MasterSpecialist.LIVE_OPS
    _assert_not_a_permit(result)


def test_wrong_purpose_denies():
    result = _hypothetical(
        _retain(_live()),
        _server(),
        purpose=MasterPurpose.DISCOVERY,
    )
    assert result.decision is MasterRuntimePolicyDecision.DENY
    assert Reason.WRONG_PURPOSE in result.denial_reasons
    assert Reason.WRONG_SPECIALIST not in result.denial_reasons
    assert result.purpose is ANSWER
    _assert_not_a_permit(result)


def test_unresolved_route_gate_denies_even_with_sealed_region():
    bundle = _retain(_live(hints=MasterRouteHints(region_text="CA")))
    result = _hypothetical(
        bundle,
        _server(region_codes=frozenset({"CA"})),
        region_scope="CA",
    )
    assert result.decision is MasterRuntimePolicyDecision.DENY
    assert Reason.UNRESOLVED_ROUTE_GATE in result.denial_reasons
    assert MasterRouteGate.REGION_VALIDATION_PENDING in result.unresolved_gates
    assert Reason.ARBITRARY_REGION_TEXT not in result.denial_reasons
    assert result.region_scope == ("CA",)
    _assert_not_a_permit(result)


def test_expired_deadline_denies():
    result = _hypothetical(
        _retain(_live()),
        _server(deadline_utc="2026-01-15T00:00:00Z"),
    )
    assert result.decision is MasterRuntimePolicyDecision.DENY
    assert Reason.DEADLINE_EXPIRED in result.denial_reasons
    _assert_not_a_permit(result)


def test_bad_historical_window_denies():
    result = _hypothetical(
        _retain(_history()),
        _historical_server(
            arguments=(
                ("start_utc", WIDE_START),
                ("end_utc", END),
                ("aircraft_id", "N123"),
            ),
        ),
    )
    assert result.decision is MasterRuntimePolicyDecision.DENY
    assert Reason.HISTORICAL_WINDOW_EXCEEDED in result.denial_reasons
    assert result.historical_window == (START, END)
    _assert_not_a_permit(result)


def test_unknown_resource_denies():
    result = _hypothetical(
        _retain(_live()),
        _server(resource_id="not_a_resource"),
    )
    assert result.decision is MasterRuntimePolicyDecision.DENY
    assert Reason.UNKNOWN_RESOURCE in result.denial_reasons
    assert result.resource_scope == "not_a_resource"
    _assert_not_a_permit(result)


def test_cross_session_matching_strings_are_not_membership():
    first = _retain(_live())
    second = _retain(_live())
    session_a, plan_a, step_a, invocation_a = first
    session_b, plan_b, step_b, invocation_b = second
    assert session_a.session_id != session_b.session_id
    assert invocation_a.invocation_id == invocation_b.invocation_id
    assert plan_a is not plan_b
    assert step_a is not step_b
    crossed = _hypothetical(
        (session_a, plan_b, step_b, invocation_b),
        _server(),
    )
    assert crossed.decision is MasterRuntimePolicyDecision.DENY
    assert crossed.membership_established is False
    assert Reason.CROSS_SESSION in crossed.denial_reasons
    assert Reason.STRING_IDENTITY_NOT_MEMBERSHIP in crossed.denial_reasons
    copied = MasterRoutePlan.from_dict(plan_a.to_dict())
    reconstructed = _hypothetical(
        (session_a, copied, copied.steps[0], copied.steps[0].invocation),
        _server(),
    )
    assert reconstructed.decision is MasterRuntimePolicyDecision.DENY
    assert Reason.STRING_IDENTITY_NOT_MEMBERSHIP in reconstructed.denial_reasons
    assert Reason.CROSS_SESSION in reconstructed.denial_reasons
    _assert_not_a_permit(crossed)


def test_caller_proof_cannot_authorize():
    bundle = _retain(_live())
    server = _server()
    allowed = _hypothetical(bundle, server)
    os.environ["WILVOR_POLICY_TEST_MODE"] = "1"
    try:
        assert os.environ.get("ENVIRONMENT") == "test"
        denied = _production(
            bundle,
            server,
            authorized=True,
            caller_policy_decision="ALLOW",
            claimed_test_mode=True,
            serialized_decision=allowed,
        )
        harness_poisoned = _hypothetical(bundle, server, authorized=True)
    finally:
        os.environ.pop("WILVOR_POLICY_TEST_MODE", None)
    assert denied.decision is MasterRuntimePolicyDecision.DENY
    assert Reason.PRINCIPAL_NOT_HOST_AUTHENTICATED in denied.denial_reasons
    assert Reason.PRINCIPAL_LABEL_NOT_AUTHENTICATION in denied.denial_reasons
    assert Reason.CALLER_AUTHORIZED_FLAG_NOT_AUTHORITY in denied.denial_reasons
    assert Reason.CALLER_PROOF_NOT_AUTHORITY in denied.denial_reasons
    assert Reason.SERIALIZED_DECISION_NOT_AUTHORITY in denied.denial_reasons
    assert Reason.CLAIMED_TEST_MODE_NOT_AUTHORITY in denied.denial_reasons
    assert harness_poisoned.decision is MasterRuntimePolicyDecision.DENY
    assert Reason.CALLER_AUTHORIZED_FLAG_NOT_AUTHORITY in harness_poisoned.denial_reasons
    fingerprint_spoof = _production(
        bundle,
        server,
        principal_ref=None,
        serialized_decision=allowed.policy_fingerprint,
    )
    assert fingerprint_spoof.decision is MasterRuntimePolicyDecision.DENY
    assert Reason.SERIALIZED_DECISION_NOT_AUTHORITY in fingerprint_spoof.denial_reasons
    _assert_not_a_permit(denied)


def test_arbitrary_decision_target_denies():
    bundle = _retain(
        _decision(),
        master_request_id="master-req-decision-1",
        correlation_id="corr-decision-1",
        target_candidate_aircraft_id="N12345",
    )
    result = _hypothetical(
        bundle,
        _server(
            tool_name="get_current_risk_evidence",
            resource_id="aircraft",
            operation=ReadOperation.GET_ITEM,
            arguments=(("aircraft_id", "N12345"),),
            target_kind=TargetObligationKind.BOUND_AIRCRAFT,
            target_id="*",
        ),
        decision_target_scope="*",
    )
    assert result.decision is MasterRuntimePolicyDecision.DENY
    assert Reason.ARBITRARY_DECISION_TARGET in result.denial_reasons
    assert Reason.TARGET_NOT_AUTHORIZED in result.denial_reasons
    assert Reason.UNRESOLVED_ROUTE_GATE in result.denial_reasons
    assert MasterRouteGate.APPLICATION_TARGET_CANDIDATE_PRESENT in result.unresolved_gates
    assert result.decision_target_scope is None
    assert result.specialist is MasterSpecialist.DECISION
    _assert_not_a_permit(result)


def test_policy_evaluation_cannot_become_a_permit():
    allowed = _hypothetical(_retain(_live()), _server())
    denied = _production(_retain(_live()), _server(), principal_ref=None)
    assert allowed.decision is MasterRuntimePolicyDecision.ALLOW
    for result in (allowed, denied):
        with pytest.raises(ContractValidationError, match="policy_evaluation_is_not_a_permit"):
            promote_policy_evaluation_to_permit(result)
        with pytest.raises(ContractValidationError, match="policy_evaluation_is_not_a_permit"):
            bool(result)
        with pytest.raises(ContractValidationError):
            replace(result, execution_permitted=True)
        with pytest.raises(ContractValidationError):
            replace(result, permit_issued=True)
        with pytest.raises(ContractValidationError):
            replace(result, authority_origin=PolicyAuthorityOrigin.HOST_AUTHENTICATED)
    with pytest.raises(ContractValidationError):
        replace(denied, decision=MasterRuntimePolicyDecision.ALLOW, denial_reasons=())


def test_fingerprint_tracks_the_evaluated_deadline():
    bundle = _retain(_live())
    first = _hypothetical(bundle, _server())
    second = _hypothetical(bundle, _server())
    later = _hypothetical(bundle, _server(deadline_utc="2026-01-15T00:06:00Z"))
    assert first.policy_fingerprint == second.policy_fingerprint
    assert first.decision is MasterRuntimePolicyDecision.ALLOW
    assert later.decision is MasterRuntimePolicyDecision.ALLOW
    assert later.policy_fingerprint != first.policy_fingerprint
    assert later.deadline_utc == "2026-01-15T00:06:00Z"


def test_scan_wildcard_and_cross_specialist_reads_deny():
    bundle = _retain(_live())
    scanned = _hypothetical(bundle, _server(operation=ReadOperation.SCAN))
    wildcard = _hypothetical(bundle, _server(resource_id="*"))
    crossed = _hypothetical(
        bundle,
        _server(
            tool_name="summarize_historical_encounters",
            resource_id="historical.encounters",
            operation=ReadOperation.QUERY,
            arguments=(
                ("start_utc", START),
                ("end_utc", END),
                ("aircraft_id", "N123"),
            ),
        ),
    )
    assert scanned.decision is MasterRuntimePolicyDecision.DENY
    assert Reason.SCAN_NOT_PERMITTED in scanned.denial_reasons
    assert wildcard.decision is MasterRuntimePolicyDecision.DENY
    assert Reason.WILDCARD_RESOURCE in wildcard.denial_reasons
    assert crossed.decision is MasterRuntimePolicyDecision.DENY
    assert Reason.CROSS_SPECIALIST_RESOURCE in crossed.denial_reasons
    assert Reason.UNSUPPORTED_TOOL in crossed.denial_reasons
    _assert_not_a_permit(scanned)


def test_stale_clock_cancellation_budget_and_arbitrary_region_deny():
    bundle = _retain(_live())
    stale = _hypothetical(
        bundle,
        _server(
            evaluation_utc="2026-01-14T23:59:59Z",
            accepted_at_utc="2026-01-14T23:59:59Z",
        ),
    )
    cancelled = _hypothetical(bundle, _server(cancelled=True))
    _executions, _turns, calls = server_read_budget(MasterSpecialist.LIVE_OPS)
    exhausted = _hypothetical(bundle, _server(tool_calls_used=calls))
    region = _hypothetical(bundle, _server(), region_scope="Western United States")
    inflated = _hypothetical(bundle, _server(), max_tool_calls=calls + 1)
    assert Reason.STALE_OR_WRONG_CLOCK in stale.denial_reasons
    assert Reason.CANCELLED in cancelled.denial_reasons
    assert Reason.BUDGET_EXHAUSTED in exhausted.denial_reasons
    assert Reason.ARBITRARY_REGION_TEXT in region.denial_reasons
    assert Reason.REGION_AUTHORITY_NOT_ESTABLISHED in region.denial_reasons
    assert Reason.CALLER_BUDGET_NOT_AUTHORITY in inflated.denial_reasons
    for result in (stale, cancelled, exhausted, region, inflated):
        assert result.decision is MasterRuntimePolicyDecision.DENY
        _assert_not_a_permit(result)


def test_pairs_match_the_boundary_and_the_boundary_stays_closed():
    assert SPECIALIST_CAPABILITY_PAIRS == BOUNDARY_PAIRS
    assert HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE is False
    assert HOST_GRANT_ISSUER_AVAILABLE is False
    assert AUTHENTICATED_PRINCIPAL_AVAILABLE is False
    assert PRODUCTION_POLICY_ENGINE_AVAILABLE is False
    assert list(MasterExecutionPolicyStatus) == [MasterExecutionPolicyStatus.DENIED]
    assert list(MasterRuntimePolicyDecision) == [
        MasterRuntimePolicyDecision.ALLOW,
        MasterRuntimePolicyDecision.DENY,
    ]
    assert "PERMIT" not in {item.value for item in MasterRuntimePolicyDecision}
    assert _evaluate_hypothetical_runtime_policy.__name__ not in (
        __import__("wilvor_ai.master_runtime_policy", fromlist=["__all__"]).__all__
    )
