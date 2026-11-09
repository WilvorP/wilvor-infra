"""Offline fake-gate / fake-executor tests for Phase 2B.4 operations."""

from __future__ import annotations

import inspect
from typing import Any

from wilvor_historical.coverage_contracts import Evaluability
from wilvor_historical.query_contracts import (
    COVERAGE_REASON_EPOCH_AMBIGUOUS,
    HAZARD_VERSION_WINDOW_LIMITATION,
    QUERY_RESULT_MALFORMED,
    CoverageEvidence,
    HistoricalOperation,
    HistoricalQueryStatus,
    ListHistoricalEncountersRequest,
    SummarizeHistoricalEncountersRequest,
    SummarizeHistoricalHazardVersionsRequest,
    SummarizeHistoricalRisksRequest,
)
from wilvor_historical_query.coverage_gate import CoverageGate, CoverageGateResult
from wilvor_historical_query.coverage_store import CoverageStoreErrorCode
from wilvor_historical_query.errors import AthenaExecutorError, AthenaExecutorErrorCode
from wilvor_historical_query.executor import AthenaExecutor, AthenaQueryResult
from wilvor_historical_query.operations import (
    V1_OPERATION_METHODS,
    HistoricalAnalyticsOperations,
)
from wilvor_historical_query.query_registry import InternalQueryId
from wilvor_historical_query import operations as operations_module
from wilvor_historical_query import result_parsers


AS_OF = "2026-09-13T12:00:00Z"
WINDOW = ("2026-09-11T10:00:00Z", "2026-09-11T11:00:00Z")


class FakeCoverageGate:
    def __init__(self, *results: CoverageGateResult) -> None:
        self._results = list(results)
        self.calls: list[dict[str, Any]] = []

    def evaluate(self, **kwargs: Any) -> CoverageGateResult:
        self.calls.append(kwargs)
        if not self._results:
            raise AssertionError("unexpected extra coverage evaluation")
        return self._results.pop(0)


class FakeExecutor:
    def __init__(
        self,
        results: list[AthenaQueryResult] | None = None,
        errors: list[AthenaExecutorError | None] | None = None,
    ) -> None:
        self._results = list(results or [])
        self._errors = list(errors or [])
        self.calls: list[dict[str, Any]] = []

    def execute_fixed(self, *, query_id: InternalQueryId, request: Any) -> AthenaQueryResult:
        self.calls.append({"query_id": query_id, "request": request})
        if self._errors:
            error = self._errors.pop(0)
            if error is not None:
                raise error
        return self._results.pop(0)


def _coverage(
    evaluability: Evaluability | None,
    reason: str,
    *,
    allowed: bool | None = None,
) -> CoverageGateResult:
    if allowed is None:
        allowed = evaluability is Evaluability.EVALUABLE
    return CoverageGateResult(
        allowed_to_query=allowed,
        coverage=CoverageEvidence(
            evaluability=evaluability,
            reason=reason,
            required_horizon_seconds=173760,
            required_streams=("facts",),
            collection_epoch_ids=("epoch-a",),
        ),
    )


def _athena(
    query_id: InternalQueryId,
    rows: tuple[dict[str, str | None], ...],
    *,
    execution_id: str = "exec-1",
) -> AthenaQueryResult:
    return AthenaQueryResult(
        query_id=query_id.value,
        query_execution_id=execution_id,
        workgroup="wilvor-historical",
        database="historical_facts",
        output_location="s3://bucket/athena-results/q",
        rows=rows,
        rows_returned=len(rows),
        data_scanned_bytes=16,
    )


def _encounter_row(physical: str = "2") -> dict[str, str | None]:
    zero = physical == "0"
    return {
        "physical_record_count": physical,
        "distinct_encounter_count": "0" if zero else "2",
        "distinct_aircraft_count": "0" if zero else "1",
        "distinct_hazard_count": "0" if zero else "1",
        "distinct_dedup_count": "0" if zero else "2",
        "min_event_time_utc": None if zero else "2026-09-11T10:00:00Z",
        "max_event_time_utc": None if zero else "2026-09-11T10:30:00Z",
    }


