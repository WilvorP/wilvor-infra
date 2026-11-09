# Step 1 CI implementation — local verification

**PASS locally. GitHub-hosted CI has not been run.** All files remain
uncommitted on `ci-step1`; earlier baseline corrections are preserved.

## Results

| Check | Result |
| --- | --- |
| actionlint 1.7.7 | Passed; YAML, jobs, expressions and matrix structure |
| Workflow security policy | Passed; main-only triggers, profile selection, immutable action pins, read-only permissions, mandatory unconditional gate, offline dashboard guard |
| Full discovery | 4,196 eligible Python cases, one owner each |
| Explicit external exclusions | 64 deployed AWS + eight deployed integration + two supplied-plan cases |
| Full Python CI groups | 4,196 passed; no failures/errors/skips; JUnit and branch coverage generated |
| Ordinary combined offline rerun | 4,196 passed in 549.73 seconds; zero failures/errors/skips |
| CI helper regressions | 26 passed, including missing/duplicate/extra cases, failed/cancelled/skipped jobs, missing reports and security mutations |
| PR profile execution | 3,096 passed; no failures/errors/skips; no coverage instrumentation |
| Terraform CI setup | Formatting passed; both roots initialized backend-disabled with read-only locks; both isolated roots validated with zero errors/warnings |
| Provider locks | Original hashes unchanged |
| Node/npm setup | Node 22.14.0 locally; npm ci succeeded; workflow selects maintained Node 22.x |
| Dashboard lint/typecheck/build | Passed; one existing Fast Refresh warning and existing MapLibre bundle-size warning remain non-blocking |
| Dashboard collection/execution | 342 collected and 342 passed across 51 files with V8 coverage |
| Final dashboard guard smoke | App and HTTP suites: 18 passed |
| Dashboard guard negative checks | External domains, numeric-IP/string-port TCP, deceptive loopback names, UDP and independent DNS resolvers blocked; local IPC still works |
| Full aggregate gate | Passed against real local reports: 4,196 unique Python cases plus dashboard reports and both Terraform validation reports |
| PR aggregate gate | Passed against PR reports: 3,096 unique Python cases |
| Aggregate negative probe | A failed upstream job produced exit code 1 as required |
| git diff --check | Passed |

The eligible Python count increased from 4,170 to 4,196 solely because of 26
new CI regression cases. Existing application/AI/infrastructure counts remain
1,241 / 2,809 / 120. Full job ownership is 426 pipelines, 275 read-models,
396 history, 144 script-safety, 2,809 Copilot, 120 infrastructure and 26
governance. The PR profile defers 1,100 Python cases explicitly, not via skips.

An initial covered dashboard run with automatic worker scaling timed out in
four tests (338 passed). CI now caps Vitest at two workers. The complete
covered rerun passed all 342 with the original five-second timeouts, assertions
and no retries. No production frontend behavior was changed to obtain a pass.
The V8 provider is exactly 3.2.7, matching the existing locked Vitest 3.2.7;
locked Vite 6.4.3 and the existing dependency entries were preserved.

Dashboard measured coverage was 85.68% lines/statements, 84.69% branches and
86.94% functions. These are measurements, not imposed thresholds. Combined
Python coverage includes namespace packages and omits generated package/build
trees; import-isolation children are not claimed as instrumented coverage.

Local aggregate exercises supplied explicit job-state maps reflecting the
local commands' observed results and copied only the workflow report allowlist
into artifact-shaped directories. This verifies accounting/aggregation and
failure propagation; it is **not** evidence that GitHub Actions itself passed.

## Commands used

Interpreter paths below abbreviated to `python` refer to `.venv/Scripts/python.exe`.
Terraform 1.9.8 and Node 22.14.0 were portable local tools; setup downloads
required sandbox escalation. esbuild/provider verification also needed access
outside the local sandbox. The offline Python/Node guards remained enabled
during test execution.

