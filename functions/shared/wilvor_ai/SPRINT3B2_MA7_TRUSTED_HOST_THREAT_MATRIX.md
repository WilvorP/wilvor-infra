# Sprint 3B.2 MA7 trusted-host threat matrix

Status: independent adversarial review of baseline `5d10c582091997732b97e496940af9fa18afd745` on `sprint3b2-ma7-security`. This document is not operational readiness, production authorization, source authentication, request-coverage certification, verified ingress, or a host grant.

Execution authority remains `DENIED`. Production authority remains `DENIED`. Source authenticity remains `NOT_ESTABLISHED`. Request coverage remains `NOT_ESTABLISHED`. No specialist runner, model provider, AWS API, Terraform plan, or endpoint was invoked. `run_synthetic_master_workflow` was not called.

Sprint 3B.2 Agents 1–11 are unmerged. Their modules were not imported. No failing placeholder tests were added for them. The assertions in the post-merge table become executable only after every one of those modules is integrated on one branch. Until then they are specifications, not expected failures on this branch.

## What the words mean on this baseline

| Standing | What it is here | What it is not |
|---|---|---|
| Descriptive | A structurally valid gateway assessment, `WITHIN_ALLOWLIST`, `structural_ready_to_execute`, scheduling `ELIGIBLE` or `COMPLETED`, a copied shared-envelope field | A principal, a grant, a ledger reservation, or a source read |
| Evaluated | `assess_master_gateway_identity`, `assess_master_execution_policy`, `check_master_read_resource`, readiness and bridge correlation | Permission to dispatch |
| Host-issued | Unavailable. `issue_application_principal` raises. `PRODUCTION_GATEWAY_ISSUANCE_AVAILABLE`, `PRODUCTION_PERMIT_ISSUER_INTEGRATED`, and `DISPATCH_GRANT_AVAILABLE` are false | A caller-built dataclass, a sealed test principal, or a policy-check object |
| Consumed | `LocalConsumptionResult` for `TRANSITION_RECORDED`, which is false in boolean context, and a fanout `MasterExecutionClaim` | A one-time production grant or a cross-worker reservation |
| Source-authenticated | Not available. Journal, Live Ops, Historical, and Decision results keep `NOT_ESTABLISHED` | A completed local fake, an empty page, a `VerifiedZeroClaim`, or a state-code string |

Shared envelope fields (`master_request_id`, `planning_session_id`, `step_id`, `invocation_id`, `specialist`, `purpose`, `capability`, `principal_ref`, `policy_revision`, `resource_scope`, `region_scope`, `historical_window`, `decision_target_scope`, `accepted_at_utc`, `deadline_utc`, `idempotency_key`, `max_model_turns`, `max_tool_calls`, `correlation_id`) are hints until a reviewed host establishes them. The application request clock and the source observation clock stay separate. Existing names are used: `MasterRouteExecutionAuthority`, `MasterProductionAuthority`, `MasterSourceAuthenticity`, `MasterRequestCoverage`, `MasterSpecialist`, `MasterPurpose`, `AgentCapability`.

Ceilings that remain authoritative: `MAX_ROUTE_STEPS` is 4, `MAX_DECISION_STEPS` is 1, the Historical interval ceiling is 7 days, and `server_read_budget`, `server_read_tools`, `server_read_argument_names`, and `server_read_resources` are the server catalogs. `ReadOperation` is `GET_ITEM`, `QUERY`, or `SCAN`. `SCAN` is denied. There is no write member. A catalog name is not a table grant.

## Baseline that was inspected

The nine Sprint 3B.1 reports reflected in `tests/contracts/test_sprint3b_ma7_execution_boundary.py` and `SPRINT3B_MA7_EXECUTION_THREAT_MATRIX.md` are the protected `POST /ai/query` route, `master_gateway_identity.py`, `master_execution_fanout.py`, `master_read_resource_guard.py`, `master_runtime_read_journal.py`, `master_live_ops_native_bridge.py`, `master_historical_native_bridge.py`, `master_decision_target_resolver.py`, and the combined-host threats. Those eight Python modules are on this baseline. This review executes them. It does not treat that execution as the future trusted host.

