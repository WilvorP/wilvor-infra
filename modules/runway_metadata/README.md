# Runway metadata module

**Canonical page:** [`docs/ingestion/runway-metadata.md`](../../docs/ingestion/runway-metadata.md)
**Verified against:** `8abd2d5` on `docs/ws2-ingestion`
**Maturity:** TF, UNIT
**Production authorization:** no grant found in this module

## Purpose

Terraform for the FAA NASR runway loader, archive bucket, `RunwayReference` table, and loader dashboard. Canonical behavior: [`docs/ingestion/runway-metadata.md`](../../docs/ingestion/runway-metadata.md).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Terraform | TF | this module; `envs/dev/main.tf` (`module.runway_metadata`) |
| Unit tests | UNIT for the loader | `tests/unit/runway_metadata/` |
| Infrastructure tests | none found | `EXPECTED_MODULES` omits this module |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | this module |

## Behavior

Loader schedule state follows `enable_runway_loader_schedule` (default and `envs/dev`: `false`). `envs/dev` sets `supported_airport_ids` to `KSFO`, `KOAK`, `KSJC`, `default_source_cycle` `2026-07-09`, and a pinned `faa_apt_zip_url`.

## Known limitations

- Three ICAO ids in `envs/dev`.
- No Terraform-source infrastructure tests for this module were found (`EXPECTED_MODULES` omits `runway_metadata`). Live-account tests under `tests/infrastructure/aws/` also omit this loader from `LAMBDA_OUTPUTS` and do not establish DEPLOYED.
- Schedule disabled in `envs/dev`.
- PITR is `false` in `envs/dev`.

## Historical record

No superseded statement is stored on this page.

## Sources

- Source: `modules/runway_metadata/loader.tf`
- Source: `envs/dev/main.tf` (`module.runway_metadata`)
