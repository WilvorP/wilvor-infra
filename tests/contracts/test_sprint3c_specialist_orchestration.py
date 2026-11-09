"""Sprint 3C Agent D: native specialist orchestration after consumed grant/fence.

Offline only. Injected providers and fake tables. No AWS, Terraform, or model APIs.
"""

from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.fixtures import live_ops_domain_fixtures as fixtures
from wilvor_ai.contracts import AgentCapability, ContractValidationError, TemporalScope
from wilvor_ai.decision_specialist import DecisionSpecialist
from wilvor_ai.decision_specialist_runtime_contracts import DecisionSpecialistRequest
from wilvor_ai.decision_tools import DecisionToolsRuntime
from wilvor_ai.historical_answer_renderer import finalize_historical_specialist_run
from wilvor_ai.historical_specialist import HistoricalAnalyticsSpecialist
from wilvor_ai.live_ops_claims import (
    LiveOpsCountMetric,
    LiveOpsCountMode,
    LiveOpsExecutedScope,
    ScopedCountClaim,
)
from wilvor_ai.live_ops_model_contracts import LiveOpsModelDecision, LiveOpsModelDecisionKind
from wilvor_ai.live_ops_specialist import LiveOpsSpecialist
from wilvor_ai.master_authorized_scheduler_adapter import (
    PRODUCTION_DISPATCH_ENABLED,
    MasterAuthorizedSchedulerAdapter,
    ScriptedTestWorker,
    ScriptedWorkerObservation,
    ScriptedWorkerReport,
    open_nonproduction_scheduler_seal,
)
from wilvor_ai.live_ops_public_contracts import (
    LiveOpsDiscovery,
    LiveOpsDiscoveryStatus,
    LiveOpsEntityHint,
    LiveOpsPublicVerificationOutcome,
    LiveOpsRunRequest,
    LiveOpsRunResult,
    LiveOpsRunStatus,
    LiveOpsTrustedContext,
)
from wilvor_ai.master_contracts import (
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
    observe_callsign,
    observe_population,
    observe_typed_aircraft_id,
)
from wilvor_ai.master_decision_execution_readiness import (
    assess_master_decision_execution_readiness,
)
from wilvor_ai.master_decision_target_resolver import (
    REQUIRED_HOST_PROOFS,
    DecisionResolutionDefect,
    DecisionResolutionStatus,
    FakeAircraftRead,
    FakeDecisionReadCatalog,
    FakeRiskRecord,
    HostProofRequirement,
)
from wilvor_ai.decision_contracts import RiskPresence, StoredRiskLevel
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_execution_grants import (
    GrantEvaluationClock,
    GrantLifecycle,
    LocalHarnessConsumptionLedger,
    LocalHarnessGrantIssuer,
    LogicalReadOperation,
    MasterExecutionGrantEnvelope,
    evaluate_grant_envelope,
    issue_production_execution_grant,
)
from wilvor_ai.master_execution_ledger import (
    InMemoryMasterExecutionLedgerStore,
    MasterExecutionLedger,
    MasterExecutionLedgerEnvelope,
    MasterExecutionLedgerOutcome,
    MasterExecutionLedgerState,
)
from wilvor_ai.master_historical_binding import prepare_master_historical_binding
from wilvor_ai.master_historical_native_bridge import HISTORICAL_FINALIZATION_ENTRYPOINT
from wilvor_ai.master_host_scheduler_integration import issued_grant_bindings
from wilvor_ai.master_live_ops_binding import prepare_master_live_ops_binding
from wilvor_ai.master_live_ops_native_bridge import MasterLiveOpsNativeResult
from wilvor_ai.master_parallel_contracts import MasterParallelContractBundle
from wilvor_ai.master_planning_session import MasterPlanningSessionRegistry
from wilvor_ai.master_read_resource_guard import server_read_budget, server_read_resources
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRouteExecutionAuthority,
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
)
from wilvor_ai.master_specialist_adapters import describe_planned_adapters
from wilvor_ai.master_specialist_runtime_contract import (
    construct_decision_native_request,
    invoke_specialist,
)
from wilvor_ai.model_contracts import ProposedToolCall
from wilvor_ai.specialist_contracts import HistoricalSpecialistTrustedContext, SpecialistRequest
from wilvor_ai.sprint3c_specialist_orchestration import (
    NATIVE_EXECUTION_IS_SOURCE_EVIDENCE,
    NATIVE_OFFLINE_EXECUTION_IS_PRODUCTION_DISPATCH,
    NATIVE_PRODUCTION_DISPATCH_AVAILABLE,
    NATIVE_SPECIALIST_SIGNATURES,
    SCRIPTED_WORKER_IS_NATIVE_EXECUTION,
    SPRINT3C_SPECIALIST_ORCHESTRATION_SCHEMA_VERSION,
    Sprint3CConsumedDispatchAuthority,
    Sprint3CLiveOpsRuntimeInjection,
    Sprint3CNativeSpecialistOrchestrator,
    assert_production_dispatch_closed,
    inspect_native_specialist_signatures,
    refuse_decision_native_request,
    refuse_historical_finalization,
    refuse_scripted_worker_as_native,
)
from wilvor_ai import live_ops as live_ops_module


