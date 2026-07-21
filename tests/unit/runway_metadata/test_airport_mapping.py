from pathlib import Path

from faa_parser import build_airport_lookup, read_csv_file

FIXTURES = Path(__file__).parent / "fixtures"


def test_build_airport_lookup_uses_faa_to_icao_mapping() -> None:
    lookup, rejected = build_airport_lookup(read_csv_file(FIXTURES / "APT_BASE.csv"))

    assert rejected == []
    assert lookup["SFO"].icao_id == "KSFO"
    assert lookup["OAK"].icao_id == "KOAK"
    assert "ZZZ" not in lookup
