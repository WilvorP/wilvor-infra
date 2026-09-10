[CmdletBinding()]
param(
    [string]$AwsProfile = "wilvor-dev",
    [string]$AwsRegion = "us-west-1",
    [string]$TerraformDirectory = "envs/dev-historical-data",
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
        & aws sts get-caller-identity --profile $Profile --region $Region --output json 1>$null 2>$null
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    return ($exitCode -eq 0)
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$terraformPath = Join-Path $repoRoot $TerraformDirectory

if ($TerraformDirectory -notmatch "dev-historical-data") {
    throw "historical-data-up.ps1 may only target envs/dev-historical-data."
}

if (-not (Test-Path $terraformPath)) {
    throw "Terraform directory was not found: $terraformPath"
}

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
    Write-Step "2. terraform init"
    & terraform init -input=false
    Assert-LastExitCode "terraform init"

    Write-Step "3. terraform fmt"
    & terraform fmt -check -recursive
    Assert-LastExitCode "terraform fmt"

    Write-Step "4. terraform validate"
    & terraform validate
    Assert-LastExitCode "terraform validate"

    Write-Step "5. terraform plan"
    & terraform plan -input=false -out=tfplan
    Assert-LastExitCode "terraform plan"

    Write-Step "6. terraform apply"
    & terraform apply -input=false tfplan
    Assert-LastExitCode "terraform apply"

    Write-Step "Persistent historical data plane apply completed"
}
finally {
    Pop-Location
}
