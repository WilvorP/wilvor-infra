"""MA3 dispatch tests. Fake workers are not aviation evidence.

Object identity selects the retained invocation inside this process. It is
not production authentication. The in-process ledger double is not Agent 5's
durable ledger. Scheduling completion stays distinct from worker success.
"""

from __future__ import annotations

import inspect
import threading
from dataclasses import replace

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.master_authorized_scheduler_adapter import (
    INTEGRATION_SEAMS,
    MA4_HANDOFF_NOT_ADMISSION,
    PROCESS_LOCAL_LOCK_LIMITATION,
    PRODUCTION_DISPATCH_ENABLED,
    SCHEDULING_IS_NOT_EXECUTION,
    SPECIALIST_RUNNER_SEAMS,
    AuthorityDistinction,
    ExecutionDisposition,
    InProcessLedgerReservationDouble,
    LocalTestHostAuthoritySeam,
    LocalTestWorkerDispatchPort,
    MasterAuthorizedSchedulerAdapter,
    ScriptedTestWorker,
    ScriptedWorkerReport,
    open_local_test_host_seam,
    reject_untrusted_activation,
)
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_execution_fanout import MasterSchedulingState
from wilvor_ai.master_planning_session import (
    MasterPlanningSessionRecord,
    MasterPlanningSessionRegistry,
)
from wilvor_ai.master_read_resource_guard import server_read_budget
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS,
    MAX_ROUTE_STEPS,
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRouteOutcome,
    MasterRoutePlan,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
)


TEXT = "Which aircraft are currently affected by hazards, and what recorded risks apply?"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
BEFORE = "2026-01-15T00:10:00Z"
DEADLINE = "2026-01-15T00:30:00Z"
ACCEPTED = "2026-01-15T00:00:01Z"


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


def _history(
    step_id: str,
    *,
    start: str,
    end: str,
    **overrides: object,
) -> MasterProposedStep:
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


def _wire(
    registry: MasterPlanningSessionRegistry,
    record: MasterPlanningSessionRecord,
    workers: dict[MasterSpecialist, ScriptedTestWorker],
    deadline: str = DEADLINE,
) -> tuple[MasterAuthorizedSchedulerAdapter, LocalTestHostAuthoritySeam, LocalTestWorkerDispatchPort]:
    seam = open_local_test_host_seam()
    ledger = InProcessLedgerReservationDouble()
    port = LocalTestWorkerDispatchPort(seam, ledger)
    for specialist, worker in workers.items():
        port.register(specialist, worker)
    adapter = MasterAuthorizedSchedulerAdapter(
        registry,
        record,
        deadline_utc=deadline,
        host_seam=seam,
        ledger=ledger,
        dispatch_port=port,
    )
    return adapter, seam, port


def _snapshot(plan: MasterRoutePlan) -> tuple[object, ...]:
    return (
        plan.to_dict(),
        plan.steps,
        tuple(step.invocation for step in plan.steps),
        tuple(step.depends_on for step in plan.steps),
    )


