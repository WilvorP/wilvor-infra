"""Deterministic Athena SQL rendering for closed historical queries.

Callers never supply SQL fragments, identifiers, operators, GROUP BY,
ORDER BY, or partition text. This module does not import AWS or execute
queries.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from wilvor_historical.query_contracts import (
    LIST_MAX_LIMIT,
    LIST_MIN_LIMIT,
    HistoricalQueryError,
)
from wilvor_historical.query_windows import (
    UtcCalendarDate,
    parse_query_window,
    touched_utc_dates,
)


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Code-owned tables and columns only. Never accept a caller identifier.
_ALLOWED_IDENTIFIERS = frozenset(
    {
        "encounter",
        "risk",
        "hazard_version",
        "year",
        "month",
        "day",
        "fact_schema_version",
        "event_time_utc",
        "event_time_epoch",
        "record_id",
        "dedup_id",
        "encounter_id",
        "aircraft_id",
        "hazard_id",
        "hazard_type",
        "hazard_version_key",
        "fact_kind",
        "encounter_state",
        "risk_id",
        "risk_level",
        "risk_score",
        "product_type",
        "materialized_at_utc",
    }
)

_ALLOWED_ALIASES = frozenset(
    {
        "physical_record_count",
        "distinct_encounter_count",
        "distinct_aircraft_count",
        "distinct_hazard_count",
        "distinct_dedup_count",
        "min_event_time_utc",
        "max_event_time_utc",
        "distinct_risk_count",
        "min_risk_score",
        "max_risk_score",
        "distinct_hazard_version_count",
        "min_materialized_at_utc",
        "max_materialized_at_utc",
        "risk_level",
    }
)

_ALLOWED_SORT_DIRECTIONS = frozenset({"ASC", "DESC"})
_CANONICAL_UTC_COLUMNS = frozenset({"event_time_utc", "materialized_at_utc"})
_WHOLE_SECOND_FRACTION_SUFFIX = ".000000Z"


class HistoricalQueryRenderError(HistoricalQueryError):
    """Raised when a fixed historical query cannot be rendered safely."""


def sql_string_literal(value: str) -> str:
    """Return a single-quoted SQL string with quotes doubled.

    Standard Athena/Trino escaping: ``'`` becomes ``''``. Backslash is a
    data character, not an escape. This is the injection boundary.
    """

    if not isinstance(value, str):
        raise HistoricalQueryRenderError("SQL string literal requires a str")
    if "\x00" in value:
        raise HistoricalQueryRenderError("SQL string literal contains NUL")
    return "'" + value.replace("'", "''") + "'"


def sql_identifier(name: str) -> str:
    """Return a code-owned identifier. Callers cannot supply names."""

    if not isinstance(name, str) or name not in _ALLOWED_IDENTIFIERS:
        raise HistoricalQueryRenderError("unknown SQL identifier")
    if not _IDENTIFIER_RE.fullmatch(name):
        raise HistoricalQueryRenderError("invalid SQL identifier")
    return name


def sql_alias(name: str) -> str:
    if not isinstance(name, str) or name not in _ALLOWED_ALIASES:
        raise HistoricalQueryRenderError("unknown SQL alias")
    if not _IDENTIFIER_RE.fullmatch(name):
        raise HistoricalQueryRenderError("invalid SQL alias")
    return name


def sql_integer_literal(value: int) -> str:
    """Render a code-owned integer. Callers cannot supply a SQL fragment."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise HistoricalQueryRenderError("SQL integer literal requires an int")
    return str(value)


def sql_limit_row_count(domain_limit: int) -> int:
    """Return LIMIT N+1 after validating the domain limit N."""

    if isinstance(domain_limit, bool) or not isinstance(domain_limit, int):
        raise HistoricalQueryRenderError("invalid limit")
    if domain_limit < LIST_MIN_LIMIT or domain_limit > LIST_MAX_LIMIT:
        raise HistoricalQueryRenderError("invalid limit")
    return domain_limit + 1


def render_limit_clause(domain_limit: int) -> str:
    return f"LIMIT {sql_limit_row_count(domain_limit)}"


