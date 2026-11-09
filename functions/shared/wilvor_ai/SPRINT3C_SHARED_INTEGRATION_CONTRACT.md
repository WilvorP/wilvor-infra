# Sprint 3C shared integration contract

Schema: `wilvor.ai.sprint3c.shared_integration_contract.v1`

This is the freeze for five parallel implementation agents. It reuses the Sprint 3B.2 types. It does not add a second framework.

Expected baseline: `02d67911b69182d738b8c97c24b4093f3702bb7f` on `ai-operations-copilot`. This pass inspected the working tree and did not run Git. Sprint 3B.2 / 3B.2X offline coverage, including the cross-agent end-to-end tests, is the promoted behavior. Passing those tests does not make any seam production-ready.

`POST /ai/query` stays absent. `modules/operational_api/variables.tf` keeps `enable_ai_query_route` default `false`. Terraform, AWS, model APIs, and the closed `PRODUCTION_*` constants stay untouched during the parallel phase.

## 1. Verified current architecture

The host is a chain of sealed offline evaluators. Each public production entry point raises or returns a denial. Local harness success stays local.

| Stage | Module | Current authority | Production entry that stays closed |
| --- | --- | --- | --- |
| Gateway parse | `master_gateway_identity` | `assess_master_gateway_identity` records an HTTP API v2 document. `InvocationProvenanceStanding` is `NOT_ATTESTED`. | `issue_application_principal` raises `ingress_attestation_unresolved` and `application_principal_not_issued`. `PRODUCTION_GATEWAY_ISSUANCE_AVAILABLE` is false. |
| Deployed route boundary | `master_gateway_runtime_boundary` | `assess_gateway_runtime_boundary(manifest, event=None, *, lambda_context=None, trusted=None)` checks a server-owned manifest. Event, Lambda context, and `trusted` never select issuer, audience, account, or invoke source. | `refuse_runtime_admission` raises `runtime_admission_not_attested` and `production_issuance_unavailable`. `RUNTIME_BOUNDARY_ISSUANCE_AVAILABLE` is false. |
| Application principal | `master_application_principal` | `NonproductionPrincipalProofPort.admit` mints `ServerOwnedPrincipalDescriptor` with `record_kind=SYNTHETIC_TEST_ADMISSION`, `production_principal=False`, `execution_permitted=False`. | `issue_production_application_principal` raises `production_principal_issuance_unavailable` and `reviewed_host_authority_not_wired`. |
| Runtime policy | `master_runtime_policy` | `evaluate_master_runtime_policy(...)` calls the production origin and returns `MasterRuntimePolicyDecision.DENY` with `PolicyAuthorityOrigin.NOT_ESTABLISHED` and `principal_ref=None`. | `promote_policy_evaluation_to_permit` raises `policy_evaluation_is_not_a_permit`. `HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE` and `HOST_GRANT_ISSUER_AVAILABLE` are false. |
| Planning session | `master_planning_session` | `MasterPlanningSessionRegistry` retains a denied route. `session_id` is the planning identity later copied as `planning_session_id`. | Session membership is not authentication. |
| Execution grant | `master_execution_grants` | `evaluate_grant_envelope` checks bounds. `LocalHarnessGrantIssuer.issue` signs a local claim. `GrantLifecycle` public envelopes stay `DESCRIPTIVE`. | `issue_production_execution_grant` raises `production_grant_issuance_closed`. `PRODUCTION_GRANT_ISSUANCE_AVAILABLE` is false. `HMAC_PREVENTS_REPLAY` is false. |
| Execution ledger | `master_execution_ledger` | `MasterExecutionLedger.reserve_once`, `mark_started`, `complete`, `fail`, `cancel` use conditional item writes. `DynamoDbMasterExecutionLedgerStore` takes an injected client and table `wilvor_master_execution_ledger`. `get_item` uses `ConsistentRead=True`. | `production_cross_instance_authority()` and `verified_aws_account_and_table_provenance()` return false. `PROCESS_LOCAL_LOCK_IS_DISTRIBUTED_ATOMICITY` is false. `GRANT_NONCE_ROTATES_ON_TAKEOVER` is false. |
| Grant/ledger join | `master_host_grant_ledger_integration` | `NonproductionHostGrantLedger.reserve_aligned` and `consume_local_and_mark_started` compose the local issuer with the ledger. | `required_atomic_single_use_interface().implemented` is false. `distributed_exactly_once` is false. `bind_production_grant_to_master_ledger` raises `ledger_not_integrated`. |
| Authorized scope | `master_authorized_scope` | `resolve_master_authorized_scope` returns `MasterAuthorizedScopeDecision` from hints plus `LocalTestHostScope`. | `promote_effective_scope_to_execution_grant` fails. `EXECUTION_GRANT_AVAILABLE` is false. |
| Runtime budget | `master_runtime_budget` | `RuntimeBudgetSession` enforces ceilings on an offline harness. Clocks are injected. | `promote_budget_decision_to_production_grant` fails. `DEPLOYED_HOST_ENABLED` is false. |
| Trusted read | `master_trusted_read_executor` | `MasterTrustedReadExecutor.execute` accepts only a sealed `HostReadInvocation` whose `authority_kind` is `NONPRODUCTION_TEST_ADAPTER`. Operations that pass are `GET_ITEM` and `QUERY`. | `issue_production_read_invocation` raises `production_grant_issuer_not_integrated`. `open_production_executor` raises `production_read_executor_not_enabled`. |
| Scoped-read join | `master_host_scoped_read_integration` | `NonproductionScopedReadHarness.prepare` then `enter` joins scope, guard, budget, and the executor. | `open_production_scoped_read` and `issue_production_scoped_read` fail. `PRODUCTION_HOST_SCOPED_READ_ENABLED` is false. |
| Scheduler | `master_authorized_scheduler_adapter` | `MasterAuthorizedSchedulerAdapter` records fanout. `dispatch_invocation` can run a scripted worker after a local consumed grant. | `PRODUCTION_DISPATCH_ENABLED` is false. `durable_exactly_once()` is false. `invoke_specialist` in `master_specialist_runtime_contract` raises `specialist_invocation_not_granted`. |
| Scheduler/ledger join | `master_host_scheduler_integration` | `NonproductionHostSchedulerPort.dispatch_consumed` requires the consumed local grant plus ledger fence fields. | Production dispatch stays disabled. `note_ledger_complete` records scheduling, not specialist success. |
| Read receipt | `master_authenticated_read_receipt` | `LocalTrustedReadHost.execute_read` returns `HostIssuedReadReceipt` with `source_authenticity()` `NOT_ESTABLISHED`. | `SOURCE_AUTHENTICATED_READ_AVAILABLE`, `LIVE_READS_ENABLED`, and `AWS_CONNECTION_IMPLEMENTED` are false. |
| Evidence join | `master_host_evidence_integration` | `integrate_host_read_evidence` returns `HostEvidenceIntegration`. `production_host_evidence_port().active` is false. | `open_production_source_authenticator` fails. `admit_evidence` raises `evidence_not_admitted`. |
| MA4 admission | `master_evidence_admission` | `admit_master_evidence` returns `MasterEvidenceAdmission` with `source_authenticity=NOT_ESTABLISHED`. Statuses are `REJECTED`, `CONTRACT_ONLY`, or `BINDING_UNRESOLVED`. | There is no `ADMITTED` status. |
| MA5 response | `master_response_policy`, `master_response_renderer` | `evaluate_master_response_policy` returns `MasterResponsePolicyHandoff`. `render_master_response` returns `MasterResponseRender`. States are `NOT_EVALUATED`, `UNSUPPORTED`, `UNAVAILABLE`, `INSUFFICIENT_EVIDENCE`. | `claims_admitted` stays false unless a later host establishes authenticity. The current evaluators do not establish it. |

