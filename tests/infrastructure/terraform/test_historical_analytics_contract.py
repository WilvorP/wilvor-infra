from __future__ import annotations

import re
from pathlib import Path

import pytest


pytestmark = pytest.mark.infrastructure


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_DIR = REPO_ROOT / "modules" / "historical_analytics"
DATA_MODULE_DIR = REPO_ROOT / "modules" / "historical_facts_data"
TRANSPORT_DIR = REPO_ROOT / "modules" / "historical_facts"
DEV_MAIN = REPO_ROOT / "envs" / "dev" / "main.tf"
DEV_OUTPUTS = REPO_ROOT / "envs" / "dev" / "outputs.tf"
DEV_DOWN = REPO_ROOT / "scripts" / "dev-down.ps1"
DEV_RESET = REPO_ROOT / "scripts" / "dev-reset.ps1"
DATA_PLANE_MAIN = REPO_ROOT / "envs" / "dev-historical-data" / "main.tf"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def module_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(MODULE_DIR.glob("*.tf"))
    )


def _dev_module_block(text: str, module_name: str) -> str:
    marker = f'module "{module_name}" {{'
    assert marker in text
    after = text.split(marker, 1)[1]
    next_module = after.find('\nmodule "')
    if next_module == -1:
        return after
    return after[:next_module]


def test_module_exists_and_is_disabled_by_default():
    assert MODULE_DIR.is_dir()
    assert (MODULE_DIR / "variables.tf").exists()
    assert (MODULE_DIR / "locals.tf").exists()
    assert (MODULE_DIR / "results_bucket.tf").exists()
    assert (MODULE_DIR / "outputs.tf").exists()
    assert (MODULE_DIR / "README.md").exists()
    assert (MODULE_DIR / "glue.tf").exists()
    assert (MODULE_DIR / "athena.tf").exists()
    assert (MODULE_DIR / "monitoring.tf").exists()
    assert not (MODULE_DIR / "iam.tf").exists()
    variables = read(MODULE_DIR / "variables.tf")
    assert "variable \"enable_historical_analytics\"" in variables
    assert "default     = false" in variables
    assert "default = false" in variables or "default     = false" in variables


def test_dev_wires_analytics_disabled_and_collection_enabled():
    text = read(DEV_MAIN)
    analytics = _dev_module_block(text, "historical_analytics")
    historical_facts = _dev_module_block(text, "historical_facts")
    operational_api = _dev_module_block(text, "operational_api")

    assert 'source = "../../modules/historical_analytics"' in analytics
    assert "enable_historical_analytics = false" in analytics
    assert "enable_historical_analytics = true" not in analytics
    assert "enable_historical_facts = true" in historical_facts
    assert "enable_historical_facts = false" not in historical_facts
    assert "enable_historical_facts = true" in operational_api
    assert "enable_historical_facts = false" not in text
    assert (
        "enable_historical_analytics_dashboard = ("
        in operational_api
        or "enable_historical_analytics_dashboard =" in operational_api
    )
    assert (
        "module.historical_analytics.enable_historical_analytics"
        in operational_api
    )


def test_dev_outputs_are_safe_when_disabled():
    outputs = read(DEV_OUTPUTS)
    assert 'output "enable_historical_analytics"' in outputs
    assert "module.historical_analytics.enable_historical_analytics" in outputs
    assert 'output "historical_analytics_results_bucket_name"' in outputs
    assert "module.historical_analytics.results_bucket_name" in outputs
    assert 'output "historical_analytics_glue_database_name"' in outputs
    assert "module.historical_analytics.database_name" in outputs
    assert 'output "historical_analytics_encounter_table_name"' in outputs
    assert 'output "historical_analytics_risk_table_name"' in outputs
    assert 'output "historical_analytics_hazard_version_table_name"' in outputs
    assert 'output "historical_analytics_hazard_geometry_table_name"' in outputs
    assert 'output "historical_analytics_workgroup_name"' in outputs
    assert "module.historical_analytics.workgroup_name" in outputs
    assert 'output "historical_analytics_dashboard_name"' in outputs
    assert "module.historical_analytics.dashboard_name" in outputs
    assert "query_role" not in outputs
    assert "named_query" not in outputs


