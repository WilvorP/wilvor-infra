output "ledger_table_name" {
  value = aws_dynamodb_table.ledger.name
}

output "ledger_table_arn" {
  value = aws_dynamodb_table.ledger.arn
}

output "function_name" {
  value = aws_lambda_function.host.function_name
}

output "function_arn" {
  value = aws_lambda_function.host.arn
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.host.name
}

output "role_name" {
  value = aws_iam_role.host.name
}

output "role_arn" {
  value = aws_iam_role.host.arn
}

output "enable_invoke" {
  value = var.enable_invoke
}

output "reserved_concurrent_executions" {
  value = local.reserved_concurrent_executions
}

output "ai_query_route_enabled" {
  description = "This module does not create POST /ai/query."
  value       = false
}
