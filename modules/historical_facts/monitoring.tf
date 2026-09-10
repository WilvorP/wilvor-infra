locals {
  historical_alarm_defaults = {
    comparison_operator = "GreaterThanOrEqualToThreshold"
    threshold           = 1
    evaluation_periods  = 1
    period              = 300
    statistic           = "Sum"
    treat_missing_data  = "notBreaching"
  }

  historical_firehose_metrics = [
    "IncomingRecords",
    "IncomingBytes",
    "ThrottledRecords",
    "SucceedProcessing.Records",
    "DeliveryToS3.Success",
    "DeliveryToS3.DataFreshness",
    "PartitionCount",
  ]
}

resource "aws_cloudwatch_dashboard" "historical_facts" {
  count = local.enabled ? 1 : 0

  dashboard_name = local.dashboard_name

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 2
        properties = {
          markdown = <<-EOT
            # Wilvor Historical Facts
            Facts path: EventBridge → Firehose → transform → S3. Geometry path: SIGMET DirectPut → Firehose → transform → S3.
            Firehose acceptance is **ACCEPTED_FOR_BOUNDED_DELIVERY_RETRY**, not **DURABLY_PERSISTED**.
            The SQS DLQ is Domain 2 only. Collection remains disabled until an approved enable.
          EOT
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 2
        width  = 12
        height = 6
        properties = {
          title  = "Facts Firehose ingest / throttle / S3"
          region = var.aws_region
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/Firehose", "IncomingRecords", "DeliveryStreamName", local.facts_stream_name],
            [".", "IncomingBytes", ".", "."],
            [".", "ThrottledRecords", ".", "."],
            [".", "SucceedProcessing.Records", ".", "."],
            [".", "DeliveryToS3.Success", ".", "."],
            [".", "PartitionCount", ".", "."],
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
          title  = "Facts Firehose freshness"
          region = var.aws_region
          stat   = "Maximum"
          period = 300
          metrics = [
            ["AWS/Firehose", "DeliveryToS3.DataFreshness", "DeliveryStreamName", local.facts_stream_name],
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
          title  = "Geometry Firehose ingest / throttle / S3"
          region = var.aws_region
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/Firehose", "IncomingRecords", "DeliveryStreamName", local.geometry_stream_name],
            [".", "IncomingBytes", ".", "."],
            [".", "ThrottledRecords", ".", "."],
            [".", "SucceedProcessing.Records", ".", "."],
            [".", "DeliveryToS3.Success", ".", "."],
            [".", "PartitionCount", ".", "."],
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
          title  = "Geometry Firehose freshness"
          region = var.aws_region
          stat   = "Maximum"
          period = 300
          metrics = [
            ["AWS/Firehose", "DeliveryToS3.DataFreshness", "DeliveryStreamName", local.geometry_stream_name],
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
          title  = "Transform Lambda"
          region = var.aws_region
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", local.transform_function_name],
            [".", "Errors", ".", "."],
            [".", "Throttles", ".", "."],
            [".", "Duration", ".", ".", { stat = "Average" }],
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
          title  = "Historical EventBridge rules"
          region = var.aws_region
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/Events", "Invocations", "RuleName", "${var.name_prefix}-historical-facts-encounter"],
            [".", "FailedInvocations", ".", "."],
            [".", "Invocations", ".", "${var.name_prefix}-historical-facts-risk"],
            [".", "FailedInvocations", ".", "."],
            [".", "Invocations", ".", "${var.name_prefix}-historical-facts-hazard_version"],
            [".", "FailedInvocations", ".", "."],
            [".", "Invocations", ".", "${var.name_prefix}-historical-facts-control"],
            [".", "FailedInvocations", ".", "."],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 20
        width  = 12
        height = 6
        properties = {
          title  = "Domain 2 DLQ"
          region = var.aws_region
          stat   = "Maximum"
          period = 300
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", "${var.name_prefix}-historical-facts-dlq"],
            [".", "ApproximateAgeOfOldestMessage", ".", "."],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 20
        width  = 12
        height = 6
        properties = {
          title  = "Producer historical-source and geometry"
          region = var.aws_region
          stat   = "Sum"
          period = 300
          metrics = [
            [local.wilvor_metric_namespace, "HistoricalSourcePutFailure", "Environment", local.historical_environment, "Pipeline", "encounter", "Component", "encounter_processor", "Stage", "historical_source"],
            [local.wilvor_metric_namespace, "HistoricalSourcePutFailure", "Environment", local.historical_environment, "Pipeline", "risk", "Component", "risk_processor", "Stage", "historical_source"],
            [local.wilvor_metric_namespace, "HistoricalSourcePutFailure", "Environment", local.historical_environment, "Pipeline", "sigmet", "Component", "sigmet_processor", "Stage", "historical_source"],
            [local.wilvor_metric_namespace, "HistoricalGeometryPutSuccess", "Environment", local.historical_environment, "Pipeline", "sigmet", "Component", "sigmet_processor", "Stage", "historical_geometry"],
            [local.wilvor_metric_namespace, "HistoricalGeometryPutFailure", "Environment", local.historical_environment, "Pipeline", "sigmet", "Component", "sigmet_processor", "Stage", "historical_geometry"],
            [local.wilvor_metric_namespace, "HistoricalGeometryOversized", "Environment", local.historical_environment, "Pipeline", "sigmet", "Component", "sigmet_processor", "Stage", "historical_geometry"],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 26
        width  = 12
        height = 6
        properties = {
          title  = "Coverage control"
          region = var.aws_region
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", local.coverage_function_name],
            [".", "Errors", ".", "."],
            [local.wilvor_metric_namespace, "CoverageControlFailure", "Environment", local.historical_environment, "Pipeline", "historical_facts", "Component", "coverage_control", "Stage", "collect"],
            [local.wilvor_metric_namespace, "CoverageProbeEmit", "Environment", local.historical_environment, "Pipeline", "historical_facts", "Component", "coverage_control", "Stage", "collect"],
            [local.wilvor_metric_namespace, "CoverageIntervalWritten", "Environment", local.historical_environment, "Pipeline", "historical_facts", "Component", "coverage_control", "Stage", "collect"],
            [local.wilvor_metric_namespace, "DurableGapWritten", "Environment", local.historical_environment, "Pipeline", "historical_facts", "Component", "coverage_control", "Stage", "collect"],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 26
        width  = 12
        height = 6
        properties = {
          title  = "Cost drivers (usage proxies)"
          region = var.aws_region
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/Firehose", "IncomingBytes", "DeliveryStreamName", local.facts_stream_name],
            [".", "IncomingRecords", ".", "."],
            [".", "IncomingBytes", ".", local.geometry_stream_name],
            [".", "IncomingRecords", ".", "."],
            ["AWS/Lambda", "Invocations", "FunctionName", local.transform_function_name],
            [".", "Duration", ".", ".", { stat = "Sum" }],
            ["AWS/Lambda", "Invocations", "FunctionName", local.coverage_function_name],
            ["AWS/Lambda", "Invocations", "FunctionName", local.dlq_consumer_function_name],
          ]
        }
      },
      {
        type   = "text"
        x      = 0
        y      = 32
        width  = 24
        height = 2
        properties = {
          markdown = <<-EOT
            Cost drivers (no billed prices): Firehose IncomingBytes/Records, Lambda invocations/duration (transform, coverage, DLQ consumer), EventBridge rule invocations, DLQ depth, S3 BucketSizeBytes/NumberOfObjects when available.
            CloudWatch logs retain 3 days. S3 facts and metadata retain 365 days. Firehose buffers 128 MiB / 900 s.
          EOT
        }
      },
    ]
  })
}

resource "aws_cloudwatch_metric_alarm" "historical_failed_invocations" {
  for_each = local.enabled ? toset(["encounter", "risk", "hazard_version", "control"]) : toset([])

  alarm_name          = "${var.name_prefix}-historical-facts-${each.key}-failed-invocations"
  alarm_description   = "Historical EventBridge ${each.key} rule reported FailedInvocations."
  namespace           = "AWS/Events"
  metric_name         = "FailedInvocations"
  comparison_operator = local.historical_alarm_defaults.comparison_operator
  threshold           = local.historical_alarm_defaults.threshold
  evaluation_periods  = local.historical_alarm_defaults.evaluation_periods
  period              = local.historical_alarm_defaults.period
  statistic           = local.historical_alarm_defaults.statistic
  treat_missing_data  = local.historical_alarm_defaults.treat_missing_data
  dimensions = {
    RuleName = "${var.name_prefix}-historical-facts-${each.key}"
  }
}

resource "aws_cloudwatch_metric_alarm" "historical_dlq_visible" {
  count = local.enabled ? 1 : 0

  alarm_name          = "${var.name_prefix}-historical-facts-dlq-visible"
  alarm_description   = "Historical facts Domain-2 DLQ has visible messages."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  comparison_operator = local.historical_alarm_defaults.comparison_operator
  threshold           = local.historical_alarm_defaults.threshold
  evaluation_periods  = local.historical_alarm_defaults.evaluation_periods
  period              = local.historical_alarm_defaults.period
  statistic           = "Maximum"
  treat_missing_data  = local.historical_alarm_defaults.treat_missing_data
  dimensions = {
    QueueName = "${var.name_prefix}-historical-facts-dlq"
  }
}

resource "aws_cloudwatch_metric_alarm" "historical_transform_errors" {
  count = local.enabled ? 1 : 0

  alarm_name          = "${var.name_prefix}-historical-facts-transform-errors"
  alarm_description   = "Historical facts transform Lambda reported Errors."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  comparison_operator = local.historical_alarm_defaults.comparison_operator
  threshold           = local.historical_alarm_defaults.threshold
  evaluation_periods  = local.historical_alarm_defaults.evaluation_periods
  period              = local.historical_alarm_defaults.period
  statistic           = local.historical_alarm_defaults.statistic
  treat_missing_data  = local.historical_alarm_defaults.treat_missing_data
  dimensions = {
    FunctionName = local.transform_function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "historical_firehose_throttles" {
  for_each = local.enabled ? local.firehose_streams : {}

  alarm_name          = "${var.name_prefix}-historical-${each.key}-firehose-throttles"
  alarm_description   = "Historical ${each.key} Firehose reported ThrottledRecords."
  namespace           = "AWS/Firehose"
  metric_name         = "ThrottledRecords"
  comparison_operator = local.historical_alarm_defaults.comparison_operator
  threshold           = local.historical_alarm_defaults.threshold
  evaluation_periods  = local.historical_alarm_defaults.evaluation_periods
  period              = local.historical_alarm_defaults.period
  statistic           = local.historical_alarm_defaults.statistic
  treat_missing_data  = local.historical_alarm_defaults.treat_missing_data
  dimensions = {
    DeliveryStreamName = each.value
  }
}

resource "aws_cloudwatch_metric_alarm" "historical_firehose_freshness" {
  for_each = local.enabled ? local.firehose_streams : {}

  alarm_name          = "${var.name_prefix}-historical-${each.key}-firehose-freshness"
  alarm_description   = "Historical ${each.key} Firehose DeliveryToS3.DataFreshness exceeded 1800s (2x 900s buffer)."
  namespace           = "AWS/Firehose"
  metric_name         = "DeliveryToS3.DataFreshness"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 1800
  evaluation_periods  = 1
  period              = 300
  statistic           = "Maximum"
  treat_missing_data  = "notBreaching"
  dimensions = {
    DeliveryStreamName = each.value
  }
}

resource "aws_cloudwatch_metric_alarm" "historical_coverage_errors" {
  count = local.enabled ? 1 : 0

  alarm_name          = "${var.name_prefix}-historical-facts-coverage-errors"
  alarm_description   = "Historical coverage_control Lambda reported Errors."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  comparison_operator = local.historical_alarm_defaults.comparison_operator
  threshold           = local.historical_alarm_defaults.threshold
  evaluation_periods  = local.historical_alarm_defaults.evaluation_periods
  period              = local.historical_alarm_defaults.period
  statistic           = local.historical_alarm_defaults.statistic
  treat_missing_data  = local.historical_alarm_defaults.treat_missing_data
  dimensions = {
    FunctionName = local.coverage_function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "historical_geometry_put_failure" {
  count = local.enabled ? 1 : 0

  alarm_name          = "${var.name_prefix}-historical-facts-geometry-put-failure"
  alarm_description   = "SIGMET reported HistoricalGeometryPutFailure."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "HistoricalGeometryPutFailure"
  comparison_operator = local.historical_alarm_defaults.comparison_operator
  threshold           = local.historical_alarm_defaults.threshold
  evaluation_periods  = local.historical_alarm_defaults.evaluation_periods
  period              = local.historical_alarm_defaults.period
  statistic           = local.historical_alarm_defaults.statistic
  treat_missing_data  = local.historical_alarm_defaults.treat_missing_data
  dimensions = {
    Environment = local.historical_environment
    Pipeline    = "sigmet"
    Component   = "sigmet_processor"
    Stage       = "historical_geometry"
  }
}

resource "aws_cloudwatch_metric_alarm" "historical_geometry_oversized" {
  count = local.enabled ? 1 : 0

  alarm_name          = "${var.name_prefix}-historical-facts-geometry-oversized"
  alarm_description   = "SIGMET reported HistoricalGeometryOversized."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "HistoricalGeometryOversized"
  comparison_operator = local.historical_alarm_defaults.comparison_operator
  threshold           = local.historical_alarm_defaults.threshold
  evaluation_periods  = local.historical_alarm_defaults.evaluation_periods
  period              = local.historical_alarm_defaults.period
  statistic           = local.historical_alarm_defaults.statistic
  treat_missing_data  = local.historical_alarm_defaults.treat_missing_data
  dimensions = {
    Environment = local.historical_environment
    Pipeline    = "sigmet"
    Component   = "sigmet_processor"
    Stage       = "historical_geometry"
  }
}

resource "aws_cloudwatch_metric_alarm" "historical_source_put_failure" {
  for_each = local.enabled ? {
    encounter = {
      pipeline  = "encounter"
      component = "encounter_processor"
    }
    risk = {
      pipeline  = "risk"
      component = "risk_processor"
    }
    hazard = {
      pipeline  = "sigmet"
      component = "sigmet_processor"
    }
  } : {}

  alarm_name          = "${var.name_prefix}-historical-facts-${each.key}-source-put-failure"
  alarm_description   = "${each.value.pipeline} reported HistoricalSourcePutFailure."
  namespace           = local.wilvor_metric_namespace
  metric_name         = "HistoricalSourcePutFailure"
  comparison_operator = local.historical_alarm_defaults.comparison_operator
  threshold           = local.historical_alarm_defaults.threshold
  evaluation_periods  = local.historical_alarm_defaults.evaluation_periods
  period              = local.historical_alarm_defaults.period
  statistic           = local.historical_alarm_defaults.statistic
  treat_missing_data  = local.historical_alarm_defaults.treat_missing_data
  dimensions = {
    Environment = local.historical_environment
    Pipeline    = each.value.pipeline
    Component   = each.value.component
    Stage       = "historical_source"
  }
}
