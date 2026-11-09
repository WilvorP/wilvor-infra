"""Direct tests for Phase 1D.2 region/hazard geospatial discovery."""

from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from wilvor_operational import current_set
from wilvor_operational import discovery
from wilvor_operational import geometry
from wilvor_operational import geospatial
from wilvor_operational import linking
from wilvor_operational import readers
from wilvor_operational import regions


REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_DIR = REPO_ROOT / "functions" / "shared" / "wilvor_operational"
SHARED_DIR = PACKAGE_DIR.parent
NOW = 1_788_661_800


class ScriptedTable:
    def __init__(self, *, records=None, query_pages=None, get_sequence=None):
        self.records = dict(records or {})
        self.query_pages = list(query_pages or [])
        self.get_sequence = list(get_sequence or [])
        self.calls = []
        self._query_index = 0
        self._get_index = 0

    def get_item(self, **kwargs):
        self.calls.append(("get_item", kwargs))
        if self.get_sequence:
            if self._get_index >= len(self.get_sequence):
                return {}
            item = self.get_sequence[self._get_index]
            self._get_index += 1
            if item is None:
                return {}
            return {"Item": item}
        key = kwargs["Key"]
        value = next(iter(key.values()))
        item = self.records.get(value)
        if item is None:
            return {}
        return {"Item": item}

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        if self._query_index < len(self.query_pages):
            page = self.query_pages[self._query_index]
            self._query_index += 1
            return page
        return {"Items": []}

    def scan(self, **kwargs):
        raise AssertionError("geospatial must not scan tables")


def _tables(*, hazards=None, coordinates=None):
    return readers.OperationalTables(
        aircraft=ScriptedTable(),
        projections=ScriptedTable(),
        projection_points=ScriptedTable(),
        hazards=hazards or ScriptedTable(),
        hazard_coordinates=coordinates or ScriptedTable(),
        encounters=ScriptedTable(),
        risks=ScriptedTable(),
        airports=ScriptedTable(),
        metar=ScriptedTable(),
        taf=ScriptedTable(),
        taf_periods=ScriptedTable(),
        airport_assessments=ScriptedTable(),
        recommendations=ScriptedTable(),
        alerts=ScriptedTable(),
    )


def _hazard(
    hazard_id,
    *,
    source_version="v1",
    product_type="SIGMET",
    hazard_type="CONVECTION",
    geometry_type="POLYGON",
    geometry_hash="hash-1",
    materialization_id="mat-1",
    valid_to_epoch=NOW + 60,
    status="ACTIVE",
    materialization_status="READY",
):
    return {
        "hazard_id": hazard_id,
        "source_version": source_version,
        "product_type": product_type,
        "hazard_type": hazard_type,
        "status": status,
        "materialization_status": materialization_status,
        "valid_to_epoch": valid_to_epoch,
        "geometry_type": geometry_type,
        "geometry_hash": geometry_hash,
        "materialization_id": materialization_id,
    }


def _expired_hazard(hazard_id, **kwargs):
    item = _hazard(hazard_id, **kwargs)
    item["valid_to_epoch"] = NOW - 60
    return item


def _square_rows(hazard, west, south, east, north):
    points = (
        (west, south),
        (east, south),
        (east, north),
        (west, north),
        (west, south),
    )
    rows = []
    for sequence, (longitude, latitude) in enumerate(points):
        rows.append(
            {
                "hazard_version_key": f"{hazard['hazard_id']}#{hazard['source_version']}",
                "hazard_id": hazard["hazard_id"],
                "source_version": hazard["source_version"],
                "geometry_type": hazard["geometry_type"],
                "geometry_hash": hazard["geometry_hash"],
                "materialization_id": hazard["materialization_id"],
                "polygon_index": 0,
                "ring_index": 0,
                "sequence_number": sequence,
                "longitude": Decimal(str(longitude)),
                "latitude": Decimal(str(latitude)),
            }
        )
    return rows


