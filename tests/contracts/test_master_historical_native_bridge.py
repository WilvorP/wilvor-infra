"""Native Historical bridge. Fakes never become authenticated aviation evidence."""

from __future__ import annotations

import inspect
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace

import pytest

from tests.fakes.recording_historical_operations import RecordingHistoricalOperations
from tests.fakes.scripted_model_provider import ScriptedModelProvider
from wilvor_ai.contracts import (
    AgentCapability,
    ContractValidationError,
    TemporalScope,
    ToolResultStatus,
)
from wilvor_ai.historical_analytics import HISTORICAL_ANALYTICS_TOOLS
from wilvor_ai.historical_analytics_mapping import RESULT_TRUNCATED_LIMITATION
from wilvor_ai.historical_specialist import (
    HistoricalAnalyticsSpecialist,
    SequentialToolCallIdFactory,
)
from wilvor_ai.master_contracts import MasterBindingDefect, MasterSpecialist
from wilvor_ai.master_evidence_admission import (
    MasterAdmissionReason,
    MasterSourceAuthenticity,
)
from wilvor_ai.master_historical_binding import prepare_master_historical_binding
from wilvor_ai.master_historical_native_bridge import (
    EVALUABLE_COMPLETENESS_STATUS,
    HISTORICAL_FINALIZATION_ENTRYPOINT,
    HISTORICAL_TOOL_ARGUMENT_ALLOWLIST,
    SPRINT_3B2_NATIVE_CONTRACT_MIGRATION_NOTES,
    HistoricalQueryObservation,
    HistoricalRowObservation,
    HistoricalToolArgumentSource,
    IndependentHistoricalToolInterval,
    MasterHistoricalNativeObservation,
    NativeObservationOrigin,
    correlate_master_historical_native_collection,
    correlate_master_historical_native_result,
    prepare_master_historical_native_invocation,
    sprint_3b2_historical_native_contract_migration,
)
from wilvor_ai.master_parallel_contracts import MasterParallelContractBundle
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterPurpose,
    MasterRequirement,
    MasterRouteExecutionAuthority,
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.master_specialist_adapters import describe_planned_adapters
from wilvor_ai.model_contracts import ModelDecision, ModelDecisionKind, ProposedToolCall
from wilvor_ai.specialist_contracts import (
    ExactCountClaim,
    HistoricalSpecialistTrustedContext,
    SpecialistRequest,
    SpecialistResult,
    SpecialistStatus,
    UnsupportedReason,
    VerifiedZeroClaim,
    VerifierOutcome,
)
from wilvor_ai.specialist_runtime_contracts import (
    ExecutedToolInvocation,
    HistoricalSpecialistRunResult,
    SpecialistRunStatus,
)
from wilvor_historical.coverage_contracts import Evaluability
from wilvor_historical.query_contracts import (
    INTERNAL_QUERY_ID_LIST_ENCOUNTERS,
    INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,
    CoverageEvidence,
    EncounterSummaryResult,
    HistoricalEncounterRecord,
    HistoricalOperation,
    HistoricalQueryResponse,
    HistoricalQueryStatus,
    ListEncountersResult,
    ListHistoricalEncountersRequest,
    QueryEvidence,
    QueryExecutionEvidence,
    SummarizeHistoricalEncountersRequest,
)
import wilvor_ai.master_historical_native_bridge as bridge_module


TEXT = "Which recorded historical encounters apply to the selected aircraft?"
MASTER = "master-req-hist-bridge-1"
CORRELATION = "corr-hist-bridge-1"
APPLICATION_TIME = "2026-01-15T00:00:01Z"
REQUESTED_TIME = "2026-01-10T12:00:00Z"
AS_OF = "2026-09-13T12:00:00Z"
PLAN_START = "2026-01-01T00:00:00Z"
PLAN_END = "2026-01-02T00:00:00Z"
SECOND_START = "2026-03-01T00:00:00Z"
SECOND_END = "2026-03-02T00:00:00Z"
TOOL_START = "2026-09-11T10:00:00Z"
TOOL_END = "2026-09-11T11:00:00Z"
AUTHORIZED_START = "2026-02-01T00:00:00Z"
AUTHORIZED_END = "2026-02-02T00:00:00Z"
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS


