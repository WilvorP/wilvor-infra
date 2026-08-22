data "aws_iam_policy_document" "processor_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "processor" {
  name               = "${var.name_prefix}-alert-lifecycle-processor-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.processor_assume_role.json
  tags               = var.tags
}

data "aws_iam_policy_document" "processor" {
  statement {
    sid       = "ReadRecommendations"
    actions   = ["dynamodb:GetItem"]
    resources = [var.recommendations_table_arn]
  }

  statement {
    sid = "ReadRiskResults"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:Query"
    ]
    resources = [
      var.risk_results_table_arn,
      "${var.risk_results_table_arn}/index/${var.risk_results_encounter_index_name}"
    ]
  }

  statement {
    sid = "ManageActiveAlerts"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query"
    ]
    resources = [
      aws_dynamodb_table.active_alerts.arn,
      "${aws_dynamodb_table.active_alerts.arn}/index/aircraft_id-updated_at_epoch-index"
    ]
  }

  statement {
    sid       = "PublishAlertEvents"
    actions   = ["events:PutEvents"]
    resources = [var.event_bus_arn]
  }

  statement {
    sid       = "PublishAlertMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
  }

  statement {
    sid = "WriteAlertLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]
    resources = ["${aws_cloudwatch_log_group.processor.arn}:*"]
  }
}

resource "aws_iam_role_policy" "processor" {
  name   = "${var.name_prefix}-alert-lifecycle-processor-policy"
  role   = aws_iam_role.processor.id
  policy = data.aws_iam_policy_document.processor.json
}

resource "aws_cloudwatch_log_group" "processor" {
  name              = "/aws/lambda/${var.name_prefix}-alert-lifecycle-processor"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_lambda_function" "processor" {
  function_name    = "${var.name_prefix}-alert-lifecycle-processor"
  filename         = var.processor_zip_path
  source_code_hash = filebase64sha256(var.processor_zip_path)
  role             = aws_iam_role.processor.arn
  handler          = "app.lambda_handler"
  runtime          = "python3.12"
  memory_size      = 256
  timeout          = 30

  environment {
    variables = {
      RECOMMENDATIONS_TABLE_NAME        = var.recommendations_table_name
      RISK_RESULTS_TABLE_NAME           = var.risk_results_table_name
      RISK_RESULTS_ENCOUNTER_INDEX_NAME = var.risk_results_encounter_index_name
      ACTIVE_ALERTS_TABLE_NAME          = aws_dynamodb_table.active_alerts.name
      AIRCRAFT_ALERT_INDEX_NAME         = "aircraft_id-updated_at_epoch-index"
      EVENT_BUS_NAME                    = var.event_bus_name
      RETENTION_SECONDS                 = "86400"
      SCHEMA_VERSION                    = "wilvor.active_alert.v4.0"
    }
  }

  depends_on = [
    aws_iam_role_policy.processor,
    aws_cloudwatch_log_group.processor
  ]

  tags = merge(
    var.tags,
    {
      Component = "alerts"
    }
  )
}

resource "aws_cloudwatch_event_rule" "recommendation_updated" {
  name           = "${var.name_prefix}-alerts-recommendation-updated"
  event_bus_name = var.event_bus_name
  state          = var.enable_event_trigger ? "ENABLED" : "DISABLED"

  event_pattern = jsonencode({
    source      = ["wilvor.recommendation"]
    detail-type = ["recommendation.updated"]
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "recommendation_updated" {
  rule           = aws_cloudwatch_event_rule.recommendation_updated.name
  event_bus_name = var.event_bus_name
  arn            = aws_lambda_function.processor.arn
  target_id      = "AlertProcessorFromRecommendation"
}

resource "aws_lambda_permission" "recommendation_updated" {
  statement_id  = "AllowRecommendationUpdatedForAlerts"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.processor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.recommendation_updated.arn
}

resource "aws_cloudwatch_event_rule" "risk_resolved" {
  name           = "${var.name_prefix}-alerts-risk-resolved"
  event_bus_name = var.event_bus_name
  state          = var.enable_event_trigger ? "ENABLED" : "DISABLED"

  event_pattern = jsonencode({
    source      = ["wilvor.risk"]
    detail-type = ["risk.resolved"]
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "risk_resolved" {
  rule           = aws_cloudwatch_event_rule.risk_resolved.name
  event_bus_name = var.event_bus_name
  arn            = aws_lambda_function.processor.arn
  target_id      = "AlertProcessorFromRiskResolution"
}

resource "aws_lambda_permission" "risk_resolved" {
  statement_id  = "AllowRiskResolvedForAlerts"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.processor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.risk_resolved.arn
}

resource "aws_cloudwatch_event_rule" "encounter_resolved" {
  name           = "${var.name_prefix}-alerts-encounter-resolved"
  event_bus_name = var.event_bus_name
  state          = var.enable_event_trigger ? "ENABLED" : "DISABLED"

  event_pattern = jsonencode({
    source      = ["wilvor.encounter"]
    detail-type = ["encounter.resolved"]
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "encounter_resolved" {
  rule           = aws_cloudwatch_event_rule.encounter_resolved.name
  event_bus_name = var.event_bus_name
  arn            = aws_lambda_function.processor.arn
  target_id      = "AlertProcessorFromEncounterResolution"
}

resource "aws_lambda_permission" "encounter_resolved" {
  statement_id  = "AllowEncounterResolvedForAlerts"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.processor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.encounter_resolved.arn
}
