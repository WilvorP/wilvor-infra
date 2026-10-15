"""Synthetic Live Ops public v1 fixtures.

These objects are structurally valid contract examples. Constructing one
does not mean LO2 verified a claim, and it does not authorize an
operational fact.
"""

from __future__ import annotations

from wilvor_ai.live_ops_public_contracts import (
    LIVE_OPS_ANSWER_CANCELLED,
    LIVE_OPS_ANSWER_INVALID_REQUEST,
    LIVE_OPS_ANSWER_NONE_VERIFIED,
    LIVE_OPS_ANSWER_PROVIDER,
    LIVE_OPS_ANSWER_REFUSED,
    LIVE_OPS_ANSWER_STOPPED,
    LIVE_OPS_ANSWER_TOOL_FAILED,
    LIVE_OPS_ANSWER_UNAVAILABLE,
    LIVE_OPS_ANSWER_UNSUPPORTED,
    LIVE_OPS_COMPLETENESS_NOTICE,
    LIVE_OPS_ENTITY_AUTHORITY,
    LIVE_OPS_PUBLIC_SCHEMA_VERSION,
    LIVE_OPS_PUBLIC_SPECIALIST,
    LiveOpsAnswerOrigin,
    LiveOpsClaimDiagnostic,
    LiveOpsContradictionRecord,
    LiveOpsDiscovery,
    LiveOpsDiscoveryStatus,
    LiveOpsEntityHint,
    LiveOpsEntityRef,
    LiveOpsEvidenceReference,
    LiveOpsFailureReason,
    LiveOpsPublicAdvisoryMode,
    LiveOpsPublicClaim,
    LiveOpsPublicClaimKind,
    LiveOpsPublicClaimVerdict,
    LiveOpsPublicContradictionSubject,
    LiveOpsPublicCountMetric,
    LiveOpsPublicCountMode,
    LiveOpsPublicCurrentnessPredicate,
    LiveOpsPublicFlag,
    LiveOpsPublicLimitationCode,
    LiveOpsPublicObservedField,
    LiveOpsPublicOverlap,
    LiveOpsPublicOverlapField,
    LiveOpsPublicProjectionField,
    LiveOpsPublicRecordedFlag,
    LiveOpsPublicRenderingPosture,
    LiveOpsPublicRiskMode,
    LiveOpsPublicScope,
    LiveOpsPublicUnsupportedReason,
    LiveOpsPublicValidationCode,
    LiveOpsPublicVerificationOutcome,
    LiveOpsRunFailure,
    LiveOpsRunRequest,
    LiveOpsRunResult,
    LiveOpsRunStatus,
    LiveOpsSelectorHints,
    LiveOpsTrustedContext,
    LiveOpsUntrustedTurn,
    live_ops_reference_utc,
)


SYNTHETIC_EPOCH = 1760486400
SYNTHETIC_UTC = "2025-10-15T00:00:00Z"
SYNTHETIC_CORRELATION_ID = "corr-synthetic-1"
SYNTHETIC_REQUEST_ID = "req-synthetic-1"
SYNTHETIC_CONVERSATION_ID = "conv-synthetic-1"
SYNTHETIC_ANSWER = "Synthetic impact claim for aircraft AC-1."


def _check_clock() -> None:
    if live_ops_reference_utc(SYNTHETIC_EPOCH) != SYNTHETIC_UTC:
        raise RuntimeError("synthetic epoch and UTC drifted")


_check_clock()


def trusted_context(**overrides: object) -> LiveOpsTrustedContext:
    """Server-side context. The default clock and cancellation callback are not polled."""

    def clock() -> int:
        return SYNTHETIC_EPOCH

    values: dict[str, object] = {
        "tables": object(),
        "now_epoch": SYNTHETIC_EPOCH,
        "correlation_id": SYNTHETIC_CORRELATION_ID,
        "max_model_turns": 4,
        "max_tool_calls": 6,
        "clock": clock,
        "deadline_epoch": SYNTHETIC_EPOCH + 30,
        "cancelled": lambda: False,
    }
    values.update(overrides)
    return LiveOpsTrustedContext(**values)  # type: ignore[arg-type]


