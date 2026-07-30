$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot

$FunctionDir = Join-Path `
  $RepoRoot `
  "functions\station_reference\loader"

$SharedPackageDir = Join-Path `
  $RepoRoot `
  "functions\shared\wilvor_weather"

$PackageDir = Join-Path $FunctionDir "package"
$DistDir = Join-Path $FunctionDir "dist"

$OutputZip = Join-Path `
  $DistDir `
  "station_reference_loader.zip"

if (-not (Test-Path $FunctionDir)) {
  throw "StationReference loader directory does not exist: $FunctionDir"
}

Push-Location $FunctionDir

try {
  Remove-Item `
    -Path $PackageDir, $DistDir `
    -Recurse `
    -Force `
    -ErrorAction SilentlyContinue

  New-Item `
    -Path $PackageDir `
    -ItemType Directory `
    -Force |
    Out-Null

  New-Item `
    -Path $DistDir `
    -ItemType Directory `
    -Force |
    Out-Null

  if (Test-Path ".\requirements.txt") {
    python -m pip install `
      --upgrade `
      --platform manylinux2014_x86_64 `
      --implementation cp `
      --python-version 3.12 `
      --only-binary=:all: `
      --no-cache-dir `
      -r ".\requirements.txt" `
      -t $PackageDir
  }

  Copy-Item `
    -Path ".\*.py" `
    -Destination $PackageDir `
    -Force

  if (Test-Path $SharedPackageDir) {
    $SharedTarget = Join-Path `
      $PackageDir `
      "wilvor_weather"

    New-Item `
      -Path $SharedTarget `
      -ItemType Directory `
      -Force |
      Out-Null

    Copy-Item `
      -Path "$SharedPackageDir\*.py" `
      -Destination $SharedTarget `
      -Force
  }

  Compress-Archive `
    -Path "$PackageDir\*" `
    -DestinationPath $OutputZip `
    -Force

  Write-Host ""
  Write-Host "StationReference Lambda package built successfully:"
  Write-Host $OutputZip
}
finally {
  Pop-Location
}
