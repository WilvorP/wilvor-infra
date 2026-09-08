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

variable "enable_historical_facts" {
  type        = bool
  default     = false
  description = "When false, this module creates no AWS resources and SIGMET historical geometry transport stays unset. Must remain false until Phase 2A.1c durable coverage/gap and monitoring are reviewed."
}

variable "event_bus_name" {
  type    = string
  default = "default"
}

variable "event_bus_arn" {
  type = string
}

variable "transform_zip_path" {
  type = string
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

variable "historical_fact_noncurrent_version_retention_days" {
  type    = number
  default = 30

  validation {
    condition     = var.historical_fact_noncurrent_version_retention_days > 0
    error_message = "historical_fact_noncurrent_version_retention_days must be greater than 0."
  }
}

variable "historical_facts_force_destroy" {
  type    = bool
  default = true
}

variable "firehose_buffering_interval_seconds" {
  type    = number
  default = 900

  validation {
    condition     = var.firehose_buffering_interval_seconds >= 60
    error_message = "firehose_buffering_interval_seconds must be at least 60."
  }
}

variable "firehose_buffering_size_mib" {
  type    = number
  default = 128

  validation {
    condition     = var.firehose_buffering_size_mib >= 64
    error_message = "firehose_buffering_size_mib must be at least 64 for dynamic partitioning."
  }
}

variable "dlq_message_retention_seconds" {
  type    = number
  default = 1209600

  validation {
    condition     = var.dlq_message_retention_seconds == 1209600
    error_message = "dlq_message_retention_seconds must be 1209600 (14 days)."
  }
}

variable "log_retention_days" {
  type    = number
  default = 3

  validation {
    condition     = var.log_retention_days > 0
    error_message = "log_retention_days must be greater than 0."
  }
}
