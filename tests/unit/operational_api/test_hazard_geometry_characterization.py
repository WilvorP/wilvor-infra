"""Freeze current Operational API _hazard_geometry behavior.

These tests characterize existing reconstruction. They do not endorse it
as Phase 1D.2 policy. Shared 1D.2 reconstruction is intentionally
stricter on malformed rows and extra polygons.
"""

from __future__ import annotations

from tests.unit.operational_api.test_read_access_characterization import (
    RecordingTable,
)


def _closed_square(*, west, south, east, north, polygon_index=0, ring_index=0):
    points = (
        (west, south),
        (east, south),
        (east, north),
        (west, north),
        (west, south),
    )
    return [
        {
            "polygon_index": polygon_index,
            "ring_index": ring_index,
            "sequence_number": sequence,
            "longitude": longitude,
            "latitude": latitude,
        }
        for sequence, (longitude, latitude) in enumerate(points)
    ]


def _install_rows(repo, rows):
    repo.HAZARD_COORDINATES = RecordingTable([{"Items": list(rows)}])


def _geometry(repo, *, hazard_id="h-1", source_version="v1", geometry_type="POLYGON"):
    return repo._hazard_geometry(
        {
            "hazard_id": hazard_id,
            "source_version": source_version,
            "geometry_type": geometry_type,
        }
    )


def test_hazard_geometry_returns_none_without_identity(operational_repository):
    repo = operational_repository
    _install_rows(repo, _closed_square(west=-122.0, south=37.0, east=-121.0, north=38.0))

    assert repo._hazard_geometry({"source_version": "v1"}) is None
    assert repo._hazard_geometry({"hazard_id": "h-1"}) is None
    assert repo._hazard_geometry({}) is None


def test_hazard_geometry_returns_none_when_rows_are_missing(operational_repository):
    repo = operational_repository
    _install_rows(repo, [])

    assert _geometry(repo) is None


def test_hazard_geometry_groups_polygons_rings_and_sequence(operational_repository):
    repo = operational_repository
    rows = [
        {
            "polygon_index": 0,
            "ring_index": 0,
            "sequence_number": 2,
            "longitude": -121.0,
            "latitude": 38.0,
        },
        {
            "polygon_index": 0,
            "ring_index": 0,
            "sequence_number": 0,
            "longitude": -122.0,
            "latitude": 37.0,
        },
        {
            "polygon_index": 0,
            "ring_index": 0,
            "sequence_number": 1,
            "longitude": -121.0,
            "latitude": 37.0,
        },
        {
            "polygon_index": 0,
            "ring_index": 0,
            "sequence_number": 3,
            "longitude": -122.0,
            "latitude": 38.0,
        },
        {
            "polygon_index": 0,
            "ring_index": 1,
            "sequence_number": 0,
            "longitude": -121.7,
            "latitude": 37.3,
        },
        {
            "polygon_index": 0,
            "ring_index": 1,
            "sequence_number": 1,
            "longitude": -121.4,
            "latitude": 37.3,
        },
        {
            "polygon_index": 0,
            "ring_index": 1,
            "sequence_number": 2,
            "longitude": -121.4,
            "latitude": 37.6,
        },
        {
            "polygon_index": 0,
            "ring_index": 1,
            "sequence_number": 3,
            "longitude": -121.7,
            "latitude": 37.6,
        },
    ]
    _install_rows(repo, rows)

    geometry = _geometry(repo)

    assert geometry["type"] == "Polygon"
    assert geometry["coordinates"][0] == [
        [-122.0, 37.0],
        [-121.0, 37.0],
        [-121.0, 38.0],
        [-122.0, 38.0],
        [-122.0, 37.0],
    ]
    assert geometry["coordinates"][1] == [
        [-121.7, 37.3],
        [-121.4, 37.3],
        [-121.4, 37.6],
        [-121.7, 37.6],
        [-121.7, 37.3],
    ]


