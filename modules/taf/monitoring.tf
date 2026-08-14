locals {
  taf_monitoring_prefix   = "${var.name_prefix}-taf"
  wilvor_metric_namespace = "Wilvor/Pipeline"
  taf_environment         = replace(var.name_prefix, "wilvor-", "")

  taf_poller_dimensions = {
    Environment = local.taf_environment
    Pipeline    = "taf"
    Component   = "taf_poller"
    Stage       = "poll"
  }

  taf_processor_dimensions = {
    Environment = local.taf_environment
    Pipeline    = "taf"
    Component   = "taf_processor"
    Stage       = "process"
  }
}

# ============================================================
# CUSTOM WILVOR METRIC ALARMS
# ============================================================

resource "aws_cloudwatch_metric_alarm" "taf_poller_poll_failure" {
  alarm_name          = "${local.taf_monitoring_prefix}-poller-poll-failure"
  alarm_description   = "TAF poller reported one or more poll failures."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "PollFailure"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"
  dimensions          = local.taf_poller_dimensions
}

resource "aws_cloudwatch_metric_alarm" "taf_poller_failed_kinesis_records" {
  alarm_name          = "${local.taf_monitoring_prefix}-poller-failed-kinesis-records"
  alarm_description   = "TAF poller failed to publish records to Kinesis."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "FailedKinesisRecords"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"
  dimensions          = local.taf_poller_dimensions
}

resource "aws_cloudwatch_metric_alarm" "taf_processor_processing_failures" {
  alarm_name          = "${local.taf_monitoring_prefix}-processor-processing-failures"
  alarm_description   = "TAF processor failed one or more retryable records."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "ProcessingFailures"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"
  dimensions          = local.taf_processor_dimensions
}

resource "aws_cloudwatch_metric_alarm" "taf_processor_bad_records" {
  alarm_name          = "${local.taf_monitoring_prefix}-processor-bad-records"
  alarm_description   = "TAF processor quarantined one or more permanent bad records."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "BadRecords"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"
  dimensions          = local.taf_processor_dimensions
}

# ============================================================
# LAMBDA NATIVE ALARMS
# ============================================================

resource "aws_cloudwatch_metric_alarm" "taf_poller_lambda_errors" {
  alarm_name          = "${local.taf_monitoring_prefix}-poller-lambda-errors"
  alarm_description   = "TAF poller Lambda had runtime errors."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.taf_poller.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "taf_poller_lambda_throttles" {
  alarm_name          = "${local.taf_monitoring_prefix}-poller-lambda-throttles"
  alarm_description   = "TAF poller Lambda was throttled."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.taf_poller.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "taf_processor_lambda_errors" {
  alarm_name          = "${local.taf_monitoring_prefix}-processor-lambda-errors"
  alarm_description   = "TAF processor Lambda had runtime errors."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.taf_processor.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "taf_processor_lambda_throttles" {
  alarm_name          = "${local.taf_monitoring_prefix}-processor-lambda-throttles"
  alarm_description   = "TAF processor Lambda was throttled."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.taf_processor.function_name
  }
}

# ============================================================
# KINESIS NATIVE ALARMS
# ============================================================

resource "aws_cloudwatch_metric_alarm" "taf_raw_write_throttles" {
  alarm_name          = "${local.taf_monitoring_prefix}-raw-write-throttles"
  alarm_description   = "TAF raw Kinesis stream had write throughput exceeded events."
  namespace           = "AWS/Kinesis"
  metric_name         = "WriteProvisionedThroughputExceeded"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StreamName = aws_kinesis_stream.taf_raw.name
  }
}

resource "aws_cloudwatch_metric_alarm" "taf_raw_read_throttles" {
  alarm_name          = "${local.taf_monitoring_prefix}-raw-read-throttles"
  alarm_description   = "TAF raw Kinesis stream had read throughput exceeded events."
  namespace           = "AWS/Kinesis"
  metric_name         = "ReadProvisionedThroughputExceeded"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StreamName = aws_kinesis_stream.taf_raw.name
  }
}

