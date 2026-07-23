"""Shared pytest configuration for the Wilvor test suite."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SHARED_CODE_DIR = REPO_ROOT / "functions" / "shared"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if str(SHARED_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_CODE_DIR))

# Prevent boto3 from trying the EC2 metadata service while unit-test modules
# create clients/resources at import time.
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-1")
os.environ.setdefault("AWS_REGION", "us-west-1")
os.environ.setdefault("ENVIRONMENT", "test")


@pytest.fixture
def load_repo_module() -> Callable[[str, str], ModuleType]:
    """Load a repository Python file under an isolated module name."""

    def _load(module_name: str, relative_path: str) -> ModuleType:
        module_path = REPO_ROOT / relative_path

        if not module_path.is_file():
            raise FileNotFoundError(f"Repository module not found: {module_path}")

        sys.modules.pop(module_name, None)

        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to load module from {module_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    return _load


@pytest.fixture
def valid_opensky_state_vector() -> list[Any]:
    """A valid OpenSky state vector using fresh timestamps."""

    now = int(time.time())

    return [
        "ABC123",          # icao24
        " UAL123  ",       # callsign
        "United States",   # origin_country
        now - 2,           # time_position
        now - 1,           # last_contact
        -122.375,          # longitude
        37.6189,           # latitude
        10_000.0,          # baro_altitude, metres
        False,             # on_ground
        230.0,             # velocity, m/s
        275.0,             # true_track
        2.5,               # vertical_rate, m/s
        None,              # sensors
        10_200.0,          # geo_altitude, metres
        "1200",            # squawk
        False,             # spi
        0,                 # position_source
    ]


@pytest.fixture
def raw_opensky_event(valid_opensky_state_vector: list[Any]) -> dict[str, Any]:
    return {
        "schema_version": "opensky_aircraft_raw.v1",
        "source": "opensky",
        "poll_id": "poll-test-001",
        "fetched_at_utc": "2026-07-18T12:00:00+00:00",
        "opensky_response_time": int(time.time()),
        "raw_index": 0,
        "raw_state_vector": valid_opensky_state_vector,
    }


@pytest.fixture
def clean_aircraft_record() -> dict[str, Any]:
    now = int(time.time())

    return {
        "icao24": "abc123",
        "callsign": "UAL123",
        "origin_country": "United States",
        "position_time_epoch": now - 2,
        "position_time_utc": "2026-07-18T11:59:58+00:00",
        "last_contact_epoch": now - 1,
        "last_contact_utc": "2026-07-18T11:59:59+00:00",
        "latitude": 37.6189,
        "longitude": -122.375,
        "has_position": True,
        "baro_altitude_m": 10_000.0,
        "geo_altitude_m": 10_200.0,
        "baro_altitude_ft": 32_808.4,
        "geo_altitude_ft": 33_464.568,
        "ground_speed_mps": 230.0,
        "ground_speed_kt": 447.0832,
        "track_deg": 275.0,
        "vertical_rate_mps": 2.5,
        "vertical_rate_fpm": 492.126,
        "on_ground": False,
        "squawk": "1200",
        "spi": False,
        "position_source": 0,
        "schema_version": "aircraft_current_state.v1",
        "ttl_epoch": now + 1800,
    }


@pytest.fixture
def kinesis_record_factory() -> Callable[[Any, str], dict[str, Any]]:
    def _make(payload: Any, sequence_number: str = "1001") -> dict[str, Any]:
        encoded = base64.b64encode(
            json.dumps(payload).encode("utf-8")
        ).decode("ascii")

        return {
            "eventSource": "aws:kinesis",
            "kinesis": {
                "sequenceNumber": sequence_number,
                "data": encoded,
            },
        }

    return _make