def sample_request() -> LiveOpsRunRequest:
    return LiveOpsRunRequest(
        user_text="Which current impacts match callsign UAL123?",
        request_id=SYNTHETIC_REQUEST_ID,
        conversation_id=SYNTHETIC_CONVERSATION_ID,
        prior_turns=(
            LiveOpsUntrustedTurn(role="user", text="Earlier untrusted question."),
            LiveOpsUntrustedTurn(role="assistant", text="Earlier untrusted reply."),
        ),
        selector_hints=LiveOpsSelectorHints(
            region="California",
            callsign="UAL123",
            hazard_ids=("HZ-1", "HZ-2"),
        ),
        entity_hints=(
            LiveOpsEntityHint(entity_kind="AIRCRAFT", entity_id="AC-1", callsign="UAL123"),
        ),
    )


def evidence(
    tool_call_id: str,
    tool_name: str,
    *limitation_codes: str,
) -> LiveOpsEvidenceReference:
    return LiveOpsEvidenceReference(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        as_of_epoch=SYNTHETIC_EPOCH,
        as_of_utc=SYNTHETIC_UTC,
        limitation_codes=limitation_codes,
    )


def impact_claim() -> LiveOpsPublicClaim:
    return LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CONFIRMED_IMPACT,
        tool_call_id="call-impacts-1",
        tool_name="search_current_impacts",
        aircraft_id="AC-1",
        hazard_id="HZ-1",
        source_version="SV-1",
        encounter_id="EN-1",
    )


