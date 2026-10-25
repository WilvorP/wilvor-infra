# Encounter

**Canonical page:** this page
**Verified against:** `8abd2d5` on `docs/ws3-processing-api`
**Maturity:** CODE, TF, UNIT
**Production authorization:** no grant found in `functions/encounter/processor/app.py`. Encounter records are geometry and time-overlap evidence, not a selected hazard or a flight-control action.

## Purpose

This module evaluates H3 corridor intersection between a READY projection and materialized hazards, then writes `aircraft_hazard_encounter` rows. Pipeline placement: [`docs/processing/README.md`](../../docs/processing/README.md). Table keys: [`docs/data/dynamodb.md`](../../docs/data/dynamodb.md).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `functions/encounter/processor/app.py` (`lambda_handler`, `handle_projection_ready`, `handle_hazard_materialized`) |
| Terraform | TF | `modules/encounter/{processor,tables,monitoring,alarms,variables,outputs}.tf`; `envs/dev/main.tf` (`module.encounter`) |
| Unit tests | UNIT | `tests/unit/encounter/test_encounter_processor.py` |
| Contract tests | none found | search: `tests/contracts/` for encounter processor |
| Infrastructure tests | none found | search: `tests/infrastructure/` for `modules/encounter` |
| Integration tests | none found | search: `tests/integration/` for encounter |
| Live harness | none found | search: `scripts/validate_*` for encounter |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | `functions/encounter/processor/app.py` |

## Behavior

### Event triggers

| Terraform rule | Pattern | Handler |
| --- | --- | --- |
| `aws_cloudwatch_event_rule.projection_ready` | `wilvor.projection` / `projection.ready` | `handle_projection_ready` |
| `aws_cloudwatch_event_rule.hazard_materialized` | `wilvor.weather` / `hazard.materialized` | `handle_hazard_materialized` |

Both rules are gated by `var.enable_encounter_event_trigger` (`true` in `envs/dev/main.tf`). Any other `detail-type` raises `ValueError` (source: `lambda_handler`).

Publishes `wilvor.encounter` / `encounter.updated` on writes and `encounter.resolved` on terminal transitions (`publish_encounter_event`).

### Source tables and dependencies

Reads: `aircraft_projection` (GetItem and GSI `aircraft_id-generated_at_epoch-index`); `aircraft_projection_cells` (PK query and GSI `h3_cell-projection_id-index`); `hazard_cells`; `active_hazards` GetItem; `hazard_coordinates` Query.

Optional `HISTORICAL_FACTS_BUCKET_NAME` from `module.historical_facts` in `envs/dev/main.tf` for PutEvents failure gap metadata.

### Output tables

`aws_dynamodb_table.aircraft_hazard_encounter` — PK `encounter_id`; GSIs `aircraft_id-detected_at_epoch-index`, `hazard_id-detected_at_epoch-index`, `projection_id-hazard_version_key-index`; TTL `expires_at_epoch`; tag `TableRole = derived-current-versioned-decision`.

`encounter_id` is `{projection_id}#{hazard_version_key}`.

### Processing responsibility

- `projection.ready`: `evaluate_projection(projection_id)` — H3 corridor ∩ hazard cells, plus polygon cell check (`H3_MATCH_PLUS_HAZARD_POLYGON_CELL_CHECK`).
- `hazard.materialized`: resolve `hazard_version_key`, find projection IDs via hazard cells ∩ projection cells, evaluate each with a hazard filter.

Unknown altitude must not erase horizontal confirmation (source comment in `build_encounter_item`).

### State transitions and validity

Non-terminal: `DETECTED` when `exact_intersection_confirmed` (geometry plus time `OVERLAP`); otherwise `MONITORING`. Terminal: `RESOLVED`, `SUPERSEDED`, `EXPIRED` via `resolve_encounter` / `supersede_stale_encounters`.

Producer readiness `projection_is_ready` requires `READY` and rejects only when `int(valid_until_epoch) < current_epoch` (inclusive at the exact end). API `is_current_projection` uses `valid_until_epoch > now_epoch`. Encounter TTL does not define current-set membership (source: `functions/shared/wilvor_operational/README.md`).

Env: `AHE_SCHEMA_VERSION` default `wilvor.aircraft_hazard_encounter.v4.0`; `AHE_RETENTION_SECONDS` (dev default 3600 from the module variable).

### Failure handling

No DLQ. Alarms: Lambda errors, throttles, duration; DynamoDB throttles; EventBridge `FailedInvocations` for both rules (`alarms.tf`). `publish_encounter_event` failure records historical source put failure and may write S3 gap incident metadata when the bucket is configured.

Lambda: memory 512, timeout 60 (source: `modules/encounter/processor.tf`).

### Test coverage

`tests/unit/encounter/test_encounter_processor.py`: `test_projection_ready_writes_encounter`, `test_building_hazard_is_skipped`, `test_no_hazard_cell_match_returns_no_candidates`, `test_expired_projection_is_rejected`, `test_hazard_materialized_finds_projection_ids`, `test_unknown_altitude_does_not_confirm_or_erase_horizontal_encounter`, `test_altitude_overlap_and_no_overlap_from_available_bands`, `test_stale_projection_is_not_evaluated`, `test_newer_projection_supersedes_only_same_hazard`, `test_resolve_replay_is_idempotent`.

## Interfaces

Event bus `var.event_bus_name`. Downstream: risk processor on `encounter.updated` / `encounter.resolved`; alert processor on `encounter.resolved` only.

## Known limitations

- Producer vs API exact-end projection validity differ.
- Altitude overlap is often `UNKNOWN` when bands are unavailable; unknown altitude does not confirm or erase a horizontal encounter (tests: `test_unknown_altitude_does_not_confirm_or_erase_horizontal_encounter`).
- Deployment is UNKNOWN.

## Historical record

No superseded statement is stored on this page.

## Sources

- Source: `modules/encounter/processor.tf`, `modules/encounter/tables.tf`, `modules/encounter/alarms.tf`
- Source: `functions/encounter/processor/app.py` (`lambda_handler`, `projection_is_ready`, `build_encounter_item`)
- Tests: `tests/unit/encounter/test_encounter_processor.py`
