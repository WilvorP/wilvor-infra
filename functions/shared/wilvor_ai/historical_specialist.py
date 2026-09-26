"""Bounded Historical Analytics Specialist orchestration (Phase 3A.2).

This module runs a provider-neutral ModelProvider loop around the bound
historical adapter. It does not verify claims, render factual answers,
create AWS clients, or import provider SDKs.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from wilvor_ai.contracts import ToolInputValueType, ToolResult
from wilvor_ai.historical_analytics import (
    HISTORICAL_ANALYTICS_TOOLS,
    HistoricalAnalyticsAdapter,
    HistoricalAnalyticsCall,
    historical_analytics_tool_schemas,
)
from wilvor_ai.model_contracts import (
    ModelDecision,
    ModelDecisionKind,
    ModelProvider,
    ModelTurnRequest,
    ProposedToolCall,
    ValidationFeedback,
    ValidationFeedbackCode,
)
from wilvor_ai.specialist_contracts import (
    HistoricalSpecialistTrustedContext,
    SpecialistRequest,
    UnsupportedReason,
)
from wilvor_ai.specialist_runtime_contracts import (
    ExecutedToolInvocation,
    HistoricalSpecialistRunResult,
    SpecialistRunStatus,
    SpecialistRuntimeErrorCode,
)
from wilvor_ai.tool_result_projection import (
    ToolResultProjection,
    ToolResultProjectionError,
    project_tool_result,
)
from wilvor_ai.tool_schema import FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES


MAX_MODEL_TURNS = 3
MAX_TOOL_CALLS = 2
MAX_INVALID_TOOL_CALL_CORRECTIONS = 1
MAX_IDENTICAL_TOOL_CALL_EXECUTIONS = 1
AI_LIST_DEFAULT = 20
AI_LIST_MIN = 1
AI_LIST_MAX = 25
LIST_HISTORICAL_ENCOUNTERS = "list_historical_encounters"
HISTORICAL_SPECIALIST_INSTRUCTION_REF = "wilvor.historical.specialist.v1"
HISTORICAL_SPECIALIST_INSTRUCTION_RULES = (
    "historical_analytics_only",
    "use_only_supplied_tools",
    "do_not_invent_tool_results",
    "as_of_is_runtime_controlled",
    "coverage_blocked_is_not_zero",
    "partial_is_not_exact",
    "materialization_is_not_validity",
    "no_geography_or_current_state_inference",
    "return_typed_decision_only",
    "final_claims_are_structured_not_prose",
)
_CATALOG_BY_NAME = {item.name: item for item in HISTORICAL_ANALYTICS_TOOLS}
_FORBIDDEN_ARGUMENTS = frozenset(FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES)


class ToolCallIdFactory(Protocol):
    """Produces one runtime tool_call_id per executed historical call."""

    def __call__(self) -> str: ...


class SequentialToolCallIdFactory:
    """Deterministic IDs for tests: ``{prefix}-1``, ``{prefix}-2``, ..."""

    def __init__(self, prefix: str = "historical-call") -> None:
        if not isinstance(prefix, str) or not prefix.strip():
            raise ValueError("prefix is required")
        self._prefix = prefix
        self._n = 0

    def __call__(self) -> str:
        self._n += 1
        return f"{self._prefix}-{self._n}"


def _default_tool_call_id_factory() -> SequentialToolCallIdFactory:
    return SequentialToolCallIdFactory(prefix=f"historical-call-{uuid.uuid4().hex}")


@dataclass(frozen=True)
class _PreparedCall:
    name: str
    requested_arguments: dict[str, Any]
    executed_arguments: dict[str, Any]
    list_limit_default_applied: bool
    canonical_key: str


def canonical_tool_call_key(
    name: str,
    arguments: Mapping[str, Any],
) -> str:
    """Stable identity from exact tool name and executable arguments."""

    return json.dumps(
        {"name": name, "arguments": dict(arguments)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _type_matches(value_type: ToolInputValueType, value: Any) -> bool:
    if value_type is ToolInputValueType.STRING:
        return isinstance(value, str)
    if value_type is ToolInputValueType.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if value_type is ToolInputValueType.NUMBER:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if value_type is ToolInputValueType.BOOLEAN:
        return isinstance(value, bool)
    if value_type is ToolInputValueType.OBJECT:
        return isinstance(value, dict)
    if value_type is ToolInputValueType.ARRAY:
        return isinstance(value, list)
    return False


def _validate_proposed_call(proposed: ProposedToolCall) -> ValidationFeedback | _PreparedCall:
    if proposed.name not in _CATALOG_BY_NAME:
        return ValidationFeedback(
            code=ValidationFeedbackCode.UNKNOWN_TOOL,
            tool_name=proposed.name,
        )
    spec = _CATALOG_BY_NAME[proposed.name]
    allowed = {field.name: field for field in spec.input_fields}

    for argument_name in proposed.arguments:
        if argument_name.casefold() in _FORBIDDEN_ARGUMENTS:
            return ValidationFeedback(
                code=ValidationFeedbackCode.TRUSTED_ARGUMENT_FORBIDDEN,
                tool_name=proposed.name,
                argument_name=argument_name,
            )
        if argument_name not in allowed:
            return ValidationFeedback(
                code=ValidationFeedbackCode.UNKNOWN_ARGUMENT,
                tool_name=proposed.name,
                argument_name=argument_name,
            )

    for field in spec.input_fields:
        if field.required and field.name not in proposed.arguments:
            return ValidationFeedback(
                code=ValidationFeedbackCode.MISSING_REQUIRED_ARGUMENT,
                tool_name=proposed.name,
                argument_name=field.name,
            )

    for argument_name, value in proposed.arguments.items():
        if not _type_matches(allowed[argument_name].value_type, value):
            return ValidationFeedback(
                code=ValidationFeedbackCode.INVALID_ARGUMENT_TYPE,
                tool_name=proposed.name,
                argument_name=argument_name,
            )

    executed = dict(proposed.arguments)
    default_applied = False
    if spec.name == LIST_HISTORICAL_ENCOUNTERS:
        if "limit" in executed:
            limit = executed["limit"]
            if limit < AI_LIST_MIN or limit > AI_LIST_MAX:
                return ValidationFeedback(
                    code=ValidationFeedbackCode.LIST_LIMIT_EXCEEDED,
                    tool_name=spec.name,
                    argument_name="limit",
                )
        else:
            executed["limit"] = AI_LIST_DEFAULT
            default_applied = True

    return _PreparedCall(
        name=spec.name,
        requested_arguments=dict(proposed.arguments),
        executed_arguments=executed,
        list_limit_default_applied=default_applied,
        canonical_key=canonical_tool_call_key(spec.name, executed),
    )


def _validate_batch(
    proposed_calls: tuple[ProposedToolCall, ...],
    *,
    remaining_budget: int,
    executed_keys: set[str],
) -> tuple[tuple[_PreparedCall, ...] | None, ValidationFeedback | None]:
    if len(proposed_calls) > remaining_budget:
        return (
            None,
            ValidationFeedback(code=ValidationFeedbackCode.TOOL_CALL_BUDGET_EXCEEDED),
        )

    prepared: list[_PreparedCall] = []
    seen_keys: set[str] = set()
    for proposed in proposed_calls:
        outcome = _validate_proposed_call(proposed)
        if isinstance(outcome, ValidationFeedback):
            return None, outcome
        if (
            outcome.canonical_key in executed_keys
            or outcome.canonical_key in seen_keys
        ):
            return (
                None,
                ValidationFeedback(
                    code=ValidationFeedbackCode.DUPLICATE_TOOL_CALL,
                    tool_name=outcome.name,
                ),
            )
        seen_keys.add(outcome.canonical_key)
        prepared.append(outcome)
    return tuple(prepared), None


def _evaluated_as_of(
    tool_results: tuple[ToolResult, ...],
    trusted_as_of_utc: str,
) -> str | None | SpecialistRuntimeErrorCode:
    populated = tuple(
        item.as_of_utc for item in tool_results if item.as_of_utc is not None
    )
    if not populated:
        return None
    unique = set(populated)
    if len(unique) != 1 or next(iter(unique)) != trusted_as_of_utc:
        return SpecialistRuntimeErrorCode.AS_OF_MISMATCH
    return populated[0]


class HistoricalAnalyticsSpecialist:
    """Bounded historical specialist. Constructor owns provider and operations."""

    def __init__(
        self,
        *,
        provider: ModelProvider,
        operations: Any,
        tool_call_id_factory: ToolCallIdFactory | None = None,
    ) -> None:
        if provider is None or not callable(getattr(provider, "complete", None)):
            raise TypeError("provider must implement ModelProvider.complete")
        if operations is None:
            raise TypeError("operations is required")
        self._provider = provider
        self._operations = operations
        self._tool_call_id_factory: ToolCallIdFactory = (
            tool_call_id_factory
            if tool_call_id_factory is not None
            else _default_tool_call_id_factory()
        )

    def run(
        self,
        request: SpecialistRequest,
        context: HistoricalSpecialistTrustedContext,
    ) -> HistoricalSpecialistRunResult:
        if not isinstance(request, SpecialistRequest):
            raise TypeError("request must be a SpecialistRequest")
        if not isinstance(context, HistoricalSpecialistTrustedContext):
            raise TypeError("context must be a HistoricalSpecialistTrustedContext")

        schemas = historical_analytics_tool_schemas()
        tool_results: list[ToolResult] = []
        projections: list[ToolResultProjection] = []
        invocations: list[ExecutedToolInvocation] = []
        executed_keys: set[str] = set()
        feedback_history: list[ValidationFeedback] = []
        pending_feedback: ValidationFeedback | None = None
        invalid_corrections = 0
        provider_turns = 0
        last_kind: ModelDecisionKind | None = None
        evaluated_as_of: str | None = None

        while provider_turns < MAX_MODEL_TURNS:
            turn = ModelTurnRequest(
                user_text=request.text,
                instruction_ref=HISTORICAL_SPECIALIST_INSTRUCTION_REF,
                tools=schemas,
                tool_results=tuple(projections),
                validation_feedback=pending_feedback,
            )
            pending_feedback = None
            try:
                decision = self._provider.complete(turn)
            except Exception:
                return self._finish(
                    status=SpecialistRunStatus.PROVIDER_FAILED,
                    tool_results=tool_results,
                    projections=projections,
                    invocations=invocations,
                    feedback_history=feedback_history,
                    evaluated_as_of=evaluated_as_of,
                    provider_turns=provider_turns,
                    runtime_errors=(
                        SpecialistRuntimeErrorCode.PROVIDER_EXCEPTION.value,
                    ),
                    terminal_kind=last_kind,
                )
            provider_turns += 1

            if not isinstance(decision, ModelDecision):
                return self._finish(
                    status=SpecialistRunStatus.PROVIDER_FAILED,
                    tool_results=tool_results,
                    projections=projections,
                    invocations=invocations,
                    feedback_history=feedback_history,
                    evaluated_as_of=evaluated_as_of,
                    provider_turns=provider_turns,
                    runtime_errors=(
                        SpecialistRuntimeErrorCode.INVALID_MODEL_DECISION.value,
                    ),
                    terminal_kind=last_kind,
                )
            last_kind = decision.kind

            if decision.kind is ModelDecisionKind.FINAL_CLAIMS:
                return self._finish(
                    status=SpecialistRunStatus.PROPOSED_CLAIMS,
                    tool_results=tool_results,
                    projections=projections,
                    invocations=invocations,
                    feedback_history=feedback_history,
                    evaluated_as_of=evaluated_as_of,
                    provider_turns=provider_turns,
                    proposed_claims=decision.claims,
                    terminal_kind=decision.kind,
                )
            if decision.kind is ModelDecisionKind.UNSUPPORTED:
                return self._finish(
                    status=SpecialistRunStatus.UNSUPPORTED,
                    tool_results=tool_results,
                    projections=projections,
                    invocations=invocations,
                    feedback_history=feedback_history,
                    evaluated_as_of=evaluated_as_of,
                    provider_turns=provider_turns,
                    unsupported_reason=decision.unsupported_reason,
                    terminal_kind=decision.kind,
                )
            if decision.kind is ModelDecisionKind.REFUSAL:
                return self._finish(
                    status=SpecialistRunStatus.PROVIDER_FAILED,
                    tool_results=tool_results,
                    projections=projections,
                    invocations=invocations,
                    feedback_history=feedback_history,
                    evaluated_as_of=evaluated_as_of,
                    provider_turns=provider_turns,
                    runtime_errors=(SpecialistRuntimeErrorCode.REFUSAL.value,),
                    terminal_kind=decision.kind,
                    refusal_code=decision.refusal_code,
                )

            prepared, feedback = _validate_batch(
                decision.tool_calls,
                remaining_budget=MAX_TOOL_CALLS - len(invocations),
                executed_keys=executed_keys,
            )
            if feedback is not None:
                feedback_history.append(feedback)
                if invalid_corrections >= MAX_INVALID_TOOL_CALL_CORRECTIONS:
                    return self._finish(
                        status=SpecialistRunStatus.INVALID_REQUEST,
                        tool_results=tool_results,
                        projections=projections,
                        invocations=invocations,
                        feedback_history=feedback_history,
                        evaluated_as_of=evaluated_as_of,
                        provider_turns=provider_turns,
                        terminal_kind=decision.kind,
                    )
                invalid_corrections += 1
                pending_feedback = feedback
                continue

            assert prepared is not None
            for item in prepared:
                outcome = self._execute_prepared(item, context.as_of_utc)
                if isinstance(outcome, HistoricalSpecialistRunResult):
                    merged_results = tool_results + list(outcome.tool_results)
                    merged_projections = projections + list(
                        outcome.tool_result_projections
                    )
                    merged_invocations = invocations + list(
                        outcome.executed_invocations
                    )
                    derived = _evaluated_as_of(
                        tuple(merged_results),
                        context.as_of_utc,
                    )
                    errors = outcome.runtime_errors
                    if isinstance(derived, SpecialistRuntimeErrorCode):
                        errors = tuple(dict.fromkeys((*errors, derived.value)))
                        derived = None
                    return self._finish(
                        status=SpecialistRunStatus.UNAVAILABLE,
                        tool_results=merged_results,
                        projections=merged_projections,
                        invocations=merged_invocations,
                        feedback_history=feedback_history,
                        evaluated_as_of=derived,
                        provider_turns=provider_turns,
                        runtime_errors=errors,
                        terminal_kind=decision.kind,
                    )
                tool_result, projection, invocation = outcome
                tool_results.append(tool_result)
                invocations.append(invocation)
                executed_keys.add(invocation.canonical_key)
                projections.append(projection)
                derived = _evaluated_as_of(tuple(tool_results), context.as_of_utc)
                if isinstance(derived, SpecialistRuntimeErrorCode):
                    return self._finish(
                        status=SpecialistRunStatus.UNAVAILABLE,
                        tool_results=tool_results,
                        projections=projections,
                        invocations=invocations,
                        feedback_history=feedback_history,
                        evaluated_as_of=None,
                        provider_turns=provider_turns,
                        runtime_errors=(derived.value,),
                        terminal_kind=decision.kind,
                    )
                evaluated_as_of = derived

        return self._finish(
            status=SpecialistRunStatus.PROVIDER_FAILED,
            tool_results=tool_results,
            projections=projections,
            invocations=invocations,
            feedback_history=feedback_history,
            evaluated_as_of=evaluated_as_of,
            provider_turns=provider_turns,
            runtime_errors=(
                SpecialistRuntimeErrorCode.MODEL_TURN_LIMIT_EXCEEDED.value,
            ),
            terminal_kind=last_kind,
        )

    def _execute_prepared(
        self,
        prepared: _PreparedCall,
        as_of_utc: str,
    ) -> (
        tuple[ToolResult, ToolResultProjection, ExecutedToolInvocation]
        | HistoricalSpecialistRunResult
    ):
        tool_call_id = self._tool_call_id_factory()
        call = HistoricalAnalyticsCall(
            operations=self._operations,
            as_of_utc=as_of_utc,
            tool_call_id=tool_call_id,
        )
        adapter = HistoricalAnalyticsAdapter(call)
        handler = adapter.get_handler(prepared.name)
        try:
            tool_result = handler(**prepared.executed_arguments)
        except Exception:
            return self._finish(
                status=SpecialistRunStatus.UNAVAILABLE,
                tool_results=[],
                projections=[],
                invocations=[],
                feedback_history=[],
                evaluated_as_of=None,
                provider_turns=0,
                runtime_errors=(
                    SpecialistRuntimeErrorCode.ADAPTER_EXECUTION_FAILED.value,
                ),
            )
        invocation = ExecutedToolInvocation(
            tool_name=prepared.name,
            tool_call_id=tool_call_id,
            requested_arguments=prepared.requested_arguments,
            executed_arguments=prepared.executed_arguments,
            list_limit_default_applied=prepared.list_limit_default_applied,
            canonical_key=prepared.canonical_key,
        )
        try:
            projection = project_tool_result(tool_result)
        except ToolResultProjectionError:
            return self._finish(
                status=SpecialistRunStatus.UNAVAILABLE,
                tool_results=[tool_result],
                projections=[],
                invocations=[invocation],
                feedback_history=[],
                evaluated_as_of=None,
                provider_turns=0,
                runtime_errors=(
                    SpecialistRuntimeErrorCode.PROJECTION_INTEGRITY_FAILED.value,
                ),
            )
        return tool_result, projection, invocation

    def _finish(
        self,
        *,
        status: SpecialistRunStatus,
        tool_results: list[ToolResult],
        projections: list[ToolResultProjection],
        invocations: list[ExecutedToolInvocation],
        feedback_history: list[ValidationFeedback],
        evaluated_as_of: str | None,
        provider_turns: int,
        runtime_errors: tuple[str, ...] = (),
        proposed_claims: tuple[Any, ...] = (),
        unsupported_reason: UnsupportedReason | None = None,
        terminal_kind: ModelDecisionKind | None = None,
        refusal_code: str | None = None,
    ) -> HistoricalSpecialistRunResult:
        return HistoricalSpecialistRunResult(
            status=status,
            tool_results=tuple(tool_results),
            tool_result_projections=tuple(projections),
            executed_invocations=tuple(invocations),
            proposed_claims=proposed_claims,
            unsupported_reason=unsupported_reason,
            evaluated_as_of_utc=evaluated_as_of,
            runtime_errors=runtime_errors,
            provider_turn_count=provider_turns,
            executed_tool_call_count=len(invocations),
            validation_feedback=tuple(feedback_history),
            terminal_kind=terminal_kind,
            refusal_code=refusal_code,
        )


__all__ = [
    "AI_LIST_DEFAULT",
    "AI_LIST_MAX",
    "AI_LIST_MIN",
    "HISTORICAL_SPECIALIST_INSTRUCTION_REF",
    "HISTORICAL_SPECIALIST_INSTRUCTION_RULES",
    "HistoricalAnalyticsSpecialist",
    "LIST_HISTORICAL_ENCOUNTERS",
    "MAX_IDENTICAL_TOOL_CALL_EXECUTIONS",
    "MAX_INVALID_TOOL_CALL_CORRECTIONS",
    "MAX_MODEL_TURNS",
    "MAX_TOOL_CALLS",
    "SequentialToolCallIdFactory",
    "canonical_tool_call_key",
]
