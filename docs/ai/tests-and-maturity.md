# Tests and maturity

**Canonical page:** this page
**Verified against:** `8abd2d5` on `docs/ws5-ai-copilot`
**Maturity:** CONTRACT, LIVE
**Production authorization:** `MasterRouteExecutionAuthority.DENIED`; no current live-harness pass is recorded here

## Purpose

This page inventories AI contract tests and live-validation harnesses, and records missing Agent API, production integration, Terraform wiring, and dashboard AI surface. The colocated contract-suite index is [`tests/contracts/README.md`](../../tests/contracts/README.md). Maturity-label definitions live in [`DOCUMENTATION_STANDARDS.md`](../DOCUMENTATION_STANDARDS.md).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Contract suite files | 117 `tests/contracts/test_*.py` files at `8abd2d5` | named search: `tests/contracts/test_*.py` |
| Current pytest pass count | UNKNOWN | this documentation task did not run the suite |
| Unit tests | UNIT for Historical and Decision harness dry-run modules; Live Ops harness tests are CONTRACT | `tests/unit/scripts/test_validate_historical_specialist_anthropic_live.py`; `tests/unit/scripts/test_validate_decision_specialist_anthropic_live.py`; `tests/contracts/test_live_ops_anthropic_live_harness.py` |
| Infrastructure tests | none found at `8abd2d5` (search: `tests/infrastructure/` for `wilvor_ai`) | named search |
| Integration tests | none found at `8abd2d5` (search: `tests/integration/` for `wilvor_ai`) | named search |
| Live harness files | LIVE | three `scripts/validate_*anthropic_live.py` files |
| Current live pass | UNKNOWN | no provider call; `test-results/` is gitignored and absent from this worktree |
| Terraform AI wiring | TF absent | search: `envs/dev`, `envs/dev-historical-data`, `modules/**/*.tf` for `wilvor_ai`, `bedrock`, `agent_api` |
| Agent API | absent | search: `functions/operational_api` for `"/ai"`; `dashboard/src/routes/routeDefinitions.ts` |
| Dashboard `/ai` route | absent | `dashboard/src/routes/routeDefinitions.ts` (`ROUTES`) |
| Deployment | UNKNOWN | not verified |
| Production authorization | `DENIED` | `functions/shared/wilvor_ai/master_routing.py` |

## Behavior

### Contract tests

AI behavior is asserted under `tests/contracts/`. The files are grouped below. Each file was present at `8abd2d5`. Assertions were not re-run. A file's presence is CONTRACT for the claims that file asserts when those assertions were read on the specialist, tools, or Master pages.

**Phase 0 / shared contracts**

- `test_ai_copilot_contracts.py` — `V1_AUTHORITY`, `Evidence`, `ToolResult` round-trips
- `test_evidence_provenance.py` — first-class evidence provenance
- `test_model_contracts.py` — `ModelDecision` discriminated union
- `test_specialist_contracts.py` — specialist request/result contracts
- `test_tool_schema.py` / `test_tool_schema_import_isolation.py` — catalog schema generation
- `test_tool_result_projection.py` — model-visible projections
- `test_provider_import_isolation.py` / `test_specialist_import_isolation.py` — import isolation

**Live Ops**

- `test_live_ops_tools.py`, `test_live_ops_adapter.py`, `test_live_ops_specialist_orchestration.py`
- `test_live_ops_evidence_verifier.py`, `test_live_ops_answer_renderer.py`, `test_live_ops_claims.py`
- `test_live_ops_tool_projection.py`, `test_live_ops_model_contracts.py`
- `test_live_ops_public_contracts.py`, `test_live_ops_public_export.py`, `test_live_ops_public_master_mock.py`
- `test_live_ops_anthropic_live_harness.py` — **offline** tests of the harness module; live actions stay behind `WILVOR_RUN_LIVE_LIVE_OPS_ANTHROPIC` and are not invoked by the test suite (source: `scripts/validate_live_ops_specialist_anthropic_live.py`)
- `test_live_ops_anthropic_offline_integration.py`, `test_anthropic_live_ops_messages_provider.py`
- matching `*_import_isolation.py` files

**Historical Analytics**

