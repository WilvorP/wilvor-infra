[CmdletBinding()]
param(
    [string]$AwsProfile = "wilvor-dev",
    [string]$AwsRegion = "us-west-1",
    [string]$NamePrefix = "wilvor-dev",
    [Parameter(Mandatory = $true)]
    [string]$ValidationDate,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:DryRunActive = [bool]$DryRun
$script:TempPaths = New-Object System.Collections.Generic.List[string]
$script:AwsCallCount = 0

$ApprovedTables = @("encounter", "risk", "hazard_version", "hazard_geometry")
$SchemaVersions = @{
    encounter        = "wilvor.historical.encounter_fact.v1"
    risk             = "wilvor.historical.risk_fact.v1"
    hazard_version   = "wilvor.historical.hazard_version_fact.v1"
    hazard_geometry  = "wilvor.historical.hazard_geometry_fact.v1"
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$validationDir = Join-Path $repoRoot "modules\historical_analytics\validation"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "============================================================"
    Write-Host $Message
    Write-Host "============================================================"
}

function Assert-ValidationDate {
    param([string]$Value)

    if ($Value -notmatch '^[0-9]{4}-[0-9]{2}-[0-9]{2}$') {
        throw "ValidationDate must be YYYY-MM-DD. Received: $Value"
    }

    $parsed = [datetime]::ParseExact(
        $Value,
        "yyyy-MM-dd",
        [System.Globalization.CultureInfo]::InvariantCulture
    )
    return [pscustomobject]@{
        Year  = $parsed.ToString("yyyy")
        Month = $parsed.ToString("MM")
        Day   = $parsed.ToString("dd")
        Text  = $Value
    }
}

function Assert-NamePrefix {
    param([string]$Value)

    if ($Value -notmatch '^[A-Za-z0-9-]+$') {
        throw "NamePrefix must match [A-Za-z0-9-]+. Received: $Value"
    }
}

function Get-TemplateSql {
    param(
        [string]$FileName,
        [hashtable]$Tokens
    )

    $path = Join-Path $validationDir $FileName
    if (-not (Test-Path $path)) {
        throw "Missing fixed SQL template: $path"
    }

    $sql = [System.IO.File]::ReadAllText($path)
    foreach ($key in $Tokens.Keys) {
        $sql = $sql.Replace($key, [string]$Tokens[$key])
    }
    if ($sql -match '__[A-Z0-9_]+__') {
        throw "Unreplaced SQL placeholder remains in $FileName"
    }
    return $sql.Trim()
}

function Register-TempPath {
    param([string]$Path)
    $script:TempPaths.Add($Path) | Out-Null
    return $Path
}

function Invoke-AwsCli {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$AwsArgs)

    if ($script:DryRunActive) {
        throw "DryRun must not call AWS."
    }

    $script:AwsCallCount += 1
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & aws @AwsArgs
        if ($LASTEXITCODE -ne 0) {
            throw "aws $($AwsArgs -join ' ') failed with exit code $LASTEXITCODE."
        }
        return $output
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

function Get-JsonObject {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) {
        throw "AWS CLI returned empty JSON."
    }
    return $Text | ConvertFrom-Json
}

function New-CliInputFile {
    param([object]$Object)

    $path = Join-Path ([System.IO.Path]::GetTempPath()) (
        "wilvor-athena-" + [guid]::NewGuid().ToString("N") + ".json"
    )
    Register-TempPath $path | Out-Null
    $json = $Object | ConvertTo-Json -Depth 8 -Compress
    [System.IO.File]::WriteAllText($path, $json)
    return $path
}

function Get-FileUri {
    param([string]$Path)
    return "file:///" + ($Path -replace '\\', '/')
}

