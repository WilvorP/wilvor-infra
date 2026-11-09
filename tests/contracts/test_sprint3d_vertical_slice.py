"""Sprint 3D cross-agent slice. Offline success is not production readiness."""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.contracts.sprint3d_validation_contract import (
    ExecutionTier,
    classify_execution,
    locate_sprint3d_hooks,
)
from tests.contracts.test_sprint3c_specialist_orchestration import BEFORE
from tests.contracts.test_sprint3d_auth_grants import (
    KID,
    SUBJECT,
    _claims,
    _provider,
    _token,
)
from tests.contracts.test_sprint3d_auth_grants import NOW as TOKEN_ISSUED
from tests.contracts.test_sprint3d_aws_execution import (
    AIRCRAFT_TABLE,
    LedgerFake,
    ReadFake,
    _caller,
    _deployment,
    _described_tables,
    _simulation,
)
from tests.contracts.test_sprint3d_native_specialists import (
    AIRCRAFT as DECISION_AIRCRAFT,
    COPIED_AIRCRAFT,
    COPIED_RECOMMENDATION,
    ELIGIBLE,
    PERMITTED_RECOMMENDATION,
    PLAN,
    QUERY_TIMESTAMP,
    READ_VERSION,
    Harness,
    OfflineProvider,
    _catalog,
    _decision,
    _decision_harness,
    _decision_host,
    _decision_port,
    _decision_provider,
    _historical_call,
    _historical_claims,
    _historical_host,
    _historical_port,
    _historical_response,
    _readiness,
    _work,
)
from tests.contracts.test_decision_tools_integration import NOW as SOURCE_EPOCH
from tests.contracts.test_decision_tools_integration import _operational
from tests.fakes.recording_historical_operations import RecordingHistoricalOperations
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
from wilvor_ai.historical_specialist import SequentialToolCallIdFactory
from wilvor_ai.live_ops import get_aircraft_operational_context
from wilvor_ai.live_ops_claims import AircraftObservedStateClaim, LiveOpsObservedField
from wilvor_ai.live_ops_model_contracts import LiveOpsModelDecision, LiveOpsModelDecisionKind
from wilvor_ai.live_ops_public_contracts import LiveOpsRunRequest, LiveOpsTrustedContext
from wilvor_ai.master_authorized_scheduler_adapter import ScriptedTestWorker
from wilvor_ai.master_authorized_scope import bind_local_test_host
from wilvor_ai.master_contracts import MasterPurpose, MasterRequirement, MasterSpecialist
from wilvor_ai.master_decision_binding import prepare_master_decision_binding
from wilvor_ai.master_evidence_admission import (
    MasterAdmissionReason,
    MasterAdmissionStatus,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
)
from wilvor_ai.master_evidence_corroboration import MasterEvidenceCorroborationInput
from wilvor_ai.master_execution_grants import LogicalReadOperation, MasterExecutionGrantEnvelope
from wilvor_ai.master_execution_ledger import (
    MasterExecutionLedgerOutcome,
    MasterExecutionLedgerState,
)
from wilvor_ai.master_historical_binding import prepare_master_historical_binding
from wilvor_ai.master_live_ops_binding import prepare_master_live_ops_binding
from wilvor_ai.master_parallel_contracts import MasterParallelContractBundle, MasterResponseLimitation
from wilvor_ai.master_planning_session import MasterPlanningSessionRegistry
from wilvor_ai.master_read_resource_guard import ReadOperation, server_read_budget
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
    MasterRuntimePolicyDenialReason,
    MasterRuntimePolicyEnvelope,
    PolicyAuthorityOrigin,
    TargetObligationKind,
    evaluate_master_runtime_policy,
    retain_runtime_policy_session,
    seal_server_policy_inputs,
)
from wilvor_ai.master_specialist_adapters import describe_planned_adapters
from wilvor_ai.master_trusted_read_executor import ReadExecutionOutcome, UntrustedReadRequest
from wilvor_ai.model_contracts import ProposedToolCall
from wilvor_ai.sprint3c_evidence_response import (
    Sprint3cEvidenceResponseInput,
    Sprint3cJoinDefect,
    Sprint3cRejectedClaim,
    bind_scoped_read_to_consumed_attempt,
    join_sprint3c_evidence_response,
)
from wilvor_ai.sprint3c_specialist_orchestration import Sprint3CLiveOpsRuntimeInjection
from wilvor_ai.sprint3d_auth_grants import (
    InjectedGrantSigningKeys,
    Sprint3DRejection,
    authenticate_bearer,
    establish_trusted_principal,
)
from wilvor_ai.sprint3d_aws_execution import AwsExecutionRefusal, live_network_gate_open, open_production_aws_execution
from wilvor_ai.sprint3d_integration_host import INTEGRATION_HOOK_MAP, Sprint3DIntegrationHost
from wilvor_ai.sprint3d_native_specialists import execute_decision_native, execute_historical_native
from wilvor_ai.sprint3d_source_evidence import (
    RejectedClaim,
    SourceAuthenticity,
    SourceEvidenceHost,
    SourceReadInvocation,
    SupportedFactualClaim,
    evaluate_source_evidence,
)
from wilvor_ai.specialist_contracts import SpecialistStatus

REPO_ROOT = Path(__file__).resolve().parents[2]
GEOJSON = REPO_ROOT / "functions" / "shared" / "wilvor_operational" / "data" / "us_states.geojson"
GEOJSON_SHA256 = "83cd47c5449be7559ef9595afc881718f11175f65542c743c7ae5d831d5b12c5"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
ACCEPTED = "2026-01-15T00:00:00Z"
DEADLINE = "2026-01-15T00:05:00Z"
NOW_TEXT = "2026-01-15T00:02:00Z"
QUERY_CLOCK = "2026-01-15T00:01:00Z"
OBSERVATION_CLOCK = "2026-01-15T00:02:30Z"
EVALUATION_CLOCK = "2026-01-15T00:03:00Z"
SOURCE_VERSION = "sv-slice-authorized"
AIRCRAFT = "N77WILVOR"
CALLSIGN = "WILVOR7"
OWNER = "slice-owner-1"
TOOL = "get_aircraft_operational_context"
NOW = datetime(2026, 1, 15, 0, 2, tzinfo=timezone.utc)
HISTORY_NOW = datetime(2026, 1, 15, 0, 10, tzinfo=timezone.utc)


