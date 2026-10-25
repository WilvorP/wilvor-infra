# Recommendations

**Canonical page:** this page
**Verified against:** `8abd2d5` on `docs/ws3-processing-api`
**Maturity:** CODE, TF, UNIT
**Production authorization:** no grant found. `ADVISORY_NOTICE` states: "Advisory decision support only. Human operational review is required. Wilvor does not issue autonomous flight-control, diversion, landing, dispatch, or ATC instructions." (source: `functions/recommendations/processor/app.py`, `ADVISORY_NOTICE`). The stored `EVALUATE_DIVERSION` label is not a clearance, not a selected diversion, not a route, and not a dispatch or ATC instruction.

## Purpose

This module writes advisory `recommendations` rows from a risk result and optional airport-assessment evidence. Pipeline placement: [`docs/processing/README.md`](../../docs/processing/README.md). Table keys: [`docs/data/dynamodb.md`](../../docs/data/dynamodb.md).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `functions/recommendations/processor/app.py` (`lambda_handler`, `action_for`, `build_recommendation`, `persist`, `publish`) |
| Terraform | TF | `modules/recommendations/{processor,tables,monitoring,variables,outputs}.tf`; `envs/dev/main.tf` (`module.recommendations`) |
| Unit tests | UNIT | `tests/unit/recommendations/test_recommendation_processor.py` |
| Contract tests | none found | search: `tests/contracts/` for recommendation processor |
| Infrastructure tests | none found | search: `tests/infrastructure/` for `modules/recommendations` |
| Integration tests | none found | search: `tests/integration/` for recommendations |
| Live harness | none found | search: `scripts/validate_*` for recommendations |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | `functions/recommendations/processor/app.py` (`ADVISORY_NOTICE`) |

## Behavior

### Event triggers

| Terraform rule | Pattern |
| --- | --- |
| `aws_cloudwatch_event_rule.risk_updated` | `wilvor.risk` / `risk.updated` |
| `aws_cloudwatch_event_rule.assessment_completed` | `wilvor.assessment` / `airport.assessment.completed` |

Gated by `var.enable_event_trigger` (`true` in `envs/dev/main.tf`). Other `detail-type` values return `UNSUPPORTED_EVENT` without raising.

Publishes `wilvor.recommendation` / `recommendation.updated` only when a new row is created (`persist` then `publish`). Failed publish raises `RuntimeError`.

### Source tables and dependencies

Reads `risk_results` GetItem; queries `airport_assessment` by `evaluation_id`.

### Output tables

`aws_dynamodb_table.recommendations` — PK `recommendation_id`; GSIs `aircraft_id-created_at_epoch-index`, `risk_id-created_at_epoch-index`, `recommendation_status-updated_at_epoch-index`; TTL `expires_at_epoch`; SSE enabled; tag `TableRole = advisory-output`.

The GSI `recommendation_status-updated_at_epoch-index` is unused by established readers (source: `functions/shared/wilvor_operational/README.md`).

### Processing responsibility

- `risk.updated` with `MEDIUM` or `HIGH`: return `WAITING_FOR_AIRPORT_ASSESSMENT` and write nothing.
- `airport.assessment.completed`: load assessments, `build_recommendation`, conditional put, publish if created.
- `risk.updated` that is not MEDIUM/HIGH continues into `build_recommendation` (LOW maps to `MONITOR` via `action_for`).

`action_for`:

| Condition | `primary_action_type` |
| --- | --- |
| HIGH and at least one `assessment_status == COMPLETE` | `EVALUATE_DIVERSION` |
| HIGH or MEDIUM otherwise | `MONITOR_AND_PREPARE_OPTIONS` |
| else | `MONITOR` |

HIGH without complete assessments uses `MONITOR_AND_PREPARE_OPTIONS` (tests: `test_high_without_airport_evidence_does_not_divert`). Standard limitations always appended: fuel, performance, route, airline policy unavailable (`build_recommendation`). `requires_human_review` is stored `True` on all three `primary_action_details` objects, including `MONITOR` (source: `functions/recommendations/processor/app.py`, `build_recommendation`).

Env: `RETENTION_SECONDS` default `86400`; `TOP_CANDIDATE_COUNT` default `5`; `SCHEMA_VERSION` default `wilvor.recommendation.v4.0`.

### State transitions and validity

Written `recommendation_status` is `ACTIVE`. Recommendations do not currently transition on `risk.resolved`; they age out through validity/retention (source: `functions/shared/wilvor_operational/README.md`). Multiple recommendations may remain current for one risk. API list membership uses lifecycle-active plus current `risk_id` (see [`docs/api/operational-api.md`](../../docs/api/operational-api.md)).

### Failure handling

No DLQ. Lambda error alarm and recommendations write-throttle alarm (`monitoring.tf`). Lambda: memory 256, timeout 30.

### Test coverage

`tests/unit/recommendations/test_recommendation_processor.py`: `test_low_risk_is_monitor`, `test_medium_risk_prepares_options`, `test_high_without_airport_evidence_does_not_divert`, `test_high_with_complete_assessment_evaluates_diversion`, `test_equivalent_risk_context_reuses_recommendation_id`.

## Interfaces

Downstream: alert processor on `recommendation.updated`.

## Known limitations

- `risk.updated` for MEDIUM/HIGH does not write a recommendation; it waits for assessment completion.
- Fuel, aircraft performance, filed route, ATC clearance, and airline policy are stored as unavailable.
- Deployment is UNKNOWN.

## Historical record

No superseded statement is stored on this page.

## Sources

- Source: `modules/recommendations/processor.tf` (`aws_cloudwatch_event_rule.risk_updated`, `aws_cloudwatch_event_rule.assessment_completed`)
- Source: `modules/recommendations/tables.tf`
- Source: `functions/recommendations/processor/app.py` (`ADVISORY_NOTICE`, `action_for`, `lambda_handler`)
- Tests: `tests/unit/recommendations/test_recommendation_processor.py`
