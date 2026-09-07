"""Direct tests for shared operational record and candidate readers."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from boto3.dynamodb.conditions import Attr, Key

from wilvor_operational import readers


REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_DIR = REPO_ROOT / "functions" / "shared" / "wilvor_operational"
NOW = 1_788_661_800
NOW_ISO = "2026-09-06T02:30:00Z"


class RecordingTable:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])
        self._index = 0

    def _next(self, default):
        if self._index < len(self.responses):
            response = self.responses[self._index]
            self._index += 1
            return response
        return default

    def get_item(self, **kwargs):
        self.calls.append(("get_item", kwargs))
        return self._next({})

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        return self._next({"Items": []})

    def scan(self, **kwargs):
        self.calls.append(("scan", kwargs))
        return self._next({"Items": []})


def condition_shape(expr):
    if expr is None:
        return None

    if hasattr(expr, "get_expression"):
        built = expr.get_expression()
        values = []
        for value in built["values"]:
            if hasattr(value, "get_expression"):
                values.append(condition_shape(value))
            elif hasattr(value, "name") and not isinstance(value, str):
                values.append(("name", value.name))
            else:
                values.append(value)
        return (built["operator"],) + tuple(values)

    return expr


def projection_fields(expression):
    return {part.strip() for part in expression.split(",")}


def test_exact_getters_preserve_retained_rows_and_return_none_when_missing():
    retained = {"aircraft_id": "abc123", "expires_at_epoch": NOW}
    aircraft = RecordingTable([{"Item": retained}])
    airport = RecordingTable([{}])
    metar = RecordingTable([{"Item": {"station_id": "KSEA"}}])
    taf = RecordingTable([{}])

    assert readers.get_aircraft_record(aircraft, "abc123") is retained
    assert readers.get_airport_status_record(airport, "KSEA") is None
    assert readers.get_metar_record(metar, "KSEA") == {"station_id": "KSEA"}
    assert readers.get_taf_record(taf, "KSEA") is None
    assert aircraft.calls[0][1]["ConsistentRead"] is True
    assert airport.calls[0][1]["Key"] == {"airport_id": "KSEA"}
    assert metar.calls[0][1]["Key"] == {"station_id": "KSEA"}
    assert taf.calls[0][1]["Key"] == {"station_id": "KSEA"}


def test_hydration_getters_use_exact_primary_keys_and_consistent_reads():
    projection = {"projection_id": "proj-1", "point_count": 12}
    hazard = {"hazard_id": "hazard-1", "source_version": "v1"}
    risk = {"risk_id": "risk-1", "encounter_id": "enc-1"}
    encounter = {"encounter_id": "enc-1", "matched_h3_cells": ["cell"]}
    projections = RecordingTable([{"Item": projection}])
    hazards = RecordingTable([{}])
    risks = RecordingTable([{"Item": risk}])
    encounters = RecordingTable([{"Item": encounter}])

    assert readers.get_projection_record(projections, "proj-1") is projection
    assert readers.get_hazard_record(hazards, "hazard-1") is None
    assert readers.get_risk_record(risks, "risk-1") is risk
    assert readers.get_encounter_record(encounters, "enc-1") is encounter
    assert projections.calls[0][1] == {
        "Key": {"projection_id": "proj-1"},
        "ConsistentRead": True,
    }
    assert hazards.calls[0][1] == {
        "Key": {"hazard_id": "hazard-1"},
        "ConsistentRead": True,
    }
    assert risks.calls[0][1] == {
        "Key": {"risk_id": "risk-1"},
        "ConsistentRead": True,
    }
    assert encounters.calls[0][1] == {
        "Key": {"encounter_id": "enc-1"},
        "ConsistentRead": True,
    }


def test_aircraft_candidate_pages_match_established_indexes_and_filters():
    table = RecordingTable([{"Items": []}, {"Items": []}, {"Items": []}])

    callsign = readers.query_aircraft_by_callsign_page(
        table,
        callsign="UAL123",
        now_epoch=NOW,
        limit=20,
        key=Key,
        attr=Attr,
    )
    h3 = readers.query_aircraft_by_h3_page(
        table,
        h3_cell="8428347ffffffff",
        now_epoch=NOW,
        limit=15,
        key=Key,
        attr=Attr,
    )
    scan = readers.scan_aircraft_page(
        table,
        now_epoch=NOW,
        limit=10,
        exclusive_start_key={"aircraft_id": "cursor-1"},
        attr=Attr,
    )

    assert callsign["items"] == []
    assert h3["last_evaluated_key"] is None
    assert scan["items"] == []
    assert table.calls[0][1]["IndexName"] == readers.IDX_AIRCRAFT_CALLSIGN
    assert table.calls[0][1]["ScanIndexForward"] is False
    assert condition_shape(table.calls[0][1]["KeyConditionExpression"]) == (
        "=",
        ("name", "callsign"),
        "UAL123",
    )
    assert table.calls[1][1]["IndexName"] == readers.IDX_AIRCRAFT_H3
    assert table.calls[2][0] == "scan"
    assert table.calls[2][1]["ExclusiveStartKey"] == {"aircraft_id": "cursor-1"}
    assert condition_shape(table.calls[2][1]["FilterExpression"]) == (
        ">",
        ("name", "expires_at_epoch"),
        NOW,
    )


def test_airport_and_hazard_candidate_pages_match_established_shapes():
    airports = RecordingTable([{"Items": []}, {"Items": []}, {"Items": []}])
    hazards = RecordingTable([{"Items": []}])

    readers.query_airports_by_impact_page(
        airports,
        weather_impact="WEATHER_IMPACTED",
        now_epoch=NOW,
        limit=25,
        key=Key,
        attr=Attr,
    )
    readers.query_airports_by_risk_page(
        airports,
        weather_risk="HIGH",
        now_epoch=NOW,
        limit=25,
        key=Key,
        attr=Attr,
    )
    readers.scan_airports_page(
        airports,
        now_epoch=NOW,
        limit=40,
        attr=Attr,
    )
    readers.query_active_hazard_candidates_page(
        hazards,
        now_epoch=NOW,
        limit=30,
        key=Key,
        attr=Attr,
    )

    assert airports.calls[0][1]["IndexName"] == readers.IDX_AIRPORT_IMPACT_TIME
    assert airports.calls[1][1]["IndexName"] == readers.IDX_AIRPORT_RISK_TIME
    assert airports.calls[2][0] == "scan"
    assert hazards.calls[0][1]["IndexName"] == readers.IDX_HAZARD_STATUS_VALIDITY
    assert hazards.calls[0][1]["ScanIndexForward"] is True
    assert condition_shape(hazards.calls[0][1]["KeyConditionExpression"]) == (
        "AND",
        ("=", ("name", "status"), "ACTIVE"),
        (">=", ("name", "valid_to_epoch"), NOW),
    )
    assert condition_shape(hazards.calls[0][1]["FilterExpression"]) == (
        "=",
        ("name", "materialization_status"),
        "READY",
    )


def test_index_and_decision_candidate_scans_match_established_shapes():
    projections = RecordingTable([{"Items": []}])
    hazards = RecordingTable([{"Items": []}])
    encounters = RecordingTable([{"Items": []}])
    risks = RecordingTable([{"Items": []}])
    recommendations = RecordingTable([{"Items": []}, {"Items": []}])
    alerts = RecordingTable([{"Items": []}, {"Items": []}])

    readers.scan_projection_index_candidates(
        projections,
        now_epoch=NOW,
        attr=Attr,
    )
    readers.scan_hazard_index_candidates(
        hazards,
        now_epoch=NOW,
        attr=Attr,
    )
    readers.scan_encounter_candidates(encounters, attr=Attr)
    readers.scan_risk_candidates(risks)
    readers.scan_recommendation_candidates(
        recommendations,
        now_iso=NOW_ISO,
        project=True,
        attr=Attr,
    )
    readers.scan_recommendation_candidates(
        recommendations,
        now_iso=NOW_ISO,
        project=False,
        attr=Attr,
    )
    readers.scan_alert_candidates(
        alerts,
        now_iso=NOW_ISO,
        project=True,
        attr=Attr,
    )
    readers.scan_alert_candidates(
        alerts,
        now_iso=NOW_ISO,
        project=False,
        attr=Attr,
    )

    assert condition_shape(projections.calls[0][1]["FilterExpression"]) == (
        "AND",
        ("=", ("name", "projection_status"), "READY"),
        (">", ("name", "valid_until_epoch"), NOW),
    )
    assert condition_shape(hazards.calls[0][1]["FilterExpression"]) == (
        "AND",
        (
            "AND",
            ("=", ("name", "status"), "ACTIVE"),
            ("=", ("name", "materialization_status"), "READY"),
        ),
        (">=", ("name", "valid_to_epoch"), NOW),
    )
    assert "FilterExpression" not in risks.calls[0][1]
    assert projection_fields(
        recommendations.calls[0][1]["ProjectionExpression"]
    ) == {
        "recommendation_id",
        "risk_id",
        "recommendation_status",
        "valid_until_utc",
        "aircraft_id",
        "hazard_id",
        "risk_level",
        "risk_score",
        "confidence",
        "primary_action_type",
        "preferred_airport_id",
        "preferred_airport_score",
        "created_at_utc",
        "created_at_epoch",
    }
    assert "ProjectionExpression" not in recommendations.calls[1][1]
    assert "ProjectionExpression" not in alerts.calls[1][1]


def test_hazard_encounter_query_uses_existing_gsi_and_drains_pages():
    table = RecordingTable(
        [
            {
                "Items": [{"encounter_id": "enc-1"}],
                "LastEvaluatedKey": {"encounter_id": "enc-1"},
            },
            {"Items": [{"encounter_id": "enc-2"}]},
        ]
    )

    items = readers.query_encounter_candidates_by_hazard(
        table,
        "hazard-1",
        key=Key,
        attr=Attr,
    )

    assert items == [
        {"encounter_id": "enc-1"},
        {"encounter_id": "enc-2"},
    ]
    assert len(table.calls) == 2
    first = table.calls[0][1]
    assert first["IndexName"] == readers.IDX_ENCOUNTER_HAZARD_TIME
    assert first["IndexName"] == "hazard_id-detected_at_epoch-index"
    assert first["ScanIndexForward"] is True
    assert "Limit" not in first
    assert "ConsistentRead" not in first
    assert condition_shape(first["KeyConditionExpression"]) == (
        "=",
        ("name", "hazard_id"),
        "hazard-1",
    )
    assert condition_shape(first["FilterExpression"]) == (
        "IN",
        ("name", "encounter_state"),
        ["DETECTED", "MONITORING"],
    )
    assert projection_fields(first["ProjectionExpression"]) == projection_fields(
        readers.ENCOUNTER_CANDIDATE_PROJECTION
    )
    assert table.calls[1][1]["ExclusiveStartKey"] == {"encounter_id": "enc-1"}
    assert "source_version" not in str(first["KeyConditionExpression"])
    assert "source_version" not in str(first.get("FilterExpression", ""))


def test_hazard_encounter_query_honors_injected_query_all():
    captured = []

    def fake_query_all(table, **kwargs):
        captured.append(kwargs)
        return [{"encounter_id": "enc-injected"}]

    items = readers.query_encounter_candidates_by_hazard(
        object(),
        "hazard-9",
        query_all=fake_query_all,
        key=Key,
        attr=Attr,
    )

    assert items == [{"encounter_id": "enc-injected"}]
    assert captured[0]["IndexName"] == readers.IDX_ENCOUNTER_HAZARD_TIME
    assert "current_set" not in captured[0]


def test_projection_points_are_single_page_and_coordinates_drain():
    points = RecordingTable(
        [
            {
                "Items": [{"point_key": "1"}],
                "LastEvaluatedKey": {"point_key": "1"},
            },
            {"Items": [{"point_key": "2"}]},
        ]
    )
    coordinates = RecordingTable(
        [
            {"Items": [{"sequence_number": 0}], "LastEvaluatedKey": {"k": "1"}},
            {"Items": [{"sequence_number": 1}]},
        ]
    )

    page = readers.query_projection_points_page(
        points,
        "proj-1",
        key=Key,
    )
    rows = readers.query_hazard_coordinate_rows(
        coordinates,
        "hazard-1#v1",
        key=Key,
    )

    assert page["items"] == [{"point_key": "1"}]
    assert page["last_evaluated_key"] == {"point_key": "1"}
    assert len(points.calls) == 1
    assert points.calls[0][1]["ConsistentRead"] is True
    assert rows == [{"sequence_number": 0}, {"sequence_number": 1}]
    assert coordinates.calls[1][1]["ExclusiveStartKey"] == {"k": "1"}


def test_taf_period_query_uses_established_window_and_first_page():
    table = RecordingTable(
        [
            {
                "Items": [{"period_key": "p1"}],
                "LastEvaluatedKey": {"period_key": "p1"},
            },
            {"Items": [{"period_key": "p2"}]},
        ]
    )

    page = readers.query_taf_period_candidates_page(
        table,
        station_id="KSEA",
        now_epoch=NOW,
        limit=50,
        key=Key,
    )

    assert page["items"] == [{"period_key": "p1"}]
    assert len(table.calls) == 1
    kwargs = table.calls[0][1]
    assert kwargs["IndexName"] == readers.IDX_TAF_PERIOD_STATION_TIME
    assert kwargs["Limit"] == 50
    assert condition_shape(kwargs["KeyConditionExpression"]) == (
        "AND",
        ("=", ("name", "station_id"), "KSEA"),
        ("BETWEEN", ("name", "period_from_epoch"), NOW - 21600, NOW + 129600),
    )


def test_operational_tables_is_an_injected_namespace():
    tables = readers.OperationalTables(
        aircraft="a",
        projections="p",
        projection_points="pp",
        hazards="h",
        hazard_coordinates="hc",
        encounters="e",
        risks="r",
        airports="ap",
        metar="m",
        taf="t",
        taf_periods="tp",
        airport_assessments="aa",
        recommendations="rec",
        alerts="al",
    )

    assert tables.aircraft == "a"
    assert tables.alerts == "al"
    assert not inspect.isabstract(readers.OperationalTables)


def test_readers_do_not_import_current_set_or_define_current_loaders():
    source = (PACKAGE_DIR / "readers.py").read_text(encoding="utf-8")
    names = [name for name in dir(readers) if name.startswith("load_current_")]

    assert names == []
    assert "current_set" not in source
    assert "load_current_" not in source
    assert "os.environ" not in source
    assert "time.time" not in source
    assert "datetime.now" not in source
    assert "boto3.resource" not in source
    assert "boto3.client" not in source
    assert "current_set" not in readers.__dict__


def test_package_init_stays_boto3_free_and_does_not_export_readers():
    init_text = (PACKAGE_DIR / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(init_text)
    imported = []

    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            imported.append((node.level, node.module or ""))
        elif isinstance(node, ast.Import):
            imported.extend((0, alias.name) for alias in node.names)

    assert "boto3" not in init_text
    assert all(module not in {"access", "readers"} for _level, module in imported)
    assert imported == [(1, "current_set")]


def test_readers_source_does_not_create_resources_or_read_env():
    source = inspect.getsource(readers)
    assert "boto3.resource" not in source
    assert "boto3.client" not in source
    assert "os.environ" not in source
    assert "time.time(" not in source
