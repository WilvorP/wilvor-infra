"""Sprint 4 Agent D: verified multi-specialist Master runtime composition.

The graph reuses the existing four-node LangGraph shape: validate, select
ready steps, invoke one native specialist, then finalize. Workers are the
Sprint 3C Live Ops ``run`` path and the Sprint 3D Historical/Decision
native entrypoints. Synthetic MA3 results cannot enter MA4.

Trusted authorization is a consumed grant plus a current fence. Models do
not authorize AWS tools. Native success is not source authentication.
Production dispatch, live model APIs, and ``claims_admitted`` stay closed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any, TypedDict

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.decision_specialist_runtime_contracts import DecisionSpecialistRunResult
from wilvor_ai.live_ops_public_contracts import LiveOpsTrustedContext
from wilvor_ai.master_authorized_scheduler_adapter import PRODUCTION_DISPATCH_ENABLED
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_decision_binding import MasterDecisionBinding
from wilvor_ai.master_decision_execution_readiness import MasterDecisionExecutionReadiness
from wilvor_ai.master_decision_target_resolver import FakeDecisionReadCatalog
from wilvor_ai.master_evidence_admission import (
    LiveOpsAdmissionSources,
    MasterEvidenceAdmissionCollection,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
    admit_master_evidence,
)
from wilvor_ai.master_evidence_corroboration import MasterEvidenceCorroborationInput
from wilvor_ai.master_historical_binding import MasterHistoricalBinding
from wilvor_ai.master_live_ops_binding import MasterLiveOpsBinding
from wilvor_ai.master_orchestration import (
    SYNTHETIC_RECURSION_LIMIT,
    reject_planning_snapshot,
    reserve_dispatch_slots,
)
from wilvor_ai.master_parallel_contracts import (
    MasterParallelContractBundle,
    MasterResponseLimitation,
    MasterResponsePolicyHandoff,
)
from wilvor_ai.master_response_policy import evaluate_master_response_policy
from wilvor_ai.master_response_renderer import MasterResponseRender, render_master_response
from wilvor_ai.master_routing import (
    MAX_ROUTE_STEPS,
    REQUEST_UNDERSTANDING_LIMITATION,
    MasterPlanningContext,
    MasterRequestCoverage,
    MasterRouteDecision,
    MasterRouteExecutionAuthority,
    MasterRouteOutcome,
    MasterRouteStepPlan,
)
from wilvor_ai.master_specialist_adapters import describe_planned_adapters
from wilvor_ai.master_specialist_runtime_contract import invoke_specialist
from wilvor_ai.specialist_contracts import SpecialistResult
from wilvor_ai.specialist_runtime_contracts import HistoricalSpecialistRunResult
from wilvor_ai.sprint3c_specialist_orchestration import (
    NATIVE_EXECUTION_IS_SOURCE_EVIDENCE,
    NATIVE_PRODUCTION_DISPATCH_AVAILABLE,
    Sprint3CConsumedDispatchAuthority,
    Sprint3CLiveOpsNativeExecution,
    Sprint3CLiveOpsRuntimeInjection,
    Sprint3CNativeSpecialistOrchestrator,
    assert_production_dispatch_closed,
)
from wilvor_ai.sprint3d_native_specialists import (
    Sprint3DDecisionNativeExecution,
    Sprint3DHistoricalNativeExecution,
    execute_decision_native,
    execute_historical_native,
)
from wilvor_ai.sprint4_master_planning import (
    Sprint4MasterPlanningResult,
    Sprint4PlanningStatus,
    plan_master_request,
)
from wilvor_ai.sprint4_master_providers import SPRINT4_LIVE_MODEL_INVOCATION_AVAILABLE


SPRINT4_MASTER_RUNTIME_SCHEMA_VERSION = "wilvor.ai.sprint4.master_runtime.v1"
SPRINT4_NATIVE_PRODUCTION_DISPATCH_AVAILABLE = False
SPRINT4_CLAIMS_ADMITTED = False
NATIVE_GRAPH_NODES = (
    "validate_input",
    "select_ready",
    "invoke_native_specialist",
    "finalize_workflow",
)
NATIVE_EVIDENCE_LIMITATION = (
    "Native specialist output is not authenticated source evidence and "
    "grants no production execution authority."
)
_SYNTHETIC_RESULT_NAMES = frozenset({"SyntheticWorkerResult", "SyntheticWorkflowResult"})


class Sprint4StepStatus(str, Enum):
    WAITING = "WAITING"
    DISPATCHED = "DISPATCHED"
    NATIVE_COMPLETED = "NATIVE_COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    OMITTED = "OMITTED"


class Sprint4TerminalStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    NATIVE_COMPLETED = "NATIVE_COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


_TERMINAL_STATUSES = frozenset(
    {
        Sprint4StepStatus.NATIVE_COMPLETED,
        Sprint4StepStatus.FAILED,
        Sprint4StepStatus.TIMED_OUT,
        Sprint4StepStatus.CANCELLED,
        Sprint4StepStatus.BLOCKED,
        Sprint4StepStatus.OMITTED,
    }
)


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _closed(value: object, denied: object, code: str) -> None:
    if value is not denied:
        _fail(code)


def _assert_closed_surface() -> None:
    _closed(PRODUCTION_DISPATCH_ENABLED, False, "production_dispatch_disabled")
    _closed(NATIVE_PRODUCTION_DISPATCH_AVAILABLE, False, "production_dispatch_disabled")
    _closed(SPRINT4_NATIVE_PRODUCTION_DISPATCH_AVAILABLE, False, "production_dispatch_disabled")
    _closed(SPRINT4_CLAIMS_ADMITTED, False, "operational_claim_not_admitted")
    _closed(SPRINT4_LIVE_MODEL_INVOCATION_AVAILABLE, False, "live_model_invocation_unavailable")
    _closed(NATIVE_EXECUTION_IS_SOURCE_EVIDENCE, False, "source_authenticity_not_established")
    assert_production_dispatch_closed()


def _reject_synthetic_evidence(artifact: object) -> None:
    if type(artifact).__name__ in _SYNTHETIC_RESULT_NAMES:
        _fail("synthetic_result_not_evidence")


@dataclass(frozen=True)
class Sprint4LiveOpsDispatch:
    """Consumed Live Ops path. The runtime is injected and offline."""

    binding: MasterLiveOpsBinding
    runtime: Sprint3CLiveOpsRuntimeInjection

    def __post_init__(self) -> None:
        if not isinstance(self.binding, MasterLiveOpsBinding):
            _fail("invalid_live_ops_binding")
        if not isinstance(self.runtime, Sprint3CLiveOpsRuntimeInjection):
            _fail("model_origin_dispatch_rejected")


@dataclass(frozen=True)
class Sprint4HistoricalDispatch:
    """Consumed Historical path through the Sprint 3D native entrypoint."""

    binding: MasterHistoricalBinding
    host: object
    port: object
    provider: object
    operations: object
    cancelled: Callable[[], bool] | None = None
    deadline_epoch: int | None = None
    observed_epoch: int | None = None
    tool_call_id_factory: Callable[[], str] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.binding, MasterHistoricalBinding):
            _fail("invalid_historical_binding")


@dataclass(frozen=True)
class Sprint4DecisionDispatch:
    """Consumed Decision path through the Sprint 3D native entrypoint."""

    readiness: MasterDecisionExecutionReadiness
    reads: FakeDecisionReadCatalog
    host: object
    port: object
    provider: object
    tables: object
    source_epoch: int
    query_timestamp_utc: str
    user_text: str
    binding: MasterDecisionBinding | None = None
    cancelled: Callable[[], bool] | None = None
    deadline_epoch: int | None = None
    observed_epoch: int | None = None
    tool_call_id_factory: Callable[[], str] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.readiness, MasterDecisionExecutionReadiness):
            _fail("invalid_decision_readiness")
        if not isinstance(self.reads, FakeDecisionReadCatalog):
            _fail("invalid_decision_read")


@dataclass(frozen=True)
class Sprint4NativeDispatchHandle:
    """One invocation's consumed authority and specialist payload."""

    specialist: MasterSpecialist
    authority: Sprint3CConsumedDispatchAuthority
    live_ops: Sprint4LiveOpsDispatch | None = None
    historical: Sprint4HistoricalDispatch | None = None
    decision: Sprint4DecisionDispatch | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.specialist, MasterSpecialist):
            _fail("invalid_specialist")
        if not isinstance(self.authority, Sprint3CConsumedDispatchAuthority):
            _fail("consumed_grant_required")
        _closed(self.authority.production_dispatch, False, "production_dispatch_disabled")
        if self.specialist is MasterSpecialist.LIVE_OPS:
            if not isinstance(self.live_ops, Sprint4LiveOpsDispatch):
                _fail("invalid_live_ops_binding")
            if self.historical is not None or self.decision is not None:
                _fail("cross_specialist_execution_rejected")
        elif self.specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
            if not isinstance(self.historical, Sprint4HistoricalDispatch):
                _fail("invalid_historical_binding")
            if self.live_ops is not None or self.decision is not None:
                _fail("cross_specialist_execution_rejected")
        elif self.specialist is MasterSpecialist.DECISION:
            if not isinstance(self.decision, Sprint4DecisionDispatch):
                _fail("invalid_decision_readiness")
            if self.live_ops is not None or self.historical is not None:
                _fail("cross_specialist_execution_rejected")
        else:
            _fail("invalid_specialist")


