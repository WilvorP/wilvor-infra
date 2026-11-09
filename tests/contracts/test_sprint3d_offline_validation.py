"""Sprint 3D offline validation of the Sprint 3C baseline.

Local fakes exercise authentication, grants, fencing, reads, evidence, and
native gates. Every success in this module is OFFLINE_SIMULATED. It is not
a live AWS pass and not a production release.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

from tests.contracts.sprint3d_validation_contract import (
    EXPECTED_SPRINT3D_INTERFACES,
    ExecutionTier,
    classify_execution,
    locate_sprint3d_hooks,
    refuse_unverified_live_pass,
)
from tests.contracts.test_sprint3c_grant_ledger import FakeDynamoClient
from tests.fixtures import live_ops_domain_fixtures as fixtures
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
from wilvor_ai.decision_contracts import RiskPresence
from wilvor_ai.live_ops import get_aircraft_operational_context
from wilvor_ai.live_ops_claims import AircraftObservedStateClaim, LiveOpsObservedField
from wilvor_ai.live_ops_model_contracts import LiveOpsModelDecision, LiveOpsModelDecisionKind
from wilvor_ai.live_ops_public_contracts import (
    LiveOpsDiscovery,
    LiveOpsDiscoveryStatus,
    LiveOpsEntityHint,
    LiveOpsRunRequest,
    LiveOpsTrustedContext,
)
from wilvor_ai.master_application_principal import (
    IndependentlyHostAuthenticatedIdentityInput,
    PrincipalRecordKind,
    mint_nonproduction_contract_seal,
)
from wilvor_ai.master_authorized_scheduler_adapter import (
    PRODUCTION_DISPATCH_ENABLED,
    MasterAuthorizedSchedulerAdapter,
    open_nonproduction_scheduler_seal,
)
from wilvor_ai.master_authorized_scope import bind_local_test_host
from wilvor_ai.master_contracts import (
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
    observe_callsign,
    observe_population,
    observe_typed_aircraft_id,
)
from wilvor_ai.master_decision_execution_readiness import assess_master_decision_execution_readiness
from wilvor_ai.master_decision_target_resolver import (
    FakeAircraftRead,
    FakeDecisionReadCatalog,
    FakeRiskRecord,
    HostProofRequirement,
)
from wilvor_ai.master_evidence_admission import (
    LiveOpsAdmissionSources,
    MasterAdmissionReason,
    MasterAdmissionStatus,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
)
from wilvor_ai.master_evidence_corroboration import MasterEvidenceCorroborationInput
from wilvor_ai.master_execution_grants import (
    HMAC_PREVENTS_REPLAY,
    PRODUCTION_GRANT_ISSUANCE_AVAILABLE,
    GrantEvaluationClock,
    GrantRejectionReason,
    LocalHarnessGrantIssuer,
    LogicalReadOperation,
    MasterExecutionGrantEnvelope,
    bind_production_grant_to_master_ledger,
    issue_production_execution_grant,
)
from wilvor_ai.master_execution_ledger import (
    DYNAMODB_LEDGER_TABLE_NAME,
    LedgerStoreUnavailable,
    MasterExecutionLedgerOutcome,
    MasterExecutionLedgerRejection,
    MasterExecutionLedgerState,
    ledger_item_key,
)
from wilvor_ai.master_gateway_identity import MasterGatewayIdentityConfiguration
from wilvor_ai.master_gateway_runtime_boundary import (
    ISSUANCE_CONTROL_IDS,
    InvokePermissionStatement,
    ServerOwnedGatewayManifest,
    expected_execution_arn,
    expected_invoke_arn,
    expected_source_arn,
)
from wilvor_ai.master_historical_binding import prepare_master_historical_binding
from wilvor_ai.master_live_ops_binding import prepare_master_live_ops_binding
from wilvor_ai.master_parallel_contracts import MasterParallelContractBundle, MasterResponseLimitation
from wilvor_ai.master_planning_session import MasterPlanningSessionRegistry
from wilvor_ai.master_read_resource_guard import (
    HISTORICAL_LIST_MAX,
    ReadOperation,
    server_read_budget,
    server_read_resources,
)
from wilvor_ai.master_response_policy import MasterResponsePolicyState
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
)
from wilvor_ai.master_runtime_policy import (
    POLICY_REVISION,
    MasterRuntimePolicyDecision,
    PolicyAuthorityOrigin,
    TargetObligationKind,
    seal_server_policy_inputs,
)
from wilvor_ai.master_specialist_adapters import describe_planned_adapters
from wilvor_ai.master_specialist_runtime_contract import invoke_specialist
from wilvor_ai.master_trusted_read_executor import (
    ReadExecutionOutcome,
    UntrustedReadRequest,
    bind_nonproduction_cancellation,
)
from wilvor_ai.model_contracts import ProposedToolCall
from wilvor_ai.sprint3c_evidence_response import (
    Sprint3cEvidenceResponseInput,
    Sprint3cJoinDefect,
    Sprint3cRejectedClaim,
    bind_scoped_read_to_consumed_attempt,
    join_sprint3c_evidence_response,
    open_sprint3c_production_evidence_admission,
)
from wilvor_ai.sprint3c_grant_ledger import (
    Sprint3CDurableGrantLedger,
    agent_a_identity_fixture,
    bind_recorded_deny_identity,
    ledger_envelope_from_grant,
    signed_grant_nonce,
)
from wilvor_ai.sprint3c_ingress_principal import (
    mint_sprint3c_production_principal,
    open_sprint3c_ingress_principal_port,
    production_ai_query_route_is_disabled,
    refuse_policy_deny_as_execution_grant,
)
from wilvor_ai.sprint3c_scoped_read import (
    GRANT_CONSUMPTION_LIFECYCLE,
    LEDGER_EXECUTION_STATE_STARTED,
    ScopedAwsReadRefusal,
    ScopedAwsSourceError,
    Sprint3CScopedReadPort,
    bind_offline_aws_read_client,
    describe_consumed_attempt_read_binding,
)
from wilvor_ai.sprint3c_specialist_orchestration import (
    Sprint3CLiveOpsRuntimeInjection,
    Sprint3CNativeSpecialistOrchestrator,
    authority_from_durable_attempt,
    inspect_native_specialist_signatures,
    refuse_decision_native_request,
    refuse_historical_finalization,
)


LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
TEXT = "Which aircraft are currently affected by hazards?"
MASTER_REQUEST = "master-req-3d-1"
CORRELATION = "corr-3d-1"
APPLICATION = "2026-01-15T00:00:00Z"
ACCEPTED = APPLICATION
DEADLINE = "2026-01-15T00:05:00Z"
NOW = "2026-01-15T00:02:00Z"
OBSERVED = "2026-01-15T00:01:00Z"
EVALUATED = "2026-01-15T00:01:30Z"
SOURCE_OBSERVED = "2026-01-14T18:00:00Z"
SOURCE_VERSION = "sv-3d-authorized"
AIRCRAFT = "a"
DECOY = "PREBUILT-AIRCRAFT"
OTHER_AIRCRAFT = "zz9"
OWNER = "validation-owner-1"
LEASE = 60
TOOL = "get_aircraft_operational_context"
ISSUER = "https://idp.wilvor.test/realms/ops"
AUDIENCE = "wilvor-master-ai"
SUBJECT = "subject-3d-1"
FORGED_SUBJECT = "forged-subject-3d"
SCOPE = "wilvor.ai.query"
ROUTE_KEY = "POST /ai/query"
ACCOUNT = "123456789012"
API_ID = "abc123def0"
REGION = "us-west-1"
PARTITION = "aws"
NAME_PREFIX = "wilvor-dev"
FUNCTION_ARN = f"arn:{PARTITION}:lambda:{REGION}:{ACCOUNT}:function:wilvor-ai-query"
PROVENANCE = "wilvor-host-3d-nonproduction"
EVALUATION_EPOCH = 1_767_225_600
PROOF_ID = "proof-3d-1"
GATEWAY_REQUEST = "gw-req-3d-1"

GRANT_ACCEPTED = "2026-10-27T19:00:00Z"
GRANT_NOW = "2026-10-27T20:00:00Z"
GRANT_EXPIRED = "2026-10-27T20:01:00Z"
GRANT_DEADLINE = "2026-10-27T21:00:00Z"
GRANT_AFTER_DEADLINE = "2026-10-27T21:00:01Z"
READ_ACCEPTED = "2026-10-27T16:00:00Z"
READ_OBSERVED = "2026-10-27T17:30:00Z"
READ_EVALUATED = "2026-10-27T18:00:00Z"
READ_DEADLINE = "2026-10-27T20:00:00Z"
READ_NOW = "2026-10-27T18:30:00Z"
READ_SOURCE = "2026-10-27T12:00:00Z"
HIST_START = "2026-10-20T18:00:00Z"
HIST_END = "2026-10-27T18:00:00Z"


class Sequence:
    def __init__(self) -> None:
        self._n = 0

    def __call__(self) -> str:
        self._n += 1
        return f"tokenseq{self._n:04d}"


class SliceProvider:
    """Injected provider. Tool choice is the authorized aircraft, not a model API."""

    def __init__(self, aircraft_id: str) -> None:
        self.aircraft_id = aircraft_id
        self.requests = []

    def complete(self, turn):
        self.requests.append(turn)
        if not turn.tool_results:
            return LiveOpsModelDecision(
                kind=LiveOpsModelDecisionKind.TOOL_CALLS,
                tool_calls=(
                    ProposedToolCall(
                        "get_aircraft_operational_context",
                        {"aircraft_id": self.aircraft_id},
                    ),
                ),
            )
        observed = turn.tool_results[-1].data["aircraft_id"]
        if observed != self.aircraft_id:
            raise RuntimeError("specialist ignored the authorized aircraft")
        aircraft = turn.tool_results[-1].data["aircraft"]
        return LiveOpsModelDecision(
            kind=LiveOpsModelDecisionKind.FINAL_CLAIMS,
            claims=(
                AircraftObservedStateClaim(
                    tool_call_id=turn.tool_results[-1].tool_call_id,
                    tool_name=turn.tool_results[-1].tool_name,
                    aircraft_id=observed,
                    field=LiveOpsObservedField.ON_GROUND,
                    value=aircraft["on_ground"],
                    retained_not_current=False,
                ),
            ),
        )


class DenyingLedgerClient:
    """Injected client that fails before any item is stored."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.operations: list[str] = []

    def get_item(self, **_kwargs: object) -> dict[str, object]:
        self.operations.append("get_item")
        raise RuntimeError(self.code)

    def put_item(self, **_kwargs: object) -> dict[str, object]:
        self.operations.append("put_item")
        raise RuntimeError(self.code)

    def transact_write_items(self, **_kwargs: object) -> dict[str, object]:
        self.operations.append("transact_write_items")
        raise RuntimeError(self.code)