- `test_historical_analytics_tools.py`, `test_historical_analytics_mapping.py`
- `test_historical_specialist.py`, `test_historical_specialist_instructions.py`
- `test_historical_verification.py`
- matching import-isolation files

**Decision**

- `test_decision_tools.py`, `test_decision_tools_adversarial.py`, `test_decision_tools_integration.py`
- `test_decision_specialist.py`, `test_decision_specialist_runtime_contracts.py`
- `test_decision_evidence_verifier.py`, `test_decision_answer_renderer.py`, `test_decision_claims.py`
- `test_decision_context.py`, `test_decision_contracts.py`, `test_decision_narrow_tools.py`
- `test_decision_model_contracts.py`, `test_decision_provider_instructions.py`
- `test_decision_tool_projection.py`
- `test_decision_expert_closure.py` — includes the sentence "Agent API remains future work" as a documented remaining gap
- `test_persisted_airport_evidence.py`
- `test_anthropic_decision_messages_provider.py`
- matching import-isolation files

**Master MA1–MA7**

- `test_master_contracts.py`, `test_master_routing.py`, `test_master_orchestration.py`
- `test_master_evidence_admission.py`, `test_master_evidence_corroboration.py`, `test_master_evidence_comparability.py`
- `test_ma4_specialist_corroboration_baseline.py`, `test_ma4_comparability_guardrails.py`
- `test_master_parallel_contracts.py`, `test_master_response_policy.py`, `test_master_response_renderer.py`
- `test_master_execution_preflight.py`, `test_master_specialist_adapters.py`, `test_master_execution_scope.py`
- `test_master_planning_session.py`, `test_master_local_capability.py`, `test_master_gate_resolution.py`
- `test_master_provenance_trace.py`
- `test_master_live_ops_binding.py`, `test_master_historical_binding.py`, `test_master_decision_binding.py`
- `test_master_execution_integration.py`, `test_master_execution_policy_boundary.py`
- `test_master_session_origin_boundary.py`
- `test_master_live_ops_execution_readiness.py`, `test_master_historical_execution_readiness.py`, `test_master_decision_execution_readiness.py`
- `test_master_source_read_trace_boundary.py`
- `test_master_offline_integration.py`
- `test_ma7_master_safety_matrix.py`, `test_ma7_master_safety_import_isolation.py`
- `test_sprint3a_ma7_safety_regression.py`, `test_sprint3a_ma7_safety_import_isolation.py`
- matching `test_master_*_import_isolation.py` files
- `test_anthropic_messages_provider.py`, `test_bedrock_converse_provider.py` — offline provider adapters; they do not call AWS or Anthropic

Sprint 3A regression tests that lock authority distinctions (source: `tests/contracts/test_sprint3a_ma7_safety_regression.py`):

- `test_matching_identifiers_are_not_authentication`
- `test_structurally_ready_plan_cannot_execute`
- `test_process_local_session_is_not_an_authenticated_principal`
- `test_single_use_registry_transition_is_not_a_dispatch_permit`
- `test_caller_reported_specialist_failure_is_not_an_authoritative_gate_outcome`
- `test_supplied_tool_results_do_not_establish_source_authenticity`
- `test_historical_intermediate_results_are_not_final_evidence`
- `test_decision_comparison_candidates_cannot_authorize_targets`
- `test_verified_zero_cannot_be_fabricated`
- `test_untrusted_evidence_produces_no_operational_factual_response`

The original Sprint 3A review recorded 12 passed and 418 passed at commit `7073b3a` on `sprint3a-ma7-safety`. That is historical evidence of a past run. It is not a current suite result at `8abd2d5` (source: `functions/shared/wilvor_ai/SPRINT3A_MA7_SECURITY_REVIEW.md`).

Exact threat-matrix function names listed as "tests to add after integration" in that review (`test_handoff_of_ready_live_step_keeps_dispatch_denied` and siblings) were **not** found as those identifiers. Equivalent contract tests exist under the module files named above. Search: `tests/` for those exact `def test_*` names at `8abd2d5`.

### Live-validation harnesses