@dataclass(frozen=True)
class Sprint4NativeStepResult:
    """Native specialist artifact kept beside the planned invocation."""

    invocation_id: str
    specialist: MasterSpecialist
    live_ops: Sprint3CLiveOpsNativeExecution | None = None
    historical: Sprint3DHistoricalNativeExecution | None = None
    decision: Sprint3DDecisionNativeExecution | None = None
    live_ops_context: LiveOpsTrustedContext | None = None
    production_dispatch: bool = False
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    native_execution_is_source_evidence: bool = False

    def __post_init__(self) -> None:
        _reject_synthetic_evidence(self.live_ops)
        _reject_synthetic_evidence(self.historical)
        _reject_synthetic_evidence(self.decision)
        _closed(self.production_dispatch, False, "production_dispatch_disabled")
        _closed(
            self.source_authenticity,
            MasterSourceAuthenticity.NOT_ESTABLISHED,
            "source_authenticity_not_established",
        )
        _closed(
            self.native_execution_is_source_evidence,
            False,
            "source_authenticity_not_established",
        )
        present = [item is not None for item in (self.live_ops, self.historical, self.decision)]
        if sum(present) != 1:
            _fail("native_result_not_bound")
        if self.specialist is MasterSpecialist.LIVE_OPS:
            if not isinstance(self.live_ops, Sprint3CLiveOpsNativeExecution):
                _fail("invalid_live_ops_result")
            if not isinstance(self.live_ops_context, LiveOpsTrustedContext):
                _fail("invalid_live_ops_context")
        elif self.specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
            if not isinstance(self.historical, Sprint3DHistoricalNativeExecution):
                _fail("invalid_historical_run")
        elif self.specialist is MasterSpecialist.DECISION:
            if not isinstance(self.decision, Sprint3DDecisionNativeExecution):
                _fail("invalid_decision_result")


