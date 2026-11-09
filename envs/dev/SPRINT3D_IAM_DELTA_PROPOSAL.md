# Sprint 3D IAM delta proposal

This is a review document for a later infrastructure change. It does not change Terraform, IAM, Lambda concurrency, or `POST /ai/query`. Do not apply it as part of the offline integration.

The runtime role is `${name_prefix}-ai-host-lambda-role`. In development that is `wilvor-dev-ai-host-lambda-role`. The ledger table name remains `wilvor_master_execution_ledger`.

## Current policy

`modules/ai_host/iam.tf` allows `dynamodb:GetItem` and `dynamodb:PutItem` on the ledger table ARN only. `modules/ai_host/locals.tf` explicitly denies `dynamodb:TransactWriteItems` on the ledger and on `ledger/index/*`, together with Scan, Query, DeleteItem, BatchWriteItem, UpdateItem, CreateTable, and PartiQL. A separate statement denies `dynamodb:*` on every resource that is not the ledger table ARN. Bedrock, Secrets Manager secret reads, `lambda:InvokeFunction`, function URL configuration, and `execute-api:Invoke` stay denied.

That policy cannot perform Agent C's consume transaction or a specialist GetItem/Query.

## Proposed allow changes

1. Remove only `dynamodb:TransactWriteItems` from `prohibited_ledger_actions`.
2. Allow `dynamodb:TransactWriteItems` on the ledger table ARN only. Do not allow it on `ledger/index/*` or on any specialist table.
3. Keep `dynamodb:GetItem` and conditional `dynamodb:PutItem` on the ledger table ARN. PutItem remains the fence-replace API. TransactWriteItems is the consume API: one conditional put of the STARTED attempt and one conditional put of the consumption marker.
4. Keep Query, Scan, DeleteItem, UpdateItem, BatchWriteItem, CreateTable, and PartiQL denied on the ledger and on `ledger/index/*`.

## Proposed specialist-table changes

Replace the blanket `dynamodb:*` deny on all non-ledger resources with:

- Allow `dynamodb:GetItem` only on a reviewed physical table whose mapping allows `GET_ITEM`.
- Allow `dynamodb:Query` only on a reviewed physical table whose mapping allows `QUERY`.
- If a reviewed mapping sets `query_index_name`, also allow `dynamodb:Query` on `arn:aws:dynamodb:region:account:table/Name/index/IndexName` for that index only.
- The current aircraft mapping is GetItem on the base table. Its query partition is `callsign`, and QUERY is not allowed, so no aircraft GSI permission is required.
- The current historical mapping uses `aircraft_id` as both hash and query partition, so `query_index_name` is null. Query uses the base table. No historical index ARN is required.
- Deny PutItem, TransactWriteItems, Scan, DeleteItem, UpdateItem, BatchWriteItem, and PartiQL on those specialist tables.
- Keep `dynamodb:*` denied for every other table.

## Control plane

Do not grant `iam:SimulatePrincipalPolicy` to the AI host role. Simulation is an operator or deploy check by a separate reviewer principal. A role simulating its own policy is not evidence that the deployed policy is the reviewed policy.

Do not grant `dynamodb:DescribeTable` to the runtime role for this delta. DescribeTable on the ledger is an implicit deny today. On every other table it is an explicit `dynamodb:*` deny. A later nonproduction probe may allow DescribeTable on the ledger ARN and the specific read-table ARNs only, still without Scan.

`sts:GetCallerIdentity` does not need a new resource allow. Do not broaden STS.

## Keys, TTL, and invocation boundary

Do not add KMS. Grant signatures in this sprint are injected HMAC keys. `PRODUCTION_SIGNING_KEY_STORE_INTEGRATED` stays false. The ledger already uses AWS-owned SSE, not a customer key. The RSA material in `tests/contracts/_rsa_test_key.txt` is disposable test material and must not be a signing key or a Lambda package input. `modules/ai_host/lambda.tf` packages only `src/handler.py`.

The ledger TTL attribute is `retention_expires_at_epoch`. Consumption markers must omit that attribute. Do not change the table TTL in this delta. TTL deletion is not a replay boundary.

Do not change reserved concurrency, `enable_ai_query_route`, the function URL, or the API Gateway invoke permission. `lambda:InvokeFunction` stays denied. Same-account invoke remains an unreviewed issuance control. The function environment keeps `AI_QUERY_ROUTE_ENABLED`, `PRODUCTION_ISSUANCE_AVAILABLE`, and `LEDGER_WRITES_ENABLED` false.

## What this delta still does not prove

Applying this delta would not open production execution. It would not establish account provenance, authenticated source evidence, or distributed exactly-once native execution. A later nonproduction test still has to run under the closed live gate, with injected credentials reviewed separately, and with production flags left false.
