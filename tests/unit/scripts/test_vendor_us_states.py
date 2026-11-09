"""Deterministic tests for Census KML → GeoJSON vendoring. No network."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "scripts" / "vendor_us_states.py"


def load_vendor():
    spec = importlib.util.spec_from_file_location("vendor_us_states", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def kml_document(placemarks: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
<Schema name="cb_test" id="kml_schema">
<SimpleField type="xsd:string" name="STUSPS"/>
<SimpleField type="xsd:string" name="NAME"/>
</Schema>
{placemarks}
</Document>
</kml>
""".encode("utf-8")


def simple_data(code: str, name: str) -> str:
    return f"""<ExtendedData>
<SchemaData schemaUrl="#kml_schema">
<SimpleData name="STUSPS">{code}</SimpleData>
<SimpleData name="NAME">{name}</SimpleData>
</SchemaData>
</ExtendedData>"""


def closed_coords(*points: tuple[float, float], altitude: str = "0.0") -> str:
    tokens = [f"{lon},{lat},{altitude}" for lon, lat in points]
    tokens.append(f"{points[0][0]},{points[0][1]},{altitude}")
    return " ".join(tokens)


def polygon_xml(coords: str, holes: tuple[str, ...] = ()) -> str:
    inner = "".join(
        f"<innerBoundaryIs><LinearRing><coordinates>{hole}</coordinates></LinearRing></innerBoundaryIs>"
        for hole in holes
    )
    return (
        "<Polygon><extrude>0</extrude><tessellate>1</tessellate>"
        "<altitudeMode>clampToGround</altitudeMode>"
        f"<outerBoundaryIs><LinearRing><coordinates>{coords}</coordinates></LinearRing></outerBoundaryIs>"
        f"{inner}</Polygon>"
    )


def placemark(code: str, name: str, geometry_xml: str) -> str:
    return (
        f"<Placemark><name>{name}</name>{simple_data(code, name)}"
        f"{geometry_xml}</Placemark>"
    )


def zip_kml(kml_bytes: bytes, name: str = "cb_test.kml") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, kml_bytes)
    return buffer.getvalue()


def write_zip(path: Path, kml_bytes: bytes) -> Path:
    path.write_bytes(zip_kml(kml_bytes))
    return path


def test_parse_ring_rejects_malformed_and_unclosed():
    vendor = load_vendor()
    closed = closed_coords((-1.0, 1.0), (1.0, 1.0), (1.0, 2.0), (-1.0, 2.0))
    assert vendor.parse_kml_ring(closed, label="ok")[0] == [-1.0, 1.0]
    with pytest.raises(vendor.VendoringError, match="malformed"):
        vendor.parse_kml_ring("1.0 2.0", label="bad")
    with pytest.raises(vendor.VendoringError, match="not closed"):
        vendor.parse_kml_ring("0,0,0.0 1,0,0.0 1,1,0.0", label="open")
    with pytest.raises(vendor.VendoringError, match="out of range"):
        vendor.parse_kml_ring("181,0,0.0 0,0,0.0 0,1,0.0 181,0,0.0", label="lon")
    with pytest.raises(vendor.VendoringError, match="non-zero altitude"):
        vendor.parse_kml_ring("0,0,1.0 1,0,1.0 1,1,1.0 0,0,1.0", label="alt")


