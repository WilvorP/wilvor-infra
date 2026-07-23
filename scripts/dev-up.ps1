[CmdletBinding()]
param(
    [string]$AwsProfile = "wilvor-dev",
    [string]$AwsRegion = "us-west-1",
    [string]$TerraformDirectory = "envs/dev",
    [string]$SecretFile = ".secrets/credentials.json",
    [switch]$SkipBuild,
    [switch]$SkipSecretRestore,
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

function Get-TerraformOutputOrNull {
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

    if ($exitCode -ne 0) {
        return $null
    }

    return [string]$value
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$terraformPath = Join-Path $repoRoot $TerraformDirectory
$resultsPath = Join-Path $repoRoot "test-results\lifecycle"

if (-not (Test-Path $terraformPath)) {
    throw "Terraform directory was not found: $terraformPath"
}

New-Item -ItemType Directory -Force -Path $resultsPath | Out-Null

Assert-Command "aws"
Assert-Command "terraform"
Assert-Command "git"

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

if (-not $SkipBuild) {
    Write-Step "2. Build every Lambda package"

    $buildScripts = @(
        Get-ChildItem `
            -Path (Join-Path $repoRoot "scripts") `
            -Filter "build_*.ps1" `
            -File |
        Sort-Object Name
    )

    if ($buildScripts.Count -eq 0) {
        throw "No scripts/build_*.ps1 files were found."
    }

    foreach ($buildScript in $buildScripts) {
        Write-Host "Running $($buildScript.Name)..."
        & $buildScript.FullName

        if (-not $?) {
            throw "Build script $($buildScript.Name) failed."
        }
    }
}
else {
    Write-Step "2. Lambda package build skipped"
}

Push-Location $terraformPath

try {
    Write-Step "3. Initialize Terraform"
    & terraform init -input=false
    Assert-LastExitCode "terraform init"

    Write-Step "4. Check Terraform formatting"
    & terraform fmt -recursive -check
    Assert-LastExitCode "terraform fmt -check"

    Write-Step "5. Validate Terraform"
    & terraform validate
    Assert-LastExitCode "terraform validate"

    Write-Step "6. Create a saved Terraform plan"

    # Keep the plan filename relative to envs/dev.
    # This avoids Windows/OneDrive path handling problems with long paths and spaces.
    $planFile = "tfplan"

    Remove-Item $planFile -Force -ErrorAction SilentlyContinue

    & terraform plan -input=false "-out=$planFile"
    Assert-LastExitCode "terraform plan"

    if (-not (Test-Path -LiteralPath $planFile -PathType Leaf)) {
        throw "terraform plan reported success, but the saved plan file was not created: $(Join-Path (Get-Location) $planFile)"
    }

    Write-Host "Saved Terraform plan: $(Resolve-Path $planFile)"

    Write-Step "7. Apply the saved Terraform plan"
    & terraform apply -input=false $planFile
    Assert-LastExitCode "terraform apply"

    Write-Step "8. Save Terraform outputs"
    $timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $outputsFile = Join-Path $resultsPath "terraform-outputs-$timestamp.json"

    & terraform output -json |
        Out-File -FilePath $outputsFile -Encoding utf8

    Assert-LastExitCode "terraform output"
    Write-Host "Terraform outputs saved to: $outputsFile"

    if (-not $SkipSecretRestore) {
        Write-Step "9. Restore the OpenSky secret when configured"

        $secretPath = if ([System.IO.Path]::IsPathRooted($SecretFile)) {
            $SecretFile
        }
        else {
            Join-Path $repoRoot $SecretFile
        }

        $secretName = Get-TerraformOutputOrNull `
            -Name "opensky_credentials_secret_name"

        if (-not [string]::IsNullOrWhiteSpace($secretName)) {
            if (-not (Test-Path -LiteralPath $secretPath -PathType Leaf)) {
                throw "Terraform exposes an OpenSky secret, but the local secret file was not found: $secretPath"
            }

            & aws secretsmanager put-secret-value `
                --profile $AwsProfile `
                --region $AwsRegion `
                --secret-id $secretName `
                --secret-string "file://$secretPath" 1>$null

            Assert-LastExitCode "OpenSky secret restoration"
            Write-Host "OpenSky secret restored: $secretName"
        }
        else {
            Write-Host "No opensky_credentials_secret_name output exists. Secret restoration skipped."
        }
    }
    else {
        Write-Step "9. Secret restoration skipped"
    }

    Write-Step "Development infrastructure is ready"
    Write-Host "Terraform directory: $terraformPath"
    Write-Host "AWS profile:         $AwsProfile"
    Write-Host "AWS region:          $AwsRegion"
}
finally {
    Pop-Location
}
