# Sprint 3A MA7 independent security review

Status: baseline review of `7073b3a4e9ac5f2380beb897f4be622e498ccdb8` on `sprint3a-ma7-safety`. This document is not operational readiness, production authorization, source authentication, or request-coverage certification.

Master execution authority remains `DENIED`. Production authority remains `DENIED`. Source authenticity remains `NOT_ESTABLISHED`. Request coverage remains `NOT_ESTABLISHED`. No specialist, model provider, AWS API, Terraform change, or endpoint was invoked. `run_synthetic_master_workflow` was not called.

The seven Sprint 3A modules below are absent from this worktree and were not imported. No failing placeholder tests were added for them.

## Baseline that was inspected

| Area | Modules read and, where noted, executed |
|---|---|
| MA1 | `master_contracts.py` |
| MA2 | `master_routing.py` through `authorize_route` |
| MA3 | `SyntheticWorkerResult` only. The synthetic graph was not run |
| MA4 | admission, corroboration through `evaluate_master_response_policy`, and comparability request binding |
| MA5 | `evaluate_master_response_policy`, `render_master_response` |
| MA6 | preflight, execution scope, planning session, gate inspection, local capability registry, provenance classification, specialist adapter descriptors, and the Live Ops, Historical, and Decision binders |
| Existing MA7 | `test_ma7_master_safety_matrix.py` and its import-isolation test |

The existing MA7 matrix stays on the parallel-contract handoff. It does not open a planning session, bind an execution scope, consume a local capability, inspect route gates, or classify provenance. The new regression file covers those boundaries. Existing modules and tests were not modified.

`MasterRouteExecutionAuthority` has no granted or production member. `MasterProductionAuthority.PRODUCTION` exists as a reserved value. `evaluate_execution_preflight` rejects it with `production_authority_not_issued`.

## Invariants locked by the new baseline tests