Also read and, where the new tests call them, executed: MA1 `master_contracts.py`; MA2 `authorize_route`; MA3 `SyntheticWorkerResult` only; MA4 `admit_master_evidence`; MA5 `evaluate_master_response_policy` and `render_master_response`; planning session, execution scope, execution policy, preflight, local capability, Live Ops readiness, and the Historical binding used by the Historical bridge.

`modules/operational_api/api.tf` and `variables.tf` were read and not planned or applied. `enable_ai_query_route` defaults to false. When the route exists, `authorization_type` is `JWT`, the route key is `POST /ai/query`, and `aws_lambda_permission.ai_query` sets `principal` to `apigateway.amazonaws.com` with `source_arn` bound to that route. CORS `allow_methods` includes `POST` only when the route flag is true, and `allow_credentials` is false. `envs/dev` was not found setting the flag. None of that file reading attests a deployed authorizer, a Lambda resource policy, or the IAM role of a function this branch does not create.

## Invariants locked on this branch

| # | Untrusted input | Baseline result | Test |
|---|---|---|---|
| 1 | Forged HTTP API v2 event, valid-looking JWT metadata, untrusted `Authorization` header | Assessment is false. Claims are structurally valid and `signature_verified` is false. Invocation provenance is `NOT_ATTESTED`. Ingress is `UNRESOLVED`. Principal standing is `NOT_ISSUED`. `issue_application_principal` and `promote_to_execution_permission` raise. Event `timeEpoch` is not the evaluation clock. | `test_forged_gateway_event_jwt_and_header_stay_unauthenticated` |
| 2 | Matching ids, a copied plan, a copied session, a reconstructed invocation | `reject_authentication_claim` stays unauthenticated. A second `authorize_route` is equal and not the same object, and authority stays `DENIED`. A session rebuilt with the same id and membership is `copied_session_identifier`. A field-equal invocation is `reconstructed_invocation_rejected`. The session is not an application principal. | `test_matching_ids_and_copied_plan_or_session_do_not_authorize` |
| 3 | Structural execution readiness and a fake approved policy | A Live Ops step can be `ready_to_execute` with gate `NOT_REQUIRED`. Scope, policy, preflight, and Live Ops readiness stay blocked. A candidate carrying `ALLOW`, `execution_permitted`, unrestricted tables, and region `California` is denied, including `POLICY_SPOOF_NOT_AUTHORITY`. Passing `execution_permitted=True` into the assessment is rejected. | `test_structural_readiness_and_fake_policy_stay_denied` |
| 4 | Allowlisted tool name | `search_current_hazards` can be `WITHIN_ALLOWLIST` for `QUERY` and region `CA`. `dispatch_permitted` stays false, boolean context raises, and `promote_read_check_to_dispatch_permit` raises. The same name with no sealed principal, a caller `ALLOW`, or `SCAN` is `DENIED`. `server_read_resources` does not pair that tool with `SCAN`. | `test_allowlisted_tool_name_is_not_a_read_or_scan_grant` |
| 5 | Local scheduling claim and process-local lock | `claim` then `complete` leaves `execution_authorized` and `evidence_verified` false. The scheduler lock is a `_thread.lock`. Two threads produce one claim and one `duplicate_claim`. A `threading.BoundedSemaphore` acquire does not change that. Local capability `TRANSITION_RECORDED` is false in boolean context and `distributed_production_authorization` stays false. | `test_local_claim_and_process_local_lock_are_not_distributed_authority` |
| 6 | Local fake journal, partial page, empty rows, claimed zero | A completed fake stays unauthenticated. A partial page records `PARTIAL_PAGINATION` and does not mint a zero. Empty rows record `EMPTY_ROWS_NOT_VERIFIED_ZERO`. A caller payload that says the zero was minted is `FORGED_CALLER_EVENT` and is not executed. | `test_local_fake_journal_partial_page_and_empty_rows_stay_unauthenticated` |
| 7 | User region and callsign | Route hint `California` is withheld from Live Ops selectors. `reject_unauthorized_selectors` rejects the region and the callsign. A callsign-only Decision hint is `UNTRUSTED_ROUTE_HINT` and selects no aircraft. Region label `California` is `REGION_LABEL_NOT_AUTHORITY` on the read guard. State code `CA` can be `WITHIN_ALLOWLIST` and still is not a dispatch permit or SIGMET coverage. | `test_user_region_and_callsign_do_not_authorize_a_target_or_a_read` |
| 8 | Missing or stale risk | A unique injected aircraft with no risk, or `RiskPresence.ABSENT`, is `MISSING_RISK_RECORD` with no level. `absent_risk_is_low_risk=True` is rejected. A stale row is `STALE_AIRCRAFT_STATE`. Neither is execution-eligible or authenticated risk evidence. | `test_missing_or_stale_risk_records_do_not_establish_a_target` |
| 9 | Source-less Historical result, partial page, empty rows, claimed `VerifiedZeroClaim` | A run with no tool results and a `VerifiedZeroClaim` stays caller-supplied, correlation-unresolved, and `verified_zero_admitted` false. A `PARTIAL` page is `PARTIAL_OR_TRUNCATED` and is not a zero. Empty `records` are `NO_ROWS` and are not a zero. The plan window is not copied into eligible tool arguments. | `test_sourceless_historical_partial_empty_and_claimed_zero_stay_unadmitted` |
| 10 | Geospatial state string | `LiveOpsRegionalStringMatch` for `California` / `CA` / `SIGMET` keeps `sigmet_geographic_coverage` false. Setting that flag true raises `geospatial_coverage_not_established`. The prepared bridge keeps both coverage flags false and does not invoke the specialist. | `test_geospatial_state_string_is_not_sigmet_coverage` |
| 11 | Synthetic MA3 result and an MA5 factual proposal | `SYNTHETIC_COMPLETED` is rejected as `UNSUPPORTED_ARTIFACT` and cannot enter the evidence bundle. A Historical answer naming an aircraft, a SIGMET, a risk level, and a zero renders a fixed sentence that does not contain that answer. | `test_synthetic_ma3_and_ma5_factual_proposal_stay_closed` |

