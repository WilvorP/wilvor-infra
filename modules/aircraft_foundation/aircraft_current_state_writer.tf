data "aws_iam_policy_document" "aircraft_current_state_writer_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_cloudwatch_log_group" "aircraft_current_state_writer" {
  name              = "/aws/lambda/${var.name_prefix}-aircraft-current-state-writer"
  retention_in_days = 3
}

resource "aws_iam_role" "aircraft_current_state_writer_lambda" {
  name               = "${var.name_prefix}-aircraft-current-state-writer-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.aircraft_current_state_writer_assume_role.json
}

data "aws_iam_policy_document" "aircraft_current_state_writer_policy" {
  statement {
    sid = "ReadCleanKinesisStream"

    actions = [
      "kinesis:DescribeStream",
      "kinesis:DescribeStreamSummary",
      "kinesis:GetRecords",
      "kinesis:GetShardIterator",
      "kinesis:ListShards"
    ]

    resources = [
      aws_kinesis_stream.aircraft_clean.arn
    ]
  }

  statement {
    sid = "WriteAircraftCurrentState"

    actions = [
      "dynamodb:DescribeTable",
      "dynamodb:PutItem"
    ]

    resources = [
      aws_dynamodb_table.aircraft_current_state.arn
    ]
  }

  statement {
    sid = "WriteCloudWatchLogs"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]

    resources = [
      "${aws_cloudwatch_log_group.aircraft_current_state_writer.arn}:*"
    ]
  }

  statement {
    sid    = "PublishAircraftStateEvents"
    effect = "Allow"

    actions = [
      "events:PutEvents"
    ]

    resources = [
      var.event_bus_arn
    ]
  }
}

resource "aws_iam_role_policy" "aircraft_current_state_writer_lambda" {
  name   = "${var.name_prefix}-aircraft-current-state-writer-lambda-policy"
  role   = aws_iam_role.aircraft_current_state_writer_lambda.id
  policy = data.aws_iam_policy_document.aircraft_current_state_writer_policy.json
}

resource "aws_lambda_function" "aircraft_current_state_writer" {
  function_name = "${var.name_prefix}-aircraft-current-state-writer"
  role          = aws_iam_role.aircraft_current_state_writer_lambda.arn
  runtime       = "python3.12"
  handler       = "app.handler"

  filename         = "${path.root}/../../functions/aircraft_current_state_writer/dist/aircraft_current_state_writer.zip"
  source_code_hash = filebase64sha256("${path.root}/../../functions/aircraft_current_state_writer/dist/aircraft_current_state_writer.zip")

  timeout     = 30
  memory_size = 128

  environment {
    variables = {
      AIRCRAFT_CURRENT_STATE_TABLE_NAME = aws_dynamodb_table.aircraft_current_state.name
      EVENT_BUS_NAME                    = var.event_bus_name
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.aircraft_current_state_writer,
    aws_iam_role_policy.aircraft_current_state_writer_lambda
  ]
}

resource "aws_lambda_event_source_mapping" "aircraft_current_state_writer_from_clean_stream" {
  event_source_arn  = aws_kinesis_stream.aircraft_clean.arn
  function_name     = aws_lambda_function.aircraft_current_state_writer.arn
  starting_position = "LATEST"

  batch_size                         = 100
  maximum_batching_window_in_seconds = 1
  parallelization_factor             = 1

  function_response_types = ["ReportBatchItemFailures"]
  maximum_retry_attempts  = 3
}