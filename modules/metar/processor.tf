data "aws_iam_policy_document" "metar_processor_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "metar_processor_lambda" {
  name               = "${var.name_prefix}-metar-processor-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.metar_processor_assume_role.json

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-metar-processor-lambda-role"
    Component = "weather-processing"
  })
}

resource "aws_cloudwatch_log_group" "metar_processor" {
  name              = "/aws/lambda/${var.name_prefix}-metar-processor"
  retention_in_days = 3

  tags = merge(var.tags, {
    Name      = "/aws/lambda/${var.name_prefix}-metar-processor"
    Component = "weather-processing"
  })
}

data "aws_iam_policy_document" "metar_processor_policy" {
  statement {
    sid    = "ReadMetarRawKinesis"
    effect = "Allow"

    actions = [
      "kinesis:DescribeStream",
      "kinesis:DescribeStreamSummary",
      "kinesis:GetRecords",
      "kinesis:GetShardIterator",
      "kinesis:ListShards",
    ]

    resources = [
      aws_kinesis_stream.metar_raw.arn,
    ]
  }

  statement {
    sid    = "WriteMetarLatestTable"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
    ]

    resources = [
      aws_dynamodb_table.metar_latest.arn,
    ]
  }

  statement {
    sid    = "WriteBadMetarRecordsToS3"
    effect = "Allow"

    actions = [
      "s3:PutObject",
    ]

    resources = [
      "${aws_s3_bucket.metar_archive.arn}/bad-records/source=metar_processor/*",
    ]
  }

  statement {
    sid    = "WriteProcessorLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "${aws_cloudwatch_log_group.metar_processor.arn}:*",
    ]
  }

  statement {
    sid    = "PublishMetarUpdatedEvents"
    effect = "Allow"

    actions = [
      "events:PutEvents",
    ]

    resources = [
      "arn:aws:events:${var.aws_region}:${var.account_id}:event-bus/${var.event_bus_name}",
    ]
  }
}

resource "aws_iam_role_policy" "metar_processor_lambda" {
  name   = "${var.name_prefix}-metar-processor-lambda-policy"
  role   = aws_iam_role.metar_processor_lambda.id
  policy = data.aws_iam_policy_document.metar_processor_policy.json
}

resource "aws_lambda_function" "metar_processor" {
  function_name = "${var.name_prefix}-metar-processor"
  role          = aws_iam_role.metar_processor_lambda.arn

  filename         = var.metar_processor_zip_path
  source_code_hash = filebase64sha256(var.metar_processor_zip_path)

  runtime = "python3.12"
  handler = "app.lambda_handler"

  memory_size = 256
  timeout     = 60

  environment {
    variables = {
      METAR_LATEST_TABLE_NAME = (
        aws_dynamodb_table.metar_latest.name
      )

      BAD_RECORDS_BUCKET_NAME = (
        aws_s3_bucket.metar_archive.bucket
      )

      BAD_RECORDS_PREFIX = (
        "bad-records/source=metar_processor"
      )

      SCHEMA_VERSION = (
        "wilvor.metar_latest.v4.0"
      )

      EVENT_SCHEMA_VERSION = (
        "wilvor.event.metar.updated.v1"
      )

      EVENT_BUS_NAME = (
        var.event_bus_name
      )

      ENVIRONMENT = lookup(
        var.tags,
        "Environment",
        "dev"
      )

      METAR_FRESH_SECONDS = tostring(
        var.metar_fresh_seconds
      )

      METAR_ACCEPTABLE_SECONDS = tostring(
        var.metar_acceptable_seconds
      )

      METAR_TTL_SECONDS = tostring(
        var.metar_ttl_seconds
      )
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.metar_processor,
    aws_iam_role_policy.metar_processor_lambda,
  ]

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-metar-processor"
    Component = "weather-processing"
  })
}

resource "aws_lambda_event_source_mapping" "metar_raw_to_processor" {
  event_source_arn = (
    aws_kinesis_stream.metar_raw.arn
  )

  function_name = (
    aws_lambda_function.metar_processor.arn
  )

  starting_position = "LATEST"

  batch_size = 10

  maximum_batching_window_in_seconds = 1

  parallelization_factor = 1

  function_response_types = [
    "ReportBatchItemFailures",
  ]

  maximum_retry_attempts = 3

  enabled = true

  depends_on = [
    aws_iam_role_policy.metar_processor_lambda,
  ]
}