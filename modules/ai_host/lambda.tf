data "archive_file" "host" {
  type        = "zip"
  source_file = "${path.module}/src/handler.py"
  output_path = "${path.module}/dist/ai_host.zip"
}

resource "aws_cloudwatch_log_group" "host" {
  name              = local.log_group_name
  retention_in_days = var.log_retention_days

  tags = merge(
    var.tags,
    {
      Component = "ai-host"
    }
  )
}

resource "aws_lambda_function" "host" {
  function_name    = local.function_name
  filename         = data.archive_file.host.output_path
  source_code_hash = data.archive_file.host.output_base64sha256
  role             = aws_iam_role.host.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  memory_size      = 128
  timeout          = 3

  # Closed by default. Zero concurrency rejects invokes before the handler.
  # There is no function URL and no API Gateway permission in this module.
  reserved_concurrent_executions = local.reserved_concurrent_executions

  environment {
    variables = {
      AI_QUERY_ROUTE_ENABLED        = "false"
      PRODUCTION_ISSUANCE_AVAILABLE = "false"
      LEDGER_WRITES_ENABLED         = "false"
      LEDGER_TABLE_NAME             = aws_dynamodb_table.ledger.name
      NAME_PREFIX                   = var.name_prefix
    }
  }

  depends_on = [
    aws_iam_role_policy.host,
    aws_cloudwatch_log_group.host,
  ]

  tags = merge(
    var.tags,
    {
      Component   = "ai-host"
      RouteStatus = "ai-query-disabled"
    }
  )

  lifecycle {
    precondition {
      condition     = local.function_name != local.operational_api_function_name
      error_message = "The AI host Lambda must stay distinct from the operational API Lambda."
    }

    precondition {
      condition     = var.enable_invoke == false || local.reserved_concurrent_executions <= local.reviewed_invoke_concurrency
      error_message = "AI host invoke concurrency cannot exceed the reviewed cap of 2."
    }
  }
}
