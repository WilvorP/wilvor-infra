"""Phase 3A.3 deterministic historical verification and rendering tests."""

from __future__ import annotations

import ast
import inspect
from dataclasses import fields, replace
from pathlib import Path

from wilvor_ai.contracts import (
    SourceRecord,
    TemporalScope,
)
from wilvor_ai.historical_analytics_mapping import (
    INVALID_REQUEST_CODE,
    RESULT_TRUNCATED_LIMITATION,
    map_historical_invalid_request,
    map_historical_query_response,
)
from wilvor_ai.historical_answer_renderer import (
    finalize_historical_specialist_run,
    render_historical_specialist_result,
)
from wilvor_ai.historical_evidence_verifier import (
    ALL_EXACT_METRICS,
    CLAIM_SET_NOT_ACTIONABLE,
    CLAIM_VERIFICATION_FAILED,
    ClaimRejectionCode,
    EXACT_METRICS_BY_OPERATION,
    LOWER_BOUND_METRIC_ID,
    HistoricalVerificationResult,
    verify_historical_specialist_run,
)
from wilvor_ai.model_contracts import ModelDecisionKind
from wilvor_ai.specialist_contracts import (
    ExactCountClaim,
    HistoricalWindowClaim,
    LimitationClaim,
    LowerBoundCountClaim,
    RecordIdentityClaim,
    SpecialistResult,
    SpecialistStatus,
    UnavailableClaim,
    UnsupportedReason,
    VerifiedZeroClaim,
    VerifierOutcome,
)
from wilvor_ai.specialist_runtime_contracts import (
    HistoricalSpecialistRunResult,
    SpecialistRunStatus,
    SpecialistRuntimeErrorCode,
)
from wilvor_historical.coverage_contracts import Evaluability
from wilvor_historical.query_contracts import (
    COVERAGE_REASON_EPOCH_AMBIGUOUS,
    HAZARD_VERSION_WINDOW_LIMITATION,
    INTERNAL_QUERY_ID_LIST_ENCOUNTERS,
    INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,
    INTERNAL_QUERY_ID_SUMMARIZE_HAZARD_VERSIONS,
    CoverageEvidence,
    EncounterSummaryResult,
    HazardVersionSummaryResult,
    HistoricalEncounterRecord,
    HistoricalOperation,
    HistoricalQueryErrorEvidence,
    HistoricalQueryResponse,
    HistoricalQueryStatus,
    ListEncountersResult,
    ListHistoricalEncountersRequest,
    QueryEvidence,
    QueryExecutionEvidence,
    SummarizeHistoricalEncountersRequest,
    SummarizeHistoricalHazardVersionsRequest,
)


AS_OF = "2026-09-13T12:00:00Z"
OTHER_AS_OF = "2026-09-14T00:00:00Z"
WINDOW = ("2026-09-11T10:00:00Z", "2026-09-11T11:00:00Z")
CALL_A = "historical-call-1"
CALL_B = "historical-call-2"
REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_SOURCE = (
    REPO_ROOT / "functions" / "shared" / "wilvor_ai" / "historical_evidence_verifier.py"
)
RENDERER_SOURCE = (
    REPO_ROOT / "functions" / "shared" / "wilvor_ai" / "historical_answer_renderer.py"
)


def _coverage(evaluability=Evaluability.EVALUABLE, reason="window_evaluable"):
    return CoverageEvidence(
        evaluability=evaluability,
        reason=reason,
        required_horizon_seconds=173760,
        required_streams=("facts",),
        collection_epoch_ids=("epoch-1",),
    )


def _execution(query_id: str = INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS):
    return QueryExecutionEvidence(
        query_id=query_id,
        query_execution_id="exec-verify-1",
        rows_returned=2,
        data_scanned_bytes=64,
        workgroup="historical-analytics",
    )


def _query_evidence(
    operation,
    *,
    as_of=AS_OF,
    count=2,
    exact=True,
    minimum=None,
    coverage_state=Evaluability.EVALUABLE,
    query_id=INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,
    datasets=("encounter",),
):
    return QueryEvidence(
        query_name=operation,
        datasets=datasets,
        requested_start_utc=WINDOW[0],
        requested_end_utc=WINDOW[1],
        coverage_state=coverage_state,
        collection_epoch_ids=("epoch-1",),
        semantic_match_count=count,
        semantic_match_count_is_exact=exact,
        minimum_match_count=minimum,
        query_executions=(_execution(query_id),),
        evaluated_as_of_utc=as_of,
    )


def _encounter_summary(count: int = 2) -> EncounterSummaryResult:
    if count == 0:
        return EncounterSummaryResult(
            physical_record_count=0,
            distinct_encounter_count=0,
            distinct_aircraft_count=0,
            distinct_hazard_count=0,
            distinct_dedup_count=0,
        )
    return EncounterSummaryResult(
        physical_record_count=count,
        distinct_encounter_count=count,
        distinct_aircraft_count=1,
        distinct_hazard_count=1,
        distinct_dedup_count=count,
        min_event_time_utc="2026-09-11T10:00:00Z",
        max_event_time_utc="2026-09-11T10:30:00Z",
    )


