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

  attribute {
    name = "status"
    type = "S"
  }

  attribute {
    name = "valid_to_epoch"
    type = "N"
  }

  attribute {
    name = "source_product_id"
    type = "S"
  }

  attribute {
    name = "created_at_utc"
    type = "S"
  }

  global_secondary_index {
    name            = "status-valid_to_epoch-index"
    hash_key        = "status"
    range_key       = "valid_to_epoch"
    projection_type = "ALL"

    read_capacity  = 5
    write_capacity = 5
  }

  global_secondary_index {
    name            = "source_product_id-created_at_utc-index"
    hash_key        = "source_product_id"
    range_key       = "created_at_utc"
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
    Name      = "${var.name_prefix}-active-hazards"
    Component = "weather-ingestion"
  })
}


resource "aws_dynamodb_table" "hazard_cells" {
  name         = "${var.name_prefix}-hazard-cells"
  billing_mode = "PROVISIONED"

  read_capacity  = 5
  write_capacity = 25

  hash_key  = "h3_cell"
  range_key = "hazard_version_key"

  attribute {
    name = "h3_cell"
    type = "S"
  }

  attribute {
    name = "hazard_version_key"
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
    Name      = "${var.name_prefix}-hazard-cells"
    Component = "weather-processing"
    DataType  = "exact-h3-coverage"
  })
}

resource "aws_dynamodb_table" "impact_cells" {
  name         = "${var.name_prefix}-impact-cells"
  billing_mode = "PROVISIONED"

  read_capacity  = 5
  write_capacity = 50

  hash_key  = "impact_cell"
  range_key = "hazard_version_key"

  attribute {
    name = "impact_cell"
    type = "S"
  }

  attribute {
    name = "hazard_version_key"
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
    Name      = "${var.name_prefix}-impact-cells"
    Component = "weather-processing"
    DataType  = "projection-trigger-area"
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

resource "aws_dynamodb_table" "hazard_station_candidates" {
  name         = "${var.name_prefix}-hazard-station-candidates"
  billing_mode = "PROVISIONED"

  read_capacity  = 25
  write_capacity = 75

  hash_key  = "hazard_version_key"
  range_key = "station_id"

  attribute {
    name = "hazard_version_key"
    type = "S"
  }

  attribute {
    name = "station_id"
    type = "S"
  }

  global_secondary_index {
    name            = "station-hazard-index"
    hash_key        = "station_id"
    range_key       = "hazard_version_key"
    projection_type = "ALL"

    read_capacity  = 25
    write_capacity = 75
  }

  ttl {
    attribute_name = "expires_at_epoch"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = false
  }

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-hazard-station-candidates"
    Component = "weather-ingestion"
    TableRole = "derived-spatial-relationship"
  })
}