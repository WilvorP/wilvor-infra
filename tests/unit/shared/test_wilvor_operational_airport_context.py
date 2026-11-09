"""Direct tests for shared airport operational context loaders."""

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


class ScriptedTable:
    def __init__(
        self,
        *,
        records=None,
        query_pages=None,
        get_sequence=None,
    ):
        self.records = dict(records or {})
        self.query_pages = list(query_pages) if query_pages is not None else None
        self.get_sequence = list(get_sequence) if get_sequence is not None else None
        self._query_index = 0
        self._get_index = 0
        self.calls = []

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


def _tables(
    *,
    airport_records=None,
    airport_gets=None,
    metar_records=None,
    taf_records=None,
    period_pages=None,
):
    return readers.OperationalTables(
        aircraft=ScriptedTable(),
        projections=ScriptedTable(),
        projection_points=ScriptedTable(),
        hazards=ScriptedTable(),
        hazard_coordinates=ScriptedTable(),
        encounters=ScriptedTable(),
        risks=ScriptedTable(),
        airports=ScriptedTable(
            records=airport_records or {},
            get_sequence=airport_gets,
        ),
        metar=ScriptedTable(records=metar_records or {}),
        taf=ScriptedTable(records=taf_records or {}),
        taf_periods=ScriptedTable(query_pages=period_pages),
        airport_assessments=ScriptedTable(),
        recommendations=ScriptedTable(),
        alerts=ScriptedTable(),
    )


def _status(
    airport_id="KSEA",
    station_id="KSEA",
    *,
    current=True,
    **extra,
):
    item = {
        "airport_id": airport_id,
        "station_id": station_id,
        "expires_at_epoch": NOW + 100 if current else NOW,
        "updated_at_epoch": NOW,
        "source_metar_version": "M1",
        "source_taf_version": "T1",
        "taf_version_key": "KSEA#T1",
        "metar_freshness_status": "FRESH",
        "taf_freshness_status": "FRESH",
        "weather_risk_level": "LOW",
        "flight_category": "VFR",
        "has_metar": True,
        "has_taf": True,
    }
    item.update(extra)
    return item


def _metar(station_id="KSEA", metar_version="M1", **extra):
    item = {
        "station_id": station_id,
        "metar_version": metar_version,
    }
    item.update(extra)
    return item


def _taf(
    station_id="KSEA",
    taf_version_key="KSEA#T1",
    source_version="T1",
    **extra,
):
    item = {
        "station_id": station_id,
        "taf_version_key": taf_version_key,
        "source_version": source_version,
        "taf_version": source_version,
        "period_materialization_status": "READY",
    }
    item.update(extra)
    return item


def _period(taf_version_key="KSEA#T1", period_key="p1", **extra):
    item = {
        "taf_version_key": taf_version_key,
        "period_key": period_key,
        "period_materialization_status": "READY",
    }
    item.update(extra)
    return item


def _build(tables, airport_id="KSEA"):
    return context.build_airport_operational_context(
        tables,
        airport_id,
        now_epoch=NOW,
    )


def test_empty_airport_id_returns_none_without_io():
    tables = _tables()

    assert _build(tables, "") is None
    assert _build(tables, "   ") is None
    assert tables.airports.calls == []
    assert tables.metar.calls == []
    assert tables.taf.calls == []
    assert tables.taf_periods.calls == []


def test_missing_exact_airport_status_returns_none_without_descendants():
    tables = _tables()

    result = _build(tables, "KSEA")

    assert result is None
    assert len(tables.airports.calls) == 1
    assert tables.airports.calls[0][1]["Key"] == {"airport_id": "KSEA"}
    assert tables.metar.calls == []
    assert tables.taf.calls == []
    assert tables.taf_periods.calls == []
    assert tables.airport_assessments.calls == []


def test_current_root_and_normalized_airport_id():
    status = _status()
    metar = _metar()
    taf = _taf()
    tables = _tables(
        airport_records={"KSEA": status},
        metar_records={"KSEA": metar},
        taf_records={"KSEA": taf},
        period_pages=[{"Items": [_period()]}],
    )

    result = _build(tables, "ksea")

    assert result.airport is status
    assert result.airport_is_current is True
    assert result.station_id == "KSEA"
    assert result.latest_metar is metar
    assert result.latest_taf is taf
    assert result.airport_status_link.state is linking.LinkState.PRESENT
    assert tables.airports.calls[0][1]["Key"] == {"airport_id": "KSEA"}


