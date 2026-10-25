# Historical facts transport (Phase 2A.1c)

**Canonical page:** this page | map [`docs/historical/transport.md`](../../docs/historical/transport.md)
**Verified against:** `8abd2d5` on `docs/ws4-historical`
**Maturity:** CODE, TF, UNIT, INFRA
**Production authorization:** no grant found. `enable_historical_facts = true` in `envs/dev/main.tf` is Terraform configuration, not a deployment record.

This module creates the **disabled-by-default** recreatable historical
transport / monitoring path:

- two DirectPut Firehose delivery streams (small facts vs geometry)
- one stdlib Firehose transform Lambda
- four narrow additional EventBridge rules on the **existing default bus**
  (encounter, risk, hazard.materialized, collection.probe)
- one Domain-2-only SQS DLQ and `dlq_gap_consumer`
- `coverage_control` (15-minute collect + explicit `DEACTIVATE`)
- CloudWatch dashboard and alarms

It does **not** create the persistent historical S3 bucket. That bucket and
all of its lifecycle / encryption / versioning / public-access configuration
are owned exclusively by `modules/historical_facts_data`
(`force_destroy = false`).

When `enable_historical_facts=true`, this module looks up the existing
bucket with gated `data.aws_s3_bucket` using the shared name
`${name_prefix}-historical-facts-${account_id}-${aws_region}`. When
`enable_historical_facts=false`, that data source is not evaluated.

It does **not** create Glue, Athena, or DynamoDB Streams.

## Enablement

The module variable default remains `false` (source: `modules/historical_facts/variables.tf`,
`enable_historical_facts`). The committed `envs/dev` root assigns
`enable_historical_facts = true` on `module.historical_facts` and
`module.operational_api` (source: `envs/dev/main.tf` at `8abd2d5` lines 117
and 805; tests: `tests/infrastructure/terraform/test_historical_facts_contract.py`,
`test_dev_historical_facts_collection_is_enabled`). That assignment is TF.
It does not establish DEPLOYED.

Ordinary `dev-down` refuses `envs/dev-historical-data` and therefore does
not destroy the persistent facts bucket. A disabled (`count = 0`) apply of
later wiring is not historical collection.

## Persistence guarantees

`ACCEPTED_FOR_BOUNDED_DELIVERY_RETRY` means Firehose accepted the record
(EventBridge target PutRecord/PutRecordBatch succeeded, or SIGMET
`PutRecord` succeeded). Bounded Firehose delivery retry may still fail later.

`DURABLY_PERSISTED` means a successful S3 dataset-prefix delivery was later
**observed**. This module does not prove that observation. Firehose
acceptance is never durable historical persistence.

Direct S3 historical control-plane writes (`metadata/*`) are create-once
(`PutObject` `IfNoneMatch="*"`). Identical retries are idempotent; a
conflicting object at the same key fails closed and does not mint a new
version.

`MISSING_PROBE` gaps may later receive an append-only
`COLLECTION_GAP_RESOLUTION` (`RESOLVED_PROVEN`) under `metadata/resolutions/`
only after exact probe confirmation, closed stream horizon, stream coverage,
and no independent blocking gap. The original gap is never mutated.

`coverage_control` also materializes current-epoch `DOMAIN_3B`
`POTENTIAL_GAP` records (`S3_DESTINATION_FAILURE`) from Firehose
`DeliveryToS3.DataFreshness` Maximum > 1800s, or from incoming activity
that remains without Success/DataFreshness for that same 1800s window.
A single buffering period without an S3 PUT is not Domain 3B. A later
successful probe does not auto-resolve `DOMAIN_3B`.

## Topology

| Stream | Name | Source | Datasets |
| --- | --- | --- | --- |
| facts | `${name_prefix}-historical-facts` | EventBridge historical rules | `encounter`, `risk`, `hazard_version` |
| geometry | `${name_prefix}-historical-geometry` | SIGMET DirectPut `PutRecord` | `hazard_geometry` |

