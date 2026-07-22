from __future__ import annotations

import sys
import zipfile
from pathlib import Path

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


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def faa_zip_path(
    tmp_path: Path,
) -> Path:
    fixtures = Path(__file__).parent / "fixtures"
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
                fixtures / filename,
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