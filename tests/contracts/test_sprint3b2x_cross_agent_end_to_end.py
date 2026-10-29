"""Offline cross-agent path for Sprint 3B.2X. Not a production authorization.

One retained Master plan is passed through the ingress policy port, the
grant/ledger host, the scheduler port, the scoped-read harness, and the
evidence integration. External services stay doubled. Production issuance,
native specialist execution, source authenticity, and AWS access stay closed.

Gaps this module records, and does not paper over:

- ``NonproductionIdentityPolicyBinding`` stays ``DENY``. Its synthetic
  principal is not a ``LocalHarnessPrincipal`` and is not redeemable.
- ``NonproductionBoundaryResult`` does not return the ledger envelope it
  stored. A field-identical envelope is rebuilt from the public reservation
  tokens. That is the same claim, not a second reservation.
- ``consume_local_and_mark_started`` marks the ledger started and then
  consumes the local grant. Those are two calls, two nonces, and not an
  atomic exactly-once write. ``bind_production_grant_to_master_ledger``
  remains closed.
- ``MasterTrustedReadExecutor.execute`` does not accept
  ``LocalHarnessExecutionGrant``. The scoped-read harness seals its own
  ``HostReadInvocation`` with ``grant_nonce``
  ``nonproduction-not-a-ledger-{action_id}``.
- ``MasterParallelContractBundle`` rejects a ``ReadReceiptCandidate``.
  MA5 is exercised on the retained plan with that artifact refused.
"""

from __future__ import annotations

import inspect
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError, TemporalScope
from wilvor_ai.master_application_principal import (
    IndependentlyHostAuthenticatedIdentityInput,
    PrincipalRecordKind,
    mint_nonproduction_contract_seal,
)
from wilvor_ai.master_authenticated_read_receipt import (
    GeographicLimitation,
    PopulationCondition,
    ReadReceiptGap,
    ReadReceiptVerificationStatus,
)
from wilvor_ai.master_authorized_scheduler_adapter import (
    MasterAuthorizedSchedulerAdapter,
    ScriptedTestWorker,
    open_nonproduction_scheduler_seal,
)
from wilvor_ai.master_authorized_scope import (
    EffectiveScopeRequestHints,
    bind_local_test_host,
    promote_effective_scope_to_execution_grant,
)
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import (
    MasterAdmissionReason,
    MasterAdmissionStatus,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
)
from wilvor_ai.master_evidence_corroboration import MasterEvidenceCorroborationInput
from wilvor_ai.master_execution_grants import (
    GrantEvaluationClock,
    GrantRejectionReason,
    LocalHarnessConsumptionLedger,
    LocalHarnessGrantIssuer,
    LogicalReadOperation,
    MasterExecutionGrantEnvelope,
    bind_production_grant_to_master_ledger,
    evaluate_grant_envelope,
    issue_production_execution_grant,
)
from wilvor_ai.master_execution_ledger import (
    InMemoryMasterExecutionLedgerStore,
    MasterExecutionLedger,
    MasterExecutionLedgerEnvelope,
    MasterExecutionLedgerRejection,
    MasterExecutionLedgerState,
)
from wilvor_ai.master_gateway_identity import MasterGatewayIdentityConfiguration
from wilvor_ai.master_gateway_runtime_boundary import (
    ConfigurationRestrictionDecision,
    InvokePermissionStatement,
    RejectedInvocationProof,
    ServerOwnedGatewayManifest,
    expected_execution_arn,
    expected_invoke_arn,
    expected_source_arn,
)
from wilvor_ai.master_host_evidence_integration import (
    HostEvidenceLimitation,
    integrate_host_read_evidence,
    open_production_source_authenticator,
)
from wilvor_ai.master_host_grant_ledger_integration import (
    CONSUMPTION_STARTED_BOUNDARY,
    DECISION_TARGET_NOT_APPLICABLE,
    NonproductionBoundaryOutcome,
    NonproductionHostGrantLedger,
)
from wilvor_ai.master_host_ingress_policy_integration import (
    IngressBindingDisposition,
    issue_production_ingress_policy,
    open_nonproduction_ingress_policy_port,
)
from wilvor_ai.master_host_scheduler_integration import NonproductionHostSchedulerPort
from wilvor_ai.master_host_scoped_read_integration import (
    NonproductionScopedReadHarness,
    ScopedReadJoinRefusal,
    issue_production_scoped_read,
    open_production_scoped_read,
)
from wilvor_ai.master_parallel_contracts import (
    MasterParallelContractBundle,
    MasterResponsePolicyState,
)
from wilvor_ai.master_planning_session import (
    MasterPlanningSessionRecord,
    MasterPlanningSessionRegistry,
)
from wilvor_ai.master_read_resource_guard import (
    ReadOperation,
    server_read_budget,
    server_read_resources,
)
from wilvor_ai.master_response_policy import evaluate_master_response_policy
from wilvor_ai.master_response_renderer import render_master_response
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
)
from wilvor_ai.master_runtime_budget import BudgetEvaluation, StartedTaskDisposition
from wilvor_ai.master_runtime_policy import (
    HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE,
    POLICY_REVISION,
    MasterRuntimePolicyDecision,
    MasterRuntimePolicyDenialReason,
    PolicyAuthorityOrigin,
    TargetObligationKind,
    seal_server_policy_inputs,
)
from wilvor_ai.master_specialist_runtime_contract import PRODUCTION_HOST
from wilvor_ai.master_trusted_read_executor import (
    HostReadInvocation,
    MasterTrustedReadExecutor,
    NonproductionAdapterSpec,
    NonproductionReadPage,
    ReadBoundaryDenial,
    ReadExecutionOutcome,
    UntrustedReadRequest,
    bind_nonproduction_cancellation,
    bind_nonproduction_host_clock,
    bind_nonproduction_read_registry,
    bind_nonproduction_test_invocation,
    issue_production_read_invocation,
    open_production_executor,
    raw_read_observation,
)


LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORICAL = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
APPLICATION = "2026-01-15T00:00:00Z"
HINTS_ACCEPTED = "2026-01-14T21:00:00Z"
OBSERVED = "2026-01-14T22:00:00Z"
EVALUATED = "2026-01-14T23:00:00Z"
DEADLINE = "2026-01-15T00:30:00Z"
NOW = "2026-01-15T00:10:00Z"
TAKEOVER = "2026-01-15T00:11:01Z"
AFTER_DEADLINE = "2026-01-15T00:31:00Z"
EIGHT_DAY_START = "2026-01-01T00:00:00Z"
EIGHT_DAY_END = "2026-01-09T00:00:00Z"
HIST_START = "2026-10-20T18:00:00Z"
HIST_END = "2026-10-27T18:00:00Z"
NOW_CLOCK = datetime(2026, 1, 15, 0, 10, tzinfo=timezone.utc)
LEASE = 60
OWNER = "ledger-owner-1"
OTHER_OWNER = "ledger-owner-2"
PRINCIPAL_REF = "principal-cross-agent"
GRANT_POLICY = "policy-rev-cross-agent"
TOOL = "get_aircraft_operational_context"
AIRCRAFT = "AC1"
MASTER_REQUEST = "master-req-cross-1"
CORRELATION = "corr-cross-1"
TEXT = "Which aircraft are currently affected by hazards?"
EVALUATION_EPOCH = 1_767_225_600
ISSUED_AT = EVALUATION_EPOCH - 30
NOT_BEFORE = EVALUATION_EPOCH - 10
EXPIRES_AT = EVALUATION_EPOCH + 300
MISLEADING_TIME_EPOCH_MS = 1_700_000_000_000
ISSUER = "https://idp.wilvor.test/realms/ops"
AUDIENCE = "wilvor-master-ai"
SUBJECT = "subject-cross-agent-1"
SCOPE = "wilvor.ai.query"
ROUTE_KEY = "POST /ai/query"
GATEWAY_REQUEST = "gw-req-cross-1"
PROOF_ID = "proof-cross-1"
PROVENANCE = "wilvor-host-ingress-nonproduction"
ACCOUNT = "123456789012"
FORGED_ACCOUNT = "999999999999"
API_ID = "abc123def0"
REGION = "us-west-1"
PARTITION = "aws"
NAME_PREFIX = "wilvor-dev"
FUNCTION_ARN = f"arn:{PARTITION}:lambda:{REGION}:{ACCOUNT}:function:wilvor-ai-query"
LOG_GROUP = "/aws/lambda/wilvor-ai-query"
READ_ACTION = "read-cross-1"
READ_TOOL_CALL = "tool-cross-1"


