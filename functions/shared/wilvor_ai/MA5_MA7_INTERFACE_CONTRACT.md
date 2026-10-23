# MA5 / MA6 / MA7 shared interface contract

Status: interface agreement for later parallel work. This document is not operational readiness, production authorization, or a statement that supplied evidence came from AWS.

The existing Wilvor dashboard application remains the single application surface. `functions/operational_api` is the dashboard Operational API. This contract does not route AI execution through that Lambda, and it does not create a second request-identity system or a second product.

## IMPLEMENTED NOW

`functions/shared/wilvor_ai/master_parallel_contracts.py`

The module records one Master request, the MA2 plan that already binds its steps, the MA4 evidence inputs later code may pass to the existing check functions, comparison requests, a nonfactual response-policy handoff, an execution-preflight report, and a safety handoff. It does not call admission, corroboration, comparison, a specialist, or a renderer.

## PROPOSED FOR LATER

MA5 response synthesis, MA6 specialist execution and provenance proof, and MA7 end-to-end safety workflows. Those workstreams import this module. They do not widen it by editing MA1–MA4.

## 1. Existing interfaces being reused

| Contract | Role |
|---|---|
| `MasterPlanningContext` | Application-owned Master request id, correlation id, application request time, and canonical user text |
| `MasterRoutePlan` / `MasterRouteStepPlan` | The only source of route steps and invocation ids |
| `MasterInvocationAuthorization` | Specialist, purpose, requirement, and native specialist request id already chosen by MA2 |
| `MasterEvidenceAdmissionInput` | One authorization plus one native artifact for MA4.1 |
| `MasterEvidenceCorroborationInput` | That admission input plus an optional Historical run for MA4.2 |
| `LiveOpsAdmissionSources` | Public Live Ops request, context, and result |
| `SpecialistResult` | Final Historical artifact |
| `HistoricalSpecialistRunResult` | Optional original Historical run, or a non-final artifact MA4.1 still rejects |
| `DecisionSpecialistRunResult` | Decision artifact |
| `MasterComparisonRequest` | Untrusted comparison selectors and native invocation ids |
| `MasterSourceAuthenticity` | Only `NOT_ESTABLISHED` |
| `MasterProductionAuthority` | Constructors in this module accept only `DENIED` |
| `MasterRouteExecutionAuthority` | Only `DENIED` exists |
| `MasterRequestCoverage` | Only `NOT_ESTABLISHED` exists |
| `admit_master_evidence` | MA4.1. Not called by this module |
| `corroborate_master_evidence` | MA4.2. Not called by this module |
| `assess_master_evidence_comparability` | MA4.3. Not called by this module |

MA3 `SyntheticWorkerResult` / `SyntheticWorkflowResult` are recognized by type name so they cannot be stored as evidence. They are not worker adapters. This module does not import `master_orchestration`.

## 2. New interfaces and their purpose

| Type | Purpose |
|---|---|
| `MasterParallelContractBundle` | One request, its plan, evidence inputs, and comparison requests |
| `MasterInvocationBinding` | A step id plus the plan authorization. Derived from the plan |
| `MasterResponsePolicyHandoff` | Closed nonfactual state, limitation codes, and a fixed status sentence |
| `MasterExecutionPreflightReport` | One of four structural states, with execution still denied |
| `MasterSafetyHandoff` | The bundle, policy, and preflight MA7 can read offline |
| `MasterResponsePolicyState` | `NOT_EVALUATED`, `UNSUPPORTED`, `UNAVAILABLE`, `INSUFFICIENT_EVIDENCE` |
| `MasterResponseLimitation` | Closed reasons a later response must keep |
| `MasterExecutionPreflightState` | `NOT_EXECUTABLE`, `BLOCKED_BY_AUTHORIZATION`, `BLOCKED_BY_MISSING_PROVENANCE`, `CONTRACT_READY_FOR_FUTURE_INTEGRATION` |
| `MasterProvenanceRequirement` | Seven future proof categories. None can be marked satisfied here |

Helpers: `response_policy_for`, `execution_preflight_for`, `historical_run_if_supplied`, `live_ops_public_sources`.

Schema: `wilvor.ai.master_parallel_contracts.v1`.

## 3. Exact input and output contracts

### MA5 — response policy

Input: a `MasterParallelContractBundle` and a `MasterResponsePolicyState`.

Output: `MasterResponsePolicyHandoff`.