def snapshot_claims() -> dict[str, LiveOpsPublicClaim]:
    """One synthetic claim for each closed public kind."""

    claims = (
        impact_claim(),
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.CONFIRMED_ENCOUNTER,
            tool_call_id="call-encounters-1",
            tool_name="search_current_encounters",
            encounter_id="EN-1",
            aircraft_id="AC-1",
            hazard_id="HZ-1",
            hazard_source_version="SV-1",
            projection_id="PROJ-1",
            recorded_flags=(
                LiveOpsPublicFlag(LiveOpsPublicRecordedFlag.CENTERLINE_INTERSECTS, False),
                LiveOpsPublicFlag(LiveOpsPublicRecordedFlag.CORRIDOR_INTERSECTS, True),
            ),
            overlap_statuses=(
                LiveOpsPublicOverlap(LiveOpsPublicOverlapField.ALTITUDE_OVERLAP_STATUS, "RECORDED"),
                LiveOpsPublicOverlap(LiveOpsPublicOverlapField.GEOMETRY_OVERLAP_STATUS, "RECORDED"),
            ),
        ),
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.SCOPED_COUNT,
            tool_call_id="call-impacts-1",
            tool_name="search_current_impacts",
            metric=LiveOpsPublicCountMetric.IMPACT_RECORD_COUNT,
            count_mode=LiveOpsPublicCountMode.EXACT_RETURNED_SET,
            count_value=1,
            scope=LiveOpsPublicScope(region="California", hazard_ids=("HZ-1", "HZ-2")),
        ),
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.QUERY_RESULT_ZERO,
            tool_call_id="call-find-zero",
            tool_name="find_aircraft_by_callsign",
            metric=LiveOpsPublicCountMetric.CALLSIGN_MATCH_COUNT,
            scope=LiveOpsPublicScope(callsign="NONE0"),
        ),
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.CONTEXT_ROOT_ABSENT,
            tool_call_id="call-aircraft-1",
            tool_name="get_aircraft_operational_context",
            entity_id="AC-9",
        ),
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION,
            tool_call_id="call-hazards-1",
            tool_name="search_current_hazards",
            hazard_id="HZ-1",
            source_version="SV-1",
            region_code="US-CA",
            region_name="California",
            region_query="California",
        ),
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE,
            tool_call_id="call-aircraft-1",
            tool_name="get_aircraft_operational_context",
            aircraft_id="AC-1",
            observed_field=LiveOpsPublicObservedField.LATITUDE,
            observed_value=37.6,
            retained_not_current=False,
        ),
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.AIRCRAFT_PROJECTION_STATE,
            tool_call_id="call-aircraft-1",
            tool_name="get_aircraft_operational_context",
            aircraft_id="AC-1",
            projection_id="PROJ-1",
            projection_field=LiveOpsPublicProjectionField.GENERATED_AT_UTC,
            projection_value="2025-10-15T00:00:00Z",
        ),
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.CURRENTNESS,
            tool_call_id="call-hazards-1",
            tool_name="search_current_hazards",
            predicate=LiveOpsPublicCurrentnessPredicate.HAZARD_IS_CURRENT,
            currentness_value=True,
            hazard_id="HZ-1",
            source_version="SV-1",
        ),
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.RISK_STATE,
            tool_call_id="call-impacts-1",
            tool_name="search_current_impacts",
            risk_mode=LiveOpsPublicRiskMode.PRESENT,
            encounter_id="EN-1",
            aircraft_id="AC-1",
            hazard_id="HZ-1",
            risk_id="RISK-1",
            risk_level="HIGH",
            risk_score=2,
        ),
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.RECOMMENDATION_SET,
            tool_call_id="call-impacts-1",
            tool_name="search_current_impacts",
            advisory_mode=LiveOpsPublicAdvisoryMode.PRESENT_SET,
            encounter_id="EN-1",
            aircraft_id="AC-1",
            hazard_id="HZ-1",
            recommendation_ids=("REC-1",),
        ),
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.ALERT_RECORD,
            tool_call_id="call-impacts-1",
            tool_name="search_current_impacts",
            advisory_mode=LiveOpsPublicAdvisoryMode.PRESENT_SET,
            encounter_id="EN-1",
            aircraft_id="AC-1",
            hazard_id="HZ-1",
            alert_id="AL-1",
            alert_state="ACTIVE",
            alert_type="IMPACT",
        ),
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.CALLSIGN_MATCH_SET,
            tool_call_id="call-find-1",
            tool_name="find_aircraft_by_callsign",
            callsign="UAL123",
            aircraft_ids=("AC-1",),
        ),
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.COVERAGE_LIMITATION,
            tool_call_id="call-impacts-1",
            tool_name="search_current_impacts",
            limitation_code=LiveOpsPublicLimitationCode.FRESHNESS_NOT_ESTABLISHED,
        ),
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.MODEL_VISIBLE_TRUNCATION,
            tool_call_id="call-impacts-1",
            tool_name="search_current_impacts",
            list_name="impacts",
            total_count=30,
        ),
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.CROSS_TOOL_CONTRADICTION,
            tool_call_id_a="call-hazards-a",
            tool_call_id_b="call-hazards-b",
            subject=LiveOpsPublicContradictionSubject.HAZARD_SOURCE_VERSION,
            hazard_id="HZ-1",
        ),
    )
    return {claim.kind.value: claim for claim in claims}


def _completed(
    *,
    claims: tuple[LiveOpsPublicClaim, ...],
    references: tuple[LiveOpsEvidenceReference, ...],
    outcome: LiveOpsPublicVerificationOutcome,
    posture: LiveOpsPublicRenderingPosture,
    origin: LiveOpsAnswerOrigin,
    answer: str,
    diagnostics: tuple[LiveOpsClaimDiagnostic, ...] = (),
    contradictions: tuple[LiveOpsContradictionRecord, ...] = (),
    limitations: tuple[str, ...] = ("FRESHNESS_NOT_ESTABLISHED",),
    discoveries: tuple[LiveOpsDiscovery, ...] = (),
    entity_refs: tuple[LiveOpsEntityRef, ...] = (),
    request_id: str | None = SYNTHETIC_REQUEST_ID,
    conversation_id: str | None = SYNTHETIC_CONVERSATION_ID,
    correlation_id: str = SYNTHETIC_CORRELATION_ID,
) -> LiveOpsRunResult:
    return LiveOpsRunResult(
        schema_version=LIVE_OPS_PUBLIC_SCHEMA_VERSION,
        request_id=request_id,
        conversation_id=conversation_id,
        correlation_id=correlation_id,
        execution_status=LiveOpsRunStatus.COMPLETED,
        verification_outcome=outcome,
        rendering_posture=posture,
        answer_origin=origin,
        answer=answer,
        reference_epoch=SYNTHETIC_EPOCH,
        reference_utc=SYNTHETIC_UTC,
        completeness_notice=LIVE_OPS_COMPLETENESS_NOTICE,
        verified_claims=claims,
        evidence_references=references,
        diagnostics=diagnostics,
        contradiction_subjects=contradictions,
        mandatory_limitations=limitations,
        discoveries=discoveries,
        verified_entity_refs=entity_refs,
        failure=None,
    )


