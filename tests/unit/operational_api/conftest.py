import sys
from types import ModuleType

import pytest


TABLE_ENV = [
    "AIRCRAFT_CURRENT_STATE_TABLE_NAME",
    "AIRCRAFT_PROJECTION_TABLE_NAME",
    "AIRCRAFT_PROJECTION_POINTS_TABLE_NAME",
    "ACTIVE_HAZARDS_TABLE_NAME",
    "HAZARD_COORDINATES_TABLE_NAME",
    "AIRCRAFT_HAZARD_ENCOUNTER_TABLE_NAME",
    "RISK_RESULTS_TABLE_NAME",
    "AIRPORT_STATUS_TABLE_NAME",
    "METAR_LATEST_TABLE_NAME",
    "TAF_LATEST_TABLE_NAME",
    "TAF_FORECAST_PERIODS_TABLE_NAME",
    "AIRPORT_ASSESSMENT_TABLE_NAME",
    "RECOMMENDATIONS_TABLE_NAME",
    "ACTIVE_ALERTS_TABLE_NAME",
]


@pytest.fixture
def operational_repository(
    load_repo_module,
    monkeypatch,
):
    monkeypatch.setenv("NAME_PREFIX", "wilvor-test")
    for name in TABLE_ENV:
        monkeypatch.setenv(name, f"test-{name.lower()}")
    return load_repo_module(
        "unit_operational_repository",
        "functions/operational_api/repository.py",
    )


@pytest.fixture
def operational_app(
    load_repo_module,
    monkeypatch,
):
    fake = ModuleType("repository")
    fake.get_overview = lambda: {}
    fake.get_freshness = lambda: {}
    fake.get_system_health = lambda: {}
    fake.list_aircraft = lambda **_: {}
    fake.get_aircraft_detail = lambda _: None
    fake.list_active_hazards = lambda **_: {}
    fake.list_active_encounters = lambda **_: {}
    fake.list_airports = lambda **_: {}
    fake.get_airport_detail = lambda _: None
    fake.list_active_recommendations = lambda **_: {}
    fake.get_recommendation_detail = lambda _: None
    fake.list_active_alerts = lambda **_: {}
    fake.get_alert_detail = lambda _: None
    monkeypatch.setitem(sys.modules, "repository", fake)
    module = load_repo_module(
        "unit_operational_app",
        "functions/operational_api/app.py",
    )
    module.repository = fake
    return module