def _risk_row(physical: str = "2") -> dict[str, str | None]:
    zero = physical == "0"
    return {
        "physical_record_count": physical,
        "distinct_risk_count": "0" if zero else "2",
        "distinct_encounter_count": "0" if zero else "1",
        "distinct_aircraft_count": "0" if zero else "1",
        "min_risk_score": None if zero else "1",
        "max_risk_score": None if zero else "4",
    }


def _hazard_row(physical: str = "1") -> dict[str, str | None]:
    zero = physical == "0"
    return {
        "physical_record_count": physical,
        "distinct_hazard_count": "0" if zero else "1",
        "distinct_hazard_version_count": "0" if zero else "1",
        "min_materialized_at_utc": None if zero else "2026-09-11T10:00:00Z",
        "max_materialized_at_utc": None if zero else "2026-09-11T10:05:00Z",
    }


def _list_row(
    event_time: str = "2026-09-11T10:00:00Z",
    suffix: str = "v1",
) -> dict[str, str | None]:
    return {
        "encounter_id": f"proj-1#hazard-1#{suffix}",
        "record_id": f"proj-1#hazard-1#{suffix}",
        "dedup_id": f"proj-1#hazard-1#{suffix}|ENCOUNTER_OBSERVED|1700000000|DETECTED",
        "aircraft_id": "abc123",
        "hazard_id": "hazard-1",
        "hazard_version_key": f"hazard-1#{suffix}",
        "fact_kind": "ENCOUNTER_OBSERVED",
        "encounter_state": "DETECTED",
        "event_time_utc": event_time,
        "hazard_type": "CONVECTION",
    }


def _ops(gate: FakeCoverageGate, executor: FakeExecutor) -> HistoricalAnalyticsOperations:
    return HistoricalAnalyticsOperations(coverage_gate=gate, executor=executor)


def _encounter_request() -> SummarizeHistoricalEncountersRequest:
    return SummarizeHistoricalEncountersRequest(start_utc=WINDOW[0], end_utc=WINDOW[1])


def _risk_request() -> SummarizeHistoricalRisksRequest:
    return SummarizeHistoricalRisksRequest(start_utc=WINDOW[0], end_utc=WINDOW[1])


def _hazard_request() -> SummarizeHistoricalHazardVersionsRequest:
    return SummarizeHistoricalHazardVersionsRequest(start_utc=WINDOW[0], end_utc=WINDOW[1])


def _list_request(limit: int = 2) -> ListHistoricalEncountersRequest:
    return ListHistoricalEncountersRequest(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        aircraft_id="abc123",
        limit=limit,
    )


def test_pre_evaluable_nonzero_post_evaluable_is_succeeded():
    gate = FakeCoverageGate(
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
    )
    executor = FakeExecutor(
        [
            _athena(
                InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
                (_encounter_row(),),
            )
        ]
    )
    result = _ops(gate, executor).summarize_historical_encounters(
        _encounter_request(),
        as_of_utc=AS_OF,
    )
    assert result.status is HistoricalQueryStatus.SUCCEEDED
    assert result.result is not None
    assert result.result.physical_record_count == 2
    assert result.evidence.evaluated_as_of_utc == AS_OF
    assert not hasattr(result.evidence, "generated_at_utc")
    assert result.evidence.coverage_state is Evaluability.EVALUABLE
    assert len(result.evidence.query_executions) == 1
    assert len(gate.calls) == 2
    assert len(executor.calls) == 1


def test_pre_evaluable_semantic_zero_post_evaluable_is_verified_zero():
    gate = FakeCoverageGate(
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
    )
    executor = FakeExecutor(
        [
            _athena(
                InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
                (_encounter_row("0"),),
            )
        ]
    )
    result = _ops(gate, executor).summarize_historical_encounters(
        _encounter_request(),
        as_of_utc=AS_OF,
    )
    assert result.status is HistoricalQueryStatus.VERIFIED_ZERO
    assert result.result is not None
    assert result.result.physical_record_count == 0
    assert result.evidence.semantic_match_count == 0


