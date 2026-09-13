"""Closed historical analytics query registry.

The public V1 catalog is exactly four domain operations. Risk-level
distribution is a second internal fixed query owned by
``summarize_historical_risks``. Callers cannot register SQL at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Mapping

from wilvor_historical.contracts import (
    ENCOUNTER_FACT_SCHEMA_VERSION,
    HAZARD_VERSION_FACT_SCHEMA_VERSION,
    RISK_FACT_SCHEMA_VERSION,
    Dataset,
)
from wilvor_historical.query_contracts import (
    INTERNAL_QUERY_ID_LIST_ENCOUNTERS,
    INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,
    INTERNAL_QUERY_ID_SUMMARIZE_HAZARD_VERSIONS,
    INTERNAL_QUERY_ID_SUMMARIZE_RISKS,
    INTERNAL_QUERY_ID_SUMMARIZE_RISKS_BY_LEVEL,
    LIST_HISTORICAL_ENCOUNTERS_ORDERING,
    EncounterSummaryResult,
    HazardVersionSummaryResult,
    HistoricalEncounterRecord,
    HistoricalOperation,
    ListHistoricalEncountersRequest,
    RiskLevelBucket,
    RiskSummaryResult,
    SummarizeHistoricalEncountersRequest,
    SummarizeHistoricalHazardVersionsRequest,
    SummarizeHistoricalRisksRequest,
    V1_HISTORICAL_OPERATIONS,
)

from .query_sql import (
    HistoricalQueryRenderError,
    count_distinct,
    count_star,
    max_by_canonical_utc,
    max_column,
    min_by_canonical_utc,
    min_column,
    optional_equality_predicate,
    order_by_canonical_utc,
    order_by_identifier,
    render_select_statement,
    required_window_predicates,
    select_column,
)


class InternalQueryId(str, Enum):
    """Fixed SQL identities. The public catalog is still four operations."""

    SUMMARIZE_HISTORICAL_ENCOUNTERS = INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS
    SUMMARIZE_HISTORICAL_RISKS = INTERNAL_QUERY_ID_SUMMARIZE_RISKS
    SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL = (
        INTERNAL_QUERY_ID_SUMMARIZE_RISKS_BY_LEVEL
    )
    SUMMARIZE_HISTORICAL_HAZARD_VERSIONS = (
        INTERNAL_QUERY_ID_SUMMARIZE_HAZARD_VERSIONS
    )
    LIST_HISTORICAL_ENCOUNTERS = INTERNAL_QUERY_ID_LIST_ENCOUNTERS


RESULT_SHAPE_ENCOUNTER_SUMMARY = EncounterSummaryResult.__name__
RESULT_SHAPE_RISK_SUMMARY = RiskSummaryResult.__name__
RESULT_SHAPE_RISK_LEVEL_BUCKET = RiskLevelBucket.__name__
RESULT_SHAPE_HAZARD_VERSION_SUMMARY = HazardVersionSummaryResult.__name__
RESULT_SHAPE_ENCOUNTER_RECORD = HistoricalEncounterRecord.__name__

ENCOUNTER_SUMMARY_OUTPUT_COLUMNS = tuple(
    field.name for field in fields(EncounterSummaryResult)
)
RISK_SUMMARY_OUTPUT_COLUMNS = tuple(
    field.name
    for field in fields(RiskSummaryResult)
    if field.name != "risk_level_distribution"
)
RISK_LEVEL_DISTRIBUTION_OUTPUT_COLUMNS = tuple(
    field.name for field in fields(RiskLevelBucket)
)
HAZARD_VERSION_SUMMARY_OUTPUT_COLUMNS = tuple(
    field.name for field in fields(HazardVersionSummaryResult)
)
LIST_ENCOUNTER_OUTPUT_COLUMNS = tuple(
    field.name for field in fields(HistoricalEncounterRecord)
)

_TABLE_BY_DATASET = {
    Dataset.ENCOUNTER: "encounter",
    Dataset.RISK: "risk",
    Dataset.HAZARD_VERSION: "hazard_version",
}

_SCHEMA_BY_DATASET = {
    Dataset.ENCOUNTER: ENCOUNTER_FACT_SCHEMA_VERSION,
    Dataset.RISK: RISK_FACT_SCHEMA_VERSION,
    Dataset.HAZARD_VERSION: HAZARD_VERSION_FACT_SCHEMA_VERSION,
}

_LIST_ORDER_BY = (
    order_by_canonical_utc("event_time_utc", "ASC"),
    order_by_identifier("record_id", "ASC"),
    order_by_identifier("dedup_id", "ASC"),
)


@dataclass(frozen=True)
class QueryDefinition:
    """Code-owned query metadata. Contains no caller SQL."""

    query_id: InternalQueryId
    operation: HistoricalOperation
    dataset: Dataset
    table_name: str
    fact_schema_version: str
    output_columns: tuple[str, ...]
    result_shape: str
    public: bool


@dataclass(frozen=True)
class RenderedQuery:
    query_id: InternalQueryId
    operation: HistoricalOperation
    dataset: Dataset
    table_name: str
    fact_schema_version: str
    sql: str
    output_columns: tuple[str, ...]
    result_shape: str


@dataclass(frozen=True)
class RenderedOperation:
    """One domain operation. Risks render two fixed Athena statements."""

    operation: HistoricalOperation
    queries: tuple[RenderedQuery, ...]


def _definition(
    query_id: InternalQueryId,
    *,
    operation: HistoricalOperation,
    dataset: Dataset,
    output_columns: tuple[str, ...],
    result_shape: str,
    public: bool,
) -> QueryDefinition:
    return QueryDefinition(
        query_id=query_id,
        operation=operation,
        dataset=dataset,
        table_name=_TABLE_BY_DATASET[dataset],
        fact_schema_version=_SCHEMA_BY_DATASET[dataset],
        output_columns=output_columns,
        result_shape=result_shape,
        public=public,
    )


_QUERY_DEFINITIONS: Mapping[InternalQueryId, QueryDefinition] = MappingProxyType(
    {
        InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS: _definition(
            InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            dataset=Dataset.ENCOUNTER,
            output_columns=ENCOUNTER_SUMMARY_OUTPUT_COLUMNS,
            result_shape=RESULT_SHAPE_ENCOUNTER_SUMMARY,
            public=True,
        ),
        InternalQueryId.SUMMARIZE_HISTORICAL_RISKS: _definition(
            InternalQueryId.SUMMARIZE_HISTORICAL_RISKS,
            operation=HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS,
            dataset=Dataset.RISK,
            output_columns=RISK_SUMMARY_OUTPUT_COLUMNS,
            result_shape=RESULT_SHAPE_RISK_SUMMARY,
            public=True,
        ),
        InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL: _definition(
            InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL,
            operation=HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS,
            dataset=Dataset.RISK,
            output_columns=RISK_LEVEL_DISTRIBUTION_OUTPUT_COLUMNS,
            result_shape=RESULT_SHAPE_RISK_LEVEL_BUCKET,
            public=False,
        ),
        InternalQueryId.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS: _definition(
            InternalQueryId.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS,
            operation=HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS,
            dataset=Dataset.HAZARD_VERSION,
            output_columns=HAZARD_VERSION_SUMMARY_OUTPUT_COLUMNS,
            result_shape=RESULT_SHAPE_HAZARD_VERSION_SUMMARY,
            public=True,
        ),
        InternalQueryId.LIST_HISTORICAL_ENCOUNTERS: _definition(
            InternalQueryId.LIST_HISTORICAL_ENCOUNTERS,
            operation=HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
            dataset=Dataset.ENCOUNTER,
            output_columns=LIST_ENCOUNTER_OUTPUT_COLUMNS,
            result_shape=RESULT_SHAPE_ENCOUNTER_RECORD,
            public=True,
        ),
    }
)

_OPERATION_QUERY_IDS: Mapping[HistoricalOperation, tuple[InternalQueryId, ...]] = (
    MappingProxyType(
        {
            HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS: (
                InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            ),
            HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS: (
                InternalQueryId.SUMMARIZE_HISTORICAL_RISKS,
                InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL,
            ),
            HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS: (
                InternalQueryId.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS,
            ),
            HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS: (
                InternalQueryId.LIST_HISTORICAL_ENCOUNTERS,
            ),
        }
    )
)


def _window_predicates(definition: QueryDefinition, request: Any) -> tuple[str, ...]:
    return required_window_predicates(
        start_utc=request.start_utc,
        end_utc=request.end_utc,
        fact_schema_version=definition.fact_schema_version,
    )


def _optional_filters(*pairs: tuple[str, str | None]) -> tuple[str, ...]:
    predicates: list[str] = []
    for column, value in pairs:
        predicate = optional_equality_predicate(column, value)
        if predicate is not None:
            predicates.append(predicate)
    return tuple(predicates)


def _render_summarize_encounters(
    definition: QueryDefinition,
    request: SummarizeHistoricalEncountersRequest,
) -> str:
    if not isinstance(request, SummarizeHistoricalEncountersRequest):
        raise HistoricalQueryRenderError("invalid summarize encounters request")
    return render_select_statement(
        select_items=(
            count_star("physical_record_count"),
            count_distinct("encounter_id", "distinct_encounter_count"),
            count_distinct("aircraft_id", "distinct_aircraft_count"),
            count_distinct("hazard_id", "distinct_hazard_count"),
            count_distinct("dedup_id", "distinct_dedup_count"),
            min_by_canonical_utc("event_time_utc", "min_event_time_utc"),
            max_by_canonical_utc("event_time_utc", "max_event_time_utc"),
        ),
        table_name=definition.table_name,
        predicates=(
            *_window_predicates(definition, request),
            *_optional_filters(
                ("aircraft_id", request.aircraft_id),
                ("hazard_id", request.hazard_id),
                ("hazard_type", request.hazard_type),
            ),
        ),
    )


def _render_summarize_risks(
    definition: QueryDefinition,
    request: SummarizeHistoricalRisksRequest,
) -> str:
    if not isinstance(request, SummarizeHistoricalRisksRequest):
        raise HistoricalQueryRenderError("invalid summarize risks request")
    return render_select_statement(
        select_items=(
            count_star("physical_record_count"),
            count_distinct("risk_id", "distinct_risk_count"),
            count_distinct("encounter_id", "distinct_encounter_count"),
            count_distinct("aircraft_id", "distinct_aircraft_count"),
            min_column("risk_score", "min_risk_score"),
            max_column("risk_score", "max_risk_score"),
        ),
        table_name=definition.table_name,
        predicates=(
            *_window_predicates(definition, request),
            *_optional_filters(
                ("aircraft_id", request.aircraft_id),
                ("hazard_id", request.hazard_id),
                ("encounter_id", request.encounter_id),
                ("risk_level", request.risk_level),
            ),
        ),
    )


def _render_summarize_risks_by_level(
    definition: QueryDefinition,
    request: SummarizeHistoricalRisksRequest,
) -> str:
    if not isinstance(request, SummarizeHistoricalRisksRequest):
        raise HistoricalQueryRenderError("invalid summarize risks request")
    return render_select_statement(
        select_items=(
            select_column("risk_level"),
            count_star("physical_record_count"),
        ),
        table_name=definition.table_name,
        predicates=(
            *_window_predicates(definition, request),
            *_optional_filters(
                ("aircraft_id", request.aircraft_id),
                ("hazard_id", request.hazard_id),
                ("encounter_id", request.encounter_id),
                ("risk_level", request.risk_level),
            ),
        ),
        group_by=("risk_level",),
        order_by=(order_by_identifier("risk_level", "ASC"),),
    )


def _render_summarize_hazard_versions(
    definition: QueryDefinition,
    request: SummarizeHistoricalHazardVersionsRequest,
) -> str:
    if not isinstance(request, SummarizeHistoricalHazardVersionsRequest):
        raise HistoricalQueryRenderError(
            "invalid summarize hazard versions request"
        )
    return render_select_statement(
        select_items=(
            count_star("physical_record_count"),
            count_distinct("hazard_id", "distinct_hazard_count"),
            count_distinct("hazard_version_key", "distinct_hazard_version_count"),
            min_by_canonical_utc("materialized_at_utc", "min_materialized_at_utc"),
            max_by_canonical_utc("materialized_at_utc", "max_materialized_at_utc"),
        ),
        table_name=definition.table_name,
        predicates=(
            *_window_predicates(definition, request),
            *_optional_filters(
                ("hazard_id", request.hazard_id),
                ("hazard_type", request.hazard_type),
                ("product_type", request.product_type),
            ),
        ),
    )


def _render_list_encounters(
    definition: QueryDefinition,
    request: ListHistoricalEncountersRequest,
) -> str:
    if not isinstance(request, ListHistoricalEncountersRequest):
        raise HistoricalQueryRenderError("invalid list encounters request")
    if request.aircraft_id is None and request.hazard_id is None:
        raise HistoricalQueryRenderError(
            "list_historical_encounters requires aircraft_id or hazard_id"
        )
    if LIST_HISTORICAL_ENCOUNTERS_ORDERING != (
        "canonical_event_time_utc ASC",
        "record_id ASC",
        "dedup_id ASC",
    ):
        raise HistoricalQueryRenderError("list ordering drifted from contract")
    return render_select_statement(
        select_items=tuple(
            select_column(column) for column in definition.output_columns
        ),
        table_name=definition.table_name,
        predicates=(
            *_window_predicates(definition, request),
            *_optional_filters(
                ("aircraft_id", request.aircraft_id),
                ("hazard_id", request.hazard_id),
            ),
        ),
        order_by=_LIST_ORDER_BY,
        domain_limit=request.limit,
    )


_RENDERERS: Mapping[
    InternalQueryId, Callable[[QueryDefinition, Any], str]
] = MappingProxyType(
    {
        InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS: _render_summarize_encounters,
        InternalQueryId.SUMMARIZE_HISTORICAL_RISKS: _render_summarize_risks,
        InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL: (
            _render_summarize_risks_by_level
        ),
        InternalQueryId.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS: (
            _render_summarize_hazard_versions
        ),
        InternalQueryId.LIST_HISTORICAL_ENCOUNTERS: _render_list_encounters,
    }
)

_REQUEST_OPERATION = MappingProxyType(
    {
        SummarizeHistoricalEncountersRequest: (
            HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS
        ),
        SummarizeHistoricalRisksRequest: (
            HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS
        ),
        SummarizeHistoricalHazardVersionsRequest: (
            HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS
        ),
        ListHistoricalEncountersRequest: (
            HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS
        ),
    }
)


def get_query_definition(query_id: InternalQueryId | str) -> QueryDefinition:
    if isinstance(query_id, str):
        try:
            query_id = InternalQueryId(query_id)
        except ValueError as exc:
            raise HistoricalQueryRenderError("unknown historical query") from exc
    try:
        return _QUERY_DEFINITIONS[query_id]
    except KeyError as exc:
        raise HistoricalQueryRenderError("unknown historical query") from exc


def public_query_definitions() -> tuple[QueryDefinition, ...]:
    return tuple(
        definition
        for query_id in (
            InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            InternalQueryId.SUMMARIZE_HISTORICAL_RISKS,
            InternalQueryId.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS,
            InternalQueryId.LIST_HISTORICAL_ENCOUNTERS,
        )
        for definition in (_QUERY_DEFINITIONS[query_id],)
        if definition.public
    )


def public_query_ids() -> frozenset[str]:
    return frozenset(item.query_id.value for item in public_query_definitions())


def _render_one(query_id: InternalQueryId, request: Any) -> RenderedQuery:
    definition = _QUERY_DEFINITIONS[query_id]
    sql = _RENDERERS[query_id](definition, request)
    return RenderedQuery(
        query_id=definition.query_id,
        operation=definition.operation,
        dataset=definition.dataset,
        table_name=definition.table_name,
        fact_schema_version=definition.fact_schema_version,
        sql=sql,
        output_columns=definition.output_columns,
        result_shape=definition.result_shape,
    )


def render_historical_operation(request: Any) -> RenderedOperation:
    """Render the fixed SQL for one typed V1 request.

    The only input is a Phase 2B-preflight request object. There is no
    SQL, table, column, GROUP BY, ORDER BY, or LIMIT fragment parameter.
    """

    operation = _REQUEST_OPERATION.get(type(request))
    if operation is None or operation not in V1_HISTORICAL_OPERATIONS:
        raise HistoricalQueryRenderError("unsupported historical request")
    query_ids = _OPERATION_QUERY_IDS[operation]
    return RenderedOperation(
        operation=operation,
        queries=tuple(_render_one(query_id, request) for query_id in query_ids),
    )
