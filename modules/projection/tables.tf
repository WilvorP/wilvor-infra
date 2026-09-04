resource "aws_dynamodb_table" "aircraft_projection_points" {
  name         = "${var.name_prefix}-aircraft-projection-points"
  billing_mode = "PAY_PER_REQUEST"
  hash_key  = "projection_id"
  range_key = "point_key"

  attribute {
    name = "projection_id"
    type = "S"
  }

  attribute {
    name = "point_key"
    type = "S"
  }

  attribute {
    name = "aircraft_id"
    type = "S"
  }

  attribute {
    name = "projected_time_epoch"
    type = "N"
  }

  global_secondary_index {
    name            = "aircraft_id-projected_time_epoch-index"
    hash_key        = "aircraft_id"
    range_key       = "projected_time_epoch"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "expires_at_epoch"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = var.enable_point_in_time_recovery
  }

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-aircraft-projection-points"
    Component = "projection"
    DataType  = "trajectory-points"
  })
}

resource "aws_dynamodb_table" "aircraft_projection" {
  name         = "${var.name_prefix}-aircraft-projection"
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "projection_id"

  attribute {
    name = "projection_id"
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

  attribute {
    name = "projection_status"
    type = "S"
  }

  attribute {
    name = "valid_until_epoch"
    type = "N"
  }

  global_secondary_index {
    name            = "aircraft_id-generated_at_epoch-index"
    hash_key        = "aircraft_id"
    range_key       = "generated_at_epoch"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "projection_status-valid_until_epoch-index"
    hash_key        = "projection_status"
    range_key       = "valid_until_epoch"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "expires_at_epoch"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = var.enable_point_in_time_recovery
  }

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-aircraft-projection"
    Component = "projection"
    DataType  = "projection-parent"
  })
}


resource "aws_dynamodb_table" "aircraft_projection_cells" {
  name         = "${var.name_prefix}-aircraft-projection-cells"
  billing_mode = "PAY_PER_REQUEST"

  hash_key  = "projection_id"
  range_key = "h3_cell"

  attribute {
    name = "projection_id"
    type = "S"
  }

  attribute {
    name = "h3_cell"
    type = "S"
  }

  global_secondary_index {
    name            = "h3_cell-projection_id-index"
    hash_key        = "h3_cell"
    range_key       = "projection_id"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "expires_at_epoch"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = var.enable_point_in_time_recovery
  }

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-aircraft-projection-cells"
    Component = "projection"
    DataType  = "projection-spatial-index"
  })
}