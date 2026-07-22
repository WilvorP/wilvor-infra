from pathlib import Path

from faa_parser import (
    parse_faa_rows,
    read_csv_file,
)


def test_one_invalid_runway_does_not_stop_valid_runways(
    fixtures_dir: Path,
) -> None:
    airport_rows = read_csv_file(
        fixtures_dir / "APT_BASE.csv"
    )

    runway_rows = read_csv_file(
        fixtures_dir / "APT_RWY.csv"
    )

    runway_end_rows = read_csv_file(
        fixtures_dir / "APT_RWY_END.csv"
    )

    runway_rows.append(
        {
            "SITE_NO": "00001.1*A",
            "ARPT_ID": "SFO",
            "RWY_ID": "10/28",
            "RWY_LEN": "NOT-A-NUMBER",
            "RWY_WIDTH": "150",
        }
    )

    result = parse_faa_rows(
        airport_rows=airport_rows,
        runway_rows=runway_rows,
        runway_end_rows=runway_end_rows,
        supported_airport_ids={"KSFO"},
    )

    assert len(result.runways) == 2

    assert any(
        "RWY_LEN must be numeric"
        in record.reason
        for record in result.rejected_records
    )