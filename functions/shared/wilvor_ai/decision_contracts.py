"""Current decision-evidence contracts for future Decision Tools.

These types describe deterministic decision evidence that a later tool may
place in ``ToolResult.data``. They do not retrieve records, calculate risk,
choose a recommendation, generate a route, or import processor modules.

Currentness remains owned by ``wilvor_operational``. This module only reports
link results the caller already established. DT2 maps operational link states
onto these tokens. The token ``ABSENT_FROM_CURRENT_CANDIDATES`` and the
limitation codes ``NO_SNAPSHOT_LIMITATION`` and
``RECOMMENDATION_ABSENCE_LIMITATION`` use the existing operational names.
This module does not import ``wilvor_operational`` and does not redefine
what "current" means. Route capability is optional on ``DecisionEvidence``.
When present, it still cannot represent a validated route.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from wilvor_ai.contracts import (
    ConfidenceLevel,
    ContractValidationError,
    JsonValue,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
    _parse_enum,
    _required,
    _sequence,
    _validate_bounded_text,
    _validate_string_tuple,
    _validate_utc,
)


DECISION_EVIDENCE_SCHEMA_VERSION = "wilvor.ai.decision_evidence.v1"
OPERATIONAL_ID_MAX_LENGTH = 512
ADVISORY_NOTICE_MAX_LENGTH = 512

# Closed vocabulary. These names are not a second currentness algorithm.
ABSENT_FROM_CURRENT_CANDIDATES = "ABSENT_FROM_CURRENT_CANDIDATES"
NO_SNAPSHOT_LIMITATION = "NO_SNAPSHOT_LIMITATION"
RECOMMENDATION_ABSENCE_LIMITATION = "RECOMMENDATION_ABSENCE_LIMITATION"

FORBIDDEN_DECISION_KEYS = frozenset(
    {
        "route",
        "routes",
        "waypoint",
        "waypoints",
        "trajectory",
        "trajectories",
        "alternative_route",
        "validated_route",
        "flight_plan",
        "flight_plan_route",
        "best_recommendation",
        "bestRecommendation",
        "selected_recommendation",
        "selectedRecommendation",
        "primary_recommendation",
        "primaryRecommendation",
        "selected_diversion",
        "diversion_clearance",
        "diversion_candidates",
        "decision_confidence",
        "model_confidence",
        "llm_confidence",
        "repaired_hazard_source_version",
        "resolved_hazard_source_version",
        "stale",
        "is_stale",
    }
)

BLOCKING_CHAIN_GAPS = frozenset(
    {
        "NO_CURRENT_PROJECTION",
        "NO_CURRENT_ENCOUNTER",
        "RISK_ABSENT",
        "HAZARD_SOURCE_VERSION_MISMATCH",
        "INCOMPLETE_CURRENT_CHAIN",
    }
)


class DecisionEvidenceKind(str, Enum):
    """Which future read-only tool payload this evidence is."""

    DECISION_CONTEXT = "DECISION_CONTEXT"
    RECOMMENDATION_EVIDENCE = "RECOMMENDATION_EVIDENCE"
    RISK_EVIDENCE = "RISK_EVIDENCE"


class DecisionEvaluationState(str, Enum):
    """Whether the caller could evaluate the requested current evidence.

    ESTABLISHED: deterministic current evidence was evaluated.
    NOT_ESTABLISHED: evaluation could not establish the evidence.
    SOURCE_UNAVAILABLE: the read path could not be evaluated.
    """

    ESTABLISHED = "ESTABLISHED"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


class DecisionReportedLinkState(str, Enum):
    """Reported operational link outcome. Not a currentness calculation."""

    PRESENT = "PRESENT"
    ABSENT_FROM_CURRENT_CANDIDATES = ABSENT_FROM_CURRENT_CANDIDATES
    NOT_CURRENT = "NOT_CURRENT"
    MISSING = "MISSING"
    HYDRATION_VERSION_MISMATCH = "HYDRATION_VERSION_MISMATCH"


class DecisionChainGap(str, Enum):
    """Reported break or exclusion in the current chain. Not a new rule."""

    AIRCRAFT_NOT_IN_CURRENT_SET = "AIRCRAFT_NOT_IN_CURRENT_SET"
    NO_CURRENT_PROJECTION = "NO_CURRENT_PROJECTION"
    NO_CURRENT_ENCOUNTER = "NO_CURRENT_ENCOUNTER"
    RISK_ABSENT = "RISK_ABSENT"
    RECOMMENDATION_ABSENT = "RECOMMENDATION_ABSENT"
    STALE_RECOMMENDATION_EXCLUDED = "STALE_RECOMMENDATION_EXCLUDED"
    HAZARD_SOURCE_VERSION_MISMATCH = "HAZARD_SOURCE_VERSION_MISMATCH"
    INCOMPLETE_CURRENT_CHAIN = "INCOMPLETE_CURRENT_CHAIN"


class DecisionLimitationCode(str, Enum):
    """Canonical limitation tokens. Prose stays with the operational owner."""

    NO_SNAPSHOT_LIMITATION = NO_SNAPSHOT_LIMITATION
    RECOMMENDATION_ABSENCE_LIMITATION = RECOMMENDATION_ABSENCE_LIMITATION


class DecisionCapabilityGap(str, Enum):
    """Approved unavailable decision capabilities. Not a route result."""

    ROUTE_ALTERNATIVE_NOT_IMPLEMENTED = "ROUTE_ALTERNATIVE_NOT_IMPLEMENTED"
    ROUTE_SAFETY_EVIDENCE_UNAVAILABLE = "ROUTE_SAFETY_EVIDENCE_UNAVAILABLE"
    RUNWAY_EVIDENCE_UNAVAILABLE = "RUNWAY_EVIDENCE_UNAVAILABLE"
    CONGESTION_EVIDENCE_UNAVAILABLE = "CONGESTION_EVIDENCE_UNAVAILABLE"


V1_UNAVAILABLE_DECISION_CAPABILITIES = (
    DecisionCapabilityGap.ROUTE_ALTERNATIVE_NOT_IMPLEMENTED,
    DecisionCapabilityGap.ROUTE_SAFETY_EVIDENCE_UNAVAILABLE,
    DecisionCapabilityGap.RUNWAY_EVIDENCE_UNAVAILABLE,
    DecisionCapabilityGap.CONGESTION_EVIDENCE_UNAVAILABLE,
)


class RiskPresence(str, Enum):
    """PRESENT is a stored risk row. ABSENT is not a level or a score."""

    PRESENT = "PRESENT"
    ABSENT = "ABSENT"


class StoredRiskLevel(str, Enum):
    """Levels the risk processor actually persists. UNKNOWN is not one."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class PersistedConfidenceSource(str, Enum):
    """Which stored field a confidence value was copied from."""

    RISK_RESULT = "RISK_RESULT"
    RECOMMENDATION = "RECOMMENDATION"