Both streams share the same transform Lambda, the persistent historical
bucket, GZIP, JSONL (newline owned by the Lambda; `AppendDelimiterToRecord`
is not enabled), 128 MiB / 900 s buffering, and Lambda-derived dynamic
partitions.

Canonical success prefix:

```
dataset=!{partitionKeyFromLambda:dataset}/year=!{partitionKeyFromLambda:year}/month=!{partitionKeyFromLambda:month}/day=!{partitionKeyFromLambda:day}/
```

Partition keys come only from the fact canonical `event_time_utc` (or the
control observation clock for `_collection_control` probes). EventBridge
envelope time and Firehose `approximateArrivalTimestamp` are not canonical.

Error prefix (diagnostic; may use delivery time):

```
errors/result=!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/
```

## EventBridge mappings

Additional rules only. Existing operational targets are unchanged.

1. `wilvor.encounter` / `encounter.updated`, `encounter.resolved` → facts Firehose
2. `wilvor.risk` / `risk.updated`, `risk.resolved` → facts Firehose
3. `wilvor.weather` / `hazard.materialized` → facts Firehose
4. `wilvor.historical.control` / `collection.probe` → facts Firehose

There is **no** geometry EventBridge rule. All `HazardGeometryFact` records
and geometry probes use SIGMET / coverage_control DirectPut.

Historical target retry: `maximum_event_age_in_seconds = 86400`,
`maximum_retry_attempts = 185`, with `dead_letter_config` on
`${name_prefix}-historical-facts-dlq`.

## Failure domains

### DOMAIN 1 — producer accepted, EventBridge did not

DynamoDB / operational write succeeded, but `PutEvents` was not accepted.
This is a possible **collection gap**.

This SQS DLQ does **not** contain Domain 1 failures. Domain 1 producer →
EventBridge acceptance misses are **not** EventBridge historical-target DLQ
messages. This module does not implement automatic replay. Producers emit
`HistoricalSourcePutFailure` and a fail-open `metadata/incidents/` write.
The producer does not invent a collection epoch. coverage_control binds
the immutable staging incident to the current epoch as a `COLLECTION_GAP`.

### DOMAIN 2 — EventBridge accepted, historical Firehose target failed

The event is on the default bus. The additional historical Firehose target
failed after EventBridge acceptance. EventBridge retries, then the Domain-2
SQS DLQ. `dlq_gap_consumer` copies the message to `metadata/incidents/` then
deletes it. No automatic replay. coverage_control binds the incident.

### DOMAIN 3A — transform `ProcessingFailed`

The transform returns `ProcessingFailed` (never `Dropped`) for malformed
JSON, unknown source/detail-type, missing canonical time, contract
validation failure, or unsupported geometry. Control probes use the same
Ok / ProcessingFailed contract.

Firehose may write a replayable processing-failed object that includes
`rawData`. That object is diagnostic under `errors/` (30 days).
`coverage_control` writes a 365-day normalized gap.

### DOMAIN 3B — Firehose accepted, S3 destination unavailable

Firehose has already accepted the record. Destination delivery uses Firehose
bounded retry/retention. Missing coverage intervals fail closed.

**Do not claim** that every S3 destination outage becomes a replayable
`s3delivery-failed` / processing-failed object.

### GEOMETRY — DirectPut fail-open

SIGMET builds `HazardGeometryFact` from in-memory READY geometry and
`PutRecord`s to the geometry stream.

- Blank `HISTORICAL_GEOMETRY_FIREHOSE_STREAM_NAME`: skip transport.
- Payload `> 1_024_000` bytes: no PutRecord, no truncation/H3/bbox/split;
  emit `HistoricalGeometryOversized`; continue operational SIGMET processing.
- PutRecord error: emit `HistoricalGeometryPutFailure`; **do not** fail the
  operational SIGMET record.
- PutRecord success: emit `HistoricalGeometryPutSuccess` =
  `ACCEPTED_FOR_BOUNDED_DELIVERY_RETRY` only.

Oversized reject and fail-open errors are possible **geometry coverage gaps**.

## Lifecycle

Owned by `modules/historical_facts_data`, not this module.

