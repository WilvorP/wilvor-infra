resource "aws_dynamodb_table" "aircraft_hazard_encounter" {
  name         = "${var.name_prefix}-aircraft-hazard-encounter"
  billing_mode = "PROVISIONED"

  read_capacity  = var.dynamodb_read_capacity
  write_capacity = var.dynamodb_write_capacity

  hash_key = "encounter_id"

  attribute {
    name = "encounter_id"
    type = "S"
  }

  attribute {
    name = "aircraft_id"
    type = "S"
  }

  attribute {
    name = "hazard_id"
    type = "S"
  }

  attribute {
    name = "projection_id"
    type = "S"
  }

  attribute {
    name = "hazard_version_key"
    type = "S"
  }

  attribute {
    name = "detected_at_epoch"
    type = "N"
  }

  global_secondary_index {
    name            = "aircraft_id-detected_at_epoch-index"
    hash_key        = "aircraft_id"
    range_key       = "detected_at_epoch"
    projection_type = "ALL"

    read_capacity  = var.dynamodb_read_capacity
    write_capacity = var.dynamodb_write_capacity
  }

  global_secondary_index {
    name            = "hazard_id-detected_at_epoch-index"
    hash_key        = "hazard_id"
    range_key       = "detected_at_epoch"
    projection_type = "ALL"

    read_capacity  = var.dynamodb_read_capacity
    write_capacity = var.dynamodb_write_capacity
  }

  global_secondary_index {
    name            = "projection_id-hazard_version_key-index"
    hash_key        = "projection_id"
    range_key       = "hazard_version_key"
    projection_type = "ALL"

    read_capacity  = var.dynamodb_read_capacity
    write_capacity = var.dynamodb_write_capacity
  }

  ttl {
    attribute_name = "expires_at_epoch"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = var.enable_point_in_time_recovery
  }

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-aircraft-hazard-encounter"
    Component = "encounter"
    TableRole = "derived-current-versioned-decision"
  })
}