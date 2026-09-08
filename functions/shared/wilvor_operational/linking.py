"""Pure deterministic operational relationship composition.

This module composes already-retrieved records using Phase 1A current-set
semantics and explicit persisted identities. It performs no DynamoDB access,
wall-clock reads, caching, environment reads, or AWS I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from . import current_set


class Coverage(str, Enum):
    FULL_SCAN = "FULL_SCAN"
    BOUNDED_QUERY = "BOUNDED_QUERY"
    EXACT_PK = "EXACT_PK"
    FULL_QUERY = "FULL_QUERY"


class Consistency(str, Enum):
    EVENTUAL = "EVENTUAL"
    CONSISTENT = "CONSISTENT"


class LinkState(str, Enum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    ABSENT_FROM_CURRENT_CANDIDATES = "ABSENT_FROM_CURRENT_CANDIDATES"
    NOT_CURRENT = "NOT_CURRENT"
    HYDRATION_MISSING = "HYDRATION_MISSING"
    HYDRATION_VERSION_MISMATCH = "HYDRATION_VERSION_MISMATCH"
    HYDRATION_IDENTITY_MISMATCH = "HYDRATION_IDENTITY_MISMATCH"
    HYDRATION_NO_LONGER_CURRENT = "HYDRATION_NO_LONGER_CURRENT"
    AMBIGUOUS = "AMBIGUOUS"
    COPIED_METADATA_NOT_REVALIDATED = "COPIED_METADATA_NOT_REVALIDATED"


class LinkKind(str, Enum):
    EXACT = "EXACT"
    VERSIONED = "VERSIONED"
    CURRENT_DEPENDENT = "CURRENT_DEPENDENT"
    COPIED_METADATA = "COPIED_METADATA"
    OPTIONAL = "OPTIONAL"


EVENTUAL_SCAN_LIMITATION = "A recently written record may not yet be visible."
ISO_CANDIDATE_LIMITATION = (
    "Recommendation and alert candidate scans compare valid_until_utc as ISO strings."
)
RECOMMENDATION_ABSENCE_LIMITATION = (
    "Absence of a recommendation is proven only within the observed ACTIVE, "
    "time-filtered candidate set."
)
ALERT_ABSENCE_LIMITATION = (
    "Absence of an alert is proven only within the observed active-state, "
    "time-filtered candidate set."
)
HAZARD_SOURCE_VERSION_LIMITATION = (
    "Current-impact lineage was not evaluated because the hazard root "
    "has no usable source_version."
)
NO_SNAPSHOT_LIMITATION = (
    "DynamoDB reads are independently observed; this composition is not "
    "a transactional cross-table snapshot."
)
MISSING_STATION_LIMITATION = (
    "METAR and TAF were not queried because AirportStatus has no station_id."
)
WEATHER_SOURCE_UNPROVEN_REASON = (
    "AirportStatus has no usable source version; latest weather lineage "
    "was not proven."
)
TAF_PERIOD_VERSION_LIMITATION = (
    "Forecast periods are exact children of the observed TAF version only; "
    "they are not a current-period set or an operational time window."
)
TAF_PERIOD_ABSENCE_LIMITATION = (
    "Absence of a forecast period is proven only for the observed TAF "
    "version key."
)
AIRPORT_DRIFT_FIELD_LIMITATION = (
    "An expected AirportStatus drift field was absent and was not fabricated."
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _identity(*pairs: tuple[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple((name, value) for name, value in pairs if value)


@dataclass(frozen=True)
class RetrievalObservation:
    source: str
    coverage: Coverage
    consistency: Consistency
    limit: int | None
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class Link:
    state: LinkState
    kind: LinkKind
    reason: str | None = None
    selected_identity: tuple[tuple[str, str], ...] = ()
    observed_identity: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class EncounterOperationalContext:
    encounter: dict[str, Any] | None
    encounter_is_current: bool
    hazard: dict[str, Any] | None
    risk: dict[str, Any] | None
    recommendations: tuple[dict[str, Any], ...]
    alerts: tuple[dict[str, Any], ...]
    encounter_link: Link
    hazard_link: Link
    risk_link: Link
    recommendation_link: Link
    alert_link: Link


@dataclass(frozen=True)
class AircraftOperationalContext:
    aircraft: dict[str, Any]
    aircraft_is_current: bool
    projection: dict[str, Any] | None
    projection_is_current: bool
    encounters: tuple[EncounterOperationalContext, ...]
    projection_link: Link
    retrieval: tuple[RetrievalObservation, ...]


@dataclass(frozen=True)
class HazardImpactContext:
    aircraft: dict[str, Any] | None
    aircraft_is_current: bool
    aircraft_link: Link
    projection: dict[str, Any] | None
    projection_is_current: bool
    projection_link: Link
    encounter: EncounterOperationalContext


@dataclass(frozen=True)
class HazardOperationalContext:
    hazard: dict[str, Any]
    hazard_is_lifecycle_active: bool
    hazard_is_current: bool
    source_version: str
    impacts: tuple[HazardImpactContext, ...]
    hazard_version_link: Link
    retrieval: tuple[RetrievalObservation, ...]


@dataclass(frozen=True)
class AirportOperationalContext:
    airport: dict[str, Any]
    airport_is_current: bool
    station_id: str
    latest_metar: dict[str, Any] | None
    metar_source_link: Link
    latest_taf: dict[str, Any] | None
    taf_source_link: Link
    latest_taf_periods: tuple[dict[str, Any], ...]
    taf_periods_link: Link
    airport_status_link: Link
    retrieval: tuple[RetrievalObservation, ...]


def scan_observation(source: str, *extra_limitations: str) -> RetrievalObservation:
    limitations = (EVENTUAL_SCAN_LIMITATION,) + extra_limitations
    return RetrievalObservation(
        source=source,
        coverage=Coverage.FULL_SCAN,
        consistency=Consistency.EVENTUAL,
        limit=None,
        limitations=limitations,
    )


def exact_pk_observation(source: str, *extra_limitations: str) -> RetrievalObservation:
    return RetrievalObservation(
        source=source,
        coverage=Coverage.EXACT_PK,
        consistency=Consistency.CONSISTENT,
        limit=None,
        limitations=extra_limitations,
    )


def query_observation(
    source: str,
    *extra_limitations: str,
    consistency: Consistency = Consistency.EVENTUAL,
) -> RetrievalObservation:
    limitations = extra_limitations
    if consistency is Consistency.EVENTUAL:
        limitations = (EVENTUAL_SCAN_LIMITATION,) + extra_limitations
    return RetrievalObservation(
        source=source,
        coverage=Coverage.FULL_QUERY,
        consistency=consistency,
        limit=None,
        limitations=limitations,
    )


def latest_weather_source_link(
    *,
    selected_station_id: str,
    selected_source_version: str,
    version_name: str,
    hydrated: dict[str, Any] | None,
    observed_version: str,
) -> tuple[dict[str, Any] | None, Link]:
    selected = _identity(
        ("station_id", selected_station_id),
        (version_name, selected_source_version),
    )
    if hydrated is None:
        return None, Link(
            state=LinkState.HYDRATION_MISSING,
            kind=LinkKind.VERSIONED,
            selected_identity=selected,
        )

    observed_station = _text(hydrated.get("station_id"))
    observed = _identity(
        ("station_id", observed_station),
        (version_name, observed_version),
    )
    if observed_station.upper() != _text(selected_station_id).upper():
        return None, Link(
            state=LinkState.HYDRATION_IDENTITY_MISMATCH,
            kind=LinkKind.VERSIONED,
            reason="hydrated station_id differs from AirportStatus station_id",
            selected_identity=selected,
            observed_identity=observed,
        )

    if not selected_source_version:
        return hydrated, Link(
            state=LinkState.MISSING,
            kind=LinkKind.VERSIONED,
            reason=WEATHER_SOURCE_UNPROVEN_REASON,
            selected_identity=_identity(("station_id", selected_station_id)),
            observed_identity=observed,
        )

    if observed_version != selected_source_version:
        return hydrated, Link(
            state=LinkState.HYDRATION_VERSION_MISMATCH,
            kind=LinkKind.VERSIONED,
            reason=(
                "latest weather version differs from AirportStatus "
                "source version"
            ),
            selected_identity=selected,
            observed_identity=observed,
        )

    return hydrated, Link(
        state=LinkState.PRESENT,
        kind=LinkKind.VERSIONED,
        selected_identity=selected,
        observed_identity=observed,
    )


def taf_periods_link_for(
    periods: tuple[dict[str, Any], ...],
    *,
    taf_version_key: str,
) -> Link:
    selected = _identity(("taf_version_key", taf_version_key))
    if not taf_version_key:
        return Link(
            state=LinkState.MISSING,
            kind=LinkKind.VERSIONED,
            reason="observed TAF has no usable taf_version_key",
        )
    if periods:
        return Link(
            state=LinkState.PRESENT,
            kind=LinkKind.VERSIONED,
            selected_identity=selected,
            observed_identity=selected,
        )
    return Link(
        state=LinkState.ABSENT_FROM_CURRENT_CANDIDATES,
        kind=LinkKind.OPTIONAL,
        reason=TAF_PERIOD_ABSENCE_LIMITATION,
        selected_identity=selected,
    )


def hydrate_aircraft(
    *,
    selected_aircraft_id: str,
    hydrated: dict[str, Any] | None,
    now_epoch: int,
) -> tuple[dict[str, Any] | None, Link]:
    selected = _identity(("aircraft_id", selected_aircraft_id))
    if hydrated is None:
        return None, Link(
            state=LinkState.HYDRATION_MISSING,
            kind=LinkKind.EXACT,
            selected_identity=selected,
        )

    observed = _identity(("aircraft_id", _text(hydrated.get("aircraft_id"))))
    if _text(hydrated.get("aircraft_id")).lower() != _text(selected_aircraft_id).lower():
        return None, Link(
            state=LinkState.HYDRATION_IDENTITY_MISMATCH,
            kind=LinkKind.EXACT,
            reason="hydrated aircraft_id differs from selected aircraft",
            selected_identity=selected,
            observed_identity=observed,
        )

    if not current_set.is_current_aircraft(hydrated, now_epoch):
        return hydrated, Link(
            state=LinkState.HYDRATION_NO_LONGER_CURRENT,
            kind=LinkKind.CURRENT_DEPENDENT,
            selected_identity=selected,
            observed_identity=observed,
        )

    return hydrated, Link(
        state=LinkState.PRESENT,
        kind=LinkKind.EXACT,
        selected_identity=selected,
        observed_identity=observed,
    )


def hydrate_projection(
    *,
    selected_projection_id: str,
    context_aircraft_id: str,
    hydrated: dict[str, Any] | None,
    now_epoch: int,
) -> tuple[dict[str, Any] | None, Link]:
    selected = _identity(("projection_id", selected_projection_id))
    if hydrated is None:
        return None, Link(
            state=LinkState.HYDRATION_MISSING,
            kind=LinkKind.EXACT,
            selected_identity=selected,
        )

    observed = _identity(("projection_id", _text(hydrated.get("projection_id"))))
    if _text(hydrated.get("aircraft_id")).lower() != _text(context_aircraft_id).lower():
        return None, Link(
            state=LinkState.HYDRATION_IDENTITY_MISMATCH,
            kind=LinkKind.EXACT,
            reason="hydrated aircraft_id differs from context aircraft",
            selected_identity=selected,
            observed_identity=observed,
        )

    if not current_set.is_current_projection(hydrated, now_epoch):
        return hydrated, Link(
            state=LinkState.HYDRATION_NO_LONGER_CURRENT,
            kind=LinkKind.CURRENT_DEPENDENT,
            selected_identity=selected,
            observed_identity=observed,
        )

    return hydrated, Link(
        state=LinkState.PRESENT,
        kind=LinkKind.EXACT,
        selected_identity=selected,
        observed_identity=observed,
    )


def hydrate_hazard(
    *,
    selected_hazard_id: str,
    selected_source_version: str,
    hydrated: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, Link]:
    selected = _identity(
        ("hazard_id", selected_hazard_id),
        ("source_version", selected_source_version),
    )
    if hydrated is None:
        return None, Link(
            state=LinkState.HYDRATION_MISSING,
            kind=LinkKind.VERSIONED,
            selected_identity=selected,
        )

    observed_version = _text(hydrated.get("source_version"))
    observed = _identity(
        ("hazard_id", _text(hydrated.get("hazard_id"))),
        ("source_version", observed_version),
    )
    if observed_version != selected_source_version:
        return None, Link(
            state=LinkState.HYDRATION_VERSION_MISMATCH,
            kind=LinkKind.VERSIONED,
            reason="exact hazard row source_version differs from selected version",
            selected_identity=selected,
            observed_identity=observed,
        )

    return hydrated, Link(
        state=LinkState.PRESENT,
        kind=LinkKind.VERSIONED,
        selected_identity=selected,
        observed_identity=observed,
    )


def hydrate_encounter(
    *,
    selected_encounter_id: str,
    context_aircraft_id: str,
    selected_projection_id: str,
    hydrated: dict[str, Any] | None,
    current_projection_ids: dict[str, str],
    current_hazard_versions: dict[str, str],
) -> tuple[dict[str, Any] | None, Link, bool]:
    selected = _identity(("encounter_id", selected_encounter_id))
    if hydrated is None:
        return (
            None,
            Link(
                state=LinkState.HYDRATION_MISSING,
                kind=LinkKind.EXACT,
                selected_identity=selected,
            ),
            False,
        )

    observed = _identity(("encounter_id", _text(hydrated.get("encounter_id"))))
    if _text(hydrated.get("aircraft_id")).lower() != _text(context_aircraft_id).lower():
        return (
            None,
            Link(
                state=LinkState.HYDRATION_IDENTITY_MISMATCH,
                kind=LinkKind.EXACT,
                reason="hydrated aircraft_id differs from context aircraft",
                selected_identity=selected,
                observed_identity=observed,
            ),
            False,
        )

    if _text(hydrated.get("projection_id")) != selected_projection_id:
        return (
            None,
            Link(
                state=LinkState.HYDRATION_IDENTITY_MISMATCH,
                kind=LinkKind.EXACT,
                reason="hydrated projection_id differs from selected projection",
                selected_identity=selected,
                observed_identity=observed,
            ),
            False,
        )

    is_current = current_set.is_current_encounter(
        hydrated,
        current_projection_ids=current_projection_ids,
        current_hazard_versions=current_hazard_versions,
    )
    if not is_current:
        return (
            hydrated,
            Link(
                state=LinkState.HYDRATION_NO_LONGER_CURRENT,
                kind=LinkKind.CURRENT_DEPENDENT,
                selected_identity=selected,
                observed_identity=observed,
            ),
            False,
        )

    return (
        hydrated,
        Link(
            state=LinkState.PRESENT,
            kind=LinkKind.EXACT,
            selected_identity=selected,
            observed_identity=observed,
        ),
        True,
    )


def hydrate_risk(
    *,
    selected_risk_id: str,
    selected_encounter_id: str,
    hydrated: dict[str, Any] | None,
    current_encounter_ids: set[str],
    now_epoch: int,
) -> tuple[dict[str, Any] | None, Link]:
    selected = _identity(
        ("risk_id", selected_risk_id),
        ("encounter_id", selected_encounter_id),
    )
    if hydrated is None:
        return None, Link(
            state=LinkState.HYDRATION_MISSING,
            kind=LinkKind.EXACT,
            selected_identity=selected,
        )

    observed = _identity(
        ("risk_id", _text(hydrated.get("risk_id"))),
        ("encounter_id", _text(hydrated.get("encounter_id"))),
    )
    if hydrated.get("encounter_id") != selected_encounter_id:
        return None, Link(
            state=LinkState.HYDRATION_IDENTITY_MISMATCH,
            kind=LinkKind.EXACT,
            reason="hydrated encounter_id differs from selected encounter",
            selected_identity=selected,
            observed_identity=observed,
        )

    if not current_set.is_current_risk(
        hydrated,
        current_encounter_ids=current_encounter_ids,
        now_epoch=now_epoch,
    ):
        return hydrated, Link(
            state=LinkState.HYDRATION_NO_LONGER_CURRENT,
            kind=LinkKind.CURRENT_DEPENDENT,
            selected_identity=selected,
            observed_identity=observed,
        )

    return hydrated, Link(
        state=LinkState.PRESENT,
        kind=LinkKind.EXACT,
        reason="copied projection/hazard fields are not revalidated",
        selected_identity=selected,
        observed_identity=observed,
    )


def select_current_recommendations(
    recommendations: Iterable[dict[str, Any]],
    *,
    current_risk_ids: set[str],
    now_epoch: int,
) -> tuple[dict[str, Any], ...]:
    return tuple(
        item
        for item in recommendations
        if current_set.is_current_recommendation_at(
            item,
            current_risk_ids=current_risk_ids,
            now_epoch=now_epoch,
        )
    )


def select_current_alerts(
    alerts: Iterable[dict[str, Any]],
    *,
    current_risk_ids: set[str],
    current_recommendation_ids: set[str],
    now_epoch: int,
) -> tuple[dict[str, Any], ...]:
    return tuple(
        item
        for item in alerts
        if current_set.is_current_alert_at(
            item,
            current_risk_ids=current_risk_ids,
            current_recommendation_ids=current_recommendation_ids,
            now_epoch=now_epoch,
        )
    )


def recommendation_link_for(recommendations: tuple[dict[str, Any], ...]) -> Link:
    if recommendations:
        return Link(
            state=LinkState.PRESENT,
            kind=LinkKind.EXACT,
            selected_identity=_identity(
                *(
                    ("recommendation_id", _text(item.get("recommendation_id")))
                    for item in recommendations
                )
            ),
        )
    return Link(
        state=LinkState.ABSENT_FROM_CURRENT_CANDIDATES,
        kind=LinkKind.OPTIONAL,
        reason=RECOMMENDATION_ABSENCE_LIMITATION,
    )


def alert_link_for(alerts: tuple[dict[str, Any], ...]) -> Link:
    if alerts:
        return Link(
            state=LinkState.PRESENT,
            kind=LinkKind.EXACT,
            selected_identity=_identity(
                *(
                    ("alert_id", _text(item.get("alert_id")))
                    for item in alerts
                )
            ),
        )
    return Link(
        state=LinkState.ABSENT_FROM_CURRENT_CANDIDATES,
        kind=LinkKind.OPTIONAL,
        reason=ALERT_ABSENCE_LIMITATION,
    )


def compose_encounter_operational_context(
    *,
    encounter: dict[str, Any] | None,
    encounter_is_current: bool,
    encounter_link: Link,
    hazard: dict[str, Any] | None,
    hazard_link: Link,
    risk: dict[str, Any] | None,
    risk_link: Link,
    recommendations: tuple[dict[str, Any], ...],
    recommendation_link: Link,
    alerts: tuple[dict[str, Any], ...],
    alert_link: Link,
) -> EncounterOperationalContext:
    return EncounterOperationalContext(
        encounter=encounter,
        encounter_is_current=encounter_is_current,
        hazard=hazard,
        risk=risk,
        recommendations=recommendations,
        alerts=alerts,
        encounter_link=encounter_link,
        hazard_link=hazard_link,
        risk_link=risk_link,
        recommendation_link=recommendation_link,
        alert_link=alert_link,
    )


def selected_encounter_hazard_lineage(
    encounter: dict[str, Any] | None,
    encounter_candidate: dict[str, Any] | None,
    current_hazard_versions: dict[str, str],
) -> tuple[str, str]:
    lineage_source = encounter or encounter_candidate or {}
    hazard_id = _text(lineage_source.get("hazard_id"))
    if not hazard_id:
        return "", ""
    return hazard_id, _text(current_hazard_versions.get(hazard_id, ""))


def compose_encounter_operational_context_from_observed_records(
    *,
    aircraft_id: str,
    selected_projection_id: str,
    encounter_candidate: dict[str, Any],
    hydrated_encounter: dict[str, Any] | None,
    hydrated_hazard: dict[str, Any] | None,
    hydrated_risk: dict[str, Any] | None,
    current_projection_ids: dict[str, str],
    current_hazard_versions: dict[str, str],
    selected_risks: dict[str, dict[str, Any]],
    selected_encounter_ids: set[str],
    recommendation_candidates: Iterable[dict[str, Any]],
    alert_candidates: Iterable[dict[str, Any]],
    now_epoch: int,
) -> EncounterOperationalContext:
    selected_encounter_id = encounter_candidate.get("encounter_id")
    encounter, encounter_link, encounter_is_current = hydrate_encounter(
        selected_encounter_id=selected_encounter_id or "",
        context_aircraft_id=aircraft_id,
        selected_projection_id=selected_projection_id,
        hydrated=hydrated_encounter,
        current_projection_ids=current_projection_ids,
        current_hazard_versions=current_hazard_versions,
    )

    hazard_id, selected_source_version = selected_encounter_hazard_lineage(
        encounter,
        encounter_candidate,
        current_hazard_versions,
    )
    if not hazard_id or not selected_source_version:
        hazard = None
        hazard_link = Link(
            state=LinkState.MISSING,
            kind=LinkKind.VERSIONED,
            selected_identity=(
                (("hazard_id", hazard_id),) if hazard_id else ()
            ),
        )
    else:
        hazard, hazard_link = hydrate_hazard(
            selected_hazard_id=hazard_id,
            selected_source_version=selected_source_version,
            hydrated=hydrated_hazard,
        )

    selected_risk = selected_risks.get(selected_encounter_id)
    if not selected_risk or not selected_risk.get("risk_id"):
        risk = None
        risk_link = Link(
            state=LinkState.MISSING,
            kind=LinkKind.EXACT,
            selected_identity=(
                (("encounter_id", selected_encounter_id),)
                if selected_encounter_id
                else ()
            ),
            reason="no current risk identity in the observed candidate set",
        )
        current_risk_ids: set[str] = set()
    else:
        risk, risk_link = hydrate_risk(
            selected_risk_id=selected_risk["risk_id"],
            selected_encounter_id=selected_encounter_id,
            hydrated=hydrated_risk,
            current_encounter_ids=selected_encounter_ids,
            now_epoch=now_epoch,
        )
        current_risk_ids = (
            {risk["risk_id"]}
            if risk is not None and risk_link.state == LinkState.PRESENT
            else set()
        )

    recommendations = select_current_recommendations(
        recommendation_candidates,
        current_risk_ids=current_risk_ids,
        now_epoch=now_epoch,
    )
    recommendation_link = recommendation_link_for(recommendations)
    current_recommendation_ids = {
        _text(item.get("recommendation_id"))
        for item in recommendations
        if item.get("recommendation_id")
    }
    alerts = select_current_alerts(
        alert_candidates,
        current_risk_ids=current_risk_ids,
        current_recommendation_ids=current_recommendation_ids,
        now_epoch=now_epoch,
    )
    return compose_encounter_operational_context(
        encounter=encounter,
        encounter_is_current=encounter_is_current,
        encounter_link=encounter_link,
        hazard=hazard,
        hazard_link=hazard_link,
        risk=risk,
        risk_link=risk_link,
        recommendations=recommendations,
        recommendation_link=recommendation_link,
        alerts=alerts,
        alert_link=alert_link_for(alerts),
    )


def compose_hazard_impact_from_observed_records(
    *,
    aircraft_id: str,
    selected_projection_id: str,
    hydrated_aircraft: dict[str, Any] | None,
    hydrated_projection: dict[str, Any] | None,
    encounter: EncounterOperationalContext,
    now_epoch: int,
) -> HazardImpactContext:
    aircraft, aircraft_link = hydrate_aircraft(
        selected_aircraft_id=aircraft_id,
        hydrated=hydrated_aircraft,
        now_epoch=now_epoch,
    )
    aircraft_is_current = bool(
        aircraft is not None and current_set.is_current_aircraft(aircraft, now_epoch)
    )

    if not selected_projection_id:
        projection = None
        projection_is_current = False
        projection_link = Link(
            state=LinkState.MISSING,
            kind=LinkKind.CURRENT_DEPENDENT,
            reason="no current projection identity in the observed candidate set",
        )
    else:
        projection, projection_link = hydrate_projection(
            selected_projection_id=selected_projection_id,
            context_aircraft_id=aircraft_id,
            hydrated=hydrated_projection,
            now_epoch=now_epoch,
        )
        projection_is_current = projection_link.state == LinkState.PRESENT

    return HazardImpactContext(
        aircraft=aircraft,
        aircraft_is_current=aircraft_is_current,
        aircraft_link=aircraft_link,
        projection=projection,
        projection_is_current=projection_is_current,
        projection_link=projection_link,
        encounter=encounter,
    )