def test_unresolved_region_does_not_query():
    hazards = ScriptedTable(query_pages=[{"Items": [_hazard("h-1")]}])
    result = geospatial.discover_current_hazards_in_region(
        _tables(hazards=hazards),
        "California, USA",
        now_epoch=NOW,
        product_type="SIGMET",
    )
    assert result.region_resolution.resolved is False
    assert result.intersecting == ()
    assert result.non_intersecting == ()
    assert result.unevaluated == ()
    assert result.rejected_candidates == ()
    assert result.retrieval == ()
    assert hazards.calls == []


def test_interior_and_outside_california_hazards():
    inside = _hazard("inside")
    outside = _hazard("outside")
    hazards = ScriptedTable(
        records={"inside": inside, "outside": outside},
        query_pages=[{"Items": [inside, outside]}],
    )
    coordinates = ScriptedTable(
        query_pages=[
            {"Items": _square_rows(inside, -120.2, 36.8, -119.8, 37.2)},
            {"Items": _square_rows(outside, -74.2, 40.6, -73.8, 41.0)},
        ]
    )
    result = geospatial.discover_current_hazards_in_region(
        _tables(hazards=hazards, coordinates=coordinates),
        "CA",
        now_epoch=NOW,
        product_type="SIGMET",
    )
    assert result.region_resolution.region.code == "CA"
    assert [item.hazard_id for item in result.intersecting] == ["inside"]
    assert [item.hazard_id for item in result.non_intersecting] == ["outside"]
    assert result.unevaluated == ()
    assert result.rejected_candidates == ()
    assert result.intersecting[0].spatial_status is geometry.SpatialStatus.INTERSECTS
    assert result.non_intersecting[0].spatial_status is geometry.SpatialStatus.DOES_NOT_INTERSECT
    assert linking.NO_SNAPSHOT_LIMITATION in result.intersecting[0].limitations
    assert geometry.GEOMETRY_HASH_LINEAGE_LIMITATION in result.intersecting[0].limitations


def test_gsi_v1_pins_exact_v2_when_current():
    gsi = _hazard("h-1", source_version="v1", geometry_hash="hash-v1")
    exact = _hazard("h-1", source_version="v2", geometry_hash="hash-v2", materialization_id="mat-v2")
    hazards = ScriptedTable(
        get_sequence=[exact, exact],
        query_pages=[{"Items": [gsi]}],
    )
    coordinates = ScriptedTable(
        query_pages=[{"Items": _square_rows(exact, -120.2, 36.8, -119.8, 37.2)}]
    )
    result = geospatial.discover_current_hazards_in_region(
        _tables(hazards=hazards, coordinates=coordinates),
        "California",
        now_epoch=NOW,
        product_type="SIGMET",
    )
    assert result.intersecting[0].source_version == "v2"
    assert geospatial.GSI_EVENTUAL_VERSION_DIVERGENCE in result.intersecting[0].limitations
    assert result.rejected_candidates == ()
    assert result.unevaluated == ()


def test_rejected_candidates_are_not_unevaluated():
    gsi_missing = _hazard("missing")
    gsi_old = _hazard("old")
    gsi_product = _hazard("wrong-product", product_type="SIGMET")
    gsi_type = _hazard("wrong-type", hazard_type="CONVECTION")
    hazards = ScriptedTable(
        records={
            "old": _expired_hazard("old"),
            "wrong-product": _hazard("wrong-product", product_type="AIRMET"),
            "wrong-type": _hazard("wrong-type", hazard_type="ICING"),
        },
        query_pages=[{"Items": [gsi_missing, gsi_old, gsi_product, gsi_type]}],
    )
    result = geospatial.discover_current_hazards_in_region(
        _tables(hazards=hazards),
        "CA",
        now_epoch=NOW,
        product_type="SIGMET",
        hazard_type="CONVECTION",
    )
    reasons = {item.hazard_id: item.reason for item in result.rejected_candidates}
    assert reasons["missing"] is geospatial.RejectionReason.PARENT_MISSING
    assert reasons["old"] is geospatial.RejectionReason.NOT_CURRENT
    assert reasons["wrong-product"] is geospatial.RejectionReason.FILTER_MISMATCH
    assert reasons["wrong-type"] is geospatial.RejectionReason.FILTER_MISMATCH
    assert result.unevaluated == ()
    assert result.intersecting == ()
    assert result.non_intersecting == ()


