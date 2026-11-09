"""Build compact GeoJSON from enabled SIGMET in-memory coordinate points.

Input points use the enabled SIGMET processor flatten_geometry_points shape:
polygon_index, ring_index, sequence_number, longitude, latitude, geometry_type.

This module does not recompute geometry_hash, import Shapely, or skip
malformed rows.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .contracts import COORDINATES_AXIS_LONLAT, HistoricalFactError


def _as_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or value is None:
        raise HistoricalFactError(f"invalid {field_name}")
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal) and value % 1 == 0:
        return int(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise HistoricalFactError(f"invalid {field_name}")


def _as_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or value is None:
        raise HistoricalFactError(f"invalid {field_name}")
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError as exc:
            raise HistoricalFactError(f"invalid {field_name}") from exc
    raise HistoricalFactError(f"invalid {field_name}")


def _as_geometry_type(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text not in {"POLYGON", "MULTIPOLYGON"}:
        raise HistoricalFactError(
            f"unsupported geometry_type: {value!r}"
        )
    return text


def build_geojson_geometry(
    points: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    geometry_type: str | None = None,
) -> dict[str, Any]:
    """Reconstruct Polygon or MultiPolygon coordinates as [lon, lat].

    ring_index 0 is the exterior. ring_index 1+ are holes. Closing coordinates
    are preserved exactly as supplied; they are not stripped or invented.
    """

    if not isinstance(points, (list, tuple)) or not points:
        raise HistoricalFactError("geometry points are missing")

    expected_type = (
        _as_geometry_type(geometry_type)
        if geometry_type is not None
        else None
    )
    grouped: dict[int, dict[int, list[tuple[int, float, float]]]] = {}
    observed_types: set[str] = set()

    for index, point in enumerate(points):
        if not isinstance(point, dict):
            raise HistoricalFactError(
                f"geometry point {index} is not an object"
            )

        point_type = _as_geometry_type(point.get("geometry_type"))
        observed_types.add(point_type)
        if expected_type is not None and point_type != expected_type:
            raise HistoricalFactError(
                "geometry_type does not match point geometry_type"
            )

        polygon_index = _as_int(point.get("polygon_index"), "polygon_index")
        ring_index = _as_int(point.get("ring_index"), "ring_index")
        sequence_number = _as_int(
            point.get("sequence_number"),
            "sequence_number",
        )
        if polygon_index < 0 or ring_index < 0 or sequence_number < 0:
            raise HistoricalFactError("geometry indexes must be non-negative")

        longitude = _as_float(point.get("longitude"), "longitude")
        latitude = _as_float(point.get("latitude"), "latitude")
        grouped.setdefault(polygon_index, {}).setdefault(ring_index, []).append(
            (sequence_number, longitude, latitude)
        )

    if len(observed_types) != 1:
        raise HistoricalFactError("geometry points have mixed geometry_type")

    resolved_type = expected_type or next(iter(observed_types))
    polygons: list[list[list[list[float]]]] = []

    for polygon_index in sorted(grouped):
        rings = grouped[polygon_index]
        if 0 not in rings:
            raise HistoricalFactError(
                f"polygon {polygon_index} is missing exterior ring_index 0"
            )
        polygon_rings: list[list[list[float]]] = []
        for ring_index in sorted(rings):
            ordered = sorted(rings[ring_index], key=lambda item: item[0])
            sequences = [item[0] for item in ordered]
            if sequences != list(range(len(sequences))):
                raise HistoricalFactError(
                    "sequence_number values are not a contiguous ordering"
                )
            if len(ordered) < 4:
                raise HistoricalFactError(
                    "geometry ring has too few positions"
                )
            polygon_rings.append(
                [[longitude, latitude] for _, longitude, latitude in ordered]
            )
        polygons.append(polygon_rings)

    if resolved_type == "POLYGON":
        if len(polygons) != 1:
            raise HistoricalFactError(
                "POLYGON geometry must contain exactly one polygon_index"
            )
        return {
            "type": "Polygon",
            "coordinates": polygons[0],
        }

    return {
        "type": "MultiPolygon",
        "coordinates": polygons,
    }


def coordinates_axis() -> str:
    return COORDINATES_AXIS_LONLAT
