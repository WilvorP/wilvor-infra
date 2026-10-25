# TAF unit tests

**Canonical page:** [`docs/ingestion/taf.md`](../../../docs/ingestion/taf.md)
**Verified against:** `8abd2d5` on `docs/ws2-ingestion`
**Maturity:** UNIT
**Production authorization:** these tests do not grant production use

These tests cover the current TAF poller and latest-state processor without
calling NOAA or AWS.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Tests | UNIT | this directory |
| Application code under test | CODE | `functions/weather/taf/` |
| Deployment | UNKNOWN | these tests do not observe AWS |

Run:

```powershell
.\scripts\run-unit-tests.ps1 -Target taf
```

Coverage:

```powershell
.\scripts\run-unit-tests.ps1 -Target taf -Coverage
```

Covered behavior includes station normalization and API chunking, 204 handling,
gzip archiving, Kinesis contracts and batching, TAF and forecast-period
normalization, visibility, wind and ceiling handling, stable source versions,
NEW/UPDATED/CORRECTED/STALE/UNCHANGED classification, conditional DynamoDB
writes, EventBridge `taf.materialized` publish, bad-record archiving, metrics,
and Lambda partial-batch failures.

## Known limitations

- Poller tests use `TAF_STATION_IDS`. They do not assert the `hazard.stations.ready` / `hazard_version_key` query path.

## Historical record

No superseded statement is stored on this page.

## Sources

- Tests: `tests/unit/taf/test_taf_poller.py`, `test_taf_processor.py` (`test_publish_taf_materialized`)