def _record(index: int) -> HistoricalEncounterRecord:
    suffix = str(index)
    return HistoricalEncounterRecord(
        encounter_id=f"ver-{suffix}#hazard-1#v1",
        record_id=f"ver-{suffix}#hazard-1#v1",
        dedup_id=f"ver-{suffix}#hazard-1#v1|ENCOUNTER_OBSERVED|1700000000|DETECTED",
        aircraft_id="abc123",
        hazard_id="hazard-1",
        hazard_version_key="hazard-1#v1",
        fact_kind="ENCOUNTER_OBSERVED",
        encounter_state="DETECTED",
        event_time_utc=f"2026-09-11T10:{index:02d}:00Z",
        hazard_type="CONVECTION",
    )


def _map(response, tool_call_id=CALL_A):
    return map_historical_query_response(response, tool_call_id=tool_call_id)


def _succeeded_encounters(tool_call_id=CALL_A, count=2):
    return _map(
        HistoricalQueryResponse(
            status=HistoricalQueryStatus.SUCCEEDED,
            operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            requested_scope=SummarizeHistoricalEncountersRequest(
                start_utc=WINDOW[0],
                end_utc=WINDOW[1],
            ),
            coverage=_coverage(),
            evidence=_query_evidence(
                HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
                count=count,
            ),
            result=_encounter_summary(count),
        ),
        tool_call_id,
    )


def _verified_zero(tool_call_id=CALL_A):
    return _map(
        HistoricalQueryResponse(
            status=HistoricalQueryStatus.VERIFIED_ZERO,
            operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            requested_scope=SummarizeHistoricalEncountersRequest(
                start_utc=WINDOW[0],
                end_utc=WINDOW[1],
            ),
            coverage=_coverage(),
            evidence=_query_evidence(
                HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
                count=0,
                exact=True,
            ),
            result=_encounter_summary(0),
        ),
        tool_call_id,
    )


def _truncated_list(minimum=6, tool_call_id=CALL_A):
    return _map(
        HistoricalQueryResponse(
            status=HistoricalQueryStatus.RESULT_TRUNCATED,
            operation=HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
            requested_scope=ListHistoricalEncountersRequest(
                start_utc=WINDOW[0],
                end_utc=WINDOW[1],
                aircraft_id="abc123",
                limit=1,
            ),
            coverage=_coverage(),
            evidence=_query_evidence(
                HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
                count=None,
                exact=False,
                minimum=minimum,
                query_id=INTERNAL_QUERY_ID_LIST_ENCOUNTERS,
            ),
            result=ListEncountersResult(records=(_record(1),), truncated=True),
        ),
        tool_call_id,
    )


def _succeeded_list(tool_call_id=CALL_A, count=2):
    records = tuple(_record(index + 1) for index in range(count))
    return _map(
        HistoricalQueryResponse(
            status=HistoricalQueryStatus.SUCCEEDED,
            operation=HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
            requested_scope=ListHistoricalEncountersRequest(
                start_utc=WINDOW[0],
                end_utc=WINDOW[1],
                aircraft_id="abc123",
                limit=count,
            ),
            coverage=_coverage(),
            evidence=_query_evidence(
                HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
                count=count,
                query_id=INTERNAL_QUERY_ID_LIST_ENCOUNTERS,
            ),
            result=ListEncountersResult(records=records),
        ),
        tool_call_id,
    )


def _hazard_versions(tool_call_id=CALL_A):
    return _map(
        HistoricalQueryResponse(
            status=HistoricalQueryStatus.SUCCEEDED,
            operation=HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS,
            requested_scope=SummarizeHistoricalHazardVersionsRequest(
                start_utc=WINDOW[0],
                end_utc=WINDOW[1],
            ),
            coverage=_coverage(),
            evidence=_query_evidence(
                HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS,
                count=2,
                query_id=INTERNAL_QUERY_ID_SUMMARIZE_HAZARD_VERSIONS,
                datasets=("hazard_version",),
            ),
            result=HazardVersionSummaryResult(
                physical_record_count=2,
                distinct_hazard_count=1,
                distinct_hazard_version_count=2,
                min_materialized_at_utc="2026-09-11T10:00:00Z",
                max_materialized_at_utc="2026-09-11T10:30:00Z",
            ),
            limitations=(HAZARD_VERSION_WINDOW_LIMITATION,),
        ),
        tool_call_id,
    )


def _coverage_blocked(tool_call_id=CALL_A):
    return _map(
        HistoricalQueryResponse(
            status=HistoricalQueryStatus.COVERAGE_BLOCKED,
            operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            requested_scope=SummarizeHistoricalEncountersRequest(
                start_utc=WINDOW[0],
                end_utc=WINDOW[1],
            ),
            coverage=_coverage(None, COVERAGE_REASON_EPOCH_AMBIGUOUS),
            evidence=_query_evidence(
                HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
                coverage_state=None,
                count=None,
                exact=None,
            ),
            error=HistoricalQueryErrorEvidence(
                code=COVERAGE_REASON_EPOCH_AMBIGUOUS,
                message=COVERAGE_REASON_EPOCH_AMBIGUOUS,
            ),
        ),
        tool_call_id,
    )