class SliceProvider:
    def __init__(self, aircraft_id: str) -> None:
        self.aircraft_id = aircraft_id
        self.requests = []

    def complete(self, turn):
        self.requests.append(turn)
        if not turn.tool_results:
            return LiveOpsModelDecision(
                kind=LiveOpsModelDecisionKind.TOOL_CALLS,
                tool_calls=(ProposedToolCall(TOOL, {"aircraft_id": self.aircraft_id}),),
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


def _keys() -> InjectedGrantSigningKeys:
    return InjectedGrantSigningKeys(active_key_id="grant-slice", keys={"grant-slice": b"k" * 32})


def _host(ledger: LedgerFake | None = None, reader: ReadFake | None = None, *, now_utc: str = NOW_TEXT):
    deployment = _deployment()
    return Sprint3DIntegrationHost(
        deployment,
        caller_identity=_caller(),
        described_tables=_described_tables(),
        iam_simulation=_simulation(deployment),
        ledger_client=ledger if ledger is not None else LedgerFake(),
        read_client=reader if reader is not None else ReadFake(),
        grant_keys=_keys(),
        now_utc=now_utc,
    )


def _step(specialist: MasterSpecialist, step_id: str, capability: AgentCapability, **window: str) -> MasterProposedStep:
    temporal = (
        MasterRouteTemporalIntent.HISTORICAL
        if specialist is MasterSpecialist.HISTORICAL_ANALYTICS
        else MasterRouteTemporalIntent.CURRENT
    )
    return MasterProposedStep(
        step_id=step_id,
        specialist=specialist,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=capability,
        temporal_intent=temporal,
        **window,
    )


def _policy(record, plan, step, *, tool_name: str, resource_id: str, operation: ReadOperation, arguments, idempotency: str, deadline: str, historical_window):
    session = retain_runtime_policy_session(record_context(record), plan)
    _executions, turns, calls = server_read_budget(step.invocation.specialist)
    server = seal_server_policy_inputs(
        evaluation_utc=ACCEPTED,
        accepted_at_utc=ACCEPTED,
        deadline_utc=deadline,
        region_codes=frozenset({"CA"}),
        tool_name=tool_name,
        resource_id=resource_id,
        operation=operation,
        arguments=arguments,
        target_kind=TargetObligationKind.NOT_APPLICABLE,
        idempotency_key=idempotency,
    )
    envelope = MasterRuntimePolicyEnvelope(
        master_request_id=step.invocation.master_request_id,
        planning_session_id=session.session_id,
        step_id=step.step_id,
        invocation_id=step.invocation.invocation_id,
        specialist=step.invocation.specialist,
        purpose=step.invocation.purpose,
        capability=step.capability,
        principal_ref=None,
        policy_revision=POLICY_REVISION,
        resource_scope=server.resource_id,
        region_scope="CA",
        historical_window=historical_window,
        decision_target_scope=None,
        accepted_at_utc=ACCEPTED,
        deadline_utc=deadline,
        idempotency_key=server.idempotency_key,
        max_model_turns=turns,
        max_tool_calls=calls,
        correlation_id=step.invocation.correlation_id,
    )
    evaluation = evaluate_master_runtime_policy(session, plan, step, step.invocation, server, envelope)
    return evaluation


def record_context(record):
    registry_holder = record_context.registry
    return registry_holder.context_for(record)


def _open(specialist, step_id, capability, master_request, correlation, deadline, **window):
    registry = MasterPlanningSessionRegistry()
    record_context.registry = registry
    context = MasterPlanningContext(
        master_request_id=master_request,
        correlation_id=correlation,
        application_request_time_utc=ACCEPTED,
        canonical_user_text="Which aircraft are currently affected?",
    )
    proposal = MasterRouteProposal(
        user_text=context.canonical_user_text,
        capabilities=(MasterProposedCapability(capability, MasterRequirement.REQUIRED),),
        steps=(_step(specialist, step_id, capability, **window),),
    )
    record = registry.open_session(proposal, context)
    decision = registry.decision_for(record)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    return registry, record, decision.plan, decision.plan.steps[0]


def _lineage(
    *,
    specialist,
    step_id,
    capability,
    tool_name,
    resource_id,
    operation,
    arguments,
    master_request,
    correlation,
    idempotency,
    jti,
    deadline,
    moment,
    now_utc,
    historical_window=None,
    ledger=None,
    reader=None,
    window_fields=None,
):
    registry, record, plan, step = _open(
        specialist,
        step_id,
        capability,
        master_request,
        correlation,
        deadline,
        **(window_fields or {}),
    )
    evaluation = _policy(
        record,
        plan,
        step,
        tool_name=tool_name,
        resource_id=resource_id,
        operation=operation,
        arguments=arguments,
        idempotency=idempotency,
        deadline=deadline,
        historical_window=historical_window,
    )
    host = _host(ledger, reader, now_utc=now_utc)
    authentication = authenticate_bearer(
        _token(_claims(jti=jti, exp=int(TOKEN_ISSUED.timestamp()) + 800)),
        _provider(),
        moment,
    )
    established = establish_trusted_principal(
        authentication,
        evaluation,
        original_planning_session_id=record.session_id,
        now=moment,
    )
    if established.principal is None:
        return host, registry, record, plan, step, evaluation, established, None, None, None
    _executions, turns, calls = server_read_budget(specialist)
    temporal = (TemporalScope.HISTORICAL,) if historical_window else (TemporalScope.CURRENT,)
    envelope = MasterExecutionGrantEnvelope(
        master_request_id=established.principal.master_request_id,
        planning_session_id=established.principal.planning_session_id,
        step_id=established.principal.step_id,
        invocation_id=established.principal.invocation_id,
        specialist=specialist,
        purpose=MasterPurpose.ANSWER,
        capability=capability,
        principal_ref=established.principal.principal_ref,
        policy_revision=POLICY_REVISION,
        tool_name=tool_name,
        resource_scope=(LogicalReadOperation(resource_id, operation),),
        region_scope=("CA",),
        temporal_scopes=temporal,
        historical_window=historical_window,
        decision_target_scope=None,
        accepted_at_utc=ACCEPTED,
        deadline_utc=deadline,
        expires_at_utc=deadline,
        idempotency_key=established.principal.idempotency_key,
        max_model_turns=turns,
        max_tool_calls=calls,
        correlation_id=established.principal.correlation_id,
    )
    _grant, coordination = host.issue_grant(established, envelope, now=moment)
    presented = host.presented_binding(established.principal, envelope)
    consumed = host.consume_verified_grant(
        coordination,
        presented,
        envelope,
        principal=established.principal,
        owner_id=OWNER,
        now_utc=now_utc,
        lease_seconds=60,
    )
    return host, registry, record, plan, step, evaluation, established, envelope, coordination, consumed


def _live(**kwargs):
    values = dict(
        specialist=MasterSpecialist.LIVE_OPS,
        step_id="live-1",
        capability=LIVE,
        tool_name=TOOL,
        resource_id="aircraft",
        operation=ReadOperation.GET_ITEM,
        arguments=(("aircraft_id", AIRCRAFT),),
        master_request="master-slice-live",
        correlation="corr-slice-live",
        idempotency="idem-slice-live",
        jti="jti-slice-live",
        deadline=DEADLINE,
        moment=NOW,
        now_utc=NOW_TEXT,
    )
    values.update(kwargs)
    return _lineage(**values)


def _seed(reader: ReadFake, aircraft_id: str = AIRCRAFT, **extra: object) -> None:
    item = {
        "aircraft_id": {"S": aircraft_id},
        "callsign": {"S": CALLSIGN},
        "on_ground": {"BOOL": True},
        "source_version": {"S": SOURCE_VERSION},
        "source_observed_at_utc": {"S": QUERY_CLOCK},
        "freshness": {"S": "FRESH"},
    }
    for name, value in extra.items():
        item[name] = value  # type: ignore[assignment]
    reader.items[(AIRCRAFT_TABLE, aircraft_id)] = item


def _read_request(attempt, aircraft_id: str = AIRCRAFT) -> UntrustedReadRequest:
    return UntrustedReadRequest(
        master_request_id=attempt.master_request_id,
        planning_session_id=attempt.planning_session_id,
        step_id=attempt.step_id,
        invocation_id=attempt.invocation_id,
        specialist=attempt.specialist,
        purpose=attempt.purpose,
        capability=attempt.capability,
        tool_name=TOOL,
        tool_call_id="tool-slice-read",
        resource_id="aircraft",
        operation=ReadOperation.GET_ITEM,
        requested_arguments=(("aircraft_id", aircraft_id),),
        correlation_id=attempt.correlation_id,
    )


def _scope(principal_ref: str):
    return bind_local_test_host(
        principal_ref=principal_ref,
        policy_revision=POLICY_REVISION,
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        capability=LIVE,
        population_entity_ids=frozenset({AIRCRAFT}),
        region_state_codes=frozenset({"CA"}),
        observation_utc=QUERY_CLOCK,
        evaluation_utc=EVALUATION_CLOCK,
    )


def test_geojson_sha256_and_size_match_the_vendored_census_artifact():
    payload = GEOJSON.read_bytes()
    assert len(payload) == 6946274
    assert hashlib.sha256(payload).hexdigest() == GEOJSON_SHA256


def test_rsa_material_is_disposable_and_not_packaged():
    key_path = Path(__file__).with_name("_rsa_test_key.txt")
    numbers = [int(line) for line in key_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    modulus, exponent, private = numbers
    assert modulus.bit_length() >= 2048
    assert exponent >= 65537
    assert private != exponent
    assert key_path.parent.name == "contracts"
    lambda_source = (REPO_ROOT / "modules" / "ai_host" / "lambda.tf").read_text(encoding="utf-8")
    assert 'source_file = "${path.module}/src/handler.py"' in lambda_source
    assert "_rsa_test_key" not in lambda_source
    packaged = (REPO_ROOT / "modules" / "ai_host" / "src").rglob("*")
    assert all("_rsa_test_key" not in path.name for path in packaged)


def test_live_ops_slice_uses_the_authorized_read_and_keeps_production_closed():
    reader = ReadFake()
    ledger = LedgerFake()
    _seed(reader)
    host, registry, record, plan, step, evaluation, established, envelope, coordination, consumed = _live(
        ledger=ledger,
        reader=reader,
    )
    assert live_network_gate_open() is False
    assert host.execution_tier == "OFFLINE_SIMULATED"
    classification = classify_execution(
        client_type_name="ReadFake",
        live_aws_connection=False,
        verified_account_provenance=False,
        production_flags_open=False,
        probe_executed=False,
    )
    assert classification.tier is ExecutionTier.OFFLINE_SIMULATED
    assert classification.live_pass is False
    assert classification.release_ready is False
    host.assert_production_closed()
    with pytest.raises(ContractValidationError, match="live_aws_probe_not_established"):
        host.probe_nonproduction_readonly()
    assert evaluation.decision is MasterRuntimePolicyDecision.DENY
    assert evaluation.authority_origin is PolicyAuthorityOrigin.NOT_ESTABLISHED
    assert evaluation.denial_reasons == (MasterRuntimePolicyDenialReason.PRINCIPAL_NOT_HOST_AUTHENTICATED,)
    assert established.principal is not None
    assert established.principal.planning_session_id == record.session_id
    assert established.principal.planning_session_id != evaluation.planning_session_id
    assert established.principal.public_policy_decision is MasterRuntimePolicyDecision.DENY
    assert consumed is not None and consumed.claimed is True and consumed.result is not None
    attempt = consumed.result.attempt
    assert attempt is not None
    assert attempt.state is MasterExecutionLedgerState.STARTED
    assert attempt.grant_nonce != attempt.attempt_token
    assert attempt.grant_nonce == coordination.request.grant_nonce
    assert attempt.payload_fingerprint == coordination.request.ledger_payload_fingerprint
    assert consumed.distributed_exactly_once is False
    assert consumed.production_authority is False
    assert host.sprint3c_consume_signed_grant_called is False
    assert ledger.operations.count("transact_write_items") == 1
    marker = next(item for item in ledger.items.values() if str(item["pk"]["S"]).startswith("consume-v1#"))
    assert "retention_expires_at_epoch" not in marker
    assert marker["grant_nonce"]["S"] == attempt.grant_nonce
    assert marker["ttl_deletion_is_correctness"]["BOOL"] is False
    ledger_envelope = host.ledger_envelope(attempt.invocation_id)
    fence = host.current_fence(
        ledger_envelope,
        owner_id=attempt.owner_id,
        attempt_token=attempt.attempt_token,
        fence_epoch=attempt.fence_epoch,
        grant_nonce=attempt.grant_nonce,
    )
    assert fence.matches_current_attempt is True
    assert fence.authorizes_execution is False
    assert fence.state is MasterExecutionLedgerState.STARTED

    captured: list[object] = []

    def handler(call, **kwargs):
        read = host.execute_server_read(attempt, _read_request(attempt), _scope(attempt.principal_ref), tool_call_id="tool-slice-read")
        captured.append(read)
        item = read.decoded_items[0]
        requested = kwargs.get("aircraft_id")
        if not isinstance(requested, str) or requested.casefold() != str(item["aircraft_id"]).casefold():
            raise RuntimeError("requested aircraft is not the read aircraft")
        row = fixtures.aircraft(requested, callsign=str(item["callsign"]), on_ground=bool(item["on_ground"]))
        call.tables.aircraft.records[row["aircraft_id"]] = row
        return get_aircraft_operational_context(call, **kwargs)

    bundle = MasterParallelContractBundle(context=registry.context_for(record), plan=plan, evidence_inputs=())
    descriptor = next(item for item in describe_planned_adapters(bundle).descriptors if item.step_id == "live-1")
    binding = prepare_master_live_ops_binding(bundle, descriptor)
    assert type(binding.request) is LiveOpsRunRequest
    provider = SliceProvider(AIRCRAFT)
    orchestrator, authority = host.open_native_dispatch(registry, record, step.invocation, attempt, envelope, now_utc=NOW_TEXT)
    executed = orchestrator.execute_live_ops(
        step.invocation,
        binding,
        authority,
        Sprint3CLiveOpsRuntimeInjection(
            provider=provider,
            tables=fixtures.tables(),
            now_epoch=fixtures.NOW,
            handlers={TOOL: handler},
            tool_call_id_factory=lambda: "tool-live-slice",
        ),
        now_utc=NOW_TEXT,
    )
    assert captured and captured[0].refusal is None
    assert captured[0].decoded_items[0]["callsign"] == CALLSIGN
    assert captured[0].decoded_items[0]["on_ground"] is True
    assert captured[0].source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert reader.requests[0]["TableName"] == AIRCRAFT_TABLE
    assert "CA" not in str(reader.requests[0]["TableName"])
    assert provider.requests[-1].tool_results[-1].data["aircraft"]["callsign"] == CALLSIGN
    assert provider.requests[-1].tool_results[-1].data["aircraft"]["on_ground"] is True
    observed = provider.requests[-1].tool_results[-1].data
    assert str(observed["aircraft_id"]).casefold() == AIRCRAFT.casefold()
    assert observed["aircraft"]["callsign"] == CALLSIGN
    assert observed["aircraft"]["on_ground"] is True
    if executed.native_result.verified_claims:
        claim = executed.native_result.verified_claims[0]
        assert claim.aircraft_id.casefold() == AIRCRAFT.casefold()
        assert claim.value is True
    assert executed.production_dispatch is False
    assert executed.identity.invocation_id == attempt.invocation_id
    assert executed.identity.planning_session_id == record.session_id

    invocation = SourceReadInvocation(
        master_request_id=attempt.master_request_id,
        planning_session_id=attempt.planning_session_id,
        step_id=attempt.step_id,
        invocation_id=attempt.invocation_id,
        tool_call_id="tool-evidence-slice",
        principal_ref=attempt.principal_ref,
        policy_revision=attempt.policy_revision,
        resource_id=attempt.resource_scope,
        operation=ReadOperation.GET_ITEM,
        correlation_id=attempt.correlation_id,
        requested_record_id=AIRCRAFT,
        expected_source_version=SOURCE_VERSION,
        expected_aircraft_id=AIRCRAFT,
    )
    receipt, assessment = host.execute_evidence(
        attempt,
        invocation,
        observation_clock_utc=OBSERVATION_CLOCK,
        evaluation_clock_utc=EVALUATION_CLOCK,
    )
    assert receipt.source_name == "aircraft-current-state"
    assert receipt.source_version == SOURCE_VERSION
    assert receipt.query_id is None
    assert receipt.records[0].record_id == AIRCRAFT
    assert receipt.records[0].aircraft_id == AIRCRAFT
    assert assessment.authenticity is SourceAuthenticity.ESTABLISHED
    assert SupportedFactualClaim.OBSERVED_SOURCE_RECORD in assessment.admitted_claims
    assert SupportedFactualClaim.GEOMETRY_INTERSECTION not in assessment.admitted_claims
    assert RejectedClaim.LOW_RISK in assessment.rejected_claims
    assert RejectedClaim.REGIONAL_TEXT_GEOGRAPHY in assessment.rejected_claims
    assert RejectedClaim.VERIFIED_ZERO not in assessment.admitted_claims
    assert MasterResponseLimitation.PRODUCTION_AUTHORITY_DENIED in assessment.ma5_limitations
    assert "divert" not in assessment.status_text.casefold()

    read_receipt = captured[0].receipt
    evidence_attempt = bind_scoped_read_to_consumed_attempt(
        attempt,
        read_receipt,
        tool_call_id="tool-slice-read",
        tool_name=TOOL,
        resource_id="aircraft",
        operation=ReadOperation.GET_ITEM,
        specialist_request_id=step.invocation.specialist_request_id,
        correlation_id=attempt.correlation_id,
        region_scope=("CA",),
    )
    trusted = LiveOpsTrustedContext(
        tables=fixtures.tables(),
        now_epoch=fixtures.NOW,
        correlation_id=attempt.correlation_id,
        max_model_turns=executed.identity.max_model_turns,
        max_tool_calls=executed.identity.max_tool_calls,
    )
    sources = __import__("wilvor_ai.master_evidence_admission", fromlist=["LiveOpsAdmissionSources"]).LiveOpsAdmissionSources(
        request=executed.bridge.request,
        context=trusted,
        result=executed.native_result,
    )
    workflow = MasterParallelContractBundle(
        context=registry.context_for(record),
        plan=plan,
        evidence_inputs=(MasterEvidenceCorroborationInput(MasterEvidenceAdmissionInput(step.invocation, sources)),),
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
                source_records=(SourceRecord(record_id="SIGMET-CA", source_version=SOURCE_VERSION, event_timestamp_utc=QUERY_CLOCK),),
                query_timestamp_utc=QUERY_CLOCK,
                freshness_status=FreshnessStatus.UNKNOWN,
                confidence=ConfidenceLevel.UNKNOWN,
                limitations=("freshness_not_established",),
                tool_call_id="tool-region-slice",
                temporal_scope=TemporalScope.CURRENT,
            ),
        ),
        as_of_utc=ACCEPTED,
        limitations=(),
        correlation_id=attempt.correlation_id,
    )
    joined = join_sprint3c_evidence_response(
        Sprint3cEvidenceResponseInput(
            authorization=step.invocation,
            consumed_attempt=evidence_attempt,
            read_receipt=read_receipt,
            native_live_ops=executed,
            live_ops_context=trusted,
            native_admission_artifact=sources,
            workflow=workflow,
            untrusted_claim=california,
            expected_aircraft_id=AIRCRAFT,
            expected_source_version=SOURCE_VERSION,
        )
    )
    assert joined.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert joined.claims_admitted is False
    assert joined.verified_zero_established is False
    assert joined.polygon_intersection_established is False
    assert joined.impacted_aircraft_count_established is False
    assert Sprint3cJoinDefect.REGIONAL_TEXT_NOT_POLYGON in joined.defects
    assert Sprint3cRejectedClaim.SIGMET_POLYGON_INTERSECTION in joined.rejected_claims
    assert joined.admission is not None
    assert MasterAdmissionReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED in joined.admission.reason_codes
    assert joined.admission.admission_status in {MasterAdmissionStatus.CONTRACT_ONLY, MasterAdmissionStatus.BINDING_UNRESOLVED}
    assert joined.response_render is not None and joined.response_render.claims_admitted is False
    assert joined.response_handoff is not None
    assert joined.response_handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert "divert" not in joined.response_render.status_text.casefold()
    assert read_receipt.outcome is ReadExecutionOutcome.COMPLETED
    assert read_receipt.record_ids == (AIRCRAFT,)


