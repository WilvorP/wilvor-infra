resource "aws_cloudwatch_dashboard" "airport_status" {
  dashboard_name = "${var.name_prefix}-airport-status"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6

        properties = {
          title  = "Airport Status Materializer"
          region = var.aws_region
          period = 60
          stat   = "Sum"

          metrics = [
            [
              "AWS/Lambda",
              "Invocations",
              "FunctionName",
              aws_lambda_function.airport_status_materializer.function_name
            ],
            [
              "AWS/Lambda",
              "Errors",
              "FunctionName",
              aws_lambda_function.airport_status_materializer.function_name
            ],
            [
              "AWS/Lambda",
              "Throttles",
              "FunctionName",
              aws_lambda_function.airport_status_materializer.function_name
            ]
          ]
        }
      },

      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6

        properties = {
          title  = "AirportStatus DynamoDB"
          region = var.aws_region
          period = 60
          stat   = "Sum"

          metrics = [
            [
              "AWS/DynamoDB",
              "ConsumedReadCapacityUnits",
              "TableName",
              aws_dynamodb_table.airport_status.name
            ],
            [
              "AWS/DynamoDB",
              "ConsumedWriteCapacityUnits",
              "TableName",
              aws_dynamodb_table.airport_status.name
            ],
            [
              "AWS/DynamoDB",
              "ReadThrottleEvents",
              "TableName",
              aws_dynamodb_table.airport_status.name
            ],
            [
              "AWS/DynamoDB",
              "WriteThrottleEvents",
              "TableName",
              aws_dynamodb_table.airport_status.name
            ]
          ]
        }
      },

      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6

        properties = {
          title  = "Airport Status Materializer Duration"
          region = var.aws_region
          period = 60
          stat   = "Average"

          metrics = [
            [
              "AWS/Lambda",
              "Duration",
              "FunctionName",
              aws_lambda_function.airport_status_materializer.function_name
            ]
          ]
        }
      },

      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6

        properties = {
          title  = "Weather → AirportStatus EventBridge"
          region = var.aws_region
          period = 60
          stat   = "Sum"

          metrics = [
            [
              "AWS/Events",
              "MatchedEvents",
              "RuleName",
              aws_cloudwatch_event_rule.airport_status_weather_updates.name
            ],
            [
              "AWS/Events",
              "Invocations",
              "RuleName",
              aws_cloudwatch_event_rule.airport_status_weather_updates.name
            ],
            [
              "AWS/Events",
              "FailedInvocations",
              "RuleName",
              aws_cloudwatch_event_rule.airport_status_weather_updates.name
            ]
          ]
        }
      }
    ]
  })
}


resource "aws_cloudwatch_metric_alarm" "airport_status_materializer_errors" {
  alarm_name = "${var.name_prefix}-airport-status-materializer-errors"

  namespace   = "AWS/Lambda"
  metric_name = "Errors"

  dimensions = {
    FunctionName = aws_lambda_function.airport_status_materializer.function_name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = "AirportStatus materializer returned one or more errors."

  tags = var.tags
}


resource "aws_cloudwatch_metric_alarm" "airport_status_materializer_throttles" {
  alarm_name = "${var.name_prefix}-airport-status-materializer-throttles"

  namespace   = "AWS/Lambda"
  metric_name = "Throttles"

  dimensions = {
    FunctionName = aws_lambda_function.airport_status_materializer.function_name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = "AirportStatus materializer was throttled."

  tags = var.tags
}


resource "aws_cloudwatch_metric_alarm" "airport_status_read_throttles" {
  alarm_name = "${var.name_prefix}-airport-status-read-throttles"

  namespace   = "AWS/DynamoDB"
  metric_name = "ReadThrottleEvents"

  dimensions = {
    TableName = aws_dynamodb_table.airport_status.name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = "AirportStatus DynamoDB read throttling detected."

  tags = var.tags
}


resource "aws_cloudwatch_metric_alarm" "airport_status_write_throttles" {
  alarm_name = "${var.name_prefix}-airport-status-write-throttles"

  namespace   = "AWS/DynamoDB"
  metric_name = "WriteThrottleEvents"

  dimensions = {
    TableName = aws_dynamodb_table.airport_status.name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = "AirportStatus DynamoDB write throttling detected."

  tags = var.tags
}


resource "aws_cloudwatch_metric_alarm" "airport_status_eventbridge_failures" {
  alarm_name = "${var.name_prefix}-airport-status-eventbridge-failures"

  namespace   = "AWS/Events"
  metric_name = "FailedInvocations"

  dimensions = {
    RuleName = aws_cloudwatch_event_rule.airport_status_weather_updates.name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = "EventBridge failed to invoke the AirportStatus materializer."

  tags = var.tags
}