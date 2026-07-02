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

variable "github_repository" {
  type        = string
  description = "GitHub repository allowed to push the Fargate probe image"
  default     = "WilvorP/wilvor-infra"
}

variable "github_actions_branch" {
  type        = string
  description = "GitHub branch allowed to assume the GitHub Actions AWS role"
  default     = "github-actions-fargate-probe"
}