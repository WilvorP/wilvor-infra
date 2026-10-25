# Operational API

**Canonical page:** this page
**Verified against:** `8abd2d5` on `docs/ws3-processing-api`
**Maturity:** CODE, TF, UNIT, INFRA
**Production authorization:** no grant found. No API authorizer, API key, Cognito, or JWT authorizer is configured in `modules/operational_api`. That absence is not production authentication.

## Purpose

This page is the HTTP reference for the dashboard Operational API. Terraform lives in [`modules/operational_api/README.md`](../../modules/operational_api/README.md). Handler and repository behavior live in [`functions/operational_api/README.md`](../../functions/operational_api/README.md). Current-set rules remain canonical in [`functions/shared/wilvor_operational/README.md`](../../functions/shared/wilvor_operational/README.md). The API is not an AI execution path: `functions/operational_api` does not import `wilvor_ai`.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `functions/operational_api/{app,repository,cloudwatch_dashboards,current_set}.py` |
| Terraform | TF | `modules/operational_api/api.tf`; `envs/dev/main.tf` (`module.operational_api`) |
| Unit tests | UNIT | `tests/unit/operational_api/` |
| Contract tests | none found | search: `tests/contracts/` for operational HTTP routes |
| Infrastructure tests | INFRA | `tests/infrastructure/terraform/test_historical_facts_contract.py`; `tests/infrastructure/terraform/test_historical_analytics_contract.py` (`test_operational_api_analytics_dashboard_is_gated`); `tests/infrastructure/terraform/test_historical_analytics_query_iam_contract.py` |
| Integration tests | none found | search: `tests/integration/` for operational API HTTP |
| Live harness | none found | search: `scripts/validate_*` for operational HTTP. `scripts/build_operational_api.ps1` is a zip build, not a live harness |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no authorizer configured | `modules/operational_api/api.tf` (search: `authorizer`, `aws_apigatewayv2_authorizer`) |

## Behavior

### Routes and methods

API Gateway exposes **16** GET routes. `local.route_keys` in `modules/operational_api/api.tf` is a 16-element set. `lambda_handler` serves those same 16 paths. CORS `allow_methods` includes `OPTIONS`, but `OPTIONS` is not a `route_keys` entry. There is no 17th or 18th HTTP route in this module (source: `modules/operational_api/api.tf`, `local.route_keys`; `functions/operational_api/app.py`, `lambda_handler`).

| Route key | Handler |
| --- | --- |
| `GET /health` | inline liveness JSON |
| `GET /overview` | `repository.get_overview` |
| `GET /freshness` | `repository.get_freshness` |
| `GET /system-health` | `repository.get_system_health` |
| `GET /system-health/dashboards/{dashboardId}` | `cloudwatch_dashboards.get_dashboard_view` |
| `GET /system-health/dashboards/{dashboardId}/widgets/{widgetId}/image` | widget PNG |
| `GET /aircraft` | `repository.list_aircraft` |
| `GET /aircraft/{aircraftId}` | `repository.get_aircraft_detail` |
| `GET /map/aircraft` | `repository.get_map_aircraft` |
| `GET /hazards/active` | `repository.list_active_hazards` |
| `GET /encounters/active` | `repository.list_active_encounters` |
| `GET /airports` | `repository.list_airports` |
| `GET /airports/status` | same as `/airports` |
| `GET /airports/{airportId}` | `repository.get_airport_detail` |
| `GET /recommendations/active` | `repository.list_active_recommendations` |
| `GET /alerts/active` | `repository.list_active_alerts` |

`/map/aircraft` is matched before the `/aircraft/` prefix so it does not shadow as an aircraft id (tests: `tests/unit/operational_api/test_map_aircraft.py`).

Non-GET methods are not registered in API Gateway. If the Lambda is invoked with a non-GET method it returns 405 `{ "message": "Method not allowed" }`. Unknown GET paths return 404 `{ "message": "Route not found", "path": "<path>" }`. There is no request body on any route.

`?bbox=` on `/map/aircraft` is not implemented in `app.py` or `repository.py`.

### Request parameters and responses

JSON responses use `content-type: application/json` and `cache-control: no-store`. Widget images use `content-type: image/png` and `cache-control: public, max-age=30` (`_png_response`, default `cache_seconds=30`). There is no `ETag`.

