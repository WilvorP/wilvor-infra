data "aws_iam_policy_document" "aircraft_raw_processor_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_cloudwatch_log_group" "aircraft_raw_processor" {
  name              = "/aws/lambda/${var.name_prefix}-aircraft-raw-processor"
  retention_in_days = 3

  tags = merge(var.tags, {
    Name      = "/aws/lambda/${var.name_prefix}-aircraft-raw-processor"
    Component = "aircraft-ingestion"
  })
}

resource "aws_iam_role" "aircraft_raw_processor_lambda" {
  name               = "${var.name_prefix}-aircraft-raw-processor-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.aircraft_raw_processor_assume_role.json

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-aircraft-raw-processor-lambda-role"
    Component = "aircraft-ingestion"
  })
}

data "aws_iam_policy_document" "aircraft_raw_processor_policy" {
  statement {
    sid    = "ReadFromAircraftRawKinesis"
    effect = "Allow"

    actions = [
      "kinesis:DescribeStream",
      "kinesis:DescribeStreamSummary",
      "kinesis:GetRecords",
      "kinesis:GetShardIterator",
      "kinesis:ListShards",
    ]

    resources = [
      aws_kinesis_stream.aircraft_raw.arn,
    ]
  }

  statement {
    sid    = "WriteToAircraftCleanKinesis"
    effect = "Allow"

    actions = [
      "kinesis:PutRecord",
      "kinesis:PutRecords",
    ]

    resources = [
      aws_kinesis_stream.aircraft_clean.arn,
    ]
  }

  statement {
    sid    = "WriteBadAircraftRecordsToS3"
    effect = "Allow"

    actions = [
      "s3:PutObject",
    ]

    resources = [
      "${aws_s3_bucket.aircraft_archive.arn}/bad-records/*",
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
      "${aws_cloudwatch_log_group.aircraft_raw_processor.arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "aircraft_raw_processor_lambda" {
  name   = "${var.name_prefix}-aircraft-raw-processor-lambda-policy"
  role   = aws_iam_role.aircraft_raw_processor_lambda.id
  policy = data.aws_iam_policy_document.aircraft_raw_processor_policy.json
}

resource "aws_lambda_function" "aircraft_raw_processor" {
  function_name = "${var.name_prefix}-aircraft-raw-processor"
  role          = aws_iam_role.aircraft_raw_processor_lambda.arn
  runtime       = "python3.12"
  handler       = "app.handler"

  filename         = "${path.root}/../../functions/aircraft_raw_processor/dist/aircraft_raw_processor.zip"
  source_code_hash = filebase64sha256("${path.root}/../../functions/aircraft_raw_processor/dist/aircraft_raw_processor.zip")

  timeout     = 30
  memory_size = 128

  environment {
    variables = {
      AIRCRAFT_ARCHIVE_BUCKET    = aws_s3_bucket.aircraft_archive.bucket
      AIRCRAFT_CLEAN_STREAM_NAME = aws_kinesis_stream.aircraft_clean.name
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.aircraft_raw_processor,
    aws_iam_role_policy.aircraft_raw_processor_lambda,
  ]

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-aircraft-raw-processor"
    Component = "aircraft-ingestion"
  })
}

resource "aws_lambda_event_source_mapping" "aircraft_raw_processor_from_raw_stream" {
  event_source_arn  = aws_kinesis_stream.aircraft_raw.arn
  function_name     = aws_lambda_function.aircraft_raw_processor.arn
  starting_position = "LATEST"

  batch_size                         = 100
  maximum_batching_window_in_seconds = 1
  parallelization_factor             = 1

  function_response_types = ["ReportBatchItemFailures"]

  maximum_retry_attempts = 3

  depends_on = [
    aws_iam_role_policy.aircraft_raw_processor_lambda,
  ]
}