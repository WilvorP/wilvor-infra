# Risk

**Canonical page:** this page
**Verified against:** `8abd2d5` on `docs/ws3-processing-api`
**Maturity:** CODE, TF, UNIT
**Production authorization:** no grant found in `functions/risk/processor/app.py`. Scoring is an advisory MVP model. A stored LOW level is not a statement that the aircraft is safe. Risk absence is not a LOW classification.

## Purpose

This module scores a stored encounter into a `risk_results` row. Pipeline placement: [`docs/processing/README.md`](../../docs/processing/README.md). Table keys: [`docs/data/dynamodb.md`](../../docs/data/dynamodb.md).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `functions/risk/processor/app.py` (`lambda_handler`, `build_risk_result`, `persist_risk_result`, `publish_risk_event`) |
| Terraform | TF | `modules/risk/{processor,tables,monitoring,variables,outputs}.tf`; `envs/dev/main.tf` (`module.risk`) |
| Unit tests | UNIT | `tests/unit/risk/test_risk_processor.py` |
| Contract tests | none found | search: `tests/contracts/` for risk processor |
| Infrastructure tests | none found | search: `tests/infrastructure/` for `modules/risk` |
| Integration tests | none found | search: `tests/integration/` for risk |
| Live harness | none found | search: `scripts/validate_*` for risk |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | `functions/risk/processor/app.py` (scoring comment block) |

## Behavior

### Event triggers

`aws_cloudwatch_event_rule.encounter_changes` matches `source: wilvor.encounter` and `detail-type: encounter.updated`, `encounter.resolved`. Gated by `var.enable_risk_event_trigger` (`true` in `envs/dev/main.tf`).

Publishes `wilvor.risk` / `risk.updated`, or `risk.resolved` when the encounter is terminal (`publish_risk_event`). `TERMINAL_ENCOUNTER_STATES` are `RESOLVED`, `SUPERSEDED`, `EXPIRED`.

### Source tables and dependencies

Reads `aircraft_hazard_encounter` GetItem only (IAM in `processor.tf`). Optional historical-facts bucket for PutEvents failure gap metadata (`envs/dev/main.tf`).

### Output tables

`aws_dynamodb_table.risk_results` — PK `risk_id`; GSIs `encounter_id-generated_at_epoch-index`, `aircraft_id-generated_at_epoch-index`; TTL `expires_at_epoch`; tag `TableRole = derived-versioned-decision`. Output `risk_results_encounter_index_name` = `encounter_id-generated_at_epoch-index`.

### Processing responsibility

`build_risk_result` applies a deterministic advisory MVP ruleset. Quoted source: "Unknown evidence contributes 0 positive points. Weak components cannot accumulate into HIGH." `persist_risk_result` uses `attribute_not_exists(risk_id)` for idempotency. The handler publishes when the row is created or the encounter is terminal.

Env: `RISK_SCHEMA_VERSION` default `wilvor.risk_results.v4.0`; `SCORING_RULESET_VERSION` default `wilvor.risk.ruleset.v2`; `SCORING_CONFIG_VERSION` default `wilvor.risk.config.dev.v1`; `RISK_RETENTION_SECONDS` default `86400`.

### State transitions and validity

A new risk row is written for a scored encounter. Terminal encounters produce a zero-risk result and `risk.resolved` (tests: `test_resolved_encounter_creates_zero_risk`). API currentness requires the `encounter_id` to be in the current encounter set; a present `valid_until_utc` must be strictly future; **missing `valid_until_utc` is accepted** (source: `functions/shared/wilvor_operational/current_set.py`, `is_current_risk`). TTL is retention.

### Failure handling

No DLQ. Alarms on Lambda errors/throttles, `risk_results` throttles, and EventBridge failed invocations. Failed `publish_risk_event` records historical gap metadata when configured.

Lambda: memory 256, timeout 30.

### Test coverage

`tests/unit/risk/test_risk_processor.py` includes HIGH/MEDIUM/LOW paths, unknown altitude, freshness, resolved zero risk, stable `risk_id`, handler publish, stale data cannot increase risk, and idempotent retry without republish. Named examples: `test_high_risk_for_confirmed_near_term_encounter`, `test_unknown_altitude_is_not_classified_low`, `test_stale_data_cannot_increase_risk`, `test_lambda_retry_does_not_republish_equivalent_risk`.

## Interfaces

Downstream: airport assessment and recommendations listen to `risk.updated`. Alerts listen to `risk.resolved`, not `risk.updated`.

## Known limitations

- Advisory MVP scoring; unknown evidence adds 0 points.
- Missing `valid_until_utc` is API-current for compatibility, not an endorsed aviation policy (source: `functions/shared/wilvor_operational/README.md`).
- Deployment is UNKNOWN.

## Historical record

`envs/dev/main.tf` still contains the comment "Keep disabled until manual Risk Processor verification succeeds." The assigned value at `8abd2d5` is `enable_risk_event_trigger = true`.

## Sources

- Source: `modules/risk/processor.tf` (`aws_cloudwatch_event_rule.encounter_changes`)
- Source: `modules/risk/tables.tf`
- Source: `functions/risk/processor/app.py` (`build_risk_result`, `publish_risk_event`, `TERMINAL_ENCOUNTER_STATES`)
- Tests: `tests/unit/risk/test_risk_processor.py`
