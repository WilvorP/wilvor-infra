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

output "aircraft_raw_processor_lambda_name" {
  value = module.aircraft_foundation.aircraft_raw_processor_lambda_name
}

output "aircraft_raw_processor_lambda_arn" {
  value = module.aircraft_foundation.aircraft_raw_processor_lambda_arn
}

output "aircraft_current_state_writer_lambda_name" {
  value = module.aircraft_foundation.aircraft_current_state_writer_lambda_name
}

output "aircraft_current_state_writer_lambda_arn" {
  value = module.aircraft_foundation.aircraft_current_state_writer_lambda_arn
}
output "sigmet_raw_stream_name" {
  value = module.sigmet.raw_stream_name
}

output "active_hazards_table_name" {
  value = module.sigmet.active_hazards_table_name
}

output "hazard_cells_table_name" {
  value = module.sigmet.hazard_cells_table_name
}

output "sigmet_poller_function_name" {
  value = module.sigmet.poller_function_name
}

output "sigmet_poller_schedule_name" {
  value = module.sigmet.schedule_name
}

output "sigmet_poller_schedule_state" {
  value = module.sigmet.schedule_state
}

output "metar_archive_bucket_name" {
  value = module.metar.archive_bucket_name
}

output "metar_raw_stream_name" {
  value = module.metar.raw_stream_name
}

output "metar_latest_table_name" {
  value = module.metar.metar_latest_table_name
}

output "metar_poller_function_name" {
  value = module.metar.poller_function_name
}

output "metar_poller_schedule_name" {
  value = module.metar.schedule_name
}

output "metar_poller_schedule_state" {
  value = module.metar.schedule_state
}

output "sigmet_processor_lambda_name" {
  value = module.sigmet.processor_function_name
}

output "sigmet_processor_lambda_arn" {
  value = module.sigmet.processor_function_arn
}

output "weather_changed_log_group_name" {
  value = module.weather_events.log_group_name
}

output "sigmet_archive_bucket_name" {
  value = module.sigmet.archive_bucket_name
}

output "sigmet_dashboard_name" {
  value = module.sigmet.dashboard_name
}

output "metar_dashboard_name" {
  value = module.metar.dashboard_name
}

output "weather_events_dashboard_name" {
  value = module.weather_events.dashboard_name
}
