locals {
  name_prefix = "${var.project_name}-${var.environment}"

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "Terraform"
    Phase       = "aircraft-foundation"
  }
}

data "aws_caller_identity" "current" {}

module "aircraft_foundation" {
  source = "../../modules/aircraft_foundation"

  name_prefix = local.name_prefix
  aws_region  = var.aws_region
  account_id  = data.aws_caller_identity.current.account_id

  tags = local.common_tags
}