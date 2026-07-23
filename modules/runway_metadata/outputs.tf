output "archive_bucket_name" {
  description = "Runway archive S3 bucket name"
  value       = aws_s3_bucket.runway_archive.bucket
}

output "archive_bucket_arn" {
  description = "Runway archive S3 bucket ARN"
  value       = aws_s3_bucket.runway_archive.arn
}

output "runway_reference_table_name" {
  description = "Runway reference DynamoDB table name"
  value       = aws_dynamodb_table.runway_reference.name
}

output "runway_reference_table_arn" {
  description = "Runway reference DynamoDB table ARN"
  value       = aws_dynamodb_table.runway_reference.arn
}

output "loader_function_name" {
  description = "Runway loader Lambda name"
  value       = aws_lambda_function.runway_loader.function_name
}

output "loader_function_arn" {
  description = "Runway loader Lambda ARN"
  value       = aws_lambda_function.runway_loader.arn
}

output "loader_log_group_name" {
  description = "Runway loader CloudWatch log group name"
  value       = aws_cloudwatch_log_group.runway_loader.name
}

output "schedule_name" {
  description = "Runway loader EventBridge schedule name"
  value       = aws_cloudwatch_event_rule.runway_loader_schedule.name
}

output "schedule_state" {
  description = "Runway loader schedule state"

  value = (
    var.enable_runway_loader_schedule
    ? "ENABLED"
    : "DISABLED"
  )
}

output "dashboard_name" {
  description = "Runway metadata CloudWatch dashboard name"
  value       = aws_cloudwatch_dashboard.runway_metadata.dashboard_name
}