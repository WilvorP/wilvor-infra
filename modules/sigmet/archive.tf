locals {
  sigmet_archive_bucket_name = "${var.name_prefix}-sigmet-archive-${var.account_id}-${var.aws_region}"
}

resource "aws_s3_bucket" "sigmet_archive" {
  bucket        = local.sigmet_archive_bucket_name
  force_destroy = var.archive_force_destroy

  tags = merge(var.tags, {
    Name      = local.sigmet_archive_bucket_name
    Component = "sigmet-ingestion"
    DataType  = "archive"
  })
}

resource "aws_s3_bucket_public_access_block" "sigmet_archive" {
  bucket = aws_s3_bucket.sigmet_archive.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "sigmet_archive" {
  bucket = aws_s3_bucket.sigmet_archive.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "sigmet_archive" {
  bucket = aws_s3_bucket.sigmet_archive.id

  rule {
    id     = "expire-raw-sigmet-data"
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
    id     = "expire-bad-sigmet-records"
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