data "aws_iam_policy_document" "sigmet_hazard_coordinates_processor_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "sigmet_hazard_coordinates_processor_lambda" {
  name               = "${var.name_prefix}-sigmet-hazard-coordinates-processor-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.sigmet_hazard_coordinates_processor_assume_role.json

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-sigmet-hazard-coordinates-processor-lambda-role"
    Component = "weather-processing"
  })
}

resource "aws_cloudwatch_log_group" "sigmet_hazard_coordinates_processor" {
  name              = "/aws/lambda/${var.name_prefix}-sigmet-hazard-coordinates-processor"
  retention_in_days = 3

  tags = merge(var.tags, {
    Name      = "/aws/lambda/${var.name_prefix}-sigmet-hazard-coordinates-processor"
    Component = "weather-processing"
  })
}

data "aws_iam_policy_document" "sigmet_hazard_coordinates_processor_policy" {
  statement {
    sid    = "ReadFromSigmetRawKinesis"
    effect = "Allow"

    actions = [
      "kinesis:DescribeStream",
      "kinesis:DescribeStreamSummary",
      "kinesis:GetRecords",
      "kinesis:GetShardIterator",
      "kinesis:ListShards",
    ]

    resources = [
      aws_kinesis_stream.sigmet_raw.arn,
    ]
  }

  statement {
    sid    = "ReadWriteHazardCoordinates"
    effect = "Allow"

    actions = [
      "dynamodb:BatchWriteItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
    ]

    resources = [
      aws_dynamodb_table.hazard_coordinates.arn,
    ]
  }

  statement {
    sid    = "PublishHazardCoordinateEvents"
    effect = "Allow"

    actions = [
      "events:PutEvents",
    ]

    resources = [
      var.event_bus_arn,
    ]
  }

  statement {
    sid    = "WriteBadSigmetCoordinateRecordsToS3"
    effect = "Allow"

    actions = [
      "s3:PutObject",
    ]

    resources = [
      "${aws_s3_bucket.sigmet_archive.arn}/bad-records/source=sigmet_hazard_coordinates_processor/*",
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
      "${aws_cloudwatch_log_group.sigmet_hazard_coordinates_processor.arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "sigmet_hazard_coordinates_processor_lambda" {
  name   = "${var.name_prefix}-sigmet-hazard-coordinates-processor-lambda-policy"
  role   = aws_iam_role.sigmet_hazard_coordinates_processor_lambda.id
  policy = data.aws_iam_policy_document.sigmet_hazard_coordinates_processor_policy.json
}

resource "aws_lambda_function" "sigmet_hazard_coordinates_processor" {
  function_name = "${var.name_prefix}-sigmet-hazard-coordinates-processor"
  role          = aws_iam_role.sigmet_hazard_coordinates_processor_lambda.arn

  filename         = var.sigmet_hazard_coordinates_processor_zip_path
  source_code_hash = filebase64sha256(var.sigmet_hazard_coordinates_processor_zip_path)

  runtime = "python3.12"
  handler = "app.lambda_handler"

  memory_size = 256
  timeout     = 60

  environment {
    variables = {
      ENVIRONMENT                    = replace(var.name_prefix, "wilvor-", "")
      HAZARD_COORDINATES_TABLE_NAME  = aws_dynamodb_table.hazard_coordinates.name
      SCHEMA_VERSION                 = "wilvor.hazard_coordinates.v4.0"
      EVENT_BUS_NAME                 = var.event_bus_name
      RETENTION_AFTER_VALID_TO_HOURS = "6"
      BAD_RECORDS_BUCKET_NAME        = aws_s3_bucket.sigmet_archive.bucket
      BAD_RECORDS_PREFIX             = "bad-records/source=sigmet_hazard_coordinates_processor"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.sigmet_hazard_coordinates_processor,
    aws_iam_role_policy.sigmet_hazard_coordinates_processor_lambda,
  ]

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-sigmet-hazard-coordinates-processor"
    Component = "weather-processing"
  })
}

resource "aws_lambda_event_source_mapping" "sigmet_raw_to_hazard_coordinates_processor" {
  event_source_arn  = aws_kinesis_stream.sigmet_raw.arn
  function_name     = aws_lambda_function.sigmet_hazard_coordinates_processor.arn
  starting_position = "LATEST"

  batch_size                         = 100
  maximum_batching_window_in_seconds = 1
  function_response_types            = ["ReportBatchItemFailures"]
  bisect_batch_on_function_error     = true

  depends_on = [
    aws_iam_role_policy.sigmet_hazard_coordinates_processor_lambda,
  ]
}