def test_pre_gap_blocks_without_athena():
    gate = FakeCoverageGate(_coverage(Evaluability.GAP_OR_UNCERTAIN, "open_gap"))
    executor = FakeExecutor()
    result = _ops(gate, executor).summarize_historical_encounters(
        _encounter_request(),
        as_of_utc=AS_OF,
    )
    assert result.status is HistoricalQueryStatus.COVERAGE_BLOCKED
    assert result.result is None
    assert result.coverage is not None
    assert result.coverage.reason == "open_gap"
    assert result.evidence.evaluated_as_of_utc == AS_OF
    assert executor.calls == []
    assert len(gate.calls) == 1


def test_pre_evaluable_post_gap_discards_result():
    gate = FakeCoverageGate(
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
        _coverage(Evaluability.GAP_OR_UNCERTAIN, "late_gap"),
    )
    executor = FakeExecutor(
        [
            _athena(
                InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
                (_encounter_row(),),
            )
        ]
    )
    result = _ops(gate, executor).summarize_historical_encounters(
        _encounter_request(),
        as_of_utc=AS_OF,
    )
    assert result.status is HistoricalQueryStatus.COVERAGE_BLOCKED
    assert result.result is None
    assert result.coverage is not None
    assert result.coverage.reason == "late_gap"


def test_zero_query_post_not_yet_is_not_verified_zero():
    gate = FakeCoverageGate(
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
        _coverage(Evaluability.NOT_YET_EVALUABLE, "horizon_open"),
    )
    executor = FakeExecutor(
        [
            _athena(
                InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
                (_encounter_row("0"),),
            )
        ]
    )
    result = _ops(gate, executor).summarize_historical_encounters(
        _encounter_request(),
        as_of_utc=AS_OF,
    )
    assert result.status is HistoricalQueryStatus.COVERAGE_BLOCKED
    assert result.status is not HistoricalQueryStatus.VERIFIED_ZERO
    assert result.result is None


def test_post_epoch_ambiguous_blocks():
    gate = FakeCoverageGate(
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
        _coverage(None, COVERAGE_REASON_EPOCH_AMBIGUOUS, allowed=False),
    )
    executor = FakeExecutor(
        [
            _athena(
                InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
                (_encounter_row(),),
            )
        ]
    )
    result = _ops(gate, executor).summarize_historical_encounters(
        _encounter_request(),
        as_of_utc=AS_OF,
    )
    assert result.status is HistoricalQueryStatus.COVERAGE_BLOCKED
    assert result.coverage is not None
    assert result.coverage.evaluability is None
    assert result.coverage.reason == COVERAGE_REASON_EPOCH_AMBIGUOUS
    assert result.result is None


def test_post_store_failure_is_unavailable():
    gate = FakeCoverageGate(
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
        _coverage(None, CoverageStoreErrorCode.COVERAGE_STORE_UNAVAILABLE.value, allowed=False),
    )
    executor = FakeExecutor(
        [
            _athena(
                InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
                (_encounter_row(),),
            )
        ]
    )
    result = _ops(gate, executor).summarize_historical_encounters(
        _encounter_request(),
        as_of_utc=AS_OF,
    )
    assert result.status is HistoricalQueryStatus.COVERAGE_STORE_UNAVAILABLE
    assert result.result is None


def test_same_as_of_used_for_pre_and_post_and_not_cached():
    first = _coverage(Evaluability.EVALUABLE, "window_evaluable")
    second = _coverage(Evaluability.EVALUABLE, "window_evaluable")
    assert first is not second
    gate = FakeCoverageGate(first, second)
    executor = FakeExecutor(
        [
            _athena(
                InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
                (_encounter_row(),),
            )
        ]
    )
    result = _ops(gate, executor).summarize_historical_encounters(
        _encounter_request(),
        as_of_utc=AS_OF,
    )
    assert [call["as_of_utc"] for call in gate.calls] == [AS_OF, AS_OF]
    assert result.evidence.evaluated_as_of_utc == AS_OF
    assert AS_OF == gate.calls[0]["as_of_utc"] == gate.calls[1]["as_of_utc"] == result.evidence.evaluated_as_of_utc
    assert gate.calls[0]["start_utc"] == WINDOW[0]
    assert gate.calls[0]["end_utc"] == WINDOW[1]
    assert gate.calls[0]["datasets"] == ("encounter",)
    assert not hasattr(result.evidence, "generated_at_utc")


