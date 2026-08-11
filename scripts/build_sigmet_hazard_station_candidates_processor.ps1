$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$FunctionDir = Join-Path $RepoRoot "functions\weather\sigmet\hazard_station_candidates_processor"
$DistDir = Join-Path $FunctionDir "dist"
$ZipPath = Join-Path $DistDir "sigmet_hazard_station_candidates_processor.zip"

$TempRoot = Join-Path $env:TEMP ("wilvor-hsc-processor-" + [guid]::NewGuid().ToString())
$PackageDir = Join-Path $TempRoot "package"

if (-not (Test-Path $FunctionDir)) {
    throw "SIGMET HazardStationCandidates processor directory not found: $FunctionDir"
}

try {
    Remove-Item -Recurse -Force $DistDir -ErrorAction SilentlyContinue

    New-Item -ItemType Directory -Force $DistDir | Out-Null
    New-Item -ItemType Directory -Force $PackageDir | Out-Null

    $RequirementsPath = Join-Path $FunctionDir "requirements.txt"

    if (Test-Path $RequirementsPath) {
        python -m pip install `
            --upgrade `
            --platform manylinux2014_x86_64 `
            --implementation cp `
            --python-version 3.12 `
            --only-binary=:all: `
            --target $PackageDir `
            -r $RequirementsPath

        if ($LASTEXITCODE -ne 0) {
            throw "pip install failed for SIGMET HazardStationCandidates processor."
        }
    }

    Copy-Item (Join-Path $FunctionDir "app.py") (Join-Path $PackageDir "app.py") -Force

    Compress-Archive `
        -Path (Join-Path $PackageDir "*") `
        -DestinationPath $ZipPath `
        -Force

    Write-Host "Built: $ZipPath"
}
finally {
    Remove-Item -Recurse -Force $TempRoot -ErrorAction SilentlyContinue
}