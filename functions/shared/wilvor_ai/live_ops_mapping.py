"""Map Phase 1 operational results onto Phase 0 ToolResult/Evidence.

This module transforms already-returned domain objects. It does not decide
operational currentness, geography, joins, or source-version authority, and
it does not perform DynamoDB, AWS, or wall-clock I/O.
"""

from __future__ import annotations

import math
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable, Mapping

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
)
from wilvor_operational import linking
from wilvor_operational import observed
from wilvor_operational.discovery import CALLSIGN_CASE_LIMITATION
from wilvor_operational.query import (
    ENCOUNTER_SELECTION_REQUIRED,
    QUERY_SELECTION_REQUIRED,
)


class LiveOpsMappingError(ValueError):
    """Raised when Live Ops mapping cannot proceed safely."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


FRESHNESS_NOT_ESTABLISHED = "FRESHNESS_NOT_ESTABLISHED"
ALERT_EVIDENCE_IDENTITY_UNAVAILABLE = "ALERT_EVIDENCE_IDENTITY_UNAVAILABLE"
NOT_RETURNED_BY_FILTERED_DISCOVERY = "NOT_RETURNED_BY_FILTERED_DISCOVERY"
GEOSPATIAL_UNEVALUATED_PRESENT = "GEOSPATIAL_UNEVALUATED_PRESENT"
OPERATIONALLY_UNEVALUATED_PRESENT = "OPERATIONALLY_UNEVALUATED_PRESENT"
REGION_UNRESOLVED = "REGION_UNRESOLVED"
CALLSIGN_REQUIRED = "find_aircraft_by_callsign requires a non-empty callsign."
LINEAGE_MISMATCH_PRESENT = "LINEAGE_MISMATCH_PRESENT"

SOURCE_AIRCRAFT = "wilvor.aircraft_current_state"
SOURCE_HAZARDS = "wilvor.active_hazards"
SOURCE_COORDINATES = "wilvor.hazard_coordinates"
SOURCE_ENCOUNTERS = "wilvor.aircraft_hazard_encounters"
SOURCE_PROJECTION = "wilvor.aircraft_projection"
SOURCE_RISK = "wilvor.risk_results"
SOURCE_RECOMMENDATIONS = "wilvor.recommendations"
SOURCE_ALERTS = "wilvor.active_alerts"
SOURCE_AIRPORT = "wilvor.airport_status"
SOURCE_METAR = "wilvor.metar_latest"
SOURCE_TAF = "wilvor.taf_latest"
SOURCE_TAF_PERIODS = "wilvor.taf_forecast_periods"
SOURCE_REGIONS = "wilvor.regions.us_states"

EVIDENCE_SOURCE_ORDER = (
    SOURCE_AIRCRAFT,
    SOURCE_HAZARDS,
    SOURCE_COORDINATES,
    SOURCE_ENCOUNTERS,
    SOURCE_PROJECTION,
    SOURCE_RISK,
    SOURCE_RECOMMENDATIONS,
    SOURCE_ALERTS,
    SOURCE_AIRPORT,
    SOURCE_METAR,
    SOURCE_TAF,
    SOURCE_TAF_PERIODS,
    SOURCE_REGIONS,
)

RETRIEVAL_SOURCE_MAP = {
    "get_aircraft_record": SOURCE_AIRCRAFT,
    "query_aircraft_by_callsign": SOURCE_AIRCRAFT,
    "query_aircraft_by_h3": SOURCE_AIRCRAFT,
    "scan_aircraft_candidates": SOURCE_AIRCRAFT,
    "get_hazard_record": SOURCE_HAZARDS,
    "query_active_hazard_candidates": SOURCE_HAZARDS,
    "scan_hazard_index_candidates": SOURCE_HAZARDS,
    "query_hazard_coordinate_rows": SOURCE_COORDINATES,
    "get_encounter_record": SOURCE_ENCOUNTERS,
    "query_encounter_candidates_by_hazard": SOURCE_ENCOUNTERS,
    "query_encounter_candidates_by_aircraft": SOURCE_ENCOUNTERS,
    "scan_encounter_candidates": SOURCE_ENCOUNTERS,
    "get_projection_record": SOURCE_PROJECTION,
    "scan_projection_index_candidates": SOURCE_PROJECTION,
    "get_risk_record": SOURCE_RISK,
    "scan_risk_candidates": SOURCE_RISK,
    "scan_recommendation_candidates": SOURCE_RECOMMENDATIONS,
    "scan_alert_candidates": SOURCE_ALERTS,
    "get_airport_status_record": SOURCE_AIRPORT,
    "query_airports_by_risk": SOURCE_AIRPORT,
    "query_airports_by_impact": SOURCE_AIRPORT,
    "get_metar_record": SOURCE_METAR,
    "get_taf_record": SOURCE_TAF,
    "query_taf_period_rows_for_version": SOURCE_TAF_PERIODS,
}

_NESTED_RETRIEVAL_ATTRS = (
    "operationally_unevaluated",
    "rejected_hazard_selections",
    "rejected_hazard_candidates",
    "geospatial_unevaluated",
    "intersecting",
    "non_intersecting",
    "unevaluated",
    "rejected_candidates",
)

_MISMATCH_LINK_STATES = frozenset(
    {
        "HYDRATION_VERSION_MISMATCH",
        "HYDRATION_IDENTITY_MISMATCH",
    }
)

AIRCRAFT_FIELDS = (
    "aircraft_id",
    "callsign",
    "origin_country",
    "position_time_epoch",
    "position_time_utc",
    "last_contact_epoch",
    "last_contact_utc",
    "latitude",
    "longitude",
    "baro_altitude_m",
    "geo_altitude_m",
    "baro_altitude_ft",
    "geo_altitude_ft",
    "ground_speed_mps",
    "ground_speed_kt",
    "track_deg",
    "vertical_rate_mps",
    "vertical_rate_fpm",
    "on_ground",
    "squawk",
    "spi",
    "position_source",
    "has_position",
    "current_h3_cell",
    "position_age_seconds",
    "freshness_status",
    "state_version",
    "source_system",
)
HAZARD_FIELDS = (
    "hazard_id",
    "source_version",
    "source_product_id",
    "amendment_type",
    "created_at_utc",
    "valid_from_epoch",
    "valid_from_utc",
    "valid_to_epoch",
    "valid_to_utc",
    "product_type",
    "hazard_type",
    "severity",
    "geometry_type",
    "geometry_point_count",
    "altitude_bands",
    "minimum_lower_altitude_ft",
    "maximum_upper_altitude_ft",
    "materialization_status",
    "materialization_id",
    "status",
    "source_system",
    "source_icao_id",
    "series_id",
    "alpha_char",
    "receipt_time_utc",
)
PROJECTION_FIELDS = (
    "projection_id",
    "aircraft_id",
    "aircraft_state_version",
    "source_position_time_epoch",
    "generated_at_epoch",
    "generated_at_utc",
    "current_aircraft_h3_cell",
    "trigger_hazard_ids",
    "projection_trigger_reason",
    "valid_until_epoch",
    "valid_until_utc",
    "projection_horizon_min",
    "point_count",
    "corridor_h3_cell_count",
    "confidence",
    "projection_status",
    "projection_algorithm_version",
    "projection_config_version",
    "current_altitude_ft",
    "freshness_status",
)
ENCOUNTER_FIELDS = (
    "encounter_id",
    "aircraft_id",
    "aircraft_state_version",
    "projection_id",
    "hazard_id",
    "hazard_source_version",
    "detected_at_epoch",
    "detected_at_utc",
    "hazard_type",
    "severity",
    "geometry_overlap_status",
    "time_overlap_status",
    "altitude_overlap_status",
    "corridor_intersects",
    "centerline_intersects",
    "inside_now",
    "exact_intersection_confirmed",
    "trajectory_confidence",
    "freshness_status",
    "current_altitude_ft",
    "encounter_state",
    "valid_from_utc",
    "valid_to_utc",
)
RISK_FIELDS = (
    "risk_id",
    "encounter_id",
    "aircraft_id",
    "hazard_id",
    "hazard_source_version",
    "projection_id",
    "hazard_type",
    "risk_score",
    "risk_level",
    "hazard_component_score",
    "geometry_component_score",
    "time_component_score",
    "altitude_component_score",
    "confidence_component_score",
    "freshness_component_score",
    "data_quality_component_score",
    "confidence",
    "freshness_status",
    "reasons",
    "limitations",
    "scoring_ruleset_version",
    "scoring_config_version",
    "generated_at_epoch",
    "generated_at_utc",
    "valid_until_utc",
    "severity",
)
RECOMMENDATION_FIELDS = (
    "recommendation_id",
    "recommendation_version",
    "recommendation_status",
    "risk_id",
    "aircraft_id",
    "hazard_id",
    "hazard_source_version",
    "risk_level",
    "risk_score",
    "confidence",
    "primary_action_type",
    "primary_action_details",
    "alternative_actions",
    "reasons",
    "limitations",
    "preferred_airport_id",
    "preferred_airport_assessment_id",
    "preferred_airport_score",
    "no_suitable_candidate_reason",
    "ruleset_version",
    "valid_from_utc",
    "valid_until_utc",
    "advisory_notice",
    "created_at_utc",
    "updated_at_utc",
)
ALERT_FIELDS = (
    "alert_id",
    "aircraft_id",
    "hazard_id",
    "hazard_source_version",
    "recommendation_id",
    "risk_id",
    "risk_level",
    "risk_score",
    "primary_action_type",
    "alert_type",
    "alert_state",
    "state_reason",
    "message",
    "preferred_airport_id",
    "created_at_utc",
    "updated_at_utc",
    "valid_until_utc",
)
AIRPORT_FIELDS = (
    "airport_id",
    "station_id",
    "station_name",
    "iata_code",
    "faa_lid",
    "country_code",
    "latitude",
    "longitude",
    "elevation_m",
    "has_metar",
    "has_taf",
    "metar_fetch_status",
    "taf_fetch_status",
    "metar_freshness_status",
    "taf_freshness_status",
    "metar_age_seconds",
    "taf_age_seconds",
    "observed_time_utc",
    "observed_time_epoch",
    "temperature_c",
    "dewpoint_c",
    "wind_direction_deg",
    "wind_speed_kt",
    "wind_gust_kt",
    "visibility_sm",
    "ceiling_ft",
    "flight_category",
    "weather_string",
    "weather_codes",
    "taf_version_key",
    "issued_at_utc",
    "issued_at_epoch",
    "valid_from_utc",
    "valid_to_utc",
    "forecast_period_count",
    "period_materialization_status",
    "weather_risk_level",
    "weather_impact_status",
    "assessment_status",
    "is_diversion_weather_ready",
    "status_reasons",
    "known_limitations",
    "source_metar_version",
    "source_taf_version",
    "updated_at_utc",
    "updated_at_epoch",
)
METAR_FIELDS = (
    "station_id",
    "airport_id",
    "station_name",
    "metar_version",
    "observed_time_epoch",
    "observed_time_utc",
    "temperature_c",
    "dewpoint_c",
    "wind_direction_deg",
    "wind_speed_kt",
    "wind_gust_kt",
    "visibility_sm",
    "ceiling_ft",
    "altimeter_hpa",
    "sea_level_pressure_hpa",
    "precipitation_in",
    "weather_string",
    "weather_codes",
    "flight_category",
    "metar_type",
    "latitude",
    "longitude",
    "clouds",
    "raw_text",
    "freshness_status",
    "source_system",
)
TAF_FIELDS = (
    "station_id",
    "airport_id",
    "station_name",
    "taf_version",
    "taf_version_key",
    "source_version",
    "issued_at_utc",
    "issued_at_epoch",
    "bulletin_time_utc",
    "valid_from_utc",
    "valid_from_epoch",
    "valid_to_utc",
    "valid_to_epoch",
    "most_recent",
    "is_amendment",
    "is_correction",
    "remarks",
    "latitude",
    "longitude",
    "forecast_period_count",
    "period_materialization_status",
    "has_undecoded_content",
    "freshness_status",
    "source_system",
    "raw_text",
)
TAF_PERIOD_FIELDS = (
    "taf_version_key",
    "period_key",
    "period_id",
    "station_id",
    "airport_id",
    "taf_version",
    "issued_at_utc",
    "period_from_epoch",
    "period_from_utc",
    "period_to_epoch",
    "period_to_utc",
    "change_type",
    "probability",
    "wind_direction_deg",
    "wind_direction_variable",
    "wind_speed_kt",
    "wind_gust_kt",
    "visibility_sm",
    "visibility_qualifier",
    "ceiling_ft",
    "forecast_flight_category",
    "weather_string",
    "weather_codes",
    "clouds",
    "sequence_number",
    "transition_complete_utc",
    "vertical_visibility_ft",
    "altimeter_in_hg",
    "low_level_wind_shear",
    "icing_turbulence_layers",
    "temperature_forecasts",
    "not_decoded",
)

LIST_FIELDS = frozenset(
    {
        "altitude_bands",
        "trigger_hazard_ids",
        "reasons",
        "limitations",
        "alternative_actions",
        "weather_codes",
        "status_reasons",
        "known_limitations",
        "clouds",
        "icing_turbulence_layers",
        "temperature_forecasts",
    }
)


def validate_call(call: Any) -> None:
    if call is None or getattr(call, "tables", None) is None:
        raise TypeError("tables is required")
    now_epoch = getattr(call, "now_epoch", None)
    if isinstance(now_epoch, bool) or not isinstance(now_epoch, int):
        raise TypeError("now_epoch must be int")
    tool_call_id = getattr(call, "tool_call_id", None)
    if not isinstance(tool_call_id, str) or not tool_call_id.strip():
        raise ContractValidationError("invalid_tool_call_id")
    correlation_id = getattr(call, "correlation_id", None)
    if correlation_id is not None and (
        not isinstance(correlation_id, str) or not correlation_id.strip()
    ):
        raise ContractValidationError("invalid_correlation_id")


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _optional_text(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def json_value(value: Any) -> JsonValue:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Decimal):
        integral = value.to_integral_value()
        if value == integral:
            return int(integral)
        return float(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Enum):
        return json_value(value.value)
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    return None


def extract_fields(
    record: Mapping[str, Any] | None,
    fields: tuple[str, ...],
) -> dict[str, JsonValue] | None:
    if record is None:
        return None
    extracted: dict[str, JsonValue] = {}
    for key in fields:
        if key not in record:
            extracted[key] = [] if key in LIST_FIELDS else None
        else:
            extracted[key] = json_value(record[key])
            if key in LIST_FIELDS and extracted[key] is None:
                extracted[key] = []
    return extracted


def extract_aircraft(record: Mapping[str, Any] | None) -> dict[str, JsonValue] | None:
    return extract_fields(record, AIRCRAFT_FIELDS)


def extract_hazard(record: Mapping[str, Any] | None) -> dict[str, JsonValue] | None:
    return extract_fields(record, HAZARD_FIELDS)


def extract_projection(record: Mapping[str, Any] | None) -> dict[str, JsonValue] | None:
    return extract_fields(record, PROJECTION_FIELDS)


def extract_encounter(record: Mapping[str, Any] | None) -> dict[str, JsonValue] | None:
    return extract_fields(record, ENCOUNTER_FIELDS)


def extract_risk(record: Mapping[str, Any] | None) -> dict[str, JsonValue] | None:
    return extract_fields(record, RISK_FIELDS)


def extract_recommendation(
    record: Mapping[str, Any] | None,
) -> dict[str, JsonValue] | None:
    return extract_fields(record, RECOMMENDATION_FIELDS)


def extract_alert(record: Mapping[str, Any] | None) -> dict[str, JsonValue] | None:
    return extract_fields(record, ALERT_FIELDS)


def extract_airport(record: Mapping[str, Any] | None) -> dict[str, JsonValue] | None:
    return extract_fields(record, AIRPORT_FIELDS)


def extract_metar(record: Mapping[str, Any] | None) -> dict[str, JsonValue] | None:
    return extract_fields(record, METAR_FIELDS)


def extract_taf(record: Mapping[str, Any] | None) -> dict[str, JsonValue] | None:
    return extract_fields(record, TAF_FIELDS)


def extract_taf_period(record: Mapping[str, Any] | None) -> dict[str, JsonValue] | None:
    return extract_fields(record, TAF_PERIOD_FIELDS)


def map_link(link: Any) -> dict[str, JsonValue] | None:
    if link is None:
        return None
    return {
        "state": _enum_value(getattr(link, "state", None)),
        "kind": _enum_value(getattr(link, "kind", None)),
        "reason": getattr(link, "reason", None),
        "selected_identity": [
            [key, value] for key, value in getattr(link, "selected_identity", ()) or ()
        ],
        "observed_identity": [
            [key, value] for key, value in getattr(link, "observed_identity", ()) or ()
        ],
    }


def map_retrieval_observation(observation: Any) -> dict[str, JsonValue]:
    return {
        "source": observation.source,
        "coverage": _enum_value(observation.coverage),
        "consistency": _enum_value(observation.consistency),
        "limit": observation.limit,
        "limitations": list(observation.limitations or ()),
    }


def map_region_identity(resolution: Any, query: str | None) -> dict[str, JsonValue]:
    region = getattr(resolution, "region", None) if resolution is not None else None
    resolved = bool(getattr(resolution, "resolved", False)) if resolution is not None else False
    return {
        "resolved": resolved,
        "query": query,
        "region_type": getattr(region, "region_type", None) if region is not None else None,
        "code": getattr(region, "code", None) if region is not None else None,
        "name": getattr(region, "name", None) if region is not None else None,
        "dataset_product": (
            getattr(region, "dataset_product", None) if region is not None else None
        ),
        "dataset_vintage": (
            getattr(region, "dataset_vintage", None) if region is not None else None
        ),
        "dataset_sha256": (
            getattr(region, "dataset_sha256", None) if region is not None else None
        ),
        "target_crs": getattr(region, "target_crs", None) if region is not None else None,
        "spatial_supported": (
            getattr(region, "spatial_supported", None) if region is not None else None
        ),
        "geometry_type": (
            getattr(region, "geometry_type", None) if region is not None else None
        ),
    }


def _observation_key(observation: Any) -> tuple[Any, ...]:
    return (
        observation.source,
        _enum_value(observation.coverage),
        _enum_value(observation.consistency),
        observation.limit,
        tuple(observation.limitations or ()),
    )


def collect_retrieval(root: Any) -> tuple[Any, ...]:
    seen: set[tuple[Any, ...]] = set()
    collected: list[Any] = []

    def add(observation: Any) -> None:
        source = getattr(observation, "source", None)
        if source not in RETRIEVAL_SOURCE_MAP:
            raise LiveOpsMappingError(f"unknown_retrieval_source:{source}")
        key = _observation_key(observation)
        if key in seen:
            return
        seen.add(key)
        collected.append(observation)

    def walk_observations(values: Any) -> None:
        if not values:
            return
        for observation in values:
            add(observation)

    walk_observations(getattr(root, "retrieval", None))
    for name in _NESTED_RETRIEVAL_ATTRS:
        nested = getattr(root, name, None)
        if not nested:
            continue
        for item in nested:
            walk_observations(getattr(item, "retrieval", None))
    return tuple(collected)


def filtered_rejection_reason(
    reason: Any,
    *,
    product_type: Any,
    hazard_type: Any,
) -> str:
    value = _text(_enum_value(reason))
    filtered = bool(_text(product_type)) or bool(_text(hazard_type))
    if filtered and value == "MISSING":
        return NOT_RETURNED_BY_FILTERED_DISCOVERY
    return value


def _has_observation_source(observations: Iterable[Any], source: str) -> bool:
    return any(getattr(item, "source", None) == source for item in observations)


def _source_record(
    record_id: Any,
    source_version: Any,
    event_timestamp_utc: Any,
) -> SourceRecord | None:
    identity = _text(record_id)
    if not identity:
        return None
    version = _optional_text(source_version)
    timestamp = _optional_text(event_timestamp_utc)
    try:
        return SourceRecord(identity, version, timestamp)
    except ContractValidationError:
        return SourceRecord(identity, version, None)


def _add_record(
    buckets: dict[str, list[SourceRecord]],
    source: str,
    record: SourceRecord | None,
) -> None:
    if record is None:
        return
    seen = {(item.record_id, item.source_version) for item in buckets[source]}
    key = (record.record_id, record.source_version)
    if key in seen:
        return
    buckets[source].append(record)


def _records_from_aircraft(buckets, item: Mapping[str, Any] | None) -> None:
    if not item:
        return
    _add_record(
        buckets,
        SOURCE_AIRCRAFT,
        _source_record(
            item.get("aircraft_id"),
            item.get("state_version"),
            item.get("position_time_utc"),
        ),
    )


def _records_from_hazard(buckets, item: Mapping[str, Any] | None) -> None:
    if not item:
        return
    _add_record(
        buckets,
        SOURCE_HAZARDS,
        _source_record(item.get("hazard_id"), item.get("source_version"), None),
    )


def _records_from_projection(buckets, item: Mapping[str, Any] | None) -> None:
    if not item:
        return
    _add_record(
        buckets,
        SOURCE_PROJECTION,
        _source_record(
            item.get("projection_id"),
            None,
            item.get("generated_at_utc"),
        ),
    )


def _records_from_encounter(buckets, item: Mapping[str, Any] | None) -> None:
    if not item:
        return
    _add_record(
        buckets,
        SOURCE_ENCOUNTERS,
        _source_record(item.get("encounter_id"), None, item.get("detected_at_utc")),
    )


def _records_from_risk(buckets, item: Mapping[str, Any] | None) -> None:
    if not item:
        return
    _add_record(
        buckets,
        SOURCE_RISK,
        _source_record(item.get("risk_id"), None, item.get("generated_at_utc")),
    )


def _records_from_recommendation(buckets, item: Mapping[str, Any] | None) -> None:
    if not item:
        return
    _add_record(
        buckets,
        SOURCE_RECOMMENDATIONS,
        _source_record(
            item.get("recommendation_id"),
            item.get("recommendation_version"),
            item.get("created_at_utc"),
        ),
    )


def _records_from_alert(
    buckets,
    item: Mapping[str, Any] | None,
    alert_identity_missing: list[bool],
) -> None:
    if not item:
        return
    fingerprint = _text(item.get("fingerprint"))
    if not fingerprint:
        alert_identity_missing.append(True)
        return
    _add_record(buckets, SOURCE_ALERTS, _source_record(fingerprint, None, None))


def _records_from_airport(buckets, item: Mapping[str, Any] | None) -> None:
    if not item:
        return
    _add_record(buckets, SOURCE_AIRPORT, _source_record(item.get("airport_id"), None, None))


def _records_from_metar(buckets, item: Mapping[str, Any] | None) -> None:
    if not item:
        return
    _add_record(
        buckets,
        SOURCE_METAR,
        _source_record(
            item.get("station_id"),
            item.get("metar_version"),
            item.get("observed_time_utc"),
        ),
    )


def _records_from_taf(buckets, item: Mapping[str, Any] | None) -> None:
    if not item:
        return
    version = item.get("taf_version")
    if not _text(version):
        version = item.get("source_version")
    _add_record(
        buckets,
        SOURCE_TAF,
        _source_record(item.get("station_id"), version, item.get("issued_at_utc")),
    )


def _records_from_taf_period(buckets, item: Mapping[str, Any] | None) -> None:
    if not item:
        return
    version_key = _text(item.get("taf_version_key"))
    period_key = _text(item.get("period_key"))
    if not version_key or not period_key:
        return
    _add_record(
        buckets,
        SOURCE_TAF_PERIODS,
        _source_record(
            f"{version_key}|{period_key}",
            item.get("taf_version"),
            None,
        ),
    )


def _records_from_encounter_context(
    buckets,
    context: Any,
    alert_identity_missing: list[bool],
) -> None:
    if context is None:
        return
    _records_from_encounter(buckets, getattr(context, "encounter", None))
    _records_from_hazard(buckets, getattr(context, "hazard", None))
    _records_from_risk(buckets, getattr(context, "risk", None))
    for item in getattr(context, "recommendations", ()) or ():
        _records_from_recommendation(buckets, item)
    for item in getattr(context, "alerts", ()) or ():
        _records_from_alert(buckets, item, alert_identity_missing)


def _records_from_impact_context(
    buckets,
    context: Any,
    alert_identity_missing: list[bool],
) -> None:
    if context is None:
        return
    _records_from_aircraft(buckets, getattr(context, "aircraft", None))
    _records_from_projection(buckets, getattr(context, "projection", None))
    _records_from_encounter_context(
        buckets,
        getattr(context, "encounter", None),
        alert_identity_missing,
    )


def _coordinate_record(hazard_id: Any, source_version: Any) -> SourceRecord | None:
    hid = _text(hazard_id)
    version = _text(source_version)
    if not hid or not version:
        return None
    return _source_record(f"{hid}#{version}", version, None)


def _evaluation_consulted_coordinates(evaluation: Any) -> bool:
    return _has_observation_source(
        getattr(evaluation, "retrieval", ()) or (),
        "query_hazard_coordinate_rows",
    )


def _collect_coordinate_records(root: Any, observations: tuple[Any, ...]) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    seen: set[tuple[str | None, str | None]] = set()

    def add(record: SourceRecord | None) -> None:
        if record is None:
            return
        key = (record.record_id, record.source_version)
        if key in seen:
            return
        seen.add(key)
        records.append(record)

    for name in (
        "intersecting",
        "non_intersecting",
        "unevaluated",
        "geospatial_unevaluated",
    ):
        for item in getattr(root, name, None) or ():
            if not _evaluation_consulted_coordinates(item):
                continue
            add(
                _coordinate_record(
                    getattr(item, "hazard_id", None),
                    getattr(item, "source_version", None),
                )
            )
    if _has_observation_source(observations, "query_hazard_coordinate_rows"):
        for item in getattr(root, "impacts", ()) or ():
            add(
                _coordinate_record(
                    getattr(item, "hazard_id", None),
                    getattr(item, "source_version", None),
                )
            )
    return records


def _region_source_record(resolution: Any) -> SourceRecord | None:
    region = getattr(resolution, "region", None) if resolution is not None else None
    if region is None:
        return None
    code = _text(getattr(region, "code", None))
    if not code:
        return None
    vintage = _text(getattr(region, "dataset_vintage", None))
    digest = _text(getattr(region, "dataset_sha256", None))
    version = f"{vintage}:{digest}" if vintage or digest else None
    return _source_record(code, version, None)


def _empty_record_buckets() -> dict[str, list[SourceRecord]]:
    return {source: [] for source in EVIDENCE_SOURCE_ORDER}


def _walk_domain_records(
    root: Any,
    buckets: dict[str, list[SourceRecord]],
    alert_identity_missing: list[bool],
) -> None:
    for match in getattr(root, "matches", ()) or ():
        kind = getattr(match, "kind", "")
        source = getattr(match, "source", None)
        if kind == "aircraft":
            _records_from_aircraft(buckets, source)
        elif kind == "hazard":
            _records_from_hazard(buckets, source)
    for item in getattr(root, "intersecting", ()) or ():
        _records_from_hazard(buckets, getattr(item, "hazard", None))
    for item in getattr(root, "non_intersecting", ()) or ():
        _records_from_hazard(buckets, getattr(item, "hazard", None))
    for item in getattr(root, "unevaluated", ()) or ():
        _records_from_hazard(buckets, getattr(item, "hazard", None))
    for item in getattr(root, "geospatial_unevaluated", ()) or ():
        _records_from_hazard(buckets, getattr(item, "hazard", None))
    for item in getattr(root, "rejected_candidates", ()) or ():
        _records_from_hazard(buckets, getattr(item, "exact_hazard", None))
    for item in getattr(root, "rejected_hazard_candidates", ()) or ():
        _records_from_hazard(buckets, getattr(item, "exact_hazard", None))
    for item in getattr(root, "impacts", ()) or ():
        _records_from_impact_context(
            buckets,
            getattr(item, "impact", item),
            alert_identity_missing,
        )
    for item in getattr(root, "encounters", ()) or ():
        _records_from_aircraft(buckets, getattr(item, "aircraft", None))
        encounter_context = getattr(item, "encounter_context", None)
        if encounter_context is None and hasattr(item, "encounter_is_current"):
            encounter_context = item
        _records_from_encounter_context(buckets, encounter_context, alert_identity_missing)
    aircraft = getattr(root, "aircraft", None)
    if isinstance(aircraft, dict):
        _records_from_aircraft(buckets, aircraft)
    projection = getattr(root, "projection", None)
    if isinstance(projection, dict):
        _records_from_projection(buckets, projection)
    hazard = getattr(root, "hazard", None)
    if isinstance(hazard, dict) and not hasattr(root, "matches"):
        _records_from_hazard(buckets, hazard)
    airport = getattr(root, "airport", None)
    if isinstance(airport, dict):
        _records_from_airport(buckets, airport)
    _records_from_metar(buckets, getattr(root, "latest_metar", None))
    _records_from_taf(buckets, getattr(root, "latest_taf", None))
    for item in getattr(root, "latest_taf_periods", ()) or ():
        _records_from_taf_period(buckets, item)
    for item in getattr(root, "current_aircraft_ids", ()) or ():
        _add_record(buckets, SOURCE_AIRCRAFT, _source_record(item, None, None))
    for item in getattr(root, "current_hazard_ids", ()) or ():
        _add_record(buckets, SOURCE_HAZARDS, _source_record(item, None, None))
    for item in getattr(root, "current_encounter_ids", ()) or ():
        _add_record(buckets, SOURCE_ENCOUNTERS, _source_record(item, None, None))
    for item in getattr(root, "current_risk_ids", ()) or ():
        _add_record(buckets, SOURCE_RISK, _source_record(item, None, None))
    for item in getattr(root, "current_recommendation_ids", ()) or ():
        _add_record(buckets, SOURCE_RECOMMENDATIONS, _source_record(item, None, None))


def _link_state(value: Any) -> str:
    return _text(_enum_value(value))


def _iter_links(root: Any) -> Iterable[Any]:
    for name in (
        "projection_link",
        "hazard_version_link",
        "metar_source_link",
        "taf_source_link",
        "taf_periods_link",
        "airport_status_link",
        "aircraft_link",
        "encounter_link",
        "hazard_link",
        "risk_link",
        "recommendation_link",
        "alert_link",
    ):
        link = getattr(root, name, None)
        if link is not None:
            yield link
    for item in getattr(root, "encounters", ()) or ():
        yield from _iter_links(item)
        yield from _iter_links(getattr(item, "encounter_context", None))
    for item in getattr(root, "impacts", ()) or ():
        yield from _iter_links(getattr(item, "impact", item))
        yield from _iter_links(getattr(getattr(item, "impact", item), "encounter", None))


def has_lineage_mismatch(root: Any) -> bool:
    return any(
        _link_state(getattr(link, "state", None)) in _MISMATCH_LINK_STATES
        for link in _iter_links(root)
        if link is not None
    )


def _unique_limitations(*groups: Iterable[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for group in groups:
        for item in group:
            text = _text(item)
            if text and text not in seen:
                seen.append(text)
    return tuple(seen)


def map_geospatial_unevaluated(item: Any) -> dict[str, JsonValue]:
    return {
        "hazard_id": getattr(item, "hazard_id", None),
        "source_version": getattr(item, "source_version", None) or None,
        "spatial_status": _enum_value(getattr(item, "spatial_status", None)),
        "limitations": list(getattr(item, "limitations", ()) or ()),
        "parent_link": map_link(getattr(item, "parent_link", None)),
        "geometry_link": map_link(getattr(item, "geometry_link", None)),
        "geometry_hash": getattr(item, "geometry_hash", None),
        "materialization_id": getattr(item, "materialization_id", None),
    }


def map_rejected_candidate(item: Any) -> dict[str, JsonValue]:
    return {
        "hazard_id": getattr(item, "hazard_id", None),
        "discovered_source_version": getattr(item, "discovered_source_version", None) or "",
        "reason": _enum_value(getattr(item, "reason", None)),
        "limitations": list(getattr(item, "limitations", ()) or ()),
    }


def map_rejected_selection(
    item: Any,
    *,
    product_type: Any = None,
    hazard_type: Any = None,
) -> dict[str, JsonValue]:
    return {
        "hazard_id": getattr(item, "hazard_id", None),
        "discovered_source_version": getattr(item, "discovered_source_version", None) or "",
        "reason": filtered_rejection_reason(
            getattr(item, "reason", None),
            product_type=product_type,
            hazard_type=hazard_type,
        ),
        "limitations": list(getattr(item, "limitations", ()) or ()),
    }


def map_unevaluated_candidate(item: Any) -> dict[str, JsonValue]:
    return {
        "encounter_id": getattr(item, "encounter_id", None) or "",
        "aircraft_id": getattr(item, "aircraft_id", None) or "",
        "hazard_id": getattr(item, "hazard_id", None) or "",
        "selected_source_version": getattr(item, "selected_source_version", None) or "",
        "reason": _enum_value(getattr(item, "reason", None)),
        "encounter_link": map_link(getattr(item, "encounter_link", None)),
        "hazard_link": map_link(getattr(item, "hazard_link", None)),
        "limitations": list(getattr(item, "limitations", ()) or ()),
    }


def map_encounter_context(context: Any) -> dict[str, JsonValue] | None:
    if context is None:
        return None
    return {
        "encounter_is_current": bool(getattr(context, "encounter_is_current", False)),
        "encounter": extract_encounter(getattr(context, "encounter", None)),
        "hazard": extract_hazard(getattr(context, "hazard", None)),
        "risk": extract_risk(getattr(context, "risk", None)),
        "recommendations": [
            extract_recommendation(item) or {}
            for item in getattr(context, "recommendations", ()) or ()
        ],
        "alerts": [
            extract_alert(item) or {}
            for item in getattr(context, "alerts", ()) or ()
        ],
        "encounter_link": map_link(getattr(context, "encounter_link", None)),
        "hazard_link": map_link(getattr(context, "hazard_link", None)),
        "risk_link": map_link(getattr(context, "risk_link", None)),
        "recommendation_link": map_link(getattr(context, "recommendation_link", None)),
        "alert_link": map_link(getattr(context, "alert_link", None)),
    }


def map_impact_record(record: Any) -> dict[str, JsonValue]:
    context = getattr(record, "impact", None)
    return {
        "hazard_id": getattr(record, "hazard_id", None),
        "source_version": getattr(record, "source_version", None),
        "encounter_id": getattr(record, "encounter_id", None),
        "aircraft_id": getattr(record, "aircraft_id", None),
        "aircraft_is_current": bool(getattr(context, "aircraft_is_current", False)),
        "projection_is_current": bool(getattr(context, "projection_is_current", False)),
        "aircraft": extract_aircraft(getattr(context, "aircraft", None)),
        "projection": extract_projection(getattr(context, "projection", None)),
        "aircraft_link": map_link(getattr(context, "aircraft_link", None)),
        "projection_link": map_link(getattr(context, "projection_link", None)),
        "encounter_context": map_encounter_context(getattr(context, "encounter", None)),
    }


def _as_of_fields(now_epoch: int) -> tuple[int, str]:
    return now_epoch, observed.epoch_to_utc_z(now_epoch)


def _status_for_search(
    *,
    confirmed_count: int,
    unevaluated_count: int,
    region_present: bool,
    region_resolved: bool | None,
    missing_selector: bool,
    lineage_mismatch: bool = False,
) -> ToolResultStatus:
    if missing_selector:
        return ToolResultStatus.UNKNOWN
    if region_present and region_resolved is False:
        return ToolResultStatus.NOT_FOUND
    if unevaluated_count or lineage_mismatch:
        return ToolResultStatus.PARTIAL
    return ToolResultStatus.SUCCESS


def _qualifies_high(
    *,
    status: ToolResultStatus,
    confirmed_count: int,
    has_unevaluated: bool,
    observations: tuple[Any, ...],
    limitations: tuple[str, ...],
    extra_logical_sources: Iterable[str],
) -> bool:
    if status is not ToolResultStatus.SUCCESS:
        return False
    if confirmed_count != 1:
        return False
    if has_unevaluated:
        return False
    if linking.NO_SNAPSHOT_LIMITATION in limitations:
        return False
    if any(
        linking.NO_SNAPSHOT_LIMITATION in (getattr(item, "limitations", ()) or ())
        for item in observations
    ):
        return False
    if not observations:
        return False
    if any(
        _enum_value(item.consistency) != linking.Consistency.CONSISTENT.value
        for item in observations
    ):
        return False
    logical = {RETRIEVAL_SOURCE_MAP[item.source] for item in observations}
    logical.update(extra_logical_sources)
    return len(logical) == 1


def confidence_for(
    *,
    status: ToolResultStatus,
    confirmed_count: int,
    has_unevaluated: bool,
    observations: tuple[Any, ...],
    limitations: tuple[str, ...],
    extra_logical_sources: Iterable[str] = (),
) -> ConfidenceLevel:
    if status in {
        ToolResultStatus.NOT_FOUND,
        ToolResultStatus.UNKNOWN,
        ToolResultStatus.UNAVAILABLE,
    }:
        return ConfidenceLevel.UNKNOWN
    if confirmed_count == 0 and has_unevaluated:
        return ConfidenceLevel.UNKNOWN
    if _qualifies_high(
        status=status,
        confirmed_count=confirmed_count,
        has_unevaluated=has_unevaluated,
        observations=observations,
        limitations=limitations,
        extra_logical_sources=extra_logical_sources,
    ):
        return ConfidenceLevel.HIGH
    if status is ToolResultStatus.SUCCESS:
        return ConfidenceLevel.MEDIUM
    if status is ToolResultStatus.PARTIAL and confirmed_count >= 1:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.UNKNOWN


def build_evidence(
    *,
    tool_call_id: str,
    as_of_utc: str,
    observations: tuple[Any, ...],
    root: Any = None,
    region_resolution: Any = None,
    region_path: bool = False,
    confidence: ConfidenceLevel,
    alert_identity_missing: bool = False,
) -> tuple[Evidence, ...]:
    consulted = {
        RETRIEVAL_SOURCE_MAP[item.source]
        for item in observations
    }
    buckets = _empty_record_buckets()
    missing: list[bool] = []
    if root is not None:
        _walk_domain_records(root, buckets, missing)
    if SOURCE_COORDINATES in consulted and root is not None:
        for record in _collect_coordinate_records(root, observations):
            _add_record(buckets, SOURCE_COORDINATES, record)
    if region_path:
        consulted.add(SOURCE_REGIONS)
        _add_record(buckets, SOURCE_REGIONS, _region_source_record(region_resolution))
    alert_missing = alert_identity_missing or bool(missing)
    evidence = []
    for source in EVIDENCE_SOURCE_ORDER:
        if source not in consulted:
            continue
        limitations = [FRESHNESS_NOT_ESTABLISHED]
        if source == SOURCE_ALERTS and alert_missing:
            limitations.append(ALERT_EVIDENCE_IDENTITY_UNAVAILABLE)
        evidence.append(
            Evidence(
                source=source,
                source_records=tuple(buckets.get(source, ())),
                query_timestamp_utc=as_of_utc,
                freshness_status=FreshnessStatus.UNKNOWN,
                confidence=confidence,
                limitations=tuple(limitations),
                tool_call_id=tool_call_id,
                temporal_scope=TemporalScope.CURRENT,
            )
        )
    return tuple(evidence)


def _build_tool_result(
    call: Any,
    *,
    tool_name: str,
    status: ToolResultStatus,
    data: dict[str, JsonValue],
    observations: tuple[Any, ...],
    root: Any = None,
    region_resolution: Any = None,
    region_path: bool = False,
    confirmed_count: int,
    has_unevaluated: bool,
    limitations: tuple[str, ...],
    alert_identity_missing: bool = False,
) -> ToolResult:
    _, as_of_utc = _as_of_fields(call.now_epoch)
    extra_sources = (SOURCE_REGIONS,) if region_path else ()
    confidence = confidence_for(
        status=status,
        confirmed_count=confirmed_count,
        has_unevaluated=has_unevaluated,
        observations=observations,
        limitations=limitations,
        extra_logical_sources=extra_sources,
    )
    freshness = ()
    if status is not ToolResultStatus.NOT_FOUND or region_path:
        freshness = (FRESHNESS_NOT_ESTABLISHED,)
    result_limitations = _unique_limitations(
        limitations,
        freshness,
        (ALERT_EVIDENCE_IDENTITY_UNAVAILABLE,) if alert_identity_missing else (),
    )
    if status is ToolResultStatus.UNKNOWN and not result_limitations:
        result_limitations = (FRESHNESS_NOT_ESTABLISHED,)
    if status is ToolResultStatus.PARTIAL and not result_limitations:
        result_limitations = (FRESHNESS_NOT_ESTABLISHED,)
    evidence = build_evidence(
        tool_call_id=call.tool_call_id,
        as_of_utc=as_of_utc,
        observations=observations,
        root=root,
        region_resolution=region_resolution,
        region_path=region_path,
        confidence=confidence,
        alert_identity_missing=alert_identity_missing,
    )
    return ToolResult(
        tool_name=tool_name,
        tool_call_id=call.tool_call_id,
        status=status,
        temporal_scope=TemporalScope.CURRENT,
        data=data,
        evidence=evidence,
        as_of_utc=as_of_utc,
        limitations=result_limitations,
        correlation_id=call.correlation_id,
    )


def _identity_value(identity: Any, name: str) -> str:
    for key, value in identity or ():
        if key == name:
            return _text(value)
    return ""


def map_search_current_hazards_discovery(
    call: Any,
    result: Any,
    *,
    product_type: Any,
    hazard_type: Any,
    hazard_ids: Any,
) -> ToolResult:
    observations = collect_retrieval(result)
    current_matches = [
        match
        for match in getattr(result, "matches", ()) or ()
        if getattr(match, "is_current", False)
    ]
    hazards = [
        {
            "hazard": extract_hazard(getattr(match, "source", None)),
            "hazard_is_current": True,
            "spatial_status": None,
        }
        for match in current_matches
    ]
    rejected: list[dict[str, JsonValue]] = []
    if hazard_ids is not None:
        by_id = {
            _identity_value(getattr(match, "identity", ()), "hazard_id"): match
            for match in getattr(result, "matches", ()) or ()
        }
        for raw_id in hazard_ids:
            hazard_id = _text(raw_id)
            if not hazard_id:
                continue
            match = by_id.get(hazard_id)
            if match is None:
                rejected.append(
                    {
                        "hazard_id": hazard_id,
                        "discovered_source_version": "",
                        "reason": filtered_rejection_reason(
                            "MISSING",
                            product_type=product_type,
                            hazard_type=hazard_type,
                        ),
                        "limitations": [],
                    }
                )
                continue
            if not getattr(match, "is_current", False):
                rejected.append(
                    {
                        "hazard_id": hazard_id,
                        "discovered_source_version": _identity_value(
                            getattr(match, "identity", ()),
                            "source_version",
                        ),
                        "reason": "NOT_CURRENT",
                        "limitations": [],
                    }
                )
    as_of_epoch, as_of_utc = _as_of_fields(call.now_epoch)
    data = {
        "as_of_epoch": as_of_epoch,
        "as_of_utc": as_of_utc,
        "region": None,
        "product_type": product_type,
        "hazard_type": hazard_type,
        "hazard_ids": json_value(hazard_ids) if hazard_ids is not None else None,
        "match_count": len(hazards),
        "hazards": hazards,
        "geospatial_unevaluated": [],
        "rejected_hazard_candidates": [],
        "rejected_hazard_selections": rejected,
        "non_intersecting_count": 0,
        "retrieval": [map_retrieval_observation(item) for item in observations],
    }
    domain_limitations = tuple(getattr(result, "limitations", ()) or ())
    return _build_tool_result(
        call,
        tool_name="search_current_hazards",
        status=ToolResultStatus.SUCCESS,
        data=data,
        observations=observations,
        root=result,
        confirmed_count=len(hazards),
        has_unevaluated=False,
        limitations=domain_limitations,
    )


def _seen_hazard_ids(*groups: Iterable[Any]) -> set[str]:
    seen: set[str] = set()
    for group in groups:
        for item in group or ():
            hazard_id = _text(getattr(item, "hazard_id", None))
            if hazard_id:
                seen.add(hazard_id)
    return seen


def map_search_current_hazards_region(
    call: Any,
    result: Any,
    *,
    region: str,
    product_type: Any,
    hazard_type: Any,
    hazard_ids: Any,
) -> ToolResult:
    observations = collect_retrieval(result)
    resolution = getattr(result, "region_resolution", None)
    resolved = bool(getattr(resolution, "resolved", False)) if resolution is not None else False
    intersecting = tuple(getattr(result, "intersecting", ()) or ())
    hazards = [
        {
            "hazard": extract_hazard(getattr(item, "hazard", None)),
            "hazard_is_current": True,
            "spatial_status": "INTERSECTS",
        }
        for item in intersecting
    ]
    geo_unevaluated = [
        map_geospatial_unevaluated(item)
        for item in getattr(result, "unevaluated", ()) or ()
    ]
    rejected_candidates = [
        map_rejected_candidate(item)
        for item in getattr(result, "rejected_candidates", ()) or ()
    ]
    rejected_selections: list[dict[str, JsonValue]] = []
    if hazard_ids is not None:
        seen = _seen_hazard_ids(
            intersecting,
            getattr(result, "non_intersecting", ()) or (),
            getattr(result, "unevaluated", ()) or (),
            getattr(result, "rejected_candidates", ()) or (),
        )
        for raw_id in hazard_ids:
            hazard_id = _text(raw_id)
            if not hazard_id or hazard_id in seen:
                continue
            rejected_selections.append(
                {
                    "hazard_id": hazard_id,
                    "discovered_source_version": "",
                    "reason": filtered_rejection_reason(
                        "MISSING",
                        product_type=product_type,
                        hazard_type=hazard_type,
                    ),
                    "limitations": [],
                }
            )
    as_of_epoch, as_of_utc = _as_of_fields(call.now_epoch)
    data = {
        "as_of_epoch": as_of_epoch,
        "as_of_utc": as_of_utc,
        "region": map_region_identity(resolution, region),
        "product_type": product_type,
        "hazard_type": hazard_type,
        "hazard_ids": json_value(hazard_ids) if hazard_ids is not None else None,
        "match_count": len(hazards),
        "hazards": hazards,
        "geospatial_unevaluated": geo_unevaluated,
        "rejected_hazard_candidates": rejected_candidates,
        "rejected_hazard_selections": rejected_selections,
        "non_intersecting_count": len(getattr(result, "non_intersecting", ()) or ()),
        "retrieval": [map_retrieval_observation(item) for item in observations],
    }
    unevaluated_count = len(geo_unevaluated)
    status = _status_for_search(
        confirmed_count=len(hazards),
        unevaluated_count=unevaluated_count,
        region_present=True,
        region_resolved=resolved,
        missing_selector=False,
    )
    extra = []
    if not resolved:
        extra.append(REGION_UNRESOLVED)
    if unevaluated_count:
        extra.append(GEOSPATIAL_UNEVALUATED_PRESENT)
    return _build_tool_result(
        call,
        tool_name="search_current_hazards",
        status=status,
        data=data,
        observations=observations,
        root=result,
        region_resolution=resolution,
        region_path=True,
        confirmed_count=len(hazards),
        has_unevaluated=bool(unevaluated_count),
        limitations=_unique_limitations(
            getattr(result, "limitations", ()) or (),
            extra,
        ),
    )


def _alert_identity_missing_from_payload(items: Iterable[Any]) -> bool:
    for item in items:
        context = getattr(item, "impact", None)
        encounter = None
        if context is not None:
            encounter = getattr(context, "encounter", None)
        if encounter is None:
            encounter = getattr(item, "encounter_context", None)
        if encounter is None and hasattr(item, "alerts"):
            encounter = item
        alerts = getattr(encounter, "alerts", ()) or ()
        for alert in alerts:
            if isinstance(alert, dict) and not _text(alert.get("fingerprint")):
                return True
    return False


def map_search_current_impacts(
    call: Any,
    result: Any,
    *,
    region: Any,
    product_type: Any,
    hazard_type: Any,
    hazard_ids: Any,
) -> ToolResult:
    observations = collect_retrieval(result)
    resolution = getattr(result, "region_resolution", None)
    region_path = region not in (None, "")
    resolved = None
    if region_path:
        resolved = bool(getattr(resolution, "resolved", False)) if resolution is not None else False
    impacts = [map_impact_record(item) for item in getattr(result, "impacts", ()) or ()]
    operationally = [
        map_unevaluated_candidate(item)
        for item in getattr(result, "operationally_unevaluated", ()) or ()
    ]
    geo_unevaluated = [
        map_geospatial_unevaluated(item)
        for item in getattr(result, "geospatial_unevaluated", ()) or ()
    ]
    as_of_epoch, as_of_utc = _as_of_fields(call.now_epoch)
    data = {
        "as_of_epoch": as_of_epoch,
        "as_of_utc": as_of_utc,
        "region": map_region_identity(resolution, region) if region_path else None,
        "product_type": product_type,
        "hazard_type": hazard_type,
        "hazard_ids": json_value(hazard_ids) if hazard_ids is not None else None,
        "impact_count": len(impacts),
        "unique_aircraft_count": len(getattr(result, "unique_aircraft_ids", ()) or ()),
        "unique_aircraft_ids": list(getattr(result, "unique_aircraft_ids", ()) or ()),
        "impacts": impacts,
        "operationally_unevaluated": operationally,
        "geospatial_unevaluated": geo_unevaluated,
        "rejected_hazard_candidates": [
            map_rejected_candidate(item)
            for item in getattr(result, "rejected_hazard_candidates", ()) or ()
        ],
        "rejected_hazard_selections": [
            map_rejected_selection(
                item,
                product_type=product_type,
                hazard_type=hazard_type,
            )
            for item in getattr(result, "rejected_hazard_selections", ()) or ()
        ],
        "retrieval": [map_retrieval_observation(item) for item in observations],
    }
    missing_selector = (not region) and hazard_ids is None
    unevaluated_count = len(operationally) + len(geo_unevaluated)
    status = _status_for_search(
        confirmed_count=len(impacts),
        unevaluated_count=unevaluated_count,
        region_present=region_path,
        region_resolved=resolved,
        missing_selector=missing_selector,
    )
    extra = []
    if missing_selector:
        extra.append(QUERY_SELECTION_REQUIRED)
    if region_path and resolved is False:
        extra.append(REGION_UNRESOLVED)
    if operationally:
        extra.append(OPERATIONALLY_UNEVALUATED_PRESENT)
    if geo_unevaluated:
        extra.append(GEOSPATIAL_UNEVALUATED_PRESENT)
    alert_missing = _alert_identity_missing_from_payload(getattr(result, "impacts", ()) or ())
    return _build_tool_result(
        call,
        tool_name="search_current_impacts",
        status=status,
        data=data,
        observations=observations,
        root=result,
        region_resolution=resolution,
        region_path=region_path,
        confirmed_count=len(impacts),
        has_unevaluated=bool(unevaluated_count),
        limitations=_unique_limitations(getattr(result, "limitations", ()) or (), extra),
        alert_identity_missing=alert_missing,
    )


def map_search_current_encounters(
    call: Any,
    result: Any,
    *,
    aircraft_id: Any,
    callsign: Any,
    hazard_id: Any,
    hazard_ids: Any,
) -> ToolResult:
    observations = collect_retrieval(result)
    encounters = []
    for item in getattr(result, "encounters", ()) or ():
        encounters.append(
            {
                "hazard_id": getattr(item, "hazard_id", None),
                "source_version": getattr(item, "source_version", None),
                "encounter_id": getattr(item, "encounter_id", None),
                "aircraft_id": getattr(item, "aircraft_id", None),
                "aircraft_is_current": bool(getattr(item, "aircraft_is_current", False)),
                "aircraft": extract_aircraft(getattr(item, "aircraft", None)),
                "encounter_context": map_encounter_context(
                    getattr(item, "encounter_context", None)
                ),
            }
        )
    operationally = [
        map_unevaluated_candidate(item)
        for item in getattr(result, "operationally_unevaluated", ()) or ()
    ]
    as_of_epoch, as_of_utc = _as_of_fields(call.now_epoch)
    data = {
        "as_of_epoch": as_of_epoch,
        "as_of_utc": as_of_utc,
        "aircraft_id": aircraft_id,
        "callsign": callsign,
        "hazard_id": hazard_id,
        "hazard_ids": json_value(hazard_ids) if hazard_ids is not None else None,
        "callsign_matches": list(getattr(result, "callsign_matches", ()) or ()),
        "encounter_count": len(encounters),
        "encounters": encounters,
        "operationally_unevaluated": operationally,
        "rejected_hazard_selections": [
            map_rejected_selection(item)
            for item in getattr(result, "rejected_hazard_selections", ()) or ()
        ],
        "retrieval": [map_retrieval_observation(item) for item in observations],
    }
    missing_selector = not any(
        (aircraft_id, callsign, hazard_id, hazard_ids is not None)
    )
    status = _status_for_search(
        confirmed_count=len(encounters),
        unevaluated_count=len(operationally),
        region_present=False,
        region_resolved=None,
        missing_selector=missing_selector,
    )
    extra = []
    if missing_selector:
        extra.append(ENCOUNTER_SELECTION_REQUIRED)
    if operationally:
        extra.append(OPERATIONALLY_UNEVALUATED_PRESENT)
    alert_missing = _alert_identity_missing_from_payload(
        getattr(result, "encounters", ()) or ()
    )
    return _build_tool_result(
        call,
        tool_name="search_current_encounters",
        status=status,
        data=data,
        observations=observations,
        root=result,
        confirmed_count=len(encounters),
        has_unevaluated=bool(operationally),
        limitations=_unique_limitations(getattr(result, "limitations", ()) or (), extra),
        alert_identity_missing=alert_missing,
    )


def map_observed_network_state(call: Any, result: Any) -> ToolResult:
    observations = collect_retrieval(result)
    as_of_epoch, as_of_utc = _as_of_fields(call.now_epoch)
    aircraft_ids = list(getattr(result, "current_aircraft_ids", ()) or ())
    hazard_ids = list(getattr(result, "current_hazard_ids", ()) or ())
    encounter_ids = list(getattr(result, "current_encounter_ids", ()) or ())
    risk_ids = list(getattr(result, "current_risk_ids", ()) or ())
    recommendation_ids = list(getattr(result, "current_recommendation_ids", ()) or ())
    alert_ids = list(getattr(result, "current_alert_ids", ()) or ())
    data = {
        "as_of_epoch": as_of_epoch,
        "as_of_utc": as_of_utc,
        "observed": True,
        "complete_network_snapshot": False,
        "current_aircraft_ids": aircraft_ids,
        "current_aircraft_count": len(aircraft_ids),
        "current_hazard_ids": hazard_ids,
        "current_hazard_count": len(hazard_ids),
        "current_encounter_ids": encounter_ids,
        "current_encounter_count": len(encounter_ids),
        "current_risk_ids": risk_ids,
        "current_risk_count": len(risk_ids),
        "current_recommendation_ids": recommendation_ids,
        "current_recommendation_count": len(recommendation_ids),
        "current_alert_ids": alert_ids,
        "current_alert_count": len(alert_ids),
        "retrieval": [map_retrieval_observation(item) for item in observations],
    }
    return _build_tool_result(
        call,
        tool_name="get_observed_network_state",
        status=ToolResultStatus.SUCCESS,
        data=data,
        observations=observations,
        root=result,
        confirmed_count=1,
        has_unevaluated=False,
        limitations=tuple(getattr(result, "limitations", ()) or ()),
    )


def _missing_context_result(
    call: Any,
    *,
    tool_name: str,
    data: dict[str, JsonValue],
) -> ToolResult:
    _, as_of_utc = _as_of_fields(call.now_epoch)
    return ToolResult(
        tool_name=tool_name,
        tool_call_id=call.tool_call_id,
        status=ToolResultStatus.NOT_FOUND,
        temporal_scope=TemporalScope.CURRENT,
        data=data,
        evidence=(),
        as_of_utc=as_of_utc,
        limitations=(),
        correlation_id=call.correlation_id,
    )


def map_aircraft_operational_context(
    call: Any,
    result: Any,
    *,
    aircraft_id: Any,
) -> ToolResult:
    as_of_epoch, as_of_utc = _as_of_fields(call.now_epoch)
    if result is None:
        return _missing_context_result(
            call,
            tool_name="get_aircraft_operational_context",
            data={
                "as_of_epoch": as_of_epoch,
                "as_of_utc": as_of_utc,
                "found": False,
                "aircraft_id": aircraft_id,
                "aircraft_is_current": False,
                "aircraft": None,
                "projection_is_current": False,
                "projection": None,
                "projection_link": None,
                "encounters": [],
                "retrieval": [],
            },
        )
    observations = collect_retrieval(result)
    mismatch = has_lineage_mismatch(result)
    encounters = [
        map_encounter_context(item) or {}
        for item in getattr(result, "encounters", ()) or ()
    ]
    data = {
        "as_of_epoch": as_of_epoch,
        "as_of_utc": as_of_utc,
        "found": True,
        "aircraft_id": aircraft_id,
        "aircraft_is_current": bool(getattr(result, "aircraft_is_current", False)),
        "aircraft": extract_aircraft(getattr(result, "aircraft", None)),
        "projection_is_current": bool(getattr(result, "projection_is_current", False)),
        "projection": extract_projection(getattr(result, "projection", None)),
        "projection_link": map_link(getattr(result, "projection_link", None)),
        "encounters": encounters,
        "retrieval": [map_retrieval_observation(item) for item in observations],
    }
    extra = [LINEAGE_MISMATCH_PRESENT] if mismatch else []
    alert_missing = _alert_identity_missing_from_payload(
        getattr(result, "encounters", ()) or ()
    )
    return _build_tool_result(
        call,
        tool_name="get_aircraft_operational_context",
        status=ToolResultStatus.PARTIAL if mismatch else ToolResultStatus.SUCCESS,
        data=data,
        observations=observations,
        root=result,
        confirmed_count=1,
        has_unevaluated=mismatch,
        limitations=_unique_limitations(
            (linking.NO_SNAPSHOT_LIMITATION,),
            extra,
        ),
        alert_identity_missing=alert_missing,
    )


def map_hazard_operational_context(
    call: Any,
    result: Any,
    *,
    hazard_id: Any,
) -> ToolResult:
    as_of_epoch, as_of_utc = _as_of_fields(call.now_epoch)
    if result is None:
        return _missing_context_result(
            call,
            tool_name="get_hazard_operational_context",
            data={
                "as_of_epoch": as_of_epoch,
                "as_of_utc": as_of_utc,
                "found": False,
                "hazard_id": hazard_id,
                "source_version": None,
                "hazard_is_lifecycle_active": False,
                "hazard_is_current": False,
                "current_impacts_evaluated": False,
                "hazard": None,
                "impacts": [],
                "hazard_version_link": None,
                "retrieval": [],
            },
        )
    observations = collect_retrieval(result)
    mismatch = has_lineage_mismatch(result)
    current_impacts_evaluated = _has_observation_source(
        observations,
        "query_encounter_candidates_by_hazard",
    )
    impacts = []
    for item in getattr(result, "impacts", ()) or ():
        encounter_row = getattr(getattr(item, "encounter", None), "encounter", None)
        aircraft_row = getattr(item, "aircraft", None)
        impacts.append(
            {
                "hazard_id": hazard_id,
                "source_version": getattr(result, "source_version", None) or None,
                "encounter_id": (
                    encounter_row.get("encounter_id")
                    if isinstance(encounter_row, dict)
                    else None
                ),
                "aircraft_id": (
                    aircraft_row.get("aircraft_id")
                    if isinstance(aircraft_row, dict)
                    else None
                ),
                "aircraft_is_current": bool(getattr(item, "aircraft_is_current", False)),
                "projection_is_current": bool(getattr(item, "projection_is_current", False)),
                "aircraft": extract_aircraft(aircraft_row),
                "projection": extract_projection(getattr(item, "projection", None)),
                "aircraft_link": map_link(getattr(item, "aircraft_link", None)),
                "projection_link": map_link(getattr(item, "projection_link", None)),
                "encounter_context": map_encounter_context(getattr(item, "encounter", None)),
            }
        )
    data = {
        "as_of_epoch": as_of_epoch,
        "as_of_utc": as_of_utc,
        "found": True,
        "hazard_id": hazard_id,
        "source_version": getattr(result, "source_version", None) or None,
        "hazard_is_lifecycle_active": bool(
            getattr(result, "hazard_is_lifecycle_active", False)
        ),
        "hazard_is_current": bool(getattr(result, "hazard_is_current", False)),
        "current_impacts_evaluated": current_impacts_evaluated,
        "hazard": extract_hazard(getattr(result, "hazard", None)),
        "impacts": impacts,
        "hazard_version_link": map_link(getattr(result, "hazard_version_link", None)),
        "retrieval": [map_retrieval_observation(item) for item in observations],
    }
    extra = [LINEAGE_MISMATCH_PRESENT] if mismatch else []
    alert_missing = False
    for item in getattr(result, "impacts", ()) or ():
        encounter = getattr(item, "encounter", None)
        for alert in getattr(encounter, "alerts", ()) or ():
            if isinstance(alert, dict) and not _text(alert.get("fingerprint")):
                alert_missing = True
    return _build_tool_result(
        call,
        tool_name="get_hazard_operational_context",
        status=ToolResultStatus.PARTIAL if mismatch else ToolResultStatus.SUCCESS,
        data=data,
        observations=observations,
        root=result,
        confirmed_count=1,
        has_unevaluated=mismatch,
        limitations=_unique_limitations((linking.NO_SNAPSHOT_LIMITATION,), extra),
        alert_identity_missing=alert_missing,
    )


def map_airport_operational_context(
    call: Any,
    result: Any,
    *,
    airport_id: Any,
) -> ToolResult:
    as_of_epoch, as_of_utc = _as_of_fields(call.now_epoch)
    if result is None:
        return _missing_context_result(
            call,
            tool_name="get_airport_operational_context",
            data={
                "as_of_epoch": as_of_epoch,
                "as_of_utc": as_of_utc,
                "found": False,
                "airport_id": airport_id,
                "airport_is_current": False,
                "station_id": None,
                "airport": None,
                "latest_metar": None,
                "latest_taf": None,
                "latest_taf_periods": [],
                "metar_source_link": None,
                "taf_source_link": None,
                "taf_periods_link": None,
                "airport_status_link": None,
                "retrieval": [],
            },
        )
    observations = collect_retrieval(result)
    mismatch = has_lineage_mismatch(result)
    data = {
        "as_of_epoch": as_of_epoch,
        "as_of_utc": as_of_utc,
        "found": True,
        "airport_id": airport_id,
        "airport_is_current": bool(getattr(result, "airport_is_current", False)),
        "station_id": getattr(result, "station_id", None) or None,
        "airport": extract_airport(getattr(result, "airport", None)),
        "latest_metar": extract_metar(getattr(result, "latest_metar", None)),
        "latest_taf": extract_taf(getattr(result, "latest_taf", None)),
        "latest_taf_periods": [
            extract_taf_period(item) or {}
            for item in getattr(result, "latest_taf_periods", ()) or ()
        ],
        "metar_source_link": map_link(getattr(result, "metar_source_link", None)),
        "taf_source_link": map_link(getattr(result, "taf_source_link", None)),
        "taf_periods_link": map_link(getattr(result, "taf_periods_link", None)),
        "airport_status_link": map_link(getattr(result, "airport_status_link", None)),
        "retrieval": [map_retrieval_observation(item) for item in observations],
    }
    extra = [LINEAGE_MISMATCH_PRESENT] if mismatch else []
    return _build_tool_result(
        call,
        tool_name="get_airport_operational_context",
        status=ToolResultStatus.PARTIAL if mismatch else ToolResultStatus.SUCCESS,
        data=data,
        observations=observations,
        root=result,
        confirmed_count=1,
        has_unevaluated=mismatch,
        limitations=_unique_limitations((linking.NO_SNAPSHOT_LIMITATION,), extra),
    )


def map_find_aircraft_by_callsign(
    call: Any,
    result: Any,
    *,
    callsign: Any,
) -> ToolResult:
    as_of_epoch, as_of_utc = _as_of_fields(call.now_epoch)
    if not _text(callsign):
        data = {
            "as_of_epoch": as_of_epoch,
            "as_of_utc": as_of_utc,
            "callsign": callsign,
            "match_count": 0,
            "matches": [],
            "retrieval": [],
        }
        return _build_tool_result(
            call,
            tool_name="find_aircraft_by_callsign",
            status=ToolResultStatus.UNKNOWN,
            data=data,
            observations=(),
            confirmed_count=0,
            has_unevaluated=False,
            limitations=(CALLSIGN_REQUIRED,),
        )
    observations = collect_retrieval(result)
    matches = [
        {
            "aircraft_is_current": True,
            "aircraft": extract_aircraft(getattr(match, "source", None)),
        }
        for match in getattr(result, "matches", ()) or ()
        if getattr(match, "is_current", False)
    ]
    data = {
        "as_of_epoch": as_of_epoch,
        "as_of_utc": as_of_utc,
        "callsign": callsign,
        "match_count": len(matches),
        "matches": matches,
        "retrieval": [map_retrieval_observation(item) for item in observations],
    }
    extra = []
    if CALLSIGN_CASE_LIMITATION in tuple(
        limitation
        for observation in observations
        for limitation in (getattr(observation, "limitations", ()) or ())
    ):
        extra.append(CALLSIGN_CASE_LIMITATION)
    return _build_tool_result(
        call,
        tool_name="find_aircraft_by_callsign",
        status=ToolResultStatus.SUCCESS,
        data=data,
        observations=observations,
        root=result,
        confirmed_count=len(matches) if matches else 0,
        has_unevaluated=False,
        limitations=tuple(extra),
    )