class RecommendationActionType(str, Enum):
    """Persisted advisory action. Not a clearance or a route."""

    MONITOR = "MONITOR"
    MONITOR_AND_PREPARE_OPTIONS = "MONITOR_AND_PREPARE_OPTIONS"
    EVALUATE_DIVERSION = "EVALUATE_DIVERSION"


class DecisionAdvisoryAuthority(str, Enum):
    """A recommendation explains stored advisory evidence only."""

    ADVISORY_ONLY = "ADVISORY_ONLY"


class PersistedEvidenceReferenceType(str, Enum):
    """Allowlisted reference types already written by the recommendation processor."""

    RISK_RESULT = "RISK_RESULT"
    AIRPORT_ASSESSMENT_EVALUATION = "AIRPORT_ASSESSMENT_EVALUATION"
    AIRPORT_ASSESSMENT = "AIRPORT_ASSESSMENT"


class PersistedEvaluationScope(str, Enum):
    """Evidence stored for one evaluation id. Not a current airport decision."""

    PERSISTED_EVALUATION_EVIDENCE = "PERSISTED_EVALUATION_EVIDENCE"


class AlertCurrentLineage(str, Enum):
    """Which already-current side qualified the alert. OR-lineage is not an error."""

    RISK = "RISK"
    RECOMMENDATION = "RECOMMENDATION"
    BOTH = "BOTH"


def _reject_forbidden_tree(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in FORBIDDEN_DECISION_KEYS:
                raise ContractValidationError("forbidden_decision_field")
            _reject_forbidden_tree(item)
        return

    if isinstance(value, list):
        for item in value:
            _reject_forbidden_tree(item)


def _reject_unknown(
    data: Mapping[str, Any],
    allowed: frozenset[str],
    label: str,
) -> None:
    extra = set(data) - allowed
    if extra & FORBIDDEN_DECISION_KEYS:
        raise ContractValidationError("forbidden_decision_field")
    if extra:
        raise ContractValidationError(f"unexpected_{label}_field")


def _operational_id(
    value: Any,
    field_name: str,
    *,
    optional: bool = False,
) -> list[str]:
    if value is None and optional:
        return []
    errors = _validate_bounded_text(
        value,
        field_name,
        optional=optional,
        max_length=OPERATIONAL_ID_MAX_LENGTH,
    )
    if errors or value is None:
        return errors
    if any(character.isspace() for character in value):
        return [f"invalid_{field_name}"]
    return []


def _optional_id(data: Mapping[str, Any], field_name: str) -> str | None:
    if field_name not in data or data[field_name] is None:
        return None
    return data[field_name]


def _enum_tuple(values: tuple[Any, ...], enum_type: type[Enum], field_name: str) -> None:
    if not isinstance(values, tuple) or any(
        not isinstance(item, enum_type) for item in values
    ):
        raise ContractValidationError(f"invalid_{field_name}")
    if len(values) != len(set(values)):
        raise ContractValidationError(f"duplicate_{field_name}")


def _id_tuple(values: tuple[Any, ...], field_name: str) -> None:
    if not isinstance(values, tuple):
        raise ContractValidationError(f"invalid_{field_name}")
    errors: list[str] = []
    for item in values:
        errors.extend(_operational_id(item, field_name))
    if errors:
        raise ContractValidationError(errors)
    if len(values) != len(set(values)):
        raise ContractValidationError(f"duplicate_{field_name}")


@dataclass(frozen=True)
class DecisionRouteCapability:
    """V1 has no validated route or trajectory alternative."""

    validated_alternative_available: bool
    unavailable: tuple[DecisionCapabilityGap, ...] = V1_UNAVAILABLE_DECISION_CAPABILITIES

    def __post_init__(self) -> None:
        if self.validated_alternative_available is not False:
            raise ContractValidationError("validated_alternative_forbidden")
        if self.unavailable != V1_UNAVAILABLE_DECISION_CAPABILITIES:
            raise ContractValidationError("invalid_unavailable_capabilities")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "validated_alternative_available": False,
            "unavailable": [item.value for item in self.unavailable],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionRouteCapability":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_capability")
        _reject_forbidden_tree(data)
        _reject_unknown(
            data,
            frozenset({"validated_alternative_available", "unavailable"}),
            "capability",
        )
        unavailable = tuple(
            _parse_enum(DecisionCapabilityGap, item, "unavailable_capabilities")
            for item in _sequence(_required(data, "unavailable"), "unavailable")
        )
        return cls(
            validated_alternative_available=_required(
                data,
                "validated_alternative_available",
            ),
            unavailable=unavailable,
        )


@dataclass(frozen=True)
class PersistedConfidence:
    """Stored pipeline confidence. Not a model-authored decision confidence."""

    value: ConfidenceLevel
    source: PersistedConfidenceSource

    def __post_init__(self) -> None:
        if not isinstance(self.value, ConfidenceLevel):
            raise ContractValidationError("invalid_confidence")
        if not isinstance(self.source, PersistedConfidenceSource):
            raise ContractValidationError("invalid_confidence_source")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"value": self.value.value, "source": self.source.value}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PersistedConfidence":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_confidence")
        _reject_forbidden_tree(data)
        _reject_unknown(data, frozenset({"value", "source"}), "confidence")
        return cls(
            value=_parse_enum(
                ConfidenceLevel,
                _required(data, "value"),
                "confidence",
            ),
            source=_parse_enum(
                PersistedConfidenceSource,
                _required(data, "source"),
                "confidence_source",
            ),
        )


