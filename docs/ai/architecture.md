# AI system architecture

**Canonical page:** this page
**Historical verification:** `8abd2d5` on `docs/ws5-ai-copilot`. Not verification of the current tree.
**Maturity:** CODE, CONTRACT, LIVE
**Production authorization:** `MasterRouteExecutionAuthority.DENIED`; `MasterProductionAuthority.DENIED`; `envs/dev` keeps `enable_ai_query_route = false` and `module.ai_host` `enable_invoke = false`

## Purpose

This page distinguishes the three `MasterSpecialist` implementations from Master Agent contracts and states each component's implementation and verification status. Tool catalogs are on [Tools and data access](tools.md). Authority and fail-closed verification are on [Evidence and safety](evidence-and-safety.md). Test inventory is on [Tests and maturity](tests-and-maturity.md).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `functions/shared/wilvor_ai/` (`LiveOpsSpecialist`, `HistoricalAnalyticsSpecialist`, `DecisionSpecialist`, `master_*.py`) |
| Terraform | TF for a closed AI host; no Agent API route in `envs/dev` | `envs/dev/main.tf` (`module.ai_host`, `enable_invoke = false`, `enable_ai_query_route = false`); `modules/ai_host/iam.tf` (`DenyModelSecretsAndInvoke` denies `bedrock:*`) |
| Unit tests | UNIT for Historical and Decision operator-harness dry-run modules; Live Ops harness tests are CONTRACT; specialist behavior is under `tests/contracts/` | `tests/unit/scripts/test_validate_historical_specialist_anthropic_live.py`; `tests/unit/scripts/test_validate_decision_specialist_anthropic_live.py`; `tests/contracts/test_live_ops_anthropic_live_harness.py` |
| Contract tests | CONTRACT | `tests/contracts/test_*.py`. The count 117 is a historical observation at `8abd2d5`, not a current inventory. |
| Infrastructure tests | Historical observation at `8abd2d5`: a search of `tests/infrastructure/` for `wilvor_ai` found none. That search is not verification of the current code. `tests/infrastructure/terraform/test_sprint3d_ai_host.py` asserts the closed host. | historical search; `tests/infrastructure/terraform/test_sprint3d_ai_host.py` |
| Integration tests | Historical observation at `8abd2d5`: a search of `tests/integration/` for `wilvor_ai` found none. That search is not verification of the current code. | named historical search |
| Live harness | LIVE | `scripts/validate_live_ops_specialist_anthropic_live.py`; `scripts/validate_historical_specialist_anthropic_live.py`; `scripts/validate_decision_specialist_anthropic_live.py` |
| Current live pass | UNKNOWN | this documentation task did not run a provider or AWS harness |
| Deployment | UNKNOWN | no apply record or account observation is cited |
| Production authorization | `DENIED` | `functions/shared/wilvor_ai/master_routing.py` (`MasterRouteExecutionAuthority`); `functions/shared/wilvor_ai/master_contracts.py` (`MasterProductionAuthority`) |
| Agent API | absent while the dev flag is false | `envs/dev/main.tf` (`enable_ai_query_route = false`); `modules/operational_api/api.tf` (`aws_apigatewayv2_route.ai_query` count) |
| Dashboard Copilot | CODE, production AI inactive | `dashboard/src/routes/routeDefinitions.ts` (`/copilot`) |

## Behavior

`MasterSpecialist` names three specialists: `LIVE_OPS`, `HISTORICAL_ANALYTICS`, and `DECISION` (source: `functions/shared/wilvor_ai/master_contracts.py`, `MasterSpecialist`). No other specialist member exists.

Wilvor's deterministic services remain authoritative for aircraft state, projections, hazards, encounters, risk, airport assessments, recommendations, alerts, geometry, trajectories, and state transitions. AI text may explain and summarize verified deterministic results. It does not replace those calculations, and it does not convert missing evidence into assumed facts (source: `functions/shared/wilvor_ai/README.md`; `dashboard/README.md`).

`AgentAuthorityMode` contains `READ_ONLY_ADVISORY` only. `V1_AUTHORITY` binds that mode to `ALLOWED_V1_CAPABILITIES` and `PROHIBITED_V1_CAPABILITIES` (source: `functions/shared/wilvor_ai/contracts.py`, `AgentAuthorityMode`, `V1_AUTHORITY`). The declaration is a testable contract, not runtime authorization enforcement (source: `functions/shared/wilvor_ai/README.md`).

