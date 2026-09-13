"""Closed registry and rendered SQL contracts for Phase 2B.1."""

from __future__ import annotations

import inspect
from dataclasses import fields

import pytest

from wilvor_historical.contracts import (
    ENCOUNTER_FACT_SCHEMA_VERSION,
    HAZARD_VERSION_FACT_SCHEMA_VERSION,
    RISK_FACT_SCHEMA_VERSION,
    Dataset,
)
from wilvor_historical.query_windows import parse_query_window
from wilvor_historical.query_contracts import (
    LIST_DEFAULT_LIMIT,
    LIST_HISTORICAL_ENCOUNTERS_ORDERING,
    V1_INTERNAL_QUERY_IDS,
    EncounterSummaryResult,
    HazardVersionSummaryResult,
    HistoricalEncounterRecord,
    HistoricalOperation,
    HistoricalQueryError,
    ListHistoricalEncountersRequest,
    RiskLevelBucket,
    RiskSummaryResult,
    SummarizeHistoricalEncountersRequest,
    SummarizeHistoricalHazardVersionsRequest,
    SummarizeHistoricalRisksRequest,
    V1_HISTORICAL_OPERATIONS,
)
from wilvor_historical_query import (
    ENCOUNTER_SUMMARY_OUTPUT_COLUMNS,
    HAZARD_VERSION_SUMMARY_OUTPUT_COLUMNS,
    LIST_ENCOUNTER_OUTPUT_COLUMNS,
    RISK_LEVEL_DISTRIBUTION_OUTPUT_COLUMNS,
    RISK_SUMMARY_OUTPUT_COLUMNS,
    HistoricalQueryRenderError,
    InternalQueryId,
    get_query_definition,
    public_query_definitions,
    public_query_ids,
    render_historical_operation,
)
from wilvor_historical_query import query_registry


WINDOW = ("2026-09-11T00:00:00Z", "2026-09-12T00:00:00Z")
_WINDOW = parse_query_window(*WINDOW)


def _sql(request) -> str:
    rendered = render_historical_operation(request)
    assert len(rendered.queries) >= 1
    return rendered.queries[0].sql


def test_public_registry_is_exactly_the_four_v1_operations():
    ids = public_query_ids()
    assert ids == {
        "summarize_historical_encounters",
        "summarize_historical_risks",
        "summarize_historical_hazard_versions",
        "list_historical_encounters",
    }
    assert ids == {item.value for item in V1_HISTORICAL_OPERATIONS}
    assert {item.operation for item in public_query_definitions()} == (
        V1_HISTORICAL_OPERATIONS
    )
    assert all(item.public for item in public_query_definitions())
    assert "summarize_historical_risks_by_level" not in ids
    assert "hazard_geometry" not in ids
    assert Dataset.HAZARD_GEOMETRY not in {
        item.dataset for item in public_query_definitions()
    }
    assert {item.value for item in InternalQueryId} == V1_INTERNAL_QUERY_IDS
    assert InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL.value not in ids


def test_risk_distribution_is_internal_not_a_fifth_public_operation():
    definition = get_query_definition(
        InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL
    )
    assert definition.public is False
    assert definition.operation is HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS
    assert definition.query_id.value == "summarize_historical_risks_by_level"
    assert "summarize_historical_risks_by_level" not in {
        item.value for item in HistoricalOperation
    }


def test_unknown_query_id_is_rejected():
    with pytest.raises(HistoricalQueryRenderError, match="unknown historical query"):
        get_query_definition("run_sql")
    with pytest.raises(HistoricalQueryRenderError, match="unknown historical query"):
        get_query_definition("query_table")


def test_schema_versions_are_code_owned_v1_constants():
    assert (
        get_query_definition(InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS)
        .fact_schema_version
        == ENCOUNTER_FACT_SCHEMA_VERSION
    )
    assert (
        get_query_definition(InternalQueryId.LIST_HISTORICAL_ENCOUNTERS)
        .fact_schema_version
        == ENCOUNTER_FACT_SCHEMA_VERSION
    )
    assert (
        get_query_definition(InternalQueryId.SUMMARIZE_HISTORICAL_RISKS)
        .fact_schema_version
        == RISK_FACT_SCHEMA_VERSION
    )
    assert (
        get_query_definition(InternalQueryId.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS)
        .fact_schema_version
        == HAZARD_VERSION_FACT_SCHEMA_VERSION
    )


