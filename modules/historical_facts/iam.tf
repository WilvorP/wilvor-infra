data "aws_iam_policy_document" "transform_assume" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

data "aws_iam_policy_document" "firehose_assume" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["firehose.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

data "aws_iam_policy_document" "eventbridge_assume" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "transform" {
  count = local.enabled ? 1 : 0

  name               = "${var.name_prefix}-historical-facts-transform-role"
  assume_role_policy = data.aws_iam_policy_document.transform_assume.json

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-historical-facts-transform-role"
  })
}

data "aws_iam_policy_document" "transform" {
  count = local.enabled ? 1 : 0

  statement {
    sid    = "WriteLambdaLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "${aws_cloudwatch_log_group.transform[0].arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "transform" {
  count = local.enabled ? 1 : 0

  name   = "${var.name_prefix}-historical-facts-transform-policy"
  role   = aws_iam_role.transform[0].id
  policy = data.aws_iam_policy_document.transform[0].json
}

resource "aws_iam_role" "firehose" {
  count = local.enabled ? 1 : 0

  name               = "${var.name_prefix}-historical-facts-firehose-role"
  assume_role_policy = data.aws_iam_policy_document.firehose_assume.json

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-historical-facts-firehose-role"
  })
}

data "aws_iam_policy_document" "firehose" {
  count = local.enabled ? 1 : 0

  statement {
    sid    = "WriteHistoricalFactsObjects"
    effect = "Allow"

    actions = [
      "s3:AbortMultipartUpload",
      "s3:GetObject",
      "s3:PutObject",
    ]

    resources = [
      "${data.aws_s3_bucket.historical_facts[0].arn}/*",
    ]
  }

  statement {
    sid    = "ListHistoricalFactsBucket"
    effect = "Allow"

    actions = [
      "s3:GetBucketLocation",
      "s3:ListBucket",
      "s3:ListBucketMultipartUploads",
    ]

    resources = [
      data.aws_s3_bucket.historical_facts[0].arn,
    ]
  }

  statement {
    sid    = "InvokeHistoricalFactsTransform"
    effect = "Allow"

    actions = [
      "lambda:InvokeFunction",
      "lambda:GetFunctionConfiguration",
    ]

    resources = [
      aws_lambda_function.transform[0].arn,
      "${aws_lambda_function.transform[0].arn}:*",
    ]
  }

  statement {
    sid    = "WriteFirehoseLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "${aws_cloudwatch_log_group.firehose["facts"].arn}:*",
      "${aws_cloudwatch_log_group.firehose["geometry"].arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "firehose" {
  count = local.enabled ? 1 : 0

  name   = "${var.name_prefix}-historical-facts-firehose-policy"
  role   = aws_iam_role.firehose[0].id
  policy = data.aws_iam_policy_document.firehose[0].json
}

resource "aws_iam_role" "eventbridge" {
  count = local.enabled ? 1 : 0

  name               = "${var.name_prefix}-historical-facts-eventbridge-role"
  assume_role_policy = data.aws_iam_policy_document.eventbridge_assume.json

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-historical-facts-eventbridge-role"
  })
}

data "aws_iam_policy_document" "eventbridge" {
  count = local.enabled ? 1 : 0

  statement {
    sid    = "PutHistoricalFactsRecords"
    effect = "Allow"

    actions = [
      "firehose:PutRecord",
      "firehose:PutRecordBatch",
    ]

    resources = [
      aws_kinesis_firehose_delivery_stream.historical["facts"].arn,
    ]
  }
}

resource "aws_iam_role_policy" "eventbridge" {
  count = local.enabled ? 1 : 0

  name   = "${var.name_prefix}-historical-facts-eventbridge-policy"
  role   = aws_iam_role.eventbridge[0].id
  policy = data.aws_iam_policy_document.eventbridge[0].json
}
