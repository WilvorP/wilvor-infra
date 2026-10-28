"""Host-controlled whole-request deadline and consumable budget.

Four clocks stay distinct:

1. Request acceptance is the host UTC instant captured when a private
   offline harness opens a session.
2. The absolute execution deadline is that acceptance instant plus the
   injected remaining host runtime, floored to a whole UTC second so the
   deadline cannot run past the host boundary.
3. Elapsed execution time is the difference of the injected monotonic
   clock. It is not a subtraction of UTC timestamps.
4. Source observation time is recorded beside a read and is never copied
   onto the acceptance instant or the deadline.

The only deadline sources are the injected host clock and the injected
remaining-runtime boundary (the Lambda ``get_remaining_time_in_millis``
shape, or an equivalent host boundary). ``requestContext.timeEpoch``,
model timestamps, envelope hints, and a journal's ``now_utc`` pair are not
deadline authority. A test-supplied clock can drive the private harness;
``clock_is_production_proof`` stays false, so that clock is not production
proof.

``MasterRuntimeReadJournal`` compares its own injected timestamps with
``timeout_budget_seconds``. Those are local fake observations. They are not
AWS remaining-time enforcement. This module refuses to adopt them.

Ceilings are the published ones: ``server_read_budget``, ``MAX_ROUTE_STEPS``,
``MAX_DECISION_STEPS``, ``MAX_HISTORICAL_WINDOW``, ``HISTORICAL_LIST_MAX``,
``LIVE_OPS_HAZARD_IDS_MAX``, ``MAX_TOOL_CALLS_PER_BATCH``, and the native
invalid-tool correction cap of 1. This module does not publish a higher
turn, tool, row, or retry ceiling. Per-specialist turn, tool, retry, and
row consumption is cumulative across invocations of that specialist and is
also capped again on each invocation. Whole-request specialist executions
stop at ``MAX_ROUTE_STEPS``. Decision executions stop at
``MAX_DECISION_STEPS``.

A descriptive within-ceiling result is not a production grant. The deployed
binder stays disabled until an independent principal, grant, and ledger
exist. This module does not issue any of those.

Who can invoke the Lambda: any IAM principal with ``lambda:InvokeFunction``,
including a direct invoke that forges ``requestContext``. API Gateway JWT
validation is an edge control. It is not cryptographic attestation attached
to the event. ``bind_deployed_host_budget`` does not read the event.

Integration, not imported from unmerged Sprint 3B.2 modules:

- Read executor: call ``begin_read`` before
  ``MasterRuntimeReadJournal.execute``. Enter the read only after
  ``mark_entered`` reports ``STARTED``. Pass a downstream timeout and poll
  cancellation; this process cannot forcibly cancel a blocking socket.
  Finish with ``finish_started``. Journal clock samples stay outside this
  deadline. Tool allowlisting remains ``server_read_tools``,
  ``server_read_argument_names``, and ``server_read_resources``. Scan is
  rejected here even if a caller labels it as a query.
- MA3 adapter: ``master_specialist_adapters.describe_planned_adapters``
  stays non-executable. A future adapter calls ``begin_specialist_execution``,
  then ``begin_model_turn`` and ``begin_tool_calls`` inside the native loop,
  and ``begin_retry`` for a correction. A retry does not reset counters.
  Cancellation stops new begins. An in-flight provider call is
  ``TERMINAL_PARTIAL`` after ``finish_started``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from threading import Lock

from wilvor_ai.contracts import (
    PROHIBITED_V1_CAPABILITIES,
    PROVENANCE_TOKEN_MAX_LENGTH,
    AgentCapability,
    ContractValidationError,
    _validate_bounded_text,
    _validate_utc,
)
from wilvor_ai.live_ops_model_contracts import MAX_TOOL_CALLS_PER_BATCH
from wilvor_ai.live_ops_public_contracts import LIVE_OPS_HAZARD_IDS_MAX
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_read_resource_guard import (
    HISTORICAL_LIST_MAX,
    ReadOperation,
    server_read_argument_names,
    server_read_budget,
    server_read_resources,
    server_read_tools,
)
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS,
    MAX_HISTORICAL_WINDOW,
    MAX_ROUTE_STEPS,
    MasterRouteExecutionAuthority,
)


MASTER_RUNTIME_BUDGET_SCHEMA_VERSION = "wilvor.ai.master_runtime_budget.v1"

# Closed until an independent host principal, grant, and ledger exist.
PRODUCTION_GRANT_AVAILABLE = False
PRODUCTION_PRINCIPAL_AVAILABLE = False
PRODUCTION_LEDGER_AVAILABLE = False
DEPLOYED_HOST_ENABLED = False
SOURCE_AUTHENTICATION_AVAILABLE = False
CLOCK_IS_PRODUCTION_PROOF = False
LOCAL_FAKE_JOURNAL_IS_AWS_ENFORCEMENT = False
EMPTY_ROWS_CREATE_VERIFIED_ZERO = False

# Restates historical_specialist.MAX_INVALID_TOOL_CALL_CORRECTIONS and
# decision_specialist.MAX_INVALID_TOOL_CALL_CORRECTIONS. Live Ops publishes
# no separate correction ceiling; a retry still consumes this cap and does
# not reset turn or tool counters.
PUBLISHED_INVALID_TOOL_CALL_CORRECTIONS = 1

_DRIFT_TOLERANCE_SECONDS = 1
_NS_PER_MS = 1_000_000
_NS_PER_SECOND = 1_000_000_000

_ROW_CEILING = {
    MasterSpecialist.HISTORICAL_ANALYTICS: HISTORICAL_LIST_MAX,
    MasterSpecialist.LIVE_OPS: LIVE_OPS_HAZARD_IDS_MAX,
}

_SPECIALIST_BUDGET = {
    specialist: server_read_budget(specialist) for specialist in MasterSpecialist
}

READ_EXECUTOR_SEAM = (
    "Call RuntimeBudgetSession.begin_read before MasterRuntimeReadJournal.execute. "
    "Enter the read only when mark_entered reports STARTED. "
    "MasterRuntimeReadJournal timeout_budget_seconds and its injected now_utc "
    "observations are local fake deadline observations and are not AWS Lambda "
    "remaining-time enforcement. After the journal outcome, call finish_started. "
    "A started read that times out or is cancelled is TERMINAL_PARTIAL because "
    "Python cannot forcibly cancel every blocking call. The executor must pass "
    "a downstream timeout and poll cancellation. Enforce server_read_tools, "
    "server_read_argument_names, and server_read_resources at this begin, not "
    "only while parsing. Do not import an unmerged Sprint 3B.2 read-executor "
    "module. A budget decision does not admit aviation evidence."
)

MA3_ADAPTER_SEAM = (
    "master_specialist_adapters.describe_planned_adapters remains non-executable "
    "and BLOCKED_BEFORE_EXECUTION_AUTHORIZATION. A future MA3 adapter calls "
    "begin_specialist_execution, then begin_model_turn and begin_tool_calls "
    "inside the native specialist loop, and begin_retry for an invalid-tool "
    "correction. begin_retry must not reset counters or reissue the deadline. "
    "Live Ops batches cannot exceed live_ops_model_contracts.MAX_TOOL_CALLS_PER_BATCH. "
    "Cancellation and deadline expiry stop new begin calls. An in-flight "
    "specialist or provider call is reported TERMINAL_PARTIAL via finish_started "
    "and is not killed. Do not import an unmerged Sprint 3B.2 MA3 module. A "
    "budget decision does not set execution authority, production authority, "
    "or source authenticity."
)

_DENIAL_MAP = (
    "DEADLINE_EXPIRED maps to MasterReadDenialReason.DEADLINE_EXPIRED",
    "CALLER_DEADLINE_NOT_AUTHORITY maps to MasterReadDenialReason.CALLER_DEADLINE_NOT_AUTHORITY",
    "BUDGET_EXHAUSTED maps to MasterReadDenialReason.BUDGET_EXHAUSTED",
    "CALLER_BUDGET_NOT_AUTHORITY maps to MasterReadDenialReason.CALLER_BUDGET_NOT_AUTHORITY",
    "CANCELLED maps to MasterReadDenialReason.CANCELLED",
    "HISTORICAL_INTERVAL_ESCALATION maps to MasterReadDenialReason.HISTORICAL_INTERVAL_ESCALATION",
    "PROHIBITED_CAPABILITY maps to MasterReadDenialReason.PROHIBITED_CAPABILITY",
    "SCAN_NOT_PERMITTED maps to MasterReadDenialReason.SCAN_NOT_PERMITTED",
    "UNSUPPORTED_TOOL maps to MasterReadDenialReason.UNSUPPORTED_TOOL",
    "ARGUMENT_NOT_ALLOWLISTED maps to MasterReadDenialReason.ARGUMENT_NOT_ALLOWLISTED",
    "RESOURCE_NOT_ALLOWLISTED maps to MasterReadDenialReason.RESOURCE_NOT_ALLOWLISTED",
    "OPERATION_NOT_ALLOWLISTED maps to MasterReadDenialReason.OPERATION_NOT_ALLOWLISTED",
)


class _HarnessSeal:
    """Process-local object. A Lambda event cannot become this seal."""


_OFFLINE_HARNESS_SEAL = _HarnessSeal()


class BudgetAction(str, Enum):
    """Consumable work. None of these values authorizes production dispatch."""

    MODEL_TURN = "MODEL_TURN"
    TOOL_CALL = "TOOL_CALL"
    SPECIALIST_EXECUTION = "SPECIALIST_EXECUTION"
    RETRY = "RETRY"
    READ = "READ"


class BudgetEvaluation(str, Enum):
    """Evaluated outcome. EVALUATED_WITHIN_CEILING is descriptive only."""

    EVALUATED_WITHIN_CEILING = "EVALUATED_WITHIN_CEILING"
    DENIED = "DENIED"
    EXHAUSTED = "EXHAUSTED"
    CANCELLED = "CANCELLED"
    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
    CLOCK_DRIFT = "CLOCK_DRIFT"
    MONOTONIC_REGRESSION = "MONOTONIC_REGRESSION"
    CLOSED = "CLOSED"


class StartedTaskDisposition(str, Enum):
    """What happened to one reserved action. Not an evidence status."""

    NOT_STARTED = "NOT_STARTED"
    RESERVED = "RESERVED"
    STARTED = "STARTED"
    COMPLETED_BOUNDED = "COMPLETED_BOUNDED"
    FAILED = "FAILED"
    CANCELLED_BEFORE_START = "CANCELLED_BEFORE_START"
    TERMINAL_PARTIAL = "TERMINAL_PARTIAL"


class BudgetDenialReason(str, Enum):
    """Why new work was refused. Overlapping names match the read guard."""

    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
    CALLER_DEADLINE_NOT_AUTHORITY = "CALLER_DEADLINE_NOT_AUTHORITY"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    CALLER_BUDGET_NOT_AUTHORITY = "CALLER_BUDGET_NOT_AUTHORITY"
    CANCELLED = "CANCELLED"
    CLOCK_DRIFT = "CLOCK_DRIFT"
    MONOTONIC_REGRESSION = "MONOTONIC_REGRESSION"
    HISTORICAL_INTERVAL_ESCALATION = "HISTORICAL_INTERVAL_ESCALATION"
    PROHIBITED_CAPABILITY = "PROHIBITED_CAPABILITY"
    SCAN_NOT_PERMITTED = "SCAN_NOT_PERMITTED"
    UNSUPPORTED_TOOL = "UNSUPPORTED_TOOL"
    ARGUMENT_NOT_ALLOWLISTED = "ARGUMENT_NOT_ALLOWLISTED"
    RESOURCE_NOT_ALLOWLISTED = "RESOURCE_NOT_ALLOWLISTED"
    OPERATION_NOT_ALLOWLISTED = "OPERATION_NOT_ALLOWLISTED"
    ROW_CEILING_NOT_PUBLISHED = "ROW_CEILING_NOT_PUBLISHED"
    BATCH_CEILING_EXCEEDED = "BATCH_CEILING_EXCEEDED"
    DEPLOYED_HOST_DISABLED = "DEPLOYED_HOST_DISABLED"


class ExhaustedScope(str, Enum):
    """Which published cap stopped the increment."""

    INVOCATION = "INVOCATION"
    SPECIALIST = "SPECIALIST"
    REQUEST = "REQUEST"


def _closed_production() -> None:
    if (
        PRODUCTION_GRANT_AVAILABLE is not False
        or PRODUCTION_PRINCIPAL_AVAILABLE is not False
        or PRODUCTION_LEDGER_AVAILABLE is not False
        or DEPLOYED_HOST_ENABLED is not False
        or SOURCE_AUTHENTICATION_AVAILABLE is not False
        or CLOCK_IS_PRODUCTION_PROOF is not False
        or LOCAL_FAKE_JOURNAL_IS_AWS_ENFORCEMENT is not False
        or EMPTY_ROWS_CREATE_VERIFIED_ZERO is not False
    ):
        raise ContractValidationError("production_budget_issuer_not_integrated")


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


def _optional_token(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _token(value, field_name)


def _whole_second(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ContractValidationError("naive_datetime_rejected")
        raise ContractValidationError(f"invalid_{field_name}")
    if not isinstance(value, str) or len(value) != 20 or value[10] != "T" or not value.endswith("Z"):
        raise ContractValidationError(f"invalid_{field_name}")
    errors = _validate_utc(value, field_name)
    if errors:
        raise ContractValidationError(errors)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ContractValidationError(f"invalid_{field_name}") from exc
    return parsed


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _count(value: object, field_name: str, *, allow_zero: bool) -> int:
    if type(value) is not int or value < 0 or (value == 0 and not allow_zero):
        raise ContractValidationError("invalid_counter")
    return value


def _window(value: object) -> tuple[datetime, datetime] | None:
    if value is None:
        return None
    if not isinstance(value, tuple) or len(value) != 2:
        raise ContractValidationError("invalid_historical_window")
    start = _whole_second(value[0], "historical_window")
    end = _whole_second(value[1], "historical_window")
    if start >= end:
        raise ContractValidationError("invalid_historical_window")
    return start, end


def _window_exceeds(span: tuple[datetime, datetime] | None) -> bool:
    if span is None:
        return False
    return span[1] - span[0] > MAX_HISTORICAL_WINDOW


@dataclass(frozen=True)
class RequestBudgetEnvelope:
    """Descriptive request context. Hints are not clocks, grants, or principals."""

    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    correlation_id: str
    idempotency_key: str
    principal_ref: str | None = None
    policy_revision: str | None = None
    resource_scope: str | None = None
    region_scope: str | None = None
    historical_window: tuple[str, str] | None = None
    decision_target_scope: str | None = None
    accepted_at_utc: str | None = None
    deadline_utc: str | None = None
    max_model_turns: int | None = None
    max_tool_calls: int | None = None

    def __post_init__(self) -> None:
        _token(self.master_request_id, "master_request_id")
        _token(self.planning_session_id, "planning_session_id")
        _token(self.step_id, "step_id")
        _token(self.invocation_id, "invocation_id")
        _token(self.correlation_id, "correlation_id")
        _token(self.idempotency_key, "idempotency_key")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        if not isinstance(self.capability, AgentCapability):
            raise ContractValidationError("invalid_capability")
        if self.capability in PROHIBITED_V1_CAPABILITIES:
            raise ContractValidationError("prohibited_capability")
        _optional_token(self.principal_ref, "principal_ref")
        _optional_token(self.policy_revision, "policy_revision")
        _optional_token(self.resource_scope, "resource_scope")
        _optional_token(self.region_scope, "region_scope")
        _optional_token(self.decision_target_scope, "decision_target_scope")
        if self.accepted_at_utc is not None:
            _whole_second(self.accepted_at_utc, "accepted_at_utc")
        if self.deadline_utc is not None:
            _whole_second(self.deadline_utc, "deadline_utc")
        if self.max_model_turns is not None:
            _count(self.max_model_turns, "max_model_turns", allow_zero=False)
        if self.max_tool_calls is not None:
            _count(self.max_tool_calls, "max_tool_calls", allow_zero=False)
        _window(self.historical_window)


@dataclass(frozen=True)
class SpecialistCeilings:
    """Published caps for one specialist. Not a caller-writable budget."""

    specialist: MasterSpecialist
    specialist_executions: int
    model_turns: int
    tool_calls: int
    retries: int
    data_rows: int | None
    request_executions: int
    decision_executions: int
    live_ops_tool_batch: int | None


@dataclass(frozen=True)
class BudgetUsage:
    """Counters actually consumed. A copy cannot reset the session."""

    model_turns: int
    tool_calls: int
    specialist_executions: int
    retries: int
    data_rows: int


@dataclass(frozen=True)
class SourceObservationRecord:
    """Source observation clock. It does not move the execution deadline."""

    invocation_id: str
    observed_at_utc: str
    rows: int
    pagination_pages: int
    source_authenticated: bool = False
    verified_zero_claim: bool = False

    def __post_init__(self) -> None:
        _closed_production()
        if self.source_authenticated is not False or self.verified_zero_claim is not False:
            raise ContractValidationError("source_observation_is_not_authentication")


@dataclass(frozen=True)
class BudgetDecision:
    """Evaluated budget result. A descriptive allow is not a production grant."""

    evaluation: BudgetEvaluation
    disposition: StartedTaskDisposition
    denial_reasons: tuple[BudgetDenialReason, ...]
    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    correlation_id: str
    idempotency_key: str
    action_id: str
    accepted_at_utc: str
    deadline_utc: str
    elapsed_monotonic_ns: int
    source_observed_at_utc: str | None
    exhausted_scope: ExhaustedScope | None
    descriptive_allow: bool
    host_issued_deadline: bool
    consumed_by_this_action: tuple[tuple[str, int], ...]
    principal_ref: str | None = None
    policy_revision: str | None = None
    resource_scope: str | None = None
    region_scope: str | None = None
    decision_target_scope: str | None = None
    production_grant_issued: bool = False
    principal_established: bool = False
    ledger_established: bool = False
    deployed_host_enabled: bool = False
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticated: bool = False
    dispatch_permitted: bool = False
    clock_is_production_proof: bool = False
    verified_zero_claim: bool = False
    schema_version: str = MASTER_RUNTIME_BUDGET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _closed_production()
        if self.schema_version != MASTER_RUNTIME_BUDGET_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if (
            self.production_grant_issued is not False
            or self.principal_established is not False
            or self.ledger_established is not False
            or self.deployed_host_enabled is not False
            or self.execution_authority is not MasterRouteExecutionAuthority.DENIED
            or self.production_authority is not MasterProductionAuthority.DENIED
            or self.source_authenticated is not False
            or self.dispatch_permitted is not False
            or self.clock_is_production_proof is not False
            or self.verified_zero_claim is not False
        ):
            raise ContractValidationError("descriptive_allow_is_not_a_production_grant")
        if self.descriptive_allow is not _descriptive_allow(self.evaluation, self.disposition):
            raise ContractValidationError("descriptive_allow_mismatch")
        if self.host_issued_deadline is not True:
            raise ContractValidationError("deadline_not_host_issued")

    def __bool__(self) -> bool:
        raise ContractValidationError("descriptive_allow_is_not_a_production_grant")


@dataclass(frozen=True)
class BudgetOperationResult:
    """In-process harness observation. The callable's return value is not stored."""

    decision: BudgetDecision
    callable_invoked: bool
    bounded_operation_completed: bool
    production_grant_issued: bool = False
    source_authenticated: bool = False
    returned_value_admitted: bool = False

    def __post_init__(self) -> None:
        _closed_production()
        if type(self.decision) is not BudgetDecision:
            raise ContractValidationError("invalid_budget_decision")
        if (
            self.production_grant_issued is not False
            or self.source_authenticated is not False
            or self.returned_value_admitted is not False
        ):
            raise ContractValidationError("bounded_operation_is_not_a_grant")
        completed = self.decision.disposition is StartedTaskDisposition.COMPLETED_BOUNDED
        if self.bounded_operation_completed is not completed:
            raise ContractValidationError("bounded_operation_mismatch")

    def __bool__(self) -> bool:
        raise ContractValidationError("bounded_operation_is_not_a_grant")


