output "aircraft_hazard_encounter_table_name" {
  value = aws_dynamodb_table.aircraft_hazard_encounter.name
}

output "aircraft_hazard_encounter_table_arn" {
  value = aws_dynamodb_table.aircraft_hazard_encounter.arn
}

output "encounter_processor_function_name" {
  value = aws_lambda_function.encounter_processor.function_name
}

output "encounter_processor_function_arn" {
  value = aws_lambda_function.encounter_processor.arn
}

output "encounter_dashboard_name" {
  value = aws_cloudwatch_dashboard.aircraft_hazard_encounter.dashboard_name
}