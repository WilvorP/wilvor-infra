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

variable "historical_fact_retention_days" {
  type    = number
  default = 365

  validation {
    condition     = var.historical_fact_retention_days > 0
    error_message = "historical_fact_retention_days must be greater than 0."
  }
}

variable "historical_fact_error_retention_days" {
  type    = number
  default = 30

  validation {
    condition     = var.historical_fact_error_retention_days > 0
    error_message = "historical_fact_error_retention_days must be greater than 0."
  }
}

variable "historical_fact_metadata_retention_days" {
  type    = number
  default = 365

  validation {
    condition     = var.historical_fact_metadata_retention_days > 0
    error_message = "historical_fact_metadata_retention_days must be greater than 0."
  }
}

variable "historical_fact_noncurrent_version_retention_days" {
  type    = number
  default = 30

  validation {
    condition     = var.historical_fact_noncurrent_version_retention_days > 0
    error_message = "historical_fact_noncurrent_version_retention_days must be greater than 0."
  }
}
