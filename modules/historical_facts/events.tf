locals {
  historical_rules = {
    encounter = {
      source       = ["wilvor.encounter"]
      detail_types = ["encounter.updated", "encounter.resolved"]
    }
    risk = {
      source       = ["wilvor.risk"]
      detail_types = ["risk.updated", "risk.resolved"]
    }
    hazard_version = {
      source       = ["wilvor.weather"]
      detail_types = ["hazard.materialized"]
    }
  }
}

resource "aws_sqs_queue" "historical_facts_dlq" {
  count = local.enabled ? 1 : 0

  name                       = "${var.name_prefix}-historical-facts-dlq"
  sqs_managed_sse_enabled    = true
  message_retention_seconds  = var.dlq_message_retention_seconds
  visibility_timeout_seconds = 30

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-historical-facts-dlq"
  })
}

resource "aws_cloudwatch_event_rule" "historical" {
  for_each = local.enabled ? local.historical_rules : {}

  name           = "${var.name_prefix}-historical-facts-${each.key}"
  description    = "Historical facts collection for ${each.key} (Domain 2 Firehose target)"
  event_bus_name = var.event_bus_name

  event_pattern = jsonencode({
    source      = each.value.source
    detail-type = each.value.detail_types
  })

  tags = local.common_tags
}

data "aws_iam_policy_document" "historical_facts_dlq" {
  count = local.enabled ? 1 : 0

  statement {
    sid    = "AllowHistoricalEventBridgeSendMessage"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    actions = [
      "sqs:SendMessage",
    ]

    resources = [
      aws_sqs_queue.historical_facts_dlq[0].arn,
    ]

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values = [
        for rule in aws_cloudwatch_event_rule.historical : rule.arn
      ]
    }
  }
}

resource "aws_sqs_queue_policy" "historical_facts_dlq" {
  count = local.enabled ? 1 : 0

  queue_url = aws_sqs_queue.historical_facts_dlq[0].id
  policy    = data.aws_iam_policy_document.historical_facts_dlq[0].json
}

resource "aws_cloudwatch_event_target" "historical_facts_firehose" {
  for_each = local.enabled ? local.historical_rules : {}

  rule           = aws_cloudwatch_event_rule.historical[each.key].name
  event_bus_name = var.event_bus_name
  target_id      = "HistoricalFactsFirehose"
  arn            = aws_kinesis_firehose_delivery_stream.historical["facts"].arn
  role_arn       = aws_iam_role.eventbridge[0].arn

  retry_policy {
    maximum_event_age_in_seconds = 86400
    maximum_retry_attempts       = 185
  }

  dead_letter_config {
    arn = aws_sqs_queue.historical_facts_dlq[0].arn
  }
}
