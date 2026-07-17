variable "name_prefix" {
  description = "Name prefix for Wilvor resources, for example wilvor-dev"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "account_id" {
  description = "AWS account ID used to create a globally unique S3 bucket name"
  type        = string
}

variable "tags" {
  description = "Common resource tags"
  type        = map(string)
  default     = {}
}

variable "metar_poller_zip_path" {
  description = "Path to the zipped METAR poller Lambda package"
  type        = string
}

variable "enable_metar_poller_schedule" {
  description = "Whether the METAR poller EventBridge schedule is enabled"
  type        = bool
  default     = false
}

variable "metar_poller_schedule_expression" {
  description = "Schedule expression for the METAR poller"
  type        = string
  default     = "rate(3 minutes)"
}

variable "metar_api_url" {
  description = "NOAA Aviation Weather METAR API URL"
  type        = string
  default     = "https://aviationweather.gov/api/data/metar?format=geojson"
}

variable "archive_force_destroy" {
  description = "Whether Terraform may delete the METAR bucket when it contains objects"
  type        = bool
  default     = true
}

variable "raw_archive_retention_days" {
  description = "Number of days to retain raw METAR API responses"
  type        = number
  default     = 3
}

variable "bad_record_retention_days" {
  description = "Number of days to retain rejected METAR records"
  type        = number
  default     = 7
}

variable "metar_processor_zip_path" {
  description = "Path to the packaged METAR processor Lambda ZIP file."
  type        = string
}