def test_historical_native_uses_the_host_grant_and_eligible_window():
    host, registry, record, plan, step, _evaluation, established, envelope, _coordination, consumed = _lineage(
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        step_id="hist-1",
        capability=HISTORY,
        tool_name="summarize_historical_encounters",
        resource_id="historical.encounters",
        operation=ReadOperation.QUERY,
        arguments=(("start_utc", PLAN[0]), ("end_utc", PLAN[1])),
        master_request="master-slice-hist",
        correlation="corr-slice-hist",
        idempotency="idem-slice-hist",
        jti="jti-slice-hist",
        deadline="2026-01-15T00:13:00Z",
        moment=HISTORY_NOW,
        now_utc=BEFORE,
        historical_window=PLAN,
        window_fields={"historical_start_utc": PLAN[0], "historical_end_utc": PLAN[1]},
    )
    assert established.principal is not None
    assert consumed is not None and consumed.claimed is True
    attempt = consumed.result.attempt
    assert attempt is not None
    assert attempt.grant_nonce != attempt.attempt_token
    bundle = MasterParallelContractBundle(context=registry.context_for(record), plan=plan, evidence_inputs=())
    descriptor = next(item for item in describe_planned_adapters(bundle).descriptors if item.step_id == "hist-1")
    binding = prepare_master_historical_binding(bundle, descriptor)
    orchestrator, authority = host.open_native_dispatch(registry, record, step.invocation, attempt, envelope, now_utc=BEFORE)
    provider = OfflineProvider([_historical_call(ELIGIBLE), _historical_claims])
    execution = execute_historical_native(
        orchestrator,
        step.invocation,
        binding,
        authority,
        now_utc=BEFORE,
        host=_historical_host(principal_ref=attempt.principal_ref, policy_revision=POLICY_REVISION),
        port=_historical_port(),
        provider=provider,
        operations=RecordingHistoricalOperations({"summarize_historical_encounters": _historical_response()}),
        tool_call_id_factory=SequentialToolCallIdFactory(),
    )
    assert execution.final_result.status is SpecialistStatus.ANSWERED
    assert execution.eligible_start_utc == ELIGIBLE[0]
    assert execution.unbound_plan_start_utc == PLAN[0]
    assert execution.plan_window_used_as_tool_arguments is False
    assert execution.read_source_versions == (READ_VERSION,)
    assert execution.native_source_versions == ()
    assert execution.source_version_authenticated is False
    assert execution.query_id_authenticated is False
    assert execution.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert execution.production_dispatch is False
    assert type(provider) is not ScriptedTestWorker


