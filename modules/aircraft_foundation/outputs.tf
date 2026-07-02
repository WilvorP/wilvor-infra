output "aircraft_raw_stream_name" {
  value = aws_kinesis_stream.aircraft_raw.name
}

output "aircraft_raw_stream_arn" {
  value = aws_kinesis_stream.aircraft_raw.arn
}

output "aircraft_clean_stream_name" {
  value = aws_kinesis_stream.aircraft_clean.name
}

output "aircraft_clean_stream_arn" {
  value = aws_kinesis_stream.aircraft_clean.arn
}

output "aircraft_archive_bucket_name" {
  value = aws_s3_bucket.aircraft_archive.bucket
}

output "aircraft_current_state_table_name" {
  value = aws_dynamodb_table.aircraft_current_state.name
}

output "aircraft_current_state_table_arn" {
  value = aws_dynamodb_table.aircraft_current_state.arn
}

output "opensky_poller_lambda_name" {
  value = aws_lambda_function.opensky_poller.function_name
}

output "opensky_poller_lambda_arn" {
  value = aws_lambda_function.opensky_poller.arn
}

output "opensky_poller_schedule_name" {
  value = aws_cloudwatch_event_rule.opensky_poller_schedule.name
}

output "opensky_poller_schedule_state" {
  value = var.enable_opensky_poller_schedule ? "ENABLED" : "DISABLED"
}

output "opensky_credentials_secret_name" {
  value = aws_secretsmanager_secret.opensky_credentials.name
}

output "opensky_credentials_secret_arn" {
  value = aws_secretsmanager_secret.opensky_credentials.arn
}

output "sigmet_raw_stream_name" {
  value = aws_kinesis_stream.sigmet_raw.name
}

output "sigmet_raw_stream_arn" {
  value = aws_kinesis_stream.sigmet_raw.arn
}

output "sigmet_clean_stream_name" {
  value = aws_kinesis_stream.sigmet_clean.name
}

output "sigmet_clean_stream_arn" {
  value = aws_kinesis_stream.sigmet_clean.arn
}

output "active_hazards_table_name" {
  value = aws_dynamodb_table.active_hazards.name
}

output "hazard_cells_table_name" {
  value = aws_dynamodb_table.hazard_cells.name
}

output "sigmet_poller_function_name" {
  value = aws_lambda_function.sigmet_poller.function_name
}

output "sigmet_poller_function_arn" {
  value = aws_lambda_function.sigmet_poller.arn
}

output "sigmet_poller_schedule_name" {
  value = aws_cloudwatch_event_rule.sigmet_poller_schedule.name
}

output "sigmet_poller_schedule_state" {
  value = var.enable_sigmet_poller_schedule ? "ENABLED" : "DISABLED"
}