List envelope from `repository._page`: `{ "items", "count", "nextToken" }` with `nextToken` null when exhausted.

| Route | Query | Path | Success body |
| --- | --- | --- | --- |
| `GET /health` | — | — | `{ service: "wilvor-operational-api", status: "ok", requestId }` |
| `GET /overview` | — | — | `generatedAt`, counts for aircraft/hazards/encounters/recommendations/alerts/airports, `topRisks` |
| `GET /freshness` | — | — | `generatedAt`, `mode: "SOURCE_TABLE_LATEST_RECORD"`, `sources.{opensky,sigmet,metar,taf}` |
| `GET /system-health` | — | — | `generatedAt`, `status` (`HEALTHY`/`DEGRADED`/`CRITICAL`), `lambda`, `cloudWatch`, `dataFreshness` |
| `GET /system-health/dashboards/{dashboardId}` | — | `dashboardId` | dashboard view from `GetDashboard` |
| `GET .../widgets/{widgetId}/image` | `range`, `width`, `height` | `dashboardId`, `widgetId` | PNG |
| `GET /map/aircraft` | — | — | `generatedAt`, `columns`, `count`, `truncated`, `aircraft` (row arrays) |
| `GET /aircraft` | `limit`, `nextToken`, `callsign`, `h3Cell` | — | paginated raw aircraft items |
| `GET /aircraft/{aircraftId}` | — | `aircraftId` (lowercased in repository) | `aircraft`, `projection`, `projectionPoints`, `currentContexts`, `recent*` slices |
| `GET /hazards/active` | `limit`, `nextToken` | — | hazards plus optional GeoJSON `geometry` |
| `GET /encounters/active` | `limit`, `nextToken` | — | `{ encounter, risk }` per item |
| `GET /airports`, `/airports/status` | `limit`, `nextToken`, `weatherRisk`, `weatherImpact` | — | raw AirportStatus items |
| `GET /airports/{airportId}` | — | `airportId` (uppercased) | `airport`, `metar`, `taf`, `tafForecastPeriods`, `recentAssessments` |
| `GET /recommendations/active` | `limit`, `nextToken` | — | current-set recommendation items |
| `GET /alerts/active` | `limit`, `nextToken` | — | current-set alert items |

`GET /health` proves API Gateway → Lambda only. It is not `GET /system-health`.

Aircraft list rejects using `callsign` and `h3Cell` together.

Dashboard client types: `dashboard/src/types/api.ts`.

### Error handling

| Condition | Status | Body |
| --- | --- | --- |
| Non-GET | 405 | `{ "message": "Method not allowed" }` |
| Unknown path | 404 | `{ "message": "Route not found", "path" }` |
| Missing aircraft / airport | 404 | `{ "message": "Aircraft not found", "aircraftId" }` or `{ "message": "Airport not found", "airportId" }` |
| `UnknownDashboard`, `UnknownWidget` | 404 | `{ "message": "<str(exc)>" }` |
| `WidgetNotMetric`, `BadTimeRange`, `BadImageSize`, `BadRequest`, `ValueError` | 400 | `{ "message": "<str(exc)>" }` |
| `botocore.exceptions.ClientError` | 500 | `{ "message": "AWS data access failed", "requestId" }` |
| Other `Exception` | 500 | `{ "message": "Internal server error", "requestId" }` |

`BadRequest` examples: `limit must be an integer`; `limit must be between 1 and {maximum}`; `nextToken is invalid`; empty `callsign`/`h3Cell`.

CloudWatch image `range` must be one of `1h`, `3h`, `6h`, `12h`, `24h` (default `3h`). Query `width`/`height` must be integers in 1–2000 or the handler returns 400 `BadImageSize`; accepted values are then raised to a minimum of 120×80 (`MIN_IMAGE_WIDTH`, `MIN_IMAGE_HEIGHT`) and capped at 2000 (`MAX_IMAGE_PIXELS`) (source: `functions/operational_api/cloudwatch_dashboards.py`, `resolve_pixel_dim`).

### Pagination and limits

`_parse_limit` default 50, maximum 100, except `/encounters/active` default 25 maximum 50.

DynamoDB-backed lists encode `LastEvaluatedKey` as base64url JSON. In-memory current-set lists (encounters, recommendations, alerts) encode `{ "offset": <int> }`.

