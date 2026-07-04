resource "aws_kinesis_stream" "sigmet_raw" {
  name             = "${var.name_prefix}-sigmet-raw"
  shard_count      = 1
  retention_period = 24

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }

  encryption_type = "KMS"
  kms_key_id      = "alias/aws/kinesis"

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-sigmet-raw"
    Component = "weather-ingestion"
    DataType  = "raw"
  })
}

resource "aws_kinesis_stream" "sigmet_clean" {
  name             = "${var.name_prefix}-sigmet-clean"
  shard_count      = 1
  retention_period = 24

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }

  encryption_type = "KMS"
  kms_key_id      = "alias/aws/kinesis"

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-sigmet-clean"
    Component = "weather-ingestion"
    DataType  = "clean"
  })
}

resource "aws_dynamodb_table" "active_hazards" {
  name         = "${var.name_prefix}-active-hazards"
  billing_mode = "PROVISIONED"

  read_capacity  = 1
  write_capacity = 1

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

  read_capacity  = 1
  write_capacity = 1

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

resource "aws_kinesis_stream" "metar_raw" {
  name             = "${var.name_prefix}-metar-raw"
  shard_count      = 1
  retention_period = 24

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }

  encryption_type = "KMS"
  kms_key_id      = "alias/aws/kinesis"

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-metar-raw"
    Component = "weather-ingestion"
    DataType  = "raw"
  })
}

resource "aws_kinesis_stream" "metar_clean" {
  name             = "${var.name_prefix}-metar-clean"
  shard_count      = 1
  retention_period = 24

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }

  encryption_type = "KMS"
  kms_key_id      = "alias/aws/kinesis"

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-metar-clean"
    Component = "weather-ingestion"
    DataType  = "clean"
  })
}

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
    Component = "weather-ingestion"
  })
}