| Script | Opt-in | Data plane | Current pass |
| --- | --- | --- | --- |
| `scripts/validate_live_ops_specialist_anthropic_live.py` | `WILVOR_RUN_LIVE_LIVE_OPS_ANTHROPIC` | fixture/domain tables unless opted in | UNKNOWN |
| `scripts/validate_historical_specialist_anthropic_live.py` | `WILVOR_RUN_LIVE_ANTHROPIC` | canned `HistoricalQueryResponse`; no AWS or Athena | UNKNOWN |
| `scripts/validate_decision_specialist_anthropic_live.py` | `WILVOR_RUN_LIVE_DECISION_ANTHROPIC` and wire/terminal/transport/routing variants | harness-local memory tables; no AWS client during dry-run | UNKNOWN |

Importing those modules does not import the Anthropic SDK, read an API key, or start a scenario. The test suite does not invoke live actions (source: each script's module docstring).

Offline tests of harness modules:

- `tests/contracts/test_live_ops_anthropic_live_harness.py`
- `tests/unit/scripts/test_validate_historical_specialist_anthropic_live.py`
- `tests/unit/scripts/test_validate_decision_specialist_anthropic_live.py`

`scripts/validate_historical_query_runtime.py` and `scripts/validate_historical_analytics.ps1` are historical-query operator scripts, not Master Agent or specialist-dispatch harnesses. WS4 owns the historical-query README.

Package README text records a prior direct-Anthropic Historical Tier-1 run and a targeted v2 retest, including a gitignored path `test-results/live-anthropic/20260930T000804Z-targeted.json`. Those stored artifacts are historical evidence of a past run. They were not present in this worktree (`test-results/` is gitignored; search found no live report JSON). They are not a current live pass. Machine reports keep `phase_3a_complete = false` (source: `functions/shared/wilvor_ai/README.md`; `scripts/validate_historical_specialist_anthropic_live.py`).

Bedrock Converse is implemented and tested offline. Live Bedrock was recorded in README as blocked by account model authorization. That is historical README evidence, not a current account observation.

### Missing production surfaces

Confirmed absent at `8abd2d5`:

| Surface | Search | Result |
| --- | --- | --- |
| Agent API / `/ai` Operational API route | `functions/operational_api` for `"/ai"` | absent |
| Dashboard `/ai` route | `dashboard/src/routes/routeDefinitions.ts` `ROUTES` | seven workflows: `/`, `/aircraft`, `/airports`, `/encounters`, `/recommendations`, `/alerts`, `/system-health`; no `/ai` |
| Terraform `wilvor_ai` resource | `envs/dev`, `envs/dev-historical-data`, `modules/**/*.tf` | absent |
| Terraform Bedrock client | `*.tf` for `bedrock` | absent |
| Agent API execution principal | `modules/historical_analytics/iam.tf` | unattached query policy; description says future Agent API attaches it; the policy is not an execution principal |

`test_decision_expert_closure.py` still records "Agent API remains future work".

No `tests/integration/` or `tests/infrastructure/` file was found that asserts AI copilot deployment.

## Known limitations

- CONTRACT means the asserting test file exists and, where cited on other pages, the assertion was read. It does not mean the suite passed today.
- LIVE means a harness file exists. It does not mean a current pass.
- Six Live Operations region tests can fail for an unrelated `us_states.geojson` CRLF checksum. That is not a decision-contract failure (source: `functions/shared/wilvor_ai/README.md`).
- `test_ma7_master_safety_matrix.py` docstring still says MA5 and MA6 modules are absent. Those modules exist in this tree. The test file is not owned by WS5 and was not edited.
- Production authorization remains `DENIED` regardless of contract-test volume.

## Historical record

No superseded statement is stored on this page. Dated pytest counts in `SPRINT3A_MA7_SECURITY_REVIEW.md` (12 passed; 418 passed at `7073b3a`) remain historical on that file.

## Sources

- Source: `tests/contracts/` (117 `test_*.py` files)
- Source: `scripts/validate_live_ops_specialist_anthropic_live.py`
- Source: `scripts/validate_historical_specialist_anthropic_live.py`
- Source: `scripts/validate_decision_specialist_anthropic_live.py`
- Source: `dashboard/src/routes/routeDefinitions.ts` (`ROUTES`)
- Source: `modules/historical_analytics/iam.tf`
- Tests: `tests/contracts/test_sprint3a_ma7_safety_regression.py`
- Tests: `tests/contracts/test_decision_expert_closure.py`
