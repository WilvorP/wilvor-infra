from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.infrastructure


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_DIR = REPO_ROOT / "modules" / "historical_facts"
DEV_MAIN = REPO_ROOT / "envs" / "dev" / "main.tf"
SIGMET_PROCESSOR_TF = REPO_ROOT / "modules" / "sigmet" / "processor.tf"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def module_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(MODULE_DIR.glob("*.tf"))
    )


def test_dev_historical_facts_remain_disabled():
    text = read(DEV_MAIN)
    assert 'source = "../../modules/historical_facts"' in text
    assert "enable_historical_facts = false" in text
    assert "enable_historical_facts = true" not in text


def test_dev_sigmet_uses_historical_geometry_outputs():
    text = read(DEV_MAIN)
    assert (
        "module.historical_facts.geometry_firehose_stream_name" in text
    )
    assert (
        "module.historical_facts.geometry_firehose_stream_arn" in text
    )


def test_no_glue_athena_coverage_or_monitoring():
    text = module_text().lower()
    assert "aws_glue" not in text
    assert "athena" not in text
    assert "aws_cloudwatch_dashboard" not in text
    assert "aws_cloudwatch_metric_alarm" not in text
    assert "coverage_start_utc" not in read(MODULE_DIR / "variables.tf")
    assert not (MODULE_DIR / "monitoring.tf").exists()


def test_historical_bucket_is_private_encrypted_versioned():
    locals_text = read(MODULE_DIR / "locals.tf")
    bucket = read(MODULE_DIR / "bucket.tf")
    assert (
        'bucket_name = "${var.name_prefix}-historical-facts-${var.account_id}-${var.aws_region}"'
        in locals_text
    )
    assert "block_public_acls       = true" in bucket
    assert "block_public_policy     = true" in bucket
    assert "ignore_public_acls      = true" in bucket
    assert "restrict_public_buckets = true" in bucket
    assert 'object_ownership = "BucketOwnerEnforced"' in bucket
    assert 'sse_algorithm = "AES256"' in bucket
    assert 'status = "Enabled"' in bucket
    assert "force_destroy = var.historical_facts_force_destroy" in bucket


def test_lifecycle_retention_and_abort_multipart():
    bucket = read(MODULE_DIR / "bucket.tf")
    variables = read(MODULE_DIR / "variables.tf")
    assert 'prefix = "dataset="' in bucket
    assert "days = var.historical_fact_retention_days" in bucket
    assert 'prefix = "errors/"' in bucket
    assert "days = var.historical_fact_error_retention_days" in bucket
    assert (
        "noncurrent_days = var.historical_fact_noncurrent_version_retention_days"
        in bucket
    )
    assert "days_after_initiation = 1" in bucket
    assert "default = 365" in variables
    assert "default = 30" in variables


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
    assert "enabled = true" in firehose
    assert 'type = "Lambda"' in firehose
    assert "AppendDelimiterToRecord" not in firehose
    assert "data_format_conversion_configuration" not in firehose
    assert "partitionKeyFromLambda:dataset" in locals_text
    assert "partitionKeyFromLambda:year" in locals_text
    assert "partitionKeyFromLambda:month" in locals_text
    assert "partitionKeyFromLambda:day" in locals_text
    assert (
        "errors/result=!{firehose:error-output-type}" in locals_text
    )
    assert "timestamp:yyyy" in locals_text
    assert "default = 128" in variables
    assert "default = 900" in variables


def test_event_time_partition_placeholders_not_arrival_time():
    text = module_text()
    assert "partitionKeyFromLambda:dataset" in text
    assert "approximateArrivalTimestamp" not in text