Operational API facts, read from Terraform and restated by `SPRINT3B2_GATEWAY_DEPLOYMENT_REVIEW.md`:

- Route key `POST /ai/query`, `authorization_type = "JWT"`, authorizer identity source `$request.header.Authorization`, payload format `2.0`, integration `AWS_PROXY`.
- `authorization_scopes` is absent. OAuth scopes are not an edge control.
- AI Lambda permission principal is `apigateway.amazonaws.com`, action `lambda:InvokeFunction`, source ARN `{execution_arn}/$default/POST/ai/query`.
- The AI function must be distinct from `{name_prefix}-operational-api`.
- A same-account `lambda:InvokeFunction` allow can present a forged HTTP API v2 event. The event is not a signature over the gateway invoke.
- Twelve issuance controls in `ISSUANCE_CONTROL_IDS` stay `satisfied=false`. Live API id, account, region, resource policy, identity policy, qualifier, function URL, and log group are `NOT_YET_INSPECTED`.

Identity that every later stage preserves, from `HostInvocationIdentity` and the grant envelope:

`master_request_id`, `planning_session_id`, `step_id`, `invocation_id`, `specialist_request_id`, `correlation_id`, `specialist`, `purpose`, `capability`.

Those six strings are pairwise distinct. `idempotency_key` is distinct from them. `principal_ref` and `policy_revision` travel with the sealed principal and the policy evaluation. They are hints on `SpecialistRequestEnvelope` and authority only when the issuing component sealed them.

Allowed specialist pairs, unchanged:

- Live Ops: `DISCOVERY` or `ANSWER` with `RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT`, temporal `(CURRENT,)`.
- Historical: `ANSWER` with `RETRIEVE_HISTORICAL_ANALYTICS`, temporal `(HISTORICAL,)`, window at most 7 days.
- Decision: `ANSWER` with `EXPLAIN_EXISTING_RISK` or `EXPLAIN_EXISTING_RECOMMENDATIONS`, temporal `(CURRENT,)`, `(PERSISTED,)`, or `(CURRENT, PERSISTED)`. `MAX_DECISION_STEPS` is 1. `MAX_ROUTE_STEPS` is 4.

Clocks stay separate roles. Equal text does not merge them.

| Role | Field |
| --- | --- |
| Application request | `accepted_at_utc` / `application_request_time_utc` |
| Deadline | `deadline_utc` |
| Requested evaluation | `requested_evaluation_time_utc` |
| Source observation | `source_observation_time_utc`, `source_reported_observed_at_utc`, `observation_clock_utc` |
| Evaluation | `evaluation_time_utc`, `evaluation_clock_utc`, native `evaluated_as_of_utc` |
| Independent host observation | `independent_observation_time_utc`, `ledger_observed_at_utc` |
| Live Ops runtime | `LiveOpsTrustedContext.now_epoch` |
| Historical trusted context | `HistoricalSpecialistTrustedContext.as_of_utc` |

`evaluation_copied_from_request` stays false. `evaluated_as_of_utc` is not backfilled from `requested_evaluation_time_utc`.

## 2. Frozen shared interfaces

Existing functions keep their signatures and their closed failure behavior. Parallel agents add new modules that call these functions. They do not retarget the closed stubs.

### I1. Verified ingress to trusted application principal

| Item | Contract |
| --- | --- |
| Input | Server-owned `MasterGatewayIdentityConfiguration` or `ServerOwnedGatewayManifest`, plus the Lambda event as untrusted material. `IndependentlyHostAuthenticatedIdentityInput` is the only identity shape the principal port accepts, and only behind `NonproductionContractSeal` today. |
| Output | `MasterGatewayIdentityAssessment`, `GatewayRuntimeBoundaryReport`, then `ServerOwnedPrincipalDescriptor` bound to `PrincipalScopeBinding`. |
| Source | `assess_master_gateway_identity`, `assess_gateway_runtime_boundary`, `observe_untrusted_gateway_claims`, `open_nonproduction_principal_proof_port`, `NonproductionIngressPolicyPort.bind`. |
| Fields preserved | `issuer`, `audience`, `subject`, `scopes`, `route_key=POST /ai/query`, `purpose`, `policy_revision`, `planning_session_id`, `master_request_id`, `correlation_id`, `gateway_request_id`, `proof_id`, `authorization_provenance`, lifetime epochs, `application_request_time_utc`. |
| Issuer | Reviewed application host. That host is not wired. The edge JWT authorizer is an edge control, not the issuer. |
| Verifier | `master_application_principal` against the server-owned configuration and the proof port. `master_gateway_runtime_boundary` verifies manifest consistency. Neither verifies a JWT signature. |
| Missing production implementation | Live satisfaction of `HostIngressProofRequirement` (gateway integration review, Lambda invoke boundary, independent token verification, issuer, audience, subject, scopes, request and session binding, purpose and route, lifetime, policy revision, authorization provenance) and of all `ISSUANCE_CONTROL_IDS`. |
| Required new implementation | Agent A, new module only. A production principal handle whose `record_kind` is distinct from `SYNTHETIC_TEST_ADMISSION`, minted only after the twelve controls are supplied by an operator-owned manifest, not by the event. The module's enable flag stays false in this sprint. |
| Failure | Forged `requestContext`, `authorizer.jwt`, `Authorization`, Lambda context, or `trusted=True` are `RejectedInvocationProof` values. `reject_claimed_principal` and `reject_untrusted_authority` fail closed. `issue_application_principal` and `issue_production_application_principal` keep raising. |
| Owner | Agent A. |
| Shared-module edit | No. `master_host_ingress_policy_integration.py` import graph stays exact. |

`MasterRuntimePolicyEvaluation` from `evaluate_master_runtime_policy` is the policy object I2 consumes. `decision=ALLOW` exists on the enum and is reachable only from the private hypothetical harness. The public production function returns `DENY`. `PolicyDecisionStatus.ALLOW_DESCRIPTIVE` on `ApprovedPolicyDecision` is the descriptive mirror in `master_specialist_runtime_contract`. It is not a second decision and it is not a permit.

### I2. Trusted principal and policy to execution grant

