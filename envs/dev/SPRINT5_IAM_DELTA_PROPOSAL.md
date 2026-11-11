# Sprint 5 IAM delta — PROPOSED, NOT APPLIED

Account: `396037508959`  
Region: `us-west-1`  
Role: `wilvor-dev-ai-host-lambda-role`  
Status: **PROPOSED_NOT_APPLIED**

This file is an operator review artifact. It does not apply Terraform, attach
policies, or mutate IAM. Do not run `terraform apply` until this exact plan is
approved.

## Current deployed host policy

Inspected from `modules/ai_host/iam.tf` and `envs/dev/main.tf`:

- Ledger `wilvor_master_execution_ledger`: `GetItem`, `PutItem`, `TransactWriteItems`
- Specialist GetItem only: `wilvor-dev-aircraft-current-state`
- Query and Scan denied on reviewed GetItem tables, including the aircraft callsign GSI
- Unreviewed DynamoDB denied
- `bedrock:*` denied
- `enable_invoke = false`

## Proposed statements (exact resources)

No wildcards. No Scan. No model-driven table or SQL selection. Bedrock stays
out of this delta.

### 1. Additional GetItem tables

`dynamodb:GetItem` on exact table ARNs:

- `arn:aws:dynamodb:us-west-1:396037508959:table/wilvor-dev-aircraft-current-state` (already attached)
- `arn:aws:dynamodb:us-west-1:396037508959:table/wilvor-dev-active-hazards`
- `arn:aws:dynamodb:us-west-1:396037508959:table/wilvor-dev-aircraft-hazard-encounter`
- `arn:aws:dynamodb:us-west-1:396037508959:table/wilvor-dev-airport-status`
- `arn:aws:dynamodb:us-west-1:396037508959:table/wilvor-dev-metar-latest`
- `arn:aws:dynamodb:us-west-1:396037508959:table/wilvor-dev-taf-latest`
- `arn:aws:dynamodb:us-west-1:396037508959:table/wilvor-dev-aircraft-projection`
- `arn:aws:dynamodb:us-west-1:396037508959:table/wilvor-dev-risk-results`
- `arn:aws:dynamodb:us-west-1:396037508959:table/wilvor-dev-recommendations`
- `arn:aws:dynamodb:us-west-1:396037508959:table/wilvor-dev-airport-assessment`
- `arn:aws:dynamodb:us-west-1:396037508959:table/wilvor-dev-active-alerts`

### 2. Live Ops bounded Query

`dynamodb:Query` on:

- `.../table/wilvor-dev-active-hazards/index/status-valid_to_epoch-index`
- `.../table/wilvor-dev-hazard-coordinates` (base-table hash/range Query)
- `.../table/wilvor-dev-aircraft-hazard-encounter/index/aircraft_id-detected_at_epoch-index`
- `.../table/wilvor-dev-aircraft-hazard-encounter/index/hazard_id-detected_at_epoch-index`

Callsign GSI `callsign-position_time_epoch-index` stays omitted until
`find_aircraft_by_callsign` is separately authorized.

### 3. Decision bounded Query (Scan replacement)

`dynamodb:Query` on:

- `.../table/wilvor-dev-aircraft-projection/index/aircraft_id-generated_at_epoch-index`
- `.../table/wilvor-dev-aircraft-hazard-encounter/index/aircraft_id-detected_at_epoch-index`
- `.../table/wilvor-dev-risk-results/index/aircraft_id-generated_at_epoch-index`
- `.../table/wilvor-dev-recommendations/index/aircraft_id-created_at_epoch-index`
- `.../table/wilvor-dev-active-alerts/index/aircraft_id-updated_at_epoch-index`
- `.../table/wilvor-dev-airport-assessment` (base table, hash `evaluation_id`, range `airport_id`)

The airport-assessment GSI `airport_id-created_at_epoch-index` is not granted.
Persisted Decision evidence calls `query_airport_assessments_for_evaluation`,
which queries the base table by `evaluation_id`. The GSI is used by the
operational API, not by the AI host.

Page limit remains 25. Empty pages are not verified zeros.

### 3a. Deny carve-out required for these Allows to take effect

Deployed `DenyUnreviewedDynamoDb` is `dynamodb:*` on every resource except the
ledger and `wilvor-dev-aircraft-current-state`. An index ARN does not match
the parent table ARN. Adding Allow statements without adding the same exact
ARNs to `not_resources` leaves the Allows overridden.

`DenyReviewedGetItemTableWrites` denies `dynamodb:Query` on every
GetItem-only base table. `wilvor-dev-airport-assessment` is therefore not in
that list. It has its own allow for GetItem and Query, and its own deny for
Scan and writes. `wilvor-dev-hazard-coordinates` allows Query only.

### 4. Historical Athena — attach existing unattached policy

Attach `wilvor-dev-historical-analytics-query` to the AI host role.

Source update after this proposal was written: `envs/dev/main.tf` contains `aws_iam_role_policy_attachment.ai_host_historical_analytics_query`. `modules/historical_analytics` still does not attach the policy. The dev-root attachment uses `module.ai_host.role_name` while `enable_invoke = false`. This file does not record an AWS apply. The attachment is not an Agent API and is not production execution.

Do **not** grant `dynamodb:Query` on historical DynamoDB tables as a substitute.

### 5. Ledger — no change

Keep GetItem / PutItem / TransactWriteItems on `wilvor_master_execution_ledger` only.

### 6. Bedrock — separately gated

Do not add `bedrock:Converse` or `bedrock:InvokeModel` in this delta.

## Must not grant

- `dynamodb:Scan`
- `dynamodb:*`
- wildcard table or index ARNs
- Bedrock, Secrets Manager reads, `lambda:InvokeFunction`, function URLs, `execute-api:Invoke`
- `enable_invoke = true`
- `POST /ai/query`

## Expected cost / risk if applied later

- IAM widening of the closed AI host role in nonproduction
- No Lambda invoke enablement, so no runtime traffic from this apply alone
- Athena policy attach does not start queries until a later approved read window

## Approval required

Do not apply this plan without an explicit written approval of these exact
statements.
