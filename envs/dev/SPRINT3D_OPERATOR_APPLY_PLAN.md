# Sprint 3D operator apply plan — development infrastructure

This plan prepares restoration. It is not approval to apply.

No `terraform apply`, destroy, import, or state edit was run. The live AWS API was not read: `aws sts get-caller-identity --profile wilvor-dev` failed because the SSO token is expired. Account and survivor conclusions below come from local state files and `envs/dev/terraform.tfvars`.

## Bound target

| Item | Value |
| --- | --- |
| Profile | `wilvor-dev` |
| Region | `us-west-1` |
| Account | `396037508959` |
| Root | `envs/dev` |
| Backend | Local file `envs/dev/terraform.tfstate`. There is no remote backend block. |
| State lineage | `09a73f24-93cf-2fb7-b4cc-0e18519042f6` |
| Current serial | `26828` |
| Current managed resources | `0` |

`envs/dev/variables.tf` rejects any other region, profile, project, or environment. `terraform_data.development_account_guard` fails the plan when the caller account is not `396037508959`.

Do not copy `terraform.tfstate.backup` over the current state. The backup is the same lineage at serial `26321` and still lists 447 managed resources from before destroy. Restoring that file would make Terraform manage resources that were destroyed. Do not apply `envs/dev/plan.txt` or `envs/dev/tfplan.json`. Both are stale.

## What is still recorded

`envs/dev-historical-data/terraform.tfstate` is a different lineage (`5dbba575-327a-0735-6b61-b1e0d30d24fe`, serial 7). It still manages:

`wilvor-dev-historical-facts-396037508959-us-west-1`

That bucket is `force_destroy = false`. This sprint does not change that root. Do not run `dev-down` against `envs/dev-historical-data`.

`envs/dev` with `enable_historical_facts = true` and `enable_historical_analytics = true` only looks that bucket up. If a fresh SSO session shows the bucket is gone, stop. Do not apply `envs/dev` until the data-plane root is reconciled. Do not flip those flags in the same change as the AI host.

## Resources that must return

The empty dev state means the next reviewed plan should create the previous development estate again. The pre-destroy backup contained 447 managed instances and no AI route, no AI Lambda, and no execution ledger. Poller schedules stay off. Operational routes stay the existing GET routes. `POST /ai/query` was not in that state and must not appear in the new plan.

| Module | Role on restore |
| --- | --- |
| `aircraft_foundation` | Streams, current-state table, processors, OpenSky secret shell. Schedule stays false. |
| `weather_events` | Weather log group and dashboard. |
| `sigmet` | Hazard tables, processor, poller. Schedule stays false. |
| `metar`, `taf` | Weather pipelines. Schedules stay false. |
| `station_reference`, `runway_metadata` | Reference loaders. Schedules stay false. |
| `airport_status`, `projection`, `encounter`, `risk`, `airport_assessment`, `recommendations`, `active_alerts` | Existing event-driven processors and tables. |
| `operational_api` | HTTP API and its Lambda. `enable_ai_query_route = false`. The AI function ARN is not passed in. |
| `historical_facts`, `historical_analytics` | Recreatable transport and analytics that read the surviving facts bucket. |

Pre-destroy counts, for the review: 24 Lambda functions, 20 DynamoDB tables, 5 provisioned Kinesis streams, 7 S3 buckets in this root, 1 HTTP API, 16 GET routes, 147 alarms, 16 dashboards, 2 Firehose streams. Those counts do not include the new AI host resources below.

## New AI host resources

These are the only new addresses. They are isolated from `modules/operational_api`. The configuration graph has no edge between `module.ai_host` and `module.operational_api`.

| Address | Purpose |
| --- | --- |
| `module.ai_host.aws_dynamodb_table.ledger` | Table `wilvor_master_execution_ledger`, keys `pk` and `sk`, on-demand, TTL attribute `retention_expires_at_epoch`. No GSI and no stream. |
| `module.ai_host.aws_iam_role.host` | `wilvor-dev-ai-host-lambda-role`. Trust is `lambda.amazonaws.com` in this account and this function ARN only. |
| `module.ai_host.aws_iam_role_policy.host` | `GetItem` and `PutItem` on that table only. Explicit denies for Scan, Query, DeleteItem, UpdateItem, BatchWriteItem, TransactWriteItems, CreateTable, and PartiQL. Explicit deny of DynamoDB on every other resource. Explicit deny of Bedrock, secret reads, `lambda:InvokeFunction`, function URLs, and `execute-api:Invoke`. |
| `module.ai_host.aws_cloudwatch_log_group.host` | `/aws/lambda/wilvor-dev-ai-host`, retention 3 days. |
| `module.ai_host.aws_lambda_function.host` | `wilvor-dev-ai-host`, Python 3.12, 128 MB, 3 second timeout, reserved concurrency 0. Handler always returns `ai_host_invoke_closed`. It does not call AWS or a model. |
| `module.ai_host.aws_cloudwatch_metric_alarm.host_errors` | Lambda errors. Missing data does not alarm. |
| `module.ai_host.aws_cloudwatch_metric_alarm.host_throttles` | A throttle means something tried to invoke the closed function. |
| `module.ai_host.aws_cloudwatch_metric_alarm.ledger_system_errors` | DynamoDB system errors. Conditional check failures are not this alarm. |
| `terraform_data.development_account_guard` | State-only account guard. It creates no AWS resource. |

