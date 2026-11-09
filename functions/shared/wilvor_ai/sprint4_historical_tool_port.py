"""Sprint 4 Agent B: host-authorized invocation of the four historical tools.

This port sits in front of ``HistoricalAnalyticsAdapter``. It does not render
SQL, create AWS clients, or treat historical facts as current observations.

Host rules:

- Tool names are the closed V1 catalog only.
- ``start_utc`` / ``end_utc`` must equal the host-eligible window. The unbound
  plan window is not a tool argument and is not copied.
- Windows over seven days are rejected, not clipped.
- ``as_of_utc`` and ``tool_call_id`` are constructor-bound trusted context.
- Caller SQL, workgroup, database, and output location arguments fail.
- List limits above the specialist AI ceiling fail rather than silently
  widening to the storage ``LIST_MAX_LIMIT``.
- Provenance stays on ``QueryExecutionTrace`` / ``SourceRecord``.
- Coverage-blocked and empty uncertified pages are not verified zeros.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from wilvor_ai.contracts import (
    ContractValidationError,
    FreshnessStatus,
    TemporalScope,
    ToolInputValueType,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.historical_analytics import (
    HISTORICAL_ANALYTICS_TOOLS,
    HistoricalAnalyticsAdapter,
    HistoricalAnalyticsCall,
)
from wilvor_ai.historical_analytics_mapping import HISTORICAL_FRESHNESS_NOT_ESTABLISHED
from wilvor_ai.historical_specialist import AI_LIST_DEFAULT, AI_LIST_MAX, AI_LIST_MIN
from wilvor_ai.sprint4_historical_query_runtime import (
    CALLER_SQL_PERMITTED,
    COVERAGE_BLOCKED_IS_ZERO,
    EMPTY_ATHENA_PAGE_IS_VERIFIED_ZERO,
    HISTORICAL_FACTS_ARE_CURRENT_OBSERVATIONS,
    PRODUCTION_HISTORICAL_ATHENA_ENABLED,
    UNBOUND_PLAN_WINDOW_IS_TOOL_ARGUMENT,
    Sprint4HistoricalQueryRuntime,
)
from wilvor_historical.query_contracts import FORBIDDEN_QUERY_REQUEST_FIELDS
from wilvor_historical.query_windows import MAX_QUERY_WINDOW
from wilvor_historical_query.operations import HistoricalAnalyticsOperations


SPRINT4_HISTORICAL_TOOL_PORT_SCHEMA_VERSION = (
    "wilvor.ai.sprint4.historical_tool_port.v1"
)

_CATALOG = {item.name: item for item in HISTORICAL_ANALYTICS_TOOLS}
_TRUSTED_ARGUMENT_NAMES = frozenset(
    {
        "as_of_utc",
        "tool_call_id",
        "operations",
        "call",
        "correlation_id",
        "dataset",
        "temporal_scope",
        "freshness",
        "workgroup",
        "database",
        "output_location",
        "bucket",
        "sql",
        "statement",
        "query_string",
        "query",
        "columns",
        "group_by",
        "order_by",
    }
)
_SQL_ARGUMENT_NAMES = frozenset(
    {
        "sql",
        "statement",
        "query_string",
        "query",
        "columns",
        "group_by",
        "order_by",
        "workgroup",
        "database",
        "output_location",
    }
)
_FORBIDDEN_REQUEST_FIELDS = FORBIDDEN_QUERY_REQUEST_FIELDS | _SQL_ARGUMENT_NAMES
LIST_HISTORICAL_ENCOUNTERS = "list_historical_encounters"


class HistoricalToolRefusal(str, Enum):
    """Why the port did not invoke a historical analytics tool."""

    PRODUCTION_QUERY_NOT_ENABLED = "PRODUCTION_QUERY_NOT_ENABLED"
    LIVE_ATHENA_NOT_INITIATED = "LIVE_ATHENA_NOT_INITIATED"
    UNKNOWN_HISTORICAL_TOOL = "UNKNOWN_HISTORICAL_TOOL"
    TRUSTED_ARGUMENT_FORBIDDEN = "TRUSTED_ARGUMENT_FORBIDDEN"
    ARBITRARY_SQL = "ARBITRARY_SQL"
    UNKNOWN_ARGUMENT = "UNKNOWN_ARGUMENT"
    MISSING_REQUIRED_ARGUMENT = "MISSING_REQUIRED_ARGUMENT"
    INVALID_ARGUMENT_TYPE = "INVALID_ARGUMENT_TYPE"
    HISTORICAL_WINDOW_NOT_AUTHORIZED = "HISTORICAL_WINDOW_NOT_AUTHORIZED"
    HISTORICAL_INTERVAL_ESCALATION = "HISTORICAL_INTERVAL_ESCALATION"
    UNBOUND_PLAN_WINDOW_NOT_TOOL_ARGUMENT = "UNBOUND_PLAN_WINDOW_NOT_TOOL_ARGUMENT"
    LIST_LIMIT_EXCEEDED = "LIST_LIMIT_EXCEEDED"
    INVALID_RUNTIME = "INVALID_RUNTIME"
    CURRENT_OBSERVATION_CLAIMED = "CURRENT_OBSERVATION_CLAIMED"
    EMPTY_PAGE_IS_NOT_VERIFIED_ZERO = "EMPTY_PAGE_IS_NOT_VERIFIED_ZERO"
    COVERAGE_BLOCKED_IS_NOT_ZERO = "COVERAGE_BLOCKED_IS_NOT_ZERO"


def _closed() -> None:
    if (
        PRODUCTION_HISTORICAL_ATHENA_ENABLED is not False
        or CALLER_SQL_PERMITTED is not False
        or HISTORICAL_FACTS_ARE_CURRENT_OBSERVATIONS is not False
        or EMPTY_ATHENA_PAGE_IS_VERIFIED_ZERO is not False
        or COVERAGE_BLOCKED_IS_ZERO is not False
        or UNBOUND_PLAN_WINDOW_IS_TOOL_ARGUMENT is not False
    ):
        raise ContractValidationError("production_historical_query_not_enabled")


def _token(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _whole_second(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or len(value) != 20 or value[10] != "T" or not value.endswith("Z"):
        raise ContractValidationError(f"invalid_{field_name}")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ContractValidationError(f"invalid_{field_name}") from exc
    return parsed


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class HostAuthorizedHistoricalWindow:
    """Host-eligible historical interval. Not a native SpecialistRequest field."""

    eligible_start_utc: str
    eligible_end_utc: str
    unbound_plan_start_utc: str
    unbound_plan_end_utc: str
    as_of_utc: str

    def __post_init__(self) -> None:
        _closed()
        eligible_start = _whole_second(self.eligible_start_utc, "eligible_start_utc")
        eligible_end = _whole_second(self.eligible_end_utc, "eligible_end_utc")
        plan_start = _whole_second(self.unbound_plan_start_utc, "unbound_plan_start_utc")
        plan_end = _whole_second(self.unbound_plan_end_utc, "unbound_plan_end_utc")
        _whole_second(self.as_of_utc, "as_of_utc")
        if eligible_start >= eligible_end:
            raise ContractValidationError("invalid_eligible_historical_window")
        if plan_start >= plan_end:
            raise ContractValidationError("invalid_unbound_plan_window")
        if eligible_end - eligible_start > MAX_QUERY_WINDOW:
            raise ContractValidationError("historical_window_exceeds_seven_days")
        if plan_end - plan_start > MAX_QUERY_WINDOW:
            raise ContractValidationError("historical_window_exceeds_seven_days")
        if eligible_start < plan_start or eligible_end > plan_end:
            raise ContractValidationError("historical_window_outside_host")
        eligible = (self.eligible_start_utc, self.eligible_end_utc)
        plan = (self.unbound_plan_start_utc, self.unbound_plan_end_utc)
        if eligible == plan:
            raise ContractValidationError("unbound_plan_window_is_not_tool_argument")
        object.__setattr__(self, "eligible_start_utc", _format_utc(eligible_start))
        object.__setattr__(self, "eligible_end_utc", _format_utc(eligible_end))
        object.__setattr__(self, "unbound_plan_start_utc", _format_utc(plan_start))
        object.__setattr__(self, "unbound_plan_end_utc", _format_utc(plan_end))
        object.__setattr__(self, "as_of_utc", _token(self.as_of_utc, "as_of_utc"))


@dataclass(frozen=True)
class HistoricalToolProvenance:
    """First-class evidence projection. Not current operational state."""

    temporal_scope: TemporalScope
    freshness_status: FreshnessStatus | None
    freshness_limitations: tuple[str, ...]
    query_ids: tuple[str, ...]
    execution_ids: tuple[str, ...]
    engine_scopes: tuple[str, ...]
    completeness_status: str | None
    completeness_reason: str | None
    evaluated_as_of_utc: str | None
    source_versions: tuple[str | None, ...]
    represented_as_current_observation: bool
    verified_zero_from_empty_uncertified: bool


@dataclass(frozen=True)
class Sprint4HistoricalToolOutcome:
    """One host-authorized historical tool attempt."""

    tool_name: str
    tool_call_id: str
    executed_arguments: dict[str, Any]
    tool_result: ToolResult | None
    provenance: HistoricalToolProvenance | None
    refusal: HistoricalToolRefusal | None
    live_athena_initiated: bool
    production_query: bool


def _refuse(
    *,
    tool_name: str,
    tool_call_id: str,
    executed_arguments: Mapping[str, Any],
    refusal: HistoricalToolRefusal,
    live_athena_initiated: bool,
) -> Sprint4HistoricalToolOutcome:
    return Sprint4HistoricalToolOutcome(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        executed_arguments=dict(executed_arguments),
        tool_result=None,
        provenance=None,
        refusal=refusal,
        live_athena_initiated=live_athena_initiated,
        production_query=False,
    )


def inspect_historical_tool_provenance(result: ToolResult) -> HistoricalToolProvenance:
    """Copy source lineage from Evidence. Do not invent current state."""

    if not isinstance(result, ToolResult):
        raise ContractValidationError("invalid_historical_tool_result")
    query_ids: list[str] = []
    execution_ids: list[str] = []
    engine_scopes: list[str] = []
    source_versions: list[str | None] = []
    freshness_status: FreshnessStatus | None = None
    completeness_status: str | None = None
    completeness_reason: str | None = None
    evaluated_as_of_utc: str | None = result.as_of_utc
    freshness_limitations: list[str] = []
    for evidence in result.evidence:
        freshness_status = evidence.freshness_status
        freshness_limitations.extend(evidence.limitations)
        if evidence.completeness is not None:
            completeness_status = evidence.completeness.status
            completeness_reason = evidence.completeness.reason
            evaluated_as_of_utc = evidence.completeness.evaluated_as_of_utc
        for trace in evidence.query_executions:
            query_ids.append(trace.query_id)
            if isinstance(trace.execution_id, str):
                execution_ids.append(trace.execution_id)
            if isinstance(trace.engine_scope, str):
                engine_scopes.append(trace.engine_scope)
        for record in evidence.source_records:
            source_versions.append(record.source_version)
    represented_as_current = (
        result.temporal_scope is TemporalScope.CURRENT
        or freshness_status is FreshnessStatus.FRESH
        or HISTORICAL_FACTS_ARE_CURRENT_OBSERVATIONS is True
    )
    uncertified_zero = (
        result.status is ToolResultStatus.NOT_FOUND
        and completeness_status != "EVALUABLE"
    )
    return HistoricalToolProvenance(
        temporal_scope=result.temporal_scope,
        freshness_status=freshness_status,
        freshness_limitations=tuple(dict.fromkeys(freshness_limitations)),
        query_ids=tuple(query_ids),
        execution_ids=tuple(execution_ids),
        engine_scopes=tuple(engine_scopes),
        completeness_status=completeness_status,
        completeness_reason=completeness_reason,
        evaluated_as_of_utc=evaluated_as_of_utc,
        source_versions=tuple(source_versions),
        represented_as_current_observation=represented_as_current,
        verified_zero_from_empty_uncertified=uncertified_zero,
    )


def _prepare_arguments(
    tool_name: str,
    arguments: Mapping[str, Any],
    window: HostAuthorizedHistoricalWindow,
) -> tuple[dict[str, Any], HistoricalToolRefusal | None]:
    spec = _CATALOG.get(tool_name)
    if spec is None:
        return {}, HistoricalToolRefusal.UNKNOWN_HISTORICAL_TOOL
    allowed = {field.name: field for field in spec.input_fields}
    for argument_name in arguments:
        folded = argument_name.casefold()
        if argument_name in _TRUSTED_ARGUMENT_NAMES or folded in _TRUSTED_ARGUMENT_NAMES:
            if folded in _SQL_ARGUMENT_NAMES or argument_name in _FORBIDDEN_REQUEST_FIELDS:
                return {}, HistoricalToolRefusal.ARBITRARY_SQL
            return {}, HistoricalToolRefusal.TRUSTED_ARGUMENT_FORBIDDEN
        if argument_name in _FORBIDDEN_REQUEST_FIELDS or folded in _SQL_ARGUMENT_NAMES:
            return {}, HistoricalToolRefusal.ARBITRARY_SQL
        if argument_name not in allowed:
            return {}, HistoricalToolRefusal.UNKNOWN_ARGUMENT
    for field in spec.input_fields:
        if field.required and field.name not in arguments:
            return {}, HistoricalToolRefusal.MISSING_REQUIRED_ARGUMENT
    executed: dict[str, Any] = {}
    for argument_name, value in arguments.items():
        field = allowed[argument_name]
        if field.value_type is ToolInputValueType.STRING:
            if not isinstance(value, str):
                return {}, HistoricalToolRefusal.INVALID_ARGUMENT_TYPE
        elif field.value_type is ToolInputValueType.INTEGER:
            if not isinstance(value, int) or isinstance(value, bool):
                return {}, HistoricalToolRefusal.INVALID_ARGUMENT_TYPE
        else:
            return {}, HistoricalToolRefusal.INVALID_ARGUMENT_TYPE
        executed[argument_name] = value
    start = executed.get("start_utc")
    end = executed.get("end_utc")
    if start != window.eligible_start_utc or end != window.eligible_end_utc:
        return executed, HistoricalToolRefusal.HISTORICAL_WINDOW_NOT_AUTHORIZED
    if (start, end) == (window.unbound_plan_start_utc, window.unbound_plan_end_utc):
        return executed, HistoricalToolRefusal.UNBOUND_PLAN_WINDOW_NOT_TOOL_ARGUMENT
    try:
        start_dt = _whole_second(start, "start_utc")
        end_dt = _whole_second(end, "end_utc")
    except ContractValidationError:
        return executed, HistoricalToolRefusal.HISTORICAL_WINDOW_NOT_AUTHORIZED
    if end_dt <= start_dt:
        return executed, HistoricalToolRefusal.HISTORICAL_WINDOW_NOT_AUTHORIZED
    if end_dt - start_dt > MAX_QUERY_WINDOW:
        return executed, HistoricalToolRefusal.HISTORICAL_INTERVAL_ESCALATION
    if spec.name == LIST_HISTORICAL_ENCOUNTERS:
        if "limit" in executed:
            limit = executed["limit"]
            if limit < AI_LIST_MIN or limit > AI_LIST_MAX:
                return executed, HistoricalToolRefusal.LIST_LIMIT_EXCEEDED
        else:
            executed["limit"] = AI_LIST_DEFAULT
    return executed, None


def _guard_result(
    result: ToolResult,
    provenance: HistoricalToolProvenance,
) -> HistoricalToolRefusal | None:
    if provenance.represented_as_current_observation:
        return HistoricalToolRefusal.CURRENT_OBSERVATION_CLAIMED
    if result.temporal_scope is not TemporalScope.HISTORICAL:
        return HistoricalToolRefusal.CURRENT_OBSERVATION_CLAIMED
    if HISTORICAL_FRESHNESS_NOT_ESTABLISHED not in result.limitations and result.evidence:
        if provenance.freshness_status is not FreshnessStatus.UNKNOWN:
            return HistoricalToolRefusal.CURRENT_OBSERVATION_CLAIMED
    if provenance.verified_zero_from_empty_uncertified:
        return HistoricalToolRefusal.EMPTY_PAGE_IS_NOT_VERIFIED_ZERO
    data_status = None
    if isinstance(result.data, dict):
        data_status = result.data.get("status")
    if data_status == "VERIFIED_ZERO" and provenance.completeness_status != "EVALUABLE":
        return HistoricalToolRefusal.EMPTY_PAGE_IS_NOT_VERIFIED_ZERO
    if data_status in {"COVERAGE_BLOCKED", "COVERAGE_STORE_UNAVAILABLE"}:
        if result.status is ToolResultStatus.NOT_FOUND:
            return HistoricalToolRefusal.COVERAGE_BLOCKED_IS_NOT_ZERO
    return None


class Sprint4HistoricalToolPort:
    """Invoke one catalog historical tool against the composed query runtime."""

    def __init__(self, runtime: Sprint4HistoricalQueryRuntime) -> None:
        _closed()
        if type(runtime) is not Sprint4HistoricalQueryRuntime:
            raise ContractValidationError("invalid_historical_query_runtime")
        if not isinstance(runtime.operations, HistoricalAnalyticsOperations):
            raise ContractValidationError("invalid_historical_operations")
        if runtime.live_athena_initiated is not False:
            raise ContractValidationError("live_athena_not_initiated")
        self._runtime = runtime

    @property
    def runtime(self) -> Sprint4HistoricalQueryRuntime:
        return self._runtime

    def invoke(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        tool_call_id: str,
        window: HostAuthorizedHistoricalWindow,
    ) -> Sprint4HistoricalToolOutcome:
        _closed()
        call_id = _token(tool_call_id, "tool_call_id")
        live = self._runtime.live_athena_initiated
        if live is not False:
            return _refuse(
                tool_name=str(tool_name),
                tool_call_id=call_id,
                executed_arguments=dict(arguments),
                refusal=HistoricalToolRefusal.LIVE_ATHENA_NOT_INITIATED,
                live_athena_initiated=True,
            )
        if not isinstance(window, HostAuthorizedHistoricalWindow):
            raise ContractValidationError("invalid_host_historical_window")
        executed, refusal = _prepare_arguments(tool_name, arguments, window)
        if refusal is not None:
            return _refuse(
                tool_name=str(tool_name),
                tool_call_id=call_id,
                executed_arguments=executed or dict(arguments),
                refusal=refusal,
                live_athena_initiated=False,
            )
        call = HistoricalAnalyticsCall(
            operations=self._runtime.operations,
            as_of_utc=window.as_of_utc,
            tool_call_id=call_id,
        )
        adapter = HistoricalAnalyticsAdapter(call)
        handler = adapter.get_handler(tool_name)
        result = handler(**executed)
        provenance = inspect_historical_tool_provenance(result)
        guarded = _guard_result(result, provenance)
        if guarded is not None:
            return _refuse(
                tool_name=tool_name,
                tool_call_id=call_id,
                executed_arguments=executed,
                refusal=guarded,
                live_athena_initiated=False,
            )
        return Sprint4HistoricalToolOutcome(
            tool_name=tool_name,
            tool_call_id=call_id,
            executed_arguments=executed,
            tool_result=result,
            provenance=provenance,
            refusal=None,
            live_athena_initiated=False,
            production_query=False,
        )


def bind_sprint4_historical_tool_port(
    runtime: Sprint4HistoricalQueryRuntime,
) -> Sprint4HistoricalToolPort:
    """Bind the four historical tools to one composed offline query runtime."""

    return Sprint4HistoricalToolPort(runtime)


__all__ = [
    "LIST_HISTORICAL_ENCOUNTERS",
    "SPRINT4_HISTORICAL_TOOL_PORT_SCHEMA_VERSION",
    "HistoricalToolProvenance",
    "HistoricalToolRefusal",
    "HostAuthorizedHistoricalWindow",
    "Sprint4HistoricalToolOutcome",
    "Sprint4HistoricalToolPort",
    "bind_sprint4_historical_tool_port",
    "inspect_historical_tool_provenance",
]