### Live Operations Specialist

`LiveOpsSpecialist` implements `LiveOpsSpecialistPort.run`. It sequences an injected provider, LO1 dispatch, LO2 verification, LO3A export, and LO3B rendering. It does not call a vendor SDK, LangGraph, or AWS (source: `functions/shared/wilvor_ai/live_ops_specialist.py`, `LiveOpsSpecialist`). Canonical page: [Live Operations Specialist](live-operations-specialist.md).

### Historical Analytics Specialist

`HistoricalAnalyticsSpecialist` runs a provider-neutral `ModelProvider` loop around the bound historical adapter. It does not verify claims, render factual answers, create AWS clients, or import provider SDKs (source: `functions/shared/wilvor_ai/historical_specialist.py`, `HistoricalAnalyticsSpecialist`). Verification and rendering are a second stage: `finalize_historical_specialist_run` (source: `functions/shared/wilvor_ai/historical_answer_renderer.py`). Canonical page: [Historical Analytics Specialist](historical-analytics-specialist.md).

### Decision Specialist

`DecisionSpecialist` dispatches the closed Decision Tools, then DE1 verification and DE2 rendering. It does not calculate aviation facts, select an encounter, rank a recommendation, or treat persisted evidence as current (source: `functions/shared/wilvor_ai/decision_specialist.py`, `DecisionSpecialist`). Canonical page: [Decision Specialist](decision-specialist.md).

### Master Agent contracts

Master modules plan, classify, admit, corroborate, render nonfactual policy text, and assess execution readiness. They do not grant production execution. `MasterRouteExecutionAuthority` defines `DENIED` only. The docstring states that this is planning permission and that production execution is not representable (source: `functions/shared/wilvor_ai/master_routing.py`, `MasterRouteExecutionAuthority`). `reserved_production_adapter` raises `production_authority_not_issued` (source: `functions/shared/wilvor_ai/master_contracts.py`, `reserved_production_adapter`). Canonical page: [Master Agent](master-agent.md).

`run_synthetic_master_workflow` is test-only LangGraph orchestration over injected fake workers. Synthetic completion is not verified aviation evidence. `execution_authority` stays `DENIED` (source: `functions/shared/wilvor_ai/master_orchestration.py`). No `master_*.py` module dispatches `LiveOpsSpecialist`, `HistoricalAnalyticsSpecialist`, or `DecisionSpecialist`.

```mermaid
flowchart LR
  subgraph specialists [Specialists CODE CONTRACT LIVE]
    LiveOps["LiveOpsSpecialist"]
    Historical["HistoricalAnalyticsSpecialist"]
    Decision["DecisionSpecialist"]
  end
  subgraph master [Master offline contracts CODE CONTRACT]
    Route["authorize_route"]
    Auth["MasterRouteExecutionAuthority"]
    Bind["MasterSpecialistAdapterDescriptor"]
  end
  Route --> Auth
  Auth -.->|"planning binding only"| Bind
```

Caption: historical verification at `8abd2d5`. Not verification of the current code. Solid edge: `authorize_route` returns `MasterRouteExecutionAuthority` (source: `functions/shared/wilvor_ai/master_routing.py`). Dotted edge: planned path; `MasterSpecialistAdapterDescriptor` is a planning binding and nothing on the object can be called to run a specialist (source: `functions/shared/wilvor_ai/master_specialist_adapters.py`, `MasterSpecialistAdapterDescriptor`). No specialist invocation edge is drawn.

```mermaid
sequenceDiagram
  participant Plan as Master plan
  participant Auth as MasterRouteExecutionAuthority
  Note over Plan, Auth: DENIED. Production execution is not representable. Do not add a specialist call here.
```

Caption: shape required while `MasterRouteExecutionAuthority` has only `DENIED`. Source: `functions/shared/wilvor_ai/master_routing.py`, `MasterRouteExecutionAuthority`. Historical verification at `8abd2d5`. Not verification of the current code.

## Interfaces

