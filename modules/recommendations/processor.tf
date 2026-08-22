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
  name               = "${var.name_prefix}-recommendation-processor-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.processor_assume_role.json
  tags               = var.tags
}

data "aws_iam_policy_document" "processor" {
  statement {
    sid       = "ReadRiskResults"
    actions   = ["dynamodb:GetItem"]
    resources = [var.risk_results_table_arn]
  }

  statement {
    sid       = "ReadAirportAssessments"
    actions   = ["dynamodb:Query"]
    resources = [var.airport_assessment_table_arn]
  }

  statement {
    sid = "WriteRecommendations"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem"
    ]
    resources = [aws_dynamodb_table.recommendations.arn]
  }

  statement {
    sid       = "PublishRecommendationEvents"
    actions   = ["events:PutEvents"]
    resources = [var.event_bus_arn]
  }

  statement {
    sid       = "PublishRecommendationMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
  }

  statement {
    sid = "WriteRecommendationLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]
    resources = ["${aws_cloudwatch_log_group.processor.arn}:*"]
  }
}

resource "aws_iam_role_policy" "processor" {
  name   = "${var.name_prefix}-recommendation-processor-policy"
  role   = aws_iam_role.processor.id
  policy = data.aws_iam_policy_document.processor.json
}

resource "aws_cloudwatch_log_group" "processor" {
  name              = "/aws/lambda/${var.name_prefix}-recommendation-processor"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_lambda_function" "processor" {
  function_name    = "${var.name_prefix}-recommendation-processor"
  filename         = var.processor_zip_path
  source_code_hash = filebase64sha256(var.processor_zip_path)
  role             = aws_iam_role.processor.arn
  handler          = "app.lambda_handler"
  runtime          = "python3.12"
  memory_size      = 256
  timeout          = 30

  environment {
    variables = {
      RISK_RESULTS_TABLE_NAME       = var.risk_results_table_name
      AIRPORT_ASSESSMENT_TABLE_NAME = var.airport_assessment_table_name
      RECOMMENDATIONS_TABLE_NAME    = aws_dynamodb_table.recommendations.name
      EVENT_BUS_NAME                = var.event_bus_name
      RETENTION_SECONDS             = "86400"
      TOP_CANDIDATE_COUNT           = "5"
      RULESET_VERSION               = "wilvor.recommendation.ruleset.v1"
      SCHEMA_VERSION                = "wilvor.recommendation.v4.0"
    }
  }

  depends_on = [
    aws_iam_role_policy.processor,
    aws_cloudwatch_log_group.processor
  ]

  tags = merge(
    var.tags,
    {
      Component = "recommendations"
    }
  )
}

resource "aws_cloudwatch_event_rule" "risk_updated" {
  name           = "${var.name_prefix}-recommendation-risk-updated"
  event_bus_name = var.event_bus_name
  state          = var.enable_event_trigger ? "ENABLED" : "DISABLED"

  event_pattern = jsonencode({
    source      = ["wilvor.risk"]
    detail-type = ["risk.updated"]
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "risk_updated" {
  rule           = aws_cloudwatch_event_rule.risk_updated.name
  event_bus_name = var.event_bus_name
  arn            = aws_lambda_function.processor.arn
  target_id      = "RecommendationProcessorFromRisk"
}

resource "aws_lambda_permission" "risk_updated" {
  statement_id  = "AllowRiskUpdatedForRecommendation"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.processor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.risk_updated.arn
}

resource "aws_cloudwatch_event_rule" "assessment_completed" {
  name           = "${var.name_prefix}-recommendation-assessment-completed"
  event_bus_name = var.event_bus_name
  state          = var.enable_event_trigger ? "ENABLED" : "DISABLED"

  event_pattern = jsonencode({
    source      = ["wilvor.assessment"]
    detail-type = ["airport.assessment.completed"]
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "assessment_completed" {
  rule           = aws_cloudwatch_event_rule.assessment_completed.name
  event_bus_name = var.event_bus_name
  arn            = aws_lambda_function.processor.arn
  target_id      = "RecommendationProcessorFromAssessment"
}

resource "aws_lambda_permission" "assessment_completed" {
  statement_id  = "AllowAssessmentCompletedForRecommendation"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.processor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.assessment_completed.arn
}
