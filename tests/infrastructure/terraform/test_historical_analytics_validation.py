from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

import pytest


pytestmark = pytest.mark.infrastructure


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "validate_historical_analytics.ps1"
VALIDATION_DIR = REPO_ROOT / "modules" / "historical_analytics" / "validation"
MODULE_README = REPO_ROOT / "modules" / "historical_analytics" / "README.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def validation_text() -> str:
    parts = [read(SCRIPT)]
    parts.extend(read(path) for path in sorted(VALIDATION_DIR.glob("*")))
    return "\n".join(parts)


def script_param_block() -> str:
    text = read(SCRIPT)
    match = re.search(r"param\((.*?)\)\s*Set-StrictMode", text, re.S)
    assert match, "script param() block not found"
    return match.group(1)


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_validation_artifacts_exist():
    assert SCRIPT.is_file()
    assert (VALIDATION_DIR / "README.md").is_file()
    for name in (
        "count_partition.sql",
        "count_month.sql",
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
    ):
        assert (VALIDATION_DIR / name).is_file()


def test_script_has_no_arbitrary_sql_parameter():
    block = script_param_block()
    assert re.search(r"\$Sql\b", block) is None
    assert re.search(r"\$Query\b", block) is None
    assert re.search(r"\$QueryString\b", block) is None
    assert re.search(r"\$Statement\b", block) is None
    assert "$ValidationDate" in block
    assert "$DryRun" in block
    assert "$NamePrefix" in block
    text = read(SCRIPT)
    assert "execute_sql" not in text.lower()
    assert "run_query" not in text.lower()


def test_result_reuse_is_explicitly_disabled():
    text = read(SCRIPT)
    assert "ResultReuseByAgeConfiguration" in text
    assert "Enabled = $false" in text
    assert "ResultConfiguration" not in text.split("function Invoke-FixedAthenaQuery")[1].split(
        "function Get-AthenaScalarRows"
    )[0]
    assert "OutputLocation" not in text.split("Invoke-FixedAthenaQuery")[1].split(
        "Assert-ResultLocation"
    )[0]
    assert "WorkGroup" in text
    assert "$workgroup" in text


def test_names_are_derived_not_caller_tables():
    text = read(SCRIPT)
    assert '_historical_facts"' in text or "_historical_facts" in text
    assert "-historical-analytics" in text
    assert "-historical-facts-" in text
    assert "-historical-athena-results-" in text
    assert 'ApprovedTables = @("encounter", "risk", "hazard_version", "hazard_geometry")' in text
    assert "evaluate_collection_window" not in text
    assert "coverage.py" not in text


def test_exact_s3_athena_count_and_snapshot():
    text = read(SCRIPT)
    assert "S3 JSONL count" in text
    assert "Athena COUNT(*)" in text
    assert "Get-JsonlLineCount" in text
    assert "GZipStream" in text
    assert "Key" in text and "Size" in text and "ETag" in text
    assert "Canonical objects changed" in text
    assert "Get-S3ObjectRecords" in text


def test_identity_schema_geometry_and_pruning_checks():
    text = validation_text()
    assert "distinct_record_id" in text
    assert "distinct_dedup_id" in text
    assert "physical fact rows" in text.lower() or "physical_rows" in text
    assert "record_id <> encounter_id" in text
    assert "record_id <> risk_id OR dedup_id <> risk_id" in text
    assert "record_id <> hazard_version_key" in text
    assert "EXCEPT" in text
    assert "json_extract_scalar(json_record, '$.record_id')" in text
    assert "json_extract_scalar(json_record, '$.geometry_type')" in text
    assert "$.geometry.type" in text
    assert "POLYGON" in text
    assert "Polygon" in text
    assert "DataScannedInBytes" in text
    assert "PRUNING_COMPARISON_SKIPPED_NO_ADDITIONAL_DATA" in text
    assert "athena-results/" in text
    assert "SQL_EMPTY" in text


