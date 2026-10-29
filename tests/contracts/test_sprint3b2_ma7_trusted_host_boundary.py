"""Adversarial baseline for the future trusted execution host.

These tests call the Master contracts that are already on
``5d10c582091997732b97e496940af9fa18afd745``. A forged gateway event, JWT
metadata, an authorization header, matching ids, a copied plan or session,
structural readiness, a fake policy, an allowlisted tool, a local scheduling
claim, a process-local lock, a fake journal, a region or callsign, a missing
or stale risk row, a source-less Historical result, a partial page, empty
rows, a claimed verified zero, a state string, a synthetic result, and an
MA5 factual proposal stay short of a principal, a grant, and authenticated
aviation evidence.

Sprint 3B.2 modules that other agents are still writing are not imported.
No specialist runner, model provider, or cloud API is called.
"""

from __future__ import annotations

import base64
import json
import threading
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from wilvor_ai.contracts import (
    AgentCapability,
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.decision_contracts import RiskPresence, StoredRiskLevel
from wilvor_ai.live_ops_public_contracts import (
    LiveOpsDiscovery,
    LiveOpsDiscoveryStatus,
    LiveOpsEntityHint,
    LiveOpsPublicClaimKind,
)
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
    observe_population,
    observe_typed_aircraft_id,
)
from wilvor_ai.master_decision_execution_readiness import (
    assess_master_decision_execution_readiness,
)
from wilvor_ai.master_decision_target_resolver import (
    DecisionResolutionDefect,
    DecisionResolutionStatus,
    FakeAircraftRead,
    FakeDecisionReadCatalog,
    FakeRiskRecord,
    MasterDecisionTargetResolution,
    RiskRecordObservation,
    resolve_master_decision_target,
)
from wilvor_ai.master_evidence_admission import (
    MasterAdmissionReason,
    MasterAdmissionStatus,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
    admit_master_evidence,
)
from wilvor_ai.master_evidence_corroboration import MasterEvidenceCorroborationInput
from wilvor_ai.master_execution_fanout import (
    MasterExecutionClaim,
    MasterExecutionFanout,
    MasterSchedulingObservation,
    MasterSchedulingState,
)
from wilvor_ai.master_execution_policy_boundary import (
    MasterExecutionPolicyAssessment,
    MasterExecutionPolicyCandidate,
    MasterExecutionPolicyDenialReason,
    assess_master_execution_policy,
)
from wilvor_ai.master_execution_preflight import (
    MasterExecutionPreflightState,
    evaluate_execution_preflight,
)
from wilvor_ai.master_execution_scope import (
    MasterExecutionDenialCategory,
    bind_master_execution_scope,
)
from wilvor_ai.master_gateway_identity import (
    APPLICATION_OWNED_PRINCIPAL_AVAILABLE,
    GATEWAY_IDENTITY_LIMITATIONS,
    GATEWAY_INVOCATION_ATTESTATION_AVAILABLE,
    PRODUCTION_GATEWAY_ISSUANCE_AVAILABLE,
    UNVERIFIED_JWT_DECODING_IS_AUTHENTICATION,
    ApplicationPrincipalStanding,
    GatewayInvocationProvenance,
    IngressAttestationStanding,
    InvocationProvenanceStanding,
    MasterGatewayIdentityConfiguration,
    assess_master_gateway_identity,
    issue_application_principal,
    promote_to_execution_permission,
)
from wilvor_ai.master_historical_binding import prepare_master_historical_binding
from wilvor_ai.master_historical_native_bridge import (
    HistoricalQueryObservation,
    HistoricalRowObservation,
    MasterHistoricalNativeBridgeEnvelope,
    NativeObservationOrigin,
    correlate_master_historical_native_result,
)
from wilvor_ai.master_live_ops_binding import prepare_master_live_ops_binding
from wilvor_ai.master_live_ops_execution_readiness import (
    assess_master_live_ops_execution_readiness,
)
from wilvor_ai.master_live_ops_native_bridge import (
    LIVE_OPS_MISSING_HOST_EXECUTION_GRANT,
    LiveOpsRegionalStringMatch,
    MasterLiveOpsNativeBridge,
    prepare_master_live_ops_native_bridge,
    reject_unauthorized_selectors,
)
from wilvor_ai.master_local_capability import (
    LocalCapabilityRegistry,
    LocalCapabilityScope,
    LocalConsumptionOutcome,
    LocalTestClock,
)
from wilvor_ai.master_orchestration import SyntheticOutcome, SyntheticWorkerResult
from wilvor_ai.master_parallel_contracts import (
    MasterParallelContractBundle,
    MasterResponseLimitation,
)
from wilvor_ai.master_planning_session import (
    MasterPlanningSessionRecord,
    MasterPlanningSessionRegistry,
)
from wilvor_ai.master_read_resource_guard import (
    DISPATCH_GRANT_AVAILABLE,
    PRODUCTION_PERMIT_ISSUER_INTEGRATED,
    MasterReadCheckStatus,
    MasterReadDenialReason,
    MasterReadResourceProposal,
    ReadOperation,
    ServerCancellation,
    ServerReadEvaluation,
    check_master_read_resource,
    promote_read_check_to_dispatch_permit,
    seal_ingress_principal,
    seal_population_scope,
    server_read_resources,
)
from wilvor_ai.master_response_policy import evaluate_master_response_policy
from wilvor_ai.master_response_renderer import render_master_response
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS,
    MAX_ROUTE_STEPS,
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
from wilvor_ai.master_runtime_read_journal import (
    AUTHENTICATED_SOURCE_READ_AVAILABLE,
    EMPTY_ROWS_CREATE_VERIFIED_ZERO,
    LOCAL_FAKE_EXECUTION_IS_SOURCE_AUTHENTICATION,
    MasterRuntimeReadJournal,
    PaginationObservation,
    ReadInvocation,
    ReadJournalGap,
    ReadJournalOutcome,
    RuntimeReadJournalEntry,
    mark_deterministic_local_fake,
)
from wilvor_ai.master_runtime_read_journal import LocalFakeReadResult
from wilvor_ai.master_session_origin_boundary import (
    AuthenticationClaimKind,
    reject_authentication_claim,
)
from wilvor_ai.master_specialist_adapters import describe_planned_adapters
from wilvor_ai.specialist_contracts import (
    SpecialistResult,
    SpecialistStatus,
    UnsupportedReason,
    VerifiedZeroClaim,
    VerifierOutcome,
)
from wilvor_ai.specialist_runtime_contracts import (
    HistoricalSpecialistRunResult,
    SpecialistRunStatus,
)