@dataclass(frozen=True)
class PersistedSourceVersions:
    """Allowlisted version fields. Not an arbitrary stored map."""

    hazard_source_version: str | None = None
    risk_schema_version: str | None = None
    airport_evaluation_id: str | None = None
    scoring_ruleset_version: str | None = None
    recommendation_ruleset_version: str | None = None

    def __post_init__(self) -> None:
        errors: list[str] = []
        for field_name in (
            "hazard_source_version",
            "risk_schema_version",
            "airport_evaluation_id",
            "scoring_ruleset_version",
            "recommendation_ruleset_version",
        ):
            errors.extend(
                _operational_id(
                    getattr(self, field_name),
                    field_name,
                    optional=True,
                )
            )
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "hazard_source_version": self.hazard_source_version,
            "risk_schema_version": self.risk_schema_version,
            "airport_evaluation_id": self.airport_evaluation_id,
            "scoring_ruleset_version": self.scoring_ruleset_version,
            "recommendation_ruleset_version": self.recommendation_ruleset_version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PersistedSourceVersions":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_source_versions")
        _reject_forbidden_tree(data)
        _reject_unknown(
            data,
            frozenset(
                {
                    "hazard_source_version",
                    "risk_schema_version",
                    "airport_evaluation_id",
                    "scoring_ruleset_version",
                    "recommendation_ruleset_version",
                }
            ),
            "source_versions",
        )
        return cls(
            hazard_source_version=_optional_id(data, "hazard_source_version"),
            risk_schema_version=_optional_id(data, "risk_schema_version"),
            airport_evaluation_id=_optional_id(data, "airport_evaluation_id"),
            scoring_ruleset_version=_optional_id(data, "scoring_ruleset_version"),
            recommendation_ruleset_version=_optional_id(
                data,
                "recommendation_ruleset_version",
            ),
        )


@dataclass(frozen=True)
class PersistedEvidenceReference:
    reference_type: PersistedEvidenceReferenceType
    record_id: str
    airport_id: str | None = None

    def __post_init__(self) -> None:
        errors = _operational_id(self.record_id, "record_id")
        errors.extend(_operational_id(self.airport_id, "airport_id", optional=True))
        if not isinstance(self.reference_type, PersistedEvidenceReferenceType):
            errors.append("invalid_reference_type")
        elif (
            self.airport_id is not None
            and self.reference_type is not PersistedEvidenceReferenceType.AIRPORT_ASSESSMENT
        ):
            errors.append("airport_id_requires_airport_assessment_reference")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "reference_type": self.reference_type.value,
            "record_id": self.record_id,
            "airport_id": self.airport_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PersistedEvidenceReference":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_evidence_reference")
        _reject_forbidden_tree(data)
        _reject_unknown(
            data,
            frozenset({"reference_type", "record_id", "airport_id"}),
            "evidence_reference",
        )
        return cls(
            reference_type=_parse_enum(
                PersistedEvidenceReferenceType,
                _required(data, "reference_type"),
                "reference_type",
            ),
            record_id=_required(data, "record_id"),
            airport_id=_optional_id(data, "airport_id"),
        )


@dataclass(frozen=True)
class PersistedAirportEvaluationEvidence:
    """Persisted rows for one recommendation evaluation.

    This is not a globally current airport assessment, a selected diversion,
    or a validated diversion route.
    """

    airport_evaluation_id: str
    airport_assessment_ids: tuple[str, ...] = ()
    scope: PersistedEvaluationScope = (
        PersistedEvaluationScope.PERSISTED_EVALUATION_EVIDENCE
    )

    def __post_init__(self) -> None:
        errors = _operational_id(self.airport_evaluation_id, "airport_evaluation_id")
        if errors:
            raise ContractValidationError(errors)
        if self.scope is not PersistedEvaluationScope.PERSISTED_EVALUATION_EVIDENCE:
            raise ContractValidationError("invalid_evaluation_scope")
        _id_tuple(self.airport_assessment_ids, "airport_assessment_ids")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "scope": self.scope.value,
            "airport_evaluation_id": self.airport_evaluation_id,
            "airport_assessment_ids": list(self.airport_assessment_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PersistedAirportEvaluationEvidence":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_airport_evaluation")
        _reject_forbidden_tree(data)
        _reject_unknown(
            data,
            frozenset(
                {
                    "scope",
                    "airport_evaluation_id",
                    "airport_assessment_ids",
                }
            ),
            "airport_evaluation",
        )
        return cls(
            airport_evaluation_id=_required(data, "airport_evaluation_id"),
            airport_assessment_ids=tuple(
                _sequence(
                    data["airport_assessment_ids"]
                    if "airport_assessment_ids" in data
                    else [],
                    "airport_assessment_ids",
                )
            ),
            scope=_parse_enum(
                PersistedEvaluationScope,
                data["scope"]
                if "scope" in data
                else PersistedEvaluationScope.PERSISTED_EVALUATION_EVIDENCE.value,
                "evaluation_scope",
            ),
        )


