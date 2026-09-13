"""Strict parser tests for V1 historical Athena result rows."""

from __future__ import annotations

import pytest

from wilvor_historical.query_contracts import QUERY_RESULT_MALFORMED
from wilvor_historical_query.executor import AthenaQueryResult
from wilvor_historical_query.query_registry import InternalQueryId
from wilvor_historical_query.result_parsers import (
    ResultParseError,
    parse_encounter_summary,
    parse_hazard_version_summary,
    parse_list_encounters,
    parse_risk_summary,
)


WINDOW_START = "2026-09-11T10:00:00Z"
WINDOW_END = "2026-09-11T11:00:00Z"


def _athena(query_id: InternalQueryId, rows: tuple[dict[str, str | None], ...]) -> AthenaQueryResult:
    return AthenaQueryResult(
        query_id=query_id.value,
        query_execution_id="exec-1",
        workgroup="wilvor-historical",
        database="historical_facts",
        output_location="s3://bucket/athena-results/q",
        rows=rows,
        rows_returned=len(rows),
        data_scanned_bytes=8,
    )


def _encounter_row(
    *,
    physical: str | None = "2",
    encounters: str | None = "2",
    aircraft: str | None = "1",
    hazards: str | None = "1",
    dedups: str | None = "2",
    minimum: str | None = "2026-09-11T10:00:00Z",
    maximum: str | None = "2026-09-11T10:30:00Z",
) -> dict[str, str | None]:
    return {
        "physical_record_count": physical,
        "distinct_encounter_count": encounters,
        "distinct_aircraft_count": aircraft,
        "distinct_hazard_count": hazards,
        "distinct_dedup_count": dedups,
        "min_event_time_utc": minimum,
        "max_event_time_utc": maximum,
    }


def _risk_row(
    *,
    physical: str | None = "3",
    risks: str | None = "3",
    encounters: str | None = "2",
    aircraft: str | None = "1",
    minimum: str | None = "1.5",
    maximum: str | None = "9",
) -> dict[str, str | None]:
    return {
        "physical_record_count": physical,
        "distinct_risk_count": risks,
        "distinct_encounter_count": encounters,
        "distinct_aircraft_count": aircraft,
        "min_risk_score": minimum,
        "max_risk_score": maximum,
    }


def _hazard_row(
    *,
    physical: str | None = "2",
    hazards: str | None = "1",
    versions: str | None = "2",
    minimum: str | None = "2026-09-11T10:00:00Z",
    maximum: str | None = "2026-09-11T10:20:00Z",
) -> dict[str, str | None]:
    return {
        "physical_record_count": physical,
        "distinct_hazard_count": hazards,
        "distinct_hazard_version_count": versions,
        "min_materialized_at_utc": minimum,
        "max_materialized_at_utc": maximum,
    }


def _list_row(
    *,
    event_time: str = "2026-09-11T10:00:00Z",
    record_id: str = "proj-1#hazard-1#v1",
    dedup_id: str = "proj-1#hazard-1#v1|ENCOUNTER_OBSERVED|1700000000|DETECTED",
    encounter_id: str = "proj-1#hazard-1#v1",
) -> dict[str, str | None]:
    return {
        "encounter_id": encounter_id,
        "record_id": record_id,
        "dedup_id": dedup_id,
        "aircraft_id": "abc123",
        "hazard_id": "hazard-1",
        "hazard_version_key": "hazard-1#v1",
        "fact_kind": "ENCOUNTER_OBSERVED",
        "encounter_state": "DETECTED",
        "event_time_utc": event_time,
        "hazard_type": "CONVECTION",
    }


def _parse_encounter(row: dict[str, str | None], *, start: str = WINDOW_START, end: str = WINDOW_END):
    return parse_encounter_summary(
        _athena(InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS, (row,)),
        start_utc=start,
        end_utc=end,
    )


def _parse_hazard(row: dict[str, str | None], *, start: str = WINDOW_START, end: str = WINDOW_END):
    return parse_hazard_version_summary(
        _athena(InternalQueryId.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS, (row,)),
        start_utc=start,
        end_utc=end,
    )


def _parse_list(
    rows: tuple[dict[str, str | None], ...],
    *,
    limit: int = 10,
    start: str = WINDOW_START,
    end: str = WINDOW_END,
    aircraft_id: str | None = "abc123",
    hazard_id: str | None = None,
):
    return parse_list_encounters(
        _athena(InternalQueryId.LIST_HISTORICAL_ENCOUNTERS, rows),
        limit=limit,
        start_utc=start,
        end_utc=end,
        aircraft_id=aircraft_id,
        hazard_id=hazard_id,
    )


def test_encounter_nonzero_and_zero_invariants():
    parsed = _parse_encounter(_encounter_row())
    assert parsed.physical_record_count == 2
    assert parsed.semantic_match_count == 2
    zero = _parse_encounter(
        _encounter_row(
            physical="0",
            encounters="0",
            aircraft="0",
            hazards="0",
            dedups="0",
            minimum=None,
            maximum=None,
        )
    )
    assert zero.physical_record_count == 0


