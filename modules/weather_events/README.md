# Weather events module

**Canonical page:** [`docs/ingestion/weather-events.md`](../../docs/ingestion/weather-events.md)
**Verified against:** `8abd2d5` on `docs/ws2-ingestion`
**Maturity:** TF, INFRA
**Production authorization:** no grant found in this module

## Purpose

Terraform EventBridge rule and CloudWatch log group that capture a subset of `wilvor.weather` events for verification. It does not poll NOAA or write DynamoDB. Canonical page: [`docs/ingestion/weather-events.md`](../../docs/ingestion/weather-events.md).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Terraform | TF | this module; `envs/dev/main.tf` (`module.weather_events`) |
| Infrastructure tests | INFRA | `tests/infrastructure/terraform/test_root_contract.py` (`EXPECTED_MODULES`) |
| Live-account infrastructure tests | files exist; require authenticated AWS and `terraform output` | `tests/infrastructure/aws/test_eventing_monitoring.py` (`test_weather_changed_log_group_exists`) |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | this module |

## Behavior

`aws_cloudwatch_event_rule.weather_changed` matches `detail-type` `Weather.changed` and `metar.updated` only (`main.tf`). The product-type metric filter in `monitoring.tf` requires `$.detail.product_type`. `metar.updated` can reach the log group without that field. SIGMET and TAF events include `product_type` but are not in this rule. Canonical detail: [`docs/ingestion/weather-events.md`](../../docs/ingestion/weather-events.md).

## Known limitations

- Current SIGMET and TAF processors do not publish `Weather.changed`. Their DetailTypes are not in this rule.
- `metar.updated` is in the rule and does not carry `product_type`.
- Live-account tests under `tests/infrastructure/aws/` do not establish DEPLOYED.

## Historical record

No superseded statement is stored on this page.

## Sources

- Source: `modules/weather_events/main.tf` (`aws_cloudwatch_event_rule.weather_changed`)
- Source: `modules/weather_events/monitoring.tf` (`aws_cloudwatch_log_metric_filter.weather_changed_by_product`)
