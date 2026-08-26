resource "aws_cloudwatch_dashboard" "recommendations" {
  dashboard_name = "${var.name_prefix}-recommendations"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6,
        properties = {
          title = "Recommendation Processor", region = var.aws_region, period = 60, stat = "Sum",
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.processor.function_name],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.processor.function_name],
            ["AWS/Lambda", "Throttles", "FunctionName", aws_lambda_function.processor.function_name]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6,
        properties = {
          title = "Recommendations DynamoDB", region = var.aws_region, period = 60, stat = "Sum",
          metrics = [
            ["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", aws_dynamodb_table.recommendations.name],
            ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", aws_dynamodb_table.recommendations.name],
            ["AWS/DynamoDB", "ReadThrottleEvents", "TableName", aws_dynamodb_table.recommendations.name],
            ["AWS/DynamoDB", "WriteThrottleEvents", "TableName", aws_dynamodb_table.recommendations.name]
          ]
        }
      }
    ]
  })
}

resource "aws_cloudwatch_metric_alarm" "processor_errors" {
  alarm_name          = "${var.name_prefix}-recommendation-processor-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { FunctionName = aws_lambda_function.processor.function_name }
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "write_throttles" {
  alarm_name          = "${var.name_prefix}-recommendations-write-throttles"
  namespace           = "AWS/DynamoDB"
  metric_name         = "WriteThrottleEvents"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { TableName = aws_dynamodb_table.recommendations.name }
  tags                = var.tags
}
