$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot

$FunctionDir = Join-Path `
  $RepoRoot `
  "functions\runway_metadata\loader"

$SharedPackageDir = Join-Path `
  $RepoRoot `
  "functions\shared\wilvor_weather"

$PackageDir = Join-Path $FunctionDir "package"
$DistDir = Join-Path $FunctionDir "dist"

$OutputZip = Join-Path `
  $DistDir `
  "runway_metadata_loader.zip"

if (-not (Test-Path $FunctionDir)) {
  throw "Runway loader directory does not exist: $FunctionDir"
}

if (-not (Test-Path $SharedPackageDir)) {
  throw "Shared Wilvor package does not exist: $SharedPackageDir"
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

  # Install third-party dependencies when requirements.txt exists.
  # The current loader uses only the Python standard library and boto3,
  # which AWS Lambda already provides.
  if (Test-Path ".\requirements.txt") {
    python -m pip install `
      --upgrade `
      -r ".\requirements.txt" `
      -t $PackageDir
  }

  # All loader modules must be at the ZIP root because the configured
  # Lambda handler will be app.lambda_handler.
  Copy-Item `
    -Path ".\*.py" `
    -Destination $PackageDir `
    -Force

  # Package the shared CloudWatch EMF helper.
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

  Compress-Archive `
    -Path "$PackageDir\*" `
    -DestinationPath $OutputZip `
    -Force

  Write-Host ""
  Write-Host "Runway metadata Lambda package built successfully:"
  Write-Host $OutputZip
}
finally {
  Pop-Location
}