def test_result_bucket_naming_is_separate_from_canonical_facts():
    locals_text = read(MODULE_DIR / "locals.tf")
    assert (
        'historical_bucket_name = "${var.name_prefix}-historical-facts-${var.account_id}-${var.aws_region}"'
        in locals_text
    )
    assert (
        'results_bucket_name    = "${var.name_prefix}-historical-athena-results-${var.account_id}-${var.aws_region}"'
        in locals_text
        or 'results_bucket_name = "${var.name_prefix}-historical-athena-results-${var.account_id}-${var.aws_region}"'
        in locals_text
    )
    assert "historical-athena-results" in locals_text
    assert local_names_differ(locals_text)


def local_names_differ(locals_text: str) -> bool:
    return (
        "historical-facts-" in locals_text
        and "historical-athena-results-" in locals_text
        and locals_text.count("historical-facts-") >= 1
    )


def test_results_bucket_is_disposable_and_hardened():
    bucket = read(MODULE_DIR / "results_bucket.tf")
    variables = read(MODULE_DIR / "variables.tf")
    locals_text = read(MODULE_DIR / "locals.tf")
    assert "count = local.enabled ? 1 : 0" in bucket
    assert "force_destroy = true" in bucket
    assert "force_destroy = false" not in bucket
    assert 'sse_algorithm = "AES256"' in bucket
    assert "block_public_acls       = true" in bucket
    assert "block_public_policy     = true" in bucket
    assert "ignore_public_acls      = true" in bucket
    assert "restrict_public_buckets = true" in bucket
    assert 'object_ownership = "BucketOwnerEnforced"' in bucket
    assert "aws_s3_bucket_versioning" not in bucket
    assert "versioning_configuration" not in bucket
    assert "days = var.result_retention_days" in bucket
    assert "default     = 3" in variables
    assert "result_retention_days must be greater than 0" in variables
    assert "days_after_initiation = 1" in bucket
    assert 'DataType  = "historical-analytics-derived"' in locals_text
    assert 'DataType  = "historical-facts"' not in locals_text
    assert 'Component = "historical-analytics"' in locals_text


def test_module_does_not_own_the_persistent_historical_bucket():
    text = module_text()
    results = read(MODULE_DIR / "results_bucket.tf")
    assert 'resource "aws_s3_bucket" "historical_facts"' not in text
    assert 'resource "aws_s3_bucket" "athena_results"' in results
    assert "aws_s3_bucket_public_access_block" in results
    assert "aws_s3_bucket_ownership_controls" in results
    assert "aws_s3_bucket_server_side_encryption_configuration" in results
    assert "aws_s3_bucket_lifecycle_configuration" in results
    assert 'data "aws_s3_bucket" "historical_facts"' in read(
        MODULE_DIR / "locals.tf"
    )
    assert "count = local.enabled ? 1 : 0" in read(MODULE_DIR / "locals.tf")
    data_plane = read(DATA_MODULE_DIR / "bucket.tf")
    assert "force_destroy = false" in data_plane
    assert "force_destroy = true" not in data_plane


def test_historical_bucket_lookup_is_gated():
    locals_text = read(MODULE_DIR / "locals.tf")
    assert 'data "aws_s3_bucket" "historical_facts"' in locals_text
    lookup = locals_text.split('data "aws_s3_bucket" "historical_facts"')[1]
    assert "count = local.enabled ? 1 : 0" in lookup
    assert "local.historical_bucket_name" in lookup
    assert "terraform_remote_state" not in module_text()


