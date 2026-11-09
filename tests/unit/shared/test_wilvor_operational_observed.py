"""Request-scoped observed operational set tests."""

from __future__ import annotations

import inspect

from boto3.dynamodb.conditions import Attr

from wilvor_operational import linking
from wilvor_operational import observed
from wilvor_operational import readers


NOW = 1_788_661_800
FUTURE = "2026-09-06T04:00:00Z"


class ScriptedTable:
    def __init__(self, *, records=None, scan_items=None, query_items=None):
        self.records = dict(records or {})
        self.scan_items = list(scan_items or [])
        self.query_items = dict(query_items or {})
        self.calls = []

    def get_item(self, **kwargs):
        self.calls.append(("get_item", kwargs))
        value = next(iter(kwargs["Key"].values()))
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


class FakePin:
    def __init__(
        self,
        hazard_id,
        source_version,
        hazard=None,
        geometry_hash=None,
        materialization_id=None,
    ):
        self.hazard_id = hazard_id
        self.source_version = source_version
        self.hazard = hazard or {
            "hazard_id": hazard_id,
            "source_version": source_version,
            "geometry_type": "POLYGON",
        }
        self.geometry_hash = geometry_hash
        self.materialization_id = materialization_id


def _tables(*, hazards=None, encounters=None, projections=None, risks=None, recs=None, alerts=None):
    return readers.OperationalTables(
        aircraft=ScriptedTable(),
        projections=projections or ScriptedTable(),
        projection_points=ScriptedTable(),
        hazards=hazards or ScriptedTable(),
        hazard_coordinates=ScriptedTable(),
        encounters=encounters or ScriptedTable(),
        risks=risks or ScriptedTable(),
        airports=ScriptedTable(),
        metar=ScriptedTable(),
        taf=ScriptedTable(),
        taf_periods=ScriptedTable(),
        airport_assessments=ScriptedTable(),
        recommendations=recs or ScriptedTable(),
        alerts=alerts or ScriptedTable(),
    )


def _projection(aircraft_id, projection_id):
    return {
        "aircraft_id": aircraft_id,
        "projection_id": projection_id,
        "generated_at_epoch": NOW,
        "valid_until_epoch": NOW + 1000,
        "projection_status": "READY",
    }


def _encounter(encounter_id, aircraft_id, projection_id, hazard_id, version="v1"):
    return {
        "encounter_id": encounter_id,
        "aircraft_id": aircraft_id,
        "projection_id": projection_id,
        "hazard_id": hazard_id,
        "hazard_source_version": version,
        "encounter_state": "DETECTED",
    }


def test_confirm_selected_hazard_pins_accepts_compatible_and_rejects_drift():
    v1 = {
        "hazard_id": "H1",
        "source_version": "v1",
        "status": "ACTIVE",
        "materialization_status": "READY",
        "valid_to_epoch": NOW + 100,
        "geometry_hash": "hash-1",
        "materialization_id": "mat-1",
        "geometry_type": "POLYGON",
    }
    v2 = {**v1, "hazard_id": "H3", "source_version": "v2", "geometry_hash": "hash-2"}
    tables = _tables(
        hazards=ScriptedTable(records={"H1": v1, "H3": v2}),
    )
    result = observed.confirm_selected_hazard_pins(
        tables,
        (
            FakePin("H1", "v1", hazard=v1, geometry_hash="hash-1", materialization_id="mat-1"),
            FakePin("H3", "v1", hazard={**v2, "source_version": "v1"}, geometry_hash="hash-1"),
        ),
        now_epoch=NOW,
    )
    assert dict(result.confirmed) == {"H1": "v1"}
    assert [item.hazard_id for item in result.failed] == ["H3"]
    assert result.failed[0].reason == "HAZARD_PIN_VERSION_DRIFT"
    assert result.failed[0].exact_hazard["source_version"] == "v2"


