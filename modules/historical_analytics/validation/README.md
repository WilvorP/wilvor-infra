# Historical analytics validation (Phase 2A.2e)

This directory holds **fixed** SQL templates for operator validation of
the Glue/Athena catalog. The only executor is:

`scripts/validate_historical_analytics.ps1`

Run it only after a human-approved Phase 2A.2f deployment.

This is **infrastructure integrity** validation. It is not a historical
query API, not a coverage evaluator, and not an AI/SQL surface.

## What it proves

For one explicit UTC date (`YYYY-MM-DD`):

- exact S3 gzip JSONL non-blank line count == Athena `COUNT(*)`
- every selected row has the expected `fact_schema_version`
- projected `year`/`month`/`day` match canonical `event_*` fields
- `event_time_utc` is canonical UTC `Z`
- encounter physical rows vs unique encounter identities vs dedup ids
- risk `risk_id = record_id = dedup_id`
- hazard_version `record_id = dedup_id = hazard_version_key`
- hazard_geometry raw-line JSON parses and geometry types map
- hazard version and geometry identity **sets** match (EXCEPT both ways)
- every Athena result lands under the disposable `athena-results/` prefix
- `ResultReuseByAgeConfiguration.Enabled = false` on every query
- canonical Key/Size/ETag snapshots are unchanged after queries
- `DataScannedInBytes` is reported; pruning is compared only when the
  same month has additional day data

Athena `COUNT(*) = 0` is printed as **`SQL_EMPTY`**. That means the
infrastructure query returned zero matching rows. No
collection-completeness or evaluability conclusion is made.
Phase 2A.1 `evaluate_collection_window` remains completeness authority.

## How to run

Dry-run (no AWS, required for 2A.2e):

```powershell
.\scripts\validate_historical_analytics.ps1 `
    -ValidationDate 2026-09-11 `
    -DryRun
```

After approved 2A.2f, using operator SSO (`wilvor-dev`):

```powershell
.\scripts\validate_historical_analytics.ps1 `
    -ValidationDate 2026-09-11
```

`-ValidationDate` is required. Known persisted development data exists
for `2026-09-11` UTC.

The script does not accept `-Sql`, `-Query`, or any caller SQL.

There is no 2A.2 runtime query IAM role. Live validation uses the
authenticated operator identity.

## Geometry types

Wilvor `geometry_type` is `POLYGON` / `MULTIPOLYGON`.
GeoJSON `geometry.type` is `Polygon` / `MultiPolygon`.
Validation compares them through that explicit mapping.

## Placeholders

Templates may contain only:

`__DATABASE__` `__TABLE__` `__YEAR__` `__MONTH__` `__DAY__` `__SCHEMA_VERSION__`

The script replaces those from validated `NamePrefix` and
`ValidationDate`. Table names are an allowlist of the four canonical
datasets.