def _parent_invocation_ids(
    step: MasterRouteStepPlan,
    by_step_id: Mapping[str, MasterRouteStepPlan],
) -> tuple[str, ...]:
    return tuple(by_step_id[item].invocation.invocation_id for item in step.depends_on)


def _permanently_blocked(step: MasterRouteStepPlan) -> bool:
    from wilvor_ai.master_routing import MasterRouteGate

    allowed = {MasterRouteGate.NOT_REQUIRED, MasterRouteGate.DEPENDENCY_BLOCKED}
    return any(gate not in allowed for gate in step.gates)


def merge_step_statuses(
    left: Mapping[str, Sprint4StepStatus] | None,
    right: Mapping[str, Sprint4StepStatus] | None,
) -> dict[str, Sprint4StepStatus]:
    merged = dict(left or {})
    for key, value in dict(right or {}).items():
        if not isinstance(value, Sprint4StepStatus):
            _fail("invalid_step_status")
        current = merged.get(key)
        if current is None:
            merged[key] = value
            continue
        if current in _TERMINAL_STATUSES or current is value:
            _fail("duplicate_native_result")
        if current is Sprint4StepStatus.WAITING and value in {
            Sprint4StepStatus.DISPATCHED,
            Sprint4StepStatus.BLOCKED,
            Sprint4StepStatus.OMITTED,
        }:
            merged[key] = value
            continue
        if current is Sprint4StepStatus.DISPATCHED and value in _TERMINAL_STATUSES:
            merged[key] = value
            continue
        if value is Sprint4StepStatus.DISPATCHED:
            _fail("duplicate_dispatch")
        _fail("duplicate_native_result")
    return merged


def bind_native_result_reducer(known_invocation_ids: frozenset[str]):
    def merge_results(
        left: Mapping[str, Sprint4NativeStepResult] | None,
        right: Mapping[str, Sprint4NativeStepResult] | None,
    ) -> dict[str, Sprint4NativeStepResult]:
        merged = dict(left or {})
        for key, value in dict(right or {}).items():
            if key not in known_invocation_ids:
                _fail("unknown_invocation_id")
            if not isinstance(value, Sprint4NativeStepResult):
                _reject_synthetic_evidence(value)
                _fail("unexpected_native_result")
            if value.invocation_id != key:
                _fail("invocation_id_mismatch")
            if key in merged:
                _fail("duplicate_native_result")
            merged[key] = value
        return merged

    return merge_results


def _live_ops_context(
    handle: Sprint4NativeDispatchHandle,
    execution: Sprint3CLiveOpsNativeExecution,
) -> LiveOpsTrustedContext:
    runtime = handle.live_ops.runtime  # type: ignore[union-attr]
    identity = execution.identity
    return LiveOpsTrustedContext(
        tables=runtime.tables,
        now_epoch=runtime.now_epoch,
        correlation_id=identity.correlation_id,
        max_model_turns=identity.max_model_turns,
        max_tool_calls=identity.max_tool_calls,
        clock=runtime.clock,
        deadline_epoch=runtime.deadline_epoch,
        cancelled=runtime.cancelled,
    )