def test_three_narrow_eventbridge_patterns_target_facts_only():
    events = read(MODULE_DIR / "events.tf")
    assert '"wilvor.encounter"' in events
    assert '"encounter.updated"' in events
    assert '"encounter.resolved"' in events
    assert '"wilvor.risk"' in events
    assert '"risk.updated"' in events
    assert '"risk.resolved"' in events
    assert '"wilvor.weather"' in events
    assert '"hazard.materialized"' in events
    assert "hazard_geometry" not in events
    assert "geometry.materialized" not in events
    assert (
        'arn            = aws_kinesis_firehose_delivery_stream.historical["facts"].arn'
        in events
    )
    assert "maximum_event_age_in_seconds = 86400" in events
    assert "maximum_retry_attempts       = 185" in events
    assert "dead_letter_config" in events
    assert "retry_policy" in events


def test_dlq_sse_and_fourteen_day_retention():
    events = read(MODULE_DIR / "events.tf")
    variables = read(MODULE_DIR / "variables.tf")
    assert "sqs_managed_sse_enabled    = true" in events or (
        "sqs_managed_sse_enabled = true" in events
    )
    assert "message_retention_seconds  = var.dlq_message_retention_seconds" in events or (
        "message_retention_seconds = var.dlq_message_retention_seconds" in events
    )
    assert "default = 1209600" in variables
    assert "events.amazonaws.com" in events
    assert "sqs:SendMessage" in events
    assert "aws:SourceArn" in events


def test_transform_lambda_logs_only_no_dynamodb():
    transform = read(MODULE_DIR / "transform.tf")
    iam = read(MODULE_DIR / "iam.tf")
    assert 'runtime = "python3.12"' in transform
    assert 'handler = "app.lambda_handler"' in transform
    assert "memory_size = 256" in transform
    assert "timeout     = 60" in transform or "timeout = 60" in transform

    transform_policy = iam.split(
        'data "aws_iam_policy_document" "transform" {'
    )[1].split('data "aws_iam_policy_document" "firehose" {')[0]
    assert "logs:CreateLogStream" in transform_policy
    assert "logs:PutLogEvents" in transform_policy
    assert "dynamodb:" not in transform_policy.lower()
    assert "s3:" not in transform_policy.lower()
    assert "firehose:" not in transform_policy.lower()
    assert "events:" not in transform_policy.lower()

    eventbridge_policy = iam.split(
        'data "aws_iam_policy_document" "eventbridge" {'
    )[1]
    assert "firehose:PutRecord" in eventbridge_policy
    assert "firehose:PutRecordBatch" in eventbridge_policy
    assert 'historical["facts"].arn' in eventbridge_policy
    assert "geometry" not in eventbridge_policy


def test_firehose_iam_is_least_privilege_s3():
    iam = read(MODULE_DIR / "iam.tf")
    for action in (
        "s3:AbortMultipartUpload",
        "s3:GetObject",
        "s3:PutObject",
        "s3:GetBucketLocation",
        "s3:ListBucket",
        "s3:ListBucketMultipartUploads",
        "lambda:InvokeFunction",
        "lambda:GetFunctionConfiguration",
    ):
        assert action in iam
    assert "s3:*" not in iam
    assert "firehose:*" not in iam
    assert "events:*" not in iam
    assert "lambda:*" not in iam
    assert "sqs:*" not in iam


def test_sigmet_firehose_permission_is_geometry_put_record_only():
    text = read(SIGMET_PROCESSOR_TF)
    assert "PutHistoricalGeometryFacts" in text
    assert "firehose:PutRecord" in text
    assert "historical_geometry_firehose_stream_arn" in text
    assert "firehose:*" not in text
    geometry_block = text.split("PutHistoricalGeometryFacts")[1].split(
        "aws_iam_role_policy"
    )[0]
    assert "PutRecordBatch" not in geometry_block


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
        "dlq_url",
        "dlq_arn",
        "historical_rule_names",
        "enable_historical_facts",
    ):
        assert f'output "{name}"' in outputs


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
        "2A.1c",
        "does **not** contain Domain 1",
    ):
        assert needle in readme
