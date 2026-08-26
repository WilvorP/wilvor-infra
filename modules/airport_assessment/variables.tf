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

variable "aircraft_current_state_table_name" {
  type = string
}

variable "aircraft_current_state_table_arn" {
  type = string
}

variable "airport_status_table_name" {
  type = string
}

variable "airport_status_table_arn" {
  type = string
}

variable "taf_forecast_periods_table_name" {
  type = string
}

variable "taf_forecast_periods_table_arn" {
  type = string
}

variable "taf_station_period_index_name" {
  type    = string
  default = "station_id-period_from_epoch-index"
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
  default = false
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