def _invalid_request(tool_call_id=CALL_A):
    return map_historical_invalid_request(
        tool_name="list_historical_encounters",
        tool_call_id=tool_call_id,
        attempted_scope={"start_utc": WINDOW[0], "end_utc": WINDOW[1]},
    )


def _window(tool_call_id=CALL_A):
    return HistoricalWindowClaim(
        tool_call_id=tool_call_id,
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
    )


def _exact(value=2, metric_id="distinct_encounter_count", tool_call_id=CALL_A):
    return ExactCountClaim(
        tool_call_id=tool_call_id,
        metric_id=metric_id,
        value=value,
    )


def _run(
    *,
    tool_results,
    claims,
    status=SpecialistRunStatus.PROPOSED_CLAIMS,
    evaluated_as_of_utc=AS_OF,
    unsupported_reason=None,
    runtime_errors=(),
    terminal_kind=ModelDecisionKind.FINAL_CLAIMS,
):
    return HistoricalSpecialistRunResult(
        status=status,
        tool_results=tuple(tool_results),
        tool_result_projections=(),
        executed_invocations=(),
        proposed_claims=tuple(claims),
        unsupported_reason=unsupported_reason,
        evaluated_as_of_utc=evaluated_as_of_utc,
        runtime_errors=runtime_errors,
        provider_turn_count=2,
        executed_tool_call_count=len(tuple(tool_results)),
        validation_feedback=(),
        terminal_kind=terminal_kind,
    )


def _finalize(**kwargs):
    return finalize_historical_specialist_run(_run(**kwargs))


def test_metric_catalog_uses_actual_result_fields_only():
    assert "distinct_version_count" not in ALL_EXACT_METRICS
    assert "rows_returned" not in ALL_EXACT_METRICS
    assert "bytes_scanned" not in ALL_EXACT_METRICS
    assert EXACT_METRICS_BY_OPERATION["summarize_historical_hazard_versions"] == {
        "physical_record_count",
        "distinct_hazard_count",
        "distinct_hazard_version_count",
    }
    assert "distinct_aircraft_count" not in EXACT_METRICS_BY_OPERATION[
        "summarize_historical_hazard_versions"
    ]


def test_exact_count_and_window_success():
    tool = _succeeded_encounters()
    result = _finalize(tool_results=(tool,), claims=(_exact(), _window()))
    assert result.verifier_outcome is VerifierOutcome.PASSED
    assert result.status is SpecialistStatus.ANSWERED
    assert result.temporal_scope.value == "HISTORICAL"
    assert result.evaluated_as_of_utc == AS_OF
    assert result.tool_results == (tool,)
    assert result.verified_claims[0] == _exact()
    assert "exact count of 2 for distinct encounters" in result.answer
    assert WINDOW[0] in result.answer
    assert result.tool_results[0].evidence[0].query_executions
    failed = _finalize(tool_results=(tool,), claims=(_exact(value=99), _window()))
    assert failed.verifier_outcome is VerifierOutcome.FAILED
    assert failed.status is SpecialistStatus.UNAVAILABLE
    assert failed.verified_claims == ()
    assert CLAIM_VERIFICATION_FAILED in failed.limitations
    assert "exact count of 99" not in failed.answer
    assert failed.answer.startswith("Wilvor could not verify")


def test_unknown_and_wrong_operation_metrics_fail():
    tool = _succeeded_encounters()
    unknown = _finalize(
        tool_results=(tool,),
        claims=(_exact(metric_id="rows_returned"),),
    )
    assert unknown.verifier_outcome is VerifierOutcome.FAILED
    verification = verify_historical_specialist_run(
        _run(tool_results=(tool,), claims=(_exact(metric_id="rows_returned"),))
    )
    assert ClaimRejectionCode.UNKNOWN_METRIC in verification.rejected_claim_codes
    hazard = _hazard_versions()
    wrong = verify_historical_specialist_run(
        _run(
            tool_results=(hazard,),
            claims=(_exact(metric_id="distinct_aircraft_count", value=1),),
        )
    )
    assert ClaimRejectionCode.METRIC_OPERATION_MISMATCH in wrong.rejected_claim_codes
    assert wrong.outcome is VerifierOutcome.FAILED


