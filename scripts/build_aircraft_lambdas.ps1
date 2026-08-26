$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$SharedAircraftDir = Join-Path $RepoRoot "functions\shared\wilvor_aircraft"

function Build-LambdaPackage {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FunctionRelativePath,

        [Parameter(Mandatory = $true)]
        [string]$ZipName,

        [string]$RequirementsRelativePath
    )

    $FunctionDir = Join-Path $RepoRoot $FunctionRelativePath
    $PackageDir = Join-Path $FunctionDir "package"
    $DistDir = Join-Path $FunctionDir "dist"
    $ZipPath = Join-Path $DistDir $ZipName

    if (-not (Test-Path $FunctionDir)) {
        throw "Function directory not found: $FunctionDir"
    }

    if (-not (Test-Path $SharedAircraftDir)) {
        throw "Shared aircraft package not found: $SharedAircraftDir"
    }

    Remove-Item `
        -Recurse `
        -Force `
        $PackageDir, $DistDir `
        -ErrorAction SilentlyContinue

    New-Item -ItemType Directory -Force $PackageDir | Out-Null
    New-Item -ItemType Directory -Force $DistDir | Out-Null

    if ($RequirementsRelativePath) {
        $RequirementsPath = Join-Path `
            $RepoRoot `
            $RequirementsRelativePath

        if (-not (Test-Path $RequirementsPath)) {
            throw "Requirements file not found: $RequirementsPath"
        }

        python -m pip install `
            --upgrade `
            --platform manylinux2014_x86_64 `
            --implementation cp `
            --python-version 3.12 `
            --abi cp312 `
            --only-binary=:all: `
            -r $RequirementsPath `
            -t $PackageDir

        if ($LASTEXITCODE -ne 0) {
            throw "Dependency installation failed for $FunctionRelativePath"
        }
    }

    Copy-Item `
        -Path (Join-Path $FunctionDir "app.py") `
        -Destination (Join-Path $PackageDir "app.py") `
        -Force

    $SharedTargetDir = Join-Path $PackageDir "wilvor_aircraft"

    New-Item `
        -ItemType Directory `
        -Force `
        $SharedTargetDir |
        Out-Null

    Copy-Item `
        -Path "$SharedAircraftDir\*" `
        -Destination $SharedTargetDir `
        -Recurse `
        -Force

    Compress-Archive `
        -Path "$PackageDir\*" `
        -DestinationPath $ZipPath `
        -Force

    Write-Host "Built: $ZipPath"
}

Build-LambdaPackage `
    -FunctionRelativePath "functions\aircraft_raw_processor" `
    -ZipName "aircraft_raw_processor.zip" `
    -RequirementsRelativePath "functions\aircraft_raw_processor\requirements.txt"

Build-LambdaPackage `
    -FunctionRelativePath "functions\aircraft_current_state_writer" `
    -ZipName "aircraft_current_state_writer.zip"

Build-LambdaPackage `
    -FunctionRelativePath "functions\opensky_poller" `
    -ZipName "opensky_poller.zip"