AS_OF = "2026-01-15T00:00:01Z"
NOW = "2026-01-15T00:00:01Z"
DEADLINE = "2026-01-15T00:05:00Z"
T0 = "2026-01-15T00:00:00Z"
T1 = "2026-01-15T00:00:01Z"
EVALUATION_EPOCH = 1_767_225_600
EXPIRES_AT = EVALUATION_EPOCH + 300
ISSUED_AT = EVALUATION_EPOCH - 30
ISSUER = "https://issuer.wilvor.example/realms/master"
AUDIENCE = "wilvor-master-ai"
SUBJECT = "subject-user-1"
REQUEST_ID = "gw-request-3b2"
ROUTE_KEY = "POST /ai/query"
REQUIRED_SCOPE = "ai.master.invoke"
AIRCRAFT = "AC-1"
CALLSIGN = "UAL100"
FACTUAL_ANSWER = (
    "Aircraft N777WX is inside SIGMET WS01. Recorded risk is HIGH. Affected count is 0."
)
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
Reason = MasterExecutionPolicyDenialReason
ReadReason = MasterReadDenialReason

BOOLEAN_CONTEXT_GAP = (
    MasterExecutionClaim,
    MasterSchedulingObservation,
    MasterExecutionPolicyAssessment,
    MasterLiveOpsNativeBridge,
    MasterHistoricalNativeBridgeEnvelope,
    MasterDecisionTargetResolution,
)


def _errors(func) -> list[str]:
    with pytest.raises(ContractValidationError) as caught:
        func()
    return list(caught.value.errors)


def _closed(value) -> None:
    expected = {
        "execution_authority": MasterRouteExecutionAuthority.DENIED,
        "production_authority": MasterProductionAuthority.DENIED,
        "source_authenticity": MasterSourceAuthenticity.NOT_ESTABLISHED,
        "request_coverage": MasterRequestCoverage.NOT_ESTABLISHED,
        "dispatch_permitted": False,
        "execution_permitted": False,
        "executable": False,
        "evidence_verified": False,
        "execution_authorized": False,
        "specialist_dispatched": False,
        "distributed_production_authorization": False,
        "authenticated_execution": False,
        "evidence_issued": False,
        "verified_zero_admitted": False,
        "geospatial_coverage_established": False,
    }
    present = [name for name in expected if hasattr(value, name)]
    assert present
    for name in present:
        assert getattr(value, name) is expected[name]


def _context(text: str = "Which aircraft are affected?", **overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-3b2",
        "correlation_id": "corr-3b2",
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


def _open(context: MasterPlanningContext, *steps: MasterProposedStep):
    proposal = _proposal(context, *steps)
    decision = authorize_route(proposal, context)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert decision.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    registry = MasterPlanningSessionRegistry()
    record = registry.open_session(proposal, context)
    return registry, record, decision


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


def _forged_token() -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').decode("ascii").rstrip("=")
    payload = {
        "aud": AUDIENCE,
        "exp": EXPIRES_AT,
        "iss": ISSUER,
        "scope": REQUIRED_SCOPE,
        "sub": "forged-subject",
    }
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("ascii")
    ).decode("ascii").rstrip("=")
    return f"Bearer {header}.{body}.forged-signature"


def _gateway_event() -> dict[str, object]:
    return {
        "version": "2.0",
        "routeKey": ROUTE_KEY,
        "rawPath": "/ai/query",
        "rawQueryString": "",
        "headers": {"authorization": _forged_token()},
        "requestContext": {
            "authorizer": {
                "jwt": {
                    "claims": {
                        "iss": ISSUER,
                        "aud": AUDIENCE,
                        "sub": SUBJECT,
                        "exp": str(EXPIRES_AT),
                        "iat": str(ISSUED_AT),
                        "scope": REQUIRED_SCOPE,
                    },
                    "scopes": [REQUIRED_SCOPE],
                }
            },
            "http": {
                "method": "POST",
                "path": "/ai/query",
                "protocol": "HTTP/1.1",
                "sourceIp": "203.0.113.10",
            },
            "requestId": REQUEST_ID,
            "routeKey": ROUTE_KEY,
            "timeEpoch": (EVALUATION_EPOCH + 10_000) * 1000,
        },
        "isBase64Encoded": False,
    }


def _config() -> MasterGatewayIdentityConfiguration:
    return MasterGatewayIdentityConfiguration(
        issuer=ISSUER,
        audience=AUDIENCE,
        required_scopes=(REQUIRED_SCOPE,),
        method="POST",
        raw_path="/ai/query",
        route_key=ROUTE_KEY,
    )


def _live_plan():
    context = _context()
    registry, record, decision = _open(
        context,
        _proposed(
            "live-1",
            MasterSpecialist.LIVE_OPS,
            LIVE,
            temporal_intent=MasterRouteTemporalIntent.CURRENT,
        ),
    )
    plan = registry.plan_for(record)
    bundle = _bundle(context, plan)
    return context, registry, record, decision, plan, bundle


def _history_binding():
    context = _context("What changed over the recorded window?")
    registry, record, decision = _open(
        context,
        _proposed(
            "hist-1",
            MasterSpecialist.HISTORICAL_ANALYTICS,
            HISTORY,
            temporal_intent=MasterRouteTemporalIntent.HISTORICAL,
            historical_start_utc="2026-01-08T00:00:00Z",
            historical_end_utc="2026-01-15T00:00:00Z",
        ),
    )
    plan = registry.plan_for(record)
    bundle = _bundle(context, plan)
    binding = prepare_master_historical_binding(
        bundle,
        _descriptor(bundle, MasterSpecialist.HISTORICAL_ANALYTICS),
    )
    return context, decision, binding


