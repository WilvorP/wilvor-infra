"""Phase 2B-preflight historical query contract tests."""

from __future__ import annotations

import ast
import inspect
from dataclasses import fields

import pytest

from wilvor_historical.coverage_contracts import Evaluability
from wilvor_historical.query_contracts import (
    AIRCRAFT_ID_CHARACTERS,
    COVERAGE_REASON_EPOCH_AMBIGUOUS,
    COVERAGE_STORE_METADATA_PREFIXES,
    ENCOUNTER_ID_CHARACTERS,
    FORBIDDEN_OPERATION_NAMES,
    FORBIDDEN_QUERY_REQUEST_FIELDS,
    HAZARD_ID_CHARACTERS,
    LIST_DEFAULT_LIMIT,
    INTERNAL_QUERY_ID_LIST_ENCOUNTERS,
    INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,
    INTERNAL_QUERY_ID_SUMMARIZE_HAZARD_VERSIONS,
    INTERNAL_QUERY_ID_SUMMARIZE_RISKS,
    INTERNAL_QUERY_ID_SUMMARIZE_RISKS_BY_LEVEL,
    LIST_HISTORICAL_ENCOUNTERS_ORDERING,
    LIST_MAX_LIMIT,
    STORED_DEDUP_ID_CHARACTERS,
    V1_HISTORICAL_OPERATIONS,
    CoverageEvidence,
    HAZARD_VERSION_WINDOW_LIMITATION,
    QUERY_RESULT_MALFORMED,
    EncounterSummaryResult,
    HazardVersionSummaryResult,
    HistoricalEncounterRecord,
    HistoricalOperation,
    HistoricalQueryError,
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
    is_verified_zero,
    request_field_names,
    status_for_evaluable_result,
    status_for_unevaluable_coverage,
)


WINDOW = ("2026-09-11T00:00:00Z", "2026-09-12T00:00:00Z")


def test_v1_catalog_is_exactly_four_named_operations():
    names = {item.value for item in HistoricalOperation}
    assert names == {
        "summarize_historical_encounters",
        "summarize_historical_risks",
        "summarize_historical_hazard_versions",
        "list_historical_encounters",
    }
    assert V1_HISTORICAL_OPERATIONS == frozenset(HistoricalOperation)
    assert names.isdisjoint(FORBIDDEN_OPERATION_NAMES)
    assert "run_sql" not in names
    assert "execute_athena" not in names
    assert "query_table" not in names


def test_evaluability_enum_is_unchanged():
    assert {item.value for item in Evaluability} == {
        "NOT_ACTIVE",
        "NOT_YET_EVALUABLE",
        "EVALUABLE",
        "GAP_OR_UNCERTAIN",
    }
    assert "EPOCH_AMBIGUOUS" not in {item.value for item in Evaluability}
    assert COVERAGE_REASON_EPOCH_AMBIGUOUS == "EPOCH_AMBIGUOUS"
    assert (
        status_for_unevaluable_coverage(reason=COVERAGE_REASON_EPOCH_AMBIGUOUS)
        is HistoricalQueryStatus.COVERAGE_BLOCKED
    )


def test_summarize_encounters_request_accepts_optional_filters():
    request = SummarizeHistoricalEncountersRequest(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        aircraft_id="abc123",
        hazard_id="sigmet-aaa",
        hazard_type="CONVECTION",
    )
    assert request.aircraft_id == "abc123"
    assert request.to_dict()["hazard_type"] == "CONVECTION"


def test_summarize_risks_and_hazard_version_requests():
    risks = SummarizeHistoricalRisksRequest(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        encounter_id="proj-1#hazard-1#v1",
        risk_level="HIGH",
    )
    hazards = SummarizeHistoricalHazardVersionsRequest(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        product_type="SIGMET",
    )
    assert risks.encounter_id == "proj-1#hazard-1#v1"
    assert hazards.product_type == "SIGMET"


