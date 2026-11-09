# Offline Python baseline — 2026-11-01

The Terraform follow-up in [TERRAFORM_BASELINE.md](TERRAFORM_BASELINE.md)
supersedes the infrastructure blockers below. This document preserves the
earlier run and fixes as historical evidence.

Branch: `ci-step1`, based on `a30cb31` from the locally recorded
`origin/ai-operations-copilot`. No production files or workflows were changed.

## Reproduce

From the repository root, with the existing test dependencies installed:

```powershell
.\.venv\Scripts\python.exe -B scripts/run_offline_tests.py -- -q -ra --tb=short --junitxml=test-results/offline-baseline/full.xml
```

On other operating systems, use the corresponding Python interpreter instead
of `.venv\Scripts\python.exe`. The historical analytics PowerShell tests
currently require Windows PowerShell and Windows file-URI behavior.

The runner selects these roots:

```text
tests/unit
tests/contracts
tests/infrastructure/terraform
tests/infrastructure/test_sprint3b_ai_auth_route.py
```

It explicitly excludes only `test_plan_safety.py` (two supplied-plan tests).
The deployed `tests/infrastructure/aws` (64 cases) and `tests/integration`
(eight cases) are outside these roots. There is no plan generation, Terraform
initialization, AWS authentication, package installation or live validation.

The runner removes inherited AWS settings, live-validation opt-ins,
`ANTHROPIC_API_KEY`, Terraform directory/plan overrides and pytest option
overrides. It sets dummy credentials, disables instance metadata and pytest
plugin autoload, and prepends `tests/offline_bootstrap` to `PYTHONPATH`.
That directory's `sitecustomize.py` installs a Python audit hook blocking
external socket connections, sends and DNS. Python children inherit it.
Literal loopback IP addresses remain available for Windows asyncio's local
wake-up socketpair. This guard does not sandbox arbitrary non-Python tools;
the selected PowerShell tests use dry runs and fake AWS CLI functions.

To select a smaller suite, supply explicit targets before pytest options:

```powershell
.\.venv\Scripts\python.exe -B scripts/run_offline_tests.py tests/unit/aircraft tests/unit/airport_status -q -ra
```

## Environment used

Windows, Python 3.11.3, pytest 9.1.1, pytest-cov 7.1.0, boto3/botocore
1.43.36, h3 4.2.2, Shapely 2.1.2, LangGraph 1.2.16, python-hcl2 8.1.2,
Lark 1.3.1. Terraform is not available on PATH. These results do not certify
a fresh dependency installation or Python 3.12 execution.

## Verified failures and corrections

The initial combined unit/contract run collected 4,050 cases and finished
with **4,027 passed, 22 failed, one skipped**, plus two warnings.

- All 15 documented aircraft failures reproduced. Shared fixtures were still
  schema v1, omitted S3 lineage and lacked v2 identity/freshness/provenance
  fields. Negative cases omitted unrelated required envelope fields. Writer
  mocks expected an obsolete stale status, mapper mocks omitted the current
  keyword arguments, and the poller test omitted required lineage arguments.
  Tests now use the existing v2 contract. The mapper test freezes both clock
  entry points rather than assuming `now_epoch` controls `datetime.now`.
- The airport-status handler failure reproduced: event publication was not
  mocked. The test now records publication and asserts the written item was
  published. The writer handler likewise records its event publication.
- Four SIGMET coordinate tests passed in isolation but failed together with
  runway tests. Their generic `import app` returned the already cached runway
  loader module. They now use a function-scoped fixture, scoped environment
  settings and a unique module name through `load_repo_module`.
- Two additional failures were introduced by the first version of the
  offline guard: Windows asyncio could not create its loopback socketpair,
  followed by an AST error during failed-event-loop cleanup. The guard now
  allows literal loopback addresses. Both Copilot tests pass in the affected
  rerun; external DNS/connect denial and child-process inheritance were also
  checked directly. No production AI behavior was changed.
