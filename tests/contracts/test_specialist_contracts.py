"""Phase 3A-preflight specialist contract tests. No specialist runtime."""

from __future__ import annotations

from dataclasses import fields

import pytest

from wilvor_ai import (
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    ExactCountClaim,
    FreshnessStatus,
    HistoricalSpecialistTrustedContext,
    HistoricalWindowClaim,
    LimitationClaim,
    LowerBoundCountClaim,
    RecordIdentityClaim,
    SpecialistClaimKind,
    SpecialistRequest,
    SpecialistResult,
    SpecialistStatus,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
    UnavailableClaim,
    UnsupportedReason,
    VerifiedZeroClaim,
    VerifierOutcome,
    specialist_claim_from_dict,
)


AS_OF = "2026-09-13T18:00:00Z"
WINDOW = ("2026-09-11T00:00:00Z", "2026-09-12T00:00:00Z")
TOOL_CALL = "tool-call-specialist-001"


def assert_validation_error(expected_error: str, factory) -> None:
    with pytest.raises(ContractValidationError) as exc_info:
        factory()
    assert expected_error in exc_info.value.errors


def _tool_result(*, as_of_utc: str | None = AS_OF) -> ToolResult:
    evidence = Evidence(
        source="wilvor.historical.encounter_fact",
        source_records=(),
        query_timestamp_utc=as_of_utc or AS_OF,
        freshness_status=FreshnessStatus.UNKNOWN,
        confidence=ConfidenceLevel.HIGH,
        limitations=("HISTORICAL_FRESHNESS_NOT_ESTABLISHED",),
        tool_call_id=TOOL_CALL,
        temporal_scope=TemporalScope.HISTORICAL,
    )
    return ToolResult(
        tool_name="summarize_historical_encounters",
        tool_call_id=TOOL_CALL,
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.HISTORICAL,
        data={"status": "SUCCEEDED"},
        evidence=(evidence,),
        as_of_utc=as_of_utc,
        limitations=(),
    )


def test_specialist_request_round_trip_and_bounds():
    request = SpecialistRequest(text="Summarize encounters last week", request_id="req-1")
    assert request.to_dict() == {
        "text": "Summarize encounters last week",
        "request_id": "req-1",
    }
    assert SpecialistRequest.from_dict(request.to_dict()) == request
    bare = SpecialistRequest(text="Count historical encounters")
    assert bare.to_dict() == {"text": "Count historical encounters"}
    assert_validation_error("invalid_text", lambda: SpecialistRequest(text=" "))
    assert_validation_error(
        "invalid_text",
        lambda: SpecialistRequest(text="x" * 8193),
    )
    assert_validation_error(
        "invalid_request_id",
        lambda: SpecialistRequest(text="hello", request_id=" "),
    )


def test_trusted_context_is_as_of_only():
    names = {item.name for item in fields(HistoricalSpecialistTrustedContext)}
    assert names == {"as_of_utc"}
    context = HistoricalSpecialistTrustedContext(as_of_utc=AS_OF)
    assert context.to_dict() == {"as_of_utc": AS_OF}
    assert HistoricalSpecialistTrustedContext.from_dict(context.to_dict()) == context
    assert_validation_error(
        "invalid_as_of_utc",
        lambda: HistoricalSpecialistTrustedContext(as_of_utc="2026-09-13"),
    )
    source = HistoricalSpecialistTrustedContext.__doc__ or ""
    assert "datetime.now" not in source


@pytest.mark.parametrize(
    "status",
    list(SpecialistStatus),
)
def test_specialist_status_is_the_approved_closed_set(status):
    assert {item.value for item in SpecialistStatus} == {
        "ANSWERED",
        "PARTIAL",
        "UNSUPPORTED",
        "UNAVAILABLE",
        "INVALID_REQUEST",
        "PROVIDER_FAILED",
    }
    assert status.value == status.name


def test_unsupported_reason_is_closed():
    assert {item.value for item in UnsupportedReason} == {
        "CURRENT_STATE",
        "GEOGRAPHY",
        "FORECAST",
        "ACTION_REQUEST",
        "INSUFFICIENT_EVIDENCE",
        "OUT_OF_CATALOG",
    }


