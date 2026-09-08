"""Direct tests for Phase 1D.2 HazardCoordinates reconstruction and intersection."""

from __future__ import annotations

from decimal import Decimal

from shapely.geometry import Polygon

from wilvor_operational import geometry
from wilvor_operational import regions


def _identity(**overrides):
    values = {
        "hazard_id": "h-1",
        "source_version": "v1",
        "geometry_type": "POLYGON",
        "geometry_hash": "hash-1",
        "materialization_id": "mat-1",
    }
    values.update(overrides)
    return geometry.PinnedHazardIdentity(**values)


def _row(
    sequence,
    longitude,
    latitude,
    *,
    polygon_index=0,
    ring_index=0,
    **extra,
):
    row = {
        "hazard_version_key": "h-1#v1",
        "hazard_id": "h-1",
        "source_version": "v1",
        "geometry_type": "POLYGON",
        "geometry_hash": "hash-1",
        "materialization_id": "mat-1",
        "polygon_index": polygon_index,
        "ring_index": ring_index,
        "sequence_number": sequence,
        "longitude": Decimal(str(longitude)),
        "latitude": Decimal(str(latitude)),
    }
    row.update(extra)
    return row


def _closed_square(west, south, east, north, **kwargs):
    points = (
        (west, south),
        (east, south),
        (east, north),
        (west, north),
        (west, south),
    )
    return [
        _row(sequence, lon, lat, **kwargs)
        for sequence, (lon, lat) in enumerate(points)
    ]


def test_reconstruct_valid_polygon_and_multipolygon():
    polygon = geometry.reconstruct_hazard_geometry(
        _closed_square(-2, -2, 2, 2),
        identity=_identity(),
    )
    assert polygon.ok is True
    assert polygon.geometry is not None
    assert geometry.GEOMETRY_HASH_LINEAGE_LIMITATION in polygon.limitations

    multi_rows = _closed_square(-2, -2, -1, -1, polygon_index=0)
    multi_rows.extend(
        _closed_square(1, 1, 2, 2, polygon_index=1)
    )
    for row in multi_rows:
        row["geometry_type"] = "MULTIPOLYGON"
    multi = geometry.reconstruct_hazard_geometry(
        multi_rows,
        identity=_identity(geometry_type="MULTIPOLYGON"),
    )
    assert multi.ok is True
    assert multi.geometry.geom_type == "MultiPolygon"


def test_reconstruct_exterior_and_hole():
    rows = _closed_square(-2, -2, 2, 2)
    rows.extend(_closed_square(-1, -1, 1, 1, ring_index=1))
    result = geometry.reconstruct_hazard_geometry(rows, identity=_identity())
    assert result.ok is True
    assert result.geometry.interiors


def test_reconstruct_rejects_integrity_failures():
    missing = geometry.reconstruct_hazard_geometry([], identity=_identity())
    assert missing.ok is False
    assert "missing" in missing.limitation

    unsupported = geometry.reconstruct_hazard_geometry(
        _closed_square(-1, -1, 1, 1),
        identity=_identity(geometry_type="LINESTRING"),
    )
    assert unsupported.ok is False

    extra_polygon = _closed_square(-2, -2, -1, -1, polygon_index=0)
    extra_polygon.extend(_closed_square(1, 1, 2, 2, polygon_index=1))
    assert geometry.reconstruct_hazard_geometry(
        extra_polygon,
        identity=_identity(),
    ).ok is False

    gap = _closed_square(-1, -1, 1, 1)
    gap[2]["polygon_index"] = 2
    assert geometry.reconstruct_hazard_geometry(gap, identity=_identity()).ok is False

    bad_ring = _closed_square(-1, -1, 1, 1)
    bad_ring[-1]["ring_index"] = 2
    assert geometry.reconstruct_hazard_geometry(bad_ring, identity=_identity()).ok is False

    duplicate_seq = _closed_square(-1, -1, 1, 1)
    duplicate_seq[1]["sequence_number"] = 0
    assert geometry.reconstruct_hazard_geometry(
        duplicate_seq,
        identity=_identity(),
    ).ok is False

    seq_gap = _closed_square(-1, -1, 1, 1)
    seq_gap[2]["sequence_number"] = 4
    assert geometry.reconstruct_hazard_geometry(seq_gap, identity=_identity()).ok is False


def test_reconstruct_rejects_invalid_numeric_values():
    bool_index = _closed_square(-1, -1, 1, 1)
    bool_index[0]["polygon_index"] = True
    assert geometry.reconstruct_hazard_geometry(bool_index, identity=_identity()).ok is False

    bool_coord = _closed_square(-1, -1, 1, 1)
    bool_coord[0]["longitude"] = True
    assert geometry.reconstruct_hazard_geometry(bool_coord, identity=_identity()).ok is False

    out_of_range = _closed_square(-1, -1, 1, 1)
    out_of_range[1]["longitude"] = 181
    assert geometry.reconstruct_hazard_geometry(
        out_of_range,
        identity=_identity(),
    ).ok is False

    out_of_range_lat = _closed_square(-1, -1, 1, 1)
    out_of_range_lat[1]["latitude"] = 91
    assert geometry.reconstruct_hazard_geometry(
        out_of_range_lat,
        identity=_identity(),
    ).ok is False


