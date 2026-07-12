variable "name_prefix" {
  description = "Prefix applied to Wilvor weather event resources"
  type        = string
}

variable "event_bus_name" {
  description = "EventBridge event bus used for Weather.changed events"
  type        = string
  default     = "default"
}

variable "tags" {
  description = "Common tags applied to supported resources"
  type        = map(string)
  default     = {}
}

variable "aws_region" {
  description = "AWS region used by CloudWatch dashboard widgets"
  type        = string
}