# Live Operations Specialist

**Canonical page:** this page
**Verified against:** `8abd2d5` on `docs/ws5-ai-copilot`
**Maturity:** CODE, CONTRACT, LIVE
**Production authorization:** `MasterRouteExecutionAuthority.DENIED`; `AgentAuthorityMode.READ_ONLY_ADVISORY` is not source-read permission

## Purpose

This page describes the Live Operations Specialist implementation, its eight-tool catalog, verification pipeline, and Master binding boundary. Tool argument tables for all specialists are on [Tools and data access](tools.md). Architecture context is on [Architecture](architecture.md).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `functions/shared/wilvor_ai/live_ops_specialist.py` (`LiveOpsSpecialist`) |
| Terraform | TF absent at `8abd2d5` (search: `*.tf` for `wilvor_ai`, `bedrock`, `LiveOpsSpecialist`) | named search |
| Unit tests | none found under `tests/unit/` for this specialist | named search |
| Contract tests | CONTRACT | `tests/contracts/test_live_ops_specialist_orchestration.py`; `tests/contracts/test_live_ops_tools.py`; `tests/contracts/test_live_ops_evidence_verifier.py`; related `test_live_ops_*.py` |
| Infrastructure tests | none found | named search |
| Integration tests | none found | named search |
| Live harness | LIVE | `scripts/validate_live_ops_specialist_anthropic_live.py` (`WILVOR_RUN_LIVE_LIVE_OPS_ANTHROPIC`) |
| Current live pass | UNKNOWN | this documentation task did not invoke the provider |
| Deployment | UNKNOWN | not verified |
| Production authorization | `DENIED` | `functions/shared/wilvor_ai/master_live_ops_binding.py`; `functions/shared/wilvor_ai/master_live_ops_execution_readiness.py` |

## Behavior

`LiveOpsSpecialist` implements `LiveOpsSpecialistPort.run`. It sequences an injected provider, LO1 dispatch, LO2 verification, LO3A export, and LO3B rendering. It does not call a vendor SDK, LangGraph, or AWS (source: `functions/shared/wilvor_ai/live_ops_specialist.py`).

The run is synchronous. LO1 executes a batch of up to two tools with no hook between them. Deadline and cancellation are cooperative: after a blocking call returns, the guard can discard its evidence instead of publishing it (source: `functions/shared/wilvor_ai/live_ops_specialist.py`).

`live_ops.py` wraps committed Phase 1 public domain functions and returns Phase 0 `ToolResult` envelopes. The tools do not implement an agent, call AWS, or decide operational currentness, geography, or source-version authority (source: `functions/shared/wilvor_ai/live_ops.py`).

All eight catalog entries use `authority_mode=READ_ONLY_ADVISORY` and `capabilities=(RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT,)` (source: `functions/shared/wilvor_ai/live_ops.py`, `LIVE_OPS_TOOLS`). `READ_ONLY_ADVISORY` on those tools is not source-read permission (source: `functions/shared/wilvor_ai/master_live_ops_execution_readiness.py`).

Budgets: `LIVE_OPS_MAX_MODEL_TURNS = 4`, `LIVE_OPS_MAX_TOOL_CALLS = 6` (source: `functions/shared/wilvor_ai/live_ops_public_contracts.py`); `MAX_TOOL_CALLS_PER_BATCH = 2` (source: `functions/shared/wilvor_ai/live_ops_model_contracts.py`).

A `LiveOpsRunResult` is an authorized specialist answer only when a trusted specialist invocation returned it. Structural validity is not operational verification (source: `functions/shared/wilvor_ai/live_ops_public_contracts.py`). Tests and mocks may construct the public objects.

### Verification

Raw `ToolResult` values are the factual authority. Model-facing projections are used only to decide which rows the model could see (source: `functions/shared/wilvor_ai/live_ops_evidence_verifier.py`). `verify_live_ops_claims` produces `LiveOpsVerificationOutcome`. `ALL_VERIFIED` means every submitted claim passed. It does not mean the user question was fully answered or that every operational record was evaluated. Mandatory source limitations stay attached either way (source: `functions/shared/wilvor_ai/live_ops_evidence_verifier.py`).

`LIVE_OPS_COMPLETENESS_NOTICE` states: "ALL_VERIFIED means every submitted claim verified. It does not mean every operational fact was investigated or that the source data is fresh, complete, or atomic." (source: `functions/shared/wilvor_ai/live_ops_public_contracts.py`, `LIVE_OPS_COMPLETENESS_NOTICE`). Admission treats `LIVE_OPS_ALL_VERIFIED_NOT_SOURCE_AUTHENTICATION` as a reason that verification is not source authentication (source: `functions/shared/wilvor_ai/master_evidence_admission.py`, `MasterAdmissionReason`).

V1 Live Ops evidence uses `FreshnessStatus.UNKNOWN` with `FRESHNESS_NOT_ESTABLISHED`. `CURRENT` is not `FRESH` (source: `functions/shared/wilvor_ai/README.md`; `functions/shared/wilvor_ai/live_ops_mapping.py`).

Fail-closed public answers include `LIVE_OPS_ANSWER_TOOL_FAILED`: "Live operations could not complete the tool execution. No operational facts are verified." (source: `functions/shared/wilvor_ai/live_ops_public_contracts.py`).

