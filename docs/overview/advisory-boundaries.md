# Advisory boundaries

**Canonical page:** this page for platform-level advisory wording. AI contract detail: [`functions/shared/wilvor_ai/README.md`](../../functions/shared/wilvor_ai/README.md). Console wording: [`dashboard/README.md`](../../dashboard/README.md).
**Verified against:** `8abd2d5` on `docs/ws1-platform-foundations`
**Maturity:** CODE, CONTRACT, LIVE
**Production authorization:** `V1_AUTHORITY.mode = READ_ONLY_ADVISORY`; `MasterRouteExecutionAuthority = DENIED` (source: `functions/shared/wilvor_ai/contracts.py`, `V1_AUTHORITY`; `functions/shared/wilvor_ai/master_routing.py`, `MasterRouteExecutionAuthority`)

## Purpose

This page records the aviation advisory boundary, human-review rule, and the split between deterministic services and AI library code. It does not grant clearances or describe production execution.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `functions/shared/wilvor_ai/contracts.py`, `master_routing.py`, `decision_answer_renderer.py` |
| Terraform | TF absent | search `*.tf` for `wilvor_ai` at `8abd2d5` |
| Unit tests | none found for this page's quotes | `not read` as UNIT for renderer strings |
| Contract tests | CONTRACT | `tests/contracts/test_master_routing.py` |
| Infrastructure tests | none found | this page is policy text from code |
| Integration tests | none found | AI is not an Operational API route |
| Live harness | LIVE | `scripts/validate_*anthropic_live.py` exist; they are not a production grant |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | `DENIED` / `READ_ONLY_ADVISORY` | symbols named above |

## Behavior

Wilvor is advisory decision support. It is not air traffic control and not a flight-control system (source: `dashboard/README.md`).

Risk scoring, airport assessment, trajectory projection, encounter geometry, and recommendation logic remain in the deterministic backend. The console does not recompute them (source: `dashboard/README.md`).

Deterministic services remain authoritative for aircraft state, projections, hazards, encounters, risk, airport assessments, recommendations, alerts, geometry, trajectories, and state transitions. AI text may explain and summarize verified deterministic results. It does not replace those calculations, and it does not convert missing evidence into assumed facts (source: `functions/shared/wilvor_ai/README.md`).

`FinalAIResponse.human_review_required` is a required Boolean. Qualified human review is required for safety-sensitive operational guidance, including routing or diversion recommendations and other output intended to support an aviation operational action. A structurally valid `False` is not a waiver of that rule (source: `functions/shared/wilvor_ai/README.md`; field on `wilvor.ai.response.v1` in `functions/shared/wilvor_ai/contracts.py`).

`AgentAuthorityMode` contains `READ_ONLY_ADVISORY` only (source: `functions/shared/wilvor_ai/contracts.py`, `AgentAuthorityMode`).

Allowed V1 capabilities (source: `functions/shared/wilvor_ai/contracts.py`, `ALLOWED_V1_CAPABILITIES`):

- `RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT`
- `RETRIEVE_HISTORICAL_ANALYTICS`
- `EXPLAIN_EXISTING_RISK`
- `EXPLAIN_EXISTING_RECOMMENDATIONS`
- `SUMMARIZE_OPERATIONAL_STATE`
- `COMPARE_VERIFIED_EVIDENCE`
- `GENERATE_BUSINESS_READABLE_SUMMARIES`
- `GENERATE_VISUALIZATION_SPECIFICATIONS`

Prohibited V1 capabilities (source: `functions/shared/wilvor_ai/contracts.py`, `PROHIBITED_V1_CAPABILITIES`):

- `MUTATE_OPERATIONAL_STATE`
- `MUTATE_ALERTS`
- `CREATE_OR_OVERWRITE_DETERMINISTIC_RECOMMENDATIONS`
- `ALTER_RISK_RESULTS`
- `ALTER_AIRCRAFT_PROJECTIONS`
- `ALTER_HAZARD_RECORDS`
- `EXECUTE_REROUTES`
- `ISSUE_AUTONOMOUS_FLIGHT_CONTROL_INSTRUCTIONS`
- `ISSUE_LANDING_INSTRUCTIONS`
- `ISSUE_ALTITUDE_INSTRUCTIONS`
- `ISSUE_ATC_INSTRUCTIONS`
- `MUTATE_AWS_INFRASTRUCTURE`

An allowed capability name is not evidence that a deployed runtime exists.

`MasterRouteExecutionAuthority` defines `DENIED` and no other member. The docstring states: "Planning permission only. Production execution is not representable." (source: `functions/shared/wilvor_ai/master_routing.py`, `MasterRouteExecutionAuthority`; tests: `tests/contracts/test_master_routing.py`, `test_live_ops_only_is_bounded_and_not_executable_authority`).

`MasterSpecialistAdapterDescriptor` is a planned binding. Nothing on the object can be called to run a specialist (source: `functions/shared/wilvor_ai/master_specialist_adapters.py`, `MasterSpecialistAdapterDescriptor`).

Renderer limit sentences in `functions/shared/wilvor_ai/decision_answer_renderer.py` include these examples. They are not the complete set in that file:

- "Stored recommendation actions are advisory evidence only."
- "The stored EVALUATE_DIVERSION label is not a clearance, not a selected diversion, not a route, and not a dispatch or ATC instruction."
- "A stored LOW level is not a statement that the aircraft is safe."

This page does not document a clearance, dispatch decision, ATC instruction, landing instruction, altitude instruction, or reroute.

The Operational API is not an AI execution path. `functions/operational_api` has no `wilvor_ai` import. Dashboard `ROUTES` has no `/ai` path (source: `dashboard/src/routes/routeDefinitions.ts`).

## Interfaces

None beyond the symbols cited above. There is no AI HTTP route in `modules/operational_api/api.tf` `local.route_keys`.

## Known limitations

- Maturity labels are defined only in [`../DOCUMENTATION_STANDARDS.md`](../DOCUMENTATION_STANDARDS.md).
- Live Anthropic harnesses authorize only the invocation each harness defines. They are not a product grant.
- `READ_ONLY_ADVISORY` is not source-read authority (source: `functions/shared/wilvor_ai/master_live_ops_execution_readiness.py`, `READ_ONLY_ADVISORY_IS_NOT_SOURCE_READ_AUTHORITY`).

## Historical record

No superseded statement is stored on this page.

## Sources

- Source: `dashboard/README.md`
- Source: `functions/shared/wilvor_ai/README.md`
- Source: `functions/shared/wilvor_ai/contracts.py` (`AgentAuthorityMode`, `ALLOWED_V1_CAPABILITIES`, `PROHIBITED_V1_CAPABILITIES`, `V1_AUTHORITY`)
- Source: `functions/shared/wilvor_ai/master_routing.py` (`MasterRouteExecutionAuthority`)
- Source: `functions/shared/wilvor_ai/master_specialist_adapters.py` (`MasterSpecialistAdapterDescriptor`)
- Source: `functions/shared/wilvor_ai/decision_answer_renderer.py`
- Tests: `tests/contracts/test_master_routing.py` (`test_live_ops_only_is_bounded_and_not_executable_authority`)