| Component | Public entry | Catalog |
| --- | --- | --- |
| Live Ops | `LiveOpsSpecialist.run` | `LIVE_OPS_TOOLS` (8 names) |
| Historical | `HistoricalAnalyticsSpecialist.run` then `finalize_historical_specialist_run` | `HISTORICAL_ANALYTICS_TOOLS` (4 names) |
| Decision | `DecisionSpecialist` over `DecisionToolsAdapter.invoke` | `DECISION_TOOLS` (4 names) |
| Master planning | `authorize_route` | none; execution denied |
| Master production adapter | `reserved_production_adapter` | raises; no grant |

`import wilvor_ai` remains contracts-only. It does not load `historical_analytics`, `live_ops`, `wilvor_historical_query`, or provider SDKs (source: `functions/shared/wilvor_ai/__init__.py`; `functions/shared/wilvor_ai/README.md`).

Catalog names were checked against exported catalogs and `READ_ONLY_TOOL_NAMES_BY_SPECIALIST` (source: `functions/shared/wilvor_ai/master_execution_policy_boundary.py`, `READ_ONLY_TOOL_NAMES_BY_SPECIALIST`; tests: `tests/contracts/test_master_execution_policy_boundary.py`, `test_vocabularies_and_read_only_catalogs_are_the_existing_ones`). Details are on [Tools and data access](tools.md).

## Known limitations

- Master execution authority is `DENIED`. Production authority is `DENIED`. Source authenticity is `NOT_ESTABLISHED`. Request coverage is `NOT_ESTABLISHED` (source: `functions/shared/wilvor_ai/master_routing.py`; `functions/shared/wilvor_ai/master_contracts.py`; `functions/shared/wilvor_ai/master_evidence_admission.py`).
- No production specialist dispatch is authorized by offline contracts. Adapter code must not dispatch from `CONTRACT_READY_FOR_FUTURE_INTEGRATION` (source: `functions/shared/wilvor_ai/MA5_MA7_INTERFACE_CONTRACT.md`, section 11.4; `functions/shared/wilvor_ai/master_execution_preflight.py`).
- `POST /ai/query` stays uncreated in `envs/dev`. The dashboard has `/copilot` and no `/ai` path. `/copilot` does not activate production AI.
- `modules/historical_analytics` does not attach `aws_iam_policy.query`. `envs/dev` attaches that policy once to `module.ai_host.role_name` while `enable_invoke = false`. The attachment is not an Agent API principal and is not production execution (source: `envs/dev/main.tf`, `aws_iam_role_policy_attachment.ai_host_historical_analytics_query`; tests: `tests/infrastructure/terraform/test_historical_analytics_query_iam_contract.py`, `test_policy_is_disposable_and_not_on_operational_api_or_data_plane`).
- LIVE records harness files. A current live pass is UNKNOWN. Stored live JSON artifacts, when cited in older README text, are historical evidence of a past run and are not a new pass (source: `functions/shared/wilvor_ai/README.md`).
- This page does not certify that any AWS account matches the repository.

## Historical record

An earlier revision said Terraform AI wiring, a dashboard `/ai` surface, and an unattached query policy were absent at `8abd2d5`. Current text follows `module.ai_host`, `enable_ai_query_route = false`, `/copilot`, and the dev-root attachment of the historical query policy to the closed AI host role. Stale package-README sentences remain on `functions/shared/wilvor_ai/README.md` under Historical record.

## Sources

- Source: `functions/shared/wilvor_ai/master_contracts.py` (`MasterSpecialist`, `MasterProductionAuthority`, `reserved_production_adapter`)
- Source: `functions/shared/wilvor_ai/master_routing.py` (`MasterRouteExecutionAuthority`, `authorize_route`)
- Source: `functions/shared/wilvor_ai/live_ops_specialist.py` (`LiveOpsSpecialist`)
- Source: `functions/shared/wilvor_ai/historical_specialist.py` (`HistoricalAnalyticsSpecialist`)
- Source: `functions/shared/wilvor_ai/decision_specialist.py` (`DecisionSpecialist`)
- Source: `functions/shared/wilvor_ai/master_orchestration.py` (`run_synthetic_master_workflow`)
- Source: `functions/shared/wilvor_ai/master_specialist_adapters.py` (`MasterSpecialistAdapterDescriptor`)
- Source: `functions/shared/wilvor_ai/contracts.py` (`V1_AUTHORITY`, `AgentAuthorityMode`)
- Tests: `tests/contracts/test_master_execution_policy_boundary.py` (`test_vocabularies_and_read_only_catalogs_are_the_existing_ones`)
