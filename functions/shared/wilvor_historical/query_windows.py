"""Pure UTC window rules for historical analytics requests.

This module never reads the wall clock and never generates SQL. Later
Athena partition predicates consume the structured date tuples only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .contracts import HistoricalFactError
from .time import (
    HistoricalTimeError,
    canonicalize_utc_z,
    epoch_from_utc_datetime,
    parse_utc_datetime,
    partition_date_utc,
)


MAX_QUERY_WINDOW_DAYS = 7
MAX_QUERY_WINDOW = timedelta(days=MAX_QUERY_WINDOW_DAYS)

# Query bounds must be exactly representable by persisted event_time_epoch
# (integer UTC seconds). Stored event_time_utc may still carry microseconds
# via datetime.isoformat(); those facts share one epoch second.
QUERY_WINDOW_PRECISION = "second"


class QueryWindowError(HistoricalFactError):
    """Raised when a historical query window is structurally invalid."""


@dataclass(frozen=True)
class UtcCalendarDate:
    """One UTC calendar date touched by a half-open query window."""

    year: str
    month: str
    day: str

    def __post_init__(self) -> None:
        if (
            len(self.year) != 4
            or len(self.month) != 2
            or len(self.day) != 2
            or not self.year.isdigit()
            or not self.month.isdigit()
            or not self.day.isdigit()
        ):
            raise QueryWindowError("invalid UTC calendar date")
        month = int(self.month)
        day = int(self.day)
        if month < 1 or month > 12 or day < 1 or day > 31:
            raise QueryWindowError("invalid UTC calendar date")

    @property
    def iso_date(self) -> str:
        return f"{self.year}-{self.month}-{self.day}"


@dataclass(frozen=True)
class QueryWindow:
    """Canonical half-open UTC interval ``[start_utc, end_utc)``."""

    start_utc: str
    end_utc: str

    def __post_init__(self) -> None:
        start = _require_query_utc(self.start_utc, "start_utc")
        end = _require_query_utc(self.end_utc, "end_utc")
        if start.microsecond != 0:
            raise QueryWindowError("start_utc must be whole-second UTC")
        if end.microsecond != 0:
            raise QueryWindowError("end_utc must be whole-second UTC")
        if start >= end:
            raise QueryWindowError("start_utc must precede end_utc")
        if end - start > MAX_QUERY_WINDOW:
            raise QueryWindowError(
                f"query window exceeds {MAX_QUERY_WINDOW_DAYS} days"
            )
        object.__setattr__(self, "start_utc", canonicalize_utc_z(start))
        object.__setattr__(self, "end_utc", canonicalize_utc_z(end))

    def start_datetime(self) -> datetime:
        return parse_utc_datetime(self.start_utc)

    def end_datetime(self) -> datetime:
        return parse_utc_datetime(self.end_utc)

    @property
    def start_epoch(self) -> int:
        return epoch_from_utc_datetime(self.start_datetime())

    @property
    def end_epoch(self) -> int:
        return epoch_from_utc_datetime(self.end_datetime())


def _require_query_utc(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise QueryWindowError(f"missing {field_name}")
    text = value.strip()
    if not text.endswith("Z"):
        try:
            parse_utc_datetime(text)
        except HistoricalTimeError as exc:
            message = str(exc)
            if "naive" in message:
                raise QueryWindowError(f"{field_name} is naive") from exc
            if "not UTC" in message:
                raise QueryWindowError(f"{field_name} is not UTC") from exc
            raise QueryWindowError(f"invalid {field_name}") from exc
        raise QueryWindowError(f"{field_name} must be canonical UTC Z")
    try:
        return parse_utc_datetime(text)
    except HistoricalTimeError as exc:
        raise QueryWindowError(f"invalid {field_name}") from exc


def parse_query_window(start_utc: Any, end_utc: Any) -> QueryWindow:
    """Validate and canonicalize a caller-supplied historical window."""

    return QueryWindow(start_utc=start_utc, end_utc=end_utc)


def touched_utc_dates(start_utc: Any, end_utc: Any) -> tuple[UtcCalendarDate, ...]:
    """Return exact UTC calendar dates touched by ``[start_utc, end_utc)``.

    Midnight ``end_utc`` is exclusive. This helper returns structured dates
    only and does not render SQL.
    """

    window = parse_query_window(start_utc, end_utc)
    first = window.start_datetime().date()
    last = (window.end_datetime() - timedelta(microseconds=1)).date()
    dates: list[UtcCalendarDate] = []
    cursor = first
    while cursor <= last:
        year, month, day = partition_date_utc(
            datetime(
                cursor.year,
                cursor.month,
                cursor.day,
                tzinfo=window.start_datetime().tzinfo,
            )
        )
        dates.append(UtcCalendarDate(year=year, month=month, day=day))
        cursor += timedelta(days=1)
    return tuple(dates)
