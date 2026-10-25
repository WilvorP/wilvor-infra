# Historical Analytics Specialist

**Canonical page:** this page
**Verified against:** `8abd2d5` on `docs/ws5-ai-copilot`
**Maturity:** CODE, CONTRACT, LIVE
**Production authorization:** `MasterRouteExecutionAuthority.DENIED`; `MasterProductionAuthority.DENIED`

## Purpose

This page describes the Historical Analytics Specialist, its four-tool catalog, two-stage run/finalize API, and Master binding boundary. Deep Phase 2C and 3A narrative remains in [`functions/shared/wilvor_ai/README.md`](../../functions/shared/wilvor_ai/README.md). Historical query domain ownership is WS4 ([`functions/shared/wilvor_historical_query/README.md`](../../functions/shared/wilvor_historical_query/README.md)).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `functions/shared/wilvor_ai/historical_specialist.py` (`HistoricalAnalyticsSpecialist`) |
| Terraform | TF absent at `8abd2d5` (search: `*.tf` for `wilvor_ai`, `HistoricalAnalyticsSpecialist`) | named search |
| Unit tests | UNIT for the operator harness module | `tests/unit/scripts/test_validate_historical_specialist_anthropic_live.py` |
| Contract tests | CONTRACT | `tests/contracts/test_historical_specialist.py`; `tests/contracts/test_historical_verification.py`; `tests/contracts/test_historical_analytics_tools.py` |
| Infrastructure tests | none found | named search |
| Integration tests | none found | named search |
| Live harness | LIVE | `scripts/validate_historical_specialist_anthropic_live.py` (`WILVOR_RUN_LIVE_ANTHROPIC`) |
| Current live pass | UNKNOWN | this documentation task did not invoke the provider |
| Past live artifact | historical evidence only | `functions/shared/wilvor_ai/README.md` records a prior Tier-1 / targeted run; `test-results/` is gitignored and was not present in this worktree |
| Deployment | UNKNOWN | not verified |
| Production authorization | `DENIED` | `functions/shared/wilvor_ai/master_historical_binding.py`; `functions/shared/wilvor_ai/master_historical_execution_readiness.py` |

## Behavior

`HistoricalAnalyticsSpecialist` runs a provider-neutral `ModelProvider` loop around `HistoricalAnalyticsAdapter`. It does not verify claims, render factual answers, create AWS clients, or import provider SDKs (source: `functions/shared/wilvor_ai/historical_specialist.py`).

Constructor binds required `provider` and `operations`, and optional `tool_call_id_factory`. `run(request, context)` accepts `SpecialistRequest` and `HistoricalSpecialistTrustedContext`. Trusted context contains only `as_of_utc`. That value is the requested deterministic evaluation instant injected into historical tools if they execute. It is not automatically `SpecialistResult` evidence (source: `functions/shared/wilvor_ai/historical_specialist.py`, `HistoricalAnalyticsSpecialist`; `functions/shared/wilvor_ai/specialist_contracts.py`, `HistoricalSpecialistTrustedContext`).

The two-stage API is retained: `specialist.run(...)` then `finalize_historical_specialist_run(...)` (source: `functions/shared/wilvor_ai/README.md`; `functions/shared/wilvor_ai/historical_answer_renderer.py`). `HistoricalSpecialistRunResult` has no `answer` and no `verified_claims`. `SpecialistRunStatus.PROPOSED_CLAIMS` means claims are unverified (source: `functions/shared/wilvor_ai/specialist_runtime_contracts.py`).

Budgets: `MAX_MODEL_TURNS = 3`, `MAX_TOOL_CALLS = 2`, `MAX_INVALID_TOOL_CALL_CORRECTIONS = 1`, `MAX_IDENTICAL_TOOL_CALL_EXECUTIONS = 1`. List policy for `list_historical_encounters`: omitted limit becomes 20; supplied `1..25` executes unchanged; supplied `<1` or `>25` is rejected with zero historical operations for that call. No silent clamping (source: `functions/shared/wilvor_ai/historical_specialist.py`, `AI_LIST_DEFAULT`, `AI_LIST_MAX`).

Instruction rules include `use_only_supplied_tools`, `do_not_invent_tool_results`, `as_of_is_runtime_controlled`, `coverage_blocked_is_not_zero`, and `no_geography_or_current_state_inference` (source: `functions/shared/wilvor_ai/historical_specialist.py`, `HISTORICAL_SPECIALIST_INSTRUCTION_RULES`). Active instruction ref is `wilvor.historical.specialist.v2` (source: `functions/shared/wilvor_ai/historical_specialist.py`, `HISTORICAL_SPECIALIST_INSTRUCTION_REF`). `wilvor.historical.specialist.v1` remains registered (source: `functions/shared/wilvor_ai/providers/instructions.py`, `HISTORICAL_SPECIALIST_V1_INSTRUCTION_REF`).

All four catalog tools are `READ_ONLY_ADVISORY` with `RETRIEVE_HISTORICAL_ANALYTICS` (source: `functions/shared/wilvor_ai/historical_analytics.py`, `HISTORICAL_ANALYTICS_TOOLS`). There is no fifth internal risk-distribution tool, no SQL/query tool, and no current-state or geography tool.

`HistoricalAnalyticsOperations` remains the domain authority. The adapter constructs Phase 2B request contracts, injects bound `as_of_utc`, and maps every `HistoricalQueryResponse` through `map_historical_query_response`. Request-construction failures return `UNKNOWN` / `INVALID_REQUEST` without calling operations and without fabricating `as_of_utc`, coverage, traces, or a certified zero (source: `functions/shared/wilvor_ai/README.md`; `functions/shared/wilvor_ai/historical_analytics.py`).