def _run(**overrides: object) -> HistoricalSpecialistRunResult:
    values: dict[str, object] = {
        "status": SpecialistRunStatus.UNAVAILABLE,
        "tool_results": (),
        "tool_result_projections": (),
        "executed_invocations": (),
        "proposed_claims": (),
        "unsupported_reason": None,
        "evaluated_as_of_utc": None,
        "runtime_errors": (),
        "provider_turn_count": 0,
        "executed_tool_call_count": 0,
        "validation_feedback": (),
    }
    values.update(overrides)
    return HistoricalSpecialistRunResult(**values)  # type: ignore[arg-type]


def _evidence(tool_call_id: str, records: tuple[SourceRecord, ...] = ()) -> Evidence:
    return Evidence(
        source="fixture",
        source_records=records,
        query_timestamp_utc=T0,
        freshness_status=FreshnessStatus.FRESH,
        confidence=ConfidenceLevel.UNKNOWN,
        limitations=(),
        tool_call_id=tool_call_id,
        temporal_scope=TemporalScope.HISTORICAL,
    )


def _tool_result(tool_call_id: str, **overrides: object) -> ToolResult:
    values: dict[str, object] = {
        "tool_name": "summarize_historical_encounters",
        "tool_call_id": tool_call_id,
        "status": ToolResultStatus.SUCCESS,
        "temporal_scope": TemporalScope.HISTORICAL,
        "data": {"records": []},
        "evidence": (_evidence(tool_call_id),),
        "as_of_utc": T0,
        "limitations": (),
        "correlation_id": "corr-3b2",
    }
    values.update(overrides)
    return ToolResult(**values)  # type: ignore[arg-type]


def _read_proposal(**overrides: object) -> MasterReadResourceProposal:
    values: dict[str, object] = {
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "capability": LIVE,
        "tool_name": "search_current_hazards",
        "resource_id": "hazards",
        "operation": ReadOperation.QUERY,
        "arguments": (("region", "CA"),),
        "master_request_id": "master-req-3b2",
        "invocation_id": "inv-read-1",
    }
    values.update(overrides)
    return MasterReadResourceProposal(**values)  # type: ignore[arg-type]


def _evaluation() -> ServerReadEvaluation:
    return ServerReadEvaluation(
        observed_utc=T0,
        deadline_utc=DEADLINE,
        executions_used=0,
        model_turns_used=0,
        tool_calls_used=0,
        cancellation=ServerCancellation(False),
    )


def _journal_execute(tool_call_id: str, result: LocalFakeReadResult) -> RuntimeReadJournalEntry:
    journal = MasterRuntimeReadJournal(_Clock((T0, T1)))
    proposal = journal.propose(
        ReadInvocation(
            invocation_id="inv-journal-1",
            tool_call_id=tool_call_id,
            tool_name="describe_hazard",
            adapter_identity="local-fake-adapter",
            requested_arguments={"airport": "KSEA"},
            master_request_id="master-req-3b2",
        )
    )

    def operation():
        return result

    mark_deterministic_local_fake(operation)
    entry = journal.execute(operation, proposal)
    assert type(journal._lock).__module__ == "_thread"
    return entry


class _Clock:
    def __init__(self, instants: tuple[str, ...]) -> None:
        self._instants = list(instants)

    def now_utc(self) -> str:
        if not self._instants:
            raise AssertionError("clock exhausted")
        return self._instants.pop(0)


def test_closed_authority_vocabulary_has_no_grant():
    assert list(MasterRouteExecutionAuthority) == [MasterRouteExecutionAuthority.DENIED]
    assert list(MasterRequestCoverage) == [MasterRequestCoverage.NOT_ESTABLISHED]
    assert MAX_ROUTE_STEPS == 4
    assert MAX_DECISION_STEPS == 1
    assert PRODUCTION_GATEWAY_ISSUANCE_AVAILABLE is False
    assert GATEWAY_INVOCATION_ATTESTATION_AVAILABLE is False
    assert APPLICATION_OWNED_PRINCIPAL_AVAILABLE is False
    assert UNVERIFIED_JWT_DECODING_IS_AUTHENTICATION is False
    assert PRODUCTION_PERMIT_ISSUER_INTEGRATED is False
    assert DISPATCH_GRANT_AVAILABLE is False
    assert AUTHENTICATED_SOURCE_READ_AVAILABLE is False
    assert LOCAL_FAKE_EXECUTION_IS_SOURCE_AUTHENTICATION is False
    assert EMPTY_ROWS_CREATE_VERIFIED_ZERO is False
    assert set(ReadOperation) == {
        ReadOperation.GET_ITEM,
        ReadOperation.QUERY,
        ReadOperation.SCAN,
    }
    for cls in BOOLEAN_CONTEXT_GAP:
        assert "__bool__" not in cls.__dict__


