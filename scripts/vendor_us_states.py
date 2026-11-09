"""One-time stdlib vendoring of official Census state Cartographic Boundary KML.

Development utility only. No network. No Shapely/GDAL/Fiona/geopandas/pyproj.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


KML_NS = "http://www.opengis.net/kml/2.2"
KML_NS_TAG = f"{{{KML_NS}}}"

GEOJSON_SEPARATORS = (",", ":")

# Explicit approved V1 scope. Names are official Census/USPS state names.
US_STATES: dict[str, str] = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}

EXCLUDED_EQUIVALENTS: dict[str, str] = {
    "DC": "District of Columbia",
    "PR": "Puerto Rico",
    "AS": "American Samoa",
    "GU": "Guam",
    "MP": "Commonwealth of the Northern Mariana Islands",
    "VI": "United States Virgin Islands",
}

ANTIMERIDIAN_SPAN = 180.0


class VendoringError(ValueError):
    """Fatal official-source conversion failure."""


def kml_tag(local_name: str) -> str:
    return f"{KML_NS_TAG}{local_name}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def dump_deterministic_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=GEOJSON_SEPARATORS,
    ) + "\n"


def parse_kml_ring(coordinates_text: str, *, label: str) -> list[list[float]]:
    ring: list[list[float]] = []
    tokens = (coordinates_text or "").split()
    if not tokens:
        raise VendoringError(f"{label}: empty LinearRing coordinates")

    for index, token in enumerate(tokens):
        parts = token.split(",")
        if len(parts) < 2 or len(parts) > 3:
            raise VendoringError(
                f"{label}: malformed coordinate tuple at {index}: {token!r}"
            )
        if any(part.strip() == "" for part in parts[:2]):
            raise VendoringError(
                f"{label}: empty longitude/latitude at {index}: {token!r}"
            )
        try:
            longitude = float(parts[0])
            latitude = float(parts[1])
        except ValueError as exc:
            raise VendoringError(
                f"{label}: non-numeric coordinate at {index}: {token!r}"
            ) from exc
        if not -180.0 <= longitude <= 180.0:
            raise VendoringError(
                f"{label}: longitude out of range at {index}: {longitude}"
            )
        if not -90.0 <= latitude <= 90.0:
            raise VendoringError(
                f"{label}: latitude out of range at {index}: {latitude}"
            )
        if len(parts) == 3:
            try:
                altitude = float(parts[2])
            except ValueError as exc:
                raise VendoringError(
                    f"{label}: non-numeric altitude at {index}: {token!r}"
                ) from exc
            if altitude != 0.0:
                raise VendoringError(
                    f"{label}: non-zero altitude is not a 2D boundary: {altitude}"
                )
        ring.append([longitude, latitude])

    if ring[0] != ring[-1]:
        raise VendoringError(f"{label}: LinearRing is not closed")
    if len(ring) < 4:
        raise VendoringError(f"{label}: LinearRing has fewer than four positions")
    return ring


def ring_antimeridian_crossings(ring: list[list[float]]) -> int:
    crossings = 0
    for first, second in zip(ring, ring[1:]):
        if abs(first[0] - second[0]) > ANTIMERIDIAN_SPAN:
            crossings += 1
    return crossings


def convert_kml_polygon(polygon: ET.Element, *, label: str) -> list[list[list[float]]]:
    outer = polygon.find(kml_tag("outerBoundaryIs"))
    if outer is None:
        raise VendoringError(f"{label}: Polygon missing outerBoundaryIs")
    outer_coords = outer.find(f".//{kml_tag('coordinates')}")
    if outer_coords is None or outer_coords.text is None:
        raise VendoringError(f"{label}: Polygon missing outer coordinates")
    rings = [
        parse_kml_ring(outer_coords.text, label=f"{label} outer"),
    ]
    for hole_index, inner in enumerate(polygon.findall(kml_tag("innerBoundaryIs"))):
        inner_coords = inner.find(f".//{kml_tag('coordinates')}")
        if inner_coords is None or inner_coords.text is None:
            raise VendoringError(f"{label}: hole {hole_index} missing coordinates")
        rings.append(
            parse_kml_ring(
                inner_coords.text,
                label=f"{label} inner {hole_index}",
            )
        )
    return rings


def convert_kml_geometry(placemark: ET.Element, *, code: str) -> dict:
    children = list(placemark)
    geometry_nodes = [
        child
        for child in children
        if child.tag in (kml_tag("Polygon"), kml_tag("MultiGeometry"))
    ]
    if len(geometry_nodes) != 1:
        raise VendoringError(
            f"{code}: expected exactly one Polygon or MultiGeometry child, "
            f"found {len(geometry_nodes)}"
        )
    node = geometry_nodes[0]
    if node.tag == kml_tag("Polygon"):
        return {
            "type": "Polygon",
            "coordinates": convert_kml_polygon(node, label=f"{code} polygon 0"),
        }

    polygons: list[list[list[list[float]]]] = []
    for child_index, child in enumerate(list(node)):
        if child.tag != kml_tag("Polygon"):
            raise VendoringError(
                f"{code}: MultiGeometry child {child_index} is unsupported "
                f"({child.tag})"
            )
        polygons.append(
            convert_kml_polygon(child, label=f"{code} polygon {child_index}")
        )
    if not polygons:
        raise VendoringError(f"{code}: MultiGeometry contains no Polygon children")
    if len(polygons) == 1:
        return {
            "type": "Polygon",
            "coordinates": polygons[0],
        }
    return {
        "type": "MultiPolygon",
        "coordinates": polygons,
    }


def simple_data_map(placemark: ET.Element) -> dict[str, str]:
    values: dict[str, str] = {}
    for node in placemark.findall(f".//{kml_tag('SimpleData')}"):
        name = node.get("name")
        if not name:
            continue
        values[name] = (node.text or "").strip()
    return values


def iter_rings(geometry: dict) -> list[list[list[float]]]:
    if geometry["type"] == "Polygon":
        return list(geometry["coordinates"])
    rings: list[list[list[float]]] = []
    for polygon in geometry["coordinates"]:
        rings.extend(polygon)
    return rings


def inspect_kml_document(root: ET.Element) -> dict[str, str]:
    if root.tag != kml_tag("kml"):
        raise VendoringError(f"unexpected KML root tag: {root.tag}")
    return {
        "namespace": KML_NS,
        "root_local_name": "kml",
    }


def extract_kml_from_zip(source_zip: Path) -> tuple[str, bytes]:
    with zipfile.ZipFile(source_zip) as archive:
        kml_names = [
            name
            for name in archive.namelist()
            if name.lower().endswith(".kml")
        ]
        if len(kml_names) != 1:
            raise VendoringError(
                f"expected exactly one .kml member, found {kml_names!r}"
            )
        return kml_names[0], archive.read(kml_names[0])


def convert_kml_bytes(kml_bytes: bytes) -> tuple[dict, dict]:
    root = ET.fromstring(kml_bytes)
    inspect_kml_document(root)
    selected: dict[str, dict] = {}
    excluded: list[str] = []
    unknown: list[str] = []

    for placemark in root.findall(f".//{kml_tag('Placemark')}"):
        fields = simple_data_map(placemark)
        code = fields.get("STUSPS", "")
        name = fields.get("NAME", "")
        if not code or not name:
            raise VendoringError(
                "Placemark missing STUSPS or NAME SimpleData"
            )
        if code in EXCLUDED_EQUIVALENTS:
            excluded.append(code)
            continue
        if code not in US_STATES:
            unknown.append(code)
            continue
        if name != US_STATES[code]:
            raise VendoringError(
                f"{code}: NAME {name!r} does not match official {US_STATES[code]!r}"
            )
        if code in selected:
            raise VendoringError(f"duplicate STUSPS {code}")
        selected[code] = {
            "type": "Feature",
            "properties": {
                "code": code,
                "name": name,
            },
            "geometry": convert_kml_geometry(placemark, code=code),
        }

    if unknown:
        raise VendoringError(
            "unmapped Census state-equivalent codes: " + ", ".join(sorted(unknown))
        )
    missing = sorted(set(US_STATES) - set(selected))
    if missing:
        raise VendoringError("missing required states: " + ", ".join(missing))
    if len(selected) != 50:
        raise VendoringError(f"expected 50 states, found {len(selected)}")

    features = [selected[code] for code in sorted(selected)]
    collection = {
        "type": "FeatureCollection",
        "features": features,
    }

    alaska = selected["AK"]["geometry"]
    alaska_crossings = sum(
        ring_antimeridian_crossings(ring) for ring in iter_rings(alaska)
    )
    california = selected["CA"]["geometry"]
    if not iter_rings(california):
        raise VendoringError("California geometry is empty")

    report = {
        "feature_count": 50,
        "excluded_codes": sorted(set(excluded)),
        "alaska_antimeridian_crossings": alaska_crossings,
        "alaska_spatial_supported": alaska_crossings == 0,
        "california_geometry_type": california["type"],
        "alaska_geometry_type": alaska["type"],
    }
    return collection, report


def build_metadata(
    *,
    source_url: str,
    retrieval_date: str,
    source_zip: Path,
    source_sha256: str,
    source_zip_bytes: int,
    internal_kml: str,
    geojson_path: Path,
    geojson_sha256: str,
    geojson_bytes: int,
    report: dict,
    invocation: list[str],
) -> dict:
    unsupported = [] if report["alaska_spatial_supported"] else ["AK"]
    return {
        "source_organization": "U.S. Census Bureau",
        "dataset_product": "Cartographic Boundary Files — State and Equivalent",
        "series": "GENZ2025",
        "vintage": "2025",
        "source_url": source_url,
        "retrieval_date": retrieval_date,
        "license": (
            "U.S. government work. Public domain. Acknowledge the U.S. Census Bureau."
        ),
        "source_format": "KML",
        "source_kml_zip_sha256": source_sha256,
        "source_kml_zip_bytes": source_zip_bytes,
        "internal_kml_file": internal_kml,
        "kml_namespace": KML_NS,
        "source_crs": (
            "KML geodetic longitude/latitude, WGS84-compatible KML CRS"
        ),
        "target_format": "GeoJSON",
        "target_crs": "WGS84-compatible GeoJSON longitude/latitude (RFC 7946)",
        "reprojection": "none",
        "transformation": (
            "Structural KML Polygon/MultiGeometry to GeoJSON Polygon/MultiPolygon"
        ),
        "altitude_handling": (
            "All Census boundary tuples were longitude,latitude,altitude with "
            "altitude 0.0 and altitudeMode clampToGround; altitude was discarded "
            "after that confirmation and is not part of the 2D region geometry."
        ),
        "included_scope": "50 U.S. states",
        "excluded_equivalents": sorted(EXCLUDED_EQUIVALENTS),
        "excluded_codes_observed": report["excluded_codes"],
        "feature_count": 50,
        "simplification": "Census Cartographic Boundary 1:500,000; no additional simplification",
        "vendored_geojson": geojson_path.name,
        "vendored_geojson_sha256": geojson_sha256,
        "vendored_geojson_bytes": geojson_bytes,
        "transformation_script": "scripts/vendor_us_states.py",
        "script_invocation": invocation,
        "source_zip_name": source_zip.name,
        "alaska_antimeridian_crossings": report["alaska_antimeridian_crossings"],
        "spatial_unsupported_codes": unsupported,
        "california_geometry_type": report["california_geometry_type"],
        "alaska_geometry_type": report["alaska_geometry_type"],
    }


def render_source_markdown(metadata: dict) -> str:
    unsupported = metadata["spatial_unsupported_codes"]
    alaska_line = (
        "Alaska spatial support: supported (no adjacent-ring antimeridian crossings)."
        if not unsupported
        else (
            "Alaska spatial support: unsupported under planar lon/lat Shapely "
            f"(antimeridian crossings={metadata['alaska_antimeridian_crossings']})."
        )
    )
    return f"""# U.S. state region geometry provenance