class DeterministicHistoricalNativeHarness:
    """Test-only specialist runner. Its result is not authenticated evidence."""

    def __init__(self, script, responses) -> None:
        if not isinstance(script, tuple):
            raise TypeError("script must be a tuple")
        provider_type = ScriptedModelProvider
        operations_type = RecordingHistoricalOperations
        if provider_type.__module__ != "tests.fakes.scripted_model_provider":
            raise TypeError("provider must be the deterministic test fake")
        if operations_type.__module__ != "tests.fakes.recording_historical_operations":
            raise TypeError("operations must be the deterministic test fake")
        self.provider = provider_type(script)
        self.operations = operations_type(responses)
        self.specialist = HistoricalAnalyticsSpecialist(
            provider=self.provider,
            operations=self.operations,
            tool_call_id_factory=SequentialToolCallIdFactory(),
        )

    def run(
        self,
        binding,
        *,
        trusted_context: HistoricalSpecialistTrustedContext,
        independent_tool_interval: IndependentHistoricalToolInterval | None = None,
        expected_source_version: str | None = None,
    ):
        host = prepare_master_historical_native_invocation(
            binding,
            trusted_context=trusted_context,
            independent_tool_interval=independent_tool_interval,
        )
        native_run = self.specialist.run(host.specialist_request, trusted_context)
        return correlate_master_historical_native_result(
            binding,
            native_run=native_run,
            observed_request=host.specialist_request,
            trusted_context=trusted_context,
            independent_tool_interval=independent_tool_interval,
            expected_source_version=expected_source_version,
        )


def _context(master: str = MASTER) -> MasterPlanningContext:
    return MasterPlanningContext(
        master_request_id=master,
        correlation_id=CORRELATION,
        application_request_time_utc=APPLICATION_TIME,
        canonical_user_text=TEXT,
    )


def _history(
    step_id: str = "hist-1",
    *,
    start: str = PLAN_START,
    end: str = PLAN_END,
) -> MasterProposedStep:
    return MasterProposedStep(
        step_id=step_id,
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=HISTORY,
        temporal_intent=MasterRouteTemporalIntent.HISTORICAL,
        historical_start_utc=start,
        historical_end_utc=end,
    )


def _bundle(
    *steps: MasterProposedStep,
    requested_evaluation_time_utc: str | None = REQUESTED_TIME,
    master: str = MASTER,
) -> MasterParallelContractBundle:
    context = _context(master)
    decision = authorize_route(
        MasterRouteProposal(
            user_text=TEXT,
            capabilities=(MasterProposedCapability(HISTORY, MasterRequirement.REQUIRED),),
            steps=steps,
        ),
        context,
    )
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    assert decision.plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    bundle = MasterParallelContractBundle(context=context, plan=decision.plan, evidence_inputs=())
    if requested_evaluation_time_utc is None:
        return bundle
    updated = []
    for step in bundle.plan.steps:
        updated.append(
            replace(
                step,
                invocation=replace(
                    step.invocation,
                    requested_evaluation_time_utc=requested_evaluation_time_utc,
                ),
            )
        )
    return replace(bundle, plan=replace(bundle.plan, steps=tuple(updated)))


def _bindings(*steps: MasterProposedStep, requested: str | None = REQUESTED_TIME, master: str = MASTER):
    bundle = _bundle(*steps, requested_evaluation_time_utc=requested, master=master)
    descriptors = tuple(
        item
        for item in describe_planned_adapters(bundle).descriptors
        if item.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    )
    return tuple(prepare_master_historical_binding(bundle, item) for item in descriptors)


def _binding(**overrides):
    return _bindings(_history(), **overrides)[0]


def _context_as_of(as_of: str = AS_OF) -> HistoricalSpecialistTrustedContext:
    return HistoricalSpecialistTrustedContext(as_of_utc=as_of)


def _interval(
    start: str = AUTHORIZED_START,
    end: str = AUTHORIZED_END,
    interval_id: str = "interval-hist-bridge-1",
) -> IndependentHistoricalToolInterval:
    return IndependentHistoricalToolInterval(interval_id=interval_id, start_utc=start, end_utc=end)


def _coverage() -> CoverageEvidence:
    return CoverageEvidence(
        evaluability=Evaluability.EVALUABLE,
        reason="window_evaluable",
        required_horizon_seconds=173760,
        required_streams=("facts",),
        collection_epoch_ids=("epoch-1",),
    )


def _evidence(
    operation: HistoricalOperation,
    *,
    as_of: str,
    count: int | None = 2,
    exact: bool | None = True,
    minimum: int | None = None,
    query_id: str = INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,
    rows_returned: int = 2,
    start: str = TOOL_START,
    end: str = TOOL_END,
) -> QueryEvidence:
    return QueryEvidence(
        query_name=operation,
        datasets=("encounter",),
        requested_start_utc=start,
        requested_end_utc=end,
        coverage_state=Evaluability.EVALUABLE,
        collection_epoch_ids=("epoch-1",),
        semantic_match_count=count,
        semantic_match_count_is_exact=exact,
        minimum_match_count=minimum,
        query_executions=(
            QueryExecutionEvidence(
                query_id=query_id,
                query_execution_id="exec-bridge-1",
                rows_returned=rows_returned,
                data_scanned_bytes=64,
                workgroup="historical-analytics",
            ),
        ),
        evaluated_as_of_utc=as_of,
    )


