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
