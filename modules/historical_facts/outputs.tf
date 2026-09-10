output "historical_bucket_name" {
  value = local.historical_bucket_id
}

output "historical_bucket_arn" {
  value = local.historical_bucket_arn
}

output "facts_firehose_stream_name" {
  value = local.enabled ? aws_kinesis_firehose_delivery_stream.historical["facts"].name : ""
}

output "facts_firehose_stream_arn" {
  value = local.enabled ? aws_kinesis_firehose_delivery_stream.historical["facts"].arn : ""
}

output "geometry_firehose_stream_name" {
  value = local.enabled ? aws_kinesis_firehose_delivery_stream.historical["geometry"].name : ""
}

output "geometry_firehose_stream_arn" {
  value = local.enabled ? aws_kinesis_firehose_delivery_stream.historical["geometry"].arn : ""
}

output "transform_lambda_name" {
  value = local.enabled ? aws_lambda_function.transform[0].function_name : ""
}

output "transform_lambda_arn" {
  value = local.enabled ? aws_lambda_function.transform[0].arn : ""
}

output "coverage_lambda_name" {
  value = local.enabled ? aws_lambda_function.coverage_control[0].function_name : ""
}

output "coverage_lambda_arn" {
  value = local.enabled ? aws_lambda_function.coverage_control[0].arn : ""
}

output "dlq_url" {
  value = local.enabled ? aws_sqs_queue.historical_facts_dlq[0].url : ""
}

output "dlq_arn" {
  value = local.enabled ? aws_sqs_queue.historical_facts_dlq[0].arn : ""
}

output "historical_rule_names" {
  value = local.enabled ? [
    for rule in aws_cloudwatch_event_rule.historical : rule.name
  ] : []
}

output "historical_control_rule_name" {
  value = local.enabled ? aws_cloudwatch_event_rule.historical["control"].name : ""
}

output "dashboard_name" {
  value = local.enabled ? aws_cloudwatch_dashboard.historical_facts[0].dashboard_name : ""
}

output "enable_historical_facts" {
  value = var.enable_historical_facts
}
