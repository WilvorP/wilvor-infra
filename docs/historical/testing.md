# Historical testing and validation limits

**Canonical page:** this page
**Verified against:** `8abd2d5` on `docs/ws4-historical`
**Maturity:** CODE, UNIT, CONTRACT, INFRA, LIVE
**Production authorization:** no grant found. Harness files authorize only the invocation each harness defines.

## Purpose

This page lists historical test layers that were found in the repository and the operational validation limits those suites do not cross. It does not publish pytest or Vitest pass counts. Current suite results are UNKNOWN.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | packages and Lambdas cited by the tests below |
| Terraform | INFRA HCL contracts only; not an apply | `tests/infrastructure/terraform/test_historical_*.py` |
| Unit tests | UNIT | `tests/unit/historical_facts/`, `tests/unit/historical_query/` |
| Contract tests | CONTRACT for AI mapping | `tests/contracts/test_historical_analytics_mapping.py` (`test_verified_zero_maps_to_not_found_with_first_class_proof`; WS5-owned, not edited here) |
| Infrastructure tests | INFRA | `tests/infrastructure/terraform/test_historical_facts_contract.py`, `test_historical_data_plane_contract.py`, `test_historical_analytics_contract.py`, `test_historical_analytics_query_iam_contract.py`, `test_historical_analytics_validation.py` |
| Integration tests | none found | search: `tests/integration/` for `historical` at `8abd2d5` |
| Live harness | LIVE | `scripts/validate_historical_analytics.ps1`, `scripts/validate_historical_query_runtime.py` |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | dry-run paths make zero AWS calls |

## Unit tests

Directories at `8abd2d5`:

`tests/unit/historical_facts/` — contracts, event mapping, time, geometry, transform, coverage evaluator, coverage_control, DLQ consumer, query contracts/windows, Glue schema parity, producer visibility, import isolation, immutable metadata writes.

`tests/unit/historical_query/` — registry, SQL renderer, Athena executor, coverage gate, operations, result parsers, partitions, temporal SQL, import isolation.

These tests use injected fakes. `tests/conftest.py` supplies fake credentials. They do not prove a deployed account.

Representative assertions that this workstream re-read:

| Claim | Test |
| --- | --- |
| Dev collection flag is `true` | `tests/infrastructure/terraform/test_historical_facts_contract.py` (`test_dev_historical_facts_collection_is_enabled`) |
| Query catalog is four operations | `tests/unit/historical_facts/test_query_contracts.py` (`test_v1_catalog_is_exactly_four_named_operations`) |
| `VERIFIED_ZERO` requires `EVALUABLE` and exact zero | `tests/unit/historical_facts/test_query_contracts.py` (`test_verified_zero_requires_evaluable_and_exact_semantic_count_zero`) |
| Pre-query gap executes zero Athena calls | `tests/unit/historical_query/test_operations.py` (`test_pre_gap_blocks_without_athena`) |
| Executor rejects caller SQL | `tests/unit/historical_query/test_athena_executor.py` (`test_public_executor_accepts_only_fixed_query_id_and_typed_request`) |
| Query policy is unattached | `tests/infrastructure/terraform/test_historical_analytics_query_iam_contract.py` (`test_query_policy_is_one_unattached_managed_policy`) |
| `VERIFIED_ZERO` maps to `ToolResultStatus.NOT_FOUND` | `tests/contracts/test_historical_analytics_mapping.py` (`test_verified_zero_maps_to_not_found_with_first_class_proof`) |

## Infrastructure tests

Terraform contract tests read HCL. They do not apply. They do not inspect AWS.

`tests/infrastructure/aws/` has no historical-specific modules (search at `8abd2d5` for `historical` under that directory: no matches). General stack checks in that tree, if present, are not a historical query runtime suite.

## Integration tests

`tests/integration/` at this commit contains aircraft, SIGMET, METAR, TAF, and combined paths. No historical facts, coverage, or Athena integration tests were found.

## Live harnesses

| Harness | What it is | Limit |
| --- | --- | --- |
| `scripts/validate_historical_analytics.ps1` | Fixed SQL templates from `modules/historical_analytics/validation` | no `-Sql` / `-Query`; dry-run makes no AWS calls; Athena empty is `SQL_EMPTY`, not `VERIFIED_ZERO` |
| `scripts/validate_historical_query_runtime.py` | Closed operations, coverage inspect, snapshot, `iam-proof` | no raw SQL flags; dry-run makes zero AWS calls; live uses operator SSO and does not prove the unattached query policy |
| `scripts/validate_historical_specialist_anthropic_live.py` | Historical specialist Anthropic harness | no AWS, no Athena, no Bedrock per `scripts/README.md`; specialist page: [`docs/ai/historical-analytics-specialist.md`](../ai/historical-analytics-specialist.md) |

A harness file is the LIVE label. A passing run is a separate statement and needs a stored artifact path. This documentation task did not run those harnesses and did not read live artifacts as a current result.

Frontend catalog test `dashboard/src/config/cloudwatchDashboards.test.ts` is mentioned in `modules/historical_analytics/README.md` as a 2A.2d carry-forward. Whether that Vitest currently passes is UNKNOWN. Dashboard files are owned by WS3.

## Operational validation limitations

- UNIT does not imply INFRA, INT, LIVE, or DEPLOYED.
- INFRA HCL contracts do not prove an applied stack.
- Operator SSO is not the future Agent API principal.
- `iam-proof` simulation does not prove SCPs, permissions boundaries, or resource policies.
- Canonical Key/Size/ETag snapshots in the analytics validator prove objects were unchanged during that run. They do not prove current account state after the fact.
- Coverage evaluator remains completeness authority even when Athena `COUNT(*)` matches an S3 line count.
- There is no integration test that a deployed `HistoricalAnalyticsOperations` path exists.
- Query-policy attachment to a Lambda role is absent from Terraform; tests assert that absence rather than an attached runtime.

## Known limitations

- Pass counts were not collected for this page.
- Whether an operator live harness would pass if run today is UNKNOWN.
- Stored live JSON artifacts, if present under `test-results/`, are historical evidence of a past run and are not rewritten as a new pass (source: `functions/shared/wilvor_ai/README.md`).
- This page does not replace [`docs/testing/overview.md`](../testing/overview.md) or [`tests/README.md`](../../tests/README.md).

## Historical record

No superseded statement is stored on this page.

## Sources

- Tests: `tests/unit/historical_facts/`
- Tests: `tests/unit/historical_query/`
- Tests: `tests/infrastructure/terraform/test_historical_facts_contract.py`
- Tests: `tests/infrastructure/terraform/test_historical_analytics_query_iam_contract.py`
- Source: `scripts/validate_historical_analytics.ps1`
- Source: `scripts/validate_historical_query_runtime.py`
- Source: `modules/historical_analytics/validation/README.md`
