data "aws_iam_policy_document" "sigmet_hsc_processor_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "sigmet_hsc_processor_lambda" {
  name               = "${var.name_prefix}-hsc-processor-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.sigmet_hsc_processor_assume_role.json

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-hsc-processor-lambda-role"
    Component = "weather-processing"
  })
}

resource "aws_cloudwatch_log_group" "sigmet_hazard_station_candidates_processor" {
  name              = "/aws/lambda/${var.name_prefix}-sigmet-hazard-station-candidates-processor"
  retention_in_days = 3

  tags = merge(var.tags, {
    Name      = "/aws/lambda/${var.name_prefix}-sigmet-hazard-station-candidates-processor"
    Component = "weather-processing"
  })
}

data "aws_iam_policy_document" "sigmet_hsc_processor_policy" {
  statement {
    sid    = "ReadHazardCoordinates"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:Scan",
    ]

    resources = [
      aws_dynamodb_table.hazard_coordinates.arn,
      "${aws_dynamodb_table.hazard_coordinates.arn}/index/*",
    ]
  }

  statement {
    sid    = "ReadStationReference"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:Scan",
    ]

    resources = [
      var.station_reference_table_arn,
      "${var.station_reference_table_arn}/index/*",
    ]
  }

  statement {
    sid    = "ManageHazardStationCandidates"
    effect = "Allow"

    actions = [
      "dynamodb:BatchWriteItem",
      "dynamodb:DeleteItem",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:Scan",
    ]

    resources = [
      aws_dynamodb_table.hazard_station_candidates.arn,
      "${aws_dynamodb_table.hazard_station_candidates.arn}/index/*",
    ]
  }

  statement {
    sid    = "PublishHazardStationsReady"
    effect = "Allow"

    actions = [
      "events:PutEvents",
    ]

    resources = [
      var.event_bus_arn,
    ]
  }

  statement {
    sid    = "WriteLambdaLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "${aws_cloudwatch_log_group.sigmet_hazard_station_candidates_processor.arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "sigmet_hsc_processor_lambda" {
  name   = "${var.name_prefix}-hsc-processor-lambda-policy"
  role   = aws_iam_role.sigmet_hsc_processor_lambda.id
  policy = data.aws_iam_policy_document.sigmet_hsc_processor_policy.json
}

resource "aws_lambda_function" "sigmet_hazard_station_candidates_processor" {
  function_name = "${var.name_prefix}-sigmet-hazard-station-candidates-processor"
  role          = aws_iam_role.sigmet_hsc_processor_lambda.arn

  filename         = var.sigmet_hazard_station_candidates_processor_zip_path
  source_code_hash = filebase64sha256(var.sigmet_hazard_station_candidates_processor_zip_path)

  runtime = "python3.12"
  handler = "app.lambda_handler"

  memory_size = 512
  timeout     = 120

  environment {
    variables = {
      ENVIRONMENT                           = replace(var.name_prefix, "wilvor-", "")
      HAZARD_COORDINATES_TABLE_NAME        = aws_dynamodb_table.hazard_coordinates.name
      STATION_REFERENCE_TABLE_NAME         = var.station_reference_table_name
      HAZARD_STATION_CANDIDATES_TABLE_NAME = aws_dynamodb_table.hazard_station_candidates.name
      SCHEMA_VERSION                       = "wilvor.hazard_station_candidates.v4.0"
      EVENT_BUS_NAME                       = var.event_bus_name
      SELECTION_RADIUS_NM                  = tostring(var.hazard_station_selection_radius_nm)
      SELECTION_CONFIG_VERSION             = var.hazard_station_selection_config_version
      STATION_REFERENCE_H3_INDEX_NAME = var.station_reference_h3_index_name
      H3_RESOLUTION                   = tostring(var.hazard_station_candidate_h3_resolution)
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.sigmet_hazard_station_candidates_processor,
    aws_iam_role_policy.sigmet_hsc_processor_lambda,
  ]

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-sigmet-hazard-station-candidates-processor"
    Component = "weather-processing"
  })
}

resource "aws_cloudwatch_event_rule" "hazard_station_candidates_source_updates" {
  name           = "${var.name_prefix}-hazard-station-candidates-source-updates"
  description    = "Build HazardStationCandidates after hazard coordinates or station reference updates"
  event_bus_name = var.event_bus_name

  event_pattern = jsonencode({
    source = [
      "wilvor.weather",
      "wilvor.reference.station",
    ]

    "detail-type" = [
      "HazardCoordinates.materialized",
      "station.reference.updated",
    ]
  })

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-hazard-station-candidates-source-updates"
    Component = "weather-processing"
  })
}

resource "aws_cloudwatch_event_target" "hazard_station_candidates_processor" {
  rule           = aws_cloudwatch_event_rule.hazard_station_candidates_source_updates.name
  event_bus_name = var.event_bus_name
  target_id      = "HazardStationCandidatesProcessorLambda"
  arn            = aws_lambda_function.sigmet_hazard_station_candidates_processor.arn
}

resource "aws_lambda_permission" "allow_eventbridge_hazard_station_candidates_processor" {
  statement_id  = "AllowExecutionFromEventBridgeHazardStationCandidates"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.sigmet_hazard_station_candidates_processor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.hazard_station_candidates_source_updates.arn
}