function Invoke-FixedAthenaQuery {
    param(
        [string]$Sql,
        [string]$Database,
        [string]$Workgroup,
        [string]$Label
    )

    $request = [ordered]@{
        QueryString            = $Sql
        QueryExecutionContext  = @{ Database = $Database }
        WorkGroup              = $Workgroup
        ResultReuseConfiguration = @{
            ResultReuseByAgeConfiguration = @{
                Enabled = $false
            }
        }
    }
    $requestPath = New-CliInputFile $request
    $startRaw = Invoke-AwsCli @(
        "athena", "start-query-execution",
        "--cli-input-json", (Get-FileUri $requestPath),
        "--output", "json"
    )
    $queryId = (Get-JsonObject $startRaw).QueryExecutionId
    if ([string]::IsNullOrWhiteSpace($queryId)) {
        throw "$Label StartQueryExecution did not return QueryExecutionId."
    }

    $deadline = [datetime]::UtcNow.AddSeconds(180)
    $execution = $null
    while ([datetime]::UtcNow -lt $deadline) {
        $execRaw = Invoke-AwsCli @(
            "athena", "get-query-execution",
            "--query-execution-id", $queryId,
            "--output", "json"
        )
        $execution = (Get-JsonObject $execRaw).QueryExecution
        $state = [string]$execution.Status.State
        if ($state -in @("SUCCEEDED", "FAILED", "CANCELED")) {
            break
        }
        Start-Sleep -Seconds 2
    }

    if ($null -eq $execution) {
        throw "$Label GetQueryExecution returned no execution."
    }
    $state = [string]$execution.Status.State
    if ($state -ne "SUCCEEDED") {
        $reason = [string]$execution.Status.StateChangeReason
        throw "$Label Athena query $state. QueryExecutionId=$queryId Reason=$reason"
    }

    return $execution
}

function Get-AthenaScalarRows {
    param(
        [string]$QueryExecutionId,
        [string]$Label
    )

    $raw = Invoke-AwsCli @(
        "athena", "get-query-results",
        "--query-execution-id", $QueryExecutionId,
        "--output", "json"
    )
    $result = Get-JsonObject $raw
    $rows = @($result.ResultSet.Rows)
    if ($rows.Count -lt 2) {
        throw "$Label Athena result had no data rows."
    }
    $headers = @($rows[0].Data | ForEach-Object { $_.VarCharValue })
    $values = @($rows[1].Data | ForEach-Object { $_.VarCharValue })
    $map = [ordered]@{}
    for ($i = 0; $i -lt $headers.Count; $i++) {
        $map[$headers[$i]] = $values[$i]
    }
    return [pscustomobject]$map
}

function Assert-ResultLocation {
    param(
        [object]$Execution,
        [string]$ResultsPrefix,
        [string]$CanonicalPrefix,
        [string]$Label
    )

    $location = [string]$Execution.ResultConfiguration.OutputLocation
    if (-not $location.StartsWith($ResultsPrefix)) {
        throw "$Label result location '$location' does not start with $ResultsPrefix"
    }
    if ($location.StartsWith($CanonicalPrefix)) {
        throw "$Label result location '$location' points at the canonical historical bucket."
    }
    Write-Host "$Label result location: $location"
}

function Get-S3ObjectRecords {
    param(
        [string]$Bucket,
        [string]$Prefix
    )

    $records = New-Object System.Collections.Generic.List[object]
    $token = $null
    do {
        $args = @(
            "s3api", "list-objects-v2",
            "--bucket", $Bucket,
            "--prefix", $Prefix,
            "--output", "json",
            "--no-paginate"
        )
        if ($token) {
            $args += @("--continuation-token", $token)
        }
        $page = Get-JsonObject (Invoke-AwsCli $args)
        foreach ($item in @($page.Contents)) {
            if ($null -eq $item) { continue }
            $key = [string]$item.Key
            if ([string]::IsNullOrWhiteSpace($key) -or $key.EndsWith("/")) {
                continue
            }
            $records.Add([pscustomobject]@{
                    Key  = $key
                    Size = [int64]$item.Size
                    ETag = [string]$item.ETag
                }) | Out-Null
        }
        if ($page.IsTruncated -eq $true) {
            $token = [string]$page.NextContinuationToken
        }
        else {
            $token = $null
        }
    } while ($token)

    return @($records | Sort-Object Key)
}

