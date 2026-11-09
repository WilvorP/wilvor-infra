# Sprint 3B MA7 execution threat matrix

Status: baseline review of `86f4d7bf47de32220dc9bbad9ba249bda25277bd` on `sprint3b-ma7-security`. This document is not operational readiness, production authorization, source authentication, request-coverage certification, or verified ingress.

Master execution authority remains `DENIED`. Production authority remains `DENIED`. Source authenticity remains `NOT_ESTABLISHED`. Request coverage remains `NOT_ESTABLISHED`. No specialist runner, model provider, AWS API, Terraform change, or endpoint was invoked. `run_synthetic_master_workflow` was not called.

Eight Sprint 3B implementation components are being built in parallel. This review is the independent security workstream. It does not add an execution module. Those eight components were not imported. No failing placeholder tests were added for them, and these tests do not assert that another agent's file is absent.

## Baseline that was inspected

| Area | Modules read and, where noted, executed |
|---|---|
| MA1 | `master_contracts.py` |
| MA2 | `master_routing.py` through `authorize_route` |
| MA3 | `SyntheticWorkerResult` only. The synthetic graph was not run |
| MA4 | `admit_master_evidence` and the response-policy path through corroboration |
| MA5 | `evaluate_master_response_policy`, `response_policy_for`, `render_master_response` |
| MA6 / Sprint 2 | preflight, execution scope, planning session, local capability registry, provenance classification, specialist adapter descriptors, and the Live Ops, Historical, and Decision binders |
| Sprint 3A | session-origin assessment, execution-policy assessment, execution handoff, Live Ops readiness, Historical readiness, Decision-target readiness, and source-read trace assessment |
| Specialist contracts | `SpecialistResult`, `HistoricalSpecialistRunResult`, `DecisionSpecialistRunResult`, `VerifiedZeroClaim`, `ExecutedToolInvocation`, `ToolResult` |
| Public catalogs | `READ_ONLY_TOOL_NAMES_BY_SPECIALIST` and `LIVE_OPS_REAL_INVOCATIONS`. The Live Ops, Historical, and Decision runner modules were not imported |

`MasterRouteExecutionAuthority` has no granted member. `MasterProductionAuthority.PRODUCTION` exists as a reserved value. `assess_master_execution_policy` and `evaluate_execution_preflight` reject it. `MasterRequestCoverage` has only `NOT_ESTABLISHED`.

## Invariants locked by the baseline tests

