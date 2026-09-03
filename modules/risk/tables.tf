resource "aws_dynamodb_table" "risk_results" {
  name         = "${var.name_prefix}-risk-results"
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "risk_id"

  attribute {
    name = "risk_id"
    type = "S"
  }

  attribute {
    name = "encounter_id"
    type = "S"
  }

  attribute {
    name = "aircraft_id"
    type = "S"
  }

  attribute {
    name = "generated_at_epoch"
    type = "N"
  }

  global_secondary_index {
    name = "encounter_id-generated_at_epoch-index"

    hash_key  = "encounter_id"
    range_key = "generated_at_epoch"

    projection_type = "ALL"
  }

  global_secondary_index {
    name = "aircraft_id-generated_at_epoch-index"

    hash_key  = "aircraft_id"
    range_key = "generated_at_epoch"

    projection_type = "ALL"
  }

  ttl {
    attribute_name = "expires_at_epoch"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = var.enable_point_in_time_recovery
  }

  tags = merge(
    var.tags,
    {
      Name = "${var.name_prefix}-risk-results"

      Component = "risk"

      TableRole = "derived-versioned-decision"
    }
  )
}