# Historical analytics query runtime

`wilvor_historical_query` renders deterministic Athena SQL for a closed
set of historical analytics queries, executes those rendered queries
through an injected, bounded Athena client, gates them with
authoritative coverage metadata, and returns typed V1 domain results.

## Current status

**2B.1 — implemented:** fixed registry and SQL renderer.

**2B.2 — implemented:** bounded deterministic Athena executor.

**2B.3 — implemented:** authoritative coverage metadata store and
fail-closed coverage gate.

**2B.4 — implemented:** four deterministic V1 historical operations.

**2B.5 — implemented:** reusable, unattached historical analytics query
IAM policy in `modules/historical_analytics`. No execution role, no
attachment, no Operational API change.

**2B.6 — implemented:** operator live-validation runner
`scripts/validate_historical_query_runtime.py`, deployed-policy
simulation, disposable lifecycle proof. Phase 2B is complete. There is
still no Agent API, no Lambda execution role, and no LLM.

**2C.1 — implemented:** `HistoricalQueryResponse` → Phase 0 `ToolResult`
mapping in `wilvor_ai.historical_analytics_mapping`.

**2C.2 — implemented:** bound `HistoricalAnalyticsAdapter` and the closed
`HISTORICAL_ANALYTICS_TOOLS` catalog in `wilvor_ai.historical_analytics`.
Trusted runtime supplies `HistoricalAnalyticsOperations`, `as_of_utc`, and
`tool_call_id`. Catalog `input_fields` is the model-visible allowlist.
Coverage-certified `VERIFIED_ZERO` mapping, first-class Evidence
provenance, and completeness ≠ freshness are preserved. AWS, SQL, query
ids, and current/geography fallback are not exposed.

**Phase 2C — complete:** AI-safe historical `ToolResult` mapping, bound
`HistoricalAnalyticsAdapter`, and the four-tool
`HISTORICAL_ANALYTICS_TOOLS` catalog. The catalog is ready for a future
specialist. Phase 2B remains the deterministic authority.

**Still not implemented:**

- model-backed Historical Analytics Specialist
- Master Agent
- Agent API / runtime AWS composition
- LLM/provider integration
- Lambda execution role / query-policy attachment to a future Agent API
- hybrid current + historical synthesis

## Authority boundary

```
wilvor_historical
    <- wilvor_historical_query
    <- wilvor_ai.historical_analytics
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
aggregate row containing zero is just query data.

Default logging of rendered SQL is not added in this subphase.

## Coverage store and gate (2B.3)

Phase 2A.1 `evaluate_collection_window` remains the sole completeness
authority. The 2B.3 layer only loads persisted control evidence and
calls that evaluator.

Metadata keys are partitioned by write/created time. V1 therefore lists
**all** objects under:

`metadata/epoch/`, `metadata/activation/`, `metadata/deactivation/`,
`metadata/coverage/`, `metadata/gaps/`, `metadata/resolutions/`,
`metadata/incidents/`

It does not restrict listing to the requested historical dates, a
current epoch, or a newest-N-days horizon. There is no metadata cache
or coverage index yet.

Phase 2A.1 writers persist `*.json` control records only. They do not
create S3 folder-marker objects under the authoritative prefixes. A
listed key ending in `/` is malformed coverage metadata and fails
closed, including zero-byte and non-zero trailing-slash objects.
Unexpected empty objects also fail closed. Listing fails closed on a
truncated page without `NextContinuationToken`, a repeated or cyclic
continuation token, or the same object key appearing twice in one
load. Distinct keys with similar record content are not deduplicated.

`as_of_utc` is injected by the caller. The store/gate do not read the
wall clock.

Ownership uses the same activation/deactivation pairing as the
evaluator (`collection_epoch_active_intervals`):

- sequential non-overlapping epochs are sliced and each owned slice is
  evaluated separately
- overlapping active periods → block with reason `EPOCH_AMBIGUOUS`
  (`Evaluability` stays `None`; this is not a fifth evaluability value)
- any inactive remainder of the requested window → `NOT_ACTIVE`
- zero epoch records → `COVERAGE_STORE_UNAVAILABLE`

A required-dataset result other than `EVALUABLE` blocks the whole
future operation. Unrelated-stream gaps (for example geometry) do not
contaminate an encounter-only request.

The coverage layer does not instantiate `AthenaExecutor`, render SQL,
or inspect fact row counts.

## Domain operations (2B.4)

`HistoricalAnalyticsOperations` injects a `CoverageGate` and
`AthenaExecutor`. It does not create AWS clients or read the wall
clock. `as_of_utc` is required and is the same value used for the
pre-query gate, the post-query gate, and
`QueryEvidence.evaluated_as_of_utc`. That field is the coverage
evaluation instant. It is not a response generation timestamp, and
`generated_at_utc` is not populated from `as_of_utc`.

Successful flow:

1. validate the typed request and injected `as_of_utc`
2. pre-query coverage gate for the exact window and operation dataset
3. if not `EVALUABLE`, return a blocked/unavailable response and execute
   **zero** Athena queries
4. `executor.execute_fixed(...)` with the closed internal query identity
   and typed request
5. strict domain parsing (`result_parsers.py`)
6. post-query coverage gate with the **same** `as_of_utc` and a fresh
   metadata load
7. if post-query is not `EVALUABLE`, discard the Athena result as
   uncertified
8. only then `SUCCEEDED` / `VERIFIED_ZERO` / `RESULT_TRUNCATED`

Athena never establishes completeness. `VERIFIED_ZERO` requires
post-query `EVALUABLE` **and** an exact semantic match count of 0.
Encounter/risk/hazard summaries use `physical_record_count`. A COUNT
row containing zero is still one Athena data row. Lists use returned
row count; `N+1` rows become `RESULT_TRUNCATED` with
`minimum_match_count = N + 1` and no exact total. There is no second
COUNT query.

`summarize_historical_risks` is atomic: aggregate first, then
risk-level distribution, then a required cross-query count check. If
either query or the consistency parse fails, there is no risk-summary
result.

Coverage mapping:

- `GAP_OR_UNCERTAIN` / `NOT_ACTIVE` / `NOT_YET_EVALUABLE` →
  `COVERAGE_BLOCKED`
- `EPOCH_AMBIGUOUS` → `COVERAGE_BLOCKED`, evaluability `None`
- store/malformed/pagination/inconsistent → `COVERAGE_STORE_UNAVAILABLE`

Hazard-version responses include the materialization/event-time
limitation. They do not reinterpret `valid_from_utc` / `valid_to_utc`.

Phase 2C adapters consume this package. They do not change these
operations, render SQL, or instantiate AWS clients. A model-backed
specialist and Agent API are not implemented here.