resource "aws_cloudwatch_metric_alarm" "taf_raw_iterator_age_high" {
  alarm_name          = "${local.taf_monitoring_prefix}-raw-iterator-age-high"
  alarm_description   = "TAF processor may be falling behind the raw Kinesis stream."
  namespace           = "AWS/Kinesis"
  metric_name         = "GetRecords.IteratorAgeMilliseconds"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 120000
  evaluation_periods  = 2
  period              = 60
  statistic           = "Maximum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StreamName = aws_kinesis_stream.taf_raw.name
  }
}

# ============================================================
# DYNAMODB NATIVE ALARMS
# ============================================================

resource "aws_cloudwatch_metric_alarm" "taf_latest_write_throttles" {
  alarm_name          = "${local.taf_monitoring_prefix}-taf-latest-write-throttles"
  alarm_description   = "TafLatest had DynamoDB write throttle events."
  namespace           = "AWS/DynamoDB"
  metric_name         = "WriteThrottleEvents"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = aws_dynamodb_table.taf_latest.name
  }
}

resource "aws_cloudwatch_metric_alarm" "taf_latest_read_throttles" {
  alarm_name          = "${local.taf_monitoring_prefix}-taf-latest-read-throttles"
  alarm_description   = "TafLatest had DynamoDB read throttle events."
  namespace           = "AWS/DynamoDB"
  metric_name         = "ReadThrottleEvents"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = aws_dynamodb_table.taf_latest.name
  }
}

resource "aws_cloudwatch_metric_alarm" "taf_forecast_periods_write_throttles" {
  alarm_name          = "${local.taf_monitoring_prefix}-taf-forecast-periods-write-throttles"
  alarm_description   = "TafForecastPeriods had DynamoDB write throttle events."
  namespace           = "AWS/DynamoDB"
  metric_name         = "WriteThrottleEvents"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = aws_dynamodb_table.taf_forecast_periods.name
  }
}

resource "aws_cloudwatch_metric_alarm" "taf_forecast_periods_read_throttles" {
  alarm_name          = "${local.taf_monitoring_prefix}-taf-forecast-periods-read-throttles"
  alarm_description   = "TafForecastPeriods had DynamoDB read throttle events."
  namespace           = "AWS/DynamoDB"
  metric_name         = "ReadThrottleEvents"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = aws_dynamodb_table.taf_forecast_periods.name
  }
}

# ============================================================
# CLOUDWATCH DASHBOARD
# ============================================================