| # | Invariant | What the baseline does | Regression |
|---|---|---|---|
| 1 | Matching IDs are not authentication | A second `authorize_route` for the same proposal yields an equal invocation that is not the object retained by `open_session`. The registry rejects it with `copied_invocation_identifier`. A field-equal copy is likewise rejected. The parallel bundle accepts the field-equal authorization, and admission still rejects the attached intermediate run. A Live Ops `COMPLETED` observation whose invocation id matches is `COMPLETED_WITH_INSUFFICIENT_EVIDENCE`, with `authoritative_terminal` false. Provenance records `MATCHING_IDENTIFIERS_NOT_ORIGIN` and stays `NOT_ESTABLISHED`. The execution scope keeps `MATCHING_IDENTIFIER_NOT_TRUST`. | `test_matching_identifiers_are_not_authentication` |
| 2 | Structurally ready plans cannot execute | A Live Ops step with gate `NOT_REQUIRED` has `ready_to_execute` true. The session, scope, preflight, and Live Ops binding all keep dispatch false. Naming `CONTRACT_READY_FOR_FUTURE_INTEGRATION` on a report still binds with `dispatch_permitted` false and every provenance requirement missing. `evaluate_execution_preflight` returns `BLOCKED_BY_AUTHORIZATION` and does not return the named-ready state. A `SYNTHETIC_COMPLETED` result is refused as evidence. | `test_structurally_ready_plan_cannot_execute` |
| 3 | Process-local session ownership is not an authenticated principal | The minted session id is not the master request id or the correlation id. A string membership is `session_principal_not_accepted`. A new handle that copies the session id is `copied_session_identifier`. Scope prerequisites still include `AUTHENTICATED_APPLICATION_PRINCIPAL` and `TRUSTED_PLANNING_SESSION_ORIGIN`. A capability whose consumer and session labels copy the session id does not authorize execution. | `test_process_local_session_is_not_an_authenticated_principal` |
| 4 | Single-use registry transitions are not dispatch permits | `consume` can return `TRANSITION_RECORDED` while the result is false in boolean context and `dispatch_permitted` is false. Replay is `REPLAY`. `record_operation_failure` does not reactivate the capability. `close_session` revokes a still-issued capability and does not grant execution. Closing the planning session does not make the retained plan executable. Gate inspection of that plan stays unsatisfied. | `test_single_use_registry_transition_is_not_a_dispatch_permit` |
| 5 | Caller-reported specialist failures are not authoritative gate outcomes | `dependency_verdict(LiveOpsRunStatus.TOOL_FAILED)` returns `FAILED` and `REQUIRED_TERMINAL_FAILURE`. `inspect_route_gates` keeps the verdict as a diagnostic and downgrades the edge effect to `REQUIRED_PENDING_TRUSTED_EVIDENCE`. `authoritative_terminal`, `dependency_terminal`, and `authoritative_satisfaction` stay false. The plan object is unchanged. `claimed_satisfied=True` and `evidence_sufficient=True` are rejected. | `test_caller_reported_specialist_failure_is_not_an_authoritative_gate_outcome` |
| 6 | Supplied ToolResults do not establish source authenticity | A `NOT_FOUND` tool result whose payload says `source_authenticated: true` and `aircraft_count: 0` is `UNSUPPORTED_ARTIFACT` at admission and `unsupported_artifact` on the parallel bundle. Provenance leaves every requirement `UNRESOLVED` or `UNSATISFIED`. `ProvenanceRequirementStatus` has no `SATISFIED` member. The payload is not read as a population count. | `test_supplied_tool_results_do_not_establish_source_authenticity` |
| 7 | Historical intermediate results are not final evidence | The parallel bundle accepts a `HistoricalSpecialistRunResult` as the historical artifact type. Admission rejects it with `HISTORICAL_RUN_NOT_FINAL_ARTIFACT`. Response policy stays `INSUFFICIENT_EVIDENCE`, not `UNAVAILABLE`. The historical binder leaves `historical_run`, `specialist_result`, `evaluated_as_of_utc`, and `inferred_count` unset. A caller-reported `PROPOSED_CLAIMS` dependency is `REQUIRED_INTERMEDIATE`, not a terminal or final gate. | `test_historical_intermediate_results_are_not_final_evidence` |
| 8 | Decision comparison candidates cannot authorize targets | The same aircraft id on the planning context, `expected_aircraft_id`, the Decision artifact, and a comparison hint remains a comparison candidate. Admission is `BINDING_UNRESOLVED` with `EXPECTED_AIRCRAFT_IS_COMPARISON_CANDIDATE`. The Decision binding keeps `trusted_aircraft_id` none. `comparison_candidates_authorize_target=True` is `target_authorization_not_granted`. The application-target gate stays `UNRESOLVED_EXECUTION_POLICY`. The rendered response does not contain the aircraft id. | `test_decision_comparison_candidates_cannot_authorize_targets` |
| 9 | Verified zero cannot be fabricated | A `VerifiedZeroClaim` on a historical artifact is `VERIFIED_ZERO_CLAIM_NOT_AUTHENTICATED`. Policy does not select `UNAVAILABLE`. The rendered sentence does not copy the answer or the digit zero. Provenance adds `FABRICATED_VERIFIED_ZERO`. The Live Ops binding keeps `verified_zero` false and `aircraft_impact_count` none. A synthetic result cannot set `evidence_verified` true. | `test_verified_zero_cannot_be_fabricated` |
| 10 | No operational factual response is produced from untrusted evidence | Historical evidence carrying a passed verifier and a verified-zero sentence, plus a Decision artifact whose aircraft id matches the comparison candidate, still renders only "Supplied evidence is not sufficient for an operational claim." Claims stay unadmitted. Preflight stays non-dispatchable. Plan coverage and end-to-end fulfillment stay closed. | `test_untrusted_evidence_produces_no_operational_factual_response` |

## Uncovered safety risks

These are properties of the current baseline. The new tests lock the behavior that exists today. They do not make a future module safe until the tests in the matrix below are added after integration.

1. **Two registries, one string.** `MasterPlanningSessionRegistry` and `LocalCapabilityRegistry` do not share a principal or a lock. Closing the planning session does not revoke capabilities. Closing the capability session does not invalidate the planning session. The same session-id string in both registries is two labels. A later handoff can wrongly treat that string as one authenticated session.

2. **Equal plans are not the retained plan.** `open_session` calls `authorize_route` again. The decision returned to the caller and the decision retained by the registry compare equal and are different objects. MA4's parallel bundle accepts a field-equal authorization. The session does not. A handoff that treats bundle acceptance as session membership would cross that boundary.

3. **The public dependency classifier is harsher than the gate inspection.** `dependency_verdict` reports `REQUIRED_TERMINAL_FAILURE` for a caller-supplied native failure. `inspect_route_gates` downgrades that effect and forces `authoritative_terminal` false. A later policy or integration module that reads `dependency_verdict` directly, or reads `edge.verdict` without `authoritative_terminal`, can treat a caller failure as a gate outcome.

4. **`applied` and `identity_matched` are not trust.** A matching invocation id marks a candidate applied and an edge identity-matched. Trust remains `UNTRUSTED_CANDIDATE`. A later module that treats `applied` or `identity_matched` as authentication would be wrong. The limitation `MATCHING_IDS_ARE_NOT_TRUST` is data on the inspection, not a check those later modules are forced to call.

