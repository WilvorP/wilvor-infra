# Airport status

**Canonical page:** this page
**Verified against:** `8abd2d5` on `docs/ws3-processing-api`
**Maturity:** CODE, TF, UNIT
**Production authorization:** no grant found in `functions/airport_status/materializer/app.py`. Weather-derived status is not a diversion clearance.

## Purpose

This module materializes a per-airport weather snapshot from station reference, latest METAR, and latest TAF. It is a parallel weather branch, not a step on the encounter → risk chain. Pipeline placement: [`docs/processing/README.md`](../../docs/processing/README.md). Table keys: [`docs/data/dynamodb.md`](../../docs/data/dynamodb.md). Ingestion producers belong to WS2 (`docs/ingestion/**` when published).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `functions/airport_status/materializer/app.py` (`lambda_handler`) |
| Terraform | TF | `modules/airport_status/{processor,tables,monitoring,variables,outputs}.tf`; `envs/dev/main.tf` (`module.airport_status`) |
| Unit tests | UNIT | `tests/unit/airport_status/test_airport_status_materializer.py` |
| Contract tests | none found | search: `tests/contracts/` for airport status materializer |
| Infrastructure tests | none found | search: `tests/infrastructure/` for `modules/airport_status` |
| Integration tests | none found | search: `tests/integration/` for airport status |
| Live harness | none found | search: `scripts/validate_*` for airport status |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | `functions/airport_status/materializer/app.py` |

## Behavior

### Event triggers

| Terraform rule | Pattern | Lambda target |
| --- | --- | --- |
| `aws_cloudwatch_event_rule.airport_status_weather_updates` | `wilvor.weather` / `metar.updated`, `taf.materialized` | `aws_lambda_function.airport_status_materializer` |
| `aws_cloudwatch_event_rule.airport_status_updated` | `wilvor.airport` / `airport.status.updated` | none |

The weather rule has `aws_cloudwatch_event_target.airport_status_materializer`. The status-updated rule exists for downstream subscription; no target is declared in this module. Terraform comment: "Matches AirportStatus updates for downstream airport assessment processing." Airport assessment does not subscribe to that event; it scans `airport_status` (source: `functions/airport_assessment/processor/app.py`, `scan_airports`).

When `PUBLISH_EVENTS=true`, the materializer publishes `wilvor.airport` / `airport.status.updated`. Failed publish raises `RuntimeError`. Bootstrap mode `event.mode == "bootstrap"` scans weather tables (tests: `test_bootstrap_scans_weather_tables`).

This module has no `enable_*_trigger` disable flag; the weather rule is always present.

### Source tables and dependencies

Reads `station_reference`, `metar_latest`, `taf_latest` (GetItem; Scan in bootstrap). Dev TTL and freshness: `airport_status_ttl_seconds = 86400`, `metar_fresh_seconds = 1800`, `taf_fresh_seconds = 21600` (`envs/dev/main.tf`).

### Output tables

`aws_dynamodb_table.airport_status` — PK `airport_id`; GSIs `station-updated-index`, `weather-risk-updated-index`, `weather-impact-updated-index`; TTL `expires_at_epoch`; SSE enabled.

### Processing responsibility

Resolve `station_id` from the event → load station reference → derive weather risk, impact, and freshness → `put_item` → publish status update.

Env `SCHEMA_VERSION` default `airport_status.v1`.

### State transitions and validity

One row per `airport_id`. API currentness is `expires_at_epoch > now_epoch` (`is_current_airport_status`). METAR/TAF freshness fields describe contents, not row membership (source: `functions/shared/wilvor_operational/README.md`).

### Failure handling

No DLQ. Materializer error/throttle alarms, DynamoDB throttle alarms, EventBridge failed invocations on the **weather** rule (`monitoring.tf`). Lambda memory default 256, timeout default 60 (`variables.tf`); `envs/dev/main.tf` does not override those two inputs.

### Test coverage

`tests/unit/airport_status/test_airport_status_materializer.py`: `test_extract_station_id_from_direct_event`, `test_extract_station_id_from_eventbridge_detail`, `test_high_weather_risk_from_ifr_metar`, `test_partial_status_when_taf_missing`, `test_lambda_handler_writes_airport_status`, `test_bootstrap_scans_weather_tables`.

## Interfaces

Airport assessment reads this table by scan. Operational API lists and details airports from it ([`docs/api/operational-api.md`](../../docs/api/operational-api.md)).

## Known limitations

- `airport.status.updated` has no Lambda target in this module.
- Missing TAF yields a partial status (tests: `test_partial_status_when_taf_missing`).
- Deployment is UNKNOWN.

## Historical record

No superseded statement is stored on this page.

## Sources

- Source: `modules/airport_status/processor.tf` (`aws_cloudwatch_event_rule.airport_status_weather_updates`, `aws_cloudwatch_event_rule.airport_status_updated`)
- Source: `modules/airport_status/tables.tf`
- Source: `functions/airport_status/materializer/app.py` (`lambda_handler`)
- Tests: `tests/unit/airport_status/test_airport_status_materializer.py`