def _dispatch_native(
    orchestrator: Sprint3CNativeSpecialistOrchestrator,
    step: MasterRouteStepPlan,
    handle: Sprint4NativeDispatchHandle,
    *,
    now_utc: str,
) -> Sprint4NativeStepResult:
    invocation = step.invocation
    if handle.specialist is not invocation.specialist:
        _fail("specialist_mismatch")
    if handle.specialist is MasterSpecialist.LIVE_OPS:
        live = handle.live_ops
        assert live is not None
        executed = orchestrator.execute_live_ops(
            invocation,
            live.binding,
            handle.authority,
            live.runtime,
            now_utc=now_utc,
        )
        return Sprint4NativeStepResult(
            invocation_id=invocation.invocation_id,
            specialist=MasterSpecialist.LIVE_OPS,
            live_ops=executed,
            live_ops_context=_live_ops_context(handle, executed),
        )
    if handle.specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
        historical = handle.historical
        assert historical is not None
        executed = execute_historical_native(
            orchestrator,
            invocation,
            historical.binding,
            handle.authority,
            now_utc=now_utc,
            host=historical.host,  # type: ignore[arg-type]
            port=historical.port,  # type: ignore[arg-type]
            provider=historical.provider,
            operations=historical.operations,
            cancelled=historical.cancelled,
            deadline_epoch=historical.deadline_epoch,
            observed_epoch=historical.observed_epoch,
            tool_call_id_factory=historical.tool_call_id_factory,
        )
        return Sprint4NativeStepResult(
            invocation_id=invocation.invocation_id,
            specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
            historical=executed,
        )
    decision = handle.decision
    assert decision is not None
    executed = execute_decision_native(
        orchestrator,
        invocation,
        decision.readiness,
        decision.reads,
        handle.authority,
        now_utc=now_utc,
        user_text=decision.user_text,
        host=decision.host,  # type: ignore[arg-type]
        port=decision.port,  # type: ignore[arg-type]
        provider=decision.provider,
        tables=decision.tables,
        source_epoch=decision.source_epoch,
        query_timestamp_utc=decision.query_timestamp_utc,
        binding=decision.binding,
        cancelled=decision.cancelled,
        deadline_epoch=decision.deadline_epoch,
        observed_epoch=decision.observed_epoch,
        tool_call_id_factory=decision.tool_call_id_factory,
    )
    return Sprint4NativeStepResult(
        invocation_id=invocation.invocation_id,
        specialist=MasterSpecialist.DECISION,
        decision=executed,
    )


def _classify_terminal(
    plan,
    statuses: Mapping[str, Sprint4StepStatus],
    budget_exhausted: bool,
) -> tuple[Sprint4TerminalStatus, tuple[str, ...]]:
    if budget_exhausted:
        return Sprint4TerminalStatus.BUDGET_EXHAUSTED, ("dispatch_budget_exhausted",)
    reasons: list[str] = []
    saw_cancel = any(status is Sprint4StepStatus.CANCELLED for status in statuses.values())
    saw_required_failure = False
    saw_required_block = False
    saw_optional_issue = False
    for step in plan.steps:
        invocation_id = step.invocation.invocation_id
        status = statuses[invocation_id]
        required = step.invocation.requirement is MasterRequirement.REQUIRED
        if status is Sprint4StepStatus.NATIVE_COMPLETED:
            continue
        if status is Sprint4StepStatus.CANCELLED:
            reasons.append(f"worker_cancelled:{step.step_id}")
            continue
        if status is Sprint4StepStatus.WAITING and saw_cancel:
            reasons.append(f"not_executed_cancelled:{step.step_id}")
            continue
        if status is Sprint4StepStatus.FAILED:
            reasons.append(f"worker_failed:{step.step_id}")
        elif status is Sprint4StepStatus.OMITTED:
            reasons.append(f"optional_worker_missing:{step.step_id}")
        elif status is Sprint4StepStatus.BLOCKED:
            reasons.append(f"blocked_dependency:{step.step_id}")
        elif status is Sprint4StepStatus.TIMED_OUT:
            reasons.append(f"worker_timed_out:{step.step_id}")
        else:
            reasons.append(f"step_not_completed:{step.step_id}:{status.value}")
        if status in {Sprint4StepStatus.FAILED, Sprint4StepStatus.TIMED_OUT} and required:
            saw_required_failure = True
        elif required:
            saw_required_block = True
        else:
            saw_optional_issue = True
    if saw_cancel:
        return Sprint4TerminalStatus.CANCELLED, tuple(reasons)
    if saw_required_failure:
        return Sprint4TerminalStatus.FAILED, tuple(reasons)
    if saw_required_block:
        return Sprint4TerminalStatus.BLOCKED, tuple(reasons)
    if saw_optional_issue:
        return Sprint4TerminalStatus.PARTIAL, tuple(reasons)
    return Sprint4TerminalStatus.NATIVE_COMPLETED, ("native_specialists_completed",)


