locals {
  taf_archive_bucket_name = "${var.name_prefix}-taf-archive-${var.account_id}-${var.aws_region}"
}

resource "aws_s3_bucket" "taf_archive" {
  bucket        = local.taf_archive_bucket_name
  force_destroy = var.archive_force_destroy

  tags = merge(var.tags, {
    Name      = local.taf_archive_bucket_name
    Component = "taf-ingestion"
    DataType  = "archive"
  })
}

resource "aws_s3_bucket_public_access_block" "taf_archive" {
  bucket = aws_s3_bucket.taf_archive.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "taf_archive" {
  bucket = aws_s3_bucket.taf_archive.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "taf_archive" {
  bucket = aws_s3_bucket.taf_archive.id

  rule {
    id     = "expire-raw-taf-data"
    status = "Enabled"

    filter {
      prefix = "raw/"
    }

    expiration {
      days = var.raw_archive_retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  rule {
    id     = "expire-bad-taf-records"
    status = "Enabled"

    filter {
      prefix = "bad-records/"
    }

    expiration {
      days = var.bad_record_retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}
