"""Phase 3A.2 bounded Historical Analytics Specialist tests."""

from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path

import pytest

from tests.fakes.recording_historical_operations import RecordingHistoricalOperations
from tests.fakes.scripted_model_provider import ScriptedModelProvider
from wilvor_ai.historical_analytics import (
    HISTORICAL_ANALYTICS_TOOLS,
    historical_analytics_tool_schemas,
)
from wilvor_ai.historical_specialist import (
    AI_LIST_DEFAULT,
    AI_LIST_MAX,
    HISTORICAL_SPECIALIST_INSTRUCTION_REF,
    HISTORICAL_SPECIALIST_INSTRUCTION_RULES,
    HistoricalAnalyticsSpecialist,
    MAX_MODEL_TURNS,
    MAX_TOOL_CALLS,
    SequentialToolCallIdFactory,
    canonical_tool_call_key,
)
from wilvor_ai.model_contracts import (
    ModelDecision,
    ModelDecisionKind,
    ProposedToolCall,
    ValidationFeedbackCode,
)
from wilvor_ai.specialist_contracts import (
    ExactCountClaim,
    HistoricalSpecialistTrustedContext,
    SpecialistRequest,
    SpecialistResult,
    UnsupportedReason,
    VerifierOutcome,
)
from wilvor_ai.specialist_runtime_contracts import (
    HistoricalSpecialistRunResult,
    SpecialistRunStatus,
    SpecialistRuntimeErrorCode,
)
from wilvor_historical.coverage_contracts import Evaluability
from wilvor_historical.query_contracts import (
    INTERNAL_QUERY_ID_LIST_ENCOUNTERS,
    INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,
    INTERNAL_QUERY_ID_SUMMARIZE_RISKS,
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
    RiskSummaryResult,
    SummarizeHistoricalEncountersRequest,
    SummarizeHistoricalRisksRequest,
)


AS_OF = "2026-09-13T12:00:00Z"
OTHER_AS_OF = "2026-09-14T00:00:00Z"
WINDOW = ("2026-09-11T10:00:00Z", "2026-09-11T11:00:00Z")
SPECIALIST_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "functions"
    / "shared"
    / "wilvor_ai"
    / "historical_specialist.py"
)


def _coverage():
    return CoverageEvidence(
        evaluability=Evaluability.EVALUABLE,
        reason="window_evaluable",
        required_horizon_seconds=173760,
        required_streams=("facts",),
        collection_epoch_ids=("epoch-1",),
    )


def _execution(query_id: str) -> QueryExecutionEvidence:
    return QueryExecutionEvidence(
        query_id=query_id,
        query_execution_id="exec-specialist-1",
        rows_returned=2,
        data_scanned_bytes=64,
        workgroup="historical-analytics",
    )


def _query_evidence(
    operation: HistoricalOperation,
    *,
    as_of: str = AS_OF,
    count: int = 2,
    exact: bool = True,
    query_id: str = INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,
    datasets: tuple[str, ...] = ("encounter",),
) -> QueryEvidence:
    return QueryEvidence(
        query_name=operation,
        datasets=datasets,
        requested_start_utc=WINDOW[0],
        requested_end_utc=WINDOW[1],
        coverage_state=Evaluability.EVALUABLE,
        collection_epoch_ids=("epoch-1",),
        semantic_match_count=count,
        semantic_match_count_is_exact=exact,
        query_executions=(_execution(query_id),),
        evaluated_as_of_utc=as_of,
    )


def _encounter_summary() -> EncounterSummaryResult:
    return EncounterSummaryResult(
        physical_record_count=2,
        distinct_encounter_count=2,
        distinct_aircraft_count=1,
        distinct_hazard_count=1,
        distinct_dedup_count=2,
        min_event_time_utc="2026-09-11T10:00:00Z",
        max_event_time_utc="2026-09-11T10:30:00Z",
    )