def test_list_requires_aircraft_or_hazard_and_defaults_limit():
    with pytest.raises(HistoricalQueryError, match="aircraft_id or hazard_id"):
        ListHistoricalEncountersRequest(start_utc=WINDOW[0], end_utc=WINDOW[1])
    request = ListHistoricalEncountersRequest(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        aircraft_id="abc123",
    )
    assert request.limit == LIST_DEFAULT_LIMIT
    both = ListHistoricalEncountersRequest(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        hazard_id="sigmet-aaa",
        limit=1,
    )
    assert both.limit == 1
    assert both.hazard_id == "sigmet-aaa"


def test_list_limit_bounds_and_non_int_rejected():
    with pytest.raises(HistoricalQueryError, match="invalid limit"):
        ListHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            aircraft_id="abc123",
            limit=0,
        )
    with pytest.raises(HistoricalQueryError, match="invalid limit"):
        ListHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            aircraft_id="abc123",
            limit=LIST_MAX_LIMIT + 1,
        )
    with pytest.raises(HistoricalQueryError, match="invalid limit"):
        ListHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            aircraft_id="abc123",
            limit=True,
        )
    capped = ListHistoricalEncountersRequest(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        aircraft_id="abc123",
        limit=LIST_MAX_LIMIT,
    )
    assert capped.limit == LIST_MAX_LIMIT


def test_list_ordering_is_code_owned_not_a_request_field():
    assert LIST_HISTORICAL_ENCOUNTERS_ORDERING == (
        "canonical_event_time_utc ASC",
        "record_id ASC",
        "dedup_id ASC",
    )
    assert "order_by" not in request_field_names(ListHistoricalEncountersRequest)
    assert "sort" not in request_field_names(ListHistoricalEncountersRequest)


def test_requests_reject_invalid_windows():
    with pytest.raises(HistoricalQueryError, match="must precede"):
        SummarizeHistoricalEncountersRequest(
            start_utc=WINDOW[1],
            end_utc=WINDOW[0],
        )
    with pytest.raises(HistoricalQueryError, match="start_utc is not UTC"):
        SummarizeHistoricalRisksRequest(
            start_utc="2026-09-11T00:00:00-04:00",
            end_utc=WINDOW[1],
        )
    with pytest.raises(HistoricalQueryError, match="whole-second"):
        SummarizeHistoricalEncountersRequest(
            start_utc="2026-09-11T12:00:00.100000Z",
            end_utc=WINDOW[1],
        )


def test_empty_and_oversized_filters_are_rejected():
    with pytest.raises(HistoricalQueryError, match="missing aircraft_id"):
        SummarizeHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            aircraft_id="   ",
        )
    with pytest.raises(HistoricalQueryError, match="exceeds maximum length"):
        SummarizeHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            hazard_id="h" * 257,
        )
    with pytest.raises(HistoricalQueryError, match="invalid hazard_type"):
        SummarizeHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            hazard_type="CONVECTION'; DROP TABLE encounter",
        )


def test_caller_identifiers_match_repository_shapes():
    request = SummarizeHistoricalRisksRequest(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        aircraft_id="a1b2c3",
        hazard_id="sigmet-aaa",
        encounter_id="proj-1#hazard-1#v1",
        risk_level="LOW",
    )
    production_like = SummarizeHistoricalRisksRequest(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        aircraft_id="abc123",
        hazard_id="sigmet-" + "a" * 24,
        encounter_id="projection-0123456789abcdef0123456789abcdef#sigmet-aaa#v1",
    )
    assert request.encounter_id.endswith("#v1")
    assert production_like.hazard_id.startswith("sigmet-")
    assert "#" in ENCOUNTER_ID_CHARACTERS
    assert "-" in HAZARD_ID_CHARACTERS
    assert "|" not in AIRCRAFT_ID_CHARACTERS
    assert "|" not in HAZARD_ID_CHARACTERS
    assert "|" not in ENCOUNTER_ID_CHARACTERS
    assert "|" in STORED_DEDUP_ID_CHARACTERS