def _configuration() -> MasterGatewayIdentityConfiguration:
    return MasterGatewayIdentityConfiguration(
        issuer=ISSUER,
        audience=AUDIENCE,
        required_scopes=(SCOPE,),
        method="POST",
        raw_path="/ai/query",
        route_key=ROUTE_KEY,
    )


def _manifest() -> ServerOwnedGatewayManifest:
    source = expected_source_arn(
        expected_execution_arn(PARTITION, REGION, ACCOUNT, API_ID),
        "$default",
    )
    return ServerOwnedGatewayManifest(
        enable_ai_query_route=True,
        name_prefix=NAME_PREFIX,
        partition=PARTITION,
        region=REGION,
        account_id=ACCOUNT,
        api_id=API_ID,
        stage_name="$default",
        route_key=ROUTE_KEY,
        issuer=ISSUER,
        audiences=(AUDIENCE,),
        required_scopes=(SCOPE,),
        authorizer_issuer=ISSUER,
        authorizer_audiences=(AUDIENCE,),
        authorizer_type="JWT",
        authorization_type="JWT",
        identity_source="$request.header.Authorization",
        function_arn=FUNCTION_ARN,
        invoke_arn=expected_invoke_arn(FUNCTION_ARN),
        integration_type="AWS_PROXY",
        integration_method="POST",
        payload_format_version="2.0",
        invoke_permissions=(
            InvokePermissionStatement(
                statement_id="AllowApiGatewayInvokeAiQuery",
                action="lambda:InvokeFunction",
                principal="apigateway.amazonaws.com",
                source_arn=source,
                function_arn=FUNCTION_ARN,
                qualifier="",
            ),
        ),
        identity_policy_invoke_principals=(),
        additional_invoke_sources=(),
        function_url_enabled=False,
        ai_log_group_name="/aws/lambda/wilvor-ai-query",
    )


def _event(subject: str = SUBJECT) -> dict[str, object]:
    return {
        "version": "2.0",
        "routeKey": ROUTE_KEY,
        "rawPath": "/ai/query",
        "rawQueryString": "",
        "headers": {"authorization": "Bearer forged-header.not-a-subject"},
        "requestContext": {
            "accountId": "999999999999",
            "apiId": "zzzzzzzzzz",
            "authorizer": {
                "jwt": {
                    "claims": {
                        "iss": ISSUER,
                        "aud": AUDIENCE,
                        "sub": subject,
                        "exp": str(EVALUATION_EPOCH + 300),
                        "iat": str(EVALUATION_EPOCH - 30),
                        "nbf": str(EVALUATION_EPOCH - 10),
                        "scope": SCOPE,
                    },
                    "scopes": [SCOPE],
                }
            },
            "http": {"method": "POST", "path": "/ai/query", "protocol": "HTTP/1.1"},
            "requestId": GATEWAY_REQUEST,
            "routeKey": ROUTE_KEY,
            "stage": "prod",
            "timeEpoch": 1_700_000_000_000,
        },
        "isBase64Encoded": False,
    }


def _proposal(step: MasterProposedStep, capability: AgentCapability) -> MasterRouteProposal:
    return MasterRouteProposal(
        user_text=TEXT,
        capabilities=(MasterProposedCapability(capability, MasterRequirement.REQUIRED),),
        steps=(step,),
    )


def _live_step() -> MasterProposedStep:
    return MasterProposedStep(
        step_id="live-1",
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=LIVE,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
    )


def _history_step() -> MasterProposedStep:
    return MasterProposedStep(
        step_id="hist-1",
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=HISTORY,
        temporal_intent=MasterRouteTemporalIntent.HISTORICAL,
        historical_start_utc="2026-01-01T00:00:00Z",
        historical_end_utc="2026-01-02T00:00:00Z",
    )


def _decision_step() -> MasterProposedStep:
    return MasterProposedStep(
        step_id="dec-1",
        specialist=MasterSpecialist.DECISION,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=RISK,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
    )


def _context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": MASTER_REQUEST,
        "correlation_id": CORRELATION,
        "application_request_time_utc": APPLICATION,
        "canonical_user_text": TEXT,
    }
    values.update(overrides)
    return MasterPlanningContext(**values)  # type: ignore[arg-type]


def _open_plan(step: MasterProposedStep, capability: AgentCapability, **context_overrides: object):
    registry = MasterPlanningSessionRegistry()
    record = registry.open_session(_proposal(step, capability), _context(**context_overrides))
    decision = registry.decision_for(record)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    return registry, record, decision.plan, decision.plan.steps[0]


def _host(record) -> IndependentlyHostAuthenticatedIdentityInput:
    return IndependentlyHostAuthenticatedIdentityInput(
        issuer=ISSUER,
        audience=AUDIENCE,
        subject=SUBJECT,
        scopes=(SCOPE,),
        gateway_request_id=GATEWAY_REQUEST,
        master_request_id=MASTER_REQUEST,
        planning_session_id=record.session_id,
        correlation_id=CORRELATION,
        method="POST",
        raw_path="/ai/query",
        route_key=ROUTE_KEY,
        purpose=MasterPurpose.ANSWER,
        policy_revision=POLICY_REVISION,
        authorization_provenance=PROVENANCE,
        proof_id=PROOF_ID,
        issued_at_epoch=EVALUATION_EPOCH - 30,
        not_before_epoch=EVALUATION_EPOCH - 10,
        expires_at_epoch=EVALUATION_EPOCH + 300,
        application_request_time_utc=APPLICATION,
    )


def _server(**overrides: object):
    values: dict[str, object] = {
        "evaluation_utc": EVALUATED,
        "accepted_at_utc": ACCEPTED,
        "deadline_utc": DEADLINE,
        "executions_used": 0,
        "model_turns_used": 0,
        "tool_calls_used": 0,
        "cancelled": False,
        "region_codes": frozenset({"CA"}),
        "tool_name": TOOL,
        "resource_id": "aircraft",
        "operation": ReadOperation.GET_ITEM,
        "arguments": (("aircraft_id", AIRCRAFT),),
        "target_kind": TargetObligationKind.NOT_APPLICABLE,
        "target_id": None,
        "idempotency_key": "idem-policy-3d-1",
    }
    values.update(overrides)
    return seal_server_policy_inputs(**values)  # type: ignore[arg-type]


