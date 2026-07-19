[CmdletBinding()]
param(
    [string]$Target = "aircraft",
    [switch]$Coverage,
    [switch]$NoJUnit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$testPath = Join-Path $repoRoot "tests\unit\$Target"
$resultPath = Join-Path $repoRoot "test-results\unit"

if (-not (Test-Path -LiteralPath $testPath -PathType Container)) {
    throw "Unit-test target does not exist: $testPath"
}

New-Item -ItemType Directory -Force -Path $resultPath | Out-Null

& python -m pytest --version *> $null

if ($LASTEXITCODE -ne 0) {
    throw (
        "pytest is not installed in the active Python environment. " +
        "Run: python -m pip install -r requirements-test.txt"
    )
}

$arguments = @(
    "-m",
    "pytest",
    $testPath,
    "-v",
    "-ra"
)

if (-not $NoJUnit) {
    $junitFile = Join-Path $resultPath "$Target-results.xml"
    $arguments += "--junitxml=$junitFile"
}

if ($Coverage) {
    $arguments += "--cov=functions"
    $arguments += "--cov-report=term-missing"
    $arguments += "--cov-report=xml:$resultPath\$Target-coverage.xml"
}

Write-Host ""
Write-Host "Running Wilvor unit tests"
Write-Host "Target: $Target"
Write-Host ""

& python @arguments

if ($LASTEXITCODE -ne 0) {
    throw "Unit tests failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "Unit tests passed."