"""MA3 scheduler-to-trusted-host adapter.

Fanout ``claim`` and ``complete`` record scheduling for one retained plan.
They do not execute a specialist and they do not admit aviation evidence.
This module keeps that split. A dispatch attempt is a separate execution
record, and it is reached only when all of the following are the original
objects for one step:

- the invocation on the retained plan
- a bounded context built from that plan and the planning session
- a host grant issued by the bound local test-host seam and then consumed
- a ledger reservation issued by the bound ledger and then consumed

Thread-local locks and scheduler claims are process-local. They are not
distributed exactly-once execution. Exactly-once execution requires Agent 5's
durable ledger, which is not imported on this branch. The in-process ledger
double only lets tests prove that dispatch refuses to run without a
reservation. It does not enable production dispatch.

The local test-host seam is an in-process object. An event body, an
Authorization header, a ``requestContext.authorizer.jwt`` dictionary, a
boolean, a string, a serialized dataclass, or an environment variable cannot
open it. This module has no Lambda entrypoint. Production principal and grant
issuance stay unavailable.

A descriptive eligibility allow is not a grant. Evaluated read ceilings are
not a permit. Fake worker results are not source-authenticated aviation
evidence and are not synthetic-orchestration results. Native specialist
runners and MA4 admission are not called.

Cross-module interfaces this branch does not import:

- Agent 4 host grant: ``wilvor_ai.master_host_execution_grant``
  ``issue`` then ``consume``, bound to the exact invocation, bounded context,
  and ledger reservation. Trust comes from the reviewed application host.
- Agent 5 durable ledger: ``wilvor_ai.master_durable_execution_ledger``
  ``reserve`` then ``consume`` for one idempotency key across hosts.
- Agent 6 policy permit: a within-allowlist evaluation must not be promoted
  to a dispatch permit. ``server_read_budget`` is copied as an evaluated
  ceiling only.
- Later specialist runners: Live Ops ``prepare_master_live_ops_native_bridge``
  and ``accept_native_live_ops_result``; Historical
  ``prepare_master_historical_native_invocation`` and
  ``correlate_master_historical_native_result``; the Decision runtime.
  None of those are called. The Live Ops bridge still records a missing
  trusted host execution grant.
- MA4: ``admit_master_evidence`` is not called. The handoff is a closed
  descriptor.
"""

from __future__ import annotations

import functools
import types
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Mapping

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    AgentCapability,
    ContractValidationError,
    _validate_bounded_text,
    _validate_utc,
)
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterPurpose,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_execution_fanout import (
    MasterExecutionFanout,
    MasterExecutionScheduleStatus,
    MasterSchedulingState,
)
from wilvor_ai.master_planning_session import (
    MasterPlanningSessionRecord,
    MasterPlanningSessionRegistry,
)
from wilvor_ai.master_read_resource_guard import server_read_budget
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS,
    MAX_HISTORICAL_WINDOW,
    MAX_ROUTE_STEPS,
    MasterPlanningContext,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRoutePlan,
    MasterRouteStepPlan,
)


MASTER_AUTHORIZED_SCHEDULER_ADAPTER_SCHEMA_VERSION = (
    "wilvor.ai.master_authorized_scheduler_adapter.v1"
)
SCHEDULER_CLAIMER_ID = "ma3-scheduler"
RESOURCE_SCOPE_NOT_AUTHORIZED = "NOT_AUTHORIZED"
PRODUCTION_DISPATCH_ENABLED = False

PROCESS_LOCAL_LOCK_LIMITATION = (
    "Thread-local locks and scheduler claims do not provide distributed "
    "exactly-once execution. Dispatch requires Agent 5's durable ledger, "
    "which is not linked on this branch."
)
SCHEDULING_IS_NOT_EXECUTION = (
    "Fanout claim and complete record scheduling only. They are not "
    "specialist execution and they are not aviation evidence."
)
TEST_WORKER_NOT_EVIDENCE = (
    "A fake worker on the local test-host seam is not an approved runtime "
    "adapter and its result is not aviation evidence."
)
PRODUCTION_DISPATCH_CLOSED = (
    "Production dispatch is disabled until a reviewed host independently "
    "issues a grant and Agent 5's durable ledger is bound outside this module."
)
EVALUATED_CEILING_IS_NOT_A_PERMIT = (
    "server_read_budget ceilings are an evaluation. They are not an Agent 6 "
    "dispatch permit."
)
MA4_HANDOFF_NOT_ADMISSION = (
    "The MA4 handoff does not call evidence admission and does not establish "
    "source authenticity."
)
SYNTHETIC_ORCHESTRATION_NOT_EVIDENCE = (
    "Synthetic orchestration is not called and its results are not aviation evidence."
)
HISTORICAL_WINDOW_IS_A_PLAN_LABEL = (
    "The historical window is copied from the retained plan. It is not a "
    "native tool argument and it is not source authentication."
)

ADAPTER_LIMITATIONS = (
    PROCESS_LOCAL_LOCK_LIMITATION,
    SCHEDULING_IS_NOT_EXECUTION,
    TEST_WORKER_NOT_EVIDENCE,
    PRODUCTION_DISPATCH_CLOSED,
    EVALUATED_CEILING_IS_NOT_A_PERMIT,
    MA4_HANDOFF_NOT_ADMISSION,
    SYNTHETIC_ORCHESTRATION_NOT_EVIDENCE,
    HISTORICAL_WINDOW_IS_A_PLAN_LABEL,
)

_TERMINAL_SCHEDULING = frozenset(
    {
        MasterSchedulingState.COMPLETED,
        MasterSchedulingState.FAILED,
        MasterSchedulingState.SKIPPED,
        MasterSchedulingState.CANCELLED,
    }
)


class AuthorityDistinction(str, Enum):
    """How a value may be used. SOURCE_AUTHENTICATED is never emitted."""

    DESCRIPTIVE = "DESCRIPTIVE"
    EVALUATED = "EVALUATED"
    HOST_ISSUED = "HOST_ISSUED"
    CONSUMED = "CONSUMED"
    SOURCE_AUTHENTICATED = "SOURCE_AUTHENTICATED"


