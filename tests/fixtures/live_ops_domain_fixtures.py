"""Offline DynamoDB-shaped fixtures for Phase 1E Live Ops tests."""

from __future__ import annotations

from decimal import Decimal

from wilvor_operational import readers


NOW = 1_788_661_800
FUTURE = "2026-09-06T04:00:00Z"
AS_OF_UTC = "2026-09-06T03:30:00Z"
TOOL_CALL_ID = "tool-call-live-ops-1"
CORRELATION_ID = "corr-live-ops-1"


class FlexibleTable:
    def __init__(
        self,
        *,
        records=None,
        scan_items=None,
        query_items=None,
        drift_after=None,
        get_sequence=None,
        query_pages=None,
    ):
        self.records = dict(records or {})
        self.scan_items = list(scan_items or [])
        self.query_items = dict(query_items or {})
        self.drift_after = dict(drift_after or {})
        self.get_sequence = list(get_sequence) if get_sequence is not None else None
        self.query_pages = list(query_pages) if query_pages is not None else None
        self.get_counts = {}
        self._get_index = 0
        self._query_index = 0
        self.calls = []

    def get_item(self, **kwargs):
        self.calls.append(("get_item", kwargs))
        if self.get_sequence is not None:
            if self._get_index < len(self.get_sequence):
                item = self.get_sequence[self._get_index]
                self._get_index += 1
            else:
                item = self.get_sequence[-1] if self.get_sequence else None
            if item is None:
                return {}
            return {"Item": item}
        value = next(iter(kwargs["Key"].values()))
        self.get_counts[value] = self.get_counts.get(value, 0) + 1
        if value in self.drift_after:
            after, replacement = self.drift_after[value]
            if self.get_counts[value] > after:
                if replacement is None:
                    return {}
                return {"Item": replacement}
        item = self.records.get(value)
        if item is None:
            return {}
        return {"Item": item}

    def scan(self, **kwargs):
        self.calls.append(("scan", kwargs))
        return {"Items": list(self.scan_items)}

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        if self.query_pages is not None:
            if "ExclusiveStartKey" not in kwargs:
                self._query_index = 0
            if self._query_index < len(self.query_pages):
                page = self.query_pages[self._query_index]
                self._query_index += 1
                return page
            return {"Items": []}
        values = _condition_values(kwargs.get("KeyConditionExpression"))
        for key in sorted(self.query_items, key=len, reverse=True):
            if key in values:
                return {"Items": list(self.query_items[key])}
        return {"Items": []}


def _condition_values(expr):
    values = []
    if expr is None:
        return values
    if hasattr(expr, "get_expression"):
        for item in expr.get_expression().get("values", ()):
            if hasattr(item, "get_expression"):
                values.extend(_condition_values(item))
            elif not hasattr(item, "name"):
                values.append(item)
    return values


def tables(**overrides):
    empty = FlexibleTable()
    return readers.OperationalTables(
        aircraft=overrides.get("aircraft", empty),
        projections=overrides.get("projections", empty),
        projection_points=empty,
        hazards=overrides.get("hazards", empty),
        hazard_coordinates=overrides.get("coordinates", empty),
        encounters=overrides.get("encounters", empty),
        risks=overrides.get("risks", empty),
        airports=overrides.get("airports", empty),
        metar=overrides.get("metar", empty),
        taf=overrides.get("taf", empty),
        taf_periods=overrides.get("taf_periods", empty),
        airport_assessments=empty,
        recommendations=overrides.get("recommendations", empty),
        alerts=overrides.get("alerts", empty),
    )


def hazard(
    hazard_id,
    *,
    source_version="v1",
    product_type="SIGMET",
    hazard_type="CONVECTION",
    valid_to_epoch=NOW + 60,
    status="ACTIVE",
    materialization_status="READY",
    geometry_hash="hash-1",
    materialization_id="mat-1",
    **extra,
):
    item = {
        "hazard_id": hazard_id,
        "source_version": source_version,
        "product_type": product_type,
        "hazard_type": hazard_type,
        "status": status,
        "materialization_status": materialization_status,
        "valid_to_epoch": valid_to_epoch,
        "geometry_type": "POLYGON",
        "geometry_hash": geometry_hash,
        "materialization_id": materialization_id,
        "severity": "SEVERE",
        "source_system": "NWS",
    }
    item.update(extra)
    return item