def _ingress(registry, record, invocation, server=None, event=None):
    port = open_sprint3c_ingress_principal_port(
        _configuration(),
        authorization_provenance=PROVENANCE,
        seal=mint_nonproduction_contract_seal(),
    )
    return port.evaluate(
        manifest=_manifest(),
        event=_event() if event is None else event,
        host_identity=_host(record),
        registry=registry,
        planning_record=record,
        invocation=invocation,
        server=_server() if server is None else server,
        evaluation_epoch=EVALUATION_EPOCH,
        lambda_context=None,
        trusted=True,
    )


def _scope(specialist: MasterSpecialist, tool_name: str) -> tuple[LogicalReadOperation, ...]:
    pairs = sorted(server_read_resources(specialist, tool_name), key=lambda item: (item[0], item[1].value))
    return tuple(LogicalReadOperation(resource_id, operation) for resource_id, operation in pairs)


def _grant_envelope(handoff, invocation, specialist, tool_name, capability, **overrides: object):
    _executions, turns, calls = server_read_budget(specialist)
    values: dict[str, object] = {
        "master_request_id": handoff.master_request_id,
        "planning_session_id": handoff.planning_session_id,
        "step_id": handoff.step_id,
        "invocation_id": handoff.invocation_id,
        "specialist": specialist,
        "purpose": handoff.purpose,
        "capability": capability,
        "principal_ref": handoff.principal_ref,
        "policy_revision": handoff.policy_revision,
        "tool_name": tool_name,
        "resource_scope": _scope(specialist, tool_name),
        "region_scope": ("CA",),
        "temporal_scopes": (TemporalScope.CURRENT,),
        "historical_window": None,
        "decision_target_scope": None,
        "accepted_at_utc": handoff.accepted_at_utc,
        "deadline_utc": handoff.deadline_utc,
        "expires_at_utc": handoff.deadline_utc,
        "idempotency_key": f"idem-{handoff.invocation_id}",
        "max_model_turns": turns,
        "max_tool_calls": calls,
        "correlation_id": invocation.correlation_id,
    }
    values.update(overrides)
    return MasterExecutionGrantEnvelope(**values)  # type: ignore[arg-type]


def _consume(host, issuer, handoff, envelope):
    identity = bind_recorded_deny_identity(handoff)
    principal = issuer.seal_principal(identity.principal_ref)
    grant = issuer.issue(issuer.prepare(envelope, principal))
    result = host.consume_signed_grant(
        grant,
        envelope=envelope,
        principal=principal,
        identity=identity,
        owner_id=OWNER,
        now_utc=NOW,
        lease_seconds=LEASE,
    )
    return identity, principal, grant, result


def _read_binding(attempt, envelope, **overrides: object):
    values: dict[str, object] = {
        "owner_id": attempt.owner_id,
        "attempt_token": attempt.attempt_token,
        "fence_epoch": attempt.fence_epoch,
        "grant_nonce": attempt.grant_nonce,
        "idempotency_key": attempt.idempotency_key,
        "payload_fingerprint": attempt.payload_fingerprint,
        "ledger_state": LEDGER_EXECUTION_STATE_STARTED,
        "grant_consumption_lifecycle": GRANT_CONSUMPTION_LIFECYCLE,
        "master_request_id": attempt.master_request_id,
        "planning_session_id": attempt.planning_session_id,
        "step_id": attempt.step_id,
        "invocation_id": attempt.invocation_id,
        "specialist": attempt.specialist,
        "purpose": attempt.purpose,
        "capability": envelope.capability,
        "principal_ref": attempt.principal_ref,
        "policy_revision": attempt.policy_revision,
        "tool_name": envelope.tool_name,
        "resource_id": "aircraft",
        "operation": ReadOperation.GET_ITEM,
        "region_scope": envelope.region_scope,
        "accepted_at_utc": attempt.accepted_at_utc,
        "deadline_utc": attempt.deadline_utc,
        "correlation_id": attempt.correlation_id,
    }
    values.update(overrides)
    return describe_consumed_attempt_read_binding(**values)


def _request(binding, **overrides: object) -> UntrustedReadRequest:
    values: dict[str, object] = {
        "master_request_id": binding.master_request_id,
        "planning_session_id": binding.planning_session_id,
        "step_id": binding.step_id,
        "invocation_id": binding.invocation_id,
        "specialist": binding.specialist,
        "purpose": binding.purpose,
        "capability": binding.capability,
        "tool_name": binding.tool_name,
        "tool_call_id": "tool-3d-1",
        "resource_id": binding.resource_id,
        "operation": binding.operation,
        "requested_arguments": (("aircraft_id", AIRCRAFT),),
        "correlation_id": binding.correlation_id,
    }
    values.update(overrides)
    return UntrustedReadRequest(**values)  # type: ignore[arg-type]


def _offline(client_type_name: str, *, live_aws_connection: bool = False, verified: bool = False):
    classification = classify_execution(
        client_type_name=client_type_name,
        live_aws_connection=live_aws_connection,
        verified_account_provenance=verified,
        production_flags_open=False,
        probe_executed=False,
    )
    assert classification.tier is ExecutionTier.OFFLINE_SIMULATED
    assert classification.live_pass is False
    assert classification.release_ready is False
    with pytest.raises(ContractValidationError, match="simulated_execution_is_not_a_live_aws_pass"):
        refuse_unverified_live_pass(classification)
    return classification


def _grant_clock() -> GrantEvaluationClock:
    return GrantEvaluationClock(datetime(2026, 10, 27, 20, 0, tzinfo=timezone.utc))


def _grant_envelope_isolated(**overrides: object) -> MasterExecutionGrantEnvelope:
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
        "resource_scope": (LogicalReadOperation("aircraft", ReadOperation.GET_ITEM),),
        "region_scope": ("CA",),
        "temporal_scopes": (TemporalScope.CURRENT,),
        "historical_window": None,
        "decision_target_scope": None,
        "accepted_at_utc": GRANT_ACCEPTED,
        "deadline_utc": GRANT_DEADLINE,
        "expires_at_utc": GRANT_DEADLINE,
        "idempotency_key": "idem-1",
        "max_model_turns": 1,
        "max_tool_calls": 1,
        "correlation_id": "correlation-1",
    }
    values.update(overrides)
    return MasterExecutionGrantEnvelope(**values)  # type: ignore[arg-type]


def _grant_harness(**envelope_overrides: object):
    client = FakeDynamoClient()
    issuer = LocalHarnessGrantIssuer(_grant_clock())
    host = Sprint3CDurableGrantLedger(issuer=issuer, client=client, token_source=Sequence())
    envelope = _grant_envelope_isolated(**envelope_overrides)
    principal = issuer.seal_principal(envelope.principal_ref)
    grant = issuer.issue(issuer.prepare(envelope, principal))
    return host, issuer, client, envelope, principal, grant


def _grant_consume(host, grant, envelope, principal, identity=None, **overrides: object):
    values: dict[str, object] = {
        "envelope": envelope,
        "principal": principal,
        "identity": identity if identity is not None else agent_a_identity_fixture(),
        "owner_id": "worker-a",
        "now_utc": GRANT_NOW,
        "lease_seconds": LEASE,
    }
    values.update(overrides)
    return host.consume_signed_grant(grant, **values)  # type: ignore[arg-type]


def _read_binding_isolated(**overrides: object):
    payload: dict[str, object] = {
        "owner_id": "owner-1",
        "attempt_token": "attempt-1",
        "fence_epoch": 1,
        "grant_nonce": "grant-nonce-1",
        "idempotency_key": "idem-live",
        "payload_fingerprint": "fingerprint-1",
        "ledger_state": LEDGER_EXECUTION_STATE_STARTED,
        "grant_consumption_lifecycle": GRANT_CONSUMPTION_LIFECYCLE,
        "master_request_id": "req-live",
        "planning_session_id": "plan-live",
        "step_id": "step-live",
        "invocation_id": "inv-live",
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "capability": LIVE,
        "principal_ref": "principal-1",
        "policy_revision": "policy-1",
        "tool_name": "get_aircraft_operational_context",
        "resource_id": "aircraft",
        "operation": ReadOperation.GET_ITEM,
        "region_scope": ("CA",),
        "accepted_at_utc": READ_ACCEPTED,
        "deadline_utc": READ_DEADLINE,
        "correlation_id": "corr-live",
        "decision_aircraft_id": None,
    }
    payload.update(overrides)
    return describe_consumed_attempt_read_binding(**payload)


