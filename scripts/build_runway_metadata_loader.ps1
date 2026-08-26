$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$FunctionDir = Join-Path $RepoRoot "functions\runway_metadata\loader"
$DistDir = Join-Path $FunctionDir "dist"
$ZipPath = Join-Path $DistDir "runway_metadata_loader.zip"

$TempRoot = Join-Path $env:TEMP ("wilvor-runway-metadata-loader-" + [guid]::NewGuid().ToString())
$PackageDir = Join-Path $TempRoot "package"

if (-not (Test-Path $FunctionDir)) {
    throw "Runway metadata loader directory not found: $FunctionDir"
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
            throw "pip install failed for runway metadata loader."
        }
    }

    Get-ChildItem -Path $FunctionDir -File -Filter "*.py" | ForEach-Object {
        Copy-Item $_.FullName -Destination $PackageDir -Force
    }

    Compress-Archive `
        -Path (Join-Path $PackageDir "*") `
        -DestinationPath $ZipPath `
        -Force

    Write-Host "Built: $ZipPath"
}
finally {
    Remove-Item -Recurse -Force $TempRoot -ErrorAction SilentlyContinue
}