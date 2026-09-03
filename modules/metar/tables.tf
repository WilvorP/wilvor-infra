resource "aws_dynamodb_table" "metar_latest" {
  name         = "${var.name_prefix}-metar-latest"
  billing_mode = "PAY_PER_REQUEST"

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
    name = "observed_time_epoch"
    type = "N"
  }

  global_secondary_index {
    name            = "airport-id-observed-time-index"
    hash_key        = "airport_id"
    range_key       = "observed_time_epoch"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "expires_at_epoch"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = false
  }

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-metar-latest"
    Component = "metar-ingestion"
  })
}