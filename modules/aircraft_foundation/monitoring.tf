locals {
  monitoring_prefix = "${var.name_prefix}-aircraft"

  wilvor_metric_namespace = "Wilvor/Pipeline"

  environment = replace(var.name_prefix, "wilvor-", "")

  common_metric_dimensions = {
    Environment = local.environment
    Pipeline    = "aircraft"
  }

  opensky_poller_dimensions = merge(local.common_metric_dimensions, {
    Component = "opensky_poller"
    Stage     = "poll"
  })

  raw_processor_dimensions = merge(local.common_metric_dimensions, {
    Component = "raw_processor"
    Stage     = "raw_to_clean"
  })

  current_state_writer_dimensions = merge(local.common_metric_dimensions, {
    Component = "current_state_writer"
    Stage     = "clean_to_dynamodb"
  })
}

# ============================================================
# CUSTOM WILVOR PIPELINE METRIC ALARMS
# Namespace: Wilvor/Pipeline
# ============================================================

resource "aws_cloudwatch_metric_alarm" "opensky_poller_poll_failure" {
  alarm_name          = "${local.monitoring_prefix}-opensky-poller-poll-failure"
  alarm_description   = "Local OpenSky poller reported one or more poll failures."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "PollFailure"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = local.opensky_poller_dimensions
}

resource "aws_cloudwatch_metric_alarm" "opensky_poller_failed_kinesis_records" {
  alarm_name          = "${local.monitoring_prefix}-opensky-poller-failed-kinesis-records"
  alarm_description   = "OpenSky poller failed to publish one or more records to Kinesis raw stream."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "FailedKinesisRecords"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = local.opensky_poller_dimensions
}

resource "aws_cloudwatch_metric_alarm" "raw_processor_clean_records_failed" {
  alarm_name          = "${local.monitoring_prefix}-raw-processor-clean-records-failed"
  alarm_description   = "Raw processor failed to publish one or more clean records."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "CleanRecordsFailed"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = local.raw_processor_dimensions
}

resource "aws_cloudwatch_metric_alarm" "raw_processor_bad_records_archive_failed" {
  alarm_name          = "${local.monitoring_prefix}-raw-processor-bad-records-archive-failed"
  alarm_description   = "Raw processor failed to archive bad records to S3."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "BadRecordsArchiveFailed"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = local.raw_processor_dimensions
}

resource "aws_cloudwatch_metric_alarm" "raw_processor_batch_item_failures" {
  alarm_name          = "${local.monitoring_prefix}-raw-processor-batch-item-failures"
  alarm_description   = "Raw processor returned one or more Kinesis batch item failures."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "BatchItemFailures"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = local.raw_processor_dimensions
}

resource "aws_cloudwatch_metric_alarm" "current_state_writer_failed_records" {
  alarm_name          = "${local.monitoring_prefix}-current-state-writer-failed-records"
  alarm_description   = "Current-state writer failed to write one or more records to DynamoDB."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "FailedRecords"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = local.current_state_writer_dimensions
}

resource "aws_cloudwatch_metric_alarm" "current_state_writer_batch_item_failures" {
  alarm_name          = "${local.monitoring_prefix}-current-state-writer-batch-item-failures"
  alarm_description   = "Current-state writer returned one or more Kinesis batch item failures."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "BatchItemFailures"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = local.current_state_writer_dimensions
}

# ============================================================
# LAMBDA NATIVE ALARMS
# ============================================================

resource "aws_cloudwatch_metric_alarm" "raw_processor_lambda_errors" {
  alarm_name          = "${local.monitoring_prefix}-raw-processor-lambda-errors"
  alarm_description   = "Raw processor Lambda had one or more runtime errors."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.aircraft_raw_processor.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "current_state_writer_lambda_errors" {
  alarm_name          = "${local.monitoring_prefix}-current-state-writer-lambda-errors"
  alarm_description   = "Current-state writer Lambda had one or more runtime errors."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.aircraft_current_state_writer.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "raw_processor_lambda_throttles" {
  alarm_name          = "${local.monitoring_prefix}-raw-processor-lambda-throttles"
  alarm_description   = "Raw processor Lambda was throttled."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.aircraft_raw_processor.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "current_state_writer_lambda_throttles" {
  alarm_name          = "${local.monitoring_prefix}-current-state-writer-lambda-throttles"
  alarm_description   = "Current-state writer Lambda was throttled."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.aircraft_current_state_writer.function_name
  }
}

# ============================================================
# KINESIS NATIVE ALARMS
# ============================================================

resource "aws_cloudwatch_metric_alarm" "raw_stream_write_throttles" {
  alarm_name          = "${local.monitoring_prefix}-raw-stream-write-throttles"
  alarm_description   = "Aircraft raw Kinesis stream had write throughput exceeded events."
  namespace           = "AWS/Kinesis"
  metric_name         = "WriteProvisionedThroughputExceeded"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StreamName = aws_kinesis_stream.aircraft_raw.name
  }
}

resource "aws_cloudwatch_metric_alarm" "raw_stream_read_throttles" {
  alarm_name          = "${local.monitoring_prefix}-raw-stream-read-throttles"
  alarm_description   = "Aircraft raw Kinesis stream had read throughput exceeded events."
  namespace           = "AWS/Kinesis"
  metric_name         = "ReadProvisionedThroughputExceeded"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StreamName = aws_kinesis_stream.aircraft_raw.name
  }
}