def test_pinned_current_missing_geometry_is_unevaluated():
    hazard = _hazard("h-1")
    hazards = ScriptedTable(
        records={"h-1": hazard},
        query_pages=[{"Items": [hazard]}],
    )
    result = geospatial.discover_current_hazards_in_region(
        _tables(hazards=hazards, coordinates=ScriptedTable(query_pages=[{"Items": []}])),
        "CA",
        now_epoch=NOW,
        product_type="SIGMET",
    )
    assert len(result.unevaluated) == 1
    assert result.unevaluated[0].spatial_status is geometry.SpatialStatus.UNEVALUATED
    assert result.non_intersecting == ()
    assert result.rejected_candidates == ()


def test_missing_source_version_is_unevaluated_without_coordinate_query():
    hazard = _hazard("h-1")
    hazard["source_version"] = ""
    coordinates = ScriptedTable()
    hazards = ScriptedTable(
        records={"h-1": hazard},
        query_pages=[{"Items": [hazard]}],
    )
    result = geospatial.discover_current_hazards_in_region(
        _tables(hazards=hazards, coordinates=coordinates),
        "CA",
        now_epoch=NOW,
        product_type="SIGMET",
    )
    assert result.unevaluated[0].spatial_status is geometry.SpatialStatus.UNEVALUATED
    assert geospatial.MISSING_SOURCE_VERSION in result.unevaluated[0].limitations
    assert coordinates.calls == []


def test_final_drift_discards_provisional_true_and_false():
    inside = _hazard("inside")
    outside = _hazard("outside")
    hazards = ScriptedTable(
        get_sequence=[
            inside,
            _hazard("inside", source_version="v2"),
            outside,
            _hazard("outside", source_version="v2"),
        ],
        query_pages=[{"Items": [inside, outside]}],
    )
    coordinates = ScriptedTable(
        query_pages=[
            {"Items": _square_rows(inside, -120.2, 36.8, -119.8, 37.2)},
            {"Items": _square_rows(outside, -74.2, 40.6, -73.8, 41.0)},
        ]
    )
    result = geospatial.discover_current_hazards_in_region(
        _tables(hazards=hazards, coordinates=coordinates),
        "California",
        now_epoch=NOW,
        product_type="SIGMET",
    )
    assert result.intersecting == ()
    assert result.non_intersecting == ()
    assert {item.hazard_id for item in result.unevaluated} == {"inside", "outside"}
    assert all(
        item.spatial_status is geometry.SpatialStatus.UNEVALUATED
        for item in result.unevaluated
    )
    assert all(
        geospatial.FINAL_PARENT_DRIFT in item.limitations
        for item in result.unevaluated
    )
    assert len([call for call in coordinates.calls if call[0] == "query"]) == 2


def test_malformed_coordinates_and_hash_mismatch_are_unevaluated():
    malformed = _hazard("malformed")
    hashed = _hazard("hashed", geometry_hash="parent-hash")
    bad_rows = _square_rows(malformed, -120.2, 36.8, -119.8, 37.2)
    bad_rows[1]["longitude"] = True
    hash_rows = _square_rows(hashed, -120.2, 36.8, -119.8, 37.2)
    hash_rows[0]["geometry_hash"] = "child-hash"
    hazards = ScriptedTable(
        records={"malformed": malformed, "hashed": hashed},
        query_pages=[{"Items": [malformed, hashed]}],
    )
    coordinates = ScriptedTable(
        query_pages=[{"Items": bad_rows}, {"Items": hash_rows}]
    )
    result = geospatial.discover_current_hazards_in_region(
        _tables(hazards=hazards, coordinates=coordinates),
        "CA",
        now_epoch=NOW,
        product_type="SIGMET",
    )
    assert {item.hazard_id for item in result.unevaluated} == {"malformed", "hashed"}
    assert result.non_intersecting == ()
    assert result.intersecting == ()


