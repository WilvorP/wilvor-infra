from pathlib import Path

from faa_parser import parse_faa_directory

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_and_join_supported_airports() -> None:
    result = parse_faa_directory(FIXTURES, supported_airport_ids={"KSFO", "KOAK"})

    assert [(runway.airport_id, runway.physical_runway_id) for runway in result.runways] == [
        ("KOAK", "12/30"),
        ("KSFO", "01L/19R"),
        ("KSFO", "01R/19L"),
    ]
    sfo = next(runway for runway in result.runways if runway.physical_runway_id == "01L/19R")
    assert sfo.record_id == "RUNWAY#01L-19R"
    assert sfo.length_ft == 7650
    assert sfo.end_1.runway_end_id == "01L"
    assert sfo.end_2 is not None
    assert sfo.end_2.runway_end_id == "19R"
    assert len(sfo.source_record_hash or "") == 64


def test_source_record_hash_is_stable() -> None:
    first = parse_faa_directory(FIXTURES, supported_airport_ids={"KSFO"})
    second = parse_faa_directory(FIXTURES, supported_airport_ids={"KSFO"})

    assert [item.source_record_hash for item in first.runways] == [
        item.source_record_hash for item in second.runways
    ]
