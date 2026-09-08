"""UTC event-time helpers for historical facts.

Canonical fact time is always an explicit UTC domain timestamp supplied by
the caller. This module never reads the wall clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any


class HistoricalTimeError(ValueError):
    """Raised when a historical timestamp is missing or not UTC."""


def _as_epoch_int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        raise HistoricalTimeError("epoch is not an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        if value % 1 != 0:
            raise HistoricalTimeError("epoch is not an integer")
        return int(value)
    if isinstance(value, float):
        if not value.is_integer():
            raise HistoricalTimeError("epoch is not an integer")
        return int(value)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.lstrip("-").isdigit():
            return int(text)
    raise HistoricalTimeError("epoch is not an integer")


def parse_utc_datetime(value: Any) -> datetime:
    """Parse a UTC-aware timestamp. Naive and non-UTC offsets are rejected."""

    if not isinstance(value, str) or not value.strip():
        raise HistoricalTimeError("timestamp is not a UTC string")

    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HistoricalTimeError("timestamp is not a UTC string") from exc

    if parsed.tzinfo is None:
        raise HistoricalTimeError("timestamp is naive")

    offset = parsed.utcoffset()
    if offset != timedelta(0):
        raise HistoricalTimeError("timestamp is not UTC")

    return parsed.astimezone(timezone.utc)


def canonicalize_utc_z(value: datetime) -> str:
    if value.tzinfo is None:
        raise HistoricalTimeError("timestamp is naive")
    if value.utcoffset() != timedelta(0):
        raise HistoricalTimeError("timestamp is not UTC")
    utc = value.astimezone(timezone.utc)
    return utc.isoformat().replace("+00:00", "Z")


def epoch_from_utc_datetime(value: datetime) -> int:
    if value.tzinfo is None:
        raise HistoricalTimeError("timestamp is naive")
    if value.utcoffset() != timedelta(0):
        raise HistoricalTimeError("timestamp is not UTC")
    return int(value.timestamp())


def utc_from_epoch(epoch: Any) -> str:
    seconds = _as_epoch_int(epoch)
    return canonicalize_utc_z(
        datetime.fromtimestamp(seconds, tz=timezone.utc)
    )


def partition_date_utc(value: datetime) -> tuple[str, str, str]:
    if value.tzinfo is None:
        raise HistoricalTimeError("timestamp is naive")
    if value.utcoffset() != timedelta(0):
        raise HistoricalTimeError("timestamp is not UTC")
    utc = value.astimezone(timezone.utc)
    return (
        f"{utc.year:04d}",
        f"{utc.month:02d}",
        f"{utc.day:02d}",
    )


def resolve_event_time(
    *,
    event_time_utc: Any,
    event_time_epoch: Any = None,
) -> tuple[str, int, str, str, str]:
    """Return canonical Z time, epoch seconds, and UTC partition parts.

    When both UTC and epoch are supplied they must agree at whole-second
    precision. Partition fields are derived only from the UTC timestamp.
    """

    parsed = parse_utc_datetime(event_time_utc)
    derived_epoch = epoch_from_utc_datetime(parsed)
    if event_time_epoch is not None:
        supplied_epoch = _as_epoch_int(event_time_epoch)
        if supplied_epoch != derived_epoch:
            raise HistoricalTimeError(
                "event_time_utc and event_time_epoch do not match"
            )
        epoch = supplied_epoch
    else:
        epoch = derived_epoch

    year, month, day = partition_date_utc(parsed)
    return canonicalize_utc_z(parsed), epoch, year, month, day
