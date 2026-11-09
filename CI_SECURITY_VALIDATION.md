# CI label and security follow-up verification

Local verification on 2026-11-01, branch `ci-step1`. This is not a GitHub-hosted
CI result. No commit, push, merge or branch operation was performed.

## Commands and outcomes

From the repository root (Python executable was `.venv/Scripts/python.exe`):

```powershell
python scripts/run_offline_tests.py tests/unit/ci -q -ra --tb=short --junitxml=test-results/security-tools/ci-regressions.xml
python scripts/ci.py policy
./test-results/ci-tools/actionlint/actionlint.exe -shellcheck= -pyflakes= .github/workflows/ci.yml
python scripts/ci.py discover --profile full --inventory test-results/ci/security-full-inventory.json
```

- CI regressions: **63 passed**, including 37 added cases. Coverage includes
  label add/remove lifecycle events, ordinary/full profile selection, policy
  mutations, clean/missing/finding/error scanner reports, sanitization and
  positive/negative aggregate-gate execution. No skips or failures.
- Workflow policy and actionlint 1.7.7: passed.
- Full collection: **4,233 eligible Python cases**, plus 74 explicit Step 2
  exclusions. Compared node-ID sets with the saved pre-change inventory:
  every one of the previous 4,196 cases remains eligible; all 37 additions
  belong to `tests/unit/ci/test_security.py`. No removed/duplicated cases or
  new application exclusions. Application test bodies were not rerun.
- Profile simulations using `scripts.ci_groups.profile_for_event` and the actual
  collected inventory: ordinary PR = PR/3,133; `ci-full` PR = full/4,233;
  removing the label = PR/3,133; main push and dispatch = full/4,233. Policy
  rejects feature-branch push triggers and missing label lifecycle events.

With the existing Node 22.14.0 tool directory prepended to PATH, from `dashboard/`:

```powershell
$env:CI = 'true'
$env:NODE_OPTIONS = '--require=./scripts/offline.cjs'
node node_modules/vitest/vitest.mjs list --json=../test-results/security-tools/dashboard-collection.json --no-cache
```

Collected **342 dashboard cases**. Initial esbuild collection was denied by the
local sandbox; an authorized retry outside that sandbox passed with the offline
guard retained. No dashboard source, lockfile, configuration or execution steps
changed, so existing application test bodies were not unnecessarily rerun.

With Node 22 on PATH, all scanner bootstraps and scans were executed:

```powershell
foreach ($scan in @('secrets','terraform-security','python-dependencies','npm-dependencies')) {
    python scripts/ci_security.py bootstrap --scan $scan
    python scripts/ci_security.py run --scan $scan
}
git diff --check
```

Public setup/advisory access was authorized. Gitleaks and Trivy downloads passed
their pinned SHA-256 checks. A conflicting npm temporary-config path was corrected
and npm audit rerun successfully. Gitleaks was rerun after making current-tree
locations repository-relative; Trivy was rerun from an isolated directory to
prevent repository-local scanner configuration from suppressing checks.

| Scanner | Version | Outcome |
| --- | --- | --- |
| Gitleaks | 8.30.1 | Completed, exit 1: 10 test-fixture candidates, pending review |
| pip-audit | 2.10.1 | All ten contexts complete, exit 1: 14 records / 13 unique advisories in developer snapshot |
| npm audit | 11.6.2 | Completed, exit 1: 22 affected package records |
| Trivy config | 0.76.1 | Completed, exit 1: 99 findings, including two high |

`validate_security_reports` was also called with all four actual sanitized
reports and correctly rejected them. Clean-report gate fixtures pass; missing
and finding-bearing report fixtures fail. This is expected failure propagation,
not a passing security baseline. Exact findings and remediation are in
[SECURITY_FINDINGS.md](SECURITY_FINDINGS.md). `git diff --check` passed.

## Verdict

**PARTIAL:** workflow implementation, discovery, profile selection and regression
validation pass. The required security baseline remains blocked by advisory,
Terraform and secret-candidate findings. No suppressions, production behavior
changes, AWS authentication/API calls, infrastructure mutation, live aviation
calls or real LLM calls were used. Artifacts contain sanitized identifiers and
locations only. The next GitHub run should remain red until findings are resolved.