- The ignored historical report was absent, so the original regression
  skipped. `historical_targeted_retest_summary.json` is an explicitly synthetic
  summary reconstructed from the existing assertions, not a recovered live
  report. It contains no provider payloads, secrets, operational records or
  real identifiers. The same acceptance and immutability assertions now run
  unconditionally against that tracked fixture. It tests summary evaluation,
  not historical live-provider behavior.
- `infrastructure` and `integration` markers are registered in `pytest.ini`.
  Strict marker checking does not exclude any tests.

The affected rerun passed **282 cases**, including runway and SIGMET together,
the historical fixture and the two affected Copilot tests. A reverse-order
run (SIGMET coordinates before runway, then aircraft and airport status)
passed **95 cases**.

The geographic artifact was not modified: raw SHA-256
`83cd47c5449be7559ef9595afc881718f11175f65542c743c7ae5d831d5b12c5`
matches its metadata and the file has no CRLF bytes.

## Remaining infrastructure blockers

The separate static infrastructure run finished with **108 passed, seven
setup errors, zero skipped**. Installed python-hcl2 rejects `envs/dev/main.tf`
at line 498, where `.aircraft_hazard_encounter_table_name` begins on a new
line after `module.encounter`. Seven root-contract cases share that parse
failure. Terraform itself was unavailable, so this does not establish whether
Terraform's parser accepts that syntax. The source was not rewritten or
normalized inside the tests to hide the problem.

The three Terraform CLI tests also require Terraform on PATH and initialized
provider/module metadata for validation. They remain selected in the full
suite, so a missing tool is an error rather than a skip. No initialization or
provider download was performed under the no-external-calls restriction.

The applicable Python inventory is **4,168 cases**: 1,241 unit + 2,809 Copilot
contract + 118 offline infrastructure cases. This corrects the previous audit
summary's arithmetic (4,166); the collected suite counts themselves have not
changed. Reports are generated under ignored `test-results/offline-baseline`.

## Final combined result

The complete applicable offline Python run finished in 409.61 seconds with
**4,158 passed, zero failed assertions, 10 setup errors, zero skipped**:

| Suite | Passed | Errors | Skipped |
| --- | ---: | ---: | ---: |
| Unit | 1,241 | 0 | 0 |
| AI Copilot contracts | 2,809 | 0 | 0 |
| Offline infrastructure and auth-route contracts | 108 | 10 | 0 |
| Total | 4,158 | 10 | 0 |

The 10 errors are the seven HCL root-contract parse errors and three missing
Terraform executable errors described above. The run returned pytest exit
code 1 and produced `test-results/offline-baseline/full.xml`. No test bodies
were deselected or suppressed to achieve the application/AI pass totals.

The application and AI suites now have a verified combined offline baseline.
The complete offline quality gate is **not ready to be green in GitHub
Actions** until the HCL parser/source compatibility issue is resolved and
Terraform formatting/validation/version checks are actually verified in a
prepared environment. No production correction was attempted for those
blockers. A clean installation and Python 3.12 verification remain future
validation work, not claims made by this current-environment run.

## Test commands executed

The direct pytest commands used this PowerShell environment setup (the network
bootstrap was subsequently adjusted to permit literal loopback IPC):

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'tests/offline_bootstrap'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:AWS_EC2_METADATA_DISABLED='true'
$env:AWS_ACCESS_KEY_ID='testing'
$env:AWS_SECRET_ACCESS_KEY='testing'
$env:AWS_SESSION_TOKEN='testing'
$env:AWS_DEFAULT_REGION='us-west-1'
$env:AWS_REGION='us-west-1'
Remove-Item Env:AWS_PROFILE,Env:WILVOR_AWS_PROFILE,Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
```

Each invocation below ran from the repository root. Output was captured to
logs; JUnit paths identify each run unambiguously.

```powershell
# Initial combined baseline (before corrections).
.\.venv\Scripts\python.exe -B -m pytest tests/unit tests/contracts -q -ra -p no:cacheprovider --tb=short --junitxml=test-results/offline-baseline/before.xml