`MasterRouteExecutionAuthority` has no granted member. `MasterRequestCoverage` has only `NOT_ESTABLISHED`.

## Boolean-context gap, reported rather than edited

These public results do not define `__bool__`: `MasterExecutionClaim`, `MasterSchedulingObservation`, `MasterExecutionPolicyAssessment`, `MasterLiveOpsNativeBridge`, `MasterHistoricalNativeBridgeEnvelope`, and `MasterDecisionTargetResolution`. A caller that branches on the object itself does not read `execution_authorized`, `dispatch_permitted`, or `source_authenticity`. The closed fields on those objects stay denied. This branch does not own those modules, so it does not add `__bool__`.

Required migration after integration, in the owning modules: make each of those results false in boolean context, or raise, the way `MasterGatewayIdentityAssessment`, `MasterReadResourceCheck`, `RuntimeReadJournalEntry`, and `LocalConsumptionResult` already do. The trusted host must branch on the closed fields. A descriptive `WITHIN_ALLOWLIST` must not become a consumed grant.

## Post-merge specifications for Agents 1–11

Add these tests only after Agents 1–11 are integrated together. Do not add them as failing tests while any of those modules is absent. Every integrated test must keep execution authority `DENIED`, production authority `DENIED`, source authenticity `NOT_ESTABLISHED`, and request coverage `NOT_ESTABLISHED` until a separately reviewed host grant exists. A descriptive allow from Agent 3 must not be callable as a grant from Agent 4.

