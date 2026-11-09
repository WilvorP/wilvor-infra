# Execution-role read-only probe window

This procedure is the review copy for one read-only deployment. `open_window` still raises, and the source file keeps `PROBE_LIVE_CALLS_ENABLED = False`. The only armed handler is `wilvor-dev-ai-host-execution-role-readonly-probe-live.zip`. Running the commands below is a separate approval. This task does not change AWS.

The executable specification is `execution_role_probe_window.py`. The probe source is `execution_role_probe.py`. The packager is `package_execution_role_probe.py`. None of those files is `modules/ai_host/src/handler.py`.

## What the window proves

One unqualified invoke of `wilvor-dev-ai-host` runs under the session Lambda injects for `wilvor-dev-ai-host-lambda-role`. The handler calls `sts:GetCallerIdentity` and issues exactly one `GetItem` through `AuthorizedReadDynamoClient`:

- Account `396037508959`
- Region `us-west-1`
- Assumed-role ARN for role `wilvor-dev-ai-host-lambda-role` in that account
- UserId prefix `AROA`, with the UserId suffix equal to the ARN session suffix
- The session suffix is recorded. It is not required to equal `wilvor-dev-ai-host`, because the Lambda execution-role guide does not document RoleSessionName
- Table `wilvor-dev-aircraft-current-state`
- Key `aircraft_id = WILVOR-SPRINT3D-READONLY-PROBE`
- `ConsistentRead = true`

A response without `Item` is an absent observation. It is not a verified zero, not verified low risk, and not evidence that the aircraft is absent. The response body does not contain item attributes. The probe does not call Bedrock, does not consume a grant, and does not write the ledger. The response keeps `production_authorized` false, `trust_policy_retained` true, and `trust_execution_blocker` false. A missing item is not a verified zero.

## Why the session is the execution role

Lambda assumes `wilvor-dev-ai-host-lambda-role` when it runs the function and injects `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN`. Customers cannot override those three names in the function environment. The probe requires `AWS_LAMBDA_FUNCTION_NAME=wilvor-dev-ai-host`, requires those three variables to be present, and refuses `AWS_PROFILE`, `AWS_ROLE_ARN`, a web-identity token file, and container-credential URIs. After that check it constructs STS and DynamoDB clients with the default SDK chain and region `us-west-1`. It does not copy the keys into the client constructor, and it does not call AssumeRole.

The current trust policy admits `sts:AssumeRole` only for `lambda.amazonaws.com`, and only when `aws:SourceAccount` is `396037508959` and `aws:SourceArn` is `arn:aws:lambda:us-west-1:396037508959:function:wilvor-dev-ai-host`. An operator is not that principal, so the operator cannot assume the role. `GetCallerIdentity` on the operator profile returns a different ARN. The probe also refuses an IAM user, a bare IAM role ARN, and any other role name.

## Trust policy

`modules/ai_host/iam.tf` stays unchanged. A closed invoke of `wilvor-dev-ai-host` already succeeded under the current trust policy: Lambda HTTP 200, handler 503 `ai_host_invoke_closed`, no `FunctionError`, reserved concurrency restored to 0, and the original `CodeSha256` unchanged. The `aws:SourceAccount` and `aws:SourceArn` conditions stay. Do not remove them, do not add an operator principal, and do not add a wildcard.

The condition matches the unqualified function ARN only. Invoke with no `--qualifier` and no `--publish`.

## Controls the role cannot perform

The identity policy allows `dynamodb:GetItem` on the aircraft table. It does not allow `dynamodb:DescribeTable` or `iam:SimulatePrincipalPolicy`. The probe therefore does not call either API and does not report `table_described_by_execution_role` or `iam_simulated_by_execution_role`.

Before any code update, rerun the existing operator read-only inspection against `wilvor-dev`. Abort unless all of these still hold:

- Aircraft table name `wilvor-dev-aircraft-current-state`, status `ACTIVE`, hash key `aircraft_id`
- Indexes `current_h3_cell-position_time_epoch-index` and `callsign-position_time_epoch-index` still exist and are not read by this probe
- The 11 expected IAM simulations still match, including `GetItem` allowed on that table and `Scan`, `Query`, `PutItem`, `UpdateItem`, `DeleteItem`, and `TransactWriteItems` denied on it
- Reserved concurrency is 0
- `POST /ai/query` is absent
- The resource policy is absent

The sealed mapping inside the probe is the reviewed constant. The Lambda event cannot replace the table name or the key.

