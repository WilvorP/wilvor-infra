output "risk_results_table_name" {
  value = aws_dynamodb_table.risk_results.name
}

output "risk_results_table_arn" {
  value = aws_dynamodb_table.risk_results.arn
}

output "risk_results_encounter_index_name" {
  value = (
    "encounter_id-generated_at_epoch-index"
  )
}

output "risk_results_aircraft_index_name" {
  value = (
    "aircraft_id-generated_at_epoch-index"
  )
}