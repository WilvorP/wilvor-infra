$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path "$PSScriptRoot\.."

$FunctionDir = Join-Path $RepoRoot "functions\weather\metar\processor"
$DistDir = Join-Path $FunctionDir "dist"
$BuildDir = Join-Path $FunctionDir "build"
$ZipPath = Join-Path $DistDir "metar_processor.zip"

if (Test-Path $BuildDir) {
    Remove-Item $BuildDir -Recurse -Force
}

if (Test-Path $DistDir) {
    Remove-Item $DistDir -Recurse -Force
}

New-Item -ItemType Directory -Force $BuildDir | Out-Null
New-Item -ItemType Directory -Force $DistDir | Out-Null

Copy-Item `
    -Path (Join-Path $FunctionDir "app.py") `
    -Destination (Join-Path $BuildDir "app.py") `
    -Force

Compress-Archive `
    -Path (Join-Path $BuildDir "*") `
    -DestinationPath $ZipPath `
    -Force

Write-Host "Built METAR processor Lambda package:"
Write-Host $ZipPath