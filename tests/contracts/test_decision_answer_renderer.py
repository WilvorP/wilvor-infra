"""Deterministic Decision answer renderer contracts."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from wilvor_ai.contracts import ContractValidationError, TemporalScope, ToolResultStatus
from wilvor_ai.decision_answer_renderer import (
    RECOGNIZED_MEMBERSHIP_LIMITATION,
    RECOGNIZED_MISSING_REFERENCED_ROW,
    RECOGNIZED_NOT_CURRENT_LIMITATION,
    RECOGNIZED_NO_SNAPSHOT_LIMITATION,
    RECOGNIZED_RECOMMENDATION_ABSENCE_LIMITATION,
    RECOGNIZED_WAITING_ROWS_ABSENT,
    DecisionRenderOutcome,
    DecisionRenderResult,
    render_decision_verification,
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
    DecisionEvaluationState,
    DecisionLimitationCode,
    DecisionReportedLinkState,
    PersistedEvaluationScope,
    RecommendationActionType,
    StoredRiskLevel,
)
from wilvor_ai.decision_evidence_verifier import (
    CLAIM_SET_NOT_ACTIONABLE,
    CLAIM_VERIFICATION_FAILED,
    DecisionClaimRejectionCode,
    DecisionVerificationResult,
)
from wilvor_ai.specialist_contracts import SPECIALIST_ANSWER_MAX_LENGTH, VerifierOutcome


AS_OF = "2026-09-29T00:00:00Z"
REPO_ROOT = Path(__file__).resolve().parents[2]
RENDERER_PATH = REPO_ROOT / "functions" / "shared" / "wilvor_ai" / "decision_answer_renderer.py"
SHARED_DIR = REPO_ROOT / "functions" / "shared"

_VERIFICATION_FAILED = (
    "Wilvor could not verify the proposed decision claims against deterministic "
    "evidence, so no factual decision answer is being returned."
)
_UNACTIONABLE = (
    "The verified claim set did not contain enough factual evidence to produce "
    "a decision answer."
)
_LENGTH_EXCEEDED = (
    "The verified decision claims exceed the deterministic answer length bound, "
    "so no factual decision answer is being returned."
)
_UNKNOWN_LIMITATION = (
    "Additional deterministic evidence limitations apply and are not expanded "
    "in this answer."
)
_SUITABILITY = "Persisted airport-evaluation evidence is not current airport suitability."
_GAP_SENTENCES = {
    DecisionChainGap.AIRCRAFT_NOT_IN_CURRENT_SET: (
        "The aircraft is not present in the verified current aircraft set."
    ),
    DecisionChainGap.NO_CURRENT_PROJECTION: (
        "No current projection is available in the verified decision chain."
    ),
    DecisionChainGap.NO_CURRENT_ENCOUNTER: (
        "No current encounter is available in the verified decision chain."
    ),
    DecisionChainGap.RISK_ABSENT: (
        "The verified current chain reports stored risk absence. "
        "Risk absence is not a LOW risk classification."
    ),
    DecisionChainGap.RECOMMENDATION_ABSENT: (
        "The verified current chain reports stored recommendation absence. "
        "This is not a MONITOR recommendation."
    ),
    DecisionChainGap.STALE_RECOMMENDATION_EXCLUDED: (
        "Stale recommendation evidence was excluded from the verified current "
        "candidate set."
    ),
    DecisionChainGap.HAZARD_SOURCE_VERSION_MISMATCH: (
        "The current decision chain contains a hazard source-version mismatch."
    ),
    DecisionChainGap.INCOMPLETE_CURRENT_CHAIN: (
        "The verified current decision chain is incomplete."
    ),
}
_CAPABILITY_SENTENCES = {
    DecisionCapabilityGap.ROUTE_ALTERNATIVE_NOT_IMPLEMENTED: (
        "The current Decision Expert does not have validated alternative-route "
        "generation."
    ),
    DecisionCapabilityGap.ROUTE_SAFETY_EVIDENCE_UNAVAILABLE: (
        "Route-safety evidence is unavailable."
    ),
    DecisionCapabilityGap.RUNWAY_EVIDENCE_UNAVAILABLE: "Runway evidence is unavailable.",
    DecisionCapabilityGap.CONGESTION_EVIDENCE_UNAVAILABLE: (
        "Congestion evidence is unavailable."
    ),
}


def _refs(claims: tuple[object, ...]) -> tuple[str, ...]:
    refs: list[str] = []
    for claim in claims:
        if isinstance(claim, CurrentPersistedLinkClaim):
            pair = (claim.current_evidence_ref, claim.persisted_evidence_ref)
        else:
            pair = (claim.evidence_ref,)
        for ref in pair:
            if ref not in refs:
                refs.append(ref)
    return tuple(refs)


def _scopes(claims: tuple[object, ...]) -> tuple[TemporalScope, ...]:
    found = set()
    for claim in claims:
        if isinstance(claim, CurrentPersistedLinkClaim):
            found.update((TemporalScope.CURRENT, TemporalScope.PERSISTED))
        else:
            found.add(claim.evidence_scope)
    return tuple(
        scope for scope in (TemporalScope.CURRENT, TemporalScope.PERSISTED) if scope in found
    )


def _passed(
    claims: tuple[object, ...],
    limitations: tuple[str, ...] = (),
) -> DecisionVerificationResult:
    scopes = _scopes(claims)
    return DecisionVerificationResult(
        outcome=VerifierOutcome.PASSED,
        verified_claims=claims,
        rejected_claim_codes=(),
        mandatory_limitations=limitations,
        used_evidence_refs=_refs(claims),
        verified_temporal_scopes=scopes,
        current_as_of_utc=AS_OF if TemporalScope.CURRENT in scopes else None,
    )


def _failed(*codes: str, limitations: tuple[str, ...] | None = None) -> DecisionVerificationResult:
    collected = [CLAIM_VERIFICATION_FAILED]
    for item in limitations or ():
        if item not in collected:
            collected.append(item)
    if CLAIM_SET_NOT_ACTIONABLE in codes and CLAIM_SET_NOT_ACTIONABLE not in collected:
        collected.append(CLAIM_SET_NOT_ACTIONABLE)
    return DecisionVerificationResult(
        outcome=VerifierOutcome.FAILED,
        verified_claims=(),
        rejected_claim_codes=codes,
        mandatory_limitations=tuple(collected),
        used_evidence_refs=(),
        verified_temporal_scopes=(),
        current_as_of_utc=None,
    )


def _render(claims: tuple[object, ...], limitations: tuple[str, ...] = ()) -> DecisionRenderResult:
    return render_decision_verification(_passed(claims, limitations))


def _identity(aircraft_id: str = "N12345", ref: str = "de-1") -> AircraftIdentityClaim:
    return AircraftIdentityClaim(ref, TemporalScope.CURRENT, aircraft_id)


def _encounter(ids: tuple[str, ...] = ("enc-1",), ref: str = "de-1") -> EncounterSetClaim:
    return EncounterSetClaim(ref, TemporalScope.CURRENT, ids)


def _risk_present(
    *,
    level: StoredRiskLevel = StoredRiskLevel.HIGH,
    score: int = 80,
    risk_id: str = "risk-1",
    encounter_id: str | None = "enc-1",
    ref: str = "de-1",
) -> RiskPresentClaim:
    return RiskPresentClaim(ref, TemporalScope.CURRENT, risk_id, level, score, encounter_id)


def _risk_absent(encounter_id: str | None = "enc-1", ref: str = "de-1") -> RiskAbsentClaim:
    return RiskAbsentClaim(ref, TemporalScope.CURRENT, encounter_id)


def _recommendation_set(
    ids: tuple[str, ...] = ("rec-1",),
    ref: str = "de-1",
) -> RecommendationSetClaim:
    return RecommendationSetClaim(
        ref,
        TemporalScope.CURRENT,
        ids,
        (
            DecisionReportedLinkState.PRESENT
            if ids
            else DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES
        ),
    )


def _action(
    recommendation_id: str = "rec-1",
    action: RecommendationActionType = RecommendationActionType.MONITOR,
    ref: str = "de-1",
) -> RecommendationActionClaim:
    return RecommendationActionClaim(
        ref,
        TemporalScope.CURRENT,
        recommendation_id,
        action,
        DecisionAdvisoryAuthority.ADVISORY_ONLY,
    )


def _candidate(
    status: PersistedAssessmentStatus = PersistedAssessmentStatus.COMPLETE,
    airport_id: str = "KDEN",
    assessment_id: str = "aa-1",
    ref: str = "de-2",
) -> PersistedCandidateClaim:
    unavailable = PersistedCapabilityEvidenceStatus.UNAVAILABLE
    return PersistedCandidateClaim(
        evidence_ref=ref,
        evidence_scope=TemporalScope.PERSISTED,
        recommendation_id="rec-1",
        airport_id=airport_id,
        airport_assessment_id=assessment_id,
        assessment_status=status,
        route_safety_status=unavailable,
        runway_evidence_status=unavailable,
        congestion_evidence_status=unavailable,
    )


def _candidate_status(
    count: int,
    reason: PersistedCandidateCollectionReason | None,
    ref: str = "de-2",
    recommendation_id: str = "rec-1",
) -> PersistedCandidateStatusClaim:
    return PersistedCandidateStatusClaim(
        ref,
        TemporalScope.PERSISTED,
        recommendation_id,
        count,
        reason,
    )


def _evaluation(
    evaluation_id: str | None,
    ref: str = "de-2",
    recommendation_id: str = "rec-1",
) -> PersistedEvaluationClaim:
    return PersistedEvaluationClaim(
        ref,
        TemporalScope.PERSISTED,
        recommendation_id,
        evaluation_id,
        PersistedEvaluationScope.PERSISTED_EVALUATION_EVIDENCE,
    )


def test_same_claims_in_different_order_render_identically() -> None:
    forward = (
        _identity(),
        _encounter(("enc-1", "enc-2")),
        _risk_present(),
        _recommendation_set(("rec-1", "rec-2")),
    )
    backward = tuple(reversed(forward))
    assert _render(forward).answer == _render(backward).answer
    assert _render(forward).outcome is DecisionRenderOutcome.FACTUAL


def test_failed_verification_returns_no_facts() -> None:
    secret = "aircraft N99999 safe diversion enc-secret"
    ordinary = render_decision_verification(
        _failed(
            DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value,
            limitations=(secret,),
        )
    )
    assert ordinary.outcome is DecisionRenderOutcome.VERIFICATION_FAILED
    assert ordinary.answer == _VERIFICATION_FAILED
    assert secret not in ordinary.answer
    assert "N99999" not in ordinary.answer

    mixed = render_decision_verification(
        _failed(
            DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH.value,
            CLAIM_SET_NOT_ACTIONABLE,
            limitations=(secret,),
        )
    )
    assert mixed.outcome is DecisionRenderOutcome.VERIFICATION_FAILED
    assert mixed.answer == _VERIFICATION_FAILED
    assert secret not in mixed.answer

    unactionable = render_decision_verification(
        _failed(CLAIM_SET_NOT_ACTIONABLE, limitations=(secret,))
    )
    assert unactionable.outcome is DecisionRenderOutcome.CLAIM_SET_NOT_ACTIONABLE
    assert unactionable.answer == _UNACTIONABLE
    assert secret not in unactionable.answer
    assert not hasattr(unactionable, "verified_claims")


def test_wrong_input_type_raises() -> None:
    with pytest.raises(TypeError):
        render_decision_verification(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        render_decision_verification("de-1")  # type: ignore[arg-type]


def test_aircraft_identity_and_duplicate_identity() -> None:
    rendered = _render((_identity(), _identity()))
    assert rendered.answer.count("Current decision evidence is for aircraft N12345.") == 1
    ordered = _render((_identity("ac-2"), _identity("ac-1")))
    assert ordered.answer.index("aircraft ac-1.") < ordered.answer.index("aircraft ac-2.")
    assert "cleared" not in rendered.answer
    assert "safe aircraft" not in rendered.answer


def test_evaluation_states_use_closed_wording() -> None:
    established = _render(
        (EvaluationStateClaim("de-1", TemporalScope.CURRENT, DecisionEvaluationState.ESTABLISHED),)
    )
    missing = _render(
        (
            EvaluationStateClaim(
                "de-1",
                TemporalScope.CURRENT,
                DecisionEvaluationState.NOT_ESTABLISHED,
            ),
        )
    )
    unavailable = _render(
        (
            EvaluationStateClaim(
                "de-1",
                TemporalScope.CURRENT,
                DecisionEvaluationState.SOURCE_UNAVAILABLE,
            ),
        )
    )
    assert established.answer.endswith("Current decision evidence was established.")
    assert missing.answer.endswith("Current decision evidence was not established.")
    assert unavailable.answer.endswith("A current decision-evidence source was unavailable.")
    assert "every" not in unavailable.answer
    assert "no risk" not in missing.answer
    assert "no recommendation" not in unavailable.answer


def test_distinct_current_statuses_stay_separate() -> None:
    rendered = _render(
        (
            ToolStatusClaim("de-1", TemporalScope.CURRENT, ToolResultStatus.PARTIAL),
            ToolStatusClaim("de-1", TemporalScope.CURRENT, ToolResultStatus.SUCCESS),
        )
    )
    success = "A cited current evidence result has status SUCCESS."
    partial = "A cited current evidence result has status PARTIAL."
    assert success in rendered.answer
    assert partial in rendered.answer
    assert rendered.answer.index(success) < rendered.answer.index(partial)
    assert "The cited current evidence result status" not in rendered.answer
    assert "both" not in rendered.answer


def test_every_tool_status_is_uninterpreted() -> None:
    for status in ToolResultStatus:
        current = _render((ToolStatusClaim("de-1", TemporalScope.CURRENT, status),))
        assert current.answer.endswith(
            f"A cited current evidence result has status {status.value}."
        )
        assert "absent" not in current.answer
        assert "safe" not in current.answer
    persisted = _render(
        (ToolStatusClaim("de-2", TemporalScope.PERSISTED, ToolResultStatus.NOT_FOUND),)
    )
    assert persisted.answer == "A cited persisted evidence result has status NOT_FOUND."
    assert _SUITABILITY not in persisted.answer
    assert "evaluation is stored" not in persisted.answer
    assert "evaluated as of" not in persisted.answer


def test_persisted_status_beside_current_identity_does_not_imply_an_evaluation() -> None:
    rendered = _render(
        (
            _identity(),
            ToolStatusClaim("de-2", TemporalScope.PERSISTED, ToolResultStatus.NOT_FOUND),
        )
    )
    assert "A cited persisted evidence result has status NOT_FOUND." in rendered.answer
    assert _SUITABILITY not in rendered.answer
    assert "is stored" not in rendered.answer


def test_encounter_sets_have_no_selected_encounter() -> None:
    empty = _render((_encounter(()),))
    assert "The verified current context contains no current encounter records." in empty.answer
    assert "hazard" not in empty.answer
    assert "safe" not in empty.answer
    one = _render((_encounter(("enc-1",)),))
    assert "The verified current encounter IDs are enc-1." in one.answer
    assert "No encounter is selected" not in one.answer
    many = _render((_encounter(("enc-2",)), _encounter(("enc-1", "enc-3"))))
    assert many.answer.index("enc-1 and enc-3") < many.answer.index("enc-2.")
    assert "No encounter is selected by this answer." in many.answer
    assert "primary" not in many.answer
    assert "selected encounter" not in many.answer


def test_risk_absence_is_not_low_and_levels_stay_stored() -> None:
    absent = _render((_risk_absent("enc-1"),))
    unnamed = _render((_risk_absent(None),))
    assert (
        "No stored current risk record is present for encounter enc-1. "
        "Risk absence is not a LOW risk classification."
    ) in absent.answer
    assert (
        "No stored current risk record is present for the verified risk target. "
        "Risk absence is not a LOW risk classification."
    ) in unnamed.answer
    assert "risk level is LOW" not in absent.answer
    assert "safe" not in absent.answer
    for level, score in (
        (StoredRiskLevel.LOW, 12),
        (StoredRiskLevel.MEDIUM, 40),
        (StoredRiskLevel.HIGH, 80),
    ):
        rendered = _render((_risk_present(level=level, score=score),))
        assert (
            f"The stored current risk level for encounter enc-1 is {level.value} "
            f"with a stored score of {score}. The stored risk id is risk-1."
        ) in rendered.answer
        assert "severity" not in rendered.answer
        if level is StoredRiskLevel.LOW:
            assert "A stored LOW level is not a statement that the aircraft is safe." in rendered.answer
        else:
            assert "not a statement that the aircraft is safe" not in rendered.answer
    omitted = _render((_risk_present(encounter_id=None),))
    assert "for the verified risk target is HIGH" in omitted.answer
    assert "for encounter" not in omitted.answer
    ordered = _render((_risk_present(), _risk_absent()))
    assert ordered.answer.index("No stored current risk") < ordered.answer.index(
        "The stored current risk level"
    )


def test_recommendation_sets_and_actions_have_no_winner() -> None:
    empty = _render((_recommendation_set(()),))
    assert (
        "No stored current recommendation is present in the verified current "
        "candidate set. This is not a MONITOR recommendation."
    ) in empty.answer
    assert "The stored advisory action" not in empty.answer
    one = _render((_recommendation_set(("rec-1",)),))
    assert "The verified current recommendation IDs are rec-1." in one.answer
    assert "No recommendation is selected" not in one.answer
    many = _render((_recommendation_set(("rec-1", "rec-2")),))
    assert "The verified current recommendation IDs are rec-1 and rec-2." in many.answer
    assert "No recommendation is selected by this answer." in many.answer
    assert "winner" not in many.answer
    assert "primary" not in many.answer
    actions = _render(
        (
            _action("rec-2", RecommendationActionType.EVALUATE_DIVERSION),
            _action("rec-1", RecommendationActionType.MONITOR),
            _action("rec-1", RecommendationActionType.MONITOR_AND_PREPARE_OPTIONS),
        )
    )
    monitor = "The stored advisory action for recommendation rec-1 is MONITOR."
    prepare = (
        "The stored advisory action for recommendation rec-1 is "
        "MONITOR_AND_PREPARE_OPTIONS."
    )
    diversion = (
        "The stored advisory action for recommendation rec-2 is EVALUATE_DIVERSION."
    )
    assert actions.answer.index(monitor) < actions.answer.index(prepare)
    assert actions.answer.index(prepare) < actions.answer.index(diversion)
    assert "Monitor the aircraft" not in actions.answer
    assert "Stored recommendation actions are advisory evidence only." in actions.answer
    assert (
        "The stored EVALUATE_DIVERSION label is not a clearance, not a selected "
        "diversion, not a route, and not a dispatch or ATC instruction."
    ) in actions.answer
    monitor_only = _render((_action(),))
    assert "not a clearance" not in monitor_only.answer


def test_every_chain_gap_has_fixed_wording_in_enum_order() -> None:
    claims = tuple(
        ChainGapClaim("de-1", TemporalScope.CURRENT, gap)
        for gap in reversed(tuple(DecisionChainGap))
    )
    answer = _render(claims).answer
    sentences = [_GAP_SENTENCES[gap] for gap in DecisionChainGap]
    positions = [answer.index(sentence) for sentence in sentences]
    assert positions == sorted(positions)
    assert "a stale recommendation" not in answer


def test_chain_gap_overlap_is_token_deduped() -> None:
    absent = _render((_risk_absent(), ChainGapClaim("de-1", TemporalScope.CURRENT, DecisionChainGap.RISK_ABSENT)))
    assert "reports stored risk absence" not in absent.answer
    assert "No stored current risk record is present" in absent.answer
    gap_only = _render((ChainGapClaim("de-1", TemporalScope.CURRENT, DecisionChainGap.RISK_ABSENT),))
    assert _GAP_SENTENCES[DecisionChainGap.RISK_ABSENT] in gap_only.answer

    empty_set = _render(
        (
            _recommendation_set(()),
            ChainGapClaim("de-1", TemporalScope.CURRENT, DecisionChainGap.RECOMMENDATION_ABSENT),
        )
    )
    assert "reports stored recommendation absence" not in empty_set.answer
    assert "This is not a MONITOR recommendation." in empty_set.answer
    recommendation_gap = _render(
        (ChainGapClaim("de-1", TemporalScope.CURRENT, DecisionChainGap.RECOMMENDATION_ABSENT),)
    )
    assert _GAP_SENTENCES[DecisionChainGap.RECOMMENDATION_ABSENT] in recommendation_gap.answer

    empty_encounters = _render(
        (
            _encounter(()),
            ChainGapClaim("de-1", TemporalScope.CURRENT, DecisionChainGap.NO_CURRENT_ENCOUNTER),
        )
    )
    assert "No current encounter is available" not in empty_encounters.answer
    assert "contains no current encounter records" in empty_encounters.answer
    encounter_gap = _render(
        (ChainGapClaim("de-1", TemporalScope.CURRENT, DecisionChainGap.NO_CURRENT_ENCOUNTER),)
    )
    assert _GAP_SENTENCES[DecisionChainGap.NO_CURRENT_ENCOUNTER] in encounter_gap.answer


def test_every_capability_gap_stays_unavailable() -> None:
    claims = tuple(
        CapabilityGapClaim("de-1", TemporalScope.CURRENT, gap)
        for gap in reversed(tuple(DecisionCapabilityGap))
    )
    answer = _render(claims).answer
    sentences = [_CAPABILITY_SENTENCES[gap] for gap in DecisionCapabilityGap]
    positions = [answer.index(sentence) for sentence in sentences]
    assert positions == sorted(positions)
    assert answer.count("Unavailable evidence does not establish an unsafe result.") == 1
    assert "route is unsafe" not in answer
    assert "no safe route" not in answer


def test_persisted_evaluation_null_id_does_not_say_an_evaluation_is_stored() -> None:
    named = _render((_evaluation("eval-1"),))
    assert (
        "Persisted airport-evaluation evidence is stored for recommendation rec-1, "
        "with evaluation id eval-1."
    ) in named.answer
    missing = _render((_evaluation(None),))
    assert (
        "The persisted airport-evaluation lookup for recommendation rec-1 does not "
        "include a stored evaluation id."
    ) in missing.answer
    assert "is stored" not in missing.answer
    assert _SUITABILITY in missing.answer


def test_persisted_candidates_do_not_rank_or_clear() -> None:
    complete = _render((_candidate(),))
    assert (
        "Persisted airport-assessment evidence for airport KDEN and recommendation "
        "rec-1 has assessment id aa-1."
    ) in complete.answer
    assert "persisted assessment scoring completed" in complete.answer
    assert "COMPLETE does not mean a safe airport, current suitability, a selected airport, or a selected diversion." in complete.answer
    assert "Route-safety evidence is unavailable." in complete.answer
    assert "Runway evidence is unavailable." in complete.answer
    assert "Congestion evidence is unavailable." in complete.answer
    assert "rank" not in complete.answer
    assert "distance" not in complete.answer
    assert "ETA" not in complete.answer
    waiting = _render((_candidate(PersistedAssessmentStatus.WAITING_FOR_WEATHER, "KSEA", "aa-2"),))
    assert "The stored assessment status is WAITING_FOR_WEATHER." in waiting.answer
    assert "will" not in waiting.answer
    assert "scoring completed" not in waiting.answer
    ordered = _render((_candidate(airport_id="KSEA", assessment_id="aa-2"), _candidate()))
    assert ordered.answer.index("airport KDEN") < ordered.answer.index("airport KSEA")
    assert _SUITABILITY in complete.answer


def test_candidate_counts_and_collection_reasons() -> None:
    empty = _render(
        (_candidate_status(0, PersistedCandidateCollectionReason.EMPTY_ASSESSMENTS),)
    )
    assert (
        "The persisted evaluation for recommendation rec-1 contains no candidate "
        "airport assessments."
    ) in empty.answer
    assert "contains 0 stored" not in empty.answer
    assert "A candidate count of zero does not mean that no safe airport exists." in empty.answer
    assert "no safe airport exists." not in empty.answer.replace(
        "A candidate count of zero does not mean that no safe airport exists.",
        "",
    )

    one = _render((_candidate_status(1, None),))
    assert "contains 1 stored candidate airport assessment." in one.answer
    assert "assessments." not in one.answer

    several = _render(
        (_candidate_status(2, PersistedCandidateCollectionReason.NO_COMPLETE_ASSESSMENT),)
    )
    assert "contains 2 stored candidate airport assessments." in several.answer
    assert (
        "has no candidate with a COMPLETE stored assessment."
    ) in several.answer
    assert "no safe airport" not in several.answer

    zero = _render((_candidate_status(0, None),))
    assert "contains 0 stored candidate airport assessments." in zero.answer
    assert "A candidate count of zero does not mean that no safe airport exists." in zero.answer
    assert "COMPLETE stored assessment" not in zero.answer


def test_current_persisted_link_keeps_scopes_distinct() -> None:
    rendered = _render((CurrentPersistedLinkClaim("de-1", "de-2", "rec-1"), _identity()))
    assert (
        "Current recommendation rec-1 has verified associated persisted "
        "airport-evaluation evidence."
    ) in rendered.answer
    assert (
        "That persisted evidence is not a current airport candidate, not current "
        "suitability, and not a selected airport."
    ) in rendered.answer
    assert "current airport recommendation" not in rendered.answer
    assert "HYBRID" not in rendered.answer
    assert f"Current evidence was evaluated as of {AS_OF}." in rendered.answer
    assert "de-1" not in rendered.answer
    assert "de-2" not in rendered.answer


def test_limitations_are_closed_and_unknown_text_is_not_copied() -> None:
    snapshot = "Current decision evidence was composed from independently observed reads and is not a single transactional snapshot."
    absence = "Recommendation absence is proven only for the observed current candidate set."
    rendered = _render(
        (
            LimitationClaim(
                "de-1",
                TemporalScope.CURRENT,
                DecisionLimitationCode.NO_SNAPSHOT_LIMITATION,
            ),
            LimitationClaim(
                "de-1",
                TemporalScope.CURRENT,
                DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION,
            ),
        ),
        (
            RECOGNIZED_NO_SNAPSHOT_LIMITATION,
            RECOGNIZED_RECOMMENDATION_ABSENCE_LIMITATION,
            RECOGNIZED_NOT_CURRENT_LIMITATION,
        ),
    )
    assert rendered.answer.count(snapshot) == 1
    assert rendered.answer.count(absence) == 1
    assert rendered.answer.count(RECOGNIZED_NOT_CURRENT_LIMITATION) == 1
    assert _SUITABILITY in rendered.answer
    assert rendered.answer.index(snapshot) < rendered.answer.index(absence)
    assert rendered.answer.index(absence) < rendered.answer.index(RECOGNIZED_NOT_CURRENT_LIMITATION)

    secret = "Divert to the safest airport KDEN now."
    unknown = _render((_identity(),), (secret, secret))
    assert secret not in unknown.answer
    assert unknown.answer.count(_UNKNOWN_LIMITATION) == 1
    assert RECOGNIZED_NOT_CURRENT_LIMITATION not in unknown.answer


def test_known_persisted_limitations_are_not_collapsed_to_generic_text() -> None:
    membership = _render((_identity(),), (RECOGNIZED_MEMBERSHIP_LIMITATION,))
    assert (
        "The currently stored persisted airport rows do not prove the original "
        "complete candidate set after TTL or later writes."
    ) in membership.answer
    assert RECOGNIZED_MEMBERSHIP_LIMITATION not in membership.answer
    assert _UNKNOWN_LIMITATION not in membership.answer
    assert _SUITABILITY not in membership.answer

    missing = _render((_identity(),), (RECOGNIZED_MISSING_REFERENCED_ROW,))
    assert (
        "A referenced persisted airport-assessment row is missing from the stored "
        "evaluation."
    ) in missing.answer
    assert RECOGNIZED_MISSING_REFERENCED_ROW not in missing.answer
    assert _UNKNOWN_LIMITATION not in missing.answer

    waiting = _render((_candidate_status(2, None),), (RECOGNIZED_WAITING_ROWS_ABSENT,))
    assert (
        "The recommendation referenced airport-assessment rows that are not "
        "present in the stored evaluation."
    ) in waiting.answer
    assert "non-empty airport assessment list" not in waiting.answer
    assert "contains 2 stored candidate airport assessments." in waiting.answer
    assert _UNKNOWN_LIMITATION not in waiting.answer


def test_recognized_limitation_strings_match_producers() -> None:
    from wilvor_ai.persisted_airport_evidence import (
        MEMBERSHIP_LIMITATION,
        NOT_CURRENT_LIMITATION,
        _MISSING_REFERENCED_ROW,
        _WAITING_ROWS_ABSENT,
    )
    from wilvor_operational.linking import (
        NO_SNAPSHOT_LIMITATION,
        RECOMMENDATION_ABSENCE_LIMITATION,
    )

    assert RECOGNIZED_NOT_CURRENT_LIMITATION == NOT_CURRENT_LIMITATION
    assert RECOGNIZED_MEMBERSHIP_LIMITATION == MEMBERSHIP_LIMITATION
    assert RECOGNIZED_MISSING_REFERENCED_ROW == _MISSING_REFERENCED_ROW
    assert RECOGNIZED_WAITING_ROWS_ABSENT == _WAITING_ROWS_ABSENT
    assert RECOGNIZED_NO_SNAPSHOT_LIMITATION == NO_SNAPSHOT_LIMITATION
    assert RECOGNIZED_RECOMMENDATION_ABSENCE_LIMITATION == RECOMMENDATION_ABSENCE_LIMITATION


def test_as_of_and_mixed_scope_never_say_hybrid() -> None:
    current = _render((_identity(),))
    assert current.answer.startswith(f"Current evidence was evaluated as of {AS_OF}.")
    assert "real-time" not in current.answer
    assert "globally latest" not in current.answer
    persisted = _render((_evaluation("eval-1"),))
    assert "evaluated as of" not in persisted.answer
    assert persisted.answer.count(_SUITABILITY) == 1
    mixed = _render((_evaluation("eval-1"), _identity(), _candidate()))
    assert "HYBRID" not in mixed.answer
    assert mixed.answer.index("aircraft N12345") < mixed.answer.index("evaluation id eval-1")
    assert mixed.answer.index("evaluation id eval-1") < mixed.answer.index("airport KDEN")
    assert mixed.answer.index("airport KDEN") < mixed.answer.index(_SUITABILITY)


def test_canonical_category_order_ignores_claim_tuple_order() -> None:
    claims = (
        CurrentPersistedLinkClaim("de-1", "de-2", "rec-1"),
        _candidate(),
        _candidate_status(2, None),
        _evaluation("eval-1"),
        CapabilityGapClaim(
            "de-1",
            TemporalScope.CURRENT,
            DecisionCapabilityGap.ROUTE_ALTERNATIVE_NOT_IMPLEMENTED,
        ),
        ChainGapClaim("de-1", TemporalScope.CURRENT, DecisionChainGap.INCOMPLETE_CURRENT_CHAIN),
        _action(),
        _recommendation_set(),
        _risk_present(),
        _encounter(),
        ToolStatusClaim("de-1", TemporalScope.CURRENT, ToolResultStatus.SUCCESS),
        EvaluationStateClaim("de-1", TemporalScope.CURRENT, DecisionEvaluationState.ESTABLISHED),
        _identity(),
        LimitationClaim("de-1", TemporalScope.CURRENT, DecisionLimitationCode.NO_SNAPSHOT_LIMITATION),
    )
    answer = _render(tuple(reversed(claims)), (RECOGNIZED_NOT_CURRENT_LIMITATION,)).answer
    markers = (
        f"evaluated as of {AS_OF}",
        "aircraft N12345",
        "was established",
        "status SUCCESS",
        "encounter IDs are enc-1",
        "stored score of 80",
        "recommendation IDs are rec-1",
        "advisory action for recommendation rec-1 is MONITOR",
        "chain is incomplete",
        "alternative-route generation",
        "evaluation id eval-1",
        "contains 2 stored",
        "airport KDEN",
        "verified associated persisted",
        _SUITABILITY,
        "independently observed reads",
        RECOGNIZED_NOT_CURRENT_LIMITATION,
    )
    positions = [answer.index(marker) for marker in markers]
    assert positions == sorted(positions)
    assert _render(claims, (RECOGNIZED_NOT_CURRENT_LIMITATION,)).answer == answer


def test_simple_factual_answer_avoids_unsupported_operational_phrases() -> None:
    answer = _render((_identity(), _risk_present(), _encounter())).answer
    for phrase in (
        "safe diversion",
        "safest airport",
        "best airport",
        "selected airport",
        "selected diversion",
        "clearance",
        "cleared",
        "fly to",
        "route to",
        "waypoint",
        "flight plan",
    ):
        assert phrase not in answer


def test_overlong_factual_answer_discards_every_sentence() -> None:
    ids = tuple(f"enc-{index:03d}-{'x' * 200}" for index in range(40))
    rendered = _render((_encounter(ids), _identity("N12345")))
    assert rendered.outcome is DecisionRenderOutcome.LENGTH_EXCEEDED
    assert rendered.answer == _LENGTH_EXCEEDED
    assert "enc-" not in rendered.answer
    assert "N12345" not in rendered.answer
    assert "x" * 20 not in rendered.answer
    short = _render((_identity(),))
    assert short.outcome is DecisionRenderOutcome.FACTUAL
    assert len(short.answer) <= SPECIALIST_ANSWER_MAX_LENGTH


def test_fail_closed_answers_are_structurally_exact() -> None:
    with pytest.raises(ContractValidationError):
        DecisionRenderResult(
            DecisionRenderOutcome.VERIFICATION_FAILED,
            "The stored current risk level is HIGH.",
        )
    with pytest.raises(ContractValidationError):
        DecisionRenderResult(DecisionRenderOutcome.LENGTH_EXCEEDED, _VERIFICATION_FAILED)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_renderer_import_isolation() -> None:
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
        "wilvor_ai.historical_answer_renderer",
        "wilvor_ai.historical_specialist",
    }
    imported = _imported_modules(RENDERER_PATH)
    for name in forbidden:
        assert name not in imported
        assert not any(item == name or item.startswith(name + ".") for item in imported)

    script = """
import sys
import wilvor_ai.decision_answer_renderer
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
    "wilvor_ai.historical_answer_renderer",
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
