"""Pure EventBridge-detail and in-memory mappings onto historical facts.

This module performs no AWS I/O. Hazard geometry is built from the enabled
SIGMET processor's in-memory coordinate points, not from a transport envelope.
"""

from __future__ import annotations

from typing import Any, Mapping

from .contracts import (
    COORDINATES_AXIS_LONLAT,
    ENCOUNTER_FACT_SCHEMA_VERSION,
    HAZARD_GEOMETRY_FACT_SCHEMA_VERSION,
    HAZARD_VERSION_FACT_SCHEMA_VERSION,
    RISK_FACT_SCHEMA_VERSION,
    Dataset,
    EncounterFact,
    FactKind,
    HazardGeometryFact,
    HazardVersionFact,
    HistoricalFactError,
    RiskFact,
    as_json_number,
    encounter_dedup_id,
)
from .geometry_geojson import build_geojson_geometry
from .time import resolve_event_time


GEOMETRY_IN_MEMORY_DETAIL_TYPE = "in_memory.hazard_geometry"
DEFAULT_ENCOUNTER_SOURCE_SYSTEM = "wilvor.encounter"
DEFAULT_RISK_SOURCE_SYSTEM = "wilvor.risk"
DEFAULT_HAZARD_SOURCE_SYSTEM = "NOAA_AVIATIONWEATHER_SIGMET"
DEFAULT_GEOMETRY_PRODUCER_SOURCE = "wilvor.weather"


class HistoricalMappingError(HistoricalFactError):
    """Raised when a producer event cannot be mapped to a historical fact."""


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require_text(detail: Mapping[str, Any], key: str) -> str:
    text = _text(detail.get(key))
    if text is None:
        raise HistoricalMappingError(f"missing {key}")
    return text


def _optional_text(detail: Mapping[str, Any], key: str) -> str | None:
    value = detail.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require_bool(detail: Mapping[str, Any], key: str) -> bool:
    value = detail.get(key)
    if isinstance(value, bool):
        return value
    raise HistoricalMappingError(f"invalid {key}")


def _json_number(value: Any, field_name: str) -> int | float:
    try:
        return as_json_number(value, field_name)
    except HistoricalFactError as exc:
        raise HistoricalMappingError(f"invalid {field_name}") from exc


def _require_int(detail: Mapping[str, Any], key: str) -> int:
    value = detail.get(key)
    if value is None or isinstance(value, bool):
        raise HistoricalMappingError(f"missing {key}")
    number = _json_number(value, key)
    if isinstance(number, float):
        if not number.is_integer():
            raise HistoricalMappingError(f"invalid {key}")
        return int(number)
    if isinstance(number, int):
        return number
    raise HistoricalMappingError(f"invalid {key}")


def _optional_int(detail: Mapping[str, Any], key: str) -> int | None:
    if detail.get(key) is None:
        return None
    return _require_int(detail, key)


def _optional_number(detail: Mapping[str, Any], key: str) -> int | float | None:
    value = detail.get(key)
    if value is None or value == "":
        return None
    number = _json_number(value, key)
    if not isinstance(number, (int, float)):
        raise HistoricalMappingError(f"invalid {key}")
    return number


def _require_number(detail: Mapping[str, Any], key: str) -> int | float:
    value = detail.get(key)
    if value is None or value == "":
        raise HistoricalMappingError(f"missing {key}")
    number = _json_number(value, key)
    if not isinstance(number, (int, float)):
        raise HistoricalMappingError(f"invalid {key}")
    return number


def _event_clock(
    *,
    event_time_utc: str,
    event_time_epoch: int | None = None,
) -> tuple[str, int, str, str, str]:
    try:
        return resolve_event_time(
            event_time_utc=event_time_utc,
            event_time_epoch=event_time_epoch,
        )
    except Exception as exc:
        raise HistoricalMappingError(str(exc)) from exc