5. **`ToolResult.data` is not interpreted.** A payload that claims authentication or a zero count does not add `FABRICATED_VERIFIED_ZERO`. That limitation is added when a `VerifiedZeroClaim` is supplied. Authenticity stays `NOT_ESTABLISHED` either way. A later source-read module that parses `data` into a count, a hazard impact, or an aircraft risk would fabricate an operational fact the current classifier refuses to read.

6. **Intermediate runs are storable and, on another path, finalizable.** The parallel bundle's native-artifact check allows `HistoricalSpecialistRunResult`. Admission rejects that object as the final artifact, so the response-policy path does not finalize it. `corroborate_master_evidence` does call `finalize_historical_specialist_run` when a final `SpecialistResult` is accompanied by a run and admission did not reject the final artifact. The historical binder does not store or finalize a run. A later readiness module must not call the finalizer and then promote the result.

7. **`CONTRACT_ONLY` is not a target grant, and it is reachable off the planned path.** Planned invocations carry a correlation id and a specialist request id, so the integrated Decision path observed here is `BINDING_UNRESOLVED` even when the aircraft ids match. A hand-built authorization that omits those ids can reach `CONTRACT_ONLY` while source authenticity stays `NOT_ESTABLISHED`. No current binder turns that status into `trusted_aircraft_id`. A later Decision readiness module must not do so.

8. **Structural readiness and the named-ready preflight state are easy to misread.** `ready_to_execute` is true for a Live Ops or Historical step whose only gate is `NOT_REQUIRED`. `CONTRACT_READY_FOR_FUTURE_INTEGRATION` can be constructed and bound. Both keep dispatch false. `evaluate_execution_preflight` does not return the named-ready state. A later module that forwards either flag as permission would leave the baseline.

9. **Capability consumption is a boolean trap.** `LocalConsumptionResult` for a recorded transition is never true in boolean context, but `outcome is TRANSITION_RECORDED` is true. That outcome does not consult the planning session, the gate inspection, or the preflight. A later module that dispatches on the outcome enum would convert bookkeeping into a permit.

10. **Comparison requests are not assessed when an endpoint is missing, and an inapplicable pair is only blocked.** A request whose specialist pair does not match the category becomes `BLOCKED` inside comparability. The response renderer still emits only the fixed insufficient sentence. Nothing in the current response path copies aircraft ids, hazard text, or counts into that sentence. A later renderer that interpolates comparison hints would.

No existing interface had to be bypassed. The tests use the public constructors and functions above. `MasterExecutionPreflightReport` requires the full `missing_provenance` tuple; a partial tuple is rejected by the existing constructor.

## Threat matrix for the seven Sprint 3A modules

Add these tests after integration. Do not add them as failing tests while the modules are absent.

