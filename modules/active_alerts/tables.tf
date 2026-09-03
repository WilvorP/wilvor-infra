resource "aws_dynamodb_table" "active_alerts" {
  name         = "${var.name_prefix}-active-alerts"
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "fingerprint"

  attribute {
    name = "fingerprint"
    type = "S"
  }

  attribute {
    name = "alert_id"
    type = "S"
  }

  attribute {
    name = "aircraft_id"
    type = "S"
  }

  attribute {
    name = "alert_state"
    type = "S"
  }

  attribute {
    name = "updated_at_epoch"
    type = "N"
  }

  global_secondary_index {
    name            = "alert_id-index"
    hash_key        = "alert_id"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "aircraft_id-updated_at_epoch-index"
    hash_key        = "aircraft_id"
    range_key       = "updated_at_epoch"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "alert_state-updated_at_epoch-index"
    hash_key        = "alert_state"
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
      Name      = "${var.name_prefix}-active-alerts"
      Component = "alerts"
      TableRole = "deduplicated-alert-state"
    }
  )
}