def _summary(count: int) -> EncounterSummaryResult:
    if count == 0:
        return EncounterSummaryResult(
            physical_record_count=0,
            distinct_encounter_count=0,
            distinct_aircraft_count=0,
            distinct_hazard_count=0,
            distinct_dedup_count=0,
        )
    return EncounterSummaryResult(
        physical_record_count=count,
        distinct_encounter_count=count,
        distinct_aircraft_count=1,
        distinct_hazard_count=1,
        distinct_dedup_count=count,
        min_event_time_utc="2026-09-11T10:00:00Z",
        max_event_time_utc="2026-09-11T10:30:00Z",
    )


def _record(index: int) -> HistoricalEncounterRecord:
    suffix = str(index)
    return HistoricalEncounterRecord(
        encounter_id=f"bridge-{suffix}#hazard-1#v1",
        record_id=f"bridge-{suffix}#hazard-1#v1",
        dedup_id=f"bridge-{suffix}#hazard-1#v1|ENCOUNTER_OBSERVED|1700000000|DETECTED",
        aircraft_id="abc123",
        hazard_id="hazard-1",
        hazard_version_key="hazard-1#v1",
        fact_kind="ENCOUNTER_OBSERVED",
        encounter_state="DETECTED",
        event_time_utc=f"2026-09-11T10:{index:02d}:00Z",
        hazard_type="CONVECTION",
    )


def _succeeded(as_of: str, *, count: int = 2, rows_returned: int = 2) -> HistoricalQueryResponse:
    return HistoricalQueryResponse(
        status=HistoricalQueryStatus.SUCCEEDED,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        requested_scope=SummarizeHistoricalEncountersRequest(start_utc=TOOL_START, end_utc=TOOL_END),
        coverage=_coverage(),
        evidence=_evidence(
            HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            as_of=as_of,
            count=count,
            rows_returned=rows_returned,
        ),
        result=_summary(count),
    )


def _verified_zero(as_of: str) -> HistoricalQueryResponse:
    return HistoricalQueryResponse(
        status=HistoricalQueryStatus.VERIFIED_ZERO,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        requested_scope=SummarizeHistoricalEncountersRequest(start_utc=TOOL_START, end_utc=TOOL_END),
        coverage=_coverage(),
        evidence=_evidence(
            HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            as_of=as_of,
            count=0,
            rows_returned=0,
        ),
        result=_summary(0),
    )


def _truncated_list(as_of: str) -> HistoricalQueryResponse:
    return HistoricalQueryResponse(
        status=HistoricalQueryStatus.RESULT_TRUNCATED,
        operation=HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
        requested_scope=ListHistoricalEncountersRequest(
            start_utc=TOOL_START,
            end_utc=TOOL_END,
            aircraft_id="abc123",
            limit=1,
        ),
        coverage=_coverage(),
        evidence=_evidence(
            HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
            as_of=as_of,
            count=None,
            exact=False,
            minimum=2,
            query_id=INTERNAL_QUERY_ID_LIST_ENCOUNTERS,
            rows_returned=1,
        ),
        result=ListEncountersResult(records=(_record(1),), truncated=True),
    )


def _listed(as_of: str) -> HistoricalQueryResponse:
    return HistoricalQueryResponse(
        status=HistoricalQueryStatus.SUCCEEDED,
        operation=HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
        requested_scope=ListHistoricalEncountersRequest(
            start_utc=TOOL_START,
            end_utc=TOOL_END,
            aircraft_id="abc123",
            limit=2,
        ),
        coverage=_coverage(),
        evidence=_evidence(
            HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
            as_of=as_of,
            count=2,
            query_id=INTERNAL_QUERY_ID_LIST_ENCOUNTERS,
        ),
        result=ListEncountersResult(records=(_record(1), _record(2))),
    )


def _tool_call(name: str, window: tuple[str, str], **arguments) -> ModelDecision:
    payload = {"start_utc": window[0], "end_utc": window[1], **arguments}
    return ModelDecision(
        kind=ModelDecisionKind.TOOL_CALLS,
        tool_calls=(ProposedToolCall(name=name, arguments=payload),),
    )


def _claims(*claims) -> ModelDecision:
    return ModelDecision(kind=ModelDecisionKind.FINAL_CLAIMS, claims=claims)


def _unsupported() -> ModelDecision:
    return ModelDecision(
        kind=ModelDecisionKind.UNSUPPORTED,
        unsupported_reason=UnsupportedReason.GEOGRAPHY,
    )


