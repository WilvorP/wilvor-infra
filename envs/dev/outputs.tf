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

output "opensky_fargate_probe_ecr_repository_url" {
  value = module.aircraft_foundation.opensky_fargate_probe_ecr_repository_url
}

output "opensky_fargate_probe_cluster_name" {
  value = module.aircraft_foundation.opensky_fargate_probe_cluster_name
}

output "opensky_fargate_probe_task_definition_arn" {
  value = module.aircraft_foundation.opensky_fargate_probe_task_definition_arn
}

output "opensky_fargate_probe_subnet_id" {
  value = module.aircraft_foundation.opensky_fargate_probe_subnet_id
}

output "opensky_fargate_probe_security_group_id" {
  value = module.aircraft_foundation.opensky_fargate_probe_security_group_id
}

output "opensky_fargate_probe_log_group_name" {
  value = module.aircraft_foundation.opensky_fargate_probe_log_group_name
}

output "github_actions_ecr_push_role_arn" {
  value = module.aircraft_foundation.github_actions_ecr_push_role_arn
}
