resource "aws_cloudwatch_metric_alarm" "host_errors" {
  alarm_name = "${var.name_prefix}-ai-host-errors"

  namespace   = "AWS/Lambda"
  metric_name = "Errors"

  dimensions = {
    FunctionName = aws_lambda_function.host.function_name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  alarm_description = "Isolated AI host Lambda returned an error. The closed handler does not raise for a refused invoke."

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "host_throttles" {
  alarm_name = "${var.name_prefix}-ai-host-throttles"

  namespace   = "AWS/Lambda"
  metric_name = "Throttles"

  dimensions = {
    FunctionName = aws_lambda_function.host.function_name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  alarm_description = "Something attempted to invoke the closed AI host. Reserved concurrency is 0 while enable_invoke is false, so a throttle is the expected signal."

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "ledger_system_errors" {
  alarm_name = "${var.name_prefix}-ai-ledger-system-errors"

  namespace   = "AWS/DynamoDB"
  metric_name = "SystemErrors"

  dimensions = {
    TableName = aws_dynamodb_table.ledger.name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  alarm_description = "Execution ledger table returned a DynamoDB system error. Conditional check failures are user errors and are not this alarm."

  tags = var.tags
}
