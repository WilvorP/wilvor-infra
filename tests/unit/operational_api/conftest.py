from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Callable

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
OPERATIONAL_API_DIR = REPO_ROOT / "functions" / "operational_api"

# app.py uses a bare `import repository`, so its own directory has to be
# importable before the module can be loaded.
if str(OPERATIONAL_API_DIR) not in sys.path:
    sys.path.insert(0, str(OPERATIONAL_API_DIR))


# repository.py resolves every table handle at import time, so all of these
# must be present before the module is executed.
OPERATIONAL_API_ENV = {
    "NAME_PREFIX": "wilvor-test",
    "AIRCRAFT_CURRENT_STATE_TABLE_NAME": "test-aircraft-current-state",
    "AIRCRAFT_PROJECTION_TABLE_NAME": "test-aircraft-projection",
    "AIRCRAFT_PROJECTION_POINTS_TABLE_NAME": "test-projection-points",
    "ACTIVE_HAZARDS_TABLE_NAME": "test-active-hazards",
    "HAZARD_COORDINATES_TABLE_NAME": "test-hazard-coordinates",
    "AIRCRAFT_HAZARD_ENCOUNTER_TABLE_NAME": "test-encounters",
    "RISK_RESULTS_TABLE_NAME": "test-risk-results",
    "AIRPORT_STATUS_TABLE_NAME": "test-airport-status",
    "METAR_LATEST_TABLE_NAME": "test-metar-latest",
    "TAF_LATEST_TABLE_NAME": "test-taf-latest",
    "TAF_FORECAST_PERIODS_TABLE_NAME": "test-taf-periods",
    "AIRPORT_ASSESSMENT_TABLE_NAME": "test-airport-assessment",
    "RECOMMENDATIONS_TABLE_NAME": "test-recommendations",
    "ACTIVE_ALERTS_TABLE_NAME": "test-active-alerts",
}


@pytest.fixture
def operational_api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in OPERATIONAL_API_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def operational_repository(
    operational_api_env: None,
    load_repo_module: Callable[[str, str], ModuleType],
) -> ModuleType:
    module = load_repo_module(
        "unit_operational_api_repository",
        "functions/operational_api/repository.py",
    )

    # The response cache is module state and would otherwise leak between
    # tests, making cache assertions order-dependent.
    module._CACHE.clear()

    return module


@pytest.fixture
def operational_app(
    operational_api_env: None,
    load_repo_module: Callable[[str, str], ModuleType],
) -> ModuleType:
    module = load_repo_module(
        "unit_operational_api_app",
        "functions/operational_api/app.py",
    )

    module.repository._CACHE.clear()

    return module