def _record(index: int) -> HistoricalEncounterRecord:
    suffix = str(index)
    return HistoricalEncounterRecord(
        encounter_id=f"spec-{suffix}#hazard-1#v1",
        record_id=f"spec-{suffix}#hazard-1#v1",
        dedup_id=f"spec-{suffix}#hazard-1#v1|ENCOUNTER_OBSERVED|1700000000|DETECTED",
        aircraft_id="abc123",
        hazard_id="hazard-1",
        hazard_version_key="hazard-1#v1",
        fact_kind="ENCOUNTER_OBSERVED",
        encounter_state="DETECTED",
        event_time_utc=f"2026-09-11T10:{index:02d}:00Z",
        hazard_type="CONVECTION",
    )


def _succeeded_encounters(*, as_of: str = AS_OF) -> HistoricalQueryResponse:
    return HistoricalQueryResponse(
        status=HistoricalQueryStatus.SUCCEEDED,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        requested_scope=SummarizeHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
        ),
        coverage=_coverage(),
        evidence=_query_evidence(
            HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            as_of=as_of,
        ),
        result=_encounter_summary(),
    )


def _succeeded_risks(*, as_of: str = AS_OF) -> HistoricalQueryResponse:
    return HistoricalQueryResponse(
        status=HistoricalQueryStatus.SUCCEEDED,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS,
        requested_scope=SummarizeHistoricalRisksRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
        ),
        coverage=_coverage(),
        evidence=_query_evidence(
            HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS,
            as_of=as_of,
            query_id=INTERNAL_QUERY_ID_SUMMARIZE_RISKS,
            datasets=("risk",),
        ),
        result=RiskSummaryResult(
            physical_record_count=2,
            distinct_risk_count=2,
            distinct_encounter_count=2,
            distinct_aircraft_count=1,
            min_risk_score=1,
            max_risk_score=4,
        ),
    )


def _succeeded_list(count: int, *, as_of: str = AS_OF) -> HistoricalQueryResponse:
    records = tuple(_record(index + 1) for index in range(count))
    return HistoricalQueryResponse(
        status=HistoricalQueryStatus.SUCCEEDED,
        operation=HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
        requested_scope=ListHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            aircraft_id="abc123",
            limit=count or 1,
        ),
        coverage=_coverage(),
        evidence=_query_evidence(
            HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
            as_of=as_of,
            count=count,
            query_id=INTERNAL_QUERY_ID_LIST_ENCOUNTERS,
        ),
        result=ListEncountersResult(records=records),
    )


def _tool_calls(*calls: ProposedToolCall) -> ModelDecision:
    return ModelDecision(kind=ModelDecisionKind.TOOL_CALLS, tool_calls=calls)


def _summarize(**arguments) -> ProposedToolCall:
    payload = {"start_utc": WINDOW[0], "end_utc": WINDOW[1], **arguments}
    return ProposedToolCall(
        name="summarize_historical_encounters",
        arguments=payload,
    )


def _risks(**arguments) -> ProposedToolCall:
    payload = {"start_utc": WINDOW[0], "end_utc": WINDOW[1], **arguments}
    return ProposedToolCall(
        name="summarize_historical_risks",
        arguments=payload,
    )


def _list(**arguments) -> ProposedToolCall:
    payload = {"start_utc": WINDOW[0], "end_utc": WINDOW[1], **arguments}
    return ProposedToolCall(name="list_historical_encounters", arguments=payload)


def _final(tool_call_id: str = "historical-call-1") -> ModelDecision:
    return ModelDecision(
        kind=ModelDecisionKind.FINAL_CLAIMS,
        claims=(
            ExactCountClaim(
                tool_call_id=tool_call_id,
                metric_id="encounter_count",
                value=2,
            ),
        ),
    )


def _unsupported() -> ModelDecision:
    return ModelDecision(
        kind=ModelDecisionKind.UNSUPPORTED,
        unsupported_reason=UnsupportedReason.GEOGRAPHY,
    )


