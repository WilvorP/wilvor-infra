# Sprint 3B.2 trusted host interface

Schema: `wilvor.ai.master_specialist_runtime_contract.v1`

This note is the frozen interface. Do not import `wilvor_ai.master_specialist_runtime_contract` until that module is merged. Copy these field names and closed flags. A valid record, a copied envelope, and a descriptive allow are not a production grant.

Baseline: `5d10c582091997732b97e496940af9fa18afd745`. Python 3.11.

## Trust distinctions

| Distinction | Meaning | This sprint |
| --- | --- | --- |
| `DESCRIPTIVE` | Names a shape or hint | Constructable |
| `EVALUATED` | A deterministic check of supplied inputs | Constructable |
| `HOST_ISSUED` | Reviewed application host issued it | Unavailable |
| `CONSUMED` | A host-issued artifact was consumed | Unavailable |
| `SOURCE_AUTHENTICATED` | An approved adapter completed a source operation | Unavailable |

Closed on every record: `production_authority=DENIED`, `execution_authority=DENIED`, `source_authenticity=NOT_ESTABLISHED`, `request_coverage=NOT_ESTABLISHED`.

`PRODUCTION_HOST` is `None`. `issue_principal`, `issue_grant`, `invoke_specialist`, `admit_evidence`, and `construct_decision_native_request` always fail.

## Clocks

| Field | Role | Baseline name |
| --- | --- | --- |
| `accepted_at_utc` | `APPLICATION_REQUEST` | `application_request_time_utc` |
| `deadline_utc` | `DEADLINE` | `deadline_utc` |
| `requested_evaluation_time_utc` | `REQUESTED_EVALUATION` | `requested_evaluation_time_utc` |
| `source_observation_time_utc` | `SOURCE_OBSERVATION` | native source observation, not the request clock |
| `evaluation_time_utc` | `EVALUATION` | not `evaluated_as_of_utc` and not the requested instant |
| `independent_observation_time_utc` | `INDEPENDENT_OBSERVATION` | budget/cancellation observation |

Whole-second UTC `YYYY-MM-DDTHH:MM:SSZ`. Equal text does not equate two roles. `evaluation_copied_from_request` stays false.

## Identity

`HostInvocationIdentity` fields:

`master_request_id`, `planning_session_id`, `step_id`, `invocation_id`, `specialist_request_id`, `correlation_id`, `specialist`, `purpose`, `capability`, `requirement`

- `planning_session_id` maps to `MasterPlanningSessionRecord.session_id` and `MasterSchedulingObservation.session_id`.
- `requirement` is `MasterRequirement`.
- Those six identity strings are pairwise distinct, without whitespace.
- Allowed pairs are the execution-scope pairs: Live Ops `DISCOVERY` or `ANSWER` with `RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT`; Historical `ANSWER` with `RETRIEVE_HISTORICAL_ANALYTICS`; Decision `ANSWER` with `EXPLAIN_EXISTING_RISK` or `EXPLAIN_EXISTING_RECOMMENDATIONS`.
- Missing required fields fail as `identity_missing`. Disagreeing candidates fail as `identity_ambiguous`. Collapsed ids fail as `request_identity_not_distinct`.
- Status on a bound identity is `DESCRIBED` only.

## Request envelope

`SpecialistRequestEnvelope` embeds one identity plus:

`accepted_at_utc`, `deadline_utc`, `idempotency_key`, `max_model_turns`, `max_tool_calls`, `max_executions`, `requested_evaluation_time_utc`, `principal_ref`, `policy_revision`, `resource_scope`, `region_scope`, `historical_window_start_utc`, `historical_window_end_utc`, `decision_target_scope`

Hints, not authority: `principal_ref`, `policy_revision`, `resource_scope`, `region_scope`, the historical window, and `decision_target_scope`.

