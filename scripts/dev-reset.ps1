[CmdletBinding()]
param(
    [string]$AwsProfile = "wilvor-dev",
    [string]$AwsRegion = "us-west-1",
    [string]$TerraformDirectory = "envs/dev",
    [string]$SecretFile = ".secrets/credentials.json",
    [switch]$SkipBuild,
    [switch]$SkipSecretRestore,
    [switch]$SkipLogin,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $Force) {
    throw "Reset was not started. Run this script with -Force because it destroys the current dev environment."
}

$downParameters = @{
    AwsProfile = $AwsProfile
    AwsRegion = $AwsRegion
    TerraformDirectory = $TerraformDirectory
    Force = $true
}

$upParameters = @{
    AwsProfile = $AwsProfile
    AwsRegion = $AwsRegion
    TerraformDirectory = $TerraformDirectory
    SecretFile = $SecretFile
}

if ($SkipLogin) {
    $downParameters["SkipLogin"] = $true
    $upParameters["SkipLogin"] = $true
}

if ($SkipBuild) {
    $upParameters["SkipBuild"] = $true
}

if ($SkipSecretRestore) {
    $upParameters["SkipSecretRestore"] = $true
}

& (Join-Path $PSScriptRoot "dev-down.ps1") @downParameters
& (Join-Path $PSScriptRoot "dev-up.ps1") @upParameters
