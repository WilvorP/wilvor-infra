resource "aws_cloudwatch_log_group" "query_failures" {
  count = local.enabled ? 1 : 0

  name              = local.query_failure_log_group_name
  retention_in_days = 3

  tags = merge(local.common_tags, {
    Name = local.query_failure_log_group_name
  })
}

data "aws_iam_policy_document" "query_failure_logs" {
  count = local.enabled ? 1 : 0

  statement {
    sid    = "AllowEventBridgeToWriteHistoricalAnalyticsQueryFailureLogs"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "${aws_cloudwatch_log_group.query_failures[0].arn}:*",
    ]
  }
}

resource "aws_cloudwatch_log_resource_policy" "eventbridge_to_query_failure_logs" {
  count = local.enabled ? 1 : 0

  policy_name     = "${var.name_prefix}-eventbridge-historical-analytics-query-failures"
  policy_document = data.aws_iam_policy_document.query_failure_logs[0].json
}

resource "aws_cloudwatch_event_rule" "query_failures" {
  count = local.enabled ? 1 : 0

  name        = "${var.name_prefix}-historical-analytics-query-failures"
  description = "Best-effort operational monitoring of FAILED/CANCELED Athena queries in the Wilvor historical analytics workgroup. Not query-status or coverage authority."

  event_pattern = jsonencode({
    source      = ["aws.athena"]
    detail-type = ["Athena Query State Change"]
    detail = {
      workgroupName = [local.workgroup_name]
      currentState  = ["FAILED", "CANCELED"]
    }
  })

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "query_failures" {
  count = local.enabled ? 1 : 0

  rule      = aws_cloudwatch_event_rule.query_failures[0].name
  target_id = "HistoricalAnalyticsQueryFailureLogs"
  arn       = aws_cloudwatch_log_group.query_failures[0].arn

  depends_on = [
    aws_cloudwatch_log_resource_policy.eventbridge_to_query_failure_logs,
  ]
}

resource "aws_cloudwatch_log_metric_filter" "query_failures" {
  count = local.enabled ? 1 : 0

  name           = "${var.name_prefix}-historical-analytics-query-failed"
  log_group_name = aws_cloudwatch_log_group.query_failures[0].name
  pattern        = "{ $.detail.currentState = \"FAILED\" || $.detail.currentState = \"CANCELED\" }"

  metric_transformation {
    name      = local.query_failure_metric_name
    namespace = local.wilvor_metric_namespace
    value     = "1"
    unit      = "Count"

    dimensions = {
      WorkGroup = "$.detail.workgroupName"
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "query_failures" {
  count = local.enabled ? 1 : 0

  alarm_name          = "${var.name_prefix}-historical-analytics-query-failed"
  alarm_description   = "Wilvor historical analytics workgroup reported an Athena FAILED or CANCELED query. Operational signal only. Scan-cutoff and intentional cancellations may alarm. Not coverage or query-status authority."
  namespace           = local.wilvor_metric_namespace
  metric_name         = local.query_failure_metric_name
  comparison_operator = local.analytics_alarm_defaults.comparison_operator
  threshold           = local.analytics_alarm_defaults.threshold
  evaluation_periods  = local.analytics_alarm_defaults.evaluation_periods
  period              = local.analytics_alarm_defaults.period
  statistic           = local.analytics_alarm_defaults.statistic
  treat_missing_data  = local.analytics_alarm_defaults.treat_missing_data

  dimensions = {
    WorkGroup = local.workgroup_name
  }
}

resource "aws_cloudwatch_dashboard" "historical_analytics" {
  count = local.enabled ? 1 : 0

  dashboard_name = local.dashboard_name

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 3
        properties = {
          markdown = <<-EOT
            # Wilvor Historical Analytics
            Operational observability only. Canonical facts remain in persistent S3 (`envs/dev-historical-data`).
            Athena query results are **derived/disposable**. Athena is **NOT** coverage authority.
            `COUNT(*) = 0` is **NOT VERIFIED_ZERO**. `evaluate_collection_window` remains authoritative.
            Per-query scan cutoff: **10 GiB**. EventBridge Athena Query State Change delivery is **best effort**.
          EOT
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 3
        width  = 12
        height = 6
        properties = {
          title  = "Athena ProcessedBytes (cost-driver visibility, not billed price)"
          region = var.aws_region
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/Athena", "ProcessedBytes", "WorkGroup", local.workgroup_name],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 3
        width  = 12
        height = 6
        properties = {
          title  = "Athena query failures (FAILED/CANCELED, best effort)"
          region = var.aws_region
          stat   = "Sum"
          period = 300
          metrics = [
            [local.wilvor_metric_namespace, local.query_failure_metric_name, "WorkGroup", local.workgroup_name],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 9
        width  = 12
        height = 6
        properties = {
          title  = "Athena query timing (Average)"
          region = var.aws_region
          stat   = "Average"
          period = 300
          metrics = [
            ["AWS/Athena", "TotalExecutionTime", "WorkGroup", local.workgroup_name],
            [".", "EngineExecutionTime", ".", "."],
            [".", "QueryPlanningTime", ".", "."],
            [".", "QueryQueueTime", ".", "."],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 9
        width  = 12
        height = 6
        properties = {
          title  = "Athena query timing (Maximum)"
          region = var.aws_region
          stat   = "Maximum"
          period = 300
          metrics = [
            ["AWS/Athena", "TotalExecutionTime", "WorkGroup", local.workgroup_name],
            [".", "EngineExecutionTime", ".", "."],
            [".", "QueryPlanningTime", ".", "."],
            [".", "QueryQueueTime", ".", "."],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 15
        width  = 12
        height = 6
        properties = {
          title  = "Results bucket size (daily, derived/disposable)"
          region = var.aws_region
          stat   = "Average"
          period = 86400
          metrics = [
            ["AWS/S3", "BucketSizeBytes", "BucketName", local.results_bucket_name, "StorageType", "StandardStorage"],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 15
        width  = 12
        height = 6
        properties = {
          title  = "Results bucket objects (daily, derived/disposable)"
          region = var.aws_region
          stat   = "Average"
          period = 86400
          metrics = [
            ["AWS/S3", "NumberOfObjects", "BucketName", local.results_bucket_name, "StorageType", "AllStorageTypes"],
          ]
        }
      },
      {
        type   = "text"
        x      = 0
        y      = 21
        width  = 24
        height = 2
        properties = {
          markdown = <<-EOT
            Results S3 metrics are **daily** snapshots of the disposable 3-day lifecycle bucket. No objects is not a failure. No fake billed-price formula.
            FAILED and **CANCELED** (AWS spelling) may include scan-cutoff or intentional cancellation. There is no zero-query alarm and no SNS.
            This EventBridge path is not query-status authority. Future executors must use GetQueryExecution. Coverage evaluation remains Phase 2A.1.
          EOT
        }
      },
    ]
  })
}