def test_exact_count_claim_accepts_zero_and_rejects_negative():
    claim = ExactCountClaim(tool_call_id=TOOL_CALL, metric_id="encounter_count", value=0)
    assert claim.to_dict()["kind"] == SpecialistClaimKind.EXACT_COUNT.value
    assert specialist_claim_from_dict(claim.to_dict()) == claim
    positive = ExactCountClaim(
        tool_call_id=TOOL_CALL,
        metric_id="encounter_count",
        value=10,
    )
    assert specialist_claim_from_dict(positive.to_dict()) == positive
    assert_validation_error(
        "invalid_value",
        lambda: ExactCountClaim(
            tool_call_id=TOOL_CALL,
            metric_id="encounter_count",
            value=-1,
        ),
    )
    assert_validation_error(
        "invalid_metric_id",
        lambda: ExactCountClaim(
            tool_call_id=TOOL_CALL,
            metric_id="encounter count",
            value=1,
        ),
    )


def test_lower_bound_count_claim_rejects_negative():
    claim = LowerBoundCountClaim(
        tool_call_id=TOOL_CALL,
        metric_id="encounter_count",
        minimum_value=21,
    )
    assert specialist_claim_from_dict(claim.to_dict()) == claim
    assert_validation_error(
        "invalid_minimum_value",
        lambda: LowerBoundCountClaim(
            tool_call_id=TOOL_CALL,
            metric_id="encounter_count",
            minimum_value=-1,
        ),
    )


def test_verified_zero_claim_has_no_numeric_payload():
    claim = VerifiedZeroClaim(tool_call_id=TOOL_CALL)
    payload = claim.to_dict()
    assert payload == {"kind": "VERIFIED_ZERO", "tool_call_id": TOOL_CALL}
    assert "value" not in payload
    assert specialist_claim_from_dict(payload) == claim


def test_historical_window_requires_start_before_end():
    claim = HistoricalWindowClaim(
        tool_call_id=TOOL_CALL,
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
    )
    assert specialist_claim_from_dict(claim.to_dict()) == claim
    assert_validation_error(
        "start_utc_must_precede_end_utc",
        lambda: HistoricalWindowClaim(
            tool_call_id=TOOL_CALL,
            start_utc=WINDOW[1],
            end_utc=WINDOW[0],
        ),
    )
    assert_validation_error(
        "start_utc_must_precede_end_utc",
        lambda: HistoricalWindowClaim(
            tool_call_id=TOOL_CALL,
            start_utc=WINDOW[0],
            end_utc=WINDOW[0],
        ),
    )


def test_record_identity_rejects_whitespace_and_blank():
    claim = RecordIdentityClaim(tool_call_id=TOOL_CALL, record_id="enc#hazard-1#v1")
    assert specialist_claim_from_dict(claim.to_dict()) == claim
    assert_validation_error(
        "invalid_record_id",
        lambda: RecordIdentityClaim(tool_call_id=TOOL_CALL, record_id="not a record"),
    )
    assert_validation_error(
        "invalid_record_id",
        lambda: RecordIdentityClaim(tool_call_id=TOOL_CALL, record_id=""),
    )


def test_limitation_and_unavailable_codes_are_identifiers():
    limitation = LimitationClaim(
        tool_call_id=TOOL_CALL,
        limitation_code="RESULT_TRUNCATED",
    )
    unavailable = UnavailableClaim(
        tool_call_id=TOOL_CALL,
        error_or_coverage_code="COVERAGE_BLOCKED",
    )
    assert specialist_claim_from_dict(limitation.to_dict()) == limitation
    assert specialist_claim_from_dict(unavailable.to_dict()) == unavailable
    assert_validation_error(
        "invalid_limitation_code",
        lambda: LimitationClaim(
            tool_call_id=TOOL_CALL,
            limitation_code="truncated because coverage failed",
        ),
    )


def test_claim_from_dict_rejects_factual_prose_fields():
    with pytest.raises(ContractValidationError) as exc_info:
        specialist_claim_from_dict(
            {
                "kind": "VERIFIED_ZERO",
                "tool_call_id": TOOL_CALL,
                "claim_text": "no matching historical records were found",
            }
        )
    assert "unexpected_claim_prose" in exc_info.value.errors