def test_forged_gateway_event_jwt_and_header_stay_unauthenticated():
    event = _gateway_event()
    assessment = assess_master_gateway_identity(
        event,
        _config(),
        evaluation_epoch=EVALUATION_EPOCH,
    )

    assert bool(assessment) is False
    assert assessment.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert assessment.production_authority is MasterProductionAuthority.DENIED
    assert assessment.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert assessment.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert assessment.execution_permitted is False
    assert assessment.production_authenticated is False
    assert assessment.limitations == GATEWAY_IDENTITY_LIMITATIONS
    assert assessment.evaluated_at_epoch == EVALUATION_EPOCH
    assert assessment.evaluated_at_epoch != event["requestContext"]["timeEpoch"]  # type: ignore[index]
    assert assessment.raw_authorization.present is True
    assert assessment.raw_authorization.value == event["headers"]["authorization"]  # type: ignore[index]
    assert assessment.raw_authorization.payload_decoded is False
    assert assessment.raw_authorization.accepted_as_authentication is False
    assert assessment.authorizer_claims.structurally_valid is True
    assert assessment.authorizer_claims.subject == SUBJECT
    assert assessment.authorizer_claims.issuer == ISSUER
    assert assessment.authorizer_claims.audience == AUDIENCE
    assert assessment.authorizer_claims.authenticates_caller is False
    assert assessment.authorizer_claims.signature_verified is False
    assert assessment.authorizer_claims.jwks_consulted is False
    assert assessment.claimed_request.request_id == REQUEST_ID
    assert assessment.claimed_request.route_key == ROUTE_KEY
    assert assessment.claimed_request.authenticates_caller is False
    assert assessment.invocation_provenance.standing is InvocationProvenanceStanding.NOT_ATTESTED
    assert assessment.invocation_provenance.attested is False
    assert assessment.invocation_provenance.event_dictionary_accepted_as_proof is False
    assert bool(assessment.invocation_provenance) is False
    assert assessment.ingress_attestation.standing is IngressAttestationStanding.UNRESOLVED
    assert assessment.ingress_attestation.protected_gateway_integration_reviewed is False
    assert assessment.ingress_attestation.lambda_invocation_boundary_reviewed is False
    assert bool(assessment.ingress_attestation) is False
    assert assessment.application_principal.standing is ApplicationPrincipalStanding.NOT_ISSUED
    assert assessment.application_principal.issued is False
    assert assessment.application_principal.subject_accepted_as_principal is False
    assert bool(assessment.application_principal) is False
    assert _errors(lambda: GatewayInvocationProvenance(attested=True)) == [
        "gateway_invocation_not_attested"
    ]
    assert _errors(lambda: issue_application_principal(assessment)) == [
        "ingress_attestation_unresolved",
        "application_principal_not_issued",
    ]
    assert _errors(lambda: promote_to_execution_permission(assessment)) == [
        "ingress_attestation_unresolved",
        "execution_permission_not_available",
    ]
    assert _errors(
        lambda: assess_master_gateway_identity(
            event,
            _config(),
            evaluation_epoch=EVALUATION_EPOCH,
            claimed_principal=SUBJECT,
        )
    ) == ["caller_principal_not_accepted"]


def test_matching_ids_and_copied_plan_or_session_do_not_authorize():
    context = _context()
    registry, record, decision = _open(
        context,
        _proposed(
            "live-1",
            MasterSpecialist.LIVE_OPS,
            LIVE,
            temporal_intent=MasterRouteTemporalIntent.CURRENT,
        ),
    )
    proposal = registry.proposal_for(record)
    retained = registry.decision_for(record)
    copied_decision = authorize_route(proposal, context)
    invocation = retained.plan.steps[0].invocation

    assert copied_decision == retained
    assert copied_decision is not retained
    assert copied_decision.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert copied_decision.plan is not retained.plan
    assert copied_decision.plan.steps[0].invocation == invocation
    assert copied_decision.plan.steps[0].invocation is not invocation

    for claim in (record.session_id, invocation.invocation_id, REQUEST_ID):
        rejection = reject_authentication_claim(claim)
        assert rejection.kind is AuthenticationClaimKind.MATCHING_IDENTIFIER
        assert rejection.authenticated is False
        assert rejection.establishes_principal is False
        assert rejection.establishes_execution_session is False
        _closed(rejection)
    object_claim = reject_authentication_claim(decision)
    assert object_claim.kind is AuthenticationClaimKind.OBJECT_IDENTITY
    assert object_claim.authenticated is False
    assert object_claim.establishes_principal is False
    _closed(object_claim)

    copied_session = MasterPlanningSessionRecord(record.session_id, record._membership)
    assert _errors(
        lambda: MasterExecutionFanout(registry, copied_session, deadline_utc=DEADLINE)
    ) == ["copied_session_identifier"]
    fanout = MasterExecutionFanout(registry, record, deadline_utc=DEADLINE)
    assert fanout.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert _errors(
        lambda: fanout.claim(replace(invocation), "claimer-1", now_utc=NOW)
    ) == ["reconstructed_invocation_rejected"]
    assert _errors(
        lambda: assess_master_gateway_identity(
            _gateway_event(),
            _config(),
            evaluation_epoch=EVALUATION_EPOCH,
            claimed_principal=record,
        )
    ) == ["planning_session_is_not_principal"]


def test_structural_readiness_and_fake_policy_stay_denied():
    context, registry, record, _decision, plan, bundle = _live_plan()
    step = plan.steps[0]
    assert step.ready_to_execute is True
    assert step.gates == (MasterRouteGate.NOT_REQUIRED,)
    descriptor = _descriptor(bundle, MasterSpecialist.LIVE_OPS)
    scope = bind_master_execution_scope(context, plan, step, descriptor)
    assert scope.structural_ready_to_execute is True
    assert scope.execution_permission_granted is False
    assert MasterExecutionDenialCategory.STRUCTURAL_READINESS_NOT_PERMISSION in scope.denial_categories
    _closed(scope)

    policy = assess_master_execution_policy(scope)
    assert policy.execution_status.value == "DENIED"
    assert policy.execution_permitted is False
    assert policy.caller_candidate_is_authority is False
    assert Reason.STRUCTURAL_READINESS_NOT_PERMISSION in policy.denial_reasons
    _closed(policy)
    assert _errors(
        lambda: assess_master_execution_policy(scope, execution_permitted=True)
    ) == ["execution_not_granted"]

    candidate = MasterExecutionPolicyCandidate(
        principal_label=SUBJECT,
        policy_decision="ALLOW",
        execution_permitted=True,
        dispatch_permitted=True,
        resource_names=("aircraft",),
        unrestricted_table_access=True,
        region_label="California",
        region_authoritative=True,
        master_request_id=context.master_request_id,
        invocation_id=step.invocation.invocation_id,
        specialist_request_id=step.invocation.specialist_request_id,
        correlation_id=context.correlation_id,
    )
    spoofed = assess_master_execution_policy(scope, candidate)
    assert spoofed.caller_candidate_is_authority is False
    assert spoofed.execution_permitted is False
    assert Reason.POLICY_SPOOF_NOT_AUTHORITY in spoofed.denial_reasons
    assert Reason.CALLER_POLICY_DECISION_NOT_AUTHORITY in spoofed.denial_reasons
    assert Reason.UNRESTRICTED_TABLE_ACCESS in spoofed.denial_reasons
    assert Reason.UNTRUSTED_REGION_LABEL in spoofed.denial_reasons
    _closed(spoofed)

    preflight = evaluate_execution_preflight(bundle)
    assert preflight.report.state is MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION
    assert preflight.report.executable is False
    assert preflight.dispatch_permitted is False
    readiness = assess_master_live_ops_execution_readiness(bundle, descriptor)
    assert readiness.structural_ready_to_execute is True
    assert readiness.execution_blocked is True
    assert readiness.source_read_authorized is False
    assert readiness.specialist_invoked is False
    _closed(readiness)
    registry.associate_invocation(record, step.invocation)


