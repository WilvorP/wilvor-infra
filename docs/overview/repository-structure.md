# Repository structure

**Canonical page:** this page
**Verified against:** `8abd2d5` on `docs/ws1-platform-foundations`
**Maturity:** CODE, TF
**Production authorization:** no grant found in this repository

## Purpose

This page lists the directories and technology versions that exist at `8abd2d5`. Architecture behavior lives in [`architecture.md`](architecture.md). Lifecycle scripts live in [`../operations/lifecycle.md`](../operations/lifecycle.md). Directory presence is not a suite result.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | repository tree at `8abd2d5` |
| Terraform | TF | `envs/dev`, `envs/dev-historical-data` |
| Unit tests | directories present; assertions not sampled on this page | `tests/unit/` |
| Contract tests | directories present; assertions not sampled on this page | `tests/contracts/` |
| Infrastructure tests | directories present; assertions not sampled on this page | `tests/infrastructure/` |
| Integration tests | directories present; assertions not sampled on this page | `tests/integration/` |
| Live harness | harness files present; not a pass result | `scripts/validate_*.py` |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | this file is a map |

## Top-level directories

| Path | Role |
| --- | --- |
| `dashboard/` | React Live Operations Console |
| `docs/` | Documentation tree, including [`DOCUMENTATION_STANDARDS.md`](../DOCUMENTATION_STANDARDS.md) |
| `envs/dev/` | Recreatable development Terraform root |
| `envs/dev-historical-data/` | Persistent historical facts data-plane root |
| `functions/` | Lambda handlers and `functions/shared/` packages |
| `modules/` | Terraform modules instantiated by the env roots |
| `scripts/` | Build, lifecycle, test runners, operator harnesses |
| `tests/` | Automated tests |
| `tools/` | `local_opensky_poller.py` |
| `requirements.txt` | Root Python pins (includes Jupyter and data libraries used locally) |

No other `envs/*/main.tf` exists at this commit.

## Functions

Lambda application directories under `functions/` at `8abd2d5`:

`opensky_poller`, `aircraft_raw_processor`, `aircraft_current_state_writer`, `weather/` (SIGMET, METAR, TAF), `station_reference`, `runway_metadata`, `airport_status`, `projection`, `encounter`, `risk`, `airport_assessment`, `recommendations`, `active_alerts`, `historical_facts`, `operational_api`.

Shared packages under `functions/shared/`:

| Package | Canonical page |
| --- | --- |
| `wilvor_operational` | [`functions/shared/wilvor_operational/README.md`](../../functions/shared/wilvor_operational/README.md) |
| `wilvor_ai` | [`functions/shared/wilvor_ai/README.md`](../../functions/shared/wilvor_ai/README.md) |
| `wilvor_historical` | [`functions/shared/wilvor_historical/README.md`](../../functions/shared/wilvor_historical/README.md) |
| `wilvor_historical_query` | [`functions/shared/wilvor_historical_query/README.md`](../../functions/shared/wilvor_historical_query/README.md) |
| `wilvor_aircraft` | [`functions/shared/wilvor_aircraft/README.md`](../../functions/shared/wilvor_aircraft/README.md); [Aircraft](../ingestion/aircraft.md) |
| `wilvor_weather` | [`functions/shared/wilvor_weather/README.md`](../../functions/shared/wilvor_weather/README.md); [Ingestion overview](../ingestion/overview.md) |