def test_specialist_result_unsupported_requires_reason_and_none_as_of():
    result = SpecialistResult(
        status=SpecialistStatus.UNSUPPORTED,
        answer="unsupported",
        temporal_scope=TemporalScope.HISTORICAL,
        evaluated_as_of_utc=None,
        tool_results=(),
        verified_claims=(),
        limitations=(),
        unsupported_reason=UnsupportedReason.GEOGRAPHY,
        verifier_outcome=VerifierOutcome.NOT_RUN,
    )
    assert result.evaluated_as_of_utc is None
    assert SpecialistResult.from_dict(result.to_dict()) == result
    assert_validation_error(
        "unsupported_requires_reason",
        lambda: SpecialistResult(
            status=SpecialistStatus.UNSUPPORTED,
            answer="unsupported",
            temporal_scope=TemporalScope.HISTORICAL,
            evaluated_as_of_utc=None,
            tool_results=(),
            verified_claims=(),
            limitations=(),
            unsupported_reason=None,
            verifier_outcome=VerifierOutcome.NOT_RUN,
        ),
    )
    assert_validation_error(
        "unsupported_forbids_verified_claims",
        lambda: SpecialistResult(
            status=SpecialistStatus.UNSUPPORTED,
            answer="unsupported",
            temporal_scope=TemporalScope.HISTORICAL,
            evaluated_as_of_utc=None,
            tool_results=(),
            verified_claims=(VerifiedZeroClaim(tool_call_id=TOOL_CALL),),
            limitations=(),
            unsupported_reason=UnsupportedReason.GEOGRAPHY,
            verifier_outcome=VerifierOutcome.NOT_RUN,
        ),
    )


def test_specialist_result_provider_failed_has_no_unsupported_reason():
    result = SpecialistResult(
        status=SpecialistStatus.PROVIDER_FAILED,
        answer="provider_failed",
        temporal_scope=TemporalScope.HISTORICAL,
        evaluated_as_of_utc=None,
        tool_results=(),
        verified_claims=(),
        limitations=(),
        unsupported_reason=None,
        verifier_outcome=VerifierOutcome.NOT_RUN,
    )
    assert result.evaluated_as_of_utc is None
    assert result.unsupported_reason is None
    assert SpecialistResult.from_dict(result.to_dict()) == result
    assert_validation_error(
        "unsupported_reason_requires_unsupported_status",
        lambda: SpecialistResult(
            status=SpecialistStatus.PROVIDER_FAILED,
            answer="provider_failed",
            temporal_scope=TemporalScope.HISTORICAL,
            evaluated_as_of_utc=None,
            tool_results=(),
            verified_claims=(),
            limitations=(),
            unsupported_reason=UnsupportedReason.OUT_OF_CATALOG,
            verifier_outcome=VerifierOutcome.NOT_RUN,
        ),
    )


def test_specialist_result_accepts_canonical_evaluated_as_of():
    result = SpecialistResult(
        status=SpecialistStatus.ANSWERED,
        answer="verified encounter count is 2",
        temporal_scope=TemporalScope.HISTORICAL,
        evaluated_as_of_utc=AS_OF,
        tool_results=(_tool_result(),),
        verified_claims=(
            ExactCountClaim(
                tool_call_id=TOOL_CALL,
                metric_id="encounter_count",
                value=2,
            ),
        ),
        limitations=(),
        unsupported_reason=None,
        verifier_outcome=VerifierOutcome.PASSED,
    )
    assert SpecialistResult.from_dict(result.to_dict()) == result
    assert_validation_error(
        "invalid_evaluated_as_of_utc",
        lambda: SpecialistResult(
            status=SpecialistStatus.ANSWERED,
            answer="verified",
            temporal_scope=TemporalScope.HISTORICAL,
            evaluated_as_of_utc="next week",
            tool_results=(),
            verified_claims=(),
            limitations=(),
            unsupported_reason=None,
            verifier_outcome=VerifierOutcome.PASSED,
        ),
    )
    assert_validation_error(
        "invalid_limitations",
        lambda: SpecialistResult(
            status=SpecialistStatus.ANSWERED,
            answer="verified",
            temporal_scope=TemporalScope.HISTORICAL,
            evaluated_as_of_utc=AS_OF,
            tool_results=(),
            verified_claims=(),
            limitations=(" ",),
            unsupported_reason=None,
            verifier_outcome=VerifierOutcome.PASSED,
        ),
    )


def test_specialist_result_omits_confidence_and_human_review():
    names = {item.name for item in fields(SpecialistResult)}
    assert "confidence" not in names
    assert "human_review_required" not in names
    assert "correlation_id" not in names
    assert "candidate_answer" not in names
