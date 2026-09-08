# Historical operational fact contracts

Phase 2A.1a defines versioned historical fact contracts and deterministic
mappings from already-produced operational data. It does **not** persist
facts, create coverage, or query history.

## Authority boundary

- **Current operational truth:** DynamoDB / Phase 1 current-set semantics.
- **Replay / rebuild source:** existing short-retention NOAA and OpenSky S3
  archives. Those objects are not Wilvor historical operational truth.
- **Historical operational truth:** the four fact datasets below, **only after**
  Phase 2A.1b successfully persists them. This package alone does not create
  historical coverage.

Do not historicalize DynamoDB TTL working rows or 3-day raw archives.
There is no backfill in 2A.1.

## Package rules

`wilvor_historical` is deterministic serialization and mapping code.

- No AWS clients, env lookups, or network.
- No `wilvor_ai`, LangGraph, or model SDKs.
- No `wilvor_operational.current_set` and no Phase 1 currentness logic.
- No Shapely. Geometry reconstruction uses ordered SIGMET rings.
- Canonical fact time is never `time.time()`, EventBridge envelope time,
  Lambda time, or Firehose arrival time.

## Datasets and schema versions

| dataset | fact_schema_version | fact_kind |
| --- | --- | --- |
| `encounter` | `wilvor.historical.encounter_fact.v1` | `ENCOUNTER_OBSERVED`, `ENCOUNTER_TERMINAL` |
| `hazard_version` | `wilvor.historical.hazard_version_fact.v1` | `HAZARD_VERSION` |
| `hazard_geometry` | `wilvor.historical.hazard_geometry_fact.v1` | `HAZARD_GEOMETRY` |
| `risk` | `wilvor.historical.risk_fact.v1` | `RISK_RESULT` |

`producer_schema_version` is copied producer provenance only. Historical
`fact_schema_version` is independently owned. Transport must never infer
schema version from an S3 path.

Versioning:

- Additive optional fields may remain v1 if readers tolerate absence.
- Field rename, type change, or meaning change requires v2.

## Common envelope

Every fact includes:

`dataset`, `fact_schema_version`, `fact_kind`, `record_id`, `dedup_id`,
`event_time_utc`, `event_time_epoch`, `event_year`, `event_month`,
`event_day`, `source_system`, `producer_source`, `producer_detail_type`,
`producer_schema_version`, `correlation_id`.

`event_year` / `event_month` / `event_day` are UTC partition parts derived
**only** from canonical `event_time_utc`. Naive timestamps and non-UTC
offsets are rejected. Canonical serialization uses `Z`.

`to_dict()` is JSON-safe. `Decimal` does not escape. `None`, `False`, `0`,
and empty collections are preserved where they are meaningful.

This is not a generic untyped event lake.

## EncounterFact

- `record_id` = `encounter_id` = `{projection_id}#{hazard_id}#{source_version}`
- Canonical event time = `detected_at_utc` / `detected_at_epoch`
- The live producer **overwrites** `detected_at` on re-evaluation. Phase 2A.1a
  does not change that current-set behavior.
- `encounter.updated` → `ENCOUNTER_OBSERVED`
- `encounter.resolved` → `ENCOUNTER_TERMINAL`
- Re-evaluations may emit additional observation facts for the **same**
  `encounter_id`. Later encounter counts must use `DISTINCT encounter_id`,
  not `COUNT(*)`.
- First persisted observation after collection exists can later be
  `MIN(event_time)` per `encounter_id`. 2A.1a does not implement SQL.
- New `projection_id` or hazard `source_version` produces a new `encounter_id`.
- Dedup:
  - observed: `encounter_id|ENCOUNTER_OBSERVED|{detected_at_epoch}|{encounter_state}`
  - terminal: `encounter_id|ENCOUNTER_TERMINAL|{detected_at_epoch}|{encounter_state}|{resolved_at_epoch}`
- EventBridge event ids are transport diagnostics only.

## RiskFact

