locals {
  sigmet_monitoring_prefix = "${var.name_prefix}-sigmet"
  wilvor_metric_namespace  = "Wilvor/Pipeline"
  sigmet_environment       = replace(var.name_prefix, "wilvor-", "")

  sigmet_poller_dimensions = {
    Environment = local.sigmet_environment
    Pipeline    = "sigmet"
    Component   = "sigmet_poller"
    Stage       = "poll"
  }

  sigmet_processor_dimensions = {
    Environment = local.sigmet_environment
    Pipeline    = "sigmet"
    Component   = "sigmet_processor"
    Stage       = "raw_to_state"
  }
}

# ============================================================
# CUSTOM WILVOR METRIC ALARMS
# ============================================================

resource "aws_cloudwatch_metric_alarm" "sigmet_poller_poll_failure" {
  alarm_name          = "${local.sigmet_monitoring_prefix}-poller-poll-failure"
  alarm_description   = "SIGMET poller reported one or more poll failures."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "PollFailure"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"
  dimensions          = local.sigmet_poller_dimensions
}

resource "aws_cloudwatch_metric_alarm" "sigmet_poller_failed_kinesis_records" {
  alarm_name          = "${local.sigmet_monitoring_prefix}-poller-failed-kinesis-records"
  alarm_description   = "SIGMET poller failed to publish records to Kinesis."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "FailedKinesisRecords"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"
  dimensions          = local.sigmet_poller_dimensions
}

resource "aws_cloudwatch_metric_alarm" "sigmet_processor_records_failed" {
  alarm_name          = "${local.sigmet_monitoring_prefix}-processor-records-failed"
  alarm_description   = "SIGMET processor failed one or more retryable records."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "RecordsFailed"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"
  dimensions          = local.sigmet_processor_dimensions
}

resource "aws_cloudwatch_metric_alarm" "sigmet_processor_batch_item_failures" {
  alarm_name          = "${local.sigmet_monitoring_prefix}-processor-batch-item-failures"
  alarm_description   = "SIGMET processor returned Kinesis partial-batch failures."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "BatchItemFailures"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"
  dimensions          = local.sigmet_processor_dimensions
}

resource "aws_cloudwatch_metric_alarm" "sigmet_processor_bad_records_written" {
  alarm_name          = "${local.sigmet_monitoring_prefix}-processor-bad-records-written"
  alarm_description   = "SIGMET processor quarantined one or more permanent bad records."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "BadRecordsWritten"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"
  dimensions          = local.sigmet_processor_dimensions
}

# ============================================================
# LAMBDA NATIVE ALARMS
# ============================================================

resource "aws_cloudwatch_metric_alarm" "sigmet_poller_lambda_errors" {
  alarm_name          = "${local.sigmet_monitoring_prefix}-poller-lambda-errors"
  alarm_description   = "SIGMET poller Lambda had runtime errors."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.sigmet_poller.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "sigmet_poller_lambda_throttles" {
  alarm_name          = "${local.sigmet_monitoring_prefix}-poller-lambda-throttles"
  alarm_description   = "SIGMET poller Lambda was throttled."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.sigmet_poller.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "sigmet_processor_lambda_errors" {
  alarm_name          = "${local.sigmet_monitoring_prefix}-processor-lambda-errors"
  alarm_description   = "SIGMET processor Lambda had runtime errors."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.sigmet_processor.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "sigmet_processor_lambda_throttles" {
  alarm_name          = "${local.sigmet_monitoring_prefix}-processor-lambda-throttles"
  alarm_description   = "SIGMET processor Lambda was throttled."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.sigmet_processor.function_name
  }
}

# ============================================================
# KINESIS NATIVE ALARMS
# ============================================================

resource "aws_cloudwatch_metric_alarm" "sigmet_raw_write_throttles" {
  alarm_name          = "${local.sigmet_monitoring_prefix}-raw-write-throttles"
  alarm_description   = "SIGMET raw Kinesis stream had write throughput exceeded events."
  namespace           = "AWS/Kinesis"
  metric_name         = "WriteProvisionedThroughputExceeded"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StreamName = aws_kinesis_stream.sigmet_raw.name
  }
}

resource "aws_cloudwatch_metric_alarm" "sigmet_raw_read_throttles" {
  alarm_name          = "${local.sigmet_monitoring_prefix}-raw-read-throttles"
  alarm_description   = "SIGMET raw Kinesis stream had read throughput exceeded events."
  namespace           = "AWS/Kinesis"
  metric_name         = "ReadProvisionedThroughputExceeded"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StreamName = aws_kinesis_stream.sigmet_raw.name
  }
}