def render_equality_predicate(column: str, value: str) -> str:
    """Render ``column = 'literal'``. ``value`` is always a quoted literal."""

    if not isinstance(value, str) or value == "":
        raise HistoricalQueryRenderError(f"invalid {column}")
    return f"{sql_identifier(column)} = {sql_string_literal(value)}"


def render_comparison_predicate(column: str, operator: str, value: str) -> str:
    if operator not in {">=", "<"}:
        raise HistoricalQueryRenderError("unsupported comparison operator")
    if not isinstance(value, str) or not value:
        raise HistoricalQueryRenderError(f"invalid {column}")
    return f"{sql_identifier(column)} {operator} {sql_string_literal(value)}"


def render_partition_predicate(dates: Sequence[UtcCalendarDate]) -> str:
    """Render an exact year/month/day OR of touched UTC dates.

    Never emits ``year IN`` / ``month IN`` / ``day IN`` Cartesian
    supersets. Each date is one ``year = 'YYYY' AND month = 'MM' AND
    day = 'DD'`` triple using Glue's zero-padded string partition keys.
    """

    if not dates:
        raise HistoricalQueryRenderError("query window touches no UTC dates")
    triples: list[str] = []
    for date in dates:
        if not isinstance(date, UtcCalendarDate):
            raise HistoricalQueryRenderError("invalid UTC calendar date")
        triples.append(
            "("
            f"year = {sql_string_literal(date.year)}"
            f" AND month = {sql_string_literal(date.month)}"
            f" AND day = {sql_string_literal(date.day)}"
            ")"
        )
    if len(triples) == 1:
        return triples[0]
    joined = "\n      OR\n      ".join(triples)
    return f"(\n      {joined}\n    )"


def render_integer_comparison_predicate(
    column: str,
    operator: str,
    value: int,
) -> str:
    if operator not in {">=", "<"}:
        raise HistoricalQueryRenderError("unsupported comparison operator")
    return f"{sql_identifier(column)} {operator} {sql_integer_literal(value)}"


def render_event_time_predicates(start_utc: str, end_utc: str) -> tuple[str, str]:
    """Half-open ``event_time_epoch >= start AND event_time_epoch < end``.

    Query windows are whole UTC seconds. Stored ``event_time_utc`` VARCHAR
    is not compared; its width is not a temporal order.
    """

    window = parse_query_window(start_utc, end_utc)
    return (
        render_integer_comparison_predicate(
            "event_time_epoch", ">=", window.start_epoch
        ),
        render_integer_comparison_predicate(
            "event_time_epoch", "<", window.end_epoch
        ),
    )


def render_schema_version_predicate(fact_schema_version: str) -> str:
    if not isinstance(fact_schema_version, str) or not fact_schema_version:
        raise HistoricalQueryRenderError("invalid fact_schema_version")
    return render_equality_predicate("fact_schema_version", fact_schema_version)


def required_window_predicates(
    *,
    start_utc: str,
    end_utc: str,
    fact_schema_version: str,
) -> tuple[str, ...]:
    dates = touched_utc_dates(start_utc, end_utc)
    event_start, event_end = render_event_time_predicates(start_utc, end_utc)
    return (
        render_partition_predicate(dates),
        event_start,
        event_end,
        render_schema_version_predicate(fact_schema_version),
    )


def optional_equality_predicate(column: str, value: str | None) -> str | None:
    if value is None:
        return None
    return render_equality_predicate(column, value)


def render_where(predicates: Sequence[str]) -> str:
    """Join code-owned predicates with AND. No ``WHERE 1=1`` fragment path."""

    cleaned = tuple(item for item in predicates if item is not None)
    if not cleaned:
        raise HistoricalQueryRenderError("WHERE requires code-owned predicates")
    if any(not isinstance(item, str) or not item.strip() for item in cleaned):
        raise HistoricalQueryRenderError("invalid WHERE predicate")
    return "WHERE\n    " + "\n    AND ".join(cleaned)


