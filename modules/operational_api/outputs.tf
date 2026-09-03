output "api_endpoint" {
  value = (
    aws_apigatewayv2_api.http.api_endpoint
  )
}

output "api_id" {
  value = (
    aws_apigatewayv2_api.http.id
  )
}

output "lambda_function_name" {
  value = (
    aws_lambda_function.api.function_name
  )
}

output "lambda_function_arn" {
  value = (
    aws_lambda_function.api.arn
  )
}

output "lambda_log_group_name" {
  value = (
    aws_cloudwatch_log_group.lambda.name
  )
}