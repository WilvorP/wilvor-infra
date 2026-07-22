locals {
  runway_metric_namespace = "Wilvor/Pipeline"

  runway_loader_dimensions = {
    Environment = var.environment
    Pipeline    = "runway_metadata"
    Component   = "runway_loader"
    Stage       = "load"
  }
}

resource "aws_cloudwatch_metric_alarm" "runway_loader_lambda_errors" {
  alarm_name = "${var.name_prefix}-runway-loader-lambda-errors"

  alarm_description = (
    "Runway metadata loader Lambda returned one or more errors."
  )

  namespace   = "AWS/Lambda"
  metric_name = "Errors"

  dimensions = {
    FunctionName = aws_lambda_function.runway_loader.function_name
  }

  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 300
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  tags = merge(var.tags, {
    Component = "runway-reference-data"
  })
}

resource "aws_cloudwatch_metric_alarm" "runway_loader_lambda_throttles" {
  alarm_name = "${var.name_prefix}-runway-loader-lambda-throttles"

  alarm_description = (
    "Runway metadata loader Lambda was throttled."
  )

  namespace   = "AWS/Lambda"
  metric_name = "Throttles"

  dimensions = {
    FunctionName = aws_lambda_function.runway_loader.function_name
  }

  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 300
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  tags = merge(var.tags, {
    Component = "runway-reference-data"
  })
}

resource "aws_cloudwatch_metric_alarm" "runway_load_failed" {
  alarm_name = "${var.name_prefix}-runway-load-failed"

  alarm_description = (
    "Runway metadata loader emitted the LoadFailed metric."
  )

  namespace   = local.runway_metric_namespace
  metric_name = "LoadFailed"
  dimensions  = local.runway_loader_dimensions

  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 300
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  tags = merge(var.tags, {
    Component = "runway-reference-data"
  })
}

resource "aws_cloudwatch_metric_alarm" "runway_table_write_throttles" {
  alarm_name = "${var.name_prefix}-runway-table-write-throttles"

  alarm_description = (
    "Runway reference DynamoDB writes were throttled."
  )

  namespace   = "AWS/DynamoDB"
  metric_name = "WriteThrottleEvents"

  dimensions = {
    TableName = aws_dynamodb_table.runway_reference.name
  }

  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 300
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  tags = merge(var.tags, {
    Component = "runway-reference-data"
  })
}

resource "aws_cloudwatch_dashboard" "runway_metadata" {
  dashboard_name = "${var.name_prefix}-runway-metadata"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 2

        properties = {
          markdown = "# Wilvor Runway Metadata\nFAA NASR ZIP → Lambda → S3 archive → DynamoDB → EventBridge"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 2
        width  = 12
        height = 6

        properties = {
          title   = "Runway Load Status"
          region  = var.aws_region
          view    = "timeSeries"
          stat    = "Sum"
          period  = 300
          stacked = false

          metrics = [
            [
              local.runway_metric_namespace,
              "LoadSucceeded",
              "Environment",
              var.environment,
              "Pipeline",
              "runway_metadata",
              "Component",
              "runway_loader",
              "Stage",
              "load"
            ],
            [
              local.runway_metric_namespace,
              "LoadFailed",
              "Environment",
              var.environment,
              "Pipeline",
              "runway_metadata",
              "Component",
              "runway_loader",
              "Stage",
              "load"
            ],
            [
              local.runway_metric_namespace,
              "DuplicateCycleSkipped",
              "Environment",
              var.environment,
              "Pipeline",
              "runway_metadata",
              "Component",
              "runway_loader",
              "Stage",
              "load"
            ]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 2
        width  = 12
        height = 6

        properties = {
          title   = "Runway Record Results"
          region  = var.aws_region
          view    = "timeSeries"
          stat    = "Sum"
          period  = 300
          stacked = false

          metrics = [
            [
              local.runway_metric_namespace,
              "RunwaysLoaded",
              "Environment",
              var.environment,
              "Pipeline",
              "runway_metadata",
              "Component",
              "runway_loader",
              "Stage",
              "load"
            ],
            [
              local.runway_metric_namespace,
              "RunwaysNew",
              "Environment",
              var.environment,
              "Pipeline",
              "runway_metadata",
              "Component",
              "runway_loader",
              "Stage",
              "load"
            ],
            [
              local.runway_metric_namespace,
              "RunwaysUpdated",
              "Environment",
              var.environment,
              "Pipeline",
              "runway_metadata",
              "Component",
              "runway_loader",
              "Stage",
              "load"
            ],
            [
              local.runway_metric_namespace,
              "RunwaysDeleted",
              "Environment",
              var.environment,
              "Pipeline",
              "runway_metadata",
              "Component",
              "runway_loader",
              "Stage",
              "load"
            ],
            [
              local.runway_metric_namespace,
              "InvalidRecords",
              "Environment",
              var.environment,
              "Pipeline",
              "runway_metadata",
              "Component",
              "runway_loader",
              "Stage",
              "load"
            ]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 8
        width  = 12
        height = 6

        properties = {
          title   = "Runway Loader Lambda"
          region  = var.aws_region
          view    = "timeSeries"
          stat    = "Sum"
          period  = 300
          stacked = false

          metrics = [
            [
              "AWS/Lambda",
              "Invocations",
              "FunctionName",
              aws_lambda_function.runway_loader.function_name
            ],
            [
              "AWS/Lambda",
              "Errors",
              "FunctionName",
              aws_lambda_function.runway_loader.function_name
            ],
            [
              "AWS/Lambda",
              "Throttles",
              "FunctionName",
              aws_lambda_function.runway_loader.function_name
            ]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 8
        width  = 12
        height = 6

        properties = {
          title   = "Runway DynamoDB"
          region  = var.aws_region
          view    = "timeSeries"
          stat    = "Sum"
          period  = 300
          stacked = false

          metrics = [
            [
              "AWS/DynamoDB",
              "ConsumedReadCapacityUnits",
              "TableName",
              aws_dynamodb_table.runway_reference.name
            ],
            [
              "AWS/DynamoDB",
              "ConsumedWriteCapacityUnits",
              "TableName",
              aws_dynamodb_table.runway_reference.name
            ],
            [
              "AWS/DynamoDB",
              "ReadThrottleEvents",
              "TableName",
              aws_dynamodb_table.runway_reference.name
            ],
            [
              "AWS/DynamoDB",
              "WriteThrottleEvents",
              "TableName",
              aws_dynamodb_table.runway_reference.name
            ]
          ]
        }
      }
    ]
  })
}