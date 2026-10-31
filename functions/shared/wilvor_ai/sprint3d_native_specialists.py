"""Sprint 3D Agent E: native Historical and Decision execution.

Live Ops already calls its native specialist from the Sprint 3C
orchestrator. This module reserves that same consumed grant and current
fence, then calls ``HistoricalAnalyticsSpecialist.run`` and
``DecisionSpecialist.run``. A scripted scheduler worker is not a specialist.

Historical tool windows come from the host-approved eligible interval.
The unbound plan window is not copied onto ``SpecialistRequest`` and is
not accepted as a tool argument. The intermediate
``HistoricalSpecialistRunResult`` is finalized with
``finalize_historical_specialist_run``. Host identity stays on this
execution record because the native result types still do not carry it.

``SourceRecord.source_version`` and ``QueryExecutionTrace.query_id`` are
copied when the native evidence has them. Agent C receipt versions and
query ids are kept beside those values and do not replace them.

A Decision request is built only after the injected read port and the
sealed host establish source currentness, an allowed target population,
and a permitted recommendation id that is not a copied comparison id.
The aircraft id on that request is the user-supplied observation. A
comparison id is never the target.

Reads go through the injected ``Sprint3CScopedReadPort``. This module
does not construct a second read client. Offline execution is not
production dispatch and is not authenticated source evidence.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from typing import Any

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.decision_specialist import DecisionSpecialist
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionSpecialistRequest,
    DecisionSpecialistRunResult,
    DecisionTargetMode,
)
from wilvor_ai.decision_tools import DecisionToolsRuntime
from wilvor_ai.historical_answer_renderer import finalize_historical_specialist_run
from wilvor_ai.historical_specialist import HistoricalAnalyticsSpecialist
from wilvor_ai.master_authorized_scheduler_adapter import ScriptedTestWorker
from wilvor_ai.master_authorized_scope import (
    EffectiveScopeRequestHints,
    EffectiveScopeStatus,
    LocalTestHostScope,
    resolve_master_authorized_scope,
)
from wilvor_ai.master_contracts import MasterSpecialist
from wilvor_ai.master_decision_binding import MasterDecisionBinding
from wilvor_ai.master_decision_execution_readiness import MasterDecisionExecutionReadiness
from wilvor_ai.master_decision_target_resolver import (
    REQUIRED_HOST_PROOFS,
    DecisionCandidateRole,
    DecisionResolutionStatus,
    FakeDecisionReadCatalog,
    HostProofRequirement,
    MasterDecisionTargetResolution,
    resolve_master_decision_target,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_historical_binding import MasterHistoricalBinding
from wilvor_ai.master_read_resource_guard import ReadOperation, server_read_resources
from wilvor_ai.specialist_contracts import (
    HistoricalSpecialistTrustedContext,
    SpecialistRequest,
    SpecialistResult,
)
from wilvor_ai.specialist_runtime_contracts import HistoricalSpecialistRunResult
from wilvor_ai.sprint3c_scoped_read import (
    GRANT_CONSUMPTION_LIFECYCLE,
    LEDGER_EXECUTION_STATE_STARTED,
    OfflineAwsReadClient,
    Sprint3CScopedReadPort,
    describe_consumed_attempt_read_binding,
)
from wilvor_ai.sprint3c_specialist_orchestration import (
    Sprint3CBoundInvocationIdentity,
    Sprint3CConsumedDispatchAuthority,
    Sprint3CNativeSpecialistOrchestrator,
)
from wilvor_ai.master_trusted_read_executor import UntrustedReadRequest


SPRINT3D_NATIVE_SPECIALISTS_SCHEMA_VERSION = "wilvor.ai.sprint3d.native_specialists.v1"
NATIVE_EXECUTION_IS_SOURCE_EVIDENCE = False
NATIVE_OFFLINE_EXECUTION_IS_PRODUCTION_DISPATCH = False
SCRIPTED_WORKER_IS_NATIVE_EXECUTION = False

_NETWORK_MARKERS = ("anthropic", "boto3", "botocore", "openai", "langgraph")
_HISTORICAL_TOOL = "summarize_historical_encounters"
_DECISION_TOOL = "get_current_risk_evidence"
_CORRELATION_FIELDS = (
    "master_request_id",
    "invocation_id",
    "specialist_request_id",
    "correlation_id",
    "application_request_time_utc",
    "requested_evaluation_time_utc",
    "source_version",
    "query_id",
)

# Reviewed migration for the integration owner. Shared native contracts are
# unchanged. Do not add these fields in place.
SPRINT3D_HISTORICAL_CONTRACT_MIGRATION = (
    "Do not add start_utc or end_utc to SpecialistRequest. Scope tool "
    "arguments to the host eligible window. That window must sit inside the "
    "requested window and must not equal the unbound plan window.",
    "Do not add master_request_id, invocation_id, specialist_request_id, "
    "correlation_id, application_request_time_utc, or "
    "requested_evaluation_time_utc to HistoricalSpecialistRunResult or "
    "SpecialistResult until a reviewed migration. Bind those identities on "
    "the host execution record, and derive SpecialistResult only by calling "
    "finalize_historical_specialist_run on the intermediate run.",
    "Do not add source_version or query_id to either historical result type. "
    "source_version stays on SourceRecord. query_id stays on QueryExecutionTrace.",
    "HistoricalEncounterRecord has no source_version. List mapping keeps a "
    "version only when the row exposes one. Add optional source_version on "
    "HistoricalEncounterRecord in a reviewed migration. Do not parse it from "
    "hazard_version_key or encounter_id.",
    "An Agent C receipt query_id and a QueryExecutionTrace query_id are "
    "different identities. Preserve both. Do not overwrite the trace.",
    "Construct DecisionSpecialistRequest only after source currentness, an "
    "allowed population aircraft id, and a permitted recommendation id that "
    "is not a copied comparison id. Do not add comparison fields to the request.",
)


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _epoch(value: str) -> int:
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _resource(specialist: MasterSpecialist, tool_name: str, operation: ReadOperation) -> str:
    matches = [
        resource_id
        for resource_id, kind in server_read_resources(specialist, tool_name)
        if kind is operation
    ]
    if len(matches) != 1:
        _fail("invalid_read_resource")
    return matches[0]


def _reject_provider(provider: object) -> None:
    if type(provider) is ScriptedTestWorker:
        _fail("specialist_execution_not_granted")
    if not callable(getattr(provider, "complete", None)):
        _fail("model_origin_dispatch_rejected")
    module = (getattr(type(provider), "__module__", "") or "").casefold()
    if any(marker in module for marker in _NETWORK_MARKERS):
        _fail("model_origin_dispatch_rejected")


def _reject_second_client(value: object) -> None:
    if type(value) is OfflineAwsReadClient:
        _fail("second_read_client_rejected")
    module = (getattr(type(value), "__module__", "") or "").casefold()
    if "boto3" in module or "botocore" in module:
        _fail("second_read_client_rejected")


def _native_fields(native_type: type) -> frozenset[str]:
    return frozenset(item.name for item in fields(native_type))


def _query_ids(results: tuple[Any, ...]) -> tuple[str, ...]:
    found: list[str] = []
    for result in results:
        for evidence in getattr(result, "evidence", ()) or ():
            for trace in getattr(evidence, "query_executions", ()) or ():
                query_id = getattr(trace, "query_id", None)
                if isinstance(query_id, str) and query_id not in found:
                    found.append(query_id)
    return tuple(found)


def _source_versions(results: tuple[Any, ...]) -> tuple[str | None, ...]:
    found: list[str | None] = []
    for result in results:
        for evidence in getattr(result, "evidence", ()) or ():
            for record in getattr(evidence, "source_records", ()) or ():
                found.append(getattr(record, "source_version", None))
    return tuple(found)


def _executed_windows(run: HistoricalSpecialistRunResult) -> tuple[tuple[str, str], ...]:
    windows: list[tuple[str, str]] = []
    for invocation in run.executed_invocations:
        arguments = invocation.executed_arguments
        if not isinstance(arguments, Mapping):
            _fail("historical_window_not_authorized")
        start = arguments.get("start_utc")
        end = arguments.get("end_utc")
        if not isinstance(start, str) or not isinstance(end, str):
            _fail("historical_window_not_authorized")
        windows.append((start, end))
    return tuple(windows)


class _BoundedProvider:
    """Stops a provider that would pass the server turn budget or a cancel."""

    def __init__(
        self,
        inner: object,
        *,
        max_turns: int,
        cancelled: Callable[[], bool] | None,
    ) -> None:
        self._inner = inner
        self._max_turns = max_turns
        self._cancelled = cancelled
        self.turns = 0
        self.rejection: str | None = None

    def complete(self, turn: object) -> object:
        if self._cancelled is not None and self._cancelled() is True:
            self.rejection = "schedule_cancelled"
            _fail(self.rejection)
        if self.turns >= self._max_turns:
            self.rejection = "server_budget_exceeded"
            _fail(self.rejection)
        self.turns += 1
        complete = getattr(self._inner, "complete")
        return complete(turn)


class ScopedReadHistoricalOperations:
    """Historical operations that read only through the injected port.

    The inner operations object formats a ``HistoricalQueryResponse``. It is
    not an AWS client. A window equal to the unbound plan window, or any
    window other than the host eligible interval, never reaches the port.
    """

    def __init__(
        self,
        inner: object,
        *,
        eligible: tuple[str, str],
        plan_window: tuple[str, str],
        port_call: Callable[[str, str, str], object | None],
        max_tool_calls: int,
    ) -> None:
        _reject_second_client(inner)
        self._inner = inner
        self._eligible = eligible
        self._plan_window = plan_window
        self._port_call = port_call
        self._max_tool_calls = max_tool_calls
        self.calls: list[str] = []
        self.receipts: list[object] = []
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
        if not isinstance(start, str) or not isinstance(end, str):
            self.rejection = "historical_window_not_authorized"
            _fail(self.rejection)
        receipt = self._port_call(name, start, end, request)
        if receipt is None:
            self.rejection = "scoped_read_refused"
            _fail(self.rejection)
        self.calls.append(name)
        self.receipts.append(receipt)
        method = getattr(self._inner, name, None)
        if not callable(method):
            self.rejection = "invalid_historical_operations"
            _fail(self.rejection)
        return method(request, as_of_utc=as_of_utc)


def _scope_historical(
    identity: Sprint3CBoundInvocationIdentity,
    binding: MasterHistoricalBinding,
    host: LocalTestHostScope,
    *,
    idempotency_key: str,
) -> tuple[str, str]:
    if host.specialist is not MasterSpecialist.HISTORICAL_ANALYTICS:
        _fail("specialist_mismatch")
    if host.historical_start_utc is None or host.historical_end_utc is None:
        _fail("historical_window_not_authorized")
    resource_id = _resource(
        MasterSpecialist.HISTORICAL_ANALYTICS,
        _HISTORICAL_TOOL,
        ReadOperation.QUERY,
    )
    hints = EffectiveScopeRequestHints(
        master_request_id=identity.master_request_id,
        planning_session_id=identity.planning_session_id,
        step_id=identity.step_id,
        invocation_id=identity.invocation_id,
        correlation_id=identity.correlation_id,
        idempotency_key=idempotency_key,
        specialist=identity.specialist,
        purpose=identity.purpose,
        capability=identity.capability,
        tool_name=_HISTORICAL_TOOL,
        resource_id=resource_id,
        operation=ReadOperation.QUERY,
        accepted_at_utc=identity.accepted_at_utc,
        deadline_utc=identity.deadline_utc,
        arguments=(
            ("end_utc", host.historical_end_utc),
            ("start_utc", host.historical_start_utc),
        ),
        location_hint="CA",
        historical_start_utc=binding.unbound_plan_window_start_utc,
        historical_end_utc=binding.unbound_plan_window_end_utc,
        unbound_plan_window_start_utc=binding.unbound_plan_window_start_utc,
        unbound_plan_window_end_utc=binding.unbound_plan_window_end_utc,
        copy_unbound_plan_window=False,
    )
    decision = resolve_master_authorized_scope(hints, host)
    window = decision.historical_window
    if (
        decision.status is not EffectiveScopeStatus.BOUNDED_DESCRIBED
        or window.eligible_start_utc != host.historical_start_utc
        or window.eligible_end_utc != host.historical_end_utc
        or window.copied_into_specialist_request
        or window.plan_window_used_as_tool_arguments
    ):
        _fail("historical_window_not_authorized")
    eligible = (window.eligible_start_utc, window.eligible_end_utc)
    plan = (binding.unbound_plan_window_start_utc, binding.unbound_plan_window_end_utc)
    if eligible == plan or None in eligible:
        _fail("historical_window_not_authorized")
    assert isinstance(eligible[0], str) and isinstance(eligible[1], str)
    return eligible[0], eligible[1]


def _read_binding(
    identity: Sprint3CBoundInvocationIdentity,
    authority: Sprint3CConsumedDispatchAuthority,
    *,
    tool_name: str,
    resource_id: str,
    operation: ReadOperation,
    idempotency_key: str,
    fingerprint: str,
    historical_window: tuple[str, str] | None,
    aircraft_id: str | None,
    recommendation_id: str | None,
) -> object:
    if authority.principal is None or authority.grant_envelope is None:
        _fail("principal_not_sealed")
    return describe_consumed_attempt_read_binding(
        owner_id=authority.owner_id,
        attempt_token=authority.attempt_token,
        fence_epoch=authority.fence_epoch,
        grant_nonce=authority.grant_nonce,
        idempotency_key=idempotency_key,
        payload_fingerprint=fingerprint,
        ledger_state=LEDGER_EXECUTION_STATE_STARTED,
        master_request_id=identity.master_request_id,
        planning_session_id=identity.planning_session_id,
        step_id=identity.step_id,
        invocation_id=identity.invocation_id,
        specialist=identity.specialist,
        purpose=identity.purpose,
        capability=identity.capability,
        principal_ref=authority.principal.reference,
        policy_revision=authority.grant_envelope.policy_revision,
        tool_name=tool_name,
        resource_id=resource_id,
        operation=operation,
        region_scope=("CA",),
        accepted_at_utc=identity.accepted_at_utc,
        deadline_utc=identity.deadline_utc,
        correlation_id=identity.correlation_id,
        grant_consumption_lifecycle=GRANT_CONSUMPTION_LIFECYCLE,
        historical_window=historical_window,
        decision_aircraft_id=aircraft_id,
        decision_recommendation_id=recommendation_id,
    )


def _port_read(
    port: Sprint3CScopedReadPort,
    binding: object,
    identity: Sprint3CBoundInvocationIdentity,
    *,
    tool_name: str,
    resource_id: str,
    operation: ReadOperation,
    arguments: tuple[tuple[str, object], ...],
    action_id: str,
    host: LocalTestHostScope,
    unbound_plan_window: tuple[str, str] | None,
) -> object | None:
    request = UntrustedReadRequest(
        master_request_id=identity.master_request_id,
        planning_session_id=identity.planning_session_id,
        step_id=identity.step_id,
        invocation_id=identity.invocation_id,
        specialist=identity.specialist,
        purpose=identity.purpose,
        capability=identity.capability,
        tool_name=tool_name,
        tool_call_id=f"scoped-{action_id}",
        resource_id=resource_id,
        operation=operation,
        requested_arguments=arguments,
        correlation_id=identity.correlation_id,
    )
    result = port.execute(
        binding,
        request,
        host,
        tool_call_id=f"scoped-{action_id}",
        action_id=action_id,
        location_hint="CA",
        unbound_plan_window=unbound_plan_window,
        copy_unbound_plan_window=False,
    )
    if result.refusal is not None or result.receipt is None or result.adapter_invoked is not True:
        return None
    if result.receipt.rows_returned < 1 or result.receipt.page_empty is not False:
        return None
    return result.receipt


@dataclass(frozen=True)
class Sprint3DHistoricalNativeExecution:
    """Host identity bound to one intermediate run and its final result."""

    identity: Sprint3CBoundInvocationIdentity
    specialist_request: SpecialistRequest
    trusted_context: HistoricalSpecialistTrustedContext
    intermediate_run: HistoricalSpecialistRunResult
    final_result: SpecialistResult
    eligible_start_utc: str
    eligible_end_utc: str
    unbound_plan_start_utc: str
    unbound_plan_end_utc: str
    native_source_versions: tuple[str | None, ...]
    read_source_versions: tuple[str, ...]
    native_query_ids: tuple[str, ...]
    read_query_ids: tuple[str, ...]
    host_proofs_satisfied: tuple[HostProofRequirement, ...] = ()
    native_run_invoked: bool = True
    finalization_performed: bool = True
    intermediate_is_final: bool = False
    native_result_carries_host_correlation: bool = False
    plan_window_copied_into_request: bool = False
    plan_window_used_as_tool_arguments: bool = False
    scripted_worker_used: bool = False
    production_dispatch: bool = False
    native_execution_is_source_evidence: bool = False
    source_version_authenticated: bool = False
    query_id_authenticated: bool = False
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    schema_version: str = SPRINT3D_NATIVE_SPECIALISTS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SPRINT3D_NATIVE_SPECIALISTS_SCHEMA_VERSION:
            _fail("invalid_schema_version")
        if not isinstance(self.identity, Sprint3CBoundInvocationIdentity):
            _fail("request_identity_mismatch")
        if self.identity.specialist is not MasterSpecialist.HISTORICAL_ANALYTICS:
            _fail("specialist_mismatch")
        if not isinstance(self.specialist_request, SpecialistRequest):
            _fail("invalid_specialist_request")
        if not isinstance(self.intermediate_run, HistoricalSpecialistRunResult):
            _fail("historical_run_not_final_artifact")
        if not isinstance(self.final_result, SpecialistResult):
            _fail("historical_run_not_final_artifact")
        if self.final_result.tool_results != self.intermediate_run.tool_results:
            _fail("historical_result_lineage_mismatch")
        if _query_ids(self.final_result.tool_results) != self.native_query_ids:
            _fail("query_id_lineage_mismatch")
        if _source_versions(self.final_result.tool_results) != self.native_source_versions:
            _fail("source_version_lineage_mismatch")
        correlation = set(_CORRELATION_FIELDS)
        if correlation & _native_fields(HistoricalSpecialistRunResult):
            _fail("invalid_native_result_gap")
        if correlation & _native_fields(SpecialistResult):
            _fail("invalid_native_result_gap")
        plan = (self.unbound_plan_start_utc, self.unbound_plan_end_utc)
        eligible = (self.eligible_start_utc, self.eligible_end_utc)
        if plan == eligible:
            _fail("historical_window_not_authorized")
        if "start_utc" in self.specialist_request.to_dict() or "end_utc" in self.specialist_request.to_dict():
            _fail("historical_window_not_authorized")
        windows = _executed_windows(self.intermediate_run)
        if not windows or any(item != eligible for item in windows) or any(item == plan for item in windows):
            _fail("historical_window_not_authorized")
        if self.trusted_context.as_of_utc == self.identity.accepted_at_utc:
            _fail("application_clock_not_evaluation_time")
        if self.specialist_request.request_id != self.identity.specialist_request_id:
            _fail("specialist_request_id_mismatch")
        closed = (
            self.native_run_invoked is not True,
            self.finalization_performed is not True,
            self.intermediate_is_final is not False,
            self.native_result_carries_host_correlation is not False,
            self.plan_window_copied_into_request is not False,
            self.plan_window_used_as_tool_arguments is not False,
            self.scripted_worker_used is not False,
            self.production_dispatch is not False,
            self.native_execution_is_source_evidence is not False,
            self.source_version_authenticated is not False,
            self.query_id_authenticated is not False,
            self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED,
            self.host_proofs_satisfied != (),
        )
        if any(closed):
            _fail("execution_not_granted")


@dataclass(frozen=True)
class Sprint3DDecisionNativeExecution:
    """One Decision request built from host proofs, then the native run."""

    identity: Sprint3CBoundInvocationIdentity
    resolution: MasterDecisionTargetResolution
    request: DecisionSpecialistRequest
    native_result: DecisionSpecialistRunResult
    population_aircraft_id: str
    permitted_recommendation_id: str
    read_source_versions: tuple[str, ...]
    read_query_ids: tuple[str, ...]
    host_proofs_satisfied: tuple[HostProofRequirement, ...]
    comparison_aircraft_id: str | None
    comparison_recommendation_id: str | None
    native_run_invoked: bool = True
    comparison_id_used_as_target: bool = False
    scripted_worker_used: bool = False
    production_dispatch: bool = False
    native_execution_is_source_evidence: bool = False
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    schema_version: str = SPRINT3D_NATIVE_SPECIALISTS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SPRINT3D_NATIVE_SPECIALISTS_SCHEMA_VERSION:
            _fail("invalid_schema_version")
        if self.identity.specialist is not MasterSpecialist.DECISION:
            _fail("specialist_mismatch")
        if not isinstance(self.request, DecisionSpecialistRequest):
            _fail("invalid_decision_request")
        if not isinstance(self.native_result, DecisionSpecialistRunResult):
            _fail("invalid_decision_result")
        if self.request.mode is not DecisionTargetMode.AIRCRAFT:
            _fail("invalid_decision_target")
        if self.request.aircraft_id != self.population_aircraft_id:
            _fail("decision_target_not_established")
        if self.request.recommendation_id is not None:
            _fail("persisted_recommendation_not_separately_authorized")
        if self.request.aircraft_id == self.comparison_aircraft_id and self.comparison_id_used_as_target:
            _fail("comparison_candidate_not_a_target")
        if self.permitted_recommendation_id == self.comparison_recommendation_id:
            _fail("persisted_recommendation_not_separately_authorized")
        if self.request.request_id != self.identity.specialist_request_id:
            _fail("specialist_request_id_mismatch")
        if self.host_proofs_satisfied != REQUIRED_HOST_PROOFS:
            _fail("caller_proof_not_host_issued")
        if self.resolution.decision_request is not None:
            _fail("execution_not_granted")
        closed = (
            self.native_run_invoked is not True,
            self.comparison_id_used_as_target is not False,
            self.scripted_worker_used is not False,
            self.production_dispatch is not False,
            self.native_execution_is_source_evidence is not False,
            self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED,
        )
        if any(closed):
            _fail("execution_not_granted")


def _user_aircraft_ids(resolution: MasterDecisionTargetResolution) -> set[str]:
    return {
        item.identifier
        for item in resolution.candidates
        if item.role is DecisionCandidateRole.USER_SUPPLIED_AIRCRAFT_IDENTIFIER
        and item.field_name == "aircraft_id"
    }


def _establish_decision_target(
    identity: Sprint3CBoundInvocationIdentity,
    resolution: MasterDecisionTargetResolution,
    binding: MasterDecisionBinding | None,
    host: LocalTestHostScope,
    authority: Sprint3CConsumedDispatchAuthority,
    port: Sprint3CScopedReadPort,
    *,
    idempotency_key: str,
    fingerprint: str,
) -> tuple[str, str, object]:
    if host.specialist is not MasterSpecialist.DECISION:
        _fail("specialist_mismatch")
    observed = resolution.observed_aircraft_id
    user_ids = _user_aircraft_ids(resolution)
    comparison_aircraft = None if binding is None else binding.comparison_candidate_aircraft_id
    comparison_recommendation = (
        None if binding is None else binding.comparison_candidate_recommendation_id
    )
    permitted = set(host.permitted_recommendation_ids)
    if len(permitted) != 1:
        _fail("persisted_recommendation_not_separately_authorized")
    permitted_id = next(iter(permitted))
    if comparison_recommendation is not None and permitted_id == comparison_recommendation:
        _fail("persisted_recommendation_not_separately_authorized")
    if (
        resolution.status is not DecisionResolutionStatus.UNIQUE_CANDIDATE_NOT_ELIGIBLE
        or observed is None
        or observed not in user_ids
    ):
        if comparison_aircraft is not None and observed is None:
            _fail("comparison_candidate_not_a_target")
        _fail("decision_target_not_established")
    if comparison_aircraft is not None and comparison_aircraft not in user_ids:
        _fail("comparison_candidate_not_a_target")
    if observed not in host.population_entity_ids or observed not in host.validated_aircraft_ids:
        _fail("decision_target_not_established")
    if host.row_present is not True or host.row_entity_id != observed:
        _fail("source_currentness_not_established")
    if host.risk_presence is None or host.source_expires_utc is None:
        _fail("source_currentness_not_established")
    if _epoch(host.evaluation_utc) >= _epoch(host.source_expires_utc):
        _fail("source_currentness_not_established")
    resource_id = _resource(MasterSpecialist.DECISION, _DECISION_TOOL, ReadOperation.GET_ITEM)
    read_binding = _read_binding(
        identity,
        authority,
        tool_name=_DECISION_TOOL,
        resource_id=resource_id,
        operation=ReadOperation.GET_ITEM,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
        historical_window=None,
        aircraft_id=observed,
        recommendation_id=permitted_id,
    )
    hints = EffectiveScopeRequestHints(
        master_request_id=identity.master_request_id,
        planning_session_id=identity.planning_session_id,
        step_id=identity.step_id,
        invocation_id=identity.invocation_id,
        correlation_id=identity.correlation_id,
        idempotency_key=idempotency_key,
        specialist=identity.specialist,
        purpose=identity.purpose,
        capability=identity.capability,
        tool_name=_DECISION_TOOL,
        resource_id=resource_id,
        operation=ReadOperation.GET_ITEM,
        accepted_at_utc=identity.accepted_at_utc,
        deadline_utc=identity.deadline_utc,
        arguments=(("aircraft_id", observed),),
        location_hint="CA",
        aircraft_id=observed,
        recommendation_id=permitted_id,
    )
    scoped = resolve_master_authorized_scope(hints, host)
    target = scoped.decision_target_scope
    if (
        scoped.status is not EffectiveScopeStatus.BOUNDED_DESCRIBED
        or target.described_aircraft_id != observed
        or target.aircraft_id_in_validated_intersection is not True
        or target.described_recommendation_id != permitted_id
        or target.separate_recommendation_permission_in_intersection is not True
        or target.target_authorization_granted is not False
    ):
        _fail("decision_target_not_established")
    receipt = _port_read(
        port,
        read_binding,
        identity,
        tool_name=_DECISION_TOOL,
        resource_id=resource_id,
        operation=ReadOperation.GET_ITEM,
        arguments=(("aircraft_id", observed),),
        action_id="decision-currentness",
        host=host,
        unbound_plan_window=None,
    )
    if receipt is None:
        _fail("source_currentness_not_established")
    return observed, permitted_id, receipt


def execute_historical_native(
    orchestrator: Sprint3CNativeSpecialistOrchestrator,
    invocation: object,
    binding: MasterHistoricalBinding,
    authority: Sprint3CConsumedDispatchAuthority,
    *,
    now_utc: str,
    host: LocalTestHostScope,
    port: Sprint3CScopedReadPort,
    provider: object,
    operations: object,
    cancelled: Callable[[], bool] | None = None,
    deadline_epoch: int | None = None,
    observed_epoch: int | None = None,
    tool_call_id_factory: Callable[[], str] | None = None,
) -> Sprint3DHistoricalNativeExecution:
    """Run Historical Analytics inside the host eligible window."""

    _reject_provider(provider)
    _reject_second_client(operations)
    if type(orchestrator) is not Sprint3CNativeSpecialistOrchestrator:
        _fail("invalid_schedule")
    if not isinstance(binding, MasterHistoricalBinding):
        _fail("invalid_historical_binding")
    if type(host) is not LocalTestHostScope or type(port) is not Sprint3CScopedReadPort:
        _fail("host_not_issued")
    if port.live_aws_initiated is not False:
        _fail("production_dispatch_disabled")
    identity, _context = orchestrator.reserve_native_dispatch(
        invocation,  # type: ignore[arg-type]
        authority,
        expected=MasterSpecialist.HISTORICAL_ANALYTICS,
        now_utc=now_utc,
    )
    orchestrator.reject_closed_before_native_run(
        identity,
        now_utc=now_utc,
        cancelled=cancelled,
        deadline_epoch=deadline_epoch,
        observed_epoch=observed_epoch,
    )
    if identity.invocation_id != binding.invocation_id:
        _fail("invocation_binding_mismatch")
    if identity.specialist_request_id != binding.specialist_request_id:
        _fail("specialist_request_id_mismatch")
    if binding.specialist_request.request_id != identity.specialist_request_id:
        _fail("specialist_request_id_mismatch")
    fingerprint = authority.payload_fingerprint or "payload-sprint3d"
    idempotency_key = identity.idempotency_key
    eligible_start, eligible_end = _scope_historical(
        identity,
        binding,
        host,
        idempotency_key=idempotency_key,
    )
    if host.evaluation_utc == identity.accepted_at_utc:
        _fail("application_clock_not_evaluation_time")
    resource_id = _resource(
        MasterSpecialist.HISTORICAL_ANALYTICS,
        _HISTORICAL_TOOL,
        ReadOperation.QUERY,
    )
    read_binding = _read_binding(
        identity,
        authority,
        tool_name=_HISTORICAL_TOOL,
        resource_id=resource_id,
        operation=ReadOperation.QUERY,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
        historical_window=(
            binding.unbound_plan_window_start_utc,
            binding.unbound_plan_window_end_utc,
        ),
        aircraft_id=None,
        recommendation_id=None,
    )
    plan = (binding.unbound_plan_window_start_utc, binding.unbound_plan_window_end_utc)

    def port_call(tool_name: str, start: str, end: str, native_request: object) -> object | None:
        resource = _resource(MasterSpecialist.HISTORICAL_ANALYTICS, tool_name, ReadOperation.QUERY)
        tool_binding = read_binding
        if tool_name != _HISTORICAL_TOOL:
            tool_binding = _read_binding(
                identity,
                authority,
                tool_name=tool_name,
                resource_id=resource,
                operation=ReadOperation.QUERY,
                idempotency_key=f"{idempotency_key}-{tool_name}",
                fingerprint=fingerprint,
                historical_window=plan,
                aircraft_id=None,
                recommendation_id=None,
            )
        arguments: list[tuple[str, object]] = [("end_utc", end), ("start_utc", start)]
        aircraft_id = getattr(native_request, "aircraft_id", None)
        hazard_id = getattr(native_request, "hazard_id", None)
        if isinstance(aircraft_id, str):
            arguments.append(("aircraft_id", aircraft_id))
        elif isinstance(hazard_id, str):
            arguments.append(("hazard_id", hazard_id))
        return _port_read(
            port,
            tool_binding,
            identity,
            tool_name=tool_name,
            resource_id=resource,
            operation=ReadOperation.QUERY,
            arguments=tuple(arguments),
            action_id=f"historical-{tool_name}",
            host=host,
            unbound_plan_window=plan,
        )

    scoped_operations = ScopedReadHistoricalOperations(
        operations,
        eligible=(eligible_start, eligible_end),
        plan_window=plan,
        port_call=port_call,
        max_tool_calls=identity.max_tool_calls,
    )
    bounded = _BoundedProvider(
        provider,
        max_turns=identity.max_model_turns,
        cancelled=cancelled,
    )
    trusted = HistoricalSpecialistTrustedContext(as_of_utc=host.evaluation_utc)
    specialist = HistoricalAnalyticsSpecialist(
        provider=bounded,
        operations=scoped_operations,
        tool_call_id_factory=tool_call_id_factory,
    )
    run = specialist.run(binding.specialist_request, trusted)
    if bounded.rejection is not None:
        _fail(bounded.rejection)
    if scoped_operations.rejection is not None:
        _fail(scoped_operations.rejection)
    if not isinstance(run, HistoricalSpecialistRunResult):
        _fail("invalid_historical_run")
    if run.provider_turn_count > identity.max_model_turns:
        _fail("server_budget_exceeded")
    if run.executed_tool_call_count > identity.max_tool_calls:
        _fail("server_budget_exceeded")
    final = finalize_historical_specialist_run(run)
    versions = _source_versions(run.tool_results)
    query_ids = _query_ids(run.tool_results)
    read_versions = tuple(
        receipt.source_version
        for receipt in scoped_operations.receipts
        if isinstance(getattr(receipt, "source_version", None), str)
    )
    read_queries = tuple(
        receipt.query_id
        for receipt in scoped_operations.receipts
        if isinstance(getattr(receipt, "query_id", None), str)
    )
    return Sprint3DHistoricalNativeExecution(
        identity=identity,
        specialist_request=binding.specialist_request,
        trusted_context=trusted,
        intermediate_run=run,
        final_result=final,
        eligible_start_utc=eligible_start,
        eligible_end_utc=eligible_end,
        unbound_plan_start_utc=plan[0],
        unbound_plan_end_utc=plan[1],
        native_source_versions=versions,
        read_source_versions=read_versions,
        native_query_ids=query_ids,
        read_query_ids=read_queries,
    )


def execute_decision_native(
    orchestrator: Sprint3CNativeSpecialistOrchestrator,
    invocation: object,
    readiness: MasterDecisionExecutionReadiness,
    reads: FakeDecisionReadCatalog,
    authority: Sprint3CConsumedDispatchAuthority,
    *,
    now_utc: str,
    user_text: str,
    host: LocalTestHostScope,
    port: Sprint3CScopedReadPort,
    provider: object,
    tables: object,
    source_epoch: int,
    query_timestamp_utc: str,
    binding: MasterDecisionBinding | None = None,
    cancelled: Callable[[], bool] | None = None,
    deadline_epoch: int | None = None,
    observed_epoch: int | None = None,
    tool_call_id_factory: Callable[[], str] | None = None,
) -> Sprint3DDecisionNativeExecution:
    """Build a Decision request from host proofs, then call the specialist."""

    _reject_provider(provider)
    _reject_second_client(tables)
    if type(orchestrator) is not Sprint3CNativeSpecialistOrchestrator:
        _fail("invalid_schedule")
    if not isinstance(readiness, MasterDecisionExecutionReadiness):
        _fail("invalid_decision_readiness")
    if not isinstance(reads, FakeDecisionReadCatalog):
        _fail("invalid_decision_read")
    if type(host) is not LocalTestHostScope or type(port) is not Sprint3CScopedReadPort:
        _fail("host_not_issued")
    if tables is None:
        _fail("invalid_tables")
    if port.live_aws_initiated is not False:
        _fail("production_dispatch_disabled")
    if not isinstance(user_text, str) or not user_text.strip():
        _fail("invalid_user_text")
    if type(source_epoch) is not int:
        _fail("invalid_source_observation_epoch")
    identity, _context = orchestrator.reserve_native_dispatch(
        invocation,  # type: ignore[arg-type]
        authority,
        expected=MasterSpecialist.DECISION,
        now_utc=now_utc,
    )
    orchestrator.reject_closed_before_native_run(
        identity,
        now_utc=now_utc,
        cancelled=cancelled,
        deadline_epoch=deadline_epoch,
        observed_epoch=observed_epoch,
    )
    if binding is not None:
        if not isinstance(binding, MasterDecisionBinding):
            _fail("invalid_decision_binding")
        if identity.invocation_id != binding.invocation_id:
            _fail("invocation_binding_mismatch")
    if query_timestamp_utc == identity.accepted_at_utc:
        _fail("application_clock_not_evaluation_time")
    resolution = resolve_master_decision_target(readiness, reads)
    if resolution.decision_request is not None or resolution.native_request_built:
        _fail("execution_not_granted")
    fingerprint = authority.payload_fingerprint or "payload-sprint3d"
    aircraft_id, recommendation_id, receipt = _establish_decision_target(
        identity,
        resolution,
        binding,
        host,
        authority,
        port,
        idempotency_key=identity.idempotency_key,
        fingerprint=fingerprint,
    )
    if aircraft_id == recommendation_id:
        _fail("request_identity_not_distinct")
    request = DecisionSpecialistRequest(
        user_text=user_text,
        mode=DecisionTargetMode.AIRCRAFT,
        aircraft_id=aircraft_id,
        recommendation_id=None,
        request_id=identity.specialist_request_id,
    )
    if request.aircraft_id == (
        None if binding is None else binding.comparison_candidate_aircraft_id
    ) and aircraft_id not in _user_aircraft_ids(resolution):
        _fail("comparison_candidate_not_a_target")
    runtime = DecisionToolsRuntime(
        tables=tables,
        now_epoch=source_epoch,
        query_timestamp_utc=query_timestamp_utc,
        correlation_id=identity.correlation_id,
    )
    bounded = _BoundedProvider(
        provider,
        max_turns=identity.max_model_turns,
        cancelled=cancelled,
    )
    specialist = DecisionSpecialist(
        provider=bounded,
        tool_call_id_factory=tool_call_id_factory,
    )
    result = specialist.run(request, runtime)
    if bounded.rejection is not None:
        _fail(bounded.rejection)
    if not isinstance(result, DecisionSpecialistRunResult):
        _fail("invalid_decision_result")
    if result.provider_turn_count > identity.max_model_turns:
        _fail("server_budget_exceeded")
    if result.executed_tool_call_count > identity.max_tool_calls:
        _fail("server_budget_exceeded")
    if result.aircraft_id != request.aircraft_id or result.recommendation_id is not None:
        _fail("decision_target_not_established")
    read_versions = (
        (receipt.source_version,)
        if isinstance(getattr(receipt, "source_version", None), str)
        else ()
    )
    read_queries = (
        (receipt.query_id,) if isinstance(getattr(receipt, "query_id", None), str) else ()
    )
    return Sprint3DDecisionNativeExecution(
        identity=identity,
        resolution=resolution,
        request=request,
        native_result=result,
        population_aircraft_id=aircraft_id,
        permitted_recommendation_id=recommendation_id,
        read_source_versions=read_versions,
        read_query_ids=read_queries,
        host_proofs_satisfied=REQUIRED_HOST_PROOFS,
        comparison_aircraft_id=None if binding is None else binding.comparison_candidate_aircraft_id,
        comparison_recommendation_id=(
            None if binding is None else binding.comparison_candidate_recommendation_id
        ),
    )


__all__ = [
    "NATIVE_EXECUTION_IS_SOURCE_EVIDENCE",
    "NATIVE_OFFLINE_EXECUTION_IS_PRODUCTION_DISPATCH",
    "SCRIPTED_WORKER_IS_NATIVE_EXECUTION",
    "SPRINT3D_HISTORICAL_CONTRACT_MIGRATION",
    "SPRINT3D_NATIVE_SPECIALISTS_SCHEMA_VERSION",
    "ScopedReadHistoricalOperations",
    "Sprint3DDecisionNativeExecution",
    "Sprint3DHistoricalNativeExecution",
    "execute_decision_native",
    "execute_historical_native",
]