def completed_all_verified(
    *,
    request_id: str | None = SYNTHETIC_REQUEST_ID,
    conversation_id: str | None = SYNTHETIC_CONVERSATION_ID,
    correlation_id: str = SYNTHETIC_CORRELATION_ID,
) -> LiveOpsRunResult:
    return _completed(
        claims=(impact_claim(),),
        references=(evidence("call-impacts-1", "search_current_impacts", "FRESHNESS_NOT_ESTABLISHED"),),
        outcome=LiveOpsPublicVerificationOutcome.ALL_VERIFIED,
        posture=LiveOpsPublicRenderingPosture.COMPLETE_VERIFIED_SET,
        origin=LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER,
        answer=SYNTHETIC_ANSWER,
        limitations=("FRESHNESS_NOT_ESTABLISHED", "SYNTHETIC_FUTURE_LIMITATION"),
        request_id=request_id,
        conversation_id=conversation_id,
        correlation_id=correlation_id,
    )


def partially_verified() -> LiveOpsRunResult:
    diagnostic = LiveOpsClaimDiagnostic(
        claim_kind=LiveOpsPublicClaimKind.SCOPED_COUNT,
        verdict=LiveOpsPublicClaimVerdict.REJECTED,
        codes=("SYNTHETIC_REJECTED",),
        tool_call_ids=("call-count-rejected",),
    )
    return _completed(
        claims=(impact_claim(),),
        references=(evidence("call-impacts-1", "search_current_impacts"),),
        outcome=LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED,
        posture=LiveOpsPublicRenderingPosture.INCOMPLETE_VERIFIED_SUBSET,
        origin=LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER,
        answer=SYNTHETIC_ANSWER,
        diagnostics=(diagnostic,),
    )


def none_verified() -> LiveOpsRunResult:
    diagnostic = LiveOpsClaimDiagnostic(
        claim_kind=LiveOpsPublicClaimKind.CONFIRMED_IMPACT,
        verdict=LiveOpsPublicClaimVerdict.INSUFFICIENT_EVIDENCE,
        codes=("SYNTHETIC_UNVERIFIED",),
    )
    return _completed(
        claims=(),
        references=(),
        outcome=LiveOpsPublicVerificationOutcome.NONE_VERIFIED,
        posture=LiveOpsPublicRenderingPosture.NO_VERIFIED_FACTS,
        origin=LiveOpsAnswerOrigin.FIXED_SAFE_TEXT,
        answer=LIVE_OPS_ANSWER_NONE_VERIFIED,
        diagnostics=(diagnostic,),
        limitations=(),
    )


def _failed(
    status: LiveOpsRunStatus,
    reason: LiveOpsFailureReason,
    answer: str,
    **failure_fields: object,
) -> LiveOpsRunResult:
    failure = LiveOpsRunFailure(reason=reason, **failure_fields)  # type: ignore[arg-type]
    return LiveOpsRunResult(
        request_id=SYNTHETIC_REQUEST_ID,
        conversation_id=SYNTHETIC_CONVERSATION_ID,
        correlation_id=SYNTHETIC_CORRELATION_ID,
        execution_status=status,
        verification_outcome=None,
        rendering_posture=None,
        answer_origin=LiveOpsAnswerOrigin.FIXED_SAFE_TEXT,
        answer=answer,
        reference_epoch=SYNTHETIC_EPOCH,
        reference_utc=SYNTHETIC_UTC,
        failure=failure,
    )