@dataclass(frozen=True)
class DeployedHostBudget:
    """Production entry. Always closed. The event is not a clock or a principal."""

    enabled: bool = False
    production_grant_issued: bool = False
    principal_established: bool = False
    ledger_established: bool = False
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    accepted_at_utc: str | None = None
    deadline_utc: str | None = None
    untrusted_input_ignored: bool = False
    denial_reason: BudgetDenialReason = BudgetDenialReason.DEPLOYED_HOST_DISABLED
    invoke_boundary: str = (
        "Any IAM principal with lambda:InvokeFunction can invoke the function, "
        "including a direct invoke that forges requestContext.timeEpoch and "
        "requestContext.authorizer.jwt. API Gateway JWT validation is an edge "
        "control and is not cryptographic attestation on the event. This binder "
        "does not issue a principal, grant, or ledger."
    )
    schema_version: str = MASTER_RUNTIME_BUDGET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _closed_production()
        if (
            self.enabled is not False
            or self.production_grant_issued is not False
            or self.principal_established is not False
            or self.ledger_established is not False
            or self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED
            or self.production_authority is not MasterProductionAuthority.DENIED
            or self.execution_authority is not MasterRouteExecutionAuthority.DENIED
            or self.accepted_at_utc is not None
            or self.deadline_utc is not None
            or self.denial_reason is not BudgetDenialReason.DEPLOYED_HOST_DISABLED
        ):
            raise ContractValidationError("deployed_host_budget_disabled")

    def __bool__(self) -> bool:
        raise ContractValidationError("deployed_host_budget_is_not_a_grant")


