locals {
  enabled = var.enable_historical_facts

  bucket_name = "${var.name_prefix}-historical-facts-${var.account_id}-${var.aws_region}"

  facts_stream_name    = "${var.name_prefix}-historical-facts"
  geometry_stream_name = "${var.name_prefix}-historical-geometry"

  transform_function_name    = "${var.name_prefix}-historical-facts-transform"
  coverage_function_name     = "${var.name_prefix}-historical-facts-coverage"
  dlq_consumer_function_name = "${var.name_prefix}-historical-facts-dlq-consumer"
  dashboard_name             = "${var.name_prefix}-historical-facts"
  historical_environment     = replace(var.name_prefix, "wilvor-", "")
  wilvor_metric_namespace    = "Wilvor/Pipeline"

  success_prefix = "dataset=!{partitionKeyFromLambda:dataset}/year=!{partitionKeyFromLambda:year}/month=!{partitionKeyFromLambda:month}/day=!{partitionKeyFromLambda:day}/"
  error_prefix   = "errors/result=!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/"

  historical_bucket_id  = local.enabled ? data.aws_s3_bucket.historical_facts[0].id : ""
  historical_bucket_arn = local.enabled ? data.aws_s3_bucket.historical_facts[0].arn : ""

  common_tags = merge(var.tags, {
    Component = "historical-facts"
    DataType  = "historical-facts"
  })
}

data "aws_s3_bucket" "historical_facts" {
  count = local.enabled ? 1 : 0

  bucket = local.bucket_name
}
