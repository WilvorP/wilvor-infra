output "airport_assessment_table_name" {
  value = aws_dynamodb_table.airport_assessment.name
}

output "airport_assessment_table_arn" {
  value = aws_dynamodb_table.airport_assessment.arn
}

output "processor_function_name" {
  value = aws_lambda_function.processor.function_name
}

output "processor_function_arn" {
  value = aws_lambda_function.processor.arn
}