def test_historical_plan_window_and_decision_grant_stay_closed():
    host, registry, record, plan, step, _evaluation, _established, envelope, _coordination, consumed = _lineage(
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        step_id="hist-1",
        capability=HISTORY,
        tool_name="summarize_historical_encounters",
        resource_id="historical.encounters",
        operation=ReadOperation.QUERY,
        arguments=(("start_utc", PLAN[0]), ("end_utc", PLAN[1])),
        master_request="master-slice-hist-window",
        correlation="corr-slice-hist-window",
        idempotency="idem-slice-hist-window",
        jti="jti-slice-hist-window",
        deadline="2026-01-15T00:13:00Z",
        moment=HISTORY_NOW,
        now_utc=BEFORE,
        historical_window=PLAN,
        window_fields={"historical_start_utc": PLAN[0], "historical_end_utc": PLAN[1]},
    )
    attempt = consumed.result.attempt
    bundle = MasterParallelContractBundle(context=registry.context_for(record), plan=plan, evidence_inputs=())
    descriptor = next(item for item in describe_planned_adapters(bundle).descriptors if item.step_id == "hist-1")
    binding = prepare_master_historical_binding(bundle, descriptor)
    orchestrator, authority = host.open_native_dispatch(registry, record, step.invocation, attempt, envelope, now_utc=BEFORE)
    with pytest.raises(ContractValidationError, match="historical_window_not_authorized"):
        execute_historical_native(
            orchestrator,
            step.invocation,
            binding,
            authority,
            now_utc=BEFORE,
            host=_historical_host(principal_ref=attempt.principal_ref, policy_revision=POLICY_REVISION),
            port=_historical_port(),
            provider=OfflineProvider([_historical_call(PLAN)]),
            operations=RecordingHistoricalOperations({}),
        )
    decision = _lineage(
        specialist=MasterSpecialist.DECISION,
        step_id="dec-1",
        capability=RISK,
        tool_name="get_current_risk_evidence",
        resource_id="aircraft",
        operation=ReadOperation.GET_ITEM,
        arguments=(("aircraft_id", DECISION_AIRCRAFT),),
        master_request="master-slice-decision",
        correlation="corr-slice-decision",
        idempotency="idem-slice-decision",
        jti="jti-slice-decision",
        deadline=DEADLINE,
        moment=NOW,
        now_utc=NOW_TEXT,
    )
    evaluation = decision[5]
    established = decision[6]
    assert MasterRuntimePolicyDenialReason.UNRESOLVED_ROUTE_GATE in evaluation.denial_reasons
    assert established.grant_eligible is False
    assert established.principal is None
    assert established.rejection is Sprint3DRejection.POLICY_VIOLATION