def test_pipe_is_rejected_on_caller_ids_but_allowed_on_dedup_output():
    with pytest.raises(HistoricalQueryError, match="invalid aircraft_id"):
        SummarizeHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            aircraft_id="abc|123",
        )
    with pytest.raises(HistoricalQueryError, match="invalid hazard_id"):
        SummarizeHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            hazard_id="sigmet|aaa",
        )
    with pytest.raises(HistoricalQueryError, match="invalid encounter_id"):
        SummarizeHistoricalRisksRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            encounter_id="proj-1#hazard-1#v1|extra",
        )
    with pytest.raises(HistoricalQueryError, match="invalid encounter_id"):
        SummarizeHistoricalRisksRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            encounter_id="proj-1",
        )
    record = HistoricalEncounterRecord(
        encounter_id="proj-1#hazard-1#v1",
        record_id="proj-1#hazard-1#v1",
        dedup_id="proj-1#hazard-1#v1|ENCOUNTER_OBSERVED|1700000000|DETECTED",
        aircraft_id="abc123",
        hazard_id="hazard-1",
        hazard_version_key="hazard-1#v1",
        fact_kind="ENCOUNTER_OBSERVED",
        encounter_state="DETECTED",
        event_time_utc="2026-09-11T12:00:00Z",
        hazard_type="CONVECTION",
    )
    assert "|" in record.dedup_id


def test_open_dimensions_are_not_closed_enums():
    for value in ("CONVECTION", "TURBULENCE", "OTHER_STORED_TYPE"):
        request = SummarizeHistoricalHazardVersionsRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            hazard_type=value,
            product_type="SIGMET",
        )
        assert request.hazard_type == value
    source = inspect.getsource(SummarizeHistoricalEncountersRequest)
    assert "CONVECTION" not in source
    assert "Enum" not in source


def test_requests_have_no_geography_sql_or_current_fields():
    request_types = (
        SummarizeHistoricalEncountersRequest,
        SummarizeHistoricalRisksRequest,
        SummarizeHistoricalHazardVersionsRequest,
        ListHistoricalEncountersRequest,
    )
    for request_type in request_types:
        names = request_field_names(request_type)
        assert names.isdisjoint(FORBIDDEN_QUERY_REQUEST_FIELDS)
        assert "inside_now" not in names
        assert "sql" not in names


def test_verified_zero_requires_evaluable_and_exact_semantic_count_zero():
    assert "rows_returned" not in inspect.signature(is_verified_zero).parameters
    assert is_verified_zero(
        coverage=Evaluability.EVALUABLE,
        semantic_match_count=0,
    )
    assert not is_verified_zero(
        coverage=Evaluability.EVALUABLE,
        semantic_match_count=1443,
    )
    assert not is_verified_zero(
        coverage=Evaluability.EVALUABLE,
        semantic_match_count=None,
    )
    for state in (
        Evaluability.GAP_OR_UNCERTAIN,
        Evaluability.NOT_ACTIVE,
        Evaluability.NOT_YET_EVALUABLE,
    ):
        assert not is_verified_zero(coverage=state, semantic_match_count=0)
        assert not is_verified_zero(coverage=state, semantic_match_count=None)