@dataclass(frozen=True)
class HostEnforcementRefusal:
    """A journal observation was not accepted as the host deadline."""

    accepted: bool = False
    reason: str = "LOCAL_FAKE_JOURNAL_DEADLINE_IS_NOT_AWS_ENFORCEMENT"
    production_grant_issued: bool = False

    def __post_init__(self) -> None:
        if self.accepted is not False or self.production_grant_issued is not False:
            raise ContractValidationError("journal_clock_is_not_host_enforcement")
        if self.reason != "LOCAL_FAKE_JOURNAL_DEADLINE_IS_NOT_AWS_ENFORCEMENT":
            raise ContractValidationError("journal_clock_is_not_host_enforcement")


@dataclass(frozen=True)
class RuntimeBudgetIntegrationSeam:
    """Documented calls a future read executor and MA3 adapter must make."""

    read_executor: str
    ma3_adapter: str
    denial_vocabulary: tuple[str, ...] = _DENIAL_MAP
    journal_deadline_is_aws_enforcement: bool = False
    production_grant_issued: bool = False

    def __post_init__(self) -> None:
        if self.read_executor != READ_EXECUTOR_SEAM or self.ma3_adapter != MA3_ADAPTER_SEAM:
            raise ContractValidationError("invalid_integration_seam")
        if self.journal_deadline_is_aws_enforcement is not False or self.production_grant_issued is not False:
            raise ContractValidationError("invalid_integration_seam")


