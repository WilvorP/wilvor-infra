"""Deterministic Phase 1D.3 operational query tests."""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from wilvor_operational import current_set
from wilvor_operational import linking
from wilvor_operational import query
from wilvor_operational import readers


REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_DIR = REPO_ROOT / "functions" / "shared" / "wilvor_operational"
SHARED_DIR = PACKAGE_DIR.parent
NOW = 1_788_661_800
FUTURE = "2026-09-06T04:00:00Z"


class FlexibleTable:
    def __init__(
        self,
        *,
        records=None,
        scan_items=None,
        query_items=None,
        drift_after=None,
    ):
        self.records = dict(records or {})
        self.scan_items = list(scan_items or [])
        self.query_items = dict(query_items or {})
        self.drift_after = dict(drift_after or {})
        self.get_counts = {}
        self.calls = []

    def get_item(self, **kwargs):
        self.calls.append(("get_item", kwargs))
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


def _tables(**overrides):
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
        metar=empty,
        taf=empty,
        taf_periods=empty,
        airport_assessments=empty,
        recommendations=overrides.get("recommendations", empty),
        alerts=overrides.get("alerts", empty),
    )


def _hazard(
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
):
    return {
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
    }


def _square_rows(hazard, west, south, east, north):
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
                "hazard_version_key": f"{hazard['hazard_id']}#{hazard['source_version']}",
                "hazard_id": hazard["hazard_id"],
                "source_version": hazard["source_version"],
                "geometry_type": hazard["geometry_type"],
                "geometry_hash": hazard["geometry_hash"],
                "materialization_id": hazard["materialization_id"],
                "polygon_index": 0,
                "ring_index": 0,
                "sequence_number": sequence,
                "longitude": Decimal(str(longitude)),
                "latitude": Decimal(str(latitude)),
            }
        )
    return rows


def _projection(aircraft_id, projection_id):
    return {
        "aircraft_id": aircraft_id,
        "projection_id": projection_id,
        "generated_at_epoch": NOW,
        "valid_until_epoch": NOW + 1000,
        "projection_status": "READY",
    }


def _encounter(
    encounter_id,
    aircraft_id,
    projection_id,
    hazard_id,
    version="v1",
):
    return {
        "encounter_id": encounter_id,
        "aircraft_id": aircraft_id,
        "projection_id": projection_id,
        "hazard_id": hazard_id,
        "hazard_source_version": version,
        "encounter_state": "DETECTED",
    }


