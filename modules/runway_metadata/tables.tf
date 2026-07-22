resource "aws_dynamodb_table" "runway_reference" {
  name           = "${var.name_prefix}-runway-reference"
  billing_mode   = "PROVISIONED"
  read_capacity  = var.dynamodb_read_capacity
  write_capacity = var.dynamodb_write_capacity

  hash_key  = "airport_id"
  range_key = "record_id"

  attribute {
    name = "airport_id"
    type = "S"
  }

  attribute {
    name = "record_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = var.enable_point_in_time_recovery
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-runway-reference"
    Component = "runway-reference-data"
    DataType  = "current-state"
  })
}