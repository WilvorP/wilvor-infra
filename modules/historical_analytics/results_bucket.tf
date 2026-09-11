resource "aws_s3_bucket" "athena_results" {
  count = local.enabled ? 1 : 0

  bucket        = local.results_bucket_name
  force_destroy = true

  tags = merge(local.common_tags, {
    Name = local.results_bucket_name
  })
}

resource "aws_s3_bucket_public_access_block" "athena_results" {
  count = local.enabled ? 1 : 0

  bucket = aws_s3_bucket.athena_results[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "athena_results" {
  count = local.enabled ? 1 : 0

  bucket = aws_s3_bucket.athena_results[0].id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "athena_results" {
  count = local.enabled ? 1 : 0

  bucket = aws_s3_bucket.athena_results[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "athena_results" {
  count = local.enabled ? 1 : 0

  bucket = aws_s3_bucket.athena_results[0].id

  rule {
    id     = "expire-current-athena-query-results"
    status = "Enabled"

    filter {
      prefix = ""
    }

    expiration {
      days = var.result_retention_days
    }
  }

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {
      prefix = ""
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}