resource "aws_cloudwatch_metric_alarm" "clean_stream_write_throttles" {
  alarm_name          = "${local.monitoring_prefix}-clean-stream-write-throttles"
  alarm_description   = "Aircraft clean Kinesis stream had write throughput exceeded events."
  namespace           = "AWS/Kinesis"
  metric_name         = "WriteProvisionedThroughputExceeded"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StreamName = aws_kinesis_stream.aircraft_clean.name
  }
}

resource "aws_cloudwatch_metric_alarm" "clean_stream_read_throttles" {
  alarm_name          = "${local.monitoring_prefix}-clean-stream-read-throttles"
  alarm_description   = "Aircraft clean Kinesis stream had read throughput exceeded events."
  namespace           = "AWS/Kinesis"
  metric_name         = "ReadProvisionedThroughputExceeded"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StreamName = aws_kinesis_stream.aircraft_clean.name
  }
}

resource "aws_cloudwatch_metric_alarm" "raw_stream_iterator_age_high" {
  alarm_name          = "${local.monitoring_prefix}-raw-stream-iterator-age-high"
  alarm_description   = "Aircraft raw Kinesis stream iterator age is high; consumer may be falling behind."
  namespace           = "AWS/Kinesis"
  metric_name         = "GetRecords.IteratorAgeMilliseconds"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 120000
  evaluation_periods  = 2
  period              = 60
  statistic           = "Maximum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StreamName = aws_kinesis_stream.aircraft_raw.name
  }
}

resource "aws_cloudwatch_metric_alarm" "clean_stream_iterator_age_high" {
  alarm_name          = "${local.monitoring_prefix}-clean-stream-iterator-age-high"
  alarm_description   = "Aircraft clean Kinesis stream iterator age is high; current-state writer may be falling behind."
  namespace           = "AWS/Kinesis"
  metric_name         = "GetRecords.IteratorAgeMilliseconds"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 120000
  evaluation_periods  = 2
  period              = 60
  statistic           = "Maximum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StreamName = aws_kinesis_stream.aircraft_clean.name
  }
}

# ============================================================
# DYNAMODB NATIVE ALARMS
# ============================================================

resource "aws_cloudwatch_metric_alarm" "aircraft_current_state_write_throttles" {
  alarm_name          = "${local.monitoring_prefix}-dynamodb-write-throttles"
  alarm_description   = "AircraftCurrentState DynamoDB table had write throttle events."
  namespace           = "AWS/DynamoDB"
  metric_name         = "WriteThrottleEvents"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = aws_dynamodb_table.aircraft_current_state.name
  }
}

resource "aws_cloudwatch_metric_alarm" "aircraft_current_state_read_throttles" {
  alarm_name          = "${local.monitoring_prefix}-dynamodb-read-throttles"
  alarm_description   = "AircraftCurrentState DynamoDB table had read throttle events."
  namespace           = "AWS/DynamoDB"
  metric_name         = "ReadThrottleEvents"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = aws_dynamodb_table.aircraft_current_state.name
  }
}

# ============================================================
# CLOUDWATCH DASHBOARD
# ============================================================

