# Historical query IAM

**Canonical page:** this page
**Verified against:** `8abd2d5` on `docs/ws4-historical`
**Maturity:** CODE, TF, INFRA, LIVE
**Production authorization:** no grant found. Repository configuration defines an unattached customer-managed policy. It does not attach that policy to a principal.

## Purpose

This page records IAM as written in Terraform and asserted by infrastructure tests. It does not retrieve AWS policy versions or simulate live authorization.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE for executor actions the policy names | `functions/shared/wilvor_historical_query/executor.py` |
| Terraform | TF | `modules/historical_analytics/iam.tf`; transport roles in `modules/historical_facts/iam.tf` |
| Unit tests | none found for IAM documents | search: `tests/unit/` for `aws_iam_policy.query` |
| Contract tests | none found | search: `tests/contracts/` for query IAM |
| Infrastructure tests | INFRA | `tests/infrastructure/terraform/test_historical_analytics_query_iam_contract.py` |
| Integration tests | none found | search: `tests/integration/` for `historical` |
| Live harness | LIVE | `scripts/validate_historical_query_runtime.py` action `iam-proof` (operator opt-in; not run for this page) |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no principal attachment found | `aws_iam_policy.query` only; no `aws_iam_role_policy_attachment` in this module |

## Behavior

### Unattached query policy (analytics)

When `enable_historical_analytics` is true, `modules/historical_analytics` creates one managed policy (source: `modules/historical_analytics/iam.tf`, `aws_iam_policy.query`):

- Name: `${name_prefix}-historical-analytics-query` (`wilvor-dev-historical-analytics-query` with committed prefix)
- Description in Terraform: "Unattached least-privilege permissions for deterministic historical analytics queries. Future Agent API attaches this to its own Lambda-trust role. This policy is not an execution principal."

Search of `modules/historical_analytics/**/*.tf` at `8abd2d5`:

| Symbol | Result |
| --- | --- |
| `aws_iam_policy.query` | present, `count = local.enabled ? 1 : 0` |
| `aws_iam_role` | absent |
| `aws_iam_role_policy` | absent |
| `aws_iam_role_policy_attachment` | absent |
| `aws_iam_policy_attachment` | absent |
| `sts:AssumeRole` / `assume_role` in `iam.tf` | absent |

`envs/dev/main.tf` does not attach `query_policy_arn` to a role. The ARN is an output only (`envs/dev/outputs.tf`, `historical_analytics_query_policy_arn`). `modules/operational_api` does not reference the query policy (tests: `tests/infrastructure/terraform/test_historical_analytics_query_iam_contract.py`, `test_query_policy_is_one_unattached_managed_policy`, `test_policy_is_disposable_and_not_on_operational_api_or_data_plane`).

Permissions described by the policy document (source: `modules/historical_analytics/iam.tf`, `data.aws_iam_policy_document.query`):

| SID | Actions | Scope |
| --- | --- | --- |
| `AthenaFixedQueries` | `StartQueryExecution`, `GetQueryExecution`, `GetQueryResults`, `StopQueryExecution` | this module's workgroup ARN |
| `GlueCatalogRead` | `GetDatabase`, `GetTable` | catalog, historical database, tables `encounter`, `risk`, `hazard_version` |
| `CanonicalBucketList` | `ListBucket` | facts bucket, prefix-conditioned |
| `CanonicalBucketLocation` | `GetBucketLocation` | facts bucket |
| `CanonicalObjectsRead` | `GetObject` | V1 `dataset=` prefixes plus seven metadata prefixes |
| `AthenaResultsBucketLocation` | `GetBucketLocation` | results bucket |
| `AthenaResultsBucketList` | `ListBucket` | `athena-results/` prefix |
| `AthenaResultsObjectsAccess` | `GetObject`, `PutObject`, `AbortMultipartUpload`, `ListMultipartUploadParts` | `athena-results/` objects |

`hazard_geometry` is cataloged but not in this V1 query policy. Canonical writes, result-object deletes, named queries, crawlers, KMS, CloudWatch, logs, IAM, STS, and Lambda invoke are absent from this document.

`s3:GetBucketLocation` is granted because Athena query execution uses the caller's credentials to resolve bucket locations. It is not used by the Python executor client.

Static Terraform tests prove intended policy structure. They do not prove effective AWS authorization.

### Transport roles (facts module)

`modules/historical_facts` creates five IAM roles with inline `aws_iam_role_policy` documents when collection is enabled. Search of that module at `8abd2d5` found no `aws_iam_role_policy_attachment` and no `aws_iam_policy_attachment`.

| Role resource | Name formula | Purpose in module README |
| --- | --- | --- |
| `aws_iam_role.transform` | `${name_prefix}-historical-facts-transform-role` | CloudWatch log writes only |
| `aws_iam_role.eventbridge` | `${name_prefix}-historical-facts-eventbridge-role` | `firehose:PutRecord` / `PutRecordBatch` on the facts stream |
| `aws_iam_role.firehose` | `${name_prefix}-historical-facts-firehose-role` | S3 destination, Lambda invoke for transform, Firehose logs |
| `aws_iam_role.coverage_control` | `${name_prefix}-historical-facts-coverage-role` | coverage control path |
| `aws_iam_role.dlq_gap_consumer` | `${name_prefix}-historical-facts-dlq-consumer-role` | DLQ consume plus `metadata/incidents/` PutObject |

SIGMET geometry PutRecord is outside this module: `modules/sigmet` statement `PutHistoricalGeometryFacts` grants `firehose:PutRecord` on the geometry stream ARN when configured (tests: `tests/infrastructure/terraform/test_historical_facts_contract.py`, `test_sigmet_firehose_permission_is_geometry_put_record_only`).

These role resources are TF. Whether they exist in an account is UNKNOWN.

### Operator live IAM proof

`scripts/validate_historical_query_runtime.py` action `iam-proof` calls `simulate_custom_policy` on the managed policy document and records `"attached_to_operator": False` (source: `scripts/validate_historical_query_runtime.py`). Simulation is identity-policy logic only. It does not prove SCPs, permissions boundaries, resource policies, or a future Lambda principal. Live `run-operation` uses operator SSO and does not prove this managed policy.

## Known limitations

- No execution role or trust policy exists in `modules/historical_analytics`.
- Operational API, dashboard API, coverage_control, ingestion, and human SSO do not receive this query policy in repository Terraform.
- A future Agent API attachment is described in comments and is not present as a resource.
- This documentation task did not call AWS IAM APIs.

## Historical record

No superseded statement is stored on this page. Phase 2B.5 "unattached" wording in `modules/historical_analytics/README.md` matches current Terraform at `8abd2d5`.

## Sources

- Source: `modules/historical_analytics/iam.tf` (`aws_iam_policy.query`, `data.aws_iam_policy_document.query`)
- Source: `modules/historical_facts/iam.tf`
- Source: `envs/dev/outputs.tf` (`historical_analytics_query_policy_arn`)
- Source: `scripts/validate_historical_query_runtime.py` (`iam-proof`, `attached_to_operator`)
- Tests: `tests/infrastructure/terraform/test_historical_analytics_query_iam_contract.py` (`test_query_policy_is_one_unattached_managed_policy`)