| Class | Prefix | Retention |
| --- | --- | --- |
| current facts | `dataset=` | 365 days |
| current error objects | `errors/` | 30 days |
| metadata / coverage / gaps / incidents | `metadata/` | ≥ fact retention |
| noncurrent versions | whole bucket | 30 days |
| incomplete multipart | whole bucket | abort after 1 day |

`force_destroy = false`.

## IAM

- Transform role: CloudWatch log writes only. No DynamoDB, S3, EventBridge,
  Firehose, Glue, or Athena.
- EventBridge target role: `firehose:PutRecord` and `firehose:PutRecordBatch`
  on the **facts** stream only.
- Firehose role: documented S3 destination actions on the looked-up
  historical bucket, Lambda invoke/get-configuration for the transform,
  Firehose log writes.
- coverage_control: logs, control PutEvents, geometry PutRecord, narrow S3
  metadata / `_collection_control` / `errors/` access, `GetMetricData`.
- dlq_gap_consumer: DLQ consume + `metadata/incidents/` PutObject.
- SIGMET / encounter / risk (outside this module): optional
  `s3:PutObject` on `metadata/incidents/*` when the bucket ARN is configured.
  SIGMET also has `firehose:PutRecord` on the geometry stream only.

## Dashboard

`${name_prefix}-historical-facts` is registered in the Operational API
catalog only when `enable_historical_facts=true`. While disabled, the API
returns 404 for that id (not 500). Alarms use `treat_missing_data =
notBreaching` and have no SNS. There is no zero-ingestion alarm.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `functions/historical_facts/transform/app.py`, `coverage_control/app.py`, `dlq_gap_consumer/app.py` |
| Terraform | TF | `modules/historical_facts/*.tf`; `envs/dev/main.tf` `module.historical_facts` |
| Unit tests | UNIT | `tests/unit/historical_facts/` |
| Contract tests | none found | search: `tests/contracts/` for this module |
| Infrastructure tests | INFRA | `tests/infrastructure/terraform/test_historical_facts_contract.py` |
| Integration tests | none found | search: `tests/integration/` for `historical` |
| Live harness | none found for current S3 delivery | operator scripts do not replace an apply record |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | TF `enable_historical_facts = true` is not DEPLOYED |

## Known limitations

- Firehose acceptance is never durable historical persistence.
- Domain 1 misses are not in the SQS DLQ and are not automatically replayed.
- Domain 3B is a potential gap; a later successful probe does not auto-resolve it.
- This module does not create Glue, Athena, or DynamoDB Streams.
- Whether collection currently delivers objects is UNKNOWN.

## Historical record

Earlier text on this page said `enable_historical_facts` must remain `false`
in env/dev until the persistent data plane exists and an approved
enable/apply is reviewed. That sentence described an enablement policy
before the committed `envs/dev` assignment. At `8abd2d5`,
`envs/dev/main.tf` sets `enable_historical_facts = true` on
`module.historical_facts` (line 117) and `module.operational_api`
(line 805).

Earlier text also said: "Do not apply this module as a live collector
while the facts bucket can be removed by ordinary `dev-down`." Ordinary
`dev-down` now refuses `envs/dev-historical-data` (source:
`scripts/dev-down.ps1`; tests:
`tests/infrastructure/terraform/test_historical_facts_contract.py`,
`test_dev_down_refuses_data_plane_and_deactivates_when_enabled`). The
bucket remains `force_destroy = false` on the data-plane root.

## Sources

- Source: `modules/historical_facts/variables.tf` (`enable_historical_facts`)
- Source: `envs/dev/main.tf` (`module.historical_facts`)
- Source: `modules/historical_facts/events.tf`
- Source: `modules/historical_facts/firehose.tf`
- Tests: `tests/infrastructure/terraform/test_historical_facts_contract.py` (`test_dev_historical_facts_collection_is_enabled`)
- Map: [`docs/historical/transport.md`](../../docs/historical/transport.md)
- Map: [`docs/historical/terraform-roots.md`](../../docs/historical/terraform-roots.md)