def test_decision_native_keeps_independent_authorization_gates():
    harness = _decision_harness()
    step = harness.step("dec-1")
    execution = execute_decision_native(
        harness.orchestrator,
        step.invocation,
        _readiness(harness),
        _catalog(),
        harness.authority(step),
        now_utc=BEFORE,
        user_text=harness.planning().canonical_user_text,
        host=_decision_host(),
        port=_decision_port(),
        provider=_decision_provider(),
        tables=_operational(),
        source_epoch=SOURCE_EPOCH,
        query_timestamp_utc=QUERY_TIMESTAMP,
    )
    assert execution.production_dispatch is False
    assert execution.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert execution.request.recommendation_id is None
    assert execution.permitted_recommendation_id == PERMITTED_RECOMMENDATION
    assert execution.request.aircraft_id == DECISION_AIRCRAFT
    copied = _decision_harness(target_candidate_aircraft_id=COPIED_AIRCRAFT)
    copied_step = copied.step("dec-1")
    with pytest.raises(ContractValidationError, match="comparison_candidate_not_a_target"):
        execute_decision_native(
            copied.orchestrator,
            copied_step.invocation,
            _readiness(copied, typed=False),
            _catalog(),
            copied.authority(copied_step),
            now_utc=BEFORE,
            user_text=copied.planning().canonical_user_text,
            host=_decision_host(),
            port=_decision_port(),
            provider=_decision_provider(),
            tables=_operational(),
            source_epoch=SOURCE_EPOCH,
            query_timestamp_utc=QUERY_TIMESTAMP,
            binding=prepare_master_decision_binding(copied.bundle(), copied.descriptor("dec-1")),
        )
    unauthorized = Harness(_work(RISK), (_decision(),), target_candidate_recommendation_id=COPIED_RECOMMENDATION)
    unauthorized_step = unauthorized.step("dec-1")
    with pytest.raises(ContractValidationError, match="persisted_recommendation_not_separately_authorized"):
        execute_decision_native(
            unauthorized.orchestrator,
            unauthorized_step.invocation,
            _readiness(unauthorized),
            _catalog(),
            unauthorized.authority(unauthorized_step),
            now_utc=BEFORE,
            user_text=unauthorized.planning().canonical_user_text,
            host=_decision_host(permitted_recommendation_ids=frozenset({COPIED_RECOMMENDATION})),
            port=_decision_port(),
            provider=_decision_provider(),
            tables=_operational(),
            source_epoch=SOURCE_EPOCH,
            query_timestamp_utc=QUERY_TIMESTAMP,
            binding=prepare_master_decision_binding(unauthorized.bundle(), unauthorized.descriptor("dec-1")),
        )


