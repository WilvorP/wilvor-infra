from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from textwrap import dedent

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]

LOADER_DIR = (
    REPO_ROOT
    / "functions"
    / "runway_metadata"
    / "loader"
)

SHARED_DIR = (
    REPO_ROOT
    / "functions"
    / "shared"
)

sys.path.insert(0, str(LOADER_DIR))
sys.path.insert(0, str(SHARED_DIR))


TEST_CSV_CONTENT = {
    "APT_BASE.csv": """
        SITE_NO,ARPT_ID,ICAO_ID,ARPT_NAME,ARPT_STATUS
        00001.1*A,SFO,KSFO,SAN FRANCISCO INTL,O
        00002.1*A,OAK,KOAK,METROPOLITAN OAKLAND INTL,O
        00003.1*A,SJC,KSJC,NORMAN Y MINETA SAN JOSE INTL,O
        00004.1*A,ZZZ,,NO ICAO TEST AIRPORT,O
    """,
    "APT_RWY.csv": """
        SITE_NO,ARPT_ID,RWY_ID,RWY_LEN,RWY_WIDTH,SURFACE_TYPE_CODE,COND,RWY_LGT_CODE
        00001.1*A,SFO,01L/19R,7650,200,ASPH-CONC,G,HIGH
        00001.1*A,SFO,01R/19L,8650,200,ASPH-CONC,G,HIGH
        00002.1*A,OAK,12/30,10520,150,ASPH,G,HIGH
        00003.1*A,SJC,12L/30R,11000,150,CONC,G,HIGH
        00004.1*A,ZZZ,09/27,4000,75,ASPH,F,MED
    """,
    "APT_RWY_END.csv": """
        SITE_NO,ARPT_ID,RWY_ID,RWY_END_ID,TRUE_ALIGNMENT,LAT_DECIMAL,LONG_DECIMAL,RWY_END_ELEV,LNDG_DIST_AVBL,TKOF_RUN_AVBL,TKOF_DIST_AVBL,ACLT_STOP_DIST_AVBL
        00001.1*A,SFO,01L/19R,01L,14.2,37.606,-122.390,10,7650,7650,7650,7650
        00001.1*A,SFO,01L/19R,19R,194.2,37.626,-122.366,12,7650,7650,7650,7650
        00001.1*A,SFO,01R/19L,01R,14.2,37.607,-122.389,10,8650,8650,8650,8650
        00001.1*A,SFO,01R/19L,19L,194.2,37.628,-122.365,12,8650,8650,8650,8650
        00002.1*A,OAK,12/30,12,118.5,37.718,-122.229,7,10000,10520,10520,10520
        00002.1*A,OAK,12/30,30,298.5,37.697,-122.187,5,10520,10520,10520,10520
        00003.1*A,SJC,12L/30R,12L,121.0,37.374,-121.929,58,11000,11000,11000,11000
        00003.1*A,SJC,12L/30R,30R,301.0,37.348,-121.901,50,11000,11000,11000,11000
        00004.1*A,ZZZ,09/27,09,90.0,35.0,-100.0,100,4000,4000,4000,4000
        00004.1*A,ZZZ,09/27,27,270.0,35.0,-99.98,100,4000,4000,4000,4000
    """,
}


def write_test_csv_files(
    destination: Path,
) -> Path:
    destination.mkdir(
        parents=True,
        exist_ok=True,
    )

    for filename, content in TEST_CSV_CONTENT.items():
        normalized_content = (
            dedent(content)
            .strip()
            + "\n"
        )

        destination.joinpath(filename).write_text(
            normalized_content,
            encoding="utf-8",
        )

    return destination


@pytest.fixture
def fixtures_dir(
    tmp_path: Path,
) -> Path:
    """
    Generate controlled FAA-like CSV inputs for each test.

    pytest removes the temporary directory after the test run.
    """

    return write_test_csv_files(
        tmp_path / "faa-fixtures"
    )


@pytest.fixture
def faa_zip_path(
    fixtures_dir: Path,
    tmp_path: Path,
) -> Path:
    zip_path = tmp_path / "faa-test.zip"

    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for filename in (
            "APT_BASE.csv",
            "APT_RWY.csv",
            "APT_RWY_END.csv",
        ):
            archive.write(
                fixtures_dir / filename,
                arcname=filename,
            )

    return zip_path


@pytest.fixture
def faa_zip_with_invalid_supported_runway(
    fixtures_dir: Path,
    tmp_path: Path,
) -> Path:
    zip_path = (
        tmp_path
        / "faa-test-with-invalid-runway.zip"
    )

    runway_text = (
        fixtures_dir
        .joinpath("APT_RWY.csv")
        .read_text(encoding="utf-8")
        .rstrip()
    )

    runway_text += (
        "\n"
        "00001.1*A,"
        "SFO,"
        "10/28,"
        "NOT-A-NUMBER,"
        "150,"
        "ASPH,"
        "G,"
        "HIGH"
        "\n"
    )

    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.write(
            fixtures_dir / "APT_BASE.csv",
            arcname="APT_BASE.csv",
        )

        archive.writestr(
            "APT_RWY.csv",
            runway_text,
        )

        archive.write(
            fixtures_dir / "APT_RWY_END.csv",
            arcname="APT_RWY_END.csv",
        )

    return zip_path