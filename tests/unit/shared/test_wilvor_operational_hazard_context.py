"""Direct tests for shared hazard operational context loaders."""

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
    def __init__(
        self,
        *,
        scan_items=None,
        records=None,
        query_pages=None,
        get_sequence=None,
    ):
        self.scan_items = list(scan_items or [])
        self.records = dict(records or {})
        self.query_pages = list(query_pages) if query_pages is not None else None
        self.get_sequence = list(get_sequence) if get_sequence is not None else None
        self._query_index = 0
        self._get_index = 0
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
        if self.get_sequence is not None:
            if self._get_index < len(self.get_sequence):
                item = self.get_sequence[self._get_index]
                self._get_index += 1
            else:
                item = self.get_sequence[-1] if self.get_sequence else None
            if item is None:
                return {}
            return {"Item": item}
        key = kwargs["Key"]
        value = next(iter(key.values()))
        item = self.records.get(value)
        if item is None:
            return {}
        return {"Item": item}


def _tables(
    *,
    hazards=None,
    hazard_records=None,
    hazard_gets=None,
    encounters=None,
    encounter_query_pages=None,
    encounter_records=None,
    aircraft_records=None,
    projections=None,
    projection_records=None,
    risks=None,
    risk_records=None,
    recommendations=None,
    alerts=None,
):
    return readers.OperationalTables(
        aircraft=ScriptedTable(records=aircraft_records or {}),
        projections=ScriptedTable(
            scan_items=projections or [],
            records=projection_records or {},
        ),
        projection_points=ScriptedTable(),
        hazards=ScriptedTable(
            records=hazard_records or {},
            get_sequence=hazard_gets,
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


def _current_hazard(hazard_id="hazard-1", source_version="v1", **extra):
    item = {
        "hazard_id": hazard_id,
        "source_version": source_version,
        "status": "ACTIVE",
        "materialization_status": "READY",
        "valid_to_epoch": NOW + 1000,
    }
    item.update(extra)
    return item


def _current_projection(
    aircraft_id="abc123",
    projection_id="proj-1",
    generated_at_epoch=NOW,
):
    return {
        "aircraft_id": aircraft_id,
        "projection_id": projection_id,
        "generated_at_epoch": generated_at_epoch,
        "valid_until_epoch": NOW + 1000,
        "projection_status": "READY",
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
    }


def _current_aircraft(aircraft_id="abc123"):
    return {
        "aircraft_id": aircraft_id,
        "expires_at_epoch": NOW + 100,
    }


def _query_pages(*items):
    return [{"Items": list(items)}]


def test_empty_hazard_id_returns_none():
    tables = _tables()

    assert context.build_hazard_operational_context(
        tables,
        "",
        now_epoch=NOW,
    ) is None
    assert tables.hazards.calls == []


def test_missing_hazard_root_returns_none_without_descendant_io():
    tables = _tables()

    result = context.build_hazard_operational_context(
        tables,
        "missing",
        now_epoch=NOW,
    )

    assert result is None
    assert tables.encounters.calls == []
    assert tables.projections.calls == []
    assert tables.aircraft.calls == []


def test_retained_expired_root_is_neutral_and_skips_impact_query():
    expired = _current_hazard(status="EXPIRED", valid_to_epoch=NOW - 1)
    tables = _tables(hazard_records={"hazard-1": expired})

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    assert isinstance(result, linking.HazardOperationalContext)
    assert result.hazard is expired
    assert result.hazard_is_lifecycle_active is False
    assert result.hazard_is_current is False
    assert result.impacts == ()
    assert tables.encounters.calls == []


def test_cancelled_root_is_neutral_and_skips_impact_query():
    cancelled = _current_hazard(status="CANCELLED")
    tables = _tables(hazard_records={"hazard-1": cancelled})

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    assert result.hazard is cancelled
    assert result.hazard_is_lifecycle_active is False
    assert result.hazard_is_current is False
    assert result.impacts == ()
    assert tables.encounters.calls == []


def test_active_not_ready_is_lifecycle_active_but_not_current():
    building = _current_hazard(materialization_status="BUILDING")
    tables = _tables(hazard_records={"hazard-1": building})

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    assert result.hazard_is_lifecycle_active is True
    assert result.hazard_is_current is False
    assert result.impacts == ()
    assert tables.encounters.calls == []


def test_validity_ended_is_not_lifecycle_active():
    ended = _current_hazard(valid_to_epoch=NOW - 1)
    tables = _tables(hazard_records={"hazard-1": ended})

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    assert result.hazard_is_lifecycle_active is False
    assert result.hazard_is_current is False
    assert result.impacts == ()


def test_current_root_with_zero_observed_encounters_records_full_query():
    hazard = _current_hazard()
    tables = _tables(
        hazard_records={"hazard-1": hazard},
        encounter_query_pages=_query_pages(),
    )

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    assert result.hazard is hazard
    assert result.hazard_is_current is True
    assert result.source_version == "v1"
    assert result.impacts == ()
    assert result.hazard_version_link.state is linking.LinkState.PRESENT
    sources = {item.source: item for item in result.retrieval}
    assert sources["query_encounter_candidates_by_hazard"].coverage is (
        linking.Coverage.FULL_QUERY
    )
    assert tables.encounters.calls


def test_current_root_missing_source_version_does_not_evaluate_lineage():
    hazard = _current_hazard()
    hazard = {
        key: value
        for key, value in hazard.items()
        if key != "source_version"
    }
    tables = _tables(hazard_records={"hazard-1": hazard})

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    assert result.hazard is hazard
    assert result.hazard_is_lifecycle_active is True
    assert result.hazard_is_current is True
    assert result.source_version == ""
    assert result.impacts == ()
    assert result.hazard_version_link.state is linking.LinkState.MISSING
    assert result.hazard_version_link.kind is linking.LinkKind.VERSIONED
    assert result.hazard_version_link.reason == (
        linking.HAZARD_SOURCE_VERSION_LIMITATION
    )
    assert tables.encounters.calls == []
    sources = [item.source for item in result.retrieval]
    assert "query_encounter_candidates_by_hazard" not in sources
    assert any(
        linking.HAZARD_SOURCE_VERSION_LIMITATION in item.limitations
        for item in result.retrieval
    )


def test_one_current_encounter_builds_one_impact():
    hazard = _current_hazard()
    projection = _current_projection()
    encounter = _current_encounter()
    aircraft = _current_aircraft()
    tables = _tables(
        hazard_records={"hazard-1": hazard},
        encounter_query_pages=_query_pages(encounter),
        encounter_records={"proj-1#hazard-1#v1": encounter},
        projections=[projection],
        projection_records={"proj-1": projection},
        aircraft_records={"abc123": aircraft},
    )

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    assert len(result.impacts) == 1
    impact = result.impacts[0]
    assert impact.aircraft is aircraft
    assert impact.aircraft_is_current is True
    assert impact.projection is projection
    assert impact.projection_is_current is True
    assert impact.encounter.encounter is encounter
    assert impact.encounter.encounter_is_current is True


def test_two_current_aircraft_produce_two_impacts():
    hazard = _current_hazard()
    proj_a = _current_projection()
    proj_b = _current_projection(aircraft_id="def456", projection_id="proj-2")
    enc_a = _current_encounter()
    enc_b = _current_encounter(
        encounter_id="proj-2#hazard-1#v1",
        aircraft_id="def456",
        projection_id="proj-2",
    )
    tables = _tables(
        hazard_records={"hazard-1": hazard},
        encounter_query_pages=_query_pages(enc_a, enc_b),
        encounter_records={
            "proj-1#hazard-1#v1": enc_a,
            "proj-2#hazard-1#v1": enc_b,
        },
        projections=[proj_a, proj_b],
        projection_records={"proj-1": proj_a, "proj-2": proj_b},
        aircraft_records={
            "abc123": _current_aircraft(),
            "def456": _current_aircraft("def456"),
        },
    )

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    assert [
        item.encounter.encounter["encounter_id"]
        for item in result.impacts
    ] == ["proj-1#hazard-1#v1", "proj-2#hazard-1#v1"]


def test_older_source_version_encounter_is_excluded():
    hazard = _current_hazard()
    projection = _current_projection()
    stale = _current_encounter(
        encounter_id="proj-1#hazard-1#v-old",
        hazard_source_version="v-old",
    )
    tables = _tables(
        hazard_records={"hazard-1": hazard},
        encounter_query_pages=_query_pages(stale),
        encounter_records={"proj-1#hazard-1#v-old": stale},
        projections=[projection],
        projection_records={"proj-1": projection},
        aircraft_records={"abc123": _current_aircraft()},
    )

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    assert result.impacts == ()
    assert result.hazard_version_link.state is linking.LinkState.PRESENT


def test_stale_projection_encounter_is_excluded():
    hazard = _current_hazard()
    current = _current_projection(projection_id="proj-new", generated_at_epoch=NOW + 5)
    stale_encounter = _current_encounter(projection_id="proj-old")
    tables = _tables(
        hazard_records={"hazard-1": hazard},
        encounter_query_pages=_query_pages(stale_encounter),
        encounter_records={"proj-1#hazard-1#v1": stale_encounter},
        projections=[current],
        projection_records={"proj-new": current},
        aircraft_records={"abc123": _current_aircraft()},
    )

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    assert result.impacts == ()


def test_missing_encounter_hydration_is_hydration_missing():
    hazard = _current_hazard()
    projection = _current_projection()
    encounter = _current_encounter()
    tables = _tables(
        hazard_records={"hazard-1": hazard},
        encounter_query_pages=_query_pages(encounter),
        encounter_records={},
        projections=[projection],
        projection_records={"proj-1": projection},
        aircraft_records={"abc123": _current_aircraft()},
    )

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    item = result.impacts[0].encounter
    assert item.encounter is None
    assert item.encounter_link.state is linking.LinkState.HYDRATION_MISSING


def test_missing_aircraft_hydration_preserves_impact():
    hazard = _current_hazard()
    projection = _current_projection()
    encounter = _current_encounter()
    tables = _tables(
        hazard_records={"hazard-1": hazard},
        encounter_query_pages=_query_pages(encounter),
        encounter_records={"proj-1#hazard-1#v1": encounter},
        projections=[projection],
        projection_records={"proj-1": projection},
        aircraft_records={},
    )

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    impact = result.impacts[0]
    assert impact.encounter.encounter is encounter
    assert impact.aircraft is None
    assert impact.aircraft_link.state is linking.LinkState.HYDRATION_MISSING
    assert impact.aircraft_is_current is False


def test_retained_aircraft_is_preserved_and_not_current():
    hazard = _current_hazard()
    projection = _current_projection()
    encounter = _current_encounter()
    retained = {
        "aircraft_id": "abc123",
        "expires_at_epoch": NOW,
    }
    tables = _tables(
        hazard_records={"hazard-1": hazard},
        encounter_query_pages=_query_pages(encounter),
        encounter_records={"proj-1#hazard-1#v1": encounter},
        projections=[projection],
        projection_records={"proj-1": projection},
        aircraft_records={"abc123": retained},
    )

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    impact = result.impacts[0]
    assert impact.aircraft is retained
    assert impact.aircraft_is_current is False
    assert impact.aircraft_link.state is (
        linking.LinkState.HYDRATION_NO_LONGER_CURRENT
    )
    assert impact.encounter.encounter is encounter


def test_projection_hydration_missing_and_identity_mismatch():
    hazard = _current_hazard()
    compact = _current_projection()
    mismatched = {
        **compact,
        "aircraft_id": "other",
    }
    encounter = _current_encounter()
    missing_tables = _tables(
        hazard_records={"hazard-1": hazard},
        encounter_query_pages=_query_pages(encounter),
        encounter_records={"proj-1#hazard-1#v1": encounter},
        projections=[compact],
        projection_records={},
        aircraft_records={"abc123": _current_aircraft()},
    )
    mismatch_tables = _tables(
        hazard_records={"hazard-1": hazard},
        encounter_query_pages=_query_pages(encounter),
        encounter_records={"proj-1#hazard-1#v1": encounter},
        projections=[compact],
        projection_records={"proj-1": mismatched},
        aircraft_records={"abc123": _current_aircraft()},
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

    assert missing.impacts[0].projection is None
    assert missing.impacts[0].projection_link.state is (
        linking.LinkState.HYDRATION_MISSING
    )
    assert mismatch.impacts[0].projection is None
    assert mismatch.impacts[0].projection_link.state is (
        linking.LinkState.HYDRATION_IDENTITY_MISMATCH
    )


def test_missing_risk_stays_missing_not_false_or_zero():
    hazard = _current_hazard()
    projection = _current_projection()
    encounter = _current_encounter()
    tables = _tables(
        hazard_records={"hazard-1": hazard},
        encounter_query_pages=_query_pages(encounter),
        encounter_records={"proj-1#hazard-1#v1": encounter},
        projections=[projection],
        projection_records={"proj-1": projection},
        aircraft_records={"abc123": _current_aircraft()},
    )

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    risk = result.impacts[0].encounter.risk
    assert risk is None
    assert risk is not False
    assert risk != 0
    assert result.impacts[0].encounter.risk_link.state is linking.LinkState.MISSING


def test_two_recommendations_and_or_alerts_are_retained():
    hazard = _current_hazard()
    projection = _current_projection()
    encounter = _current_encounter()
    risk = {
        "risk_id": "risk-1",
        "encounter_id": "proj-1#hazard-1#v1",
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
    tables = _tables(
        hazard_records={"hazard-1": hazard},
        encounter_query_pages=_query_pages(encounter),
        encounter_records={"proj-1#hazard-1#v1": encounter},
        projections=[projection],
        projection_records={"proj-1": projection},
        aircraft_records={"abc123": _current_aircraft()},
        risks=[risk],
        risk_records={"risk-1": risk},
        recommendations=recs,
        alerts=alerts,
    )

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    item = result.impacts[0].encounter
    assert [rec["recommendation_id"] for rec in item.recommendations] == [
        "rec-1",
        "rec-2",
    ]
    assert [alert["alert_id"] for alert in item.alerts] == [
        "alert-risk",
        "alert-rec",
    ]


def test_missing_risk_valid_until_is_still_accepted():
    hazard = _current_hazard()
    projection = _current_projection()
    encounter = _current_encounter()
    risk = {
        "risk_id": "risk-1",
        "encounter_id": "proj-1#hazard-1#v1",
        "generated_at_epoch": NOW,
    }
    tables = _tables(
        hazard_records={"hazard-1": hazard},
        encounter_query_pages=_query_pages(encounter),
        encounter_records={"proj-1#hazard-1#v1": encounter},
        projections=[projection],
        projection_records={"proj-1": projection},
        aircraft_records={"abc123": _current_aircraft()},
        risks=[risk],
        risk_records={"risk-1": risk},
    )

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    assert result.impacts[0].encounter.risk is risk
    assert result.impacts[0].encounter.risk_link.state is linking.LinkState.PRESENT


def test_equal_risk_epoch_keeps_first_observed():
    hazard = _current_hazard()
    projection = _current_projection()
    encounter = _current_encounter()
    first = {
        "risk_id": "risk-first",
        "encounter_id": "proj-1#hazard-1#v1",
        "generated_at_epoch": NOW,
    }
    second = {
        "risk_id": "risk-second",
        "encounter_id": "proj-1#hazard-1#v1",
        "generated_at_epoch": NOW,
    }
    tables = _tables(
        hazard_records={"hazard-1": hazard},
        encounter_query_pages=_query_pages(encounter),
        encounter_records={"proj-1#hazard-1#v1": encounter},
        projections=[projection],
        projection_records={"proj-1": projection},
        aircraft_records={"abc123": _current_aircraft()},
        risks=[first, second],
        risk_records={"risk-first": first, "risk-second": second},
    )

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    assert result.impacts[0].encounter.risk is first


def test_final_root_v2_does_not_rebuild_or_substitute():
    v1 = _current_hazard(source_version="v1")
    v2 = _current_hazard(source_version="v2")
    projection = _current_projection()
    encounter = _current_encounter()
    tables = _tables(
        hazard_gets=[v1, v2, v2],
        encounter_query_pages=_query_pages(encounter),
        encounter_records={"proj-1#hazard-1#v1": encounter},
        projections=[projection],
        projection_records={"proj-1": projection},
        aircraft_records={"abc123": _current_aircraft()},
    )

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    assert result.hazard is v1
    assert result.hazard["source_version"] == "v1"
    assert result.source_version == "v1"
    assert result.hazard_is_current is True
    assert result.hazard_version_link.state is (
        linking.LinkState.HYDRATION_VERSION_MISMATCH
    )
    assert ("source_version", "v1") in result.hazard_version_link.selected_identity
    assert ("source_version", "v2") in result.hazard_version_link.observed_identity
    assert len(result.impacts) == 1
    assert result.impacts[0].encounter.encounter["hazard_source_version"] == "v1"
    query_calls = [call for call in tables.encounters.calls if call[0] == "query"]
    assert len(query_calls) >= 1


def test_final_root_still_v1_is_not_a_snapshot():
    hazard = _current_hazard()
    tables = _tables(
        hazard_records={"hazard-1": hazard},
        encounter_query_pages=_query_pages(),
    )

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    assert result.hazard_version_link.state is linking.LinkState.PRESENT
    assert any(
        linking.NO_SNAPSHOT_LIMITATION in item.limitations
        for item in result.retrieval
    )
    assert all(
        not hasattr(item, "complete_for_current_membership")
        for item in result.retrieval
    )
    assert all(not hasattr(item, "complete") for item in result.retrieval)


def test_hazard_gsi_query_drains_pages_and_has_no_limit():
    hazard = _current_hazard()
    projection = _current_projection()
    first = _current_encounter()
    second = _current_encounter(
        encounter_id="proj-2#hazard-1#v1",
        aircraft_id="def456",
        projection_id="proj-2",
    )
    proj_b = _current_projection(aircraft_id="def456", projection_id="proj-2")
    tables = _tables(
        hazard_records={"hazard-1": hazard},
        encounter_query_pages=[
            {"Items": [first], "LastEvaluatedKey": {"encounter_id": "enc-1"}},
            {"Items": [second]},
        ],
        encounter_records={
            "proj-1#hazard-1#v1": first,
            "proj-2#hazard-1#v1": second,
        },
        projections=[projection, proj_b],
        projection_records={"proj-1": projection, "proj-2": proj_b},
        aircraft_records={
            "abc123": _current_aircraft(),
            "def456": _current_aircraft("def456"),
        },
    )

    result = context.build_hazard_operational_context(
        tables,
        "hazard-1",
        now_epoch=NOW,
    )

    query_calls = [
        call for call in tables.encounters.calls if call[0] == "query"
    ]
    assert len(query_calls) == 2
    assert "Limit" not in query_calls[0][1]
    assert query_calls[1][1]["ExclusiveStartKey"] == {"encounter_id": "enc-1"}
    assert len(result.impacts) == 2
    query_obs = next(
        item
        for item in result.retrieval
        if item.source == "query_encounter_candidates_by_hazard"
    )
    assert query_obs.coverage is linking.Coverage.FULL_QUERY
    assert query_obs.consistency is linking.Consistency.EVENTUAL
    assert query_obs.limit is None
    assert linking.EVENTUAL_SCAN_LIMITATION in query_obs.limitations


def test_one_now_epoch_and_no_forbidden_imports_or_geography():
    captured = []
    original = current_set.is_current_hazard

    def wrapped(item, now_epoch):
        captured.append(now_epoch)
        return original(item, now_epoch)

    tables = _tables(hazard_records={"hazard-1": _current_hazard()})
    current_set.is_current_hazard = wrapped
    try:
        context.build_hazard_operational_context(
            tables,
            "hazard-1",
            now_epoch=NOW,
        )
    finally:
        current_set.is_current_hazard = original

    assert captured == [NOW]
    source = inspect.getsource(context)
    text = (PACKAGE_DIR / "context.py").read_text(encoding="utf-8")
    assert "time.time" not in source
    assert "datetime.now" not in source
    assert "datetime.utcnow" not in source
    assert "operational_api" not in text
    assert "wilvor_ai" not in text
    assert "resolve_region" not in source
    assert "search_current_impacts" not in source
    assert "geometry" not in inspect.getsource(
        context.build_hazard_operational_context
    )