| # | Invariant | What the baseline does | Test |
|---|---|---|---|
| 1 | Matching IDs are not authentication | A second `authorize_route` for the same proposal equals the decision retained by `open_session` and is a different object. The registry rejects that invocation, a field-equal copy, and the invocation-id string with `copied_invocation_identifier`. `reject_authentication_claim` classifies the id as `MATCHING_IDENTIFIER` and stays unauthenticated. A policy candidate that copies the request, invocation, specialist-request, and correlation ids adds `MATCHING_IDENTITY_NOT_AUTHENTICATION`. A proposed trace that copies those ids records `MATCHING_IDENTIFIERS_NOT_ORIGIN` and observes no trusted runtime event. | `test_matching_identifiers_are_not_authentication` |
| 2 | An API Gateway-looking payload is not verified ingress | An HTTP API v2-shaped mapping, including `requestContext.authorizer.jwt.claims` and an `Authorization` header, is rejected. The mapping is `MATCHING_IDENTIFIER`. The header is `USER_TEXT`. Session origin does not treat the mapping as the retained context or as an application principal. Passing the mapping as a session record is `invalid_session`. A membership string copied from `sub` is `session_principal_not_accepted`. A policy candidate that copies `sub`, `AUTHORIZED`, and the gateway request id stays denied, with `POLICY_SPOOF_NOT_AUTHORITY` and `FOREIGN_INVOCATION`. | `test_api_gateway_payload_is_not_verified_ingress` |
| 3 | A planning-session record does not authorize multiple executions | A Live Ops plus Historical plan can associate one invocation object. The second invocation, a pair, and a replay are `multiple_invocations_rejected`. `propose_invocation_fanout` keeps `fanout_authorized` false. The execution handoff reports one selected invocation, keeps `multi_invocation_execution_supported` false, and does not authorize the unselected step. Promoting that flag is `multiple_invocations_rejected`. | `test_planning_session_record_does_not_authorize_multiple_executions` |
| 4 | Structural readiness cannot authorize dispatch | A Live Ops step whose only gate is `NOT_REQUIRED` has `ready_to_execute` and `structural_ready_to_execute` true. The adapter stays `BLOCKED_BEFORE_EXECUTION_AUTHORIZATION`. Policy records `STRUCTURAL_READINESS_NOT_PERMISSION`. Preflight returns `BLOCKED_BY_AUTHORIZATION`. A named `CONTRACT_READY_FOR_FUTURE_INTEGRATION` report stays non-executable. Live Ops readiness copies the ready flag, keeps `execution_blocked` true, and does not invoke the specialist. The handoff of that ready step stays unauthorized. | `test_structural_readiness_cannot_authorize_dispatch` |
| 5 | A caller can forge policy-looking objects | A dict with `ALLOW`, `execution_permitted`, and `PRODUCTION` is `invalid_policy_candidate`. A `MasterExecutionPolicyCandidate` carrying those claims, an authoritative region label, and unrestricted table access is denied with `POLICY_SPOOF_NOT_AUTHORITY`, `CALLER_POLICY_DECISION_NOT_AUTHORITY`, `UNRESTRICTED_TABLE_ACCESS`, and `UNTRUSTED_REGION_LABEL`. `caller_candidate_is_authority` stays false. Passing `execution_permitted=True` or `MasterProductionAuthority.PRODUCTION` into the assessment is rejected. | `test_caller_can_forge_policy_looking_objects` |
| 6 | Local capability consumption is not a production grant | `consume` can return `TRANSITION_RECORDED` while the result is false in boolean context. Production authority stays `DENIED` and `distributed_production_authorization` stays false. Replay is `REPLAY`. A proposed trace whose permit reference copies the local session id records `PERMIT_REFERENCE_NOT_CONSUMPTION` and does not permit dispatch. | `test_local_capability_consumption_is_not_a_production_grant` |
| 7 | A tool name in a public catalog is not permission to read | `search_current_hazards` is in the policy catalog and the Live Ops readiness catalog. Every matching real-invocation record has `authorized_to_invoke` false. Naming that tool, an unlisted `AircraftCurrent` resource, and `*` denies the read-scope requirement with `RECOGNIZED_TOOL_NAME_NOT_A_GRANT`, `RESOURCE_ESCALATION`, and `UNRESTRICTED_TABLE_ACCESS`. Live Ops readiness keeps `source_read_authorized` false and creates no tool result. | `test_public_tool_name_is_not_permission_to_read` |
| 8 | An apparently complete journal is not source authentication | A `ProposedDispatcherExecutionEvent` filled with matching ids, a permit reference, an adapter label, tool arguments, a source response, both clocks, a source version, entity ids, claimed-complete population, a trace reference, and an `EXECUTED` disposition still has an empty `observed_trusted_runtime_events`. The trace reference is `FORGED_EXECUTION_TRACE`. No obligation is `trusted_runtime_observed`. The assessment is false in boolean context. | `test_apparently_complete_journal_is_not_source_authentication` |
| 9 | A native specialist result is not automatically Master-admitted evidence | A Historical `SpecialistResult` with `VerifierOutcome.PASSED` and a `VerifiedZeroClaim` is `BINDING_UNRESOLVED`, with verifier and verified-zero reasons, because the native artifact does not carry the Master correlation. A `HistoricalSpecialistRunResult` is `REJECTED` as `HISTORICAL_RUN_NOT_FINAL_ARTIFACT`. A Decision run result is not `CONTRACT_ONLY` and does not authenticate the verifier. The rendered response does not copy the specialist answer. | `test_native_specialist_result_is_not_automatically_master_admitted_evidence` |
| 10 | A callsign or discovered aircraft does not authorize Decision execution | The same `N777WX` value on a typed observation, a callsign observation, a route hint, a comparison candidate, a `LiveOpsDiscovery` with `identity_resolved` true, and an entity hint leaves `selected_aircraft_id` unset. Discovery `selection_authorized` stays false. Trusted-target material is unavailable. Setting `callsign_is_aircraft_identity` is `callsign_not_authenticated_aircraft`. | `test_callsign_or_discovered_aircraft_does_not_authorize_decision_execution` |
| 11 | Missing or stale source records cannot support verified zero | Empty source records are `MISSING_SOURCE_RECORDS` and do not mint a verified-zero claim, even when the payload says `aircraft_count` is 0. A stale result plus a caller `VerifiedZeroClaim` records `STALE_DATA` and `FABRICATED_VERIFIED_ZERO` and does not establish zero. An exact cardinality of zero is an untrusted indication. Historical readiness with no observed result keeps `inferred_count` unset. Admission of a verified-zero claim stays unauthenticated. | `test_missing_or_stale_source_records_cannot_support_verified_zero` |
| 12 | Timeout, cancellation, and partial coverage are not complete success | `SyntheticWorkerResult` rejects `evidence_verified=True` for `TIMED_OUT`, `CANCELLED`, `PARTIAL`, and `SYNTHETIC_COMPLETED`. Admission rejects each as `UNSUPPORTED_ARTIFACT`. A partial tool result with `PROVIDER_TIMEOUT` proposes a timeout and does not establish zero or fulfillment. A success status paired with provider timeout, runtime timeout, or cancellation conflicts and stays unobserved. `MasterRequestCoverage` has only `NOT_ESTABLISHED`. An empty bundle renders the insufficient-evidence sentence. | `test_timeout_cancellation_and_partial_coverage_are_not_complete_success` |
| 13 | Synthetic MA3 results cannot become MA4 operational evidence | A `SYNTHETIC_COMPLETED` result that copies Master ids is `REJECTED` with `UNSUPPORTED_ARTIFACT`. The parallel bundle refuses it with `synthetic_result_not_evidence`. Provenance classification refuses it with `unsupported_provenance_subject`. Historical readiness does not store it, does not finalize it, and issues no evidence. | `test_synthetic_ma3_results_cannot_become_ma4_operational_evidence` |
| 14 | MA5 cannot produce unsupported factual aviation answers | A Historical artifact whose answer names an aircraft, a SIGMET, a risk level, and a zero count still renders one of the four fixed sentences. The unsupported policy sentence is fixed and does not contain that answer. A handoff whose status text is the factual sentence is `invalid_status_text`. `claims_admitted=True` is `operational_claim_not_admitted`. | `test_ma5_cannot_produce_unsupported_factual_aviation_answers` |

