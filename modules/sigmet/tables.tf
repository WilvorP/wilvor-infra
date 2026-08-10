resource "aws_dynamodb_table" "active_hazards" {
  name         = "${var.name_prefix}-active-hazards"
  billing_mode = "PROVISIONED"

  read_capacity  = 5
  write_capacity = 5

  hash_key = "hazard_id"

  attribute {
    name = "hazard_id"
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
    Name      = "${var.name_prefix}-active-hazards"
    Component = "weather-ingestion"
  })
}

resource "aws_dynamodb_table" "hazard_cells" {
  name         = "${var.name_prefix}-hazard-cells"
  billing_mode = "PROVISIONED"

  read_capacity  = 5
  write_capacity = 25

  hash_key  = "cell_id"
  range_key = "hazard_id"

  attribute {
    name = "cell_id"
    type = "S"
  }

  attribute {
    name = "hazard_id"
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
    Name      = "${var.name_prefix}-hazard-cells"
    Component = "weather-ingestion"
  })
}

resource "aws_dynamodb_table" "hazard_coordinates" {
  name         = "${var.name_prefix}-hazard-coordinates"
  billing_mode = "PROVISIONED"

  read_capacity  = 5
  write_capacity = 25

  hash_key  = "hazard_version_key"
  range_key = "coordinate_key"

  attribute {
    name = "hazard_version_key"
    type = "S"
  }

  attribute {
    name = "coordinate_key"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at_epoch"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = false
  }

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-hazard-coordinates"
    Component = "weather-ingestion"
    TableRole = "versioned-spatial-child"
  })
}