def test_evaluable_count_row_with_zero_is_verified_zero_status():
    summary = EncounterSummaryResult(
        physical_record_count=0,
        distinct_encounter_count=0,
        distinct_aircraft_count=0,
        distinct_hazard_count=0,
        distinct_dedup_count=0,
    )
    assert summary.semantic_match_count == 0
    assert summary.semantic_match_count_is_exact is True
    assert summary.minimum_match_count is None
    assert (
        status_for_evaluable_result(
            semantic_match_count=summary.semantic_match_count,
        )
        is HistoricalQueryStatus.VERIFIED_ZERO
    )
    evidence = QueryEvidence(
        query_name=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        datasets=("encounter",),
        requested_start_utc=WINDOW[0],
        requested_end_utc=WINDOW[1],
        coverage_state=Evaluability.EVALUABLE,
        collection_epoch_ids=("epoch-1",),
        semantic_match_count=summary.semantic_match_count,
        semantic_match_count_is_exact=True,
        query_executions=(
            QueryExecutionEvidence(
                query_id=INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,
                rows_returned=1,
            ),
        ),
    )
    assert evidence.query_executions[0].rows_returned == 1
    assert "rows_returned" not in {item.name for item in fields(QueryEvidence)}
    assert evidence.semantic_match_count == 0
    assert evidence.semantic_match_count_is_exact is True
    assert is_verified_zero(
        coverage=evidence.coverage_state,
        semantic_match_count=evidence.semantic_match_count,
    )


def test_physical_record_count_is_not_distinct_encounter_count():
    summary = EncounterSummaryResult(
        physical_record_count=1443,
        distinct_encounter_count=1438,
        distinct_aircraft_count=10,
        distinct_hazard_count=20,
        distinct_dedup_count=1442,
        min_event_time_utc="2026-09-11T00:00:00Z",
        max_event_time_utc="2026-09-11T23:59:59Z",
    )
    payload = summary.to_dict()
    assert payload["physical_record_count"] == 1443
    assert payload["distinct_encounter_count"] == 1438
    assert "count" not in payload
    assert "encounter_count" not in payload
    assert summary.semantic_match_count == 1443
    assert (
        status_for_evaluable_result(semantic_match_count=summary.semantic_match_count)
        is HistoricalQueryStatus.SUCCEEDED
    )


def test_risk_and_hazard_version_summary_shapes():
    risks = RiskSummaryResult(
        physical_record_count=1438,
        distinct_risk_count=1438,
        distinct_encounter_count=1438,
        distinct_aircraft_count=10,
        min_risk_score=0,
        max_risk_score=90,
        risk_level_distribution=(
            RiskLevelBucket(risk_level="LOW", physical_record_count=10),
            RiskLevelBucket(risk_level="HIGH", physical_record_count=1428),
        ),
    )
    versions = HazardVersionSummaryResult(
        physical_record_count=20,
        distinct_hazard_count=18,
        distinct_hazard_version_count=20,
        min_materialized_at_utc="2026-09-11T00:00:00Z",
        max_materialized_at_utc="2026-09-11T12:00:00Z",
    )
    assert risks.semantic_match_count == 1438
    assert versions.semantic_match_count == 20
    assert risks.to_dict()["risk_level_distribution"][0]["risk_level"] == "LOW"


def test_list_count_exactness_and_truncation():
    record = HistoricalEncounterRecord(
        encounter_id="proj-1#hazard-1#v1",
        record_id="proj-1#hazard-1#v1",
        dedup_id="proj-1#hazard-1#v1|ENCOUNTER_OBSERVED|1700000000|DETECTED",
        aircraft_id="abc123",
        hazard_id="hazard-1",
        hazard_version_key="hazard-1#v1",
        fact_kind="ENCOUNTER_OBSERVED",
        encounter_state="DETECTED",
        event_time_utc="2026-09-11T12:00:00Z",
        hazard_type="CONVECTION",
    )
    empty = ListEncountersResult(records=())
    assert empty.semantic_match_count == 0
    assert empty.semantic_match_count_is_exact is True
    assert empty.minimum_match_count is None
    assert is_verified_zero(
        coverage=Evaluability.EVALUABLE,
        semantic_match_count=empty.semantic_match_count,
    )
    complete = ListEncountersResult(records=(record,))
    assert complete.semantic_match_count == 1
    assert complete.semantic_match_count_is_exact is True
    assert complete.minimum_match_count is None
    truncated = ListEncountersResult(records=(record,), truncated=True)
    payload = truncated.to_dict()
    assert truncated.semantic_match_count is None
    assert truncated.semantic_match_count_is_exact is False
    assert truncated.minimum_match_count == 2
    assert payload["semantic_match_count"] is None
    assert payload["minimum_match_count"] == 2
    assert not is_verified_zero(
        coverage=Evaluability.EVALUABLE,
        semantic_match_count=truncated.semantic_match_count,
    )
    assert (
        status_for_evaluable_result(
            semantic_match_count=truncated.semantic_match_count,
            truncated=True,
        )
        is HistoricalQueryStatus.RESULT_TRUNCATED
    )


