locals {
  airport_status_materializer_function_name = "${var.name_prefix}-airport-status-materializer"
}

data "aws_iam_policy_document" "airport_status_materializer_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "airport_status_materializer" {
  name               = "${var.name_prefix}-airport-status-materializer-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.airport_status_materializer_assume_role.json

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-airport-status-materializer-lambda-role"
    Component = "airport-status"
  })
}

resource "aws_cloudwatch_log_group" "airport_status_materializer" {
  name              = "/aws/lambda/${local.airport_status_materializer_function_name}"
  retention_in_days = var.log_retention_days

  tags = merge(var.tags, {
    Name      = "/aws/lambda/${local.airport_status_materializer_function_name}"
    Component = "airport-status"
  })
}

data "aws_iam_policy_document" "airport_status_materializer" {
  statement {
    sid    = "WriteLambdaLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "${aws_cloudwatch_log_group.airport_status_materializer.arn}:*",
    ]
  }

  statement {
    sid    = "ReadSourceWeatherAndStationTables"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:Scan",
    ]

    resources = [
      var.station_reference_table_arn,
      var.metar_latest_table_arn,
      var.taf_latest_table_arn,
    ]
  }

  statement {
    sid    = "WriteAirportStatus"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
    ]

    resources = [
      aws_dynamodb_table.airport_status.arn,
      "${aws_dynamodb_table.airport_status.arn}/index/*",
    ]
  }
  statement {
    sid    = "PublishAirportStatusEvents"
    effect = "Allow"

    actions = [
      "events:PutEvents",
    ]

    resources = [
      var.event_bus_arn,
    ]
  }
}

resource "aws_iam_role_policy" "airport_status_materializer" {
  name   = "${var.name_prefix}-airport-status-materializer-lambda-policy"
  role   = aws_iam_role.airport_status_materializer.id
  policy = data.aws_iam_policy_document.airport_status_materializer.json
}

resource "aws_lambda_function" "airport_status_materializer" {
  function_name    = local.airport_status_materializer_function_name
  role             = aws_iam_role.airport_status_materializer.arn
  filename         = var.airport_status_materializer_zip_path
  source_code_hash = filebase64sha256(var.airport_status_materializer_zip_path)

  runtime = "python3.12"
  handler = "app.lambda_handler"

  memory_size = var.lambda_memory_size
  timeout     = var.lambda_timeout_seconds

  environment {
    variables = {
      AIRPORT_STATUS_TABLE_NAME    = aws_dynamodb_table.airport_status.name
      STATION_REFERENCE_TABLE_NAME = var.station_reference_table_name
      METAR_LATEST_TABLE_NAME      = var.metar_latest_table_name
      TAF_LATEST_TABLE_NAME        = var.taf_latest_table_name
      EVENT_BUS_NAME               = var.event_bus_name
      AIRPORT_STATUS_EVENT_SOURCE  = "wilvor.airport"
      PUBLISH_EVENTS               = "true"
      SCHEMA_VERSION               = "airport_status.v1"
      AIRPORT_STATUS_TTL_SECONDS   = tostring(var.airport_status_ttl_seconds)
      METAR_FRESH_SECONDS          = tostring(var.metar_fresh_seconds)
      TAF_FRESH_SECONDS            = tostring(var.taf_fresh_seconds)
      ENVIRONMENT                  = var.environment
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.airport_status_materializer,
    aws_iam_role_policy.airport_status_materializer,
  ]

  tags = merge(var.tags, {
    Name      = local.airport_status_materializer_function_name
    Component = "airport-status"
  })
}

resource "aws_cloudwatch_event_rule" "airport_status_updated" {
  name        = "${var.name_prefix}-airport-status-updated"
  description = "Matches AirportStatus updates for downstream airport assessment processing"

  event_pattern = jsonencode({
    source      = ["wilvor.airport"]
    detail-type = ["airport.status.updated"]
  })

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-airport-status-updated"
    Component = "airport-status"
  })
}

resource "aws_cloudwatch_event_rule" "airport_status_weather_updates" {
  name        = "${var.name_prefix}-airport-status-weather-updates"
  description = "Materialize AirportStatus when METAR or TAF current weather changes"

  event_pattern = jsonencode({
    source = ["wilvor.weather"]
    detail-type = [
      "metar.updated",
      "taf.materialized",
    ]
  })

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-airport-status-weather-updates"
    Component = "airport-status"
  })
}

resource "aws_cloudwatch_event_target" "airport_status_materializer" {
  rule      = aws_cloudwatch_event_rule.airport_status_weather_updates.name
  target_id = "AirportStatusMaterializerLambda"
  arn       = aws_lambda_function.airport_status_materializer.arn
}

resource "aws_lambda_permission" "allow_eventbridge_airport_status_materializer" {
  statement_id  = "AllowExecutionFromEventBridgeAirportStatus"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.airport_status_materializer.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.airport_status_weather_updates.arn
}