def _descriptive_allow(evaluation: BudgetEvaluation, disposition: StartedTaskDisposition) -> bool:
    return evaluation is BudgetEvaluation.EVALUATED_WITHIN_CEILING and disposition in {
        StartedTaskDisposition.RESERVED,
        StartedTaskDisposition.STARTED,
        StartedTaskDisposition.COMPLETED_BOUNDED,
    }


def offline_harness_seal() -> object:
    """Return the process-local harness seal.

    A dict, header, boolean, environment variable, or forged gateway event
    cannot become this object. Holding it still does not issue a grant.
    """

    return _OFFLINE_HARNESS_SEAL


def bind_deployed_host_budget(
    host_event: object = None,
    clock: object = None,
    remaining_runtime: object = None,
) -> DeployedHostBudget:
    """Return the disabled production budget.

    ``host_event``, ``clock``, and ``remaining_runtime`` are ignored. They
    are not read. A forged ``timeEpoch`` cannot become the deadline.
    """

    _closed_production()
    return DeployedHostBudget(
        untrusted_input_ignored=host_event is not None or clock is not None or remaining_runtime is not None,
    )


def promote_budget_decision_to_production_grant(decision: BudgetDecision) -> None:
    """Refuse to turn a descriptive allow into a production grant."""

    if type(decision) is not BudgetDecision:
        raise ContractValidationError("invalid_budget_decision")
    _closed_production()
    raise ContractValidationError("descriptive_allow_is_not_a_production_grant")


def journal_observation_is_not_host_enforcement(
    started_at: object = None,
    finished_at: object = None,
    budget_seconds: object = None,
) -> HostEnforcementRefusal:
    """Refuse a journal timestamp pair as AWS execution enforcement.

    The arguments are accepted so a caller can show the observation it
    tried to reuse. They are not parsed as the host deadline.
    """

    del started_at, finished_at, budget_seconds
    _closed_production()
    return HostEnforcementRefusal()


def integration_seam() -> RuntimeBudgetIntegrationSeam:
    """Return the read-executor and MA3 adapter call seam."""

    return RuntimeBudgetIntegrationSeam(
        read_executor=READ_EXECUTOR_SEAM,
        ma3_adapter=MA3_ADAPTER_SEAM,
    )


def published_ceilings(specialist: MasterSpecialist) -> SpecialistCeilings:
    """Return the published caps. Caller numbers cannot replace them."""

    if not isinstance(specialist, MasterSpecialist):
        raise ContractValidationError("invalid_specialist")
    executions, turns, tools = _SPECIALIST_BUDGET[specialist]
    return SpecialistCeilings(
        specialist=specialist,
        specialist_executions=executions,
        model_turns=turns,
        tool_calls=tools,
        retries=PUBLISHED_INVALID_TOOL_CALL_CORRECTIONS,
        data_rows=_ROW_CEILING.get(specialist),
        request_executions=MAX_ROUTE_STEPS,
        decision_executions=MAX_DECISION_STEPS,
        live_ops_tool_batch=MAX_TOOL_CALLS_PER_BATCH if specialist is MasterSpecialist.LIVE_OPS else None,
    )


@dataclass
class _Counters:
    model_turns: int = 0
    tool_calls: int = 0
    executions: int = 0
    retries: int = 0
    data_rows: int = 0


@dataclass
class _Invocation:
    step_id: str
    invocation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    counters: _Counters


@dataclass
class _Action:
    action_id: str
    invocation_id: str
    kind: BudgetAction
    count: int
    rows: int
    evaluation: BudgetEvaluation
    disposition: StartedTaskDisposition
    denial_reasons: tuple[BudgetDenialReason, ...]
    exhausted_scope: ExhaustedScope | None
    consumed: tuple[tuple[str, int], ...]
    entered: bool = False
    finished: bool = False


