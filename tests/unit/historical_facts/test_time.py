"""UTC event-time and partition derivation tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wilvor_historical.time import (
    HistoricalTimeError,
    canonicalize_utc_z,
    parse_utc_datetime,
    partition_date_utc,
    resolve_event_time,
)


def test_z_and_offset_zero_are_accepted_and_canonicalized():
    utc, epoch, year, month, day = resolve_event_time(
        event_time_utc="2023-11-14T22:13:20+00:00",
        event_time_epoch=1_700_000_000,
    )
    assert utc == "2023-11-14T22:13:20Z"
    assert epoch == 1_700_000_000
    assert (year, month, day) == ("2023", "11", "14")
    assert canonicalize_utc_z(parse_utc_datetime("2023-11-14T22:13:20Z")) == utc


def test_partition_uses_utc_date_not_local_interpretation():
    utc, epoch, year, month, day = resolve_event_time(
        event_time_utc="2023-11-15T00:00:00Z",
    )
    assert utc == "2023-11-15T00:00:00Z"
    assert (year, month, day) == ("2023", "11", "15")
    late = resolve_event_time(event_time_utc="2023-11-14T23:59:59Z")
    assert late[2:] == ("2023", "11", "14")
    assert late[1] != epoch


def test_leap_day_partition():
    _, _, year, month, day = resolve_event_time(
        event_time_utc="2024-02-29T00:00:00Z",
    )
    assert (year, month, day) == ("2024", "02", "29")


def test_naive_and_non_utc_are_rejected():
    with pytest.raises(HistoricalTimeError, match="naive"):
        parse_utc_datetime("2023-11-14T22:13:20")
    with pytest.raises(HistoricalTimeError, match="not UTC"):
        parse_utc_datetime("2023-11-14T22:13:20-05:00")


def test_mismatched_epoch_is_rejected():
    with pytest.raises(HistoricalTimeError, match="do not match"):
        resolve_event_time(
            event_time_utc="2023-11-14T22:13:20Z",
            event_time_epoch=1,
        )


def test_partition_helper_rejects_naive_datetime():
    with pytest.raises(HistoricalTimeError, match="naive"):
        partition_date_utc(datetime(2023, 11, 14, 22, 13, 20))
    year, month, day = partition_date_utc(
        datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
    )
    assert (year, month, day) == ("2023", "11", "14")
