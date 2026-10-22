"""Offline tests for synthetic LangGraph orchestration. These tests do not execute specialists."""

from __future__ import annotations

import importlib.metadata
import threading

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError, TemporalScope
from wilvor_ai.master_contracts import MasterPurpose, MasterRequirement, MasterSpecialist
from wilvor_ai.master_orchestration import (
    CANCELLATION_SUPERSTEP_LIMITATION,
    SCRIPTED_TIMEOUT_LIMITATION,
    SYNTHETIC_GRAPH_NODES,
    SYNTHETIC_RECURSION_LIMIT,
    SyntheticAssignment,
    SyntheticOutcome,
    SyntheticProductionAuthority,
    SyntheticStepStatus,
    SyntheticTerminalStatus,
    SyntheticWorkerResult,
    SyntheticWorkflowResult,
    bind_result_reducer,
    compile_synthetic_test_graph,
    reject_planning_snapshot,
    reserve_dispatch_slots,
    run_synthetic_master_workflow,
)
from wilvor_ai.master_routing import (
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
    MasterSynthesisFulfillment,
    authorize_route,
)


TEXT = "Which aircraft are currently affected by hazards, and what recorded risks apply?"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
COMPARE = AgentCapability.COMPARE_VERIFIED_EVIDENCE
ALTITUDE = AgentCapability.ISSUE_ALTITUDE_INSTRUCTIONS


def _context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "correlation_id": "corr-1",
        "application_request_time_utc": "2026-01-15T00:00:01Z",
        "canonical_user_text": TEXT,
    }
    values.update(overrides)
    return MasterPlanningContext(**values)  # type: ignore[arg-type]


def _work(*capabilities: AgentCapability, optional: AgentCapability | None = None) -> tuple:
    items = [MasterProposedCapability(item, MasterRequirement.REQUIRED) for item in capabilities]
    if optional is not None:
        items.append(MasterProposedCapability(optional, MasterRequirement.OPTIONAL))
    return tuple(items)


def _step(step_id: str, specialist: MasterSpecialist, purpose: MasterPurpose, capability: AgentCapability, **overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "step_id": step_id,
        "specialist": specialist,
        "purpose": purpose,
        "requirement": MasterRequirement.REQUIRED,
        "capability": capability,
    }
    values.update(overrides)
    return MasterProposedStep(**values)  # type: ignore[arg-type]


def _live(step_id: str = "live-1", **overrides: object) -> MasterProposedStep:
    return _step(step_id, MasterSpecialist.LIVE_OPS, MasterPurpose.ANSWER, LIVE, temporal_intent=MasterRouteTemporalIntent.CURRENT, **overrides)


def _history(step_id: str = "hist-1", **overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "temporal_intent": MasterRouteTemporalIntent.HISTORICAL,
        "historical_start_utc": "2026-01-01T00:00:00Z",
        "historical_end_utc": "2026-01-02T00:00:00Z",
    }
    values.update(overrides)
    return _step(step_id, MasterSpecialist.HISTORICAL_ANALYTICS, MasterPurpose.ANSWER, HISTORY, **values)


