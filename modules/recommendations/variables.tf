variable "name_prefix" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "risk_results_table_name" {
  type = string
}

variable "risk_results_table_arn" {
  type = string
}

variable "airport_assessment_table_name" {
  type = string
}

variable "airport_assessment_table_arn" {
  type = string
}

variable "processor_zip_path" {
  type = string
}

variable "event_bus_name" {
  type = string
}

variable "event_bus_arn" {
  type = string
}

variable "enable_event_trigger" {
  type    = bool
  default = true
}

variable "dynamodb_read_capacity" {
  type    = number
  default = 5
}

variable "dynamodb_write_capacity" {
  type    = number
  default = 10
}

variable "enable_point_in_time_recovery" {
  type    = bool
  default = false
}

variable "log_retention_days" {
  type    = number
  default = 3
}
