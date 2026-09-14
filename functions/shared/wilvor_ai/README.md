# Wilvor AI Operations Copilot Phase 0

Phase 0 defines dependency-free data contracts and the V1 authority boundary
for future AI Operations Copilot components. It does not implement an AI
runtime or retrieve operational data.

Wilvor's deterministic services remain authoritative for aircraft state,
projections, hazards, encounters, risk, airport assessments, recommendations,
alerts, geometry, trajectories, and state transitions. Future AI components
may explain and summarize verified deterministic results; they must not
replace those calculations or convert missing evidence into assumed facts.

## Contract package

`wilvor_ai.contracts` uses only the Python standard library. Its frozen
dataclasses use snake_case wire fields and explicit schema versions:

- `wilvor.ai.evidence.v1`
- `wilvor.ai.tool_result.v1`
- `wilvor.ai.response.v1`
- `wilvor.ai.authority.v1`

`to_dict()` and `from_dict()` provide deterministic semantic round trips.
They preserve `UNKNOWN`, `None`, `False`, numeric zero, and intentionally
empty collections. Dictionary or JSON key ordering is not part of the public
contract.

Invalid contract construction raises `ContractValidationError`. Consumers may
use its structured `errors` categories, but human-readable exception wording
is not a compatibility guarantee.

## Temporal scope

`TemporalScope` has three values:

- `CURRENT`: evidence for current operational state.
- `HISTORICAL`: evidence for a historical period or historical analytics.
- `HYBRID`: a result that actually combines current and historical evidence.

Scope describes the result or evidence represented, not the scope of the
originating user request. In a future hybrid workflow, a live operational tool
result remains `CURRENT` and a historical analytics tool result remains
`HISTORICAL`. A later final response may combine their evidence and declare
`HYBRID`.

Only a `ToolResult` that itself declares `HYBRID` must contain both CURRENT
and HISTORICAL evidence. Phase 0 performs no temporal classification, routing,
tool execution, or aggregation.

## Evidence and provenance

`Evidence` identifies:

- the deterministic source;
- zero or more supporting `SourceRecord` values;
- each record ID, optional source version, and optional event timestamp;
- the query timestamp;
- contextual freshness;
- source confidence where applicable;
- limitations;
- the originating tool-call ID;
- temporal scope;
- optional first-class deterministic provenance: `SourceCompleteness`,
  `MatchCardinality`, `QueryExecutionTrace` records, and `error_code`.

Zero source records are valid for a verified no-match query or an unavailable
source. Optional source versions and event timestamps remain `None` when they
are unavailable; the contract never fabricates them.

Optional provenance is generic deterministic-tool evidence. It is not a
historical-only metadata blob. Completeness describes source fitness for a
requested evaluation window and is never freshness. When every optional
provenance field is unset, `Evidence.to_dict()` omits those keys so
established `wilvor.ai.evidence.v1` objects keep their existing wire shape.
Missing keys deserialize to `None` / `()`.

First-class `Evidence` is the authoritative provenance surface. A future
verifier must not depend solely on `ToolResult.data["evidence"]` or
`ToolResult.data["coverage"]`. `ToolResult.data` remains the deterministic
application/result payload. Phase 2C.2 publishes bound historical adapters
and `HISTORICAL_ANALYTICS_TOOLS`; those modules are imported explicitly
and are not part of `import wilvor_ai`.

Confidence is represented as `HIGH`, `MEDIUM`, `LOW`, or `UNKNOWN`. These
values retain their supplied meaning. Phase 0 does not calculate or reconcile
projection, risk, recommendation, or future AI-response confidence.

## Contextual freshness

Freshness is contextual and is never calculated by this package.

For CURRENT operational evidence, freshness may describe whether source data
meets the applicable current-state age or timeliness expectation.

For HISTORICAL evidence, the age of the historical event does not make the
evidence stale. Historical freshness concerns the fitness, completeness, or
update state of the analytical source for the requested period, where that is
known.