def test_parallel_independent_steps_keep_separate_identity_and_clocks():
    registry, record, plan = _open(_work(LIVE, HISTORY), (_live(), _history("hist-1", start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z")))
    snapshot = _snapshot(plan)
    live_worker = ScriptedTestWorker()
    historical_worker = ScriptedTestWorker()
    adapter, seam, _port = _wire(
        registry,
        record,
        {
            MasterSpecialist.LIVE_OPS: live_worker,
            MasterSpecialist.HISTORICAL_ANALYTICS: historical_worker,
        },
    )
    assert adapter.max_route_steps is MAX_ROUTE_STEPS
    assert adapter.max_decision_steps is MAX_DECISION_STEPS
    assert MAX_ROUTE_STEPS == 4
    assert MAX_DECISION_STEPS == 1
    assert adapter.dependency_edges() == ()
    assert adapter.production_dispatch_enabled is False
    assert adapter.durable_exactly_once is False
    assert PRODUCTION_DISPATCH_ENABLED is False
    described = adapter.describe(BEFORE)
    assert [item.descriptive_allow for item in described] == [True, True]
    assert all(item.usable_production_grant is False for item in described)
    assert all(item.authority is AuthorityDistinction.DESCRIPTIVE for item in described)
    assert seam.issued_count == 0

    live = next(step for step in plan.steps if step.step_id == "live-1")
    context = adapter.bounded_context(live.invocation, now_utc=BEFORE)
    assert context.master_request_id == "master-req-1"
    assert context.correlation_id == "corr-1"
    assert context.planning_session_id == record.session_id
    assert context.planning_session_id != context.master_request_id
    assert context.accepted_at_utc == ACCEPTED
    assert context.evaluation_utc == BEFORE
    assert context.deadline_utc == DEADLINE
    assert context.source_observation_utc is None
    assert context.source_clock_established is False
    assert context.identity_authority is AuthorityDistinction.DESCRIPTIVE
    assert context.budget_authority is AuthorityDistinction.EVALUATED
    assert context.identity_authority is not AuthorityDistinction.SOURCE_AUTHENTICATED
    assert (context.max_executions, context.max_model_turns, context.max_tool_calls) == server_read_budget(
        MasterSpecialist.LIVE_OPS
    )
    assert context.principal_ref is None
    assert context.policy_revision is None
    assert context.resource_scope == "NOT_AUTHORIZED"
    assert context.resource_scope_authorized is False
    assert context.region_scope_authorized is False
    assert context.planning_ready_authorizes_dispatch is False
    assert seam.issued_count == 0

    eligible = [item.step_id for item in adapter.fanout.eligible(BEFORE)]
    wave = adapter.run_eligible(BEFORE)
    assert [item.step_id for item in wave] == eligible
    assert len(wave) == 2
    assert len({item.invocation_id for item in wave}) == 2
    assert {item.specialist for item in wave} == {
        MasterSpecialist.LIVE_OPS,
        MasterSpecialist.HISTORICAL_ANALYTICS,
    }
    assert all(item.scheduling_completed and item.execution_succeeded for item in wave)
    assert all(item.scheduling_state is MasterSchedulingState.COMPLETED for item in wave)
    assert all(item.execution_disposition is ExecutionDisposition.TEST_WORKER_SUCCEEDED for item in wave)
    assert all(item.evidence_verified is False and item.aviation_evidence is False for item in wave)
    assert all(item.scheduling_is_execution is False for item in wave)
    assert all(item.execution_authority is MasterRouteExecutionAuthority.DENIED for item in wave)
    assert live_worker.invocation_ids == [live.invocation.invocation_id]
    assert len(historical_worker.invocation_ids) == 1
    assert adapter.fanout.evidence_verified is False
    assert adapter.fanout.execution_authorized is False
    assert _snapshot(plan) == snapshot
    assert registry.plan_for(record) is plan


def test_repeated_historical_invocations_stay_separate():
    registry, record, plan = _open(
        _work(HISTORY),
        (
            _history("hist-1", start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z"),
            _history("hist-2", start="2026-01-03T00:00:00Z", end="2026-01-05T00:00:00Z"),
        ),
    )
    worker = ScriptedTestWorker()
    adapter, _seam, _port = _wire(registry, record, {MasterSpecialist.HISTORICAL_ANALYTICS: worker})
    outcomes = adapter.run_to_quiescence(BEFORE)
    assert [item.step_id for item in outcomes] == ["hist-1", "hist-2"]
    assert outcomes[0].specialist is outcomes[1].specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    assert outcomes[0].invocation_id != outcomes[1].invocation_id
    assert outcomes[0].historical_window == ("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
    assert outcomes[1].historical_window == ("2026-01-03T00:00:00Z", "2026-01-05T00:00:00Z")
    assert outcomes[0].historical_window != outcomes[1].historical_window
    assert worker.invocation_ids == [outcomes[0].invocation_id, outcomes[1].invocation_id]
    assert all(item.execution_succeeded and item.evidence_verified is False for item in outcomes)
    assert adapter.dependency_edges() == ()


def test_live_ops_then_decision_preserves_the_dependency_edge():
    registry, record, plan = _open(
        _work(LIVE, RISK),
        (_live(), _decision(depends_on=("live-1",))),
        target_candidate_aircraft_id="ac-1",
    )
    decision = next(step for step in plan.steps if step.step_id == "dec-1")
    live_worker = ScriptedTestWorker()
    decision_worker = ScriptedTestWorker()
    adapter, _seam, _port = _wire(
        registry,
        record,
        {MasterSpecialist.LIVE_OPS: live_worker, MasterSpecialist.DECISION: decision_worker},
    )
    assert adapter.dependency_edges() == (("dec-1", "live-1"),)
    assert [item.step_id for item in adapter.fanout.eligible(BEFORE)] == ["live-1"]
    decision_context = adapter.bounded_context(decision.invocation, now_utc=BEFORE)
    assert decision_context.planning_ready_to_execute is False
    assert decision_context.planning_ready_authorizes_dispatch is False
    assert MasterRouteGate.DEPENDENCY_BLOCKED in decision_context.planning_gates
    assert decision_context.decision_target_scope == "ac-1"
    assert decision_context.decision_target_authorized is False
    assert decision_context.historical_window is None

    first = adapter.run_eligible(BEFORE)
    assert [item.step_id for item in first] == ["live-1"]
    assert decision_worker.invocation_ids == []
    assert adapter.fanout.plan.steps[1].gates == decision.gates
    second = adapter.run_eligible(BEFORE)
    assert [item.step_id for item in second] == ["dec-1"]
    assert decision_worker.invocation_ids == [decision.invocation.invocation_id]
    assert second[0].scheduling_completed is True
    assert second[0].execution_succeeded is True
    assert second[0].evidence_verified is False
    assert second[0].execution_authority is MasterRouteExecutionAuthority.DENIED
    assert decision.ready_to_execute is False
    assert adapter.dependency_edges() == (("dec-1", "live-1"),)


def test_duplicate_dispatch_does_not_call_the_worker_again():
    registry, record, plan = _open(_work(LIVE), (_live(),))
    worker = ScriptedTestWorker()
    adapter, _seam, _port = _wire(registry, record, {MasterSpecialist.LIVE_OPS: worker})
    invocation = plan.steps[0].invocation
    context, reservation, grant = adapter.stage_dispatch(invocation, now_utc=BEFORE)
    consumed_grant, consumed_reservation = adapter.consume_staged(invocation, context, reservation, grant)
    with pytest.raises(ContractValidationError) as again:
        adapter.consume_staged(invocation, context, reservation, grant)
    assert again.value.errors == ("grant_already_consumed",)
    outcome = adapter.submit_dispatch(
        invocation,
        consumed_grant=consumed_grant,
        consumed_reservation=consumed_reservation,
        context=context,
        now_utc=BEFORE,
    )
    assert outcome.execution_succeeded is True
    assert worker.invocation_ids == [invocation.invocation_id]
    with pytest.raises(ContractValidationError) as duplicate:
        adapter.dispatch_invocation(invocation, now_utc=BEFORE)
    assert duplicate.value.errors == ("duplicate_dispatch",)
    assert worker.invocation_ids == [invocation.invocation_id]
    assert adapter.durable_exactly_once is False


def test_substituted_invocation_is_rejected():
    registry, record, plan = _open(_work(LIVE), (_live(),))
    worker = ScriptedTestWorker()
    adapter, _seam, _port = _wire(registry, record, {MasterSpecialist.LIVE_OPS: worker})
    invocation = plan.steps[0].invocation
    copied = MasterInvocationAuthorization.from_dict(invocation.to_dict())
    assert copied is not invocation
    assert copied.invocation_id == invocation.invocation_id
    with pytest.raises(ContractValidationError) as substituted:
        adapter.dispatch_invocation(copied, now_utc=BEFORE)
    assert substituted.value.errors == ("substituted_invocation_rejected",)
    assert worker.invocation_ids == []
    outcome = adapter.dispatch_invocation(invocation, now_utc=BEFORE)
    assert outcome.invocation_id == invocation.invocation_id
    assert worker.invocation_ids == [invocation.invocation_id]


def test_forged_grant_and_foreign_context_are_rejected():
    registry, record, plan = _open(_work(LIVE), (_live(),))
    other_registry, other_record, other_plan = _open(_work(LIVE), (_live(),))
    worker = ScriptedTestWorker()
    other_worker = ScriptedTestWorker()
    adapter, _seam, _port = _wire(registry, record, {MasterSpecialist.LIVE_OPS: worker})
    other, _other_seam, _other_port = _wire(
        other_registry,
        other_record,
        {MasterSpecialist.LIVE_OPS: other_worker},
    )
    invocation = plan.steps[0].invocation
    context = adapter.bounded_context(invocation, now_utc=BEFORE)
    with pytest.raises(ContractValidationError) as boolean_grant:
        adapter.submit_dispatch(
            invocation,
            consumed_grant=True,
            consumed_reservation=True,
            context=context,
            now_utc=BEFORE,
        )
    assert boolean_grant.value.errors == ("forged_grant_rejected",)
    with pytest.raises(ContractValidationError) as mapping_grant:
        adapter.submit_dispatch(
            invocation,
            consumed_grant={"consumed": True, "authority": "HOST_ISSUED"},
            consumed_reservation={"durable": True},
            context=context,
            now_utc=BEFORE,
        )
    assert mapping_grant.value.errors == ("forged_grant_rejected",)

    staged_context, reservation, grant = adapter.stage_dispatch(invocation, now_utc=BEFORE)
    with pytest.raises(ContractValidationError) as unconsumed:
        adapter.submit_dispatch(
            invocation,
            consumed_grant=grant,
            consumed_reservation=reservation,
            context=staged_context,
            now_utc=BEFORE,
        )
    assert unconsumed.value.errors == ("grant_not_consumed",)

    consumed_grant, consumed_reservation = adapter.consume_staged(
        invocation,
        staged_context,
        reservation,
        grant,
    )
    forged_context = replace(staged_context, region_scope="north")
    with pytest.raises(ContractValidationError) as mismatch:
        adapter.submit_dispatch(
            invocation,
            consumed_grant=consumed_grant,
            consumed_reservation=consumed_reservation,
            context=forged_context,
            now_utc=BEFORE,
        )
    assert mismatch.value.errors == ("bounded_context_mismatch",)

    other_invocation = other_plan.steps[0].invocation
    other_context, other_reservation, other_grant = other.stage_dispatch(other_invocation, now_utc=BEFORE)
    other_consumed_grant, _other_consumed_reservation = other.consume_staged(
        other_invocation,
        other_context,
        other_reservation,
        other_grant,
    )
    with pytest.raises(ContractValidationError) as foreign:
        adapter.submit_dispatch(
            invocation,
            consumed_grant=other_consumed_grant,
            consumed_reservation=consumed_reservation,
            context=staged_context,
            now_utc=BEFORE,
        )
    assert foreign.value.errors == ("forged_grant_rejected",)
    assert worker.invocation_ids == []
    assert other_worker.invocation_ids == []

    outcome = adapter.submit_dispatch(
        invocation,
        consumed_grant=consumed_grant,
        consumed_reservation=consumed_reservation,
        context=staged_context,
        now_utc=BEFORE,
    )
    assert outcome.execution_succeeded is True
    assert outcome.production_dispatch is False
    assert worker.invocation_ids == [invocation.invocation_id]


def test_failed_predecessor_skips_the_dependent_without_dispatch():
    registry, record, plan = _open(
        _work(LIVE, HISTORY, RISK),
        (
            _live(),
            _history("hist-1", start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z"),
            _decision(depends_on=("live-1",)),
        ),
        target_candidate_aircraft_id="ac-1",
    )
    steps = {step.step_id: step for step in plan.steps}
    live_worker = ScriptedTestWorker(
        {steps["live-1"].invocation.invocation_id: ScriptedWorkerReport.FAILED}  # type: ignore[attr-defined]
    )
    historical_worker = ScriptedTestWorker()
    decision_worker = ScriptedTestWorker()
    adapter, _seam, _port = _wire(
        registry,
        record,
        {
            MasterSpecialist.LIVE_OPS: live_worker,
            MasterSpecialist.HISTORICAL_ANALYTICS: historical_worker,
            MasterSpecialist.DECISION: decision_worker,
        },
    )
    wave = adapter.run_eligible(BEFORE)
    assert {item.step_id for item in wave} == {"live-1", "hist-1"}
    recorded = {item.step_id: item for item in adapter.outcomes()}
    historical = recorded["hist-1"]
    live = recorded["live-1"]
    decision = recorded["dec-1"]
    assert historical.scheduling_state is MasterSchedulingState.COMPLETED
    assert historical.execution_disposition is ExecutionDisposition.TEST_WORKER_SUCCEEDED
    assert historical.scheduling_completed is True
    assert historical.execution_succeeded is True
    assert live.scheduling_state is MasterSchedulingState.FAILED
    assert live.execution_disposition is ExecutionDisposition.TEST_WORKER_FAILED
    assert live.scheduling_completed is False
    assert live.execution_succeeded is False
    assert live.scheduling_state.value != live.execution_disposition.value
    assert decision.scheduling_state is MasterSchedulingState.SKIPPED
    assert decision.execution_disposition is ExecutionDisposition.SKIPPED_PREDECESSOR
    assert decision.execution_succeeded is False
    assert decision.reason == "dependency_failed:live-1"
    assert decision_worker.invocation_ids == []
    assert all(item.evidence_verified is False for item in recorded.values())
    with pytest.raises(ContractValidationError) as blocked:
        adapter.dispatch_invocation(steps["dec-1"].invocation, now_utc=BEFORE)  # type: ignore[attr-defined]
    assert blocked.value.errors == ("dependency_failed",)


def test_cancellation_deadline_and_timeout_stop_real_success():
    registry, record, _plan = _open(_work(LIVE, RISK), (_live(), _decision(depends_on=("live-1",))), target_candidate_aircraft_id="ac-1")
    live_worker = ScriptedTestWorker()
    decision_worker = ScriptedTestWorker()
    adapter, _seam, _port = _wire(
        registry,
        record,
        {MasterSpecialist.LIVE_OPS: live_worker, MasterSpecialist.DECISION: decision_worker},
    )
    cancelled = adapter.cancel(now_utc=BEFORE)
    assert {item.execution_disposition for item in cancelled} == {ExecutionDisposition.CANCELLED}
    assert all(item.execution_succeeded is False and item.scheduling_completed is False for item in cancelled)
    assert live_worker.invocation_ids == []
    assert decision_worker.invocation_ids == []
    with pytest.raises(ContractValidationError) as denied:
        adapter.dispatch_invocation(_plan_step(adapter, "live-1").invocation, now_utc=BEFORE)
    assert denied.value.errors == ("schedule_cancelled",)

    timed_registry, timed_record, timed_plan = _open(_work(LIVE), (_live(),))
    timed_worker = ScriptedTestWorker(
        {timed_plan.steps[0].invocation.invocation_id: ScriptedWorkerReport.TIMED_OUT}
    )
    timed_adapter, _timed_seam, _timed_port = _wire(
        timed_registry,
        timed_record,
        {MasterSpecialist.LIVE_OPS: timed_worker},
    )
    timed = timed_adapter.dispatch_invocation(timed_plan.steps[0].invocation, now_utc=BEFORE)
    assert timed.execution_disposition is ExecutionDisposition.TEST_WORKER_TIMED_OUT
    assert timed.scheduling_state is MasterSchedulingState.FAILED
    assert timed.scheduling_completed is False
    assert timed.execution_succeeded is False
    assert timed.evidence_verified is False

    expired_registry, expired_record, expired_plan = _open(
        _work(LIVE, RISK),
        (_live(), _decision(depends_on=("live-1",))),
        target_candidate_aircraft_id="ac-1",
    )
    expired_live = ScriptedTestWorker()
    expired_decision = ScriptedTestWorker()
    expired, _expired_seam, _expired_port = _wire(
        expired_registry,
        expired_record,
        {MasterSpecialist.LIVE_OPS: expired_live, MasterSpecialist.DECISION: expired_decision},
    )
    assert expired.run_eligible(DEADLINE) == ()
    expired_outcomes = {item.step_id: item for item in expired.outcomes()}
    assert set(expired_outcomes) == {"live-1", "dec-1"}
    assert all(
        item.execution_disposition is ExecutionDisposition.SKIPPED_DEADLINE for item in expired_outcomes.values()
    )
    assert expired_live.invocation_ids == []
    assert expired_decision.invocation_ids == []
    with pytest.raises(ContractValidationError) as late:
        expired.dispatch_invocation(expired_plan.steps[0].invocation, now_utc=DEADLINE)
    assert late.value.errors == ("deadline_expired",)


def _plan_step(adapter: MasterAuthorizedSchedulerAdapter, step_id: str):
    return next(step for step in adapter.fanout.plan.steps if step.step_id == step_id)


def test_partial_completion_keeps_execution_distinct_from_scheduling():
    registry, record, plan = _open(
        _work(LIVE, HISTORY, RISK),
        (
            _history("hist-1", start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z"),
            _live(),
            _decision(depends_on=("live-1",)),
        ),
        target_candidate_aircraft_id="ac-1",
    )
    steps = {step.step_id: step for step in plan.steps}
    live_worker = ScriptedTestWorker({steps["live-1"].invocation.invocation_id: ScriptedWorkerReport.FAILED})  # type: ignore[attr-defined]
    historical_worker = ScriptedTestWorker()
    decision_worker = ScriptedTestWorker()
    adapter, _seam, _port = _wire(
        registry,
        record,
        {
            MasterSpecialist.HISTORICAL_ANALYTICS: historical_worker,
            MasterSpecialist.LIVE_OPS: live_worker,
            MasterSpecialist.DECISION: decision_worker,
        },
    )
    adapter.run_to_quiescence(BEFORE)
    recorded = {item.step_id: item for item in adapter.outcomes()}
    assert recorded["hist-1"].scheduling_completed is True
    assert recorded["hist-1"].execution_succeeded is True
    assert recorded["live-1"].scheduling_completed is False
    assert recorded["live-1"].execution_disposition is ExecutionDisposition.TEST_WORKER_FAILED
    assert recorded["dec-1"].execution_disposition is ExecutionDisposition.SKIPPED_PREDECESSOR
    assert recorded["hist-1"].scheduling_state.value != recorded["hist-1"].execution_disposition.value
    assert SCHEDULING_IS_NOT_EXECUTION in adapter.limitations()
    handoff = adapter.handoff_to_ma4(BEFORE)
    assert handoff.admission_performed is False
    assert {item.execution_disposition for item in handoff.outcomes} == {
        ExecutionDisposition.TEST_WORKER_SUCCEEDED,
        ExecutionDisposition.TEST_WORKER_FAILED,
        ExecutionDisposition.SKIPPED_PREDECESSOR,
    }
    assert all(item.evidence_verified is False for item in handoff.outcomes)
    assert decision_worker.invocation_ids == []


def test_finished_schedule_hands_off_to_ma4_without_admission():
    registry, record, plan = _open(
        _work(LIVE, HISTORY, RISK),
        (
            _live(),
            _history("hist-1", start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z"),
            _history("hist-2", start="2026-01-03T00:00:00Z", end="2026-01-04T00:00:00Z", depends_on=("hist-1",)),
            _decision(depends_on=("live-1",)),
        ),
        target_candidate_aircraft_id="ac-1",
    )
    snapshot = _snapshot(plan)
    historical = ScriptedTestWorker()
    adapter, _seam, _port = _wire(
        registry,
        record,
        {
            MasterSpecialist.LIVE_OPS: ScriptedTestWorker(),
            MasterSpecialist.HISTORICAL_ANALYTICS: historical,
            MasterSpecialist.DECISION: ScriptedTestWorker(),
        },
    )
    outcomes = adapter.run_to_quiescence(BEFORE)
    assert [item.step_id for item in outcomes] == ["hist-1", "live-1", "dec-1", "hist-2"]
    assert len(outcomes) == MAX_ROUTE_STEPS
    assert sum(item.specialist is MasterSpecialist.DECISION for item in outcomes) == MAX_DECISION_STEPS
    assert adapter.dependency_edges() == (("dec-1", "live-1"), ("hist-2", "hist-1"))
    assert historical.invocation_ids == [outcomes[0].invocation_id, outcomes[3].invocation_id]
    assert outcomes[0].historical_window != outcomes[3].historical_window
    assert all(item.execution_succeeded and item.scheduling_completed for item in outcomes)
    assert all(item.correlation_id == "corr-1" and item.master_request_id == "master-req-1" for item in outcomes)
    assert all(item.session_id == record.session_id for item in outcomes)
    assert all(item.evidence_verified is False for item in outcomes)
    handoff = adapter.handoff_to_ma4(BEFORE)
    assert handoff.admission_performed is False
    assert handoff.specialist_runners_invoked is False
    assert handoff.synthetic_orchestration_used is False
    assert handoff.evidence_verified is False
    assert handoff.aviation_evidence is False
    assert handoff.production_dispatch_enabled is False
    assert handoff.durable_exactly_once is False
    assert handoff.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert handoff.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert handoff.dependency_edges == adapter.dependency_edges()
    assert handoff.outcomes == outcomes
    assert MA4_HANDOFF_NOT_ADMISSION in handoff.limitations
    assert all(item.imported is False and item.status == "CLOSED" for item in INTEGRATION_SEAMS)
    assert [item.peer for item in INTEGRATION_SEAMS] == [
        "Agent 4 host grant",
        "Agent 5 durable ledger",
        "Agent 6 execution policy permit",
    ]
    assert all(item.invoked is False and item.evidence_admitted is False for item in SPECIALIST_RUNNER_SEAMS)
    assert _snapshot(plan) == snapshot
    assert plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert plan.end_to_end_fulfilled is False


def test_missing_authority_keeps_production_dispatch_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("WILVOR_TEST_MODE", "1")
    monkeypatch.setenv("AWS_REGION", "us-west-1")
    registry, record, plan = _open(_work(LIVE), (_live(),))
    closed = MasterAuthorizedSchedulerAdapter(registry, record, deadline_utc=DEADLINE)
    assert closed.production_dispatch_enabled is False
    assert closed.describe(BEFORE)[0].descriptive_allow is True
    assert closed.describe(BEFORE)[0].usable_production_grant is False
    with pytest.raises(ContractValidationError) as missing:
        closed.run_eligible(BEFORE)
    assert missing.value.errors == ("production_dispatch_disabled",)

    seam = open_local_test_host_seam()
    grant_only = MasterAuthorizedSchedulerAdapter(
        registry,
        record,
        deadline_utc=DEADLINE,
        host_seam=seam,
    )
    with pytest.raises(ContractValidationError) as no_ledger:
        grant_only.run_eligible(BEFORE)
    assert no_ledger.value.errors == ("ledger_authority_missing",)
    assert seam.issued_count == 0

    ledger = InProcessLedgerReservationDouble()
    ledger_only = MasterAuthorizedSchedulerAdapter(
        registry,
        record,
        deadline_utc=DEADLINE,
        ledger=ledger,
    )
    with pytest.raises(ContractValidationError) as no_grant:
        ledger_only.dispatch_invocation(plan.steps[0].invocation, now_utc=BEFORE)
    assert no_grant.value.errors == ("host_grant_authority_missing",)
    assert ledger.durable_exactly_once is False
    assert ledger.production_ledger is False

    with pytest.raises(ContractValidationError) as no_port:
        MasterAuthorizedSchedulerAdapter(
            registry,
            record,
            deadline_utc=DEADLINE,
            host_seam=open_local_test_host_seam(),
            ledger=InProcessLedgerReservationDouble(),
        ).run_eligible(BEFORE)
    assert no_port.value.errors == ("dispatch_port_missing",)

    class _DurableClaim:
        durable_exactly_once = True

    with pytest.raises(ContractValidationError) as forged_ledger:
        MasterAuthorizedSchedulerAdapter(
            registry,
            record,
            deadline_utc=DEADLINE,
            host_seam=open_local_test_host_seam(),
            ledger=_DurableClaim(),  # type: ignore[arg-type]
        )
    assert forged_ledger.value.errors == ("ledger_authority_missing",)

    event = {
        "headers": {"Authorization": "Bearer forged"},
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user"}}}},
        "ENVIRONMENT": "test",
        "test_mode": True,
    }
    with pytest.raises(ContractValidationError) as activation:
        reject_untrusted_activation(event)
    assert activation.value.errors == ("production_input_cannot_open_test_seam",)
    with pytest.raises(ContractValidationError) as env_string:
        LocalTestHostAuthoritySeam("test")
    assert env_string.value.errors == ("production_input_cannot_open_test_seam",)
    with pytest.raises(ContractValidationError) as env_flag:
        LocalTestHostAuthoritySeam(True)
    assert env_flag.value.errors == ("production_input_cannot_open_test_seam",)

    submit_params = inspect.signature(MasterAuthorizedSchedulerAdapter.submit_dispatch).parameters
    assert list(submit_params) == [
        "self",
        "invocation",
        "consumed_grant",
        "consumed_reservation",
        "context",
        "now_utc",
    ]
    with pytest.raises(ContractValidationError) as callable_port:
        MasterAuthorizedSchedulerAdapter(
            registry,
            record,
            deadline_utc=DEADLINE,
            host_seam=open_local_test_host_seam(),
            ledger=InProcessLedgerReservationDouble(),
            dispatch_port=lambda *args, **kwargs: None,  # type: ignore[arg-type]
        )
    assert callable_port.value.errors == ("model_origin_dispatch_rejected",)
    port = LocalTestWorkerDispatchPort(open_local_test_host_seam(), InProcessLedgerReservationDouble())
    with pytest.raises(ContractValidationError) as callable_worker:
        port.register(MasterSpecialist.LIVE_OPS, lambda context, invocation: None)
    assert callable_worker.value.errors == ("model_origin_dispatch_rejected",)


def test_process_local_lock_is_not_distributed_exactly_once():
    registry, record, plan = _open(
        _work(LIVE, HISTORY),
        (
            _live(),
            _history("hist-1", start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z"),
        ),
    )
    live_worker = ScriptedTestWorker()
    historical_worker = ScriptedTestWorker()
    adapter, _seam, _port = _wire(
        registry,
        record,
        {
            MasterSpecialist.LIVE_OPS: live_worker,
            MasterSpecialist.HISTORICAL_ANALYTICS: historical_worker,
        },
    )
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            adapter.run_eligible(BEFORE)
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(live_worker.invocation_ids) == 1
    assert len(historical_worker.invocation_ids) == 1
    assert adapter.durable_exactly_once is False
    assert PROCESS_LOCAL_LOCK_LIMITATION in adapter.limitations()
    assert len(plan.steps) == 2


def test_budget_overrun_fails_the_attempt_without_raising_the_ceiling():
    registry, record, plan = _open(_work(LIVE), (_live(),))
    worker = ScriptedTestWorker(tool_calls_used=server_read_budget(MasterSpecialist.LIVE_OPS)[2] + 1)
    adapter, _seam, _port = _wire(registry, record, {MasterSpecialist.LIVE_OPS: worker})
    outcome = adapter.dispatch_invocation(plan.steps[0].invocation, now_utc=BEFORE)
    assert outcome.reason == "budget_exceeded"
    assert outcome.execution_succeeded is False
    assert outcome.scheduling_completed is False
    assert outcome.execution_disposition is ExecutionDisposition.TEST_WORKER_FAILED
    context = adapter.bounded_context(plan.steps[0].invocation, now_utc=BEFORE)
    assert context.max_tool_calls == server_read_budget(MasterSpecialist.LIVE_OPS)[2]
    with pytest.raises(ContractValidationError) as ceiling:
        replace(context, max_tool_calls=context.max_tool_calls + 1)
    assert ceiling.value.errors == ("server_budget_exceeded",)
