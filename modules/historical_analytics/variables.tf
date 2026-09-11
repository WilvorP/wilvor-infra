variable "name_prefix" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "account_id" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "enable_historical_analytics" {
  type        = bool
  default     = false
  description = "When false, this module creates no AWS resources and does not look up the persistent historical bucket. Must remain false until analytics enablement is explicitly approved."
}

variable "result_retention_days" {
  type        = number
  default     = 3
  description = "Days to retain disposable Athena query-result objects. Derived data only; not canonical historical truth."

  validation {
    condition     = var.result_retention_days > 0
    error_message = "result_retention_days must be greater than 0."
  }
}

variable "projection_year_min" {
  type        = number
  default     = 2026
  description = "Inclusive lower bound for Athena partition projection on canonical event-time year."

  validation {
    condition     = var.projection_year_min > 0
    error_message = "projection_year_min must be greater than 0."
  }
}

variable "projection_year_max" {
  type        = number
  default     = 2036
  description = "Inclusive upper bound for Athena partition projection on canonical event-time year."

  validation {
    condition     = var.projection_year_max > 0
    error_message = "projection_year_max must be greater than 0."
  }
}