def test_pre_gate_failures_execute_zero_queries():
    cases = (
        _coverage(Evaluability.GAP_OR_UNCERTAIN, "open_gap"),
        _coverage(Evaluability.NOT_ACTIVE, "NOT_ACTIVE"),
        _coverage(Evaluability.NOT_YET_EVALUABLE, "horizon_open"),
        _coverage(None, COVERAGE_REASON_EPOCH_AMBIGUOUS, allowed=False),
        _coverage(None, CoverageStoreErrorCode.COVERAGE_STORE_UNAVAILABLE.value, allowed=False),
        _coverage(None, CoverageStoreErrorCode.COVERAGE_METADATA_MALFORMED.value, allowed=False),
    )
    expected = (
        HistoricalQueryStatus.COVERAGE_BLOCKED,
        HistoricalQueryStatus.COVERAGE_BLOCKED,
        HistoricalQueryStatus.COVERAGE_BLOCKED,
        HistoricalQueryStatus.COVERAGE_BLOCKED,
        HistoricalQueryStatus.COVERAGE_STORE_UNAVAILABLE,
        HistoricalQueryStatus.COVERAGE_STORE_UNAVAILABLE,
    )
    for gate_result, status in zip(cases, expected, strict=True):
        gate = FakeCoverageGate(gate_result)
        executor = FakeExecutor()
        result = _ops(gate, executor).summarize_historical_encounters(
            _encounter_request(),
            as_of_utc=AS_OF,
        )
        assert result.status is status
        assert result.result is None
        assert executor.calls == []


def test_encounter_query_evidence_and_parser_failure():
    gate = FakeCoverageGate(
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
    )
    executor = FakeExecutor(
        [
            _athena(
                InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
                (_encounter_row(),),
            )
        ]
    )
    result = _ops(gate, executor).summarize_historical_encounters(
        _encounter_request(),
        as_of_utc=AS_OF,
    )
    assert result.evidence.query_name is HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS
    assert result.evidence.query_executions[0].query_id == (
        InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS.value
    )
    assert result.evidence.query_executions[0].rows_returned == 1
    bad = FakeCoverageGate(_coverage(Evaluability.EVALUABLE, "window_evaluable"))
    failed = FakeExecutor(
        [
            _athena(
                InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
                (_encounter_row(), _encounter_row()),
            )
        ]
    )
    parsed = _ops(bad, failed).summarize_historical_encounters(
        _encounter_request(),
        as_of_utc=AS_OF,
    )
    assert parsed.status is HistoricalQueryStatus.QUERY_FAILED
    assert parsed.error is not None
    assert parsed.error.code == QUERY_RESULT_MALFORMED
    assert parsed.result is None
    assert len(bad.calls) == 1


def test_risk_two_query_success_and_zero():
    gate = FakeCoverageGate(
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
    )
    executor = FakeExecutor(
        [
            _athena(InternalQueryId.SUMMARIZE_HISTORICAL_RISKS, (_risk_row(),), execution_id="agg"),
            _athena(
                InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL,
                (
                    {"risk_level": "HIGH", "physical_record_count": "1"},
                    {"risk_level": "LOW", "physical_record_count": "1"},
                ),
                execution_id="dist",
            ),
        ]
    )
    result = _ops(gate, executor).summarize_historical_risks(_risk_request(), as_of_utc=AS_OF)
    assert result.status is HistoricalQueryStatus.SUCCEEDED
    assert result.result is not None
    assert len(result.evidence.query_executions) == 2
    assert [item.query_id for item in result.evidence.query_executions] == [
        InternalQueryId.SUMMARIZE_HISTORICAL_RISKS.value,
        InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL.value,
    ]
    assert [call["query_id"] for call in executor.calls] == [
        InternalQueryId.SUMMARIZE_HISTORICAL_RISKS,
        InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL,
    ]
    zero_gate = FakeCoverageGate(
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
    )
    zero_exec = FakeExecutor(
        [
            _athena(InternalQueryId.SUMMARIZE_HISTORICAL_RISKS, (_risk_row("0"),)),
            _athena(InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL, ()),
        ]
    )
    zero = _ops(zero_gate, zero_exec).summarize_historical_risks(
        _risk_request(),
        as_of_utc=AS_OF,
    )
    assert zero.status is HistoricalQueryStatus.VERIFIED_ZERO


