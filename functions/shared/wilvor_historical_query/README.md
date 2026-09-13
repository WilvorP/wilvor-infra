# Historical analytics query registry and executor

`wilvor_historical_query` renders deterministic Athena SQL for a closed
set of historical analytics queries and executes those rendered queries
through an injected, bounded Athena client.

## Current status

**2B.1 — implemented:** fixed registry and SQL renderer.

**2B.2 — implemented:** bounded deterministic Athena executor. Offline
fake-client unit tests only. Live AWS validation has **not** happened.

**Still not implemented:**

- **2B.3:** coverage S3 loader / gate
- **2B.4:** domain operations
- **2B.5:** query IAM policy
- **2B.6:** live validation / observability
- **Phase 2C:** `wilvor_ai` adapters

## Authority boundary

```
wilvor_historical
    <- wilvor_historical_query
    <- future Phase 2C adapters
```

`wilvor_historical` never imports this package. Phase 2B-preflight
contracts and `touched_utc_dates` stay AWS-free.

This package may import `wilvor_historical`. It must not import
`boto3`, `botocore`, or `wilvor_ai`. The executor does not create an
AWS client on import. Tests inject a fake Athena client.

## Fixed-query registry (2B.1)

Public V1 identities are exactly:

- `summarize_historical_encounters`
- `summarize_historical_risks`
- `summarize_historical_hazard_versions`
- `list_historical_encounters`

There is no API for raw SQL, table names, column lists, GROUP BY,
ORDER BY, arbitrary expressions, or LIMIT fragments. Query selection is
a typed Phase 2B-preflight request object.

`summarize_historical_risks` is one domain operation that renders **two**
internal fixed queries: the aggregate summary and a code-owned
`GROUP BY risk_level` distribution. Callers cannot choose the grouping
column. The distribution query is not a public V1 operation.

No geometry or geography query exists.

## Exact partition pruning

Touched UTC dates come from `wilvor_historical.query_windows`. The
renderer emits one exact triple per date:

```sql
(
  (year = '2026' AND month = '01' AND day = '31')
  OR
  (year = '2026' AND month = '02' AND day = '01')
)
```

Glue partition keys are zero-padded strings (`YYYY`, `MM`, `DD`). The
renderer never emits Cartesian `year IN (...) AND month IN (...) AND
day IN (...)`.

Every query also applies the half-open **whole-second** epoch predicate:

```sql
event_time_epoch >= <start_epoch>
AND event_time_epoch < <end_epoch>
```

Query-window bounds must be whole UTC seconds. That matches persisted
`event_time_epoch`. Stored `event_time_utc` may still have microseconds;
those rows share one epoch second. The renderer never uses raw VARCHAR
`event_time_utc` comparison, `MIN(event_time_utc)`, or
`ORDER BY event_time_utc` as temporal truth.

Intra-second order uses a code-owned fixed-width rewrite of the stored
canonical UTC string (`...00Z` → `...00.000000Z`). Summary min/max
return the actual stored string via `min_by` / `max_by` on that key.
List order uses the same key, then `record_id`, then `dedup_id`.
Athena timestamp parsers are not used.

Partitions alone are not correctness.

## Schema versions

`fact_schema_version` is a code-owned constant from
`wilvor_historical.contracts`. Callers cannot supply it.

| dataset | fact_schema_version |
| --- | --- |
| `encounter` | `wilvor.historical.encounter_fact.v1` |
| `risk` | `wilvor.historical.risk_fact.v1` |
| `hazard_version` | `wilvor.historical.hazard_version_fact.v1` |

Hazard-version windows use materialized/event time
(`event_time_epoch` / `event_time_utc`), not `valid_from_utc` /
`valid_to_utc`.

## Safe literal rendering

String values are single-quoted and internal quotes are doubled. Regex
validation on request contracts is not the injection boundary. The
renderer still escapes every literal.

## List truncation

`list_historical_encounters` ordering is code-owned:

normalized canonical `event_time_utc` ASC, `record_id` ASC, `dedup_id` ASC

`event_time_utc` is selected as the stored canonical value. Window
inclusion remains `event_time_epoch`.

