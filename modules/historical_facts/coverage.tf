resource "aws_cloudwatch_log_group" "coverage_control" {
  count = local.enabled ? 1 : 0

  name              = "/aws/lambda/${local.coverage_function_name}"
  retention_in_days = var.log_retention_days

  tags = merge(local.common_tags, {
    Name = "/aws/lambda/${local.coverage_function_name}"
  })
}

resource "aws_iam_role" "coverage_control" {
  count = local.enabled ? 1 : 0

  name               = "${var.name_prefix}-historical-facts-coverage-role"
  assume_role_policy = data.aws_iam_policy_document.transform_assume.json

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-historical-facts-coverage-role"
  })
}

data "aws_iam_policy_document" "coverage_control" {
  count = local.enabled ? 1 : 0

  statement {
    sid    = "WriteLambdaLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "${aws_cloudwatch_log_group.coverage_control[0].arn}:*",
    ]
  }

  statement {
    sid    = "PutHistoricalControlProbe"
    effect = "Allow"

    actions = [
      "events:PutEvents",
    ]

    resources = [
      var.event_bus_arn,
    ]
  }

  statement {
    sid    = "PutGeometryControlProbe"
    effect = "Allow"

    actions = [
      "firehose:PutRecord",
    ]

    resources = [
      aws_kinesis_firehose_delivery_stream.historical["geometry"].arn,
    ]
  }

  statement {
    sid    = "WriteHistoricalMetadata"
    effect = "Allow"

    actions = [
      "s3:PutObject",
    ]

    resources = [
      "${data.aws_s3_bucket.historical_facts[0].arn}/metadata/*",
    ]
  }

  statement {
    sid    = "ReadHistoricalControlObjects"
    effect = "Allow"

    actions = [
      "s3:GetObject",
    ]

    resources = [
      "${data.aws_s3_bucket.historical_facts[0].arn}/metadata/*",
      "${data.aws_s3_bucket.historical_facts[0].arn}/dataset=_collection_control/*",
      "${data.aws_s3_bucket.historical_facts[0].arn}/errors/*",
    ]
  }

  statement {
    sid    = "ListHistoricalControlPrefixes"
    effect = "Allow"

    actions = [
      "s3:ListBucket",
    ]

    resources = [
      data.aws_s3_bucket.historical_facts[0].arn,
    ]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        "metadata/*",
        "dataset=_collection_control/*",
        "errors/*",
      ]
    }
  }

  statement {
    sid    = "ReadCoverageBackstopMetrics"
    effect = "Allow"

    actions = [
      "cloudwatch:GetMetricData",
    ]

    resources = [
      "*",
    ]
  }
}

resource "aws_iam_role_policy" "coverage_control" {
  count = local.enabled ? 1 : 0

  name   = "${var.name_prefix}-historical-facts-coverage-policy"
  role   = aws_iam_role.coverage_control[0].id
  policy = data.aws_iam_policy_document.coverage_control[0].json
}

