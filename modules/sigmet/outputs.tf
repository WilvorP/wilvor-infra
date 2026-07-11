output "archive_bucket_name" {
  value = aws_s3_bucket.sigmet_archive.bucket
}

output "raw_stream_name" {
  value = aws_kinesis_stream.sigmet_raw.name
}

output "raw_stream_arn" {
  value = aws_kinesis_stream.sigmet_raw.arn
}

output "active_hazards_table_name" {
  value = aws_dynamodb_table.active_hazards.name
}

output "hazard_cells_table_name" {
  value = aws_dynamodb_table.hazard_cells.name
}

output "poller_function_name" {
  value = aws_lambda_function.sigmet_poller.function_name
}

output "processor_function_name" {
  value = aws_lambda_function.sigmet_processor.function_name
}

output "processor_function_arn" {
  value = aws_lambda_function.sigmet_processor.arn
}

output "schedule_name" {
  value = aws_cloudwatch_event_rule.sigmet_poller_schedule.name
}

output "schedule_state" {
  value = var.enable_sigmet_poller_schedule ? "ENABLED" : "DISABLED"
}