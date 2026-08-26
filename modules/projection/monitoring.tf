resource "aws_cloudwatch_metric_alarm" "projection_processor_errors" {
  alarm_name = (
    "${var.name_prefix}-projection-processor-errors"
  )

  alarm_description = (
    "Projection Processor Lambda had runtime errors."
  )

  namespace   = "AWS/Lambda"
  metric_name = "Errors"

  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"

  treat_missing_data = "notBreaching"

  dimensions = {
    FunctionName = (
      aws_lambda_function
      .projection_processor
      .function_name
    )
  }
}

resource "aws_cloudwatch_metric_alarm" "projection_processor_throttles" {
  alarm_name = (
    "${var.name_prefix}-projection-processor-throttles"
  )

  alarm_description = (
    "Projection Processor Lambda was throttled."
  )

  namespace   = "AWS/Lambda"
  metric_name = "Throttles"

  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"

  treat_missing_data = "notBreaching"

  dimensions = {
    FunctionName = (
      aws_lambda_function
      .projection_processor
      .function_name
    )
  }
}

resource "aws_cloudwatch_metric_alarm" "projection_points_write_throttles" {
  alarm_name = (
    "${var.name_prefix}-projection-points-write-throttles"
  )

  namespace   = "AWS/DynamoDB"
  metric_name = "WriteThrottleEvents"

  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"

  treat_missing_data = "notBreaching"

  dimensions = {
    TableName = (
      aws_dynamodb_table
      .aircraft_projection_points
      .name
    )
  }
}

resource "aws_cloudwatch_metric_alarm" "projection_points_read_throttles" {
  alarm_name = (
    "${var.name_prefix}-projection-points-read-throttles"
  )

  namespace   = "AWS/DynamoDB"
  metric_name = "ReadThrottleEvents"

  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"

  treat_missing_data = "notBreaching"

  dimensions = {
    TableName = (
      aws_dynamodb_table
      .aircraft_projection_points
      .name
    )
  }
}

resource "aws_cloudwatch_metric_alarm" "projection_points_gsi_write_throttles" {
  alarm_name = (
    "${var.name_prefix}-projection-points-gsi-write-throttles"
  )

  alarm_description = (
    "AircraftProjectionPoints GSI had DynamoDB write throttle events."
  )

  namespace   = "AWS/DynamoDB"
  metric_name = "WriteThrottleEvents"

  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"

  treat_missing_data = "notBreaching"

  dimensions = {
    TableName = (
      aws_dynamodb_table
      .aircraft_projection_points
      .name
    )

    GlobalSecondaryIndexName = (
      "aircraft_id-projected_time_epoch-index"
    )
  }
}

resource "aws_cloudwatch_metric_alarm" "projection_points_gsi_read_throttles" {
  alarm_name = (
    "${var.name_prefix}-projection-points-gsi-read-throttles"
  )

  alarm_description = (
    "AircraftProjectionPoints GSI had DynamoDB read throttle events."
  )

  namespace   = "AWS/DynamoDB"
  metric_name = "ReadThrottleEvents"

  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"

  treat_missing_data = "notBreaching"

  dimensions = {
    TableName = (
      aws_dynamodb_table
      .aircraft_projection_points
      .name
    )

    GlobalSecondaryIndexName = (
      "aircraft_id-projected_time_epoch-index"
    )
  }
}

locals {
  projection_dynamodb_alarm_targets = {
    aircraft_projection = {
      table_name = aws_dynamodb_table.aircraft_projection.name
      index_name = null
      label      = "projection"
    }

    aircraft_projection_aircraft_time_gsi = {
      table_name = aws_dynamodb_table.aircraft_projection.name
      index_name = "aircraft_id-generated_at_epoch-index"
      label      = "projection-aircraft-time-gsi"
    }

    aircraft_projection_status_validity_gsi = {
      table_name = aws_dynamodb_table.aircraft_projection.name
      index_name = "projection_status-valid_until_epoch-index"
      label      = "projection-status-validity-gsi"
    }

    aircraft_projection_cells = {
      table_name = aws_dynamodb_table.aircraft_projection_cells.name
      index_name = null
      label      = "projection-cells"
    }

    aircraft_projection_cells_h3_gsi = {
      table_name = aws_dynamodb_table.aircraft_projection_cells.name
      index_name = "h3_cell-projection_id-index"
      label      = "projection-cells-h3-gsi"
    }
  }
}