def test_verified_zero_is_forbidden_in_executable_artifacts():
    script = read(SCRIPT)
    assert "VERIFIED_ZERO" not in script
    assert "SQL_EMPTY" in script
    for path in VALIDATION_DIR.glob("*.sql"):
        assert "VERIFIED_ZERO" not in read(path)
    assert "VERIFIED_ZERO" in read(MODULE_README)
    assert "SQL_EMPTY" in read(MODULE_README)
    assert "VERIFIED_ZERO" not in read(VALIDATION_DIR / "README.md")


def test_docs_state_sql_empty_and_frontend_gap():
    readme = read(MODULE_README)
    validation = read(VALIDATION_DIR / "README.md")
    assert "SQL_EMPTY" in readme
    assert "VERIFIED_ZERO" in readme
    assert "ResultReuseByAgeConfiguration.Enabled = false" in readme
    assert "operator SSO" in readme
    assert "AI query surface" in readme
    assert "cloudwatchDashboards.test.ts" in readme
    assert "SQL_EMPTY" in validation
    assert "2026-09-11" in validation


def test_dry_run_renders_sql_and_makes_no_aws_calls():
    completed = run_script("-ValidationDate", "2026-09-11", "-DryRun")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    output = completed.stdout + completed.stderr
    assert "DRY RUN: no Athena/S3 execution occurred" in output
    assert "AWS calls: 0" in output
    assert "year=2026/month=09/day=11" in output
    assert "wilvor_dev_historical_facts" in output
    assert "wilvor-dev-historical-analytics" in output
    assert "SELECT COUNT(*)" in output
    assert "SQL_EMPTY" in output
    assert "VERIFIED_ZERO" not in output
    assert "No collection-completeness or evaluability conclusion is made." in output
    assert "HISTORICAL ANALYTICS VALIDATION PASSED" not in output
    assert "start-query-execution" not in output.lower()


def test_invalid_date_fails_before_aws():
    completed = run_script("-ValidationDate", "2026-13-40", "-DryRun")
    assert completed.returncode != 0
    output = completed.stdout + completed.stderr
    assert "YYYY-MM-DD" in output or "ParseExact" in output or "VALIDATION FAILED" in output
    assert "start-query-execution" not in output.lower()


def _invoke_aws_cli_function_text() -> str:
    text = read(SCRIPT)
    match = re.search(
        r"function Invoke-AwsCli \{.*?\n\}\n\nfunction ",
        text,
        re.S,
    )
    assert match, "Invoke-AwsCli function not found"
    return match.group(0).rsplit("function ", 1)[0]


def test_invoke_aws_cli_uses_explicit_named_argv_array():
    function_text = _invoke_aws_cli_function_text()
    assert "ValueFromRemainingArguments" not in function_text
    assert "[string[]]$AwsArgs" in function_text
    assert "Mandatory = $true" in function_text
    assert '& aws @AwsArgs' in function_text
    assert '-join "`n"' in function_text

    script = read(SCRIPT)
    call_sites = [
        line.strip()
        for line in script.splitlines()
        if "Invoke-AwsCli" in line and not line.strip().startswith("function ")
    ]
    assert call_sites
    for site in call_sites:
        assert "-AwsArgs" in site, site

    s3_fn = script.split("function Get-S3ObjectRecords")[1].split("function Get-JsonlLineCount")[0]
    assert "$cliArgs" in s3_fn
    assert re.search(r"\$args\b", s3_fn) is None


def test_invoke_aws_cli_argv_is_not_joined_into_one_token():
    function_text = _invoke_aws_cli_function_text()
    smoke = (
        "$ErrorActionPreference = 'Stop'\n"
        "$global:LASTEXITCODE = 0\n"
        "$script:DryRunActive = $false\n"
        "$script:AwsCallCount = 0\n"
        "$script:Captured = @()\n"
            "function aws { $script:Captured = @($args); $global:LASTEXITCODE = 0; '{\"ok\":true}' }\n"
        f"{function_text}\n"
        "Invoke-AwsCli -AwsArgs @('sts','get-caller-identity','--profile','wilvor-dev') | Out-Null\n"
        "if ($script:Captured.Count -ne 4) { Write-Output ('COUNT=' + $script:Captured.Count); exit 1 }\n"
        "if ($script:Captured[0] -ne 'sts') { exit 1 }\n"
        "Write-Output ('ARGV=' + ($script:Captured -join '|'))\n"
    )
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".ps1",
        delete=False,
        encoding="utf-8",
    ) as handle:
        handle.write(smoke)
        path = handle.name
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "ARGV=sts|get-caller-identity|--profile|wilvor-dev" in completed.stdout


