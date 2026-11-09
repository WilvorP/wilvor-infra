"""CURRENT-scope model-facing projection of Live Ops ToolResults.

The raw ToolResult remains the audit and verifier authority. This module
builds an explicit allowlist. It does not decide currentness, geography, or
source-version authority.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Mapping

from wilvor_ai.contracts import (
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    JsonValue,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
    _is_json_value,
    _parse_enum,
    _required,
    _sequence,
    _validate_string_tuple,
    _validate_text,
    _validate_utc,
)
from wilvor_ai.live_ops_model_contracts import (
    LIVE_OPS_MODEL_VISIBLE_LIST_MAX,
    LIVE_OPS_PROJECTED_STRING_MAX,
    LIVE_OPS_PROJECTION_JSON_MAX_BYTES,
)


class LiveOpsProjectionError(ContractValidationError):
    """Raised when a ToolResult cannot be safely projected for a model."""


_AIRCRAFT_KEYS = (
    "aircraft_id",
    "callsign",
    "latitude",
    "longitude",
    "baro_altitude_ft",
    "geo_altitude_ft",
    "ground_speed_kt",
    "track_deg",
    "on_ground",
    "position_time_utc",
    "current_h3_cell",
    "freshness_status",
    "has_position",
)
_PROJECTION_KEYS = (
    "projection_id",
    "aircraft_id",
    "generated_at_utc",
    "valid_until_utc",
    "projection_horizon_min",
    "projection_status",
    "current_aircraft_h3_cell",
    "trigger_hazard_ids",
    "projection_trigger_reason",
    "confidence",
)
_HAZARD_KEYS = (
    "hazard_id",
    "source_version",
    "product_type",
    "hazard_type",
    "status",
    "materialization_status",
    "valid_from_utc",
    "valid_to_utc",
    "severity",
)
_ENCOUNTER_KEYS = (
    "encounter_id",
    "aircraft_id",
    "projection_id",
    "hazard_id",
    "hazard_source_version",
    "encounter_state",
    "geometry_overlap_status",
    "time_overlap_status",
    "altitude_overlap_status",
    "corridor_intersects",
    "centerline_intersects",
    "inside_now",
    "exact_intersection_confirmed",
)
_RISK_KEYS = (
    "risk_id",
    "encounter_id",
    "aircraft_id",
    "hazard_id",
    "hazard_source_version",
    "projection_id",
    "risk_level",
    "risk_score",
    "confidence",
    "limitations",
    "valid_until_utc",
)
_RECOMMENDATION_KEYS = (
    "recommendation_id",
    "recommendation_status",
    "primary_action_type",
    "risk_id",
    "limitations",
)
_ALERT_KEYS = (
    "alert_id",
    "aircraft_id",
    "hazard_id",
    "hazard_source_version",
    "recommendation_id",
    "risk_id",
    "risk_level",
    "alert_type",
    "alert_state",
    "primary_action_type",
    "valid_until_utc",
)
_AIRPORT_KEYS = (
    "airport_id",
    "station_id",
    "iata_code",
    "has_metar",
    "has_taf",
    "metar_freshness_status",
    "taf_freshness_status",
    "observed_time_utc",
    "flight_category",
    "weather_risk_level",
)
_METAR_KEYS = (
    "station_id",
    "airport_id",
    "metar_version",
    "observed_time_utc",
    "freshness_status",
)
_TAF_KEYS = (
    "station_id",
    "airport_id",
    "taf_version_key",
    "source_version",
    "issued_at_utc",
    "freshness_status",
)
_LINK_KEYS = (
    "state",
    "kind",
    "reason",
    "selected_identity",
    "observed_identity",
)
_REGION_KEYS = (
    "resolved",
    "query",
    "region_type",
    "code",
    "name",
    "dataset_product",
    "dataset_vintage",
    "spatial_supported",
    "geometry_type",
)
_RETRIEVAL_KEYS = (
    "source",
    "coverage",
    "consistency",
    "limit",
    "limitations",
)
_LINK_IDENTITY_KEYS = frozenset(
    {
        "aircraft_id",
        "hazard_id",
        "source_version",
        "encounter_id",
        "projection_id",
        "airport_id",
        "station_id",
        "risk_id",
        "recommendation_id",
        "alert_id",
        "taf_version_key",
        "metar_version",
        "source_taf_version",
        "updated_at_epoch",
    }
)
_TEXT_CLIPPED = "TEXT_CLIPPED"
_FORBIDDEN_RECOMMENDATION_KEYS = frozenset(
    {
        "preferred_airport_id",
        "preferred_airport_assessment_id",
        "preferred_airport_score",
        "alternative_actions",
        "advisory_notice",
        "primary_action_details",
    }
)
_FREE_TEXT_KEYS = frozenset({"reason", "projection_trigger_reason", "state_reason"})
_TOP_LEVEL_LISTS = (
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
)


@dataclass(frozen=True)
class LiveOpsProjectedEvidence:
    """Evidence facts a model may see. Query traces stay on the raw result."""

    source: str
    temporal_scope: TemporalScope
    freshness_status: FreshnessStatus
    limitations: tuple[str, ...]
    source_records: tuple[SourceRecord, ...] = ()
    confidence: ConfidenceLevel | None = None

    def __post_init__(self) -> None:
        errors = _validate_text(self.source, "source")
        errors.extend(_validate_string_tuple(self.limitations, "limitations"))
        if not isinstance(self.temporal_scope, TemporalScope):
            errors.append("invalid_temporal_scope")
        if not isinstance(self.freshness_status, FreshnessStatus):
            errors.append("invalid_freshness_status")
        if not isinstance(self.source_records, tuple) or any(
            not isinstance(item, SourceRecord) for item in self.source_records
        ):
            errors.append("invalid_source_records")
        if self.confidence is not None and not isinstance(
            self.confidence,
            ConfidenceLevel,
        ):
            errors.append("invalid_confidence")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "source": self.source,
            "temporal_scope": self.temporal_scope.value,
            "freshness_status": self.freshness_status.value,
            "limitations": list(self.limitations),
            "source_records": [item.to_dict() for item in self.source_records],
        }
        if self.confidence is not None:
            payload["confidence"] = self.confidence.value
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsProjectedEvidence":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_live_ops_projected_evidence")
        confidence = data["confidence"] if "confidence" in data else None
        return cls(
            source=_required(data, "source"),
            temporal_scope=_parse_enum(
                TemporalScope,
                _required(data, "temporal_scope"),
                "temporal_scope",
            ),
            freshness_status=_parse_enum(
                FreshnessStatus,
                _required(data, "freshness_status"),
                "freshness_status",
            ),
            limitations=tuple(_sequence(_required(data, "limitations"), "limitations")),
            source_records=tuple(
                SourceRecord.from_dict(item)
                for item in _sequence(
                    data.get("source_records", ()),
                    "source_records",
                )
            ),
            confidence=(
                None
                if confidence is None
                else _parse_enum(ConfidenceLevel, confidence, "confidence")
            ),
        )


@dataclass(frozen=True)
class LiveOpsToolProjection:
    """Safe CURRENT-scope copy of one Live Ops ToolResult."""

    tool_name: str
    tool_call_id: str
    status: ToolResultStatus
    temporal_scope: TemporalScope
    as_of_utc: str | None
    data: JsonValue
    limitations: tuple[str, ...]
    evidence: tuple[LiveOpsProjectedEvidence, ...]
    as_of_epoch: int | None = None

    def __post_init__(self) -> None:
        errors = _validate_text(self.tool_name, "tool_name")
        errors.extend(_validate_text(self.tool_call_id, "tool_call_id"))
        errors.extend(_validate_utc(self.as_of_utc, "as_of_utc", optional=True))
        errors.extend(_validate_string_tuple(self.limitations, "limitations"))
        if not isinstance(self.status, ToolResultStatus):
            errors.append("invalid_status")
        if self.temporal_scope is not TemporalScope.CURRENT:
            errors.append("projection_requires_current_scope")
        if not isinstance(self.evidence, tuple) or any(
            not isinstance(item, LiveOpsProjectedEvidence) for item in self.evidence
        ):
            errors.append("invalid_evidence")
        if self.as_of_epoch is not None and type(self.as_of_epoch) is not int:
            errors.append("invalid_as_of_epoch")
        if not isinstance(self.data, dict) or not _is_json_value(self.data):
            errors.append("invalid_data")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "status": self.status.value,
            "temporal_scope": self.temporal_scope.value,
            "as_of_utc": self.as_of_utc,
            "as_of_epoch": self.as_of_epoch,
            "data": copy.deepcopy(self.data),
            "limitations": list(self.limitations),
            "evidence": [item.to_dict() for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsToolProjection":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_live_ops_tool_projection")
        return cls(
            tool_name=_required(data, "tool_name"),
            tool_call_id=_required(data, "tool_call_id"),
            status=_parse_enum(ToolResultStatus, _required(data, "status"), "status"),
            temporal_scope=_parse_enum(
                TemporalScope,
                _required(data, "temporal_scope"),
                "temporal_scope",
            ),
            as_of_utc=data["as_of_utc"] if "as_of_utc" in data else None,
            as_of_epoch=data["as_of_epoch"] if "as_of_epoch" in data else None,
            data=copy.deepcopy(_required(data, "data")),
            limitations=tuple(_sequence(_required(data, "limitations"), "limitations")),
            evidence=tuple(
                LiveOpsProjectedEvidence.from_dict(item)
                for item in _sequence(_required(data, "evidence"), "evidence")
            ),
        )


def project_live_ops_tool_result(result: ToolResult) -> LiveOpsToolProjection:
    """Derive a bounded CURRENT view without mutating the audit ToolResult."""

    if not isinstance(result, ToolResult):
        raise TypeError("result must be a ToolResult")
    if result.temporal_scope is not TemporalScope.CURRENT:
        raise LiveOpsProjectionError(("projection_requires_current_scope",))

    raw_data = result.data if isinstance(result.data, dict) else {}
    allowlisted = _project_application_data(
        result.tool_name,
        raw_data,
        result.status,
    )
    evidence = tuple(_project_evidence(item) for item in result.evidence)
    projection = _fit_projection(
        result,
        allowlisted,
        evidence,
        max_visible=LIVE_OPS_MODEL_VISIBLE_LIST_MAX,
    )
    if projection is None:
        raise LiveOpsProjectionError(("projection_mandatory_metadata_overflow",))
    return projection


def _fit_projection(
    result: ToolResult,
    allowlisted: dict[str, JsonValue],
    evidence: tuple[LiveOpsProjectedEvidence, ...],
    *,
    max_visible: int,
) -> LiveOpsToolProjection | None:
    for visible in range(max_visible, -1, -1):
        data = _apply_list_bounds(allowlisted, visible)
        candidate = LiveOpsToolProjection(
            tool_name=result.tool_name,
            tool_call_id=result.tool_call_id,
            status=result.status,
            temporal_scope=result.temporal_scope,
            as_of_utc=result.as_of_utc,
            as_of_epoch=_as_int(data.get("as_of_epoch")),
            data=data,
            limitations=tuple(result.limitations),
            evidence=evidence,
        )
        encoded = json.dumps(
            candidate.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        if len(encoded.encode("utf-8")) <= LIVE_OPS_PROJECTION_JSON_MAX_BYTES:
            return candidate
        if visible == 0:
            return None
    return None


def _apply_list_bounds(
    data: Mapping[str, JsonValue],
    max_visible: int,
) -> dict[str, JsonValue]:
    bounded = copy.deepcopy(dict(data))
    for name in _TOP_LEVEL_LISTS:
        if name not in bounded:
            continue
        values = bounded[name]
        if not isinstance(values, list):
            continue
        total = len(values)
        visible = values[:max_visible]
        bounded[name] = visible
        bounded[f"{name}_total_count"] = total
        bounded[f"{name}_visible_count"] = len(visible)
        bounded[f"{name}_omitted_count"] = total - len(visible)
        bounded[f"{name}_truncated"] = total > len(visible)
    return bounded


def _project_application_data(
    tool_name: str,
    data: Mapping[str, Any],
    status: ToolResultStatus,
) -> dict[str, JsonValue]:
    if tool_name == "search_current_hazards":
        return _project_hazard_search(data)
    if tool_name == "search_current_impacts":
        return _project_impact_search(data)
    if tool_name == "search_current_encounters":
        return _project_encounter_search(data)
    if tool_name == "get_observed_network_state":
        return _project_network_state(data)
    if tool_name == "get_aircraft_operational_context":
        return _project_aircraft_context(data, status)
    if tool_name == "get_hazard_operational_context":
        return _project_hazard_context(data, status)
    if tool_name == "get_airport_operational_context":
        return _project_airport_context(data, status)
    if tool_name == "find_aircraft_by_callsign":
        return _project_callsign_search(data)
    raise LiveOpsProjectionError(("unknown_live_ops_tool",))


def _project_hazard_search(data: Mapping[str, Any]) -> dict[str, JsonValue]:
    hazards = [_project_hazard_match(item) for item in _as_list(data.get("hazards"))]
    payload = _common_search_fields(data)
    payload.update(
        {
            "match_count": _authoritative_count(data, "match_count", hazards),
            "unique_hazard_count": _unique_ids(hazards, "hazard", "hazard_id"),
            "hazards": hazards,
            "geospatial_unevaluated": [
                _project_geo_unevaluated(item)
                for item in _as_list(data.get("geospatial_unevaluated"))
            ],
            "rejected_hazard_candidates": [
                _project_rejected(item)
                for item in _as_list(data.get("rejected_hazard_candidates"))
            ],
            "rejected_hazard_selections": [
                _project_rejected(item)
                for item in _as_list(data.get("rejected_hazard_selections"))
            ],
            "non_intersecting_count": _as_int(data.get("non_intersecting_count")) or 0,
            "geospatial_unevaluated_count": len(
                _as_list(data.get("geospatial_unevaluated"))
            ),
        }
    )
    return payload


def _project_impact_search(data: Mapping[str, Any]) -> dict[str, JsonValue]:
    impacts = [_project_impact(item) for item in _as_list(data.get("impacts"))]
    payload = _common_search_fields(data)
    payload.update(
        {
            "impact_count": _authoritative_count(data, "impact_count", impacts),
            "unique_aircraft_count": _as_int(data.get("unique_aircraft_count"))
            if data.get("unique_aircraft_count") is not None
            else len({item.get("aircraft_id") for item in impacts if item.get("aircraft_id")}),
            "unique_hazard_count": _unique_ids(impacts, None, "hazard_id"),
            "unique_aircraft_ids": [
                item for item in _as_list(data.get("unique_aircraft_ids")) if isinstance(item, str)
            ],
            "impacts": impacts,
            "operationally_unevaluated": [
                _project_unevaluated(item)
                for item in _as_list(data.get("operationally_unevaluated"))
            ],
            "geospatial_unevaluated": [
                _project_geo_unevaluated(item)
                for item in _as_list(data.get("geospatial_unevaluated"))
            ],
            "rejected_hazard_candidates": [
                _project_rejected(item)
                for item in _as_list(data.get("rejected_hazard_candidates"))
            ],
            "rejected_hazard_selections": [
                _project_rejected(item)
                for item in _as_list(data.get("rejected_hazard_selections"))
            ],
            "operationally_unevaluated_count": len(
                _as_list(data.get("operationally_unevaluated"))
            ),
            "geospatial_unevaluated_count": len(
                _as_list(data.get("geospatial_unevaluated"))
            ),
        }
    )
    return payload


def _project_encounter_search(data: Mapping[str, Any]) -> dict[str, JsonValue]:
    encounters = [
        _project_encounter_record(item) for item in _as_list(data.get("encounters"))
    ]
    matches = [
        item for item in _as_list(data.get("callsign_matches")) if isinstance(item, str)
    ]
    return {
        "as_of_epoch": _as_int(data.get("as_of_epoch")),
        "as_of_utc": data.get("as_of_utc"),
        "aircraft_id": data.get("aircraft_id"),
        "callsign": data.get("callsign"),
        "hazard_id": data.get("hazard_id"),
        "hazard_ids": copy.deepcopy(data.get("hazard_ids")),
        "callsign_matches": matches,
        "callsign_match_count": len(matches),
        "encounter_count": _authoritative_count(data, "encounter_count", encounters),
        "unique_aircraft_count": len(
            {item.get("aircraft_id") for item in encounters if item.get("aircraft_id")}
        ),
        "unique_hazard_count": _unique_ids(encounters, None, "hazard_id"),
        "encounters": encounters,
        "operationally_unevaluated": [
            _project_unevaluated(item)
            for item in _as_list(data.get("operationally_unevaluated"))
        ],
        "rejected_hazard_selections": [
            _project_rejected(item)
            for item in _as_list(data.get("rejected_hazard_selections"))
        ],
        "operationally_unevaluated_count": len(
            _as_list(data.get("operationally_unevaluated"))
        ),
        "retrieval": [_project_retrieval(item) for item in _as_list(data.get("retrieval"))],
    }


def _project_network_state(data: Mapping[str, Any]) -> dict[str, JsonValue]:
    return {
        "as_of_epoch": _as_int(data.get("as_of_epoch")),
        "as_of_utc": data.get("as_of_utc"),
        "observed": True,
        "complete_network_snapshot": False,
        "current_aircraft_count": _as_int(data.get("current_aircraft_count")) or 0,
        "current_hazard_count": _as_int(data.get("current_hazard_count")) or 0,
        "current_encounter_count": _as_int(data.get("current_encounter_count")) or 0,
        "current_risk_count": _as_int(data.get("current_risk_count")) or 0,
        "current_recommendation_count": _as_int(data.get("current_recommendation_count"))
        or 0,
        "current_alert_count": _as_int(data.get("current_alert_count")) or 0,
        "current_aircraft_ids": _string_list(data.get("current_aircraft_ids")),
        "current_hazard_ids": _string_list(data.get("current_hazard_ids")),
        "current_encounter_ids": _string_list(data.get("current_encounter_ids")),
        "current_risk_ids": _string_list(data.get("current_risk_ids")),
        "current_recommendation_ids": _string_list(data.get("current_recommendation_ids")),
        "current_alert_ids": _string_list(data.get("current_alert_ids")),
        "retrieval": [_project_retrieval(item) for item in _as_list(data.get("retrieval"))],
    }


def _project_aircraft_context(
    data: Mapping[str, Any],
    status: ToolResultStatus,
) -> dict[str, JsonValue]:
    encounters = [
        _project_encounter_context(item) for item in _as_list(data.get("encounters"))
    ]
    return {
        "as_of_epoch": _as_int(data.get("as_of_epoch")),
        "as_of_utc": data.get("as_of_utc"),
        "found": _required_found(data, status),
        "aircraft_id": data.get("aircraft_id"),
        "aircraft_is_current": bool(data.get("aircraft_is_current")),
        "aircraft": _pick(data.get("aircraft"), _AIRCRAFT_KEYS),
        "projection_is_current": bool(data.get("projection_is_current")),
        "projection": _pick(data.get("projection"), _PROJECTION_KEYS),
        "projection_link": _project_link(data.get("projection_link")),
        "encounters": encounters,
        "unique_hazard_count": _unique_ids(encounters, "encounter", "hazard_id")
        if encounters
        else 0,
        "retrieval": [_project_retrieval(item) for item in _as_list(data.get("retrieval"))],
    }


def _project_hazard_context(
    data: Mapping[str, Any],
    status: ToolResultStatus,
) -> dict[str, JsonValue]:
    # Model-visible name matches the mapping field. It is not inferred from
    # hazard.status and is distinct from hazard_is_current.
    impacts = [_project_impact(item) for item in _as_list(data.get("impacts"))]
    return {
        "as_of_epoch": _as_int(data.get("as_of_epoch")),
        "as_of_utc": data.get("as_of_utc"),
        "found": _required_found(data, status),
        "hazard_id": data.get("hazard_id")
        if "hazard_id" in data
        else ((data.get("hazard") or {}) or {}).get("hazard_id"),
        "hazard_is_current": bool(data.get("hazard_is_current")),
        "hazard_is_lifecycle_active": _require_bool(data, "hazard_is_lifecycle_active"),
        "current_impacts_evaluated": bool(data.get("current_impacts_evaluated")),
        "hazard": _pick(data.get("hazard"), _HAZARD_KEYS),
        "impacts": impacts,
        "unique_hazard_count": _unique_ids(impacts, None, "hazard_id") if impacts else (
            1 if _pick(data.get("hazard"), _HAZARD_KEYS) else 0
        ),
        "unique_aircraft_count": len(
            {item.get("aircraft_id") for item in impacts if item.get("aircraft_id")}
        ),
        "hazard_version_link": _project_link(data.get("hazard_version_link")),
        "retrieval": [_project_retrieval(item) for item in _as_list(data.get("retrieval"))],
    }


def _project_airport_context(
    data: Mapping[str, Any],
    status: ToolResultStatus,
) -> dict[str, JsonValue]:
    return {
        "as_of_epoch": _as_int(data.get("as_of_epoch")),
        "as_of_utc": data.get("as_of_utc"),
        "found": _required_found(data, status),
        "airport_id": data.get("airport_id")
        if "airport_id" in data
        else ((data.get("airport") or {}) or {}).get("airport_id"),
        "airport_is_current": _require_bool(data, "airport_is_current"),
        "airport": _pick(data.get("airport"), _AIRPORT_KEYS),
        "latest_metar": _pick(data.get("latest_metar"), _METAR_KEYS),
        "latest_taf": _pick(data.get("latest_taf"), _TAF_KEYS),
        "metar_link": _project_link(data.get("metar_link") or data.get("metar_source_link")),
        "taf_link": _project_link(data.get("taf_link") or data.get("taf_source_link")),
        "retrieval": [_project_retrieval(item) for item in _as_list(data.get("retrieval"))],
    }


def _project_callsign_search(data: Mapping[str, Any]) -> dict[str, JsonValue]:
    matches = [_project_callsign_match(item) for item in _as_list(data.get("matches"))]
    return {
        "as_of_epoch": _as_int(data.get("as_of_epoch")),
        "as_of_utc": data.get("as_of_utc"),
        "callsign": data.get("callsign"),
        "match_count": _authoritative_count(data, "match_count", matches),
        "matches": matches,
        "retrieval": [_project_retrieval(item) for item in _as_list(data.get("retrieval"))],
    }


def _common_search_fields(data: Mapping[str, Any]) -> dict[str, JsonValue]:
    return {
        "as_of_epoch": _as_int(data.get("as_of_epoch")),
        "as_of_utc": data.get("as_of_utc"),
        "region": _pick(data.get("region"), _REGION_KEYS) if isinstance(data.get("region"), dict) else data.get("region"),
        "product_type": data.get("product_type"),
        "hazard_type": data.get("hazard_type"),
        "hazard_ids": copy.deepcopy(data.get("hazard_ids")),
        "retrieval": [_project_retrieval(item) for item in _as_list(data.get("retrieval"))],
    }


def _project_hazard_match(item: Any) -> dict[str, JsonValue]:
    record = item if isinstance(item, Mapping) else {}
    hazard = _pick(record.get("hazard"), _HAZARD_KEYS)
    payload = dict(hazard or {})
    payload["hazard"] = hazard
    payload["hazard_is_current"] = bool(record.get("hazard_is_current"))
    payload["spatial_status"] = record.get("spatial_status")
    return payload


def _project_impact(item: Any) -> dict[str, JsonValue]:
    record = item if isinstance(item, Mapping) else {}
    return {
        "hazard_id": record.get("hazard_id"),
        "source_version": record.get("source_version"),
        "encounter_id": record.get("encounter_id"),
        "aircraft_id": record.get("aircraft_id"),
        "aircraft_is_current": bool(record.get("aircraft_is_current")),
        "projection_is_current": bool(record.get("projection_is_current")),
        "aircraft": _pick(record.get("aircraft"), _AIRCRAFT_KEYS),
        "projection": _pick(record.get("projection"), _PROJECTION_KEYS),
        "aircraft_link": _project_link(record.get("aircraft_link")),
        "projection_link": _project_link(record.get("projection_link")),
        "encounter_context": _project_encounter_context(record.get("encounter_context")),
    }


def _project_encounter_record(item: Any) -> dict[str, JsonValue]:
    record = item if isinstance(item, Mapping) else {}
    return {
        "hazard_id": record.get("hazard_id"),
        "source_version": record.get("source_version"),
        "encounter_id": record.get("encounter_id"),
        "aircraft_id": record.get("aircraft_id"),
        "aircraft_is_current": bool(record.get("aircraft_is_current")),
        "aircraft": _pick(record.get("aircraft"), _AIRCRAFT_KEYS),
        "encounter_context": _project_encounter_context(record.get("encounter_context")),
    }


def _project_encounter_context(item: Any) -> dict[str, JsonValue] | None:
    if not isinstance(item, Mapping):
        return None
    encounter = _pick(item.get("encounter"), _ENCOUNTER_KEYS)
    return {
        "encounter_is_current": bool(item.get("encounter_is_current")),
        "encounter": encounter,
        "hazard": _pick(item.get("hazard"), _HAZARD_KEYS),
        "risk": _pick(item.get("risk"), _RISK_KEYS),
        "recommendations": [
            _pick(rec, _RECOMMENDATION_KEYS) or {}
            for rec in _as_list(item.get("recommendations"))
        ],
        "alerts": [
            _pick(alert, _ALERT_KEYS) or {} for alert in _as_list(item.get("alerts"))
        ],
        "encounter_link": _project_link(item.get("encounter_link")),
        "hazard_link": _project_link(item.get("hazard_link")),
        "risk_link": _project_link(item.get("risk_link")),
        "recommendation_link": _project_link(item.get("recommendation_link")),
        "alert_link": _project_link(item.get("alert_link")),
    }


def _project_callsign_match(item: Any) -> dict[str, JsonValue]:
    record = item if isinstance(item, Mapping) else {}
    aircraft = _pick(record.get("aircraft"), _AIRCRAFT_KEYS)
    return {
        "aircraft_id": record.get("aircraft_id")
        or (aircraft or {}).get("aircraft_id"),
        "aircraft_is_current": bool(record.get("aircraft_is_current")),
        "aircraft": aircraft,
    }


def _project_unevaluated(item: Any) -> dict[str, JsonValue]:
    record = item if isinstance(item, Mapping) else {}
    payload = {
        "encounter_id": record.get("encounter_id") or "",
        "aircraft_id": record.get("aircraft_id") or "",
        "hazard_id": record.get("hazard_id") or "",
        "selected_source_version": record.get("selected_source_version") or "",
        "encounter_link": _project_link(record.get("encounter_link")),
        "hazard_link": _project_link(record.get("hazard_link")),
        "limitations": _string_list(record.get("limitations")),
    }
    payload.update(_free_text_fields(record.get("reason"), "reason"))
    return payload


def _project_geo_unevaluated(item: Any) -> dict[str, JsonValue]:
    record = item if isinstance(item, Mapping) else {}
    return {
        "hazard_id": record.get("hazard_id"),
        "source_version": record.get("source_version"),
        "spatial_status": record.get("spatial_status"),
        "limitations": _string_list(record.get("limitations")),
        "parent_link": _project_link(record.get("parent_link")),
        "geometry_link": _project_link(record.get("geometry_link")),
    }


def _project_rejected(item: Any) -> dict[str, JsonValue]:
    record = item if isinstance(item, Mapping) else {}
    payload = {
        "hazard_id": record.get("hazard_id"),
        "discovered_source_version": record.get("discovered_source_version") or "",
        "limitations": _string_list(record.get("limitations")),
    }
    payload.update(_free_text_fields(record.get("reason"), "reason"))
    return payload


def _project_link(item: Any) -> dict[str, JsonValue] | None:
    if not isinstance(item, Mapping):
        return None
    payload: dict[str, JsonValue] = {
        "state": item.get("state"),
        "kind": item.get("kind"),
        "selected_identity": _project_identity(item.get("selected_identity")),
        "observed_identity": _project_identity(item.get("observed_identity")),
    }
    payload.update(_free_text_fields(item.get("reason"), "reason"))
    return payload


def _project_retrieval(item: Any) -> dict[str, JsonValue]:
    record = item if isinstance(item, Mapping) else {}
    payload: dict[str, JsonValue] = {
        "source": record.get("source"),
        "coverage": record.get("coverage"),
        "consistency": record.get("consistency"),
        "limitations": _string_list(record.get("limitations")),
    }
    if "limit" in record:
        payload["limit"] = record.get("limit")
    return payload


def _project_evidence(evidence: Evidence) -> LiveOpsProjectedEvidence:
    return LiveOpsProjectedEvidence(
        source=evidence.source,
        temporal_scope=evidence.temporal_scope,
        freshness_status=evidence.freshness_status,
        limitations=tuple(evidence.limitations),
        source_records=tuple(
            SourceRecord(
                record_id=item.record_id,
                source_version=item.source_version,
                event_timestamp_utc=item.event_timestamp_utc,
            )
            for item in evidence.source_records
        ),
        confidence=evidence.confidence,
    )


def _pick(value: Any, keys: tuple[str, ...]) -> dict[str, JsonValue] | None:
    if not isinstance(value, Mapping):
        return None
    picked: dict[str, JsonValue] = {}
    for key in keys:
        if key in _FORBIDDEN_RECOMMENDATION_KEYS:
            continue
        if key not in value:
            continue
        item = value[key]
        if key in _FREE_TEXT_KEYS:
            picked.update(_free_text_fields(item, key))
        elif key == "limitations":
            picked[key] = _string_list(item)
        elif key == "trigger_hazard_ids":
            picked[key] = _string_list(item)
        else:
            picked[key] = copy.deepcopy(item)
    return picked


def _clip_free_text(value: Any) -> tuple[Any, bool]:
    if not isinstance(value, str):
        return value, False
    if len(value) <= LIVE_OPS_PROJECTED_STRING_MAX:
        return value, False
    return _TEXT_CLIPPED, True


def _free_text_fields(value: Any, key: str) -> dict[str, JsonValue]:
    text, clipped = _clip_free_text(value)
    payload: dict[str, JsonValue] = {key: text}
    if clipped:
        payload[f"{key}_clipped"] = True
    return payload


def _require_bool(data: Mapping[str, Any], field_name: str) -> bool:
    if field_name not in data:
        raise LiveOpsProjectionError((f"missing_{field_name}",))
    value = data[field_name]
    if type(value) is not bool:
        raise LiveOpsProjectionError((f"invalid_{field_name}",))
    return value


def _required_found(data: Mapping[str, Any], status: ToolResultStatus) -> bool:
    found = _require_bool(data, "found")
    if status is ToolResultStatus.NOT_FOUND and found:
        raise LiveOpsProjectionError(("not_found_cannot_be_found",))
    return found


def _project_identity(value: Any) -> list[list[str]] | None:
    if value is None:
        return None
    projected: list[list[str]] = []
    if not isinstance(value, list):
        return projected
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        key, raw = item[0], item[1]
        if not isinstance(key, str) or key not in _LINK_IDENTITY_KEYS:
            continue
        if type(raw) is int:
            projected.append([key, str(raw)])
            continue
        if isinstance(raw, str):
            projected.append([key, raw])
    return projected


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _string_list(value: Any) -> list[str]:
    return [item for item in _as_list(value) if isinstance(item, str)]


def _as_int(value: Any) -> int | None:
    if type(value) is int:
        return value
    return None


def _authoritative_count(
    data: Mapping[str, Any],
    field_name: str,
    records: list[Any],
) -> int:
    value = _as_int(data.get(field_name))
    if value is not None:
        return value
    return len(records)


def _unique_ids(records: list[Mapping[str, Any]], nested: str | None, key: str) -> int:
    values: set[str] = set()
    for item in records:
        if not isinstance(item, Mapping):
            continue
        candidate = item.get(key)
        if not candidate and nested:
            nested_record = item.get(nested)
            if isinstance(nested_record, Mapping):
                candidate = nested_record.get(key)
        if isinstance(candidate, str) and candidate:
            values.add(candidate)
    return len(values)


__all__ = [
    "LiveOpsProjectedEvidence",
    "LiveOpsProjectionError",
    "LiveOpsToolProjection",
    "project_live_ops_tool_result",
]
