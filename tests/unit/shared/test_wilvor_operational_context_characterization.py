"""Freeze Phase 1C composition semantics before the 1D.3 public extraction."""

from __future__ import annotations

from wilvor_operational import context
from wilvor_operational import linking
from wilvor_operational import readers


NOW = 1_788_661_800
FUTURE = "2026-09-06T04:00:00Z"


class ScriptedTable:
    def __init__(
        self,
        *,
        scan_items=None,
        records=None,
        query_pages=None,
    ):
        self.scan_items = list(scan_items or [])
        self.records = dict(records or {})
        self.query_pages = list(query_pages) if query_pages is not None else None
        self._query_index = 0
        self.calls = []

    def scan(self, **kwargs):
        self.calls.append(("scan", kwargs))
        return {"Items": list(self.scan_items)}

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        pages = self.query_pages
        if pages is None:
            return {"Items": []}
        if self._query_index < len(pages):
            page = pages[self._query_index]
            self._query_index += 1
            return page
        return {"Items": []}

    def get_item(self, **kwargs):
        self.calls.append(("get_item", kwargs))
        key = kwargs["Key"]
        value = next(iter(key.values()))
        item = self.records.get(value)
        if item is None:
            return {}
        return {"Item": item}


def _tables(
    *,
    aircraft=None,
    aircraft_id="abc123",
    aircraft_records=None,
    projections=None,
    projection_records=None,
    hazards=None,
    hazard_records=None,
    encounters=None,
    encounter_records=None,
    encounter_query_pages=None,
    risks=None,
    risk_records=None,
    recommendations=None,
    alerts=None,
):
    if aircraft_records is None and aircraft is not None:
        aircraft_records = {aircraft_id: aircraft}
    return readers.OperationalTables(
        aircraft=ScriptedTable(records=aircraft_records or {}),
        projections=ScriptedTable(
            scan_items=projections or [],
            records=projection_records or {},
        ),
        projection_points=ScriptedTable(),
        hazards=ScriptedTable(
            scan_items=hazards or [],
            records=hazard_records or {},
        ),
        hazard_coordinates=ScriptedTable(),
        encounters=ScriptedTable(
            scan_items=encounters or [],
            records=encounter_records or {},
            query_pages=encounter_query_pages,
        ),
        risks=ScriptedTable(
            scan_items=risks or [],
            records=risk_records or {},
        ),
        airports=ScriptedTable(),
        metar=ScriptedTable(),
        taf=ScriptedTable(),
        taf_periods=ScriptedTable(),
        airport_assessments=ScriptedTable(),
        recommendations=ScriptedTable(scan_items=recommendations or []),
        alerts=ScriptedTable(scan_items=alerts or []),
    )


def _current_projection(projection_id="proj-1", generated_at_epoch=NOW):
    return {
        "aircraft_id": "abc123",
        "projection_id": projection_id,
        "generated_at_epoch": generated_at_epoch,
        "valid_until_epoch": NOW + 1000,
        "projection_status": "READY",
        "point_count": 9,
        "aircraft_state_version": "abc123#1",
    }


def _current_hazard(hazard_id="hazard-1", source_version="v1"):
    return {
        "hazard_id": hazard_id,
        "source_version": source_version,
        "status": "ACTIVE",
        "materialization_status": "READY",
        "valid_to_epoch": NOW + 1000,
    }


def _current_encounter(
    encounter_id="proj-1#hazard-1#v1",
    aircraft_id="abc123",
    projection_id="proj-1",
    hazard_id="hazard-1",
    hazard_source_version="v1",
):
    return {
        "encounter_id": encounter_id,
        "aircraft_id": aircraft_id,
        "projection_id": projection_id,
        "hazard_id": hazard_id,
        "hazard_source_version": hazard_source_version,
        "encounter_state": "DETECTED",
        "matched_h3_cells": ["cell-1"],
    }


