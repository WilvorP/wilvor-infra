# SIGMET module

**Canonical page:** [`docs/ingestion/sigmet.md`](../../docs/ingestion/sigmet.md)
**Verified against:** `8abd2d5` on `docs/ws2-ingestion`
**Maturity:** TF, UNIT, INFRA
**Production authorization:** no grant found in this module

## Purpose

Terraform for NOAA SIGMET poller, raw stream, archive, ActiveHazards and child tables, v4 processor, disabled standalone coordinates mapping, and hazard-station-candidates processor. Canonical behavior: [`docs/ingestion/sigmet.md`](../../docs/ingestion/sigmet.md) and [`docs/ingestion/hazard-station-candidates.md`](../../docs/ingestion/hazard-station-candidates.md).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Terraform | TF | this module; `envs/dev/main.tf` (`module.sigmet`) |
| Unit tests | UNIT for Lambdas | `tests/unit/sigmet/` |
| Infrastructure tests | INFRA | `tests/infrastructure/terraform/test_root_contract.py`; `tests/infrastructure/terraform/test_historical_facts_contract.py` |
| Live-account infrastructure tests | files exist; require authenticated AWS and `terraform output` | `tests/infrastructure/aws/test_compute.py`, `test_data_services.py`, `test_eventing_monitoring.py` |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | this module |

## Behavior

Poller schedule state follows `enable_sigmet_poller_schedule` (default and `envs/dev`: `false`). Processor Kinesis mapping in `processor.tf` is not disabled. Coordinates mapping in `hazard_coordinates_processor.tf` sets `enabled = false`. HSC EventBridge rule matches `HazardCoordinates.materialized` and `station.reference.updated`. Default `station_reference_h3_index_name` is `h3_cell-station_id-index`; `envs/dev` overrides to `h3-station-index`.

## Known limitations

- Disabled poller schedule in `envs/dev`.
- Disabled coordinates Kinesis consumer.
- Module GSI default does not match `modules/station_reference/tables.tf` unless overridden.
- PITR is `enabled = false` on tables in `tables.tf`.
- Live-account tests under `tests/infrastructure/aws/` do not establish DEPLOYED.

## Historical record

No superseded statement is stored on this page.

## Sources

- Source: `modules/sigmet/poller.tf`, `processor.tf`, `hazard_coordinates_processor.tf`, `hazard_station_candidates_processor.tf`
- Source: `envs/dev/main.tf` (`module.sigmet`)