def test_forged_tokens_principals_and_grant_replay_fail_closed():
    provider = _provider()
    assert authenticate_bearer(_token(_claims(), header={"alg": "none", "typ": "JWT", "kid": KID}, signature=b""), provider, NOW).rejection in {
        Sprint3DRejection.MALFORMED_TOKEN,
        Sprint3DRejection.WRONG_ALGORITHM,
    }
    assert authenticate_bearer(_token(_claims(), header={"alg": "RS256", "typ": "JWT", "kid": "other-kid"}), provider, NOW).rejection is Sprint3DRejection.UNREVIEWED_SIGNING_KEY
    forged = _token(_claims())
    forged = forged[:-4] + ("A" if forged[-4] != "A" else "B") + forged[-3:]
    assert authenticate_bearer(forged, provider, NOW).rejection is Sprint3DRejection.FORGED_SIGNATURE
    assert authenticate_bearer(_token(_claims(iss="https://other.example/realms/ops")), provider, NOW).rejection is Sprint3DRejection.ISSUER_MISMATCH
    assert authenticate_bearer(_token(_claims(aud="other-app")), provider, NOW).rejection is Sprint3DRejection.AUDIENCE_MISMATCH
    assert authenticate_bearer(_token(_claims(scope="other.read")), provider, NOW).rejection is Sprint3DRejection.SCOPE_MISMATCH
    issued = int(NOW.timestamp())
    assert authenticate_bearer(_token(_claims(iat=issued - 120, nbf=issued - 120, exp=issued - 1)), provider, NOW).rejection is Sprint3DRejection.TOKEN_EXPIRED
    event = {"requestContext": {"authorizer": {"jwt": {"claims": {"sub": SUBJECT, "scope": "ai.query.read"}}}}}
    assert authenticate_bearer(str(event), provider, NOW).authenticated is False

    host, _registry, record, _plan, _step, evaluation, established, envelope, coordination, consumed = _live(
        master_request="master-slice-replay",
        correlation="corr-slice-replay",
        idempotency="idem-slice-replay",
        jti="jti-slice-replay",
    )
    attempt = consumed.result.attempt
    replay = host.consume_verified_grant(
        coordination,
        host.presented_binding(established.principal, envelope),
        envelope,
        principal=established.principal,
        owner_id=OWNER,
        now_utc=NOW_TEXT,
    )
    assert replay.replayed is True
    assert replay.claimed is False
    assert replay.result.attempt.attempt_token == attempt.attempt_token
    assert replay.result.attempt.grant_nonce == attempt.grant_nonce
    assert replay.result.outcome is MasterExecutionLedgerOutcome.REPLAYED
    other = host.presented_binding(established.principal, envelope)
    swapped = type(other)(**{**other.__dict__, "planning_session_id": "other-session-slice"})
    cross = host.consume_verified_grant(
        coordination,
        swapped,
        envelope,
        principal=established.principal,
        owner_id=OWNER,
        now_utc=NOW_TEXT,
    )
    assert cross.claimed is False
    assert cross.rejection in {Sprint3DRejection.SESSION_MISMATCH.value, "session_mismatch"}
    forged_principal = authenticate_bearer(_token(_claims(sub="intruder-9", jti="jti-intruder")), provider, NOW)
    intruder = establish_trusted_principal(
        forged_principal,
        evaluation,
        original_planning_session_id=record.session_id,
        now=NOW,
    )
    assert intruder.principal is None or intruder.principal.verified_subject != established.principal.verified_subject
    if intruder.principal is not None:
        rejected = host.issue_grant(intruder, envelope, now=NOW)
        assert rejected[0] is None