def test_no_athena_query_role_or_sql_tool():
    text = module_text().lower()
    assert "aws_athena_named_query" not in text
    assert "aws_athena_prepared_statement" not in text
    assert "aws_glue_crawler" not in text
    assert "aws_iam_role" not in text
    assert "aws_dynamodb" not in text
    assert "startqueryexecution" not in text
    assert "execute_sql" not in text
    assert "run_query" not in text
    assert "query_athena" not in text
    transport = "\n".join(
        path.read_text(encoding="utf-8")
        for path in TRANSPORT_DIR.glob("*.tf")
    ).lower()
    assert "aws_glue" not in transport
    assert "athena" not in transport


def test_glue_database_and_four_tables_are_gated():
    glue = read(MODULE_DIR / "glue.tf")
    locals_text = read(MODULE_DIR / "locals.tf")
    outputs = read(MODULE_DIR / "outputs.tf")
    assert glue.count('resource "aws_glue_catalog_database"') == 1
    assert glue.count('resource "aws_glue_catalog_table"') == 2
    assert 'resource "aws_glue_catalog_table" "structured"' in glue
    assert 'resource "aws_glue_catalog_table" "hazard_geometry"' in glue
    assert "aws_glue_crawler" not in module_text()
    assert glue.count("count = local.enabled ? 1 : 0") == 2
    assert "for_each = local.enabled ? local.structured_schema_versions : {}" in glue
    assert (
        'glue_database_name     = "${replace(var.name_prefix, "-", "_")}_historical_facts"'
        in locals_text
    )
    assert 'name          = each.key' in glue
    assert 'name          = "hazard_geometry"' in glue
    assert 'encounter      = "wilvor.historical.encounter_fact.v1"' in locals_text
    assert 'risk           = "wilvor.historical.risk_fact.v1"' in locals_text
    assert 'hazard_version = "wilvor.historical.hazard_version_fact.v1"' in locals_text
    assert 'geometry_schema_version = "wilvor.historical.hazard_geometry_fact.v1"' in locals_text
    assert "encounter" in locals_text
    assert "risk" in locals_text
    assert "hazard_version" in locals_text
    assert 'output "database_name"' in outputs
    assert 'output "encounter_table_name"' in outputs
    assert 'output "risk_table_name"' in outputs
    assert 'output "hazard_version_table_name"' in outputs
    assert 'output "hazard_geometry_table_name"' in outputs
    assert (
        'value = local.enabled ? aws_glue_catalog_database.historical_facts[0].name : ""'
        in outputs
    )
    assert (
        'value = local.enabled ? aws_glue_catalog_table.structured["encounter"].name : ""'
        in outputs
    )
    assert (
        'value = local.enabled ? aws_glue_catalog_table.hazard_geometry[0].name : ""'
        in outputs
    )


def test_glue_dataset_locations_and_prefixes():
    glue = read(MODULE_DIR / "glue.tf")
    text = module_text()
    assert "dataset=${each.key}/" in glue
    assert "dataset=hazard_geometry/" in glue
    assert "dataset=encounter/" not in glue
    assert "dataset=risk/" not in glue
    assert "dataset=hazard_version/" not in glue
    assert "metadata/" not in glue
    assert "errors/" not in glue
    assert "_collection_control" not in text
    assert "MSCK" not in text
    assert "aws_glue_partition" not in text
    assert 'table_type    = "EXTERNAL_TABLE"' in glue
    assert "org.apache.hadoop.mapred.TextInputFormat" in glue
    assert "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat" in glue
    assert "compressionType" in glue
    assert 'classification                        = "json"' in glue or (
        'classification = "json"' in glue
    )


def test_structured_serde_is_hive_json_not_openx():
    glue = read(MODULE_DIR / "glue.tf")
    text = module_text()
    assert "org.apache.hive.hcatalog.data.JsonSerDe" in glue
    assert "org.openx.data.jsonserde.JsonSerDe" not in text
    assert "ignore.malformed.json" not in text
    assert "case.insensitive" not in text


