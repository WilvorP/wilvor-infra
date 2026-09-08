locals {
  enabled = var.enable_historical_facts

  bucket_name = "${var.name_prefix}-historical-facts-${var.account_id}-${var.aws_region}"

  facts_stream_name    = "${var.name_prefix}-historical-facts"
  geometry_stream_name = "${var.name_prefix}-historical-geometry"

  transform_function_name = "${var.name_prefix}-historical-facts-transform"

  success_prefix = "dataset=!{partitionKeyFromLambda:dataset}/year=!{partitionKeyFromLambda:year}/month=!{partitionKeyFromLambda:month}/day=!{partitionKeyFromLambda:day}/"
  error_prefix   = "errors/result=!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/"

  common_tags = merge(var.tags, {
    Component = "historical-facts"
    DataType  = "historical-facts"
  })
}