class ScriptedWorkerReport(str, Enum):
    """Scripted test-worker result. Not an evidence verdict."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class ExecutionDisposition(str, Enum):
    """What the dispatch attempt did. Distinct from scheduling state."""

    NOT_DISPATCHED = "NOT_DISPATCHED"
    TEST_WORKER_SUCCEEDED = "TEST_WORKER_SUCCEEDED"
    TEST_WORKER_FAILED = "TEST_WORKER_FAILED"
    TEST_WORKER_TIMED_OUT = "TEST_WORKER_TIMED_OUT"
    CANCELLED = "CANCELLED"
    SKIPPED_PREDECESSOR = "SKIPPED_PREDECESSOR"
    SKIPPED_DEADLINE = "SKIPPED_DEADLINE"


@dataclass(frozen=True)
class IntegrationSeam:
    """Documented peer this branch does not import."""

    peer: str
    expected_module: str
    imported: bool
    required_operations: tuple[str, ...]
    status: str

    def __post_init__(self) -> None:
        if self.imported:
            raise ContractValidationError("unmerged_module_imported")
        if self.status != "CLOSED":
            raise ContractValidationError("integration_seam_not_closed")
        if not isinstance(self.required_operations, tuple) or not self.required_operations:
            raise ContractValidationError("invalid_integration_seam")


@dataclass(frozen=True)
class SpecialistRunnerSeam:
    """Later runner this adapter does not call."""

    runner: str
    module_name: str
    operations: tuple[str, ...]
    invoked: bool = False
    evidence_admitted: bool = False

    def __post_init__(self) -> None:
        if self.invoked or self.evidence_admitted:
            raise ContractValidationError("specialist_runner_not_authorized")
        if not isinstance(self.operations, tuple) or not self.operations:
            raise ContractValidationError("invalid_integration_seam")


INTEGRATION_SEAMS = (
    IntegrationSeam(
        peer="Agent 4 host grant",
        expected_module="wilvor_ai.master_host_execution_grant",
        imported=False,
        required_operations=(
            "issue a grant bound to the exact invocation, bounded context, and ledger reservation",
            "consume that grant once before dispatch",
        ),
        status="CLOSED",
    ),
    IntegrationSeam(
        peer="Agent 5 durable ledger",
        expected_module="wilvor_ai.master_durable_execution_ledger",
        imported=False,
        required_operations=(
            "reserve one idempotency key for the exact invocation and bounded context",
            "consume that reservation once at the dispatch boundary",
        ),
        status="CLOSED",
    ),
    IntegrationSeam(
        peer="Agent 6 execution policy permit",
        expected_module="wilvor_ai.master_execution_policy_permit",
        imported=False,
        required_operations=(
            "keep a within-allowlist evaluation distinct from a dispatch permit",
            "do not promote server_read_budget into execution authority",
        ),
        status="CLOSED",
    ),
)

SPECIALIST_RUNNER_SEAMS = (
    SpecialistRunnerSeam(
        runner="Live Ops native bridge",
        module_name="wilvor_ai.master_live_ops_native_bridge",
        operations=(
            "prepare_master_live_ops_native_bridge",
            "accept_native_live_ops_result",
        ),
    ),
    SpecialistRunnerSeam(
        runner="Historical native bridge",
        module_name="wilvor_ai.master_historical_native_bridge",
        operations=(
            "prepare_master_historical_native_invocation",
            "correlate_master_historical_native_result",
        ),
    ),
    SpecialistRunnerSeam(
        runner="Decision specialist runtime",
        module_name="wilvor_ai.decision_specialist",
        operations=("Decision specialist run",),
    ),
    SpecialistRunnerSeam(
        runner="MA4 evidence admission",
        module_name="wilvor_ai.master_evidence_admission",
        operations=("admit_master_evidence",),
    ),
)


class _SeamMembership:
    """Private issuer mark. A copied string is not this object."""


class _LedgerMembership:
    """Private ledger mark. A caller-created boolean is not this object."""


class _ScheduleBinding:
    """Ties one seam and one ledger to one adapter. Not a principal."""


_LOCAL_TEST_ACTIVATION = object()


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


def _count(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _reject_model_callable(value: object, code: str = "model_origin_dispatch_rejected") -> None:
    if isinstance(value, (types.FunctionType, types.MethodType, types.BuiltinFunctionType, functools.partial)):
        raise ContractValidationError(code)
    if isinstance(value, (str, bytes, bytearray, bool, int, float, type)):
        raise ContractValidationError(code)
    if isinstance(value, Mapping):
        raise ContractValidationError(code)
    if "dispatch" in getattr(value, "__dict__", {}):
        raise ContractValidationError(code)


def reject_untrusted_activation(value: object) -> None:
    """Refuse every production-shaped input.

    No event, header, JWT dictionary, environment variable, boolean, or
    serialized object is inspected. The local test seam is not opened here.
    """

    raise ContractValidationError("production_input_cannot_open_test_seam")


def _deny_closed_flags(
    *,
    source_authenticity: object,
    production_authority: object,
    execution_authority: object,
    evidence_verified: object,
    aviation_evidence: object,
    production_dispatch_enabled: object,
    durable_exactly_once: object,
    schema_version: object,
) -> None:
    if schema_version != MASTER_AUTHORIZED_SCHEDULER_ADAPTER_SCHEMA_VERSION:
        raise ContractValidationError("invalid_schema_version")
    if source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
        raise ContractValidationError("source_authenticity_not_established")
    if production_authority is not MasterProductionAuthority.DENIED:
        raise ContractValidationError("production_authority_not_issued")
    if execution_authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
    if evidence_verified or aviation_evidence or production_dispatch_enabled or durable_exactly_once:
        raise ContractValidationError("execution_authority_not_granted")
    if PRODUCTION_DISPATCH_ENABLED is not False:
        raise ContractValidationError("production_dispatch_disabled")


@dataclass(frozen=True)
class BoundedDispatchContext:
    """Descriptive envelope for one retained step.

    Identity fields are copied from the planning session and the retained
    plan. Read ceilings are an evaluation from ``server_read_budget``. Neither
    kind is a host grant. ``source_observation_utc`` stays empty so the
    application clock is not treated as a source clock.
    """

    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    region_scope: str | None
    historical_window: tuple[str, str] | None
    decision_target_scope: str | None
    accepted_at_utc: str
    deadline_utc: str
    evaluation_utc: str
    idempotency_key: str
    max_executions: int
    max_model_turns: int
    max_tool_calls: int
    correlation_id: str
    depends_on: tuple[str, ...]
    planning_gates: tuple[MasterRouteGate, ...]
    planning_ready_to_execute: bool
    principal_ref: None = None
    policy_revision: None = None
    resource_scope: str = RESOURCE_SCOPE_NOT_AUTHORIZED
    source_observation_utc: None = None
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    evidence_verified: bool = False
    aviation_evidence: bool = False
    production_dispatch_enabled: bool = False
    durable_exactly_once: bool = False
    schema_version: str = MASTER_AUTHORIZED_SCHEDULER_ADAPTER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _deny_closed_flags(
            source_authenticity=self.source_authenticity,
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            evidence_verified=self.evidence_verified,
            aviation_evidence=self.aviation_evidence,
            production_dispatch_enabled=self.production_dispatch_enabled,
            durable_exactly_once=self.durable_exactly_once,
            schema_version=self.schema_version,
        )
        _token(self.master_request_id, "master_request_id")
        _token(self.planning_session_id, "planning_session_id")
        _token(self.step_id, "step_id")
        _token(self.invocation_id, "invocation_id")
        _token(self.correlation_id, "correlation_id")
        _token(self.idempotency_key, "idempotency_key")
        if self.principal_ref is not None:
            raise ContractValidationError("principal_not_authenticated")
        if self.policy_revision is not None:
            raise ContractValidationError("policy_revision_not_authenticated")
        if self.resource_scope != RESOURCE_SCOPE_NOT_AUTHORIZED:
            raise ContractValidationError("resource_scope_not_authorized")
        if self.source_observation_utc is not None:
            raise ContractValidationError("source_clock_not_established")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        if not isinstance(self.capability, AgentCapability):
            raise ContractValidationError("invalid_capability")
        if self.region_scope is not None:
            if not isinstance(self.region_scope, str) or len(self.region_scope) > PROVENANCE_TOKEN_MAX_LENGTH:
                raise ContractValidationError("invalid_region_scope")
        if not isinstance(self.depends_on, tuple) or any(not isinstance(item, str) for item in self.depends_on):
            raise ContractValidationError("invalid_dependency")
        if not isinstance(self.planning_gates, tuple) or any(
            not isinstance(item, MasterRouteGate) for item in self.planning_gates
        ):
            raise ContractValidationError("invalid_gates")
        if not isinstance(self.planning_ready_to_execute, bool):
            raise ContractValidationError("invalid_ready_to_execute")
        expected_key = f"ma3:{self.planning_session_id}:{self.step_id}:{self.invocation_id}"
        if self.idempotency_key != expected_key:
            raise ContractValidationError("idempotency_key_mismatch")
        identities = (
            self.master_request_id,
            self.invocation_id,
            self.correlation_id,
            self.planning_session_id,
        )
        if len(set(identities)) != 4:
            raise ContractValidationError("request_identity_not_distinct")
        _whole_second(self.accepted_at_utc, "accepted_at_utc")
        _whole_second(self.deadline_utc, "deadline_utc")
        _whole_second(self.evaluation_utc, "evaluation_utc")
        executions, turns, calls = server_read_budget(self.specialist)
        if (
            self.max_executions != executions
            or self.max_model_turns != turns
            or self.max_tool_calls != calls
        ):
            raise ContractValidationError("server_budget_exceeded")
        self._validate_window()
        if self.decision_target_scope is not None:
            _token(self.decision_target_scope, "decision_target_scope")
        if self.specialist is not MasterSpecialist.DECISION and self.decision_target_scope is not None:
            raise ContractValidationError("decision_target_not_authorized")

    def _validate_window(self) -> None:
        window = self.historical_window
        historical = self.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
        if not historical:
            if window is not None:
                raise ContractValidationError("contradictory_temporal_bounds")
            return
        if (
            not isinstance(window, tuple)
            or len(window) != 2
            or any(not isinstance(item, str) for item in window)
        ):
            raise ContractValidationError("missing_historical_interval")
        start = _whole_second(window[0], "historical_start_utc")
        end = _whole_second(window[1], "historical_end_utc")
        if start >= end:
            raise ContractValidationError("contradictory_temporal_bounds")
        if end - start > MAX_HISTORICAL_WINDOW:
            raise ContractValidationError("historical_window_exceeds_seven_days")

    @property
    def identity_authority(self) -> AuthorityDistinction:
        return AuthorityDistinction.DESCRIPTIVE

    @property
    def budget_authority(self) -> AuthorityDistinction:
        return AuthorityDistinction.EVALUATED

    @property
    def planning_ready_authorizes_dispatch(self) -> bool:
        return False

    @property
    def resource_scope_authorized(self) -> bool:
        return False

    @property
    def region_scope_authorized(self) -> bool:
        return False

    @property
    def decision_target_authorized(self) -> bool:
        return False

    @property
    def source_clock_established(self) -> bool:
        return False


@dataclass(frozen=True)
class DescriptiveDispatchAssessment:
    """Eligibility description. ``descriptive_allow`` does not issue a grant."""

    step_id: str
    invocation_id: str
    specialist: MasterSpecialist
    scheduling_state: MasterSchedulingState
    descriptive_allow: bool
    authority: AuthorityDistinction = AuthorityDistinction.DESCRIPTIVE
    usable_production_grant: bool = False
    production_dispatch_enabled: bool = False
    evidence_verified: bool = False
    schema_version: str = MASTER_AUTHORIZED_SCHEDULER_ADAPTER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_AUTHORIZED_SCHEDULER_ADAPTER_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if self.authority is not AuthorityDistinction.DESCRIPTIVE:
            raise ContractValidationError("descriptive_authority_required")
        if self.usable_production_grant or self.production_dispatch_enabled or self.evidence_verified:
            raise ContractValidationError("descriptive_allow_is_not_a_grant")
        if not isinstance(self.descriptive_allow, bool):
            raise ContractValidationError("invalid_descriptive_allow")
        if not isinstance(self.scheduling_state, MasterSchedulingState):
            raise ContractValidationError("invalid_scheduling_state")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")


@dataclass(frozen=True)
class ScriptedWorkerObservation:
    """In-process worker note. It cannot verify evidence."""

    report: ScriptedWorkerReport
    tool_calls_used: int = 0
    model_turns_used: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.report, ScriptedWorkerReport):
            raise ContractValidationError("invalid_worker_report")
        _count(self.tool_calls_used, "tool_calls_used")
        _count(self.model_turns_used, "model_turns_used")


@dataclass(frozen=True)
class SchedulerInvocationOutcome:
    """One step after scheduling and, separately, after a dispatch attempt."""

    session_id: str
    master_request_id: str
    correlation_id: str
    step_id: str
    invocation_id: str
    specialist: MasterSpecialist
    scheduling_state: MasterSchedulingState
    execution_disposition: ExecutionDisposition
    scheduling_completed: bool
    execution_succeeded: bool
    historical_window: tuple[str, str] | None
    depends_on: tuple[str, ...]
    reason: str | None = None
    evidence_verified: bool = False
    aviation_evidence: bool = False
    production_dispatch: bool = False
    durable_exactly_once: bool = False
    scheduling_is_execution: bool = False
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    schema_version: str = MASTER_AUTHORIZED_SCHEDULER_ADAPTER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _deny_closed_flags(
            source_authenticity=self.source_authenticity,
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            evidence_verified=self.evidence_verified,
            aviation_evidence=self.aviation_evidence,
            production_dispatch_enabled=self.production_dispatch,
            durable_exactly_once=self.durable_exactly_once,
            schema_version=self.schema_version,
        )
        if self.scheduling_is_execution:
            raise ContractValidationError("scheduling_is_not_execution")
        if not isinstance(self.scheduling_completed, bool) or not isinstance(self.execution_succeeded, bool):
            raise ContractValidationError("invalid_dispatch_outcome")
        if not isinstance(self.scheduling_state, MasterSchedulingState):
            raise ContractValidationError("invalid_scheduling_state")
        if not isinstance(self.execution_disposition, ExecutionDisposition):
            raise ContractValidationError("invalid_execution_disposition")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if self.reason is not None:
            _token(self.reason, "reason")
        if self.execution_succeeded and self.execution_disposition is not ExecutionDisposition.TEST_WORKER_SUCCEEDED:
            raise ContractValidationError("execution_success_not_established")
        if self.scheduling_completed and self.scheduling_state is not MasterSchedulingState.COMPLETED:
            raise ContractValidationError("scheduling_completion_not_established")
        if self.execution_succeeded and not self.scheduling_completed:
            raise ContractValidationError("execution_success_not_established")


@dataclass(frozen=True)
class Ma4SchedulerHandoff:
    """Closed descriptor for a later MA4 pass. Not an admission input."""

    session_id: str
    master_request_id: str
    correlation_id: str
    outcomes: tuple[SchedulerInvocationOutcome, ...]
    dependency_edges: tuple[tuple[str, str], ...]
    admission_performed: bool = False
    evidence_verified: bool = False
    aviation_evidence: bool = False
    synthetic_orchestration_used: bool = False
    specialist_runners_invoked: bool = False
    production_dispatch_enabled: bool = False
    durable_exactly_once: bool = False
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    limitations: tuple[str, ...] = ADAPTER_LIMITATIONS
    schema_version: str = MASTER_AUTHORIZED_SCHEDULER_ADAPTER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _deny_closed_flags(
            source_authenticity=self.source_authenticity,
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            evidence_verified=self.evidence_verified,
            aviation_evidence=self.aviation_evidence,
            production_dispatch_enabled=self.production_dispatch_enabled,
            durable_exactly_once=self.durable_exactly_once,
            schema_version=self.schema_version,
        )
        if self.admission_performed or self.synthetic_orchestration_used or self.specialist_runners_invoked:
            raise ContractValidationError("ma4_admission_not_performed")
        if self.limitations != ADAPTER_LIMITATIONS:
            raise ContractValidationError("scheduling_evidence_not_verified")
        if not isinstance(self.outcomes, tuple) or any(
            not isinstance(item, SchedulerInvocationOutcome) for item in self.outcomes
        ):
            raise ContractValidationError("invalid_dispatch_outcome")
        if not isinstance(self.dependency_edges, tuple) or any(
            not isinstance(item, tuple) or len(item) != 2 for item in self.dependency_edges
        ):
            raise ContractValidationError("invalid_dependency")
        if len(self.outcomes) > MAX_ROUTE_STEPS:
            raise ContractValidationError("excessive_plan_size")
        decisions = sum(item.specialist is MasterSpecialist.DECISION for item in self.outcomes)
        if decisions > MAX_DECISION_STEPS:
            raise ContractValidationError("decision_fanout_not_permitted")


class _GrantRecord:
    __slots__ = (
        "invocation",
        "context",
        "reservation",
        "grant",
        "consumed_grant",
        "used",
    )

    def __init__(self, invocation: object, context: object, reservation: object, grant: object) -> None:
        self.invocation = invocation
        self.context = context
        self.reservation = reservation
        self.grant = grant
        self.consumed_grant: object | None = None
        self.used = False


class _LedgerRecord:
    __slots__ = ("invocation", "context", "reservation", "consumed", "consumed_reservation")

    def __init__(self, invocation: object, context: object, reservation: object) -> None:
        self.invocation = invocation
        self.context = context
        self.reservation = reservation
        self.consumed = False
        self.consumed_reservation: object | None = None


class HostDispatchGrant:
    """Host-issued test grant. Issuance is not consumption and not a production permit."""

    __slots__ = ("_membership",)

    def __init__(self, membership: object) -> None:
        if type(membership) is not _SeamMembership:
            raise ContractValidationError("forged_grant_rejected")
        self._membership = membership

    @property
    def authority(self) -> AuthorityDistinction:
        return AuthorityDistinction.HOST_ISSUED

    @property
    def production_dispatch_enabled(self) -> bool:
        return False


class ConsumedHostDispatchGrant:
    """Single-use consumption record. Possession of this type is not authority."""

    __slots__ = ("_membership",)

    def __init__(self, membership: object) -> None:
        if type(membership) is not _SeamMembership:
            raise ContractValidationError("forged_grant_rejected")
        self._membership = membership

    @property
    def authority(self) -> AuthorityDistinction:
        return AuthorityDistinction.CONSUMED

    @property
    def production_dispatch_enabled(self) -> bool:
        return False


class LedgerReservation:
    """Reservation object. A durable boolean on another object is not a reservation."""

    __slots__ = ("_membership",)

    def __init__(self, membership: object) -> None:
        if type(membership) is not _LedgerMembership:
            raise ContractValidationError("forged_reservation_rejected")
        self._membership = membership

    @property
    def durable_exactly_once(self) -> bool:
        return False


class ConsumedLedgerReservation:
    """Consumed reservation. It does not become durable by being consumed."""

    __slots__ = ("_membership", "_reservation")

    def __init__(self, membership: object, reservation: LedgerReservation) -> None:
        if type(membership) is not _LedgerMembership or type(reservation) is not LedgerReservation:
            raise ContractValidationError("forged_reservation_rejected")
        self._membership = membership
        self._reservation = reservation

    @property
    def durable_exactly_once(self) -> bool:
        return False


class LocalTestHostAuthoritySeam:
    """In-process test issuer.

    Construct it through ``open_local_test_host_seam``. Production input has
    no parameter that selects this class.
    """

    def __init__(self, activation: object) -> None:
        if activation is not _LOCAL_TEST_ACTIVATION:
            raise ContractValidationError("production_input_cannot_open_test_seam")
        self._membership = _SeamMembership()
        self._binding: _ScheduleBinding | None = None
        self._lock = Lock()
        self._issued: dict[int, _GrantRecord] = {}
        self._consumed: dict[int, _GrantRecord] = {}
        self._by_reservation: dict[int, _GrantRecord] = {}

    @property
    def production_issuer_available(self) -> bool:
        return False

    @property
    def issued_count(self) -> int:
        with self._lock:
            return len(self._issued)

    def bind(self, binding: object) -> None:
        if type(binding) is not _ScheduleBinding:
            raise ContractValidationError("forged_schedule_binding")
        with self._lock:
            if self._binding is None:
                self._binding = binding
                return
            if self._binding is binding:
                return
            raise ContractValidationError("authority_already_bound")

    def issue(
        self,
        binding: object,
        invocation: object,
        context: object,
        reservation: object,
    ) -> HostDispatchGrant:
        if type(binding) is not _ScheduleBinding or binding is not self._binding:
            raise ContractValidationError("foreign_schedule_rejected")
        if type(context) is not BoundedDispatchContext:
            raise ContractValidationError("forged_context_rejected")
        if type(reservation) is not LedgerReservation:
            raise ContractValidationError("forged_reservation_rejected")
        with self._lock:
            existing = self._by_reservation.get(id(reservation))
            if existing is not None:
                if existing.invocation is not invocation or existing.context is not context:
                    raise ContractValidationError("grant_invocation_mismatch")
                if existing.consumed_grant is not None:
                    raise ContractValidationError("duplicate_dispatch")
                return existing.grant  # type: ignore[return-value]
            grant = HostDispatchGrant(self._membership)
            record = _GrantRecord(invocation, context, reservation, grant)
            self._issued[id(grant)] = record
            self._by_reservation[id(reservation)] = record
            return grant

    def consume(self, grant: object) -> ConsumedHostDispatchGrant:
        if type(grant) is not HostDispatchGrant:
            raise ContractValidationError("forged_grant_rejected")
        with self._lock:
            record = self._issued.get(id(grant))
            if record is None:
                raise ContractValidationError("forged_grant_rejected")
            if record.consumed_grant is not None:
                raise ContractValidationError("grant_already_consumed")
            consumed = ConsumedHostDispatchGrant(self._membership)
            record.consumed_grant = consumed
            self._consumed[id(consumed)] = record
            return consumed

    def assert_consumed(
        self,
        consumed_grant: object,
        invocation: object,
        context: object,
    ) -> _GrantRecord:
        if type(consumed_grant) is HostDispatchGrant:
            raise ContractValidationError("grant_not_consumed")
        if type(consumed_grant) is not ConsumedHostDispatchGrant:
            raise ContractValidationError("forged_grant_rejected")
        with self._lock:
            record = self._consumed.get(id(consumed_grant))
            if record is None or record.consumed_grant is not consumed_grant:
                raise ContractValidationError("forged_grant_rejected")
            if record.invocation is not invocation:
                raise ContractValidationError("grant_invocation_mismatch")
            if record.context is not context:
                raise ContractValidationError("bounded_context_mismatch")
            return record

    def mark_used(self, consumed_grant: object) -> None:
        if type(consumed_grant) is not ConsumedHostDispatchGrant:
            raise ContractValidationError("forged_grant_rejected")
        with self._lock:
            record = self._consumed.get(id(consumed_grant))
            if record is None:
                raise ContractValidationError("forged_grant_rejected")
            if record.used:
                raise ContractValidationError("duplicate_dispatch")
            record.used = True

    def reservation_for(self, consumed_grant: object) -> LedgerReservation:
        if type(consumed_grant) is not ConsumedHostDispatchGrant:
            raise ContractValidationError("forged_grant_rejected")
        with self._lock:
            record = self._consumed.get(id(consumed_grant))
            if record is None or type(record.reservation) is not LedgerReservation:
                raise ContractValidationError("forged_grant_rejected")
            return record.reservation

    def assert_staged(
        self,
        grant: object,
        invocation: object,
        context: object,
        reservation: object,
    ) -> None:
        if type(grant) is not HostDispatchGrant:
            raise ContractValidationError("forged_grant_rejected")
        with self._lock:
            record = self._issued.get(id(grant))
            if record is None:
                raise ContractValidationError("forged_grant_rejected")
            if record.consumed_grant is not None:
                raise ContractValidationError("grant_already_consumed")
            if record.invocation is not invocation:
                raise ContractValidationError("grant_invocation_mismatch")
            if record.context is not context:
                raise ContractValidationError("bounded_context_mismatch")
            if record.reservation is not reservation:
                raise ContractValidationError("reservation_mismatch")


def open_local_test_host_seam() -> LocalTestHostAuthoritySeam:
    """Open the in-process test issuer.

    This function reads no environment variable and no event. Calling it is
    the test wiring. Production input cannot reach it through this module.
    """

    return LocalTestHostAuthoritySeam(_LOCAL_TEST_ACTIVATION)


class InProcessLedgerReservationDouble:
    """Reserve/consume stand-in for Agent 5.

    This object is not durable and it is not exactly-once across hosts.
    ``durable_exactly_once`` stays false. A caller cannot flip it.
    """

    def __init__(self) -> None:
        self._membership = _LedgerMembership()
        self._binding: _ScheduleBinding | None = None
        self._lock = Lock()
        self._by_key: dict[str, _LedgerRecord] = {}
        self._by_reservation: dict[int, _LedgerRecord] = {}
        self._consumed: dict[int, _LedgerRecord] = {}

    @property
    def durable_exactly_once(self) -> bool:
        return False

    @property
    def production_ledger(self) -> bool:
        return False

    def bind(self, binding: object) -> None:
        if type(binding) is not _ScheduleBinding:
            raise ContractValidationError("forged_schedule_binding")
        with self._lock:
            if self._binding is None:
                self._binding = binding
                return
            if self._binding is binding:
                return
            raise ContractValidationError("authority_already_bound")

    def consumed_invocation(self, invocation: object) -> bool:
        with self._lock:
            return any(record.invocation is invocation and record.consumed for record in self._by_key.values())

    def reserve(self, binding: object, invocation: object, context: object) -> LedgerReservation:
        if type(binding) is not _ScheduleBinding or binding is not self._binding:
            raise ContractValidationError("foreign_schedule_rejected")
        if type(context) is not BoundedDispatchContext:
            raise ContractValidationError("forged_context_rejected")
        if not isinstance(invocation, MasterInvocationAuthorization):
            raise ContractValidationError("forged_invocation_rejected")
        if context.invocation_id != invocation.invocation_id:
            raise ContractValidationError("request_identity_mismatch")
        with self._lock:
            record = self._by_key.get(context.idempotency_key)
            if record is None:
                reservation = LedgerReservation(self._membership)
                record = _LedgerRecord(invocation, context, reservation)
                self._by_key[context.idempotency_key] = record
                self._by_reservation[id(reservation)] = record
                return reservation
            if record.invocation is not invocation:
                raise ContractValidationError("substituted_invocation_rejected")
            if record.consumed:
                raise ContractValidationError("duplicate_dispatch")
            if record.context is not context:
                raise ContractValidationError("bounded_context_mismatch")
            return record.reservation  # type: ignore[return-value]

    def consume(self, reservation: object) -> ConsumedLedgerReservation:
        if type(reservation) is not LedgerReservation:
            raise ContractValidationError("forged_reservation_rejected")
        with self._lock:
            record = self._by_reservation.get(id(reservation))
            if record is None or record.reservation is not reservation:
                raise ContractValidationError("forged_reservation_rejected")
            if record.consumed:
                raise ContractValidationError("reservation_already_consumed")
            consumed = ConsumedLedgerReservation(self._membership, reservation)
            record.consumed = True
            record.consumed_reservation = consumed
            self._consumed[id(consumed)] = record
            return consumed

    def assert_consumed(
        self,
        consumed_reservation: object,
        invocation: object,
        context: object,
    ) -> _LedgerRecord:
        if type(consumed_reservation) is LedgerReservation:
            raise ContractValidationError("reservation_not_consumed")
        if type(consumed_reservation) is not ConsumedLedgerReservation:
            raise ContractValidationError("forged_reservation_rejected")
        with self._lock:
            record = self._consumed.get(id(consumed_reservation))
            if record is None or record.consumed_reservation is not consumed_reservation:
                raise ContractValidationError("forged_reservation_rejected")
            if record.invocation is not invocation:
                raise ContractValidationError("reservation_invocation_mismatch")
            if record.context is not context:
                raise ContractValidationError("bounded_context_mismatch")
            return record

    def original_reservation(self, consumed_reservation: object) -> LedgerReservation:
        if type(consumed_reservation) is not ConsumedLedgerReservation:
            raise ContractValidationError("forged_reservation_rejected")
        with self._lock:
            record = self._consumed.get(id(consumed_reservation))
            if record is None or record.consumed_reservation is not consumed_reservation:
                raise ContractValidationError("forged_reservation_rejected")
            if type(record.reservation) is not LedgerReservation:
                raise ContractValidationError("forged_reservation_rejected")
            return record.reservation


class ScriptedTestWorker:
    """Registered fake worker. One instance may serve repeated Historical steps.

    Reports are keyed by invocation id so repeated specialists stay separate.
    The worker has no network client and no model callable.
    """

    def __init__(
        self,
        reports: Mapping[str, ScriptedWorkerReport] | None = None,
        *,
        default: ScriptedWorkerReport = ScriptedWorkerReport.SUCCEEDED,
        tool_calls_used: int = 0,
        model_turns_used: int = 0,
    ) -> None:
        if not isinstance(default, ScriptedWorkerReport):
            raise ContractValidationError("invalid_worker_report")
        if reports is None:
            stored: dict[str, ScriptedWorkerReport] = {}
        elif isinstance(reports, (str, bytes, bytearray)) or not isinstance(reports, Mapping):
            raise ContractValidationError("invalid_worker_report")
        else:
            stored = {}
            for key, value in reports.items():
                if not isinstance(key, str) or not isinstance(value, ScriptedWorkerReport):
                    raise ContractValidationError("invalid_worker_report")
                stored[key] = value
        self._reports = stored
        self._default = default
        self._tool_calls_used = _count(tool_calls_used, "tool_calls_used")
        self._model_turns_used = _count(model_turns_used, "model_turns_used")
        self.invocation_ids: list[str] = []

    def observe(
        self,
        context: object,
        invocation: object,
    ) -> ScriptedWorkerObservation:
        if type(context) is not BoundedDispatchContext or not isinstance(invocation, MasterInvocationAuthorization):
            raise ContractValidationError("worker_observation_rejected")
        self.invocation_ids.append(invocation.invocation_id)
        report = self._reports.get(invocation.invocation_id, self._default)
        return ScriptedWorkerObservation(
            report=report,
            tool_calls_used=self._tool_calls_used,
            model_turns_used=self._model_turns_used,
        )


class SchedulerDispatchPort:
    """Injectable port. The dispatch method accepts no model callable.

    Subclasses run only after the adapter has checked the consumed grant,
    consumed reservation, bounded context, and exact invocation.
    """

    def dispatch(
        self,
        *,
        consumed_grant: object,
        consumed_reservation: object,
        context: object,
        invocation: object,
    ) -> ScriptedWorkerObservation:
        raise ContractValidationError("dispatch_not_implemented")


class LocalTestWorkerDispatchPort(SchedulerDispatchPort):
    """Looks up a fake worker registered for the invocation's specialist."""

    def __init__(
        self,
        seam: LocalTestHostAuthoritySeam,
        ledger: InProcessLedgerReservationDouble,
    ) -> None:
        if type(seam) is not LocalTestHostAuthoritySeam:
            raise ContractValidationError("host_grant_authority_missing")
        if type(ledger) is not InProcessLedgerReservationDouble:
            raise ContractValidationError("ledger_authority_missing")
        self._seam = seam
        self._ledger = ledger
        self._workers: dict[MasterSpecialist, ScriptedTestWorker] = {}
        self.dispatched_invocation_ids: list[str] = []

    @property
    def seam(self) -> LocalTestHostAuthoritySeam:
        return self._seam

    @property
    def ledger(self) -> InProcessLedgerReservationDouble:
        return self._ledger

    def worker_registered(self, specialist: MasterSpecialist) -> bool:
        return specialist in self._workers

    def register(self, specialist: MasterSpecialist, worker: object) -> None:
        _reject_model_callable(worker)
        if type(worker) is not ScriptedTestWorker:
            raise ContractValidationError("model_origin_dispatch_rejected")
        if not isinstance(specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if specialist in self._workers:
            raise ContractValidationError("worker_already_registered")
        self._workers[specialist] = worker

    def dispatch(
        self,
        *,
        consumed_grant: object,
        consumed_reservation: object,
        context: object,
        invocation: object,
    ) -> ScriptedWorkerObservation:
        if not isinstance(invocation, MasterInvocationAuthorization):
            raise ContractValidationError("forged_invocation_rejected")
        if type(context) is not BoundedDispatchContext:
            raise ContractValidationError("forged_context_rejected")
        self._seam.assert_consumed(consumed_grant, invocation, context)
        self._ledger.assert_consumed(consumed_reservation, invocation, context)
        if self._seam.reservation_for(consumed_grant) is not self._ledger.original_reservation(consumed_reservation):
            raise ContractValidationError("reservation_mismatch")
        worker = self._workers.get(invocation.specialist)
        if worker is None:
            raise ContractValidationError("test_worker_not_registered")
        self.dispatched_invocation_ids.append(invocation.invocation_id)
        return worker.observe(context, invocation)


class MasterAuthorizedSchedulerAdapter:
    """Dispatch eligible retained invocations through one test-host seam.

    Missing seam or ledger leaves production dispatch disabled. A present
    in-process ledger still does not make execution exactly-once or turn a
    fake worker result into evidence.
    """

    def __init__(
        self,
        registry: MasterPlanningSessionRegistry,
        record: MasterPlanningSessionRecord,
        *,
        deadline_utc: str,
        host_seam: LocalTestHostAuthoritySeam | None = None,
        ledger: InProcessLedgerReservationDouble | None = None,
        dispatch_port: SchedulerDispatchPort | None = None,
        fanout: MasterExecutionFanout | None = None,
    ) -> None:
        if host_seam is not None and type(host_seam) is not LocalTestHostAuthoritySeam:
            raise ContractValidationError("host_grant_authority_missing")
        if ledger is not None and type(ledger) is not InProcessLedgerReservationDouble:
            raise ContractValidationError("ledger_authority_missing")
        if dispatch_port is not None:
            _reject_model_callable(dispatch_port)
            if not isinstance(dispatch_port, SchedulerDispatchPort) or type(dispatch_port) is SchedulerDispatchPort:
                raise ContractValidationError("dispatch_port_not_registered")
        if isinstance(dispatch_port, LocalTestWorkerDispatchPort):
            if host_seam is None or ledger is None:
                raise ContractValidationError("dispatch_port_binding_mismatch")
            if dispatch_port.seam is not host_seam or dispatch_port.ledger is not ledger:
                raise ContractValidationError("dispatch_port_binding_mismatch")
        if fanout is None:
            fanout = MasterExecutionFanout(registry, record, deadline_utc=deadline_utc)
        elif not isinstance(fanout, MasterExecutionFanout):
            raise ContractValidationError("invalid_schedule")
        retained = registry.plan_for(record)
        if fanout.plan is not retained:
            raise ContractValidationError("reconstructed_plan_rejected")
        if fanout.session_id != record.session_id:
            raise ContractValidationError("session_mismatch")
        if fanout.deadline_utc != deadline_utc:
            raise ContractValidationError("deadline_mismatch")
        self._registry = registry
        self._record = record
        self._planning = registry.context_for(record)
        self._fanout = fanout
        self._deadline_utc = deadline_utc
        self._seam = host_seam
        self._ledger = ledger
        self._port = dispatch_port
        self._binding = _ScheduleBinding()
        self._lock = Lock()
        self._contexts: dict[tuple[int, str], BoundedDispatchContext] = {}
        self._outcomes: dict[str, SchedulerInvocationOutcome] = {}
        if host_seam is not None:
            host_seam.bind(self._binding)
        if ledger is not None:
            ledger.bind(self._binding)
        self._require_retained()

    @property
    def fanout(self) -> MasterExecutionFanout:
        return self._fanout

    @property
    def session_id(self) -> str:
        return self._record.session_id

    @property
    def production_dispatch_enabled(self) -> bool:
        return False

    @property
    def durable_exactly_once(self) -> bool:
        return False

    @property
    def max_route_steps(self) -> int:
        return MAX_ROUTE_STEPS

    @property
    def max_decision_steps(self) -> int:
        return MAX_DECISION_STEPS

    def limitations(self) -> tuple[str, ...]:
        return ADAPTER_LIMITATIONS

    def dependency_edges(self) -> tuple[tuple[str, str], ...]:
        with self._lock:
            self._require_retained()
            return self._fanout.dependency_edges()

    def describe(self, now_utc: str) -> tuple[DescriptiveDispatchAssessment, ...]:
        """Describe eligibility. This issues no grant and calls no worker."""

        _whole_second(now_utc, "now_utc")
        with self._lock:
            status = self._fanout.observe_time(now_utc)
            assessments = []
            for observation in self._fanout.observations(now_utc):
                allow = (
                    status is MasterExecutionScheduleStatus.OPEN
                    and observation.state is MasterSchedulingState.ELIGIBLE
                )
                assessments.append(
                    DescriptiveDispatchAssessment(
                        step_id=observation.step_id,
                        invocation_id=observation.invocation_id,
                        specialist=observation.specialist,
                        scheduling_state=observation.state,
                        descriptive_allow=allow,
                    )
                )
            return tuple(assessments)

    def bounded_context(
        self,
        invocation: MasterInvocationAuthorization,
        *,
        now_utc: str,
    ) -> BoundedDispatchContext:
        """Copy one retained step into the shared envelope. This issues no grant."""

        _whole_second(now_utc, "now_utc")
        with self._lock:
            step = self._require_exact(invocation)
            return self._context_for(step, now_utc)

    def stage_dispatch(
        self,
        invocation: MasterInvocationAuthorization,
        *,
        now_utc: str,
    ) -> tuple[BoundedDispatchContext, LedgerReservation, HostDispatchGrant]:
        """Issue a grant and reservation without consuming them or calling a worker."""

        _whole_second(now_utc, "now_utc")
        with self._lock:
            return self._stage_locked(invocation, now_utc)

    def consume_staged(
        self,
        invocation: MasterInvocationAuthorization,
        context: BoundedDispatchContext,
        reservation: LedgerReservation,
        grant: HostDispatchGrant,
    ) -> tuple[ConsumedHostDispatchGrant, ConsumedLedgerReservation]:
        """Consume one staged grant and its reservation. This does not dispatch."""

        with self._lock:
            self._require_test_authority()
            self._require_exact(invocation)
            if self._seam is None or self._ledger is None:
                raise ContractValidationError("production_dispatch_disabled")
            self._seam.assert_staged(grant, invocation, context, reservation)
            consumed_grant = self._seam.consume(grant)
            consumed_reservation = self._ledger.consume(reservation)
            return consumed_grant, consumed_reservation

    def submit_dispatch(
        self,
        invocation: MasterInvocationAuthorization,
        *,
        consumed_grant: object,
        consumed_reservation: object,
        context: object,
        now_utc: str,
    ) -> SchedulerInvocationOutcome:
        """Dispatch only a consumed grant, consumed reservation, context, and exact invocation."""

        _whole_second(now_utc, "now_utc")
        with self._lock:
            return self._submit_locked(
                invocation,
                consumed_grant=consumed_grant,
                consumed_reservation=consumed_reservation,
                context=context,
                now_utc=now_utc,
            )

    def dispatch_invocation(
        self,
        invocation: MasterInvocationAuthorization,
        *,
        now_utc: str,
    ) -> SchedulerInvocationOutcome:
        """Stage, consume, and dispatch one exact eligible invocation."""

        _whole_second(now_utc, "now_utc")
        with self._lock:
            context, reservation, grant = self._stage_locked(invocation, now_utc)
            if self._seam is None or self._ledger is None:
                raise ContractValidationError("production_dispatch_disabled")
            consumed_grant = self._seam.consume(grant)
            consumed_reservation = self._ledger.consume(reservation)
            return self._submit_locked(
                invocation,
                consumed_grant=consumed_grant,
                consumed_reservation=consumed_reservation,
                context=context,
                now_utc=now_utc,
            )

    def run_eligible(self, now_utc: str) -> tuple[SchedulerInvocationOutcome, ...]:
        """Dispatch every invocation that is eligible now, in retained plan order."""

        _whole_second(now_utc, "now_utc")
        with self._lock:
            return self._run_eligible_locked(now_utc)

    def run_to_quiescence(self, now_utc: str) -> tuple[SchedulerInvocationOutcome, ...]:
        """Dispatch eligible waves until a dependency cannot become eligible."""

        _whole_second(now_utc, "now_utc")
        with self._lock:
            for _ in range(MAX_ROUTE_STEPS):
                if not self._run_eligible_locked(now_utc):
                    break
            self._record_terminal_without_dispatch(now_utc)
            return self._ordered_outcomes()

    def cancel(self, *, now_utc: str) -> tuple[SchedulerInvocationOutcome, ...]:
        """Cancel unfinished scheduling. This does not call a specialist."""

        _whole_second(now_utc, "now_utc")
        with self._lock:
            self._require_retained()
            self._fanout.cancel(now_utc=now_utc)
            self._record_terminal_without_dispatch(now_utc)
            return self._ordered_outcomes()

    def outcomes(self) -> tuple[SchedulerInvocationOutcome, ...]:
        with self._lock:
            return self._ordered_outcomes()

    def handoff_to_ma4(self, now_utc: str) -> Ma4SchedulerHandoff:
        """Describe recorded attempts for a later MA4 admission pass.

        Admission is not performed. Synthetic orchestration results are not read.
        """

        _whole_second(now_utc, "now_utc")
        with self._lock:
            self._require_retained()
            self._fanout.observe_time(now_utc)
            self._record_terminal_without_dispatch(now_utc)
            observations = {
                item.step_id: item for item in self._fanout.observations(now_utc)
            }
            ordered: list[SchedulerInvocationOutcome] = []
            for step in self._fanout.plan.steps:
                recorded = self._outcomes.get(step.step_id)
                if recorded is None:
                    recorded = self._outcome_from_observation(step, observations[step.step_id])
                ordered.append(recorded)
            return Ma4SchedulerHandoff(
                session_id=self._record.session_id,
                master_request_id=self._planning.master_request_id,
                correlation_id=self._planning.correlation_id,
                outcomes=tuple(ordered),
                dependency_edges=self._fanout.dependency_edges(),
            )

    def _run_eligible_locked(self, now_utc: str) -> tuple[SchedulerInvocationOutcome, ...]:
        self._require_test_authority()
        self._require_retained()
        status = self._fanout.observe_time(now_utc)
        if status is not MasterExecutionScheduleStatus.OPEN:
            self._record_terminal_without_dispatch(now_utc)
            return ()
        eligible_ids = [item.step_id for item in self._fanout.eligible(now_utc)]
        dispatched = []
        steps = {step.step_id: step for step in self._fanout.plan.steps}
        for step_id in eligible_ids:
            dispatched.append(self._dispatch_invocation_locked(steps[step_id].invocation, now_utc))
        self._record_terminal_without_dispatch(now_utc)
        return tuple(dispatched)

    def _dispatch_invocation_locked(
        self,
        invocation: MasterInvocationAuthorization,
        now_utc: str,
    ) -> SchedulerInvocationOutcome:
        context, reservation, grant = self._stage_locked(invocation, now_utc)
        if self._seam is None or self._ledger is None:
            raise ContractValidationError("production_dispatch_disabled")
        consumed_grant = self._seam.consume(grant)
        consumed_reservation = self._ledger.consume(reservation)
        return self._submit_locked(
            invocation,
            consumed_grant=consumed_grant,
            consumed_reservation=consumed_reservation,
            context=context,
            now_utc=now_utc,
        )

    def _stage_locked(
        self,
        invocation: MasterInvocationAuthorization,
        now_utc: str,
    ) -> tuple[BoundedDispatchContext, LedgerReservation, HostDispatchGrant]:
        self._require_test_authority()
        step = self._require_exact(invocation)
        if self._seam is None or self._ledger is None:
            raise ContractValidationError("production_dispatch_disabled")
        if self._ledger.consumed_invocation(invocation):
            raise ContractValidationError("duplicate_dispatch")
        self._reject_terminal_block(step, now_utc)
        self._require_open(now_utc)
        self._require_eligible(step, now_utc)
        self._require_worker(step)
        context = self._context_for(step, now_utc)
        reservation = self._ledger.reserve(self._binding, invocation, context)
        grant = self._seam.issue(self._binding, invocation, context, reservation)
        return context, reservation, grant

    def _submit_locked(
        self,
        invocation: MasterInvocationAuthorization,
        *,
        consumed_grant: object,
        consumed_reservation: object,
        context: object,
        now_utc: str,
    ) -> SchedulerInvocationOutcome:
        self._require_test_authority()
        step = self._require_exact(invocation)
        if self._seam is None or self._ledger is None or self._port is None:
            raise ContractValidationError("production_dispatch_disabled")
        if step.step_id in self._outcomes and self._ledger.consumed_invocation(invocation):
            raise ContractValidationError("duplicate_dispatch")
        self._reject_terminal_block(step, now_utc)
        cached = self._contexts.get((id(invocation), now_utc))
        if cached is None or context is not cached:
            raise ContractValidationError("bounded_context_mismatch")
        self._require_context_identity(step, cached)
        self._require_open(now_utc)
        record = self._seam.assert_consumed(consumed_grant, invocation, cached)
        self._ledger.assert_consumed(consumed_reservation, invocation, cached)
        if record.reservation is not self._ledger.original_reservation(consumed_reservation):
            raise ContractValidationError("reservation_mismatch")
        self._seam.mark_used(consumed_grant)
        self._require_eligible(step, now_utc)
        self._fanout.claim(invocation, SCHEDULER_CLAIMER_ID, now_utc=now_utc)
        try:
            observation = self._port.dispatch(
                consumed_grant=consumed_grant,
                consumed_reservation=consumed_reservation,
                context=cached,
                invocation=invocation,
            )
        except ContractValidationError:
            self._fail_if_in_progress(invocation, now_utc, "worker_failed")
            self._finish(
                step,
                ExecutionDisposition.TEST_WORKER_FAILED,
                succeeded=False,
                reason="worker_failed",
                now_utc=now_utc,
            )
            raise
        if type(observation) is not ScriptedWorkerObservation:
            self._fail_if_in_progress(invocation, now_utc, "worker_failed")
            return self._finish(
                step,
                ExecutionDisposition.TEST_WORKER_FAILED,
                succeeded=False,
                reason="worker_failed",
                now_utc=now_utc,
            )
        if observation.report is ScriptedWorkerReport.SUCCEEDED:
            if (
                observation.tool_calls_used > cached.max_tool_calls
                or observation.model_turns_used > cached.max_model_turns
            ):
                self._fanout.fail(invocation, SCHEDULER_CLAIMER_ID, now_utc=now_utc, reason="budget_exceeded")
                return self._finish(
                    step,
                    ExecutionDisposition.TEST_WORKER_FAILED,
                    succeeded=False,
                    reason="budget_exceeded",
                    now_utc=now_utc,
                )
            self._fanout.complete(invocation, SCHEDULER_CLAIMER_ID, now_utc=now_utc)
            return self._finish(
                step,
                ExecutionDisposition.TEST_WORKER_SUCCEEDED,
                succeeded=True,
                reason=None,
                now_utc=now_utc,
            )
        if observation.report is ScriptedWorkerReport.TIMED_OUT:
            self._fanout.fail(invocation, SCHEDULER_CLAIMER_ID, now_utc=now_utc, reason="worker_timeout")
            return self._finish(
                step,
                ExecutionDisposition.TEST_WORKER_TIMED_OUT,
                succeeded=False,
                reason="worker_timeout",
                now_utc=now_utc,
            )
        self._fanout.fail(invocation, SCHEDULER_CLAIMER_ID, now_utc=now_utc, reason="worker_failed")
        return self._finish(
            step,
            ExecutionDisposition.TEST_WORKER_FAILED,
            succeeded=False,
            reason="worker_failed",
            now_utc=now_utc,
        )

    def _finish(
        self,
        step: MasterRouteStepPlan,
        disposition: ExecutionDisposition,
        *,
        succeeded: bool,
        reason: str | None,
        now_utc: str,
    ) -> SchedulerInvocationOutcome:
        observation = next(
            item for item in self._fanout.observations(now_utc) if item.step_id == step.step_id
        )
        scheduling_completed = observation.state is MasterSchedulingState.COMPLETED
        outcome = self._make_outcome(
            step,
            scheduling_state=observation.state,
            execution_disposition=disposition,
            scheduling_completed=scheduling_completed,
            execution_succeeded=succeeded and scheduling_completed,
            reason=reason if reason is not None else observation.reason,
        )
        self._outcomes[step.step_id] = outcome
        return outcome

    def _fail_if_in_progress(self, invocation: MasterInvocationAuthorization, now_utc: str, reason: str) -> None:
        observations = {item.invocation_id: item for item in self._fanout.observations(now_utc)}
        current = observations.get(invocation.invocation_id)
        if current is not None and current.state is MasterSchedulingState.IN_PROGRESS:
            self._fanout.fail(invocation, SCHEDULER_CLAIMER_ID, now_utc=now_utc, reason=reason)

    def _require_test_authority(self) -> None:
        if PRODUCTION_DISPATCH_ENABLED is not False:
            raise ContractValidationError("production_dispatch_disabled")
        if self._seam is None and self._ledger is None:
            raise ContractValidationError("production_dispatch_disabled")
        if type(self._seam) is not LocalTestHostAuthoritySeam:
            raise ContractValidationError("host_grant_authority_missing")
        if type(self._ledger) is not InProcessLedgerReservationDouble:
            raise ContractValidationError("ledger_authority_missing")
        if self._port is None:
            raise ContractValidationError("dispatch_port_missing")

    def _require_worker(self, step: MasterRouteStepPlan) -> None:
        port = self._port
        if isinstance(port, LocalTestWorkerDispatchPort) and not port.worker_registered(step.invocation.specialist):
            raise ContractValidationError("test_worker_not_registered")

    def _reject_terminal_block(self, step: MasterRouteStepPlan, now_utc: str) -> None:
        status = self._fanout.observe_time(now_utc)
        current = {item.step_id: item for item in self._fanout.observations(now_utc)}[step.step_id]
        reason = current.reason or ""
        if current.state is MasterSchedulingState.SKIPPED and reason.startswith("dependency_"):
            raise ContractValidationError("dependency_failed")
        if current.state is MasterSchedulingState.CANCELLED or status is MasterExecutionScheduleStatus.CANCELLED:
            raise ContractValidationError("schedule_cancelled")
        if status is MasterExecutionScheduleStatus.EXPIRED or reason == "deadline_expired":
            raise ContractValidationError("deadline_expired")

    def _require_open(self, now_utc: str) -> None:
        status = self._fanout.observe_time(now_utc)
        if status is MasterExecutionScheduleStatus.EXPIRED:
            raise ContractValidationError("deadline_expired")
        if status is MasterExecutionScheduleStatus.CANCELLED:
            raise ContractValidationError("schedule_cancelled")
        if status is not MasterExecutionScheduleStatus.OPEN:
            raise ContractValidationError("schedule_closed")

    def _require_eligible(self, step: MasterRouteStepPlan, now_utc: str) -> None:
        eligible = {item.step_id: item for item in self._fanout.eligible(now_utc)}
        if step.step_id in eligible:
            return
        current = {item.step_id: item for item in self._fanout.observations(now_utc)}[step.step_id]
        reason = current.reason or ""
        if current.state is MasterSchedulingState.SKIPPED and reason.startswith("dependency_"):
            raise ContractValidationError("dependency_failed")
        if current.state is MasterSchedulingState.CANCELLED:
            raise ContractValidationError("schedule_cancelled")
        if reason == "deadline_expired" or self._fanout.status is MasterExecutionScheduleStatus.EXPIRED:
            raise ContractValidationError("deadline_expired")
        raise ContractValidationError("step_not_eligible")

    def _require_retained(self) -> MasterRoutePlan:
        retained = self._registry.plan_for(self._record)
        if retained is not self._fanout.plan:
            raise ContractValidationError("reconstructed_plan_rejected")
        if retained.execution_authority is not MasterRouteExecutionAuthority.DENIED:
            raise ContractValidationError("execution_authority_not_granted")
        if retained.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
            raise ContractValidationError("request_coverage_not_established")
        if retained.end_to_end_fulfilled:
            raise ContractValidationError("end_to_end_fulfillment_not_granted")
        if len(retained.steps) > MAX_ROUTE_STEPS:
            raise ContractValidationError("excessive_plan_size")
        decisions = sum(step.invocation.specialist is MasterSpecialist.DECISION for step in retained.steps)
        if decisions > MAX_DECISION_STEPS:
            raise ContractValidationError("decision_fanout_not_permitted")
        return retained

    def _require_exact(self, invocation: object) -> MasterRouteStepPlan:
        plan = self._require_retained()
        if isinstance(invocation, (str, bytes, bytearray)) or isinstance(invocation, Mapping):
            raise ContractValidationError("substituted_invocation_rejected")
        if not isinstance(invocation, MasterInvocationAuthorization):
            raise ContractValidationError("forged_invocation_rejected")
        for step in plan.steps:
            if step.invocation is invocation:
                self._require_request_identity(invocation)
                return step
        if any(step.invocation.invocation_id == invocation.invocation_id for step in plan.steps):
            raise ContractValidationError("substituted_invocation_rejected")
        raise ContractValidationError("unknown_invocation")

    def _require_request_identity(self, invocation: MasterInvocationAuthorization) -> None:
        planning = self._planning
        if invocation.master_request_id != planning.master_request_id:
            raise ContractValidationError("master_request_mismatch")
        if invocation.correlation_id != planning.correlation_id:
            raise ContractValidationError("correlation_mismatch")
        if invocation.application_request_time_utc != planning.application_request_time_utc:
            raise ContractValidationError("application_time_mismatch")
        if not isinstance(invocation.specialist_request_id, str):
            raise ContractValidationError("specialist_request_id_required")
        identities = (
            invocation.master_request_id,
            invocation.invocation_id,
            invocation.specialist_request_id,
            invocation.correlation_id,
        )
        if len(set(identities)) != 4:
            raise ContractValidationError("request_identity_not_distinct")

    def _require_context_identity(self, step: MasterRouteStepPlan, context: BoundedDispatchContext) -> None:
        invocation = step.invocation
        if context.master_request_id != self._planning.master_request_id:
            raise ContractValidationError("master_request_mismatch")
        if context.correlation_id != self._planning.correlation_id:
            raise ContractValidationError("correlation_mismatch")
        if context.planning_session_id != self._record.session_id:
            raise ContractValidationError("session_mismatch")
        if context.step_id != step.step_id or context.invocation_id != invocation.invocation_id:
            raise ContractValidationError("request_identity_mismatch")
        if context.specialist is not invocation.specialist or context.purpose is not invocation.purpose:
            raise ContractValidationError("request_identity_mismatch")
        if context.capability is not step.capability:
            raise ContractValidationError("request_identity_mismatch")
        if context.depends_on != step.depends_on:
            raise ContractValidationError("invalid_dependency")
        if context.deadline_utc != self._deadline_utc:
            raise ContractValidationError("deadline_mismatch")
        if context.accepted_at_utc != self._planning.application_request_time_utc:
            raise ContractValidationError("application_time_mismatch")

    def _context_for(self, step: MasterRouteStepPlan, now_utc: str) -> BoundedDispatchContext:
        invocation = step.invocation
        key = (id(invocation), now_utc)
        cached = self._contexts.get(key)
        if cached is not None:
            return cached
        executions, turns, calls = server_read_budget(invocation.specialist)
        if invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
            window: tuple[str, str] | None = (step.historical_start_utc, step.historical_end_utc)  # type: ignore[assignment]
        else:
            window = None
        if invocation.specialist is MasterSpecialist.DECISION:
            target = invocation.expected_aircraft_id or invocation.expected_recommendation_id
        else:
            target = None
        context = BoundedDispatchContext(
            master_request_id=self._planning.master_request_id,
            planning_session_id=self._record.session_id,
            step_id=step.step_id,
            invocation_id=invocation.invocation_id,
            specialist=invocation.specialist,
            purpose=invocation.purpose,
            capability=step.capability,
            region_scope=step.region_text,
            historical_window=window,
            decision_target_scope=target,
            accepted_at_utc=self._planning.application_request_time_utc,
            deadline_utc=self._deadline_utc,
            evaluation_utc=now_utc,
            idempotency_key=(
                f"ma3:{self._record.session_id}:{step.step_id}:{invocation.invocation_id}"
            ),
            max_executions=executions,
            max_model_turns=turns,
            max_tool_calls=calls,
            correlation_id=self._planning.correlation_id,
            depends_on=step.depends_on,
            planning_gates=step.gates,
            planning_ready_to_execute=step.ready_to_execute,
        )
        self._contexts[key] = context
        return context

    def _record_terminal_without_dispatch(self, now_utc: str) -> None:
        observations = {item.step_id: item for item in self._fanout.observations(now_utc)}
        for step in self._fanout.plan.steps:
            if step.step_id in self._outcomes:
                continue
            observation = observations[step.step_id]
            if observation.state not in _TERMINAL_SCHEDULING:
                continue
            self._outcomes[step.step_id] = self._outcome_from_observation(step, observation)

    def _outcome_from_observation(self, step: MasterRouteStepPlan, observation: object) -> SchedulerInvocationOutcome:
        state = observation.state  # type: ignore[attr-defined]
        reason = observation.reason  # type: ignore[attr-defined]
        reason_text = reason or ""
        if state is MasterSchedulingState.SKIPPED and reason_text.startswith("dependency_"):
            disposition = ExecutionDisposition.SKIPPED_PREDECESSOR
        elif state is MasterSchedulingState.SKIPPED and reason_text == "deadline_expired":
            disposition = ExecutionDisposition.SKIPPED_DEADLINE
        elif state is MasterSchedulingState.CANCELLED:
            disposition = ExecutionDisposition.CANCELLED
        elif state is MasterSchedulingState.FAILED:
            disposition = ExecutionDisposition.TEST_WORKER_FAILED
        else:
            disposition = ExecutionDisposition.NOT_DISPATCHED
        return self._make_outcome(
            step,
            scheduling_state=state,
            execution_disposition=disposition,
            scheduling_completed=state is MasterSchedulingState.COMPLETED,
            execution_succeeded=False,
            reason=reason,
        )

    def _make_outcome(
        self,
        step: MasterRouteStepPlan,
        *,
        scheduling_state: MasterSchedulingState,
        execution_disposition: ExecutionDisposition,
        scheduling_completed: bool,
        execution_succeeded: bool,
        reason: str | None,
    ) -> SchedulerInvocationOutcome:
        invocation = step.invocation
        window = None
        if invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
            window = (step.historical_start_utc, step.historical_end_utc)
        return SchedulerInvocationOutcome(
            session_id=self._record.session_id,
            master_request_id=self._planning.master_request_id,
            correlation_id=self._planning.correlation_id,
            step_id=step.step_id,
            invocation_id=invocation.invocation_id,
            specialist=invocation.specialist,
            scheduling_state=scheduling_state,
            execution_disposition=execution_disposition,
            scheduling_completed=scheduling_completed,
            execution_succeeded=execution_succeeded,
            historical_window=window,  # type: ignore[arg-type]
            depends_on=step.depends_on,
            reason=reason,
        )

    def _ordered_outcomes(self) -> tuple[SchedulerInvocationOutcome, ...]:
        return tuple(
            self._outcomes[step.step_id]
            for step in self._fanout.plan.steps
            if step.step_id in self._outcomes
        )