def tool_execution_failed() -> LiveOpsRunResult:
    return _failed(
        LiveOpsRunStatus.TOOL_FAILED,
        LiveOpsFailureReason.TOOL_EXECUTION_FAILED,
        LIVE_OPS_ANSWER_TOOL_FAILED,
        failed_tool_name="search_current_impacts",
    )


def tool_failed_not_authorized() -> LiveOpsRunResult:
    return LiveOpsRunResult(
        request_id=SYNTHETIC_REQUEST_ID,
        conversation_id=SYNTHETIC_CONVERSATION_ID,
        correlation_id=SYNTHETIC_CORRELATION_ID,
        execution_status=LiveOpsRunStatus.TOOL_FAILED,
        verification_outcome=LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED,
        rendering_posture=LiveOpsPublicRenderingPosture.NOT_RENDERABLE,
        answer_origin=LiveOpsAnswerOrigin.FIXED_SAFE_TEXT,
        answer=LIVE_OPS_ANSWER_TOOL_FAILED,
        reference_epoch=SYNTHETIC_EPOCH,
        reference_utc=SYNTHETIC_UTC,
        structural_codes=("RUN_EXECUTION_FAILED",),
        failure=LiveOpsRunFailure(
            reason=LiveOpsFailureReason.TOOL_EXECUTION_FAILED,
            failed_tool_name="search_current_impacts",
        ),
    )


def ledger_not_authorized() -> LiveOpsRunResult:
    return LiveOpsRunResult(
        request_id=SYNTHETIC_REQUEST_ID,
        conversation_id=SYNTHETIC_CONVERSATION_ID,
        correlation_id=SYNTHETIC_CORRELATION_ID,
        execution_status=LiveOpsRunStatus.UNAVAILABLE,
        verification_outcome=LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED,
        rendering_posture=LiveOpsPublicRenderingPosture.NOT_RENDERABLE,
        answer_origin=LiveOpsAnswerOrigin.FIXED_SAFE_TEXT,
        answer=LIVE_OPS_ANSWER_UNAVAILABLE,
        reference_epoch=SYNTHETIC_EPOCH,
        reference_utc=SYNTHETIC_UTC,
        structural_codes=("LEDGER_NOT_AUTHORIZED",),
        failure=LiveOpsRunFailure(reason=LiveOpsFailureReason.LEDGER_NOT_AUTHORIZED),
    )


def unsupported_request() -> LiveOpsRunResult:
    return _failed(
        LiveOpsRunStatus.UNSUPPORTED,
        LiveOpsFailureReason.UNSUPPORTED,
        LIVE_OPS_ANSWER_UNSUPPORTED,
        unsupported_reason=LiveOpsPublicUnsupportedReason.HISTORICAL_ANALYTICS,
    )


def model_refused() -> LiveOpsRunResult:
    return _failed(
        LiveOpsRunStatus.REFUSED,
        LiveOpsFailureReason.MODEL_REFUSAL,
        LIVE_OPS_ANSWER_REFUSED,
        refusal_code="SYNTHETIC_REFUSAL",
    )


def provider_failed() -> LiveOpsRunResult:
    return _failed(
        LiveOpsRunStatus.PROVIDER_FAILED,
        LiveOpsFailureReason.TRANSPORT_OR_API,
        LIVE_OPS_ANSWER_PROVIDER,
    )


def provider_timeout() -> LiveOpsRunResult:
    return _failed(
        LiveOpsRunStatus.PROVIDER_TIMEOUT,
        LiveOpsFailureReason.PROVIDER_TIMEOUT,
        LIVE_OPS_ANSWER_PROVIDER,
    )


def runtime_timeout() -> LiveOpsRunResult:
    return _failed(
        LiveOpsRunStatus.RUNTIME_TIMEOUT,
        LiveOpsFailureReason.RUNTIME_TIMEOUT,
        LIVE_OPS_ANSWER_STOPPED,
    )


