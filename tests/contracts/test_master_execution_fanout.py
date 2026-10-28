"""Scheduling tests for every invocation on one retained Master plan.

The fake worker below is test-only. Its return value is not passed into the
scheduler and is not aviation evidence. Object identity checks registry
membership of the original invocation inside this process. It is not
production authentication or execution authorization.
"""

from __future__ import annotations

import inspect
import threading
from dataclasses import replace

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_execution_fanout import (
    MASTER_EXECUTION_FANOUT_SCHEMA_VERSION,
    MAX_DECISION_STEPS,
    MAX_ROUTE_STEPS,
    PLANNING_ELIGIBILITY_LIMITATION,
    SCHEDULING_COMPLETION_LIMITATION,
    MasterExecutionClaim,
    MasterExecutionFanout,
    MasterExecutionScheduleStatus,
    MasterSchedulingObservation,
    MasterSchedulingState,
)
from wilvor_ai.master_planning_session import (
    MasterPlanningSessionRecord,
    MasterPlanningSessionRegistry,
)
from wilvor_ai.master_routing import (
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
AT_DEADLINE = "2026-01-15T00:30:00Z"


class _FakeSchedulingWorker:
    """Visible test double. It cannot authenticate a source or verify evidence."""

    test_only = True

    def run(self, invocation_id: str) -> dict[str, object]:
        return {
            "test_only": True,
            "invocation_id": invocation_id,
            "evidence_verified": False,
            "aviation_evidence": False,
        }


def _context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "correlation_id": "corr-1",
        "application_request_time_utc": "2026-01-15T00:00:01Z",
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
    start: str = "2026-01-01T00:00:00Z",
    end: str = "2026-01-02T00:00:00Z",
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
    context = _context(**context_overrides)
    proposal = MasterRouteProposal(user_text=TEXT, capabilities=capabilities, steps=steps)
    record = registry.open_session(proposal, context)
    decision = registry.decision_for(record)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    return registry, record, decision.plan


def _fanout(
    registry: MasterPlanningSessionRegistry,
    record: MasterPlanningSessionRecord,
    deadline: str = DEADLINE,
) -> MasterExecutionFanout:
    return MasterExecutionFanout(registry, record, deadline_utc=deadline)


def _snapshot(plan: MasterRoutePlan) -> tuple[object, ...]:
    return (
        plan.to_dict(),
        plan.steps,
        tuple(step.invocation for step in plan.steps),
        tuple(step.ready_to_execute for step in plan.steps),
        tuple(step.gates for step in plan.steps),
        tuple(step.depends_on for step in plan.steps),
    )


def _assert_plan_untouched(
    plan: MasterRoutePlan,
    snapshot: tuple[object, ...],
    registry: MasterPlanningSessionRegistry,
    record: MasterPlanningSessionRecord,
) -> None:
    payload, steps, invocations, ready, gates, edges = snapshot
    assert registry.plan_for(record) is plan
    assert plan.to_dict() == payload
    assert plan.steps is steps
    assert tuple(step.invocation for step in plan.steps) == invocations
    assert tuple(step.ready_to_execute for step in plan.steps) == ready
    assert tuple(step.gates for step in plan.steps) == gates
    assert tuple(step.depends_on for step in plan.steps) == edges
    assert plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert plan.end_to_end_fulfilled is False


def _by_step(fanout: MasterExecutionFanout, now: str = BEFORE) -> dict[str, MasterSchedulingObservation]:
    return {item.step_id: item for item in fanout.observations(now)}


def test_single_invocation_schedules_without_granting_execution():
    assert [item.value for item in MasterSchedulingState] == [
        "PENDING",
        "ELIGIBLE",
        "IN_PROGRESS",
        "COMPLETED",
        "FAILED",
        "SKIPPED",
        "CANCELLED",
    ]
    assert MASTER_EXECUTION_FANOUT_SCHEMA_VERSION == "wilvor.ai.master_execution_fanout.v1"
    assert MAX_ROUTE_STEPS == 4
    assert MAX_DECISION_STEPS == 1
    assert not hasattr(MasterExecutionFanout, "open_session")
    assert not hasattr(MasterExecutionFanout, "associate_invocation")
    assert not hasattr(MasterExecutionFanout, "dispatch")
    assert not hasattr(MasterExecutionFanout, "execute")
    init_params = inspect.signature(MasterExecutionFanout.__init__).parameters
    claim_params = inspect.signature(MasterExecutionFanout.claim).parameters
    complete_params = inspect.signature(MasterExecutionFanout.complete).parameters
    assert list(init_params) == ["self", "registry", "record", "deadline_utc"]
    assert list(claim_params) == ["self", "invocation", "claimer_id", "now_utc"]
    assert "result" not in complete_params
    assert "worker" not in complete_params
    assert "authorized" not in claim_params
    assert "result" not in MasterSchedulingObservation.__dataclass_fields__

    registry, record, plan = _open(_work(LIVE), (_live(),))
    snapshot = _snapshot(plan)
    fanout = _fanout(registry, record)
    invocation = plan.steps[0].invocation
    assert fanout.session_id == record.session_id
    assert fanout.session_id != plan.master_request_id
    assert fanout.plan is plan
    assert fanout.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert fanout.production_authority is MasterProductionAuthority.DENIED
    assert fanout.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert fanout.evidence_verified is False
    assert fanout.execution_authorized is False
    assert fanout.authorization_decision_required is True
    assert fanout.limitations() == (PLANNING_ELIGIBILITY_LIMITATION, SCHEDULING_COMPLETION_LIMITATION)
    assert fanout.max_route_steps is MAX_ROUTE_STEPS
    assert fanout.max_decision_steps is MAX_DECISION_STEPS
    with pytest.raises(ContractValidationError) as missing:
        registry.selected_invocation(record)
    assert missing.value.errors == ("invalid_session_state",)

    eligible = fanout.eligible(BEFORE)
    assert [item.step_id for item in eligible] == ["live-1"]
    assert eligible[0].state is MasterSchedulingState.ELIGIBLE
    assert eligible[0].planning_ready_to_execute is True
    assert eligible[0].execution_authorized is False
    assert eligible[0].evidence_verified is False
    assert eligible[0].authorization_decision_required is True
    assert SCHEDULING_COMPLETION_LIMITATION in eligible[0].limitations
    assert "not verified aviation evidence" in SCHEDULING_COMPLETION_LIMITATION

    claim = fanout.claim(invocation, "claimer-1", now_utc=BEFORE)
    assert claim.execution_authorized is False
    assert claim.evidence_verified is False
    assert claim.authorization_decision_required is True
    assert claim.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert claim.session_id == record.session_id
    assert _by_step(fanout)["live-1"].state is MasterSchedulingState.IN_PROGRESS
    with pytest.raises(ContractValidationError) as mismatch:
        fanout.complete(invocation, "other-claimer", now_utc=BEFORE)
    assert mismatch.value.errors == ("claimer_mismatch",)

    fake = _FakeSchedulingWorker().run(claim.invocation_id)
    assert fake["test_only"] is True
    assert fake["evidence_verified"] is False
    assert fake["aviation_evidence"] is False
    fanout.complete(invocation, "claimer-1", now_utc=BEFORE)
    finished = _by_step(fanout)["live-1"]
    assert finished.state is MasterSchedulingState.COMPLETED
    assert finished.evidence_verified is False
    assert finished.execution_authorized is False
    assert fanout.evidence_verified is False
    assert fanout.status is MasterExecutionScheduleStatus.CLOSED
    assert not hasattr(fanout, "results")
    _assert_plan_untouched(plan, snapshot, registry, record)


def test_live_ops_followed_by_decision_keeps_authorization_separate():
    registry, record, plan = _open(
        _work(LIVE, RISK),
        (_live(), _decision(depends_on=("live-1",))),
        target_candidate_aircraft_id="ac-1",
    )
    snapshot = _snapshot(plan)
    fanout = _fanout(registry, record)
    live = plan.steps[0]
    decision = plan.steps[1]
    assert live.invocation.specialist is MasterSpecialist.LIVE_OPS
    assert decision.invocation.specialist is MasterSpecialist.DECISION
    assert live.invocation is not decision.invocation
    assert live.invocation.invocation_id != decision.invocation.invocation_id
    assert decision.ready_to_execute is False
    assert MasterRouteGate.DEPENDENCY_BLOCKED in decision.gates
    assert fanout.dependency_edges() == (("dec-1", "live-1"),)
    assert [item.step_id for item in fanout.eligible(BEFORE)] == ["live-1"]
    assert _by_step(fanout)["dec-1"].state is MasterSchedulingState.PENDING

    with pytest.raises(ContractValidationError) as blocked:
        fanout.claim(decision.invocation, "claimer-decision", now_utc=BEFORE)
    assert blocked.value.errors == ("step_not_eligible",)

    live_claim = fanout.claim(live.invocation, "claimer-live", now_utc=BEFORE)
    assert live_claim.planning_ready_to_execute is True
    assert live_claim.execution_authorized is False
    fake = _FakeSchedulingWorker().run(live_claim.invocation_id)
    assert fake["aviation_evidence"] is False
    fanout.complete(live.invocation, "claimer-live", now_utc=BEFORE)

    ready = _by_step(fanout)["dec-1"]
    assert ready.state is MasterSchedulingState.ELIGIBLE
    assert ready.planning_ready_to_execute is False
    assert MasterRouteGate.DEPENDENCY_BLOCKED in ready.planning_gates
    assert ready.execution_authorized is False
    assert ready.authorization_decision_required is True
    assert decision.ready_to_execute is False
    decision_claim = fanout.claim(decision.invocation, "claimer-decision", now_utc=BEFORE)
    assert decision_claim.execution_authorized is False
    assert decision_claim.authorization_decision_required is True
    assert decision_claim.evidence_verified is False
    assert decision_claim.planning_ready_to_execute is False
    assert decision_claim.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert fanout.remaining_decision_claims() == 0
    assert fanout.execution_authorized is False
    _assert_plan_untouched(plan, snapshot, registry, record)


def test_parallel_independent_steps_stay_eligible_together():
    registry, record, plan = _open(_work(HISTORY, LIVE), (_history("hist-1"), _live()))
    snapshot = _snapshot(plan)
    fanout = _fanout(registry, record)
    assert [item.step_id for item in fanout.eligible(BEFORE)] == [step.step_id for step in plan.steps]
    assert len(fanout.eligible(BEFORE)) == 2
    claims = []
    for step in plan.steps:
        claims.append(fanout.claim(step.invocation, f"claimer-{step.step_id}", now_utc=BEFORE))
    assert [item.execution_authorized for item in claims] == [False, False]
    assert [item.state for item in fanout.observations(BEFORE)] == [
        MasterSchedulingState.IN_PROGRESS,
        MasterSchedulingState.IN_PROGRESS,
    ]
    assert claims[0].invocation_id != claims[1].invocation_id
    with pytest.raises(ContractValidationError) as missing:
        registry.selected_invocation(record)
    assert missing.value.errors == ("invalid_session_state",)
    selected = registry.associate_invocation(record, plan.steps[0].invocation)
    assert selected is plan.steps[0].invocation
    with pytest.raises(ContractValidationError) as second:
        registry.associate_invocation(record, plan.steps[1].invocation)
    assert second.value.errors == ("multiple_invocations_rejected",)
    assert [item.state for item in fanout.observations(BEFORE)] == [
        MasterSchedulingState.IN_PROGRESS,
        MasterSchedulingState.IN_PROGRESS,
    ]
    _assert_plan_untouched(plan, snapshot, registry, record)


def test_repeated_historical_invocations_keep_distinct_identities():
    registry, record, plan = _open(
        _work(HISTORY),
        (
            _history("hist-a", start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z"),
            _history("hist-b", start="2026-01-03T00:00:00Z", end="2026-01-04T00:00:00Z"),
        ),
    )
    fanout = _fanout(registry, record)
    first, second = plan.steps
    assert first.invocation.specialist is second.invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    assert first.invocation is not second.invocation
    assert first.invocation.invocation_id != second.invocation.invocation_id
    assert first.invocation.specialist_request_id != second.invocation.specialist_request_id
    assert [item.invocation_id for item in fanout.eligible(BEFORE)] == [
        first.invocation.invocation_id,
        second.invocation.invocation_id,
    ]
    left = fanout.claim(first.invocation, "claimer-a", now_utc=BEFORE)
    right = fanout.claim(second.invocation, "claimer-b", now_utc=BEFORE)
    assert left.invocation_id != right.invocation_id
    assert left.specialist is right.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    assert left.evidence_verified is False
    assert right.evidence_verified is False
    assert _by_step(fanout)["hist-a"].state is MasterSchedulingState.IN_PROGRESS
    assert _by_step(fanout)["hist-b"].state is MasterSchedulingState.IN_PROGRESS


def test_four_step_maximum_uses_the_route_limit():
    steps = tuple(_history(f"hist-{index}") for index in range(1, 5))
    registry, record, plan = _open(_work(HISTORY), steps)
    fanout = _fanout(registry, record)
    assert len(plan.steps) == MAX_ROUTE_STEPS == fanout.max_route_steps
    assert fanout.max_decision_steps == MAX_DECISION_STEPS == 1
    assert fanout.remaining_route_claims() == MAX_ROUTE_STEPS
    assert [item.step_id for item in fanout.eligible(BEFORE)] == [step.step_id for step in plan.steps]
    claimed: set[str] = set()
    for step in reversed(plan.steps):
        fanout.claim(step.invocation, f"claimer-{step.step_id}", now_utc=BEFORE)
        claimed.add(step.step_id)
        remaining = [item.step_id for item in fanout.eligible(BEFORE)]
        assert remaining == [item.step_id for item in plan.steps if item.step_id not in claimed]
    assert fanout.remaining_route_claims() == 0
    assert fanout.status is MasterExecutionScheduleStatus.OPEN
    assert [item.state for item in fanout.observations(BEFORE)] == [MasterSchedulingState.IN_PROGRESS] * 4

    oversized_registry, oversized_record, oversized = _open(_work(HISTORY), steps)
    object.__setattr__(oversized, "steps", oversized.steps + (oversized.steps[0],))
    with pytest.raises(ContractValidationError) as too_many:
        _fanout(oversized_registry, oversized_record)
    assert too_many.value.errors == ("excessive_plan_size",)

    decision_registry, decision_record, decision_plan = _open(
        _work(RISK),
        (_decision(),),
        target_candidate_aircraft_id="ac-1",
    )
    original = decision_plan.steps[0]
    extra_invocation = replace(
        original.invocation,
        invocation_id="master-req-1-extra",
        specialist_request_id="master-req-1-specialist-extra",
    )
    extra = replace(original, step_id="dec-extra", invocation=extra_invocation)
    object.__setattr__(decision_plan, "steps", (original, extra))
    with pytest.raises(ContractValidationError) as fanout_denied:
        _fanout(decision_registry, decision_record)
    assert fanout_denied.value.errors == ("decision_fanout_not_permitted",)


def test_invalid_dependency_is_rejected():
    registry, record, plan = _open(
        _work(HISTORY),
        (_history("hist-1"), _history("hist-2", depends_on=("hist-1",))),
    )
    object.__setattr__(plan.steps[1], "depends_on", ("missing-step",))
    with pytest.raises(ContractValidationError) as missing:
        _fanout(registry, record)
    assert missing.value.errors == ("invalid_dependency",)
    assert plan.steps[1].depends_on == ("missing-step",)
    assert plan.execution_authority is MasterRouteExecutionAuthority.DENIED

    cyclic_registry, cyclic_record, cyclic_plan = _open(
        _work(HISTORY),
        (_history("hist-1"), _history("hist-2")),
    )
    object.__setattr__(cyclic_plan.steps[0], "depends_on", ("hist-2",))
    object.__setattr__(cyclic_plan.steps[1], "depends_on", ("hist-1",))
    with pytest.raises(ContractValidationError) as cycle:
        _fanout(cyclic_registry, cyclic_record)
    assert cycle.value.errors == ("cyclic_dependency",)


def test_reconstructed_invocation_cannot_be_claimed():
    registry, record, plan = _open(_work(LIVE), (_live(),))
    fanout = _fanout(registry, record)
    invocation = plan.steps[0].invocation
    copied = MasterInvocationAuthorization.from_dict(invocation.to_dict())
    rebuilt = MasterRoutePlan.from_dict(plan.to_dict()).steps[0].invocation
    assert copied is not invocation
    assert copied.invocation_id == invocation.invocation_id
    assert rebuilt is not invocation
    with pytest.raises(ContractValidationError) as reconstructed:
        fanout.claim(copied, "claimer-1", now_utc=BEFORE)
    assert reconstructed.value.errors == ("reconstructed_invocation_rejected",)
    with pytest.raises(ContractValidationError) as from_plan:
        fanout.claim(rebuilt, "claimer-1", now_utc=BEFORE)
    assert from_plan.value.errors == ("reconstructed_invocation_rejected",)
    with pytest.raises(ContractValidationError) as serialized:
        fanout.claim(invocation.to_dict(), "claimer-1", now_utc=BEFORE)  # type: ignore[arg-type]
    assert serialized.value.errors == ("deserialized_invocation_rejected",)
    claim = fanout.claim(invocation, "claimer-1", now_utc=BEFORE)
    assert claim.invocation_id == invocation.invocation_id
    assert claim.execution_authorized is False
    assert fanout.plan is plan


def test_duplicate_claim_is_rejected():
    registry, record, plan = _open(_work(LIVE), (_live(),))
    fanout = _fanout(registry, record)
    invocation = plan.steps[0].invocation
    first = fanout.claim(invocation, "claimer-1", now_utc=BEFORE)
    with pytest.raises(ContractValidationError) as duplicate:
        fanout.claim(invocation, "claimer-2", now_utc=BEFORE)
    assert duplicate.value.errors == ("duplicate_claim",)
    assert _by_step(fanout)["live-1"].state is MasterSchedulingState.IN_PROGRESS
    assert fanout.remaining_route_claims() == MAX_ROUTE_STEPS - 1
    fanout.complete(invocation, "claimer-1", now_utc=BEFORE)
    assert first.evidence_verified is False


def test_concurrent_claimers_cannot_both_reserve_one_step():
    registry, record, plan = _open(_work(LIVE), (_live(),))
    snapshot = _snapshot(plan)
    fanout = _fanout(registry, record)
    invocation = plan.steps[0].invocation
    barrier = threading.Barrier(2)
    receipts: list[MasterExecutionClaim] = []
    errors: list[BaseException] = []

    def attempt(claimer_id: str) -> None:
        try:
            barrier.wait(5)
            receipts.append(fanout.claim(invocation, claimer_id, now_utc=BEFORE))
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=attempt, args=("claimer-1",)),
        threading.Thread(target=attempt, args=("claimer-2",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert [thread.is_alive() for thread in threads] == [False, False]
    assert len(receipts) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], ContractValidationError)
    assert errors[0].errors == ("duplicate_claim",)
    assert receipts[0].claimer_id in {"claimer-1", "claimer-2"}
    assert receipts[0].execution_authorized is False
    assert receipts[0].evidence_verified is False
    assert _by_step(fanout)["live-1"].state is MasterSchedulingState.IN_PROGRESS
    _assert_plan_untouched(plan, snapshot, registry, record)


def test_failed_dependency_skips_dependents():
    registry, record, plan = _open(
        _work(HISTORY),
        (
            _history("hist-1"),
            _history("hist-2", depends_on=("hist-1",)),
            _history("hist-3", depends_on=("hist-2",)),
        ),
    )
    snapshot = _snapshot(plan)
    fanout = _fanout(registry, record)
    assert [item.step_id for item in fanout.eligible(BEFORE)] == ["hist-1"]
    root = plan.steps[0].invocation
    fanout.claim(root, "claimer-1", now_utc=BEFORE)
    fanout.fail(root, "claimer-1", now_utc=BEFORE, reason="worker_failed")
    observed = _by_step(fanout)
    assert observed["hist-1"].state is MasterSchedulingState.FAILED
    assert observed["hist-1"].reason == "worker_failed"
    assert observed["hist-1"].evidence_verified is False
    assert observed["hist-2"].state is MasterSchedulingState.SKIPPED
    assert observed["hist-2"].reason == "dependency_failed:hist-1"
    assert observed["hist-3"].state is MasterSchedulingState.SKIPPED
    assert observed["hist-3"].reason == "dependency_skipped:hist-2"
    assert fanout.eligible(BEFORE) == ()
    with pytest.raises(ContractValidationError) as direct:
        fanout.claim(plan.steps[1].invocation, "claimer-2", now_utc=BEFORE)
    assert direct.value.errors == ("dependency_failed",)
    with pytest.raises(ContractValidationError) as indirect:
        fanout.claim(plan.steps[2].invocation, "claimer-3", now_utc=BEFORE)
    assert indirect.value.errors == ("dependency_failed",)
    assert fanout.status is MasterExecutionScheduleStatus.CLOSED
    _assert_plan_untouched(plan, snapshot, registry, record)


def test_cancellation_before_and_during_scheduling():
    before_registry, before_record, before_plan = _open(
        _work(HISTORY, LIVE),
        (_history("hist-1"), _live()),
    )
    before = _fanout(before_registry, before_record)
    before_snapshot = _snapshot(before_plan)
    before.cancel(now_utc=BEFORE)
    assert before.status is MasterExecutionScheduleStatus.CANCELLED
    assert [item.state for item in before.observations(BEFORE)] == [
        MasterSchedulingState.CANCELLED,
        MasterSchedulingState.CANCELLED,
    ]
    assert [item.reason for item in before.observations(BEFORE)] == ["cancelled", "cancelled"]
    with pytest.raises(ContractValidationError) as early:
        before.claim(before_plan.steps[0].invocation, "claimer-1", now_utc=BEFORE)
    assert early.value.errors == ("schedule_cancelled",)
    assert before.eligible(BEFORE) == ()
    _assert_plan_untouched(before_plan, before_snapshot, before_registry, before_record)

    registry, record, plan = _open(_work(HISTORY, LIVE), (_history("hist-1"), _live()))
    snapshot = _snapshot(plan)
    fanout = _fanout(registry, record)
    claimed = plan.steps[0]
    waiting = plan.steps[1]
    fanout.claim(claimed.invocation, "claimer-1", now_utc=BEFORE)
    fanout.cancel(now_utc=BEFORE)
    observed = _by_step(fanout)
    assert fanout.status is MasterExecutionScheduleStatus.CANCELLED
    assert observed[claimed.step_id].state is MasterSchedulingState.CANCELLED
    assert observed[claimed.step_id].reason == "cancelled_during_scheduling"
    assert observed[waiting.step_id].state is MasterSchedulingState.CANCELLED
    assert observed[waiting.step_id].reason == "cancelled"
    with pytest.raises(ContractValidationError) as finished:
        fanout.complete(claimed.invocation, "claimer-1", now_utc=BEFORE)
    assert finished.value.errors == ("schedule_cancelled",)
    with pytest.raises(ContractValidationError) as later:
        fanout.claim(waiting.invocation, "claimer-2", now_utc=BEFORE)
    assert later.value.errors == ("schedule_cancelled",)
    _assert_plan_untouched(plan, snapshot, registry, record)


def test_deadline_expiry_stops_unstarted_and_reserved_steps():
    early_registry, early_record, early_plan = _open(_work(LIVE), (_live(),))
    early = _fanout(early_registry, early_record)
    assert early.observe_time(BEFORE) is MasterExecutionScheduleStatus.OPEN
    assert early.observe_time(AT_DEADLINE) is MasterExecutionScheduleStatus.EXPIRED
    assert [item.state for item in early.observations(AT_DEADLINE)] == [MasterSchedulingState.SKIPPED]
    assert [item.reason for item in early.observations(AT_DEADLINE)] == ["deadline_expired"]
    with pytest.raises(ContractValidationError) as expired:
        early.claim(early_plan.steps[0].invocation, "claimer-1", now_utc=AT_DEADLINE)
    assert expired.value.errors == ("deadline_expired",)
    assert early.evidence_verified is False

    registry, record, plan = _open(_work(HISTORY, LIVE), (_history("hist-1"), _live()))
    fanout = _fanout(registry, record)
    fanout.claim(plan.steps[0].invocation, "claimer-1", now_utc=BEFORE)
    assert fanout.observe_time(AT_DEADLINE) is MasterExecutionScheduleStatus.EXPIRED
    observed = _by_step(fanout, AT_DEADLINE)
    assert observed[plan.steps[0].step_id].state is MasterSchedulingState.CANCELLED
    assert observed[plan.steps[0].step_id].reason == "deadline_expired"
    assert observed[plan.steps[1].step_id].state is MasterSchedulingState.SKIPPED
    assert observed[plan.steps[1].step_id].reason == "deadline_expired"
    with pytest.raises(ContractValidationError) as completion:
        fanout.complete(plan.steps[0].invocation, "claimer-1", now_utc=AT_DEADLINE)
    assert completion.value.errors == ("deadline_expired",)
    with pytest.raises(ContractValidationError) as sibling:
        fanout.claim(plan.steps[1].invocation, "claimer-2", now_utc=AT_DEADLINE)
    assert sibling.value.errors == ("deadline_expired",)
    assert [item.evidence_verified for item in fanout.observations(AT_DEADLINE)] == [False, False]


def test_no_scheduling_after_terminal_closure():
    registry, record, plan = _open(_work(HISTORY, LIVE), (_history("hist-1"), _live()))
    snapshot = _snapshot(plan)
    fanout = _fanout(registry, record)
    fanout.claim(plan.steps[0].invocation, "claimer-1", now_utc=BEFORE)
    with pytest.raises(ContractValidationError) as outstanding:
        fanout.close(now_utc=BEFORE)
    assert outstanding.value.errors == ("claim_outstanding",)
    assert fanout.status is MasterExecutionScheduleStatus.OPEN
    fanout.complete(plan.steps[0].invocation, "claimer-1", now_utc=BEFORE)
    fanout.close(now_utc=BEFORE)
    assert fanout.status is MasterExecutionScheduleStatus.CLOSED
    assert _by_step(fanout)[plan.steps[1].step_id].state is MasterSchedulingState.SKIPPED
    assert _by_step(fanout)[plan.steps[1].step_id].reason == "schedule_closed"
    with pytest.raises(ContractValidationError) as closed:
        fanout.claim(plan.steps[1].invocation, "claimer-2", now_utc=BEFORE)
    assert closed.value.errors == ("schedule_closed",)
    with pytest.raises(ContractValidationError) as again:
        fanout.close(now_utc=BEFORE)
    assert again.value.errors == ("schedule_closed",)
    assert fanout.eligible(BEFORE) == ()
    _assert_plan_untouched(plan, snapshot, registry, record)

    settled_registry, settled_record, settled_plan = _open(_work(LIVE), (_live(),))
    settled = _fanout(settled_registry, settled_record)
    invocation = settled_plan.steps[0].invocation
    settled.claim(invocation, "claimer-1", now_utc=BEFORE)
    settled.complete(invocation, "claimer-1", now_utc=BEFORE)
    assert settled.status is MasterExecutionScheduleStatus.CLOSED
    with pytest.raises(ContractValidationError) as natural:
        settled.claim(invocation, "claimer-2", now_utc=BEFORE)
    assert natural.value.errors == ("schedule_closed",)

    session_registry, session_record, session_plan = _open(_work(LIVE), (_live(),))
    session = _fanout(session_registry, session_record)
    session_registry.close(session_record)
    with pytest.raises(ContractValidationError) as session_closed:
        session.claim(session_plan.steps[0].invocation, "claimer-1", now_utc=BEFORE)
    assert session_closed.value.errors == ("session_closed",)
    assert session.status is MasterExecutionScheduleStatus.CLOSED
    assert session_plan.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_claim_receipt_cannot_grant_execution_or_evidence():
    with pytest.raises(ContractValidationError) as authorized:
        MasterExecutionClaim(
            session_id="session-id",
            step_id="live-1",
            invocation_id="master-req-1-1",
            specialist=MasterSpecialist.LIVE_OPS,
            claimer_id="claimer-1",
            planning_gates=(MasterRouteGate.NOT_REQUIRED,),
            planning_ready_to_execute=True,
            execution_authorized=True,
        )
    assert authorized.value.errors == ("execution_authority_not_granted",)
    with pytest.raises(ContractValidationError) as evidence:
        MasterExecutionClaim(
            session_id="session-id",
            step_id="live-1",
            invocation_id="master-req-1-1",
            specialist=MasterSpecialist.LIVE_OPS,
            claimer_id="claimer-1",
            planning_gates=(MasterRouteGate.NOT_REQUIRED,),
            planning_ready_to_execute=True,
            evidence_verified=True,
        )
    assert evidence.value.errors == ("execution_authority_not_granted",)
    with pytest.raises(ContractValidationError) as decision:
        MasterExecutionClaim(
            session_id="session-id",
            step_id="live-1",
            invocation_id="master-req-1-1",
            specialist=MasterSpecialist.LIVE_OPS,
            claimer_id="claimer-1",
            planning_gates=(MasterRouteGate.NOT_REQUIRED,),
            planning_ready_to_execute=False,
            authorization_decision_required=False,
        )
    assert decision.value.errors == ("authorization_decision_required",)