### Master boundary

`prepare_master_live_ops_binding` preserves execution authority `DENIED`, production authority `DENIED`, and source authenticity `NOT_ESTABLISHED` (source: `functions/shared/wilvor_ai/master_live_ops_binding.py`). `assess_master_live_ops_execution_readiness` does not call `LiveOpsSpecialist.run`, `LiveOpsAdapter.dispatch`, a model provider, `resolve_region`, or any read-only Live Ops tool (source: `functions/shared/wilvor_ai/master_live_ops_execution_readiness.py`).

The binder may construct a public `LiveOpsRunRequest`. That request is not a trusted context, not an admission source, and not a dispatch (source: `functions/shared/wilvor_ai/master_live_ops_execution_readiness.py`).

`search_current_hazards` and `search_current_impacts` mark `region` optional on the catalog. `LiveOpsAdapter` still rejects a hazard search without `region` or `hazard_ids` and an impact search without those selectors. An optional parameter is not an authorized selector (source: `functions/shared/wilvor_ai/master_live_ops_execution_readiness.py`; `functions/shared/wilvor_ai/live_ops.py`, `search_current_hazards` description).

`resolve_region` can resolve a census state name. That resolution is not SIGMET geometry and does not establish geospatial coverage (source: `functions/shared/wilvor_ai/master_live_ops_execution_readiness.py`).

## Interfaces

Trusted runtimes are distinct objects. Those fields are not model arguments.

| Object | Fields | Role |
| --- | --- | --- |
| `LiveOpsTrustedContext` | required `tables`, `now_epoch`, `correlation_id`; `max_model_turns` / `max_tool_calls` with Live Ops caps; optional `clock`, `deadline_epoch`, `cancelled` | `LiveOpsSpecialist.run` argument (source: `functions/shared/wilvor_ai/live_ops_public_contracts.py`, `LiveOpsTrustedContext`) |
| `LiveOpsSpecialistTrustedContext` | `tables`, `now_epoch`, optional `correlation_id` | constructed inside `run` and passed to `LiveOpsAdapter` (source: `functions/shared/wilvor_ai/live_ops_specialist_runtime_contracts.py`, `LiveOpsSpecialistTrustedContext`; `functions/shared/wilvor_ai/live_ops_specialist.py`) |
| `LiveOpsCall` | `tables`, `now_epoch`, `tool_call_id`, optional `correlation_id` | per-tool envelope; `tool_call_id` is unique per invocation (source: `functions/shared/wilvor_ai/live_ops.py`, `LiveOpsCall`) |

Domain sources wrapped by the eight tools are `wilvor_operational.discovery`, `wilvor_operational.query`, `wilvor_operational.context`, and, when a region argument is supplied, `wilvor_operational.geospatial` (source: `functions/shared/wilvor_ai/live_ops.py`). Catalog names and fields are on [Tools and data access](tools.md).

Provider adapters: `functions/shared/wilvor_ai/providers/anthropic_live_ops_messages.py`, `functions/shared/wilvor_ai/providers/live_ops_instructions.py`. Core packages do not import the Anthropic SDK.

## Known limitations

- The specialist does not authenticate DynamoDB reads. Injected `tables` are caller-supplied.
- DynamoDB reads are independently observed; the composition is not a transactional cross-table snapshot (source: `functions/shared/wilvor_ai/live_ops_evidence_verifier.py`, `_NO_SNAPSHOT`).
- A recently written record may not yet be visible (source: `functions/shared/wilvor_ai/live_ops_evidence_verifier.py`, `_EVENTUAL`).
- Absence of a recommendation or alert is proven only within the observed time-filtered candidate set (source: `functions/shared/wilvor_ai/live_ops_evidence_verifier.py`).
- Master Live Ops binding and readiness do not dispatch the specialist.
- The live harness uses fixture/domain tables unless an operator opts in. Current pass is UNKNOWN.

## Historical record

`functions/shared/wilvor_ai/README.md` previously listed "Live Ops Expert" among items still not implemented after this specialist existed. That sentence is retained on the package README under Historical record. This page is the current mapping.

## Sources

- Source: `functions/shared/wilvor_ai/live_ops_specialist.py` (`LiveOpsSpecialist`)
- Source: `functions/shared/wilvor_ai/live_ops.py` (`LIVE_OPS_TOOLS`)
- Source: `functions/shared/wilvor_ai/live_ops_adapter.py` (`LiveOpsAdapter`)
- Source: `functions/shared/wilvor_ai/live_ops_evidence_verifier.py` (`verify_live_ops_claims`)
- Source: `functions/shared/wilvor_ai/live_ops_public_contracts.py` (`LIVE_OPS_COMPLETENESS_NOTICE`)
- Source: `functions/shared/wilvor_ai/master_live_ops_binding.py`
- Source: `functions/shared/wilvor_ai/master_live_ops_execution_readiness.py`
- Tests: `tests/contracts/test_live_ops_specialist_orchestration.py`
- Tests: `tests/contracts/test_live_ops_tools.py`
- Tests: `tests/contracts/test_master_live_ops_execution_readiness.py`