resource "aws_cloudwatch_metric_alarm" "projection_dynamodb_read_throttles" {
  for_each = local.projection_dynamodb_alarm_targets

  alarm_name = "${var.name_prefix}-${each.value.label}-read-throttles"

  alarm_description = "DynamoDB read throttles for ${each.value.label}."

  namespace   = "AWS/DynamoDB"
  metric_name = "ReadThrottleEvents"

  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"

  treat_missing_data = "notBreaching"

  dimensions = merge(
    {
      TableName = each.value.table_name
    },
    each.value.index_name != null
    ? {
      GlobalSecondaryIndexName = each.value.index_name
    }
    : {}
  )
}


resource "aws_cloudwatch_metric_alarm" "projection_dynamodb_write_throttles" {
  for_each = local.projection_dynamodb_alarm_targets

  alarm_name = "${var.name_prefix}-${each.value.label}-write-throttles"

  alarm_description = "DynamoDB write throttles for ${each.value.label}."

  namespace   = "AWS/DynamoDB"
  metric_name = "WriteThrottleEvents"

  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"

  treat_missing_data = "notBreaching"

  dimensions = merge(
    {
      TableName = each.value.table_name
    },
    each.value.index_name != null
    ? {
      GlobalSecondaryIndexName = each.value.index_name
    }
    : {}
  )
}

