resource "aws_dynamodb_table" "recommendations" {
  name         = "${var.name_prefix}-recommendations"
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "recommendation_id"

  attribute {
    name = "recommendation_id"
    type = "S"
  }

  attribute {
    name = "aircraft_id"
    type = "S"
  }

  attribute {
    name = "risk_id"
    type = "S"
  }

  attribute {
    name = "recommendation_status"
    type = "S"
  }

  attribute {
    name = "created_at_epoch"
    type = "N"
  }

  attribute {
    name = "updated_at_epoch"
    type = "N"
  }

  global_secondary_index {
    name            = "aircraft_id-created_at_epoch-index"
    hash_key        = "aircraft_id"
    range_key       = "created_at_epoch"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "risk_id-created_at_epoch-index"
    hash_key        = "risk_id"
    range_key       = "created_at_epoch"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "recommendation_status-updated_at_epoch-index"
    hash_key        = "recommendation_status"
    range_key       = "updated_at_epoch"
    projection_type = "ALL"
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
      Name      = "${var.name_prefix}-recommendations"
      Component = "recommendations"
      TableRole = "advisory-output"
    }
  )
}
