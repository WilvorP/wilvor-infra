data "aws_iam_policy_document" "taf_poller_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "taf_poller_lambda" {
  name               = "${var.name_prefix}-taf-poller-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.taf_poller_assume_role.json

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-taf-poller-lambda-role"
    Component = "weather-ingestion"
  })
}

resource "aws_cloudwatch_log_group" "taf_poller" {
  name              = "/aws/lambda/${var.name_prefix}-taf-poller"
  retention_in_days = 3

  tags = merge(var.tags, {
    Name      = "/aws/lambda/${var.name_prefix}-taf-poller"
    Component = "weather-ingestion"
  })
}

data "aws_iam_policy_document" "taf_poller_policy" {
  statement {
    sid    = "WriteToTafRawKinesis"
    effect = "Allow"

    actions = [
      "kinesis:PutRecord",
      "kinesis:PutRecords",
    ]

    resources = [aws_kinesis_stream.taf_raw.arn]
  }

  statement {
    sid    = "WriteLambdaLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = ["${aws_cloudwatch_log_group.taf_poller.arn}:*"]
  }

  statement {
    sid     = "WriteRawTafResponseToS3"
    effect  = "Allow"
    actions = ["s3:PutObject"]

    resources = [
      "${aws_s3_bucket.taf_archive.arn}/raw/source=taf/*",
    ]
  }
}

resource "aws_iam_role_policy" "taf_poller_lambda" {
  name   = "${var.name_prefix}-taf-poller-lambda-policy"
  role   = aws_iam_role.taf_poller_lambda.id
  policy = data.aws_iam_policy_document.taf_poller_policy.json
}

resource "aws_lambda_function" "taf_poller" {
  function_name    = "${var.name_prefix}-taf-poller"
  role             = aws_iam_role.taf_poller_lambda.arn
  filename         = var.taf_poller_zip_path
  source_code_hash = filebase64sha256(var.taf_poller_zip_path)
  runtime          = "python3.12"
  handler          = "app.lambda_handler"
  memory_size      = 128
  timeout          = 90

  environment {
    variables = {
      ENVIRONMENT            = replace(var.name_prefix, "wilvor-", "")
      TAF_RAW_STREAM_NAME    = aws_kinesis_stream.taf_raw.name
      ARCHIVE_BUCKET_NAME    = aws_s3_bucket.taf_archive.bucket
      NOAA_TAF_URL           = var.taf_api_url
      TAF_STATION_IDS        = var.taf_station_ids
      TAF_STATION_CHUNK_SIZE = tostring(var.taf_station_chunk_size)
      RAW_PREFIX             = "raw/source=taf"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.taf_poller,
    aws_iam_role_policy.taf_poller_lambda,
  ]

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-taf-poller"
    Component = "weather-ingestion"
  })
}

resource "aws_cloudwatch_event_rule" "taf_poller_schedule" {
  name                = "${var.name_prefix}-taf-poller-schedule"
  description         = "Schedule for NOAA TAF polling"
  schedule_expression = var.taf_poller_schedule_expression
  state               = var.enable_taf_poller_schedule ? "ENABLED" : "DISABLED"

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-taf-poller-schedule"
    Component = "weather-ingestion"
  })
}

resource "aws_cloudwatch_event_target" "taf_poller" {
  rule      = aws_cloudwatch_event_rule.taf_poller_schedule.name
  target_id = "TafPollerLambda"
  arn       = aws_lambda_function.taf_poller.arn
}

resource "aws_lambda_permission" "allow_eventbridge_taf_poller" {
  statement_id  = "AllowExecutionFromEventBridgeTafPoller"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.taf_poller.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.taf_poller_schedule.arn
}