@dataclass(frozen=True)
class DecisionRiskEvidence:
    presence: RiskPresence
    risk_id: str | None = None
    encounter_id: str | None = None
    scoring_ruleset_version: str | None = None
    generated_at_utc: str | None = None
    valid_until_utc: str | None = None
    risk_level: StoredRiskLevel | None = None
    risk_score: int | None = None
    confidence: PersistedConfidence | None = None
    reasons: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.presence, RiskPresence):
            raise ContractValidationError("invalid_risk_presence")
        errors = _operational_id(self.risk_id, "risk_id", optional=True)
        errors.extend(_operational_id(self.encounter_id, "encounter_id", optional=True))
        errors.extend(
            _operational_id(
                self.scoring_ruleset_version,
                "scoring_ruleset_version",
                optional=True,
            )
        )
        errors.extend(
            _validate_utc(self.generated_at_utc, "generated_at_utc", optional=True)
        )
        errors.extend(
            _validate_utc(self.valid_until_utc, "valid_until_utc", optional=True)
        )
        errors.extend(_validate_string_tuple(self.reasons, "reasons"))
        errors.extend(_validate_string_tuple(self.limitations, "limitations"))
        if self.confidence is not None:
            if not isinstance(self.confidence, PersistedConfidence):
                errors.append("invalid_confidence")
            elif self.confidence.source is not PersistedConfidenceSource.RISK_RESULT:
                errors.append("risk_confidence_source_mismatch")
        if self.presence is RiskPresence.ABSENT:
            if any(
                value is not None
                for value in (
                    self.risk_id,
                    self.risk_level,
                    self.risk_score,
                    self.scoring_ruleset_version,
                    self.generated_at_utc,
                    self.valid_until_utc,
                    self.confidence,
                )
            ) or self.reasons:
                errors.append("absent_risk_forbids_stored_value")
        else:
            if self.risk_level is None or not isinstance(self.risk_level, StoredRiskLevel):
                errors.append("present_risk_requires_level")
            errors.extend(_operational_id(self.risk_id, "risk_id"))
            if type(self.risk_score) is not int or self.risk_score < 0:
                errors.append("present_risk_requires_score")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "presence": self.presence.value,
            "risk_id": self.risk_id,
            "encounter_id": self.encounter_id,
            "scoring_ruleset_version": self.scoring_ruleset_version,
            "generated_at_utc": self.generated_at_utc,
            "valid_until_utc": self.valid_until_utc,
            "risk_level": None if self.risk_level is None else self.risk_level.value,
            "risk_score": self.risk_score,
            "confidence": None if self.confidence is None else self.confidence.to_dict(),
            "reasons": list(self.reasons),
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionRiskEvidence":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_risk")
        _reject_forbidden_tree(data)
        _reject_unknown(
            data,
            frozenset(
                {
                    "presence",
                    "risk_id",
                    "encounter_id",
                    "scoring_ruleset_version",
                    "generated_at_utc",
                    "valid_until_utc",
                    "risk_level",
                    "risk_score",
                    "confidence",
                    "reasons",
                    "limitations",
                }
            ),
            "risk",
        )
        level = _optional_id(data, "risk_level")
        confidence = data["confidence"] if "confidence" in data else None
        return cls(
            presence=_parse_enum(
                RiskPresence,
                _required(data, "presence"),
                "risk_presence",
            ),
            risk_id=_optional_id(data, "risk_id"),
            encounter_id=_optional_id(data, "encounter_id"),
            scoring_ruleset_version=_optional_id(data, "scoring_ruleset_version"),
            generated_at_utc=_optional_id(data, "generated_at_utc"),
            valid_until_utc=_optional_id(data, "valid_until_utc"),
            risk_level=(
                None
                if level is None
                else _parse_enum(StoredRiskLevel, level, "risk_level")
            ),
            risk_score=data["risk_score"] if "risk_score" in data else None,
            confidence=(
                None if confidence is None else PersistedConfidence.from_dict(confidence)
            ),
            reasons=tuple(_sequence(data["reasons"] if "reasons" in data else [], "reasons")),
            limitations=tuple(
                _sequence(
                    data["limitations"] if "limitations" in data else [],
                    "limitations",
                )
            ),
        )


@dataclass(frozen=True)
class DecisionRecommendationEvidence:
    recommendation_id: str
    primary_action_type: RecommendationActionType
    advisory_authority: DecisionAdvisoryAuthority
    recommendation_version: str | None = None
    ruleset_version: str | None = None
    valid_from_utc: str | None = None
    valid_until_utc: str | None = None
    advisory_notice: str | None = None
    confidence: PersistedConfidence | None = None
    evidence_references: tuple[PersistedEvidenceReference, ...] = ()
    source_versions: PersistedSourceVersions | None = None
    airport_evaluation: PersistedAirportEvaluationEvidence | None = None
    reasons: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        errors = _operational_id(self.recommendation_id, "recommendation_id")
        errors.extend(
            _operational_id(
                self.recommendation_version,
                "recommendation_version",
                optional=True,
            )
        )
        errors.extend(
            _operational_id(self.ruleset_version, "ruleset_version", optional=True)
        )
        errors.extend(_validate_utc(self.valid_from_utc, "valid_from_utc", optional=True))
        errors.extend(_validate_utc(self.valid_until_utc, "valid_until_utc", optional=True))
        errors.extend(
            _validate_bounded_text(
                self.advisory_notice,
                "advisory_notice",
                optional=True,
                max_length=ADVISORY_NOTICE_MAX_LENGTH,
            )
        )
        errors.extend(_validate_string_tuple(self.reasons, "reasons"))
        errors.extend(_validate_string_tuple(self.limitations, "limitations"))
        if not isinstance(self.primary_action_type, RecommendationActionType):
            errors.append("invalid_primary_action_type")
        if self.advisory_authority is not DecisionAdvisoryAuthority.ADVISORY_ONLY:
            errors.append("invalid_advisory_authority")
        if self.confidence is not None:
            if not isinstance(self.confidence, PersistedConfidence):
                errors.append("invalid_confidence")
            elif self.confidence.source is not PersistedConfidenceSource.RECOMMENDATION:
                errors.append("recommendation_confidence_source_mismatch")
        if not isinstance(self.evidence_references, tuple) or any(
            not isinstance(item, PersistedEvidenceReference)
            for item in self.evidence_references
        ):
            errors.append("invalid_evidence_references")
        if self.source_versions is not None and not isinstance(
            self.source_versions,
            PersistedSourceVersions,
        ):
            errors.append("invalid_source_versions")
        if self.airport_evaluation is not None and not isinstance(
            self.airport_evaluation,
            PersistedAirportEvaluationEvidence,
        ):
            errors.append("invalid_airport_evaluation")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "recommendation_id": self.recommendation_id,
            "recommendation_version": self.recommendation_version,
            "ruleset_version": self.ruleset_version,
            "valid_from_utc": self.valid_from_utc,
            "valid_until_utc": self.valid_until_utc,
            "primary_action_type": self.primary_action_type.value,
            "advisory_authority": self.advisory_authority.value,
            "advisory_notice": self.advisory_notice,
            "confidence": None if self.confidence is None else self.confidence.to_dict(),
            "evidence_references": [item.to_dict() for item in self.evidence_references],
            "source_versions": (
                None if self.source_versions is None else self.source_versions.to_dict()
            ),
            "airport_evaluation": (
                None
                if self.airport_evaluation is None
                else self.airport_evaluation.to_dict()
            ),
            "reasons": list(self.reasons),
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionRecommendationEvidence":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_recommendation")
        _reject_forbidden_tree(data)
        _reject_unknown(
            data,
            frozenset(
                {
                    "recommendation_id",
                    "recommendation_version",
                    "ruleset_version",
                    "valid_from_utc",
                    "valid_until_utc",
                    "primary_action_type",
                    "advisory_authority",
                    "advisory_notice",
                    "confidence",
                    "evidence_references",
                    "source_versions",
                    "airport_evaluation",
                    "reasons",
                    "limitations",
                }
            ),
            "recommendation",
        )
        confidence = data["confidence"] if "confidence" in data else None
        source_versions = data["source_versions"] if "source_versions" in data else None
        airport_evaluation = (
            data["airport_evaluation"] if "airport_evaluation" in data else None
        )
        return cls(
            recommendation_id=_required(data, "recommendation_id"),
            primary_action_type=_parse_enum(
                RecommendationActionType,
                _required(data, "primary_action_type"),
                "primary_action_type",
            ),
            advisory_authority=_parse_enum(
                DecisionAdvisoryAuthority,
                _required(data, "advisory_authority"),
                "advisory_authority",
            ),
            recommendation_version=_optional_id(data, "recommendation_version"),
            ruleset_version=_optional_id(data, "ruleset_version"),
            valid_from_utc=_optional_id(data, "valid_from_utc"),
            valid_until_utc=_optional_id(data, "valid_until_utc"),
            advisory_notice=_optional_id(data, "advisory_notice"),
            confidence=(
                None if confidence is None else PersistedConfidence.from_dict(confidence)
            ),
            evidence_references=tuple(
                PersistedEvidenceReference.from_dict(item)
                for item in _sequence(
                    data["evidence_references"] if "evidence_references" in data else [],
                    "evidence_references",
                )
            ),
            source_versions=(
                None
                if source_versions is None
                else PersistedSourceVersions.from_dict(source_versions)
            ),
            airport_evaluation=(
                None
                if airport_evaluation is None
                else PersistedAirportEvaluationEvidence.from_dict(airport_evaluation)
            ),
            reasons=tuple(_sequence(data["reasons"] if "reasons" in data else [], "reasons")),
            limitations=tuple(
                _sequence(
                    data["limitations"] if "limitations" in data else [],
                    "limitations",
                )
            ),
        )