| Item | Contract |
| --- | --- |
| Input | Sealed `ServerOwnedPrincipalDescriptor` plus `MasterRuntimePolicyEvaluation` for the same `master_request_id`, `planning_session_id`, `step_id`, `invocation_id`, `policy_revision`. Bounds come from `MasterExecutionGrantEnvelope`. |
| Output | Today: `MasterExecutionGrantEvaluation` with `lifecycle=DESCRIPTIVE` and `establishes_authority=False`, or `LocalHarnessExecutionGrant` with `production_grant=False`. Descriptive reference: `IssuedGrantReference` with `issued=False`. |
| Source | `evaluate_grant_envelope`, `refuse_production_grant_issuance`, `LocalHarnessGrantIssuer.prepare` / `issue` / `verify`. `issue_grant` and `issue_production_execution_grant` do not succeed. |
| Fields preserved | Envelope fields in `MasterExecutionGrantEnvelope`: identity, `principal_ref`, `policy_revision`, `tool_name`, `resource_scope` as `LogicalReadOperation` (`GET_ITEM` or `QUERY` only), `argument_names`, `region_scope`, `temporal_scopes`, `historical_window`, `decision_target_scope`, `accepted_at_utc`, `deadline_utc`, `expires_at_utc`, `idempotency_key`, `max_model_turns`, `max_tool_calls`, `correlation_id`. |
| Issuer | Grant signer. Local issuer is `LocalHarnessGrantIssuer` and is not production. Production signer is unwired (`PRODUCTION_SIGNING_KEY_STORE_INTEGRATED` is false). |
| Verifier | `LocalHarnessGrantIssuer.verify` checks signature and every bound field. A matching id, a serialized dataclass, or `bounds_accepted=True` does not verify a production grant. |
| Missing production implementation | A signing-key store, a host-issued lifecycle distinct from `HOST_ISSUED_LOCAL_HARNESS`, and a consume signature that takes one atomic ledger. |
| Required new implementation | Agent B, new module. Signed, bounded, non-transferable grant bound to one principal, one session, one invocation, one policy revision, one resource scope, and one deadline. Enable flag stays false. |
| Failure | Cross-principal, cross-session, mutated bounds, expired deadline, and caller-supplied allow labels return `GrantRejectionReason` on `GrantVerification` / `ProductionGrantRefusal`. `issue_production_execution_grant` raises `production_grant_issuance_closed`. |
| Owner | Agent B. |
| Shared-module edit | No. Do not change `LocalHarnessGrantIssuer.consume` or the closed constants. |

### I3. Execution grant to atomic ledger consumption

| Item | Contract |
| --- | --- |
| Input | Signed grant claim, `MasterExecutionLedgerEnvelope`, `owner_id`, `attempt_token`, `fence_epoch`, `now_utc`, `lease_seconds` at most `MAX_LEASE_SECONDS` (120). |
| Output | `GrantConsumption` and `MasterExecutionLedgerResult`. Joined locally as `NonproductionBoundaryResult`. |
| Source | `MasterExecutionLedger.reserve_once`, `mark_started`, `fenced_attempt_matches`; `LocalHarnessGrantIssuer.consume`; `NonproductionHostGrantLedger.consume_local_and_mark_started`. |
| Fields preserved | Ledger key is `(principal_ref, planning_session_id, master_request_id)` plus sort `(invocation_id, purpose)`. Fence is `state`, `fence_epoch`, `attempt_token`, `owner_id`, `payload_fingerprint`, `grant_nonce`. `idempotency_key` is stored. `repeat_non_idempotent_after_expiry` defaults so expiry is not a new execution. |
| Issuer | Ledger writer issues the fence and the ledger `grant_nonce`. The grant signer issues the signed nonce. Those two nonces are independent today. |
| Verifier | Conditional `put_if_absent` / `replace_if` on the ledger store. `fenced_attempt_matches` rejects a stale fence after takeover. Grant `verify` rejects a bad signature before consume. |
| Missing production implementation | The seam named by `required_atomic_single_use_interface`: one conditional write that consumes the signed nonce and marks the attempt started. `DynamoDbMasterExecutionLedgerStore` has no verified account or table provenance. |
| Required new implementation | Agent B, new module, injected DynamoDB client double in tests. Same conditional expressions already published: `CONDITIONAL_PUT_ABSENT` and `conditional_replace_expression`. Retention `RETENTION_AFTER_DEADLINE` is 30 days. TTL deletion remains not correctness and not first use. |
| Failure | `LedgerStoreUnavailable` on transport or missing table. Conditional conflict returns `MasterExecutionLedgerOutcome.REJECTED` or `REPLAYED`, not a second start. Replay of the same fingerprint does not create another start. Stale `fence_epoch` fails `MasterExecutionAttemptFenceCheck`. |
| Owner | Agent B. |
| Shared-module edit | No. `master_host_grant_ledger_integration.py` import list stays exact, including the ban on `boto3` inside that file. |

A successful ledger transition fences one attempt. It does not execute a specialist, and it is not distributed exactly-once specialist execution. Crash after `mark_started` and before the specialist returns is still one open attempt, recovered by fence and lease, not by claiming the specialist ran once.

### I4. Consumed grant to native specialist dispatch

| Item | Contract |
| --- | --- |
| Input | `ConsumedHostDispatchGrant` or the equivalent consumed `LocalHarnessExecutionGrant`, `ConsumedLedgerReservation` or `MasterExecutionLedgerResult` with matching `owner_id`, `attempt_token`, `fence_epoch`, and `BoundedDispatchContext`. |
| Output | Native result, kept beside host identity. Live Ops: `MasterLiveOpsNativeResult`. The runtime-contract wrapper `NativeSpecialistExecutionResult` stays `result_retained=False` and is an observation, not the native object. |
| Source | `NonproductionHostSchedulerPort.dispatch_consumed`. Native calls are the three `run` methods in section 4. `invoke_specialist` stays closed. |
| Fields preserved | The identity tuple, `correlation_id`, ceilings, `accepted_at_utc`, `deadline_utc`, fence fields. Live Ops bridge also keeps `specialist_request_id` and `application_request_time_utc` because `LiveOpsRunResult` does not carry them. |
| Issuer | Dispatch is allowed only after grant consume and a current fence. The scheduler does not issue the grant. |
| Verifier | `LocalTestHostAuthoritySeam.assert_consumed` and `fenced_attempt_matches`. An unconsumed grant, a second dispatch, and a stale fence are rejected. |
| Missing production implementation | A dispatcher that calls `LiveOpsSpecialist.run` under a consumed production grant. Historical finalization and Decision request construction stay unavailable for the reasons in section 4. |
| Required new implementation | Agent D, new orchestrator. First slice calls Live Ops only, with an injected provider. Historical and Decision branches stop at their existing gates. |
| Failure | `consumed_grant_required`, `specialist_execution_not_granted`, `execution_authority_not_granted`, `production_dispatch_disabled`, `model_origin_dispatch_rejected`. `invoke_specialist` raises `specialist_invocation_not_granted`. Cancellation and deadline expiry fail closed before `run`. |
| Owner | Agent D. |
| Shared-module edit | No. Do not edit `live_ops_specialist.py`, `historical_specialist.py`, `decision_specialist.py`, or `master_specialist_runtime_contract.py`. |

### I5. Consumed grant to authorized read invocation

