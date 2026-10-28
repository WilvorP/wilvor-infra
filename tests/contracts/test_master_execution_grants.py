"""Local-harness execution grants stay distinct from production authority."""

from __future__ import annotations

import copy
import pickle
from datetime import datetime, timedelta, timezone

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError, TemporalScope
from wilvor_ai.master_contracts import MasterProductionAuthority, MasterPurpose, MasterSpecialist
from wilvor_ai.master_execution_fanout import MasterExecutionClaim
from wilvor_ai.master_execution_grants import (
    HMAC_PREVENTS_REPLAY,
    INTEGRATION_OBLIGATIONS,
    LOCAL_HARNESS_IS_PRODUCTION_AUTHORIZATION,
    MAX_DECISION_STEPS,
    MAX_ROUTE_STEPS,
    PRODUCTION_BUDGET_HOST_INTEGRATED,
    PRODUCTION_CONSUMPTION_LEDGER_INTEGRATED,
    PRODUCTION_GRANT_ISSUANCE_AVAILABLE,
    PRODUCTION_POLICY_HOST_INTEGRATED,
    PRODUCTION_PRINCIPAL_HOST_INTEGRATED,
    PRODUCTION_SCHEDULER_HOST_INTEGRATED,
    PRODUCTION_SCOPE_HOST_INTEGRATED,
    PRODUCTION_SIGNING_KEY_STORE_INTEGRATED,
    SOURCE_AUTHENTICITY_NOT_ESTABLISHED,
    GrantEvaluationClock,
    GrantEvaluationLimitation,
    GrantLifecycle,
    GrantRejectionReason,
    LocalHarnessConsumptionLedger,
    LocalHarnessExecutionGrant,
    LocalHarnessGrantIssuer,
    LocalHarnessPrincipal,
    LogicalReadOperation,
    MasterExecutionGrantEnvelope,
    evaluate_grant_envelope,
    issue_production_execution_grant,
    parse_execution_grant,
    refuse_production_grant_issuance,
)
from wilvor_ai.master_execution_policy_boundary import MasterExecutionPolicyAssessment
from wilvor_ai.master_execution_scope import MasterExecutionScope
from wilvor_ai.master_gateway_identity import MasterGatewayIdentityAssessment
from wilvor_ai.master_local_capability import LocalCapabilityHandle
from wilvor_ai.master_planning_session import MasterPlanningSessionRecord
from wilvor_ai.master_read_resource_guard import (
    MasterReadCheckStatus,
    MasterReadResourceCheck,
    ReadOperation,
    server_read_argument_names,
    server_read_budget,
    server_read_resources,
)
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS as ROUTING_MAX_DECISION_STEPS,
    MAX_ROUTE_STEPS as ROUTING_MAX_ROUTE_STEPS,
    MasterRouteExecutionAuthority,
)


ACCEPTED = "2026-10-27T19:00:00Z"
NOW = datetime(2026, 10, 27, 20, 0, tzinfo=timezone.utc)
EXPIRES = "2026-10-27T21:00:00Z"
DEADLINE = "2026-10-27T21:00:00Z"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT


def _clock() -> GrantEvaluationClock:
    return GrantEvaluationClock(NOW)


def _read(resource_id: str, operation: ReadOperation) -> LogicalReadOperation:
    return LogicalReadOperation(resource_id, operation)


def _envelope(**overrides: object) -> MasterExecutionGrantEnvelope:
    values: dict[str, object] = {
        "master_request_id": "request-1",
        "planning_session_id": "session-1",
        "step_id": "step-1",
        "invocation_id": "invocation-1",
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "capability": LIVE,
        "principal_ref": "principal-1",
        "policy_revision": "policy-rev-1",
        "tool_name": "get_aircraft_operational_context",
        "resource_scope": (_read("aircraft", ReadOperation.GET_ITEM),),
        "region_scope": ("CA",),
        "temporal_scopes": (TemporalScope.CURRENT,),
        "historical_window": None,
        "decision_target_scope": None,
        "accepted_at_utc": ACCEPTED,
        "deadline_utc": DEADLINE,
        "expires_at_utc": EXPIRES,
        "idempotency_key": "idem-1",
        "max_model_turns": 1,
        "max_tool_calls": 1,
        "correlation_id": "correlation-1",
    }
    values.update(overrides)
    return MasterExecutionGrantEnvelope(**values)  # type: ignore[arg-type]


