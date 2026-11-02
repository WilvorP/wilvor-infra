# Wilvor Step 1 CI

`.github/workflows/ci.yml` is the single entry point. No deployment workflow
or credentials are configured. Require **CI quality gate** in branch protection.

| Event | Profile |
| --- | --- |
| Feature-branch push | No run |
| Pull request targeting `main` | PR |
| Push/merge to `main` | Full offline |
| Manual `workflow_dispatch`, on the selected ref | Full offline |

Concurrent runs of the same PR are cancelled when superseded. Main/manual
runs are not cancelled in progress. There are no path filters that could
silently omit required checks.

## Jobs and coverage

All Python jobs initially use Windows and Python 3.11, matching the verified
baseline. The Terraform job requires Windows PowerShell for historical script
tests, and uses Terraform 1.9.8. The dashboard uses Node 22 and `npm ci`.
Local verification used Node 22.14.0; setup-node resolves the maintained 22.x
patch on GitHub, just as setup-python resolves the current 3.11 patch.

| Owner/job | Automatically discovered tests | PR | Full |
| --- | --- | --- | --- |
| Workflow security/discovery | `tests/unit/ci`, actionlint, workflow policy, collection accounting | Yes | Yes |
| Python: pipelines | aircraft, SIGMET, METAR, TAF, encounter, projection, risk, recommendations, runway metadata, airport status, active alerts | Deferred | All |
| Python: read-models | `tests/unit/operational_api`, `tests/unit/shared` | Deferred | All |
| Python: history | `tests/unit/historical_facts`, `tests/unit/historical_query` | Deferred | All |
| Python: script-safety | `tests/unit/scripts`, including fake/dry-run live-harness safety regressions | All | All |
| Python: copilot | Entire `tests/contracts`, including all Master/specialist/provider/evidence/authorization/cross-agent safety suites | All | All |
| Terraform/infrastructure | `tests/infrastructure/terraform` and top-level AI auth-route contracts | Static/PowerShell tests + formatting; no provider initialization | All offline cases + both-root validation |
| Dashboard | ESLint and TypeScript | Yes | Yes |
| Dashboard | All configured Vitest cases, coverage and production build | Deferred | Yes |
| CI quality gate | Required job status, ownership/report completeness, coverage aggregation | Yes | Yes |

PR CI keeps **every Copilot contract and script-safety case**. It saves work
by deferring three application groups, three Terraform CLI cases/provider
initialization, and dashboard tests/build. PR Python execution omits coverage
instrumentation to reduce the critical-path cost of the mandatory Copilot
suite; JUnit and exact case accounting remain required. Full CI executes every eligible
offline test. The Python matrix is generated from the same inventory/profile
used by the gate; profile checks are not separately duplicated test jobs.

Observed starting inventory: 426 pipelines, 275 read-models, 396 history,
144 script-safety, 2,809 Copilot, 120 infrastructure, 26 new CI helper cases.
That is **4,196 Python cases**, plus the existing 342 dashboard cases. These
are observations, not ceilings or hardcoded passing counts. PR selection is
3,096 Python cases; the full profile adds 1,100 more. No tests are selected
by a fragile keyword such as `not anthropic` or `not integration`.

## Discovery and adding tests

`scripts/ci_groups.py` is the ownership map. `tests/ci_plugin.py` records actual
pytest node IDs, including parametrizations. Discovery collects **all** tests
under `tests` with the offline guard enabled; fixtures/test bodies do not run
in collection mode. Every collected case must have exactly one owner or an
explicit external/deployed exclusion. Duplicate or unknown ownership fails.

Add files/cases normally inside an existing domain/contract directory: no
workflow/count update is needed. For a new top-level test domain, register an
explicit owner in `ci_groups.py`; the discovery job fails until this is done.
The job recollects its selected files and verifies an exact node-ID match
against discovery. The gate verifies the union of executed Python cases,
detecting missing, duplicate, extra, failed or skipped cases. No count is
fixed at 4,170/4,196. The two new parser regressions and new CI regressions
remain part of normal infrastructure/unit discovery.

Explicit exclusions remain visible with collected counts:

- `tests/infrastructure/aws`: deployed AWS checks (currently 64).
- `tests/integration`: deployed development integration (currently eight).
- `tests/infrastructure/terraform/test_plan_safety.py`: supplied-plan checks
  (currently two), deferred to Step 2. CI never generates a plan for them.

Smoke/replay placeholders currently collect no cases. A new suite there must
be classified explicitly. `tests/manual_mapper_test.py` has module assertions
but zero collected cases; do not mistake it for an automated suite count.

## Local commands

Use the existing virtual environment or install the repository's CI manifest:

```powershell
python -m pip install -r tests/requirements-ci.txt
python scripts/ci_bootstrap.py  # setup-only, checksum-verified actionlint 1.7.7
./test-results/ci-tools/actionlint/actionlint.exe -shellcheck= -pyflakes= .github/workflows/ci.yml
python scripts/ci.py policy
python scripts/ci.py discover --profile full
python scripts/ci.py run --group copilot
```