def test_hazard_geometry_is_regex_raw_line():
    glue = read(MODULE_DIR / "glue.tf")
    geometry = glue.split('resource "aws_glue_catalog_table" "hazard_geometry"')[1]
    structured = glue.split('resource "aws_glue_catalog_table" "structured"')[1].split(
        'resource "aws_glue_catalog_table" "hazard_geometry"'
    )[0]
    assert "org.apache.hadoop.hive.serde2.RegexSerDe" in geometry
    assert 'input.regex" = "^(.*)$"' in geometry or '"input.regex" = "^(.*)$"' in geometry
    assert 'name = "json_record"' in geometry
    assert 'type = "string"' in geometry
    assert geometry.count("columns {") == 1
    assert "coordinates" not in geometry
    assert 'name = "geometry"' not in geometry
    assert "org.apache.hive.hcatalog.data.JsonSerDe" in structured
    assert "org.apache.hadoop.hive.serde2.RegexSerDe" not in structured


def test_partition_projection_uses_escaped_athena_placeholders():
    glue = read(MODULE_DIR / "glue.tf")
    locals_text = read(MODULE_DIR / "locals.tf")
    variables = read(MODULE_DIR / "variables.tf")
    assert re.search(r'"projection\.enabled"\s*=\s*"true"', locals_text)
    assert re.search(r'"projection\.year\.type"\s*=\s*"integer"', locals_text)
    assert re.search(r'"projection\.year\.digits"\s*=\s*"4"', locals_text)
    assert re.search(r'"projection\.month\.type"\s*=\s*"integer"', locals_text)
    assert re.search(r'"projection\.month\.range"\s*=\s*"1,12"', locals_text)
    assert re.search(r'"projection\.month\.digits"\s*=\s*"2"', locals_text)
    assert re.search(r'"projection\.day\.type"\s*=\s*"integer"', locals_text)
    assert re.search(r'"projection\.day\.range"\s*=\s*"1,31"', locals_text)
    assert re.search(r'"projection\.day\.digits"\s*=\s*"2"', locals_text)
    assert "year=$${year}/month=$${month}/day=$${day}" in glue
    assert "default     = 2026" in variables
    assert "default     = 2036" in variables
    assert "projection_year_min must be greater than 0" in variables
    assert "projection_year_max must be greater than 0" in variables
    assert "projection_year_max must be >= projection_year_min" in glue
    assert "partitionKeyFromLambda" not in glue
    assert "approximateArrivalTimestamp" not in glue
    assert glue.count("partition_keys {") == 6


def test_projection_follows_canonical_event_partition_layout():
    facts_locals = read(TRANSPORT_DIR / "locals.tf")
    glue = read(MODULE_DIR / "glue.tf")
    readme = read(MODULE_DIR / "README.md")
    assert (
        "dataset=!{partitionKeyFromLambda:dataset}/"
        "year=!{partitionKeyFromLambda:year}/"
        "month=!{partitionKeyFromLambda:month}/"
        "day=!{partitionKeyFromLambda:day}/"
        in facts_locals
    )
    assert "dataset=${each.key}/year=$${year}/month=$${month}/day=$${day}" in glue
    assert (
        "dataset=hazard_geometry/year=$${year}/month=$${month}/day=$${day}"
        in glue
    )
    assert "partitionKeyFromLambda" not in glue
    assert "!{timestamp:" not in glue
    assert "approximateArrivalTimestamp" not in glue
    assert "canonical event dates" in readme
    assert "detected_at_utc" in readme
    assert "generated_at_utc" in readme
    assert "materialized_at_utc" in readme


def test_glue_does_not_write_canonical_history():
    text = module_text()
    glue = read(MODULE_DIR / "glue.tf")
    results = read(MODULE_DIR / "results_bucket.tf")
    assert 'resource "aws_s3_bucket" "historical_facts"' not in text
    assert "aws_s3_object" not in text
    assert "aws_s3_bucket_object" not in text
    assert "s3:PutObject" not in text
    assert "s3:DeleteObject" not in text
    assert "aws_s3_bucket_policy" not in text
    assert "local.historical_bucket_id" in glue
    assert 'resource "aws_s3_bucket" "athena_results"' in results
    assert "force_destroy = true" in results


