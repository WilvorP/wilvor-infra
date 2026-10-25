# Airport assessment

**Canonical page:** this page
**Verified against:** `8abd2d5` on `docs/ws3-processing-api`
**Maturity:** CODE, TF
**Production authorization:** no grant found in `functions/airport_assessment/processor/app.py`. Ranked airport candidates are advisory evidence. A candidate count of zero does not mean that no safe airport exists.

## Purpose

This module ranks nearby airports against a MEDIUM or HIGH risk result and writes evaluation-scoped `airport_assessment` rows. Pipeline placement: [`docs/processing/README.md`](../../docs/processing/README.md). Table keys: [`docs/data/dynamodb.md`](../../docs/data/dynamodb.md). There is no global current-assessment selector (source: `functions/shared/wilvor_operational/README.md`, AirportAssessment).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `functions/airport_assessment/processor/app.py` (`lambda_handler`, `build_candidate`, `publish_completed`, `scan_airports`) |
| Terraform | TF | `modules/airport_assessment/{processor,tables,monitoring,variables,outputs}.tf`; `envs/dev/main.tf` (`module.airport_assessment`) |
| Unit tests | none found for this processor | search: `tests/unit/airport_assessment/` returned 0 files. Reader tests exist at `tests/unit/shared/test_airport_assessment_reader.py` |
| Contract tests | none found for this processor | search: `tests/contracts/` for airport assessment processor. AI persisted-evidence contracts exist and are owned by WS5 |
| Infrastructure tests | none found | search: `tests/infrastructure/` for `modules/airport_assessment` |
| Integration tests | none found | search: `tests/integration/` for airport assessment |
| Live harness | none found | search: `scripts/validate_*` for airport assessment |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | `functions/airport_assessment/processor/app.py` |

## Behavior

### Event triggers

`aws_cloudwatch_event_rule.risk_updated` matches `wilvor.risk` / `risk.updated`. Gated by `var.enable_event_trigger` (`true` in `envs/dev/main.tf`). It does not match `risk.resolved`.

Publishes `wilvor.assessment` / `airport.assessment.completed` (`publish_completed`). Failed publish raises `RuntimeError`.

### Source tables and dependencies

Reads: `risk_results` GetItem; `aircraft_current_state` GetItem; **scan** `airport_status`; query `taf_forecast_periods` GSI `station_id-period_from_epoch-index` for an ETA window.

Does not subscribe to `airport.status.updated`.

### Output tables

`aws_dynamodb_table.airport_assessment` — PK `evaluation_id`, SK `airport_id`; GSIs `airport_assessment_id-index`, `airport_id-created_at_epoch-index`; TTL `expires_at_epoch`; SSE enabled; tag `TableRole = airport-candidate-result`.

### Processing responsibility

On `risk.updated` with `risk_id`:

- Skip when risk is missing (`RISK_NOT_FOUND`).
- Skip unless `risk_level` is `MEDIUM` or `HIGH` (`RISK_LEVEL_DOES_NOT_REQUIRE_AIRPORT_ASSESSMENT`).
- Skip when aircraft state is missing or `has_position` is false.

Deterministic `evaluation_id` from `risk_id`, `state_version`, and `ASSESSMENT_RULESET_VERSION`. Scan airports, rank by distance/score, persist candidates, publish completion with `COMPLETE` or `PARTIAL`.

Dev env defaults from module variables: `SEARCH_RADIUS_NM=250`, `MAX_CANDIDATES=10`, `ETA_UNCERTAINTY_MINUTES=10`, `RETENTION_SECONDS=86400`.

### State transitions and validity

Candidate `assessment_status` includes `COMPLETE` and `WAITING_FOR_WEATHER`. There is no `is_current_airport_assessment` (tests: `tests/unit/shared/test_wilvor_operational_current_set.py`, `test_no_global_airport_assessment_currentness_is_defined`). TTL is retention.

### Failure handling

No DLQ. Lambda and DynamoDB throttle alarms plus EventBridge failed invocations (`monitoring.tf`). Lambda: memory 512, timeout 60.

### Test coverage

No dedicated processor unit suite. Shared reader coverage: `tests/unit/shared/test_airport_assessment_reader.py` (`test_recommendation_get_uses_primary_key_and_consistent_read`, `test_evaluation_query_uses_partition_key_and_paginates`).

## Interfaces

Downstream: recommendation processor on `airport.assessment.completed`. Operational API airport detail returns `recentAssessments` from the `airport_id-created_at_epoch-index` GSI, not a global current set.

## Known limitations

- No dedicated processor unit tests under `tests/unit/airport_assessment/`.
- Full table scan of `airport_status` on each qualifying `risk.updated`.
- `build_candidate` stores `route_safety_status`, `runway_evidence_status`, and `congestion_evidence_status` as `UNAVAILABLE` (source: `functions/airport_assessment/processor/app.py`, `build_candidate`).
- Deployment is UNKNOWN.

## Historical record

`envs/dev/main.tf` still contains the comment "Enable after first manual processor verification." The assigned value at `8abd2d5` is `enable_event_trigger = true`.

## Sources

- Source: `modules/airport_assessment/processor.tf` (`aws_cloudwatch_event_rule.risk_updated`)
- Source: `modules/airport_assessment/tables.tf`
- Source: `functions/airport_assessment/processor/app.py` (`lambda_handler`, `build_candidate`)
- Tests: `tests/unit/shared/test_airport_assessment_reader.py`; `tests/unit/shared/test_wilvor_operational_current_set.py` (`test_no_global_airport_assessment_currentness_is_defined`)
