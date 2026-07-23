locals {
  name_prefix = "${var.project_name}-${var.environment}"

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "Terraform"
    Phase       = "runway-metadata"
  }
}

data "aws_caller_identity" "current" {}

# module "aircraft_foundation" {
#   source = "../../modules/aircraft_foundation"

#   name_prefix = local.name_prefix
#   aws_region  = var.aws_region
#   account_id  = data.aws_caller_identity.current.account_id

#   opensky_poller_zip_path = "${path.root}/../../functions/opensky_poller/dist/opensky_poller.zip"

#   enable_opensky_poller_schedule     = false
#   opensky_poller_schedule_expression = "rate(5 minutes)"

#   tags = local.common_tags
# }

locals {
  default_event_bus_name = "default"
  default_event_bus_arn  = "arn:aws:events:${var.aws_region}:${data.aws_caller_identity.current.account_id}:event-bus/default"
}

# module "weather_events" {
#   source = "../../modules/weather_events"

#   name_prefix    = local.name_prefix
#   aws_region     = var.aws_region
#   event_bus_name = local.default_event_bus_name
#   tags           = local.common_tags
# }

# module "sigmet" {
#   source = "../../modules/sigmet"

#   name_prefix = local.name_prefix
#   aws_region  = var.aws_region
#   account_id  = data.aws_caller_identity.current.account_id

#   sigmet_poller_zip_path = (
#     "${path.root}/../../functions/weather/sigmet/poller/dist/sigmet_poller.zip"
#   )

#   sigmet_processor_zip_path = (
#     "${path.root}/../../functions/weather/sigmet/processor/dist/sigmet_processor.zip"
#   )

#   enable_sigmet_poller_schedule     = false
#   sigmet_poller_schedule_expression = "rate(2 minutes)"
#   sigmet_api_url                    = "https://aviationweather.gov/api/data/airsigmet?format=geojson"

#   event_bus_name = local.default_event_bus_name
#   event_bus_arn  = local.default_event_bus_arn

#   tags = local.common_tags
# }

# module "metar" {
#   source = "../../modules/metar"

#   name_prefix = local.name_prefix
#   aws_region  = var.aws_region
#   account_id  = data.aws_caller_identity.current.account_id

#   metar_poller_zip_path = (
#     "${path.root}/../../functions/weather/metar/poller/dist/metar_poller.zip"
#   )

#   metar_processor_zip_path = (
#     "${path.root}/../../functions/weather/metar/processor/dist/metar_processor.zip"
#   )

#   enable_metar_poller_schedule     = false
#   metar_poller_schedule_expression = "rate(3 minutes)"
#   metar_api_url                    = "https://aviationweather.gov/api/data/metar?ids=KSFO,KOAK,KSJC&format=geojson"

#   tags = local.common_tags
# }

# module "taf" {
#   source = "../../modules/taf"

#   name_prefix = local.name_prefix
#   aws_region  = var.aws_region
#   account_id  = data.aws_caller_identity.current.account_id

#   taf_poller_zip_path = (
#     "${path.root}/../../functions/weather/taf/poller/dist/taf_poller.zip"
#   )

#   taf_processor_zip_path = (
#     "${path.root}/../../functions/weather/taf/processor/dist/taf_processor.zip"
#   )

#   # Keep automatic polling disabled until manual testing is complete.
#   enable_taf_poller_schedule     = false
#   taf_poller_schedule_expression = "rate(10 minutes)"

#   taf_api_url            = "https://aviationweather.gov/api/data/taf"
#   taf_station_ids        = "KSFO,KOAK,KSJC"
#   taf_station_chunk_size = 100

#   # The processor can publish Weather.changed to the existing AWS default bus.
#   # No weather-events logging/dashboard module is created on this branch.
#   event_bus_name = local.default_event_bus_name
#   event_bus_arn  = local.default_event_bus_arn

#   archive_force_destroy      = true
#   raw_archive_retention_days = 3
#   bad_record_retention_days  = 7

#   tags = local.common_tags
# }

module "runway_metadata" {
  source = "../../modules/runway_metadata"

  name_prefix = local.name_prefix
  environment = var.environment
  aws_region  = var.aws_region

  account_id = (
    data.aws_caller_identity.current.account_id
  )

  runway_loader_zip_path = (
    "${path.root}/../../functions/runway_metadata/loader/dist/runway_metadata_loader.zip"
  )

  supported_airport_ids = [
    "KSFO",
    "KOAK",
    "KSJC",
  ]

  # Keep automatic execution disabled until manual AWS testing passes.
  enable_runway_loader_schedule = false

  runway_loader_schedule_expression = "rate(1 day)"

  # Values already validated against the real FAA source package.
  default_source_cycle = "2026-07-09"

  faa_apt_zip_url = (
    "https://nfdc.faa.gov/webContent/28DaySub/extra/09_Jul_2026_APT_CSV.zip"
  )

  event_bus_name = local.default_event_bus_name
  event_bus_arn  = local.default_event_bus_arn

  archive_force_destroy     = true
  bad_record_retention_days = 30

  log_retention_days          = 3
  lambda_memory_size          = 2048
  lambda_timeout_seconds      = 600
  lambda_ephemeral_storage_mb = 2048
  http_timeout_seconds        = 120

  enable_point_in_time_recovery = false

  dynamodb_read_capacity  = 5
  dynamodb_write_capacity = 5

  tags = local.common_tags
}