def _bindings(envelope: MasterExecutionGrantEnvelope, principal: object, **overrides: object) -> dict[str, object]:
    evaluation = evaluate_grant_envelope(envelope)
    assert evaluation.bounds_accepted is True
    values: dict[str, object] = {
        "principal": principal,
        "planning_session_id": envelope.planning_session_id,
        "step_id": envelope.step_id,
        "invocation_id": envelope.invocation_id,
        "specialist": envelope.specialist,
        "purpose": envelope.purpose,
        "capability": envelope.capability,
        "policy_revision": envelope.policy_revision,
        "tool_name": evaluation.tool_name,
        "resource_scope": evaluation.resource_scope,
        "argument_names": evaluation.argument_names,
        "region_scope": evaluation.region_scope,
        "temporal_scopes": evaluation.temporal_scopes,
        "historical_window": evaluation.historical_window,
        "decision_target_scope": evaluation.decision_target_scope,
        "accepted_at_utc": envelope.accepted_at_utc,
        "deadline_utc": envelope.deadline_utc,
        "expires_at_utc": envelope.expires_at_utc,
        "idempotency_key": envelope.idempotency_key,
        "max_model_turns": envelope.max_model_turns,
        "max_tool_calls": envelope.max_tool_calls,
        "correlation_id": envelope.correlation_id,
        "master_request_id": envelope.master_request_id,
    }
    values.update(overrides)
    return values


def _issued(**overrides: object):
    clock = _clock()
    issuer = LocalHarnessGrantIssuer(clock)
    envelope = _envelope(**overrides)
    principal = issuer.seal_principal(envelope.principal_ref)
    grant = issuer.issue(issuer.prepare(envelope, principal))
    return clock, issuer, envelope, principal, grant


def _assert_not_production(result: object) -> None:
    assert bool(result) is False
    assert result.execution_authority is MasterRouteExecutionAuthority.DENIED  # type: ignore[attr-defined]
    assert result.production_authority is MasterProductionAuthority.DENIED  # type: ignore[attr-defined]
    assert result.source_authenticity == SOURCE_AUTHENTICITY_NOT_ESTABLISHED  # type: ignore[attr-defined]
    assert result.execution_permitted is False  # type: ignore[attr-defined]
    assert result.dispatch_permitted is False  # type: ignore[attr-defined]
    assert result.specialist_dispatched is False  # type: ignore[attr-defined]
    assert result.atc_instructions_authorized is False  # type: ignore[attr-defined]
    assert result.data_plane_write_authorized is False  # type: ignore[attr-defined]
    assert result.source_authenticated is False  # type: ignore[attr-defined]
    assert result.establishes_authority is False  # type: ignore[attr-defined]
    assert result.hmac_prevents_replay is False  # type: ignore[attr-defined]