def square_rows(item, west, south, east, north):
    points = (
        (west, south),
        (east, south),
        (east, north),
        (west, north),
        (west, south),
    )
    rows = []
    for sequence, (longitude, latitude) in enumerate(points):
        rows.append(
            {
                "hazard_version_key": f"{item['hazard_id']}#{item['source_version']}",
                "hazard_id": item["hazard_id"],
                "source_version": item["source_version"],
                "geometry_type": item["geometry_type"],
                "geometry_hash": item["geometry_hash"],
                "materialization_id": item["materialization_id"],
                "polygon_index": 0,
                "ring_index": 0,
                "sequence_number": sequence,
                "longitude": Decimal(str(longitude)),
                "latitude": Decimal(str(latitude)),
            }
        )
    return rows


def projection(aircraft_id, projection_id):
    return {
        "aircraft_id": aircraft_id,
        "projection_id": projection_id,
        "generated_at_epoch": NOW,
        "generated_at_utc": AS_OF_UTC,
        "valid_until_epoch": NOW + 1000,
        "projection_status": "READY",
        "trigger_hazard_ids": ["H1"],
        "confidence": "UNKNOWN",
        "freshness_status": "UNKNOWN",
    }


def encounter(encounter_id, aircraft_id, projection_id, hazard_id, version="v1"):
    return {
        "encounter_id": encounter_id,
        "aircraft_id": aircraft_id,
        "projection_id": projection_id,
        "hazard_id": hazard_id,
        "hazard_source_version": version,
        "encounter_state": "DETECTED",
        "detected_at_utc": AS_OF_UTC,
        "freshness_status": "UNKNOWN",
    }


def aircraft(aircraft_id, *, callsign="UAL123", current=True, **extra):
    item = {
        "aircraft_id": aircraft_id,
        "callsign": callsign,
        "expires_at_epoch": NOW + 100 if current else NOW,
        "state_version": "sv-1",
        "position_time_utc": AS_OF_UTC,
        "freshness_status": "UNKNOWN",
        "on_ground": False,
        "has_position": True,
    }
    item.update(extra)
    return item


def california_tables():
    h1 = hazard("H1")
    h2 = hazard("H2")
    outside = hazard("outside")
    uneval = hazard("uneval")
    stale_gsi = hazard("stale")
    stale_exact = hazard("stale", valid_to_epoch=NOW - 60)
    airmet = hazard("airmet", product_type="AIRMET")
    h3_v1 = hazard("H3", source_version="v1")
    h3_v2 = hazard("H3", source_version="v2", geometry_hash="hash-2")
    hazards = FlexibleTable(
        records={
            "H1": h1,
            "H2": h2,
            "outside": outside,
            "uneval": uneval,
            "stale": stale_exact,
            "airmet": airmet,
            "H3": h3_v1,
        },
        query_items={
            "ACTIVE": [h1, h2, outside, uneval, stale_gsi, airmet, h3_v1],
        },
        drift_after={"H3": (2, h3_v2)},
    )
    coordinates = FlexibleTable(
        query_items={
            "H1#v1": square_rows(h1, -120.2, 36.8, -119.8, 37.2),
            "H2#v1": square_rows(h2, -120.1, 36.9, -119.7, 37.3),
            "outside#v1": square_rows(outside, -74.2, 40.6, -73.8, 41.0),
            "uneval#v1": [],
            "H3#v1": square_rows(h3_v1, -120.0, 36.7, -119.6, 37.1),
        }
    )
    enc_h1a = encounter("H1-A", "a", "proj-a", "H1")
    enc_h1b = encounter("H1-B", "b", "proj-b", "H1")
    enc_h2a = encounter("H2-A", "a", "proj-a", "H2")
    enc_h2c = encounter("H2-C", "c", "proj-c", "H2")
    stale_proj = encounter("stale-proj", "a", "proj-old", "H1")
    old_version = encounter("old-ver", "a", "proj-a", "H1", version="v-old")
    encounters = FlexibleTable(
        records={
            "H1-A": enc_h1a,
            "H1-B": enc_h1b,
            "H2-A": enc_h2a,
            "H2-C": {**enc_h2c, "aircraft_id": "other"},
        },
        query_items={
            "H1": [enc_h1a, enc_h1b, stale_proj, old_version],
            "H2": [enc_h2a, enc_h2c],
            "H3": [encounter("H3-A", "a", "proj-a", "H3")],
        },
        scan_items=[enc_h1a, enc_h1b, enc_h2a, enc_h2c, stale_proj, old_version],
    )
    risk = {
        "risk_id": "risk-1",
        "encounter_id": "H1-A",
        "generated_at_epoch": NOW,
        "generated_at_utc": AS_OF_UTC,
        "risk_level": "HIGH",
        "risk_score": 90,
        "reasons": ["overlap"],
        "limitations": [],
        "confidence": "UNKNOWN",
        "freshness_status": "UNKNOWN",
    }
    recs = [
        {
            "recommendation_id": "rec-1",
            "recommendation_version": "rv-1",
            "risk_id": "risk-1",
            "recommendation_status": "ACTIVE",
            "valid_until_utc": FUTURE,
            "created_at_utc": AS_OF_UTC,
            "reasons": ["risk"],
            "limitations": [],
            "alternative_actions": [{"action": "DIVERT"}],
        },
        {
            "recommendation_id": "rec-2",
            "recommendation_version": "rv-1",
            "risk_id": "risk-1",
            "recommendation_status": "ACTIVE",
            "valid_until_utc": FUTURE,
            "created_at_utc": AS_OF_UTC,
            "reasons": [],
            "limitations": [],
            "alternative_actions": [],
        },
    ]
    alerts = [
        {
            "alert_id": "alert-risk",
            "fingerprint": "fp-alert-risk",
            "risk_id": "risk-1",
            "alert_state": "NEW",
            "valid_until_utc": FUTURE,
        },
        {
            "alert_id": "alert-rec",
            "fingerprint": "fp-alert-rec",
            "recommendation_id": "rec-2",
            "alert_state": "UPDATED",
            "valid_until_utc": FUTURE,
        },
    ]
    return tables(
        hazards=hazards,
        coordinates=coordinates,
        encounters=encounters,
        projections=FlexibleTable(
            scan_items=[
                projection("a", "proj-a"),
                projection("b", "proj-b"),
                projection("c", "proj-c"),
            ],
            records={
                "proj-a": projection("a", "proj-a"),
                "proj-b": projection("b", "proj-b"),
                "proj-c": projection("c", "proj-c"),
            },
        ),
        aircraft=FlexibleTable(
            records={
                "a": aircraft("a"),
                "b": aircraft("b", current=False),
                "c": aircraft("c"),
            },
            query_items={"UAL123": [aircraft("a"), aircraft("c")]},
            scan_items=[aircraft("a"), aircraft("b", current=False), aircraft("c")],
        ),
        risks=FlexibleTable(scan_items=[risk], records={"risk-1": risk}),
        recommendations=FlexibleTable(scan_items=recs),
        alerts=FlexibleTable(scan_items=alerts),
    )