def test_risk_first_query_failure_skips_second():
    gate = FakeCoverageGate(_coverage(Evaluability.EVALUABLE, "window_evaluable"))
    executor = FakeExecutor(
        errors=[
            AthenaExecutorError(
                AthenaExecutorErrorCode.QUERY_TIMEOUT,
                "Athena query timed out",
                query_id=InternalQueryId.SUMMARIZE_HISTORICAL_RISKS.value,
                query_execution_id="agg",
                athena_state="CANCELLED",
            )
        ]
    )
    result = _ops(gate, executor).summarize_historical_risks(_risk_request(), as_of_utc=AS_OF)
    assert result.status is HistoricalQueryStatus.QUERY_TIMEOUT
    assert result.result is None
    assert len(executor.calls) == 1
    assert result.error is not None
    assert result.error.query_execution_id == "agg"


def test_risk_second_query_failure_has_no_domain_result():
    gate = FakeCoverageGate(_coverage(Evaluability.EVALUABLE, "window_evaluable"))
    executor = FakeExecutor(
        results=[_athena(InternalQueryId.SUMMARIZE_HISTORICAL_RISKS, (_risk_row(),))],
        errors=[
            None,
            AthenaExecutorError(
                AthenaExecutorErrorCode.QUERY_FAILED,
                "Athena query failed",
                query_id=InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL.value,
                query_execution_id="dist",
                athena_state="FAILED",
                athena_reason="SYNTAX_ERROR",
            ),
        ],
    )
    result = _ops(gate, executor).summarize_historical_risks(_risk_request(), as_of_utc=AS_OF)
    assert result.status is HistoricalQueryStatus.QUERY_FAILED
    assert result.result is None
    assert len(result.evidence.query_executions) == 1
    assert result.evidence.query_executions[0].query_id == (
        InternalQueryId.SUMMARIZE_HISTORICAL_RISKS.value
    )
    assert result.error is not None
    assert result.error.query_id == InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL.value
    assert len(executor.calls) == 2


def test_risk_post_coverage_discards_assembled_result():
    gate = FakeCoverageGate(
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
        _coverage(Evaluability.GAP_OR_UNCERTAIN, "late_gap"),
    )
    executor = FakeExecutor(
        [
            _athena(InternalQueryId.SUMMARIZE_HISTORICAL_RISKS, (_risk_row(),)),
            _athena(
                InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL,
                (
                    {"risk_level": "HIGH", "physical_record_count": "1"},
                    {"risk_level": "LOW", "physical_record_count": "1"},
                ),
            ),
        ]
    )
    result = _ops(gate, executor).summarize_historical_risks(_risk_request(), as_of_utc=AS_OF)
    assert result.status is HistoricalQueryStatus.COVERAGE_BLOCKED
    assert result.result is None


def test_risk_inconsistent_distribution_is_query_failed():
    gate = FakeCoverageGate(_coverage(Evaluability.EVALUABLE, "window_evaluable"))
    executor = FakeExecutor(
        [
            _athena(InternalQueryId.SUMMARIZE_HISTORICAL_RISKS, (_risk_row(),)),
            _athena(
                InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL,
                ({"risk_level": "HIGH", "physical_record_count": "9"},),
            ),
        ]
    )
    result = _ops(gate, executor).summarize_historical_risks(_risk_request(), as_of_utc=AS_OF)
    assert result.status is HistoricalQueryStatus.QUERY_FAILED
    assert result.error is not None
    assert result.error.code == QUERY_RESULT_MALFORMED
    assert len(gate.calls) == 1