def test_local_harness_lifecycle_is_not_a_production_grant():
    executions, turns, calls = server_read_budget(MasterSpecialist.LIVE_OPS)
    assert executions == 1
    assert MAX_ROUTE_STEPS is ROUTING_MAX_ROUTE_STEPS
    assert MAX_DECISION_STEPS is ROUTING_MAX_DECISION_STEPS
    assert MAX_ROUTE_STEPS == 4
    assert MAX_DECISION_STEPS == 1
    clock, issuer, envelope, principal, grant = _issued(max_model_turns=turns, max_tool_calls=calls)
    evaluation = evaluate_grant_envelope(envelope)
    assert evaluation.lifecycle is GrantLifecycle.EVALUATED
    assert evaluation.bounds_accepted is True
    assert evaluation.production_grant_issued is False
    assert evaluation.decision_target_trusted is False
    assert GrantEvaluationLimitation.POLICY_REVISION_IS_NOT_AGENT3_DECISION in evaluation.limitations
    assert evaluation.argument_names == tuple(
        sorted(server_read_argument_names(MasterSpecialist.LIVE_OPS, "get_aircraft_operational_context"))
    )
    assert evaluation.resource_scope is not None
    assert frozenset((item.resource_id, item.operation) for item in evaluation.resource_scope) == (
        server_read_resources(MasterSpecialist.LIVE_OPS, "get_aircraft_operational_context")
    )
    _assert_not_production(evaluation)
    assert bool(envelope) is False
    assert bool(grant) is False
    assert issuer.production_grants_issued == 0
    verification = issuer.verify(grant, **_bindings(envelope, principal))
    assert verification.lifecycle is GrantLifecycle.HOST_ISSUED_LOCAL_HARNESS
    assert verification.local_harness_binding_accepted is True
    assert verification.signature_intact is True
    assert verification.reason is None
    _assert_not_production(verification)
    claim = grant.serialized_claim()
    assert claim["accepted_at_utc"] == ACCEPTED
    assert claim["authority_domain"] == "LOCAL_HARNESS"
    assert not any(isinstance(value, bytes) for value in claim.values())
    parsed = parse_execution_grant(claim)
    assert bool(parsed) is False
    assert parsed.establishes_authority is False
    parsed_verification = issuer.verify(parsed, **_bindings(envelope, principal))
    assert parsed_verification.local_harness_binding_accepted is True
    assert parsed_verification.establishes_authority is False
    ledger = LocalHarnessConsumptionLedger()
    assert ledger.durable is False
    assert ledger.distributed is False
    consumed = issuer.consume(parsed, ledger, **_bindings(envelope, principal))
    assert consumed.lifecycle is GrantLifecycle.CONSUMED_LOCAL_HARNESS
    assert consumed.local_harness_consumption_recorded is True
    assert consumed.durable_single_use is False
    assert consumed.distributed_ledger is False
    assert ledger.holds_nonce(str(claim["nonce"])) is True
    _assert_not_production(consumed)
    clock.advance_to(NOW + timedelta(minutes=5))
    assert grant.serialized_claim()["accepted_at_utc"] == ACCEPTED


