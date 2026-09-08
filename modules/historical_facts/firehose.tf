locals {
  firehose_streams = {
    facts    = local.facts_stream_name
    geometry = local.geometry_stream_name
  }
}

resource "aws_cloudwatch_log_group" "firehose" {
  for_each = local.enabled ? local.firehose_streams : {}

  name              = "/aws/kinesisfirehose/${each.value}"
  retention_in_days = var.log_retention_days

  tags = merge(local.common_tags, {
    Name = "/aws/kinesisfirehose/${each.value}"
  })
}

resource "aws_cloudwatch_log_stream" "firehose_s3" {
  for_each = local.enabled ? local.firehose_streams : {}

  name           = "S3Delivery"
  log_group_name = aws_cloudwatch_log_group.firehose[each.key].name
}

resource "aws_kinesis_firehose_delivery_stream" "historical" {
  for_each = local.enabled ? local.firehose_streams : {}

  name        = each.value
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn   = aws_iam_role.firehose[0].arn
    bucket_arn = aws_s3_bucket.historical_facts[0].arn

    buffering_size     = var.firehose_buffering_size_mib
    buffering_interval = var.firehose_buffering_interval_seconds
    compression_format = "GZIP"

    prefix              = local.success_prefix
    error_output_prefix = local.error_prefix

    processing_configuration {
      enabled = true

      processors {
        type = "Lambda"

        parameters {
          parameter_name  = "LambdaArn"
          parameter_value = "${aws_lambda_function.transform[0].arn}:$LATEST"
        }

        parameters {
          parameter_name  = "NumberOfRetries"
          parameter_value = "3"
        }

        parameters {
          parameter_name  = "BufferSizeInMBs"
          parameter_value = "3"
        }

        parameters {
          parameter_name  = "BufferIntervalInSeconds"
          parameter_value = "30"
        }
      }
    }

    dynamic_partitioning_configuration {
      enabled = true
    }

    cloudwatch_logging_options {
      enabled         = true
      log_group_name  = aws_cloudwatch_log_group.firehose[each.key].name
      log_stream_name = aws_cloudwatch_log_stream.firehose_s3[each.key].name
    }
  }

  depends_on = [
    aws_iam_role_policy.firehose,
  ]

  tags = merge(local.common_tags, {
    Name = each.value
  })
}
