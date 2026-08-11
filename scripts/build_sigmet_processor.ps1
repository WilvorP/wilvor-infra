$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$FunctionDir = Join-Path $RepoRoot "functions\weather\sigmet\processor"
$DistDir = Join-Path $FunctionDir "dist"
$ZipPath = Join-Path $DistDir "sigmet_processor.zip"

$TempRoot = Join-Path $env:TEMP ("wilvor-sigmet-processor-" + [guid]::NewGuid().ToString())
$PackageDir = Join-Path $TempRoot "package"

if (-not (Test-Path $FunctionDir)) {
    throw "SIGMET processor directory not found: $FunctionDir"
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
                -r requirements.txt `
                -t $PackageDir

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