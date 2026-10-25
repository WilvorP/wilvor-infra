# TAF module

**Canonical page:** [`docs/ingestion/taf.md`](../../docs/ingestion/taf.md)
**Verified against:** `8abd2d5` on `docs/ws2-ingestion`
**Maturity:** TF, UNIT, INFRA
**Production authorization:** no grant found in this module

## Purpose

Terraform for TAF poller, raw stream, archive, latest and forecast-period tables, and processor. Canonical behavior: [`docs/ingestion/taf.md`](../../docs/ingestion/taf.md).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Terraform | TF | this module; `envs/dev/main.tf` (`module.taf`) |
| Unit tests | UNIT | `tests/unit/taf/` |
| Infrastructure tests | INFRA | `tests/infrastructure/terraform/test_root_contract.py` |
| Live-account infrastructure tests | files exist; require authenticated AWS and `terraform output` | `tests/infrastructure/aws/test_compute.py`, `test_data_services.py`, `test_eventing_monitoring.py` |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | this module |

## Behavior

`poller.tf` defines `aws_cloudwatch_event_rule.taf_poller_hazard_stations_ready` and a schedule rule gated by `enable_taf_poller_schedule`. `envs/dev` sets that flag `false` and `taf_station_ids = ""`. Processor env `SCHEMA_VERSION` is `internal.taf.v1`.

## Known limitations

- Empty `taf_station_ids` in `envs/dev` makes the env fallback unusable without override.
- `envs/dev/main.tf` comment about `Weather.changed` does not match `publish_taf_materialized`.
- PITR on TAF tables is `enabled = false`.
- Live-account tests under `tests/infrastructure/aws/` do not establish DEPLOYED.

## Historical record

No superseded statement is stored on this page.

## Sources

- Source: `modules/taf/poller.tf` (`aws_cloudwatch_event_rule.taf_poller_hazard_stations_ready`)
- Source: `modules/taf/processor.tf` (`SCHEMA_VERSION`)
- Source: `envs/dev/main.tf` (`module.taf`)
