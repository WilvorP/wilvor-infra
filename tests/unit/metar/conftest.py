"""Fixtures for METAR poller and processor unit tests."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from types import ModuleType
from typing import Any, Callable

import pytest


@pytest.fixture
def fixed_metar_time() -> datetime:
    return datetime(
        2026,
        7,
        18,
        12,
        30,
        tzinfo=timezone.utc,
    )


@pytest.fixture
def metar_feature() -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {
            "icaoId": "kjfk",
            "name": (
                "John F Kennedy International Airport"
            ),
            "obsTime": "2026-07-18T12:00:00Z",
            "receiptTime": "2026-07-18T12:01:00Z",
            "temp": 25.0,
            "dewp": 18.0,
            "wdir": 220,
            "wspd": 12,
            "wgst": 20,
            "visib": 10.0,
            "altim": 1013.2,
            "slp": 1012.8,
            "wxString": "-RA BR",
            "precip": 0.02,
            "fltCat": "MVFR",
            "metarType": "METAR",
            "elev": 4,
            "clouds": [
                {
                    "cover": "SCT",
                    "base": 2000,
                },
                {
                    "cover": "BKN",
                    "base": 4500,
                },
            ],
            "rawOb": (
                "KJFK 181200Z 22012G20KT "
                "10SM -RA BR SCT020 BKN045 "
                "25/18 A2992"
            ),
        },
        "geometry": {
            "type": "Point",
            "coordinates": [
                -73.7781,
                40.6413,
            ],
        },
    }


@pytest.fixture
def metar_candidate_context() -> dict[str, Any]:
    return {
        "station_id": "KJFK",
        "airport_id": "KJFK",
        "hazard_ids": [
            "hazard-001",
        ],
        "hazard_version_keys": [
            "hazard-001#v1",
        ],
    }


@pytest.fixture
def metar_raw_event(
    metar_feature: dict[str, Any],
    metar_candidate_context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "raw.noaa.metar.v2",
        "source": "NOAA_AVIATION_WEATHER",
        "product_type": "METAR",
        "ingestion_type": "RAW_METAR_FEATURE",
        "poll_id": "poll-metar-001",
        "correlation_id": "corr-metar-001",
        "received_at": (
            "2026-07-18T12:05:00+00:00"
        ),
        "raw_s3_bucket": (
            "test-weather-archive"
        ),
        "raw_s3_key": (
            "raw/source=metar/"
            "year=2026/month=07/day=18/"
            "hour=12/"
            "metar-poll-metar-001.json.gz"
        ),
        "record_index": 0,
        "request_source": (
            "HAZARD_STATIONS_READY"
        ),
        "trigger_hazard_version_key": (
            "hazard-001#v1"
        ),
        "candidate_context": (
            metar_candidate_context
        ),
        "feature": metar_feature,
    }


@pytest.fixture
def metar_kinesis_record_factory(
) -> Callable[[Any, str], dict[str, Any]]:
    def _make(
        payload: Any,
        sequence_number: str = "metar-seq-1",
    ) -> dict[str, Any]:
        encoded = base64.b64encode(
            json.dumps(
                payload
            ).encode("utf-8")
        ).decode("ascii")

        return {
            "eventSource": "aws:kinesis",
            "kinesis": {
                "sequenceNumber":
                    sequence_number,
                "data": encoded,
            },
        }

    return _make


@pytest.fixture
def metar_poller(
    load_repo_module: Callable[
        [str, str],
        ModuleType,
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> ModuleType:
    monkeypatch.setenv(
        "NOAA_METAR_URL",
        (
            "https://"
            "aviationweather.example.test/"
            "api/data/metar"
        ),
    )

    monkeypatch.setenv(
        "ARCHIVE_BUCKET_NAME",
        "test-weather-archive",
    )

    monkeypatch.setenv(
        "RAW_PREFIX",
        "raw/source=metar",
    )

    monkeypatch.setenv(
        "METAR_RAW_STREAM_NAME",
        "test-metar-raw",
    )

    monkeypatch.setenv(
        "HAZARD_STATION_CANDIDATES_TABLE_NAME",
        "test-hazard-station-candidates",
    )

    monkeypatch.setenv(
        "METAR_STATION_CHUNK_SIZE",
        "100",
    )

    return load_repo_module(
        "unit_metar_poller_app",
        "functions/weather/metar/poller/app.py",
    )


@pytest.fixture
def metar_processor(
    load_repo_module: Callable[
        [str, str],
        ModuleType,
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> ModuleType:
    monkeypatch.setenv(
        "METAR_LATEST_TABLE_NAME",
        "test-metar-latest",
    )

    monkeypatch.setenv(
        "BAD_RECORDS_BUCKET_NAME",
        "test-weather-archive",
    )

    monkeypatch.setenv(
        "BAD_RECORDS_PREFIX",
        (
            "bad-records/"
            "source=metar_processor"
        ),
    )

    monkeypatch.setenv(
        "SCHEMA_VERSION",
        "wilvor.metar_latest.v4.0",
    )

    monkeypatch.setenv(
        "EVENT_SCHEMA_VERSION",
        (
            "wilvor.event."
            "metar.updated.v1"
        ),
    )

    monkeypatch.setenv(
        "ENVIRONMENT",
        "test",
    )

    monkeypatch.setenv(
        "EVENT_BUS_NAME",
        "test-weather-events",
    )

    monkeypatch.setenv(
        "METAR_FRESH_SECONDS",
        "600",
    )

    monkeypatch.setenv(
        "METAR_ACCEPTABLE_SECONDS",
        "1800",
    )

    monkeypatch.setenv(
        "METAR_TTL_SECONDS",
        "86400",
    )

    return load_repo_module(
        "unit_metar_processor_app",
        "functions/weather/metar/processor/app.py",
    )