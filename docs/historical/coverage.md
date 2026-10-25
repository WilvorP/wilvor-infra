# Historical coverage and verified zero

**Canonical page:** this page | evaluator [`functions/shared/wilvor_historical/coverage.py`](../../functions/shared/wilvor_historical/coverage.py) | query contracts [`functions/shared/wilvor_historical/query_contracts.py`](../../functions/shared/wilvor_historical/query_contracts.py)
**Verified against:** `8abd2d5` on `docs/ws4-historical`
**Maturity:** CODE, TF, UNIT, INFRA, LIVE
**Production authorization:** no grant found. Coverage evaluability is not a production execution grant.

## Purpose

This page records how collection completeness is decided, how missing data is represented, and when a zero result may be called `VERIFIED_ZERO`. Query SQL and Athena execution stay on [Queries](queries.md).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `functions/shared/wilvor_historical/coverage.py`, `coverage_contracts.py`, `query_contracts.py`; `functions/shared/wilvor_historical_query/coverage_gate.py`, `coverage_store.py`; `functions/historical_facts/coverage_control/app.py` |
| Terraform | TF for coverage Lambda and metadata IAM | `modules/historical_facts/coverage.tf` |
| Unit tests | UNIT | `tests/unit/historical_facts/test_coverage.py`, `test_coverage_control.py`, `test_query_contracts.py`; `tests/unit/historical_query/test_coverage_gate.py`, `test_operations.py` |
| Contract tests | none found for the evaluator | search: `tests/contracts/` for `evaluate_collection_window` |
| Infrastructure tests | INFRA for `dev-down` DEACTIVATE | `tests/infrastructure/terraform/test_historical_facts_contract.py` |
| Integration tests | none found | search: `tests/integration/` for `historical` |
| Live harness | LIVE for operator inspection | `scripts/validate_historical_query_runtime.py` action `inspect-coverage` |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | evaluator is AWS-free; store requires injected S3 |

## Behavior

Terraform apply time is not verified historical coverage start (source: `functions/shared/wilvor_historical/README.md`). Example recorded there: infra enabled 02:00, delivery broken until 02:44. 02:00–02:44 must not later be reported as a verified zero-event window.

### Evaluability

`evaluate_collection_window` is the sole completeness authority (source: `functions/shared/wilvor_historical/coverage.py`, `evaluate_collection_window`). It does not import boto3 or read the wall clock.

`Evaluability` has four values (source: `functions/shared/wilvor_historical/coverage_contracts.py`, `Evaluability`):

| Value | Meaning in evaluator |
| --- | --- |
| `NOT_ACTIVE` | window is outside an active collection period, or mixes active and intentionally inactive |
| `NOT_YET_EVALUABLE` | window ends after `as_of_utc`, or late-arrival horizon is not closed |
| `EVALUABLE` | required coverage intervals exist, horizons are closed, and no unresolved overlapping gap or unbound incident remains |
| `GAP_OR_UNCERTAIN` | missing coverage interval, unresolved gap, unbound incident, or open Domain 3B overlap |

`EPOCH_AMBIGUOUS` is a coverage **reason** string (`COVERAGE_REASON_EPOCH_AMBIGUOUS`), not a fifth `Evaluability` value (source: `functions/shared/wilvor_historical/query_contracts.py`; tests: `tests/unit/historical_facts/test_query_contracts.py`, `test_evaluability_enum_is_unchanged`).

Locked late-arrival horizons (source: `functions/shared/wilvor_historical/coverage.py`; tests: `tests/unit/historical_facts/test_coverage.py`, `test_policy_horizons_are_locked_constants`):

| Dataset | Horizon seconds |
| --- | --- |
| encounter | `173760` |
| risk | `173730` |
| hazard_version | `173760` |
| hazard_geometry | `87360` |

Control records use `CONTROL_SCHEMA_VERSION = "wilvor.historical.collection_control.v1"` and dataset `_collection_control`. They are not EncounterFact, RiskFact, HazardVersionFact, or HazardGeometryFact (source: `functions/shared/wilvor_historical/coverage_contracts.py`).

### Missing data and gaps

`GapDomain`: `DOMAIN_1`, `DOMAIN_2`, `DOMAIN_3A`, `DOMAIN_3B`, `GEOMETRY`, `CONTROL`.

`UncertaintyClass`: `KNOWN_MISSING`, `POTENTIAL_GAP`, `TRANSPORT_OR_CONTROL_OUTAGE`, `INTENTIONALLY_INACTIVE`.

`RecoveryState`: `OPEN`, `REPLAY_ATTEMPTED`, `REPLAY_PERSISTED`, `RESOLVED_PROVEN`, `UNABLE_TO_PROVE_COMPLETE`.

`MISSING_PROBE` gaps may later receive append-only `COLLECTION_GAP_RESOLUTION` (`RESOLVED_PROVEN`) under `metadata/resolutions/` only after exact probe confirmation, closed stream horizon, stream coverage, and no independent blocking gap. The original gap is never mutated (source: `modules/historical_facts/README.md`; tests: `tests/unit/historical_facts/test_coverage_control.py`, `test_confirmed_probe_after_horizon_writes_resolved_proven`).