| Item | Contract |
| --- | --- |
| Input | Consumed grant bounds plus `UntrustedReadRequest`. Caller fields `caller_scope`, `caller_policy_decision`, `caller_authorized`, `caller_deadline_utc`, `caller_max_tool_calls`, `caller_callable`, and `caller_tool_result` are untrusted. |
| Output | `ReadReceiptCandidate` from `MasterTrustedReadExecutor.execute`. Join result is `ScopedReadJoinResult`. |
| Source | `bind_nonproduction_test_invocation` (no grant parameter), `NonproductionScopedReadHarness.prepare` / `enter`, `MasterTrustedReadExecutor.execute`. |
| Fields preserved | Identity, `tool_name`, `tool_call_id`, `resource_id`, `operation`, `principal_ref`, `policy_revision`, `region_codes`, historical window, decision aircraft and recommendation ids, `grant_nonce`, `idempotency_key`, both clocks, `executed_arguments`, `record_ids`, `source_name`, `source_version`, `query_id`, pagination flags. |
| Issuer | Host read invocation. Today only `HostAuthorityKind.NONPRODUCTION_TEST_ADAPTER`. The grant issuer does not become a read permit by being copied into `grant_nonce`. |
| Verifier | Executor checks sealed host, allowlisted tool and arguments from `server_read_tools`, `server_read_argument_names`, and `server_read_resources`, budget, deadline, and cancellation. `allowlist_status_is_not_a_permit` rejects `WITHIN_ALLOWLIST`. |
| Missing production implementation | An approved AWS adapter for `GET_ITEM` and `QUERY` against specialist tables, with account and table provenance. `PRODUCTION_GRANT_ISSUER_INTEGRATED` is false, so the existing executor records `ReadBoundaryDenial.PRODUCTION_GRANT_NOT_INTEGRATED` for a production-shaped call. |
| Required new implementation | Agent C, new adapter module. Injected client. `Scan`, writes, SQL argument names `sql`, `statement`, `query_string`, wildcards, and raw ARNs fail. Server injects scope arguments. Duplicate idempotency invokes the adapter once. |
| Failure | `scan_not_permitted`, `resource_not_allowlisted`, `wildcard_resource`, `arbitrary_table_arn`, `arbitrary_sql`, `historical_interval_escalation`, `cancellation_not_host_issued`, deadline and budget denials. `issue_production_read_invocation` raises `production_grant_issuer_not_integrated`. |
| Owner | Agent C. |
| Shared-module edit | No. `master_host_scoped_read_integration.py` stays free of `boto3`. Budget and scope modules keep their closed promotion functions. |

### I6. Authorized read to authenticated source evidence

| Item | Contract |
| --- | --- |
| Input | `ReadReceiptCandidate` or a raw observation dict, plus `MasterInvocationAuthorization`, optional `ExpectedObservationClocks`, expected source version, aircraft id, step id, and tool-call id. |
| Output | `ReadReceiptVerification` and `HostEvidenceIntegration`. A host-issued local receipt is `HostIssuedReadReceipt` with production methods returning false. |
| Source | `raw_read_observation`, `assess_untrusted_read_subject`, `verify_read_receipt`, `integrate_host_read_evidence`. |
| Fields preserved | Source identity (`source_name` / adapter identity), `query_id` where the adapter returned one, `source_version` where the row or `SourceRecord` has one, request clock, observation clock, evaluation clock, pagination (`has_more`, `truncated`, `page_index`, `rows_returned`), population conditions, geographic limitations. |
| Issuer | Approved source adapter, after a consumed grant. The current local adapter's origin is `ReadOrigin` local test, and `ProductionOrigin` stays unresolved. |
| Verifier | `verify_read_receipt` and `integrate_host_read_evidence`. A copied candidate, a tool result, and a caller trace are not receipts. `TOOL_RESULT_IS_READ_RECEIPT` and `CALLER_SUPPLIED_TRACE_ADMITTED` are false. |
| Missing production implementation | `ProductionHostEvidencePort` id `wilvor.production_host_source_authenticator.unavailable` with `authenticator_integrated=False`. |
| Required new implementation | Agent E, new module that accepts a receipt only from Agent C's adapter identity and only when I3 consumption and I5 invocation ids match. Local and nonproduction receipts remain `NOT_ESTABLISHED`. |
| Failure | `forged_read_receipt`, `read_observation_is_not_source_authentication`, clock mismatch, source-version mismatch, incomplete pagination. `open_production_source_authenticator` fails. |
| Owner | Agent E. |
| Shared-module edit | No. |

### I7. Native specialist result and evidence to MA4 admission

| Item | Contract |
| --- | --- |
| Input | `MasterEvidenceAdmissionInput` holding `MasterInvocationAuthorization` and one native artifact. Live Ops artifact is `LiveOpsAdmissionSources` (`request`, `context`, `result`), not a bare `LiveOpsRunResult`. |
| Output | `MasterEvidenceAdmissionCollection`. |
| Source | `admit_master_evidence`. Historical correlation uses `correlate_master_historical_native_result` and does not call `finalize_historical_specialist_run`. Decision admission uses `resolve_master_decision_target` and does not call `release_master_decision_target`. |
| Fields preserved | `master_request_id`, `invocation_id`, `specialist`, admission `reason_codes`, `binding_defects`, `temporal_limitations`, `source_authenticity`. |
| Issuer | MA4 admits evidence only after I6 says the source adapter completed inside the effective scope. `admit_evidence` in the runtime contract is not that issuer. |
| Verifier | `admit_master_evidence` plus `integrate_host_read_evidence`. Verifier success inside a specialist (`ALL_VERIFIED`, historical verifier, decision verifier) is not source authentication. |
| Missing production implementation | A path that can return authenticity other than `MasterSourceAuthenticity.NOT_ESTABLISHED`. No such status exists on the enum today. Adding one is an integration-owner change. |
| Required new implementation | Agent E joins I4 and I6 and calls `admit_master_evidence`. Until I6 is production-authenticated, the admission stays `CONTRACT_ONLY` or `BINDING_UNRESOLVED` with `SOURCE_AUTHENTICITY_NOT_ESTABLISHED`. |
| Failure | Serialized records, route plans, mismatched invocation ids, `HISTORICAL_RUN_NOT_FINAL_ARTIFACT`, `NATIVE_CORRELATION_ABSENT`, `DECISION_TARGET_CANDIDATE_ABSENT`, `LIVE_OPS_ALL_VERIFIED_NOT_SOURCE_AUTHENTICATION`, `VERIFIED_ZERO_NOT_INFERRED`, `GEOGRAPHIC_PROVENANCE_ABSENT`. |
| Owner | Agent E. |
| Shared-module edit | Adding an admitted status or a new authenticity enum value waits for the integration owner. |

### I8. MA4 admission to MA5 safe response

| Item | Contract |
| --- | --- |
| Input | `MasterParallelContractBundle` whose evidence inputs are the MA4 admissions and corroboration inputs. |
| Output | `MasterResponsePolicyHandoff`, then `MasterResponseRender` (`state`, `status_text`, `limitation_codes`, `claims_admitted`). |
| Source | `evaluate_master_response_policy`, `render_master_response`. |
| Fields preserved | `master_request_id`, invocation ids, `source_authenticity`, `production_authority`, `request_coverage`, limitation codes. |
| Issuer | Response policy. It does not issue grants, authenticity, or operational instructions. |
| Verifier | Renderer copies the handoff. It does not promote `claims_admitted`. |
| Missing production implementation | A handoff that can set `claims_admitted` from an I7 admission whose authenticity was established by I6. The current policy states have no success state that means "safe operational claim." |
| Required new implementation | Agent E renders the closed handoff, including insufficient evidence, truncation, and unverified zero. Wording must not add safety or diversion instructions. |
| Failure | Missing invocation, non-uniform native evidence, and unauthenticated source become `INSUFFICIENT_EVIDENCE`, `UNAVAILABLE`, or `UNSUPPORTED` with `claims_admitted=False`. |
| Owner | Agent E. |
| Shared-module edit | No. `master_parallel_contracts.py` stays frozen. |

## 3. Existing code to reuse

Reuse these. Do not wrap them in a new interface family.

