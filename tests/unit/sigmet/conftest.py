"""Fixtures used by the SIGMET unit-test suite."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from types import ModuleType
from typing import Any, Callable

import pytest


@pytest.fixture
def sigmet_feature() -> dict[str, Any]:
    """Representative NOAA GeoJSON SIGMET feature."""

    return {
        "type": "Feature",
        "properties": {
            "icaoId": "KZNY",
            "airSigmetType": "SIGMET",
            "alphaChar": "A",
            "seriesId": "12",
            "creationTime": "2026-07-18T12:00:00Z",
            "validTimeFrom": "2026-07-18T12:00:00Z",
            "validTimeTo": "2026-07-18T18:00:00Z",
            "hazard": "Turbulence",
            "severity": "SEV",
            "rawAirSigmet": "SIGMET A12 SEV TURB",
            "altitudeLow1": 180,
            "altitudeHi1": 400,
            "movementDir": 90,
            "movementSpd": 20,
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [-75.0, 40.0],
                    [-74.0, 40.0],
                    [-74.0, 41.0],
                    [-75.0, 41.0],
                    [-75.0, 40.0],
                ]
            ],
        },
    }


@pytest.fixture
def sigmet_raw_event(sigmet_feature: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "raw.noaa.airsigmet.v1",
        "source": "NOAA_AVIATION_WEATHER",
        "product_type": "SIGMET",
        "ingestion_type": "RAW_SIGMET_FEATURE",
        "poll_id": "poll-sigmet-001",
        "received_at": "2026-07-18T12:01:00+00:00",
        "raw_s3_bucket": "test-sigmet-archive",
        "raw_s3_key": (
            "raw/source=sigmet/year=2026/month=07/day=18/hour=12/"
            "sigmet-poll-sigmet-001.json.gz"
        ),
        "record_index": 0,
        "feature": sigmet_feature,
    }


@pytest.fixture
def sigmet_kinesis_record_factory() -> Callable[
    [Any, str, float], dict[str, Any]
]:
    def _make(
        payload: Any,
        sequence_number: str = "sigmet-seq-1",
        arrival_timestamp: float = 1_752_840_060.0,
    ) -> dict[str, Any]:
        encoded = base64.b64encode(
            json.dumps(payload).encode("utf-8")
        ).decode("ascii")

        return {
            "eventSource": "aws:kinesis",
            "kinesis": {
                "sequenceNumber": sequence_number,
                "approximateArrivalTimestamp": arrival_timestamp,
                "data": encoded,
            },
        }

    return _make


@pytest.fixture
def sigmet_poller(
    load_repo_module: Callable[[str, str], ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> ModuleType:
    monkeypatch.setenv(
        "NOAA_SIGMET_URL",
        "https://aviationweather.example.test/api/data/airsigmet",
    )
    monkeypatch.setenv(
        "ARCHIVE_BUCKET_NAME",
        "test-sigmet-archive",
    )
    monkeypatch.setenv(
        "RAW_PREFIX",
        "raw/source=sigmet",
    )
    monkeypatch.setenv(
        "SIGMET_RAW_STREAM_NAME",
        "test-sigmet-raw",
    )

    return load_repo_module(
        "unit_sigmet_poller_app",
        "functions/weather/sigmet/poller/app.py",
    )


@pytest.fixture
def sigmet_processor(
    load_repo_module: Callable[[str, str], ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> ModuleType:
    monkeypatch.setenv(
        "ACTIVE_HAZARDS_TABLE_NAME",
        "test-active-hazards",
    )
    monkeypatch.setenv(
        "HAZARD_CELLS_TABLE_NAME",
        "test-hazard-cells",
    )
    monkeypatch.setenv(
        "H3_RESOLUTION",
        "4",
    )
    monkeypatch.setenv(
        "SCHEMA_VERSION",
        "internal.sigmet.v1",
    )
    monkeypatch.setenv(
        "EVENT_BUS_NAME",
        "test-weather-events",
    )
    monkeypatch.setenv(
        "BAD_RECORDS_BUCKET_NAME",
        "test-sigmet-archive",
    )
    monkeypatch.setenv(
        "BAD_RECORDS_PREFIX",
        "bad-records/source=sigmet_processor",
    )

    return load_repo_module(
        "unit_sigmet_processor_app",
        "functions/weather/sigmet/processor/app.py",
    )


@pytest.fixture
def fixed_sigmet_time() -> datetime:
    return datetime(2026, 7, 18, 12, 30, tzinfo=timezone.utc)