def _convert_to_object_array_function_text() -> str:
    text = read(SCRIPT)
    match = re.search(
        r"function ConvertTo-ObjectArray \{.*?\n\}\n\nfunction ",
        text,
        re.S,
    )
    assert match, "ConvertTo-ObjectArray function not found"
    return match.group(0).rsplit("function ", 1)[0]


def test_s3_collection_call_sites_normalize_arrays():
    script = read(SCRIPT)
    assert "function ConvertTo-ObjectArray" in script
    assignments = [
        line.strip()
        for line in script.splitlines()
        if "Get-S3ObjectRecords" in line and not line.strip().startswith("function ")
    ]
    assert assignments
    for line in assignments:
        assert "@(ConvertTo-ObjectArray" in line, line


def test_convert_to_object_array_zero_one_many_under_strictmode():
    function_text = _convert_to_object_array_function_text()
    smoke = (
        "Set-StrictMode -Version Latest\n"
        "$ErrorActionPreference = 'Stop'\n"
        f"{function_text}\n"
        "$zero = @(ConvertTo-ObjectArray $null)\n"
        "if ($zero.Count -ne 0) { Write-Output 'ZERO_FAIL'; exit 1 }\n"
        "$nullish = @(ConvertTo-ObjectArray @($null))\n"
        "if ($nullish.Count -ne 0) { Write-Output 'NULL_ITEM_FAIL'; exit 1 }\n"
        "$oneIn = [pscustomobject]@{ Key = 'a'; Size = 1; ETag = 'e' }\n"
        "$one = @(ConvertTo-ObjectArray $oneIn)\n"
        "if ($one.Count -ne 1) { Write-Output ('ONE_FAIL=' + $one.Count); exit 1 }\n"
        "if ($one[0].Key -ne 'a') { exit 1 }\n"
        "$manyIn = @(\n"
        "  [pscustomobject]@{ Key = 'a' },\n"
        "  [pscustomobject]@{ Key = 'b' }\n"
        ")\n"
        "$many = @(ConvertTo-ObjectArray $manyIn)\n"
        "if ($many.Count -ne 2) { Write-Output ('MANY_FAIL=' + $many.Count); exit 1 }\n"
        "function Get-OneRecord { return [pscustomobject]@{ Key = 'only' } }\n"
        "$unwrapped = Get-OneRecord\n"
        "$caught = $false\n"
        "try { [void]$unwrapped.Count } catch { $caught = $true }\n"
        "if (-not $caught) { Write-Output 'STRICT_DID_NOT_THROW'; exit 1 }\n"
        "$wrapped = @(ConvertTo-ObjectArray (Get-OneRecord))\n"
        "if ($wrapped.Count -ne 1) { Write-Output 'WRAP_FAIL'; exit 1 }\n"
        "Write-Output 'COLLECTION_SHAPE_OK'\n"
    )
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".ps1",
        delete=False,
        encoding="utf-8",
    ) as handle:
        handle.write(smoke)
        path = handle.name
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "COLLECTION_SHAPE_OK" in completed.stdout


def _get_file_uri_function_text() -> str:
    text = read(SCRIPT)
    match = re.search(
        r"function Get-FileUri \{.*?\n\}\n\nfunction ",
        text,
        re.S,
    )
    assert match, "Get-FileUri function not found"
    return match.group(0).rsplit("function ", 1)[0]


