output "active_alerts_table_name" {
  value = aws_dynamodb_table.active_alerts.name
}

output "active_alerts_table_arn" {
  value = aws_dynamodb_table.active_alerts.arn
}

output "processor_function_name" {
  value = aws_lambda_function.processor.function_name
}

output "processor_function_arn" {
  value = aws_lambda_function.processor.arn
}

output "dashboard_name" {
  value = aws_cloudwatch_dashboard.active_alerts.dashboard_name
}
