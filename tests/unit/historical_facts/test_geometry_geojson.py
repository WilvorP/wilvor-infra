"""GeoJSON reconstruction from enabled SIGMET in-memory coordinate points."""

from __future__ import annotations

import hashlib
import inspect
from types import ModuleType
from typing import Callable

import pytest

from wilvor_historical.geometry_geojson import build_geojson_geometry
from wilvor_historical.from_events import (
    GEOMETRY_IN_MEMORY_DETAIL_TYPE,
    build_hazard_geometry_fact,
)


@pytest.fixture
def sigmet_feature() -> dict:
    return {
        "type": "Feature",
        "properties": {"hazard": "Turbulence"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [-75.0, 40.0],
                [-74.0, 40.0],
                [-74.0, 41.0],
                [-75.0, 41.0],
                [-75.0, 40.0],
            ]],
        },
    }


@pytest.fixture
def sigmet_processor(
    load_repo_module: Callable[[str, str], ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> ModuleType:
    monkeypatch.setenv("ACTIVE_HAZARDS_TABLE_NAME", "test-active-hazards")
    monkeypatch.setenv("HAZARD_COORDINATES_TABLE_NAME", "test-hazard-coordinates")
    monkeypatch.setenv("HAZARD_CELLS_TABLE_NAME", "test-hazard-cells")
    monkeypatch.setenv("IMPACT_CELLS_TABLE_NAME", "test-impact-cells")
    monkeypatch.setenv("H3_RESOLUTION", "4")
    monkeypatch.setenv("IMPACT_GRID_DISTANCE", "2")
    monkeypatch.setenv("IMPACT_RADIUS_NM", "50")
    monkeypatch.setenv("SCHEMA_VERSION", "wilvor.active_hazards.v4.0")
    monkeypatch.setenv("RETENTION_AFTER_VALID_TO_HOURS", "6")
    monkeypatch.setenv("BAD_RECORDS_BUCKET_NAME", "test-sigmet-archive")
    monkeypatch.setenv("BAD_RECORDS_PREFIX", "bad-records/source=sigmet_processor")
    monkeypatch.setenv("EVENT_BUS_NAME", "default")
    return load_repo_module(
        "historical_facts_sigmet_processor_app",
        "functions/weather/sigmet/processor/app.py",
    )


def _parent(**overrides):
    item = {
        "hazard_id": "sigmet-aaa",
        "source_version": "v1",
        "hazard_version_key": "sigmet-aaa#v1",
        "materialization_id": "hazard-materialization-1",
        "geometry_hash": "copied-hash-v1",
        "geometry_type": "POLYGON",
        "geometry_point_count": 5,
        "materialized_at_utc": "2026-07-18T12:30:00+00:00",
        "source_system": "NOAA_AVIATIONWEATHER_SIGMET",
        "schema_version": "wilvor.active_hazards.v4.0",
        "correlation_id": "poll-1:0",
    }
    item.update(overrides)
    return item


def test_polygon_from_enabled_sigmet_flatten_preserves_close_and_lonlat(
    sigmet_processor,
    sigmet_feature,
):
    points = sigmet_processor.flatten_geometry_points(
        sigmet_feature["geometry"]
    )
    geometry = build_geojson_geometry(points, geometry_type="POLYGON")
    assert geometry["type"] == "Polygon"
    ring = geometry["coordinates"][0]
    assert ring[0] == [-75.0, 40.0]
    assert ring[-1] == [-75.0, 40.0]
    assert len(ring) == 5
    fact = build_hazard_geometry_fact(
        active_hazard=_parent(geometry_point_count=len(points)),
        geometry_points=points,
    )
    assert fact.coordinates_axis == "lonlat"
    assert fact.producer_detail_type == GEOMETRY_IN_MEMORY_DETAIL_TYPE
    assert fact.geometry_hash == "copied-hash-v1"
    assert fact.event_time_utc == "2026-07-18T12:30:00Z"
    assert fact.record_id == "sigmet-aaa#v1"


def test_polygon_with_hole_keeps_ring_order(sigmet_processor):
    geometry = {
        "type": "Polygon",
        "coordinates": [
            [
                [-75.0, 40.0],
                [-74.0, 40.0],
                [-74.0, 41.0],
                [-75.0, 41.0],
                [-75.0, 40.0],
            ],
            [
                [-74.8, 40.2],
                [-74.7, 40.2],
                [-74.7, 40.3],
                [-74.8, 40.3],
                [-74.8, 40.2],
            ],
        ],
    }
    points = sigmet_processor.flatten_geometry_points(geometry)
    geojson = build_geojson_geometry(points, geometry_type="POLYGON")
    assert geojson["type"] == "Polygon"
    assert geojson["coordinates"][0][0] == [-75.0, 40.0]
    assert geojson["coordinates"][1][0] == [-74.8, 40.2]
    assert [point["ring_index"] for point in points[:5]] == [0, 0, 0, 0, 0]
    assert points[5]["ring_index"] == 1
    assert points[0]["sequence_number"] == 0
    assert points[5]["sequence_number"] == 0


def test_multipolygon_preserves_polygon_indexes(sigmet_processor):
    geometry = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[-75.0, 40.0], [-74.0, 40.0], [-74.0, 41.0], [-75.0, 40.0]]],
            [[[-80.0, 35.0], [-79.0, 35.0], [-79.0, 36.0], [-80.0, 35.0]]],
        ],
    }
    points = sigmet_processor.flatten_geometry_points(geometry)
    geojson = build_geojson_geometry(points, geometry_type="MULTIPOLYGON")
    assert geojson["type"] == "MultiPolygon"
    assert len(geojson["coordinates"]) == 2
    assert geojson["coordinates"][0][0][0] == [-75.0, 40.0]
    assert geojson["coordinates"][1][0][0] == [-80.0, 35.0]
    fact = build_hazard_geometry_fact(
        active_hazard=_parent(
            geometry_type="MULTIPOLYGON",
            geometry_point_count=len(points),
        ),
        geometry_points=points,
    )
    assert fact.geometry_type == "MULTIPOLYGON"
    assert fact.geometry["type"] == "MultiPolygon"


def test_version_isolation_copies_hash_without_recomputing(sigmet_processor):
    points = sigmet_processor.flatten_geometry_points(
        {
            "type": "Polygon",
            "coordinates": [[
                [-75.0, 40.0],
                [-74.0, 40.0],
                [-74.0, 41.0],
                [-75.0, 41.0],
                [-75.0, 40.0],
            ]],
        }
    )
    v1 = build_hazard_geometry_fact(
        active_hazard=_parent(),
        geometry_points=points,
    )
    v2 = build_hazard_geometry_fact(
        active_hazard=_parent(
            source_version="v2",
            hazard_version_key="sigmet-aaa#v2",
            geometry_hash="copied-hash-v2",
        ),
        geometry_points=points,
    )
    assert v1.hazard_id == v2.hazard_id
    assert v1.record_id != v2.record_id
    assert v1.geometry_hash == "copied-hash-v1"
    assert v2.geometry_hash == "copied-hash-v2"
    source = inspect.getsource(build_geojson_geometry)
    assert "sha256" not in source
    assert "hashlib" not in source
    assert hashlib.sha256(b"copied-hash-v1").hexdigest() != v1.geometry_hash
