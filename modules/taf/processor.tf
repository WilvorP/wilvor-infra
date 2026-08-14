data "aws_iam_policy_document" "taf_processor_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "taf_processor_lambda" {
  name               = "${var.name_prefix}-taf-processor-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.taf_processor_assume_role.json

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-taf-processor-lambda-role"
    Component = "weather-processing"
  })
}

resource "aws_cloudwatch_log_group" "taf_processor" {
  name              = "/aws/lambda/${var.name_prefix}-taf-processor"
  retention_in_days = 3

  tags = merge(var.tags, {
    Name      = "/aws/lambda/${var.name_prefix}-taf-processor"
    Component = "weather-processing"
  })
}

data "aws_iam_policy_document" "taf_processor_policy" {
  statement {
    sid    = "ReadFromTafRawKinesis"
    effect = "Allow"

    actions = [
      "kinesis:DescribeStream",
      "kinesis:DescribeStreamSummary",
      "kinesis:GetRecords",
      "kinesis:GetShardIterator",
      "kinesis:ListShards",
    ]

    resources = [aws_kinesis_stream.taf_raw.arn]
  }

  statement {
    sid    = "ReadWriteTafLatest"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
    ]

    resources = [aws_dynamodb_table.taf_latest.arn]
  }

  statement {
    sid    = "WriteTafForecastPeriods"
    effect = "Allow"
    actions = [
      "dynamodb:BatchWriteItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
    ]

    resources = [
      aws_dynamodb_table.taf_forecast_periods.arn,
      "${aws_dynamodb_table.taf_forecast_periods.arn}/index/*",
    ]
  }

  statement {
    sid     = "PublishTafMaterializedEvents"
    effect  = "Allow"
    actions = ["events:PutEvents"]

    resources = [var.event_bus_arn]
  }

  statement {
    sid     = "WriteBadTafRecordsToS3"
    effect  = "Allow"
    actions = ["s3:PutObject"]

    resources = [
      "${aws_s3_bucket.taf_archive.arn}/bad-records/source=taf_processor/*",
    ]
  }

  statement {
    sid    = "WriteLambdaLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = ["${aws_cloudwatch_log_group.taf_processor.arn}:*"]
  }
}

resource "aws_iam_role_policy" "taf_processor_lambda" {
  name   = "${var.name_prefix}-taf-processor-lambda-policy"
  role   = aws_iam_role.taf_processor_lambda.id
  policy = data.aws_iam_policy_document.taf_processor_policy.json
}

resource "aws_lambda_function" "taf_processor" {
  function_name    = "${var.name_prefix}-taf-processor"
  role             = aws_iam_role.taf_processor_lambda.arn
  filename         = var.taf_processor_zip_path
  source_code_hash = filebase64sha256(var.taf_processor_zip_path)
  runtime          = "python3.12"
  handler          = "app.lambda_handler"
  memory_size      = 256
  timeout          = 60

  environment {
    variables = {
      ENVIRONMENT                     = replace(var.name_prefix, "wilvor-", "")
      TAF_LATEST_TABLE_NAME           = aws_dynamodb_table.taf_latest.name
      TAF_FORECAST_PERIODS_TABLE_NAME = aws_dynamodb_table.taf_forecast_periods.name
      SCHEMA_VERSION                  = "internal.taf.v1"
      EVENT_BUS_NAME                  = var.event_bus_name
      BAD_RECORDS_BUCKET_NAME         = aws_s3_bucket.taf_archive.bucket
      BAD_RECORDS_PREFIX              = "bad-records/source=taf_processor"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.taf_processor,
    aws_iam_role_policy.taf_processor_lambda,
  ]

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-taf-processor"
    Component = "weather-processing"
  })
}

resource "aws_lambda_event_source_mapping" "taf_raw_to_taf_processor" {
  event_source_arn                   = aws_kinesis_stream.taf_raw.arn
  function_name                      = aws_lambda_function.taf_processor.arn
  starting_position                  = "LATEST"
  batch_size                         = 100
  maximum_batching_window_in_seconds = 1
  function_response_types            = ["ReportBatchItemFailures"]
  bisect_batch_on_function_error     = true

  depends_on = [aws_iam_role_policy.taf_processor_lambda]
}