def test_concurrent_consume_stale_fence_and_lease_do_not_start_another_attempt():
    ledger = LedgerFake()
    host, _registry, _record, _plan, _step, _evaluation, established, envelope, coordination, first = _live(
        ledger=ledger,
        master_request="master-slice-race",
        correlation="corr-slice-race",
        idempotency="idem-slice-race",
        jti="jti-slice-race",
    )
    presented = host.presented_binding(established.principal, envelope)
    outcomes = []

    def consume(token: str) -> None:
        outcomes.append(
            host.consume_verified_grant(
                coordination,
                presented,
                envelope,
                principal=established.principal,
                owner_id=OWNER,
                now_utc=NOW_TEXT,
                attempt_token=token,
            )
        )

    threads = [threading.Thread(target=consume, args=(f"attempt-race-{index}",)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert first.claimed is True
    assert sum(item.claimed for item in outcomes) == 0
    assert all(item.replayed for item in outcomes)
    started = [item for item in ledger.items.values() if item.get("state", {}).get("S") == "STARTED"]
    assert len(started) == 1
    assert started[0]["repeat_non_idempotent_after_expiry"]["BOOL"] is False
    stale = host.current_fence(
        host.ledger_envelope(first.result.attempt.invocation_id),
        owner_id=OWNER,
        attempt_token=first.result.attempt.attempt_token,
        fence_epoch=first.result.attempt.fence_epoch + 1,
        grant_nonce=first.result.attempt.grant_nonce,
    )
    assert stale.matches_current_attempt is False
    late = host.consume_verified_grant(
        coordination,
        presented,
        envelope,
        principal=established.principal,
        owner_id=OWNER,
        now_utc="2026-01-15T00:04:30Z",
    )
    assert late.replayed is True
    assert late.result.attempt.grant_nonce == first.result.attempt.grant_nonce
    assert len([item for item in ledger.items.values() if item.get("state", {}).get("S") == "STARTED"]) == 1


def test_reads_adapters_and_false_aviation_claims_fail_closed():
    reader = ReadFake()
    ledger = LedgerFake()
    _seed(reader)
    host, _registry, _record, _plan, _step, _evaluation, _established, envelope, _coordination, consumed = _live(
        ledger=ledger,
        reader=reader,
        master_request="master-slice-reads",
        correlation="corr-slice-reads",
        idempotency="idem-slice-reads",
        jti="jti-slice-reads",
    )
    attempt = consumed.result.attempt
    scope = _scope(attempt.principal_ref)
    before = len(reader.operations)
    scan = host.execute_server_read(
        attempt,
        UntrustedReadRequest(
            master_request_id=attempt.master_request_id,
            planning_session_id=attempt.planning_session_id,
            step_id=attempt.step_id,
            invocation_id=attempt.invocation_id,
            specialist=attempt.specialist,
            purpose=attempt.purpose,
            capability=attempt.capability,
            tool_name=TOOL,
            tool_call_id="tool-scan",
            resource_id="aircraft",
            operation=ReadOperation.SCAN,
            requested_arguments=(),
            correlation_id=attempt.correlation_id,
        ),
        scope,
        tool_call_id="tool-scan",
    )
    sql = host.execute_server_read(
        attempt,
        UntrustedReadRequest(
            master_request_id=attempt.master_request_id,
            planning_session_id=attempt.planning_session_id,
            step_id=attempt.step_id,
            invocation_id=attempt.invocation_id,
            specialist=attempt.specialist,
            purpose=attempt.purpose,
            capability=attempt.capability,
            tool_name=TOOL,
            tool_call_id="tool-sql",
            resource_id="aircraft",
            operation=ReadOperation.GET_ITEM,
            requested_arguments=(("sql", "select * from aircraft"),),
            correlation_id=attempt.correlation_id,
        ),
        scope,
        tool_call_id="tool-sql",
    )
    write = host.execute_server_read(
        attempt,
        UntrustedReadRequest(
            master_request_id=attempt.master_request_id,
            planning_session_id=attempt.planning_session_id,
            step_id=attempt.step_id,
            invocation_id=attempt.invocation_id,
            specialist=attempt.specialist,
            purpose=attempt.purpose,
            capability=attempt.capability,
            tool_name="put_item",
            tool_call_id="tool-write",
            resource_id="aircraft",
            operation=ReadOperation.GET_ITEM,
            requested_arguments=(("aircraft_id", AIRCRAFT),),
            correlation_id=attempt.correlation_id,
        ),
        scope,
        tool_call_id="tool-write",
    )
    foreign = host.execute_server_read(
        attempt,
        UntrustedReadRequest(
            master_request_id=attempt.master_request_id,
            planning_session_id=attempt.planning_session_id,
            step_id=attempt.step_id,
            invocation_id=attempt.invocation_id,
            specialist=attempt.specialist,
            purpose=attempt.purpose,
            capability=attempt.capability,
            tool_name=TOOL,
            tool_call_id="tool-foreign",
            resource_id="caller.table",
            operation=ReadOperation.GET_ITEM,
            requested_arguments=(("aircraft_id", AIRCRAFT),),
            caller_scope={"region": "eu-west-1"},
            correlation_id=attempt.correlation_id,
        ),
        scope,
        tool_call_id="tool-foreign",
    )
    assert scan.refusal is AwsExecutionRefusal.SCAN_NOT_PERMITTED
    assert sql.refusal is AwsExecutionRefusal.ARBITRARY_SQL
    assert write.refusal is AwsExecutionRefusal.WRITE_NOT_PERMITTED
    assert foreign.refusal is AwsExecutionRefusal.CALLER_SCOPE_NOT_AUTHORITY
    assert len(reader.operations) == before
    with pytest.raises(ContractValidationError):
        host._read_client.scan(TableName=AIRCRAFT_TABLE)
    with pytest.raises(ContractValidationError, match="caller_adapter_rejected"):
        host.execute_evidence(
            attempt,
            SourceReadInvocation(
                master_request_id=attempt.master_request_id,
                planning_session_id=attempt.planning_session_id,
                step_id=attempt.step_id,
                invocation_id=attempt.invocation_id,
                tool_call_id="tool-caller-adapter",
                principal_ref=attempt.principal_ref,
                policy_revision=attempt.policy_revision,
                resource_id=attempt.resource_scope,
                operation=ReadOperation.GET_ITEM,
                correlation_id=attempt.correlation_id,
                requested_record_id=AIRCRAFT,
            ),
            observation_clock_utc=OBSERVATION_CLOCK,
            evaluation_clock_utc=EVALUATION_CLOCK,
            caller_execute=lambda _invocation: None,
        )
    _receipt, assessment = host.execute_evidence(
        attempt,
        SourceReadInvocation(
            master_request_id=attempt.master_request_id,
            planning_session_id=attempt.planning_session_id,
            step_id=attempt.step_id,
            invocation_id=attempt.invocation_id,
            tool_call_id="tool-evidence-clocks",
            principal_ref=attempt.principal_ref,
            policy_revision=attempt.policy_revision,
            resource_id=attempt.resource_scope,
            operation=ReadOperation.GET_ITEM,
            correlation_id=attempt.correlation_id,
            requested_record_id=AIRCRAFT,
            expected_source_version="sv-other",
        ),
        observation_clock_utc=ACCEPTED,
        evaluation_clock_utc=ACCEPTED,
    )
    assert assessment.factual_claims_admitted is False
    assert SupportedFactualClaim.OBSERVED_SOURCE_RECORD not in assessment.admitted_claims
    other_host = SourceEvidenceHost(type("Clock", (), {"now_utc": staticmethod(lambda: OBSERVATION_CLOCK)})())
    forged = evaluate_source_evidence(other_host, _receipt)
    assert forged.verdict.value == "FORGED"
    with pytest.raises(ContractValidationError, match="invocation_mismatch"):
        host.execute_evidence(
            attempt,
            SourceReadInvocation(
                master_request_id=attempt.master_request_id,
                planning_session_id=attempt.planning_session_id,
                step_id=attempt.step_id,
                invocation_id="other-invocation-slice",
                tool_call_id="tool-other-invocation",
                principal_ref=attempt.principal_ref,
                policy_revision=attempt.policy_revision,
                resource_id=attempt.resource_scope,
                operation=ReadOperation.GET_ITEM,
                correlation_id=attempt.correlation_id,
                requested_record_id=AIRCRAFT,
            ),
            observation_clock_utc=OBSERVATION_CLOCK,
            evaluation_clock_utc=EVALUATION_CLOCK,
        )


def test_empty_truncated_throttled_and_denied_reads_fail_closed():
    reader = ReadFake()
    host, _registry, _record, _plan, _step, _evaluation, _established, _envelope, _coordination, consumed = _live(
        reader=reader,
        master_request="master-slice-empty",
        correlation="corr-slice-empty",
        idempotency="idem-slice-empty",
        jti="jti-slice-empty",
    )
    attempt = consumed.result.attempt
    _receipt, empty = host.execute_evidence(
        attempt,
        SourceReadInvocation(
            master_request_id=attempt.master_request_id,
            planning_session_id=attempt.planning_session_id,
            step_id=attempt.step_id,
            invocation_id=attempt.invocation_id,
            tool_call_id="tool-empty",
            principal_ref=attempt.principal_ref,
            policy_revision=attempt.policy_revision,
            resource_id=attempt.resource_scope,
            operation=ReadOperation.GET_ITEM,
            correlation_id=attempt.correlation_id,
            requested_record_id=AIRCRAFT,
        ),
        observation_clock_utc=OBSERVATION_CLOCK,
        evaluation_clock_utc=EVALUATION_CLOCK,
    )
    assert empty.factual_claims_admitted is False
    assert RejectedClaim.VERIFIED_ZERO in empty.rejected_claims
    assert empty.low_risk_admitted is False
    assert empty.verified_zero_established is False
    assert SupportedFactualClaim.GEOMETRY_INTERSECTION not in empty.admitted_claims

    reader.items[(AIRCRAFT_TABLE, AIRCRAFT)] = {
        "aircraft_id": {"S": AIRCRAFT},
        "source_version": {"S": SOURCE_VERSION},
        "source_observed_at_utc": {"S": QUERY_CLOCK},
        "freshness": {"S": "FRESH"},
        "claimed_polygon_intersection": {"BOOL": True},
        "claimed_impacted_aircraft_count": {"N": "4"},
        "claimed_risk_level": {"S": "LOW"},
    }
    original = reader.get_item

    def truncated(**kwargs):
        response = original(**kwargs)
        response["LastEvaluatedKey"] = {"aircraft_id": {"S": AIRCRAFT}}
        response["query_id"] = "caller-query"
        return response

    reader.get_item = truncated
    _page, partial = host.execute_evidence(
        attempt,
        SourceReadInvocation(
            master_request_id=attempt.master_request_id,
            planning_session_id=attempt.planning_session_id,
            step_id=attempt.step_id,
            invocation_id=attempt.invocation_id,
            tool_call_id="tool-truncated",
            principal_ref=attempt.principal_ref,
            policy_revision=attempt.policy_revision,
            resource_id=attempt.resource_scope,
            operation=ReadOperation.GET_ITEM,
            correlation_id=attempt.correlation_id,
            requested_record_id=AIRCRAFT,
            expected_source_version=SOURCE_VERSION,
            expected_aircraft_id=AIRCRAFT,
        ),
        observation_clock_utc=OBSERVATION_CLOCK,
        evaluation_clock_utc=EVALUATION_CLOCK,
    )
    assert partial.factual_claims_admitted is False
    assert RejectedClaim.VERIFIED_ZERO not in partial.admitted_claims
    assert SupportedFactualClaim.GEOMETRY_INTERSECTION not in partial.admitted_claims

    throttled = LedgerFake()
    throttled.throttle_remaining = 5
    with pytest.raises(Exception):
        _live(
            ledger=throttled,
            master_request="master-slice-throttle",
            correlation="corr-slice-throttle",
            idempotency="idem-slice-throttle",
            jti="jti-slice-throttle",
        )
    assert not any(item.get("state", {}).get("S") == "STARTED" for item in throttled.items.values())

    class Denied:
        def get_item(self, **_kwargs):
            raise RuntimeError("AccessDenied")

        def put_item(self, **_kwargs):
            raise RuntimeError("AccessDenied")

        def transact_write_items(self, **_kwargs):
            raise RuntimeError("AccessDenied")

        def query(self, **_kwargs):
            raise RuntimeError("AccessDenied")

    with pytest.raises(Exception):
        _live(
            ledger=Denied(),
            master_request="master-slice-denied",
            correlation="corr-slice-denied",
            idempotency="idem-slice-denied",
            jti="jti-slice-denied",
        )


def test_cancellation_stops_before_native_execution():
    reader = ReadFake()
    _seed(reader)
    host, registry, record, plan, step, _evaluation, _established, envelope, _coordination, consumed = _live(
        reader=reader,
        master_request="master-slice-cancel",
        correlation="corr-slice-cancel",
        idempotency="idem-slice-cancel",
        jti="jti-slice-cancel",
    )
    attempt = consumed.result.attempt
    bundle = MasterParallelContractBundle(context=registry.context_for(record), plan=plan, evidence_inputs=())
    descriptor = next(item for item in describe_planned_adapters(bundle).descriptors if item.step_id == "live-1")
    binding = prepare_master_live_ops_binding(bundle, descriptor)
    provider = SliceProvider(AIRCRAFT)
    orchestrator, authority = host.open_native_dispatch(registry, record, step.invocation, attempt, envelope, now_utc=NOW_TEXT)
    with pytest.raises(ContractValidationError, match="schedule_cancelled"):
        orchestrator.execute_live_ops(
            step.invocation,
            binding,
            authority,
            Sprint3CLiveOpsRuntimeInjection(
                provider=provider,
                tables=fixtures.tables(),
                now_epoch=fixtures.NOW,
                cancelled=lambda: True,
            ),
            now_utc=NOW_TEXT,
        )
    assert provider.requests == []
    with pytest.raises(ContractValidationError, match="model_origin_dispatch_rejected"):
        Sprint3CLiveOpsRuntimeInjection(
            provider=ScriptedTestWorker(),
            tables=fixtures.tables(),
            now_epoch=fixtures.NOW,
        )


def test_validation_hooks_stay_unverified_and_production_execution_stays_closed():
    hooks = locate_sprint3d_hooks()
    assert any(item.status == "ABSENT" for item in hooks)
    assert all(item.status != "VERIFIED" for item in hooks)
    assert any(item.status == "PRESENT_UNVERIFIED" for item in hooks)
    assert any(item.interface_id == "I3D-10" and item.status == "ABSENT" for item in hooks)
    mapped = {item[0]: item[2] for item in INTEGRATION_HOOK_MAP}
    assert mapped["I3D-10"] == "ABSENT"
    assert mapped["I3D-2"] == "BLOCKED"
    with pytest.raises(ContractValidationError, match="production_execution_closed"):
        open_production_aws_execution()
    assert live_network_gate_open() is False


def test_production_consume_request_does_not_write():
    host, _registry, _record, _plan, _step, _evaluation, established, envelope, coordination, consumed = _live(
        master_request="master-slice-prod",
        correlation="corr-slice-prod",
        idempotency="idem-slice-prod",
        jti="jti-slice-prod",
    )
    assert consumed.claimed is True
    refused = host.consume_verified_grant(
        coordination,
        host.presented_binding(established.principal, envelope),
        envelope,
        principal=established.principal,
        owner_id=OWNER,
        now_utc=NOW_TEXT,
        production_requested=True,
    )
    assert refused.claimed is False
    assert refused.rejection == "production_execution_closed"
    assert refused.data_plane_invoked is False