def _read_host(**overrides: object):
    payload: dict[str, object] = {
        "principal_ref": "principal-1",
        "policy_revision": "policy-1",
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "capability": LIVE,
        "population_entity_ids": frozenset({AIRCRAFT}),
        "region_state_codes": frozenset({"CA"}),
        "observation_utc": READ_OBSERVED,
        "evaluation_utc": READ_EVALUATED,
    }
    payload.update(overrides)
    return bind_local_test_host(**payload)  # type: ignore[arg-type]


def _orchestrator(registry, record, issuer, durable):
    return Sprint3CNativeSpecialistOrchestrator(
        MasterAuthorizedSchedulerAdapter(
            registry,
            record,
            deadline_utc=DEADLINE,
            integration_seal=open_nonproduction_scheduler_seal(),
        ),
        issuer=issuer,
        execution_ledger=durable.shared_execution_ledger(),
    )


def test_offline_chain_preserves_one_invocation_and_stays_simulated():
    registry, record, plan, step = _open_plan(_live_step(), LIVE)
    ingress = _ingress(registry, record, step.invocation)
    handoff = ingress.agent_b_handoff()
    assert ingress.production_principal_issued is False
    assert ingress.verified_upstream_authentication is False
    assert ingress.caller_event_fields_accepted_as_attestation is False
    assert ingress.grant_eligible is False
    assert handoff.principal.record_kind is PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION
    assert handoff.policy_decision is MasterRuntimePolicyDecision.DENY
    assert handoff.authority_origin is PolicyAuthorityOrigin.NOT_ESTABLISHED
    assert FORGED_SUBJECT not in handoff.principal_ref
    assert ingress.unsatisfied_issuance_control_ids == ISSUANCE_CONTROL_IDS
    with pytest.raises(ContractValidationError, match="policy_evaluation_is_not_a_permit"):
        refuse_policy_deny_as_execution_grant(handoff.policy_evaluation)
    with pytest.raises(ContractValidationError):
        mint_sprint3c_production_principal()

    issuer = LocalHarnessGrantIssuer(GrantEvaluationClock(datetime(2026, 1, 15, 0, 2, tzinfo=timezone.utc)))
    ledger_client = FakeDynamoClient()
    durable = Sprint3CDurableGrantLedger(issuer=issuer, client=ledger_client, token_source=Sequence())
    envelope = _grant_envelope(handoff, step.invocation, MasterSpecialist.LIVE_OPS, TOOL, step.capability)
    _identity, principal, grant, consumed = _consume(durable, issuer, handoff, envelope)
    attempt = consumed.attempt
    assert consumed.outcome is MasterExecutionLedgerOutcome.CLAIMED
    assert attempt is not None
    assert attempt.grant_nonce == signed_grant_nonce(grant)
    assert attempt.specialist_executed is False
    assert attempt.distributed_exactly_once is False
    assert attempt.verified_aws_provenance is False
    assert durable.verified_aws_account_and_table_provenance is False
    replay = durable.consume_signed_grant(
        grant,
        envelope=envelope,
        principal=principal,
        identity=bind_recorded_deny_identity(handoff),
        owner_id=OWNER,
        now_utc=NOW,
        lease_seconds=LEASE,
    )
    assert replay.outcome is MasterExecutionLedgerOutcome.REPLAYED
    assert replay.specialist_executed is False
    assert replay.attempt is not None
    assert replay.attempt.attempt_token == attempt.attempt_token

    read_client = bind_offline_aws_read_client(
        get_items={
            ("aircraft", AIRCRAFT): {
                "aircraft_id": AIRCRAFT,
                "source_version": SOURCE_VERSION,
                "observed_at_utc": SOURCE_OBSERVED,
                "on_ground": False,
            }
        }
    )
    binding = _read_binding(attempt, envelope)
    read = Sprint3CScopedReadPort(read_client, now_utc=NOW).execute(
        binding,
        _request(binding),
        bind_local_test_host(
            principal_ref=handoff.principal_ref,
            policy_revision=POLICY_REVISION,
            specialist=MasterSpecialist.LIVE_OPS,
            purpose=MasterPurpose.ANSWER,
            capability=LIVE,
            population_entity_ids=frozenset({AIRCRAFT}),
            region_state_codes=frozenset({"CA"}),
            observation_utc=OBSERVED,
            evaluation_utc=EVALUATED,
        ),
        tool_call_id="tool-3d-1",
        action_id="read-3d-1",
        location_hint="California",
        census_region_name="California",
        sigmet_region_string="Oakland FIR",
    )
    assert read.live_aws_initiated is False
    assert read.approved_source_adapter is False
    assert read.sigmet_polygon_intersection_established is False
    assert read.verified_zero is False
    assert read_client.live_aws_connection is False
    receipt = read.receipt
    assert receipt is not None
    assert receipt.record_ids == (AIRCRAFT,)
    assert receipt.source_version == SOURCE_VERSION
    assert receipt.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert DECOY not in receipt.record_ids
    assert receipt.source_reported_observed_at_utc != ACCEPTED
    assert receipt.source_reported_observed_at_utc != EVALUATED

    bundle = MasterParallelContractBundle(
        context=registry.context_for(record),
        plan=plan,
        evidence_inputs=(),
    )
    descriptor = next(item for item in describe_planned_adapters(bundle).descriptors if item.step_id == "live-1")
    live_binding = prepare_master_live_ops_binding(bundle, descriptor)
    assert type(live_binding.request) is LiveOpsRunRequest
    provider = SliceProvider(AIRCRAFT)
    log: list[str] = []

    def handler(call, **kwargs):
        if kwargs.get("aircraft_id") != AIRCRAFT:
            raise RuntimeError("handler refused an aircraft the authorized read did not return")
        log.append(str(kwargs["aircraft_id"]))
        return get_aircraft_operational_context(call, **kwargs)

    executed = _orchestrator(registry, record, issuer, durable).execute_live_ops(
        step.invocation,
        live_binding,
        authority_from_durable_attempt(
            grant,
            attempt,
            principal=principal,
            grant_envelope=envelope,
            ledger_envelope=ledger_envelope_from_grant(envelope, principal_ref=principal.reference),
        ),
        Sprint3CLiveOpsRuntimeInjection(
            provider=provider,
            tables=fixtures.california_tables(),
            now_epoch=fixtures.NOW,
            handlers={"get_aircraft_operational_context": handler},
            tool_call_id_factory=lambda: "tool-live-3d",
        ),
        now_utc=NOW,
    )
    assert executed.production_dispatch is False
    assert executed.native_execution_is_source_evidence is False
    assert executed.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert executed.identity.invocation_id == attempt.invocation_id
    assert log == [AIRCRAFT]
    assert PRODUCTION_DISPATCH_ENABLED is False

    evidence_attempt = bind_scoped_read_to_consumed_attempt(
        attempt,
        receipt,
        tool_call_id="tool-3d-1",
        tool_name=TOOL,
        resource_id="aircraft",
        operation=ReadOperation.GET_ITEM,
        specialist_request_id=step.invocation.specialist_request_id,
        correlation_id=CORRELATION,
        region_scope=("CA",),
    )
    trusted = LiveOpsTrustedContext(
        tables=fixtures.california_tables(),
        now_epoch=fixtures.NOW,
        correlation_id=CORRELATION,
        max_model_turns=executed.identity.max_model_turns,
        max_tool_calls=executed.identity.max_tool_calls,
    )
    sources = LiveOpsAdmissionSources(
        request=executed.bridge.request,
        context=trusted,
        result=executed.native_result,
    )
    workflow = MasterParallelContractBundle(
        context=registry.context_for(record),
        plan=plan,
        evidence_inputs=(
            MasterEvidenceCorroborationInput(MasterEvidenceAdmissionInput(step.invocation, sources)),
        ),
    )
    california = ToolResult(
        tool_name="search_current_hazards",
        tool_call_id="tool-region-3d",
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.CURRENT,
        data={"region": "California", "polygon_intersection": True, "impacted_aircraft_count": 4},
        evidence=(
            Evidence(
                source="hazards",
                source_records=(
                    SourceRecord(
                        record_id="SIGMET-CA",
                        source_version=SOURCE_VERSION,
                        event_timestamp_utc=SOURCE_OBSERVED,
                    ),
                ),
                query_timestamp_utc=SOURCE_OBSERVED,
                freshness_status=FreshnessStatus.UNKNOWN,
                confidence=ConfidenceLevel.UNKNOWN,
                limitations=("freshness_not_established",),
                tool_call_id="tool-region-3d",
                temporal_scope=TemporalScope.CURRENT,
            ),
        ),
        as_of_utc=ACCEPTED,
        limitations=(),
        correlation_id=CORRELATION,
    )
    joined = join_sprint3c_evidence_response(
        Sprint3cEvidenceResponseInput(
            authorization=step.invocation,
            consumed_attempt=evidence_attempt,
            read_receipt=receipt,
            native_live_ops=executed,
            live_ops_context=trusted,
            native_admission_artifact=sources,
            workflow=workflow,
            untrusted_claim=california,
            expected_aircraft_id=AIRCRAFT,
            expected_source_version=SOURCE_VERSION,
        )
    )
    assert joined.claims_admitted is False
    assert joined.polygon_intersection_established is False
    assert joined.impacted_aircraft_count_established is False
    assert joined.low_risk_admitted is False
    assert joined.verified_zero_established is False
    assert Sprint3cJoinDefect.REGIONAL_TEXT_NOT_POLYGON in joined.defects
    assert Sprint3cRejectedClaim.SIGMET_POLYGON_INTERSECTION in joined.rejected_claims
    assert Sprint3cRejectedClaim.IMPACTED_AIRCRAFT_COUNT in joined.rejected_claims
    assert Sprint3cRejectedClaim.SAFETY_OR_DIVERSION_INSTRUCTION in joined.rejected_claims
    assert joined.provenance.source_version == SOURCE_VERSION
    assert joined.provenance.source_version_is_integrity_proof is False
    assert joined.provenance.query_id_authenticated is False
    assert joined.admission is not None
    assert joined.admission.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert MasterAdmissionReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED in joined.admission.reason_codes
    assert joined.response_render is not None
    assert joined.response_render.claims_admitted is False
    assert joined.response_handoff is not None
    assert joined.response_handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert MasterResponseLimitation.SOURCE_AUTHENTICITY_NOT_ESTABLISHED in joined.response_handoff.limitations
    rendered = joined.response_render.status_text.casefold()
    for fragment in ("divert", "recommend", "low risk", "verified zero", "safety instruction", "impacted aircraft"):
        assert fragment not in rendered
    assert any(character.isdigit() for character in joined.response_render.status_text) is False
    identities = (
        ingress.master_request_id,
        ingress.planning_session_id,
        ingress.step_id,
        ingress.invocation_id,
        step.invocation.specialist_request_id,
        step.invocation.correlation_id,
    )
    assert len(set(identities)) == 6
    assert identities[3] == attempt.invocation_id == receipt.invocation_id == executed.identity.invocation_id
    _offline(type(read_client).__name__, live_aws_connection=read_client.live_aws_connection)
    _offline(type(ledger_client).__name__)
    with pytest.raises(ContractValidationError, match="production_ma4_admission_not_available"):
        open_sprint3c_production_evidence_admission()