class RuntimeBudgetSession:
    """One whole-request budget. Shared caps are consumed under one lock."""

    def __init__(
        self,
        envelope: RequestBudgetEnvelope,
        clock: object,
        remaining_runtime: object,
    ) -> None:
        _closed_production()
        if type(envelope) is not RequestBudgetEnvelope:
            raise ContractValidationError("invalid_budget_envelope")
        if not callable(getattr(clock, "now_utc", None)) or not callable(getattr(clock, "monotonic_ns", None)):
            raise ContractValidationError("invalid_host_clock")
        if not callable(getattr(remaining_runtime, "remaining_ms", None)):
            raise ContractValidationError("invalid_host_runtime")
        accepted = _read_utc(clock)
        origin = _read_monotonic(clock)
        remaining_ms = _count(remaining_runtime.remaining_ms(), "remaining_ms", allow_zero=True)
        span = _window(envelope.historical_window)
        hint_deadline = None if envelope.deadline_utc is None else _whole_second(envelope.deadline_utc, "deadline_utc")
        deadline = accepted + timedelta(seconds=remaining_ms // 1000)
        max_turns = max(item[1] for item in _SPECIALIST_BUDGET.values())
        max_tools = max(item[2] for item in _SPECIALIST_BUDGET.values())
        self._envelope = envelope
        self._clock = clock
        self._accepted_at = accepted
        self._deadline = deadline
        self._origin_ns = origin
        self._last_monotonic_ns = origin
        self._last_utc = accepted
        self._budget_ns = remaining_ms * _NS_PER_MS
        self._issued_remaining_ms = remaining_ms
        self._caller_deadline_blocks = hint_deadline is not None and hint_deadline != deadline
        self._caller_budget_blocks = (
            (envelope.max_model_turns is not None and envelope.max_model_turns > max_turns)
            or (envelope.max_tool_calls is not None and envelope.max_tool_calls > max_tools)
        )
        self._historical_window_blocks = _window_exceeds(span)
        self._cancelled = False
        self._drift = False
        self._regression = False
        self._lock = Lock()
        self._request = _Counters()
        self._by_specialist = {specialist: _Counters() for specialist in MasterSpecialist}
        self._invocations: dict[str, _Invocation] = {}
        self._actions: dict[str, _Action] = {}
        self._observations: list[SourceObservationRecord] = []
        self._decision_invocations = 0
        self._register(
            step_id=envelope.step_id,
            invocation_id=envelope.invocation_id,
            specialist=envelope.specialist,
            purpose=envelope.purpose,
            capability=envelope.capability,
        )

    def __bool__(self) -> bool:
        raise ContractValidationError("budget_session_is_not_a_grant")

    @property
    def accepted_at_utc(self) -> str:
        return _format_utc(self._accepted_at)

    @property
    def deadline_utc(self) -> str:
        return _format_utc(self._deadline)

    @property
    def issued_remaining_ms(self) -> int:
        return self._issued_remaining_ms

    @property
    def production_grant_issued(self) -> bool:
        return False

    @property
    def principal_established(self) -> bool:
        return False

    @property
    def ledger_established(self) -> bool:
        return False

    @property
    def deployed_host_enabled(self) -> bool:
        return False

    @property
    def clock_is_production_proof(self) -> bool:
        return False

    def register_invocation(
        self,
        *,
        step_id: str,
        invocation_id: str,
        specialist: MasterSpecialist,
        purpose: MasterPurpose,
        capability: AgentCapability,
    ) -> None:
        """Admit another planned invocation under the published route caps."""

        with self._lock:
            self._register(
                step_id=step_id,
                invocation_id=invocation_id,
                specialist=specialist,
                purpose=purpose,
                capability=capability,
            )

    def decision_for(self, action_id: str) -> BudgetDecision:
        """Return the current evaluated snapshot for one action."""

        with self._lock:
            return self._decision(self._require_action(action_id))

    def usage(
        self,
        *,
        invocation_id: str | None = None,
        specialist: MasterSpecialist | None = None,
    ) -> BudgetUsage:
        """Return a copy of consumed counters."""

        with self._lock:
            if invocation_id is not None:
                invocation = self._require_invocation(invocation_id)
                if specialist is not None and invocation.specialist is not specialist:
                    raise ContractValidationError("specialist_mismatch")
                counters = invocation.counters
            elif specialist is not None:
                if not isinstance(specialist, MasterSpecialist):
                    raise ContractValidationError("invalid_specialist")
                counters = self._by_specialist[specialist]
            else:
                counters = self._request
            return BudgetUsage(
                model_turns=counters.model_turns,
                tool_calls=counters.tool_calls,
                specialist_executions=counters.executions,
                retries=counters.retries,
                data_rows=counters.data_rows,
            )

    def begin_model_turn(self, *, invocation_id: str, action_id: str, count: int = 1) -> BudgetDecision:
        return self._begin(BudgetAction.MODEL_TURN, invocation_id=invocation_id, action_id=action_id, count=count, rows=0)

    def begin_tool_calls(self, *, invocation_id: str, action_id: str, count: int) -> BudgetDecision:
        return self._begin(BudgetAction.TOOL_CALL, invocation_id=invocation_id, action_id=action_id, count=count, rows=0)

    def begin_specialist_execution(self, *, invocation_id: str, action_id: str) -> BudgetDecision:
        return self._begin(
            BudgetAction.SPECIALIST_EXECUTION,
            invocation_id=invocation_id,
            action_id=action_id,
            count=1,
            rows=0,
        )

    def begin_retry(self, *, invocation_id: str, action_id: str) -> BudgetDecision:
        """Consume one correction. Turn and tool counters stay where they are."""

        return self._begin(BudgetAction.RETRY, invocation_id=invocation_id, action_id=action_id, count=1, rows=0)

    def begin_read(
        self,
        *,
        invocation_id: str,
        action_id: str,
        tool_name: str,
        operation: ReadOperation,
        rows: int = 0,
        pagination_pages: int = 1,
        arguments: tuple[tuple[str, object], ...] | None = None,
        resource_id: str | None = None,
        historical_window: tuple[str, str] | None = None,
    ) -> BudgetDecision:
        """Reserve a keyed read. Scan and unknown tools consume nothing."""

        pages = _count(pagination_pages, "pagination_pages", allow_zero=False)
        row_count = _count(rows, "data_rows", allow_zero=True)
        with self._lock:
            invocation = self._require_invocation(invocation_id)
            refusal = self._read_policy(
                invocation,
                tool_name=tool_name,
                operation=operation,
                arguments=arguments,
                resource_id=resource_id,
                historical_window=historical_window,
            )
            if refusal is not None:
                return self._store_refusal(
                    kind=BudgetAction.READ,
                    invocation=invocation,
                    action_id=action_id,
                    count=pages,
                    rows=row_count,
                    evaluation=BudgetEvaluation.DENIED,
                    reasons=(refusal,),
                    scope=None,
                )
            return self._reserve(
                BudgetAction.READ,
                invocation=invocation,
                action_id=action_id,
                count=pages,
                rows=row_count,
            )

    def mark_entered(self, action_id: str, *, downstream_timeout_s: int) -> BudgetDecision:
        """Mark a reserved action started, or stop it before the callable runs.

        ``downstream_timeout_s`` is required because this process cannot
        cancel a blocking read or provider call that has already entered.
        """

        _count(downstream_timeout_s, "downstream_timeout_s", allow_zero=False)
        with self._lock:
            action = self._require_action(action_id)
            if action.finished or action.disposition is not StartedTaskDisposition.RESERVED:
                raise ContractValidationError("action_not_reserved")
            gate = self._gate()
            if gate is not None:
                evaluation, reasons = gate
                action.evaluation = evaluation
                action.denial_reasons = reasons
                action.disposition = _stopped_before_start(evaluation)
                return self._decision(action)
            self._require_downstream_timeout(downstream_timeout_s)
            action.entered = True
            action.disposition = StartedTaskDisposition.STARTED
            action.evaluation = BudgetEvaluation.EVALUATED_WITHIN_CEILING
            return self._decision(action)

    def finish_started(self, action_id: str, *, failed: bool = False) -> BudgetDecision:
        """Close an entered action. Cancellation after entry is terminal/partial."""

        if type(failed) is not bool:
            raise ContractValidationError("invalid_counter")
        with self._lock:
            action = self._require_action(action_id)
            if not action.entered:
                raise ContractValidationError("action_not_started")
            if action.finished:
                raise ContractValidationError("action_already_finished")
            self._finish_locked(action, failed=failed)
            return self._decision(action)

    def run_bounded_operation(
        self,
        action_id: str,
        operation: object,
        *,
        downstream_timeout_s: int,
    ) -> BudgetOperationResult:
        """Run one local callable inside a reservation.

        The callable is not invoked when cancellation or the deadline wins
        before entry. Once invoked, this method waits for it to return.
        Python cannot forcibly cancel the call. The return value is discarded.
        """

        if not callable(operation):
            raise ContractValidationError("operation_not_callable")
        _count(downstream_timeout_s, "downstream_timeout_s", allow_zero=False)
        with self._lock:
            action = self._require_action(action_id)
            if action.disposition is not StartedTaskDisposition.RESERVED:
                raise ContractValidationError("action_not_reserved")
            gate = self._gate()
            if gate is not None:
                evaluation, reasons = gate
                action.evaluation = evaluation
                action.denial_reasons = reasons
                action.disposition = _stopped_before_start(evaluation)
                decision = self._decision(action)
                invoked = False
            else:
                self._require_downstream_timeout(downstream_timeout_s)
                action.entered = True
                action.disposition = StartedTaskDisposition.STARTED
                decision = None
                invoked = True
        if decision is not None:
            return BudgetOperationResult(
                decision=decision,
                callable_invoked=invoked,
                bounded_operation_completed=False,
            )
        failed = False
        try:
            operation()
        except BaseException:
            failed = True
            with self._lock:
                self._finish_locked(action, failed=True)
            raise
        with self._lock:
            self._finish_locked(action, failed=failed)
            finished = self._decision(action)
        return BudgetOperationResult(
            decision=finished,
            callable_invoked=True,
            bounded_operation_completed=finished.disposition is StartedTaskDisposition.COMPLETED_BOUNDED,
        )

    def cancel(self) -> None:
        """Stop new dispatch and new reads. In-flight calls are not killed."""

        with self._lock:
            self._cancelled = True

    def reset_for_retry(self, invocation_id: str) -> None:
        """Reject a retry that tries to zero consumed counters."""

        _token(invocation_id, "invocation_id")
        raise ContractValidationError("counter_reset_rejected")

    def reissue_deadline(self, deadline_utc: str) -> None:
        """Reject a second deadline. The host deadline stays as issued."""

        _whole_second(deadline_utc, "deadline_utc")
        raise ContractValidationError("deadline_reissue_rejected")

    def move_timeout_backwards(self, deadline_utc: str) -> None:
        """Reject an earlier timeout. The host deadline is not moved."""

        _whole_second(deadline_utc, "deadline_utc")
        raise ContractValidationError("timeout_move_backwards_rejected")

    def adopt_journal_clock_as_deadline(
        self,
        started_at: str,
        finished_at: str,
        budget_seconds: int,
    ) -> None:
        """Reject a local fake journal interval as the host deadline."""

        del started_at, finished_at, budget_seconds
        raise ContractValidationError("journal_clock_is_not_host_enforcement")

    def record_source_observation(
        self,
        *,
        invocation_id: str,
        observed_at_utc: str,
        rows: int = 0,
        pagination_pages: int = 0,
    ) -> SourceObservationRecord:
        """Store a source clock without moving acceptance or the deadline."""

        observed = _whole_second(observed_at_utc, "source_observed_at_utc")
        row_count = _count(rows, "data_rows", allow_zero=True)
        pages = _count(pagination_pages, "pagination_pages", allow_zero=True)
        with self._lock:
            self._require_invocation(invocation_id)
            deadline = self._deadline
            accepted = self._accepted_at
            record = SourceObservationRecord(
                invocation_id=invocation_id,
                observed_at_utc=_format_utc(observed),
                rows=row_count,
                pagination_pages=pages,
            )
            self._observations.append(record)
            if self._deadline != deadline or self._accepted_at != accepted:
                raise ContractValidationError("source_clock_moved_deadline")
            return record

    def source_observations(self) -> tuple[SourceObservationRecord, ...]:
        with self._lock:
            return tuple(self._observations)

    def _register(
        self,
        *,
        step_id: str,
        invocation_id: str,
        specialist: MasterSpecialist,
        purpose: MasterPurpose,
        capability: AgentCapability,
    ) -> None:
        _token(step_id, "step_id")
        _token(invocation_id, "invocation_id")
        if not isinstance(specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        if not isinstance(capability, AgentCapability):
            raise ContractValidationError("invalid_capability")
        if capability in PROHIBITED_V1_CAPABILITIES:
            raise ContractValidationError("prohibited_capability")
        if invocation_id in self._invocations:
            raise ContractValidationError("duplicate_invocation_id")
        if len(self._invocations) >= MAX_ROUTE_STEPS:
            raise ContractValidationError("excessive_plan_size")
        if specialist is MasterSpecialist.DECISION and self._decision_invocations >= MAX_DECISION_STEPS:
            raise ContractValidationError("decision_fanout_not_permitted")
        self._invocations[invocation_id] = _Invocation(
            step_id=step_id,
            invocation_id=invocation_id,
            specialist=specialist,
            purpose=purpose,
            capability=capability,
            counters=_Counters(),
        )
        if specialist is MasterSpecialist.DECISION:
            self._decision_invocations += 1

    def _begin(
        self,
        kind: BudgetAction,
        *,
        invocation_id: str,
        action_id: str,
        count: int,
        rows: int,
    ) -> BudgetDecision:
        counted = _count(count, "count", allow_zero=False)
        row_count = _count(rows, "data_rows", allow_zero=True)
        with self._lock:
            invocation = self._require_invocation(invocation_id)
            return self._reserve(kind, invocation=invocation, action_id=action_id, count=counted, rows=row_count)

    def _reserve(
        self,
        kind: BudgetAction,
        *,
        invocation: _Invocation,
        action_id: str,
        count: int,
        rows: int,
    ) -> BudgetDecision:
        _token(action_id, "action_id")
        existing = self._actions.get(action_id)
        if existing is not None:
            if (
                existing.invocation_id != invocation.invocation_id
                or existing.kind is not kind
                or existing.count != count
                or existing.rows != rows
            ):
                raise ContractValidationError("action_id_conflict")
            return self._decision(existing)
        gate = self._gate()
        if gate is not None:
            evaluation, reasons = gate
            return self._store_refusal(
                kind=kind,
                invocation=invocation,
                action_id=action_id,
                count=count,
                rows=rows,
                evaluation=evaluation,
                reasons=reasons,
                scope=None,
            )
        if kind is BudgetAction.SPECIALIST_EXECUTION and invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
            if self._historical_window_blocks:
                return self._store_refusal(
                    kind=kind,
                    invocation=invocation,
                    action_id=action_id,
                    count=count,
                    rows=rows,
                    evaluation=BudgetEvaluation.DENIED,
                    reasons=(BudgetDenialReason.HISTORICAL_INTERVAL_ESCALATION,),
                    scope=None,
                )
        problem = self._ceiling(invocation, kind, count, rows)
        if problem is not None:
            evaluation, reasons, scope = problem
            return self._store_refusal(
                kind=kind,
                invocation=invocation,
                action_id=action_id,
                count=count,
                rows=rows,
                evaluation=evaluation,
                reasons=reasons,
                scope=scope,
            )
        consumed = self._apply(invocation, kind, count, rows)
        action = _Action(
            action_id=action_id,
            invocation_id=invocation.invocation_id,
            kind=kind,
            count=count,
            rows=rows,
            evaluation=BudgetEvaluation.EVALUATED_WITHIN_CEILING,
            disposition=StartedTaskDisposition.RESERVED,
            denial_reasons=(),
            exhausted_scope=None,
            consumed=consumed,
        )
        self._actions[action_id] = action
        return self._decision(action)

    def _store_refusal(
        self,
        *,
        kind: BudgetAction,
        invocation: _Invocation,
        action_id: str,
        count: int,
        rows: int,
        evaluation: BudgetEvaluation,
        reasons: tuple[BudgetDenialReason, ...],
        scope: ExhaustedScope | None,
    ) -> BudgetDecision:
        _token(action_id, "action_id")
        existing = self._actions.get(action_id)
        if existing is not None:
            if (
                existing.invocation_id != invocation.invocation_id
                or existing.kind is not kind
                or existing.count != count
                or existing.rows != rows
            ):
                raise ContractValidationError("action_id_conflict")
            return self._decision(existing)
        action = _Action(
            action_id=action_id,
            invocation_id=invocation.invocation_id,
            kind=kind,
            count=count,
            rows=rows,
            evaluation=evaluation,
            disposition=StartedTaskDisposition.NOT_STARTED,
            denial_reasons=reasons,
            exhausted_scope=scope,
            consumed=(),
        )
        self._actions[action_id] = action
        return self._decision(action)

    def _gate(self) -> tuple[BudgetEvaluation, tuple[BudgetDenialReason, ...]] | None:
        self._read_clock()
        if self._regression:
            return BudgetEvaluation.MONOTONIC_REGRESSION, (BudgetDenialReason.MONOTONIC_REGRESSION,)
        if self._drift:
            return BudgetEvaluation.CLOCK_DRIFT, (BudgetDenialReason.CLOCK_DRIFT,)
        if self._cancelled:
            return BudgetEvaluation.CANCELLED, (BudgetDenialReason.CANCELLED,)
        if self._deadline_reached():
            return BudgetEvaluation.DEADLINE_EXPIRED, (BudgetDenialReason.DEADLINE_EXPIRED,)
        caller: list[BudgetDenialReason] = []
        if self._caller_deadline_blocks:
            caller.append(BudgetDenialReason.CALLER_DEADLINE_NOT_AUTHORITY)
        if self._caller_budget_blocks:
            caller.append(BudgetDenialReason.CALLER_BUDGET_NOT_AUTHORITY)
        if caller:
            return BudgetEvaluation.DENIED, tuple(caller)
        return None

    def _read_clock(self) -> None:
        parsed = _read_utc(self._clock)
        mono = _read_monotonic(self._clock)
        if mono < self._last_monotonic_ns:
            self._regression = True
            return
        utc_elapsed = (parsed - self._accepted_at).total_seconds()
        mono_elapsed = (mono - self._origin_ns) / _NS_PER_SECOND
        if abs(utc_elapsed - mono_elapsed) > _DRIFT_TOLERANCE_SECONDS:
            self._drift = True
        self._last_monotonic_ns = mono
        self._last_utc = parsed

    def _deadline_reached(self) -> bool:
        if self._last_utc >= self._deadline:
            return True
        return (self._last_monotonic_ns - self._origin_ns) >= self._budget_ns

    def _ceiling(
        self,
        invocation: _Invocation,
        kind: BudgetAction,
        count: int,
        rows: int,
    ) -> tuple[BudgetEvaluation, tuple[BudgetDenialReason, ...], ExhaustedScope | None] | None:
        specialist = invocation.specialist
        executions, turns, tools = _SPECIALIST_BUDGET[specialist]
        invocation_counters = invocation.counters
        specialist_counters = self._by_specialist[specialist]
        if kind is BudgetAction.MODEL_TURN:
            if invocation_counters.model_turns + count > turns:
                return _exhausted(ExhaustedScope.INVOCATION)
            if specialist_counters.model_turns + count > turns:
                return _exhausted(ExhaustedScope.SPECIALIST)
            return None
        if kind in {BudgetAction.TOOL_CALL, BudgetAction.READ}:
            if specialist is MasterSpecialist.LIVE_OPS and count > MAX_TOOL_CALLS_PER_BATCH:
                return (
                    BudgetEvaluation.DENIED,
                    (BudgetDenialReason.BATCH_CEILING_EXCEEDED,),
                    None,
                )
            if invocation_counters.tool_calls + count > tools:
                return _exhausted(ExhaustedScope.INVOCATION)
            if specialist_counters.tool_calls + count > tools:
                return _exhausted(ExhaustedScope.SPECIALIST)
            if rows:
                ceiling = _ROW_CEILING.get(specialist)
                if ceiling is None:
                    return (
                        BudgetEvaluation.DENIED,
                        (BudgetDenialReason.ROW_CEILING_NOT_PUBLISHED,),
                        None,
                    )
                if invocation_counters.data_rows + rows > ceiling:
                    return _exhausted(ExhaustedScope.INVOCATION)
                if specialist_counters.data_rows + rows > ceiling:
                    return _exhausted(ExhaustedScope.SPECIALIST)
            return None
        if kind is BudgetAction.SPECIALIST_EXECUTION:
            if invocation_counters.executions + count > executions:
                return _exhausted(ExhaustedScope.INVOCATION)
            if specialist is MasterSpecialist.DECISION and specialist_counters.executions + count > MAX_DECISION_STEPS:
                return _exhausted(ExhaustedScope.REQUEST)
            if self._request.executions + count > MAX_ROUTE_STEPS:
                return _exhausted(ExhaustedScope.REQUEST)
            return None
        if kind is BudgetAction.RETRY:
            if invocation_counters.retries + count > PUBLISHED_INVALID_TOOL_CALL_CORRECTIONS:
                return _exhausted(ExhaustedScope.INVOCATION)
            if specialist_counters.retries + count > PUBLISHED_INVALID_TOOL_CALL_CORRECTIONS:
                return _exhausted(ExhaustedScope.SPECIALIST)
            return None
        raise ContractValidationError("invalid_budget_action")

    def _apply(
        self,
        invocation: _Invocation,
        kind: BudgetAction,
        count: int,
        rows: int,
    ) -> tuple[tuple[str, int], ...]:
        specialist_counters = self._by_specialist[invocation.specialist]
        consumed: list[tuple[str, int]] = []
        if kind is BudgetAction.MODEL_TURN:
            invocation.counters.model_turns += count
            specialist_counters.model_turns += count
            self._request.model_turns += count
            consumed.append(("model_turns", count))
        elif kind in {BudgetAction.TOOL_CALL, BudgetAction.READ}:
            invocation.counters.tool_calls += count
            specialist_counters.tool_calls += count
            self._request.tool_calls += count
            consumed.append(("tool_calls", count))
            if rows:
                invocation.counters.data_rows += rows
                specialist_counters.data_rows += rows
                self._request.data_rows += rows
                consumed.append(("data_rows", rows))
        elif kind is BudgetAction.SPECIALIST_EXECUTION:
            invocation.counters.executions += count
            specialist_counters.executions += count
            self._request.executions += count
            consumed.append(("specialist_executions", count))
        elif kind is BudgetAction.RETRY:
            invocation.counters.retries += count
            specialist_counters.retries += count
            self._request.retries += count
            consumed.append(("retries", count))
        return tuple(consumed)

    def _read_policy(
        self,
        invocation: _Invocation,
        *,
        tool_name: str,
        operation: ReadOperation,
        arguments: tuple[tuple[str, object], ...] | None,
        resource_id: str | None,
        historical_window: tuple[str, str] | None,
    ) -> BudgetDenialReason | None:
        if not isinstance(tool_name, str):
            raise ContractValidationError("invalid_tool_name")
        _token(tool_name, "tool_name")
        if not isinstance(operation, ReadOperation):
            raise ContractValidationError("invalid_operation")
        if operation is ReadOperation.SCAN:
            return BudgetDenialReason.SCAN_NOT_PERMITTED
        if operation not in {ReadOperation.GET_ITEM, ReadOperation.QUERY}:
            return BudgetDenialReason.OPERATION_NOT_ALLOWLISTED
        if tool_name not in server_read_tools(invocation.specialist):
            return BudgetDenialReason.UNSUPPORTED_TOOL
        if arguments is not None:
            if not isinstance(arguments, tuple):
                raise ContractValidationError("invalid_arguments")
            allowed = server_read_argument_names(invocation.specialist, tool_name)
            for item in arguments:
                if not isinstance(item, tuple) or len(item) != 2 or not isinstance(item[0], str):
                    raise ContractValidationError("invalid_arguments")
                if item[0] not in allowed:
                    return BudgetDenialReason.ARGUMENT_NOT_ALLOWLISTED
        if resource_id is not None:
            _token(resource_id, "resource_id")
            if (resource_id, operation) not in server_read_resources(invocation.specialist, tool_name):
                return BudgetDenialReason.RESOURCE_NOT_ALLOWLISTED
        span = _window(historical_window)
        if _window_exceeds(span) or (
            invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS and self._historical_window_blocks
        ):
            return BudgetDenialReason.HISTORICAL_INTERVAL_ESCALATION
        if span is not None and invocation.specialist is not MasterSpecialist.HISTORICAL_ANALYTICS:
            return BudgetDenialReason.HISTORICAL_INTERVAL_ESCALATION
        return None

    def _finish_locked(self, action: _Action, *, failed: bool) -> None:
        self._read_clock()
        action.finished = True
        if failed:
            action.disposition = StartedTaskDisposition.FAILED
            action.evaluation = BudgetEvaluation.DENIED
            action.denial_reasons = ()
            return
        if self._regression:
            action.disposition = StartedTaskDisposition.TERMINAL_PARTIAL
            action.evaluation = BudgetEvaluation.MONOTONIC_REGRESSION
            action.denial_reasons = (BudgetDenialReason.MONOTONIC_REGRESSION,)
            return
        if self._drift:
            action.disposition = StartedTaskDisposition.TERMINAL_PARTIAL
            action.evaluation = BudgetEvaluation.CLOCK_DRIFT
            action.denial_reasons = (BudgetDenialReason.CLOCK_DRIFT,)
            return
        if self._cancelled:
            action.disposition = StartedTaskDisposition.TERMINAL_PARTIAL
            action.evaluation = BudgetEvaluation.CANCELLED
            action.denial_reasons = (BudgetDenialReason.CANCELLED,)
            return
        if self._deadline_reached():
            action.disposition = StartedTaskDisposition.TERMINAL_PARTIAL
            action.evaluation = BudgetEvaluation.DEADLINE_EXPIRED
            action.denial_reasons = (BudgetDenialReason.DEADLINE_EXPIRED,)
            return
        action.disposition = StartedTaskDisposition.COMPLETED_BOUNDED
        action.evaluation = BudgetEvaluation.EVALUATED_WITHIN_CEILING
        action.denial_reasons = ()

    def _require_downstream_timeout(self, downstream_timeout_s: int) -> None:
        self._read_clock()
        remaining_ns = self._budget_ns - (self._last_monotonic_ns - self._origin_ns)
        remaining_seconds = remaining_ns // _NS_PER_SECOND
        if remaining_seconds < 1 or downstream_timeout_s > remaining_seconds:
            raise ContractValidationError("downstream_timeout_exceeds_deadline")

    def _require_invocation(self, invocation_id: str) -> _Invocation:
        _token(invocation_id, "invocation_id")
        invocation = self._invocations.get(invocation_id)
        if invocation is None:
            raise ContractValidationError("unknown_invocation")
        return invocation

    def _require_action(self, action_id: str) -> _Action:
        _token(action_id, "action_id")
        action = self._actions.get(action_id)
        if action is None:
            raise ContractValidationError("unknown_action")
        return action

    def _decision(self, action: _Action) -> BudgetDecision:
        invocation = self._invocations[action.invocation_id]
        envelope = self._envelope
        observed = next(
            (
                record.observed_at_utc
                for record in reversed(self._observations)
                if record.invocation_id == invocation.invocation_id
            ),
            None,
        )
        return BudgetDecision(
            evaluation=action.evaluation,
            disposition=action.disposition,
            denial_reasons=action.denial_reasons,
            master_request_id=envelope.master_request_id,
            planning_session_id=envelope.planning_session_id,
            step_id=invocation.step_id,
            invocation_id=invocation.invocation_id,
            specialist=invocation.specialist,
            purpose=invocation.purpose,
            capability=invocation.capability,
            correlation_id=envelope.correlation_id,
            idempotency_key=envelope.idempotency_key,
            action_id=action.action_id,
            accepted_at_utc=self.accepted_at_utc,
            deadline_utc=self.deadline_utc,
            elapsed_monotonic_ns=self._last_monotonic_ns - self._origin_ns,
            source_observed_at_utc=observed,
            exhausted_scope=action.exhausted_scope,
            descriptive_allow=_descriptive_allow(action.evaluation, action.disposition),
            host_issued_deadline=True,
            consumed_by_this_action=action.consumed,
            principal_ref=envelope.principal_ref,
            policy_revision=envelope.policy_revision,
            resource_scope=envelope.resource_scope,
            region_scope=envelope.region_scope,
            decision_target_scope=envelope.decision_target_scope,
        )


def _exhausted(
    scope: ExhaustedScope,
) -> tuple[BudgetEvaluation, tuple[BudgetDenialReason, ...], ExhaustedScope]:
    return BudgetEvaluation.EXHAUSTED, (BudgetDenialReason.BUDGET_EXHAUSTED,), scope


def _stopped_before_start(evaluation: BudgetEvaluation) -> StartedTaskDisposition:
    if evaluation is BudgetEvaluation.CANCELLED:
        return StartedTaskDisposition.CANCELLED_BEFORE_START
    return StartedTaskDisposition.NOT_STARTED


def _read_utc(clock: object) -> datetime:
    reader = getattr(clock, "now_utc", None)
    if not callable(reader):
        raise ContractValidationError("invalid_host_clock")
    return _whole_second(reader(), "host_clock")


def _read_monotonic(clock: object) -> int:
    reader = getattr(clock, "monotonic_ns", None)
    if not callable(reader):
        raise ContractValidationError("invalid_host_clock")
    value = reader()
    if type(value) is not int or value < 0:
        raise ContractValidationError("invalid_monotonic_clock")
    return value


def open_private_offline_harness(
    seal: object,
    *,
    clock: object,
    remaining_runtime: object,
    envelope: RequestBudgetEnvelope,
) -> RuntimeBudgetSession:
    """Open one offline session. Production input cannot pass the seal check.

    The session evaluates ceilings and can run a local callable. It does not
    enable the deployed host and does not issue a grant, principal, or ledger.
    """

    _closed_production()
    if seal is not _OFFLINE_HARNESS_SEAL:
        raise ContractValidationError("offline_harness_seal_required")
    return RuntimeBudgetSession(envelope, clock, remaining_runtime)


__all__ = [
    "CLOCK_IS_PRODUCTION_PROOF",
    "DEPLOYED_HOST_ENABLED",
    "EMPTY_ROWS_CREATE_VERIFIED_ZERO",
    "LOCAL_FAKE_JOURNAL_IS_AWS_ENFORCEMENT",
    "MA3_ADAPTER_SEAM",
    "MASTER_RUNTIME_BUDGET_SCHEMA_VERSION",
    "PRODUCTION_GRANT_AVAILABLE",
    "PRODUCTION_LEDGER_AVAILABLE",
    "PRODUCTION_PRINCIPAL_AVAILABLE",
    "PUBLISHED_INVALID_TOOL_CALL_CORRECTIONS",
    "READ_EXECUTOR_SEAM",
    "SOURCE_AUTHENTICATION_AVAILABLE",
    "BudgetAction",
    "BudgetDecision",
    "BudgetDenialReason",
    "BudgetEvaluation",
    "BudgetOperationResult",
    "BudgetUsage",
    "DeployedHostBudget",
    "ExhaustedScope",
    "HostEnforcementRefusal",
    "RequestBudgetEnvelope",
    "RuntimeBudgetIntegrationSeam",
    "RuntimeBudgetSession",
    "SourceObservationRecord",
    "SpecialistCeilings",
    "StartedTaskDisposition",
    "bind_deployed_host_budget",
    "integration_seam",
    "journal_observation_is_not_host_enforcement",
    "offline_harness_seal",
    "open_private_offline_harness",
    "promote_budget_decision_to_production_grant",
    "published_ceilings",
]
