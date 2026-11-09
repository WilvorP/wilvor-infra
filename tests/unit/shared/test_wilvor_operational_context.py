"""Direct tests for shared aircraft/encounter operational context loaders."""

from __future__ import annotations

import inspect
from pathlib import Path

from wilvor_operational import context
from wilvor_operational import current_set
from wilvor_operational import linking
from wilvor_operational import readers


REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_DIR = REPO_ROOT / "functions" / "shared" / "wilvor_operational"
NOW = 1_788_661_800
FUTURE = "2026-09-06T04:00:00Z"


class ScriptedTable:
    def __init__(self, *, scan_items=None, records=None):
        self.scan_items = list(scan_items or [])
        self.records = dict(records or {})
        self.calls = []

    def scan(self, **kwargs):
        self.calls.append(("scan", kwargs))
        return {"Items": list(self.scan_items)}

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
    projections=None,
    projection_records=None,
    hazards=None,
    hazard_records=None,
    encounters=None,
    encounter_records=None,
    risks=None,
    risk_records=None,
    recommendations=None,
    alerts=None,
):
    return readers.OperationalTables(
        aircraft=ScriptedTable(records={aircraft_id: aircraft} if aircraft else {}),
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


def test_missing_aircraft_root_returns_none_without_descendant_scans():
    tables = _tables()

    result = context.build_aircraft_operational_context(
        tables,
        "missing",
        now_epoch=NOW,
    )

    assert result is None
    assert tables.projections.calls == []
    assert tables.encounters.calls == []


def test_retained_expired_root_returns_context_with_aircraft_not_current():
    expired = {
        "aircraft_id": "abc123",
        "expires_at_epoch": NOW,
        "callsign": "UAL1",
    }
    tables = _tables(aircraft=expired)

    result = context.build_aircraft_operational_context(
        tables,
        "ABC123",
        now_epoch=NOW,
    )

    assert isinstance(result, linking.AircraftOperationalContext)
    assert result.aircraft is expired
    assert result.aircraft_is_current is False
    assert result.projection is None


def test_projection_uses_phase_1a_winner_and_hydrates_full_row():
    compact_newer = {
        "aircraft_id": "abc123",
        "projection_id": "proj-newer",
        "generated_at_epoch": NOW + 5,
        "valid_until_epoch": NOW + 1000,
        "projection_status": "READY",
    }
    compact_older = {
        "aircraft_id": "abc123",
        "projection_id": "proj-older",
        "generated_at_epoch": NOW,
        "valid_until_epoch": NOW + 2000,
        "projection_status": "READY",
    }
    full_newer = {
        **compact_newer,
        "point_count": 42,
        "aircraft_state_version": "abc123#9",
    }
    tables = _tables(
        aircraft={
            "aircraft_id": "abc123",
            "expires_at_epoch": NOW + 100,
        },
        projections=[compact_older, compact_newer],
        projection_records={"proj-newer": full_newer},
    )

    result = context.build_aircraft_operational_context(
        tables,
        "abc123",
        now_epoch=NOW,
    )

    assert result.projection is full_newer
    assert result.projection["point_count"] == 42
    assert result.projection_is_current is True
    assert "point_count" not in compact_newer or result.projection is not compact_newer


def test_wrong_aircraft_and_stale_hazard_encounters_are_excluded():
    projection = _current_projection()
    hazard = _current_hazard()
    tables = _tables(
        aircraft={"aircraft_id": "abc123", "expires_at_epoch": NOW + 100},
        projections=[projection],
        projection_records={"proj-1": projection},
        hazards=[hazard],
        hazard_records={"hazard-1": hazard},
        encounters=[
            _current_encounter(aircraft_id="other"),
            _current_encounter(
                encounter_id="proj-1#hazard-1#v-old",
                hazard_source_version="v-old",
            ),
            _current_encounter(),
        ],
        encounter_records={
            "proj-1#hazard-1#v1": _current_encounter(),
        },
    )

    result = context.build_aircraft_operational_context(
        tables,
        "abc123",
        now_epoch=NOW,
    )

    assert len(result.encounters) == 1
    assert result.encounters[0].encounter["encounter_id"] == "proj-1#hazard-1#v1"


def test_hazard_version_race_does_not_substitute_v2():
    projection = _current_projection()
    selected_hazard = _current_hazard(source_version="v1")
    live_hazard = _current_hazard(source_version="v2")
    encounter = _current_encounter()
    tables = _tables(
        aircraft={"aircraft_id": "abc123", "expires_at_epoch": NOW + 100},
        projections=[projection],
        projection_records={"proj-1": projection},
        hazards=[selected_hazard],
        hazard_records={"hazard-1": live_hazard},
        encounters=[encounter],
        encounter_records={"proj-1#hazard-1#v1": encounter},
    )

    result = context.build_aircraft_operational_context(
        tables,
        "abc123",
        now_epoch=NOW,
    )

    assert len(result.encounters) == 1
    item = result.encounters[0]
    assert item.hazard is None
    assert item.hazard_link.state is linking.LinkState.HYDRATION_VERSION_MISMATCH
    assert ("source_version", "v1") in item.hazard_link.selected_identity
    assert ("source_version", "v2") in item.hazard_link.observed_identity
    assert item.encounter["hazard_source_version"] == "v1"


def test_hydrated_risk_with_other_encounter_is_not_used():
    projection = _current_projection()
    hazard = _current_hazard()
    encounter = _current_encounter()
    selected_risk = {
        "risk_id": "risk-1",
        "encounter_id": "proj-1#hazard-1#v1",
        "generated_at_epoch": NOW,
    }
    hydrated_risk = {
        "risk_id": "risk-1",
        "encounter_id": "other-encounter",
        "generated_at_epoch": NOW,
    }
    tables = _tables(
        aircraft={"aircraft_id": "abc123", "expires_at_epoch": NOW + 100},
        projections=[projection],
        projection_records={"proj-1": projection},
        hazards=[hazard],
        hazard_records={"hazard-1": hazard},
        encounters=[encounter],
        encounter_records={"proj-1#hazard-1#v1": encounter},
        risks=[selected_risk],
        risk_records={"risk-1": hydrated_risk},
    )

    result = context.build_aircraft_operational_context(
        tables,
        "abc123",
        now_epoch=NOW,
    )

    item = result.encounters[0]
    assert item.risk is None
    assert item.risk_link.state is linking.LinkState.HYDRATION_IDENTITY_MISMATCH


def test_selected_identity_with_missing_exact_row_is_hydration_missing():
    projection = _current_projection()
    hazard = _current_hazard()
    encounter = _current_encounter()
    risk = {
        "risk_id": "risk-1",
        "encounter_id": "proj-1#hazard-1#v1",
        "generated_at_epoch": NOW,
    }
    tables = _tables(
        aircraft={"aircraft_id": "abc123", "expires_at_epoch": NOW + 100},
        projections=[projection],
        projection_records={"proj-1": projection},
        hazards=[hazard],
        hazard_records={"hazard-1": hazard},
        encounters=[encounter],
        encounter_records={},
        risks=[risk],
        risk_records={},
    )

    result = context.build_aircraft_operational_context(
        tables,
        "abc123",
        now_epoch=NOW,
    )

    item = result.encounters[0]
    assert item.encounter is None
    assert item.encounter_link.state is linking.LinkState.HYDRATION_MISSING
    assert item.risk is None
    assert item.risk_link.state is linking.LinkState.HYDRATION_MISSING


def test_multiple_recommendations_and_or_alerts_are_preserved():
    projection = _current_projection()
    hazard = _current_hazard()
    encounter = _current_encounter()
    risk = {
        "risk_id": "risk-1",
        "encounter_id": "proj-1#hazard-1#v1",
        "generated_at_epoch": NOW,
        "risk_level": "HIGH",
    }
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
    tables = _tables(
        aircraft={"aircraft_id": "abc123", "expires_at_epoch": NOW + 100},
        projections=[projection],
        projection_records={"proj-1": projection},
        hazards=[hazard],
        hazard_records={"hazard-1": hazard},
        encounters=[encounter],
        encounter_records={"proj-1#hazard-1#v1": encounter},
        risks=[risk],
        risk_records={"risk-1": risk},
        recommendations=recs,
        alerts=alerts,
    )

    result = context.build_aircraft_operational_context(
        tables,
        "abc123",
        now_epoch=NOW,
    )

    item = result.encounters[0]
    assert [rec["recommendation_id"] for rec in item.recommendations] == [
        "rec-1",
        "rec-2",
    ]
    assert [alert["alert_id"] for alert in item.alerts] == [
        "alert-risk",
        "alert-rec",
    ]


def test_retrieval_observations_are_honest_and_use_one_now_epoch():
    captured = []
    original = current_set.is_current_aircraft

    def wrapped(item, now_epoch):
        captured.append(now_epoch)
        return original(item, now_epoch)

    tables = _tables(
        aircraft={"aircraft_id": "abc123", "expires_at_epoch": NOW + 100},
    )

    current_set.is_current_aircraft = wrapped
    try:
        result = context.build_aircraft_operational_context(
            tables,
            "abc123",
            now_epoch=NOW,
        )
    finally:
        current_set.is_current_aircraft = original

    assert captured == [NOW]
    assert all(
        not hasattr(item, "complete_for_current_membership")
        for item in result.retrieval
    )
    scan_sources = {
        item.source: item
        for item in result.retrieval
        if item.coverage is linking.Coverage.FULL_SCAN
    }
    assert scan_sources["scan_encounter_candidates"].consistency is (
        linking.Consistency.EVENTUAL
    )
    assert linking.EVENTUAL_SCAN_LIMITATION in (
        scan_sources["scan_encounter_candidates"].limitations
    )


def test_context_has_no_module_cache_or_forbidden_imports():
    source = inspect.getsource(context)
    text = (PACKAGE_DIR / "context.py").read_text(encoding="utf-8")

    assert not hasattr(context, "_CACHE")
    assert "time.time" not in source
    assert "datetime.now" not in source
    assert "datetime.utcnow" not in source
    assert "operational_api" not in text
    assert "wilvor_ai" not in text
    assert "os.environ" not in source
    assert "boto3.resource" not in source
    assert "boto3.client" not in source