def test_polygon_and_hole_and_multigeometry(tmp_path: Path):
    vendor = load_vendor()
    outer = closed_coords((-2.0, 0.0), (2.0, 0.0), (2.0, 2.0), (-2.0, 2.0))
    hole = closed_coords((-0.5, 0.5), (0.5, 0.5), (0.5, 1.0), (-0.5, 1.0))
    other = closed_coords((10.0, 10.0), (11.0, 10.0), (11.0, 11.0), (10.0, 11.0))
    kml = kml_document(
        placemark("CA", "California", polygon_xml(outer, (hole,)))
        + placemark(
            "AK",
            "Alaska",
            f"<MultiGeometry>{polygon_xml(outer)}{polygon_xml(other)}</MultiGeometry>",
        )
        + "".join(
            placemark(code, name, polygon_xml(other))
            for code, name in vendor.US_STATES.items()
            if code not in {"CA", "AK"}
        )
        + placemark("DC", "District of Columbia", polygon_xml(other))
    )
    collection, report = vendor.convert_kml_bytes(kml)
    assert collection["type"] == "FeatureCollection"
    assert len(collection["features"]) == 50
    codes = [feature["properties"]["code"] for feature in collection["features"]]
    assert codes == sorted(vendor.US_STATES)
    california = next(
        feature
        for feature in collection["features"]
        if feature["properties"]["code"] == "CA"
    )
    assert california["geometry"]["type"] == "Polygon"
    assert len(california["geometry"]["coordinates"]) == 2
    alaska = next(
        feature
        for feature in collection["features"]
        if feature["properties"]["code"] == "AK"
    )
    assert alaska["geometry"]["type"] == "MultiPolygon"
    assert report["feature_count"] == 50
    assert "DC" not in codes


def test_unsupported_multigeometry_child_fails():
    vendor = load_vendor()
    coords = closed_coords((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    kml = kml_document(
        "".join(
            placemark(
                code,
                name,
                f"<MultiGeometry>{polygon_xml(coords)}<Point><coordinates>0,0,0.0</coordinates></Point></MultiGeometry>",
            )
            if code == "CA"
            else placemark(code, name, polygon_xml(coords))
            for code, name in vendor.US_STATES.items()
        )
    )
    with pytest.raises(vendor.VendoringError, match="unsupported"):
        vendor.convert_kml_bytes(kml)


def test_wrong_name_and_unmapped_code_fail():
    vendor = load_vendor()
    coords = closed_coords((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    kml = kml_document(
        "".join(
            placemark(code, "Calif" if code == "CA" else name, polygon_xml(coords))
            for code, name in vendor.US_STATES.items()
        )
    )
    with pytest.raises(vendor.VendoringError, match="does not match"):
        vendor.convert_kml_bytes(kml)

    kml_unknown = kml_document(
        "".join(
            placemark(code, name, polygon_xml(coords))
            for code, name in vendor.US_STATES.items()
        )
        + placemark("XX", "Nowhere", polygon_xml(coords))
    )
    with pytest.raises(vendor.VendoringError, match="unmapped"):
        vendor.convert_kml_bytes(kml_unknown)


def test_end_to_end_zip_is_byte_deterministic(tmp_path: Path):
    vendor = load_vendor()
    coords = closed_coords((-120.0, 36.0), (-119.0, 36.0), (-119.0, 37.0), (-120.0, 37.0))
    kml = kml_document(
        "".join(
            placemark(code, name, polygon_xml(coords))
            for code, name in vendor.US_STATES.items()
        )
        + placemark("PR", "Puerto Rico", polygon_xml(coords))
    )
    source_zip = write_zip(tmp_path / "source.zip", kml)
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    argv = [
        "python",
        "scripts/vendor_us_states.py",
        "--source-zip",
        str(source_zip),
        "--source-url",
        "https://example.test/kml.zip",
        "--retrieval-date",
        "2026-09-07",
        "--output-dir",
        str(first_dir),
    ]
    first = vendor.vendor_us_states(
        source_zip=source_zip,
        source_url="https://example.test/kml.zip",
        retrieval_date="2026-09-07",
        output_dir=first_dir,
        argv=argv,
    )
    second = vendor.vendor_us_states(
        source_zip=source_zip,
        source_url="https://example.test/kml.zip",
        retrieval_date="2026-09-07",
        output_dir=second_dir,
        argv=argv,
    )
    geojson = (first_dir / "us_states.geojson").read_bytes()
    assert first["vendored_geojson_sha256"] == hashlib.sha256(geojson).hexdigest()
    assert geojson == (second_dir / "us_states.geojson").read_bytes()
    assert first["vendored_geojson_sha256"] == second["vendored_geojson_sha256"]
    assert first["reprojection"] == "none"
    parsed = json.loads(geojson)
    assert parsed["features"][0]["properties"]["code"] == "AK"
    assert "PR" not in {
        feature["properties"]["code"] for feature in parsed["features"]
    }