@pytest.mark.parametrize(
    "rows",
    [
        (),
        (_encounter_row(), _encounter_row()),
    ],
)
def test_encounter_requires_exactly_one_row(rows):
    with pytest.raises(ResultParseError, match="exactly one data row") as captured:
        parse_encounter_summary(
            _athena(InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS, rows),
            start_utc=WINDOW_START,
            end_utc=WINDOW_END,
        )
    assert captured.value.code == QUERY_RESULT_MALFORMED


@pytest.mark.parametrize(
    "row",
    [
        _encounter_row(physical="-1"),
        _encounter_row(physical="1.5"),
        _encounter_row(physical="true"),
        _encounter_row(physical=None),
        _encounter_row(encounters="3"),
        _encounter_row(physical="0", encounters="0", aircraft="0", hazards="0", dedups="0", minimum="2026-09-11T10:00:00Z", maximum=None),
        _encounter_row(minimum=None),
        _encounter_row(minimum="2026-09-11T11:00:00Z", maximum="2026-09-11T10:00:00Z"),
    ],
)
def test_encounter_malformed_values_fail(row):
    with pytest.raises(ResultParseError):
        _parse_encounter(row)


def test_risk_distribution_consistency_and_failures():
    aggregate = _athena(InternalQueryId.SUMMARIZE_HISTORICAL_RISKS, (_risk_row(),))
    distribution = _athena(
        InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL,
        (
            {"risk_level": "HIGH", "physical_record_count": "2"},
            {"risk_level": "LOW", "physical_record_count": "1"},
        ),
    )
    parsed = parse_risk_summary(aggregate, distribution)
    assert parsed.physical_record_count == 3
    assert [item.risk_level for item in parsed.risk_level_distribution] == ["HIGH", "LOW"]
    zero = parse_risk_summary(
        _athena(
            InternalQueryId.SUMMARIZE_HISTORICAL_RISKS,
            (_risk_row(physical="0", risks="0", encounters="0", aircraft="0", minimum=None, maximum=None),),
        ),
        _athena(InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL, ()),
    )
    assert zero.physical_record_count == 0
    with pytest.raises(ResultParseError, match="duplicate risk_level"):
        parse_risk_summary(
            aggregate,
            _athena(
                InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL,
                (
                    {"risk_level": "HIGH", "physical_record_count": "2"},
                    {"risk_level": "HIGH", "physical_record_count": "1"},
                ),
            ),
        )
    with pytest.raises(ResultParseError, match="does not match"):
        parse_risk_summary(
            aggregate,
            _athena(
                InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL,
                ({"risk_level": "HIGH", "physical_record_count": "1"},),
            ),
        )
    with pytest.raises(ResultParseError, match="empty distribution"):
        parse_risk_summary(
            _athena(
                InternalQueryId.SUMMARIZE_HISTORICAL_RISKS,
                (_risk_row(physical="0", risks="0", encounters="0", aircraft="0", minimum=None, maximum=None),),
            ),
            _athena(
                InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL,
                ({"risk_level": "HIGH", "physical_record_count": "1"},),
            ),
        )
    with pytest.raises(ResultParseError, match="greater than zero"):
        parse_risk_summary(
            aggregate,
            _athena(
                InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL,
                (
                    {"risk_level": "HIGH", "physical_record_count": "0"},
                    {"risk_level": "LOW", "physical_record_count": "3"},
                ),
            ),
        )
    with pytest.raises(ResultParseError, match="greater than maximum"):
        parse_risk_summary(
            _athena(
                InternalQueryId.SUMMARIZE_HISTORICAL_RISKS,
                (_risk_row(minimum="9", maximum="1"),),
            ),
            distribution,
        )
    with pytest.raises(ResultParseError, match="invalid min_risk_score"):
        parse_risk_summary(
            _athena(
                InternalQueryId.SUMMARIZE_HISTORICAL_RISKS,
                (_risk_row(minimum="NaN"),),
            ),
            distribution,
        )


def test_hazard_version_parser_invariants():
    parsed = _parse_hazard(_hazard_row())
    assert parsed.semantic_match_count == 2
    assert "valid_from" not in parsed.to_dict()
    assert "valid_to" not in parsed.to_dict()
    zero = _parse_hazard(
        _hazard_row(physical="0", hazards="0", versions="0", minimum=None, maximum=None)
    )
    assert zero.physical_record_count == 0
    with pytest.raises(ResultParseError):
        _parse_hazard(_hazard_row(hazards="3"))
    with pytest.raises(ResultParseError):
        _parse_hazard(
            _hazard_row(minimum="2026-09-11T11:00:00Z", maximum="2026-09-11T10:00:00Z")
        )


