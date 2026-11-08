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
    sid = "QueryReviewedIndexes"

    actions = [
      "dynamodb:Query",
    ]

    resources = var.reviewed_query_index_arns
  }

  statement {
    sid    = "DenyReviewedQueryIndexMutations"
    effect = "Deny"

    actions = local.prohibited_query_target_actions

    resources = var.reviewed_query_index_arns
  }

  statement {
    sid = "QueryReviewedBaseTables"

    actions = [
      "dynamodb:Query",
    ]

    resources = var.reviewed_query_only_table_arns
  }

  statement {
    sid    = "DenyQueryOnlyTableNonQuery"
    effect = "Deny"

    actions = local.prohibited_query_target_actions

    resources = var.reviewed_query_only_table_arns
  }

  statement {
    sid = "ReadAndQueryReviewedBaseTables"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:Query",
    ]

    resources = var.reviewed_getitem_and_query_table_arns
  }

  statement {
    sid    = "DenyGetItemAndQueryTableMutations"
    effect = "Deny"

    actions = local.prohibited_getitem_and_query_actions

    resources = var.reviewed_getitem_and_query_table_arns
  }

  statement {
    sid    = "DenyUnreviewedDynamoDb"
    effect = "Deny"

    actions = [
      "dynamodb:*",
    ]

    # Every reviewed index and base-table ARN must be excluded. An index ARN
    # does not match its parent table ARN, so leaving an index out of this
    # list makes dynamodb:* deny override the Query allow. Unlisted indexes,
    # including the aircraft callsign GSI, stay denied.
    not_resources = concat(
      [aws_dynamodb_table.ledger.arn],
      local.reviewed_dynamodb_arns,
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
      condition = (
        length(local.reviewed_dynamodb_arns) == length(distinct(local.reviewed_dynamodb_arns))
        && alltrue([
          for arn in local.reviewed_dynamodb_arns :
          startswith(arn, "arn:aws:dynamodb:${var.aws_region}:${var.account_id}:table/")
          && !strcontains(arn, "*")
          && arn != aws_dynamodb_table.ledger.arn
          && !strcontains(arn, "callsign-position_time_epoch-index")
        ])
        && alltrue([
          for arn in concat(
            var.reviewed_getitem_table_arns,
            var.reviewed_query_only_table_arns,
            var.reviewed_getitem_and_query_table_arns,
          ) : !strcontains(arn, "/index/")
        ])
        && alltrue([
          for arn in var.reviewed_query_index_arns : strcontains(arn, "/index/")
        ])
      )
      error_message = "Reviewed DynamoDB reads must be disjoint exact ARNs in this account and region, must not be the ledger, and must not include the callsign index."
    }
  }
}