def test_retained_expired_root_is_returned_and_still_observes_weather():
    status = _status(current=False)
    metar = _metar()
    taf = _taf()
    tables = _tables(
        airport_records={"KSEA": status},
        metar_records={"KSEA": metar},
        taf_records={"KSEA": taf},
        period_pages=[{"Items": [_period()]}],
    )

    result = _build(tables)

    assert result is not None
    assert result.airport is status
    assert result.airport_is_current is False
    assert result.latest_metar is metar
    assert result.latest_taf is taf
    assert result.latest_taf_periods == (_period(),)
    assert tables.metar.calls
    assert tables.taf.calls


def test_missing_station_id_does_not_fall_back_to_airport_id():
    status = _status(station_id="")
    tables = _tables(
        airport_records={"KSEA": status},
        metar_records={"KSEA": _metar()},
        taf_records={"KSEA": _taf()},
        period_pages=[{"Items": [_period()]}],
    )

    result = _build(tables)

    assert result.airport is status
    assert result.station_id == ""
    assert result.latest_metar is None
    assert result.latest_taf is None
    assert result.latest_taf_periods == ()
    assert result.metar_source_link.state is linking.LinkState.MISSING
    assert result.taf_source_link.state is linking.LinkState.MISSING
    assert result.taf_periods_link.state is linking.LinkState.MISSING
    assert result.metar_source_link.state is not linking.LinkState.PRESENT
    assert tables.metar.calls == []
    assert tables.taf.calls == []
    assert tables.taf_periods.calls == []
    assert all(
        call[1]["Key"] == {"airport_id": "KSEA"}
        for call in tables.airports.calls
    )


def test_latest_metar_present_absent_match_mismatch_and_unproven():
    status_match = _status()
    metar_m1 = _metar(metar_version="M1")
    present = _build(
        _tables(
            airport_records={"KSEA": status_match},
            metar_records={"KSEA": metar_m1},
            taf_records={"KSEA": _taf()},
        )
    )
    assert present.latest_metar is metar_m1
    assert present.metar_source_link.state is linking.LinkState.PRESENT

    status_missing_metar = _status()
    absent = _build(
        _tables(
            airport_records={"KSEA": status_missing_metar},
            taf_records={"KSEA": _taf()},
        )
    )
    assert absent.latest_metar is None
    assert absent.metar_source_link.state is linking.LinkState.HYDRATION_MISSING
    assert absent.latest_metar is not False
    assert "VFR" not in str(absent.metar_source_link.state)

    status_mismatch = _status(source_metar_version="M1")
    metar_m2 = _metar(metar_version="M2")
    mismatch = _build(
        _tables(
            airport_records={"KSEA": status_mismatch},
            metar_records={"KSEA": metar_m2},
            taf_records={"KSEA": _taf()},
        )
    )
    assert mismatch.latest_metar is metar_m2
    assert mismatch.airport is status_mismatch
    assert mismatch.airport["source_metar_version"] == "M1"
    assert mismatch.metar_source_link.state is (
        linking.LinkState.HYDRATION_VERSION_MISMATCH
    )

    status_unproven = _status(source_metar_version="", metar_version="")
    unproven = _build(
        _tables(
            airport_records={"KSEA": status_unproven},
            metar_records={"KSEA": metar_m2},
            taf_records={"KSEA": _taf()},
        )
    )
    assert unproven.latest_metar is metar_m2
    assert unproven.metar_source_link.state is linking.LinkState.MISSING
    assert unproven.metar_source_link.state is not linking.LinkState.PRESENT


def test_latest_taf_present_absent_match_mismatch_and_unproven():
    taf_t1 = _taf()
    present = _build(
        _tables(
            airport_records={"KSEA": _status()},
            metar_records={"KSEA": _metar()},
            taf_records={"KSEA": taf_t1},
        )
    )
    assert present.latest_taf is taf_t1
    assert present.taf_source_link.state is linking.LinkState.PRESENT

    absent = _build(
        _tables(
            airport_records={"KSEA": _status()},
            metar_records={"KSEA": _metar()},
        )
    )
    assert absent.latest_taf is None
    assert absent.taf_source_link.state is linking.LinkState.HYDRATION_MISSING
    assert absent.latest_taf_periods == ()
    assert "no forecast" not in str(absent.taf_source_link.reason or "").lower()

    taf_t2 = _taf(taf_version_key="KSEA#T2", source_version="T2")
    mismatch = _build(
        _tables(
            airport_records={"KSEA": _status()},
            metar_records={"KSEA": _metar()},
            taf_records={"KSEA": taf_t2},
        )
    )
    assert mismatch.latest_taf is taf_t2
    assert mismatch.airport["source_taf_version"] == "T1"
    assert mismatch.taf_source_link.state is (
        linking.LinkState.HYDRATION_VERSION_MISMATCH
    )

    status_unproven = _status(
        source_taf_version="",
        taf_source_version="",
        taf_version="",
        taf_version_key="",
    )
    unproven = _build(
        _tables(
            airport_records={"KSEA": status_unproven},
            metar_records={"KSEA": _metar()},
            taf_records={"KSEA": taf_t2},
        )
    )
    assert unproven.latest_taf is taf_t2
    assert unproven.taf_source_link.state is linking.LinkState.MISSING
    assert unproven.taf_source_link.state is not linking.LinkState.PRESENT