function Get-JsonlLineCount {
    param([string]$GzipPath)

    $stream = $null
    $gzip = $null
    $reader = $null
    try {
        $stream = [System.IO.File]::OpenRead($GzipPath)
        $gzip = New-Object System.IO.Compression.GZipStream(
            $stream,
            [System.IO.Compression.CompressionMode]::Decompress
        )
        $reader = New-Object System.IO.StreamReader($gzip)
        $count = 0
        while ($null -ne ($line = $reader.ReadLine())) {
            if (-not [string]::IsNullOrWhiteSpace($line)) {
                $count += 1
            }
        }
        return $count
    }
    finally {
        if ($reader) { $reader.Dispose() }
        if ($gzip) { $gzip.Dispose() }
        if ($stream) { $stream.Dispose() }
    }
}

function Get-PartitionJsonlCount {
    param(
        [string]$Bucket,
        [string]$Prefix,
        [string]$Dataset
    )

    $objects = Get-S3ObjectRecords -Bucket $Bucket -Prefix $Prefix
    if ($objects.Count -eq 0) {
        throw "Canonical partition $Prefix has no objects."
    }

    $lineCount = 0
    foreach ($object in $objects) {
        if (-not $object.Key.EndsWith(".gz")) {
            throw "Canonical object '$($object.Key)' is not gzip JSONL."
        }
        $local = Register-TempPath (
            Join-Path ([System.IO.Path]::GetTempPath()) (
                "wilvor-s3-" + [guid]::NewGuid().ToString("N") + ".json.gz"
            )
        )
        Invoke-AwsCli @(
            "s3", "cp",
            "s3://$Bucket/$($object.Key)",
            $local,
            "--only-show-errors"
        ) | Out-Null
        $lineCount += Get-JsonlLineCount $local
    }

    Write-Host ("{0}: S3 objects={1} JSONL rows={2}" -f $Dataset, $objects.Count, $lineCount)
    return [pscustomobject]@{
        ObjectCount = $objects.Count
        LineCount   = $lineCount
        Objects     = $objects
    }
}

function Test-MonthHasOtherDays {
    param(
        [string]$Bucket,
        [string]$MonthPrefix,
        [string]$Day
    )

    $objects = Get-S3ObjectRecords -Bucket $Bucket -Prefix $MonthPrefix
    foreach ($object in $objects) {
        if ($object.Key -notmatch "/day=$Day/") {
            return $true
        }
    }
    return $false
}

function Get-SnapshotSignature {
    param([object[]]$Objects)
    $lines = foreach ($object in ($Objects | Sort-Object Key)) {
        "{0}|{1}|{2}" -f $object.Key, $object.Size, $object.ETag
    }
    return ($lines -join "`n")
}