def test_verified_zero_success_and_invalid_variants():
    zero = _verified_zero()
    result = _finalize(tool_results=(zero,), claims=(VerifiedZeroClaim(CALL_A),))
    assert result.status is SpecialistStatus.ANSWERED
    assert result.verifier_outcome is VerifierOutcome.PASSED
    assert "No matching historical records were found" in result.answer
    assert "evaluable" in result.answer
    assert "currently" not in result.answer.lower()
    assert "no aircraft were affected" not in result.answer

    blocked = _coverage_blocked()
    assert (
        verify_historical_specialist_run(
            _run(tool_results=(blocked,), claims=(VerifiedZeroClaim(CALL_A),))
        ).outcome
        is VerifierOutcome.FAILED
    )
    success = _succeeded_encounters()
    assert (
        verify_historical_specialist_run(
            _run(tool_results=(success,), claims=(VerifiedZeroClaim(CALL_A),))
        ).rejected_claim_codes
        == (ClaimRejectionCode.VERIFIED_ZERO_PROOF_FAILED,)
    )
    nonzero_card = replace(
        zero,
        evidence=(
            replace(
                zero.evidence[0],
                match_cardinality=replace(
                    zero.evidence[0].match_cardinality,
                    exact_count=2,
                ),
            ),
        ),
    )
    assert (
        verify_historical_specialist_run(
            _run(tool_results=(nonzero_card,), claims=(VerifiedZeroClaim(CALL_A),))
        ).outcome
        is VerifierOutcome.FAILED
    )


def test_partial_lower_bound_is_mandatory_and_not_row_count():
    tool = _truncated_list(minimum=6)
    result = _finalize(
        tool_results=(tool,),
        claims=(
            LowerBoundCountClaim(
                tool_call_id=CALL_A,
                metric_id=LOWER_BOUND_METRIC_ID,
                minimum_value=6,
            ),
        ),
    )
    assert result.status is SpecialistStatus.PARTIAL
    assert result.verifier_outcome is VerifierOutcome.PASSED
    assert "at least 6" in result.answer
    assert "incomplete/truncated" in result.answer
    assert RESULT_TRUNCATED_LIMITATION in result.limitations
    assert "exact count" not in result.answer
    wrong = verify_historical_specialist_run(
        _run(
            tool_results=(tool,),
            claims=(
                LowerBoundCountClaim(
                    tool_call_id=CALL_A,
                    metric_id=LOWER_BOUND_METRIC_ID,
                    minimum_value=1,
                ),
            ),
        )
    )
    assert ClaimRejectionCode.LOWER_BOUND_VALUE_MISMATCH in wrong.rejected_claim_codes
    assert tool.evidence[0].query_executions[0].rows_returned == 2


def test_record_identity_rules():
    listed = _succeeded_list(count=2)
    first_id = listed.data["result"]["records"][0]["record_id"]
    ok = _finalize(
        tool_results=(listed,),
        claims=(RecordIdentityClaim(tool_call_id=CALL_A, record_id=first_id),),
    )
    assert ok.verifier_outcome is VerifierOutcome.PASSED
    assert first_id in ok.answer
    missing = verify_historical_specialist_run(
        _run(
            tool_results=(listed,),
            claims=(RecordIdentityClaim(tool_call_id=CALL_A, record_id="missing-id"),),
        )
    )
    assert ClaimRejectionCode.RECORD_IDENTITY_NOT_FOUND in missing.rejected_claim_codes
    summary = _succeeded_encounters()
    from_summary = verify_historical_specialist_run(
        _run(
            tool_results=(summary,),
            claims=(RecordIdentityClaim(tool_call_id=CALL_A, record_id=first_id),),
        )
    )
    assert (
        ClaimRejectionCode.RECORD_IDENTITY_UNSUPPORTED_OPERATION
        in from_summary.rejected_claim_codes
    )
    partial = _truncated_list()
    returned = partial.data["result"]["records"][0]["record_id"]
    partial_ok = _finalize(
        tool_results=(partial,),
        claims=(RecordIdentityClaim(tool_call_id=CALL_A, record_id=returned),),
    )
    assert partial_ok.status is SpecialistStatus.PARTIAL
    assert RESULT_TRUNCATED_LIMITATION in partial_ok.limitations
    assert "incomplete/truncated" in partial_ok.answer


def test_historical_window_uses_requested_scope_not_event_times():
    tool = _succeeded_encounters()
    assert (
        verify_historical_specialist_run(
            _run(tool_results=(tool,), claims=(_exact(), _window()))
        ).outcome
        is VerifierOutcome.PASSED
    )
    wrong = verify_historical_specialist_run(
        _run(
            tool_results=(tool,),
            claims=(
                _exact(),
                HistoricalWindowClaim(
                    tool_call_id=CALL_A,
                    start_utc="2026-09-01T00:00:00Z",
                    end_utc=WINDOW[1],
                ),
            ),
        )
    )
    assert ClaimRejectionCode.WINDOW_MISMATCH in wrong.rejected_claim_codes
    observed = verify_historical_specialist_run(
        _run(
            tool_results=(tool,),
            claims=(
                _exact(),
                HistoricalWindowClaim(
                    tool_call_id=CALL_A,
                    start_utc=tool.data["result"]["min_event_time_utc"],
                    end_utc=tool.data["result"]["max_event_time_utc"],
                ),
            ),
        )
    )
    assert ClaimRejectionCode.WINDOW_MISMATCH in observed.rejected_claim_codes


