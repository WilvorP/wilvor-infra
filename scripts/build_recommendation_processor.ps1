$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$FunctionDir = Join-Path $RepoRoot "functions\recommendations\processor"
$DistDir = Join-Path $FunctionDir "dist"
$BuildDir = Join-Path $FunctionDir ".build"
$ZipPath = Join-Path $DistDir "recommendation_processor.zip"

Remove-Item $BuildDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $BuildDir | Out-Null
New-Item -ItemType Directory -Force $DistDir | Out-Null
Copy-Item "$FunctionDir\app.py" "$BuildDir\app.py"
Compress-Archive -Path "$BuildDir\*" -DestinationPath $ZipPath -Force
Remove-Item $BuildDir -Recurse -Force
Write-Host "Built $ZipPath"
