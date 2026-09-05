output "api_endpoint" {
  value = aws_apigatewayv2_api.http.api_endpoint
}

output "api_id" {
  value = aws_apigatewayv2_api.http.id
}

output "lambda_function_name" {
  value = aws_lambda_function.ai_copilot.function_name
}

output "lambda_function_arn" {
  value = aws_lambda_function.ai_copilot.arn
}

output "lambda_log_group_name" {
  value = aws_cloudwatch_log_group.lambda.name
}

output "insights_table_name" {
  value = aws_dynamodb_table.insights.name
}

output "insights_table_arn" {
  value = aws_dynamodb_table.insights.arn
}

output "network_summary_schedule_name" {
  value = aws_cloudwatch_event_rule.network_summary.name
}

output "network_summary_schedule_state" {
  value = aws_cloudwatch_event_rule.network_summary.state
}

output "event_rule_names" {
  value = {
    for key, rule in aws_cloudwatch_event_rule.ai_events :
    key => rule.name
  }
}

output "event_dlq_name" {
  value = aws_sqs_queue.event_dlq.name
}

output "alarm_names" {
  value = concat(
    [
      aws_cloudwatch_metric_alarm.lambda_errors.alarm_name,
      aws_cloudwatch_metric_alarm.lambda_throttles.alarm_name,
      aws_cloudwatch_metric_alarm.lambda_duration.alarm_name,
      aws_cloudwatch_metric_alarm.api_5xx.alarm_name,
      aws_cloudwatch_metric_alarm.schedule_failures.alarm_name,
      aws_cloudwatch_metric_alarm.event_dlq_messages.alarm_name,
    ],
    [
      for alarm in aws_cloudwatch_metric_alarm.event_failures :
      alarm.alarm_name
    ]
  )
}
