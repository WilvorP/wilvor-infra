"""Persisted airport-evaluation evidence. Not a current decision chain."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

from wilvor_ai.contracts import (
    ContractValidationError,
    TemporalScope,
    ToolResult,
)
from wilvor_ai.decision_contracts import (
    FORBIDDEN_DECISION_KEYS,
    PersistedEvaluationScope,
)


PERSISTED_AIRPORT_EVIDENCE_SCHEMA_VERSION = "wilvor.ai.persisted_airport_evidence.v1"
COMPLETE = "COMPLETE"
WAITING_FOR_WEATHER = "WAITING_FOR_WEATHER"
UNAVAILABLE = "UNAVAILABLE"
_ACCEPTED_STATUSES = frozenset({COMPLETE, WAITING_FOR_WEATHER})
_EXTRA_FORBIDDEN = frozenset(
    {
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
    }
)
_FORBIDDEN = FORBIDDEN_DECISION_KEYS | _EXTRA_FORBIDDEN


def _reject_forbidden_tree(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _FORBIDDEN:
                raise ContractValidationError("forbidden_persisted_airport_field")
            _reject_forbidden_tree(item)
    elif isinstance(value, list):
        for item in value:
            _reject_forbidden_tree(item)


def _required(data: Mapping[str, Any], key: str) -> Any:
    if key not in data:
        raise ContractValidationError(f"missing_{key}")
    return data[key]


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _text(value, field_name)


def _json_number(value: Any, field_name: str) -> int | float:
    number = _decimal(value)
    if number is None:
        raise ContractValidationError(f"invalid_{field_name}")
    if number == number.to_integral_value():
        return int(number)
    return float(number)


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except Exception:
            return None
    return None


def numbers_equal(left: Any, right: Any) -> bool:
    """Compare persisted numbers without binary-float rounding."""

    first = _decimal(left)
    second = _decimal(right)
    if first is None or second is None:
        return False
    return first == second


@dataclass(frozen=True)
class PersistedAirportCandidate:
    airport_id: str
    airport_assessment_id: str
    risk_id: str
    assessment_status: str
    route_safety_status: str
    runway_evidence_status: str
    congestion_evidence_status: str
    created_at_utc: str
    created_at_epoch: int
    expires_at_epoch: int
    evaluation_version: str
    assessment_ruleset_version: str
    schema_version: str
    distance_nm: int | float
    eta_minutes: int | float
    candidate_reason: str
    known_limitations: tuple[str, ...]
    rank: int | None = None
    total_airport_score: int | float | None = None
    distance_score: int | float | None = None
    weather_score: int | float | None = None
    taf_score: int | float | None = None
    estimated_arrival_time_utc: str | None = None
    weather_risk_level: str | None = None
    metar_version: str | None = None
    taf_version: str | None = None
    taf_period_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.airport_id, "airport_id")
        _text(self.airport_assessment_id, "airport_assessment_id")
        _text(self.risk_id, "risk_id")
        if self.assessment_status not in _ACCEPTED_STATUSES:
            raise ContractValidationError("invalid_assessment_status")
        for field_name in (
            "route_safety_status",
            "runway_evidence_status",
            "congestion_evidence_status",
        ):
            if getattr(self, field_name) != UNAVAILABLE:
                raise ContractValidationError(f"invalid_{field_name}")
        _text(self.created_at_utc, "created_at_utc")
        _text(self.evaluation_version, "evaluation_version")
        _text(self.assessment_ruleset_version, "assessment_ruleset_version")
        _text(self.schema_version, "schema_version")
        _text(self.candidate_reason, "candidate_reason")
        if type(self.created_at_epoch) is not int or type(self.expires_at_epoch) is not int:
            raise ContractValidationError("invalid_generation_epoch")
        if not isinstance(self.known_limitations, tuple) or any(
            not isinstance(item, str) or not item for item in self.known_limitations
        ):
            raise ContractValidationError("invalid_known_limitations")
        if not isinstance(self.taf_period_ids, tuple) or any(
            not isinstance(item, str) or not item for item in self.taf_period_ids
        ):
            raise ContractValidationError("invalid_taf_period_ids")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "airport_id": self.airport_id,
            "airport_assessment_id": self.airport_assessment_id,
            "risk_id": self.risk_id,
            "assessment_status": self.assessment_status,
            "route_safety_status": self.route_safety_status,
            "runway_evidence_status": self.runway_evidence_status,
            "congestion_evidence_status": self.congestion_evidence_status,
            "created_at_utc": self.created_at_utc,
            "created_at_epoch": self.created_at_epoch,
            "expires_at_epoch": self.expires_at_epoch,
            "evaluation_version": self.evaluation_version,
            "assessment_ruleset_version": self.assessment_ruleset_version,
            "schema_version": self.schema_version,
            "distance_nm": self.distance_nm,
            "eta_minutes": self.eta_minutes,
            "candidate_reason": self.candidate_reason,
            "known_limitations": list(self.known_limitations),
            "rank": self.rank,
            "total_airport_score": self.total_airport_score,
            "distance_score": self.distance_score,
            "weather_score": self.weather_score,
            "taf_score": self.taf_score,
            "estimated_arrival_time_utc": self.estimated_arrival_time_utc,
            "weather_risk_level": self.weather_risk_level,
            "metar_version": self.metar_version,
            "taf_version": self.taf_version,
            "taf_period_ids": list(self.taf_period_ids),
        }
        return payload


@dataclass(frozen=True)
class PersistedAirportEvidence:
    recommendation_id: str
    airport_evaluation_id: str | None
    candidates: tuple[PersistedAirportCandidate, ...]
    no_suitable_candidate_reason: str | None = None
    recommendation_observed_empty_at_utc: str | None = None
    scope: PersistedEvaluationScope = (
        PersistedEvaluationScope.PERSISTED_EVALUATION_EVIDENCE
    )
    schema_version: str = PERSISTED_AIRPORT_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _text(self.recommendation_id, "recommendation_id")
        if self.airport_evaluation_id is not None:
            _text(self.airport_evaluation_id, "airport_evaluation_id")
        if self.scope is not PersistedEvaluationScope.PERSISTED_EVALUATION_EVIDENCE:
            raise ContractValidationError("invalid_evaluation_scope")
        if self.schema_version != PERSISTED_AIRPORT_EVIDENCE_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if self.no_suitable_candidate_reason is not None:
            _text(self.no_suitable_candidate_reason, "no_suitable_candidate_reason")
        if self.recommendation_observed_empty_at_utc is not None:
            _text(
                self.recommendation_observed_empty_at_utc,
                "recommendation_observed_empty_at_utc",
            )
        if not isinstance(self.candidates, tuple) or any(
            not isinstance(item, PersistedAirportCandidate) for item in self.candidates
        ):
            raise ContractValidationError("invalid_candidates")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "recommendation_id": self.recommendation_id,
            "airport_evaluation_id": self.airport_evaluation_id,
            "scope": self.scope.value,
            "candidates": [item.to_dict() for item in self.candidates],
            "no_suitable_candidate_reason": self.no_suitable_candidate_reason,
            "recommendation_observed_empty_at_utc": (
                self.recommendation_observed_empty_at_utc
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PersistedAirportEvidence":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_persisted_airport_evidence")
        _reject_forbidden_tree(data)
        candidates = tuple(
            _candidate_from_dict(item) for item in _required(data, "candidates")
        )
        scope = _required(data, "scope")
        if scope != PersistedEvaluationScope.PERSISTED_EVALUATION_EVIDENCE.value:
            raise ContractValidationError("invalid_evaluation_scope")
        return cls(
            recommendation_id=_text(_required(data, "recommendation_id"), "recommendation_id"),
            airport_evaluation_id=_optional_text(
                data["airport_evaluation_id"] if "airport_evaluation_id" in data else None,
                "airport_evaluation_id",
            ),
            candidates=candidates,
            no_suitable_candidate_reason=_optional_text(
                data["no_suitable_candidate_reason"]
                if "no_suitable_candidate_reason" in data
                else None,
                "no_suitable_candidate_reason",
            ),
            recommendation_observed_empty_at_utc=_optional_text(
                data["recommendation_observed_empty_at_utc"]
                if "recommendation_observed_empty_at_utc" in data
                else None,
                "recommendation_observed_empty_at_utc",
            ),
            schema_version=_text(
                _required(data, "schema_version"),
                "schema_version",
            ),
        )


def _candidate_from_dict(data: Any) -> PersistedAirportCandidate:
    if not isinstance(data, Mapping):
        raise ContractValidationError("invalid_candidate")
    _reject_forbidden_tree(data)

    def optional_number(key: str) -> int | float | None:
        if key not in data or data[key] is None:
            return None
        return _json_number(data[key], key)

    periods = data["taf_period_ids"] if "taf_period_ids" in data else []
    limitations = _required(data, "known_limitations")
    return PersistedAirportCandidate(
        airport_id=_text(_required(data, "airport_id"), "airport_id"),
        airport_assessment_id=_text(
            _required(data, "airport_assessment_id"),
            "airport_assessment_id",
        ),
        risk_id=_text(_required(data, "risk_id"), "risk_id"),
        assessment_status=_text(_required(data, "assessment_status"), "assessment_status"),
        route_safety_status=_text(
            _required(data, "route_safety_status"),
            "route_safety_status",
        ),
        runway_evidence_status=_text(
            _required(data, "runway_evidence_status"),
            "runway_evidence_status",
        ),
        congestion_evidence_status=_text(
            _required(data, "congestion_evidence_status"),
            "congestion_evidence_status",
        ),
        created_at_utc=_text(_required(data, "created_at_utc"), "created_at_utc"),
        created_at_epoch=_json_number(_required(data, "created_at_epoch"), "created_at_epoch"),
        expires_at_epoch=_json_number(_required(data, "expires_at_epoch"), "expires_at_epoch"),
        evaluation_version=_text(_required(data, "evaluation_version"), "evaluation_version"),
        assessment_ruleset_version=_text(
            _required(data, "assessment_ruleset_version"),
            "assessment_ruleset_version",
        ),
        schema_version=_text(_required(data, "schema_version"), "schema_version"),
        distance_nm=_json_number(_required(data, "distance_nm"), "distance_nm"),
        eta_minutes=_json_number(_required(data, "eta_minutes"), "eta_minutes"),
        candidate_reason=_text(_required(data, "candidate_reason"), "candidate_reason"),
        known_limitations=tuple(limitations),
        rank=optional_number("rank"),
        total_airport_score=optional_number("total_airport_score"),
        distance_score=optional_number("distance_score"),
        weather_score=optional_number("weather_score"),
        taf_score=optional_number("taf_score"),
        estimated_arrival_time_utc=_optional_text(
            data["estimated_arrival_time_utc"]
            if "estimated_arrival_time_utc" in data
            else None,
            "estimated_arrival_time_utc",
        ),
        weather_risk_level=_optional_text(
            data["weather_risk_level"] if "weather_risk_level" in data else None,
            "weather_risk_level",
        ),
        metar_version=_optional_text(
            data["metar_version"] if "metar_version" in data else None,
            "metar_version",
        ),
        taf_version=_optional_text(
            data["taf_version"] if "taf_version" in data else None,
            "taf_version",
        ),
        taf_period_ids=tuple(periods),
    )


def validate_persisted_airport_tool_result(result: ToolResult) -> PersistedAirportEvidence:
    """Accept a persisted ToolResult. Current decision evidence stays separate."""

    if not isinstance(result, ToolResult):
        raise ContractValidationError("invalid_tool_result")
    if result.temporal_scope is not TemporalScope.PERSISTED:
        raise ContractValidationError("persisted_airport_temporal_scope")
    if any(item.temporal_scope is not TemporalScope.PERSISTED for item in result.evidence):
        raise ContractValidationError("persisted_airport_evidence_scope")
    if not isinstance(result.data, Mapping):
        raise ContractValidationError("invalid_persisted_airport_evidence")
    _reject_forbidden_tree(result.data)
    return PersistedAirportEvidence.from_dict(result.data)