class _DirectInvokeContext:
    function_name = "wilvor-ai-query"
    aws_request_id = "direct-invoke-not-gateway"
    invoked_function_arn = FUNCTION_ARN


def _errors(func: object) -> tuple[str, ...]:
    with pytest.raises(ContractValidationError) as caught:
        func()  # type: ignore[operator]
    return caught.value.errors


def _exact_scope(specialist: MasterSpecialist, tool_name: str) -> tuple[LogicalReadOperation, ...]:
    pairs = sorted(server_read_resources(specialist, tool_name), key=lambda item: (item[0], item[1].value))
    return tuple(LogicalReadOperation(resource_id, operation) for resource_id, operation in pairs)


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
    source = expected_source_arn(expected_execution_arn(PARTITION, REGION, ACCOUNT, API_ID), "$default")
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
        ai_log_group_name=LOG_GROUP,
    )


def _event(**overrides: object) -> dict[str, object]:
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": SUBJECT,
        "exp": str(EXPIRES_AT),
        "iat": str(ISSUED_AT),
        "nbf": str(NOT_BEFORE),
        "scope": SCOPE,
    }
    claim_overrides = overrides.pop("claims", {})
    if type(claim_overrides) is dict:
        claims.update(claim_overrides)
    event: dict[str, object] = {
        "version": "2.0",
        "routeKey": ROUTE_KEY,
        "rawPath": "/ai/query",
        "rawQueryString": "",
        "headers": {"authorization": "Bearer forged-header.not-a-subject"},
        "requestContext": {
            "accountId": FORGED_ACCOUNT,
            "apiId": "zzzzzzzzzz",
            "authorizer": {"jwt": {"claims": claims, "scopes": [SCOPE]}},
            "http": {"method": "POST", "path": "/ai/query", "protocol": "HTTP/1.1"},
            "requestId": GATEWAY_REQUEST,
            "routeKey": ROUTE_KEY,
            "stage": "prod",
            "timeEpoch": MISLEADING_TIME_EPOCH_MS,
        },
        "isBase64Encoded": False,
    }
    event.update(overrides)
    return event


def _host_identity(record: MasterPlanningSessionRecord) -> IndependentlyHostAuthenticatedIdentityInput:
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
        issued_at_epoch=ISSUED_AT,
        not_before_epoch=NOT_BEFORE,
        expires_at_epoch=EXPIRES_AT,
        application_request_time_utc=APPLICATION,
    )


def _server():
    return seal_server_policy_inputs(
        evaluation_utc=APPLICATION,
        accepted_at_utc=APPLICATION,
        deadline_utc=DEADLINE,
        executions_used=0,
        model_turns_used=0,
        tool_calls_used=0,
        cancelled=False,
        region_codes=frozenset(),
        tool_name=TOOL,
        resource_id="aircraft",
        operation=ReadOperation.GET_ITEM,
        arguments=(("aircraft_id", AIRCRAFT),),
        target_kind=TargetObligationKind.NOT_APPLICABLE,
        target_id=None,
        idempotency_key="idem-policy-cross-1",
    )


def _ingress_port():
    return open_nonproduction_ingress_policy_port(
        _configuration(),
        authorization_provenance=PROVENANCE,
        seal=mint_nonproduction_contract_seal(),
    )