def _current_aircraft(aircraft_id="abc123"):
    return {
        "aircraft_id": aircraft_id,
        "expires_at_epoch": NOW + 100,
        "callsign": "UAL1",
    }


def _risk(*, risk_id="risk-1", generated_at_epoch=NOW, valid_until_utc=None):
    item = {
        "risk_id": risk_id,
        "encounter_id": "proj-1#hazard-1#v1",
        "generated_at_epoch": generated_at_epoch,
        "risk_level": "HIGH",
    }
    if valid_until_utc is not None:
        item["valid_until_utc"] = valid_until_utc
    return item


def _recs_and_alerts():
    recs = [
        {
            "recommendation_id": "rec-1",
            "risk_id": "risk-1",
            "recommendation_status": "ACTIVE",
            "valid_until_utc": FUTURE,
        },
        {
            "recommendation_id": "rec-2",
            "risk_id": "risk-1",
            "recommendation_status": "ACTIVE",
            "valid_until_utc": FUTURE,
        },
    ]
    alerts = [
        {
            "alert_id": "alert-risk",
            "risk_id": "risk-1",
            "alert_state": "NEW",
            "valid_until_utc": FUTURE,
        },
        {
            "alert_id": "alert-rec",
            "recommendation_id": "rec-2",
            "alert_state": "UPDATED",
            "valid_until_utc": FUTURE,
        },
    ]
    return recs, alerts


def _full_chain_tables(*, include_risk=True, first_observed_tie=False):
    aircraft = _current_aircraft()
    projection = _current_projection()
    hazard = _current_hazard()
    encounter = _current_encounter()
    recs, alerts = _recs_and_alerts()
    if first_observed_tie:
        first = _risk(risk_id="risk-first")
        second = _risk(risk_id="risk-second")
        risks = [first, second]
        risk_records = {"risk-first": first, "risk-second": second}
    elif include_risk:
        risk = _risk()
        risks = [risk]
        risk_records = {"risk-1": risk}
    else:
        risks = []
        risk_records = {}
    return _tables(
        aircraft=aircraft,
        projections=[projection],
        projection_records={"proj-1": projection},
        hazards=[hazard],
        hazard_records={"hazard-1": hazard},
        encounters=[encounter],
        encounter_records={"proj-1#hazard-1#v1": encounter},
        encounter_query_pages=[{"Items": [encounter]}],
        risks=risks,
        risk_records=risk_records,
        recommendations=recs,
        alerts=alerts,
    )


def test_aircraft_context_freezes_full_decision_chain():
    tables = _full_chain_tables()

    result = context.build_aircraft_operational_context(
        tables,
        "abc123",
        now_epoch=NOW,
    )

    assert result.aircraft_is_current is True
    assert result.projection_is_current is True
    assert result.projection_link.state is linking.LinkState.PRESENT
    assert result.projection["projection_id"] == "proj-1"
    assert len(result.encounters) == 1
    item = result.encounters[0]
    assert item.encounter["encounter_id"] == "proj-1#hazard-1#v1"
    assert item.encounter_is_current is True
    assert item.encounter_link.state is linking.LinkState.PRESENT
    assert item.hazard["source_version"] == "v1"
    assert item.hazard_link.state is linking.LinkState.PRESENT
    assert item.risk["risk_id"] == "risk-1"
    assert item.risk_link.state is linking.LinkState.PRESENT
    assert [rec["recommendation_id"] for rec in item.recommendations] == [
        "rec-1",
        "rec-2",
    ]
    assert [alert["alert_id"] for alert in item.alerts] == [
        "alert-risk",
        "alert-rec",
    ]
    assert item.recommendation_link.state is linking.LinkState.PRESENT
    assert item.alert_link.state is linking.LinkState.PRESENT
    assert any(
        observation.source == "scan_encounter_candidates"
        for observation in result.retrieval
    )


