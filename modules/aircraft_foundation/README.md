# Aircraft foundation module

**Canonical page:** [`docs/ingestion/aircraft.md`](../../docs/ingestion/aircraft.md)
**Verified against:** `8abd2d5` on `docs/ws2-ingestion`
**Maturity:** CODE, TF, UNIT, INFRA
**Production authorization:** no grant found in this module

## Purpose

Terraform for OpenSky poller, aircraft raw and clean Kinesis streams, archive bucket, current-state table, raw processor, current-state writer, OpenSky secret, and pipeline dashboard. Behavior is on the canonical page.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Terraform | TF | this module; instantiated in `envs/dev/main.tf` (`module.aircraft_foundation`) |
| Code this module packages | CODE | zip paths under `functions/opensky_poller`, `functions/aircraft_raw_processor`, `functions/aircraft_current_state_writer` |
| Unit tests | UNIT for Lambdas | `tests/unit/aircraft/` |
| Infrastructure tests | INFRA | `tests/infrastructure/terraform/test_root_contract.py` (`test_dev_poller_schedules_are_disabled`) |
| Live-account infrastructure tests | files exist; require authenticated AWS and `terraform output` | `tests/infrastructure/aws/test_compute.py`, `test_data_services.py`, `test_eventing_monitoring.py`, `test_secrets.py` |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | this module |

## Behavior

`opensky_poller.tf` creates `aws_cloudwatch_event_rule.opensky_poller_schedule` with `state` from `enable_opensky_poller_schedule` (default `false`). `envs/dev` sets that input `false` and `opensky_poller_schedule_expression = "rate(5 minutes)"`. Streams, table hash `aircraft_id`, and secret name `${name_prefix}/opensky/credentials` are in `main.tf` and `secrets.tf`.

## Known limitations

- Schedule default and `envs/dev` assignment are disabled. TF is not DEPLOYED.
- Secret resource has no secret version in this module.
- PITR on the current-state table is `enabled = false` (`main.tf`).
- Live-account tests under `tests/infrastructure/aws/` do not establish DEPLOYED.

## Historical record

No superseded statement is stored on this page.

## Sources

- Source: `modules/aircraft_foundation/opensky_poller.tf` (`enable_opensky_poller_schedule`)
- Source: `envs/dev/main.tf` (`module.aircraft_foundation`)
