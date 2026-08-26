from __future__ import annotations

from datetime import datetime, timezone
from types import ModuleType
from typing import Callable

import pytest


@pytest.fixture
def fixed_airport_status_time() -> datetime:
    return datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def airport_status_materializer(
    load_repo_module: Callable[[str, str], ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> ModuleType:
    monkeypatch.setenv("AIRPORT_STATUS_TABLE_NAME", "test-airport-status")
    monkeypatch.setenv("STATION_REFERENCE_TABLE_NAME", "test-station-reference")
    monkeypatch.setenv("METAR_LATEST_TABLE_NAME", "test-metar-latest")
    monkeypatch.setenv("TAF_LATEST_TABLE_NAME", "test-taf-latest")
    monkeypatch.setenv("SCHEMA_VERSION", "airport_status.v1")
    monkeypatch.setenv("AIRPORT_STATUS_TTL_SECONDS", "86400")
    monkeypatch.setenv("METAR_FRESH_SECONDS", "1800")
    monkeypatch.setenv("TAF_FRESH_SECONDS", "21600")

    return load_repo_module(
        "unit_airport_status_materializer_app",
        "functions/airport_status/materializer/app.py",
    )