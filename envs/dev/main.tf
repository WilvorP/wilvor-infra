locals {
  name_prefix = "${var.project_name}-${var.environment}"

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "Terraform"
    Phase       = "aircraft-current-state-v2"
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

  event_bus_name = local.default_event_bus_name
  event_bus_arn  = local.default_event_bus_arn

  aircraft_h3_resolution      = 4
  aircraft_state_ttl_seconds  = 1800
  aircraft_fresh_seconds      = 60
  aircraft_acceptable_seconds = 180

  tags = local.common_tags
}

locals {
  default_event_bus_name = "default"
  default_event_bus_arn  = "arn:aws:events:${var.aws_region}:${data.aws_caller_identity.current.account_id}:event-bus/default"
}

module "weather_events" {
  source = "../../modules/weather_events"

  name_prefix    = local.name_prefix
  aws_region     = var.aws_region
  event_bus_name = local.default_event_bus_name
  tags           = local.common_tags
}

module "sigmet" {
  source = "../../modules/sigmet"

  name_prefix = local.name_prefix
  aws_region  = var.aws_region
  account_id  = data.aws_caller_identity.current.account_id

  sigmet_poller_zip_path = (
    "${path.root}/../../functions/weather/sigmet/poller/dist/sigmet_poller.zip"
  )

  sigmet_processor_zip_path = (
    "${path.root}/../../functions/weather/sigmet/processor/dist/sigmet_processor.zip"
  )

  sigmet_hazard_coordinates_processor_zip_path = (
    "${path.root}/../../functions/weather/sigmet/hazard_coordinates_processor/dist/sigmet_hazard_coordinates_processor.zip"
  )

  sigmet_hazard_station_candidates_processor_zip_path = (
    "${path.root}/../../functions/weather/sigmet/hazard_station_candidates_processor/dist/sigmet_hazard_station_candidates_processor.zip"
  )

  station_reference_table_name    = module.station_reference.station_reference_table_name
  station_reference_table_arn     = module.station_reference.station_reference_table_arn
  station_reference_h3_index_name = "h3-station-index"

  hazard_station_selection_radius_nm      = 50
  hazard_station_selection_config_version = "hazard-station-selection-v1"

  enable_sigmet_poller_schedule     = false
  sigmet_poller_schedule_expression = "rate(2 minutes)"
  sigmet_api_url                    = "https://aviationweather.gov/api/data/airsigmet?format=geojson"

  event_bus_name = local.default_event_bus_name
  event_bus_arn  = local.default_event_bus_arn

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

  metar_processor_zip_path = (
    "${path.root}/../../functions/weather/metar/processor/dist/metar_processor.zip"
  )

  hazard_station_candidates_table_name = (
    module.sigmet.hazard_station_candidates_table_name
  )

  hazard_station_candidates_table_arn = (
    module.sigmet.hazard_station_candidates_table_arn
  )

  event_bus_name = local.default_event_bus_name

  # Keep disabled for the first deployment.
  # We will first prove the event-driven HSC path works.
  enable_metar_poller_schedule = false

  metar_poller_schedule_expression = (
    "rate(3 minutes)"
  )

  # Base endpoint only.
  # station IDs are added dynamically by the Lambda.
  metar_api_url = (
    "https://aviationweather.gov/api/data/metar?format=geojson"
  )

  metar_station_chunk_size = 100

  metar_fresh_seconds = 600

  metar_acceptable_seconds = 1800

  metar_ttl_seconds = 86400

  tags = local.common_tags
}

module "taf" {
  source = "../../modules/taf"

  name_prefix                          = local.name_prefix
  aws_region                           = var.aws_region
  account_id                           = data.aws_caller_identity.current.account_id
  hazard_station_candidates_table_name = module.sigmet.hazard_station_candidates_table_name
  hazard_station_candidates_table_arn  = module.sigmet.hazard_station_candidates_table_arn

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
  taf_station_ids        = ""
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

module "station_reference" {
  source = "../../modules/station_reference"

  name_prefix = local.name_prefix
  environment = var.environment
  aws_region  = var.aws_region

  account_id = data.aws_caller_identity.current.account_id

  station_reference_loader_zip_path = (
    "${path.root}/../../functions/station_reference/loader/dist/station_reference_loader.zip"
  )

  station_cache_url = "https://aviationweather.gov/data/cache/stations.cache.json.gz"

  # Static/reference load. Keep schedule disabled until manual AWS testing passes.
  enable_station_reference_loader_schedule     = false
  station_reference_loader_schedule_expression = "rate(1 day)"

  station_h3_resolution = 4

  event_bus_name = local.default_event_bus_name
  event_bus_arn  = local.default_event_bus_arn

  archive_force_destroy         = true
  bad_record_retention_days     = 30
  log_retention_days            = 3
  lambda_memory_size            = 1024
  lambda_timeout_seconds        = 300
  http_timeout_seconds          = 60
  enable_point_in_time_recovery = false
  dynamodb_read_capacity        = 10
  dynamodb_write_capacity       = 200

  tags = local.common_tags
}

module "airport_status" {
  source = "../../modules/airport_status"

  name_prefix = local.name_prefix
  environment = var.environment
  aws_region  = var.aws_region
  account_id  = data.aws_caller_identity.current.account_id

  airport_status_materializer_zip_path = (
    "${path.root}/../../functions/airport_status/materializer/dist/airport_status_materializer.zip"
  )

  station_reference_table_name = module.station_reference.station_reference_table_name
  station_reference_table_arn  = module.station_reference.station_reference_table_arn

  metar_latest_table_name = module.metar.metar_latest_table_name
  metar_latest_table_arn  = module.metar.metar_latest_table_arn

  taf_latest_table_name = module.taf.taf_latest_table_name
  taf_latest_table_arn  = module.taf.taf_latest_table_arn

  event_bus_name = local.default_event_bus_name
  event_bus_arn  = local.default_event_bus_arn

  airport_status_ttl_seconds = 86400
  metar_fresh_seconds        = 1800
  taf_fresh_seconds          = 21600

  enable_point_in_time_recovery = false
  dynamodb_read_capacity        = 5
  dynamodb_write_capacity       = 5

  tags = local.common_tags
}

module "projection" {
  source = "../../modules/projection"

  name_prefix = local.name_prefix
  aws_region  = var.aws_region

  aircraft_current_state_table_name = (
    module.aircraft_foundation.aircraft_current_state_table_name
  )

  aircraft_current_state_table_arn = (
    module.aircraft_foundation.aircraft_current_state_table_arn
  )

  impact_cells_table_name = (
    module.sigmet.impact_cells_table_name
  )

  impact_cells_table_arn = (
    module.sigmet.impact_cells_table_arn
  )

  active_hazards_table_name = (
    module.sigmet.active_hazards_table_name
  )

  active_hazards_table_arn = (
    module.sigmet.active_hazards_table_arn
  )

  projection_processor_zip_path = (
    "../../functions/projection/processor/dist/projection_processor.zip"
  )

  event_bus_name = local.default_event_bus_name
  event_bus_arn  = local.default_event_bus_arn

  # Keep disabled until AircraftProjection parent
  # materialization is implemented.
  enable_projection_event_trigger = true

  dynamodb_read_capacity  = 5
  dynamodb_write_capacity = 25

  enable_point_in_time_recovery = false

  tags = local.common_tags
}