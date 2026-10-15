"""Closed Live Ops claim contracts.

A valid claim is not an operational fact. LO2 verification checks claims
against raw ToolResults. This module does not verify evidence, render
answers, call a model, or read operational tables.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
    JsonValue,
    _parse_enum,
    _required,
    _sequence,
    _validate_bounded_text,
)


LIVE_OPS_MAX_CLAIMS_PER_RESPONSE = 32
LIVE_OPS_HAZARD_IDS_MAX = 25
LIVE_OPS_IDENTITY_SET_MAX = 25

LIVE_OPS_TOOL_NAMES = frozenset(
    {
        "search_current_hazards",
        "search_current_impacts",
        "search_current_encounters",
        "get_observed_network_state",
        "get_aircraft_operational_context",
        "get_hazard_operational_context",
        "get_airport_operational_context",
        "find_aircraft_by_callsign",
    }
)

_SCOPE_FIELDS = frozenset(
    {
        "region",
        "product_type",
        "hazard_type",
        "hazard_ids",
        "aircraft_id",
        "callsign",
        "hazard_id",
    }
)
_PROSE_KEYS = frozenset(
    {
        "answer",
        "answer_text",
        "advisory_notice",
        "candidate_answer",
        "claim_text",
        "explanation",
        "factual_text",
        "message",
        "projection_trigger_reason",
        "reason",
        "state_reason",
        "statement",
        "summary",
    }
)
_FORBIDDEN_KEYS = frozenset(
    {
        "alternative_actions",
        "altitude_instruction",
        "atc_instruction",
        "best_airport",
        "computed_risk",
        "dispatch_instruction",
        "diversion_airport",
        "landing_instruction",
        "preferred_airport",
        "preferred_airport_assessment_id",
        "preferred_airport_id",
        "preferred_airport_score",
        "primary_action_details",
        "risk_score_calculated",
        "route",
        "safest_airport",
        "score",
        "selected_aircraft_id",
        "selected_airport",
        "selected_recommendation_id",
        "waypoint",
        "winning_recommendation",
        "winning_recommendation_id",
    }
)
_VISIBLE_LIST_NAMES = frozenset(
    {
        "hazards",
        "impacts",
        "encounters",
        "matches",
        "unique_aircraft_ids",
        "callsign_matches",
        "geospatial_unevaluated",
        "operationally_unevaluated",
        "rejected_hazard_candidates",
        "rejected_hazard_selections",
        "current_aircraft_ids",
        "current_hazard_ids",
        "current_encounter_ids",
        "current_risk_ids",
        "current_recommendation_ids",
        "current_alert_ids",
    }
)


class LiveOpsClaimKind(str, Enum):
    CONFIRMED_IMPACT = "CONFIRMED_IMPACT"
    CONFIRMED_ENCOUNTER = "CONFIRMED_ENCOUNTER"
    SCOPED_COUNT = "SCOPED_COUNT"
    QUERY_RESULT_ZERO = "QUERY_RESULT_ZERO"
    CONTEXT_ROOT_ABSENT = "CONTEXT_ROOT_ABSENT"
    HAZARD_REGION_INTERSECTION = "HAZARD_REGION_INTERSECTION"
    AIRCRAFT_OBSERVED_STATE = "AIRCRAFT_OBSERVED_STATE"
    AIRCRAFT_PROJECTION_STATE = "AIRCRAFT_PROJECTION_STATE"
    CURRENTNESS = "CURRENTNESS"
    RISK_STATE = "RISK_STATE"
    RECOMMENDATION_SET = "RECOMMENDATION_SET"
    ALERT_RECORD = "ALERT_RECORD"
    CALLSIGN_MATCH_SET = "CALLSIGN_MATCH_SET"
    COVERAGE_LIMITATION = "COVERAGE_LIMITATION"
    MODEL_VISIBLE_TRUNCATION = "MODEL_VISIBLE_TRUNCATION"
    CROSS_TOOL_CONTRADICTION = "CROSS_TOOL_CONTRADICTION"


class LiveOpsCountMetric(str, Enum):
    IMPACT_RECORD_COUNT = "IMPACT_RECORD_COUNT"
    ENCOUNTER_RECORD_COUNT = "ENCOUNTER_RECORD_COUNT"
    UNIQUE_AIRCRAFT_COUNT = "UNIQUE_AIRCRAFT_COUNT"
    UNIQUE_HAZARD_COUNT = "UNIQUE_HAZARD_COUNT"
    HAZARD_QUERY_MATCH_COUNT = "HAZARD_QUERY_MATCH_COUNT"
    CALLSIGN_MATCH_COUNT = "CALLSIGN_MATCH_COUNT"
    NETWORK_AIRCRAFT_COUNT = "NETWORK_AIRCRAFT_COUNT"
    NETWORK_HAZARD_COUNT = "NETWORK_HAZARD_COUNT"
    NETWORK_ENCOUNTER_COUNT = "NETWORK_ENCOUNTER_COUNT"
    NETWORK_RISK_COUNT = "NETWORK_RISK_COUNT"
    NETWORK_RECOMMENDATION_COUNT = "NETWORK_RECOMMENDATION_COUNT"
    NETWORK_ALERT_COUNT = "NETWORK_ALERT_COUNT"
    HAZARD_CONTEXT_IMPACT_COUNT = "HAZARD_CONTEXT_IMPACT_COUNT"
    AIRCRAFT_CONTEXT_ENCOUNTER_COUNT = "AIRCRAFT_CONTEXT_ENCOUNTER_COUNT"


class LiveOpsCountMode(str, Enum):
    EXACT_RETURNED_SET = "EXACT_RETURNED_SET"
    CONFIRMED_SUBSET = "CONFIRMED_SUBSET"


class LiveOpsCurrentnessPredicate(str, Enum):
    AIRCRAFT_IS_CURRENT = "AIRCRAFT_IS_CURRENT"
    HAZARD_IS_CURRENT = "HAZARD_IS_CURRENT"
    HAZARD_IS_LIFECYCLE_ACTIVE = "HAZARD_IS_LIFECYCLE_ACTIVE"
    PROJECTION_IS_CURRENT = "PROJECTION_IS_CURRENT"
    ENCOUNTER_IS_CURRENT = "ENCOUNTER_IS_CURRENT"
    AIRPORT_IS_CURRENT = "AIRPORT_IS_CURRENT"
    CURRENT_IMPACTS_EVALUATED = "CURRENT_IMPACTS_EVALUATED"
    HAZARD_SELECTION_NOT_CURRENT = "HAZARD_SELECTION_NOT_CURRENT"


class LiveOpsRiskMode(str, Enum):
    PRESENT = "PRESENT"
    ABSENT_FROM_OBSERVED_CANDIDATES = "ABSENT_FROM_OBSERVED_CANDIDATES"


class LiveOpsAdvisoryMode(str, Enum):
    PRESENT_SET = "PRESENT_SET"
    ABSENT_FROM_OBSERVED_CANDIDATES = "ABSENT_FROM_OBSERVED_CANDIDATES"


class LiveOpsObservedField(str, Enum):
    LATITUDE = "latitude"
    LONGITUDE = "longitude"
    BARO_ALTITUDE_FT = "baro_altitude_ft"
    GEO_ALTITUDE_FT = "geo_altitude_ft"
    GROUND_SPEED_KT = "ground_speed_kt"
    TRACK_DEG = "track_deg"
    ON_GROUND = "on_ground"
    POSITION_TIME_UTC = "position_time_utc"
    HAS_POSITION = "has_position"
    CURRENT_H3_CELL = "current_h3_cell"
    FRESHNESS_STATUS = "freshness_status"


class LiveOpsProjectionField(str, Enum):
    PROJECTION_ID = "projection_id"
    AIRCRAFT_ID = "aircraft_id"
    GENERATED_AT_UTC = "generated_at_utc"
    VALID_UNTIL_UTC = "valid_until_utc"
    PROJECTION_HORIZON_MIN = "projection_horizon_min"
    PROJECTION_STATUS = "projection_status"
    CURRENT_AIRCRAFT_H3_CELL = "current_aircraft_h3_cell"
    CONFIDENCE = "confidence"


class LiveOpsRecordedEncounterFlag(str, Enum):
    CORRIDOR_INTERSECTS = "corridor_intersects"
    CENTERLINE_INTERSECTS = "centerline_intersects"
    INSIDE_NOW = "inside_now"
    EXACT_INTERSECTION_CONFIRMED = "exact_intersection_confirmed"


class LiveOpsOverlapStatusField(str, Enum):
    GEOMETRY_OVERLAP_STATUS = "geometry_overlap_status"
    TIME_OVERLAP_STATUS = "time_overlap_status"
    ALTITUDE_OVERLAP_STATUS = "altitude_overlap_status"


class LiveOpsLimitationCode(str, Enum):
    FRESHNESS_NOT_ESTABLISHED = "FRESHNESS_NOT_ESTABLISHED"
    NO_SNAPSHOT_LIMITATION = "NO_SNAPSHOT_LIMITATION"
    GEOSPATIAL_UNEVALUATED_PRESENT = "GEOSPATIAL_UNEVALUATED_PRESENT"
    OPERATIONALLY_UNEVALUATED_PRESENT = "OPERATIONALLY_UNEVALUATED_PRESENT"
    REGION_UNRESOLVED = "REGION_UNRESOLVED"
    LINEAGE_MISMATCH_PRESENT = "LINEAGE_MISMATCH_PRESENT"
    ALERT_EVIDENCE_IDENTITY_UNAVAILABLE = "ALERT_EVIDENCE_IDENTITY_UNAVAILABLE"
    QUERY_SELECTION_REQUIRED = "QUERY_SELECTION_REQUIRED"
    ENCOUNTER_SELECTION_REQUIRED = "ENCOUNTER_SELECTION_REQUIRED"
    CALLSIGN_REQUIRED = "CALLSIGN_REQUIRED"
    CALLSIGN_CASE_LIMITATION = "CALLSIGN_CASE_LIMITATION"
    EVENTUAL_SCAN_LIMITATION = "EVENTUAL_SCAN_LIMITATION"
    RECOMMENDATION_ABSENCE_LIMITATION = "RECOMMENDATION_ABSENCE_LIMITATION"
    ALERT_ABSENCE_LIMITATION = "ALERT_ABSENCE_LIMITATION"
    OBSERVED_INVENTORY_NOT_A_COMPLETE_SNAPSHOT = (
        "OBSERVED_INVENTORY_NOT_A_COMPLETE_SNAPSHOT"
    )
    SPATIAL_EVALUATION_UNSUPPORTED = "SPATIAL_EVALUATION_UNSUPPORTED"
    RECORDED_OVERLAP_NOT_PHYSICAL_ENTRY = "RECORDED_OVERLAP_NOT_PHYSICAL_ENTRY"


class LiveOpsContradictionSubjectKind(str, Enum):
    HAZARD_SOURCE_VERSION = "HAZARD_SOURCE_VERSION"
    AIRCRAFT_CURRENTNESS = "AIRCRAFT_CURRENTNESS"
    ENCOUNTER_LINEAGE = "ENCOUNTER_LINEAGE"


def _reject_tree(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractValidationError("unexpected_claim_field")
            folded = key.casefold()
            if folded in _PROSE_KEYS:
                raise ContractValidationError("unexpected_claim_prose")
            if folded in _FORBIDDEN_KEYS:
                raise ContractValidationError("forbidden_claim_field")
            _reject_tree(item)
        return
    if isinstance(value, list):
        for item in value:
            _reject_tree(item)


def _load(
    data: Mapping[str, Any],
    allowed: frozenset[str],
    kind: LiveOpsClaimKind,
) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise ContractValidationError("invalid_live_ops_claim")
    _reject_tree(data)
    if set(data) - allowed:
        raise ContractValidationError("unexpected_claim_field")
    if _required(data, "kind") != kind.value:
        raise ContractValidationError("invalid_kind")
    return data


def _token(value: Any, field_name: str) -> str:
    errors = _validate_bounded_text(
        value,
        field_name,
        max_length=PROVENANCE_TOKEN_MAX_LENGTH,
    )
    if errors:
        raise ContractValidationError(errors)
    if value != value.strip():
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _tool_name(value: Any, allowed: frozenset[str]) -> str:
    name = _token(value, "tool_name")
    if name not in allowed:
        raise ContractValidationError("invalid_tool_name")
    return name


def _count(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _id_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ContractValidationError(f"invalid_{field_name}")
    if len(value) > LIVE_OPS_IDENTITY_SET_MAX:
        raise ContractValidationError(f"too_many_{field_name}")
    items = tuple(_token(item, field_name) for item in value)
    if len(items) != len(set(items)):
        raise ContractValidationError(f"duplicate_{field_name}")
    if list(items) != sorted(items):
        raise ContractValidationError(f"unsorted_{field_name}")
    return items


def _parse_id_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    return _id_tuple(tuple(_sequence(value, field_name)), field_name)


def _optional_token(data: Mapping[str, Any], field_name: str) -> str | None:
    if field_name not in data or data[field_name] is None:
        return None
    return _token(data[field_name], field_name)


def _scalar_value(value: Any, field_name: str) -> JsonValue:
    if isinstance(value, bool) or isinstance(value, str):
        if isinstance(value, str):
            return _token(value, field_name)
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ContractValidationError(f"invalid_{field_name}")


@dataclass(frozen=True)
class LiveOpsExecutedScope:
    """Executed selector echo. Absent fields are not part of the scope."""

    region: str | None = None
    product_type: str | None = None
    hazard_type: str | None = None
    hazard_ids: tuple[str, ...] | None = None
    aircraft_id: str | None = None
    callsign: str | None = None
    hazard_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("region", "product_type", "hazard_type", "aircraft_id", "callsign", "hazard_id"):
            value = getattr(self, name)
            if value is not None:
                _token(value, name)
        if self.hazard_ids is not None:
            parsed = _id_tuple(self.hazard_ids, "hazard_ids")
            if not parsed or len(parsed) > LIVE_OPS_HAZARD_IDS_MAX:
                raise ContractValidationError("invalid_hazard_ids")
            object.__setattr__(self, "hazard_ids", parsed)

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {}
        for name in ("region", "product_type", "hazard_type", "aircraft_id", "callsign", "hazard_id"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        if self.hazard_ids is not None:
            payload["hazard_ids"] = list(self.hazard_ids)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsExecutedScope":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_scope")
        _reject_tree(data)
        if set(data) - _SCOPE_FIELDS:
            raise ContractValidationError("unexpected_claim_field")
        hazard_ids = None
        if "hazard_ids" in data:
            hazard_ids = _parse_id_tuple(data["hazard_ids"], "hazard_ids")
        return cls(
            region=data["region"] if "region" in data else None,
            product_type=data["product_type"] if "product_type" in data else None,
            hazard_type=data["hazard_type"] if "hazard_type" in data else None,
            hazard_ids=hazard_ids,
            aircraft_id=data["aircraft_id"] if "aircraft_id" in data else None,
            callsign=data["callsign"] if "callsign" in data else None,
            hazard_id=data["hazard_id"] if "hazard_id" in data else None,
        )


def _scope(data: Mapping[str, Any]) -> LiveOpsExecutedScope:
    if "scope" not in data or not isinstance(data["scope"], Mapping):
        raise ContractValidationError("invalid_scope")
    return LiveOpsExecutedScope.from_dict(data["scope"])


def _flag_pairs(value: Any, enum_type: type[Enum], field_name: str) -> tuple[tuple[str, Any], ...]:
    rows = _sequence(value, field_name)
    if len(rows) > len(enum_type):
        raise ContractValidationError(f"too_many_{field_name}")
    parsed: list[tuple[str, Any]] = []
    for item in rows:
        if not isinstance(item, Mapping) or set(item) != {"name", "value"}:
            raise ContractValidationError(f"invalid_{field_name}")
        name = _parse_enum(enum_type, _required(item, "name"), "name")
        parsed.append((name.value, item["value"]))
    names = [item[0] for item in parsed]
    if len(names) != len(set(names)):
        raise ContractValidationError(f"duplicate_{field_name}")
    parsed.sort(key=lambda item: item[0])
    return tuple(parsed)


@dataclass(frozen=True)
class ConfirmedImpactClaim:
    tool_call_id: str
    tool_name: str
    aircraft_id: str
    hazard_id: str
    source_version: str
    encounter_id: str

    def __post_init__(self) -> None:
        _token(self.tool_call_id, "tool_call_id")
        _tool_name(
            self.tool_name,
            frozenset({"search_current_impacts", "get_hazard_operational_context"}),
        )
        for name in ("aircraft_id", "hazard_id", "source_version", "encounter_id"):
            _token(getattr(self, name), name)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": LiveOpsClaimKind.CONFIRMED_IMPACT.value,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "aircraft_id": self.aircraft_id,
            "hazard_id": self.hazard_id,
            "source_version": self.source_version,
            "encounter_id": self.encounter_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConfirmedImpactClaim":
        payload = _load(
            data,
            frozenset(
                {
                    "kind",
                    "tool_call_id",
                    "tool_name",
                    "aircraft_id",
                    "hazard_id",
                    "source_version",
                    "encounter_id",
                }
            ),
            LiveOpsClaimKind.CONFIRMED_IMPACT,
        )
        return cls(
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            aircraft_id=_required(payload, "aircraft_id"),
            hazard_id=_required(payload, "hazard_id"),
            source_version=_required(payload, "source_version"),
            encounter_id=_required(payload, "encounter_id"),
        )


@dataclass(frozen=True)
class ConfirmedEncounterClaim:
    tool_call_id: str
    tool_name: str
    encounter_id: str
    aircraft_id: str
    hazard_id: str
    hazard_source_version: str
    projection_id: str | None = None
    recorded_flags: tuple[tuple[str, bool], ...] = ()
    overlap_statuses: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _token(self.tool_call_id, "tool_call_id")
        _tool_name(
            self.tool_name,
            frozenset(
                {"search_current_encounters", "get_aircraft_operational_context"}
            ),
        )
        for name in ("encounter_id", "aircraft_id", "hazard_id", "hazard_source_version"):
            _token(getattr(self, name), name)
        if self.projection_id is not None:
            _token(self.projection_id, "projection_id")
        if not isinstance(self.recorded_flags, tuple) or not isinstance(self.overlap_statuses, tuple):
            raise ContractValidationError("invalid_recorded_flags")
        if len(self.recorded_flags) > len(LiveOpsRecordedEncounterFlag):
            raise ContractValidationError("too_many_recorded_flags")
        if len(self.overlap_statuses) > len(LiveOpsOverlapStatusField):
            raise ContractValidationError("too_many_overlap_statuses")
        seen: set[str] = set()
        for name, value in self.recorded_flags:
            if name not in LiveOpsRecordedEncounterFlag._value2member_map_ or not isinstance(value, bool):
                raise ContractValidationError("invalid_recorded_flags")
            if name in seen:
                raise ContractValidationError("duplicate_recorded_flags")
            seen.add(name)
        seen.clear()
        for name, value in self.overlap_statuses:
            if name not in LiveOpsOverlapStatusField._value2member_map_:
                raise ContractValidationError("invalid_overlap_statuses")
            _token(value, "overlap_status")
            if name in seen:
                raise ContractValidationError("duplicate_overlap_statuses")
            seen.add(name)

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "kind": LiveOpsClaimKind.CONFIRMED_ENCOUNTER.value,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "encounter_id": self.encounter_id,
            "aircraft_id": self.aircraft_id,
            "hazard_id": self.hazard_id,
            "hazard_source_version": self.hazard_source_version,
            "recorded_flags": [
                {"name": name, "value": value} for name, value in self.recorded_flags
            ],
            "overlap_statuses": [
                {"name": name, "value": value} for name, value in self.overlap_statuses
            ],
        }
        if self.projection_id is not None:
            payload["projection_id"] = self.projection_id
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConfirmedEncounterClaim":
        allowed = {
            "kind",
            "tool_call_id",
            "tool_name",
            "encounter_id",
            "aircraft_id",
            "hazard_id",
            "hazard_source_version",
            "projection_id",
            "recorded_flags",
            "overlap_statuses",
        }
        payload = _load(data, frozenset(allowed), LiveOpsClaimKind.CONFIRMED_ENCOUNTER)
        flags = _flag_pairs(
            payload.get("recorded_flags", ()),
            LiveOpsRecordedEncounterFlag,
            "recorded_flags",
        )
        overlaps = _flag_pairs(
            payload.get("overlap_statuses", ()),
            LiveOpsOverlapStatusField,
            "overlap_statuses",
        )
        for _name, value in flags:
            if not isinstance(value, bool):
                raise ContractValidationError("invalid_recorded_flags")
        for _name, value in overlaps:
            _token(value, "overlap_status")
        return cls(
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            encounter_id=_required(payload, "encounter_id"),
            aircraft_id=_required(payload, "aircraft_id"),
            hazard_id=_required(payload, "hazard_id"),
            hazard_source_version=_required(payload, "hazard_source_version"),
            projection_id=_optional_token(payload, "projection_id"),
            recorded_flags=tuple((name, value) for name, value in flags),
            overlap_statuses=tuple((name, value) for name, value in overlaps),
        )


@dataclass(frozen=True)
class ScopedCountClaim:
    tool_call_id: str
    tool_name: str
    metric: LiveOpsCountMetric
    mode: LiveOpsCountMode
    value: int
    scope: LiveOpsExecutedScope

    def __post_init__(self) -> None:
        _token(self.tool_call_id, "tool_call_id")
        _tool_name(self.tool_name, LIVE_OPS_TOOL_NAMES)
        if not isinstance(self.metric, LiveOpsCountMetric):
            raise ContractValidationError("invalid_metric")
        if not isinstance(self.mode, LiveOpsCountMode):
            raise ContractValidationError("invalid_count_mode")
        _count(self.value, "value")
        if not isinstance(self.scope, LiveOpsExecutedScope):
            raise ContractValidationError("invalid_scope")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": LiveOpsClaimKind.SCOPED_COUNT.value,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "metric": self.metric.value,
            "mode": self.mode.value,
            "value": self.value,
            "scope": self.scope.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ScopedCountClaim":
        payload = _load(
            data,
            frozenset({"kind", "tool_call_id", "tool_name", "metric", "mode", "value", "scope"}),
            LiveOpsClaimKind.SCOPED_COUNT,
        )
        return cls(
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            metric=_parse_enum(LiveOpsCountMetric, _required(payload, "metric"), "metric"),
            mode=_parse_enum(LiveOpsCountMode, _required(payload, "mode"), "mode"),
            value=_required(payload, "value"),
            scope=_scope(payload),
        )


@dataclass(frozen=True)
class QueryResultZeroClaim:
    tool_call_id: str
    tool_name: str
    metric: LiveOpsCountMetric
    scope: LiveOpsExecutedScope

    def __post_init__(self) -> None:
        _token(self.tool_call_id, "tool_call_id")
        _tool_name(self.tool_name, LIVE_OPS_TOOL_NAMES)
        if not isinstance(self.metric, LiveOpsCountMetric):
            raise ContractValidationError("invalid_metric")
        if self.metric not in {
            LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
            LiveOpsCountMetric.IMPACT_RECORD_COUNT,
            LiveOpsCountMetric.ENCOUNTER_RECORD_COUNT,
            LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
        }:
            raise ContractValidationError("invalid_zero_metric")
        if not isinstance(self.scope, LiveOpsExecutedScope):
            raise ContractValidationError("invalid_scope")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": LiveOpsClaimKind.QUERY_RESULT_ZERO.value,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "metric": self.metric.value,
            "scope": self.scope.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "QueryResultZeroClaim":
        payload = _load(
            data,
            frozenset({"kind", "tool_call_id", "tool_name", "metric", "scope"}),
            LiveOpsClaimKind.QUERY_RESULT_ZERO,
        )
        return cls(
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            metric=_parse_enum(LiveOpsCountMetric, _required(payload, "metric"), "metric"),
            scope=_scope(payload),
        )


@dataclass(frozen=True)
class ContextRootAbsentClaim:
    tool_call_id: str
    tool_name: str
    entity_id: str

    def __post_init__(self) -> None:
        _token(self.tool_call_id, "tool_call_id")
        _tool_name(
            self.tool_name,
            frozenset(
                {
                    "get_aircraft_operational_context",
                    "get_hazard_operational_context",
                    "get_airport_operational_context",
                }
            ),
        )
        _token(self.entity_id, "entity_id")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": LiveOpsClaimKind.CONTEXT_ROOT_ABSENT.value,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "entity_id": self.entity_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ContextRootAbsentClaim":
        payload = _load(
            data,
            frozenset({"kind", "tool_call_id", "tool_name", "entity_id"}),
            LiveOpsClaimKind.CONTEXT_ROOT_ABSENT,
        )
        return cls(
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            entity_id=_required(payload, "entity_id"),
        )


@dataclass(frozen=True)
class HazardRegionIntersectionClaim:
    tool_call_id: str
    hazard_id: str
    source_version: str
    region_code: str
    region_name: str
    region_query: str

    def __post_init__(self) -> None:
        _token(self.tool_call_id, "tool_call_id")
        for name in ("hazard_id", "source_version", "region_code", "region_name", "region_query"):
            _token(getattr(self, name), name)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": LiveOpsClaimKind.HAZARD_REGION_INTERSECTION.value,
            "tool_call_id": self.tool_call_id,
            "tool_name": "search_current_hazards",
            "hazard_id": self.hazard_id,
            "source_version": self.source_version,
            "region_code": self.region_code,
            "region_name": self.region_name,
            "region_query": self.region_query,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HazardRegionIntersectionClaim":
        payload = _load(
            data,
            frozenset(
                {
                    "kind",
                    "tool_call_id",
                    "tool_name",
                    "hazard_id",
                    "source_version",
                    "region_code",
                    "region_name",
                    "region_query",
                }
            ),
            LiveOpsClaimKind.HAZARD_REGION_INTERSECTION,
        )
        if _required(payload, "tool_name") != "search_current_hazards":
            raise ContractValidationError("invalid_tool_name")
        return cls(
            tool_call_id=_required(payload, "tool_call_id"),
            hazard_id=_required(payload, "hazard_id"),
            source_version=_required(payload, "source_version"),
            region_code=_required(payload, "region_code"),
            region_name=_required(payload, "region_name"),
            region_query=_required(payload, "region_query"),
        )


@dataclass(frozen=True)
class AircraftObservedStateClaim:
    tool_call_id: str
    tool_name: str
    aircraft_id: str
    field: LiveOpsObservedField
    value: JsonValue
    retained_not_current: bool

    def __post_init__(self) -> None:
        _token(self.tool_call_id, "tool_call_id")
        _tool_name(
            self.tool_name,
            frozenset(
                {
                    "get_aircraft_operational_context",
                    "search_current_impacts",
                    "search_current_encounters",
                }
            ),
        )
        _token(self.aircraft_id, "aircraft_id")
        if not isinstance(self.field, LiveOpsObservedField):
            raise ContractValidationError("invalid_field")
        _scalar_value(self.value, "value")
        if not isinstance(self.retained_not_current, bool):
            raise ContractValidationError("invalid_retained_not_current")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": LiveOpsClaimKind.AIRCRAFT_OBSERVED_STATE.value,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "aircraft_id": self.aircraft_id,
            "field": self.field.value,
            "value": self.value,
            "retained_not_current": self.retained_not_current,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AircraftObservedStateClaim":
        payload = _load(
            data,
            frozenset(
                {
                    "kind",
                    "tool_call_id",
                    "tool_name",
                    "aircraft_id",
                    "field",
                    "value",
                    "retained_not_current",
                }
            ),
            LiveOpsClaimKind.AIRCRAFT_OBSERVED_STATE,
        )
        return cls(
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            aircraft_id=_required(payload, "aircraft_id"),
            field=_parse_enum(LiveOpsObservedField, _required(payload, "field"), "field"),
            value=_required(payload, "value"),
            retained_not_current=_required(payload, "retained_not_current"),
        )


@dataclass(frozen=True)
class AircraftProjectionStateClaim:
    tool_call_id: str
    tool_name: str
    aircraft_id: str
    projection_id: str
    field: LiveOpsProjectionField
    value: JsonValue

    def __post_init__(self) -> None:
        _token(self.tool_call_id, "tool_call_id")
        _tool_name(
            self.tool_name,
            frozenset(
                {
                    "get_aircraft_operational_context",
                    "search_current_impacts",
                    "get_hazard_operational_context",
                }
            ),
        )
        _token(self.aircraft_id, "aircraft_id")
        _token(self.projection_id, "projection_id")
        if not isinstance(self.field, LiveOpsProjectionField):
            raise ContractValidationError("invalid_field")
        _scalar_value(self.value, "value")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": LiveOpsClaimKind.AIRCRAFT_PROJECTION_STATE.value,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "aircraft_id": self.aircraft_id,
            "projection_id": self.projection_id,
            "field": self.field.value,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AircraftProjectionStateClaim":
        payload = _load(
            data,
            frozenset(
                {
                    "kind",
                    "tool_call_id",
                    "tool_name",
                    "aircraft_id",
                    "projection_id",
                    "field",
                    "value",
                }
            ),
            LiveOpsClaimKind.AIRCRAFT_PROJECTION_STATE,
        )
        return cls(
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            aircraft_id=_required(payload, "aircraft_id"),
            projection_id=_required(payload, "projection_id"),
            field=_parse_enum(
                LiveOpsProjectionField,
                _required(payload, "field"),
                "field",
            ),
            value=_required(payload, "value"),
        )


@dataclass(frozen=True)
class CurrentnessClaim:
    tool_call_id: str
    tool_name: str
    predicate: LiveOpsCurrentnessPredicate
    value: bool
    aircraft_id: str | None = None
    hazard_id: str | None = None
    source_version: str | None = None
    encounter_id: str | None = None
    airport_id: str | None = None
    projection_id: str | None = None

    def __post_init__(self) -> None:
        _token(self.tool_call_id, "tool_call_id")
        _tool_name(self.tool_name, LIVE_OPS_TOOL_NAMES)
        if not isinstance(self.predicate, LiveOpsCurrentnessPredicate):
            raise ContractValidationError("invalid_predicate")
        if not isinstance(self.value, bool):
            raise ContractValidationError("invalid_value")
        identities = {
            "aircraft_id": self.aircraft_id,
            "hazard_id": self.hazard_id,
            "source_version": self.source_version,
            "encounter_id": self.encounter_id,
            "airport_id": self.airport_id,
            "projection_id": self.projection_id,
        }
        for name, item in identities.items():
            if item is not None:
                _token(item, name)
        required = {
            LiveOpsCurrentnessPredicate.AIRCRAFT_IS_CURRENT: {"aircraft_id"},
            LiveOpsCurrentnessPredicate.HAZARD_IS_CURRENT: {"hazard_id"},
            LiveOpsCurrentnessPredicate.HAZARD_IS_LIFECYCLE_ACTIVE: {"hazard_id"},
            LiveOpsCurrentnessPredicate.PROJECTION_IS_CURRENT: {"aircraft_id"},
            LiveOpsCurrentnessPredicate.ENCOUNTER_IS_CURRENT: {
                "encounter_id",
                "aircraft_id",
                "hazard_id",
            },
            LiveOpsCurrentnessPredicate.AIRPORT_IS_CURRENT: {"airport_id"},
            LiveOpsCurrentnessPredicate.CURRENT_IMPACTS_EVALUATED: {"hazard_id"},
            LiveOpsCurrentnessPredicate.HAZARD_SELECTION_NOT_CURRENT: {
                "hazard_id",
                "source_version",
            },
        }[self.predicate]
        optional = {
            LiveOpsCurrentnessPredicate.HAZARD_IS_CURRENT: {"source_version"},
            LiveOpsCurrentnessPredicate.PROJECTION_IS_CURRENT: {"projection_id"},
        }.get(self.predicate, set())
        present = {name for name, item in identities.items() if item is not None}
        if not required <= present:
            raise ContractValidationError("missing_currentness_identity")
        if present - required - optional:
            raise ContractValidationError("inapplicable_identity_field")

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "kind": LiveOpsClaimKind.CURRENTNESS.value,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "predicate": self.predicate.value,
            "value": self.value,
        }
        for name in (
            "aircraft_id",
            "hazard_id",
            "source_version",
            "encounter_id",
            "airport_id",
            "projection_id",
        ):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CurrentnessClaim":
        payload = _load(
            data,
            frozenset(
                {
                    "kind",
                    "tool_call_id",
                    "tool_name",
                    "predicate",
                    "value",
                    "aircraft_id",
                    "hazard_id",
                    "source_version",
                    "encounter_id",
                    "airport_id",
                    "projection_id",
                }
            ),
            LiveOpsClaimKind.CURRENTNESS,
        )
        return cls(
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            predicate=_parse_enum(
                LiveOpsCurrentnessPredicate,
                _required(payload, "predicate"),
                "predicate",
            ),
            value=_required(payload, "value"),
            aircraft_id=_optional_token(payload, "aircraft_id"),
            hazard_id=_optional_token(payload, "hazard_id"),
            source_version=_optional_token(payload, "source_version"),
            encounter_id=_optional_token(payload, "encounter_id"),
            airport_id=_optional_token(payload, "airport_id"),
            projection_id=_optional_token(payload, "projection_id"),
        )


@dataclass(frozen=True)
class RiskStateClaim:
    tool_call_id: str
    tool_name: str
    mode: LiveOpsRiskMode
    encounter_id: str
    aircraft_id: str
    hazard_id: str
    risk_id: str | None = None
    risk_level: str | None = None
    risk_score: int | None = None

    def __post_init__(self) -> None:
        _token(self.tool_call_id, "tool_call_id")
        _tool_name(
            self.tool_name,
            frozenset(
                {
                    "search_current_impacts",
                    "search_current_encounters",
                    "get_aircraft_operational_context",
                    "get_hazard_operational_context",
                }
            ),
        )
        if not isinstance(self.mode, LiveOpsRiskMode):
            raise ContractValidationError("invalid_risk_mode")
        for name in ("encounter_id", "aircraft_id", "hazard_id"):
            _token(getattr(self, name), name)
        if self.mode is LiveOpsRiskMode.PRESENT:
            _token(self.risk_id, "risk_id")
            _token(self.risk_level, "risk_level")
            _count(self.risk_score, "risk_score")
        elif any(item is not None for item in (self.risk_id, self.risk_level, self.risk_score)):
            raise ContractValidationError("absent_risk_forbids_risk_fields")

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "kind": LiveOpsClaimKind.RISK_STATE.value,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "mode": self.mode.value,
            "encounter_id": self.encounter_id,
            "aircraft_id": self.aircraft_id,
            "hazard_id": self.hazard_id,
        }
        if self.mode is LiveOpsRiskMode.PRESENT:
            payload["risk_id"] = self.risk_id
            payload["risk_level"] = self.risk_level
            payload["risk_score"] = self.risk_score
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RiskStateClaim":
        payload = _load(
            data,
            frozenset(
                {
                    "kind",
                    "tool_call_id",
                    "tool_name",
                    "mode",
                    "encounter_id",
                    "aircraft_id",
                    "hazard_id",
                    "risk_id",
                    "risk_level",
                    "risk_score",
                }
            ),
            LiveOpsClaimKind.RISK_STATE,
        )
        return cls(
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            mode=_parse_enum(LiveOpsRiskMode, _required(payload, "mode"), "mode"),
            encounter_id=_required(payload, "encounter_id"),
            aircraft_id=_required(payload, "aircraft_id"),
            hazard_id=_required(payload, "hazard_id"),
            risk_id=_optional_token(payload, "risk_id"),
            risk_level=_optional_token(payload, "risk_level"),
            risk_score=payload["risk_score"] if "risk_score" in payload else None,
        )


@dataclass(frozen=True)
class RecommendationSetClaim:
    tool_call_id: str
    tool_name: str
    mode: LiveOpsAdvisoryMode
    encounter_id: str
    aircraft_id: str
    hazard_id: str
    recommendation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _token(self.tool_call_id, "tool_call_id")
        _tool_name(
            self.tool_name,
            frozenset(
                {
                    "search_current_impacts",
                    "search_current_encounters",
                    "get_aircraft_operational_context",
                    "get_hazard_operational_context",
                }
            ),
        )
        if not isinstance(self.mode, LiveOpsAdvisoryMode):
            raise ContractValidationError("invalid_advisory_mode")
        for name in ("encounter_id", "aircraft_id", "hazard_id"):
            _token(getattr(self, name), name)
        ids = _id_tuple(self.recommendation_ids, "recommendation_ids")
        object.__setattr__(self, "recommendation_ids", ids)
        if self.mode is LiveOpsAdvisoryMode.PRESENT_SET and not ids:
            raise ContractValidationError("present_set_requires_ids")
        if self.mode is LiveOpsAdvisoryMode.ABSENT_FROM_OBSERVED_CANDIDATES and ids:
            raise ContractValidationError("absent_set_forbids_ids")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": LiveOpsClaimKind.RECOMMENDATION_SET.value,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "mode": self.mode.value,
            "encounter_id": self.encounter_id,
            "aircraft_id": self.aircraft_id,
            "hazard_id": self.hazard_id,
            "recommendation_ids": list(self.recommendation_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RecommendationSetClaim":
        payload = _load(
            data,
            frozenset(
                {
                    "kind",
                    "tool_call_id",
                    "tool_name",
                    "mode",
                    "encounter_id",
                    "aircraft_id",
                    "hazard_id",
                    "recommendation_ids",
                }
            ),
            LiveOpsClaimKind.RECOMMENDATION_SET,
        )
        return cls(
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            mode=_parse_enum(LiveOpsAdvisoryMode, _required(payload, "mode"), "mode"),
            encounter_id=_required(payload, "encounter_id"),
            aircraft_id=_required(payload, "aircraft_id"),
            hazard_id=_required(payload, "hazard_id"),
            recommendation_ids=_parse_id_tuple(
                payload.get("recommendation_ids", ()),
                "recommendation_ids",
            ),
        )


@dataclass(frozen=True)
class AlertRecordClaim:
    tool_call_id: str
    tool_name: str
    mode: LiveOpsAdvisoryMode
    encounter_id: str
    aircraft_id: str
    hazard_id: str
    alert_id: str | None = None
    alert_state: str | None = None
    alert_type: str | None = None

    def __post_init__(self) -> None:
        _token(self.tool_call_id, "tool_call_id")
        _tool_name(
            self.tool_name,
            frozenset(
                {
                    "search_current_impacts",
                    "search_current_encounters",
                    "get_aircraft_operational_context",
                    "get_hazard_operational_context",
                }
            ),
        )
        if not isinstance(self.mode, LiveOpsAdvisoryMode):
            raise ContractValidationError("invalid_advisory_mode")
        for name in ("encounter_id", "aircraft_id", "hazard_id"):
            _token(getattr(self, name), name)
        if self.mode is LiveOpsAdvisoryMode.PRESENT_SET:
            _token(self.alert_id, "alert_id")
            _token(self.alert_state, "alert_state")
            if self.alert_type is not None:
                _token(self.alert_type, "alert_type")
        elif any(item is not None for item in (self.alert_id, self.alert_state, self.alert_type)):
            raise ContractValidationError("absent_alert_forbids_alert_fields")

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "kind": LiveOpsClaimKind.ALERT_RECORD.value,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "mode": self.mode.value,
            "encounter_id": self.encounter_id,
            "aircraft_id": self.aircraft_id,
            "hazard_id": self.hazard_id,
        }
        if self.mode is LiveOpsAdvisoryMode.PRESENT_SET:
            payload["alert_id"] = self.alert_id
            payload["alert_state"] = self.alert_state
            if self.alert_type is not None:
                payload["alert_type"] = self.alert_type
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AlertRecordClaim":
        payload = _load(
            data,
            frozenset(
                {
                    "kind",
                    "tool_call_id",
                    "tool_name",
                    "mode",
                    "encounter_id",
                    "aircraft_id",
                    "hazard_id",
                    "alert_id",
                    "alert_state",
                    "alert_type",
                }
            ),
            LiveOpsClaimKind.ALERT_RECORD,
        )
        return cls(
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            mode=_parse_enum(LiveOpsAdvisoryMode, _required(payload, "mode"), "mode"),
            encounter_id=_required(payload, "encounter_id"),
            aircraft_id=_required(payload, "aircraft_id"),
            hazard_id=_required(payload, "hazard_id"),
            alert_id=_optional_token(payload, "alert_id"),
            alert_state=_optional_token(payload, "alert_state"),
            alert_type=_optional_token(payload, "alert_type"),
        )


@dataclass(frozen=True)
class CallsignMatchSetClaim:
    tool_call_id: str
    callsign: str
    aircraft_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _token(self.tool_call_id, "tool_call_id")
        _token(self.callsign, "callsign")
        object.__setattr__(self, "aircraft_ids", _id_tuple(self.aircraft_ids, "aircraft_ids"))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": LiveOpsClaimKind.CALLSIGN_MATCH_SET.value,
            "tool_call_id": self.tool_call_id,
            "tool_name": "find_aircraft_by_callsign",
            "callsign": self.callsign,
            "aircraft_ids": list(self.aircraft_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CallsignMatchSetClaim":
        payload = _load(
            data,
            frozenset({"kind", "tool_call_id", "tool_name", "callsign", "aircraft_ids"}),
            LiveOpsClaimKind.CALLSIGN_MATCH_SET,
        )
        if _required(payload, "tool_name") != "find_aircraft_by_callsign":
            raise ContractValidationError("invalid_tool_name")
        return cls(
            tool_call_id=_required(payload, "tool_call_id"),
            callsign=_required(payload, "callsign"),
            aircraft_ids=_parse_id_tuple(_required(payload, "aircraft_ids"), "aircraft_ids"),
        )


@dataclass(frozen=True)
class CoverageLimitationClaim:
    tool_call_id: str
    tool_name: str
    code: LiveOpsLimitationCode

    def __post_init__(self) -> None:
        _token(self.tool_call_id, "tool_call_id")
        _tool_name(self.tool_name, LIVE_OPS_TOOL_NAMES)
        if not isinstance(self.code, LiveOpsLimitationCode):
            raise ContractValidationError("invalid_limitation_code")
        if self.code is LiveOpsLimitationCode.RECORDED_OVERLAP_NOT_PHYSICAL_ENTRY:
            raise ContractValidationError("not_a_source_limitation")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": LiveOpsClaimKind.COVERAGE_LIMITATION.value,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "code": self.code.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CoverageLimitationClaim":
        payload = _load(
            data,
            frozenset({"kind", "tool_call_id", "tool_name", "code"}),
            LiveOpsClaimKind.COVERAGE_LIMITATION,
        )
        return cls(
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            code=_parse_enum(LiveOpsLimitationCode, _required(payload, "code"), "code"),
        )


@dataclass(frozen=True)
class ModelVisibleTruncationClaim:
    tool_call_id: str
    tool_name: str
    list_name: str
    total_count: int

    def __post_init__(self) -> None:
        _token(self.tool_call_id, "tool_call_id")
        _tool_name(self.tool_name, LIVE_OPS_TOOL_NAMES)
        if self.list_name not in _VISIBLE_LIST_NAMES:
            raise ContractValidationError("invalid_list_name")
        _count(self.total_count, "total_count")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": LiveOpsClaimKind.MODEL_VISIBLE_TRUNCATION.value,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "list_name": self.list_name,
            "total_count": self.total_count,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ModelVisibleTruncationClaim":
        payload = _load(
            data,
            frozenset({"kind", "tool_call_id", "tool_name", "list_name", "total_count"}),
            LiveOpsClaimKind.MODEL_VISIBLE_TRUNCATION,
        )
        return cls(
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            list_name=_required(payload, "list_name"),
            total_count=_required(payload, "total_count"),
        )


@dataclass(frozen=True)
class CrossToolContradictionClaim:
    tool_call_id_a: str
    tool_call_id_b: str
    subject: LiveOpsContradictionSubjectKind
    hazard_id: str | None = None
    aircraft_id: str | None = None
    encounter_id: str | None = None

    def __post_init__(self) -> None:
        _token(self.tool_call_id_a, "tool_call_id_a")
        _token(self.tool_call_id_b, "tool_call_id_b")
        if self.tool_call_id_a == self.tool_call_id_b:
            raise ContractValidationError("contradiction_requires_two_calls")
        if not isinstance(self.subject, LiveOpsContradictionSubjectKind):
            raise ContractValidationError("invalid_contradiction_subject")
        identities = {
            "hazard_id": self.hazard_id,
            "aircraft_id": self.aircraft_id,
            "encounter_id": self.encounter_id,
        }
        for name, value in identities.items():
            if value is not None:
                _token(value, name)
        required = {
            LiveOpsContradictionSubjectKind.HAZARD_SOURCE_VERSION: "hazard_id",
            LiveOpsContradictionSubjectKind.AIRCRAFT_CURRENTNESS: "aircraft_id",
            LiveOpsContradictionSubjectKind.ENCOUNTER_LINEAGE: "encounter_id",
        }[self.subject]
        if identities[required] is None:
            raise ContractValidationError("missing_contradiction_identity")
        if any(name != required and value is not None for name, value in identities.items()):
            raise ContractValidationError("inapplicable_identity_field")

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "kind": LiveOpsClaimKind.CROSS_TOOL_CONTRADICTION.value,
            "tool_call_id_a": self.tool_call_id_a,
            "tool_call_id_b": self.tool_call_id_b,
            "subject": self.subject.value,
        }
        for name in ("hazard_id", "aircraft_id", "encounter_id"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CrossToolContradictionClaim":
        payload = _load(
            data,
            frozenset(
                {
                    "kind",
                    "tool_call_id_a",
                    "tool_call_id_b",
                    "subject",
                    "hazard_id",
                    "aircraft_id",
                    "encounter_id",
                }
            ),
            LiveOpsClaimKind.CROSS_TOOL_CONTRADICTION,
        )
        return cls(
            tool_call_id_a=_required(payload, "tool_call_id_a"),
            tool_call_id_b=_required(payload, "tool_call_id_b"),
            subject=_parse_enum(
                LiveOpsContradictionSubjectKind,
                _required(payload, "subject"),
                "subject",
            ),
            hazard_id=_optional_token(payload, "hazard_id"),
            aircraft_id=_optional_token(payload, "aircraft_id"),
            encounter_id=_optional_token(payload, "encounter_id"),
        )


LiveOpsClaim = (
    ConfirmedImpactClaim
    | ConfirmedEncounterClaim
    | ScopedCountClaim
    | QueryResultZeroClaim
    | ContextRootAbsentClaim
    | HazardRegionIntersectionClaim
    | AircraftObservedStateClaim
    | AircraftProjectionStateClaim
    | CurrentnessClaim
    | RiskStateClaim
    | RecommendationSetClaim
    | AlertRecordClaim
    | CallsignMatchSetClaim
    | CoverageLimitationClaim
    | ModelVisibleTruncationClaim
    | CrossToolContradictionClaim
)

_CLAIM_TYPES: dict[str, type] = {
    LiveOpsClaimKind.CONFIRMED_IMPACT.value: ConfirmedImpactClaim,
    LiveOpsClaimKind.CONFIRMED_ENCOUNTER.value: ConfirmedEncounterClaim,
    LiveOpsClaimKind.SCOPED_COUNT.value: ScopedCountClaim,
    LiveOpsClaimKind.QUERY_RESULT_ZERO.value: QueryResultZeroClaim,
    LiveOpsClaimKind.CONTEXT_ROOT_ABSENT.value: ContextRootAbsentClaim,
    LiveOpsClaimKind.HAZARD_REGION_INTERSECTION.value: HazardRegionIntersectionClaim,
    LiveOpsClaimKind.AIRCRAFT_OBSERVED_STATE.value: AircraftObservedStateClaim,
    LiveOpsClaimKind.AIRCRAFT_PROJECTION_STATE.value: AircraftProjectionStateClaim,
    LiveOpsClaimKind.CURRENTNESS.value: CurrentnessClaim,
    LiveOpsClaimKind.RISK_STATE.value: RiskStateClaim,
    LiveOpsClaimKind.RECOMMENDATION_SET.value: RecommendationSetClaim,
    LiveOpsClaimKind.ALERT_RECORD.value: AlertRecordClaim,
    LiveOpsClaimKind.CALLSIGN_MATCH_SET.value: CallsignMatchSetClaim,
    LiveOpsClaimKind.COVERAGE_LIMITATION.value: CoverageLimitationClaim,
    LiveOpsClaimKind.MODEL_VISIBLE_TRUNCATION.value: ModelVisibleTruncationClaim,
    LiveOpsClaimKind.CROSS_TOOL_CONTRADICTION.value: CrossToolContradictionClaim,
}


def is_live_ops_claim(value: object) -> bool:
    return isinstance(value, tuple(_CLAIM_TYPES.values()))


def live_ops_claim_from_dict(data: Mapping[str, Any]) -> LiveOpsClaim:
    if not isinstance(data, Mapping):
        raise ContractValidationError("invalid_live_ops_claim")
    kind = data.get("kind")
    claim_type = _CLAIM_TYPES.get(kind)
    if claim_type is None:
        raise ContractValidationError("invalid_kind")
    return claim_type.from_dict(data)


__all__ = [
    "LIVE_OPS_IDENTITY_SET_MAX",
    "LIVE_OPS_MAX_CLAIMS_PER_RESPONSE",
    "LIVE_OPS_TOOL_NAMES",
    "AircraftObservedStateClaim",
    "AircraftProjectionStateClaim",
    "AlertRecordClaim",
    "CallsignMatchSetClaim",
    "ConfirmedEncounterClaim",
    "ConfirmedImpactClaim",
    "ContextRootAbsentClaim",
    "CoverageLimitationClaim",
    "CrossToolContradictionClaim",
    "CurrentnessClaim",
    "HazardRegionIntersectionClaim",
    "LiveOpsAdvisoryMode",
    "LiveOpsClaim",
    "LiveOpsClaimKind",
    "LiveOpsContradictionSubjectKind",
    "LiveOpsCountMetric",
    "LiveOpsCountMode",
    "LiveOpsCurrentnessPredicate",
    "LiveOpsExecutedScope",
    "LiveOpsLimitationCode",
    "LiveOpsObservedField",
    "LiveOpsOverlapStatusField",
    "LiveOpsProjectionField",
    "LiveOpsRecordedEncounterFlag",
    "LiveOpsRiskMode",
    "ModelVisibleTruncationClaim",
    "QueryResultZeroClaim",
    "RecommendationSetClaim",
    "RiskStateClaim",
    "ScopedCountClaim",
    "is_live_ops_claim",
    "live_ops_claim_from_dict",
]
