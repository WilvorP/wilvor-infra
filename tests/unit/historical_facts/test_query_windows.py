"""Pure historical query window and touched-date tests."""

from __future__ import annotations

import inspect

import pytest

from wilvor_historical.query_windows import (
    MAX_QUERY_WINDOW_DAYS,
    QUERY_WINDOW_PRECISION,
    QueryWindow,
    QueryWindowError,
    UtcCalendarDate,
    parse_query_window,
    touched_utc_dates,
)


def _dates(*iso_dates: str) -> tuple[str, ...]:
    return tuple(item.iso_date for item in touched_utc_dates(*iso_dates))


def test_valid_canonical_z_window_is_accepted():
    window = parse_query_window("2026-09-11T00:00:00Z", "2026-09-12T00:00:00Z")
    assert window.start_utc == "2026-09-11T00:00:00Z"
    assert window.end_utc == "2026-09-12T00:00:00Z"


def test_z_without_seconds_is_canonicalized():
    window = parse_query_window("2026-01-31T12:00Z", "2026-02-02T00:00Z")
    assert window.start_utc == "2026-01-31T12:00:00Z"
    assert window.end_utc == "2026-02-02T00:00:00Z"


def test_naive_timestamps_are_rejected():
    with pytest.raises(QueryWindowError, match="start_utc is naive"):
        parse_query_window("2026-01-31T12:00:00", "2026-02-01T00:00:00Z")
    with pytest.raises(QueryWindowError, match="end_utc is naive"):
        parse_query_window("2026-01-31T12:00:00Z", "2026-02-01T00:00:00")


def test_non_utc_offsets_are_rejected_not_converted():
    with pytest.raises(QueryWindowError, match="start_utc is not UTC"):
        parse_query_window("2026-01-31T12:00:00-05:00", "2026-02-01T00:00:00Z")
    with pytest.raises(QueryWindowError, match="canonical UTC Z"):
        parse_query_window("2026-01-31T12:00:00+00:00", "2026-02-01T00:00:00Z")


def test_start_equal_end_is_rejected():
    with pytest.raises(QueryWindowError, match="must precede"):
        parse_query_window("2026-09-11T00:00:00Z", "2026-09-11T00:00:00Z")


def test_start_after_end_is_rejected():
    with pytest.raises(QueryWindowError, match="must precede"):
        parse_query_window("2026-09-12T00:00:00Z", "2026-09-11T00:00:00Z")


def test_exactly_seven_days_is_accepted():
    window = parse_query_window("2026-01-01T00:00:00Z", "2026-01-08T00:00:00Z")
    assert (window.end_datetime() - window.start_datetime()).days == (
        MAX_QUERY_WINDOW_DAYS
    )


def test_more_than_seven_days_is_rejected():
    with pytest.raises(QueryWindowError, match="exceeds 7 days"):
        parse_query_window("2026-01-01T00:00:00Z", "2026-01-08T00:00:01Z")


def test_missing_window_bounds_are_rejected():
    with pytest.raises(QueryWindowError, match="missing start_utc"):
        parse_query_window("", "2026-09-12T00:00:00Z")
    with pytest.raises(QueryWindowError, match="missing end_utc"):
        parse_query_window("2026-09-11T00:00:00Z", "   ")


def test_same_day_window_touches_one_date():
    assert _dates("2026-09-11T00:00:00Z", "2026-09-11T23:59:59Z") == (
        "2026-09-11",
    )


def test_midnight_exclusive_end_does_not_include_end_date():
    assert _dates("2026-01-31T12:00Z", "2026-02-02T00:00Z") == (
        "2026-01-31",
        "2026-02-01",
    )
    assert _dates("2026-09-11T00:00:00Z", "2026-09-12T00:00:00Z") == (
        "2026-09-11",
    )


def test_one_second_after_midnight_includes_new_day():
    assert _dates("2026-01-31T12:00Z", "2026-02-02T00:00:01Z") == (
        "2026-01-31",
        "2026-02-01",
        "2026-02-02",
    )


def test_year_crossing_midnight_end_is_exclusive():
    assert _dates("2026-12-31T23:00Z", "2027-01-01T00:00Z") == ("2026-12-31",)


def test_year_crossing_after_midnight_includes_new_year():
    assert _dates("2026-12-31T23:00Z", "2027-01-01T00:00:01Z") == (
        "2026-12-31",
        "2027-01-01",
    )


def test_touched_dates_are_structured_not_sql():
    dates = touched_utc_dates("2026-01-31T12:00Z", "2026-02-02T00:00Z")
    assert dates == (
        UtcCalendarDate(year="2026", month="01", day="31"),
        UtcCalendarDate(year="2026", month="02", day="01"),
    )
    source = inspect.getsource(touched_utc_dates)
    assert "SELECT" not in source
    assert "year =" not in source
    assert "OR" not in source


def test_query_window_dataclass_validates():
    window = QueryWindow(
        start_utc="2026-09-11T00:00:00Z",
        end_utc="2026-09-12T00:00:00Z",
    )
    assert window.start_utc == "2026-09-11T00:00:00Z"
    assert QUERY_WINDOW_PRECISION == "second"
    assert window.end_epoch - window.start_epoch == 86400


def test_fractional_second_window_bounds_are_rejected():
    with pytest.raises(QueryWindowError, match="start_utc must be whole-second"):
        parse_query_window("2026-09-11T12:00:00.100000Z", "2026-09-11T13:00:00Z")
    with pytest.raises(QueryWindowError, match="end_utc must be whole-second"):
        parse_query_window("2026-09-11T12:00:00Z", "2026-09-11T12:00:00.900000Z")


def test_zero_fraction_is_canonicalized_to_whole_second():
    window = parse_query_window(
        "2026-09-11T12:00:00.000000Z",
        "2026-09-11T13:00:00.000000Z",
    )
    assert window.start_utc == "2026-09-11T12:00:00Z"
    assert window.end_utc == "2026-09-11T13:00:00Z"