TEXT = "Which aircraft are currently affected by hazards, and what recorded risks apply?"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
BEFORE = "2026-01-15T00:10:00Z"
DEADLINE = "2026-01-15T00:30:00Z"
EXPIRES = "2026-01-15T00:30:00Z"
ACCEPTED = "2026-01-15T00:00:01Z"
GRANT_NOW = datetime(2026, 1, 15, 0, 10, tzinfo=timezone.utc)
OWNER = "ledger-owner-sprint3c"
PRINCIPAL = "principal-sprint3c"
POLICY = "policy-rev-sprint3c"
AIRCRAFT = "AC-1"
OTHER = "AC-2"
MODULE = (
    Path(__file__).resolve().parents[2]
    / "functions"
    / "shared"
    / "wilvor_ai"
    / "sprint3c_specialist_orchestration.py"
)
TOOLS = {
    MasterSpecialist.LIVE_OPS: "get_aircraft_operational_context",
    MasterSpecialist.HISTORICAL_ANALYTICS: "summarize_historical_encounters",
    MasterSpecialist.DECISION: "get_current_risk_evidence",
}
TEMPORAL = {
    MasterSpecialist.LIVE_OPS: (TemporalScope.CURRENT,),
    MasterSpecialist.HISTORICAL_ANALYTICS: (TemporalScope.HISTORICAL,),
    MasterSpecialist.DECISION: (TemporalScope.CURRENT,),
}
_TOOL_NAMES = (
    "search_current_hazards",
    "search_current_impacts",
    "search_current_encounters",
    "get_observed_network_state",
    "get_aircraft_operational_context",
    "get_hazard_operational_context",
    "get_airport_operational_context",
    "find_aircraft_by_callsign",
)


