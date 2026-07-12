output "archive_bucket_name" {
  description = "Name of the METAR archive bucket"
  value       = aws_s3_bucket.metar_archive.bucket
}

output "archive_bucket_arn" {
  description = "ARN of the METAR archive bucket"
  value       = aws_s3_bucket.metar_archive.arn
}

output "raw_stream_name" {
  description = "Name of the raw METAR Kinesis stream"
  value       = aws_kinesis_stream.metar_raw.name
}

output "raw_stream_arn" {
  description = "ARN of the raw METAR Kinesis stream"
  value       = aws_kinesis_stream.metar_raw.arn
}

output "metar_latest_table_name" {
  description = "Name of the MetarLatest DynamoDB table"
  value       = aws_dynamodb_table.metar_latest.name
}

output "metar_latest_table_arn" {
  description = "ARN of the MetarLatest DynamoDB table"
  value       = aws_dynamodb_table.metar_latest.arn
}

output "poller_function_name" {
  description = "Name of the METAR poller Lambda"
  value       = aws_lambda_function.metar_poller.function_name
}

output "poller_function_arn" {
  description = "ARN of the METAR poller Lambda"
  value       = aws_lambda_function.metar_poller.arn
}

output "schedule_name" {
  description = "Name of the METAR polling schedule"
  value       = aws_cloudwatch_event_rule.metar_poller_schedule.name
}

output "schedule_state" {
  description = "Whether the METAR polling schedule is enabled"
  value       = var.enable_metar_poller_schedule ? "ENABLED" : "DISABLED"
}

output "dashboard_name" {
  description = "Name of the METAR CloudWatch dashboard"
  value       = aws_cloudwatch_dashboard.metar_pipeline.dashboard_name
}