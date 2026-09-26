"""Deterministic historical factual renderer and finalizer (Phase 3A.3).

Answers are code-rendered from verified claims and full ToolResults.
This module does not call a model, verify by projection, or import AWS.
"""

from __future__ import annotations

from wilvor_ai.contracts import TemporalScope
from wilvor_ai.historical_analytics_mapping import (
    MAPPING_INTEGRITY_FAILED,
    RESULT_TRUNCATED_LIMITATION,
)
from wilvor_ai.historical_evidence_verifier import (
    CLAIM_SET_NOT_ACTIONABLE,
    CLAIM_VERIFICATION_FAILED,
    LOWER_BOUND_METRIC_ID,
    HistoricalVerificationResult,
    verify_historical_specialist_run,
)
from wilvor_ai.specialist_contracts import (
    ExactCountClaim,
    HistoricalWindowClaim,
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
from wilvor_historical.query_contracts import (
    COVERAGE_REASON_EPOCH_AMBIGUOUS,
    HAZARD_VERSION_WINDOW_LIMITATION,
    QUERY_RESULT_MALFORMED,
)


METRIC_LABELS = {
    "physical_record_count": "historical records",
    "distinct_encounter_count": "distinct encounters",
    "distinct_aircraft_count": "distinct aircraft",
    "distinct_hazard_count": "distinct hazards",
    "distinct_dedup_count": "distinct historical observations",
    "distinct_risk_count": "distinct risk results",
    "distinct_hazard_version_count": "distinct hazard versions",
    LOWER_BOUND_METRIC_ID: "matching records",
}

_UNSUPPORTED_ANSWERS = {
    UnsupportedReason.CURRENT_STATE: (
        "This historical analytics specialist cannot answer current-state "
        "questions."
    ),
    UnsupportedReason.GEOGRAPHY: (
        "The available historical evidence cannot establish the requested "
        "geographic impact."
    ),
    UnsupportedReason.FORECAST: (
        "This specialist does not provide forecast/future-state answers."
    ),
    UnsupportedReason.ACTION_REQUEST: (
        "This specialist is read-only/advisory and cannot provide or execute "
        "the requested operational action."
    ),
    UnsupportedReason.INSUFFICIENT_EVIDENCE: (
        "The available historical toolset cannot establish that claim."
    ),
    UnsupportedReason.OUT_OF_CATALOG: (
        "The request is outside this specialist's historical analytics "
        "capability."
    ),
}

_COVERAGE_UNAVAILABLE_CODES = frozenset(
    {
        COVERAGE_REASON_EPOCH_AMBIGUOUS,
        "NOT_ACTIVE",
        "NOT_YET_EVALUABLE",
        "GAP_OR_UNCERTAIN",
    }
)
_QUERY_UNAVAILABLE_CODES = frozenset(
    {
        "QUERY_FAILED",
        "QUERY_CANCELED",
        "QUERY_TIMEOUT",
        QUERY_RESULT_MALFORMED,
    }
)
_INTEGRITY_CODES = frozenset(
    {
        MAPPING_INTEGRITY_FAILED,
        SpecialistRuntimeErrorCode.AS_OF_MISMATCH.value,
        SpecialistRuntimeErrorCode.PROJECTION_INTEGRITY_FAILED.value,
        SpecialistRuntimeErrorCode.ADAPTER_EXECUTION_FAILED.value,
        CLAIM_VERIFICATION_FAILED,
        CLAIM_SET_NOT_ACTIONABLE,
    }
)

_UNAVAILABLE_ANSWER = (
    "Wilvor cannot certify an answer for the requested historical interval "
    "from the available deterministic evidence."
)
_VERIFICATION_FAILED_ANSWER = (
    "Wilvor could not verify the model-proposed historical claims against the "
    "deterministic tool evidence, so no factual answer is being returned."
)
_INVALID_REQUEST_ANSWER = (
    "The historical request could not form a valid deterministic operation."
)
_PROVIDER_FAILED_ANSWER = (
    "The historical analytics provider did not complete a valid turn, so no "
    "factual answer is being returned."
)
_VERIFIED_ZERO_ANSWER = (
    "No matching historical records were found in the requested interval, and "
    "Wilvor's persisted collection coverage for that interval is evaluable."
)
_MATERIALIZATION_SENTENCE = (
    "Hazard versions were selected by materialization/event time, not "
    "validity-interval overlap."
)
_TRUNCATION_SENTENCE = "The returned list is incomplete/truncated."


def finalize_historical_specialist_run(
    run_result: HistoricalSpecialistRunResult,
) -> SpecialistResult:
    """Convert a 3A.2 run result into a verified SpecialistResult.

    Does not accept a provider, operations, AWS clients, or trusted context.
    """

    if not isinstance(run_result, HistoricalSpecialistRunResult):
        raise TypeError("run_result must be a HistoricalSpecialistRunResult")
    if run_result.status is SpecialistRunStatus.PROPOSED_CLAIMS:
        verification = verify_historical_specialist_run(run_result)
        return render_historical_specialist_result(run_result, verification)
    return render_historical_specialist_result(run_result, None)


def render_historical_specialist_result(
    run_result: HistoricalSpecialistRunResult,
    verification: HistoricalVerificationResult | None,
) -> SpecialistResult:
    """Render a deterministic SpecialistResult. No model prose."""

    if run_result.status is SpecialistRunStatus.PROPOSED_CLAIMS:
        if verification is None:
            verification = verify_historical_specialist_run(run_result)
        if verification.outcome is VerifierOutcome.FAILED:
            return _result(
                run_result,
                status=SpecialistStatus.UNAVAILABLE,
                answer=_VERIFICATION_FAILED_ANSWER,
                verified_claims=(),
                limitations=verification.mandatory_limitations,
                verifier_outcome=VerifierOutcome.FAILED,
            )
        assert verification.derived_specialist_status is not None
        return _result(
            run_result,
            status=verification.derived_specialist_status,
            answer=_factual_answer(run_result, verification),
            verified_claims=verification.verified_claims,
            limitations=verification.mandatory_limitations,
            verifier_outcome=VerifierOutcome.PASSED,
        )

    if run_result.status is SpecialistRunStatus.UNSUPPORTED:
        reason = run_result.unsupported_reason
        assert reason is not None
        return _result(
            run_result,
            status=SpecialistStatus.UNSUPPORTED,
            answer=_UNSUPPORTED_ANSWERS[reason],
            verified_claims=(),
            limitations=(),
            verifier_outcome=VerifierOutcome.NOT_RUN,
            unsupported_reason=reason,
        )
    if run_result.status is SpecialistRunStatus.INVALID_REQUEST:
        return _result(
            run_result,
            status=SpecialistStatus.INVALID_REQUEST,
            answer=_INVALID_REQUEST_ANSWER,
            verified_claims=(),
            limitations=(),
            verifier_outcome=VerifierOutcome.NOT_RUN,
        )
    if run_result.status is SpecialistRunStatus.UNAVAILABLE:
        return _result(
            run_result,
            status=SpecialistStatus.UNAVAILABLE,
            answer=_unavailable_answer(run_result),
            verified_claims=(),
            limitations=tuple(run_result.runtime_errors),
            verifier_outcome=VerifierOutcome.NOT_RUN,
        )
    return _result(
        run_result,
        status=SpecialistStatus.PROVIDER_FAILED,
        answer=_PROVIDER_FAILED_ANSWER,
        verified_claims=(),
        limitations=tuple(run_result.runtime_errors),
        verifier_outcome=VerifierOutcome.NOT_RUN,
    )


def _result(
    run_result: HistoricalSpecialistRunResult,
    *,
    status: SpecialistStatus,
    answer: str,
    verified_claims,
    limitations: tuple[str, ...],
    verifier_outcome: VerifierOutcome,
    unsupported_reason: UnsupportedReason | None = None,
) -> SpecialistResult:
    return SpecialistResult(
        status=status,
        answer=answer,
        temporal_scope=TemporalScope.HISTORICAL,
        evaluated_as_of_utc=run_result.evaluated_as_of_utc,
        tool_results=run_result.tool_results,
        verified_claims=verified_claims,
        limitations=limitations,
        unsupported_reason=unsupported_reason,
        verifier_outcome=verifier_outcome,
    )


def _factual_answer(
    run_result: HistoricalSpecialistRunResult,
    verification: HistoricalVerificationResult,
) -> str:
    claims = verification.verified_claims
    if any(isinstance(item, UnavailableClaim) for item in claims) or (
        verification.derived_specialist_status is SpecialistStatus.UNAVAILABLE
    ):
        return _unavailable_answer(run_result, claims)

    parts: list[str] = []
    if any(isinstance(item, VerifiedZeroClaim) for item in claims):
        parts.append(_VERIFIED_ZERO_ANSWER)
    for claim in claims:
        if isinstance(claim, ExactCountClaim):
            label = METRIC_LABELS[claim.metric_id]
            parts.append(
                "The historical query returned an exact count of "
                f"{claim.value} for {label} in the requested interval."
            )
        elif isinstance(claim, HistoricalWindowClaim):
            continue
        elif isinstance(claim, RecordIdentityClaim):
            parts.append(
                f"Returned historical record identifier: {claim.record_id}."
            )
    lowers = [item for item in claims if isinstance(item, LowerBoundCountClaim)]
    if lowers:
        parts.append(
            "The historical query returned at least "
            f"{lowers[0].minimum_value} matching records in the requested interval."
        )
    window = next((item for item in claims if isinstance(item, HistoricalWindowClaim)), None)
    if window is not None:
        parts.append(
            f"Requested interval: [{window.start_utc}, {window.end_utc})."
        )
    if HAZARD_VERSION_WINDOW_LIMITATION in verification.mandatory_limitations:
        parts.append(_MATERIALIZATION_SENTENCE)
    if RESULT_TRUNCATED_LIMITATION in verification.mandatory_limitations:
        if _TRUNCATION_SENTENCE not in parts:
            parts.append(_TRUNCATION_SENTENCE)
    return " ".join(parts)


def _unavailable_answer(
    run_result: HistoricalSpecialistRunResult,
    claims: tuple = (),
) -> str:
    codes = list(run_result.runtime_errors)
    for claim in claims:
        if isinstance(claim, UnavailableClaim):
            codes.append(claim.error_or_coverage_code)
    for result in run_result.tool_results:
        codes.extend(result.limitations)
        for evidence in result.evidence:
            if evidence.error_code is not None:
                codes.append(evidence.error_code)
    category = "internal evidence integrity failure"
    if any(code in _COVERAGE_UNAVAILABLE_CODES for code in codes):
        category = "coverage unavailable"
    elif any(code in _QUERY_UNAVAILABLE_CODES for code in codes):
        category = "query unavailable"
    elif any(code in _INTEGRITY_CODES for code in codes):
        category = "internal evidence integrity failure"
    return f"{_UNAVAILABLE_ANSWER} {category.capitalize()}."


__all__ = [
    "METRIC_LABELS",
    "finalize_historical_specialist_run",
    "render_historical_specialist_result",
]
