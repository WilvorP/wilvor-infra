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

  cloudwatch_dashboard_ids = concat(
    [
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
    ],
    var.enable_historical_facts ? ["historical-facts"] : [],
    var.enable_historical_analytics_dashboard ? ["historical-analytics"] : [],
  )

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

  ai_query_route_key = "POST /ai/query"

  ai_jwt_issuer_pattern = "^https://[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9](:[0-9]{1,5})?(/[][A-Za-z0-9._~:/?#@!$&'()+,;=%-]*)?$"

  ai_lambda_function_arn_pattern = "^arn:aws[a-z0-9-]*:lambda:[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+:[0-9]{12}:function:[A-Za-z0-9-_]{1,64}(?::[A-Za-z0-9-_]+)?$"

  ai_lambda_zero_account_pattern = "^arn:aws[a-z0-9-]*:lambda:[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+:0{12}:function:"

  ai_lambda_qualified_arn_pattern = "^(arn:aws[a-z0-9-]*:lambda:[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+:[0-9]{12}:function:[A-Za-z0-9-_]{1,64}):([A-Za-z0-9-_]+)$"

  ai_jwt_issuer_configured = can(regex(
    local.ai_jwt_issuer_pattern,
    var.ai_jwt_issuer,
  ))

  ai_jwt_audience_configured = (
    length(var.ai_jwt_audience) > 0 &&
    alltrue([
      for audience in var.ai_jwt_audience :
      length(audience) > 0 &&
      !can(regex("[[:space:]]", audience)) &&
      !strcontains(audience, "*")
    ])
  )

  ai_lambda_function_arn_configured = (
    can(regex(
      local.ai_lambda_function_arn_pattern,
      var.ai_lambda_function_arn,
    )) &&
    !can(regex(
      local.ai_lambda_zero_account_pattern,
      var.ai_lambda_function_arn,
    ))
  )

  ai_lambda_qualified = can(regex(
    local.ai_lambda_qualified_arn_pattern,
    var.ai_lambda_function_arn,
  ))

  ai_lambda_permission_function_name = (
    local.ai_lambda_qualified
    ? regex(
      local.ai_lambda_qualified_arn_pattern,
      var.ai_lambda_function_arn,
    )[0]
    : var.ai_lambda_function_arn
  )

  ai_lambda_permission_qualifier = (
    local.ai_lambda_qualified
    ? regex(
      local.ai_lambda_qualified_arn_pattern,
      var.ai_lambda_function_arn,
    )[1]
    : ""
  )

  ai_lambda_invoke_arn_expected = (
    local.ai_lambda_function_arn_configured
    ? "arn:${split(":", var.ai_lambda_function_arn)[1]}:apigateway:${split(":", var.ai_lambda_function_arn)[3]}:lambda:path/2015-03-31/functions/${var.ai_lambda_function_arn}/invocations"
    : ""
  )

  ai_lambda_invoke_configured = (
    local.ai_lambda_function_arn_configured &&
    local.ai_lambda_invoke_arn_expected != "" &&
    var.ai_lambda_invoke_arn == local.ai_lambda_invoke_arn_expected
  )

  ai_lambda_targets_operational_api = (
    endswith(
      var.ai_lambda_function_arn,
      ":function:${var.name_prefix}-operational-api",
    ) ||
    strcontains(
      var.ai_lambda_function_arn,
      ":function:${var.name_prefix}-operational-api:",
    )
  )

  ai_query_route_configuration_valid = (
    local.ai_jwt_issuer_configured &&
    local.ai_jwt_audience_configured &&
    local.ai_lambda_invoke_configured &&
    !local.ai_lambda_targets_operational_api
  )

  ai_query_route_configuration_error = join(" ", compact([
    var.enable_ai_query_route && !local.ai_jwt_issuer_configured ? "ai_jwt_issuer must be an externally configured https issuer URL when enable_ai_query_route is true." : "",
    var.enable_ai_query_route && !local.ai_jwt_audience_configured ? "ai_jwt_audience must include at least one externally configured audience when enable_ai_query_route is true." : "",
    var.enable_ai_query_route && !local.ai_lambda_function_arn_configured ? "ai_lambda_function_arn must be an explicit Lambda function ARN when enable_ai_query_route is true. This module does not create that function." : "",
    var.enable_ai_query_route && local.ai_lambda_function_arn_configured && !local.ai_lambda_invoke_configured ? "ai_lambda_invoke_arn must be the API Gateway invoke ARN for ai_lambda_function_arn when enable_ai_query_route is true." : "",
    var.enable_ai_query_route && local.ai_lambda_targets_operational_api ? "POST /ai/query must use a separate AI Lambda, not the operational API Lambda." : "",
    local.ai_query_route_key != "POST /ai/query" ? "The AI route key must remain POST /ai/query." : "",
  ]))

  ai_query_source_arn = "${aws_apigatewayv2_api.http.execution_arn}/${aws_apigatewayv2_stage.default.name}/POST/ai/query"
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
        NAME_PREFIX                           = var.name_prefix
        ENABLE_HISTORICAL_FACTS_DASHBOARD     = var.enable_historical_facts ? "true" : "false"
        ENABLE_HISTORICAL_ANALYTICS_DASHBOARD = var.enable_historical_analytics_dashboard ? "true" : "false"
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

    allow_methods = (
      var.enable_ai_query_route
      ? [
        "GET",
        "OPTIONS",
        "POST",
      ]
      : [
        "GET",
        "OPTIONS",
      ]
    )

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

  lifecycle {
    precondition {
      condition = (
        !var.enable_ai_query_route ||
        (
          local.ai_query_route_configuration_valid &&
          local.ai_query_route_key == "POST /ai/query"
        )
      )

      error_message = coalesce(
        local.ai_query_route_configuration_error,
        "POST /ai/query requires an externally configured JWT issuer, audience, and AI Lambda.",
      )
    }
  }
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