A V1 region such as California means the geographic state
polygon/multipolygon in this vendored U.S. Census Cartographic Boundary
dataset at the recorded vintage and structural KML-to-GeoJSON
transformation. It does not mean FAA airspace, ARTCC, FIR, an operational
aviation region, ImpactCells, HazardCells, an H3 region, or arbitrary
proximity to the named place. Spatial results are relative to this
dataset/version only.

## Source

- Organization: {metadata['source_organization']}
- Product: {metadata['dataset_product']}
- Series: {metadata['series']}
- Vintage: {metadata['vintage']}
- Scale: 1:500,000
- Official URL: {metadata['source_url']}
- Retrieval date: {metadata['retrieval_date']}
- License: {metadata['license']}
- Source format: {metadata['source_format']}
- Internal KML file: {metadata['internal_kml_file']}
- KML namespace: {metadata['kml_namespace']}
- Source KML ZIP SHA-256: `{metadata['source_kml_zip_sha256']}`
- Source KML ZIP bytes: {metadata['source_kml_zip_bytes']}

## Coordinates

- Source coordinate system: {metadata['source_crs']}
- Target format: {metadata['target_format']}
- Target coordinate convention: {metadata['target_crs']}
- Reprojection: {metadata['reprojection']}
- Transformation: {metadata['transformation']}
- Altitude: {metadata['altitude_handling']}
- Simplification: {metadata['simplification']}

