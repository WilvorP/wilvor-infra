output "enable_historical_analytics" {
  value = var.enable_historical_analytics
}

output "results_bucket_name" {
  value = local.enabled ? aws_s3_bucket.athena_results[0].id : ""
}

output "results_bucket_arn" {
  value = local.enabled ? aws_s3_bucket.athena_results[0].arn : ""
}

output "historical_bucket_name" {
  value = local.historical_bucket_id
}

output "historical_bucket_arn" {
  value = local.historical_bucket_arn
}
