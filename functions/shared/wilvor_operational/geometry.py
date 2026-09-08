"""Strict HazardCoordinates reconstruction and Shapely spatial operations.

This module is the only approved Shapely import path for Phase 1D.2.
It does not query DynamoDB, resolve regions, or import wilvor_ai.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from shapely.geometry import MultiPolygon, Point, Polygon

from . import regions


class SpatialStatus(str, Enum):
    INTERSECTS = "INTERSECTS"
    DOES_NOT_INTERSECT = "DOES_NOT_INTERSECT"
    UNEVALUATED = "UNEVALUATED"


GEOMETRY_HASH_LINEAGE_LIMITATION = (
    "geometry_hash is compared as copied parent/child lineage only; it is not "
    "an independently recomputed content checksum."
)
REGION_SPATIAL_UNSUPPORTED = (
    "Region identity resolved but spatial evaluation is unsupported for this "
    "vendored geometry representation."
)


@dataclass(frozen=True)
class PinnedHazardIdentity:
    hazard_id: str
    source_version: str
    geometry_type: str
    geometry_hash: str
    materialization_id: str


@dataclass(frozen=True)
class GeometryReconstruction:
    ok: bool
    geometry: object | None
    limitation: str | None
    limitations: tuple[str, ...] = ()


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if isinstance(value, float) and number != value:
        return None
    return number


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fail(limitation: str, *extra: str) -> GeometryReconstruction:
    limitations = (limitation,) + extra
    return GeometryReconstruction(
        ok=False,
        geometry=None,
        limitation=limitation,
        limitations=limitations,
    )


def _ok(geometry, *limitations: str) -> GeometryReconstruction:
    return GeometryReconstruction(
        ok=True,
        geometry=geometry,
        limitation=None,
        limitations=limitations,
    )


def reconstruct_hazard_geometry(
    rows,
    *,
    identity: PinnedHazardIdentity,
) -> GeometryReconstruction:
    hazard_id = _text(identity.hazard_id)
    source_version = _text(identity.source_version)
    geometry_type = _text(identity.geometry_type).upper()
    if not hazard_id or not source_version:
        return _fail("pinned hazard identity is missing source_version")
    if geometry_type not in {"POLYGON", "MULTIPOLYGON"}:
        return _fail(f"unsupported geometry_type: {geometry_type or '<empty>'}")
    if not rows:
        return _fail("HazardCoordinates rows are missing")

    expected_key = f"{hazard_id}#{source_version}"
    grouped: dict[int, dict[int, list[tuple[int, float, float]]]] = {}
    observed_types: set[str] = set()
    lineage_limitations: list[str] = [GEOMETRY_HASH_LINEAGE_LIMITATION]
    parent_hash = _text(identity.geometry_hash)
    parent_materialization = _text(identity.materialization_id)
    saw_hash = False
    saw_materialization = False

    for row in rows:
        if not isinstance(row, dict):
            return _fail("coordinate row is not an object")
        row_key = _text(row.get("hazard_version_key"))
        if row_key and row_key != expected_key:
            return _fail("coordinate hazard_version_key does not match pinned identity")
        row_hazard_id = _text(row.get("hazard_id"))
        row_source_version = _text(row.get("source_version"))
        if row_hazard_id and row_hazard_id != hazard_id:
            return _fail("coordinate hazard_id does not match pinned identity")
        if row_source_version and row_source_version != source_version:
            return _fail("coordinate source_version does not match pinned identity")

        row_type = _text(row.get("geometry_type")).upper()
        if row_type:
            observed_types.add(row_type)
            if row_type != geometry_type:
                return _fail("coordinate geometry_type does not match pinned parent")

        row_hash = _text(row.get("geometry_hash"))
        if row_hash:
            saw_hash = True
            if parent_hash and row_hash != parent_hash:
                return _fail("copied geometry_hash lineage mismatch")
        row_materialization = _text(row.get("materialization_id"))
        if row_materialization:
            saw_materialization = True
            if parent_materialization and row_materialization != parent_materialization:
                return _fail("copied materialization_id lineage mismatch")

        polygon_index = _as_int(row.get("polygon_index"))
        ring_index = _as_int(row.get("ring_index"))
        sequence_number = _as_int(row.get("sequence_number"))
        if polygon_index is None or ring_index is None or sequence_number is None:
            return _fail("coordinate indexes are not non-negative integers")
        if polygon_index < 0 or ring_index < 0 or sequence_number < 0:
            return _fail("coordinate indexes are not non-negative integers")

        longitude = _as_float(row.get("longitude"))
        latitude = _as_float(row.get("latitude"))
        if longitude is None or latitude is None:
            return _fail("coordinate latitude/longitude is not numeric")
        if not -180.0 <= longitude <= 180.0:
            return _fail(f"longitude out of range: {longitude}")
        if not -90.0 <= latitude <= 90.0:
            return _fail(f"latitude out of range: {latitude}")

        grouped.setdefault(polygon_index, {}).setdefault(ring_index, []).append(
            (sequence_number, longitude, latitude)
        )

    if parent_hash and not saw_hash:
        return _fail("parent geometry_hash is present but coordinate rows omit it")
    if parent_materialization and not saw_materialization:
        return _fail("parent materialization_id is present but coordinate rows omit it")
    if not parent_hash:
        lineage_limitations.append(
            "geometry_hash was absent and was not fabricated; lineage comparison "
            "was limited to fields that were present."
        )
    if not parent_materialization:
        lineage_limitations.append(
            "materialization_id was absent and was not fabricated."
        )
    if observed_types and observed_types != {geometry_type}:
        return _fail("coordinate geometry_type is inconsistent")

    polygon_indexes = sorted(grouped)
    if polygon_indexes != list(range(len(polygon_indexes))):
        return _fail("polygon indexes are not dense and zero-based")
    if geometry_type == "POLYGON" and len(polygon_indexes) != 1:
        return _fail("POLYGON must contain exactly one polygon_index 0")
    if geometry_type == "MULTIPOLYGON" and not polygon_indexes:
        return _fail("MULTIPOLYGON has no polygons")

    polygons: list[list[list[tuple[float, float]]]] = []
    for polygon_index in polygon_indexes:
        ring_indexes = sorted(grouped[polygon_index])
        if ring_indexes != list(range(len(ring_indexes))):
            return _fail("ring indexes are not dense and zero-based")
        rings: list[list[tuple[float, float]]] = []
        for ring_index in ring_indexes:
            records = sorted(
                grouped[polygon_index][ring_index],
                key=lambda item: item[0],
            )
            sequences = [item[0] for item in records]
            if sequences != list(range(len(sequences))):
                return _fail("sequence_number values are not dense 0..N-1")
            coords = [(item[1], item[2]) for item in records]
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            if len(coords) < 4:
                return _fail("ring has fewer than four positions after closure")
            distinct = set(coords[:-1])
            if len(distinct) < 3:
                return _fail("ring is degenerate")
            rings.append(coords)
        polygons.append(rings)

    try:
        if geometry_type == "POLYGON":
            geometry = Polygon(polygons[0][0], polygons[0][1:])
        else:
            parts = [Polygon(rings[0], rings[1:]) for rings in polygons]
            geometry = MultiPolygon(parts)
    except Exception:
        return _fail("Shapely could not construct the persisted geometry")

    if geometry.is_empty:
        return _fail("constructed geometry is empty")
    if not geometry.is_valid:
        return _fail("constructed geometry is topologically invalid")
    return _ok(geometry, *tuple(lineage_limitations))


def region_to_shapely(region: regions.Region) -> GeometryReconstruction:
    if not region.spatial_supported:
        return _fail(REGION_SPATIAL_UNSUPPORTED)
    try:
        if region.geometry_type == "Polygon":
            rings = [list(ring) for ring in region.coordinates]
            geometry = Polygon(rings[0], rings[1:])
        elif region.geometry_type == "MultiPolygon":
            parts = []
            for polygon in region.coordinates:
                rings = [list(ring) for ring in polygon]
                parts.append(Polygon(rings[0], rings[1:]))
            geometry = MultiPolygon(parts)
        else:
            return _fail(f"unsupported region geometry type: {region.geometry_type}")
    except Exception:
        return _fail("Shapely could not construct the vendored region geometry")
    if geometry.is_empty:
        return _fail("vendored region geometry is empty")
    if not geometry.is_valid:
        return _fail("vendored region geometry is topologically invalid")
    return _ok(geometry)


def spatial_intersects(hazard_geometry, region_geometry) -> SpatialStatus:
    if hazard_geometry is None or region_geometry is None:
        return SpatialStatus.UNEVALUATED
    try:
        if hazard_geometry.is_empty or region_geometry.is_empty:
            return SpatialStatus.UNEVALUATED
        if not hazard_geometry.is_valid or not region_geometry.is_valid:
            return SpatialStatus.UNEVALUATED
        if hazard_geometry.intersects(region_geometry):
            return SpatialStatus.INTERSECTS
        return SpatialStatus.DOES_NOT_INTERSECT
    except Exception:
        return SpatialStatus.UNEVALUATED


def point_from_lon_lat(longitude: float, latitude: float) -> Point:
    return Point(longitude, latitude)