Other caps: `/map/aircraft` `MAP_AIRCRAFT_MAX_ITEMS = 8000` with `truncated: true`; `/system-health` `activeAlarms` truncated to 50; overview `topRisks` and latest recommendations capped at 5; airport `topImpacted` at 5.

API Gateway stage throttling in `envs/dev/main.tf`: burst 50, rate 25.

### Caching and freshness

| Surface | TTL |
| --- | --- |
| JSON HTTP `Cache-Control` | `no-store` |
| PNG HTTP `Cache-Control` | `public, max-age=30` |
| `get_overview` process cache | 45 s |
| `get_freshness` | 15 s |
| `get_system-health` | 15 s |
| `get_map_aircraft` | 15 s (`MAP_AIRCRAFT_CACHE_TTL_SECONDS`) |
| current-set snapshots | 15 s (`CURRENT_ENCOUNTER_CACHE_TTL_SECONDS`) |
| CloudWatch dashboard metadata | 20 s (`DASHBOARD_CACHE_TTL_SECONDS`) |
| CloudWatch widget PNG | 30 s (`IMAGE_CACHE_TTL_SECONDS`) |

`GET /freshness` mode is `SOURCE_TABLE_LATEST_RECORD`. OpenSky: fresh ≤90 s, acceptable ≤180 s, else `STALE`. METAR: fresh ≤600 s, stale threshold 1800 s. TAF: fresh ≤7200 s, stale threshold 21600 s. SIGMET uses `AVAILABLE`/`UNAVAILABLE` plus a note, not FRESH/STALE banding (source: `repository.get_freshness`). SIGMET `AVAILABLE` describes source availability, not an age-based freshness grade.

These numbers are Lambda cache and source-age bands. They are not `FreshnessStatus` from `wilvor_ai.contracts`.

### Current-state selection

Authoritative functions: `functions/shared/wilvor_operational/current_set.py`, re-exported by `functions/operational_api/current_set.py`.

| Entity | List / overview current rule |
| --- | --- |
| Aircraft | `expires_at_epoch > now` |
| Airport status | same |
| Projection | `READY` and `valid_until_epoch > now`; latest `(generated_at_epoch, projection_id)` per aircraft |
| Hazard (`/hazards/active`) | `ACTIVE`, `valid_to_epoch >= now`, `materialization_status == READY` |
| Encounter | `DETECTED`/`MONITORING` plus current projection and hazard version |
| Risk (overview current) | current encounter; present `valid_until_utc` strictly future; missing validity accepted |
| Recommendation (`/recommendations/active`) | lifecycle-active plus current `risk_id` — aligned with `overview.recommendations.currentCount` |
| Alert (`/alerts/active`) | lifecycle-active plus current risk **or** recommendation — aligned with `overview.alerts.currentCount` |

`overview.recommendations.activeCount` / `alerts.activeCount` count lifecycle-active rows and are **not** the same as `currentCount` (source comments in `app.py` at the `/recommendations/active` and `/alerts/active` branches).

Overview hazard `activeCount` uses lifecycle-active GSI semantics and does **not** require `materialization_status == READY`.

Aircraft and airport detail GetItem paths can return retained expired rows that lists exclude.

`/encounters/active` attaches the newest risk from a GSI query without the overview current-risk validity filter (source: `functions/shared/wilvor_operational/README.md`).

### Authorization

No `aws_apigatewayv2_authorizer` resource exists in `modules/operational_api`. Search of that module for `authorizer`, `api_key`, `Cognito`, and `JWT` returned no matches. `aws_lambda_permission.api_gateway` allows `apigateway.amazonaws.com` to invoke the Lambda with `source_arn = "${execution_arn}/*/*"`.

The HTTP API is reachable by any caller who can reach the public `api_endpoint` output. That is not production-grade access control. IAM on `aws_iam_role.lambda` is the Lambda execution role, not caller authentication.

Dashboard client comment: "The operational API is GET-only and unauthenticated at the edge." (source: `dashboard/src/api/http.ts`)

### CORS

`aws_apigatewayv2_api.http` (`api.tf`):

