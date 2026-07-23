# Wilvor Infrastructure Scripts

This folder contains the PowerShell scripts used to build, deploy, reset, and destroy the Wilvor development infrastructure.

The lifecycle scripts are intended for local development and infrastructure testing. They automate the manual Terraform workflow so the development environment can be created and destroyed safely each day to reduce AWS cost.

---

## Prerequisites

Run the scripts from the repository root.

Required tools:

- PowerShell
- AWS CLI
- Terraform
- Git
- Python virtual environment used by the project
- AWS CLI profile named `wilvor-dev`
- Access to the Wilvor AWS development account

Activate the virtual environment first:

```powershell
.\.venv\Scripts\Activate
```

If Windows blocks the scripts because they are not digitally signed:

```powershell
Get-ChildItem .\scripts\dev-*.ps1 | Unblock-File
```

If they are still blocked, allow scripts only for the current PowerShell session:

```powershell
Set-ExecutionPolicy `
    -Scope Process `
    -ExecutionPolicy Bypass `
    -Force
```

This setting expires when the terminal is closed.

---

# Lifecycle scripts

## `dev-up.ps1`

Creates or updates the Wilvor development infrastructure.

It performs the following steps:

1. Verifies AWS authentication.
2. Starts `aws sso login` when the session is expired.
3. Runs every `scripts/build_*.ps1` Lambda packaging script.
4. Runs `terraform init`.
5. Checks Terraform formatting.
6. Runs `terraform validate`.
7. Creates a saved Terraform plan named `tfplan`.
8. Confirms that the plan file exists.
9. Applies the saved plan.
10. Saves Terraform outputs under `test-results/lifecycle/`.
11. Restores the OpenSky credentials in AWS Secrets Manager.

### Standard usage

```powershell
.\scripts\dev-up.ps1
```

### Skip Lambda builds

Use this when the Lambda ZIP packages are already current:

```powershell
.\scripts\dev-up.ps1 -SkipBuild
```

### Skip OpenSky secret restoration

```powershell
.\scripts\dev-up.ps1 -SkipSecretRestore
```

### Use another AWS profile or region

```powershell
.\scripts\dev-up.ps1 `
    -AwsProfile "another-profile" `
    -AwsRegion "us-east-1"
```

### Use another Terraform environment

```powershell
.\scripts\dev-up.ps1 `
    -TerraformDirectory "envs/staging"
```

### Use another local secret file

```powershell
.\scripts\dev-up.ps1 `
    -SecretFile ".secrets/another-credentials.json"
```

### Skip automatic AWS login

This causes the script to fail immediately when the AWS session is unavailable:

```powershell
.\scripts\dev-up.ps1 -SkipLogin
```

### Parameters

| Parameter | Type | Default | Purpose |
|---|---:|---|---|
| `-AwsProfile` | String | `wilvor-dev` | AWS CLI profile |
| `-AwsRegion` | String | `us-west-1` | AWS region |
| `-TerraformDirectory` | String | `envs/dev` | Terraform environment directory |
| `-SecretFile` | String | `.secrets/credentials.json` | Local OpenSky credential file |
| `-SkipBuild` | Switch | Off | Skip all `build_*.ps1` scripts |
| `-SkipSecretRestore` | Switch | Off | Do not upload the OpenSky secret |
| `-SkipLogin` | Switch | Off | Do not start AWS SSO login automatically |

---

## `dev-down.ps1`

Destroys the Wilvor development infrastructure.

The script requires the `-Force` flag to reduce the risk of accidental destruction.

It performs the following steps:

1. Verifies AWS authentication.
2. Captures Terraform outputs.
3. Captures the current Terraform state.
4. Creates a saved destroy plan named `destroy.tfplan`.
5. Confirms that the destroy plan exists.
6. Applies the destroy plan.
7. Confirms that `terraform state list` is empty.
8. Removes temporary Terraform plan files.

### Standard usage

```powershell
.\scripts\dev-down.ps1 -Force
```

### Use another AWS profile or region

```powershell
.\scripts\dev-down.ps1 `
    -Force `
    -AwsProfile "another-profile" `
    -AwsRegion "us-east-1"
```

### Parameters

| Parameter | Type | Default | Purpose |
|---|---:|---|---|
| `-AwsProfile` | String | `wilvor-dev` | AWS CLI profile |
| `-AwsRegion` | String | `us-west-1` | AWS region |
| `-TerraformDirectory` | String | `envs/dev` | Terraform environment directory |
| `-Force` | Switch | Required | Confirms that destruction is intentional |
| `-SkipLogin` | Switch | Off | Do not start AWS SSO login automatically |

### Independent verification

After the script finishes:

```powershell
cd .\envs\dev
terraform state list
cd ..\..
```

A successful destroy should return no resources.

---

## `dev-reset.ps1`

Destroys and recreates the complete development environment.

Internally it runs:

```text
dev-down.ps1
→ verify empty Terraform state
→ dev-up.ps1
→ recreate infrastructure
→ restore secrets
```

The script requires `-Force` because it destroys the current environment.

### Standard usage

```powershell
.\scripts\dev-reset.ps1 -Force
```

