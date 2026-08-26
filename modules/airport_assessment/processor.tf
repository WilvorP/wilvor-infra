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
  name = "${var.name_prefix}-airport-assessment-processor-lambda-role"

  assume_role_policy = (
    data.aws_iam_policy_document.processor_assume_role.json
  )

  tags = var.tags
}

data "aws_iam_policy_document" "processor" {

  statement {
    sid = "ReadRiskResults"

    actions = [
      "dynamodb:GetItem"
    ]

    resources = [
      var.risk_results_table_arn
    ]
  }

  statement {
    sid = "ReadAircraftCurrentState"

    actions = [
      "dynamodb:GetItem"
    ]

    resources = [
      var.aircraft_current_state_table_arn
    ]
  }

  statement {
    sid = "ReadAirportStatus"

    actions = [
      "dynamodb:Scan",
      "dynamodb:GetItem"
    ]

    resources = [
      var.airport_status_table_arn
    ]
  }

  statement {
    sid = "ReadTafForecastPeriods"

    actions = [
      "dynamodb:Query"
    ]

    resources = [
      var.taf_forecast_periods_table_arn,
      "${var.taf_forecast_periods_table_arn}/index/${var.taf_station_period_index_name}"
    ]
  }

  statement {
    sid = "WriteAirportAssessments"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem"
    ]

    resources = [
      aws_dynamodb_table.airport_assessment.arn
    ]
  }

  statement {
    sid = "PublishAssessmentEvents"

    actions = [
      "events:PutEvents"
    ]

    resources = [
      var.event_bus_arn
    ]
  }

  statement {
    sid = "WriteLogs"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]

    resources = [
      "${aws_cloudwatch_log_group.processor.arn}:*"
    ]
  }
}

resource "aws_iam_role_policy" "processor" {
  name = "${var.name_prefix}-airport-assessment-processor-policy"
  role = aws_iam_role.processor.id

  policy = (
    data.aws_iam_policy_document.processor.json
  )
}

resource "aws_cloudwatch_log_group" "processor" {
  name = (
    "/aws/lambda/${var.name_prefix}-airport-assessment-processor"
  )

  retention_in_days = var.log_retention_days

  tags = var.tags
}

resource "aws_lambda_function" "processor" {
  function_name = (
    "${var.name_prefix}-airport-assessment-processor"
  )

  filename         = var.processor_zip_path
  source_code_hash = filebase64sha256(var.processor_zip_path)

  role    = aws_iam_role.processor.arn
  handler = "app.lambda_handler"
  runtime = "python3.12"

  memory_size = 512
  timeout     = 60

  environment {
    variables = {
      RISK_RESULTS_TABLE_NAME = (
        var.risk_results_table_name
      )

      AIRCRAFT_CURRENT_STATE_TABLE_NAME = (
        var.aircraft_current_state_table_name
      )

      AIRPORT_STATUS_TABLE_NAME = (
        var.airport_status_table_name
      )

      TAF_FORECAST_PERIODS_TABLE_NAME = (
        var.taf_forecast_periods_table_name
      )

      TAF_STATION_PERIOD_INDEX_NAME = (
        var.taf_station_period_index_name
      )

      AIRPORT_ASSESSMENT_TABLE_NAME = (
        aws_dynamodb_table.airport_assessment.name
      )

      EVENT_BUS_NAME = var.event_bus_name

      SEARCH_RADIUS_NM        = "250"
      MAX_CANDIDATES          = "10"
      ETA_UNCERTAINTY_MINUTES = "10"
      RETENTION_SECONDS       = "86400"

      ASSESSMENT_RULESET_VERSION = (
        "wilvor.airport-assessment.ruleset.v1"
      )

      SCHEMA_VERSION = (
        "wilvor.airport_assessment.v1"
      )
    }
  }

  depends_on = [
    aws_iam_role_policy.processor,
    aws_cloudwatch_log_group.processor
  ]

  tags = merge(
    var.tags,
    {
      Component = "airport-assessment"
    }
  )
}

resource "aws_cloudwatch_event_rule" "risk_updated" {
  name = (
    "${var.name_prefix}-airport-assessment-risk-updated"
  )

  event_bus_name = var.event_bus_name

  state = (
    var.enable_event_trigger
    ? "ENABLED"
    : "DISABLED"
  )

  event_pattern = jsonencode({
    source = [
      "wilvor.risk"
    ]

    detail-type = [
      "risk.updated"
    ]
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "processor" {
  rule           = aws_cloudwatch_event_rule.risk_updated.name
  event_bus_name = var.event_bus_name

  arn       = aws_lambda_function.processor.arn
  target_id = "AirportAssessmentProcessor"
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id = "AllowRiskUpdatedForAirportAssessment"

  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.processor.function_name
  principal     = "events.amazonaws.com"

  source_arn = aws_cloudwatch_event_rule.risk_updated.arn
}