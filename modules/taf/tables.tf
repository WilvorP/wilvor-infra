resource "aws_dynamodb_table" "taf_latest" {
  name           = "${var.name_prefix}-taf-latest"
  billing_mode   = "PROVISIONED"
  read_capacity  = 5
  write_capacity = 5
  hash_key       = "station_id"

  attribute {
    name = "station_id"
    type = "S"
  }

  attribute {
    name = "airport_id"
    type = "S"
  }

  attribute {
    name = "issued_at_epoch"
    type = "N"
  }

  global_secondary_index {
    name            = "airport_id-issued_at_epoch-index"
    hash_key        = "airport_id"
    range_key       = "issued_at_epoch"
    projection_type = "ALL"

    read_capacity  = 5
    write_capacity = 5
  }

  ttl {
    attribute_name = "expires_at_epoch"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = false
  }

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-taf-latest"
    Component = "weather-processing"
    DataType  = "current-state"
    TableRole = "source-owned-current-state"
  })
}

resource "aws_dynamodb_table" "taf_forecast_periods" {
  name           = "${var.name_prefix}-taf-forecast-periods"
  billing_mode   = "PROVISIONED"
  read_capacity  = 10
  write_capacity = 25

  hash_key  = "taf_version_key"
  range_key = "period_key"

  attribute {
    name = "taf_version_key"
    type = "S"
  }

  attribute {
    name = "period_key"
    type = "S"
  }

  attribute {
    name = "station_id"
    type = "S"
  }

  attribute {
    name = "period_from_epoch"
    type = "N"
  }

  global_secondary_index {
    name            = "station_id-period_from_epoch-index"
    hash_key        = "station_id"
    range_key       = "period_from_epoch"
    projection_type = "ALL"

    read_capacity  = 10
    write_capacity = 25
  }

  ttl {
    attribute_name = "expires_at_epoch"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = false
  }

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-taf-forecast-periods"
    Component = "weather-processing"
    DataType  = "forecast-periods"
    TableRole = "versioned-weather-child"
  })
}