## Limits that remain on this baseline

These are properties of the current contracts. The tests lock today's behavior. They do not make a future module safe until the integration tests below exist.

1. **Every supplied mapping is the same kind of rejected claim.** `reject_authentication_claim` classifies a dict as `MATCHING_IDENTIFIER`. It does not parse issuer, audience, subject, expiry, route, or method, and it has no attested-ingress state. A later gateway parser that accepts a structurally valid `requestContext.authorizer.jwt` as verified ingress would leave this baseline.

2. **One session can remember one invocation.** `associate_invocation` and `inspect_master_execution_handoff` keep the other planned steps as labels. Eligibility of those steps is not represented. A later scheduler that treats "eligible" as permission to run the unselected invocation would leave this baseline.

3. **`structural_ready_to_execute` and `CONTRACT_READY_FOR_FUTURE_INTEGRATION` are easy to misread.** Both can be true or named while dispatch stays false. Live Ops readiness copies the ready flag and still blocks. A later bridge that dispatches on either flag would leave this baseline.

4. **A policy candidate is accepted so it can be denied.** `execution_permitted=True` on `MasterExecutionPolicyCandidate` is stored and then denied. The same flag passed to `assess_master_execution_policy` is rejected. A later guard that reads the candidate boolean instead of the assessment would leave this baseline.

5. **Capability consumption is a boolean trap.** `LocalConsumptionResult` for `TRANSITION_RECORDED` is never true in boolean context, and `outcome is TRANSITION_RECORDED` is true. Consumption does not consult the planning session, the policy assessment, or the source-read trace. A later dispatcher that runs a tool because the outcome enum matches would leave this baseline.

6. **Catalog membership is data, not a grant.** The policy module and the Live Ops readiness module restate public tool names. An unlisted name is escalation, a wildcard is unrestricted access, and a listed name is `RECOGNIZED_TOOL_NAME_NOT_A_GRANT`. A later allowlist that treats "name is in the catalog" as table permission would leave this baseline.

