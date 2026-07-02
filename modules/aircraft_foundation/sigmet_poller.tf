data "aws_iam_policy_document" "sigmet_poller_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "sigmet_poller_lambda" {
  name               = "${var.name_prefix}-sigmet-poller-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.sigmet_poller_assume_role.json

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-sigmet-poller-lambda-role"
    Component = "weather-ingestion"
  })
}

resource "aws_cloudwatch_log_group" "sigmet_poller" {
  name              = "/aws/lambda/${var.name_prefix}-sigmet-poller"
  retention_in_days = 3

  tags = merge(var.tags, {
    Name      = "/aws/lambda/${var.name_prefix}-sigmet-poller"
    Component = "weather-ingestion"
  })
}

data "aws_iam_policy_document" "sigmet_poller_policy" {
  statement {
    sid    = "WriteToSigmetRawKinesis"
    effect = "Allow"

    actions = [
      "kinesis:PutRecord",
      "kinesis:PutRecords",
    ]

    resources = [
      aws_kinesis_stream.sigmet_raw.arn,
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
      "${aws_cloudwatch_log_group.sigmet_poller.arn}:*",
    ]
  }

  statement {
    sid    = "WriteRawSigmetResponseToS3"
    effect = "Allow"

    actions = [
      "s3:PutObject",
    ]

    resources = [
      "${aws_s3_bucket.aircraft_archive.arn}/raw/*",
    ]
  }
}

resource "aws_iam_role_policy" "sigmet_poller_lambda" {
  name   = "${var.name_prefix}-sigmet-poller-lambda-policy"
  role   = aws_iam_role.sigmet_poller_lambda.id
  policy = data.aws_iam_policy_document.sigmet_poller_policy.json
}

resource "aws_lambda_function" "sigmet_poller" {
  function_name = "${var.name_prefix}-sigmet-poller"
  role          = aws_iam_role.sigmet_poller_lambda.arn

  filename         = var.sigmet_poller_zip_path
  source_code_hash = filebase64sha256(var.sigmet_poller_zip_path)

  runtime = "python3.12"
  handler = "app.lambda_handler"

  memory_size = 128
  timeout     = 30

  environment {
    variables = {
      SIGMET_RAW_STREAM_NAME = aws_kinesis_stream.sigmet_raw.name
      ARCHIVE_BUCKET_NAME    = aws_s3_bucket.aircraft_archive.bucket
      NOAA_SIGMET_URL        = var.sigmet_api_url
      RAW_PREFIX             = "raw/source=sigmet"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.sigmet_poller,
    aws_iam_role_policy.sigmet_poller_lambda,
  ]

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-sigmet-poller"
    Component = "weather-ingestion"
  })
}

resource "aws_cloudwatch_event_rule" "sigmet_poller_schedule" {
  name                = "${var.name_prefix}-sigmet-poller-schedule"
  description         = "Schedule for NOAA SIGMET polling"
  schedule_expression = var.sigmet_poller_schedule_expression
  state               = var.enable_sigmet_poller_schedule ? "ENABLED" : "DISABLED"

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-sigmet-poller-schedule"
    Component = "weather-ingestion"
  })
}

resource "aws_cloudwatch_event_target" "sigmet_poller" {
  rule      = aws_cloudwatch_event_rule.sigmet_poller_schedule.name
  target_id = "SigmetPollerLambda"
  arn       = aws_lambda_function.sigmet_poller.arn
}

resource "aws_lambda_permission" "allow_eventbridge_sigmet_poller" {
  statement_id  = "AllowExecutionFromEventBridgeSigmetPoller"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.sigmet_poller.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.sigmet_poller_schedule.arn
}
