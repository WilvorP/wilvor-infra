$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$FunctionDir = Join-Path $RepoRoot "functions\weather\sigmet\poller"

Push-Location $FunctionDir

try {
    Remove-Item -Recurse -Force package, dist -ErrorAction SilentlyContinue

    New-Item -ItemType Directory -Force package | Out-Null
    New-Item -ItemType Directory -Force dist | Out-Null

    if (Test-Path requirements.txt) {
        python -m pip install `
            -r requirements.txt `
            -t package
    }

    Copy-Item app.py package/app.py -Force

    Compress-Archive `
        -Path package/* `
        -DestinationPath dist/sigmet_poller.zip `
        -Force

    Write-Host "Built: $FunctionDir\dist\sigmet_poller.zip"
}
finally {
    Pop-Location
}