7. **The current journal is a proposed event.** `ProposedDispatcherExecutionEvent` can be filled until it looks complete, including `execution_trace_ref` and `MasterDisposition.EXECUTED`. `observed_trusted_runtime_events` stays empty, and there is no parameter that fills it. A later runtime journal that copies that object into an observed-event tuple would leave this baseline.

8. **`ToolResult.data` is not a count.** A payload that says `aircraft_count` is 0 does not mint a `VerifiedZeroClaim` and does not add `FABRICATED_VERIFIED_ZERO` by itself. A later reader that parses `data` into an aircraft, hazard, risk, or zero would fabricate a fact this classifier does not read.

9. **Native correlation is absent, and `CONTRACT_ONLY` remains a structural status.** Planned Historical and Decision admissions stay unresolved because the native artifacts do not carry Master correlation fields. A hand-built authorization that omits those ids can still reach `CONTRACT_ONLY` on the existing admission function. That status does not set `evidence_verified`. A later bridge that copies Master ids onto a caller-supplied result and treats `CONTRACT_ONLY` as authenticated evidence would leave this baseline.

10. **`identity_resolved` is not target authorization.** A unique Live Ops discovery can say `identity_resolved` true while `selection_authorized` is false and Decision readiness leaves `selected_aircraft_id` unset. A later resolver that promotes a unique callsign match, a comparison candidate, or a typed id would leave this baseline.

11. **Timeout and partial outcomes have no success member that grants coverage.** Synthetic timeout, cancellation, partial completion, and synthetic completion are the same unsupported artifact at admission. `MasterRequestCoverage` cannot represent complete coverage. A later fanout state named `completed` that sets request coverage, or a partial read that becomes a zero, would leave this baseline.

12. **MA5 has no answer field.** The renderer copies a closed sentence. A later renderer that interpolates `SpecialistResult.answer`, a callsign, a hazard name, or a count would leave this baseline.

No existing interface had to be bypassed. The tests use the public constructors and functions above.

## Threat matrix for the Sprint 3B implementation components

Add these tests after the components are combined. Do not add them as failing tests while the components are absent.

The combination must still be tested for authentication bypass, unauthorized table access, replay, idempotency, concurrency, stale data, wrong-region scope, and fabricated verified zero. Every later test must keep execution authority `DENIED`, production authority `DENIED`, source authenticity `NOT_ESTABLISHED`, and request coverage `NOT_ESTABLISHED` until a separately reviewed host grant exists. None of those tests may promote a gateway payload, a catalog name, a journal entry, a fake specialist result, or a synthetic result to authenticated aviation evidence.

