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

variable "event_bus_name" {
  description = "EventBridge bus used for aircraft state events"
  type        = string
  default     = "default"
}

variable "event_bus_arn" {
  description = "ARN of the EventBridge bus used for aircraft state events"
  type        = string
}

variable "aircraft_h3_resolution" {
  description = "H3 resolution used for current aircraft positions"
  type        = number
  default     = 4
}

variable "aircraft_state_ttl_seconds" {
  description = "Aircraft current-state retention in seconds"
  type        = number
  default     = 1800
}

variable "aircraft_fresh_seconds" {
  description = "Maximum position age classified as FRESH"
  type        = number
  default     = 60
}

variable "aircraft_acceptable_seconds" {
  description = "Maximum position age classified as ACCEPTABLE"
  type        = number
  default     = 180
}