resource "aws_lambda_function" "coverage_control" {
  count = local.enabled ? 1 : 0

  function_name = local.coverage_function_name
  role          = aws_iam_role.coverage_control[0].arn

  filename         = var.coverage_zip_path
  source_code_hash = filebase64sha256(var.coverage_zip_path)

  runtime = "python3.12"
  handler = "app.lambda_handler"

  memory_size = 256
  timeout     = 60

  environment {
    variables = {
      ENVIRONMENT                              = local.historical_environment
      HISTORICAL_FACTS_BUCKET_NAME             = local.historical_bucket_id
      EVENT_BUS_NAME                           = var.event_bus_name
      HISTORICAL_FACTS_FIREHOSE_STREAM_NAME    = local.facts_stream_name
      HISTORICAL_GEOMETRY_FIREHOSE_STREAM_NAME = local.geometry_stream_name
      NAME_PREFIX                              = var.name_prefix
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.coverage_control,
    aws_iam_role_policy.coverage_control,
  ]

  tags = merge(local.common_tags, {
    Name = local.coverage_function_name
  })
}

resource "aws_lambda_permission" "allow_eventbridge_coverage_schedule" {
  count = local.enabled ? 1 : 0

  statement_id  = "AllowExecutionFromCoverageSchedule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.coverage_control[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.coverage_schedule[0].arn
}

resource "aws_cloudwatch_event_rule" "coverage_schedule" {
  count = local.enabled ? 1 : 0

  name                = "${var.name_prefix}-historical-facts-coverage"
  description         = "15-minute historical coverage control collect"
  schedule_expression = "rate(15 minutes)"

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "coverage_schedule" {
  count = local.enabled ? 1 : 0

  rule      = aws_cloudwatch_event_rule.coverage_schedule[0].name
  target_id = "HistoricalFactsCoverageControl"
  arn       = aws_lambda_function.coverage_control[0].arn
}

resource "aws_cloudwatch_log_group" "dlq_gap_consumer" {
  count = local.enabled ? 1 : 0

  name              = "/aws/lambda/${local.dlq_consumer_function_name}"
  retention_in_days = var.log_retention_days

  tags = merge(local.common_tags, {
    Name = "/aws/lambda/${local.dlq_consumer_function_name}"
  })
}

resource "aws_iam_role" "dlq_gap_consumer" {
  count = local.enabled ? 1 : 0

  name               = "${var.name_prefix}-historical-facts-dlq-consumer-role"
  assume_role_policy = data.aws_iam_policy_document.transform_assume.json

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-historical-facts-dlq-consumer-role"
  })
}

data "aws_iam_policy_document" "dlq_gap_consumer" {
  count = local.enabled ? 1 : 0

  statement {
    sid    = "WriteLambdaLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "${aws_cloudwatch_log_group.dlq_gap_consumer[0].arn}:*",
    ]
  }

  statement {
    sid    = "ConsumeHistoricalFactsDlq"
    effect = "Allow"

    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:ChangeMessageVisibility",
    ]

    resources = [
      aws_sqs_queue.historical_facts_dlq[0].arn,
    ]
  }

  statement {
    sid    = "WriteDomain2Incidents"
    effect = "Allow"

    actions = [
      "s3:PutObject",
    ]

    resources = [
      "${data.aws_s3_bucket.historical_facts[0].arn}/metadata/incidents/*",
    ]
  }
}

resource "aws_iam_role_policy" "dlq_gap_consumer" {
  count = local.enabled ? 1 : 0

  name   = "${var.name_prefix}-historical-facts-dlq-consumer-policy"
  role   = aws_iam_role.dlq_gap_consumer[0].id
  policy = data.aws_iam_policy_document.dlq_gap_consumer[0].json
}

resource "aws_lambda_function" "dlq_gap_consumer" {
  count = local.enabled ? 1 : 0

  function_name = local.dlq_consumer_function_name
  role          = aws_iam_role.dlq_gap_consumer[0].arn

  filename         = var.dlq_consumer_zip_path
  source_code_hash = filebase64sha256(var.dlq_consumer_zip_path)

  runtime = "python3.12"
  handler = "app.lambda_handler"

  memory_size = 256
  timeout     = 60

  environment {
    variables = {
      ENVIRONMENT                  = local.historical_environment
      HISTORICAL_FACTS_BUCKET_NAME = local.historical_bucket_id
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.dlq_gap_consumer,
    aws_iam_role_policy.dlq_gap_consumer,
  ]

  tags = merge(local.common_tags, {
    Name = local.dlq_consumer_function_name
  })
}

resource "aws_lambda_event_source_mapping" "dlq_gap_consumer" {
  count = local.enabled ? 1 : 0

  event_source_arn = aws_sqs_queue.historical_facts_dlq[0].arn
  function_name    = aws_lambda_function.dlq_gap_consumer[0].arn
  batch_size       = 10
  enabled          = true
}
