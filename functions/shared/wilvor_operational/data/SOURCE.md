# U.S. state region geometry provenance

A V1 region such as California means the geographic state
polygon/multipolygon in this vendored U.S. Census Cartographic Boundary
dataset at the recorded vintage and structural KML-to-GeoJSON
transformation. It does not mean FAA airspace, ARTCC, FIR, an operational
aviation region, ImpactCells, HazardCells, an H3 region, or arbitrary
proximity to the named place. Spatial results are relative to this
dataset/version only.

## Source

- Organization: U.S. Census Bureau
- Product: Cartographic Boundary Files — State and Equivalent
- Series: GENZ2025
- Vintage: 2025
- Scale: 1:500,000
- Official URL: https://www2.census.gov/geo/tiger/GENZ2025/kml/cb_2025_us_state_500k.zip
- Retrieval date: 2026-09-07
- License: U.S. government work. Public domain. Acknowledge the U.S. Census Bureau.
- Source format: KML
- Internal KML file: cb_2025_us_state_500k.kml
- KML namespace: http://www.opengis.net/kml/2.2
- Source KML ZIP SHA-256: `4295a503ff47e6787adfb520ad6706557e41c2ce57ced5a134087f8e35143a02`
- Source KML ZIP bytes: 2525312

## Coordinates

- Source coordinate system: KML geodetic longitude/latitude, WGS84-compatible KML CRS
- Target format: GeoJSON
- Target coordinate convention: WGS84-compatible GeoJSON longitude/latitude (RFC 7946)
- Reprojection: none
- Transformation: Structural KML Polygon/MultiGeometry to GeoJSON Polygon/MultiPolygon
- Altitude: All Census boundary tuples were longitude,latitude,altitude with altitude 0.0 and altitudeMode clampToGround; altitude was discarded after that confirmation and is not part of the 2D region geometry.
- Simplification: Census Cartographic Boundary 1:500,000; no additional simplification

## Scope

- Included: 50 U.S. states
- Excluded Census state-equivalents: AS, DC, GU, MP, PR, VI
- Feature count: 50
- Alaska spatial support: supported (no adjacent-ring antimeridian crossings).

## Vendored artifact

- File: us_states.geojson
- SHA-256: `83cd47c5449be7559ef9595afc881718f11175f65542c743c7ae5d831d5b12c5`
- Bytes: 6946274
- Script: scripts/vendor_us_states.py
- Invocation: `python scripts/vendor_us_states.py --source-zip C:\Users\ibrah\AppData\Local\Temp\wilvor-census-1d2\cb_2025_us_state_500k.kml.zip --source-url https://www2.census.gov/geo/tiger/GENZ2025/kml/cb_2025_us_state_500k.zip --retrieval-date 2026-09-07 --output-dir functions\shared\wilvor_operational\data`

Census Bureau Cartographic Boundary Files are simplified representations
of selected geographic areas from the MAF/TIGER System. Acknowledge the
U.S. Census Bureau.
