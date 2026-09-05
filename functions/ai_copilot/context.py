from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from evidence import EvidenceCatalog
from schemas import now_iso


def _select(
    item: Any,
    fields: list[str],
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    return {
        field: item.get(field)
        for field in fields
    }


def _records(
    value: Any,
    fields: list[str],
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        selected
        for item in value[:limit]
        if (selected := _select(item, fields))
        is not None
    ]


def _unique_strings(*collections: Any) -> list[str]:
    result = []
    seen = set()
    for collection in collections:
        if not isinstance(collection, list):
            continue
        for item in collection:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if text and text not in seen:
                seen.add(text)
                result.append(text)
    return result


def _parallel(**calls) -> dict[str, Any]:
    with ThreadPoolExecutor(
        max_workers=len(calls)
    ) as executor:
        futures = {
            name: executor.submit(callable_)
            for name, callable_ in calls.items()
        }
        return {
            name: future.result()
            for name, future in futures.items()
        }


def freshness_warnings(
    freshness: Any,
) -> list[str]:
    if not isinstance(freshness, dict):
        return ["Operational source freshness is unavailable."]
    sources = freshness.get("sources")
    if not isinstance(sources, dict):
        return ["Operational source freshness is unavailable."]

    warnings = []
    for source_name, state in sources.items():
        if not isinstance(state, dict):
            warnings.append(
                f"{source_name.upper()} freshness is unavailable."
            )
            continue
        status = str(
            state.get("status") or "UNKNOWN"
        ).upper()
        if status in {
            "STALE",
            "UNAVAILABLE",
            "UNKNOWN",
            "CRITICAL",
        }:
            warnings.append(
                f"{source_name.upper()} data freshness is {status}."
            )
    return warnings


def _timestamp_state(value: Any) -> str | None:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        expired = parsed.astimezone(
            timezone.utc
        ) <= datetime.now(timezone.utc)
        return "EXPIRED" if expired else "CURRENT"
    except (TypeError, ValueError):
        return "UNKNOWN"


def _expired(value: Any) -> bool:
    return _timestamp_state(value) == "EXPIRED"


def _append_validity_warning(
    warnings: list[str],
    value: Any,
    label: str,
) -> None:
    state = _timestamp_state(value)
    if state == "EXPIRED":
        warnings.append(f"{label} is expired.")
    elif state == "UNKNOWN":
        warnings.append(
            f"{label} validity timestamp is invalid."
        )


def _mark_expired(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    for record in records:
        if "valid_until_utc" in record:
            record["isExpired"] = _expired(
                record.get("valid_until_utc")
            )
    return records


def material_context(
    context: dict[str, Any],
) -> dict[str, Any]:
    ignored = {
        "generatedAt",
        "ageSeconds",
        "evidenceCatalog",
    }

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: clean(child)
                for key, child in value.items()
                if key not in ignored
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return clean(deepcopy(context))


class ContextBuilders:
    AIRCRAFT_FIELDS = [
        "aircraft_id",
        "callsign",
        "origin_country",
        "latitude",
        "longitude",
        "baro_altitude_ft",
        "geo_altitude_ft",
        "ground_speed_kt",
        "track_deg",
        "vertical_rate_fpm",
        "on_ground",
        "current_h3_cell",
        "position_time_utc",
        "last_contact_utc",
        "position_age_seconds",
        "freshness_status",
        "state_version",
        "expires_at_epoch",
    ]
    PROJECTION_FIELDS = [
        "projection_id",
        "aircraft_state_version",
        "generated_at_utc",
        "valid_until_utc",
        "projection_horizon_min",
        "point_count",
        "confidence",
        "projection_status",
        "projection_trigger_reason",
        "projection_algorithm_version",
        "projection_config_version",
    ]
    PROJECTION_POINT_FIELDS = [
        "point_sequence_number",
        "horizon_min",
        "projected_time_utc",
        "latitude",
        "longitude",
        "estimated_altitude_ft",
        "confidence",
    ]
    ENCOUNTER_FIELDS = [
        "encounter_id",
        "aircraft_id",
        "projection_id",
        "hazard_id",
        "hazard_source_version",
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
        "encounter_state",
        "detected_at_utc",
        "valid_from_utc",
        "valid_to_utc",
    ]
    RISK_FIELDS = [
        "risk_id",
        "encounter_id",
        "aircraft_id",
        "hazard_id",
        "hazard_source_version",
        "projection_id",
        "hazard_type",
        "severity",
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
        "generated_at_utc",
        "valid_until_utc",
    ]
    RECOMMENDATION_FIELDS = [
        "recommendation_id",
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
        "evidence_references",
        "airport_evaluation_id",
        "preferred_airport_id",
        "preferred_airport_score",
        "candidate_airport_summaries",
        "no_suitable_candidate_reason",
        "ruleset_version",
        "valid_from_utc",
        "valid_until_utc",
        "advisory_notice",
    ]
    ALERT_FIELDS = [
        "alert_id",
        "aircraft_id",
        "hazard_id",
        "hazard_source_version",
        "recommendation_id",
        "risk_id",
        "risk_level",
        "risk_score",
        "primary_action_type",
        "preferred_airport_id",
        "alert_type",
        "alert_state",
        "state_reason",
        "message",
        "notification_count",
        "last_notified_at_utc",
        "created_at_utc",
        "updated_at_utc",
        "valid_until_utc",
        "resolved_at_utc",
        "superseded_by_alert_id",
    ]
    ASSESSMENT_FIELDS = [
        "evaluation_id",
        "airport_assessment_id",
        "airport_id",
        "risk_id",
        "aircraft_id",
        "hazard_id",
        "airport_name",
        "hard_filter_passed",
        "rejection_reasons",
        "distance_nm",
        "eta_minutes",
        "estimated_arrival_time_utc",
        "eta_uncertainty_minutes",
        "weather_risk_level",
        "route_safety_status",
        "runway_evidence_status",
        "congestion_evidence_status",
        "assessment_status",
        "taf_period_ids",
        "distance_score",
        "weather_score",
        "taf_score",
        "total_airport_score",
        "rank",
        "known_limitations",
    ]

    def __init__(self, client) -> None:
        self.client = client

    @staticmethod
    def _base(
        subject_type: str,
        subject_id: str,
    ) -> dict[str, Any]:
        return {
            "contextVersion": "1.0",
            "generatedAt": now_iso(),
            "subject": {
                "type": subject_type,
                "id": subject_id,
            },
        }

    @staticmethod
    def _add_record_evidence(
        catalog: EvidenceCatalog,
        source_type: str,
        source_id: Any,
        record: Any,
        observed_at: str | None = None,
    ) -> None:
        if isinstance(record, dict):
            catalog.add_tree(
                source_type=source_type,
                source_id=str(source_id or "unknown"),
                value=record,
                observed_at=observed_at,
            )

    @staticmethod
    def _add_freshness_evidence(
        catalog: EvidenceCatalog,
        freshness: Any,
    ) -> None:
        if not isinstance(freshness, dict):
            return
        for source, state in (
            freshness.get("sources") or {}
        ).items():
            if isinstance(state, dict):
                catalog.add_tree(
                    source_type="Freshness",
                    source_id=source,
                    value=state,
                    observed_at=state.get("latestAt"),
                )

    def build_network_context(self) -> dict[str, Any]:
        values = _parallel(
            overview=self.client.overview,
            freshness=self.client.freshness,
            system_health=self.client.system_health,
        )
        overview = values["overview"]
        freshness = values["freshness"]
        system_health = values["system_health"]
        catalog = EvidenceCatalog()
        catalog.add_tree(
            source_type="OperationalOverview",
            source_id="current",
            value=overview,
            observed_at=overview.get("generatedAt"),
        )
        catalog.add_tree(
            source_type="SystemHealth",
            source_id="current",
            value=system_health,
            observed_at=system_health.get("generatedAt"),
        )
        self._add_freshness_evidence(
            catalog,
            freshness,
        )
        context = self._base("NETWORK", "CURRENT")
        context.update(
            {
                "overview": overview,
                "freshness": freshness,
                "systemHealth": system_health,
                "limitations": [],
                "dataFreshnessWarnings": (
                    freshness_warnings(freshness)
                ),
                "evidenceCatalog": catalog.items(),
            }
        )
        return context

    def build_aircraft_context(
        self,
        aircraft_id: str,
    ) -> dict[str, Any]:
        values = _parallel(
            detail=lambda: self.client.aircraft(
                aircraft_id
            ),
            freshness=self.client.freshness,
        )
        detail = values["detail"]
        freshness = values["freshness"]
        aircraft = _select(
            detail.get("aircraft"),
            self.AIRCRAFT_FIELDS,
        )
        projection = _select(
            detail.get("projection"),
            self.PROJECTION_FIELDS,
        )
        points = _records(
            detail.get("projectionPoints"),
            self.PROJECTION_POINT_FIELDS,
            limit=12,
        )
        encounters = _records(
            detail.get("recentEncounters"),
            self.ENCOUNTER_FIELDS,
        )
        risks = _mark_expired(
            _records(
                detail.get("recentRisks"),
                self.RISK_FIELDS,
            )
        )
        recommendations = _mark_expired(
            _records(
                detail.get("recentRecommendations"),
                self.RECOMMENDATION_FIELDS,
            )
        )
        alerts = _mark_expired(
            _records(
                detail.get("recentAlerts"),
                self.ALERT_FIELDS,
            )
        )
        hazards = []
        seen_hazards = set()
        for encounter in encounters:
            hazard_id = encounter.get("hazard_id")
            if not hazard_id or hazard_id in seen_hazards:
                continue
            seen_hazards.add(hazard_id)
            hazards.append(
                {
                    "hazard_id": hazard_id,
                    "hazard_source_version": (
                        encounter.get(
                            "hazard_source_version"
                        )
                    ),
                    "hazard_type": encounter.get(
                        "hazard_type"
                    ),
                    "severity": encounter.get(
                        "severity"
                    ),
                    "valid_from_utc": encounter.get(
                        "valid_from_utc"
                    ),
                    "valid_to_utc": encounter.get(
                        "valid_to_utc"
                    ),
                }
            )

        limitations = [
            "Filed route unavailable.",
            "Fuel state unavailable.",
            "Aircraft performance limits unavailable.",
            "ATC clearance unavailable.",
            "Airline operating policy unavailable.",
        ]
        if projection is None:
            limitations.append(
                "Current projection unavailable."
            )
        for risk in risks:
            limitations.extend(
                risk.get("limitations") or []
            )
        for recommendation in recommendations:
            limitations.extend(
                recommendation.get("limitations") or []
            )
        limitations = _unique_strings(limitations)
        warnings = freshness_warnings(freshness)
        aircraft_freshness = str(
            (aircraft or {}).get("freshness_status")
            or "UNKNOWN"
        ).upper()
        if aircraft_freshness in {
            "STALE",
            "UNAVAILABLE",
            "UNKNOWN",
        }:
            warnings.append(
                "Requested aircraft state freshness is "
                f"{aircraft_freshness}."
            )
        expires_at = (aircraft or {}).get(
            "expires_at_epoch"
        )
        if expires_at is not None:
            try:
                if int(expires_at) <= int(
                    datetime.now(
                        timezone.utc
                    ).timestamp()
                ):
                    warnings.append(
                        "Requested aircraft state is expired."
                    )
            except (TypeError, ValueError):
                warnings.append(
                    "Requested aircraft state expiry is invalid."
                )
        if any(item.get("isExpired") for item in risks):
            warnings.append(
                "One or more returned risk results are expired."
            )
        if any(
            item.get("isExpired")
            for item in recommendations
        ):
            warnings.append(
                "One or more returned recommendations are expired."
            )
        warnings = _unique_strings(warnings)

        catalog = EvidenceCatalog()
        subject_id = (
            (aircraft or {}).get("aircraft_id")
            or aircraft_id.lower()
        )
        self._add_record_evidence(
            catalog,
            "AircraftCurrentState",
            subject_id,
            aircraft,
            (aircraft or {}).get("position_time_utc"),
        )
        self._add_record_evidence(
            catalog,
            "AircraftProjection",
            (projection or {}).get("projection_id"),
            projection,
            (projection or {}).get("generated_at_utc"),
        )
        for encounter in encounters:
            self._add_record_evidence(
                catalog,
                "AircraftHazardEncounter",
                encounter.get("encounter_id"),
                encounter,
                encounter.get("detected_at_utc"),
            )
        for risk in risks:
            self._add_record_evidence(
                catalog,
                "RiskResult",
                risk.get("risk_id"),
                risk,
                risk.get("generated_at_utc"),
            )
        for recommendation in recommendations:
            self._add_record_evidence(
                catalog,
                "Recommendation",
                recommendation.get(
                    "recommendation_id"
                ),
                recommendation,
            )
        for alert in alerts:
            self._add_record_evidence(
                catalog,
                "ActiveAlert",
                alert.get("alert_id"),
                alert,
                alert.get("updated_at_utc"),
            )
        self._add_freshness_evidence(
            catalog,
            freshness,
        )

        context = self._base(
            "AIRCRAFT",
            str(subject_id),
        )
        context.update(
            {
                "aircraft": aircraft,
                "projection": projection,
                "projectionPoints": points,
                "encounters": encounters,
                "risks": risks,
                "recommendations": recommendations,
                "alerts": alerts,
                "hazards": hazards,
                "freshness": freshness,
                "limitations": limitations,
                "dataFreshnessWarnings": warnings,
                "evidenceCatalog": catalog.items(),
            }
        )
        return context

    def build_airport_context(
        self,
        airport_id: str,
    ) -> dict[str, Any]:
        values = _parallel(
            detail=lambda: self.client.airport(
                airport_id
            ),
            freshness=self.client.freshness,
        )
        detail = values["detail"]
        freshness = values["freshness"]
        airport = _select(
            detail.get("airport"),
            [
                "airport_id",
                "station_id",
                "station_name",
                "latitude",
                "longitude",
                "elevation_m",
                "has_metar",
                "has_taf",
                "metar_freshness_status",
                "taf_freshness_status",
                "weather_risk_level",
                "weather_impact_status",
                "assessment_status",
                "is_diversion_weather_ready",
                "status_reasons",
                "known_limitations",
                "updated_at_utc",
                "expires_at_epoch",
            ],
        )
        metar = _select(
            detail.get("metar"),
            [
                "station_id",
                "metar_version",
                "observed_time_utc",
                "receipt_time_utc",
                "wind_direction_deg",
                "wind_speed_kt",
                "wind_gust_kt",
                "visibility_sm",
                "ceiling_ft",
                "flight_category",
                "weather_string",
                "weather_codes",
            ],
        )
        taf = _select(
            detail.get("taf"),
            [
                "station_id",
                "taf_version",
                "source_version",
                "issued_at_utc",
                "valid_from_utc",
                "valid_to_utc",
                "period_materialization_status",
                "forecast_period_count",
            ],
        )
        periods = _records(
            detail.get("tafForecastPeriods"),
            [
                "period_id",
                "period_from_utc",
                "period_to_utc",
                "change_indicator",
                "wind_direction_deg",
                "wind_speed_kt",
                "wind_gust_kt",
                "visibility_sm",
                "ceiling_ft",
                "flight_category",
                "weather_codes",
                "materialization_status",
            ],
            limit=20,
        )
        assessments = _records(
            detail.get("recentAssessments"),
            self.ASSESSMENT_FIELDS,
        )
        limitations = [
            "Active runway unavailable.",
            "Aircraft-specific runway requirement unavailable.",
            "Airport congestion evidence unavailable.",
        ]
        limitations.extend(
            (airport or {}).get("known_limitations")
            or []
        )
        for assessment in assessments:
            limitations.extend(
                assessment.get("known_limitations")
                or []
            )
        limitations = _unique_strings(limitations)
        warnings = freshness_warnings(freshness)
        for source, key in (
            ("METAR", "metar_freshness_status"),
            ("TAF", "taf_freshness_status"),
        ):
            status = str(
                (airport or {}).get(key) or "UNKNOWN"
            ).upper()
            if status in {
                "STALE",
                "UNAVAILABLE",
                "UNKNOWN",
                "MISSING",
            }:
                warnings.append(
                    f"Requested airport {source} freshness is {status}."
                )
        airport_expires_at = (airport or {}).get(
            "expires_at_epoch"
        )
        if airport_expires_at is not None:
            try:
                if int(airport_expires_at) <= int(
                    datetime.now(
                        timezone.utc
                    ).timestamp()
                ):
                    warnings.append(
                        "Requested airport status is expired."
                    )
            except (TypeError, ValueError):
                warnings.append(
                    "Requested airport status expiry is invalid."
                )
        warnings = _unique_strings(warnings)

        catalog = EvidenceCatalog()
        subject_id = (
            (airport or {}).get("airport_id")
            or airport_id.upper()
        )
        self._add_record_evidence(
            catalog,
            "AirportStatus",
            subject_id,
            airport,
            (airport or {}).get("updated_at_utc"),
        )
        self._add_record_evidence(
            catalog,
            "MetarLatest",
            (metar or {}).get("station_id"),
            metar,
            (metar or {}).get("observed_time_utc"),
        )
        self._add_record_evidence(
            catalog,
            "TafLatest",
            (taf or {}).get("station_id"),
            taf,
            (taf or {}).get("issued_at_utc"),
        )
        for assessment in assessments:
            self._add_record_evidence(
                catalog,
                "AirportAssessment",
                assessment.get(
                    "airport_assessment_id"
                ),
                assessment,
            )
        self._add_freshness_evidence(
            catalog,
            freshness,
        )
        context = self._base(
            "AIRPORT",
            str(subject_id),
        )
        context.update(
            {
                "airport": airport,
                "metar": metar,
                "taf": taf,
                "tafForecastPeriods": periods,
                "recentAssessments": assessments,
                "freshness": freshness,
                "limitations": limitations,
                "dataFreshnessWarnings": warnings,
                "evidenceCatalog": catalog.items(),
            }
        )
        return context

    def build_recommendation_context(
        self,
        recommendation_id: str,
    ) -> dict[str, Any]:
        values = _parallel(
            detail=lambda: self.client.recommendation(
                recommendation_id
            ),
            freshness=self.client.freshness,
        )
        detail = values["detail"]
        freshness = values["freshness"]
        recommendation = _select(
            detail.get("recommendation"),
            self.RECOMMENDATION_FIELDS,
        )
        risk = _select(
            detail.get("risk"),
            self.RISK_FIELDS,
        )
        assessments = _records(
            detail.get("airportAssessments"),
            self.ASSESSMENT_FIELDS,
        )
        limitations = _unique_strings(
            (risk or {}).get("limitations") or [],
            (recommendation or {}).get(
                "limitations"
            )
            or [],
            *[
                item.get("known_limitations") or []
                for item in assessments
            ],
        )
        warnings = freshness_warnings(freshness)
        _append_validity_warning(
            warnings,
            (recommendation or {}).get(
                "valid_until_utc"
            ),
            "The deterministic recommendation",
        )
        _append_validity_warning(
            warnings,
            (risk or {}).get("valid_until_utc"),
            "The linked risk result",
        )
        catalog = EvidenceCatalog()
        subject_id = (
            (recommendation or {}).get(
                "recommendation_id"
            )
            or recommendation_id
        )
        self._add_record_evidence(
            catalog,
            "Recommendation",
            subject_id,
            recommendation,
        )
        self._add_record_evidence(
            catalog,
            "RiskResult",
            (risk or {}).get("risk_id"),
            risk,
            (risk or {}).get("generated_at_utc"),
        )
        for assessment in assessments:
            self._add_record_evidence(
                catalog,
                "AirportAssessment",
                assessment.get(
                    "airport_assessment_id"
                ),
                assessment,
            )
        self._add_freshness_evidence(
            catalog,
            freshness,
        )
        context = self._base(
            "RECOMMENDATION",
            str(subject_id),
        )
        context.update(
            {
                "recommendation": recommendation,
                "risk": risk,
                "airportAssessments": assessments,
                "freshness": freshness,
                "limitations": limitations,
                "dataFreshnessWarnings": (
                    _unique_strings(warnings)
                ),
                "evidenceCatalog": catalog.items(),
            }
        )
        return context

    def build_alert_context(
        self,
        alert_id: str,
    ) -> dict[str, Any]:
        values = _parallel(
            detail=lambda: self.client.alert(alert_id),
            freshness=self.client.freshness,
        )
        detail = values["detail"]
        freshness = values["freshness"]
        alert = _select(
            detail.get("alert"),
            self.ALERT_FIELDS,
        )
        recommendation = _select(
            detail.get("recommendation"),
            self.RECOMMENDATION_FIELDS,
        )
        risk = _select(
            detail.get("risk"),
            self.RISK_FIELDS,
        )
        encounter = _select(
            detail.get("encounter"),
            self.ENCOUNTER_FIELDS,
        )
        limitations = _unique_strings(
            (risk or {}).get("limitations") or [],
            (recommendation or {}).get(
                "limitations"
            )
            or [],
        )
        warnings = freshness_warnings(freshness)
        _append_validity_warning(
            warnings,
            (alert or {}).get("valid_until_utc"),
            "The deterministic alert",
        )
        _append_validity_warning(
            warnings,
            (recommendation or {}).get(
                "valid_until_utc"
            ),
            "The linked recommendation",
        )
        _append_validity_warning(
            warnings,
            (risk or {}).get("valid_until_utc"),
            "The linked risk result",
        )
        _append_validity_warning(
            warnings,
            (encounter or {}).get("valid_to_utc"),
            "The linked encounter",
        )
        catalog = EvidenceCatalog()
        subject_id = (
            (alert or {}).get("alert_id")
            or alert_id
        )
        self._add_record_evidence(
            catalog,
            "ActiveAlert",
            subject_id,
            alert,
            (alert or {}).get("updated_at_utc"),
        )
        self._add_record_evidence(
            catalog,
            "Recommendation",
            (recommendation or {}).get(
                "recommendation_id"
            ),
            recommendation,
        )
        self._add_record_evidence(
            catalog,
            "RiskResult",
            (risk or {}).get("risk_id"),
            risk,
            (risk or {}).get("generated_at_utc"),
        )
        self._add_record_evidence(
            catalog,
            "AircraftHazardEncounter",
            (encounter or {}).get("encounter_id"),
            encounter,
            (encounter or {}).get("detected_at_utc"),
        )
        self._add_freshness_evidence(
            catalog,
            freshness,
        )
        context = self._base(
            "ALERT",
            str(subject_id),
        )
        context.update(
            {
                "alert": alert,
                "recommendation": recommendation,
                "risk": risk,
                "encounter": encounter,
                "freshness": freshness,
                "limitations": limitations,
                "dataFreshnessWarnings": (
                    _unique_strings(warnings)
                ),
                "evidenceCatalog": catalog.items(),
            }
        )
        return context
