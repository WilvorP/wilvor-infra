from __future__ import annotations

import pytest


def test_normalize_ring_converts_lon_lat_to_lat_lon(
    sigmet_processor,
):
    ring = [
        [-75.0, 40.0],
        [-74.0, 40.0],
        ["bad", 41.0],
        [-75.0, 40.0],
    ]

    assert sigmet_processor.normalize_ring_lonlat_to_latlng(ring) == [
        (40.0, -75.0),
        (40.0, -74.0),
    ]


def test_polygon_to_h3_cells_builds_latlng_polygon(
    sigmet_processor,
    monkeypatch,
):
    captured = {}

    class FakePolygon:
        def __init__(self, outer, *holes):
            captured["outer"] = outer
            captured["holes"] = holes

    monkeypatch.setattr(
        sigmet_processor.h3,
        "LatLngPoly",
        FakePolygon,
    )
    monkeypatch.setattr(
        sigmet_processor.h3,
        "polygon_to_cells",
        lambda polygon, resolution: {"cell-b", "cell-a"},
    )

    coordinates = [
        [
            [-75.0, 40.0],
            [-74.0, 40.0],
            [-74.0, 41.0],
            [-75.0, 40.0],
        ],
        [
            [-74.8, 40.2],
            [-74.7, 40.2],
            [-74.7, 40.3],
            [-74.8, 40.2],
        ],
    ]

    cells = sigmet_processor.polygon_to_h3_cells(
        coordinates,
        resolution=4,
    )

    assert cells == {"cell-a", "cell-b"}
    assert captured["outer"] == [
        (40.0, -75.0),
        (40.0, -74.0),
        (41.0, -74.0),
    ]
    assert captured["holes"] == (
        [
            (40.2, -74.8),
            (40.2, -74.7),
            (40.3, -74.7),
        ],
    )


@pytest.mark.parametrize(
    "coordinates",
    [
        None,
        [],
        [[]],
        [[[-75.0, 40.0], [-74.0, 40.0]]],
    ],
)
def test_polygon_to_h3_cells_rejects_invalid_outer_ring(
    sigmet_processor,
    coordinates,
):
    with pytest.raises(sigmet_processor.PermanentRecordError):
        sigmet_processor.polygon_to_h3_cells(coordinates, 4)


def test_polygon_to_h3_cells_wraps_h3_error(
    sigmet_processor,
    monkeypatch,
):
    monkeypatch.setattr(
        sigmet_processor.h3,
        "LatLngPoly",
        lambda *args: object(),
    )
    monkeypatch.setattr(
        sigmet_processor.h3,
        "polygon_to_cells",
        lambda polygon, resolution: (_ for _ in ()).throw(
            ValueError("bad polygon")
        ),
    )

    with pytest.raises(
        sigmet_processor.PermanentRecordError,
        match="Failed to convert polygon to H3 cells",
    ):
        sigmet_processor.polygon_to_h3_cells(
            [
                [
                    [-75.0, 40.0],
                    [-74.0, 40.0],
                    [-74.0, 41.0],
                ]
            ],
            4,
        )


def test_polygon_centroid_cell_averages_coordinates(
    sigmet_processor,
    monkeypatch,
):
    captured = {}

    def fake_latlng_to_cell(lat, lon, resolution):
        captured.update(
            {
                "lat": lat,
                "lon": lon,
                "resolution": resolution,
            }
        )
        return "centroid-cell"

    monkeypatch.setattr(
        sigmet_processor.h3,
        "latlng_to_cell",
        fake_latlng_to_cell,
    )

    cell = sigmet_processor.polygon_centroid_cell(
        [
            [
                [-75.0, 40.0],
                [-73.0, 40.0],
                [-74.0, 43.0],
            ]
        ],
        4,
    )

    assert cell == "centroid-cell"
    assert captured == {
        "lat": 41.0,
        "lon": -74.0,
        "resolution": 4,
    }


def test_geometry_to_h3_cells_polygon_uses_centroid_fallback(
    sigmet_processor,
    monkeypatch,
):
    monkeypatch.setattr(
        sigmet_processor,
        "polygon_to_h3_cells",
        lambda coordinates, resolution: set(),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "polygon_centroid_cell",
        lambda coordinates, resolution: "fallback",
    )

    result = sigmet_processor.geometry_to_h3_cells(
        {
            "type": "Polygon",
            "coordinates": [[[-75.0, 40.0]]],
        },
        4,
    )

    assert result == ["fallback"]