def build_encounter_fact(
    detail: Mapping[str, Any],
    *,
    producer_source: str,
    producer_detail_type: str,
) -> EncounterFact:
    if producer_source != "wilvor.encounter":
        raise HistoricalMappingError("unexpected encounter producer_source")
    if producer_detail_type == "encounter.updated":
        fact_kind = FactKind.ENCOUNTER_OBSERVED
    elif producer_detail_type == "encounter.resolved":
        fact_kind = FactKind.ENCOUNTER_TERMINAL
    else:
        raise HistoricalMappingError(
            f"unsupported encounter detail-type: {producer_detail_type}"
        )

    encounter_id = _require_text(detail, "encounter_id")
    encounter_state = _require_text(detail, "encounter_state")
    detected_at_utc = _require_text(detail, "detected_at_utc")
    detected_at_epoch = _require_int(detail, "detected_at_epoch")
    event_time_utc, event_time_epoch, year, month, day = _event_clock(
        event_time_utc=detected_at_utc,
        event_time_epoch=detected_at_epoch,
    )
    resolved_at_epoch = _optional_int(detail, "resolved_at_epoch")
    resolved_at_utc = _optional_text(detail, "resolved_at_utc")
    if fact_kind is FactKind.ENCOUNTER_TERMINAL and (
        resolved_at_epoch is None or resolved_at_utc is None
    ):
        raise HistoricalMappingError(
            "encounter.resolved missing resolved_at"
        )
    if resolved_at_utc is not None:
        resolved_at_utc, resolved_epoch_canon, _, _, _ = _event_clock(
            event_time_utc=resolved_at_utc,
            event_time_epoch=resolved_at_epoch,
        )
        resolved_at_epoch = resolved_epoch_canon

    return EncounterFact(
        dataset=Dataset.ENCOUNTER,
        fact_schema_version=ENCOUNTER_FACT_SCHEMA_VERSION,
        fact_kind=fact_kind,
        record_id=encounter_id,
        dedup_id=encounter_dedup_id(
            encounter_id=encounter_id,
            fact_kind=fact_kind,
            detected_at_epoch=event_time_epoch,
            encounter_state=encounter_state,
            resolved_at_epoch=resolved_at_epoch,
        ),
        event_time_utc=event_time_utc,
        event_time_epoch=event_time_epoch,
        event_year=year,
        event_month=month,
        event_day=day,
        source_system=_optional_text(detail, "source_system")
        or DEFAULT_ENCOUNTER_SOURCE_SYSTEM,
        producer_source=producer_source,
        producer_detail_type=producer_detail_type,
        producer_schema_version=_optional_text(detail, "schema_version"),
        correlation_id=_optional_text(detail, "correlation_id"),
        encounter_id=encounter_id,
        aircraft_id=_require_text(detail, "aircraft_id"),
        aircraft_state_version=_require_text(
            detail,
            "aircraft_state_version",
        ),
        projection_id=_require_text(detail, "projection_id"),
        hazard_id=_require_text(detail, "hazard_id"),
        hazard_source_version=_require_text(detail, "hazard_source_version"),
        hazard_version_key=_require_text(detail, "hazard_version_key"),
        encounter_state=encounter_state,
        geometry_overlap_status=_optional_text(
            detail,
            "geometry_overlap_status",
        ),
        time_overlap_status=_optional_text(detail, "time_overlap_status"),
        altitude_overlap_status=_optional_text(
            detail,
            "altitude_overlap_status",
        ),
        exact_intersection_confirmed=_require_bool(
            detail,
            "exact_intersection_confirmed",
        ),
        detected_at_epoch=event_time_epoch,
        detected_at_utc=event_time_utc,
        resolution_reason=_optional_text(detail, "resolution_reason"),
        geometry_hash=_require_text(detail, "geometry_hash"),
        hazard_type=_require_text(detail, "hazard_type"),
        inside_now=_require_bool(detail, "inside_now"),
        corridor_intersects=_require_bool(detail, "corridor_intersects"),
        resolved_at_epoch=resolved_at_epoch,
        resolved_at_utc=resolved_at_utc,
    )


