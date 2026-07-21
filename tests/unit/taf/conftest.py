"""Fixtures for TAF poller and processor unit tests."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from types import ModuleType
from typing import Any, Callable

import pytest


@pytest.fixture
def fixed_taf_time() -> datetime:
    return datetime(2026, 7, 18, 12, 30, tzinfo=timezone.utc)


@pytest.fixture
def taf_record() -> dict[str, Any]:
    return {
        "icaoId": "kjfk",
        "name": "John F Kennedy International Airport",
        "issueTime": "2026-07-18T11:30:00Z",
        "bulletinTime": "2026-07-18T11:25:00Z",
        "validTimeFrom": "2026-07-18T12:00:00Z",
        "validTimeTo": "2026-07-19T18:00:00Z",
        "mostRecent": True,
        "remarks": "AMD NOT SKED",
        "lat": 40.6413,
        "lon": -73.7781,
        "elev": 4,
        "rawTAF": (
            "TAF KJFK 181130Z 1812/1918 VRB05KT P6SM "
            "SCT020 BKN050"
        ),
        "fcsts": [
            {
                "timeFrom": "2026-07-18T12:00:00Z",
                "timeTo": "2026-07-18T18:00:00Z",
                "wdir": "VRB",
                "wspd": "5",
                "visib": "6+",
                "wxString": "-RA BR",
                "clouds": [
                    {"cover": "SCT", "base": 2000},
                    {"cover": "BKN", "base": 5000},
                ],
            },
            {
                "timeFrom": "2026-07-18T18:00:00Z",
                "timeTo": "2026-07-19T00:00:00Z",
                "fcstChange": "TEMPO",
                "probability": 30,
                "wdir": 220,
                "wspd": 12,
                "wgst": 20,
                "visib": 3,
                "clouds": [
                    {"cover": "OVC", "base": 1200},
                ],
                "notDecoded": "TEST TOKEN",
            },
        ],
    }


@pytest.fixture
def taf_raw_event(taf_record: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "raw.noaa.taf.v1",
        "source": "NOAA_AVIATION_WEATHER",
        "product_type": "TAF",
        "ingestion_type": "RAW_TAF_RECORD",
        "poll_id": "poll-taf-001",
        "received_at": "2026-07-18T12:05:00+00:00",
        "raw_s3_bucket": "test-weather-archive",
        "raw_s3_key": (
            "raw/source=taf/year=2026/month=07/day=18/hour=12/"
            "taf-poll-taf-001.json.gz"
        ),
        "record_index": 0,
        "taf": taf_record,
    }


@pytest.fixture
def taf_kinesis_record_factory() -> Callable[[Any, str], dict[str, Any]]:
    def _make(
        payload: Any,
        sequence_number: str = "taf-seq-1",
    ) -> dict[str, Any]:
        encoded = base64.b64encode(
            json.dumps(payload).encode("utf-8")
        ).decode("ascii")

        return {
            "eventSource": "aws:kinesis",
            "eventID": f"event-{sequence_number}",
            "kinesis": {
                "sequenceNumber": sequence_number,
                "data": encoded,
            },
        }

    return _make


@pytest.fixture
def taf_poller(
    load_repo_module: Callable[[str, str], ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> ModuleType:
    monkeypatch.setenv("TAF_STATION_IDS", "kjfk, ksfo, KJFK")
    monkeypatch.setenv(
        "NOAA_TAF_URL",
        "https://aviationweather.example.test/api/data/taf",
    )
    monkeypatch.setenv("TAF_STATION_CHUNK_SIZE", "100")
    monkeypatch.setenv("ARCHIVE_BUCKET_NAME", "test-weather-archive")
    monkeypatch.setenv("RAW_PREFIX", "raw/source=taf")
    monkeypatch.setenv("TAF_RAW_STREAM_NAME", "test-taf-raw")

    return load_repo_module(
        "unit_taf_poller_app",
        "functions/weather/taf/poller/app.py",
    )


@pytest.fixture
def taf_processor(
    load_repo_module: Callable[[str, str], ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> ModuleType:
    monkeypatch.setenv("TAF_LATEST_TABLE_NAME", "test-taf-latest")
    monkeypatch.setenv("EVENT_BUS_NAME", "test-weather-events")
    monkeypatch.setenv(
        "BAD_RECORDS_BUCKET_NAME",
        "test-weather-archive",
    )
    monkeypatch.setenv(
        "BAD_RECORDS_PREFIX",
        "bad-records/source=taf_processor",
    )
    monkeypatch.setenv("SCHEMA_VERSION", "internal.taf.v1")

    return load_repo_module(
        "unit_taf_processor_app",
        "functions/weather/taf/processor/app.py",
    )
