resource "aws_dynamodb_table" "station_reference" {
  name           = "${var.name_prefix}-station-reference"
  billing_mode   = "PROVISIONED"
  read_capacity  = var.dynamodb_read_capacity
  write_capacity = var.dynamodb_write_capacity

  hash_key = "station_id"

  attribute {
    name = "station_id"
    type = "S"
  }

  attribute {
    name = "airport_id"
    type = "S"
  }

  attribute {
    name = "h3_cell"
    type = "S"
  }

  global_secondary_index {
    name            = "airport-station-index"
    hash_key        = "airport_id"
    range_key       = "station_id"
    projection_type = "ALL"
    read_capacity   = var.dynamodb_read_capacity
    write_capacity  = var.dynamodb_write_capacity
  }

  global_secondary_index {
    name            = "h3-station-index"
    hash_key        = "h3_cell"
    range_key       = "station_id"
    projection_type = "ALL"
    read_capacity   = var.dynamodb_read_capacity
    write_capacity  = var.dynamodb_write_capacity
  }

  point_in_time_recovery {
    enabled = var.enable_point_in_time_recovery
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-station-reference"
    Component = "station-reference-data"
    DataType  = "reference"
  })
}
