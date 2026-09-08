"""Aircraft, encounter, hazard, and airport operational context loaders.

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


def _normalize_airport_id(airport_id) -> str:
    return _text(airport_id).upper()


_AIRPORT_DRIFT_FIELDS = (
    "updated_at_epoch",
    "source_metar_version",
    "source_taf_version",
    "taf_version_key",
)


def _status_metar_source_version(airport) -> str:
    return _text(airport.get("source_metar_version")) or _text(
        airport.get("metar_version")
    )


def _observed_metar_version(metar) -> str:
    if metar is None:
        return ""
    return _text(metar.get("metar_version"))


def _taf_compare_versions(airport, latest_taf):
    status_key = _text(airport.get("taf_version_key"))
    latest_key = _text(latest_taf.get("taf_version_key")) if latest_taf else ""
    if status_key and latest_key:
        return status_key, "taf_version_key", latest_key

    selected = (
        _text(airport.get("source_taf_version"))
        or _text(airport.get("taf_source_version"))
        or _text(airport.get("taf_version"))
    )
    observed = ""
    if latest_taf is not None:
        observed = (
            _text(latest_taf.get("source_version"))
            or _text(latest_taf.get("taf_version"))
        )
    return selected, "source_taf_version", observed


def _airport_identity(row, airport_id):
    pairs = [("airport_id", airport_id or _text((row or {}).get("airport_id")))]
    if row is not None and row.get("updated_at_epoch") is not None:
        pairs.append(("updated_at_epoch", str(row.get("updated_at_epoch"))))
    return tuple((name, value) for name, value in pairs if value)


def _airport_root_drift(initial, final):
    limitations = []
    if final is None:
        return True, limitations

    drifted = False
    missing_contract = False
    for field in _AIRPORT_DRIFT_FIELDS:
        if field not in initial or field not in final:
            missing_contract = True
            continue
        if initial.get(field) != final.get(field):
            drifted = True
    if missing_contract:
        limitations.append(linking.AIRPORT_DRIFT_FIELD_LIMITATION)
    return drifted, limitations


def _missing_weather_lineage_link(reason):
    return linking.Link(
        state=linking.LinkState.MISSING,
        kind=linking.LinkKind.VERSIONED,
        reason=reason,
    )


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
    hydrated_projection = (
        readers.get_projection_record(tables.projections, selected_projection_id)
        if selected_projection_id
        else None
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
    return linking.compose_hazard_impact_from_observed_records(
        aircraft_id=aircraft_id,
        selected_projection_id=selected_projection_id,
        hydrated_aircraft=hydrated_aircraft,
        hydrated_projection=hydrated_projection,
        encounter=encounter,
        now_epoch=now_epoch,
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
    encounter, _, _ = linking.hydrate_encounter(
        selected_encounter_id=selected_encounter_id or "",
        context_aircraft_id=aircraft_id,
        selected_projection_id=selected_projection_id,
        hydrated=hydrated_encounter,
        current_projection_ids=current_projection_ids,
        current_hazard_versions=current_hazard_versions,
    )
    hazard_id, selected_source_version = linking.selected_encounter_hazard_lineage(
        encounter,
        encounter_candidate,
        current_hazard_versions,
    )
    hydrated_hazard = (
        readers.get_hazard_record(tables.hazards, hazard_id)
        if hazard_id and selected_source_version
        else None
    )
    selected_risk = selected_risks.get(selected_encounter_id)
    hydrated_risk = (
        readers.get_risk_record(tables.risks, selected_risk["risk_id"])
        if selected_risk and selected_risk.get("risk_id")
        else None
    )
    return linking.compose_encounter_operational_context_from_observed_records(
        aircraft_id=aircraft_id,
        selected_projection_id=selected_projection_id,
        encounter_candidate=encounter_candidate,
        hydrated_encounter=hydrated_encounter,
        hydrated_hazard=hydrated_hazard,
        hydrated_risk=hydrated_risk,
        current_projection_ids=current_projection_ids,
        current_hazard_versions=current_hazard_versions,
        selected_risks=selected_risks,
        selected_encounter_ids=selected_encounter_ids,
        recommendation_candidates=recommendation_candidates,
        alert_candidates=alert_candidates,
        now_epoch=now_epoch,
    )


def build_airport_operational_context(
    tables,
    airport_id,
    *,
    now_epoch,
):
    airport_id = _normalize_airport_id(airport_id)
    if not airport_id:
        return None

    airport = readers.get_airport_status_record(tables.airports, airport_id)
    if airport is None:
        return None

    airport_is_current = current_set.is_current_airport_status(airport, now_epoch)
    station_id = _text(airport.get("station_id"))
    retrieval = [linking.exact_pk_observation("get_airport_status_record")]
    latest_metar = None
    latest_taf = None
    latest_taf_periods = ()

    if not station_id:
        retrieval[0] = linking.exact_pk_observation(
            "get_airport_status_record",
            linking.MISSING_STATION_LIMITATION,
        )
        metar_source_link = _missing_weather_lineage_link(
            linking.MISSING_STATION_LIMITATION,
        )
        taf_source_link = _missing_weather_lineage_link(
            linking.MISSING_STATION_LIMITATION,
        )
        taf_periods_link = _missing_weather_lineage_link(
            linking.MISSING_STATION_LIMITATION,
        )
    else:
        hydrated_metar = readers.get_metar_record(tables.metar, station_id)
        retrieval.append(linking.exact_pk_observation("get_metar_record"))
        latest_metar, metar_source_link = linking.latest_weather_source_link(
            selected_station_id=station_id,
            selected_source_version=_status_metar_source_version(airport),
            version_name="metar_version",
            hydrated=hydrated_metar,
            observed_version=_observed_metar_version(hydrated_metar),
        )

        hydrated_taf = readers.get_taf_record(tables.taf, station_id)
        retrieval.append(linking.exact_pk_observation("get_taf_record"))
        selected_taf_version, taf_version_name, observed_taf_version = (
            _taf_compare_versions(airport, hydrated_taf)
        )
        latest_taf, taf_source_link = linking.latest_weather_source_link(
            selected_station_id=station_id,
            selected_source_version=selected_taf_version,
            version_name=taf_version_name,
            hydrated=hydrated_taf,
            observed_version=observed_taf_version,
        )

        if latest_taf is None:
            taf_periods_link = _missing_weather_lineage_link(
                "latest TAF is absent; forecast periods were not queried "
                "from AirportStatus copied taf_version_key"
            )
        else:
            taf_version_key = _text(latest_taf.get("taf_version_key"))
            if not taf_version_key:
                taf_periods_link = linking.taf_periods_link_for(
                    (),
                    taf_version_key="",
                )
            else:
                latest_taf_periods = tuple(
                    readers.query_taf_period_rows_for_version(
                        tables.taf_periods,
                        taf_version_key,
                    )
                )
                taf_periods_link = linking.taf_periods_link_for(
                    latest_taf_periods,
                    taf_version_key=taf_version_key,
                )
                retrieval.append(
                    linking.query_observation(
                        "query_taf_period_rows_for_version",
                        linking.TAF_PERIOD_VERSION_LIMITATION,
                        consistency=linking.Consistency.CONSISTENT,
                    )
                )

    final = readers.get_airport_status_record(tables.airports, airport_id)
    drifted, drift_limitations = _airport_root_drift(airport, final)
    retrieval.append(
        linking.exact_pk_observation(
            "get_airport_status_record",
            linking.NO_SNAPSHOT_LIMITATION,
            *drift_limitations,
        )
    )
    if drifted:
        airport_status_link = linking.Link(
            state=linking.LinkState.HYDRATION_VERSION_MISMATCH,
            kind=linking.LinkKind.EXACT,
            reason="AirportStatus changed during descendant observation",
            selected_identity=_airport_identity(airport, airport_id),
            observed_identity=_airport_identity(final, airport_id),
        )
    else:
        airport_status_link = linking.Link(
            state=linking.LinkState.PRESENT,
            kind=linking.LinkKind.EXACT,
            selected_identity=_airport_identity(airport, airport_id),
            observed_identity=_airport_identity(final, airport_id),
        )

    return linking.AirportOperationalContext(
        airport=airport,
        airport_is_current=airport_is_current,
        station_id=station_id,
        latest_metar=latest_metar,
        metar_source_link=metar_source_link,
        latest_taf=latest_taf,
        taf_source_link=taf_source_link,
        latest_taf_periods=latest_taf_periods,
        taf_periods_link=taf_periods_link,
        airport_status_link=airport_status_link,
        retrieval=tuple(retrieval),
    )
