# Projection

**Canonical page:** this page
**Verified against:** `8abd2d5` on `docs/ws3-processing-api`
**Maturity:** CODE, TF, UNIT
**Production authorization:** no grant found in `functions/projection/processor/app.py`. Projection output is deterministic trajectory evidence, not a clearance or route.

## Purpose

This module materializes a constant-velocity corridor projection when an eligible aircraft state update arrives. Pipeline placement: [`docs/processing/README.md`](../../docs/processing/README.md). Table keys: [`docs/data/dynamodb.md`](../../docs/data/dynamodb.md).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `functions/projection/processor/app.py` (`lambda_handler`, `evaluate_eligibility`, `materialize_projection`) |
| Terraform | TF | `modules/projection/{processor,tables,monitoring,variables,outputs}.tf`; `envs/dev/main.tf` (`module.projection`) |
| Unit tests | UNIT | `tests/unit/projection/test_projection_processor.py` |
| Contract tests | none found | search: `tests/contracts/` for projection processor |
| Infrastructure tests | none found | search: `tests/infrastructure/` for `modules/projection` |
| Integration tests | none found | search: `tests/integration/` for projection |
| Live harness | none found | search: `scripts/validate_*` for projection |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | `functions/projection/processor/app.py` |

## Behavior

### Event triggers

| Terraform rule | Pattern | Lambda target | Handler action |
| --- | --- | --- | --- |
| `aws_cloudwatch_event_rule.aircraft_state_updated` | `source: wilvor.aircraft`, `detail-type: aircraft.state.updated` | `aws_lambda_function.projection_processor` | Processed |
| `aws_cloudwatch_event_rule.hazard_materialized` | `source: wilvor.weather`, `detail-type: hazard.materialized` | same Lambda | Ignored |

Both rules are `ENABLED` when `var.enable_projection_event_trigger` is true. `envs/dev/main.tf` sets `enable_projection_event_trigger = true` at `8abd2d5`.

`lambda_handler` processes `aircraft.state.updated` only. Other `detail-type` values, including `hazard.materialized`, return `ignored: True` with `reason: UNSUPPORTED_EVENT_TYPE` instead of raising (source: `functions/projection/processor/app.py`, `lambda_handler`).

Publishes `wilvor.projection` / `projection.ready` via `publish_projection_ready`. Failed `PutEvents` raises `RuntimeError`.

### Source tables and dependencies

Reads (IAM in `processor.tf`):

- `aircraft_current_state` GetItem; Query GSI `current_h3_cell-position_time_epoch-index`
- `impact_cells` Query; Query GSI `hazard_version_key-h3_cell-index`
- `active_hazards` GetItem

### Output tables

Writes:

- `aws_dynamodb_table.aircraft_projection` — PK `projection_id`
- `aws_dynamodb_table.aircraft_projection_points` — PK `projection_id`, SK `point_key`
- `aws_dynamodb_table.aircraft_projection_cells` — PK `projection_id`, SK `h3_cell`

All three enable TTL on `expires_at_epoch`. PITR follows `var.enable_point_in_time_recovery` (`false` in `envs/dev/main.tf`).

### Processing responsibility

On `aircraft.state.updated`: eligibility → parent row in `BUILDING` → points and cells → count validation → `READY` → `projection.ready`. `projection_id` is a deterministic hash of `projection_identity`. A READY replay republishes `projection.ready` (`materialize_projection`).

Eligibility requires a matching `state_version`, usable aircraft state (position, H3, freshness, max position age, airborne when `REQUIRE_AIRBORNE=true`, speed/track), and at least one current impact-cell / active-hazard match at the aircraft H3 (source: `evaluate_eligibility`). EventBridge delay is treated as possible; older `state_version` events are not projected (source comment at `evaluate_eligibility`).

Dev algorithm/config env: `PROJECTION_ALGORITHM_VERSION` default `wilvor.projection.constant_velocity.v1`; `projection_config_version` in `envs/dev/main.tf` is `wilvor.projection.config.dev_wide_v2`; horizons `"5,10,15,30"` minutes.

### State transitions and validity

Parent statuses: `BUILDING` → `READY`. Materialization errors call `mark_projection_failed`.

`valid_until_epoch` = `generated_at_epoch + max(PROJECTION_HORIZONS_MIN)*60`. TTL `expires_at_epoch` = `valid_until + PROJECTION_RETENTION_SECONDS` (env default `3600`). Operational API currentness uses `READY` and `valid_until_epoch > now_epoch`; TTL is retention only (source: `functions/shared/wilvor_operational/current_set.py`, `is_current_projection`).

### Failure handling

No DLQ. Lambda error and throttle alarms plus DynamoDB throttle alarms in `monitoring.tf`. Handler re-raises eligibility and materialization failures after metrics.

Lambda: runtime `python3.12`, handler `app.lambda_handler`, memory 256, timeout 30 (source: `modules/projection/processor.tf`).

### Test coverage

`tests/unit/projection/test_projection_processor.py`:

- `test_valid_current_impact_match_is_eligible`
- `test_stale_aircraft_event_is_rejected`
- `test_no_impact_cell_match_is_not_eligible`
- `test_building_hazard_is_not_eligible`
- `test_ground_aircraft_is_not_eligible`
- `test_projection_parent_stores_altitude_as_decimal`

## Interfaces

Env vars from `processor.tf` include table names, `EVENT_BUS_NAME`, `PROJECTION_HORIZONS_MIN`, `CORRIDOR_GRID_DISTANCES`, `MAX_CORRIDOR_CELLS`, `PROJECTION_RETENTION_SECONDS`, `MAX_TRIGGER_HAZARDS`, `MAX_POSITION_AGE_SECONDS`, `REQUIRE_AIRBORNE`.

## Known limitations

- Terraform invokes the Lambda on `hazard.materialized`; Python does not refresh projections for that event.
- No historical-facts gap writer on failed `projection.ready` publish (raises `RuntimeError`).
- Deployment is UNKNOWN.

## Historical record

`envs/dev/main.tf` still contains the comment "Keep disabled until AircraftProjection parent materialization is implemented." The assigned value at `8abd2d5` is `enable_projection_event_trigger = true`.

## Sources

- Source: `modules/projection/processor.tf` (`aws_lambda_function.projection_processor`, `aws_cloudwatch_event_rule.aircraft_state_updated`, `aws_cloudwatch_event_rule.hazard_materialized`)
- Source: `modules/projection/tables.tf`
- Source: `functions/projection/processor/app.py` (`lambda_handler`, `evaluate_eligibility`, `materialize_projection`)
- Tests: `tests/unit/projection/test_projection_processor.py`
