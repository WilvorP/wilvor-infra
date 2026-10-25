# Decision Specialist

**Canonical page:** this page
**Verified against:** `8abd2d5` on `docs/ws5-ai-copilot`
**Maturity:** CODE, CONTRACT, LIVE
**Production authorization:** `MasterRouteExecutionAuthority.DENIED`; independently trusted Decision target is not available

## Purpose

This page describes the Decision Specialist (Decision Expert), its four-tool catalog, DE1/DE2/DE3 split, and the prohibition on selecting clearances, flight levels, or diversions. Renderer limit sentences are restated on [Evidence and safety](evidence-and-safety.md).

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `functions/shared/wilvor_ai/decision_specialist.py` (`DecisionSpecialist`) |
| Terraform | TF absent at `8abd2d5` (search: `*.tf` for `wilvor_ai`, `DecisionSpecialist`) | named search |
| Unit tests | UNIT for the operator harness module | `tests/unit/scripts/test_validate_decision_specialist_anthropic_live.py` |
| Contract tests | CONTRACT | `tests/contracts/test_decision_specialist.py`; `tests/contracts/test_decision_tools.py`; `tests/contracts/test_decision_evidence_verifier.py`; `tests/contracts/test_decision_expert_closure.py` |
| Infrastructure tests | none found | named search |
| Integration tests | CONTRACT integration-style tests exist; they are not `tests/integration/` | `tests/contracts/test_decision_tools_integration.py` |
| Live harness | LIVE | `scripts/validate_decision_specialist_anthropic_live.py` (`WILVOR_RUN_LIVE_DECISION_ANTHROPIC` and wire/terminal/transport/routing variants) |
| Current live pass | UNKNOWN | this documentation task did not invoke the provider |
| Deployment | UNKNOWN | not verified |
| Production authorization | `DENIED`; no trusted Decision target | `functions/shared/wilvor_ai/master_decision_binding.py`; `functions/shared/wilvor_ai/master_decision_execution_readiness.py` |

## Behavior

DE3 (`DecisionSpecialist`) dispatches the closed Decision Tools, then DE1 verifies claims and DE2 renders factual output. It does not calculate aviation facts, select an encounter, rank a recommendation, or treat persisted evidence as current (source: `functions/shared/wilvor_ai/decision_specialist.py`).

`COMPLETED` means `FINAL_CLAIMS` ran through DE1 and DE2 with no skipped fanout. It does not mean verification passed. `PARTIAL` means `FINAL_CLAIMS` ran after a skipped persisted fanout. Orchestration status is not the DE1 verification outcome (source: `functions/shared/wilvor_ai/decision_specialist.py`; `functions/shared/wilvor_ai/decision_specialist_runtime_contracts.py`, `DecisionSpecialistRunStatus`).

`DecisionTargetMode` is `AIRCRAFT` or `DIRECT_PERSISTED`. One trusted target. User text is not a second target (source: `functions/shared/wilvor_ai/decision_specialist_runtime_contracts.py`, `DecisionTargetMode`).

There are exactly four Decision tools and no fifth (source: `functions/shared/wilvor_ai/decision_tools.py`, `DECISION_TOOLS`):

- `get_current_decision_context(aircraft_id)` — `TemporalScope.CURRENT`
- `get_current_risk_evidence(aircraft_id)` — `CURRENT`
- `get_current_recommendation(aircraft_id)` — `CURRENT`
- `get_persisted_airport_candidate_evidence(recommendation_id)` — `TemporalScope.PERSISTED`

Do not describe CURRENT plus PERSISTED as `HYBRID`. `HYBRID` elsewhere means current plus historical synthesis (source: `functions/shared/wilvor_ai/README.md`; `functions/shared/wilvor_ai/contracts.py`, `TemporalScope`).

All four tools are `READ_ONLY_ADVISORY` with `RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT`. The catalog constructor rejects any other authority mode (source: `functions/shared/wilvor_ai/decision_tools.py`, `DecisionToolSpec.__post_init__`).

Budgets: `MAX_MODEL_TURNS = 4`, `MAX_TOOL_CALLS = 4`, `MAX_PERSISTED_AIRPORT_CALLS = 3` (source: `functions/shared/wilvor_ai/decision_specialist.py`). Instruction ref: `wilvor.decision.specialist.v1`.

Currentness stays in `wilvor_operational`. Decision contracts report those results. They do not define a second meaning of current (source: `functions/shared/wilvor_ai/README.md`). `get_current_recommendation` returns the current set, including zero or many recommendations, and does not select a winner. `EVALUATE_DIVERSION` is stored advisory evidence, not a clearance or a route (source: `functions/shared/wilvor_ai/decision_tools.py`).

`get_persisted_airport_candidate_evidence` reads airport-assessment rows already stored for the evaluation id on one recommendation. It is not a current airport selector, a route planner, or a diversion planner. COMPLETE means persisted assessment scoring completed. Route, runway, and congestion evidence stay unavailable. The persisted evidence is not asserted to be current (source: `functions/shared/wilvor_ai/decision_tools.py`).

### Verification and rendering

Proposed Decision claims are untrusted. Full raw `ToolResult` objects are the only verification authority. `DecisionToolProjection` and `DecisionEvidenceSnapshot` are not consulted (source: `functions/shared/wilvor_ai/decision_evidence_verifier.py`, `verify_decision_evidence`).

