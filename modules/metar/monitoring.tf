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
          markdown = "# Wilvor METAR Pipeline\nNOAA → METAR poller → S3 archive and raw Kinesis"
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
          title   = "METAR Poller Lambda Health"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false

          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.metar_poller.function_name],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.metar_poller.function_name],
            ["AWS/Lambda", "Throttles", "FunctionName", aws_lambda_function.metar_poller.function_name]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 8
        width  = 24
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
            ["AWS/Kinesis", "WriteProvisionedThroughputExceeded", "StreamName", aws_kinesis_stream.metar_raw.name]
          ]
        }
      }
    ]
  })
}