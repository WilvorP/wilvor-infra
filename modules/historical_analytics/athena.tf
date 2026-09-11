resource "aws_athena_workgroup" "historical_analytics" {
  count = local.enabled ? 1 : 0

  name        = local.workgroup_name
  state       = "ENABLED"
  description = "Wilvor deterministic historical analytics over canonical historical facts. This workgroup is a cost and control boundary. It is not a query API and does not determine collection completeness."

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    requester_pays_enabled             = false
    bytes_scanned_cutoff_per_query     = var.bytes_scanned_cutoff_per_query

    engine_version {
      selected_engine_version = "Athena engine version 3"
    }

    result_configuration {
      output_location       = "s3://${aws_s3_bucket.athena_results[0].id}/${local.athena_results_prefix}"
      expected_bucket_owner = var.account_id

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }

  tags = merge(local.common_tags, {
    Name = local.workgroup_name
  })
}
