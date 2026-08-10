$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$FunctionDir = Join-Path $RepoRoot "functions\weather\sigmet\hazard_coordinates_processor"

if (-not (Test-Path $FunctionDir)) {
    throw "SIGMET HazardCoordinates processor directory not found: $FunctionDir"
}

Push-Location $FunctionDir

try {
    Remove-Item -Recurse -Force package, dist -ErrorAction SilentlyContinue

    New-Item -ItemType Directory -Force package | Out-Null
    New-Item -ItemType Directory -Force dist | Out-Null

    if (Test-Path requirements.txt) {
        python -m pip install `
            --upgrade `
            -r requirements.txt `
            -t package

        if ($LASTEXITCODE -ne 0) {
            throw "pip install failed for SIGMET HazardCoordinates processor."
        }
    }

    Copy-Item app.py package\app.py -Force

    Compress-Archive `
        -Path package\* `
        -DestinationPath dist\sigmet_hazard_coordinates_processor.zip `
        -Force

    Write-Host "Built: $FunctionDir\dist\sigmet_hazard_coordinates_processor.zip"
}
finally {
    Pop-Location
}