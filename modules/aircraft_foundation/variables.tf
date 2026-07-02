variable "name_prefix" {
  description = "Name prefix for Wilvor resources, for example wilvor-dev"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "account_id" {
  description = "AWS account ID used to make globally unique S3 bucket names"
  type        = string
}

variable "tags" {
  description = "Common tags"
  type        = map(string)
  default     = {}
}


variable "opensky_poller_zip_path" {
  description = "Path to the zipped OpenSky poller Lambda package"
  type        = string
}

variable "enable_opensky_poller_schedule" {
  description = "Whether the OpenSky poller EventBridge schedule is enabled"
  type        = bool
  default     = false
}

variable "opensky_poller_schedule_expression" {
  description = "Schedule expression for the OpenSky poller"
  type        = string
  default     = "rate(5 minutes)"
}

variable "sigmet_poller_zip_path" {
  description = "Path to the zipped SIGMET poller Lambda package"
  type        = string
}

variable "enable_sigmet_poller_schedule" {
  description = "Whether the SIGMET poller EventBridge schedule is enabled"
  type        = bool
  default     = false
}

variable "sigmet_poller_schedule_expression" {
  description = "Schedule expression for the SIGMET poller"
  type        = string
  default     = "rate(2 minutes)"
}

variable "sigmet_api_url" {
  description = "NOAA Aviation Weather SIGMET/AIRMET API URL"
  type        = string
  default     = "https://aviationweather.gov/api/data/airsigmet?format=geojson"
}