`authorization_attached`, `hints_are_authority`, and `historical_window_is_tool_argument` stay false. Deadline is strictly after `accepted_at_utc`. `idempotency_key` is distinct from the six identity strings. `max_executions` is the server budget (`1`). Turns and tool calls are at least 1 and at most `server_read_budget`. Historical windows are required only for Historical, are not tool arguments, and are at most 7 days (`MAX_HISTORICAL_WINDOW`). No ARN and no wildcard in resource or region hints.

## Other records

Principal requirement: `identity`, `principal_ref_hint`, `required_issuer="reviewed_application_host"`. `satisfied`, `host_issued`, `edge_jwt_is_attestation`, and `direct_lambda_invoke_verified` stay false. Status `NOT_ESTABLISHED`.

Effective execution scope: identity, `temporal_scopes`, copied resource and region hints, historical window, `decision_target_scope`. `max_route_steps=4`, `max_decision_steps=1`. `historical_window_bound_to_tool_arguments`, `decision_target_established`, `region_scope_is_geographic_coverage`, `scope_authorized`, `redeemable`, and `execution_permission_granted` stay false. Temporal scopes match routing: Historical `(HISTORICAL,)`, Live Ops `(CURRENT,)`, Decision `(CURRENT,)` or `(PERSISTED,)` or `(CURRENT, PERSISTED)`.

Approved policy decision: `identity`, `policy_revision`, `policy_status`. Statuses: `ALLOW_DESCRIPTIVE` maps to `EVALUATED_ALLOW_DESCRIPTIVE`, `DENY` to `EVALUATED_DENY`, `NOT_ESTABLISHED` to `NOT_ESTABLISHED`. Posture is `EVALUATED`. `usable_as_production_grant` stays false.

Issued grant reference: `identity`, `grant_ref`, `policy_revision`, `effective_scope`. `issued`, `self_authorizing`, `usable_as_production_grant`, and `executable` stay false. `copied_from_envelope` may be true and still does not issue the grant. `grant_ref` is distinct from the six identity strings. The scope identity must equal the reference identity.

Ledger reservation: `identity`, `reservation_ref`, `idempotency_key`. `consumed` and `grants_execution` stay false. `reservation_ref` and `idempotency_key` are distinct from each other and from the six identity strings.

Budget and cancellation: identity, ceilings, `accepted_at_utc`, `deadline_utc`, `independent_observation_time_utc`, `cancellation` (`NOT_CANCELLED`, `CANCELLED`, `DEADLINE_EXPIRED`), observed turn and tool counts. `budget_exhausted` is true only when an observed count is at least the ceiling. `enforced_at_tool_execution` and `caller_budget_is_authority` stay false. Enforcement belongs at actual tool execution.

Tool execution observation: identity, `tool_name`, `operation`, `observation_status`, `argument_names`, optional `source_identity`, `query_or_record_id`, `source_version`, `source_observation_time_utc`, `evaluation_time_utc`, `completeness`, `pagination_complete`, `truncated`, `missing_record`, `empty_page`, `impact_count`, `regional_name_match`. Operation is `GET_ITEM` or `QUERY` and must be on `server_read_resources` for that tool. Tool and argument names come from `server_read_tools` and `server_read_argument_names`. `SCAN`, unknown tools, writes, and SQL argument names `sql`, `statement`, and `query_string` fail. `approved_runtime_adapter`, `aviation_evidence`, `verified_zero`, and `sigmet_geographic_coverage` stay false. A missing record, empty page, absent impact count, or regional string does not become a `VerifiedZeroClaim`.

Native specialist execution result: identity, `stage`, native status enum, `observed`, `present_host_fields`, `missing_host_fields`. The native object is not retained. `result_retained`, `host_identity_complete`, `matching_identifier_is_authorization`, `source_authenticated`, and `aviation_evidence` stay false. A mapping is a serialized record, not a native result.

