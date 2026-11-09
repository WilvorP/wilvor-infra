# Terraform offline baseline follow-up

This follows `OFFLINE_BASELINE.md` and preserves all earlier baseline fixes.

## Root cause and correction

`envs/dev/main.tf:496–504` contains parenthesized attribute traversals split
before the dot. Terraform 1.9.8 accepts the original source: `fmt -check`
passes and, after backend-disabled initialization, `validate -json` reports
zero errors and warnings. This is valid Terraform syntax, not a configuration
error. `envs/dev/main.tf` was not modified.

python-hcl2 8.1.2's postlexer supports newlines before continued operators,
but does not handle a newline before a traversal dot. The test-only
`tests/helpers/hcl_compat.py` retains the upstream grammar and transformation,
removing newline/comment tokens only before a dot inside parentheses. It does
not rewrite strings, heredocs, resources, expressions or production source.
The dependency is pinned to 8.1.2 because the adapter uses its postlexer API.
Two regression cases verify literal preservation and rejection of malformed
or unparenthesized continued traversals. Terraform remains the authoritative
syntax validator; the static parser does not replace it.

Once parsing worked, the root contract exposed an obsolete five-module
inventory. It now explicitly expects all 17 modules on the authoritative
branch, retaining exact set equality and source-path assertions.

## CLI and initialization

Terraform was still absent from PATH. A portable official Terraform 1.9.8
Windows AMD64 archive was downloaded into ignored
`test-results/offline-tools/terraform-1.9.8` and verified against the official
SHA256SUMS file:

```text
2667de56106bc6707968286357e29c20f8dbbb2f429ef57099b04994f82d9684
```

PATH was changed only for the verification processes. A local, inert
`validation.tfrc` avoids reading developer-specific CLI credentials/config.
`CHECKPOINT_DISABLE=1` disables Terraform's version-check network request;
the offline test runner now sets it as well.

Both actual roots were initialized with backends disabled, no prompts and
read-only provider locks. The already installed local AWS 5.100.0 provider
was reused; no AWS authentication or AWS service calls were needed.

```powershell
$env:PATH = (Join-Path (Get-Location) 'test-results/offline-tools/terraform-1.9.8') + ';' + $env:PATH
$env:TF_CLI_CONFIG_FILE = Join-Path (Get-Location) 'test-results/offline-tools/validation.tfrc'
$env:CHECKPOINT_DISABLE = '1'
$plugins = Join-Path (Get-Location) 'envs/dev/.terraform/providers'
terraform -chdir=envs/dev init -backend=false -input=false -lockfile=readonly -no-color "-plugin-dir=$plugins"
terraform -chdir=envs/dev-historical-data init -backend=false -input=false -lockfile=readonly -no-color "-plugin-dir=$plugins"
terraform -chdir=envs/dev validate -json
terraform -chdir=envs/dev-historical-data validate -json
terraform fmt -recursive -check -no-color envs
terraform fmt -recursive -check -no-color modules
```

For a fresh checkout without a local provider mirror, omit `-plugin-dir`:
backend-disabled initialization installs the provider selected by the lock.
Provider/tool downloads are setup operations, not offline test execution.

Both lockfiles retained their original SHA-256:

```text
envs/dev/.terraform.lock.hcl
6c4d28a195e36fd3194ec9a9b4c1b7366e42aee81a05aa01e296f34b2cac9792
envs/dev-historical-data/.terraform.lock.hcl
bb45cd135b8b96bf8af81c1a4b3f0a02e0bd441d33c10fb809628a87aaf6014c
```

The original recursive formatting test also traversed ignored Lambda package
directories with inaccessible files. It now checks `envs` and `modules`,
covering all Terraform source without inspecting generated packages/tools.
Formatting identified nine alignment-only lines across three table files.
Only those reviewed lines were corrected; no recursive write-format command
was used and no resource values or Terraform semantics changed.

## Validation-only artifacts

A source-only validation copy without Lambda ZIPs fails for the two literal
aircraft `filebase64sha256` references. Their ZIPs are already tracked, so the
original roots do not rely on ignored local builds. For validation independent
of those binaries, use:

```powershell
.\.venv\Scripts\python.exe -B scripts/prepare_terraform_validation.py
```

It creates a unique ignored workspace, copies only `.tf` source and the two
lockfiles, and creates 24 deterministic validation-only archives at the
referenced relative paths. Each ZIP contains only `VALIDATION_ONLY.txt`, no
Lambda handler or production payload. State, tfvars, credentials and real
packaging directories are not copied or modified. Never deploy this workspace.