| Agent | Module under test after integration | Assertion that becomes executable only after all of Agents 1–11 are integrated | What remains an infrastructure or deployment review, not an offline proof | Baseline that must still hold |
|---|---|---|---|---|
| 1 Gateway invocation trust | `master_gateway_runtime_boundary.py` | `mixed_trust_event_cannot_satisfy_host_invocation_provenance`: a forged v2 event whose body, header, and `requestContext.authorizer.jwt` match the server issuer, audience, and `POST /ai/query` is admitted only as an untrusted observation. It does not call Agent 2 issuance, Agent 4 grant issue, or Agent 8 execution. | Who can call `lambda:InvokeFunction` on the AI function, including a direct invoke that never passed API Gateway. The route flag, JWT authorizer attachment, and `source_arn` on `aws_lambda_permission.ai_query` are Terraform and account-policy review. Offline parsing cannot prove the deployed authorizer ran. | Invariant 1 |
| 2 Application principal | `master_application_principal.py` | `principal_issuance_requires_host_provenance`: the only object Agents 3, 4, and 7 will accept is a principal Agent 2 minted from an Agent 1 observation plus an independent host identity input. Issuer, audience, route, and role on a caller dataclass, a copied `sub`, or a gateway assessment are not that object. | The identity provider, JWKS, and the human review that the Lambda resource policy admits only the intended API Gateway source. Role claims in a token are not an IAM role. | Invariants 1 and 2 |
| 3 Runtime policy | `master_runtime_policy.py` | `evaluated_allow_is_not_a_grant_input`: a deterministic allow for specialist, purpose, capability, and resource scope is a descriptive evaluation. Agent 4 rejects it unless Agent 2's principal and Agent 1's unresolved-ingress gate are both present and the ingress gate is still unresolved, so no production grant is issued. Issuer, audience, route, and role mismatches deny the evaluation. | The production policy source, its revision, and the IAM actions attached to the Lambda role. A unit-test allow fixture is not that revision. | Invariants 3 and 4 |
| 4 Execution grants | `master_execution_grants.py` | `one_time_grant_is_bound_and_not_replayable`: a grant is issued only for one retained invocation, one Agent 2 principal, one Agent 3 evaluation, and one resource scope. A second issue, a copied grant, a scheduler claim, and a `WITHIN_ALLOWLIST` check do not mint another. The grant is not a source receipt. | KMS or storage used to hold grant state, and the Lambda role that would be allowed to write it. This branch has no such store. | Invariants 3, 4, and 5 |
| 5 Execution ledger | `master_execution_ledger.py` | `ledger_reserve_once_is_single_winner_across_workers`: two workers calling `reserve_once` for one idempotency key produce one reservation. Crash recovery of `mark_started` without `complete` does not become success, a second dispatch, or a verified zero. The process-local fanout lock is not accepted as the ledger. | Conditional writes, transaction conflicts, and TTL on the real table. An in-memory port can show the state machine and cannot show cross-process atomicity. | Invariant 5 |
| 6 Runtime budget | `master_runtime_budget.py` | `timeout_and_cancellation_stop_tool_execution`: deadline, cancellation, and `server_read_budget` are enforced on the execution side of Agent 8, not only while parsing the request. A timed-out or cancelled attempt cannot be completed by Agent 5 or rendered as success by Agent 11. Request-deadline clocks stay distinct from source-observation clocks. | Lambda maximum duration, API Gateway's 29000 ms integration timeout, and clock synchronization in the account. An injected test clock is not those controls. | Invariants 5 and 6 |
| 7 Authorized scope | `master_authorized_scope.py` | `effective_scope_is_the_intersection_not_the_hint`: region, Historical window, and Decision target are the intersection of request hints, Agent 2 entitlements, and an approved population. A window above 7 days, a caller region name, and a callsign do not become the effective scope. The Historical plan window is not copied into tool arguments. | The monitored-entity population and the approved US region set as deployed data. A sealed `frozenset({"CA"})` in a unit test is not that population. | Invariants 7 and 8 |
| 8 Trusted read executor | `master_trusted_read_executor.py` | `executor_rejects_scans_writes_and_unproven_grants`: execution requires the Agent 4 grant, Agent 7 scope, and Agent 6 budget. `SCAN`, a write-shaped operation, an unlisted table, and a tool name that is only allowlisted are not executed. Cancellation is checked at the adapter call. A returned page is not a receipt. | The Lambda role's DynamoDB and Athena actions. Offline denial of `SCAN` does not prove the deployed role lacks `dynamodb:Scan` or a write action. | Invariant 4 |
| 9 Scheduler adapter | `master_authorized_scheduler_adapter.py` | `scheduling_claim_does_not_dispatch`: Agent 9 keeps the retained plan, step, and invocation identity and calls Agent 8 only with an Agent 4 grant and an Agent 5 reservation. `ELIGIBLE`, `IN_PROGRESS`, and `COMPLETED` do not dispatch. A synthetic MA3 result is not a specialist result. | Worker concurrency and retry configuration of the deployed function. The adapter's injected port is not that fleet. | Invariants 5 and 11 |
| 10 Read receipt and MA4 | `master_authenticated_read_receipt.py` | `receipt_requires_a_completed_source_operation`: a receipt exists only for an Agent 8 completion with source identity, query identity, source version where the adapter has one, observation time, pagination, and the Agent 7 scope. A source-less Historical result, a partial page, empty rows, a missing provenance field, and a caller `VerifiedZeroClaim` do not become the receipt and do not enter MA4 as admitted evidence. | The adapter's actual query against the operational store, including SIGMET geometry. A fake row with `rows_returned=0` is not that query. | Invariants 6 and 9 |
| 11 Specialist runtime contract | `master_specialist_runtime_contract.py` | `evidence_reaches_ma5_only_through_the_host_contract`: Live Ops, Historical, and Decision proposals stay unauthenticated until Agent 10 accepts them. A state string is not SIGMET geometry. A Decision target is not selected from a callsign or a missing risk row. MA5 renders a fixed sentence when the receipt is absent. The contract does not invoke a model. | Provider credentials, network egress, and the deployed decision to enable a specialist. A contract object is not that enablement. | Invariants 7, 8, 10, and 11 |