When freshness cannot be established, use `UNKNOWN` with a limitation. The
contract does not create a global freshness algorithm. It also does not
rewrite existing subsystem vocabularies: for example, SIGMET `AVAILABLE`
describes source availability rather than an age-based freshness grade.

## Tool results

`ToolResult` is the envelope for future approved deterministic tools. Its
status is one of:

- `SUCCESS`
- `PARTIAL`
- `NOT_FOUND`
- `STALE`
- `UNAVAILABLE`
- `UNKNOWN`

The envelope also carries deterministic JSON-compatible data, evidence,
temporal scope, an optional data `as_of_utc`, limitations, tool-call identity,
and optional correlation identity.

`NOT_FOUND` may contain an explicitly verified empty result and a zero count.
That is distinct from `UNKNOWN` or `UNAVAILABLE`. `PARTIAL`, `STALE`,
`UNAVAILABLE`, and `UNKNOWN` retain their limitations rather than presenting
incomplete evidence as complete.

Phase 0 defines no tools and makes no DynamoDB, Operational API, network,
Athena, or Glue calls.

## Final AI responses

`FinalAIResponse` supports:

- answer;
- evidence;
- as-of UTC timestamp;
- confidence;
- limitations;
- visualization specifications;
- navigation specifications;
- explicit `human_review_required` Boolean;
- temporal scope;
- optional correlation identity.

Visualization and navigation collections may be empty. Phase 0 does not
define rendering, navigation behavior, or builders.

`human_review_required` must always be present and Boolean. Both `True` and
`False` are structurally valid. Qualified human review is required for
safety-sensitive operational guidance, including routing or diversion
recommendations and other output intended to support an aviation operational
action. A pure informational summary or historical analysis may represent
`False`.

This package does not decide when review is mandatory. That policy belongs to
later orchestration and evidence-verifier phases.

## V1 authority boundary

`V1_AUTHORITY` declares `READ_ONLY_ADVISORY` mode and immutable allowed and
prohibited capability sets.

The allowed set describes future retrieval, explanation, comparison,
summarization, and visualization-specification capabilities over verified
evidence.

The prohibited set covers:

- operational-state mutation;
- alert mutation;
- deterministic recommendation creation or overwrite;
- risk-result alteration;
- aircraft-projection alteration;
- hazard-record alteration;
- reroute execution;
- autonomous flight-control instructions;
- landing instructions;
- altitude instructions;
- ATC instructions;
- AWS infrastructure mutation.

This declaration is a testable contract, not runtime authorization
enforcement.

## Phase 1 and later

Phase 1 may build reusable application/domain query code consumed separately
by the Operational API and approved AI tools. It must not turn the existing
Operational API Lambda into an AI-tool traffic bottleneck. The dashboard
Operational API and future Agent API/runtime retain separate compute and
concurrency boundaries.

Whenever a later phase introduces operational AWS services or resources,
observability is part of that phase's Definition of Done. Where meaningful,
that includes CloudWatch metrics, dashboards, alarms, logs, latency/error/
failure visibility, and cost/usage visibility.

Phase 0 creates no CloudWatch or other AWS resources.

## Phase 1E Live Operations adapters

Phase 1E adds read-only Live Operations tools in `wilvor_ai.live_ops`. They
wrap committed Phase 1 public domain functions and return Phase 0
`ToolResult` / `Evidence` envelopes. `wilvor_ai.live_ops_mapping` maps already
returned domain objects onto curated JSON payloads, retrieval-driven logical
evidence sources, and Phase 0 status/confidence/freshness.

The adapters do not implement an agent runtime, call AWS, construct boto3
clients, read the wall clock, or decide operational currentness, geography,
joins, or source-version authority. `import wilvor_ai` remains contracts-only
and does not import `wilvor_operational`. Region geospatial evaluation is
loaded only when a region argument is supplied.

The V1 catalog is exactly eight retrieve-only tools:

- `search_current_hazards`
- `search_current_impacts`
- `search_current_encounters`
- `get_observed_network_state`
- `get_aircraft_operational_context`
- `get_hazard_operational_context`
- `get_airport_operational_context`
- `find_aircraft_by_callsign`

