variable "aws_region" {
  description = "AWS region for Wilvor Dev infrastructure"
  type        = string
}

variable "aws_profile" {
  description = "AWS CLI profile used for Wilvor Dev"
  type        = string
}

variable "project_name" {
  description = "Project name"
  type        = string
}

variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "ai_bedrock_model_id" {
  description = "Bedrock model or inference profile ID used by the AI Copilot"
  type        = string
}

variable "ai_bedrock_foundation_model_id" {
  description = "Foundation model ID authorized behind the AI inference profile"
  type        = string
}

variable "enable_ai_event_triggers" {
  description = "Enable event-driven AI insight generation"
  type        = bool
  default     = false
}

variable "enable_ai_network_summary_schedule" {
  description = "Enable the scheduled five-minute AI network summary"
  type        = bool
  default     = false
}
