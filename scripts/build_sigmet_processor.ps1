$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$FunctionDir = Join-Path $RepoRoot "functions\weather\sigmet\processor"
$SharedWeatherDir = Join-Path $RepoRoot "functions\shared\wilvor_weather"
$DistDir = Join-Path $FunctionDir "dist"
$ZipPath = Join-Path $DistDir "sigmet_processor.zip"

$TempRoot = Join-Path $env:TEMP ("wilvor-sigmet-processor-" + [guid]::NewGuid().ToString())
$PackageDir = Join-Path $TempRoot "package"

if (-not (Test-Path $FunctionDir)) {
    throw "SIGMET processor directory not found: $FunctionDir"
}

if (-not (Test-Path $SharedWeatherDir)) {
    throw "Shared weather package not found: $SharedWeatherDir"
}

try {
    Remove-Item -Recurse -Force $DistDir -ErrorAction SilentlyContinue

    New-Item -ItemType Directory -Force $DistDir | Out-Null
    New-Item -ItemType Directory -Force $PackageDir | Out-Null

    $RequirementsPath = Join-Path $FunctionDir "requirements.txt"

    if (Test-Path $RequirementsPath) {
        Push-Location $FunctionDir

        try {
            python -m pip install `
                --upgrade `
                --platform manylinux2014_x86_64 `
                --implementation cp `
                --python-version 3.12 `
                --only-binary=:all: `
                --target $PackageDir `
                -r $RequirementsPath

            if ($LASTEXITCODE -ne 0) {
                throw "pip install failed for SIGMET processor."
            }
        }
        finally {
            Pop-Location
        }
    }

    Get-ChildItem -Path $FunctionDir -File -Filter "*.py" | ForEach-Object {
        Copy-Item $_.FullName -Destination $PackageDir -Force
    }

    $SharedTargetDir = Join-Path $PackageDir "wilvor_weather"

    New-Item `
        -ItemType Directory `
        -Force `
        $SharedTargetDir | Out-Null

    Copy-Item `
        -Path "$SharedWeatherDir\*" `
        -Destination $SharedTargetDir `
        -Recurse `
        -Force

    Compress-Archive `
        -Path (Join-Path $PackageDir "*") `
        -DestinationPath $ZipPath `
        -Force

    Write-Host "Built SIGMET processor Lambda package:"
    Write-Host $ZipPath
}
finally {
    Remove-Item -Recurse -Force $TempRoot -ErrorAction SilentlyContinue
}