def _refusal() -> ModelDecision:
    return ModelDecision(kind=ModelDecisionKind.REFUSAL, refusal_code="MODEL_REFUSED")


def _run(
    script,
    responses=None,
    *,
    as_of: str = AS_OF,
    factory=None,
):
    provider = ScriptedModelProvider(script)
    operations = RecordingHistoricalOperations(responses or {})
    specialist = HistoricalAnalyticsSpecialist(
        provider=provider,
        operations=operations,
        tool_call_id_factory=factory or SequentialToolCallIdFactory(),
    )
    result = specialist.run(
        SpecialistRequest(text="summarize historical encounters"),
        HistoricalSpecialistTrustedContext(as_of_utc=as_of),
    )
    return result, provider, operations


def test_baseline_catalog_and_schemas_remain_exactly_four():
    assert [item.name for item in HISTORICAL_ANALYTICS_TOOLS] == [
        "summarize_historical_encounters",
        "summarize_historical_risks",
        "summarize_historical_hazard_versions",
        "list_historical_encounters",
    ]
    assert [item.name for item in historical_analytics_tool_schemas()] == [
        item.name for item in HISTORICAL_ANALYTICS_TOOLS
    ]
    assert "answer" not in {item.name for item in fields(HistoricalSpecialistRunResult)}
    assert "verified_claims" not in {
        item.name for item in fields(HistoricalSpecialistRunResult)
    }


def test_happy_path_one_tool_then_unverified_claims():
    result, provider, operations = _run(
        [_tool_calls(_summarize()), _final()],
        {"summarize_historical_encounters": _succeeded_encounters()},
    )
    assert result.status is SpecialistRunStatus.PROPOSED_CLAIMS
    assert result.terminal_kind is ModelDecisionKind.FINAL_CLAIMS
    assert len(result.tool_results) == 1
    assert len(result.tool_result_projections) == 1
    assert result.executed_tool_call_count == 1
    assert result.provider_turn_count == 2
    assert result.executed_invocations[0].tool_call_id == "historical-call-1"
    assert operations.calls[0]["as_of_utc"] == AS_OF
    assert result.evaluated_as_of_utc == AS_OF
    assert result.proposed_claims[0].tool_call_id == "historical-call-1"
    assert result.tool_results[0].evidence[0].query_executions
    assert "query_executions" not in result.tool_result_projections[0].to_dict()
    assert provider.requests[0].tool_results == ()
    assert provider.requests[0].instruction_ref == HISTORICAL_SPECIALIST_INSTRUCTION_REF
    assert [item.name for item in provider.requests[0].tools] == [
        item.name for item in HISTORICAL_ANALYTICS_TOOLS
    ]
    assert provider.requests[1].tool_results[0].tool_call_id == "historical-call-1"
    source = SPECIALIST_SOURCE.read_text(encoding="utf-8")
    assert "SpecialistResult(" not in source
    assert "verified_claims" not in source
    assert "ANSWERED" not in source


def test_two_tools_use_unique_ids_and_same_trusted_as_of():
    result, provider, operations = _run(
        [_tool_calls(_summarize(), _risks()), _final("historical-call-1")],
        {
            "summarize_historical_encounters": _succeeded_encounters(),
            "summarize_historical_risks": _succeeded_risks(),
        },
    )
    assert result.executed_tool_call_count == 2
    assert [item.tool_call_id for item in result.executed_invocations] == [
        "historical-call-1",
        "historical-call-2",
    ]
    assert [item["as_of_utc"] for item in operations.calls] == [AS_OF, AS_OF]
    assert [item["method"] for item in operations.calls] == [
        "summarize_historical_encounters",
        "summarize_historical_risks",
    ]
    assert result.evaluated_as_of_utc == AS_OF
    assert len(provider.requests[1].tool_results) == 2