def test_allowlisted_tool_name_is_not_a_read_or_scan_grant():
    resources = server_read_resources(MasterSpecialist.LIVE_OPS, "search_current_hazards")
    assert ("hazards", ReadOperation.QUERY) in resources
    assert all(operation is not ReadOperation.SCAN for _resource, operation in resources)

    allowed = check_master_read_resource(
        _read_proposal(),
        evaluation=_evaluation(),
        principal=seal_ingress_principal("ingress-principal-1"),
        population_scope=seal_population_scope(frozenset({"CA"})),
    )
    assert allowed.status is MasterReadCheckStatus.WITHIN_ALLOWLIST
    assert allowed.denial_reasons == ()
    assert allowed.dispatch_permitted is False
    assert allowed.execution_permitted is False
    assert allowed.production_permit_issued is False
    assert _errors(lambda: bool(allowed)) == ["policy_check_is_not_dispatch_permit"]
    assert _errors(lambda: promote_read_check_to_dispatch_permit(allowed)) == [
        "policy_check_is_not_dispatch_permit"
    ]

    unnamed = check_master_read_resource(
        _read_proposal(),
        evaluation=_evaluation(),
        principal=None,
        population_scope=seal_population_scope(frozenset({"CA"})),
    )
    assert unnamed.status is MasterReadCheckStatus.DENIED
    assert ReadReason.PRINCIPAL_NOT_TRUSTED in unnamed.denial_reasons
    assert unnamed.dispatch_permitted is False

    caller_allow = check_master_read_resource(
        _read_proposal(caller_policy_decision="ALLOW", caller_authorized=True),
        evaluation=_evaluation(),
        principal=seal_ingress_principal("ingress-principal-1"),
        population_scope=seal_population_scope(frozenset({"CA"})),
    )
    assert caller_allow.status is MasterReadCheckStatus.DENIED
    assert ReadReason.CALLER_POLICY_NOT_AUTHORITY in caller_allow.denial_reasons

    scanned = check_master_read_resource(
        _read_proposal(operation=ReadOperation.SCAN),
        evaluation=_evaluation(),
        principal=seal_ingress_principal("ingress-principal-1"),
        population_scope=seal_population_scope(frozenset({"CA"})),
    )
    assert scanned.status is MasterReadCheckStatus.DENIED
    assert ReadReason.SCAN_NOT_PERMITTED in scanned.denial_reasons
    assert ReadReason.RECOGNIZED_TOOL_NAME_NOT_A_GRANT in scanned.denial_reasons
    assert scanned.dispatch_permitted is False


