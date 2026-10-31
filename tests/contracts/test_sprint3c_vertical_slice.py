"""Sprint 3C offline vertical slice across the five agent modules.

Local success is not production authentication, authenticated aviation
evidence, or deployment readiness. Historical Analytics and Decision
Intelligence stop at their unresolved gates.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.fixtures import live_ops_domain_fixtures as fixtures
from wilvor_ai.contracts import AgentCapability, ContractValidationError, TemporalScope, ToolResult, ToolResultStatus
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
from wilvor_ai.master_decision_target_resolver import FakeAircraftRead, FakeDecisionReadCatalog, FakeRiskRecord
from wilvor_ai.master_evidence_admission import (
    MasterAdmissionReason,
    MasterAdmissionStatus,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
)
from wilvor_ai.master_evidence_corroboration import MasterEvidenceCorroborationInput
from wilvor_ai.master_execution_grants import (
    PRODUCTION_GRANT_ISSUANCE_AVAILABLE,
    GrantEvaluationClock,
    GrantRejectionReason,
    LocalHarnessGrantIssuer,
    LogicalReadOperation,
    MasterExecutionGrantEnvelope,
    parse_execution_grant,
)
from wilvor_ai.master_execution_ledger import (
    MasterExecutionLedgerOutcome,
    MasterExecutionLedgerRejection,
    MasterExecutionLedgerState,
)
from wilvor_ai.master_gateway_identity import MasterGatewayIdentityConfiguration
from wilvor_ai.master_gateway_runtime_boundary import (
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
from wilvor_ai.master_read_resource_guard import ReadOperation, server_read_budget, server_read_resources
from wilvor_ai.master_response_policy import MasterResponsePolicyState
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRouteExecutionAuthority,
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
from wilvor_ai.master_trusted_read_executor import ReadExecutionOutcome, UntrustedReadRequest
from wilvor_ai.model_contracts import ProposedToolCall
from wilvor_ai.sprint3c_evidence_response import (
    Sprint3cEvidenceResponseInput,
    Sprint3cJoinDefect,
    Sprint3cJoinStatus,
    Sprint3cRejectedClaim,
    bind_scoped_read_to_consumed_attempt,
    join_sprint3c_evidence_response,
)
from wilvor_ai.sprint3c_grant_ledger import (
    Sprint3CDurableGrantLedger,
    bind_recorded_deny_identity,
    ledger_envelope_from_grant,
    signed_grant_nonce,
)
from wilvor_ai.sprint3c_ingress_principal import (
    open_sprint3c_ingress_principal_port,
    refuse_policy_deny_as_execution_grant,
)
from wilvor_ai.sprint3c_scoped_read import (
    GRANT_CONSUMPTION_LIFECYCLE,
    LEDGER_EXECUTION_STATE_STARTED,
    ScopedAwsReadRefusal,
    Sprint3CScopedReadPort,
    bind_offline_aws_read_client,
    describe_consumed_attempt_read_binding,
)
from wilvor_ai.sprint3c_specialist_orchestration import (
    Sprint3CLiveOpsRuntimeInjection,
    Sprint3CNativeSpecialistOrchestrator,
    authority_from_durable_attempt,
)
from wilvor_ai.contracts import Evidence, SourceRecord, ConfidenceLevel, FreshnessStatus


REPO_ROOT = Path(__file__).resolve().parents[2]
GEOJSON = REPO_ROOT / "functions" / "shared" / "wilvor_operational" / "data" / "us_states.geojson"
GEOJSON_SHA256 = "83cd47c5449be7559ef9595afc881718f11175f65542c743c7ae5d831d5b12c5"

LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
TEXT = "Which aircraft are currently affected by hazards?"
MASTER_REQUEST = "master-req-slice-1"
CORRELATION = "corr-slice-1"
APPLICATION = "2026-01-15T00:00:00Z"
ACCEPTED = APPLICATION
DEADLINE = "2026-01-15T00:05:00Z"
NOW = "2026-01-15T00:02:00Z"
OBSERVED = "2026-01-15T00:01:00Z"
EVALUATED = "2026-01-15T00:01:30Z"
SOURCE_OBSERVED = "2026-01-14T18:00:00Z"
SOURCE_VERSION = "sv-slice-authorized"
AIRCRAFT = "a"
DECOY = "PREBUILT-AIRCRAFT"
OWNER = "slice-owner-1"
LEASE = 60
TOOL = "get_aircraft_operational_context"
ISSUER = "https://idp.wilvor.test/realms/ops"
AUDIENCE = "wilvor-master-ai"
SUBJECT = "subject-slice-1"
SCOPE = "wilvor.ai.query"
ROUTE_KEY = "POST /ai/query"
ACCOUNT = "123456789012"
API_ID = "abc123def0"
REGION = "us-west-1"
PARTITION = "aws"
NAME_PREFIX = "wilvor-dev"
FUNCTION_ARN = f"arn:{PARTITION}:lambda:{REGION}:{ACCOUNT}:function:wilvor-ai-query"
PROVENANCE = "wilvor-host-slice-nonproduction"
EVALUATION_EPOCH = 1_767_225_600
PROOF_ID = "proof-slice-1"
GATEWAY_REQUEST = "gw-req-slice-1"


class SliceProvider:
    """Deterministic provider. Tool choice comes from the authorized read id."""

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
        projection = turn.tool_results[-1]
        observed = projection.data["aircraft_id"]
        if observed != self.aircraft_id:
            raise RuntimeError("specialist ignored the authorized aircraft")
        aircraft = projection.data["aircraft"]
        return LiveOpsModelDecision(
            kind=LiveOpsModelDecisionKind.FINAL_CLAIMS,
            claims=(
                AircraftObservedStateClaim(
                    tool_call_id=projection.tool_call_id,
                    tool_name=projection.tool_name,
                    aircraft_id=observed,
                    field=LiveOpsObservedField.ON_GROUND,
                    value=aircraft["on_ground"],
                    retained_not_current=False,
                ),
            ),
        )


class Sequence:
    def __init__(self) -> None:
        self._n = 0

    def __call__(self) -> str:
        self._n += 1
        return f"slicetoken{self._n:04d}"


class TransactionCanceledException(Exception):
    def __init__(self) -> None:
        super().__init__("transaction canceled")
        self.response = {"Error": {"Code": "TransactionCanceledException"}}


class FakeDynamoClient:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, dict[str, object]]] = {}

    def get_item(self, **kwargs: object) -> dict[str, object]:
        key = _item_key(kwargs["Key"])
        item = self.items.get(key)
        if item is None:
            return {}
        return {"Item": dict(item)}

    def put_item(self, **kwargs: object) -> dict[str, object]:
        item = kwargs["Item"]
        assert isinstance(item, dict)
        self.items[_item_key_from_item(item)] = dict(item)
        return {}

    def transact_write_items(self, **kwargs: object) -> dict[str, object]:
        planned: list[tuple[tuple[str, str], dict[str, dict[str, object]]]] = []
        for entry in kwargs["TransactItems"]:
            assert isinstance(entry, dict)
            assert set(entry) == {"Put"}
            put = entry["Put"]
            expression = str(put["ConditionExpression"])
            assert "attribute_not_exists" in expression
            item = put["Item"]
            assert isinstance(item, dict)
            key = _item_key_from_item(item)
            if key in self.items:
                raise TransactionCanceledException()
            planned.append((key, dict(item)))
        for key, item in planned:
            self.items[key] = item
        return {}


def _text(attribute: object) -> str:
    assert isinstance(attribute, dict)
    value = attribute["S"]
    assert isinstance(value, str)
    return value


def _item_key(key: object) -> tuple[str, str]:
    assert isinstance(key, dict)
    return _text(key["pk"]), _text(key["sk"])


def _item_key_from_item(item: dict[str, dict[str, object]]) -> tuple[str, str]:
    return _text(item["pk"]), _text(item["sk"])


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


def _event() -> dict[str, object]:
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
                        "sub": SUBJECT,
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
        "idempotency_key": "idem-policy-slice-1",
    }
    values.update(overrides)
    return seal_server_policy_inputs(**values)  # type: ignore[arg-type]


def _ingress(registry, record, invocation, server=None):
    port = open_sprint3c_ingress_principal_port(
        _configuration(),
        authorization_provenance=PROVENANCE,
        seal=mint_nonproduction_contract_seal(),
    )
    return port.evaluate(
        manifest=_manifest(),
        event=_event(),
        host_identity=_host(record),
        registry=registry,
        planning_record=record,
        invocation=invocation,
        server=_server() if server is None else server,
        evaluation_epoch=EVALUATION_EPOCH,
        lambda_context=None,
        trusted=True,
    )


def _clock() -> GrantEvaluationClock:
    return GrantEvaluationClock(datetime(2026, 1, 15, 0, 2, tzinfo=timezone.utc))


def _scope(specialist: MasterSpecialist, tool_name: str) -> tuple[LogicalReadOperation, ...]:
    pairs = sorted(server_read_resources(specialist, tool_name), key=lambda item: (item[0], item[1].value))
    return tuple(LogicalReadOperation(resource_id, operation) for resource_id, operation in pairs)


def _grant_envelope(
    handoff,
    invocation,
    specialist: MasterSpecialist,
    tool_name: str,
    capability: AgentCapability,
    **overrides: object,
):
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
        "tool_call_id": "tool-slice-1",
        "resource_id": binding.resource_id,
        "operation": binding.operation,
        "requested_arguments": (("aircraft_id", AIRCRAFT),),
        "correlation_id": binding.correlation_id,
    }
    values.update(overrides)
    return UntrustedReadRequest(**values)  # type: ignore[arg-type]


def _live_host(principal_ref: str):
    return bind_local_test_host(
        principal_ref=principal_ref,
        policy_revision=POLICY_REVISION,
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        capability=LIVE,
        population_entity_ids=frozenset({AIRCRAFT}),
        region_state_codes=frozenset({"CA"}),
        observation_utc=OBSERVED,
        evaluation_utc=EVALUATED,
    )


def _authorized_handler(aircraft_id: str, log: list[str]):
    def handler(call, **kwargs):
        if kwargs.get("aircraft_id") != aircraft_id:
            raise RuntimeError("handler refused an aircraft the authorized read did not return")
        log.append(str(kwargs["aircraft_id"]))
        return get_aircraft_operational_context(call, **kwargs)

    return {"get_aircraft_operational_context": handler}


def test_geojson_sha256_matches_the_vendored_census_artifact():
    digest = hashlib.sha256(GEOJSON.read_bytes()).hexdigest()
    assert digest == GEOJSON_SHA256


def test_live_ops_vertical_slice_preserves_one_invocation_and_uses_the_read():
    assert PRODUCTION_GRANT_ISSUANCE_AVAILABLE is False
    assert PRODUCTION_DISPATCH_ENABLED is False
    registry, record, plan, step = _open_plan(_live_step(), LIVE)
    ingress = _ingress(registry, record, step.invocation)
    handoff = ingress.agent_b_handoff()
    with pytest.raises(ContractValidationError, match="policy_evaluation_is_not_a_permit"):
        refuse_policy_deny_as_execution_grant(handoff.policy_evaluation)
    denied = Sprint3CDurableGrantLedger(
        issuer=LocalHarnessGrantIssuer(_clock()),
        client=FakeDynamoClient(),
        token_source=Sequence(),
    ).issue_from_policy_evaluation(handoff.policy_evaluation)
    assert denied.grant_reason is GrantRejectionReason.EVALUATION_NOT_A_GRANT
    assert handoff.policy_decision is MasterRuntimePolicyDecision.DENY
    assert handoff.authority_origin is PolicyAuthorityOrigin.NOT_ESTABLISHED
    assert handoff.grant_eligible is False
    assert handoff.planning_session_id == record.session_id
    assert handoff.planning_session_id != handoff.policy_evaluation.planning_session_id
    assert handoff.principal.record_kind is PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION
    assert handoff.principal.production_principal is False

    issuer = LocalHarnessGrantIssuer(_clock())
    ledger_client = FakeDynamoClient()
    durable = Sprint3CDurableGrantLedger(issuer=issuer, client=ledger_client, token_source=Sequence())
    envelope = _grant_envelope(handoff, step.invocation, MasterSpecialist.LIVE_OPS, TOOL, step.capability)
    _identity, principal, grant, consumed = _consume(durable, issuer, handoff, envelope)
    assert consumed.outcome is MasterExecutionLedgerOutcome.CLAIMED
    attempt = consumed.attempt
    assert attempt is not None
    assert attempt.state is MasterExecutionLedgerState.STARTED
    assert attempt.grant_nonce == signed_grant_nonce(grant)
    assert attempt.invocation_id == handoff.invocation_id == step.invocation.invocation_id
    assert attempt.planning_session_id == record.session_id
    assert attempt.principal_ref == handoff.principal_ref
    assert attempt.master_request_id == MASTER_REQUEST
    assert attempt.correlation_id == CORRELATION
    assert attempt.distributed_exactly_once is False
    assert attempt.execution_permitted is False
    assert attempt.specialist_executed is False
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
    assert replay.attempt is not None
    assert replay.attempt.state is MasterExecutionLedgerState.STARTED
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
    assert binding.ledger_state == LEDGER_EXECUTION_STATE_STARTED
    assert binding.grant_consumption_lifecycle == GRANT_CONSUMPTION_LIFECYCLE
    read = Sprint3CScopedReadPort(read_client, now_utc=NOW).execute(
        binding,
        _request(binding),
        _live_host(handoff.principal_ref),
        tool_call_id="tool-slice-1",
        action_id="read-slice-1",
        location_hint="California",
        census_region_name="California",
        sigmet_region_string="Oakland FIR",
    )
    assert read.refusal is None
    assert read.adapter_invoked is True
    assert read.aws_operations == ("get_item",)
    assert read.verified_zero is False
    assert read.sigmet_polygon_intersection_established is False
    receipt = read.receipt
    assert receipt is not None
    assert receipt.outcome is ReadExecutionOutcome.COMPLETED
    assert receipt.record_ids == (AIRCRAFT,)
    assert receipt.source_version == SOURCE_VERSION
    assert receipt.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert receipt.approved_source_adapter is False
    assert receipt.verified_zero_minted is False
    assert receipt.invocation_id == attempt.invocation_id
    assert DECOY not in receipt.record_ids

    bundle = MasterParallelContractBundle(
        context=registry.context_for(record),
        plan=plan,
        evidence_inputs=(),
    )
    described = describe_planned_adapters(bundle)
    descriptor = next(item for item in described.descriptors if item.step_id == "live-1")
    live_binding = prepare_master_live_ops_binding(bundle, descriptor)
    assert type(live_binding.request) is LiveOpsRunRequest
    log: list[str] = []
    provider = SliceProvider(receipt.record_ids[0])
    orchestrator = Sprint3CNativeSpecialistOrchestrator(
        MasterAuthorizedSchedulerAdapter(
            registry,
            record,
            deadline_utc=DEADLINE,
            integration_seal=open_nonproduction_scheduler_seal(),
        ),
        issuer=issuer,
        execution_ledger=durable.shared_execution_ledger(),
    )
    authority = authority_from_durable_attempt(
        grant,
        attempt,
        principal=principal,
        grant_envelope=envelope,
        ledger_envelope=ledger_envelope_from_grant(envelope, principal_ref=principal.reference),
    )
    assert authority.ledger_state is MasterExecutionLedgerState.STARTED
    executed = orchestrator.execute_live_ops(
        step.invocation,
        live_binding,
        authority,
        Sprint3CLiveOpsRuntimeInjection(
            provider=provider,
            tables=fixtures.california_tables(),
            now_epoch=fixtures.NOW,
            handlers=_authorized_handler(receipt.record_ids[0], log),
            tool_call_id_factory=lambda: "tool-live-1",
        ),
        now_utc=NOW,
    )
    assert executed.live_ops_run_invoked is True
    assert executed.production_dispatch is False
    assert executed.identity.invocation_id == attempt.invocation_id
    assert executed.identity.master_request_id == MASTER_REQUEST
    assert executed.identity.planning_session_id == record.session_id
    assert executed.identity.correlation_id == CORRELATION
    assert executed.mapped.native_result is executed.native_result
    assert log == [AIRCRAFT]
    assert provider.requests
    assert provider.requests[0].tool_results == ()
    assert provider.requests[-1].tool_results[-1].data["aircraft_id"] == AIRCRAFT
    assert DECOY not in str(executed.native_result.answer)
    claim_ids = tuple(
        claim.aircraft_id for claim in executed.native_result.verified_claims if claim.aircraft_id is not None
    )
    if claim_ids:
        assert claim_ids == (AIRCRAFT,)
        assert DECOY not in claim_ids

    evidence_attempt = bind_scoped_read_to_consumed_attempt(
        attempt,
        receipt,
        tool_call_id="tool-slice-1",
        tool_name=TOOL,
        resource_id="aircraft",
        operation=ReadOperation.GET_ITEM,
        specialist_request_id=step.invocation.specialist_request_id,
        correlation_id=CORRELATION,
        region_scope=("CA",),
    )
    assert evidence_attempt.ledger_state == LEDGER_EXECUTION_STATE_STARTED
    assert evidence_attempt.ledger_grant_nonce == attempt.grant_nonce
    trusted = LiveOpsTrustedContext(
        tables=fixtures.california_tables(),
        now_epoch=fixtures.NOW,
        correlation_id=CORRELATION,
        max_model_turns=executed.identity.max_model_turns,
        max_tool_calls=executed.identity.max_tool_calls,
    )
    sources = __import__(
        "wilvor_ai.master_evidence_admission",
        fromlist=["LiveOpsAdmissionSources"],
    ).LiveOpsAdmissionSources(
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
        tool_call_id="tool-region-slice",
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
                tool_call_id="tool-region-slice",
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
    assert joined.authorization_bound is True
    assert joined.consumed_attempt_bound is True
    assert joined.scoped_read_adapter_accepted is True
    assert joined.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert joined.claims_admitted is False
    assert joined.aviation_evidence_admitted is False
    assert joined.verified_zero_established is False
    assert joined.polygon_intersection_established is False
    assert joined.impacted_aircraft_count_established is False
    assert Sprint3cJoinDefect.GRANT_NONCE_NOT_PRODUCTION_BIND in joined.defects
    assert Sprint3cJoinDefect.REGIONAL_TEXT_NOT_POLYGON in joined.defects
    assert Sprint3cRejectedClaim.SIGMET_POLYGON_INTERSECTION in joined.rejected_claims
    assert joined.admission is not None
    assert joined.admission.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert MasterAdmissionReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED in joined.admission.reason_codes
    assert joined.admission.admission_status in {
        MasterAdmissionStatus.CONTRACT_ONLY,
        MasterAdmissionStatus.BINDING_UNRESOLVED,
    }
    assert joined.response_render is not None
    assert joined.response_render.claims_admitted is False
    assert joined.response_handoff is not None
    assert joined.response_handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert MasterResponseLimitation.SOURCE_AUTHENTICITY_NOT_ESTABLISHED in joined.response_handoff.limitations
    assert "divert" not in joined.response_render.status_text.casefold()
    identities = (
        ingress.master_request_id,
        ingress.planning_session_id,
        ingress.step_id,
        ingress.invocation_id,
        step.invocation.specialist_request_id,
        step.invocation.correlation_id,
    )
    assert identities[0] == attempt.master_request_id == receipt.master_request_id == executed.identity.master_request_id
    assert identities[1] == attempt.planning_session_id == receipt.planning_session_id
    assert identities[2] == attempt.step_id == receipt.step_id == executed.identity.step_id
    assert identities[3] == attempt.invocation_id == receipt.invocation_id == joined.admission.invocation_id
    assert len(set(identities)) == 6

    historical = _historical_gate()
    assert historical.finalization_performed is False
    assert historical.evidence_issued is False
    assert historical.native_run_invoked is False
    decision = _decision_gate()
    assert decision.decision_request is None
    assert decision.native_request_built is False
    assert decision.native_run_invoked is False


def _historical_gate():
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
            idempotency_key="idem-policy-hist-slice",
        ),
    )
    issuer = LocalHarnessGrantIssuer(_clock())
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
    assert consumed.outcome is MasterExecutionLedgerOutcome.CLAIMED
    assert consumed.attempt is not None
    assert consumed.attempt.state is MasterExecutionLedgerState.STARTED
    bundle = MasterParallelContractBundle(context=registry.context_for(record), plan=plan, evidence_inputs=())
    descriptor = next(item for item in describe_planned_adapters(bundle).descriptors if item.step_id == "hist-1")
    orchestrator = Sprint3CNativeSpecialistOrchestrator(
        MasterAuthorizedSchedulerAdapter(
            registry,
            record,
            deadline_utc=DEADLINE,
            integration_seal=open_nonproduction_scheduler_seal(),
        ),
        issuer=issuer,
        execution_ledger=durable.shared_execution_ledger(),
    )
    return orchestrator.correlate_historical(
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


def _decision_gate():
    registry, record, plan, step = _open_plan(
        _decision_step(),
        RISK,
        target_candidate_aircraft_id=AIRCRAFT,
    )
    ingress = _ingress(
        registry,
        record,
        step.invocation,
        server=_server(
            tool_name="get_current_risk_evidence",
            idempotency_key="idem-policy-dec-slice",
        ),
    )
    issuer = LocalHarnessGrantIssuer(_clock())
    durable = Sprint3CDurableGrantLedger(issuer=issuer, client=FakeDynamoClient(), token_source=Sequence())
    envelope = _grant_envelope(
        ingress.agent_b_handoff(),
        step.invocation,
        MasterSpecialist.DECISION,
        "get_current_risk_evidence",
        step.capability,
        decision_target_scope=AIRCRAFT,
        idempotency_key=f"idem-dec-{step.invocation.invocation_id}",
    )
    _identity, principal, grant, consumed = _consume(durable, issuer, ingress.agent_b_handoff(), envelope)
    assert consumed.outcome is MasterExecutionLedgerOutcome.CLAIMED
    assert consumed.attempt is not None
    bundle = MasterParallelContractBundle(context=registry.context_for(record), plan=plan, evidence_inputs=())
    discovery = LiveOpsDiscovery(
        callsign="UAL123",
        status=LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH,
        aircraft_ids=(AIRCRAFT,),
        evidence_tool_call_id="discovery-slice-1",
        reference_epoch=fixtures.NOW,
        identity_resolved=True,
        selection_authorized=False,
    )
    readiness = assess_master_decision_execution_readiness(
        bundle,
        next(item for item in describe_planned_adapters(bundle).descriptors if item.step_id == "dec-1"),
        typed_aircraft_observations=(observe_typed_aircraft_id(AIRCRAFT),),
        callsign_observations=(observe_callsign(discovery),),
        discoveries=(discovery,),
        entity_hints=(LiveOpsEntityHint(entity_kind="AIRCRAFT", entity_id=AIRCRAFT, callsign="UAL123"),),
        population_observations=(observe_population((AIRCRAFT,)),),
    )
    catalog = FakeDecisionReadCatalog(
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
    )
    orchestrator = Sprint3CNativeSpecialistOrchestrator(
        MasterAuthorizedSchedulerAdapter(
            registry,
            record,
            deadline_utc=DEADLINE,
            integration_seal=open_nonproduction_scheduler_seal(),
        ),
        issuer=issuer,
        execution_ledger=durable.shared_execution_ledger(),
    )
    return orchestrator.resolve_decision(
        step.invocation,
        readiness,
        catalog,
        authority_from_durable_attempt(
            grant,
            consumed.attempt,
            principal=principal,
            grant_envelope=envelope,
            ledger_envelope=ledger_envelope_from_grant(envelope, principal_ref=principal.reference),
        ),
        now_utc=NOW,
    )


def _live_consumed():
    registry, record, _plan, step = _open_plan(_live_step(), LIVE)
    ingress = _ingress(registry, record, step.invocation)
    issuer = LocalHarnessGrantIssuer(_clock())
    durable = Sprint3CDurableGrantLedger(issuer=issuer, client=FakeDynamoClient(), token_source=Sequence())
    envelope = _grant_envelope(ingress.agent_b_handoff(), step.invocation, MasterSpecialist.LIVE_OPS, TOOL, step.capability)
    identity, principal, grant, consumed = _consume(durable, issuer, ingress.agent_b_handoff(), envelope)
    return registry, record, step, ingress, issuer, durable, envelope, identity, principal, grant, consumed


def test_forged_principal_cross_session_and_replay_fail_closed():
    registry, record, step, ingress, issuer, durable, envelope, identity, principal, grant, consumed = _live_consumed()
    assert consumed.outcome is MasterExecutionLedgerOutcome.CLAIMED
    with pytest.raises(ContractValidationError, match="invalid_identity_binding"):
        bind_recorded_deny_identity(object())
    other = issuer.seal_principal("forged-principal-ref")
    forged = durable.consume_signed_grant(
        grant,
        envelope=envelope,
        principal=other,
        identity=bind_recorded_deny_identity(ingress.agent_b_handoff()),
        owner_id=OWNER,
        now_utc=NOW,
        lease_seconds=LEASE,
    )
    assert forged.outcome is MasterExecutionLedgerOutcome.REJECTED
    assert forged.grant_reason in {
        GrantRejectionReason.PRINCIPAL_MISMATCH,
        GrantRejectionReason.REPLAY,
    }
    crossed = MasterExecutionGrantEnvelope(
        **{
            **{field: getattr(envelope, field) for field in (
                "master_request_id",
                "step_id",
                "invocation_id",
                "specialist",
                "purpose",
                "capability",
                "principal_ref",
                "policy_revision",
                "tool_name",
                "resource_scope",
                "region_scope",
                "temporal_scopes",
                "historical_window",
                "decision_target_scope",
                "accepted_at_utc",
                "deadline_utc",
                "expires_at_utc",
                "idempotency_key",
                "max_model_turns",
                "max_tool_calls",
                "correlation_id",
            )},
            "planning_session_id": "session-forged-other",
        }
    )
    denied = durable.consume_signed_grant(
        grant,
        envelope=crossed,
        principal=principal,
        identity=identity,
        owner_id=OWNER,
        now_utc=NOW,
        lease_seconds=LEASE,
    )
    assert denied.outcome is MasterExecutionLedgerOutcome.REJECTED
    replay = durable.consume_signed_grant(
        grant,
        envelope=envelope,
        principal=principal,
        identity=identity,
        owner_id=OWNER,
        now_utc=NOW,
        lease_seconds=LEASE,
    )
    assert replay.outcome is MasterExecutionLedgerOutcome.REPLAYED
    assert replay.fresh_transition is False
    stale = durable.fenced_attempt_matches(
        ledger_envelope_from_grant(envelope, principal_ref=principal.reference),
        owner_id=OWNER,
        attempt_token=consumed.attempt.attempt_token,
        fence_epoch=consumed.attempt.fence_epoch + 1,
        grant_nonce=consumed.attempt.grant_nonce,
    )
    assert stale.matches_current_attempt is False
    assert stale.rejection is MasterExecutionLedgerRejection.FENCED_OUT
    assert stale.authorizes_execution is False
    del registry, record, step


def test_revoked_and_expired_authority_fail_closed():
    _registry, _record, step, ingress, issuer, durable, envelope, _identity, principal, grant, consumed = _live_consumed()
    assert consumed.outcome is MasterExecutionLedgerOutcome.CLAIMED
    retired = LocalHarnessGrantIssuer(_clock())
    retired_host = Sprint3CDurableGrantLedger(
        issuer=retired,
        client=FakeDynamoClient(),
        token_source=Sequence(),
    )
    retired_principal = retired.seal_principal(ingress.principal_ref)
    fresh = retired.issue(retired.prepare(envelope, retired_principal))
    signing_key = str(fresh.serialized_claim()["key_id"])
    retired.rotate_signing_key()
    retired.retire_verification_key(signing_key)
    revoked = retired_host.consume_signed_grant(
        fresh,
        envelope=envelope,
        principal=retired_principal,
        identity=bind_recorded_deny_identity(ingress.agent_b_handoff()),
        owner_id=OWNER,
        now_utc=NOW,
        lease_seconds=LEASE,
    )
    assert revoked.outcome is MasterExecutionLedgerOutcome.REJECTED
    assert revoked.grant_reason is GrantRejectionReason.KEY_RETIRED
    expiring = LocalHarnessGrantIssuer(_clock())
    expiring_host = Sprint3CDurableGrantLedger(
        issuer=expiring,
        client=FakeDynamoClient(),
        token_source=Sequence(),
    )
    expiring_principal = expiring.seal_principal(ingress.principal_ref)
    issued = expiring.issue(expiring.prepare(envelope, expiring_principal))
    expiring._clock.advance_to(datetime(2026, 1, 15, 0, 6, tzinfo=timezone.utc))
    expired = expiring_host.consume_signed_grant(
        issued,
        envelope=envelope,
        principal=expiring_principal,
        identity=bind_recorded_deny_identity(ingress.agent_b_handoff()),
        owner_id=OWNER,
        now_utc="2026-01-15T00:06:00Z",
        lease_seconds=LEASE,
    )
    assert expired.outcome is MasterExecutionLedgerOutcome.REJECTED
    assert expired.grant_reason is GrantRejectionReason.EXPIRED
    del step


def _truncated_historical_page():
    _registry, _record, _plan, step = _open_plan(_history_step(), HISTORY)
    ingress = _ingress(
        _registry,
        _record,
        step.invocation,
        server=_server(
            tool_name="list_historical_encounters",
            resource_id="historical.encounters",
            operation=ReadOperation.QUERY,
            arguments=(("aircraft_id", AIRCRAFT),),
            idempotency_key="idem-policy-incomplete",
        ),
    )
    issuer = LocalHarnessGrantIssuer(_clock())
    durable = Sprint3CDurableGrantLedger(issuer=issuer, client=FakeDynamoClient(), token_source=Sequence())
    window = ("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
    envelope = _grant_envelope(
        ingress.agent_b_handoff(),
        step.invocation,
        MasterSpecialist.HISTORICAL_ANALYTICS,
        "list_historical_encounters",
        step.capability,
        temporal_scopes=(TemporalScope.HISTORICAL,),
        historical_window=window,
        idempotency_key=f"idem-incomplete-{step.invocation.invocation_id}",
    )
    _identity, _principal, _grant, consumed = _consume(durable, issuer, ingress.agent_b_handoff(), envelope)
    assert consumed.outcome is MasterExecutionLedgerOutcome.CLAIMED
    attempt = consumed.attempt
    assert attempt is not None
    binding = _read_binding(
        attempt,
        envelope,
        resource_id="historical.encounters",
        operation=ReadOperation.QUERY,
        historical_window=window,
    )
    client = bind_offline_aws_read_client(
        query_pages={
            "historical.encounters": [
                {
                    "Items": [{"record_id": {"S": "enc-incomplete"}}],
                    "LastEvaluatedKey": {"record_id": {"S": "secret-page-token"}},
                    "query_id": "source-query-incomplete",
                }
            ]
        }
    )
    result = Sprint3CScopedReadPort(client, now_utc=NOW).execute(
        binding,
        _request(
            binding,
            tool_call_id="tool-trunc-1",
            requested_arguments=(
                ("aircraft_id", AIRCRAFT),
                ("start_utc", window[0]),
                ("end_utc", window[1]),
            ),
        ),
        bind_local_test_host(
            principal_ref=ingress.principal_ref,
            policy_revision=POLICY_REVISION,
            specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
            purpose=MasterPurpose.ANSWER,
            capability=HISTORY,
            population_entity_ids=frozenset({AIRCRAFT}),
            region_state_codes=frozenset({"CA"}),
            observation_utc=OBSERVED,
            evaluation_utc=EVALUATED,
            historical_interval_id="hist-slice-incomplete",
            historical_start_utc=window[0],
            historical_end_utc=window[1],
        ),
        tool_call_id="tool-trunc-1",
        action_id="read-trunc-1",
    )
    assert result.refusal is None
    return SimpleNamespace(
        receipt=result.receipt,
        verified_zero=result.verified_zero,
        attempt=attempt,
        specialist_request_id=step.invocation.specialist_request_id,
        invocation=step.invocation,
    )


def test_invalid_reads_cancellation_incomplete_and_empty_pages_fail_closed():
    _registry, _record, _step, ingress, _issuer, _durable, envelope, _identity, _principal, _grant, consumed = _live_consumed()
    attempt = consumed.attempt
    assert attempt is not None
    binding = _read_binding(attempt, envelope)
    client = bind_offline_aws_read_client(get_items={})
    port = Sprint3CScopedReadPort(client, now_utc=NOW)
    scanned = port.execute(
        binding,
        _request(binding, operation=ReadOperation.SCAN, tool_call_id="tool-scan-1"),
        _live_host(ingress.principal_ref),
        tool_call_id="tool-scan-1",
        action_id="read-scan-1",
    )
    assert scanned.refusal is ScopedAwsReadRefusal.SCAN_NOT_PERMITTED
    assert scanned.adapter_invoked is False
    assert client.operations == []
    empty_binding = describe_consumed_attempt_read_binding(
        **{
            **{name: getattr(binding, name) for name in (
                "owner_id",
                "attempt_token",
                "fence_epoch",
                "grant_nonce",
                "payload_fingerprint",
                "ledger_state",
                "grant_consumption_lifecycle",
                "master_request_id",
                "planning_session_id",
                "step_id",
                "invocation_id",
                "specialist",
                "purpose",
                "capability",
                "principal_ref",
                "policy_revision",
                "tool_name",
                "resource_id",
                "operation",
                "region_scope",
                "accepted_at_utc",
                "deadline_utc",
                "correlation_id",
            )},
            "idempotency_key": "idem-empty-slice",
        }
    )
    empty = Sprint3CScopedReadPort(bind_offline_aws_read_client(get_items={}), now_utc=NOW).execute(
        empty_binding,
        _request(empty_binding, tool_call_id="tool-empty-1"),
        _live_host(ingress.principal_ref),
        tool_call_id="tool-empty-1",
        action_id="read-empty-1",
    )
    assert empty.receipt is not None
    assert empty.receipt.page_empty is True
    assert empty.receipt.verified_zero_minted is False
    assert empty.verified_zero is False
    truncated = _truncated_historical_page()
    assert truncated.receipt is not None
    assert truncated.receipt.truncated is True
    assert truncated.receipt.has_more is True
    assert truncated.receipt.partial_pagination is True
    assert truncated.verified_zero is False
    assert truncated.receipt.verified_zero_minted is False
    assert "secret-page-token" not in repr(truncated.receipt)
    evidence_attempt = bind_scoped_read_to_consumed_attempt(
        truncated.attempt,
        truncated.receipt,
        tool_call_id="tool-trunc-1",
        tool_name="list_historical_encounters",
        resource_id="historical.encounters",
        operation=ReadOperation.QUERY,
        specialist_request_id=truncated.specialist_request_id,
        correlation_id=CORRELATION,
        region_scope=("CA",),
    )
    incomplete = join_sprint3c_evidence_response(
        Sprint3cEvidenceResponseInput(
            authorization=truncated.invocation,
            consumed_attempt=evidence_attempt,
            read_receipt=truncated.receipt,
        )
    )
    assert Sprint3cJoinDefect.PARTIAL_OR_TRUNCATED in incomplete.defects
    assert incomplete.claims_admitted is False
    assert incomplete.verified_zero_established is False
    with pytest.raises(ContractValidationError, match="ledger_execution_state_must_stay_started"):
        describe_consumed_attempt_read_binding(
            **{
                **{name: getattr(binding, name) for name in (
                    "owner_id",
                    "attempt_token",
                    "fence_epoch",
                    "grant_nonce",
                    "idempotency_key",
                    "payload_fingerprint",
                    "grant_consumption_lifecycle",
                    "master_request_id",
                    "planning_session_id",
                    "step_id",
                    "invocation_id",
                    "specialist",
                    "purpose",
                    "capability",
                    "principal_ref",
                    "policy_revision",
                    "tool_name",
                    "resource_id",
                    "operation",
                    "region_scope",
                    "accepted_at_utc",
                    "deadline_utc",
                    "correlation_id",
                )},
                "ledger_state": "CONSUMED",
                "idempotency_key": "idem-consumed-label",
            }
        )


def test_forged_receipt_and_copied_authority_are_rejected():
    _registry, _record, step, ingress, issuer, durable, envelope, _identity, principal, grant, consumed = _live_consumed()
    attempt = consumed.attempt
    assert attempt is not None
    with pytest.raises(ContractValidationError, match="forged_read_receipt"):
        bind_scoped_read_to_consumed_attempt(
            attempt,
            {"adapter_id": "nonproduction:forged", "source_authenticity": "ESTABLISHED"},
            tool_call_id="tool-slice-1",
            tool_name=TOOL,
            resource_id="aircraft",
            operation=ReadOperation.GET_ITEM,
            specialist_request_id=step.invocation.specialist_request_id,
            correlation_id=CORRELATION,
            region_scope=("CA",),
        )
    copied = dict(grant.serialized_claim())
    parsed = parse_execution_grant(copied)
    replayed_copy = durable.consume_signed_grant(
        parsed,
        envelope=envelope,
        principal=principal,
        identity=bind_recorded_deny_identity(ingress.agent_b_handoff()),
        owner_id=OWNER,
        now_utc=NOW,
        lease_seconds=LEASE,
    )
    assert replayed_copy.outcome is MasterExecutionLedgerOutcome.REPLAYED
    assert replayed_copy.fresh_transition is False
    assert replayed_copy.replayed is True
    assert replayed_copy.specialist_executed is False
    assert replayed_copy.execution_authority is MasterRouteExecutionAuthority.DENIED
    tampered = dict(copied)
    tampered["signature"] = "0" * 64
    forged_copy = durable.consume_signed_grant(
        parse_execution_grant(tampered),
        envelope=envelope,
        principal=principal,
        identity=bind_recorded_deny_identity(ingress.agent_b_handoff()),
        owner_id=OWNER,
        now_utc=NOW,
        lease_seconds=LEASE,
    )
    assert forged_copy.outcome is MasterExecutionLedgerOutcome.REJECTED
    assert forged_copy.grant_reason is GrantRejectionReason.TAMPERED
    assert forged_copy.specialist_executed is False
    with pytest.raises(ContractValidationError, match="serialized_grant_rejected"):
        authority_from_durable_attempt(
            parsed,
            attempt,
            principal=principal,
            grant_envelope=envelope,
            ledger_envelope=ledger_envelope_from_grant(envelope, principal_ref=principal.reference),
        )
    orchestrator = Sprint3CNativeSpecialistOrchestrator(
        MasterAuthorizedSchedulerAdapter(
            _registry,
            _record,
            deadline_utc=DEADLINE,
            integration_seal=open_nonproduction_scheduler_seal(),
        ),
        issuer=issuer,
        execution_ledger=durable.shared_execution_ledger(),
    )
    bundle = MasterParallelContractBundle(
        context=_registry.context_for(_record),
        plan=_registry.plan_for(_record),
        evidence_inputs=(),
    )
    descriptor = next(item for item in describe_planned_adapters(bundle).descriptors if item.step_id == "live-1")
    live_binding = prepare_master_live_ops_binding(bundle, descriptor)
    authority = authority_from_durable_attempt(
        grant,
        attempt,
        principal=principal,
        grant_envelope=envelope,
        ledger_envelope=ledger_envelope_from_grant(envelope, principal_ref=principal.reference),
    )
    provider = SliceProvider(DECOY)
    with pytest.raises(ContractValidationError):
        orchestrator.execute_live_ops(
            step.invocation,
            live_binding,
            authority,
            Sprint3CLiveOpsRuntimeInjection(
                provider=provider,
                tables=fixtures.california_tables(),
                now_epoch=fixtures.NOW,
                handlers=_authorized_handler(AIRCRAFT, []),
                cancelled=lambda: True,
                tool_call_id_factory=lambda: "tool-cancel-1",
            ),
            now_utc=NOW,
        )
    assert provider.requests == []
