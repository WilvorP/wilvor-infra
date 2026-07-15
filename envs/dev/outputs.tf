output "environment" {
  value = var.environment
}

output "aws_region" {
  value = var.aws_region
}

output "name_prefix" {
  value = local.name_prefix
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

output "weather_changed_log_group_name" {
  value = module.weather_events.log_group_name
}

output "metar_dashboard_name" {
  value = module.metar.dashboard_name
}

output "weather_events_dashboard_name" {
  value = module.weather_events.dashboard_name
}