resource "aws_s3_bucket" "historical_facts" {
  bucket        = local.bucket_name
  force_destroy = false

  tags = merge(local.common_tags, {
    Name = local.bucket_name
  })
}

resource "aws_s3_bucket_public_access_block" "historical_facts" {
  bucket = aws_s3_bucket.historical_facts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "historical_facts" {
  bucket = aws_s3_bucket.historical_facts.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "historical_facts" {
  bucket = aws_s3_bucket.historical_facts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "historical_facts" {
  bucket = aws_s3_bucket.historical_facts.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "historical_facts" {
  bucket = aws_s3_bucket.historical_facts.id

  depends_on = [
    aws_s3_bucket_versioning.historical_facts,
  ]

  rule {
    id     = "expire-current-historical-facts"
    status = "Enabled"

    filter {
      prefix = "dataset="
    }

    expiration {
      days = var.historical_fact_retention_days
    }
  }

  rule {
    id     = "expire-current-historical-errors"
    status = "Enabled"

    filter {
      prefix = "errors/"
    }

    expiration {
      days = var.historical_fact_error_retention_days
    }
  }

  rule {
    id     = "expire-current-historical-metadata"
    status = "Enabled"

    filter {
      prefix = "metadata/"
    }

    expiration {
      days = var.historical_fact_metadata_retention_days
    }
  }

  rule {
    id     = "expire-noncurrent-historical-versions"
    status = "Enabled"

    filter {
      prefix = ""
    }

    noncurrent_version_expiration {
      noncurrent_days = var.historical_fact_noncurrent_version_retention_days
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

  lifecycle {
    precondition {
      condition = (
        var.historical_fact_metadata_retention_days
        >= var.historical_fact_retention_days
      )
      error_message = "historical_fact_metadata_retention_days must be >= historical_fact_retention_days so metadata never expires before the facts it qualifies."
    }
  }
}

check "metadata_retention_covers_facts" {
  assert {
    condition = (
      var.historical_fact_metadata_retention_days
      >= var.historical_fact_retention_days
    )
    error_message = "historical_fact_metadata_retention_days must be >= historical_fact_retention_days so metadata never expires before the facts it qualifies."
  }
}
