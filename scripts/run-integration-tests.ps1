[CmdletBinding()]
param(
    [ValidateSet("aircraft", "sigmet", "metar", "taf", "all")]
    [string]$Target = "all",

    [string]$AwsProfile = "wilvor-dev",
    [string]$AwsRegion = "us-west-1",
    [string]$TerraformDirectory = "envs/dev",

    [ValidateRange(30, 600)]
    [int]$TimeoutSeconds = 120,

    [ValidateRange(1, 30)]
    [int]$PollSeconds = 2,

    [switch]$KeepArtifacts,
    [switch]$SkipLogin,
    [switch]$NoJUnit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$terraformPath = Join-Path $repoRoot $TerraformDirectory
$resultPath = Join-Path $repoRoot "test-results\integration"

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
Assert-Command "aws"

if (-not (Test-Path -LiteralPath $terraformPath -PathType Container)) {
    throw "Terraform directory does not exist: $terraformPath"
}

& python -c "import pytest, boto3" 2>$null

if ($LASTEXITCODE -ne 0) {
    throw (
        "Integration test dependencies are missing. Run: " +
        "python -m pip install -r requirements-test.txt"
    )
}

if (-not (Test-AwsAuthentication)) {
    if ($SkipLogin) {
        throw "AWS authentication is unavailable and -SkipLogin was used."
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

$testRoots = switch ($Target) {
    "all" {
        @(Join-Path $repoRoot "tests\integration")
    }
    default {
        @(Join-Path $repoRoot "tests\integration\$Target")
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

New-Item -ItemType Directory -Force -Path $resultPath | Out-Null

$env:WILVOR_AWS_PROFILE = $AwsProfile
$env:AWS_PROFILE = $AwsProfile
$env:WILVOR_AWS_REGION = $AwsRegion
$env:AWS_REGION = $AwsRegion
$env:AWS_DEFAULT_REGION = $AwsRegion
$env:WILVOR_TERRAFORM_DIR = $TerraformDirectory
$env:WILVOR_INTEGRATION_TIMEOUT_SECONDS = "$TimeoutSeconds"
$env:WILVOR_INTEGRATION_POLL_SECONDS = "$PollSeconds"

if ($KeepArtifacts) {
    $env:WILVOR_KEEP_INTEGRATION_ARTIFACTS = "true"
}
else {
    Remove-Item `
        Env:\WILVOR_KEEP_INTEGRATION_ARTIFACTS `
        -ErrorAction SilentlyContinue
}

$arguments = @("-m", "pytest")
$arguments += $testRoots
$arguments += @("-v", "-ra")

if (-not $NoJUnit) {
    $junitFile = Join-Path $resultPath "$Target-results.xml"
    $arguments += "--junitxml=$junitFile"
}

Write-Host ""
Write-Host "Running Wilvor deployed integration tests"
Write-Host "Target: $Target"
Write-Host "AWS profile: $AwsProfile"
Write-Host "AWS region: $AwsRegion"
Write-Host "Timeout: $TimeoutSeconds seconds"
Write-Host "Cleanup enabled: $(-not $KeepArtifacts)"
Write-Host ""

& python @arguments

if ($LASTEXITCODE -ne 0) {
    throw "Integration tests failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "Integration tests passed."