def test_forged_event_and_untrusted_materials_do_not_authenticate():
    registry, record, _plan, step = _open_plan(_live_step(), LIVE)
    with pytest.raises(ContractValidationError, match="subject_inconsistent"):
        _ingress(registry, record, step.invocation, event=_event(FORGED_SUBJECT))
    ingress = _ingress(registry, record, step.invocation)
    assert ingress.principal.subject == SUBJECT
    assert FORGED_SUBJECT not in ingress.principal_ref
    assert ingress.verified_upstream_authentication is False
    assert ingress.caller_event_fields_accepted_as_attestation is False
    assert ingress.aws_deployment_evidence.authenticated is False
    assert bool(ingress.aws_deployment_evidence) is False
    assert ingress.iam_restriction.same_account_identity_policy_reviewed is False
    assert production_ai_query_route_is_disabled() is True
    assert HMAC_PREVENTS_REPLAY is False
    assert PRODUCTION_GRANT_ISSUANCE_AVAILABLE is False
    with pytest.raises(ContractValidationError, match="production_grant_issuance_closed"):
        issue_production_execution_grant()
    with pytest.raises(ContractValidationError, match="ledger_not_integrated"):
        bind_production_grant_to_master_ledger()
    with pytest.raises(ContractValidationError, match="specialist_invocation_not_granted"):
        invoke_specialist()


def test_grant_signing_replay_expiry_and_cross_principal_isolation():
    host, issuer, client, envelope, principal, grant = _grant_harness()
    first = _grant_consume(host, grant, envelope, principal)
    assert first.outcome is MasterExecutionLedgerOutcome.CLAIMED
    assert first.attempt is not None
    nonce = first.attempt.grant_nonce
    assert nonce == signed_grant_nonce(grant)
    replay = _grant_consume(host, grant, envelope, principal)
    assert replay.outcome is MasterExecutionLedgerOutcome.REPLAYED
    assert replay.attempt is not None
    assert replay.attempt.grant_nonce == nonce
    assert replay.fresh_transition is False

    other = issuer.seal_principal("principal-other")
    crossed = _grant_consume(host, grant, envelope, other)
    assert crossed.outcome is MasterExecutionLedgerOutcome.REJECTED
    assert crossed.grant_reason is GrantRejectionReason.PRINCIPAL_MISMATCH
    assert crossed.attempt is None

    other_session = _grant_envelope_isolated(
        planning_session_id="session-other",
        invocation_id="invocation-other",
        idempotency_key="idem-other",
    )
    session_principal = issuer.seal_principal(other_session.principal_ref)
    session_grant = issuer.issue(issuer.prepare(other_session, session_principal))
    session_result = _grant_consume(
        host,
        session_grant,
        other_session,
        session_principal,
        identity=agent_a_identity_fixture(planning_session_id="session-1", invocation_id="invocation-other"),
    )
    assert session_result.outcome is MasterExecutionLedgerOutcome.REJECTED
    assert session_result.grant_reason is GrantRejectionReason.SESSION_MISMATCH

    late_host, late_issuer, late_client, late_envelope, late_principal, late_grant = _grant_harness(
        invocation_id="invocation-late",
        idempotency_key="idem-late",
    )
    late = _grant_consume(
        late_host,
        late_grant,
        late_envelope,
        late_principal,
        identity=agent_a_identity_fixture(invocation_id="invocation-late"),
        now_utc=GRANT_AFTER_DEADLINE,
    )
    assert late.outcome is MasterExecutionLedgerOutcome.REJECTED
    assert late.ledger_rejection is MasterExecutionLedgerRejection.DEADLINE_EXPIRED
    assert late_client.items == {}

    expired = _grant_consume(host, grant, envelope, principal, now_utc=GRANT_EXPIRED, owner_id="worker-b")
    assert expired.outcome is MasterExecutionLedgerOutcome.REJECTED
    assert expired.ledger_rejection is MasterExecutionLedgerRejection.NON_IDEMPOTENT_REPEAT_PROHIBITED
    stored = client.items[ledger_item_key(ledger_envelope_from_grant(envelope, principal_ref=envelope.principal_ref))]
    assert stored["grant_nonce"]["S"] == nonce
    stale = host.fenced_attempt_matches(
        envelope,
        owner_id=first.attempt.owner_id,
        attempt_token=first.attempt.attempt_token,
        fence_epoch=first.attempt.fence_epoch + 1,
        grant_nonce=nonce,
    )
    assert stale.matches_current_attempt is False
    _offline(type(client).__name__)


