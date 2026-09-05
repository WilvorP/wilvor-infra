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

output "taf_archive_bucket_name" {
  value = module.taf.archive_bucket_name
}

output "taf_raw_stream_name" {
  value = module.taf.raw_stream_name
}

output "taf_raw_stream_arn" {
  value = module.taf.raw_stream_arn
}

output "taf_latest_table_name" {
  value = module.taf.taf_latest_table_name
}

output "taf_poller_function_name" {
  value = module.taf.poller_function_name
}

output "taf_processor_lambda_name" {
  value = module.taf.processor_function_name
}

output "taf_processor_lambda_arn" {
  value = module.taf.processor_function_arn
}

output "taf_poller_schedule_name" {
  value = module.taf.schedule_name
}

output "taf_poller_schedule_state" {
  value = module.taf.schedule_state
}

output "taf_dashboard_name" {
  value = module.taf.dashboard_name
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

output "metar_processor_lambda_name" {
  value = module.metar.processor_function_name
}

output "metar_processor_lambda_arn" {
  value = module.metar.processor_function_arn
}

output "metar_processor_event_source_mapping_uuid" {
  value = module.metar.processor_event_source_mapping_uuid
}

output "runway_archive_bucket_name" {
  value = module.runway_metadata.archive_bucket_name
}

output "runway_reference_table_name" {
  value = module.runway_metadata.runway_reference_table_name
}

output "runway_loader_function_name" {
  value = module.runway_metadata.loader_function_name
}

output "runway_loader_function_arn" {
  value = module.runway_metadata.loader_function_arn
}

output "runway_loader_log_group_name" {
  value = module.runway_metadata.loader_log_group_name
}

output "runway_loader_schedule_name" {
  value = module.runway_metadata.schedule_name
}

output "runway_loader_schedule_state" {
  value = module.runway_metadata.schedule_state
}

output "runway_dashboard_name" {
  value = module.runway_metadata.dashboard_name
}

output "station_reference_archive_bucket_name" {
  value = module.station_reference.archive_bucket_name
}

output "station_reference_table_name" {
  value = module.station_reference.station_reference_table_name
}

output "station_reference_table_arn" {
  value = module.station_reference.station_reference_table_arn
}

output "station_reference_loader_function_name" {
  value = module.station_reference.loader_function_name
}

output "station_reference_loader_function_arn" {
  value = module.station_reference.loader_function_arn
}

output "station_reference_loader_log_group_name" {
  value = module.station_reference.loader_log_group_name
}

output "station_reference_loader_schedule_name" {
  value = module.station_reference.schedule_name
}

output "station_reference_loader_schedule_state" {
  value = module.station_reference.schedule_state
}

output "hazard_coordinates_table_name" {
  value = module.sigmet.hazard_coordinates_table_name
}

output "impact_cells_table_name" {
  value = module.sigmet.impact_cells_table_name
}

output "hazard_station_candidates_table_name" {
  value = module.sigmet.hazard_station_candidates_table_name
}

output "operational_api_endpoint" {
  value = (
    module.operational_api.api_endpoint
  )
}

output "operational_api_id" {
  value = (
    module.operational_api.api_id
  )
}

output "operational_api_lambda_name" {
  value = (
    module.operational_api.lambda_function_name
  )
}

output "operational_api_lambda_arn" {
  value = (
    module.operational_api.lambda_function_arn
  )
}

output "operational_api_log_group_name" {
  value = (
    module.operational_api.lambda_log_group_name
  )
}

output "ai_copilot_api_endpoint" {
  value = module.ai_copilot.api_endpoint
}

output "ai_copilot_api_id" {
  value = module.ai_copilot.api_id
}

output "ai_copilot_lambda_name" {
  value = module.ai_copilot.lambda_function_name
}

output "ai_copilot_lambda_arn" {
  value = module.ai_copilot.lambda_function_arn
}

output "ai_copilot_log_group_name" {
  value = module.ai_copilot.lambda_log_group_name
}

output "ai_insights_table_name" {
  value = module.ai_copilot.insights_table_name
}

output "ai_network_summary_schedule_name" {
  value = module.ai_copilot.network_summary_schedule_name
}

output "ai_network_summary_schedule_state" {
  value = module.ai_copilot.network_summary_schedule_state
}

output "ai_event_rule_names" {
  value = module.ai_copilot.event_rule_names
}

output "ai_event_dlq_name" {
  value = module.ai_copilot.event_dlq_name
}

output "ai_copilot_alarm_names" {
  value = module.ai_copilot.alarm_names
}