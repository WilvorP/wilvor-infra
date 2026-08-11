variable "name_prefix" {
  description = "Common Wilvor resource name prefix"
  type        = string
}

variable "environment" {
  description = "Deployment environment such as dev or prod"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "account_id" {
  description = "AWS account ID used in globally unique resource names"
  type        = string
}

variable "tags" {
  description = "Common resource tags"
  type        = map(string)
  default     = {}
}

variable "station_reference_loader_zip_path" {
  description = "Path to the StationReference Lambda deployment ZIP"
  type        = string
}

variable "station_cache_url" {
  description = "Aviation Weather station cache URL"
  type        = string
  default     = "https://aviationweather.gov/data/cache/stations.cache.json.gz"
}

variable "default_source_version" {
  description = "Fallback station reference source version used when the response has no Last-Modified header"
  type        = string
  default     = "awc-stations-current"
}

variable "schema_version" {
  description = "StationReference schema version emitted by the loader"
  type        = string
  default     = "station-reference-v1"
}

variable "station_h3_resolution" {
  description = "H3 resolution used for StationReference spatial selection"
  type        = number
  default     = 4

  validation {
    condition     = var.station_h3_resolution >= 0 && var.station_h3_resolution <= 15
    error_message = "station_h3_resolution must be between 0 and 15."
  }
}

variable "event_bus_name" {
  description = "EventBridge bus receiving station.reference.updated"
  type        = string
  default     = "default"
}

variable "event_bus_arn" {
  description = "ARN of the EventBridge bus"
  type        = string
}

variable "enable_station_reference_loader_schedule" {
  description = "Whether the automatic StationReference loader schedule is enabled"
  type        = bool
  default     = false
}

variable "station_reference_loader_schedule_expression" {
  description = "StationReference loader EventBridge schedule expression"
  type        = string
  default     = "rate(1 day)"
}

variable "archive_force_destroy" {
  description = "Whether Terraform may delete a non-empty archive bucket"
  type        = bool
  default     = false
}

variable "bad_record_retention_days" {
  description = "Number of days to retain rejected station reference records"
  type        = number
  default     = 30

  validation {
    condition     = var.bad_record_retention_days >= 1
    error_message = "bad_record_retention_days must be at least 1."
  }
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 3
}

variable "lambda_memory_size" {
  description = "StationReference loader Lambda memory in MB"
  type        = number
  default     = 1024
}

variable "lambda_timeout_seconds" {
  description = "StationReference loader Lambda timeout in seconds"
  type        = number
  default     = 300

  validation {
    condition     = var.lambda_timeout_seconds >= 1 && var.lambda_timeout_seconds <= 900
    error_message = "lambda_timeout_seconds must be between 1 and 900."
  }
}

variable "http_timeout_seconds" {
  description = "Aviation Weather station-cache HTTP request timeout"
  type        = number
  default     = 60
}

variable "enable_point_in_time_recovery" {
  description = "Enable DynamoDB point-in-time recovery"
  type        = bool
  default     = false
}

variable "dynamodb_read_capacity" {
  description = "Provisioned DynamoDB read capacity units"
  type        = number
  default     = 50

  validation {
    condition     = var.dynamodb_read_capacity >= 1
    error_message = "dynamodb_read_capacity must be at least 1."
  }
}

variable "dynamodb_write_capacity" {
  description = "Provisioned DynamoDB write capacity units"
  type        = number
  default     = 25

  validation {
    condition     = var.dynamodb_write_capacity >= 1
    error_message = "dynamodb_write_capacity must be at least 1."
  }
}
