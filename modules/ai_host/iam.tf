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
    sid    = "DenyOtherDynamoDbTables"
    effect = "Deny"

    actions = [
      "dynamodb:*",
    ]

    not_resources = [
      aws_dynamodb_table.ledger.arn,
    ]
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
}
