output "environment" {
  value = var.environment
}

output "aws_region" {
  value = var.aws_region
}

output "name_prefix" {
  value = local.name_prefix
}

output "aircraft_raw_stream_name" {
  value = module.aircraft_foundation.aircraft_raw_stream_name
}

output "aircraft_clean_stream_name" {
  value = module.aircraft_foundation.aircraft_clean_stream_name
}

output "aircraft_archive_bucket_name" {
  value = module.aircraft_foundation.aircraft_archive_bucket_name
}

output "aircraft_current_state_table_name" {
  value = module.aircraft_foundation.aircraft_current_state_table_name
}

output "opensky_poller_lambda_name" {
  value = module.aircraft_foundation.opensky_poller_lambda_name
}

output "opensky_poller_schedule_name" {
  value = module.aircraft_foundation.opensky_poller_schedule_name
}

output "opensky_poller_schedule_state" {
  value = module.aircraft_foundation.opensky_poller_schedule_state
}


output "opensky_credentials_secret_name" {
  value = module.aircraft_foundation.opensky_credentials_secret_name
}

output "opensky_credentials_secret_arn" {
  value = module.aircraft_foundation.opensky_credentials_secret_arn
}

output "sigmet_raw_stream_name" {
  value = module.aircraft_foundation.sigmet_raw_stream_name
}

output "sigmet_clean_stream_name" {
  value = module.aircraft_foundation.sigmet_clean_stream_name
}

output "active_hazards_table_name" {
  value = module.aircraft_foundation.active_hazards_table_name
}

output "hazard_cells_table_name" {
  value = module.aircraft_foundation.hazard_cells_table_name
}

output "sigmet_poller_function_name" {
  value = module.aircraft_foundation.sigmet_poller_function_name
}

output "sigmet_poller_schedule_name" {
  value = module.aircraft_foundation.sigmet_poller_schedule_name
}

output "sigmet_poller_schedule_state" {
  value = module.aircraft_foundation.sigmet_poller_schedule_state
}