Risk facts are immutable scored `RiskResult` rows. They are **not** a
separate historical lifecycle.

- `fact_kind` is always `RISK_RESULT`
- `record_id` = `dedup_id` = `risk_id`
- Canonical event time = `generated_at_utc` / `generated_at_epoch`
- `wilvor.risk` / `risk.updated` and `risk.resolved` are two transport
  opportunities for the same underlying result. Provenance
  `producer_detail_type` may differ; analytics identity does not.
- Multiple legitimate `risk_id` values may exist for one `encounter_id`.
  Equivalent scoring inputs reuse `risk_id`.

## HazardVersionFact

- `hazard_id` is stable logical SIGMET/AIRMET identity across amendments.
- `source_version` is a specific content/amendment version.
- `record_id` = `dedup_id` = `hazard_version_key` = `{hazard_id}#{source_version}`
- Amendment V1 and V2 are distinct version facts.
- Later “distinct SIGMETs” may count `DISTINCT hazard_id`.
- Later “versions” may count `DISTINCT hazard_version_key`.
- Canonical event time = `materialized_at_utc` (READY materialization).
  `valid_from_utc` / `valid_to_utc` are validity attributes, not partition
  time.
- `geometry_hash` is copied lineage from the enabled SIGMET processor. It is
  not recomputed here.
- Mapped from enabled `wilvor.weather` / `hazard.materialized` after READY.
  The disabled hazard-coordinates processor is not authority.

On the enabled producer, `published_at_utc` on `hazard.materialized` is the
**same timestamp value** as `materialized_at_utc`. They are not two wall
clocks.

## HazardGeometryFact

- Same identity and canonical event time as the matching HazardVersionFact.
- Compact GeoJSON `Polygon` / `MultiPolygon` only, coordinates
  `[longitude, latitude]`, `coordinates_axis = "lonlat"`.
- Exterior ring = `ring_index` 0; holes = `ring_index` 1+.
- Ordering: `polygon_index`, then `ring_index`, then `sequence_number`.
- Closing coordinates follow the **enabled** SIGMET writer (preserved, not
  stripped or invented).
- Built from the same in-memory flattened points used to write
  HazardCoordinates. No DynamoDB read. No H3/ImpactCells/bbox authority.

### Geometry transport is not selected in 2A.1a

`build_hazard_geometry_fact` is transport-neutral. Default
`producer_detail_type` is `in_memory.hazard_geometry`, which is **not** an
EventBridge detail-type.

Phase 2A.1b must choose delivery:

- EventBridge `hazard.geometry.materialized` plus Firehose overflow, or
- direct Firehose for all geometry facts.

2A.1a does not publish geometry events and does not create Firehose clients.

## Event mapping

`fact_from_event(source, detail_type, detail)` maps:

- `wilvor.encounter` / `encounter.updated` | `encounter.resolved`
- `wilvor.risk` / `risk.updated` | `risk.resolved`
- `wilvor.weather` / `hazard.materialized`

Unknown source/detail-type raises `HistoricalMappingError`. It does not
ignore the event. Geometry is constructed with
`build_hazard_geometry_fact`, not `fact_from_event`.

## Coverage (not implemented here)

Terraform apply time is **not** verified historical coverage start.

Future metadata may record `collection_enabled_at_utc`. It must not be named
`coverage_start_utc` unless continuous successful collection has actually
been established.

Example: infra enabled 02:00, delivery broken until 02:44. 02:00–02:44 must
not be reported later as a verified zero-event window.

Phase 2B/2C must distinguish verified zero facts, unproven collection, and
query failure. 2A.1c may persist collection metadata; 2A.1a does not.

## Later phases

- **2A.1b:** EventBridge / Firehose / S3 persistence.
- **2A.1c:** observability and collection metadata.
- **2A.2:** Glue / Athena.
- **2B:** deterministic historical queries.
- **2C:** analytics ToolResult adapters.

No AI, Glue, Athena, S3, or Firehose belongs in this package.
