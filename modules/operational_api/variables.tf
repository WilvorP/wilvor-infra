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