resource "aws_cloudwatch_dashboard" "projection_pipeline" {
  dashboard_name = "${var.name_prefix}-projection-pipeline"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6

        properties = {
          title  = "Projection Processor Lambda"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 60

          metrics = [
            [
              "AWS/Lambda",
              "Invocations",
              "FunctionName",
              aws_lambda_function.projection_processor.function_name
            ],
            [
              ".",
              "Errors",
              ".",
              "."
            ],
            [
              ".",
              "Throttles",
              ".",
              "."
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
          title  = "Projection Eligibility"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 60

          metrics = [
            [
              "Wilvor/Pipeline",
              "EventsReceived",
              "Environment",
              lookup(var.tags, "Environment", "dev"),
              "Pipeline",
              "projection",
              "Component",
              "projection_processor",
              "Stage",
              "eligibility"
            ],
            [
              ".",
              "EligibleAircraft",
              ".",
              ".",
              ".",
              ".",
              ".",
              ".",
              ".",
              "."
            ],
            [
              ".",
              "IneligibleAircraft",
              ".",
              ".",
              ".",
              ".",
              ".",
              ".",
              ".",
              "."
            ],
            [
              ".",
              "StaleEventsSkipped",
              ".",
              ".",
              ".",
              ".",
              ".",
              ".",
              ".",
              "."
            ],
            [
              ".",
              "EligibilityFailures",
              ".",
              ".",
              ".",
              ".",
              ".",
              ".",
              ".",
              "."
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
          title  = "AircraftProjectionPoints Capacity"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 60

          metrics = [
            [
              "AWS/DynamoDB",
              "ConsumedReadCapacityUnits",
              "TableName",
              aws_dynamodb_table.aircraft_projection_points.name
            ],
            [
              ".",
              "ConsumedWriteCapacityUnits",
              ".",
              "."
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
          title  = "AircraftProjectionPoints GSI Capacity"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 60

          metrics = [
            [
              "AWS/DynamoDB",
              "ConsumedReadCapacityUnits",
              "TableName",
              aws_dynamodb_table.aircraft_projection_points.name,
              "GlobalSecondaryIndexName",
              "aircraft_id-projected_time_epoch-index"
            ],
            [
              ".",
              "ConsumedWriteCapacityUnits",
              ".",
              ".",
              ".",
              "."
            ]
          ]
        }
      },

      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 12
        height = 6

        properties = {
          title  = "AircraftProjectionPoints Throttles"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 60

          metrics = [
            [
              "AWS/DynamoDB",
              "ReadThrottleEvents",
              "TableName",
              aws_dynamodb_table.aircraft_projection_points.name
            ],
            [
              ".",
              "WriteThrottleEvents",
              ".",
              "."
            ]
          ]
        }
      },

      {
        type   = "metric"
        x      = 12
        y      = 12
        width  = 12
        height = 6

        properties = {
          title  = "AircraftProjectionPoints GSI Throttles"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 60

          metrics = [
            [
              "AWS/DynamoDB",
              "ReadThrottleEvents",
              "TableName",
              aws_dynamodb_table.aircraft_projection_points.name,
              "GlobalSecondaryIndexName",
              "aircraft_id-projected_time_epoch-index"
            ],
            [
              ".",
              "WriteThrottleEvents",
              ".",
              ".",
              ".",
              "."
            ]
          ]
        }
      },

      {
        type   = "metric"
        x      = 0
        y      = 18
        width  = 24
        height = 6

        properties = {
          title  = "Projection Eligibility — Impact Matching"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 60

          metrics = [
            [
              "Wilvor/Pipeline",
              "ImpactCandidatesFound",
              "Environment",
              lookup(var.tags, "Environment", "dev"),
              "Pipeline",
              "projection",
              "Component",
              "projection_processor",
              "Stage",
              "eligibility"
            ],
            [
              ".",
              "ValidImpactMatches",
              ".",
              ".",
              ".",
              ".",
              ".",
              ".",
              ".",
              "."
            ]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 24
        width  = 12
        height = 6

        properties = {
          title  = "AircraftProjection Capacity"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 60

          metrics = [
            [
              "AWS/DynamoDB",
              "ConsumedReadCapacityUnits",
              "TableName",
              aws_dynamodb_table.aircraft_projection.name
            ],
            [
              ".",
              "ConsumedWriteCapacityUnits",
              ".",
              "."
            ],
            [
              "AWS/DynamoDB",
              "ConsumedReadCapacityUnits",
              "TableName",
              aws_dynamodb_table.aircraft_projection.name,
              "GlobalSecondaryIndexName",
              "aircraft_id-generated_at_epoch-index"
            ],
            [
              ".",
              "ConsumedWriteCapacityUnits",
              ".",
              ".",
              ".",
              "."
            ],
            [
              "AWS/DynamoDB",
              "ConsumedReadCapacityUnits",
              "TableName",
              aws_dynamodb_table.aircraft_projection.name,
              "GlobalSecondaryIndexName",
              "projection_status-valid_until_epoch-index"
            ],
            [
              ".",
              "ConsumedWriteCapacityUnits",
              ".",
              ".",
              ".",
              "."
            ]
          ]
        }
      },

      {
        type   = "metric"
        x      = 12
        y      = 24
        width  = 12
        height = 6

        properties = {
          title  = "AircraftProjection Throttles"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 60

          metrics = [
            [
              "AWS/DynamoDB",
              "ReadThrottleEvents",
              "TableName",
              aws_dynamodb_table.aircraft_projection.name
            ],
            [
              ".",
              "WriteThrottleEvents",
              ".",
              "."
            ],
            [
              "AWS/DynamoDB",
              "ReadThrottleEvents",
              "TableName",
              aws_dynamodb_table.aircraft_projection.name,
              "GlobalSecondaryIndexName",
              "aircraft_id-generated_at_epoch-index"
            ],
            [
              ".",
              "WriteThrottleEvents",
              ".",
              ".",
              ".",
              "."
            ],
            [
              "AWS/DynamoDB",
              "ReadThrottleEvents",
              "TableName",
              aws_dynamodb_table.aircraft_projection.name,
              "GlobalSecondaryIndexName",
              "projection_status-valid_until_epoch-index"
            ],
            [
              ".",
              "WriteThrottleEvents",
              ".",
              ".",
              ".",
              "."
            ]
          ]
        }
      },

      {
        type   = "metric"
        x      = 0
        y      = 30
        width  = 12
        height = 6

        properties = {
          title  = "AircraftProjectionCells Capacity"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 60

          metrics = [
            [
              "AWS/DynamoDB",
              "ConsumedReadCapacityUnits",
              "TableName",
              aws_dynamodb_table.aircraft_projection_cells.name
            ],
            [
              ".",
              "ConsumedWriteCapacityUnits",
              ".",
              "."
            ],
            [
              "AWS/DynamoDB",
              "ConsumedReadCapacityUnits",
              "TableName",
              aws_dynamodb_table.aircraft_projection_cells.name,
              "GlobalSecondaryIndexName",
              "h3_cell-projection_id-index"
            ],
            [
              ".",
              "ConsumedWriteCapacityUnits",
              ".",
              ".",
              ".",
              "."
            ]
          ]
        }
      },

      {
        type   = "metric"
        x      = 12
        y      = 30
        width  = 12
        height = 6

        properties = {
          title  = "AircraftProjectionCells Throttles"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 60

          metrics = [
            [
              "AWS/DynamoDB",
              "ReadThrottleEvents",
              "TableName",
              aws_dynamodb_table.aircraft_projection_cells.name
            ],
            [
              ".",
              "WriteThrottleEvents",
              ".",
              "."
            ],
            [
              "AWS/DynamoDB",
              "ReadThrottleEvents",
              "TableName",
              aws_dynamodb_table.aircraft_projection_cells.name,
              "GlobalSecondaryIndexName",
              "h3_cell-projection_id-index"
            ],
            [
              ".",
              "WriteThrottleEvents",
              ".",
              ".",
              ".",
              "."
            ]
          ]
        }
      }

    ]
  })
}

