[CmdletBinding()]
param(
    [string]$RepositoryRoot = "."
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path $RepositoryRoot).Path
$testsRoot = Join-Path $repoRoot "tests"

$directories = @(
    "unit\aircraft",
    "unit\sigmet",
    "unit\metar",
    "unit\taf",
    "unit\shared",

    "integration\aircraft",
    "integration\sigmet",
    "integration\metar",
    "integration\taf",
    "integration\combined",

    "infrastructure\terraform",
    "infrastructure\aws",

    "smoke\live",

    "fixtures\aircraft\valid",
    "fixtures\aircraft\invalid",
    "fixtures\aircraft\expected",

    "fixtures\sigmet\valid",
    "fixtures\sigmet\invalid",
    "fixtures\sigmet\expected",

    "fixtures\metar\valid",
    "fixtures\metar\invalid",
    "fixtures\metar\expected",

    "fixtures\taf\valid",
    "fixtures\taf\invalid",
    "fixtures\taf\expected",

    "fixtures\shared",

    "contracts",
    "helpers",
    "replay"
)

Write-Host ""
Write-Host "Creating Wilvor test structure under:"
Write-Host $testsRoot
Write-Host ""

New-Item `
    -ItemType Directory `
    -Force `
    -Path $testsRoot |
    Out-Null

foreach ($relativeDirectory in $directories) {
    $fullPath = Join-Path $testsRoot $relativeDirectory

    New-Item `
        -ItemType Directory `
        -Force `
        -Path $fullPath |
        Out-Null

    # Git does not track empty directories, so keep a placeholder until tests
    # are added. Existing files are never removed or overwritten.
    $gitkeepPath = Join-Path $fullPath ".gitkeep"

    if (-not (Test-Path -LiteralPath $gitkeepPath)) {
        New-Item `
            -ItemType File `
            -Path $gitkeepPath |
            Out-Null
    }

    Write-Host "Created: tests\$relativeDirectory"
}

$conftestPath = Join-Path $testsRoot "conftest.py"

if (-not (Test-Path -LiteralPath $conftestPath)) {
    @'
"""Shared pytest fixtures for the Wilvor test suite.

Fixtures will be added as the unit, infrastructure, integration, replay,
and live smoke-test implementations are introduced.
"""
'@ | Set-Content `
        -LiteralPath $conftestPath `
        -Encoding utf8

    Write-Host "Created: tests\conftest.py"
}
else {
    Write-Host "Preserved existing: tests\conftest.py"
}

Write-Host ""
Write-Host "Test folder structure is ready."
Write-Host "Existing test files were preserved."