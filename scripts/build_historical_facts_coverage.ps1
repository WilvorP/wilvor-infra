$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$SharedHistoricalDir = Join-Path $RepoRoot "functions\shared\wilvor_historical"
$RuntimeDir = Join-Path $RepoRoot "functions\historical_facts\runtime"

function Build-HistoricalControlZip {
    param(
        [string]$FunctionDir,
        [string]$ZipName
    )

    $DistDir = Join-Path $FunctionDir "dist"
    $ZipPath = Join-Path $DistDir $ZipName
    $TempRoot = Join-Path $env:TEMP ("wilvor-historical-control-" + [guid]::NewGuid().ToString())
    $PackageDir = Join-Path $TempRoot "package"

    if (-not (Test-Path $FunctionDir)) {
        throw "Historical control directory not found: $FunctionDir"
    }

    try {
        Remove-Item -Recurse -Force $DistDir -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force $DistDir | Out-Null
        New-Item -ItemType Directory -Force $PackageDir | Out-Null

        Copy-Item (Join-Path $FunctionDir "app.py") (Join-Path $PackageDir "app.py") -Force
        Copy-Item (Join-Path $RuntimeDir "gap_writer.py") (Join-Path $PackageDir "gap_writer.py") -Force

        $SharedTargetDir = Join-Path $PackageDir "wilvor_historical"
        New-Item -ItemType Directory -Force $SharedTargetDir | Out-Null
        Copy-Item -Path "$SharedHistoricalDir\*" -Destination $SharedTargetDir -Recurse -Force

        Compress-Archive -Path (Join-Path $PackageDir "*") -DestinationPath $ZipPath -Force
        Write-Host "Built historical control Lambda package:"
        Write-Host $ZipPath
    }
    finally {
        Remove-Item -Recurse -Force $TempRoot -ErrorAction SilentlyContinue
    }
}

Build-HistoricalControlZip `
    -FunctionDir (Join-Path $RepoRoot "functions\historical_facts\coverage_control") `
    -ZipName "historical_facts_coverage.zip"

Build-HistoricalControlZip `
    -FunctionDir (Join-Path $RepoRoot "functions\historical_facts\dlq_gap_consumer") `
    -ZipName "historical_facts_dlq_consumer.zip"
