resource "aws_dynamodb_table" "airport_status" {
  name           = "${var.name_prefix}-airport-status"
  billing_mode   = "PROVISIONED"
  read_capacity  = var.dynamodb_read_capacity
  write_capacity = var.dynamodb_write_capacity

  hash_key = "airport_id"

  attribute {
    name = "airport_id"
    type = "S"
  }

  attribute {
    name = "station_id"
    type = "S"
  }

  attribute {
    name = "weather_risk_level"
    type = "S"
  }

  attribute {
    name = "weather_impact_status"
    type = "S"
  }

  attribute {
    name = "updated_at_epoch"
    type = "N"
  }

  global_secondary_index {
    name            = "station-updated-index"
    hash_key        = "station_id"
    range_key       = "updated_at_epoch"
    projection_type = "ALL"
    read_capacity   = var.dynamodb_read_capacity
    write_capacity  = var.dynamodb_write_capacity
  }

  global_secondary_index {
    name            = "weather-risk-updated-index"
    hash_key        = "weather_risk_level"
    range_key       = "updated_at_epoch"
    projection_type = "ALL"
    read_capacity   = var.dynamodb_read_capacity
    write_capacity  = var.dynamodb_write_capacity
  }

  global_secondary_index {
    name            = "weather-impact-updated-index"
    hash_key        = "weather_impact_status"
    range_key       = "updated_at_epoch"
    projection_type = "ALL"
    read_capacity   = var.dynamodb_read_capacity
    write_capacity  = var.dynamodb_write_capacity
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

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-airport-status"
    Component = "airport-status"
    DataType  = "derived-current-state"
  })
}