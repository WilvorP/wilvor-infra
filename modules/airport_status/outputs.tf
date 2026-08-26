output "airport_status_table_name" {
  description = "Name of the AirportStatus DynamoDB table"
  value       = aws_dynamodb_table.airport_status.name
}

output "airport_status_table_arn" {
  description = "ARN of the AirportStatus DynamoDB table"
  value       = aws_dynamodb_table.airport_status.arn
}

output "materializer_function_name" {
  description = "Name of the AirportStatus materializer Lambda"
  value       = aws_lambda_function.airport_status_materializer.function_name
}

output "materializer_function_arn" {
  description = "ARN of the AirportStatus materializer Lambda"
  value       = aws_lambda_function.airport_status_materializer.arn
}

output "weather_update_rule_name" {
  description = "EventBridge rule that invokes AirportStatus materializer"
  value       = aws_cloudwatch_event_rule.airport_status_weather_updates.name
}

output "airport_status_updated_rule_name" {
  description = "EventBridge rule matching airport.status.updated events"
  value       = aws_cloudwatch_event_rule.airport_status_updated.name
}

output "airport_status_updated_rule_arn" {
  description = "EventBridge rule ARN matching airport.status.updated events"
  value       = aws_cloudwatch_event_rule.airport_status_updated.arn
}