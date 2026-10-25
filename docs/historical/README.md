# Historical facts and analytics

This page is an index. It states no behavior. Canonical pages own the claims.

**Verified against:** `8abd2d5` on `docs/ws4-historical`

## How to use this tree

1. Read [`DOCUMENTATION_STANDARDS.md`](../DOCUMENTATION_STANDARDS.md) for maturity labels.
2. Use the topic pages below. Link to a canonical page; do not copy it.
3. Colocated package and module READMEs remain the deep specifications.

During documentation work, leave Python, TypeScript, Terraform, tests, operational scripts, and infrastructure unchanged.

Whether `envs/dev` or `envs/dev-historical-data` is applied in an AWS account is UNKNOWN. Terraform assignments on this tree are TF, not DEPLOYED.

## Pages

| Page | Topic |
| --- | --- |
| [Terraform roots](terraform-roots.md) | `envs/dev` versus `envs/dev-historical-data`; `enable_historical_facts` |
| [Fact identity](fact-identity.md) | Canonical ids, event time, deduplication, schema versions |
| [Transport](transport.md) | Fact generation, EventBridge, Firehose, S3 delivery |
| [Analytics catalog](analytics.md) | Glue, Athena, disposable results |
| [Coverage](coverage.md) | Evaluability, missing data, `VERIFIED_ZERO` |
| [Queries](queries.md) | Deterministic V1 operations and data contracts |
| [AI boundary](ai-boundary.md) | Query runtime versus Historical Analytics Specialist |
| [IAM](iam.md) | Query policy attachment status from repository configuration |
| [Testing](testing.md) | Unit, infrastructure, live-harness, and validation limits |

## Colocated specifications

| Path | Topic |
| --- | --- |
| [`modules/historical_facts_data/README.md`](../../modules/historical_facts_data/README.md) | Persistent facts bucket |
| [`modules/historical_facts/README.md`](../../modules/historical_facts/README.md) | Recreatable transport |
| [`modules/historical_analytics/README.md`](../../modules/historical_analytics/README.md) | Glue, Athena, query policy |
| [`modules/historical_analytics/validation/README.md`](../../modules/historical_analytics/validation/README.md) | Catalog validation SQL |
| [`functions/shared/wilvor_historical/README.md`](../../functions/shared/wilvor_historical/README.md) | Fact and query contracts |
| [`functions/shared/wilvor_historical_query/README.md`](../../functions/shared/wilvor_historical_query/README.md) | Query runtime |

## Other workstreams

These paths are owned by other documentation workstreams. This index names them; it does not link to unpublished pages.

| Area | Path | Owner |
| --- | --- | --- |
| Platform architecture and lifecycle | `docs/overview/**`, `docs/operations/**` | WS1 |
| AI Operations Copilot, including the Historical Analytics Specialist | `docs/ai/**` | WS5 |
| AI package README (existing) | [`functions/shared/wilvor_ai/README.md`](../../functions/shared/wilvor_ai/README.md) | WS5 |