@dataclass
class Lane:
    """One retained Live Ops plan and the offline hosts bound to it."""

    registry: MasterPlanningSessionRegistry
    record: MasterPlanningSessionRecord
    invocation: MasterInvocationAuthorization
    issuer: LocalHarnessGrantIssuer
    principal: object
    consumption: LocalHarnessConsumptionLedger
    execution: MasterExecutionLedger
    grant_host: NonproductionHostGrantLedger
    envelope: MasterExecutionGrantEnvelope
    plan_snapshot: tuple[object, ...]
    worker: ScriptedTestWorker
    adapter: MasterAuthorizedSchedulerAdapter
    port: NonproductionHostSchedulerPort

    @classmethod
    def open(
        cls,
        *,
        issuer: LocalHarnessGrantIssuer | None = None,
        principal: object | None = None,
        consumption: LocalHarnessConsumptionLedger | None = None,
        execution: MasterExecutionLedger | None = None,
        idempotency_key: str | None = None,
    ) -> Lane:
        registry = MasterPlanningSessionRegistry()
        proposal = MasterRouteProposal(
            user_text=TEXT,
            capabilities=(MasterProposedCapability(LIVE, MasterRequirement.REQUIRED),),
            steps=(
                MasterProposedStep(
                    step_id="live-1",
                    specialist=MasterSpecialist.LIVE_OPS,
                    purpose=MasterPurpose.ANSWER,
                    requirement=MasterRequirement.REQUIRED,
                    capability=LIVE,
                    temporal_intent=MasterRouteTemporalIntent.CURRENT,
                ),
            ),
        )
        context = MasterPlanningContext(
            master_request_id=MASTER_REQUEST,
            correlation_id=CORRELATION,
            application_request_time_utc=APPLICATION,
            canonical_user_text=TEXT,
        )
        record = registry.open_session(proposal, context)
        decision = registry.decision_for(record)
        assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
        assert decision.plan is not None
        plan = registry.require_retained_plan(record, registry.plan_for(record))
        assert plan is decision.plan
        step = plan.steps[0]
        invocation = step.invocation
        assert invocation.master_request_id == MASTER_REQUEST
        assert invocation.correlation_id == CORRELATION
        assert invocation.application_request_time_utc == APPLICATION
        assert invocation.specialist is MasterSpecialist.LIVE_OPS
        assert invocation.purpose is MasterPurpose.ANSWER
        assert step.capability is LIVE
        assert isinstance(invocation.specialist_request_id, str)
        _executions, turns, calls = server_read_budget(invocation.specialist)
        grant_issuer = issuer if issuer is not None else LocalHarnessGrantIssuer(GrantEvaluationClock(NOW_CLOCK))
        grant_principal = principal if principal is not None else grant_issuer.seal_principal(PRINCIPAL_REF)
        grant_consumption = consumption if consumption is not None else LocalHarnessConsumptionLedger()
        grant_execution = execution if execution is not None else MasterExecutionLedger(InMemoryMasterExecutionLedgerStore())
        key = idempotency_key or f"idem-grant-{invocation.invocation_id}"
        envelope = MasterExecutionGrantEnvelope(
            master_request_id=invocation.master_request_id,
            planning_session_id=record.session_id,
            step_id=step.step_id,
            invocation_id=invocation.invocation_id,
            specialist=invocation.specialist,
            purpose=invocation.purpose,
            capability=step.capability,
            principal_ref=PRINCIPAL_REF,
            policy_revision=GRANT_POLICY,
            tool_name=TOOL,
            resource_scope=_exact_scope(invocation.specialist, TOOL),
            region_scope=("CA",),
            temporal_scopes=(TemporalScope.CURRENT,),
            historical_window=None,
            decision_target_scope=None,
            accepted_at_utc=APPLICATION,
            deadline_utc=DEADLINE,
            expires_at_utc=DEADLINE,
            idempotency_key=key,
            max_model_turns=turns,
            max_tool_calls=calls,
            correlation_id=CORRELATION,
        )
        seal = open_nonproduction_scheduler_seal()
        adapter = MasterAuthorizedSchedulerAdapter(registry, record, deadline_utc=DEADLINE, integration_seal=seal)
        scheduler = NonproductionHostSchedulerPort(adapter, grant_issuer, grant_consumption, grant_execution)
        worker = ScriptedTestWorker()
        scheduler.register(MasterSpecialist.LIVE_OPS, worker)
        return cls(
            registry=registry,
            record=record,
            invocation=invocation,
            issuer=grant_issuer,
            principal=grant_principal,
            consumption=grant_consumption,
            execution=grant_execution,
            grant_host=NonproductionHostGrantLedger(issuer=grant_issuer, ledger=grant_execution),
            envelope=envelope,
            plan_snapshot=_snapshot(plan),
            worker=worker,
            adapter=adapter,
            port=scheduler,
        )

    @property
    def step(self):
        return self.registry.plan_for(self.record).steps[0]

    def bind_ingress(self, port: object | None = None, **overrides: object):
        active = _ingress_port() if port is None else port
        values: dict[str, object] = {
            "manifest": _manifest(),
            "event": _event(),
            "host_identity": _host_identity(self.record),
            "registry": self.registry,
            "planning_record": self.record,
            "invocation": self.invocation,
            "server": _server(),
            "evaluation_epoch": EVALUATION_EPOCH,
            "lambda_context": _DirectInvokeContext(),
            "trusted": True,
        }
        values.update(overrides)
        return active.bind(**values)  # type: ignore[attr-defined, arg-type]

    def reserve(self, **overrides: object):
        values: dict[str, object] = {"owner_id": OWNER, "now_utc": NOW, "lease_seconds": LEASE}
        values.update(overrides)
        return self.grant_host.reserve_aligned(self.envelope, principal=self.principal, **values)  # type: ignore[arg-type]

    def start(self, reserved, **overrides: object):
        values: dict[str, object] = {
            "owner_id": reserved.owner_id,
            "attempt_token": reserved.attempt_token,
            "fence_epoch": reserved.fence_epoch,
            "now_utc": NOW,
        }
        values.update(overrides)
        return self.grant_host.consume_local_and_mark_started(
            reserved.grant,
            principal=self.principal,
            local_consumption_ledger=self.consumption,
            **values,  # type: ignore[arg-type]
        )

    def ledger_envelope(self, reserved) -> MasterExecutionLedgerEnvelope:
        """Rebuild the claim from the tokens the grant host already published."""

        assert reserved.resource_scope == "aircraft:GET_ITEM"
        assert reserved.region_scope == "CA"
        assert reserved.decision_target_scope == DECISION_TARGET_NOT_APPLICABLE
        return MasterExecutionLedgerEnvelope(
            master_request_id=self.envelope.master_request_id,
            planning_session_id=self.envelope.planning_session_id,
            step_id=self.envelope.step_id,
            invocation_id=self.envelope.invocation_id,
            specialist=self.envelope.specialist,
            purpose=self.envelope.purpose,
            capability=self.envelope.capability,
            principal_ref=self.envelope.principal_ref,
            policy_revision=self.envelope.policy_revision,
            resource_scope=reserved.resource_scope,
            region_scope=reserved.region_scope,
            decision_target_scope=reserved.decision_target_scope,
            accepted_at_utc=self.envelope.accepted_at_utc,
            deadline_utc=self.envelope.deadline_utc,
            idempotency_key=self.envelope.idempotency_key,
            max_model_turns=self.envelope.max_model_turns,
            max_tool_calls=self.envelope.max_tool_calls,
            correlation_id=self.envelope.correlation_id,
            repeat_non_idempotent_after_expiry=False,
        )

    def dispatch(self, started, **overrides: object):
        ledger = overrides.pop("ledger_envelope", None)
        if ledger is None:
            ledger = self.ledger_envelope(started)
        values: dict[str, object] = {
            "grant": started.grant,
            "principal": self.principal,
            "grant_envelope": self.envelope,
            "ledger_envelope": ledger,
            "owner_id": started.owner_id,
            "attempt_token": started.attempt_token,
            "fence_epoch": started.fence_epoch,
            "now_utc": NOW,
        }
        values.update(overrides)
        return self.port.dispatch_consumed(self.invocation, **values)  # type: ignore[arg-type]


def _snapshot(plan: object) -> tuple[object, ...]:
    return (
        plan.to_dict(),  # type: ignore[attr-defined]
        tuple(step.invocation for step in plan.steps),  # type: ignore[attr-defined]
        plan.execution_authority,  # type: ignore[attr-defined]
    )


def _live_hints(lane: Lane, **overrides: object) -> EffectiveScopeRequestHints:
    values: dict[str, object] = {
        "master_request_id": lane.invocation.master_request_id,
        "planning_session_id": lane.record.session_id,
        "step_id": lane.step.step_id,
        "invocation_id": lane.invocation.invocation_id,
        "correlation_id": CORRELATION,
        "idempotency_key": f"idem-read-{lane.invocation.invocation_id}",
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "capability": LIVE,
        "tool_name": TOOL,
        "resource_id": "aircraft",
        "operation": ReadOperation.GET_ITEM,
        "accepted_at_utc": HINTS_ACCEPTED,
        "deadline_utc": DEADLINE,
        "arguments": (("aircraft_id", AIRCRAFT),),
        "location_hint": "California",
        "census_region_name": "California",
        "sigmet_region_string": "Oakland FIR",
        "entity_id": AIRCRAFT,
        "aircraft_id": AIRCRAFT,
    }
    values.update(overrides)
    return EffectiveScopeRequestHints(**values)  # type: ignore[arg-type]


def _live_host():
    return bind_local_test_host(
        principal_ref="scoped-read-host-principal",
        policy_revision="scoped-read-policy",
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        capability=LIVE,
        population_entity_ids=frozenset({AIRCRAFT}),
        region_state_codes=frozenset({"CA", "NV"}),
        observation_utc=OBSERVED,
        evaluation_utc=EVALUATED,
    )


def _harness(read: object, **spec: object) -> NonproductionScopedReadHarness:
    values: dict[str, object] = {
        "specialist": MasterSpecialist.LIVE_OPS,
        "tool_name": TOOL,
        "capability": LIVE,
        "resource_id": "aircraft",
        "operation": ReadOperation.GET_ITEM,
    }
    values.update(spec)
    registry = bind_nonproduction_read_registry(
        (
            NonproductionAdapterSpec(
                specialist=values["specialist"],  # type: ignore[arg-type]
                tool_name=values["tool_name"],  # type: ignore[arg-type]
                capability=values["capability"],  # type: ignore[arg-type]
                resource_id=values["resource_id"],  # type: ignore[arg-type]
                operation=values["operation"],  # type: ignore[arg-type]
                read=read,  # type: ignore[arg-type]
            ),
        )
    )
    return NonproductionScopedReadHarness(registry, now_utc=APPLICATION)


def _page(**overrides: object) -> NonproductionReadPage:
    values: dict[str, object] = {"record_ids": (AIRCRAFT,), "source_name": "aircraft"}
    values.update(overrides)
    return NonproductionReadPage(**values)  # type: ignore[arg-type]


def _codes(result: object) -> set[str]:
    scope = getattr(result, "scope")
    return {reason.value for reason in scope.denial_reasons}


def _arm_environment() -> dict[str, str | None]:
    keys = ("WILVOR_TEST_MODE", "WILVOR_PRODUCTION_PRINCIPAL", "WILVOR_ALLOW_SYNTHETIC_PRINCIPAL")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ["WILVOR_TEST_MODE"] = "1"
    os.environ["WILVOR_PRODUCTION_PRINCIPAL"] = "true"
    os.environ["WILVOR_ALLOW_SYNTHETIC_PRINCIPAL"] = "1"
    return previous


