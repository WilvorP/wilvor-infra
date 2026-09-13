"""Deterministic historical analytics query registry and bounded Athena executor.

Phase 2B.1 owns fixed-query SQL. Phase 2B.2 executes those rendered
queries through an injected Athena client. This package does not load
coverage, orchestrate domain operations, or adapt ToolResults.
"""

from .errors import AthenaExecutorError, AthenaExecutorErrorCode
from .executor import (
    AthenaExecutor,
    AthenaExecutorConfig,
    AthenaQueryResult,
)
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
    render_fixed_query,
    render_historical_operation,
)
from .query_sql import HistoricalQueryRenderError, sql_string_literal

__all__ = [
    "AthenaExecutor",
    "AthenaExecutorConfig",
    "AthenaExecutorError",
    "AthenaExecutorErrorCode",
    "AthenaQueryResult",
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
    "render_fixed_query",
    "render_historical_operation",
    "sql_string_literal",
]