def test_competing_writers_fencing_and_crash_boundary():
    host, _issuer, client, envelope, principal, grant = _grant_harness(
        invocation_id="invocation-race",
        idempotency_key="idem-race",
    )
    identity = agent_a_identity_fixture(invocation_id="invocation-race")
    barrier = threading.Barrier(4)
    results = []
    errors: list[BaseException] = []
    guard = threading.Lock()

    def worker(index: int) -> None:
        try:
            barrier.wait()
            result = _grant_consume(host, grant, envelope, principal, identity=identity, owner_id=f"worker-{index}")
            with guard:
                results.append(result)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    claimed = [item for item in results if item.outcome is MasterExecutionLedgerOutcome.CLAIMED]
    assert len(claimed) == 1
    assert claimed[0].attempt is not None
    assert claimed[0].attempt.state is MasterExecutionLedgerState.STARTED
    assert claimed[0].attempt.specialist_executed is False
    assert all(item.distributed_exactly_once is False for item in results)
    assert "scan" not in client.operations
    assert "delete_item" not in client.operations

    crashed = claimed[0].attempt
    completed = host.complete_attempt(
        envelope,
        owner_id=crashed.owner_id,
        attempt_token=crashed.attempt_token,
        fence_epoch=crashed.fence_epoch,
        now_utc=GRANT_NOW,
    )
    assert completed.state is MasterExecutionLedgerState.COMPLETED
    assert completed.specialist_executed is False
    fenced_out = host.complete_attempt(
        envelope,
        owner_id=crashed.owner_id,
        attempt_token=crashed.attempt_token,
        fence_epoch=crashed.fence_epoch + 1,
        now_utc=GRANT_NOW,
    )
    assert fenced_out.outcome is MasterExecutionLedgerOutcome.REJECTED
    assert fenced_out.rejection is MasterExecutionLedgerRejection.FENCED_OUT

    ttl_host, _ttl_issuer, ttl_client, ttl_envelope, ttl_principal, ttl_grant = _grant_harness(
        invocation_id="invocation-ttl",
        idempotency_key="idem-ttl",
    )
    ttl_first = _grant_consume(
        ttl_host,
        ttl_grant,
        ttl_envelope,
        ttl_principal,
        identity=agent_a_identity_fixture(invocation_id="invocation-ttl"),
    )
    assert ttl_first.attempt is not None
    ledger_envelope = ledger_envelope_from_grant(ttl_envelope, principal_ref=ttl_envelope.principal_ref)
    partition, sort_key = ledger_item_key(ledger_envelope)
    ttl_client.simulate_ttl_of_attempt(partition, sort_key)
    reopened = _grant_consume(
        ttl_host,
        ttl_grant,
        ttl_envelope,
        ttl_principal,
        identity=agent_a_identity_fixture(invocation_id="invocation-ttl"),
        owner_id="worker-after-ttl",
    )
    assert reopened.outcome is MasterExecutionLedgerOutcome.REJECTED
    assert reopened.ledger_rejection is MasterExecutionLedgerRejection.RETENTION_GAP
    assert reopened.attempt is None
    _offline("FakeDynamoClient")


def test_strict_table_resource_region_and_target_authorization():
    client = bind_offline_aws_read_client(
        get_items={
            ("aircraft", AIRCRAFT): {
                "aircraft_id": AIRCRAFT,
                "source_version": "sv-auth",
                "observed_at_utc": READ_SOURCE,
            },
            ("aircraft", OTHER_AIRCRAFT): {"aircraft_id": OTHER_AIRCRAFT},
        }
    )
    port = Sprint3CScopedReadPort(client, now_utc=READ_NOW)
    binding = _read_binding_isolated()
    host = _read_host()
    authorized = port.execute(
        binding,
        _request(binding, tool_call_id="tool-auth"),
        host,
        tool_call_id="tool-auth",
        action_id="read-auth",
    )
    assert authorized.receipt is not None
    assert authorized.receipt.record_ids == (AIRCRAFT,)
    assert authorized.plan is not None
    assert authorized.plan.table_name == "aircraft"
    assert authorized.plan.method == "get_item"
    assert "arn:aws" not in authorized.plan.table_name

    scan_binding = _read_binding_isolated(idempotency_key="idem-scan", invocation_id="inv-scan")
    scanned = port.execute(
        scan_binding,
        _request(scan_binding, operation=ReadOperation.SCAN, tool_call_id="tool-scan"),
        host,
        tool_call_id="tool-scan",
        action_id="read-scan",
    )
    assert scanned.adapter_invoked is False
    assert scanned.refusal is not None
    sql = port.execute(
        _read_binding_isolated(idempotency_key="idem-sql", invocation_id="inv-sql"),
        _request(
            _read_binding_isolated(idempotency_key="idem-sql", invocation_id="inv-sql"),
            requested_arguments=(("aircraft_id", AIRCRAFT), ("sql", "select * from aircraft")),
            tool_call_id="tool-sql",
        ),
        host,
        tool_call_id="tool-sql",
        action_id="read-sql",
    )
    assert sql.adapter_invoked is False
    assert sql.refusal in {ScopedAwsReadRefusal.ARBITRARY_SQL, ScopedAwsReadRefusal.GUARD_DENIED, ScopedAwsReadRefusal.SCOPE_DENIED}

    tx_binding = _read_binding_isolated(idempotency_key="idem-tx", invocation_id="inv-tx")
    foreign_region = port.execute(
        tx_binding,
        _request(
            tx_binding,
            requested_arguments=(("aircraft_id", AIRCRAFT), ("region", "TX")),
            tool_call_id="tool-tx",
        ),
        host,
        tool_call_id="tool-tx",
        action_id="read-tx",
        location_hint="Texas",
    )
    assert foreign_region.adapter_invoked is False
    assert foreign_region.sigmet_polygon_intersection_established is False
    assert foreign_region.receipt is None or foreign_region.receipt.record_ids == ()

    other_aircraft = port.execute(
        _read_binding_isolated(idempotency_key="idem-zz", invocation_id="inv-zz"),
        _request(
            _read_binding_isolated(idempotency_key="idem-zz", invocation_id="inv-zz"),
            requested_arguments=(("aircraft_id", OTHER_AIRCRAFT),),
            tool_call_id="tool-zz",
        ),
        host,
        tool_call_id="tool-zz",
        action_id="read-zz",
    )
    assert other_aircraft.adapter_invoked is False
    assert other_aircraft.receipt is None or OTHER_AIRCRAFT not in other_aircraft.receipt.record_ids
    with pytest.raises(ContractValidationError, match="scan_not_permitted"):
        describe_consumed_attempt_read_binding(
            **{
                "owner_id": "owner-1",
                "attempt_token": "attempt-scan",
                "fence_epoch": 1,
                "grant_nonce": "grant-nonce-scan",
                "idempotency_key": "idem-scan-bind",
                "payload_fingerprint": "fingerprint-scan",
                "ledger_state": LEDGER_EXECUTION_STATE_STARTED,
                "grant_consumption_lifecycle": GRANT_CONSUMPTION_LIFECYCLE,
                "master_request_id": "req-scan",
                "planning_session_id": "plan-scan",
                "step_id": "step-scan",
                "invocation_id": "inv-scan",
                "specialist": MasterSpecialist.LIVE_OPS,
                "purpose": MasterPurpose.ANSWER,
                "capability": LIVE,
                "principal_ref": "principal-1",
                "policy_revision": "policy-1",
                "tool_name": "get_aircraft_operational_context",
                "resource_id": "aircraft",
                "operation": ReadOperation.SCAN,
                "region_scope": ("CA",),
                "accepted_at_utc": READ_ACCEPTED,
                "deadline_utc": READ_DEADLINE,
                "correlation_id": "corr-scan",
            }
        )
    assert "scan" not in client.operations
    _offline("OfflineAwsReadClient", live_aws_connection=client.live_aws_connection)