def test_output_columns_match_preflight_result_contracts():
    assert ENCOUNTER_SUMMARY_OUTPUT_COLUMNS == tuple(
        field.name for field in fields(EncounterSummaryResult)
    )
    assert RISK_SUMMARY_OUTPUT_COLUMNS == tuple(
        field.name
        for field in fields(RiskSummaryResult)
        if field.name != "risk_level_distribution"
    )
    assert RISK_LEVEL_DISTRIBUTION_OUTPUT_COLUMNS == tuple(
        field.name for field in fields(RiskLevelBucket)
    )
    assert HAZARD_VERSION_SUMMARY_OUTPUT_COLUMNS == tuple(
        field.name for field in fields(HazardVersionSummaryResult)
    )
    assert LIST_ENCOUNTER_OUTPUT_COLUMNS == tuple(
        field.name for field in fields(HistoricalEncounterRecord)
    )


def test_render_api_accepts_only_the_typed_request():
    signature = inspect.signature(render_historical_operation)
    assert list(signature.parameters) == ["request"]
    with pytest.raises(HistoricalQueryRenderError, match="unsupported"):
        render_historical_operation(
            {
                "sql": "SELECT 1",
                "table": "encounter",
                "group_by": "aircraft_id",
            }
        )


def test_summarize_encounters_sql_and_aliases():
    request = SummarizeHistoricalEncountersRequest(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        aircraft_id="abc123",
        hazard_id="hazard-1",
        hazard_type="CONVECTION",
    )
    rendered = render_historical_operation(request)
    assert rendered.operation is HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS
    assert len(rendered.queries) == 1
    sql = rendered.queries[0].sql
    assert rendered.queries[0].output_columns == ENCOUNTER_SUMMARY_OUTPUT_COLUMNS
    assert sql == (
        "SELECT\n"
        "    COUNT(*) AS physical_record_count,\n"
        "    COUNT(DISTINCT encounter_id) AS distinct_encounter_count,\n"
        "    COUNT(DISTINCT aircraft_id) AS distinct_aircraft_count,\n"
        "    COUNT(DISTINCT hazard_id) AS distinct_hazard_count,\n"
        "    COUNT(DISTINCT dedup_id) AS distinct_dedup_count,\n"
        "    min_by(event_time_utc, CASE WHEN event_time_utc LIKE '%.%Z' THEN event_time_utc ELSE replace(event_time_utc, 'Z', '.000000Z') END) AS min_event_time_utc,\n"
        "    max_by(event_time_utc, CASE WHEN event_time_utc LIKE '%.%Z' THEN event_time_utc ELSE replace(event_time_utc, 'Z', '.000000Z') END) AS max_event_time_utc\n"
        "FROM encounter\n"
        "WHERE\n"
        "    (year = '2026' AND month = '09' AND day = '11')\n"
        f"    AND event_time_epoch >= {_WINDOW.start_epoch}\n"
        f"    AND event_time_epoch < {_WINDOW.end_epoch}\n"
        "    AND fact_schema_version = 'wilvor.historical.encounter_fact.v1'\n"
        "    AND aircraft_id = 'abc123'\n"
        "    AND hazard_id = 'hazard-1'\n"
        "    AND hazard_type = 'CONVECTION'"
    )
    assert "encounter_count" not in sql.split("physical_record_count")[0]
    assert "JOIN" not in sql
    assert "inside_now" not in sql
    assert "SELECT *" not in sql


def test_optional_encounter_filters_are_omitted_when_absent():
    sql = _sql(
        SummarizeHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
        )
    )
    assert "aircraft_id =" not in sql
    assert "hazard_id =" not in sql
    assert "hazard_type =" not in sql
    assert "fact_schema_version = 'wilvor.historical.encounter_fact.v1'" in sql


def test_summarize_risks_renders_summary_and_fixed_distribution():
    request = SummarizeHistoricalRisksRequest(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        aircraft_id="abc123",
        encounter_id="proj-1#hazard-1#v1",
        risk_level="HIGH",
    )
    rendered = render_historical_operation(request)
    assert rendered.operation is HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS
    assert [item.query_id for item in rendered.queries] == [
        InternalQueryId.SUMMARIZE_HISTORICAL_RISKS,
        InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL,
    ]
    summary_sql, distribution_sql = (item.sql for item in rendered.queries)
    assert rendered.queries[0].output_columns == RISK_SUMMARY_OUTPUT_COLUMNS
    assert (
        rendered.queries[1].output_columns == RISK_LEVEL_DISTRIBUTION_OUTPUT_COLUMNS
    )
    assert "COUNT(DISTINCT risk_id) AS distinct_risk_count" in summary_sql
    assert "MIN(risk_score) AS min_risk_score" in summary_sql
    assert "MAX(risk_score) AS max_risk_score" in summary_sql
    assert "GROUP BY" not in summary_sql
    assert "FROM risk" in summary_sql
    assert "GROUP BY risk_level" in distribution_sql
    assert "ORDER BY\n    risk_level ASC" in distribution_sql
    assert "COUNT(*) AS physical_record_count" in distribution_sql
    assert "encounter_id = 'proj-1#hazard-1#v1'" in summary_sql
    assert "encounter_id = 'proj-1#hazard-1#v1'" in distribution_sql
    assert "group_by" not in inspect.signature(render_historical_operation).parameters