The deployed package sets `AI_QUERY_ROUTE_ENABLED`, `PRODUCTION_ISSUANCE_AVAILABLE`, and `LEDGER_WRITES_ENABLED` to `false`. The handler does not read those variables. There is no function URL and no `aws_lambda_permission`.

The ledger name is the Sprint 3C name. TTL deletion is not correctness and is not permission to start again. Point-in-time recovery is off, and deletion protection is off, so the existing `dev-down` path can still remove the table. The ledger lasts only as long as this disposable root stays applied.

## Cost

Restoring the old estate is the cost. Five provisioned Kinesis shards are about $0.015 per shard-hour, roughly $1.80 per day before payload charges, if the environment is left up. About 147 alarms are $0.10 each per month. Dashboards beyond the three free dashboards are $3 each per month. DynamoDB on-demand tables cost storage while idle. The historical facts bucket already exists and is not part of this apply.

The AI addition while idle is small: an empty on-demand table, one log group with a three-day retention, three alarms (about $0.30 per month if left up), and a Lambda that cannot run at concurrency 0. No customer-managed KMS key and no point-in-time recovery charge.

Destroy the dev root at the end of the session with the existing `dev-down` flow after the environment is no longer needed. Do not destroy the historical-data root.

## Sequence

1. Renew the session: `aws sso login --profile wilvor-dev`.
2. Confirm the caller. Stop unless the account is `396037508959` and the region in use is `us-west-1`.

```powershell
aws sts get-caller-identity --profile wilvor-dev --region us-west-1
```

3. Read survivors. These commands only describe or list. Stop if any `wilvor-dev` Lambda, table, stream, or API already exists while `envs/dev` state is empty. Do not import those resources in this change.

```powershell
aws s3api head-bucket --bucket wilvor-dev-historical-facts-396037508959-us-west-1 --profile wilvor-dev --region us-west-1
aws dynamodb describe-table --table-name wilvor_master_execution_ledger --profile wilvor-dev --region us-west-1
aws lambda get-function --function-name wilvor-dev-ai-host --profile wilvor-dev --region us-west-1
aws lambda list-functions --profile wilvor-dev --region us-west-1 --query "Functions[?starts_with(FunctionName, 'wilvor-dev')].FunctionName"
aws dynamodb list-tables --profile wilvor-dev --region us-west-1
aws apigatewayv2 get-apis --profile wilvor-dev --region us-west-1 --query "Items[].Name"
```

`ResourceNotFoundException` for the ledger and the AI function is the expected result. A successful `head-bucket` is required before planning `envs/dev`.

4. From `envs/dev`, with the current empty state left in place:

```powershell
terraform fmt -recursive -check
terraform validate
terraform plan -input=false -out=tfplan
```

5. Review `tfplan` before any apply. Require all of the following:

- Creates for the previous dev modules and the AI addresses listed above.
- No destroy and no replace.
- No action on `wilvor-dev-historical-facts-396037508959-us-west-1`.
- No create for `aws_apigatewayv2_route.ai_query`, `aws_apigatewayv2_integration.ai_query`, `aws_apigatewayv2_authorizer.ai_jwt`, or `aws_lambda_permission.ai_query`.
- `module.ai_host.aws_lambda_function.host` shows `reserved_concurrent_executions = 0`.
- The account in the plan is `396037508959`.

6. Stop. Apply only after a separate operator approval of that saved plan. `scripts/dev-up.ps1` applies a plan by itself, so do not run it until that approval. OpenSky secret restoration stays a separate operator step and was not run here.

## Operator approval required before any of these

- `terraform apply` of `envs/dev`, including `dev-up.ps1` and `dev-reset.ps1`.
- Importing a surviving resource into the empty dev state.
- Replacing `terraform.tfstate` with the backup.
- Applying or destroying `envs/dev-historical-data`.
- Setting `enable_ai_query_route`, `enable_invoke`, `ai_lambda_function_arn`, `ai_jwt_issuer`, or `ai_jwt_audience`.
- Replacing `modules/ai_host/src/handler.py` with a host that writes the ledger or calls a model.
- Removing the IAM denies for Bedrock, secrets, invoke, or the prohibited ledger APIs.
- Granting the AI role read access to specialist tables.
- Enabling a poller schedule or raising AI concurrency above 0.
- Point-in-time recovery or deletion protection on the ledger. Protection would block `dev-down`. A ledger that must survive daily teardown needs its own root, which this change does not create.