Provenance handoff: identity, the clocks above, `tool_observations`, optional `native_result`. `admitted`, `verified_zero`, and `evaluation_copied_from_request` stay false. Status `NOT_ESTABLISHED`.

## Allowed statuses

Record statuses that may appear on a non-failure object:

- `DESCRIBED`
- `EVALUATED_ALLOW_DESCRIPTIVE`
- `EVALUATED_DENY`
- `NOT_ESTABLISHED`

Failure observations only:

- `IDENTITY_MISSING`
- `IDENTITY_AMBIGUOUS`
- `REJECTED_FALSE_PROOF`

No status named `PRODUCTION`, `GRANTED`, or `PERMITTED` exists. A policy-check result, including `ALLOW_DESCRIPTIVE` and a read-guard `WITHIN_ALLOWLIST`, is not a grant.

## False proof

`observe_authorization_proof(None)` is `NOT_ESTABLISHED`. Any other proof is `REJECTED_FALSE_PROOF` and issues nothing. Kinds: boolean, string claim, serialized dataclass, same-id object, policy check, environment claim (`ENVIRONMENT`, `AWS_*`, `WILVOR_*`), JWT dictionary (`jwt` or `requestContext`), authorization header, event body, native result, and any other caller object. API Gateway JWT validation is an edge control, not attestation on the Lambda event. A direct invoke can carry a forged gateway-looking event. `direct_lambda_invoke_verified` stays false. An environment value that says test mode does not open a seam.

## Native entrypoints

Do not call these from this contract.

| Specialist | Owner | Call | Direct result | Final result |
| --- | --- | --- | --- | --- |
| `LIVE_OPS` | `LiveOpsSpecialist.run(request: LiveOpsRunRequest, context: LiveOpsTrustedContext)` | module `wilvor_ai.live_ops_specialist` | `LiveOpsRunResult` | `LiveOpsRunResult` |
| `HISTORICAL_ANALYTICS` | `HistoricalAnalyticsSpecialist.run(request: SpecialistRequest, context: HistoricalSpecialistTrustedContext)` | module `wilvor_ai.historical_specialist` | `HistoricalSpecialistRunResult` | `SpecialistResult` via `finalize_historical_specialist_run` |
| `DECISION` | `DecisionSpecialist.run(request: DecisionSpecialistRequest, runtime: DecisionToolsRuntime)` | module `wilvor_ai.decision_specialist` | `DecisionSpecialistRunResult` | `DecisionSpecialistRunResult` |

Native status enums, not a second vocabulary: `LiveOpsRunStatus`, `SpecialistRunStatus`, `SpecialistStatus`, `DecisionSpecialistRunStatus`.

`DecisionToolsRuntime` fields are `tables`, `now_epoch`, `query_timestamp_utc`, `correlation_id`. Its module is `wilvor_ai.decision_tools`. Do not import that module from this contract. `correlation_id` there is optional and is not copied onto `DecisionSpecialistRunResult`. The parameter name is `runtime`, not `context`.

`LiveOpsTrustedContext` carries `tables`, `now_epoch`, `correlation_id`, turn and tool ceilings, `clock`, `deadline_epoch`, and `cancelled`. This contract does not construct it. `now_epoch` is not `accepted_at_utc`.

## Historical contract migration

Unresolved. Do not add these fields to the native types in this sprint. `source_version` owner is `SourceRecord`. `query_id` owner is `QueryExecutionTrace`. `native_type_changed` stays false.

The gap list matches `historical_native_result_correlation_gaps()`. This contract restates it locally: importing `master_historical_execution_readiness` or `master_historical_binding` also loads `wilvor_ai.historical_answer_renderer` through `master_evidence_corroboration`, and loading that module is not permission to call the finalizer. `HistoricalSpecialistRunResult` and `SpecialistResult` still omit `master_request_id`, `specialist_request_id`, `correlation_id`, `application_request_time_utc`, `requested_evaluation_time_utc`, `source_version`, and `query_id`. `SpecialistRequest` has no `start_utc` or `end_utc`. `HistoricalSpecialistTrustedContext` has only `as_of_utc`. `ToolResult.correlation_id` can be present and is not a result-level correlation contract. `evaluated_as_of_utc` is not backfilled from `requested_evaluation_time_utc`. The unbound plan window is not a native tool argument. `finalize_historical_specialist_run` is not called.

