"""Offline MA3 dispatch through a consumed host grant and a started ledger fence.

Scripted workers are not native specialists. Scheduling complete() is not
MA4 admission. The local consumption ledger and the execution ledger are
not one atomic store.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError, TemporalScope
from wilvor_ai.master_authorized_scheduler_adapter import (
    PRODUCTION_DISPATCH_ENABLED,
    ExecutionDisposition,
    MasterAuthorizedSchedulerAdapter,
    ScriptedTestWorker,
    ScriptedWorkerObservation,
    ScriptedWorkerReport,
    open_nonproduction_scheduler_seal,
)
from wilvor_ai.master_contracts import (
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_execution_fanout import MasterSchedulingState
from wilvor_ai.master_execution_grants import (
    GrantEvaluationClock,
    GrantLifecycle,
    GrantRejectionReason,
    LocalHarnessConsumptionLedger,
    LocalHarnessExecutionGrant,
    LocalHarnessGrantIssuer,
    LogicalReadOperation,
    MasterExecutionGrantEnvelope,
    evaluate_grant_envelope,
    issue_production_execution_grant,
    parse_execution_grant,
)
from wilvor_ai.master_execution_ledger import (
    InMemoryMasterExecutionLedgerStore,
    MasterExecutionLedger,
    MasterExecutionLedgerEnvelope,
    MasterExecutionLedgerOutcome,
    MasterExecutionLedgerState,
)
from wilvor_ai.master_host_scheduler_integration import (
    AFTER_MERGE_TESTS,
    SCHEDULING_COMPLETE_IS_NOT_SPECIALIST_SUCCESS,
    NonproductionHostSchedulerPort,
    issued_grant_bindings,
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
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS,
    MAX_ROUTE_STEPS,
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRouteExecutionAuthority,
    MasterRouteOutcome,
    MasterRoutePlan,
    MasterRouteProposal,
    MasterRouteStepPlan,
    MasterRouteTemporalIntent,
)
from wilvor_ai.master_specialist_runtime_contract import (
    PRODUCTION_HOST,
    RuntimeContractStatus,
    invoke_specialist,
    observe_local_scheduler_protocol,
)


TEXT = "Which aircraft are currently affected by hazards, and what recorded risks apply?"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
BEFORE = "2026-01-15T00:10:00Z"
DEADLINE = "2026-01-15T00:30:00Z"
EXPIRES = "2026-01-15T00:30:00Z"
ACCEPTED = "2026-01-15T00:00:01Z"
NOW = datetime(2026, 1, 15, 0, 10, tzinfo=timezone.utc)
OWNER = "ledger-owner-1"
PRINCIPAL = "principal-ma3"
POLICY = "policy-rev-ma3"
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


def _context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "correlation_id": "corr-1",
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


def _history(step_id: str, *, start: str, end: str, **overrides: object) -> MasterProposedStep:
    return _step(
        step_id,
        MasterSpecialist.HISTORICAL_ANALYTICS,
        HISTORY,
        temporal_intent=MasterRouteTemporalIntent.HISTORICAL,
        historical_start_utc=start,
        historical_end_utc=end,
        **overrides,
    )


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


def _open(
    capabilities: tuple[MasterProposedCapability, ...],
    steps: tuple[MasterProposedStep, ...],
    **context_overrides: object,
) -> tuple[MasterPlanningSessionRegistry, MasterPlanningSessionRecord, MasterRoutePlan]:
    registry = MasterPlanningSessionRegistry()
    proposal = MasterRouteProposal(user_text=TEXT, capabilities=capabilities, steps=steps)
    record = registry.open_session(proposal, _context(**context_overrides))
    decision = registry.decision_for(record)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    return registry, record, decision.plan


def _snapshot(plan: MasterRoutePlan) -> tuple[object, ...]:
    return (
        plan.to_dict(),
        plan.steps,
        tuple(step.invocation for step in plan.steps),
        tuple(step.depends_on for step in plan.steps),
        plan.execution_authority,
    )


@dataclass
class Prepared:
    grant: LocalHarnessExecutionGrant
    grant_envelope: MasterExecutionGrantEnvelope
    ledger_envelope: MasterExecutionLedgerEnvelope
    attempt_token: str
    fence_epoch: int


class Session:
    def __init__(
        self,
        capabilities: tuple[MasterProposedCapability, ...],
        steps: tuple[MasterProposedStep, ...],
        workers: dict[MasterSpecialist, ScriptedTestWorker],
        *,
        issuer: LocalHarnessGrantIssuer | None = None,
        consumption: LocalHarnessConsumptionLedger | None = None,
        execution: MasterExecutionLedger | None = None,
        principal: object | None = None,
        **context_overrides: object,
    ) -> None:
        self.registry, self.record, self.plan = _open(capabilities, steps, **context_overrides)
        self.snapshot = _snapshot(self.plan)
        self.clock = GrantEvaluationClock(NOW)
        self.issuer = issuer if issuer is not None else LocalHarnessGrantIssuer(self.clock)
        self.consumption = consumption if consumption is not None else LocalHarnessConsumptionLedger()
        self.execution = execution if execution is not None else MasterExecutionLedger(InMemoryMasterExecutionLedgerStore())
        self.principal = principal if principal is not None else self.issuer.seal_principal(PRINCIPAL)
        self.seal = open_nonproduction_scheduler_seal()
        self.adapter = MasterAuthorizedSchedulerAdapter(
            self.registry,
            self.record,
            deadline_utc=DEADLINE,
            integration_seal=self.seal,
        )
        self.port = NonproductionHostSchedulerPort(
            self.adapter,
            self.issuer,
            self.consumption,
            self.execution,
        )
        for specialist, worker in workers.items():
            self.port.register(specialist, worker)

    def step(self, step_id: str) -> MasterRouteStepPlan:
        return next(item for item in self.plan.steps if item.step_id == step_id)

    def grant_envelope(self, step: MasterRouteStepPlan, **overrides: object) -> MasterExecutionGrantEnvelope:
        context = self.adapter.bounded_context(step.invocation, now_utc=BEFORE)
        specialist = step.invocation.specialist
        _executions, turns, calls = server_read_budget(specialist)
        tool_name = TOOLS[specialist]
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
            "tool_name": tool_name,
            "resource_scope": _scope(specialist, tool_name),
            "region_scope": ("CA",),
            "temporal_scopes": TEMPORAL[specialist],
            "historical_window": context.historical_window,
            "decision_target_scope": context.decision_target_scope,
            "accepted_at_utc": context.accepted_at_utc,
            "deadline_utc": context.deadline_utc,
            "expires_at_utc": EXPIRES,
            "idempotency_key": f"grant-{context.planning_session_id}-{context.step_id}",
            "max_model_turns": turns,
            "max_tool_calls": calls,
            "correlation_id": context.correlation_id,
        }
        values.update(overrides)
        return MasterExecutionGrantEnvelope(**values)  # type: ignore[arg-type]

    def ledger_envelope(
        self,
        step: MasterRouteStepPlan,
        *,
        region_scope: str = "not-geographic-verification",
        resource_scope: str = "keyed-read-label",
        **overrides: object,
    ) -> MasterExecutionLedgerEnvelope:
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
            "resource_scope": resource_scope,
            "region_scope": region_scope,
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
        values.update(overrides)
        return MasterExecutionLedgerEnvelope(**values)  # type: ignore[arg-type]

    def issue(self, envelope: MasterExecutionGrantEnvelope, *, consume: bool = True) -> LocalHarnessExecutionGrant:
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

    def start_ledger(self, envelope: MasterExecutionLedgerEnvelope) -> tuple[str, int]:
        reserved = self.execution.reserve_once(envelope, owner_id=OWNER, now_utc=BEFORE, lease_seconds=60)
        assert reserved.outcome is MasterExecutionLedgerOutcome.CLAIMED
        assert reserved.state is MasterExecutionLedgerState.RESERVED
        assert reserved.dispatch_permitted is False
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
        assert started.fresh_transition is True
        assert started.specialist_executed is False
        assert started.dispatch_permitted is False
        return reserved.attempt_token, reserved.fence_epoch

    def prepare(self, step: MasterRouteStepPlan, *, consume: bool = True, start: bool = True, **ledger_overrides: object) -> Prepared:
        grant_envelope = self.grant_envelope(step)
        grant = self.issue(grant_envelope, consume=consume)
        ledger_envelope = self.ledger_envelope(step, **ledger_overrides)
        if start:
            token, fence = self.start_ledger(ledger_envelope)
        else:
            reserved = self.execution.reserve_once(ledger_envelope, owner_id=OWNER, now_utc=BEFORE, lease_seconds=60)
            assert reserved.attempt_token is not None and reserved.fence_epoch is not None
            token, fence = reserved.attempt_token, reserved.fence_epoch
        return Prepared(grant, grant_envelope, ledger_envelope, token, fence)

    def dispatch(self, step: MasterRouteStepPlan, prepared: Prepared, **overrides: object):
        values: dict[str, object] = {
            "grant": prepared.grant,
            "principal": self.principal,
            "grant_envelope": prepared.grant_envelope,
            "ledger_envelope": prepared.ledger_envelope,
            "owner_id": OWNER,
            "attempt_token": prepared.attempt_token,
            "fence_epoch": prepared.fence_epoch,
            "now_utc": BEFORE,
        }
        values.update(overrides)
        return self.port.dispatch_consumed(step.invocation, **values)  # type: ignore[arg-type]


def _assert_closed_success(session: Session, outcome: object) -> None:
    assert outcome.native_specialist_executed is False  # type: ignore[attr-defined]
    assert outcome.scheduling_complete_is_specialist_success is False  # type: ignore[attr-defined]
    assert outcome.ma4_admission_performed is False  # type: ignore[attr-defined]
    assert outcome.evidence_verified is False  # type: ignore[attr-defined]
    assert outcome.aviation_evidence is False  # type: ignore[attr-defined]
    assert outcome.execution_authority is MasterRouteExecutionAuthority.DENIED  # type: ignore[attr-defined]
    assert outcome.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED  # type: ignore[attr-defined]
    assert session.adapter.production_dispatch_enabled is False
    assert session.port.production_dispatch_enabled is False
    assert session.port.distributed_atomicity is False
    assert session.plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert _snapshot(session.plan) == session.snapshot


def test_two_independent_invocations_stay_separate_and_do_not_admit_evidence():
    live_worker = ScriptedTestWorker()
    historical_worker = ScriptedTestWorker()
    session = Session(
        _work(LIVE, HISTORY),
        (_live(), _history("hist-1", start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z")),
        {
            MasterSpecialist.LIVE_OPS: live_worker,
            MasterSpecialist.HISTORICAL_ANALYTICS: historical_worker,
        },
    )
    assert session.adapter.max_route_steps == MAX_ROUTE_STEPS == 4
    assert session.adapter.max_decision_steps == MAX_DECISION_STEPS == 1
    assert session.adapter.dependency_edges() == ()
    live = session.step("live-1")
    historical = session.step("hist-1")
    assert live.invocation is not historical.invocation
    live_prepared = session.prepare(live)
    historical_prepared = session.prepare(historical)
    first = session.dispatch(live, live_prepared)
    second = session.dispatch(historical, historical_prepared)
    assert first.invocation_id != second.invocation_id
    assert first.scheduling_completed is True and second.scheduling_completed is True
    assert first.execution_disposition.name == "TEST_WORKER_SUCCEEDED"
    assert live_worker.invocation_ids == [live.invocation.invocation_id]
    assert historical_worker.invocation_ids == [historical.invocation.invocation_id]
    _assert_closed_success(session, first)
    handoff = session.port.handoff(BEFORE)
    assert handoff.admission_performed is False
    assert handoff.evidence_verified is False
    assert handoff.aviation_evidence is False
    assert handoff.specialist_runners_invoked is False
    assert handoff.production_dispatch_enabled is False
    assert {item.invocation_id for item in handoff.outcomes} == {
        live.invocation.invocation_id,
        historical.invocation.invocation_id,
    }
    assert session.port.last_confirmation is not None
    assert session.port.last_confirmation.ledger_nonce_is_host_grant is False
    assert session.port.last_confirmation.grant_resource_scope_is_ledger_scope is False
    assert session.port.last_confirmation.distributed_atomicity is False
    completed = session.port.note_ledger_complete(
        historical_prepared.ledger_envelope,
        owner_id=OWNER,
        attempt_token=historical_prepared.attempt_token,
        fence_epoch=historical_prepared.fence_epoch,
        now_utc=BEFORE,
    )
    assert completed.specialist_executed is False
    assert completed.dispatch_permitted is False
    assert SCHEDULING_COMPLETE_IS_NOT_SPECIALIST_SUCCESS
    note = observe_local_scheduler_protocol(True)
    assert note.status is RuntimeContractStatus.REJECTED_FALSE_PROOF
    assert note.native_specialist_executed is False
    assert observe_local_scheduler_protocol("LOW").aviation_evidence is False
    assert observe_local_scheduler_protocol(handoff).native_specialist_executed is False


def test_live_ops_then_decision_preserves_the_dependency_and_blocks_early_decision():
    live_worker = ScriptedTestWorker()
    decision_worker = ScriptedTestWorker()
    session = Session(
        _work(LIVE, RISK),
        (_live(), _decision(depends_on=("live-1",))),
        {MasterSpecialist.LIVE_OPS: live_worker, MasterSpecialist.DECISION: decision_worker},
        target_candidate_aircraft_id="ac-1",
    )
    decision = session.step("dec-1")
    prepared_decision = session.prepare(decision)
    assert session.adapter.dependency_edges() == (("dec-1", "live-1"),)
    with pytest.raises(ContractValidationError) as early:
        session.dispatch(decision, prepared_decision)
    assert early.value.errors == ("step_not_eligible",)
    assert decision_worker.invocation_ids == []
    live = session.step("live-1")
    live_outcome = session.dispatch(live, session.prepare(live))
    assert live_outcome.scheduling_completed is True
    decision_outcome = session.dispatch(decision, prepared_decision)
    assert decision_outcome.step_id == "dec-1"
    assert decision_outcome.depends_on == ("live-1",)
    assert decision_worker.invocation_ids == [decision.invocation.invocation_id]
    assert decision.ready_to_execute is False
    assert session.adapter.dependency_edges() == (("dec-1", "live-1"),)
    handoff = session.port.handoff(BEFORE)
    assert handoff.dependency_edges == (("dec-1", "live-1"),)
    assert handoff.admission_performed is False
    assert _snapshot(session.plan) == session.snapshot


def test_repeated_historical_steps_share_a_worker_and_keep_separate_windows():
    worker = ScriptedTestWorker()
    session = Session(
        _work(HISTORY),
        (
            _history("hist-1", start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z"),
            _history("hist-2", start="2026-01-03T00:00:00Z", end="2026-01-04T00:00:00Z"),
        ),
        {MasterSpecialist.HISTORICAL_ANALYTICS: worker},
    )
    first_step = session.step("hist-1")
    second_step = session.step("hist-2")
    first = session.dispatch(first_step, session.prepare(first_step))
    second = session.dispatch(second_step, session.prepare(second_step))
    assert first.historical_window == ("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
    assert second.historical_window == ("2026-01-03T00:00:00Z", "2026-01-04T00:00:00Z")
    assert first.invocation_id != second.invocation_id
    assert worker.invocation_ids == [first.invocation_id, second.invocation_id]
    assert session.port.handoff(BEFORE).admission_performed is False
    assert _snapshot(session.plan) == session.snapshot


def test_failed_and_timed_out_predecessors_skip_the_dependent_without_dispatch():
    failed_worker = ScriptedTestWorker(default=ScriptedWorkerReport.FAILED)
    decision_worker = ScriptedTestWorker()
    failed = Session(
        _work(LIVE, RISK),
        (_live(), _decision(depends_on=("live-1",))),
        {MasterSpecialist.LIVE_OPS: failed_worker, MasterSpecialist.DECISION: decision_worker},
        target_candidate_aircraft_id="ac-1",
    )
    live = failed.dispatch(failed.step("live-1"), failed.prepare(failed.step("live-1")))
    assert live.execution_succeeded is False
    assert live.execution_disposition is ExecutionDisposition.TEST_WORKER_FAILED
    assert live.reason == "worker_failed"
    assert live.scheduling_state is MasterSchedulingState.FAILED
    decision = failed.prepare(failed.step("dec-1"))
    with pytest.raises(ContractValidationError) as blocked:
        failed.dispatch(failed.step("dec-1"), decision)
    assert blocked.value.errors == ("dependency_failed",)
    assert decision_worker.invocation_ids == []
    handoff = failed.port.handoff(BEFORE)
    skipped = next(item for item in handoff.outcomes if item.step_id == "dec-1")
    assert skipped.execution_succeeded is False
    assert skipped.ma4_admission_performed is False
    assert handoff.admission_performed is False

    timed_worker = ScriptedTestWorker(default=ScriptedWorkerReport.TIMED_OUT)
    later_decision = ScriptedTestWorker()
    timed = Session(
        _work(LIVE, RISK),
        (_live(), _decision(depends_on=("live-1",))),
        {MasterSpecialist.LIVE_OPS: timed_worker, MasterSpecialist.DECISION: later_decision},
        target_candidate_aircraft_id="ac-1",
    )
    outcome = timed.dispatch(timed.step("live-1"), timed.prepare(timed.step("live-1")))
    assert outcome.reason == "worker_timeout"
    assert outcome.execution_succeeded is False
    with pytest.raises(ContractValidationError) as timeout_block:
        timed.dispatch(timed.step("dec-1"), timed.prepare(timed.step("dec-1")))
    assert timeout_block.value.errors == ("dependency_failed",)
    assert later_decision.invocation_ids == []


def test_duplicate_claim_does_not_run_the_worker_again():
    worker = ScriptedTestWorker()
    session = Session(_work(LIVE), (_live(),), {MasterSpecialist.LIVE_OPS: worker})
    step = session.step("live-1")
    prepared = session.prepare(step)
    outcome = session.dispatch(step, prepared)
    assert outcome.execution_succeeded is True
    replay = session.execution.mark_started(
        prepared.ledger_envelope,
        owner_id=OWNER,
        attempt_token=prepared.attempt_token,
        fence_epoch=prepared.fence_epoch,
        now_utc=BEFORE,
    )
    assert replay.outcome is MasterExecutionLedgerOutcome.REPLAYED
    assert replay.fresh_transition is False
    assert replay.dispatch_permitted is False
    with pytest.raises(ContractValidationError) as duplicate:
        session.dispatch(step, prepared)
    assert duplicate.value.errors == ("duplicate_claim",)
    assert worker.invocation_ids == [step.invocation.invocation_id]


def test_stale_fence_and_unstarted_fence_do_not_dispatch():
    worker = ScriptedTestWorker()
    session = Session(_work(LIVE), (_live(),), {MasterSpecialist.LIVE_OPS: worker})
    step = session.step("live-1")
    prepared = session.prepare(step)
    with pytest.raises(ContractValidationError) as stale:
        session.dispatch(step, prepared, fence_epoch=prepared.fence_epoch + 1)
    assert stale.value.errors == ("stale_fence",)
    assert worker.invocation_ids == []
    outcome = session.dispatch(step, prepared)
    assert outcome.scheduling_completed is True
    assert worker.invocation_ids == [step.invocation.invocation_id]

    quiet = ScriptedTestWorker()
    other = Session(_work(LIVE), (_live("live-2"),), {MasterSpecialist.LIVE_OPS: quiet})
    pending = other.prepare(other.step("live-2"), start=False)
    with pytest.raises(ContractValidationError) as missing:
        other.dispatch(other.step("live-2"), pending)
    assert missing.value.errors == ("mark_started_fence_required",)
    assert other.port.last_confirmation is None
    with pytest.raises(ContractValidationError) as closed:
        other.dispatch(other.step("live-2"), pending)
    assert closed.value.errors == ("ledger_fence_rejected",)
    assert quiet.invocation_ids == []


def test_cancellation_and_deadline_stop_dispatch():
    worker = ScriptedTestWorker()
    cancelled = Session(_work(LIVE), (_live(),), {MasterSpecialist.LIVE_OPS: worker})
    step = cancelled.step("live-1")
    prepared = cancelled.prepare(step)
    cancelled.adapter.cancel(now_utc=BEFORE)
    with pytest.raises(ContractValidationError) as stopped:
        cancelled.dispatch(step, prepared)
    assert stopped.value.errors == ("schedule_cancelled",)
    assert worker.invocation_ids == []
    assert cancelled.port.handoff(BEFORE).admission_performed is False

    expired_worker = ScriptedTestWorker()
    expired = Session(_work(LIVE), (_live(),), {MasterSpecialist.LIVE_OPS: expired_worker})
    pending = expired.prepare(expired.step("live-1"))
    with pytest.raises(ContractValidationError) as deadline:
        expired.dispatch(expired.step("live-1"), pending, now_utc="2026-01-15T00:31:00Z")
    assert deadline.value.errors == ("deadline_expired",)
    assert expired_worker.invocation_ids == []


def test_consumed_only_gate_rejects_an_issued_grant_and_a_consumption_record():
    worker = ScriptedTestWorker()
    session = Session(_work(LIVE), (_live(),), {MasterSpecialist.LIVE_OPS: worker})
    step = session.step("live-1")
    prepared = session.prepare(step, consume=False)
    with pytest.raises(ContractValidationError) as unconsumed:
        session.dispatch(step, prepared)
    assert unconsumed.value.errors == ("consumed_grant_required",)
    assert worker.invocation_ids == []
    consumption = session.issuer.consume(
        prepared.grant,
        session.consumption,
        **issued_grant_bindings(prepared.grant_envelope, session.principal),
    )
    with pytest.raises(ContractValidationError) as record:
        session.dispatch(step, prepared, grant=consumption)
    assert record.value.errors == ("forged_grant_rejected",)
    assert worker.invocation_ids == []
    outcome = session.dispatch(step, prepared)
    assert outcome.execution_succeeded is True
    assert session.port.last_verification is not None
    assert session.port.last_verification.consumed is True
    assert session.port.last_verification.dispatch_permitted is False
    assert worker.invocation_ids == [step.invocation.invocation_id]


def test_cross_session_cross_specialist_and_cross_principal_grants_are_rejected():
    issuer = LocalHarnessGrantIssuer(GrantEvaluationClock(NOW))
    consumption = LocalHarnessConsumptionLedger()
    principal = issuer.seal_principal(PRINCIPAL)
    left_worker = ScriptedTestWorker()
    left = Session(
        _work(LIVE),
        (_live(),),
        {MasterSpecialist.LIVE_OPS: left_worker},
        issuer=issuer,
        consumption=consumption,
        principal=principal,
    )
    right_worker = ScriptedTestWorker()
    right = Session(
        _work(LIVE),
        (_live(),),
        {MasterSpecialist.LIVE_OPS: right_worker},
        issuer=issuer,
        consumption=consumption,
        execution=MasterExecutionLedger(InMemoryMasterExecutionLedgerStore()),
        principal=principal,
        master_request_id="master-req-1",
        correlation_id="corr-right",
    )
    stolen = left.prepare(left.step("live-1"))
    right_step = right.step("live-1")
    right_prepared = right.prepare(right_step)
    with pytest.raises(ContractValidationError) as cross_session:
        right.dispatch(
            right_step,
            right_prepared,
            grant=stolen.grant,
            grant_envelope=stolen.grant_envelope,
        )
    assert cross_session.value.errors == ("SESSION_MISMATCH",)
    assert right_worker.invocation_ids == []

    historical_worker = ScriptedTestWorker()
    historical = Session(
        _work(HISTORY),
        (_history("hist-1", start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z"),),
        {MasterSpecialist.HISTORICAL_ANALYTICS: historical_worker},
        issuer=issuer,
        consumption=consumption,
        execution=MasterExecutionLedger(InMemoryMasterExecutionLedgerStore()),
        principal=principal,
        correlation_id="corr-hist",
    )
    hist_step = historical.step("hist-1")
    context = historical.adapter.bounded_context(hist_step.invocation, now_utc=BEFORE)
    _executions, turns, calls = server_read_budget(MasterSpecialist.LIVE_OPS)
    forged_envelope = MasterExecutionGrantEnvelope(
        master_request_id=context.master_request_id,
        planning_session_id=context.planning_session_id,
        step_id=context.step_id,
        invocation_id=context.invocation_id,
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        capability=LIVE,
        principal_ref=PRINCIPAL,
        policy_revision=POLICY,
        tool_name=TOOLS[MasterSpecialist.LIVE_OPS],
        resource_scope=_scope(MasterSpecialist.LIVE_OPS, TOOLS[MasterSpecialist.LIVE_OPS]),
        region_scope=("CA",),
        temporal_scopes=(TemporalScope.CURRENT,),
        historical_window=None,
        decision_target_scope=None,
        accepted_at_utc=context.accepted_at_utc,
        deadline_utc=context.deadline_utc,
        expires_at_utc=EXPIRES,
        idempotency_key="grant-forged-live-on-historical",
        max_model_turns=turns,
        max_tool_calls=calls,
        correlation_id=context.correlation_id,
    )
    forged = historical.issue(forged_envelope)
    prepared = historical.prepare(hist_step)
    other_token = prepared.attempt_token
    with pytest.raises(ContractValidationError) as cross_specialist:
        historical.dispatch(hist_step, prepared, grant=forged, grant_envelope=forged_envelope)
    assert cross_specialist.value.errors == ("SPECIALIST_MISMATCH",)
    assert historical_worker.invocation_ids == []
    other = Session(_work(LIVE), (_live("live-9"),), {MasterSpecialist.LIVE_OPS: ScriptedTestWorker()})
    other_prepared = other.prepare(other.step("live-9"))
    with pytest.raises(ContractValidationError) as cross_token:
        historical.dispatch(hist_step, prepared, attempt_token=other_prepared.attempt_token)
    assert cross_token.value.errors == ("stale_fence",)
    assert other_token != other_prepared.attempt_token
    swapped = issuer.seal_principal(PRINCIPAL)
    assert swapped is not principal
    with pytest.raises(ContractValidationError) as cross_principal:
        historical.dispatch(hist_step, prepared, principal=swapped)
    assert cross_principal.value.errors == ("PRINCIPAL_MISMATCH",)
    assert historical_worker.invocation_ids == []


def test_budget_overrun_fails_without_raising_the_server_ceiling():
    _executions, turns, calls = server_read_budget(MasterSpecialist.LIVE_OPS)
    worker = ScriptedTestWorker(tool_calls_used=calls + 1, model_turns_used=turns)
    session = Session(_work(LIVE), (_live(),), {MasterSpecialist.LIVE_OPS: worker})
    outcome = session.dispatch(session.step("live-1"), session.prepare(session.step("live-1")))
    assert outcome.execution_succeeded is False
    assert outcome.reason == "budget_exceeded"
    assert outcome.native_specialist_executed is False
    assert server_read_budget(MasterSpecialist.LIVE_OPS) == (_executions, turns, calls)
    assert session.port.handoff(BEFORE).admission_performed is False
    assert worker.invocation_ids == [session.step("live-1").invocation.invocation_id]


def test_model_controlled_dispatch_serialized_grants_and_flags_are_rejected():
    worker = ScriptedTestWorker()
    session = Session(_work(LIVE), (_live(),), {MasterSpecialist.LIVE_OPS: worker})
    step = session.step("live-1")
    prepared = session.prepare(step)

    def _model() -> None:
        return None

    with pytest.raises(ContractValidationError) as registered:
        session.port.register(MasterSpecialist.LIVE_OPS, _model)
    assert registered.value.errors == ("model_origin_dispatch_rejected",)
    with pytest.raises(ContractValidationError) as called:
        session.dispatch(step, prepared, grant=_model)
    assert called.value.errors == ("model_origin_dispatch_rejected",)

    class _Flag:
        authorized = True

    with pytest.raises(ContractValidationError) as flag:
        session.dispatch(step, prepared, grant=_Flag())
    assert flag.value.errors == ("authorized_flag_rejected",)
    with pytest.raises(ContractValidationError) as token:
        session.dispatch(step, prepared, grant=prepared.attempt_token)
    assert token.value.errors == ("token_not_a_grant",)
    claim = prepared.grant.serialized_claim()
    with pytest.raises(ContractValidationError) as serialized:
        session.dispatch(step, prepared, grant=claim)
    assert serialized.value.errors == ("serialized_grant_rejected",)
    with pytest.raises(ContractValidationError) as parsed:
        session.dispatch(step, prepared, grant=parse_execution_grant(claim))
    assert parsed.value.errors == ("serialized_grant_rejected",)
    with pytest.raises(ContractValidationError) as scan:
        LogicalReadOperation("aircraft", ReadOperation.SCAN)
    assert scan.value.errors == ("scan_not_permitted",)
    with pytest.raises(ContractValidationError) as write:
        LogicalReadOperation("put_item", ReadOperation.GET_ITEM)
    assert write.value.errors == ("data_plane_write_not_authorized",)
    assert worker.invocation_ids == []
    outcome = session.dispatch(step, prepared)
    assert outcome.execution_succeeded is True
    assert worker.invocation_ids == [step.invocation.invocation_id]


def test_false_aviation_strings_and_ledger_complete_do_not_admit_ma4():
    worker = ScriptedTestWorker()
    session = Session(_work(LIVE), (_live(),), {MasterSpecialist.LIVE_OPS: worker})
    step = session.step("live-1")
    prepared = session.prepare(
        step,
        region_scope="SIGMET-polygon-overlap",
        resource_scope="verified-zero",
    )
    outcome = session.dispatch(step, prepared)
    assert outcome.scheduling_completed is True
    assert outcome.execution_succeeded is True
    assert outcome.scheduling_complete_is_specialist_success is False
    assert outcome.native_specialist_executed is False
    assert outcome.aviation_evidence is False
    confirmation = session.port.last_confirmation
    assert confirmation is not None
    assert confirmation.region_scope_is_geographic_verification is False
    assert confirmation.grant_resource_scope_is_ledger_scope is False
    assert confirmation.decision_target_authorized is False
    completed = session.port.note_ledger_complete(
        prepared.ledger_envelope,
        owner_id=OWNER,
        attempt_token=prepared.attempt_token,
        fence_epoch=prepared.fence_epoch,
        now_utc=BEFORE,
    )
    assert completed.state is MasterExecutionLedgerState.COMPLETED
    assert completed.specialist_executed is False
    assert completed.specialist_execution_permitted is False
    handoff = session.port.handoff(BEFORE)
    assert handoff.admission_performed is False
    assert handoff.aviation_evidence is False
    assert handoff.evidence_verified is False
    assert handoff.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert handoff.specialist_runners_invoked is False
    assert "LOW" not in handoff.limitations
    low = observe_local_scheduler_protocol("LOW")
    assert low.status is RuntimeContractStatus.REJECTED_FALSE_PROOF
    assert low.aviation_evidence is False
    geographic = observe_local_scheduler_protocol({"region": "SIGMET polygon overlap"})
    assert geographic.evidence_admitted is False
    assert geographic.native_specialist_executed is False


def test_execution_ledger_is_not_the_consumption_ledger_and_production_stays_closed():
    session = Session(_work(LIVE), (_live(),), {MasterSpecialist.LIVE_OPS: ScriptedTestWorker()})
    envelope = session.grant_envelope(session.step("live-1"))
    grant = session.issue(envelope, consume=False)
    rejected = session.port.demonstrate_execution_ledger_is_not_a_consumption_ledger(
        grant,
        session.principal,
        envelope,
    )
    assert rejected.reason is GrantRejectionReason.LEDGER_NOT_INTEGRATED
    assert rejected.distributed_ledger is False
    assert rejected.durable_single_use is False
    assert rejected.dispatch_permitted is False
    assert rejected.local_harness_consumption_recorded is False
    verification = session.issuer.verify(grant, **issued_grant_bindings(envelope, session.principal))
    assert verification.local_harness_binding_accepted is True
    assert verification.consumed is False
    with pytest.raises(ContractValidationError) as production:
        issue_production_execution_grant(envelope)
    assert production.value.errors == ("production_grant_issuance_closed",)
    with pytest.raises(ContractValidationError) as native:
        invoke_specialist(envelope)
    assert native.value.errors == ("specialist_invocation_not_granted",)
    assert PRODUCTION_DISPATCH_ENABLED is False
    assert PRODUCTION_HOST is None
    assert session.issuer.production_grants_issued == 0
    assert session.seal.grants_authority is False
    assert len(AFTER_MERGE_TESTS) == 4
    assert "LEDGER_NOT_INTEGRATED" in AFTER_MERGE_TESTS[0]
    assert "ScriptedTestWorker" in AFTER_MERGE_TESTS[2]


def test_nonproduction_seal_can_record_a_scripted_note_without_ma4_admission():
    registry, record, plan = _open(_work(LIVE), (_live(),))
    seal = open_nonproduction_scheduler_seal()
    adapter = MasterAuthorizedSchedulerAdapter(registry, record, deadline_utc=DEADLINE, integration_seal=seal)
    invocation = plan.steps[0].invocation
    outcome = adapter.record_offline_scripted_attempt(
        invocation,
        ScriptedWorkerObservation(report=ScriptedWorkerReport.SUCCEEDED),
        now_utc=BEFORE,
        seal=seal,
    )
    assert outcome.scheduling_completed is True
    assert outcome.execution_succeeded is True
    assert outcome.scheduling_complete_is_specialist_success is False
    assert outcome.native_specialist_executed is False
    handoff = adapter.handoff_to_ma4(BEFORE)
    assert handoff.admission_performed is False
    assert handoff.specialist_runners_invoked is False
    assert adapter.production_dispatch_enabled is False
    assert seal.production_dispatch_enabled is False
    with pytest.raises(ContractValidationError) as missing:
        adapter.record_offline_scripted_attempt(
            invocation,
            ScriptedWorkerObservation(report=ScriptedWorkerReport.SUCCEEDED),
            now_utc=BEFORE,
            seal=open_nonproduction_scheduler_seal(),
        )
    assert missing.value.errors == ("host_grant_authority_missing",)