def compile_native_master_graph(
    handles: Mapping[str, Sprint4NativeDispatchHandle],
    *,
    nonce: str,
    decision: MasterRouteDecision,
    orchestrator: Sprint3CNativeSpecialistOrchestrator,
    now_utc: str,
):
    """Compile one native specialist graph for an authorized MA2 plan."""

    _assert_closed_surface()
    if decision.outcome is not MasterRouteOutcome.AUTHORIZED_PLAN or decision.plan is None:
        _fail("authorized_plan_required")
    if type(orchestrator) is not Sprint3CNativeSpecialistOrchestrator:
        _fail("invalid_schedule")
    if not isinstance(handles, Mapping) or isinstance(handles, (str, bytes)):
        _fail("invalid_dispatch_handles")
    if len(handles) > MAX_ROUTE_STEPS:
        _fail("excessive_plan_size")
    registry = dict(handles)
    for key, handle in registry.items():
        if not isinstance(key, str) or not isinstance(handle, Sprint4NativeDispatchHandle):
            _fail("invalid_dispatch_handles")
    plan = decision.plan
    known = tuple(step.invocation.invocation_id for step in plan.steps)
    known_set = frozenset(known)
    by_step_id = {step.step_id: step for step in plan.steps}
    by_invocation = {step.invocation.invocation_id: step for step in plan.steps}
    merge_results = bind_native_result_reducer(known_set)

    NativeGraphState = TypedDict(
        "NativeGraphState",
        {
            "ticket_nonce": Annotated[str, reject_planning_snapshot],
            "decision": Annotated[MasterRouteDecision, reject_planning_snapshot],
            "statuses": Annotated[dict[str, Sprint4StepStatus], merge_step_statuses],
            "results": Annotated[dict[str, Sprint4NativeStepResult], merge_results],
            "ready": list[str],
            "dispatch_count": int,
            "budget_exhausted": bool,
            "terminal_status": str,
            "reason_codes": list[str],
        },
    )

    def validate_input(state):
        if state.get("ticket_nonce") != nonce or state.get("decision") is not decision:
            _fail("invalid_native_ticket")
        if state.get("results") or state.get("statuses") or state.get("ready"):
            _fail("forged_native_state")
        if decision.execution_authority is not MasterRouteExecutionAuthority.DENIED:
            _fail("execution_authority_not_granted")
        statuses = {}
        for step in plan.steps:
            invocation_id = step.invocation.invocation_id
            if _permanently_blocked(step):
                statuses[invocation_id] = Sprint4StepStatus.BLOCKED
            else:
                statuses[invocation_id] = Sprint4StepStatus.WAITING
        return {
            "statuses": statuses,
            "ready": [],
            "dispatch_count": 0,
            "budget_exhausted": False,
            "terminal_status": "",
            "reason_codes": [],
        }

    def select_ready(state):
        if state["budget_exhausted"]:
            return {"ready": []}
        statuses = dict(state["statuses"])
        if any(status is Sprint4StepStatus.CANCELLED for status in statuses.values()):
            return {"ready": []}
        updates: dict[str, Sprint4StepStatus] = {}
        for step in plan.steps:
            invocation_id = step.invocation.invocation_id
            if statuses.get(invocation_id) is not Sprint4StepStatus.WAITING:
                continue
            if _permanently_blocked(step):
                updates[invocation_id] = Sprint4StepStatus.BLOCKED
                statuses[invocation_id] = Sprint4StepStatus.BLOCKED
                continue
            parent_ids = _parent_invocation_ids(step, by_step_id)
            parent_statuses = tuple(statuses.get(item) for item in parent_ids)
            if parent_ids and all(item in _TERMINAL_STATUSES for item in parent_statuses):
                if any(item is not Sprint4StepStatus.NATIVE_COMPLETED for item in parent_statuses):
                    updates[invocation_id] = Sprint4StepStatus.BLOCKED
                    statuses[invocation_id] = Sprint4StepStatus.BLOCKED
        eligible: list[str] = []
        for step in plan.steps:
            invocation_id = step.invocation.invocation_id
            if statuses.get(invocation_id) is not Sprint4StepStatus.WAITING:
                continue
            parent_ids = _parent_invocation_ids(step, by_step_id)
            if all(statuses.get(item) is Sprint4StepStatus.NATIVE_COMPLETED for item in parent_ids):
                eligible.append(invocation_id)
        reserved = reserve_dispatch_slots(state["dispatch_count"], tuple(eligible))
        if reserved is None:
            return {"statuses": updates, "ready": [], "budget_exhausted": True}
        for invocation_id in eligible:
            updates[invocation_id] = Sprint4StepStatus.DISPATCHED
        return {
            "statuses": updates,
            "ready": eligible,
            "dispatch_count": reserved,
        }

    def route_ready(state):
        ready = state.get("ready") or []
        if not ready:
            return "finalize_workflow"
        return [
            Send("invoke_native_specialist", {"invocation_id": invocation_id})
            for invocation_id in ready
        ]

    def invoke_native_specialist(state):
        invocation_id = state["invocation_id"]  # type: ignore[index]
        step = by_invocation[invocation_id]
        handle = registry.get(invocation_id)
        if handle is None:
            if step.invocation.requirement is MasterRequirement.OPTIONAL:
                return {"statuses": {invocation_id: Sprint4StepStatus.OMITTED}}
            return {"statuses": {invocation_id: Sprint4StepStatus.FAILED}}
        try:
            result = _dispatch_native(orchestrator, step, handle, now_utc=now_utc)
        except ContractValidationError as exc:
            if exc.errors == ("deadline_expired",):
                return {"statuses": {invocation_id: Sprint4StepStatus.TIMED_OUT}}
            if exc.errors == ("schedule_cancelled",):
                return {"statuses": {invocation_id: Sprint4StepStatus.CANCELLED}}
            if step.invocation.requirement is MasterRequirement.OPTIONAL and exc.errors == (
                "consumed_grant_required",
            ):
                return {"statuses": {invocation_id: Sprint4StepStatus.OMITTED}}
            raise
        return {
            "statuses": {invocation_id: Sprint4StepStatus.NATIVE_COMPLETED},
            "results": {invocation_id: result},
        }

    def finalize_workflow(state):
        status, reasons = _classify_terminal(
            plan,
            state["statuses"],
            bool(state["budget_exhausted"]),
        )
        return {"terminal_status": status.value, "reason_codes": list(reasons), "ready": []}

    builder = StateGraph(NativeGraphState)
    builder.add_node("validate_input", validate_input)
    builder.add_node("select_ready", select_ready)
    builder.add_node("invoke_native_specialist", invoke_native_specialist)
    builder.add_node("finalize_workflow", finalize_workflow)
    builder.add_edge(START, "validate_input")
    builder.add_edge("validate_input", "select_ready")
    builder.add_conditional_edges(
        "select_ready",
        route_ready,
        ["invoke_native_specialist", "finalize_workflow"],
    )
    builder.add_edge("invoke_native_specialist", "select_ready")
    builder.add_edge("finalize_workflow", END)
    return builder.compile()


