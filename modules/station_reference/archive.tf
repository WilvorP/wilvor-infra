locals {
  station_reference_archive_bucket_name = (
    "${var.name_prefix}-station-reference-archive-${var.account_id}-${var.aws_region}"
  )
}

resource "aws_s3_bucket" "station_reference_archive" {
  bucket        = local.station_reference_archive_bucket_name
  force_destroy = var.archive_force_destroy

  tags = merge(var.tags, {
    Name      = local.station_reference_archive_bucket_name
    Component = "station-reference-data"
    DataType  = "archive"
  })
}

resource "aws_s3_bucket_public_access_block" "station_reference_archive" {
  bucket = aws_s3_bucket.station_reference_archive.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "station_reference_archive" {
  bucket = aws_s3_bucket.station_reference_archive.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "station_reference_archive" {
  bucket = aws_s3_bucket.station_reference_archive.id

  rule {
    id     = "expire-station-reference-bad-records"
    status = "Enabled"

    filter {
      prefix = "bad/"
    }

    expiration {
      days = var.bad_record_retention_days
    }
  }

  rule {
    id     = "abort-incomplete-station-reference-uploads"
    status = "Enabled"

    filter {
      prefix = ""
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}
