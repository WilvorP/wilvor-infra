# Onboarding

**Canonical page:** this page for developer environment setup. Lifecycle apply/destroy: [`lifecycle.md`](lifecycle.md). Script flags: [`../../scripts/README.md`](../../scripts/README.md).
**Verified against:** `8abd2d5` on `docs/ws1-platform-foundations`
**Maturity:** CODE, TF, LIVE
**Production authorization:** no grant found. Lifecycle scripts target the development Terraform roots only.

## Purpose

This page lists the local tools, credentials layout, and first commands a developer uses. It does not invent a CI pipeline or a production bootstrap. It does not run Terraform apply.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `scripts/*.ps1`, `dashboard/package.json`, `tests/requirements-test.txt` |
| Terraform | TF | `envs/dev/terraform.tfvars`, `envs/dev/providers.tf` |
| Unit tests | directories present; assertions not sampled on this page | `tests/unit/` |
| Contract tests | directories present; assertions not sampled on this page | `tests/contracts/` |
| Infrastructure tests | runner present; assertions not sampled on this page | `scripts/run-infrastructure-tests.ps1` |
| Integration tests | runner present; assertions not sampled on this page | `scripts/run-integration-tests.ps1` |
| Live harness | LIVE | `scripts/validate_*.py` exist; LIVE is not a pass result |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | `scripts/README.md` safety rules; no prod env root |

## Required local tools

`scripts/README.md` lists:

- PowerShell
- AWS CLI
- Terraform
- Git
- The project Python virtual environment
- AWS CLI profile `wilvor-dev`
- Access to the Wilvor AWS development account

`envs/dev/terraform.tfvars` sets `aws_region = "us-west-1"`, `aws_profile = "wilvor-dev"`, `project_name = "wilvor"`, `environment = "dev"`.

Terraform version constraint: `>= 1.6.0` (source: `envs/dev/providers.tf`). Dashboard Node engines: `^18.0.0 || ^20.0.0 || >=22.0.0` (source: `dashboard/package.json`).

## Python environment

From the repository root, as documented in `scripts/README.md`:

```powershell
.\.venv\Scripts\Activate
```

Install test dependencies from the path that exists:

```powershell
python -m pip install -r tests/requirements-test.txt
```

Infrastructure tests: `tests/requirements-infrastructure.txt` (includes `-r requirements-test.txt`). Live Anthropic harness pin: `tests/requirements-live-validation.txt` (`anthropic==1.8.0`).

Root `requirements.txt` is a local pin set (boto3, h3, shapely, pandas, Jupyter). It is not the Lambda package manifest.

`scripts/run-unit-tests.ps1` still mentions `requirements-test.txt` at the repository root. At `8abd2d5` that file is `tests/requirements-test.txt`.

If Windows blocks unsigned scripts, `scripts/README.md` documents `Unblock-File` on `scripts/dev-*.ps1` and a process-scoped `Set-ExecutionPolicy Bypass`.

## AWS session

Default profile and region match `envs/dev/terraform.tfvars` and the lifecycle script parameters (`-AwsProfile wilvor-dev`, `-AwsRegion us-west-1` in `scripts/dev-up.ps1`).

```powershell
aws sso login --profile wilvor-dev
aws sts get-caller-identity --profile wilvor-dev --region us-west-1
```

Do not commit `.secrets/credentials.json`. `dev-up.ps1` default `-SecretFile` is `.secrets/credentials.json` for OpenSky restoration into Secrets Manager. `.gitignore` includes `.secrets/`.

## Dashboard

Canonical steps: [`dashboard/README.md`](../../dashboard/README.md).

```bash
cd dashboard
npm install
cp .env.example .env
npm run dev
```

The dev server uses port 5173 with `strictPort: true` (source: `dashboard/vite.config.ts`). API CORS allowlist contains `http://localhost:3000` and `http://localhost:5173` (source: `envs/dev/main.tf`, `module.operational_api.cors_allowed_origins`). Set `VITE_WILVOR_API_BASE_URL` to a reachable Operational API base URL. Whether that URL exists in an account is UNKNOWN.

```bash
npm run test      # vitest run
npm run verify    # lint, typecheck, test, build
```

## Tests without AWS mutation

Offline:

```powershell
python -m pytest tests/unit -q -p no:cacheprovider
python -m pytest tests/contracts -q -p no:cacheprovider
.\scripts\run-unit-tests.ps1 -Target aircraft
.\scripts\run-infrastructure-tests.ps1 -Target terraform
```

Contract tests for AI Phase 0 are documented in [`../../tests/README.md`](../../tests/README.md) as runnable without AWS credentials, network, or an AI model.

AWS-reading infrastructure tests and deployed integration tests require a `dev` environment and are described in [`lifecycle.md`](lifecycle.md). This page does not run them.

## Documentation

Standards: [`../DOCUMENTATION_STANDARDS.md`](../DOCUMENTATION_STANDARDS.md). Index: [`../README.md`](../README.md). Cursor rule: `.cursor/rules/wilvor-documentation.mdc` (attaches to Markdown globs).

## Known limitations

- This page does not create a venv, install packages, or apply Terraform.
- No staging or production Terraform root exists.
- `scripts/run-unit-tests.ps1` default `-Target` is `aircraft`. Other unit directories exist under `tests/unit/` and can be passed as `-Target` when the directory exists, or run with pytest directly.
- Operator live harnesses require explicit opt-in environment variables. Documentation work does not run them against external APIs.

## Historical record

No superseded statement is stored on this page.

## Sources

- Source: `scripts/README.md`
- Source: `scripts/dev-up.ps1`
- Source: `scripts/run-unit-tests.ps1`
- Source: `envs/dev/terraform.tfvars`
- Source: `envs/dev/providers.tf`
- Source: `dashboard/package.json`
- Source: `dashboard/vite.config.ts`
- Source: `dashboard/README.md`
- Source: `tests/requirements-test.txt`
- Source: `.gitignore`
