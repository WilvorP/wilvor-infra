"""Persisted METAR/TAF context linked from airport-assessment citations.

This module copies weather rows that a stored airport assessment already
named. It does not poll weather, rescore an airport, or treat a persisted
METAR/TAF as a current operational observation. A missing row is not VFR,
not clear weather, and not LOW weather risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from wilvor_ai.contracts import (
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    SourceRecord,
    TemporalScope,
    _validate_utc,
)
from wilvor_ai.persisted_airport_contracts import PersistedAirportCandidate
from wilvor_operational import readers


SPRINT4_DECISION_WEATHER_SCHEMA_VERSION = (
    "wilvor.ai.sprint4.decision_weather_context.v1"
)
PERSISTED_WEATHER_IS_CURRENT_OBSERVATION = False
MISSING_WEATHER_IS_VFR = False
MISSING_WEATHER_IS_LOW_RISK = False

NOT_CURRENT_WEATHER_LIMITATION = (
    "Persisted METAR/TAF context is not a current operational observation."
)
MISSING_WEATHER_LIMITATION = (
    "A missing METAR or TAF record is not VFR, not clear weather, and not "
    "LOW weather risk."
)
VERSION_MISMATCH_LIMITATION = (
    "Persisted METAR/TAF version does not match the stored "
    "airport-assessment citation. Versions were not repaired."
)
_READ_FAILED = "The persisted METAR or TAF read failed."
_SOURCE_METAR = "get_metar_record"
_SOURCE_TAF = "get_taf_record"


class WeatherContextStatus(str, Enum):
    """Outcome of one persisted weather lookup. Not a flight category."""

    MATCHED = "MATCHED"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    MISSING = "MISSING"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_CITED = "NOT_CITED"
    STATION_UNRESOLVED = "STATION_UNRESOLVED"


STATION_UNRESOLVED_TOKEN = "UNRESOLVED"
STATION_UNRESOLVED_LIMITATION = (
    "AirportStatus.station_id was not resolved. airport_id was not used as "
    "a METAR/TAF lookup key."
)


def _closed() -> None:
    if (
        PERSISTED_WEATHER_IS_CURRENT_OBSERVATION is not False
        or MISSING_WEATHER_IS_VFR is not False
        or MISSING_WEATHER_IS_LOW_RISK is not False
    ):
        raise ContractValidationError("persisted_weather_is_not_current")


def _text(value: Any) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    return value


def _utc_or_none(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if _validate_utc(value, "event_timestamp_utc"):
        return None
    return value


@dataclass(frozen=True)
class Sprint4WeatherProductContext:
    """One METAR or TAF citation plus the persisted row, if any."""

    product: str
    airport_id: str
    station_id: str
    cited_version: str | None
    observed_version: str | None
    event_timestamp_utc: str | None
    status: WeatherContextStatus
    is_current_operational_observation: bool = False
    flight_category_inferred: bool = False

    def __post_init__(self) -> None:
        _closed()
        if self.product not in {"METAR", "TAF"}:
            raise ContractValidationError("invalid_weather_product")
        if not self.airport_id or self.airport_id != self.airport_id.strip():
            raise ContractValidationError("invalid_airport_id")
        if not self.station_id or self.station_id != self.station_id.strip():
            raise ContractValidationError("invalid_station_id")
        if not isinstance(self.status, WeatherContextStatus):
            raise ContractValidationError("invalid_weather_status")
        if self.is_current_operational_observation is not False:
            raise ContractValidationError("persisted_weather_is_not_current")
        if self.flight_category_inferred is not False:
            raise ContractValidationError("missing_weather_is_not_vfr")
        if self.status is WeatherContextStatus.MISSING and (
            self.observed_version is not None or self.event_timestamp_utc is not None
        ):
            raise ContractValidationError("missing_weather_has_no_observation")


@dataclass(frozen=True)
class Sprint4AirportWeatherContext:
    """METAR and TAF context for one persisted airport candidate."""

    airport_id: str
    airport_assessment_id: str
    metar: Sprint4WeatherProductContext
    taf: Sprint4WeatherProductContext
    evidence: tuple[Evidence, ...]
    temporal_scope: TemporalScope = TemporalScope.PERSISTED
    schema_version: str = SPRINT4_DECISION_WEATHER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _closed()
        if self.schema_version != SPRINT4_DECISION_WEATHER_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if self.temporal_scope is not TemporalScope.PERSISTED:
            raise ContractValidationError("persisted_weather_temporal_scope")
        if self.metar.airport_id != self.airport_id or self.taf.airport_id != self.airport_id:
            raise ContractValidationError("weather_airport_identity_mismatch")
        if any(item.temporal_scope is not TemporalScope.PERSISTED for item in self.evidence):
            raise ContractValidationError("persisted_weather_temporal_scope")
        if any(item.freshness_status is not FreshnessStatus.UNKNOWN for item in self.evidence):
            raise ContractValidationError("persisted_weather_freshness_not_reevaluated")


def load_persisted_airport_weather_context(
    tables: Any,
    candidates: tuple[PersistedAirportCandidate, ...],
    *,
    query_timestamp_utc: str,
    tool_call_id: str,
) -> tuple[Sprint4AirportWeatherContext, ...]:
    """Fetch cited METAR/TAF rows. Absence is not a weather classification."""

    _closed()
    errors = _validate_utc(query_timestamp_utc, "query_timestamp_utc")
    if errors:
        raise ContractValidationError(errors)
    if (
        not isinstance(tool_call_id, str)
        or not tool_call_id
        or tool_call_id != tool_call_id.strip()
    ):
        raise ContractValidationError("invalid_tool_call_id")
    if tables is None:
        raise ContractValidationError("invalid_tables")
    if not isinstance(candidates, tuple) or any(
        not isinstance(item, PersistedAirportCandidate) for item in candidates
    ):
        raise ContractValidationError("invalid_candidates")
    return tuple(
        _one_candidate(tables, item, query_timestamp_utc, tool_call_id)
        for item in candidates
    )


def _resolve_station_id(tables: Any, airport_id: str) -> tuple[str | None, WeatherContextStatus | None]:
    """Resolve METAR/TAF station from AirportStatus. airport_id is not a fallback."""

    airports = getattr(tables, "airports", None)
    if airports is None:
        return None, WeatherContextStatus.UNAVAILABLE
    try:
        row = readers.get_airport_status_record(airports, airport_id)
    except Exception:
        return None, WeatherContextStatus.UNAVAILABLE
    if not isinstance(row, dict):
        return None, WeatherContextStatus.STATION_UNRESOLVED
    station_id = _text(row.get("station_id"))
    if station_id is None:
        return None, WeatherContextStatus.STATION_UNRESOLVED
    return station_id, None


def _unresolved_product(
    *,
    product: str,
    airport_id: str,
    cited_version: str | None,
    status: WeatherContextStatus,
) -> Sprint4WeatherProductContext:
    if cited_version is None:
        return Sprint4WeatherProductContext(
            product=product,
            airport_id=airport_id,
            station_id=STATION_UNRESOLVED_TOKEN,
            cited_version=None,
            observed_version=None,
            event_timestamp_utc=None,
            status=WeatherContextStatus.NOT_CITED,
        )
    return Sprint4WeatherProductContext(
        product=product,
        airport_id=airport_id,
        station_id=STATION_UNRESOLVED_TOKEN,
        cited_version=cited_version,
        observed_version=None,
        event_timestamp_utc=None,
        status=status,
    )


def _one_candidate(
    tables: Any,
    candidate: PersistedAirportCandidate,
    query_timestamp_utc: str,
    tool_call_id: str,
) -> Sprint4AirportWeatherContext:
    station_id, unresolved = _resolve_station_id(tables, candidate.airport_id)
    if unresolved is not None:
        metar = _unresolved_product(
            product="METAR",
            airport_id=candidate.airport_id,
            cited_version=candidate.metar_version,
            status=unresolved,
        )
        taf = _unresolved_product(
            product="TAF",
            airport_id=candidate.airport_id,
            cited_version=candidate.taf_version,
            status=unresolved,
        )
        evidence = tuple(
            item
            for item in (
                _evidence(query_timestamp_utc, tool_call_id, metar, _SOURCE_METAR),
                _evidence(query_timestamp_utc, tool_call_id, taf, _SOURCE_TAF),
            )
            if item is not None
        )
        return Sprint4AirportWeatherContext(
            airport_id=candidate.airport_id,
            airport_assessment_id=candidate.airport_assessment_id,
            metar=metar,
            taf=taf,
            evidence=evidence,
        )
    metar = _product(
        tables,
        product="METAR",
        airport_id=candidate.airport_id,
        station_id=station_id,
        cited_version=candidate.metar_version,
        reader=readers.get_metar_record,
        table=getattr(tables, "metar", None),
        version_fields=("metar_version",),
        time_fields=("observed_time_utc", "observation_time_utc"),
    )
    taf = _product(
        tables,
        product="TAF",
        airport_id=candidate.airport_id,
        station_id=station_id,
        cited_version=candidate.taf_version,
        reader=readers.get_taf_record,
        table=getattr(tables, "taf", None),
        version_fields=("taf_version", "source_version"),
        time_fields=("issued_at_utc",),
    )
    evidence = tuple(
        item
        for item in (
            _evidence(query_timestamp_utc, tool_call_id, metar, _SOURCE_METAR),
            _evidence(query_timestamp_utc, tool_call_id, taf, _SOURCE_TAF),
        )
        if item is not None
    )
    return Sprint4AirportWeatherContext(
        airport_id=candidate.airport_id,
        airport_assessment_id=candidate.airport_assessment_id,
        metar=metar,
        taf=taf,
        evidence=evidence,
    )


def _product(
    tables: Any,
    *,
    product: str,
    airport_id: str,
    station_id: str,
    cited_version: str | None,
    reader: Any,
    table: Any,
    version_fields: tuple[str, ...],
    time_fields: tuple[str, ...],
) -> Sprint4WeatherProductContext:
    del tables
    if cited_version is None:
        return Sprint4WeatherProductContext(
            product=product,
            airport_id=airport_id,
            station_id=station_id,
            cited_version=None,
            observed_version=None,
            event_timestamp_utc=None,
            status=WeatherContextStatus.NOT_CITED,
        )
    if table is None:
        return Sprint4WeatherProductContext(
            product=product,
            airport_id=airport_id,
            station_id=station_id,
            cited_version=cited_version,
            observed_version=None,
            event_timestamp_utc=None,
            status=WeatherContextStatus.UNAVAILABLE,
        )
    try:
        row = reader(table, station_id)
    except Exception:
        return Sprint4WeatherProductContext(
            product=product,
            airport_id=airport_id,
            station_id=station_id,
            cited_version=cited_version,
            observed_version=None,
            event_timestamp_utc=None,
            status=WeatherContextStatus.UNAVAILABLE,
        )
    if not isinstance(row, dict):
        return Sprint4WeatherProductContext(
            product=product,
            airport_id=airport_id,
            station_id=station_id,
            cited_version=cited_version,
            observed_version=None,
            event_timestamp_utc=None,
            status=WeatherContextStatus.MISSING,
        )
    observed_station = _text(row.get("station_id")) or _text(row.get("airport_id"))
    if observed_station is not None and observed_station != station_id:
        return Sprint4WeatherProductContext(
            product=product,
            airport_id=airport_id,
            station_id=station_id,
            cited_version=cited_version,
            observed_version=None,
            event_timestamp_utc=None,
            status=WeatherContextStatus.VERSION_MISMATCH,
        )
    observed_version = None
    for field_name in version_fields:
        observed_version = _text(row.get(field_name))
        if observed_version is not None:
            break
    event_timestamp_utc = None
    for field_name in time_fields:
        event_timestamp_utc = _utc_or_none(row.get(field_name))
        if event_timestamp_utc is not None:
            break
    if observed_version is None or observed_version != cited_version:
        return Sprint4WeatherProductContext(
            product=product,
            airport_id=airport_id,
            station_id=station_id,
            cited_version=cited_version,
            observed_version=observed_version,
            event_timestamp_utc=event_timestamp_utc,
            status=WeatherContextStatus.VERSION_MISMATCH,
        )
    return Sprint4WeatherProductContext(
        product=product,
        airport_id=airport_id,
        station_id=station_id,
        cited_version=cited_version,
        observed_version=observed_version,
        event_timestamp_utc=event_timestamp_utc,
        status=WeatherContextStatus.MATCHED,
    )


def _evidence(
    query_timestamp_utc: str,
    tool_call_id: str,
    product: Sprint4WeatherProductContext,
    source: str,
) -> Evidence | None:
    if product.status is WeatherContextStatus.NOT_CITED:
        return None
    records: tuple[SourceRecord, ...] = ()
    limitations = [NOT_CURRENT_WEATHER_LIMITATION]
    if product.status is WeatherContextStatus.MISSING:
        limitations.append(MISSING_WEATHER_LIMITATION)
    elif product.status is WeatherContextStatus.VERSION_MISMATCH:
        limitations.append(VERSION_MISMATCH_LIMITATION)
    elif product.status is WeatherContextStatus.UNAVAILABLE:
        limitations.append(_READ_FAILED)
    elif product.status is WeatherContextStatus.STATION_UNRESOLVED:
        limitations.append(STATION_UNRESOLVED_LIMITATION)
    else:
        records = (
            SourceRecord(
                record_id=product.station_id,
                source_version=product.observed_version,
                event_timestamp_utc=product.event_timestamp_utc,
            ),
        )
    return Evidence(
        source=source,
        source_records=records,
        query_timestamp_utc=query_timestamp_utc,
        freshness_status=FreshnessStatus.UNKNOWN,
        confidence=ConfidenceLevel.UNKNOWN,
        limitations=tuple(limitations),
        tool_call_id=tool_call_id,
        temporal_scope=TemporalScope.PERSISTED,
    )
