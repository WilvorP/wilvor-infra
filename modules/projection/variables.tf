variable "name_prefix" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "dynamodb_read_capacity" {
  type    = number
  default = 5
}

variable "dynamodb_write_capacity" {
  type    = number
  default = 25
}

variable "enable_point_in_time_recovery" {
  type    = bool
  default = false
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "aircraft_current_state_table_name" {
  type = string
}

variable "aircraft_current_state_table_arn" {
  type = string
}

variable "impact_cells_table_name" {
  type = string
}

variable "impact_cells_table_arn" {
  type = string
}

variable "active_hazards_table_name" {
  type = string
}

variable "active_hazards_table_arn" {
  type = string
}

variable "event_bus_name" {
  type    = string
  default = "default"
}

variable "projection_processor_zip_path" {
  type = string
}

variable "enable_projection_event_trigger" {
  type    = bool
  default = false
}

variable "log_retention_days" {
  type    = number
  default = 3
}

variable "event_bus_arn" {
  type = string
}

variable "projection_horizons_min" {
  type    = string
  default = "5,10,15,30"
}

variable "corridor_grid_distances" {
  type    = string
  default = "0,0,1,1"
}

variable "max_corridor_cells" {
  type    = number
  default = 2000
}

variable "projection_config_version" {
  type    = string
  default = "wilvor.projection.config.v1"
}