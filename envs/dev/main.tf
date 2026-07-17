locals {
  name_prefix = "${var.project_name}-${var.environment}"

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "Terraform"
    Phase       = "taf-complete-pipeline"
  }

  default_event_bus_name = "default"
  default_event_bus_arn = (
    "arn:aws:events:${var.aws_region}:${data.aws_caller_identity.current.account_id}:event-bus/default"
  )
}

data "aws_caller_identity" "current" {}

module "taf" {
  source = "../../modules/taf"

  name_prefix = local.name_prefix
  aws_region  = var.aws_region
  account_id  = data.aws_caller_identity.current.account_id

  taf_poller_zip_path = (
    "${path.root}/../../functions/weather/taf/poller/dist/taf_poller.zip"
  )

  taf_processor_zip_path = (
    "${path.root}/../../functions/weather/taf/processor/dist/taf_processor.zip"
  )

  # Keep automatic polling disabled until manual testing is complete.
  enable_taf_poller_schedule     = false
  taf_poller_schedule_expression = "rate(10 minutes)"

  taf_api_url            = "https://aviationweather.gov/api/data/taf"
  taf_station_ids        = "KSFO,KOAK,KSJC"
  taf_station_chunk_size = 100

  # The processor can publish Weather.changed to the existing AWS default bus.
  # No weather-events logging/dashboard module is created on this branch.
  event_bus_name = local.default_event_bus_name
  event_bus_arn  = local.default_event_bus_arn

  archive_force_destroy      = true
  raw_archive_retention_days = 3
  bad_record_retention_days  = 7

  tags = local.common_tags
}


module "weather_events" {
  source = "../../modules/weather_events"

  name_prefix    = local.name_prefix
  aws_region     = var.aws_region
  event_bus_name = local.default_event_bus_name
  tags           = local.common_tags
}