def render_select(select_items: Sequence[str]) -> str:
    if not select_items:
        raise HistoricalQueryRenderError("SELECT requires code-owned columns")
    if any(not isinstance(item, str) or not item.strip() for item in select_items):
        raise HistoricalQueryRenderError("invalid SELECT item")
    return "SELECT\n    " + ",\n    ".join(select_items)


def render_from(table_name: str) -> str:
    return f"FROM {sql_identifier(table_name)}"


def count_star(alias: str) -> str:
    return f"COUNT(*) AS {sql_alias(alias)}"


def count_distinct(column: str, alias: str) -> str:
    return f"COUNT(DISTINCT {sql_identifier(column)}) AS {sql_alias(alias)}"


def min_column(column: str, alias: str) -> str:
    return f"MIN({sql_identifier(column)}) AS {sql_alias(alias)}"


def max_column(column: str, alias: str) -> str:
    return f"MAX({sql_identifier(column)}) AS {sql_alias(alias)}"


def canonical_utc_order_key_sql(column: str) -> str:
    """Normalize a stored ``canonicalize_utc_z`` string for lexical order.

    Whole-second forms have no fraction and must sort as ``.000000Z``.
    Fractional forms already have six digits from ``datetime.isoformat()``.
    This is string rewriting only. It does not parse Athena timestamps.
    """

    if column not in _CANONICAL_UTC_COLUMNS:
        raise HistoricalQueryRenderError("unsupported canonical UTC column")
    ident = sql_identifier(column)
    suffix = sql_string_literal(_WHOLE_SECOND_FRACTION_SUFFIX)
    return (
        f"CASE WHEN {ident} LIKE '%.%Z' THEN {ident}"
        f" ELSE replace({ident}, 'Z', {suffix}) END"
    )


def min_by_canonical_utc(column: str, alias: str) -> str:
    """Return the stored canonical UTC string of the earliest event."""

    return (
        f"min_by({sql_identifier(column)}, {canonical_utc_order_key_sql(column)})"
        f" AS {sql_alias(alias)}"
    )


def max_by_canonical_utc(column: str, alias: str) -> str:
    """Return the stored canonical UTC string of the latest event."""

    return (
        f"max_by({sql_identifier(column)}, {canonical_utc_order_key_sql(column)})"
        f" AS {sql_alias(alias)}"
    )


def order_by_identifier(column: str, direction: str = "ASC") -> str:
    if direction not in _ALLOWED_SORT_DIRECTIONS:
        raise HistoricalQueryRenderError("unsupported ORDER BY direction")
    return f"{sql_identifier(column)} {direction}"


def order_by_canonical_utc(column: str, direction: str = "ASC") -> str:
    if direction not in _ALLOWED_SORT_DIRECTIONS:
        raise HistoricalQueryRenderError("unsupported ORDER BY direction")
    return f"{canonical_utc_order_key_sql(column)} {direction}"


def select_column(column: str) -> str:
    return sql_identifier(column)


def render_group_by(columns: Sequence[str]) -> str:
    if not columns:
        raise HistoricalQueryRenderError("GROUP BY requires code-owned columns")
    rendered = [sql_identifier(column) for column in columns]
    return "GROUP BY " + ", ".join(rendered)


def render_order_by(clauses: Sequence[str]) -> str:
    if not clauses:
        raise HistoricalQueryRenderError("ORDER BY requires code-owned columns")
    if any(not isinstance(item, str) or not item.strip() for item in clauses):
        raise HistoricalQueryRenderError("invalid ORDER BY clause")
    return "ORDER BY\n    " + ",\n    ".join(clauses)


def render_select_statement(
    *,
    select_items: Sequence[str],
    table_name: str,
    predicates: Sequence[str],
    group_by: Sequence[str] = (),
    order_by: Sequence[str] = (),
    domain_limit: int | None = None,
) -> str:
    """Assemble one deterministic statement from code-owned parts only."""

    clauses = [
        render_select(select_items),
        render_from(table_name),
        render_where(predicates),
    ]
    if group_by:
        clauses.append(render_group_by(group_by))
    if order_by:
        clauses.append(render_order_by(order_by))
    if domain_limit is not None:
        clauses.append(render_limit_clause(domain_limit))
    return "\n".join(clauses)
