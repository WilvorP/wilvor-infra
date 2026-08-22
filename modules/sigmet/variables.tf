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

variable "sigmet_poller_zip_path" {
  type = string
}

variable "sigmet_processor_zip_path" {
  type = string
}

variable "sigmet_hazard_coordinates_processor_zip_path" {
  type = string
}

variable "enable_sigmet_poller_schedule" {
  type    = bool
  default = false
}

variable "sigmet_poller_schedule_expression" {
  type    = string
  default = "rate(2 minutes)"
}

variable "sigmet_api_url" {
  type    = string
  default = "https://aviationweather.gov/api/data/airsigmet?format=geojson"
}

variable "event_bus_name" {
  type    = string
  default = "default"
}

variable "event_bus_arn" {
  type = string
}

variable "archive_force_destroy" {
  type    = bool
  default = true
}

variable "raw_archive_retention_days" {
  type    = number
  default = 3
}

variable "bad_record_retention_days" {
  type    = number
  default = 7
}

variable "sigmet_hazard_station_candidates_processor_zip_path" {
  type = string
}

variable "station_reference_table_name" {
  type = string
}

variable "station_reference_table_arn" {
  type = string
}

variable "hazard_station_selection_radius_nm" {
  type    = number
  default = 50
}

variable "hazard_station_selection_config_version" {
  type    = string
  default = "hazard-station-selection-v1"
}

variable "station_reference_h3_index_name" {
  type    = string
  default = "h3_cell-station_id-index"
}

variable "hazard_station_candidate_h3_resolution" {
  type    = number
  default = 4
}

variable "impact_grid_distance" {
  type        = number
  default     = 2
  description = "H3 grid-ring expansion around exact SIGMET hazard cells for ImpactCells."
}

variable "impact_radius_nm" {
  type        = number
  default     = 50
  description = "Metadata radius stored on ImpactCells. Actual H3 expansion is controlled by impact_grid_distance."
}

variable "impact_expansion_config_version" {
  type        = string
  default     = "wilvor.impact_expansion.v1"
  description = "Version string used to force impact-cell rematerialization when expansion config changes."
}