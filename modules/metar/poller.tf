data "aws_iam_policy_document" "metar_poller_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "metar_poller_lambda" {
  name               = "${var.name_prefix}-metar-poller-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.metar_poller_assume_role.json

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-metar-poller-lambda-role"
    Component = "weather-ingestion"
  })
}

resource "aws_cloudwatch_log_group" "metar_poller" {
  name              = "/aws/lambda/${var.name_prefix}-metar-poller"
  retention_in_days = 3

  tags = merge(var.tags, {
    Name      = "/aws/lambda/${var.name_prefix}-metar-poller"
    Component = "weather-ingestion"
  })
}

data "aws_iam_policy_document" "metar_poller_policy" {
  statement {
    sid    = "WriteToMetarRawKinesis"
    effect = "Allow"

    actions = [
      "kinesis:PutRecord",
      "kinesis:PutRecords",
    ]

    resources = [
      aws_kinesis_stream.metar_raw.arn,
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
      "${aws_cloudwatch_log_group.metar_poller.arn}:*",
    ]
  }

  statement {
    sid    = "WriteRawMetarResponseToS3"
    effect = "Allow"

    actions = [
      "s3:PutObject",
    ]

    resources = [
      "${aws_s3_bucket.metar_archive.arn}/raw/source=metar/*",
    ]
  }
}

resource "aws_iam_role_policy" "metar_poller_lambda" {
  name   = "${var.name_prefix}-metar-poller-lambda-policy"
  role   = aws_iam_role.metar_poller_lambda.id
  policy = data.aws_iam_policy_document.metar_poller_policy.json
}

resource "aws_lambda_function" "metar_poller" {
  function_name = "${var.name_prefix}-metar-poller"
  role          = aws_iam_role.metar_poller_lambda.arn

  filename         = var.metar_poller_zip_path
  source_code_hash = filebase64sha256(var.metar_poller_zip_path)

  runtime = "python3.12"
  handler = "app.lambda_handler"

  memory_size = 128
  timeout     = 30

  environment {
    variables = {
      ENVIRONMENT           = replace(var.name_prefix, "wilvor-", "")
      METAR_RAW_STREAM_NAME = aws_kinesis_stream.metar_raw.name
      ARCHIVE_BUCKET_NAME   = aws_s3_bucket.metar_archive.bucket
      NOAA_METAR_URL        = var.metar_api_url
      RAW_PREFIX            = "raw/source=metar"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.metar_poller,
    aws_iam_role_policy.metar_poller_lambda,
  ]

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-metar-poller"
    Component = "weather-ingestion"
  })
}

resource "aws_cloudwatch_event_rule" "metar_poller_schedule" {
  name                = "${var.name_prefix}-metar-poller-schedule"
  description         = "Schedule for NOAA METAR polling"
  schedule_expression = var.metar_poller_schedule_expression
  state               = var.enable_metar_poller_schedule ? "ENABLED" : "DISABLED"

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-metar-poller-schedule"
    Component = "weather-ingestion"
  })
}

resource "aws_cloudwatch_event_target" "metar_poller" {
  rule      = aws_cloudwatch_event_rule.metar_poller_schedule.name
  target_id = "MetarPollerLambda"
  arn       = aws_lambda_function.metar_poller.arn
}

resource "aws_lambda_permission" "allow_eventbridge_metar_poller" {
  statement_id  = "AllowExecutionFromEventBridgeMetarPoller"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.metar_poller.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.metar_poller_schedule.arn
}
