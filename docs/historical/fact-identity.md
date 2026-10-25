# Canonical fact identity

**Canonical page:** this page | deep specification [`functions/shared/wilvor_historical/README.md`](../../functions/shared/wilvor_historical/README.md)
**Verified against:** `8abd2d5` on `docs/ws4-historical`
**Maturity:** CODE, UNIT
**Production authorization:** no grant found in `functions/shared/wilvor_historical`. This package does not persist facts or authorize collection.

## Purpose

This page records canonical identity, event time, deduplication, and schema versioning for the four historical datasets. Transport, coverage, and query execution stay on their canonical pages.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `functions/shared/wilvor_historical/contracts.py` |
| Terraform | absent | search: `envs/dev`, `envs/dev-historical-data`, `modules/` for fact dataclass resources; facts are Python contracts |
| Unit tests | UNIT | `tests/unit/historical_facts/test_contracts.py`, `test_from_events.py`, `test_time.py` |
| Contract tests | none found | search: `tests/contracts/` for `EncounterFact` |
| Infrastructure tests | none found for these dataclasses | search: `tests/infrastructure/` for `EncounterFact` |
| Integration tests | none found | search: `tests/integration/` for `historical` |
| Live harness | none found for these contracts | contracts are AWS-free |
| Deployment | UNKNOWN | contracts do not establish an applied lake |
| Production authorization | no grant found | this package has no AWS clients |

## Behavior

`wilvor_historical` is deterministic serialization and mapping code. It does not import AWS, `wilvor_ai`, or `wilvor_operational.current_set` (source: `functions/shared/wilvor_historical/README.md`; tests: `tests/unit/historical_facts/test_import_isolation.py`).

Frozen dataclasses are the historical schema, independent of EventBridge payload evolution and DynamoDB current-set semantics (source: `functions/shared/wilvor_historical/contracts.py`, module docstring).

### Datasets and schema versions

| Dataset | `fact_schema_version` | `fact_kind` | Canonical event time |
| --- | --- | --- | --- |
| `encounter` | `wilvor.historical.encounter_fact.v1` | `ENCOUNTER_OBSERVED`, `ENCOUNTER_TERMINAL` | `detected_at_utc` / `detected_at_epoch` |
| `risk` | `wilvor.historical.risk_fact.v1` | `RISK_RESULT` | `generated_at_utc` / `generated_at_epoch` |
| `hazard_version` | `wilvor.historical.hazard_version_fact.v1` | `HAZARD_VERSION` | `materialized_at_utc` |
| `hazard_geometry` | `wilvor.historical.hazard_geometry_fact.v1` | `HAZARD_GEOMETRY` | `materialized_at_utc` |

Constants: `ENCOUNTER_FACT_SCHEMA_VERSION`, `RISK_FACT_SCHEMA_VERSION`, `HAZARD_VERSION_FACT_SCHEMA_VERSION`, `HAZARD_GEOMETRY_FACT_SCHEMA_VERSION` (source: `functions/shared/wilvor_historical/contracts.py`). `producer_schema_version` is copied producer provenance only. Transport must not infer schema version from an S3 path (source: `functions/shared/wilvor_historical/README.md`).

Versioning rule recorded in that README: additive optional fields may remain v1 if readers tolerate absence; field rename, type change, or meaning change requires v2.

Glue tables store the expected version as `wilvor.expected_fact_schema_version`. That documents the catalog. Query SQL still filters row `fact_schema_version` from the code-owned constant (source: `modules/historical_analytics/glue.tf`; `functions/shared/wilvor_historical_query/query_registry.py`).

### Common envelope

Every fact includes `dataset`, `fact_schema_version`, `fact_kind`, `record_id`, `dedup_id`, `event_time_utc`, `event_time_epoch`, `event_year`, `event_month`, `event_day`, `source_system`, `producer_source`, `producer_detail_type`, `producer_schema_version`, `correlation_id` (source: `functions/shared/wilvor_historical/README.md`; envelope validation in `contracts.py`, `_validate_envelope`).

`event_year` / `event_month` / `event_day` are UTC partition parts derived only from canonical `event_time_utc` (source: `functions/shared/wilvor_historical/time.py`, `resolve_event_time`). Naive timestamps and non-UTC offsets are rejected. Canonical serialization uses `Z`.

