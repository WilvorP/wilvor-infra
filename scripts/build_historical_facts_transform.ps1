$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$FunctionDir = Join-Path $RepoRoot "functions\historical_facts\transform"
$SharedHistoricalDir = Join-Path $RepoRoot "functions\shared\wilvor_historical"
$DistDir = Join-Path $FunctionDir "dist"
$ZipPath = Join-Path $DistDir "historical_facts_transform.zip"

$TempRoot = Join-Path $env:TEMP ("wilvor-historical-facts-transform-" + [guid]::NewGuid().ToString())
$PackageDir = Join-Path $TempRoot "package"

if (-not (Test-Path $FunctionDir)) {
    throw "Historical facts transform directory not found: $FunctionDir"
}

if (-not (Test-Path $SharedHistoricalDir)) {
    throw "Shared historical package not found: $SharedHistoricalDir"
}

try {
    Remove-Item -Recurse -Force $DistDir -ErrorAction SilentlyContinue

    New-Item -ItemType Directory -Force $DistDir | Out-Null
    New-Item -ItemType Directory -Force $PackageDir | Out-Null

    Copy-Item `
        (Join-Path $FunctionDir "app.py") `
        (Join-Path $PackageDir "app.py") `
        -Force

    $SharedTargetDir = Join-Path $PackageDir "wilvor_historical"

    New-Item `
        -ItemType Directory `
        -Force `
        $SharedTargetDir | Out-Null

    Copy-Item `
        -Path "$SharedHistoricalDir\*" `
        -Destination $SharedTargetDir `
        -Recurse `
        -Force

    Compress-Archive `
        -Path (Join-Path $PackageDir "*") `
        -DestinationPath $ZipPath `
        -Force

    Write-Host "Built historical facts transform Lambda package:"
    Write-Host $ZipPath
}
finally {
    Remove-Item -Recurse -Force $TempRoot -ErrorAction SilentlyContinue
}
