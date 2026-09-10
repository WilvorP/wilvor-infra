# Persistent historical facts data plane

This module is the **only** owner of the historical S3 bucket and its
bucket-level configuration:

- `aws_s3_bucket`
- public access block
- BucketOwnerEnforced ownership
- AES256 encryption
- versioning
- lifecycle for `dataset=`, `errors/`, and `metadata/`
- `historical_fact_metadata_retention_days >= historical_fact_retention_days`
- noncurrent-version expiration
- abort incomplete multipart uploads
- `force_destroy = false`

It is not gated by `enable_historical_facts`. Recreatable transport in
`modules/historical_facts` looks up this bucket by the shared name
`${name_prefix}-historical-facts-${account_id}-${aws_region}` and must
not create bucket, lifecycle, encryption, versioning, or public-access
resources.

There is no `historical-data-down.ps1`. Ordinary `dev-down` / `dev-reset`
must not target this root. A versioned non-empty bucket cannot be
destroyed by a normal down script.

Break-glass store reset (documented only): list and delete **all object
versions and delete markers**, verify the bucket is empty of versions,
then `terraform destroy` this root. That terminates the current
`collection_epoch_id`. `aws s3 rm --recursive` is not sufficient.