def test_hazard_version_sql_uses_materialized_event_time_not_validity():
    sql = _sql(
        SummarizeHistoricalHazardVersionsRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            hazard_id="sigmet-aaa",
            product_type="SIGMET",
        )
    )
    assert "FROM hazard_version" in sql
    assert "COUNT(DISTINCT hazard_id) AS distinct_hazard_count" in sql
    assert (
        "COUNT(DISTINCT hazard_version_key) AS distinct_hazard_version_count"
        in sql
    )
    assert (
        "min_by(materialized_at_utc, CASE WHEN materialized_at_utc LIKE '%.%Z' THEN materialized_at_utc ELSE replace(materialized_at_utc, 'Z', '.000000Z') END) AS min_materialized_at_utc"
        in sql
    )
    assert (
        "max_by(materialized_at_utc, CASE WHEN materialized_at_utc LIKE '%.%Z' THEN materialized_at_utc ELSE replace(materialized_at_utc, 'Z', '.000000Z') END) AS max_materialized_at_utc"
        in sql
    )
    assert f"event_time_epoch >= {_WINDOW.start_epoch}" in sql
    assert f"event_time_epoch < {_WINDOW.end_epoch}" in sql
    assert "MIN(materialized_at_utc)" not in sql
    assert "MAX(materialized_at_utc)" not in sql
    assert "valid_from_utc" not in sql
    assert "valid_to_utc" not in sql
    assert (
        "fact_schema_version = 'wilvor.historical.hazard_version_fact.v1'" in sql
    )


def test_list_sql_uses_fixed_order_allowlist_and_n_plus_one_limit():
    request = ListHistoricalEncountersRequest(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        aircraft_id="abc123",
        limit=100,
    )
    rendered = render_historical_operation(request)
    sql = rendered.queries[0].sql
    assert rendered.queries[0].output_columns == LIST_ENCOUNTER_OUTPUT_COLUMNS
    expected_select = "SELECT\n    " + ",\n    ".join(LIST_ENCOUNTER_OUTPUT_COLUMNS)
    assert sql.startswith(expected_select)
    assert "SELECT *" not in sql
    assert "inside_now" not in sql
    assert "corridor_intersects" not in sql
    assert (
        "ORDER BY\n"
        "    CASE WHEN event_time_utc LIKE '%.%Z' THEN event_time_utc ELSE replace(event_time_utc, 'Z', '.000000Z') END ASC,\n"
        "    record_id ASC,\n"
        "    dedup_id ASC"
    ) in sql
    assert sql.endswith("LIMIT 101")
    assert LIST_HISTORICAL_ENCOUNTERS_ORDERING == (
        "canonical_event_time_utc ASC",
        "record_id ASC",
        "dedup_id ASC",
    )
    assert "COUNT(" not in sql
    assert request.limit == LIST_DEFAULT_LIMIT


def test_list_limit_one_renders_limit_two():
    sql = _sql(
        ListHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            hazard_id="hazard-1",
            limit=1,
        )
    )
    assert sql.endswith("LIMIT 2")


def test_list_still_requires_a_selector():
    with pytest.raises(HistoricalQueryError, match="aircraft_id or hazard_id"):
        ListHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
        )


def test_rendering_is_byte_for_byte_deterministic():
    request = SummarizeHistoricalEncountersRequest(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        hazard_type="CONVECTION",
    )
    first = render_historical_operation(request)
    second = render_historical_operation(request)
    assert first.queries[0].sql == second.queries[0].sql
    source = inspect.getsource(query_registry.render_historical_operation)
    assert "time.time" not in source
    assert "uuid" not in source
    assert "random" not in source


def test_query_definitions_have_no_sql_field_for_callers_to_fill():
    definition = get_query_definition(
        InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS
    )
    names = {item.name for item in fields(definition)}
    assert "sql" not in names
    assert "sql_template" not in names
    assert "group_by" not in names
    assert "order_by" not in names
