"""Phase 3A.1 model-visible ToolResult projection tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from wilvor_ai import (
    ConfidenceLevel,
    Evidence,
    FreshnessStatus,
    MatchCardinality,
    QueryExecutionTrace,
    SourceCompleteness,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.historical_analytics_mapping import (
    HISTORICAL_FRESHNESS_NOT_ESTABLISHED,
    MAPPING_INTEGRITY_FAILED,
    RESULT_TRUNCATED_LIMITATION,
    map_historical_invalid_request,
    map_historical_query_response,
)
from wilvor_ai.model_contracts import ModelTurnRequest
from wilvor_ai.tool_result_projection import (
    AUDIT_ONLY_TRACE_KEYS,
    MODEL_VISIBLE_RECORD_MAX,
    ToolResultProjectionError,
    project_tool_result,
)
from wilvor_historical.coverage_contracts import Evaluability
from wilvor_historical.query_contracts import (
    COVERAGE_REASON_EPOCH_AMBIGUOUS,
    CoverageEvidence,
    EncounterSummaryResult,
    HistoricalEncounterRecord,
    HistoricalOperation,
    HistoricalQueryErrorEvidence,
    HistoricalQueryResponse,
    HistoricalQueryStatus,
    ListEncountersResult,
    ListHistoricalEncountersRequest,
    QueryEvidence,
    QueryExecutionEvidence,
    SummarizeHistoricalEncountersRequest,
    INTERNAL_QUERY_ID_LIST_ENCOUNTERS,
    INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,
)
from wilvor_ai.historical_analytics import historical_analytics_tool_schemas


AS_OF = "2026-09-13T12:00:00Z"
WINDOW = ("2026-09-11T10:00:00Z", "2026-09-11T11:00:00Z")
TOOL_CALL_ID = "tool-call-projection-001"


def _coverage(
    evaluability: Evaluability | None,
    reason: str = "window_evaluable",
) -> CoverageEvidence:
    return CoverageEvidence(
        evaluability=evaluability,
        reason=reason,
        required_horizon_seconds=173760,
        required_streams=("facts",),
        collection_epoch_ids=("epoch-1",),
    )


def _query_evidence(
    *,
    coverage_state: Evaluability | None,
    semantic_match_count: int | None = None,
    semantic_match_count_is_exact: bool | None = None,
    minimum_match_count: int | None = None,
    executions: tuple[QueryExecutionEvidence, ...] = (),
    operation: HistoricalOperation = (
        HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS
    ),
) -> QueryEvidence:
    return QueryEvidence(
        query_name=operation,
        datasets=("encounter",),
        requested_start_utc=WINDOW[0],
        requested_end_utc=WINDOW[1],
        coverage_state=coverage_state,
        collection_epoch_ids=("epoch-1",),
        semantic_match_count=semantic_match_count,
        semantic_match_count_is_exact=semantic_match_count_is_exact,
        minimum_match_count=minimum_match_count,
        query_executions=executions,
        evaluated_as_of_utc=AS_OF,
    )


def _execution(
    query_id: str = INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,
) -> QueryExecutionEvidence:
    return QueryExecutionEvidence(
        query_id=query_id,
        query_execution_id="exec-projection-1",
        rows_returned=2,
        data_scanned_bytes=64,
        workgroup="historical-analytics",
    )


def _nonzero_summary() -> EncounterSummaryResult:
    return EncounterSummaryResult(
        physical_record_count=2,
        distinct_encounter_count=2,
        distinct_aircraft_count=1,
        distinct_hazard_count=1,
        distinct_dedup_count=2,
        min_event_time_utc="2026-09-11T10:00:00Z",
        max_event_time_utc="2026-09-11T10:30:00Z",
    )


def _zero_summary() -> EncounterSummaryResult:
    return EncounterSummaryResult(
        physical_record_count=0,
        distinct_encounter_count=0,
        distinct_aircraft_count=0,
        distinct_hazard_count=0,
        distinct_dedup_count=0,
    )


def _record(index: int = 1) -> HistoricalEncounterRecord:
    suffix = str(index)
    return HistoricalEncounterRecord(
        encounter_id=f"proj-{suffix}#hazard-1#v1",
        record_id=f"proj-{suffix}#hazard-1#v1",
        dedup_id=f"proj-{suffix}#hazard-1#v1|ENCOUNTER_OBSERVED|1700000000|DETECTED",
        aircraft_id="abc123",
        hazard_id="hazard-1",
        hazard_version_key="hazard-1#v1",
        fact_kind="ENCOUNTER_OBSERVED",
        encounter_state="DETECTED",
        event_time_utc=f"2026-09-11T10:{index:02d}:00Z",
        hazard_type="CONVECTION",
    )


def _map(response: HistoricalQueryResponse) -> ToolResult:
    return map_historical_query_response(response, tool_call_id=TOOL_CALL_ID)


def test_projection_preserves_succeeded_safety_fields():
    original = _map(
        HistoricalQueryResponse(
            status=HistoricalQueryStatus.SUCCEEDED,
            operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            requested_scope=SummarizeHistoricalEncountersRequest(
                start_utc=WINDOW[0],
                end_utc=WINDOW[1],
            ),
            coverage=_coverage(Evaluability.EVALUABLE),
            evidence=_query_evidence(
                coverage_state=Evaluability.EVALUABLE,
                semantic_match_count=2,
                semantic_match_count_is_exact=True,
                executions=(_execution(),),
            ),
            result=_nonzero_summary(),
        )
    )
    snapshot = original.to_dict()
    projection = project_tool_result(original)

    assert projection.status is ToolResultStatus.SUCCESS
    assert projection.temporal_scope is TemporalScope.HISTORICAL
    assert projection.as_of_utc == AS_OF
    assert projection.operation == "summarize_historical_encounters"
    assert projection.result == original.data["result"]
    assert projection.requested_scope == original.data["requested_scope"]
    evidence = projection.evidence[0]
    assert evidence.completeness is not None
    assert evidence.completeness.status == Evaluability.EVALUABLE.value
    assert evidence.match_cardinality == MatchCardinality(
        exact_count=2,
        is_exact=True,
        minimum_count=None,
    )
    assert HISTORICAL_FRESHNESS_NOT_ESTABLISHED in evidence.limitations
    assert projection.error_code is None
    assert evidence.error_code is None
    assert original.to_dict() == snapshot
    assert original.evidence[0].query_executions


def test_projection_preserves_verified_zero_and_hides_traces():
    original = _map(
        HistoricalQueryResponse(
            status=HistoricalQueryStatus.VERIFIED_ZERO,
            operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            requested_scope=SummarizeHistoricalEncountersRequest(
                start_utc=WINDOW[0],
                end_utc=WINDOW[1],
            ),
            coverage=_coverage(Evaluability.EVALUABLE),
            evidence=_query_evidence(
                coverage_state=Evaluability.EVALUABLE,
                semantic_match_count=0,
                semantic_match_count_is_exact=True,
                executions=(_execution(),),
            ),
            result=_zero_summary(),
        )
    )
    projection = project_tool_result(original)
    payload = projection.to_dict()

    assert projection.status is ToolResultStatus.NOT_FOUND
    assert projection.evidence[0].match_cardinality == MatchCardinality(
        exact_count=0,
        is_exact=True,
        minimum_count=None,
    )
    serialized = str(payload)
    for key in AUDIT_ONLY_TRACE_KEYS:
        assert key not in payload
        assert key not in serialized
    assert original.evidence[0].query_executions[0].execution_id == "exec-projection-1"


def test_projection_preserves_partial_lower_bound():
    original = _map(
        HistoricalQueryResponse(
            status=HistoricalQueryStatus.RESULT_TRUNCATED,
            operation=HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
            requested_scope=ListHistoricalEncountersRequest(
                start_utc=WINDOW[0],
                end_utc=WINDOW[1],
                aircraft_id="abc123",
                limit=1,
            ),
            coverage=_coverage(Evaluability.EVALUABLE),
            evidence=_query_evidence(
                operation=HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
                coverage_state=Evaluability.EVALUABLE,
                semantic_match_count=None,
                semantic_match_count_is_exact=False,
                minimum_match_count=2,
                executions=(
                    QueryExecutionEvidence(
                        query_id=INTERNAL_QUERY_ID_LIST_ENCOUNTERS,
                        query_execution_id="exec-list-1",
                        rows_returned=2,
                        data_scanned_bytes=32,
                        workgroup="historical-analytics",
                    ),
                ),
            ),
            result=ListEncountersResult(records=(_record(),), truncated=True),
        )
    )
    projection = project_tool_result(original)
    cardinality = projection.evidence[0].match_cardinality

    assert projection.status is ToolResultStatus.PARTIAL
    assert cardinality is not None
    assert cardinality.is_exact is False
    assert cardinality.exact_count is None
    assert cardinality.minimum_count == 2
    assert RESULT_TRUNCATED_LIMITATION in projection.limitations
    assert isinstance(projection.result, dict)
    assert len(projection.result["records"]) == 1
    assert len(projection.evidence[0].source_records) == 1


def test_projection_preserves_coverage_blocked_unavailability():
    original = _map(
        HistoricalQueryResponse(
            status=HistoricalQueryStatus.COVERAGE_BLOCKED,
            operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            requested_scope=SummarizeHistoricalEncountersRequest(
                start_utc=WINDOW[0],
                end_utc=WINDOW[1],
            ),
            coverage=_coverage(None, COVERAGE_REASON_EPOCH_AMBIGUOUS),
            evidence=_query_evidence(coverage_state=None),
            error=HistoricalQueryErrorEvidence(
                code=COVERAGE_REASON_EPOCH_AMBIGUOUS,
                message=COVERAGE_REASON_EPOCH_AMBIGUOUS,
            ),
        )
    )
    projection = project_tool_result(original)

    assert projection.status is ToolResultStatus.UNAVAILABLE
    assert projection.status is not ToolResultStatus.NOT_FOUND
    assert projection.error_code == COVERAGE_REASON_EPOCH_AMBIGUOUS
    assert projection.evidence[0].error_code == COVERAGE_REASON_EPOCH_AMBIGUOUS
    completeness = projection.evidence[0].completeness
    assert completeness is not None
    assert completeness.status is None
    assert completeness.reason == COVERAGE_REASON_EPOCH_AMBIGUOUS
    assert projection.evidence[0].match_cardinality is None or (
        projection.evidence[0].match_cardinality.exact_count != 0
    )


def test_projection_does_not_remap_status():
    original = _map(
        HistoricalQueryResponse(
            status=HistoricalQueryStatus.SUCCEEDED,
            operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            requested_scope=SummarizeHistoricalEncountersRequest(
                start_utc=WINDOW[0],
                end_utc=WINDOW[1],
            ),
            coverage=_coverage(Evaluability.EVALUABLE),
            evidence=_query_evidence(
                coverage_state=Evaluability.EVALUABLE,
                semantic_match_count=2,
                semantic_match_count_is_exact=True,
                executions=(_execution(),),
            ),
            result=_nonzero_summary(),
        )
    )
    projection = project_tool_result(original)
    assert projection.status is original.status
    assert projection.evidence[0].completeness is not None
    assert projection.evidence[0].completeness.status == "EVALUABLE"
    assert "freshness_status" not in projection.evidence[0].to_dict()


@pytest.mark.parametrize("count", [0, 1, 25])
def test_list_projection_keeps_records_and_source_records_aligned(count):
    records = tuple(_record(index=index + 1) for index in range(count))
    original = _map(
        HistoricalQueryResponse(
            status=HistoricalQueryStatus.SUCCEEDED,
            operation=HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
            requested_scope=ListHistoricalEncountersRequest(
                start_utc=WINDOW[0],
                end_utc=WINDOW[1],
                aircraft_id="abc123",
                limit=count or 1,
            ),
            coverage=_coverage(Evaluability.EVALUABLE),
            evidence=_query_evidence(
                operation=HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
                coverage_state=Evaluability.EVALUABLE,
                semantic_match_count=count,
                semantic_match_count_is_exact=True,
                executions=(_execution(INTERNAL_QUERY_ID_LIST_ENCOUNTERS),),
            ),
            result=ListEncountersResult(records=records),
        )
    )
    projection = project_tool_result(original)
    result_records = (
        projection.result["records"] if isinstance(projection.result, dict) else []
    )
    assert len(result_records) == count
    assert len(projection.evidence[0].source_records) == count
    assert original.evidence[0].source_records == projection.evidence[0].source_records
    for data_record, source in zip(
        result_records,
        projection.evidence[0].source_records,
        strict=True,
    ):
        assert data_record["record_id"] == source.record_id
        assert data_record["event_time_utc"] == source.event_timestamp_utc


def test_more_than_25_records_fails_closed_without_mutating_original():
    records = tuple(
        SourceRecord(
            record_id=f"record-{index}",
            source_version=None,
            event_timestamp_utc=AS_OF,
        )
        for index in range(MODEL_VISIBLE_RECORD_MAX + 1)
    )
    original = ToolResult(
        tool_name="list_historical_encounters",
        tool_call_id=TOOL_CALL_ID,
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.HISTORICAL,
        data={
            "operation": "list_historical_encounters",
            "requested_scope": {"limit": 26},
            "result": {
                "records": [{"record_id": item.record_id} for item in records],
                "truncated": False,
            },
            "limitations": [],
            "error": None,
        },
        evidence=(
            Evidence(
                source="wilvor.historical.encounter_fact",
                source_records=records,
                query_timestamp_utc=AS_OF,
                freshness_status=FreshnessStatus.UNKNOWN,
                confidence=ConfidenceLevel.HIGH,
                limitations=(HISTORICAL_FRESHNESS_NOT_ESTABLISHED,),
                tool_call_id=TOOL_CALL_ID,
                temporal_scope=TemporalScope.HISTORICAL,
                completeness=SourceCompleteness(
                    status="EVALUABLE",
                    evaluated_as_of_utc=AS_OF,
                ),
                match_cardinality=MatchCardinality(
                    exact_count=26,
                    is_exact=True,
                ),
                query_executions=(
                    QueryExecutionTrace(
                        query_id="internal-list",
                        execution_id="exec-too-many",
                        rows_returned=26,
                        bytes_scanned=99,
                        engine_scope="historical-analytics",
                    ),
                ),
            ),
        ),
        as_of_utc=AS_OF,
        limitations=(),
    )
    snapshot = original.to_dict()
    with pytest.raises(ToolResultProjectionError) as exc_info:
        project_tool_result(original)
    assert "projection_record_limit_exceeded" in exc_info.value.errors
    assert original.to_dict() == snapshot
    assert len(original.evidence[0].source_records) == 26
    assert original.evidence[0].query_executions[0].bytes_scanned == 99


def test_projection_rejects_current_scope_and_forbidden_payload_keys():
    current = ToolResult(
        tool_name="search_current_hazards",
        tool_call_id=TOOL_CALL_ID,
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.CURRENT,
        data={"hazards": []},
        evidence=(
            Evidence(
                source="wilvor.operational.hazard",
                source_records=(),
                query_timestamp_utc=AS_OF,
                freshness_status=FreshnessStatus.UNKNOWN,
                confidence=ConfidenceLevel.HIGH,
                limitations=("FRESHNESS_NOT_ESTABLISHED",),
                tool_call_id=TOOL_CALL_ID,
                temporal_scope=TemporalScope.CURRENT,
            ),
        ),
        as_of_utc=AS_OF,
        limitations=(),
    )
    with pytest.raises(ToolResultProjectionError) as current_error:
        project_tool_result(current)
    assert "projection_requires_historical_scope" in current_error.value.errors

    historical = replace(
        current,
        tool_name="summarize_historical_encounters",
        temporal_scope=TemporalScope.HISTORICAL,
        data={"coverage": {"evaluability": "EVALUABLE"}, "result": None},
        evidence=(
            replace(current.evidence[0], temporal_scope=TemporalScope.HISTORICAL),
        ),
    )
    with pytest.raises(ToolResultProjectionError) as blob_error:
        project_tool_result(historical)
    assert "forbidden_application_data_key" in blob_error.value.errors


def test_model_turn_request_round_trips_schemas_and_projections():
    original = _map(
        HistoricalQueryResponse(
            status=HistoricalQueryStatus.SUCCEEDED,
            operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            requested_scope=SummarizeHistoricalEncountersRequest(
                start_utc=WINDOW[0],
                end_utc=WINDOW[1],
            ),
            coverage=_coverage(Evaluability.EVALUABLE),
            evidence=_query_evidence(
                coverage_state=Evaluability.EVALUABLE,
                semantic_match_count=2,
                semantic_match_count_is_exact=True,
                executions=(_execution(),),
            ),
            result=_nonzero_summary(),
        )
    )
    schemas = historical_analytics_tool_schemas()
    initial = ModelTurnRequest(
        user_text="summarize encounters",
        tools=schemas,
    )
    later = ModelTurnRequest(
        user_text="summarize encounters",
        tools=schemas,
        tool_results=(project_tool_result(original),),
    )
    assert len(initial.tools) == 4
    assert initial.tool_results == ()
    assert ModelTurnRequest.from_dict(initial.to_dict()) == initial
    assert ModelTurnRequest.from_dict(later.to_dict()) == later
    assert "role" not in later.to_dict()
    assert "messages" not in later.to_dict()


def _two_record_list() -> ToolResult:
    records = (_record(index=1), _record(index=2))
    return _map(
        HistoricalQueryResponse(
            status=HistoricalQueryStatus.SUCCEEDED,
            operation=HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
            requested_scope=ListHistoricalEncountersRequest(
                start_utc=WINDOW[0],
                end_utc=WINDOW[1],
                aircraft_id="abc123",
                limit=2,
            ),
            coverage=_coverage(Evaluability.EVALUABLE),
            evidence=_query_evidence(
                operation=HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
                coverage_state=Evaluability.EVALUABLE,
                semantic_match_count=2,
                semantic_match_count_is_exact=True,
                executions=(_execution(INTERNAL_QUERY_ID_LIST_ENCOUNTERS),),
            ),
            result=ListEncountersResult(records=records),
        )
    )


def _with_result_records(original: ToolResult, records: list[dict]) -> ToolResult:
    data = dict(original.data)
    result = dict(data["result"])
    result["records"] = records
    data["result"] = result
    return replace(original, data=data)


def test_mapping_integrity_projects_evidence_error_when_application_error_absent():
    original = _map(
        HistoricalQueryResponse(
            status=HistoricalQueryStatus.VERIFIED_ZERO,
            operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            requested_scope=SummarizeHistoricalEncountersRequest(
                start_utc=WINDOW[0],
                end_utc=WINDOW[1],
            ),
            coverage=_coverage(Evaluability.EVALUABLE),
            evidence=_query_evidence(
                coverage_state=Evaluability.EVALUABLE,
                semantic_match_count=2,
                semantic_match_count_is_exact=True,
                executions=(_execution(),),
            ),
            result=_nonzero_summary(),
        )
    )
    assert original.evidence[0].error_code == MAPPING_INTEGRITY_FAILED
    assert original.data["error"] is None
    projection = project_tool_result(original)
    assert projection.error_code == MAPPING_INTEGRITY_FAILED
    assert projection.evidence[0].error_code == MAPPING_INTEGRITY_FAILED


def test_application_and_evidence_error_codes_must_agree():
    original = _map(
        HistoricalQueryResponse(
            status=HistoricalQueryStatus.COVERAGE_BLOCKED,
            operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            requested_scope=SummarizeHistoricalEncountersRequest(
                start_utc=WINDOW[0],
                end_utc=WINDOW[1],
            ),
            coverage=_coverage(None, COVERAGE_REASON_EPOCH_AMBIGUOUS),
            evidence=_query_evidence(coverage_state=None),
            error=HistoricalQueryErrorEvidence(
                code=COVERAGE_REASON_EPOCH_AMBIGUOUS,
                message=COVERAGE_REASON_EPOCH_AMBIGUOUS,
            ),
        )
    )
    snapshot = original.to_dict()
    data = dict(original.data)
    data["error"] = {"code": "QUERY_FAILED", "message": "QUERY_FAILED"}
    mismatched = replace(original, data=data)
    with pytest.raises(ToolResultProjectionError) as exc_info:
        project_tool_result(mismatched)
    assert "projection_error_code_mismatch" in exc_info.value.errors
    assert original.to_dict() == snapshot
    assert mismatched.data["error"]["code"] == "QUERY_FAILED"
    assert mismatched.evidence[0].error_code == COVERAGE_REASON_EPOCH_AMBIGUOUS


def test_invalid_request_projects_without_error_code_or_as_of():
    original = map_historical_invalid_request(
        tool_name="list_historical_encounters",
        tool_call_id=TOOL_CALL_ID,
        attempted_scope={"start_utc": WINDOW[0], "end_utc": WINDOW[1]},
    )
    projection = project_tool_result(original)
    assert projection.error_code is None
    assert projection.evidence == ()
    assert projection.as_of_utc is None
    assert original.data["error"]["code"] == "INVALID_REQUEST"


def test_list_record_identity_mismatch_and_reorder_fail_closed():
    original = _two_record_list()
    snapshot = original.to_dict()
    records = list(original.data["result"]["records"])
    first, second = records[0], records[1]

    mismatched_id = _with_result_records(
        original,
        [{**first, "record_id": "proj-9#hazard-1#v1"}, second],
    )
    with pytest.raises(ToolResultProjectionError) as id_error:
        project_tool_result(mismatched_id)
    assert "projection_record_identity_mismatch" in id_error.value.errors

    mismatched_time = _with_result_records(
        original,
        [{**first, "event_time_utc": "2026-09-11T11:00:00Z"}, second],
    )
    with pytest.raises(ToolResultProjectionError) as time_error:
        project_tool_result(mismatched_time)
    assert "projection_record_identity_mismatch" in time_error.value.errors

    reordered = _with_result_records(original, [second, first])
    with pytest.raises(ToolResultProjectionError) as order_error:
        project_tool_result(reordered)
    assert "projection_record_identity_mismatch" in order_error.value.errors

    assert original.to_dict() == snapshot
    assert project_tool_result(original).evidence[0].source_records[0].record_id == (
        first["record_id"]
    )


def test_as_of_values_must_agree_when_populated():
    original = _map(
        HistoricalQueryResponse(
            status=HistoricalQueryStatus.SUCCEEDED,
            operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            requested_scope=SummarizeHistoricalEncountersRequest(
                start_utc=WINDOW[0],
                end_utc=WINDOW[1],
            ),
            coverage=_coverage(Evaluability.EVALUABLE),
            evidence=_query_evidence(
                coverage_state=Evaluability.EVALUABLE,
                semantic_match_count=2,
                semantic_match_count_is_exact=True,
                executions=(_execution(),),
            ),
            result=_nonzero_summary(),
        )
    )
    snapshot = original.to_dict()
    other = "2026-09-14T00:00:00Z"
    projection = project_tool_result(original)
    assert projection.as_of_utc == AS_OF
    assert original.evidence[0].query_timestamp_utc == AS_OF
    assert original.evidence[0].completeness.evaluated_as_of_utc == AS_OF

    as_of_mismatch = replace(original, as_of_utc=other)
    with pytest.raises(ToolResultProjectionError) as as_of_error:
        project_tool_result(as_of_mismatch)
    assert "projection_as_of_mismatch" in as_of_error.value.errors

    query_vs_as_of = replace(
        original,
        evidence=(replace(original.evidence[0], query_timestamp_utc=other),),
    )
    with pytest.raises(ToolResultProjectionError) as query_as_of_error:
        project_tool_result(query_vs_as_of)
    assert "projection_as_of_mismatch" in query_as_of_error.value.errors

    completeness_mismatch = replace(
        original,
        evidence=(
            replace(
                original.evidence[0],
                completeness=replace(
                    original.evidence[0].completeness,
                    evaluated_as_of_utc=other,
                ),
            ),
        ),
    )
    with pytest.raises(ToolResultProjectionError) as completeness_error:
        project_tool_result(completeness_mismatch)
    assert "projection_as_of_mismatch" in completeness_error.value.errors

    query_mismatch = replace(
        original,
        as_of_utc=None,
        evidence=(
            replace(original.evidence[0], query_timestamp_utc=other),
        ),
    )
    with pytest.raises(ToolResultProjectionError) as query_error:
        project_tool_result(query_mismatch)
    assert "projection_as_of_mismatch" in query_error.value.errors
    assert original.to_dict() == snapshot