def test_schema_version_parameters_are_v1_constants():
    glue = read(MODULE_DIR / "glue.tf")
    locals_text = read(MODULE_DIR / "locals.tf")
    assert '"wilvor.expected_fact_schema_version"' in glue
    assert 'encounter      = "wilvor.historical.encounter_fact.v1"' in locals_text
    assert 'risk           = "wilvor.historical.risk_fact.v1"' in locals_text
    assert 'hazard_version = "wilvor.historical.hazard_version_fact.v1"' in locals_text
    assert 'geometry_schema_version = "wilvor.historical.hazard_geometry_fact.v1"' in locals_text


def test_athena_workgroup_is_gated_and_named():
    athena = read(MODULE_DIR / "athena.tf")
    locals_text = read(MODULE_DIR / "locals.tf")
    outputs = read(MODULE_DIR / "outputs.tf")
    text = module_text()
    assert text.count('resource "aws_athena_workgroup"') == 1
    assert 'resource "aws_athena_workgroup" "historical_analytics"' in athena
    assert "count = local.enabled ? 1 : 0" in athena
    assert 'workgroup_name         = "${var.name_prefix}-historical-analytics"' in locals_text
    assert "name        = local.workgroup_name" in athena
    assert 'state       = "ENABLED"' in athena
    assert "aws_athena_named_query" not in text
    assert "aws_athena_prepared_statement" not in text
    assert "aws_iam_role" not in text
    assert (
        'value = local.enabled ? aws_athena_workgroup.historical_analytics[0].name : ""'
        in outputs
    )
    assert 'output "workgroup_name"' in outputs
    assert 'output "query_role"' not in outputs


def test_athena_workgroup_controls_and_cutoff():
    athena = read(MODULE_DIR / "athena.tf")
    variables = read(MODULE_DIR / "variables.tf")
    assert "enforce_workgroup_configuration    = true" in athena
    assert "publish_cloudwatch_metrics_enabled = true" in athena
    assert "requester_pays_enabled             = false" in athena
    assert "bytes_scanned_cutoff_per_query     = var.bytes_scanned_cutoff_per_query" in athena
    assert "variable \"bytes_scanned_cutoff_per_query\"" in variables
    assert "default     = 10737418240" in variables
    assert "10000000" in variables
    assert "bytes_scanned_cutoff_per_query must be at least 10000000 bytes" in variables
    assert "10000000000" not in variables
    assert "10000000000" not in athena


def test_athena_results_use_disposable_bucket_only():
    athena = read(MODULE_DIR / "athena.tf")
    locals_text = read(MODULE_DIR / "locals.tf")
    assert 'athena_results_prefix  = "athena-results/"' in locals_text
    assert (
        'output_location       = "s3://${aws_s3_bucket.athena_results[0].id}/${local.athena_results_prefix}"'
        in athena
    )
    assert "local.historical_bucket_id" not in athena
    assert "historical-facts-" not in athena
    assert "dataset=" not in athena
    assert "metadata/" not in athena
    assert "errors/" not in athena
    assert 'encryption_option = "SSE_S3"' in athena
    assert "expected_bucket_owner = var.account_id" in athena
    assert "acl_configuration" not in athena
    assert "s3_acl_option" not in athena


def test_athena_engine_is_version_3():
    athena = read(MODULE_DIR / "athena.tf")
    assert 'selected_engine_version = "Athena engine version 3"' in athena
    assert "Auto" not in athena


def test_result_reuse_is_not_a_workgroup_setting():
    text = module_text()
    readme = read(MODULE_DIR / "README.md")
    assert "result_reuse" not in text.lower()
    assert "ResultReuseByAgeConfiguration" not in text
    assert "reuse" not in read(MODULE_DIR / "athena.tf").lower()
    assert "Result reuse is **not** a workgroup setting" in readme
    assert "ResultReuseByAgeConfiguration.Enabled = false" in readme


