# Wilvor operational current-set semantics

## Purpose and authority

Phase 1A centralizes the current Operational API's definitions of operational
`CURRENT` and `ACTIVE` records. `wilvor_operational.current_set` is the single
shared implementation of those existing runtime semantics. The Operational API
uses it through its existing `current_set` import path, and future Phase 1B
query code may import it directly.

Here, **authoritative runtime semantics** means authoritative for what the
current Wilvor runtime does. It does **not** certify every preserved behavior as
the ideal aviation-domain policy. Phase 1A is a parity-preserving extraction,
not a policy review. The ambiguities below remain visible precisely so future
work does not mistake compatibility behavior for an endorsed rule.

The package is pure, standard-library-only domain code. Dependency direction
is:

```text
wilvor_operational.current_set
        ^
        |
operational_api.current_set compatibility facade
        ^
        |
operational_api.repository
```

The shared package has no AWS, boto3, Lambda configuration, environment,
network, writer, or `wilvor_ai` dependency.

## Reference time and validity boundaries

Every time-dependent shared function receives an explicit reference time.
The shared package never reads the wall clock. Operational API repository code
keeps its existing clock-acquisition points and passes those values into shared
functions.

Established boundaries are intentionally domain-specific:

- Aircraft and AirportStatus are current only when
  `expires_at_epoch > now_epoch` (strict).
- AircraftProjection is current only when
  `valid_until_epoch > now_epoch` (strict).
- A lifecycle-active or queryable-current hazard permits
  `valid_to_epoch == now_epoch`; it expires only when
  `valid_to_epoch < now_epoch` (inclusive end).
- A present RiskResult `valid_until_utc`, Recommendation
  `valid_until_utc`, or ActiveAlert `valid_until_utc` must parse to an epoch
  strictly greater than the supplied reference epoch.

The producer/API projection boundary differs: encounter production currently
treats `valid_until_epoch == now_epoch` as still ready, while the Operational
API excludes it. Phase 1A preserves the API boundary in shared API semantics
and does not repair the producer discrepancy.

## TTL and physical retention

DynamoDB TTL deletion is asynchronous. Physical presence is never proof that a
record is operationally current.

The persisted TTL field has different runtime roles by entity:

- Aircraft and AirportStatus membership explicitly uses
  `expires_at_epoch > now_epoch`.
- Projection membership uses `valid_until_epoch`; its TTL is retention.
- Hazard membership uses lifecycle status, materialization status, and
  `valid_to_epoch`; its TTL is retention.
- Encounter membership ignores TTL and uses state plus exact lineage.
- Risk, recommendation, and alert membership uses their established validity
  and lineage rules; TTL remains retention.

## Lifecycle-active and queryable/current

These concepts are not interchangeable:

- **Lifecycle-active** means the record's own state and validity permit active
  use.
- **Queryable/current** may additionally require materialization readiness or
  current upstream lineage.

The shared module exposes distinct functions where the runtime makes this
distinction. Existing compatibility helpers retain their existing narrower
meaning even when a stronger, explicitly named shared function also exists.

## Entity semantics

### AircraftCurrentState

The table is materialized as one row per `aircraft_id`. List, map, and overview
membership uses strict `expires_at_epoch > now_epoch`. Freshness labels and
`has_position` do not define current membership; the map separately requires
coordinates as presentation eligibility.

The aircraft detail path performs a consistent key lookup and does not apply
the expiry predicate. An expired row retained pending TTL deletion can
therefore be returned by detail while being absent from current lists.

### AircraftProjection

A projection candidate is current when:

1. `projection_status` normalizes to `READY`; and
2. `valid_until_epoch > now_epoch`.

For each normalized lower-case `aircraft_id`, the current winner is the maximum
tuple `(generated_at_epoch, projection_id)`. Missing/falsey generated epochs
behave as zero. The projection ID tie-break makes selection stable for a
provided candidate set.

Projection membership does not verify that `aircraft_state_version` equals the
currently materialized aircraft row. TTL does not participate.

### ActiveHazard

Lifecycle-active means:

1. `status` normalizes to `ACTIVE`; and
2. `valid_to_epoch >= now_epoch`.

Queryable-current adds:

3. `materialization_status` normalizes to `READY`.

`valid_from_epoch` does not participate in Operational API current membership.
The overview hazard `activeCount` uses lifecycle-active semantics through the
status/validity index and is therefore broader than `/hazards/active` and
encounter lineage, which also require `READY`.

The current hazard-version index maps `hazard_id` to `source_version`.

### AircraftHazardEncounter

An encounter is current when:

1. `encounter_state` is `DETECTED` or `MONITORING`;
2. its normalized aircraft ID maps to the same `projection_id` in the current
   projection index; and
3. its `hazard_id` maps to the same hazard source version in the current hazard
   index.

`hazard_source_version` is authoritative when present. The legacy
`hazard_version_key` suffix is a compatibility fallback when it is absent.
Missing projection or hazard lineage excludes the encounter. Terminal and
unknown states are excluded. Encounter TTL and its own validity timestamps do
not decide membership.