def test_trusted_argument_attack_is_rejected_before_adapter():
    result, provider, operations = _run(
        [
            _tool_calls(_summarize(as_of_utc="2099-01-01T00:00:00Z")),
            _final(),
        ],
        {"summarize_historical_encounters": _succeeded_encounters()},
    )
    assert operations.calls == []
    assert result.executed_tool_call_count == 0
    assert result.validation_feedback[0].code is (
        ValidationFeedbackCode.TRUSTED_ARGUMENT_FORBIDDEN
    )
    assert result.validation_feedback[0].argument_name == "as_of_utc"
    assert provider.requests[1].validation_feedback is not None
    assert result.status is SpecialistRunStatus.PROPOSED_CLAIMS

    for name, argument in (
        ("tool_call_id", "forged-id"),
        ("operations", "stolen"),
    ):
        blocked, _, ops = _run(
            [_tool_calls(_summarize(**{name: argument})), _unsupported()],
        )
        assert ops.calls == []
        assert blocked.validation_feedback[0].code is (
            ValidationFeedbackCode.TRUSTED_ARGUMENT_FORBIDDEN
        )


def test_unknown_tool_is_rejected_without_fuzzy_match():
    for name in ("run_sql", "summarize_historical_risks_by_level"):
        result, _, operations = _run(
            [
                _tool_calls(ProposedToolCall(name=name, arguments={})),
                _unsupported(),
            ]
        )
        assert operations.calls == []
        assert result.validation_feedback[0].code is ValidationFeedbackCode.UNKNOWN_TOOL
        assert result.validation_feedback[0].tool_name == name


def test_required_and_type_errors_are_rejected_before_adapter():
    cases = [
        (
            ProposedToolCall(
                name="summarize_historical_encounters",
                arguments={"end_utc": WINDOW[1]},
            ),
            ValidationFeedbackCode.MISSING_REQUIRED_ARGUMENT,
            "start_utc",
        ),
        (_list(aircraft_id="abc123", limit="20"), ValidationFeedbackCode.INVALID_ARGUMENT_TYPE, "limit"),
        (_list(aircraft_id="abc123", limit=True), ValidationFeedbackCode.INVALID_ARGUMENT_TYPE, "limit"),
        (_summarize(unexpected="x"), ValidationFeedbackCode.UNKNOWN_ARGUMENT, "unexpected"),
    ]
    for call, code, argument in cases:
        result, _, operations = _run([_tool_calls(call), _unsupported()])
        assert operations.calls == []
        assert result.validation_feedback[0].code is code
        assert result.validation_feedback[0].argument_name == argument


def test_list_policy_default_bounds_and_reject_without_clamp():
    omitted, _, ops = _run(
        [_tool_calls(_list(aircraft_id="abc123")), _final()],
        {"list_historical_encounters": _succeeded_list(2)},
    )
    assert ops.calls[0]["request"].limit == AI_LIST_DEFAULT
    assert omitted.executed_invocations[0].list_limit_default_applied is True
    assert omitted.executed_invocations[0].executed_arguments["limit"] == 20
    assert "limit" not in omitted.executed_invocations[0].requested_arguments

    for limit in (1, AI_LIST_MAX):
        result, _, ops = _run(
            [_tool_calls(_list(aircraft_id="abc123", limit=limit)), _final()],
            {"list_historical_encounters": _succeeded_list(1)},
        )
        assert ops.calls[0]["request"].limit == limit
        assert result.executed_invocations[0].list_limit_default_applied is False

    for limit in (26, 100):
        result, _, ops = _run(
            [_tool_calls(_list(aircraft_id="abc123", limit=limit)), _unsupported()],
        )
        assert ops.calls == []
        assert result.validation_feedback[0].code is (
            ValidationFeedbackCode.LIST_LIMIT_EXCEEDED
        )

    omitted_key = canonical_tool_call_key(
        "list_historical_encounters",
        {
            "start_utc": WINDOW[0],
            "end_utc": WINDOW[1],
            "aircraft_id": "abc123",
            "limit": 20,
        },
    )
    explicit_key = canonical_tool_call_key(
        "list_historical_encounters",
        {
            "start_utc": WINDOW[0],
            "end_utc": WINDOW[1],
            "aircraft_id": "abc123",
            "limit": 20,
        },
    )
    assert omitted_key == explicit_key
    assert omitted.executed_invocations[0].canonical_key == omitted_key


