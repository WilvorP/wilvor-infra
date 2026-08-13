locals {
  metar_monitoring_prefix = "${var.name_prefix}-metar"
  wilvor_metric_namespace = "Wilvor/Pipeline"
  metar_environment       = replace(var.name_prefix, "wilvor-", "")

  metar_poller_dimensions = {
    Environment = local.metar_environment
    Pipeline    = "metar"
    Component   = "metar_poller"
    Stage       = "poll"
  }

  metar_processor_dimensions = {
    Environment = local.metar_environment
    Pipeline    = "metar"
    Component   = "metar_processor"
    Stage       = "latest_state"
  }
}

resource "aws_cloudwatch_metric_alarm" "metar_poller_poll_failure" {
  alarm_name          = "${local.metar_monitoring_prefix}-poller-poll-failure"
  alarm_description   = "METAR poller reported one or more poll failures."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "PollFailure"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"
  dimensions          = local.metar_poller_dimensions
}

resource "aws_cloudwatch_metric_alarm" "metar_poller_failed_kinesis_records" {
  alarm_name          = "${local.metar_monitoring_prefix}-poller-failed-kinesis-records"
  alarm_description   = "METAR poller failed to publish records to Kinesis."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "FailedKinesisRecords"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"
  dimensions          = local.metar_poller_dimensions
}

resource "aws_cloudwatch_metric_alarm" "metar_processor_processing_failures" {
  alarm_name          = "${local.metar_monitoring_prefix}-processor-processing-failures"
  alarm_description   = "METAR processor reported one or more processing failures."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "ProcessingFailures"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"
  dimensions          = local.metar_processor_dimensions
}

resource "aws_cloudwatch_metric_alarm" "metar_processor_batch_item_failures" {
  alarm_name          = "${local.metar_monitoring_prefix}-processor-batch-item-failures"
  alarm_description   = "METAR processor returned Kinesis partial-batch failures."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "BatchItemFailures"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"
  dimensions          = local.metar_processor_dimensions
}

resource "aws_cloudwatch_metric_alarm" "metar_processor_bad_records_written" {
  alarm_name          = "${local.metar_monitoring_prefix}-processor-bad-records-written"
  alarm_description   = "METAR processor quarantined one or more permanent bad records."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "BadRecordsWritten"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"
  dimensions          = local.metar_processor_dimensions
}

resource "aws_cloudwatch_metric_alarm" "metar_poller_lambda_errors" {
  alarm_name          = "${local.metar_monitoring_prefix}-poller-lambda-errors"
  alarm_description   = "METAR poller Lambda had runtime errors."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.metar_poller.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "metar_poller_lambda_throttles" {
  alarm_name          = "${local.metar_monitoring_prefix}-poller-lambda-throttles"
  alarm_description   = "METAR poller Lambda was throttled."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.metar_poller.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "metar_processor_lambda_errors" {
  alarm_name          = "${local.metar_monitoring_prefix}-processor-lambda-errors"
  alarm_description   = "METAR processor Lambda had runtime errors."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.metar_processor.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "metar_processor_lambda_throttles" {
  alarm_name          = "${local.metar_monitoring_prefix}-processor-lambda-throttles"
  alarm_description   = "METAR processor Lambda was throttled."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.metar_processor.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "metar_raw_write_throttles" {
  alarm_name          = "${local.metar_monitoring_prefix}-raw-write-throttles"
  alarm_description   = "METAR raw Kinesis stream had write throughput exceeded events."
  namespace           = "AWS/Kinesis"
  metric_name         = "WriteProvisionedThroughputExceeded"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StreamName = aws_kinesis_stream.metar_raw.name
  }
}

resource "aws_cloudwatch_metric_alarm" "metar_raw_read_throttles" {
  alarm_name          = "${local.metar_monitoring_prefix}-raw-read-throttles"
  alarm_description   = "METAR raw Kinesis stream had read throughput exceeded events."
  namespace           = "AWS/Kinesis"
  metric_name         = "ReadProvisionedThroughputExceeded"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StreamName = aws_kinesis_stream.metar_raw.name
  }
}

resource "aws_cloudwatch_metric_alarm" "metar_raw_iterator_age_warning" {
  alarm_name          = "${local.metar_monitoring_prefix}-raw-iterator-age-warning"
  alarm_description   = "METAR processor consumer lag exceeded 10 seconds."
  namespace           = "AWS/Kinesis"
  metric_name         = "GetRecords.IteratorAgeMilliseconds"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 10000
  evaluation_periods  = 1
  period              = 60
  statistic           = "Maximum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StreamName = aws_kinesis_stream.metar_raw.name
  }
}

resource "aws_cloudwatch_metric_alarm" "metar_raw_iterator_age_critical" {
  alarm_name          = "${local.metar_monitoring_prefix}-raw-iterator-age-critical"
  alarm_description   = "METAR processor consumer lag exceeded 30 seconds."
  namespace           = "AWS/Kinesis"
  metric_name         = "GetRecords.IteratorAgeMilliseconds"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 30000
  evaluation_periods  = 1
  period              = 60
  statistic           = "Maximum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StreamName = aws_kinesis_stream.metar_raw.name
  }
}

