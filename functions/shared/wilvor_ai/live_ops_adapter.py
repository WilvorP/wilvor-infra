"""Bound Live Ops dispatch with trusted identity and pre-execution validation.

This module validates model-proposed tool calls, assigns trusted
tool_call_id values, and invokes the eight approved Live Ops functions.
It does not run a specialist loop, verify claims, or call a model.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from wilvor_ai.contracts import (
    ContractValidationError,
    JsonValue,
    TemporalScope,
    ToolInputValueType,
    ToolResult,
)
from wilvor_ai.live_ops import LIVE_OPS_TOOLS, LiveOpsCall
from wilvor_ai.live_ops import (
    find_aircraft_by_callsign,
    get_aircraft_operational_context,
    get_airport_operational_context,
    get_hazard_operational_context,
    get_observed_network_state,
    search_current_encounters,
    search_current_hazards,
    search_current_impacts,
)
from wilvor_ai.live_ops_model_contracts import (
    LIVE_OPS_ARGUMENT_STRING_MAX,
    LIVE_OPS_HAZARD_IDS_MAX,
    MAX_TOOL_CALLS_PER_BATCH,
    LiveOpsValidationCode,
    LiveOpsValidationFailure,
)
from wilvor_ai.live_ops_specialist_runtime_contracts import (
    LIVE_OPS_CANONICAL_KEY_MAX_LENGTH,
    LiveOpsDispatchResult,
    LiveOpsDispatchStatus,
    LiveOpsPreparedCall,
    LiveOpsRuntimeErrorCode,
    LiveOpsSpecialistTrustedContext,
    LiveOpsToolInvocation,
)
from wilvor_ai.live_ops_tool_projection import (
    LiveOpsProjectionError,
    project_live_ops_tool_result,
)
from wilvor_ai.model_contracts import ProposedToolCall
from wilvor_ai.tool_schema import FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES


ToolHandler = Callable[..., ToolResult]

_CATALOG = {item.name: item for item in LIVE_OPS_TOOLS}
_DEFAULT_HANDLERS: dict[str, ToolHandler] = {
    "search_current_hazards": search_current_hazards,
    "search_current_impacts": search_current_impacts,
    "search_current_encounters": search_current_encounters,
    "get_observed_network_state": get_observed_network_state,
    "get_aircraft_operational_context": get_aircraft_operational_context,
    "get_hazard_operational_context": get_hazard_operational_context,
    "get_airport_operational_context": get_airport_operational_context,
    "find_aircraft_by_callsign": find_aircraft_by_callsign,
}
_ID_FIELDS = frozenset({"aircraft_id", "hazard_id", "airport_id"})
_UPPER_FIELDS = frozenset({"callsign", "product_type", "hazard_type", "airport_id"})
_LOWER_FIELDS = frozenset({"aircraft_id"})


class LiveOpsAdapter:
    """Validate a bounded batch, assign trusted ids, then dispatch sequentially."""

    def __init__(
        self,
        context: LiveOpsSpecialistTrustedContext,
        *,
        handlers: Mapping[str, ToolHandler] | None = None,
    ) -> None:
        if not isinstance(context, LiveOpsSpecialistTrustedContext):
            raise TypeError("context must be a LiveOpsSpecialistTrustedContext")
        self._context = context
        self._handlers = dict(_DEFAULT_HANDLERS)
        if handlers is not None:
            self._handlers.update(handlers)
        self._used_tool_call_ids: set[str] = set()

    def dispatch(
        self,
        proposed: Sequence[ProposedToolCall],
        *,
        tool_call_id_factory: Callable[[], str],
    ) -> LiveOpsDispatchResult:
        if not callable(tool_call_id_factory):
            return _execution_failed(
                LiveOpsRuntimeErrorCode.TOOL_CALL_ID_FACTORY_FAILED
            )

        validated = _validate_batch(proposed)
        if isinstance(validated, LiveOpsValidationFailure):
            return LiveOpsDispatchResult(
                status=LiveOpsDispatchStatus.VALIDATION_REJECTED,
                validation_failure=validated,
            )

        planned_ids = _collect_tool_call_ids(
            count=len(validated),
            factory=tool_call_id_factory,
            used=self._used_tool_call_ids,
        )
        if isinstance(planned_ids, LiveOpsDispatchResult):
            return planned_ids

        self._used_tool_call_ids.update(planned_ids)
        invocations: list[LiveOpsToolInvocation] = []
        for prepared, tool_call_id in zip(validated, planned_ids, strict=True):
            outcome = self._execute_one(prepared, tool_call_id)
            if isinstance(outcome, LiveOpsDispatchResult):
                return LiveOpsDispatchResult(
                    status=LiveOpsDispatchStatus.EXECUTION_FAILED,
                    invocations=tuple(invocations) + outcome.invocations,
                    execution_error=outcome.execution_error,
                    failed_tool_name=outcome.failed_tool_name or prepared.name,
                    failed_tool_call_id=outcome.failed_tool_call_id or tool_call_id,
                    planned_tool_call_ids=planned_ids,
                )
            invocations.append(outcome)

        return LiveOpsDispatchResult(
            status=LiveOpsDispatchStatus.COMPLETED,
            invocations=tuple(invocations),
            planned_tool_call_ids=planned_ids,
        )

    def _execute_one(
        self,
        prepared: LiveOpsPreparedCall,
        tool_call_id: str,
    ) -> LiveOpsToolInvocation | LiveOpsDispatchResult:
        handler = self._handlers.get(prepared.name)
        if handler is None:
            return _execution_failed(
                LiveOpsRuntimeErrorCode.TOOL_EXECUTION_FAILED,
                tool_name=prepared.name,
                tool_call_id=tool_call_id,
            )
        try:
            call = LiveOpsCall(
                tables=self._context.tables,
                now_epoch=self._context.now_epoch,
                tool_call_id=tool_call_id,
                correlation_id=self._context.correlation_id,
            )
            result = handler(call, **prepared.executed_arguments)
        except Exception:
            return _execution_failed(
                LiveOpsRuntimeErrorCode.TOOL_EXECUTION_FAILED,
                tool_name=prepared.name,
                tool_call_id=tool_call_id,
            )

        if not isinstance(result, ToolResult):
            return _execution_failed(
                LiveOpsRuntimeErrorCode.TOOL_RESULT_IDENTITY_MISMATCH,
                tool_name=prepared.name,
                tool_call_id=tool_call_id,
            )
        if (
            result.tool_name != prepared.name
            or result.tool_call_id != tool_call_id
            or result.correlation_id != self._context.correlation_id
            or result.temporal_scope is not TemporalScope.CURRENT
        ):
            return LiveOpsDispatchResult(
                status=LiveOpsDispatchStatus.EXECUTION_FAILED,
                invocations=(
                    LiveOpsToolInvocation(
                        tool_name=prepared.name,
                        tool_call_id=tool_call_id,
                        requested_arguments=prepared.requested_arguments,
                        executed_arguments=prepared.executed_arguments,
                        canonical_key=prepared.canonical_key,
                        raw_tool_result=result,
                        model_projection=None,
                    ),
                ),
                execution_error=LiveOpsRuntimeErrorCode.TOOL_RESULT_IDENTITY_MISMATCH,
                failed_tool_name=prepared.name,
                failed_tool_call_id=tool_call_id,
            )

        try:
            projection = project_live_ops_tool_result(result)
        except (LiveOpsProjectionError, ContractValidationError, TypeError):
            return LiveOpsDispatchResult(
                status=LiveOpsDispatchStatus.EXECUTION_FAILED,
                invocations=(
                    LiveOpsToolInvocation(
                        tool_name=prepared.name,
                        tool_call_id=tool_call_id,
                        requested_arguments=prepared.requested_arguments,
                        executed_arguments=prepared.executed_arguments,
                        canonical_key=prepared.canonical_key,
                        raw_tool_result=result,
                        model_projection=None,
                    ),
                ),
                execution_error=LiveOpsRuntimeErrorCode.PROJECTION_INTEGRITY_FAILED,
                failed_tool_name=prepared.name,
                failed_tool_call_id=tool_call_id,
            )

        return LiveOpsToolInvocation(
            tool_name=prepared.name,
            tool_call_id=tool_call_id,
            requested_arguments=prepared.requested_arguments,
            executed_arguments=prepared.executed_arguments,
            canonical_key=prepared.canonical_key,
            raw_tool_result=result,
            model_projection=projection,
        )


def _validate_batch(
    proposed: Sequence[ProposedToolCall],
) -> tuple[LiveOpsPreparedCall, ...] | LiveOpsValidationFailure:
    if not isinstance(proposed, Sequence) or isinstance(proposed, (str, bytes)):
        return LiveOpsValidationFailure(code=LiveOpsValidationCode.EMPTY_BATCH)
    if len(proposed) == 0:
        return LiveOpsValidationFailure(code=LiveOpsValidationCode.EMPTY_BATCH)
    if len(proposed) > MAX_TOOL_CALLS_PER_BATCH:
        return LiveOpsValidationFailure(code=LiveOpsValidationCode.BATCH_TOO_LARGE)

    prepared: list[LiveOpsPreparedCall] = []
    seen: set[str] = set()
    for item in proposed:
        if not isinstance(item, ProposedToolCall):
            return LiveOpsValidationFailure(code=LiveOpsValidationCode.UNKNOWN_TOOL)
        outcome = _validate_proposed_call(item)
        if isinstance(outcome, LiveOpsValidationFailure):
            return outcome
        if outcome.canonical_key in seen:
            return LiveOpsValidationFailure(
                code=LiveOpsValidationCode.DUPLICATE_TOOL_CALL,
                tool_name=outcome.name,
            )
        seen.add(outcome.canonical_key)
        prepared.append(outcome)
    return tuple(prepared)


def _validate_proposed_call(
    proposed: ProposedToolCall,
) -> LiveOpsPreparedCall | LiveOpsValidationFailure:
    spec = _CATALOG.get(proposed.name)
    if spec is None:
        return LiveOpsValidationFailure(
            code=LiveOpsValidationCode.UNKNOWN_TOOL,
            tool_name=proposed.name,
        )
    if not isinstance(proposed.arguments, Mapping):
        return LiveOpsValidationFailure(
            code=LiveOpsValidationCode.INVALID_ARGUMENT_TYPE,
            tool_name=proposed.name,
        )

    allowed = {field.name: field for field in spec.input_fields}
    for name in proposed.arguments:
        if not isinstance(name, str):
            return LiveOpsValidationFailure(
                code=LiveOpsValidationCode.UNKNOWN_ARGUMENT,
                tool_name=proposed.name,
            )
        if name.casefold() in FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES:
            return LiveOpsValidationFailure(
                code=LiveOpsValidationCode.TRUSTED_ARGUMENT_FORBIDDEN,
                tool_name=proposed.name,
                argument_name=name,
            )
        if name not in allowed:
            return LiveOpsValidationFailure(
                code=LiveOpsValidationCode.UNKNOWN_ARGUMENT,
                tool_name=proposed.name,
                argument_name=name,
            )

    for field in spec.input_fields:
        if field.required and field.name not in proposed.arguments:
            return LiveOpsValidationFailure(
                code=LiveOpsValidationCode.MISSING_REQUIRED_ARGUMENT,
                tool_name=proposed.name,
                argument_name=field.name,
            )

    executed: dict[str, JsonValue] = {}
    for name, value in proposed.arguments.items():
        checked = _validate_argument_value(
            proposed.name,
            allowed[name].value_type,
            name,
            value,
        )
        if isinstance(checked, LiveOpsValidationFailure):
            return checked
        executed[name] = checked

    selector_failure = _validate_selectors(proposed.name, executed)
    if selector_failure is not None:
        return selector_failure

    requested = {
        key: copy_json(value) for key, value in proposed.arguments.items()
    }
    canonical_key = canonical_tool_call_key(proposed.name, executed)
    if len(canonical_key) > LIVE_OPS_CANONICAL_KEY_MAX_LENGTH:
        return LiveOpsValidationFailure(
            code=LiveOpsValidationCode.CANONICAL_KEY_TOO_LARGE,
            tool_name=proposed.name,
        )
    try:
        return LiveOpsPreparedCall(
            name=proposed.name,
            requested_arguments=requested,
            executed_arguments=executed,
            canonical_key=canonical_key,
        )
    except ContractValidationError as exc:
        if "invalid_canonical_key" in exc.errors:
            return LiveOpsValidationFailure(
                code=LiveOpsValidationCode.CANONICAL_KEY_TOO_LARGE,
                tool_name=proposed.name,
            )
        raise


def _validate_argument_value(
    tool_name: str,
    value_type: ToolInputValueType,
    name: str,
    value: Any,
) -> JsonValue | LiveOpsValidationFailure:
    if isinstance(value, (dict,)):
        return LiveOpsValidationFailure(
            code=LiveOpsValidationCode.NESTED_VALUE_FORBIDDEN,
            tool_name=tool_name,
            argument_name=name,
        )
    if value_type is ToolInputValueType.ARRAY:
        return _validate_hazard_ids(tool_name, name, value)
    if value_type is not ToolInputValueType.STRING:
        return LiveOpsValidationFailure(
            code=LiveOpsValidationCode.INVALID_ARGUMENT_TYPE,
            tool_name=tool_name,
            argument_name=name,
        )
    if isinstance(value, bool):
        return LiveOpsValidationFailure(
            code=LiveOpsValidationCode.BOOLEAN_INTEGER_CONFUSION,
            tool_name=tool_name,
            argument_name=name,
        )
    if isinstance(value, (int, float)):
        return LiveOpsValidationFailure(
            code=LiveOpsValidationCode.INVALID_ARGUMENT_TYPE,
            tool_name=tool_name,
            argument_name=name,
        )
    if not isinstance(value, str):
        if isinstance(value, list):
            return LiveOpsValidationFailure(
                code=LiveOpsValidationCode.NESTED_VALUE_FORBIDDEN,
                tool_name=tool_name,
                argument_name=name,
            )
        return LiveOpsValidationFailure(
            code=LiveOpsValidationCode.INVALID_ARGUMENT_TYPE,
            tool_name=tool_name,
            argument_name=name,
        )
    if not value or value != value.strip():
        return LiveOpsValidationFailure(
            code=LiveOpsValidationCode.EMPTY_OR_WHITESPACE,
            tool_name=tool_name,
            argument_name=name,
        )
    if name in _ID_FIELDS and any(character.isspace() for character in value):
        return LiveOpsValidationFailure(
            code=LiveOpsValidationCode.INVALID_ARGUMENT_VALUE,
            tool_name=tool_name,
            argument_name=name,
        )
    if len(value) > LIVE_OPS_ARGUMENT_STRING_MAX:
        return LiveOpsValidationFailure(
            code=LiveOpsValidationCode.STRING_TOO_LONG,
            tool_name=tool_name,
            argument_name=name,
        )
    return _normalize_string(name, value)


def _validate_hazard_ids(
    tool_name: str,
    name: str,
    value: Any,
) -> list[str] | LiveOpsValidationFailure:
    if isinstance(value, bool):
        return LiveOpsValidationFailure(
            code=LiveOpsValidationCode.BOOLEAN_INTEGER_CONFUSION,
            tool_name=tool_name,
            argument_name=name,
        )
    if not isinstance(value, list):
        return LiveOpsValidationFailure(
            code=LiveOpsValidationCode.INVALID_ARGUMENT_TYPE,
            tool_name=tool_name,
            argument_name=name,
        )
    if not value:
        return LiveOpsValidationFailure(
            code=LiveOpsValidationCode.EMPTY_HAZARD_IDS,
            tool_name=tool_name,
            argument_name=name,
        )
    if len(value) > LIVE_OPS_HAZARD_IDS_MAX:
        return LiveOpsValidationFailure(
            code=LiveOpsValidationCode.ARRAY_TOO_LARGE,
            tool_name=tool_name,
            argument_name=name,
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, (dict, list)):
            return LiveOpsValidationFailure(
                code=LiveOpsValidationCode.NESTED_VALUE_FORBIDDEN,
                tool_name=tool_name,
                argument_name=name,
            )
        if not isinstance(item, str):
            return LiveOpsValidationFailure(
                code=LiveOpsValidationCode.INVALID_ARGUMENT_TYPE,
                tool_name=tool_name,
                argument_name=name,
            )
        if not item or item != item.strip() or any(character.isspace() for character in item):
            return LiveOpsValidationFailure(
                code=LiveOpsValidationCode.EMPTY_OR_WHITESPACE,
                tool_name=tool_name,
                argument_name=name,
            )
        if len(item) > LIVE_OPS_ARGUMENT_STRING_MAX:
            return LiveOpsValidationFailure(
                code=LiveOpsValidationCode.STRING_TOO_LONG,
                tool_name=tool_name,
                argument_name=name,
            )
        if item in seen:
            return LiveOpsValidationFailure(
                code=LiveOpsValidationCode.DUPLICATE_HAZARD_ID,
                tool_name=tool_name,
                argument_name=name,
            )
        seen.add(item)
        normalized.append(item)
    return normalized


def _validate_selectors(
    tool_name: str,
    executed: Mapping[str, JsonValue],
) -> LiveOpsValidationFailure | None:
    if tool_name == "search_current_hazards":
        if not _has_region(executed) and not _has_hazard_ids(executed):
            return LiveOpsValidationFailure(
                code=LiveOpsValidationCode.UNBOUNDED_HAZARD_SEARCH,
                tool_name=tool_name,
            )
        return None
    if tool_name == "search_current_impacts":
        if not _has_region(executed) and not _has_hazard_ids(executed):
            return LiveOpsValidationFailure(
                code=LiveOpsValidationCode.MISSING_SELECTOR,
                tool_name=tool_name,
            )
        return None
    if tool_name == "search_current_encounters":
        if not any(
            (
                _present_string(executed, "aircraft_id"),
                _present_string(executed, "callsign"),
                _present_string(executed, "hazard_id"),
                _has_hazard_ids(executed),
            )
        ):
            return LiveOpsValidationFailure(
                code=LiveOpsValidationCode.MISSING_SELECTOR,
                tool_name=tool_name,
            )
    return None


def _normalize_string(name: str, value: str) -> str:
    if name in _LOWER_FIELDS:
        return value.lower()
    if name in _UPPER_FIELDS:
        return value.upper()
    return value


def canonical_tool_call_key(
    name: str,
    executed_arguments: Mapping[str, JsonValue],
) -> str:
    canonical_arguments: dict[str, JsonValue] = {}
    for key, value in executed_arguments.items():
        if key == "hazard_ids" and isinstance(value, list):
            canonical_arguments[key] = sorted(str(item) for item in value)
        elif key == "region" and isinstance(value, str):
            canonical_arguments[key] = " ".join(value.split()).casefold()
        else:
            canonical_arguments[key] = value
    return json.dumps(
        {"name": name, "arguments": canonical_arguments},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _collect_tool_call_ids(
    *,
    count: int,
    factory: Callable[[], str],
    used: set[str],
) -> tuple[str, ...] | LiveOpsDispatchResult:
    planned: list[str] = []
    seen: set[str] = set()
    try:
        for _ in range(count):
            tool_call_id = factory()
            failure = _validate_generated_id(tool_call_id, seen, used)
            if failure is not None:
                return failure
            seen.add(tool_call_id)
            planned.append(tool_call_id)
    except Exception:
        return _execution_failed(LiveOpsRuntimeErrorCode.TOOL_CALL_ID_FACTORY_FAILED)
    return tuple(planned)


def _validate_generated_id(
    tool_call_id: Any,
    seen: set[str],
    used: set[str],
) -> LiveOpsDispatchResult | None:
    if (
        not isinstance(tool_call_id, str)
        or not tool_call_id
        or tool_call_id != tool_call_id.strip()
    ):
        return LiveOpsDispatchResult(
            status=LiveOpsDispatchStatus.EXECUTION_FAILED,
            execution_error=LiveOpsRuntimeErrorCode.TOOL_CALL_ID_FACTORY_FAILED,
            validation_failure=None,
        )
    if tool_call_id in seen or tool_call_id in used:
        return LiveOpsDispatchResult(
            status=LiveOpsDispatchStatus.EXECUTION_FAILED,
            execution_error=LiveOpsRuntimeErrorCode.TOOL_CALL_ID_FACTORY_FAILED,
            failed_tool_call_id=tool_call_id,
        )
    return None


def _execution_failed(
    code: LiveOpsRuntimeErrorCode,
    *,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
) -> LiveOpsDispatchResult:
    return LiveOpsDispatchResult(
        status=LiveOpsDispatchStatus.EXECUTION_FAILED,
        execution_error=code,
        failed_tool_name=tool_name,
        failed_tool_call_id=tool_call_id,
    )


def _has_region(executed: Mapping[str, JsonValue]) -> bool:
    return _present_string(executed, "region")


def _has_hazard_ids(executed: Mapping[str, JsonValue]) -> bool:
    value = executed.get("hazard_ids")
    return isinstance(value, list) and bool(value)


def _present_string(executed: Mapping[str, JsonValue], name: str) -> bool:
    value = executed.get(name)
    return isinstance(value, str) and bool(value)


def copy_json(value: JsonValue) -> JsonValue:
    if isinstance(value, list):
        return [copy_json(item) for item in value]
    if isinstance(value, dict):
        return {key: copy_json(item) for key, item in value.items()}
    return value


__all__ = [
    "LiveOpsAdapter",
    "canonical_tool_call_key",
]