def test_batch_atomicity_rejects_entire_malformed_or_over_budget_decision():
    result, _, operations = _run(
        [
            _tool_calls(_summarize(), _summarize(as_of_utc="2099-01-01T00:00:00Z")),
            _unsupported(),
        ],
        {"summarize_historical_encounters": _succeeded_encounters()},
    )
    assert operations.calls == []
    assert result.validation_feedback[0].code is (
        ValidationFeedbackCode.TRUSTED_ARGUMENT_FORBIDDEN
    )

    over_budget, _, operations = _run(
        [
            _tool_calls(_summarize(), _risks(), _list(aircraft_id="abc123")),
            _unsupported(),
        ],
    )
    assert operations.calls == []
    assert over_budget.validation_feedback[0].code is (
        ValidationFeedbackCode.TOOL_CALL_BUDGET_EXCEEDED
    )


def test_duplicate_calls_are_rejected_and_distinct_args_may_execute():
    same_batch, _, operations = _run(
        [_tool_calls(_summarize(), _summarize()), _unsupported()],
        {"summarize_historical_encounters": _succeeded_encounters()},
    )
    assert operations.calls == []
    assert same_batch.validation_feedback[0].code is (
        ValidationFeedbackCode.DUPLICATE_TOOL_CALL
    )

    later, provider, operations = _run(
        [_tool_calls(_summarize()), _tool_calls(_summarize()), _unsupported()],
        {"summarize_historical_encounters": _succeeded_encounters()},
    )
    assert len(operations.calls) == 1
    assert later.validation_feedback[0].code is ValidationFeedbackCode.DUPLICATE_TOOL_CALL
    assert later.executed_tool_call_count == 1
    assert provider.requests[1].validation_feedback is None
    assert provider.requests[2].validation_feedback is not None

    omitted_then_explicit, _, operations = _run(
        [
            _tool_calls(_list(aircraft_id="abc123")),
            _tool_calls(_list(aircraft_id="abc123", limit=20)),
            _unsupported(),
        ],
        {"list_historical_encounters": _succeeded_list(1)},
    )
    assert len(operations.calls) == 1
    assert omitted_then_explicit.validation_feedback[0].code is (
        ValidationFeedbackCode.DUPLICATE_TOOL_CALL
    )

    distinct, _, operations = _run(
        [
            _tool_calls(
                _summarize(),
                _summarize(aircraft_id="abc123"),
            ),
            _final(),
        ],
        {
            "summarize_historical_encounters": [
                _succeeded_encounters(),
                _succeeded_encounters(),
            ]
        },
    )
    assert len(operations.calls) == 2
    assert distinct.executed_tool_call_count == 2


def test_correction_budget_allows_one_then_stops_invalid_request():
    result, provider, operations = _run(
        [
            _tool_calls(ProposedToolCall(name="run_sql", arguments={})),
            _tool_calls(_summarize(unexpected="x")),
            _final(),
        ]
    )
    assert operations.calls == []
    assert result.status is SpecialistRunStatus.INVALID_REQUEST
    assert result.provider_turn_count == 2
    assert len(result.validation_feedback) == 2
    assert len(provider.requests) == 2
    assert provider.requests[1].validation_feedback is not None


