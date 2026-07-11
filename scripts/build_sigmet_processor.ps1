$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$FunctionDir = Join-Path $RepoRoot "functions\weather\sigmet\processor"
$SharedWeatherDir = Join-Path $RepoRoot "functions\shared\wilvor_weather"

if (-not (Test-Path $FunctionDir)) {
    throw "SIGMET processor directory not found: $FunctionDir"
}

if (-not (Test-Path $SharedWeatherDir)) {
    throw "Shared weather package not found: $SharedWeatherDir"
}

Push-Location $FunctionDir

try {
    Remove-Item -Recurse -Force package, dist -ErrorAction SilentlyContinue

    New-Item -ItemType Directory -Force package | Out-Null
    New-Item -ItemType Directory -Force dist | Out-Null

    python -m pip install `
        --upgrade `
        --platform manylinux2014_x86_64 `
        --implementation cp `
        --python-version 3.12 `
        --abi cp312 `
        --only-binary=:all: `
        -r requirements.txt `
        -t package

    Copy-Item app.py package\app.py -Force

    $SharedTargetDir = Join-Path $FunctionDir "package\wilvor_weather"
    New-Item -ItemType Directory -Force $SharedTargetDir | Out-Null

    Copy-Item `
        -Path "$SharedWeatherDir\*" `
        -Destination $SharedTargetDir `
        -Recurse `
        -Force

    Compress-Archive `
        -Path package\* `
        -DestinationPath dist\sigmet_processor.zip `
        -Force

    Write-Host "Built: $FunctionDir\dist\sigmet_processor.zip"
}
finally {
    Pop-Location
}