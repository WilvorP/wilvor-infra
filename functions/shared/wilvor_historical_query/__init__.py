"""Deterministic historical analytics query registry, executor, and coverage gate.

Phase 2B.1 owns fixed-query SQL. Phase 2B.2 executes rendered queries
through an injected Athena client. Phase 2B.3 loads coverage metadata
and gates Athena. This package does not orchestrate domain operations
or adapt ToolResults.
"""

from .coverage_gate import CoverageGate, CoverageGateResult
from .coverage_store import (
    CoverageStore,
    CoverageStoreConfig,
    CoverageStoreError,
    CoverageStoreErrorCode,
)
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
    "CoverageGate",
    "CoverageGateResult",
    "CoverageStore",
    "CoverageStoreConfig",
    "CoverageStoreError",
    "CoverageStoreErrorCode",
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