### RiskResult

Current risk selection begins with current encounter IDs. A risk is eligible
only when its `encounter_id` is current.

If `valid_until_utc` is present, it must parse and be strictly later than the
explicit reference time. **A missing `valid_until_utc` is currently accepted**
as unbounded. This is compatibility behavior, not an endorsed policy.

For each encounter, the first eligible input record is retained unless a later
candidate has a strictly greater `generated_at_epoch`. Equal generated epochs
do not replace the first candidate. The function is deterministic for a
provided ordered sequence, but DynamoDB scan order supplies no repository-wide
ordering guarantee. Phase 1A intentionally introduces no tie-break.

Encounter identity is the selection lineage. Copied projection/hazard fields
and scoring ruleset/config versions are not revalidated by the current reader.
Missing current risk remains missing and is never converted to zero or false.

### Recommendation

The existing public compatibility helper called
`is_current_recommendation` checks only:

1. `recommendation_status` is `ACTIVE`; and
2. `risk_id` belongs to the current risk-ID set.

It is intentionally non-temporal and retains that exact signature and meaning.

Lifecycle-active recommendation membership additionally requires a parseable
`valid_until_utc` strictly later than the explicit reference time. Full current
membership combines lifecycle-active status/validity with current-risk
lineage.

Every qualifying recommendation is current. There is no per-risk or
per-aircraft winner, so multiple recommendations may remain current. The
reader does not revalidate copied aircraft/hazard/risk metadata after the risk
ID joins. Recommendations do not currently transition on `risk.resolved`; they
age out through validity/retention.

### ActiveAlert

The existing public compatibility helper called `is_current_alert` checks:

1. `alert_state` is `NEW`, `MONITORING`, `ESCALATED`, or `UPDATED`; and
2. the alert has either a current `risk_id` **or** a current
   `recommendation_id`.

It is intentionally non-temporal and preserves OR-lineage behavior. A
disagreement between risk and recommendation references can still qualify when
one reference is current.

Lifecycle-active alert membership additionally requires a parseable
`valid_until_utc` strictly later than the explicit reference time. Full current
membership combines lifecycle-active status/validity with the existing OR
lineage. `RESOLVED`, unknown states, and expired alerts are excluded.

### AirportStatus

AirportStatus is materialized current state with one row per `airport_id`.
List and overview membership uses strict
`expires_at_epoch > now_epoch`. METAR/TAF freshness and weather-readiness
fields affect the record's operational contents, not row membership.

The airport detail path does not apply the expiry predicate, so a retained
expired row can still be returned. This mirrors aircraft detail behavior.

### AirportAssessment

AirportAssessment is evaluation-scoped, history-like decision context keyed by
`evaluation_id` and airport. The repository contains no authoritative global
"current airport assessment" rule. Phase 1A deliberately provides no
`is_current_airport_assessment` function or global selector.

## Preserved API composition differences

Phase 1A does not repair these current behaviors:

- Overview hazard `activeCount` is broader than queryable-current hazards
  because it does not require materialization `READY`.
- Aircraft and airport detail paths can return retained expired records that
  current lists exclude.
- `/encounters/active` and some detail compositions attach the newest risk
  returned by a GSI query without applying the overview's current-risk
  validity selection.
- Equal-time risk winners depend on candidate input order.
- Missing risk validity is accepted by current-risk selection.
- Hazard `valid_from` is ignored by API membership.
- Projection API and producer exact-end boundaries differ.
- Alert currentness uses current-risk OR current-recommendation lineage.
- Multiple recommendations can be current for the same risk.
- Snapshot/cache acquisition times can temporarily skew endpoint views.
- Airport detail can compose TAF periods without constraining them to one
  `taf_version_key`.

Changing any of these requires an explicit domain/policy decision and a
separately authorized behavior change.

## Phase boundaries

- **Phase 1A:** defines and shares what counts as current or active.
- **Phase 1B:** may retrieve authoritative current records.
- **Phase 1C:** may join records into aircraft, hazard, and airport contexts.
- **Phase 1D:** may search/filter contexts, including geospatial concerns.
- **Phase 1E:** may expose deterministic queries through Phase 0 AI contracts.

This package does not provide DynamoDB repositories, context queries, search,
geospatial logic, AI tools, `ToolResult`, `Evidence`, agents, providers, or AWS
infrastructure.

## Pre-refactor characterization baseline

- HEAD: `d6b5225a2d554121cdf5b5d405a053cdcd41df0d`
- Recorded: 2026-09-07
- Command:

  ```powershell
  $env:PYTHONDONTWRITEBYTECODE='1'; python -m pytest tests/unit/operational_api/test_current_state_characterization.py -q -p no:cacheprovider
  ```

- Result: `11 passed in 0.47s`

The characterization suite ran before `wilvor_operational.current_set`
existed. The identical suite must pass after facade conversion and repository
delegation.