def test_coverage_evidence_uses_plural_epoch_ids():
    evidence = CoverageEvidence(
        evaluability=Evaluability.EVALUABLE,
        reason="window_evaluable",
        required_horizon_seconds=173760,
        required_streams=("facts",),
        collection_epoch_ids=("epoch-a", "epoch-b"),
    )
    payload = evidence.to_dict()
    assert payload["collection_epoch_ids"] == ["epoch-a", "epoch-b"]
    assert "current_epoch" not in payload
    assert not hasattr(evidence, "current_epoch")
    names = {item.name for item in fields(QueryEvidence)}
    assert "current_epoch" not in names
    assert "collection_epoch_ids" in names
    assert "semantic_match_count" in names
    assert "semantic_match_count_is_exact" in names
    assert "minimum_match_count" in names
    assert "query_executions" in names
    assert "evaluated_as_of_utc" in names
    assert "generated_at_utc" not in names
    assert "rows_returned" not in names
    assert "query_execution_id" not in names
    assert "workgroup" not in names
    execution_names = {item.name for item in fields(QueryExecutionEvidence)}
    assert "sql" not in execution_names
    assert "rows_returned" in execution_names
    assert "data_scanned_bytes" in execution_names
    assert "query_execution_id" in execution_names


def test_blocked_operation_may_have_zero_executions():
    evidence = QueryEvidence(
        query_name=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        datasets=("encounter",),
        requested_start_utc=WINDOW[0],
        requested_end_utc=WINDOW[1],
        coverage_state=Evaluability.GAP_OR_UNCERTAIN,
        collection_epoch_ids=("epoch-1",),
        query_executions=(),
    )
    assert evidence.query_executions == ()
    assert evidence.data_scanned_bytes == 0
    assert evidence.semantic_match_count is None


def test_single_query_operation_carries_one_execution():
    execution = QueryExecutionEvidence(
        query_id=INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,
        query_execution_id="exec-encounter-1",
        rows_returned=1,
        data_scanned_bytes=4096,
        workgroup="wilvor-dev-historical-analytics",
    )
    evidence = QueryEvidence(
        query_name=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        datasets=("encounter",),
        requested_start_utc=WINDOW[0],
        requested_end_utc=WINDOW[1],
        coverage_state=Evaluability.EVALUABLE,
        collection_epoch_ids=("epoch-1",),
        semantic_match_count=0,
        semantic_match_count_is_exact=True,
        query_executions=(execution,),
    )
    assert len(evidence.query_executions) == 1
    assert evidence.query_executions[0].query_execution_id == "exec-encounter-1"
    assert evidence.query_executions[0].rows_returned == 1
    assert evidence.data_scanned_bytes == 4096
    assert evidence.semantic_match_count == 0
    list_evidence = QueryEvidence(
        query_name=HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
        datasets=("encounter",),
        requested_start_utc=WINDOW[0],
        requested_end_utc=WINDOW[1],
        coverage_state=Evaluability.EVALUABLE,
        collection_epoch_ids=("epoch-1",),
        semantic_match_count=1,
        semantic_match_count_is_exact=True,
        query_executions=(
            QueryExecutionEvidence(
                query_id=INTERNAL_QUERY_ID_LIST_ENCOUNTERS,
                query_execution_id="exec-list-1",
                rows_returned=1,
                data_scanned_bytes=512,
            ),
        ),
    )
    hazard_evidence = QueryEvidence(
        query_name=HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS,
        datasets=("hazard_version",),
        requested_start_utc=WINDOW[0],
        requested_end_utc=WINDOW[1],
        coverage_state=Evaluability.EVALUABLE,
        collection_epoch_ids=("epoch-1",),
        semantic_match_count=20,
        semantic_match_count_is_exact=True,
        query_executions=(
            QueryExecutionEvidence(
                query_id=INTERNAL_QUERY_ID_SUMMARIZE_HAZARD_VERSIONS,
                query_execution_id="exec-hazard-1",
                rows_returned=1,
                data_scanned_bytes=256,
            ),
        ),
    )
    assert len(list_evidence.query_executions) == 1
    assert len(hazard_evidence.query_executions) == 1
    assert list_evidence.semantic_match_count == 1
    assert hazard_evidence.semantic_match_count == 20


