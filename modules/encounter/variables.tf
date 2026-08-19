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

variable "event_bus_name" {
  type    = string
  default = "default"
}

variable "event_bus_arn" {
  type = string
}

variable "encounter_processor_zip_path" {
  type = string
}

variable "enable_encounter_event_trigger" {
  type    = bool
  default = true
}

variable "log_retention_days" {
  type    = number
  default = 3
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

variable "aircraft_projection_table_name" {
  type = string
}

variable "aircraft_projection_table_arn" {
  type = string
}

variable "aircraft_projection_cells_table_name" {
  type = string
}

variable "aircraft_projection_cells_table_arn" {
  type = string
}

variable "aircraft_projection_cells_h3_index_name" {
  type = string
}

variable "hazard_cells_table_name" {
  type = string
}

variable "hazard_cells_table_arn" {
  type = string
}

variable "hazard_cells_hazard_version_index_name" {
  type = string
}

variable "active_hazards_table_name" {
  type = string
}

variable "active_hazards_table_arn" {
  type = string
}

variable "hazard_coordinates_table_name" {
  type = string
}

variable "hazard_coordinates_table_arn" {
  type = string
}

variable "encounter_retention_seconds" {
  type    = number
  default = 3600
}

variable "max_matched_h3_cells" {
  type    = number
  default = 200
}