## Gitleaks-only follow-up, 2026-11-03

The preceding scanner results are the original snapshot. This follow-up inspected
only the ten Gitleaks candidates and reran no dependency or Terraform scanner.
All ten candidates were confirmed synthetic: five occurrences duplicated across
history/current source. Captured `git show <commit>:<file>` source matched the
original current lines and showed the same test-only context. No matched value
was printed, no real service was contacted, and no credential rotation was needed.

Corrections were limited to three test fixture files:

- `tests/contracts/test_sprint3c_scoped_read.py`: two calls formatted across
  multiple lines; the complete Python module AST is identical.
- `tests/contracts/test_master_specialist_runtime_contract.py`: one synthetic
  default idempotency nonce shortened; assertions and authorization behavior intact.
- `dashboard/src/api/operationalApi.test.ts`: two unsigned pagination fixtures
  built from readable JSON with `btoa(JSON.stringify(...))`. Both original Base64
  byte sequences and all encoding/security assertions are preserved.

Executed verification commands (repository-root Python was
`.venv/Scripts/python.exe`, Node 22.14.0 was prepended to PATH):

```powershell
python scripts/run_offline_tests.py tests/contracts/test_sprint3c_scoped_read.py tests/contracts/test_master_specialist_runtime_contract.py tests/unit/ci -q -ra --tb=short --junitxml=test-results/security-tools/gitleaks-regressions.xml
python scripts/ci_security.py run --scan secrets
python scripts/ci.py discover --profile full --inventory test-results/ci/gitleaks-full-inventory.json
# From dashboard/, with CI=true and NODE_OPTIONS=--require=./scripts/offline.cjs:
npm.cmd test -- src/api/operationalApi.test.ts --reporter=default --reporter=junit --outputFile.junit=../test-results/security-tools/gitleaks-dashboard-junit.xml
node node_modules/vitest/vitest.mjs list --json=../test-results/security-tools/gitleaks-dashboard-collection.json --no-cache
git diff --check
```

Results: **91 Python tests passed** (28 affected contracts and 63 CI regressions),
**16 affected dashboard tests passed**, no skipped/failed tests. Full Python
collection is exactly identical to the prior inventory: **4,233 eligible cases**
and 74 explicit Step 2 exclusions. Dashboard collection remains **342 cases**.

Initial Gitleaks/dashboard attempts encountered sandbox Git/filesystem access
restrictions, not test failures. Authorized retries outside the sandbox completed;
the dashboard offline network guard remained enabled. Final Gitleaks 8.30.1
status is **complete, exit 1, five historical findings and zero working-tree
findings**. Scanner implementation, rules, history and exclusions were unchanged.
The detailed ten-row investigation is in `SECURITY_FINDINGS.md`.

**PARTIAL:** all safe current-fixture corrections pass. Five historical synthetic
findings remain blocking until an explicitly approved, narrowly scoped exception
or coordinated history rewrite is chosen. Neither option was implemented. No
other scanner, dependency manifest, Terraform configuration, production behavior,
AI authorization rule or workflow was changed in this follow-up.

## Approved historical fingerprints, 2026-11-03

The user explicitly approved exact `.gitleaksignore` exceptions for G1-G5.
The preceding five historical blockers are resolved by these exceptions; history
was not rewritten. This does not resolve the other scanners' earlier findings.

Before changing ignore behavior, Gitleaks 8.30.1 was rerun using the CI execution
path with an empty ignore file. The sanitizer retained the scanner's emitted
`Fingerprint` metadata (never `Secret` or `Match`). All five were verified against
the complete commit hash, file, `generic-api-key` rule and original line. The
exact entries and individual justifications are in `SECURITY_FINDINGS.md` and
the five-entry `.gitleaksignore` file.

Both `python scripts/ci.py policy` and scanner startup validate that the ignore
file contains exactly the approved set, without duplicates, additions or changes.
The scanner uses those entries only for history; it uses an empty ignore file
for current source. Built-in rules, `--log-opts=--all`, disabled inline allow
comments, redaction, environment isolation and exit-code propagation are intact.

