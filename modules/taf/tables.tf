resource "aws_dynamodb_table" "taf_latest" {
  name           = "${var.name_prefix}-taf-latest"
  billing_mode   = "PROVISIONED"
  read_capacity  = 1
  write_capacity = 1
  hash_key       = "station_id"

  attribute {
    name = "station_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-taf-latest"
    Component = "weather-processing"
    DataType  = "current-state"
  })
}
