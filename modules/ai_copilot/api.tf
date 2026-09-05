data "aws_partition" "current" {}

data "aws_caller_identity" "current" {}

locals {
  route_keys = toset([
    "GET /health",
    "POST /ai/chat",
    "POST /ai/summaries/network",
    "POST /ai/aircraft/{aircraftId}/explain",
    "POST /ai/airports/{airportId}/summarize",
    "POST /ai/recommendations/{recommendationId}/explain",
    "POST /ai/alerts/{alertId}/incident-summary",
    "GET /ai/insights/{subjectType}/{subjectId}",
  ])
}

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = [
      "sts:AssumeRole",
    ]

    principals {
      type = "Service"
      identifiers = [
        "lambda.amazonaws.com",
      ]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name = "${var.name_prefix}-ai-copilot-lambda-role"

  assume_role_policy = (
    data.aws_iam_policy_document.lambda_assume_role.json
  )

  tags = var.tags
}

data "aws_iam_policy_document" "lambda" {
  statement {
    sid = "InvokeConfiguredBedrockModel"
    actions = [
      "bedrock:InvokeModel",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:bedrock:*::foundation-model/${var.bedrock_foundation_model_id}",
      "arn:${data.aws_partition.current.partition}:bedrock:${var.aws_region}::inference-profile/${var.bedrock_model_id}",
      "arn:${data.aws_partition.current.partition}:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:inference-profile/${var.bedrock_model_id}",
    ]

    condition {
      test     = "ArnEquals"
      variable = "bedrock:InferenceProfileArn"
      values = [
        "arn:${data.aws_partition.current.partition}:bedrock:${var.aws_region}::inference-profile/${var.bedrock_model_id}",
        "arn:${data.aws_partition.current.partition}:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:inference-profile/${var.bedrock_model_id}",
      ]
    }
  }

  statement {
    sid = "ReadWriteAiInsightsOnly"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
    ]
    resources = [
      aws_dynamodb_table.insights.arn,
    ]
  }

  statement {
    sid = "WriteLambdaLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "${aws_cloudwatch_log_group.lambda.arn}:*",
    ]
  }

  statement {
    sid = "SendFailedAsyncEventsToDlq"
    actions = [
      "sqs:SendMessage",
    ]
    resources = [
      aws_sqs_queue.event_dlq.arn,
    ]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name = "${var.name_prefix}-ai-copilot-lambda-policy"
  role = aws_iam_role.lambda.id

  policy = data.aws_iam_policy_document.lambda.json
}

resource "aws_cloudwatch_log_group" "lambda" {
  name = "/aws/lambda/${var.name_prefix}-ai-copilot"

  retention_in_days = var.log_retention_days

  tags = var.tags
}

resource "aws_lambda_function" "ai_copilot" {
  function_name = "${var.name_prefix}-ai-copilot"
  filename      = var.lambda_zip_path

  source_code_hash = filebase64sha256(
    var.lambda_zip_path
  )

  role    = aws_iam_role.lambda.arn
  handler = "app.lambda_handler"
  runtime = "python3.12"

  memory_size = var.lambda_memory_size
  timeout     = var.lambda_timeout_seconds

  reserved_concurrent_executions = (
    var.lambda_reserved_concurrency
  )

  environment {
    variables = {
      OPERATIONAL_API_BASE_URL        = var.operational_api_base_url
      AI_INSIGHTS_TABLE_NAME          = aws_dynamodb_table.insights.name
      BEDROCK_MODEL_ID                = var.bedrock_model_id
      AI_MAX_OUTPUT_TOKENS            = tostring(var.ai_max_output_tokens)
      AI_TEMPERATURE                  = tostring(var.ai_temperature)
      AI_MAX_TOOL_ROUNDS              = tostring(var.ai_max_tool_rounds)
      PROMPT_VERSION                  = var.prompt_version
      AI_MAX_MESSAGE_CHARS            = tostring(var.ai_max_message_chars)
      AI_MAX_HISTORY_ITEMS            = tostring(var.ai_max_history_items)
      AI_MAX_CONTEXT_BYTES            = "131072"
      AI_MAX_OPERATION_SECONDS        = "25"
      AI_CACHE_TTL_SECONDS            = tostring(var.ai_cache_ttl_seconds)
      AI_INSIGHT_RETENTION_SECONDS    = tostring(var.ai_insight_retention_seconds)
      OPERATIONAL_API_TIMEOUT_SECONDS = "5"
      BEDROCK_CONNECT_TIMEOUT_SECONDS = "2"
      BEDROCK_READ_TIMEOUT_SECONDS    = "10"
    }
  }

  depends_on = [
    aws_iam_role_policy.lambda,
    aws_cloudwatch_log_group.lambda,
  ]

  tags = merge(
    var.tags,
    {
      Component = "ai-copilot"
    }
  )
}

resource "aws_apigatewayv2_api" "http" {
  name          = "${var.name_prefix}-ai-copilot-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_credentials = false
    allow_headers = [
      "authorization",
      "content-type",
    ]
    allow_methods = [
      "GET",
      "POST",
      "OPTIONS",
    ]
    allow_origins = var.cors_allowed_origins
    max_age       = 300
  }

  tags = merge(
    var.tags,
    {
      Component = "ai-copilot"
    }
  )
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  integration_uri        = aws_lambda_function.ai_copilot.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
}

resource "aws_apigatewayv2_route" "routes" {
  for_each = local.route_keys

  api_id    = aws_apigatewayv2_api.http.id
  route_key = each.value
  target = (
    "integrations/${aws_apigatewayv2_integration.lambda.id}"
  )
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = (
      var.api_throttling_burst_limit
    )
    throttling_rate_limit = (
      var.api_throttling_rate_limit
    )
  }

  tags = var.tags
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ai_copilot.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn = (
    "${aws_apigatewayv2_api.http.execution_arn}/*/*"
  )
}