def _restore_environment(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_cross_agent_local_path_preserves_one_invocation_and_stays_closed():
    previous = _arm_environment()
    try:
        lane = Lane.open()
        port = _ingress_port()
        binding = lane.bind_ingress(port=port)
        reserved = lane.reserve()
        replayed_reserve = lane.reserve()
        started = lane.start(reserved)
        replayed_start = lane.start(reserved)
        ledger = lane.ledger_envelope(started)
        check = lane.execution.fenced_attempt_matches(
            ledger,
            owner_id=OWNER,
            attempt_token=started.attempt_token,
            fence_epoch=started.fence_epoch,
            grant_nonce=started.ledger_grant_nonce,
        )
        outcome = lane.dispatch(started, ledger_envelope=ledger)
        calls: list[int] = []

        def read(arguments: dict[str, object], _cancellation: object) -> NonproductionReadPage:
            calls.append(1)
            assert arguments["aircraft_id"] == AIRCRAFT
            assert "limit" not in arguments
            return _page()

        hints = _live_hints(lane)
        widened = _harness(read).run(
            replace(hints, caller_max_tool_calls=99, idempotency_key="idem-read-caller-budget"),
            _live_host(),
            tool_call_id="tool-caller-budget",
            action_id="read-caller-budget",
        )
        harness = _harness(read)
        read_result = harness.run(hints, _live_host(), tool_call_id=READ_TOOL_CALL, action_id=READ_ACTION)
        prepared = harness.prepare(
            replace(hints, idempotency_key="idem-read-expired"),
            _live_host(),
            tool_call_id="tool-expired",
            action_id="read-expired",
            session=read_result.session,
        )
        harness.advance_seconds(120)
        expired_read = harness.enter(prepared)
        receipt = read_result.receipt
        assert receipt is not None
        evidence = integrate_host_read_evidence(
            receipt,
            lane.invocation,
            expected_step_id=lane.step.step_id,
            expected_tool_call_id=READ_TOOL_CALL,
            expected_aircraft_id=AIRCRAFT,
        )
        rendered = _render_refused_receipt(lane, receipt)
    finally:
        _restore_environment(previous)

    assert binding.disposition is IngressBindingDisposition.NONPRODUCTION
    assert binding.redeemable is False
    assert binding.policy_decision is MasterRuntimePolicyDecision.DENY
    assert binding.policy_denial_reasons == (MasterRuntimePolicyDenialReason.PRINCIPAL_NOT_HOST_AUTHENTICATED,)
    assert binding.policy_authority_origin is PolicyAuthorityOrigin.NOT_ESTABLISHED
    assert binding.consumed_record_kind is PrincipalRecordKind.CONSUMED_SYNTHETIC_ADMISSION
    assert binding.principal.record_kind is PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION
    assert binding.principal.production_principal is False
    assert binding.membership_established is True
    assert binding.membership_is_authentication is False
    assert binding.planning_session_id == lane.record.session_id
    assert binding.master_request_id == MASTER_REQUEST
    assert binding.invocation_id == lane.invocation.invocation_id
    assert binding.specialist_request_id == lane.invocation.specialist_request_id
    assert binding.correlation_id == CORRELATION
    assert binding.specialist is MasterSpecialist.LIVE_OPS
    assert binding.purpose is MasterPurpose.ANSWER
    assert binding.capability is LIVE
    assert binding.application_request_time_utc == APPLICATION
    assert binding.original_step_id == "live-1"
    assert binding.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert binding.production_authority is MasterProductionAuthority.DENIED
    assert binding.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert binding.permit_issued is False
    assert binding.allows_specialist_execution() is False
    assert RejectedInvocationProof.FORGED_HTTP_API_EVENT in binding.rejected_invocation_proofs
    assert RejectedInvocationProof.JWT_LOOKING_MATERIAL in binding.rejected_invocation_proofs
    assert RejectedInvocationProof.AUTHORIZATION_HEADER in binding.rejected_invocation_proofs
    assert binding.identity_assessment.production_authenticated is False
    assert binding.identity_assessment.raw_authorization.accepted_as_authentication is False
    assert binding.boundary_report.configuration_decision is ConfigurationRestrictionDecision.RESTRICTED
    assert binding.boundary_report.event_accepted_as_invocation_source is False
    assert type(binding.principal) is not type(lane.principal)
    assert HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE is False
    assert _errors(binding.redeem) == ("nonproduction_binding_not_redeemable",)
    assert "replayed_principal" in _errors(lambda: lane.bind_ingress(port=port))
    assert _errors(lambda: port.present_again(binding)) == ("replayed_principal",)
    assert _errors(lambda: lane.grant_host.reserve_aligned(
        lane.envelope, principal=binding.principal, owner_id=OWNER, now_utc=NOW, lease_seconds=LEASE
    )) == ("principal_not_sealed",)
    assert binding.policy_decision is MasterRuntimePolicyDecision.DENY

    assert reserved.outcome is NonproductionBoundaryOutcome.RESERVED_LOCAL
    assert reserved.fresh_transition is True
    assert reserved.grant_newly_issued is True
    assert reserved.fence_epoch == 1
    assert reserved.state is MasterExecutionLedgerState.RESERVED
    assert reserved.attempt_token == reserved.attempt_nonce
    assert reserved.idempotency_key == lane.envelope.idempotency_key
    assert reserved.grant_nonce != reserved.ledger_grant_nonce
    assert reserved.production_grant is False
    assert reserved.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert replayed_reserve.outcome is NonproductionBoundaryOutcome.REPLAYED_LOCAL
    assert replayed_reserve.grant is reserved.grant
    assert replayed_reserve.attempt_token == reserved.attempt_token
    assert replayed_reserve.grant_newly_issued is False
    assert started.outcome is NonproductionBoundaryOutcome.STARTED_LOCAL
    assert started.countable_local_start is True
    assert started.local_harness_consumption_recorded is True
    assert started.fresh_transition is True
    assert started.state is MasterExecutionLedgerState.STARTED
    assert started.fence_epoch == 1
    assert started.attempt_token == reserved.attempt_token
    assert started.durable_ledger_consumed is False
    assert started.distributed_exactly_once is False
    assert started.dispatch_permitted is False
    assert lane.consumption.holds_nonce(started.grant_nonce) is True
    assert lane.consumption.durable is False
    assert replayed_start.outcome is NonproductionBoundaryOutcome.REPLAYED_LOCAL
    assert replayed_start.countable_local_start is False
    assert lane.grant_host.distributed_exactly_once_achieved is False
    assert lane.grant_host.consumption_started_boundary == CONSUMPTION_STARTED_BOUNDARY
    assert lane.issuer.production_grants_issued == 0
    assert check.matches_current_attempt is True
    assert check.authorizes_execution is False
    assert _errors(lambda: issue_production_execution_grant(started.grant)) == ("production_grant_issuance_closed",)
    assert _errors(lambda: bind_production_grant_to_master_ledger(started.grant, ledger)) == (
        "ledger_not_integrated",
    )

    assert outcome.native_specialist_executed is False
    assert outcome.scheduling_completed is True
    assert outcome.ma4_admission_performed is False
    assert outcome.evidence_verified is False
    assert outcome.aviation_evidence is False
    assert outcome.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert outcome.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert outcome.invocation_id == lane.invocation.invocation_id
    assert lane.worker.invocation_ids == [lane.invocation.invocation_id]
    assert lane.port.production_dispatch_enabled is False
    assert lane.port.distributed_atomicity is False
    assert lane.port.durable_exactly_once is False
    assert lane.adapter.production_dispatch_enabled is False
    assert PRODUCTION_HOST is None
    confirmation = lane.port.last_confirmation
    assert confirmation is not None
    assert confirmation.grant_consumed is True
    assert confirmation.fence_replayed is True
    assert confirmation.ledger_grant_nonce == started.ledger_grant_nonce
    assert confirmation.ledger_grant_nonce != started.grant_nonce
    assert confirmation.ledger_nonce_is_host_grant is False
    assert confirmation.native_specialist_executed is False
    assert _snapshot(lane.registry.plan_for(lane.record)) == lane.plan_snapshot
    assert _errors(lambda: lane.dispatch(started, ledger_envelope=ledger)) == ("duplicate_claim",)
    assert lane.worker.invocation_ids == [lane.invocation.invocation_id]
    handoff = lane.port.handoff(NOW)
    assert handoff.admission_performed is False
    assert handoff.specialist_runners_invoked is False
    assert handoff.aviation_evidence is False
    assert handoff.production_dispatch_enabled is False

    assert widened.adapter_invoked is False
    assert "CALLER_BUDGET_NOT_AUTHORITY" in _codes(widened)
    assert calls == [1]
    assert read_result.refusal is None
    assert read_result.adapter_invoked is True
    assert receipt.operation is ReadOperation.GET_ITEM
    assert receipt.resource_id == "aircraft"
    assert receipt.requested_arguments == (("aircraft_id", AIRCRAFT),)
    assert receipt.executed_arguments == (("aircraft_id", AIRCRAFT),)
    assert receipt.effective_region_codes == ("CA",)
    assert "NV" not in receipt.effective_region_codes
    assert receipt.source_version is None
    assert receipt.query_id is None
    assert receipt.grant_nonce == f"nonproduction-not-a-ledger-{READ_ACTION}"
    assert receipt.grant_nonce != started.grant_nonce
    assert receipt.grant_nonce != started.ledger_grant_nonce
    assert receipt.deadline_utc != DEADLINE
    assert receipt.accepted_at_utc == APPLICATION
    assert receipt.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert receipt.production_grant_issued is False
    assert receipt.verified_zero_minted is False
    assert receipt.geographic_coverage == "NOT_ESTABLISHED"
    assert read_result.sigmet_polygon_intersection_established is False
    assert read_result.session is not None
    assert read_result.session.usage(invocation_id=lane.invocation.invocation_id).tool_calls == 1
    _executions, _turns, tool_ceiling = server_read_budget(MasterSpecialist.LIVE_OPS)
    assert tool_ceiling >= 1
    assert expired_read.adapter_invoked is False
    assert expired_read.budget is not None
    assert expired_read.budget.evaluation is BudgetEvaluation.DEADLINE_EXPIRED
    assert calls == [1]

    assert evidence.observation_status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
    assert evidence.receipt_status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
    assert evidence.authorization_bound is True
    assert evidence.binding_defects == ()
    assert evidence.admission.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.UNSUPPORTED_ARTIFACT in evidence.admission.reason_codes
    assert evidence.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert evidence.aviation_evidence_admitted is False
    assert evidence.verified_zero_established is False
    assert evidence.low_risk_admitted is False
    assert evidence.decision_risk_level is None
    assert evidence.polygon_intersection_established is False
    assert evidence.impacted_aircraft_count_established is False
    assert PopulationCondition.SOURCE_VERSION_ABSENT in evidence.population_conditions
    assert PopulationCondition.MISSING_QUERY_ID in evidence.population_conditions
    assert ReadReceiptGap.MISSING_SOURCE_VERSION in evidence.gaps
    assert ReadReceiptGap.MISSING_QUERY_ID in evidence.gaps
    assert GeographicLimitation.POLYGON_INTERSECTION_NOT_ESTABLISHED in evidence.geographic_limitations
    assert rendered.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert rendered.claims_admitted is False
    assert "LOW" not in rendered.status_text
    assert "recommend" not in rendered.status_text.casefold()
    assert not any(character.isdigit() for character in rendered.status_text)
    assert _snapshot(lane.registry.plan_for(lane.record)) == lane.plan_snapshot


def test_agent2_grant_does_not_authorize_the_scoped_read():
    lane = Lane.open()
    reserved = lane.reserve()
    started = lane.start(reserved)
    calls: list[int] = []

    def read(*_args: object) -> NonproductionReadPage:
        calls.append(1)
        return _page()

    hints = _live_hints(lane)
    harness = _harness(read)
    presented = harness.run(hints, started.grant, tool_call_id=READ_TOOL_CALL, action_id=READ_ACTION)
    registry = bind_nonproduction_read_registry(
        (
            NonproductionAdapterSpec(
                specialist=MasterSpecialist.LIVE_OPS,
                tool_name=TOOL,
                capability=LIVE,
                resource_id="aircraft",
                operation=ReadOperation.GET_ITEM,
                read=read,
            ),
        )
    )
    executor = MasterTrustedReadExecutor(registry, bind_nonproduction_host_clock(APPLICATION))
    request = UntrustedReadRequest(
        master_request_id=lane.invocation.master_request_id,
        planning_session_id=lane.record.session_id,
        step_id=lane.step.step_id,
        invocation_id=lane.invocation.invocation_id,
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        capability=LIVE,
        tool_name=TOOL,
        tool_call_id=READ_TOOL_CALL,
        resource_id="aircraft",
        operation=ReadOperation.GET_ITEM,
        requested_arguments=(("aircraft_id", AIRCRAFT),),
        correlation_id=CORRELATION,
    )
    rejected = executor.execute(
        request,
        host_invocation=started.grant,
        cancellation=bind_nonproduction_cancellation(),
    )

    assert calls == []
    assert presented.adapter_invoked is False
    assert presented.refusal is ScopedReadJoinRefusal.SCOPE_DENIED
    assert "PRODUCTION_INPUT_NOT_A_HOST" in _codes(presented)
    assert presented.production_grant_issued is False
    assert rejected.adapter_invoked is False
    assert rejected.outcome is not ReadExecutionOutcome.COMPLETED
    assert ReadBoundaryDenial.HOST_AUTHORITY_ABSENT in rejected.denial_reasons
    assert ReadBoundaryDenial.PRODUCTION_GRANT_NOT_INTEGRATED in rejected.denial_reasons
    assert rejected.production_grant_issued is False
    assert not isinstance(started.grant, HostReadInvocation)
    assert _errors(lambda: issue_production_read_invocation(started.grant)) == ("production_grant_issuer_not_integrated",)
    assert _errors(lambda: open_production_executor(started.grant)) == ("production_read_executor_not_enabled",)
    assert _errors(open_production_scoped_read) == ("production_read_executor_not_enabled",)
    assert _errors(lambda: issue_production_scoped_read(started.grant)) == ("production_grant_issuer_not_integrated",)
    succeeded = harness.run(hints, _live_host(), tool_call_id=READ_TOOL_CALL, action_id=READ_ACTION)
    assert succeeded.receipt is not None
    assert _errors(lambda: promote_effective_scope_to_execution_grant(succeeded.scope)) == (
        "effective_scope_is_not_execution_grant",
    )
    assert calls == [1]


def test_forged_gateway_event_and_jwt_cannot_authenticate_the_host():
    previous = _arm_environment()
    try:
        lane = Lane.open()
        forged = _event()
        assert _errors(lambda: lane.bind_ingress(event=forged, host_identity=forged)) == (
            "caller_built_identity_not_host_issued",
        )
        malformed = _event(claims={"scope": "wilvor.ai.query wilvor.ai.admin"})
        assert _errors(lambda: lane.bind_ingress(event=malformed)) == ("malformed_scopes",)
        binding = lane.bind_ingress(event=forged)
        assert _errors(lambda: issue_production_ingress_policy(forged, binding)) == (
            "production_principal_issuance_unavailable",
            "reviewed_host_authority_not_wired",
            "nonproduction_binding_not_redeemable",
        )
    finally:
        _restore_environment(previous)

    assert binding.policy_decision is MasterRuntimePolicyDecision.DENY
    assert binding.policy_denial_reasons == (MasterRuntimePolicyDenialReason.PRINCIPAL_NOT_HOST_AUTHENTICATED,)
    assert binding.identity_assessment.production_authenticated is False
    assert binding.identity_assessment.raw_authorization.payload_decoded is False
    assert binding.identity_assessment.raw_authorization.accepted_as_authentication is False
    assert RejectedInvocationProof.FORGED_HTTP_API_EVENT in binding.rejected_invocation_proofs
    assert RejectedInvocationProof.JWT_LOOKING_MATERIAL in binding.rejected_invocation_proofs
    assert RejectedInvocationProof.AUTHORIZATION_HEADER in binding.rejected_invocation_proofs
    assert binding.permit_issued is False
    assert binding.redeemable is False
    assert binding.allows_specialist_execution() is False


def test_copied_invocation_and_copied_session_fail_closed():
    lane = Lane.open()
    copied = MasterInvocationAuthorization.from_dict(lane.invocation.to_dict())
    assert copied is not lane.invocation
    assert copied.invocation_id == lane.invocation.invocation_id
    assert "copied_invocation_identifier" in _errors(lambda: lane.bind_ingress(invocation=copied))
    port = _ingress_port()
    lane.bind_ingress(port=port)
    assert lane.registry.selected_invocation(lane.record) is lane.invocation
    assert _errors(lambda: lane.bind_ingress(port=port, invocation=copied)) == ("wrong_invocation",)
    reserved = lane.reserve()
    started = lane.start(reserved)
    assert _errors(lambda: lane.port.dispatch_consumed(
        copied,
        grant=started.grant,
        principal=lane.principal,
        grant_envelope=lane.envelope,
        ledger_envelope=lane.ledger_envelope(started),
        owner_id=started.owner_id,
        attempt_token=started.attempt_token,
        fence_epoch=started.fence_epoch,
        now_utc=NOW,
    )) == ("substituted_invocation_rejected",)
    assert lane.worker.invocation_ids == []
    copied_record = MasterPlanningSessionRecord(lane.record.session_id, object())
    assert _errors(lambda: lane.registry.context_for(copied_record)) == ("copied_session_identifier",)
    assert _errors(lambda: lane.bind_ingress(planning_record=copied_record)) == ("copied_session_identifier",)
    assert _snapshot(lane.registry.plan_for(lane.record)) == lane.plan_snapshot


def test_cross_principal_and_cross_session_grants_are_rejected():
    issuer = LocalHarnessGrantIssuer(GrantEvaluationClock(NOW_CLOCK))
    principal = issuer.seal_principal(PRINCIPAL_REF)
    consumption = LocalHarnessConsumptionLedger()
    execution = MasterExecutionLedger(InMemoryMasterExecutionLedgerStore())
    left = Lane.open(issuer=issuer, principal=principal, consumption=consumption, execution=execution, idempotency_key="idem-grant-left")
    right = Lane.open(
        issuer=issuer,
        principal=principal,
        consumption=consumption,
        execution=execution,
        idempotency_key="idem-grant-right",
    )
    twin = issuer.seal_principal(PRINCIPAL_REF)
    assert twin is not principal
    reserved = left.reserve()
    mismatched = left.grant_host.consume_local_and_mark_started(
        reserved.grant,
        principal=twin,
        local_consumption_ledger=consumption,
        owner_id=OWNER,
        attempt_token=reserved.attempt_token,
        fence_epoch=reserved.fence_epoch,
        now_utc=NOW,
    )
    started = left.start(reserved)
    other_reserved = right.reserve()
    other_started = right.start(other_reserved)

    assert mismatched.outcome is NonproductionBoundaryOutcome.DENIED
    assert mismatched.grant_reason is GrantRejectionReason.PRINCIPAL_MISMATCH
    assert mismatched.countable_local_start is False
    assert consumption.holds_nonce(reserved.grant_nonce) is True
    assert _errors(lambda: left.dispatch(other_started)) == ("SESSION_MISMATCH",)
    assert _errors(lambda: left.dispatch(started, principal=twin)) == ("PRINCIPAL_MISMATCH",)
    assert left.worker.invocation_ids == []
    assert right.worker.invocation_ids == []
    assert started.production_grant is False


def test_unconsumed_grant_cannot_dispatch():
    lane = Lane.open()
    reserved = lane.reserve()
    assert reserved.state is MasterExecutionLedgerState.RESERVED
    assert lane.consumption.holds_nonce(reserved.grant_nonce) is False
    assert _errors(lambda: lane.dispatch(reserved)) == ("consumed_grant_required",)
    assert lane.worker.invocation_ids == []
    assert lane.port.production_dispatch_enabled is False


def test_replayed_grant_does_not_create_another_start():
    lane = Lane.open()
    reserved = lane.reserve()
    first = lane.start(reserved)
    second = lane.start(reserved)
    countable = [item for item in (first, second) if item.countable_local_start]

    assert first.outcome is NonproductionBoundaryOutcome.STARTED_LOCAL
    assert second.outcome is NonproductionBoundaryOutcome.REPLAYED_LOCAL
    assert second.fresh_transition is False
    assert second.countable_local_start is False
    assert second.grant is first.grant
    assert second.attempt_token == first.attempt_token
    assert second.fence_epoch == first.fence_epoch
    assert countable == [first]
    assert second.distributed_exactly_once is False
    assert second.dispatch_permitted is False


def test_stale_fence_after_takeover_cannot_dispatch():
    lane = Lane.open()
    first = lane.reserve()
    taken = lane.reserve(owner_id=OTHER_OWNER, now_utc=TAKEOVER)
    stale = lane.start(
        taken,
        owner_id=OWNER,
        attempt_token=first.attempt_token,
        fence_epoch=first.fence_epoch,
        now_utc=TAKEOVER,
    )
    started = lane.start(taken, owner_id=OTHER_OWNER, now_utc=TAKEOVER)

    assert taken.outcome is NonproductionBoundaryOutcome.RESERVED_LOCAL
    assert taken.grant_newly_issued is False
    assert taken.fence_epoch == 2
    assert taken.attempt_token != first.attempt_token
    assert taken.ledger_grant_nonce == first.ledger_grant_nonce
    assert taken.grant_nonce == first.grant_nonce
    assert stale.ledger_rejection is MasterExecutionLedgerRejection.FENCED_OUT
    assert stale.countable_local_start is False
    assert started.countable_local_start is True
    assert _errors(lambda: lane.dispatch(
        started,
        owner_id=OTHER_OWNER,
        attempt_token=first.attempt_token,
        fence_epoch=first.fence_epoch,
        now_utc=TAKEOVER,
    )) == ("stale_fence",)
    assert lane.worker.invocation_ids == []
    assert lane.execution.grant_nonce_rotates_on_takeover is False


def test_second_dispatch_is_rejected():
    lane = Lane.open()
    started = lane.start(lane.reserve())
    first = lane.dispatch(started)
    assert first.scheduling_completed is True
    assert lane.worker.invocation_ids == [lane.invocation.invocation_id]
    assert _errors(lambda: lane.dispatch(started)) == ("duplicate_claim",)
    assert lane.worker.invocation_ids == [lane.invocation.invocation_id]
    assert _snapshot(lane.registry.plan_for(lane.record)) == lane.plan_snapshot


def test_expired_deadline_and_cancellation_fail_closed():
    expired = Lane.open()
    expired_started = expired.start(expired.reserve())
    assert _errors(lambda: expired.dispatch(expired_started, now_utc=AFTER_DEADLINE)) == ("deadline_expired",)
    assert expired.worker.invocation_ids == []

    cancelled = Lane.open()
    cancelled_reserved = cancelled.reserve()
    cancelled_grant = cancelled.grant_host.cancel_reserved(
        cancelled_reserved.grant,
        principal=cancelled.principal,
        owner_id=OWNER,
        attempt_token=cancelled_reserved.attempt_token,
        fence_epoch=cancelled_reserved.fence_epoch,
        now_utc=NOW,
    )
    blocked = cancelled.start(cancelled_reserved)
    assert cancelled_grant.outcome is NonproductionBoundaryOutcome.CANCELLED_LOCAL
    assert cancelled_grant.countable_local_start is False
    assert blocked.outcome is NonproductionBoundaryOutcome.DENIED
    assert blocked.countable_local_start is False
    assert cancelled.consumption.holds_nonce(cancelled_reserved.grant_nonce) is False

    scheduled = Lane.open()
    scheduled_started = scheduled.start(scheduled.reserve())
    scheduled.adapter.cancel(now_utc=NOW)
    assert _errors(lambda: scheduled.dispatch(scheduled_started)) == ("schedule_cancelled",)
    assert scheduled.worker.invocation_ids == []
    assert scheduled.port.handoff(NOW).admission_performed is False


def test_scan_and_out_of_scope_resource_do_not_read():
    lane = Lane.open()
    calls: list[int] = []
    hints = _live_hints(lane)
    harness = _harness(lambda *_args: calls.append(1) or _page())
    host = _live_host()
    scanned = harness.run(
        replace(hints, operation=ReadOperation.SCAN, idempotency_key="idem-read-scan"),
        host,
        tool_call_id="tool-scan",
        action_id="read-scan",
    )
    foreign = harness.run(
        replace(hints, resource_id="sigmets", idempotency_key="idem-read-sigmets"),
        host,
        tool_call_id="tool-sigmets",
        action_id="read-sigmets",
    )
    write = harness.run(
        replace(hints, tool_name="put_item", idempotency_key="idem-read-put"),
        host,
        tool_call_id="tool-put",
        action_id="read-put",
    )

    assert calls == []
    assert scanned.adapter_invoked is False
    assert "SCAN_NOT_PERMITTED" in _codes(scanned)
    assert foreign.adapter_invoked is False
    assert foreign.refusal is ScopedReadJoinRefusal.SCOPE_DENIED
    assert write.adapter_invoked is False
    assert "TOOL_SELECTION_OUT_OF_BOUNDS" in _codes(write)
    assert scanned.production_grant_issued is False


def test_historical_interval_beyond_seven_days_is_not_clipped():
    lane = Lane.open()
    live_window = replace(
        lane.envelope,
        historical_window=(EIGHT_DAY_START, EIGHT_DAY_END),
        idempotency_key="idem-grant-live-window",
    )
    live_evaluation = evaluate_grant_envelope(live_window)
    _executions, turns, calls = server_read_budget(MasterSpecialist.HISTORICAL_ANALYTICS)
    historical = MasterExecutionGrantEnvelope(
        master_request_id=lane.invocation.master_request_id,
        planning_session_id=lane.record.session_id,
        step_id="hist-eight",
        invocation_id="inv-eight-day",
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        purpose=MasterPurpose.ANSWER,
        capability=HISTORICAL,
        principal_ref=PRINCIPAL_REF,
        policy_revision=GRANT_POLICY,
        tool_name="summarize_historical_encounters",
        resource_scope=_exact_scope(MasterSpecialist.HISTORICAL_ANALYTICS, "summarize_historical_encounters"),
        region_scope=("CA",),
        temporal_scopes=(TemporalScope.HISTORICAL,),
        historical_window=(EIGHT_DAY_START, EIGHT_DAY_END),
        decision_target_scope=None,
        accepted_at_utc=APPLICATION,
        deadline_utc=DEADLINE,
        expires_at_utc=DEADLINE,
        idempotency_key="idem-grant-eight-day",
        max_model_turns=turns,
        max_tool_calls=calls,
        correlation_id="corr-eight-day",
    )
    historical_evaluation = evaluate_grant_envelope(historical)
    denied = lane.grant_host.reserve_aligned(
        historical,
        principal=lane.principal,
        owner_id=OWNER,
        now_utc=NOW,
        lease_seconds=LEASE,
    )
    calls: list[int] = []
    harness = _harness(
        lambda *_args: calls.append(1) or _page(),
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        tool_name="list_historical_encounters",
        capability=HISTORICAL,
        resource_id="historical.encounters",
        operation=ReadOperation.QUERY,
    )
    hinted = EffectiveScopeRequestHints(
        master_request_id=lane.invocation.master_request_id,
        planning_session_id=lane.record.session_id,
        step_id="hist-eight",
        invocation_id="inv-eight-read",
        correlation_id="corr-eight-read",
        idempotency_key="idem-read-eight",
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        purpose=MasterPurpose.ANSWER,
        capability=HISTORICAL,
        tool_name="list_historical_encounters",
        resource_id="historical.encounters",
        operation=ReadOperation.QUERY,
        accepted_at_utc="2026-10-27T16:00:00Z",
        deadline_utc="2026-10-27T20:00:00Z",
        arguments=(("aircraft_id", AIRCRAFT), ("end_utc", HIST_END), ("start_utc", "2026-10-19T18:00:00Z")),
        aircraft_id=AIRCRAFT,
        entity_id=AIRCRAFT,
        historical_start_utc="2026-10-19T18:00:00Z",
        historical_end_utc=HIST_END,
    )
    host = bind_local_test_host(
        principal_ref="scoped-read-host-principal",
        policy_revision="scoped-read-policy",
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        purpose=MasterPurpose.ANSWER,
        capability=HISTORICAL,
        population_entity_ids=frozenset({AIRCRAFT}),
        region_state_codes=frozenset({"CA"}),
        observation_utc="2026-10-27T17:30:00Z",
        evaluation_utc="2026-10-27T18:00:00Z",
        historical_interval_id="hist-interval-cross",
        historical_start_utc=HIST_START,
        historical_end_utc=HIST_END,
    )
    read = harness.run(hinted, host, tool_call_id="tool-eight", action_id="read-eight")

    assert GrantRejectionReason.HISTORICAL_WINDOW_NOT_APPLICABLE in live_evaluation.reasons
    assert live_evaluation.bounds_accepted is False
    assert GrantRejectionReason.HISTORICAL_INTERVAL_EXCEEDS_CEILING in historical_evaluation.reasons
    assert historical_evaluation.bounds_accepted is False
    assert denied.outcome is NonproductionBoundaryOutcome.DENIED
    assert denied.grant is None
    assert denied.grant_newly_issued is False
    assert calls == []
    assert read.adapter_invoked is False
    assert "UNBOUNDED_HISTORICAL_WINDOW" in _codes(read)
    assert read.scope is not None
    assert read.scope.historical_window.eligible_start_utc is None
    assert read.scope.historical_window.eligible_end_utc is None
    assert read.scope.historical_window.within_seven_days is False


def test_conflicting_decision_targets_are_rejected():
    lane = Lane.open()
    left = evaluate_grant_envelope(replace(lane.envelope, decision_target_scope="ac-left", idempotency_key="idem-grant-left-target"))
    right = evaluate_grant_envelope(replace(lane.envelope, decision_target_scope="ac-right", idempotency_key="idem-grant-right-target"))
    denied = lane.grant_host.reserve_aligned(
        replace(lane.envelope, decision_target_scope="ac-left", idempotency_key="idem-grant-left-target"),
        principal=lane.principal,
        owner_id=OWNER,
        now_utc=NOW,
        lease_seconds=LEASE,
    )

    assert left.decision_target_scope is None
    assert right.decision_target_scope is None
    assert GrantRejectionReason.DECISION_TARGET_NOT_APPLICABLE in left.reasons
    assert GrantRejectionReason.DECISION_TARGET_NOT_APPLICABLE in right.reasons
    assert denied.outcome is NonproductionBoundaryOutcome.DENIED
    assert denied.grant is None
    assert GrantRejectionReason.DECISION_TARGET_NOT_APPLICABLE in denied.grant_reasons
    assert denied.decision_target_trusted is False


def test_local_receipt_is_not_promoted_to_authenticated_evidence():
    lane = Lane.open()
    receipt = _one_read(lane)
    evidence = integrate_host_read_evidence(
        receipt,
        lane.invocation,
        expected_step_id=lane.step.step_id,
        expected_tool_call_id=READ_TOOL_CALL,
    )
    serialized = integrate_host_read_evidence(raw_read_observation(receipt), lane.invocation)
    rendered = _render_refused_receipt(lane, receipt)

    assert evidence.admission.admission_status is MasterAdmissionStatus.REJECTED
    assert evidence.admission.evidence_verified is False
    assert evidence.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert evidence.aviation_evidence_admitted is False
    assert serialized.receipt_status is ReadReceiptVerificationStatus.NOT_A_RECEIPT
    assert HostEvidenceLimitation.SERIALIZED_OBSERVATION_NOT_RECEIPT in serialized.limitations
    assert serialized.admission.admission_status is MasterAdmissionStatus.REJECTED
    assert _errors(lambda: MasterParallelContractBundle(
        context=lane.registry.context_for(lane.record),
        plan=lane.registry.plan_for(lane.record),
        evidence_inputs=(
            MasterEvidenceCorroborationInput(MasterEvidenceAdmissionInput(lane.invocation, receipt)),
        ),
    )) == ("unsupported_artifact",)
    assert _errors(open_production_source_authenticator) == ("production_source_authenticator_not_integrated",)
    assert rendered.claims_admitted is False
    assert rendered.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert not any(character.isdigit() for character in rendered.status_text)


def test_empty_read_is_not_a_verified_zero():
    lane = Lane.open()
    calls: list[int] = []
    result = _harness(lambda *_args: calls.append(1) or _page(record_ids=())).run(
        _live_hints(lane, idempotency_key="idem-read-empty"),
        _live_host(),
        tool_call_id="tool-empty",
        action_id="read-empty",
    )
    assert result.receipt is not None
    evidence = integrate_host_read_evidence(
        result.receipt,
        lane.invocation,
        expected_step_id=lane.step.step_id,
        expected_tool_call_id="tool-empty",
        expected_aircraft_id=AIRCRAFT,
    )

    assert calls == [1]
    assert result.verified_zero is False
    assert result.receipt.page_empty is True
    assert result.receipt.rows_returned == 0
    assert result.receipt.verified_zero_minted is False
    assert evidence.verified_zero_established is False
    assert evidence.observed_rows_returned == 0
    assert HostEvidenceLimitation.ZERO_ROWS_NOT_VERIFIED_ZERO in evidence.limitations
    assert ReadReceiptGap.SCOPED_EMPTY_NOT_VERIFIED_ZERO in evidence.gaps
    assert evidence.admission.admission_status is MasterAdmissionStatus.REJECTED
    assert evidence.impacted_aircraft_count_established is False


def test_missing_risk_is_not_recorded_as_low():
    lane = Lane.open()
    receipt = _one_read(lane)
    evidence = integrate_host_read_evidence(
        receipt,
        lane.invocation,
        expected_step_id=lane.step.step_id,
        expected_tool_call_id=READ_TOOL_CALL,
        untrusted_claim={"risk_level": "LOW", "risk": "LOW", "recommendation": "divert now"},
    )
    rendered = _render_refused_receipt(lane, receipt)

    assert HostEvidenceLimitation.CLAIMED_LOW_RISK_NOT_ESTABLISHED in evidence.limitations
    assert evidence.low_risk_admitted is False
    assert evidence.decision_risk_level is None
    assert evidence.operational_claim_admitted is False
    assert evidence.admission.admission_status is MasterAdmissionStatus.REJECTED
    assert "LOW" not in rendered.status_text
    assert "divert" not in rendered.status_text.casefold()
    assert rendered.claims_admitted is False


def test_california_text_is_not_sigmet_polygon_overlap():
    lane = Lane.open()
    calls: list[int] = []
    hinted = _live_hints(lane, claims_exact_sigmet_polygon=True, claims_geographic_coverage=True)
    denied = _harness(lambda *_args: calls.append(1) or _page()).run(
        hinted,
        _live_host(),
        tool_call_id="tool-polygon",
        action_id="read-polygon",
    )
    claim = integrate_host_read_evidence(
        {
            "state": "California",
            "region_text": "Oakland FIR",
            "polygon_intersection": True,
            "impacted_aircraft_count": 4,
        },
        lane.invocation,
    )

    assert calls == []
    assert denied.adapter_invoked is False
    assert denied.sigmet_polygon_intersection_established is False
    assert denied.geographic_coverage_established is False
    assert "FALSE_GEOGRAPHIC_COVERAGE_CLAIM" in _codes(denied)
    assert claim.polygon_intersection_established is False
    assert claim.impacted_aircraft_count_established is False
    assert claim.aviation_evidence_admitted is False
    assert GeographicLimitation.REGIONAL_TEXT_NOT_POLYGON in claim.geographic_limitations
    assert GeographicLimitation.POLYGON_INTERSECTION_NOT_ESTABLISHED in claim.geographic_limitations
    assert GeographicLimitation.IMPACT_COUNT_NOT_ESTABLISHED in claim.geographic_limitations
    assert HostEvidenceLimitation.REGIONAL_TEXT_NOT_POLYGON_INTERSECTION in claim.limitations
    assert claim.admission.admission_status is MasterAdmissionStatus.REJECTED
    assert claim.receipt_status is ReadReceiptVerificationStatus.NOT_A_RECEIPT


def test_duplicate_read_idempotency_invokes_the_adapter_once():
    lane = Lane.open()
    calls: list[int] = []
    harness = _harness(lambda *_args: calls.append(1) or _page())
    hints = _live_hints(lane)
    host = _live_host()
    first = harness.run(hints, host, tool_call_id=READ_TOOL_CALL, action_id=READ_ACTION)
    second = harness.run(
        hints,
        host,
        tool_call_id="tool-cross-2",
        action_id="read-cross-2",
    )

    assert calls == [1]
    assert first.receipt is not None
    assert first.receipt.outcome is ReadExecutionOutcome.COMPLETED
    assert second.adapter_invoked is False
    assert second.receipt is not None
    assert ReadBoundaryDenial.DUPLICATE_CONSUMPTION in second.receipt.denial_reasons
    assert second.receipt.verified_zero_minted is False


def _one_read(lane: Lane):
    result = _harness(lambda *_args: _page()).run(
        _live_hints(lane),
        _live_host(),
        tool_call_id=READ_TOOL_CALL,
        action_id=READ_ACTION,
    )
    assert result.receipt is not None
    assert result.adapter_invoked is True
    return result.receipt


def _render_refused_receipt(lane: Lane, receipt: object):
    context = lane.registry.context_for(lane.record)
    plan = lane.registry.plan_for(lane.record)
    with pytest.raises(ContractValidationError) as caught:
        MasterParallelContractBundle(
            context=context,
            plan=plan,
            evidence_inputs=(MasterEvidenceCorroborationInput(MasterEvidenceAdmissionInput(lane.invocation, receipt)),),
        )
    assert caught.value.errors == ("unsupported_artifact",)
    handoff = evaluate_master_response_policy(MasterParallelContractBundle(context=context, plan=plan, evidence_inputs=()))
    assert handoff.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert handoff.production_authority is MasterProductionAuthority.DENIED
    assert handoff.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    return render_master_response(handoff)


def test_bind_nonproduction_test_invocation_has_no_grant_parameter():
    """The reviewed read seal takes a nonce string. It cannot accept the grant."""

    parameters = inspect.signature(bind_nonproduction_test_invocation).parameters
    assert "grant" not in parameters
    assert parameters["grant_nonce"].annotation in {str, "str"}
    lane = Lane.open()
    started = lane.start(lane.reserve())
    assert _errors(lambda: issue_production_read_invocation(started.grant)) == (
        "production_grant_issuer_not_integrated",
    )
    assert _errors(lambda: open_production_executor(started.grant)) == ("production_read_executor_not_enabled",)
