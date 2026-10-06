"""Deterministic Decision claim verifier.

Proposed Decision claims are untrusted. Full raw ToolResult objects are the
only verification authority. DecisionToolProjection and DecisionEvidenceSnapshot
are not consulted. This module does not render answers, call a model, choose
an encounter, rank airports, or import operational runtime.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from wilvor_ai.contracts import (
    ContractValidationError,
    JsonValue,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
    _parse_enum,
    _required,
    _sequence,
    _validate_string_tuple,
    _validate_utc,
)
from wilvor_ai.decision_claims import (
    DECISION_EVIDENCE_REF_MAX_LENGTH,
    DECISION_EVIDENCE_REF_PATTERN,
    AircraftIdentityClaim,
    CapabilityGapClaim,
    ChainGapClaim,
    CurrentPersistedLinkClaim,
    DecisionClaim,
    EncounterSetClaim,
    EvaluationStateClaim,
    LimitationClaim,
    PersistedCandidateClaim,
    PersistedCandidateCollectionReason,
    PersistedCandidateStatusClaim,
    PersistedEvaluationClaim,
    RecommendationActionClaim,
    RecommendationSetClaim,
    RiskAbsentClaim,
    RiskPresentClaim,
    ToolStatusClaim,
    decision_claim_from_dict,
)
from wilvor_ai.decision_contracts import (
    DecisionEvidence,
    DecisionEvidenceKind,
    DecisionEvaluationState,
    DecisionReportedLinkState,
    RiskPresence,
    validate_decision_tool_result,
)
from wilvor_ai.persisted_airport_contracts import (
    COMPLETE,
    PersistedAirportEvidence,
    validate_persisted_airport_tool_result,
)
from wilvor_ai.specialist_contracts import VerifierOutcome


DECISION_VERIFICATION_SCHEMA_VERSION = "wilvor.ai.decision_verification.v1"
CLAIM_VERIFICATION_FAILED = "CLAIM_VERIFICATION_FAILED"
CLAIM_SET_NOT_ACTIONABLE = "CLAIM_SET_NOT_ACTIONABLE"

CONTEXT_TOOL = "get_current_decision_context"
RISK_TOOL = "get_current_risk_evidence"
RECOMMENDATION_TOOL = "get_current_recommendation"
PERSISTED_TOOL = "get_persisted_airport_candidate_evidence"

EMPTY_ASSESSMENTS_REASON = "No candidate airport assessment was available."
NO_COMPLETE_ASSESSMENT_REASON = (
    "No candidate airport currently has a COMPLETE assessment."
)
NOT_CURRENT_LIMITATION = (
    "Persisted airport evaluation evidence is not asserted to be current."
)

_CURRENT_TOOLS = frozenset({CONTEXT_TOOL, RISK_TOOL, RECOMMENDATION_TOOL})
_RECOMMENDATION_CLAIM_TOOLS = frozenset({CONTEXT_TOOL, RECOMMENDATION_TOOL})
_KIND_BY_TOOL = {
    CONTEXT_TOOL: DecisionEvidenceKind.DECISION_CONTEXT,
    RISK_TOOL: DecisionEvidenceKind.RISK_EVIDENCE,
    RECOMMENDATION_TOOL: DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
}
_ESTABLISHED_STATUSES = frozenset({ToolResultStatus.SUCCESS, ToolResultStatus.PARTIAL})
_PERSISTED_FACT_STATUSES = _ESTABLISHED_STATUSES
_SCOPE_ORDER = (TemporalScope.CURRENT, TemporalScope.PERSISTED)
_RESULT_KEYS = frozenset(
    {
        "schema_version",
        "outcome",
        "verified_claims",
        "rejected_claim_codes",
        "mandatory_limitations",
        "used_evidence_refs",
        "verified_temporal_scopes",
        "current_as_of_utc",
    }
)
_UNMAPPABLE = object()

_CLAIM_TYPES = (
    AircraftIdentityClaim,
    EvaluationStateClaim,
    EncounterSetClaim,
    RiskAbsentClaim,
    RiskPresentClaim,
    RecommendationSetClaim,
    RecommendationActionClaim,
    ChainGapClaim,
    CapabilityGapClaim,
    PersistedEvaluationClaim,
    PersistedCandidateClaim,
    PersistedCandidateStatusClaim,
    ToolStatusClaim,
    LimitationClaim,
    CurrentPersistedLinkClaim,
)
_FACTUAL_CLAIM_TYPES = tuple(
    claim_type for claim_type in _CLAIM_TYPES if claim_type is not LimitationClaim
)
_PRESENCE_CLAIM_TYPES = (
    EncounterSetClaim,
    RiskAbsentClaim,
    RiskPresentClaim,
    RecommendationSetClaim,
    RecommendationActionClaim,
)


class DecisionClaimRejectionCode(str, Enum):
    """Closed Decision verification failures. Not model prose."""

    EMPTY_CLAIM_SET = "EMPTY_CLAIM_SET"
    UNKNOWN_EVIDENCE_REF = "UNKNOWN_EVIDENCE_REF"
    DUPLICATE_EVIDENCE_REF = "DUPLICATE_EVIDENCE_REF"
    DUPLICATE_RAW_TOOL_CALL_ID = "DUPLICATE_RAW_TOOL_CALL_ID"
    RAW_TOOL_RESULT_INVALID = "RAW_TOOL_RESULT_INVALID"
    CLAIM_SCOPE_MISMATCH = "CLAIM_SCOPE_MISMATCH"
    CLAIM_TOOL_MISMATCH = "CLAIM_TOOL_MISMATCH"
    CLAIM_VALUE_MISMATCH = "CLAIM_VALUE_MISMATCH"
    AMBIGUOUS_CLAIM_TARGET = "AMBIGUOUS_CLAIM_TARGET"
    DUPLICATE_CLAIM = "DUPLICATE_CLAIM"
    CONTRADICTORY_CLAIMS = "CONTRADICTORY_CLAIMS"
    AIRCRAFT_IDENTITY_MISMATCH = "AIRCRAFT_IDENTITY_MISMATCH"
    CURRENT_AS_OF_MISMATCH = "CURRENT_AS_OF_MISMATCH"
    CURRENT_CORRELATION_MISMATCH = "CURRENT_CORRELATION_MISMATCH"
    CURRENT_CONTEXT_CONTRADICTION = "CURRENT_CONTEXT_CONTRADICTION"
    CURRENT_RISK_CONTRADICTION = "CURRENT_RISK_CONTRADICTION"
    CURRENT_RECOMMENDATION_CONTRADICTION = "CURRENT_RECOMMENDATION_CONTRADICTION"
    CURRENT_PERSISTED_LINK_MISMATCH = "CURRENT_PERSISTED_LINK_MISMATCH"
    PERSISTED_EVIDENCE_CONTRADICTION = "PERSISTED_EVIDENCE_CONTRADICTION"
    CLAIM_SET_NOT_ACTIONABLE = CLAIM_SET_NOT_ACTIONABLE


@dataclass(frozen=True)
class _RiskFact:
    encounter_id: str | None
    presence: RiskPresence
    risk_id: str | None
    risk_level: Any
    risk_score: int | None


@dataclass(frozen=True)
class _RecommendationFact:
    recommendation_id: str
    primary_action_type: Any
    advisory_authority: Any


@dataclass(frozen=True)
class _RecommendationView:
    lifted: bool
    encounter_count: int
    facts: tuple[_RecommendationFact, ...]


@dataclass(frozen=True)
class _ParsedBinding:
    evidence_ref: str
    result: ToolResult
    evidence: DecisionEvidence | None = None
    persisted: PersistedAirportEvidence | None = None


def _is_decision_claim(value: object) -> bool:
    return isinstance(value, _CLAIM_TYPES)


def _ordered_codes(codes: tuple[DecisionClaimRejectionCode, ...] | list[Any]) -> tuple[str, ...]:
    present = {item.value if isinstance(item, DecisionClaimRejectionCode) else item for item in codes}
    return tuple(item.value for item in DecisionClaimRejectionCode if item.value in present)


def _claim_key(claim: DecisionClaim) -> str:
    return json.dumps(claim.to_dict(), sort_keys=True, separators=(",", ":"))


def _claim_refs(claim: DecisionClaim) -> tuple[str, ...]:
    if isinstance(claim, CurrentPersistedLinkClaim):
        return (claim.current_evidence_ref, claim.persisted_evidence_ref)
    return (claim.evidence_ref,)


def _used_refs(claims: tuple[DecisionClaim, ...]) -> tuple[str, ...]:
    refs: list[str] = []
    for claim in claims:
        for ref in _claim_refs(claim):
            if ref not in refs:
                refs.append(ref)
    return tuple(refs)


def _claim_scopes(claims: tuple[DecisionClaim, ...]) -> tuple[TemporalScope, ...]:
    """Scopes named by the claims themselves. This does not read ToolResults."""

    found: set[TemporalScope] = set()
    for claim in claims:
        if isinstance(claim, CurrentPersistedLinkClaim):
            found.add(TemporalScope.CURRENT)
            found.add(TemporalScope.PERSISTED)
        else:
            found.add(claim.evidence_scope)
    return tuple(scope for scope in _SCOPE_ORDER if scope in found)


def _evidence_ref_ok(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= DECISION_EVIDENCE_REF_MAX_LENGTH
        and DECISION_EVIDENCE_REF_PATTERN.fullmatch(value) is not None
    )


def _scopes_canonical(scopes: tuple[Any, ...]) -> bool:
    if not isinstance(scopes, tuple):
        return False
    if any(item not in _SCOPE_ORDER for item in scopes):
        return False
    if len(scopes) != len(set(scopes)):
        return False
    return scopes == tuple(item for item in _SCOPE_ORDER if item in scopes)


@dataclass(frozen=True)
class DecisionEvidenceBinding:
    """Runtime-owned de-N handle for one raw ToolResult.

    This type has no model parser. ``tool_call_id`` stays on the ToolResult.
    """

    evidence_ref: str
    raw_tool_result: ToolResult

    def __post_init__(self) -> None:
        errors: list[str] = []
        if not _evidence_ref_ok(self.evidence_ref):
            errors.append("invalid_evidence_ref")
        if not isinstance(self.raw_tool_result, ToolResult):
            errors.append("invalid_tool_result")
        if errors:
            raise ContractValidationError(errors)


@dataclass(frozen=True)
class DecisionVerificationResult:
    """Atomic Decision verification artifact. Not a specialist answer.

    Structural checks do not re-resolve evidence refs. The verifier computes
    used refs, scopes, and the current instant from the bindings it received.
    """

    outcome: VerifierOutcome
    verified_claims: tuple[DecisionClaim, ...]
    rejected_claim_codes: tuple[str, ...]
    mandatory_limitations: tuple[str, ...]
    used_evidence_refs: tuple[str, ...]
    verified_temporal_scopes: tuple[TemporalScope, ...]
    current_as_of_utc: str | None
    schema_version: str = DECISION_VERIFICATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        errors: list[str] = []
        if self.schema_version != DECISION_VERIFICATION_SCHEMA_VERSION:
            errors.append("invalid_schema_version")
        if not isinstance(self.outcome, VerifierOutcome) or self.outcome is VerifierOutcome.NOT_RUN:
            errors.append("invalid_outcome")
        if not isinstance(self.verified_claims, tuple) or any(
            not _is_decision_claim(item) for item in self.verified_claims
        ):
            errors.append("invalid_verified_claims")
        errors.extend(
            _validate_string_tuple(self.rejected_claim_codes, "rejected_claim_codes")
        )
        allowed_codes = {item.value for item in DecisionClaimRejectionCode}
        if any(item not in allowed_codes for item in self.rejected_claim_codes):
            errors.append("invalid_rejected_claim_codes")
        if len(self.rejected_claim_codes) != len(set(self.rejected_claim_codes)):
            errors.append("duplicate_rejected_claim_codes")
        errors.extend(
            _validate_string_tuple(self.mandatory_limitations, "mandatory_limitations")
        )
        if not isinstance(self.used_evidence_refs, tuple) or any(
            not _evidence_ref_ok(item) for item in self.used_evidence_refs
        ):
            errors.append("invalid_used_evidence_refs")
        if len(self.used_evidence_refs) != len(set(self.used_evidence_refs)):
            errors.append("duplicate_used_evidence_refs")
        if not _scopes_canonical(self.verified_temporal_scopes):
            errors.append("invalid_verified_temporal_scopes")
        errors.extend(
            _validate_utc(self.current_as_of_utc, "current_as_of_utc", optional=True)
        )
        has_current = (
            isinstance(self.verified_temporal_scopes, tuple)
            and TemporalScope.CURRENT in self.verified_temporal_scopes
        )
        if has_current == (self.current_as_of_utc is None):
            errors.append("current_as_of_scope_mismatch")
        if self.outcome is VerifierOutcome.PASSED:
            if not self.verified_claims:
                errors.append("passed_requires_verified_claims")
            if self.rejected_claim_codes:
                errors.append("passed_forbids_rejections")
            if CLAIM_VERIFICATION_FAILED in self.mandatory_limitations:
                errors.append("passed_forbids_verification_failure")
            if CLAIM_SET_NOT_ACTIONABLE in self.mandatory_limitations:
                errors.append("passed_forbids_unactionable_limitation")
            claims_ok = isinstance(self.verified_claims, tuple) and bool(
                self.verified_claims
            ) and all(_is_decision_claim(item) for item in self.verified_claims)
            if claims_ok:
                if self.used_evidence_refs != _used_refs(self.verified_claims):
                    errors.append("used_evidence_refs_mismatch")
                if self.verified_temporal_scopes != _claim_scopes(self.verified_claims):
                    errors.append("verified_temporal_scopes_mismatch")
        if self.outcome is VerifierOutcome.FAILED:
            if self.verified_claims:
                errors.append("failed_forbids_verified_claims")
            if not self.rejected_claim_codes:
                errors.append("failed_requires_rejections")
            if CLAIM_VERIFICATION_FAILED not in self.mandatory_limitations:
                errors.append("failed_requires_verification_failure")
            if self.used_evidence_refs:
                errors.append("failed_forbids_used_evidence_refs")
            if self.verified_temporal_scopes:
                errors.append("failed_forbids_temporal_scopes")
            if self.current_as_of_utc is not None:
                errors.append("failed_forbids_current_as_of")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "outcome": self.outcome.value,
            "verified_claims": [item.to_dict() for item in self.verified_claims],
            "rejected_claim_codes": list(self.rejected_claim_codes),
            "mandatory_limitations": list(self.mandatory_limitations),
            "used_evidence_refs": list(self.used_evidence_refs),
            "verified_temporal_scopes": [item.value for item in self.verified_temporal_scopes],
            "current_as_of_utc": self.current_as_of_utc,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionVerificationResult":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_decision_verification_result")
        extra = set(data) - _RESULT_KEYS
        if extra:
            raise ContractValidationError("unexpected_decision_verification_field")
        return cls(
            schema_version=_required(data, "schema_version"),
            outcome=_parse_enum(VerifierOutcome, _required(data, "outcome"), "outcome"),
            verified_claims=tuple(
                decision_claim_from_dict(item)
                for item in _sequence(_required(data, "verified_claims"), "verified_claims")
            ),
            rejected_claim_codes=tuple(
                _sequence(
                    _required(data, "rejected_claim_codes"),
                    "rejected_claim_codes",
                )
            ),
            mandatory_limitations=tuple(
                _sequence(
                    _required(data, "mandatory_limitations"),
                    "mandatory_limitations",
                )
            ),
            used_evidence_refs=tuple(
                _sequence(_required(data, "used_evidence_refs"), "used_evidence_refs")
            ),
            verified_temporal_scopes=tuple(
                _parse_enum(TemporalScope, item, "verified_temporal_scopes")
                for item in _sequence(
                    _required(data, "verified_temporal_scopes"),
                    "verified_temporal_scopes",
                )
            ),
            current_as_of_utc=_required(data, "current_as_of_utc"),
        )


def verify_decision_evidence(
    claims: tuple[DecisionClaim, ...],
    bindings: tuple[DecisionEvidenceBinding, ...],
) -> DecisionVerificationResult:
    """Verify untrusted claims against raw ToolResults only."""

    if not isinstance(claims, tuple) or any(not _is_decision_claim(item) for item in claims):
        raise TypeError("claims must be a tuple of Decision claims")
    if not isinstance(bindings, tuple) or any(
        not isinstance(item, DecisionEvidenceBinding) for item in bindings
    ):
        raise TypeError("bindings must be a tuple of DecisionEvidenceBinding")
    if not claims:
        return _failed((DecisionClaimRejectionCode.EMPTY_CLAIM_SET,))

    indexed, binding_codes = _index_bindings(bindings)
    if binding_codes:
        return _failed(binding_codes)
    parsed, raw_codes = _parse_bindings(indexed)
    if raw_codes:
        return _failed(raw_codes)

    codes: list[DecisionClaimRejectionCode] = []
    codes.extend(_cross_current(parsed))
    codes.extend(_cross_persisted(parsed))
    if not any(isinstance(item, _FACTUAL_CLAIM_TYPES) for item in claims):
        codes.append(DecisionClaimRejectionCode.CLAIM_SET_NOT_ACTIONABLE)
    if _duplicate_claim(claims):
        codes.append(DecisionClaimRejectionCode.DUPLICATE_CLAIM)
    if _contradictory_claims(claims):
        codes.append(DecisionClaimRejectionCode.CONTRADICTORY_CLAIMS)
    if codes:
        return _failed(tuple(codes))

    for claim in claims:
        rejected = _verify_claim(claim, parsed)
        if rejected is not None:
            return _failed((rejected,))
    return _passed(claims, parsed)


def _failed(
    codes: tuple[DecisionClaimRejectionCode, ...] | list[DecisionClaimRejectionCode],
) -> DecisionVerificationResult:
    ordered = _ordered_codes(tuple(codes))
    limitations = [CLAIM_VERIFICATION_FAILED]
    if DecisionClaimRejectionCode.CLAIM_SET_NOT_ACTIONABLE.value in ordered:
        limitations.append(CLAIM_SET_NOT_ACTIONABLE)
    return DecisionVerificationResult(
        outcome=VerifierOutcome.FAILED,
        verified_claims=(),
        rejected_claim_codes=ordered,
        mandatory_limitations=tuple(limitations),
        used_evidence_refs=(),
        verified_temporal_scopes=(),
        current_as_of_utc=None,
    )


def _passed(
    claims: tuple[DecisionClaim, ...],
    parsed: dict[str, _ParsedBinding],
) -> DecisionVerificationResult:
    scopes = _verified_scopes(claims, parsed)
    return DecisionVerificationResult(
        outcome=VerifierOutcome.PASSED,
        verified_claims=claims,
        rejected_claim_codes=(),
        mandatory_limitations=_mandatory_limitations(claims, parsed),
        used_evidence_refs=_used_refs(claims),
        verified_temporal_scopes=scopes,
        current_as_of_utc=_current_as_of(scopes, parsed),
    )


def _index_bindings(
    bindings: tuple[DecisionEvidenceBinding, ...],
) -> tuple[dict[str, DecisionEvidenceBinding], tuple[DecisionClaimRejectionCode, ...]]:
    by_ref: dict[str, DecisionEvidenceBinding] = {}
    seen_calls: set[str] = set()
    codes: list[DecisionClaimRejectionCode] = []
    for binding in bindings:
        if binding.evidence_ref in by_ref:
            codes.append(DecisionClaimRejectionCode.DUPLICATE_EVIDENCE_REF)
        else:
            by_ref[binding.evidence_ref] = binding
        tool_call_id = binding.raw_tool_result.tool_call_id
        if tool_call_id in seen_calls:
            codes.append(DecisionClaimRejectionCode.DUPLICATE_RAW_TOOL_CALL_ID)
        else:
            seen_calls.add(tool_call_id)
    return by_ref, tuple(codes)


def _parse_bindings(
    indexed: dict[str, DecisionEvidenceBinding],
) -> tuple[dict[str, _ParsedBinding], tuple[DecisionClaimRejectionCode, ...]]:
    parsed: dict[str, _ParsedBinding] = {}
    invalid = False
    for ref, binding in indexed.items():
        item, ok = _parse_one(ref, binding.raw_tool_result)
        if not ok or item is None:
            invalid = True
            continue
        parsed[ref] = item
    if invalid:
        return {}, (DecisionClaimRejectionCode.RAW_TOOL_RESULT_INVALID,)
    return parsed, ()


def _parse_one(
    evidence_ref: str,
    result: ToolResult,
) -> tuple[_ParsedBinding | None, bool]:
    if result.tool_name in _CURRENT_TOOLS:
        if result.temporal_scope is not TemporalScope.CURRENT:
            return None, False
        try:
            evidence = validate_decision_tool_result(result)
        except ContractValidationError:
            return None, False
        if evidence.kind is not _KIND_BY_TOOL[result.tool_name]:
            return None, False
        if not _current_facts_consistent(evidence):
            return None, False
        return _ParsedBinding(evidence_ref, result, evidence=evidence), True
    if result.tool_name == PERSISTED_TOOL:
        if result.temporal_scope is not TemporalScope.PERSISTED:
            return None, False
        try:
            persisted = validate_persisted_airport_tool_result(result)
        except ContractValidationError:
            return None, False
        if not _persisted_reason_consistent(persisted):
            return None, False
        return _ParsedBinding(evidence_ref, result, persisted=persisted), True
    return None, False


def _current_facts_consistent(evidence: DecisionEvidence) -> bool:
    seen_encounters: set[str] = set()
    facts: list[_RiskFact] = []
    if evidence.risk is not None and not evidence.encounters:
        facts.append(_risk_fact(evidence.risk.encounter_id, evidence.risk))
    else:
        for encounter in evidence.encounters:
            if encounter.encounter_id in seen_encounters:
                return False
            seen_encounters.add(encounter.encounter_id)
            if (
                encounter.risk.encounter_id is not None
                and encounter.risk.encounter_id != encounter.encounter_id
            ):
                return False
            facts.append(_risk_fact(encounter.encounter_id, encounter.risk))
    by_risk: dict[str, _RiskFact] = {}
    for fact in facts:
        if fact.risk_id is None:
            continue
        previous = by_risk.get(fact.risk_id)
        if previous is not None and previous != fact:
            return False
        by_risk[fact.risk_id] = fact
    seen_recommendations: dict[str, tuple[Any, Any]] = {}
    for fact in _recommendation_facts(evidence):
        signature = (fact.primary_action_type, fact.advisory_authority)
        previous = seen_recommendations.get(fact.recommendation_id)
        if previous is not None and previous != signature:
            return False
        seen_recommendations[fact.recommendation_id] = signature
    return True


def _persisted_reason_consistent(persisted: PersistedAirportEvidence) -> bool:
    reason = persisted.no_suitable_candidate_reason
    if reason == EMPTY_ASSESSMENTS_REASON and persisted.candidates:
        return False
    if reason == NO_COMPLETE_ASSESSMENT_REASON and any(
        item.assessment_status == COMPLETE for item in persisted.candidates
    ):
        return False
    return True


def _risk_fact(encounter_id: str | None, risk: Any) -> _RiskFact:
    return _RiskFact(
        encounter_id=encounter_id,
        presence=risk.presence,
        risk_id=risk.risk_id,
        risk_level=risk.risk_level,
        risk_score=risk.risk_score,
    )


def _claimable_risk_facts(evidence: DecisionEvidence) -> tuple[_RiskFact, ...]:
    """Risk facts an established in-set result may support.

    Unestablished placeholders and aircraft-not-in-set shapes contribute none.
    A structural ABSENT row is not operational absence in those cases.
    """

    if not _operationally_established(evidence):
        return ()
    if evidence.risk is not None and not evidence.encounters:
        return (_risk_fact(evidence.risk.encounter_id, evidence.risk),)
    return tuple(
        _risk_fact(encounter.encounter_id, encounter.risk)
        for encounter in evidence.encounters
    )


def _operationally_established(evidence: DecisionEvidence) -> bool:
    return (
        evidence.evaluation_state is DecisionEvaluationState.ESTABLISHED
        and evidence.aircraft_in_current_set is True
    )


def _status_established(result: ToolResult) -> bool:
    return result.status in _ESTABLISHED_STATUSES


def _presence_established(evidence: DecisionEvidence, result: ToolResult) -> bool:
    return _operationally_established(evidence) and _status_established(result)


def _recommendation_facts(evidence: DecisionEvidence) -> tuple[_RecommendationFact, ...]:
    objects: list[Any] = []
    if evidence.recommendations is not None:
        objects.extend(evidence.recommendations.current)
    for encounter in evidence.encounters:
        objects.extend(encounter.recommendations.current)
    return tuple(
        _RecommendationFact(
            recommendation_id=item.recommendation_id,
            primary_action_type=item.primary_action_type,
            advisory_authority=item.advisory_authority,
        )
        for item in objects
    )


def _recommendation_view(evidence: DecisionEvidence) -> _RecommendationView | None:
    if not _operationally_established(evidence):
        return None
    if evidence.recommendations is not None:
        return _RecommendationView(
            lifted=True,
            encounter_count=0,
            facts=_recommendation_facts(evidence),
        )
    if evidence.encounters:
        return _RecommendationView(
            lifted=False,
            encounter_count=len(evidence.encounters),
            facts=_recommendation_facts(evidence),
        )
    return None


def _complete_recommendation_ids(
    facts: tuple[_RecommendationFact, ...],
) -> tuple[str, ...]:
    return tuple(sorted({item.recommendation_id for item in facts}))


def _cross_current(
    parsed: dict[str, _ParsedBinding],
) -> list[DecisionClaimRejectionCode]:
    current = [item for item in parsed.values() if item.evidence is not None]
    if not current:
        return []
    codes: list[DecisionClaimRejectionCode] = []
    as_of_values = [item.result.as_of_utc for item in current]
    if any(item is None for item in as_of_values) or len(set(as_of_values)) != 1:
        codes.append(DecisionClaimRejectionCode.CURRENT_AS_OF_MISMATCH)
    correlations = [
        item.result.correlation_id
        for item in current
        if item.result.correlation_id is not None
    ]
    if len(set(correlations)) > 1:
        codes.append(DecisionClaimRejectionCode.CURRENT_CORRELATION_MISMATCH)

    established = [
        item.evidence
        for item in current
        if item.evidence is not None
        and item.evidence.evaluation_state is DecisionEvaluationState.ESTABLISHED
    ]
    unestablished = [
        item.evidence
        for item in current
        if item.evidence is not None
        and item.evidence.evaluation_state is not DecisionEvaluationState.ESTABLISHED
    ]
    if established and unestablished:
        codes.append(DecisionClaimRejectionCode.CURRENT_CONTEXT_CONTRADICTION)
    if len(established) >= 2:
        baseline = established[0]
        for evidence in established[1:]:
            if (
                evidence.aircraft_in_current_set != baseline.aircraft_in_current_set
                or evidence.projection_state != baseline.projection_state
                or evidence.projection_id != baseline.projection_id
                or evidence.aircraft_state_version != baseline.aircraft_state_version
            ):
                codes.append(DecisionClaimRejectionCode.CURRENT_CONTEXT_CONTRADICTION)
                break
    aircraft_ids = {
        item.evidence.aircraft_id
        for item in current
        if item.evidence is not None and item.evidence.aircraft_id is not None
    }
    if len(aircraft_ids) > 1:
        codes.append(DecisionClaimRejectionCode.AIRCRAFT_IDENTITY_MISMATCH)
    codes.extend(_cross_encounters_and_risk(current))
    codes.extend(
        _cross_recommendations(
            [item.evidence for item in current if item.evidence is not None]
        )
    )
    return codes


def _encounter_id_set(evidence: DecisionEvidence) -> tuple[str, ...]:
    return tuple(sorted(encounter.encounter_id for encounter in evidence.encounters))


def _context_authorities(current: list[_ParsedBinding]) -> list[DecisionEvidence]:
    """In-set context results are the complete encounter collection."""

    authorities: list[DecisionEvidence] = []
    for item in current:
        if item.result.tool_name != CONTEXT_TOOL or item.evidence is None:
            continue
        if _operationally_established(item.evidence):
            authorities.append(item.evidence)
    return authorities


def _cross_encounters_and_risk(
    current: list[_ParsedBinding],
) -> list[DecisionClaimRejectionCode]:
    codes: list[DecisionClaimRejectionCode] = []
    authorities = _context_authorities(current)
    evidences = [item.evidence for item in current if item.evidence is not None]
    if authorities:
        baseline = _encounter_id_set(authorities[0])
        if any(_encounter_id_set(item) != baseline for item in authorities[1:]):
            codes.append(DecisionClaimRejectionCode.CURRENT_CONTEXT_CONTRADICTION)
        for item in current:
            if (
                item.evidence is None
                or item.result.tool_name == CONTEXT_TOOL
                or not _operationally_established(item.evidence)
            ):
                continue
            for authority in authorities:
                _compare_narrow_to_context(item.evidence, authority, codes)
    else:
        codes.extend(_cross_narrow_encounters_without_context(evidences))
    codes.extend(_cross_risk_facts(evidences))
    return codes


def _compare_narrow_to_context(
    narrow: DecisionEvidence,
    context: DecisionEvidence,
    codes: list[DecisionClaimRejectionCode],
) -> None:
    """Compare a narrow current result with the complete context encounter set.

    A lifted top-level risk against a complete zero-encounter context is
    ``CURRENT_CONTEXT_CONTRADICTION``. The authoritative set contains no
    encounter that could carry that risk. The same code is used when the
    lifted risk names an encounter the empty set does not contain, and when
    a lift coexists with more than one context encounter. A null encounter id
    does not exempt a lift from the multi-encounter rule.

    ``CURRENT_RISK_CONTRADICTION`` is only for a single context encounter
    whose stored risk fact disagrees, including a null lifted encounter id
    compared verifier-side with that sole encounter.
    """

    if narrow.encounters:
        if _encounter_id_set(narrow) != _encounter_id_set(context):
            codes.append(DecisionClaimRejectionCode.CURRENT_CONTEXT_CONTRADICTION)
        return
    if narrow.risk is None:
        return
    risk = narrow.risk
    count = len(context.encounters)
    if count > 1:
        codes.append(DecisionClaimRejectionCode.CURRENT_CONTEXT_CONTRADICTION)
        return
    if count == 0:
        if risk.presence is RiskPresence.PRESENT or risk.encounter_id is not None:
            codes.append(DecisionClaimRejectionCode.CURRENT_CONTEXT_CONTRADICTION)
        return
    sole = context.encounters[0]
    if risk.encounter_id is not None:
        if risk.encounter_id != sole.encounter_id:
            codes.append(DecisionClaimRejectionCode.CURRENT_RISK_CONTRADICTION)
        return
    lifted = _risk_fact(sole.encounter_id, risk)
    stored = _risk_fact(sole.encounter_id, sole.risk)
    if lifted != stored:
        codes.append(DecisionClaimRejectionCode.CURRENT_RISK_CONTRADICTION)


def _cross_narrow_encounters_without_context(
    evidences: list[DecisionEvidence],
) -> list[DecisionClaimRejectionCode]:
    """Narrow-to-narrow behavior used only when no context binding exists."""

    codes: list[DecisionClaimRejectionCode] = []
    nonempty = [
        tuple(encounter.encounter_id for encounter in evidence.encounters)
        for evidence in evidences
        if evidence.encounters
    ]
    if len(nonempty) >= 2 and any(set(item) != set(nonempty[0]) for item in nonempty[1:]):
        codes.append(DecisionClaimRejectionCode.CURRENT_CONTEXT_CONTRADICTION)
    lifted = [
        evidence
        for evidence in evidences
        if evidence.risk is not None and not evidence.encounters and evidence.risk.encounter_id
    ]
    for evidence in lifted:
        encounter_id = evidence.risk.encounter_id if evidence.risk is not None else None
        for other in evidences:
            if other is evidence or not other.encounters:
                continue
            if len(other.encounters) > 1:
                codes.append(DecisionClaimRejectionCode.CURRENT_CONTEXT_CONTRADICTION)
            elif encounter_id not in {item.encounter_id for item in other.encounters}:
                codes.append(DecisionClaimRejectionCode.CURRENT_RISK_CONTRADICTION)
    return codes


def _cross_risk_facts(
    evidences: list[DecisionEvidence],
) -> list[DecisionClaimRejectionCode]:
    codes: list[DecisionClaimRejectionCode] = []
    by_key: dict[str | None, _RiskFact] = {}
    for evidence in evidences:
        for fact in _claimable_risk_facts(evidence):
            previous = by_key.get(fact.encounter_id)
            if previous is None:
                by_key[fact.encounter_id] = fact
            elif previous != fact:
                codes.append(DecisionClaimRejectionCode.CURRENT_RISK_CONTRADICTION)
    return codes


def _cross_recommendations(
    evidences: list[DecisionEvidence],
) -> list[DecisionClaimRejectionCode]:
    views = [view for view in (_recommendation_view(evidence) for evidence in evidences) if view is not None]
    codes: list[DecisionClaimRejectionCode] = []
    for index, left in enumerate(views):
        for right in views[index + 1 :]:
            if (left.lifted and right.encounter_count > 1) or (
                right.lifted and left.encounter_count > 1
            ):
                codes.append(DecisionClaimRejectionCode.CURRENT_RECOMMENDATION_CONTRADICTION)
                continue
            if _complete_recommendation_ids(left.facts) != _complete_recommendation_ids(right.facts):
                codes.append(DecisionClaimRejectionCode.CURRENT_RECOMMENDATION_CONTRADICTION)
                continue
            left_actions = {
                (item.recommendation_id, item.primary_action_type, item.advisory_authority)
                for item in left.facts
            }
            right_actions = {
                (item.recommendation_id, item.primary_action_type, item.advisory_authority)
                for item in right.facts
            }
            if left_actions != right_actions:
                codes.append(DecisionClaimRejectionCode.CURRENT_RECOMMENDATION_CONTRADICTION)
    return codes


def _candidate_signature(candidate: Any) -> tuple[Any, ...]:
    return (
        candidate.airport_id,
        candidate.assessment_status,
        candidate.route_safety_status,
        candidate.runway_evidence_status,
        candidate.congestion_evidence_status,
    )


def _cross_persisted(
    parsed: dict[str, _ParsedBinding],
) -> list[DecisionClaimRejectionCode]:
    grouped: dict[str, list[_ParsedBinding]] = {}
    for item in parsed.values():
        if item.persisted is None:
            continue
        grouped.setdefault(item.persisted.recommendation_id, []).append(item)
    for group in grouped.values():
        if len(group) < 2:
            continue
        baseline = group[0]
        assert baseline.persisted is not None
        for other in group[1:]:
            assert other.persisted is not None
            if (
                other.result.status is not baseline.result.status
                or other.persisted.airport_evaluation_id != baseline.persisted.airport_evaluation_id
            ):
                return [DecisionClaimRejectionCode.PERSISTED_EVIDENCE_CONTRADICTION]
            left = {
                item.airport_assessment_id: _candidate_signature(item)
                for item in baseline.persisted.candidates
            }
            right = {
                item.airport_assessment_id: _candidate_signature(item)
                for item in other.persisted.candidates
            }
            shared = set(left) & set(right)
            if any(left[key] != right[key] for key in shared):
                return [DecisionClaimRejectionCode.PERSISTED_EVIDENCE_CONTRADICTION]
    return []


def _duplicate_claim(claims: tuple[DecisionClaim, ...]) -> bool:
    seen: set[str] = set()
    for claim in claims:
        key = _claim_key(claim)
        if key in seen:
            return True
        seen.add(key)
    return False


def _risk_claims_disagree(claims: list[RiskAbsentClaim | RiskPresentClaim]) -> bool:
    if any(isinstance(item, RiskAbsentClaim) for item in claims) and any(
        isinstance(item, RiskPresentClaim) for item in claims
    ):
        return True
    presents = [item for item in claims if isinstance(item, RiskPresentClaim)]
    signatures = {(item.risk_id, item.risk_level, item.risk_score) for item in presents}
    return len(signatures) > 1


def _contradictory_claims(claims: tuple[DecisionClaim, ...]) -> bool:
    identities = [item.aircraft_id for item in claims if isinstance(item, AircraftIdentityClaim)]
    if len(set(identities)) > 1:
        return True

    def grouped(claim_type: type[Any]) -> dict[str, list[Any]]:
        groups: dict[str, list[Any]] = {}
        for item in claims:
            if isinstance(item, claim_type):
                groups.setdefault(item.evidence_ref, []).append(item)
        return groups

    for group in grouped(EvaluationStateClaim).values():
        if len({item.evaluation_state for item in group}) > 1:
            return True
    for group in grouped(ToolStatusClaim).values():
        if len({item.status for item in group}) > 1:
            return True
    for group in grouped(EncounterSetClaim).values():
        if len({item.encounter_ids for item in group}) > 1:
            return True
    for group in grouped(RecommendationSetClaim).values():
        if len({(item.recommendation_ids, item.absence_state) for item in group}) > 1:
            return True

    risk_claims = [
        item for item in claims if isinstance(item, (RiskAbsentClaim, RiskPresentClaim))
    ]
    by_ref: dict[str, list[RiskAbsentClaim | RiskPresentClaim]] = {}
    for item in risk_claims:
        by_ref.setdefault(item.evidence_ref, []).append(item)
    for group in by_ref.values():
        if any(item.encounter_id is None for item in group) and len(group) > 1:
            return True
        by_encounter: dict[str, list[RiskAbsentClaim | RiskPresentClaim]] = {}
        for item in group:
            if item.encounter_id is not None:
                by_encounter.setdefault(item.encounter_id, []).append(item)
        if any(_risk_claims_disagree(items) for items in by_encounter.values()):
            return True
    by_encounter_global: dict[str, list[RiskAbsentClaim | RiskPresentClaim]] = {}
    for item in risk_claims:
        if item.encounter_id is not None:
            by_encounter_global.setdefault(item.encounter_id, []).append(item)
    if any(_risk_claims_disagree(items) for items in by_encounter_global.values()):
        return True

    actions: dict[str, set[tuple[Any, Any]]] = {}
    for item in claims:
        if isinstance(item, RecommendationActionClaim):
            actions.setdefault(item.recommendation_id, set()).add(
                (item.primary_action_type, item.advisory_authority)
            )
    if any(len(signatures) > 1 for signatures in actions.values()):
        return True

    candidates: dict[str, set[tuple[Any, ...]]] = {}
    for item in claims:
        if isinstance(item, PersistedCandidateClaim):
            candidates.setdefault(item.airport_assessment_id, set()).add(
                (
                    item.recommendation_id,
                    item.airport_id,
                    item.assessment_status,
                    item.route_safety_status,
                    item.runway_evidence_status,
                    item.congestion_evidence_status,
                )
            )
    if any(len(signatures) > 1 for signatures in candidates.values()):
        return True

    links: dict[tuple[str, str], set[str]] = {}
    for item in claims:
        if isinstance(item, CurrentPersistedLinkClaim):
            links.setdefault(
                (item.current_evidence_ref, item.persisted_evidence_ref),
                set(),
            ).add(item.recommendation_id)
    return any(len(ids) > 1 for ids in links.values())


def _verify_claim(
    claim: DecisionClaim,
    parsed: dict[str, _ParsedBinding],
) -> DecisionClaimRejectionCode | None:
    if isinstance(claim, CurrentPersistedLinkClaim):
        return _verify_link(claim, parsed)
    item = parsed.get(claim.evidence_ref)
    if item is None:
        return DecisionClaimRejectionCode.UNKNOWN_EVIDENCE_REF
    if claim.evidence_scope is not item.result.temporal_scope:
        return DecisionClaimRejectionCode.CLAIM_SCOPE_MISMATCH
    if isinstance(claim, AircraftIdentityClaim):
        return _verify_identity(claim, item)
    if isinstance(claim, EvaluationStateClaim):
        return _verify_evaluation_state(claim, item)
    if isinstance(claim, EncounterSetClaim):
        return _verify_encounter_set(claim, item)
    if isinstance(claim, (RiskAbsentClaim, RiskPresentClaim)):
        return _verify_risk(claim, item)
    if isinstance(claim, RecommendationSetClaim):
        return _verify_recommendation_set(claim, item)
    if isinstance(claim, RecommendationActionClaim):
        return _verify_recommendation_action(claim, item)
    if isinstance(claim, ChainGapClaim):
        return _verify_chain_gap(claim, item)
    if isinstance(claim, CapabilityGapClaim):
        return _verify_capability_gap(claim, item)
    if isinstance(claim, PersistedEvaluationClaim):
        return _verify_persisted_evaluation(claim, item)
    if isinstance(claim, PersistedCandidateClaim):
        return _verify_persisted_candidate(claim, item)
    if isinstance(claim, PersistedCandidateStatusClaim):
        return _verify_persisted_status(claim, item)
    if isinstance(claim, ToolStatusClaim):
        return _verify_tool_status(claim, item)
    if isinstance(claim, LimitationClaim):
        return _verify_limitation(claim, item)
    return DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH


def _require_current(
    item: _ParsedBinding,
    allowed_tools: frozenset[str],
) -> DecisionClaimRejectionCode | None:
    if item.evidence is None or item.result.tool_name not in allowed_tools:
        return DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH
    return None


def _verify_identity(
    claim: AircraftIdentityClaim,
    item: _ParsedBinding,
) -> DecisionClaimRejectionCode | None:
    rejected = _require_current(item, _CURRENT_TOOLS)
    if rejected is not None or item.evidence is None:
        return rejected or DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH
    evidence = item.evidence
    if (
        evidence.evaluation_state is not DecisionEvaluationState.ESTABLISHED
        or evidence.aircraft_id is None
        or evidence.aircraft_id != claim.aircraft_id
    ):
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    return None


def _verify_evaluation_state(
    claim: EvaluationStateClaim,
    item: _ParsedBinding,
) -> DecisionClaimRejectionCode | None:
    rejected = _require_current(item, _CURRENT_TOOLS)
    if rejected is not None or item.evidence is None:
        return rejected or DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH
    if item.evidence.evaluation_state is not claim.evaluation_state:
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    return None


def _verify_encounter_set(
    claim: EncounterSetClaim,
    item: _ParsedBinding,
) -> DecisionClaimRejectionCode | None:
    """Only full current context establishes the encounter set.

    Narrow tools clear ``encounters`` when they lift one encounter. An empty
    narrow tuple is not proof that the aircraft has no current encounters.
    """

    if item.result.tool_name != CONTEXT_TOOL or item.evidence is None:
        return DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH
    if not _presence_established(item.evidence, item.result):
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    actual = tuple(sorted(encounter.encounter_id for encounter in item.evidence.encounters))
    if actual != claim.encounter_ids:
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    return None


def _verify_risk(
    claim: RiskAbsentClaim | RiskPresentClaim,
    item: _ParsedBinding,
) -> DecisionClaimRejectionCode | None:
    rejected = _require_current(item, _CURRENT_TOOLS)
    if rejected is not None or item.evidence is None:
        return rejected or DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH
    if not _presence_established(item.evidence, item.result):
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    facts = _claimable_risk_facts(item.evidence)
    if claim.encounter_id is None:
        if len(facts) != 1:
            return (
                DecisionClaimRejectionCode.AMBIGUOUS_CLAIM_TARGET
                if len(facts) > 1
                else DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
            )
        fact = facts[0]
    else:
        matched = [fact for fact in facts if fact.encounter_id == claim.encounter_id]
        if len(matched) != 1:
            return (
                DecisionClaimRejectionCode.AMBIGUOUS_CLAIM_TARGET
                if len(matched) > 1
                else DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
            )
        fact = matched[0]
    if isinstance(claim, RiskAbsentClaim):
        if fact.presence is not RiskPresence.ABSENT:
            return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
        return None
    if (
        fact.presence is not RiskPresence.PRESENT
        or fact.risk_id != claim.risk_id
        or fact.risk_level != claim.risk_level
        or fact.risk_score != claim.risk_score
    ):
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    return None


def _claimable_recommendation_facts(
    item: _ParsedBinding,
) -> tuple[_RecommendationFact, ...] | None:
    if item.evidence is None or item.result.tool_name not in _RECOMMENDATION_CLAIM_TOOLS:
        return None
    if not _presence_established(item.evidence, item.result):
        return None
    view = _recommendation_view(item.evidence)
    if view is None:
        return None
    return view.facts


def _verify_recommendation_set(
    claim: RecommendationSetClaim,
    item: _ParsedBinding,
) -> DecisionClaimRejectionCode | None:
    if item.result.tool_name not in _RECOMMENDATION_CLAIM_TOOLS or item.evidence is None:
        return DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH
    if not _presence_established(item.evidence, item.result):
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    facts = _claimable_recommendation_facts(item)
    if facts is None:
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    ids = _complete_recommendation_ids(facts)
    expected_absence = (
        DecisionReportedLinkState.PRESENT
        if ids
        else DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES
    )
    if ids != claim.recommendation_ids or claim.absence_state is not expected_absence:
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    return None


def _verify_recommendation_action(
    claim: RecommendationActionClaim,
    item: _ParsedBinding,
) -> DecisionClaimRejectionCode | None:
    if item.result.tool_name not in _RECOMMENDATION_CLAIM_TOOLS or item.evidence is None:
        return DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH
    if not _presence_established(item.evidence, item.result):
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    facts = _claimable_recommendation_facts(item)
    if facts is None:
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    matched = [fact for fact in facts if fact.recommendation_id == claim.recommendation_id]
    if not matched:
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    signatures = {(fact.primary_action_type, fact.advisory_authority) for fact in matched}
    if len(signatures) != 1:
        return DecisionClaimRejectionCode.AMBIGUOUS_CLAIM_TARGET
    action, authority = signatures.pop()
    if action != claim.primary_action_type or authority != claim.advisory_authority:
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    return None


def _verify_chain_gap(
    claim: ChainGapClaim,
    item: _ParsedBinding,
) -> DecisionClaimRejectionCode | None:
    rejected = _require_current(item, _CURRENT_TOOLS)
    if rejected is not None or item.evidence is None:
        return rejected or DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH
    if claim.chain_gap not in item.evidence.chain_gaps:
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    return None


def _verify_capability_gap(
    claim: CapabilityGapClaim,
    item: _ParsedBinding,
) -> DecisionClaimRejectionCode | None:
    if item.result.tool_name != CONTEXT_TOOL or item.evidence is None:
        return DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH
    capability = item.evidence.capability
    if capability is None or claim.capability_gap not in capability.unavailable:
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    return None


def _mapped_reason(reason: str | None) -> Any:
    if reason is None:
        return None
    if reason == EMPTY_ASSESSMENTS_REASON:
        return PersistedCandidateCollectionReason.EMPTY_ASSESSMENTS
    if reason == NO_COMPLETE_ASSESSMENT_REASON:
        return PersistedCandidateCollectionReason.NO_COMPLETE_ASSESSMENT
    return _UNMAPPABLE


def _persisted_fact_gate(item: _ParsedBinding) -> DecisionClaimRejectionCode | None:
    if item.persisted is None or item.result.tool_name != PERSISTED_TOOL:
        return DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH
    if item.result.status not in _PERSISTED_FACT_STATUSES:
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    if _mapped_reason(item.persisted.no_suitable_candidate_reason) is _UNMAPPABLE:
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    return None


def _verify_persisted_evaluation(
    claim: PersistedEvaluationClaim,
    item: _ParsedBinding,
) -> DecisionClaimRejectionCode | None:
    rejected = _persisted_fact_gate(item)
    if rejected is not None or item.persisted is None:
        return rejected or DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH
    persisted = item.persisted
    if (
        persisted.recommendation_id != claim.recommendation_id
        or persisted.airport_evaluation_id != claim.airport_evaluation_id
        or persisted.scope is not claim.evaluation_scope
    ):
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    return None


def _verify_persisted_candidate(
    claim: PersistedCandidateClaim,
    item: _ParsedBinding,
) -> DecisionClaimRejectionCode | None:
    rejected = _persisted_fact_gate(item)
    if rejected is not None or item.persisted is None:
        return rejected or DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH
    persisted = item.persisted
    if persisted.recommendation_id != claim.recommendation_id:
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    airports = [candidate.airport_id for candidate in persisted.candidates]
    assessments = [candidate.airport_assessment_id for candidate in persisted.candidates]
    if airports.count(claim.airport_id) > 1 or assessments.count(claim.airport_assessment_id) > 1:
        return DecisionClaimRejectionCode.AMBIGUOUS_CLAIM_TARGET
    matched = [
        candidate
        for candidate in persisted.candidates
        if candidate.airport_id == claim.airport_id
        and candidate.airport_assessment_id == claim.airport_assessment_id
    ]
    if len(matched) != 1:
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    candidate = matched[0]
    if (
        candidate.assessment_status != claim.assessment_status.value
        or candidate.route_safety_status != claim.route_safety_status.value
        or candidate.runway_evidence_status != claim.runway_evidence_status.value
        or candidate.congestion_evidence_status != claim.congestion_evidence_status.value
    ):
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    return None


def _verify_persisted_status(
    claim: PersistedCandidateStatusClaim,
    item: _ParsedBinding,
) -> DecisionClaimRejectionCode | None:
    rejected = _persisted_fact_gate(item)
    if rejected is not None or item.persisted is None:
        return rejected or DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH
    persisted = item.persisted
    mapped = _mapped_reason(persisted.no_suitable_candidate_reason)
    if (
        persisted.recommendation_id != claim.recommendation_id
        or len(persisted.candidates) != claim.candidate_count
        or mapped != claim.collection_reason
    ):
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    return None


def _verify_tool_status(
    claim: ToolStatusClaim,
    item: _ParsedBinding,
) -> DecisionClaimRejectionCode | None:
    if claim.status is not item.result.status:
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    return None


def _verify_limitation(
    claim: LimitationClaim,
    item: _ParsedBinding,
) -> DecisionClaimRejectionCode | None:
    rejected = _require_current(item, _CURRENT_TOOLS)
    if rejected is not None or item.evidence is None:
        return rejected or DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH
    if claim.limitation_code not in item.evidence.limitation_codes:
        return DecisionClaimRejectionCode.CLAIM_VALUE_MISMATCH
    return None


def _verify_link(
    claim: CurrentPersistedLinkClaim,
    parsed: dict[str, _ParsedBinding],
) -> DecisionClaimRejectionCode | None:
    current = parsed.get(claim.current_evidence_ref)
    persisted = parsed.get(claim.persisted_evidence_ref)
    if current is None or persisted is None:
        return DecisionClaimRejectionCode.UNKNOWN_EVIDENCE_REF
    if current.result.tool_name not in _RECOMMENDATION_CLAIM_TOOLS or current.evidence is None:
        return DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH
    if persisted.result.tool_name != PERSISTED_TOOL or persisted.persisted is None:
        return DecisionClaimRejectionCode.CLAIM_TOOL_MISMATCH
    if current.result.temporal_scope is not TemporalScope.CURRENT:
        return DecisionClaimRejectionCode.CLAIM_SCOPE_MISMATCH
    if persisted.result.temporal_scope is not TemporalScope.PERSISTED:
        return DecisionClaimRejectionCode.CLAIM_SCOPE_MISMATCH
    if not _presence_established(current.evidence, current.result):
        return DecisionClaimRejectionCode.CURRENT_PERSISTED_LINK_MISMATCH
    facts = _claimable_recommendation_facts(current)
    ids = () if facts is None else _complete_recommendation_ids(facts)
    payload = persisted.persisted
    if (
        facts is None
        or claim.recommendation_id not in ids
        or persisted.result.status not in _PERSISTED_FACT_STATUSES
        or payload.airport_evaluation_id is None
        or payload.recommendation_id != claim.recommendation_id
    ):
        return DecisionClaimRejectionCode.CURRENT_PERSISTED_LINK_MISMATCH
    return None


def _verified_scopes(
    claims: tuple[DecisionClaim, ...],
    parsed: dict[str, _ParsedBinding],
) -> tuple[TemporalScope, ...]:
    found = {parsed[ref].result.temporal_scope for ref in _used_refs(claims)}
    return tuple(scope for scope in _SCOPE_ORDER if scope in found)


def _current_as_of(
    scopes: tuple[TemporalScope, ...],
    parsed: dict[str, _ParsedBinding],
) -> str | None:
    if TemporalScope.CURRENT not in scopes:
        return None
    for item in parsed.values():
        if item.result.temporal_scope is TemporalScope.CURRENT:
            return item.result.as_of_utc
    return None


def _mandatory_limitations(
    claims: tuple[DecisionClaim, ...],
    parsed: dict[str, _ParsedBinding],
) -> tuple[str, ...]:
    collected: list[str] = []
    for ref in _used_refs(claims):
        result = parsed[ref].result
        for limitation in result.limitations:
            if limitation not in collected:
                collected.append(limitation)
        if result.temporal_scope is TemporalScope.PERSISTED and any(
            NOT_CURRENT_LIMITATION in evidence.limitations for evidence in result.evidence
        ):
            if NOT_CURRENT_LIMITATION not in collected:
                collected.append(NOT_CURRENT_LIMITATION)
    return tuple(collected)


__all__ = [
    "CLAIM_SET_NOT_ACTIONABLE",
    "CLAIM_VERIFICATION_FAILED",
    "CONTEXT_TOOL",
    "DECISION_VERIFICATION_SCHEMA_VERSION",
    "EMPTY_ASSESSMENTS_REASON",
    "NOT_CURRENT_LIMITATION",
    "NO_COMPLETE_ASSESSMENT_REASON",
    "PERSISTED_TOOL",
    "RECOMMENDATION_TOOL",
    "RISK_TOOL",
    "DecisionClaimRejectionCode",
    "DecisionEvidenceBinding",
    "DecisionVerificationResult",
    "verify_decision_evidence",
]
