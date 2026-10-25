# METAR module

**Canonical page:** [`docs/ingestion/metar.md`](../../docs/ingestion/metar.md)
**Verified against:** `8abd2d5` on `docs/ws2-ingestion`
**Maturity:** TF, UNIT, INFRA
**Production authorization:** no grant found in this module

## Purpose

Terraform for METAR poller, raw stream, archive, latest-state table, and processor. Canonical behavior: [`docs/ingestion/metar.md`](../../docs/ingestion/metar.md). Hazard-station trigger: [`docs/ingestion/hazard-station-candidates.md`](../../docs/ingestion/hazard-station-candidates.md).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Terraform | TF | this module; `envs/dev/main.tf` (`module.metar`) |
| Unit tests | UNIT | `tests/unit/metar/` |
| Infrastructure tests | INFRA | `tests/infrastructure/terraform/test_root_contract.py` |
| Live-account infrastructure tests | files exist; require authenticated AWS and `terraform output` | `tests/infrastructure/aws/test_compute.py`, `test_data_services.py`, `test_eventing_monitoring.py` |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | this module |

## Behavior

`poller.tf` defines both `aws_cloudwatch_event_rule.metar_hazard_stations_ready` (`hazard.stations.ready`) and a schedule rule whose `state` follows `enable_metar_poller_schedule`. `envs/dev` sets that flag `false`. Processor env `SCHEMA_VERSION` is `wilvor.metar_latest.v4.0`.

## Known limitations

- Schedule disabled in `envs/dev`. The HSC event rule is not gated by that flag.
- PITR on `metar_latest` is `enabled = false`.
- Live-account tests under `tests/infrastructure/aws/` do not establish DEPLOYED.

## Historical record

No superseded statement is stored on this page.

## Sources

- Source: `modules/metar/poller.tf` (`aws_cloudwatch_event_rule.metar_hazard_stations_ready`)
- Source: `modules/metar/processor.tf` (`SCHEMA_VERSION`)
- Source: `envs/dev/main.tf` (`module.metar`)
