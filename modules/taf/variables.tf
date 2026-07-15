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

variable "taf_poller_zip_path" {
  type = string
}

variable "taf_processor_zip_path" {
  type = string
}

variable "enable_taf_poller_schedule" {
  type    = bool
  default = false
}

variable "taf_poller_schedule_expression" {
  type    = string
  default = "rate(10 minutes)"
}

variable "taf_api_url" {
  type    = string
  default = "https://aviationweather.gov/api/data/taf"
}

variable "taf_station_ids" {
  type    = string
  default = "KSFO,KOAK,KSJC"
}

variable "taf_station_chunk_size" {
  type    = number
  default = 100
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
