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