Combined assertion, also executable only after all eleven modules are integrated: `forged_complete_zero_cannot_cross_grant_ledger_executor_and_renderer`. One request carries a matching gateway event, an `ALLOW` policy, an allowlisted tool, a scheduling claim, a process-local lock, a fake journal page, region `California`, callsign text, a missing risk row, an empty Historical page, a `VerifiedZeroClaim`, a state-code string, a synthetic MA3 result, and a factual MA5 proposal. The integrated host still denies execution, issues no principal and no grant, records no source authenticity, and renders no aircraft, SIGMET, risk, or zero.

## Deployment and CI review that this branch does not prove

These stay reviews of Terraform, the account, and the pipeline. They are not pytest results.

| Review | What to inspect | Why an offline test cannot close it |
|---|---|---|
| Authenticated route off by default | `enable_ai_query_route` is false unless a reviewed change sets it. `authorization_type` is `JWT` whenever the route count is 1. CORS does not add `POST` while the flag is false. `allow_credentials` stays false. | Reading `api.tf` does not query the deployed API. |
| Invoke boundary | `lambda:InvokeFunction` for the AI function is limited to `apigateway.amazonaws.com` and `local.ai_query_source_arn`. No other statement grants a direct invoke. | A forged event in a unit test never asks AWS who the resource policy allows. |
| CI | The pipeline fails if `envs/dev` enables the route, widens the invoke permission, or attaches `dynamodb:Scan` or a write action to the AI role before the host grant exists. | This branch does not run CI and does not plan Terraform. |
| IAM versus the allowlist | The function role, if it is ever created, allows only the keyed reads named by `server_read_resources` for the approved tables. It does not allow `Scan`, `PutItem`, `UpdateItem`, `DeleteItem`, or arbitrary Athena SQL. | `check_master_read_resource` does not read IAM. |