def test_domain_invalid_request_is_a_real_tool_result_not_dispatcher_correction():
    result, provider, operations = _run(
        [_tool_calls(_list()), _final("historical-call-1")],
    )
    assert operations.calls == []
    assert result.executed_tool_call_count == 1
    assert result.tool_results[0].as_of_utc is None
    assert result.tool_results[0].data["error"]["code"] == "INVALID_REQUEST"
    assert result.evaluated_as_of_utc is None
    assert result.validation_feedback == ()
    assert provider.requests[1].tool_results[0].error_code is None
    assert result.status is SpecialistRunStatus.PROPOSED_CLAIMS


def test_projection_failure_stops_unavailable_without_model_feedback():
    result, provider, operations = _run(
        [_tool_calls(_list(aircraft_id="abc123", limit=25)), _final()],
        {"list_historical_encounters": _succeeded_list(26)},
    )
    assert len(operations.calls) == 1
    assert result.status is SpecialistRunStatus.UNAVAILABLE
    assert result.runtime_errors == (
        SpecialistRuntimeErrorCode.PROJECTION_INTEGRITY_FAILED.value,
    )
    assert len(result.tool_results) == 1
    assert result.tool_result_projections == ()
    assert result.proposed_claims == ()
    assert len(provider.requests) == 1
    assert provider.requests[0].validation_feedback is None
    serialized = str(result.to_dict())
    assert "projection_record_limit_exceeded" not in serialized
    assert "ToolResultProjectionError" not in serialized


def test_as_of_integrity_rules():
    none_tools, _, _ = _run([_unsupported()])
    assert none_tools.evaluated_as_of_utc is None

    invalid_only, _, _ = _run([_tool_calls(_list()), _unsupported()])
    assert invalid_only.evaluated_as_of_utc is None
    assert invalid_only.tool_results[0].as_of_utc is None

    matching, _, _ = _run(
        [_tool_calls(_summarize()), _unsupported()],
        {"summarize_historical_encounters": _succeeded_encounters()},
    )
    assert matching.evaluated_as_of_utc == AS_OF

    two_match, _, _ = _run(
        [_tool_calls(_summarize(), _risks()), _unsupported()],
        {
            "summarize_historical_encounters": _succeeded_encounters(),
            "summarize_historical_risks": _succeeded_risks(),
        },
    )
    assert two_match.evaluated_as_of_utc == AS_OF

    mismatch, provider, _ = _run(
        [_tool_calls(_summarize()), _final()],
        {"summarize_historical_encounters": _succeeded_encounters(as_of=OTHER_AS_OF)},
    )
    assert mismatch.status is SpecialistRunStatus.UNAVAILABLE
    assert mismatch.runtime_errors == (
        SpecialistRuntimeErrorCode.AS_OF_MISMATCH.value,
    )
    assert mismatch.evaluated_as_of_utc is None
    assert len(provider.requests) == 1

    disagree, _, _ = _run(
        [_tool_calls(_summarize(), _risks()), _final()],
        {
            "summarize_historical_encounters": _succeeded_encounters(),
            "summarize_historical_risks": _succeeded_risks(as_of=OTHER_AS_OF),
        },
    )
    assert disagree.status is SpecialistRunStatus.UNAVAILABLE
    assert disagree.runtime_errors == (
        SpecialistRuntimeErrorCode.AS_OF_MISMATCH.value,
    )
    assert len(disagree.tool_results) == 2


