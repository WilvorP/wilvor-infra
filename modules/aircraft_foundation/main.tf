locals {
  aircraft_archive_bucket_name = "${var.name_prefix}-aircraft-archive-${var.account_id}-${var.aws_region}"
}

resource "aws_kinesis_stream" "aircraft_raw" {
  name             = "${var.name_prefix}-aircraft-raw"
  shard_count      = 1
  retention_period = 24

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }

  encryption_type = "KMS"
  kms_key_id      = "alias/aws/kinesis"

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-aircraft-raw"
    Component = "aircraft-ingestion"
    DataType  = "raw"
  })
}

resource "aws_kinesis_stream" "aircraft_clean" {
  name             = "${var.name_prefix}-aircraft-clean"
  shard_count      = 1
  retention_period = 24

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }

  encryption_type = "KMS"
  kms_key_id      = "alias/aws/kinesis"

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-aircraft-clean"
    Component = "aircraft-ingestion"
    DataType  = "clean"
  })
}

resource "aws_s3_bucket" "aircraft_archive" {
  bucket        = local.aircraft_archive_bucket_name
  force_destroy = true

  tags = merge(var.tags, {
    Name      = local.aircraft_archive_bucket_name
    Component = "aircraft-ingestion"
  })
}

resource "aws_s3_bucket_public_access_block" "aircraft_archive" {
  bucket = aws_s3_bucket.aircraft_archive.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "aircraft_archive" {
  bucket = aws_s3_bucket.aircraft_archive.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "aircraft_archive" {
  bucket = aws_s3_bucket.aircraft_archive.id

  rule {
    id     = "expire-raw-aircraft-data"
    status = "Enabled"

    filter {
      prefix = "raw/"
    }

    expiration {
      days = 3
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  rule {
    id     = "expire-clean-aircraft-data"
    status = "Enabled"

    filter {
      prefix = "clean/"
    }

    expiration {
      days = 3
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  rule {
    id     = "expire-bad-aircraft-records"
    status = "Enabled"

    filter {
      prefix = "bad-records/"
    }

    expiration {
      days = 7
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_dynamodb_table" "aircraft_current_state" {
  name         = "${var.name_prefix}-aircraft-current-state"
  billing_mode = "PROVISIONED"

  read_capacity  = 1
  write_capacity = 1

  hash_key = "icao24"

  attribute {
    name = "icao24"
    type = "S"
  }

  ttl {
    attribute_name = "ttl_epoch"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = false
  }

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-aircraft-current-state"
    Component = "aircraft-ingestion"
  })
}