| Module | Threat that appears only once the module exists | Tests to add after integration | Baseline that must still hold |
|---|---|---|---|
| `master_execution_integration.py` | One handoff joins the retained session, selected invocation, adapter descriptor, execution scope, gate inspection, and preflight. Empty evidence can be mistaken for coverage or a verified zero. A field-equal invocation can be substituted for the retained object. `BLOCKED_BY_AUTHORIZATION` can be cleared because the scope and the inspection exist. | `handoff_of_ready_live_step_keeps_dispatch_denied`; `field_equal_invocation_is_not_retained_selection`; `empty_evidence_is_not_coverage_or_zero`; `preflight_block_survives_scope_and_gate_inspection`; `consumed_capability_is_not_a_handoff_permit` | Invariants 1, 2, 4, and 10 |
| `master_session_origin_boundary.py` | Caller-supplied planning context, process-local membership, or a retained route decision can be reported as an authenticated principal or as permission to execute every invocation on the plan. A copied session id can reopen a closed session. | `context_membership_and_retained_decision_do_not_authenticate`; `session_id_string_cannot_open_or_select`; `one_selected_invocation_does_not_authorize_siblings`; `closed_session_cannot_be_reopened_by_matching_ids` | Invariants 1 and 3 |
| `master_execution_policy_boundary.py` | `dependency_verdict`'s terminal effect, a ready scope, a consumed capability, region text, or a historical window can be treated as an approved principal, read scope, deadline, or cleared gate. | `caller_failure_verdict_does_not_clear_policy`; `ready_scope_plus_consumed_capability_stays_denied`; `region_text_and_historical_window_are_not_approved_resources`; `policy_cannot_mark_principal_deadline_or_read_scope_satisfied` | Invariants 2, 4, and 5 |
| `master_live_ops_execution_readiness.py` | `prepare_master_live_ops_binding` returns a public `LiveOpsRunRequest`. Readiness can mistake that request, a supplied `ToolResult`, public evidence refs, or `structural_ready_to_execute` for a trusted context, an admission source, an impact count, or a dispatch. Withheld hints can be copied into selectors. | `prepared_request_creates_no_trusted_context_or_admission_source`; `supplied_tool_result_cannot_satisfy_live_ops_readiness`; `structurally_ready_live_step_is_not_dispatchable`; `readiness_emits_no_impact_count_or_verified_zero` | Invariants 2, 6, and 9 |
| `master_historical_execution_readiness.py` | Readiness can store the intermediate run, call `finalize_historical_specialist_run`, copy the plan window into tool arguments, or copy the requested evaluation time into `evaluated_as_of_utc`. A missing run or a failure can be rendered as zero. | `intermediate_run_and_finalizer_output_are_not_readiness_evidence`; `plan_window_is_not_a_tool_argument_or_source_clock`; `missing_run_and_failure_are_not_zero`; `requested_evaluation_time_is_not_actual_source_time` | Invariants 7 and 9 |
| `master_decision_execution_readiness.py` | A comparison candidate, callsign, typed aircraft id, recommendation hint, Live Ops discovery, or matching aircraft string can become `trusted_aircraft_id`. `BINDING_UNRESOLVED`, `CONTRACT_ONLY`, or `EXPECTED_AIRCRAFT_IS_COMPARISON_CANDIDATE` can be treated as target authorization. | `matching_comparison_candidate_does_not_grant_a_trusted_target`; `callsign_typed_hint_and_discovery_do_not_authorize`; `recommendation_hint_is_not_persisted_authorization`; `decision_artifact_is_not_source_authentication`; `contract_only_admission_is_not_a_target_grant` | Invariants 8 and 10 |
| `master_source_read_trace_boundary.py` | A fully populated proposed dispatcher event, a `ToolResult` payload, a `VerifiedZeroClaim`, matching authorization ids, or a permit-consumption reference can mark a `MasterProvenanceRequirement` satisfied or can turn capability consumption into a source read. | `fully_populated_trace_keeps_every_requirement_unsatisfied`; `tool_result_payload_and_verified_zero_claim_are_not_an_empty_read`; `matching_authorization_ids_are_not_source_origin`; `permit_reference_is_not_capability_consumption_or_dispatch` | Invariants 1, 6, and 9 |

Every later test must keep execution authority `DENIED`, production authority `DENIED`, source authenticity `NOT_ESTABLISHED`, and request coverage `NOT_ESTABLISHED`. None of those tests may promote a real or synthetic result to authenticated aviation evidence, authenticate a principal from a caller string or object identity, treat capability consumption as dispatch permission, or satisfy a gate from caller-supplied evidence.

## Import isolation

`tests/contracts/test_sprint3a_ma7_safety_import_isolation.py` parses the regression and imports it in a fresh interpreter.

Absent files, and modules that must not load:

- `wilvor_ai.master_execution_integration`
- `wilvor_ai.master_session_origin_boundary`
- `wilvor_ai.master_execution_policy_boundary`
- `wilvor_ai.master_live_ops_execution_readiness`
- `wilvor_ai.master_historical_execution_readiness`
- `wilvor_ai.master_decision_execution_readiness`
- `wilvor_ai.master_source_read_trace_boundary`
- `boto3`, `botocore`, `anthropic`, `openai`
- `wilvor_ai.historical_specialist`, `wilvor_ai.decision_specialist`, `wilvor_ai.live_ops`, `wilvor_ai.live_ops_adapter`
- `wilvor_operational`

Importing `SyntheticWorkerResult` loads the existing MA3 module and therefore LangGraph. That import does not run the graph. LangGraph is an existing MA3 dependency, not one of the seven unfinished modules.

## Independently observed results

Command, using `wilvor-infra/.venv`:

`python -m pytest tests/contracts/test_sprint3a_ma7_safety_regression.py tests/contracts/test_sprint3a_ma7_safety_import_isolation.py -q -p no:cacheprovider`

Result: 12 passed.

The same interpreter then ran the existing MA7 matrix and the Master contract regressions for parallel contracts, response policy and rendering, preflight, execution scope, planning session, gate resolution, local capability, provenance, specialist adapters, Live Ops binding, Historical binding, Decision binding, evidence admission, and offline integration.

Result: 418 passed.

