# Operational API Terraform

**Canonical page:** this page for Terraform wiring. HTTP contracts: [`docs/api/operational-api.md`](../../docs/api/operational-api.md)
**Verified against:** `8abd2d5` on `docs/ws3-processing-api`
**Maturity:** CODE, TF, UNIT, INFRA
**Production authorization:** no grant found. This module does not declare `aws_apigatewayv2_authorizer`. That is not production authentication.

## Purpose

This module creates the HTTP API Gateway, the operational-api Lambda, IAM, CORS, routes, and stage throttling. It does not compute risk, recommendations, or alerts.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | Lambda zip consumed from `var.api_zip_path` |
| Terraform | TF | `modules/operational_api/{api,variables,outputs}.tf`; `envs/dev/main.tf` (`module.operational_api`) |
| Unit tests | UNIT | `tests/unit/operational_api/test_map_aircraft.py` (`test_route_is_declared_in_terraform`) |
| Contract tests | none found | search: `tests/contracts/` for this module |
| Infrastructure tests | INFRA | `tests/infrastructure/terraform/test_historical_facts_contract.py`; `tests/infrastructure/terraform/test_historical_analytics_contract.py`; `tests/infrastructure/terraform/test_historical_analytics_query_iam_contract.py` |
| Integration tests | none found | search: `tests/integration/` |
| Live harness | none found | search: `scripts/validate_*` |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no authorizer | `modules/operational_api/api.tf` |

## Behavior

Resources: `aws_lambda_function.api` (handler `app.lambda_handler`, runtime `python3.12`); `aws_apigatewayv2_api.http` protocol `HTTP`; `aws_apigatewayv2_integration.lambda` AWS_PROXY payload 2.0, `timeout_milliseconds = 29000`; `aws_apigatewayv2_route.routes` for each `local.route_keys`; `aws_apigatewayv2_stage.default` name `$default`, `auto_deploy = true`; `aws_lambda_permission.api_gateway`.

`local.route_keys` is the 16 GET routes listed on [`docs/api/operational-api.md`](../../docs/api/operational-api.md). CORS `allow_methods` includes `OPTIONS`; that method is not a `route_keys` entry.

CORS: credentials false; headers `authorization`, `content-type`; methods `GET`, `OPTIONS`; origins `var.cors_allowed_origins`; max_age 300.

No authorizer resource. Invocation is open at the HTTP edge to the published endpoint.

IAM: DynamoDB GetItem/Query/Scan on `var.table_arns` plus `/index/*`; CloudWatch DescribeAlarms, GetMetricStatistics, GetDashboard (allowlisted ARNs), GetMetricWidgetImage; Lambda GetAccountSettings; log writes. No DynamoDB PutItem.

Dev overrides (`envs/dev/main.tf`): memory 1024, timeout 30, reserved concurrency 5, throttling burst 50 / rate 25, `enable_historical_facts = true`, `enable_historical_analytics_dashboard = module.historical_analytics.enable_historical_analytics`.

## Interfaces

Inputs: `name_prefix`, `api_zip_path`, `table_names`, `table_arns`, `cors_allowed_origins`, Lambda sizing, throttling, historical dashboard flags.

Outputs: API id, endpoint, Lambda name/arn, log group (see `outputs.tf` and `envs/dev/outputs.tf`).

## Known limitations

- No authorizer.
- CORS allowlist in dev is localhost only.
- Reserved concurrency and throttling are capacity limits, not authentication.
- Deployment is UNKNOWN.

## Historical record

A draft of this page described `local.route_keys` as 18 GET routes. The set in `api.tf` has 16 keys.

## Sources

- Source: `modules/operational_api/api.tf`
- Source: `modules/operational_api/variables.tf`
- Source: `envs/dev/main.tf` (`module.operational_api`)
- Tests: `tests/unit/operational_api/test_map_aircraft.py`; `tests/infrastructure/terraform/test_historical_analytics_contract.py` (`test_operational_api_analytics_dashboard_is_gated`)
