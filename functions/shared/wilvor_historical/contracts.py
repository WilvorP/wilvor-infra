"""Versioned historical operational fact contracts.

These frozen dataclasses are the historical schema, independent of EventBridge
payload evolution and of DynamoDB current-set semantics.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping

from .time import resolve_event_time


ENCOUNTER_FACT_SCHEMA_VERSION = "wilvor.historical.encounter_fact.v1"
HAZARD_VERSION_FACT_SCHEMA_VERSION = (
    "wilvor.historical.hazard_version_fact.v1"
)
HAZARD_GEOMETRY_FACT_SCHEMA_VERSION = (
    "wilvor.historical.hazard_geometry_fact.v1"
)
RISK_FACT_SCHEMA_VERSION = "wilvor.historical.risk_fact.v1"
COORDINATES_AXIS_LONLAT = "lonlat"


class HistoricalFactError(ValueError):
    """Raised when a historical fact is structurally invalid."""


class Dataset(str, Enum):
    ENCOUNTER = "encounter"
    HAZARD_VERSION = "hazard_version"
    HAZARD_GEOMETRY = "hazard_geometry"
    RISK = "risk"


class FactKind(str, Enum):
    ENCOUNTER_OBSERVED = "ENCOUNTER_OBSERVED"
    ENCOUNTER_TERMINAL = "ENCOUNTER_TERMINAL"
    HAZARD_VERSION = "HAZARD_VERSION"
    HAZARD_GEOMETRY = "HAZARD_GEOMETRY"
    RISK_RESULT = "RISK_RESULT"


def json_safe(value: Any) -> Any:
    """Return a JSON-compatible value. Decimal does not escape."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise HistoricalFactError("non-finite float is not JSON-safe")
        return value
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    raise HistoricalFactError(
        f"value of type {type(value).__name__} is not JSON-safe"
    )


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalFactError(f"missing {field_name}")
    return value.strip()


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HistoricalFactError(f"invalid {field_name}")
    text = value.strip()
    return text or None


def _require_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise HistoricalFactError(f"invalid {field_name}")


def _optional_bool(value: Any, field_name: str) -> bool | None:
    if value is None:
        return None
    return _require_bool(value, field_name)