HEAD remained `7073b3a4e9ac5f2380beb897f4be622e498ccdb8` on `sprint3a-ma7-safety`. No commit was created.

## File scope

Created, and left uncommitted:

- `functions/shared/wilvor_ai/SPRINT3A_MA7_SECURITY_REVIEW.md`
- `tests/contracts/test_sprint3a_ma7_safety_regression.py`
- `tests/contracts/test_sprint3a_ma7_safety_import_isolation.py`

No existing Wilvor source module or test file was modified. No other Sprint 3A stage was started.

## Current baseline addendum — 2026-10-25 at `8abd2d5`

**Verified against:** `8abd2d5` on `docs/ws5-ai-copilot` (`docs/wilvor-documentation` baseline)
**Production authorization:** `MasterRouteExecutionAuthority.DENIED`; `MasterProductionAuthority.DENIED`; `MasterSourceAuthenticity.NOT_ESTABLISHED`; `MasterRequestCoverage.NOT_ESTABLISHED`

This addendum does not rewrite the baseline review of `7073b3a` on
`sprint3a-ma7-safety`. The original findings, threat matrix, and dated
pytest counts (12 passed; 418 passed at that HEAD) remain historical
evidence of that review. They are not a current suite result.

### What changed in the tree

The seven Sprint 3A modules named as absent from the `7073b3a` worktree
now exist:

- `functions/shared/wilvor_ai/master_execution_integration.py`
- `functions/shared/wilvor_ai/master_session_origin_boundary.py`
- `functions/shared/wilvor_ai/master_execution_policy_boundary.py`
- `functions/shared/wilvor_ai/master_live_ops_execution_readiness.py`
- `functions/shared/wilvor_ai/master_historical_execution_readiness.py`
- `functions/shared/wilvor_ai/master_decision_execution_readiness.py`
- `functions/shared/wilvor_ai/master_source_read_trace_boundary.py`

MA5 and MA6 modules named in the baseline inspection table also exist
(`master_response_policy.py`, `master_execution_preflight.py`, and the
other MA6 files listed there). `test_ma7_master_safety_matrix.py` still
says "MA5 and MA6 modules are absent on this branch". That docstring is
stale relative to this tree. The test file is not owned by this
documentation workstream and was not edited.

### Authority — unchanged

Master execution authority remains `DENIED`. Production authority remains
`DENIED`. Source authenticity remains `NOT_ESTABLISHED`. Request coverage
remains `NOT_ESTABLISHED`. No constructor in these modules grants
production specialist dispatch. `run_synthetic_master_workflow` remains
test-only and was not called for this addendum.

The ten baseline invariants in the original table still describe the
intended fail-closed posture. Their regression tests remain in
`tests/contracts/test_sprint3a_ma7_safety_regression.py`.

### Threat-matrix tests

The original "tests to add after integration" used exact function names
such as `test_handoff_of_ready_live_step_keeps_dispatch_denied`. Those
exact identifiers were not found at `8abd2d5` (search: `tests/` for those
`def test_*` names). Contract tests that cover the same modules and
denials do exist, for example:

- `tests/contracts/test_master_execution_integration.py` (`test_structural_readiness_does_not_grant_execution_permission`)
- `tests/contracts/test_master_session_origin_boundary.py` (`test_copied_session_identifier_is_not_membership_or_a_principal`)
- `tests/contracts/test_master_live_ops_execution_readiness.py` (`test_structural_readiness_is_not_source_read_or_dispatch_permission`)
- `tests/contracts/test_master_source_read_trace_boundary.py` (`test_permit_reference_is_not_capability_consumption_or_dispatch`)

Those files are CONTRACT coverage of the modules. They are not a claim
that every named threat-matrix test was added under the original title.

### Surfaces that remain absent

Search at `8abd2d5`:

- Agent API / `"/ai"` in `functions/operational_api` — absent
- Dashboard `/ai` in `dashboard/src/routes/routeDefinitions.ts` — absent
- Terraform `wilvor_ai` / `bedrock` / `agent_api` under `envs/dev`,
  `envs/dev-historical-data`, and `modules/**/*.tf` — absent except an
  unattached historical-analytics IAM policy described as for a future
  Agent API (`modules/historical_analytics/iam.tf`)

No model provider or AWS API was invoked for this addendum. Current
pytest pass counts are UNKNOWN.

### Known limitations

- This addendum is documentation. It does not certify production
  readiness or source authentication.
- Uncovered safety risks in the original review remain properties to
  re-read when a later module claims a grant.
- Mapping pages: `docs/ai/master-agent.md`, `docs/ai/evidence-and-safety.md`.
