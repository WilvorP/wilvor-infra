"""Temporal SQL must not use variable-width VARCHAR or epoch-only order."""

from __future__ import annotations

import inspect

import pytest

from wilvor_historical.query_contracts import (
    HistoricalQueryError,
    ListHistoricalEncountersRequest,
    SummarizeHistoricalEncountersRequest,
    SummarizeHistoricalHazardVersionsRequest,
)
from wilvor_historical.query_windows import QueryWindowError, parse_query_window
from wilvor_historical.time import (
    canonical_utc_order_key,
    epoch_from_utc_datetime,
    parse_utc_datetime,
)
from wilvor_historical_query import render_historical_operation
from wilvor_historical_query import query_sql
from wilvor_historical_query.query_sql import canonical_utc_order_key_sql


SAME_SECOND = (
    "2026-09-11T12:00:00Z",
    "2026-09-11T12:00:00.100000Z",
    "2026-09-11T12:00:00.900000Z",
)
ORDERED_TIMES = SAME_SECOND + ("2026-09-11T12:00:01Z",)
EVENT_TIME_KEY = canonical_utc_order_key_sql("event_time_utc")
MATERIALIZED_KEY = canonical_utc_order_key_sql("materialized_at_utc")


def _encounter_sql(start: str, end: str) -> str:
    return render_historical_operation(
        SummarizeHistoricalEncountersRequest(start_utc=start, end_utc=end)
    ).queries[0].sql


def _list_sql(start: str, end: str) -> str:
    return render_historical_operation(
        ListHistoricalEncountersRequest(
            start_utc=start,
            end_utc=end,
            aircraft_id="abc123",
        )
    ).queries[0].sql


def _stored_min(*times: str) -> str:
    return min(times, key=canonical_utc_order_key)


def _stored_max(*times: str) -> str:
    return max(times, key=canonical_utc_order_key)


def test_normalized_key_orders_same_second_then_next_second():
    keys = tuple(canonical_utc_order_key(item) for item in ORDERED_TIMES)
    assert keys == (
        "2026-09-11T12:00:00.000000Z",
        "2026-09-11T12:00:00.100000Z",
        "2026-09-11T12:00:00.900000Z",
        "2026-09-11T12:00:01.000000Z",
    )
    assert keys == tuple(sorted(keys))
    assert SAME_SECOND[1] < SAME_SECOND[0]
    assert parse_utc_datetime(SAME_SECOND[1]) > parse_utc_datetime(SAME_SECOND[0])


def test_summary_minmax_fractional_pair():
    early, late = SAME_SECOND[1], SAME_SECOND[2]
    assert _stored_min(early, late) == early
    assert _stored_max(early, late) == late
    sql = _encounter_sql("2026-09-11T12:00:00Z", "2026-09-11T12:00:01Z")
    assert f"min_by(event_time_utc, {EVENT_TIME_KEY})" in sql
    assert f"max_by(event_time_utc, {EVENT_TIME_KEY})" in sql
    assert "min_by(event_time_utc, event_time_epoch)" not in sql


def test_summary_minmax_whole_second_and_fraction():
    whole, frac = SAME_SECOND[0], SAME_SECOND[1]
    assert _stored_min(whole, frac) == whole
    assert _stored_max(whole, frac) == frac
    assert canonical_utc_order_key(whole) == "2026-09-11T12:00:00.000000Z"


def test_summary_minmax_identical_timestamps():
    stamp = SAME_SECOND[1]
    assert _stored_min(stamp, stamp) == stamp
    assert _stored_max(stamp, stamp) == stamp
    sql = _list_sql("2026-09-11T12:00:00Z", "2026-09-11T13:00:00Z")
    assert f"{EVENT_TIME_KEY} ASC" in sql
    assert "record_id ASC" in sql
    assert "dedup_id ASC" in sql


def test_where_still_uses_epoch_not_raw_varchar():
    sql = _encounter_sql("2026-09-11T12:00:00Z", "2026-09-11T12:00:01Z")
    window = parse_query_window("2026-09-11T12:00:00Z", "2026-09-11T12:00:01Z")
    assert f"event_time_epoch >= {window.start_epoch}" in sql
    assert f"event_time_epoch < {window.end_epoch}" in sql
    assert "event_time_utc >=" not in sql
    assert "event_time_utc <" not in sql
    epochs = [
        epoch_from_utc_datetime(parse_utc_datetime(item)) for item in SAME_SECOND
    ]
    assert epochs[0] == epochs[1] == epochs[2] == window.start_epoch


def test_minmax_and_list_do_not_order_solely_by_epoch():
    sql = _encounter_sql("2026-09-11T12:00:00Z", "2026-09-11T13:00:00Z")
    list_sql = _list_sql("2026-09-11T12:00:00Z", "2026-09-11T13:00:00Z")
    assert "min_by(event_time_utc, event_time_epoch)" not in sql
    assert "max_by(event_time_utc, event_time_epoch)" not in sql
    assert "ORDER BY\n    event_time_epoch ASC" not in list_sql
    assert f"ORDER BY\n    {EVENT_TIME_KEY} ASC,\n    record_id ASC,\n    dedup_id ASC" in list_sql
    assert "event_time_utc" in list_sql


def test_no_athena_temporal_parser_was_introduced():
    source = inspect.getsource(query_sql)
    assert "from_iso8601" not in source
    assert "date_parse" not in source
    assert "parse_datetime" not in source
    assert "CAST(" not in source
    assert " AS timestamp" not in source.lower()
    assert "from_unixtime" not in source
    assert "replace(" in source
    assert "LIKE '%.%Z'" in source


def test_fractional_window_bounds_remain_rejected():
    with pytest.raises(QueryWindowError, match="whole-second"):
        parse_query_window(SAME_SECOND[1], "2026-09-11T13:00:00Z")
    with pytest.raises(HistoricalQueryError, match="whole-second"):
        SummarizeHistoricalEncountersRequest(
            start_utc=SAME_SECOND[1],
            end_utc="2026-09-11T13:00:00Z",
        )
    with pytest.raises(HistoricalQueryError, match="whole-second"):
        ListHistoricalEncountersRequest(
            start_utc="2026-09-11T12:00:00Z",
            end_utc=SAME_SECOND[2],
            aircraft_id="abc123",
        )


def test_renderer_remains_deterministic_for_same_window():
    first = _encounter_sql("2026-09-11T12:00:00Z", "2026-09-11T13:00:00Z")
    second = _encounter_sql("2026-09-11T12:00:00Z", "2026-09-11T13:00:00Z")
    assert first == second


def test_hazard_version_minmax_uses_canonical_key():
    sql = render_historical_operation(
        SummarizeHistoricalHazardVersionsRequest(
            start_utc="2026-09-11T12:00:00Z",
            end_utc="2026-09-11T13:00:00Z",
        )
    ).queries[0].sql
    assert f"min_by(materialized_at_utc, {MATERIALIZED_KEY})" in sql
    assert f"max_by(materialized_at_utc, {MATERIALIZED_KEY})" in sql
    assert "min_by(materialized_at_utc, event_time_epoch)" not in sql
    assert "MIN(materialized_at_utc)" not in sql
    assert "valid_from_utc" not in sql
    assert "event_time_epoch >=" in sql