Domain 3B `POTENTIAL_GAP` records use reason `S3_DESTINATION_FAILURE` when Firehose `DeliveryToS3.DataFreshness` Maximum > 1800 s, or when incoming activity remains without Success/DataFreshness for that window. A single buffering period without an S3 PUT is not Domain 3B. A later successful probe does not auto-resolve Domain 3B (source: `modules/historical_facts/README.md`; `functions/shared/wilvor_historical/coverage.py`, `DATA_FRESHNESS_THRESHOLD_SECONDS`).

`coverage_control` also materializes current-epoch control records on a 15-minute EventBridge schedule (source: `modules/historical_facts/coverage.tf`). `as_of_utc` for evaluation is injected by the caller. The store and gate do not read the wall clock.

### Coverage store load rule

Until a metadata index exists, V1 loads **all** objects under (source: `functions/shared/wilvor_historical/query_contracts.py`, `COVERAGE_STORE_METADATA_PREFIXES`):

- `metadata/epoch/`
- `metadata/activation/`
- `metadata/deactivation/`
- `metadata/coverage/`
- `metadata/gaps/`
- `metadata/resolutions/`
- `metadata/incidents/`

It does not restrict listing to the requested historical dates. Trailing-slash keys, unexpected empty objects, truncated listing pages, cyclic continuation tokens, and duplicate keys fail closed (source: `functions/shared/wilvor_historical_query/coverage_store.py`). Zero epoch records → `COVERAGE_STORE_UNAVAILABLE`. Overlapping active periods → `EPOCH_AMBIGUOUS` with `Evaluability` `None`.

A required-dataset result other than `EVALUABLE` blocks the whole operation. Unrelated-stream gaps (for example geometry) do not contaminate an encounter-only request (source: `functions/shared/wilvor_historical_query/README.md`).

### `VERIFIED_ZERO`

`is_verified_zero(coverage, semantic_match_count)` returns true only when coverage is `EVALUABLE` **and** the exact semantic match count is `0` (source: `functions/shared/wilvor_historical/query_contracts.py`, `is_verified_zero`; tests: `tests/unit/historical_facts/test_query_contracts.py`, `test_verified_zero_requires_evaluable_and_exact_semantic_count_zero`).

| Case | Status |
| --- | --- |
| `EVALUABLE` and exact semantic count `0` | `VERIFIED_ZERO` |
| `EVALUABLE` and exact semantic count `> 0` | `SUCCEEDED` |
| List fetched `N+1` rows | `RESULT_TRUNCATED`; `semantic_match_count` is null; `minimum_match_count = N + 1` |
| Any non-`EVALUABLE` coverage | never `VERIFIED_ZERO`; operations map to `COVERAGE_BLOCKED` |
| Executor `rows_returned` | not an input to `is_verified_zero` |
| Athena `COUNT(*) = 0` without EVALUABLE coverage | catalog validation prints `SQL_EMPTY`; not completeness |

A COUNT query that returns one result row with value 0 is still `VERIFIED_ZERO` when coverage is `EVALUABLE` and the exact semantic count is 0. Physical row counts are not encounter, aircraft, hazard, or risk-entity counts.

Athena never establishes completeness. `HistoricalAnalyticsOperations` runs a pre-query coverage gate, executes Athena only if `EVALUABLE`, then a post-query gate with the same `as_of_utc`. If post-query is not `EVALUABLE`, the Athena result is discarded as uncertified (source: `functions/shared/wilvor_historical_query/operations.py`).

Operator catalog validation (`scripts/validate_historical_analytics.ps1`) forbids the token `VERIFIED_ZERO` in executable artifacts (tests: `tests/infrastructure/terraform/test_historical_analytics_validation.py`, `test_verified_zero_is_forbidden_in_executable_artifacts`).

## Known limitations

- Missing coverage metadata fails closed. It is not converted into a verified empty history.
- `NOT_FOUND` on an AI `ToolResult` may represent coverage-certified `VERIFIED_ZERO`. That mapping is owned by WS5 (`functions/shared/wilvor_ai/historical_analytics_mapping.py`). It is not a current-state miss.
- Hazard-version windows use materialization/event time, not `valid_from_utc` / `valid_to_utc` overlap (`HAZARD_VERSION_WINDOW_LIMITATION`).
- V1 does not support airport, callsign, state, region, California, geometry, or current containment request fields (source: `functions/shared/wilvor_historical/query_contracts.py`, `FORBIDDEN_QUERY_REQUEST_FIELDS`).
- Whether coverage metadata currently exists in the persistent bucket is UNKNOWN.

## Historical record

No superseded statement is stored on this page.

## Sources

- Source: `functions/shared/wilvor_historical/coverage.py` (`evaluate_collection_window`)
- Source: `functions/shared/wilvor_historical/coverage_contracts.py` (`Evaluability`, `GapDomain`, `UncertaintyClass`)
- Source: `functions/shared/wilvor_historical/query_contracts.py` (`is_verified_zero`, `HistoricalQueryStatus`)
- Source: `functions/shared/wilvor_historical_query/operations.py` (`HistoricalAnalyticsOperations`)
- Tests: `tests/unit/historical_facts/test_coverage.py`
- Tests: `tests/unit/historical_facts/test_query_contracts.py` (`test_verified_zero_requires_evaluable_and_exact_semantic_count_zero`)
- Tests: `tests/unit/historical_query/test_operations.py` (`test_pre_evaluable_semantic_zero_post_evaluable_is_verified_zero`)