def build_risk_fact(
    detail: Mapping[str, Any],
    *,
    producer_source: str,
    producer_detail_type: str,
) -> RiskFact:
    if producer_source != "wilvor.risk":
        raise HistoricalMappingError("unexpected risk producer_source")
    if producer_detail_type not in {"risk.updated", "risk.resolved"}:
        raise HistoricalMappingError(
            f"unsupported risk detail-type: {producer_detail_type}"
        )

    risk_id = _require_text(detail, "risk_id")
    generated_at_utc = _require_text(detail, "generated_at_utc")
    generated_at_epoch = _require_int(detail, "generated_at_epoch")
    event_time_utc, event_time_epoch, year, month, day = _event_clock(
        event_time_utc=generated_at_utc,
        event_time_epoch=generated_at_epoch,
    )

    return RiskFact(
        dataset=Dataset.RISK,
        fact_schema_version=RISK_FACT_SCHEMA_VERSION,
        fact_kind=FactKind.RISK_RESULT,
        record_id=risk_id,
        dedup_id=risk_id,
        event_time_utc=event_time_utc,
        event_time_epoch=event_time_epoch,
        event_year=year,
        event_month=month,
        event_day=day,
        source_system=_optional_text(detail, "source_system")
        or DEFAULT_RISK_SOURCE_SYSTEM,
        producer_source=producer_source,
        producer_detail_type=producer_detail_type,
        producer_schema_version=_optional_text(detail, "schema_version"),
        correlation_id=_optional_text(detail, "correlation_id"),
        risk_id=risk_id,
        encounter_id=_require_text(detail, "encounter_id"),
        aircraft_id=_require_text(detail, "aircraft_id"),
        hazard_id=_require_text(detail, "hazard_id"),
        hazard_source_version=_require_text(detail, "hazard_source_version"),
        projection_id=_require_text(detail, "projection_id"),
        risk_score=_require_number(detail, "risk_score"),
        risk_level=_require_text(detail, "risk_level"),
        generated_at_utc=event_time_utc,
        generated_at_epoch=event_time_epoch,
        scoring_ruleset_version=_require_text(
            detail,
            "scoring_ruleset_version",
        ),
        scoring_config_version=_require_text(
            detail,
            "scoring_config_version",
        ),
        hazard_type=_require_text(detail, "hazard_type"),
        encounter_state=_require_text(detail, "encounter_state"),
        confidence=_optional_text(detail, "confidence"),
        freshness_status=_optional_text(detail, "freshness_status"),
        valid_until_utc=_optional_text(detail, "valid_until_utc"),
    )


def build_hazard_version_fact(
    detail: Mapping[str, Any],
    *,
    producer_source: str,
    producer_detail_type: str,
) -> HazardVersionFact:
    if producer_source != "wilvor.weather":
        raise HistoricalMappingError("unexpected hazard producer_source")
    if producer_detail_type != "hazard.materialized":
        raise HistoricalMappingError(
            f"unsupported hazard detail-type: {producer_detail_type}"
        )

    hazard_id = _require_text(detail, "hazard_id")
    source_version = _require_text(detail, "source_version")
    hazard_version_key = _optional_text(detail, "hazard_version_key") or (
        f"{hazard_id}#{source_version}"
    )
    materialized_at_utc = _require_text(detail, "materialized_at_utc")
    event_time_utc, event_time_epoch, year, month, day = _event_clock(
        event_time_utc=materialized_at_utc,
    )
    geometry_type = _require_text(detail, "geometry_type").upper()

    return HazardVersionFact(
        dataset=Dataset.HAZARD_VERSION,
        fact_schema_version=HAZARD_VERSION_FACT_SCHEMA_VERSION,
        fact_kind=FactKind.HAZARD_VERSION,
        record_id=hazard_version_key,
        dedup_id=hazard_version_key,
        event_time_utc=event_time_utc,
        event_time_epoch=event_time_epoch,
        event_year=year,
        event_month=month,
        event_day=day,
        source_system=_optional_text(detail, "source_system")
        or DEFAULT_HAZARD_SOURCE_SYSTEM,
        producer_source=producer_source,
        producer_detail_type=producer_detail_type,
        producer_schema_version=_optional_text(detail, "schema_version"),
        correlation_id=_optional_text(detail, "correlation_id"),
        hazard_id=hazard_id,
        source_version=source_version,
        hazard_version_key=hazard_version_key,
        materialization_id=_require_text(detail, "materialization_id"),
        geometry_hash=_require_text(detail, "geometry_hash"),
        geometry_type=geometry_type,
        geometry_point_count=_require_int(detail, "geometry_point_count"),
        product_type=_require_text(detail, "product_type"),
        hazard_type=_require_text(detail, "hazard_type"),
        status=_require_text(detail, "status"),
        valid_from_utc=_require_text(detail, "valid_from_utc"),
        valid_to_utc=_require_text(detail, "valid_to_utc"),
        materialized_at_utc=event_time_utc,
        amendment_type=_optional_text(detail, "amendment_type"),
        created_at_utc=_optional_text(detail, "created_at_utc"),
        received_at_utc=_optional_text(detail, "received_at_utc"),
        source_product_id=_optional_text(detail, "source_product_id"),
        severity=_optional_text(detail, "severity"),
        minimum_lower_altitude_ft=_optional_number(
            detail,
            "minimum_lower_altitude_ft",
        ),
        maximum_upper_altitude_ft=_optional_number(
            detail,
            "maximum_upper_altitude_ft",
        ),
        source_icao_id=_optional_text(detail, "source_icao_id"),
        series_id=_optional_text(detail, "series_id"),
        alpha_char=_optional_text(detail, "alpha_char"),
        raw_s3_uri=_optional_text(detail, "raw_s3_uri"),
    )


