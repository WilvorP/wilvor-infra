$ErrorActionPreference = "Stop"

$RepoRoot = (
    Resolve-Path (
        Join-Path $PSScriptRoot ".."
    )
).Path

$FunctionDir = Join-Path `
    $RepoRoot `
    "functions\ai_copilot"

$DistDir = Join-Path `
    $FunctionDir `
    "dist"

$BuildDir = Join-Path `
    $FunctionDir `
    ".build"

$ZipPath = Join-Path `
    $DistDir `
    "ai_copilot.zip"

Remove-Item `
    $BuildDir `
    -Recurse `
    -Force `
    -ErrorAction SilentlyContinue

Remove-Item `
    $ZipPath `
    -Force `
    -ErrorAction SilentlyContinue

New-Item `
    -ItemType Directory `
    -Force `
    $BuildDir |
    Out-Null

New-Item `
    -ItemType Directory `
    -Force `
    $DistDir |
    Out-Null

python -m pip install `
    --platform manylinux2014_x86_64 `
    --implementation cp `
    --python-version 3.12 `
    --only-binary=:all: `
    --upgrade `
    -r (Join-Path $FunctionDir "requirements.txt") `
    -t $BuildDir

if ($LASTEXITCODE -ne 0) {
    throw "Failed to install AI Copilot dependencies."
}

Copy-Item `
    "$FunctionDir\*.py" `
    $BuildDir

$ZipAttempt = 0

while ($true) {
    try {
        $ZipAttempt += 1

        Compress-Archive `
            -Path "$BuildDir\*" `
            -DestinationPath $ZipPath `
            -Force `
            -ErrorAction Stop

        break
    }
    catch {
        Remove-Item `
            $ZipPath `
            -Force `
            -ErrorAction SilentlyContinue

        if ($ZipAttempt -ge 3) {
            throw
        }

        Start-Sleep -Milliseconds 1000
    }
}

Remove-Item `
    $BuildDir `
    -Recurse `
    -Force

Write-Host "Built $ZipPath"