def test_hazard_summary_success_zero_and_limitation():
    gate = FakeCoverageGate(
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
    )
    executor = FakeExecutor(
        [_athena(InternalQueryId.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS, (_hazard_row(),))]
    )
    result = _ops(gate, executor).summarize_historical_hazard_versions(
        _hazard_request(),
        as_of_utc=AS_OF,
    )
    assert result.status is HistoricalQueryStatus.SUCCEEDED
    assert result.limitations == (HAZARD_VERSION_WINDOW_LIMITATION,)
    assert "valid_from" not in result.result.to_dict()
    zero_gate = FakeCoverageGate(
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
    )
    zero = _ops(
        zero_gate,
        FakeExecutor(
            [_athena(InternalQueryId.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS, (_hazard_row("0"),))]
        ),
    ).summarize_historical_hazard_versions(_hazard_request(), as_of_utc=AS_OF)
    assert zero.status is HistoricalQueryStatus.VERIFIED_ZERO
    assert zero.limitations == (HAZARD_VERSION_WINDOW_LIMITATION,)


def test_list_zero_exact_and_truncated():
    zero_gate = FakeCoverageGate(
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
    )
    zero = _ops(
        zero_gate,
        FakeExecutor([_athena(InternalQueryId.LIST_HISTORICAL_ENCOUNTERS, ())]),
    ).list_historical_encounters(_list_request(), as_of_utc=AS_OF)
    assert zero.status is HistoricalQueryStatus.VERIFIED_ZERO
    assert zero.result is not None
    assert zero.result.records == ()
    assert zero.evidence.semantic_match_count == 0
    assert zero.evidence.semantic_match_count_is_exact is True
    exact_gate = FakeCoverageGate(
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
    )
    exact = _ops(
        exact_gate,
        FakeExecutor(
            [
                _athena(
                    InternalQueryId.LIST_HISTORICAL_ENCOUNTERS,
                    (
                        _list_row(),
                        _list_row("2026-09-11T10:01:00Z", "v2"),
                    ),
                )
            ]
        ),
    ).list_historical_encounters(_list_request(limit=2), as_of_utc=AS_OF)
    assert exact.status is HistoricalQueryStatus.SUCCEEDED
    assert exact.evidence.semantic_match_count == 2
    assert exact.evidence.minimum_match_count is None
    truncated_gate = FakeCoverageGate(
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
    )
    truncated = _ops(
        truncated_gate,
        FakeExecutor(
            [
                _athena(
                    InternalQueryId.LIST_HISTORICAL_ENCOUNTERS,
                    (
                        _list_row(),
                        _list_row("2026-09-11T10:01:00Z", "v2"),
                        _list_row("2026-09-11T10:02:00Z", "v3"),
                    ),
                )
            ]
        ),
    ).list_historical_encounters(_list_request(limit=2), as_of_utc=AS_OF)
    assert truncated.status is HistoricalQueryStatus.RESULT_TRUNCATED
    assert truncated.result is not None
    assert len(truncated.result.records) == 2
    assert truncated.evidence.semantic_match_count is None
    assert truncated.evidence.semantic_match_count_is_exact is False
    assert truncated.evidence.minimum_match_count == 3
    assert len(truncated_gate.calls) == 2
    assert len(truncated_gate._results) == 0


def test_list_is_one_executor_call_without_current_state():
    gate = FakeCoverageGate(
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
        _coverage(Evaluability.EVALUABLE, "window_evaluable"),
    )
    executor = FakeExecutor(
        [_athena(InternalQueryId.LIST_HISTORICAL_ENCOUNTERS, (_list_row(),))]
    )
    result = _ops(gate, executor).list_historical_encounters(
        _list_request(limit=10),
        as_of_utc=AS_OF,
    )
    assert len(executor.calls) == 1
    record = result.result.records[0].to_dict()
    assert "inside_now" not in record
    assert "corridor_intersects" not in record
    assert "exact_intersection_confirmed" not in record
    assert "latitude" not in record
    assert "airport" not in record