def _assert_unauthenticated(envelope) -> None:
    assert envelope.authenticated_execution is False
    assert envelope.caller_supplied_native_result_is_authenticated_execution is False
    assert envelope.evidence_issued is False
    assert envelope.finalization_performed is False
    assert envelope.dispatch_permitted is False
    assert envelope.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert envelope.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert envelope.stops_before_execution_authorization is True
    assert envelope.verified_zero_admitted is False
    assert envelope.failure_interpreted_as_zero is False
    assert envelope.incomplete_coverage_interpreted_as_zero is False
    assert envelope.native_correlation_resolved is False
    assert envelope.native_specialist_request_id_resolved is False
    assert envelope.tool_result_correlation_satisfies_result_contract is False
    assert envelope.requested_time_is_runtime_evaluation_time is False
    assert envelope.application_clock_is_runtime_evaluation_time is False
    assert envelope.evaluated_as_of_is_authenticated_source_time is False
    assert envelope.source_version_authenticated is False
    assert envelope.authenticated_source_version is None
    assert envelope.authenticated_query_id is None
    assert envelope.bridge_copied_unbound_plan_window is False
    assert envelope.run_is_final_artifact is False
    assert envelope.finalization_entrypoint == HISTORICAL_FINALIZATION_ENTRYPOINT
    assert envelope.finalization_entrypoint == "finalize_historical_specialist_run"
    assert envelope.host.binding.historical_run is None
    assert envelope.host.binding.specialist_result is None
    assert envelope.host.binding.trusted_context is None
    assert envelope.readiness.historical_run is None
    assert envelope.readiness.specialist_result is None
    assert envelope.readiness.evidence_issued is False


def test_catalog_allowlist_matches_historical_tools():
    expected = {
        spec.name: frozenset(field.name for field in spec.input_fields)
        for spec in HISTORICAL_ANALYTICS_TOOLS
    }
    assert dict(HISTORICAL_TOOL_ARGUMENT_ALLOWLIST) == expected
    assert EVALUABLE_COMPLETENESS_STATUS == Evaluability.EVALUABLE.value
    assert RESULT_TRUNCATED_LIMITATION == "RESULT_TRUNCATED"


def test_sprint_3b2_migration_records_absent_native_correlation_fields():
    gaps = {
        item.contract_name: item.missing_fields
        for item in sprint_3b2_historical_native_contract_migration()
    }
    run_fields = {item.name for item in fields(HistoricalSpecialistRunResult)}
    result_fields = {item.name for item in fields(SpecialistResult)}
    request_fields = {item.name for item in fields(SpecialistRequest)}
    for name in (
        "master_request_id",
        "specialist_request_id",
        "correlation_id",
        "application_request_time_utc",
        "requested_evaluation_time_utc",
        "source_version",
        "query_id",
    ):
        assert name in gaps["HistoricalSpecialistRunResult"]
        assert name in gaps["SpecialistResult"]
        assert name not in run_fields
        assert name not in result_fields
    assert "start_utc" not in request_fields
    assert "end_utc" not in request_fields
    assert "HistoricalSpecialistRunResult does not carry host correlation fields." in (
        SPRINT_3B2_NATIVE_CONTRACT_MIGRATION_NOTES
    )
    assert "finalize_historical_specialist_run(" not in inspect.getsource(bridge_module)
    assert "HistoricalAnalyticsSpecialist(" not in inspect.getsource(bridge_module)


def test_native_invocation_uses_deterministic_fakes_without_authenticating():
    binding = _binding()
    window = (TOOL_START, TOOL_END)
    harness = DeterministicHistoricalNativeHarness(
        (
            _tool_call("summarize_historical_encounters", window),
            _claims(
                ExactCountClaim(
                    tool_call_id="historical-call-1",
                    metric_id="encounter_count",
                    value=2,
                )
            ),
        ),
        {"summarize_historical_encounters": _succeeded(AS_OF)},
    )
    envelope = harness.run(binding, trusted_context=_context_as_of())

    assert isinstance(envelope.native_run, HistoricalSpecialistRunResult)
    assert not isinstance(envelope.native_run, SpecialistResult)
    assert envelope.native_final_result is None
    assert envelope.native_observation_origin is NativeObservationOrigin.CALLER_SUPPLIED
    assert envelope.native_run.status is SpecialistRunStatus.PROPOSED_CLAIMS
    assert envelope.native_run.executed_tool_call_count == 1
    assert harness.provider.requests[0].user_text == TEXT
    assert PLAN_START not in harness.provider.requests[0].user_text
    assert PLAN_END not in harness.provider.requests[0].user_text
    assert harness.operations.calls[0]["as_of_utc"] == AS_OF
    assert envelope.host.specialist_request is binding.specialist_request
    assert envelope.host.specialist_request.request_id == binding.specialist_request_id
    assert envelope.master_request_id == binding.authorization.master_request_id
    assert envelope.invocation_id == binding.invocation_id
    assert envelope.correlation_id == CORRELATION
    assert envelope.application_request_time_utc == APPLICATION_TIME
    assert envelope.requested_evaluation_time_utc == REQUESTED_TIME
    assert envelope.actual_runtime_evaluation_time_utc == AS_OF
    assert envelope.unbound_plan_window_start_utc == PLAN_START
    assert envelope.unbound_plan_window_end_utc == PLAN_END
    assert envelope.eligible_tool_arguments is None
    assert envelope.tool_argument_source is HistoricalToolArgumentSource.NONE
    assert envelope.row_observation is HistoricalRowObservation.ROWS_PRESENT
    assert envelope.observed_query_ids == (INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,)
    assert "correlation_id" not in HistoricalSpecialistRunResult.__dataclass_fields__
    _assert_unauthenticated(envelope)