@dataclass(frozen=True)
class DecisionRecommendationSet:
    """Zero, one, or many current recommendations. Order is not a winner."""

    current: tuple[DecisionRecommendationEvidence, ...]
    absence_state: DecisionReportedLinkState
    excluded_stale_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.current, tuple) or any(
            not isinstance(item, DecisionRecommendationEvidence) for item in self.current
        ):
            raise ContractValidationError("invalid_current_recommendations")
        if not isinstance(self.absence_state, DecisionReportedLinkState):
            raise ContractValidationError("invalid_absence_state")
        _id_tuple(self.excluded_stale_ids, "excluded_stale_ids")
        current_ids = [item.recommendation_id for item in self.current]
        if len(current_ids) != len(set(current_ids)):
            raise ContractValidationError("duplicate_recommendation_id")
        if set(current_ids) & set(self.excluded_stale_ids):
            raise ContractValidationError("stale_recommendation_not_current")
        if self.current:
            if self.absence_state is not DecisionReportedLinkState.PRESENT:
                raise ContractValidationError("current_recommendations_require_present_link")
        elif self.absence_state is not DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES:
            raise ContractValidationError("empty_recommendations_require_absence")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "current": [item.to_dict() for item in self.current],
            "absence_state": self.absence_state.value,
            "excluded_stale_ids": list(self.excluded_stale_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionRecommendationSet":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_recommendation_set")
        _reject_forbidden_tree(data)
        _reject_unknown(
            data,
            frozenset({"current", "absence_state", "excluded_stale_ids"}),
            "recommendation_set",
        )
        return cls(
            current=tuple(
                DecisionRecommendationEvidence.from_dict(item)
                for item in _sequence(_required(data, "current"), "current")
            ),
            absence_state=_parse_enum(
                DecisionReportedLinkState,
                _required(data, "absence_state"),
                "absence_state",
            ),
            excluded_stale_ids=tuple(
                _sequence(
                    data["excluded_stale_ids"] if "excluded_stale_ids" in data else [],
                    "excluded_stale_ids",
                )
            ),
        )


@dataclass(frozen=True)
class HazardSourceVersionLink:
    hazard_id: str
    state: DecisionReportedLinkState
    persisted_source_version: str | None = None
    current_source_version: str | None = None

    def __post_init__(self) -> None:
        errors = _operational_id(self.hazard_id, "hazard_id")
        errors.extend(
            _operational_id(
                self.persisted_source_version,
                "persisted_source_version",
                optional=True,
            )
        )
        errors.extend(
            _operational_id(
                self.current_source_version,
                "current_source_version",
                optional=True,
            )
        )
        if not isinstance(self.state, DecisionReportedLinkState):
            errors.append("invalid_hazard_link_state")
        elif self.state is DecisionReportedLinkState.HYDRATION_VERSION_MISMATCH:
            if (
                not self.persisted_source_version
                or not self.current_source_version
                or self.persisted_source_version == self.current_source_version
            ):
                errors.append("hazard_version_mismatch_not_repairable")
        elif self.state is DecisionReportedLinkState.PRESENT:
            if (
                not self.persisted_source_version
                or self.persisted_source_version != self.current_source_version
            ):
                errors.append("hazard_version_mismatch_not_repairable")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "hazard_id": self.hazard_id,
            "state": self.state.value,
            "persisted_source_version": self.persisted_source_version,
            "current_source_version": self.current_source_version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HazardSourceVersionLink":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_hazard_link")
        _reject_forbidden_tree(data)
        _reject_unknown(
            data,
            frozenset(
                {
                    "hazard_id",
                    "state",
                    "persisted_source_version",
                    "current_source_version",
                }
            ),
            "hazard_link",
        )
        return cls(
            hazard_id=_required(data, "hazard_id"),
            state=_parse_enum(
                DecisionReportedLinkState,
                _required(data, "state"),
                "hazard_link_state",
            ),
            persisted_source_version=_optional_id(data, "persisted_source_version"),
            current_source_version=_optional_id(data, "current_source_version"),
        )


