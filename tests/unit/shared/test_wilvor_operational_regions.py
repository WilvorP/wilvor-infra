"""Direct tests for Phase 1D.2 Shapely-free U.S. state region resolution."""

from __future__ import annotations

import ast
import hashlib
import inspect
from pathlib import Path

import pytest

from wilvor_operational import regions


REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_DIR = REPO_ROOT / "functions" / "shared" / "wilvor_operational"
DATA_DIR = PACKAGE_DIR / "data"


def test_regions_module_is_shapely_free():
    source = inspect.getsource(regions)
    assert "shapely" not in source
    assert "wilvor_ai" not in source
    tree = ast.parse((PACKAGE_DIR / "regions.py").read_text(encoding="utf-8"))
    imported = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module.split(".")[0])
    assert "shapely" not in imported
    assert "geometry" not in imported
    assert "geospatial" not in imported


def test_resolve_california_aliases():
    california = regions.resolve_region("California")
    assert california.resolved is True
    assert california.region is not None
    assert california.region.code == "CA"
    assert california.region.name == "California"
    assert california.region.region_type == regions.REGION_TYPE_US_STATE
    assert california.region.dataset_vintage == "2025"
    assert regions.resolve_region("CA").region.code == "CA"
    assert regions.resolve_region("ca").region.code == "CA"
    assert regions.resolve_region("  California  ").region.code == "CA"
    assert regions.resolve_region("california").region is california.region


def test_resolve_region_rejects_invalid_queries():
    for text in ("", "   ", "California, USA", "Los Angeles", "Calif", "unknown"):
        result = regions.resolve_region(text)
        assert result.resolved is False
        assert result.region is None


def test_dataset_contains_exactly_fifty_unique_states():
    dataset = regions.load_us_states_dataset()
    assert len(dataset.regions) == 50
    codes = [region.code for region in dataset.regions]
    names = [region.name for region in dataset.regions]
    assert len(set(codes)) == 50
    assert len(set(names)) == 50
    assert "DC" not in codes
    assert "PR" not in codes
    assert "AS" not in codes
    assert "GU" not in codes
    assert "MP" not in codes
    assert "VI" not in codes
    assert "CA" in dataset.by_code
    california = dataset.by_code["CA"]
    assert california.geometry_type in {"Polygon", "MultiPolygon"}
    assert california.coordinates
    assert california.spatial_supported is True


def test_metadata_sha_matches_geojson_bytes():
    dataset = regions.load_us_states_dataset()
    geojson_bytes = (DATA_DIR / "us_states.geojson").read_bytes()
    digest = hashlib.sha256(geojson_bytes).hexdigest()
    assert digest == dataset.metadata["vendored_geojson_sha256"]
    assert digest == dataset.by_code["CA"].dataset_sha256
    assert dataset.metadata["vintage"] == "2025"
    assert dataset.metadata["reprojection"] == "none"
    assert "NAD83" not in dataset.metadata["source_crs"]
    assert dataset.metadata["feature_count"] == 50
    assert dataset.metadata["target_crs"]


def test_region_coordinates_are_immutable_tuples():
    region = regions.resolve_region("CA").region
    assert isinstance(region.coordinates, tuple)
    first = region.coordinates[0]
    assert isinstance(first, tuple)
    with pytest.raises(TypeError):
        region.coordinates[0] = ()
    original = region.coordinates
    dataset = regions.load_us_states_dataset()
    with pytest.raises((TypeError, AttributeError)):
        dataset.by_code["CA"] = region
    later = regions.resolve_region("California").region
    assert later.coordinates is original
    assert later is region


def test_alaska_resolves_and_records_spatial_support():
    result = regions.resolve_region("AK")
    assert result.resolved is True
    assert result.region.name == "Alaska"
    dataset = regions.load_us_states_dataset()
    unsupported = set(dataset.metadata.get("spatial_unsupported_codes") or ())
    if unsupported:
        assert result.region.spatial_supported is False
        assert "AK" in unsupported
    else:
        assert result.region.spatial_supported is True
        assert dataset.metadata["alaska_antimeridian_crossings"] == 0


def test_vendored_coordinates_are_in_range():
    dataset = regions.load_us_states_dataset()
    for region in dataset.regions:
        assert region.geometry_type in {"Polygon", "MultiPolygon"}
        polygons = (
            region.coordinates
            if region.geometry_type == "MultiPolygon"
            else (region.coordinates,)
        )
        for polygon in polygons:
            assert polygon
            for ring in polygon:
                assert len(ring) >= 4
                assert ring[0] == ring[-1]
                for longitude, latitude in ring:
                    assert -180.0 <= longitude <= 180.0
                    assert -90.0 <= latitude <= 90.0

