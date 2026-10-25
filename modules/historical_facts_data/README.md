# Persistent historical facts data plane

**Canonical page:** this page | map [`docs/historical/terraform-roots.md`](../../docs/historical/terraform-roots.md)
**Verified against:** `8abd2d5` on `docs/ws4-historical`
**Maturity:** TF, INFRA
**Production authorization:** no grant found. Instantiating this module in Terraform is not a recorded apply.

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

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | absent | this module is Terraform only |
| Terraform | TF | `modules/historical_facts_data/bucket.tf`; `envs/dev-historical-data/main.tf` |
| Unit tests | none found | search: `tests/unit/` for `historical_facts_data` |
| Contract tests | none found | search: `tests/contracts/` for this module |
| Infrastructure tests | INFRA | `tests/infrastructure/terraform/test_historical_data_plane_contract.py` |
| Integration tests | none found | search: `tests/integration/` for `historical` |
| Live harness | none found | `scripts/historical-data-up.ps1` is an apply script, not a LIVE validation harness |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | `force_destroy = false` is configuration, not an apply record |

## Known limitations

- Whether the persistent bucket exists in an AWS account is UNKNOWN.
- Break-glass reset is documented only. This file is not an operator runbook that performs it.
- There is no `scripts/historical-data-down.ps1`.

## Historical record

No superseded statement is stored on this page.

## Sources

- Source: `modules/historical_facts_data/bucket.tf` (`aws_s3_bucket.historical_facts`)
- Source: `modules/historical_facts_data/locals.tf` (`local.bucket_name`)
- Source: `envs/dev-historical-data/main.tf` (`module.historical_facts_data`)
- Tests: `tests/infrastructure/terraform/test_historical_data_plane_contract.py`
- Map: [`docs/historical/terraform-roots.md`](../../docs/historical/terraform-roots.md)