- Ingress: `current_disabled_route_manifest`, `assess_gateway_runtime_boundary`, `assess_master_gateway_identity`, `observe_untrusted_gateway_claims`, `NonproductionIngressPolicyPort.bind`.
- Principal: `ServerOwnedPrincipalDescriptor`, `PrincipalScopeBinding`, `HostIngressProofRequirement`, `NonproductionPrincipalProofPort`.
- Policy: `retain_runtime_policy_session`, `seal_server_policy_inputs`, `evaluate_master_runtime_policy`, `MasterRuntimePolicyEvaluation`.
- Session: `MasterPlanningSessionRecord.session_id`.
- Grant: `MasterExecutionGrantEnvelope`, `LogicalReadOperation`, `evaluate_grant_envelope`, `LocalHarnessGrantIssuer.verify`, `parse_execution_grant`.
- Ledger: `MasterExecutionLedgerEnvelope`, `MasterExecutionLedger.for_durable`, `DynamoDbMasterExecutionLedgerStore`, `conditional_replace_expression`, `ledger_item_key`, `fenced_attempt_matches`.
- Join already specified: `required_atomic_single_use_interface`.
- Scope and budget: `resolve_master_authorized_scope`, `server_read_budget`, `RuntimeBudgetSession.begin_read`, `record_source_observation`.
- Read: `UntrustedReadRequest`, `ReadReceiptCandidate`, `raw_read_observation`, `ReadOperation.GET_ITEM`, `ReadOperation.QUERY`.
- Dispatch observations: `BoundedDispatchContext`, `dispatch_consumed`, `prepare_master_live_ops_native_bridge`, `accept_native_live_ops_result`, `prepare_master_historical_native_invocation`, `correlate_master_historical_native_result`, `resolve_master_decision_target`.
- Evidence and response: `integrate_host_read_evidence`, `admit_master_evidence`, `evaluate_master_response_policy`, `render_master_response`.
- Descriptive contract, read-only: `SPRINT3B2_TRUSTED_HOST_INTERFACE.md` and `master_specialist_runtime_contract`.

`ReadOperation.SCAN` remains defined so callers can be rejected. It is not an allowed operation.

## 4. Critical production gaps

1. Gateway provenance is `NOT_ATTESTED`. Direct invoke can forge the event. Same-account identity policy is unreviewed. `authorization_scopes` is unset.
2. `evaluate_master_runtime_policy` has no host-authenticated principal argument. Its production origin returns `DENY`.
3. Grant consume, ledger `reserve_once`, and `mark_started` are separate writes. The signed nonce and the ledger nonce differ. `HMAC_PREVENTS_REPLAY` is false.
4. `DynamoDbMasterExecutionLedgerStore` is a conditional-write adapter over an injected client. Constructing it does not prove the AWS account or the table.
5. Ledger fencing is attempt fencing under concurrency and retries. It is not exactly-once specialist execution.
6. `GRANT_NONCE_ROTATES_ON_TAKEOVER` is false. After lease expiry the new owner gets a new fence and the same grant nonce. Dispatch must present the current fence.
7. TTL absence is not first use and not permission to repeat a non-idempotent start.
8. Read executor, scoped-read harness, and evidence port reject production promotion. A local `ReadReceiptCandidate` is not an authenticated source.
9. `MasterSourceAuthenticity` has only `NOT_ESTABLISHED`. MA4 cannot represent a production admission until the integration owner extends that enum in one change with the tests that guard it.
10. Historical native results omit host correlation and source lineage. The gap checker raises if `source_version` or `query_id` is added to `HistoricalSpecialistRunResult` or `SpecialistResult`. `source_version` belongs to `SourceRecord`. `query_id` belongs to `QueryExecutionTrace`.
11. Decision `decision_request` is typed `None`. `release_master_decision_target` refuses every native request. Proof issuer is unavailable.
12. `enable_ai_query_route` remains false. No AI Lambda is deployed by this module.

## 5. Native specialist contracts

These `run` methods are different types. A shared request envelope must not be passed to them.

### Live Ops

`LiveOpsSpecialist.run(request: LiveOpsRunRequest, context: LiveOpsTrustedContext) -> LiveOpsRunResult` in `wilvor_ai.live_ops_specialist`.

`LiveOpsRunRequest`: `user_text`, `schema_version`, optional `request_id`, optional `conversation_id`, `prior_turns`, `selector_hints`, `entity_hints`.

`LiveOpsTrustedContext`: `tables`, `now_epoch`, `correlation_id`, `max_model_turns`, `max_tool_calls`, optional `clock`, optional `deadline_epoch`, optional `cancelled`. `now_epoch` is not `accepted_at_utc`.

`LiveOpsRunResult` carries `correlation_id`, `execution_status` (`LiveOpsRunStatus`), `reference_epoch`, `reference_utc`, claims, and optional `request_id`. It does not carry `master_request_id`, `planning_session_id`, `step_id`, `invocation_id`, or `specialist_request_id`.

Host lineage for the vertical slice is `MasterLiveOpsNativeBridge`, built by `prepare_master_live_ops_native_bridge(binding)` and paired by `accept_native_live_ops_result(bridge, result, *, source_observation_epoch)`. `specialist_invoked_by_bridge` stays false when the bridge only accepts an already produced result. Agent D's orchestrator is what calls `run`. Selector hints on the request are not authorization. `reject_unauthorized_selectors` stays in force.

This is the only specialist in the first vertical slice.

### Historical Analytics

`HistoricalAnalyticsSpecialist.run(request: SpecialistRequest, context: HistoricalSpecialistTrustedContext) -> HistoricalSpecialistRunResult` in `wilvor_ai.historical_specialist`.

`SpecialistRequest` is `text` plus optional `request_id`. It has no `start_utc` or `end_utc`. The plan window is not a tool argument.

`HistoricalSpecialistTrustedContext` is only `as_of_utc`.

Final artifact is `SpecialistResult` from `finalize_historical_specialist_run(run_result)` in `wilvor_ai.historical_answer_renderer`. The finalizer takes no provider, no operations, and no trusted context. `HistoricalSpecialistRunResult` is intermediate. `MasterAdmissionReason.HISTORICAL_RUN_NOT_FINAL_ARTIFACT` applies if the run is admitted directly.

`historical_native_result_correlation_gaps()` reports `_CORRELATION_FIELDS` absent on `HistoricalSpecialistRunResult`, `SpecialistResult`, `ToolResult`, `Evidence`, `SpecialistRequest`, and `HistoricalSpecialistTrustedContext`. The field list is:

`master_request_id`, `specialist_request_id`, `request_id`, `correlation_id`, `application_request_time_utc`, `requested_evaluation_time_utc`, `unbound_plan_window_start_utc`, `unbound_plan_window_end_utc`, `start_utc`, `end_utc`, `authenticated_source_evaluation_time_utc`, `source_version`, `query_id`.

`SpecialistRequest.request_id` and `ToolResult.correlation_id` are the exceptions present on those two types. `ToolResult.correlation_id` does not satisfy the result-level correlation contract. `evaluated_as_of_utc` is present on the run and on `SpecialistResult` and is still not authenticated source time. Host `planning_session_id`, `step_id`, and `invocation_id` are also absent from those native results.

Agent D may call `prepare_master_historical_native_invocation` and `correlate_master_historical_native_result`. Agent D does not call `finalize_historical_specialist_run` and does not add the missing fields. Source lineage for a historical read, when a read exists, stays on `Evidence.source_records: tuple[SourceRecord, ...]` and `Evidence.query_executions: tuple[QueryExecutionTrace, ...]`.

