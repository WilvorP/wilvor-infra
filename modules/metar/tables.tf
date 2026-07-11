resource "aws_dynamodb_table" "metar_latest" {
  name         = "${var.name_prefix}-metar-latest"
  billing_mode = "PROVISIONED"

  read_capacity  = 1
  write_capacity = 1

  hash_key = "station_id"

  attribute {
    name = "station_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
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