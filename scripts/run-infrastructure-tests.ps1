[CmdletBinding()]
param(
    [ValidateSet("terraform", "aws", "all")]
    [string]$Target = "all",

    [string]$AwsProfile = "wilvor-dev",
    [string]$AwsRegion = "us-west-1",
    [string]$TerraformDirectory = "envs/dev",
    [string]$PlanFile,

    [switch]$SkipLogin,
    [switch]$AllowDestroyPlan,
    [switch]$NoJUnit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$terraformPath = Join-Path $repoRoot $TerraformDirectory
$resultPath = Join-Path $repoRoot "test-results\infrastructure"

function Assert-Command {
    param([Parameter(Mandatory)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command is not available: $Name"
    }
}

function Test-AwsAuthentication {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    try {
        & aws sts get-caller-identity `
            --profile $AwsProfile `
            --region $AwsRegion `
            --output json *> $null

        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

Assert-Command "python"
Assert-Command "terraform"

if (-not (Test-Path -LiteralPath $terraformPath -PathType Container)) {
    throw "Terraform directory does not exist: $terraformPath"
}

New-Item -ItemType Directory -Force -Path $resultPath | Out-Null

$testRoots = switch ($Target) {
    "terraform" {
        @(Join-Path $repoRoot "tests\infrastructure\terraform")
    }
    "aws" {
        @(Join-Path $repoRoot "tests\infrastructure\aws")
    }
    "all" {
        @(
            (Join-Path $repoRoot "tests\infrastructure\terraform"),
            (Join-Path $repoRoot "tests\infrastructure\aws")
        )
    }
}

$duplicateTestFiles = @(
    foreach ($testRoot in $testRoots) {
        Get-ChildItem `
            -LiteralPath $testRoot `
            -Recurse `
            -File `
            -Filter "*.py" |
        Where-Object { $_.BaseName -match "\(\d+\)$" }
    }
)

if ($duplicateTestFiles.Count -gt 0) {
    Write-Host ""
    Write-Host "Duplicate downloaded test files were found:"

    $duplicateTestFiles | ForEach-Object {
        Write-Host " - $($_.FullName)"
    }

    throw "Remove duplicate '(1).py' files before running tests."
}

& python -c "import pytest, hcl2, boto3" 2>$null

if ($LASTEXITCODE -ne 0) {
    throw (
        "Infrastructure test dependencies are missing. Run: " +
        "python -m pip install -r requirements-infrastructure.txt"
    )
}

if ($Target -in @("terraform", "all")) {
    Write-Host ""
    Write-Host "Initializing Terraform for validation..."

    & terraform `
        "-chdir=$terraformPath" `
        init `
        -input=false

    if ($LASTEXITCODE -ne 0) {
        throw "terraform init failed with exit code $LASTEXITCODE."
    }
}

if ($Target -in @("aws", "all")) {
    Assert-Command "aws"

    if (-not (Test-AwsAuthentication)) {
        if ($SkipLogin) {
            throw (
                "AWS authentication is unavailable and -SkipLogin was used."
            )
        }

        Write-Host ""
        Write-Host "Starting AWS SSO login for profile $AwsProfile..."

        & aws sso login --profile $AwsProfile

        if ($LASTEXITCODE -ne 0) {
            throw "AWS SSO login failed with exit code $LASTEXITCODE."
        }
    }

    if (-not (Test-AwsAuthentication)) {
        throw "AWS authentication failed after login."
    }
}

$env:WILVOR_AWS_PROFILE = $AwsProfile
$env:AWS_PROFILE = $AwsProfile
$env:WILVOR_AWS_REGION = $AwsRegion
$env:AWS_REGION = $AwsRegion
$env:AWS_DEFAULT_REGION = $AwsRegion
$env:WILVOR_TERRAFORM_DIR = $TerraformDirectory

if ($PlanFile) {
    $env:WILVOR_TFPLAN_PATH = $PlanFile
}
else {
    Remove-Item Env:\WILVOR_TFPLAN_PATH -ErrorAction SilentlyContinue
}

if ($AllowDestroyPlan) {
    $env:WILVOR_ALLOW_DESTROY = "true"
}
else {
    Remove-Item Env:\WILVOR_ALLOW_DESTROY -ErrorAction SilentlyContinue
}

$arguments = @(
    "-m",
    "pytest"
)

$arguments += $testRoots
$arguments += @("-v", "-ra")

if (-not $NoJUnit) {
    $junitFile = Join-Path $resultPath "$Target-results.xml"
    $arguments += "--junitxml=$junitFile"
}

Write-Host ""
Write-Host "Running Wilvor infrastructure tests"
Write-Host "Target: $Target"
Write-Host "Terraform directory: $TerraformDirectory"

if ($Target -in @("aws", "all")) {
    Write-Host "AWS profile: $AwsProfile"
    Write-Host "AWS region: $AwsRegion"
}

Write-Host ""

& python @arguments

if ($LASTEXITCODE -ne 0) {
    throw "Infrastructure tests failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "Infrastructure tests passed."
