output "archive_bucket_name" {
  description = "StationReference archive S3 bucket name"
  value       = aws_s3_bucket.station_reference_archive.bucket
}

output "archive_bucket_arn" {
  description = "StationReference archive S3 bucket ARN"
  value       = aws_s3_bucket.station_reference_archive.arn
}

output "station_reference_table_name" {
  description = "StationReference DynamoDB table name"
  value       = aws_dynamodb_table.station_reference.name
}

output "station_reference_table_arn" {
  description = "StationReference DynamoDB table ARN"
  value       = aws_dynamodb_table.station_reference.arn
}

output "loader_function_name" {
  description = "StationReference loader Lambda name"
  value       = aws_lambda_function.station_reference_loader.function_name
}

output "loader_function_arn" {
  description = "StationReference loader Lambda ARN"
  value       = aws_lambda_function.station_reference_loader.arn
}

output "loader_log_group_name" {
  description = "StationReference loader CloudWatch log group name"
  value       = aws_cloudwatch_log_group.station_reference_loader.name
}

output "schedule_name" {
  description = "StationReference loader EventBridge schedule name"
  value       = aws_cloudwatch_event_rule.station_reference_loader_schedule.name
}

output "schedule_state" {
  description = "StationReference loader schedule state"
  value = (
    var.enable_station_reference_loader_schedule
    ? "ENABLED"
    : "DISABLED"
  )
}