def test_budget_deadline_cancellation_pagination_and_retry():
    client = bind_offline_aws_read_client(
        get_items={("aircraft", AIRCRAFT): {"aircraft_id": AIRCRAFT, "source_version": "sv-budget"}},
    )
    binding = _read_binding_isolated(idempotency_key="idem-budget", invocation_id="inv-budget")
    port = Sprint3CScopedReadPort(client, now_utc=READ_NOW)
    first = port.execute(
        binding,
        _request(binding, tool_call_id="tool-budget"),
        _read_host(),
        tool_call_id="tool-budget",
        action_id="read-budget",
    )
    assert first.caller_arguments_widened_authorization is False
    assert first.receipt is not None
    assert first.receipt.record_ids == (AIRCRAFT,)
    repeated = port.execute(
        binding,
        _request(binding, caller_max_tool_calls=99, tool_call_id="tool-budget"),
        _read_host(),
        tool_call_id="tool-budget",
        action_id="read-budget-again",
    )
    assert repeated is first
    assert repeated.caller_arguments_widened_authorization is False
    assert client.operations.count("get_item") == 1
    caller_binding = _read_binding_isolated(idempotency_key="idem-caller", invocation_id="inv-caller")
    widened = port.execute(
        caller_binding,
        _request(
            caller_binding,
            caller_authorized=True,
            caller_max_tool_calls=99,
            caller_deadline_utc="2026-10-27T23:00:00Z",
            caller_policy_decision="ALLOW",
            tool_call_id="tool-caller",
        ),
        _read_host(),
        tool_call_id="tool-caller",
        action_id="read-caller",
    )
    assert widened.caller_arguments_widened_authorization is False
    assert widened.adapter_invoked is False

    cancel = bind_nonproduction_cancellation()
    cancel.cancel()
    cancelled_binding = _read_binding_isolated(idempotency_key="idem-cancel", invocation_id="inv-cancel")
    cancelled = Sprint3CScopedReadPort(client, now_utc=READ_NOW).execute(
        cancelled_binding,
        _request(cancelled_binding, tool_call_id="tool-cancel"),
        _read_host(),
        tool_call_id="tool-cancel",
        action_id="read-cancel",
        cancellation=cancel,
    )
    assert cancelled.adapter_invoked is False
    assert cancelled.refusal in {
        ScopedAwsReadRefusal.STOPPED_BEFORE_ADAPTER,
        ScopedAwsReadRefusal.GUARD_DENIED,
    }

    late_binding = _read_binding_isolated(idempotency_key="idem-late", invocation_id="inv-late")
    late_port = Sprint3CScopedReadPort(client, now_utc=READ_NOW)
    late_port.advance_seconds(90 * 60)
    late = late_port.execute(
        late_binding,
        _request(late_binding, tool_call_id="tool-late"),
        _read_host(),
        tool_call_id="tool-late",
        action_id="read-late",
    )
    assert late.adapter_invoked is False
    assert late.verified_zero is False
    assert late.refusal in {
        ScopedAwsReadRefusal.STOPPED_BEFORE_ADAPTER,
        ScopedAwsReadRefusal.READ_NOT_COMPLETED,
    }

    historical_binding = describe_consumed_attempt_read_binding(
        owner_id="owner-hist",
        attempt_token="attempt-hist",
        fence_epoch=1,
        grant_nonce="grant-nonce-hist",
        idempotency_key="idem-hist-page",
        payload_fingerprint="fingerprint-hist",
        ledger_state=LEDGER_EXECUTION_STATE_STARTED,
        grant_consumption_lifecycle=GRANT_CONSUMPTION_LIFECYCLE,
        master_request_id="req-hist",
        planning_session_id="plan-hist",
        step_id="step-hist",
        invocation_id="inv-hist",
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        purpose=MasterPurpose.ANSWER,
        capability=HISTORY,
        principal_ref="principal-1",
        policy_revision="policy-1",
        tool_name="list_historical_encounters",
        resource_id="historical.encounters",
        operation=ReadOperation.QUERY,
        region_scope=("CA",),
        accepted_at_utc=READ_ACCEPTED,
        deadline_utc=READ_DEADLINE,
        correlation_id="corr-hist",
        historical_window=(HIST_START, HIST_END),
    )
    historical_host = bind_local_test_host(
        principal_ref="principal-1",
        policy_revision="policy-1",
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        purpose=MasterPurpose.ANSWER,
        capability=HISTORY,
        population_entity_ids=frozenset({AIRCRAFT}),
        region_state_codes=frozenset({"CA"}),
        observation_utc=READ_OBSERVED,
        evaluation_utc=READ_EVALUATED,
        historical_interval_id="hist-interval-3d",
        historical_start_utc=HIST_START,
        historical_end_utc=HIST_END,
    )
    page_client = bind_offline_aws_read_client(
        query_pages={
            "historical.encounters": [
                {
                    "Items": [
                        {
                            "record_id": "enc-1",
                            "aircraft_id": AIRCRAFT,
                            "source_version": "enc-v1",
                            "observed_at_utc": READ_SOURCE,
                        }
                    ],
                    "LastEvaluatedKey": {"record_id": {"S": "secret-page-token"}},
                    "query_id": "source-query-3d",
                }
            ]
        }
    )
    page = Sprint3CScopedReadPort(page_client, now_utc=READ_NOW).execute(
        historical_binding,
        _request(
            historical_binding,
            requested_arguments=(
                ("aircraft_id", AIRCRAFT),
                ("start_utc", HIST_START),
                ("end_utc", HIST_END),
            ),
            tool_call_id="tool-page",
        ),
        historical_host,
        tool_call_id="tool-page",
        action_id="read-page",
    )
    assert page.receipt is not None
    assert page.receipt.has_more is True
    assert page.receipt.truncated is True
    assert page.receipt.query_id == "source-query-3d"
    assert page.aws_operations == ("query",)
    assert "ExclusiveStartKey" not in page_client.requests[-1]
    assert page_client.requests[-1]["Limit"] == HISTORICAL_LIST_MAX
    assert "secret-page-token" not in repr(page.observation)
    _offline("OfflineAwsReadClient")


def test_missing_page_is_not_a_verified_zero_or_low_risk():
    empty_client = bind_offline_aws_read_client()
    binding = _read_binding_isolated(idempotency_key="idem-empty", invocation_id="inv-empty")
    empty = Sprint3CScopedReadPort(empty_client, now_utc=READ_NOW).execute(
        binding,
        _request(binding, tool_call_id="tool-empty"),
        _read_host(),
        tool_call_id="tool-empty",
        action_id="read-empty",
    )
    assert empty.receipt is not None
    assert empty.receipt.page_empty is True
    assert empty.receipt.verified_zero_minted is False
    assert empty.verified_zero is False
    assert empty.absent_record_is_low_risk is False
    assert empty.inferred_risk_level is None
    assert empty.sigmet_polygon_intersection_established is False
    assert empty.receipt.record_ids == ()
    _offline("OfflineAwsReadClient", live_aws_connection=empty_client.live_aws_connection)