Legacy runbooks under `functions/` (bodies preserved; listed from [Ingestion and reference data](../ingestion/README.md#historical-runbooks)):

- `functions/local_runner_documentation.txt`
- `functions/metar_pipeline_runbook.txt`
- `functions/weather_pipeline_reproducibility.txt`
- `functions/Runway_Metadata_Commands.txt`
- `functions/Wilvor_Aircraft_SIGMET_Integration_Test_Runbook_Original_Structure.txt.txt`

## Terraform modules

`modules/` at `8abd2d5`: `aircraft_foundation`, `weather_events`, `sigmet`, `metar`, `taf`, `station_reference`, `runway_metadata`, `airport_status`, `projection`, `encounter`, `risk`, `airport_assessment`, `recommendations`, `active_alerts`, `operational_api`, `historical_facts`, `historical_facts_data`, `historical_analytics`.

Each listed module has a colocated `README.md`. Ingestion module pointers: [`docs/ingestion/README.md`](../ingestion/README.md). Processing and API module pointers: [`docs/processing/README.md`](../processing/README.md), [`docs/api/README.md`](../api/README.md). Historical module pointers: [`docs/historical/README.md`](../historical/README.md).

## Tests and scripts

See [`../testing/overview.md`](../testing/overview.md) and [`../../tests/README.md`](../../tests/README.md). Script catalog: [`../../scripts/README.md`](../../scripts/README.md).

`scripts/build_*.ps1` at this commit (21 files): `build_active_alert_processor.ps1`, `build_aircraft_lambdas.ps1`, `build_airport_assessment_processor.ps1`, `build_airport_status_materializer.ps1`, `build_encounter_processor.ps1`, `build_historical_facts_coverage.ps1`, `build_historical_facts_transform.ps1`, `build_metar_poller.ps1`, `build_metar_processor.ps1`, `build_operational_api.ps1`, `build_projection_processor.ps1`, `build_recommendation_processor.ps1`, `build_risk_processor.ps1`, `build_runway_metadata_loader.ps1`, `build_sigmet_hazard_coordinates_processor.ps1`, `build_sigmet_hazard_station_candidates_processor.ps1`, `build_sigmet_poller.ps1`, `build_sigmet_processor.ps1`, `build_station_reference_loader.ps1`, `build_taf_poller.ps1`, `build_taf_processor.ps1`.

`dev-up.ps1` runs every `scripts/build_*.ps1` unless `-SkipBuild` (source: `scripts/dev-up.ps1`; `scripts/README.md`).

## Technology stack

| Component | Version or pin | Source |
| --- | --- | --- |
| Lambda runtime | `python3.12` | `modules/operational_api/api.tf` (`aws_lambda_function.api`); other processor `.tf` files use the same runtime |
| Terraform | `>= 1.6.0` | `envs/dev/providers.tf` |
| AWS provider | `~> 5.0` | `envs/dev/providers.tf` |
| Default region | `us-west-1` | `envs/dev/terraform.tfvars` (`aws_region`) |
| Default AWS profile | `wilvor-dev` | `envs/dev/terraform.tfvars` (`aws_profile`) |
| Project / environment | `wilvor` / `dev` | `envs/dev/terraform.tfvars` |
| React | `^19.0.0` | `dashboard/package.json` |
| Vite | `^6.0.5` | `dashboard/package.json` |
| Vitest | `^3.0.0` | `dashboard/package.json` |
| MapLibre | `^5.0.0` | `dashboard/package.json` |
| TanStack Query | `^5.62.7` | `dashboard/package.json` |
| react-router-dom | `^7.1.1` | `dashboard/package.json` |
| Node engines | `^18.0.0 || ^20.0.0 || >=22.0.0` | `dashboard/package.json` |
| pytest | `>=8.0,<10` | `tests/requirements-test.txt` |
| Live Anthropic SDK pin | `anthropic==1.8.0` | `tests/requirements-live-validation.txt`; `scripts/README.md` |

There is no `pyproject.toml` or `pytest.ini` at this commit. Pytest collection uses `tests/conftest.py`. Dashboard tests are configured in `dashboard/vite.config.ts` (`test.environment = 'jsdom'`).

AWS services that appear as Terraform resources include Lambda, DynamoDB, Kinesis, S3, EventBridge, API Gateway v2 HTTP, Kinesis Firehose, SQS, Glue, Athena, CloudWatch Logs/alarms/dashboards, and Secrets Manager. That is configuration in Git, not DEPLOYED.

## Developer entry points

| Task | Command or path | Notes |
| --- | --- | --- |
| Python venv | `.\.venv\Scripts\Activate` | Documented in `scripts/README.md` |
| Test dependencies | `python -m pip install -r tests/requirements-test.txt` | File lives under `tests/`, not repo root |
| Infrastructure test dependencies | `tests/requirements-infrastructure.txt` | Includes `-r requirements-test.txt` |
| Dashboard | `cd dashboard; npm install; npm run dev` | [`dashboard/README.md`](../../dashboard/README.md) |
| Unit tests | `.\scripts\run-unit-tests.ps1` | Default `-Target aircraft` |
| Infrastructure tests | `.\scripts\run-infrastructure-tests.ps1` | Terraform and/or AWS |
| Integration tests | `.\scripts\run-integration-tests.ps1` | Requires deployed `environment = dev` |
| Dashboard verify | `npm run verify` in `dashboard/` | lint, typecheck, vitest, build |

`scripts/run-unit-tests.ps1` looks for `requirements-test.txt` at the repository root. At `8abd2d5` the file is `tests/requirements-test.txt`. Install from the path that exists.

## Known limitations

- Root `requirements.txt` is a local/notebook pin set. It is not the Lambda runtime manifest. Lambda components have their own `functions/**/requirements.txt` files.
- `.gitignore` ignores `*.json` with exceptions for `functions/shared/wilvor_operational/data/us_states.meta.json` and `tests/fixtures/live_ops_public_claim_snapshots.json`.
- `tools/` contains a local OpenSky poller helper. It is not a Terraform resource.

## Historical record

No superseded statement is stored on this page.

## Sources

- Source: repository directories at `8abd2d5`
- Source: `envs/dev/providers.tf`
- Source: `envs/dev/terraform.tfvars`
- Source: `dashboard/package.json`
- Source: `tests/requirements-test.txt`
- Source: `scripts/dev-up.ps1`
- Source: `scripts/run-unit-tests.ps1`
