locals {
  enabled = var.enable_historical_analytics

  historical_bucket_name = "${var.name_prefix}-historical-facts-${var.account_id}-${var.aws_region}"
  results_bucket_name    = "${var.name_prefix}-historical-athena-results-${var.account_id}-${var.aws_region}"

  historical_bucket_id  = local.enabled ? data.aws_s3_bucket.historical_facts[0].id : ""
  historical_bucket_arn = local.enabled ? data.aws_s3_bucket.historical_facts[0].arn : ""

  common_tags = merge(var.tags, {
    Component = "historical-analytics"
    DataType  = "historical-analytics-derived"
  })
}

data "aws_s3_bucket" "historical_facts" {
  count = local.enabled ? 1 : 0

  bucket = local.historical_bucket_name
}
