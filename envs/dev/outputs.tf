output "environment" {
  value = var.environment
}

output "aws_region" {
  value = var.aws_region
}

output "name_prefix" {
  value = local.name_prefix
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

output "weather_changed_log_group_name" {
  value = module.weather_events.log_group_name
}

output "weather_changed_rule_name" {
  value = module.weather_events.rule_name
}

output "weather_events_dashboard_name" {
  value = module.weather_events.dashboard_name
}