def as_json_number(value: Any, field_name: str) -> int | float:
    if isinstance(value, bool) or value is None:
        raise HistoricalFactError(f"invalid {field_name}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise HistoricalFactError(f"invalid {field_name}")
        return value
    if isinstance(value, Decimal):
        return json_safe(value)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            if "." in text or "e" in text.lower():
                number = float(text)
            else:
                number = int(text)
        except ValueError as exc:
            raise HistoricalFactError(f"invalid {field_name}") from exc
        return json_safe(number)
    raise HistoricalFactError(f"invalid {field_name}")


def _validate_envelope(
    *,
    dataset: Dataset,
    fact_schema_version: str,
    expected_schema: str,
    fact_kind: FactKind,
    allowed_kinds: set[FactKind],
    record_id: str,
    dedup_id: str,
    event_time_utc: str,
    event_time_epoch: int,
    event_year: str,
    event_month: str,
    event_day: str,
    source_system: str,
    producer_source: str,
    producer_detail_type: str,
    producer_schema_version: str | None,
    correlation_id: str | None,
) -> None:
    if fact_schema_version != expected_schema:
        raise HistoricalFactError("invalid fact_schema_version")
    if fact_kind not in allowed_kinds:
        raise HistoricalFactError("invalid fact_kind")
    _require_text(record_id, "record_id")
    _require_text(dedup_id, "dedup_id")
    _require_text(source_system, "source_system")
    _require_text(producer_source, "producer_source")
    _require_text(producer_detail_type, "producer_detail_type")
    _optional_text(producer_schema_version, "producer_schema_version")
    _optional_text(correlation_id, "correlation_id")

    canonical_utc, epoch, year, month, day = resolve_event_time(
        event_time_utc=event_time_utc,
        event_time_epoch=event_time_epoch,
    )
    if event_time_utc != canonical_utc:
        raise HistoricalFactError("event_time_utc must be canonical UTC Z")
    if int(event_time_epoch) != epoch:
        raise HistoricalFactError("event_time_epoch does not match event_time_utc")
    if (event_year, event_month, event_day) != (year, month, day):
        raise HistoricalFactError(
            "partition fields must be derived from event_time_utc"
        )
    if dataset is Dataset.ENCOUNTER and not record_id:
        raise HistoricalFactError("missing record_id")


def _envelope_dict(fact: Any) -> dict[str, Any]:
    payload = asdict(fact)
    payload["dataset"] = fact.dataset.value
    payload["fact_kind"] = fact.fact_kind.value
    return {key: json_safe(value) for key, value in payload.items()}


@dataclass(frozen=True)
class EncounterFact:
    dataset: Dataset
    fact_schema_version: str
    fact_kind: FactKind
    record_id: str
    dedup_id: str
    event_time_utc: str
    event_time_epoch: int
    event_year: str
    event_month: str
    event_day: str
    source_system: str
    producer_source: str
    producer_detail_type: str
    producer_schema_version: str | None
    correlation_id: str | None
    encounter_id: str
    aircraft_id: str
    aircraft_state_version: str | None
    projection_id: str
    hazard_id: str
    hazard_source_version: str
    hazard_version_key: str
    encounter_state: str
    geometry_overlap_status: str | None
    time_overlap_status: str | None
    altitude_overlap_status: str | None
    exact_intersection_confirmed: bool
    detected_at_epoch: int
    detected_at_utc: str
    resolution_reason: str | None
    geometry_hash: str
    hazard_type: str | None
    inside_now: bool
    corridor_intersects: bool
    resolved_at_epoch: int | None
    resolved_at_utc: str | None

    def __post_init__(self) -> None:
        if self.dataset is not Dataset.ENCOUNTER:
            raise HistoricalFactError("invalid dataset")
        _validate_envelope(
            dataset=self.dataset,
            fact_schema_version=self.fact_schema_version,
            expected_schema=ENCOUNTER_FACT_SCHEMA_VERSION,
            fact_kind=self.fact_kind,
            allowed_kinds={
                FactKind.ENCOUNTER_OBSERVED,
                FactKind.ENCOUNTER_TERMINAL,
            },
            record_id=self.record_id,
            dedup_id=self.dedup_id,
            event_time_utc=self.event_time_utc,
            event_time_epoch=self.event_time_epoch,
            event_year=self.event_year,
            event_month=self.event_month,
            event_day=self.event_day,
            source_system=self.source_system,
            producer_source=self.producer_source,
            producer_detail_type=self.producer_detail_type,
            producer_schema_version=self.producer_schema_version,
            correlation_id=self.correlation_id,
        )
        if self.record_id != self.encounter_id:
            raise HistoricalFactError("record_id must equal encounter_id")
        if not self.aircraft_state_version:
            raise HistoricalFactError("missing aircraft_state_version")
        if not self.hazard_type:
            raise HistoricalFactError("missing hazard_type")
        if self.event_time_utc != self.detected_at_utc:
            raise HistoricalFactError(
                "canonical encounter event time is detected_at_utc"
            )
        if int(self.event_time_epoch) != int(self.detected_at_epoch):
            raise HistoricalFactError(
                "canonical encounter event time is detected_at_epoch"
            )
        if self.fact_kind is FactKind.ENCOUNTER_TERMINAL:
            if self.resolved_at_epoch is None or not self.resolved_at_utc:
                raise HistoricalFactError(
                    "ENCOUNTER_TERMINAL requires resolved_at"
                )
        else:
            expected = (
                f"{self.encounter_id}|ENCOUNTER_OBSERVED|"
                f"{int(self.detected_at_epoch)}|{self.encounter_state}"
            )
            if self.dedup_id != expected:
                raise HistoricalFactError("invalid encounter dedup_id")
            return
        expected_terminal = (
            f"{self.encounter_id}|ENCOUNTER_TERMINAL|"
            f"{int(self.detected_at_epoch)}|{self.encounter_state}|"
            f"{int(self.resolved_at_epoch)}"
        )
        if self.dedup_id != expected_terminal:
            raise HistoricalFactError("invalid encounter dedup_id")

    def to_dict(self) -> dict[str, Any]:
        return _envelope_dict(self)


@dataclass(frozen=True)
class RiskFact:
    dataset: Dataset
    fact_schema_version: str
    fact_kind: FactKind
    record_id: str
    dedup_id: str
    event_time_utc: str
    event_time_epoch: int
    event_year: str
    event_month: str
    event_day: str
    source_system: str
    producer_source: str
    producer_detail_type: str
    producer_schema_version: str | None
    correlation_id: str | None
    risk_id: str
    encounter_id: str
    aircraft_id: str
    hazard_id: str
    hazard_source_version: str
    projection_id: str
    risk_score: int | float
    risk_level: str
    generated_at_utc: str
    generated_at_epoch: int
    scoring_ruleset_version: str
    scoring_config_version: str
    hazard_type: str | None
    encounter_state: str
    confidence: str | None
    freshness_status: str | None
    valid_until_utc: str | None

    def __post_init__(self) -> None:
        if self.dataset is not Dataset.RISK:
            raise HistoricalFactError("invalid dataset")
        _validate_envelope(
            dataset=self.dataset,
            fact_schema_version=self.fact_schema_version,
            expected_schema=RISK_FACT_SCHEMA_VERSION,
            fact_kind=self.fact_kind,
            allowed_kinds={FactKind.RISK_RESULT},
            record_id=self.record_id,
            dedup_id=self.dedup_id,
            event_time_utc=self.event_time_utc,
            event_time_epoch=self.event_time_epoch,
            event_year=self.event_year,
            event_month=self.event_month,
            event_day=self.event_day,
            source_system=self.source_system,
            producer_source=self.producer_source,
            producer_detail_type=self.producer_detail_type,
            producer_schema_version=self.producer_schema_version,
            correlation_id=self.correlation_id,
        )
        if self.record_id != self.risk_id or self.dedup_id != self.risk_id:
            raise HistoricalFactError("risk record_id and dedup_id must equal risk_id")
        if not self.hazard_type:
            raise HistoricalFactError("missing hazard_type")
        if self.event_time_utc != self.generated_at_utc:
            raise HistoricalFactError(
                "canonical risk event time is generated_at_utc"
            )
        if int(self.event_time_epoch) != int(self.generated_at_epoch):
            raise HistoricalFactError(
                "canonical risk event time is generated_at_epoch"
            )

    def to_dict(self) -> dict[str, Any]:
        return _envelope_dict(self)


@dataclass(frozen=True)
class HazardVersionFact:
    dataset: Dataset
    fact_schema_version: str
    fact_kind: FactKind
    record_id: str
    dedup_id: str
    event_time_utc: str
    event_time_epoch: int
    event_year: str
    event_month: str
    event_day: str
    source_system: str
    producer_source: str
    producer_detail_type: str
    producer_schema_version: str | None
    correlation_id: str | None
    hazard_id: str
    source_version: str
    hazard_version_key: str
    materialization_id: str
    geometry_hash: str
    geometry_type: str
    geometry_point_count: int
    product_type: str
    hazard_type: str
    status: str
    valid_from_utc: str
    valid_to_utc: str
    materialized_at_utc: str
    amendment_type: str | None
    created_at_utc: str | None
    received_at_utc: str | None
    source_product_id: str | None
    severity: str | None
    minimum_lower_altitude_ft: int | float | None
    maximum_upper_altitude_ft: int | float | None
    source_icao_id: str | None
    series_id: str | None
    alpha_char: str | None
    raw_s3_uri: str | None

    def __post_init__(self) -> None:
        if self.dataset is not Dataset.HAZARD_VERSION:
            raise HistoricalFactError("invalid dataset")
        _validate_envelope(
            dataset=self.dataset,
            fact_schema_version=self.fact_schema_version,
            expected_schema=HAZARD_VERSION_FACT_SCHEMA_VERSION,
            fact_kind=self.fact_kind,
            allowed_kinds={FactKind.HAZARD_VERSION},
            record_id=self.record_id,
            dedup_id=self.dedup_id,
            event_time_utc=self.event_time_utc,
            event_time_epoch=self.event_time_epoch,
            event_year=self.event_year,
            event_month=self.event_month,
            event_day=self.event_day,
            source_system=self.source_system,
            producer_source=self.producer_source,
            producer_detail_type=self.producer_detail_type,
            producer_schema_version=self.producer_schema_version,
            correlation_id=self.correlation_id,
        )
        expected_key = f"{self.hazard_id}#{self.source_version}"
        if self.hazard_version_key != expected_key:
            raise HistoricalFactError("hazard_version_key mismatch")
        if self.record_id != expected_key or self.dedup_id != expected_key:
            raise HistoricalFactError(
                "hazard version record_id and dedup_id must equal hazard_version_key"
            )
        if self.event_time_utc != self.materialized_at_utc:
            raise HistoricalFactError(
                "canonical hazard version event time is materialized_at_utc"
            )
        if self.geometry_type not in {"POLYGON", "MULTIPOLYGON"}:
            raise HistoricalFactError("unsupported geometry_type")
        if int(self.geometry_point_count) < 0:
            raise HistoricalFactError("invalid geometry_point_count")

    def to_dict(self) -> dict[str, Any]:
        return _envelope_dict(self)


@dataclass(frozen=True)
class HazardGeometryFact:
    dataset: Dataset
    fact_schema_version: str
    fact_kind: FactKind
    record_id: str
    dedup_id: str
    event_time_utc: str
    event_time_epoch: int
    event_year: str
    event_month: str
    event_day: str
    source_system: str
    producer_source: str
    producer_detail_type: str
    producer_schema_version: str | None
    correlation_id: str | None
    hazard_id: str
    source_version: str
    hazard_version_key: str
    materialization_id: str
    geometry_hash: str
    geometry_type: str
    geometry_point_count: int
    materialized_at_utc: str
    coordinates_axis: str
    geometry: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.dataset is not Dataset.HAZARD_GEOMETRY:
            raise HistoricalFactError("invalid dataset")
        _validate_envelope(
            dataset=self.dataset,
            fact_schema_version=self.fact_schema_version,
            expected_schema=HAZARD_GEOMETRY_FACT_SCHEMA_VERSION,
            fact_kind=self.fact_kind,
            allowed_kinds={FactKind.HAZARD_GEOMETRY},
            record_id=self.record_id,
            dedup_id=self.dedup_id,
            event_time_utc=self.event_time_utc,
            event_time_epoch=self.event_time_epoch,
            event_year=self.event_year,
            event_month=self.event_month,
            event_day=self.event_day,
            source_system=self.source_system,
            producer_source=self.producer_source,
            producer_detail_type=self.producer_detail_type,
            producer_schema_version=self.producer_schema_version,
            correlation_id=self.correlation_id,
        )
        expected_key = f"{self.hazard_id}#{self.source_version}"
        if self.hazard_version_key != expected_key:
            raise HistoricalFactError("hazard_version_key mismatch")
        if self.record_id != expected_key or self.dedup_id != expected_key:
            raise HistoricalFactError(
                "hazard geometry record_id and dedup_id must equal hazard_version_key"
            )
        if self.event_time_utc != self.materialized_at_utc:
            raise HistoricalFactError(
                "canonical hazard geometry event time is materialized_at_utc"
            )
        if self.coordinates_axis != COORDINATES_AXIS_LONLAT:
            raise HistoricalFactError("coordinates_axis must be lonlat")
        if self.geometry_type not in {"POLYGON", "MULTIPOLYGON"}:
            raise HistoricalFactError("unsupported geometry_type")
        if not isinstance(self.geometry, Mapping):
            raise HistoricalFactError("geometry must be an object")
        geo_type = str(self.geometry.get("type") or "")
        expected_geo = (
            "Polygon" if self.geometry_type == "POLYGON" else "MultiPolygon"
        )
        if geo_type != expected_geo:
            raise HistoricalFactError("geometry type does not match geometry_type")
        object.__setattr__(self, "geometry", json_safe(dict(self.geometry)))

    def to_dict(self) -> dict[str, Any]:
        return _envelope_dict(self)


def encounter_dedup_id(
    *,
    encounter_id: str,
    fact_kind: FactKind,
    detected_at_epoch: int,
    encounter_state: str,
    resolved_at_epoch: int | None = None,
) -> str:
    if fact_kind is FactKind.ENCOUNTER_OBSERVED:
        return (
            f"{encounter_id}|ENCOUNTER_OBSERVED|"
            f"{int(detected_at_epoch)}|{encounter_state}"
        )
    if fact_kind is FactKind.ENCOUNTER_TERMINAL:
        if resolved_at_epoch is None:
            raise HistoricalFactError(
                "ENCOUNTER_TERMINAL dedup_id requires resolved_at_epoch"
            )
        return (
            f"{encounter_id}|ENCOUNTER_TERMINAL|"
            f"{int(detected_at_epoch)}|{encounter_state}|"
            f"{int(resolved_at_epoch)}"
        )
    raise HistoricalFactError("invalid encounter fact_kind")
