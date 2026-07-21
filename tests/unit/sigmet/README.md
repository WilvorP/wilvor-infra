# SIGMET Unit Tests

These tests exercise the SIGMET poller and processor without calling NOAA or AWS.

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
- EventBridge `Weather.changed` events
- Idempotent processor orchestration
- Bad-record S3 quarantine
- Lambda partial-batch retry behavior

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