resource "aws_cloudwatch_metric_alarm" "metar_latest_write_throttles" {
  alarm_name          = "${local.metar_monitoring_prefix}-latest-write-throttles"
  alarm_description   = "MetarLatest had DynamoDB write throttle events."
  namespace           = "AWS/DynamoDB"
  metric_name         = "WriteThrottleEvents"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = aws_dynamodb_table.metar_latest.name
  }
}

resource "aws_cloudwatch_metric_alarm" "metar_latest_read_throttles" {
  alarm_name          = "${local.metar_monitoring_prefix}-latest-read-throttles"
  alarm_description   = "MetarLatest had DynamoDB read throttle events."
  namespace           = "AWS/DynamoDB"
  metric_name         = "ReadThrottleEvents"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = aws_dynamodb_table.metar_latest.name
  }
}

resource "aws_cloudwatch_metric_alarm" "metar_latest_get_item_system_errors" {
  alarm_name          = "${local.metar_monitoring_prefix}-latest-get-item-system-errors"
  alarm_description   = "MetarLatest GetItem operations returned DynamoDB system errors."
  namespace           = "AWS/DynamoDB"
  metric_name         = "SystemErrors"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = aws_dynamodb_table.metar_latest.name
    Operation = "GetItem"
  }
}

resource "aws_cloudwatch_metric_alarm" "metar_latest_put_item_system_errors" {
  alarm_name          = "${local.metar_monitoring_prefix}-latest-put-item-system-errors"
  alarm_description   = "MetarLatest PutItem operations returned DynamoDB system errors."
  namespace           = "AWS/DynamoDB"
  metric_name         = "SystemErrors"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = aws_dynamodb_table.metar_latest.name
    Operation = "PutItem"
  }
}

resource "aws_cloudwatch_metric_alarm" "metar_latest_update_item_system_errors" {
  alarm_name          = "${local.metar_monitoring_prefix}-latest-update-item-system-errors"
  alarm_description   = "MetarLatest UpdateItem operations returned DynamoDB system errors."
  namespace           = "AWS/DynamoDB"
  metric_name         = "SystemErrors"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = aws_dynamodb_table.metar_latest.name
    Operation = "UpdateItem"
  }
}

