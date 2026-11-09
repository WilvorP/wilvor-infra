"""Deterministic operational query fabric.

Public domain functions compose request-scoped observed data with Phase 1A
current-set semantics and public Phase 1C composition. Geospatial imports
occur only on the region path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from . import current_set
from . import discovery
from . import linking
from . import observed
from . import readers


if TYPE_CHECKING:
    from . import geospatial
    from . import regions


QUERY_SELECTION_REQUIRED = (
    "search_current_impacts requires region or hazard_ids; "
    "a worldwide impact scan is not performed."
)
ENCOUNTER_SELECTION_REQUIRED = (
    "search_current_encounters requires aircraft_id, callsign, "
    "hazard_id, or hazard_ids."
)


class OperationalUnevaluatedReason(str, Enum):
    HAZARD_PIN_MISSING = "HAZARD_PIN_MISSING"
    HAZARD_PIN_NOT_CURRENT = "HAZARD_PIN_NOT_CURRENT"
    HAZARD_PIN_VERSION_DRIFT = "HAZARD_PIN_VERSION_DRIFT"
    HAZARD_PIN_IDENTITY_DRIFT = "HAZARD_PIN_IDENTITY_DRIFT"
    HAZARD_SOURCE_VERSION_MISSING = "HAZARD_SOURCE_VERSION_MISSING"
    HAZARD_AUTHORITY_UNESTABLISHED = "HAZARD_AUTHORITY_UNESTABLISHED"
    ENCOUNTER_HYDRATION_MISSING = "ENCOUNTER_HYDRATION_MISSING"
    ENCOUNTER_HYDRATION_IDENTITY_MISMATCH = "ENCOUNTER_HYDRATION_IDENTITY_MISMATCH"
    ENCOUNTER_HYDRATION_NO_LONGER_CURRENT = "ENCOUNTER_HYDRATION_NO_LONGER_CURRENT"
    HAZARD_HYDRATION_MISSING = "HAZARD_HYDRATION_MISSING"
    HAZARD_HYDRATION_VERSION_MISMATCH = "HAZARD_HYDRATION_VERSION_MISMATCH"


class HazardSelectionRejectionReason(str, Enum):
    MISSING = "MISSING"
    NOT_CURRENT = "NOT_CURRENT"
    SOURCE_VERSION_MISSING = "SOURCE_VERSION_MISSING"


@dataclass(frozen=True)
class CurrentImpactRecord:
    hazard_id: str
    source_version: str
    encounter_id: str
    aircraft_id: str
    impact: linking.HazardImpactContext


@dataclass(frozen=True)
class OperationallyUnevaluatedImpactCandidate:
    encounter_id: str
    aircraft_id: str
    hazard_id: str
    selected_source_version: str
    reason: str
    encounter_link: linking.Link | None
    hazard_link: linking.Link | None
    retrieval: tuple[linking.RetrievalObservation, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class RejectedOperationalHazardSelection:
    hazard_id: str
    discovered_source_version: str
    exact_hazard: dict[str, Any] | None
    reason: str
    limitations: tuple[str, ...]
    retrieval: tuple[linking.RetrievalObservation, ...]


@dataclass(frozen=True)
class CurrentImpactQueryResult:
    now_epoch: int
    impacts: tuple[CurrentImpactRecord, ...]
    unique_aircraft_ids: tuple[str, ...]
    operationally_unevaluated: tuple[OperationallyUnevaluatedImpactCandidate, ...]
    region_resolution: regions.RegionResolution | None
    geospatial_unevaluated: tuple[geospatial.HazardRegionEvaluation, ...]
    rejected_hazard_candidates: tuple[geospatial.RejectedHazardCandidate, ...]
    rejected_hazard_selections: tuple[RejectedOperationalHazardSelection, ...]
    retrieval: tuple[linking.RetrievalObservation, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class CurrentEncounterRecord:
    hazard_id: str
    source_version: str
    encounter_id: str
    aircraft_id: str
    encounter_context: linking.EncounterOperationalContext
    aircraft: dict[str, Any] | None
    aircraft_is_current: bool


@dataclass(frozen=True)
class OperationallyUnevaluatedEncounterCandidate:
    encounter_id: str
    aircraft_id: str
    hazard_id: str
    selected_source_version: str
    reason: str
    encounter_link: linking.Link | None
    hazard_link: linking.Link | None
    retrieval: tuple[linking.RetrievalObservation, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class CurrentEncounterQueryResult:
    now_epoch: int
    encounters: tuple[CurrentEncounterRecord, ...]
    operationally_unevaluated: tuple[OperationallyUnevaluatedEncounterCandidate, ...]
    callsign_matches: tuple[str, ...]
    rejected_hazard_selections: tuple[RejectedOperationalHazardSelection, ...]
    retrieval: tuple[linking.RetrievalObservation, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class ObservedNetworkState:
    as_of_epoch: int
    current_aircraft_ids: tuple[str, ...]
    current_hazard_ids: tuple[str, ...]
    current_encounter_ids: tuple[str, ...]
    current_risk_ids: tuple[str, ...]
    current_recommendation_ids: tuple[str, ...]
    current_alert_ids: tuple[str, ...]
    retrieval: tuple[linking.RetrievalObservation, ...]
    limitations: tuple[str, ...]


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _identity_value(identity: tuple[tuple[str, str], ...], name: str) -> str:
    for key, value in identity:
        if key == name:
            return value
    return ""


def unique_aircraft_ids_from_impacts(
    impacts: tuple[CurrentImpactRecord, ...],
) -> tuple[str, ...]:
    return tuple(sorted({item.aircraft_id for item in impacts if item.aircraft_id}))


def _sort_impacts(
    impacts: tuple[CurrentImpactRecord, ...],
) -> tuple[CurrentImpactRecord, ...]:
    return tuple(
        sorted(
            impacts,
            key=lambda item: (
                item.hazard_id,
                item.source_version,
                item.aircraft_id,
                item.encounter_id,
            ),
        )
    )


def _sort_encounters(
    encounters: tuple[CurrentEncounterRecord, ...],
) -> tuple[CurrentEncounterRecord, ...]:
    return tuple(
        sorted(
            encounters,
            key=lambda item: (
                item.hazard_id,
                item.source_version,
                item.aircraft_id,
                item.encounter_id,
            ),
        )
    )


def _empty_impact_result(
    now_epoch: int,
    *extra_limitations: str,
    retrieval: tuple[linking.RetrievalObservation, ...] = (),
) -> CurrentImpactQueryResult:
    return CurrentImpactQueryResult(
        now_epoch=now_epoch,
        impacts=(),
        unique_aircraft_ids=(),
        operationally_unevaluated=(),
        region_resolution=None,
        geospatial_unevaluated=(),
        rejected_hazard_candidates=(),
        rejected_hazard_selections=(),
        retrieval=retrieval,
        limitations=(linking.NO_SNAPSHOT_LIMITATION,) + extra_limitations,
    )


def _failed_pin_candidate(
    failed: observed.FailedHazardPin,
) -> OperationallyUnevaluatedImpactCandidate:
    return OperationallyUnevaluatedImpactCandidate(
        encounter_id="",
        aircraft_id="",
        hazard_id=failed.hazard_id,
        selected_source_version=failed.selected_source_version,
        reason=failed.reason,
        encounter_link=None,
        hazard_link=failed.link,
        retrieval=failed.retrieval,
        limitations=failed.limitations,
    )


def _reason_for_encounter_link(link: linking.Link) -> str:
    if link.state is linking.LinkState.HYDRATION_MISSING:
        return OperationalUnevaluatedReason.ENCOUNTER_HYDRATION_MISSING.value
    if link.state is linking.LinkState.HYDRATION_IDENTITY_MISMATCH:
        return OperationalUnevaluatedReason.ENCOUNTER_HYDRATION_IDENTITY_MISMATCH.value
    if link.state is linking.LinkState.HYDRATION_NO_LONGER_CURRENT:
        return OperationalUnevaluatedReason.ENCOUNTER_HYDRATION_NO_LONGER_CURRENT.value
    if link.state is linking.LinkState.HYDRATION_VERSION_MISMATCH:
        return OperationalUnevaluatedReason.ENCOUNTER_HYDRATION_IDENTITY_MISMATCH.value
    return link.state.value


def _reason_for_hazard_link(link: linking.Link) -> str:
    if link.state is linking.LinkState.HYDRATION_MISSING:
        return OperationalUnevaluatedReason.HAZARD_HYDRATION_MISSING.value
    if link.state is linking.LinkState.HYDRATION_VERSION_MISMATCH:
        return OperationalUnevaluatedReason.HAZARD_HYDRATION_VERSION_MISMATCH.value
    if link.state is linking.LinkState.MISSING:
        return OperationalUnevaluatedReason.HAZARD_SOURCE_VERSION_MISSING.value
    return link.state.value


class _ExactRows:
    def __init__(self):
        self.encounters: dict[str, dict[str, Any] | None] = {}
        self.hazards: dict[str, dict[str, Any] | None] = {}
        self.risks: dict[str, dict[str, Any] | None] = {}
        self.aircraft: dict[str, dict[str, Any] | None] = {}
        self.projections: dict[str, dict[str, Any] | None] = {}
        self.retrieval: list[linking.RetrievalObservation] = []

    def encounter(self, tables, encounter_id: str):
        if encounter_id not in self.encounters:
            self.retrieval.append(linking.exact_pk_observation("get_encounter_record"))
            self.encounters[encounter_id] = (
                readers.get_encounter_record(tables.encounters, encounter_id)
                if encounter_id
                else None
            )
        return self.encounters[encounter_id]

    def hazard(self, tables, hazard_id: str):
        if hazard_id not in self.hazards:
            self.retrieval.append(linking.exact_pk_observation("get_hazard_record"))
            self.hazards[hazard_id] = (
                readers.get_hazard_record(tables.hazards, hazard_id)
                if hazard_id
                else None
            )
        return self.hazards[hazard_id]

    def risk(self, tables, risk_id: str):
        if risk_id not in self.risks:
            self.retrieval.append(linking.exact_pk_observation("get_risk_record"))
            self.risks[risk_id] = (
                readers.get_risk_record(tables.risks, risk_id) if risk_id else None
            )
        return self.risks[risk_id]

    def aircraft_row(self, tables, aircraft_id: str):
        if aircraft_id not in self.aircraft:
            self.retrieval.append(linking.exact_pk_observation("get_aircraft_record"))
            self.aircraft[aircraft_id] = (
                readers.get_aircraft_record(tables.aircraft, aircraft_id)
                if aircraft_id
                else None
            )
        return self.aircraft[aircraft_id]

    def projection(self, tables, projection_id: str):
        if projection_id not in self.projections:
            self.retrieval.append(linking.exact_pk_observation("get_projection_record"))
            self.projections[projection_id] = (
                readers.get_projection_record(tables.projections, projection_id)
                if projection_id
                else None
            )
        return self.projections[projection_id]


def _compose_encounter(
    tables,
    exact: _ExactRows,
    *,
    candidate: dict[str, Any],
    observed_set: observed.ObservedOperationalSet,
):
    aircraft_id = _text(candidate.get("aircraft_id")).lower()
    selected_projection_id = observed_set.current_projections_by_aircraft.get(
        aircraft_id,
        "",
    )
    encounter_id = _text(candidate.get("encounter_id"))
    hydrated_encounter = exact.encounter(tables, encounter_id)
    encounter, _, _ = linking.hydrate_encounter(
        selected_encounter_id=encounter_id,
        context_aircraft_id=aircraft_id,
        selected_projection_id=selected_projection_id,
        hydrated=hydrated_encounter,
        current_projection_ids=dict(observed_set.current_projections_by_aircraft),
        current_hazard_versions=dict(observed_set.selected_hazard_versions),
    )
    hazard_id, source_version = linking.selected_encounter_hazard_lineage(
        encounter,
        candidate,
        dict(observed_set.selected_hazard_versions),
    )
    hydrated_hazard = (
        exact.hazard(tables, hazard_id) if hazard_id and source_version else None
    )
    selected_risk = observed_set.current_risks_by_encounter.get(encounter_id)
    hydrated_risk = (
        exact.risk(tables, _text(selected_risk.get("risk_id")))
        if selected_risk and selected_risk.get("risk_id")
        else None
    )
    selected_encounter_ids = {
        item.get("encounter_id")
        for item in observed_set.current_encounters
        if item.get("encounter_id")
    }
    return linking.compose_encounter_operational_context_from_observed_records(
        aircraft_id=aircraft_id,
        selected_projection_id=selected_projection_id,
        encounter_candidate=candidate,
        hydrated_encounter=hydrated_encounter,
        hydrated_hazard=hydrated_hazard,
        hydrated_risk=hydrated_risk,
        current_projection_ids=dict(observed_set.current_projections_by_aircraft),
        current_hazard_versions=dict(observed_set.selected_hazard_versions),
        selected_risks=dict(observed_set.current_risks_by_encounter),
        selected_encounter_ids=selected_encounter_ids,
        recommendation_candidates=observed_set.recommendation_candidates,
        alert_candidates=observed_set.alert_candidates,
        now_epoch=observed_set.now_epoch,
    )


def _compose_confirmed_impacts(
    tables,
    observed_set: observed.ObservedOperationalSet,
) -> tuple[
    tuple[CurrentImpactRecord, ...],
    tuple[OperationallyUnevaluatedImpactCandidate, ...],
    tuple[linking.RetrievalObservation, ...],
]:
    exact = _ExactRows()
    impacts: list[CurrentImpactRecord] = []
    unevaluated: list[OperationallyUnevaluatedImpactCandidate] = []
    for candidate in observed_set.current_encounters:
        encounter_id = _text(candidate.get("encounter_id"))
        aircraft_id = _text(candidate.get("aircraft_id")).lower()
        composed = _compose_encounter(
            tables,
            exact,
            candidate=candidate,
            observed_set=observed_set,
        )
        hazard_id, source_version = linking.selected_encounter_hazard_lineage(
            composed.encounter,
            candidate,
            dict(observed_set.selected_hazard_versions),
        )
        if (
            composed.encounter_link.state is not linking.LinkState.PRESENT
            or not composed.encounter_is_current
        ):
            unevaluated.append(
                OperationallyUnevaluatedImpactCandidate(
                    encounter_id=encounter_id,
                    aircraft_id=aircraft_id,
                    hazard_id=hazard_id,
                    selected_source_version=source_version,
                    reason=_reason_for_encounter_link(composed.encounter_link),
                    encounter_link=composed.encounter_link,
                    hazard_link=composed.hazard_link,
                    retrieval=(),
                    limitations=(linking.NO_SNAPSHOT_LIMITATION,),
                )
            )
            continue
        if composed.hazard_link.state is not linking.LinkState.PRESENT:
            unevaluated.append(
                OperationallyUnevaluatedImpactCandidate(
                    encounter_id=encounter_id,
                    aircraft_id=aircraft_id,
                    hazard_id=hazard_id,
                    selected_source_version=source_version,
                    reason=_reason_for_hazard_link(composed.hazard_link),
                    encounter_link=composed.encounter_link,
                    hazard_link=composed.hazard_link,
                    retrieval=(),
                    limitations=(linking.NO_SNAPSHOT_LIMITATION,),
                )
            )
            continue
        selected_projection_id = observed_set.current_projections_by_aircraft.get(
            aircraft_id,
            "",
        )
        impact = linking.compose_hazard_impact_from_observed_records(
            aircraft_id=aircraft_id,
            selected_projection_id=selected_projection_id,
            hydrated_aircraft=exact.aircraft_row(tables, aircraft_id),
            hydrated_projection=(
                exact.projection(tables, selected_projection_id)
                if selected_projection_id
                else None
            ),
            encounter=composed,
            now_epoch=observed_set.now_epoch,
        )
        impacts.append(
            CurrentImpactRecord(
                hazard_id=hazard_id,
                source_version=source_version,
                encounter_id=encounter_id,
                aircraft_id=aircraft_id,
                impact=impact,
            )
        )
    return _sort_impacts(tuple(impacts)), tuple(unevaluated), tuple(exact.retrieval)


def _load_observed_for_hazards(tables, *, now_epoch, selected_hazard_versions):
    projections, projection_retrieval = observed.load_current_projection_index(
        tables,
        now_epoch=now_epoch,
    )
    encounters, encounter_retrieval = observed.load_encounter_candidates_for_hazards(
        tables,
        selected_hazard_versions.keys(),
    )
    decisions = observed.load_decision_candidates(tables, now_epoch=now_epoch)
    observed_set = observed.assemble_observed_operational_set(
        now_epoch=now_epoch,
        current_projections_by_aircraft=projections,
        selected_hazard_versions=selected_hazard_versions,
        encounter_candidates=encounters,
        decision_candidates=decisions,
        retrieval=projection_retrieval + encounter_retrieval + decisions.retrieval,
    )
    return observed_set


def _explicit_hazard_pins(tables, *, now_epoch, hazard_ids, product_type, hazard_type):
    requested = tuple(_text(item) for item in hazard_ids if _text(item))
    discovered = discovery.discover_current_hazards(
        tables,
        now_epoch=now_epoch,
        product_type=product_type,
        hazard_type=hazard_type,
        hazard_ids=requested,
    )
    matches_by_id = {
        _identity_value(match.identity, "hazard_id"): match
        for match in discovered.matches
    }
    selected: dict[str, str] = {}
    rejected: list[RejectedOperationalHazardSelection] = []
    unevaluated: list[OperationallyUnevaluatedImpactCandidate] = []
    for hazard_id in requested:
        match = matches_by_id.get(hazard_id)
        if match is None:
            rejected.append(
                RejectedOperationalHazardSelection(
                    hazard_id=hazard_id,
                    discovered_source_version="",
                    exact_hazard=None,
                    reason=HazardSelectionRejectionReason.MISSING.value,
                    limitations=(),
                    retrieval=discovered.retrieval,
                )
            )
            continue
        source_version = _identity_value(match.identity, "source_version")
        if not match.is_current:
            rejected.append(
                RejectedOperationalHazardSelection(
                    hazard_id=hazard_id,
                    discovered_source_version=source_version,
                    exact_hazard=match.source,
                    reason=HazardSelectionRejectionReason.NOT_CURRENT.value,
                    limitations=(),
                    retrieval=discovered.retrieval,
                )
            )
            continue
        if not source_version:
            rejected.append(
                RejectedOperationalHazardSelection(
                    hazard_id=hazard_id,
                    discovered_source_version="",
                    exact_hazard=match.source,
                    reason=HazardSelectionRejectionReason.SOURCE_VERSION_MISSING.value,
                    limitations=(linking.HAZARD_SOURCE_VERSION_LIMITATION,),
                    retrieval=discovered.retrieval,
                )
            )
            unevaluated.append(
                OperationallyUnevaluatedImpactCandidate(
                    encounter_id="",
                    aircraft_id="",
                    hazard_id=hazard_id,
                    selected_source_version="",
                    reason=(
                        OperationalUnevaluatedReason.HAZARD_SOURCE_VERSION_MISSING.value
                    ),
                    encounter_link=None,
                    hazard_link=linking.Link(
                        state=linking.LinkState.MISSING,
                        kind=linking.LinkKind.VERSIONED,
                        selected_identity=(("hazard_id", hazard_id),),
                        reason=linking.HAZARD_SOURCE_VERSION_LIMITATION,
                    ),
                    retrieval=discovered.retrieval,
                    limitations=(linking.HAZARD_SOURCE_VERSION_LIMITATION,),
                )
            )
            continue
        selected[hazard_id] = source_version
    return selected, tuple(rejected), tuple(unevaluated), discovered.retrieval


def search_current_impacts(
    tables,
    *,
    now_epoch,
    region=None,
    product_type=None,
    hazard_type=None,
    hazard_ids=None,
) -> CurrentImpactQueryResult:
    if not region and hazard_ids is None:
        return _empty_impact_result(now_epoch, QUERY_SELECTION_REQUIRED)

    if region:
        from . import geospatial

        geo = geospatial.discover_current_hazards_in_region(
            tables,
            region,
            now_epoch=now_epoch,
            product_type=product_type,
            hazard_type=hazard_type,
            hazard_ids=hazard_ids,
        )
        confirmation = observed.confirm_selected_hazard_pins(
            tables,
            geo.intersecting,
            now_epoch=now_epoch,
        )
        pin_failures = tuple(
            _failed_pin_candidate(item) for item in confirmation.failed
        )
        retrieval = list(geo.retrieval) + list(confirmation.retrieval)
        if confirmation.confirmed:
            observed_set = _load_observed_for_hazards(
                tables,
                now_epoch=now_epoch,
                selected_hazard_versions=confirmation.confirmed,
            )
            retrieval.extend(observed_set.retrieval)
            impacts, unevaluated, hydrate_retrieval = _compose_confirmed_impacts(
                tables,
                observed_set,
            )
            retrieval.extend(hydrate_retrieval)
        else:
            impacts = ()
            unevaluated = ()
        return CurrentImpactQueryResult(
            now_epoch=now_epoch,
            impacts=impacts,
            unique_aircraft_ids=unique_aircraft_ids_from_impacts(impacts),
            operationally_unevaluated=pin_failures + unevaluated,
            region_resolution=geo.region_resolution,
            geospatial_unevaluated=geo.unevaluated,
            rejected_hazard_candidates=geo.rejected_candidates,
            rejected_hazard_selections=(),
            retrieval=tuple(retrieval),
            limitations=(linking.NO_SNAPSHOT_LIMITATION,),
        )

    selected, rejected, unevaluated, discovery_retrieval = _explicit_hazard_pins(
        tables,
        now_epoch=now_epoch,
        hazard_ids=hazard_ids,
        product_type=product_type,
        hazard_type=hazard_type,
    )
    retrieval = list(discovery_retrieval)
    if selected:
        observed_set = _load_observed_for_hazards(
            tables,
            now_epoch=now_epoch,
            selected_hazard_versions=selected,
        )
        retrieval.extend(observed_set.retrieval)
        impacts, composed_unevaluated, hydrate_retrieval = _compose_confirmed_impacts(
            tables,
            observed_set,
        )
        retrieval.extend(hydrate_retrieval)
        unevaluated = unevaluated + composed_unevaluated
    else:
        impacts = ()
    return CurrentImpactQueryResult(
        now_epoch=now_epoch,
        impacts=impacts,
        unique_aircraft_ids=unique_aircraft_ids_from_impacts(impacts),
        operationally_unevaluated=unevaluated,
        region_resolution=None,
        geospatial_unevaluated=(),
        rejected_hazard_candidates=(),
        rejected_hazard_selections=rejected,
        retrieval=tuple(retrieval),
        limitations=(linking.NO_SNAPSHOT_LIMITATION,),
    )


def _resolve_aircraft_filters(*, aircraft_id, callsign, tables, now_epoch):
    callsign_matches: tuple[str, ...] = ()
    limitations: list[str] = []
    retrieval: list[linking.RetrievalObservation] = []
    selected: set[str] = set()
    if callsign:
        discovered = discovery.discover_aircraft_by_callsign(
            tables,
            callsign,
            now_epoch=now_epoch,
        )
        retrieval.extend(discovered.retrieval)
        limitations.append(discovery.CALLSIGN_CASE_LIMITATION)
        callsign_matches = tuple(
            _identity_value(match.identity, "aircraft_id")
            for match in discovered.matches
        )
        selected.update(callsign_matches)
    if aircraft_id:
        normalized = _text(aircraft_id).lower()
        if callsign:
            selected.intersection_update({normalized})
        else:
            if normalized:
                selected.add(normalized)
    return tuple(sorted(selected)), callsign_matches, tuple(retrieval), tuple(limitations)


def _hazard_filter_ids(*, hazard_id, hazard_ids) -> tuple[str, ...]:
    values = []
    if hazard_id:
        values.append(_text(hazard_id))
    if hazard_ids is not None:
        values.extend(_text(item) for item in hazard_ids)
    return tuple(item for item in values if item)


def _authority_for_aircraft_encounters(
    tables,
    encounter_candidates: tuple[dict[str, Any], ...],
    *,
    now_epoch,
):
    hazard_ids = []
    seen: set[str] = set()
    for item in encounter_candidates:
        hazard_id = _text(item.get("hazard_id"))
        if hazard_id and hazard_id not in seen:
            seen.add(hazard_id)
            hazard_ids.append(hazard_id)
    discovered = observed.load_current_hazard_versions_for_ids(
        tables,
        hazard_ids,
        now_epoch=now_epoch,
    )
    unevaluated: list[OperationallyUnevaluatedEncounterCandidate] = []
    for item in encounter_candidates:
        hazard_id = _text(item.get("hazard_id"))
        encounter_id = _text(item.get("encounter_id"))
        aircraft_id = _text(item.get("aircraft_id")).lower()
        match = discovered.matches_by_id.get(hazard_id)
        if match is None:
            unevaluated.append(
                OperationallyUnevaluatedEncounterCandidate(
                    encounter_id=encounter_id,
                    aircraft_id=aircraft_id,
                    hazard_id=hazard_id,
                    selected_source_version="",
                    reason=(
                        OperationalUnevaluatedReason.HAZARD_AUTHORITY_UNESTABLISHED.value
                    ),
                    encounter_link=None,
                    hazard_link=linking.Link(
                        state=linking.LinkState.HYDRATION_MISSING,
                        kind=linking.LinkKind.VERSIONED,
                        selected_identity=(
                            (("hazard_id", hazard_id),) if hazard_id else ()
                        ),
                    ),
                    retrieval=discovered.retrieval,
                    limitations=(linking.NO_SNAPSHOT_LIMITATION,),
                )
            )
            continue
        source_version = _identity_value(match.identity, "source_version")
        if match.is_current and not source_version:
            unevaluated.append(
                OperationallyUnevaluatedEncounterCandidate(
                    encounter_id=encounter_id,
                    aircraft_id=aircraft_id,
                    hazard_id=hazard_id,
                    selected_source_version="",
                    reason=(
                        OperationalUnevaluatedReason.HAZARD_SOURCE_VERSION_MISSING.value
                    ),
                    encounter_link=None,
                    hazard_link=linking.Link(
                        state=linking.LinkState.MISSING,
                        kind=linking.LinkKind.VERSIONED,
                        selected_identity=(("hazard_id", hazard_id),),
                        reason=linking.HAZARD_SOURCE_VERSION_LIMITATION,
                    ),
                    retrieval=discovered.retrieval,
                    limitations=(linking.HAZARD_SOURCE_VERSION_LIMITATION,),
                )
            )
    return discovered, tuple(unevaluated)


def search_current_encounters(
    tables,
    *,
    now_epoch,
    aircraft_id=None,
    callsign=None,
    hazard_id=None,
    hazard_ids=None,
) -> CurrentEncounterQueryResult:
    if not any((aircraft_id, callsign, hazard_id, hazard_ids is not None)):
        return CurrentEncounterQueryResult(
            now_epoch=now_epoch,
            encounters=(),
            operationally_unevaluated=(),
            callsign_matches=(),
            rejected_hazard_selections=(),
            retrieval=(),
            limitations=(
                linking.NO_SNAPSHOT_LIMITATION,
                ENCOUNTER_SELECTION_REQUIRED,
            ),
        )

    aircraft_ids, callsign_matches, aircraft_retrieval, callsign_limitations = (
        _resolve_aircraft_filters(
            aircraft_id=aircraft_id,
            callsign=callsign,
            tables=tables,
            now_epoch=now_epoch,
        )
    )
    hazard_filter = _hazard_filter_ids(hazard_id=hazard_id, hazard_ids=hazard_ids)
    retrieval = list(aircraft_retrieval)
    limitations = [linking.NO_SNAPSHOT_LIMITATION, *callsign_limitations]
    unevaluated: list[OperationallyUnevaluatedEncounterCandidate] = []
    rejected: tuple[RejectedOperationalHazardSelection, ...] = ()

    if aircraft_id or callsign:
        encounters, encounter_retrieval = observed.load_encounter_candidates_for_aircraft(
            tables,
            aircraft_ids,
        )
        retrieval.extend(encounter_retrieval)
        if hazard_filter:
            allowed = set(hazard_filter)
            encounters = tuple(
                item
                for item in encounters
                if _text(item.get("hazard_id")) in allowed
            )
        authority, authority_unevaluated = _authority_for_aircraft_encounters(
            tables,
            encounters,
            now_epoch=now_epoch,
        )
        retrieval.extend(authority.retrieval)
        unevaluated.extend(authority_unevaluated)
        selected_versions = dict(authority.versions)
        if hazard_filter:
            selected_versions = {
                key: value
                for key, value in selected_versions.items()
                if key in set(hazard_filter)
            }
    else:
        selected, rejected, impact_unevaluated, discovery_retrieval = (
            _explicit_hazard_pins(
                tables,
                now_epoch=now_epoch,
                hazard_ids=hazard_filter,
                product_type=None,
                hazard_type=None,
            )
        )
        retrieval.extend(discovery_retrieval)
        for item in impact_unevaluated:
            unevaluated.append(
                OperationallyUnevaluatedEncounterCandidate(
                    encounter_id=item.encounter_id,
                    aircraft_id=item.aircraft_id,
                    hazard_id=item.hazard_id,
                    selected_source_version=item.selected_source_version,
                    reason=item.reason,
                    encounter_link=item.encounter_link,
                    hazard_link=item.hazard_link,
                    retrieval=item.retrieval,
                    limitations=item.limitations,
                )
            )
        selected_versions = selected
        encounters, encounter_retrieval = observed.load_encounter_candidates_for_hazards(
            tables,
            selected_versions.keys(),
        )
        retrieval.extend(encounter_retrieval)

    projections, projection_retrieval = observed.load_current_projection_index(
        tables,
        now_epoch=now_epoch,
    )
    retrieval.extend(projection_retrieval)
    decisions = observed.load_decision_candidates(tables, now_epoch=now_epoch)
    retrieval.extend(decisions.retrieval)
    observed_set = observed.assemble_observed_operational_set(
        now_epoch=now_epoch,
        current_projections_by_aircraft=projections,
        selected_hazard_versions=selected_versions,
        encounter_candidates=encounters,
        decision_candidates=decisions,
        retrieval=(),
    )
    exact = _ExactRows()
    confirmed: list[CurrentEncounterRecord] = []
    for candidate in observed_set.current_encounters:
        composed = _compose_encounter(
            tables,
            exact,
            candidate=candidate,
            observed_set=observed_set,
        )
        encounter_id = _text(candidate.get("encounter_id"))
        aircraft_key = _text(candidate.get("aircraft_id")).lower()
        hazard_key, source_version = linking.selected_encounter_hazard_lineage(
            composed.encounter,
            candidate,
            dict(observed_set.selected_hazard_versions),
        )
        if (
            composed.encounter_link.state is not linking.LinkState.PRESENT
            or not composed.encounter_is_current
            or composed.hazard_link.state is not linking.LinkState.PRESENT
        ):
            reason = (
                _reason_for_encounter_link(composed.encounter_link)
                if composed.encounter_link.state is not linking.LinkState.PRESENT
                or not composed.encounter_is_current
                else _reason_for_hazard_link(composed.hazard_link)
            )
            unevaluated.append(
                OperationallyUnevaluatedEncounterCandidate(
                    encounter_id=encounter_id,
                    aircraft_id=aircraft_key,
                    hazard_id=hazard_key,
                    selected_source_version=source_version,
                    reason=reason,
                    encounter_link=composed.encounter_link,
                    hazard_link=composed.hazard_link,
                    retrieval=(),
                    limitations=(linking.NO_SNAPSHOT_LIMITATION,),
                )
            )
            continue
        aircraft_row = exact.aircraft_row(tables, aircraft_key)
        aircraft, aircraft_link = linking.hydrate_aircraft(
            selected_aircraft_id=aircraft_key,
            hydrated=aircraft_row,
            now_epoch=now_epoch,
        )
        confirmed.append(
            CurrentEncounterRecord(
                hazard_id=hazard_key,
                source_version=source_version,
                encounter_id=encounter_id,
                aircraft_id=aircraft_key,
                encounter_context=composed,
                aircraft=aircraft,
                aircraft_is_current=aircraft_link.state is linking.LinkState.PRESENT
                and bool(
                    aircraft is not None
                    and current_set.is_current_aircraft(aircraft, now_epoch)
                ),
            )
        )
    retrieval.extend(exact.retrieval)
    return CurrentEncounterQueryResult(
        now_epoch=now_epoch,
        encounters=_sort_encounters(tuple(confirmed)),
        operationally_unevaluated=tuple(unevaluated),
        callsign_matches=callsign_matches,
        rejected_hazard_selections=rejected,
        retrieval=tuple(retrieval),
        limitations=tuple(limitations),
    )


def get_observed_network_state(tables, *, now_epoch) -> ObservedNetworkState:
    retrieval: list[linking.RetrievalObservation] = [
        linking.scan_observation("scan_aircraft_candidates"),
        linking.scan_observation("scan_encounter_candidates"),
    ]
    aircraft_candidates = readers.scan_aircraft_candidates(
        tables.aircraft,
        now_epoch=now_epoch,
    )
    current_aircraft_ids = tuple(
        sorted(
            {
                _text(item.get("aircraft_id")).lower()
                for item in aircraft_candidates
                if current_set.is_current_aircraft(item, now_epoch)
                and _text(item.get("aircraft_id"))
            }
        )
    )
    hazards = discovery.discover_current_hazards(tables, now_epoch=now_epoch)
    retrieval.extend(hazards.retrieval)
    current_hazard_ids = tuple(
        sorted(
            {
                _identity_value(match.identity, "hazard_id")
                for match in hazards.matches
                if match.is_current
            }
        )
    )
    selected_versions = {
        _identity_value(match.identity, "hazard_id"): _identity_value(
            match.identity,
            "source_version",
        )
        for match in hazards.matches
        if match.is_current and _identity_value(match.identity, "source_version")
    }
    projections, projection_retrieval = observed.load_current_projection_index(
        tables,
        now_epoch=now_epoch,
    )
    retrieval.extend(projection_retrieval)
    encounter_candidates = readers.scan_encounter_candidates(tables.encounters)
    decisions = observed.load_decision_candidates(tables, now_epoch=now_epoch)
    retrieval.extend(decisions.retrieval)
    observed_set = observed.assemble_observed_operational_set(
        now_epoch=now_epoch,
        current_projections_by_aircraft=projections,
        selected_hazard_versions=selected_versions,
        encounter_candidates=encounter_candidates,
        decision_candidates=decisions,
        retrieval=(),
    )
    current_encounter_ids = tuple(
        sorted(
            {
                _text(item.get("encounter_id"))
                for item in observed_set.current_encounters
                if item.get("encounter_id")
            }
        )
    )
    current_risk_ids = tuple(
        sorted(
            {
                _text(item.get("risk_id"))
                for item in observed_set.current_risks_by_encounter.values()
                if item.get("risk_id")
            }
        )
    )
    current_risk_id_set = set(current_risk_ids)
    current_recommendations = linking.select_current_recommendations(
        observed_set.recommendation_candidates,
        current_risk_ids=current_risk_id_set,
        now_epoch=now_epoch,
    )
    current_recommendation_ids = tuple(
        sorted(
            {
                _text(item.get("recommendation_id"))
                for item in current_recommendations
                if item.get("recommendation_id")
            }
        )
    )
    current_alerts = linking.select_current_alerts(
        observed_set.alert_candidates,
        current_risk_ids=current_risk_id_set,
        current_recommendation_ids=set(current_recommendation_ids),
        now_epoch=now_epoch,
    )
    current_alert_ids = tuple(
        sorted(
            {
                _text(item.get("alert_id"))
                for item in current_alerts
                if item.get("alert_id")
            }
        )
    )
    return ObservedNetworkState(
        as_of_epoch=now_epoch,
        current_aircraft_ids=current_aircraft_ids,
        current_hazard_ids=current_hazard_ids,
        current_encounter_ids=current_encounter_ids,
        current_risk_ids=current_risk_ids,
        current_recommendation_ids=current_recommendation_ids,
        current_alert_ids=current_alert_ids,
        retrieval=tuple(retrieval),
        limitations=(linking.NO_SNAPSHOT_LIMITATION,),
    )
