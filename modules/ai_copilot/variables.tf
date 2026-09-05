variable "name_prefix" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "lambda_zip_path" {
  type = string
}

variable "operational_api_base_url" {
  type = string
}

variable "bedrock_model_id" {
  type = string
}

variable "bedrock_foundation_model_id" {
  type = string
}

variable "prompt_version" {
  type    = string
  default = "wilvor-ai-v1"
}

variable "ai_max_output_tokens" {
  type    = number
  default = 1200
}

variable "ai_temperature" {
  type    = number
  default = 0.1
}

variable "ai_max_tool_rounds" {
  type    = number
  default = 4
}

variable "ai_max_message_chars" {
  type    = number
  default = 4000
}

variable "ai_max_history_items" {
  type    = number
  default = 10
}

variable "ai_cache_ttl_seconds" {
  type    = number
  default = 300
}

variable "ai_insight_retention_seconds" {
  type    = number
  default = 604800
}

variable "enable_event_triggers" {
  type    = bool
  default = false
}

variable "enable_network_summary_schedule" {
  type    = bool
  default = false
}

variable "network_summary_schedule_expression" {
  type    = string
  default = "rate(5 minutes)"
}

variable "cors_allowed_origins" {
  type = list(string)
  default = [
    "http://localhost:3000",
    "http://localhost:5173",
  ]
}

variable "lambda_memory_size" {
  type    = number
  default = 512
}

variable "lambda_timeout_seconds" {
  type    = number
  default = 30
}

variable "lambda_reserved_concurrency" {
  type    = number
  default = 2
}

variable "log_retention_days" {
  type    = number
  default = 3
}

variable "api_throttling_burst_limit" {
  type    = number
  default = 5
}

variable "api_throttling_rate_limit" {
  type    = number
  default = 2
}

variable "enable_point_in_time_recovery" {
  type    = bool
  default = false
}

variable "tags" {
  type    = map(string)
  default = {}
}