def test_periods_use_latest_taf_version_key_and_drain_without_window():
    latest = _taf(taf_version_key="KSEA#T2", source_version="T2")
    first = _period("KSEA#T2", "p1")
    second = _period("KSEA#T2", "p2")
    tables = _tables(
        airport_records={"KSEA": _status(taf_version_key="KSEA#T1")},
        metar_records={"KSEA": _metar()},
        taf_records={"KSEA": latest},
        period_pages=[
            {"Items": [first], "LastEvaluatedKey": {"period_key": "p1"}},
            {"Items": [second]},
        ],
    )

    result = _build(tables)

    assert result.latest_taf is latest
    assert result.latest_taf_periods == (first, second)
    assert ("taf_version_key", "KSEA#T2") in (
        result.taf_periods_link.selected_identity
    )
    query_calls = [
        call for call in tables.taf_periods.calls if call[0] == "query"
    ]
    assert len(query_calls) == 2
    first_kwargs = query_calls[0][1]
    assert "IndexName" not in first_kwargs
    assert "Limit" not in first_kwargs
    assert "FilterExpression" not in first_kwargs
    assert first_kwargs["ScanIndexForward"] is True
    assert first_kwargs["ConsistentRead"] is True
    assert condition_shape(first_kwargs["KeyConditionExpression"]) == (
        "=",
        ("name", "taf_version_key"),
        "KSEA#T2",
    )
    assert "station_id" not in str(first_kwargs["KeyConditionExpression"])
    assert "period_from_epoch" not in str(first_kwargs["KeyConditionExpression"])
    assert query_calls[1][1]["ExclusiveStartKey"] == {"period_key": "p1"}
    period_obs = next(
        item
        for item in result.retrieval
        if item.source == "query_taf_period_rows_for_version"
    )
    assert period_obs.coverage is linking.Coverage.FULL_QUERY
    assert period_obs.consistency is linking.Consistency.CONSISTENT
    assert period_obs.limit is None
    assert linking.TAF_PERIOD_VERSION_LIMITATION in period_obs.limitations


def test_missing_latest_taf_or_version_key_does_not_query_orphan_periods():
    no_latest = _build(
        _tables(
            airport_records={"KSEA": _status(taf_version_key="KSEA#T1")},
            metar_records={"KSEA": _metar()},
            period_pages=[{"Items": [_period()]}],
        )
    )
    assert no_latest.latest_taf is None
    assert no_latest.latest_taf_periods == ()
    assert no_latest.taf_periods_link.state is linking.LinkState.MISSING

    latest_without_key = _taf(taf_version_key="", source_version="T1")
    tables = _tables(
        airport_records={"KSEA": _status()},
        metar_records={"KSEA": _metar()},
        taf_records={"KSEA": latest_without_key},
        period_pages=[{"Items": [_period()]}],
    )
    no_key = _build(tables)
    assert no_key.latest_taf is latest_without_key
    assert no_key.latest_taf_periods == ()
    assert no_key.taf_periods_link.state is linking.LinkState.MISSING
    assert tables.taf_periods.calls == []


def test_hydrated_station_identity_mismatch_is_not_used_as_weather():
    tables = _tables(
        airport_records={"KSEA": _status()},
        metar_records={"KSEA": _metar(station_id="KPDX")},
        taf_records={"KSEA": _taf(station_id="KPDX")},
        period_pages=[{"Items": [_period()]}],
    )

    result = _build(tables)

    assert result.latest_metar is None
    assert result.metar_source_link.state is (
        linking.LinkState.HYDRATION_IDENTITY_MISMATCH
    )
    assert result.latest_taf is None
    assert result.taf_source_link.state is (
        linking.LinkState.HYDRATION_IDENTITY_MISMATCH
    )
    assert result.latest_taf_periods == ()
    assert tables.taf_periods.calls == []


def test_building_materialization_is_preserved_exactly():
    taf = _taf(period_materialization_status="BUILDING")
    period = _period(period_materialization_status="BUILDING")
    tables = _tables(
        airport_records={"KSEA": _status()},
        metar_records={"KSEA": _metar()},
        taf_records={"KSEA": taf},
        period_pages=[{"Items": [period]}],
    )

    result = _build(tables)

    assert result.latest_taf["period_materialization_status"] == "BUILDING"
    assert result.latest_taf_periods[0]["period_materialization_status"] == (
        "BUILDING"
    )


