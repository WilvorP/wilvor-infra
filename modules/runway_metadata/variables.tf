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

variable "runway_loader_zip_path" {
  description = "Path to the runway metadata Lambda deployment ZIP"
  type        = string
}

variable "supported_airport_ids" {
  description = "ICAO airports included in the runway catalog"
  type        = list(string)

  validation {
    condition = (
      length(var.supported_airport_ids) > 0 &&
      alltrue([
        for airport_id in var.supported_airport_ids :
        can(regex("^[A-Z0-9]{4}$", airport_id))
      ])
    )

    error_message = "supported_airport_ids must contain uppercase four-character ICAO identifiers."
  }
}

variable "faa_apt_zip_url" {
  description = "FAA Airports and Other Landing Facilities CSV ZIP URL"
  type        = string
}

variable "default_source_cycle" {
  description = "FAA source cycle used when the Lambda event does not provide one"
  type        = string
}

variable "event_bus_name" {
  description = "EventBridge bus receiving ReferenceData.changed"
  type        = string
  default     = "default"
}

variable "event_bus_arn" {
  description = "ARN of the EventBridge bus"
  type        = string
}

variable "enable_runway_loader_schedule" {
  description = "Whether the automatic runway loader schedule is enabled"
  type        = bool
  default     = false
}

variable "runway_loader_schedule_expression" {
  description = "Runway loader EventBridge schedule expression"
  type        = string
  default     = "rate(1 day)"
}

variable "archive_force_destroy" {
  description = "Whether Terraform may delete a non-empty archive bucket"
  type        = bool
  default     = false
}

variable "bad_record_retention_days" {
  description = "Number of days to retain rejected runway records"
  type        = number
  default     = 30

  validation {
    condition     = var.bad_record_retention_days >= 1
    error_message = "bad_record_retention_days must be at least 1."
  }
}

variable "log_retention_days" {
  description = "CloudWatch log retention"
  type        = number
  default     = 3
}

variable "lambda_memory_size" {
  description = "Runway loader Lambda memory in MB"
  type        = number
  default     = 2048
}

variable "lambda_timeout_seconds" {
  description = "Runway loader Lambda timeout in seconds"
  type        = number
  default     = 600

  validation {
    condition = (
      var.lambda_timeout_seconds >= 1 &&
      var.lambda_timeout_seconds <= 900
    )

    error_message = "lambda_timeout_seconds must be between 1 and 900."
  }
}

variable "lambda_ephemeral_storage_mb" {
  description = "Lambda temporary storage in MB"
  type        = number
  default     = 2048

  validation {
    condition = (
      var.lambda_ephemeral_storage_mb >= 512 &&
      var.lambda_ephemeral_storage_mb <= 10240
    )

    error_message = "lambda_ephemeral_storage_mb must be between 512 and 10240."
  }
}

variable "http_timeout_seconds" {
  description = "FAA HTTPS request timeout"
  type        = number
  default     = 120
}

variable "enable_point_in_time_recovery" {
  description = "Enable DynamoDB point-in-time recovery"
  type        = bool
  default     = false
}

variable "dynamodb_read_capacity" {
  description = "Provisioned DynamoDB read capacity units"
  type        = number
  default     = 5

  validation {
    condition     = var.dynamodb_read_capacity >= 1
    error_message = "dynamodb_read_capacity must be at least 1."
  }
}

variable "dynamodb_write_capacity" {
  description = "Provisioned DynamoDB write capacity units"
  type        = number
  default     = 5

  validation {
    condition     = var.dynamodb_write_capacity >= 1
    error_message = "dynamodb_write_capacity must be at least 1."
  }
}