Verification commands (Python was `.venv/Scripts/python.exe`):

```powershell
python scripts/ci_security.py run --scan secrets
python scripts/run_offline_tests.py tests/unit/ci -q -ra --tb=short --junitxml=test-results/security-tools/fingerprint-regressions.xml
python scripts/ci.py policy
python scripts/ci.py discover --profile full --inventory test-results/ci/fingerprint-full-inventory.json
./test-results/ci-tools/actionlint/actionlint.exe -shellcheck= -pyflakes= .github/workflows/ci.yml
python test-results/security-tools/verify_fingerprint_negative_control.py
python scripts/ci_security.py run --scan secrets
git diff --check
```

The local negative-control helper generated a noncredential canary at an
otherwise unused, nonignored repository path. It verified ordinary discovery,
invoked `scripts/ci_security.py run --scan secrets` as a child process (no scanner
mocks or scope substitutions), and required exit 1 plus a new working-tree
`generic-api-key` finding outside the five historical fingerprints. It passed;
history stayed clean, no value was printed, and a `finally` block removed the
temporary file. Only sanitized metadata was retained in the ignored validation
directory. The subsequent clean scan uses the same CI command.

Results: **85 CI regressions passed**, including 22 new exact-exception, policy,
invocation, offline-safeguard and failure-propagation cases. Full Python discovery
retains every previous 4,233 case and adds only those 22 CI regressions:
**4,255 eligible cases**, with the same 74 Step 2 exclusions. Application and
dashboard tests/configuration were not changed or unnecessarily rerun.
Workflow policy, actionlint and whitespace checks passed.

Final Gitleaks 8.30.1 result: **PASS, exit 0, zero findings, exactly five approved
historical fingerprint exceptions**. The negative control separately verified
that a new finding fails CI. No pip-audit, npm audit, Trivy, cloud operations,
credential rotation, Git write operation or production behavior change occurred.
Verdict for this fingerprint-remediation task: **PASS**. The previously documented
dependency/Terraform findings remain unchanged and were not revalidated here.

## Python dependency remediation, 2026-11-05

Scope was limited to Python dependency vulnerabilities and audit infrastructure.
Gitleaks exceptions/tests, application/provider behavior, Lambda requirements,
and npm/Terraform findings were preserved. No other scanner was rerun.

Root `requirements.txt` is retained and audited as a developer/notebook reference
freeze. CI uses `tests/requirements-ci.txt`; packaging scripts use their own
Lambda requirement files. The root freeze includes NumPy 2.5.0 (Python >=3.12)
and is not a Python 3.11 CI installation entry point. Regenerating it here would
force unrelated dependency changes. Only these three pins changed:

| Dependency | Before | After |
| --- | --- | --- |
| certifi | 2026.6.17 | 2026.10.18 |
| tornado | 6.5.7 | 6.6 |
| urllib3 | 2.7.0 | 2.8.0 |

Reverse-replacing those three version strings reproduced the original Git blob
exactly: UTF-16LE BOM, 44 CRLF lines and every unrelated byte were preserved.
The original 14 records / 13 unique advisories and the classification for each
dependency are recorded with resolved outcomes in `SECURITY_FINDINGS.md`.

Public PyPI release metadata and a wheel-only resolver dry run verified the
three patched packages against unchanged Requests 2.34.2, botocore 1.43.36,
ipykernel 7.3.0 and jupyter_client 8.9.1. No complete Python >=3.12 notebook
environment installation is claimed; only Python 3.11 is installed here.

The audit now independently covers all **12** requirements manifests, including
the two previously audited via nested includes. Mandatory owners cannot disappear
or be renamed silently. New supported requirements files are discovered; unknown
packaging manifests fail for classification review. Empty active requirements,
unclassified includes/options, non-public sources, unpinned root snapshot entries,
unresolved/skipped dependencies and empty/error advisory responses fail closed.
Only the existing stdlib-only historical transform may contain no dependencies.