def execute_authorized_native_plan(
    decision: MasterRouteDecision,
    orchestrator: Sprint3CNativeSpecialistOrchestrator,
    handles: Mapping[str, Sprint4NativeDispatchHandle],
    *,
    now_utc: str,
    recursion_limit: int | None = None,
) -> "Sprint4NativeWorkflowResult":
    """Run native specialists for one authorized plan. Not production dispatch."""

    from secrets import token_hex

    _assert_closed_surface()
    if decision.outcome is not MasterRouteOutcome.AUTHORIZED_PLAN or decision.plan is None:
        return Sprint4NativeWorkflowResult(
            terminal_status=Sprint4TerminalStatus.NOT_RUN,
            reasons=decision.reasons,
            decision=decision,
            step_statuses=(),
            results=(),
            limitations=(NATIVE_EVIDENCE_LIMITATION, REQUEST_UNDERSTANDING_LIMITATION),
            dispatch_count=0,
            executed_nodes=(),
        )
    limit = SYNTHETIC_RECURSION_LIMIT if recursion_limit is None else recursion_limit
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        _fail("invalid_recursion_limit")
    nonce = token_hex(16)
    graph = compile_native_master_graph(
        handles,
        nonce=nonce,
        decision=decision,
        orchestrator=orchestrator,
        now_utc=now_utc,
    )
    initial = {
        "ticket_nonce": nonce,
        "decision": decision,
        "statuses": {},
        "results": {},
        "ready": [],
        "dispatch_count": 0,
        "budget_exhausted": False,
        "terminal_status": "",
        "reason_codes": [],
    }
    executed: list[str] = []
    last_values: Mapping[str, Any] | None = None
    try:
        for kind, payload in graph.stream(
            initial,
            stream_mode=["updates", "values"],
            config={"recursion_limit": limit},
        ):
            if kind == "values":
                last_values = payload
                continue
            node_name, _update = next(iter(payload.items()))
            executed.append(node_name)
    except GraphRecursionError:
        statuses = {} if last_values is None else dict(last_values.get("statuses") or {})
        results = {} if last_values is None else dict(last_values.get("results") or {})
        return _workflow_result(
            Sprint4TerminalStatus.BUDGET_EXHAUSTED,
            ("graph_recursion_exhausted",),
            decision,
            statuses,
            results,
            0 if last_values is None else int(last_values.get("dispatch_count") or 0),
            tuple(executed),
        )
    if last_values is None or not last_values.get("terminal_status"):
        _fail("native_workflow_did_not_finish")
    return _workflow_result(
        Sprint4TerminalStatus(last_values["terminal_status"]),
        tuple(last_values.get("reason_codes") or ()),
        decision,
        dict(last_values.get("statuses") or {}),
        dict(last_values.get("results") or {}),
        int(last_values.get("dispatch_count") or 0),
        tuple(executed),
    )


def _workflow_result(
    terminal: Sprint4TerminalStatus,
    reasons: tuple[str, ...],
    decision: MasterRouteDecision,
    statuses: Mapping[str, Sprint4StepStatus],
    results: Mapping[str, Sprint4NativeStepResult],
    dispatch_count: int,
    executed_nodes: tuple[str, ...],
) -> "Sprint4NativeWorkflowResult":
    plan = decision.plan
    ordered_statuses = ()
    ordered_results = ()
    if plan is not None:
        ordered_statuses = tuple(
            (
                step.invocation.invocation_id,
                statuses.get(step.invocation.invocation_id, Sprint4StepStatus.WAITING),
            )
            for step in plan.steps
        )
        ordered_results = tuple(
            results[step.invocation.invocation_id]
            for step in plan.steps
            if step.invocation.invocation_id in results
        )
    return Sprint4NativeWorkflowResult(
        terminal_status=terminal,
        reasons=reasons,
        decision=decision,
        step_statuses=ordered_statuses,
        results=ordered_results,
        limitations=(NATIVE_EVIDENCE_LIMITATION, decision.understanding_limitation),
        dispatch_count=dispatch_count,
        executed_nodes=executed_nodes,
    )


