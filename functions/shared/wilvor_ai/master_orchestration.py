"""Test-only LangGraph orchestration for synthetic Master workers.

Each call compiles the same four-node StateGraph against an immutable copy of
the worker registry supplied by that call. There is no global registry and no
default specialist. The in-process nonce only checks that a run still matches
the decision just produced by ``authorize_route``. It is not an authenticator
and it does not grant production execution.

``execution_authority`` stays ``DENIED``. A synthetic completion is not
verified aviation evidence. Workers already dispatched in one LangGraph
superstep can still finish after a sibling returns a cancellation; this module
does not claim a stronger cancellation guarantee.

Recursion limit 14 was measured on LangGraph 1.2.16. A four-step dependency
chain runs 11 supersteps, and that release rejects ``recursion_limit`` 11
while accepting 12. Fourteen leaves a margin of two. Dispatch is also capped
at the MA2 four-step maximum. A scripted timeout is not wall-clock enforcement.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from importlib.metadata import version as package_version
from secrets import token_hex
from typing import Annotated, Any, Mapping, TypedDict

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.master_contracts import (
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_routing import (
    MAX_ROUTE_STEPS,
    REQUEST_UNDERSTANDING_LIMITATION,
    MasterPlanningContext,
    MasterRequestCoverage,
    MasterRouteDecision,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRouteHints,
    MasterRouteOutcome,
    MasterRoutePlan,
    MasterRouteProposal,
    MasterRouteStepPlan,
    authorize_route,
)


SYNTHETIC_WORKFLOW_SCHEMA_VERSION = "wilvor.ai.synthetic_master_workflow.v1"
SYNTHETIC_RESULT_SCHEMA_VERSION = "wilvor.ai.synthetic_worker_result.v1"
SYNTHETIC_NOTE_MAX_LENGTH = 512
SYNTHETIC_RECURSION_LIMIT = 14
SYNTHETIC_GRAPH_NODES = (
    "validate_input",
    "select_ready",
    "invoke_fake_specialist",
    "finalize_workflow",
)
SYNTHETIC_EVIDENCE_LIMITATION = (
    "Synthetic worker output is not verified aviation evidence and grants no "
    "production execution authority."
)
CANCELLATION_SUPERSTEP_LIMITATION = (
    "Cancellation does not prevent other synthetic workers already dispatched "
    "in the same LangGraph superstep from running."
)
SCRIPTED_TIMEOUT_LIMITATION = (
    "A scripted timeout records a synthetic outcome. It does not prove "
    "wall-clock runtime enforcement."
)
_DEPENDENCY_GATES = frozenset(
    {
        MasterRouteGate.NOT_REQUIRED,
        MasterRouteGate.DEPENDENCY_BLOCKED,
    }
)
_LANGGRAPH_VERSION = package_version("langgraph")


class SyntheticOutcome(str, Enum):
    SYNTHETIC_COMPLETED = "SYNTHETIC_COMPLETED"
    NO_RESULT = "NO_RESULT"
    UNSUPPORTED = "UNSUPPORTED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


class SyntheticStepStatus(str, Enum):
    WAITING = "WAITING"
    DISPATCHED = "DISPATCHED"
    SYNTHETIC_COMPLETED = "SYNTHETIC_COMPLETED"
    NO_RESULT = "NO_RESULT"
    UNSUPPORTED = "UNSUPPORTED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    OMITTED = "OMITTED"


class SyntheticTerminalStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    SYNTHETIC_COMPLETED = "SYNTHETIC_COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


class SyntheticProductionAuthority(str, Enum):
    """Production authority cannot be represented on a synthetic result."""

    DENIED = "DENIED"


_TERMINAL_STATUSES = frozenset(
    {
        SyntheticStepStatus.SYNTHETIC_COMPLETED,
        SyntheticStepStatus.NO_RESULT,
        SyntheticStepStatus.UNSUPPORTED,
        SyntheticStepStatus.PARTIAL,
        SyntheticStepStatus.FAILED,
        SyntheticStepStatus.TIMED_OUT,
        SyntheticStepStatus.CANCELLED,
        SyntheticStepStatus.BLOCKED,
        SyntheticStepStatus.OMITTED,
    }
)
_OUTCOME_STATUS = {
    SyntheticOutcome.SYNTHETIC_COMPLETED: SyntheticStepStatus.SYNTHETIC_COMPLETED,
    SyntheticOutcome.NO_RESULT: SyntheticStepStatus.NO_RESULT,
    SyntheticOutcome.UNSUPPORTED: SyntheticStepStatus.UNSUPPORTED,
    SyntheticOutcome.PARTIAL: SyntheticStepStatus.PARTIAL,
    SyntheticOutcome.FAILED: SyntheticStepStatus.FAILED,
    SyntheticOutcome.TIMED_OUT: SyntheticStepStatus.TIMED_OUT,
    SyntheticOutcome.CANCELLED: SyntheticStepStatus.CANCELLED,
}


def _note(value: Any) -> str:
    if not isinstance(value, str) or len(value) > SYNTHETIC_NOTE_MAX_LENGTH or value != value.strip():
        raise ContractValidationError("invalid_synthetic_note")
    return value


def _deny_synthetic_authority(value: Any) -> SyntheticProductionAuthority:
    if value is not SyntheticProductionAuthority.DENIED:
        raise ContractValidationError("production_authority_not_issued")
    return value


@dataclass(frozen=True)
class SyntheticAssignment:
    """One immutable synthetic task. It carries no service clients or tool arguments."""

    master_request_id: str
    correlation_id: str
    invocation_id: str
    specialist_request_id: str
    step_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    requirement: MasterRequirement
    capability: AgentCapability
    historical_start_utc: str | None
    historical_end_utc: str | None
    untrusted_hints: MasterRouteHints
    known_invocation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.untrusted_hints, MasterRouteHints):
            raise ContractValidationError("invalid_route_hints")
        if self.invocation_id not in self.known_invocation_ids:
            raise ContractValidationError("unknown_invocation_id")


@dataclass(frozen=True)
class SyntheticWorkerResult:
    """A fake worker return. ``evidence_verified`` cannot be true."""

    master_request_id: str
    correlation_id: str
    invocation_id: str
    specialist_request_id: str
    specialist: MasterSpecialist
    outcome: SyntheticOutcome
    note: str
    evidence_verified: bool = False
    production_authority: SyntheticProductionAuthority = SyntheticProductionAuthority.DENIED
    schema_version: str = SYNTHETIC_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SYNTHETIC_RESULT_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.outcome, SyntheticOutcome):
            raise ContractValidationError("invalid_synthetic_outcome")
        _note(self.note)
        if self.evidence_verified is not False:
            raise ContractValidationError("synthetic_evidence_not_verified")
        _deny_synthetic_authority(self.production_authority)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "master_request_id": self.master_request_id,
            "correlation_id": self.correlation_id,
            "invocation_id": self.invocation_id,
            "specialist_request_id": self.specialist_request_id,
            "specialist": self.specialist.value,
            "outcome": self.outcome.value,
            "note": self.note,
            "evidence_verified": False,
            "production_authority": SyntheticProductionAuthority.DENIED.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SyntheticWorkerResult":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_synthetic_result")
        if data.get("evidence_verified") is not False:
            raise ContractValidationError("synthetic_evidence_not_verified")
        if data.get("production_authority") != SyntheticProductionAuthority.DENIED.value:
            raise ContractValidationError("production_authority_not_issued")
        return cls(
            master_request_id=data["master_request_id"],
            correlation_id=data["correlation_id"],
            invocation_id=data["invocation_id"],
            specialist_request_id=data["specialist_request_id"],
            specialist=MasterSpecialist(data["specialist"]),
            outcome=SyntheticOutcome(data["outcome"]),
            note=data["note"],
            evidence_verified=False,
            production_authority=SyntheticProductionAuthority.DENIED,
            schema_version=data["schema_version"],
        )


@dataclass(frozen=True)
class SyntheticWorkflowResult:
    """Structured synthetic workflow output. It is not a final aviation answer."""

    terminal_status: SyntheticTerminalStatus
    reasons: tuple[str, ...]
    decision: MasterRouteDecision | None
    step_statuses: tuple[tuple[str, SyntheticStepStatus], ...]
    results: tuple[SyntheticWorkerResult, ...]
    limitations: tuple[str, ...]
    dispatch_count: int
    dispatch_batches: tuple[tuple[str, ...], ...]
    executed_nodes: tuple[str, ...]
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    end_to_end_fulfilled: bool = False
    evidence_verified: bool = False
    langgraph_version: str = _LANGGRAPH_VERSION
    schema_version: str = SYNTHETIC_WORKFLOW_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SYNTHETIC_WORKFLOW_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if self.execution_authority is not MasterRouteExecutionAuthority.DENIED:
            raise ContractValidationError("production_authority_not_issued")
        if self.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
            raise ContractValidationError("request_coverage_not_established")
        if self.end_to_end_fulfilled:
            raise ContractValidationError("end_to_end_fulfillment_not_granted")
        if self.evidence_verified is not False:
            raise ContractValidationError("synthetic_evidence_not_verified")
        if self.langgraph_version != _LANGGRAPH_VERSION:
            raise ContractValidationError("langgraph_version_mismatch")
        if any(item.evidence_verified or item.production_authority is not SyntheticProductionAuthority.DENIED for item in self.results):
            raise ContractValidationError("synthetic_evidence_not_verified")
        if self.decision is not None and self.decision.plan is not None:
            if self.decision.plan.execution_authority is not MasterRouteExecutionAuthority.DENIED:
                raise ContractValidationError("production_authority_not_issued")
            if self.decision.plan.end_to_end_fulfilled:
                raise ContractValidationError("end_to_end_fulfillment_not_granted")
            if any(item.executable or item.fulfillment.value != "NOT_YET_FULFILLED" for item in self.decision.plan.deferred_synthesis):
                raise ContractValidationError("synthesis_not_fulfilled")


def reject_planning_snapshot(left: Any, right: Any) -> Any:
    """Reject a graph update that replaces the authorized planning snapshot.

    LangGraph stores a reducer channel's first value by calling ``typ()``.
    For a string that default is ``""``, so the nonce channel accepts that
    empty value once and then rejects any different replacement.
    """

    if left is None or left == "":
        return right
    if right != left:
        raise ContractValidationError("planning_snapshot_modified")
    return left


def reserve_dispatch_slots(already_dispatched: int, invocation_ids: tuple[str, ...]) -> int | None:
    """Return the new dispatch count, or None when the four-step cap would be exceeded."""

    if isinstance(already_dispatched, bool) or not isinstance(already_dispatched, int):
        raise ContractValidationError("invalid_dispatch_count")
    if already_dispatched < 0 or already_dispatched + len(invocation_ids) > MAX_ROUTE_STEPS:
        return None
    return already_dispatched + len(invocation_ids)


def merge_step_statuses(
    left: Mapping[str, SyntheticStepStatus] | None,
    right: Mapping[str, SyntheticStepStatus] | None,
) -> dict[str, SyntheticStepStatus]:
    merged = dict(left or {})
    for key, value in dict(right or {}).items():
        if not isinstance(value, SyntheticStepStatus):
            raise ContractValidationError("invalid_step_status")
        current = merged.get(key)
        if current is None:
            merged[key] = value
            continue
        if current in _TERMINAL_STATUSES or current is value:
            raise ContractValidationError("duplicate_synthetic_result")
        if current is SyntheticStepStatus.WAITING and value in {
            SyntheticStepStatus.DISPATCHED,
            SyntheticStepStatus.BLOCKED,
            SyntheticStepStatus.OMITTED,
        }:
            merged[key] = value
            continue
        if current is SyntheticStepStatus.DISPATCHED and value in _TERMINAL_STATUSES:
            merged[key] = value
            continue
        if value is SyntheticStepStatus.DISPATCHED:
            raise ContractValidationError("duplicate_dispatch")
        raise ContractValidationError("duplicate_synthetic_result")
    return merged


def bind_result_reducer(known_invocation_ids: frozenset[str]):
    """Build the per-request result reducer. Unknown and duplicate ids fail closed."""

    def merge_synthetic_results(
        left: Mapping[str, SyntheticWorkerResult] | None,
        right: Mapping[str, SyntheticWorkerResult] | None,
    ) -> dict[str, SyntheticWorkerResult]:
        merged = dict(left or {})
        for key, value in dict(right or {}).items():
            if key not in known_invocation_ids:
                raise ContractValidationError("unknown_invocation_id")
            if not isinstance(value, SyntheticWorkerResult):
                raise ContractValidationError("unexpected_synthetic_result")
            if value.invocation_id != key:
                raise ContractValidationError("invocation_id_mismatch")
            if key in merged:
                raise ContractValidationError("duplicate_synthetic_result")
            merged[key] = value
        return merged

    return merge_synthetic_results


def _freeze_workers(workers: Mapping[Any, Any] | None) -> dict[MasterSpecialist, Any]:
    if not isinstance(workers, Mapping) or isinstance(workers, (str, bytes)):
        raise ContractValidationError("invalid_worker_registry")
    frozen: dict[MasterSpecialist, Any] = {}
    for key, value in workers.items():
        if not isinstance(key, MasterSpecialist) or not callable(value):
            raise ContractValidationError("invalid_worker_registry")
        frozen[key] = value
    return dict(frozen)


def _permanently_blocked(step: MasterRouteStepPlan) -> bool:
    return any(gate not in _DEPENDENCY_GATES for gate in step.gates)


def _parent_invocation_ids(step: MasterRouteStepPlan, by_step_id: Mapping[str, MasterRouteStepPlan]) -> tuple[str, ...]:
    return tuple(by_step_id[item].invocation.invocation_id for item in step.depends_on)


def _assignment(step: MasterRouteStepPlan, known: tuple[str, ...]) -> SyntheticAssignment:
    invocation = step.invocation
    if invocation.specialist_request_id is None:
        raise ContractValidationError("specialist_request_id_required")
    return SyntheticAssignment(
        master_request_id=invocation.master_request_id,
        correlation_id=invocation.correlation_id or "",
        invocation_id=invocation.invocation_id,
        specialist_request_id=invocation.specialist_request_id,
        step_id=step.step_id,
        specialist=invocation.specialist,
        purpose=invocation.purpose,
        requirement=invocation.requirement,
        capability=step.capability,
        historical_start_utc=step.historical_start_utc,
        historical_end_utc=step.historical_end_utc,
        untrusted_hints=step.untrusted_hints,
        known_invocation_ids=known,
    )


def _check_result(assignment: SyntheticAssignment, result: SyntheticWorkerResult) -> None:
    if result.invocation_id not in assignment.known_invocation_ids:
        raise ContractValidationError("unknown_invocation_id")
    if result.invocation_id != assignment.invocation_id:
        raise ContractValidationError("invocation_id_mismatch")
    if result.master_request_id != assignment.master_request_id:
        raise ContractValidationError("master_request_mismatch")
    if result.correlation_id != assignment.correlation_id:
        raise ContractValidationError("correlation_id_mismatch")
    if result.specialist_request_id != assignment.specialist_request_id:
        raise ContractValidationError("specialist_request_id_mismatch")
    if result.specialist is not assignment.specialist:
        raise ContractValidationError("specialist_mismatch")


def _limitations(decision: MasterRouteDecision | None, statuses: Mapping[str, SyntheticStepStatus]) -> tuple[str, ...]:
    notes = [SYNTHETIC_EVIDENCE_LIMITATION]
    if decision is not None:
        notes.append(decision.understanding_limitation)
    else:
        notes.append(REQUEST_UNDERSTANDING_LIMITATION)
    values = set(statuses.values())
    if SyntheticStepStatus.CANCELLED in values:
        notes.append(CANCELLATION_SUPERSTEP_LIMITATION)
    if SyntheticStepStatus.TIMED_OUT in values:
        notes.append(SCRIPTED_TIMEOUT_LIMITATION)
    if decision is not None and decision.plan is not None and decision.plan.deferred_synthesis:
        notes.append("Deferred Master synthesis remains unfulfilled.")
    return tuple(notes)


def _classify_terminal(
    plan: MasterRoutePlan,
    statuses: Mapping[str, SyntheticStepStatus],
    results: Mapping[str, SyntheticWorkerResult],
    budget_exhausted: bool,
) -> tuple[SyntheticTerminalStatus, tuple[str, ...]]:
    if budget_exhausted:
        return SyntheticTerminalStatus.BUDGET_EXHAUSTED, ("dispatch_budget_exhausted",)
    reasons: list[str] = []
    saw_cancel = False
    saw_required_failure = False
    saw_required_block = False
    saw_optional_issue = False
    for step in plan.steps:
        invocation_id = step.invocation.invocation_id
        status = statuses[invocation_id]
        required = step.invocation.requirement is MasterRequirement.REQUIRED
        if status is SyntheticStepStatus.SYNTHETIC_COMPLETED:
            continue
        if status is SyntheticStepStatus.CANCELLED:
            saw_cancel = True
            reasons.append(f"worker_cancelled:{step.step_id}")
            continue
        if status is SyntheticStepStatus.TIMED_OUT:
            reasons.append(f"worker_timed_out:{step.step_id}")
        elif status is SyntheticStepStatus.FAILED and invocation_id not in results:
            prefix = "required_worker_missing" if required else "optional_worker_missing"
            reasons.append(f"{prefix}:{step.step_id}")
        elif status is SyntheticStepStatus.FAILED:
            reasons.append(f"worker_failed:{step.step_id}")
        elif status is SyntheticStepStatus.OMITTED:
            reasons.append(f"optional_worker_missing:{step.step_id}")
        elif status is SyntheticStepStatus.BLOCKED:
            gates = tuple(gate.value for gate in step.gates if gate not in _DEPENDENCY_GATES)
            if gates:
                reasons.extend(f"blocked_gate:{step.step_id}:{gate}" for gate in gates)
            else:
                reasons.append(f"blocked_dependency:{step.step_id}")
        else:
            reasons.append(f"step_not_completed:{step.step_id}:{status.value}")
        if status in {SyntheticStepStatus.FAILED, SyntheticStepStatus.TIMED_OUT} and required:
            saw_required_failure = True
        elif required:
            saw_required_block = True
        else:
            saw_optional_issue = True
    if saw_cancel:
        return SyntheticTerminalStatus.CANCELLED, tuple(reasons)
    if saw_required_failure:
        return SyntheticTerminalStatus.FAILED, tuple(reasons)
    if saw_required_block:
        return SyntheticTerminalStatus.BLOCKED, tuple(reasons)
    if saw_optional_issue:
        return SyntheticTerminalStatus.PARTIAL, tuple(reasons)
    return SyntheticTerminalStatus.SYNTHETIC_COMPLETED, ("synthetic_workers_completed",)


def _ordered_results(
    plan: MasterRoutePlan,
    results: Mapping[str, SyntheticWorkerResult],
) -> tuple[SyntheticWorkerResult, ...]:
    ordered = []
    for step in plan.steps:
        item = results.get(step.invocation.invocation_id)
        if item is not None:
            ordered.append(item)
    return tuple(ordered)


def _ordered_statuses(
    plan: MasterRoutePlan,
    statuses: Mapping[str, SyntheticStepStatus],
) -> tuple[tuple[str, SyntheticStepStatus], ...]:
    return tuple(
        (step.invocation.invocation_id, statuses.get(step.invocation.invocation_id, SyntheticStepStatus.WAITING))
        for step in plan.steps
    )


def compile_synthetic_test_graph(
    workers: Mapping[MasterSpecialist, Any],
    *,
    nonce: str,
    decision: MasterRouteDecision,
):
    """Compile the fixed test graph for one authorized decision and one registry."""

    if decision.outcome is not MasterRouteOutcome.AUTHORIZED_PLAN or decision.plan is None:
        raise ContractValidationError("authorized_plan_required")
    registry = _freeze_workers(workers)
    plan = decision.plan
    known = tuple(step.invocation.invocation_id for step in plan.steps)
    known_set = frozenset(known)
    by_step_id = {step.step_id: step for step in plan.steps}
    merge_results = bind_result_reducer(known_set)

    # Functional TypedDict so the per-request reducer is bound now. A class
    # body would stringify these annotations and lose the enclosing registry.
    SyntheticGraphState = TypedDict(
        "SyntheticGraphState",
        {
            "ticket_nonce": Annotated[str, reject_planning_snapshot],
            "decision": Annotated[MasterRouteDecision, reject_planning_snapshot],
            "statuses": Annotated[dict[str, SyntheticStepStatus], merge_step_statuses],
            "results": Annotated[dict[str, SyntheticWorkerResult], merge_results],
            "ready": list[str],
            "dispatch_count": int,
            "budget_exhausted": bool,
            "terminal_status": str,
            "reason_codes": list[str],
        },
    )

    def validate_input(state):
        if state.get("ticket_nonce") != nonce or state.get("decision") is not decision:
            raise ContractValidationError("invalid_synthetic_ticket")
        if state.get("results") or state.get("statuses") or state.get("ready"):
            raise ContractValidationError("forged_synthetic_state")
        if state.get("dispatch_count") not in (0, None) or state.get("budget_exhausted") not in (False, None):
            raise ContractValidationError("forged_synthetic_state")
        if decision.execution_authority is not MasterRouteExecutionAuthority.DENIED:
            raise ContractValidationError("production_authority_not_issued")
        statuses = {}
        for step in plan.steps:
            invocation_id = step.invocation.invocation_id
            if _permanently_blocked(step):
                statuses[invocation_id] = SyntheticStepStatus.BLOCKED
            else:
                statuses[invocation_id] = SyntheticStepStatus.WAITING
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
        updates: dict[str, SyntheticStepStatus] = {}
        for step in plan.steps:
            invocation_id = step.invocation.invocation_id
            if statuses.get(invocation_id) is not SyntheticStepStatus.WAITING:
                continue
            if _permanently_blocked(step):
                updates[invocation_id] = SyntheticStepStatus.BLOCKED
                statuses[invocation_id] = SyntheticStepStatus.BLOCKED
                continue
            parent_ids = _parent_invocation_ids(step, by_step_id)
            parent_statuses = tuple(statuses.get(item) for item in parent_ids)
            if parent_ids and all(item in _TERMINAL_STATUSES for item in parent_statuses):
                if any(item is not SyntheticStepStatus.SYNTHETIC_COMPLETED for item in parent_statuses):
                    updates[invocation_id] = SyntheticStepStatus.BLOCKED
                    statuses[invocation_id] = SyntheticStepStatus.BLOCKED
        eligible: list[str] = []
        for step in plan.steps:
            invocation_id = step.invocation.invocation_id
            if statuses.get(invocation_id) is not SyntheticStepStatus.WAITING:
                continue
            parent_ids = _parent_invocation_ids(step, by_step_id)
            if all(statuses.get(item) is SyntheticStepStatus.SYNTHETIC_COMPLETED for item in parent_ids):
                eligible.append(invocation_id)
        reserved = reserve_dispatch_slots(state["dispatch_count"], tuple(eligible))
        if reserved is None:
            return {"statuses": updates, "ready": [], "budget_exhausted": True}
        for invocation_id in eligible:
            updates[invocation_id] = SyntheticStepStatus.DISPATCHED
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
            Send(
                "invoke_fake_specialist",
                {"assignment": _assignment(by_invocation[invocation_id], known)},
            )
            for invocation_id in ready
        ]

    by_invocation = {step.invocation.invocation_id: step for step in plan.steps}

    def invoke_fake_specialist(state):
        assignment = state["assignment"]  # type: ignore[index]
        if not isinstance(assignment, SyntheticAssignment):
            raise ContractValidationError("invalid_synthetic_assignment")
        worker = registry.get(assignment.specialist)
        if worker is None:
            if assignment.requirement is MasterRequirement.OPTIONAL:
                return {"statuses": {assignment.invocation_id: SyntheticStepStatus.OMITTED}}
            return {"statuses": {assignment.invocation_id: SyntheticStepStatus.FAILED}}
        try:
            returned = worker(assignment)
        except ContractValidationError:
            raise
        except Exception:
            returned = SyntheticWorkerResult(
                master_request_id=assignment.master_request_id,
                correlation_id=assignment.correlation_id,
                invocation_id=assignment.invocation_id,
                specialist_request_id=assignment.specialist_request_id,
                specialist=assignment.specialist,
                outcome=SyntheticOutcome.FAILED,
                note="synthetic worker exception",
            )
        if not isinstance(returned, SyntheticWorkerResult):
            raise ContractValidationError("unexpected_synthetic_result")
        _check_result(assignment, returned)
        return {
            "statuses": {assignment.invocation_id: _OUTCOME_STATUS[returned.outcome]},
            "results": {assignment.invocation_id: returned},
        }

    def finalize_workflow(state):
        status, reasons = _classify_terminal(
            plan,
            state["statuses"],
            state["results"],
            bool(state["budget_exhausted"]),
        )
        return {"terminal_status": status.value, "reason_codes": list(reasons), "ready": []}

    builder = StateGraph(SyntheticGraphState)
    builder.add_node("validate_input", validate_input)
    builder.add_node("select_ready", select_ready)
    builder.add_node("invoke_fake_specialist", invoke_fake_specialist)
    builder.add_node("finalize_workflow", finalize_workflow)
    builder.add_edge(START, "validate_input")
    builder.add_edge("validate_input", "select_ready")
    builder.add_conditional_edges("select_ready", route_ready, ["invoke_fake_specialist", "finalize_workflow"])
    builder.add_edge("invoke_fake_specialist", "select_ready")
    builder.add_edge("finalize_workflow", END)
    return builder.compile()


def _empty_result(
    *,
    terminal_status: SyntheticTerminalStatus,
    reasons: tuple[str, ...],
    decision: MasterRouteDecision | None,
    statuses: Mapping[str, SyntheticStepStatus] | None = None,
    results: Mapping[str, SyntheticWorkerResult] | None = None,
    dispatch_count: int = 0,
    dispatch_batches: tuple[tuple[str, ...], ...] = (),
    executed_nodes: tuple[str, ...] = (),
) -> SyntheticWorkflowResult:
    plan = None if decision is None else decision.plan
    status_map = dict(statuses or {})
    result_map = dict(results or {})
    return SyntheticWorkflowResult(
        terminal_status=terminal_status,
        reasons=reasons,
        decision=decision,
        step_statuses=_ordered_statuses(plan, status_map) if plan is not None else (),
        results=_ordered_results(plan, result_map) if plan is not None else (),
        limitations=_limitations(decision, status_map),
        dispatch_count=dispatch_count,
        dispatch_batches=dispatch_batches,
        executed_nodes=executed_nodes,
    )


def _recursion_limit(value: int | None) -> int:
    if value is None:
        return SYNTHETIC_RECURSION_LIMIT
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractValidationError("invalid_recursion_limit")
    return value


def run_synthetic_master_workflow(
    context: MasterPlanningContext,
    proposal: MasterRouteProposal,
    workers: Mapping[MasterSpecialist, Any],
    *,
    recursion_limit: int | None = None,
) -> SyntheticWorkflowResult:
    """Plan with MA2, then run injected synthetic workers on a new graph instance.

    A ``MasterRoutePlan``, including one restored by ``from_dict``, is not accepted.
    """

    if not isinstance(context, MasterPlanningContext):
        raise TypeError("planning context is required")
    if not isinstance(proposal, MasterRouteProposal):
        raise TypeError("proposal is required")
    registry = _freeze_workers(workers)
    limit = _recursion_limit(recursion_limit)
    decision = authorize_route(proposal, context)
    if decision.outcome is not MasterRouteOutcome.AUTHORIZED_PLAN or decision.plan is None:
        return _empty_result(
            terminal_status=SyntheticTerminalStatus.NOT_RUN,
            reasons=decision.reasons,
            decision=decision,
        )
    nonce = token_hex(16)
    graph = compile_synthetic_test_graph(registry, nonce=nonce, decision=decision)
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
    batches: list[tuple[str, ...]] = []
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
            node_name, update = next(iter(payload.items()))
            executed.append(node_name)
            if node_name == "select_ready":
                dispatched = tuple(
                    key
                    for key, value in dict(update.get("statuses") or {}).items()
                    if value is SyntheticStepStatus.DISPATCHED
                )
                if dispatched:
                    batches.append(dispatched)
    except GraphRecursionError:
        statuses = {} if last_values is None else dict(last_values.get("statuses") or {})
        results = {} if last_values is None else dict(last_values.get("results") or {})
        return _empty_result(
            terminal_status=SyntheticTerminalStatus.BUDGET_EXHAUSTED,
            reasons=("graph_recursion_exhausted",),
            decision=decision,
            statuses=statuses,
            results=results,
            dispatch_count=0 if last_values is None else int(last_values.get("dispatch_count") or 0),
            dispatch_batches=tuple(batches),
            executed_nodes=tuple(executed),
        )
    if last_values is None or not last_values.get("terminal_status"):
        raise ContractValidationError("synthetic_workflow_did_not_finish")
    return _empty_result(
        terminal_status=SyntheticTerminalStatus(last_values["terminal_status"]),
        reasons=tuple(last_values.get("reason_codes") or ()),
        decision=decision,
        statuses=dict(last_values.get("statuses") or {}),
        results=dict(last_values.get("results") or {}),
        dispatch_count=int(last_values.get("dispatch_count") or 0),
        dispatch_batches=tuple(batches),
        executed_nodes=tuple(executed),
    )
