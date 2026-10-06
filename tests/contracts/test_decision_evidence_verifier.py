"""Deterministic Decision evidence verifier contracts."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from wilvor_ai.contracts import (
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.decision_claims import (
    AircraftIdentityClaim,
    CapabilityGapClaim,
    ChainGapClaim,
    CurrentPersistedLinkClaim,
    EncounterSetClaim,
    EvaluationStateClaim,
    LimitationClaim,
    PersistedAssessmentStatus,
    PersistedCandidateClaim,
    PersistedCandidateCollectionReason,
    PersistedCandidateStatusClaim,
    PersistedCapabilityEvidenceStatus,
    PersistedEvaluationClaim,
    RecommendationActionClaim,
    RecommendationSetClaim,
    RiskAbsentClaim,
    RiskPresentClaim,
    ToolStatusClaim,
)
from wilvor_ai.decision_contracts import (
    DecisionAdvisoryAuthority,
    DecisionCapabilityGap,
    DecisionChainGap,
    DecisionEncounterLink,
    DecisionEvaluationState,
    DecisionEvidence,
    DecisionEvidenceKind,
    DecisionLimitationCode,
    DecisionRecommendationEvidence,
    DecisionRecommendationSet,
    DecisionReportedLinkState,
    DecisionRiskEvidence,
    DecisionRouteCapability,
    HazardSourceVersionLink,
    PersistedEvaluationScope,
    RecommendationActionType,
    RiskPresence,
    StoredRiskLevel,
    expected_decision_status,
)
from wilvor_ai.decision_evidence_verifier import (
    CLAIM_SET_NOT_ACTIONABLE,
    CLAIM_VERIFICATION_FAILED,
    CONTEXT_TOOL,
    DECISION_VERIFICATION_SCHEMA_VERSION,
    EMPTY_ASSESSMENTS_REASON,
    NOT_CURRENT_LIMITATION,
    NO_COMPLETE_ASSESSMENT_REASON,
    PERSISTED_TOOL,
    RECOMMENDATION_TOOL,
    RISK_TOOL,
    DecisionClaimRejectionCode,
    DecisionEvidenceBinding,
    DecisionVerificationResult,
    verify_decision_evidence,
)
from wilvor_ai.persisted_airport_contracts import (
    PersistedAirportCandidate,
    PersistedAirportEvidence,
)
from wilvor_ai.persisted_airport_evidence import (
    EMPTY_ASSESSMENTS,
    NOT_CURRENT_LIMITATION as PRODUCER_NOT_CURRENT,
    NO_COMPLETE_ASSESSMENT,
)
from wilvor_ai.specialist_contracts import SpecialistStatus, VerifierOutcome


AS_OF = "2026-09-29T00:00:00Z"
AS_OF_OTHER = "2026-09-29T01:00:00Z"
UNTIL = "2026-09-29T00:15:00Z"
REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = REPO_ROOT / "functions" / "shared" / "wilvor_ai" / "decision_evidence_verifier.py"
SHARED_DIR = REPO_ROOT / "functions" / "shared"
_UNAVAILABLE = PersistedCapabilityEvidenceStatus.UNAVAILABLE


def _errors(factory) -> list[str]:
    with pytest.raises(ContractValidationError) as caught:
        factory()
    return caught.value.errors


def _capability() -> DecisionRouteCapability:
    return DecisionRouteCapability(validated_alternative_available=False)


def _present_risk(
    *,
    level: StoredRiskLevel = StoredRiskLevel.HIGH,
    score: int = 80,
    risk_id: str = "risk-1",
    encounter_id: str | None = "enc-1",
) -> DecisionRiskEvidence:
    return DecisionRiskEvidence(
        presence=RiskPresence.PRESENT,
        risk_id=risk_id,
        encounter_id=encounter_id,
        risk_level=level,
        risk_score=score,
    )


def _absent_risk(encounter_id: str | None = None) -> DecisionRiskEvidence:
    return DecisionRiskEvidence(presence=RiskPresence.ABSENT, encounter_id=encounter_id)


def _recommendation(
    recommendation_id: str = "rec-1",
    action: RecommendationActionType = RecommendationActionType.MONITOR,
) -> DecisionRecommendationEvidence:
    return DecisionRecommendationEvidence(
        recommendation_id=recommendation_id,
        primary_action_type=action,
        advisory_authority=DecisionAdvisoryAuthority.ADVISORY_ONLY,
    )


def _recommendations(*items: DecisionRecommendationEvidence) -> DecisionRecommendationSet:
    return DecisionRecommendationSet(
        current=items,
        absence_state=(
            DecisionReportedLinkState.PRESENT
            if items
            else DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES
        ),
    )


def _hazard(hazard_id: str = "haz-1") -> HazardSourceVersionLink:
    return HazardSourceVersionLink(
        hazard_id=hazard_id,
        state=DecisionReportedLinkState.PRESENT,
        persisted_source_version="sv-1",
        current_source_version="sv-1",
    )


def _encounter(
    encounter_id: str = "enc-1",
    risk: DecisionRiskEvidence | None = None,
    recommendations: DecisionRecommendationSet | None = None,
    hazard_id: str = "haz-1",
) -> DecisionEncounterLink:
    return DecisionEncounterLink(
        encounter_id=encounter_id,
        projection_id="proj-1",
        aircraft_state_version="state-1",
        hazard=_hazard(hazard_id),
        risk=risk or _present_risk(encounter_id=encounter_id),
        recommendations=recommendations or _recommendations(_recommendation()),
    )


def _context(**overrides) -> DecisionEvidence:
    values = {
        "kind": DecisionEvidenceKind.DECISION_CONTEXT,
        "evaluation_state": DecisionEvaluationState.ESTABLISHED,
        "aircraft_in_current_set": True,
        "aircraft_id": "ac-1",
        "projection_state": DecisionReportedLinkState.PRESENT,
        "projection_id": "proj-1",
        "aircraft_state_version": "state-1",
        "encounters": (_encounter(),),
        "capability": _capability(),
        "chain_gaps": (),
        "limitation_codes": (),
    }
    values.update(overrides)
    return DecisionEvidence(**values)


def _risk_evidence(**overrides) -> DecisionEvidence:
    values = {
        "kind": DecisionEvidenceKind.RISK_EVIDENCE,
        "evaluation_state": DecisionEvaluationState.ESTABLISHED,
        "aircraft_in_current_set": True,
        "aircraft_id": "ac-1",
        "projection_state": DecisionReportedLinkState.PRESENT,
        "projection_id": "proj-1",
        "aircraft_state_version": "state-1",
        "encounters": (),
        "risk": _present_risk(),
        "capability": None,
        "chain_gaps": (),
        "limitation_codes": (),
    }
    values.update(overrides)
    return DecisionEvidence(**values)


def _recommendation_evidence(**overrides) -> DecisionEvidence:
    values = {
        "kind": DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
        "evaluation_state": DecisionEvaluationState.ESTABLISHED,
        "aircraft_in_current_set": True,
        "aircraft_id": "ac-1",
        "projection_state": DecisionReportedLinkState.PRESENT,
        "projection_id": "proj-1",
        "aircraft_state_version": "state-1",
        "encounters": (),
        "risk": _present_risk(),
        "recommendations": _recommendations(_recommendation()),
        "capability": None,
        "chain_gaps": (),
        "limitation_codes": (),
    }
    values.update(overrides)
    return DecisionEvidence(**values)


def _packet(
    tool_call_id: str,
    *,
    scope: TemporalScope = TemporalScope.CURRENT,
    limitations: tuple[str, ...] = (),
) -> Evidence:
    return Evidence(
        source="wilvor.decision.verifier",
        source_records=(
            SourceRecord(record_id="ac-1", source_version=None, event_timestamp_utc=None),
        ),
        query_timestamp_utc=AS_OF,
        freshness_status=FreshnessStatus.FRESH,
        confidence=ConfidenceLevel.UNKNOWN,
        limitations=limitations,
        tool_call_id=tool_call_id,
        temporal_scope=scope,
    )


def _current_result(
    evidence: DecisionEvidence,
    tool_name: str,
    tool_call_id: str = "call-1",
    *,
    limitations: tuple[str, ...] = (),
    as_of: str | None = AS_OF,
    correlation_id: str | None = None,
    status: ToolResultStatus | None = None,
    include_evidence: bool = True,
) -> ToolResult:
    resolved = expected_decision_status(evidence) if status is None else status
    if resolved in {
        ToolResultStatus.PARTIAL,
        ToolResultStatus.UNAVAILABLE,
        ToolResultStatus.UNKNOWN,
        ToolResultStatus.STALE,
    } and not limitations:
        limitations = ("Stored limitation.",)
    return ToolResult(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        status=resolved,
        temporal_scope=TemporalScope.CURRENT,
        data=evidence.to_dict(),
        evidence=(_packet(tool_call_id),) if include_evidence else (),
        as_of_utc=as_of,
        limitations=limitations,
        correlation_id=correlation_id,
    )


def _candidate(
    status: str = "COMPLETE",
    airport_id: str = "KDEN",
    assessment_id: str = "aa-1",
) -> PersistedAirportCandidate:
    scores = {}
    if status == "COMPLETE":
        scores = {
            "rank": 1,
            "total_airport_score": 80,
            "distance_score": 70,
            "weather_score": 60,
            "taf_score": 50,
        }
    return PersistedAirportCandidate(
        airport_id=airport_id,
        airport_assessment_id=assessment_id,
        risk_id="risk-1",
        assessment_status=status,
        route_safety_status="UNAVAILABLE",
        runway_evidence_status="UNAVAILABLE",
        congestion_evidence_status="UNAVAILABLE",
        created_at_utc=AS_OF,
        created_at_epoch=1,
        expires_at_epoch=2,
        evaluation_version="ruleset-1",
        assessment_ruleset_version="ruleset-1",
        schema_version="wilvor.airport_assessment.v1",
        distance_nm=40,
        eta_minutes=20,
        candidate_reason="Within diversion search radius.",
        known_limitations=("Route hazard evaluation is not implemented yet.",),
        **scores,
    )


def _persisted_payload(**overrides) -> PersistedAirportEvidence:
    values = {
        "recommendation_id": "rec-1",
        "airport_evaluation_id": "eval-1",
        "candidates": (_candidate(),),
    }
    values.update(overrides)
    return PersistedAirportEvidence(**values)


def _persisted_result(
    payload: PersistedAirportEvidence,
    tool_call_id: str = "call-p",
    *,
    status: ToolResultStatus = ToolResultStatus.SUCCESS,
    limitations: tuple[str, ...] = (),
    evidence_limitations: tuple[str, ...] = (NOT_CURRENT_LIMITATION,),
    include_evidence: bool = True,
    as_of: str | None = None,
) -> ToolResult:
    if status in {
        ToolResultStatus.PARTIAL,
        ToolResultStatus.UNAVAILABLE,
        ToolResultStatus.UNKNOWN,
    } and not limitations:
        limitations = ("Stored persisted limitation.",)
    evidence = ()
    if include_evidence:
        evidence = (
            _packet(
                tool_call_id,
                scope=TemporalScope.PERSISTED,
                limitations=evidence_limitations,
            ),
        )
    return ToolResult(
        tool_name=PERSISTED_TOOL,
        tool_call_id=tool_call_id,
        status=status,
        temporal_scope=TemporalScope.PERSISTED,
        data=payload.to_dict(),
        evidence=evidence,
        as_of_utc=as_of,
        limitations=limitations,
    )


def _bind(ref: str, result: ToolResult) -> DecisionEvidenceBinding:
    return DecisionEvidenceBinding(ref, result)


def _identity(ref: str = "de-1", aircraft_id: str = "ac-1") -> AircraftIdentityClaim:
    return AircraftIdentityClaim(ref, TemporalScope.CURRENT, aircraft_id)


def _risk_present(
    ref: str = "de-1",
    *,
    level: StoredRiskLevel = StoredRiskLevel.HIGH,
    score: int = 80,
    risk_id: str = "risk-1",
    encounter_id: str | None = "enc-1",
) -> RiskPresentClaim:
    return RiskPresentClaim(
        evidence_ref=ref,
        evidence_scope=TemporalScope.CURRENT,
        risk_id=risk_id,
        risk_level=level,
        risk_score=score,
        encounter_id=encounter_id,
    )


def _risk_absent(ref: str = "de-1", encounter_id: str | None = "enc-1") -> RiskAbsentClaim:
    return RiskAbsentClaim(ref, TemporalScope.CURRENT, encounter_id)


def _recommendation_set(
    ref: str = "de-1",
    ids: tuple[str, ...] = ("rec-1",),
) -> RecommendationSetClaim:
    return RecommendationSetClaim(
        evidence_ref=ref,
        evidence_scope=TemporalScope.CURRENT,
        recommendation_ids=ids,
        absence_state=(
            DecisionReportedLinkState.PRESENT
            if ids
            else DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES
        ),
    )


def _action(
    ref: str = "de-1",
    recommendation_id: str = "rec-1",
    action: RecommendationActionType = RecommendationActionType.MONITOR,
) -> RecommendationActionClaim:
    return RecommendationActionClaim(
        evidence_ref=ref,
        evidence_scope=TemporalScope.CURRENT,
        recommendation_id=recommendation_id,
        primary_action_type=action,
        advisory_authority=DecisionAdvisoryAuthority.ADVISORY_ONLY,
    )


def _candidate_claim(
    ref: str = "de-2",
    *,
    status: PersistedAssessmentStatus = PersistedAssessmentStatus.COMPLETE,
    airport_id: str = "KDEN",
    assessment_id: str = "aa-1",
) -> PersistedCandidateClaim:
    return PersistedCandidateClaim(
        evidence_ref=ref,
        evidence_scope=TemporalScope.PERSISTED,
        recommendation_id="rec-1",
        airport_id=airport_id,
        airport_assessment_id=assessment_id,
        assessment_status=status,
        route_safety_status=_UNAVAILABLE,
        runway_evidence_status=_UNAVAILABLE,
        congestion_evidence_status=_UNAVAILABLE,
    )


def _verify(claims, bindings):
    return verify_decision_evidence(tuple(claims), tuple(bindings))


def _assert_failed(result: DecisionVerificationResult, *codes: str) -> None:
    assert result.outcome is VerifierOutcome.FAILED
    assert result.verified_claims == ()
    assert result.rejected_claim_codes == codes
    assert result.used_evidence_refs == ()
    assert result.verified_temporal_scopes == ()
    assert result.current_as_of_utc is None
    assert CLAIM_VERIFICATION_FAILED in result.mandatory_limitations


def _not_found_risk() -> DecisionEvidence:
    return _risk_evidence(
        aircraft_in_current_set=False,
        projection_state=DecisionReportedLinkState.MISSING,
        projection_id=None,
        aircraft_state_version=None,
        risk=_absent_risk(),
        chain_gaps=(
            DecisionChainGap.AIRCRAFT_NOT_IN_CURRENT_SET,
            DecisionChainGap.RISK_ABSENT,
        ),
    )


def _not_found_recommendation() -> DecisionEvidence:
    return _recommendation_evidence(
        aircraft_in_current_set=False,
        projection_state=DecisionReportedLinkState.MISSING,
        projection_id=None,
        aircraft_state_version=None,
        risk=_absent_risk(),
        recommendations=_recommendations(),
        chain_gaps=(
            DecisionChainGap.AIRCRAFT_NOT_IN_CURRENT_SET,
            DecisionChainGap.RISK_ABSENT,
            DecisionChainGap.RECOMMENDATION_ABSENT,
        ),
        limitation_codes=(DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION,),
    )


def test_producer_collection_strings_match_verifier_constants() -> None:
    assert EMPTY_ASSESSMENTS_REASON == EMPTY_ASSESSMENTS
    assert NO_COMPLETE_ASSESSMENT_REASON == NO_COMPLETE_ASSESSMENT
    assert NOT_CURRENT_LIMITATION == PRODUCER_NOT_CURRENT


def test_binding_rejects_malformed_ref_and_non_tool_result() -> None:
    assert "invalid_evidence_ref" in _errors(lambda: DecisionEvidenceBinding("de-0", _current_result(_context(), CONTEXT_TOOL)))
    assert "invalid_tool_result" in _errors(lambda: DecisionEvidenceBinding("de-1", object()))


def test_verify_rejects_non_tuples() -> None:
    binding = _bind("de-1", _current_result(_context(), CONTEXT_TOOL))
    with pytest.raises(TypeError):
        verify_decision_evidence([_identity()], (binding,))
    with pytest.raises(TypeError):
        verify_decision_evidence((_identity(),), [binding])


def test_empty_claim_set_fails() -> None:
    result = _verify((), ())
    _assert_failed(result, DecisionClaimRejectionCode.EMPTY_CLAIM_SET.value)
    assert result.mandatory_limitations == (CLAIM_VERIFICATION_FAILED,)


def test_limitation_only_is_not_actionable() -> None:
    evidence = _context(limitation_codes=(DecisionLimitationCode.NO_SNAPSHOT_LIMITATION,))
    claim = LimitationClaim(
        "de-1",
        TemporalScope.CURRENT,
        DecisionLimitationCode.NO_SNAPSHOT_LIMITATION,
    )
    result = _verify((claim,), (_bind("de-1", _current_result(evidence, CONTEXT_TOOL)),))
    _assert_failed(result, DecisionClaimRejectionCode.CLAIM_SET_NOT_ACTIONABLE.value)
    assert result.mandatory_limitations == (
        CLAIM_VERIFICATION_FAILED,
        CLAIM_SET_NOT_ACTIONABLE,
    )


def test_aircraft_identity_exact_pass_and_fail() -> None:
    result = _verify(
        (_identity(),),
        (_bind("de-1", _current_result(_context(), CONTEXT_TOOL)),),
    )
    assert result.outcome is VerifierOutcome.PASSED
    assert result.verified_claims == (_identity(),)
    assert result.rejected_claim_codes == ()
    assert result.current_as_of_utc == AS_OF
    assert result.verified_temporal_scopes == (TemporalScope.CURRENT,)

    failed = _verify(
        (_identity(aircraft_id="ac-2"),),
        (_bind("de-1", _current_result(_context(), CONTEXT_TOOL)),),
    )
    _assert_failed(failed, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)


def test_source_unavailable_does_not_verify_identity() -> None:
    evidence = _context(
        evaluation_state=DecisionEvaluationState.SOURCE_UNAVAILABLE,
        aircraft_in_current_set=False,
        aircraft_id=None,
        projection_state=DecisionReportedLinkState.MISSING,
        projection_id=None,
        aircraft_state_version=None,
        encounters=(),
        chain_gaps=(),
    )
    result = _verify(
        (_identity(),),
        (_bind("de-1", _current_result(evidence, CONTEXT_TOOL)),),
    )
    _assert_failed(result, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)


def test_evaluation_state_exact_pass_and_fail() -> None:
    claim = EvaluationStateClaim(
        "de-1",
        TemporalScope.CURRENT,
        DecisionEvaluationState.ESTABLISHED,
    )
    passed = _verify((claim,), (_bind("de-1", _current_result(_context(), CONTEXT_TOOL)),))
    assert passed.outcome is VerifierOutcome.PASSED
    wrong = EvaluationStateClaim(
        "de-1",
        TemporalScope.CURRENT,
        DecisionEvaluationState.SOURCE_UNAVAILABLE,
    )
    failed = _verify((wrong,), (_bind("de-1", _current_result(_context(), CONTEXT_TOOL)),))
    _assert_failed(failed, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)


def test_context_with_zero_encounters_verifies_empty_encounter_set() -> None:
    evidence = _context(
        encounters=(),
        chain_gaps=(DecisionChainGap.NO_CURRENT_ENCOUNTER,),
    )
    claim = EncounterSetClaim("de-1", TemporalScope.CURRENT, ())
    result = _verify((claim,), (_bind("de-1", _current_result(evidence, CONTEXT_TOOL)),))
    assert result.outcome is VerifierOutcome.PASSED
    assert result.verified_claims[0].encounter_ids == ()


def test_encounter_set_must_be_complete_and_context_only() -> None:
    evidence = _context(
        encounters=(
            _encounter("enc-2", _present_risk(encounter_id="enc-2", risk_id="risk-2"), _recommendations(_recommendation("rec-2")), "haz-2"),
            _encounter("enc-1"),
        )
    )
    complete = EncounterSetClaim("de-1", TemporalScope.CURRENT, ("enc-1", "enc-2"))
    passed = _verify((complete,), (_bind("de-1", _current_result(evidence, CONTEXT_TOOL)),))
    assert passed.outcome is VerifierOutcome.PASSED

    subset = EncounterSetClaim("de-1", TemporalScope.CURRENT, ("enc-1",))
    failed = _verify((subset,), (_bind("de-1", _current_result(evidence, CONTEXT_TOOL)),))
    _assert_failed(failed, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)


def test_narrow_tools_reject_encounter_set() -> None:
    risk = _bind("de-1", _current_result(_risk_evidence(), RISK_TOOL, "call-r"))
    recommendation = _bind(
        "de-2",
        _current_result(_recommendation_evidence(), RECOMMENDATION_TOOL, "call-m"),
    )
    risk_claim = EncounterSetClaim("de-1", TemporalScope.CURRENT, ())
    recommendation_claim = EncounterSetClaim("de-2", TemporalScope.CURRENT, ())
    risk_result = _verify((risk_claim,), (risk,))
    recommendation_result = _verify((recommendation_claim,), (recommendation,))
    _assert_failed(risk_result, DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH.value)
    _assert_failed(recommendation_result, DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH.value)


def test_not_found_context_does_not_verify_empty_encounter_set() -> None:
    evidence = _context(
        aircraft_in_current_set=False,
        projection_state=DecisionReportedLinkState.MISSING,
        projection_id=None,
        aircraft_state_version=None,
        encounters=(),
        chain_gaps=(DecisionChainGap.AIRCRAFT_NOT_IN_CURRENT_SET,),
    )
    claim = EncounterSetClaim("de-1", TemporalScope.CURRENT, ())
    result = _verify((claim,), (_bind("de-1", _current_result(evidence, CONTEXT_TOOL)),))
    _assert_failed(result, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)
    status = _verify(
        (ToolStatusClaim("de-1", TemporalScope.CURRENT, ToolResultStatus.NOT_FOUND),),
        (_bind("de-1", _current_result(evidence, CONTEXT_TOOL)),),
    )
    assert status.outcome is VerifierOutcome.PASSED
    gap = _verify(
        (
            ChainGapClaim(
                "de-1",
                TemporalScope.CURRENT,
                DecisionChainGap.AIRCRAFT_NOT_IN_CURRENT_SET,
            ),
        ),
        (_bind("de-1", _current_result(evidence, CONTEXT_TOOL)),),
    )
    assert gap.outcome is VerifierOutcome.PASSED


@pytest.mark.parametrize(
    ("level", "score"),
    (
        (StoredRiskLevel.LOW, 12),
        (StoredRiskLevel.MEDIUM, 40),
        (StoredRiskLevel.HIGH, 80),
    ),
)
def test_present_risk_levels_match_exactly(level: StoredRiskLevel, score: int) -> None:
    evidence = _risk_evidence(risk=_present_risk(level=level, score=score))
    claim = _risk_present(level=level, score=score)
    result = _verify((claim,), (_bind("de-1", _current_result(evidence, RISK_TOOL)),))
    assert result.outcome is VerifierOutcome.PASSED
    assert result.verified_claims[0].risk_level is level
    assert result.verified_claims[0].risk_score == score


def test_absent_is_not_low_and_mismatches_fail() -> None:
    absent = _risk_evidence(
        risk=_absent_risk("enc-1"),
        chain_gaps=(DecisionChainGap.RISK_ABSENT,),
    )
    binding = _bind("de-1", _current_result(absent, RISK_TOOL))
    low = _verify((_risk_present(level=StoredRiskLevel.LOW, score=12),), (binding,))
    _assert_failed(low, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)
    proved_absent = _verify((_risk_absent(),), (binding,))
    assert proved_absent.outcome is VerifierOutcome.PASSED

    present = _bind("de-1", _current_result(_risk_evidence(), RISK_TOOL))
    score = _verify((_risk_present(score=81),), (present,))
    risk_id = _verify((_risk_present(risk_id="risk-other"),), (present,))
    _assert_failed(score, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)
    _assert_failed(risk_id, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)


def test_not_found_structural_absent_does_not_verify_risk_absence() -> None:
    result = _verify(
        (_risk_absent(encounter_id=None),),
        (_bind("de-1", _current_result(_not_found_risk(), RISK_TOOL)),),
    )
    _assert_failed(result, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)
    status = _verify(
        (ToolStatusClaim("de-1", TemporalScope.CURRENT, ToolResultStatus.NOT_FOUND),),
        (_bind("de-1", _current_result(_not_found_risk(), RISK_TOOL)),),
    )
    assert status.outcome is VerifierOutcome.PASSED
    assert status.verified_claims[0].status is ToolResultStatus.NOT_FOUND


def test_omitted_encounter_is_exact_only_when_one_fact_exists() -> None:
    one = _verify(
        (_risk_present(encounter_id=None),),
        (_bind("de-1", _current_result(_risk_evidence(), RISK_TOOL)),),
    )
    assert one.outcome is VerifierOutcome.PASSED

    many = _context(
        encounters=(
            _encounter("enc-1"),
            _encounter(
                "enc-2",
                _present_risk(
                    level=StoredRiskLevel.MEDIUM,
                    score=40,
                    risk_id="risk-2",
                    encounter_id="enc-2",
                ),
                _recommendations(_recommendation("rec-2")),
                "haz-2",
            ),
        )
    )
    ambiguous = _verify(
        (_risk_present(encounter_id=None),),
        (_bind("de-1", _current_result(many, CONTEXT_TOOL)),),
    )
    _assert_failed(ambiguous, DecisionClaimRejectionCode.AMBIGUOUS_CLAIM_TARGET.value)
    named = _verify(
        (_risk_present(),),
        (_bind("de-1", _current_result(many, CONTEXT_TOOL)),),
    )
    assert named.outcome is VerifierOutcome.PASSED


def test_recommendation_set_is_complete_and_empty_is_not_monitor() -> None:
    evidence = _context(
        encounters=(
            _encounter(
                recommendations=_recommendations(
                    _recommendation("rec-2"),
                    _recommendation("rec-1", RecommendationActionType.EVALUATE_DIVERSION),
                )
            ),
        )
    )
    passed = _verify(
        (_recommendation_set(ids=("rec-1", "rec-2")),),
        (_bind("de-1", _current_result(evidence, CONTEXT_TOOL)),),
    )
    assert passed.outcome is VerifierOutcome.PASSED
    subset = _verify(
        (_recommendation_set(ids=("rec-1",)),),
        (_bind("de-1", _current_result(evidence, CONTEXT_TOOL)),),
    )
    _assert_failed(subset, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)

    empty_evidence = _recommendation_evidence(
        recommendations=_recommendations(),
        chain_gaps=(DecisionChainGap.RECOMMENDATION_ABSENT,),
        limitation_codes=(DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION,),
    )
    empty = _verify(
        (_recommendation_set("de-1", ()),),
        (_bind("de-1", _current_result(empty_evidence, RECOMMENDATION_TOOL)),),
    )
    assert empty.outcome is VerifierOutcome.PASSED
    assert empty.verified_claims[0].absence_state is (
        DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES
    )
    assert empty.verified_claims[0].recommendation_ids == ()
    monitor = _verify(
        (_action(),),
        (_bind("de-1", _current_result(empty_evidence, RECOMMENDATION_TOOL)),),
    )
    _assert_failed(monitor, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)


def test_not_found_empty_recommendation_object_is_not_absence() -> None:
    result = _verify(
        (_recommendation_set("de-1", ()),),
        (_bind("de-1", _current_result(_not_found_recommendation(), RECOMMENDATION_TOOL)),),
    )
    _assert_failed(result, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)


def test_recommendation_action_matches_advisory_diversion_without_selection() -> None:
    evidence = _recommendation_evidence(
        recommendations=_recommendations(
            _recommendation("rec-1", RecommendationActionType.EVALUATE_DIVERSION)
        )
    )
    claim = _action(action=RecommendationActionType.EVALUATE_DIVERSION)
    result = _verify((claim,), (_bind("de-1", _current_result(evidence, RECOMMENDATION_TOOL)),))
    assert result.outcome is VerifierOutcome.PASSED
    verified = result.verified_claims[0]
    assert verified.primary_action_type is RecommendationActionType.EVALUATE_DIVERSION
    assert verified.advisory_authority is DecisionAdvisoryAuthority.ADVISORY_ONLY
    assert "selected_diversion" not in verified.to_dict()


def test_risk_tool_cannot_support_a_recommendation_claim() -> None:
    evidence = _risk_evidence(
        encounters=(
            _encounter(),
            _encounter(
                "enc-2",
                _present_risk(risk_id="risk-2", encounter_id="enc-2"),
                _recommendations(_recommendation("rec-2")),
                "haz-2",
            ),
        ),
        risk=None,
    )
    result = _verify(
        (_recommendation_set(ids=("rec-1", "rec-2")),),
        (_bind("de-1", _current_result(evidence, RISK_TOOL)),),
    )
    _assert_failed(result, DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH.value)


def test_chain_gap_and_capability_gap_membership() -> None:
    evidence = _context(
        encounters=(),
        chain_gaps=(DecisionChainGap.NO_CURRENT_ENCOUNTER,),
    )
    gap = ChainGapClaim("de-1", TemporalScope.CURRENT, DecisionChainGap.NO_CURRENT_ENCOUNTER)
    capability = CapabilityGapClaim(
        "de-1",
        TemporalScope.CURRENT,
        DecisionCapabilityGap.ROUTE_ALTERNATIVE_NOT_IMPLEMENTED,
    )
    passed = _verify(
        (gap, capability),
        (_bind("de-1", _current_result(evidence, CONTEXT_TOOL)),),
    )
    assert passed.outcome is VerifierOutcome.PASSED
    missing = ChainGapClaim("de-1", TemporalScope.CURRENT, DecisionChainGap.RISK_ABSENT)
    failed = _verify((missing,), (_bind("de-1", _current_result(evidence, CONTEXT_TOOL)),))
    _assert_failed(failed, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)
    narrow = _verify(
        (
            CapabilityGapClaim(
                "de-1",
                TemporalScope.CURRENT,
                DecisionCapabilityGap.RUNWAY_EVIDENCE_UNAVAILABLE,
            ),
        ),
        (_bind("de-1", _current_result(_risk_evidence(), RISK_TOOL)),),
    )
    _assert_failed(narrow, DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH.value)


def test_limitation_code_ignores_tool_result_prose() -> None:
    evidence = _context(limitation_codes=(DecisionLimitationCode.NO_SNAPSHOT_LIMITATION,))
    result = _current_result(
        evidence,
        CONTEXT_TOOL,
        limitations=(DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION.value,),
    )
    prose = LimitationClaim(
        "de-1",
        TemporalScope.CURRENT,
        DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION,
    )
    failed = _verify((prose, _identity()), (_bind("de-1", result),))
    _assert_failed(failed, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)
    code = LimitationClaim(
        "de-1",
        TemporalScope.CURRENT,
        DecisionLimitationCode.NO_SNAPSHOT_LIMITATION,
    )
    passed = _verify((_identity(), code), (_bind("de-1", result),))
    assert passed.outcome is VerifierOutcome.PASSED
    assert passed.mandatory_limitations == (
        DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION.value,
    )


def test_tool_status_is_exact_for_current_and_persisted() -> None:
    unavailable = _context(
        evaluation_state=DecisionEvaluationState.SOURCE_UNAVAILABLE,
        aircraft_in_current_set=False,
        aircraft_id=None,
        projection_state=DecisionReportedLinkState.MISSING,
        projection_id=None,
        aircraft_state_version=None,
        encounters=(),
        chain_gaps=(),
    )
    claim = ToolStatusClaim("de-1", TemporalScope.CURRENT, ToolResultStatus.UNAVAILABLE)
    passed = _verify((claim,), (_bind("de-1", _current_result(unavailable, CONTEXT_TOOL)),))
    assert passed.outcome is VerifierOutcome.PASSED
    assert passed.verified_claims[0].status is ToolResultStatus.UNAVAILABLE

    wrong = ToolStatusClaim("de-1", TemporalScope.CURRENT, ToolResultStatus.SUCCESS)
    failed = _verify((wrong,), (_bind("de-1", _current_result(unavailable, CONTEXT_TOOL)),))
    _assert_failed(failed, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)

    payload = _persisted_payload()
    persisted_claim = ToolStatusClaim("de-2", TemporalScope.PERSISTED, ToolResultStatus.SUCCESS)
    persisted = _verify(
        (persisted_claim,),
        (_bind("de-2", _persisted_result(payload)),),
    )
    assert persisted.outcome is VerifierOutcome.PASSED
    assert persisted.verified_temporal_scopes == (TemporalScope.PERSISTED,)
    assert persisted.current_as_of_utc is None
    mismatched_scope = ToolStatusClaim("de-2", TemporalScope.CURRENT, ToolResultStatus.SUCCESS)
    scope_failed = _verify((mismatched_scope,), (_bind("de-2", _persisted_result(payload)),))
    _assert_failed(scope_failed, DecisionClaimRejectionCode.CLAIM_SCOPE_MISMATCH.value)


def test_persisted_evaluation_candidate_and_status() -> None:
    payload = _persisted_payload()
    evaluation = PersistedEvaluationClaim(
        "de-2",
        TemporalScope.PERSISTED,
        "rec-1",
        "eval-1",
        PersistedEvaluationScope.PERSISTED_EVALUATION_EVIDENCE,
    )
    status = PersistedCandidateStatusClaim(
        "de-2",
        TemporalScope.PERSISTED,
        "rec-1",
        1,
        None,
    )
    result = _verify(
        (evaluation, _candidate_claim(), status),
        (_bind("de-2", _persisted_result(payload, limitations=("membership note.",))),),
    )
    assert result.outcome is VerifierOutcome.PASSED
    candidate = result.verified_claims[1]
    assert candidate.assessment_status is PersistedAssessmentStatus.COMPLETE
    assert candidate.route_safety_status is _UNAVAILABLE
    assert "rank" not in candidate.to_dict()
    assert "safe" not in candidate.to_dict()
    assert result.mandatory_limitations == ("membership note.", NOT_CURRENT_LIMITATION)

    wrong_count = PersistedCandidateStatusClaim(
        "de-2",
        TemporalScope.PERSISTED,
        "rec-1",
        0,
        None,
    )
    counted = _verify((wrong_count,), (_bind("de-2", _persisted_result(payload)),))
    _assert_failed(counted, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)


def test_null_evaluation_id_matches_only_null_and_not_found_does_not() -> None:
    payload = _persisted_payload(airport_evaluation_id=None, candidates=())
    claim = PersistedEvaluationClaim(
        "de-2",
        TemporalScope.PERSISTED,
        "rec-1",
        None,
        PersistedEvaluationScope.PERSISTED_EVALUATION_EVIDENCE,
    )
    passed = _verify((claim,), (_bind("de-2", _persisted_result(payload)),))
    assert passed.outcome is VerifierOutcome.PASSED
    present = PersistedEvaluationClaim(
        "de-2",
        TemporalScope.PERSISTED,
        "rec-1",
        "eval-1",
        PersistedEvaluationScope.PERSISTED_EVALUATION_EVIDENCE,
    )
    failed = _verify((present,), (_bind("de-2", _persisted_result(payload)),))
    _assert_failed(failed, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)

    missing = _persisted_result(
        _persisted_payload(airport_evaluation_id=None, candidates=()),
        status=ToolResultStatus.NOT_FOUND,
        limitations=("The recommendation was not found.",),
        include_evidence=False,
    )
    not_found = _verify((claim,), (_bind("de-2", missing),))
    _assert_failed(not_found, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)


def test_waiting_for_weather_and_collection_reasons() -> None:
    waiting = _persisted_payload(
        candidates=(_candidate("WAITING_FOR_WEATHER"),),
        no_suitable_candidate_reason=NO_COMPLETE_ASSESSMENT_REASON,
    )
    claim = _candidate_claim(status=PersistedAssessmentStatus.WAITING_FOR_WEATHER)
    status = PersistedCandidateStatusClaim(
        "de-2",
        TemporalScope.PERSISTED,
        "rec-1",
        1,
        PersistedCandidateCollectionReason.NO_COMPLETE_ASSESSMENT,
    )
    result = _verify(
        (claim, status),
        (
            _bind(
                "de-2",
                _persisted_result(waiting, status=ToolResultStatus.PARTIAL),
            ),
        ),
    )
    assert result.outcome is VerifierOutcome.PASSED
    assert result.verified_claims[0].assessment_status is PersistedAssessmentStatus.WAITING_FOR_WEATHER

    empty = _persisted_payload(
        candidates=(),
        no_suitable_candidate_reason=EMPTY_ASSESSMENTS_REASON,
        recommendation_observed_empty_at_utc=AS_OF,
    )
    empty_status = PersistedCandidateStatusClaim(
        "de-2",
        TemporalScope.PERSISTED,
        "rec-1",
        0,
        PersistedCandidateCollectionReason.EMPTY_ASSESSMENTS,
    )
    empty_result = _verify((empty_status,), (_bind("de-2", _persisted_result(empty)),))
    assert empty_result.outcome is VerifierOutcome.PASSED


def test_similar_collection_prose_does_not_map() -> None:
    payload = _persisted_payload(
        candidates=(),
        no_suitable_candidate_reason="No candidate airport assessments were available.",
    )
    claim = PersistedCandidateStatusClaim(
        "de-2",
        TemporalScope.PERSISTED,
        "rec-1",
        0,
        PersistedCandidateCollectionReason.EMPTY_ASSESSMENTS,
    )
    result = _verify((claim,), (_bind("de-2", _persisted_result(payload)),))
    _assert_failed(result, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)


def test_empty_reason_with_candidates_is_raw_invalid() -> None:
    payload = _persisted_payload(
        no_suitable_candidate_reason=EMPTY_ASSESSMENTS_REASON,
    )
    result = _verify(
        (ToolStatusClaim("de-2", TemporalScope.PERSISTED, ToolResultStatus.SUCCESS),),
        (_bind("de-2", _persisted_result(payload)),),
    )
    _assert_failed(result, DecisionClaimRejectionCode.RAW_TOOL_RESULT_INVALID.value)


def test_no_complete_reason_with_complete_candidate_is_raw_invalid() -> None:
    payload = _persisted_payload(
        no_suitable_candidate_reason=NO_COMPLETE_ASSESSMENT_REASON,
    )
    result = _verify(
        (_candidate_claim(),),
        (_bind("de-2", _persisted_result(payload, status=ToolResultStatus.PARTIAL)),),
    )
    _assert_failed(result, DecisionClaimRejectionCode.RAW_TOOL_RESULT_INVALID.value)


def test_duplicate_candidate_identity_is_ambiguous() -> None:
    payload = _persisted_payload(
        candidates=(
            _candidate("COMPLETE", "KDEN", "aa-1"),
            _candidate("WAITING_FOR_WEATHER", "KDEN", "aa-2"),
        )
    )
    result = _verify(
        (_candidate_claim(),),
        (_bind("de-2", _persisted_result(payload)),),
    )
    _assert_failed(result, DecisionClaimRejectionCode.AMBIGUOUS_CLAIM_TARGET.value)


def test_current_persisted_link_and_rejections() -> None:
    current = _bind("de-1", _current_result(_context(), CONTEXT_TOOL, "call-c"))
    persisted = _bind("de-2", _persisted_result(_persisted_payload(), "call-p"))
    link = CurrentPersistedLinkClaim("de-1", "de-2", "rec-1")
    passed = _verify((link,), (current, persisted))
    assert passed.outcome is VerifierOutcome.PASSED
    assert passed.verified_temporal_scopes == (TemporalScope.CURRENT, TemporalScope.PERSISTED)
    assert passed.current_as_of_utc == AS_OF
    assert passed.used_evidence_refs == ("de-1", "de-2")
    assert TemporalScope.HYBRID not in passed.verified_temporal_scopes

    mismatched = _verify(
        (CurrentPersistedLinkClaim("de-1", "de-2", "rec-9"),),
        (current, persisted),
    )
    _assert_failed(mismatched, DecisionClaimRejectionCode.CURRENT_PERSISTED_LINK_MISMATCH.value)

    not_found = _bind(
        "de-2",
        _persisted_result(
            _persisted_payload(airport_evaluation_id=None, candidates=()),
            "call-p",
            status=ToolResultStatus.NOT_FOUND,
            limitations=("The recommendation was not found.",),
            include_evidence=False,
        ),
    )
    hidden = _verify((link,), (current, not_found))
    _assert_failed(hidden, DecisionClaimRejectionCode.CURRENT_PERSISTED_LINK_MISMATCH.value)

    absent_current = _context(
        encounters=(_encounter(recommendations=_recommendations()),),
        chain_gaps=(DecisionChainGap.RECOMMENDATION_ABSENT,),
        limitation_codes=(DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION,),
    )
    missing_recommendation = _verify(
        (link,),
        (
            _bind("de-1", _current_result(absent_current, CONTEXT_TOOL, "call-c")),
            persisted,
        ),
    )
    _assert_failed(
        missing_recommendation,
        DecisionClaimRejectionCode.CURRENT_PERSISTED_LINK_MISMATCH.value,
    )


def test_partial_persisted_evaluation_can_link_without_becoming_current() -> None:
    payload = _persisted_payload(
        candidates=(),
        no_suitable_candidate_reason=NO_COMPLETE_ASSESSMENT_REASON,
    )
    link = CurrentPersistedLinkClaim("de-1", "de-2", "rec-1")
    result = _verify(
        (link,),
        (
            _bind("de-1", _current_result(_context(), CONTEXT_TOOL, "call-c")),
            _bind(
                "de-2",
                _persisted_result(payload, "call-p", status=ToolResultStatus.PARTIAL, as_of=AS_OF_OTHER),
            ),
        ),
    )
    assert result.outcome is VerifierOutcome.PASSED
    assert result.verified_temporal_scopes == (TemporalScope.CURRENT, TemporalScope.PERSISTED)


def test_one_false_claim_discards_the_valid_claim() -> None:
    result = _verify(
        (_identity(), _risk_present(score=1)),
        (_bind("de-1", _current_result(_context(), CONTEXT_TOOL)),),
    )
    _assert_failed(result, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)


def test_duplicate_and_contradictory_claims_fail_atomically() -> None:
    binding = _bind("de-1", _current_result(_context(), CONTEXT_TOOL))
    duplicate = _verify((_identity(), _identity()), (binding,))
    _assert_failed(duplicate, DecisionClaimRejectionCode.DUPLICATE_CLAIM.value)
    contradiction = _verify(
        (_risk_present(), _risk_absent()),
        (binding,),
    )
    _assert_failed(contradiction, DecisionClaimRejectionCode.CONTRADICTORY_CLAIMS.value)


def test_unknown_ref_and_duplicate_bindings() -> None:
    result = _current_result(_context(), CONTEXT_TOOL)
    unknown = _verify((_identity(),), ())
    _assert_failed(unknown, DecisionClaimRejectionCode.UNKNOWN_EVIDENCE_REF.value)
    duplicated_ref = _verify(
        (_identity(),),
        (_bind("de-1", result), _bind("de-1", _current_result(_context(), CONTEXT_TOOL, "call-2"))),
    )
    _assert_failed(duplicated_ref, DecisionClaimRejectionCode.DUPLICATE_EVIDENCE_REF.value)
    same_call = _current_result(_context(), CONTEXT_TOOL, "call-1")
    duplicated_call = _verify(
        (_identity("de-1"), _identity("de-2")),
        (_bind("de-1", result), _bind("de-2", same_call)),
    )
    _assert_failed(
        duplicated_call,
        DecisionClaimRejectionCode.DUPLICATE_RAW_TOOL_CALL_ID.value,
    )


def test_agreeing_current_tools_pass() -> None:
    context = _bind("de-1", _current_result(_context(), CONTEXT_TOOL, "call-c", correlation_id="corr-1"))
    risk = _bind("de-2", _current_result(_risk_evidence(), RISK_TOOL, "call-r"))
    recommendation = _bind(
        "de-3",
        _current_result(_recommendation_evidence(), RECOMMENDATION_TOOL, "call-m", correlation_id="corr-1"),
    )
    claims = (
        _identity(),
        EncounterSetClaim("de-1", TemporalScope.CURRENT, ("enc-1",)),
        _risk_present("de-2"),
        _recommendation_set("de-3"),
        _action("de-3"),
    )
    result = _verify(claims, (context, risk, recommendation))
    assert result.outcome is VerifierOutcome.PASSED
    assert result.verified_claims == claims
    assert result.schema_version == DECISION_VERIFICATION_SCHEMA_VERSION
    assert DecisionVerificationResult.from_dict(result.to_dict()) == result


def test_uncited_risk_and_recommendation_disagreements_fail() -> None:
    context = _context()
    risk = _risk_evidence(
        risk=_present_risk(level=StoredRiskLevel.MEDIUM, score=40)
    )
    hidden_risk = _verify(
        (_risk_present(),),
        (
            _bind("de-1", _current_result(context, CONTEXT_TOOL, "call-c")),
            _bind("de-2", _current_result(risk, RISK_TOOL, "call-r")),
        ),
    )
    _assert_failed(hidden_risk, DecisionClaimRejectionCode.CURRENT_RISK_CONTRADICTION.value)

    other_recommendations = _recommendation_evidence(
        recommendations=_recommendations(_recommendation("rec-2"))
    )
    hidden_set = _verify(
        (_recommendation_set(),),
        (
            _bind("de-1", _current_result(context, CONTEXT_TOOL, "call-c")),
            _bind("de-2", _current_result(other_recommendations, RECOMMENDATION_TOOL, "call-m")),
        ),
    )
    _assert_failed(
        hidden_set,
        DecisionClaimRejectionCode.CURRENT_RECOMMENDATION_CONTRADICTION.value,
    )
    other_action = _recommendation_evidence(
        recommendations=_recommendations(
            _recommendation("rec-1", RecommendationActionType.EVALUATE_DIVERSION)
        )
    )
    hidden_action = _verify(
        (_action(),),
        (
            _bind("de-1", _current_result(context, CONTEXT_TOOL, "call-c")),
            _bind("de-2", _current_result(other_action, RECOMMENDATION_TOOL, "call-m")),
        ),
    )
    _assert_failed(
        hidden_action,
        DecisionClaimRejectionCode.CURRENT_RECOMMENDATION_CONTRADICTION.value,
    )


def test_two_aircraft_and_as_of_mismatch_fail() -> None:
    other = _context(aircraft_id="ac-2")
    aircraft = _verify(
        (_identity(),),
        (
            _bind("de-1", _current_result(_context(), CONTEXT_TOOL, "call-1")),
            _bind("de-2", _current_result(other, CONTEXT_TOOL, "call-2")),
        ),
    )
    _assert_failed(aircraft, DecisionClaimRejectionCode.AIRCRAFT_IDENTITY_MISMATCH.value)
    clock = _verify(
        (_identity(),),
        (
            _bind("de-1", _current_result(_context(), CONTEXT_TOOL, "call-1")),
            _bind("de-2", _current_result(_risk_evidence(), RISK_TOOL, "call-2", as_of=AS_OF_OTHER)),
        ),
    )
    _assert_failed(clock, DecisionClaimRejectionCode.CURRENT_AS_OF_MISMATCH.value)


def test_correlation_ids_must_agree_only_when_both_present() -> None:
    mismatch = _verify(
        (_identity(),),
        (
            _bind(
                "de-1",
                _current_result(_context(), CONTEXT_TOOL, "call-1", correlation_id="corr-1"),
            ),
            _bind(
                "de-2",
                _current_result(_risk_evidence(), RISK_TOOL, "call-2", correlation_id="corr-2"),
            ),
        ),
    )
    _assert_failed(mismatch, DecisionClaimRejectionCode.CURRENT_CORRELATION_MISMATCH.value)
    omitted = _verify(
        (_identity(), _risk_present("de-2")),
        (
            _bind(
                "de-1",
                _current_result(_context(), CONTEXT_TOOL, "call-1", correlation_id="corr-1"),
            ),
            _bind("de-2", _current_result(_risk_evidence(), RISK_TOOL, "call-2")),
        ),
    )
    assert omitted.outcome is VerifierOutcome.PASSED


def test_narrow_omission_is_not_a_false_contradiction() -> None:
    claims = (
        EncounterSetClaim("de-1", TemporalScope.CURRENT, ("enc-1",)),
        _recommendation_set(),
        _risk_present("de-2"),
    )
    result = _verify(
        claims,
        (
            _bind("de-1", _current_result(_context(), CONTEXT_TOOL, "call-c")),
            _bind("de-2", _current_result(_risk_evidence(), RISK_TOOL, "call-r")),
        ),
    )
    assert result.outcome is VerifierOutcome.PASSED


def test_lifted_encounter_against_multiple_context_encounters_fails() -> None:
    context = _context(
        encounters=(
            _encounter(),
            _encounter(
                "enc-2",
                _present_risk(risk_id="risk-2", encounter_id="enc-2", level=StoredRiskLevel.LOW, score=12),
                _recommendations(_recommendation("rec-2")),
                "haz-2",
            ),
        )
    )
    result = _verify(
        (_identity(),),
        (
            _bind("de-1", _current_result(context, CONTEXT_TOOL, "call-c")),
            _bind("de-2", _current_result(_risk_evidence(), RISK_TOOL, "call-r")),
        ),
    )
    _assert_failed(result, DecisionClaimRejectionCode.CURRENT_CONTEXT_CONTRADICTION.value)


def test_in_set_disagreement_fails_even_when_uncited() -> None:
    found = _context()
    missing = _context(
        aircraft_in_current_set=False,
        projection_state=DecisionReportedLinkState.MISSING,
        projection_id=None,
        aircraft_state_version=None,
        encounters=(),
        chain_gaps=(DecisionChainGap.AIRCRAFT_NOT_IN_CURRENT_SET,),
    )
    result = _verify(
        (_identity(),),
        (
            _bind("de-1", _current_result(found, CONTEXT_TOOL, "call-1")),
            _bind("de-2", _current_result(missing, CONTEXT_TOOL, "call-2")),
        ),
    )
    _assert_failed(result, DecisionClaimRejectionCode.CURRENT_CONTEXT_CONTRADICTION.value)


def test_malformed_wrong_kind_and_wrong_scope_fail_closed() -> None:
    malformed = ToolResult(
        tool_name=CONTEXT_TOOL,
        tool_call_id="call-1",
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.CURRENT,
        data={},
        evidence=(_packet("call-1"),),
        as_of_utc=AS_OF,
        limitations=(),
    )
    wrong_kind = _current_result(_context(), RISK_TOOL)
    wrong_scope = ToolResult(
        tool_name=CONTEXT_TOOL,
        tool_call_id="call-3",
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.PERSISTED,
        data=_context().to_dict(),
        evidence=(_packet("call-3", scope=TemporalScope.PERSISTED),),
        as_of_utc=AS_OF,
        limitations=(),
    )
    unknown = ToolResult(
        tool_name="list_historical_encounters",
        tool_call_id="call-4",
        status=ToolResultStatus.UNAVAILABLE,
        temporal_scope=TemporalScope.HISTORICAL,
        data=None,
        evidence=(),
        as_of_utc=AS_OF,
        limitations=("unavailable",),
    )
    for result in (malformed, wrong_kind, wrong_scope, unknown):
        verified = _verify((_identity(),), (_bind("de-1", result),))
        _assert_failed(verified, DecisionClaimRejectionCode.RAW_TOOL_RESULT_INVALID.value)

    duplicated = _context(
        encounters=(_encounter("enc-1"), _encounter("enc-1", hazard_id="haz-2"))
    )
    duplicate_ids = _verify(
        (_identity(),),
        (_bind("de-1", _current_result(duplicated, CONTEXT_TOOL)),),
    )
    _assert_failed(duplicate_ids, DecisionClaimRejectionCode.RAW_TOOL_RESULT_INVALID.value)


def test_malformed_persisted_payload_fails_closed() -> None:
    result = ToolResult(
        tool_name=PERSISTED_TOOL,
        tool_call_id="call-p",
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.PERSISTED,
        data={},
        evidence=(_packet("call-p", scope=TemporalScope.PERSISTED),),
        as_of_utc=None,
        limitations=(),
    )
    verified = _verify(
        (ToolStatusClaim("de-2", TemporalScope.PERSISTED, ToolResultStatus.SUCCESS),),
        (_bind("de-2", result),),
    )
    _assert_failed(verified, DecisionClaimRejectionCode.RAW_TOOL_RESULT_INVALID.value)


def test_failure_does_not_copy_raw_limitation_prose() -> None:
    result = _verify(
        (_identity(aircraft_id="ac-2"),),
        (
            _bind(
                "de-1",
                _current_result(_context(), CONTEXT_TOOL, limitations=("Do not copy this prose.",)),
            ),
        ),
    )
    _assert_failed(result, DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value)
    assert result.mandatory_limitations == (CLAIM_VERIFICATION_FAILED,)


def test_limitations_are_deduped_in_first_citation_order() -> None:
    first = _current_result(
        _context(),
        CONTEXT_TOOL,
        "call-1",
        limitations=("alpha", "shared"),
    )
    second = _current_result(
        _risk_evidence(),
        RISK_TOOL,
        "call-2",
        limitations=("shared", "beta"),
    )
    result = _verify(
        (_risk_present("de-2"), _identity("de-1")),
        (_bind("de-1", first), _bind("de-2", second)),
    )
    assert result.outcome is VerifierOutcome.PASSED
    assert result.used_evidence_refs == ("de-2", "de-1")
    assert result.mandatory_limitations == ("shared", "beta", "alpha")


def test_persisted_bindings_for_one_recommendation_must_agree() -> None:
    left = _persisted_result(_persisted_payload(), "call-1")
    right = _persisted_result(
        _persisted_payload(airport_evaluation_id="eval-2"),
        "call-2",
    )
    result = _verify(
        (
            PersistedEvaluationClaim(
                "de-1",
                TemporalScope.PERSISTED,
                "rec-1",
                "eval-1",
                PersistedEvaluationScope.PERSISTED_EVALUATION_EVIDENCE,
            ),
        ),
        (_bind("de-1", left), _bind("de-2", right)),
    )
    _assert_failed(result, DecisionClaimRejectionCode.PERSISTED_EVIDENCE_CONTRADICTION.value)


def test_result_round_trip_rejects_extra_fields_and_bad_invariants() -> None:
    passed = _verify(
        (_identity(),),
        (_bind("de-1", _current_result(_context(), CONTEXT_TOOL)),),
    )
    payload = passed.to_dict()
    payload["explanation"] = "model prose"
    with pytest.raises(ContractValidationError):
        DecisionVerificationResult.from_dict(payload)

    with pytest.raises(ContractValidationError):
        DecisionVerificationResult(
            outcome=VerifierOutcome.PASSED,
            verified_claims=(_identity(),),
            rejected_claim_codes=(),
            mandatory_limitations=(),
            used_evidence_refs=("de-1",),
            verified_temporal_scopes=(TemporalScope.PERSISTED, TemporalScope.CURRENT),
            current_as_of_utc=AS_OF,
        )
    with pytest.raises(ContractValidationError):
        DecisionVerificationResult(
            outcome=VerifierOutcome.PASSED,
            verified_claims=(_identity(),),
            rejected_claim_codes=(),
            mandatory_limitations=(),
            used_evidence_refs=("de-1",),
            verified_temporal_scopes=(TemporalScope.CURRENT,),
            current_as_of_utc=None,
        )
    with pytest.raises(ContractValidationError):
        DecisionVerificationResult(
            outcome=VerifierOutcome.PASSED,
            verified_claims=(_identity(),),
            rejected_claim_codes=(),
            mandatory_limitations=(),
            used_evidence_refs=("de-1",),
            verified_temporal_scopes=(TemporalScope.CURRENT, TemporalScope.CURRENT),
            current_as_of_utc=AS_OF,
        )
    with pytest.raises(ContractValidationError):
        DecisionVerificationResult(
            outcome=VerifierOutcome.FAILED,
            verified_claims=(_identity(),),
            rejected_claim_codes=(DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value,),
            mandatory_limitations=(CLAIM_VERIFICATION_FAILED,),
            used_evidence_refs=(),
            verified_temporal_scopes=(),
            current_as_of_utc=None,
        )
    with pytest.raises(ContractValidationError):
        DecisionVerificationResult(
            outcome=VerifierOutcome.NOT_RUN,
            verified_claims=(),
            rejected_claim_codes=(),
            mandatory_limitations=(),
            used_evidence_refs=(),
            verified_temporal_scopes=(),
            current_as_of_utc=None,
        )
    assert not hasattr(DecisionVerificationResult, "derived_specialist_status")
    assert SpecialistStatus.ANSWERED.value == "ANSWERED"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_verifier_import_isolation() -> None:
    forbidden = {
        "boto3",
        "botocore",
        "anthropic",
        "openai",
        "langgraph",
        "wilvor_operational",
        "wilvor_ai.providers",
        "wilvor_ai.decision_tools",
        "wilvor_ai.decision_context",
        "wilvor_ai.persisted_airport_evidence",
        "wilvor_ai.decision_tool_projection",
        "wilvor_ai.decision_model_contracts",
        "wilvor_ai.historical_evidence_verifier",
        "wilvor_ai.historical_specialist",
    }
    imported = _imported_modules(VERIFIER_PATH)
    for name in forbidden:
        assert name not in imported
        assert not any(item == name or item.startswith(name + ".") for item in imported)

    script = """
import sys
import wilvor_ai.decision_evidence_verifier
forbidden = {
    "boto3",
    "botocore",
    "anthropic",
    "openai",
    "langgraph",
    "wilvor_operational",
    "wilvor_ai.providers",
    "wilvor_ai.decision_tools",
    "wilvor_ai.decision_context",
    "wilvor_ai.persisted_airport_evidence",
    "wilvor_ai.decision_tool_projection",
    "wilvor_ai.decision_model_contracts",
    "wilvor_ai.historical_evidence_verifier",
    "wilvor_ai.historical_specialist",
}
for name in forbidden:
    assert name not in sys.modules
    assert not any(module == name or module.startswith(name + ".") for module in sys.modules)
"""
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