All eight are `READ_ONLY_ADVISORY` with
`RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT`. There is no ninth convenience
tool, no provider schema, and no routing or prompt logic.

Each catalog entry now carries `input_fields`: the authoritative AI-visible
field allowlist. `ToolInputField` names the model-controlled arguments only.
It is not OpenAI function schema, JSON Schema, a provider schema, a type
validator, or a replacement for domain request validation. Trusted runtime
context (`call`, `tables`, `now_epoch`, `tool_call_id`, `correlation_id`) is
not part of that allowlist.

A future model/provider integration must build tool schemas from the trusted
catalog or a later bound adapter surface. It must not introspect unbound
Live Ops functions with `inspect.signature`, because those callables still
accept `LiveOpsCall`. Phase 2C historical adapters use constructor-bound
methods plus the same `input_fields` allowlist contract. No LLM/provider
exists in this phase.

All V1 Live Ops evidence uses `FreshnessStatus.UNKNOWN` with
`FRESHNESS_NOT_ESTABLISHED`. `CURRENT` is not `FRESH`. Confirmed-zero results
remain distinguishable from unevaluated-zero `PARTIAL` results.

## Phase 2C Historical Analytics adapters

Phase 2C is complete: AI-safe historical `ToolResult` mapping, a bound
adapter, and the four-tool historical catalog. It does not implement a
model-backed specialist.

Phase 2C.2 exposes the four Phase 2B historical operations through a bound
adapter. Trusted runtime constructs `HistoricalAnalyticsCall` and
`HistoricalAnalyticsAdapter`. Model-visible arguments are only
`HistoricalAnalyticsToolSpec.input_fields`.
`HISTORICAL_ANALYTICS_TOOLS` is ready for a future specialist. It is not
itself a specialist, Master Agent, or Agent API.

The catalog is exactly four retrieve-only tools:

- `summarize_historical_encounters`
- `summarize_historical_risks`
- `summarize_historical_hazard_versions`
- `list_historical_encounters`

All four are `READ_ONLY_ADVISORY` with `RETRIEVE_HISTORICAL_ANALYTICS`.
There is no fifth internal risk-distribution tool, no SQL/query tool, and
no current-state or geography tool. The catalog is not merged with
`LIVE_OPS_TOOLS`.

`HistoricalAnalyticsOperations` remains the domain authority. The adapter
constructs the existing Phase 2B request contracts, injects the bound
`as_of_utc`, and maps every `HistoricalQueryResponse` through
`map_historical_query_response`. Request-construction failures return
`UNKNOWN` / `INVALID_REQUEST` without calling operations and without
fabricating `as_of_utc`, coverage, traces, or a certified zero.

Coverage-certified `VERIFIED_ZERO` maps to `NOT_FOUND` and keeps
first-class exact-zero / `EVALUABLE` proof. Completeness is not freshness.
First-class `Evidence` is the provenance surface. AWS clients, SQL, query
ids, workgroups, and current/geography fallback are not exposed.

Consumers import `wilvor_ai.historical_analytics` explicitly. A
model-backed specialist and Agent API are not implemented.

## Deliberately not implemented

Phase 0 does not add LangGraph, agents, LLM/provider integration, prompts,
credentials, operational tools, shared query extraction, geospatial tools,
Athena, Glue, generated SQL, decision tools, evidence-verifier execution,
visualization generation, an Agent API/runtime, API Gateway changes, an `/ai`
page, dashboard changes, DynamoDB changes, IAM, Terraform, deployment, or
production infrastructure.

Phase 2C-preflight adds the AI-visible field allowlist and generic
Evidence provenance contracts. Phase 2C.1 adds
`wilvor_ai.historical_analytics_mapping`, which translates an already-returned
`HistoricalQueryResponse` into a Phase 0 `ToolResult`. First-class `Evidence`
is the provenance authority. Completeness is not freshness.

