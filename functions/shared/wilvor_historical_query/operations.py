"""Deterministic V1 historical analytics operations.

Coverage remains the Phase 2A.1 evaluator's authority. Athena never
establishes completeness. ``evaluated_as_of_utc`` is the injected
``as_of_utc`` used for both coverage checks. This is not a response
generation timestamp.
"""

from __future__ import annotations

from collections.abc import Sequence
from types import MappingProxyType
from typing import Any

from wilvor_historical.contracts import Dataset
from wilvor_historical.coverage_contracts import Evaluability
from wilvor_historical.query_contracts import (
    COVERAGE_REASON_EPOCH_AMBIGUOUS,
    HAZARD_VERSION_WINDOW_LIMITATION,
    QUERY_RESULT_MALFORMED,
    CoverageEvidence,
    HistoricalOperation,
    HistoricalQueryError,
    HistoricalQueryErrorEvidence,
    HistoricalQueryResponse,
    HistoricalQueryStatus,
    ListHistoricalEncountersRequest,
    QueryEvidence,
    QueryExecutionEvidence,
    SummarizeHistoricalEncountersRequest,
    SummarizeHistoricalHazardVersionsRequest,
    SummarizeHistoricalRisksRequest,
    is_verified_zero,
    status_for_evaluable_result,
    status_for_unevaluable_coverage,
)
from wilvor_historical.time import HistoricalTimeError, canonicalize_utc_z, parse_utc_datetime

from .coverage_gate import CoverageGate, CoverageGateResult
from .coverage_store import CoverageStoreErrorCode
from .errors import AthenaExecutorError, AthenaExecutorErrorCode
from .executor import AthenaExecutor, AthenaQueryResult
from .query_registry import InternalQueryId
from .result_parsers import (
    ResultParseError,
    parse_encounter_summary,
    parse_hazard_version_summary,
    parse_list_encounters,
    parse_risk_summary,
)


OPERATION_DATASETS = MappingProxyType(
    {
        HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS: (Dataset.ENCOUNTER.value,),
        HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS: (Dataset.RISK.value,),
        HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS: (
            Dataset.HAZARD_VERSION.value,
        ),
        HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS: (Dataset.ENCOUNTER.value,),
    }
)

_STORE_UNAVAILABLE_REASONS = frozenset(
    {
        CoverageStoreErrorCode.COVERAGE_STORE_UNAVAILABLE.value,
        CoverageStoreErrorCode.COVERAGE_METADATA_MALFORMED.value,
        CoverageStoreErrorCode.COVERAGE_PAGINATION_MALFORMED.value,
        "COVERAGE_EVALUATOR_INCONSISTENT",
    }
)

_EXECUTOR_STATUS = {
    AthenaExecutorErrorCode.QUERY_FAILED: HistoricalQueryStatus.QUERY_FAILED,
    AthenaExecutorErrorCode.QUERY_CANCELED: HistoricalQueryStatus.QUERY_CANCELED,
    AthenaExecutorErrorCode.QUERY_TIMEOUT: HistoricalQueryStatus.QUERY_TIMEOUT,
}


