variable "name_prefix" {
  description = "Common Wilvor resource name prefix, for example wilvor-dev"
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
  description = "AWS account ID"
  type        = string
}

variable "tags" {
  description = "Common resource tags"
  type        = map(string)
  default     = {}
}

variable "airport_status_materializer_zip_path" {
  description = "Path to the AirportStatus materializer Lambda ZIP"
  type        = string
}

variable "station_reference_table_name" {
  type = string
}

variable "station_reference_table_arn" {
  type = string
}

variable "metar_latest_table_name" {
  type = string
}

variable "metar_latest_table_arn" {
  type = string
}

variable "taf_latest_table_name" {
  type = string
}

variable "taf_latest_table_arn" {
  type = string
}

variable "event_bus_name" {
  type    = string
  default = "default"
}

variable "event_bus_arn" {
  type = string
}

variable "airport_status_ttl_seconds" {
  type    = number
  default = 86400
}

variable "metar_fresh_seconds" {
  type    = number
  default = 1800
}

variable "taf_fresh_seconds" {
  type    = number
  default = 21600
}

variable "dynamodb_read_capacity" {
  type    = number
  default = 5
}

variable "dynamodb_write_capacity" {
  type    = number
  default = 5
}

variable "enable_point_in_time_recovery" {
  type    = bool
  default = false
}

variable "lambda_memory_size" {
  type    = number
  default = 256
}

variable "lambda_timeout_seconds" {
  type    = number
  default = 60
}

variable "log_retention_days" {
  type    = number
  default = 3
}