def build_hazard_geometry_fact(
    *,
    active_hazard: Mapping[str, Any],
    geometry_points: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    producer_source: str = DEFAULT_GEOMETRY_PRODUCER_SOURCE,
    producer_detail_type: str = GEOMETRY_IN_MEMORY_DETAIL_TYPE,
) -> HazardGeometryFact:
    """Build a version-pinned geometry fact from in-memory SIGMET points.

    Transport is intentionally unset in Phase 2A.1a. The default
    producer_detail_type is an in-memory provenance label, not an EventBridge
    detail-type.
    """

    if producer_detail_type != GEOMETRY_IN_MEMORY_DETAIL_TYPE:
        raise HistoricalMappingError(
            "hazard geometry transport is not selected in Phase 2A.1a"
        )

    hazard_id = _require_text(active_hazard, "hazard_id")
    source_version = _require_text(active_hazard, "source_version")
    hazard_version_key = _optional_text(
        active_hazard,
        "hazard_version_key",
    ) or f"{hazard_id}#{source_version}"
    materialized_at_utc = _require_text(active_hazard, "materialized_at_utc")
    event_time_utc, event_time_epoch, year, month, day = _event_clock(
        event_time_utc=materialized_at_utc,
    )
    geometry_type = _require_text(active_hazard, "geometry_type").upper()
    geometry = build_geojson_geometry(
        list(geometry_points),
        geometry_type=geometry_type,
    )
    point_count = active_hazard.get("geometry_point_count")
    if point_count is None:
        geometry_point_count = len(geometry_points)
    else:
        geometry_point_count = _require_int(
            active_hazard,
            "geometry_point_count",
        )
        if geometry_point_count != len(geometry_points):
            raise HistoricalMappingError(
                "geometry_point_count does not match geometry points"
            )

    return HazardGeometryFact(
        dataset=Dataset.HAZARD_GEOMETRY,
        fact_schema_version=HAZARD_GEOMETRY_FACT_SCHEMA_VERSION,
        fact_kind=FactKind.HAZARD_GEOMETRY,
        record_id=hazard_version_key,
        dedup_id=hazard_version_key,
        event_time_utc=event_time_utc,
        event_time_epoch=event_time_epoch,
        event_year=year,
        event_month=month,
        event_day=day,
        source_system=_optional_text(active_hazard, "source_system")
        or DEFAULT_HAZARD_SOURCE_SYSTEM,
        producer_source=producer_source,
        producer_detail_type=producer_detail_type,
        producer_schema_version=_optional_text(active_hazard, "schema_version"),
        correlation_id=_optional_text(active_hazard, "correlation_id"),
        hazard_id=hazard_id,
        source_version=source_version,
        hazard_version_key=hazard_version_key,
        materialization_id=_require_text(active_hazard, "materialization_id"),
        geometry_hash=_require_text(active_hazard, "geometry_hash"),
        geometry_type=geometry_type,
        geometry_point_count=geometry_point_count,
        materialized_at_utc=event_time_utc,
        coordinates_axis=COORDINATES_AXIS_LONLAT,
        geometry=geometry,
    )


def fact_from_event(
    source: str,
    detail_type: str,
    detail: Mapping[str, Any] | None,
) -> EncounterFact | RiskFact | HazardVersionFact:
    if not isinstance(detail, Mapping):
        raise HistoricalMappingError("event detail is not an object")

    producer_source = str(source or "").strip()
    producer_detail_type = str(detail_type or "").strip()

    if (
        producer_source == "wilvor.encounter"
        and producer_detail_type in {"encounter.updated", "encounter.resolved"}
    ):
        return build_encounter_fact(
            detail,
            producer_source=producer_source,
            producer_detail_type=producer_detail_type,
        )
    if (
        producer_source == "wilvor.risk"
        and producer_detail_type in {"risk.updated", "risk.resolved"}
    ):
        return build_risk_fact(
            detail,
            producer_source=producer_source,
            producer_detail_type=producer_detail_type,
        )
    if (
        producer_source == "wilvor.weather"
        and producer_detail_type == "hazard.materialized"
    ):
        return build_hazard_version_fact(
            detail,
            producer_source=producer_source,
            producer_detail_type=producer_detail_type,
        )

    raise HistoricalMappingError(
        "unsupported historical event: "
        f"{producer_source}/{producer_detail_type}"
    )
