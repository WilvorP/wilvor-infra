variable "name_prefix" {
  type = string
}

variable "api_zip_path" {
  type = string
}

variable "table_names" {
  type = map(string)
}

variable "table_arns" {
  type = list(string)
}

variable "cors_allowed_origins" {
  type = list(string)

  default = [
    "http://localhost:3000",
    "http://localhost:5173",
  ]
}

variable "log_retention_days" {
  type    = number
  default = 3
}

variable "lambda_memory_size" {
  type    = number
  default = 512
}

variable "lambda_timeout_seconds" {
  type    = number
  default = 15
}

variable "lambda_reserved_concurrency" {
  type    = number
  default = -1
}

variable "api_throttling_burst_limit" {
  type    = number
  default = 50
}

variable "api_throttling_rate_limit" {
  type    = number
  default = 25
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "enable_historical_facts" {
  type        = bool
  default     = false
  description = "When true, register the historical-facts CloudWatch dashboard in the Operational API catalog."
}

variable "enable_historical_analytics_dashboard" {
  type        = bool
  default     = false
  description = "When true, register the historical-analytics CloudWatch dashboard in the Operational API catalog."
}

variable "enable_ai_query_route" {
  type        = bool
  default     = false
  nullable    = false
  description = "When true, add POST /ai/query to the existing HTTP API. The route stays absent unless this is set and the external JWT issuer, audience, and AI Lambda values are all configured. This module does not create an identity provider or the AI function."
}

variable "ai_jwt_issuer" {
  type        = string
  default     = ""
  nullable    = false
  description = "Externally managed JWT issuer URL for the optional POST /ai/query authorizer. Required only when enable_ai_query_route is true. This module does not create an identity provider or issuer."

  validation {
    condition = (
      var.ai_jwt_issuer == "" ||
      can(regex(
        "^https://[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9](:[0-9]{1,5})?(/[][A-Za-z0-9._~:/?#@!$&'()+,;=%-]*)?$",
        var.ai_jwt_issuer,
      ))
    )

    error_message = "ai_jwt_issuer must be empty or an externally configured https issuer URL."
  }
}

variable "ai_jwt_audience" {
  type        = list(string)
  default     = []
  nullable    = false
  description = "Externally managed JWT audience values for the optional POST /ai/query authorizer. At least one value is required when enable_ai_query_route is true. This module does not create a client or audience."

  validation {
    condition = alltrue([
      for audience in var.ai_jwt_audience :
      length(audience) > 0 &&
      !can(regex("[[:space:]]", audience)) &&
      !strcontains(audience, "*")
    ])

    error_message = "ai_jwt_audience values must be non-empty externally configured audiences without spaces or wildcards."
  }
}

variable "ai_lambda_function_arn" {
  type        = string
  default     = ""
  nullable    = false
  description = "ARN of the external AI Lambda invoked by POST /ai/query. Required only when enable_ai_query_route is true. This module does not create that function."

  validation {
    condition = (
      var.ai_lambda_function_arn == "" ||
      (
        can(regex(
          "^arn:aws[a-z0-9-]*:lambda:[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+:[0-9]{12}:function:[A-Za-z0-9-_]{1,64}(?::[A-Za-z0-9-_]+)?$",
          var.ai_lambda_function_arn,
        )) &&
        !can(regex(
          "^arn:aws[a-z0-9-]*:lambda:[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+:0{12}:function:",
          var.ai_lambda_function_arn,
        ))
      )
    )

    error_message = "ai_lambda_function_arn must be empty or an explicit Lambda function ARN. This module does not create that function."
  }
}

variable "ai_lambda_invoke_arn" {
  type        = string
  default     = ""
  nullable    = false
  description = "API Gateway integration URI for the external AI Lambda. Required only when enable_ai_query_route is true, and it must be the invoke ARN of ai_lambda_function_arn."

  validation {
    condition = (
      var.ai_lambda_invoke_arn == "" ||
      can(regex(
        "^arn:aws[a-z0-9-]*:apigateway:[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+:lambda:path/2015-03-31/functions/arn:aws[a-z0-9-]*:lambda:[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+:[0-9]{12}:function:[A-Za-z0-9-_]{1,64}(?::[A-Za-z0-9-_]+)?/invocations$",
        var.ai_lambda_invoke_arn,
      ))
    )

    error_message = "ai_lambda_invoke_arn must be empty or the API Gateway invoke ARN of an explicit Lambda function."
  }
}