- `master_request_id` and `invocation_ids` are read from the bundle.
- `status_text` must be the fixed sentence for that state.
- `claims_admitted` is false.
- There is no answer field.
- Required limitations include source authenticity not established, production authority denied, no operational claim, and execution denied.
- `UNAVAILABLE` text states that no count or zero is inferred.

MA5 may later map MA4 statuses onto these four states. It may not add aircraft, SIGMET, risk, or recommendation sentences.

### MA6 — execution preflight

Input: a `MasterParallelContractBundle`.

Output of `execution_preflight_for`:

- `BLOCKED_BY_AUTHORIZATION` when the plan has any step, because MA2 execution authority is `DENIED`.
- `NOT_EXECUTABLE` when the plan has no steps.
- `missing_provenance` is every `MasterProvenanceRequirement`, in enum order.
- `executable` is false.
- Production authority is `DENIED`.

`CONTRACT_READY_FOR_FUTURE_INTEGRATION` and `BLOCKED_BY_MISSING_PROVENANCE` can be named explicitly. Both still require the full missing-provenance tuple, `executable is False`, and denied authority. This module never returns `CONTRACT_READY_FOR_FUTURE_INTEGRATION` from `execution_preflight_for`. MA2 has no granted-execution value. Adding one is a future reviewed contract change, not a field this module may set.

### MA7 — safety handoff

Input: one bundle, the policy built from that same bundle, and the preflight built from that same bundle.

Output: `MasterSafetyHandoff`.

A policy or preflight from a different bundle is `master_request_mismatch`. MA7 reads identity, bindings, evidence input types, comparison requests, response limitations, execution denial, and provenance gaps. It does not receive a test runner from this module.

## 4. Data ownership and trust boundaries

| Data | Owner | Trust |
|---|---|---|
| Master request id, correlation id, application request time, canonical user text | Application `MasterPlanningContext` | Planning identity. Not source authentication |
| Step id, invocation id, specialist request id | MA2 `MasterRoutePlan` | Structural binding. Not execution permission |
| User-supplied aircraft id, callsign, region, timestamp, comparison hints | Existing untrusted hint fields | Not authenticated identity or equivalence |
| Historical `SpecialistResult` and optional run | Supplied evidence | Offline material for MA4.2 |
| Decision run result | Supplied evidence | Not a production Decision dispatch |
| Live Ops public request, context, result, evidence references | Public contract | Not raw tool results |
| MA4 admission, corroboration, and comparison results | The MA4 functions, when a later caller invokes them | Not certificates stored on this bundle |
| Response status sentence | This module's closed map | Not an operational claim |

The bundle does not accept a caller-built `MasterEvidenceAdmission`, `MasterEvidenceCorroboration`, or `MasterComparisonAssessment` as proof that a check ran.

## 5. Invocation and request identity rules

- The planning context and the route plan must share `master_request_id` and `canonical_user_text`.
- Each planned authorization must carry that same correlation id and application request time.
- Evidence authorizations must equal the planned authorization for that invocation id.
- An evidence input whose invocation id is not on the plan is `invocation_not_in_plan`. A result cannot create a step.
- Invocation ids are unique across the plan and across evidence inputs.
- At most four steps and four evidence inputs (`MAX_ROUTE_STEPS`).
- At most one Decision invocation (`MAX_DECISION_STEPS`).
- Comparison requests use native invocation ids from that same plan. Left and right must differ. Hints stay hints.

## 6. Source-authenticity limitations

IMPLEMENTED NOW: `source_authenticity` cannot be anything except `NOT_ESTABLISHED`. `evidence_verified` is not a field this module can set true. `CONSISTENT_WITH_SUPPLIED_EVIDENCE` is an MA4.2 finding about supplied material. It is not an authenticated operational read. Comparability eligibility stays inside `BLOCKED`, `UNSUPPORTED`, `NOT_ESTABLISHED`, and `CONTRACT_CHECKABLE_ONLY`. None of those is operational agreement or conflict. `BINDING_UNRESOLVED` stays unresolved.

PROPOSED FOR LATER: a separately reviewable MA6 provenance mechanism must show, before any production read is treated as authentic:

- Trusted source adapter identity
- Authorized execution context
- Source and read provenance
- Request and entity binding
- Observation and evaluation timestamps appropriate to the specialist
- Source-version integrity where a version is claimed
- Completeness and geographic scope when a population-wide outcome is claimed