def _decision(step_id: str = "dec-1", **overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {"capability": RISK, "temporal_intent": MasterRouteTemporalIntent.CURRENT}
    values.update(overrides)
    capability = values.pop("capability")
    return _step(step_id, MasterSpecialist.DECISION, MasterPurpose.ANSWER, capability, **values)  # type: ignore[arg-type]


def _proposal(steps: tuple[MasterProposedStep, ...], capabilities: tuple | None = None, **overrides: object) -> MasterRouteProposal:
    if capabilities is None:
        capabilities = _work(LIVE)
    values: dict[str, object] = {"user_text": TEXT, "capabilities": capabilities, "steps": steps}
    values.update(overrides)
    return MasterRouteProposal(**values)  # type: ignore[arg-type]


def _envelope(assignment: SyntheticAssignment, outcome: SyntheticOutcome = SyntheticOutcome.SYNTHETIC_COMPLETED, **overrides: object) -> SyntheticWorkerResult:
    values: dict[str, object] = {
        "master_request_id": assignment.master_request_id,
        "correlation_id": assignment.correlation_id,
        "invocation_id": assignment.invocation_id,
        "specialist_request_id": assignment.specialist_request_id,
        "specialist": assignment.specialist,
        "outcome": outcome,
        "note": "synthetic",
    }
    values.update(overrides)
    return SyntheticWorkerResult(**values)  # type: ignore[arg-type]


def _workers(calls: list[SyntheticAssignment], outcomes: dict[str, object] | None = None, specialists: tuple[MasterSpecialist, ...] | None = None) -> dict:
    selected = specialists or (
        MasterSpecialist.LIVE_OPS,
        MasterSpecialist.HISTORICAL_ANALYTICS,
        MasterSpecialist.DECISION,
    )
    outcomes = outcomes or {}

    def make(specialist: MasterSpecialist):
        def worker(assignment: SyntheticAssignment):
            calls.append(assignment)
            spec = outcomes.get(assignment.step_id, SyntheticOutcome.SYNTHETIC_COMPLETED)
            if spec == "BAD":
                return "bad"
            if spec == "RAISE":
                raise RuntimeError("scripted")
            if spec == "CORR":
                return _envelope(assignment, correlation_id="other-corr")
            if spec == "INV":
                return _envelope(assignment, invocation_id="not-a-real-id")
            if spec == "REQ":
                return _envelope(assignment, master_request_id="other-req")
            return _envelope(assignment, outcome=spec)  # type: ignore[arg-type]

        return worker

    return {item: make(item) for item in selected}


def _run(steps, capabilities=None, calls=None, outcomes=None, specialists=None, **context):
    bucket = [] if calls is None else calls
    result = run_synthetic_master_workflow(
        _context(**context),
        _proposal(steps, capabilities),
        _workers(bucket, outcomes, specialists),
    )
    return result, bucket


def test_single_live_ops_synthetic_completion_denies_execution():
    result, calls = _run((_live(),), _work(LIVE, COMPARE))
    assert result.terminal_status is SyntheticTerminalStatus.SYNTHETIC_COMPLETED
    assert result.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert result.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert result.end_to_end_fulfilled is False
    assert result.evidence_verified is False
    assert result.results[0].outcome is SyntheticOutcome.SYNTHETIC_COMPLETED
    assert result.results[0].evidence_verified is False
    assert result.results[0].production_authority is SyntheticProductionAuthority.DENIED
    assert result.decision.plan.deferred_synthesis[0].fulfillment is MasterSynthesisFulfillment.NOT_YET_FULFILLED
    assert result.decision.plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert "answer" not in SyntheticWorkflowResult.__dataclass_fields__
    assert len(calls) == 1
    assert calls[0].untrusted_hints.region_text is None


def test_single_historical_step_preserves_utc_window():
    hints = MasterRouteHints(callsign="UAL123")
    result, calls = _run((_history(hints=hints),), _work(HISTORY))
    step = result.decision.plan.steps[0]
    assert step.historical_start_utc == "2026-01-01T00:00:00Z"
    assert step.historical_end_utc == "2026-01-02T00:00:00Z"
    assert calls[0].historical_start_utc == "2026-01-01T00:00:00Z"
    assert calls[0].historical_end_utc == "2026-01-02T00:00:00Z"
    assert calls[0].untrusted_hints.callsign == "UAL123"
    assert step.untrusted_hints.callsign == "UAL123"
    assert step.invocation.expected_aircraft_id is None
    assert step.temporal_scopes == (TemporalScope.HISTORICAL,)
    assert result.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_independent_live_ops_and_historical_both_run():
    result, calls = _run((_live(), _history()), _work(LIVE, HISTORY))
    assert result.terminal_status is SyntheticTerminalStatus.SYNTHETIC_COMPLETED
    assert len(calls) == 2
    assert [item.invocation_id for item in result.results] == [
        step.invocation.invocation_id for step in result.decision.plan.steps
    ]
    assert len(result.dispatch_batches) == 1
    assert len(result.dispatch_batches[0]) == 2


def test_result_order_follows_the_plan_not_completion_order():
    result, _calls = _run((_live(), _history()), _work(LIVE, HISTORY))
    assert [step.invocation.specialist for step in result.decision.plan.steps] == [
        MasterSpecialist.HISTORICAL_ANALYTICS,
        MasterSpecialist.LIVE_OPS,
    ]
    assert [item.specialist for item in result.results] == [
        MasterSpecialist.HISTORICAL_ANALYTICS,
        MasterSpecialist.LIVE_OPS,
    ]


def test_live_ops_can_finish_before_historical_while_results_stay_in_plan_order():
    entered = {
        MasterSpecialist.HISTORICAL_ANALYTICS: threading.Event(),
        MasterSpecialist.LIVE_OPS: threading.Event(),
    }
    live_finished = threading.Event()
    finish_order: list[MasterSpecialist] = []
    thread_ids: list[int] = []
    calls: list[SyntheticAssignment] = []
    lock = threading.Lock()

    def make(specialist: MasterSpecialist):
        def worker(assignment: SyntheticAssignment) -> SyntheticWorkerResult:
            with lock:
                calls.append(assignment)
                thread_ids.append(threading.get_ident())
            entered[specialist].set()
            for event in entered.values():
                if not event.wait(5):
                    raise ContractValidationError("parallel_peer_did_not_enter")
            if specialist is MasterSpecialist.LIVE_OPS:
                with lock:
                    finish_order.append(specialist)
                live_finished.set()
            else:
                if not live_finished.wait(5):
                    raise ContractValidationError("live_ops_did_not_finish_first")
                with lock:
                    finish_order.append(specialist)
            return _envelope(assignment)

        return worker

    result = run_synthetic_master_workflow(
        _context(),
        _proposal((_live(), _history()), _work(LIVE, HISTORY)),
        {
            MasterSpecialist.LIVE_OPS: make(MasterSpecialist.LIVE_OPS),
            MasterSpecialist.HISTORICAL_ANALYTICS: make(MasterSpecialist.HISTORICAL_ANALYTICS),
        },
    )
    assert finish_order == [MasterSpecialist.LIVE_OPS, MasterSpecialist.HISTORICAL_ANALYTICS]
    assert len(set(thread_ids)) == 2
    assert [item.specialist for item in result.results] == [
        MasterSpecialist.HISTORICAL_ANALYTICS,
        MasterSpecialist.LIVE_OPS,
    ]
    assert {item.outcome for item in result.results} == {SyntheticOutcome.SYNTHETIC_COMPLETED}
    assert len({item.invocation_id for item in result.results}) == 2
    flat = [item for batch in result.dispatch_batches for item in batch]
    assert len(flat) == len(set(flat)) == 2
    assert result.dispatch_count == 2
    assert {item.step_id for item in calls} == {"live-1", "hist-1"}


def test_live_ops_then_historical_waits_for_the_parent():
    result, calls = _run((_live("live-1"), _history("hist-1", depends_on=("live-1",))), _work(LIVE, HISTORY))
    assert [item.step_id for item in calls] == ["live-1", "hist-1"]
    assert result.dispatch_batches[0] != result.dispatch_batches[1]
    assert result.decision.plan.steps[1].gates == (MasterRouteGate.DEPENDENCY_BLOCKED,)
    assert result.terminal_status is SyntheticTerminalStatus.SYNTHETIC_COMPLETED


def test_historical_then_live_ops_waits_for_the_parent():
    result, calls = _run((_history("hist-1"), _live("live-1", depends_on=("hist-1",))), _work(LIVE, HISTORY))
    assert [item.step_id for item in calls] == ["hist-1", "live-1"]
    assert result.terminal_status is SyntheticTerminalStatus.SYNTHETIC_COMPLETED
    assert MasterRouteGate.DEPENDENCY_BLOCKED in result.decision.plan.steps[1].gates


def test_child_waits_for_two_parents():
    child = _live("live-2", depends_on=("hist-1", "live-1"))
    result, calls = _run((_history("hist-1"), _live("live-1"), child), _work(LIVE, HISTORY))
    assert [item.step_id for item in calls][:2] == ["hist-1", "live-1"] or set(item.step_id for item in calls[:2]) == {"hist-1", "live-1"}
    assert calls[-1].step_id == "live-2"
    assert len(result.dispatch_batches) == 2
    assert result.terminal_status is SyntheticTerminalStatus.SYNTHETIC_COMPLETED


def test_required_parent_failure_does_not_run_the_child():
    result, calls = _run(
        (_live("live-1"), _history("hist-1", depends_on=("live-1",))),
        _work(LIVE, HISTORY),
        outcomes={"live-1": SyntheticOutcome.FAILED},
    )
    assert [item.step_id for item in calls] == ["live-1"]
    assert result.terminal_status is SyntheticTerminalStatus.FAILED
    assert any(status is SyntheticStepStatus.BLOCKED for _, status in result.step_statuses)


def test_partial_or_unknown_parent_does_not_run_the_child():
    for outcome in (SyntheticOutcome.PARTIAL, SyntheticOutcome.NO_RESULT, SyntheticOutcome.UNSUPPORTED):
        result, calls = _run(
            (_live("live-1"), _history("hist-1", depends_on=("live-1",))),
            _work(LIVE, HISTORY),
            outcomes={"live-1": outcome},
        )
        assert [item.step_id for item in calls] == ["live-1"]
        assert result.terminal_status is SyntheticTerminalStatus.BLOCKED


def test_discovery_completion_does_not_unblock_decision():
    discovery = _step(
        "find-1",
        MasterSpecialist.LIVE_OPS,
        MasterPurpose.DISCOVERY,
        LIVE,
        hints=MasterRouteHints(callsign="UAL123"),
    )
    gated = _decision("dec-1", depends_on=("find-1",), hints=MasterRouteHints(callsign="UAL123"))
    result, calls = _run((discovery, gated), _work(LIVE, RISK))
    assert [item.step_id for item in calls] == ["find-1"]
    decision_step = result.decision.plan.steps[1]
    assert decision_step.invocation.expected_aircraft_id is None
    assert decision_step.target_authorization_granted is False
    assert decision_step.ready_to_execute is False
    assert MasterRouteGate.TRUSTED_IDENTITY_VALIDATION_PENDING in decision_step.gates
    assert MasterRouteGate.DEPENDENCY_BLOCKED in decision_step.gates
    assert result.terminal_status is SyntheticTerminalStatus.BLOCKED


def test_optional_worker_failure_is_partial():
    result, calls = _run(
        (_live("live-1"), _history("hist-1", requirement=MasterRequirement.OPTIONAL)),
        _work(LIVE, optional=HISTORY),
        outcomes={"hist-1": SyntheticOutcome.FAILED},
    )
    assert len(calls) == 2
    assert result.terminal_status is SyntheticTerminalStatus.PARTIAL
    assert result.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_required_worker_failure_is_not_success():
    result, _calls = _run((_live(),), outcomes={"live-1": "RAISE"})
    assert result.terminal_status is SyntheticTerminalStatus.FAILED
    assert result.results[0].note == "synthetic worker exception"
    assert result.results[0].evidence_verified is False
    assert result.end_to_end_fulfilled is False


def test_missing_required_worker_fails_closed():
    result, calls = _run((_live(),), specialists=(MasterSpecialist.HISTORICAL_ANALYTICS,))
    assert calls == []
    assert result.terminal_status is SyntheticTerminalStatus.FAILED
    assert "required_worker_missing:live-1" in result.reasons


def test_optional_missing_worker_is_explicit():
    result, calls = _run(
        (_live("live-1"), _history("hist-1", requirement=MasterRequirement.OPTIONAL)),
        _work(LIVE, optional=HISTORY),
        specialists=(MasterSpecialist.LIVE_OPS,),
    )
    assert [item.step_id for item in calls] == ["live-1"]
    assert result.terminal_status is SyntheticTerminalStatus.PARTIAL
    assert "optional_worker_missing:hist-1" in result.reasons


def test_region_gate_blocks_dispatch_and_stays_on_the_plan():
    step = _live(hints=MasterRouteHints(region_text="California"))
    before = authorize_route(_proposal((step,)), _context()).plan.to_dict()
    result, calls = _run((step,))
    assert calls == []
    assert result.terminal_status is SyntheticTerminalStatus.BLOCKED
    assert result.decision.plan.to_dict() == before
    assert MasterRouteGate.REGION_VALIDATION_PENDING in result.decision.plan.steps[0].gates
    assert result.decision.plan.steps[0].ready_to_execute is False
    assert result.decision.plan.steps[0].untrusted_hints.region_text == "California"


def test_unresolved_temporal_scope_is_not_dispatched():
    result, calls = _run(
        (_decision(temporal_intent=MasterRouteTemporalIntent.UNSPECIFIED),),
        _work(RISK),
    )
    assert calls == []
    assert result.terminal_status is SyntheticTerminalStatus.BLOCKED
    assert MasterRouteGate.TEMPORAL_SCOPE_UNRESOLVED in result.decision.plan.steps[0].gates
    assert result.decision.plan.steps[0].temporal_scopes == ()


def test_decision_identity_gate_is_not_dispatched():
    result, calls = _run((_decision(),), _work(RISK), target_candidate_aircraft_id="ac-1")
    assert calls == []
    step = result.decision.plan.steps[0]
    assert step.invocation.expected_aircraft_id == "ac-1"
    assert step.target_authorization_granted is False
    assert step.ready_to_execute is False
    assert MasterRouteGate.TRUSTED_IDENTITY_VALIDATION_PENDING in step.gates
    assert MasterRouteGate.CURRENTNESS_VALIDATION_PENDING in step.gates
    assert result.terminal_status is SyntheticTerminalStatus.BLOCKED


def test_prohibited_proposal_does_not_enter_the_worker():
    result, calls = _run((_live(),), _work(LIVE, ALTITUDE))
    assert calls == []
    assert result.terminal_status is SyntheticTerminalStatus.NOT_RUN
    assert result.decision.outcome is MasterRouteOutcome.REFUSED
    assert result.executed_nodes == ()


def test_unsupported_route_is_not_run():
    step = _step("hist-find", MasterSpecialist.HISTORICAL_ANALYTICS, MasterPurpose.DISCOVERY, HISTORY)
    result, calls = _run((step,), _work(HISTORY))
    assert calls == []
    assert result.terminal_status is SyntheticTerminalStatus.NOT_RUN
    assert result.decision.outcome is MasterRouteOutcome.UNSUPPORTED


def test_duplicate_result_reducer_rejects_conflicting_writes():
    sample = SyntheticWorkerResult(
        master_request_id="master-req-1",
        correlation_id="corr-1",
        invocation_id="inv-1",
        specialist_request_id="master-req-1-specialist-1",
        specialist=MasterSpecialist.LIVE_OPS,
        outcome=SyntheticOutcome.SYNTHETIC_COMPLETED,
        note="synthetic",
    )
    other = SyntheticWorkerResult(
        master_request_id="master-req-1",
        correlation_id="corr-1",
        invocation_id="inv-1",
        specialist_request_id="master-req-1-specialist-1",
        specialist=MasterSpecialist.LIVE_OPS,
        outcome=SyntheticOutcome.FAILED,
        note="other",
    )
    reducer = bind_result_reducer(frozenset({"inv-1"}))
    with pytest.raises(ContractValidationError, match="duplicate_synthetic_result"):
        reducer({"inv-1": sample}, {"inv-1": other})


def test_unknown_invocation_id_is_rejected():
    sample = SyntheticWorkerResult(
        master_request_id="master-req-1",
        correlation_id="corr-1",
        invocation_id="inv-unknown",
        specialist_request_id="master-req-1-specialist-1",
        specialist=MasterSpecialist.LIVE_OPS,
        outcome=SyntheticOutcome.SYNTHETIC_COMPLETED,
        note="synthetic",
    )
    reducer = bind_result_reducer(frozenset({"inv-1"}))
    with pytest.raises(ContractValidationError, match="unknown_invocation_id"):
        reducer({}, {"inv-unknown": sample})
    with pytest.raises(ContractValidationError, match="unknown_invocation_id"):
        _run((_live(),), outcomes={"live-1": "INV"})


def test_correlation_mismatch_is_rejected():
    with pytest.raises(ContractValidationError, match="correlation_id_mismatch"):
        _run((_live(),), outcomes={"live-1": "CORR"})


def test_master_request_id_mismatch_is_rejected():
    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        _run((_live(),), outcomes={"live-1": "REQ"})


def test_serialized_plan_is_not_execution_authorization():
    calls: list[SyntheticAssignment] = []
    decision = authorize_route(_proposal((_live(),)), _context())
    with pytest.raises(TypeError, match="proposal is required"):
        run_synthetic_master_workflow(_context(), decision.plan, _workers(calls))
    cloned = type(decision).from_dict(decision.to_dict())
    graph = compile_synthetic_test_graph(_workers(calls), nonce="ticket-a", decision=decision)
    with pytest.raises(ContractValidationError, match="invalid_synthetic_ticket"):
        graph.invoke(
            {
                "ticket_nonce": "ticket-a",
                "decision": cloned,
                "statuses": {},
                "results": {},
                "ready": [],
                "dispatch_count": 0,
                "budget_exhausted": False,
                "terminal_status": "",
                "reason_codes": [],
            }
        )
    assert calls == []


def test_population_hint_does_not_increase_sends():
    hints = MasterRouteHints(population_aircraft_ids=("ac-1", "ac-2", "ac-3"))
    result, calls = _run((_live(hints=hints),))
    assert len(calls) == 1
    assert len(result.dispatch_batches) == 1
    assert len(result.dispatch_batches[0]) == 1
    assert calls[0].untrusted_hints.population_aircraft_ids == ("ac-1", "ac-2", "ac-3")


def test_five_step_proposal_does_not_run():
    with pytest.raises(ContractValidationError, match="excessive_plan_size"):
        _proposal(tuple(_live(f"live-{index}") for index in range(5)))


def test_distinct_master_requests_do_not_mix():
    first_calls: list[SyntheticAssignment] = []
    second_calls: list[SyntheticAssignment] = []
    first, _ = _run((_live(),), calls=first_calls, master_request_id="master-a", correlation_id="corr-a")
    second, _ = _run((_history(),), _work(HISTORY), calls=second_calls, master_request_id="master-b", correlation_id="corr-b")
    assert {item.master_request_id for item in first.results} == {"master-a"}
    assert {item.correlation_id for item in first.results} == {"corr-a"}
    assert {item.master_request_id for item in second.results} == {"master-b"}
    assert {item.correlation_id for item in second.results} == {"corr-b"}
    assert {item.invocation_id for item in first.results}.isdisjoint({item.invocation_id for item in second.results})
    assert first_calls[0].master_request_id == "master-a"
    assert second_calls[0].master_request_id == "master-b"


def test_second_call_does_not_reuse_the_first_registry():
    first_calls: list[SyntheticAssignment] = []
    second_calls: list[SyntheticAssignment] = []
    _run((_live(),), calls=first_calls)
    _run((_live(),), calls=second_calls)
    assert len(first_calls) == 1
    assert len(second_calls) == 1
    assert first_calls is not second_calls


def test_stream_updates_name_the_langgraph_nodes():
    result, _calls = _run((_live(),))
    assert result.executed_nodes == (
        "validate_input",
        "select_ready",
        "invoke_fake_specialist",
        "select_ready",
        "finalize_workflow",
    )
    assert result.executed_nodes.index("select_ready") < result.executed_nodes.index("invoke_fake_specialist")


def test_compiled_graph_uses_the_fixed_node_set():
    decision = authorize_route(_proposal((_live(),)), _context())
    graph = compile_synthetic_test_graph({}, nonce="ticket-a", decision=decision)
    nodes = set(graph.get_graph().nodes)
    assert set(SYNTHETIC_GRAPH_NODES) <= nodes
    assert "LIVE_OPS" not in nodes
    assert "HISTORICAL_ANALYTICS" not in nodes
    assert "DECISION" not in nodes
    assert hasattr(graph, "stream")


def test_recursion_budget_does_not_fabricate_completion():
    calls: list[SyntheticAssignment] = []
    result = run_synthetic_master_workflow(
        _context(),
        _proposal((_live(), _history("hist-1", depends_on=("live-1",))), _work(LIVE, HISTORY)),
        _workers(calls),
        recursion_limit=2,
    )
    assert result.terminal_status is SyntheticTerminalStatus.BUDGET_EXHAUSTED
    assert result.reasons == ("graph_recursion_exhausted",)
    assert all(status is not SyntheticStepStatus.SYNTHETIC_COMPLETED for _, status in result.step_statuses)
    assert result.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_four_step_dependency_chain_terminates():
    steps = (
        _live("live-1"),
        _history("hist-1", depends_on=("live-1",)),
        _live("live-2", depends_on=("hist-1",)),
        _history("hist-2", depends_on=("live-2",)),
    )
    result, calls = _run(steps, _work(LIVE, HISTORY))
    assert result.terminal_status is SyntheticTerminalStatus.SYNTHETIC_COMPLETED
    assert result.dispatch_count == 4
    assert [item.step_id for item in calls] == ["live-1", "hist-1", "live-2", "hist-2"]
    assert result.executed_nodes.count("invoke_fake_specialist") == 4
    assert len(result.executed_nodes) <= SYNTHETIC_RECURSION_LIMIT


def test_four_mixed_parallel_and_sequential_steps_terminate():
    steps = (
        _history("hist-a"),
        _live("live-a"),
        _history("hist-b", depends_on=("hist-a",)),
        _live("live-b", depends_on=("live-a",)),
    )
    result, calls = _run(steps, _work(LIVE, HISTORY))
    assert result.terminal_status is SyntheticTerminalStatus.SYNTHETIC_COMPLETED
    assert result.dispatch_count == 4
    assert len(result.dispatch_batches) == 2
    assert [len(batch) for batch in result.dispatch_batches] == [2, 2]
    assert {item.step_id for item in calls} == {"hist-a", "live-a", "hist-b", "live-b"}
    assert result.executed_nodes.count("invoke_fake_specialist") == 4


def test_dispatch_is_recorded_before_the_worker_and_never_repeats():
    result, calls = _run((_live(), _history()), _work(LIVE, HISTORY))
    flat = [item for batch in result.dispatch_batches for item in batch]
    assert len(flat) == len(set(flat)) == 2
    assert result.executed_nodes.index("select_ready") < result.executed_nodes.index("invoke_fake_specialist")
    assert len(calls) == 2
    assert result.dispatch_count == 2


def test_parallel_cancellation_records_the_sibling_that_already_ran():
    result, calls = _run(
        (_live("live-1"), _history("hist-1")),
        _work(LIVE, HISTORY),
        outcomes={"hist-1": SyntheticOutcome.CANCELLED},
    )
    assert {item.step_id for item in calls} == {"live-1", "hist-1"}
    assert result.terminal_status is SyntheticTerminalStatus.CANCELLED
    assert {item.invocation_id for item in result.results} == set(result.dispatch_batches[0])
    assert CANCELLATION_SUPERSTEP_LIMITATION in result.limitations
    child = _history("hist-2", depends_on=("hist-1",))
    child_result, child_calls = _run(
        (_history("hist-1"), child),
        _work(HISTORY),
        outcomes={"hist-1": SyntheticOutcome.CANCELLED},
    )
    assert [item.step_id for item in child_calls] == ["hist-1"]
    assert child_result.terminal_status is SyntheticTerminalStatus.CANCELLED


def test_cancellation_does_not_dispatch_a_later_ready_step():
    steps = (
        _history("hist-1"),
        _live("live-1"),
        _live("live-2", depends_on=("live-1",)),
    )
    proposal = _proposal(steps, _work(LIVE, HISTORY))
    context = _context()
    before = authorize_route(proposal, context).plan.to_dict()
    calls: list[SyntheticAssignment] = []
    result = run_synthetic_master_workflow(
        context,
        proposal,
        _workers(calls, {"hist-1": SyntheticOutcome.CANCELLED}),
    )
    assert {item.step_id for item in calls} == {"hist-1", "live-1"}
    assert result.terminal_status is SyntheticTerminalStatus.CANCELLED
    assert [item.outcome for item in result.results] == [
        SyntheticOutcome.CANCELLED,
        SyntheticOutcome.SYNTHETIC_COMPLETED,
    ]
    assert result.step_statuses[2][1] is SyntheticStepStatus.WAITING
    assert "not_executed_cancelled:live-2" in result.reasons
    assert result.executed_nodes.count("invoke_fake_specialist") == 2
    assert result.executed_nodes[-2:] == ("select_ready", "finalize_workflow")
    flat = [item for batch in result.dispatch_batches for item in batch]
    assert len(flat) == len(set(flat)) == 2
    assert result.decision.plan.to_dict() == before
    assert result.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert result.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert result.end_to_end_fulfilled is False
    assert result.evidence_verified is False
    assert result.decision.plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert CANCELLATION_SUPERSTEP_LIMITATION in result.limitations


def test_scripted_timeout_is_a_failure_not_a_wall_clock_timer():
    result, calls = _run((_live(),), outcomes={"live-1": SyntheticOutcome.TIMED_OUT})
    assert len(calls) == 1
    assert result.terminal_status is SyntheticTerminalStatus.FAILED
    assert result.results[0].outcome is SyntheticOutcome.TIMED_OUT
    assert SCRIPTED_TIMEOUT_LIMITATION in result.limitations
    blocked, blocked_calls = _run(
        (_live("live-1"), _history("hist-1", depends_on=("live-1",))),
        _work(LIVE, HISTORY),
        outcomes={"live-1": SyntheticOutcome.TIMED_OUT},
    )
    assert [item.step_id for item in blocked_calls] == ["live-1"]
    assert blocked.terminal_status is SyntheticTerminalStatus.FAILED


def test_unexpected_worker_output_is_a_contract_error():
    with pytest.raises(ContractValidationError, match="unexpected_synthetic_result"):
        _run((_live(),), outcomes={"live-1": "BAD"})


def test_invalid_worker_registry_is_rejected():
    with pytest.raises(ContractValidationError, match="invalid_worker_registry"):
        run_synthetic_master_workflow(_context(), _proposal((_live(),)), {"LIVE_OPS": lambda _assignment: None})


def test_planning_snapshot_rejects_replacement():
    first = authorize_route(_proposal((_live(),)), _context()).plan
    second = authorize_route(_proposal((_history(),), _work(HISTORY)), _context()).plan
    with pytest.raises(ContractValidationError, match="planning_snapshot_modified"):
        reject_planning_snapshot(first, second)


def test_dispatch_budget_helper_stops_at_four():
    assert reserve_dispatch_slots(4, ("extra",)) is None
    assert reserve_dispatch_slots(3, ("one",)) == 4
    with pytest.raises(ContractValidationError, match="invalid_dispatch_count"):
        reserve_dispatch_slots(True, ())  # type: ignore[arg-type]


def test_synthetic_result_cannot_claim_verified_evidence_or_production():
    payload = {
        "schema_version": "wilvor.ai.synthetic_worker_result.v1",
        "master_request_id": "master-req-1",
        "correlation_id": "corr-1",
        "invocation_id": "master-req-1-1",
        "specialist_request_id": "master-req-1-specialist-1",
        "specialist": "LIVE_OPS",
        "outcome": "SYNTHETIC_COMPLETED",
        "note": "synthetic",
        "evidence_verified": True,
        "production_authority": "DENIED",
    }
    with pytest.raises(ContractValidationError, match="synthetic_evidence_not_verified"):
        SyntheticWorkerResult.from_dict(payload)
    payload["evidence_verified"] = False
    payload["production_authority"] = "PRODUCTION"
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        SyntheticWorkerResult.from_dict(payload)
    with pytest.raises(ContractValidationError, match="synthetic_evidence_not_verified"):
        SyntheticWorkerResult(
            master_request_id="master-req-1",
            correlation_id="corr-1",
            invocation_id="master-req-1-1",
            specialist_request_id="master-req-1-specialist-1",
            specialist=MasterSpecialist.LIVE_OPS,
            outcome=SyntheticOutcome.SYNTHETIC_COMPLETED,
            note="synthetic",
            evidence_verified=True,
        )


def _valid_result_payload() -> dict[str, object]:
    return {
        "schema_version": "wilvor.ai.synthetic_worker_result.v1",
        "master_request_id": "master-req-1",
        "correlation_id": "corr-1",
        "invocation_id": "master-req-1-1",
        "specialist_request_id": "master-req-1-specialist-1",
        "specialist": "LIVE_OPS",
        "outcome": "SYNTHETIC_COMPLETED",
        "note": "synthetic",
        "evidence_verified": False,
        "production_authority": "DENIED",
    }


def test_synthetic_result_round_trip_preserves_a_valid_payload():
    workflow, _calls = _run((_live(),))
    original = workflow.results[0]
    payload = original.to_dict()
    restored = SyntheticWorkerResult.from_dict(payload)
    assert restored == original
    assert restored.to_dict() == payload
    assert restored.evidence_verified is False
    assert restored.production_authority is SyntheticProductionAuthority.DENIED


def test_synthetic_result_from_dict_rejects_malformed_payloads():
    valid = _valid_result_payload()
    with pytest.raises(ContractValidationError, match="invalid_synthetic_result"):
        SyntheticWorkerResult.from_dict(["not", "a", "mapping"])  # type: ignore[arg-type]
    missing = dict(valid)
    del missing["invocation_id"]
    with pytest.raises(ContractValidationError, match="missing_invocation_id"):
        SyntheticWorkerResult.from_dict(missing)
    unknown = dict(valid)
    unknown["aircraft_id"] = "ac-1"
    with pytest.raises(ContractValidationError, match="unexpected_synthetic_result_field"):
        SyntheticWorkerResult.from_dict(unknown)
    wrong_schema = dict(valid)
    wrong_schema["schema_version"] = "wilvor.ai.synthetic_worker_result.v0"
    with pytest.raises(ContractValidationError, match="invalid_schema_version"):
        SyntheticWorkerResult.from_dict(wrong_schema)
    bad_identity = dict(valid)
    bad_identity["master_request_id"] = 12
    with pytest.raises(ContractValidationError, match="invalid_master_request_id"):
        SyntheticWorkerResult.from_dict(bad_identity)
    bad_specialist = dict(valid)
    bad_specialist["specialist"] = "NOT_A_SPECIALIST"
    with pytest.raises(ContractValidationError, match="invalid_specialist"):
        SyntheticWorkerResult.from_dict(bad_specialist)
    bad_outcome = dict(valid)
    bad_outcome["outcome"] = "MAYBE"
    with pytest.raises(ContractValidationError, match="invalid_outcome"):
        SyntheticWorkerResult.from_dict(bad_outcome)
    padded = dict(valid)
    padded["note"] = " synthetic"
    with pytest.raises(ContractValidationError, match="invalid_synthetic_note"):
        SyntheticWorkerResult.from_dict(padded)
    oversized = dict(valid)
    oversized["note"] = "x" * 513
    with pytest.raises(ContractValidationError, match="invalid_synthetic_note"):
        SyntheticWorkerResult.from_dict(oversized)
    coerced = dict(valid)
    coerced["evidence_verified"] = 0
    with pytest.raises(ContractValidationError, match="synthetic_evidence_not_verified"):
        SyntheticWorkerResult.from_dict(coerced)
    missing_authority = dict(valid)
    del missing_authority["production_authority"]
    with pytest.raises(ContractValidationError, match="missing_production_authority"):
        SyntheticWorkerResult.from_dict(missing_authority)


def test_stored_gates_are_unchanged_after_a_successful_dependency():
    proposal = _proposal((_live("live-1"), _history("hist-1", depends_on=("live-1",))), _work(LIVE, HISTORY))
    before = authorize_route(proposal, _context()).plan.to_dict()
    result, _calls = _run((_live("live-1"), _history("hist-1", depends_on=("live-1",))), _work(LIVE, HISTORY))
    assert result.decision.plan.to_dict() == before
    assert result.decision.plan.steps[1].gates == (MasterRouteGate.DEPENDENCY_BLOCKED,)
    assert result.decision.plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert result.decision.plan.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED


def test_installed_langgraph_version_is_pinned():
    assert importlib.metadata.version("langgraph") == "1.2.16"
    result, _calls = _run((_live(),))
    assert result.langgraph_version == "1.2.16"
