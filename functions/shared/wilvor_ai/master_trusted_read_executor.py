"""Host-side restricted read execution. Not a production grant.

This module is the last boundary before a read-only adapter runs. A tool
name on a catalog, a ``WITHIN_ALLOWLIST`` policy check, a caller callable,
or a caller-built ``ToolResult`` does not execute and does not authenticate
aviation evidence.

Trust for a positive local run comes only from objects this process sealed
through ``bind_nonproduction_test_invocation`` and
``bind_nonproduction_read_registry``. Those binders are the offline harness.
They are not activated by an environment variable, a boolean, a serialized
dict, or a policy-check result. ``issue_production_read_invocation`` and
``open_production_executor`` always fail. Flipping the closed constants to
true also fails closed; it does not open a success path.

Two effects stay distinct. ``OPERATIONAL_READ`` is a keyed ``GET_ITEM`` or
``QUERY`` through the registered adapter. ``INTERNAL_LEDGER_WRITE`` records
idempotency consumption for a later ledger. The ledger effect is not an
operational table write, a DynamoDB mutation, or an aviation action.

A completed ``ReadReceiptCandidate`` is a raw observation for Agent 10.
``raw_read_observation`` copies the descriptive fields. Neither object is
source-authenticated evidence. Empty pages, local rows, and reported source
versions do not mint a verified-zero claim or a geographic impact.

Integration (modules are not imported; they are not merged in this wave):

Agent 4, ``wilvor_ai.master_execution_grants``
    Must return a process-local grant bound to principal_ref,
    planning_session_id, step_id, invocation_id, master_request_id,
    specialist, purpose, capability, tool_name, tool_call_id, resource_id,
    ReadOperation, policy_revision, region codes, historical window,
    decision aircraft id, decision recommendation id, accepted_at_utc,
    deadline_utc, idempotency_key, grant_nonce, and consumed counters
    (executions_used, model_turns_used, tool_calls_used). A serialized grant,
    a ``MasterReadResourceCheck``, and a string token are not that object.
    Until a reviewed host seals Agent 4's grant into
    ``HostReadInvocation``, production issuance stays closed. This module
    will not duck-type an unmerged grant class.

Agent 6, ``wilvor_ai.master_runtime_budget``
    Must own the host clock, absolute deadline, cooperative cancellation,
    and the consumed counters above. Ceilings are
    ``server_read_budget`` only. This executor rechecks those ceilings, the
    deadline, and cancellation immediately before the adapter call and again
    after it returns. It cannot interrupt a blocked socket. Elapsed
    ``timeout_budget_seconds`` is separate from ``accepted_at_utc`` /
    ``deadline_utc`` and from any source-reported observation time.

Agent 7, ``wilvor_ai.master_authorized_scope``
    Must supply the effective US region-code set, the approved historical
    window (<= 7 days), and the approved Decision aircraft or recommendation
    id. A location hint, a caller scope object, and a descriptive allow are
    not that scope. This executor copies only the sealed host fields into
    executed arguments and the receipt. Requested arguments stay unchanged
    when a limit is injected.

Agent 10, ``wilvor_ai.master_authenticated_read_receipt``
    May accept ``ReadReceiptCandidate`` only when ``type`` is this class and
    the process seal matches. ``raw_read_observation`` is a dict and is not
    host origin. ``authority_kind`` ``NONPRODUCTION_TEST_ADAPTER`` with
    ``source_authenticity`` ``NOT_ESTABLISHED`` must not become an MA4
    receipt. There is no ToolResult field. ``next_page_token`` is not
    retained. Requested and executed arguments are separate. Application
    acceptance/deadline clocks are separate from execution-clock samples and
    from ``source_reported_observed_at_utc``.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    AgentCapability,
    ContractValidationError,
    _validate_bounded_text,
    _validate_utc,
)
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_read_resource_guard import (
    HISTORICAL_LIST_MAX,
    HISTORICAL_LIST_MIN,
    MasterReadDenialReason,
    MasterReadResourceProposal,
    ReadOperation,
    ServerCancellation,
    ServerReadEvaluation,
    check_master_read_resource,
    seal_ingress_principal,
    seal_population_scope,
    server_read_argument_names,
    server_read_budget,
    server_read_resources,
    server_read_tools,
)
from wilvor_ai.master_routing import MAX_HISTORICAL_WINDOW


MASTER_TRUSTED_READ_EXECUTOR_SCHEMA_VERSION = "wilvor.ai.master_trusted_read_executor.v1"
RAW_OBSERVATION_KIND = "RAW_READ_OBSERVATION"
LEDGER_RESOURCE_ID = "ledger.invocation"
LEDGER_OPERATION = "CONSUME"
SOURCE_CLOCK_NOT_ESTABLISHED = "NOT_ESTABLISHED"
GEOGRAPHIC_COVERAGE_NOT_ESTABLISHED = "NOT_ESTABLISHED"

# Closed. A true value is not a success switch; execute() rejects it.
PRODUCTION_READ_EXECUTOR_ENABLED = False
PRODUCTION_GRANT_ISSUER_INTEGRATED = False
APPROVED_SOURCE_ADAPTER_INTEGRATED = False
SOURCE_AUTHENTICATED_EVIDENCE_AVAILABLE = False
FAKE_RESULT_IS_AVIATION_EVIDENCE = False
WITHIN_ALLOWLIST_IS_EXECUTION_GRANT = False
EMPTY_PAGE_CREATES_VERIFIED_ZERO = False
ENVIRONMENT_SELECTS_TEST_AUTHORITY = False

_LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
_HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
_RISK = AgentCapability.EXPLAIN_EXISTING_RISK
_RECOMMENDATIONS = AgentCapability.EXPLAIN_EXISTING_RECOMMENDATIONS

_DECISION_CAPABILITIES = {
    "get_current_decision_context": frozenset({_RISK, _RECOMMENDATIONS}),
    "get_current_risk_evidence": frozenset({_RISK}),
    "get_current_recommendation": frozenset({_RECOMMENDATIONS}),
    "get_persisted_airport_candidate_evidence": frozenset({_RECOMMENDATIONS}),
}
_TOOL_CAPABILITIES = {
    MasterSpecialist.LIVE_OPS: {
        name: frozenset({_LIVE}) for name in server_read_tools(MasterSpecialist.LIVE_OPS)
    },
    MasterSpecialist.HISTORICAL_ANALYTICS: {
        name: frozenset({_HISTORY})
        for name in server_read_tools(MasterSpecialist.HISTORICAL_ANALYTICS)
    },
    MasterSpecialist.DECISION: dict(_DECISION_CAPABILITIES),
}

_AWS_METHODS = frozenset(
    {
        "scan",
        "scan_all",
        "put_item",
        "delete_item",
        "update_item",
        "batch_write_item",
        "batch_get_item",
        "execute_statement",
        "execute_transaction",
        "transact_write_items",
        "start_query_execution",
        "get_query_execution",
        "get_query_results",
        "transact_get_items",
    }
)
_WILDCARDS = frozenset({"*", "all", "all_tables", "any", "unrestricted", "table", "tables"})
_SQL_RE = re.compile(
    r"(?i)(?:^|[^A-Za-z0-9_])(select|insert|update|delete|drop|alter|union)(?:[^A-Za-z0-9_]|$)"
)
_ERROR_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROCESS_KEY = secrets.token_bytes(32)


class _HostSeal:
    """Process-local seal. Caller strings, dicts, and booleans are not this object."""


_SEAL = _HostSeal()


class HostAuthorityKind(str, Enum):
    """Where an invocation object came from. Production is not a member that can be sealed."""

    NONPRODUCTION_TEST_ADAPTER = "NONPRODUCTION_TEST_ADAPTER"
    ABSENT = "ABSENT"


class EffectPlane(str, Enum):
    """Operational keyed reads and internal consumption records are different planes."""

    OPERATIONAL_READ = "OPERATIONAL_READ"
    INTERNAL_LEDGER_WRITE = "INTERNAL_LEDGER_WRITE"


class ReadExecutionOutcome(str, Enum):
    """How this boundary ended. Not an evidence-admission status."""

    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    EXCEPTION = "EXCEPTION"
    QUERY_LIMIT_EXCEEDED = "QUERY_LIMIT_EXCEEDED"


class ReadBoundaryDenial(str, Enum):
    """Why the adapter did not return an admissible observation."""

    HOST_AUTHORITY_ABSENT = "HOST_AUTHORITY_ABSENT"
    PRODUCTION_GRANT_NOT_INTEGRATED = "PRODUCTION_GRANT_NOT_INTEGRATED"
    APPROVED_SOURCE_ADAPTER_ABSENT = "APPROVED_SOURCE_ADAPTER_ABSENT"
    CANCELLATION_AUTHORITY_ABSENT = "CANCELLATION_AUTHORITY_ABSENT"
    CALLER_CALLABLE_REJECTED = "CALLER_CALLABLE_REJECTED"
    CALLER_SCOPE_NOT_AUTHORITY = "CALLER_SCOPE_NOT_AUTHORITY"
    CALLER_TOOL_RESULT_NOT_A_READ = "CALLER_TOOL_RESULT_NOT_A_READ"
    CALLER_BUDGET_NOT_AUTHORITY = "CALLER_BUDGET_NOT_AUTHORITY"
    CALLER_DEADLINE_NOT_AUTHORITY = "CALLER_DEADLINE_NOT_AUTHORITY"
    POLICY_CHECK_NOT_EXECUTION_GRANT = "POLICY_CHECK_NOT_EXECUTION_GRANT"
    POLICY_EVALUATION_DENIED = "POLICY_EVALUATION_DENIED"
    SPECIALIST_MISMATCH = "SPECIALIST_MISMATCH"
    PURPOSE_MISMATCH = "PURPOSE_MISMATCH"
    TOOL_MISMATCH = "TOOL_MISMATCH"
    CAPABILITY_MISMATCH = "CAPABILITY_MISMATCH"
    RESOURCE_MISMATCH = "RESOURCE_MISMATCH"
    OPERATION_MISMATCH = "OPERATION_MISMATCH"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    CORRELATION_MISMATCH = "CORRELATION_MISMATCH"
    SCAN_NOT_PERMITTED = "SCAN_NOT_PERMITTED"
    WILDCARD_RESOURCE = "WILDCARD_RESOURCE"
    ARBITRARY_TABLE_ARN = "ARBITRARY_TABLE_ARN"
    ARBITRARY_SQL = "ARBITRARY_SQL"
    ARBITRARY_AWS_METHOD = "ARBITRARY_AWS_METHOD"
    ARGUMENT_NOT_ALLOWLISTED = "ARGUMENT_NOT_ALLOWLISTED"
    ARGUMENT_INVALID = "ARGUMENT_INVALID"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"
    QUERY_LIMIT_EXCEEDED = "QUERY_LIMIT_EXCEEDED"
    ADAPTER_EXCEPTION = "ADAPTER_EXCEPTION"
    ADAPTER_RETURN_NOT_OBSERVATION = "ADAPTER_RETURN_NOT_OBSERVATION"
    DUPLICATE_CONSUMPTION = "DUPLICATE_CONSUMPTION"
    HISTORICAL_WINDOW_ABSENT = "HISTORICAL_WINDOW_ABSENT"
    HISTORICAL_WINDOW_OUTSIDE_HOST = "HISTORICAL_WINDOW_OUTSIDE_HOST"
    DECISION_TARGET_ABSENT = "DECISION_TARGET_ABSENT"
    DECISION_TARGET_MISMATCH = "DECISION_TARGET_MISMATCH"


_DENIAL_ORDER = tuple(ReadBoundaryDenial)
_INVOKED_OUTCOMES = frozenset(
    {
        ReadExecutionOutcome.COMPLETED,
        ReadExecutionOutcome.TIMEOUT,
        ReadExecutionOutcome.CANCELLED,
        ReadExecutionOutcome.EXCEPTION,
        ReadExecutionOutcome.QUERY_LIMIT_EXCEEDED,
    }
)


def _assert_closed_surface() -> None:
    closed = (
        PRODUCTION_READ_EXECUTOR_ENABLED,
        PRODUCTION_GRANT_ISSUER_INTEGRATED,
        APPROVED_SOURCE_ADAPTER_INTEGRATED,
        SOURCE_AUTHENTICATED_EVIDENCE_AVAILABLE,
        FAKE_RESULT_IS_AVIATION_EVIDENCE,
        WITHIN_ALLOWLIST_IS_EXECUTION_GRANT,
        EMPTY_PAGE_CREATES_VERIFIED_ZERO,
        ENVIRONMENT_SELECTS_TEST_AUTHORITY,
    )
    if any(flag is not False for flag in closed):
        raise ContractValidationError("production_read_executor_not_enabled")
    if MAX_HISTORICAL_WINDOW.days != 7 or HISTORICAL_LIST_MAX < HISTORICAL_LIST_MIN:
        raise ContractValidationError("invalid_server_allowlist")
    for specialist, tools in _TOOL_CAPABILITIES.items():
        if set(tools) != set(server_read_tools(specialist)):
            raise ContractValidationError("read_executor_catalog_drift")
        for tool_name, capabilities in tools.items():
            if not capabilities:
                raise ContractValidationError("read_executor_catalog_drift")
            for _resource, operation in server_read_resources(specialist, tool_name):
                if operation is ReadOperation.SCAN:
                    raise ContractValidationError("scan_not_permitted")
    if set(_DECISION_CAPABILITIES) != set(server_read_tools(MasterSpecialist.DECISION)):
        raise ContractValidationError("read_executor_catalog_drift")


_assert_closed_surface()


def _require_closed() -> None:
    if (
        PRODUCTION_READ_EXECUTOR_ENABLED is not False
        or PRODUCTION_GRANT_ISSUER_INTEGRATED is not False
        or APPROVED_SOURCE_ADAPTER_INTEGRATED is not False
    ):
        raise ContractValidationError("production_read_executor_not_enabled")


def _fingerprint(material: tuple[object, ...]) -> str:
    return hmac.new(_PROCESS_KEY, repr(material).encode("utf-8"), hashlib.sha256).hexdigest()


def _fingerprint_matches(fingerprint: object, material: tuple[object, ...]) -> bool:
    if not isinstance(fingerprint, str):
        return False
    try:
        return hmac.compare_digest(fingerprint, _fingerprint(material))
    except TypeError:
        return False


def _token(value: object, field_name: str) -> str:
    errors = _validate_bounded_text(value, field_name, max_length=PROVENANCE_TOKEN_MAX_LENGTH)
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
    if not isinstance(value, str) or len(value) != 20 or not value.endswith("Z") or value[10] != "T":
        raise ContractValidationError(f"invalid_{field_name}")
    errors = _validate_utc(value, field_name)
    if errors:
        raise ContractValidationError(errors)
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.microsecond != 0:
        raise ContractValidationError(f"invalid_{field_name}")
    return parsed


def _count(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _enum_member(value: object, enum_type: type[Enum], field_name: str) -> Enum:
    if type(value) is not enum_type:
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _wildcard_text(value: str) -> bool:
    return "*" in value or value.casefold() in _WILDCARDS


def _arn_text(value: str) -> bool:
    folded = value.casefold()
    return folded.startswith("arn:") or folded.startswith("dynamodb:") or ":table/" in folded


def _sql_text(value: str) -> bool:
    return _SQL_RE.search(value) is not None


def _aws_method_text(value: str) -> bool:
    return value.casefold() in _AWS_METHODS


def _text_denials(value: str) -> tuple[ReadBoundaryDenial, ...]:
    reasons: list[ReadBoundaryDenial] = []
    if _wildcard_text(value):
        reasons.append(ReadBoundaryDenial.WILDCARD_RESOURCE)
    if _arn_text(value):
        reasons.append(ReadBoundaryDenial.ARBITRARY_TABLE_ARN)
    if _sql_text(value):
        reasons.append(ReadBoundaryDenial.ARBITRARY_SQL)
    if _aws_method_text(value):
        reasons.append(ReadBoundaryDenial.ARBITRARY_AWS_METHOD)
    return tuple(reasons)


def _walk_text_denials(value: object) -> tuple[ReadBoundaryDenial, ...]:
    if isinstance(value, str):
        return _text_denials(value)
    if isinstance(value, tuple):
        reasons: list[ReadBoundaryDenial] = []
        for item in value:
            reasons.extend(_walk_text_denials(item))
        return tuple(reasons)
    return ()


def _unique(reasons: list[ReadBoundaryDenial]) -> tuple[ReadBoundaryDenial, ...]:
    present = set(reasons)
    return tuple(reason for reason in _DENIAL_ORDER if reason in present)


def _argument_names(specialist: MasterSpecialist, tool_name: str) -> frozenset[str] | None:
    try:
        return server_read_argument_names(specialist, tool_name)
    except ContractValidationError:
        return None


def _resource_pairs(
    specialist: MasterSpecialist,
    tool_name: str,
) -> frozenset[tuple[str, ReadOperation]] | None:
    try:
        return server_read_resources(specialist, tool_name)
    except ContractValidationError:
        return None


@dataclass(frozen=True)
class ExecutionEffect:
    """One boundary effect. The ledger plane cannot carry an operational mutation."""

    plane: EffectPlane
    operation: str
    resource_id: str
    operational_data_mutation: bool = False

    def __post_init__(self) -> None:
        if type(self.plane) is not EffectPlane:
            raise ContractValidationError("invalid_effect_plane")
        if self.operational_data_mutation is not False:
            raise ContractValidationError("operational_mutation_not_permitted")
        _token(self.resource_id, "resource_id")
        if self.plane is EffectPlane.OPERATIONAL_READ:
            if self.operation not in {ReadOperation.GET_ITEM.value, ReadOperation.QUERY.value}:
                raise ContractValidationError("invalid_operational_read")
            if self.resource_id == LEDGER_RESOURCE_ID:
                raise ContractValidationError("ledger_write_is_not_operational_read")
            return
        if self.operation != LEDGER_OPERATION or self.resource_id != LEDGER_RESOURCE_ID:
            raise ContractValidationError("invalid_internal_ledger_write")


@dataclass(frozen=True)
class HostReadInvocation:
    """Sealed nonproduction invocation. A copy, dict, or replaced field is not authority."""

    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    tool_name: str
    tool_call_id: str
    resource_id: str
    operation: ReadOperation
    principal_ref: str
    policy_revision: str
    region_codes: tuple[str, ...]
    historical_start_utc: str | None
    historical_end_utc: str | None
    decision_aircraft_id: str | None
    decision_recommendation_id: str | None
    accepted_at_utc: str
    deadline_utc: str
    idempotency_key: str
    grant_nonce: str
    executions_used: int
    model_turns_used: int
    tool_calls_used: int
    timeout_budget_seconds: int
    correlation_id: str | None = None
    authority_kind: HostAuthorityKind = HostAuthorityKind.NONPRODUCTION_TEST_ADAPTER
    _fingerprint: str = field(default="", repr=False, compare=False)
    _seal: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _SEAL or self.authority_kind is not HostAuthorityKind.NONPRODUCTION_TEST_ADAPTER:
            raise ContractValidationError("host_authority_absent")
        _validate_host_fields(self)
        if not _fingerprint_matches(self._fingerprint, _host_material(self)):
            raise ContractValidationError("host_invocation_not_sealed")

    def __bool__(self) -> bool:
        raise ContractValidationError("host_invocation_is_not_a_grant")


def _host_material(host: HostReadInvocation) -> tuple[object, ...]:
    return (
        host.authority_kind.value,
        host.master_request_id,
        host.planning_session_id,
        host.step_id,
        host.invocation_id,
        host.specialist.value,
        host.purpose.value,
        host.capability.value,
        host.tool_name,
        host.tool_call_id,
        host.resource_id,
        host.operation.value,
        host.principal_ref,
        host.policy_revision,
        host.region_codes,
        host.historical_start_utc,
        host.historical_end_utc,
        host.decision_aircraft_id,
        host.decision_recommendation_id,
        host.accepted_at_utc,
        host.deadline_utc,
        host.idempotency_key,
        host.grant_nonce,
        host.executions_used,
        host.model_turns_used,
        host.tool_calls_used,
        host.timeout_budget_seconds,
        host.correlation_id,
    )


def _validate_host_fields(host: HostReadInvocation) -> None:
    _token(host.master_request_id, "master_request_id")
    _token(host.planning_session_id, "planning_session_id")
    _token(host.step_id, "step_id")
    _token(host.invocation_id, "invocation_id")
    _enum_member(host.specialist, MasterSpecialist, "specialist")
    _enum_member(host.purpose, MasterPurpose, "purpose")
    _enum_member(host.capability, AgentCapability, "capability")
    _token(host.tool_name, "tool_name")
    _token(host.tool_call_id, "tool_call_id")
    _token(host.resource_id, "resource_id")
    _enum_member(host.operation, ReadOperation, "operation")
    _token(host.principal_ref, "principal_ref")
    _token(host.policy_revision, "policy_revision")
    _token(host.idempotency_key, "idempotency_key")
    _token(host.grant_nonce, "grant_nonce")
    _optional_token(host.correlation_id, "correlation_id")
    _optional_token(host.decision_aircraft_id, "decision_aircraft_id")
    _optional_token(host.decision_recommendation_id, "decision_recommendation_id")
    _count(host.executions_used, "executions_used")
    _count(host.model_turns_used, "model_turns_used")
    _count(host.tool_calls_used, "tool_calls_used")
    if isinstance(host.timeout_budget_seconds, bool) or type(host.timeout_budget_seconds) is not int:
        raise ContractValidationError("invalid_timeout_budget_seconds")
    if host.timeout_budget_seconds < 1:
        raise ContractValidationError("invalid_timeout_budget_seconds")
    accepted = _whole_second(host.accepted_at_utc, "accepted_at_utc")
    deadline = _whole_second(host.deadline_utc, "deadline_utc")
    if deadline <= accepted:
        raise ContractValidationError("invalid_deadline_utc")
    if not isinstance(host.region_codes, tuple) or any(type(code) is not str for code in host.region_codes):
        raise ContractValidationError("invalid_region_scope")
    if host.region_codes and tuple(sorted(host.region_codes)) != host.region_codes:
        raise ContractValidationError("invalid_region_scope")
    start = host.historical_start_utc
    end = host.historical_end_utc
    if (start is None) is not (end is None):
        raise ContractValidationError("invalid_historical_window")
    if start is not None and end is not None:
        parsed_start = _whole_second(start, "historical_start_utc")
        parsed_end = _whole_second(end, "historical_end_utc")
        if parsed_start >= parsed_end or parsed_end - parsed_start > MAX_HISTORICAL_WINDOW:
            raise ContractValidationError("historical_interval_escalation")
    if host.operation is ReadOperation.SCAN:
        raise ContractValidationError("scan_not_permitted")
    for denial in _text_denials(host.resource_id):
        if denial is ReadBoundaryDenial.WILDCARD_RESOURCE:
            raise ContractValidationError("wildcard_resource")
        if denial is ReadBoundaryDenial.ARBITRARY_TABLE_ARN:
            raise ContractValidationError("arbitrary_table_arn")
        if denial is ReadBoundaryDenial.ARBITRARY_SQL:
            raise ContractValidationError("arbitrary_sql")
        if denial is ReadBoundaryDenial.ARBITRARY_AWS_METHOD:
            raise ContractValidationError("arbitrary_aws_method")
    names = _TOOL_CAPABILITIES.get(host.specialist, {})
    if host.tool_name not in names or host.capability not in names[host.tool_name]:
        raise ContractValidationError("capability_specialist_mismatch")
    pairs = _resource_pairs(host.specialist, host.tool_name)
    if pairs is None or (host.resource_id, host.operation) not in pairs:
        raise ContractValidationError("resource_not_allowlisted")


def _host_is_sealed(host: object) -> bool:
    if type(host) is not HostReadInvocation:
        return False
    if host._seal is not _SEAL or host.authority_kind is not HostAuthorityKind.NONPRODUCTION_TEST_ADAPTER:
        return False
    return _fingerprint_matches(host._fingerprint, _host_material(host))


class NonproductionHostClock:
    """Injected harness clock. Samples are not production time proof."""

    __slots__ = ("_now", "_seal")

    def __init__(self, now_utc: str, _seal: object) -> None:
        _require_closed()
        if _seal is not _SEAL:
            raise ContractValidationError("host_clock_not_sealed")
        _whole_second(now_utc, "now_utc")
        self._now = now_utc
        self._seal = _seal

    def now_utc(self) -> str:
        if self._seal is not _SEAL:
            raise ContractValidationError("host_clock_not_sealed")
        return self._now

    def advance_to(self, instant: str) -> None:
        if self._seal is not _SEAL:
            raise ContractValidationError("host_clock_not_sealed")
        parsed = _whole_second(instant, "now_utc")
        if parsed < _whole_second(self._now, "now_utc"):
            raise ContractValidationError("host_clock_moved_backward")
        self._now = instant


class HostCancellation:
    """Cooperative cancel flag sealed for this process. A caller boolean is not this object."""

    __slots__ = ("_flag", "_lock", "_seal")

    def __init__(self, _seal: object) -> None:
        _require_closed()
        if _seal is not _SEAL:
            raise ContractValidationError("cancellation_not_host_issued")
        self._flag = False
        self._lock = threading.Lock()
        self._seal = _seal

    def cancel(self) -> None:
        if self._seal is not _SEAL:
            raise ContractValidationError("cancellation_not_host_issued")
        with self._lock:
            self._flag = True

    def is_cancelled(self) -> bool:
        if self._seal is not _SEAL:
            raise ContractValidationError("cancellation_not_host_issued")
        with self._lock:
            return self._flag


@dataclass(frozen=True)
class NonproductionAdapterSpec:
    """Registration input for the harness binder. Not an argument to execute."""

    specialist: MasterSpecialist
    tool_name: str
    capability: AgentCapability
    resource_id: str
    operation: ReadOperation
    read: object

    def __post_init__(self) -> None:
        _enum_member(self.specialist, MasterSpecialist, "specialist")
        _enum_member(self.capability, AgentCapability, "capability")
        _enum_member(self.operation, ReadOperation, "operation")
        _token(self.tool_name, "tool_name")
        _token(self.resource_id, "resource_id")
        if not callable(self.read):
            raise ContractValidationError("invalid_read_adapter")


class _BoundAdapter:
    __slots__ = ("adapter_id", "key", "_read", "_seal")

    def __init__(
        self,
        adapter_id: str,
        key: tuple[MasterSpecialist, str, AgentCapability, str, ReadOperation],
        read: object,
        _seal: object,
    ) -> None:
        if _seal is not _SEAL or not callable(read):
            raise ContractValidationError("invalid_read_adapter")
        self.adapter_id = adapter_id
        self.key = key
        self._read = read
        self._seal = _seal

    def read(self, executed_arguments: dict[str, object], cancellation: HostCancellation) -> object:
        if self._seal is not _SEAL:
            raise ContractValidationError("approved_source_adapter_absent")
        return self._read(dict(executed_arguments), cancellation)


class NonproductionReadRegistry:
    """Server-owned static registry of nonproduction adapters. Not a production source registry."""

    __slots__ = ("_by_key", "_seal", "_frozen")

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_frozen", False):
            raise ContractValidationError("registry_frozen")
        object.__setattr__(self, name, value)

    def __init__(self, by_key: MappingProxyType[tuple[object, ...], _BoundAdapter], _seal: object) -> None:
        _require_closed()
        if _seal is not _SEAL or type(by_key) is not MappingProxyType:
            raise ContractValidationError("approved_source_adapter_absent")
        self._by_key = by_key
        self._seal = _seal
        self._frozen = True

    def __bool__(self) -> bool:
        raise ContractValidationError("adapter_registry_is_not_a_grant")

    def resolve(
        self,
        key: tuple[MasterSpecialist, str, AgentCapability, str, ReadOperation],
    ) -> _BoundAdapter | None:
        if self._seal is not _SEAL:
            raise ContractValidationError("approved_source_adapter_absent")
        binding = self._by_key.get(key)
        if binding is None or binding._seal is not _SEAL:
            return None
        return binding


@dataclass(frozen=True)
class UntrustedReadRequest:
    """Caller proposal. Embedded scopes, callables, and tool results are not authority."""

    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    tool_name: str
    tool_call_id: str
    resource_id: str
    operation: ReadOperation
    requested_arguments: tuple[tuple[str, object], ...]
    correlation_id: str | None = None
    caller_scope: object = None
    caller_policy_decision: str | None = None
    caller_authorized: bool | None = None
    caller_deadline_utc: str | None = None
    caller_max_tool_calls: int | None = None
    caller_callable: object = None
    caller_tool_result: object = None
    principal_label: str | None = None

    def __post_init__(self) -> None:
        _token(self.master_request_id, "master_request_id")
        _token(self.planning_session_id, "planning_session_id")
        _token(self.step_id, "step_id")
        _token(self.invocation_id, "invocation_id")
        _enum_member(self.specialist, MasterSpecialist, "specialist")
        _enum_member(self.purpose, MasterPurpose, "purpose")
        _enum_member(self.capability, AgentCapability, "capability")
        _token(self.tool_name, "tool_name")
        _token(self.tool_call_id, "tool_call_id")
        _token(self.resource_id, "resource_id")
        _enum_member(self.operation, ReadOperation, "operation")
        _optional_token(self.correlation_id, "correlation_id")
        if self.principal_label is not None:
            _token(self.principal_label, "principal_label")
        if self.caller_deadline_utc is not None:
            _token(self.caller_deadline_utc, "caller_deadline_utc")
        if self.caller_policy_decision is not None:
            _token(self.caller_policy_decision, "caller_policy_decision")
        if self.caller_authorized is not None and type(self.caller_authorized) is not bool:
            raise ContractValidationError("invalid_caller_authorized")
        if self.caller_max_tool_calls is not None:
            _count(self.caller_max_tool_calls, "caller_max_tool_calls")
        if not isinstance(self.requested_arguments, tuple):
            raise ContractValidationError("invalid_arguments")
        seen: set[str] = set()
        for item in self.requested_arguments:
            if not isinstance(item, tuple) or len(item) != 2 or not isinstance(item[0], str):
                raise ContractValidationError("invalid_arguments")
            if item[0] in seen:
                raise ContractValidationError("duplicate_argument")
            seen.add(item[0])


@dataclass(frozen=True)
class NonproductionReadPage:
    """Return value of a harness adapter. Not aviation evidence and not a ToolResult."""

    record_ids: tuple[str, ...] = ()
    source_name: str | None = None
    source_version: str | None = None
    query_id: str | None = None
    page_index: int = 0
    has_more: bool = False
    truncated: bool = False
    source_observed_at_utc: str | None = None
    next_page_token: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.record_ids, tuple) or any(type(item) is not str for item in self.record_ids):
            raise ContractValidationError("invalid_read_page")
        if type(self.page_index) is not int or isinstance(self.page_index, bool) or self.page_index < 0:
            raise ContractValidationError("invalid_read_page")
        if type(self.has_more) is not bool or type(self.truncated) is not bool:
            raise ContractValidationError("invalid_read_page")
        if self.next_page_token is not None and type(self.next_page_token) is not str:
            raise ContractValidationError("invalid_page_token")


@dataclass(frozen=True)
class ReadReceiptCandidate:
    """In-process observation for Agent 10. ``__bool__`` is not an execution gate."""

    outcome: ReadExecutionOutcome
    authority_kind: HostAuthorityKind
    denial_reasons: tuple[ReadBoundaryDenial, ...]
    guard_denial_reasons: tuple[MasterReadDenialReason, ...]
    adapter_invoked: bool
    local_test_adapter_resolved: bool
    policy_check_supplied: bool
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    operation: ReadOperation
    requested_arguments: tuple[tuple[str, object], ...]
    executed_arguments: tuple[tuple[str, object], ...]
    scope_injected_argument_names: tuple[str, ...]
    effective_region_codes: tuple[str, ...]
    effects: tuple[ExecutionEffect, ...]
    record_ids: tuple[str, ...]
    schema_version: str = MASTER_TRUSTED_READ_EXECUTOR_SCHEMA_VERSION
    adapter_id: str | None = None
    master_request_id: str | None = None
    planning_session_id: str | None = None
    step_id: str | None = None
    invocation_id: str | None = None
    tool_call_id: str | None = None
    principal_ref: str | None = None
    policy_revision: str | None = None
    resource_id: str | None = None
    correlation_id: str | None = None
    grant_nonce: str | None = None
    idempotency_key: str | None = None
    accepted_at_utc: str | None = None
    deadline_utc: str | None = None
    execution_started_at_utc: str | None = None
    execution_finished_at_utc: str | None = None
    source_reported_observed_at_utc: str | None = None
    effective_historical_start_utc: str | None = None
    effective_historical_end_utc: str | None = None
    effective_decision_aircraft_id: str | None = None
    effective_decision_recommendation_id: str | None = None
    row_limit: int | None = None
    rows_returned: int = 0
    page_index: int | None = None
    has_more: bool = False
    next_page_token_present: bool = False
    truncated: bool = False
    partial_pagination: bool = False
    page_empty: bool = False
    source_name: str | None = None
    source_version: str | None = None
    query_id: str | None = None
    error_type: str | None = None
    consumed_idempotency: bool = False
    approved_source_adapter: bool = False
    production_grant_issued: bool = False
    policy_check_used: bool = False
    source_clock_authority: str = SOURCE_CLOCK_NOT_ESTABLISHED
    geographic_coverage: str = GEOGRAPHIC_COVERAGE_NOT_ESTABLISHED
    source_version_is_integrity_proof: bool = False
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    aviation_evidence_admitted: bool = False
    verified_zero_minted: bool = False
    geographic_impact_verified: bool = False
    tool_result_authentically_read: bool = False
    local_fake_is_aviation_evidence: bool = False
    empty_page_creates_verified_zero: bool = False
    _seal: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _SEAL:
            raise ContractValidationError("forged_read_receipt")
        self._reject_promotion()
        self._validate_shape()

    def __bool__(self) -> bool:
        raise ContractValidationError("read_receipt_is_not_a_grant")

    def _reject_promotion(self) -> None:
        if (
            self.schema_version != MASTER_TRUSTED_READ_EXECUTOR_SCHEMA_VERSION
            or self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED
            or self.production_authority is not MasterProductionAuthority.DENIED
            or self.approved_source_adapter is not False
            or self.production_grant_issued is not False
            or self.policy_check_used is not False
            or self.source_clock_authority != SOURCE_CLOCK_NOT_ESTABLISHED
            or self.geographic_coverage != GEOGRAPHIC_COVERAGE_NOT_ESTABLISHED
            or self.source_version_is_integrity_proof is not False
            or self.aviation_evidence_admitted is not False
            or self.verified_zero_minted is not False
            or self.geographic_impact_verified is not False
            or self.tool_result_authentically_read is not False
            or self.local_fake_is_aviation_evidence is not False
            or self.empty_page_creates_verified_zero is not False
            or SOURCE_AUTHENTICATED_EVIDENCE_AVAILABLE is not False
            or FAKE_RESULT_IS_AVIATION_EVIDENCE is not False
            or EMPTY_PAGE_CREATES_VERIFIED_ZERO is not False
        ):
            raise ContractValidationError("read_observation_is_not_source_authentication")

    def _validate_shape(self) -> None:
        _enum_member(self.outcome, ReadExecutionOutcome, "outcome")
        _enum_member(self.authority_kind, HostAuthorityKind, "authority_kind")
        _enum_member(self.specialist, MasterSpecialist, "specialist")
        _enum_member(self.purpose, MasterPurpose, "purpose")
        _enum_member(self.capability, AgentCapability, "capability")
        _enum_member(self.operation, ReadOperation, "operation")
        if type(self.adapter_invoked) is not bool or type(self.local_test_adapter_resolved) is not bool:
            raise ContractValidationError("invalid_read_receipt")
        if type(self.policy_check_supplied) is not bool or type(self.consumed_idempotency) is not bool:
            raise ContractValidationError("invalid_read_receipt")
        if self.denial_reasons != _unique(list(self.denial_reasons)):
            raise ContractValidationError("invalid_denial_reasons")
        if not isinstance(self.guard_denial_reasons, tuple) or any(
            type(reason) is not MasterReadDenialReason for reason in self.guard_denial_reasons
        ):
            raise ContractValidationError("invalid_denial_reasons")
        if (self.outcome is ReadExecutionOutcome.COMPLETED) is not (not self.denial_reasons):
            raise ContractValidationError("invalid_read_receipt")
        if self.outcome in _INVOKED_OUTCOMES:
            if not self.adapter_invoked or not self.local_test_adapter_resolved or not self.consumed_idempotency:
                raise ContractValidationError("invalid_read_receipt")
            if len(self.effects) != 2:
                raise ContractValidationError("invalid_read_receipt")
            if self.effects[0].plane is not EffectPlane.OPERATIONAL_READ:
                raise ContractValidationError("invalid_read_receipt")
            if self.effects[1].plane is not EffectPlane.INTERNAL_LEDGER_WRITE:
                raise ContractValidationError("invalid_read_receipt")
        else:
            if self.adapter_invoked or self.local_test_adapter_resolved or self.consumed_idempotency:
                raise ContractValidationError("invalid_read_receipt")
            if self.effects:
                raise ContractValidationError("invalid_read_receipt")
        if self.outcome is not ReadExecutionOutcome.COMPLETED and self.record_ids:
            raise ContractValidationError("invalid_read_receipt")
        if self.rows_returned != len(self.record_ids):
            raise ContractValidationError("invalid_read_receipt")
        if self.page_empty is not (self.outcome is ReadExecutionOutcome.COMPLETED and not self.record_ids):
            raise ContractValidationError("invalid_read_receipt")


def bind_nonproduction_test_invocation(
    *,
    master_request_id: str,
    planning_session_id: str,
    step_id: str,
    invocation_id: str,
    specialist: MasterSpecialist,
    purpose: MasterPurpose,
    capability: AgentCapability,
    tool_name: str,
    tool_call_id: str,
    resource_id: str,
    operation: ReadOperation,
    principal_ref: str,
    policy_revision: str,
    accepted_at_utc: str,
    deadline_utc: str,
    idempotency_key: str,
    grant_nonce: str,
    executions_used: int,
    model_turns_used: int,
    tool_calls_used: int,
    timeout_budget_seconds: int,
    region_codes: frozenset[str] | None = None,
    historical_start_utc: str | None = None,
    historical_end_utc: str | None = None,
    decision_aircraft_id: str | None = None,
    decision_recommendation_id: str | None = None,
    correlation_id: str | None = None,
) -> HostReadInvocation:
    """Seal one harness invocation. There is no production or test-mode argument."""

    _require_closed()
    if region_codes:
        sealed_regions = seal_population_scope(region_codes)
        canonical_regions = tuple(sorted(sealed_regions.region_codes))
    else:
        canonical_regions = ()
    material = (
        HostAuthorityKind.NONPRODUCTION_TEST_ADAPTER.value,
        master_request_id,
        planning_session_id,
        step_id,
        invocation_id,
        specialist.value,
        purpose.value,
        capability.value,
        tool_name,
        tool_call_id,
        resource_id,
        operation.value,
        principal_ref,
        policy_revision,
        canonical_regions,
        historical_start_utc,
        historical_end_utc,
        decision_aircraft_id,
        decision_recommendation_id,
        accepted_at_utc,
        deadline_utc,
        idempotency_key,
        grant_nonce,
        executions_used,
        model_turns_used,
        tool_calls_used,
        timeout_budget_seconds,
        correlation_id,
    )
    return HostReadInvocation(
        master_request_id=master_request_id,
        planning_session_id=planning_session_id,
        step_id=step_id,
        invocation_id=invocation_id,
        specialist=specialist,
        purpose=purpose,
        capability=capability,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        resource_id=resource_id,
        operation=operation,
        principal_ref=principal_ref,
        policy_revision=policy_revision,
        region_codes=canonical_regions,
        historical_start_utc=historical_start_utc,
        historical_end_utc=historical_end_utc,
        decision_aircraft_id=decision_aircraft_id,
        decision_recommendation_id=decision_recommendation_id,
        accepted_at_utc=accepted_at_utc,
        deadline_utc=deadline_utc,
        idempotency_key=idempotency_key,
        grant_nonce=grant_nonce,
        executions_used=executions_used,
        model_turns_used=model_turns_used,
        tool_calls_used=tool_calls_used,
        timeout_budget_seconds=timeout_budget_seconds,
        correlation_id=correlation_id,
        authority_kind=HostAuthorityKind.NONPRODUCTION_TEST_ADAPTER,
        _fingerprint=_fingerprint(material),
        _seal=_SEAL,
    )


def bind_nonproduction_host_clock(now_utc: str) -> NonproductionHostClock:
    """Seal a harness clock. This does not prove production time."""

    return NonproductionHostClock(now_utc, _SEAL)


def bind_nonproduction_cancellation() -> HostCancellation:
    """Seal a harness cancellation flag."""

    return HostCancellation(_SEAL)


def bind_nonproduction_read_registry(
    specs: tuple[NonproductionAdapterSpec, ...],
) -> NonproductionReadRegistry:
    """Seal harness adapters. Every binding is nonproduction, keyed, and read-only."""

    _require_closed()
    if type(specs) is not tuple:
        raise ContractValidationError("invalid_read_registry")
    by_key: dict[tuple[object, ...], _BoundAdapter] = {}
    for spec in specs:
        if type(spec) is not NonproductionAdapterSpec:
            raise ContractValidationError("invalid_read_adapter")
        if spec.operation is ReadOperation.SCAN:
            raise ContractValidationError("scan_not_permitted")
        names = _TOOL_CAPABILITIES.get(spec.specialist, {})
        if spec.tool_name not in names or spec.capability not in names[spec.tool_name]:
            raise ContractValidationError("capability_specialist_mismatch")
        pairs = _resource_pairs(spec.specialist, spec.tool_name)
        if pairs is None or (spec.resource_id, spec.operation) not in pairs:
            raise ContractValidationError("resource_not_allowlisted")
        for denial in _text_denials(spec.resource_id):
            raise ContractValidationError(denial.value.lower())
        key = (spec.specialist, spec.tool_name, spec.capability, spec.resource_id, spec.operation)
        if key in by_key:
            raise ContractValidationError("duplicate_read_adapter")
        adapter_id = (
            f"nonproduction:{spec.specialist.value}:{spec.tool_name}:"
            f"{spec.resource_id}:{spec.operation.value}"
        )
        _token(adapter_id, "adapter_id")
        by_key[key] = _BoundAdapter(adapter_id, key, spec.read, _SEAL)
    return NonproductionReadRegistry(MappingProxyType(by_key), _SEAL)


def issue_production_read_invocation(*_args: object, **_kwargs: object) -> None:
    """Production grant issuance is not integrated. There is no success return."""

    _require_closed()
    raise ContractValidationError("production_grant_issuer_not_integrated")


def open_production_executor(*_args: object, **_kwargs: object) -> None:
    """Production execution is not integrated. There is no success return."""

    _require_closed()
    raise ContractValidationError("production_read_executor_not_enabled")


def _effects(operation: ReadOperation, resource_id: str) -> tuple[ExecutionEffect, ...]:
    return (
        ExecutionEffect(
            plane=EffectPlane.OPERATIONAL_READ,
            operation=operation.value,
            resource_id=resource_id,
        ),
        ExecutionEffect(
            plane=EffectPlane.INTERNAL_LEDGER_WRITE,
            operation=LEDGER_OPERATION,
            resource_id=LEDGER_RESOURCE_ID,
        ),
    )


def _identity_denials(
    host: HostReadInvocation,
    request: UntrustedReadRequest,
) -> list[ReadBoundaryDenial]:
    reasons: list[ReadBoundaryDenial] = []
    if host.specialist is not request.specialist:
        reasons.append(ReadBoundaryDenial.SPECIALIST_MISMATCH)
    if host.purpose is not request.purpose:
        reasons.append(ReadBoundaryDenial.PURPOSE_MISMATCH)
    if host.capability is not request.capability:
        reasons.append(ReadBoundaryDenial.CAPABILITY_MISMATCH)
    if host.tool_name != request.tool_name:
        reasons.append(ReadBoundaryDenial.TOOL_MISMATCH)
    if host.resource_id != request.resource_id:
        reasons.append(ReadBoundaryDenial.RESOURCE_MISMATCH)
    if host.operation is not request.operation:
        reasons.append(ReadBoundaryDenial.OPERATION_MISMATCH)
    if (
        host.master_request_id != request.master_request_id
        or host.planning_session_id != request.planning_session_id
        or host.step_id != request.step_id
        or host.invocation_id != request.invocation_id
        or host.tool_call_id != request.tool_call_id
    ):
        reasons.append(ReadBoundaryDenial.IDENTITY_MISMATCH)
    if (
        request.correlation_id is not None
        and host.correlation_id is not None
        and request.correlation_id != host.correlation_id
    ):
        reasons.append(ReadBoundaryDenial.CORRELATION_MISMATCH)
    return reasons


def _scope_denials(
    host: HostReadInvocation,
    request: UntrustedReadRequest,
) -> list[ReadBoundaryDenial]:
    reasons: list[ReadBoundaryDenial] = []
    if host.specialist is not request.specialist or host.tool_name != request.tool_name:
        return reasons
    arguments = dict(request.requested_arguments)
    names = _argument_names(request.specialist, request.tool_name)
    if names is not None and "start_utc" in names:
        if host.historical_start_utc is None or host.historical_end_utc is None:
            reasons.append(ReadBoundaryDenial.HISTORICAL_WINDOW_ABSENT)
        else:
            start = arguments.get("start_utc")
            end = arguments.get("end_utc")
            try:
                requested_start = _whole_second(start, "start_utc")
                requested_end = _whole_second(end, "end_utc")
                host_start = _whole_second(host.historical_start_utc, "historical_start_utc")
                host_end = _whole_second(host.historical_end_utc, "historical_end_utc")
            except ContractValidationError:
                reasons.append(ReadBoundaryDenial.HISTORICAL_WINDOW_OUTSIDE_HOST)
            else:
                if requested_start < host_start or requested_end > host_end:
                    reasons.append(ReadBoundaryDenial.HISTORICAL_WINDOW_OUTSIDE_HOST)
    if request.specialist is MasterSpecialist.DECISION:
        if "aircraft_id" in arguments:
            if host.decision_aircraft_id is None:
                reasons.append(ReadBoundaryDenial.DECISION_TARGET_ABSENT)
            elif arguments.get("aircraft_id") != host.decision_aircraft_id:
                reasons.append(ReadBoundaryDenial.DECISION_TARGET_MISMATCH)
        if "recommendation_id" in arguments:
            if host.decision_recommendation_id is None:
                reasons.append(ReadBoundaryDenial.DECISION_TARGET_ABSENT)
            elif arguments.get("recommendation_id") != host.decision_recommendation_id:
                reasons.append(ReadBoundaryDenial.DECISION_TARGET_MISMATCH)
    if "region" in arguments:
        region = arguments.get("region")
        if not isinstance(region, str) or region not in host.region_codes:
            reasons.append(ReadBoundaryDenial.ARGUMENT_INVALID)
    return reasons


def _argument_denials(request: UntrustedReadRequest) -> list[ReadBoundaryDenial]:
    reasons: list[ReadBoundaryDenial] = []
    names = _argument_names(request.specialist, request.tool_name)
    for name, value in request.requested_arguments:
        if names is not None and name not in names:
            reasons.append(ReadBoundaryDenial.ARGUMENT_NOT_ALLOWLISTED)
        if callable(value) or isinstance(value, (dict, list, set)):
            reasons.append(ReadBoundaryDenial.CALLER_CALLABLE_REJECTED)
            reasons.append(ReadBoundaryDenial.ARGUMENT_INVALID)
        reasons.extend(_walk_text_denials(value))
    return reasons


def _resource_denials(request: UntrustedReadRequest) -> list[ReadBoundaryDenial]:
    reasons = list(_text_denials(request.resource_id))
    reasons.extend(_text_denials(request.tool_name))
    if request.operation is ReadOperation.SCAN:
        reasons.append(ReadBoundaryDenial.SCAN_NOT_PERMITTED)
    pairs = _resource_pairs(request.specialist, request.tool_name)
    if pairs is not None and (request.resource_id, request.operation) not in pairs:
        if request.operation is not ReadOperation.SCAN and not _text_denials(request.resource_id):
            reasons.append(ReadBoundaryDenial.RESOURCE_MISMATCH)
    return reasons


def _budget_deadline_denials(
    host: HostReadInvocation,
    observed_utc: str,
    cancellation: HostCancellation,
) -> list[ReadBoundaryDenial]:
    reasons: list[ReadBoundaryDenial] = []
    max_executions, max_turns, max_calls = server_read_budget(host.specialist)
    if (
        host.executions_used >= max_executions
        or host.model_turns_used >= max_turns
        or host.tool_calls_used >= max_calls
    ):
        reasons.append(ReadBoundaryDenial.BUDGET_EXHAUSTED)
    observed = _whole_second(observed_utc, "observed_utc")
    deadline = _whole_second(host.deadline_utc, "deadline_utc")
    if deadline <= observed:
        reasons.append(ReadBoundaryDenial.DEADLINE_EXPIRED)
    if cancellation.is_cancelled():
        reasons.append(ReadBoundaryDenial.CANCELLED)
    return reasons


def _caller_denials(request: UntrustedReadRequest) -> list[ReadBoundaryDenial]:
    reasons: list[ReadBoundaryDenial] = []
    if request.caller_scope is not None:
        reasons.append(ReadBoundaryDenial.CALLER_SCOPE_NOT_AUTHORITY)
    if request.caller_callable is not None or any(callable(value) for _name, value in request.requested_arguments):
        reasons.append(ReadBoundaryDenial.CALLER_CALLABLE_REJECTED)
    if request.caller_tool_result is not None or type(request.caller_tool_result).__name__ == "ToolResult":
        reasons.append(ReadBoundaryDenial.CALLER_TOOL_RESULT_NOT_A_READ)
    if request.caller_max_tool_calls is not None:
        reasons.append(ReadBoundaryDenial.CALLER_BUDGET_NOT_AUTHORITY)
    if request.caller_deadline_utc is not None:
        reasons.append(ReadBoundaryDenial.CALLER_DEADLINE_NOT_AUTHORITY)
    if request.caller_authorized is not None or request.caller_policy_decision is not None:
        reasons.append(ReadBoundaryDenial.POLICY_CHECK_NOT_EXECUTION_GRANT)
    return reasons


def _executed_arguments(
    request: UntrustedReadRequest,
) -> tuple[tuple[tuple[str, object], ...], tuple[str, ...]]:
    names = _argument_names(request.specialist, request.tool_name) or frozenset()
    pairs = list(request.requested_arguments)
    present = {name for name, _value in pairs}
    injected: list[str] = []
    if "limit" in names and "limit" not in present:
        pairs.append(("limit", HISTORICAL_LIST_MAX))
        injected.append("limit")
    return tuple(pairs), tuple(injected)


def _row_cap(operation: ReadOperation, executed: tuple[tuple[str, object], ...]) -> int:
    if operation is ReadOperation.GET_ITEM:
        return 1
    for name, value in executed:
        if name == "limit" and type(value) is int and not isinstance(value, bool):
            return value
    return HISTORICAL_LIST_MAX


def _error_type(exc: BaseException) -> str:
    name = type(exc).__name__
    if _ERROR_TYPE_RE.fullmatch(name) and len(name) <= 64:
        return name
    return "Exception"


def _elapsed_exceeded(started_at: str, finished_at: str, budget_seconds: int) -> bool:
    started = _whole_second(started_at, "execution_started_at_utc")
    finished = _whole_second(finished_at, "execution_finished_at_utc")
    if finished < started:
        raise ContractValidationError("host_clock_moved_backward")
    return (finished - started).total_seconds() > budget_seconds


class ReadExecutionError(Exception):
    """A started read failed. The message is the outcome code, not adapter text."""

    def __init__(self, receipt: "ReadReceiptCandidate"):
        self.receipt = receipt
        super().__init__(receipt.outcome.value)


class MasterTrustedReadExecutor:
    """Runs one keyed read after the host seal, allowlist, scope, budget, and adapter all match.

    ``execute`` does not accept a callable. ``policy_check`` is ignored as
    authority and is never read. The returned receipt is not aviation evidence.
    """

    def __init__(self, registry: object, clock: object) -> None:
        _require_closed()
        if type(registry) is not NonproductionReadRegistry or registry._seal is not _SEAL:
            raise ContractValidationError("approved_source_adapter_absent")
        if type(clock) is not NonproductionHostClock or clock._seal is not _SEAL:
            raise ContractValidationError("host_clock_not_sealed")
        self._registry = registry
        self._clock = clock
        self._lock = threading.Lock()
        self._consumed: dict[str, str] = {}

    def __bool__(self) -> bool:
        raise ContractValidationError("read_executor_is_not_a_grant")

    def execute(
        self,
        request: UntrustedReadRequest,
        *,
        host_invocation: object = None,
        cancellation: object = None,
        policy_check: object = None,
    ) -> ReadReceiptCandidate:
        """Validate at this boundary, then call the sealed adapter or reject."""

        _require_closed()
        if type(request) is not UntrustedReadRequest:
            raise ContractValidationError("invalid_read_request")
        host = host_invocation if _host_is_sealed(host_invocation) else None
        if host is None or type(cancellation) is not HostCancellation or cancellation._seal is not _SEAL:
            return self._rejected(
                request,
                host if isinstance(host, HostReadInvocation) else None,
                self._closed_denials(host, cancellation, policy_check, request),
                (),
                policy_check is not None,
            )
        assert isinstance(host, HostReadInvocation)
        observed = self._clock.now_utc()
        denials = _caller_denials(request)
        denials.extend(_identity_denials(host, request))
        denials.extend(_resource_denials(request))
        denials.extend(_argument_denials(request))
        denials.extend(_scope_denials(host, request))
        denials.extend(_budget_deadline_denials(host, observed, cancellation))
        guard_reasons = self._guard_reasons(request, host, observed, cancellation)
        if guard_reasons:
            denials.append(ReadBoundaryDenial.POLICY_EVALUATION_DENIED)
        binding = None
        if host.specialist is request.specialist and host.tool_name == request.tool_name:
            binding = self._registry.resolve(
                (host.specialist, host.tool_name, host.capability, host.resource_id, host.operation)
            )
            if binding is None:
                denials.append(ReadBoundaryDenial.APPROVED_SOURCE_ADAPTER_ABSENT)
        elif not denials:
            denials.append(ReadBoundaryDenial.APPROVED_SOURCE_ADAPTER_ABSENT)
        unique = _unique(denials)
        if unique:
            return self._rejected(request, host, unique, guard_reasons, policy_check is not None)
        if binding is None:
            return self._rejected(
                request,
                host,
                (ReadBoundaryDenial.APPROVED_SOURCE_ADAPTER_ABSENT,),
                guard_reasons,
                policy_check is not None,
            )
        executed, injected = _executed_arguments(request)
        with self._lock:
            if host.idempotency_key in self._consumed:
                return self._rejected(
                    request,
                    host,
                    (ReadBoundaryDenial.DUPLICATE_CONSUMPTION,),
                    guard_reasons,
                    policy_check is not None,
                )
            self._consumed[host.idempotency_key] = "STARTED"
            started = self._clock.now_utc()
        return self._run(
            request,
            host,
            cancellation,
            binding,
            executed,
            injected,
            started,
            guard_reasons,
            policy_check is not None,
        )

    def _closed_denials(
        self,
        host: object,
        cancellation: object,
        policy_check: object,
        request: UntrustedReadRequest,
    ) -> tuple[ReadBoundaryDenial, ...]:
        reasons: list[ReadBoundaryDenial] = []
        if host is None:
            reasons.append(ReadBoundaryDenial.HOST_AUTHORITY_ABSENT)
            reasons.append(ReadBoundaryDenial.PRODUCTION_GRANT_NOT_INTEGRATED)
        if type(cancellation) is not HostCancellation or cancellation._seal is not _SEAL:
            reasons.append(ReadBoundaryDenial.CANCELLATION_AUTHORITY_ABSENT)
        if policy_check is not None:
            reasons.append(ReadBoundaryDenial.POLICY_CHECK_NOT_EXECUTION_GRANT)
        reasons.extend(_caller_denials(request))
        reasons.extend(_resource_denials(request))
        return _unique(reasons)

    def _guard_reasons(
        self,
        request: UntrustedReadRequest,
        host: HostReadInvocation,
        observed_utc: str,
        cancellation: HostCancellation,
    ) -> tuple[MasterReadDenialReason, ...]:
        start = request_argument(request, "start_utc")
        end = request_argument(request, "end_utc")
        proposal = MasterReadResourceProposal(
            specialist=request.specialist,
            purpose=request.purpose,
            capability=request.capability,
            tool_name=request.tool_name,
            resource_id=request.resource_id,
            operation=request.operation,
            arguments=request.requested_arguments,
            master_request_id=request.master_request_id,
            invocation_id=request.invocation_id,
            principal_label=request.principal_label,
            caller_allowlist=request.caller_scope,
            caller_policy_decision=request.caller_policy_decision,
            caller_authorized=request.caller_authorized,
            caller_deadline_utc=request.caller_deadline_utc,
            caller_max_tool_calls=request.caller_max_tool_calls,
            historical_start_utc=start if isinstance(start, str) else None,
            historical_end_utc=end if isinstance(end, str) else None,
        )
        evaluation = ServerReadEvaluation(
            observed_utc=observed_utc,
            deadline_utc=host.deadline_utc,
            executions_used=host.executions_used,
            model_turns_used=host.model_turns_used,
            tool_calls_used=host.tool_calls_used,
            cancellation=ServerCancellation(cancellation.is_cancelled()),
        )
        population = seal_population_scope(frozenset(host.region_codes)) if host.region_codes else None
        check = check_master_read_resource(
            proposal,
            evaluation=evaluation,
            principal=seal_ingress_principal(host.principal_ref),
            population_scope=population,
        )
        return check.denial_reasons

    def _run(
        self,
        request: UntrustedReadRequest,
        host: HostReadInvocation,
        cancellation: HostCancellation,
        binding: _BoundAdapter,
        executed: tuple[tuple[str, object], ...],
        injected: tuple[str, ...],
        started: str,
        guard_reasons: tuple[MasterReadDenialReason, ...],
        policy_check_supplied: bool,
    ) -> ReadReceiptCandidate:
        page: object = None
        failure: ReadBoundaryDenial | None = None
        error_type: str | None = None
        try:
            page = binding.read(dict(executed), cancellation)
        except Exception as exc:
            failure = ReadBoundaryDenial.ADAPTER_EXCEPTION
            error_type = _error_type(exc)
            page = None
        finished = self._clock.now_utc()
        outcome = ReadExecutionOutcome.EXCEPTION if failure else ReadExecutionOutcome.COMPLETED
        record_ids: tuple[str, ...] = ()
        page_index = None
        has_more = False
        token_present = False
        truncated = False
        source_name = None
        source_version = None
        query_id = None
        source_observed = None
        row_limit = _row_cap(host.operation, executed)
        if failure is None and cancellation.is_cancelled():
            failure = ReadBoundaryDenial.CANCELLED
            outcome = ReadExecutionOutcome.CANCELLED
            page = None
        elif failure is None and (
            _elapsed_exceeded(started, finished, host.timeout_budget_seconds)
            or _whole_second(finished, "execution_finished_at_utc") >= _whole_second(host.deadline_utc, "deadline_utc")
        ):
            failure = ReadBoundaryDenial.TIMEOUT
            outcome = ReadExecutionOutcome.TIMEOUT
            error_type = "timeout"
            page = None
        elif failure is None:
            observed = _accept_page(page, row_limit)
            if isinstance(observed, ReadBoundaryDenial):
                failure = observed
                outcome = (
                    ReadExecutionOutcome.QUERY_LIMIT_EXCEEDED
                    if observed is ReadBoundaryDenial.QUERY_LIMIT_EXCEEDED
                    else ReadExecutionOutcome.EXCEPTION
                )
                if outcome is ReadExecutionOutcome.EXCEPTION:
                    error_type = "invalid_read_page"
                else:
                    error_type = "query_limit_exceeded"
            else:
                record_ids, page_index, has_more, token_present, truncated = observed[0]
                source_name, source_version, query_id, source_observed = observed[1]
        if failure is not None:
            record_ids = ()
            page_index = None
            has_more = False
            token_present = False
            truncated = False
            source_name = None
            source_version = None
            query_id = None
            source_observed = None
        with self._lock:
            self._consumed[host.idempotency_key] = outcome.value
        receipt = self._receipt(
            request,
            host,
            outcome=outcome,
            denials=_unique([failure] if failure is not None else []),
            guard_reasons=guard_reasons,
            policy_check_supplied=policy_check_supplied,
            adapter_invoked=True,
            adapter_id=binding.adapter_id,
            executed=executed,
            injected=injected,
            started=started,
            finished=finished,
            record_ids=record_ids,
            page_index=page_index,
            has_more=has_more,
            token_present=token_present,
            truncated=truncated,
            source_name=source_name,
            source_version=source_version,
            query_id=query_id,
            source_observed=source_observed,
            row_limit=row_limit,
            error_type=error_type,
            consumed=True,
        )
        if failure is not None:
            raise ReadExecutionError(receipt) from None
        return receipt

    def _rejected(
        self,
        request: UntrustedReadRequest,
        host: HostReadInvocation | None,
        denials: tuple[ReadBoundaryDenial, ...],
        guard_reasons: tuple[MasterReadDenialReason, ...],
        policy_check_supplied: bool,
    ) -> ReadReceiptCandidate:
        return self._receipt(
            request,
            host,
            outcome=ReadExecutionOutcome.REJECTED,
            denials=denials,
            guard_reasons=guard_reasons,
            policy_check_supplied=policy_check_supplied,
            adapter_invoked=False,
            adapter_id=None,
            executed=(),
            injected=(),
            started=None,
            finished=None,
            record_ids=(),
            page_index=None,
            has_more=False,
            token_present=False,
            truncated=False,
            source_name=None,
            source_version=None,
            query_id=None,
            source_observed=None,
            row_limit=None,
            error_type=None,
            consumed=False,
        )

    def _receipt(
        self,
        request: UntrustedReadRequest,
        host: HostReadInvocation | None,
        *,
        outcome: ReadExecutionOutcome,
        denials: tuple[ReadBoundaryDenial, ...],
        guard_reasons: tuple[MasterReadDenialReason, ...],
        policy_check_supplied: bool,
        adapter_invoked: bool,
        adapter_id: str | None,
        executed: tuple[tuple[str, object], ...],
        injected: tuple[str, ...],
        started: str | None,
        finished: str | None,
        record_ids: tuple[str, ...],
        page_index: int | None,
        has_more: bool,
        token_present: bool,
        truncated: bool,
        source_name: str | None,
        source_version: str | None,
        query_id: str | None,
        source_observed: str | None,
        row_limit: int | None,
        error_type: str | None,
        consumed: bool,
    ) -> ReadReceiptCandidate:
        partial = bool(has_more or truncated) and outcome is ReadExecutionOutcome.COMPLETED
        return ReadReceiptCandidate(
            outcome=outcome,
            authority_kind=(
                HostAuthorityKind.NONPRODUCTION_TEST_ADAPTER
                if host is not None
                else HostAuthorityKind.ABSENT
            ),
            denial_reasons=denials,
            guard_denial_reasons=guard_reasons,
            adapter_invoked=adapter_invoked,
            local_test_adapter_resolved=adapter_invoked,
            policy_check_supplied=policy_check_supplied,
            specialist=request.specialist,
            purpose=request.purpose,
            capability=request.capability,
            operation=request.operation,
            requested_arguments=request.requested_arguments,
            executed_arguments=executed if adapter_invoked else (),
            scope_injected_argument_names=injected if adapter_invoked else (),
            effective_region_codes=host.region_codes if host is not None else (),
            effects=_effects(request.operation, request.resource_id) if adapter_invoked else (),
            record_ids=record_ids,
            adapter_id=adapter_id,
            master_request_id=request.master_request_id,
            planning_session_id=request.planning_session_id,
            step_id=request.step_id,
            invocation_id=request.invocation_id,
            tool_call_id=request.tool_call_id,
            principal_ref=host.principal_ref if host is not None else None,
            policy_revision=host.policy_revision if host is not None else None,
            resource_id=request.resource_id,
            correlation_id=host.correlation_id if host is not None else None,
            grant_nonce=host.grant_nonce if host is not None else None,
            idempotency_key=host.idempotency_key if host is not None else None,
            accepted_at_utc=host.accepted_at_utc if host is not None else None,
            deadline_utc=host.deadline_utc if host is not None else None,
            execution_started_at_utc=started,
            execution_finished_at_utc=finished,
            source_reported_observed_at_utc=source_observed,
            effective_historical_start_utc=host.historical_start_utc if host is not None else None,
            effective_historical_end_utc=host.historical_end_utc if host is not None else None,
            effective_decision_aircraft_id=host.decision_aircraft_id if host is not None else None,
            effective_decision_recommendation_id=host.decision_recommendation_id if host is not None else None,
            row_limit=row_limit,
            rows_returned=len(record_ids),
            page_index=page_index,
            has_more=has_more if outcome is ReadExecutionOutcome.COMPLETED else False,
            next_page_token_present=token_present if outcome is ReadExecutionOutcome.COMPLETED else False,
            truncated=truncated if outcome is ReadExecutionOutcome.COMPLETED else False,
            partial_pagination=partial,
            page_empty=outcome is ReadExecutionOutcome.COMPLETED and not record_ids,
            source_name=source_name,
            source_version=source_version,
            query_id=query_id,
            error_type=error_type,
            consumed_idempotency=consumed,
            _seal=_SEAL,
        )


def request_argument(request: UntrustedReadRequest, name: str) -> object:
    for key, value in request.requested_arguments:
        if key == name:
            return value
    return None


def _accept_page(
    page: object,
    row_limit: int,
) -> ReadBoundaryDenial | tuple[
    tuple[tuple[str, ...], int, bool, bool, bool],
    tuple[str | None, str | None, str | None, str | None],
]:
    if type(page) is not NonproductionReadPage:
        return ReadBoundaryDenial.ADAPTER_RETURN_NOT_OBSERVATION
    try:
        return _accepted_page(page, row_limit)
    except ContractValidationError:
        return ReadBoundaryDenial.ADAPTER_RETURN_NOT_OBSERVATION


def _accepted_page(
    page: NonproductionReadPage,
    row_limit: int,
) -> tuple[
    tuple[tuple[str, ...], int, bool, bool, bool],
    tuple[str | None, str | None, str | None, str | None],
]:
    if len(page.record_ids) > row_limit:
        return ReadBoundaryDenial.QUERY_LIMIT_EXCEEDED  # type: ignore[return-value]
    for record_id in page.record_ids:
        if not record_id or any(character.isspace() for character in record_id) or _text_denials(record_id):
            return ReadBoundaryDenial.ADAPTER_RETURN_NOT_OBSERVATION  # type: ignore[return-value]
        _token(record_id, "record_id")
    for label, value in (
        ("source_name", page.source_name),
        ("source_version", page.source_version),
        ("query_id", page.query_id),
    ):
        if value is not None:
            _optional_token(value, label)
    source_observed = page.source_observed_at_utc
    if source_observed is not None:
        _whole_second(source_observed, "source_reported_observed_at_utc")
    # ``next_page_token`` is intentionally not returned.
    token_present = page.next_page_token is not None and page.next_page_token != ""
    return (
        (page.record_ids, page.page_index, page.has_more, token_present, page.truncated),
        (page.source_name, page.source_version, page.query_id, source_observed),
    )


def raw_read_observation(receipt: ReadReceiptCandidate) -> dict[str, object]:
    """Descriptive copy for Agent 10. The dict is not a sealed read and not a ToolResult."""

    if type(receipt) is not ReadReceiptCandidate or receipt._seal is not _SEAL:
        raise ContractValidationError("forged_read_receipt")
    return {
        "schema_version": receipt.schema_version,
        "candidate_kind": RAW_OBSERVATION_KIND,
        "authentic_source_read": False,
        "authority_kind": receipt.authority_kind.value,
        "outcome": receipt.outcome.value,
        "denial_reasons": [reason.value for reason in receipt.denial_reasons],
        "guard_denial_reasons": [reason.value for reason in receipt.guard_denial_reasons],
        "adapter_id": receipt.adapter_id,
        "adapter_origin": (
            "NONPRODUCTION_TEST" if receipt.local_test_adapter_resolved else "ABSENT"
        ),
        "approved_source_adapter": False,
        "production_grant_issued": False,
        "policy_check_used": False,
        "master_request_id": receipt.master_request_id,
        "planning_session_id": receipt.planning_session_id,
        "step_id": receipt.step_id,
        "invocation_id": receipt.invocation_id,
        "tool_call_id": receipt.tool_call_id,
        "specialist": receipt.specialist.value,
        "purpose": receipt.purpose.value,
        "capability": receipt.capability.value,
        "principal_ref": receipt.principal_ref,
        "policy_revision": receipt.policy_revision,
        "resource_id": receipt.resource_id,
        "operation": receipt.operation.value,
        "correlation_id": receipt.correlation_id,
        "grant_nonce": receipt.grant_nonce,
        "idempotency_key": receipt.idempotency_key,
        "accepted_at_utc": receipt.accepted_at_utc,
        "deadline_utc": receipt.deadline_utc,
        "execution_started_at_utc": receipt.execution_started_at_utc,
        "execution_finished_at_utc": receipt.execution_finished_at_utc,
        "source_reported_observed_at_utc": receipt.source_reported_observed_at_utc,
        "source_clock_authority": SOURCE_CLOCK_NOT_ESTABLISHED,
        "requested_arguments": [[name, value] for name, value in receipt.requested_arguments],
        "executed_arguments": [[name, value] for name, value in receipt.executed_arguments],
        "scope_injected_argument_names": list(receipt.scope_injected_argument_names),
        "effective_region_codes": list(receipt.effective_region_codes),
        "effective_historical_start_utc": receipt.effective_historical_start_utc,
        "effective_historical_end_utc": receipt.effective_historical_end_utc,
        "effective_decision_aircraft_id": receipt.effective_decision_aircraft_id,
        "effective_decision_recommendation_id": receipt.effective_decision_recommendation_id,
        "geographic_coverage": GEOGRAPHIC_COVERAGE_NOT_ESTABLISHED,
        "geographic_impact_verified": False,
        "row_limit": receipt.row_limit,
        "rows_returned": receipt.rows_returned,
        "record_ids": list(receipt.record_ids),
        "page_index": receipt.page_index,
        "has_more": receipt.has_more,
        "next_page_token_present": receipt.next_page_token_present,
        "truncated": receipt.truncated,
        "partial_pagination": receipt.partial_pagination,
        "page_empty": receipt.page_empty,
        "source_name": receipt.source_name,
        "source_version": receipt.source_version,
        "source_version_is_integrity_proof": False,
        "query_id": receipt.query_id,
        "error_type": receipt.error_type,
        "effects": [
            {
                "plane": effect.plane.value,
                "operation": effect.operation,
                "resource_id": effect.resource_id,
                "operational_data_mutation": False,
            }
            for effect in receipt.effects
        ],
        "source_authenticity": receipt.source_authenticity.value,
        "production_authority": receipt.production_authority.value,
        "aviation_evidence_admitted": False,
        "verified_zero_minted": False,
        "tool_result_authentically_read": False,
        "local_fake_is_aviation_evidence": False,
    }


__all__ = [
    "APPROVED_SOURCE_ADAPTER_INTEGRATED",
    "EMPTY_PAGE_CREATES_VERIFIED_ZERO",
    "ENVIRONMENT_SELECTS_TEST_AUTHORITY",
    "FAKE_RESULT_IS_AVIATION_EVIDENCE",
    "GEOGRAPHIC_COVERAGE_NOT_ESTABLISHED",
    "LEDGER_OPERATION",
    "LEDGER_RESOURCE_ID",
    "MASTER_TRUSTED_READ_EXECUTOR_SCHEMA_VERSION",
    "PRODUCTION_GRANT_ISSUER_INTEGRATED",
    "PRODUCTION_READ_EXECUTOR_ENABLED",
    "RAW_OBSERVATION_KIND",
    "SOURCE_AUTHENTICATED_EVIDENCE_AVAILABLE",
    "SOURCE_CLOCK_NOT_ESTABLISHED",
    "WITHIN_ALLOWLIST_IS_EXECUTION_GRANT",
    "EffectPlane",
    "ExecutionEffect",
    "HostAuthorityKind",
    "HostCancellation",
    "HostReadInvocation",
    "MasterTrustedReadExecutor",
    "NonproductionAdapterSpec",
    "NonproductionHostClock",
    "NonproductionReadPage",
    "NonproductionReadRegistry",
    "ReadBoundaryDenial",
    "ReadExecutionError",
    "ReadExecutionOutcome",
    "ReadReceiptCandidate",
    "UntrustedReadRequest",
    "bind_nonproduction_cancellation",
    "bind_nonproduction_host_clock",
    "bind_nonproduction_read_registry",
    "bind_nonproduction_test_invocation",
    "issue_production_read_invocation",
    "open_production_executor",
    "raw_read_observation",
]