Factual sentences come only from a `DecisionVerificationResult`. The renderer does not verify claims, read raw tool results, call a model, or import operational runtime (source: `functions/shared/wilvor_ai/decision_answer_renderer.py`). Source sentences include:

- "Stored recommendation actions are advisory evidence only."
- "The stored EVALUATE_DIVERSION label is not a clearance, not a selected diversion, not a route, and not a dispatch or ATC instruction."
- "No encounter is selected by this answer."
- "No recommendation is selected by this answer."
- "A stored LOW level is not a statement that the aircraft is safe."
- "Unavailable evidence does not establish an unsafe result."
- "A candidate count of zero does not mean that no safe airport exists."
- "Risk absence is not a LOW risk classification."
- "Persisted airport-evaluation evidence is not current airport suitability."

(source: `functions/shared/wilvor_ai/decision_answer_renderer.py`).

The Decision Specialist does not generate routes, waypoints, flight plans, route safety, ATC instructions, dispatch instructions, landing instructions, altitude instructions, or autonomous flight-control instructions. Those names appear in `PROHIBITED_V1_CAPABILITIES` (source: `functions/shared/wilvor_ai/contracts.py`, `PROHIBITED_V1_CAPABILITIES`; `functions/shared/wilvor_ai/README.md`).

The LLM cannot select an ATC clearance, flight level, or operational diversion command. Model-visible target arguments remain only `aircraft_id` or `recommendation_id`. The model may request approved tools, inspect snapshots, propose typed claims, and return `UNSUPPORTED` or `REFUSAL`. It may not authoritatively calculate risk, create recommendations, choose a winning recommendation, choose a diversion airport, generate routes, verify its own claims, or write the authoritative factual answer (source: `functions/shared/wilvor_ai/README.md`).

### Master boundary

`prepare_master_decision_binding`: naming a Decision invocation does not authorize a Decision target. An independently trusted target authorization mechanism is not implemented (source: `functions/shared/wilvor_ai/master_decision_binding.py`). The Decision readiness module states: "A genuinely trusted Decision target is the remaining category. No such target is available." Production authority stays `DENIED`. Source authenticity stays `NOT_ESTABLISHED`. Request coverage stays `NOT_ESTABLISHED` (source: `functions/shared/wilvor_ai/master_decision_execution_readiness.py`).

A matching aircraft id on the planning context, `expected_aircraft_id`, a Decision artifact, and a comparison hint remains a comparison candidate. It is not a trusted target (tests: `tests/contracts/test_sprint3a_ma7_safety_regression.py`, `test_decision_comparison_candidates_cannot_authorize_targets`).

## Interfaces

Trusted runtime on `DecisionToolsRuntime`: `tables`, `now_epoch`, `query_timestamp_utc`, optional `correlation_id` (source: `functions/shared/wilvor_ai/decision_tools.py`, `DecisionToolsRuntime`). Domain operations: `wilvor_ai.decision_context` and `wilvor_ai.persisted_airport_evidence`, which read through `wilvor_operational`.

Provider: `functions/shared/wilvor_ai/providers/anthropic_decision_messages.py`. `DEFAULT_MODEL_ID` and `APPROVED_MODEL_IDS` are `claude-sonnet-4-6`. The adapter accepts an injected Messages client. The harness uses harness-local memory tables. There is no AWS client and no network during dry-run (source: `scripts/validate_decision_specialist_anthropic_live.py`).

## Known limitations

- No stored risk is not LOW. No recommendation is not MONITOR. Multiple encounters do not create a selected encounter. Multiple recommendations do not create a winner. Multiple persisted candidates do not create a selected airport (source: `functions/shared/wilvor_ai/README.md`).
- COMPLETE is not a safe, current, selected, recommended, or cleared airport.
- Route alternatives, route safety, runway evidence, and congestion stay unavailable (`validated_alternative_available` is false).
- Master Decision binding does not dispatch the specialist and does not mint `trusted_aircraft_id`.
- Current live-harness pass is UNKNOWN.
- Production client construction, secrets, runtime composition, and deployment remain future integration work (source: `functions/shared/wilvor_ai/README.md`).

## Historical record

DT2 README text said there was no decision-tool catalog and no Decision Expert. DT5 and the Decision Expert section of the package README supersede that for current status. This page maps the completed specialist without deleting that phase history.

## Sources

- Source: `functions/shared/wilvor_ai/decision_specialist.py` (`DecisionSpecialist`)
- Source: `functions/shared/wilvor_ai/decision_tools.py` (`DECISION_TOOLS`)
- Source: `functions/shared/wilvor_ai/decision_evidence_verifier.py` (`verify_decision_evidence`)
- Source: `functions/shared/wilvor_ai/decision_answer_renderer.py`
- Source: `functions/shared/wilvor_ai/master_decision_execution_readiness.py`
- Tests: `tests/contracts/test_decision_specialist.py`
- Tests: `tests/contracts/test_decision_tools.py`
- Tests: `tests/contracts/test_sprint3a_ma7_safety_regression.py` (`test_decision_comparison_candidates_cannot_authorize_targets`)