def test_executor_integrity_failures_map_to_query_failed():
    gate = FakeCoverageGate(_coverage(Evaluability.EVALUABLE, "window_evaluable"))
    executor = FakeExecutor(
        errors=[
            AthenaExecutorError(
                AthenaExecutorErrorCode.WORKGROUP_MISMATCH,
                "workgroup mismatch",
                query_id=InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS.value,
            )
        ]
    )
    result = _ops(gate, executor).summarize_historical_encounters(
        _encounter_request(),
        as_of_utc=AS_OF,
    )
    assert result.status is HistoricalQueryStatus.QUERY_FAILED
    assert result.error is not None
    assert result.error.code == AthenaExecutorErrorCode.WORKGROUP_MISMATCH.value
    canceled = _ops(
        FakeCoverageGate(_coverage(Evaluability.EVALUABLE, "window_evaluable")),
        FakeExecutor(
            errors=[
                AthenaExecutorError(
                    AthenaExecutorErrorCode.QUERY_CANCELED,
                    "Athena query cancelled",
                    query_id=InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS.value,
                    athena_state="CANCELLED",
                )
            ]
        ),
    ).summarize_historical_encounters(_encounter_request(), as_of_utc=AS_OF)
    assert canceled.status is HistoricalQueryStatus.QUERY_CANCELED


def test_out_of_window_parse_does_not_post_certify():
    gate = FakeCoverageGate(_coverage(Evaluability.EVALUABLE, "window_evaluable"))
    executor = FakeExecutor(
        [
            _athena(
                InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
                (
                    _encounter_row()
                    | {
                        "min_event_time_utc": "2026-09-11T09:00:00Z",
                        "max_event_time_utc": "2026-09-11T10:30:00Z",
                    },
                ),
            )
        ]
    )
    result = _ops(gate, executor).summarize_historical_encounters(
        _encounter_request(),
        as_of_utc=AS_OF,
    )
    assert result.status is HistoricalQueryStatus.QUERY_FAILED
    assert result.result is None
    assert result.error is not None
    assert result.error.code == QUERY_RESULT_MALFORMED
    assert result.evidence.evaluated_as_of_utc == AS_OF
    assert len(gate.calls) == 1


def test_invalid_as_of_does_not_query():
    executor = FakeExecutor()
    result = _ops(FakeCoverageGate(), executor).summarize_historical_encounters(
        _encounter_request(),
        as_of_utc="2026-09-13T12:00:00+00:00",
    )
    assert result.status is HistoricalQueryStatus.INVALID_REQUEST
    assert result.result is None
    assert result.evidence.evaluated_as_of_utc is None
    assert executor.calls == []


def test_operations_have_exactly_four_entry_points_and_no_clock_or_sql():
    assert V1_OPERATION_METHODS == (
        "summarize_historical_encounters",
        "summarize_historical_risks",
        "summarize_historical_hazard_versions",
        "list_historical_encounters",
    )
    public = [
        name
        for name in dir(HistoricalAnalyticsOperations)
        if not name.startswith("_") and callable(getattr(HistoricalAnalyticsOperations, name))
    ]
    assert set(public) == set(V1_OPERATION_METHODS)
    source = inspect.getsource(operations_module)
    assert "datetime.now" not in source
    assert "datetime.utcnow" not in source
    assert "time.time" not in source
    assert "boto3" not in source
    assert "render_fixed_query" not in source
    assert "_execute_rendered" not in source
    assert "execute_sql" not in source
    assert "evaluate_collection_window" not in source
    assert "AthenaExecutor" in source
    assert "CoverageGate" in source
    assert "wilvor_ai" not in source
    assert "is_verified_zero" in source
    assert "generated_at_utc" not in source
    assert "evaluated_as_of_utc" in source
    parser_source = inspect.getsource(result_parsers)
    assert "datetime.now" not in parser_source
    assert "time.time" not in parser_source
    assert "inside_now" not in parser_source
    assert inspect.signature(AthenaExecutor.execute_fixed)
    assert inspect.signature(CoverageGate.evaluate)