### Decision

`DecisionSpecialist.run(request: DecisionSpecialistRequest, runtime: DecisionToolsRuntime) -> DecisionSpecialistRunResult` in `wilvor_ai.decision_specialist`. The second parameter is `runtime`, not `context`.

`DecisionSpecialistRequest`: `user_text`, `mode` (`AIRCRAFT` or `DIRECT_PERSISTED`), exactly one of `aircraft_id` or `recommendation_id`, optional `request_id`.

`DecisionToolsRuntime`: `tables`, `now_epoch`, `query_timestamp_utc`, optional `correlation_id`. That `correlation_id` is not copied onto `DecisionSpecialistRunResult`.

`resolve_master_decision_target` returns `MasterDecisionTargetResolution` with `decision_request=None`, `native_request_built=False`, `trusted_target_issued=False`, and `read_origin=INJECTED_FAKE` for the only catalog it accepts. Required proofs, issuer unavailable:

- `SOURCE_CURRENTNESS`
- `ALLOWED_TARGET_POPULATION`
- `SEPARATE_PERSISTED_RECOMMENDATION_AUTHORIZATION`

These comparison fields are not the native target: `MasterInvocationAuthorization.expected_aircraft_id`, `expected_recommendation_id`, `MasterPlanningContext.target_candidate_aircraft_id`, `target_candidate_recommendation_id`. A unique injected candidate is `UNIQUE_CANDIDATE_NOT_ELIGIBLE`. `release_master_decision_target` and `construct_decision_native_request` refuse the native request.

Missing risk stays `RiskRecordObservation.MISSING`. `absent_risk_is_low_risk` stays false. Agent D stops at `resolve_master_decision_target`. Agent D does not call `DecisionSpecialist.run` in the production-shaped path.

## 6. Five agent assignments

Parallel rule for every agent:

- Create only the new files named here.
- Leave every `PRODUCTION_*`, `*_AVAILABLE`, and `*_ENABLED` constant false.
- Leave every `issue_production_*`, `open_production_*`, `invoke_specialist`, `admit_evidence`, and `construct_decision_native_request` function raising.
- Leave pinned Sprint 3B.2X import graphs unchanged.
- Tests use injected fakes. No AWS calls, no model SDK calls, no Terraform changes.
- Existing public signatures stay as they are. Additive helpers inside an owned existing file are allowed only when every current test still passes without editing that test.

Exclusive ownership means one writer. A file in one agent's list is absent from the others. Shared files in the integration-owner list have no parallel writer.

### Agent A — AWS ingress and trusted principal

New files:

- `functions/shared/wilvor_ai/sprint3c_ingress_principal.py`
- `tests/contracts/test_sprint3c_ingress_principal.py`

Existing files this agent may modify, exclusively, under the parallel rule:

- `functions/shared/wilvor_ai/master_gateway_identity.py`
- `functions/shared/wilvor_ai/master_gateway_runtime_boundary.py`
- `functions/shared/wilvor_ai/master_application_principal.py`
- `functions/shared/wilvor_ai/master_runtime_policy.py`
- `functions/shared/wilvor_ai/master_planning_session.py`

Inputs: server-owned manifest and configuration. The event is untrusted.

Outputs agreed with B: a sealed principal handle plus `MasterRuntimePolicyEvaluation` sharing `master_request_id`, `planning_session_id`, `step_id`, `invocation_id`, `principal_ref`, `policy_revision`, `purpose`, `accepted_at_utc`, `deadline_utc`. While the public policy function returns `DENY`, the output's `decision` is `DENY` and `authority_origin` is `NOT_ESTABLISHED`. Agent A records that fact. Agent A does not synthesize `ALLOW`.

Frozen interfaces: I1, and the policy half of I2.

Acceptance: forged JWT-looking events fail; synthetic descriptors stay `production_principal=False`; `issue_production_application_principal` still raises; existing ingress import-isolation tests pass.

Trust boundary that stays disabled: production principal issuance, runtime admission, `POST /ai/query`.

Must not modify: grant, ledger, read, scheduler, specialist, evidence, response, Terraform, `master_host_ingress_policy_integration.py`, `master_specialist_runtime_contract.py`.

### Agent B — Durable grant and DynamoDB execution ledger

New files:

- `functions/shared/wilvor_ai/sprint3c_grant_ledger.py`
- `tests/contracts/test_sprint3c_grant_ledger.py`

Existing files this agent may modify, exclusively, under the parallel rule:

- `functions/shared/wilvor_ai/master_execution_grants.py`
- `functions/shared/wilvor_ai/master_execution_ledger.py`

`master_host_grant_ledger_integration.py` is frozen. Its import set is an exact equality in `test_sprint3b2x_grant_ledger_import_isolation.py`.

Inputs from A: sealed principal ref, policy revision, identity tuple, deadline. A `DENY` evaluation is not issued as a grant.

Outputs agreed with D and C: a consumed-attempt record with `owner_id`, `attempt_token`, `fence_epoch`, `grant_nonce`, `idempotency_key`, `payload_fingerprint`, ledger `state`, and `distributed_exactly_once=False`.

Frozen interfaces: I2, I3.

Acceptance: signature mismatch fails; replay returns `REPLAYED` and does not start again; stale fence fails; cross-principal and cross-session grants fail; conditional expressions reject Scan and Delete; `bind_production_grant_to_master_ledger` still raises `ledger_not_integrated`; tests inject a fake client and do not call AWS.

Trust boundary that stays disabled: `PRODUCTION_GRANT_ISSUANCE_AVAILABLE`, `DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE`, verified AWS provenance, exactly-once specialist execution.

Must not modify: ingress, policy, scheduler, read executor, specialists, evidence, `master_host_grant_ledger_integration.py`.

### Agent C — Production scoped AWS read tools

New files:

- `functions/shared/wilvor_ai/sprint3c_scoped_read.py`
- `tests/contracts/test_sprint3c_scoped_read.py`

Existing files this agent may modify, exclusively, under the parallel rule:

- `functions/shared/wilvor_ai/master_authorized_scope.py`
- `functions/shared/wilvor_ai/master_runtime_budget.py`
- `functions/shared/wilvor_ai/master_trusted_read_executor.py`
- `functions/shared/wilvor_ai/master_read_resource_guard.py`
- `functions/shared/wilvor_ai/master_runtime_read_journal.py`

`master_host_scoped_read_integration.py` is frozen.

Inputs from B: consumed attempt, resource scope, region scope, deadline, idempotency key, grant nonce. Untrusted tool arguments arrive as `UntrustedReadRequest`.

Outputs agreed with E: `ReadReceiptCandidate` or the same fields projected by `raw_read_observation`, with `approved_source_adapter=False` and `source_authenticity=NOT_ESTABLISHED` until the integration owner promotes the adapter. Pagination and source version are still populated when the fake source returns them.

Frozen interfaces: I5.

Acceptance: `GET_ITEM` and `QUERY` only; Scan, SQL, writes, wildcards, and ARN targets fail; seven-day historical window is rejected rather than clipped; server scope wins over caller scope; cancellation and deadline stop the read; a repeated idempotency key hits the adapter once; empty page sets `page_empty` and leaves `verified_zero_minted=False`; `open_production_executor` still raises.