def test_alaska_supported_or_unevaluated_not_outside():
    hazard = _hazard("h-1")
    hazards = ScriptedTable(
        records={"h-1": hazard},
        query_pages=[{"Items": [hazard]}],
    )
    coordinates = ScriptedTable(
        query_pages=[{"Items": _square_rows(hazard, -120.2, 36.8, -119.8, 37.2)}]
    )
    result = geospatial.discover_current_hazards_in_region(
        _tables(hazards=hazards, coordinates=coordinates),
        "Alaska",
        now_epoch=NOW,
        product_type="SIGMET",
    )
    region = result.region_resolution.region
    assert region.code == "AK"
    if region.spatial_supported:
        assert result.unevaluated == ()
        assert result.non_intersecting[0].spatial_status is geometry.SpatialStatus.DOES_NOT_INTERSECT
    else:
        assert result.unevaluated[0].spatial_status is geometry.SpatialStatus.UNEVALUATED
        assert result.non_intersecting == ()
        assert result.intersecting == ()


def test_coordinate_query_is_consistent_full_query():
    hazard = _hazard("h-1")
    hazards = ScriptedTable(
        records={"h-1": hazard},
        query_pages=[{"Items": [hazard]}],
    )
    coordinates = ScriptedTable(
        query_pages=[{"Items": _square_rows(hazard, -120.2, 36.8, -119.8, 37.2)}]
    )
    result = geospatial.discover_current_hazards_in_region(
        _tables(hazards=hazards, coordinates=coordinates),
        "CA",
        now_epoch=NOW,
        product_type="SIGMET",
    )
    coord_obs = [
        observation
        for observation in result.retrieval
        if observation.source == "query_hazard_coordinate_rows"
    ]
    assert coord_obs
    assert coord_obs[0].coverage is linking.Coverage.FULL_QUERY
    assert coord_obs[0].consistency is linking.Consistency.CONSISTENT
    assert coord_obs[0].limit is None
    assert linking.EVENTUAL_SCAN_LIMITATION not in coord_obs[0].limitations


def test_does_not_compose_impacts_or_import_ai():
    source = inspect.getsource(geospatial)
    assert "build_hazard_operational_context" not in source
    assert "discover_current_encounters" not in source
    tree = ast.parse((PACKAGE_DIR / "geospatial.py").read_text(encoding="utf-8"))
    imported = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert "wilvor_ai" not in imported


def test_runtime_modules_do_not_import_shapely():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    init_path = (PACKAGE_DIR / "__init__.py").as_posix()
    script = f"""
import sys
modules = [
    'wilvor_operational',
    'wilvor_operational.current_set',
    'wilvor_operational.access',
    'wilvor_operational.readers',
    'wilvor_operational.linking',
    'wilvor_operational.context',
    'wilvor_operational.discovery',
    'wilvor_operational.regions',
    'wilvor_operational.observed',
    'wilvor_operational.query',
]
for name in modules:
    __import__(name)
assert not any(
    module == 'shapely' or module.startswith('shapely.')
    for module in sys.modules
), sorted(module for module in sys.modules if 'shapely' in module)
from pathlib import Path
text = Path({init_path!r}).read_text(encoding='utf-8')
assert 'regions' not in text
assert 'geometry' not in text
assert 'geospatial' not in text
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_discovery_source_remains_shapely_free():
    source = inspect.getsource(discovery)
    assert "shapely" not in source
    assert "geospatial" not in source
    assert "shapely" not in inspect.getsource(current_set)
    assert "shapely" not in inspect.getsource(regions)