def test_risk_summary_evidence_carries_two_distinct_executions():
    executions = (
        QueryExecutionEvidence(
            query_id=INTERNAL_QUERY_ID_SUMMARIZE_RISKS,
            query_execution_id="exec-risk-agg",
            rows_returned=1,
            data_scanned_bytes=2048,
            workgroup="wilvor-dev-historical-analytics",
        ),
        QueryExecutionEvidence(
            query_id=INTERNAL_QUERY_ID_SUMMARIZE_RISKS_BY_LEVEL,
            query_execution_id="exec-risk-dist",
            rows_returned=3,
            data_scanned_bytes=1024,
            workgroup="wilvor-dev-historical-analytics",
        ),
    )
    evidence = QueryEvidence(
        query_name=HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS,
        datasets=("risk",),
        requested_start_utc=WINDOW[0],
        requested_end_utc=WINDOW[1],
        coverage_state=Evaluability.EVALUABLE,
        collection_epoch_ids=("epoch-1",),
        semantic_match_count=1438,
        semantic_match_count_is_exact=True,
        query_executions=executions,
    )
    assert [item.query_id for item in evidence.query_executions] == [
        INTERNAL_QUERY_ID_SUMMARIZE_RISKS,
        INTERNAL_QUERY_ID_SUMMARIZE_RISKS_BY_LEVEL,
    ]
    assert [item.query_execution_id for item in evidence.query_executions] == [
        "exec-risk-agg",
        "exec-risk-dist",
    ]
    assert evidence.query_executions[0].rows_returned == 1
    assert evidence.query_executions[1].rows_returned == 3
    assert evidence.query_executions[0].data_scanned_bytes == 2048
    assert evidence.query_executions[1].data_scanned_bytes == 1024
    assert evidence.data_scanned_bytes == 3072
    assert evidence.to_dict()["data_scanned_bytes"] == 3072
    assert evidence.semantic_match_count == 1438
    unknown_bytes = QueryEvidence(
        query_name=HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS,
        datasets=("risk",),
        requested_start_utc=WINDOW[0],
        requested_end_utc=WINDOW[1],
        coverage_state=Evaluability.EVALUABLE,
        collection_epoch_ids=("epoch-1",),
        query_executions=(
            executions[0],
            QueryExecutionEvidence(
                query_id=INTERNAL_QUERY_ID_SUMMARIZE_RISKS_BY_LEVEL,
                query_execution_id="exec-risk-dist-unknown",
                rows_returned=3,
            ),
        ),
    )
    assert unknown_bytes.data_scanned_bytes is None
    assert INTERNAL_QUERY_ID_SUMMARIZE_RISKS_BY_LEVEL not in {
        item.value for item in HistoricalOperation
    }
    with pytest.raises(HistoricalQueryError, match="does not belong"):
        QueryEvidence(
            query_name=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            datasets=("encounter",),
            requested_start_utc=WINDOW[0],
            requested_end_utc=WINDOW[1],
            coverage_state=Evaluability.EVALUABLE,
            collection_epoch_ids=("epoch-1",),
            query_executions=executions,
        )


