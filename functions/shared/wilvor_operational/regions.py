"""Deterministic U.S. state region identity and static Census geometry.

This module is Shapely-free. It loads the vendored Census Cartographic
Boundary dataset packaged with wilvor_operational.data.

A resolved V1 region such as California means the geographic state
polygon/multipolygon in that vendored dataset at the recorded vintage.
It does not mean FAA airspace, ARTCC, FIR, an operational aviation
region, ImpactCells, HazardCells, an H3 region, or arbitrary proximity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from types import MappingProxyType
from typing import Any


REGION_TYPE_US_STATE = "US_STATE"
DATA_PACKAGE = "wilvor_operational.data"
GEOJSON_NAME = "us_states.geojson"
META_NAME = "us_states.meta.json"


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_query(value: Any) -> str:
    return " ".join(_text(value).split()).casefold()


def _freeze_point(point: Any) -> tuple[float, float]:
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        raise ValueError("invalid coordinate pair in vendored state geometry")
    longitude = point[0]
    latitude = point[1]
    if isinstance(longitude, bool) or isinstance(latitude, bool):
        raise ValueError("boolean coordinate in vendored state geometry")
    return (float(longitude), float(latitude))


def _freeze_ring(ring: Any) -> tuple[tuple[float, float], ...]:
    if not isinstance(ring, (list, tuple)) or not ring:
        raise ValueError("invalid ring in vendored state geometry")
    return tuple(_freeze_point(point) for point in ring)


def _freeze_polygon(polygon: Any) -> tuple[tuple[tuple[float, float], ...], ...]:
    if not isinstance(polygon, (list, tuple)) or not polygon:
        raise ValueError("invalid polygon in vendored state geometry")
    return tuple(_freeze_ring(ring) for ring in polygon)


def _freeze_geometry(geometry: dict[str, Any]) -> tuple[str, tuple]:
    geometry_type = _text(geometry.get("type"))
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon":
        return geometry_type, _freeze_polygon(coordinates)
    if geometry_type == "MultiPolygon":
        if not isinstance(coordinates, (list, tuple)) or not coordinates:
            raise ValueError("invalid MultiPolygon in vendored state geometry")
        frozen = tuple(_freeze_polygon(polygon) for polygon in coordinates)
        return geometry_type, frozen
    raise ValueError(f"unsupported vendored geometry type: {geometry_type}")


@dataclass(frozen=True)
class Region:
    region_type: str
    code: str
    name: str
    dataset_product: str
    dataset_vintage: str
    dataset_sha256: str
    target_crs: str
    spatial_supported: bool
    geometry_type: str
    coordinates: tuple


@dataclass(frozen=True)
class RegionResolution:
    resolved: bool
    region: Region | None


@dataclass(frozen=True)
class UsStatesDataset:
    regions: tuple[Region, ...]
    by_code: MappingProxyType[str, Region]
    by_query: MappingProxyType[str, Region]
    metadata: MappingProxyType[str, Any]


def _load_package_bytes(name: str) -> bytes:
    return resources.files(DATA_PACKAGE).joinpath(name).read_bytes()


def _parse_dataset() -> UsStatesDataset:
    meta = json.loads(_load_package_bytes(META_NAME).decode("utf-8"))
    geojson_bytes = _load_package_bytes(GEOJSON_NAME)
    digest = hashlib.sha256(geojson_bytes).hexdigest()
    recorded = _text(meta.get("vendored_geojson_sha256"))
    if digest != recorded:
        raise RuntimeError(
            "vendored us_states.geojson SHA-256 does not match us_states.meta.json"
        )
    collection = json.loads(geojson_bytes.decode("utf-8"))
    features = collection.get("features")
    if collection.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise RuntimeError("vendored us_states.geojson is not a FeatureCollection")
    if len(features) != 50 or meta.get("feature_count") != 50:
        raise RuntimeError("vendored state dataset does not contain exactly 50 states")

    unsupported = {
        _text(code)
        for code in (meta.get("spatial_unsupported_codes") or ())
        if _text(code)
    }
    regions = []
    by_code: dict[str, Region] = {}
    by_query: dict[str, Region] = {}
    for feature in features:
        properties = feature.get("properties") or {}
        code = _text(properties.get("code")).upper()
        name = _text(properties.get("name"))
        geometry_type, coordinates = _freeze_geometry(feature.get("geometry") or {})
        region = Region(
            region_type=REGION_TYPE_US_STATE,
            code=code,
            name=name,
            dataset_product=_text(meta.get("dataset_product")),
            dataset_vintage=_text(meta.get("vintage")),
            dataset_sha256=recorded,
            target_crs=_text(meta.get("target_crs")),
            spatial_supported=code not in unsupported,
            geometry_type=geometry_type,
            coordinates=coordinates,
        )
        if code in by_code:
            raise RuntimeError(f"duplicate vendored state code: {code}")
        by_code[code] = region
        by_query[_normalize_query(code)] = region
        by_query[_normalize_query(name)] = region
        regions.append(region)

    if len(by_code) != 50:
        raise RuntimeError("vendored state codes are not unique")
    return UsStatesDataset(
        regions=tuple(regions),
        by_code=MappingProxyType(by_code),
        by_query=MappingProxyType(by_query),
        metadata=MappingProxyType(meta),
    )


@lru_cache(maxsize=1)
def load_us_states_dataset() -> UsStatesDataset:
    return _parse_dataset()


def resolve_region(text) -> RegionResolution:
    query = _normalize_query(text)
    if not query:
        return RegionResolution(resolved=False, region=None)
    region = load_us_states_dataset().by_query.get(query)
    if region is None:
        return RegionResolution(resolved=False, region=None)
    return RegionResolution(resolved=True, region=region)