def budget_exhausted() -> LiveOpsRunResult:
    return _failed(
        LiveOpsRunStatus.BUDGET_EXHAUSTED,
        LiveOpsFailureReason.MODEL_TURNS,
        LIVE_OPS_ANSWER_STOPPED,
    )


def tool_budget_exhausted() -> LiveOpsRunResult:
    return _failed(
        LiveOpsRunStatus.BUDGET_EXHAUSTED,
        LiveOpsFailureReason.TOOL_CALLS,
        LIVE_OPS_ANSWER_STOPPED,
    )


def cancelled() -> LiveOpsRunResult:
    return _failed(
        LiveOpsRunStatus.CANCELLED,
        LiveOpsFailureReason.CANCELLED,
        LIVE_OPS_ANSWER_CANCELLED,
    )


def validation_exhausted() -> LiveOpsRunResult:
    return _failed(
        LiveOpsRunStatus.INVALID_REQUEST,
        LiveOpsFailureReason.TOOL_CALL_VALIDATION_EXHAUSTED,
        LIVE_OPS_ANSWER_INVALID_REQUEST,
        validation_code=LiveOpsPublicValidationCode.UNKNOWN_TOOL,
    )


def rejected_request() -> LiveOpsRunResult:
    return LiveOpsRunResult.rejected_payload(
        trusted_context(),
        request_id=SYNTHETIC_REQUEST_ID,
        conversation_id=SYNTHETIC_CONVERSATION_ID,
    )


def contradiction_result() -> LiveOpsRunResult:
    claim = snapshot_claims()["CROSS_TOOL_CONTRADICTION"]
    return _completed(
        claims=(claim,),
        references=(
            evidence("call-hazards-a", "search_current_hazards"),
            evidence("call-hazards-b", "get_hazard_operational_context"),
        ),
        outcome=LiveOpsPublicVerificationOutcome.ALL_VERIFIED,
        posture=LiveOpsPublicRenderingPosture.COMPLETE_VERIFIED_SET,
        origin=LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER,
        answer="Synthetic contradiction for hazard HZ-1.",
        contradictions=(
            LiveOpsContradictionRecord(
                subject=LiveOpsPublicContradictionSubject.HAZARD_SOURCE_VERSION,
                identity=(("hazard_id", "HZ-1"),),
                tool_call_ids=("call-hazards-a", "call-hazards-b"),
            ),
        ),
    )


def _discovery(
    callsign: str,
    status: LiveOpsDiscoveryStatus,
    aircraft_ids: tuple[str, ...],
    tool_call_id: str,
    identity_resolved: bool,
) -> LiveOpsDiscovery:
    return LiveOpsDiscovery(
        callsign=callsign,
        status=status,
        aircraft_ids=aircraft_ids,
        evidence_tool_call_id=tool_call_id,
        reference_epoch=SYNTHETIC_EPOCH,
        identity_resolved=identity_resolved,
        selection_authorized=False,
    )


def _entity_ref(entity_id: str, callsign: str, tool_call_id: str) -> LiveOpsEntityRef:
    return LiveOpsEntityRef(
        entity_kind="AIRCRAFT",
        entity_id=entity_id,
        callsign=callsign,
        authority=LIVE_OPS_ENTITY_AUTHORITY,
        source_specialist=LIVE_OPS_PUBLIC_SPECIALIST,
        public_schema_version=LIVE_OPS_PUBLIC_SCHEMA_VERSION,
        correlation_id=SYNTHETIC_CORRELATION_ID,
        reference_epoch=SYNTHETIC_EPOCH,
        reference_utc=SYNTHETIC_UTC,
        evidence_tool_call_id=tool_call_id,
    )


def unique_match_result() -> LiveOpsRunResult:
    claim = snapshot_claims()["CALLSIGN_MATCH_SET"]
    return _completed(
        claims=(claim,),
        references=(evidence("call-find-1", "find_aircraft_by_callsign"),),
        outcome=LiveOpsPublicVerificationOutcome.ALL_VERIFIED,
        posture=LiveOpsPublicRenderingPosture.COMPLETE_VERIFIED_SET,
        origin=LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER,
        answer="Synthetic unique callsign match for UAL123.",
        discoveries=(
            _discovery(
                "UAL123",
                LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH,
                ("AC-1",),
                "call-find-1",
                True,
            ),
        ),
        entity_refs=(_entity_ref("AC-1", "UAL123", "call-find-1"),),
    )


