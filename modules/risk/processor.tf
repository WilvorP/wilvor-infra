data "aws_iam_policy_document" "risk_processor_assume_role" {
  statement {
    actions = [
      "sts:AssumeRole"
    ]

    principals {
      type = "Service"

      identifiers = [
        "lambda.amazonaws.com"
      ]
    }
  }
}


resource "aws_iam_role" "risk_processor" {
  name = "${var.name_prefix}-risk-processor-lambda-role"

  assume_role_policy = (
    data.aws_iam_policy_document
    .risk_processor_assume_role
    .json
  )

  tags = var.tags
}


data "aws_iam_policy_document" "risk_processor" {
  statement {
    sid = "ReadAircraftHazardEncounter"

    actions = [
      "dynamodb:GetItem"
    ]

    resources = [
      var.aircraft_hazard_encounter_table_arn
    ]
  }

  statement {
    sid = "WriteRiskResults"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem"
    ]

    resources = [
      aws_dynamodb_table.risk_results.arn
    ]
  }

  statement {
    sid = "PublishRiskEvents"

    actions = [
      "events:PutEvents"
    ]

    resources = [
      var.event_bus_arn
    ]
  }

  statement {
    sid = "PublishRiskMetrics"

    actions = [
      "cloudwatch:PutMetricData"
    ]

    resources = [
      "*"
    ]
  }

  statement {
    sid = "WriteLambdaLogs"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]

    resources = [
      "${aws_cloudwatch_log_group.risk_processor.arn}:*"
    ]
  }
}


resource "aws_iam_role_policy" "risk_processor" {
  name = "${var.name_prefix}-risk-processor-lambda-policy"

  role = (
    aws_iam_role
    .risk_processor
    .id
  )

  policy = (
    data.aws_iam_policy_document
    .risk_processor
    .json
  )
}


resource "aws_cloudwatch_log_group" "risk_processor" {
  name = (
    "/aws/lambda/${var.name_prefix}-risk-processor"
  )

  retention_in_days = (
    var.log_retention_days
  )

  tags = var.tags
}


resource "aws_lambda_function" "risk_processor" {
  function_name = (
    "${var.name_prefix}-risk-processor"
  )

  filename = (
    var.risk_processor_zip_path
  )

  source_code_hash = filebase64sha256(
    var.risk_processor_zip_path
  )

  role = (
    aws_iam_role
    .risk_processor
    .arn
  )

  handler = "app.lambda_handler"
  runtime = "python3.12"

  memory_size = 256
  timeout     = 30

  environment {
    variables = {
      ENVIRONMENT = (
        lookup(
          var.tags,
          "Environment",
          "dev"
        )
      )

      AIRCRAFT_HAZARD_ENCOUNTER_TABLE_NAME = (
        var.aircraft_hazard_encounter_table_name
      )

      RISK_RESULTS_TABLE_NAME = (
        aws_dynamodb_table
        .risk_results
        .name
      )

      EVENT_BUS_NAME = (
        var.event_bus_name
      )

      RISK_SCHEMA_VERSION = (
        "wilvor.risk_results.v4.0"
      )

      SCORING_RULESET_VERSION = (
        "wilvor.risk.ruleset.v2"
      )

      SCORING_CONFIG_VERSION = (
        "wilvor.risk.config.dev.v1"
      )

      RISK_RETENTION_SECONDS = (
        "86400"
      )
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.risk_processor,
    aws_iam_role_policy.risk_processor
  ]

  tags = merge(
    var.tags,
    {
      Name      = "${var.name_prefix}-risk-processor"
      Component = "risk"
    }
  )
}


resource "aws_cloudwatch_event_rule" "encounter_changes" {
  name = (
    "${var.name_prefix}-risk-encounter-changes"
  )

  description = (
    "Runs risk scoring after aircraft hazard encounter updates or resolution."
  )

  event_bus_name = (
    var.event_bus_name
  )

  state = (
    var.enable_risk_event_trigger
    ? "ENABLED"
    : "DISABLED"
  )

  event_pattern = jsonencode({
    source = [
      "wilvor.encounter"
    ]

    detail-type = [
      "encounter.updated",
      "encounter.resolved"
    ]
  })

  tags = var.tags
}


resource "aws_cloudwatch_event_target" "risk_processor" {
  rule = (
    aws_cloudwatch_event_rule
    .encounter_changes
    .name
  )

  event_bus_name = (
    var.event_bus_name
  )

  arn = (
    aws_lambda_function
    .risk_processor
    .arn
  )

  target_id = (
    "RiskProcessorLambda"
  )
}


resource "aws_lambda_permission" "allow_eventbridge_risk_processor" {
  statement_id = (
    "AllowExecutionFromEncounterEvents"
  )

  action = (
    "lambda:InvokeFunction"
  )

  function_name = (
    aws_lambda_function
    .risk_processor
    .function_name
  )

  principal = (
    "events.amazonaws.com"
  )

  source_arn = (
    aws_cloudwatch_event_rule
    .encounter_changes
    .arn
  )
}