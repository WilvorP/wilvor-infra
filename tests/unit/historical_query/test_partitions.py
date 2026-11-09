"""Exact partition-predicate rendering for half-open UTC windows."""

from __future__ import annotations

import pytest

from wilvor_historical.query_contracts import SummarizeHistoricalEncountersRequest
from wilvor_historical.query_windows import parse_query_window, touched_utc_dates
from wilvor_historical_query import HistoricalQueryRenderError, render_historical_operation
from wilvor_historical_query.query_sql import render_partition_predicate


def _sql(start_utc: str, end_utc: str) -> str:
    return render_historical_operation(
        SummarizeHistoricalEncountersRequest(
            start_utc=start_utc,
            end_utc=end_utc,
        )
    ).queries[0].sql


def _where(sql: str) -> str:
    return sql.split("WHERE\n", 1)[1]


def _epochs(start_utc: str, end_utc: str) -> tuple[int, int]:
    window = parse_query_window(start_utc, end_utc)
    return window.start_epoch, window.end_epoch


def test_same_day_is_one_exact_date_triple():
    start, end = "2026-09-11T00:00:00Z", "2026-09-11T23:59:59Z"
    sql = _sql(start, end)
    where = _where(sql)
    start_epoch, end_epoch = _epochs(start, end)
    assert "(year = '2026' AND month = '09' AND day = '11')" in where
    assert "day = '12'" not in where
    assert "year IN" not in sql
    assert "month IN" not in sql
    assert "day IN" not in sql
    assert f"event_time_epoch >= {start_epoch}" in sql
    assert f"event_time_epoch < {end_epoch}" in sql
    assert "event_time_utc >=" not in sql


def test_month_crossing_is_jan_31_and_feb_1_only():
    start, end = "2026-01-31T12:00:00Z", "2026-02-02T00:00:00Z"
    sql = _sql(start, end)
    assert touched_utc_dates(start, end)
    where = _where(sql)
    start_epoch, end_epoch = _epochs(start, end)
    assert (
        "(\n"
        "      (year = '2026' AND month = '01' AND day = '31')\n"
        "      OR\n"
        "      (year = '2026' AND month = '02' AND day = '01')\n"
        "    )"
    ) in where
    assert "day = '02'" not in where
    assert "month = '03'" not in where
    assert "year IN" not in sql
    assert "month IN" not in sql
    assert "day IN" not in sql
    assert f"event_time_epoch >= {start_epoch}" in sql
    assert f"event_time_epoch < {end_epoch}" in sql


def test_year_crossing_includes_jan_1_only_when_interval_touches_it():
    midnight = _sql("2026-12-31T23:00:00Z", "2027-01-01T00:00:00Z")
    assert "(year = '2026' AND month = '12' AND day = '31')" in midnight
    assert "year = '2027'" not in midnight
    assert "day = '01'" not in midnight
    after_midnight = _sql("2026-12-31T23:00:00Z", "2027-01-01T00:00:01Z")
    start_epoch, end_epoch = _epochs(
        "2026-12-31T23:00:00Z",
        "2027-01-01T00:00:01Z",
    )
    assert (
        "(\n"
        "      (year = '2026' AND month = '12' AND day = '31')\n"
        "      OR\n"
        "      (year = '2027' AND month = '01' AND day = '01')\n"
        "    )"
    ) in after_midnight
    assert f"event_time_epoch >= {start_epoch}" in after_midnight
    assert f"event_time_epoch < {end_epoch}" in after_midnight


def test_midnight_exclusive_end_omits_the_end_date():
    start, end = "2026-09-11T00:00:00Z", "2026-09-12T00:00:00Z"
    sql = _sql(start, end)
    _, end_epoch = _epochs(start, end)
    assert "(year = '2026' AND month = '09' AND day = '11')" in sql
    assert "day = '12'" not in sql
    assert f"event_time_epoch < {end_epoch}" in sql


def test_one_second_after_midnight_includes_the_new_date():
    start, end = "2026-01-31T12:00:00Z", "2026-02-02T00:00:01Z"
    sql = _sql(start, end)
    _, end_epoch = _epochs(start, end)
    assert "(year = '2026' AND month = '01' AND day = '31')" in sql
    assert "(year = '2026' AND month = '02' AND day = '01')" in sql
    assert "(year = '2026' AND month = '02' AND day = '02')" in sql
    assert f"event_time_epoch < {end_epoch}" in sql


def test_partition_helper_rejects_empty_dates():
    with pytest.raises(HistoricalQueryRenderError, match="touches no UTC dates"):
        render_partition_predicate(())


def test_half_open_event_time_is_always_present():
    sql = _sql("2026-09-11T00:00:00Z", "2026-09-12T00:00:00Z")
    assert "event_time_epoch >=" in sql
    assert "event_time_epoch <" in sql
    assert "event_time_utc >=" not in sql
    assert "event_time_utc <" not in sql
