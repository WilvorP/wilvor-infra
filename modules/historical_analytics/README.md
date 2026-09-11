# Historical analytics foundation (Phase 2A.2b)

This module is the **disabled-by-default** recreatable analytics
foundation. It currently owns:

- the feature flag `enable_historical_analytics` (default `false`)
- a gated read-only lookup of the persistent historical facts bucket
- a disposable Athena query-results S3 bucket
- an explicit Glue database and four external tables (no crawler)

It does **not** create Athena workgroups, crawlers, dashboards,
runtime query IAM roles, or named query APIs.

## Ownership boundary

Canonical historical facts are owned exclusively by
`envs/dev-historical-data` / `modules/historical_facts_data`.

- Persistent facts bucket: `force_destroy = false`
- Disposable Athena query-results bucket: `force_destroy = true`

| Class | Root | Persistence | `force_destroy` |
| --- | --- | --- | --- |
| Historical facts bucket | `envs/dev-historical-data` | persistent | `false` |
| Athena query-results bucket | `envs/dev` (this module) | disposable derived data | `true` |

The persistent bucket name is

`${name_prefix}-historical-facts-${account_id}-${aws_region}`

This module never declares `aws_s3_bucket` for that name. When
`enable_historical_analytics=true`, it looks the bucket up with gated
`data.aws_s3_bucket` using the same formula. When the flag is `false`,
that data source is not evaluated, so `terraform validate` does not
require the data-plane bucket to exist.

The results bucket name is

`${name_prefix}-historical-athena-results-${account_id}-${aws_region}`

Results are derived analytics artifacts (default 3-day current-object
expiry). They are not canonical historical truth, are not part of
coverage evaluation, and must never be written under the canonical
`dataset=` / `metadata/` / `errors/` prefixes.

Tags use `Component = historical-analytics` and
`DataType = historical-analytics-derived`. This bucket is **not** tagged
`DataType = historical-facts`.

## Lifecycle

`dev-up` recreates disposable `envs/dev` resources. With analytics still
disabled, this module creates nothing.

`dev-down -Force`:

1. DEACTIVATEs historical collection when collection is enabled
2. destroys `envs/dev`, including this module's results bucket if it
   exists (`force_destroy = true`)
3. leaves the persistent historical facts bucket intact

There is no `historical-data-down.ps1`. Ordinary `dev-down` / `dev-reset`
must not target `envs/dev-historical-data`.

## Enablement

`enable_historical_analytics` must remain `false` in `envs/dev` until a
reviewed analytics enable/apply.

## Glue catalog (Phase 2A.2b)

Database name: `${replace(name_prefix, "-", "_")}_historical_facts`
(`wilvor_dev_historical_facts` in dev).

Four external tables over the persistent bucket. The catalog is
read-only. It does not write canonical objects and does not decide
collection completeness.

| Table | SerDe | Physical prefix |
| --- | --- | --- |
| `encounter` | Hive JsonSerDe | `dataset=encounter/` |
| `risk` | Hive JsonSerDe | `dataset=risk/` |
| `hazard_version` | Hive JsonSerDe | `dataset=hazard_version/` |
| `hazard_geometry` | RegexSerDe `json_record` | `dataset=hazard_geometry/` |

Hive JsonSerDe class:

`org.apache.hive.hcatalog.data.JsonSerDe`

**OpenX (`org.openx.data.jsonserde.JsonSerDe`) is forbidden.** AWS
documents non-deterministic row counts and unexpected NULLs. Do not add
`ignore.malformed.json` or `case.insensitive`.

Canonical timestamps stay `string`. Invalid JSON or type mismatches
must fail the query (`HIVE_CURSOR_ERROR` / `HIVE_BAD_DATA`), not skip
rows.

`metadata/`, `errors/`, and `dataset=_collection_control/` are not
cataloged.

### Partition projection

Partition columns are `year`, `month`, `day` (Hive path keys). JSON
`event_year` / `event_month` / `event_day` remain data columns.

Projection uses integer year/month/day with digits 4/2/2 and range
`projection_year_min`–`projection_year_max` (defaults 2026–2036).
These partitions are **canonical event dates**, not Firehose arrival
or ingestion time:

- encounter: `detected_at_utc`
- risk: `generated_at_utc`
- hazard_version / hazard_geometry: `materialized_at_utc`

Terraform must emit literal Athena placeholders. The source uses
`$${year}` `$${month}` `$${day}` so the table parameter contains
`${year}` `${month}` `${day}`.

`dataset` is not a partition key. Each table `LOCATION` is already
scoped to one `dataset=` prefix.

### hazard_geometry raw line

Physical `geometry` is a nested GeoJSON object. `POLYGON` and
`MULTIPOLYGON` have different coordinate nesting depths, so a single
Hive struct/array type cannot represent both losslessly.

V1 table: `input.regex = "^(.*)$"` and one data column `json_record`.
Deterministic access:

```sql
json_extract_scalar(json_record, '$.record_id')
json_extract_scalar(json_record, '$.hazard_version_key')
json_extract_scalar(json_record, '$.fact_schema_version')
json_extract_scalar(json_record, '$.geometry_type')
json_format(json_extract(json_record, '$.geometry'))
```

Join:

```sql
hazard_version.record_id
  = json_extract_scalar(hazard_geometry.json_record, '$.record_id')
```

or the same identity via `hazard_version_key`.

### Schema version parameter

Each table sets `wilvor.expected_fact_schema_version` to the v1
constant. That documents the catalog. Phase 2B must still
filter/assert row `fact_schema_version`. This catalog does not union
schema versions.

### Coverage / zeros

Athena answers which rows are present. `COUNT(*) = 0` is not a
verified historical zero. `evaluate_collection_window` remains
authoritative. No Athena workgroup exists until Phase 2A.2c.
