"""In-process Live Ops specialist orchestration.

This module implements ``LiveOpsSpecialistPort.run``. It sequences an
injected provider, LO1 dispatch, LO2 verification, LO3A export, and LO3B
rendering. It does not call a vendor SDK, LangGraph, or AWS.

Per-run evidence, budgets, and the LO1 adapter live on the stack. The
specialist instance keeps only the injected provider, id factory, and
handlers. Those three objects may be shared by concurrent ``run`` calls:

- The default UUID factory is safe for independent runs.
- A production provider must support concurrent ``complete`` calls, or
  serialize them itself.
- A shared id factory must be thread-safe.
- Shared handlers must not rely on unsafe shared mutable state.

The run is synchronous. LO1 executes a batch of up to two tools with no
hook between them, and this module cannot interrupt an in-flight provider
or DynamoDB call. Deadline and cancellation are cooperative: after a
blocking call returns, the guard can discard its evidence instead of
publishing it. LO3D must bound provider and transport time itself.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from typing import Any

from wilvor_ai.contracts import ContractValidationError, ToolResult
from wilvor_ai.live_ops import live_ops_tool_schemas
from wilvor_ai.live_ops_adapter import LiveOpsAdapter, _validate_batch
from wilvor_ai.live_ops_answer_renderer import render_live_ops_answer
from wilvor_ai.live_ops_evidence_verifier import (
    LiveOpsTrustedEvidenceLedger,
    verify_live_ops_claims,
)
from wilvor_ai.live_ops_model_contracts import (
    LIVE_OPS_INSTRUCTION_REF,
    LiveOpsModelDecision,
    LiveOpsModelDecisionKind,
    LiveOpsModelProvider,
    LiveOpsModelTurnRequest,
    LiveOpsProviderFailureKind,
    LiveOpsUnsupportedReason,
    LiveOpsValidationFailure,
)
from wilvor_ai.live_ops_public_contracts import (
    LIVE_OPS_ANSWER_CANCELLED,
    LIVE_OPS_ANSWER_INVALID_REQUEST,
    LIVE_OPS_ANSWER_NONE_VERIFIED,
    LIVE_OPS_ANSWER_PROVIDER,
    LIVE_OPS_ANSWER_REFUSED,
    LIVE_OPS_ANSWER_STOPPED,
    LIVE_OPS_ANSWER_TOOL_FAILED,
    LIVE_OPS_ANSWER_UNAVAILABLE,
    LIVE_OPS_ANSWER_UNSUPPORTED,
    LiveOpsAnswerOrigin,
    LiveOpsFailureReason,
    LiveOpsPublicRenderingPosture,
    LiveOpsPublicUnsupportedReason,
    LiveOpsPublicValidationCode,
    LiveOpsPublicVerificationOutcome,
    LiveOpsRunFailure,
    LiveOpsRunRequest,
    LiveOpsRunResult,
    LiveOpsRunStatus,
    LiveOpsTrustedContext,
    live_ops_reference_utc,
)
from wilvor_ai.live_ops_public_export import export_live_ops_evidence
from wilvor_ai.live_ops_specialist_runtime_contracts import (
    LiveOpsDispatchResult,
    LiveOpsDispatchStatus,
    LiveOpsSpecialistTrustedContext,
)
from wilvor_ai.live_ops_tool_projection import LiveOpsToolProjection
from wilvor_ai.model_contracts import ProposedToolCall


_CALLSIGN_TOOL = "find_aircraft_by_callsign"
_RUN_EXECUTION_FAILED = "RUN_EXECUTION_FAILED"
_FIXED_ANSWERS = {
    LiveOpsRunStatus.INVALID_REQUEST: LIVE_OPS_ANSWER_INVALID_REQUEST,
    LiveOpsRunStatus.UNSUPPORTED: LIVE_OPS_ANSWER_UNSUPPORTED,
    LiveOpsRunStatus.REFUSED: LIVE_OPS_ANSWER_REFUSED,
    LiveOpsRunStatus.TOOL_FAILED: LIVE_OPS_ANSWER_TOOL_FAILED,
    LiveOpsRunStatus.PROVIDER_FAILED: LIVE_OPS_ANSWER_PROVIDER,
    LiveOpsRunStatus.PROVIDER_TIMEOUT: LIVE_OPS_ANSWER_PROVIDER,
    LiveOpsRunStatus.RUNTIME_TIMEOUT: LIVE_OPS_ANSWER_STOPPED,
    LiveOpsRunStatus.BUDGET_EXHAUSTED: LIVE_OPS_ANSWER_STOPPED,
    LiveOpsRunStatus.CANCELLED: LIVE_OPS_ANSWER_CANCELLED,
    LiveOpsRunStatus.UNAVAILABLE: LIVE_OPS_ANSWER_UNAVAILABLE,
}


class LiveOpsProviderError(Exception):
    """A classified provider failure. The message is only the kind value."""

    def __init__(self, kind: LiveOpsProviderFailureKind) -> None:
        if not isinstance(kind, LiveOpsProviderFailureKind):
            raise TypeError("kind must be a LiveOpsProviderFailureKind")
        self.kind = kind
        super().__init__(kind.value)


class LiveOpsProviderTimeoutError(Exception):
    """The provider reported its own timeout.

    ``LiveOpsProviderFailureKind`` has no timeout member. This exception
    stays inside Live Ops and maps to the existing public
    ``PROVIDER_TIMEOUT`` status.
    """

    def __init__(self) -> None:
        super().__init__(LiveOpsFailureReason.PROVIDER_TIMEOUT.value)


def _default_tool_call_id() -> str:
    return "live-ops-" + uuid.uuid4().hex


def _selector_items(
    request: LiveOpsRunRequest,
) -> tuple[tuple[str, str | tuple[str, ...]], ...]:
    hints = request.selector_hints
    if hints is None:
        return ()
    payload = hints.to_dict()
    items: list[tuple[str, str | tuple[str, ...]]] = []
    for name in (
        "region",
        "product_type",
        "hazard_type",
        "hazard_ids",
        "aircraft_id",
        "callsign",
        "airport_id",
        "hazard_id",
    ):
        if name not in payload:
            continue
        value = payload[name]
        if name == "hazard_ids":
            value = tuple(value)
        items.append((name, value))
    return tuple(items)


def _entity_items(
    request: LiveOpsRunRequest,
) -> tuple[tuple[str, str, str | None], ...]:
    return tuple(
        (item.entity_kind, item.entity_id, item.callsign) for item in request.entity_hints
    )


def _prior_turns(request: LiveOpsRunRequest) -> tuple[tuple[str, str], ...]:
    return tuple((item.role, item.text) for item in request.prior_turns)


class LiveOpsSpecialist:
    """One provider-neutral Live Ops run behind the public port.

    ``run`` allocates a fresh adapter and ledger. It does not keep those
    objects on ``self``. See the module docstring for shared-dependency
    rules and the cooperative deadline limit.
    """

    def __init__(
        self,
        *,
        provider: LiveOpsModelProvider,
        tool_call_id_factory: Callable[[], str] | None = None,
        handlers: Mapping[str, Callable[..., ToolResult]] | None = None,
    ) -> None:
        if not callable(getattr(provider, "complete", None)):
            raise TypeError("provider must implement complete")
        if tool_call_id_factory is None:
            tool_call_id_factory = _default_tool_call_id
        elif not callable(tool_call_id_factory):
            raise TypeError("tool_call_id_factory must be callable")
        if handlers is not None and not isinstance(handlers, Mapping):
            raise TypeError("handlers must be a mapping")
        self._provider = provider
        self._tool_call_id_factory = tool_call_id_factory
        self._handlers = None if handlers is None else dict(handlers)

    def run(
        self,
        request: LiveOpsRunRequest,
        context: LiveOpsTrustedContext,
    ) -> LiveOpsRunResult:
        if not isinstance(request, LiveOpsRunRequest) or not isinstance(
            context, LiveOpsTrustedContext
        ):
            raise TypeError(
                "Live Ops run requires a LiveOpsRunRequest and a LiveOpsTrustedContext"
            )
        try:
            return self._run_bounded(request, context)
        except Exception:
            return _unavailable_baseline(request, context)

    def _run_bounded(
        self,
        request: LiveOpsRunRequest,
        context: LiveOpsTrustedContext,
    ) -> LiveOpsRunResult:
        runtime = LiveOpsSpecialistTrustedContext(
            tables=context.tables,
            now_epoch=context.now_epoch,
            correlation_id=context.correlation_id,
        )
        adapter = LiveOpsAdapter(
            runtime,
            handlers=None if self._handlers is None else dict(self._handlers),
        )
        schemas = live_ops_tool_schemas()
        projections: list[LiveOpsToolProjection] = []
        dispatches: list[LiveOpsDispatchResult] = []
        feedback: LiveOpsValidationFailure | None = None
        turns = 0
        tools_used = 0

        def guard() -> LiveOpsRunResult | None:
            return _guard(request, context)

        def finish(claims: tuple[Any, ...]) -> LiveOpsRunResult:
            return _finish(
                request,
                context,
                tuple(dispatches),
                claims,
                _failed_tool_name(dispatches),
                guard,
            )

        blocked = guard()
        if blocked is not None:
            return blocked

        hint = None if request.selector_hints is None else request.selector_hints.callsign
        if hint is not None:
            if tools_used + 1 > context.max_tool_calls:
                return _budget(request, context, LiveOpsFailureReason.TOOL_CALLS)
            blocked = guard()
            if blocked is not None:
                return blocked
            outcome = adapter.dispatch(
                (ProposedToolCall(name=_CALLSIGN_TOOL, arguments={"callsign": hint}),),
                tool_call_id_factory=self._tool_call_id_factory,
            )
            blocked = guard()
            if blocked is not None:
                return blocked
            if outcome.status is LiveOpsDispatchStatus.VALIDATION_REJECTED:
                if outcome.validation_failure is None:
                    return _unavailable_baseline(request, context)
                return _invalid_request(request, context, outcome.validation_failure)
            routed = _consume_dispatch(
                request,
                context,
                outcome,
                projections,
                dispatches,
            )
            if isinstance(routed, LiveOpsRunResult):
                return routed
            if routed == "failed":
                return finish(())
            if type(routed) is not int:
                return _unavailable_baseline(request, context)
            tools_used += routed

        while True:
            blocked = guard()
            if blocked is not None:
                return blocked
            if turns >= context.max_model_turns:
                return _budget(request, context, LiveOpsFailureReason.MODEL_TURNS)
            turns += 1
            turn = LiveOpsModelTurnRequest(
                user_text=request.user_text,
                instruction_ref=LIVE_OPS_INSTRUCTION_REF,
                tools=schemas,
                tool_results=tuple(projections),
                validation_feedback=feedback,
                prior_turns=_prior_turns(request),
                selector_hint_items=_selector_items(request),
                entity_hint_items=_entity_items(request),
            )
            feedback = None
            decision, failure = _complete(self._provider, turn)
            blocked = guard()
            if blocked is not None:
                return blocked
            if failure is not None:
                return _provider_result(request, context, failure)
            if not isinstance(decision, LiveOpsModelDecision):
                return _provider_result(
                    request,
                    context,
                    (LiveOpsRunStatus.PROVIDER_FAILED, LiveOpsFailureReason.MALFORMED_OUTPUT),
                )
            if decision.kind is LiveOpsModelDecisionKind.UNSUPPORTED:
                return _unsupported(request, context, decision.unsupported_reason)
            if decision.kind is LiveOpsModelDecisionKind.REFUSAL:
                return _refused(request, context, decision.refusal_code)
            if decision.kind is LiveOpsModelDecisionKind.FINAL_CLAIMS:
                return finish(decision.claims)

            remaining = context.max_tool_calls - tools_used
            if len(decision.tool_calls) > remaining:
                return _budget(request, context, LiveOpsFailureReason.TOOL_CALLS)
            if turns >= context.max_model_turns:
                validated = _validate_batch(decision.tool_calls)
                if isinstance(validated, LiveOpsValidationFailure):
                    return _invalid_request(request, context, validated)
                return _budget(request, context, LiveOpsFailureReason.MODEL_TURNS)

            blocked = guard()
            if blocked is not None:
                return blocked
            outcome = adapter.dispatch(
                decision.tool_calls,
                tool_call_id_factory=self._tool_call_id_factory,
            )
            blocked = guard()
            if blocked is not None:
                return blocked
            consumed = _consume_dispatch(
                request,
                context,
                outcome,
                projections,
                dispatches,
            )
            if isinstance(consumed, LiveOpsRunResult):
                return consumed
            if consumed == "retry":
                feedback = outcome.validation_failure
                continue
            if consumed == "failed":
                return finish(())
            if type(consumed) is not int:
                return _unavailable_baseline(request, context)
            tools_used += consumed


def decode_and_run_live_ops(
    specialist: LiveOpsSpecialist,
    data: Mapping[str, Any],
    context: LiveOpsTrustedContext,
) -> LiveOpsRunResult:
    """Decode a public payload, then run it.

    ``run`` does not accept a dictionary. A malformed payload becomes the
    fixed invalid-request result and does not echo attacker-controlled ids.
    """

    if not isinstance(specialist, LiveOpsSpecialist):
        raise TypeError("specialist must be a LiveOpsSpecialist")
    if not isinstance(context, LiveOpsTrustedContext):
        raise TypeError("context must be a LiveOpsTrustedContext")
    try:
        request = LiveOpsRunRequest.from_dict(data)
    except ContractValidationError:
        return LiveOpsRunResult.rejected_payload(context)
    return specialist.run(request, context)


def _complete(
    provider: LiveOpsModelProvider,
    turn: LiveOpsModelTurnRequest,
) -> tuple[Any, tuple[LiveOpsRunStatus, LiveOpsFailureReason] | None]:
    try:
        return provider.complete(turn), None
    except LiveOpsProviderTimeoutError:
        return None, (LiveOpsRunStatus.PROVIDER_TIMEOUT, LiveOpsFailureReason.PROVIDER_TIMEOUT)
    except LiveOpsProviderError as exc:
        return None, (LiveOpsRunStatus.PROVIDER_FAILED, _provider_reason(exc.kind))
    except ContractValidationError:
        return None, (LiveOpsRunStatus.PROVIDER_FAILED, LiveOpsFailureReason.MALFORMED_OUTPUT)
    except Exception:
        return None, (LiveOpsRunStatus.PROVIDER_FAILED, LiveOpsFailureReason.TRANSPORT_OR_API)


def _provider_reason(kind: LiveOpsProviderFailureKind) -> LiveOpsFailureReason:
    return {
        LiveOpsProviderFailureKind.TRANSPORT_OR_API: LiveOpsFailureReason.TRANSPORT_OR_API,
        LiveOpsProviderFailureKind.MALFORMED_OUTPUT: LiveOpsFailureReason.MALFORMED_OUTPUT,
        LiveOpsProviderFailureKind.CONTEXT_LENGTH: LiveOpsFailureReason.CONTEXT_LENGTH,
    }[kind]


def _consume_dispatch(
    request: LiveOpsRunRequest,
    context: LiveOpsTrustedContext,
    outcome: LiveOpsDispatchResult,
    projections: list[LiveOpsToolProjection],
    dispatches: list[LiveOpsDispatchResult],
) -> LiveOpsRunResult | str | int:
    """Record one dispatch.

    Returns a public result, ``"retry"``, ``"failed"``, or the number of
    tool slots consumed by a completed batch.
    """

    if outcome.status is LiveOpsDispatchStatus.VALIDATION_REJECTED:
        if outcome.validation_failure is None:
            return _unavailable_baseline(request, context)
        return "retry"
    if outcome.status is LiveOpsDispatchStatus.EXECUTION_FAILED:
        dispatches.append(outcome)
        return "failed"
    if outcome.status is not LiveOpsDispatchStatus.COMPLETED:
        return _unavailable_baseline(request, context)
    collected: list[LiveOpsToolProjection] = []
    for invocation in outcome.invocations:
        if not isinstance(invocation.model_projection, LiveOpsToolProjection):
            return _unavailable_baseline(request, context)
        collected.append(invocation.model_projection)
    dispatches.append(outcome)
    projections.extend(collected)
    return len(outcome.invocations)


def _failed_tool_name(dispatches: list[LiveOpsDispatchResult]) -> str | None:
    for dispatch in reversed(dispatches):
        if dispatch.status is LiveOpsDispatchStatus.EXECUTION_FAILED:
            name = dispatch.failed_tool_name
            if isinstance(name, str) and name.strip():
                return name
    return None


def _finish(
    request: LiveOpsRunRequest,
    context: LiveOpsTrustedContext,
    dispatches: tuple[LiveOpsDispatchResult, ...],
    claims: tuple[Any, ...],
    failed_tool_name: str | None,
    guard: Callable[[], LiveOpsRunResult | None],
) -> LiveOpsRunResult:
    blocked = guard()
    if blocked is not None:
        return blocked
    try:
        ledger = LiveOpsTrustedEvidenceLedger(
            correlation_id=context.correlation_id,
            now_epoch=context.now_epoch,
            dispatches=dispatches,
        )
        verified = verify_live_ops_claims(claims, ledger)
    except Exception:
        return _unavailable_baseline(request, context)
    blocked = guard()
    if blocked is not None:
        return blocked
    try:
        export = export_live_ops_evidence(verified, ledger, claims)
    except Exception:
        return _unavailable_baseline(request, context)
    if (
        export.correlation_id != context.correlation_id
        or export.reference_epoch != context.now_epoch
        or export.reference_utc != live_ops_reference_utc(context.now_epoch)
    ):
        return _unavailable_baseline(request, context)

    outcome = export.verification_outcome
    if outcome is LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED:
        blocked = guard()
        if blocked is not None:
            return blocked
        if _RUN_EXECUTION_FAILED in export.structural_codes:
            return _tool_failed(request, context, export, failed_tool_name)
        return _ledger_denied(request, context, export)
    if outcome is LiveOpsPublicVerificationOutcome.NONE_VERIFIED:
        return _publish_completed(
            request,
            context,
            _assemble(
                request,
                context,
                execution_status=LiveOpsRunStatus.COMPLETED,
                verification_outcome=outcome,
                rendering_posture=export.rendering_posture,
                answer_origin=LiveOpsAnswerOrigin.FIXED_SAFE_TEXT,
                answer=LIVE_OPS_ANSWER_NONE_VERIFIED,
                verified_claims=export.verified_claims,
                evidence_references=export.evidence_references,
                diagnostics=export.diagnostics,
                structural_codes=export.structural_codes,
                contradiction_subjects=export.contradiction_subjects,
                mandatory_limitations=export.mandatory_limitations,
                discoveries=export.discoveries,
                verified_entity_refs=export.verified_entity_refs,
            ),
            guard,
        )
    if outcome not in {
        LiveOpsPublicVerificationOutcome.ALL_VERIFIED,
        LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED,
    }:
        return _unavailable_baseline(request, context)
    blocked = guard()
    if blocked is not None:
        return blocked
    try:
        rendered = render_live_ops_answer(export)
    except Exception:
        return _unavailable_baseline(request, context)
    return _publish_completed(
        request,
        context,
        _assemble(
            request,
            context,
            execution_status=LiveOpsRunStatus.COMPLETED,
            verification_outcome=outcome,
            rendering_posture=export.rendering_posture,
            answer_origin=LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER,
            answer=rendered.answer,
            verified_claims=export.verified_claims,
            evidence_references=export.evidence_references,
            diagnostics=export.diagnostics,
            structural_codes=export.structural_codes,
            contradiction_subjects=export.contradiction_subjects,
            mandatory_limitations=export.mandatory_limitations,
            discoveries=export.discoveries,
            verified_entity_refs=export.verified_entity_refs,
        ),
        guard,
    )


def _publish_completed(
    request: LiveOpsRunRequest,
    context: LiveOpsTrustedContext,
    result: LiveOpsRunResult,
    guard: Callable[[], LiveOpsRunResult | None],
) -> LiveOpsRunResult:
    if result.execution_status is not LiveOpsRunStatus.COMPLETED:
        return result
    blocked = guard()
    if blocked is not None:
        return blocked
    return result


def _guard(
    request: LiveOpsRunRequest,
    context: LiveOpsTrustedContext,
) -> LiveOpsRunResult | None:
    if context.cancelled is not None:
        try:
            cancelled = context.cancelled()
        except Exception:
            return _unavailable_baseline(request, context)
        if type(cancelled) is not bool:
            return _unavailable_baseline(request, context)
        if cancelled:
            return _terminal(
                request,
                context,
                LiveOpsRunStatus.CANCELLED,
                LiveOpsFailureReason.CANCELLED,
            )
    if context.deadline_epoch is None:
        return None
    try:
        observed = context.clock()
    except Exception:
        return _unavailable_baseline(request, context)
    if type(observed) is not int:
        return _unavailable_baseline(request, context)
    if observed >= context.deadline_epoch:
        return _terminal(
            request,
            context,
            LiveOpsRunStatus.RUNTIME_TIMEOUT,
            LiveOpsFailureReason.RUNTIME_TIMEOUT,
        )
    return None


def _tool_failed(
    request: LiveOpsRunRequest,
    context: LiveOpsTrustedContext,
    export: Any,
    failed_tool_name: str | None,
) -> LiveOpsRunResult:
    failure = _execution_failure(failed_tool_name)
    return _assemble(
        request,
        context,
        execution_status=LiveOpsRunStatus.TOOL_FAILED,
        verification_outcome=LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED,
        rendering_posture=LiveOpsPublicRenderingPosture.NOT_RENDERABLE,
        answer_origin=LiveOpsAnswerOrigin.FIXED_SAFE_TEXT,
        answer=LIVE_OPS_ANSWER_TOOL_FAILED,
        structural_codes=export.structural_codes,
        mandatory_limitations=export.mandatory_limitations,
        failure=failure,
    )


def _execution_failure(failed_tool_name: str | None) -> LiveOpsRunFailure:
    if failed_tool_name is None:
        return LiveOpsRunFailure(reason=LiveOpsFailureReason.TOOL_EXECUTION_FAILED)
    try:
        return LiveOpsRunFailure(
            reason=LiveOpsFailureReason.TOOL_EXECUTION_FAILED,
            failed_tool_name=failed_tool_name,
        )
    except ContractValidationError:
        return LiveOpsRunFailure(reason=LiveOpsFailureReason.TOOL_EXECUTION_FAILED)


def _ledger_denied(
    request: LiveOpsRunRequest,
    context: LiveOpsTrustedContext,
    export: Any,
) -> LiveOpsRunResult:
    return _assemble(
        request,
        context,
        execution_status=LiveOpsRunStatus.UNAVAILABLE,
        verification_outcome=LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED,
        rendering_posture=LiveOpsPublicRenderingPosture.NOT_RENDERABLE,
        answer_origin=LiveOpsAnswerOrigin.FIXED_SAFE_TEXT,
        answer=LIVE_OPS_ANSWER_UNAVAILABLE,
        structural_codes=export.structural_codes,
        mandatory_limitations=export.mandatory_limitations,
        failure=LiveOpsRunFailure(reason=LiveOpsFailureReason.LEDGER_NOT_AUTHORIZED),
    )


def _unsupported(
    request: LiveOpsRunRequest,
    context: LiveOpsTrustedContext,
    reason: LiveOpsUnsupportedReason | None,
) -> LiveOpsRunResult:
    if not isinstance(reason, LiveOpsUnsupportedReason):
        return _unavailable_baseline(request, context)
    try:
        public_reason = LiveOpsPublicUnsupportedReason(reason.value)
    except ValueError:
        return _unavailable_baseline(request, context)
    return _terminal(
        request,
        context,
        LiveOpsRunStatus.UNSUPPORTED,
        LiveOpsFailureReason.UNSUPPORTED,
        unsupported_reason=public_reason,
    )


def _refused(
    request: LiveOpsRunRequest,
    context: LiveOpsTrustedContext,
    refusal_code: str | None,
) -> LiveOpsRunResult:
    return _terminal(
        request,
        context,
        LiveOpsRunStatus.REFUSED,
        LiveOpsFailureReason.MODEL_REFUSAL,
        refusal_code=refusal_code,
    )


def _invalid_request(
    request: LiveOpsRunRequest,
    context: LiveOpsTrustedContext,
    failure: LiveOpsValidationFailure,
) -> LiveOpsRunResult:
    try:
        code = LiveOpsPublicValidationCode(failure.code.value)
    except ValueError:
        return _unavailable_baseline(request, context)
    return _terminal(
        request,
        context,
        LiveOpsRunStatus.INVALID_REQUEST,
        LiveOpsFailureReason.TOOL_CALL_VALIDATION_EXHAUSTED,
        validation_code=code,
    )


def _budget(
    request: LiveOpsRunRequest,
    context: LiveOpsTrustedContext,
    reason: LiveOpsFailureReason,
) -> LiveOpsRunResult:
    return _terminal(
        request,
        context,
        LiveOpsRunStatus.BUDGET_EXHAUSTED,
        reason,
    )


def _provider_result(
    request: LiveOpsRunRequest,
    context: LiveOpsTrustedContext,
    failure: tuple[LiveOpsRunStatus, LiveOpsFailureReason],
) -> LiveOpsRunResult:
    status, reason = failure
    return _terminal(request, context, status, reason)


def _terminal(
    request: LiveOpsRunRequest,
    context: LiveOpsTrustedContext,
    status: LiveOpsRunStatus,
    reason: LiveOpsFailureReason,
    **failure_fields: Any,
) -> LiveOpsRunResult:
    return _assemble(
        request,
        context,
        execution_status=status,
        verification_outcome=None,
        rendering_posture=None,
        answer_origin=LiveOpsAnswerOrigin.FIXED_SAFE_TEXT,
        answer=_FIXED_ANSWERS[status],
        failure=LiveOpsRunFailure(reason=reason, **failure_fields),
    )


def _assemble(request: LiveOpsRunRequest, context: LiveOpsTrustedContext, **fields: Any) -> LiveOpsRunResult:
    try:
        return LiveOpsRunResult(
            request_id=request.request_id,
            conversation_id=request.conversation_id,
            correlation_id=context.correlation_id,
            reference_epoch=context.now_epoch,
            reference_utc=live_ops_reference_utc(context.now_epoch),
            **fields,
        )
    except Exception:
        return _unavailable_baseline(request, context)


def _unavailable_baseline(
    request: LiveOpsRunRequest,
    context: LiveOpsTrustedContext,
) -> LiveOpsRunResult:
    """Fixed internal failure. This function does not call ``_assemble``."""

    return LiveOpsRunResult(
        request_id=request.request_id,
        conversation_id=request.conversation_id,
        correlation_id=context.correlation_id,
        execution_status=LiveOpsRunStatus.UNAVAILABLE,
        verification_outcome=None,
        rendering_posture=None,
        answer_origin=LiveOpsAnswerOrigin.FIXED_SAFE_TEXT,
        answer=LIVE_OPS_ANSWER_UNAVAILABLE,
        reference_epoch=context.now_epoch,
        reference_utc=live_ops_reference_utc(context.now_epoch),
        failure=LiveOpsRunFailure(reason=LiveOpsFailureReason.INTERNAL_ERROR),
    )


__all__ = [
    "LiveOpsProviderError",
    "LiveOpsProviderTimeoutError",
    "LiveOpsSpecialist",
    "decode_and_run_live_ops",
]
