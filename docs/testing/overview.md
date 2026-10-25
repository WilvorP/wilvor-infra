# Testing overview

**Canonical page:** this page for the test-layer map. Directory conventions: [`../../tests/README.md`](../../tests/README.md). Integration runner: [`../../tests/integration/README.md`](../../tests/integration/README.md). Infrastructure runner: [`../../tests/infrastructure/README.md`](../../tests/infrastructure/README.md).
**Verified against:** `8abd2d5` on `docs/ws1-platform-foundations`
**Maturity:** CODE, UNIT, CONTRACT, INFRA, INT, LIVE
**Production authorization:** no grant found. Integration and AWS infrastructure tests refuse non-`dev` Terraform outputs.

## Purpose

This page distinguishes the test layers that exist in the repository. It does not claim that any suite currently passes. Pass counts were not collected for this documentation.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `tests/` tree at `8abd2d5` |
| Terraform | TF | infrastructure tests read `envs/dev` |
| Unit tests | UNIT | `tests/unit/` |
| Contract tests | CONTRACT | `tests/contracts/` |
| Infrastructure tests | INFRA | `tests/infrastructure/` |
| Integration tests | INT | `tests/integration/{aircraft,sigmet,metar,taf}/` |
| Live harness | LIVE | `scripts/validate_*.py`; `tests/requirements-live-validation.txt` |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | `tests/integration/conftest.py` (`environment == "dev"`) |

## Layers

| Layer | Location | What it asserts | AWS / network |
| --- | --- | --- | --- |
| Unit | `tests/unit/` | Handlers, mappers, current-set helpers, historical transform/query, API repository, harness CLIs | Fake credentials in `tests/conftest.py`; no deployed account |
| Contract | `tests/contracts/` | AI/schema contracts, Master routing authority, specialist envelopes, import isolation | Offline. AI contract command in [`../../tests/README.md`](../../tests/README.md) |
| Infrastructure Terraform | `tests/infrastructure/terraform/` | HCL contracts, fmt/validate, plan safety, historical enablement | Reads source and optional plan files |
| Infrastructure AWS | `tests/infrastructure/aws/` | Identity, streams, tables, Lambdas, EventBridge, dashboards, secret metadata | Read-only against the authenticated `dev` account |
| Integration | `tests/integration/` | Controlled records through deployed aircraft, SIGMET, METAR, TAF pipelines | Writes then cleans up in `dev` |
| Live harness | `scripts/validate_*.py` and `validate_historical_analytics.ps1` | Operator opt-in checks | Depends on the harness; dual opt-in for Anthropic |
| Smoke | `tests/smoke/live/` | Placeholder only (`.gitkeep`) | none |
| Replay | `tests/replay/` | Placeholder only (`.gitkeep`) | none |

Dashboard tests are Vitest under `dashboard/src/**/*.test.{ts,tsx}` (source: `dashboard/vite.config.ts`, `dashboard/package.json` script `test`). They are not pytest.

## Unit

Directories at `8abd2d5`: `active_alerts`, `aircraft`, `airport_status`, `encounter`, `historical_facts`, `historical_query`, `metar`, `operational_api`, `projection`, `recommendations`, `risk`, `runway_metadata`, `scripts`, `shared`, `sigmet`, `taf`.

Examples of assertions that were read:

- `tests/unit/aircraft/test_opensky_mapper.py` (`test_unit_conversions`)
- `tests/unit/operational_api/test_current_alerts.py` (`test_active_alerts_list_returns_current_set_only`)
- `tests/unit/sigmet/test_hazard_station_candidates_processor.py` (`hazard.stations.ready` publication)

Runner: `scripts/run-unit-tests.ps1`. Default `-Target aircraft`. The script verifies a directory `tests\unit\<Target>` exists. Pytest can also run the whole tree:

```powershell
python -m pytest tests/unit -q -p no:cacheprovider
```

`tests/manual_mapper_test.py` is not named `test_*.py` inside a collected package path used by the unit runner; it is an ad-hoc mapper script.

## Contract

`tests/contracts/` contains 117 `test_*.py` modules at this commit, including Master routing, live-ops, historical specialist, decision tools, and import-isolation suites. Index: [`tests/contracts/README.md`](../../tests/contracts/README.md).

Example assertion: `tests/contracts/test_master_routing.py` `test_live_ops_only_is_bounded_and_not_executable_authority` requires `MasterRouteExecutionAuthority.DENIED` even when the plan outcome is `AUTHORIZED_PLAN`.

```powershell
python -m pytest tests/contracts -q -p no:cacheprovider
```

These tests are not a live model pass.

## Infrastructure

Terraform modules: `test_root_contract.py`, `test_plan_safety.py`, `test_terraform_cli.py`, `test_historical_facts_contract.py`, `test_historical_data_plane_contract.py`, `test_historical_analytics_contract.py`, `test_historical_analytics_query_iam_contract.py`, `test_historical_analytics_validation.py`.

`EXPECTED_MODULES` in `test_root_contract.py` lists `aircraft_foundation`, `weather_events`, `sigmet`, `metar`, and `taf` only. Later `envs/dev` modules are covered by the historical contract tests and by reading `envs/dev/main.tf` directly, not by that expected-module set.