@dataclass(frozen=True)
class DecisionEncounterLink:
    encounter_id: str
    hazard: HazardSourceVersionLink
    risk: DecisionRiskEvidence
    recommendations: DecisionRecommendationSet
    projection_id: str | None = None
    aircraft_state_version: str | None = None

    def __post_init__(self) -> None:
        errors = _operational_id(self.encounter_id, "encounter_id")
        errors.extend(_operational_id(self.projection_id, "projection_id", optional=True))
        errors.extend(
            _operational_id(
                self.aircraft_state_version,
                "aircraft_state_version",
                optional=True,
            )
        )
        if not isinstance(self.hazard, HazardSourceVersionLink):
            errors.append("invalid_hazard_link")
        if not isinstance(self.risk, DecisionRiskEvidence):
            errors.append("invalid_risk")
        if not isinstance(self.recommendations, DecisionRecommendationSet):
            errors.append("invalid_recommendation_set")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "encounter_id": self.encounter_id,
            "projection_id": self.projection_id,
            "aircraft_state_version": self.aircraft_state_version,
            "hazard": self.hazard.to_dict(),
            "risk": self.risk.to_dict(),
            "recommendations": self.recommendations.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionEncounterLink":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_encounter")
        _reject_forbidden_tree(data)
        _reject_unknown(
            data,
            frozenset(
                {
                    "encounter_id",
                    "projection_id",
                    "aircraft_state_version",
                    "hazard",
                    "risk",
                    "recommendations",
                }
            ),
            "encounter",
        )
        return cls(
            encounter_id=_required(data, "encounter_id"),
            hazard=HazardSourceVersionLink.from_dict(_required(data, "hazard")),
            risk=DecisionRiskEvidence.from_dict(_required(data, "risk")),
            recommendations=DecisionRecommendationSet.from_dict(
                _required(data, "recommendations")
            ),
            projection_id=_optional_id(data, "projection_id"),
            aircraft_state_version=_optional_id(data, "aircraft_state_version"),
        )


@dataclass(frozen=True)
class DecisionAlertLink:
    alert_id: str
    lineage: AlertCurrentLineage
    risk_id: str | None = None
    recommendation_id: str | None = None
    encounter_id: str | None = None

    def __post_init__(self) -> None:
        errors = _operational_id(self.alert_id, "alert_id")
        errors.extend(_operational_id(self.risk_id, "risk_id", optional=True))
        errors.extend(
            _operational_id(self.recommendation_id, "recommendation_id", optional=True)
        )
        errors.extend(_operational_id(self.encounter_id, "encounter_id", optional=True))
        if not isinstance(self.lineage, AlertCurrentLineage):
            errors.append("invalid_alert_lineage")
        elif self.lineage is AlertCurrentLineage.RISK:
            if self.risk_id is None or self.recommendation_id is not None:
                errors.append("invalid_alert_lineage")
        elif self.lineage is AlertCurrentLineage.RECOMMENDATION:
            if self.recommendation_id is None or self.risk_id is not None:
                errors.append("invalid_alert_lineage")
        elif self.risk_id is None or self.recommendation_id is None:
            errors.append("invalid_alert_lineage")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "alert_id": self.alert_id,
            "lineage": self.lineage.value,
            "risk_id": self.risk_id,
            "recommendation_id": self.recommendation_id,
            "encounter_id": self.encounter_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionAlertLink":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_alert")
        _reject_forbidden_tree(data)
        _reject_unknown(
            data,
            frozenset(
                {
                    "alert_id",
                    "lineage",
                    "risk_id",
                    "recommendation_id",
                    "encounter_id",
                }
            ),
            "alert",
        )
        return cls(
            alert_id=_required(data, "alert_id"),
            lineage=_parse_enum(
                AlertCurrentLineage,
                _required(data, "lineage"),
                "alert_lineage",
            ),
            risk_id=_optional_id(data, "risk_id"),
            recommendation_id=_optional_id(data, "recommendation_id"),
            encounter_id=_optional_id(data, "encounter_id"),
        )


def _gaps(values: tuple[DecisionChainGap, ...]) -> None:
    _enum_tuple(values, DecisionChainGap, "chain_gaps")


def _limitation_codes(values: tuple[DecisionLimitationCode, ...]) -> None:
    _enum_tuple(values, DecisionLimitationCode, "limitation_codes")


