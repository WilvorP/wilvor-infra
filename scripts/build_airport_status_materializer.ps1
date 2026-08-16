$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$srcDir = Join-Path $repoRoot "functions\airport_status\materializer"
$distDir = Join-Path $srcDir "dist"
$zipPath = Join-Path $distDir "airport_status_materializer.zip"

if (Test-Path $distDir) {
  Remove-Item -Recurse -Force $distDir
}

New-Item -ItemType Directory -Force $distDir | Out-Null

Compress-Archive `
  -Path (Join-Path $srcDir "app.py") `
  -DestinationPath $zipPath `
  -Force

Write-Host "Built $zipPath"