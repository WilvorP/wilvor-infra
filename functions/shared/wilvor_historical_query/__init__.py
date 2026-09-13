"""Deterministic historical analytics query registry and SQL renderer.

Phase 2B.1 owns fixed-query SQL only. It does not execute Athena, load
coverage, orchestrate operations, or adapt ToolResults.
"""

from .query_registry import (
    ENCOUNTER_SUMMARY_OUTPUT_COLUMNS,
    HAZARD_VERSION_SUMMARY_OUTPUT_COLUMNS,
    LIST_ENCOUNTER_OUTPUT_COLUMNS,
    RISK_LEVEL_DISTRIBUTION_OUTPUT_COLUMNS,
    RISK_SUMMARY_OUTPUT_COLUMNS,
    InternalQueryId,
    QueryDefinition,
    RenderedOperation,
    RenderedQuery,
    get_query_definition,
    public_query_definitions,
    public_query_ids,
    render_historical_operation,
)
from .query_sql import HistoricalQueryRenderError, sql_string_literal

__all__ = [
    "ENCOUNTER_SUMMARY_OUTPUT_COLUMNS",
    "HAZARD_VERSION_SUMMARY_OUTPUT_COLUMNS",
    "LIST_ENCOUNTER_OUTPUT_COLUMNS",
    "RISK_LEVEL_DISTRIBUTION_OUTPUT_COLUMNS",
    "RISK_SUMMARY_OUTPUT_COLUMNS",
    "HistoricalQueryRenderError",
    "InternalQueryId",
    "QueryDefinition",
    "RenderedOperation",
    "RenderedQuery",
    "get_query_definition",
    "public_query_definitions",
    "public_query_ids",
    "render_historical_operation",
    "sql_string_literal",
]
