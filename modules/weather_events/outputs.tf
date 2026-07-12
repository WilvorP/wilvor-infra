output "log_group_name" {
  description = "CloudWatch log group receiving Weather.changed test events"
  value       = aws_cloudwatch_log_group.weather_changed_events.name
}

output "log_group_arn" {
  description = "ARN of the Weather.changed CloudWatch log group"
  value       = aws_cloudwatch_log_group.weather_changed_events.arn
}

output "rule_name" {
  description = "Name of the Weather.changed EventBridge rule"
  value       = aws_cloudwatch_event_rule.weather_changed.name
}

output "rule_arn" {
  description = "ARN of the Weather.changed EventBridge rule"
  value       = aws_cloudwatch_event_rule.weather_changed.arn
}

output "event_bus_name" {
  description = "EventBridge bus used by the Weather.changed rule"
  value       = var.event_bus_name
}

output "dashboard_name" {
  description = "Name of the Weather.changed CloudWatch dashboard"
  value       = aws_cloudwatch_dashboard.weather_events.dashboard_name
}