class HistoricalAnalyticsOperations:
    """Four deterministic V1 historical operations. No AWS clients created here."""

    def __init__(
        self,
        *,
        coverage_gate: CoverageGate,
        executor: AthenaExecutor,
    ) -> None:
        if coverage_gate is None or executor is None:
            raise HistoricalQueryError("coverage_gate and executor are required")
        self._coverage_gate = coverage_gate
        self._executor = executor

    def summarize_historical_encounters(
        self,
        request: SummarizeHistoricalEncountersRequest,
        *,
        as_of_utc: str,
    ) -> HistoricalQueryResponse:
        return self._run_single(
            operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            request=request,
            as_of_utc=as_of_utc,
            query_id=InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            parse=lambda result: parse_encounter_summary(
                result,
                start_utc=request.start_utc,
                end_utc=request.end_utc,
            ),
        )

    def summarize_historical_risks(
        self,
        request: SummarizeHistoricalRisksRequest,
        *,
        as_of_utc: str,
    ) -> HistoricalQueryResponse:
        as_of, invalid = self._canonicalize_as_of(as_of_utc, request)
        if invalid is not None:
            return invalid
        pre = self._coverage_gate.evaluate(
            datasets=OPERATION_DATASETS[HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS],
            start_utc=request.start_utc,
            end_utc=request.end_utc,
            as_of_utc=as_of,
        )
        if not pre.allowed_to_query:
            return self._coverage_outcome(
                HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS,
                request,
                as_of,
                pre,
            )
        executions: list[QueryExecutionEvidence] = []
        try:
            aggregate = self._executor.execute_fixed(
                query_id=InternalQueryId.SUMMARIZE_HISTORICAL_RISKS,
                request=request,
            )
        except AthenaExecutorError as exc:
            return self._executor_outcome(
                HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS,
                request,
                as_of,
                pre,
                exc,
                executions,
            )
        executions.append(_execution_evidence(aggregate))
        try:
            distribution = self._executor.execute_fixed(
                query_id=InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL,
                request=request,
            )
        except AthenaExecutorError as exc:
            return self._executor_outcome(
                HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS,
                request,
                as_of,
                pre,
                exc,
                executions,
            )
        executions.append(_execution_evidence(distribution))
        try:
            parsed = parse_risk_summary(aggregate, distribution)
        except ResultParseError as exc:
            return self._parse_outcome(
                HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS,
                request,
                as_of,
                pre,
                exc,
                executions,
            )
        return self._certify(
            HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS,
            request,
            as_of,
            parsed,
            executions,
        )

    def summarize_historical_hazard_versions(
        self,
        request: SummarizeHistoricalHazardVersionsRequest,
        *,
        as_of_utc: str,
    ) -> HistoricalQueryResponse:
        return self._run_single(
            operation=HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS,
            request=request,
            as_of_utc=as_of_utc,
            query_id=InternalQueryId.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS,
            parse=lambda result: parse_hazard_version_summary(
                result,
                start_utc=request.start_utc,
                end_utc=request.end_utc,
            ),
        )

    def list_historical_encounters(
        self,
        request: ListHistoricalEncountersRequest,
        *,
        as_of_utc: str,
    ) -> HistoricalQueryResponse:
        return self._run_single(
            operation=HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
            request=request,
            as_of_utc=as_of_utc,
            query_id=InternalQueryId.LIST_HISTORICAL_ENCOUNTERS,
            parse=lambda result: parse_list_encounters(
                result,
                limit=request.limit,
                start_utc=request.start_utc,
                end_utc=request.end_utc,
                aircraft_id=request.aircraft_id,
                hazard_id=request.hazard_id,
            ),
        )

    def _run_single(
        self,
        *,
        operation: HistoricalOperation,
        request: Any,
        as_of_utc: str,
        query_id: InternalQueryId,
        parse: Any,
    ) -> HistoricalQueryResponse:
        as_of, invalid = self._canonicalize_as_of(as_of_utc, request, operation=operation)
        if invalid is not None:
            return invalid
        pre = self._coverage_gate.evaluate(
            datasets=OPERATION_DATASETS[operation],
            start_utc=request.start_utc,
            end_utc=request.end_utc,
            as_of_utc=as_of,
        )
        if not pre.allowed_to_query:
            return self._coverage_outcome(operation, request, as_of, pre)
        try:
            executed = self._executor.execute_fixed(query_id=query_id, request=request)
        except AthenaExecutorError as exc:
            return self._executor_outcome(operation, request, as_of, pre, exc, ())
        executions = (_execution_evidence(executed),)
        try:
            parsed = parse(executed)
        except ResultParseError as exc:
            return self._parse_outcome(operation, request, as_of, pre, exc, executions)
        return self._certify(operation, request, as_of, parsed, executions)

    def _canonicalize_as_of(
        self,
        as_of_utc: str,
        request: Any,
        *,
        operation: HistoricalOperation | None = None,
    ) -> tuple[str, HistoricalQueryResponse | None]:
        chosen = operation or _operation_for_request(request)
        if not isinstance(as_of_utc, str) or not as_of_utc.endswith("Z"):
            return "", self._invalid_request(chosen, request, "as_of_utc must be canonical UTC Z")
        try:
            return canonicalize_utc_z(parse_utc_datetime(as_of_utc)), None
        except HistoricalTimeError:
            return "", self._invalid_request(chosen, request, "invalid as_of_utc")

    def _certify(
        self,
        operation: HistoricalOperation,
        request: Any,
        as_of_utc: str,
        parsed: Any,
        executions: Sequence[QueryExecutionEvidence],
    ) -> HistoricalQueryResponse:
        post = self._coverage_gate.evaluate(
            datasets=OPERATION_DATASETS[operation],
            start_utc=request.start_utc,
            end_utc=request.end_utc,
            as_of_utc=as_of_utc,
        )
        if not post.allowed_to_query:
            return self._coverage_outcome(
                operation,
                request,
                as_of_utc,
                post,
                executions=executions,
            )
        if post.evaluability is not Evaluability.EVALUABLE:
            return self._coverage_outcome(
                operation,
                request,
                as_of_utc,
                post,
                executions=executions,
            )
        truncated = bool(getattr(parsed, "truncated", False))
        semantic = parsed.semantic_match_count
        status = status_for_evaluable_result(
            semantic_match_count=semantic,
            truncated=truncated,
        )
        if status is HistoricalQueryStatus.VERIFIED_ZERO:
            if not is_verified_zero(
                coverage=Evaluability.EVALUABLE,
                semantic_match_count=semantic,
            ):
                return self._parse_outcome(
                    operation,
                    request,
                    as_of_utc,
                    post,
                    ResultParseError("verified-zero invariant failed"),
                    executions,
                )
        return self._response(
            status=status,
            operation=operation,
            request=request,
            as_of_utc=as_of_utc,
            coverage=post.coverage,
            result=parsed,
            executions=executions,
            semantic_match_count=parsed.semantic_match_count,
            semantic_match_count_is_exact=parsed.semantic_match_count_is_exact,
            minimum_match_count=parsed.minimum_match_count,
        )

    def _coverage_outcome(
        self,
        operation: HistoricalOperation,
        request: Any,
        as_of_utc: str,
        gate: CoverageGateResult,
        executions: Sequence[QueryExecutionEvidence] = (),
    ) -> HistoricalQueryResponse:
        reason = gate.reason
        if reason in _STORE_UNAVAILABLE_REASONS:
            status = HistoricalQueryStatus.COVERAGE_STORE_UNAVAILABLE
        elif reason == CoverageStoreErrorCode.INVALID_REQUEST.value:
            status = HistoricalQueryStatus.INVALID_REQUEST
        else:
            status = status_for_unevaluable_coverage(reason=reason)
        return self._response(
            status=status,
            operation=operation,
            request=request,
            as_of_utc=as_of_utc,
            coverage=gate.coverage,
            executions=executions,
            error=HistoricalQueryErrorEvidence(code=reason, message=reason),
        )

    def _executor_outcome(
        self,
        operation: HistoricalOperation,
        request: Any,
        as_of_utc: str,
        gate: CoverageGateResult,
        error: AthenaExecutorError,
        executions: Sequence[QueryExecutionEvidence],
    ) -> HistoricalQueryResponse:
        status = _EXECUTOR_STATUS.get(error.code, HistoricalQueryStatus.QUERY_FAILED)
        return self._response(
            status=status,
            operation=operation,
            request=request,
            as_of_utc=as_of_utc,
            coverage=gate.coverage,
            executions=executions,
            error=HistoricalQueryErrorEvidence(
                code=error.code.value,
                message=str(error),
                query_id=error.query_id,
                query_execution_id=error.query_execution_id,
                athena_state=error.athena_state,
                athena_reason=error.athena_reason,
            ),
        )

    def _parse_outcome(
        self,
        operation: HistoricalOperation,
        request: Any,
        as_of_utc: str,
        gate: CoverageGateResult,
        error: ResultParseError,
        executions: Sequence[QueryExecutionEvidence],
    ) -> HistoricalQueryResponse:
        return self._response(
            status=HistoricalQueryStatus.QUERY_FAILED,
            operation=operation,
            request=request,
            as_of_utc=as_of_utc,
            coverage=gate.coverage,
            executions=executions,
            error=HistoricalQueryErrorEvidence(
                code=error.code,
                message=str(error),
            ),
        )

    def _invalid_request(
        self,
        operation: HistoricalOperation,
        request: Any,
        message: str,
    ) -> HistoricalQueryResponse:
        return HistoricalQueryResponse(
            status=HistoricalQueryStatus.INVALID_REQUEST,
            operation=operation,
            requested_scope=request,
            coverage=None,
            evidence=QueryEvidence(
                query_name=operation,
                datasets=OPERATION_DATASETS[operation],
                requested_start_utc=request.start_utc,
                requested_end_utc=request.end_utc,
                coverage_state=None,
                collection_epoch_ids=(),
            ),
            error=HistoricalQueryErrorEvidence(
                code=HistoricalQueryStatus.INVALID_REQUEST.value,
                message=message,
            ),
        )

    def _response(
        self,
        *,
        status: HistoricalQueryStatus,
        operation: HistoricalOperation,
        request: Any,
        as_of_utc: str,
        coverage: CoverageEvidence | None,
        executions: Sequence[QueryExecutionEvidence],
        result: Any = None,
        error: HistoricalQueryErrorEvidence | None = None,
        semantic_match_count: int | None = None,
        semantic_match_count_is_exact: bool | None = None,
        minimum_match_count: int | None = None,
    ) -> HistoricalQueryResponse:
        return HistoricalQueryResponse(
            status=status,
            operation=operation,
            requested_scope=request,
            coverage=coverage,
            result=result,
            limitations=_limitations(operation),
            error=error,
            evidence=QueryEvidence(
                query_name=operation,
                datasets=OPERATION_DATASETS[operation],
                requested_start_utc=request.start_utc,
                requested_end_utc=request.end_utc,
                coverage_state=None if coverage is None else coverage.evaluability,
                collection_epoch_ids=(
                    () if coverage is None else coverage.collection_epoch_ids
                ),
                semantic_match_count=semantic_match_count,
                semantic_match_count_is_exact=semantic_match_count_is_exact,
                minimum_match_count=minimum_match_count,
                query_executions=tuple(executions),
                evaluated_as_of_utc=as_of_utc,
            ),
        )


def _execution_evidence(result: AthenaQueryResult) -> QueryExecutionEvidence:
    return QueryExecutionEvidence(
        query_id=result.query_id,
        query_execution_id=result.query_execution_id,
        rows_returned=result.rows_returned,
        data_scanned_bytes=result.data_scanned_bytes,
        workgroup=result.workgroup,
    )


def _limitations(operation: HistoricalOperation) -> tuple[str, ...]:
    if operation is HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS:
        return (HAZARD_VERSION_WINDOW_LIMITATION,)
    return ()


def _operation_for_request(request: Any) -> HistoricalOperation:
    if isinstance(request, SummarizeHistoricalEncountersRequest):
        return HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS
    if isinstance(request, SummarizeHistoricalRisksRequest):
        return HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS
    if isinstance(request, SummarizeHistoricalHazardVersionsRequest):
        return HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS
    if isinstance(request, ListHistoricalEncountersRequest):
        return HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS
    raise HistoricalQueryError("unknown historical request")


# Imported by architecture tests so the four public entry points stay closed.
V1_OPERATION_METHODS = (
    "summarize_historical_encounters",
    "summarize_historical_risks",
    "summarize_historical_hazard_versions",
    "list_historical_encounters",
)
