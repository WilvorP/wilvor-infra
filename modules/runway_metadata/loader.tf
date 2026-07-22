locals {
  runway_loader_function_name = (
    "${var.name_prefix}-runway-metadata-loader"
  )

  runway_raw_prefix = "raw/source=faa-nasr"
  runway_bad_prefix = "bad/source=faa-nasr"
}

data "aws_iam_policy_document" "runway_loader_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type = "Service"

      identifiers = [
        "lambda.amazonaws.com",
      ]
    }

    actions = [
      "sts:AssumeRole",
    ]
  }
}

resource "aws_iam_role" "runway_loader" {
  name = "${var.name_prefix}-runway-loader-lambda-role"

  assume_role_policy = (
    data.aws_iam_policy_document.runway_loader_assume_role.json
  )

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-runway-loader-lambda-role"
    Component = "runway-reference-data"
  })
}

resource "aws_cloudwatch_log_group" "runway_loader" {
  name = "/aws/lambda/${local.runway_loader_function_name}"

  retention_in_days = var.log_retention_days

  tags = merge(var.tags, {
    Name      = "/aws/lambda/${local.runway_loader_function_name}"
    Component = "runway-reference-data"
  })
}

data "aws_iam_policy_document" "runway_loader" {
  statement {
    sid    = "WriteLambdaLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "${aws_cloudwatch_log_group.runway_loader.arn}:*",
    ]
  }

  statement {
    sid    = "ReadArchivedRunwaySources"
    effect = "Allow"

    actions = [
      "s3:GetObject",
    ]

    resources = [
      "${aws_s3_bucket.runway_archive.arn}/${local.runway_raw_prefix}/*",
    ]
  }

  statement {
    sid    = "WriteRunwayArchiveObjects"
    effect = "Allow"

    actions = [
      "s3:PutObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]

    resources = [
      "${aws_s3_bucket.runway_archive.arn}/${local.runway_raw_prefix}/*",
      "${aws_s3_bucket.runway_archive.arn}/${local.runway_bad_prefix}/*",
    ]
  }

  statement {
    sid    = "ManageRunwayReferenceState"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:BatchWriteItem",
    ]

    resources = [
      aws_dynamodb_table.runway_reference.arn,
    ]
  }

  statement {
    sid    = "PublishReferenceDataEvents"
    effect = "Allow"

    actions = [
      "events:PutEvents",
    ]

    resources = [
      var.event_bus_arn,
    ]
  }
}

resource "aws_iam_role_policy" "runway_loader" {
  name = "${var.name_prefix}-runway-loader-lambda-policy"
  role = aws_iam_role.runway_loader.id

  policy = data.aws_iam_policy_document.runway_loader.json
}

resource "aws_lambda_function" "runway_loader" {
  function_name = local.runway_loader_function_name

  role = aws_iam_role.runway_loader.arn

  filename = var.runway_loader_zip_path

  source_code_hash = filebase64sha256(
    var.runway_loader_zip_path
  )

  runtime = "python3.12"
  handler = "app.lambda_handler"

  memory_size = var.lambda_memory_size
  timeout     = var.lambda_timeout_seconds

  ephemeral_storage {
    size = var.lambda_ephemeral_storage_mb
  }

  environment {
    variables = {
      ENVIRONMENT = var.environment

      RUNWAY_REFERENCE_TABLE_NAME = (
        aws_dynamodb_table.runway_reference.name
      )

      ARCHIVE_BUCKET_NAME = (
        aws_s3_bucket.runway_archive.bucket
      )

      SUPPORTED_AIRPORT_IDS_JSON = jsonencode(
        var.supported_airport_ids
      )

      FAA_APT_ZIP_URL      = var.faa_apt_zip_url
      DEFAULT_SOURCE_CYCLE = var.default_source_cycle

      EVENT_BUS_NAME = var.event_bus_name

      RAW_PREFIX = local.runway_raw_prefix
      BAD_PREFIX = local.runway_bad_prefix

      HTTP_TIMEOUT_SECONDS = tostring(
        var.http_timeout_seconds
      )

      WORK_DIRECTORY = "/tmp/runway-loader"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.runway_loader,
    aws_iam_role_policy.runway_loader,
  ]

  tags = merge(var.tags, {
    Name      = local.runway_loader_function_name
    Component = "runway-reference-data"
  })
}

resource "aws_cloudwatch_event_rule" "runway_loader_schedule" {
  name = "${var.name_prefix}-runway-loader-schedule"

  description = (
    "Schedule for FAA NASR runway reference-data loading"
  )

  schedule_expression = (
    var.runway_loader_schedule_expression
  )

  state = (
    var.enable_runway_loader_schedule
    ? "ENABLED"
    : "DISABLED"
  )

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-runway-loader-schedule"
    Component = "runway-reference-data"
  })
}

resource "aws_cloudwatch_event_target" "runway_loader" {
  rule = aws_cloudwatch_event_rule.runway_loader_schedule.name

  target_id = "RunwayMetadataLoaderLambda"
  arn       = aws_lambda_function.runway_loader.arn
}

resource "aws_lambda_permission" "allow_eventbridge_runway_loader" {
  statement_id = "AllowExecutionFromEventBridgeRunwayLoader"

  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.runway_loader.function_name
  principal     = "events.amazonaws.com"

  source_arn = aws_cloudwatch_event_rule.runway_loader_schedule.arn
}