def test_hazard_materialization_limitation_is_mandatory():
    tool = _hazard_versions()
    result = _finalize(
        tool_results=(tool,),
        claims=(_exact(metric_id="distinct_hazard_version_count", value=2),),
    )
    assert result.verifier_outcome is VerifierOutcome.PASSED
    assert HAZARD_VERSION_WINDOW_LIMITATION in result.limitations
    assert "materialization/event time" in result.answer
    assert "valid during the window" not in result.answer.lower()
    assert "validity-interval overlap" in result.answer


def test_unavailable_claim_and_exact_count_against_blocked():
    blocked = _coverage_blocked()
    ok = _finalize(
        tool_results=(blocked,),
        claims=(
            UnavailableClaim(
                tool_call_id=CALL_A,
                error_or_coverage_code=COVERAGE_REASON_EPOCH_AMBIGUOUS,
            ),
        ),
        evaluated_as_of_utc=blocked.as_of_utc,
    )
    assert ok.verifier_outcome is VerifierOutcome.PASSED
    assert ok.status is SpecialistStatus.UNAVAILABLE
    assert "no matching historical records" not in ok.answer.lower()
    assert "cannot certify" in ok.answer
    assert "coverage unavailable" in ok.answer.lower()
    failed = verify_historical_specialist_run(
        _run(
            tool_results=(blocked,),
            claims=(_exact(),),
            evaluated_as_of_utc=blocked.as_of_utc,
        )
    )
    assert failed.outcome is VerifierOutcome.FAILED


def test_unknown_tool_call_id_does_not_resolve_by_value():
    tool = _succeeded_encounters()
    verification = verify_historical_specialist_run(
        _run(
            tool_results=(tool,),
            claims=(_exact(tool_call_id="missing-call"),),
        )
    )
    assert verification.outcome is VerifierOutcome.FAILED
    assert ClaimRejectionCode.UNKNOWN_TOOL_CALL_ID in verification.rejected_claim_codes


def test_duplicates_and_contradictions_fail_atomically():
    tool = _succeeded_encounters()
    listed = _succeeded_list()
    record_id = listed.data["result"]["records"][0]["record_id"]
    cases = [
        (_run(tool_results=(tool,), claims=(_exact(), _exact())),),
        (
            _run(
                tool_results=(tool,),
                claims=(VerifiedZeroClaim(CALL_A), _exact(value=2)),
            ),
        ),
        (
            _run(
                tool_results=(listed,),
                claims=(
                    VerifiedZeroClaim(CALL_A),
                    RecordIdentityClaim(tool_call_id=CALL_A, record_id=record_id),
                ),
            ),
        ),
        (
            _run(
                tool_results=(_truncated_list(),),
                claims=(
                    _exact(metric_id="physical_record_count", value=1),
                    LowerBoundCountClaim(
                        tool_call_id=CALL_A,
                        metric_id=LOWER_BOUND_METRIC_ID,
                        minimum_value=6,
                    ),
                ),
            ),
        ),
    ]
    for (run,) in cases:
        result = finalize_historical_specialist_run(run)
        assert result.verifier_outcome is VerifierOutcome.FAILED
        assert result.verified_claims == ()
        assert result.status is SpecialistStatus.UNAVAILABLE


def test_non_actionable_window_or_limitation_only():
    tool = _succeeded_encounters()
    window_only = _finalize(tool_results=(tool,), claims=(_window(),))
    assert window_only.verifier_outcome is VerifierOutcome.FAILED
    assert CLAIM_SET_NOT_ACTIONABLE in window_only.limitations
    assert window_only.status is SpecialistStatus.UNAVAILABLE
    assert "exact count" not in window_only.answer
    limitation_only = _finalize(
        tool_results=(_truncated_list(),),
        claims=(
            LimitationClaim(
                tool_call_id=CALL_A,
                limitation_code=RESULT_TRUNCATED_LIMITATION,
            ),
        ),
    )
    assert limitation_only.verifier_outcome is VerifierOutcome.FAILED
    assert CLAIM_SET_NOT_ACTIONABLE in limitation_only.limitations


