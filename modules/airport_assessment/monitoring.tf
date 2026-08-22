resource "aws_cloudwatch_dashboard" "airport_assessment" {
  dashboard_name = "${var.name_prefix}-airport-assessment"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6

        properties = {
          title   = "Airport Assessment Processor"
          region  = var.aws_region
          view    = "timeSeries"
          stacked = false
          period  = 60
          stat    = "Sum"

          metrics = [
            [
              "AWS/Lambda",
              "Invocations",
              "FunctionName",
              aws_lambda_function.processor.function_name
            ],
            [
              "AWS/Lambda",
              "Errors",
              "FunctionName",
              aws_lambda_function.processor.function_name
            ],
            [
              "AWS/Lambda",
              "Throttles",
              "FunctionName",
              aws_lambda_function.processor.function_name
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
          title   = "AirportAssessment DynamoDB"
          region  = var.aws_region
          view    = "timeSeries"
          stacked = false
          period  = 60
          stat    = "Sum"

          metrics = [
            [
              "AWS/DynamoDB",
              "ConsumedReadCapacityUnits",
              "TableName",
              aws_dynamodb_table.airport_assessment.name
            ],
            [
              "AWS/DynamoDB",
              "ConsumedWriteCapacityUnits",
              "TableName",
              aws_dynamodb_table.airport_assessment.name
            ],
            [
              "AWS/DynamoDB",
              "ReadThrottleEvents",
              "TableName",
              aws_dynamodb_table.airport_assessment.name
            ],
            [
              "AWS/DynamoDB",
              "WriteThrottleEvents",
              "TableName",
              aws_dynamodb_table.airport_assessment.name
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
          title   = "Airport Assessment Duration"
          region  = var.aws_region
          view    = "timeSeries"
          stacked = false
          period  = 60
          stat    = "Average"

          metrics = [
            [
              "AWS/Lambda",
              "Duration",
              "FunctionName",
              aws_lambda_function.processor.function_name
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
          title   = "Risk → Airport Assessment EventBridge"
          region  = var.aws_region
          view    = "timeSeries"
          stacked = false
          period  = 60
          stat    = "Sum"

          metrics = [
            [
              "AWS/Events",
              "MatchedEvents",
              "RuleName",
              aws_cloudwatch_event_rule.risk_updated.name
            ],
            [
              "AWS/Events",
              "Invocations",
              "RuleName",
              aws_cloudwatch_event_rule.risk_updated.name
            ],
            [
              "AWS/Events",
              "FailedInvocations",
              "RuleName",
              aws_cloudwatch_event_rule.risk_updated.name
            ]
          ]
        }
      }
    ]
  })
}


resource "aws_cloudwatch_metric_alarm" "processor_errors" {
  alarm_name = (
    "${var.name_prefix}-airport-assessment-processor-errors"
  )

  namespace   = "AWS/Lambda"
  metric_name = "Errors"

  dimensions = {
    FunctionName = aws_lambda_function.processor.function_name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = (
    "Airport Assessment Processor returned one or more errors."
  )

  tags = var.tags
}


resource "aws_cloudwatch_metric_alarm" "processor_throttles" {
  alarm_name = (
    "${var.name_prefix}-airport-assessment-processor-throttles"
  )

  namespace   = "AWS/Lambda"
  metric_name = "Throttles"

  dimensions = {
    FunctionName = aws_lambda_function.processor.function_name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = (
    "Airport Assessment Processor was throttled."
  )

  tags = var.tags
}


resource "aws_cloudwatch_metric_alarm" "processor_duration" {
  alarm_name = (
    "${var.name_prefix}-airport-assessment-processor-duration-high"
  )

  namespace   = "AWS/Lambda"
  metric_name = "Duration"

  dimensions = {
    FunctionName = aws_lambda_function.processor.function_name
  }

  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 45000
  comparison_operator = "GreaterThanThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = (
    "Airport Assessment Processor average duration exceeded 45 seconds."
  )

  tags = var.tags
}


resource "aws_cloudwatch_metric_alarm" "read_throttles" {
  alarm_name = (
    "${var.name_prefix}-airport-assessment-read-throttles"
  )

  namespace   = "AWS/DynamoDB"
  metric_name = "ReadThrottleEvents"

  dimensions = {
    TableName = aws_dynamodb_table.airport_assessment.name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = (
    "AirportAssessment DynamoDB read throttling detected."
  )

  tags = var.tags
}


resource "aws_cloudwatch_metric_alarm" "write_throttles" {
  alarm_name = (
    "${var.name_prefix}-airport-assessment-write-throttles"
  )

  namespace   = "AWS/DynamoDB"
  metric_name = "WriteThrottleEvents"

  dimensions = {
    TableName = aws_dynamodb_table.airport_assessment.name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = (
    "AirportAssessment DynamoDB write throttling detected."
  )

  tags = var.tags
}


resource "aws_cloudwatch_metric_alarm" "eventbridge_failed_invocations" {
  alarm_name = (
    "${var.name_prefix}-airport-assessment-eventbridge-failed-invocations"
  )

  namespace   = "AWS/Events"
  metric_name = "FailedInvocations"

  dimensions = {
    RuleName = aws_cloudwatch_event_rule.risk_updated.name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = (
    "Risk events failed to invoke Airport Assessment Processor."
  )

  tags = var.tags
}