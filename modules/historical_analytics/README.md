# Historical analytics foundation (Phase 2A.2d)

This module is the **disabled-by-default** recreatable analytics
foundation. It currently owns:

- the feature flag `enable_historical_analytics` (default `false`)
- a gated read-only lookup of the persistent historical facts bucket
- a disposable Athena query-results S3 bucket
- an explicit Glue database and four external tables (no crawler)
- one dedicated Athena SQL workgroup
- operational CloudWatch observability (dashboard, FAILED/CANCELED alarm)

It does **not** create crawlers, runtime query IAM roles, named query
APIs, or query executors. Phase 2B.5 adds one reusable, **unattached**
customer-managed query policy. No execution role or attachment exists
in this module.

## Ownership boundary

Canonical historical facts are owned exclusively by
`envs/dev-historical-data` / `modules/historical_facts_data`.

- Persistent facts bucket: `force_destroy = false`
- Disposable Athena query-results bucket: `force_destroy = true`
- Disposable Athena workgroup: `force_destroy = true`

| Class | Root | Persistence | `force_destroy` |
| --- | --- | --- | --- |
| Historical facts bucket | `envs/dev-historical-data` | persistent | `false` |
| Athena query-results bucket | `envs/dev` (this module) | disposable derived data | `true` |
| Athena workgroup | `envs/dev` (this module) | disposable query-history control boundary | `true` |

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

`dev-up` recreates disposable `envs/dev` resources. The module default
for `enable_historical_analytics` is `false`. `envs/dev` intentionally
enables historical analytics, so normal `dev-up` recreates:

- Glue catalog (database and four external tables)
- Athena workgroup
- Athena results bucket
- unattached historical analytics query IAM policy
- analytics monitoring / dashboard

The Athena workgroup uses `force_destroy = true` because normal query
execution history otherwise prevents deletion. The results bucket uses
`force_destroy = true` because query results are derived disposable
artifacts. Canonical historical S3 is never owned by this module.

`dev-down -Force`:

1. DEACTIVATEs historical collection when collection is enabled
2. destroys `envs/dev`, including this module's disposable Glue catalog,
   workgroup, results bucket, unattached query IAM policy, and
   analytics monitoring
3. leaves the persistent historical facts bucket intact

There is no `historical-data-down.ps1`. Ordinary `dev-down` / `dev-reset`
must not target `envs/dev-historical-data`.

## Enablement

The module default for `enable_historical_analytics` remains `false`.
`envs/dev` intentionally enables historical analytics.

## Query IAM policy (Phase 2B.5)

Name: `${name_prefix}-historical-analytics-query`
(`wilvor-dev-historical-analytics-query` in dev).

This is a disposable `aws_iam_policy` owned by this module / `envs/dev`.
It is **not** attached to any principal in 2B.5. There is no
`aws_iam_role`, trust policy, or `sts:AssumeRole` here.

- Operational API, dashboard API, coverage_control, ingestion, and
  human SSO do **not** receive this policy
- Future Agent API gets its own Lambda-trust role and will attach this
  policy ARN later. That attachment is **not** part of 2B.5
- Operator SSO used by live validation does **not** prove this managed
  policy. 2B.6 retrieved and simulated the deployed document; it did
  not attach the policy to the operator or create a Lambda role.

Permissions describe only the deterministic historical query path:

| Area | Scope |
| --- | --- |
| Athena | `StartQueryExecution`, `GetQueryExecution`, `GetQueryResults`, `StopQueryExecution` on this module's workgroup only |
| Glue | `GetDatabase` / `GetTable` on the catalog, historical database, and V1 tables `encounter`, `risk`, `hazard_version` |
| Canonical facts bucket | read-only `GetObject` on those three `dataset=` prefixes plus the seven 2A.1 metadata prefixes; `ListBucket` constrained to those prefixes |
| Results bucket | `GetBucketLocation` / prefix-limited `ListBucket`; object `GetObject` / `PutObject` / multipart abort-parts under `athena-results/` only |