resource "aws_cloudwatch_dashboard" "taf_pipeline" {
  dashboard_name = "${var.name_prefix}-taf-pipeline"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 2

        properties = {
          markdown = "# Wilvor TAF Pipeline\nNOAA → poller → S3/Kinesis → processor → DynamoDB → EventBridge"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 2
        width  = 12
        height = 6

        properties = {
          title   = "TAF Poller"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false

          metrics = [
            [local.wilvor_metric_namespace, "PollSuccess", "Environment", local.taf_environment, "Pipeline", "taf", "Component", "taf_poller", "Stage", "poll"],
            [local.wilvor_metric_namespace, "PollFailure", "Environment", local.taf_environment, "Pipeline", "taf", "Component", "taf_poller", "Stage", "poll"],
            [local.wilvor_metric_namespace, "RecordsReceived", "Environment", local.taf_environment, "Pipeline", "taf", "Component", "taf_poller", "Stage", "poll"],
            [local.wilvor_metric_namespace, "PublishedToKinesis", "Environment", local.taf_environment, "Pipeline", "taf", "Component", "taf_poller", "Stage", "poll"],
            [local.wilvor_metric_namespace, "FailedKinesisRecords", "Environment", local.taf_environment, "Pipeline", "taf", "Component", "taf_poller", "Stage", "poll"],
            [local.wilvor_metric_namespace, "RawArchiveSuccess", "Environment", local.taf_environment, "Pipeline", "taf", "Component", "taf_poller", "Stage", "poll"]
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
          title   = "TAF Processor"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false

          metrics = [
            [local.wilvor_metric_namespace, "RecordsReceived", "Environment", local.taf_environment, "Pipeline", "taf", "Component", "taf_processor", "Stage", "process"],
            [local.wilvor_metric_namespace, "RecordsNew", "Environment", local.taf_environment, "Pipeline", "taf", "Component", "taf_processor", "Stage", "process"],
            [local.wilvor_metric_namespace, "RecordsUpdated", "Environment", local.taf_environment, "Pipeline", "taf", "Component", "taf_processor", "Stage", "process"],
            [local.wilvor_metric_namespace, "RecordsCorrected", "Environment", local.taf_environment, "Pipeline", "taf", "Component", "taf_processor", "Stage", "process"],
            [local.wilvor_metric_namespace, "RecordsUnchanged", "Environment", local.taf_environment, "Pipeline", "taf", "Component", "taf_processor", "Stage", "process"],
            [local.wilvor_metric_namespace, "RecordsStale", "Environment", local.taf_environment, "Pipeline", "taf", "Component", "taf_processor", "Stage", "process"],
            [local.wilvor_metric_namespace, "BadRecords", "Environment", local.taf_environment, "Pipeline", "taf", "Component", "taf_processor", "Stage", "process"],
            [local.wilvor_metric_namespace, "ProcessingFailures", "Environment", local.taf_environment, "Pipeline", "taf", "Component", "taf_processor", "Stage", "process"]
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
          title   = "TAF Raw Kinesis"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false

          metrics = [
            ["AWS/Kinesis", "IncomingRecords", "StreamName", aws_kinesis_stream.taf_raw.name],
            ["AWS/Kinesis", "WriteProvisionedThroughputExceeded", "StreamName", aws_kinesis_stream.taf_raw.name],
            ["AWS/Kinesis", "ReadProvisionedThroughputExceeded", "StreamName", aws_kinesis_stream.taf_raw.name],
            ["AWS/Kinesis", "GetRecords.IteratorAgeMilliseconds", "StreamName", aws_kinesis_stream.taf_raw.name, { stat = "Maximum", label = "Iterator age (max ms)" }]
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
          title   = "TAF Lambda Health"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false

          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.taf_poller.function_name],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.taf_poller.function_name],
            ["AWS/Lambda", "Throttles", "FunctionName", aws_lambda_function.taf_poller.function_name],
            ["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.taf_processor.function_name],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.taf_processor.function_name],
            ["AWS/Lambda", "Throttles", "FunctionName", aws_lambda_function.taf_processor.function_name]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 14
        width  = 24
        height = 6
        properties = {
          title   = "TafLatest DynamoDB"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", aws_dynamodb_table.taf_latest.name],
            ["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", aws_dynamodb_table.taf_latest.name],
            ["AWS/DynamoDB", "WriteThrottleEvents", "TableName", aws_dynamodb_table.taf_latest.name],
            ["AWS/DynamoDB", "ReadThrottleEvents", "TableName", aws_dynamodb_table.taf_latest.name]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 20
        width  = 24
        height = 6
        properties = {
          title   = "TafForecastPeriods DynamoDB"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", aws_dynamodb_table.taf_forecast_periods.name],
            ["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", aws_dynamodb_table.taf_forecast_periods.name],
            ["AWS/DynamoDB", "WriteThrottleEvents", "TableName", aws_dynamodb_table.taf_forecast_periods.name],
            ["AWS/DynamoDB", "ReadThrottleEvents", "TableName", aws_dynamodb_table.taf_forecast_periods.name]
          ]
        }
      }
    ]
  })
}