def airport_tables(
    *,
    current=True,
    fingerprint_alert=True,
    mismatch=False,
):
    status = {
        "airport_id": "KSEA",
        "station_id": "KSEA",
        "expires_at_epoch": NOW + 100 if current else NOW,
        "updated_at_epoch": NOW,
        "source_metar_version": "M1",
        "source_taf_version": "T2" if mismatch else "T1",
        "taf_version_key": "KSEA#T2" if mismatch else "KSEA#T1",
        "metar_freshness_status": "FRESH",
        "taf_freshness_status": "FRESH",
        "weather_risk_level": "LOW",
        "flight_category": "VFR",
        "has_metar": True,
        "has_taf": True,
        "weather_codes": ["RA"],
        "status_reasons": [],
        "known_limitations": [],
        "temperature_c": Decimal("12.0"),
        "on_ground": False,
    }
    metar = {
        "station_id": "KSEA",
        "airport_id": "KSEA",
        "metar_version": "M1",
        "observed_time_utc": AS_OF_UTC,
        "weather_codes": ["RA"],
        "clouds": [{"cover": "BKN", "base_ft": 2000}],
        "freshness_status": "UNKNOWN",
        "temperature_c": Decimal("12"),
    }
    taf = {
        "station_id": "KSEA",
        "airport_id": "KSEA",
        "taf_version_key": "KSEA#T1",
        "source_version": "T1",
        "taf_version": "T1",
        "issued_at_utc": AS_OF_UTC,
        "period_materialization_status": "READY",
        "freshness_status": "UNKNOWN",
        "is_amendment": False,
    }
    period = {
        "taf_version_key": "KSEA#T1",
        "period_key": "P1",
        "period_id": "KSEA-P1",
        "station_id": "KSEA",
        "airport_id": "KSEA",
        "taf_version": "T1",
        "weather_codes": [],
        "clouds": [],
        "icing_turbulence_layers": [],
        "temperature_forecasts": [],
        "not_decoded": False,
        "wind_speed_kt": 0,
    }
    return tables(
        airports=FlexibleTable(records={"KSEA": status}),
        metar=FlexibleTable(records={"KSEA": metar}),
        taf=FlexibleTable(records={"KSEA": taf}),
        taf_periods=FlexibleTable(query_items={"KSEA#T1": [period]}),
    )