def test_production_issuance_stays_closed_for_allowlist_claims_and_tokens():
    assert PRODUCTION_GRANT_ISSUANCE_AVAILABLE is False
    assert PRODUCTION_PRINCIPAL_HOST_INTEGRATED is False
    assert PRODUCTION_POLICY_HOST_INTEGRATED is False
    assert PRODUCTION_CONSUMPTION_LEDGER_INTEGRATED is False
    assert PRODUCTION_BUDGET_HOST_INTEGRATED is False
    assert PRODUCTION_SCOPE_HOST_INTEGRATED is False
    assert PRODUCTION_SCHEDULER_HOST_INTEGRATED is False
    assert PRODUCTION_SIGNING_KEY_STORE_INTEGRATED is False
    assert HMAC_PREVENTS_REPLAY is False
    assert LOCAL_HARNESS_IS_PRODUCTION_AUTHORIZATION is False
    check = MasterReadResourceCheck(
        status=MasterReadCheckStatus.WITHIN_ALLOWLIST,
        denial_reasons=(),
        specialist=MasterSpecialist.LIVE_OPS,
        capability=LIVE,
        tool_name="get_aircraft_operational_context",
        resource_id="aircraft",
        operation=ReadOperation.GET_ITEM,
    )
    refusal = refuse_production_grant_issuance(check)
    assert refusal.reason is GrantRejectionReason.READ_CHECK_NOT_A_GRANT
    assert refusal.observed_allow_label == "WITHIN_ALLOWLIST"
    assert refusal.production_grant_issued is False
    _assert_not_production(refusal)
    claim = MasterExecutionClaim(
        session_id="session-1",
        step_id="step-1",
        invocation_id="invocation-1",
        specialist=MasterSpecialist.LIVE_OPS,
        claimer_id="claimer-1",
        planning_gates=(),
        planning_ready_to_execute=True,
    )
    assert refuse_production_grant_issuance(claim).reason is GrantRejectionReason.EXECUTION_CLAIM_NOT_A_GRANT
    assessment = object.__new__(MasterExecutionPolicyAssessment)
    assert refuse_production_grant_issuance(assessment).reason is GrantRejectionReason.POLICY_EVALUATION_NOT_A_GRANT
    assert refuse_production_grant_issuance(object.__new__(MasterExecutionScope)).reason is (
        GrantRejectionReason.SCOPE_NOT_A_GRANT
    )
    handle = LocalCapabilityHandle.from_description({"kind": "not-a-capability"})
    assert refuse_production_grant_issuance(handle).reason is GrantRejectionReason.LOCAL_CAPABILITY_NOT_A_GRANT
    assert refuse_production_grant_issuance(object.__new__(MasterGatewayIdentityAssessment)).reason is (
        GrantRejectionReason.GATEWAY_IDENTITY_NOT_A_PRINCIPAL
    )
    assert refuse_production_grant_issuance(object.__new__(MasterPlanningSessionRecord)).reason is (
        GrantRejectionReason.PLANNING_SESSION_NOT_A_GRANT
    )
    assert refuse_production_grant_issuance("ALLOW").reason is GrantRejectionReason.CALLER_TOKEN_NOT_A_GRANT
    assert refuse_production_grant_issuance(b"secret-token").reason is GrantRejectionReason.CALLER_TOKEN_NOT_A_GRANT
    assert refuse_production_grant_issuance(True).reason is GrantRejectionReason.CALLER_TOKEN_NOT_A_GRANT
    event = {
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-1"}}}},
        "headers": {"authorization": "Bearer forged"},
        "WILVOR_TEST_MODE": "1",
    }
    assert refuse_production_grant_issuance(event).reason is GrantRejectionReason.CALLER_EVENT_NOT_A_GRANT
    assert refuse_production_grant_issuance({"execution_permitted": True}).reason is (
        GrantRejectionReason.SERIALIZED_CLAIM_NOT_AUTHORITY
    )
    assert refuse_production_grant_issuance(_envelope()).reason is GrantRejectionReason.DESCRIPTIVE_ENVELOPE_NOT_A_GRANT
    assert refuse_production_grant_issuance(evaluate_grant_envelope(_envelope())).reason is (
        GrantRejectionReason.EVALUATION_NOT_A_GRANT
    )
    assert refuse_production_grant_issuance(object()).reason is GrantRejectionReason.DENIED_BY_DEFAULT
    _clock_issuer = _issued()
    grant = _clock_issuer[4]
    assert refuse_production_grant_issuance(grant).reason is GrantRejectionReason.LOCAL_HARNESS_GRANT_NOT_PRODUCTION
    assert refuse_production_grant_issuance(parse_execution_grant(grant.serialized_claim())).reason is (
        GrantRejectionReason.SERIALIZED_CLAIM_NOT_AUTHORITY
    )
    with pytest.raises(ContractValidationError, match="production_grant_issuance_closed"):
        issue_production_execution_grant(check, claim, assessment, event, True, "ALLOW")
    with pytest.raises(ContractValidationError, match="grant_constructor_refused"):
        LocalHarnessExecutionGrant()
    with pytest.raises(ContractValidationError, match="grant_constructor_refused"):
        LocalHarnessPrincipal()
    assert not hasattr(issue_production_execution_grant, "make_authorized_grant")