`hazard_geometry` is cataloged but **not** in this V1 query policy.
Canonical writes, result-object deletes, named queries, crawlers, KMS,
CloudWatch, logs, IAM, STS, and Lambda invoke are absent.

Athena tables use partition projection (`projection.enabled = true`).
This policy therefore does not grant `glue:GetPartitions`.

`s3:GetBucketLocation` is granted on the canonical source bucket and
the results bucket because Athena query execution uses the caller's
credentials to resolve those bucket locations. It is not used by the
Python executor client.

Static Terraform tests prove intended policy structure. They do not
prove effective AWS authorization. 2B.6 retrieved the deployed default
policy version and simulated explicit allow/deny pairs. Simulation is
identity-policy logic only: it does not prove SCPs, permissions
boundaries, resource policies, or a future Lambda principal.

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
verified historical zero and is not `VERIFIED_ZERO`.
`evaluate_collection_window` remains authoritative.

## Athena workgroup (Phase 2A.2c)

Name: `${name_prefix}-historical-analytics`
(`wilvor-dev-historical-analytics` in dev).

The workgroup is a **cost and control boundary** over the already
cataloged historical facts. It is not a query API and it does not
decide collection completeness.

| Setting | Value |
| --- | --- |
| State | `ENABLED` |
| Engine | Athena engine version 3 |
| Enforce workgroup configuration | `true` |
| CloudWatch query metrics | `true` |
| Requester pays | `false` |
| Per-query scan cutoff | `10737418240` (10 GiB) |
| Force destroy | `true` |

The workgroup is disposable with `envs/dev`. Normal validation and
analytics use leave query-execution history in the workgroup. Without
`force_destroy = true`, Athena refuses `DeleteWorkGroup` and normal
`dev-down` teardown is blocked. This does **not** make canonical
historical facts disposable.

`enforce_workgroup_configuration = true` prevents clients from
redirecting results away from the controlled derived location.

Result location is the **disposable** results bucket only:

`s3://<results-bucket>/athena-results/`

That prefix is not the canonical historical bucket and must never use
`dataset=`, `metadata/`, or `errors/`. Canonical history remains
read-only.

Result encryption is `SSE_S3`. `expected_bucket_owner` is the account
id. If the results bucket owner does not match, Athena fails the
result write rather than writing elsewhere. The results bucket already
uses `BucketOwnerEnforced`; this workgroup does not set ACLs.

Exceeding the 10 GiB per-query cutoff **cancels/fails that query**. It
does not change historical truth and is not a workgroup-wide scan
budget.

### Result reuse

Result reuse is **not** a workgroup setting. AWS defaults it to
disabled. Each `StartQueryExecution` must choose it.

There is no query executor in this phase. 2A.2e validation and
the Phase 2B deterministic executor must submit:

`ResultReuseByAgeConfiguration.Enabled = false`

Do not assume a Terraform workgroup flag disables reuse.

## Observability (Phase 2A.2d)

Dashboard name / catalog id:

`${name_prefix}-historical-analytics` / `historical-analytics`

(`wilvor-dev-historical-analytics` in dev). Separate from
`historical-facts` because collection transport and analytics are
different failure domains.

This dashboard is **operational observability only**:

- Athena `AWS/Athena` metrics (`ProcessedBytes`, execution / planning /
  queue times) are cost-driver and performance visibility
- `ProcessedBytes` is not a billed-price calculator
- results-bucket `AWS/S3` `BucketSizeBytes` / `NumberOfObjects` are
  **daily** snapshots of derived disposable data (3-day lifecycle)
- no objects is not a failure
- no dataset row-count widget
- no coverage metadata widget
- no zero-query / inactivity alarm
- no SNS

Athena service events to EventBridge (`Athena Query State Change`) are
**best effort**. A missing failure event does not prove a query
succeeded. This path must not be used for coverage, verified-zero,
gap resolution, query-result correctness, or canonical completeness.

