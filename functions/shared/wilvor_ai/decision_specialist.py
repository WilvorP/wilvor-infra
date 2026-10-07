"""Bounded Decision Expert orchestration.

DE3 dispatches the closed Decision Tools, then DE1 and DE2. It does not
calculate aviation facts, select an encounter, rank a recommendation, or
treat persisted evidence as current.

Terminal status and evidence-collection incompleteness are separate.
``collection_partial_reason`` records a persisted fanout that was skipped
before dispatch. COMPLETED means FINAL_CLAIMS ran through DE1 and DE2 with
no skipped fanout. It does not mean verification passed. PARTIAL means
FINAL_CLAIMS ran through DE1 and DE2 after a skipped fanout. If the run
later ends PROVIDER_FAILED, INVALID_REQUEST, UNSUPPORTED, or UNAVAILABLE,
the collection reason stays for audit and the status is not relabeled
PARTIAL.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from wilvor_ai.contracts import JsonValue, ToolResult, ToolResultStatus
from wilvor_ai.decision_answer_renderer import render_decision_verification
from wilvor_ai.decision_contracts import (
    DecisionEvaluationState,
    validate_decision_tool_result,
)
from wilvor_ai.decision_evidence_verifier import (
    DecisionEvidenceBinding,
    verify_decision_evidence,
)
from wilvor_ai.decision_model_contracts import (
    DecisionEvidenceSnapshot,
    DecisionModelDecision,
    DecisionModelTurnRequest,
)
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionCollectionPartialReason,
    DecisionDispatchOrigin,
    DecisionModelProvider,
    DecisionRuntimeErrorCode,
    DecisionSpecialistRequest,
    DecisionSpecialistRunResult,
    DecisionSpecialistRunStatus,
    DecisionTargetMode,
    DecisionToolInvocationAudit,
)
from wilvor_ai.decision_tools import (
    DECISION_TOOLS,
    DecisionToolInvocation,
    DecisionToolsAdapter,
    DecisionToolsRuntime,
    decision_tool_schemas,
)
from wilvor_ai.model_contracts import (
    ModelDecisionKind,
    ProposedToolCall,
    ValidationFeedback,
    ValidationFeedbackCode,
)
from wilvor_ai.tool_schema import FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES


MAX_MODEL_TURNS = 4
MAX_TOOL_CALLS = 4
MAX_PERSISTED_AIRPORT_CALLS = 3
MAX_INVALID_TOOL_CALL_CORRECTIONS = 1
MAX_IDENTICAL_TOOL_CALL_EXECUTIONS = 1
DECISION_SPECIALIST_INSTRUCTION_REF = "wilvor.decision.specialist.v1"

_REQUIRED_ARGUMENT = {
    spec.name: spec.input_fields[0].name for spec in DECISION_TOOLS
}
_CURRENT_TOOLS = frozenset(
    name for name, argument in _REQUIRED_ARGUMENT.items() if argument == "aircraft_id"
)
_PERSISTED_TOOL = next(
    name
    for name, argument in _REQUIRED_ARGUMENT.items()
    if argument == "recommendation_id"
)
_RECOMMENDATION_TOOL = "get_current_recommendation"


class SequentialDecisionToolCallIdFactory:
    """Injected test ids. Production uses a fresh uuid per execution."""

    def __init__(self, prefix: str = "decision-call") -> None:
        if not isinstance(prefix, str) or not prefix.strip():
            raise ValueError("prefix is required")
        self._prefix = prefix
        self._n = 0

    def __call__(self) -> str:
        self._n += 1
        return f"{self._prefix}-{self._n}"


def _fresh_decision_tool_call_id() -> str:
    return f"decision-call-{uuid.uuid4().hex}"


def canonical_tool_call_key(name: str, arguments: Mapping[str, Any]) -> str:
    """Identity of one actual execution. Not a shared sequence."""

    return json.dumps(
        {"name": name, "arguments": dict(arguments)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


@dataclass(frozen=True)
class _PlannedExecution:
    tool_name: str
    requested_arguments: dict[str, JsonValue]
    executed_arguments: dict[str, JsonValue]
    canonical_key: str
    dispatch_origin: DecisionDispatchOrigin


@dataclass(frozen=True)
class _BatchPlan:
    executions: tuple[_PlannedExecution, ...] | None
    feedback: ValidationFeedback | None
    collection_partial_reason: DecisionCollectionPartialReason | None


def _feedback(
    code: ValidationFeedbackCode,
    tool_name: str | None = None,
    argument_name: str | None = None,
) -> ValidationFeedback:
    return ValidationFeedback(
        code=code,
        tool_name=tool_name,
        argument_name=argument_name,
    )


def _duplicate(tool_name: str) -> ValidationFeedback:
    return _feedback(ValidationFeedbackCode.DUPLICATE_TOOL_CALL, tool_name)


def _budget(tool_name: str | None = None) -> ValidationFeedback:
    return _feedback(ValidationFeedbackCode.TOOL_CALL_BUDGET_EXCEEDED, tool_name)


def _rejected(
    feedback: ValidationFeedback,
    reason: DecisionCollectionPartialReason | None = None,
) -> _BatchPlan:
    return _BatchPlan(None, feedback, reason)


def _validate_proposed_call(
    call: ProposedToolCall,
    request: DecisionSpecialistRequest,
) -> ValidationFeedback | None:
    required = _REQUIRED_ARGUMENT.get(call.name)
    if required is None:
        return _feedback(ValidationFeedbackCode.UNKNOWN_TOOL, call.name)
    for name in call.arguments:
        if name.casefold() in FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES:
            return _feedback(
                ValidationFeedbackCode.TRUSTED_ARGUMENT_FORBIDDEN,
                call.name,
                name,
            )
    for name in call.arguments:
        if name != required:
            return _feedback(
                ValidationFeedbackCode.UNKNOWN_ARGUMENT,
                call.name,
                name,
            )
    if required not in call.arguments:
        return _feedback(
            ValidationFeedbackCode.MISSING_REQUIRED_ARGUMENT,
            call.name,
            required,
        )
    value = call.arguments[required]
    if not isinstance(value, str):
        return _feedback(
            ValidationFeedbackCode.INVALID_ARGUMENT_TYPE,
            call.name,
            required,
        )
    if request.mode is DecisionTargetMode.DIRECT_PERSISTED:
        if call.name in _CURRENT_TOOLS:
            return _feedback(
                ValidationFeedbackCode.UNKNOWN_ARGUMENT,
                call.name,
                "aircraft_id",
            )
        if value != request.recommendation_id:
            return _feedback(
                ValidationFeedbackCode.UNKNOWN_ARGUMENT,
                call.name,
                "recommendation_id",
            )
        return None
    if call.name in _CURRENT_TOOLS and value != request.aircraft_id:
        return _feedback(
            ValidationFeedbackCode.UNKNOWN_ARGUMENT,
            call.name,
            "aircraft_id",
        )
    return None


def _ordinary_plan(call: ProposedToolCall) -> _PlannedExecution:
    arguments = dict(call.arguments)
    return _PlannedExecution(
        tool_name=call.name,
        requested_arguments=arguments,
        executed_arguments=dict(arguments),
        canonical_key=canonical_tool_call_key(call.name, arguments),
        dispatch_origin=DecisionDispatchOrigin.MODEL_REQUEST,
    )


def _expand_persisted_trigger(
    trigger: ProposedToolCall,
    known: tuple[str, ...] | None,
    executed_keys: set[str],
) -> _BatchPlan:
    if known is None or trigger.arguments["recommendation_id"] not in known:
        return _rejected(
            _feedback(
                ValidationFeedbackCode.UNKNOWN_ARGUMENT,
                _PERSISTED_TOOL,
                "recommendation_id",
            )
        )
    requested = dict(trigger.arguments)
    plans: list[_PlannedExecution] = []
    for recommendation_id in known:
        arguments = {"recommendation_id": recommendation_id}
        key = canonical_tool_call_key(_PERSISTED_TOOL, arguments)
        if key in executed_keys:
            return _rejected(_duplicate(_PERSISTED_TOOL))
        plans.append(
            _PlannedExecution(
                tool_name=_PERSISTED_TOOL,
                requested_arguments=dict(requested),
                executed_arguments=arguments,
                canonical_key=key,
                dispatch_origin=DecisionDispatchOrigin.DETERMINISTIC_PERSISTED_FANOUT,
            )
        )
    if len(plans) > MAX_PERSISTED_AIRPORT_CALLS:
        return _rejected(
            _budget(_PERSISTED_TOOL),
            DecisionCollectionPartialReason.PERSISTED_FANOUT_CAP,
        )
    return _BatchPlan(tuple(plans), None, None)


def _plan_batch(
    calls: tuple[ProposedToolCall, ...],
    request: DecisionSpecialistRequest,
    known: tuple[str, ...] | None,
    executed_keys: set[str],
    remaining_budget: int,
) -> _BatchPlan:
    """Validate the full actual execution plan before any adapter call."""

    ordinary: dict[int, _PlannedExecution] = {}
    persisted: list[ProposedToolCall] = []
    seen_keys: set[str] = set()
    for call in calls:
        feedback = _validate_proposed_call(call, request)
        if feedback is not None:
            return _rejected(feedback)
        if (
            request.mode is DecisionTargetMode.AIRCRAFT
            and call.name == _PERSISTED_TOOL
        ):
            persisted.append(call)
            continue
        plan = _ordinary_plan(call)
        if (
            plan.canonical_key in seen_keys
            or plan.canonical_key in executed_keys
        ):
            return _rejected(_duplicate(call.name))
        seen_keys.add(plan.canonical_key)
        ordinary[id(call)] = plan

    if len(persisted) > 1:
        return _rejected(_duplicate(_PERSISTED_TOOL))

    fanout: tuple[_PlannedExecution, ...] = ()
    trigger: ProposedToolCall | None = None
    if persisted:
        trigger = persisted[0]
        expanded = _expand_persisted_trigger(trigger, known, executed_keys)
        if expanded.feedback is not None:
            return expanded
        assert expanded.executions is not None
        fanout = expanded.executions

    planned: list[_PlannedExecution] = []
    for call in calls:
        if trigger is not None and call is trigger:
            planned.extend(fanout)
        else:
            planned.append(ordinary[id(call)])

    if len(planned) > remaining_budget:
        if trigger is not None:
            return _rejected(
                _budget(_PERSISTED_TOOL),
                DecisionCollectionPartialReason.PERSISTED_FANOUT_BUDGET,
            )
        return _rejected(_budget())
    return _BatchPlan(tuple(planned), None, None)


def _known_recommendation_ids(result: ToolResult) -> tuple[str, ...] | None:
    """Return a known id tuple, including an established empty tuple."""

    if result.tool_name != _RECOMMENDATION_TOOL:
        return None
    if result.status not in {ToolResultStatus.SUCCESS, ToolResultStatus.PARTIAL}:
        return None
    try:
        evidence = validate_decision_tool_result(result)
    except Exception:
        return None
    if evidence.evaluation_state is not DecisionEvaluationState.ESTABLISHED:
        return None
    if evidence.aircraft_in_current_set is not True:
        return None
    if evidence.encounters:
        identifiers = [
            item.recommendation_id
            for encounter in evidence.encounters
            for item in encounter.recommendations.current
        ]
    elif evidence.recommendations is not None:
        identifiers = [
            item.recommendation_id for item in evidence.recommendations.current
        ]
    else:
        return None
    return tuple(sorted(set(identifiers)))


def _current_known_recommendations(
    results: tuple[ToolResult, ...],
) -> tuple[str, ...] | None:
    known: tuple[str, ...] | None = None
    for result in results:
        extracted = _known_recommendation_ids(result)
        if extracted is not None:
            known = extracted
    return known


def _evidence_ref(index: int) -> str:
    return f"de-{index}"


class _DispatchFailure(Exception):
    """An adapter call failed after planning. The message is not published."""


class DecisionSpecialist:
    """One bounded Decision run. Per-run evidence stays on the stack."""

    def __init__(
        self,
        *,
        provider: DecisionModelProvider,
        tool_call_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not callable(getattr(provider, "complete", None)):
            raise TypeError("provider must implement complete")
        if tool_call_id_factory is None:
            tool_call_id_factory = _fresh_decision_tool_call_id
        elif not callable(tool_call_id_factory):
            raise TypeError("tool_call_id_factory must be callable")
        self._provider = provider
        self._tool_call_id_factory = tool_call_id_factory

    def run(
        self,
        request: DecisionSpecialistRequest,
        runtime: DecisionToolsRuntime,
    ) -> DecisionSpecialistRunResult:
        if not isinstance(request, DecisionSpecialistRequest):
            raise TypeError("request must be a DecisionSpecialistRequest")
        if not isinstance(runtime, DecisionToolsRuntime):
            raise TypeError("runtime must be a DecisionToolsRuntime")

        adapter = DecisionToolsAdapter(runtime)
        schemas = decision_tool_schemas()
        tool_results: list[ToolResult] = []
        snapshots: list[DecisionEvidenceSnapshot] = []
        bindings: list[DecisionEvidenceBinding] = []
        invocations: list[DecisionToolInvocationAudit] = []
        executed_keys: set[str] = set()
        feedback_history: list[ValidationFeedback] = []
        pending_feedback: ValidationFeedback | None = None
        collection_partial_reason: DecisionCollectionPartialReason | None = None
        invalid_corrections = 0
        provider_turns = 0
        last_kind: ModelDecisionKind | None = None

        while provider_turns < MAX_MODEL_TURNS:
            turn = DecisionModelTurnRequest(
                user_text=request.user_text,
                instruction_ref=DECISION_SPECIALIST_INSTRUCTION_REF,
                tools=schemas,
                evidence_snapshots=tuple(snapshots),
                validation_feedback=pending_feedback,
            )
            pending_feedback = None
            try:
                decision = self._provider.complete(turn)
            except Exception:
                return self._result(
                    request,
                    status=DecisionSpecialistRunStatus.PROVIDER_FAILED,
                    tool_results=tool_results,
                    snapshots=snapshots,
                    bindings=bindings,
                    invocations=invocations,
                    feedback_history=feedback_history,
                    provider_turns=provider_turns,
                    runtime_errors=(DecisionRuntimeErrorCode.PROVIDER_EXCEPTION,),
                    collection_partial_reason=collection_partial_reason,
                    terminal_kind=last_kind,
                )
            provider_turns += 1
            if not isinstance(decision, DecisionModelDecision):
                return self._result(
                    request,
                    status=DecisionSpecialistRunStatus.PROVIDER_FAILED,
                    tool_results=tool_results,
                    snapshots=snapshots,
                    bindings=bindings,
                    invocations=invocations,
                    feedback_history=feedback_history,
                    provider_turns=provider_turns,
                    runtime_errors=(
                        DecisionRuntimeErrorCode.INVALID_MODEL_DECISION,
                    ),
                    collection_partial_reason=collection_partial_reason,
                    terminal_kind=last_kind,
                )
            last_kind = decision.kind
            if decision.kind is ModelDecisionKind.FINAL_CLAIMS:
                verification = verify_decision_evidence(
                    decision.claims,
                    tuple(bindings),
                )
                rendered = render_decision_verification(verification)
                status = (
                    DecisionSpecialistRunStatus.PARTIAL
                    if collection_partial_reason is not None
                    else DecisionSpecialistRunStatus.COMPLETED
                )
                return self._result(
                    request,
                    status=status,
                    tool_results=tool_results,
                    snapshots=snapshots,
                    bindings=bindings,
                    invocations=invocations,
                    feedback_history=feedback_history,
                    provider_turns=provider_turns,
                    proposed_claims=decision.claims,
                    verification=verification,
                    render=rendered,
                    collection_partial_reason=collection_partial_reason,
                    terminal_kind=decision.kind,
                )
            if decision.kind is ModelDecisionKind.UNSUPPORTED:
                return self._result(
                    request,
                    status=DecisionSpecialistRunStatus.UNSUPPORTED,
                    tool_results=tool_results,
                    snapshots=snapshots,
                    bindings=bindings,
                    invocations=invocations,
                    feedback_history=feedback_history,
                    provider_turns=provider_turns,
                    unsupported_reason=decision.unsupported_reason,
                    collection_partial_reason=collection_partial_reason,
                    terminal_kind=decision.kind,
                )
            if decision.kind is ModelDecisionKind.REFUSAL:
                return self._result(
                    request,
                    status=DecisionSpecialistRunStatus.PROVIDER_FAILED,
                    tool_results=tool_results,
                    snapshots=snapshots,
                    bindings=bindings,
                    invocations=invocations,
                    feedback_history=feedback_history,
                    provider_turns=provider_turns,
                    runtime_errors=(DecisionRuntimeErrorCode.REFUSAL,),
                    collection_partial_reason=collection_partial_reason,
                    terminal_kind=decision.kind,
                    refusal_code=decision.refusal_code,
                )

            known = _current_known_recommendations(tuple(tool_results))
            plan = _plan_batch(
                decision.tool_calls,
                request,
                known,
                executed_keys,
                MAX_TOOL_CALLS - len(invocations),
            )
            if plan.feedback is not None:
                feedback_history.append(plan.feedback)
                if plan.collection_partial_reason is not None:
                    if collection_partial_reason is None:
                        collection_partial_reason = plan.collection_partial_reason
                    pending_feedback = plan.feedback
                    continue
                if plan.feedback.code is ValidationFeedbackCode.TOOL_CALL_BUDGET_EXCEEDED:
                    pending_feedback = plan.feedback
                    continue
                if invalid_corrections >= MAX_INVALID_TOOL_CALL_CORRECTIONS:
                    return self._result(
                        request,
                        status=DecisionSpecialistRunStatus.INVALID_REQUEST,
                        tool_results=tool_results,
                        snapshots=snapshots,
                        bindings=bindings,
                        invocations=invocations,
                        feedback_history=feedback_history,
                        provider_turns=provider_turns,
                        collection_partial_reason=collection_partial_reason,
                        terminal_kind=decision.kind,
                    )
                invalid_corrections += 1
                pending_feedback = plan.feedback
                continue

            assert plan.executions is not None
            for item in plan.executions:
                try:
                    raw, snapshot, binding, audit = self._execute(
                        adapter,
                        item,
                        len(tool_results) + 1,
                    )
                except _DispatchFailure:
                    return self._result(
                        request,
                        status=DecisionSpecialistRunStatus.UNAVAILABLE,
                        tool_results=tool_results,
                        snapshots=snapshots,
                        bindings=bindings,
                        invocations=invocations,
                        feedback_history=feedback_history,
                        provider_turns=provider_turns,
                        runtime_errors=(
                            DecisionRuntimeErrorCode.ADAPTER_EXECUTION_FAILED,
                        ),
                        collection_partial_reason=collection_partial_reason,
                        terminal_kind=decision.kind,
                    )
                tool_results.append(raw)
                snapshots.append(snapshot)
                bindings.append(binding)
                invocations.append(audit)
                executed_keys.add(item.canonical_key)

        return self._result(
            request,
            status=DecisionSpecialistRunStatus.PROVIDER_FAILED,
            tool_results=tool_results,
            snapshots=snapshots,
            bindings=bindings,
            invocations=invocations,
            feedback_history=feedback_history,
            provider_turns=provider_turns,
            runtime_errors=(DecisionRuntimeErrorCode.MODEL_TURN_LIMIT_EXCEEDED,),
            collection_partial_reason=collection_partial_reason,
            terminal_kind=last_kind,
        )

    def _execute(
        self,
        adapter: DecisionToolsAdapter,
        item: _PlannedExecution,
        evidence_index: int,
    ) -> tuple[
        ToolResult,
        DecisionEvidenceSnapshot,
        DecisionEvidenceBinding,
        DecisionToolInvocationAudit,
    ]:
        try:
            tool_call_id = self._tool_call_id_factory()
            if (
                not isinstance(tool_call_id, str)
                or not tool_call_id
                or tool_call_id != tool_call_id.strip()
                or any(character.isspace() for character in tool_call_id)
            ):
                raise RuntimeError("invalid tool call id")
            invocation = adapter.invoke(
                item.tool_name,
                dict(item.executed_arguments),
                tool_call_id=tool_call_id,
            )
            if not isinstance(invocation, DecisionToolInvocation):
                raise RuntimeError("invalid adapter invocation")
            raw = invocation.raw_tool_result
            if raw.tool_call_id != tool_call_id or raw.tool_name != item.tool_name:
                raise RuntimeError("adapter result identity mismatch")
            evidence_ref = _evidence_ref(evidence_index)
            snapshot = DecisionEvidenceSnapshot(
                evidence_ref,
                invocation.model_projection,
            )
            binding = DecisionEvidenceBinding(evidence_ref, raw)
            audit = DecisionToolInvocationAudit(
                tool_name=item.tool_name,
                tool_call_id=tool_call_id,
                evidence_ref=evidence_ref,
                requested_arguments=dict(item.requested_arguments),
                executed_arguments=dict(item.executed_arguments),
                canonical_key=item.canonical_key,
                dispatch_origin=item.dispatch_origin,
            )
        except Exception:
            raise _DispatchFailure from None
        return raw, snapshot, binding, audit

    def _result(
        self,
        request: DecisionSpecialistRequest,
        *,
        status: DecisionSpecialistRunStatus,
        tool_results: list[ToolResult],
        snapshots: list[DecisionEvidenceSnapshot],
        bindings: list[DecisionEvidenceBinding],
        invocations: list[DecisionToolInvocationAudit],
        feedback_history: list[ValidationFeedback],
        provider_turns: int,
        runtime_errors: tuple[DecisionRuntimeErrorCode, ...] = (),
        proposed_claims: tuple[Any, ...] = (),
        verification: Any = None,
        render: Any = None,
        unsupported_reason: Any = None,
        collection_partial_reason: DecisionCollectionPartialReason | None = None,
        terminal_kind: ModelDecisionKind | None = None,
        refusal_code: str | None = None,
    ) -> DecisionSpecialistRunResult:
        persisted = sum(1 for item in tool_results if item.tool_name == _PERSISTED_TOOL)
        return DecisionSpecialistRunResult(
            status=status,
            mode=request.mode,
            aircraft_id=request.aircraft_id,
            recommendation_id=request.recommendation_id,
            tool_results=tuple(tool_results),
            evidence_snapshots=tuple(snapshots),
            evidence_bindings=tuple(bindings),
            invocations=tuple(invocations),
            proposed_claims=tuple(proposed_claims),
            verification=verification,
            render=render,
            unsupported_reason=unsupported_reason,
            runtime_errors=runtime_errors,
            collection_partial_reason=collection_partial_reason,
            validation_feedback=tuple(feedback_history),
            provider_turn_count=provider_turns,
            executed_tool_call_count=len(tool_results),
            executed_persisted_call_count=persisted,
            terminal_kind=terminal_kind,
            refusal_code=refusal_code,
        )