## Package

The closed zip, when one is needed for comparison, comes from `build_isolated_zip`. The deployment artifact comes only from `build_live_test_zip`. Its file name is `wilvor-dev-ai-host-execution-role-readonly-probe-live.zip`. The builder flips `PROBE_LIVE_CALLS_ENABLED` only inside that zip's `handler.py` and writes a manifest beside it. The source file remains false. Both builders refuse:

- `modules/ai_host/dist/ai_host.zip`
- `modules/ai_host/src/handler.py`
- `functions/aircraft_current_state_writer/dist/aircraft_current_state_writer.zip`
- `functions/aircraft_raw_processor/dist/aircraft_raw_processor.zip`
- `functions/opensky_poller/dist/opensky_poller.zip`

The zip contains `handler.py` copied from `execution_role_probe.py` plus the `wilvor_ai` modules that the live path imports. The closed handler returns before that import. `boto3` stays in the Python 3.12 runtime. The deployed handler setting remains `handler.lambda_handler`, so `lambda.tf` stays unchanged.

Importing Agent C pulls the scoped-read graph, including historical modules. That import happens on the live path. The Task 2C diagnostic host timeout is 30 seconds and memory is 256 MB. Reserved concurrency stays 0 until the approved window. Production AI flags stay false. A timeout is a failed probe, followed by restore. The constant-false package does not import Agent C while returning 503. Flushed `WILVOR_PROBE_DIAG` checkpoints record handler entry, context validation, Agent C import, STS, GetItem, and completion. They do not log credentials.

Copy the live zip and its manifest into an evidence directory whose path contains no spaces. Recompute the SHA-256 and the Lambda `CodeSha256` (standard base64 of that SHA-256) and compare both with the manifest. Abort on mismatch. The source probe file must still contain `PROBE_LIVE_CALLS_ENABLED = False`.

## Same-account invoke

A resource-based `lambda:InvokeFunction` permission is not required for a same-account caller whose identity policy already Allows `lambda:InvokeFunction` on `arn:aws:lambda:us-west-1:396037508959:function:wilvor-dev-ai-host` exactly.

`review_operator_invoke` still requires that exact Allow. A wildcard is not treated as that Allow. The November 7 preflight found the current operator is the SSO role `AWSReservedSSO_WilvorDevAdministrator_258d65e4509aa753` with only AWS managed `AdministratorAccess` (`Action: *`, `Resource: *`). IAM simulation of invoke is allowed through that wildcard. That is not the reviewed exact statement.

Two remaining paths, compared and not applied here:

A. One-time administrator exception for this isolated development probe only. The existing administrator session can already invoke. Do not attach a redundant exact `lambda:InvokeFunction` Allow onto that AdministratorAccess role. The exception, if later approved out of band, is procedural: one `RequestResponse` invoke, reserved concurrency 1, no CLI retry, immediate restore. It does not make `AdministratorAccess` a standing AI-host invoke grant.

B. A separate least-privileged operator identity whose identity policy Allows `lambda:InvokeFunction` on that function ARN exactly, with no asterisk, and whose window-driving APIs are reviewed separately. This is the standing control for any later live AI work.

Recommended for this Task 2C probe only: path A. Path B remains the standing requirement for subsequent live work. This plan does not create identities, attach policies, or call `lambda:AddPermission`. `refuse_redundant_administrator_exact_allow` stays closed.

## Order

Profile `wilvor-dev`, region `us-west-1`, function `wilvor-dev-ai-host`. Timeout is 30 seconds. Memory is 256 MB. Environment stays `AI_QUERY_ROUTE_ENABLED=false`, `PRODUCTION_ISSUANCE_AVAILABLE=false`, and `LEDGER_WRITES_ENABLED=false`. This window does not change those variables.