Canonical fact time is never `time.time()`, EventBridge envelope time, Lambda time, or Firehose arrival time (source: `functions/shared/wilvor_historical/README.md`).

### Identity and deduplication

| Fact | `record_id` | `dedup_id` |
| --- | --- | --- |
| `EncounterFact` | `encounter_id` = `{projection_id}#{hazard_id}#{source_version}` | observed: `{encounter_id}\|ENCOUNTER_OBSERVED\|{detected_at_epoch}\|{encounter_state}`; terminal adds `\|{resolved_at_epoch}` |
| `RiskFact` | `risk_id` | `risk_id` |
| `HazardVersionFact` | `hazard_version_key` = `{hazard_id}#{source_version}` | same as `record_id` |
| `HazardGeometryFact` | same `hazard_version_key` | same as `record_id` |

Re-evaluations may emit additional `ENCOUNTER_OBSERVED` facts for the same `encounter_id`. Later encounter counts must use `DISTINCT encounter_id`, not `COUNT(*)` (source: `functions/shared/wilvor_historical/README.md`; tests: `tests/unit/historical_facts/test_from_events.py`, `test_same_encounter_different_evaluation_time_has_new_observation_dedup`).

`wilvor.risk` / `risk.updated` and `risk.resolved` are two transport opportunities for the same `RiskResult`. Provenance `producer_detail_type` may differ; analytics identity does not (tests: `tests/unit/historical_facts/test_from_events.py`, `test_risk_updated_and_resolved_are_the_same_result_fact`).

Hazard amendment versions are distinct facts. `valid_from_utc` / `valid_to_utc` are validity attributes, not partition time (tests: `tests/unit/historical_facts/test_from_events.py`, `test_hazard_amendment_versions_are_isolated`).

Mismatched `fact_schema_version` is rejected (tests: `tests/unit/historical_facts/test_contracts.py`, `test_mismatched_schema_version_is_rejected`).

### Event mapping

`fact_from_event(source, detail_type, detail)` maps:

- `wilvor.encounter` / `encounter.updated` → `ENCOUNTER_OBSERVED`
- `wilvor.encounter` / `encounter.resolved` → `ENCOUNTER_TERMINAL`
- `wilvor.risk` / `risk.updated` and `risk.resolved` → `RiskFact`
- `wilvor.weather` / `hazard.materialized` → `HazardVersionFact`

Unknown source/detail-type raises `HistoricalMappingError`. Geometry is constructed with `build_hazard_geometry_fact`, not `fact_from_event`. Default geometry `producer_detail_type` is `in_memory.hazard_geometry`, which is not an EventBridge detail-type (source: `functions/shared/wilvor_historical/from_events.py`, `GEOMETRY_IN_MEMORY_DETAIL_TYPE`).

### Geometry identity

`HazardGeometryFact` shares identity and canonical event time with the matching `HazardVersionFact`. Compact GeoJSON `Polygon` / `MultiPolygon` only, coordinates `[longitude, latitude]`, `coordinates_axis = "lonlat"` (source: `functions/shared/wilvor_historical/geometry_geojson.py`; `contracts.py`, `COORDINATES_AXIS_LONLAT`). Closing coordinates follow the enabled SIGMET writer. The builder does not recompute `geometry_hash`.

## Known limitations

- This package does not persist facts, create coverage, or query history.
- Terraform apply time is not verified historical coverage start (source: `functions/shared/wilvor_historical/README.md`).
- Encounter facts store no aircraft or intersection coordinates, so later geometry work cannot claim an impact occurred inside a region from encounter plus hazard polygon alone.
- Physical row counts are not encounter, aircraft, hazard, or risk-entity counts.

## Historical record

No superseded statement is stored on this page. Phase numbering in the package README is preserved there.

## Sources

- Source: `functions/shared/wilvor_historical/contracts.py` (`Dataset`, `FactKind`, schema version constants)
- Source: `functions/shared/wilvor_historical/from_events.py` (`fact_from_event`, `build_hazard_geometry_fact`)
- Source: `functions/shared/wilvor_historical/time.py` (`resolve_event_time`)
- Tests: `tests/unit/historical_facts/test_contracts.py`
- Tests: `tests/unit/historical_facts/test_from_events.py`
- Tests: `tests/unit/historical_facts/test_time.py`
