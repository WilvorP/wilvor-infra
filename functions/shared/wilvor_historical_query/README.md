# Historical analytics query registry (Phase 2B.1)

`wilvor_historical_query` renders deterministic Athena SQL for a closed
set of historical analytics queries. It does **not** execute queries.

## Authority boundary

```
wilvor_historical
    <- wilvor_historical_query
    <- future Phase 2C adapters
```

`wilvor_historical` never imports this package. Phase 2B-preflight
contracts and `touched_utc_dates` stay AWS-free.

This package may import `wilvor_historical`. It must not import
`boto3`, `botocore`, or `wilvor_ai`.

## Fixed-query registry only

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
column. Operation-level `QueryEvidence` carries a
`query_executions` tuple so both Athena execution ids, row counts, and
scanned bytes stay auditable. The distribution query is not a public
V1 operation.

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

Selected columns are the `HistoricalEncounterRecord` allowlist. No
`SELECT *`. No current DynamoDB enrichment.

## Later phases (not implemented here)

- **2B.2:** Athena execution
- **2B.3:** coverage-store gating
- **2B.4:** operation orchestration
- **2B.5:** query IAM
- **Phase 2C:** `wilvor_ai` ToolResult adapters