# =====================================================================
# Optional JWT-protected POST /ai/query
# Same HTTP API and origin. Separate Lambda. Absent unless enabled.
# =====================================================================

resource "aws_apigatewayv2_authorizer" "ai_jwt" {
  count = (
    var.enable_ai_query_route ? 1 : 0
  )

  api_id = (
    aws_apigatewayv2_api.http.id
  )

  authorizer_type = "JWT"

  identity_sources = [
    "$request.header.Authorization",
  ]

  name = (
    "${var.name_prefix}-ai-query-jwt"
  )

  jwt_configuration {
    audience = var.ai_jwt_audience

    issuer = var.ai_jwt_issuer
  }

  lifecycle {
    precondition {
      condition = (
        !var.enable_ai_query_route ||
        (
          local.ai_query_route_configuration_valid &&
          local.ai_query_route_key == "POST /ai/query"
        )
      )

      error_message = coalesce(
        local.ai_query_route_configuration_error,
        "POST /ai/query requires an externally configured JWT issuer, audience, and AI Lambda.",
      )
    }
  }
}


resource "aws_apigatewayv2_integration" "ai_query" {
  count = (
    var.enable_ai_query_route ? 1 : 0
  )

  api_id = (
    aws_apigatewayv2_api.http.id
  )

  integration_type   = "AWS_PROXY"
  integration_method = "POST"

  integration_uri = (
    var.ai_lambda_invoke_arn
  )

  payload_format_version = "2.0"

  timeout_milliseconds = 29000

  lifecycle {
    precondition {
      condition = (
        !var.enable_ai_query_route ||
        (
          local.ai_query_route_configuration_valid &&
          local.ai_query_route_key == "POST /ai/query"
        )
      )

      error_message = coalesce(
        local.ai_query_route_configuration_error,
        "POST /ai/query requires an externally configured JWT issuer, audience, and AI Lambda.",
      )
    }
  }
}


resource "aws_apigatewayv2_route" "ai_query" {
  count = (
    var.enable_ai_query_route ? 1 : 0
  )

  api_id = (
    aws_apigatewayv2_api.http.id
  )

  route_key = "POST /ai/query"

  authorization_type = "JWT"

  authorizer_id = (
    aws_apigatewayv2_authorizer.ai_jwt[0].id
  )

  target = (
    "integrations/${aws_apigatewayv2_integration.ai_query[0].id}"
  )

  lifecycle {
    precondition {
      condition = (
        !var.enable_ai_query_route ||
        (
          local.ai_query_route_configuration_valid &&
          local.ai_query_route_key == "POST /ai/query"
        )
      )

      error_message = coalesce(
        local.ai_query_route_configuration_error,
        "POST /ai/query requires an externally configured JWT issuer, audience, and AI Lambda.",
      )
    }
  }
}


resource "aws_lambda_permission" "ai_query" {
  count = (
    var.enable_ai_query_route && !local.ai_lambda_qualified
    ? 1
    : 0
  )

  statement_id = (
    "AllowApiGatewayInvokeAiQuery"
  )

  action = (
    "lambda:InvokeFunction"
  )

  function_name = (
    local.ai_lambda_permission_function_name
  )

  principal = (
    "apigateway.amazonaws.com"
  )

  source_arn = (
    local.ai_query_source_arn
  )

  depends_on = [
    aws_apigatewayv2_route.ai_query,
  ]

  lifecycle {
    precondition {
      condition = (
        !var.enable_ai_query_route ||
        (
          local.ai_query_route_configuration_valid &&
          local.ai_query_route_key == "POST /ai/query"
        )
      )

      error_message = coalesce(
        local.ai_query_route_configuration_error,
        "POST /ai/query requires an externally configured JWT issuer, audience, and AI Lambda.",
      )
    }
  }
}


resource "aws_lambda_permission" "ai_query_qualified" {
  count = (
    var.enable_ai_query_route && local.ai_lambda_qualified
    ? 1
    : 0
  )

  statement_id = (
    "AllowApiGatewayInvokeAiQueryQualified"
  )

  action = (
    "lambda:InvokeFunction"
  )

  function_name = (
    local.ai_lambda_permission_function_name
  )

  qualifier = (
    local.ai_lambda_permission_qualifier
  )

  principal = (
    "apigateway.amazonaws.com"
  )

  source_arn = (
    local.ai_query_source_arn
  )

  depends_on = [
    aws_apigatewayv2_route.ai_query,
  ]

  lifecycle {
    precondition {
      condition = (
        !var.enable_ai_query_route ||
        (
          local.ai_query_route_configuration_valid &&
          local.ai_query_route_key == "POST /ai/query"
        )
      )

      error_message = coalesce(
        local.ai_query_route_configuration_error,
        "POST /ai/query requires an externally configured JWT issuer, audience, and AI Lambda.",
      )
    }
  }
}