### Skip Lambda builds during recreation

```powershell
.\scripts\dev-reset.ps1 `
    -Force `
    -SkipBuild
```

### Skip secret restoration

```powershell
.\scripts\dev-reset.ps1 `
    -Force `
    -SkipSecretRestore
```

### Parameters

| Parameter | Type | Default | Purpose |
|---|---:|---|---|
| `-AwsProfile` | String | `wilvor-dev` | AWS CLI profile |
| `-AwsRegion` | String | `us-west-1` | AWS region |
| `-TerraformDirectory` | String | `envs/dev` | Terraform environment directory |
| `-SecretFile` | String | `.secrets/credentials.json` | Local OpenSky credential file |
| `-SkipBuild` | Switch | Off | Skip all Lambda package builds during recreation |
| `-SkipSecretRestore` | Switch | Off | Do not restore the OpenSky secret |
| `-SkipLogin` | Switch | Off | Do not start AWS SSO login automatically |
| `-Force` | Switch | Required | Confirms that reset is intentional |

---

# Common workflows

## Start the environment for development

```powershell
.\scripts\dev-up.ps1
```

## Start the environment without rebuilding Lambda packages

```powershell
.\scripts\dev-up.ps1 -SkipBuild
```

## Destroy the environment at the end of the day

```powershell
.\scripts\dev-down.ps1 -Force
```

## Completely rebuild the environment

```powershell
.\scripts\dev-reset.ps1 `
    -Force `
    -SkipBuild
```

## Validate the recreated environment

```powershell
cd .\envs\dev

terraform state list
terraform output
terraform plan

cd ..\..
```

A healthy environment should end with:

```text
No changes. Your infrastructure matches the configuration.
```

---

## Poll every pipeline once

```powershell
.\scripts\poll-all-once.ps1

# Evidence and generated files

Lifecycle evidence is saved under:

```text
test-results/lifecycle/
```

Examples:

```text
terraform-outputs-<timestamp>.json
pre-destroy-outputs-<timestamp>.json
pre-destroy-state-<timestamp>.json
```

Temporary Terraform plans are created under the selected Terraform environment:

```text
envs/dev/tfplan
envs/dev/destroy.tfplan
```

The destroy script removes these plan files after a successful teardown.

Do not commit saved plan files because Terraform plans can contain sensitive configuration values.

Recommended `.gitignore` entries:

```gitignore
envs/*/tfplan
envs/*/destroy.tfplan
test-results/
```

---

# Lambda build scripts

`dev-up.ps1` automatically discovers and runs every file matching:

```text
scripts/build_*.ps1
```

This means a new pipeline build script can be added without changing `dev-up.ps1`.

Example:

```text
scripts/build_aircraft_processor.ps1
scripts/build_sigmet_poller.ps1
scripts/build_metar_processor.ps1
scripts/build_taf_processor.ps1
```

The scripts are executed alphabetically.

---

# Safety rules

- Run lifecycle scripts only from the repository root.
- Use `dev-down.ps1 -Force` only for disposable development environments.
- Do not point these scripts at production without reviewing every parameter and Terraform backend.
- Do not manually delete Terraform-managed AWS resources unless recovery requires it.
- Do not commit `.secrets/credentials.json`.
- Do not commit Terraform plan files.
- Review `terraform plan` output whenever infrastructure changes are introduced.
- Preserve `test-results/lifecycle/` when debugging a failed reset or destroy.

---

# Troubleshooting

## Script is not digitally signed

```powershell
Get-ChildItem .\scripts\dev-*.ps1 | Unblock-File
```

## AWS session expired

The scripts normally start SSO login automatically.

You may also log in manually:

```powershell
aws sso login --profile wilvor-dev
```

Verify the session:

```powershell
aws sts get-caller-identity `
    --profile wilvor-dev `
    --region us-west-1
```

## Terraform plan file is missing

The current scripts create the plan using a relative path from the Terraform directory and verify it exists before applying.

Check manually:

```powershell
Test-Path .\envs\dev\tfplan
```

## Lambda package build fails

Run the failing build script directly:

```powershell
.\scripts\build_<component>.ps1
```

Then inspect its output before rerunning:

```powershell
.\scripts\dev-up.ps1
```

## OpenSky secret file is missing

Expected default location:

```text
.secrets/credentials.json
```

Expected structure:

```json
{
  "clientId": "your-client-id",
  "clientSecret": "your-client-secret"
}
```

Use another location with:

```powershell
.\scripts\dev-up.ps1 `
    -SecretFile "C:\secure\credentials.json"
```

## Destroy finishes but state is not empty

Run:

```powershell
cd .\envs\dev
terraform state list
cd ..\..
```

Do not manually remove state entries until the remaining resources and destroy error have been investigated.

---

# Planned testing additions

The lifecycle scripts are Phase 1 of the infrastructure testing framework.

The next phases will add:

1. Unit tests for validators, parsers, normalizers, and handlers.
2. Terraform and AWS infrastructure assertions.
3. Deterministic aircraft, SIGMET, METAR, and TAF integration tests.
4. Live external API smoke tests.
5. A top-level test orchestrator.
6. Machine-readable and Markdown test reports.