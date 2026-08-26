locals {
  station_reference_loader_function_name = "${var.name_prefix}-station-reference-loader"
  station_reference_raw_prefix           = "raw/source=aviation-weather-stations"
  station_reference_bad_prefix           = "bad/source=aviation-weather-stations"
}

data "aws_iam_policy_document" "station_reference_loader_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "station_reference_loader" {
  name = "${var.name_prefix}-station-reference-loader-lambda-role"

  assume_role_policy = data.aws_iam_policy_document.station_reference_loader_assume_role.json

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-station-reference-loader-lambda-role"
    Component = "station-reference-data"
  })
}

resource "aws_cloudwatch_log_group" "station_reference_loader" {
  name              = "/aws/lambda/${local.station_reference_loader_function_name}"
  retention_in_days = var.log_retention_days

  tags = merge(var.tags, {
    Name      = "/aws/lambda/${local.station_reference_loader_function_name}"
    Component = "station-reference-data"
  })
}

data "aws_iam_policy_document" "station_reference_loader" {
  statement {
    sid    = "WriteLambdaLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "${aws_cloudwatch_log_group.station_reference_loader.arn}:*",
    ]
  }

  statement {
    sid    = "WriteStationReferenceArchiveObjects"
    effect = "Allow"

    actions = [
      "s3:PutObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]

    resources = [
      "${aws_s3_bucket.station_reference_archive.arn}/${local.station_reference_raw_prefix}/*",
      "${aws_s3_bucket.station_reference_archive.arn}/${local.station_reference_bad_prefix}/*",
    ]
  }

  statement {
    sid    = "ManageStationReferenceState"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:BatchWriteItem",
      "dynamodb:Query",
    ]

    resources = [
      aws_dynamodb_table.station_reference.arn,
      "${aws_dynamodb_table.station_reference.arn}/index/*",
    ]
  }

  statement {
    sid    = "PublishStationReferenceEvents"
    effect = "Allow"

    actions = ["events:PutEvents"]

    resources = [var.event_bus_arn]
  }
}

resource "aws_iam_role_policy" "station_reference_loader" {
  name   = "${var.name_prefix}-station-reference-loader-lambda-policy"
  role   = aws_iam_role.station_reference_loader.id
  policy = data.aws_iam_policy_document.station_reference_loader.json
}

resource "aws_lambda_function" "station_reference_loader" {
  function_name = local.station_reference_loader_function_name
  role          = aws_iam_role.station_reference_loader.arn

  filename         = var.station_reference_loader_zip_path
  source_code_hash = filebase64sha256(var.station_reference_loader_zip_path)

  runtime = "python3.12"
  handler = "app.lambda_handler"

  memory_size = var.lambda_memory_size
  timeout     = var.lambda_timeout_seconds

  environment {
    variables = {
      STATION_REFERENCE_TABLE_NAME = aws_dynamodb_table.station_reference.name
      ARCHIVE_BUCKET_NAME          = aws_s3_bucket.station_reference_archive.bucket
      STATION_CACHE_URL            = var.station_cache_url
      EVENT_BUS_NAME               = var.event_bus_name
      RAW_PREFIX                   = local.station_reference_raw_prefix
      BAD_PREFIX                   = local.station_reference_bad_prefix
      STATION_H3_RESOLUTION        = tostring(var.station_h3_resolution)
      SOURCE_VERSION               = var.default_source_version
      HTTP_TIMEOUT_SECONDS         = tostring(var.http_timeout_seconds)
      SCHEMA_VERSION               = var.schema_version
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.station_reference_loader,
    aws_iam_role_policy.station_reference_loader,
  ]

  tags = merge(var.tags, {
    Name      = local.station_reference_loader_function_name
    Component = "station-reference-data"
  })
}

resource "aws_cloudwatch_event_rule" "station_reference_loader_schedule" {
  name        = "${var.name_prefix}-station-reference-loader-schedule"
  description = "Schedule for Aviation Weather station reference loading"

  schedule_expression = var.station_reference_loader_schedule_expression

  state = (
    var.enable_station_reference_loader_schedule
    ? "ENABLED"
    : "DISABLED"
  )

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-station-reference-loader-schedule"
    Component = "station-reference-data"
  })
}

resource "aws_cloudwatch_event_target" "station_reference_loader" {
  rule      = aws_cloudwatch_event_rule.station_reference_loader_schedule.name
  target_id = "StationReferenceLoaderLambda"
  arn       = aws_lambda_function.station_reference_loader.arn
}

resource "aws_lambda_permission" "allow_eventbridge_station_reference_loader" {
  statement_id  = "AllowExecutionFromEventBridgeStationReferenceLoader"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.station_reference_loader.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.station_reference_loader_schedule.arn
}
