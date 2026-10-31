variable "name_prefix" {
  type        = string
  description = "Development name prefix. The deployed function name is the prefix plus -ai-host and must stay distinct from the operational API Lambda."

  validation {
    condition     = length(var.name_prefix) > 0 && !strcontains(var.name_prefix, "*")
    error_message = "name_prefix must be a non-empty value without a wildcard."
  }
}

variable "aws_region" {
  type        = string
  description = "Region of the isolated AI host. Wilvor development uses us-west-1."
}

variable "account_id" {
  type        = string
  description = "Account that may assume the AI host role. Passed from the root caller identity. This module does not accept a cross-account principal."

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be the 12-digit account id from the caller identity."
  }
}

variable "log_retention_days" {
  type        = number
  default     = 3
  description = "Short retention for the closed AI host log group."

  validation {
    condition     = contains([1, 3, 5, 7, 14], var.log_retention_days)
    error_message = "log_retention_days must be a short development retention value."
  }
}

variable "enable_invoke" {
  type        = bool
  default     = false
  nullable    = false
  description = "When false, reserved concurrency is 0 and the function cannot run. This switch does not create POST /ai/query, a function URL, or a Lambda permission."
}

variable "tags" {
  type    = map(string)
  default = {}
}