Phase 2C.2 adds the bound Historical Analytics adapter and the closed
four-tool catalog in `wilvor_ai.historical_analytics`. Trusted runtime
constructs `HistoricalAnalyticsCall` with `HistoricalAnalyticsOperations`,
`as_of_utc`, and `tool_call_id`. The adapter methods accept only catalog
`input_fields`. `HISTORICAL_ANALYTICS_TOOLS` is the specialist-facing
allowlist; it is not combined with `LIVE_OPS_TOOLS`. Coverage-certified
`VERIFIED_ZERO` maps to `NOT_FOUND` with first-class exact-zero proof.
Adapters do not expose AWS, SQL, query ids, or current/geography fallback.
`import wilvor_ai` remains contracts-only and does not load
`historical_analytics`, `historical_analytics_mapping`, or
`wilvor_historical_query`.

Phase 2C is complete. The following are not implemented yet:

- model-backed Historical Analytics Specialist runtime
- Master Agent
- Agent API
- LLM/provider integration
- runtime Lambda composition
- query-policy attachment to a future Agent API role
- hybrid current + historical synthesis

## Phase 3A-preflight provider-neutral specialist contracts

Phase 3A-preflight adds provider-neutral types only. It does not implement a
specialist runtime, tool-calling loop, dispatcher, evidence verifier,
deterministic renderer, fake provider, or any LLM/provider SDK. Provider
choice remains deferred. Core packages still do not import AWS or model
SDKs.

`ToolInputField` now carries optional generic `value_type` and
`description` for later provider-neutral schema generation. Default
`STRING` and a missing description are omitted from `to_dict()` so
established `{name, required}` payloads remain accepted. This metadata is
not JSON Schema, not an enum allowlist, not min/max, and not domain
validation. Historical catalog types and field descriptions are populated
in 3A.1.

New modules `wilvor_ai.specialist_contracts` and `wilvor_ai.model_contracts`
are dependency-free and root-exported. `import wilvor_ai` remains
contracts-only: it does not load `historical_analytics`, `live_ops`,
`wilvor_historical_query`, or provider SDKs.

Trusted invocation context is `HistoricalSpecialistTrustedContext` with
only `as_of_utc`. That value is the requested deterministic evaluation
instant that will be injected into historical tools if they execute. It is
not automatically `SpecialistResult` evidence.

A future Historical Analytics Specialist constructor binds dependencies:

```text
HistoricalAnalyticsSpecialist(
    provider=ModelProvider,
    operations=HistoricalAnalyticsOperations,
)
```

then:

```text
run(request: SpecialistRequest, context: HistoricalSpecialistTrustedContext)
    -> SpecialistResult
```

`run()` does not accept provider, operations, or AWS clients. A later
Master Agent does not need to know how Athena, coverage, S3, Glue, or
`HistoricalAnalyticsOperations` are constructed.

`ModelDecision` is a discriminated union: exactly one of `TOOL_CALLS`,
`FINAL_CLAIMS`, `UNSUPPORTED`, or `REFUSAL`. Mixed or empty decisions are
rejected at construction. `REFUSAL` means the provider/model did not
perform a valid turn; a future runtime should normally map it to
`PROVIDER_FAILED`, not `UNSUPPORTED`. `UNSUPPORTED` is a specialist
capability judgment with a closed `UnsupportedReason`.

V1 model-generated operational prose is not part of these contracts.
`ModelDecision` and typed claims have no `answer` / `candidate_answer` /
`claim_text`. `SpecialistResult.answer` will later hold deterministic
renderer output from verified claims, not copied model wording.

Claims are typed records (`EXACT_COUNT`, `LOWER_BOUND_COUNT`,
`VERIFIED_ZERO`, `HISTORICAL_WINDOW`, `RECORD_IDENTITY`, `LIMITATION`,
`UNAVAILABLE`). Each references `tool_call_id`. `metric_id` is a bounded
code string; the future historical verifier owns the allowed metric
catalog. `UNSUPPORTED` is a status/reason, not an evidence claim.