def test_terminal_states_do_not_run_verifier():
    unsupported = finalize_historical_specialist_run(
        _run(
            tool_results=(),
            claims=(),
            status=SpecialistRunStatus.UNSUPPORTED,
            evaluated_as_of_utc=None,
            unsupported_reason=UnsupportedReason.GEOGRAPHY,
            terminal_kind=ModelDecisionKind.UNSUPPORTED,
        )
    )
    assert unsupported.verifier_outcome is VerifierOutcome.NOT_RUN
    assert unsupported.status is SpecialistStatus.UNSUPPORTED
    assert unsupported.unsupported_reason is UnsupportedReason.GEOGRAPHY
    assert "geographic impact" in unsupported.answer
    assert unsupported.verified_claims == ()

    invalid = finalize_historical_specialist_run(
        _run(
            tool_results=(),
            claims=(),
            status=SpecialistRunStatus.INVALID_REQUEST,
            evaluated_as_of_utc=None,
            terminal_kind=ModelDecisionKind.TOOL_CALLS,
        )
    )
    assert invalid.status is SpecialistStatus.INVALID_REQUEST
    assert invalid.verifier_outcome is VerifierOutcome.NOT_RUN

    unavailable = finalize_historical_specialist_run(
        _run(
            tool_results=(),
            claims=(),
            status=SpecialistRunStatus.UNAVAILABLE,
            evaluated_as_of_utc=None,
            runtime_errors=(SpecialistRuntimeErrorCode.AS_OF_MISMATCH.value,),
            terminal_kind=ModelDecisionKind.TOOL_CALLS,
        )
    )
    assert unavailable.status is SpecialistStatus.UNAVAILABLE
    assert unavailable.verifier_outcome is VerifierOutcome.NOT_RUN
    assert "cannot certify" in unavailable.answer

    provider = finalize_historical_specialist_run(
        _run(
            tool_results=(),
            claims=(),
            status=SpecialistRunStatus.PROVIDER_FAILED,
            evaluated_as_of_utc=None,
            runtime_errors=(SpecialistRuntimeErrorCode.REFUSAL.value,),
            terminal_kind=ModelDecisionKind.REFUSAL,
        )
    )
    assert provider.status is SpecialistStatus.PROVIDER_FAILED
    assert provider.verifier_outcome is VerifierOutcome.NOT_RUN
    assert "provider" in provider.answer.lower()


def test_provider_failure_after_evaluated_tool_does_not_answer_from_it():
    tool = _succeeded_encounters()
    result = finalize_historical_specialist_run(
        _run(
            tool_results=(tool,),
            claims=(),
            status=SpecialistRunStatus.PROVIDER_FAILED,
            runtime_errors=(SpecialistRuntimeErrorCode.PROVIDER_EXCEPTION.value,),
            terminal_kind=None,
        )
    )
    assert result.status is SpecialistStatus.PROVIDER_FAILED
    assert result.evaluated_as_of_utc == AS_OF
    assert result.tool_results == (tool,)
    assert result.verified_claims == ()
    assert "exact count" not in result.answer


def test_used_versus_unused_tool_results():
    invalid = _invalid_request(CALL_A)
    success = _succeeded_encounters(CALL_B)
    result = _finalize(
        tool_results=(invalid, success),
        claims=(_exact(tool_call_id=CALL_B),),
    )
    assert result.status is SpecialistStatus.ANSWERED
    assert result.tool_results == (invalid, success)
    assert result.status is not SpecialistStatus.INVALID_REQUEST
    global_fail = finalize_historical_specialist_run(
        _run(
            tool_results=(success,),
            claims=(),
            status=SpecialistRunStatus.UNAVAILABLE,
            runtime_errors=(
                SpecialistRuntimeErrorCode.PROJECTION_INTEGRITY_FAILED.value,
            ),
        )
    )
    assert global_fail.status is SpecialistStatus.UNAVAILABLE
    assert global_fail.verifier_outcome is VerifierOutcome.NOT_RUN


def test_multiple_successful_tools_and_malformed_as_of():
    first = _succeeded_encounters(CALL_A)
    second = _succeeded_encounters(CALL_B)
    result = _finalize(
        tool_results=(first, second),
        claims=(
            _exact(tool_call_id=CALL_A),
            _exact(tool_call_id=CALL_B, metric_id="distinct_aircraft_count", value=1),
        ),
    )
    assert result.status is SpecialistStatus.ANSWERED
    assert result.evaluated_as_of_utc == AS_OF
    assert first.as_of_utc == AS_OF
    assert second.as_of_utc == AS_OF
    assert len(result.verified_claims) == 2
    mismatched = _finalize(
        tool_results=(first, replace(second, as_of_utc=OTHER_AS_OF)),
        claims=(
            _exact(tool_call_id=CALL_A),
            _exact(tool_call_id=CALL_B, metric_id="distinct_aircraft_count", value=1),
        ),
    )
    assert mismatched.verifier_outcome is VerifierOutcome.FAILED
    assert mismatched.status is SpecialistStatus.UNAVAILABLE
    run_differs = _finalize(
        tool_results=(first, second),
        claims=(
            _exact(tool_call_id=CALL_A),
            _exact(tool_call_id=CALL_B, metric_id="distinct_aircraft_count", value=1),
        ),
        evaluated_as_of_utc=OTHER_AS_OF,
    )
    assert run_differs.verifier_outcome is VerifierOutcome.FAILED
    assert ClaimRejectionCode.RUN_STATE_MALFORMED in verify_historical_specialist_run(
        _run(
            tool_results=(first, second),
            claims=(
                _exact(tool_call_id=CALL_A),
                _exact(tool_call_id=CALL_B, metric_id="distinct_aircraft_count", value=1),
            ),
            evaluated_as_of_utc=OTHER_AS_OF,
        )
    ).rejected_claim_codes
    missing_run_as_of = verify_historical_specialist_run(
        _run(
            tool_results=(first,),
            claims=(_exact(),),
            evaluated_as_of_utc=None,
        )
    )
    assert missing_run_as_of.outcome is VerifierOutcome.FAILED
    assert ClaimRejectionCode.RUN_STATE_MALFORMED in missing_run_as_of.rejected_claim_codes