Future deterministic query code must use `GetQueryExecution` for
authoritative query status. Phase 2A.1 `evaluate_collection_window`
remains historical completeness authority.

The EventBridge rule admits only this workgroup and AWS states:

- `FAILED`
- `CANCELED`

AWS spelling is **`CANCELED`**, not `CANCELLED`. Scan-cutoff
cancellation and intentional manual cancellation may alarm. That is
acceptable operational signal in dev.

Custom metric: `Wilvor/Pipeline` / `HistoricalAnalyticsQueryFailed`
(dimension `WorkGroup`). Alarm: `>= 1` in one 5-minute period,
`treat_missing_data = notBreaching`.

The Operational API registers catalog id `historical-analytics` only
when `ENABLE_HISTORICAL_ANALYTICS_DASHBOARD=true`. While disabled, that
id returns 404 (not 500). The API has no Athena query permissions.

## Validation (Phase 2A.2e)

Operator tooling lives in `validation/` and
`scripts/validate_historical_analytics.ps1`.

It runs **only after** approved 2A.2f enable/apply. It is not a
deployment step and not a Phase 2B query API.

- Fixed SQL templates only. No `-Sql` / `-Query` parameter.
- Every `StartQueryExecution` sets
  `ResultReuseByAgeConfiguration.Enabled = false`.
- Result `OutputLocation` must start with
  `s3://<results-bucket>/athena-results/`.
- Exact S3 gzip JSONL line count must equal Athena `COUNT(*)`.
- Canonical Key/Size/ETag snapshots must be unchanged after queries.
- Geometry is validated as RegexSerDe `json_record` with
  `POLYGON`/`MULTIPOLYGON` → GeoJSON `Polygon`/`MultiPolygon`.
- Hazard version and geometry identities are compared as sets.
- Partition pruning evidence uses `DataScannedInBytes`.
- Athena empty results are **`SQL_EMPTY`**, never `VERIFIED_ZERO`.
- Coverage evaluator remains authoritative.
- No 2A.2 / 2B.5 query IAM role exists. The 2B.5 managed policy is
  unattached. Live validation uses operator SSO and does not itself
  prove that managed policy.
- This script must not be used as an AI query surface.

Dry-run makes no AWS calls:

```powershell
.\scripts\validate_historical_analytics.ps1 -ValidationDate 2026-09-11 -DryRun
```

## Phase 2B closure (2B.6)

Deterministic historical analytics is complete:

- four V1 operations through `HistoricalAnalyticsOperations`
- authoritative pre/post coverage certification
- fixed SQL only, bounded Athena executor, result reuse disabled
- least-privilege unattached query policy
- live operator validation via
  `scripts/validate_historical_query_runtime.py`
- persistent facts/metadata survived normal `dev-down`

Custom runtime CloudWatch metrics were **not** added. There is no
deployed Agent API/Lambda to emit them. 2B observability remains the
existing Athena workgroup dashboard, S3 result-bucket widgets, and
EventBridge FAILED/CANCELED → `Wilvor/Pipeline`
`HistoricalAnalyticsQueryFailed` alarm. That monitoring is
observability only, never query-correctness evidence.

Operator SSO proved runtime behavior. It did **not** prove future
Lambda-role authorization.

Phase 2C is complete as an offline AI-safe adapter layer:
`HistoricalQueryResponse` → `ToolResult`, bound
`HistoricalAnalyticsAdapter`, and `HISTORICAL_ANALYTICS_TOOLS`. That
catalog is ready for a future specialist. No model-backed specialist,
Master Agent, Agent API, LLM/provider integration, runtime Lambda
composition, or query-policy attachment exists yet.

### Frontend Vitest (2A.2d carry-forward)

The `historical-analytics` dashboard catalog test was added in 2A.2d.
The implementing environment did not have Node/npm on PATH. Before
Phase 2A.2 is declared fully complete, run the frontend catalog test in
CI or an environment with Node:

```powershell
cd dashboard
npm test -- src/config/cloudwatchDashboards.test.ts
```
