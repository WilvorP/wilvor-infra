"""Direct tests for Phase 1D.1 deterministic entity discovery."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from wilvor_operational import current_set
from wilvor_operational import discovery
from wilvor_operational import linking
from wilvor_operational import readers


REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_DIR = REPO_ROOT / "functions" / "shared" / "wilvor_operational"
NOW = 1_788_661_800
H3_CELL = "8428347ffffffff"


class ScriptedTable:
    def __init__(self, *, records=None, query_pages=None):
        self.records = dict(records or {})
        self.query_pages = list(query_pages or [])
        self.calls = []
        self._query_index = 0

    def get_item(self, **kwargs):
        self.calls.append(("get_item", kwargs))
        key = kwargs["Key"]
        value = next(iter(key.values()))
        item = self.records.get(value)
        if item is None:
            return {}
        return {"Item": item}

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        if "ExclusiveStartKey" not in kwargs:
            self._query_index = 0
        if self._query_index < len(self.query_pages):
            page = self.query_pages[self._query_index]
            self._query_index += 1
            return page
        return {"Items": []}

    def scan(self, **kwargs):
        raise AssertionError("discovery must not scan tables")


def _tables(*, aircraft=None, hazards=None, airports=None):
    return readers.OperationalTables(
        aircraft=aircraft or ScriptedTable(),
        projections=ScriptedTable(),
        projection_points=ScriptedTable(),
        hazards=hazards or ScriptedTable(),
        hazard_coordinates=ScriptedTable(),
        encounters=ScriptedTable(),
        risks=ScriptedTable(),
        airports=airports or ScriptedTable(),
        metar=ScriptedTable(),
        taf=ScriptedTable(),
        taf_periods=ScriptedTable(),
        airport_assessments=ScriptedTable(),
        recommendations=ScriptedTable(),
        alerts=ScriptedTable(),
    )


def _current_aircraft(aircraft_id, callsign="UAL123", h3_cell=H3_CELL):
    return {
        "aircraft_id": aircraft_id,
        "callsign": callsign,
        "current_h3_cell": h3_cell,
        "expires_at_epoch": NOW + 60,
    }


def _expired_aircraft(aircraft_id, callsign="UAL123", h3_cell=H3_CELL):
    return {
        "aircraft_id": aircraft_id,
        "callsign": callsign,
        "current_h3_cell": h3_cell,
        "expires_at_epoch": NOW,
    }


def _current_hazard(
    hazard_id,
    *,
    product_type="SIGMET",
    hazard_type="CONVECTION",
    source_version="v1",
    materialization_status="READY",
    status="ACTIVE",
    valid_to_epoch=NOW + 60,
):
    return {
        "hazard_id": hazard_id,
        "source_version": source_version,
        "product_type": product_type,
        "hazard_type": hazard_type,
        "status": status,
        "materialization_status": materialization_status,
        "valid_to_epoch": valid_to_epoch,
    }


def _current_airport(airport_id, *, risk="HIGH", impact="WEATHER_IMPACTED"):
    return {
        "airport_id": airport_id,
        "weather_risk_level": risk,
        "weather_impact_status": impact,
        "expires_at_epoch": NOW + 60,
    }


def _expired_airport(airport_id, *, risk="HIGH", impact="WEATHER_IMPACTED"):
    return {
        "airport_id": airport_id,
        "weather_risk_level": risk,
        "weather_impact_status": impact,
        "expires_at_epoch": NOW,
    }


def _hazard_ids(result):
    return {
        dict(match.identity)["hazard_id"]
        for match in result.matches
    }


def _query_observation(result):
    assert len(result.retrieval) == 1
    return result.retrieval[0]


def test_aircraft_by_id_empty_missing_current_and_retained():
    current = _current_aircraft("abc123")
    expired = _expired_aircraft("def456")
    tables = _tables(
        aircraft=ScriptedTable(records={"abc123": current, "def456": expired})
    )

    empty = discovery.discover_aircraft_by_id(_tables(), "", now_epoch=NOW)
    missing = discovery.discover_aircraft_by_id(tables, "missing", now_epoch=NOW)
    found = discovery.discover_aircraft_by_id(tables, "ABC123", now_epoch=NOW)
    retained = discovery.discover_aircraft_by_id(tables, "def456", now_epoch=NOW)

    assert empty.matches == ()
    assert empty.retrieval == ()
    assert missing.matches == ()
    assert missing.retrieval[0].coverage == linking.Coverage.EXACT_PK
    assert missing.retrieval[0].consistency == linking.Consistency.CONSISTENT
    assert missing.retrieval[0].limit is None
    assert found.matches[0].source is current
    assert found.matches[0].is_current is True
    assert found.matches[0].identity == (("aircraft_id", "abc123"),)
    assert retained.matches[0].source is expired
    assert retained.matches[0].is_current is False
    assert current_set.is_current_aircraft(expired, NOW) is False


def test_callsign_returns_all_current_matches_and_no_winner():
    first = _current_aircraft("aaa111", callsign="UAL123")
    second = _current_aircraft("bbb222", callsign="UAL123")
    expired = _expired_aircraft("ccc333", callsign="UAL123")
    tables = _tables(
        aircraft=ScriptedTable(
            query_pages=[
                {
                    "Items": [first, expired],
                    "LastEvaluatedKey": {"aircraft_id": "aaa111"},
                },
                {"Items": [second]},
            ]
        )
    )

    result = discovery.discover_aircraft_by_callsign(
        tables,
        "ual123",
        now_epoch=NOW,
    )

    assert {match.source["aircraft_id"] for match in result.matches} == {
        "aaa111",
        "bbb222",
    }
    assert all(match.is_current for match in result.matches)
    assert len(result.matches) == 2
    observation = _query_observation(result)
    assert observation.coverage == linking.Coverage.FULL_QUERY
    assert observation.consistency == linking.Consistency.EVENTUAL
    assert observation.limit is None
    assert discovery.CALLSIGN_CASE_LIMITATION in observation.limitations
    assert linking.EVENTUAL_SCAN_LIMITATION in observation.limitations
    assert "Limit" not in tables.aircraft.calls[0][1]
    assert "ConsistentRead" not in tables.aircraft.calls[0][1]
    assert len(tables.aircraft.calls) == 2
    assert tables.aircraft.calls[1][1]["ExclusiveStartKey"] == {
        "aircraft_id": "aaa111"
    }
    scan_calls = [call for call in tables.aircraft.calls if call[0] == "scan"]
    assert scan_calls == []


def test_callsign_zero_and_one_match_and_empty_query():
    only = _current_aircraft("abc123")
    tables = _tables(
        aircraft=ScriptedTable(query_pages=[{"Items": [only]}])
    )

    empty = discovery.discover_aircraft_by_callsign(_tables(), "   ", now_epoch=NOW)
    none = discovery.discover_aircraft_by_callsign(
        _tables(aircraft=ScriptedTable(query_pages=[{"Items": []}])),
        "UAL999",
        now_epoch=NOW,
    )
    one = discovery.discover_aircraft_by_callsign(tables, "UAL123", now_epoch=NOW)

    assert empty.matches == ()
    assert empty.retrieval == ()
    assert none.matches == ()
    assert one.matches[0].source is only
    assert one.matches[0].is_current is True


def test_h3_discovery_drains_pages_and_excludes_expired():
    first = _current_aircraft("aaa111")
    expired = _expired_aircraft("ccc333")
    second = _current_aircraft("bbb222")
    tables = _tables(
        aircraft=ScriptedTable(
            query_pages=[
                {"Items": [first, expired], "LastEvaluatedKey": {"k": "1"}},
                {"Items": [second]},
            ]
        )
    )

    result = discovery.discover_aircraft_by_h3(tables, H3_CELL, now_epoch=NOW)

    assert {match.source["aircraft_id"] for match in result.matches} == {
        "aaa111",
        "bbb222",
    }
    observation = _query_observation(result)
    assert observation.coverage == linking.Coverage.FULL_QUERY
    assert observation.consistency == linking.Consistency.EVENTUAL
    assert observation.limit is None
    assert "Limit" not in tables.aircraft.calls[0][1]
    assert len(tables.aircraft.calls) == 2


def test_current_hazards_use_phase_1a_and_product_type():
    current_sigmet = _current_hazard("hz-sig")
    airmet = _current_hazard("hz-air", product_type="AIRMET", hazard_type="ICING")
    building = _current_hazard("hz-build", materialization_status="BUILDING")
    cancelled = _current_hazard("hz-can", status="CANCELLED")
    inclusive = _current_hazard("hz-end", valid_to_epoch=NOW)
    tables = _tables(
        hazards=ScriptedTable(
            query_pages=[
                {
                    "Items": [current_sigmet, airmet, building],
                    "LastEvaluatedKey": {"k": "1"},
                },
                {"Items": [cancelled, inclusive]},
            ]
        )
    )

    all_current = discovery.discover_current_hazards(tables, now_epoch=NOW)
    sigmets = discovery.discover_current_hazards(
        tables,
        now_epoch=NOW,
        product_type="SIGMET",
    )

    assert _hazard_ids(all_current) == {"hz-sig", "hz-air", "hz-end"}
    assert _hazard_ids(sigmets) == {"hz-sig", "hz-end"}
    assert current_set.is_current_hazard(inclusive, NOW) is True
    assert current_set.is_current_hazard(building, NOW) is False
    observation = _query_observation(all_current)
    assert observation.coverage == linking.Coverage.FULL_QUERY
    assert observation.consistency == linking.Consistency.EVENTUAL
    assert observation.limit is None
    assert "ProjectionExpression" not in tables.hazards.calls[0][1]


def test_hazard_type_is_distinct_from_product_type():
    convection = _current_hazard("hz-conv", hazard_type="CONVECTION")
    turbulence = _current_hazard(
        "hz-turb",
        product_type="SIGMET",
        hazard_type="TURBULENCE",
    )
    tables = _tables(
        hazards=ScriptedTable(query_pages=[{"Items": [convection, turbulence]}])
    )

    by_phenomenon = discovery.discover_current_hazards(
        tables,
        now_epoch=NOW,
        hazard_type="CONVECTION",
    )
    misread = discovery.discover_current_hazards(
        tables,
        now_epoch=NOW,
        hazard_type="SIGMET",
    )

    assert _hazard_ids(by_phenomenon) == {"hz-conv"}
    assert misread.matches == ()
    assert by_phenomenon.matches[0].identity == (
        ("hazard_id", "hz-conv"),
        ("source_version", "v1"),
    )


def test_explicit_hazard_ids_use_exact_pk_and_do_not_replace_ids():
    current = _current_hazard("hz-1")
    airmet = _current_hazard("hz-2", product_type="AIRMET")
    tables = _tables(
        hazards=ScriptedTable(records={"hz-1": current, "hz-2": airmet})
    )

    found = discovery.discover_current_hazards(
        tables,
        now_epoch=NOW,
        hazard_ids=["hz-1", "missing", "hz-2"],
    )
    filtered = discovery.discover_current_hazards(
        tables,
        now_epoch=NOW,
        hazard_ids=["hz-1", "hz-2"],
        product_type="SIGMET",
    )
    missing_only = discovery.discover_current_hazards(
        tables,
        now_epoch=NOW,
        hazard_ids=["missing"],
    )

    assert [dict(match.identity)["hazard_id"] for match in found.matches] == [
        "hz-1",
        "hz-2",
    ]
    assert found.matches[0].is_current is True
    assert found.matches[0].source is current
    assert [obs.coverage for obs in found.retrieval] == [
        linking.Coverage.EXACT_PK,
        linking.Coverage.EXACT_PK,
        linking.Coverage.EXACT_PK,
    ]
    assert all(
        obs.consistency == linking.Consistency.CONSISTENT
        for obs in found.retrieval
    )
    assert _hazard_ids(filtered) == {"hz-1"}
    assert missing_only.matches == ()
    assert missing_only.retrieval[0].coverage == linking.Coverage.EXACT_PK
    get_keys = [call[1]["Key"]["hazard_id"] for call in tables.hazards.calls]
    assert get_keys == ["hz-1", "missing", "hz-2", "hz-1", "hz-2", "missing"]
    assert all(call[0] == "get_item" for call in tables.hazards.calls)


def test_airport_exact_and_weather_discovery():
    current = _current_airport("KSEA")
    expired = _expired_airport("KPDX")
    extra_current = _current_airport("KSFO", risk="HIGH", impact="WEATHER_IMPACTED")
    airports = ScriptedTable(
        records={"KSEA": current, "KPDX": expired},
        query_pages=[
            {"Items": [current, expired], "LastEvaluatedKey": {"k": "1"}},
            {"Items": [extra_current]},
        ],
    )
    tables = _tables(airports=airports)

    exact = discovery.discover_airport_by_id(tables, "ksea", now_epoch=NOW)
    retained = discovery.discover_airport_by_id(tables, "kpdx", now_epoch=NOW)
    missing = discovery.discover_airport_by_id(tables, "KXXX", now_epoch=NOW)
    risk = discovery.discover_airports_by_weather_risk(
        tables,
        "HIGH",
        now_epoch=NOW,
    )
    impact = discovery.discover_airports_by_weather_impact(
        _tables(
            airports=ScriptedTable(
                query_pages=[
                    {"Items": [current, expired], "LastEvaluatedKey": {"k": "1"}},
                    {"Items": [extra_current]},
                ]
            )
        ),
        "WEATHER_IMPACTED",
        now_epoch=NOW,
    )

    assert exact.matches[0].source is current
    assert exact.matches[0].is_current is True
    assert exact.matches[0].identity == (("airport_id", "KSEA"),)
    assert exact.retrieval[0].coverage == linking.Coverage.EXACT_PK
    assert retained.matches[0].is_current is False
    assert missing.matches == ()
    assert {match.source["airport_id"] for match in risk.matches} == {
        "KSEA",
        "KSFO",
    }
    assert {match.source["airport_id"] for match in impact.matches} == {
        "KSEA",
        "KSFO",
    }
    assert _query_observation(risk).coverage == linking.Coverage.FULL_QUERY
    assert _query_observation(risk).consistency == linking.Consistency.EVENTUAL
    assert _query_observation(risk).limit is None
    assert len(airports.calls) >= 2
    assert "Limit" not in airports.calls[-1][1]


def test_discovery_has_no_completeness_boolean_or_hidden_clock():
    source = inspect.getsource(discovery)
    assert "complete=True" not in source
    assert "is_complete" not in source
    assert "complete_for_current_membership" not in source
    assert "time.time" not in source
    assert "datetime.now" not in source
    assert "datetime.utcnow" not in source
    assert "wilvor_ai" not in source
    assert "operational_api" not in source
    assert "shapely" not in source
    assert "regions" not in source
    assert "geometry.py" not in source
    assert "discover_current_encounters" not in source
    assert "EncounterDiscoveryResult" not in source
    assert "ToolResult" not in source
    assert "Evidence" not in source


def test_discovery_does_not_import_ai_or_change_package_init():
    init_text = (PACKAGE_DIR / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(init_text)
    imported = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")

    assert imported == ["current_set"]
    assert "wilvor_ai" not in inspect.getsource(discovery)
    assert not hasattr(discovery, "discover_current_encounters_for_aircraft")
    assert not hasattr(discovery, "discover_current_encounters_for_hazard")
    assert not hasattr(discovery, "search_current_encounters")
    assert not hasattr(discovery, "EncounterDiscoveryResult")