def test_unsupported_and_refusal_terminals():
    first_unsupported, _, operations = _run([_unsupported()])
    assert first_unsupported.status is SpecialistRunStatus.UNSUPPORTED
    assert first_unsupported.unsupported_reason is UnsupportedReason.GEOGRAPHY
    assert first_unsupported.evaluated_as_of_utc is None
    assert operations.calls == []

    first_refusal, _, operations = _run([_refusal()])
    assert first_refusal.status is SpecialistRunStatus.PROVIDER_FAILED
    assert first_refusal.runtime_errors == (SpecialistRuntimeErrorCode.REFUSAL.value,)
    assert first_refusal.unsupported_reason is None
    assert first_refusal.refusal_code == "MODEL_REFUSED"
    assert operations.calls == []

    after_tool = _run(
        [_tool_calls(_summarize()), _unsupported()],
        {"summarize_historical_encounters": _succeeded_encounters()},
    )[0]
    assert after_tool.status is SpecialistRunStatus.UNSUPPORTED
    assert after_tool.evaluated_as_of_utc == AS_OF
    assert len(after_tool.tool_results) == 1

    after_refusal = _run(
        [_tool_calls(_summarize()), _refusal()],
        {"summarize_historical_encounters": _succeeded_encounters()},
    )[0]
    assert after_refusal.status is SpecialistRunStatus.PROVIDER_FAILED
    assert after_refusal.evaluated_as_of_utc == AS_OF
    assert len(after_refusal.tool_results) == 1


def test_provider_exception_is_contained():
    first, _, operations = _run([RuntimeError("secret token")])
    assert first.status is SpecialistRunStatus.PROVIDER_FAILED
    assert first.runtime_errors == (
        SpecialistRuntimeErrorCode.PROVIDER_EXCEPTION.value,
    )
    assert "secret token" not in str(first.to_dict())
    assert operations.calls == []
    assert first.proposed_claims == ()

    later, _, operations = _run(
        [_tool_calls(_summarize()), RuntimeError("secret token")],
        {"summarize_historical_encounters": _succeeded_encounters()},
    )
    assert later.status is SpecialistRunStatus.PROVIDER_FAILED
    assert len(later.tool_results) == 1
    assert later.evaluated_as_of_utc == AS_OF
    assert "secret token" not in str(later.to_dict())


def test_model_turn_limit_and_tool_call_budget():
    result, provider, operations = _run(
        [
            _tool_calls(_summarize()),
            _tool_calls(_summarize(aircraft_id="abc123")),
            _tool_calls(_summarize(hazard_id="hazard-1")),
            _final(),
        ],
        {
            "summarize_historical_encounters": [
                _succeeded_encounters(),
                _succeeded_encounters(),
            ]
        },
    )
    assert result.status is SpecialistRunStatus.PROVIDER_FAILED
    assert result.runtime_errors == (
        SpecialistRuntimeErrorCode.MODEL_TURN_LIMIT_EXCEEDED.value,
    )
    assert len(provider.requests) <= MAX_MODEL_TURNS
    assert result.provider_turn_count == MAX_MODEL_TURNS
    assert result.executed_tool_call_count <= MAX_TOOL_CALLS
    assert result.proposed_claims == ()


def test_zero_tool_final_claims_remain_unverified_and_not_answered():
    result, _, _ = _run([_final("historical-call-unexecuted")])
    assert result.status is SpecialistRunStatus.PROPOSED_CLAIMS
    assert result.tool_results == ()
    assert result.evaluated_as_of_utc is None
    assert result.proposed_claims
    names = {item.name for item in fields(HistoricalSpecialistRunResult)}
    assert "answer" not in names
    assert SpecialistResult is not type(result)
    assert result.status is not VerifierOutcome.PASSED


def test_run_result_round_trip_and_instruction_lock():
    result, _, _ = _run(
        [_tool_calls(_summarize()), _final()],
        {"summarize_historical_encounters": _succeeded_encounters()},
    )
    restored = HistoricalSpecialistRunResult.from_dict(result.to_dict())
    assert restored == result
    assert HISTORICAL_SPECIALIST_INSTRUCTION_REF == "wilvor.historical.specialist.v2"
    assert "return_typed_decision_only" in HISTORICAL_SPECIALIST_INSTRUCTION_RULES
    source = SPECIALIST_SOURCE.read_text(encoding="utf-8")
    assert "datetime.now" not in source
    assert "boto3" not in source
    assert "LIVE_OPS_TOOLS" not in source
    assert "claim_text" not in source
    assert "There were" not in source
