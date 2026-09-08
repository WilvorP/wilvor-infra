# Historical facts transport (Phase 2A.1b)

This module creates the **disabled-by-default** historical fact transport path:

- one private historical S3 bucket
- two DirectPut Firehose delivery streams (small facts vs geometry)
- one stdlib Firehose transform Lambda
- three narrow additional EventBridge rules on the **existing default bus**
- one Domain-2-only SQS DLQ

It does **not** create Glue, Athena, dashboards, alarms, coverage manifests, or gap records.

## Enablement

`enable_historical_facts` must remain `false` in env/dev until Phase **2A.1c** durable coverage/gap and observability work is complete and reviewed.

**Do not apply this module as a live collector before 2A.1c.** A disabled (`count = 0`) apply of later wiring is not historical collection. Enabling collection without 2A.1c leaves coverage gaps unobserved.

## Persistence guarantees

`ACCEPTED_FOR_BOUNDED_DELIVERY_RETRY` means Firehose accepted the record (EventBridge target PutRecord/PutRecordBatch succeeded, or SIGMET `PutRecord` succeeded). Bounded Firehose delivery retry may still fail later.

`DURABLY_PERSISTED` means a successful S3 dataset-prefix delivery was later **observed**. This module does not prove that observation. Firehose acceptance is never durable historical persistence.

Never equate the two.

## Topology

| Stream | Name | Source | Datasets |
| --- | --- | --- | --- |
| facts | `${name_prefix}-historical-facts` | EventBridge historical rules | `encounter`, `risk`, `hazard_version` |
| geometry | `${name_prefix}-historical-geometry` | SIGMET DirectPut `PutRecord` | `hazard_geometry` |

Both streams share the same transform Lambda, the same historical bucket, GZIP, JSONL (newline owned by the Lambda; `AppendDelimiterToRecord` is not enabled), 128 MiB / 900 s buffering, and Lambda-derived dynamic partitions.

Canonical success prefix:

```
dataset=!{partitionKeyFromLambda:dataset}/year=!{partitionKeyFromLambda:year}/month=!{partitionKeyFromLambda:month}/day=!{partitionKeyFromLambda:day}/
```

Partition keys come only from the fact canonical `event_time_utc`. EventBridge envelope time and Firehose `approximateArrivalTimestamp` are not canonical.

Error prefix (diagnostic; may use delivery time):

```
errors/result=!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/
```

## EventBridge mappings

Additional rules only. Existing operational targets are unchanged.

1. `wilvor.encounter` / `encounter.updated`, `encounter.resolved` → facts Firehose
2. `wilvor.risk` / `risk.updated`, `risk.resolved` → facts Firehose
3. `wilvor.weather` / `hazard.materialized` → facts Firehose

There is **no** geometry EventBridge rule. All `HazardGeometryFact` records use SIGMET DirectPut.

Historical target retry: `maximum_event_age_in_seconds = 86400`, `maximum_retry_attempts = 185`, with `dead_letter_config` on `${name_prefix}-historical-facts-dlq`.

## Failure domains

### DOMAIN 1 — producer accepted, EventBridge did not

DynamoDB / operational write succeeded, but `PutEvents` was not accepted. This is a possible **collection gap**.

This SQS DLQ does **not** contain Domain 1 failures. Domain 1 producer → EventBridge acceptance misses are **not** EventBridge historical-target DLQ messages. This module does not implement automatic replay.

### DOMAIN 2 — EventBridge accepted, historical Firehose target failed

The event is on the default bus. The additional historical Firehose target failed after EventBridge acceptance. EventBridge retries, then the Domain-2 SQS DLQ.

This DLQ is Domain 2 only. No automatic replay is implemented.

### DOMAIN 3A — transform `ProcessingFailed`

The transform returns `ProcessingFailed` (never `Dropped`) for malformed JSON, unknown source/detail-type, missing canonical time, contract validation failure, or unsupported geometry.

Firehose may write a replayable processing-failed object that includes `rawData`. That object is diagnostic under `errors/`.

### DOMAIN 3B — Firehose accepted, S3 destination unavailable

Firehose has already accepted the record. Destination delivery uses Firehose bounded retry/retention.

**Do not claim** that every S3 destination outage becomes a replayable `s3delivery-failed` / processing-failed object. Processing-failed objects are for transform/processing failures (3A), not a guaranteed capture of destination outages.

### GEOMETRY — DirectPut fail-open

SIGMET builds `HazardGeometryFact` from in-memory READY geometry and `PutRecord`s to the geometry stream.

- Blank `HISTORICAL_GEOMETRY_FIREHOSE_STREAM_NAME`: skip transport.
- Payload `> 1_024_000` bytes: no PutRecord, no truncation/H3/bbox/split; emit `HistoricalGeometryOversized`; continue operational SIGMET processing.
- PutRecord error: emit `HistoricalGeometryPutFailure`; **do not** fail the operational SIGMET record.
- PutRecord success: emit `HistoricalGeometryPutSuccess` = `ACCEPTED_FOR_BOUNDED_DELIVERY_RETRY` only.

Oversized reject and fail-open errors are possible **geometry coverage gaps**.

## Lifecycle

| Class | Prefix | Retention |
| --- | --- | --- |
| current facts | `dataset=` | `historical_fact_retention_days` (default 365) |
| current error objects | `errors/` | `historical_fact_error_retention_days` (default 30) |
| noncurrent versions | whole bucket | `historical_fact_noncurrent_version_retention_days` (default 30) |
| incomplete multipart | whole bucket | abort after 1 day |

Versioning is enabled so replacements are recoverable for a bounded window; noncurrent expiration prevents silent forever retention.

## IAM

- Transform role: CloudWatch log writes only. No DynamoDB, S3, EventBridge, Firehose, Glue, or Athena.
- EventBridge target role: `firehose:PutRecord` and `firehose:PutRecordBatch` on the **facts** stream only.
- Firehose role: documented S3 destination actions on the historical bucket, Lambda invoke/get-configuration for the transform, Firehose log writes.
- SIGMET processor (outside this module): `firehose:PutRecord` on the **geometry** stream only, and only when that stream ARN is configured.

## 2A.1c not included

Dashboards, alarms, durable coverage/gap records, five-state evaluability, and `enable_historical_facts = true` belong to Phase 2A.1c.