@dataclass(frozen=True)
class DecisionEvidence:
    """Typed ``ToolResult.data`` for current decision evidence.

    Multiple current recommendations stay a set. No field selects a winner.
    Narrow risk and recommendation evidence may carry several encounters.
    When they do, top-level risk and recommendations stay empty.
    When evaluation is not established, narrow evidence keeps its kind with
    an ABSENT risk and no recommendation set. That is not proven absence.
    Missing risk has presence ABSENT and no level or score. A stored LOW risk
    has presence PRESENT. Route capability is optional. When present,
    ``validated_alternative_available`` is false.
    """

    kind: DecisionEvidenceKind
    evaluation_state: DecisionEvaluationState
    aircraft_in_current_set: bool
    projection_state: DecisionReportedLinkState
    chain_gaps: tuple[DecisionChainGap, ...]
    limitation_codes: tuple[DecisionLimitationCode, ...]
    capability: DecisionRouteCapability | None = None
    aircraft_id: str | None = None
    projection_id: str | None = None
    aircraft_state_version: str | None = None
    encounters: tuple[DecisionEncounterLink, ...] = ()
    risk: DecisionRiskEvidence | None = None
    recommendations: DecisionRecommendationSet | None = None
    alerts: tuple[DecisionAlertLink, ...] = ()
    schema_version: str = DECISION_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        errors: list[str] = []
        if self.schema_version != DECISION_EVIDENCE_SCHEMA_VERSION:
            errors.append("invalid_schema_version")
        if not isinstance(self.kind, DecisionEvidenceKind):
            errors.append("invalid_kind")
        if not isinstance(self.evaluation_state, DecisionEvaluationState):
            errors.append("invalid_evaluation_state")
        if type(self.aircraft_in_current_set) is not bool:
            errors.append("invalid_aircraft_in_current_set")
        if not isinstance(self.projection_state, DecisionReportedLinkState):
            errors.append("invalid_projection_state")
        if self.capability is not None and not isinstance(
            self.capability,
            DecisionRouteCapability,
        ):
            errors.append("invalid_capability")
        errors.extend(_operational_id(self.aircraft_id, "aircraft_id", optional=True))
        errors.extend(_operational_id(self.projection_id, "projection_id", optional=True))
        errors.extend(
            _operational_id(
                self.aircraft_state_version,
                "aircraft_state_version",
                optional=True,
            )
        )
        if errors:
            raise ContractValidationError(errors)

        _gaps(self.chain_gaps)
        _limitation_codes(self.limitation_codes)
        if not isinstance(self.encounters, tuple) or any(
            not isinstance(item, DecisionEncounterLink) for item in self.encounters
        ):
            raise ContractValidationError("invalid_encounters")
        if self.risk is not None and not isinstance(self.risk, DecisionRiskEvidence):
            raise ContractValidationError("invalid_risk")
        if self.recommendations is not None and not isinstance(
            self.recommendations,
            DecisionRecommendationSet,
        ):
            raise ContractValidationError("invalid_recommendation_set")
        if not isinstance(self.alerts, tuple) or any(
            not isinstance(item, DecisionAlertLink) for item in self.alerts
        ):
            raise ContractValidationError("invalid_alerts")

        self._validate_kind_shape()
        self._validate_reported_chain()

    def _validate_kind_shape(self) -> None:
        if self.kind is DecisionEvidenceKind.DECISION_CONTEXT:
            if self.risk is not None or self.recommendations is not None:
                raise ContractValidationError("context_forbids_top_level_winner")
            return
        if self.encounters:
            if self.risk is not None or self.recommendations is not None:
                raise ContractValidationError("narrow_evidence_forbids_top_level_winner")
            return
        if self.kind is DecisionEvidenceKind.RISK_EVIDENCE:
            if self.risk is None or self.recommendations is not None:
                raise ContractValidationError("risk_evidence_requires_risk_only")
            return
        if (
            self.evaluation_state is not DecisionEvaluationState.ESTABLISHED
            and self.recommendations is None
            and self.risk is not None
            and self.risk.presence is RiskPresence.ABSENT
        ):
            return
        if self.recommendations is None or self.risk is None:
            raise ContractValidationError("recommendation_evidence_requires_risk_and_set")

    def _iter_risks(self) -> tuple[DecisionRiskEvidence, ...]:
        risks = [encounter.risk for encounter in self.encounters]
        if self.risk is not None:
            risks.append(self.risk)
        return tuple(risks)

    def _iter_recommendation_sets(self) -> tuple[DecisionRecommendationSet, ...]:
        sets = [encounter.recommendations for encounter in self.encounters]
        if self.recommendations is not None:
            sets.append(self.recommendations)
        return tuple(sets)

    def _validate_reported_chain(self) -> None:
        if self.evaluation_state is not DecisionEvaluationState.ESTABLISHED:
            if (
                self.encounters
                or self.recommendations
                or self.alerts
                or self.chain_gaps
                or self.aircraft_in_current_set
                or self.projection_state is DecisionReportedLinkState.PRESENT
                or self.projection_id is not None
            ):
                raise ContractValidationError("unestablished_evidence_must_stay_empty")
            if self.risk is not None and self.risk.presence is not RiskPresence.ABSENT:
                raise ContractValidationError("unestablished_evidence_forbids_stored_risk")
            return

        gaps = set(self.chain_gaps)
        aircraft_gap = DecisionChainGap.AIRCRAFT_NOT_IN_CURRENT_SET
        if self.aircraft_in_current_set:
            if aircraft_gap in gaps or self.aircraft_id is None:
                raise ContractValidationError("aircraft_gap_mismatch")
        elif aircraft_gap not in gaps or self.encounters:
            raise ContractValidationError("aircraft_gap_mismatch")

        projection_gap = DecisionChainGap.NO_CURRENT_PROJECTION
        if self.projection_state is DecisionReportedLinkState.PRESENT:
            if (
                self.projection_id is None
                or projection_gap in gaps
                or not self.aircraft_in_current_set
            ):
                raise ContractValidationError("projection_gap_mismatch")
        elif self.projection_id is not None or self.encounters:
            raise ContractValidationError("projection_gap_mismatch")
        elif self.aircraft_in_current_set and projection_gap not in gaps:
            raise ContractValidationError("projection_gap_mismatch")

        if (
            self.kind is DecisionEvidenceKind.DECISION_CONTEXT
            and self.projection_state is DecisionReportedLinkState.PRESENT
            and not self.encounters
            and DecisionChainGap.NO_CURRENT_ENCOUNTER not in gaps
        ):
            raise ContractValidationError("encounter_gap_mismatch")

        risks = self._iter_risks()
        risk_absent = any(item.presence is RiskPresence.ABSENT for item in risks)
        if risk_absent != (DecisionChainGap.RISK_ABSENT in gaps):
            raise ContractValidationError("risk_gap_mismatch")

        recommendation_sets = self._iter_recommendation_sets()
        recommendation_absent = any(not item.current for item in recommendation_sets)
        if recommendation_absent != (DecisionChainGap.RECOMMENDATION_ABSENT in gaps):
            raise ContractValidationError("recommendation_gap_mismatch")
        if recommendation_absent and (
            DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION
            not in self.limitation_codes
        ):
            raise ContractValidationError("recommendation_absence_requires_limitation")
        if any(item.current for item in recommendation_sets):
            if self.risk is not None and self.risk.presence is not RiskPresence.PRESENT:
                raise ContractValidationError("recommendations_require_present_risk")
            for encounter in self.encounters:
                if (
                    encounter.recommendations.current
                    and encounter.risk.presence is not RiskPresence.PRESENT
                ):
                    raise ContractValidationError("recommendations_require_present_risk")

        mismatch = any(
            encounter.hazard.state
            is DecisionReportedLinkState.HYDRATION_VERSION_MISMATCH
            for encounter in self.encounters
        )
        if mismatch != (DecisionChainGap.HAZARD_SOURCE_VERSION_MISMATCH in gaps):
            raise ContractValidationError("hazard_gap_mismatch")

        stale = any(item.excluded_stale_ids for item in recommendation_sets)
        if stale != (DecisionChainGap.STALE_RECOMMENDATION_EXCLUDED in gaps):
            raise ContractValidationError("stale_gap_mismatch")
        self._validate_risk_references()

    def _validate_risk_references(self) -> None:
        pairs: list[tuple[DecisionRiskEvidence, DecisionRecommendationSet]] = [
            (encounter.risk, encounter.recommendations) for encounter in self.encounters
        ]
        if self.risk is not None and self.recommendations is not None:
            pairs.append((self.risk, self.recommendations))
        for risk, recommendations in pairs:
            if risk.presence is not RiskPresence.PRESENT:
                continue
            for item in recommendations.current:
                for reference in item.evidence_references:
                    if (
                        reference.reference_type
                        is PersistedEvidenceReferenceType.RISK_RESULT
                        and reference.record_id != risk.risk_id
                    ):
                        raise ContractValidationError("evidence_reference_risk_mismatch")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind.value,
            "evaluation_state": self.evaluation_state.value,
            "aircraft_id": self.aircraft_id,
            "aircraft_in_current_set": self.aircraft_in_current_set,
            "projection_state": self.projection_state.value,
            "projection_id": self.projection_id,
            "aircraft_state_version": self.aircraft_state_version,
            "encounters": [item.to_dict() for item in self.encounters],
            "risk": None if self.risk is None else self.risk.to_dict(),
            "recommendations": (
                None if self.recommendations is None else self.recommendations.to_dict()
            ),
            "alerts": [item.to_dict() for item in self.alerts],
            "capability": None if self.capability is None else self.capability.to_dict(),
            "chain_gaps": [item.value for item in self.chain_gaps],
            "limitation_codes": [item.value for item in self.limitation_codes],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionEvidence":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_decision_evidence")
        _reject_forbidden_tree(data)
        _reject_unknown(
            data,
            frozenset(
                {
                    "schema_version",
                    "kind",
                    "evaluation_state",
                    "aircraft_id",
                    "aircraft_in_current_set",
                    "projection_state",
                    "projection_id",
                    "aircraft_state_version",
                    "encounters",
                    "risk",
                    "recommendations",
                    "alerts",
                    "capability",
                    "chain_gaps",
                    "limitation_codes",
                }
            ),
            "decision_evidence",
        )
        risk = data["risk"] if "risk" in data else None
        recommendations = data["recommendations"] if "recommendations" in data else None
        if "capability" not in data or data["capability"] is None:
            capability = None
        else:
            capability = DecisionRouteCapability.from_dict(data["capability"])
        return cls(
            schema_version=_required(data, "schema_version"),
            kind=_parse_enum(
                DecisionEvidenceKind,
                _required(data, "kind"),
                "kind",
            ),
            evaluation_state=_parse_enum(
                DecisionEvaluationState,
                _required(data, "evaluation_state"),
                "evaluation_state",
            ),
            aircraft_id=_optional_id(data, "aircraft_id"),
            aircraft_in_current_set=_required(data, "aircraft_in_current_set"),
            projection_state=_parse_enum(
                DecisionReportedLinkState,
                _required(data, "projection_state"),
                "projection_state",
            ),
            projection_id=_optional_id(data, "projection_id"),
            aircraft_state_version=_optional_id(data, "aircraft_state_version"),
            encounters=tuple(
                DecisionEncounterLink.from_dict(item)
                for item in _sequence(
                    data["encounters"] if "encounters" in data else [],
                    "encounters",
                )
            ),
            risk=None if risk is None else DecisionRiskEvidence.from_dict(risk),
            recommendations=(
                None
                if recommendations is None
                else DecisionRecommendationSet.from_dict(recommendations)
            ),
            alerts=tuple(
                DecisionAlertLink.from_dict(item)
                for item in _sequence(data["alerts"] if "alerts" in data else [], "alerts")
            ),
            capability=capability,
            chain_gaps=tuple(
                _parse_enum(DecisionChainGap, item, "chain_gaps")
                for item in _sequence(_required(data, "chain_gaps"), "chain_gaps")
            ),
            limitation_codes=tuple(
                _parse_enum(DecisionLimitationCode, item, "limitation_codes")
                for item in _sequence(
                    _required(data, "limitation_codes"),
                    "limitation_codes",
                )
            ),
        )


