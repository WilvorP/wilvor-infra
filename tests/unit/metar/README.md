# METAR unit tests

**Canonical page:** [`docs/ingestion/metar.md`](../../../docs/ingestion/metar.md)
**Verified against:** `8abd2d5` on `docs/ws2-ingestion`
**Maturity:** UNIT
**Production authorization:** these tests do not grant production use

These tests cover the current METAR poller and latest-state processor without
calling NOAA or AWS.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Tests | UNIT | this directory |
| Application code under test | CODE | `functions/weather/metar/` |
| Deployment | UNKNOWN | these tests do not observe AWS |

Run:

```powershell
.\scripts\run-unit-tests.ps1 -Target metar
```

Coverage:

```powershell
.\scripts\run-unit-tests.ps1 -Target metar -Coverage
```

Covered behavior includes HTTP fetching, response extraction, gzip archiving,
Kinesis contracts and batching, feature normalization, `metar_version`,
NEW/UPDATED/CORRECTED/STALE/UNCHANGED classification, conditional DynamoDB
writes, pending EventBridge retries for `metar.updated`, bad-record archiving,
metrics, Lambda partial-batch failures, and `hazard.stations.ready` station
scope resolution.

## Known limitations

- Integration tests under `tests/integration/metar/` still assert `metar_latest.v1`, `source_version`, and a weather-log predicate of `product_type == "METAR"` plus `change_type == "NEW"`. This suite asserts v4 names: DetailType `metar.updated` and detail key `reason` (`test_publish_metar_updated_event`). Current CODE omits `product_type` and `change_type` on the EventBridge detail.

## Historical record

No superseded statement is stored on this page.

## Sources

- Tests: `tests/unit/metar/test_metar_poller.py`, `test_metar_processor.py`