def test_hazard_impact_freezes_full_decision_chain():
    tables = _full_chain_tables()

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    assert result.hazard_is_current is True
    assert result.source_version == "v1"
    assert result.hazard_version_link.state is linking.LinkState.PRESENT
    assert len(result.impacts) == 1
    impact = result.impacts[0]
    assert impact.aircraft_is_current is True
    assert impact.projection_is_current is True
    assert impact.projection_link.state is linking.LinkState.PRESENT
    item = impact.encounter
    assert item.encounter_is_current is True
    assert item.encounter_link.state is linking.LinkState.PRESENT
    assert item.hazard["source_version"] == "v1"
    assert item.hazard_link.state is linking.LinkState.PRESENT
    assert item.risk["risk_id"] == "risk-1"
    assert item.risk_link.state is linking.LinkState.PRESENT
    assert [rec["recommendation_id"] for rec in item.recommendations] == [
        "rec-1",
        "rec-2",
    ]
    assert [alert["alert_id"] for alert in item.alerts] == [
        "alert-risk",
        "alert-rec",
    ]


def test_missing_risk_and_missing_valid_until_are_preserved():
    missing = context.build_hazard_operational_context(
        _full_chain_tables(include_risk=False),
        "hazard-1",
        now_epoch=NOW,
    )
    accepted = context.build_hazard_operational_context(
        _full_chain_tables(include_risk=True),
        "hazard-1",
        now_epoch=NOW,
    )

    assert missing.impacts[0].encounter.risk is None
    assert missing.impacts[0].encounter.risk_link.state is linking.LinkState.MISSING
    assert accepted.impacts[0].encounter.risk["risk_id"] == "risk-1"
    assert "valid_until_utc" not in accepted.impacts[0].encounter.risk
    assert accepted.impacts[0].encounter.risk_link.state is linking.LinkState.PRESENT


def test_equal_risk_epoch_keeps_first_observed():
    result = context.build_hazard_operational_context(
        _full_chain_tables(first_observed_tie=True),
        "hazard-1",
        now_epoch=NOW,
    )

    assert result.impacts[0].encounter.risk["risk_id"] == "risk-first"


def test_projection_hydrate_failure_preserves_impact_relationship():
    aircraft = _current_aircraft()
    compact = _current_projection()
    mismatched = {**compact, "aircraft_id": "other"}
    encounter = _current_encounter()
    hazard = _current_hazard()
    missing_tables = _tables(
        aircraft=aircraft,
        projections=[compact],
        projection_records={},
        hazards=[hazard],
        hazard_records={"hazard-1": hazard},
        encounters=[encounter],
        encounter_records={"proj-1#hazard-1#v1": encounter},
        encounter_query_pages=[{"Items": [encounter]}],
    )
    mismatch_tables = _tables(
        aircraft=aircraft,
        projections=[compact],
        projection_records={"proj-1": mismatched},
        hazards=[hazard],
        hazard_records={"hazard-1": hazard},
        encounters=[encounter],
        encounter_records={"proj-1#hazard-1#v1": encounter},
        encounter_query_pages=[{"Items": [encounter]}],
    )

    missing = context.build_hazard_operational_context(
        missing_tables,
        "hazard-1",
        now_epoch=NOW,
    )
    mismatch = context.build_hazard_operational_context(
        mismatch_tables,
        "hazard-1",
        now_epoch=NOW,
    )

    assert len(missing.impacts) == 1
    assert missing.impacts[0].encounter.encounter is encounter
    assert missing.impacts[0].encounter.encounter_is_current is True
    assert missing.impacts[0].projection is None
    assert missing.impacts[0].projection_is_current is False
    assert missing.impacts[0].projection_link.state is (
        linking.LinkState.HYDRATION_MISSING
    )
    assert len(mismatch.impacts) == 1
    assert mismatch.impacts[0].encounter.encounter is encounter
    assert mismatch.impacts[0].projection is None
    assert mismatch.impacts[0].projection_link.state is (
        linking.LinkState.HYDRATION_IDENTITY_MISMATCH
    )