try {
    Assert-NamePrefix $NamePrefix
    $date = Assert-ValidationDate $ValidationDate

    $database = ($NamePrefix -replace '-', '_') + "_historical_facts"
    $workgroup = "$NamePrefix-historical-analytics"
    $accountPlaceholder = "<account_id>"
    $accountId = $accountPlaceholder
    $canonicalBucket = "$NamePrefix-historical-facts-$accountPlaceholder-$AwsRegion"
    $resultsBucket = "$NamePrefix-historical-athena-results-$accountPlaceholder-$AwsRegion"

    $tokensBase = @{
        "__DATABASE__" = $database
        "__YEAR__"     = $date.Year
        "__MONTH__"    = $date.Month
        "__DAY__"      = $date.Day
    }

    Write-Step "Historical analytics validation plan"
    Write-Host "Profile:            $AwsProfile"
    Write-Host "Region:             $AwsRegion"
    Write-Host "NamePrefix:         $NamePrefix"
    Write-Host "ValidationDate:     $($date.Text)"
    Write-Host "Partition:          year=$($date.Year)/month=$($date.Month)/day=$($date.Day)"
    Write-Host "Database:           $database"
    Write-Host "Workgroup:          $workgroup"
    Write-Host "Canonical bucket:   $canonicalBucket"
    Write-Host "Results bucket:     $resultsBucket"
    Write-Host "DryRun:             $DryRun"

    Write-Step "Fixed SQL checks"
    foreach ($dataset in $ApprovedTables) {
        $countSql = Get-TemplateSql "count_partition.sql" (@{
                "__DATABASE__" = $database
                "__TABLE__"    = $dataset
                "__YEAR__"     = $date.Year
                "__MONTH__"    = $date.Month
                "__DAY__"      = $date.Day
            })
        Write-Host ""
        Write-Host "-- $dataset COUNT(*)"
        Write-Host $countSql
    }
    Write-Host ""
    Write-Host "-- schema / identity / geometry / set / empty / pruning templates rendered"
    $previewFiles = @(
        "schema_version_structured.sql",
        "schema_version_geometry.sql",
        "partition_alignment_structured.sql",
        "partition_alignment_geometry.sql",
        "encounter_identity.sql",
        "risk_identity.sql",
        "hazard_identity.sql",
        "hazard_geometry_integrity.sql",
        "hazard_version_minus_geometry.sql",
        "hazard_geometry_minus_version.sql",
        "hazard_version_duplicate_ids.sql",
        "hazard_geometry_duplicate_ids.sql",
        "empty_result.sql",
        "count_month.sql"
    )
    foreach ($file in $previewFiles) {
        $previewTokens = $tokensBase.Clone()
        $previewTokens["__TABLE__"] = "encounter"
        $previewTokens["__SCHEMA_VERSION__"] = $SchemaVersions.encounter
        [void](Get-TemplateSql $file $previewTokens)
        Write-Host "validated template: $file"
    }

    Write-Host ""
    Write-Host "Sequence: preconditions, canonical snapshot, S3 JSONL counts, Athena exact counts, schema, event-date alignment, identities, geometry, hazard sets, SQL_EMPTY, pruning bytes, result location, post snapshot."
    Write-Host "ResultReuseByAgeConfiguration.Enabled = false on every StartQueryExecution."
    Write-Host "Athena COUNT(*)=0 is reported as SQL_EMPTY."
    Write-Host "Infrastructure query returned zero matching rows. No collection-completeness or evaluability conclusion is made."

    if ($DryRun) {
        Write-Step "DRY RUN: no Athena/S3 execution occurred"
        Write-Host "AWS calls: $script:AwsCallCount"
        exit 0
    }

    $env:AWS_PAGER = ""
    $env:AWS_PROFILE = $AwsProfile
    $env:AWS_REGION = $AwsRegion
    $env:AWS_DEFAULT_REGION = $AwsRegion

    if (-not (Get-Command "aws" -ErrorAction SilentlyContinue)) {
        throw "Required command 'aws' was not found on PATH."
    }

    Write-Step "Preconditions"
    $identity = Get-JsonObject (Invoke-AwsCli @(
            "sts", "get-caller-identity",
            "--profile", $AwsProfile,
            "--region", $AwsRegion,
            "--output", "json"
        ))
    $accountId = [string]$identity.Account
    if ([string]::IsNullOrWhiteSpace($accountId)) {
        throw "Unable to resolve AWS account id."
    }
    $canonicalBucket = "$NamePrefix-historical-facts-$accountId-$AwsRegion"
    $resultsBucket = "$NamePrefix-historical-athena-results-$accountId-$AwsRegion"
    $resultsPrefix = "s3://$resultsBucket/athena-results/"
    $canonicalPrefix = "s3://$canonicalBucket/"

    Write-Host "Account:            $accountId"
    Write-Host "Canonical bucket:   $canonicalBucket"
    Write-Host "Results bucket:     $resultsBucket"

    Invoke-AwsCli @("s3api", "head-bucket", "--bucket", $canonicalBucket) | Out-Null
    Invoke-AwsCli @("s3api", "head-bucket", "--bucket", $resultsBucket) | Out-Null
    Invoke-AwsCli @("glue", "get-database", "--name", $database, "--output", "json") | Out-Null
    foreach ($table in $ApprovedTables) {
        Invoke-AwsCli @("glue", "get-table", "--database-name", $database, "--name", $table, "--output", "json") | Out-Null
    }
    $wg = Get-JsonObject (Invoke-AwsCli @(
            "athena", "get-work-group",
            "--work-group", $workgroup,
            "--output", "json"
        ))
    $actualWorkgroup = [string]$wg.WorkGroup.Name
    if ($actualWorkgroup -ne $workgroup) {
        throw "Workgroup name '$actualWorkgroup' does not match $workgroup"
    }

    $tokensBase["__DATABASE__"] = $database

    Write-Step "Canonical object snapshot (before queries)"
    $before = [ordered]@{}
    foreach ($dataset in $ApprovedTables) {
        $prefix = "dataset=$dataset/year=$($date.Year)/month=$($date.Month)/day=$($date.Day)/"
        $before[$dataset] = Get-S3ObjectRecords -Bucket $canonicalBucket -Prefix $prefix
    }

    Write-Step "Canonical S3 JSONL counts"
    $s3Counts = [ordered]@{}
    foreach ($dataset in $ApprovedTables) {
        $prefix = "dataset=$dataset/year=$($date.Year)/month=$($date.Month)/day=$($date.Day)/"
        $s3Counts[$dataset] = Get-PartitionJsonlCount -Bucket $canonicalBucket -Prefix $prefix -Dataset $dataset
    }

    Write-Step "Athena exact COUNT(*) and result location"
    $athenaCounts = [ordered]@{}
    $boundedBytes = $null
    foreach ($dataset in $ApprovedTables) {
        $sql = Get-TemplateSql "count_partition.sql" (@{
                "__DATABASE__" = $database
                "__TABLE__"    = $dataset
                "__YEAR__"     = $date.Year
                "__MONTH__"    = $date.Month
                "__DAY__"      = $date.Day
            })
        $execution = Invoke-FixedAthenaQuery -Sql $sql -Database $database -Workgroup $workgroup -Label "$dataset count"
        Assert-ResultLocation -Execution $execution -ResultsPrefix $resultsPrefix -CanonicalPrefix $canonicalPrefix -Label "$dataset count"
        $row = Get-AthenaScalarRows -QueryExecutionId $execution.QueryExecutionId -Label "$dataset count"
        $athenaCount = [int64]$row.row_count
        $scanned = [int64]$execution.Statistics.DataScannedInBytes
        $athenaCounts[$dataset] = $athenaCount
        Write-Host ("{0}: Athena COUNT(*)={1} DataScannedInBytes={2}" -f $dataset, $athenaCount, $scanned)
        if ($athenaCount -ne $s3Counts[$dataset].LineCount) {
            throw "$dataset S3 JSONL count $($s3Counts[$dataset].LineCount) != Athena COUNT(*) $athenaCount"
        }
        if ($dataset -eq "encounter") {
            $boundedBytes = $scanned
        }
    }

    Write-Step "Schema versions"
    foreach ($dataset in @("encounter", "risk", "hazard_version")) {
        $sql = Get-TemplateSql "schema_version_structured.sql" (@{
                "__DATABASE__"       = $database
                "__TABLE__"          = $dataset
                "__YEAR__"           = $date.Year
                "__MONTH__"          = $date.Month
                "__DAY__"            = $date.Day
                "__SCHEMA_VERSION__" = $SchemaVersions[$dataset]
            })
        $execution = Invoke-FixedAthenaQuery -Sql $sql -Database $database -Workgroup $workgroup -Label "$dataset schema"
        Assert-ResultLocation -Execution $execution -ResultsPrefix $resultsPrefix -CanonicalPrefix $canonicalPrefix -Label "$dataset schema"
        $bad = [int64](Get-AthenaScalarRows -QueryExecutionId $execution.QueryExecutionId -Label "$dataset schema").unexpected_or_null_versions
        Write-Host ("{0}: expected {1}; unexpected/null={2}" -f $dataset, $SchemaVersions[$dataset], $bad)
        if ($bad -ne 0) {
            throw "$dataset has unexpected or NULL fact_schema_version rows."
        }
    }
    $geoSchemaSql = Get-TemplateSql "schema_version_geometry.sql" (@{
            "__DATABASE__"       = $database
            "__YEAR__"           = $date.Year
            "__MONTH__"          = $date.Month
            "__DAY__"            = $date.Day
            "__SCHEMA_VERSION__" = $SchemaVersions.hazard_geometry
        })
    $geoSchemaExec = Invoke-FixedAthenaQuery -Sql $geoSchemaSql -Database $database -Workgroup $workgroup -Label "hazard_geometry schema"
    Assert-ResultLocation -Execution $geoSchemaExec -ResultsPrefix $resultsPrefix -CanonicalPrefix $canonicalPrefix -Label "hazard_geometry schema"
    $geoBad = [int64](Get-AthenaScalarRows -QueryExecutionId $geoSchemaExec.QueryExecutionId -Label "hazard_geometry schema").unexpected_or_null_versions
    Write-Host ("hazard_geometry: expected {0}; unexpected/null={1}" -f $SchemaVersions.hazard_geometry, $geoBad)
    if ($geoBad -ne 0) {
        throw "hazard_geometry has unexpected or NULL fact_schema_version rows."
    }

    Write-Step "Canonical event-date partition alignment"
    foreach ($dataset in @("encounter", "risk", "hazard_version")) {
        $sql = Get-TemplateSql "partition_alignment_structured.sql" (@{
                "__DATABASE__" = $database
                "__TABLE__"    = $dataset
                "__YEAR__"     = $date.Year
                "__MONTH__"    = $date.Month
                "__DAY__"      = $date.Day
            })
        $execution = Invoke-FixedAthenaQuery -Sql $sql -Database $database -Workgroup $workgroup -Label "$dataset alignment"
        Assert-ResultLocation -Execution $execution -ResultsPrefix $resultsPrefix -CanonicalPrefix $canonicalPrefix -Label "$dataset alignment"
        $bad = [int64](Get-AthenaScalarRows -QueryExecutionId $execution.QueryExecutionId -Label "$dataset alignment").misaligned_or_invalid_event_time
        Write-Host "$dataset misaligned or invalid event_time_utc rows: $bad"
        if ($bad -ne 0) {
            throw "$dataset partition/event-date alignment failed."
        }
    }
    $geoAlignSql = Get-TemplateSql "partition_alignment_geometry.sql" $tokensBase
    $geoAlignExec = Invoke-FixedAthenaQuery -Sql $geoAlignSql -Database $database -Workgroup $workgroup -Label "hazard_geometry alignment"
    Assert-ResultLocation -Execution $geoAlignExec -ResultsPrefix $resultsPrefix -CanonicalPrefix $canonicalPrefix -Label "hazard_geometry alignment"
    $geoAlignBad = [int64](Get-AthenaScalarRows -QueryExecutionId $geoAlignExec.QueryExecutionId -Label "hazard_geometry alignment").misaligned_or_invalid_event_time
    Write-Host "hazard_geometry misaligned or invalid event_time_utc rows: $geoAlignBad"
    if ($geoAlignBad -ne 0) {
        throw "hazard_geometry partition/event-date alignment failed."
    }

    Write-Step "Encounter identity (physical rows != unique encounters)"
    $encSql = Get-TemplateSql "encounter_identity.sql" $tokensBase
    $encExec = Invoke-FixedAthenaQuery -Sql $encSql -Database $database -Workgroup $workgroup -Label "encounter identity"
    Assert-ResultLocation -Execution $encExec -ResultsPrefix $resultsPrefix -CanonicalPrefix $canonicalPrefix -Label "encounter identity"
    $enc = Get-AthenaScalarRows -QueryExecutionId $encExec.QueryExecutionId -Label "encounter identity"
    Write-Host ("physical fact rows={0}" -f $enc.physical_rows)
    Write-Host ("unique encounter identities (record_id/encounter_id)={0}/{1}" -f $enc.distinct_record_id, $enc.distinct_encounter_id)
    Write-Host ("unique dedup/event identities={0}" -f $enc.distinct_dedup_id)
    if ([int64]$enc.record_encounter_mismatch -ne 0) {
        throw "encounter record_id != encounter_id for one or more rows."
    }

    Write-Step "Risk identity (risk_id = record_id = dedup_id)"
    $riskSql = Get-TemplateSql "risk_identity.sql" $tokensBase
    $riskExec = Invoke-FixedAthenaQuery -Sql $riskSql -Database $database -Workgroup $workgroup -Label "risk identity"
    Assert-ResultLocation -Execution $riskExec -ResultsPrefix $resultsPrefix -CanonicalPrefix $canonicalPrefix -Label "risk identity"
    $risk = Get-AthenaScalarRows -QueryExecutionId $riskExec.QueryExecutionId -Label "risk identity"
    Write-Host ("physical={0} distinct risk_id/record_id/dedup_id={1}/{2}/{3}" -f $risk.physical_rows, $risk.distinct_risk_id, $risk.distinct_record_id, $risk.distinct_dedup_id)
    if ([int64]$risk.identity_mismatch -ne 0) {
        throw "risk identity mismatch: record_id/dedup_id must equal risk_id."
    }
    if (
        [int64]$risk.distinct_risk_id -ne [int64]$risk.distinct_record_id -or
        [int64]$risk.distinct_risk_id -ne [int64]$risk.distinct_dedup_id
    ) {
        throw "risk distinct identity counts are not equal."
    }

    Write-Step "Hazard version identity (record_id = dedup_id = hazard_version_key)"
    $hvSql = Get-TemplateSql "hazard_identity.sql" $tokensBase
    $hvExec = Invoke-FixedAthenaQuery -Sql $hvSql -Database $database -Workgroup $workgroup -Label "hazard identity"
    Assert-ResultLocation -Execution $hvExec -ResultsPrefix $resultsPrefix -CanonicalPrefix $canonicalPrefix -Label "hazard identity"
    $hv = Get-AthenaScalarRows -QueryExecutionId $hvExec.QueryExecutionId -Label "hazard identity"
    Write-Host ("physical={0} distinct record_id/dedup_id/hazard_version_key={1}/{2}/{3}" -f $hv.physical_rows, $hv.distinct_record_id, $hv.distinct_dedup_id, $hv.distinct_hazard_version_key)
    if ([int64]$hv.identity_mismatch -ne 0) {
        throw "hazard_version identity mismatch."
    }

    Write-Step "Hazard geometry raw-line integrity"
    $geoSql = Get-TemplateSql "hazard_geometry_integrity.sql" $tokensBase
    $geoExec = Invoke-FixedAthenaQuery -Sql $geoSql -Database $database -Workgroup $workgroup -Label "geometry integrity"
    Assert-ResultLocation -Execution $geoExec -ResultsPrefix $resultsPrefix -CanonicalPrefix $canonicalPrefix -Label "geometry integrity"
    $geoInvalid = [int64](Get-AthenaScalarRows -QueryExecutionId $geoExec.QueryExecutionId -Label "geometry integrity").invalid_geometry_rows
    Write-Host "invalid geometry rows: $geoInvalid"
    if ($geoInvalid -ne 0) {
        throw "hazard_geometry JSON/type integrity failed."
    }
    Write-Host "geometry_type POLYGON/MULTIPOLYGON maps to GeoJSON type Polygon/MultiPolygon."

    Write-Step "Hazard version <-> geometry identity sets"
    foreach ($pair in @(
            @{ File = "hazard_version_duplicate_ids.sql"; Field = "duplicate_identity_groups"; Label = "hazard_version duplicates" },
            @{ File = "hazard_geometry_duplicate_ids.sql"; Field = "duplicate_identity_groups"; Label = "hazard_geometry duplicates" },
            @{ File = "hazard_version_minus_geometry.sql"; Field = "version_ids_missing_from_geometry"; Label = "version minus geometry" },
            @{ File = "hazard_geometry_minus_version.sql"; Field = "geometry_ids_missing_from_version"; Label = "geometry minus version" }
        )) {
        $sql = Get-TemplateSql $pair.File $tokensBase
        $execution = Invoke-FixedAthenaQuery -Sql $sql -Database $database -Workgroup $workgroup -Label $pair.Label
        Assert-ResultLocation -Execution $execution -ResultsPrefix $resultsPrefix -CanonicalPrefix $canonicalPrefix -Label $pair.Label
        $value = [int64](Get-AthenaScalarRows -QueryExecutionId $execution.QueryExecutionId -Label $pair.Label).($pair.Field)
        Write-Host ("{0}: {1}" -f $pair.Label, $value)
        if ($value -ne 0) {
            throw "$($pair.Label) failed with count $value"
        }
    }

    Write-Step "Empty SQL result wording"
    $emptySql = Get-TemplateSql "empty_result.sql" $tokensBase
    $emptyExec = Invoke-FixedAthenaQuery -Sql $emptySql -Database $database -Workgroup $workgroup -Label "empty result"
    Assert-ResultLocation -Execution $emptyExec -ResultsPrefix $resultsPrefix -CanonicalPrefix $canonicalPrefix -Label "empty result"
    $emptyCount = [int64](Get-AthenaScalarRows -QueryExecutionId $emptyExec.QueryExecutionId -Label "empty result").row_count
    if ($emptyCount -eq 0) {
        Write-Host "SQL_EMPTY"
    }
    else {
        throw "Fixed empty-result query returned $emptyCount rows; expected SQL_EMPTY."
    }

    Write-Step "Partition pruning (DataScannedInBytes)"
    $monthSql = Get-TemplateSql "count_month.sql" (@{
            "__DATABASE__" = $database
            "__TABLE__"    = "encounter"
            "__YEAR__"     = $date.Year
            "__MONTH__"    = $date.Month
        })
    $monthExec = Invoke-FixedAthenaQuery -Sql $monthSql -Database $database -Workgroup $workgroup -Label "encounter month count"
    Assert-ResultLocation -Execution $monthExec -ResultsPrefix $resultsPrefix -CanonicalPrefix $canonicalPrefix -Label "encounter month count"
    $broaderBytes = [int64]$monthExec.Statistics.DataScannedInBytes
    Write-Host ("bounded day DataScannedInBytes={0}" -f $boundedBytes)
    Write-Host ("broader month DataScannedInBytes={0}" -f $broaderBytes)
    $monthPrefix = "dataset=encounter/year=$($date.Year)/month=$($date.Month)/"
    $hasOtherDays = Test-MonthHasOtherDays -Bucket $canonicalBucket -MonthPrefix $monthPrefix -Day $date.Day
    if ($hasOtherDays) {
        if ($boundedBytes -ge $broaderBytes) {
            throw "Partition pruning failed: bounded bytes $boundedBytes >= broader bytes $broaderBytes"
        }
        Write-Host "bounded bytes < broader bytes"
    }
    else {
        Write-Host "PRUNING_COMPARISON_SKIPPED_NO_ADDITIONAL_DATA"
    }

    Write-Step "Canonical object snapshot (after queries)"
    foreach ($dataset in $ApprovedTables) {
        $prefix = "dataset=$dataset/year=$($date.Year)/month=$($date.Month)/day=$($date.Day)/"
        $after = Get-S3ObjectRecords -Bucket $canonicalBucket -Prefix $prefix
        $beforeSig = Get-SnapshotSignature $before[$dataset]
        $afterSig = Get-SnapshotSignature $after
        if ($beforeSig -ne $afterSig) {
            throw "Canonical objects changed for $dataset during validation."
        }
        Write-Host "$dataset Key/Size/ETag snapshot unchanged"
    }

    Write-Step "HISTORICAL ANALYTICS VALIDATION PASSED"
    Write-Host "Infrastructure integrity only. SQL_EMPTY means the infrastructure query returned zero matching rows. No collection-completeness or evaluability conclusion is made."
    exit 0
}
catch {
    Write-Host ""
    Write-Host "HISTORICAL ANALYTICS VALIDATION FAILED"
    Write-Host $_.Exception.Message
    exit 1
}
finally {
    foreach ($path in $script:TempPaths) {
        if (Test-Path $path) {
            Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        }
    }
}
