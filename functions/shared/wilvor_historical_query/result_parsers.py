"""Strict Athena row parsers for V1 historical analytics operations.

These parsers do not evaluate coverage, invent defaults, or sort rows to
hide executor-order violations.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from wilvor_historical.contracts import HistoricalFactError, as_json_number
from wilvor_historical.query_contracts import (
    QUERY_RESULT_MALFORMED,
    EncounterSummaryResult,
    HazardVersionSummaryResult,
    HistoricalEncounterRecord,
    HistoricalQueryError,
    ListEncountersResult,
    RiskLevelBucket,
    RiskSummaryResult,
)
from wilvor_historical.query_windows import parse_query_window, utc_instant_in_query_window
from wilvor_historical.time import HistoricalTimeError, canonical_utc_order_key

from .executor import AthenaQueryResult
from .query_registry import (
    ENCOUNTER_SUMMARY_OUTPUT_COLUMNS,
    HAZARD_VERSION_SUMMARY_OUTPUT_COLUMNS,
    LIST_ENCOUNTER_OUTPUT_COLUMNS,
    RISK_LEVEL_DISTRIBUTION_OUTPUT_COLUMNS,
    RISK_SUMMARY_OUTPUT_COLUMNS,
)


class ResultParseError(Exception):
    """Fail-closed Athena result integrity failure."""

    def __init__(self, message: str, *, code: str = QUERY_RESULT_MALFORMED) -> None:
        super().__init__(message)
        self.code = code


def _require_row(
    result: AthenaQueryResult,
    columns: Sequence[str],
) -> Mapping[str, str | None]:
    if result.rows_returned != 1 or len(result.rows) != 1:
        raise ResultParseError("aggregate query must return exactly one data row")
    return _require_columns(result.rows[0], columns)


def _require_columns(
    row: Mapping[str, str | None],
    columns: Sequence[str],
) -> Mapping[str, str | None]:
    if not isinstance(row, Mapping):
        raise ResultParseError("malformed Athena data row")
    for column in columns:
        if column not in row:
            raise ResultParseError("Athena row is missing a required column")
    return row


def _require_count(value: str | None, field_name: str) -> int:
    if value is None:
        raise ResultParseError(f"{field_name} is required")
    if not isinstance(value, str):
        raise ResultParseError(f"invalid {field_name}")
    text = value.strip()
    if not text or not text.isascii() or not text.isdigit():
        raise ResultParseError(f"invalid {field_name}")
    try:
        number = int(text, 10)
    except ValueError as exc:
        raise ResultParseError(f"invalid {field_name}") from exc
    if number < 0:
        raise ResultParseError(f"invalid {field_name}")
    return number


def _require_positive_count(value: str | None, field_name: str) -> int:
    number = _require_count(value, field_name)
    if number < 1:
        raise ResultParseError(f"{field_name} must be greater than zero")
    return number


def _optional_stored_utc(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ResultParseError(f"invalid {field_name}")
    text = value.strip()
    if not text.endswith("Z"):
        raise ResultParseError(f"{field_name} must be canonical UTC Z")
    try:
        canonical_utc_order_key(text)
    except HistoricalTimeError as exc:
        raise ResultParseError(f"invalid {field_name}") from exc
    return text


def _require_stored_utc(value: str | None, field_name: str) -> str:
    parsed = _optional_stored_utc(value, field_name)
    if parsed is None:
        raise ResultParseError(f"{field_name} is required")
    return parsed


def _require_ordered_utc(minimum: str, maximum: str) -> None:
    if canonical_utc_order_key(minimum) > canonical_utc_order_key(maximum):
        raise ResultParseError("minimum timestamp is after maximum timestamp")


def _require_in_requested_window(
    value: str,
    *,
    start_utc: str,
    end_utc: str,
    field_name: str,
) -> None:
    window = parse_query_window(start_utc, end_utc)
    if not utc_instant_in_query_window(value, window):
        raise ResultParseError(f"{field_name} is outside the requested window")


def _require_distinct_within_physical(
    *,
    physical_record_count: int,
    distinct_counts: Mapping[str, int],
) -> None:
    if physical_record_count == 0:
        if any(value != 0 for value in distinct_counts.values()):
            raise ResultParseError("zero physical count requires zero distinct counts")
        return
    for field_name, value in distinct_counts.items():
        if value > physical_record_count:
            raise ResultParseError(
                f"{field_name} exceeds physical_record_count"
            )


def _optional_score(value: str | None, field_name: str) -> int | float | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ResultParseError(f"invalid {field_name}")
    try:
        return as_json_number(value.strip(), field_name)
    except HistoricalFactError as exc:
        raise ResultParseError(f"invalid {field_name}") from exc


def _require_score(value: str | None, field_name: str) -> int | float:
    parsed = _optional_score(value, field_name)
    if parsed is None:
        raise ResultParseError(f"{field_name} is required")
    return parsed


def parse_encounter_summary(
    result: AthenaQueryResult,
    *,
    start_utc: str,
    end_utc: str,
) -> EncounterSummaryResult:
    row = _require_row(result, ENCOUNTER_SUMMARY_OUTPUT_COLUMNS)
    physical = _require_count(row["physical_record_count"], "physical_record_count")
    distinct = {
        "distinct_encounter_count": _require_count(
            row["distinct_encounter_count"], "distinct_encounter_count"
        ),
        "distinct_aircraft_count": _require_count(
            row["distinct_aircraft_count"], "distinct_aircraft_count"
        ),
        "distinct_hazard_count": _require_count(
            row["distinct_hazard_count"], "distinct_hazard_count"
        ),
        "distinct_dedup_count": _require_count(
            row["distinct_dedup_count"], "distinct_dedup_count"
        ),
    }
    _require_distinct_within_physical(
        physical_record_count=physical,
        distinct_counts=distinct,
    )
    minimum = _optional_stored_utc(row["min_event_time_utc"], "min_event_time_utc")
    maximum = _optional_stored_utc(row["max_event_time_utc"], "max_event_time_utc")
    if physical == 0:
        if minimum is not None or maximum is not None:
            raise ResultParseError("zero encounter count cannot carry event times")
    else:
        if minimum is None or maximum is None:
            raise ResultParseError("non-zero encounter count requires event times")
        _require_ordered_utc(minimum, maximum)
        _require_in_requested_window(
            minimum, start_utc=start_utc, end_utc=end_utc, field_name="min_event_time_utc"
        )
        _require_in_requested_window(
            maximum, start_utc=start_utc, end_utc=end_utc, field_name="max_event_time_utc"
        )
    try:
        return EncounterSummaryResult(
            physical_record_count=physical,
            distinct_encounter_count=distinct["distinct_encounter_count"],
            distinct_aircraft_count=distinct["distinct_aircraft_count"],
            distinct_hazard_count=distinct["distinct_hazard_count"],
            distinct_dedup_count=distinct["distinct_dedup_count"],
            min_event_time_utc=minimum,
            max_event_time_utc=maximum,
        )
    except HistoricalQueryError as exc:
        raise ResultParseError("encounter summary is malformed") from exc


def parse_risk_summary(
    aggregate: AthenaQueryResult,
    distribution: AthenaQueryResult,
) -> RiskSummaryResult:
    row = _require_row(aggregate, RISK_SUMMARY_OUTPUT_COLUMNS)
    physical = _require_count(row["physical_record_count"], "physical_record_count")
    distinct = {
        "distinct_risk_count": _require_count(
            row["distinct_risk_count"], "distinct_risk_count"
        ),
        "distinct_encounter_count": _require_count(
            row["distinct_encounter_count"], "distinct_encounter_count"
        ),
        "distinct_aircraft_count": _require_count(
            row["distinct_aircraft_count"], "distinct_aircraft_count"
        ),
    }
    _require_distinct_within_physical(
        physical_record_count=physical,
        distinct_counts=distinct,
    )
    minimum = _optional_score(row["min_risk_score"], "min_risk_score")
    maximum = _optional_score(row["max_risk_score"], "max_risk_score")
    if physical == 0:
        if minimum is not None or maximum is not None:
            raise ResultParseError("zero risk count cannot carry scores")
        buckets = _parse_risk_distribution(distribution, expected_total=0)
    else:
        if minimum is None or maximum is None:
            raise ResultParseError("non-zero risk count requires scores")
        if minimum > maximum:
            raise ResultParseError("minimum risk score is greater than maximum")
        buckets = _parse_risk_distribution(distribution, expected_total=physical)
    try:
        return RiskSummaryResult(
            physical_record_count=physical,
            distinct_risk_count=distinct["distinct_risk_count"],
            distinct_encounter_count=distinct["distinct_encounter_count"],
            distinct_aircraft_count=distinct["distinct_aircraft_count"],
            min_risk_score=minimum,
            max_risk_score=maximum,
            risk_level_distribution=buckets,
        )
    except HistoricalQueryError as exc:
        raise ResultParseError("risk summary is malformed") from exc


def _parse_risk_distribution(
    result: AthenaQueryResult,
    *,
    expected_total: int,
) -> tuple[RiskLevelBucket, ...]:
    if result.rows_returned != len(result.rows):
        raise ResultParseError("distribution rows_returned does not match data rows")
    if expected_total == 0:
        if result.rows_returned != 0 or result.rows:
            raise ResultParseError(
                "zero risk aggregate requires an empty distribution"
            )
        return ()
    buckets: list[RiskLevelBucket] = []
    seen: set[str] = set()
    total = 0
    for row in result.rows:
        mapped = _require_columns(row, RISK_LEVEL_DISTRIBUTION_OUTPUT_COLUMNS)
        level = mapped["risk_level"]
        if level is None or not isinstance(level, str) or not level.strip():
            raise ResultParseError("risk_level is required")
        count = _require_positive_count(
            mapped["physical_record_count"], "physical_record_count"
        )
        try:
            bucket = RiskLevelBucket(risk_level=level, physical_record_count=count)
        except HistoricalQueryError as exc:
            raise ResultParseError("risk_level is malformed") from exc
        if bucket.risk_level in seen:
            raise ResultParseError("duplicate risk_level in distribution")
        seen.add(bucket.risk_level)
        total += bucket.physical_record_count
        buckets.append(bucket)
    if total != expected_total:
        raise ResultParseError(
            "risk distribution count does not match aggregate physical_record_count"
        )
    return tuple(buckets)


def parse_hazard_version_summary(
    result: AthenaQueryResult,
    *,
    start_utc: str,
    end_utc: str,
) -> HazardVersionSummaryResult:
    row = _require_row(result, HAZARD_VERSION_SUMMARY_OUTPUT_COLUMNS)
    physical = _require_count(row["physical_record_count"], "physical_record_count")
    distinct = {
        "distinct_hazard_count": _require_count(
            row["distinct_hazard_count"], "distinct_hazard_count"
        ),
        "distinct_hazard_version_count": _require_count(
            row["distinct_hazard_version_count"],
            "distinct_hazard_version_count",
        ),
    }
    _require_distinct_within_physical(
        physical_record_count=physical,
        distinct_counts=distinct,
    )
    minimum = _optional_stored_utc(
        row["min_materialized_at_utc"], "min_materialized_at_utc"
    )
    maximum = _optional_stored_utc(
        row["max_materialized_at_utc"], "max_materialized_at_utc"
    )
    if physical == 0:
        if minimum is not None or maximum is not None:
            raise ResultParseError(
                "zero hazard-version count cannot carry materialized times"
            )
    else:
        if minimum is None or maximum is None:
            raise ResultParseError(
                "non-zero hazard-version count requires materialized times"
            )
        _require_ordered_utc(minimum, maximum)
        _require_in_requested_window(
            minimum,
            start_utc=start_utc,
            end_utc=end_utc,
            field_name="min_materialized_at_utc",
        )
        _require_in_requested_window(
            maximum,
            start_utc=start_utc,
            end_utc=end_utc,
            field_name="max_materialized_at_utc",
        )
    try:
        return HazardVersionSummaryResult(
            physical_record_count=physical,
            distinct_hazard_count=distinct["distinct_hazard_count"],
            distinct_hazard_version_count=distinct["distinct_hazard_version_count"],
            min_materialized_at_utc=minimum,
            max_materialized_at_utc=maximum,
        )
    except HistoricalQueryError as exc:
        raise ResultParseError("hazard-version summary is malformed") from exc


def parse_list_encounters(
    result: AthenaQueryResult,
    *,
    limit: int,
    start_utc: str,
    end_utc: str,
    aircraft_id: str | None = None,
    hazard_id: str | None = None,
) -> ListEncountersResult:
    if result.rows_returned != len(result.rows):
        raise ResultParseError("list rows_returned does not match data rows")
    if result.rows_returned > limit + 1:
        raise ResultParseError("list returned more rows than the executor bound")
    records: list[HistoricalEncounterRecord] = []
    previous: tuple[str, str, str] | None = None
    for row in result.rows:
        mapped = _require_columns(row, LIST_ENCOUNTER_OUTPUT_COLUMNS)
        try:
            record = HistoricalEncounterRecord(
                encounter_id=_require_text(mapped["encounter_id"], "encounter_id"),
                record_id=_require_text(mapped["record_id"], "record_id"),
                dedup_id=_require_text(mapped["dedup_id"], "dedup_id"),
                aircraft_id=_require_text(mapped["aircraft_id"], "aircraft_id"),
                hazard_id=_require_text(mapped["hazard_id"], "hazard_id"),
                hazard_version_key=_require_text(
                    mapped["hazard_version_key"], "hazard_version_key"
                ),
                fact_kind=_require_text(mapped["fact_kind"], "fact_kind"),
                encounter_state=_require_text(
                    mapped["encounter_state"], "encounter_state"
                ),
                event_time_utc=_require_stored_utc(
                    mapped["event_time_utc"], "event_time_utc"
                ),
                hazard_type=_require_text(mapped["hazard_type"], "hazard_type"),
            )
        except (HistoricalQueryError, ResultParseError) as exc:
            raise ResultParseError("historical encounter record is malformed") from exc
        key = (
            canonical_utc_order_key(record.event_time_utc),
            record.record_id,
            record.dedup_id,
        )
        if previous is not None and key < previous:
            raise ResultParseError("list rows are out of deterministic order")
        previous = key
        _require_in_requested_window(
            record.event_time_utc,
            start_utc=start_utc,
            end_utc=end_utc,
            field_name="event_time_utc",
        )
        if aircraft_id is not None and record.aircraft_id != aircraft_id:
            raise ResultParseError("list row aircraft_id does not match the request")
        if hazard_id is not None and record.hazard_id != hazard_id:
            raise ResultParseError("list row hazard_id does not match the request")
        records.append(record)
    if not records:
        return ListEncountersResult(records=(), truncated=False)
    if len(records) == limit + 1:
        return ListEncountersResult(records=tuple(records[:limit]), truncated=True)
    return ListEncountersResult(records=tuple(records), truncated=False)


def _require_text(value: str | None, field_name: str) -> str:
    if value is None or not isinstance(value, str) or not value.strip():
        raise ResultParseError(f"{field_name} is required")
    return value