Both staged roots validated successfully with zero errors/warnings while
reusing the already initialized local metadata through `TF_DATA_DIR`:

```powershell
$prepared = .\.venv\Scripts\python.exe -B scripts/prepare_terraform_validation.py | ConvertFrom-Json
$env:TF_DATA_DIR = Join-Path (Get-Location) 'envs/dev/.terraform'
terraform "-chdir=$($prepared.workspace)/envs/dev" validate -json
$env:TF_DATA_DIR = Join-Path (Get-Location) 'envs/dev-historical-data/.terraform'
terraform "-chdir=$($prepared.workspace)/envs/dev-historical-data" validate -json
Remove-Item Env:TF_DATA_DIR
```

Alternatively, initialize each staged root with the same backend-disabled,
read-only-lock commands before validation. Preparation itself never invokes
Terraform. Two independently prepared workspaces were compared to check ZIP
byte determinism and absence of state/tfvars.

## Test execution

```powershell
# Parser compatibility + existing root contracts: 14 passed.
.\.venv\Scripts\python.exe -B scripts/run_offline_tests.py tests/infrastructure/terraform/test_hcl_compat.py tests/infrastructure/terraform/test_root_contract.py -q -ra --tb=short --junitxml=test-results/offline-baseline/hcl-fix.xml

# Complete offline infrastructure: 120 passed, no failures/errors/skips.
.\.venv\Scripts\python.exe -B scripts/run_offline_tests.py tests/infrastructure/terraform tests/infrastructure/test_sprint3b_ai_auth_route.py -q -ra --tb=short --junitxml=test-results/offline-baseline/infrastructure-final.xml

# Complete offline Python suite (with the PATH/CLI configuration above).
.\.venv\Scripts\python.exe -B scripts/run_offline_tests.py tests/unit tests/contracts tests/infrastructure/terraform tests/infrastructure/test_sprint3b_ai_auth_route.py -q -ra --tb=short --junitxml=test-results/offline-baseline/full-terraform-fixed.xml
```

The two supplied-plan cases remain explicitly excluded by the runner and
deferred to Step 2. No plan, apply, destroy, AWS authentication, model calls,
GitHub workflow, commit, push, merge or branch change was performed.

The expected eligible inventory is **4,170**: 1,241 unit, 2,809 AI contracts,
and 120 infrastructure cases. The increase from 4,168 is exactly the two
parser regression tests, not a selection change.

## Final verified result

The complete combined run finished in **384.82 seconds** with exit code 0:

| Group | Passed | Failed | Errors | Skipped |
| --- | ---: | ---: | ---: | ---: |
| Python unit tests | 1,241 | 0 | 0 | 0 |
| AI Operations Copilot contracts | 2,809 | 0 | 0 | 0 |
| Offline infrastructure | 120 | 0 | 0 | 0 |
| Total | 4,170 | 0 | 0 | 0 |

JUnit: `test-results/offline-baseline/full-terraform-fixed.xml`.
Captured output: `test-results/offline-baseline/full-terraform-fixed.log`.
Both original roots and both isolated validation copies reported `valid=true`,
zero errors and zero warnings. Both source-tree formatting checks passed.
`git diff --check` passed; both provider locks and `envs/dev/main.tf` remain
unchanged. Earlier fixture, marker, import-isolation and network-guard fixes
are preserved.

**PASS: the verified offline baseline is ready for GitHub Actions
implementation.** There are no remaining blockers for this task. Results use
the existing Windows/Python 3.11.3 environment and Terraform 1.9.8; a fresh
dependency installation or another Python version is not claimed here.

## Files changed in this follow-up

```text
modules/airport_status/tables.tf                  # spacing only
modules/projection/tables.tf                      # spacing only
modules/taf/tables.tf                             # spacing only
scripts/prepare_terraform_validation.py           # isolated artifact preparation
scripts/run_offline_tests.py                      # disable Terraform checkpoint
tests/helpers/hcl_compat.py                       # scoped parser compatibility
tests/infrastructure/terraform/test_hcl_compat.py  # two regressions
tests/infrastructure/terraform/test_root_contract.py
tests/infrastructure/terraform/test_terraform_cli.py
tests/requirements-infrastructure.txt
tests/OFFLINE_BASELINE.md                         # link to this follow-up
tests/TERRAFORM_BASELINE.md
```
