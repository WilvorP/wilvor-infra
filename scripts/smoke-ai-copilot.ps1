param(
    [Parameter(Mandatory = $true)]
    [string]$ApiEndpoint,

    [Parameter(Mandatory = $true)]
    [string]$AircraftId
)

$ErrorActionPreference = "Stop"
$BaseUrl = $ApiEndpoint.TrimEnd("/")

Write-Host "AI Copilot health"
Invoke-RestMethod `
    -Method Get `
    -Uri "$BaseUrl/health"

Write-Host "Network summary"
Invoke-RestMethod `
    -Method Post `
    -Uri "$BaseUrl/ai/summaries/network" `
    -ContentType "application/json" `
    -Body "{}"

$EncodedAircraftId = [uri]::EscapeDataString(
    $AircraftId
)

Write-Host "Aircraft explanation"
Invoke-RestMethod `
    -Method Post `
    -Uri (
        "$BaseUrl/ai/aircraft/" +
        "$EncodedAircraftId/explain"
    ) `
    -ContentType "application/json" `
    -Body "{}"

Write-Host "Copilot chat"
Invoke-RestMethod `
    -Method Post `
    -Uri "$BaseUrl/ai/chat" `
    -ContentType "application/json" `
    -Body (
        @{
            message = (
                "What is happening right now?"
            )
            history = @()
        } |
        ConvertTo-Json -Depth 5
    )
