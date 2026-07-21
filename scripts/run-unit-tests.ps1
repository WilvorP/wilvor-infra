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
$requirementsPath = Join-Path $repoRoot "requirements-test.txt"

if (-not (Test-Path -LiteralPath $testPath -PathType Container)) {
    throw "Unit-test target does not exist: $testPath"
}

New-Item -ItemType Directory -Force -Path $resultPath | Out-Null

# Browser downloads can create files such as test_name (1).py.
# Pytest collects those files too, causing duplicate test execution.
$duplicateTestFiles = @(
    Get-ChildItem `
        -LiteralPath $testPath `
        -Recurse `
        -File `
        -Filter "*.py" |
    Where-Object {
        $_.BaseName -match "\(\d+\)$"
    }
)

if ($duplicateTestFiles.Count -gt 0) {
    Write-Host ""
    Write-Host "Duplicate downloaded test files were found:"

    $duplicateTestFiles | ForEach-Object {
        Write-Host " - $($_.FullName)"
    }

    throw (
        "Remove the duplicate '(1).py' files before running the suite. " +
        "Keep the matching file without '(1)' in its name."
    )
}

& python -m pytest --version *> $null

if ($LASTEXITCODE -ne 0) {
    throw (
        "pytest is not installed in the active Python environment. " +
        "Run: python -m pip install -r requirements-test.txt"
    )
}

# Verify target-specific imports before pytest collection. This produces one
# actionable error rather than dozens of fixture setup errors.
$requiredImports = @{
    "aircraft" = @("boto3")
    "sigmet"   = @("boto3", "h3")
    "metar"    = @("boto3")
    "taf"      = @("boto3")
}

if ($requiredImports.ContainsKey($Target)) {
    foreach ($moduleName in $requiredImports[$Target]) {
        & python -c "import $moduleName" 2>$null

        if ($LASTEXITCODE -ne 0) {
            $installCommand = if (Test-Path -LiteralPath $requirementsPath) {
                "python -m pip install -r requirements-test.txt"
            }
            else {
                "python -m pip install $moduleName"
            }

            throw (
                "Python dependency '$moduleName' is missing for the " +
                "$Target unit tests. Run: $installCommand"
            )
        }
    }
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