@dataclass(frozen=True)
class Sprint4NativeWorkflowResult:
    """Native graph output. It is not a final aviation answer."""

    terminal_status: Sprint4TerminalStatus
    reasons: tuple[str, ...]
    decision: MasterRouteDecision
    step_statuses: tuple[tuple[str, Sprint4StepStatus], ...]
    results: tuple[Sprint4NativeStepResult, ...]
    limitations: tuple[str, ...]
    dispatch_count: int
    executed_nodes: tuple[str, ...]
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    evidence_verified: bool = False
    schema_version: str = SPRINT4_MASTER_RUNTIME_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SPRINT4_MASTER_RUNTIME_SCHEMA_VERSION:
            _fail("invalid_schema_version")
        _closed(self.execution_authority, MasterRouteExecutionAuthority.DENIED, "execution_authority_not_granted")
        _closed(
            self.request_coverage,
            MasterRequestCoverage.NOT_ESTABLISHED,
            "request_coverage_not_established",
        )
        _closed(self.evidence_verified, False, "synthetic_evidence_not_verified")
        for item in self.results:
            _reject_synthetic_evidence(item)


def _admission_artifact(result: Sprint4NativeStepResult) -> tuple[object, HistoricalSpecialistRunResult | None]:
    if result.specialist is MasterSpecialist.LIVE_OPS:
        live = result.live_ops
        assert live is not None and result.live_ops_context is not None
        return (
            LiveOpsAdmissionSources(
                request=live.bridge.request,
                context=result.live_ops_context,
                result=live.native_result,
            ),
            None,
        )
    if result.specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
        historical = result.historical
        assert historical is not None
        if not isinstance(historical.final_result, SpecialistResult):
            _fail("historical_finalization_not_performed")
        return historical.final_result, historical.intermediate_run
    decision = result.decision
    assert decision is not None
    if not isinstance(decision.native_result, DecisionSpecialistRunResult):
        _fail("invalid_decision_result")
    return decision.native_result, None


def synthesize_grounded_response(
    context: MasterPlanningContext,
    workflow: Sprint4NativeWorkflowResult,
) -> "Sprint4GroundedSynthesis":
    """MA4 admission plus MA5/MA6 rendering. Claims stay suppressed."""

    _assert_closed_surface()
    if workflow.decision.plan is None:
        _fail("authorized_plan_required")
    plan = workflow.decision.plan
    by_id = {item.invocation_id: item for item in workflow.results}
    evidence_inputs: list[MasterEvidenceCorroborationInput] = []
    for step in plan.steps:
        result = by_id.get(step.invocation.invocation_id)
        if result is None:
            continue
        artifact, historical_run = _admission_artifact(result)
        _reject_synthetic_evidence(artifact)
        evidence_inputs.append(
            MasterEvidenceCorroborationInput(
                admission_input=MasterEvidenceAdmissionInput(
                    authorization=step.invocation,
                    artifact=artifact,
                ),
                historical_run=historical_run,
            )
        )
    bundle = MasterParallelContractBundle(
        context=context,
        plan=plan,
        evidence_inputs=tuple(evidence_inputs),
    )
    adapters = describe_planned_adapters(bundle)
    admissions = admit_master_evidence(
        tuple(item.admission_input for item in evidence_inputs)
    )
    handoff = evaluate_master_response_policy(bundle)
    rendered = render_master_response(handoff)
    if rendered.claims_admitted is not False:
        _fail("operational_claim_not_admitted")
    if admissions.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
        _fail("source_authenticity_not_established")
    required = {
        MasterResponseLimitation.SOURCE_AUTHENTICITY_NOT_ESTABLISHED,
        MasterResponseLimitation.PRODUCTION_AUTHORITY_DENIED,
        MasterResponseLimitation.NO_OPERATIONAL_CLAIM,
        MasterResponseLimitation.EXECUTION_DENIED,
    }
    if not required.issubset(set(handoff.limitations)):
        _fail("response_limitations_incomplete")
    return Sprint4GroundedSynthesis(
        bundle=bundle,
        adapters=adapters.descriptors,
        admissions=admissions,
        handoff=handoff,
        rendered=rendered,
    )