```powershell
python scripts/ci_bootstrap.py
./test-results/ci-tools/actionlint/actionlint.exe -shellcheck= -pyflakes= .github/workflows/ci.yml
python scripts/ci.py policy
python scripts/ci.py discover --profile full
python scripts/ci.py discover --profile pr --inventory test-results/ci/pr-inventory.json

# Executed each full group, with JUnit/coverage and exact collection comparison.
python scripts/ci.py run --group pipelines
python scripts/ci.py run --group read-models
python scripts/ci.py run --group history
python scripts/ci.py run --group script-safety
python scripts/ci.py run --group copilot
python scripts/ci.py run --group governance
python scripts/ci_terraform.py --profile full --plugin-dir envs/dev/.terraform/providers
python scripts/ci.py run --group infrastructure

# Independent combined check of all eligible cases through the existing runner.
python scripts/run_offline_tests.py -- -q -ra --tb=short --junitxml=test-results/ci/complete-suite.xml

# PR formatting/static execution: no provider initialization or coverage.
python scripts/ci_terraform.py --profile pr
python scripts/ci.py run --group governance --inventory test-results/ci/pr-inventory.json
python scripts/ci.py run --group script-safety --inventory test-results/ci/pr-inventory.json
python scripts/ci.py run --group infrastructure --inventory test-results/ci/pr-inventory.json
python scripts/ci.py run --group copilot --inventory test-results/ci/pr-inventory.json
```

From `dashboard/`, install dependencies before enabling the offline guard:

```powershell
npm.cmd ci --no-audit --no-fund
$env:CI = 'true'
$env:NODE_OPTIONS = '--require=./scripts/offline.cjs'
npm.cmd run lint
npm.cmd run typecheck
node node_modules/vitest/vitest.mjs list --json=../test-results/ci/dashboard/expected.json --no-cache
npm.cmd test -- --coverage --reporter=default --reporter=junit --reporter=json --outputFile.junit=../test-results/ci/dashboard/junit.xml --outputFile.json=../test-results/ci/dashboard/vitest.json
npm.cmd run build
npm.cmd test -- src/App.test.tsx src/api/http.test.ts
```

From the repository root, the reporting/accounting checks were:

```powershell
python scripts/ci.py dashboard --profile full
python scripts/ci.py gate --profile full
python scripts/ci.py gate --profile pr --inventory test-results/ci/pr-inventory.json --artifacts test-results/ci/pr-downloads
git diff --check
```

The gate commands require `NEEDS_JSON`; on GitHub it comes directly from
`toJSON(needs)`. Full and PR local gate records are saved under ignored
`test-results/ci/full-gate.json` and `test-results/ci/pr-gate.json`.

## Files introduced or changed for CI

| Files | Purpose |
| --- | --- |
| `.github/workflows/ci.yml` | Single workflow, generated Python matrix, Windows Terraform/dashboard jobs and aggregate gate |
| `scripts/ci.py` | Collection orchestration, exact execution accounting, workflow policy, report sanitization, summaries and aggregate coverage/gate |
| `scripts/ci_groups.py` | Central ownership/profile/exclusion rules; no permanent count ceiling |
| `scripts/ci_bootstrap.py` | Setup-only pinned/checksummed actionlint installation |
| `scripts/ci_terraform.py` | Formatting, protected initialization and isolated both-root validation |
| `scripts/run_offline_tests.py` | Permit plan-case collection only; retain execution exclusion and inherited offline safeguards |
| `tests/ci_plugin.py` | Explicit pytest inventory, node-ID verification and outcome reporting |
| `tests/unit/ci/test_ci.py` | 26 CI/security/accounting regressions |
| `tests/requirements-ci.txt` | CI-only YAML validation dependency and established test manifests |
| `tests/requirements-test.txt` | Compatible coverage minimum for namespace reporting/aggregation |
| `.coveragerc` | Relative paths, branch coverage, namespace packages and generated-tree omissions; no threshold |
| `dashboard/scripts/offline.cjs` | External TCP/UDP/DNS guard with local IPC support |
| `dashboard/package.json`, `dashboard/package-lock.json` | Matched Vitest coverage provider; correct locked dependency update |
| `dashboard/vite.config.ts` | Coverage output and CI worker budget; no retries or timeout relaxation |
| `CI.md`, `CI_VALIDATION.md` | Architecture, profiles, local use, reports, exclusions, Step 2 and verification evidence |

No production aviation logic, AI safety/authorization/evidence rules, provider
lockfiles, Terraform root semantics or existing safety test selection were
changed. No credentials, cloud mutation, real model/aviation API calls,
deployment, commit, push, merge or PR creation occurred. No blocking local
validation issues remain. The first GitHub-hosted run is still pending manual
review and the user's Git operations.
