locals {
  name_prefix = "${var.project_name}-${var.environment}"

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "Terraform"
    Phase       = "metar-complete-pipeline"
  }
}

data "aws_caller_identity" "current" {}

locals {
  default_event_bus_name = "default"
  default_event_bus_arn  = "arn:aws:events:${var.aws_region}:${data.aws_caller_identity.current.account_id}:event-bus/default"
}

module "weather_events" {
  source = "../../modules/weather_events"

  name_prefix = local.name_prefix
  aws_region  = var.aws_region

  event_bus_name = local.default_event_bus_name

  tags = local.common_tags
}

module "metar" {
  source = "../../modules/metar"

  name_prefix = local.name_prefix
  aws_region  = var.aws_region
  account_id  = data.aws_caller_identity.current.account_id

  metar_poller_zip_path = (
    "${path.root}/../../functions/weather/metar/poller/dist/metar_poller.zip"
  )

  enable_metar_poller_schedule = false

  metar_poller_schedule_expression = "rate(3 minutes)"

  metar_api_url = "https://aviationweather.gov/api/data/metar?ids=KSFO,KOAK,KSJC&format=geojson"

  tags = local.common_tags
}