Executed validation (Python was `.venv/Scripts/python.exe` unless specified):

```powershell
python scripts/ci_security.py run --scan python-dependencies
python test-results/security-tools/verify_python_remediation.py
# Isolated fresh CI environment installed the actual CI manifest plus these patches:
python -m venv test-results/python-remediation/ci-venv
./test-results/python-remediation/ci-venv/Scripts/python.exe -m pip install -r tests/requirements-ci.txt certifi==2026.10.18 urllib3==2.8.0 --report test-results/python-remediation/install.json
./test-results/python-remediation/ci-venv/Scripts/python.exe -m pip check
./test-results/python-remediation/ci-venv/Scripts/python.exe -B scripts/run_offline_tests.py tests/unit -q -ra --tb=short --junitxml=test-results/python-remediation/unit-junit.xml
python -m pip install certifi==2026.10.18 urllib3==2.8.0 --report test-results/python-remediation/local-transport-upgrade.json
python -m pip check
python scripts/run_offline_tests.py tests/unit/ci -q -ra --tb=short --junitxml=test-results/security-tools/python-audit-regressions.xml
python scripts/ci.py policy
python scripts/ci.py discover --profile full --inventory test-results/ci/python-audit-full-inventory.json
./test-results/ci-tools/actionlint/actionlint.exe -shellcheck= -pyflakes= .github/workflows/ci.yml
git diff --check
```

Setup/advisory subprocesses used the scanner's sanitized environment and public
registries. The initial sandbox socket restriction was retried with authorized
public network access. The interrupted server was recovered by checking files
and process/report state before restarting incomplete validations.

Actual results:

- **pip-audit 2.10.1: PASS, exit 0, zero findings, all 12 contexts complete.**
  The root's 44 declared packages remain included. CI/test/infrastructure,
  live-validation and Lambda contexts resolve transitives independently.
- Fresh Python 3.11 CI install and `pip check`: passed. It resolved certifi
  2026.10.18, urllib3 2.8.0, boto3/botocore 1.43.125, pytest 9.2.0,
  python-hcl2 8.1.2 and langgraph 1.2.16 within the existing manifest constraints.
- Fresh-environment offline unit suite: **1,352 passed**, no failures/errors/skips.
- The existing local test venv was updated only for certifi/urllib3 (the old
  vulnerable versions were installed there), passed `pip check`, and passed
  **111 CI regressions**, including 26 new Python audit cases. Tornado remains
  absent from CI environments; notebook parent compatibility was resolver-tested.
- Real negative control: a disposable manifest pinned certifi 2026.6.17. Actual
  pip-audit resolved it and exited **1**. Its parsed findings were rejected by
  the actual CI security-report validator. Other scanner reports in that isolated
  validator test were synthetic clean controls, not claimed scanner results.
  No vulnerable package was installed, no repository manifest was modified for
  the control, and no advisory was ignored. Unit regressions also verify scanner
  exit/status propagation, manifest discovery and reclassification rejection.
- Full discovery: **4,281 eligible Python cases**, preserving every prior 4,255
  case and adding only 26 CI regressions. The 74 Step 2 exclusions are unchanged.
  Dashboard source/tests were not changed or rerun.
- Workflow policy, actionlint and `git diff --check`: passed. All five Gitleaks
  exceptions remain exact and unchanged.

**PASS for Python remediation. Remaining Python dependency blockers: none.**
The full newer-Python notebook reference environment was not installed here;
its declared packages were audited and relevant parent compatibility verified.
At the time of this section, npm and Terraform findings were still open.
They were handled in the following section. No Git write
operations, AWS operations, real model calls or production logic changes occurred.

## npm remediation and final local validation, 2026-11-05

npm vulnerabilities were fixed in the dashboard lockfile. Terraform findings
were reviewed and not applied. No commit, push, merge or branch operation
was performed. No AWS, aviation or model API was called.