Host identity also requires `planning_session_id`, `step_id`, and `invocation_id`, which those native results do not carry.

## Decision target

Establish the target before constructing `DecisionSpecialistRequest`. Required host proofs, issuer `unavailable` in the current resolver:

- `SOURCE_CURRENTNESS`
- `ALLOWED_TARGET_POPULATION`
- `SEPARATE_PERSISTED_RECOMMENDATION_AUTHORIZATION`

`MasterDecisionTargetResolution.decision_request` stays `None`. `release_master_decision_target` refuses every caller-built native request. A unique injected candidate is `UNIQUE_CANDIDATE_NOT_ELIGIBLE`, not a target. These comparison fields are not the native target:

- `MasterInvocationAuthorization.expected_aircraft_id`
- `MasterInvocationAuthorization.expected_recommendation_id`
- `MasterPlanningContext.target_candidate_aircraft_id`
- `MasterPlanningContext.target_candidate_recommendation_id`

Native mode is `AIRCRAFT` (`aircraft_id` only) or `DIRECT_PERSISTED` (`recommendation_id` only).

## Ceilings and reads

`MAX_ROUTE_STEPS=4`. `MAX_DECISION_STEPS=1`. Historical interval at most 7 days. Budgets, tools, argument names, and resources come from `server_read_budget`, `server_read_tools`, `server_read_argument_names`, and `server_read_resources`. Reads are `GET_ITEM` and `QUERY` only. No DynamoDB Scan, arbitrary Athena SQL, writes, autonomous aviation actions, or flight-control instructions.

## Integration

Principal: consume `PrincipalRequirement`. Do not treat the event body, `Authorization`, `requestContext.authorizer.jwt`, a principal string, or a boolean as the issuer. Issuance stays unavailable until the reviewed host and the Lambda invoke path are independently established.

Policy: emit `ApprovedPolicyDecision` with the three statuses above. Do not convert `ALLOW_DESCRIPTIVE` or `MasterReadCheckStatus.WITHIN_ALLOWLIST` into a grant.

Grants: emit `IssuedGrantReference` with `issued` false until the reviewed host issues it. Do not accept the envelope, a serialized dataclass, or a same-id object as the grant.

Ledger: emit `LedgerReservation` with `consumed` false. Consumption is a later host step, not a copy of the reservation.

Budget: emit `BudgetCancellation` using `server_read_budget`. Keep the independent observation clock off the request clock. Enforce the ceiling and cancellation inside the tool execution adapter, not only while parsing.

Read execution: emit `ToolExecutionObservation` only for allowlisted `GET_ITEM` or `QUERY` calls. Leave evidence, verified zero, and SIGMET coverage false.

Provenance: emit `ProvenanceHandoff`. Admit evidence only after an approved runtime adapter completes a source operation with source identity, query or record identity, source version where available, both clocks, completeness, pagination, truncation, and the effective authorized scope.

Adapter: use the entrypoint table. Do not call `run` from a descriptive record. For Historical, keep the intermediate `HistoricalSpecialistRunResult` distinct from the final `SpecialistResult`. For Decision, do not build `DecisionSpecialistRequest` until the three host proofs exist.

Population and gateway: `principal_ref` and route hints stay untrusted. Gateway JWT validation does not attach cryptographic attestation to the Lambda event.

## Still closed

Principal issuance, grant issuance, specialist dispatch, evidence admission, native type changes, production authority, source authenticity, request coverage, redeemable scope, and verified-zero claims from absence.