This module cannot mark any `MasterProvenanceRequirement` satisfied.

## 7. Offline-only execution policy

IMPLEMENTED NOW:

- `execution_authority` is `DENIED`.
- `production_authority` is `DENIED`.
- `request_coverage` is `NOT_ESTABLISHED`.
- `end_to_end_fulfilled` is false.
- `executable` on a preflight report is false.
- No constructor calls AWS, a model provider, LangGraph, or a specialist runner.

PROPOSED FOR LATER: production execution only after the provenance mechanism in section 6 is reviewed and MA2 grows an explicit execution authority in a separate change. Until then, retain `DENIED`.

## 8. Response claim admissibility restrictions

A response handoff may carry only the four policy states, the closed limitation codes, and the fixed status sentence. It must not carry:

- An operational aircraft state, track, or instruction
- A SIGMET, hazard geometry, or exposure conclusion
- A risk level, including a conversion of unknown or unavailable into zero
- A recommendation or pilot instruction
- A claim that supplied tool results were read from a production source
- Free-form model or user prose presented as evidence

`claims_admitted=True` is rejected.

## 9. Import and dependency boundaries

The production module may import Master contracts, MA2 routing, MA4 admission, corroboration, and comparability types, specialist result types, and the public Live Ops admission sources.

It must not import boto3, botocore, Anthropic, OpenAI, LangGraph, `historical_specialist`, `decision_specialist`, `live_ops`, `live_ops_adapter`, `master_orchestration`, `wilvor_operational`, or test fixtures.

No new third-party dependency is added.

## 10. Proposed exclusive file ownership

These files are not created by this foundation.

### Agent 1 / MA5

- `functions/shared/wilvor_ai/master_response_policy.py`
- `functions/shared/wilvor_ai/master_response_renderer.py`
- `tests/contracts/test_master_response_policy.py`
- `tests/contracts/test_master_response_renderer.py`

### Agent 2 / MA6

- `functions/shared/wilvor_ai/master_execution_preflight.py`
- `functions/shared/wilvor_ai/master_specialist_adapters.py`
- `tests/contracts/test_master_execution_preflight.py`
- `tests/contracts/test_master_specialist_adapters.py`

### Agent 3 / MA7

- `tests/fixtures/ma7_master_safety_cases.py`
- `tests/contracts/test_ma7_master_safety_matrix.py`
- `tests/contracts/test_ma7_master_safety_import_isolation.py`

This foundation owns only:

- `functions/shared/wilvor_ai/master_parallel_contracts.py`
- `functions/shared/wilvor_ai/MA5_MA7_INTERFACE_CONTRACT.md`
- `tests/contracts/test_master_parallel_contracts.py`
- `tests/contracts/test_master_parallel_contracts_import_isolation.py`

Existing specialist runners, dashboard files, and Operational API files stay unchanged.

## 11. Explicit integration checkpoints

1. This interface is reviewed before MA5, MA6, or MA7 edit code.
2. Each agent writes only the files in section 10 and imports this module.
3. MA5 calls the existing MA4 functions when it needs a check. It does not treat a stored MA4 object as a certificate.
4. MA6 preflight remains denied until a reviewed provenance design exists. Adapter code must not dispatch from `CONTRACT_READY_FOR_FUTURE_INTEGRATION`.
5. MA7 builds offline fixtures through `MasterSafetyHandoff` and the existing MA4 fixtures. It does not call deployed AWS or live providers.
6. Dashboard or Operational API integration is a later change outside these files. It must keep the Operational API Lambda off the AI execution path.
7. Any change that would add `AUTHENTICATED`, `GRANTED`, or a second execution authority stops and is reviewed as a contract change. It is not made inside a workstream.

## 12. Open requirements before any production execution

- Reviewed proof for every `MasterProvenanceRequirement` that a specific read needs.
- An explicit MA2 execution authority other than `DENIED`, if execution is ever to be representable.
- Native specialist request and result binding checked against the executing adapter, not against a caller-supplied object.
- Separation of supplied-evidence consistency from source authentication.
- Population and geographic claims backed by completeness and scope evidence.
- Unknown, unavailable, and unresolved bindings preserved rather than rendered as zero or as a current factual answer.
- No use of MA3 synthetic results as production evidence.
- No live aviation provider calls and no AWS writes from these contracts.