Commands used the repository `.venv` Python 3.11, Node 22.23.4 (checksum
`cbc74b5e0982c9dbb41525fd8fb5dcd32fbee237863f7829072f4639a1ce5f20`), npm 10.9.9
for `npm ci`, npm 11.6.2 for the CI audit, and Terraform 1.9.8 (checksum
`2667de56106bc6707968286357e29c20f8dbbb2f429ef57099b04994f82d9684`).

```powershell
python scripts/ci_security.py run --scan npm-dependencies
python scripts/ci_security.py run --scan terraform-security
python scripts/ci_security.py run --scan secrets
python scripts/ci_security.py run --scan python-dependencies
python scripts/ci_terraform.py --profile full --plugin-dir envs/dev/.terraform/providers
python scripts/run_offline_tests.py -- -q -ra --tb=line --junitxml=test-results/ci/complete-suite.xml
python scripts/ci.py policy
python scripts/ci.py dashboard --profile full
./test-results/ci-tools/actionlint/actionlint.exe -shellcheck= -pyflakes= .github/workflows/ci.yml
```

Dashboard commands, from `dashboard/` after `npm ci --no-audit --no-fund`, used
`CI=true` and `NODE_OPTIONS=--require=./scripts/offline.cjs` for lint,
typecheck, Vitest and the production build. The guard was not set during
`npm ci`, because it would be inherited by dependency install scripts.

Results:

- npm audit 11.6.2: **PASS, exit 0, zero findings.** Before this change the same command reported 17 affected records. Details are in `SECURITY_FINDINGS.md`.
- Trivy 0.76.1: **exit 1, 99 findings (2 high, 25 medium, 72 low).** Same inventory as the prior scan. No Terraform file or provider lock changed. The CI security-report validator rejects this report, which is the required fail-closed behavior.
- Gitleaks 8.30.1: **PASS, exit 0, zero findings**, with the same five historical fingerprint exceptions.
- pip-audit 2.10.1: **PASS, exit 0, zero findings, all 12 contexts.**
- Python offline suite: **4,281 passed**, no failures, errors or skips, in 349.97 seconds.
- Dashboard: **342 collected and 342 passed.** Coverage 81.80% lines/statements, 72.26% branches, 83.75% functions. ESLint had the existing Fast Refresh warning and no errors. Typecheck and the production build passed. The MapLibre worker was emitted as its own chunk.
- Terraform 1.9.8: formatting passed, both roots initialized with read-only locks, and both validated with zero errors and zero warnings. Provider locks were unchanged.
- Workflow policy and actionlint 1.7.7 passed.

**PARTIAL:** npm, Python, dashboard, Gitleaks, pip-audit, workflow policy and Terraform syntax validation pass. The security gate stays red until an infrastructure owner approves or changes the 99 Trivy findings. No scanner rule was disabled and no ignore comment was added.

## Sprint 3D historical fingerprints, 2026-11-07

GitHub Actions run `42396968336` failed Gitleaks and Trivy. Python, dashboard,
Terraform validation, npm audit, and pip-audit had passed. This follow-up
reproduced the secrets scan and documented the Terraform decision. No commit,
push, merge, checkout, AWS call, or Terraform apply was performed.

The local clone could not see commit `972780efbcc51fb0005767bddbbd819cb4406d56`
until the missing remote tips were fetched into temporary refs.
`sprint3d-five-agent-integration` points at that commit.
`origin/ai-operations-copilot` was stale at `a30cb31`; its remote tip
`c68742c` contains that commit and two later commits from 2026-11-01. GitHub
`fetch-depth: 0` fetches every branch, which is why the hosted scan reported
candidates the earlier local scan did not. The current branch stayed
`ci-step1`. The artifact API returned 401, so the fingerprints below come from
the local Gitleaks 8.30.1 report produced by `scripts/ci_security.py`.

Before adding exceptions, that command exited **1** with exactly two history
findings and no working-tree findings:

- `972780efbcc51fb0005767bddbbd819cb4406d56:tests/contracts/test_sprint3d_auth_grants.py:generic-api-key:426`
- `972780efbcc51fb0005767bddbbd819cb4406d56:tests/contracts/test_sprint3d_offline_validation.py:generic-api-key:1493`