def test_multiple_historical_invocations_keep_distinct_identities():
    first, second = _bindings(
        _history("hist-1", start=PLAN_START, end=PLAN_END),
        _history("hist-2", start=SECOND_START, end=SECOND_END),
    )
    window = (TOOL_START, TOOL_END)

    def invoke(binding):
        harness = DeterministicHistoricalNativeHarness(
            (
                _tool_call("summarize_historical_encounters", window),
                _claims(
                    ExactCountClaim(
                        tool_call_id="historical-call-1",
                        metric_id="encounter_count",
                        value=2,
                    )
                ),
            ),
            {"summarize_historical_encounters": _succeeded(AS_OF)},
        )
        return harness.run(binding, trusted_context=_context_as_of())

    with ThreadPoolExecutor(max_workers=2) as pool:
        left, right = tuple(pool.map(invoke, (first, second)))
    collection = correlate_master_historical_native_collection(
        (
            MasterHistoricalNativeObservation(
                binding=first,
                native_run=left.native_run,
                observed_request=first.specialist_request,
                trusted_context=_context_as_of(),
            ),
            MasterHistoricalNativeObservation(
                binding=second,
                native_run=right.native_run,
                observed_request=second.specialist_request,
                trusted_context=_context_as_of(),
            ),
        )
    )

    assert left.invocation_id != right.invocation_id
    assert left.specialist_request_id != right.specialist_request_id
    assert left.master_request_id == right.master_request_id == MASTER
    assert left.correlation_id == right.correlation_id == CORRELATION
    assert left.unbound_plan_window_start_utc == PLAN_START
    assert right.unbound_plan_window_start_utc == SECOND_START
    assert left.native_run is not right.native_run
    assert [item.invocation_id for item in collection.envelopes] == [
        first.invocation_id,
        second.invocation_id,
    ]
    assert collection.envelopes[0].native_run is left.native_run
    assert collection.envelopes[1].native_run is right.native_run
    for envelope in collection.envelopes:
        _assert_unauthenticated(envelope)
    with pytest.raises(ContractValidationError, match="native_run_reused"):
        correlate_master_historical_native_collection(
            (
                MasterHistoricalNativeObservation(binding=first, native_run=left.native_run),
                MasterHistoricalNativeObservation(binding=second, native_run=left.native_run),
            )
        )


def test_unbound_plan_window_is_not_copied_until_an_independent_interval_exists():
    binding = _binding()
    unbound = prepare_master_historical_native_invocation(binding)
    interval = _interval()
    eligible = prepare_master_historical_native_invocation(
        binding,
        independent_tool_interval=interval,
    )
    plan_window = (PLAN_START, PLAN_END)
    echoed = DeterministicHistoricalNativeHarness(
        (_tool_call("summarize_historical_encounters", plan_window), _unsupported()),
        {"summarize_historical_encounters": _succeeded(AS_OF)},
    ).run(binding, trusted_context=_context_as_of())
    authorized = DeterministicHistoricalNativeHarness(
        (
            _tool_call("summarize_historical_encounters", (AUTHORIZED_START, AUTHORIZED_END)),
            _unsupported(),
        ),
        {"summarize_historical_encounters": _succeeded(AS_OF)},
    ).run(
        binding,
        trusted_context=_context_as_of(),
        independent_tool_interval=interval,
    )

    assert unbound.eligible_tool_arguments is None
    assert unbound.tool_argument_source is HistoricalToolArgumentSource.NONE
    assert unbound.structured_request_arguments is None
    assert unbound.specialist_request.text == TEXT
    assert dict(eligible.eligible_tool_arguments) == {
        "start_utc": AUTHORIZED_START,
        "end_utc": AUTHORIZED_END,
    }
    assert eligible.tool_argument_source is HistoricalToolArgumentSource.INDEPENDENT_INTERVAL
    assert eligible.unbound_plan_window_start_utc == PLAN_START
    assert echoed.unbound_plan_window_observed_in_executed_arguments is True
    assert echoed.eligible_tool_arguments is None
    assert echoed.bridge_copied_unbound_plan_window is False
    assert authorized.executed_window_matches_independent_interval is True
    assert authorized.unbound_plan_window_observed_in_executed_arguments is False
    assert authorized.eligible_tool_arguments == eligible.eligible_tool_arguments
    _assert_unauthenticated(echoed)
    _assert_unauthenticated(authorized)
    signature = inspect.signature(prepare_master_historical_native_invocation)
    assert "tool_arguments" not in signature.parameters
    assert "dispatch_permitted" not in signature.parameters
    with pytest.raises(TypeError):
        prepare_master_historical_native_invocation(binding, tool_arguments={"start_utc": PLAN_START})


