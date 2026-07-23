[CmdletBinding()]
param(
    [string]$AwsProfile = "wilvor-dev",
    [string]$AwsRegion = "us-west-1",
    [string]$TerraformDirectory = "envs/dev",
    [string]$AircraftRunner = "functions/opensky_poller/local_runner.py",
    [int]$DownstreamWaitSeconds = 20,
    [switch]$SkipAircraft,
    [switch]$SkipSigmet,
    [switch]$SkipMetar,
    [switch]$SkipTaf,
    [switch]$SkipLogin
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$env:AWS_PAGER = ""
$env:AWS_PROFILE = $AwsProfile
$env:AWS_REGION = $AwsRegion
$env:AWS_DEFAULT_REGION = $AwsRegion

function Write-Step {
    param([string]$Message)

    Write-Host ""
    Write-Host "============================================================"
    Write-Host $Message
    Write-Host "============================================================"
}

function Assert-Command {
    param([string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found on PATH."
    }
}

function Assert-LastExitCode {
    param([string]$Operation)

    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE."
    }
}

function Test-AwsSession {
    param(
        [string]$Profile,
        [string]$Region
    )

    $previousPreference = $ErrorActionPreference

    try {
        $ErrorActionPreference = "Continue"

        & aws sts get-caller-identity `
            --profile $Profile `
            --region $Region `
            --output json 1>$null 2>$null

        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    return ($exitCode -eq 0)
}

function Get-RequiredTerraformOutput {
    param([string]$Name)

    $previousPreference = $ErrorActionPreference

    try {
        $ErrorActionPreference = "Continue"
        $value = & terraform output -raw $Name 2>$null
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    if ($exitCode -ne 0 -or [string]::IsNullOrWhiteSpace([string]$value)) {
        throw "Required Terraform output '$Name' was not found. Confirm that the development infrastructure is deployed and that the output name exists."
    }

    return ([string]$value).Trim()
}

function Invoke-AircraftPollerOnce {
    param(
        [string]$RunnerPath,
        [string]$OutputFile
    )

    if (-not (Test-Path -LiteralPath $RunnerPath -PathType Leaf)) {
        throw "Aircraft local runner was not found: $RunnerPath"
    }

    Write-Host "Running aircraft poller once..."

    $previousPreference = $ErrorActionPreference

    try {
        $ErrorActionPreference = "Continue"

        & python $RunnerPath --once 2>&1 |
            Tee-Object -FilePath $OutputFile

        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    if ($exitCode -ne 0) {
        throw "Aircraft poller failed with exit code $exitCode. See $OutputFile"
    }

    Write-Host "Aircraft poller completed successfully."
}

function Invoke-LambdaPollerOnce {
    param(
        [string]$Component,
        [string]$FunctionName,
        [string]$ResponseFile,
        [string]$MetadataFile
    )

    Write-Host "Invoking $Component poller Lambda: $FunctionName"

    # Run from the result directory and use simple relative output filenames.
    # This avoids Windows path issues with spaces and satisfies the required
    # AWS CLI Lambda invoke outfile argument.
    $responseLeaf = Split-Path -Leaf $ResponseFile
    $metadataLeaf = Split-Path -Leaf $MetadataFile

    $previousPreference = $ErrorActionPreference

    try {
        $ErrorActionPreference = "Continue"

        & aws lambda invoke `
            --function-name $FunctionName `
            --profile $AwsProfile `
            --region $AwsRegion `
            --cli-binary-format raw-in-base64-out `
            --output json `
            $responseLeaf 2>&1 |
            Tee-Object -FilePath $metadataLeaf

        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    if ($exitCode -ne 0) {
        throw "$Component Lambda invocation failed with exit code $exitCode. See $MetadataFile"
    }

    if (-not (Test-Path -LiteralPath $ResponseFile -PathType Leaf)) {
        throw "$Component Lambda invocation did not create its response file: $ResponseFile"
    }

    $metadata = $null

    try {
        $metadata = Get-Content -LiteralPath $MetadataFile -Raw |
            ConvertFrom-Json
    }
    catch {
        throw "$Component Lambda invocation metadata was not valid JSON. See $MetadataFile"
    }

    if ($metadata.PSObject.Properties.Name -contains "FunctionError") {
        if (-not [string]::IsNullOrWhiteSpace([string]$metadata.FunctionError)) {
            throw "$Component Lambda returned FunctionError '$($metadata.FunctionError)'. See $ResponseFile"
        }
    }

    $responseText = Get-Content -LiteralPath $ResponseFile -Raw

    Write-Host ""
    Write-Host "$Component response:"
    Write-Host $responseText

    if (-not [string]::IsNullOrWhiteSpace($responseText)) {
        try {
            $responseJson = $responseText | ConvertFrom-Json

            if ($responseJson.PSObject.Properties.Name -contains "ok") {
                if ($responseJson.ok -eq $false) {
                    throw "$Component poller returned ok=false. See $ResponseFile"
                }
            }
        }
        catch {
            if ($_.Exception.Message -like "*returned ok=false*") {
                throw
            }

            Write-Warning "$Component response was not a JSON object. The invocation itself succeeded; inspect $ResponseFile."
        }
    }

    Write-Host "$Component poller completed successfully."
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$terraformPath = Join-Path $repoRoot $TerraformDirectory
$aircraftRunnerPath = if ([System.IO.Path]::IsPathRooted($AircraftRunner)) {
    $AircraftRunner
}
else {
    Join-Path $repoRoot $AircraftRunner
}

if (-not (Test-Path -LiteralPath $terraformPath -PathType Container)) {
    throw "Terraform directory was not found: $terraformPath"
}

Assert-Command "aws"
Assert-Command "terraform"

if (-not $SkipAircraft) {
    Assert-Command "python"
}

Set-Location $repoRoot

Write-Step "1. Verify AWS authentication"

if (-not (Test-AwsSession -Profile $AwsProfile -Region $AwsRegion)) {
    if ($SkipLogin) {
        throw "AWS authentication is unavailable and -SkipLogin was supplied."
    }

    Write-Host "AWS session is missing or expired. Starting AWS SSO login..."
    & aws sso login --profile $AwsProfile
    Assert-LastExitCode "AWS SSO login"

    if (-not (Test-AwsSession -Profile $AwsProfile -Region $AwsRegion)) {
        throw "AWS SSO login completed, but AWS identity verification still failed."
    }
}

Write-Host "AWS authentication is valid."

Write-Step "2. Read poller names from Terraform"

$sigmetFunction = $null
$metarFunction = $null
$tafFunction = $null

Push-Location $terraformPath

try {
    if (-not $SkipSigmet) {
        $sigmetFunction = Get-RequiredTerraformOutput `
            -Name "sigmet_poller_function_name"

        Write-Host "SIGMET: $sigmetFunction"
    }

    if (-not $SkipMetar) {
        $metarFunction = Get-RequiredTerraformOutput `
            -Name "metar_poller_function_name"

        Write-Host "METAR:  $metarFunction"
    }

    if (-not $SkipTaf) {
        $tafFunction = Get-RequiredTerraformOutput `
            -Name "taf_poller_function_name"

        Write-Host "TAF:    $tafFunction"
    }
}
finally {
    Pop-Location
}

$timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$resultDirectory = Join-Path $repoRoot "test-results\poll-once\$timestamp"

New-Item -ItemType Directory -Force -Path $resultDirectory |
    Out-Null

Write-Host "Results directory: $resultDirectory"

$results = @()

# The script intentionally continues after an individual failure so all
# requested pollers are attempted and a complete summary is produced.

if (-not $SkipAircraft) {
    Write-Step "3. Poll aircraft once"

    $aircraftOutput = Join-Path $resultDirectory "aircraft-response.txt"

    try {
        Invoke-AircraftPollerOnce `
            -RunnerPath $aircraftRunnerPath `
            -OutputFile $aircraftOutput

        $results += [PSCustomObject]@{
            Component = "aircraft"
            Status = "PASS"
            Mode = "local"
            FunctionName = $null
            ResponseFile = $aircraftOutput
            Error = $null
        }
    }
    catch {
        Write-Error $_.Exception.Message

        $results += [PSCustomObject]@{
            Component = "aircraft"
            Status = "FAIL"
            Mode = "local"
            FunctionName = $null
            ResponseFile = $aircraftOutput
            Error = $_.Exception.Message
        }
    }
}

Push-Location $resultDirectory

try {
    if (-not $SkipSigmet) {
        Write-Step "4. Poll SIGMET once"

        $responseFile = Join-Path $resultDirectory "sigmet-response.json"
        $metadataFile = Join-Path $resultDirectory "sigmet-invoke-metadata.json"

        try {
            Invoke-LambdaPollerOnce `
                -Component "SIGMET" `
                -FunctionName $sigmetFunction `
                -ResponseFile $responseFile `
                -MetadataFile $metadataFile

            $results += [PSCustomObject]@{
                Component = "sigmet"
                Status = "PASS"
                Mode = "lambda"
                FunctionName = $sigmetFunction
                ResponseFile = $responseFile
                Error = $null
            }
        }
        catch {
            Write-Error $_.Exception.Message

            $results += [PSCustomObject]@{
                Component = "sigmet"
                Status = "FAIL"
                Mode = "lambda"
                FunctionName = $sigmetFunction
                ResponseFile = $responseFile
                Error = $_.Exception.Message
            }
        }
    }

    if (-not $SkipMetar) {
        Write-Step "5. Poll METAR once"

        $responseFile = Join-Path $resultDirectory "metar-response.json"
        $metadataFile = Join-Path $resultDirectory "metar-invoke-metadata.json"

        try {
            Invoke-LambdaPollerOnce `
                -Component "METAR" `
                -FunctionName $metarFunction `
                -ResponseFile $responseFile `
                -MetadataFile $metadataFile

            $results += [PSCustomObject]@{
                Component = "metar"
                Status = "PASS"
                Mode = "lambda"
                FunctionName = $metarFunction
                ResponseFile = $responseFile
                Error = $null
            }
        }
        catch {
            Write-Error $_.Exception.Message

            $results += [PSCustomObject]@{
                Component = "metar"
                Status = "FAIL"
                Mode = "lambda"
                FunctionName = $metarFunction
                ResponseFile = $responseFile
                Error = $_.Exception.Message
            }
        }
    }

    if (-not $SkipTaf) {
        Write-Step "6. Poll TAF once"

        $responseFile = Join-Path $resultDirectory "taf-response.json"
        $metadataFile = Join-Path $resultDirectory "taf-invoke-metadata.json"

        try {
            Invoke-LambdaPollerOnce `
                -Component "TAF" `
                -FunctionName $tafFunction `
                -ResponseFile $responseFile `
                -MetadataFile $metadataFile

            $results += [PSCustomObject]@{
                Component = "taf"
                Status = "PASS"
                Mode = "lambda"
                FunctionName = $tafFunction
                ResponseFile = $responseFile
                Error = $null
            }
        }
        catch {
            Write-Error $_.Exception.Message

            $results += [PSCustomObject]@{
                Component = "taf"
                Status = "FAIL"
                Mode = "lambda"
                FunctionName = $tafFunction
                ResponseFile = $responseFile
                Error = $_.Exception.Message
            }
        }
    }
}
finally {
    Pop-Location
}

if ($DownstreamWaitSeconds -gt 0) {
    Write-Step "7. Wait for asynchronous downstream processing"
    Write-Host "Waiting $DownstreamWaitSeconds seconds for Kinesis-triggered processors..."
    Start-Sleep -Seconds $DownstreamWaitSeconds
}

Write-Step "8. Poll summary"

$results |
    Format-Table Component, Status, Mode, FunctionName -AutoSize

$summaryFile = Join-Path $resultDirectory "poll-summary.json"

$summary = [PSCustomObject]@{
    StartedAtUtc = $timestamp
    CompletedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    AwsProfile = $AwsProfile
    AwsRegion = $AwsRegion
    DownstreamWaitSeconds = $DownstreamWaitSeconds
    Results = $results
}

$summary |
    ConvertTo-Json -Depth 6 |
    Out-File -LiteralPath $summaryFile -Encoding utf8

Write-Host "Summary saved to: $summaryFile"

$failed = @(
    $results |
    Where-Object { $_.Status -eq "FAIL" }
)

if ($failed.Count -gt 0) {
    throw "$($failed.Count) poller(s) failed. Review the summary and response files in $resultDirectory"
}

Write-Host ""
Write-Host "All requested pollers completed successfully."