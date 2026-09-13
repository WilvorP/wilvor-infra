"""Phase 2C.1 historical query response → ToolResult mapping tests."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from wilvor_ai.contracts import (
    ConfidenceLevel,
    FreshnessStatus,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.historical_analytics_mapping import (
    HISTORICAL_FRESHNESS_NOT_ESTABLISHED,
    INVALID_REQUEST_CODE,
    MAPPING_INTEGRITY_FAILED,
    RESULT_TRUNCATED_LIMITATION,
    map_historical_invalid_request,
    map_historical_query_response,
)
from wilvor_historical.coverage_contracts import Evaluability
from wilvor_historical.query_contracts import (
    COVERAGE_REASON_EPOCH_AMBIGUOUS,
    HAZARD_VERSION_WINDOW_LIMITATION,
    INTERNAL_QUERY_ID_LIST_ENCOUNTERS,
    INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,
    INTERNAL_QUERY_ID_SUMMARIZE_RISKS,
    INTERNAL_QUERY_ID_SUMMARIZE_RISKS_BY_LEVEL,
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
    RiskLevelBucket,
    RiskSummaryResult,
    SummarizeHistoricalEncountersRequest,
    SummarizeHistoricalHazardVersionsRequest,
    SummarizeHistoricalRisksRequest,
    HazardVersionSummaryResult,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MAPPING = SHARED_DIR / "wilvor_ai" / "historical_analytics_mapping.py"

AS_OF = "2026-09-13T12:00:00Z"
WINDOW = ("2026-09-11T10:00:00Z", "2026-09-11T11:00:00Z")
TOOL_CALL_ID = "tool-call-historical-001"


def _coverage(
    evaluability: Evaluability | None,
    reason: str = "window_evaluable",
    *,
    epochs: tuple[str, ...] = ("epoch-1",),
) -> CoverageEvidence:
    return CoverageEvidence(
        evaluability=evaluability,
        reason=reason,
        required_horizon_seconds=173760,
        required_streams=("facts",),
        collection_epoch_ids=epochs,
    )


def _query_evidence(
    operation: HistoricalOperation,
    *,
    datasets: tuple[str, ...],
    coverage_state: Evaluability | None,
    semantic_match_count: int | None = None,
    semantic_match_count_is_exact: bool | None = None,
    minimum_match_count: int | None = None,
    executions: tuple[QueryExecutionEvidence, ...] = (),
    evaluated_as_of_utc: str | None = AS_OF,
    epochs: tuple[str, ...] = ("epoch-1",),
) -> QueryEvidence:
    return QueryEvidence(
        query_name=operation,
        datasets=datasets,
        requested_start_utc=WINDOW[0],
        requested_end_utc=WINDOW[1],
        coverage_state=coverage_state,
        collection_epoch_ids=epochs,
        semantic_match_count=semantic_match_count,
        semantic_match_count_is_exact=semantic_match_count_is_exact,
        minimum_match_count=minimum_match_count,
        query_executions=executions,
        evaluated_as_of_utc=evaluated_as_of_utc,
    )


def _execution(
    query_id: str,
    *,
    execution_id: str = "exec-1",
    rows_returned: int = 1,
    bytes_scanned: int = 16,
    workgroup: str = "historical-analytics",
) -> QueryExecutionEvidence:
    return QueryExecutionEvidence(
        query_id=query_id,
        query_execution_id=execution_id,
        rows_returned=rows_returned,
        data_scanned_bytes=bytes_scanned,
        workgroup=workgroup,
    )


def _encounter_request() -> SummarizeHistoricalEncountersRequest:
    return SummarizeHistoricalEncountersRequest(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
    )


def _zero_summary() -> EncounterSummaryResult:
    return EncounterSummaryResult(
        physical_record_count=0,
        distinct_encounter_count=0,
        distinct_aircraft_count=0,
        distinct_hazard_count=0,
        distinct_dedup_count=0,
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


def _map(response: HistoricalQueryResponse) -> ToolResult:
    return map_historical_query_response(response, tool_call_id=TOOL_CALL_ID)


def _round_trip(result: ToolResult) -> ToolResult:
    restored = ToolResult.from_dict(result.to_dict())
    assert restored == result
    return restored


def test_verified_zero_maps_to_not_found_with_first_class_proof():
    response = HistoricalQueryResponse(
        status=HistoricalQueryStatus.VERIFIED_ZERO,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        requested_scope=_encounter_request(),
        coverage=_coverage(Evaluability.EVALUABLE),
        evidence=_query_evidence(
            HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            datasets=("encounter",),
            coverage_state=Evaluability.EVALUABLE,
            semantic_match_count=0,
            semantic_match_count_is_exact=True,
            executions=(_execution(INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS),),
        ),
        result=_zero_summary(),
    )
    result = _round_trip(_map(response))
    evidence = result.evidence[0]

    assert result.status is ToolResultStatus.NOT_FOUND
    assert result.temporal_scope is TemporalScope.HISTORICAL
    assert evidence.temporal_scope is TemporalScope.HISTORICAL
    assert evidence.completeness is not None
    assert evidence.completeness.status == Evaluability.EVALUABLE.value
    assert evidence.match_cardinality is not None
    assert evidence.match_cardinality.exact_count == 0
    assert evidence.match_cardinality.is_exact is True
    assert evidence.confidence is ConfidenceLevel.HIGH
    assert evidence.freshness_status is FreshnessStatus.UNKNOWN
    assert HISTORICAL_FRESHNESS_NOT_ESTABLISHED in evidence.limitations
    assert result.as_of_utc == AS_OF
    assert evidence.query_timestamp_utc == AS_OF
    assert evidence.completeness.evaluated_as_of_utc == AS_OF
    assert "rows_returned" not in result.data
    assert "coverage" not in result.data
    assert "evidence" not in result.data
    assert result.data["status"] == "VERIFIED_ZERO"


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "coverage": _coverage(Evaluability.GAP_OR_UNCERTAIN, "gap_present"),
            "coverage_state": Evaluability.GAP_OR_UNCERTAIN,
            "semantic_match_count": 0,
            "semantic_match_count_is_exact": True,
        },
        {
            "coverage": _coverage(Evaluability.EVALUABLE),
            "coverage_state": Evaluability.EVALUABLE,
            "semantic_match_count": 2,
            "semantic_match_count_is_exact": True,
        },
    ],
)
def test_inconsistent_verified_zero_fails_closed(kwargs):
    response = HistoricalQueryResponse(
        status=HistoricalQueryStatus.VERIFIED_ZERO,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        requested_scope=_encounter_request(),
        coverage=kwargs["coverage"],
        evidence=_query_evidence(
            HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            datasets=("encounter",),
            coverage_state=kwargs["coverage_state"],
            semantic_match_count=kwargs["semantic_match_count"],
            semantic_match_count_is_exact=kwargs["semantic_match_count_is_exact"],
            executions=(_execution(INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS),),
        ),
        result=_zero_summary()
        if kwargs["semantic_match_count"] == 0
        else _nonzero_summary(),
    )
    result = _round_trip(_map(response))

    assert result.status is ToolResultStatus.UNAVAILABLE
    assert result.status is not ToolResultStatus.NOT_FOUND
    assert MAPPING_INTEGRITY_FAILED in result.limitations
    assert result.evidence[0].error_code == MAPPING_INTEGRITY_FAILED
    assert result.data["status"] == "VERIFIED_ZERO"


def test_succeeded_maps_success_and_omits_source_records():
    response = HistoricalQueryResponse(
        status=HistoricalQueryStatus.SUCCEEDED,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        requested_scope=_encounter_request(),
        coverage=_coverage(Evaluability.EVALUABLE),
        evidence=_query_evidence(
            HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            datasets=("encounter",),
            coverage_state=Evaluability.EVALUABLE,
            semantic_match_count=2,
            semantic_match_count_is_exact=True,
            executions=(_execution(INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS),),
        ),
        result=_nonzero_summary(),
    )
    result = _round_trip(_map(response))

    assert result.status is ToolResultStatus.SUCCESS
    assert result.evidence[0].source == "wilvor.historical.encounter_fact"
    assert result.evidence[0].source_records == ()
    assert result.evidence[0].match_cardinality.exact_count == 2
    assert result.evidence[0].confidence is ConfidenceLevel.HIGH
    assert result.as_of_utc == AS_OF


def test_succeeded_without_evaluable_coverage_fails_closed():
    response = HistoricalQueryResponse(
        status=HistoricalQueryStatus.SUCCEEDED,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        requested_scope=_encounter_request(),
        coverage=_coverage(Evaluability.NOT_YET_EVALUABLE, "not_yet"),
        evidence=_query_evidence(
            HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            datasets=("encounter",),
            coverage_state=Evaluability.NOT_YET_EVALUABLE,
            semantic_match_count=2,
            semantic_match_count_is_exact=True,
        ),
        result=_nonzero_summary(),
    )
    result = _round_trip(_map(response))
    assert result.status is ToolResultStatus.UNAVAILABLE
    assert MAPPING_INTEGRITY_FAILED in result.limitations


def test_truncated_list_maps_partial_lower_bound():
    record = HistoricalEncounterRecord(
        encounter_id="proj-1#hazard-1#v1",
        record_id="proj-1#hazard-1#v1",
        dedup_id="proj-1#hazard-1#v1|ENCOUNTER_OBSERVED|1700000000|DETECTED",
        aircraft_id="abc123",
        hazard_id="hazard-1",
        hazard_version_key="hazard-1#v1",
        fact_kind="ENCOUNTER_OBSERVED",
        encounter_state="DETECTED",
        event_time_utc="2026-09-11T10:00:00Z",
        hazard_type="CONVECTION",
    )
    response = HistoricalQueryResponse(
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
            HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
            datasets=("encounter",),
            coverage_state=Evaluability.EVALUABLE,
            semantic_match_count=None,
            semantic_match_count_is_exact=False,
            minimum_match_count=2,
            executions=(_execution(INTERNAL_QUERY_ID_LIST_ENCOUNTERS, rows_returned=2),),
        ),
        result=ListEncountersResult(records=(record,), truncated=True),
        limitations=(),
    )
    result = _round_trip(_map(response))
    cardinality = result.evidence[0].match_cardinality
    source_record = result.evidence[0].source_records[0]

    assert result.status is ToolResultStatus.PARTIAL
    assert cardinality is not None
    assert cardinality.exact_count is None
    assert cardinality.is_exact is False
    assert cardinality.minimum_count == 2
    assert RESULT_TRUNCATED_LIMITATION in result.limitations
    assert result.evidence[0].confidence is ConfidenceLevel.MEDIUM
    assert source_record.record_id == "proj-1#hazard-1#v1"
    assert source_record.event_timestamp_utc == "2026-09-11T10:00:00Z"
    assert source_record.source_version is None


def test_truncated_with_exact_total_fails_closed():
    record = HistoricalEncounterRecord(
        encounter_id="proj-1#hazard-1#v1",
        record_id="proj-1#hazard-1#v1",
        dedup_id="proj-1#hazard-1#v1|ENCOUNTER_OBSERVED|1700000000|DETECTED",
        aircraft_id="abc123",
        hazard_id="hazard-1",
        hazard_version_key="hazard-1#v1",
        fact_kind="ENCOUNTER_OBSERVED",
        encounter_state="DETECTED",
        event_time_utc="2026-09-11T10:00:00Z",
        hazard_type="CONVECTION",
    )
    response = HistoricalQueryResponse(
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
            HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
            datasets=("encounter",),
            coverage_state=Evaluability.EVALUABLE,
            semantic_match_count=1,
            semantic_match_count_is_exact=True,
        ),
        result=ListEncountersResult(records=(record,), truncated=True),
    )
    result = _round_trip(_map(response))
    assert result.status is ToolResultStatus.UNAVAILABLE
    assert result.status is not ToolResultStatus.PARTIAL
    assert MAPPING_INTEGRITY_FAILED in result.limitations


def test_risk_summary_preserves_two_execution_traces():
    response = HistoricalQueryResponse(
        status=HistoricalQueryStatus.SUCCEEDED,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS,
        requested_scope=SummarizeHistoricalRisksRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
        ),
        coverage=_coverage(Evaluability.EVALUABLE),
        evidence=_query_evidence(
            HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS,
            datasets=("risk",),
            coverage_state=Evaluability.EVALUABLE,
            semantic_match_count=4,
            semantic_match_count_is_exact=True,
            executions=(
                _execution(
                    INTERNAL_QUERY_ID_SUMMARIZE_RISKS,
                    execution_id="exec-risk-agg",
                    rows_returned=1,
                    bytes_scanned=2048,
                    workgroup="historical-analytics",
                ),
                _execution(
                    INTERNAL_QUERY_ID_SUMMARIZE_RISKS_BY_LEVEL,
                    execution_id="exec-risk-dist",
                    rows_returned=3,
                    bytes_scanned=1024,
                    workgroup="historical-analytics",
                ),
            ),
        ),
        result=RiskSummaryResult(
            physical_record_count=4,
            distinct_risk_count=4,
            distinct_encounter_count=2,
            distinct_aircraft_count=2,
            min_risk_score=1,
            max_risk_score=4,
            risk_level_distribution=(
                RiskLevelBucket(risk_level="HIGH", physical_record_count=4),
            ),
        ),
    )
    result = _round_trip(_map(response))
    traces = result.evidence[0].query_executions

    assert result.evidence[0].source == "wilvor.historical.risk_fact"
    assert [item.query_id for item in traces] == [
        INTERNAL_QUERY_ID_SUMMARIZE_RISKS,
        INTERNAL_QUERY_ID_SUMMARIZE_RISKS_BY_LEVEL,
    ]
    assert [item.execution_id for item in traces] == [
        "exec-risk-agg",
        "exec-risk-dist",
    ]
    assert [item.rows_returned for item in traces] == [1, 3]
    assert [item.bytes_scanned for item in traces] == [2048, 1024]
    assert {item.engine_scope for item in traces} == {"historical-analytics"}
    assert "sql" not in result.to_dict()
    assert "query_string" not in str(result.to_dict())


def test_hazard_version_limitation_and_source_are_preserved():
    response = HistoricalQueryResponse(
        status=HistoricalQueryStatus.SUCCEEDED,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS,
        requested_scope=SummarizeHistoricalHazardVersionsRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
        ),
        coverage=_coverage(Evaluability.EVALUABLE),
        evidence=_query_evidence(
            HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS,
            datasets=("hazard_version",),
            coverage_state=Evaluability.EVALUABLE,
            semantic_match_count=1,
            semantic_match_count_is_exact=True,
        ),
        result=HazardVersionSummaryResult(
            physical_record_count=1,
            distinct_hazard_count=1,
            distinct_hazard_version_count=1,
            min_materialized_at_utc="2026-09-11T10:00:00Z",
            max_materialized_at_utc="2026-09-11T10:05:00Z",
        ),
        limitations=(HAZARD_VERSION_WINDOW_LIMITATION,),
    )
    result = _round_trip(_map(response))
    assert result.evidence[0].source == "wilvor.historical.hazard_version_fact"
    assert HAZARD_VERSION_WINDOW_LIMITATION in result.limitations
    assert result.data["limitations"] == [HAZARD_VERSION_WINDOW_LIMITATION]


@pytest.mark.parametrize(
    ("status", "reason", "error_code"),
    [
        (
            HistoricalQueryStatus.COVERAGE_BLOCKED,
            "gap_present",
            "gap_present",
        ),
        (
            HistoricalQueryStatus.COVERAGE_STORE_UNAVAILABLE,
            "COVERAGE_STORE_UNAVAILABLE",
            "COVERAGE_STORE_UNAVAILABLE",
        ),
        (
            HistoricalQueryStatus.QUERY_FAILED,
            "window_evaluable",
            "QUERY_FAILED",
        ),
        (
            HistoricalQueryStatus.QUERY_CANCELED,
            "window_evaluable",
            "QUERY_CANCELED",
        ),
        (
            HistoricalQueryStatus.QUERY_TIMEOUT,
            "window_evaluable",
            "QUERY_TIMEOUT",
        ),
        (
            HistoricalQueryStatus.INVALID_REQUEST,
            None,
            "INVALID_REQUEST",
        ),
    ],
)
def test_blocked_and_failure_statuses_never_look_certified(
    status,
    reason,
    error_code,
):
    if status is HistoricalQueryStatus.INVALID_REQUEST:
        coverage = None
        coverage_state = None
        as_of = None
        semantic_count = None
        semantic_exact = None
    elif status is HistoricalQueryStatus.COVERAGE_BLOCKED:
        coverage = _coverage(Evaluability.GAP_OR_UNCERTAIN, reason)
        coverage_state = Evaluability.GAP_OR_UNCERTAIN
        as_of = AS_OF
        semantic_count = 0
        semantic_exact = True
    else:
        coverage = _coverage(Evaluability.EVALUABLE, reason)
        coverage_state = Evaluability.EVALUABLE
        as_of = AS_OF
        semantic_count = None
        semantic_exact = None
    response = HistoricalQueryResponse(
        status=status,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        requested_scope=_encounter_request(),
        coverage=coverage,
        evidence=_query_evidence(
            HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            datasets=("encounter",),
            coverage_state=coverage_state,
            semantic_match_count=semantic_count,
            semantic_match_count_is_exact=semantic_exact,
            evaluated_as_of_utc=as_of,
        ),
        error=HistoricalQueryErrorEvidence(code=error_code, message=error_code),
    )
    result = _round_trip(_map(response))

    assert result.status not in {
        ToolResultStatus.SUCCESS,
        ToolResultStatus.NOT_FOUND,
        ToolResultStatus.PARTIAL,
    }
    if status is HistoricalQueryStatus.INVALID_REQUEST:
        assert result.status is ToolResultStatus.UNKNOWN
        assert result.as_of_utc is None
        assert result.evidence == ()
    else:
        assert result.status is ToolResultStatus.UNAVAILABLE
        assert result.evidence[0].error_code == error_code
        assert result.evidence[0].confidence is ConfidenceLevel.UNKNOWN
    assert result.data["status"] == status.value
    assert result.correlation_id is None


def test_coverage_blocked_epoch_ambiguous_does_not_invent_evaluability():
    response = HistoricalQueryResponse(
        status=HistoricalQueryStatus.COVERAGE_BLOCKED,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        requested_scope=_encounter_request(),
        coverage=_coverage(None, COVERAGE_REASON_EPOCH_AMBIGUOUS),
        evidence=_query_evidence(
            HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            datasets=("encounter",),
            coverage_state=None,
        ),
        error=HistoricalQueryErrorEvidence(
            code=COVERAGE_REASON_EPOCH_AMBIGUOUS,
            message=COVERAGE_REASON_EPOCH_AMBIGUOUS,
        ),
    )
    result = _round_trip(_map(response))
    completeness = result.evidence[0].completeness

    assert result.status is ToolResultStatus.UNAVAILABLE
    assert completeness is not None
    assert completeness.status is None
    assert completeness.reason == COVERAGE_REASON_EPOCH_AMBIGUOUS


def test_invalid_request_helper_is_unknown_without_as_of_or_traces():
    result = _round_trip(
        map_historical_invalid_request(
            tool_name="list_historical_encounters",
            tool_call_id=TOOL_CALL_ID,
            attempted_scope={"start_utc": WINDOW[0], "end_utc": WINDOW[1]},
        )
    )

    assert result.status is ToolResultStatus.UNKNOWN
    assert result.temporal_scope is TemporalScope.HISTORICAL
    assert result.tool_call_id == TOOL_CALL_ID
    assert result.correlation_id is None
    assert result.as_of_utc is None
    assert result.evidence == ()
    assert result.data["status"] == INVALID_REQUEST_CODE
    assert result.data["result"] is None
    assert result.data["error"] == {
        "code": INVALID_REQUEST_CODE,
        "message": INVALID_REQUEST_CODE,
    }
    assert INVALID_REQUEST_CODE in result.limitations


def test_as_of_is_evaluation_instant_not_event_time():
    response = HistoricalQueryResponse(
        status=HistoricalQueryStatus.SUCCEEDED,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        requested_scope=_encounter_request(),
        coverage=_coverage(Evaluability.EVALUABLE),
        evidence=_query_evidence(
            HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            datasets=("encounter",),
            coverage_state=Evaluability.EVALUABLE,
            semantic_match_count=2,
            semantic_match_count_is_exact=True,
        ),
        result=_nonzero_summary(),
    )
    result = _map(response)
    assert result.as_of_utc == AS_OF
    assert result.as_of_utc != result.data["result"]["min_event_time_utc"]
    assert result.as_of_utc != result.data["result"]["max_event_time_utc"]


def test_mapping_does_not_import_runtime_or_provider_sdks():
    source = MAPPING.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
            imported.add(node.module)
    assert imported.isdisjoint(
        {
            "boto3",
            "botocore",
            "wilvor_operational",
            "openai",
            "anthropic",
            "langgraph",
        }
    )
    assert "wilvor_historical_query" not in imported
    assert "wilvor_ai.live_ops" not in imported
    assert "HistoricalAnalyticsOperations" not in source
    assert "AthenaExecutor" not in source
    assert "CoverageGate" not in source
    assert "CoverageStore" not in source
    assert "render_fixed_query" not in source
    assert "datetime.now" not in source
    assert "time.time" not in source


def test_root_wilvor_ai_does_not_import_mapping():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
import wilvor_ai
assert 'wilvor_ai.historical_analytics_mapping' not in sys.modules
assert 'wilvor_historical_query' not in sys.modules
assert not hasattr(wilvor_ai, 'map_historical_query_response')
assert not hasattr(wilvor_ai, 'HISTORICAL_ANALYTICS_TOOLS')
""",
        ],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