def test_requested_time_stays_distinct_from_runtime_evaluation_time():
    binding = _binding()
    differing = DeterministicHistoricalNativeHarness(
        (_tool_call("summarize_historical_encounters", (TOOL_START, TOOL_END)), _unsupported()),
        {"summarize_historical_encounters": _succeeded(AS_OF)},
    ).run(binding, trusted_context=_context_as_of(AS_OF))
    same_instant = _binding(requested=AS_OF)
    matching = DeterministicHistoricalNativeHarness(
        (_tool_call("summarize_historical_encounters", (TOOL_START, TOOL_END)), _unsupported()),
        {"summarize_historical_encounters": _succeeded(AS_OF)},
    ).run(same_instant, trusted_context=_context_as_of(AS_OF))

    assert differing.requested_evaluation_time_utc == REQUESTED_TIME
    assert differing.actual_runtime_evaluation_time_utc == AS_OF
    assert differing.application_request_time_utc == APPLICATION_TIME
    assert MasterBindingDefect.TIME_MISMATCH in differing.binding_defects
    assert differing.requested_time_is_runtime_evaluation_time is False
    assert matching.requested_evaluation_time_utc == AS_OF
    assert matching.actual_runtime_evaluation_time_utc == AS_OF
    assert matching.requested_time_is_runtime_evaluation_time is False
    assert MasterBindingDefect.TIME_MISMATCH not in matching.binding_defects
    assert matching.application_clock_is_runtime_evaluation_time is False
    with pytest.raises(ContractValidationError, match="application_clock_not_evaluation_time"):
        prepare_master_historical_native_invocation(
            binding,
            trusted_context=HistoricalSpecialistTrustedContext(as_of_utc=APPLICATION_TIME),
        )


def test_wrong_native_request_id_and_wrong_correlation_are_not_execution():
    binding = _binding()
    harness = DeterministicHistoricalNativeHarness(
        (_tool_call("summarize_historical_encounters", (TOOL_START, TOOL_END)), _unsupported()),
        {"summarize_historical_encounters": _succeeded(AS_OF)},
    )
    produced = harness.run(binding, trusted_context=_context_as_of())
    wrong_request = correlate_master_historical_native_result(
        binding,
        native_run=produced.native_run,
        observed_request=SpecialistRequest(text=TEXT, request_id="other-specialist-request"),
    )
    tool_result = replace(produced.native_run.tool_results[0], correlation_id="corr-other-bridge")
    wrong_correlation = correlate_master_historical_native_result(
        binding,
        native_run=replace(produced.native_run, tool_results=(tool_result,)),
        observed_request=binding.specialist_request,
    )
    matched_tool = replace(produced.native_run.tool_results[0], correlation_id=CORRELATION)
    matched_correlation = correlate_master_historical_native_result(
        binding,
        native_run=replace(produced.native_run, tool_results=(matched_tool,)),
        observed_request=binding.specialist_request,
    )

    assert MasterBindingDefect.REQUEST_ID_MISMATCH in wrong_request.binding_defects
    assert wrong_request.native_specialist_request_id_resolved is False
    assert MasterBindingDefect.CORRELATION_MISMATCH in wrong_correlation.binding_defects
    assert wrong_correlation.native_correlation_resolved is False
    assert "correlation_id" not in HistoricalSpecialistRunResult.__dataclass_fields__
    assert matched_correlation.native_run.tool_results[0].correlation_id == CORRELATION
    assert MasterBindingDefect.CORRELATION_MISMATCH not in matched_correlation.binding_defects
    assert matched_correlation.native_correlation_resolved is False
    assert matched_correlation.tool_result_correlation_satisfies_result_contract is False
    assert MasterAdmissionReason.NATIVE_CORRELATION_ABSENT in matched_correlation.correlation_reasons
    assert MasterAdmissionReason.NATIVE_SPECIALIST_REQUEST_ID_ABSENT in (
        matched_correlation.correlation_reasons
    )
    for envelope in (wrong_request, wrong_correlation, matched_correlation):
        _assert_unauthenticated(envelope)