Mandatory safety limitations are deterministic. A later specialist must
independently propagate applicable `ToolResult` limitations, including
`RESULT_TRUNCATED`, `HAZARD_VERSION_WINDOW_LIMITATION`, coverage/query
unavailability, and mapping integrity failures. A model `LIMITATION` claim
cannot suppress those by omission. `PARTIAL`, `UNAVAILABLE`, and
`VERIFIED_ZERO` proof requirements remain deterministic
status/evidence semantics.

`SpecialistResult.evaluated_as_of_utc` is optional evidence-backed
evaluation time from executed `ToolResult`s. The contract constructor does
not copy trusted `as_of_utc` onto it. Unsupported or provider failure
before tools, and pre-operation `INVALID_REQUEST` results with
`ToolResult.as_of_utc is None`, leave it `None`. 3A.2 derives the value.

Future 3A.2 list policy for `list_historical_encounters` (not implemented
here; Phase 2B `LIST_DEFAULT_LIMIT=100` and `LIST_MAX_LIMIT=200` stay
unchanged): omitted limit may become an explicit specialist default of 20;
supplied `1..25` executes unchanged; supplied `>25` is rejected with zero
historical operations for that call and one bounded correction. No silent
clamping. `ToolInputField` does not encode that max.

`ModelTurnRequest` carries `user_text`, optional `instruction_ref`, and
optional 3A.1 `tools` / `tool_results`. Empty defaults omit those keys so
the 3A-preflight `{user_text}` payload remains parseable. This is not a
vendor chat transcript.

## Phase 3A.1 tool schemas and model-visible projections

Phase 3A.1 adds provider-neutral `ToolSchema` generation and
`ToolResultProjection`. It does not execute a model, dispatch historical
tools, verify claims, or render answers. No provider SDK exists.

`HISTORICAL_ANALYTICS_TOOLS.input_fields` remains the field-inclusion
allowlist. `build_tool_schemas` / `historical_analytics_tool_schemas()`
copy catalog names, descriptions, required flags, value types, and field
descriptions. Schema generation fails closed if a catalog field uses a
trusted or infrastructure name. It does not inspect unbound signatures
and does not emit JSON Schema / vendor tool objects.

`project_tool_result` derives a model-visible view from an audit-grade
`ToolResult` without mutating it. The projection keeps status, HISTORICAL
scope, evaluated `as_of_utc`, completeness, match cardinality,
limitations, error codes, requested scope, and the application `result`.
It omits `Evidence.query_executions` and other Athena/S3 diagnostics.
Application data is limited to `operation`, `requested_scope`, `result`,
and `limitations`. The model-visible `error_code` is
`Evidence.error_code` only. If `data.error.code` is also present, it must
equal that evidence code or projection fails closed. Mapping-coherence
failures may have an evidence code with no application error. Nested
`coverage` / `evidence` blobs are rejected.

List `data.result.records` and `source_records` must match in Phase 2C
order by `record_id` and event timestamp (`event_time_utc` /
`event_timestamp_utc`). Count-only agreement is not enough. More than 25
records in either representation fails closed
(`projection_record_limit_exceeded`) so the model never sees a second
independent "partial" concept. Populated `ToolResult.as_of_utc`,
`Evidence.query_timestamp_utc`, and
`Evidence.completeness.evaluated_as_of_utc` must be identical; disagreement
fails closed. 3A.2 dispatch will later keep specialist lists at or below
25. Projection does not silently trim or change SUCCESS / NOT_FOUND /
PARTIAL / UNAVAILABLE.

`import wilvor_ai` still does not load `tool_schema`,
`tool_result_projection`, historical adapters, Live Ops, or provider SDKs.

Phase 3A is not complete.

## Tests

Run the offline contract suite from the repository root, including Phase 0
contracts, Phase 1E Live Operations adapter tests, Phase 2C historical
adapter tests, and Phase 3A-preflight specialist contracts:

```powershell
python -m pytest tests/contracts -q -p no:cacheprovider
```

The examples in `tests/fixtures/ai_copilot_contract_examples.py` use synthetic
identifiers and make no claims about live aviation operations.