def test_geometry_to_h3_cells_merges_multipolygon_cells(
    sigmet_processor,
    monkeypatch,
):
    outputs = iter([
        {"cell-b", "cell-a"},
        {"cell-c", "cell-b"},
    ])

    monkeypatch.setattr(
        sigmet_processor,
        "polygon_to_h3_cells",
        lambda coordinates, resolution: next(outputs),
    )

    result = sigmet_processor.geometry_to_h3_cells(
        {
            "type": "MultiPolygon",
            "coordinates": [["polygon-1"], ["polygon-2"]],
        },
        4,
    )

    assert result == ["cell-a", "cell-b", "cell-c"]


@pytest.mark.parametrize(
    ("geometry", "message"),
    [
        (None, "Geometry is missing or invalid"),
        ({"type": "LineString", "coordinates": []}, "Unsupported geometry"),
        (
            {"type": "MultiPolygon", "coordinates": "bad"},
            "MultiPolygon coordinates are missing or invalid",
        ),
    ],
)
def test_geometry_to_h3_cells_rejects_unsupported_geometry(
    sigmet_processor,
    geometry,
    message,
):
    with pytest.raises(
        sigmet_processor.PermanentRecordError,
        match=message,
    ):
        sigmet_processor.geometry_to_h3_cells(geometry, 4)

def test_flatten_geometry_points_preserves_source_order(
    sigmet_processor,
    sigmet_feature,
):
    points = sigmet_processor.flatten_geometry_points(
        sigmet_feature["geometry"]
    )

    assert len(points) == 5

    assert points[0] == {
        "geometry_type": "POLYGON",
        "polygon_index": 0,
        "ring_index": 0,
        "sequence_number": 0,
        "latitude": 40.0,
        "longitude": -75.0,
    }

    # The source closing point must be preserved.
    assert points[-1] == {
        "geometry_type": "POLYGON",
        "polygon_index": 0,
        "ring_index": 0,
        "sequence_number": 4,
        "latitude": 40.0,
        "longitude": -75.0,
    }


def test_flatten_geometry_points_supports_multipolygon(
    sigmet_processor,
):
    geometry = {
        "type": "MultiPolygon",
        "coordinates": [
            [
                [
                    [-75.0, 40.0],
                    [-74.0, 40.0],
                    [-74.0, 41.0],
                    [-75.0, 40.0],
                ]
            ],
            [
                [
                    [-80.0, 35.0],
                    [-79.0, 35.0],
                    [-79.0, 36.0],
                    [-80.0, 35.0],
                ]
            ],
        ],
    }

    points = sigmet_processor.flatten_geometry_points(geometry)

    assert len(points) == 8
    assert {point["polygon_index"] for point in points} == {0, 1}
    assert {
        point["geometry_type"]
        for point in points
    } == {"MULTIPOLYGON"}


@pytest.mark.parametrize(
    "point",
    [
        ["bad", 40.0],
        [-75.0, "bad"],
        [-181.0, 40.0],
        [-75.0, 91.0],
        [True, 40.0],
    ],
)
def test_flatten_geometry_points_rejects_invalid_coordinates(
    sigmet_processor,
    point,
):
    geometry = {
        "type": "Polygon",
        "coordinates": [
            [
                [-75.0, 40.0],
                [-74.0, 40.0],
                point,
            ]
        ],
    }

    with pytest.raises(sigmet_processor.PermanentRecordError):
        sigmet_processor.flatten_geometry_points(geometry)


def test_geometry_hash_changes_when_coordinate_order_changes(
    sigmet_processor,
    sigmet_feature,
):
    original = sigmet_processor.build_geometry_hash(
        sigmet_feature["geometry"]
    )

    changed_geometry = {
        "type": "Polygon",
        "coordinates": [
            list(
                reversed(
                    sigmet_feature["geometry"]["coordinates"][0]
                )
            )
        ],
    }

    changed = sigmet_processor.build_geometry_hash(
        changed_geometry
    )

    assert original != changed


def test_expand_impact_cells_deduplicates_and_keeps_minimum_distance(
    sigmet_processor,
    monkeypatch,
):
    rings = {
        ("cell-a", 1): {"near-a", "shared"},
        ("cell-a", 2): {"outer-a", "shared-two"},
        ("cell-b", 1): {"near-b", "shared-two"},
        ("cell-b", 2): {"outer-b"},
    }

    monkeypatch.setattr(
        sigmet_processor.h3,
        "grid_ring",
        lambda cell, distance: rings[(cell, distance)],
    )

    result = sigmet_processor.expand_impact_cells(
        ["cell-a", "cell-b"],
        max_grid_distance=2,
    )

    assert result["cell-a"] == 0
    assert result["cell-b"] == 0
    assert result["shared"] == 1

    # Appears at distance 2 from cell-a but distance 1 from cell-b.
    assert result["shared-two"] == 1

    assert result["outer-a"] == 2
    assert result["outer-b"] == 2