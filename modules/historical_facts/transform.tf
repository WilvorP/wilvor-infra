resource "aws_cloudwatch_log_group" "transform" {
  count = local.enabled ? 1 : 0

  name              = "/aws/lambda/${local.transform_function_name}"
  retention_in_days = var.log_retention_days

  tags = merge(local.common_tags, {
    Name = "/aws/lambda/${local.transform_function_name}"
  })
}

resource "aws_lambda_function" "transform" {
  count = local.enabled ? 1 : 0

  function_name = local.transform_function_name
  role          = aws_iam_role.transform[0].arn

  filename         = var.transform_zip_path
  source_code_hash = filebase64sha256(var.transform_zip_path)

  runtime = "python3.12"
  handler = "app.lambda_handler"

  memory_size = 256
  timeout     = 60

  environment {
    variables = {
      ENVIRONMENT = replace(var.name_prefix, "wilvor-", "")
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.transform,
    aws_iam_role_policy.transform,
  ]

  tags = merge(local.common_tags, {
    Name = local.transform_function_name
  })
}

resource "aws_lambda_permission" "allow_firehose_transform" {
  for_each = local.enabled ? aws_kinesis_firehose_delivery_stream.historical : {}

  statement_id  = "AllowExecutionFromHistoricalFirehose${title(each.key)}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.transform[0].function_name
  principal     = "firehose.amazonaws.com"
  source_arn    = each.value.arn
}
