output "aircraft_raw_stream_name" {
  value = aws_kinesis_stream.aircraft_raw.name
}

output "aircraft_raw_stream_arn" {
  value = aws_kinesis_stream.aircraft_raw.arn
}

output "aircraft_clean_stream_name" {
  value = aws_kinesis_stream.aircraft_clean.name
}

output "aircraft_clean_stream_arn" {
  value = aws_kinesis_stream.aircraft_clean.arn
}

output "aircraft_archive_bucket_name" {
  value = aws_s3_bucket.aircraft_archive.bucket
}

output "aircraft_current_state_table_name" {
  value = aws_dynamodb_table.aircraft_current_state.name
}

output "aircraft_current_state_table_arn" {
  value = aws_dynamodb_table.aircraft_current_state.arn
}

output "opensky_poller_lambda_name" {
  value = aws_lambda_function.opensky_poller.function_name
}

output "opensky_poller_lambda_arn" {
  value = aws_lambda_function.opensky_poller.arn
}

output "opensky_poller_schedule_name" {
  value = aws_cloudwatch_event_rule.opensky_poller_schedule.name
}

output "opensky_poller_schedule_state" {
  value = var.enable_opensky_poller_schedule ? "ENABLED" : "DISABLED"
}

output "opensky_credentials_secret_name" {
  value = aws_secretsmanager_secret.opensky_credentials.name
}

output "opensky_credentials_secret_arn" {
  value = aws_secretsmanager_secret.opensky_credentials.arn
}

output "opensky_fargate_probe_ecr_repository_url" {
  value = aws_ecr_repository.opensky_fargate_probe.repository_url
}

output "opensky_fargate_probe_cluster_name" {
  value = aws_ecs_cluster.opensky_fargate_probe.name
}

output "opensky_fargate_probe_task_definition_arn" {
  value = aws_ecs_task_definition.opensky_fargate_probe.arn
}

output "opensky_fargate_probe_subnet_id" {
  value = aws_subnet.opensky_fargate_probe_public.id
}

output "opensky_fargate_probe_security_group_id" {
  value = aws_security_group.opensky_fargate_probe.id
}

output "opensky_fargate_probe_log_group_name" {
  value = aws_cloudwatch_log_group.opensky_fargate_probe.name
}

output "github_actions_ecr_push_role_arn" {
  value = aws_iam_role.github_actions_ecr_push.arn
}