def test_hazard_geometry_serializes_longitude_latitude(operational_repository):
    repo = operational_repository
    _install_rows(repo, _closed_square(west=-122.5, south=37.1, east=-121.2, north=38.4))

    geometry = _geometry(repo)

    assert geometry["type"] == "Polygon"
    for point in geometry["coordinates"][0]:
        assert point == [point[0], point[1]]
        assert point[0] in (-122.5, -121.2)
        assert point[1] in (37.1, 38.4)


def test_hazard_geometry_closes_open_rings(operational_repository):
    repo = operational_repository
    rows = [
        {
            "polygon_index": 0,
            "ring_index": 0,
            "sequence_number": sequence,
            "longitude": longitude,
            "latitude": latitude,
        }
        for sequence, (longitude, latitude) in enumerate(
            (
                (-122.0, 37.0),
                (-121.0, 37.0),
                (-121.0, 38.0),
                (-122.0, 38.0),
            )
        )
    ]
    _install_rows(repo, rows)

    geometry = _geometry(repo)

    assert geometry["coordinates"][0][0] == [-122.0, 37.0]
    assert geometry["coordinates"][0][-1] == [-122.0, 37.0]
    assert len(geometry["coordinates"][0]) == 5


def test_hazard_geometry_polygon_keeps_only_first_polygon(operational_repository):
    repo = operational_repository
    rows = _closed_square(
        west=-122.0,
        south=37.0,
        east=-121.0,
        north=38.0,
        polygon_index=0,
    ) + _closed_square(
        west=-120.0,
        south=36.0,
        east=-119.0,
        north=37.0,
        polygon_index=1,
    )
    _install_rows(repo, rows)

    geometry = _geometry(repo, geometry_type="POLYGON")

    assert geometry["type"] == "Polygon"
    assert len(geometry["coordinates"]) == 1
    assert geometry["coordinates"][0][0] == [-122.0, 37.0]
    assert [-120.0, 36.0] not in geometry["coordinates"][0]


def test_hazard_geometry_multipolygon_keeps_all_polygons(operational_repository):
    repo = operational_repository
    rows = _closed_square(
        west=-122.0,
        south=37.0,
        east=-121.0,
        north=38.0,
        polygon_index=0,
    ) + _closed_square(
        west=-120.0,
        south=36.0,
        east=-119.0,
        north=37.0,
        polygon_index=1,
    )
    _install_rows(repo, rows)

    geometry = _geometry(repo, geometry_type="MULTIPOLYGON")

    assert geometry["type"] == "MultiPolygon"
    assert len(geometry["coordinates"]) == 2
    assert geometry["coordinates"][0][0][0] == [-122.0, 37.0]
    assert geometry["coordinates"][1][0][0] == [-120.0, 36.0]


def test_hazard_geometry_skips_malformed_rows(operational_repository):
    repo = operational_repository
    rows = _closed_square(west=-122.0, south=37.0, east=-121.0, north=38.0)
    rows.insert(
        1,
        {
            "polygon_index": "bad",
            "ring_index": 0,
            "sequence_number": 99,
            "longitude": 0,
            "latitude": 0,
        },
    )
    rows.append(
        {
            "polygon_index": 0,
            "ring_index": 0,
            "sequence_number": 50,
            "latitude": 1,
        }
    )
    _install_rows(repo, rows)

    geometry = _geometry(repo)

    assert geometry["type"] == "Polygon"
    assert [0, 1] not in geometry["coordinates"][0]
    assert [0, 0] not in geometry["coordinates"][0]


def test_hazard_geometry_returns_none_when_no_usable_ring(operational_repository):
    repo = operational_repository
    _install_rows(
        repo,
        [
            {
                "polygon_index": 0,
                "ring_index": 0,
                "sequence_number": 0,
                "longitude": -122.0,
                "latitude": 37.0,
            },
            {
                "polygon_index": 0,
                "ring_index": 0,
                "sequence_number": 1,
                "longitude": -121.0,
                "latitude": 37.0,
            },
        ],
    )

    assert _geometry(repo) is None