def test_forgery_tampering_and_cross_binding_are_rejected_before_consumption():
    issuer_clock, issuer, envelope, principal, grant = _issued()
    del issuer_clock
    ledger = LocalHarnessConsumptionLedger()
    forged = dict(grant.serialized_claim())
    forged["nonce"] = "forged-nonce"
    forged["signature"] = "ab" * 32
    forged_result = issuer.verify(parse_execution_grant(forged), **_bindings(envelope, principal))
    assert forged_result.reason is GrantRejectionReason.FORGED
    assert forged_result.signature_intact is False
    assert forged_result.local_harness_binding_accepted is False
    tampered = dict(grant.serialized_claim())
    tampered["step_id"] = "step-other"
    tampered_result = issuer.verify(parse_execution_grant(tampered), **_bindings(envelope, principal))
    assert tampered_result.reason is GrantRejectionReason.TAMPERED
    assert tampered_result.execution_permitted is False
    other_principal = issuer.seal_principal(envelope.principal_ref)
    principal_result = issuer.verify(grant, **_bindings(envelope, other_principal))
    assert principal_result.reason is GrantRejectionReason.PRINCIPAL_MISMATCH
    assert principal_result.signature_intact is True
    session_result = issuer.verify(grant, **_bindings(envelope, principal, planning_session_id="session-2"))
    assert session_result.reason is GrantRejectionReason.SESSION_MISMATCH
    step_result = issuer.verify(grant, **_bindings(envelope, principal, step_id="step-2"))
    assert step_result.reason is GrantRejectionReason.STEP_MISMATCH
    assert step_result.signature_intact is True
    specialist_result = issuer.verify(
        grant,
        **_bindings(envelope, principal, specialist=MasterSpecialist.HISTORICAL_ANALYTICS),
    )
    assert specialist_result.reason is GrantRejectionReason.SPECIALIST_MISMATCH
    copied = copy.copy(grant)
    assert issuer.verify(copied, **_bindings(envelope, principal)).reason is GrantRejectionReason.COPIED_GRANT
    other_issuer = LocalHarnessGrantIssuer(_clock())
    assert other_issuer.verify(grant, **_bindings(envelope, principal)).reason is GrantRejectionReason.FOREIGN_ISSUER
    denied = issuer.consume(parse_execution_grant(tampered), ledger, **_bindings(envelope, principal))
    assert denied.local_harness_consumption_recorded is False
    assert ledger.holds_nonce(str(grant.serialized_claim()["nonce"])) is False
    with pytest.raises(ContractValidationError, match="production_grant_issuance_closed"):
        parse_execution_grant({**grant.serialized_claim(), "authority_domain": "PRODUCTION"})


def test_expiry_is_latched_and_replay_is_rejected_without_trusting_hmac():
    _clock, issuer, envelope, principal, grant = _issued()
    bindings = _bindings(envelope, principal)
    ledger = LocalHarnessConsumptionLedger()
    missing = issuer.consume(grant, None, **bindings)
    assert missing.reason is GrantRejectionReason.MISSING_LEDGER
    assert missing.signature_intact is True
    duck = type("Ledger", (), {"reserve_once": lambda *args, **kwargs: True})()
    assert issuer.consume(grant, duck, **bindings).reason is GrantRejectionReason.LEDGER_NOT_INTEGRATED
    assert issuer.verify(grant, **bindings).local_harness_binding_accepted is True
    duplicate_ledger = LocalHarnessConsumptionLedger()
    assert duplicate_ledger.reserve_once(nonce="other-nonce", idempotency_key=envelope.idempotency_key) is True
    duplicate = issuer.consume(grant, duplicate_ledger, **bindings)
    assert duplicate.reason is GrantRejectionReason.DUPLICATE_CONSUMPTION
    assert duplicate.signature_intact is True
    assert issuer.verify(grant, **bindings).reason is None
    consumed = issuer.consume(grant, ledger, **bindings)
    assert consumed.local_harness_consumption_recorded is True
    assert consumed.hmac_prevents_replay is False
    replay_verification = issuer.verify(parse_execution_grant(grant.serialized_claim()), **bindings)
    assert replay_verification.reason is GrantRejectionReason.REPLAY
    assert replay_verification.signature_intact is True
    assert replay_verification.local_harness_binding_accepted is False
    assert HMAC_PREVENTS_REPLAY is False
    fresh = LocalHarnessConsumptionLedger()
    replay = issuer.consume(grant, fresh, **bindings)
    assert replay.reason is GrantRejectionReason.REPLAY
    assert replay.signature_intact is True
    assert fresh.holds_nonce(str(grant.serialized_claim()["nonce"])) is False
    second = issuer.consume(parse_execution_grant(grant.serialized_claim()), ledger, **bindings)
    assert second.reason is GrantRejectionReason.REPLAY


