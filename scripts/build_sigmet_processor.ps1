$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$FunctionDir = Join-Path $RepoRoot "functions\weather\sigmet\processor"

Push-Location $FunctionDir

try {
    Remove-Item -Recurse -Force package, dist -ErrorAction SilentlyContinue

    New-Item -ItemType Directory -Force package | Out-Null
    New-Item -ItemType Directory -Force dist | Out-Null

    python -m pip install `
        --platform manylinux2014_x86_64 `
        --implementation cp `
        --python-version 3.12 `
        --abi cp312 `
        --only-binary=:all: `
        -r requirements.txt `
        -t package

    Copy-Item app.py package/app.py -Force

    Compress-Archive `
        -Path package/* `
        -DestinationPath dist/sigmet_processor.zip `
        -Force

    Write-Host "Built: $FunctionDir\dist\sigmet_processor.zip"
}
finally {
    Pop-Location
}