| Component | Threat that appears only once the component exists | Tests to add after integration | Baseline that must still hold |
|---|---|---|---|
| Protected `POST /ai/query` route (`modules/operational_api`) | The route can be enabled without a JWT authorizer, can fall back to `authorization_type` `NONE`, can target the existing GET operational Lambda, or can grant the AI Lambda a broader invoke permission than the route. A disabled default can be mistaken for an already authenticated production route. | `route_stays_absent_until_issuer_audience_and_lambda_are_configured`; `enabled_route_rejects_none_authorization`; `ai_invoke_permission_is_bound_to_the_route`; `existing_get_routes_do_not_gain_an_unauthenticated_post` | Invariant 2 |
| `master_gateway_identity.py` | A forged `Authorization` header, a copied `sub`, a wrong issuer or audience, an expired claim, a wrong method or route, or a directly constructed HTTP API v2 event can be reported as an application principal. Matching the gateway request id to the Master request id can be treated as authentication. A structurally valid claim can be promoted into execution permission before the Lambda attestation boundary exists. | `forged_header_subject_and_scope_do_not_authenticate`; `wrong_issuer_audience_method_and_route_are_rejected`; `expired_and_malformed_claims_are_rejected`; `constructed_event_is_not_attested_ingress`; `matching_request_id_does_not_grant_execution` | Invariants 1 and 2 |
| `master_execution_fanout.py` | Tracking every invocation can be mistaken for authorizing every invocation. An eligible or in-progress scheduling state can be treated as a dispatch permit. A second claim of the same step can double-execute. Cancellation, a deadline, or a dependency failure can be mapped to complete success or a verified zero. Concurrent claims can both win. A fake worker result can be stored as specialist evidence. | `eligibility_is_not_execution_authorization`; `second_claim_of_one_step_is_rejected`; `concurrent_claims_have_one_winner`; `cancellation_deadline_and_dependency_failure_are_not_success`; `fake_worker_result_is_not_admitted_evidence`; `decision_fanout_stays_inside_max_decision_steps` | Invariants 3, 12, and 13 |
| `master_read_resource_guard.py` | A caller-supplied allowlist, region label, or policy boolean can satisfy the guard. A public tool name, an unlisted table, or a wildcard can become a DynamoDB read. A historical interval above the existing ceiling, a wrong region, or an unbounded runtime can pass. The guard can be wired as the production permit issuer. Replay of an allowed check can be treated as a new grant. | `caller_allowlist_and_policy_boolean_are_not_authority`; `catalog_name_unlisted_table_and_wildcard_stay_denied`; `wrong_region_and_arbitrary_historical_interval_stay_denied`; `guard_result_is_not_a_production_permit`; `repeated_allow_check_is_not_a_second_grant` | Invariants 4, 5, and 7 |
| `master_runtime_read_journal.py` | A completed journal entry, a full set of clocks, a source version, or a pagination record can be treated as source authentication. An empty fake result can mint a verified zero. A timeout, cancellation, or truncated page can be rewritten as success. A stale injected clock can be accepted as a fresh source observation. The journal can be confused with the proposed dispatcher event. | `completed_journal_does_not_authenticate_the_read`; `empty_fake_result_does_not_mint_verified_zero`; `timeout_cancellation_and_truncation_stay_incomplete`; `stale_injected_clock_is_not_source_freshness`; `proposed_event_is_not_a_journal_observation` | Invariants 6, 8, 11, and 12 |
| `master_live_ops_native_bridge.py` | The bridge can dispatch `LiveOpsSpecialist.run` because the binding and readiness object exist. Model or user hints can become tool selectors. A regional string can be reported as SIGMET coverage. A native failure, cancellation, or partial result can become an impact count or a verified zero. A fake table read can be returned as authenticated Live Ops evidence. A replay of the same request id can be accepted as a second source read. | `binding_and_readiness_do_not_dispatch_live_ops`; `hints_are_not_authorized_selectors`; `region_string_is_not_sigmet_coverage`; `native_failure_cancellation_and_partial_are_not_zero`; `fake_table_read_is_not_authenticated_evidence`; `replayed_request_identity_is_not_a_second_read` | Invariants 4, 7, 9, and 12 |
| `master_historical_native_bridge.py` | The bridge can copy the plan window into tool arguments, copy the requested evaluation time into `evaluated_as_of_utc`, or call finalization to produce a `SpecialistResult`. A missing row set, a truncated query, or a stale source version can become a verified zero. A caller-supplied native result can be treated as authenticated execution because its ids were overwritten with Master ids. Concurrent Historical invocations can share one window or one query id. | `plan_window_is_not_copied_into_tool_arguments`; `requested_time_is_not_authenticated_source_time`; `intermediate_run_is_not_finalized_into_evidence`; `missing_truncated_and_stale_results_are_not_zero`; `caller_supplied_result_stays_unauthenticated`; `distinct_historical_invocations_keep_distinct_windows` | Invariants 9, 11, 12, and 13 |
| `master_decision_target_resolver.py` | A unique callsign, a Live Ops discovery, a comparison candidate, or a typed aircraft id can become the authorized Decision target. Stale aircraft state or an out-of-scope aircraft can be selected. A missing risk record can be rendered as low risk. A persisted recommendation can be read without a separate authorization. The resolver can build a runnable `DecisionSpecialistRequest` or exceed `MAX_DECISION_STEPS`. | `unique_callsign_and_discovery_do_not_select_a_target`; `stale_and_out_of_scope_aircraft_are_rejected`; `missing_risk_is_not_low_risk`; `persisted_recommendation_requires_separate_authorization`; `resolver_does_not_build_a_runnable_decision_request`; `decision_fanout_stays_at_one_invocation` | Invariants 10 and 14 |
| Combined host | Wiring the route, gateway parser, fanout, guard, journal, and three bridges together creates cross-component failures that no single module can show. A gateway claim can bypass the guard. A fanout claim can replay a consumed capability. Two concurrent invocations can pass the guard for one table grant. A journal entry from a fake bridge can be admitted as MA4 evidence. A wrong-region Live Ops read can be paired with a Historical window and a Decision target to fabricate a verified zero. Idempotent replay of the same Master request can be reported as a second authenticated read. | `gateway_claim_does_not_bypass_the_read_guard`; `fanout_claim_does_not_replay_a_consumed_capability`; `concurrent_invocations_cannot_share_one_table_grant`; `journal_from_a_fake_bridge_is_not_ma4_evidence`; `wrong_region_scope_cannot_authorize_live_ops_or_decision`; `stale_journal_cannot_support_verified_zero`; `idempotent_replay_is_not_a_second_authenticated_read`; `timeout_or_partial_fanout_cannot_render_a_factual_answer` | Invariants 1 through 14 |