Trust boundary that stays disabled: `PRODUCTION_READ_EXECUTOR_ENABLED`, `PRODUCTION_HOST_SCOPED_READ_ENABLED`, source authentication, geographic coverage, SIGMET polygon intersection.

Must not modify: grant ledger, scheduler, specialists, evidence admission, response policy, Terraform.

### Agent D — Native specialist orchestration

New files:

- `functions/shared/wilvor_ai/sprint3c_specialist_orchestration.py`
- `tests/contracts/test_sprint3c_specialist_orchestration.py`

Existing files this agent may modify, exclusively, under the parallel rule:

- `functions/shared/wilvor_ai/master_authorized_scheduler_adapter.py`
- `functions/shared/wilvor_ai/master_live_ops_native_bridge.py`
- `functions/shared/wilvor_ai/master_historical_native_bridge.py`
- `functions/shared/wilvor_ai/master_decision_target_resolver.py`
- `functions/shared/wilvor_ai/master_live_ops_binding.py`
- `functions/shared/wilvor_ai/master_historical_binding.py`
- `functions/shared/wilvor_ai/master_decision_binding.py`
- `functions/shared/wilvor_ai/master_live_ops_execution_readiness.py`
- `functions/shared/wilvor_ai/master_historical_execution_readiness.py`
- `functions/shared/wilvor_ai/master_decision_execution_readiness.py`

`master_host_scheduler_integration.py` is frozen. Native `run` modules and `historical_answer_renderer.py` are frozen.

Inputs from B: consumed attempt and current fence. Live Ops read callbacks come from C's output, passed in as data. D does not import C's module if that creates an ownership cycle; the checkpoint test performs the wiring. D's unit tests use a fake read page.

Outputs agreed with E:

- Live Ops: `MasterLiveOpsNativeResult` whose bridge identity matches the consumed attempt, plus the untouched `LiveOpsRunResult`.
- Historical: `MasterHistoricalNativeBridgeEnvelope` with `finalization_performed=False` and `evidence_issued=False`.
- Decision: `MasterDecisionTargetResolution` with `decision_request is None`.

Frozen interfaces: I4, and the native half of I7.

Acceptance: unconsumed grant does not call `run`; second dispatch fails; stale fence fails; deadline and cancellation fail; Live Ops `run` is invoked only with `LiveOpsRunRequest` and `LiveOpsTrustedContext`; provider is injected and is not a network client; historical finalize is not called; Decision `run` is not called; `PRODUCTION_DISPATCH_ENABLED` stays false; `invoke_specialist` still raises.

Trust boundary that stays disabled: production dispatch, native historical type changes, Decision target release, model APIs.

Must not modify: `live_ops_specialist.py`, `historical_specialist.py`, `decision_specialist.py`, `decision_tools.py`, `historical_answer_renderer.py`, `specialist_contracts.py`, `specialist_runtime_contracts.py`, `decision_specialist_runtime_contracts.py`, `live_ops_public_contracts.py`, evidence modules, grant ledger, read executor.

### Agent E — Authenticated evidence and response safety

New files:

- `functions/shared/wilvor_ai/sprint3c_evidence_response.py`
- `tests/contracts/test_sprint3c_evidence_response.py`

Existing files this agent may modify, exclusively, under the parallel rule:

- `functions/shared/wilvor_ai/master_authenticated_read_receipt.py`
- `functions/shared/wilvor_ai/master_evidence_admission.py`
- `functions/shared/wilvor_ai/master_response_policy.py`
- `functions/shared/wilvor_ai/master_response_renderer.py`
- `functions/shared/wilvor_ai/master_evidence_corroboration.py`
- `functions/shared/wilvor_ai/master_evidence_comparability.py`
- `functions/shared/wilvor_ai/master_provenance_trace.py`
- `functions/shared/wilvor_ai/master_source_read_trace_boundary.py`

`master_host_evidence_integration.py` and `master_parallel_contracts.py` are frozen.

Inputs from D: `MasterLiveOpsNativeResult` for the slice; historical envelope and decision resolution for the parallel negative paths. Inputs from C: `ReadReceiptCandidate` and clocks.

Outputs: `HostEvidenceIntegration` via the existing integrator, `MasterEvidenceAdmissionCollection`, `MasterResponsePolicyHandoff`, `MasterResponseRender`.

Frozen interfaces: I6, I7, I8.

Acceptance: local receipt is not production evidence; empty page is not a verified zero; missing risk is not low; California text and regional strings are not SIGMET polygon overlap and are not an impacted-aircraft count; Live Ops verified claims are not source authentication; historical run is not a final artifact; decision comparison ids are not a target; response limitations include the authenticity gap; `claims_admitted` stays false; `admit_evidence` still raises.

Trust boundary that stays disabled: `SOURCE_AUTHENTICATED_READ_AVAILABLE`, `PRODUCTION_SOURCE_AUTHENTICATOR_INTEGRATED`, aviation-evidence admission, automatic safety or diversion text.

Must not modify: ingress, grants, ledger, read executor, scheduler, native specialists, `contracts.py` (`SourceRecord`, `QueryExecutionTrace`, `Evidence`, `ToolResult`).

### Integration owner — after checkpoints, not in parallel

No parallel agent edits these:

- `master_specialist_runtime_contract.py`
- `master_host_ingress_policy_integration.py`
- `master_host_grant_ledger_integration.py`
- `master_host_scoped_read_integration.py`
- `master_host_scheduler_integration.py`
- `master_host_evidence_integration.py`
- `master_contracts.py`
- `master_routing.py`
- `master_orchestration.py`
- `master_parallel_contracts.py`
- `master_execution_fanout.py`
- `master_execution_integration.py`
- `master_execution_preflight.py`
- `master_execution_policy_boundary.py`
- `master_execution_scope.py`
- `contracts.py`
- native specialist modules and `historical_answer_renderer.py`
- `modules/operational_api/**`
- every existing `test_sprint3b2*.py` and `test_sprint3b2x*.py`

The integration owner adds one file when the checkpoints exist:

- `tests/contracts/test_sprint3c_vertical_slice.py`

That test imports the five new modules and asserts one invocation id across I1–I8. It does not flip closed flags.

Overlapping existing-file ownership: none. Each existing file above has one owner or is frozen for the integration owner.

## 7. Cross-agent checkpoints

Run these while the other agents continue. Each checkpoint is a test in the producing agent's new test file, plus the integration-owner slice test once both sides exist. A missing peer is a fake object that matches the frozen fields, not a copied production grant.

