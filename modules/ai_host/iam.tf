data "aws_iam_policy_document" "assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values = [
        "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.function_name}",
      ]
    }
  }
}

resource "aws_iam_role" "host" {
  name               = local.role_name
  assume_role_policy = data.aws_iam_policy_document.assume_role.json

  tags = merge(
    var.tags,
    {
      Component = "ai-host"
    }
  )
}

data "aws_iam_policy_document" "host" {
  statement {
    sid = "ReadAndPutExecutionLedger"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:TransactWriteItems",
    ]

    resources = [
      aws_dynamodb_table.ledger.arn,
    ]
  }

  statement {
    sid    = "DenyLedgerProhibitedApis"
    effect = "Deny"

    actions = local.prohibited_ledger_actions

    resources = [
      aws_dynamodb_table.ledger.arn,
      "${aws_dynamodb_table.ledger.arn}/index/*",
    ]
  }

  statement {
    sid = "ReadReviewedGetItemTables"

    actions = [
      "dynamodb:GetItem",
    ]

    resources = var.reviewed_getitem_table_arns
  }

  statement {
    sid    = "DenyReviewedGetItemTableWrites"
    effect = "Deny"

    actions = local.prohibited_getitem_table_actions

    resources = var.reviewed_getitem_table_arns
  }

  statement {
    sid    = "DenyUnreviewedDynamoDb"
    effect = "Deny"

    actions = [
      "dynamodb:*",
    ]

    # Exact table ARNs only. An index ARN does not match these values, so
    # this deny still covers every index, including the aircraft callsign GSI.
    not_resources = concat(
      [aws_dynamodb_table.ledger.arn],
      var.reviewed_getitem_table_arns,
    )
  }

  statement {
    sid    = "DenyModelSecretsAndInvoke"
    effect = "Deny"

    actions = [
      "bedrock:*",
      "secretsmanager:GetSecretValue",
      "secretsmanager:GetSecret",
      "lambda:InvokeFunction",
      "lambda:AddPermission",
      "lambda:CreateFunctionUrlConfig",
      "execute-api:Invoke",
    ]

    resources = [
      "*",
    ]
  }

  statement {
    sid = "WriteAiHostLogs"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "${aws_cloudwatch_log_group.host.arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "host" {
  name   = "${var.name_prefix}-ai-host-policy"
  role   = aws_iam_role.host.id
  policy = data.aws_iam_policy_document.host.json

  lifecycle {
    precondition {
      condition = alltrue([
        for arn in var.reviewed_getitem_table_arns :
        startswith(arn, "arn:aws:dynamodb:${var.aws_region}:${var.account_id}:table/")
        && !strcontains(arn, "*")
        && !strcontains(arn, "/index/")
        && arn != aws_dynamodb_table.ledger.arn
      ])
      error_message = "Reviewed GetItem tables must be exact base-table ARNs in this account and region, and must not be the execution ledger."
    }
  }
}