Other Python group names are `pipelines`, `read-models`, `history`,
`script-safety`, `infrastructure`, and `governance`. Every group command uses
`scripts/run_offline_tests.py`, explicit pytest plugins and JUnit. Full-profile
commands also collect branch coverage. To inspect PR accounting without overwriting full accounting:

```powershell
python scripts/ci.py discover --profile pr --inventory test-results/ci/pr-inventory.json
python scripts/ci.py run --group script-safety --inventory test-results/ci/pr-inventory.json
```

With Terraform 1.9.8 on PATH:

```powershell
python scripts/ci_terraform.py --profile full
$env:TF_CLI_CONFIG_FILE = Join-Path (Get-Location) 'test-results/ci/terraform/validation.tfrc'
python scripts/ci.py run --group infrastructure
```

Terraform setup uses `init -backend=false -input=false -lockfile=readonly`.
It verifies lockfile hashes, uses an inert CLI config, disables version-check
network requests, and never authenticates to AWS. Both roots are validated
in isolated source/lockfile copies prepared by the tested
`scripts/prepare_terraform_validation.py`. Placeholder ZIPs contain only a
warning file, no handler; never deploy them. Actual source roots are also
initialized so existing CLI assertions execute. Existing state and real
packaging artifacts are not uploaded. `--plugin-dir` can reuse a local mirror.

For the dashboard, from `dashboard/`, using Node 22:

```powershell
npm.cmd ci --no-audit --no-fund  # setup downloads allowed
$env:CI = 'true'               # two Vitest workers, unchanged test timeouts
$env:NODE_OPTIONS = '--require=./scripts/offline.cjs'
npm.cmd run lint
npm.cmd run typecheck
node node_modules/vitest/vitest.mjs list --json=../test-results/ci/dashboard/expected.json --no-cache
npm.cmd test -- --coverage --reporter=default --reporter=junit --reporter=json --outputFile.junit=../test-results/ci/dashboard/junit.xml --outputFile.json=../test-results/ci/dashboard/vitest.json
npm.cmd run build
Remove-Item Env:NODE_OPTIONS
```

From the repository root, `python scripts/ci.py dashboard --profile full`
checks list-versus-execution accounting and publishes the safe report. The
V8 coverage provider is pinned to the locked Vitest version. Build already
typechecks too; the explicit typecheck step gives a separate diagnostic.

For an ordinary combined Python rerun (without CI group reports):

```powershell
python scripts/run_offline_tests.py -- -q -ra
```

## Reports and failures

Each test job uploads an explicitly allowlisted `ci-results-<owner>` artifact,
even on failure. It includes sanitized JUnit, status/node-ID accounting and
coverage in the full profile. Captured stdout/stderr is removed from uploaded JUnit; raw provider
reports, state, `.env`, tfvars, local archives, browser builds and logs are not
artifact inputs. Only synthetic offline fixtures are used by tests.

Job summaries show discovery counts, explicit exclusions, execution outcomes
and the final gate. The gate requires successful (not skipped/cancelled) job
families, all expected case results/JUnit, full-profile coverage, and both Terraform
validation reports in the full profile. Matrix fail-fast is disabled so one
failure does not hide other results. No `continue-on-error` or arbitrary
coverage threshold is used. Python coverage data is combined with relative
source paths; dashboard coverage stays separate. Python coverage measures
the instrumented pytest process; fresh import-isolation child interpreters
are safety checks and are not claimed as instrumented subprocess coverage.

The aggregate artifact contains `gate.json` and, for full runs, combined Python coverage.
Coverage is a measurement, not an invented pass percentage. Missing reports,
unexpected skips, collection drift and setup failures fail the gate. A
GitHub-hosted result exists only after the workflow actually runs there.

## Security and Step 2

All action revisions are full commit SHAs and are checked against the policy
allowlist in `scripts/ci.py`. Review/update both the workflow and allowlist
when updating an action; rerun actionlint/security regressions. Permissions
are only `contents: read`; checkout does not persist credentials. No secrets,
AWS authentication, SSO, OIDC, model credentials, deployment or CD are used.
PR-controlled strings never enter inline shell expressions. Profile and matrix
values travel through environment variables and validated fixed group names.

Downloads are confined to setup. Python tests retain the existing inherited
socket guard and dummy SDK-import credentials; the dashboard preload denies
external socket/DNS operations while allowing local test-runner IPC. Terraform
validation accesses provider schemas, not deployed resources. Artifacts are
retained seven days and never include broad `test-results/**` uploads.

Step 2 can add a separately authorized integration job family for the explicit
AWS/integration/plan exclusions, with its own credentials/environment and gate
status. Keep offline owners, reporting schema and no-credential jobs intact;
extend the ownership/security/gate tests deliberately instead of moving live
tests into an offline group.