resource "aws_cloudwatch_metric_alarm" "sigmet_raw_iterator_age_high" {
  alarm_name          = "${local.sigmet_monitoring_prefix}-raw-iterator-age-high"
  alarm_description   = "SIGMET processor may be falling behind the raw Kinesis stream."
  namespace           = "AWS/Kinesis"
  metric_name         = "GetRecords.IteratorAgeMilliseconds"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 120000
  evaluation_periods  = 2
  period              = 60
  statistic           = "Maximum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StreamName = aws_kinesis_stream.sigmet_raw.name
  }
}

# ============================================================
# DYNAMODB NATIVE ALARMS
# ============================================================

resource "aws_cloudwatch_metric_alarm" "active_hazards_write_throttles" {
  alarm_name          = "${local.sigmet_monitoring_prefix}-active-hazards-write-throttles"
  alarm_description   = "ActiveHazards had DynamoDB write throttle events."
  namespace           = "AWS/DynamoDB"
  metric_name         = "WriteThrottleEvents"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = aws_dynamodb_table.active_hazards.name
  }
}

resource "aws_cloudwatch_metric_alarm" "active_hazards_read_throttles" {
  alarm_name          = "${local.sigmet_monitoring_prefix}-active-hazards-read-throttles"
  alarm_description   = "ActiveHazards had DynamoDB read throttle events."
  namespace           = "AWS/DynamoDB"
  metric_name         = "ReadThrottleEvents"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = aws_dynamodb_table.active_hazards.name
  }
}

resource "aws_cloudwatch_metric_alarm" "hazard_cells_write_throttles" {
  alarm_name          = "${local.sigmet_monitoring_prefix}-hazard-cells-write-throttles"
  alarm_description   = "HazardCells had DynamoDB write throttle events."
  namespace           = "AWS/DynamoDB"
  metric_name         = "WriteThrottleEvents"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = aws_dynamodb_table.hazard_cells.name
  }
}

resource "aws_cloudwatch_metric_alarm" "hazard_cells_read_throttles" {
  alarm_name          = "${local.sigmet_monitoring_prefix}-hazard-cells-read-throttles"
  alarm_description   = "HazardCells had DynamoDB read throttle events."
  namespace           = "AWS/DynamoDB"
  metric_name         = "ReadThrottleEvents"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = aws_dynamodb_table.hazard_cells.name
  }
}

# ============================================================
# DASHBOARD
# ============================================================

