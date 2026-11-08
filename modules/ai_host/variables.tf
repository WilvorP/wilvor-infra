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

variable "reviewed_getitem_table_arns" {
  type        = list(string)
  description = "Exact DynamoDB base-table ARNs whose reviewed mapping allows GetItem and does not allow Query. Index ARNs and wildcards are rejected. The ledger ARN is not a member of this list."

  validation {
    condition = (
      length(var.reviewed_getitem_table_arns) > 0
      && length(var.reviewed_getitem_table_arns) == length(distinct(var.reviewed_getitem_table_arns))
      && alltrue([
        for arn in var.reviewed_getitem_table_arns :
        can(regex("^arn:aws:dynamodb:[a-z0-9-]+:[0-9]{12}:table/[A-Za-z0-9_.-]+$", arn))
      ])
    )
    error_message = "reviewed_getitem_table_arns must be distinct exact DynamoDB table ARNs without wildcards or index suffixes."
  }
}

variable "reviewed_query_index_arns" {
  type        = list(string)
  description = "Exact DynamoDB GSI ARNs that may be queried. Base-table ARNs, wildcards, and the aircraft callsign index are rejected."

  validation {
    condition = (
      length(var.reviewed_query_index_arns) > 0
      && length(var.reviewed_query_index_arns) == length(distinct(var.reviewed_query_index_arns))
      && alltrue([
        for arn in var.reviewed_query_index_arns :
        can(regex("^arn:aws:dynamodb:[a-z0-9-]+:[0-9]{12}:table/[A-Za-z0-9_.-]+/index/[A-Za-z0-9_.-]+$", arn))
        && !strcontains(arn, "*")
        && !strcontains(arn, "callsign-position_time_epoch-index")
      ])
    )
    error_message = "reviewed_query_index_arns must be distinct exact index ARNs and must not include the callsign index."
  }
}

variable "reviewed_query_only_table_arns" {
  type        = list(string)
  description = "Exact DynamoDB base-table ARNs that may be queried and must not allow GetItem. Used for hazard-coordinates hash/range Query."

  validation {
    condition = (
      length(var.reviewed_query_only_table_arns) > 0
      && length(var.reviewed_query_only_table_arns) == length(distinct(var.reviewed_query_only_table_arns))
      && alltrue([
        for arn in var.reviewed_query_only_table_arns :
        can(regex("^arn:aws:dynamodb:[a-z0-9-]+:[0-9]{12}:table/[A-Za-z0-9_.-]+$", arn))
      ])
    )
    error_message = "reviewed_query_only_table_arns must be distinct exact base-table ARNs without wildcards or index suffixes."
  }
}

variable "reviewed_getitem_and_query_table_arns" {
  type        = list(string)
  description = "Exact DynamoDB base-table ARNs that allow both GetItem and Query. Airport assessment is queried by its evaluation_id hash key, not by its airport_id GSI."

  validation {
    condition = (
      length(var.reviewed_getitem_and_query_table_arns) > 0
      && length(var.reviewed_getitem_and_query_table_arns) == length(distinct(var.reviewed_getitem_and_query_table_arns))
      && alltrue([
        for arn in var.reviewed_getitem_and_query_table_arns :
        can(regex("^arn:aws:dynamodb:[a-z0-9-]+:[0-9]{12}:table/[A-Za-z0-9_.-]+$", arn))
      ])
    )
    error_message = "reviewed_getitem_and_query_table_arns must be distinct exact base-table ARNs without wildcards or index suffixes."
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}
