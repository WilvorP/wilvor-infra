"""Deterministic Decision Expert answer renderer.

Factual sentences come only from a DecisionVerificationResult. This module
does not verify claims, read raw tool results, call a model, or import
operational runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from wilvor_ai.contracts import ContractValidationError, TemporalScope, ToolResultStatus
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
    PersistedEvaluationClaim,
    RecommendationActionClaim,
    RecommendationSetClaim,
    RiskAbsentClaim,
    RiskPresentClaim,
    ToolStatusClaim,
)
from wilvor_ai.decision_contracts import (
    DecisionCapabilityGap,
    DecisionChainGap,
    DecisionEvaluationState,
    DecisionLimitationCode,
    RecommendationActionType,
    StoredRiskLevel,
)
from wilvor_ai.decision_evidence_verifier import (
    CLAIM_SET_NOT_ACTIONABLE,
    DecisionVerificationResult,
)
from wilvor_ai.specialist_contracts import SPECIALIST_ANSWER_MAX_LENGTH, VerifierOutcome


RECOGNIZED_NOT_CURRENT_LIMITATION = (
    "Persisted airport evaluation evidence is not asserted to be current."
)
RECOGNIZED_MEMBERSHIP_LIMITATION = (
    "Persisted airport rows are returned for this evaluation id. This read "
    "does not prove the original complete candidate set after TTL or a later write."
)
RECOGNIZED_MISSING_REFERENCED_ROW = (
    "A referenced airport assessment row is not in the stored evaluation."
)
RECOGNIZED_WAITING_ROWS_ABSENT = (
    "The recommendation recorded a non-empty airport assessment list. "
    "Those rows are not in the stored evaluation."
)
RECOGNIZED_NO_SNAPSHOT_LIMITATION = (
    "DynamoDB reads are independently observed; this composition is not "
    "a transactional cross-table snapshot."
)
RECOGNIZED_RECOMMENDATION_ABSENCE_LIMITATION = (
    "Absence of a recommendation is proven only within the observed ACTIVE, "
    "time-filtered candidate set."
)

_VERIFICATION_FAILED_ANSWER = (
    "Wilvor could not verify the proposed decision claims against deterministic "
    "evidence, so no factual decision answer is being returned."
)
_UNACTIONABLE_ANSWER = (
    "The verified claim set did not contain enough factual evidence to produce "
    "a decision answer."
)
_LENGTH_EXCEEDED_ANSWER = (
    "The verified decision claims exceed the deterministic answer length bound, "
    "so no factual decision answer is being returned."
)
_UNKNOWN_LIMITATION_SENTENCE = (
    "Additional deterministic evidence limitations apply and are not expanded "
    "in this answer."
)
_PERSISTED_SUITABILITY_SENTENCE = (
    "Persisted airport-evaluation evidence is not current airport suitability."
)
_NO_ENCOUNTER_SELECTED = "No encounter is selected by this answer."
_NO_RECOMMENDATION_SELECTED = "No recommendation is selected by this answer."
_LOW_NOT_SAFE = "A stored LOW level is not a statement that the aircraft is safe."
_ADVISORY_ONLY = "Stored recommendation actions are advisory evidence only."
_DIVERSION_LIMIT = (
    "The stored EVALUATE_DIVERSION label is not a clearance, not a selected "
    "diversion, not a route, and not a dispatch or ATC instruction."
)
_UNAVAILABLE_NOT_UNSAFE = "Unavailable evidence does not establish an unsafe result."
_ZERO_CANDIDATES_NOT_UNSAFE = (
    "A candidate count of zero does not mean that no safe airport exists."
)
_NOT_LOW = "Risk absence is not a LOW risk classification."
_NOT_MONITOR = "This is not a MONITOR recommendation."

_SNAPSHOT_SENTENCE = (
    "Current decision evidence was composed from independently observed reads "
    "and is not a single transactional snapshot."
)
_RECOMMENDATION_ABSENCE_SENTENCE = (
    "Recommendation absence is proven only for the observed current candidate set."
)
_MEMBERSHIP_SENTENCE = (
    "The currently stored persisted airport rows do not prove the original "
    "complete candidate set after TTL or later writes."
)
_MISSING_ROW_SENTENCE = (
    "A referenced persisted airport-assessment row is missing from the stored "
    "evaluation."
)
_WAITING_ROWS_SENTENCE = (
    "The recommendation referenced airport-assessment rows that are not present "
    "in the stored evaluation."
)

_SCOPE_ORDER = {TemporalScope.CURRENT: 0, TemporalScope.PERSISTED: 1}
_STATUS_ORDER = {status: index for index, status in enumerate(ToolResultStatus)}
_EVALUATION_ORDER = {
    state: index for index, state in enumerate(DecisionEvaluationState)
}
_ACTION_ORDER = {
    action: index for index, action in enumerate(RecommendationActionType)
}
_GAP_ORDER = {gap: index for index, gap in enumerate(DecisionChainGap)}
_CAPABILITY_ORDER = {
    gap: index for index, gap in enumerate(DecisionCapabilityGap)
}
_PERSISTED_FACT_TYPES = (
    PersistedEvaluationClaim,
    PersistedCandidateClaim,
    PersistedCandidateStatusClaim,
    CurrentPersistedLinkClaim,
)
_MANDATORY_TOKENS = {
    RECOGNIZED_NO_SNAPSHOT_LIMITATION: "lim:snapshot",
    RECOGNIZED_RECOMMENDATION_ABSENCE_LIMITATION: "lim:rec-absence",
    RECOGNIZED_NOT_CURRENT_LIMITATION: "lim:not-current",
    RECOGNIZED_MEMBERSHIP_LIMITATION: "lim:membership",
    RECOGNIZED_MISSING_REFERENCED_ROW: "lim:missing-row",
    RECOGNIZED_WAITING_ROWS_ABSENT: "lim:waiting-rows",
}
_CLAIM_LIMITATION_TOKENS = {
    DecisionLimitationCode.NO_SNAPSHOT_LIMITATION: "lim:snapshot",
    DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION: "lim:rec-absence",
}
_LIMITATION_SENTENCES = (
    ("lim:snapshot", _SNAPSHOT_SENTENCE),
    ("lim:rec-absence", _RECOMMENDATION_ABSENCE_SENTENCE),
    ("lim:not-current", RECOGNIZED_NOT_CURRENT_LIMITATION),
    ("lim:membership", _MEMBERSHIP_SENTENCE),
    ("lim:missing-row", _MISSING_ROW_SENTENCE),
    ("lim:waiting-rows", _WAITING_ROWS_SENTENCE),
)


class DecisionRenderOutcome(str, Enum):
    """How a verification result was rendered. Not inferred from answer text."""

    FACTUAL = "FACTUAL"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    CLAIM_SET_NOT_ACTIONABLE = "CLAIM_SET_NOT_ACTIONABLE"
    LENGTH_EXCEEDED = "LENGTH_EXCEEDED"


_FIXED_ANSWERS = {
    DecisionRenderOutcome.VERIFICATION_FAILED: _VERIFICATION_FAILED_ANSWER,
    DecisionRenderOutcome.CLAIM_SET_NOT_ACTIONABLE: _UNACTIONABLE_ANSWER,
    DecisionRenderOutcome.LENGTH_EXCEEDED: _LENGTH_EXCEEDED_ANSWER,
}


@dataclass(frozen=True)
class DecisionRenderResult:
    """Rendered decision prose and why it was or was not factual.

    This artifact carries no claims, evidence refs, tool results, or model text.
    """

    outcome: DecisionRenderOutcome
    answer: str

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, DecisionRenderOutcome):
            raise ContractValidationError("invalid_render_outcome")
        if not isinstance(self.answer, str) or not self.answer.strip():
            raise ContractValidationError("invalid_render_answer")
        expected = _FIXED_ANSWERS.get(self.outcome)
        if expected is not None and self.answer != expected:
            raise ContractValidationError("invalid_render_answer")


class _Answer:
    def __init__(self) -> None:
        self._tokens: set[str] = set()
        self._sentences: list[str] = []

    def add(self, token: str, sentence: str) -> None:
        if token in self._tokens:
            return
        self._tokens.add(token)
        self._sentences.append(sentence)

    def text(self) -> str:
        return " ".join(self._sentences)


def render_decision_verification(
    verification: DecisionVerificationResult,
) -> DecisionRenderResult:
    """Render verified decision claims. Raw evidence is not an input."""

    if not isinstance(verification, DecisionVerificationResult):
        raise TypeError("verification must be a DecisionVerificationResult")
    if verification.outcome is VerifierOutcome.FAILED:
        if verification.rejected_claim_codes == (CLAIM_SET_NOT_ACTIONABLE,):
            return DecisionRenderResult(
                DecisionRenderOutcome.CLAIM_SET_NOT_ACTIONABLE,
                _UNACTIONABLE_ANSWER,
            )
        return DecisionRenderResult(
            DecisionRenderOutcome.VERIFICATION_FAILED,
            _VERIFICATION_FAILED_ANSWER,
        )
    answer = _factual_answer(verification)
    if len(answer) > SPECIALIST_ANSWER_MAX_LENGTH:
        return DecisionRenderResult(
            DecisionRenderOutcome.LENGTH_EXCEEDED,
            _LENGTH_EXCEEDED_ANSWER,
        )
    return DecisionRenderResult(DecisionRenderOutcome.FACTUAL, answer)


def _factual_answer(verification: DecisionVerificationResult) -> str:
    claims = verification.verified_claims
    answer = _Answer()
    if (
        TemporalScope.CURRENT in verification.verified_temporal_scopes
        and verification.current_as_of_utc is not None
    ):
        answer.add(
            "as-of",
            f"Current evidence was evaluated as of {verification.current_as_of_utc}.",
        )
    _add_identities(answer, claims)
    _add_evaluation_states(answer, claims)
    _add_statuses(answer, claims)
    _add_encounters(answer, claims)
    _add_risks(answer, claims)
    _add_recommendation_sets(answer, claims)
    _add_actions(answer, claims)
    _add_chain_gaps(answer, claims)
    _add_capability_gaps(answer, claims)
    _add_persisted_evaluations(answer, claims)
    _add_candidate_statuses(answer, claims)
    _add_candidates(answer, claims)
    _add_links(answer, claims)
    limitation_tokens, unknown = _limitation_tokens(
        claims,
        verification.mandatory_limitations,
    )
    if _persisted_facts_present(claims) or "lim:not-current" in limitation_tokens:
        answer.add("persisted-suitability", _PERSISTED_SUITABILITY_SENTENCE)
    for token, sentence in _LIMITATION_SENTENCES:
        if token in limitation_tokens:
            answer.add(token, sentence)
    if unknown:
        answer.add("lim:unknown", _UNKNOWN_LIMITATION_SENTENCE)
    return answer.text()


def _persisted_facts_present(claims: tuple[object, ...]) -> bool:
    return any(isinstance(claim, _PERSISTED_FACT_TYPES) for claim in claims)


def _add_identities(_answer: _Answer, claims: tuple[object, ...]) -> None:
    identities = sorted(
        (claim for claim in claims if isinstance(claim, AircraftIdentityClaim)),
        key=lambda claim: claim.aircraft_id,
    )
    for claim in identities:
        _answer.add(
            f"identity:{claim.aircraft_id}",
            f"Current decision evidence is for aircraft {claim.aircraft_id}.",
        )


def _add_evaluation_states(_answer: _Answer, claims: tuple[object, ...]) -> None:
    states = sorted(
        (claim for claim in claims if isinstance(claim, EvaluationStateClaim)),
        key=lambda claim: _EVALUATION_ORDER[claim.evaluation_state],
    )
    sentences = {
        DecisionEvaluationState.ESTABLISHED: "Current decision evidence was established.",
        DecisionEvaluationState.NOT_ESTABLISHED: (
            "Current decision evidence was not established."
        ),
        DecisionEvaluationState.SOURCE_UNAVAILABLE: (
            "A current decision-evidence source was unavailable."
        ),
    }
    for claim in states:
        _answer.add(
            f"evaluation:{claim.evaluation_state.value}",
            sentences[claim.evaluation_state],
        )


def _add_statuses(_answer: _Answer, claims: tuple[object, ...]) -> None:
    statuses = sorted(
        (claim for claim in claims if isinstance(claim, ToolStatusClaim)),
        key=lambda claim: (_SCOPE_ORDER[claim.evidence_scope], _STATUS_ORDER[claim.status]),
    )
    for claim in statuses:
        scope = "current" if claim.evidence_scope is TemporalScope.CURRENT else "persisted"
        _answer.add(
            f"status:{scope}:{claim.status.value}",
            f"A cited {scope} evidence result has status {claim.status.value}.",
        )


def _add_encounters(_answer: _Answer, claims: tuple[object, ...]) -> None:
    sets = sorted(
        (claim.encounter_ids for claim in claims if isinstance(claim, EncounterSetClaim))
    )
    multiple = False
    for encounter_ids in sets:
        multiple = multiple or len(encounter_ids) > 1
        if encounter_ids:
            sentence = (
                "The verified current encounter IDs are "
                f"{_join_ids(encounter_ids)}."
            )
        else:
            sentence = "The verified current context contains no current encounter records."
        _answer.add(f"encounters:{encounter_ids}", sentence)
    if multiple:
        _answer.add("encounters:none-selected", _NO_ENCOUNTER_SELECTED)


def _add_risks(_answer: _Answer, claims: tuple[object, ...]) -> None:
    risks = [
        claim
        for claim in claims
        if isinstance(claim, (RiskAbsentClaim, RiskPresentClaim))
    ]
    risks.sort(key=_risk_sort_key)
    saw_low = False
    for claim in risks:
        if isinstance(claim, RiskAbsentClaim):
            target = _risk_target(claim.encounter_id)
            _answer.add(
                f"risk-absent:{claim.encounter_id or ''}",
                f"No stored current risk record is present for {target}. {_NOT_LOW}",
            )
            continue
        target = _risk_target(claim.encounter_id)
        _answer.add(
            (
                "risk-present:"
                f"{claim.encounter_id or ''}:{claim.risk_id}:"
                f"{claim.risk_level.value}:{claim.risk_score}"
            ),
            (
                f"The stored current risk level for {target} is {claim.risk_level.value} "
                f"with a stored score of {claim.risk_score}. "
                f"The stored risk id is {claim.risk_id}."
            ),
        )
        saw_low = saw_low or claim.risk_level is StoredRiskLevel.LOW
    if saw_low:
        _answer.add("risk:low-not-safe", _LOW_NOT_SAFE)


def _risk_target(encounter_id: str | None) -> str:
    if encounter_id is None:
        return "the verified risk target"
    return f"encounter {encounter_id}"


def _risk_sort_key(claim: RiskAbsentClaim | RiskPresentClaim) -> tuple[object, ...]:
    if isinstance(claim, RiskAbsentClaim):
        return (0, claim.encounter_id or "", "")
    return (1, claim.encounter_id or "", claim.risk_id)


def _add_recommendation_sets(_answer: _Answer, claims: tuple[object, ...]) -> None:
    sets = sorted(
        (
            claim.recommendation_ids
            for claim in claims
            if isinstance(claim, RecommendationSetClaim)
        )
    )
    multiple = False
    for recommendation_ids in sets:
        multiple = multiple or len(recommendation_ids) > 1
        if recommendation_ids:
            sentence = (
                "The verified current recommendation IDs are "
                f"{_join_ids(recommendation_ids)}."
            )
        else:
            sentence = (
                "No stored current recommendation is present in the verified "
                f"current candidate set. {_NOT_MONITOR}"
            )
        _answer.add(f"recommendations:{recommendation_ids}", sentence)
    if multiple:
        _answer.add("recommendations:none-selected", _NO_RECOMMENDATION_SELECTED)


def _add_actions(_answer: _Answer, claims: tuple[object, ...]) -> None:
    actions = sorted(
        (claim for claim in claims if isinstance(claim, RecommendationActionClaim)),
        key=lambda claim: (
            claim.recommendation_id,
            _ACTION_ORDER[claim.primary_action_type],
        ),
    )
    diversion = False
    for claim in actions:
        _answer.add(
            f"action:{claim.recommendation_id}:{claim.primary_action_type.value}",
            (
                "The stored advisory action for recommendation "
                f"{claim.recommendation_id} is {claim.primary_action_type.value}."
            ),
        )
        diversion = (
            diversion
            or claim.primary_action_type is RecommendationActionType.EVALUATE_DIVERSION
        )
    if diversion:
        _answer.add("action:diversion-limit", _DIVERSION_LIMIT)
    if actions:
        _answer.add("action:advisory-only", _ADVISORY_ONLY)


def _add_chain_gaps(_answer: _Answer, claims: tuple[object, ...]) -> None:
    suppressed = set()
    if any(
        isinstance(claim, RiskAbsentClaim) for claim in claims
    ):
        suppressed.add(DecisionChainGap.RISK_ABSENT)
    if any(
        isinstance(claim, RecommendationSetClaim) and not claim.recommendation_ids
        for claim in claims
    ):
        suppressed.add(DecisionChainGap.RECOMMENDATION_ABSENT)
    if any(
        isinstance(claim, EncounterSetClaim) and not claim.encounter_ids
        for claim in claims
    ):
        suppressed.add(DecisionChainGap.NO_CURRENT_ENCOUNTER)
    gaps = sorted(
        (
            claim.chain_gap
            for claim in claims
            if isinstance(claim, ChainGapClaim) and claim.chain_gap not in suppressed
        ),
        key=lambda gap: _GAP_ORDER[gap],
    )
    sentences = {
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
            f"The verified current chain reports stored risk absence. {_NOT_LOW}"
        ),
        DecisionChainGap.RECOMMENDATION_ABSENT: (
            "The verified current chain reports stored recommendation absence. "
            f"{_NOT_MONITOR}"
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
    for gap in gaps:
        _answer.add(f"gap:{gap.value}", sentences[gap])


def _add_capability_gaps(_answer: _Answer, claims: tuple[object, ...]) -> None:
    gaps = sorted(
        (
            claim.capability_gap
            for claim in claims
            if isinstance(claim, CapabilityGapClaim)
        ),
        key=lambda gap: _CAPABILITY_ORDER[gap],
    )
    sentences = {
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
    for gap in gaps:
        _answer.add(f"capability:{gap.value}", sentences[gap])
    if gaps:
        _answer.add("capability:not-unsafe", _UNAVAILABLE_NOT_UNSAFE)


def _add_persisted_evaluations(_answer: _Answer, claims: tuple[object, ...]) -> None:
    evaluations = sorted(
        (claim for claim in claims if isinstance(claim, PersistedEvaluationClaim)),
        key=lambda claim: (claim.recommendation_id, claim.airport_evaluation_id or ""),
    )
    for claim in evaluations:
        if claim.airport_evaluation_id is None:
            sentence = (
                "The persisted airport-evaluation lookup for recommendation "
                f"{claim.recommendation_id} does not include a stored evaluation id."
            )
        else:
            sentence = (
                "Persisted airport-evaluation evidence is stored for recommendation "
                f"{claim.recommendation_id}, with evaluation id "
                f"{claim.airport_evaluation_id}."
            )
        _answer.add(
            f"persisted-evaluation:{claim.recommendation_id}:{claim.airport_evaluation_id or ''}",
            sentence,
        )


def _add_candidate_statuses(_answer: _Answer, claims: tuple[object, ...]) -> None:
    statuses = sorted(
        (claim for claim in claims if isinstance(claim, PersistedCandidateStatusClaim)),
        key=lambda claim: (
            claim.recommendation_id,
            claim.candidate_count,
            "" if claim.collection_reason is None else claim.collection_reason.value,
        ),
    )
    empty_recommendations = {
        claim.recommendation_id
        for claim in statuses
        if claim.collection_reason
        is PersistedCandidateCollectionReason.EMPTY_ASSESSMENTS
    }
    saw_zero = False
    for claim in statuses:
        saw_zero = saw_zero or claim.candidate_count == 0
        reason = "" if claim.collection_reason is None else claim.collection_reason.value
        if (
            claim.collection_reason
            is PersistedCandidateCollectionReason.EMPTY_ASSESSMENTS
        ):
            _answer.add(
                f"candidate-status:{claim.recommendation_id}:empty",
                (
                    "The persisted evaluation for recommendation "
                    f"{claim.recommendation_id} contains no candidate airport "
                    "assessments."
                ),
            )
        elif not (
            claim.candidate_count == 0
            and claim.recommendation_id in empty_recommendations
        ):
            noun = "assessment" if claim.candidate_count == 1 else "assessments"
            _answer.add(
                f"candidate-status:{claim.recommendation_id}:{claim.candidate_count}:{reason}",
                (
                    "The persisted evaluation for recommendation "
                    f"{claim.recommendation_id} contains {claim.candidate_count} "
                    f"stored candidate airport {noun}."
                ),
            )
        if (
            claim.collection_reason
            is PersistedCandidateCollectionReason.NO_COMPLETE_ASSESSMENT
        ):
            _answer.add(
                f"candidate-status:{claim.recommendation_id}:no-complete",
                (
                    "The persisted evaluation for recommendation "
                    f"{claim.recommendation_id} has no candidate with a COMPLETE "
                    "stored assessment."
                ),
            )
    if saw_zero:
        _answer.add("candidate-status:zero-not-unsafe", _ZERO_CANDIDATES_NOT_UNSAFE)


def _add_candidates(_answer: _Answer, claims: tuple[object, ...]) -> None:
    candidates = sorted(
        (claim for claim in claims if isinstance(claim, PersistedCandidateClaim)),
        key=lambda claim: (
            claim.recommendation_id,
            claim.airport_id,
            claim.airport_assessment_id,
        ),
    )
    for claim in candidates:
        prefix = (
            "Persisted airport-assessment evidence for airport "
            f"{claim.airport_id} and recommendation {claim.recommendation_id} "
            f"has assessment id {claim.airport_assessment_id}."
        )
        if claim.assessment_status is PersistedAssessmentStatus.COMPLETE:
            detail = (
                "The stored assessment status is COMPLETE, which means persisted "
                "assessment scoring completed. COMPLETE does not mean a safe "
                "airport, current suitability, a selected airport, or a selected "
                "diversion."
            )
        else:
            detail = "The stored assessment status is WAITING_FOR_WEATHER."
        _answer.add(
            (
                "candidate:"
                f"{claim.recommendation_id}:{claim.airport_id}:"
                f"{claim.airport_assessment_id}:{claim.assessment_status.value}"
            ),
            (
                f"{prefix} {detail} Route-safety evidence is unavailable. "
                "Runway evidence is unavailable. Congestion evidence is unavailable."
            ),
        )


def _add_links(_answer: _Answer, claims: tuple[object, ...]) -> None:
    links = sorted(
        (claim for claim in claims if isinstance(claim, CurrentPersistedLinkClaim)),
        key=lambda claim: claim.recommendation_id,
    )
    for claim in links:
        _answer.add(
            f"link:{claim.recommendation_id}",
            (
                "Current recommendation "
                f"{claim.recommendation_id} has verified associated persisted "
                "airport-evaluation evidence. That persisted evidence is not a "
                "current airport candidate, not current suitability, and not a "
                "selected airport."
            ),
        )


def _limitation_tokens(
    claims: tuple[object, ...],
    mandatory: tuple[str, ...],
) -> tuple[set[str], bool]:
    tokens: set[str] = set()
    for claim in claims:
        if isinstance(claim, LimitationClaim):
            tokens.add(_CLAIM_LIMITATION_TOKENS[claim.limitation_code])
    unknown = False
    for item in mandatory:
        token = _MANDATORY_TOKENS.get(item)
        if token is None:
            unknown = True
        else:
            tokens.add(token)
    return tokens, unknown


def _join_ids(ids: tuple[str, ...]) -> str:
    if len(ids) == 1:
        return ids[0]
    if len(ids) == 2:
        return f"{ids[0]} and {ids[1]}"
    return ", ".join(ids[:-1]) + f", and {ids[-1]}"


__all__ = [
    "RECOGNIZED_MEMBERSHIP_LIMITATION",
    "RECOGNIZED_MISSING_REFERENCED_ROW",
    "RECOGNIZED_NOT_CURRENT_LIMITATION",
    "RECOGNIZED_NO_SNAPSHOT_LIMITATION",
    "RECOGNIZED_RECOMMENDATION_ABSENCE_LIMITATION",
    "RECOGNIZED_WAITING_ROWS_ABSENT",
    "DecisionRenderOutcome",
    "DecisionRenderResult",
    "render_decision_verification",
]
