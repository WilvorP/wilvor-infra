locals {
  ai_event_triggers = {
    risk = {
      source       = "wilvor.risk"
      detail_types = ["risk.updated"]
    }
    recommendation = {
      source       = "wilvor.recommendation"
      detail_types = ["recommendation.updated"]
    }
    alert = {
      source = "wilvor.alert"
      detail_types = [
        "alert.updated",
        "alert.resolved",
      ]
    }
    airport = {
      source       = "wilvor.airport"
      detail_types = ["airport.status.updated"]
    }
  }
}

resource "aws_sqs_queue" "event_dlq" {
  name = "${var.name_prefix}-ai-copilot-event-dlq"

  message_retention_seconds = 345600
  sqs_managed_sse_enabled   = true

  tags = merge(
    var.tags,
    {
      Component = "ai-copilot"
      QueueRole = "event-failure-dlq"
    }
  )
}

resource "aws_cloudwatch_event_rule" "ai_events" {
  for_each = local.ai_event_triggers

  name = (
    "${var.name_prefix}-ai-copilot-${each.key}"
  )
  description = (
    "Generate bounded Wilvor AI insight for ${each.key} changes."
  )
  state = (
    var.enable_event_triggers ? "ENABLED" : "DISABLED"
  )

  event_pattern = jsonencode({
    source      = [each.value.source]
    detail-type = each.value.detail_types
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "ai_events" {
  for_each = local.ai_event_triggers

  rule = aws_cloudwatch_event_rule.ai_events[
    each.key
  ].name
  arn = aws_lambda_function.ai_copilot.arn

  retry_policy {
    maximum_event_age_in_seconds = 3600
    maximum_retry_attempts       = 2
  }

  dead_letter_config {
    arn = aws_sqs_queue.event_dlq.arn
  }

  depends_on = [
    aws_sqs_queue_policy.event_dlq,
  ]
}

resource "aws_lambda_permission" "ai_events" {
  for_each = local.ai_event_triggers

  statement_id = (
    "AllowEventBridge${title(each.key)}"
  )
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ai_copilot.function_name
  principal     = "events.amazonaws.com"
  source_arn = aws_cloudwatch_event_rule.ai_events[
    each.key
  ].arn
}

resource "aws_cloudwatch_event_rule" "network_summary" {
  name = (
    "${var.name_prefix}-ai-copilot-network-summary-schedule"
  )
  description = (
    "Generate the bounded Wilvor network summary."
  )
  schedule_expression = (
    var.network_summary_schedule_expression
  )
  state = (
    var.enable_network_summary_schedule ? "ENABLED" : "DISABLED"
  )

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "network_summary" {
  rule = aws_cloudwatch_event_rule.network_summary.name
  arn  = aws_lambda_function.ai_copilot.arn

  retry_policy {
    maximum_event_age_in_seconds = 3600
    maximum_retry_attempts       = 2
  }

  dead_letter_config {
    arn = aws_sqs_queue.event_dlq.arn
  }

  depends_on = [
    aws_sqs_queue_policy.event_dlq,
  ]
}

resource "aws_lambda_permission" "network_summary" {
  statement_id  = "AllowEventBridgeNetworkSummary"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ai_copilot.function_name
  principal     = "events.amazonaws.com"
  source_arn = (
    aws_cloudwatch_event_rule.network_summary.arn
  )
}

data "aws_iam_policy_document" "event_dlq" {
  statement {
    sid = "AllowEventBridgeDelivery"
    actions = [
      "sqs:SendMessage",
    ]
    resources = [
      aws_sqs_queue.event_dlq.arn,
    ]

    principals {
      type = "Service"
      identifiers = [
        "events.amazonaws.com",
      ]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values = concat(
        [
          for rule in aws_cloudwatch_event_rule.ai_events :
          rule.arn
        ],
        [
          aws_cloudwatch_event_rule.network_summary.arn,
        ]
      )
    }
  }
}

resource "aws_sqs_queue_policy" "event_dlq" {
  queue_url = aws_sqs_queue.event_dlq.id
  policy    = data.aws_iam_policy_document.event_dlq.json
}

resource "aws_lambda_function_event_invoke_config" "ai_copilot" {
  function_name = aws_lambda_function.ai_copilot.function_name

  maximum_event_age_in_seconds = 3600
  maximum_retry_attempts       = 1

  destination_config {
    on_failure {
      destination = aws_sqs_queue.event_dlq.arn
    }
  }

  depends_on = [
    aws_iam_role_policy.lambda,
  ]
}