# Static infrastructure (no Terraform executable required).
.\.venv\Scripts\python.exe -B -m pytest tests/infrastructure/terraform tests/infrastructure/test_sprint3b_ai_auth_route.py --ignore=tests/infrastructure/terraform/test_plan_safety.py --ignore=tests/infrastructure/terraform/test_terraform_cli.py -q -ra --strict-markers -p no:cacheprovider --tb=short --junitxml=test-results/offline-baseline/static-infrastructure.xml

# Reproduce the documented aircraft and airport failures in isolation.
.\.venv\Scripts\python.exe -B -m pytest tests/unit/aircraft tests/unit/airport_status -q -ra --strict-markers -p no:cacheprovider --tb=short --junitxml=test-results/offline-baseline/isolated-aircraft-airport.xml

# Intermediate affected rerun: two mapper mocks still needed current keyword arguments.
.\.venv\Scripts\python.exe -B -m pytest tests/unit/aircraft tests/unit/airport_status tests/unit/scripts/test_validate_historical_specialist_anthropic_live.py tests/unit/sigmet -q -ra --strict-markers -p no:cacheprovider --tb=short --junitxml=test-results/offline-baseline/affected.xml

# Final affected rerun, including known order interactions and Copilot guard fallout.
.\.venv\Scripts\python.exe -B scripts/run_offline_tests.py tests/unit/aircraft tests/unit/airport_status tests/unit/runway_metadata tests/unit/sigmet tests/unit/scripts/test_validate_historical_specialist_anthropic_live.py tests/contracts/test_live_ops_public_master_mock.py tests/contracts/test_live_ops_specialist_import_isolation.py -q -ra --tb=short --junitxml=test-results/offline-baseline/affected-final.xml

# Opposite collection order for the module-collision regression.
.\.venv\Scripts\python.exe -B scripts/run_offline_tests.py tests/unit/sigmet/test_hazard_coordinates_processor.py tests/unit/runway_metadata tests/unit/aircraft tests/unit/airport_status -q -ra --tb=short --junitxml=test-results/offline-baseline/reversed-order.xml

# Tracked historical fixture alone: one passed, no ignored report required.
.\.venv\Scripts\python.exe -B scripts/run_offline_tests.py tests/unit/scripts/test_validate_historical_specialist_anthropic_live.py::test_targeted_report_fixture_reevaluates_without_mutation -q -ra --tb=short --junitxml=test-results/offline-baseline/fixture-only.xml

# Final complete applicable offline Python suite; Terraform CLI remains selected.
.\.venv\Scripts\python.exe -B scripts/run_offline_tests.py tests/unit tests/contracts tests/infrastructure/terraform tests/infrastructure/test_sprint3b_ai_auth_route.py -q -ra --tb=short --junitxml=test-results/offline-baseline/full.xml
```

The isolated aircraft/airport run was **59 passed, 16 failed, zero skipped**.
The intermediate affected rerun was **257 passed, two failed, zero skipped**.
The corrected affected, reverse-order and fixture-only runs were respectively
**282 passed**, **95 passed** and **one passed**, with no failures or skips.

## Files changed

```text
.gitignore
pytest.ini
scripts/run_offline_tests.py
tests/OFFLINE_BASELINE.md
tests/conftest.py
tests/fixtures/historical_targeted_retest_summary.json
tests/offline_bootstrap/sitecustomize.py
tests/unit/aircraft/test_aircraft_current_state_writer.py
tests/unit/aircraft/test_aircraft_raw_processor.py
tests/unit/aircraft/test_opensky_mapper.py
tests/unit/aircraft/test_opensky_poller.py
tests/unit/airport_status/test_airport_status_materializer.py
tests/unit/scripts/test_validate_historical_specialist_anthropic_live.py
tests/unit/sigmet/test_hazard_coordinates_processor.py
```

JUnit XML and captured logs are under ignored `test-results/offline-baseline`.
`git diff --check` passed. No files under `functions`, `modules`, `envs`,
`dashboard` or `.github` were changed. No commits, pushes or branch changes
were made.
