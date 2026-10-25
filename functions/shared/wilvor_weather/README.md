# wilvor_weather

**Canonical page:** [`docs/ingestion/overview.md`](../../../docs/ingestion/overview.md)
**Verified against:** `8abd2d5` on `docs/ws2-ingestion`
**Maturity:** CODE
**Production authorization:** no grant found in this package

## Purpose

Shared CloudWatch Embedded Metric Format helper used by weather pollers/processors and the runway loader. It does not fetch NOAA or FAA data. Pipeline behavior is on the ingestion pages.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `functions/shared/wilvor_weather/monitoring.py` (`emit_metric`) |
| Terraform | this package is copied into some Lambda zips | not a Terraform root |
| Unit tests | none found dedicated to this package | SIGMET/METAR/TAF/runway tests exercise callers |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | this package |

## Behavior

`emit_metric` prints JSON to stdout with namespace `Wilvor/Pipeline` and dimensions `Environment`, `Pipeline`, `Component`, `Stage`. High-cardinality values belong in `properties`, not dimensions (source: `monitoring.py` docstring). `__init__.py` is empty.

Callers that import `wilvor_weather.monitoring.emit_metric` at this commit:

- `functions/weather/sigmet/poller/app.py`
- `functions/weather/sigmet/processor/app.py`
- `functions/weather/metar/poller/app.py`
- `functions/weather/taf/poller/app.py`
- `functions/weather/taf/processor/app.py`
- `functions/runway_metadata/loader/app.py`

METAR processor defines its own `emit_metrics` and does not import this package (source: `functions/weather/metar/processor/app.py`). Station-reference loader does not import it either.

## Known limitations

- This package does not define weather product schemas or EventBridge detail-types.
- `scripts/build_station_reference_loader.ps1` copies this package into the station-reference zip. `functions/station_reference/loader/app.py` does not import it.
- `scripts/build_runway_metadata_loader.ps1` copies loader `*.py` only and does not copy this package; `functions/runway_metadata/loader/app.py` still imports `wilvor_weather.monitoring`. Whether a given zip contains the package is UNKNOWN unless that zip is inspected.

## Historical record

No superseded statement is stored on this page.

## Sources

- Source: `functions/shared/wilvor_weather/monitoring.py` (`emit_metric`, `METRIC_NAMESPACE`)
- Callers: `functions/weather/sigmet/poller/app.py`, `functions/weather/sigmet/processor/app.py`, `functions/weather/metar/poller/app.py`, `functions/weather/taf/poller/app.py`, `functions/weather/taf/processor/app.py`, `functions/runway_metadata/loader/app.py`
