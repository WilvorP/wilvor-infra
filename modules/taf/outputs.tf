output "archive_bucket_name" {
  value = aws_s3_bucket.taf_archive.bucket
}

output "raw_stream_name" {
  value = aws_kinesis_stream.taf_raw.name
}

output "raw_stream_arn" {
  value = aws_kinesis_stream.taf_raw.arn
}

output "taf_latest_table_name" {
  value = aws_dynamodb_table.taf_latest.name
}

output "poller_function_name" {
  value = aws_lambda_function.taf_poller.function_name
}

output "processor_function_name" {
  value = aws_lambda_function.taf_processor.function_name
}

output "processor_function_arn" {
  value = aws_lambda_function.taf_processor.arn
}

output "schedule_name" {
  value = aws_cloudwatch_event_rule.taf_poller_schedule.name
}

output "schedule_state" {
  value = var.enable_taf_poller_schedule ? "ENABLED" : "DISABLED"
}

output "dashboard_name" {
  description = "Name of the TAF CloudWatch dashboard"
  value       = aws_cloudwatch_dashboard.taf_pipeline.dashboard_name
}

output "taf_forecast_periods_table_name" {
  value = aws_dynamodb_table.taf_forecast_periods.name
}

output "taf_forecast_periods_table_arn" {
  value = aws_dynamodb_table.taf_forecast_periods.arn
}