resource "aws_cloudwatch_dashboard" "sigmet_pipeline" {
  dashboard_name = "${var.name_prefix}-sigmet-pipeline"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 2

        properties = {
          markdown = "# Wilvor SIGMET Pipeline\nNOAA → poller → S3/Kinesis → processor → DynamoDB → EventBridge"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 2
        width  = 12
        height = 6

        properties = {
          title   = "SIGMET Poller"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false

          metrics = [
            [local.wilvor_metric_namespace, "PollSuccess", "Environment", local.sigmet_environment, "Pipeline", "sigmet", "Component", "sigmet_poller", "Stage", "poll"],
            [local.wilvor_metric_namespace, "PollFailure", "Environment", local.sigmet_environment, "Pipeline", "sigmet", "Component", "sigmet_poller", "Stage", "poll"],
            [local.wilvor_metric_namespace, "FeaturesReceived", "Environment", local.sigmet_environment, "Pipeline", "sigmet", "Component", "sigmet_poller", "Stage", "poll"],
            [local.wilvor_metric_namespace, "PublishedToKinesis", "Environment", local.sigmet_environment, "Pipeline", "sigmet", "Component", "sigmet_poller", "Stage", "poll"],
            [local.wilvor_metric_namespace, "FailedKinesisRecords", "Environment", local.sigmet_environment, "Pipeline", "sigmet", "Component", "sigmet_poller", "Stage", "poll"],
            [local.wilvor_metric_namespace, "RawArchiveSuccess", "Environment", local.sigmet_environment, "Pipeline", "sigmet", "Component", "sigmet_poller", "Stage", "poll"]
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
          title   = "SIGMET Processor"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false

          metrics = [
            [local.wilvor_metric_namespace, "RecordsReceived", "Environment", local.sigmet_environment, "Pipeline", "sigmet", "Component", "sigmet_processor", "Stage", "raw_to_state"],
            [local.wilvor_metric_namespace, "RecordsProcessed", "Environment", local.sigmet_environment, "Pipeline", "sigmet", "Component", "sigmet_processor", "Stage", "raw_to_state"],
            [local.wilvor_metric_namespace, "RecordsFailed", "Environment", local.sigmet_environment, "Pipeline", "sigmet", "Component", "sigmet_processor", "Stage", "raw_to_state"],
            [local.wilvor_metric_namespace, "BadRecordsWritten", "Environment", local.sigmet_environment, "Pipeline", "sigmet", "Component", "sigmet_processor", "Stage", "raw_to_state"],
            [local.wilvor_metric_namespace, "NewRecords", "Environment", local.sigmet_environment, "Pipeline", "sigmet", "Component", "sigmet_processor", "Stage", "raw_to_state"],
            [local.wilvor_metric_namespace, "UpdatedRecords", "Environment", local.sigmet_environment, "Pipeline", "sigmet", "Component", "sigmet_processor", "Stage", "raw_to_state"],
            [local.wilvor_metric_namespace, "UnchangedRecords", "Environment", local.sigmet_environment, "Pipeline", "sigmet", "Component", "sigmet_processor", "Stage", "raw_to_state"],
            [local.wilvor_metric_namespace, "EventBridgeEventsPublished", "Environment", local.sigmet_environment, "Pipeline", "sigmet", "Component", "sigmet_processor", "Stage", "raw_to_state"],
            [local.wilvor_metric_namespace, "BatchItemFailures", "Environment", local.sigmet_environment, "Pipeline", "sigmet", "Component", "sigmet_processor", "Stage", "raw_to_state"]
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
          title   = "SIGMET Raw Kinesis"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false

          metrics = [
            ["AWS/Kinesis", "IncomingRecords", "StreamName", aws_kinesis_stream.sigmet_raw.name],
            ["AWS/Kinesis", "WriteProvisionedThroughputExceeded", "StreamName", aws_kinesis_stream.sigmet_raw.name],
            ["AWS/Kinesis", "ReadProvisionedThroughputExceeded", "StreamName", aws_kinesis_stream.sigmet_raw.name],
            ["AWS/Kinesis", "GetRecords.IteratorAgeMilliseconds", "StreamName", aws_kinesis_stream.sigmet_raw.name, { stat = "Maximum", label = "Iterator age (max ms)" }]
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
          title   = "SIGMET Lambda Health"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false

          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.sigmet_poller.function_name],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.sigmet_poller.function_name],
            ["AWS/Lambda", "Throttles", "FunctionName", aws_lambda_function.sigmet_poller.function_name],
            ["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.sigmet_processor.function_name],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.sigmet_processor.function_name],
            ["AWS/Lambda", "Throttles", "FunctionName", aws_lambda_function.sigmet_processor.function_name]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 14
        width  = 12
        height = 6

        properties = {
          title   = "ActiveHazards DynamoDB"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false

          metrics = [
            ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", aws_dynamodb_table.active_hazards.name],
            ["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", aws_dynamodb_table.active_hazards.name],
            ["AWS/DynamoDB", "WriteThrottleEvents", "TableName", aws_dynamodb_table.active_hazards.name],
            ["AWS/DynamoDB", "ReadThrottleEvents", "TableName", aws_dynamodb_table.active_hazards.name]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 14
        width  = 12
        height = 6

        properties = {
          title   = "HazardCells DynamoDB"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false

          metrics = [
            ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", aws_dynamodb_table.hazard_cells.name],
            ["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", aws_dynamodb_table.hazard_cells.name],
            ["AWS/DynamoDB", "WriteThrottleEvents", "TableName", aws_dynamodb_table.hazard_cells.name],
            ["AWS/DynamoDB", "ReadThrottleEvents", "TableName", aws_dynamodb_table.hazard_cells.name]
          ]
        }
      }
    ]
  })
}