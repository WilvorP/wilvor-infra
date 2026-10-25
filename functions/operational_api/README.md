# Operational API Lambda

**Canonical page:** this page for the Python application. HTTP contracts: [`docs/api/operational-api.md`](../../docs/api/operational-api.md)
**Verified against:** `8abd2d5` on `docs/ws3-processing-api`
**Maturity:** CODE, TF, UNIT
**Production authorization:** no grant found. The handler is GET-only. It does not import `wilvor_ai` and does not execute specialists.

## Purpose

This package is the dashboard Operational API Lambda: route dispatch, DynamoDB reads, current-set composition, freshness, system health, and CloudWatch dashboard PNG render. Shared current-set code is [`functions/shared/wilvor_operational/README.md`](../shared/wilvor_operational/README.md).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `functions/operational_api/{app,repository,cloudwatch_dashboards,current_set}.py` |
| Terraform | TF | `modules/operational_api/api.tf` |
| Unit tests | UNIT | `tests/unit/operational_api/` |
| Contract tests | none found | search: `tests/contracts/` for this Lambda |
| Infrastructure tests | none found for this Lambda | search: `tests/infrastructure/` for the Python handler. Catalog-gating INFRA tests cover `modules/operational_api/api.tf` on [`modules/operational_api/README.md`](../../modules/operational_api/README.md) |
| Integration tests | none found | search: `tests/integration/` |
| Live harness | none found | `scripts/build_operational_api.ps1` packages the zip |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | `functions/operational_api/app.py` (`lambda_handler`) |

## Behavior

`app.lambda_handler` rejects non-GET with 405, dispatches the routes in [`docs/api/operational-api.md`](../../docs/api/operational-api.md), and maps exceptions to 400/404/500 as documented there.

`repository.py` performs DynamoDB reads through `wilvor_operational` helpers, applies current-set selection, and caches selected payloads in process memory (`_cached`). `current_set.py` is a facade over `wilvor_operational.current_set`. `cloudwatch_dashboards.py` reads allowlisted `GetDashboard` bodies and renders metric widgets via `GetMetricWidgetImage`. Widget metric JSON is never accepted from the caller.

Packaging: `scripts/build_operational_api.ps1` zips `app.py`, `repository.py`, `current_set.py`, `cloudwatch_dashboards.py`, and `wilvor_operational` into `functions/operational_api/dist/operational_api.zip`.

## Interfaces

Environment: `NAME_PREFIX`, `ENABLE_HISTORICAL_FACTS_DASHBOARD`, `ENABLE_HISTORICAL_ANALYTICS_DASHBOARD`, plus table name keys listed in `envs/dev/main.tf` `module.operational_api.table_names`.

This Lambda does not write operational tables.

## Known limitations

- Scan-backed list and map endpoints; process caches; overview vs list count differences. Full list: [`docs/api/operational-api.md`](../../docs/api/operational-api.md).
- Deployment is UNKNOWN.

## Historical record

No superseded statement is stored on this page.

## Sources

- Source: `functions/operational_api/app.py` (`lambda_handler`)
- Source: `functions/operational_api/repository.py`
- Source: `functions/operational_api/cloudwatch_dashboards.py`
- Source: `scripts/build_operational_api.ps1`
- Tests: `tests/unit/operational_api/`