resource "aws_cloudwatch_dashboard" "aircraft_pipeline" {
  dashboard_name = "${var.name_prefix}-aircraft-pipeline"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 2

        properties = {
          markdown = "# Wilvor Aircraft Pipeline\nOpenSky local poller → Kinesis raw → raw processor → Kinesis clean → current-state writer → DynamoDB"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 2
        width  = 12
        height = 6

        properties = {
          title  = "OpenSky Poller - Local Producer"
          region = var.aws_region
          stat   = "Sum"
          period = 60
          metrics = [
            [local.wilvor_metric_namespace, "PollSuccess", "Environment", local.environment, "Pipeline", "aircraft", "Component", "opensky_poller", "Stage", "poll"],
            [local.wilvor_metric_namespace, "PollFailure", "Environment", local.environment, "Pipeline", "aircraft", "Component", "opensky_poller", "Stage", "poll"],
            [local.wilvor_metric_namespace, "StatesCount", "Environment", local.environment, "Pipeline", "aircraft", "Component", "opensky_poller", "Stage", "poll"],
            [local.wilvor_metric_namespace, "PublishedToKinesis", "Environment", local.environment, "Pipeline", "aircraft", "Component", "opensky_poller", "Stage", "poll"],
            [local.wilvor_metric_namespace, "FailedKinesisRecords", "Environment", local.environment, "Pipeline", "aircraft", "Component", "opensky_poller", "Stage", "poll"]
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
          title  = "Raw Processor - Raw to Clean"
          region = var.aws_region
          stat   = "Sum"
          period = 60
          metrics = [
            [local.wilvor_metric_namespace, "TotalRecords", "Environment", local.environment, "Pipeline", "aircraft", "Component", "raw_processor", "Stage", "raw_to_clean"],
            [local.wilvor_metric_namespace, "ValidRecords", "Environment", local.environment, "Pipeline", "aircraft", "Component", "raw_processor", "Stage", "raw_to_clean"],
            [local.wilvor_metric_namespace, "RejectedRecords", "Environment", local.environment, "Pipeline", "aircraft", "Component", "raw_processor", "Stage", "raw_to_clean"],
            [local.wilvor_metric_namespace, "CleanRecordsPublished", "Environment", local.environment, "Pipeline", "aircraft", "Component", "raw_processor", "Stage", "raw_to_clean"],
            [local.wilvor_metric_namespace, "CleanRecordsFailed", "Environment", local.environment, "Pipeline", "aircraft", "Component", "raw_processor", "Stage", "raw_to_clean"]
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
          title  = "Current State Writer - Clean to DynamoDB"
          region = var.aws_region
          stat   = "Sum"
          period = 60
          metrics = [
            [local.wilvor_metric_namespace, "TotalRecords", "Environment", local.environment, "Pipeline", "aircraft", "Component", "current_state_writer", "Stage", "clean_to_dynamodb"],
            [local.wilvor_metric_namespace, "WrittenRecords", "Environment", local.environment, "Pipeline", "aircraft", "Component", "current_state_writer", "Stage", "clean_to_dynamodb"],
            [local.wilvor_metric_namespace, "SkippedStaleRecords", "Environment", local.environment, "Pipeline", "aircraft", "Component", "current_state_writer", "Stage", "clean_to_dynamodb"],
            [local.wilvor_metric_namespace, "RejectedRecords", "Environment", local.environment, "Pipeline", "aircraft", "Component", "current_state_writer", "Stage", "clean_to_dynamodb"],
            [local.wilvor_metric_namespace, "FailedRecords", "Environment", local.environment, "Pipeline", "aircraft", "Component", "current_state_writer", "Stage", "clean_to_dynamodb"]
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
          title  = "Kinesis Streams"
          region = var.aws_region
          stat   = "Sum"
          period = 60
          metrics = [
            ["AWS/Kinesis", "IncomingRecords", "StreamName", aws_kinesis_stream.aircraft_raw.name],
            ["AWS/Kinesis", "IncomingRecords", "StreamName", aws_kinesis_stream.aircraft_clean.name],
            ["AWS/Kinesis", "WriteProvisionedThroughputExceeded", "StreamName", aws_kinesis_stream.aircraft_raw.name],
            ["AWS/Kinesis", "WriteProvisionedThroughputExceeded", "StreamName", aws_kinesis_stream.aircraft_clean.name],
            ["AWS/Kinesis", "ReadProvisionedThroughputExceeded", "StreamName", aws_kinesis_stream.aircraft_raw.name],
            ["AWS/Kinesis", "ReadProvisionedThroughputExceeded", "StreamName", aws_kinesis_stream.aircraft_clean.name]
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
          title  = "Lambda Errors"
          region = var.aws_region
          stat   = "Sum"
          period = 60
          metrics = [
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.aircraft_raw_processor.function_name],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.aircraft_current_state_writer.function_name],
            ["AWS/Lambda", "Throttles", "FunctionName", aws_lambda_function.aircraft_raw_processor.function_name],
            ["AWS/Lambda", "Throttles", "FunctionName", aws_lambda_function.aircraft_current_state_writer.function_name]
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
          title  = "DynamoDB Current State"
          region = var.aws_region
          stat   = "Sum"
          period = 60
          metrics = [
            ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", aws_dynamodb_table.aircraft_current_state.name],
            ["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", aws_dynamodb_table.aircraft_current_state.name],
            ["AWS/DynamoDB", "WriteThrottleEvents", "TableName", aws_dynamodb_table.aircraft_current_state.name],
            ["AWS/DynamoDB", "ReadThrottleEvents", "TableName", aws_dynamodb_table.aircraft_current_state.name]
          ]
        }
      }
    ]
  })
}
