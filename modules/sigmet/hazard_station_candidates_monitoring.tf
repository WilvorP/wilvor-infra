resource "aws_cloudwatch_dashboard" "hazard_station_candidates" {
  dashboard_name = "${var.name_prefix}-hazard-station-candidates"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6

        properties = {
          title  = "Hazard Station Candidates Processor"
          region = var.aws_region
          period = 60
          stat   = "Sum"

          metrics = [
            [
              "AWS/Lambda",
              "Invocations",
              "FunctionName",
              aws_lambda_function.sigmet_hazard_station_candidates_processor.function_name
            ],
            [
              "AWS/Lambda",
              "Errors",
              "FunctionName",
              aws_lambda_function.sigmet_hazard_station_candidates_processor.function_name
            ],
            [
              "AWS/Lambda",
              "Throttles",
              "FunctionName",
              aws_lambda_function.sigmet_hazard_station_candidates_processor.function_name
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
          title  = "HazardStationCandidates DynamoDB"
          region = var.aws_region
          period = 60
          stat   = "Sum"

          metrics = [
            [
              "AWS/DynamoDB",
              "ConsumedReadCapacityUnits",
              "TableName",
              aws_dynamodb_table.hazard_station_candidates.name
            ],
            [
              "AWS/DynamoDB",
              "ConsumedWriteCapacityUnits",
              "TableName",
              aws_dynamodb_table.hazard_station_candidates.name
            ],
            [
              "AWS/DynamoDB",
              "ReadThrottleEvents",
              "TableName",
              aws_dynamodb_table.hazard_station_candidates.name
            ],
            [
              "AWS/DynamoDB",
              "WriteThrottleEvents",
              "TableName",
              aws_dynamodb_table.hazard_station_candidates.name
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
          title  = "Hazard Station Candidate Processor Duration"
          region = var.aws_region
          period = 60
          stat   = "Average"

          metrics = [
            [
              "AWS/Lambda",
              "Duration",
              "FunctionName",
              aws_lambda_function.sigmet_hazard_station_candidates_processor.function_name
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
          title  = "Hazard Station Candidate EventBridge"
          region = var.aws_region
          period = 60
          stat   = "Sum"

          metrics = [
            [
              "AWS/Events",
              "MatchedEvents",
              "RuleName",
              aws_cloudwatch_event_rule.hazard_station_candidates_source_updates.name
            ],
            [
              "AWS/Events",
              "Invocations",
              "RuleName",
              aws_cloudwatch_event_rule.hazard_station_candidates_source_updates.name
            ],
            [
              "AWS/Events",
              "FailedInvocations",
              "RuleName",
              aws_cloudwatch_event_rule.hazard_station_candidates_source_updates.name
            ]
          ]
        }
      }
    ]
  })
}


resource "aws_cloudwatch_metric_alarm" "hazard_station_candidates_processor_errors" {
  alarm_name = "${var.name_prefix}-hazard-station-candidates-processor-errors"

  namespace   = "AWS/Lambda"
  metric_name = "Errors"

  dimensions = {
    FunctionName = aws_lambda_function.sigmet_hazard_station_candidates_processor.function_name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = "HazardStationCandidates processor returned one or more errors."

  tags = var.tags
}


resource "aws_cloudwatch_metric_alarm" "hazard_station_candidates_processor_throttles" {
  alarm_name = "${var.name_prefix}-hazard-station-candidates-processor-throttles"

  namespace   = "AWS/Lambda"
  metric_name = "Throttles"

  dimensions = {
    FunctionName = aws_lambda_function.sigmet_hazard_station_candidates_processor.function_name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = "HazardStationCandidates processor was throttled."

  tags = var.tags
}


resource "aws_cloudwatch_metric_alarm" "hazard_station_candidates_read_throttles" {
  alarm_name = "${var.name_prefix}-hazard-station-candidates-read-throttles"

  namespace   = "AWS/DynamoDB"
  metric_name = "ReadThrottleEvents"

  dimensions = {
    TableName = aws_dynamodb_table.hazard_station_candidates.name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = "HazardStationCandidates DynamoDB read throttling detected."

  tags = var.tags
}


resource "aws_cloudwatch_metric_alarm" "hazard_station_candidates_write_throttles" {
  alarm_name = "${var.name_prefix}-hazard-station-candidates-write-throttles"

  namespace   = "AWS/DynamoDB"
  metric_name = "WriteThrottleEvents"

  dimensions = {
    TableName = aws_dynamodb_table.hazard_station_candidates.name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = "HazardStationCandidates DynamoDB write throttling detected."

  tags = var.tags
}


resource "aws_cloudwatch_metric_alarm" "hazard_station_candidates_eventbridge_failures" {
  alarm_name = "${var.name_prefix}-hazard-station-candidates-eventbridge-failures"

  namespace   = "AWS/Events"
  metric_name = "FailedInvocations"

  dimensions = {
    RuleName = aws_cloudwatch_event_rule.hazard_station_candidates_source_updates.name
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  alarm_description = "EventBridge failed to invoke the HazardStationCandidates processor."

  tags = var.tags
}