def _california_tables():
    h1 = _hazard("H1")
    h2 = _hazard("H2")
    outside = _hazard("outside")
    uneval = _hazard("uneval")
    stale_gsi = _hazard("stale")
    stale_exact = _hazard("stale", valid_to_epoch=NOW - 60)
    airmet = _hazard("airmet", product_type="AIRMET")
    h3_v1 = _hazard("H3", source_version="v1")
    h3_v2 = _hazard("H3", source_version="v2", geometry_hash="hash-2")
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
            "H1#v1": _square_rows(h1, -120.2, 36.8, -119.8, 37.2),
            "H2#v1": _square_rows(h2, -120.1, 36.9, -119.7, 37.3),
            "outside#v1": _square_rows(outside, -74.2, 40.6, -73.8, 41.0),
            "uneval#v1": [],
            "H3#v1": _square_rows(h3_v1, -120.0, 36.7, -119.6, 37.1),
        }
    )
    enc_h1a = _encounter("H1-A", "a", "proj-a", "H1")
    enc_h1b = _encounter("H1-B", "b", "proj-b", "H1")
    enc_h2a = _encounter("H2-A", "a", "proj-a", "H2")
    enc_h2c = _encounter("H2-C", "c", "proj-c", "H2")
    stale_proj = _encounter("stale-proj", "a", "proj-old", "H1")
    old_version = _encounter("old-ver", "a", "proj-a", "H1", version="v-old")
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
            "H3": [_encounter("H3-A", "a", "proj-a", "H3")],
        },
    )
    risk = {
        "risk_id": "risk-1",
        "encounter_id": "H1-A",
        "generated_at_epoch": NOW,
    }
    risk_second = {
        "risk_id": "risk-second",
        "encounter_id": "H1-A",
        "generated_at_epoch": NOW,
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
    return _tables(
        hazards=hazards,
        coordinates=coordinates,
        encounters=encounters,
        projections=FlexibleTable(
            scan_items=[
                _projection("a", "proj-a"),
                _projection("b", "proj-b"),
                _projection("c", "proj-c"),
            ],
            records={
                "proj-a": _projection("a", "proj-a"),
                "proj-b": _projection("b", "proj-b"),
                "proj-c": _projection("c", "proj-c"),
            },
        ),
        aircraft=FlexibleTable(
            records={
                "a": {"aircraft_id": "a", "expires_at_epoch": NOW + 100},
                "b": {"aircraft_id": "b", "expires_at_epoch": NOW},
                "c": {"aircraft_id": "c", "expires_at_epoch": NOW + 100},
            }
        ),
        risks=FlexibleTable(
            scan_items=[risk, risk_second],
            records={"risk-1": risk, "risk-second": risk_second},
        ),
        recommendations=FlexibleTable(scan_items=recs),
        alerts=FlexibleTable(scan_items=alerts),
    ), encounters


def test_california_golden_current_sigmet_impacts():
    tables, encounters = _california_tables()
    result = query.search_current_impacts(
        tables,
        now_epoch=NOW,
        region="California",
        product_type="SIGMET",
    )
    keys = [
        (item.hazard_id, item.aircraft_id, item.encounter_id)
        for item in result.impacts
    ]
    assert keys == [
        ("H1", "a", "H1-A"),
        ("H1", "b", "H1-B"),
        ("H2", "a", "H2-A"),
    ]
    assert result.unique_aircraft_ids == ("a", "b")
    reasons = {
        (item.hazard_id, item.encounter_id, item.reason)
        for item in result.operationally_unevaluated
    }
    assert ("H2", "H2-C", "ENCOUNTER_HYDRATION_IDENTITY_MISMATCH") in reasons
    assert any(
        item.hazard_id == "H3"
        and item.reason == "HAZARD_PIN_VERSION_DRIFT"
        and item.encounter_id == ""
        for item in result.operationally_unevaluated
    )
    assert "c" not in result.unique_aircraft_ids
    assert any(item.hazard_id == "uneval" for item in result.geospatial_unevaluated)
    assert any(item.hazard_id == "stale" for item in result.rejected_hazard_candidates)
    assert all(item.hazard_id != "outside" for item in result.impacts)
    assert all(item.hazard_id != "airmet" for item in result.impacts)
    assert all(item.hazard_id != "H3" for item in result.impacts)
    assert not any(
        "H3" in str(call[1].get("KeyConditionExpression", ""))
        for call in encounters.calls
        if call[0] == "query"
    )
    impact_h1a = next(
        item for item in result.impacts if item.encounter_id == "H1-A"
    )
    assert impact_h1a.impact.encounter.risk["risk_id"] == "risk-1"
    assert "valid_until_utc" not in impact_h1a.impact.encounter.risk
    assert [
        rec["recommendation_id"]
        for rec in impact_h1a.impact.encounter.recommendations
    ] == ["rec-1", "rec-2"]
    assert [
        alert["alert_id"] for alert in impact_h1a.impact.encounter.alerts
    ] == ["alert-risk", "alert-rec"]
    impact_h2a = next(
        item for item in result.impacts if item.encounter_id == "H2-A"
    )
    assert impact_h2a.impact.encounter.risk is None
    assert impact_h1a.impact.aircraft_is_current is True
    assert impact_h1a.impact.aircraft_is_current is True
    h1b = next(item for item in result.impacts if item.encounter_id == "H1-B")
    assert h1b.impact.aircraft_is_current is False
    assert linking.NO_SNAPSHOT_LIMITATION in result.limitations


def test_explicit_hazard_path_is_shapely_free_and_preserves_cardinality():
    h1 = _hazard("H1")
    h2 = _hazard("H2")
    enc_h1a = _encounter("H1-A", "a", "proj-a", "H1")
    enc_h2a = _encounter("H2-A", "a", "proj-a", "H2")
    tables = _tables(
        hazards=FlexibleTable(records={"H1": h1, "H2": h2}),
        encounters=FlexibleTable(
            records={"H1-A": enc_h1a, "H2-A": enc_h2a},
            query_items={"H1": [enc_h1a], "H2": [enc_h2a]},
        ),
        projections=FlexibleTable(
            scan_items=[_projection("a", "proj-a")],
            records={"proj-a": _projection("a", "proj-a")},
        ),
        aircraft=FlexibleTable(
            records={"a": {"aircraft_id": "a", "expires_at_epoch": NOW + 100}}
        ),
        risks=FlexibleTable(scan_items=[]),
        recommendations=FlexibleTable(scan_items=[]),
        alerts=FlexibleTable(scan_items=[]),
    )
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    script = f"""
import sys
from wilvor_operational import query
from wilvor_operational import readers

class T:
    def __init__(self, records=None, scan_items=None, query_items=None):
        self.records = dict(records or {{}})
        self.scan_items = list(scan_items or [])
        self.query_items = dict(query_items or {{}})
        self.calls = []
    def get_item(self, **kwargs):
        value = next(iter(kwargs['Key'].values()))
        item = self.records.get(value)
        return {{'Item': item}} if item is not None else {{}}
    def scan(self, **kwargs):
        return {{'Items': list(self.scan_items)}}
    def query(self, **kwargs):
        values = []
        expr = kwargs.get('KeyConditionExpression')
        def walk(item):
            if item is None:
                return
            if hasattr(item, 'get_expression'):
                for value in item.get_expression().get('values', ()):
                    walk(value)
            elif not hasattr(item, 'name'):
                values.append(item)
        walk(expr)
        for key, items in self.query_items.items():
            if key in values:
                return {{'Items': list(items)}}
        return {{'Items': []}}

now = {NOW}
h1 = {h1!r}
h2 = {h2!r}
enc_h1a = {enc_h1a!r}
enc_h2a = {enc_h2a!r}
proj = {{
    'aircraft_id': 'a',
    'projection_id': 'proj-a',
    'generated_at_epoch': now,
    'valid_until_epoch': now + 1000,
    'projection_status': 'READY',
}}
tables = readers.OperationalTables(
    aircraft=T(records={{'a': {{'aircraft_id': 'a', 'expires_at_epoch': now + 100}}}}),
    projections=T(scan_items=[proj], records={{'proj-a': proj}}),
    projection_points=T(),
    hazards=T(records={{'H1': h1, 'H2': h2}}),
    hazard_coordinates=T(),
    encounters=T(records={{'H1-A': enc_h1a, 'H2-A': enc_h2a}}, query_items={{'H1': [enc_h1a], 'H2': [enc_h2a]}}),
    risks=T(),
    airports=T(),
    metar=T(),
    taf=T(),
    taf_periods=T(),
    airport_assessments=T(),
    recommendations=T(),
    alerts=T(),
)
result = query.search_current_impacts(tables, now_epoch=now, hazard_ids=('H1', 'H2'))
assert [item.encounter_id for item in result.impacts] == ['H1-A', 'H2-A']
assert not any(module == 'shapely' or module.startswith('shapely.') for module in sys.modules)
assert 'wilvor_operational.geospatial' not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_explicit_hazard_composition_drift_is_not_repinned():
    h1_v1 = _hazard("H1", source_version="v1")
    h1_v2 = _hazard("H1", source_version="v2")
    enc = _encounter("H1-A", "a", "proj-a", "H1")
    hazards = FlexibleTable(
        records={"H1": h1_v1},
        drift_after={"H1": (1, h1_v2)},
    )
    tables = _tables(
        hazards=hazards,
        encounters=FlexibleTable(
            records={"H1-A": enc},
            query_items={"H1": [enc]},
        ),
        projections=FlexibleTable(
            scan_items=[_projection("a", "proj-a")],
            records={"proj-a": _projection("a", "proj-a")},
        ),
        aircraft=FlexibleTable(
            records={"a": {"aircraft_id": "a", "expires_at_epoch": NOW + 100}}
        ),
        risks=FlexibleTable(scan_items=[]),
        recommendations=FlexibleTable(scan_items=[]),
        alerts=FlexibleTable(scan_items=[]),
    )
    result = query.search_current_impacts(
        tables,
        now_epoch=NOW,
        hazard_ids=("H1",),
    )
    assert result.impacts == ()
    assert result.operationally_unevaluated[0].reason == (
        "HAZARD_HYDRATION_VERSION_MISMATCH"
    )
    assert result.operationally_unevaluated[0].selected_source_version == "v1"
    assert hazards.get_counts["H1"] == 2


def test_callsign_union_and_hazard_authority_not_self_authorized():
    aircraft_a = {
        "aircraft_id": "aa11aa",
        "callsign": "UAL123",
        "expires_at_epoch": NOW + 100,
    }
    aircraft_b = {
        "aircraft_id": "bb22bb",
        "callsign": "UAL123",
        "expires_at_epoch": NOW + 100,
    }
    h1 = _hazard("H1", source_version="v2")
    enc_a = _encounter("E-A", "aa11aa", "proj-a", "H1", version="v2")
    enc_b_old = _encounter("E-B", "bb22bb", "proj-b", "H1", version="v1")
    tables = _tables(
        aircraft=FlexibleTable(
            records={"aa11aa": aircraft_a, "bb22bb": aircraft_b},
            query_items={"UAL123": [aircraft_a, aircraft_b]},
        ),
        hazards=FlexibleTable(records={"H1": h1}),
        encounters=FlexibleTable(
            records={"E-A": enc_a, "E-B": enc_b_old},
            query_items={
                "aa11aa": [enc_a],
                "bb22bb": [enc_b_old],
            },
        ),
        projections=FlexibleTable(
            scan_items=[
                _projection("aa11aa", "proj-a"),
                _projection("bb22bb", "proj-b"),
            ],
            records={
                "proj-a": _projection("aa11aa", "proj-a"),
                "proj-b": _projection("bb22bb", "proj-b"),
            },
        ),
        risks=FlexibleTable(scan_items=[]),
        recommendations=FlexibleTable(scan_items=[]),
        alerts=FlexibleTable(scan_items=[]),
    )
    result = query.search_current_encounters(
        tables,
        now_epoch=NOW,
        callsign="ual123",
    )
    assert result.callsign_matches == ("aa11aa", "bb22bb")
    assert [item.encounter_id for item in result.encounters] == ["E-A"]
    assert all(item.encounter_id != "E-B" for item in result.encounters)
    assert discovery_case_limitation(result)
    assert not any(
        item.encounter_id == "E-B" for item in result.operationally_unevaluated
    )


def discovery_case_limitation(result):
    from wilvor_operational import discovery

    return discovery.CALLSIGN_CASE_LIMITATION in result.limitations


def test_aircraft_path_missing_hazard_is_operationally_unevaluated():
    enc = _encounter("E-1", "aa11aa", "proj-a", "H1")
    tables = _tables(
        aircraft=FlexibleTable(
            records={"aa11aa": {"aircraft_id": "aa11aa", "expires_at_epoch": NOW + 100}}
        ),
        hazards=FlexibleTable(records={}),
        encounters=FlexibleTable(
            records={"E-1": enc},
            query_items={"aa11aa": [enc]},
        ),
        projections=FlexibleTable(
            scan_items=[_projection("aa11aa", "proj-a")],
            records={"proj-a": _projection("aa11aa", "proj-a")},
        ),
        risks=FlexibleTable(scan_items=[]),
        recommendations=FlexibleTable(scan_items=[]),
        alerts=FlexibleTable(scan_items=[]),
    )
    result = query.search_current_encounters(
        tables,
        now_epoch=NOW,
        aircraft_id="aa11aa",
    )
    assert result.encounters == ()
    assert result.operationally_unevaluated[0].reason == "HAZARD_AUTHORITY_UNESTABLISHED"
    assert result.operationally_unevaluated[0].hazard_id == "H1"


def test_aircraft_path_noncurrent_hazard_is_ordinary_exclusion():
    enc = _encounter("E-1", "aa11aa", "proj-a", "H1")
    expired = _hazard("H1", valid_to_epoch=NOW - 10)
    tables = _tables(
        aircraft=FlexibleTable(
            records={"aa11aa": {"aircraft_id": "aa11aa", "expires_at_epoch": NOW + 100}}
        ),
        hazards=FlexibleTable(records={"H1": expired}),
        encounters=FlexibleTable(
            records={"E-1": enc},
            query_items={"aa11aa": [enc]},
        ),
        projections=FlexibleTable(
            scan_items=[_projection("aa11aa", "proj-a")],
            records={"proj-a": _projection("aa11aa", "proj-a")},
        ),
        risks=FlexibleTable(scan_items=[]),
        recommendations=FlexibleTable(scan_items=[]),
        alerts=FlexibleTable(scan_items=[]),
    )
    result = query.search_current_encounters(
        tables,
        now_epoch=NOW,
        aircraft_id="aa11aa",
    )
    assert result.encounters == ()
    assert result.operationally_unevaluated == ()


def test_observed_network_state_uses_phase_1a_and_skips_airports():
    ready = _hazard("ready")
    building = _hazard("building", materialization_status="BUILDING")
    expired_aircraft = {"aircraft_id": "old", "expires_at_epoch": NOW}
    current_aircraft = {"aircraft_id": "cur", "expires_at_epoch": NOW + 10}
    proj = _projection("cur", "proj-cur")
    current_enc = _encounter("E-CUR", "cur", "proj-cur", "ready")
    stale_proj = _encounter("E-STALE", "cur", "proj-old", "ready")
    old_version = _encounter("E-OLD", "cur", "proj-cur", "ready", version="v-old")
    risk = {
        "risk_id": "risk-1",
        "encounter_id": "E-CUR",
        "generated_at_epoch": NOW,
    }
    rec = {
        "recommendation_id": "rec-1",
        "risk_id": "risk-1",
        "recommendation_status": "ACTIVE",
        "valid_until_utc": FUTURE,
    }
    alert = {
        "alert_id": "alert-1",
        "recommendation_id": "rec-1",
        "alert_state": "NEW",
        "valid_until_utc": FUTURE,
    }
    airports = FlexibleTable(scan_items=[{"airport_id": "KLAX"}])
    tables = _tables(
        aircraft=FlexibleTable(scan_items=[current_aircraft, expired_aircraft]),
        hazards=FlexibleTable(
            records={"ready": ready, "building": building},
            query_items={"ACTIVE": [ready, building]},
        ),
        encounters=FlexibleTable(
            scan_items=[current_enc, stale_proj, old_version]
        ),
        projections=FlexibleTable(scan_items=[proj]),
        risks=FlexibleTable(scan_items=[risk]),
        recommendations=FlexibleTable(scan_items=[rec]),
        alerts=FlexibleTable(scan_items=[alert]),
        airports=airports,
    )
    result = query.get_observed_network_state(tables, now_epoch=NOW)
    assert result.current_aircraft_ids == ("cur",)
    assert result.current_hazard_ids == ("ready",)
    assert result.current_encounter_ids == ("E-CUR",)
    assert result.current_risk_ids == ("risk-1",)
    assert result.current_recommendation_ids == ("rec-1",)
    assert result.current_alert_ids == ("alert-1",)
    assert airports.calls == []
    assert linking.NO_SNAPSHOT_LIMITATION in result.limitations
    assert not hasattr(result, "complete")
    assert current_set.is_current_hazard(ready, NOW) is True
    assert current_set.is_current_hazard(building, NOW) is False


def test_query_import_is_shapely_free():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    script = """
import sys
modules = [
    'wilvor_operational',
    'wilvor_operational.current_set',
    'wilvor_operational.access',
    'wilvor_operational.readers',
    'wilvor_operational.linking',
    'wilvor_operational.context',
    'wilvor_operational.discovery',
    'wilvor_operational.regions',
    'wilvor_operational.observed',
    'wilvor_operational.query',
]
for name in modules:
    __import__(name)
assert not any(
    module == 'shapely' or module.startswith('shapely.')
    for module in sys.modules
)
assert 'wilvor_operational.geospatial' not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_region_path_may_load_geospatial():
    tables, _ = _california_tables()
    query.search_current_impacts(
        tables,
        now_epoch=NOW,
        region="California",
        product_type="SIGMET",
    )
    assert "wilvor_operational.geospatial" in sys.modules


def test_query_source_has_no_clock_ai_or_toplevel_geospatial():
    source = inspect.getsource(query)
    text = (PACKAGE_DIR / "query.py").read_text(encoding="utf-8")
    tree_imports = []
    import ast

    for node in ast.parse(text).body:
        if isinstance(node, ast.ImportFrom) and node.module:
            tree_imports.append(node.module)
        if isinstance(node, ast.Import):
            tree_imports.extend(alias.name for alias in node.names)
    assert "wilvor_operational.geospatial" not in tree_imports
    assert ".geospatial" not in tree_imports
    assert "time.time" not in source
    assert "datetime.now" not in source
    assert "datetime.utcnow" not in source
    assert "wilvor_ai" not in text
    assert "search_current_risks" not in source
    assert "search_current_recommendations" not in source
    assert "search_current_alerts" not in source