def test_unconsumed_grant_expires_before_the_ledger_and_stays_expired():
    clock, issuer, envelope, principal, grant = _issued()
    bindings = _bindings(envelope, principal)
    ledger = LocalHarnessConsumptionLedger()
    clock.advance_to(datetime(2026, 10, 27, 21, 0, tzinfo=timezone.utc))
    expired = issuer.verify(grant, **bindings)
    assert expired.reason is GrantRejectionReason.EXPIRED
    assert expired.signature_intact is True
    assert expired.execution_permitted is False
    assert issuer.consume(grant, ledger, **bindings).reason is GrantRejectionReason.EXPIRED
    assert ledger.holds_nonce(str(grant.serialized_claim()["nonce"])) is False
    clock.advance_to(NOW)
    assert issuer.verify(grant, **bindings).reason is GrantRejectionReason.EXPIRED


def test_key_rotation_retires_verification_without_consuming_the_nonce():
    clock, issuer, envelope, principal, grant = _issued()
    first_key = issuer.signing_key_id
    rotated = issuer.rotate_signing_key()
    assert rotated != first_key
    assert issuer.verify(grant, **_bindings(envelope, principal)).local_harness_binding_accepted is True
    later_envelope = _envelope(idempotency_key="idem-2", step_id="step-2", invocation_id="invocation-2")
    later = issuer.issue(issuer.prepare(later_envelope, principal))
    assert later.serialized_claim()["key_id"] == rotated
    assert grant.serialized_claim()["key_id"] == first_key
    issuer.retire_verification_key(first_key)
    retired = issuer.verify(grant, **_bindings(envelope, principal))
    assert retired.reason is GrantRejectionReason.KEY_RETIRED
    assert issuer.verify(later, **_bindings(later_envelope, principal)).local_harness_binding_accepted is True
    ledger = LocalHarnessConsumptionLedger()
    assert issuer.consume(grant, ledger, **_bindings(envelope, principal)).reason is GrantRejectionReason.KEY_RETIRED
    assert ledger.holds_nonce(str(grant.serialized_claim()["nonce"])) is False
    with pytest.raises(ContractValidationError, match="active_signing_key_not_retired"):
        issuer.retire_verification_key(rotated)
    with pytest.raises(ContractValidationError, match="issuer_not_serializable"):
        pickle.dumps(issuer)
    del clock


def test_duplicate_idempotency_and_bounds_fail_closed():
    _clock, issuer, envelope, principal, grant = _issued()
    with pytest.raises(ContractValidationError, match="duplicate_idempotency"):
        issuer.issue(issuer.prepare(envelope, principal))
    assert issuer.verify(grant, **_bindings(envelope, principal)).local_harness_binding_accepted is True
    with pytest.raises(ContractValidationError, match="scan_not_permitted"):
        LogicalReadOperation("aircraft", ReadOperation.SCAN)
    with pytest.raises(ContractValidationError, match="data_plane_write_not_authorized"):
        LogicalReadOperation("put_item", ReadOperation.GET_ITEM)
    atc = evaluate_grant_envelope(
        _envelope(capability=AgentCapability.ISSUE_ATC_INSTRUCTIONS, idempotency_key="idem-atc")
    )
    assert atc.bounds_accepted is False
    assert GrantRejectionReason.ATC_NOT_AUTHORIZED in atc.reasons
    assert GrantRejectionReason.PROHIBITED_CAPABILITY in atc.reasons
    assert atc.atc_instructions_authorized is False
    write = evaluate_grant_envelope(_envelope(tool_name="put_item", idempotency_key="idem-write"))
    assert GrantRejectionReason.DATA_PLANE_WRITE_NOT_AUTHORIZED in write.reasons
    assert write.data_plane_write_authorized is False
    ceiling = evaluate_grant_envelope(_envelope(max_model_turns=5, idempotency_key="idem-budget"))
    assert GrantRejectionReason.BUDGET_EXCEEDS_SERVER_CEILING in ceiling.reasons
    wide = evaluate_grant_envelope(
        _envelope(argument_names=("aircraft_id", "sql"), idempotency_key="idem-args")
    )
    assert GrantRejectionReason.CALLER_ARGUMENT_NAMES_NOT_AUTHORITY in wide.reasons
    region = evaluate_grant_envelope(_envelope(region_scope=("*",), idempotency_key="idem-region"))
    assert GrantRejectionReason.REGION_NOT_AUTHORIZED in region.reasons
    historical = evaluate_grant_envelope(
        _envelope(
            specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
            purpose=MasterPurpose.ANSWER,
            capability=AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS,
            tool_name="summarize_historical_encounters",
            resource_scope=(_read("historical.encounters", ReadOperation.QUERY),),
            temporal_scopes=(TemporalScope.HISTORICAL,),
            historical_window=("2026-10-01T00:00:00Z", "2026-10-09T00:00:00Z"),
            idempotency_key="idem-history",
        )
    )
    assert GrantRejectionReason.HISTORICAL_INTERVAL_EXCEEDS_CEILING in historical.reasons
    network = evaluate_grant_envelope(
        _envelope(tool_name="get_observed_network_state", idempotency_key="idem-network")
    )
    assert GrantRejectionReason.RESOURCE_SCOPE_NOT_EXACT in network.reasons
    with pytest.raises(ContractValidationError, match="grant_bounds_rejected"):
        issuer.prepare(_envelope(max_tool_calls=99, idempotency_key="idem-over"), principal)
    with pytest.raises(ContractValidationError, match="invalid_issuance_ticket"):
        issuer.issue(grant)


