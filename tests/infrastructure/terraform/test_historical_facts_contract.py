from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.infrastructure


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_DIR = REPO_ROOT / "modules" / "historical_facts"
DATA_MODULE_DIR = REPO_ROOT / "modules" / "historical_facts_data"
DEV_MAIN = REPO_ROOT / "envs" / "dev" / "main.tf"
DEV_DOWN = REPO_ROOT / "scripts" / "dev-down.ps1"
DEV_RESET = REPO_ROOT / "scripts" / "dev-reset.ps1"
SIGMET_PROCESSOR_TF = REPO_ROOT / "modules" / "sigmet" / "processor.tf"
API_TF = REPO_ROOT / "modules" / "operational_api" / "api.tf"


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


def test_dev_historical_facts_collection_is_enabled():
    text = read(DEV_MAIN)
    historical_facts = _dev_module_block(text, "historical_facts")
    operational_api = _dev_module_block(text, "operational_api")

    assert 'source = "../../modules/historical_facts"' in historical_facts
    assert "enable_historical_facts = true" in historical_facts
    assert "enable_historical_facts = false" not in historical_facts
    assert "enable_historical_facts = true" in operational_api
    assert "enable_historical_facts = false" not in operational_api
    assert "enable_historical_facts = false" not in text
    assert "historical_facts_force_destroy" not in text


def test_dev_sigmet_uses_historical_geometry_outputs():
    text = read(DEV_MAIN)
    assert (
        "module.historical_facts.geometry_firehose_stream_name" in text
    )
    assert (
        "module.historical_facts.geometry_firehose_stream_arn" in text
    )


def test_no_glue_athena_or_coverage_start_variable():
    text = module_text().lower()
    assert "aws_glue" not in text
    assert "athena" not in text
    assert "coverage_start_utc" not in read(MODULE_DIR / "variables.tf")
    assert "aws_dynamodb_stream" not in text


def test_transport_does_not_own_the_persistent_bucket():
    text = module_text()
    assert 'resource "aws_s3_bucket"' not in text
    assert "aws_s3_bucket_public_access_block" not in text
    assert "aws_s3_bucket_ownership_controls" not in text
    assert "aws_s3_bucket_server_side_encryption_configuration" not in text
    assert "aws_s3_bucket_versioning" not in text
    assert "aws_s3_bucket_lifecycle_configuration" not in text
    assert not (MODULE_DIR / "bucket.tf").exists()
    assert 'data "aws_s3_bucket" "historical_facts"' in text
    assert "count = local.enabled ? 1 : 0" in read(MODULE_DIR / "locals.tf")
    assert (
        'bucket_name = "${var.name_prefix}-historical-facts-${var.account_id}-${var.aws_region}"'
        in read(MODULE_DIR / "locals.tf")
    )


def test_monitoring_exists_and_is_gated():
    monitoring = read(MODULE_DIR / "monitoring.tf")
    coverage = read(MODULE_DIR / "coverage.tf")
    assert (MODULE_DIR / "monitoring.tf").exists()
    assert 'aws_cloudwatch_dashboard" "historical_facts"' in monitoring
    assert "count = local.enabled ? 1 : 0" in monitoring
    assert 'treat_missing_data  = "notBreaching"' in monitoring
    assert "threshold           = 1800" in monitoring
    assert "FailedProcessing.Records" not in monitoring
    assert "aws_sns" not in monitoring
    assert "count = local.enabled ? 1 : 0" in coverage
    assert "wilvor.historical.control" in read(MODULE_DIR / "events.tf")
    assert "collection.probe" in read(MODULE_DIR / "events.tf")


def test_two_directput_firehose_streams_share_transform():
    firehose = read(MODULE_DIR / "firehose.tf")
    locals_text = read(MODULE_DIR / "locals.tf")
    variables = read(MODULE_DIR / "variables.tf")
    assert 'facts    = local.facts_stream_name' in firehose
    assert 'geometry = local.geometry_stream_name' in firehose
    assert 'destination = "extended_s3"' in firehose
    assert "kinesis_source_configuration" not in firehose
    assert 'compression_format = "GZIP"' in firehose
    assert "buffering_size     = var.firehose_buffering_size_mib" in firehose
    assert (
        "buffering_interval = var.firehose_buffering_interval_seconds"
        in firehose
    )
    assert "dynamic_partitioning_configuration" in firehose
    assert 'type = "Lambda"' in firehose
    assert "AppendDelimiterToRecord" not in firehose
    assert "data_format_conversion_configuration" not in firehose
    assert "partitionKeyFromLambda:dataset" in locals_text
    assert "default = 128" in variables
    assert "default = 900" in variables
    assert "data.aws_s3_bucket.historical_facts[0].arn" in firehose


def test_event_time_partition_placeholders_not_arrival_time():
    text = module_text()
    assert "partitionKeyFromLambda:dataset" in text
    assert "approximateArrivalTimestamp" not in text


def test_four_narrow_eventbridge_patterns_target_facts_only():
    events = read(MODULE_DIR / "events.tf")
    assert '"wilvor.encounter"' in events
    assert '"wilvor.risk"' in events
    assert '"wilvor.weather"' in events
    assert '"wilvor.historical.control"' in events
    assert '"collection.probe"' in events
    assert "hazard_geometry" not in events
    assert "geometry.materialized" not in events
    assert (
        'arn            = aws_kinesis_firehose_delivery_stream.historical["facts"].arn'
        in events
    )
    assert "maximum_event_age_in_seconds = 86400" in events
    assert "maximum_retry_attempts       = 185" in events
    assert "visibility_timeout_seconds = 90" in events