## Coverage gaps that remain after the baseline tests

- No host-issued principal, one-time grant, durable ledger, execution budget, effective scope, read executor, scheduler adapter, source receipt, or specialist runtime contract exists on this branch. The post-merge rows are specifications.
- `WITHIN_ALLOWLIST` and `structural_ready_to_execute` are descriptive. They are not grants. The boolean-context gap above is unfixed because those types are outside this agent's files.
- The fanout lock and the local-capability lock are process-local. They do not prove two Lambda environments.
- A sealed read-guard principal and a sealed region code are test seams. Production input cannot mint them, and they still do not dispatch.
- Empty rows, a partial page, and a `VerifiedZeroClaim` are classified and not admitted. This branch cannot show a real query's pagination token or source version.
- Region `CA` and the word `California` are not compared with a SIGMET polygon. Geographic verification waits on an approved geometry adapter and Agent 10.
- API Gateway JWT validation remains an edge control. Direct invoke of a Lambda is an IAM review.
- MA5 has no answer field. A later renderer that interpolates `SpecialistResult.answer` would leave this baseline.

## Import isolation

`tests/contracts/test_sprint3b2_ma7_trusted_host_import_isolation.py` parses the new boundary tests and imports them in a fresh interpreter.

Modules that must not load:

- `wilvor_ai.master_gateway_runtime_boundary`
- `wilvor_ai.master_application_principal`
- `wilvor_ai.master_runtime_policy`
- `wilvor_ai.master_execution_grants`
- `wilvor_ai.master_execution_ledger`
- `wilvor_ai.master_runtime_budget`
- `wilvor_ai.master_authorized_scope`
- `wilvor_ai.master_trusted_read_executor`
- `wilvor_ai.master_authorized_scheduler_adapter`
- `wilvor_ai.master_authenticated_read_receipt`
- `wilvor_ai.master_specialist_runtime_contract`
- `boto3`, `botocore`, `anthropic`, `openai`
- `wilvor_ai.historical_specialist`, `wilvor_ai.decision_specialist`, `wilvor_ai.live_ops`, `wilvor_ai.live_ops_adapter`
- `wilvor_operational`

The protected route is Terraform, not a Python import. Importing `SyntheticWorkerResult` loads the existing MA3 module and therefore LangGraph. That import does not run the graph.

## Independently observed results

Command, from the `sprint3b2-ma7-security` worktree, using `wilvor-infra/.venv`:

`python -m pytest tests/contracts/test_sprint3b2_ma7_trusted_host_boundary.py tests/contracts/test_sprint3b2_ma7_trusted_host_import_isolation.py tests/contracts/test_sprint3b_ma7_execution_boundary.py tests/contracts/test_sprint3b_ma7_execution_import_isolation.py tests/contracts/test_sprint3a_ma7_safety_regression.py tests/contracts/test_sprint3a_ma7_safety_import_isolation.py tests/contracts/test_ma7_master_safety_matrix.py tests/contracts/test_ma7_master_safety_import_isolation.py -q -p no:cacheprovider`

Result: 85 passed.

HEAD remained `5d10c582091997732b97e496940af9fa18afd745` on `sprint3b2-ma7-security`. No commit was created.

## File scope

Created, and left uncommitted:

- `functions/shared/wilvor_ai/SPRINT3B2_MA7_TRUSTED_HOST_THREAT_MATRIX.md`
- `tests/contracts/test_sprint3b2_ma7_trusted_host_boundary.py`
- `tests/contracts/test_sprint3b2_ma7_trusted_host_import_isolation.py`

No existing Wilvor source module or test file was modified.
