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