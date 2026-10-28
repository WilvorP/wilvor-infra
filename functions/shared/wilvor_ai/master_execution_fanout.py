"""Deterministic scheduling for every invocation on one retained Master plan.

The schedule cites the original planning-session identity and the original
step and dependency edges. It tracks every planned invocation, including
repeated specialists, without asking the planning registry to select more
than one invocation and without minting another planning identity.

Planning-step eligibility is a scheduling observation. It is not execution
authorization. A claim reserves a step for a future trusted dispatcher and
does not grant that dispatcher permission to run it. The dispatcher still
needs a separate approved authorization decision before running a claimed
step. This module does not call specialists, models, AWS, or a dispatcher,
and it does not accept a caller-supplied policy flag.

The retained plan is read and never mutated. Invocation membership is object
identity on that plan. A copied, deserialized, or reconstructed invocation is
not a claim target. A recorded scheduling completion is not aviation evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Mapping

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
    _validate_bounded_text,
    _validate_utc,
)
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_planning_session import (
    MasterPlanningSessionRecord,
    MasterPlanningSessionRegistry,
    MasterPlanningSessionState,
)
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS,
    MAX_ROUTE_STEPS,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRoutePlan,
    MasterRouteStepPlan,
)


MASTER_EXECUTION_FANOUT_SCHEMA_VERSION = "wilvor.ai.master_execution_fanout.v1"
PLANNING_ELIGIBILITY_LIMITATION = (
    "Planning-step eligibility is not execution authorization. A future "
    "trusted dispatcher needs a separate approved authorization decision "
    "before running a claimed step."
)
SCHEDULING_COMPLETION_LIMITATION = (
    "Scheduling completion is not verified aviation evidence and does not "
    "grant execution authority."
)
_LIMITATIONS = (PLANNING_ELIGIBILITY_LIMITATION, SCHEDULING_COMPLETION_LIMITATION)
_TERMINAL_STEPS = frozenset(
    {
        "COMPLETED",
        "FAILED",
        "SKIPPED",
        "CANCELLED",
    }
)


class MasterSchedulingState(str, Enum):
    """Scheduling state of one planned invocation. None of these grants execution."""

    PENDING = "PENDING"
    ELIGIBLE = "ELIGIBLE"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class MasterExecutionScheduleStatus(str, Enum):
    """Lifecycle of one schedule. Terminal statuses accept no new claims."""

    OPEN = "OPEN"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    CLOSED = "CLOSED"


def _token(value: object, field_name: str) -> str:
    errors = _validate_bounded_text(
        value,
        field_name,
        max_length=PROVENANCE_TOKEN_MAX_LENGTH,
    )
    if errors:
        raise ContractValidationError(errors)
    if not isinstance(value, str) or value != value.strip() or any(character.isspace() for character in value):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _whole_second(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or len(value) != 20 or value[10] != "T" or not value.endswith("Z"):
        raise ContractValidationError(f"invalid_{field_name}")
    errors = _validate_utc(value, field_name)
    if errors:
        raise ContractValidationError(errors)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ContractValidationError(f"invalid_{field_name}") from exc


def _dependency_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(character.isspace() for character in value)
        or len(value) > PROVENANCE_TOKEN_MAX_LENGTH
    ):
        raise ContractValidationError("invalid_dependency")
    return value


def _reject_invalid_dependencies(steps: tuple[MasterRouteStepPlan, ...]) -> None:
    by_id = {step.step_id: step for step in steps}
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(step_id: str) -> None:
        if step_id in visiting:
            raise ContractValidationError("cyclic_dependency")
        if step_id in visited:
            return
        step = by_id.get(step_id)
        if step is None:
            raise ContractValidationError("invalid_dependency")
        visiting.add(step_id)
        if not isinstance(step.depends_on, tuple):
            raise ContractValidationError("invalid_dependency")
        seen: set[str] = set()
        for dependency in step.depends_on:
            dependency_id = _dependency_id(dependency)
            if dependency_id == step_id or dependency_id in seen or dependency_id not in by_id:
                raise ContractValidationError("invalid_dependency")
            seen.add(dependency_id)
            walk(dependency_id)
        visiting.remove(step_id)
        visited.add(step_id)

    for step in steps:
        walk(step.step_id)


def _assert_schedulable(plan: MasterRoutePlan) -> None:
    if plan.execution_authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
    if plan.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
        raise ContractValidationError("request_coverage_not_established")
    if plan.end_to_end_fulfilled:
        raise ContractValidationError("end_to_end_fulfillment_not_granted")
    steps = plan.steps
    if not isinstance(steps, tuple) or any(not isinstance(step, MasterRouteStepPlan) for step in steps):
        raise ContractValidationError("invalid_steps")
    if len(steps) > MAX_ROUTE_STEPS:
        raise ContractValidationError("excessive_plan_size")
    decision_steps = sum(step.invocation.specialist is MasterSpecialist.DECISION for step in steps)
    if decision_steps > MAX_DECISION_STEPS:
        raise ContractValidationError("decision_fanout_not_permitted")
    step_ids = [step.step_id for step in steps]
    invocation_ids = [step.invocation.invocation_id for step in steps]
    if len(step_ids) != len(set(step_ids)):
        raise ContractValidationError("duplicate_step_id")
    if len(invocation_ids) != len(set(invocation_ids)):
        raise ContractValidationError("duplicate_invocation_id")
    for step in steps:
        if not isinstance(step.invocation, MasterInvocationAuthorization):
            raise ContractValidationError("invalid_invocation_authorization")
        if step.invocation.master_request_id != plan.master_request_id:
            raise ContractValidationError("master_request_mismatch")
    _reject_invalid_dependencies(steps)


def _deny_authority(
    *,
    execution_authority: MasterRouteExecutionAuthority,
    production_authority: MasterProductionAuthority,
    source_authenticity: MasterSourceAuthenticity,
    execution_authorized: bool,
    evidence_verified: bool,
    authorization_decision_required: bool,
    limitations: tuple[str, ...],
    schema_version: str,
) -> None:
    if schema_version != MASTER_EXECUTION_FANOUT_SCHEMA_VERSION:
        raise ContractValidationError("invalid_schema_version")
    if execution_authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
    if production_authority is not MasterProductionAuthority.DENIED:
        raise ContractValidationError("production_authority_not_issued")
    if source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
        raise ContractValidationError("source_authenticity_not_established")
    if execution_authorized or evidence_verified:
        raise ContractValidationError("execution_authority_not_granted")
    if not authorization_decision_required:
        raise ContractValidationError("authorization_decision_required")
    if limitations != _LIMITATIONS:
        raise ContractValidationError("scheduling_evidence_not_verified")


@dataclass(frozen=True)
class MasterSchedulingObservation:
    """Read-only scheduling view of one original planned step.

    ``planning_ready_to_execute`` copies the plan's structural flag. It is not
    an execution grant. ``authorization_decision_required`` stays true because
    this observation is not an approved authorization decision.
    """

    session_id: str
    step_id: str
    invocation_id: str
    specialist: MasterSpecialist
    requirement: MasterRequirement
    state: MasterSchedulingState
    depends_on: tuple[str, ...]
    planning_gates: tuple[MasterRouteGate, ...]
    planning_ready_to_execute: bool
    reason: str | None = None
    limitations: tuple[str, ...] = _LIMITATIONS
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    execution_authorized: bool = False
    evidence_verified: bool = False
    authorization_decision_required: bool = True
    schema_version: str = MASTER_EXECUTION_FANOUT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _token(self.session_id, "session_id")
        _token(self.step_id, "step_id")
        _token(self.invocation_id, "invocation_id")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.requirement, MasterRequirement):
            raise ContractValidationError("invalid_requirement")
        if not isinstance(self.state, MasterSchedulingState):
            raise ContractValidationError("invalid_scheduling_state")
        if not isinstance(self.depends_on, tuple) or any(not isinstance(item, str) for item in self.depends_on):
            raise ContractValidationError("invalid_dependency")
        if not isinstance(self.planning_gates, tuple) or any(
            not isinstance(item, MasterRouteGate) for item in self.planning_gates
        ):
            raise ContractValidationError("invalid_gates")
        if not isinstance(self.planning_ready_to_execute, bool):
            raise ContractValidationError("invalid_ready_to_execute")
        if self.reason is not None:
            _token(self.reason, "reason")
        _deny_authority(
            execution_authority=self.execution_authority,
            production_authority=self.production_authority,
            source_authenticity=self.source_authenticity,
            execution_authorized=self.execution_authorized,
            evidence_verified=self.evidence_verified,
            authorization_decision_required=self.authorization_decision_required,
            limitations=self.limitations,
            schema_version=self.schema_version,
        )


@dataclass(frozen=True)
class MasterExecutionClaim:
    """Atomic reservation of one eligible step.

    The claim does not call a specialist and does not authorize execution.
    ``authorization_decision_required`` records that a future trusted
    dispatcher still needs a separate approved authorization decision before
    running the step.
    """

    session_id: str
    step_id: str
    invocation_id: str
    specialist: MasterSpecialist
    claimer_id: str
    planning_gates: tuple[MasterRouteGate, ...]
    planning_ready_to_execute: bool
    reason: str | None = None
    limitations: tuple[str, ...] = _LIMITATIONS
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    execution_authorized: bool = False
    evidence_verified: bool = False
    authorization_decision_required: bool = True
    schema_version: str = MASTER_EXECUTION_FANOUT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _token(self.session_id, "session_id")
        _token(self.step_id, "step_id")
        _token(self.invocation_id, "invocation_id")
        _token(self.claimer_id, "claimer_id")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.planning_gates, tuple) or any(
            not isinstance(item, MasterRouteGate) for item in self.planning_gates
        ):
            raise ContractValidationError("invalid_gates")
        if not isinstance(self.planning_ready_to_execute, bool):
            raise ContractValidationError("invalid_ready_to_execute")
        if self.reason is not None:
            _token(self.reason, "reason")
        _deny_authority(
            execution_authority=self.execution_authority,
            production_authority=self.production_authority,
            source_authenticity=self.source_authenticity,
            execution_authorized=self.execution_authorized,
            evidence_verified=self.evidence_verified,
            authorization_decision_required=self.authorization_decision_required,
            limitations=self.limitations,
            schema_version=self.schema_version,
        )


class _Slot:
    """Mutable scheduling record for one original step. Not a plan copy."""

    __slots__ = (
        "invocation",
        "step_id",
        "invocation_id",
        "specialist",
        "requirement",
        "depends_on",
        "planning_gates",
        "planning_ready_to_execute",
        "state",
        "reason",
        "claimer_id",
        "claim",
    )

    def __init__(self, step: MasterRouteStepPlan) -> None:
        self.invocation = step.invocation
        self.step_id = step.step_id
        self.invocation_id = step.invocation.invocation_id
        self.specialist = step.invocation.specialist
        self.requirement = step.invocation.requirement
        self.depends_on = tuple(_dependency_id(item) for item in step.depends_on)
        self.planning_gates = tuple(step.gates)
        self.planning_ready_to_execute = step.ready_to_execute
        self.state = (
            MasterSchedulingState.PENDING if self.depends_on else MasterSchedulingState.ELIGIBLE
        )
        self.reason: str | None = None
        self.claimer_id: str | None = None
        self.claim: MasterExecutionClaim | None = None


class MasterExecutionFanout:
    """Bounded, dependency-aware schedule for one retained Master plan.

    Helpers that mutate slots assume the caller holds ``_lock``. Public
    methods take that lock for the whole transition so a claim cannot be
    duplicated.
    """

    def __init__(
        self,
        registry: MasterPlanningSessionRegistry,
        record: MasterPlanningSessionRecord,
        *,
        deadline_utc: str,
    ) -> None:
        if not isinstance(registry, MasterPlanningSessionRegistry):
            raise TypeError("planning session registry is required")
        if not isinstance(record, MasterPlanningSessionRecord):
            raise TypeError("planning session is required")
        deadline = _whole_second(deadline_utc, "deadline_utc")
        plan = registry.plan_for(record)
        _assert_schedulable(plan)
        slots = tuple(_Slot(step) for step in plan.steps)
        self._registry = registry
        self._record = record
        self._plan = plan
        self._deadline = deadline
        self._deadline_utc = deadline_utc
        self._lock = Lock()
        self._slots = slots
        self._by_id = {slot.step_id: slot for slot in slots}
        self._status = (
            MasterExecutionScheduleStatus.OPEN if slots else MasterExecutionScheduleStatus.CLOSED
        )

    @property
    def session_id(self) -> str:
        return self._record.session_id

    @property
    def status(self) -> MasterExecutionScheduleStatus:
        with self._lock:
            return self._status

    @property
    def plan(self) -> MasterRoutePlan:
        """The original retained plan. Scheduling does not replace it."""

        return self._plan

    @property
    def deadline_utc(self) -> str:
        return self._deadline_utc

    @property
    def max_route_steps(self) -> int:
        return MAX_ROUTE_STEPS

    @property
    def max_decision_steps(self) -> int:
        return MAX_DECISION_STEPS

    @property
    def execution_authority(self) -> MasterRouteExecutionAuthority:
        return MasterRouteExecutionAuthority.DENIED

    @property
    def production_authority(self) -> MasterProductionAuthority:
        return MasterProductionAuthority.DENIED

    @property
    def source_authenticity(self) -> MasterSourceAuthenticity:
        return MasterSourceAuthenticity.NOT_ESTABLISHED

    @property
    def evidence_verified(self) -> bool:
        return False

    @property
    def execution_authorized(self) -> bool:
        return False

    @property
    def authorization_decision_required(self) -> bool:
        return True

    def limitations(self) -> tuple[str, ...]:
        return _LIMITATIONS

    def dependency_edges(self) -> tuple[tuple[str, str], ...]:
        """Original dependency edges in retained plan order."""

        with self._lock:
            return tuple(
                (slot.step_id, dependency)
                for slot in self._slots
                for dependency in slot.depends_on
            )

    def remaining_route_claims(self) -> int:
        with self._lock:
            return MAX_ROUTE_STEPS - self._route_claims_used()

    def remaining_decision_claims(self) -> int:
        with self._lock:
            return MAX_DECISION_STEPS - self._decision_claims_used()

    def observe_time(self, now_utc: str) -> MasterExecutionScheduleStatus:
        """Apply the deadline if ``now_utc`` has reached it and return the status."""

        parsed = _whole_second(now_utc, "now_utc")
        with self._lock:
            self._touch(parsed)
            return self._status

    def observations(self, now_utc: str) -> tuple[MasterSchedulingObservation, ...]:
        parsed = _whole_second(now_utc, "now_utc")
        with self._lock:
            self._touch(parsed)
            return tuple(self._observation(slot) for slot in self._slots)

    def eligible(self, now_utc: str) -> tuple[MasterSchedulingObservation, ...]:
        """Eligible steps in retained plan order.

        Eligibility follows completed predecessors. It does not clear planning
        gates and it does not authorize execution.
        """

        parsed = _whole_second(now_utc, "now_utc")
        with self._lock:
            self._touch(parsed)
            if self._status is not MasterExecutionScheduleStatus.OPEN:
                return ()
            return tuple(
                self._observation(slot)
                for slot in self._slots
                if slot.state is MasterSchedulingState.ELIGIBLE
            )

    def claim(
        self,
        invocation: MasterInvocationAuthorization,
        claimer_id: str,
        *,
        now_utc: str,
    ) -> MasterExecutionClaim:
        """Atomically reserve one eligible original invocation.

        The returned claim is not an authorization decision and does not run
        the specialist.
        """

        claimer = _token(claimer_id, "claimer_id")
        parsed = _whole_second(now_utc, "now_utc")
        with self._lock:
            self._touch(parsed)
            slot = self._slot_for(invocation)
            self._reject_inactive(slot)
            if slot.claim is not None or slot.state is MasterSchedulingState.IN_PROGRESS:
                raise ContractValidationError("duplicate_claim")
            if slot.state is MasterSchedulingState.SKIPPED and (slot.reason or "").startswith("dependency_"):
                raise ContractValidationError("dependency_failed")
            if slot.state is not MasterSchedulingState.ELIGIBLE:
                raise ContractValidationError("step_not_eligible")
            self._reserve_budget(slot)
            claim = MasterExecutionClaim(
                session_id=self._record.session_id,
                step_id=slot.step_id,
                invocation_id=slot.invocation_id,
                specialist=slot.specialist,
                claimer_id=claimer,
                planning_gates=slot.planning_gates,
                planning_ready_to_execute=slot.planning_ready_to_execute,
            )
            slot.claim = claim
            slot.claimer_id = claimer
            slot.state = MasterSchedulingState.IN_PROGRESS
            slot.reason = None
            return claim

    def complete(
        self,
        invocation: MasterInvocationAuthorization,
        claimer_id: str,
        *,
        now_utc: str,
    ) -> None:
        """Record that a claimed step finished scheduling.

        This is not specialist execution and it does not admit aviation evidence.
        Dependents become eligible only after this scheduling completion.
        """

        claimer = _token(claimer_id, "claimer_id")
        parsed = _whole_second(now_utc, "now_utc")
        with self._lock:
            self._touch(parsed)
            self._require_open_schedule()
            slot = self._slot_for(invocation)
            self._require_claimer(slot, claimer)
            slot.state = MasterSchedulingState.COMPLETED
            slot.reason = None
            self._refresh()
            self._close_if_settled()

    def fail(
        self,
        invocation: MasterInvocationAuthorization,
        claimer_id: str,
        *,
        now_utc: str,
        reason: str,
    ) -> None:
        """Record a claimed step as failed and skip newly blocked dependents.

        Dependents are not claimed. Their skip reason names the blocking step.
        """

        claimer = _token(claimer_id, "claimer_id")
        failure = _token(reason, "reason")
        parsed = _whole_second(now_utc, "now_utc")
        with self._lock:
            self._touch(parsed)
            self._require_open_schedule()
            slot = self._slot_for(invocation)
            self._require_claimer(slot, claimer)
            slot.state = MasterSchedulingState.FAILED
            slot.reason = failure
            self._propagate_blockers()
            self._refresh()
            self._close_if_settled()

    def cancel(self, *, now_utc: str) -> None:
        """Cancel every step that has not reached a scheduling terminal state.

        An in-progress reservation is revoked. No further step can be claimed.
        This does not call or interrupt a specialist, because this module
        never starts one.
        """

        parsed = _whole_second(now_utc, "now_utc")
        with self._lock:
            self._require_session()
            self._assert_plan_denied()
            if self._status is MasterExecutionScheduleStatus.OPEN and parsed >= self._deadline:
                self._expire_unfinished()
                raise ContractValidationError("deadline_expired")
            self._require_open_schedule()
            for slot in self._slots:
                if slot.state in (MasterSchedulingState.PENDING, MasterSchedulingState.ELIGIBLE):
                    slot.state = MasterSchedulingState.CANCELLED
                    slot.reason = "cancelled"
                elif slot.state is MasterSchedulingState.IN_PROGRESS:
                    slot.state = MasterSchedulingState.CANCELLED
                    slot.reason = "cancelled_during_scheduling"
            self._status = MasterExecutionScheduleStatus.CANCELLED

    def close(self, *, now_utc: str) -> None:
        """Close the schedule and refuse later claims.

        An outstanding reservation must be completed, failed, or cancelled
        before closure. Waiting steps are skipped and are not run.
        """

        parsed = _whole_second(now_utc, "now_utc")
        with self._lock:
            self._touch(parsed)
            self._require_open_schedule()
            if any(slot.state is MasterSchedulingState.IN_PROGRESS for slot in self._slots):
                raise ContractValidationError("claim_outstanding")
            for slot in self._slots:
                if slot.state in (MasterSchedulingState.PENDING, MasterSchedulingState.ELIGIBLE):
                    slot.state = MasterSchedulingState.SKIPPED
                    slot.reason = "schedule_closed"
            self._status = MasterExecutionScheduleStatus.CLOSED

    def _touch(self, now: datetime) -> None:
        self._require_session()
        self._assert_plan_denied()
        if self._status is MasterExecutionScheduleStatus.OPEN and now >= self._deadline:
            self._expire_unfinished()

    def _require_session(self) -> None:
        state = self._registry.state(self._record)
        if state is MasterPlanningSessionState.OPEN:
            retained = self._registry.plan_for(self._record)
            if retained is not self._plan:
                self._status = MasterExecutionScheduleStatus.CLOSED
                raise ContractValidationError("reconstructed_plan_rejected")
            return
        if self._status is MasterExecutionScheduleStatus.OPEN:
            self._status = MasterExecutionScheduleStatus.CLOSED
        if state is MasterPlanningSessionState.CLOSED:
            raise ContractValidationError("session_closed")
        if state is MasterPlanningSessionState.INVALIDATED:
            raise ContractValidationError("session_invalidated")
        raise ContractValidationError("schedule_closed")

    def _assert_plan_denied(self) -> None:
        plan = self._plan
        if plan.execution_authority is not MasterRouteExecutionAuthority.DENIED:
            self._status = MasterExecutionScheduleStatus.CLOSED
            raise ContractValidationError("execution_authority_not_granted")
        if plan.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
            self._status = MasterExecutionScheduleStatus.CLOSED
            raise ContractValidationError("request_coverage_not_established")
        if plan.end_to_end_fulfilled:
            self._status = MasterExecutionScheduleStatus.CLOSED
            raise ContractValidationError("end_to_end_fulfillment_not_granted")

    def _require_open_schedule(self) -> None:
        if self._status is MasterExecutionScheduleStatus.CANCELLED:
            raise ContractValidationError("schedule_cancelled")
        if self._status is MasterExecutionScheduleStatus.EXPIRED:
            raise ContractValidationError("deadline_expired")
        if self._status is not MasterExecutionScheduleStatus.OPEN:
            raise ContractValidationError("schedule_closed")

    def _reject_inactive(self, slot: _Slot) -> None:
        if self._status is MasterExecutionScheduleStatus.CANCELLED:
            raise ContractValidationError("schedule_cancelled")
        if self._status is MasterExecutionScheduleStatus.EXPIRED:
            raise ContractValidationError("deadline_expired")
        if self._status is not MasterExecutionScheduleStatus.OPEN:
            if slot.state is MasterSchedulingState.SKIPPED and (slot.reason or "").startswith("dependency_"):
                raise ContractValidationError("dependency_failed")
            raise ContractValidationError("schedule_closed")

    def _slot_for(self, invocation: object) -> _Slot:
        if isinstance(invocation, (str, bytes)):
            raise ContractValidationError("copied_invocation_identifier")
        if isinstance(invocation, Mapping):
            raise ContractValidationError("deserialized_invocation_rejected")
        if not isinstance(invocation, MasterInvocationAuthorization):
            raise ContractValidationError("forged_invocation_rejected")
        for slot in self._slots:
            if slot.invocation is invocation:
                return slot
        if any(slot.invocation_id == invocation.invocation_id for slot in self._slots):
            raise ContractValidationError("reconstructed_invocation_rejected")
        raise ContractValidationError("unknown_invocation")

    def _require_claimer(self, slot: _Slot, claimer_id: str) -> None:
        if slot.state is not MasterSchedulingState.IN_PROGRESS or slot.claim is None:
            raise ContractValidationError("step_not_in_progress")
        if slot.claimer_id != claimer_id:
            raise ContractValidationError("claimer_mismatch")

    def _route_claims_used(self) -> int:
        return sum(slot.claim is not None for slot in self._slots)

    def _decision_claims_used(self) -> int:
        return sum(
            slot.claim is not None and slot.specialist is MasterSpecialist.DECISION
            for slot in self._slots
        )

    def _reserve_budget(self, slot: _Slot) -> None:
        if self._route_claims_used() >= MAX_ROUTE_STEPS:
            raise ContractValidationError("excessive_plan_size")
        if slot.specialist is MasterSpecialist.DECISION and self._decision_claims_used() >= MAX_DECISION_STEPS:
            raise ContractValidationError("decision_fanout_not_permitted")

    def _refresh(self) -> None:
        if self._status is not MasterExecutionScheduleStatus.OPEN:
            return
        completed = {
            slot.step_id
            for slot in self._slots
            if slot.state is MasterSchedulingState.COMPLETED
        }
        for slot in self._slots:
            if slot.state is not MasterSchedulingState.PENDING:
                continue
            if all(dependency in completed for dependency in slot.depends_on):
                slot.state = MasterSchedulingState.ELIGIBLE
                slot.reason = None

    def _propagate_blockers(self) -> None:
        changed = True
        while changed:
            changed = False
            for slot in self._slots:
                if slot.state not in (MasterSchedulingState.PENDING, MasterSchedulingState.ELIGIBLE):
                    continue
                for dependency_id in slot.depends_on:
                    parent = self._by_id[dependency_id]
                    if parent.state is MasterSchedulingState.FAILED:
                        slot.state = MasterSchedulingState.SKIPPED
                        slot.reason = f"dependency_failed:{dependency_id}"
                        changed = True
                        break
                    if parent.state is MasterSchedulingState.SKIPPED:
                        slot.state = MasterSchedulingState.SKIPPED
                        slot.reason = f"dependency_skipped:{dependency_id}"
                        changed = True
                        break
                    if parent.state is MasterSchedulingState.CANCELLED:
                        slot.state = MasterSchedulingState.CANCELLED
                        slot.reason = f"dependency_cancelled:{dependency_id}"
                        changed = True
                        break

    def _expire_unfinished(self) -> None:
        for slot in self._slots:
            if slot.state in (MasterSchedulingState.PENDING, MasterSchedulingState.ELIGIBLE):
                slot.state = MasterSchedulingState.SKIPPED
                slot.reason = "deadline_expired"
            elif slot.state is MasterSchedulingState.IN_PROGRESS:
                slot.state = MasterSchedulingState.CANCELLED
                slot.reason = "deadline_expired"
        self._status = MasterExecutionScheduleStatus.EXPIRED

    def _close_if_settled(self) -> None:
        if self._status is not MasterExecutionScheduleStatus.OPEN:
            return
        if self._slots and all(slot.state.value in _TERMINAL_STEPS for slot in self._slots):
            self._status = MasterExecutionScheduleStatus.CLOSED

    def _observation(self, slot: _Slot) -> MasterSchedulingObservation:
        return MasterSchedulingObservation(
            session_id=self._record.session_id,
            step_id=slot.step_id,
            invocation_id=slot.invocation_id,
            specialist=slot.specialist,
            requirement=slot.requirement,
            state=slot.state,
            depends_on=slot.depends_on,
            planning_gates=slot.planning_gates,
            planning_ready_to_execute=slot.planning_ready_to_execute,
            reason=slot.reason,
        )
