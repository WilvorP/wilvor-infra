"""Sprint 5 Master runtime seams onto Sprint 4 specialist entrypoints.

Historical facts use the Athena/S3 runtime. DynamoDB Query is not a
historical substitute. Decision live uses persisted tools with bounded
reads. Native success is not source authentication.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.historical_answer_renderer import finalize_historical_specialist_run
from wilvor_ai.historical_specialist import HistoricalAnalyticsSpecialist
from wilvor_ai.master_contracts import MasterSpecialist
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.specialist_contracts import HistoricalSpecialistTrustedContext
from wilvor_ai.specialist_runtime_contracts import HistoricalSpecialistRunResult
from wilvor_ai.sprint3c_specialist_orchestration import (
    Sprint3CConsumedDispatchAuthority,
    Sprint3CNativeSpecialistOrchestrator,
)
from wilvor_ai.sprint3d_native_specialists import (
    Sprint3DHistoricalNativeExecution,
    _BoundedProvider,
    _query_ids,
    _scope_historical,
    _source_versions,
)
from wilvor_ai.sprint4_decision_live import (
    Sprint4DecisionLiveCall,
    Sprint4DecisionLiveResult,
    execute_sprint4_decision_live,
)
from wilvor_ai.sprint4_historical_query_runtime import Sprint4HistoricalQueryRuntime
from wilvor_ai.sprint4_master_runtime import Sprint4HistoricalDispatch
from wilvor_historical_query.operations import HistoricalAnalyticsOperations


SPRINT5_MASTER_RUNTIME_SCHEMA = "wilvor.ai.sprint5.master_runtime.v1"
HISTORICAL_DYNAMODB_QUERY_SUBSTITUTED = False
NATIVE_EXECUTION_IS_SOURCE_EVIDENCE = False


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _closed() -> None:
    if HISTORICAL_DYNAMODB_QUERY_SUBSTITUTED is not False:
        _fail("historical_dynamodb_query_not_substituted")
    if NATIVE_EXECUTION_IS_SOURCE_EVIDENCE is not False:
        _fail("source_authenticity_not_established")


class Sprint5AthenaHistoricalOperations:
    """Window-fenced Athena operations. No DynamoDB Query port."""

    def __init__(
        self,
        inner: HistoricalAnalyticsOperations,
        *,
        eligible: tuple[str, str],
        plan_window: tuple[str, str],
        max_tool_calls: int,
    ) -> None:
        _closed()
        if type(inner) is not HistoricalAnalyticsOperations:
            _fail("historical_athena_runtime_required")
        if eligible == plan_window:
            _fail("historical_window_not_authorized")
        self._inner = inner
        self._eligible = eligible
        self._plan_window = plan_window
        self._max_tool_calls = max_tool_calls
        self.calls: list[str] = []
        self.rejection: str | None = None

    def summarize_historical_encounters(self, request: object, *, as_of_utc: str) -> object:
        return self._dispatch("summarize_historical_encounters", request, as_of_utc)

    def summarize_historical_risks(self, request: object, *, as_of_utc: str) -> object:
        return self._dispatch("summarize_historical_risks", request, as_of_utc)

    def summarize_historical_hazard_versions(self, request: object, *, as_of_utc: str) -> object:
        return self._dispatch("summarize_historical_hazard_versions", request, as_of_utc)

    def list_historical_encounters(self, request: object, *, as_of_utc: str) -> object:
        return self._dispatch("list_historical_encounters", request, as_of_utc)

    def _dispatch(self, name: str, request: object, as_of_utc: str) -> object:
        if self.rejection is not None:
            _fail(self.rejection)
        start = getattr(request, "start_utc", None)
        end = getattr(request, "end_utc", None)
        window = (start, end)
        if window == self._plan_window or window != self._eligible:
            self.rejection = "historical_window_not_authorized"
            _fail(self.rejection)
        if len(self.calls) >= self._max_tool_calls:
            self.rejection = "server_budget_exceeded"
            _fail(self.rejection)
        method = getattr(self._inner, name, None)
        if not callable(method):
            self.rejection = "invalid_historical_operations"
            _fail(self.rejection)
        self.calls.append(name)
        return method(request, as_of_utc=as_of_utc)


def execute_sprint5_historical_athena(
    orchestrator: Sprint3CNativeSpecialistOrchestrator,
    invocation: object,
    historical: Sprint4HistoricalDispatch,
    authority: Sprint3CConsumedDispatchAuthority,
    *,
    now_utc: str,
) -> Sprint3DHistoricalNativeExecution:
    """Run Historical Analytics on the Sprint 4 Athena runtime.

    The host-eligible window is enforced. DynamoDB Query is not used for
    historical fact retrieval.
    """

    _closed()
    if type(orchestrator) is not Sprint3CNativeSpecialistOrchestrator:
        _fail("invalid_schedule")
    if not isinstance(historical, Sprint4HistoricalDispatch):
        _fail("invalid_historical_binding")
    if historical.use_athena is not True:
        _fail("historical_athena_runtime_required")
    operations = historical.operations
    if type(operations) is Sprint4HistoricalQueryRuntime:
        operations = operations.operations
    if type(operations) is not HistoricalAnalyticsOperations:
        _fail("historical_athena_runtime_required")
    identity, _context = orchestrator.reserve_native_dispatch(
        invocation,  # type: ignore[arg-type]
        authority,
        expected=MasterSpecialist.HISTORICAL_ANALYTICS,
        now_utc=now_utc,
    )
    orchestrator.reject_closed_before_native_run(
        identity,
        now_utc=now_utc,
        cancelled=historical.cancelled,
        deadline_epoch=historical.deadline_epoch,
        observed_epoch=historical.observed_epoch,
    )
    if identity.invocation_id != historical.binding.invocation_id:
        _fail("invocation_binding_mismatch")
    eligible_start, eligible_end = _scope_historical(
        identity,
        historical.binding,
        historical.host,  # type: ignore[arg-type]
        idempotency_key=identity.idempotency_key,
    )
    plan = (
        historical.binding.unbound_plan_window_start_utc,
        historical.binding.unbound_plan_window_end_utc,
    )
    scoped = Sprint5AthenaHistoricalOperations(
        operations,
        eligible=(eligible_start, eligible_end),
        plan_window=plan,
        max_tool_calls=identity.max_tool_calls,
    )
    bounded = _BoundedProvider(
        historical.provider,
        max_turns=identity.max_model_turns,
        cancelled=historical.cancelled,
    )
    trusted = HistoricalSpecialistTrustedContext(as_of_utc=historical.host.evaluation_utc)
    specialist = HistoricalAnalyticsSpecialist(
        provider=bounded,
        operations=scoped,
        tool_call_id_factory=historical.tool_call_id_factory,
    )
    run = specialist.run(historical.binding.specialist_request, trusted)
    if bounded.rejection is not None:
        _fail(bounded.rejection)
    if scoped.rejection is not None:
        _fail(scoped.rejection)
    if not isinstance(run, HistoricalSpecialistRunResult):
        _fail("invalid_historical_run")
    final = finalize_historical_specialist_run(run)
    return Sprint3DHistoricalNativeExecution(
        identity=identity,
        specialist_request=historical.binding.specialist_request,
        trusted_context=trusted,
        intermediate_run=run,
        final_result=final,
        eligible_start_utc=eligible_start,
        eligible_end_utc=eligible_end,
        unbound_plan_start_utc=plan[0],
        unbound_plan_end_utc=plan[1],
        native_source_versions=_source_versions(run.tool_results),
        read_source_versions=(),
        native_query_ids=_query_ids(run.tool_results),
        read_query_ids=(),
    )


def execute_sprint5_decision_live_dispatch(decision: object) -> Sprint4DecisionLiveResult:
    """Run the Sprint 4 Decision live path. Not a DecisionSpecialist MA4 artifact."""

    _closed()
    live_call = getattr(decision, "live_call", None)
    if not isinstance(live_call, Sprint4DecisionLiveCall):
        _fail("invalid_decision_live_call")
    result = execute_sprint4_decision_live(live_call)
    if result.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
        _fail("source_authenticity_not_established")
    return result


def historical_runtime_inventory() -> dict[str, object]:
    _closed()
    return {
        "schema_version": SPRINT5_MASTER_RUNTIME_SCHEMA,
        "historical_uses_athena": True,
        "historical_uses_dynamodb_query": False,
        "native_execution_is_source_evidence": False,
    }


__all__ = [
    "HISTORICAL_DYNAMODB_QUERY_SUBSTITUTED",
    "NATIVE_EXECUTION_IS_SOURCE_EVIDENCE",
    "SPRINT5_MASTER_RUNTIME_SCHEMA",
    "Sprint5AthenaHistoricalOperations",
    "execute_sprint5_decision_live_dispatch",
    "execute_sprint5_historical_athena",
    "historical_runtime_inventory",
]