def test_rendering_is_deterministic_and_round_trips():
    tool = _succeeded_encounters()
    run = _run(tool_results=(tool,), claims=(_exact(), _window()))
    first = finalize_historical_specialist_run(run)
    second = finalize_historical_specialist_run(run)
    assert first.to_dict() == second.to_dict()
    assert SpecialistResult.from_dict(first.to_dict()) == first
    assert "claim_text" not in first.to_dict()
    names = {item.name for item in fields(first)}
    assert names.isdisjoint({"candidate_answer", "claim_text", "factual_text"})


def test_serialization_round_trips_all_final_statuses():
    samples = [
        _finalize(tool_results=(_succeeded_encounters(),), claims=(_exact(),)),
        _finalize(
            tool_results=(_truncated_list(),),
            claims=(
                LowerBoundCountClaim(
                    tool_call_id=CALL_A,
                    metric_id=LOWER_BOUND_METRIC_ID,
                    minimum_value=6,
                ),
            ),
        ),
        finalize_historical_specialist_run(
            _run(
                tool_results=(),
                claims=(),
                status=SpecialistRunStatus.UNSUPPORTED,
                evaluated_as_of_utc=None,
                unsupported_reason=UnsupportedReason.FORECAST,
                terminal_kind=ModelDecisionKind.UNSUPPORTED,
            )
        ),
        finalize_historical_specialist_run(
            _run(
                tool_results=(),
                claims=(),
                status=SpecialistRunStatus.UNAVAILABLE,
                evaluated_as_of_utc=None,
                runtime_errors=("as_of_mismatch",),
            )
        ),
        finalize_historical_specialist_run(
            _run(
                tool_results=(),
                claims=(),
                status=SpecialistRunStatus.INVALID_REQUEST,
                evaluated_as_of_utc=None,
            )
        ),
        finalize_historical_specialist_run(
            _run(
                tool_results=(),
                claims=(),
                status=SpecialistRunStatus.PROVIDER_FAILED,
                evaluated_as_of_utc=None,
                runtime_errors=("refusal",),
            )
        ),
        _finalize(tool_results=(_succeeded_encounters(),), claims=(_exact(value=9),)),
    ]
    expected = [
        SpecialistStatus.ANSWERED,
        SpecialistStatus.PARTIAL,
        SpecialistStatus.UNSUPPORTED,
        SpecialistStatus.UNAVAILABLE,
        SpecialistStatus.INVALID_REQUEST,
        SpecialistStatus.PROVIDER_FAILED,
        SpecialistStatus.UNAVAILABLE,
    ]
    for sample, status in zip(samples, expected, strict=True):
        assert sample.status is status
        assert SpecialistResult.from_dict(sample.to_dict()) == sample


def test_verification_result_round_trip_and_no_provider_in_finalizer():
    run = _run(tool_results=(_succeeded_encounters(),), claims=(_exact(),))
    verification = verify_historical_specialist_run(run)
    assert HistoricalVerificationResult.from_dict(verification.to_dict()) == verification
    source = RENDERER_SOURCE.read_text(encoding="utf-8")
    assert "ModelProvider" not in source
    assert ".complete(" not in source
    assert "boto3" not in source
    assert "datetime.now" not in source
    signature = inspect.signature(finalize_historical_specialist_run)
    assert list(signature.parameters) == ["run_result"]
    verifier = VERIFIER_SOURCE.read_text(encoding="utf-8")
    assert "ToolResultProjection" not in verifier
    assert "claim_text" not in verifier
    assert "AthenaExecutor" not in verifier
    tree = ast.parse(source)
    called = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }
    assert "complete" not in called


