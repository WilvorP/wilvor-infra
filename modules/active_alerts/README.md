# Active alerts

**Canonical page:** this page
**Verified against:** `8abd2d5` on `docs/ws3-processing-api`
**Maturity:** CODE, TF, UNIT
**Production authorization:** no grant found in `functions/active_alerts/processor/app.py`. Alert rows are deduplicated operational awareness state, not ATC instructions and not mutations of deterministic risk or recommendations by an AI path.

## Purpose

This module maintains fingerprint-deduplicated `active_alerts` rows from recommendation updates and from risk or encounter resolution. Pipeline placement: [`docs/processing/README.md`](../../docs/processing/README.md). Table keys: [`docs/data/dynamodb.md`](../../docs/data/dynamodb.md).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `functions/active_alerts/processor/app.py` (`lambda_handler`, `handle_recommendation`, `resolve_risk`, `fingerprint`) |
| Terraform | TF | `modules/active_alerts/{processor,tables,monitoring,variables,outputs}.tf`; `envs/dev/main.tf` (`module.active_alerts`) |
| Unit tests | UNIT | `tests/unit/active_alerts/test_active_alert_processor.py` |
| Contract tests | none found | search: `tests/contracts/` for active alert processor |
| Infrastructure tests | none found | search: `tests/infrastructure/` for `modules/active_alerts` |
| Integration tests | none found | search: `tests/integration/` for active alerts |
| Live harness | none found | search: `scripts/validate_*` for active alerts |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | `functions/active_alerts/processor/app.py` |

## Behavior

### Event triggers

| Terraform rule | Pattern | Handler |
| --- | --- | --- |
| `aws_cloudwatch_event_rule.recommendation_updated` | `wilvor.recommendation` / `recommendation.updated` | `handle_recommendation` |
| `aws_cloudwatch_event_rule.risk_resolved` | `wilvor.risk` / `risk.resolved` | `resolve_risk` |
| `aws_cloudwatch_event_rule.encounter_resolved` | `wilvor.encounter` / `encounter.resolved` | load risks for the encounter, then `resolve_risk` |

Gated by `var.enable_event_trigger` (`true` in `envs/dev/main.tf`). Other `detail-type` values return `UNSUPPORTED_EVENT`. There is no rule for `risk.updated`.

Publishes `wilvor.alert` / `alert.updated` and `alert.resolved`.

### Source tables and dependencies

Reads: `recommendations` GetItem; `risk_results` GetItem and Query GSI `encounter_id-generated_at_epoch-index`; `active_alerts` Query GSI `aircraft_id-updated_at_epoch-index`.

### Output tables

`aws_dynamodb_table.active_alerts` — PK `fingerprint` (not `alert_id`); GSIs `alert_id-index`, `aircraft_id-updated_at_epoch-index`, `alert_state-updated_at_epoch-index`; TTL `expires_at_epoch`; SSE enabled; tag `TableRole = deduplicated-alert-state`.

### Processing responsibility

Deduplicate by content fingerprint (`digest` / `fingerprint`). Recommendation-driven create/update is limited to MEDIUM/HIGH in `handle_recommendation`. Resolve on `risk.resolved` or `encounter.resolved`. Same fingerprint with a new recommendation updates one row (tests: `test_same_fingerprint_with_new_recommendation_updates_one_row`).

Env: `SCHEMA_VERSION` default `wilvor.active_alert.v4.0`; `RETENTION_SECONDS` default `86400`.

### State transitions and validity

`ACTIVE_STATES` = `NEW`, `MONITORING`, `ESCALATED`, `UPDATED`. Terminal: `RESOLVED`. API currentness uses those active states, future `valid_until_utc` on the full helper, and current `risk_id` **or** current `recommendation_id` (OR lineage; source: `functions/shared/wilvor_operational/current_set.py`, `is_current_alert`). TTL is retention.

### Failure handling

No DLQ. Processor error alarm and write-throttle alarm (`monitoring.tf`). Lambda: memory 256, timeout 30.

### Test coverage

`tests/unit/active_alerts/test_active_alert_processor.py`: `test_fingerprint_is_stable_across_recommendation_ids`, `test_same_fingerprint_with_new_recommendation_updates_one_row`, `test_encounter_resolve_resolves_active_alerts`, `test_resolve_item_replay_is_idempotent`.

## Interfaces

Operational API `GET /alerts/active` returns the current set, which is stricter than overview `alerts.activeCount` lifecycle-active counting. See [`docs/api/operational-api.md`](../../docs/api/operational-api.md).

## Known limitations

- Does not listen to `risk.updated`.
- OR-lineage currentness can keep an alert when either risk or recommendation remains current (source: `functions/shared/wilvor_operational/README.md`).
- Deployment is UNKNOWN.

## Historical record

No superseded statement is stored on this page.

## Sources

- Source: `modules/active_alerts/processor.tf` (`aws_cloudwatch_event_rule.recommendation_updated`, `aws_cloudwatch_event_rule.risk_resolved`, `aws_cloudwatch_event_rule.encounter_resolved`)
- Source: `modules/active_alerts/tables.tf`
- Source: `functions/active_alerts/processor/app.py` (`lambda_handler`, `ACTIVE_STATES`)
- Tests: `tests/unit/active_alerts/test_active_alert_processor.py`
