resource "aws_cloudwatch_dashboard" "risk_pipeline" {
  dashboard_name = "${var.name_prefix}-risk-pipeline"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6

        properties = {
          title   = "Risk Processor Lambda"
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          period  = 60
          stat    = "Sum"

          metrics = [
            [
              "AWS/Lambda",
              "Invocations",
              "FunctionName",
              aws_lambda_function.risk_processor.function_name
            ],
            [
              "AWS/Lambda",
              "Errors",
              "FunctionName",
              aws_lambda_function.risk_processor.function_name
            ],
            [
              "AWS/Lambda",
              "Throttles",
              "FunctionName",
              aws_lambda_function.risk_processor.function_name
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
          title   = "Risk Evaluations"
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          period  = 60
          stat    = "Sum"

          metrics = [
            [
              "Wilvor/Pipeline",
              "RiskEvaluations",
              "Environment",
              lookup(var.tags, "Environment", "dev"),
              "Pipeline",
              "risk",
              "Component",
              "risk_processor",
              "Stage",
              "scoring"
            ],
            [
              "Wilvor/Pipeline",
              "RiskResultsWritten",
              "Environment",
              lookup(var.tags, "Environment", "dev"),
              "Pipeline",
              "risk",
              "Component",
              "risk_processor",
              "Stage",
              "scoring"
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
          title   = "RiskResults DynamoDB"
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          period  = 60
          stat    = "Sum"

          metrics = [
            [
              "AWS/DynamoDB",
              "ConsumedReadCapacityUnits",
              "TableName",
              aws_dynamodb_table.risk_results.name
            ],
            [
              "AWS/DynamoDB",
              "ConsumedWriteCapacityUnits",
              "TableName",
              aws_dynamodb_table.risk_results.name
            ],
            [
              "AWS/DynamoDB",
              "ReadThrottleEvents",
              "TableName",
              aws_dynamodb_table.risk_results.name
            ],
            [
              "AWS/DynamoDB",
              "WriteThrottleEvents",
              "TableName",
              aws_dynamodb_table.risk_results.name
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
          title   = "Risk Processor Duration"
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          period  = 60
          stat    = "Average"

          metrics = [
            [
              "AWS/Lambda",
              "Duration",
              "FunctionName",
              aws_lambda_function.risk_processor.function_name
            ]
          ]
        }
      }
    ]
  })
}


resource "aws_cloudwatch_metric_alarm" "risk_processor_errors" {
  alarm_name = "${var.name_prefix}-risk-processor-errors"

  namespace   = "AWS/Lambda"
  metric_name = "Errors"

  dimensions = {
    FunctionName = aws_lambda_function.risk_processor.function_name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = "Risk Processor Lambda returned one or more errors."

  tags = var.tags
}


resource "aws_cloudwatch_metric_alarm" "risk_processor_throttles" {
  alarm_name = "${var.name_prefix}-risk-processor-throttles"

  namespace   = "AWS/Lambda"
  metric_name = "Throttles"

  dimensions = {
    FunctionName = aws_lambda_function.risk_processor.function_name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = "Risk Processor Lambda was throttled."

  tags = var.tags
}


resource "aws_cloudwatch_metric_alarm" "risk_processor_duration" {
  alarm_name = "${var.name_prefix}-risk-processor-duration-high"

  namespace   = "AWS/Lambda"
  metric_name = "Duration"

  dimensions = {
    FunctionName = aws_lambda_function.risk_processor.function_name
  }

  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 20000
  comparison_operator = "GreaterThanThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = "Risk Processor average duration exceeded 20 seconds."

  tags = var.tags
}


resource "aws_cloudwatch_metric_alarm" "risk_results_read_throttles" {
  alarm_name = "${var.name_prefix}-risk-results-read-throttles"

  namespace   = "AWS/DynamoDB"
  metric_name = "ReadThrottleEvents"

  dimensions = {
    TableName = aws_dynamodb_table.risk_results.name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = "RiskResults DynamoDB read throttling detected."

  tags = var.tags
}


resource "aws_cloudwatch_metric_alarm" "risk_results_write_throttles" {
  alarm_name = "${var.name_prefix}-risk-results-write-throttles"

  namespace   = "AWS/DynamoDB"
  metric_name = "WriteThrottleEvents"

  dimensions = {
    TableName = aws_dynamodb_table.risk_results.name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = "RiskResults DynamoDB write throttling detected."

  tags = var.tags
}


resource "aws_cloudwatch_metric_alarm" "risk_eventbridge_failed_invocations" {
  alarm_name = "${var.name_prefix}-risk-eventbridge-failed-invocations"

  namespace   = "AWS/Events"
  metric_name = "FailedInvocations"

  dimensions = {
    RuleName = aws_cloudwatch_event_rule.encounter_changes.name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = "Encounter events failed to invoke the Risk Processor."

  tags = var.tags
}