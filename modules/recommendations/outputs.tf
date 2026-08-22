output "recommendations_table_name" {
  value = aws_dynamodb_table.recommendations.name
}

output "recommendations_table_arn" {
  value = aws_dynamodb_table.recommendations.arn
}

output "processor_function_name" {
  value = aws_lambda_function.processor.function_name
}

output "processor_function_arn" {
  value = aws_lambda_function.processor.arn
}

output "dashboard_name" {
  value = aws_cloudwatch_dashboard.recommendations.dashboard_name
}