def test_monitoring_resources_are_gated():
    monitoring = read(MODULE_DIR / "monitoring.tf")
    outputs = read(MODULE_DIR / "outputs.tf")
    assert 'resource "aws_cloudwatch_dashboard" "historical_analytics"' in monitoring
    assert 'resource "aws_cloudwatch_event_rule" "query_failures"' in monitoring
    assert 'resource "aws_cloudwatch_event_target" "query_failures"' in monitoring
    assert 'resource "aws_cloudwatch_log_group" "query_failures"' in monitoring
    assert 'resource "aws_cloudwatch_log_resource_policy"' in monitoring
    assert 'resource "aws_cloudwatch_log_metric_filter" "query_failures"' in monitoring
    assert 'resource "aws_cloudwatch_metric_alarm" "query_failures"' in monitoring
    assert monitoring.count("count = local.enabled ? 1 : 0") >= 7
    assert "aws_sns" not in monitoring
    assert "aws_lambda" not in monitoring
    assert 'alarm_name          = "${var.name_prefix}-historical-analytics-zero' not in monitoring
    assert "IncomingRecords" not in monitoring
    assert (
        'value = local.enabled ? aws_cloudwatch_dashboard.historical_analytics[0].dashboard_name : ""'
        in outputs
    )


def test_athena_failure_event_pattern_uses_canceled_spelling():
    monitoring = read(MODULE_DIR / "monitoring.tf")
    assert '"aws.athena"' in monitoring
    assert '"Athena Query State Change"' in monitoring
    assert "workgroupName" in monitoring
    assert "local.workgroup_name" in monitoring
    assert '"FAILED"' in monitoring
    assert '"CANCELED"' in monitoring
    assert "CANCELLED" not in monitoring
    assert "currentState" in monitoring


def test_eventbridge_logs_resource_policy_matches_repo_pattern():
    monitoring = read(MODULE_DIR / "monitoring.tf")
    assert '"events.amazonaws.com"' in monitoring
    assert "logs:CreateLogStream" in monitoring
    assert "logs:PutLogEvents" in monitoring
    assert "aws_iam_role" not in monitoring
    assert "s3:GetObject" not in monitoring
    assert "s3:PutObject" not in monitoring
    assert "athena:StartQueryExecution" not in monitoring
    assert "/aws/events/${var.name_prefix}-historical-analytics-query-failures" in (
        read(MODULE_DIR / "locals.tf")
    )
    assert "retention_in_days = 3" in monitoring
    principals = monitoring.split("principals")[1].split("actions")[0]
    assert "events.amazonaws.com" in principals
    assert "lambda.amazonaws.com" not in principals


def test_query_failure_metric_and_alarm():
    monitoring = read(MODULE_DIR / "monitoring.tf")
    locals_text = read(MODULE_DIR / "locals.tf")
    assert 'query_failure_metric_name    = "HistoricalAnalyticsQueryFailed"' in locals_text
    assert 'wilvor_metric_namespace      = "Wilvor/Pipeline"' in locals_text
    assert 'WorkGroup = "$.detail.workgroupName"' in monitoring
    assert 'value     = "1"' in monitoring
    assert 'treat_missing_data  = local.analytics_alarm_defaults.treat_missing_data' in monitoring
    assert 'treat_missing_data  = "notBreaching"' in locals_text
    assert "threshold           = 1" in locals_text
    assert "evaluation_periods  = 1" in locals_text
    assert "period              = 300" in locals_text
    assert "aws_sns_topic" not in monitoring
    assert "alarm_actions" not in monitoring
    alarm = monitoring.split('resource "aws_cloudwatch_metric_alarm" "query_failures"')[1]
    alarm = alarm.split('resource "aws_cloudwatch_dashboard"')[0]
    assert "ProcessedBytes" not in alarm
    assert "metric_name         = local.query_failure_metric_name" in alarm