def test_dlq_sse_and_fourteen_day_retention():
    events = read(MODULE_DIR / "events.tf")
    variables = read(MODULE_DIR / "variables.tf")
    assert "sqs_managed_sse_enabled    = true" in events or (
        "sqs_managed_sse_enabled = true" in events
    )
    assert "default = 1209600" in variables


def test_transform_lambda_logs_only_no_dynamodb():
    transform = read(MODULE_DIR / "transform.tf")
    iam = read(MODULE_DIR / "iam.tf")
    assert 'runtime = "python3.12"' in transform
    assert "timeout     = 60" in transform or "timeout = 60" in transform

    transform_policy = iam.split(
        'data "aws_iam_policy_document" "transform" {'
    )[1].split('data "aws_iam_policy_document" "firehose" {')[0]
    assert "logs:CreateLogStream" in transform_policy
    assert "dynamodb:" not in transform_policy.lower()
    assert "s3:" not in transform_policy.lower()
    assert "firehose:" not in transform_policy.lower()

    eventbridge_policy = iam.split(
        'data "aws_iam_policy_document" "eventbridge" {'
    )[1]
    assert "firehose:PutRecord" in eventbridge_policy
    assert 'historical["facts"].arn' in eventbridge_policy
    assert "geometry" not in eventbridge_policy


def test_horizon_inputs_match_committed_timeouts():
    encounter = read(REPO_ROOT / "modules" / "encounter" / "processor.tf")
    risk = read(REPO_ROOT / "modules" / "risk" / "processor.tf")
    sigmet = read(SIGMET_PROCESSOR_TF)
    events = read(MODULE_DIR / "events.tf")
    variables = read(MODULE_DIR / "variables.tf")
    assert "timeout     = 60" in encounter or "timeout = 60" in encounter
    assert "timeout     = 30" in risk or "timeout = 30" in risk
    assert "timeout     = 60" in sigmet
    assert "maximum_event_age_in_seconds = 86400" in events
    assert "default = 900" in variables


def test_firehose_iam_is_least_privilege_s3():
    iam = read(MODULE_DIR / "iam.tf")
    coverage = read(MODULE_DIR / "coverage.tf")
    for action in (
        "s3:AbortMultipartUpload",
        "s3:GetObject",
        "s3:PutObject",
        "s3:ListBucket",
        "lambda:InvokeFunction",
    ):
        assert action in iam or action in coverage
    assert "s3:*" not in iam
    assert "s3:*" not in coverage
    assert "firehose:*" not in iam
    assert "firehose:*" not in coverage
    assert "events:*" not in iam
    assert "cloudwatch:*" not in coverage
    assert "cloudwatch:GetMetricData" in coverage
    assert "metadata/incidents/*" in coverage
    assert 'sid    = "WriteDomain2Incidents"' in coverage or 'sid = "WriteDomain2Incidents"' in coverage


def test_sigmet_firehose_permission_is_geometry_put_record_only():
    text = read(SIGMET_PROCESSOR_TF)
    assert "PutHistoricalGeometryFacts" in text
    assert "WriteHistoricalGapMetadata" in text
    assert "historical_facts_bucket_arn" in text
    assert "metadata/incidents/*" in text
    assert "metadata/gaps/*" not in text
    geometry_block = text.split("PutHistoricalGeometryFacts")[1].split(
        "aws_iam_role_policy"
    )[0]
    assert "PutRecordBatch" not in geometry_block


def test_operational_api_catalog_id_is_conditional():
    text = read(API_TF)
    assert 'var.enable_historical_facts ? ["historical-facts"] : []' in text
    assert "ENABLE_HISTORICAL_FACTS_DASHBOARD" in text
    uncond = text.split("var.enable_historical_facts ?")[0]
    assert '"historical-facts"' not in uncond


def test_required_outputs_exist():
    outputs = read(MODULE_DIR / "outputs.tf")
    for name in (
        "historical_bucket_name",
        "historical_bucket_arn",
        "facts_firehose_stream_name",
        "facts_firehose_stream_arn",
        "geometry_firehose_stream_name",
        "geometry_firehose_stream_arn",
        "transform_lambda_name",
        "transform_lambda_arn",
        "coverage_lambda_name",
        "coverage_lambda_arn",
        "dlq_url",
        "dlq_arn",
        "historical_rule_names",
        "dashboard_name",
        "enable_historical_facts",
    ):
        assert f'output "{name}"' in outputs


def test_dev_down_refuses_data_plane_and_deactivates_when_enabled():
    down = read(DEV_DOWN)
    reset = read(DEV_RESET)
    assert "dev-historical-data" in down
    assert "must not target the persistent historical data plane" in down
    assert "DEACTIVATE" in down
    assert "head-object" in down
    assert "enable_historical_facts is false" in down
    assert "dev-historical-data" in reset
    assert not (REPO_ROOT / "scripts" / "historical-data-down.ps1").exists()


def test_readme_documents_failure_domains_and_persistence_terms():
    readme = read(MODULE_DIR / "README.md")
    for needle in (
        "ACCEPTED_FOR_BOUNDED_DELIVERY_RETRY",
        "DURABLY_PERSISTED",
        "DOMAIN 1",
        "DOMAIN 2",
        "DOMAIN 3A",
        "DOMAIN 3B",
        "GEOMETRY",
        "does **not** contain Domain 1",
        "modules/historical_facts_data",
        "force_destroy = false",
    ):
        assert needle in readme