def test_fact_bearing_claims_require_historical_evaluable_evidence():
    success = _succeeded_encounters()
    missing = replace(
        success,
        evidence=(replace(success.evidence[0], completeness=None),),
    )
    missing_result = verify_historical_specialist_run(
        _run(tool_results=(missing,), claims=(_exact(),))
    )
    assert missing_result.outcome is VerifierOutcome.FAILED
    assert ClaimRejectionCode.COMPLETENESS_NOT_EVALUABLE in missing_result.rejected_claim_codes

    not_evaluable = replace(
        success,
        evidence=(
            replace(
                success.evidence[0],
                completeness=replace(success.evidence[0].completeness, status=None),
            ),
        ),
    )
    not_eval_result = verify_historical_specialist_run(
        _run(tool_results=(not_evaluable,), claims=(_exact(),))
    )
    assert not_eval_result.outcome is VerifierOutcome.FAILED
    assert (
        ClaimRejectionCode.COMPLETENESS_NOT_EVALUABLE
        in not_eval_result.rejected_claim_codes
    )

    current = replace(
        success,
        temporal_scope=TemporalScope.CURRENT,
        evidence=(
            replace(success.evidence[0], temporal_scope=TemporalScope.CURRENT),
        ),
    )
    current_result = verify_historical_specialist_run(
        _run(tool_results=(current,), claims=(_exact(),))
    )
    assert current_result.outcome is VerifierOutcome.FAILED
    assert ClaimRejectionCode.NON_HISTORICAL_TOOL in current_result.rejected_claim_codes

    partial = _truncated_list()
    partial_incomplete = replace(
        partial,
        evidence=(replace(partial.evidence[0], completeness=None),),
    )
    lower = LowerBoundCountClaim(
        tool_call_id=CALL_A,
        metric_id=LOWER_BOUND_METRIC_ID,
        minimum_value=6,
    )
    lower_result = verify_historical_specialist_run(
        _run(tool_results=(partial_incomplete,), claims=(lower,))
    )
    assert lower_result.outcome is VerifierOutcome.FAILED
    assert ClaimRejectionCode.COMPLETENESS_NOT_EVALUABLE in lower_result.rejected_claim_codes

    listed = _succeeded_list()
    record_id = listed.data["result"]["records"][0]["record_id"]
    identity = RecordIdentityClaim(tool_call_id=CALL_A, record_id=record_id)
    assert (
        verify_historical_specialist_run(
            _run(tool_results=(listed,), claims=(identity,))
        ).outcome
        is VerifierOutcome.PASSED
    )
    assert (
        verify_historical_specialist_run(
            _run(
                tool_results=(_truncated_list(),),
                claims=(
                    RecordIdentityClaim(
                        tool_call_id=CALL_A,
                        record_id=_truncated_list().data["result"]["records"][0][
                            "record_id"
                        ],
                    ),
                ),
            )
        ).outcome
        is VerifierOutcome.PASSED
    )

    blocked = _map(
        HistoricalQueryResponse(
            status=HistoricalQueryStatus.COVERAGE_BLOCKED,
            operation=HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
            requested_scope=ListHistoricalEncountersRequest(
                start_utc=WINDOW[0],
                end_utc=WINDOW[1],
                aircraft_id="abc123",
                limit=20,
            ),
            coverage=_coverage(None, COVERAGE_REASON_EPOCH_AMBIGUOUS),
            evidence=_query_evidence(
                HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
                coverage_state=None,
                count=None,
                exact=None,
                query_id=INTERNAL_QUERY_ID_LIST_ENCOUNTERS,
            ),
            error=HistoricalQueryErrorEvidence(
                code=COVERAGE_REASON_EPOCH_AMBIGUOUS,
                message=COVERAGE_REASON_EPOCH_AMBIGUOUS,
            ),
        )
    )
    fabricated_id = "ver-9#hazard-1#v1"
    data = dict(blocked.data)
    data["result"] = {
        "records": [
            {"record_id": fabricated_id, "event_time_utc": "2026-09-11T10:09:00Z"}
        ]
    }
    fabricated = replace(
        blocked,
        data=data,
        evidence=(
            replace(
                blocked.evidence[0],
                source_records=(
                    SourceRecord(
                        record_id=fabricated_id,
                        source_version=None,
                        event_timestamp_utc="2026-09-11T10:09:00Z",
                    ),
                ),
            ),
        ),
    )
    fabricated_result = verify_historical_specialist_run(
        _run(
            tool_results=(fabricated,),
            claims=(RecordIdentityClaim(tool_call_id=CALL_A, record_id=fabricated_id),),
            evaluated_as_of_utc=fabricated.as_of_utc,
        )
    )
    assert fabricated_result.outcome is VerifierOutcome.FAILED
    assert (
        ClaimRejectionCode.RECORD_IDENTITY_UNSUPPORTED_OPERATION
        in fabricated_result.rejected_claim_codes
    )


def test_unavailable_claim_rejects_unknown_invalid_request():
    invalid = _invalid_request()
    verification = verify_historical_specialist_run(
        _run(
            tool_results=(invalid,),
            claims=(
                UnavailableClaim(
                    tool_call_id=CALL_A,
                    error_or_coverage_code=INVALID_REQUEST_CODE,
                ),
            ),
            evaluated_as_of_utc=None,
        )
    )
    assert invalid.status.value == "UNKNOWN"
    assert invalid.data["error"]["code"] == INVALID_REQUEST_CODE
    assert INVALID_REQUEST_CODE in invalid.limitations
    assert verification.outcome is VerifierOutcome.FAILED
    assert (
        ClaimRejectionCode.UNAVAILABLE_STATUS_UNSUPPORTED
        in verification.rejected_claim_codes
    )


def test_finalizer_never_mentions_current_state_or_validity_overlap_as_fact():
    result = _finalize(
        tool_results=(_hazard_versions(),),
        claims=(_exact(metric_id="physical_record_count", value=2),),
    )
    lowered = result.answer.lower()
    assert "currently" not in lowered
    assert "california" not in lowered
    assert "hazards valid during" not in lowered
    assert inspect.signature(render_historical_specialist_result).parameters[
        "run_result"
    ]