def expected_decision_status(evidence: DecisionEvidence) -> ToolResultStatus:
    """Map reported gaps to an existing ToolResult status. Does not fetch data."""

    if evidence.evaluation_state is DecisionEvaluationState.NOT_ESTABLISHED:
        return ToolResultStatus.UNKNOWN
    if evidence.evaluation_state is DecisionEvaluationState.SOURCE_UNAVAILABLE:
        return ToolResultStatus.UNAVAILABLE
    if DecisionChainGap.AIRCRAFT_NOT_IN_CURRENT_SET in evidence.chain_gaps:
        return ToolResultStatus.NOT_FOUND
    if BLOCKING_CHAIN_GAPS.intersection(item.value for item in evidence.chain_gaps):
        return ToolResultStatus.PARTIAL
    return ToolResultStatus.SUCCESS


def validate_decision_tool_result(result: ToolResult) -> DecisionEvidence:
    """Accept a current ToolResult whose data is decision evidence.

    Missing risk is not SUCCESS with level LOW. Missing recommendations are
    not SUCCESS with action MONITOR. A missing route is not NOT_FOUND.
    """

    if not isinstance(result, ToolResult):
        raise ContractValidationError("invalid_tool_result")
    if result.temporal_scope is not TemporalScope.CURRENT:
        raise ContractValidationError("decision_temporal_scope_must_be_current")
    if any(item.temporal_scope is not TemporalScope.CURRENT for item in result.evidence):
        raise ContractValidationError("decision_temporal_scope_must_be_current")
    if not isinstance(result.data, Mapping):
        raise ContractValidationError("invalid_decision_evidence")

    evidence = DecisionEvidence.from_dict(result.data)
    expected = expected_decision_status(evidence)
    if result.status is not expected:
        raise ContractValidationError("decision_status_mismatch")
    if expected is ToolResultStatus.NOT_FOUND and any(
        item.presence is RiskPresence.PRESENT and item.risk_level is not None
        for item in evidence._iter_risks()
    ):
        raise ContractValidationError("not_found_forbids_stored_risk")
    return evidence
