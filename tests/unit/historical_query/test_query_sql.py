"""SQL literal safety, LIMIT, and renderer defense-in-depth tests."""

from __future__ import annotations

import inspect

import pytest

from wilvor_historical.query_contracts import (
    HistoricalQueryError,
    ListHistoricalEncountersRequest,
    SummarizeHistoricalEncountersRequest,
    SummarizeHistoricalRisksRequest,
)
from wilvor_historical_query import (
    HistoricalQueryRenderError,
    render_historical_operation,
    sql_string_literal,
)
from wilvor_historical_query.query_sql import (
    render_equality_predicate,
    render_limit_clause,
    render_select_statement,
    sql_identifier,
    sql_integer_literal,
    sql_limit_row_count,
)


WINDOW = ("2026-09-11T00:00:00Z", "2026-09-12T00:00:00Z")


def test_single_quote_is_doubled_inside_a_quoted_literal():
    assert sql_string_literal("O'Hare") == "'O''Hare'"
    assert sql_string_literal("'") == "''''"
    assert sql_string_literal("''") == "''''''"
    rendered = render_equality_predicate("aircraft_id", "x' OR '1'='1")
    assert rendered == "aircraft_id = 'x'' OR ''1''=''1'"
    assert rendered.startswith("aircraft_id = '")
    assert rendered.endswith("'")
    assert " OR " in rendered
    assert rendered.split(" = ", 1)[1] == "'x'' OR ''1''=''1'"


def test_injected_or_and_comment_syntax_remain_literal_content():
    payloads = (
        "'; DROP TABLE x; --",
        "x' OR '1'='1",
        "--",
        "/* encounter */",
        "abc\\123",
        "abc\\'def",
    )
    for payload in payloads:
        sql = render_equality_predicate("hazard_id", payload)
        assert sql.startswith("hazard_id = '")
        assert sql.endswith("'")
        assert sql == f"hazard_id = {sql_string_literal(payload)}"
        assert "DROP TABLE" not in sql[: len("hazard_id = ")]


def test_caller_value_cannot_add_a_second_statement():
    sql = render_equality_predicate("aircraft_id", "abc'; DROP TABLE encounter; --")
    assert sql == "aircraft_id = 'abc''; DROP TABLE encounter; --'"
    statement = render_select_statement(
        select_items=("COUNT(*) AS physical_record_count",),
        table_name="encounter",
        predicates=(
            "(year = '2026' AND month = '09' AND day = '11')",
            sql,
        ),
    )
    prefix, literal = statement.split("aircraft_id = ", 1)
    assert not statement.strip().endswith(";")
    assert ";" not in prefix
    assert literal.startswith("'")
    assert literal.endswith("'")
    assert "DROP TABLE encounter" in literal
    assert "FROM encounter" in prefix
    assert statement.count("FROM encounter") == 1


def test_preflight_contract_still_rejects_quote_in_caller_ids():
    with pytest.raises(HistoricalQueryError, match="invalid aircraft_id"):
        SummarizeHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            aircraft_id="x' OR '1'='1",
        )
    with pytest.raises(HistoricalQueryError, match="invalid hazard_id"):
        SummarizeHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            hazard_id="'; DROP TABLE x; --",
        )


def test_backslash_is_data_not_an_escape():
    assert sql_string_literal("foo\\bar") == "'foo\\bar'"
    assert sql_string_literal("foo\\") == "'foo\\'"


def test_nul_in_literal_is_rejected():
    with pytest.raises(HistoricalQueryRenderError, match="NUL"):
        sql_string_literal("abc\x00def")


def test_integer_literals_reject_bool_and_strings():
    assert sql_integer_literal(1757587200) == "1757587200"
    with pytest.raises(HistoricalQueryRenderError, match="integer"):
        sql_integer_literal(True)  # type: ignore[arg-type]
    with pytest.raises(HistoricalQueryRenderError, match="integer"):
        sql_integer_literal("1757587200")  # type: ignore[arg-type]


def test_limit_is_validated_integer_n_plus_one():
    assert sql_limit_row_count(1) == 2
    assert sql_limit_row_count(200) == 201
    assert render_limit_clause(100) == "LIMIT 101"
    for invalid in (0, 201, -1, True, "100", 1.5, None):
        with pytest.raises(HistoricalQueryRenderError, match="invalid limit"):
            sql_limit_row_count(invalid)  # type: ignore[arg-type]
        with pytest.raises((HistoricalQueryRenderError, HistoricalQueryError)):
            ListHistoricalEncountersRequest(
                start_utc=WINDOW[0],
                end_utc=WINDOW[1],
                aircraft_id="abc123",
                limit=invalid,  # type: ignore[arg-type]
            )


def test_renderer_rejects_empty_optional_filter_instead_of_malformed_sql():
    with pytest.raises(HistoricalQueryRenderError, match="invalid aircraft_id"):
        render_equality_predicate("aircraft_id", "")


def test_unknown_identifier_or_operator_is_rejected():
    with pytest.raises(HistoricalQueryRenderError, match="unknown SQL identifier"):
        sql_identifier("encounter; DROP TABLE x")
    with pytest.raises(HistoricalQueryRenderError, match="unknown SQL identifier"):
        sql_identifier("year IN")
    with pytest.raises(HistoricalQueryRenderError, match="unknown SQL identifier"):
        render_equality_predicate("valid_from_utc", "2026-01-01T00:00:00Z")


def test_no_arbitrary_group_or_order_parameter_exists():
    source = inspect.getsource(render_historical_operation)
    assert "group_by" not in inspect.signature(render_historical_operation).parameters
    assert "order_by" not in inspect.signature(render_historical_operation).parameters
    assert "sql" not in inspect.signature(render_historical_operation).parameters
    assert "table" not in inspect.signature(render_historical_operation).parameters
    assert "columns" not in inspect.signature(render_historical_operation).parameters
    assert "def render_historical_operation(request" in source


def test_rendered_optional_filter_keeps_hash_and_hyphen_as_data():
    sql = render_historical_operation(
        SummarizeHistoricalRisksRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
            encounter_id="proj-1#hazard-1#v1",
            hazard_id="sigmet-aaa",
        )
    ).queries[0].sql
    assert "encounter_id = 'proj-1#hazard-1#v1'" in sql
    assert "hazard_id = 'sigmet-aaa'" in sql
    assert sql.count(";") == 0