def test_future_coverage_store_prefixes_are_complete_not_window_bounded():
    assert COVERAGE_STORE_METADATA_PREFIXES == (
        "metadata/epoch/",
        "metadata/activation/",
        "metadata/deactivation/",
        "metadata/coverage/",
        "metadata/gaps/",
        "metadata/resolutions/",
        "metadata/incidents/",
    )


def test_query_modules_have_no_sql_or_aws():
    from wilvor_historical import query_contracts, query_windows

    forbidden = {
        "boto3",
        "botocore",
        "wilvor_ai",
        "athena",
        "pyathena",
    }
    for module in (query_contracts, query_windows):
        imported = set()
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        assert imported.isdisjoint(forbidden)
        source = inspect.getsource(module)
        assert "time.time(" not in source
        assert "datetime.now(" not in source
        assert "SELECT" not in source
        assert "StartQueryExecution" not in source


def test_query_evidence_evaluated_as_of_is_not_generation_time():
    evidence = QueryEvidence(
        query_name=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        datasets=("encounter",),
        requested_start_utc=WINDOW[0],
        requested_end_utc=WINDOW[1],
        coverage_state=Evaluability.EVALUABLE,
        collection_epoch_ids=("epoch-1",),
        semantic_match_count=0,
        semantic_match_count_is_exact=True,
        evaluated_as_of_utc="2026-09-13T12:00:00Z",
    )
    assert evidence.evaluated_as_of_utc == "2026-09-13T12:00:00Z"
    assert "generated_at_utc" not in evidence.to_dict()
    assert not hasattr(evidence, "generated_at_utc")


def test_hazard_version_limitation_is_materialization_time():
    assert "materialization/event time" in HAZARD_VERSION_WINDOW_LIMITATION
    assert "valid_from_utc" not in HAZARD_VERSION_WINDOW_LIMITATION
    assert "valid_to_utc" not in HAZARD_VERSION_WINDOW_LIMITATION
    assert QUERY_RESULT_MALFORMED == "RESULT_MALFORMED"


def test_historical_query_response_rejects_result_when_blocked():
    request = SummarizeHistoricalEncountersRequest(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
    )
    coverage = CoverageEvidence(
        evaluability=Evaluability.GAP_OR_UNCERTAIN,
        reason="open_gap",
        required_horizon_seconds=173760,
        required_streams=("facts",),
        collection_epoch_ids=("epoch-a",),
    )
    evidence = QueryEvidence(
        query_name=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        datasets=("encounter",),
        requested_start_utc=WINDOW[0],
        requested_end_utc=WINDOW[1],
        coverage_state=Evaluability.GAP_OR_UNCERTAIN,
        collection_epoch_ids=("epoch-a",),
    )
    with pytest.raises(HistoricalQueryError, match="cannot carry a result"):
        HistoricalQueryResponse(
            status=HistoricalQueryStatus.COVERAGE_BLOCKED,
            operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            requested_scope=request,
            coverage=coverage,
            evidence=evidence,
            result=EncounterSummaryResult(
                physical_record_count=0,
                distinct_encounter_count=0,
                distinct_aircraft_count=0,
                distinct_hazard_count=0,
                distinct_dedup_count=0,
            ),
        )
    blocked = HistoricalQueryResponse(
        status=HistoricalQueryStatus.COVERAGE_BLOCKED,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        requested_scope=request,
        coverage=coverage,
        evidence=evidence,
        error=HistoricalQueryErrorEvidence(code="open_gap", message="open_gap"),
    )
    assert blocked.result is None
    assert blocked.to_dict()["result"] is None