AWS modules: `test_identity.py`, `test_compute.py`, `test_data_services.py`, `test_eventing_monitoring.py`, `test_secrets.py`. `test_tests_are_targeting_dev_environment` asserts `terraform_outputs["environment"] == "dev"`.

Runner: `scripts/run-infrastructure-tests.ps1 -Target terraform|aws|all`. Details: [`../../tests/infrastructure/README.md`](../../tests/infrastructure/README.md).

## Integration

Pipeline files:

- `tests/integration/aircraft/test_aircraft_pipeline.py`
- `tests/integration/sigmet/test_sigmet_pipeline.py`
- `tests/integration/metar/test_metar_pipeline.py`
- `tests/integration/taf/test_taf_pipeline.py`

`tests/integration/combined/` contains only `.gitkeep`. Downstream processors exist as Terraform modules in `envs/dev/main.tf`. Dedicated unit-test directories exist for `projection`, `encounter`, `risk`, `airport_status`, `recommendations`, and `active_alerts`. Search for `tests/unit/airport_assessment/` returned no directory. None of these processors have a file under `tests/integration/` at this commit.

`tests/integration/conftest.py` fails unless Terraform outputs report `environment = dev` and `name_prefix` ends with `-dev`.

Runner: `scripts/run-integration-tests.ps1`. Details: [`../../tests/integration/README.md`](../../tests/integration/README.md). INT means the test file exists and assertions were written for a deployed `dev` environment. It does not mean that environment is applied now.

## Live harness

Harness files (LIVE label, not a pass result):

| Harness | Opt-in | Claimed non-goals from the script or `scripts/README.md` |
| --- | --- | --- |
| `scripts/validate_historical_query_runtime.py` | action flags; dry-run vs live | not a generic SQL tool; dry-run makes zero AWS calls |
| `scripts/validate_historical_analytics.ps1` | `-DryRun` | dry-run must not call AWS |
| `scripts/validate_historical_specialist_anthropic_live.py` | `WILVOR_RUN_LIVE_ANTHROPIC=1` | no AWS, no Athena, no Bedrock |
| `scripts/validate_live_ops_specialist_anthropic_live.py` | `WILVOR_RUN_LIVE_LIVE_OPS_ANTHROPIC` | import does not load the SDK |
| `scripts/validate_decision_specialist_anthropic_live.py` | `WILVOR_RUN_LIVE_DECISION_ANTHROPIC` | dry-run: no AWS, no network |
| `scripts/poll-all-once.ps1` | include switches | METAR/TAF skipped unless explicitly included |

Unit tests exist for some harness CLIs under `tests/unit/scripts/`. Contract tests exist for the Live Ops Anthropic harness (`tests/contracts/test_live_ops_anthropic_live_harness.py`). Those tests check gates offline.

Stored JSON under `test-results/live-anthropic/` is historical evidence of a past run when present. It is not a new live pass (source: `functions/shared/wilvor_ai/README.md`). This documentation task did not read those artifacts as a current result.

## Smoke and replay

`tests/smoke/live/` and `tests/replay/` contain only `.gitkeep`. The [`../../tests/README.md`](../../tests/README.md) descriptions of live smoke and replay are the intended boundary, not implemented pytest modules.

## Runners and reports

| Runner | JUnit destination |
| --- | --- |
| `scripts/run-unit-tests.ps1` | `test-results/unit/` |
| `scripts/run-infrastructure-tests.ps1` | `test-results/infrastructure/` |
| `scripts/run-integration-tests.ps1` | `test-results/integration/` |

`test-results/` is gitignored. There is no single orchestrator script that runs every layer. There is no Markdown report generator in `scripts/` at this commit.

## Known limitations

- Suite pass/fail today is UNKNOWN.
- `scripts/run-unit-tests.ps1` looks for repo-root `requirements-test.txt`; the file is `tests/requirements-test.txt`.
- Fixture payload directories under `tests/fixtures/{aircraft,sigmet,metar,taf}` are `.gitkeep` placeholders. Integration tests build records in code via `tests/helpers/integration_support.py`.
- Production resources must never be targeted (`tests/README.md` test boundaries). Only `environment = dev` is accepted by integration and AWS infrastructure tests.

## Historical record

No superseded statement is stored on this page. The older `tests/README.md` directory tree is preserved under that file's historical record.

## Sources

- Source: `tests/` directory tree at `8abd2d5`
- Source: `tests/conftest.py`
- Source: `tests/integration/conftest.py`
- Source: `tests/infrastructure/aws/test_identity.py` (`test_tests_are_targeting_dev_environment`)
- Source: `scripts/run-unit-tests.ps1`
- Source: `scripts/run-infrastructure-tests.ps1`
- Source: `scripts/run-integration-tests.ps1`
- Tests: `tests/contracts/test_master_routing.py` (`test_live_ops_only_is_bounded_and_not_executable_authority`)
- Tests: `tests/infrastructure/terraform/test_root_contract.py` (`EXPECTED_MODULES`)