resource "aws_cloudwatch_dashboard" "metar_pipeline" {
  dashboard_name = "${var.name_prefix}-metar-pipeline"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 2

        properties = {
          markdown = "# Wilvor METAR Pipeline\nNOAA → poller → S3/Kinesis → processor → DynamoDB → EventBridge"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 2
        width  = 12
        height = 6

        properties = {
          title   = "METAR Poller"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false

          metrics = [
            [local.wilvor_metric_namespace, "PollSuccess", "Environment", local.metar_environment, "Pipeline", "metar", "Component", "metar_poller", "Stage", "poll"],
            [local.wilvor_metric_namespace, "PollFailure", "Environment", local.metar_environment, "Pipeline", "metar", "Component", "metar_poller", "Stage", "poll"],
            [local.wilvor_metric_namespace, "FeaturesReceived", "Environment", local.metar_environment, "Pipeline", "metar", "Component", "metar_poller", "Stage", "poll"],
            [local.wilvor_metric_namespace, "PublishedToKinesis", "Environment", local.metar_environment, "Pipeline", "metar", "Component", "metar_poller", "Stage", "poll"],
            [local.wilvor_metric_namespace, "FailedKinesisRecords", "Environment", local.metar_environment, "Pipeline", "metar", "Component", "metar_poller", "Stage", "poll"],
            [local.wilvor_metric_namespace, "RawArchiveSuccess", "Environment", local.metar_environment, "Pipeline", "metar", "Component", "metar_poller", "Stage", "poll"]
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
          title   = "METAR Processor State Changes"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false

          metrics = [
            [local.wilvor_metric_namespace, "RecordsReceived", "Environment", local.metar_environment, "Pipeline", "metar", "Component", "metar_processor", "Stage", "latest_state"],
            [local.wilvor_metric_namespace, "RecordsNew", "Environment", local.metar_environment, "Pipeline", "metar", "Component", "metar_processor", "Stage", "latest_state"],
            [local.wilvor_metric_namespace, "RecordsUpdated", "Environment", local.metar_environment, "Pipeline", "metar", "Component", "metar_processor", "Stage", "latest_state"],
            [local.wilvor_metric_namespace, "RecordsCorrected", "Environment", local.metar_environment, "Pipeline", "metar", "Component", "metar_processor", "Stage", "latest_state"],
            [local.wilvor_metric_namespace, "RecordsUnchanged", "Environment", local.metar_environment, "Pipeline", "metar", "Component", "metar_processor", "Stage", "latest_state"],
            [local.wilvor_metric_namespace, "RecordsStale", "Environment", local.metar_environment, "Pipeline", "metar", "Component", "metar_processor", "Stage", "latest_state"],
            [local.wilvor_metric_namespace, "DynamoDBWrites", "Environment", local.metar_environment, "Pipeline", "metar", "Component", "metar_processor", "Stage", "latest_state"]
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
          title   = "METAR Raw Kinesis"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false

          metrics = [
            ["AWS/Kinesis", "IncomingRecords", "StreamName", aws_kinesis_stream.metar_raw.name],
            ["AWS/Kinesis", "IncomingBytes", "StreamName", aws_kinesis_stream.metar_raw.name],
            ["AWS/Kinesis", "WriteProvisionedThroughputExceeded", "StreamName", aws_kinesis_stream.metar_raw.name],
            ["AWS/Kinesis", "ReadProvisionedThroughputExceeded", "StreamName", aws_kinesis_stream.metar_raw.name],
            [
              "AWS/Kinesis",
              "GetRecords.IteratorAgeMilliseconds",
              "StreamName",
              aws_kinesis_stream.metar_raw.name,
              {
                stat  = "Maximum"
                label = "Iterator age (max ms)"
              }
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
          title   = "METAR Lambda Health"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false

          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.metar_poller.function_name],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.metar_poller.function_name],
            ["AWS/Lambda", "Throttles", "FunctionName", aws_lambda_function.metar_poller.function_name],
            ["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.metar_processor.function_name],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.metar_processor.function_name],
            ["AWS/Lambda", "Throttles", "FunctionName", aws_lambda_function.metar_processor.function_name]
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
          title   = "MetarLatest DynamoDB"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false

          metrics = [
            ["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", aws_dynamodb_table.metar_latest.name],
            ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", aws_dynamodb_table.metar_latest.name],
            ["AWS/DynamoDB", "ReadThrottleEvents", "TableName", aws_dynamodb_table.metar_latest.name],
            ["AWS/DynamoDB", "WriteThrottleEvents", "TableName", aws_dynamodb_table.metar_latest.name],
            ["AWS/DynamoDB", "SystemErrors", "TableName", aws_dynamodb_table.metar_latest.name, "Operation", "GetItem"],
            ["AWS/DynamoDB", "SystemErrors", "TableName", aws_dynamodb_table.metar_latest.name, "Operation", "PutItem"],
            ["AWS/DynamoDB", "SystemErrors", "TableName", aws_dynamodb_table.metar_latest.name, "Operation", "UpdateItem"]
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
          title   = "METAR Processor Failures and Event Publishing"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false

          metrics = [
            [local.wilvor_metric_namespace, "MetarUpdatedEventsPublished", "Environment", local.metar_environment, "Pipeline", "metar", "Component", "metar_processor", "Stage", "latest_state"],
            [local.wilvor_metric_namespace, "BadRecordsWritten", "Environment", local.metar_environment, "Pipeline", "metar", "Component", "metar_processor", "Stage", "latest_state"],
            [local.wilvor_metric_namespace, "ProcessingFailures", "Environment", local.metar_environment, "Pipeline", "metar", "Component", "metar_processor", "Stage", "latest_state"],
            [local.wilvor_metric_namespace, "BatchItemFailures", "Environment", local.metar_environment, "Pipeline", "metar", "Component", "metar_processor", "Stage", "latest_state"]
          ]
        }
      }
    ]
  })
}