## Independently observed results

Command, using `wilvor-infra/.venv`, from the `sprint3b-ma7-security` worktree:

`python -m pytest tests/contracts/test_sprint3b_ma7_execution_boundary.py tests/contracts/test_sprint3b_ma7_execution_import_isolation.py -q -p no:cacheprovider`

Result: 16 passed.

The same interpreter then ran the existing MA7 matrix, the Sprint 3A MA7 regression, and the Master contract regressions for parallel contracts, response policy and rendering, preflight, execution scope, planning session, gate resolution, local capability, provenance, source-read trace, specialist adapters, Live Ops binding and readiness, Historical binding and readiness, Decision binding and readiness, execution policy, session origin, execution handoff, evidence admission, and offline integration.

Result: 601 passed.

HEAD remained `86f4d7bf47de32220dc9bbad9ba249bda25277bd` on `sprint3b-ma7-security`. No commit was created.

## Import isolation

`tests/contracts/test_sprint3b_ma7_execution_import_isolation.py` parses the boundary tests and imports them in a fresh interpreter.

Modules that must not load:

- `wilvor_ai.master_gateway_identity`
- `wilvor_ai.master_execution_fanout`
- `wilvor_ai.master_read_resource_guard`
- `wilvor_ai.master_runtime_read_journal`
- `wilvor_ai.master_live_ops_native_bridge`
- `wilvor_ai.master_historical_native_bridge`
- `wilvor_ai.master_decision_target_resolver`
- `boto3`, `botocore`, `anthropic`, `openai`
- `wilvor_ai.historical_specialist`, `wilvor_ai.decision_specialist`, `wilvor_ai.live_ops`, `wilvor_ai.live_ops_adapter`
- `wilvor_operational`

The protected route is Terraform, not a Python import. Importing `SyntheticWorkerResult` loads the existing MA3 module and therefore LangGraph. That import does not run the graph. LangGraph is an existing MA3 dependency, not one of the unfinished Sprint 3B modules.

## Security boundaries still unavailable

- Verified API Gateway invocation provenance and an application-owned principal
- A production execution grant and a production policy engine
- Authorized multi-invocation execution
- An approved read-only resource scope, region authority, and execution deadline
- A trusted runtime observation of a source read
- Production specialist dispatch for Live Ops, Historical Analytics, and Decision
- Authenticated source-read provenance and an established verified zero
- A trusted Decision target and a factual Master answer

## Integration dependencies

The baseline tests do not depend on the eight components above. After those components are combined, the tests in the matrix become required before any host path can be described as execution authorization. The gateway parser still needs the protected route and a reviewed Lambda attestation boundary before a principal can be considered for production. The fanout, guard, journal, and specialist bridges still need that principal and a separate execution grant before a tool call can be treated as a source read.

## File scope

Created, and left uncommitted:

- `functions/shared/wilvor_ai/SPRINT3B_MA7_EXECUTION_THREAT_MATRIX.md`
- `tests/contracts/test_sprint3b_ma7_execution_boundary.py`
- `tests/contracts/test_sprint3b_ma7_execution_import_isolation.py`

No existing Wilvor source module or test file was modified. No other Sprint 3B stage was started.