def test_decision_target_binding_does_not_authorize_an_aviation_action():
    envelope = _envelope(
        specialist=MasterSpecialist.DECISION,
        purpose=MasterPurpose.ANSWER,
        capability=AgentCapability.EXPLAIN_EXISTING_RISK,
        tool_name="get_current_risk_evidence",
        resource_scope=(_read("aircraft", ReadOperation.GET_ITEM),),
        temporal_scopes=(TemporalScope.CURRENT,),
        decision_target_scope="target-1",
        idempotency_key="idem-decision",
    )
    evaluation = evaluate_grant_envelope(envelope)
    assert evaluation.bounds_accepted is True
    assert evaluation.decision_target_trusted is False
    assert evaluation.atc_instructions_authorized is False
    issuer = LocalHarnessGrantIssuer(_clock())
    principal = issuer.seal_principal(envelope.principal_ref)
    grant = issuer.issue(issuer.prepare(envelope, principal))
    verified = issuer.verify(grant, **_bindings(envelope, principal))
    assert verified.local_harness_binding_accepted is True
    assert verified.decision_target_trusted is False
    mismatch = issuer.verify(grant, **_bindings(envelope, principal, decision_target_scope="target-2"))
    assert mismatch.reason is GrantRejectionReason.DECISION_TARGET_MISMATCH
    assert mismatch.execution_permitted is False
    missing = evaluate_grant_envelope(
        _envelope(
            specialist=MasterSpecialist.DECISION,
            purpose=MasterPurpose.ANSWER,
            capability=AgentCapability.EXPLAIN_EXISTING_RISK,
            tool_name="get_current_risk_evidence",
            decision_target_scope=None,
            idempotency_key="idem-target-missing",
        )
    )
    assert GrantRejectionReason.DECISION_TARGET_NOT_BOUND in missing.reasons


def test_integration_obligations_stay_unintegrated():
    agents = {item.agent: item for item in INTEGRATION_OBLIGATIONS}
    assert set(agents) == {"Agent 2", "Agent 3", "Agent 5", "Agent 6", "Agent 7", "Agent 9"}
    assert agents["Agent 3"].module == "wilvor_ai.master_runtime_policy"
    assert "policy_revision" in agents["Agent 3"].obligation
    assert agents["Agent 5"].module == "wilvor_ai.master_execution_ledger"
    assert "reserve_once" in agents["Agent 5"].obligation
    assert "HMAC does not prevent replay" in agents["Agent 5"].obligation
    assert agents["Agent 6"].module == "wilvor_ai.master_runtime_budget"
    assert agents["Agent 9"].module == "wilvor_ai.master_authorized_scheduler_adapter"
    assert "fanout claim" in agents["Agent 9"].obligation
    assert all(item.status == "NOT_INTEGRATED" for item in INTEGRATION_OBLIGATIONS)
    with pytest.raises(ContractValidationError, match="integration_not_established"):
        type(INTEGRATION_OBLIGATIONS[0])(
            agent="Agent 5",
            module="wilvor_ai.master_execution_ledger",
            status="INTEGRATED",
            obligation="not allowed",
        )
