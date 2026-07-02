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

  opensky_poller_zip_path = "${path.root}/../../functions/opensky_poller/dist/opensky_poller.zip"

  enable_opensky_poller_schedule     = false
  opensky_poller_schedule_expression = "rate(5 minutes)"

  sigmet_poller_zip_path = "${path.root}/../../functions/sigmet_poller/dist/sigmet_poller.zip"

  enable_sigmet_poller_schedule     = false
  sigmet_poller_schedule_expression = "rate(2 minutes)"
  sigmet_api_url                    = "https://aviationweather.gov/api/data/airsigmet?format=geojson"

  tags = local.common_tags
}