def test_local_claim_and_process_local_lock_are_not_distributed_authority():
    context, registry, record, _decision, plan, _bundle_unused = _live_plan()
    del context
    fanout = MasterExecutionFanout(registry, record, deadline_utc=DEADLINE)
    invocation = plan.steps[0].invocation
    assert type(fanout._lock).__module__ == "_thread"
    semaphore = threading.BoundedSemaphore(1)
    assert semaphore.acquire(blocking=False) is True

    claim = fanout.claim(invocation, "claimer-1", now_utc=NOW)
    assert claim.execution_authorized is False
    assert claim.evidence_verified is False
    assert claim.authorization_decision_required is True
    assert claim.planning_ready_to_execute is True
    _closed(claim)
    fanout.complete(invocation, "claimer-1", now_utc=NOW)
    completed = fanout.observations(NOW)[0]
    assert completed.state is MasterSchedulingState.COMPLETED
    assert completed.execution_authorized is False
    assert completed.evidence_verified is False
    assert completed.planning_ready_to_execute is True
    _closed(completed)
    semaphore.release()

    registry, record, _decision = _open(
        _context(master_request_id="master-req-3b2-race"),
        _proposed(
            "live-race",
            MasterSpecialist.LIVE_OPS,
            LIVE,
            temporal_intent=MasterRouteTemporalIntent.CURRENT,
        ),
    )[:3]
    race = MasterExecutionFanout(registry, record, deadline_utc=DEADLINE)
    race_invocation = race.plan.steps[0].invocation
    winners: list[MasterExecutionClaim] = []
    failures: list[tuple[str, ...]] = []
    started = threading.Barrier(2)

    def reserve() -> None:
        started.wait()
        try:
            winners.append(race.claim(race_invocation, "claimer-race", now_utc=NOW))
        except ContractValidationError as exc:
            failures.append(exc.errors)

    threads = [threading.Thread(target=reserve), threading.Thread(target=reserve)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(winners) == 1
    assert failures == [("duplicate_claim",)]
    assert winners[0].execution_authorized is False
    assert race.evidence_verified is False

    capability_registry = LocalCapabilityRegistry(
        LocalTestClock(datetime(2026, 1, 15, 0, 0, 1, tzinfo=timezone.utc))
    )
    scope = LocalCapabilityScope(
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        agent_capability=LIVE,
    )
    handle = capability_registry.issue(
        scope=scope,
        invocation_id="inv-local-3b2",
        consumer_id="consumer-local-3b2",
        session_id="session-local-3b2",
        expires_at=datetime(2026, 1, 15, 0, 5, tzinfo=timezone.utc),
    )
    consumed = capability_registry.consume(
        handle,
        scope=scope,
        invocation_id="inv-local-3b2",
        consumer_id="consumer-local-3b2",
    )
    assert consumed.outcome is LocalConsumptionOutcome.TRANSITION_RECORDED
    assert bool(consumed) is False
    assert consumed.distributed_production_authorization is False
    assert type(capability_registry._lock).__module__ == "_thread"
    _closed(consumed)


def test_local_fake_journal_partial_page_and_empty_rows_stay_unauthenticated():
    empty = _journal_execute(
        "tool-call-empty",
        LocalFakeReadResult(
            tool_result=ToolResult(
                tool_name="describe_hazard",
                tool_call_id="tool-call-empty",
                status=ToolResultStatus.SUCCESS,
                temporal_scope=TemporalScope.CURRENT,
                data={"records": [], "verified_zero": True, "aircraft_count": 0},
                evidence=(
                    Evidence(
                        source="fixture",
                        source_records=(),
                        query_timestamp_utc=T0,
                        freshness_status=FreshnessStatus.FRESH,
                        confidence=ConfidenceLevel.UNKNOWN,
                        limitations=(),
                        tool_call_id="tool-call-empty",
                        temporal_scope=TemporalScope.CURRENT,
                    ),
                ),
                as_of_utc=T0,
                limitations=(),
            ),
            executed_arguments={"airport": "KSEA"},
            rows_returned=0,
        ),
    )
    assert empty.outcome is ReadJournalOutcome.COMPLETED
    assert empty.verified_zero_minted is False
    assert empty.verified_zero_claim is None
    assert empty.aviation_evidence_admitted is False
    assert empty.trusted_runtime_observed is False
    assert bool(empty) is False
    assert ReadJournalGap.EMPTY_ROWS_NOT_VERIFIED_ZERO in empty.gaps
    assert ReadJournalGap.LOCAL_FAKE_NOT_AVIATION_EVIDENCE in empty.gaps
    _closed(empty)

    partial = _journal_execute(
        "tool-call-partial",
        LocalFakeReadResult(
            tool_result=ToolResult(
                tool_name="describe_hazard",
                tool_call_id="tool-call-partial",
                status=ToolResultStatus.PARTIAL,
                temporal_scope=TemporalScope.CURRENT,
                data={"records": [{"hazard_id": "row-1"}]},
                evidence=(
                    Evidence(
                        source="fixture",
                        source_records=(),
                        query_timestamp_utc=T0,
                        freshness_status=FreshnessStatus.UNKNOWN,
                        confidence=ConfidenceLevel.UNKNOWN,
                        limitations=("partial page",),
                        tool_call_id="tool-call-partial",
                        temporal_scope=TemporalScope.CURRENT,
                    ),
                ),
                as_of_utc=T0,
                limitations=("partial page",),
            ),
            executed_arguments={"airport": "KSEA"},
            pagination=PaginationObservation(
                page_index=0,
                page_count=2,
                rows_in_page=1,
                has_more=True,
                next_page_token_present=True,
            ),
            truncated=True,
            rows_returned=1,
            next_page_token="page-2",
        ),
    )
    assert partial.outcome is ReadJournalOutcome.COMPLETED
    assert partial.verified_zero_minted is False
    assert ReadJournalGap.PARTIAL_PAGINATION in partial.gaps
    assert ReadJournalGap.EMPTY_ROWS_NOT_VERIFIED_ZERO not in partial.gaps
    _closed(partial)

    forged = RuntimeReadJournalEntry.from_caller(
        {
            "outcome": "COMPLETED",
            "verified_zero_minted": True,
            "aviation_evidence_admitted": True,
            "source_authenticity": "ESTABLISHED",
        }
    )
    assert forged.recorded_by_journal is False
    assert forged.verified_zero_minted is False
    assert ReadJournalGap.FORGED_CALLER_EVENT in forged.gaps
    assert bool(forged) is False
    _closed(forged)


def test_user_region_and_callsign_do_not_authorize_a_target_or_a_read():
    context = _context(
        "Which aircraft are affected?",
        target_candidate_aircraft_id=None,
    )
    registry, record, _decision = _open(
        context,
        _proposed(
            "live-1",
            MasterSpecialist.LIVE_OPS,
            LIVE,
            temporal_intent=MasterRouteTemporalIntent.CURRENT,
            hints=MasterRouteHints(region_text="California", callsign=CALLSIGN),
        ),
    )
    plan = registry.plan_for(record)
    bundle = _bundle(context, plan)
    bridge = prepare_master_live_ops_native_bridge(
        prepare_master_live_ops_binding(
            bundle,
            _descriptor(bundle, MasterSpecialist.LIVE_OPS),
        )
    )
    assert bridge.withheld_untrusted_hints.region_text == "California"
    assert bridge.withheld_untrusted_hints.callsign == CALLSIGN
    assert bridge.request.selector_hints is None
    assert bridge.request.entity_hints == ()
    assert bridge.selectors_authorized is False
    assert bridge.callsign_authenticated is False
    assert bridge.geospatial_coverage_established is False
    assert bridge.regional_string_match_is_sigmet_coverage is False
    assert bridge.specialist_invoked is False
    assert bridge.missing_integration_dependency == LIVE_OPS_MISSING_HOST_EXECUTION_GRANT
    assert all(item.satisfied is False for item in bridge.host_inputs)
    _closed(bridge)
    reject_unauthorized_selectors(bridge)
    assert bridge.selectors_authorized is False
    assert _errors(lambda: reject_unauthorized_selectors(bridge, region="California")) == [
        "selection_hints_not_trusted"
    ]
    assert _errors(lambda: reject_unauthorized_selectors(bridge, callsign=CALLSIGN)) == [
        "callsign_not_authenticated_aircraft"
    ]

    labeled = check_master_read_resource(
        _read_proposal(claimed_region="California", arguments=(("region", "California"),)),
        evaluation=_evaluation(),
        principal=seal_ingress_principal("ingress-principal-1"),
        population_scope=seal_population_scope(frozenset({"CA"})),
    )
    assert labeled.status is MasterReadCheckStatus.DENIED
    assert ReadReason.REGION_LABEL_NOT_AUTHORITY in labeled.denial_reasons
    assert labeled.dispatch_permitted is False

    coded = check_master_read_resource(
        _read_proposal(),
        evaluation=_evaluation(),
        principal=seal_ingress_principal("ingress-principal-1"),
        population_scope=seal_population_scope(frozenset({"CA"})),
    )
    assert coded.status is MasterReadCheckStatus.WITHIN_ALLOWLIST
    assert coded.dispatch_permitted is False

    decision_context = _context("Which recorded risk applies?")
    _decision_registry, _decision_record, decision_route = _open(
        decision_context,
        _proposed(
            "dec-1",
            MasterSpecialist.DECISION,
            RISK,
            temporal_intent=MasterRouteTemporalIntent.CURRENT,
            hints=MasterRouteHints(callsign=CALLSIGN),
        ),
    )
    decision_bundle = _bundle(decision_context, decision_route.plan)
    readiness = assess_master_decision_execution_readiness(
        decision_bundle,
        _descriptor(decision_bundle, MasterSpecialist.DECISION),
    )
    resolution = resolve_master_decision_target(
        readiness,
        FakeDecisionReadCatalog(
            aircraft=(
                FakeAircraftRead(
                    aircraft_id=AIRCRAFT,
                    callsign=CALLSIGN,
                    state_fresh=True,
                    population_member=True,
                    in_request_scope=True,
                    risk=FakeRiskRecord(RiskPresence.PRESENT, StoredRiskLevel.HIGH),
                ),
            )
        ),
    )
    assert DecisionResolutionDefect.UNTRUSTED_ROUTE_HINT in resolution.defects
    assert resolution.observed_aircraft_id is None
    assert resolution.execution_eligible is False
    assert resolution.trusted_target_issued is False
    assert resolution.decision_request is None
    _closed(resolution)


def test_missing_or_stale_risk_records_do_not_establish_a_target():
    text = "Which recorded risk applies?"
    context = _context(text, target_candidate_aircraft_id=AIRCRAFT)
    _registry, _record, decision = _open(
        context,
        _proposed(
            "dec-1",
            MasterSpecialist.DECISION,
            RISK,
            temporal_intent=MasterRouteTemporalIntent.CURRENT,
        ),
    )
    bundle = _bundle(context, decision.plan)
    discovery = LiveOpsDiscovery(
        callsign=CALLSIGN,
        status=LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH,
        aircraft_ids=(AIRCRAFT,),
        evidence_tool_call_id="discovery-tool-1",
        reference_epoch=1_700_000_000,
        identity_resolved=True,
        selection_authorized=False,
    )
    readiness = assess_master_decision_execution_readiness(
        bundle,
        _descriptor(bundle, MasterSpecialist.DECISION),
        typed_aircraft_observations=(observe_typed_aircraft_id(AIRCRAFT),),
        discoveries=(discovery,),
        entity_hints=(
            LiveOpsEntityHint(entity_kind="AIRCRAFT", entity_id=AIRCRAFT, callsign=CALLSIGN),
        ),
        population_observations=(observe_population((AIRCRAFT,)),),
    )

    def resolve(risk: FakeRiskRecord | None, *, state_fresh: bool = True):
        return resolve_master_decision_target(
            readiness,
            FakeDecisionReadCatalog(
                aircraft=(
                    FakeAircraftRead(
                        aircraft_id=AIRCRAFT,
                        callsign=CALLSIGN,
                        state_fresh=state_fresh,
                        population_member=True,
                        in_request_scope=True,
                        risk=risk,
                    ),
                )
            ),
        )

    missing = resolve(None)
    absent = resolve(FakeRiskRecord(RiskPresence.ABSENT))
    stale = resolve(FakeRiskRecord(RiskPresence.PRESENT, StoredRiskLevel.HIGH), state_fresh=False)

    assert missing.status is DecisionResolutionStatus.REJECTED
    assert DecisionResolutionDefect.MISSING_RISK_RECORD in missing.defects
    assert missing.risk_observation is RiskRecordObservation.MISSING
    assert missing.observed_risk_level is None
    assert missing.absent_risk_is_low_risk is False
    assert missing.authenticated_risk_evidence is False
    assert missing.execution_eligible is False
    assert missing.readiness.recorded_risk_level is None
    _closed(missing)

    assert absent.risk_observation is RiskRecordObservation.MISSING
    assert absent.observed_risk_level is None
    assert absent.execution_eligible is False
    assert _errors(lambda: resolve_master_decision_target(
        readiness,
        FakeDecisionReadCatalog(
            aircraft=(
                FakeAircraftRead(
                    aircraft_id=AIRCRAFT,
                    callsign=CALLSIGN,
                    state_fresh=True,
                    population_member=True,
                    in_request_scope=True,
                    risk=None,
                ),
            )
        ),
        absent_risk_is_low_risk=True,
    )) == ["absent_risk_is_not_low_risk"]

    assert stale.status is DecisionResolutionStatus.REJECTED
    assert DecisionResolutionDefect.STALE_AIRCRAFT_STATE in stale.defects
    assert stale.observed_aircraft_id is None
    assert stale.execution_eligible is False
    assert stale.authenticated_risk_evidence is False
    _closed(stale)


def test_sourceless_historical_partial_empty_and_claimed_zero_stay_unadmitted():
    _context_unused, _decision, binding = _history_binding()
    claimed = correlate_master_historical_native_result(
        binding,
        native_run=_run(
            status=SpecialistRunStatus.PROPOSED_CLAIMS,
            proposed_claims=(VerifiedZeroClaim(tool_call_id="tool-call-zero"),),
            evaluated_as_of_utc="2026-01-15T00:03:00Z",
        ),
        native_final_result=SpecialistResult(
            status=SpecialistStatus.ANSWERED,
            answer=FACTUAL_ANSWER,
            temporal_scope=TemporalScope.HISTORICAL,
            evaluated_as_of_utc="2026-01-15T00:03:00Z",
            tool_results=(),
            verified_claims=(VerifiedZeroClaim(tool_call_id="tool-call-zero"),),
            limitations=(),
            unsupported_reason=None,
            verifier_outcome=VerifierOutcome.PASSED,
        ),
    )
    assert claimed.native_observation_origin is NativeObservationOrigin.CALLER_SUPPLIED
    assert claimed.row_observation is HistoricalRowObservation.NOT_OBSERVED
    assert claimed.verified_zero_claim_proposed is True
    assert claimed.verified_zero_admitted is False
    assert claimed.native_correlation_resolved is False
    assert claimed.eligible_tool_arguments is None
    assert claimed.bridge_copied_unbound_plan_window is False
    assert claimed.missing_provenance
    assert claimed.authenticated_source_version is None
    _closed(claimed)

    partial = correlate_master_historical_native_result(
        binding,
        native_run=_run(
            tool_results=(
                _tool_result(
                    "tool-call-partial",
                    status=ToolResultStatus.PARTIAL,
                    data={"status": "RESULT_TRUNCATED", "records": [{"id": "row-1"}]},
                    limitations=("RESULT_TRUNCATED",),
                ),
            ),
            executed_tool_call_count=1,
        ),
    )
    assert partial.query_observation is HistoricalQueryObservation.PARTIAL_OR_TRUNCATED
    assert partial.row_observation is not HistoricalRowObservation.VERIFIED_ZERO_SHAPE
    assert partial.incomplete_coverage_interpreted_as_zero is False
    assert partial.verified_zero_admitted is False
    assert partial.evidence_issued is False
    _closed(partial)

    empty = correlate_master_historical_native_result(
        binding,
        native_run=_run(
            tool_results=(
                _tool_result(
                    "tool-call-empty",
                    data={"result": {"records": [], "distinct_aircraft_count": 0}},
                ),
            ),
            executed_tool_call_count=1,
        ),
    )
    assert empty.row_observation is HistoricalRowObservation.NO_ROWS
    assert empty.verified_zero_claim_proposed is False
    assert empty.verified_zero_admitted is False
    assert empty.failure_interpreted_as_zero is False
    assert empty.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    _closed(empty)


def test_geospatial_state_string_is_not_sigmet_coverage():
    match = LiveOpsRegionalStringMatch(
        region_query="California",
        claim_kind=LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION,
        region_name="California",
        region_code="CA",
        product_type="SIGMET",
    )
    assert match.sigmet_geographic_coverage is False
    assert _errors(
        lambda: LiveOpsRegionalStringMatch(
            region_query="California",
            claim_kind=LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION,
            region_code="CA",
            product_type="SIGMET",
            sigmet_geographic_coverage=True,
        )
    ) == ["geospatial_coverage_not_established"]

    context, _registry, _record, _decision, _plan, bundle = _live_plan()
    del context
    bridge = prepare_master_live_ops_native_bridge(
        prepare_master_live_ops_binding(
            bundle,
            _descriptor(bundle, MasterSpecialist.LIVE_OPS),
        )
    )
    assert bridge.geospatial_coverage_established is False
    assert bridge.regional_string_match_is_sigmet_coverage is False
    assert bridge.census_region_resolution_is_sigmet_coverage is False
    assert bridge.aircraft_impact_count is None
    assert bridge.verified_zero is False
    assert bridge.specialist_invoked is False
    _closed(bridge)


def test_synthetic_ma3_and_ma5_factual_proposal_stay_closed():
    context, decision, _binding = _history_binding()
    authorization = decision.plan.steps[0].invocation
    synthetic = SyntheticWorkerResult(
        master_request_id=authorization.master_request_id,
        correlation_id=authorization.correlation_id,
        invocation_id=authorization.invocation_id,
        specialist_request_id=authorization.specialist_request_id,
        specialist=authorization.specialist,
        outcome=SyntheticOutcome.SYNTHETIC_COMPLETED,
        note="synthetic worker output is not an operational read",
    )
    assert synthetic.evidence_verified is False
    admission = admit_master_evidence((MasterEvidenceAdmissionInput(authorization, synthetic),))
    assert admission.evidence_verified is False
    assert admission.admissions[0].admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.UNSUPPORTED_ARTIFACT in admission.admissions[0].reason_codes
    _closed(admission)
    assert _errors(
        lambda: _bundle(
            context,
            decision.plan,
            (MasterEvidenceCorroborationInput(MasterEvidenceAdmissionInput(authorization, synthetic)),),
        )
    ) == ["synthetic_result_not_evidence"]

    factual = SpecialistResult(
        status=SpecialistStatus.UNSUPPORTED,
        answer=FACTUAL_ANSWER,
        temporal_scope=TemporalScope.HISTORICAL,
        evaluated_as_of_utc=AS_OF,
        tool_results=(),
        verified_claims=(),
        limitations=(),
        unsupported_reason=UnsupportedReason.INSUFFICIENT_EVIDENCE,
        verifier_outcome=VerifierOutcome.NOT_RUN,
    )
    handoff = evaluate_master_response_policy(
        _bundle(
            context,
            decision.plan,
            (MasterEvidenceCorroborationInput(MasterEvidenceAdmissionInput(authorization, factual)),),
        )
    )
    rendered = render_master_response(handoff)
    assert rendered.claims_admitted is False
    assert rendered.status_text != FACTUAL_ANSWER
    assert "N777WX" not in rendered.status_text
    assert "SIGMET" not in rendered.status_text
    assert "HIGH" not in rendered.status_text
    assert MasterResponseLimitation.NO_OPERATIONAL_CLAIM in handoff.limitations
    assert MasterResponseLimitation.EXECUTION_DENIED in handoff.limitations
    _closed(handoff)
