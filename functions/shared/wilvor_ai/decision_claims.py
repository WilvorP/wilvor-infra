"""Closed Decision Expert claim contracts.

These types describe structured claims a later model may propose. They do
not verify claims, render answers, call a model, or read operational data.
A valid claim is not operational truth. Raw ToolResult objects remain the
future verifier authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from wilvor_ai.contracts import (
    ContractValidationError,
    JsonValue,
    TemporalScope,
    ToolResultStatus,
    _parse_enum,
    _required,
    _sequence,
)
from wilvor_ai.decision_contracts import (
    FORBIDDEN_DECISION_KEYS,
    DecisionAdvisoryAuthority,
    DecisionCapabilityGap,
    DecisionChainGap,
    DecisionEvaluationState,
    DecisionLimitationCode,
    DecisionReportedLinkState,
    PersistedEvaluationScope,
    RecommendationActionType,
    StoredRiskLevel,
    _operational_id,
)
from wilvor_ai.persisted_airport_contracts import (
    COMPLETE,
    UNAVAILABLE,
    WAITING_FOR_WEATHER,
)
from wilvor_ai.specialist_contracts import FORBIDDEN_CLAIM_PROSE_KEYS


DECISION_EVIDENCE_REF_PATTERN = re.compile(r"^de-[1-9][0-9]{0,8}$")
DECISION_EVIDENCE_REF_MAX_LENGTH = 12

_CURRENT_ONLY = frozenset({TemporalScope.CURRENT})
_PERSISTED_ONLY = frozenset({TemporalScope.PERSISTED})
_CURRENT_OR_PERSISTED = frozenset({TemporalScope.CURRENT, TemporalScope.PERSISTED})

_PROSE_KEYS = frozenset(
    key.casefold()
    for key in (*FORBIDDEN_CLAIM_PROSE_KEYS, "explanation")
)
_FORBIDDEN_KEYS = frozenset(
    key.casefold()
    for key in (
        *FORBIDDEN_DECISION_KEYS,
        "preferred_airport",
        "preferred_airport_id",
        "preferred_airport_assessment_id",
        "preferred_airport_score",
        "candidate_airport_summaries",
        "alternative_actions",
        "safe_diversion",
        "selected_airport",
        "airport_latitude",
        "airport_longitude",
        "search_origin_type",
        "rank",
        "score",
        "scores",
        "total_score",
        "total_airport_score",
        "distance_nm",
        "eta_minutes",
        "distance_score",
        "weather_score",
        "taf_score",
        "selected_encounter",
        "primary_encounter",
        "selected_encounter_id",
        "safest_airport",
        "best_airport",
        "winning_recommendation",
        "recommendation_winner",
        "atc_clearance",
        "dispatch_instruction",
        "landing_instruction",
        "tool_call_id",
        "correlation_id",
    )
)


class DecisionClaimKind(str, Enum):
    """Closed Decision claim kinds. Unsupported is a decision status, not a claim."""

    AIRCRAFT_IDENTITY = "AIRCRAFT_IDENTITY"
    EVALUATION_STATE = "EVALUATION_STATE"
    ENCOUNTER_SET = "ENCOUNTER_SET"
    RISK_ABSENT = "RISK_ABSENT"
    RISK_PRESENT = "RISK_PRESENT"
    RECOMMENDATION_SET = "RECOMMENDATION_SET"
    RECOMMENDATION_ACTION = "RECOMMENDATION_ACTION"
    CHAIN_GAP = "CHAIN_GAP"
    CAPABILITY_GAP = "CAPABILITY_GAP"
    PERSISTED_EVALUATION = "PERSISTED_EVALUATION"
    PERSISTED_CANDIDATE = "PERSISTED_CANDIDATE"
    PERSISTED_CANDIDATE_STATUS = "PERSISTED_CANDIDATE_STATUS"
    TOOL_STATUS = "TOOL_STATUS"
    LIMITATION = "LIMITATION"
    CURRENT_PERSISTED_LINK = "CURRENT_PERSISTED_LINK"


class PersistedAssessmentStatus(str, Enum):
    """Stored assessment completion. COMPLETE is not a safe diversion."""

    COMPLETE = COMPLETE
    WAITING_FOR_WEATHER = WAITING_FOR_WEATHER


class PersistedCapabilityEvidenceStatus(str, Enum):
    """V1 persisted candidate claims can report only unavailable capability evidence."""

    UNAVAILABLE = UNAVAILABLE


class PersistedCandidateCollectionReason(str, Enum):
    """Symbolic collection state. Not the stored prose sentence."""

    EMPTY_ASSESSMENTS = "EMPTY_ASSESSMENTS"
    NO_COMPLETE_ASSESSMENT = "NO_COMPLETE_ASSESSMENT"


def _reject_forbidden_tree(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.casefold() in _FORBIDDEN_KEYS:
                raise ContractValidationError("forbidden_decision_field")
            _reject_forbidden_tree(item)
        return
    if isinstance(value, list):
        for item in value:
            _reject_forbidden_tree(item)


def _reject_prose_tree(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.casefold() in _PROSE_KEYS:
                raise ContractValidationError("unexpected_claim_prose")
            _reject_prose_tree(item)
        return
    if isinstance(value, list):
        for item in value:
            _reject_prose_tree(item)


def _reject_nested_values(data: Mapping[str, Any]) -> None:
    for value in data.values():
        if isinstance(value, Mapping):
            raise ContractValidationError("unexpected_claim_field")
        if isinstance(value, list) and any(
            isinstance(item, (Mapping, list)) for item in value
        ):
            raise ContractValidationError("unexpected_claim_field")


def _load(
    data: Mapping[str, Any],
    allowed: frozenset[str],
    kind: DecisionClaimKind,
) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise ContractValidationError("invalid_decision_claim")
    _reject_forbidden_tree(data)
    _reject_prose_tree(data)
    extra = set(data) - allowed
    if extra:
        raise ContractValidationError("unexpected_claim_field")
    actual = _required(data, "kind")
    if actual != kind.value:
        raise ContractValidationError("invalid_kind")
    _reject_nested_values(data)
    return data


def _evidence_ref(value: Any, field_name: str) -> list[str]:
    if (
        not isinstance(value, str)
        or len(value) > DECISION_EVIDENCE_REF_MAX_LENGTH
        or DECISION_EVIDENCE_REF_PATTERN.fullmatch(value) is None
    ):
        return [f"invalid_{field_name}"]
    return []


def _require_evidence_ref(value: Any, field_name: str = "evidence_ref") -> str:
    errors = _evidence_ref(value, field_name)
    if errors:
        raise ContractValidationError(errors)
    return value


def _scope(
    value: Any,
    allowed: frozenset[TemporalScope],
) -> list[str]:
    if not isinstance(value, TemporalScope) or value not in allowed:
        return ["invalid_evidence_scope"]
    return []


def _parse_scope(
    data: Mapping[str, Any],
    allowed: frozenset[TemporalScope],
) -> TemporalScope:
    parsed = _parse_enum(
        TemporalScope,
        _required(data, "evidence_scope"),
        "evidence_scope",
    )
    if parsed not in allowed:
        raise ContractValidationError("invalid_evidence_scope")
    return parsed


def _checked_ids(values: tuple[Any, ...], field_name: str) -> None:
    if not isinstance(values, tuple):
        raise ContractValidationError(f"invalid_{field_name}")
    errors: list[str] = []
    for item in values:
        errors.extend(_operational_id(item, field_name))
    if errors:
        raise ContractValidationError(errors)
    if len(values) != len(set(values)):
        raise ContractValidationError(f"duplicate_{field_name}")
    if list(values) != sorted(values):
        raise ContractValidationError(f"unsorted_{field_name}")


def _id_tuple(data: Mapping[str, Any], field_name: str) -> tuple[str, ...]:
    return tuple(_sequence(_required(data, field_name), field_name))


def _optional_id(data: Mapping[str, Any], field_name: str) -> str | None:
    if field_name not in data:
        raise ContractValidationError(f"missing_{field_name}")
    value = data[field_name]
    if value is None:
        return None
    return value


@dataclass(frozen=True)
class AircraftIdentityClaim:
    evidence_ref: str
    evidence_scope: TemporalScope
    aircraft_id: str

    def __post_init__(self) -> None:
        errors = _evidence_ref(self.evidence_ref, "evidence_ref")
        errors.extend(_scope(self.evidence_scope, _CURRENT_ONLY))
        errors.extend(_operational_id(self.aircraft_id, "aircraft_id"))
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": DecisionClaimKind.AIRCRAFT_IDENTITY.value,
            "evidence_ref": self.evidence_ref,
            "evidence_scope": self.evidence_scope.value,
            "aircraft_id": self.aircraft_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AircraftIdentityClaim":
        payload = _load(
            data,
            frozenset({"kind", "evidence_ref", "evidence_scope", "aircraft_id"}),
            DecisionClaimKind.AIRCRAFT_IDENTITY,
        )
        return cls(
            evidence_ref=_required(payload, "evidence_ref"),
            evidence_scope=_parse_scope(payload, _CURRENT_ONLY),
            aircraft_id=_required(payload, "aircraft_id"),
        )


@dataclass(frozen=True)
class EvaluationStateClaim:
    evidence_ref: str
    evidence_scope: TemporalScope
    evaluation_state: DecisionEvaluationState

    def __post_init__(self) -> None:
        errors = _evidence_ref(self.evidence_ref, "evidence_ref")
        errors.extend(_scope(self.evidence_scope, _CURRENT_ONLY))
        if not isinstance(self.evaluation_state, DecisionEvaluationState):
            errors.append("invalid_evaluation_state")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": DecisionClaimKind.EVALUATION_STATE.value,
            "evidence_ref": self.evidence_ref,
            "evidence_scope": self.evidence_scope.value,
            "evaluation_state": self.evaluation_state.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvaluationStateClaim":
        payload = _load(
            data,
            frozenset(
                {"kind", "evidence_ref", "evidence_scope", "evaluation_state"}
            ),
            DecisionClaimKind.EVALUATION_STATE,
        )
        return cls(
            evidence_ref=_required(payload, "evidence_ref"),
            evidence_scope=_parse_scope(payload, _CURRENT_ONLY),
            evaluation_state=_parse_enum(
                DecisionEvaluationState,
                _required(payload, "evaluation_state"),
                "evaluation_state",
            ),
        )


@dataclass(frozen=True)
class EncounterSetClaim:
    """Complete encounter-id set. Order is canonical, not a selected encounter."""

    evidence_ref: str
    evidence_scope: TemporalScope
    encounter_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        errors = _evidence_ref(self.evidence_ref, "evidence_ref")
        errors.extend(_scope(self.evidence_scope, _CURRENT_ONLY))
        if errors:
            raise ContractValidationError(errors)
        _checked_ids(self.encounter_ids, "encounter_ids")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": DecisionClaimKind.ENCOUNTER_SET.value,
            "evidence_ref": self.evidence_ref,
            "evidence_scope": self.evidence_scope.value,
            "encounter_ids": list(self.encounter_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EncounterSetClaim":
        payload = _load(
            data,
            frozenset({"kind", "evidence_ref", "evidence_scope", "encounter_ids"}),
            DecisionClaimKind.ENCOUNTER_SET,
        )
        return cls(
            evidence_ref=_required(payload, "evidence_ref"),
            evidence_scope=_parse_scope(payload, _CURRENT_ONLY),
            encounter_ids=_id_tuple(payload, "encounter_ids"),
        )


@dataclass(frozen=True)
class RiskAbsentClaim:
    """Stored risk absence. This claim cannot carry a level, score, or risk id."""

    evidence_ref: str
    evidence_scope: TemporalScope
    encounter_id: str | None = None

    def __post_init__(self) -> None:
        errors = _evidence_ref(self.evidence_ref, "evidence_ref")
        errors.extend(_scope(self.evidence_scope, _CURRENT_ONLY))
        errors.extend(_operational_id(self.encounter_id, "encounter_id", optional=True))
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": DecisionClaimKind.RISK_ABSENT.value,
            "evidence_ref": self.evidence_ref,
            "evidence_scope": self.evidence_scope.value,
            "encounter_id": self.encounter_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RiskAbsentClaim":
        payload = _load(
            data,
            frozenset(
                {"kind", "evidence_ref", "evidence_scope", "encounter_id"}
            ),
            DecisionClaimKind.RISK_ABSENT,
        )
        return cls(
            evidence_ref=_required(payload, "evidence_ref"),
            evidence_scope=_parse_scope(payload, _CURRENT_ONLY),
            encounter_id=_optional_id(payload, "encounter_id"),
        )


@dataclass(frozen=True)
class RiskPresentClaim:
    evidence_ref: str
    evidence_scope: TemporalScope
    risk_id: str
    risk_level: StoredRiskLevel
    risk_score: int
    encounter_id: str | None = None

    def __post_init__(self) -> None:
        errors = _evidence_ref(self.evidence_ref, "evidence_ref")
        errors.extend(_scope(self.evidence_scope, _CURRENT_ONLY))
        errors.extend(_operational_id(self.risk_id, "risk_id"))
        errors.extend(_operational_id(self.encounter_id, "encounter_id", optional=True))
        if not isinstance(self.risk_level, StoredRiskLevel):
            errors.append("invalid_risk_level")
        if type(self.risk_score) is not int or self.risk_score < 0:
            errors.append("invalid_risk_score")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": DecisionClaimKind.RISK_PRESENT.value,
            "evidence_ref": self.evidence_ref,
            "evidence_scope": self.evidence_scope.value,
            "encounter_id": self.encounter_id,
            "risk_id": self.risk_id,
            "risk_level": self.risk_level.value,
            "risk_score": self.risk_score,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RiskPresentClaim":
        payload = _load(
            data,
            frozenset(
                {
                    "kind",
                    "evidence_ref",
                    "evidence_scope",
                    "encounter_id",
                    "risk_id",
                    "risk_level",
                    "risk_score",
                }
            ),
            DecisionClaimKind.RISK_PRESENT,
        )
        return cls(
            evidence_ref=_required(payload, "evidence_ref"),
            evidence_scope=_parse_scope(payload, _CURRENT_ONLY),
            encounter_id=_optional_id(payload, "encounter_id"),
            risk_id=_required(payload, "risk_id"),
            risk_level=_parse_enum(
                StoredRiskLevel,
                _required(payload, "risk_level"),
                "risk_level",
            ),
            risk_score=_required(payload, "risk_score"),
        )


@dataclass(frozen=True)
class RecommendationSetClaim:
    """Complete recommendation-id set. Order is canonical, not a winner."""

    evidence_ref: str
    evidence_scope: TemporalScope
    recommendation_ids: tuple[str, ...]
    absence_state: DecisionReportedLinkState

    def __post_init__(self) -> None:
        errors = _evidence_ref(self.evidence_ref, "evidence_ref")
        errors.extend(_scope(self.evidence_scope, _CURRENT_ONLY))
        if not isinstance(self.absence_state, DecisionReportedLinkState):
            errors.append("invalid_absence_state")
        if errors:
            raise ContractValidationError(errors)
        _checked_ids(self.recommendation_ids, "recommendation_ids")
        if self.recommendation_ids:
            if self.absence_state is not DecisionReportedLinkState.PRESENT:
                raise ContractValidationError(
                    "current_recommendations_require_present_link"
                )
        elif (
            self.absence_state
            is not DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES
        ):
            raise ContractValidationError("empty_recommendations_require_absence")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": DecisionClaimKind.RECOMMENDATION_SET.value,
            "evidence_ref": self.evidence_ref,
            "evidence_scope": self.evidence_scope.value,
            "recommendation_ids": list(self.recommendation_ids),
            "absence_state": self.absence_state.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RecommendationSetClaim":
        payload = _load(
            data,
            frozenset(
                {
                    "kind",
                    "evidence_ref",
                    "evidence_scope",
                    "recommendation_ids",
                    "absence_state",
                }
            ),
            DecisionClaimKind.RECOMMENDATION_SET,
        )
        return cls(
            evidence_ref=_required(payload, "evidence_ref"),
            evidence_scope=_parse_scope(payload, _CURRENT_ONLY),
            recommendation_ids=_id_tuple(payload, "recommendation_ids"),
            absence_state=_parse_enum(
                DecisionReportedLinkState,
                _required(payload, "absence_state"),
                "absence_state",
            ),
        )


@dataclass(frozen=True)
class RecommendationActionClaim:
    evidence_ref: str
    evidence_scope: TemporalScope
    recommendation_id: str
    primary_action_type: RecommendationActionType
    advisory_authority: DecisionAdvisoryAuthority

    def __post_init__(self) -> None:
        errors = _evidence_ref(self.evidence_ref, "evidence_ref")
        errors.extend(_scope(self.evidence_scope, _CURRENT_ONLY))
        errors.extend(_operational_id(self.recommendation_id, "recommendation_id"))
        if not isinstance(self.primary_action_type, RecommendationActionType):
            errors.append("invalid_primary_action_type")
        if self.advisory_authority is not DecisionAdvisoryAuthority.ADVISORY_ONLY:
            errors.append("invalid_advisory_authority")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": DecisionClaimKind.RECOMMENDATION_ACTION.value,
            "evidence_ref": self.evidence_ref,
            "evidence_scope": self.evidence_scope.value,
            "recommendation_id": self.recommendation_id,
            "primary_action_type": self.primary_action_type.value,
            "advisory_authority": self.advisory_authority.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RecommendationActionClaim":
        payload = _load(
            data,
            frozenset(
                {
                    "kind",
                    "evidence_ref",
                    "evidence_scope",
                    "recommendation_id",
                    "primary_action_type",
                    "advisory_authority",
                }
            ),
            DecisionClaimKind.RECOMMENDATION_ACTION,
        )
        return cls(
            evidence_ref=_required(payload, "evidence_ref"),
            evidence_scope=_parse_scope(payload, _CURRENT_ONLY),
            recommendation_id=_required(payload, "recommendation_id"),
            primary_action_type=_parse_enum(
                RecommendationActionType,
                _required(payload, "primary_action_type"),
                "primary_action_type",
            ),
            advisory_authority=_parse_enum(
                DecisionAdvisoryAuthority,
                _required(payload, "advisory_authority"),
                "advisory_authority",
            ),
        )


@dataclass(frozen=True)
class ChainGapClaim:
    evidence_ref: str
    evidence_scope: TemporalScope
    chain_gap: DecisionChainGap

    def __post_init__(self) -> None:
        errors = _evidence_ref(self.evidence_ref, "evidence_ref")
        errors.extend(_scope(self.evidence_scope, _CURRENT_ONLY))
        if not isinstance(self.chain_gap, DecisionChainGap):
            errors.append("invalid_chain_gap")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": DecisionClaimKind.CHAIN_GAP.value,
            "evidence_ref": self.evidence_ref,
            "evidence_scope": self.evidence_scope.value,
            "chain_gap": self.chain_gap.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ChainGapClaim":
        payload = _load(
            data,
            frozenset({"kind", "evidence_ref", "evidence_scope", "chain_gap"}),
            DecisionClaimKind.CHAIN_GAP,
        )
        return cls(
            evidence_ref=_required(payload, "evidence_ref"),
            evidence_scope=_parse_scope(payload, _CURRENT_ONLY),
            chain_gap=_parse_enum(
                DecisionChainGap,
                _required(payload, "chain_gap"),
                "chain_gap",
            ),
        )


@dataclass(frozen=True)
class CapabilityGapClaim:
    evidence_ref: str
    evidence_scope: TemporalScope
    capability_gap: DecisionCapabilityGap

    def __post_init__(self) -> None:
        errors = _evidence_ref(self.evidence_ref, "evidence_ref")
        errors.extend(_scope(self.evidence_scope, _CURRENT_ONLY))
        if not isinstance(self.capability_gap, DecisionCapabilityGap):
            errors.append("invalid_capability_gap")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": DecisionClaimKind.CAPABILITY_GAP.value,
            "evidence_ref": self.evidence_ref,
            "evidence_scope": self.evidence_scope.value,
            "capability_gap": self.capability_gap.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CapabilityGapClaim":
        payload = _load(
            data,
            frozenset({"kind", "evidence_ref", "evidence_scope", "capability_gap"}),
            DecisionClaimKind.CAPABILITY_GAP,
        )
        return cls(
            evidence_ref=_required(payload, "evidence_ref"),
            evidence_scope=_parse_scope(payload, _CURRENT_ONLY),
            capability_gap=_parse_enum(
                DecisionCapabilityGap,
                _required(payload, "capability_gap"),
                "capability_gap",
            ),
        )


@dataclass(frozen=True)
class PersistedEvaluationClaim:
    evidence_ref: str
    evidence_scope: TemporalScope
    recommendation_id: str
    airport_evaluation_id: str | None
    evaluation_scope: PersistedEvaluationScope

    def __post_init__(self) -> None:
        errors = _evidence_ref(self.evidence_ref, "evidence_ref")
        errors.extend(_scope(self.evidence_scope, _PERSISTED_ONLY))
        errors.extend(_operational_id(self.recommendation_id, "recommendation_id"))
        errors.extend(
            _operational_id(
                self.airport_evaluation_id,
                "airport_evaluation_id",
                optional=True,
            )
        )
        if (
            self.evaluation_scope
            is not PersistedEvaluationScope.PERSISTED_EVALUATION_EVIDENCE
        ):
            errors.append("invalid_evaluation_scope")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": DecisionClaimKind.PERSISTED_EVALUATION.value,
            "evidence_ref": self.evidence_ref,
            "evidence_scope": self.evidence_scope.value,
            "recommendation_id": self.recommendation_id,
            "airport_evaluation_id": self.airport_evaluation_id,
            "evaluation_scope": self.evaluation_scope.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PersistedEvaluationClaim":
        payload = _load(
            data,
            frozenset(
                {
                    "kind",
                    "evidence_ref",
                    "evidence_scope",
                    "recommendation_id",
                    "airport_evaluation_id",
                    "evaluation_scope",
                }
            ),
            DecisionClaimKind.PERSISTED_EVALUATION,
        )
        return cls(
            evidence_ref=_required(payload, "evidence_ref"),
            evidence_scope=_parse_scope(payload, _PERSISTED_ONLY),
            recommendation_id=_required(payload, "recommendation_id"),
            airport_evaluation_id=_optional_id(payload, "airport_evaluation_id"),
            evaluation_scope=_parse_enum(
                PersistedEvaluationScope,
                _required(payload, "evaluation_scope"),
                "evaluation_scope",
            ),
        )


@dataclass(frozen=True)
class PersistedCandidateClaim:
    evidence_ref: str
    evidence_scope: TemporalScope
    recommendation_id: str
    airport_id: str
    airport_assessment_id: str
    assessment_status: PersistedAssessmentStatus
    route_safety_status: PersistedCapabilityEvidenceStatus
    runway_evidence_status: PersistedCapabilityEvidenceStatus
    congestion_evidence_status: PersistedCapabilityEvidenceStatus

    def __post_init__(self) -> None:
        errors = _evidence_ref(self.evidence_ref, "evidence_ref")
        errors.extend(_scope(self.evidence_scope, _PERSISTED_ONLY))
        errors.extend(_operational_id(self.recommendation_id, "recommendation_id"))
        errors.extend(_operational_id(self.airport_id, "airport_id"))
        errors.extend(
            _operational_id(self.airport_assessment_id, "airport_assessment_id")
        )
        if not isinstance(self.assessment_status, PersistedAssessmentStatus):
            errors.append("invalid_assessment_status")
        for field_name in (
            "route_safety_status",
            "runway_evidence_status",
            "congestion_evidence_status",
        ):
            if (
                getattr(self, field_name)
                is not PersistedCapabilityEvidenceStatus.UNAVAILABLE
            ):
                errors.append(f"invalid_{field_name}")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": DecisionClaimKind.PERSISTED_CANDIDATE.value,
            "evidence_ref": self.evidence_ref,
            "evidence_scope": self.evidence_scope.value,
            "recommendation_id": self.recommendation_id,
            "airport_id": self.airport_id,
            "airport_assessment_id": self.airport_assessment_id,
            "assessment_status": self.assessment_status.value,
            "route_safety_status": self.route_safety_status.value,
            "runway_evidence_status": self.runway_evidence_status.value,
            "congestion_evidence_status": self.congestion_evidence_status.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PersistedCandidateClaim":
        payload = _load(
            data,
            frozenset(
                {
                    "kind",
                    "evidence_ref",
                    "evidence_scope",
                    "recommendation_id",
                    "airport_id",
                    "airport_assessment_id",
                    "assessment_status",
                    "route_safety_status",
                    "runway_evidence_status",
                    "congestion_evidence_status",
                }
            ),
            DecisionClaimKind.PERSISTED_CANDIDATE,
        )
        return cls(
            evidence_ref=_required(payload, "evidence_ref"),
            evidence_scope=_parse_scope(payload, _PERSISTED_ONLY),
            recommendation_id=_required(payload, "recommendation_id"),
            airport_id=_required(payload, "airport_id"),
            airport_assessment_id=_required(payload, "airport_assessment_id"),
            assessment_status=_parse_enum(
                PersistedAssessmentStatus,
                _required(payload, "assessment_status"),
                "assessment_status",
            ),
            route_safety_status=_parse_enum(
                PersistedCapabilityEvidenceStatus,
                _required(payload, "route_safety_status"),
                "route_safety_status",
            ),
            runway_evidence_status=_parse_enum(
                PersistedCapabilityEvidenceStatus,
                _required(payload, "runway_evidence_status"),
                "runway_evidence_status",
            ),
            congestion_evidence_status=_parse_enum(
                PersistedCapabilityEvidenceStatus,
                _required(payload, "congestion_evidence_status"),
                "congestion_evidence_status",
            ),
        )


@dataclass(frozen=True)
class PersistedCandidateStatusClaim:
    evidence_ref: str
    evidence_scope: TemporalScope
    recommendation_id: str
    candidate_count: int
    collection_reason: PersistedCandidateCollectionReason | None

    def __post_init__(self) -> None:
        errors = _evidence_ref(self.evidence_ref, "evidence_ref")
        errors.extend(_scope(self.evidence_scope, _PERSISTED_ONLY))
        errors.extend(_operational_id(self.recommendation_id, "recommendation_id"))
        if type(self.candidate_count) is not int or self.candidate_count < 0:
            errors.append("invalid_candidate_count")
        if (
            self.collection_reason is not None
            and not isinstance(
                self.collection_reason,
                PersistedCandidateCollectionReason,
            )
        ):
            errors.append("invalid_collection_reason")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": DecisionClaimKind.PERSISTED_CANDIDATE_STATUS.value,
            "evidence_ref": self.evidence_ref,
            "evidence_scope": self.evidence_scope.value,
            "recommendation_id": self.recommendation_id,
            "candidate_count": self.candidate_count,
            "collection_reason": (
                None
                if self.collection_reason is None
                else self.collection_reason.value
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PersistedCandidateStatusClaim":
        payload = _load(
            data,
            frozenset(
                {
                    "kind",
                    "evidence_ref",
                    "evidence_scope",
                    "recommendation_id",
                    "candidate_count",
                    "collection_reason",
                }
            ),
            DecisionClaimKind.PERSISTED_CANDIDATE_STATUS,
        )
        reason = _required(payload, "collection_reason")
        return cls(
            evidence_ref=_required(payload, "evidence_ref"),
            evidence_scope=_parse_scope(payload, _PERSISTED_ONLY),
            recommendation_id=_required(payload, "recommendation_id"),
            candidate_count=_required(payload, "candidate_count"),
            collection_reason=(
                None
                if reason is None
                else _parse_enum(
                    PersistedCandidateCollectionReason,
                    reason,
                    "collection_reason",
                )
            ),
        )


@dataclass(frozen=True)
class ToolStatusClaim:
    evidence_ref: str
    evidence_scope: TemporalScope
    status: ToolResultStatus

    def __post_init__(self) -> None:
        errors = _evidence_ref(self.evidence_ref, "evidence_ref")
        errors.extend(_scope(self.evidence_scope, _CURRENT_OR_PERSISTED))
        if not isinstance(self.status, ToolResultStatus):
            errors.append("invalid_status")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": DecisionClaimKind.TOOL_STATUS.value,
            "evidence_ref": self.evidence_ref,
            "evidence_scope": self.evidence_scope.value,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolStatusClaim":
        payload = _load(
            data,
            frozenset({"kind", "evidence_ref", "evidence_scope", "status"}),
            DecisionClaimKind.TOOL_STATUS,
        )
        return cls(
            evidence_ref=_required(payload, "evidence_ref"),
            evidence_scope=_parse_scope(payload, _CURRENT_OR_PERSISTED),
            status=_parse_enum(
                ToolResultStatus,
                _required(payload, "status"),
                "status",
            ),
        )


@dataclass(frozen=True)
class LimitationClaim:
    """Current DecisionLimitationCode only. Persisted prose stays off this claim."""

    evidence_ref: str
    evidence_scope: TemporalScope
    limitation_code: DecisionLimitationCode

    def __post_init__(self) -> None:
        errors = _evidence_ref(self.evidence_ref, "evidence_ref")
        errors.extend(_scope(self.evidence_scope, _CURRENT_ONLY))
        if not isinstance(self.limitation_code, DecisionLimitationCode):
            errors.append("invalid_limitation_code")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": DecisionClaimKind.LIMITATION.value,
            "evidence_ref": self.evidence_ref,
            "evidence_scope": self.evidence_scope.value,
            "limitation_code": self.limitation_code.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LimitationClaim":
        payload = _load(
            data,
            frozenset(
                {"kind", "evidence_ref", "evidence_scope", "limitation_code"}
            ),
            DecisionClaimKind.LIMITATION,
        )
        return cls(
            evidence_ref=_required(payload, "evidence_ref"),
            evidence_scope=_parse_scope(payload, _CURRENT_ONLY),
            limitation_code=_parse_enum(
                DecisionLimitationCode,
                _required(payload, "limitation_code"),
                "limitation_code",
            ),
        )


@dataclass(frozen=True)
class CurrentPersistedLinkClaim:
    """Link one current recommendation id to one persisted evaluation ref.

    This claim has no combined temporal scope. It does not make persisted
    airport evidence current.
    """

    current_evidence_ref: str
    persisted_evidence_ref: str
    recommendation_id: str

    def __post_init__(self) -> None:
        errors = _evidence_ref(self.current_evidence_ref, "current_evidence_ref")
        errors.extend(
            _evidence_ref(self.persisted_evidence_ref, "persisted_evidence_ref")
        )
        errors.extend(_operational_id(self.recommendation_id, "recommendation_id"))
        if (
            not errors
            and self.current_evidence_ref == self.persisted_evidence_ref
        ):
            errors.append("evidence_refs_must_differ")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": DecisionClaimKind.CURRENT_PERSISTED_LINK.value,
            "current_evidence_ref": self.current_evidence_ref,
            "persisted_evidence_ref": self.persisted_evidence_ref,
            "recommendation_id": self.recommendation_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CurrentPersistedLinkClaim":
        payload = _load(
            data,
            frozenset(
                {
                    "kind",
                    "current_evidence_ref",
                    "persisted_evidence_ref",
                    "recommendation_id",
                }
            ),
            DecisionClaimKind.CURRENT_PERSISTED_LINK,
        )
        return cls(
            current_evidence_ref=_required(payload, "current_evidence_ref"),
            persisted_evidence_ref=_required(payload, "persisted_evidence_ref"),
            recommendation_id=_required(payload, "recommendation_id"),
        )


DecisionClaim = (
    AircraftIdentityClaim
    | EvaluationStateClaim
    | EncounterSetClaim
    | RiskAbsentClaim
    | RiskPresentClaim
    | RecommendationSetClaim
    | RecommendationActionClaim
    | ChainGapClaim
    | CapabilityGapClaim
    | PersistedEvaluationClaim
    | PersistedCandidateClaim
    | PersistedCandidateStatusClaim
    | ToolStatusClaim
    | LimitationClaim
    | CurrentPersistedLinkClaim
)

_CLAIM_FACTORIES = {
    DecisionClaimKind.AIRCRAFT_IDENTITY: AircraftIdentityClaim.from_dict,
    DecisionClaimKind.EVALUATION_STATE: EvaluationStateClaim.from_dict,
    DecisionClaimKind.ENCOUNTER_SET: EncounterSetClaim.from_dict,
    DecisionClaimKind.RISK_ABSENT: RiskAbsentClaim.from_dict,
    DecisionClaimKind.RISK_PRESENT: RiskPresentClaim.from_dict,
    DecisionClaimKind.RECOMMENDATION_SET: RecommendationSetClaim.from_dict,
    DecisionClaimKind.RECOMMENDATION_ACTION: RecommendationActionClaim.from_dict,
    DecisionClaimKind.CHAIN_GAP: ChainGapClaim.from_dict,
    DecisionClaimKind.CAPABILITY_GAP: CapabilityGapClaim.from_dict,
    DecisionClaimKind.PERSISTED_EVALUATION: PersistedEvaluationClaim.from_dict,
    DecisionClaimKind.PERSISTED_CANDIDATE: PersistedCandidateClaim.from_dict,
    DecisionClaimKind.PERSISTED_CANDIDATE_STATUS: (
        PersistedCandidateStatusClaim.from_dict
    ),
    DecisionClaimKind.TOOL_STATUS: ToolStatusClaim.from_dict,
    DecisionClaimKind.LIMITATION: LimitationClaim.from_dict,
    DecisionClaimKind.CURRENT_PERSISTED_LINK: CurrentPersistedLinkClaim.from_dict,
}


def _is_decision_claim(value: object) -> bool:
    return isinstance(
        value,
        (
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
        ),
    )


def decision_claim_from_dict(data: Mapping[str, Any]) -> DecisionClaim:
    """Parse one closed Decision claim. Unknown kinds and extra fields fail."""

    if not isinstance(data, Mapping):
        raise ContractValidationError("invalid_decision_claim")
    _reject_forbidden_tree(data)
    _reject_prose_tree(data)
    kind = _parse_enum(DecisionClaimKind, _required(data, "kind"), "kind")
    return _CLAIM_FACTORIES[kind](data)


__all__ = [
    "DECISION_EVIDENCE_REF_MAX_LENGTH",
    "DECISION_EVIDENCE_REF_PATTERN",
    "AircraftIdentityClaim",
    "CapabilityGapClaim",
    "ChainGapClaim",
    "CurrentPersistedLinkClaim",
    "DecisionClaim",
    "DecisionClaimKind",
    "EncounterSetClaim",
    "EvaluationStateClaim",
    "LimitationClaim",
    "PersistedAssessmentStatus",
    "PersistedCandidateClaim",
    "PersistedCandidateCollectionReason",
    "PersistedCandidateStatusClaim",
    "PersistedCapabilityEvidenceStatus",
    "PersistedEvaluationClaim",
    "RecommendationActionClaim",
    "RecommendationSetClaim",
    "RiskAbsentClaim",
    "RiskPresentClaim",
    "ToolStatusClaim",
    "decision_claim_from_dict",
]
