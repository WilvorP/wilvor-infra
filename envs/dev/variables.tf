variable "aws_region" {
  description = "AWS region for Wilvor Dev infrastructure"
  type        = string

  validation {
    condition     = var.aws_region == "us-west-1"
    error_message = "Wilvor development infrastructure is deployed only in us-west-1."
  }
}

variable "aws_profile" {
  description = "AWS CLI profile used for Wilvor Dev"
  type        = string

  validation {
    condition     = var.aws_profile == "wilvor-dev"
    error_message = "Wilvor development infrastructure uses the wilvor-dev AWS profile."
  }
}

variable "project_name" {
  description = "Project name"
  type        = string

  validation {
    condition     = var.project_name == "wilvor"
    error_message = "This root is the Wilvor project."
  }
}

variable "environment" {
  description = "Deployment environment"
  type        = string

  validation {
    condition     = var.environment == "dev"
    error_message = "This root is the development environment."
  }
}
