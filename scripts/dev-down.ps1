[CmdletBinding()]
param(
    [string]$AwsProfile = "wilvor-dev",
    [string]$AwsRegion = "us-west-1",
    [string]$TerraformDirectory = "envs/dev",
    [switch]$Force,
    [switch]$SkipLogin
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$env:AWS_PAGER = ""
$env:AWS_PROFILE = $AwsProfile
$env:AWS_REGION = $AwsRegion
$env:AWS_DEFAULT_REGION = $AwsRegion

function Write-Step {
    param([string]$Message)

    Write-Host ""
    Write-Host "============================================================"
    Write-Host $Message
    Write-Host "============================================================"
}

function Assert-Command {
    param([string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found on PATH."
    }
}

function Assert-LastExitCode {
    param([string]$Operation)

    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE."
    }
}

function Test-AwsSession {
    param(
        [string]$Profile,
        [string]$Region
    )

    $previousPreference = $ErrorActionPreference

    try {
        $ErrorActionPreference = "Continue"

        & aws sts get-caller-identity `
            --profile $Profile `
            --region $Region `
            --output json 1>$null 2>$null

        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    return ($exitCode -eq 0)
}

if (-not $Force) {
    throw "Destruction was not started. Run this script with -Force after confirming that the dev environment may be deleted."
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$terraformPath = Join-Path $repoRoot $TerraformDirectory
$resultsPath = Join-Path $repoRoot "test-results\lifecycle"

if (-not (Test-Path $terraformPath)) {
    throw "Terraform directory was not found: $terraformPath"
}

New-Item -ItemType Directory -Force -Path $resultsPath | Out-Null

Assert-Command "aws"
Assert-Command "terraform"

Write-Step "1. Verify AWS authentication"

if (-not (Test-AwsSession -Profile $AwsProfile -Region $AwsRegion)) {
    if ($SkipLogin) {
        throw "AWS authentication is unavailable and -SkipLogin was supplied."
    }

    Write-Host "AWS session is missing or expired. Starting AWS SSO login..."
    & aws sso login --profile $AwsProfile
    Assert-LastExitCode "AWS SSO login"

    if (-not (Test-AwsSession -Profile $AwsProfile -Region $AwsRegion)) {
        throw "AWS SSO login completed, but AWS identity verification still failed."
    }
}

Push-Location $terraformPath

try {
    $timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")

    Write-Step "2. Capture pre-destroy evidence"

    $outputsFile = Join-Path $resultsPath "pre-destroy-outputs-$timestamp.json"
    & terraform output -json |
        Out-File -FilePath $outputsFile -Encoding utf8

    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Terraform outputs could not be captured. Continuing with state capture."
    }

    $stateFile = Join-Path $resultsPath "pre-destroy-state-$timestamp.json"
    & terraform state pull |
        Out-File -FilePath $stateFile -Encoding utf8

    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Terraform state could not be captured. The environment may already be empty."
    }

    Write-Step "3. Create a saved destroy plan"

    # Use a local relative path for the same Windows path-safety reason as dev-up.ps1.
    $destroyPlanFile = "destroy.tfplan"

    Remove-Item $destroyPlanFile -Force -ErrorAction SilentlyContinue

    & terraform plan -destroy -input=false "-out=$destroyPlanFile"
    Assert-LastExitCode "terraform plan -destroy"

    if (-not (Test-Path -LiteralPath $destroyPlanFile -PathType Leaf)) {
        throw "terraform plan -destroy reported success, but the saved destroy plan was not created: $(Join-Path (Get-Location) $destroyPlanFile)"
    }

    Write-Host "Saved destroy plan: $(Resolve-Path $destroyPlanFile)"

    Write-Step "4. Apply the destroy plan"
    & terraform apply -input=false $destroyPlanFile
    Assert-LastExitCode "terraform apply destroy.tfplan"

    Write-Step "5. Verify that Terraform state is empty"
    $remainingResources = @(& terraform state list)
    Assert-LastExitCode "terraform state list"

    $remainingResources = @(
        $remainingResources |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )

    if ($remainingResources.Count -gt 0) {
        Write-Host "Resources still present in Terraform state:"
        $remainingResources | ForEach-Object {
            Write-Host " - $_"
        }

        throw "Terraform destroy completed, but state is not empty."
    }

    Remove-Item $destroyPlanFile -Force -ErrorAction SilentlyContinue
    Remove-Item "tfplan" -Force -ErrorAction SilentlyContinue

    Write-Step "Development infrastructure was destroyed successfully"
    Write-Host "Pre-destroy evidence: $resultsPath"
}
finally {
    Pop-Location
}
