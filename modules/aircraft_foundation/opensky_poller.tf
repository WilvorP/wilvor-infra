data "aws_iam_policy_document" "opensky_poller_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "opensky_poller_lambda" {
  name               = "${var.name_prefix}-opensky-poller-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.opensky_poller_assume_role.json

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-opensky-poller-lambda-role"
    Component = "aircraft-ingestion"
  })
}

resource "aws_cloudwatch_log_group" "opensky_poller" {
  name              = "/aws/lambda/${var.name_prefix}-opensky-poller"
  retention_in_days = 3

  tags = merge(var.tags, {
    Name      = "/aws/lambda/${var.name_prefix}-opensky-poller"
    Component = "aircraft-ingestion"
  })
}

data "aws_iam_policy_document" "opensky_poller_policy" {
  statement {
    sid    = "WriteToAircraftRawKinesis"
    effect = "Allow"

    actions = [
      "kinesis:PutRecord",
      "kinesis:PutRecords"
    ]

    resources = [
      aws_kinesis_stream.aircraft_raw.arn
    ]
  }

  statement {
    sid    = "WriteLambdaLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]

    resources = [
      "${aws_cloudwatch_log_group.opensky_poller.arn}:*"
    ]
  }

  statement {
    sid    = "ReadOpenSkyCredentials"
    effect = "Allow"

    actions = [
      "secretsmanager:GetSecretValue"
    ]

    resources = [
      aws_secretsmanager_secret.opensky_credentials.arn
    ]
  }
  statement {
  sid    = "WriteRawOpenSkyResponseToS3"
  effect = "Allow"

  actions = [
    "s3:PutObject"
  ]

  resources = [
    "${aws_s3_bucket.aircraft_archive.arn}/raw/*"
  ]
  }
}

resource "aws_iam_role_policy" "opensky_poller_lambda" {
  name   = "${var.name_prefix}-opensky-poller-lambda-policy"
  role   = aws_iam_role.opensky_poller_lambda.id
  policy = data.aws_iam_policy_document.opensky_poller_policy.json
}

resource "aws_lambda_function" "opensky_poller" {
  function_name = "${var.name_prefix}-opensky-poller"
  role          = aws_iam_role.opensky_poller_lambda.arn

  filename         = var.opensky_poller_zip_path
  source_code_hash = filebase64sha256(var.opensky_poller_zip_path)

  runtime = "python3.12"
  handler = "app.handler"

  memory_size = 128
  timeout     = 30


  environment {
    variables = {
      AIRCRAFT_RAW_STREAM_NAME = aws_kinesis_stream.aircraft_raw.name
      AIRCRAFT_ARCHIVE_BUCKET  = aws_s3_bucket.aircraft_archive.bucket
      OPENSKY_SECRET_ARN       = aws_secretsmanager_secret.opensky_credentials.arn

      OPENSKY_TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
      OPENSKY_STATES_URL = "https://opensky-network.org/api/states/all"

      # Small San Francisco Bay Area test box
      OPENSKY_LAMIN = "37.0"
      OPENSKY_LOMIN = "-123.0"
      OPENSKY_LAMAX = "38.5"
      OPENSKY_LOMAX = "-121.5"

      ENVIRONMENT = "dev"
      MODE        = "real-opensky-poller"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.opensky_poller,
    aws_iam_role_policy.opensky_poller_lambda
  ]

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-opensky-poller"
    Component = "aircraft-ingestion"
  })
}

resource "aws_cloudwatch_event_rule" "opensky_poller_schedule" {
  name                = "${var.name_prefix}-opensky-poller-schedule"
  description         = "Disabled schedule for OpenSky aircraft polling in dev"
  schedule_expression = var.opensky_poller_schedule_expression
  state               = var.enable_opensky_poller_schedule ? "ENABLED" : "DISABLED"

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-opensky-poller-schedule"
    Component = "aircraft-ingestion"
  })
}

resource "aws_cloudwatch_event_target" "opensky_poller" {
  rule      = aws_cloudwatch_event_rule.opensky_poller_schedule.name
  target_id = "OpenSkyPollerLambda"
  arn       = aws_lambda_function.opensky_poller.arn
}

resource "aws_lambda_permission" "allow_eventbridge_opensky_poller" {
  statement_id  = "AllowExecutionFromEventBridgeOpenSkyPoller"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.opensky_poller.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.opensky_poller_schedule.arn
}