Coverage-certified `VERIFIED_ZERO` maps to `NOT_FOUND` and keeps first-class exact-zero / `EVALUABLE` proof. Completeness is not freshness (source: `functions/shared/wilvor_ai/README.md`).

### Verification

Proposed claims are untrusted assertions. Full `ToolResult` and first-class `Evidence` are the only verification authority (source: `functions/shared/wilvor_ai/historical_evidence_verifier.py`). `verify_historical_specialist_run` runs only when `run_result.status is SpecialistRunStatus.PROPOSED_CLAIMS`; otherwise `VerifierOutcome.NOT_RUN`.

Any verification failure yields `VerifierOutcome.FAILED`, empty `verified_claims`, and `SpecialistStatus.UNAVAILABLE` with `CLAIM_VERIFICATION_FAILED`. There is no verification retry and no provider call (source: `functions/shared/wilvor_ai/README.md`). Model quality is not the factual safety boundary. Unsupported factual claims are not rendered unless deterministic evidence verification passes (source: `functions/shared/wilvor_ai/README.md`).

`SpecialistResult.answer` is code-rendered. Model-authored operational prose remains prohibited (source: `functions/shared/wilvor_ai/README.md`).

### Master boundary

`prepare_master_historical_binding` does not call `HistoricalAnalyticsSpecialist` or `finalize_historical_specialist_run`. `evaluated_as_of_utc` stays unset. `HistoricalSpecialistTrustedContext` is not constructed (source: `functions/shared/wilvor_ai/master_historical_binding.py`). Execution readiness keeps `production_authority: MasterProductionAuthority.DENIED` and `source_authenticity: NOT_ESTABLISHED` (source: `functions/shared/wilvor_ai/master_historical_execution_readiness.py`).

Admission rejects a `HistoricalSpecialistRunResult` as the final artifact with `HISTORICAL_RUN_NOT_FINAL_ARTIFACT` (source: `functions/shared/wilvor_ai/master_evidence_admission.py`, `MasterAdmissionReason`). Intermediate runs are not final evidence (tests: `tests/contracts/test_sprint3a_ma7_safety_regression.py`, `test_historical_intermediate_results_are_not_final_evidence`).

## Interfaces

Trusted runtime on `HistoricalAnalyticsCall`: `operations`, `as_of_utc`, `tool_call_id` (source: `functions/shared/wilvor_ai/historical_analytics.py`, `HistoricalAnalyticsCall`).

Domain source: `wilvor_historical_query.operations.HistoricalAnalyticsOperations` and Phase 2B request contracts in `wilvor_historical.query_contracts`. The adapter does not expose AWS clients, SQL, query ids, workgroups, or current/geography fallback (source: `functions/shared/wilvor_ai/README.md`).

Provider adapters: `functions/shared/wilvor_ai/providers/anthropic_messages.py`, `functions/shared/wilvor_ai/providers/bedrock_converse.py`, `functions/shared/wilvor_ai/providers/instructions.py`. Both adapters accept injected clients. They do not import `anthropic` or `boto3` (source: `functions/shared/wilvor_ai/README.md`). Live Bedrock was recorded as blocked by account model authorization; that is historical README evidence, not a current account observation.

The live harness uses canned production `HistoricalQueryResponse` operations. AWS and Athena are not used (source: `scripts/validate_historical_specialist_anthropic_live.py`).

## Known limitations

- `TOOL_SCHEMA_GAP`: the model-facing `list_historical_encounters` schema permits start/end without `aircraft_id` or `hazard_id`, while `ListHistoricalEncountersRequest` requires at least one identifier. The deterministic adapter still fails closed (source: `functions/shared/wilvor_ai/README.md`).
- Coverage blocked is not zero. Missing records do not establish zero risk or a certified empty population without evaluable exact-zero proof.
- The age of a historical event does not make the evidence stale. Historical freshness concerns fitness, completeness, or update state of the analytical source for the requested period, where that is known (source: `functions/shared/wilvor_ai/README.md`; `functions/shared/wilvor_ai/contracts.py`, `FreshnessStatus`).
- Master historical binding does not dispatch the specialist.
- Current live-harness pass is UNKNOWN. Stored live JSON artifacts are historical evidence and are not rewritten when harness accounting later changes (source: `functions/shared/wilvor_ai/README.md`).
- Agent API, Lambda execution role, and query-policy attachment to an Agent API role remain absent (source: `functions/shared/wilvor_historical_query/README.md`; `modules/historical_analytics/iam.tf`).

## Historical record

Phase 2C README text said `HISTORICAL_ANALYTICS_TOOLS` was ready for a future specialist and that a model-backed specialist was not implemented. Those sentences described the end of Phase 2C. Later sections of the same README record Phase 3A specialist completion. This page is the current mapping.

## Sources

- Source: `functions/shared/wilvor_ai/historical_specialist.py` (`HistoricalAnalyticsSpecialist`)
- Source: `functions/shared/wilvor_ai/historical_analytics.py` (`HISTORICAL_ANALYTICS_TOOLS`, `HistoricalAnalyticsAdapter`)
- Source: `functions/shared/wilvor_ai/historical_evidence_verifier.py` (`verify_historical_specialist_run`)
- Source: `functions/shared/wilvor_ai/historical_answer_renderer.py` (`finalize_historical_specialist_run`)
- Source: `functions/shared/wilvor_ai/master_historical_binding.py`
- Tests: `tests/contracts/test_historical_specialist.py`
- Tests: `tests/contracts/test_historical_verification.py`
- Tests: `tests/contracts/test_sprint3a_ma7_safety_regression.py` (`test_historical_intermediate_results_are_not_final_evidence`)