Rendered `LIMIT` is `N + 1` for domain limit `N` in `1..200`. That
proves only `true matches >= N + 1` when truncated. It does not compute
an exact total.

Executor `max_data_rows` for a list query is the same `N + 1`. That is
a safety bound, not the domain `RESULT_TRUNCATED` contract.

Selected columns are the `HistoricalEncounterRecord` allowlist. No
`SELECT *`. No current DynamoDB enrichment.

## Bounded Athena executor (2B.2)

`AthenaExecutor.execute_fixed(query_id=..., request=...)` is the only
production execution entry point. Callers supply a closed
`InternalQueryId` and a typed historical request. The executor renders
through the 2B.1 registry and then executes that internal
`RenderedQuery`. There is no public `execute_sql` / `run_sql` /
`execute(RenderedQuery)` / raw-SQL API.

`InternalQueryId` has the five code-owned statements, including internal
`summarize_historical_risks_by_level`. That identity is not a fifth
`HistoricalOperation`. The query id must belong to the supplied request
type or the executor fails with `INVALID_REQUEST` before
`StartQueryExecution`.

Deployment values are composed into `AthenaExecutorConfig` and are
**not** historical request fields:

- dedicated historical `workgroup`
- dedicated historical Glue `database`
- `expected_results_prefix` = `s3://<results-bucket>/athena-results/`
  (trailing `/` is required or normalized)
- `timeout_seconds` default `180`
- `poll_interval_seconds` default `2`

A query caller cannot choose workgroup, database, `OutputLocation`, or
result reuse.

Every `StartQueryExecution` sends:

- `QueryString` = the 2B.1 rendered SQL
- `QueryExecutionContext.Database` = configured database
- `WorkGroup` = configured workgroup
- `ResultReuseConfiguration.ResultReuseByAgeConfiguration.Enabled = false`

The executor does **not** pass `ResultConfiguration.OutputLocation`.
The workgroup owns output location.

Polling uses `GetQueryExecution` as the authoritative state source.
Athena API states are `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, and
`CANCELLED`. That last spelling is **not** the EventBridge query-state
filter `CANCELED`. Clock and sleep are injectable. The loop is bounded
by monotonic elapsed time.

On timeout the executor calls `StopQueryExecution` for the exact
`QueryExecutionId` and fails with `QUERY_TIMEOUT`. A stop failure is
secondary diagnostic context; timeout is never converted into success.

After `SUCCEEDED`, and before `GetQueryResults`, the executor verifies:

- `WorkGroup` equals the configured workgroup
- `QueryExecutionContext.Database` equals the configured database
- `ResultConfiguration.OutputLocation` is present and starts with the
  exact derived prefix (trailing `/` prevents `athena-results-evil/`)
- when GetQueryExecution returns `Query`, it must exactly equal the
  internally rendered SQL

It does not inspect or write S3. Unexpected locations are not rewritten.

Result pages are consumed with `NextToken`. On the first page,
`ResultSetMetadata.ColumnInfo` and the first row's cell values must both
equal `output_columns` exactly and in order. Only that validated header
row is discarded. A first row of data is rejected, not silently dropped.
Later pages treat every row as data. A header-only first page is a
legitimate zero-data result (`rows_returned = 0`); that is not
`VERIFIED_ZERO`. SQL NULL in a **data** cell (missing `VarCharValue`)
stays `None`. Exceeding the code-owned `max_data_rows` bound fails with
`RESULT_LIMIT_EXCEEDED` rather than silent truncation.

Code-owned bounds:

- encounter / risk / hazard summaries: `1`
- risk-level distribution: `1000` (open stored labels; not assumed to
  be three risk levels)
- list encounters: `requested_limit + 1`

Success evidence includes `query_id`, `query_execution_id`, workgroup,
database, output location, generic typed rows (`column -> str | None`),
`rows_returned` (data rows only), and `data_scanned_bytes`
(`Statistics.DataScannedInBytes` as `int | None`). No dollar conversion.

The executor does **not** determine `VERIFIED_ZERO`. It does not call
`evaluate_collection_window` or `is_verified_zero`. A successful
aggregate row containing zero is just query data. Coverage (2B.3) and
semantic interpretation (2B.4) come later.

Default logging of rendered SQL is not added in this subphase.
