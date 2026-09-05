resource "aws_dynamodb_table" "insights" {
  name         = "${var.name_prefix}-ai-insights"
  billing_mode = "PAY_PER_REQUEST"

  hash_key  = "subject_key"
  range_key = "sort_key"

  attribute {
    name = "subject_key"
    type = "S"
  }

  attribute {
    name = "sort_key"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at_epoch"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = var.enable_point_in_time_recovery
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(
    var.tags,
    {
      Name      = "${var.name_prefix}-ai-insights"
      Component = "ai-copilot"
      TableRole = "ai-cache-audit"
    }
  )
}
