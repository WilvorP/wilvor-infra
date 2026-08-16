$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$FunctionDir = Join-Path $RepoRoot "functions\projection\processor"
$DistDir = Join-Path $FunctionDir "dist"
$ZipPath = Join-Path $DistDir "projection_processor.zip"

$TempDir = Join-Path `
    $env:TEMP `
    ("wilvor-projection-" + [guid]::NewGuid().ToString())

try {
    Remove-Item `
        -Recurse `
        -Force `
        $DistDir `
        -ErrorAction SilentlyContinue

    New-Item `
        -ItemType Directory `
        -Force `
        $DistDir | Out-Null

    New-Item `
        -ItemType Directory `
        -Force `
        $TempDir | Out-Null

    Write-Host "Installing Projection Processor dependencies..."

    python -m pip install `
        --platform manylinux2014_x86_64 `
        --implementation cp `
        --python-version 3.12 `
        --only-binary=:all: `
        --upgrade `
        -r (Join-Path $FunctionDir "requirements.txt") `
        -t $TempDir

    if ($LASTEXITCODE -ne 0) {
        throw "pip install failed."
    }

    Copy-Item `
        (Join-Path $FunctionDir "app.py") `
        (Join-Path $TempDir "app.py") `
        -Force

    Write-Host "Creating Lambda zip..."

    Compress-Archive `
        -Path (Join-Path $TempDir "*") `
        -DestinationPath $ZipPath `
        -Force

    Write-Host ""
    Write-Host "Built:"
    Write-Host $ZipPath
}
finally {
    Remove-Item `
        -Recurse `
        -Force `
        $TempDir `
        -ErrorAction SilentlyContinue
}