def test_native_live_ops_historical_and_decision_contracts():
    catalog = inspect_native_specialist_signatures()
    assert catalog.live_ops_run_parameters == ("request", "context")
    assert catalog.historical_run_parameters == ("request", "context")
    assert catalog.decision_run_parameters == ("request", "runtime")
    with pytest.raises(ContractValidationError, match="historical_finalization_not_performed"):
        refuse_historical_finalization()
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        refuse_decision_native_request()

    registry, record, plan, step = _open_plan(_history_step(), HISTORY)
    ingress = _ingress(
        registry,
        record,
        step.invocation,
        server=_server(
            tool_name="summarize_historical_encounters",
            resource_id="historical.encounters",
            operation=ReadOperation.QUERY,
            arguments=(("window_label", "historical-window"),),
            idempotency_key="idem-policy-hist-3d",
        ),
    )
    issuer = LocalHarnessGrantIssuer(GrantEvaluationClock(datetime(2026, 1, 15, 0, 2, tzinfo=timezone.utc)))
    durable = Sprint3CDurableGrantLedger(issuer=issuer, client=FakeDynamoClient(), token_source=Sequence())
    envelope = _grant_envelope(
        ingress.agent_b_handoff(),
        step.invocation,
        MasterSpecialist.HISTORICAL_ANALYTICS,
        "summarize_historical_encounters",
        step.capability,
        temporal_scopes=(TemporalScope.HISTORICAL,),
        historical_window=("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"),
        idempotency_key=f"idem-hist-{step.invocation.invocation_id}",
    )
    _identity, principal, grant, consumed = _consume(durable, issuer, ingress.agent_b_handoff(), envelope)
    assert consumed.attempt is not None
    bundle = MasterParallelContractBundle(context=registry.context_for(record), plan=plan, evidence_inputs=())
    descriptor = next(item for item in describe_planned_adapters(bundle).descriptors if item.step_id == "hist-1")
    historical = _orchestrator(registry, record, issuer, durable).correlate_historical(
        step.invocation,
        prepare_master_historical_binding(bundle, descriptor),
        authority_from_durable_attempt(
            grant,
            consumed.attempt,
            principal=principal,
            grant_envelope=envelope,
            ledger_envelope=ledger_envelope_from_grant(envelope, principal_ref=principal.reference),
        ),
        now_utc=NOW,
    )
    assert historical.native_run_invoked is False
    assert historical.finalization_performed is False
    assert historical.evidence_issued is False
    assert historical.envelope.native_run is None
    assert historical.envelope.correlation_gaps

    decision_registry, decision_record, decision_plan, decision_step = _open_plan(
        _decision_step(),
        RISK,
        target_candidate_aircraft_id=AIRCRAFT,
    )
    decision_ingress = _ingress(
        decision_registry,
        decision_record,
        decision_step.invocation,
        server=_server(
            tool_name="get_current_risk_evidence",
            idempotency_key="idem-policy-dec-3d",
        ),
    )
    decision_issuer = LocalHarnessGrantIssuer(
        GrantEvaluationClock(datetime(2026, 1, 15, 0, 2, tzinfo=timezone.utc))
    )
    decision_durable = Sprint3CDurableGrantLedger(
        issuer=decision_issuer,
        client=FakeDynamoClient(),
        token_source=Sequence(),
    )
    decision_envelope = _grant_envelope(
        decision_ingress.agent_b_handoff(),
        decision_step.invocation,
        MasterSpecialist.DECISION,
        "get_current_risk_evidence",
        decision_step.capability,
        decision_target_scope=AIRCRAFT,
        idempotency_key=f"idem-dec-{decision_step.invocation.invocation_id}",
    )
    _decision_identity, decision_principal, decision_grant, decision_consumed = _consume(
        decision_durable,
        decision_issuer,
        decision_ingress.agent_b_handoff(),
        decision_envelope,
    )
    assert decision_consumed.attempt is not None
    decision_bundle = MasterParallelContractBundle(
        context=decision_registry.context_for(decision_record),
        plan=decision_plan,
        evidence_inputs=(),
    )
    discovery = LiveOpsDiscovery(
        callsign="UAL123",
        status=LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH,
        aircraft_ids=(AIRCRAFT,),
        evidence_tool_call_id="discovery-3d-1",
        reference_epoch=fixtures.NOW,
        identity_resolved=True,
        selection_authorized=False,
    )
    readiness = assess_master_decision_execution_readiness(
        decision_bundle,
        next(item for item in describe_planned_adapters(decision_bundle).descriptors if item.step_id == "dec-1"),
        typed_aircraft_observations=(observe_typed_aircraft_id(AIRCRAFT),),
        callsign_observations=(observe_callsign(discovery),),
        discoveries=(discovery,),
        entity_hints=(LiveOpsEntityHint(entity_kind="AIRCRAFT", entity_id=AIRCRAFT, callsign="UAL123"),),
        population_observations=(observe_population((AIRCRAFT,)),),
    )
    decision = _orchestrator(decision_registry, decision_record, decision_issuer, decision_durable).resolve_decision(
        decision_step.invocation,
        readiness,
        FakeDecisionReadCatalog(
            aircraft=(
                FakeAircraftRead(
                    aircraft_id=AIRCRAFT,
                    callsign="UAL123",
                    state_fresh=True,
                    population_member=True,
                    in_request_scope=True,
                    risk=FakeRiskRecord(RiskPresence.ABSENT),
                ),
            )
        ),
        authority_from_durable_attempt(
            decision_grant,
            decision_consumed.attempt,
            principal=decision_principal,
            grant_envelope=decision_envelope,
            ledger_envelope=ledger_envelope_from_grant(
                decision_envelope,
                principal_ref=decision_principal.reference,
            ),
        ),
        now_utc=NOW,
    )
    assert decision.native_run_invoked is False
    assert decision.decision_request is None
    assert decision.native_request_built is False
    assert decision.resolution.decision_request is None
    assert decision.resolution.absent_risk_is_low_risk is False
    assert HostProofRequirement.SOURCE_CURRENTNESS not in decision.resolution.host_proofs_satisfied
    assert HostProofRequirement.ALLOWED_TARGET_POPULATION not in decision.resolution.host_proofs_satisfied
    assert (
        HostProofRequirement.SEPARATE_PERSISTED_RECOMMENDATION_AUTHORIZATION
        not in decision.resolution.host_proofs_satisfied
    )
    _offline("FakeDynamoClient")


def test_fail_closed_on_throttling_iam_denial_missing_data_and_source_outage():
    host, _issuer, _client, envelope, principal, grant = _grant_harness(
        invocation_id="invocation-iam",
        idempotency_key="idem-iam",
    )
    for code in ("AccessDeniedException", "ProvisionedThroughputExceededException", "ResourceNotFoundException"):
        denying = DenyingLedgerClient(code)
        denied_host = Sprint3CDurableGrantLedger(
            issuer=LocalHarnessGrantIssuer(_grant_clock()),
            client=denying,
            token_source=Sequence(),
        )
        fresh_envelope = _grant_envelope_isolated(
            invocation_id=f"invocation-{code[:8]}",
            idempotency_key=f"idem-{code[:8]}",
        )
        fresh_principal = denied_host._issuer.seal_principal(fresh_envelope.principal_ref)
        fresh_grant = denied_host._issuer.issue(denied_host._issuer.prepare(fresh_envelope, fresh_principal))
        with pytest.raises(LedgerStoreUnavailable):
            _grant_consume(
                denied_host,
                fresh_grant,
                fresh_envelope,
                fresh_principal,
                identity=agent_a_identity_fixture(invocation_id=fresh_envelope.invocation_id),
            )
        assert denying.operations
        _offline(type(denying).__name__)

    outage = bind_offline_aws_read_client(errors={"get_item": ScopedAwsSourceError("source outage")})
    binding = _read_binding_isolated(idempotency_key="idem-outage", invocation_id="inv-outage")
    failed = Sprint3CScopedReadPort(outage, now_utc=READ_NOW).execute(
        binding,
        _request(binding, tool_call_id="tool-outage"),
        _read_host(),
        tool_call_id="tool-outage",
        action_id="read-outage",
    )
    assert failed.receipt is not None
    assert failed.receipt.outcome is ReadExecutionOutcome.EXCEPTION
    assert failed.receipt.page_empty is False
    assert failed.verified_zero is False
    assert failed.live_aws_initiated is False

    throttled = bind_offline_aws_read_client(
        errors={"get_item": RuntimeError("ProvisionedThroughputExceededException")}
    )
    throttle_binding = _read_binding_isolated(idempotency_key="idem-throttle", invocation_id="inv-throttle")
    throttle = Sprint3CScopedReadPort(throttled, now_utc=READ_NOW).execute(
        throttle_binding,
        _request(throttle_binding, tool_call_id="tool-throttle"),
        _read_host(),
        tool_call_id="tool-throttle",
        action_id="read-throttle",
    )
    assert throttle.receipt is not None
    assert throttle.receipt.outcome is ReadExecutionOutcome.EXCEPTION
    assert throttle.receipt.record_ids == ()
    assert throttle.sigmet_polygon_intersection_established is False

    class PretendBoto:
        def get_item(self, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("live get_item")

        def query(self, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("live query")

    pretend_binding = _read_binding_isolated(idempotency_key="idem-pretend", invocation_id="inv-pretend")
    pretend = Sprint3CScopedReadPort(PretendBoto(), now_utc=READ_NOW).execute(
        pretend_binding,
        _request(pretend_binding, tool_call_id="tool-pretend"),
        _read_host(),
        tool_call_id="tool-pretend",
        action_id="read-pretend",
    )
    assert pretend.refusal is ScopedAwsReadRefusal.LIVE_AWS_NOT_INITIATED
    assert pretend.live_aws_initiated is False
    assert pretend.adapter_invoked is False
    lied = classify_execution(
        client_type_name="PretendBoto",
        live_aws_connection=True,
        verified_account_provenance=True,
        production_flags_open=True,
        probe_executed=False,
    )
    assert lied.tier is ExecutionTier.OFFLINE_SIMULATED
    assert lied.live_pass is False
    assert lied.reason == "aws_shaped_client_without_verified_probe"
    del host, envelope, principal, grant


def test_expected_sprint3d_interfaces_are_recorded_and_unverified():
    hooks = locate_sprint3d_hooks()
    assert len(hooks) == len(EXPECTED_SPRINT3D_INTERFACES)
    assert [item.interface_id for item in hooks] == [item.interface_id for item in EXPECTED_SPRINT3D_INTERFACES]
    by_id = {hook.interface_id: hook for hook in hooks}
    for hook, expected in zip(hooks, EXPECTED_SPRINT3D_INTERFACES):
        assert hook.module == expected.module
        assert hook.entrypoint == expected.entrypoint
        assert hook.status != "VERIFIED"
        assert hook.blocks_release is True
        assert expected.baseline
        assert expected.requirement
    assert by_id["I3D-6"].status == "PRESENT_UNVERIFIED"
    assert by_id["I3D-7"].status == "PRESENT_UNVERIFIED"
    assert by_id["I3D-8"].status == "PRESENT_UNVERIFIED"
    for interface_id in ("I3D-1", "I3D-2", "I3D-3", "I3D-4", "I3D-5", "I3D-9", "I3D-10"):
        assert by_id[interface_id].status == "ABSENT"
    assert all(item.interface_id.startswith("I3D-") for item in EXPECTED_SPRINT3D_INTERFACES)