| Checkpoint | When it can run | Assert |
| --- | --- | --- |
| CP0 | Immediately | Existing 3B.2X import-isolation and `test_sprint3b2x_cross_agent_end_to_end.py` still pass. Closed constants named in this document are false. |
| CP1 | Agent A has the new principal module | Forged event does not change `principal_ref`. Policy output is the sealed `MasterRuntimePolicyEvaluation`. `decision=DENY` is not passed to `issue_production_execution_grant`. |
| CP2 | Agent B has the new ledger module, using A's identity fixture | Same identity and idempotency key. First consume returns a fence. Replay is `REPLAYED`. Stale fence is rejected. `distributed_exactly_once` is false. Fake DynamoDB client only. |
| CP3 | Agent D has the Live Ops orchestrator, using B's fence fixture | `LiveOpsSpecialist.run` sees `LiveOpsRunRequest` and `LiveOpsTrustedContext`. Bridge identity equals the fence identity. Historical envelope has `finalization_performed=False`. Decision resolution has `decision_request is None`. |
| CP4 | Agent C has the read adapter, using B's scope fixture | One `GET_ITEM` or `QUERY`. Scan rejected. Empty page is `page_empty` with `verified_zero_minted=False`. Receipt identity equals the fence. |
| CP5 | Agent E has the join, using D's Live Ops result and C's receipt | `admit_master_evidence` keeps `SOURCE_AUTHENTICITY_NOT_ESTABLISHED`. Renderer `claims_admitted` is false. Regional text is not polygon overlap. |
| CP6 | CP1–CP5 fixtures exist | `test_sprint3c_vertical_slice.py` runs the Live Ops chain on fakes: principal, grant consume, ledger fence, Live Ops `run`, scoped read, MA4, MA5. One `invocation_id` throughout. Historical and Decision arms stop at their gates in the same test. |
| CP7 | Operator supplies a reviewed nonproduction manifest | Still outside this parallel sprint. Requires the twelve issuance controls, a reviewed same-account identity policy, and an explicit decision to change Terraform. CP7 is not a parallel-agent exit. |

Interface mismatches are defects against section 2, found at the earliest checkpoint that joins those two agents. Agents do not wait for all five branches.

## 8. Security acceptance criteria

The slice meets these criteria before any enablement review. Offline tests already encode the same rules. New tests must keep them.

- The principal is server-owned. Caller JWT-looking fields, `Authorization`, and `requestContext.authorizer.jwt` are observations.
- Lambda invoke path stays the reviewed API Gateway source ARN for `POST /ai/query` plus `apigateway.amazonaws.com`. Wildcard invoke, the operational Lambda, function URLs, and other principals stay issuance blockers.
- Session, invocation, and principal ids match across I1–I8 or the stage fails closed.
- Grants are signed, time-bounded, and bound to one principal, one session, one invocation, and one resource scope.
- Ledger consumption is a conditional write with a fence. Retries and concurrent attempts replay or reject. They do not open a second start for the same fingerprint.
- Exactly-once specialist execution is not claimed from that write.
- Reads are `GET_ITEM` and `QUERY` on allowlisted resources. Scan, arbitrary SQL, and writes fail.
- Budgets and deadlines are the server values from `server_read_budget` and `ServerPolicyInputs`. Caller ceilings are ignored.
- Cancellation and timeout are host-owned (`HostCancellation`, deadline clock, budget `cancel`).
- Application, source, and evaluation clocks stay distinct fields.
- Source name, `source_version` from `SourceRecord` when present, and `query_id` from `QueryExecutionTrace` when present are copied from the adapter result. Absence stays absence.
- Pagination reports `has_more`, `truncated`, and row counts. A partial page is not complete coverage.
- An empty page does not create a verified-zero claim.
- A missing risk record does not become a low risk.
- California text, a region name, or a census label does not establish aircraft-SIGMET polygon intersection or an impacted-aircraft count.
- Response text does not add safety instructions or diversion instructions.

## 9. Tests

Offline, in the new test files, no network:

- A: forged gateway event, direct-invoke context, untrusted `trusted=True`, synthetic principal remains non-production, public policy evaluation remains `DENY`.
- B: sign and verify, reject mutated envelope, consume once, replay, stale fence, lease expiry is not a new nonce and not a new start, TTL deletion is not first use, fake client conditional failure, `distributed_exactly_once` false.
- C: allowlisted `GET_ITEM` and `QUERY`, reject Scan and SQL, reject out-of-scope resource, reject interval over 7 days without clipping, idempotent second call, cancellation, timeout, empty page, source clock distinct from deadline.
- D: Live Ops happy path with fake provider and fake page; reject unconsumed grant; reject second dispatch; historical correlation without finalize; decision resolver without `DecisionSpecialist.run`; conflicting decision targets rejected.
- E: local receipt rejected as production evidence; empty read; missing risk; California text; Live Ops all-verified is not source authentication; MA5 limitations present; `claims_admitted` false.
- CP6 vertical slice joins the five fakes on one invocation id.

AWS nonproduction, deferred until CP7 and an operator manifest. Not part of parallel exit:

- `apigatewayv2:GetApi` and `lambda:GetPolicy` recorded against the twelve controls.
- Same-account identity policy review for `lambda:InvokeFunction`.
- Ledger conditional write against the nonproduction table `wilvor_master_execution_ledger` in the reviewed account, then a second concurrent writer.
- One `GET_ITEM` or `QUERY` against a reviewed nonproduction specialist table. No Scan.
- CloudTrail used to attribute the invoke. The event request id is not that attribution.
- `enable_ai_query_route` still false unless a separate change enables it after the controls are satisfied.

Existing Sprint 3B.2 and 3B.2X tests stay as they are. New tests do not delete or weaken them.

## 10. Blockers that prevent production enablement

Any one of these blocks enabling the route, flipping a closed constant, or treating the slice as production:

- Any `ISSUANCE_CONTROL_IDS` entry unsatisfied, including same-account invoke and absent `authorization_scopes` review.
- `InvocationProvenanceStanding.NOT_ATTESTED` or a principal whose `record_kind` is `SYNTHETIC_TEST_ADMISSION`.
- Public runtime policy still `DENY` / `NOT_ESTABLISHED`, or an `ALLOW` taken from the hypothetical harness.
- `issue_production_execution_grant` or `bind_production_grant_to_master_ledger` still the closed stubs, or consume still split across `LocalHarnessConsumptionLedger` and `mark_started`.
- Ledger client without verified account and table provenance.
- A claim that the ledger write is exactly-once specialist execution.
- Read path still `NONPRODUCTION_TEST_ADAPTER`, or `approved_source_adapter` false, or Scan reachable.
- `MasterSourceAuthenticity.NOT_ESTABLISHED` on the admission that is shown to a user as fact.
- Historical result admitted without result-level correlation, `SourceRecord.source_version` where the source has a version, and `QueryExecutionTrace.query_id` where a query id exists.
- `DecisionSpecialistRequest` built while any of the three host proofs is unissued.
- Verified-zero, low-risk-from-absence, SIGMET intersection from region text, or safety or diversion instructions in the render.
- `enable_ai_query_route` set true from this sprint.
- Real model API credentials used to finish the slice.

## 11. Launch readiness

| Agent | Ready to implement in parallel | Ready for production |
| --- | --- | --- |
| A. Ingress and principal | Yes. New module and owned files only. Closed issuers stay closed. | No. Ingress is not attested. |
| B. Grant and ledger | Yes. New module, fake DynamoDB client, existing conditional expressions. | No. Atomic consume and AWS provenance are absent. A ledger write is not exactly-once execution. |
| C. Scoped reads | Yes. New `GET_ITEM` / `QUERY` adapter behind the existing denials. | No. Production executor and source authentication stay off. |
| D. Specialist orchestration | Yes. Live Ops `run` with an injected provider. Historical and Decision stop at the current gates. | No. Dispatch flag stays false. Historical lineage and Decision proofs are unresolved. |
| E. Evidence and response | Yes. Join existing admission and renderer and keep claims closed. | No. Authenticity has no established state. |

All five agents can launch at the same time. None of the five is ready for production. Parallel launch optimizes implementation speed. It leaves authentication, ledger fencing, read scope, and aviation-evidence rules at their current closed settings until the integration owner and the operator clear section 10.
