"""Deterministic non-geographic operational identity discovery.

Phase 1D.1 discovers observed aircraft, hazard, and AirportStatus identities
from Phase 1B reads and Phase 1A current-set semantics. Callers supply table
handles and one reference epoch.

This module does not acquire wall-clock time, cache, call the Operational
API, compose encounters or impacts, reconstruct geometry, or import AI
contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import current_set
from . import linking
from . import readers


CALLSIGN_CASE_LIMITATION = (
    "Callsign discovery queries the GSI after strip+uppercase normalization "
    "to match Operational API query behavior. Stored callsign values are "
    "writer-controlled (strip-only) and DynamoDB GSI equality is case-sensitive. "
    "This is not case-insensitive or globally complete callsign coverage."
)

KIND_AIRCRAFT = "aircraft"
KIND_HAZARD = "hazard"
KIND_AIRPORT = "airport"


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_aircraft_id(aircraft_id) -> str:
    return _text(aircraft_id).lower()


def _normalize_airport_id(airport_id) -> str:
    return _text(airport_id).upper()


def _normalize_callsign(callsign) -> str:
    return _text(callsign).upper()


def _normalize_filter(value) -> str:
    return _text(value).upper()


def _identity(*pairs: tuple[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple((name, value) for name, value in pairs if value)


@dataclass(frozen=True)
class EntityMatch:
    kind: str
    identity: tuple[tuple[str, str], ...]
    source: dict[str, Any] | None
    is_current: bool


@dataclass(frozen=True)
class AircraftDiscoveryResult:
    matches: tuple[EntityMatch, ...]
    retrieval: tuple[linking.RetrievalObservation, ...]


@dataclass(frozen=True)
class HazardDiscoveryResult:
    matches: tuple[EntityMatch, ...]
    retrieval: tuple[linking.RetrievalObservation, ...]


@dataclass(frozen=True)
class AirportDiscoveryResult:
    matches: tuple[EntityMatch, ...]
    retrieval: tuple[linking.RetrievalObservation, ...]


def _aircraft_match(item: dict[str, Any], *, now_epoch: int) -> EntityMatch:
    aircraft_id = _normalize_aircraft_id(item.get("aircraft_id"))
    return EntityMatch(
        kind=KIND_AIRCRAFT,
        identity=_identity(("aircraft_id", aircraft_id)),
        source=item,
        is_current=current_set.is_current_aircraft(item, now_epoch),
    )


def _hazard_match(item: dict[str, Any], *, now_epoch: int) -> EntityMatch:
    hazard_id = _text(item.get("hazard_id"))
    source_version = _text(item.get("source_version"))
    return EntityMatch(
        kind=KIND_HAZARD,
        identity=_identity(
            ("hazard_id", hazard_id),
            ("source_version", source_version),
        ),
        source=item,
        is_current=current_set.is_current_hazard(item, now_epoch),
    )


def _airport_match(item: dict[str, Any], *, now_epoch: int) -> EntityMatch:
    airport_id = _normalize_airport_id(item.get("airport_id"))
    return EntityMatch(
        kind=KIND_AIRPORT,
        identity=_identity(("airport_id", airport_id)),
        source=item,
        is_current=current_set.is_current_airport_status(item, now_epoch),
    )


def _empty_aircraft(*observations: linking.RetrievalObservation):
    return AircraftDiscoveryResult(matches=(), retrieval=observations)


def _empty_airport(*observations: linking.RetrievalObservation):
    return AirportDiscoveryResult(matches=(), retrieval=observations)


def _hazard_passes_type_filters(
    item: dict[str, Any],
    *,
    product_type: str,
    hazard_type: str,
) -> bool:
    if product_type and _normalize_filter(item.get("product_type")) != product_type:
        return False
    if hazard_type and _normalize_filter(item.get("hazard_type")) != hazard_type:
        return False
    return True


def discover_aircraft_by_id(tables, aircraft_id, *, now_epoch):
    aircraft_id = _normalize_aircraft_id(aircraft_id)
    if not aircraft_id:
        return _empty_aircraft()

    item = readers.get_aircraft_record(tables.aircraft, aircraft_id)
    observation = linking.exact_pk_observation("get_aircraft_record")
    if item is None:
        return _empty_aircraft(observation)
    return AircraftDiscoveryResult(
        matches=(_aircraft_match(item, now_epoch=now_epoch),),
        retrieval=(observation,),
    )


def discover_aircraft_by_callsign(tables, callsign, *, now_epoch):
    callsign = _normalize_callsign(callsign)
    if not callsign:
        return _empty_aircraft()
    observation = linking.query_observation(
        "query_aircraft_by_callsign",
        CALLSIGN_CASE_LIMITATION,
    )

    candidates = readers.query_aircraft_by_callsign(
        tables.aircraft,
        callsign=callsign,
        now_epoch=now_epoch,
    )
    matches = tuple(
        _aircraft_match(item, now_epoch=now_epoch)
        for item in candidates
        if current_set.is_current_aircraft(item, now_epoch)
    )
    return AircraftDiscoveryResult(matches=matches, retrieval=(observation,))


def discover_aircraft_by_h3(tables, h3_cell, *, now_epoch):
    h3_cell = _text(h3_cell)
    if not h3_cell:
        return _empty_aircraft()
    observation = linking.query_observation("query_aircraft_by_h3")

    candidates = readers.query_aircraft_by_h3(
        tables.aircraft,
        h3_cell=h3_cell,
        now_epoch=now_epoch,
    )
    matches = tuple(
        _aircraft_match(item, now_epoch=now_epoch)
        for item in candidates
        if current_set.is_current_aircraft(item, now_epoch)
    )
    return AircraftDiscoveryResult(matches=matches, retrieval=(observation,))


def discover_current_hazards(
    tables,
    *,
    now_epoch,
    product_type=None,
    hazard_type=None,
    hazard_ids=None,
):
    product_type = _normalize_filter(product_type) if product_type is not None else ""
    hazard_type = _normalize_filter(hazard_type) if hazard_type is not None else ""

    if hazard_ids is not None:
        matches = []
        retrieval = []
        for raw_id in hazard_ids:
            hazard_id = _text(raw_id)
            if not hazard_id:
                continue
            retrieval.append(linking.exact_pk_observation("get_hazard_record"))
            item = readers.get_hazard_record(tables.hazards, hazard_id)
            if item is None:
                continue
            if not _hazard_passes_type_filters(
                item,
                product_type=product_type,
                hazard_type=hazard_type,
            ):
                continue
            matches.append(_hazard_match(item, now_epoch=now_epoch))
        return HazardDiscoveryResult(
            matches=tuple(matches),
            retrieval=tuple(retrieval),
        )

    observation = linking.query_observation("query_active_hazard_candidates")
    candidates = readers.query_active_hazard_candidates(
        tables.hazards,
        now_epoch=now_epoch,
    )
    matches = tuple(
        _hazard_match(item, now_epoch=now_epoch)
        for item in candidates
        if current_set.is_current_hazard(item, now_epoch)
        and _hazard_passes_type_filters(
            item,
            product_type=product_type,
            hazard_type=hazard_type,
        )
    )
    return HazardDiscoveryResult(matches=matches, retrieval=(observation,))


def discover_airport_by_id(tables, airport_id, *, now_epoch):
    airport_id = _normalize_airport_id(airport_id)
    if not airport_id:
        return _empty_airport()

    item = readers.get_airport_status_record(tables.airports, airport_id)
    observation = linking.exact_pk_observation("get_airport_status_record")
    if item is None:
        return _empty_airport(observation)
    return AirportDiscoveryResult(
        matches=(_airport_match(item, now_epoch=now_epoch),),
        retrieval=(observation,),
    )


def discover_airports_by_weather_risk(tables, weather_risk, *, now_epoch):
    weather_risk = _text(weather_risk)
    if not weather_risk:
        return _empty_airport()
    observation = linking.query_observation("query_airports_by_risk")

    candidates = readers.query_airports_by_risk(
        tables.airports,
        weather_risk=weather_risk,
        now_epoch=now_epoch,
    )
    matches = tuple(
        _airport_match(item, now_epoch=now_epoch)
        for item in candidates
        if current_set.is_current_airport_status(item, now_epoch)
    )
    return AirportDiscoveryResult(matches=matches, retrieval=(observation,))


def discover_airports_by_weather_impact(tables, weather_impact, *, now_epoch):
    weather_impact = _text(weather_impact)
    if not weather_impact:
        return _empty_airport()
    observation = linking.query_observation("query_airports_by_impact")

    candidates = readers.query_airports_by_impact(
        tables.airports,
        weather_impact=weather_impact,
        now_epoch=now_epoch,
    )
    matches = tuple(
        _airport_match(item, now_epoch=now_epoch)
        for item in candidates
        if current_set.is_current_airport_status(item, now_epoch)
    )
    return AirportDiscoveryResult(matches=matches, retrieval=(observation,))