def test_missing_native_result_correlation_is_recorded_without_being_invented():
    binding = _binding()
    envelope = correlate_master_historical_native_result(binding)
    run_gap = next(
        item
        for item in envelope.migration_gaps
        if item.contract_name == "HistoricalSpecialistRunResult"
    )

    assert envelope.native_run is None
    assert envelope.native_observation_origin is NativeObservationOrigin.ABSENT
    assert envelope.actual_runtime_evaluation_time_utc is None
    assert envelope.native_correlation_resolved is False
    assert "correlation_id" in run_gap.missing_fields
    assert "master_request_id" in run_gap.missing_fields
    assert MasterAdmissionReason.MISSING_NATIVE_ARTIFACT in envelope.correlation_reasons
    assert MasterAdmissionReason.NATIVE_CORRELATION_ABSENT in envelope.correlation_reasons
    _assert_unauthenticated(envelope)


def test_final_result_stays_distinct_from_the_intermediate_run():
    binding = _binding()
    produced = DeterministicHistoricalNativeHarness(
        (
            _tool_call("summarize_historical_encounters", (TOOL_START, TOOL_END)),
            _claims(
                ExactCountClaim(
                    tool_call_id="historical-call-1",
                    metric_id="encounter_count",
                    value=2,
                )
            ),
        ),
        {"summarize_historical_encounters": _succeeded(AS_OF)},
    ).run(binding, trusted_context=_context_as_of())
    final_result = SpecialistResult(
        status=SpecialistStatus.UNSUPPORTED,
        answer="The historical request is outside this specialist.",
        temporal_scope=TemporalScope.HISTORICAL,
        evaluated_as_of_utc=None,
        tool_results=(),
        verified_claims=(),
        limitations=(),
        unsupported_reason=UnsupportedReason.GEOGRAPHY,
        verifier_outcome=VerifierOutcome.NOT_RUN,
    )
    both = correlate_master_historical_native_result(
        binding,
        native_run=produced.native_run,
        native_final_result=final_result,
        observed_request=binding.specialist_request,
    )

    assert isinstance(produced.native_run, HistoricalSpecialistRunResult)
    assert produced.native_final_result is None
    assert produced.finalization_performed is False
    assert both.native_run is produced.native_run
    assert both.native_final_result is final_result
    assert type(both.native_run) is not type(both.native_final_result)
    assert both.intermediate_run_type == "HistoricalSpecialistRunResult"
    assert both.final_result_type == "SpecialistResult"
    assert both.finalization_performed is False
    assert both.evidence_issued is False
    assert both.readiness.finalization_complete is False
    _assert_unauthenticated(both)
    with pytest.raises(ContractValidationError, match="historical_run_not_final_artifact"):
        correlate_master_historical_native_result(binding, native_run=final_result)
    with pytest.raises(ContractValidationError, match="historical_run_not_final_artifact"):
        correlate_master_historical_native_result(
            binding,
            native_final_result=produced.native_run,
        )


def test_partial_query_is_not_exact_and_not_a_verified_zero():
    binding = _binding()
    envelope = DeterministicHistoricalNativeHarness(
        (
            _tool_call(
                "list_historical_encounters",
                (TOOL_START, TOOL_END),
                aircraft_id="abc123",
                limit=1,
            ),
            _unsupported(),
        ),
        {"list_historical_encounters": _truncated_list(AS_OF)},
    ).run(binding, trusted_context=_context_as_of())

    assert envelope.native_run.tool_results[0].status is ToolResultStatus.PARTIAL
    assert RESULT_TRUNCATED_LIMITATION in envelope.native_run.tool_results[0].limitations
    assert envelope.query_observation is HistoricalQueryObservation.PARTIAL_OR_TRUNCATED
    assert envelope.row_observation is not HistoricalRowObservation.VERIFIED_ZERO_SHAPE
    assert envelope.verified_zero_admitted is False
    assert envelope.incomplete_coverage_interpreted_as_zero is False
    assert envelope.native_final_result is None
    _assert_unauthenticated(envelope)


def test_no_rows_is_not_a_verified_zero():
    binding = _binding()
    no_rows = DeterministicHistoricalNativeHarness(
        (_tool_call("summarize_historical_encounters", (TOOL_START, TOOL_END)), _unsupported()),
        {"summarize_historical_encounters": _succeeded(AS_OF, count=0, rows_returned=0)},
    ).run(binding, trusted_context=_context_as_of())
    verified_shape = DeterministicHistoricalNativeHarness(
        (
            _tool_call("summarize_historical_encounters", (TOOL_START, TOOL_END)),
            _claims(VerifiedZeroClaim(tool_call_id="historical-call-1")),
        ),
        {"summarize_historical_encounters": _verified_zero(AS_OF)},
    ).run(binding, trusted_context=_context_as_of())

    assert no_rows.native_run.tool_results[0].status is ToolResultStatus.SUCCESS
    assert no_rows.row_observation is HistoricalRowObservation.NO_ROWS
    assert no_rows.verified_zero_claim_proposed is False
    assert no_rows.verified_zero_admitted is False
    assert verified_shape.native_run.tool_results[0].status is ToolResultStatus.NOT_FOUND
    assert verified_shape.row_observation is HistoricalRowObservation.VERIFIED_ZERO_SHAPE
    assert verified_shape.verified_zero_claim_proposed is True
    assert verified_shape.verified_zero_admitted is False
    assert verified_shape.native_final_result is None
    assert verified_shape.query_observation is HistoricalQueryObservation.OBSERVED_NOT_PARTIAL
    _assert_unauthenticated(no_rows)
    _assert_unauthenticated(verified_shape)