## Scope

- Included: {metadata['included_scope']}
- Excluded Census state-equivalents: {', '.join(metadata['excluded_equivalents'])}
- Feature count: {metadata['feature_count']}
- {alaska_line}

## Vendored artifact

- File: {metadata['vendored_geojson']}
- SHA-256: `{metadata['vendored_geojson_sha256']}`
- Bytes: {metadata['vendored_geojson_bytes']}
- Script: {metadata['transformation_script']}
- Invocation: `{' '.join(metadata['script_invocation'])}`

Census Bureau Cartographic Boundary Files are simplified representations
of selected geographic areas from the MAF/TIGER System. Acknowledge the
U.S. Census Bureau.
"""


def vendor_us_states(
    *,
    source_zip: Path,
    source_url: str,
    retrieval_date: str,
    output_dir: Path,
    argv: list[str],
) -> dict:
    source_zip = source_zip.resolve()
    output_dir = output_dir.resolve()
    if not source_zip.is_file():
        raise VendoringError(f"source zip not found: {source_zip}")
    source_bytes = source_zip.read_bytes()
    source_sha256 = sha256_bytes(source_bytes)
    internal_kml, kml_bytes = extract_kml_from_zip(source_zip)
    collection, report = convert_kml_bytes(kml_bytes)

    output_dir.mkdir(parents=True, exist_ok=True)
    geojson_path = output_dir / "us_states.geojson"
    geojson_text = dump_deterministic_json(collection)
    geojson_data = geojson_text.encode("utf-8")
    geojson_path.write_bytes(geojson_data)

    metadata = build_metadata(
        source_url=source_url,
        retrieval_date=retrieval_date,
        source_zip=source_zip,
        source_sha256=source_sha256,
        source_zip_bytes=len(source_bytes),
        internal_kml=internal_kml,
        geojson_path=geojson_path,
        geojson_sha256=sha256_bytes(geojson_data),
        geojson_bytes=len(geojson_data),
        report=report,
        invocation=argv,
    )
    meta_path = output_dir / "us_states.meta.json"
    meta_path.write_text(dump_deterministic_json(metadata), encoding="utf-8")
    source_md = output_dir / "SOURCE.md"
    source_md.write_text(render_source_markdown(metadata), encoding="utf-8")
    return metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Vendor official Census state Cartographic Boundary KML."
    )
    parser.add_argument("--source-zip", required=True, type=Path)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--retrieval-date", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = [
        "python",
        "scripts/vendor_us_states.py",
        "--source-zip",
        str(args.source_zip),
        "--source-url",
        args.source_url,
        "--retrieval-date",
        args.retrieval_date,
        "--output-dir",
        str(args.output_dir),
    ]
    vendor_us_states(
        source_zip=args.source_zip,
        source_url=args.source_url,
        retrieval_date=args.retrieval_date,
        output_dir=args.output_dir,
        argv=command,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