def test_analytics_dashboard_metrics_and_contract_text():
    monitoring = read(MODULE_DIR / "monitoring.tf")
    assert 'dashboard_name = local.dashboard_name' in monitoring
    assert 'dashboard_name         = "${var.name_prefix}-historical-analytics"' in (
        read(MODULE_DIR / "locals.tf")
    )
    assert "AWS/Athena" in monitoring
    assert "ProcessedBytes" in monitoring
    assert "TotalExecutionTime" in monitoring
    assert "EngineExecutionTime" in monitoring
    assert "QueryPlanningTime" in monitoring
    assert "QueryQueueTime" in monitoring
    assert "WorkGroup" in monitoring
    assert "DPUCount" not in monitoring
    assert "BucketSizeBytes" in monitoring
    assert "NumberOfObjects" in monitoring
    assert "StandardStorage" in monitoring
    assert "AllStorageTypes" in monitoring
    assert "period = 86400" in monitoring
    assert "NOT VERIFIED_ZERO" in monitoring
    assert "evaluate_collection_window" in monitoring
    assert "best effort" in monitoring
    assert "10 GiB" in monitoring
    assert "3-day" in monitoring
    assert "$" not in monitoring or "billed" in monitoring
    assert "0.00" not in monitoring
    assert "USD" not in monitoring
    assert "row-count" not in monitoring.lower()
    assert "aws_glue_crawler" not in monitoring


def test_operational_api_analytics_dashboard_is_gated():
    api = read(REPO_ROOT / "modules" / "operational_api" / "api.tf")
    variables = read(REPO_ROOT / "modules" / "operational_api" / "variables.tf")
    assert "variable \"enable_historical_analytics_dashboard\"" in variables
    assert "default     = false" in variables
    assert (
        'var.enable_historical_analytics_dashboard ? ["historical-analytics"] : []'
        in api
    )
    assert "ENABLE_HISTORICAL_ANALYTICS_DASHBOARD" in api
    assert "athena:StartQueryExecution" not in api
    assert "athena:GetQueryExecution" not in api
    assert "athena:GetQueryResults" not in api
    uncond = api.split("var.enable_historical_analytics_dashboard ?")[0]
    assert '"historical-analytics"' not in uncond
    assert "cloudwatch:GetDashboard" in api


def test_data_plane_root_is_unchanged():
    main = read(DATA_PLANE_MAIN)
    assert 'source = "../../modules/historical_facts_data"' in main
    assert "historical_analytics" not in main
    assert "enable_historical_analytics" not in main


def test_dev_down_still_protects_data_plane():
    down = read(DEV_DOWN)
    reset = read(DEV_RESET)
    assert "must not target the persistent historical data plane" in down
    assert "DEACTIVATE" in down
    assert "dev-historical-data" in reset
    assert not (REPO_ROOT / "scripts" / "historical-data-down.ps1").exists()


def test_readme_documents_ownership_and_lifecycle():
    readme = read(MODULE_DIR / "README.md")
    for needle in (
        "envs/dev-historical-data",
        "force_destroy = false",
        "force_destroy = true",
        "3-day",
        "dev-down",
        "DEACTIVATE",
        "does **not** create crawlers, runtime query IAM roles",
        "historical-data-down.ps1",
        "enable_historical_analytics",
        "OpenX",
        "org.apache.hive.hcatalog.data.JsonSerDe",
        "RegexSerDe",
        "$${year}",
        "detected_at_utc",
        "generated_at_utc",
        "materialized_at_utc",
        "COUNT(*) = 0",
        "evaluate_collection_window",
        "ResultReuseByAgeConfiguration.Enabled = false",
        "wilvor-dev-historical-analytics",
        "Athena engine version 3",
        "10737418240",
        "expected_bucket_owner",
        "athena-results/",
        "best effort",
        "CANCELED",
        "GetQueryExecution",
        "HistoricalAnalyticsQueryFailed",
        "notBreaching",
            "ENABLE_HISTORICAL_ANALYTICS_DASHBOARD",
            "not `CANCELLED`",
        ):
            assert needle in readme