def test_no_airport_assessment_fields_or_reads():
    tables = _tables(
        airport_records={"KSEA": _status()},
        metar_records={"KSEA": _metar()},
        taf_records={"KSEA": _taf()},
    )

    result = _build(tables)

    assert not hasattr(result, "recent_assessments")
    assert not hasattr(result, "current_assessment")
    assert not hasattr(result, "active_assessment")
    assert not hasattr(result, "metar")
    assert not hasattr(result, "taf")
    assert not hasattr(result, "taf_periods")
    assert tables.airport_assessments.calls == []


def test_root_weather_freshness_and_risk_fields_are_preserved_exactly():
    status = _status(
        metar_freshness_status="STALE",
        taf_freshness_status="UNKNOWN",
        weather_risk_level="HIGH",
        flight_category="IFR",
        has_metar=True,
        has_taf=False,
    )
    tables = _tables(airport_records={"KSEA": status})

    result = _build(tables)

    assert result.airport is status
    assert result.airport["metar_freshness_status"] == "STALE"
    assert result.airport["taf_freshness_status"] == "UNKNOWN"
    assert result.airport["weather_risk_level"] == "HIGH"
    assert result.airport["flight_category"] == "IFR"
    assert result.airport["has_metar"] is True
    assert result.airport["has_taf"] is False


def test_final_airport_status_drift_keeps_initial_root_and_does_not_rebuild():
    initial = _status(updated_at_epoch=NOW, source_metar_version="M1")
    final = _status(
        updated_at_epoch=NOW + 50,
        source_metar_version="M2",
        station_id="KPDX",
    )
    metar = _metar()
    tables = _tables(
        airport_gets=[initial, final],
        metar_records={"KSEA": metar},
        taf_records={"KSEA": _taf()},
    )

    result = _build(tables)

    assert result.airport is initial
    assert result.airport_is_current is True
    assert result.station_id == "KSEA"
    assert result.latest_metar is metar
    assert result.airport_status_link.state is (
        linking.LinkState.HYDRATION_VERSION_MISMATCH
    )
    assert tables.metar.calls[0][1]["Key"] == {"station_id": "KSEA"}
    assert len(tables.airports.calls) == 2


def test_same_version_final_read_does_not_claim_a_snapshot():
    status = _status()
    tables = _tables(
        airport_records={"KSEA": status},
        metar_records={"KSEA": _metar()},
        taf_records={"KSEA": _taf()},
    )

    result = _build(tables)

    assert result.airport_status_link.state is linking.LinkState.PRESENT
    assert any(
        linking.NO_SNAPSHOT_LIMITATION in item.limitations
        for item in result.retrieval
    )
    assert all(not hasattr(item, "complete") for item in result.retrieval)
    exact = [
        item
        for item in result.retrieval
        if item.source == "get_airport_status_record"
    ]
    assert exact[0].coverage is linking.Coverage.EXACT_PK
    assert exact[0].consistency is linking.Consistency.CONSISTENT
    assert exact[-1].coverage is linking.Coverage.EXACT_PK
    assert exact[-1].consistency is linking.Consistency.CONSISTENT
    metar_obs = next(
        item for item in result.retrieval if item.source == "get_metar_record"
    )
    taf_obs = next(
        item for item in result.retrieval if item.source == "get_taf_record"
    )
    assert metar_obs.coverage is linking.Coverage.EXACT_PK
    assert metar_obs.consistency is linking.Consistency.CONSISTENT
    assert taf_obs.coverage is linking.Coverage.EXACT_PK
    assert taf_obs.consistency is linking.Consistency.CONSISTENT


def test_one_now_epoch_and_no_forbidden_imports_cache_or_geography():
    captured = []
    original = current_set.is_current_airport_status

    def wrapped(item, now_epoch):
        captured.append(now_epoch)
        return original(item, now_epoch)

    tables = _tables(airport_records={"KSEA": _status()})
    current_set.is_current_airport_status = wrapped
    try:
        result = context.build_airport_operational_context(
            tables,
            "KSEA",
            now_epoch=NOW,
        )
    finally:
        current_set.is_current_airport_status = original

    assert result is not None
    assert captured == [NOW]
    source = inspect.getsource(context)
    builder = inspect.getsource(context.build_airport_operational_context)
    text = (PACKAGE_DIR / "context.py").read_text(encoding="utf-8")
    assert "time.time" not in source
    assert "datetime.now" not in source
    assert "datetime.utcnow" not in source
    assert "operational_api" not in text
    assert "wilvor_ai" not in text
    assert not hasattr(context, "_CACHE")
    assert "resolve_region" not in builder
    assert "search" not in builder
    assert "geometry" not in builder
    assert "airport_assessments" not in builder
