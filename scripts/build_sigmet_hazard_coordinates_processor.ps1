$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$FunctionDir = Join-Path $RepoRoot "functions\weather\sigmet\hazard_coordinates_processor"
$DistDir = Join-Path $FunctionDir "dist"
$ZipPath = Join-Path $DistDir "sigmet_hazard_coordinates_processor.zip"

$TempRoot = Join-Path $env:TEMP ("wilvor-hazard-coordinates-processor-" + [guid]::NewGuid().ToString())
$PackageDir = Join-Path $TempRoot "package"

if (-not (Test-Path $FunctionDir)) {
    throw "SIGMET HazardCoordinates processor directory not found: $FunctionDir"
}

try {
    Remove-Item -Recurse -Force $DistDir -ErrorAction SilentlyContinue

    New-Item -ItemType Directory -Force $DistDir | Out-Null
    New-Item -ItemType Directory -Force $PackageDir | Out-Null

    $RequirementsPath = Join-Path $FunctionDir "requirements.txt"

    if (Test-Path $RequirementsPath) {
        python -m pip install `
            --upgrade `
            -r $RequirementsPath `
            -t $PackageDir

        if ($LASTEXITCODE -ne 0) {
            throw "pip install failed for SIGMET HazardCoordinates processor."
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