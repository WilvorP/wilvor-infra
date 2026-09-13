"""Map certified historical query responses onto Phase 0 ToolResult.

This module transforms an already-returned ``HistoricalQueryResponse``.
It does not execute operations, render SQL, or call Athena, S3, coverage,
or DynamoDB. Domain status remains authoritative unless the certified
status lacks the first-class proof fields required to support it.
"""

from __future__ import annotations

from wilvor_ai.contracts import (
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
from wilvor_historical.coverage_contracts import Evaluability
from wilvor_historical.query_contracts import (
    HistoricalOperation,
    HistoricalQueryResponse,
    HistoricalQueryStatus,
    ListEncountersResult,
)


HISTORICAL_FRESHNESS_NOT_ESTABLISHED = "HISTORICAL_FRESHNESS_NOT_ESTABLISHED"
MAPPING_INTEGRITY_FAILED = "HISTORICAL_MAPPING_INCOHERENT"
RESULT_TRUNCATED_LIMITATION = "RESULT_TRUNCATED"

SOURCE_BY_DATASET = {
    "encounter": "wilvor.historical.encounter_fact",
    "risk": "wilvor.historical.risk_fact",
    "hazard_version": "wilvor.historical.hazard_version_fact",
}

_STATUS_MAP = {
    HistoricalQueryStatus.SUCCEEDED: ToolResultStatus.SUCCESS,
    HistoricalQueryStatus.VERIFIED_ZERO: ToolResultStatus.NOT_FOUND,
    HistoricalQueryStatus.RESULT_TRUNCATED: ToolResultStatus.PARTIAL,
    HistoricalQueryStatus.COVERAGE_BLOCKED: ToolResultStatus.UNAVAILABLE,
    HistoricalQueryStatus.COVERAGE_STORE_UNAVAILABLE: ToolResultStatus.UNAVAILABLE,
    HistoricalQueryStatus.QUERY_FAILED: ToolResultStatus.UNAVAILABLE,
    HistoricalQueryStatus.QUERY_CANCELED: ToolResultStatus.UNAVAILABLE,
    HistoricalQueryStatus.QUERY_TIMEOUT: ToolResultStatus.UNAVAILABLE,
    HistoricalQueryStatus.INVALID_REQUEST: ToolResultStatus.UNKNOWN,
}

_CERTIFIED_STATUSES = frozenset(
    {
        HistoricalQueryStatus.SUCCEEDED,
        HistoricalQueryStatus.VERIFIED_ZERO,
        HistoricalQueryStatus.RESULT_TRUNCATED,
    }
)


def map_historical_query_response(
    response: HistoricalQueryResponse,
    *,
    tool_call_id: str,
) -> ToolResult:
    """Translate a domain historical response into a Phase 0 ToolResult.

    ``tool_call_id`` is trusted mapper input, not a model-visible argument.
    ``correlation_id`` stays None until a later Agent API exists.
    """

    if not isinstance(response, HistoricalQueryResponse):
        raise TypeError("response must be a HistoricalQueryResponse")

    coherent = _certified_status_is_coherent(response)
    if coherent:
        status = _STATUS_MAP[response.status]
        confidence = _confidence_for(response.status)
        error_code = None if response.error is None else response.error.code
    else:
        status = ToolResultStatus.UNAVAILABLE
        confidence = ConfidenceLevel.UNKNOWN
        error_code = MAPPING_INTEGRITY_FAILED

    as_of_utc = response.evidence.evaluated_as_of_utc
    limitations = _tool_limitations(response, status=status, coherent=coherent)
    evidence = _build_evidence(
        response,
        tool_call_id=tool_call_id,
        as_of_utc=as_of_utc,
        confidence=confidence,
        error_code=error_code,
    )
    if status in {
        ToolResultStatus.SUCCESS,
        ToolResultStatus.PARTIAL,
    } and not evidence:
        status = ToolResultStatus.UNAVAILABLE
        confidence = ConfidenceLevel.UNKNOWN
        error_code = MAPPING_INTEGRITY_FAILED
        limitations = _unique(
            limitations + (MAPPING_INTEGRITY_FAILED, status.value)
        )

    return ToolResult(
        tool_name=response.operation.value,
        tool_call_id=tool_call_id,
        status=status,
        temporal_scope=TemporalScope.HISTORICAL,
        data=_application_payload(response),
        evidence=evidence,
        as_of_utc=as_of_utc,
        limitations=limitations,
        correlation_id=None,
    )


def _certified_status_is_coherent(response: HistoricalQueryResponse) -> bool:
    if response.status not in _CERTIFIED_STATUSES:
        return True
    if response.evidence.evaluated_as_of_utc is None:
        return False
    if not _coverage_fields_agree(response):
        return False
    evaluability = _evaluability(response)
    evidence = response.evidence
    if response.status is HistoricalQueryStatus.VERIFIED_ZERO:
        return (
            evaluability is Evaluability.EVALUABLE
            and evidence.semantic_match_count == 0
            and evidence.semantic_match_count_is_exact is True
        )
    if response.status is HistoricalQueryStatus.RESULT_TRUNCATED:
        minimum = evidence.minimum_match_count
        return (
            evidence.semantic_match_count is None
            and evidence.semantic_match_count_is_exact is False
            and isinstance(minimum, int)
            and not isinstance(minimum, bool)
            and minimum >= 0
        )
    return evaluability is Evaluability.EVALUABLE


def _coverage_fields_agree(response: HistoricalQueryResponse) -> bool:
    if response.coverage is None or response.evidence.coverage_state is None:
        return True
    return response.coverage.evaluability is response.evidence.coverage_state


def _evaluability(response: HistoricalQueryResponse) -> Evaluability | None:
    if response.coverage is not None:
        return response.coverage.evaluability
    return response.evidence.coverage_state


def _confidence_for(status: HistoricalQueryStatus) -> ConfidenceLevel:
    if status in {
        HistoricalQueryStatus.SUCCEEDED,
        HistoricalQueryStatus.VERIFIED_ZERO,
    }:
        return ConfidenceLevel.HIGH
    if status is HistoricalQueryStatus.RESULT_TRUNCATED:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.UNKNOWN


def _tool_limitations(
    response: HistoricalQueryResponse,
    *,
    status: ToolResultStatus,
    coherent: bool,
) -> tuple[str, ...]:
    items = list(response.limitations)
    if status is ToolResultStatus.PARTIAL:
        items.append(RESULT_TRUNCATED_LIMITATION)
    if not coherent:
        items.append(MAPPING_INTEGRITY_FAILED)
    if status in {ToolResultStatus.UNAVAILABLE, ToolResultStatus.UNKNOWN}:
        if response.error is not None:
            items.append(response.error.code)
        elif response.coverage is not None:
            items.append(response.coverage.reason)
        elif not items:
            items.append(status.value)
    return _unique(tuple(items))


def _build_evidence(
    response: HistoricalQueryResponse,
    *,
    tool_call_id: str,
    as_of_utc: str | None,
    confidence: ConfidenceLevel,
    error_code: str | None,
) -> tuple[Evidence, ...]:
    if as_of_utc is None:
        return ()
    source = _source_for(response)
    return (
        Evidence(
            source=source,
            source_records=_source_records(response),
            query_timestamp_utc=as_of_utc,
            freshness_status=FreshnessStatus.UNKNOWN,
            confidence=confidence,
            limitations=(HISTORICAL_FRESHNESS_NOT_ESTABLISHED,),
            tool_call_id=tool_call_id,
            temporal_scope=TemporalScope.HISTORICAL,
            completeness=_completeness(response, as_of_utc=as_of_utc),
            match_cardinality=_match_cardinality(response),
            query_executions=_query_executions(response),
            error_code=error_code,
        ),
    )


def _source_for(response: HistoricalQueryResponse) -> str:
    datasets = response.evidence.datasets
    if len(datasets) != 1 or datasets[0] not in SOURCE_BY_DATASET:
        raise TypeError("historical evidence must name exactly one known dataset")
    return SOURCE_BY_DATASET[datasets[0]]


def _completeness(
    response: HistoricalQueryResponse,
    *,
    as_of_utc: str,
) -> SourceCompleteness:
    coverage = response.coverage
    evaluability = _evaluability(response)
    reason = None
    epoch_ids: tuple[str, ...] = response.evidence.collection_epoch_ids
    if coverage is not None:
        reason = coverage.reason
        epoch_ids = coverage.collection_epoch_ids
    return SourceCompleteness(
        status=None if evaluability is None else evaluability.value,
        reason=reason,
        epoch_ids=epoch_ids,
        evaluated_as_of_utc=as_of_utc,
    )


def _match_cardinality(response: HistoricalQueryResponse) -> MatchCardinality | None:
    evidence = response.evidence
    if (
        evidence.semantic_match_count is None
        and evidence.semantic_match_count_is_exact is None
        and evidence.minimum_match_count is None
    ):
        return None
    if evidence.semantic_match_count_is_exact is True:
        return MatchCardinality(
            exact_count=evidence.semantic_match_count,
            is_exact=True,
            minimum_count=None,
        )
    if evidence.semantic_match_count_is_exact is False:
        return MatchCardinality(
            exact_count=None,
            is_exact=False,
            minimum_count=evidence.minimum_match_count,
        )
    return MatchCardinality()


def _query_executions(
    response: HistoricalQueryResponse,
) -> tuple[QueryExecutionTrace, ...]:
    return tuple(
        QueryExecutionTrace(
            query_id=item.query_id,
            execution_id=item.query_execution_id,
            rows_returned=item.rows_returned,
            bytes_scanned=item.data_scanned_bytes,
            engine_scope=item.workgroup,
        )
        for item in response.evidence.query_executions
    )


def _source_records(response: HistoricalQueryResponse) -> tuple[SourceRecord, ...]:
    if response.operation is not HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS:
        return ()
    result = response.result
    if not isinstance(result, ListEncountersResult):
        return ()
    return tuple(
        SourceRecord(
            record_id=item.record_id,
            source_version=None,
            event_timestamp_utc=item.event_time_utc,
        )
        for item in result.records
    )


def _application_payload(response: HistoricalQueryResponse) -> dict[str, object]:
    error = None
    if response.error is not None:
        error = {
            "code": response.error.code,
            "message": response.error.message,
        }
    return {
        "status": response.status.value,
        "operation": response.operation.value,
        "requested_scope": response.requested_scope.to_dict(),
        "result": None if response.result is None else response.result.to_dict(),
        "limitations": list(response.limitations),
        "error": error,
    }


def _unique(items: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))