def test_source_version_mismatch_is_not_authenticated():
    binding = _binding()
    produced = DeterministicHistoricalNativeHarness(
        (
            _tool_call(
                "list_historical_encounters",
                (TOOL_START, TOOL_END),
                aircraft_id="abc123",
                limit=2,
            ),
            _unsupported(),
        ),
        {"list_historical_encounters": _listed(AS_OF)},
    ).run(binding, trusted_context=_context_as_of(), expected_source_version="source-v1")
    evidence = produced.native_run.tool_results[0].evidence[0]
    conflicting = replace(
        evidence,
        source_records=(
            replace(evidence.source_records[0], source_version="source-v1"),
            replace(evidence.source_records[1], source_version="source-v2"),
        ),
    )
    conflicted_run = replace(
        produced.native_run,
        tool_results=(replace(produced.native_run.tool_results[0], evidence=(conflicting,)),),
    )
    conflicted = correlate_master_historical_native_result(
        binding,
        native_run=conflicted_run,
        observed_request=binding.specialist_request,
    )

    assert produced.observed_source_versions == (None, None)
    assert produced.source_version_mismatch is True
    assert produced.source_version_authenticated is False
    assert produced.authenticated_source_version is None
    assert conflicted.observed_source_versions == ("source-v1", "source-v2")
    assert conflicted.source_version_mismatch is True
    assert conflicted.observed_query_ids == (INTERNAL_QUERY_ID_LIST_ENCOUNTERS,)
    assert conflicted.authenticated_query_id is None
    assert "source_version" not in HistoricalSpecialistRunResult.__dataclass_fields__
    _assert_unauthenticated(produced)
    _assert_unauthenticated(conflicted)


def test_unsupported_historical_tool_arguments_do_not_execute_or_authenticate():
    binding = _binding()
    harness = DeterministicHistoricalNativeHarness(
        (
            _tool_call(
                "summarize_historical_encounters",
                (TOOL_START, TOOL_END),
                as_of_utc=AS_OF,
            ),
            _tool_call(
                "summarize_historical_encounters",
                (TOOL_START, TOOL_END),
                as_of_utc=AS_OF,
            ),
        ),
        {"summarize_historical_encounters": _succeeded(AS_OF)},
    )
    rejected = harness.run(binding, trusted_context=_context_as_of())
    invocation = ExecutedToolInvocation(
        tool_name="summarize_historical_encounters",
        tool_call_id="historical-call-1",
        requested_arguments={"start_utc": TOOL_START, "end_utc": TOOL_END, "sql": "select 1"},
        executed_arguments={"start_utc": TOOL_START, "end_utc": TOOL_END, "sql": "select 1"},
        list_limit_default_applied=False,
        canonical_key="bridge-unsupported",
    )
    supplied = correlate_master_historical_native_result(
        binding,
        native_run=replace(
            rejected.native_run,
            executed_invocations=(invocation,),
            executed_tool_call_count=1,
        ),
        observed_request=binding.specialist_request,
    )

    assert rejected.native_run.status is SpecialistRunStatus.INVALID_REQUEST
    assert rejected.native_run.executed_tool_call_count == 0
    assert rejected.unsupported_argument_names == ("as_of_utc",)
    assert harness.operations.calls == []
    assert "sql" in supplied.unsupported_argument_names
    assert "as_of_utc" in supplied.unsupported_argument_names
    assert supplied.evidence_issued is False
    assert supplied.authenticated_execution is False
    _assert_unauthenticated(rejected)
    _assert_unauthenticated(supplied)


def test_production_bridge_rejects_execution_controls():
    signature = inspect.signature(correlate_master_historical_native_result)
    assert "operations" not in signature.parameters
    assert "provider" not in signature.parameters
    assert "authenticated_execution" not in signature.parameters
    assert "dispatch_permitted" not in signature.parameters
    binding = _binding()
    with pytest.raises(TypeError):
        correlate_master_historical_native_result(binding, authenticated_execution=True)
    with pytest.raises(TypeError):
        correlate_master_historical_native_result(binding, operations=object())
