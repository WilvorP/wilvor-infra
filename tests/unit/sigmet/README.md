# SIGMET unit tests

**Canonical page:** [`docs/ingestion/sigmet.md`](../../../docs/ingestion/sigmet.md)
**Verified against:** `8abd2d5` on `docs/ws2-ingestion`
**Maturity:** UNIT
**Production authorization:** these tests do not grant production use

These tests exercise the SIGMET poller and processor without calling NOAA or AWS.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Tests | UNIT | this directory |
| Application code under test | CODE | `functions/weather/sigmet/` |
| Deployment | UNKNOWN | these tests do not observe AWS |

## Coverage

### Poller

- NOAA HTTP request construction and error handling
- Supported NOAA response shapes
- Time-partitioned S3 archive keys
- Gzip raw-response archiving
- Kinesis partition-key derivation
- 500-record Kinesis batching
- Successful and failed Lambda-handler metrics

### Processor

- Timestamp parsing and canonicalization
- Stable hashing
- Kinesis decoding and permanent-error classification
- GeoJSON feature and property validation
- Deterministic `hazard_id` generation
- Content-sensitive `source_version`
- Hazard-type extraction
- Polygon and MultiPolygon H3 behavior
- Centroid fallback
- Active-hazard item construction
- NEW, UPDATED, retry-publish, and UNCHANGED decisions
- Hazard-cell synchronization
- Publisher-helper invocation after READY (`test_process_decoded_record_new_hazard` patches `publish_hazard_coordinates_materialized` and `publish_hazard_materialized`; it does not assert EventBridge `DetailType` strings)
- Idempotent processor orchestration
- Bad-record S3 quarantine
- Lambda partial-batch retry behavior

### Hazard station candidates

- Inside-polygon and nearby station selection
- `NO_CANDIDATES` still publishes `hazard.stations.ready`

Canonical HSC page: [`docs/ingestion/hazard-station-candidates.md`](../../../docs/ingestion/hazard-station-candidates.md).

## Run

From the repository root:

```powershell
.\scripts\run-unit-tests.ps1 -Target sigmet
```

With coverage:

```powershell
.\scripts\run-unit-tests.ps1 `
    -Target sigmet `
    -Coverage
```

Reports are written to:

```text
test-results/unit/
├── sigmet-results.xml
└── sigmet-coverage.xml
```

## Isolation rule

The suite does not require:

- Deployed Terraform infrastructure
- AWS SSO
- NOAA availability
- Internet access

## Known limitations

- No unit test in this directory asserts `Weather.changed` (search: `tests/unit/sigmet` for that string).
- V4 processor tests do not assert EventBridge `DetailType` `HazardCoordinates.materialized` or `hazard.materialized`. `test_process_decoded_record_new_hazard` records helper invocation order (`coordinate-event`, `hazard-event`) after patching those functions (source: `tests/unit/sigmet/test_sigmet_processor_state.py`). The implementation puts those DetailTypes (source: `functions/weather/sigmet/processor/app.py`, `publish_hazard_coordinates_materialized`, `publish_hazard_materialized`).
- The standalone coordinates processor test does assert `DetailType` `HazardCoordinates.materialized` (source: `tests/unit/sigmet/test_hazard_coordinates_processor.py`, `test_polygon_creates_required_coordinate_rows`). That Lambda mapping is `enabled = false`.
- Hazard-station-candidate tests assert `DetailType` `hazard.stations.ready` (source: `tests/unit/sigmet/test_hazard_station_candidates_processor.py`).
- Integration tests under `tests/integration/sigmet/` still wait for `Weather.changed` and `internal.sigmet.v1`. Those files are outside this suite.

## Historical record

An earlier version of this README listed EventBridge `Weather.changed` events as processor coverage, then listed `HazardCoordinates.materialized` and `hazard.materialized` as if publisher-helper tests verified those DetailType strings. Current CODE puts those two DetailTypes. Current v4 processor unit tests invoke the patched helpers and do not read the PutEvents `DetailType`.

## Sources

- Tests: `tests/unit/sigmet/test_sigmet_poller.py`, `test_sigmet_processor_state.py`, `test_hazard_station_candidates_processor.py`