- `allow_credentials = false`
- `allow_headers = ["authorization", "content-type"]`
- `allow_methods = ["GET", "OPTIONS"]`
- `allow_origins = var.cors_allowed_origins`
- `max_age = 300`

Default and `envs/dev/main.tf` origins: `http://localhost:3000`, `http://localhost:5173`. The Lambda does not set CORS headers.

### Data limitations

- Unfiltered `GET /aircraft` is a DynamoDB Scan, described in repository comments as acceptable for the first dev API.
- `GET /map/aircraft` is Scan-based, caps at 8000 rows, and does not join risk.
- Overview, freshness, and several current-set loaders scan tables into Lambda memory and cache briefly.
- Hazard geometry is optional and skipped when coordinate rings are invalid.
- List items are raw DynamoDB attributes; writers omit `None`.
- Recommendations and alerts are stored advisory records. The API does not convert `EVALUATE_DIVERSION` into a clearance.
- CloudWatch dashboard IAM is GetDashboard / GetMetricWidgetImage on an allowlist. Athena is not on this role (tests: `test_operational_api_analytics_dashboard_is_gated`; query IAM contract).

## Interfaces

Dev Lambda (`envs/dev/main.tf`): memory 1024, timeout 30 s, `lambda_reserved_concurrency = 5`, log retention 3 days. Module defaults are memory 512, timeout 15 s, reserved concurrency `-1`. API Gateway integration timeout is 29000 ms.

Table environment keys: see `envs/dev/main.tf` `module.operational_api.table_names` and [`docs/data/dynamodb.md`](../data/dynamodb.md).

IAM Sids: `ReadOperationalState` (GetItem/Query/Scan), `ReadOperationalHealth`, `ReadAllowlistedDashboards`, `RenderDashboardMetricWidgets`, `WriteLambdaLogs`. No DynamoDB writes.

CloudWatch catalog base ids in `local.cloudwatch_dashboard_ids` / `DASHBOARD_CATALOG`: `aircraft-pipeline`, `aircraft-hazard-encounter`, `projection-pipeline`, `sigmet-pipeline`, `metar-pipeline`, `taf-pipeline`, `weather-events`, `hazard-station-candidates`, `airport-status`, `airport-assessment`, `risk-pipeline`, `recommendations`, `active-alerts`, `runway-metadata`. `historical-facts` and `historical-analytics` are env-gated.

Outputs: `operational_api_endpoint`, `operational_api_id`, `operational_api_lambda_name`, `operational_api_lambda_arn`, `operational_api_log_group_name` (`envs/dev/outputs.tf`).

## Known limitations

- No HTTP authorizer. Do not treat CORS localhost allowlisting as authentication.
- Process-level caches can skew views across warm Lambda instances.
- Producer vs API projection exact-end validity differ ([`docs/processing/README.md`](../processing/README.md)).
- Deployment is UNKNOWN.
- Dashboard source comments still cite `reserved_concurrent_executions = 2`; `envs/dev/main.tf` at `8abd2d5` sets `lambda_reserved_concurrency = 5`.

## Historical record

A draft of this page stated that Terraform `local.route_keys` and `lambda_handler` both register 18 GET routes. The set in `modules/operational_api/api.tf` has 16 keys, and those 16 match the GET paths `lambda_handler` serves. Stale dashboard README sentences about this API are recorded in [`dashboard/README.md`](../../dashboard/README.md) under Historical record.

## Sources

- Source: `modules/operational_api/api.tf` (`local.route_keys`, `aws_apigatewayv2_api.http`, `aws_lambda_function.api`)
- Source: `functions/operational_api/app.py` (`lambda_handler`, `_parse_limit`, `_response`, `_png_response`)
- Source: `functions/operational_api/repository.py` (`get_overview`, `get_freshness`, `get_map_aircraft`, `MAP_AIRCRAFT_MAX_ITEMS`)
- Source: `functions/operational_api/cloudwatch_dashboards.py` (`DASHBOARD_CATALOG`, `TIME_RANGES`)
- Source: `envs/dev/main.tf` (`module.operational_api`)
- Tests: `tests/unit/operational_api/test_map_aircraft.py`; `tests/unit/operational_api/test_current_state_characterization.py`; `tests/infrastructure/terraform/test_historical_analytics_contract.py` (`test_operational_api_analytics_dashboard_is_gated`)