def test_get_file_uri_uses_aws_cli_windows_file_prefix():
    function_text = _get_file_uri_function_text()
    assert "GetFullPath" in function_text
    assert '"file://$fullPath"' in function_text
    assert "file:///" not in function_text
    assert "-replace" not in function_text

    smoke = (
        "Set-StrictMode -Version Latest\n"
        "$ErrorActionPreference = 'Stop'\n"
        f"{function_text}\n"
        "$dir = Join-Path ([System.IO.Path]::GetTempPath()) "
        "('wilvor-fileuri-' + [guid]::NewGuid().ToString('N') + '\\Temp Folder')\n"
        "New-Item -ItemType Directory -Force -Path $dir | Out-Null\n"
        "$path = Join-Path $dir 'request.json'\n"
        "[System.IO.File]::WriteAllText($path, '{}')\n"
        "try {\n"
        "  $uri = Get-FileUri $path\n"
        "  if ($uri -notmatch '^file://[A-Za-z]:') { Write-Output ('PREFIX=' + $uri); exit 1 }\n"
        "  if ($uri.StartsWith('file:///')) { Write-Output ('TRIPLE=' + $uri); exit 1 }\n"
        "  if ($uri -notmatch 'Temp Folder\\\\request\\.json$') { "
        "    Write-Output ('SPACEPATH=' + $uri); exit 1 "
        "  }\n"
        "  Write-Output ('URI=' + $uri)\n"
        "} finally {\n"
        "  Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue\n"
        "}\n"
    )
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".ps1",
        delete=False,
        encoding="utf-8",
    ) as handle:
        handle.write(smoke)
        path = handle.name
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "URI=file://" in completed.stdout
    assert "file:///" not in completed.stdout.split("URI=", 1)[1]


def test_cli_input_json_file_uri_is_one_argv_token():
    invoke_text = _invoke_aws_cli_function_text()
    file_uri_text = _get_file_uri_function_text()
    smoke = (
        "Set-StrictMode -Version Latest\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$global:LASTEXITCODE = 0\n"
        "$script:DryRunActive = $false\n"
        "$script:AwsCallCount = 0\n"
        "$script:Captured = @()\n"
        "function aws { $script:Captured = @($args); $global:LASTEXITCODE = 0; '{\"ok\":true}' }\n"
        f"{invoke_text}\n"
        f"{file_uri_text}\n"
        "$dir = Join-Path ([System.IO.Path]::GetTempPath()) "
        "('wilvor-cliarg-' + [guid]::NewGuid().ToString('N') + '\\Temp Folder')\n"
        "New-Item -ItemType Directory -Force -Path $dir | Out-Null\n"
        "$path = Join-Path $dir 'request.json'\n"
        "[System.IO.File]::WriteAllText($path, '{}')\n"
        "try {\n"
        "  $uri = Get-FileUri $path\n"
        "  Invoke-AwsCli -AwsArgs @('athena','start-query-execution',"
        "'--cli-input-json',$uri) | Out-Null\n"
        "  if ($script:Captured.Count -ne 4) { "
        "    Write-Output ('COUNT=' + $script:Captured.Count); exit 1 "
        "  }\n"
        "  if ($script:Captured[2] -ne '--cli-input-json') { exit 1 }\n"
        "  if ($script:Captured[3] -ne $uri) { "
        "    Write-Output ('TOKEN=' + $script:Captured[3]); exit 1 "
        "  }\n"
        "  if ($script:Captured[3].Contains(' ')) { }\n"
        "  else { Write-Output 'NO_SPACE'; exit 1 }\n"
        "  Write-Output ('ARGV3=' + $script:Captured[3])\n"
        "} finally {\n"
        "  Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue\n"
        "}\n"
    )
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".ps1",
        delete=False,
        encoding="utf-8",
    ) as handle:
        handle.write(smoke)
        path = handle.name
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "ARGV3=file://" in completed.stdout
    assert "file:///" not in completed.stdout.split("ARGV3=", 1)[1]


def test_powershell_parser_accepts_script():
    command = (
        "$errs = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{SCRIPT.as_posix()}', [ref]$null, [ref]$errs); "
        "if ($errs) { $errs | ForEach-Object { $_.ToString() }; exit 1 }"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