Both are synthetic idempotency fixtures, described as G11 and G12 in
`SECURITY_FINDINGS.md`. The original five fingerprints were not changed.

```powershell
python scripts/ci_security.py run --scan secrets
python scripts/run_offline_tests.py tests/unit/ci/test_gitleaks_exceptions.py -q -ra --tb=short --junitxml=test-results/security-tools/gitleaks-sprint3d-regressions.xml
python test-results/security-tools/_canary_gitleaks.py
python scripts/ci_security.py run --scan secrets
python scripts/run_offline_tests.py tests/unit/ci -q -ra --tb=line --junitxml=test-results/security-tools/gitleaks-ci-regressions.xml
python scripts/ci.py policy
git diff --check
```

Results, using `.venv\Scripts\python.exe`:

- Pre-exception reproduction: **exit 1, 2 history findings, 0 working-tree findings.**
- Focused exception tests: **25 passed.**
- Negative control: **pass.** Exit 1, one working-tree `generic-api-key` on the temporary canary, zero history findings, matched value absent from `scan.json`, and the canary file removed.
- Post-canary scan with the Sprint 3D history still reachable: **exit 0, zero findings, seven approved history exceptions.**
- CI regressions: **114 passed.** That is the prior 111 plus the two new fingerprint cases and the focused Sprint 3D regression.
- Workflow policy: passed.
- Trivy, npm audit, pip-audit, the 4,281 Python tests, and the dashboard suite were not rerun.

**PARTIAL:** the secrets job is clean for this history, and the Terraform decision is recorded without a suppression. The security gate stays red until an owner approves or changes the 99 Trivy findings. Temporary investigation refs were removed after the clean scan. No production behavior changed.

## Temporary Terraform baseline, 2026-11-07

The 99 Trivy findings are unchanged and are now a temporary development CI
deferral in `security/terraform-security-baseline.json`. This is not risk
acceptance and not production clearance. Trivy still exits 1 and the published
report still lists every finding. The process exit is 0 only when the
independent comparison matches the baseline. `*.json` stays ignored except for
this reviewed file, using the same tracking exception pattern as the other
committed JSON fixtures.

```powershell
python scripts/run_offline_tests.py tests/unit/ci -q -ra --tb=line --junitxml=test-results/security-tools/baseline-ci-regressions.xml
python scripts/ci_security.py run --scan terraform-security
python scripts/ci.py policy
./test-results/ci-tools/actionlint/actionlint.exe -shellcheck= -pyflakes= .github/workflows/ci.yml
python scripts/ci_security.py run --scan secrets
python test-results/security-tools/_canary_gitleaks.py
python scripts/ci_security.py run --scan secrets
git diff --check
```

The secrets commands ran while the remote tips of `sprint3d-five-agent-integration`
(`972780e`) and `ai-operations-copilot` (`c873448`, including a commit from
2026-11-07) were reachable. Those temporary refs were removed afterward and
`origin/ai-operations-copilot` was restored to `a30cb31`. The working branch
stayed `ci-step1`.

Results, using `.venv\Scripts\python.exe`:

- CI regressions: **137 passed**.
- Trivy 0.76.1: raw status `findings`, raw exit 1, **99 findings**, 118 Terraform files scanned. CI policy decision `deferred`, 99 deferred, 0 unexpected, 0 resolved, `risk_acceptance` false. Process exit **0**.
- The aggregate gate test accepted that exact report and rejected one added finding.
- A same-count substitution, a severity increase, a duplicate occurrence, a missing or malformed baseline, a missing report, a scanner error, missing coverage, and a tampered clean status all failed in unit tests.
- Workflow policy and actionlint 1.7.7 passed.
- Gitleaks 8.30.1 on the GitHub-visible history: **exit 0, zero findings, seven history exceptions.**
- Canary: exit 1, one working-tree finding, zero history findings, value absent from the report, file removed. The following secrets scan was clean again.
- No Terraform source, application code, AWS call, or full Python/dashboard rerun.

**PASS for this CI deferral.** The 99 findings remain unresolved and must be remediated or formally accepted later. The baseline is not a substitute for that review.