@dataclass(frozen=True)
class Sprint4GroundedSynthesis:
    """Closed MA5 render of native specialist evidence. Not an operational answer."""

    bundle: MasterParallelContractBundle
    adapters: tuple[Any, ...]
    admissions: MasterEvidenceAdmissionCollection
    handoff: MasterResponsePolicyHandoff
    rendered: MasterResponseRender
    claims_admitted: bool = False
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    schema_version: str = SPRINT4_MASTER_RUNTIME_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SPRINT4_MASTER_RUNTIME_SCHEMA_VERSION:
            _fail("invalid_schema_version")
        _closed(self.claims_admitted, False, "operational_claim_not_admitted")
        _closed(self.rendered.claims_admitted, False, "operational_claim_not_admitted")
        _closed(
            self.production_authority,
            MasterProductionAuthority.DENIED,
            "production_authority_not_issued",
        )
        _closed(
            self.source_authenticity,
            MasterSourceAuthenticity.NOT_ESTABLISHED,
            "source_authenticity_not_established",
        )


@dataclass(frozen=True)
class Sprint4MasterRuntimeResult:
    """End-to-end Master composition for one request."""

    planning: Sprint4MasterPlanningResult
    workflow: Sprint4NativeWorkflowResult | None
    synthesis: Sprint4GroundedSynthesis | None
    schema_version: str = SPRINT4_MASTER_RUNTIME_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SPRINT4_MASTER_RUNTIME_SCHEMA_VERSION:
            _fail("invalid_schema_version")
        if not isinstance(self.planning, Sprint4MasterPlanningResult):
            _fail("invalid_planning_status")
        if self.workflow is not None and not isinstance(self.workflow, Sprint4NativeWorkflowResult):
            _fail("invalid_native_result")
        if self.synthesis is not None and not isinstance(self.synthesis, Sprint4GroundedSynthesis):
            _fail("invalid_response_policy")


def run_sprint4_master_runtime(
    context: MasterPlanningContext,
    planner: object,
    *,
    orchestrator: Sprint3CNativeSpecialistOrchestrator,
    handles: Mapping[str, Sprint4NativeDispatchHandle],
    now_utc: str,
    retained_decision: MasterRouteDecision | None = None,
    recursion_limit: int | None = None,
) -> Sprint4MasterRuntimeResult:
    """Plan with MA2, dispatch native specialists, then render a grounded MA5 reply.

    ``retained_decision`` must be the session-owned MA2 object when native
    dispatch is requested. ``authorize_route`` mints new invocation objects;
    the scheduler admits only the retained ones.
    """

    _assert_closed_surface()
    planning = plan_master_request(context, planner)
    if (
        planning.status is not Sprint4PlanningStatus.AUTHORIZED_PLAN
        or planning.decision is None
        or planning.decision.plan is None
    ):
        return Sprint4MasterRuntimeResult(planning=planning, workflow=None, synthesis=None)
    dispatch_decision = retained_decision if retained_decision is not None else planning.decision
    if dispatch_decision.outcome is not MasterRouteOutcome.AUTHORIZED_PLAN or dispatch_decision.plan is None:
        _fail("authorized_plan_required")
    if retained_decision is not None:
        planned_ids = tuple(step.invocation.invocation_id for step in planning.decision.plan.steps)
        retained_ids = tuple(step.invocation.invocation_id for step in dispatch_decision.plan.steps)
        if planned_ids != retained_ids:
            _fail("invocation_binding_mismatch")
        if dispatch_decision.plan.master_request_id != context.master_request_id:
            _fail("master_request_mismatch")
    workflow = execute_authorized_native_plan(
        dispatch_decision,
        orchestrator,
        handles,
        now_utc=now_utc,
        recursion_limit=recursion_limit,
    )
    synthesis = None
    if workflow.results:
        synthesis = synthesize_grounded_response(context, workflow)
    return Sprint4MasterRuntimeResult(
        planning=planning,
        workflow=workflow,
        synthesis=synthesis,
    )


def refuse_unconsumed_native_dispatch(*_args: object, **_kwargs: object) -> None:
    _fail("consumed_grant_required")


def refuse_synthetic_as_native_evidence(artifact: object) -> None:
    _reject_synthetic_evidence(artifact)
    _fail("adapter_evidence_not_issued")


__all__ = [
    "NATIVE_EVIDENCE_LIMITATION",
    "NATIVE_GRAPH_NODES",
    "SPRINT4_CLAIMS_ADMITTED",
    "SPRINT4_MASTER_RUNTIME_SCHEMA_VERSION",
    "SPRINT4_NATIVE_PRODUCTION_DISPATCH_AVAILABLE",
    "Sprint4DecisionDispatch",
    "Sprint4GroundedSynthesis",
    "Sprint4HistoricalDispatch",
    "Sprint4LiveOpsDispatch",
    "Sprint4MasterRuntimeResult",
    "Sprint4NativeDispatchHandle",
    "Sprint4NativeStepResult",
    "Sprint4NativeWorkflowResult",
    "Sprint4StepStatus",
    "Sprint4TerminalStatus",
    "compile_native_master_graph",
    "execute_authorized_native_plan",
    "refuse_synthetic_as_native_evidence",
    "refuse_unconsumed_native_dispatch",
    "run_sprint4_master_runtime",
    "synthesize_grounded_response",
]