def ambiguous_match_result() -> LiveOpsRunResult:
    claim = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CALLSIGN_MATCH_SET,
        tool_call_id="call-find-many",
        tool_name="find_aircraft_by_callsign",
        callsign="UAL123",
        aircraft_ids=("AC-1", "AC-2"),
    )
    return _completed(
        claims=(claim,),
        references=(evidence("call-find-many", "find_aircraft_by_callsign"),),
        outcome=LiveOpsPublicVerificationOutcome.ALL_VERIFIED,
        posture=LiveOpsPublicRenderingPosture.COMPLETE_VERIFIED_SET,
        origin=LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER,
        answer="Synthetic multiple callsign matches for UAL123.",
        discoveries=(
            _discovery(
                "UAL123",
                LiveOpsDiscoveryStatus.MULTIPLE_SUPPORTED_MATCHES,
                ("AC-1", "AC-2"),
                "call-find-many",
                False,
            ),
        ),
    )


def zero_match_result() -> LiveOpsRunResult:
    claim = snapshot_claims()["QUERY_RESULT_ZERO"]
    return _completed(
        claims=(claim,),
        references=(evidence("call-find-zero", "find_aircraft_by_callsign"),),
        outcome=LiveOpsPublicVerificationOutcome.ALL_VERIFIED,
        posture=LiveOpsPublicRenderingPosture.COMPLETE_VERIFIED_SET,
        origin=LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER,
        answer="Synthetic supported zero for callsign NONE0.",
        discoveries=(
            _discovery(
                "NONE0",
                LiveOpsDiscoveryStatus.NO_SUPPORTED_MATCH,
                (),
                "call-find-zero",
                False,
            ),
        ),
    )


def incomplete_discovery_result() -> LiveOpsRunResult:
    return _completed(
        claims=(impact_claim(),),
        references=(
            evidence("call-find-incomplete", "find_aircraft_by_callsign"),
            evidence("call-impacts-1", "search_current_impacts"),
        ),
        outcome=LiveOpsPublicVerificationOutcome.ALL_VERIFIED,
        posture=LiveOpsPublicRenderingPosture.COMPLETE_VERIFIED_SET,
        origin=LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER,
        answer=SYNTHETIC_ANSWER,
        discoveries=(
            _discovery(
                "UAL999",
                LiveOpsDiscoveryStatus.INCOMPLETE,
                (),
                "call-find-incomplete",
                False,
            ),
        ),
    )


def separate_callsign_discoveries() -> LiveOpsRunResult:
    first = snapshot_claims()["CALLSIGN_MATCH_SET"]
    second = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CALLSIGN_MATCH_SET,
        tool_call_id="call-find-2",
        tool_name="find_aircraft_by_callsign",
        callsign="DAL456",
        aircraft_ids=("AC-2", "AC-3"),
    )
    return _completed(
        claims=(first, second),
        references=(
            evidence("call-find-1", "find_aircraft_by_callsign"),
            evidence("call-find-2", "find_aircraft_by_callsign"),
        ),
        outcome=LiveOpsPublicVerificationOutcome.ALL_VERIFIED,
        posture=LiveOpsPublicRenderingPosture.COMPLETE_VERIFIED_SET,
        origin=LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER,
        answer="Synthetic separate callsign searches.",
        discoveries=(
            _discovery(
                "UAL123",
                LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH,
                ("AC-1",),
                "call-find-1",
                True,
            ),
            _discovery(
                "DAL456",
                LiveOpsDiscoveryStatus.MULTIPLE_SUPPORTED_MATCHES,
                ("AC-2", "AC-3"),
                "call-find-2",
                False,
            ),
        ),
        entity_refs=(_entity_ref("AC-1", "UAL123", "call-find-1"),),
    )
