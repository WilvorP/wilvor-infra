"""Aircraft, encounter, and hazard operational context loaders.

Callers supply table handles and an explicit reference epoch. This module
does not acquire wall-clock time, cache results, call the Operational API,
or construct AWS resources.
"""

from __future__ import annotations

from datetime import datetime, timezone

from boto3.dynamodb.conditions import Attr

from . import current_set
from . import linking
from . import readers


def _epoch_to_utc_z(now_epoch: int) -> str:
    return datetime.fromtimestamp(
        int(now_epoch),
        tz=timezone.utc,
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_aircraft_id(aircraft_id) -> str:
    return _text(aircraft_id).lower()


def build_aircraft_operational_context(
    tables,
    aircraft_id,
    *,
    now_epoch,
):
    aircraft_id = _normalize_aircraft_id(aircraft_id)
    if not aircraft_id:
        return None

    aircraft = readers.get_aircraft_record(tables.aircraft, aircraft_id)
    if aircraft is None:
        return None

    now_iso = _epoch_to_utc_z(now_epoch)
    retrieval = [
        linking.exact_pk_observation("get_aircraft_record"),
        linking.scan_observation("scan_projection_index_candidates"),
        linking.scan_observation("scan_hazard_index_candidates"),
        linking.scan_observation("scan_encounter_candidates"),
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
        linking.exact_pk_observation("get_projection_record"),
        linking.exact_pk_observation("get_hazard_record"),
        linking.exact_pk_observation("get_risk_record"),
        linking.exact_pk_observation("get_encounter_record"),
    ]

    projection_candidates = readers.scan_projection_index_candidates(
        tables.projections,
        now_epoch=now_epoch,
        attr=Attr,
    )
    hazard_candidates = readers.scan_hazard_index_candidates(
        tables.hazards,
        now_epoch=now_epoch,
        attr=Attr,
    )
    encounter_candidates = readers.scan_encounter_candidates(
        tables.encounters,
        attr=Attr,
    )
    risk_candidates = readers.scan_risk_candidates(tables.risks)
    recommendation_candidates = readers.scan_recommendation_candidates(
        tables.recommendations,
        now_iso=now_iso,
        project=False,
        attr=Attr,
    )
    alert_candidates = readers.scan_alert_candidates(
        tables.alerts,
        now_iso=now_iso,
        project=False,
        attr=Attr,
    )

    current_projection_ids = current_set.index_current_projections(
        projection_candidates,
        now_epoch,
    )
    current_hazard_versions = current_set.index_current_hazard_versions(
        hazard_candidates,
        now_epoch,
    )

    selected_projection_id = current_projection_ids.get(aircraft_id)
    projection = None
    projection_is_current = False
    if not selected_projection_id:
        projection_link = linking.Link(
            state=linking.LinkState.MISSING,
            kind=linking.LinkKind.CURRENT_DEPENDENT,
            reason="no current projection identity in the observed candidate set",
        )
    else:
        hydrated_projection = readers.get_projection_record(
            tables.projections,
            selected_projection_id,
        )
        projection, projection_link = linking.hydrate_projection(
            selected_projection_id=selected_projection_id,
            context_aircraft_id=aircraft_id,
            hydrated=hydrated_projection,
            now_epoch=now_epoch,
        )
        projection_is_current = (
            projection_link.state == linking.LinkState.PRESENT
        )

    selected_encounters = [
        item
        for item in encounter_candidates
        if _text(item.get("aircraft_id")).lower() == aircraft_id
        and current_set.is_current_encounter(
            item,
            current_projection_ids=current_projection_ids,
            current_hazard_versions=current_hazard_versions,
        )
    ]
    selected_encounter_ids = {
        item.get("encounter_id")
        for item in selected_encounters
        if item.get("encounter_id")
    }
    selected_risks = current_set.index_latest_current_risks(
        risk_candidates,
        current_encounter_ids=selected_encounter_ids,
        now_epoch=now_epoch,
    )

    encounters = tuple(
        _build_encounter_context(
            tables,
            aircraft_id=aircraft_id,
            selected_projection_id=selected_projection_id or "",
            encounter_candidate=item,
            current_projection_ids=current_projection_ids,
            current_hazard_versions=current_hazard_versions,
            selected_risks=selected_risks,
            selected_encounter_ids=selected_encounter_ids,
            recommendation_candidates=recommendation_candidates,
            alert_candidates=alert_candidates,
            now_epoch=now_epoch,
        )
        for item in selected_encounters
    )

    return linking.AircraftOperationalContext(
        aircraft=aircraft,
        aircraft_is_current=current_set.is_current_aircraft(
            aircraft,
            now_epoch,
        ),
        projection=projection,
        projection_is_current=projection_is_current,
        encounters=encounters,
        projection_link=projection_link,
        retrieval=tuple(retrieval),
    )


def _hazard_version_link(hazard_id, source_version, observed):
    selected = (
        (("hazard_id", hazard_id), ("source_version", source_version))
        if source_version
        else ((("hazard_id", hazard_id),) if hazard_id else ())
    )
    if observed is None:
        observed_identity = ()
    else:
        observed_identity = (
            ("hazard_id", _text(observed.get("hazard_id"))),
            ("source_version", _text(observed.get("source_version"))),
        )
        observed_identity = tuple(
            pair for pair in observed_identity if pair[1]
        )
    return linking.Link(
        state=linking.LinkState.PRESENT,
        kind=linking.LinkKind.VERSIONED,
        selected_identity=selected,
        observed_identity=observed_identity,
    )


def _build_hazard_impact(
    tables,
    *,
    encounter_candidate,
    current_projection_ids,
    current_hazard_versions,
    selected_risks,
    selected_encounter_ids,
    recommendation_candidates,
    alert_candidates,
    now_epoch,
):
    aircraft_id = _normalize_aircraft_id(encounter_candidate.get("aircraft_id"))
    selected_projection_id = current_projection_ids.get(aircraft_id, "")
    hydrated_aircraft = (
        readers.get_aircraft_record(tables.aircraft, aircraft_id)
        if aircraft_id
        else None
    )
    aircraft, aircraft_link = linking.hydrate_aircraft(
        selected_aircraft_id=aircraft_id,
        hydrated=hydrated_aircraft,
        now_epoch=now_epoch,
    )
    aircraft_is_current = bool(
        aircraft is not None
        and current_set.is_current_aircraft(aircraft, now_epoch)
    )

    if not selected_projection_id:
        projection = None
        projection_is_current = False
        projection_link = linking.Link(
            state=linking.LinkState.MISSING,
            kind=linking.LinkKind.CURRENT_DEPENDENT,
            reason="no current projection identity in the observed candidate set",
        )
    else:
        hydrated_projection = readers.get_projection_record(
            tables.projections,
            selected_projection_id,
        )
        projection, projection_link = linking.hydrate_projection(
            selected_projection_id=selected_projection_id,
            context_aircraft_id=aircraft_id,
            hydrated=hydrated_projection,
            now_epoch=now_epoch,
        )
        projection_is_current = (
            projection_link.state == linking.LinkState.PRESENT
        )

    encounter = _build_encounter_context(
        tables,
        aircraft_id=aircraft_id,
        selected_projection_id=selected_projection_id,
        encounter_candidate=encounter_candidate,
        current_projection_ids=current_projection_ids,
        current_hazard_versions=current_hazard_versions,
        selected_risks=selected_risks,
        selected_encounter_ids=selected_encounter_ids,
        recommendation_candidates=recommendation_candidates,
        alert_candidates=alert_candidates,
        now_epoch=now_epoch,
    )
    return linking.HazardImpactContext(
        aircraft=aircraft,
        aircraft_is_current=aircraft_is_current,
        aircraft_link=aircraft_link,
        projection=projection,
        projection_is_current=projection_is_current,
        projection_link=projection_link,
        encounter=encounter,
    )


def build_hazard_operational_context(
    tables,
    hazard_id,
    *,
    now_epoch,
):
    hazard_id = _text(hazard_id)
    if not hazard_id:
        return None

    hazard = readers.get_hazard_record(tables.hazards, hazard_id)
    if hazard is None:
        return None

    now_iso = _epoch_to_utc_z(now_epoch)
    source_version = _text(hazard.get("source_version"))
    hazard_is_lifecycle_active = current_set.is_lifecycle_active_hazard(
        hazard,
        now_epoch,
    )
    hazard_is_current = current_set.is_current_hazard(hazard, now_epoch)
    evaluate_impacts = hazard_is_current and bool(source_version)
    retrieval = []
    impacts = ()

    if hazard_is_current and not source_version:
        retrieval.append(
            linking.exact_pk_observation(
                "get_hazard_record",
                linking.HAZARD_SOURCE_VERSION_LIMITATION,
            )
        )
        hazard_version_link = linking.Link(
            state=linking.LinkState.MISSING,
            kind=linking.LinkKind.VERSIONED,
            selected_identity=(("hazard_id", hazard_id),),
            reason=linking.HAZARD_SOURCE_VERSION_LIMITATION,
        )
    else:
        retrieval.append(linking.exact_pk_observation("get_hazard_record"))
        hazard_version_link = _hazard_version_link(
            hazard_id,
            source_version,
            hazard,
        )

    if evaluate_impacts:
        retrieval.extend(
            [
                linking.query_observation(
                    "query_encounter_candidates_by_hazard",
                ),
                linking.scan_observation("scan_projection_index_candidates"),
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
                linking.exact_pk_observation("get_aircraft_record"),
                linking.exact_pk_observation("get_projection_record"),
                linking.exact_pk_observation("get_encounter_record"),
                linking.exact_pk_observation("get_risk_record"),
                linking.exact_pk_observation("get_hazard_record"),
            ]
        )
        current_hazard_versions = {hazard_id: source_version}
        encounter_candidates = readers.query_encounter_candidates_by_hazard(
            tables.encounters,
            hazard_id,
            attr=Attr,
        )
        projection_candidates = readers.scan_projection_index_candidates(
            tables.projections,
            now_epoch=now_epoch,
            attr=Attr,
        )
        risk_candidates = readers.scan_risk_candidates(tables.risks)
        recommendation_candidates = readers.scan_recommendation_candidates(
            tables.recommendations,
            now_iso=now_iso,
            project=False,
            attr=Attr,
        )
        alert_candidates = readers.scan_alert_candidates(
            tables.alerts,
            now_iso=now_iso,
            project=False,
            attr=Attr,
        )
        current_projection_ids = current_set.index_current_projections(
            projection_candidates,
            now_epoch,
        )
        selected_encounters = [
            item
            for item in encounter_candidates
            if current_set.is_current_encounter(
                item,
                current_projection_ids=current_projection_ids,
                current_hazard_versions=current_hazard_versions,
            )
        ]
        selected_encounter_ids = {
            item.get("encounter_id")
            for item in selected_encounters
            if item.get("encounter_id")
        }
        selected_risks = current_set.index_latest_current_risks(
            risk_candidates,
            current_encounter_ids=selected_encounter_ids,
            now_epoch=now_epoch,
        )
        impacts = tuple(
            _build_hazard_impact(
                tables,
                encounter_candidate=item,
                current_projection_ids=current_projection_ids,
                current_hazard_versions=current_hazard_versions,
                selected_risks=selected_risks,
                selected_encounter_ids=selected_encounter_ids,
                recommendation_candidates=recommendation_candidates,
                alert_candidates=alert_candidates,
                now_epoch=now_epoch,
            )
            for item in selected_encounters
        )

    final = readers.get_hazard_record(tables.hazards, hazard_id)
    retrieval.append(
        linking.exact_pk_observation(
            "get_hazard_record",
            linking.NO_SNAPSHOT_LIMITATION,
        )
    )
    if source_version and final is not None:
        final_version = _text(final.get("source_version"))
        if final_version != source_version:
            hazard_version_link = linking.Link(
                state=linking.LinkState.HYDRATION_VERSION_MISMATCH,
                kind=linking.LinkKind.VERSIONED,
                reason=(
                    "exact hazard row source_version differs from "
                    "selected version"
                ),
                selected_identity=(
                    ("hazard_id", hazard_id),
                    ("source_version", source_version),
                ),
                observed_identity=(
                    ("hazard_id", _text(final.get("hazard_id"))),
                    ("source_version", final_version),
                ),
            )

    return linking.HazardOperationalContext(
        hazard=hazard,
        hazard_is_lifecycle_active=hazard_is_lifecycle_active,
        hazard_is_current=hazard_is_current,
        source_version=source_version,
        impacts=impacts,
        hazard_version_link=hazard_version_link,
        retrieval=tuple(retrieval),
    )


def _build_encounter_context(
    tables,
    *,
    aircraft_id,
    selected_projection_id,
    encounter_candidate,
    current_projection_ids,
    current_hazard_versions,
    selected_risks,
    selected_encounter_ids,
    recommendation_candidates,
    alert_candidates,
    now_epoch,
):
    selected_encounter_id = encounter_candidate.get("encounter_id")
    hydrated_encounter = (
        readers.get_encounter_record(
            tables.encounters,
            selected_encounter_id,
        )
        if selected_encounter_id
        else None
    )
    encounter, encounter_link, encounter_is_current = linking.hydrate_encounter(
        selected_encounter_id=selected_encounter_id or "",
        context_aircraft_id=aircraft_id,
        selected_projection_id=selected_projection_id,
        hydrated=hydrated_encounter,
        current_projection_ids=current_projection_ids,
        current_hazard_versions=current_hazard_versions,
    )

    lineage_source = encounter or encounter_candidate
    hazard_id = _text(lineage_source.get("hazard_id"))
    selected_source_version = current_hazard_versions.get(hazard_id, "")
    if not hazard_id or not selected_source_version:
        hazard = None
        hazard_link = linking.Link(
            state=linking.LinkState.MISSING,
            kind=linking.LinkKind.VERSIONED,
            selected_identity=(
                (("hazard_id", hazard_id),) if hazard_id else ()
            ),
        )
    else:
        hydrated_hazard = readers.get_hazard_record(tables.hazards, hazard_id)
        hazard, hazard_link = linking.hydrate_hazard(
            selected_hazard_id=hazard_id,
            selected_source_version=selected_source_version,
            hydrated=hydrated_hazard,
        )

    selected_risk = selected_risks.get(selected_encounter_id)
    if not selected_risk or not selected_risk.get("risk_id"):
        risk = None
        risk_link = linking.Link(
            state=linking.LinkState.MISSING,
            kind=linking.LinkKind.EXACT,
            selected_identity=(
                (("encounter_id", selected_encounter_id),)
                if selected_encounter_id
                else ()
            ),
            reason="no current risk identity in the observed candidate set",
        )
        current_risk_ids = set()
    else:
        risk, risk_link = linking.hydrate_risk(
            selected_risk_id=selected_risk["risk_id"],
            selected_encounter_id=selected_encounter_id,
            hydrated=readers.get_risk_record(
                tables.risks,
                selected_risk["risk_id"],
            ),
            current_encounter_ids=selected_encounter_ids,
            now_epoch=now_epoch,
        )
        current_risk_ids = (
            {risk["risk_id"]}
            if risk is not None and risk_link.state == linking.LinkState.PRESENT
            else set()
        )

    recommendations = linking.select_current_recommendations(
        recommendation_candidates,
        current_risk_ids=current_risk_ids,
        now_epoch=now_epoch,
    )
    recommendation_link = linking.recommendation_link_for(recommendations)
    current_recommendation_ids = {
        _text(item.get("recommendation_id"))
        for item in recommendations
        if item.get("recommendation_id")
    }
    alerts = linking.select_current_alerts(
        alert_candidates,
        current_risk_ids=current_risk_ids,
        current_recommendation_ids=current_recommendation_ids,
        now_epoch=now_epoch,
    )
    alert_link = linking.alert_link_for(alerts)

    return linking.compose_encounter_operational_context(
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
        alert_link=alert_link,
    )