def test_multi_hazard_loaders_scan_decision_tables_once():
    projections = ScriptedTable(
        scan_items=[
            _projection("a", "proj-a"),
            _projection("b", "proj-b"),
        ]
    )
    encounters = ScriptedTable(
        query_items={
            "H1": [_encounter("e1", "a", "proj-a", "H1")],
            "H2": [_encounter("e2", "b", "proj-b", "H2")],
        }
    )
    first = {
        "risk_id": "risk-first",
        "encounter_id": "e1",
        "generated_at_epoch": NOW,
    }
    second = {
        "risk_id": "risk-second",
        "encounter_id": "e1",
        "generated_at_epoch": NOW,
    }
    risks = ScriptedTable(scan_items=[first, second])
    recs = ScriptedTable(
        scan_items=[
            {
                "recommendation_id": "rec-1",
                "risk_id": "risk-first",
                "recommendation_status": "ACTIVE",
                "valid_until_utc": FUTURE,
            },
            {
                "recommendation_id": "rec-2",
                "risk_id": "risk-first",
                "recommendation_status": "ACTIVE",
                "valid_until_utc": FUTURE,
            },
        ]
    )
    alerts = ScriptedTable(
        scan_items=[
            {
                "alert_id": "alert-risk",
                "risk_id": "risk-first",
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
    )
    tables = _tables(
        projections=projections,
        encounters=encounters,
        risks=risks,
        recs=recs,
        alerts=alerts,
    )
    projection_index, _ = observed.load_current_projection_index(
        tables,
        now_epoch=NOW,
    )
    encounter_candidates, encounter_retrieval = (
        observed.load_encounter_candidates_for_hazards(tables, ("H1", "H2"))
    )
    decisions = observed.load_decision_candidates(tables, now_epoch=NOW)
    observed_set = observed.assemble_observed_operational_set(
        now_epoch=NOW,
        current_projections_by_aircraft=projection_index,
        selected_hazard_versions={"H1": "v1", "H2": "v1"},
        encounter_candidates=encounter_candidates,
        decision_candidates=decisions,
        retrieval=encounter_retrieval + decisions.retrieval,
    )

    assert len([call for call in projections.calls if call[0] == "scan"]) == 1
    assert len([call for call in risks.calls if call[0] == "scan"]) == 1
    assert len([call for call in recs.calls if call[0] == "scan"]) == 1
    assert len([call for call in alerts.calls if call[0] == "scan"]) == 1
    assert len([call for call in encounters.calls if call[0] == "query"]) == 2
    assert all(
        "source_version" not in str(call[1].get("FilterExpression", ""))
        and "hazard_source_version" not in str(call[1].get("FilterExpression", ""))
        for call in encounters.calls
        if call[0] == "query"
    )
    assert dict(observed_set.selected_hazard_versions) == {"H1": "v1", "H2": "v1"}
    assert [
        item["encounter_id"] for item in observed_set.current_encounters
    ] == ["e1", "e2"]
    assert observed_set.current_risks_by_encounter["e1"] is first
    assert len(observed_set.recommendation_candidates) == 2
    assert {item["alert_id"] for item in observed_set.alert_candidates} == {
        "alert-risk",
        "alert-rec",
    }
    assert observed_set.now_epoch == NOW
    assert not hasattr(observed, "_CACHE")


def test_failed_pin_is_not_passed_to_encounter_loader():
    encounters = ScriptedTable(
        query_items={"H1": [_encounter("e1", "a", "proj-a", "H1")]}
    )
    tables = _tables(encounters=encounters)
    observed.load_encounter_candidates_for_hazards(tables, ("H1",))
    assert [call[0] for call in encounters.calls] == ["query"]
    assert "H3" not in str(encounters.calls)


def test_observed_has_no_clock_cache_or_shapely():
    source = inspect.getsource(observed)
    assert "time.time" not in source
    assert "datetime.now" not in source
    assert "datetime.utcnow" not in source
    assert "shapely" not in source
    assert "geospatial" not in source
    assert "wilvor_ai" not in source
    assert Attr is not None