1. Record `get-function`, `get-function-configuration`, and `get-function-concurrency`. Save the code SHA-256, handler, runtime, timeout, role ARN, environment, and concurrency. Confirm concurrency is 0, the handler is `handler.lambda_handler`, the runtime is `python3.12`, the timeout is 30, and memory is 256.
2. Download the current package to an evidence directory that is not one of the refused zip paths. Base64-encode the SHA-256 of those bytes and compare it with `CodeSha256`. Abort on mismatch.
3. Confirm the resource policy is absent (`ResourceNotFoundException` from `get-policy`) and `POST /ai/query` is absent on `wilvor-dev-operational-api`.
4. Rerun the operator schema and IAM checks. Abort on drift.
5. Review the same-account invoke Allow. Abort when it is missing or wildcarded.
6. While concurrency is still 0, `update-function-code` with the evidence copy of `wilvor-dev-ai-host-execution-role-readonly-probe-live.zip`. Then `lambda wait function-updated`. Read `CodeSha256` again and compare it with the manifest. Abort unless `LastUpdateStatus` is `Successful` and the SHA matches. Do not raise concurrency while the update is `InProgress`.
7. `put-function-concurrency --reserved-concurrent-executions 1`. Do not use 2. Do not delete the concurrency setting; deleting it removes the reservation and opens the account pool.
8. Invoke once, unqualified. The CLI read timeout must exceed the 30-second Lambda timeout so a socket timeout cannot fire while the function is still running. `AWS_MAX_ATTEMPTS=1` so the CLI cannot retry into a second invoke. On Windows PowerShell set `$env:AWS_MAX_ATTEMPTS='1'` before the same `aws` command:

```text
AWS_MAX_ATTEMPTS=1 aws --profile wilvor-dev --region us-west-1 lambda invoke --function-name wilvor-dev-ai-host --invocation-type RequestResponse --cli-binary-format raw-in-base64-out --cli-read-timeout 40 --payload file://allowlisted-event.json probe-response.json
```

The payload file is exactly:

```json
{"schema":"wilvor.ai.execution_role_probe.request.v1","operation":"getitem_aircraft_probe","aircraft_id":"WILVOR-SPRINT3D-READONLY-PROBE"}
```

`--cli-binary-format raw-in-base64-out` is required so the handler receives a JSON object. A base64 string is an unsupported event and still consumes the one invoke. Do not retry.

9. Immediately run the restore commands below, including after a successful response. Maximum Lambda invokes in the window: 1. Inside that invoke the probe forwards exactly one `GetItem`. A second `GetItem` is refused. The DynamoDB and STS client read timeout is 1 second and botocore `total_max_attempts` is 1, so the initial GetItem is the only network attempt. `retries.max_attempts` is not used; that key counts extra retries after the first call. No Scan, Query, PutItem, UpdateItem, DeleteItem, or TransactWriteItems is part of the probe.

`UpdateFunctionCode` and `PutFunctionConcurrency` are outside Terraform. Until restore finishes, the function code and reserved concurrency drift from `enable_invoke = false` and from the `handler.py` archive. Do not run `terraform apply` to clear that drift. An apply during a partial window can replace the code or concurrency while the backup restore is still outstanding. Restore first, then compare the SHA. A later apply is a separate change, and only after the restored SHA matches the backup.

## Evidence

CloudWatch log group `/aws/lambda/wilvor-dev-ai-host`. Keep the one request id and the response body. The body must show the assumed-role ARN, the recorded session suffix, `get_item_calls` of 1, and the closed safety flags. Do not log credentials or the DynamoDB item. A timeout or an init error in CloudWatch is a failed probe, not a successful read.

CloudTrail management events can show `UpdateFunctionCode`, `PutFunctionConcurrency`, and `AssumeRole` by `lambda.amazonaws.com` when management events are enabled in `us-west-1`. `lambda:Invoke` and `dynamodb:GetItem` are data events. A trail that does not select those data events will not contain them. `GetCallerIdentity` is not CloudTrail evidence. This window does not call `put-event-selectors`. Absence of a data event is not proof about the read. The handler response and the CloudWatch request id are the read evidence. Record the trail's selectors so the limitation is explicit.

## Restore

A `finally` block does not run when the process is killed, the shell closes, or the network drops. The restore commands are the recovery mechanism. They are idempotent. If `UpdateFunctionCode` returns a conflict because a previous update is still running, wait and run the restore commands again from the first command. Do not invoke again.

1. `put-function-concurrency --reserved-concurrent-executions 0`
2. `update-function-code` with the backup zip from step 2. Omit `--publish`.
3. `lambda wait function-updated`.
4. Read `CodeSha256` in a separate command and compare it with the SHA captured in step 1. The comparison happens after the waiter returns, not only inside the same process.
5. Read concurrency and confirm it is 0.
6. Read the environment and confirm the three production flags are still false.
7. Read the operational API routes and confirm `POST /ai/query` is absent.
8. Confirm the resource policy is still absent.

Do not run `terraform apply`, `dev-up`, or `dev-down` as the restore. Leave the namespaced ledger transaction unsent. The live probe constant stays false in this revision.
