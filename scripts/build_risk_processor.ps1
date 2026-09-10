$ErrorActionPreference = "Stop"

$RepoRoot = (
    Resolve-Path (
        Join-Path $PSScriptRoot ".."
    )
).Path

$FunctionDir = Join-Path `
    $RepoRoot `
    "functions\risk\processor"

$DistDir = Join-Path `
    $FunctionDir `
    "dist"

$ZipPath = Join-Path `
    $DistDir `
    "risk_processor.zip"

$TempDir = Join-Path `
    $FunctionDir `
    ".build"

if (Test-Path $TempDir) {
    Remove-Item `
        $TempDir `
        -Recurse `
        -Force
}

if (Test-Path $ZipPath) {
    Remove-Item `
        $ZipPath `
        -Force
}

New-Item `
    -ItemType Directory `
    -Force `
    $TempDir |
    Out-Null

New-Item `
    -ItemType Directory `
    -Force `
    $DistDir |
    Out-Null

Copy-Item `
    (Join-Path $FunctionDir "app.py") `
    (Join-Path $TempDir "app.py")

$SharedHistoricalDir = Join-Path $RepoRoot "functions\shared\wilvor_historical"
$SharedTargetDir = Join-Path $TempDir "wilvor_historical"
New-Item -ItemType Directory -Force $SharedTargetDir | Out-Null
Copy-Item -Path "$SharedHistoricalDir\*" -Destination $SharedTargetDir -Recurse -Force
Copy-Item `
    (Join-Path $RepoRoot "functions\historical_facts\runtime\gap_writer.py") `
    (Join-Path $TempDir "gap_writer.py") `
    -Force

Compress-Archive `
    -Path (
        Join-Path $TempDir "*"
    ) `
    -DestinationPath $ZipPath `
    -Force

Remove-Item `
    $TempDir `
    -Recurse `
    -Force

Write-Host ""
Write-Host "Built:"
Write-Host $ZipPath