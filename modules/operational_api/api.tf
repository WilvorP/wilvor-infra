locals {
  dynamodb_resources = distinct(
    concat(
      var.table_arns,
      [
        for arn in var.table_arns :
        "${arn}/index/*"
      ]
    )
  )

  cloudwatch_dashboard_ids = [
    "aircraft-pipeline",
    "aircraft-hazard-encounter",
    "projection-pipeline",
    "sigmet-pipeline",
    "metar-pipeline",
    "taf-pipeline",
    "weather-events",
    "hazard-station-candidates",
    "airport-status",
    "airport-assessment",
    "risk-pipeline",
    "recommendations",
    "active-alerts",
    "runway-metadata",
  ]

  cloudwatch_dashboard_arns = [
    for dashboard_id in local.cloudwatch_dashboard_ids :
    "arn:aws:cloudwatch::${data.aws_caller_identity.current.account_id}:dashboard/${var.name_prefix}-${dashboard_id}"
  ]

  route_keys = toset([
  "GET /health",

  "GET /overview",
  "GET /freshness",
  "GET /system-health",
  "GET /system-health/dashboards/{dashboardId}",
  "GET /system-health/dashboards/{dashboardId}/widgets/{widgetId}/image",

  "GET /aircraft",
  "GET /aircraft/{aircraftId}",

  "GET /map/aircraft",

  "GET /hazards/active",

  "GET /encounters/active",

  "GET /airports",
  "GET /airports/status",
  "GET /airports/{airportId}",

  "GET /recommendations/active",

  "GET /alerts/active",
  ])
}

data "aws_caller_identity" "current" {}


# =====================================================================
# Lambda IAM role
# =====================================================================

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
  name = (
    "${var.name_prefix}-operational-api-lambda-role"
  )

  assume_role_policy = (
    data.aws_iam_policy_document.lambda_assume_role.json
  )

  tags = var.tags
}


# =====================================================================
# Lambda logging
# =====================================================================

resource "aws_cloudwatch_log_group" "lambda" {
  name = (
    "/aws/lambda/${var.name_prefix}-operational-api"
  )

  retention_in_days = (
    var.log_retention_days
  )

  tags = var.tags
}


# =====================================================================
# Read-only DynamoDB permissions
# =====================================================================

data "aws_iam_policy_document" "lambda" {
  statement {
    sid = "ReadOperationalState"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:Scan",
    ]

    resources = (
      local.dynamodb_resources
    )
  }

  statement {
    sid = "ReadOperationalHealth"

    actions = [
      "cloudwatch:DescribeAlarms",
      "cloudwatch:GetMetricStatistics",
      "lambda:GetAccountSettings",
    ]

    resources = [
      "*",
    ]
  }

  statement {
    sid = "ReadAllowlistedDashboards"

    actions = [
      "cloudwatch:GetDashboard",
    ]

    resources = local.cloudwatch_dashboard_arns
  }

  statement {
    sid = "RenderDashboardMetricWidgets"

    # GetMetricWidgetImage does not support resource-level IAM.
    actions = [
      "cloudwatch:GetMetricWidgetImage",
    ]

    resources = [
      "*",
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
}


resource "aws_iam_role_policy" "lambda" {
  name = (
    "${var.name_prefix}-operational-api-policy"
  )

  role = (
    aws_iam_role.lambda.id
  )

  policy = (
    data.aws_iam_policy_document.lambda.json
  )
}


# =====================================================================
# Lambda
# =====================================================================

resource "aws_lambda_function" "api" {
  function_name = (
    "${var.name_prefix}-operational-api"
  )

  filename = (
    var.api_zip_path
  )

  source_code_hash = (
    filebase64sha256(var.api_zip_path)
  )

  role = (
    aws_iam_role.lambda.arn
  )

  handler = "app.lambda_handler"
  runtime = "python3.12"

  memory_size = (
    var.lambda_memory_size
  )

  timeout = (
    var.lambda_timeout_seconds
  )

  reserved_concurrent_executions = (
    var.lambda_reserved_concurrency
  )

  environment {
  variables = merge(
    var.table_names,
    {
      NAME_PREFIX = var.name_prefix
    }
  )
  }

  depends_on = [
    aws_iam_role_policy.lambda,
    aws_cloudwatch_log_group.lambda,
  ]

  tags = merge(
    var.tags,
    {
      Component = "operational-api"
    }
  )
}


# =====================================================================
# HTTP API Gateway
# =====================================================================

resource "aws_apigatewayv2_api" "http" {
  name = (
    "${var.name_prefix}-operational-api"
  )

  protocol_type = "HTTP"

  cors_configuration {
    allow_credentials = false

    allow_headers = [
      "authorization",
      "content-type",
    ]

    allow_methods = [
      "GET",
      "OPTIONS",
    ]

    allow_origins = (
      var.cors_allowed_origins
    )

    max_age = 300
  }

  tags = merge(
    var.tags,
    {
      Component = "operational-api"
    }
  )
}


# =====================================================================
# Lambda integration
# =====================================================================

resource "aws_apigatewayv2_integration" "lambda" {
  api_id = (
    aws_apigatewayv2_api.http.id
  )

  integration_type   = "AWS_PROXY"
  integration_method = "POST"

  integration_uri = (
    aws_lambda_function.api.invoke_arn
  )

  payload_format_version = "2.0"

  timeout_milliseconds = 29000
}


# =====================================================================
# Routes
# =====================================================================

resource "aws_apigatewayv2_route" "routes" {
  for_each = (
    local.route_keys
  )

  api_id = (
    aws_apigatewayv2_api.http.id
  )

  route_key = each.value

  target = (
    "integrations/${aws_apigatewayv2_integration.lambda.id}"
  )
}


# =====================================================================
# Default stage
# =====================================================================

resource "aws_apigatewayv2_stage" "default" {
  api_id = (
    aws_apigatewayv2_api.http.id
  )

  name = "$default"

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


# =====================================================================
# API Gateway -> Lambda permission
# =====================================================================

resource "aws_lambda_permission" "api_gateway" {
  statement_id = (
    "AllowApiGatewayInvoke"
  )

  action = (
    "lambda:InvokeFunction"
  )

  function_name = (
    aws_lambda_function.api.function_name
  )

  principal = (
    "apigateway.amazonaws.com"
  )

  source_arn = (
    "${aws_apigatewayv2_api.http.execution_arn}/*/*"
  )
}