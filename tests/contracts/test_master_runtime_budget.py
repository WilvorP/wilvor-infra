"""Host budget boundaries. A within-ceiling harness result is not a grant."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.decision_specialist import MAX_INVALID_TOOL_CALL_CORRECTIONS as DECISION_RETRIES
from wilvor_ai.decision_specialist import MAX_MODEL_TURNS as DECISION_TURNS
from wilvor_ai.decision_specialist import MAX_TOOL_CALLS as DECISION_TOOLS
from wilvor_ai.historical_specialist import AI_LIST_MAX
from wilvor_ai.historical_specialist import MAX_INVALID_TOOL_CALL_CORRECTIONS as HISTORICAL_RETRIES
from wilvor_ai.historical_specialist import MAX_MODEL_TURNS as HISTORICAL_TURNS
from wilvor_ai.historical_specialist import MAX_TOOL_CALLS as HISTORICAL_TOOLS
from wilvor_ai.live_ops_model_contracts import MAX_TOOL_CALLS_PER_BATCH
from wilvor_ai.live_ops_public_contracts import (
    LIVE_OPS_HAZARD_IDS_MAX,
    LIVE_OPS_MAX_MODEL_TURNS,
    LIVE_OPS_MAX_TOOL_CALLS,
)
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_read_resource_guard import (
    HISTORICAL_LIST_MAX,
    MasterReadDenialReason,
    ReadOperation,
    server_read_budget,
)
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS,
    MAX_HISTORICAL_WINDOW,
    MAX_ROUTE_STEPS,
    MasterRouteExecutionAuthority,
)
from wilvor_ai.master_runtime_budget import (
    DEPLOYED_HOST_ENABLED,
    EMPTY_ROWS_CREATE_VERIFIED_ZERO,
    LOCAL_FAKE_JOURNAL_IS_AWS_ENFORCEMENT,
    PRODUCTION_GRANT_AVAILABLE,
    PRODUCTION_LEDGER_AVAILABLE,
    PRODUCTION_PRINCIPAL_AVAILABLE,
    PUBLISHED_INVALID_TOOL_CALL_CORRECTIONS,
    BudgetDenialReason,
    BudgetEvaluation,
    ExhaustedScope,
    StartedTaskDisposition,
    bind_deployed_host_budget,
    integration_seam,
    journal_observation_is_not_host_enforcement,
    offline_harness_seal,
    open_private_offline_harness,
    promote_budget_decision_to_production_grant,
    published_ceilings,
    RequestBudgetEnvelope,
)


LIVE = MasterSpecialist.LIVE_OPS
HISTORY = MasterSpecialist.HISTORICAL_ANALYTICS
DECISION = MasterSpecialist.DECISION
LIVE_CAPABILITY = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY_CAPABILITY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
DECISION_CAPABILITY = AgentCapability.EXPLAIN_EXISTING_RISK
ACCEPTED = "2026-10-27T20:00:00Z"
NS = 1_000_000_000


class HostClock:
    def __init__(self, utc: str = ACCEPTED, mono_ns: int = 0) -> None:
        self.utc = utc
        self.mono_ns = mono_ns

    def now_utc(self) -> str:
        return self.utc

    def monotonic_ns(self) -> int:
        return self.mono_ns

    def advance(self, seconds: int = 0, *, mono_ns: int | None = None) -> None:
        if seconds:
            parsed = datetime.strptime(self.utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            self.utc = (parsed + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if mono_ns is None:
            self.mono_ns += seconds * NS
        else:
            self.mono_ns = mono_ns


class Remaining:
    def __init__(self, milliseconds: int) -> None:
        self.milliseconds = milliseconds

    def remaining_ms(self) -> int:
        return self.milliseconds


class NaiveClock(HostClock):
    def now_utc(self) -> datetime:
        return datetime(2026, 10, 27, 20, 0, 0)


class BoomClock:
    def now_utc(self) -> str:
        raise AssertionError("deployed binder must not read a caller clock")

    def monotonic_ns(self) -> int:
        raise AssertionError("deployed binder must not read a caller clock")


def _envelope(specialist: MasterSpecialist = LIVE, **overrides: object) -> RequestBudgetEnvelope:
    capability = {
        LIVE: LIVE_CAPABILITY,
        HISTORY: HISTORY_CAPABILITY,
        DECISION: DECISION_CAPABILITY,
    }[specialist]
    values: dict[str, object] = {
        "master_request_id": "request-1",
        "planning_session_id": "plan-1",
        "step_id": "step-1",
        "invocation_id": "invoke-1",
        "specialist": specialist,
        "purpose": MasterPurpose.ANSWER,
        "capability": capability,
        "correlation_id": "correlation-1",
        "idempotency_key": "idem-1",
    }
    values.update(overrides)
    return RequestBudgetEnvelope(**values)  # type: ignore[arg-type]


def _open(
    *,
    specialist: MasterSpecialist = LIVE,
    remaining_ms: int = 5_000,
    clock: HostClock | None = None,
    envelope: RequestBudgetEnvelope | None = None,
    remaining: Remaining | None = None,
):
    return open_private_offline_harness(
        offline_harness_seal(),
        clock=clock or HostClock(),
        remaining_runtime=remaining or Remaining(remaining_ms),
        envelope=envelope or _envelope(specialist),
    )


def test_published_ceilings_match_existing_caps_and_are_not_higher():
    live = published_ceilings(LIVE)
    historical = published_ceilings(HISTORY)
    decision = published_ceilings(DECISION)
    assert server_read_budget(LIVE) == (1, LIVE_OPS_MAX_MODEL_TURNS, LIVE_OPS_MAX_TOOL_CALLS)
    assert server_read_budget(HISTORY) == (1, HISTORICAL_TURNS, HISTORICAL_TOOLS)
    assert server_read_budget(DECISION) == (1, DECISION_TURNS, DECISION_TOOLS)
    assert (live.specialist_executions, live.model_turns, live.tool_calls) == server_read_budget(LIVE)
    assert (historical.model_turns, historical.tool_calls) == (HISTORICAL_TURNS, HISTORICAL_TOOLS)
    assert (decision.model_turns, decision.tool_calls) == (DECISION_TURNS, DECISION_TOOLS)
    assert historical.data_rows == HISTORICAL_LIST_MAX == AI_LIST_MAX
    assert live.data_rows == LIVE_OPS_HAZARD_IDS_MAX
    assert decision.data_rows is None
    assert live.retries == historical.retries == decision.retries == PUBLISHED_INVALID_TOOL_CALL_CORRECTIONS
    assert PUBLISHED_INVALID_TOOL_CALL_CORRECTIONS == HISTORICAL_RETRIES == DECISION_RETRIES == 1
    assert live.request_executions == MAX_ROUTE_STEPS == 4
    assert live.decision_executions == MAX_DECISION_STEPS == 1
    assert live.live_ops_tool_batch == MAX_TOOL_CALLS_PER_BATCH
    assert historical.live_ops_tool_batch is None
    assert MAX_HISTORICAL_WINDOW == timedelta(days=7)


def test_harness_permits_one_bounded_operation_without_a_grant():
    ran: list[str] = []
    session = _open()
    assert session.accepted_at_utc == ACCEPTED
    assert session.deadline_utc == "2026-10-27T20:00:05Z"
    assert session.production_grant_issued is False
    assert session.principal_established is False
    assert session.ledger_established is False
    assert session.deployed_host_enabled is False
    assert session.clock_is_production_proof is False
    reserved = session.begin_model_turn(invocation_id="invoke-1", action_id="turn-1")
    replay = session.begin_model_turn(invocation_id="invoke-1", action_id="turn-1")
    assert replay.action_id == reserved.action_id
    assert session.usage(invocation_id="invoke-1").model_turns == 1
    result = session.run_bounded_operation("turn-1", lambda: ran.append("ok"), downstream_timeout_s=1)
    assert ran == ["ok"]
    assert result.callable_invoked is True
    assert result.bounded_operation_completed is True
    assert result.returned_value_admitted is False
    assert result.production_grant_issued is False
    assert result.source_authenticated is False
    decision = result.decision
    assert decision.evaluation is BudgetEvaluation.EVALUATED_WITHIN_CEILING
    assert decision.descriptive_allow is True
    assert decision.disposition is StartedTaskDisposition.COMPLETED_BOUNDED
    assert decision.host_issued_deadline is True
    assert decision.production_grant_issued is False
    assert decision.dispatch_permitted is False
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert decision.production_authority is MasterProductionAuthority.DENIED
    assert decision.source_authenticated is False
    assert decision.clock_is_production_proof is False
    assert decision.master_request_id == "request-1"
    assert decision.deadline_utc == session.deadline_utc
    with pytest.raises(ContractValidationError, match="descriptive_allow_is_not_a_production_grant"):
        bool(decision)
    with pytest.raises(ContractValidationError, match="descriptive_allow_is_not_a_production_grant"):
        promote_budget_decision_to_production_grant(decision)


def test_deadline_boundary_uses_host_clock_and_remaining_runtime():
    clock = HostClock()
    session = _open(clock=clock, remaining_ms=2_000)
    assert session.deadline_utc == "2026-10-27T20:00:02Z"
    clock.advance(1)
    allowed = session.begin_model_turn(invocation_id="invoke-1", action_id="before-deadline")
    assert allowed.evaluation is BudgetEvaluation.EVALUATED_WITHIN_CEILING
    clock.advance(1)
    expired = session.begin_model_turn(invocation_id="invoke-1", action_id="at-deadline")
    assert expired.evaluation is BudgetEvaluation.DEADLINE_EXPIRED
    assert expired.disposition is StartedTaskDisposition.NOT_STARTED
    assert expired.denial_reasons == (BudgetDenialReason.DEADLINE_EXPIRED,)
    assert BudgetDenialReason.DEADLINE_EXPIRED.value == MasterReadDenialReason.DEADLINE_EXPIRED.value
    assert session.usage(invocation_id="invoke-1").model_turns == 1

    monotonic = HostClock()
    monotonic_session = _open(clock=monotonic, remaining_ms=2_000)
    monotonic.advance(1, mono_ns=2 * NS - 1)
    under = monotonic_session.begin_tool_calls(invocation_id="invoke-1", action_id="under-mono", count=1)
    assert under.evaluation is BudgetEvaluation.EVALUATED_WITHIN_CEILING
    at_budget = HostClock()
    at_session = _open(clock=at_budget, remaining_ms=2_000)
    at_budget.advance(1, mono_ns=2 * NS)
    exact = at_session.begin_tool_calls(invocation_id="invoke-1", action_id="exact-mono", count=1)
    assert exact.evaluation is BudgetEvaluation.DEADLINE_EXPIRED
    assert exact.consumed_by_this_action == ()


def test_monotonic_clock_is_not_utc_subtraction_and_drift_stops_new_work():
    clock = HostClock()
    session = _open(clock=clock, remaining_ms=30_000)
    clock.advance(0, mono_ns=500_000_000)
    moved = session.begin_model_turn(invocation_id="invoke-1", action_id="half-second")
    assert moved.elapsed_monotonic_ns == 500_000_000
    assert moved.accepted_at_utc == ACCEPTED
    clock.mono_ns = -1
    with pytest.raises(ContractValidationError, match="invalid_monotonic_clock"):
        session.begin_model_turn(invocation_id="invoke-1", action_id="bad-mono")
    clock.mono_ns = 100
    regressed = session.begin_model_turn(invocation_id="invoke-1", action_id="regressed")
    assert regressed.evaluation is BudgetEvaluation.MONOTONIC_REGRESSION
    assert session.usage(invocation_id="invoke-1").model_turns == 1

    drifted = HostClock()
    drift_session = _open(clock=drifted, remaining_ms=30_000)
    drifted.advance(10, mono_ns=0)
    utc_drift = drift_session.begin_model_turn(invocation_id="invoke-1", action_id="utc-drift")
    assert utc_drift.evaluation is BudgetEvaluation.CLOCK_DRIFT
    assert drift_session.usage(invocation_id="invoke-1").model_turns == 0

    other = HostClock()
    other_session = _open(clock=other, remaining_ms=30_000)
    other.advance(0, mono_ns=10 * NS)
    mono_drift = other_session.begin_read(
        invocation_id="invoke-1",
        action_id="mono-drift",
        tool_name="search_current_hazards",
        operation=ReadOperation.QUERY,
    )
    assert mono_drift.evaluation is BudgetEvaluation.CLOCK_DRIFT
    assert other_session.usage(invocation_id="invoke-1").tool_calls == 0


def test_concurrent_increments_cannot_pass_the_shared_tool_cap():
    session = _open(remaining_ms=30_000)
    workers = LIVE_OPS_MAX_TOOL_CALLS * 2

    def consume(index: int):
        return session.begin_tool_calls(
            invocation_id="invoke-1",
            action_id=f"tool-{index}",
            count=1,
        ).evaluation

    with ThreadPoolExecutor(max_workers=workers) as pool:
        evaluations = tuple(pool.map(consume, range(workers)))
    allowed = sum(item is BudgetEvaluation.EVALUATED_WITHIN_CEILING for item in evaluations)
    exhausted = sum(item is BudgetEvaluation.EXHAUSTED for item in evaluations)
    assert allowed == LIVE_OPS_MAX_TOOL_CALLS
    assert exhausted == LIVE_OPS_MAX_TOOL_CALLS
    assert session.usage(invocation_id="invoke-1").tool_calls == LIVE_OPS_MAX_TOOL_CALLS
    assert session.usage(specialist=LIVE).tool_calls == LIVE_OPS_MAX_TOOL_CALLS


def test_cancellation_before_entry_and_during_an_in_flight_call():
    session = _open()
    session.cancel()
    denied = session.begin_specialist_execution(invocation_id="invoke-1", action_id="exec-cancelled")
    assert denied.evaluation is BudgetEvaluation.CANCELLED
    assert denied.disposition is StartedTaskDisposition.NOT_STARTED
    assert session.usage(invocation_id="invoke-1").specialist_executions == 0
    read = session.begin_read(
        invocation_id="invoke-1",
        action_id="read-cancelled",
        tool_name="search_current_hazards",
        operation=ReadOperation.QUERY,
    )
    assert read.evaluation is BudgetEvaluation.CANCELLED
    assert session.usage(invocation_id="invoke-1").tool_calls == 0

    running = _open()
    reserved = running.begin_model_turn(invocation_id="invoke-1", action_id="in-flight")
    assert reserved.descriptive_allow is True
    entered = threading.Event()
    release = threading.Event()
    holder: dict[str, object] = {}

    def operation() -> None:
        entered.set()
        assert release.wait(5)

    def runner() -> None:
        try:
            holder["result"] = running.run_bounded_operation(
                "in-flight",
                operation,
                downstream_timeout_s=1,
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            holder["error"] = exc

    worker = threading.Thread(target=runner)
    worker.start()
    assert entered.wait(5)
    running.cancel()
    release.set()
    worker.join(5)
    assert worker.is_alive() is False
    assert "error" not in holder
    result = holder["result"]
    assert result.callable_invoked is True
    assert result.bounded_operation_completed is False
    assert result.decision.disposition is StartedTaskDisposition.TERMINAL_PARTIAL
    assert result.decision.evaluation is BudgetEvaluation.CANCELLED
    assert result.production_grant_issued is False
    follow = running.begin_read(
        invocation_id="invoke-1",
        action_id="after-cancel",
        tool_name="search_current_hazards",
        operation=ReadOperation.QUERY,
    )
    assert follow.evaluation is BudgetEvaluation.CANCELLED
    assert running.usage(invocation_id="invoke-1").model_turns == 1
    assert running.usage(invocation_id="invoke-1").tool_calls == 0


def test_started_work_that_passes_the_deadline_is_terminal_partial():
    clock = HostClock()
    session = _open(clock=clock, remaining_ms=5_000)
    session.begin_tool_calls(invocation_id="invoke-1", action_id="slow-read", count=1)

    def operation() -> None:
        clock.advance(5)

    result = session.run_bounded_operation("slow-read", operation, downstream_timeout_s=1)
    assert result.callable_invoked is True
    assert result.bounded_operation_completed is False
    assert result.decision.disposition is StartedTaskDisposition.TERMINAL_PARTIAL
    assert result.decision.evaluation is BudgetEvaluation.DEADLINE_EXPIRED
    assert result.returned_value_admitted is False


def test_per_specialist_ceilings_and_exhaustion_propagation():
    session = _open(specialist=HISTORY, remaining_ms=30_000)
    session.register_invocation(
        step_id="step-2",
        invocation_id="invoke-2",
        specialist=HISTORY,
        purpose=MasterPurpose.ANSWER,
        capability=HISTORY_CAPABILITY,
    )
    session.register_invocation(
        step_id="step-3",
        invocation_id="invoke-live",
        specialist=LIVE,
        purpose=MasterPurpose.ANSWER,
        capability=LIVE_CAPABILITY,
    )
    first = session.begin_tool_calls(invocation_id="invoke-1", action_id="hist-tools", count=HISTORICAL_TOOLS)
    assert first.evaluation is BudgetEvaluation.EVALUATED_WITHIN_CEILING
    propagated = session.begin_tool_calls(invocation_id="invoke-2", action_id="hist-more", count=1)
    assert propagated.evaluation is BudgetEvaluation.EXHAUSTED
    assert propagated.exhausted_scope is ExhaustedScope.SPECIALIST
    assert propagated.denial_reasons == (BudgetDenialReason.BUDGET_EXHAUSTED,)
    assert session.usage(invocation_id="invoke-2").tool_calls == 0
    live = session.begin_tool_calls(invocation_id="invoke-live", action_id="live-tool", count=1)
    assert live.evaluation is BudgetEvaluation.EVALUATED_WITHIN_CEILING
    batch = session.begin_tool_calls(
        invocation_id="invoke-live",
        action_id="live-batch",
        count=MAX_TOOL_CALLS_PER_BATCH + 1,
    )
    assert batch.evaluation is BudgetEvaluation.DENIED
    assert batch.denial_reasons == (BudgetDenialReason.BATCH_CEILING_EXCEEDED,)
    assert session.usage(invocation_id="invoke-live").tool_calls == 1

    turns = session.begin_model_turn(
        invocation_id="invoke-1",
        action_id="hist-turns",
        count=HISTORICAL_TURNS,
    )
    assert turns.evaluation is BudgetEvaluation.EVALUATED_WITHIN_CEILING
    extra_turn = session.begin_model_turn(invocation_id="invoke-2", action_id="hist-turn-2", count=1)
    assert extra_turn.exhausted_scope is ExhaustedScope.SPECIALIST
    assert session.usage(specialist=HISTORY).model_turns == HISTORICAL_TURNS


def test_row_pagination_and_route_caps_use_published_limits():
    session = _open(specialist=HISTORY, remaining_ms=30_000)
    filled = session.begin_read(
        invocation_id="invoke-1",
        action_id="rows-full",
        tool_name="list_historical_encounters",
        operation=ReadOperation.QUERY,
        rows=HISTORICAL_LIST_MAX,
    )
    assert filled.evaluation is BudgetEvaluation.EVALUATED_WITHIN_CEILING
    overflow = session.begin_read(
        invocation_id="invoke-1",
        action_id="rows-over",
        tool_name="list_historical_encounters",
        operation=ReadOperation.QUERY,
        rows=1,
    )
    assert overflow.evaluation is BudgetEvaluation.EXHAUSTED
    assert session.usage(invocation_id="invoke-1").data_rows == HISTORICAL_LIST_MAX
    pages = _open(specialist=HISTORY, remaining_ms=30_000)
    too_many_pages = pages.begin_read(
        invocation_id="invoke-1",
        action_id="pages",
        tool_name="list_historical_encounters",
        operation=ReadOperation.QUERY,
        pagination_pages=HISTORICAL_TOOLS + 1,
    )
    assert too_many_pages.evaluation is BudgetEvaluation.EXHAUSTED
    assert pages.usage(invocation_id="invoke-1").tool_calls == 0

    decision = _open(specialist=DECISION, remaining_ms=30_000)
    unpublished = decision.begin_read(
        invocation_id="invoke-1",
        action_id="decision-rows",
        tool_name="get_current_decision_context",
        operation=ReadOperation.GET_ITEM,
        rows=1,
        resource_id="aircraft",
    )
    assert unpublished.denial_reasons == (BudgetDenialReason.ROW_CEILING_NOT_PUBLISHED,)
    assert decision.usage(invocation_id="invoke-1").tool_calls == 0
    with pytest.raises(ContractValidationError, match="decision_fanout_not_permitted"):
        decision.register_invocation(
            step_id="step-decision-2",
            invocation_id="invoke-decision-2",
            specialist=DECISION,
            purpose=MasterPurpose.ANSWER,
            capability=DECISION_CAPABILITY,
        )

    route = _open(remaining_ms=30_000)
    route.register_invocation(
        step_id="step-h",
        invocation_id="invoke-h",
        specialist=HISTORY,
        purpose=MasterPurpose.ANSWER,
        capability=HISTORY_CAPABILITY,
    )
    route.register_invocation(
        step_id="step-d",
        invocation_id="invoke-d",
        specialist=DECISION,
        purpose=MasterPurpose.ANSWER,
        capability=DECISION_CAPABILITY,
    )
    route.register_invocation(
        step_id="step-live-2",
        invocation_id="invoke-live-2",
        specialist=LIVE,
        purpose=MasterPurpose.ANSWER,
        capability=LIVE_CAPABILITY,
    )
    for invocation_id, action_id in (
        ("invoke-1", "exec-1"),
        ("invoke-h", "exec-h"),
        ("invoke-d", "exec-d"),
        ("invoke-live-2", "exec-live-2"),
    ):
        decision_result = route.begin_specialist_execution(invocation_id=invocation_id, action_id=action_id)
        assert decision_result.evaluation is BudgetEvaluation.EVALUATED_WITHIN_CEILING
    assert route.usage().specialist_executions == MAX_ROUTE_STEPS
    with pytest.raises(ContractValidationError, match="excessive_plan_size"):
        route.register_invocation(
            step_id="step-5",
            invocation_id="invoke-5",
            specialist=LIVE,
            purpose=MasterPurpose.ANSWER,
            capability=LIVE_CAPABILITY,
        )


def test_retry_does_not_reset_and_deadline_mutations_are_rejected():
    clock = HostClock()
    remaining = Remaining(5_000)
    session = _open(clock=clock, remaining=remaining)
    session.begin_model_turn(invocation_id="invoke-1", action_id="turn")
    session.begin_tool_calls(invocation_id="invoke-1", action_id="tool", count=1)
    retry = session.begin_retry(invocation_id="invoke-1", action_id="retry-1")
    assert retry.evaluation is BudgetEvaluation.EVALUATED_WITHIN_CEILING
    usage = session.usage(invocation_id="invoke-1")
    assert usage.model_turns == 1
    assert usage.tool_calls == 1
    assert usage.retries == 1
    again = session.begin_retry(invocation_id="invoke-1", action_id="retry-2")
    assert again.exhausted_scope is ExhaustedScope.INVOCATION
    with pytest.raises(ContractValidationError, match="counter_reset_rejected"):
        session.reset_for_retry("invoke-1")
    assert session.usage(invocation_id="invoke-1") == usage
    deadline = session.deadline_utc
    with pytest.raises(ContractValidationError, match="deadline_reissue_rejected"):
        session.reissue_deadline("2026-10-27T20:00:09Z")
    with pytest.raises(ContractValidationError, match="timeout_move_backwards_rejected"):
        session.move_timeout_backwards("2026-10-27T20:00:01Z")
    remaining.milliseconds = 1
    assert session.deadline_utc == deadline
    assert session.issued_remaining_ms == 5_000
    with pytest.raises(ContractValidationError, match="journal_clock_is_not_host_enforcement"):
        session.adopt_journal_clock_as_deadline(ACCEPTED, "2026-10-27T20:00:01Z", 1)
    assert session.deadline_utc == deadline
    refusal = journal_observation_is_not_host_enforcement(ACCEPTED, "2026-10-27T20:00:01Z", 1)
    assert refusal.accepted is False
    assert LOCAL_FAKE_JOURNAL_IS_AWS_ENFORCEMENT is False


def test_invalid_counters_naive_datetimes_and_caller_clocks_are_rejected():
    session = _open()
    with pytest.raises(ContractValidationError, match="invalid_counter"):
        session.begin_tool_calls(invocation_id="invoke-1", action_id="bool-count", count=True)  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="invalid_counter"):
        session.begin_model_turn(invocation_id="invoke-1", action_id="negative", count=-1)
    assert session.usage(invocation_id="invoke-1").model_turns == 0
    with pytest.raises(ContractValidationError, match="naive_datetime_rejected"):
        _open(clock=NaiveClock())
    with pytest.raises(ContractValidationError, match="invalid_counter"):
        _open(remaining=Remaining(True))  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="offline_harness_seal_required"):
        open_private_offline_harness(
            {"requestContext": {"timeEpoch": 1_700_000_000_000}},
            clock=HostClock(),
            remaining_runtime=Remaining(5_000),
            envelope=_envelope(),
        )
    hinted = _open(
        envelope=_envelope(
            accepted_at_utc="2026-10-27T19:00:00Z",
            deadline_utc="2026-10-27T20:00:01Z",
            max_tool_calls=LIVE_OPS_MAX_TOOL_CALLS + 1,
        )
    )
    assert hinted.accepted_at_utc == ACCEPTED
    assert hinted.deadline_utc == "2026-10-27T20:00:05Z"
    blocked = hinted.begin_model_turn(invocation_id="invoke-1", action_id="caller-budget")
    assert BudgetDenialReason.CALLER_BUDGET_NOT_AUTHORITY in blocked.denial_reasons
    assert hinted.usage(invocation_id="invoke-1").model_turns == 0


def test_one_invocation_failure_does_not_corrupt_another():
    session = _open(remaining_ms=30_000)
    session.register_invocation(
        step_id="step-h",
        invocation_id="invoke-h",
        specialist=HISTORY,
        purpose=MasterPurpose.ANSWER,
        capability=HISTORY_CAPABILITY,
    )
    session.begin_model_turn(invocation_id="invoke-1", action_id="live-turn")
    session.begin_model_turn(invocation_id="invoke-h", action_id="hist-turn")
    before = session.usage(invocation_id="invoke-h")

    def fail() -> None:
        raise RuntimeError("specialist failed")

    with pytest.raises(RuntimeError, match="specialist failed"):
        session.run_bounded_operation("live-turn", fail, downstream_timeout_s=1)
    assert session.usage(invocation_id="invoke-h") == before
    assert session.usage(invocation_id="invoke-1").model_turns == 1
    failed = session.decision_for("live-turn")
    assert failed.disposition is StartedTaskDisposition.FAILED
    assert failed.invocation_id == "invoke-1"
    follow = session.begin_model_turn(invocation_id="invoke-h", action_id="hist-turn-2")
    assert follow.evaluation is BudgetEvaluation.EVALUATED_WITHIN_CEILING
    assert session.usage(invocation_id="invoke-h").model_turns == 2
    entered = session.mark_entered("hist-turn", downstream_timeout_s=1)
    assert entered.disposition is StartedTaskDisposition.STARTED
    isolated = session.finish_started("hist-turn", failed=True)
    assert isolated.disposition is StartedTaskDisposition.FAILED
    assert isolated.invocation_id == "invoke-h"
    assert session.decision_for("live-turn").disposition is StartedTaskDisposition.FAILED
    assert session.usage(invocation_id="invoke-1").model_turns == 1


def test_reads_reject_scan_and_source_time_does_not_move_the_deadline():
    session = _open()
    scanned = session.begin_read(
        invocation_id="invoke-1",
        action_id="scan",
        tool_name="search_current_hazards",
        operation=ReadOperation.SCAN,
    )
    assert scanned.denial_reasons == (BudgetDenialReason.SCAN_NOT_PERMITTED,)
    assert scanned.denial_reasons[0].value == MasterReadDenialReason.SCAN_NOT_PERMITTED.value
    unknown = session.begin_read(
        invocation_id="invoke-1",
        action_id="unknown-tool",
        tool_name="scan_all",
        operation=ReadOperation.QUERY,
    )
    assert unknown.denial_reasons == (BudgetDenialReason.UNSUPPORTED_TOOL,)
    bad_argument = session.begin_read(
        invocation_id="invoke-1",
        action_id="bad-arg",
        tool_name="search_current_hazards",
        operation=ReadOperation.QUERY,
        arguments=(("table", "aircraft"),),
    )
    assert bad_argument.denial_reasons == (BudgetDenialReason.ARGUMENT_NOT_ALLOWLISTED,)
    assert session.usage(invocation_id="invoke-1").tool_calls == 0
    empty = session.begin_read(
        invocation_id="invoke-1",
        action_id="empty",
        tool_name="search_current_hazards",
        operation=ReadOperation.QUERY,
        rows=0,
        resource_id="hazards",
    )
    assert empty.verified_zero_claim is False
    assert EMPTY_ROWS_CREATE_VERIFIED_ZERO is False
    deadline = session.deadline_utc
    accepted = session.accepted_at_utc
    observation = session.record_source_observation(
        invocation_id="invoke-1",
        observed_at_utc="2026-10-27T23:59:59Z",
        rows=0,
        pagination_pages=1,
    )
    assert observation.source_authenticated is False
    assert observation.verified_zero_claim is False
    assert session.deadline_utc == deadline
    assert session.accepted_at_utc == accepted
    assert session.decision_for("empty").source_observed_at_utc == "2026-10-27T23:59:59Z"
    with pytest.raises(ContractValidationError, match="naive_datetime_rejected"):
        session.record_source_observation(
            invocation_id="invoke-1",
            observed_at_utc=datetime(2026, 10, 27, 21, 0, 0),  # type: ignore[arg-type]
        )


def test_deployed_host_stays_disabled_and_seams_are_explicit():
    event = {
        "requestContext": {
            "timeEpoch": 1_700_000_000_000,
            "authorizer": {"jwt": {"claims": {"sub": "user-1"}}},
        },
        "headers": {"Authorization": "Bearer forged"},
    }
    bound = bind_deployed_host_budget(event, BoomClock(), Remaining(1))
    assert bound.enabled is False
    assert bound.production_grant_issued is False
    assert bound.principal_established is False
    assert bound.ledger_established is False
    assert bound.accepted_at_utc is None
    assert bound.deadline_utc is None
    assert bound.untrusted_input_ignored is True
    assert bound.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert bound.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert "lambda:InvokeFunction" in bound.invoke_boundary
    assert "timeEpoch" in bound.invoke_boundary
    with pytest.raises(ContractValidationError, match="deployed_host_budget_is_not_a_grant"):
        bool(bound)
    assert DEPLOYED_HOST_ENABLED is False
    assert PRODUCTION_GRANT_AVAILABLE is False
    assert PRODUCTION_PRINCIPAL_AVAILABLE is False
    assert PRODUCTION_LEDGER_AVAILABLE is False
    seam = integration_seam()
    assert "MasterRuntimeReadJournal.execute" in seam.read_executor
    assert "timeout_budget_seconds" in seam.read_executor
    assert "not AWS Lambda" in seam.read_executor
    assert "server_read_tools" in seam.read_executor
    assert "TERMINAL_PARTIAL" in seam.read_executor
    assert "describe_planned_adapters" in seam.ma3_adapter
    assert "begin_specialist_execution" in seam.ma3_adapter
    assert "begin_retry" in seam.ma3_adapter
    assert "MAX_TOOL_CALLS_PER_BATCH" in seam.ma3_adapter
    assert seam.journal_deadline_is_aws_enforcement is False
    assert seam.production_grant_issued is False
    with pytest.raises(ContractValidationError, match="prohibited_capability"):
        _envelope(capability=AgentCapability.ISSUE_AUTONOMOUS_FLIGHT_CONTROL_INSTRUCTIONS)


def test_historical_window_over_seven_days_does_not_stop_other_specialists():
    session = _open(
        envelope=_envelope(
            historical_window=("2026-01-01T00:00:00Z", "2026-01-09T00:00:00Z"),
        )
    )
    session.register_invocation(
        step_id="step-h",
        invocation_id="invoke-h",
        specialist=HISTORY,
        purpose=MasterPurpose.ANSWER,
        capability=HISTORY_CAPABILITY,
    )
    live = session.begin_model_turn(invocation_id="invoke-1", action_id="live-ok")
    assert live.evaluation is BudgetEvaluation.EVALUATED_WITHIN_CEILING
    historical = session.begin_specialist_execution(invocation_id="invoke-h", action_id="hist-window")
    assert historical.denial_reasons == (BudgetDenialReason.HISTORICAL_INTERVAL_ESCALATION,)
    assert session.usage(invocation_id="invoke-h").specialist_executions == 0


def test_mark_entered_requires_a_downstream_timeout_and_stops_before_the_call():
    session = _open()
    session.begin_read(
        invocation_id="invoke-1",
        action_id="read-1",
        tool_name="search_current_hazards",
        operation=ReadOperation.QUERY,
    )
    session.cancel()
    stopped = session.mark_entered("read-1", downstream_timeout_s=1)
    assert stopped.disposition is StartedTaskDisposition.CANCELLED_BEFORE_START
    with pytest.raises(ContractValidationError, match="action_not_started"):
        session.finish_started("read-1")
    with pytest.raises(ContractValidationError, match="invalid_counter"):
        session.mark_entered("read-1", downstream_timeout_s=False)  # type: ignore[arg-type]
