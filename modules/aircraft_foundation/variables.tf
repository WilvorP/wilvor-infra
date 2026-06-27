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