class FakeProvider:
    """Deterministic injected provider. Not a vendor client."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.requests = []

    def complete(self, turn):
        self.requests.append(turn)
        if not self.steps:
            raise RuntimeError("script exhausted")
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step(turn)
        return step


class RecordingFakeHandlers(dict):
    """Fake-table handlers. Success is not source authentication."""

    def __init__(self, log):
        super().__init__()
        self.log = log
        for name in _TOOL_NAMES:
            self[name] = self._wrap(getattr(live_ops_module, name), name)

    def _wrap(self, function, name):
        log = self.log

        def handler(call, **kwargs):
            log.append((name, dict(kwargs)))
            return function(call, **kwargs)

        return handler


def _ids():
    state = {"n": 0}

    def factory():
        state["n"] += 1
        return "tool-" + str(state["n"])

    return factory


def _tools(*calls):
    proposed = tuple(
        item if isinstance(item, ProposedToolCall) else ProposedToolCall(item[0], item[1])
        for item in calls
    )
    return LiveOpsModelDecision(kind=LiveOpsModelDecisionKind.TOOL_CALLS, tool_calls=proposed)


def _claims(*items):
    return LiveOpsModelDecision(kind=LiveOpsModelDecisionKind.FINAL_CLAIMS, claims=tuple(items))


def _network_claim(projection):
    return ScopedCountClaim(
        tool_call_id=projection.tool_call_id,
        tool_name=projection.tool_name,
        metric=LiveOpsCountMetric.NETWORK_AIRCRAFT_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=projection.data["current_aircraft_count"],
        scope=LiveOpsExecutedScope(),
    )


def _network_from_turn(turn):
    return _claims(_network_claim(turn.tool_results[-1]))


def _context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-sprint3c-1",
        "correlation_id": "corr-sprint3c-1",
        "application_request_time_utc": ACCEPTED,
        "canonical_user_text": TEXT,
    }
    values.update(overrides)
    return MasterPlanningContext(**values)  # type: ignore[arg-type]


def _work(*capabilities: AgentCapability) -> tuple[MasterProposedCapability, ...]:
    return tuple(MasterProposedCapability(item, MasterRequirement.REQUIRED) for item in capabilities)


def _step(
    step_id: str,
    specialist: MasterSpecialist,
    capability: AgentCapability,
    **overrides: object,
) -> MasterProposedStep:
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
    return _step(
        step_id,
        MasterSpecialist.LIVE_OPS,
        LIVE,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
        **overrides,
    )


def _history(step_id: str = "hist-1", **overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "temporal_intent": MasterRouteTemporalIntent.HISTORICAL,
        "historical_start_utc": "2026-01-01T00:00:00Z",
        "historical_end_utc": "2026-01-02T00:00:00Z",
    }
    values.update(overrides)
    return _step(step_id, MasterSpecialist.HISTORICAL_ANALYTICS, HISTORY, **values)


def _decision(step_id: str = "dec-1", **overrides: object) -> MasterProposedStep:
    return _step(
        step_id,
        MasterSpecialist.DECISION,
        RISK,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
        **overrides,
    )


def _scope(specialist: MasterSpecialist, tool_name: str) -> tuple[LogicalReadOperation, ...]:
    pairs = sorted(server_read_resources(specialist, tool_name), key=lambda item: (item[0], item[1].value))
    return tuple(LogicalReadOperation(resource_id, operation) for resource_id, operation in pairs)


class Harness:
    def __init__(
        self,
        capabilities: tuple[MasterProposedCapability, ...],
        steps: tuple[MasterProposedStep, ...],
        **context_overrides: object,
    ) -> None:
        self.registry = MasterPlanningSessionRegistry()
        proposal = MasterRouteProposal(user_text=TEXT, capabilities=capabilities, steps=steps)
        self.record = self.registry.open_session(proposal, _context(**context_overrides))
        decision = self.registry.decision_for(self.record)
        assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
        assert decision.plan is not None
        self.plan = decision.plan
        self.clock = GrantEvaluationClock(GRANT_NOW)
        self.issuer = LocalHarnessGrantIssuer(self.clock)
        self.consumption = LocalHarnessConsumptionLedger()
        self.execution = MasterExecutionLedger(InMemoryMasterExecutionLedgerStore())
        self.principal = self.issuer.seal_principal(PRINCIPAL)
        self.seal = open_nonproduction_scheduler_seal()
        self.adapter = MasterAuthorizedSchedulerAdapter(
            self.registry,
            self.record,
            deadline_utc=DEADLINE,
            integration_seal=self.seal,
        )
        self.orchestrator = Sprint3CNativeSpecialistOrchestrator(
            self.adapter,
            issuer=self.issuer,
            execution_ledger=self.execution,
        )

    def planning(self) -> MasterPlanningContext:
        return self.registry.context_for(self.record)

    def bundle(self) -> MasterParallelContractBundle:
        return MasterParallelContractBundle(
            context=self.planning(),
            plan=self.plan,
            evidence_inputs=(),
        )

    def step(self, step_id: str):
        return next(item for item in self.plan.steps if item.step_id == step_id)

    def descriptor(self, step_id: str):
        described = describe_planned_adapters(self.bundle())
        matches = [item for item in described.descriptors if item.step_id == step_id]
        assert len(matches) == 1
        return matches[0]

    def grant_envelope(self, step) -> MasterExecutionGrantEnvelope:
        context = self.adapter.bounded_context(step.invocation, now_utc=BEFORE)
        specialist = step.invocation.specialist
        _executions, turns, calls = server_read_budget(specialist)
        tool_name = TOOLS[specialist]
        return MasterExecutionGrantEnvelope(
            master_request_id=context.master_request_id,
            planning_session_id=context.planning_session_id,
            step_id=context.step_id,
            invocation_id=context.invocation_id,
            specialist=context.specialist,
            purpose=context.purpose,
            capability=context.capability,
            principal_ref=PRINCIPAL,
            policy_revision=POLICY,
            tool_name=tool_name,
            resource_scope=_scope(specialist, tool_name),
            region_scope=("CA",),
            temporal_scopes=TEMPORAL[specialist],
            historical_window=context.historical_window,
            decision_target_scope=context.decision_target_scope,
            accepted_at_utc=context.accepted_at_utc,
            deadline_utc=context.deadline_utc,
            expires_at_utc=EXPIRES,
            idempotency_key=f"grant-{context.planning_session_id}-{context.step_id}",
            max_model_turns=turns,
            max_tool_calls=calls,
            correlation_id=context.correlation_id,
        )

    def ledger_envelope(self, step) -> MasterExecutionLedgerEnvelope:
        context = self.adapter.bounded_context(step.invocation, now_utc=BEFORE)
        _executions, turns, calls = server_read_budget(step.invocation.specialist)
        target = context.decision_target_scope if context.decision_target_scope is not None else "not-a-decision-target"
        values: dict[str, object] = {
            "master_request_id": context.master_request_id,
            "planning_session_id": context.planning_session_id,
            "step_id": context.step_id,
            "invocation_id": context.invocation_id,
            "specialist": context.specialist,
            "purpose": context.purpose,
            "capability": context.capability,
            "principal_ref": PRINCIPAL,
            "policy_revision": POLICY,
            "resource_scope": "keyed-read-label",
            "region_scope": "not-geographic-verification",
            "decision_target_scope": target,
            "accepted_at_utc": context.accepted_at_utc,
            "deadline_utc": context.deadline_utc,
            "idempotency_key": f"ledger-{context.planning_session_id}-{context.step_id}",
            "max_model_turns": turns,
            "max_tool_calls": calls,
            "correlation_id": context.correlation_id,
        }
        if context.historical_window is not None:
            values["historical_start_utc"] = context.historical_window[0]
            values["historical_end_utc"] = context.historical_window[1]
        return MasterExecutionLedgerEnvelope(**values)  # type: ignore[arg-type]

    def consume(self, envelope: MasterExecutionGrantEnvelope, *, consume: bool = True):
        evaluation = evaluate_grant_envelope(envelope)
        assert evaluation.bounds_accepted is True
        grant = self.issuer.issue(self.issuer.prepare(envelope, self.principal))
        if consume:
            consumption = self.issuer.consume(
                grant,
                self.consumption,
                **issued_grant_bindings(envelope, self.principal),
            )
            assert consumption.local_harness_consumption_recorded is True
            assert consumption.dispatch_permitted is False
            assert consumption.lifecycle is GrantLifecycle.CONSUMED_LOCAL_HARNESS
        return grant

    def start(self, envelope: MasterExecutionLedgerEnvelope) -> tuple[str, int, str]:
        reserved = self.execution.reserve_once(envelope, owner_id=OWNER, now_utc=BEFORE, lease_seconds=60)
        assert reserved.outcome is MasterExecutionLedgerOutcome.CLAIMED
        assert reserved.state is MasterExecutionLedgerState.RESERVED
        assert reserved.attempt_token is not None
        assert reserved.fence_epoch is not None
        started = self.execution.mark_started(
            envelope,
            owner_id=OWNER,
            attempt_token=reserved.attempt_token,
            fence_epoch=reserved.fence_epoch,
            now_utc=BEFORE,
        )
        assert started.outcome is MasterExecutionLedgerOutcome.CLAIMED
        assert started.state is MasterExecutionLedgerState.STARTED
        assert started.specialist_executed is False
        assert isinstance(started.grant_nonce, str)
        return reserved.attempt_token, reserved.fence_epoch, started.grant_nonce

    def authority(self, step, *, consume: bool = True, start: bool = True, **fence_overrides: object):
        grant_envelope = self.grant_envelope(step)
        grant = self.consume(grant_envelope, consume=consume)
        ledger_envelope = self.ledger_envelope(step)
        if start:
            token, fence, nonce = self.start(ledger_envelope)
        else:
            reserved = self.execution.reserve_once(
                ledger_envelope,
                owner_id=OWNER,
                now_utc=BEFORE,
                lease_seconds=60,
            )
            assert reserved.attempt_token is not None and reserved.fence_epoch is not None
            token, fence, nonce = reserved.attempt_token, reserved.fence_epoch, reserved.grant_nonce or "missing-nonce"
        values: dict[str, object] = {
            "grant": grant,
            "owner_id": OWNER,
            "attempt_token": token,
            "fence_epoch": fence,
            "grant_nonce": nonce,
            "principal": self.principal,
            "grant_envelope": grant_envelope,
            "ledger_envelope": ledger_envelope,
        }
        values.update(fence_overrides)
        return Sprint3CConsumedDispatchAuthority(**values)  # type: ignore[arg-type]


def _runtime(provider, log=None, **overrides: object) -> Sprint3CLiveOpsRuntimeInjection:
    if log is None:
        log = []
    values: dict[str, object] = {
        "provider": provider,
        "tables": fixtures.california_tables(),
        "now_epoch": fixtures.NOW,
        "handlers": RecordingFakeHandlers(log),
        "tool_call_id_factory": _ids(),
    }
    values.update(overrides)
    return Sprint3CLiveOpsRuntimeInjection(**values)  # type: ignore[arg-type]


def _live_harness() -> tuple[Harness, object, object]:
    harness = Harness(_work(LIVE), (_live(),))
    step = harness.step("live-1")
    binding = prepare_master_live_ops_binding(harness.bundle(), harness.descriptor("live-1"))
    return harness, step, binding


def test_native_signatures_are_read_from_code_not_guessed():
    catalog = inspect_native_specialist_signatures()
    assert catalog == NATIVE_SPECIALIST_SIGNATURES
    assert catalog.live_ops_run_parameters == ("request", "context")
    assert catalog.historical_run_parameters == ("request", "context")
    assert catalog.decision_run_parameters == ("request", "runtime")
    assert catalog.live_ops_request_type == "LiveOpsRunRequest"
    assert catalog.live_ops_context_type == "LiveOpsTrustedContext"
    assert catalog.historical_request_type == "SpecialistRequest"
    assert catalog.historical_context_type == "HistoricalSpecialistTrustedContext"
    assert catalog.decision_request_type == "DecisionSpecialistRequest"
    assert catalog.decision_runtime_type == "DecisionToolsRuntime"
    assert catalog.historical_finalization_entrypoint == HISTORICAL_FINALIZATION_ENTRYPOINT
    assert catalog.required_decision_proofs == (
        HostProofRequirement.SOURCE_CURRENTNESS,
        HostProofRequirement.ALLOWED_TARGET_POPULATION,
        HostProofRequirement.SEPARATE_PERSISTED_RECOMMENDATION_AUTHORIZATION,
    )
    live_hints = inspect.signature(LiveOpsSpecialist.run)
    assert tuple(name for name in live_hints.parameters if name != "self") == ("request", "context")
    historical_hints = inspect.signature(HistoricalAnalyticsSpecialist.run)
    assert tuple(name for name in historical_hints.parameters if name != "self") == ("request", "context")
    decision_hints = inspect.signature(DecisionSpecialist.run)
    assert tuple(name for name in decision_hints.parameters if name != "self") == ("request", "runtime")
    assert inspect.signature(finalize_historical_specialist_run).parameters["run_result"].annotation in {
        "HistoricalSpecialistRunResult",
        inspect.signature(finalize_historical_specialist_run).parameters["run_result"].annotation,
    }


def test_cp0_production_dispatch_and_closed_entries_stay_closed():
    assert PRODUCTION_DISPATCH_ENABLED is False
    assert NATIVE_PRODUCTION_DISPATCH_AVAILABLE is False
    assert NATIVE_OFFLINE_EXECUTION_IS_PRODUCTION_DISPATCH is False
    assert SCRIPTED_WORKER_IS_NATIVE_EXECUTION is False
    assert NATIVE_EXECUTION_IS_SOURCE_EVIDENCE is False
    assert_production_dispatch_closed()
    with pytest.raises(ContractValidationError, match="specialist_invocation_not_granted"):
        invoke_specialist()
    with pytest.raises(ContractValidationError, match="decision_target_not_established"):
        construct_decision_native_request()
    with pytest.raises(ContractValidationError, match="production_grant_issuance_closed"):
        issue_production_execution_grant()
    with pytest.raises(ContractValidationError, match="historical_finalization_not_performed"):
        refuse_historical_finalization()
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        refuse_decision_native_request()
    with pytest.raises(ContractValidationError, match="specialist_execution_not_granted"):
        refuse_scripted_worker_as_native(ScriptedTestWorker())
    with pytest.raises(ContractValidationError, match="specialist_execution_not_granted"):
        refuse_scripted_worker_as_native(
            ScriptedWorkerObservation(report=ScriptedWorkerReport.SUCCEEDED)
        )


def test_cp3_live_ops_run_sees_native_types_and_preserves_identity():
    harness, step, binding = _live_harness()
    log: list[object] = []
    provider = FakeProvider([_tools(("get_observed_network_state", {})), _network_from_turn])
    executed = harness.orchestrator.execute_live_ops(
        step.invocation,
        binding,
        harness.authority(step),
        _runtime(provider, log),
        now_utc=BEFORE,
    )

    assert executed.live_ops_run_invoked is True
    assert executed.scripted_worker_used is False
    assert executed.production_dispatch is False
    assert executed.distributed_exactly_once is False
    assert type(binding.request) is LiveOpsRunRequest
    assert executed.bridge.request is binding.request
    assert isinstance(executed.native_result, LiveOpsRunResult)
    assert executed.mapped.native_result is executed.native_result
    assert type(executed.mapped) is MasterLiveOpsNativeResult
    assert executed.identity.invocation_id == step.invocation.invocation_id
    assert executed.identity.invocation_id == executed.bridge.invocation_id
    assert executed.identity.specialist_request_id == binding.specialist_request_id
    assert executed.identity.correlation_id == binding.correlation_id
    assert executed.identity.master_request_id == binding.master_request_id
    assert executed.identity.step_id == "live-1"
    assert executed.identity.specialist is MasterSpecialist.LIVE_OPS
    assert executed.native_result.request_id == binding.specialist_request_id
    assert executed.native_result.correlation_id == binding.correlation_id
    assert provider.requests
    assert isinstance(provider.requests[0].user_text if hasattr(provider.requests[0], "user_text") else TEXT, str)
    assert type(executed.bridge.request) is LiveOpsRunRequest
    assert executed.native_result.execution_status is LiveOpsRunStatus.COMPLETED
    assert executed.mapped.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED
    assert executed.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert executed.native_execution_is_source_evidence is False
    assert executed.mapped.native_execution_is_source_evidence is False
    assert executed.mapped.authenticated_source_evidence is False
    assert executed.mapped.specialist_invoked_by_bridge is False
    assert executed.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert [item[0] for item in log] == ["get_observed_network_state"]
    identities = (
        executed.identity.master_request_id,
        executed.identity.planning_session_id,
        executed.identity.step_id,
        executed.identity.invocation_id,
        executed.identity.specialist_request_id,
        executed.identity.correlation_id,
    )
    assert len(set(identities)) == 6


def test_unconsumed_grant_does_not_call_run():
    harness, step, binding = _live_harness()
    provider = FakeProvider([_tools(("get_observed_network_state", {})), _network_from_turn])
    with pytest.raises(ContractValidationError, match="consumed_grant_required"):
        harness.orchestrator.execute_live_ops(
            step.invocation,
            binding,
            harness.authority(step, consume=False),
            _runtime(provider),
            now_utc=BEFORE,
        )
    assert provider.requests == []


def test_second_dispatch_is_rejected_as_replay():
    harness, step, binding = _live_harness()
    provider = FakeProvider([_tools(("get_observed_network_state", {})), _network_from_turn])
    authority = harness.authority(step)
    first = harness.orchestrator.execute_live_ops(
        step.invocation,
        binding,
        authority,
        _runtime(provider),
        now_utc=BEFORE,
    )
    assert first.live_ops_run_invoked is True
    replay_provider = FakeProvider([_tools(("get_observed_network_state", {})), _network_from_turn])
    with pytest.raises(ContractValidationError, match="duplicate_dispatch"):
        harness.orchestrator.execute_live_ops(
            step.invocation,
            binding,
            authority,
            _runtime(replay_provider),
            now_utc=BEFORE,
        )
    assert replay_provider.requests == []


def test_stale_fence_is_rejected_before_run():
    harness, step, binding = _live_harness()
    provider = FakeProvider([_tools(("get_observed_network_state", {})), _network_from_turn])
    authority = harness.authority(step, fence_epoch=99)
    with pytest.raises(ContractValidationError, match="stale_fence"):
        harness.orchestrator.execute_live_ops(
            step.invocation,
            binding,
            authority,
            _runtime(provider),
            now_utc=BEFORE,
        )
    assert provider.requests == []


def test_deadline_and_cancellation_fail_closed_before_run():
    harness, step, binding = _live_harness()
    late = FakeProvider([_tools(("get_observed_network_state", {})), _network_from_turn])
    with pytest.raises(ContractValidationError, match="deadline_expired"):
        harness.orchestrator.execute_live_ops(
            step.invocation,
            binding,
            harness.authority(step),
            _runtime(late),
            now_utc="2026-01-15T00:31:00Z",
        )
    assert late.requests == []

    cancelled_harness, cancelled_step, cancelled_binding = _live_harness()
    cancelled_provider = FakeProvider([_tools(("get_observed_network_state", {})), _network_from_turn])
    with pytest.raises(ContractValidationError, match="schedule_cancelled"):
        cancelled_harness.orchestrator.execute_live_ops(
            cancelled_step.invocation,
            cancelled_binding,
            cancelled_harness.authority(cancelled_step),
            _runtime(cancelled_provider, cancelled=lambda: True),
            now_utc=BEFORE,
        )
    assert cancelled_provider.requests == []


def test_unapproved_model_selected_tools_are_not_executed():
    harness, step, binding = _live_harness()
    log: list[object] = []
    provider = FakeProvider(
        [
            _tools(("not_a_catalog_tool", {})),
            _tools(("not_a_catalog_tool", {})),
            _tools(("not_a_catalog_tool", {})),
            _tools(("not_a_catalog_tool", {})),
        ]
    )
    executed = harness.orchestrator.execute_live_ops(
        step.invocation,
        binding,
        harness.authority(step),
        _runtime(provider, log),
        now_utc=BEFORE,
    )
    assert executed.native_result.execution_status is LiveOpsRunStatus.INVALID_REQUEST
    assert log == []
    assert executed.native_result.verified_claims == ()

    extra_harness, extra_step, extra_binding = _live_harness()
    extra_provider = FakeProvider([_tools(("get_observed_network_state", {})), _network_from_turn])
    handlers = RecordingFakeHandlers([])
    handlers["drop_table"] = handlers["get_observed_network_state"]
    with pytest.raises(ContractValidationError, match="model_origin_dispatch_rejected"):
        extra_harness.orchestrator.execute_live_ops(
            extra_step.invocation,
            extra_binding,
            extra_harness.authority(extra_step),
            _runtime(extra_provider, handlers=handlers),
            now_utc=BEFORE,
        )
    assert extra_provider.requests == []


def test_scripted_worker_is_not_native_execution():
    harness, step, binding = _live_harness()
    with pytest.raises(ContractValidationError, match="model_origin_dispatch_rejected"):
        harness.orchestrator.execute_live_ops(
            step.invocation,
            binding,
            harness.authority(step),
            _runtime(ScriptedTestWorker()),
            now_utc=BEFORE,
        )
    with pytest.raises(ContractValidationError, match="specialist_execution_not_granted"):
        refuse_scripted_worker_as_native(ScriptedTestWorker())
    with pytest.raises(ContractValidationError, match="specialist_execution_not_granted"):
        refuse_scripted_worker_as_native(
            ScriptedWorkerObservation(report=ScriptedWorkerReport.SUCCEEDED)
        )


def test_cross_specialist_grant_is_rejected():
    harness = Harness(_work(LIVE, HISTORY), (_live(), _history()))
    live = harness.step("live-1")
    historical = harness.step("hist-1")
    binding = prepare_master_live_ops_binding(harness.bundle(), harness.descriptor("live-1"))
    provider = FakeProvider([_tools(("get_observed_network_state", {})), _network_from_turn])
    with pytest.raises(ContractValidationError, match="cross_specialist_execution_rejected"):
        harness.orchestrator.execute_live_ops(
            live.invocation,
            binding,
            harness.authority(historical),
            _runtime(provider),
            now_utc=BEFORE,
        )
    assert provider.requests == []


def test_historical_correlation_stays_unfinalized():
    harness = Harness(_work(HISTORY), (_history(),))
    step = harness.step("hist-1")
    binding = prepare_master_historical_binding(harness.bundle(), harness.descriptor("hist-1"))
    path = harness.orchestrator.correlate_historical(
        step.invocation,
        binding,
        harness.authority(step),
        now_utc=BEFORE,
    )
    assert path.native_run_invoked is False
    assert path.finalization_performed is False
    assert path.evidence_issued is False
    assert path.envelope.finalization_performed is False
    assert path.envelope.evidence_issued is False
    assert path.envelope.native_run is None
    assert path.identity.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    assert path.identity.invocation_id == step.invocation.invocation_id
    assert path.envelope.correlation_gaps
    source = MODULE.read_text(encoding="utf-8")
    assert "finalize_historical_specialist_run(" not in source
    assert "HistoricalAnalyticsSpecialist(" not in source


def _discovery() -> LiveOpsDiscovery:
    return LiveOpsDiscovery(
        callsign="UAL100",
        status=LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH,
        aircraft_ids=(AIRCRAFT,),
        evidence_tool_call_id="discovery-tool-1",
        reference_epoch=1_700_000_000,
        identity_resolved=True,
        selection_authorized=False,
    )


def test_decision_stays_blocked_until_three_independent_proofs_exist():
    discovery = _discovery()
    harness = Harness(
        _work(RISK),
        (_decision(),),
        target_candidate_aircraft_id=AIRCRAFT,
    )
    step = harness.step("dec-1")
    readiness = assess_master_decision_execution_readiness(
        harness.bundle(),
        harness.descriptor("dec-1"),
        typed_aircraft_observations=(observe_typed_aircraft_id(AIRCRAFT),),
        callsign_observations=(observe_callsign(discovery),),
        discoveries=(discovery,),
        entity_hints=(
            LiveOpsEntityHint(entity_kind="AIRCRAFT", entity_id=AIRCRAFT, callsign="UAL100"),
        ),
        population_observations=(observe_population((AIRCRAFT,)),),
    )
    catalog = FakeDecisionReadCatalog(
        aircraft=(
            FakeAircraftRead(
                aircraft_id=AIRCRAFT,
                callsign="UAL100",
                state_fresh=True,
                population_member=True,
                in_request_scope=True,
                risk=FakeRiskRecord(RiskPresence.PRESENT, StoredRiskLevel.HIGH),
            ),
        )
    )
    path = harness.orchestrator.resolve_decision(
        step.invocation,
        readiness,
        catalog,
        harness.authority(step),
        now_utc=BEFORE,
    )
    assert path.native_run_invoked is False
    assert path.decision_request is None
    assert path.native_request_built is False
    assert path.resolution.decision_request is None
    assert path.resolution.host_proofs_required == REQUIRED_HOST_PROOFS
    assert path.resolution.host_proofs_satisfied == ()
    assert path.resolution.trusted_target_issued is False
    assert path.resolution.status is DecisionResolutionStatus.UNIQUE_CANDIDATE_NOT_ELIGIBLE
    source = MODULE.read_text(encoding="utf-8")
    assert "DecisionSpecialist(" not in source
    assert "construct_decision_native_request(" not in source


def test_conflicting_decision_targets_are_rejected():
    harness = Harness(
        _work(RISK),
        (_decision(),),
        target_candidate_aircraft_id=AIRCRAFT,
    )
    step = harness.step("dec-1")
    readiness = assess_master_decision_execution_readiness(
        harness.bundle(),
        harness.descriptor("dec-1"),
        typed_aircraft_observations=(
            observe_typed_aircraft_id(AIRCRAFT),
            observe_typed_aircraft_id(OTHER),
        ),
    )
    catalog = FakeDecisionReadCatalog(
        aircraft=(
            FakeAircraftRead(
                aircraft_id=AIRCRAFT,
                callsign=None,
                state_fresh=True,
                population_member=True,
                in_request_scope=True,
                risk=FakeRiskRecord(RiskPresence.PRESENT, StoredRiskLevel.HIGH),
            ),
            FakeAircraftRead(
                aircraft_id=OTHER,
                callsign=None,
                state_fresh=True,
                population_member=True,
                in_request_scope=True,
                risk=FakeRiskRecord(RiskPresence.PRESENT, StoredRiskLevel.HIGH),
            ),
        )
    )
    path = harness.orchestrator.resolve_decision(
        step.invocation,
        readiness,
        catalog,
        harness.authority(step),
        now_utc=BEFORE,
    )
    assert path.resolution.status is DecisionResolutionStatus.REJECTED
    assert DecisionResolutionDefect.AIRCRAFT_ID_MISMATCH in path.resolution.defects
    assert path.resolution.decision_request is None
    assert path.native_run_invoked is False


def test_dependent_decision_is_not_eligible_before_predecessor():
    harness = Harness(
        _work(LIVE, RISK),
        (_live(), _decision(depends_on=("live-1",))),
        target_candidate_aircraft_id=AIRCRAFT,
    )
    decision = harness.step("dec-1")
    readiness = assess_master_decision_execution_readiness(
        harness.bundle(),
        harness.descriptor("dec-1"),
        typed_aircraft_observations=(observe_typed_aircraft_id(AIRCRAFT),),
    )
    catalog = FakeDecisionReadCatalog(
        aircraft=(
            FakeAircraftRead(
                aircraft_id=AIRCRAFT,
                callsign=None,
                state_fresh=True,
                population_member=True,
                in_request_scope=True,
                risk=FakeRiskRecord(RiskPresence.PRESENT, StoredRiskLevel.HIGH),
            ),
        )
    )
    with pytest.raises(ContractValidationError, match="step_not_eligible"):
        harness.orchestrator.resolve_decision(
            decision.invocation,
            readiness,
            catalog,
            harness.authority(decision),
            now_utc=BEFORE,
        )


def test_module_stays_offline_and_does_not_open_production_dispatch():
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {
        "anthropic",
        "boto3",
        "botocore",
        "langgraph",
        "openai",
        "wilvor_ai.sprint3c_scoped_read",
        "wilvor_ai.sprint3c_grant_ledger",
        "wilvor_ai.sprint3c_ingress_principal",
        "wilvor_ai.sprint3c_evidence_response",
    }
    assert forbidden.isdisjoint(imported)
    assert "os.environ" not in source
    assert "lambda_handler" not in source
    assert PRODUCTION_DISPATCH_ENABLED is False
    env = os.environ.copy()
    pythonpath = str(MODULE.parents[1])
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    script = """
import wilvor_ai.sprint3c_specialist_orchestration as module
assert module.PRODUCTION_DISPATCH_ENABLED is False
assert module.NATIVE_PRODUCTION_DISPATCH_AVAILABLE is False
assert module.SCRIPTED_WORKER_IS_NATIVE_EXECUTION is False
from wilvor_ai.master_specialist_runtime_contract import invoke_specialist
from wilvor_ai.contracts import ContractValidationError
try:
    invoke_specialist()
except ContractValidationError as exc:
    assert exc.errors == ("specialist_invocation_not_granted",)
else:
    raise AssertionError("invoke_specialist must remain closed")
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
