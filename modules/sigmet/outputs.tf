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

output "dashboard_name" {
  description = "Name of the SIGMET CloudWatch dashboard"
  value       = aws_cloudwatch_dashboard.sigmet_pipeline.dashboard_name
}

output "hazard_coordinates_table_name" {
  value = aws_dynamodb_table.hazard_coordinates.name
}

output "hazard_coordinates_table_arn" {
  value = aws_dynamodb_table.hazard_coordinates.arn
}

output "hazard_cells_table_arn" {
  value = aws_dynamodb_table.hazard_cells.arn
}

output "impact_cells_table_name" {
  value = aws_dynamodb_table.impact_cells.name
}

output "impact_cells_table_arn" {
  value = aws_dynamodb_table.impact_cells.arn
}

output "hazard_coordinates_processor_function_name" {
  value = aws_lambda_function.sigmet_hazard_coordinates_processor.function_name
}

output "hazard_coordinates_processor_function_arn" {
  value = aws_lambda_function.sigmet_hazard_coordinates_processor.arn
}


output "hazard_station_candidates_table_name" {
  value = aws_dynamodb_table.hazard_station_candidates.name
}

output "hazard_station_candidates_table_arn" {
  value = aws_dynamodb_table.hazard_station_candidates.arn
}

output "active_hazards_table_arn" {
  value = aws_dynamodb_table.active_hazards.arn
}

output "hazard_station_candidates_processor_function_name" {
  value = aws_lambda_function.sigmet_hazard_station_candidates_processor.function_name
}

output "hazard_station_candidates_processor_function_arn" {
  value = aws_lambda_function.sigmet_hazard_station_candidates_processor.arn
}