def test_list_truncation_and_order():
    first = _list_row()
    second = _list_row(
        event_time="2026-09-11T10:01:00Z",
        record_id="proj-1#hazard-1#v2",
        dedup_id="proj-1#hazard-1#v2|ENCOUNTER_OBSERVED|1700000060|DETECTED",
        encounter_id="proj-1#hazard-1#v2",
    )
    third = _list_row(
        event_time="2026-09-11T10:02:00Z",
        record_id="proj-1#hazard-1#v3",
        dedup_id="proj-1#hazard-1#v3|ENCOUNTER_OBSERVED|1700000120|DETECTED",
        encounter_id="proj-1#hazard-1#v3",
    )
    empty = _parse_list((), limit=2)
    assert empty.records == ()
    assert empty.truncated is False
    exact = _parse_list((first, second), limit=2)
    assert exact.semantic_match_count == 2
    assert exact.truncated is False
    truncated = _parse_list((first, second, third), limit=2)
    assert truncated.truncated is True
    assert truncated.semantic_match_count is None
    assert truncated.minimum_match_count == 3
    assert len(truncated.records) == 2
    with pytest.raises(ResultParseError, match="out of deterministic order"):
        _parse_list((second, first))
    later_record = _list_row(
        event_time="2026-09-11T10:00:00Z",
        record_id="proj-1#hazard-1#v0",
        dedup_id="proj-1#hazard-1#v0|ENCOUNTER_OBSERVED|1700000000|DETECTED",
        encounter_id="proj-1#hazard-1#v0",
    )
    with pytest.raises(ResultParseError, match="out of deterministic order"):
        _parse_list((first, later_record))
    same_ids_bad_dedup = _list_row(
        event_time="2026-09-11T10:00:00Z",
        record_id="proj-1#hazard-1#v1",
        dedup_id="proj-1#hazard-1#v1|ENCOUNTER_OBSERVED|1699999999|DETECTED",
    )
    with pytest.raises(ResultParseError, match="out of deterministic order"):
        _parse_list((first, same_ids_bad_dedup))
    with pytest.raises(ResultParseError):
        _parse_list(({**first, "aircraft_id": None},))


def test_encounter_window_integrity():
    _parse_encounter(_encounter_row())
    _parse_encounter(
        _encounter_row(
            physical="1",
            encounters="1",
            aircraft="1",
            hazards="1",
            dedups="1",
            minimum="2026-09-11T10:00:00.900000Z",
            maximum="2026-09-11T10:00:00.900000Z",
        )
    )
    with pytest.raises(ResultParseError, match="outside the requested window"):
        _parse_encounter(
            _encounter_row(minimum="2026-09-11T09:59:59Z", maximum="2026-09-11T10:30:00Z")
        )
    with pytest.raises(ResultParseError, match="outside the requested window"):
        _parse_encounter(
            _encounter_row(minimum="2026-09-11T10:00:00Z", maximum="2026-09-11T11:00:00Z")
        )


def test_hazard_window_integrity():
    _parse_hazard(_hazard_row())
    _parse_hazard(
        _hazard_row(
            physical="1",
            hazards="1",
            versions="1",
            minimum="2026-09-11T10:00:00.900000Z",
            maximum="2026-09-11T10:00:00.900000Z",
        )
    )
    with pytest.raises(ResultParseError, match="outside the requested window"):
        _parse_hazard(
            _hazard_row(minimum="2026-09-11T09:59:59Z", maximum="2026-09-11T10:20:00Z")
        )
    with pytest.raises(ResultParseError, match="outside the requested window"):
        _parse_hazard(
            _hazard_row(minimum="2026-09-11T10:00:00Z", maximum="2026-09-11T11:00:00Z")
        )


def test_list_window_and_selector_integrity():
    inside = _list_row(event_time="2026-09-11T10:00:00.900000Z")
    _parse_list((inside,))
    with pytest.raises(ResultParseError, match="outside the requested window"):
        _parse_list((_list_row(event_time="2026-09-11T09:59:59Z"),))
    with pytest.raises(ResultParseError, match="outside the requested window"):
        _parse_list((_list_row(event_time="2026-09-11T11:00:00Z"),))
    _parse_list((_list_row(),), aircraft_id="abc123")
    mismatched_aircraft = {**_list_row(), "aircraft_id": "zzz999"}
    with pytest.raises(ResultParseError, match="aircraft_id"):
        _parse_list((mismatched_aircraft,), aircraft_id="abc123")
    _parse_list((_list_row(),), aircraft_id=None, hazard_id="hazard-1")
    mismatched_hazard = {**_list_row(), "hazard_id": "hazard-2"}
    with pytest.raises(ResultParseError, match="hazard_id"):
        _parse_list((mismatched_hazard,), aircraft_id=None, hazard_id="hazard-1")
    _parse_list((_list_row(),), aircraft_id="abc123", hazard_id="hazard-1")
    with pytest.raises(ResultParseError, match="hazard_id"):
        _parse_list((mismatched_hazard,), aircraft_id="abc123", hazard_id="hazard-1")