def test_reconstruct_rejects_lineage_and_identity_mismatch():
    wrong_id = _closed_square(-1, -1, 1, 1)
    wrong_id[0]["hazard_id"] = "other"
    assert geometry.reconstruct_hazard_geometry(wrong_id, identity=_identity()).ok is False

    wrong_version = _closed_square(-1, -1, 1, 1)
    wrong_version[0]["source_version"] = "v9"
    assert geometry.reconstruct_hazard_geometry(
        wrong_version,
        identity=_identity(),
    ).ok is False

    wrong_hash = _closed_square(-1, -1, 1, 1)
    wrong_hash[0]["geometry_hash"] = "other-hash"
    assert geometry.reconstruct_hazard_geometry(wrong_hash, identity=_identity()).ok is False

    wrong_mat = _closed_square(-1, -1, 1, 1)
    wrong_mat[0]["materialization_id"] = "other-mat"
    assert geometry.reconstruct_hazard_geometry(wrong_mat, identity=_identity()).ok is False

    wrong_type = _closed_square(-1, -1, 1, 1)
    wrong_type[0]["geometry_type"] = "MULTIPOLYGON"
    assert geometry.reconstruct_hazard_geometry(wrong_type, identity=_identity()).ok is False


def test_reconstruct_rejects_degenerate_and_invalid_topology():
    short = [
        _row(0, 0, 0),
        _row(1, 1, 0),
        _row(2, 0, 0),
    ]
    assert geometry.reconstruct_hazard_geometry(short, identity=_identity()).ok is False

    bowtie = [
        _row(0, 0, 0),
        _row(1, 1, 0),
        _row(2, 0, 1),
        _row(3, 1, 0),
        _row(4, 0, 0),
    ]
    result = geometry.reconstruct_hazard_geometry(bowtie, identity=_identity())
    assert result.ok is False
    assert "invalid" in result.limitation


def test_intersection_semantics():
    region = Polygon([(-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1)])
    inside = Polygon([(-0.2, -0.2), (0.2, -0.2), (0.2, 0.2), (-0.2, 0.2), (-0.2, -0.2)])
    container = Polygon([(-2, -2), (2, -2), (2, 2), (-2, 2), (-2, -2)])
    overlap = Polygon([(0.5, -0.5), (1.5, -0.5), (1.5, 0.5), (0.5, 0.5), (0.5, -0.5)])
    edge = Polygon([(1, -0.5), (2, -0.5), (2, 0.5), (1, 0.5), (1, -0.5)])
    vertex = Polygon([(1, 1), (2, 1), (2, 2), (1, 2), (1, 1)])
    outside = Polygon([(3, 3), (4, 3), (4, 4), (3, 4), (3, 3)])
    hole_region = Polygon(
        [(-2, -2), (2, -2), (2, 2), (-2, 2), (-2, -2)],
        [[(-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1)]],
    )
    in_hole = Polygon([(-0.2, -0.2), (0.2, -0.2), (0.2, 0.2), (-0.2, 0.2), (-0.2, -0.2)])
    multi_rows = [
        *_closed_square(3, 3, 4, 4, polygon_index=0),
        *_closed_square(-0.5, -0.5, 0.5, 0.5, polygon_index=1),
    ]
    for row in multi_rows:
        row["geometry_type"] = "MULTIPOLYGON"
    multi = geometry.reconstruct_hazard_geometry(
        multi_rows,
        identity=_identity(geometry_type="MULTIPOLYGON"),
    )

    assert geometry.spatial_intersects(inside, region) is geometry.SpatialStatus.INTERSECTS
    assert geometry.spatial_intersects(container, region) is geometry.SpatialStatus.INTERSECTS
    assert geometry.spatial_intersects(overlap, region) is geometry.SpatialStatus.INTERSECTS
    assert geometry.spatial_intersects(edge, region) is geometry.SpatialStatus.INTERSECTS
    assert geometry.spatial_intersects(vertex, region) is geometry.SpatialStatus.INTERSECTS
    assert geometry.spatial_intersects(outside, region) is geometry.SpatialStatus.DOES_NOT_INTERSECT
    assert geometry.spatial_intersects(in_hole, hole_region) is geometry.SpatialStatus.DOES_NOT_INTERSECT
    assert multi.ok is True
    assert geometry.spatial_intersects(multi.geometry, region) is geometry.SpatialStatus.INTERSECTS
    assert geometry.spatial_intersects(outside, None) is geometry.SpatialStatus.UNEVALUATED


def test_california_region_geometry_smoke():
    region = regions.resolve_region("California").region
    reconstructed = geometry.region_to_shapely(region)
    assert reconstructed.ok is True
    assert not reconstructed.geometry.is_empty
    interior = geometry.point_from_lon_lat(-120.0, 37.0)
    distant = geometry.point_from_lon_lat(0.0, 0.0)
    assert reconstructed.geometry.contains(interior)
    assert not reconstructed.geometry.contains(distant)
    assert not reconstructed.geometry.intersects(distant)
