"""Request-scoped observed operational candidate loading.

This module loads independently observed DynamoDB candidates for one
caller-supplied now_epoch. It is not a transactional snapshot, not
globally complete, and does not cache across requests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from . import current_set
from . import discovery
from . import linking
from . import readers


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def epoch_to_utc_z(now_epoch: int) -> str:
    return datetime.fromtimestamp(
        int(now_epoch),
        tz=timezone.utc,
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _identity_value(identity: tuple[tuple[str, str], ...], name: str) -> str:
    for key, value in identity:
        if key == name:
            return value
    return ""


@dataclass(frozen=True)
class ObservedOperationalSet:
    now_epoch: int
    current_projections_by_aircraft: Mapping[str, str]
    selected_hazard_versions: Mapping[str, str]
    encounter_candidates: tuple[dict[str, Any], ...]
    current_encounters: tuple[dict[str, Any], ...]
    current_risks_by_encounter: Mapping[str, dict[str, Any]]
    recommendation_candidates: tuple[dict[str, Any], ...]
    alert_candidates: tuple[dict[str, Any], ...]
    retrieval: tuple[linking.RetrievalObservation, ...]


@dataclass(frozen=True)
class ConfirmedHazardPin:
    hazard_id: str
    source_version: str
    hazard: dict[str, Any]
    retrieval: tuple[linking.RetrievalObservation, ...]


@dataclass(frozen=True)
class FailedHazardPin:
    hazard_id: str
    selected_source_version: str
    exact_hazard: dict[str, Any] | None
    reason: str
    link: linking.Link
    retrieval: tuple[linking.RetrievalObservation, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class HazardVersionDiscovery:
    versions: Mapping[str, str]
    matches_by_id: Mapping[str, discovery.EntityMatch]
    requested_ids: tuple[str, ...]
    retrieval: tuple[linking.RetrievalObservation, ...]


@dataclass(frozen=True)
class DecisionCandidates:
    risk_candidates: tuple[dict[str, Any], ...]
    recommendation_candidates: tuple[dict[str, Any], ...]
    alert_candidates: tuple[dict[str, Any], ...]
    retrieval: tuple[linking.RetrievalObservation, ...]


@dataclass(frozen=True)
class PinConfirmationResult:
    confirmed: Mapping[str, str]
    failed: tuple[FailedHazardPin, ...]
    retrieval: tuple[linking.RetrievalObservation, ...]


def confirm_selected_hazard_pins(tables, intersecting, *, now_epoch) -> PinConfirmationResult:
    confirmed: dict[str, str] = {}
    failed: list[FailedHazardPin] = []
    retrieval: list[linking.RetrievalObservation] = []
    for evaluation in intersecting:
        hazard_id = _text(getattr(evaluation, "hazard_id", ""))
        selected_version = _text(getattr(evaluation, "source_version", ""))
        observation = linking.exact_pk_observation(
            "get_hazard_record",
            linking.NO_SNAPSHOT_LIMITATION,
        )
        retrieval.append(observation)
        exact = (
            readers.get_hazard_record(tables.hazards, hazard_id)
            if hazard_id
            else None
        )
        selected = (
            (("hazard_id", hazard_id), ("source_version", selected_version))
            if selected_version
            else ((("hazard_id", hazard_id),) if hazard_id else ())
        )
        if exact is None:
            failed.append(
                FailedHazardPin(
                    hazard_id=hazard_id,
                    selected_source_version=selected_version,
                    exact_hazard=None,
                    reason="HAZARD_PIN_MISSING",
                    link=linking.Link(
                        state=linking.LinkState.HYDRATION_MISSING,
                        kind=linking.LinkKind.VERSIONED,
                        selected_identity=selected,
                    ),
                    retrieval=(observation,),
                    limitations=(linking.NO_SNAPSHOT_LIMITATION,),
                )
            )
            continue
        observed_version = _text(exact.get("source_version"))
        observed = (
            ("hazard_id", _text(exact.get("hazard_id"))),
            ("source_version", observed_version),
        )
        if _text(exact.get("hazard_id")) != hazard_id:
            failed.append(
                FailedHazardPin(
                    hazard_id=hazard_id,
                    selected_source_version=selected_version,
                    exact_hazard=exact,
                    reason="HAZARD_PIN_IDENTITY_DRIFT",
                    link=linking.Link(
                        state=linking.LinkState.HYDRATION_IDENTITY_MISMATCH,
                        kind=linking.LinkKind.VERSIONED,
                        selected_identity=selected,
                        observed_identity=observed,
                    ),
                    retrieval=(observation,),
                    limitations=(linking.NO_SNAPSHOT_LIMITATION,),
                )
            )
            continue
        if not current_set.is_current_hazard(exact, now_epoch):
            failed.append(
                FailedHazardPin(
                    hazard_id=hazard_id,
                    selected_source_version=selected_version,
                    exact_hazard=exact,
                    reason="HAZARD_PIN_NOT_CURRENT",
                    link=linking.Link(
                        state=linking.LinkState.NOT_CURRENT,
                        kind=linking.LinkKind.VERSIONED,
                        selected_identity=selected,
                        observed_identity=observed,
                    ),
                    retrieval=(observation,),
                    limitations=(linking.NO_SNAPSHOT_LIMITATION,),
                )
            )
            continue
        if observed_version != selected_version:
            failed.append(
                FailedHazardPin(
                    hazard_id=hazard_id,
                    selected_source_version=selected_version,
                    exact_hazard=exact,
                    reason="HAZARD_PIN_VERSION_DRIFT",
                    link=linking.Link(
                        state=linking.LinkState.HYDRATION_VERSION_MISMATCH,
                        kind=linking.LinkKind.VERSIONED,
                        reason=(
                            "exact hazard row source_version differs from "
                            "selected version"
                        ),
                        selected_identity=selected,
                        observed_identity=observed,
                    ),
                    retrieval=(observation,),
                    limitations=(linking.NO_SNAPSHOT_LIMITATION,),
                )
            )
            continue
        if not _copied_identity_matches(evaluation, exact):
            failed.append(
                FailedHazardPin(
                    hazard_id=hazard_id,
                    selected_source_version=selected_version,
                    exact_hazard=exact,
                    reason="HAZARD_PIN_IDENTITY_DRIFT",
                    link=linking.Link(
                        state=linking.LinkState.HYDRATION_IDENTITY_MISMATCH,
                        kind=linking.LinkKind.VERSIONED,
                        selected_identity=selected,
                        observed_identity=observed,
                    ),
                    retrieval=(observation,),
                    limitations=(linking.NO_SNAPSHOT_LIMITATION,),
                )
            )
            continue
        confirmed[hazard_id] = selected_version
    return PinConfirmationResult(
        confirmed=MappingProxyType(dict(confirmed)),
        failed=tuple(failed),
        retrieval=tuple(retrieval),
    )


def _copied_identity_matches(evaluation, exact: dict[str, Any]) -> bool:
    pinned_hash = _text(getattr(evaluation, "geometry_hash", "") or "")
    if pinned_hash and pinned_hash != _text(exact.get("geometry_hash")):
        return False
    pinned_materialization = _text(
        getattr(evaluation, "materialization_id", "") or ""
    )
    if pinned_materialization and pinned_materialization != _text(
        exact.get("materialization_id")
    ):
        return False
    pinned_hazard = getattr(evaluation, "hazard", None) or {}
    pinned_type = _text(pinned_hazard.get("geometry_type"))
    if pinned_type and pinned_type != _text(exact.get("geometry_type")):
        return False
    return True


def load_current_projection_index(tables, *, now_epoch):
    observation = linking.scan_observation("scan_projection_index_candidates")
    candidates = readers.scan_projection_index_candidates(
        tables.projections,
        now_epoch=now_epoch,
    )
    indexed = current_set.index_current_projections(candidates, now_epoch)
    return MappingProxyType(dict(indexed)), (observation,)


def load_encounter_candidates_for_hazards(tables, hazard_ids: Iterable[str]):
    candidates: list[dict[str, Any]] = []
    retrieval: list[linking.RetrievalObservation] = []
    seen: set[str] = set()
    for hazard_id in hazard_ids:
        normalized = _text(hazard_id)
        if not normalized:
            continue
        retrieval.append(
            linking.query_observation("query_encounter_candidates_by_hazard")
        )
        for item in readers.query_encounter_candidates_by_hazard(
            tables.encounters,
            normalized,
        ):
            encounter_id = _text(item.get("encounter_id"))
            if encounter_id and encounter_id in seen:
                continue
            if encounter_id:
                seen.add(encounter_id)
            candidates.append(item)
    return tuple(candidates), tuple(retrieval)


def load_encounter_candidates_for_aircraft(tables, aircraft_ids: Iterable[str]):
    candidates: list[dict[str, Any]] = []
    retrieval: list[linking.RetrievalObservation] = []
    seen: set[str] = set()
    for aircraft_id in aircraft_ids:
        normalized = _text(aircraft_id).lower()
        if not normalized:
            continue
        retrieval.append(
            linking.query_observation("query_encounter_candidates_by_aircraft")
        )
        for item in readers.query_encounter_candidates_by_aircraft(
            tables.encounters,
            normalized,
        ):
            encounter_id = _text(item.get("encounter_id"))
            if encounter_id and encounter_id in seen:
                continue
            if encounter_id:
                seen.add(encounter_id)
            candidates.append(item)
    return tuple(candidates), tuple(retrieval)


def load_current_hazard_versions_for_ids(tables, hazard_ids, *, now_epoch):
    requested = tuple(_text(item) for item in hazard_ids if _text(item))
    result = discovery.discover_current_hazards(
        tables,
        now_epoch=now_epoch,
        hazard_ids=requested,
    )
    matches_by_id: dict[str, discovery.EntityMatch] = {}
    versions: dict[str, str] = {}
    for match in result.matches:
        hazard_id = _identity_value(match.identity, "hazard_id")
        if not hazard_id:
            continue
        matches_by_id[hazard_id] = match
        source_version = _identity_value(match.identity, "source_version")
        if match.is_current and source_version:
            versions[hazard_id] = source_version
    return HazardVersionDiscovery(
        versions=MappingProxyType(dict(versions)),
        matches_by_id=MappingProxyType(matches_by_id),
        requested_ids=requested,
        retrieval=result.retrieval,
    )


def load_decision_candidates(tables, *, now_epoch) -> DecisionCandidates:
    now_iso = epoch_to_utc_z(now_epoch)
    retrieval = (
        linking.scan_observation("scan_risk_candidates"),
        linking.scan_observation(
            "scan_recommendation_candidates",
            linking.ISO_CANDIDATE_LIMITATION,
            linking.RECOMMENDATION_ABSENCE_LIMITATION,
        ),
        linking.scan_observation(
            "scan_alert_candidates",
            linking.ISO_CANDIDATE_LIMITATION,
            linking.ALERT_ABSENCE_LIMITATION,
        ),
    )
    return DecisionCandidates(
        risk_candidates=tuple(readers.scan_risk_candidates(tables.risks)),
        recommendation_candidates=tuple(
            readers.scan_recommendation_candidates(
                tables.recommendations,
                now_iso=now_iso,
                project=False,
            )
        ),
        alert_candidates=tuple(
            readers.scan_alert_candidates(
                tables.alerts,
                now_iso=now_iso,
                project=False,
            )
        ),
        retrieval=retrieval,
    )


def assemble_observed_operational_set(
    *,
    now_epoch: int,
    current_projections_by_aircraft: Mapping[str, str],
    selected_hazard_versions: Mapping[str, str],
    encounter_candidates: Iterable[dict[str, Any]],
    decision_candidates: DecisionCandidates,
    retrieval: Iterable[linking.RetrievalObservation],
) -> ObservedOperationalSet:
    current_encounters = tuple(
        item
        for item in encounter_candidates
        if current_set.is_current_encounter(
            item,
            current_projection_ids=dict(current_projections_by_aircraft),
            current_hazard_versions=dict(selected_hazard_versions),
        )
    )
    selected_encounter_ids = {
        item.get("encounter_id")
        for item in current_encounters
        if item.get("encounter_id")
    }
    current_risks = current_set.index_latest_current_risks(
        decision_candidates.risk_candidates,
        current_encounter_ids=selected_encounter_ids,
        now_epoch=now_epoch,
    )
    return ObservedOperationalSet(
        now_epoch=now_epoch,
        current_projections_by_aircraft=MappingProxyType(
            dict(current_projections_by_aircraft)
        ),
        selected_hazard_versions=MappingProxyType(dict(selected_hazard_versions)),
        encounter_candidates=tuple(encounter_candidates),
        current_encounters=current_encounters,
        current_risks_by_encounter=MappingProxyType(dict(current_risks)),
        recommendation_candidates=decision_candidates.recommendation_candidates,
        alert_candidates=decision_candidates.alert_candidates,
        retrieval=tuple(retrieval),
    )
