# Station reference module

**Canonical page:** [`docs/ingestion/station-reference.md`](../../docs/ingestion/station-reference.md)
**Historical verification:** `8abd2d5` on `docs/ws2-ingestion`. Not verification of the current tree.
**Maturity:** TF
**Production authorization:** no grant found in this module

## Purpose

Terraform for the Aviation Weather station-cache loader, archive bucket, and `StationReference` table with GSI `h3-station-index`. Canonical behavior: [`docs/ingestion/station-reference.md`](../../docs/ingestion/station-reference.md).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Terraform | TF | this module; `envs/dev/main.tf` (`module.station_reference`) |
| Unit tests | none found for this module | no `tests/unit/station_reference/` |
| Infrastructure tests | INFRA for the module source path | `EXPECTED_MODULES` includes `station_reference` |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | this module |

## Behavior

Loader schedule state follows `enable_station_reference_loader_schedule` (default and `envs/dev`: `false`). Table GSI name is `h3-station-index` (`tables.tf`). `envs/dev` passes that name into `module.sigmet` as `station_reference_h3_index_name`. Loader env `SOURCE_VERSION` is `var.default_source_version`, default nonempty `awc-stations-current` (`loader.tf`, `variables.tf`). `envs/dev` does not override it. HTTP Last-Modified and date fallbacks in the loader run only when that env is empty.

## Known limitations

- No unit tests for this module were found. `EXPECTED_MODULES` includes `station_reference`. Live-account tests under `tests/infrastructure/aws/` omit this loader from `LAMBDA_OUTPUTS` and do not establish DEPLOYED.
- Schedule disabled in `envs/dev`.
- PITR follows `enable_point_in_time_recovery` (`false` in `envs/dev`).

## Historical record

No superseded statement is stored on this page.

## Sources

- Source: `modules/station_reference/tables.tf` (`h3-station-index`)
- Source: `envs/dev/main.tf` (`module.station_reference`)
