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

The Historical Analytics Specialist constructor binds dependencies:

```text
HistoricalAnalyticsSpecialist(
    provider=ModelProvider,
    operations=HistoricalAnalyticsOperations,
)
```

then:

```text
run(request: SpecialistRequest, context: HistoricalSpecialistTrustedContext)
    -> HistoricalSpecialistRunResult
```

`run()` does not accept provider, operations, or AWS clients. A later
Master Agent does not need to know how Athena, coverage, S3, Glue, or
`HistoricalAnalyticsOperations` are constructed. 3A.3 will convert the
run result into a verified `SpecialistResult`.

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
`ToolResult.as_of_utc is None`, leave it `None`. 3A.2 derives the value
on `HistoricalSpecialistRunResult`.

3A.2 list policy for `list_historical_encounters` (Phase 2B
`LIST_DEFAULT_LIMIT=100` and `LIST_MAX_LIMIT=200` stay unchanged): omitted
limit becomes an explicit specialist default of 20; supplied `1..25`
executes unchanged; supplied `<1` or `>25` is rejected with zero
historical operations for that call. No silent clamping.
`ToolInputField` does not encode that max.

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
`tool_result_projection`, historical adapters, specialist runtime, Live
Ops, or provider SDKs.

## Phase 3A.2 bounded specialist orchestration

Phase 3A.2 adds offline `HistoricalAnalyticsSpecialist` orchestration
around an injected `ModelProvider` and an already-built
`HistoricalAnalyticsOperations` object. Trusted invocation context is
`as_of_utc` only. The specialist does not create AWS clients, Coverage
stores, Athena executors, or provider SDKs.

Each executed historical call builds a fresh `HistoricalAnalyticsCall`
with the trusted `as_of_utc` and a runtime-generated `tool_call_id`, then
dispatches only through `HistoricalAnalyticsAdapter.get_handler`. Full
audit `ToolResult` values are retained; the provider sees only
`project_tool_result` views plus optional dispatcher `validation_feedback`.
The specialist now requests `wilvor.historical.specialist.v2`. The recorded
live v1 instruction remains registered and byte-for-byte unchanged.

Loop bounds: at most 3 model turns, 2 historical tool executions, one
structurally invalid TOOL_CALLS correction, and one execution per
canonical `(name, normalized arguments)` key. TOOL_CALLS batches are
validated atomically; one invalid or over-budget call executes none.
Omitted `list_historical_encounters.limit` becomes explicit 20 before
canonicalization, so it matches an explicit 20. Domain adapter
`INVALID_REQUEST` is a real ToolResult and does not consume the
dispatcher correction budget.

`FINAL_CLAIMS` stop the loop and are stored as unverified
`proposed_claims`. They are not `verified_claims` and do not produce a
factual answer. 3A.3 owns verification and rendering. `UNSUPPORTED`
retains prior ToolResults. `REFUSAL`, provider exceptions, malformed
decisions, and an exhausted model-turn budget are `PROVIDER_FAILED`.
Projection or evaluated `as_of` integrity failures are `UNAVAILABLE` and
do not ask the model to correct them.

Tests use a scripted fake provider only. No real LLM is called.

## Phase 3A.3 deterministic verification and rendering

Phase 3A.3 converts `HistoricalSpecialistRunResult` into `SpecialistResult`
through `verify_historical_specialist_run` and
`finalize_historical_specialist_run`. The model only proposes typed claims.
Full `ToolResult` / first-class `Evidence` is verifier authority;
`ToolResultProjection` is not. All proposed claims must verify atomically.
Any failure yields `VerifierOutcome.FAILED`, empty `verified_claims`, and
`SpecialistStatus.UNAVAILABLE` with `CLAIM_VERIFICATION_FAILED`. There is
no verification retry and no provider call.

Exact counts use a code-owned metric allowlist from actual Phase 2B result
fields. Fact-bearing exact-count, lower-bound, and record-identity claims
require `TemporalScope.HISTORICAL` and `Evidence.completeness.status ==
EVALUABLE`. Exact counts also require `SUCCESS`; lower bounds also
require `PARTIAL`; record identity is list-only and accepts
`SUCCESS` / `PARTIAL`. Verified zero keeps its stronger `NOT_FOUND` plus
evaluable exact-zero proof. Windows match requested scope only and do
not require `EVALUABLE`. `UNAVAILABLE` claims require
`ToolResultStatus.UNAVAILABLE`; domain `UNKNOWN` / `INVALID_REQUEST` is
not treated as coverage failure. Malformed `ToolResult`s are not
repaired. Limitation claims cannot suppress mandatory
`RESULT_TRUNCATED` or `HAZARD_VERSION_WINDOW_LIMITATION`. Window-only or
limitation-only claim sets are non-actionable.

`SpecialistResult.answer` is code-rendered. Model-authored operational
prose remains prohibited. `evaluated_as_of_utc` is copied from the run
result only. Used factual `ToolResult.as_of_utc` values must agree with
each other and with `run_result.evaluated_as_of_utc`; the finalizer does
not invent as-of, read trusted context, or read wall clock. All audit
`ToolResult`s are retained. Used-tool claims determine factual status; an
unused earlier domain `INVALID_REQUEST` does not override a later
verified success. Global 3A.2 `UNAVAILABLE` remains fatal. The two-stage
API is retained: `specialist.run(...)` then
`finalize_historical_specialist_run(...)`.

## Phase 3A.4 offline Bedrock Converse adapter

Phase 3A.4 adds an offline Amazon Bedrock Runtime Converse adapter behind
the existing `ModelProvider` protocol. It does not call Bedrock, create AWS
clients, or change 3A.2/3A.3 safety semantics.

Provider lock:

- Amazon Bedrock Runtime Converse
- Claude Sonnet 4.6
- initial allowlisted US geo inference profile: `us.anthropic.claude-sonnet-4-6`

`BedrockConverseModelProvider` accepts an injected `BedrockConverseClient`
with `converse(**kwargs) -> Mapping`. It does not import `boto3`/`botocore`
and does not construct `boto3.client("bedrock-runtime")`. Real client
composition belongs to 3A.5 or later Agent API/runtime composition.

Each `complete(ModelTurnRequest)` reconstructs one Converse request from the
provider-neutral snapshot: the request's versioned instruction ref
(`wilvor.historical.specialist.v1` remains registered; the specialist now
sends `v2`), original `user_text`, the four catalog `ToolSchema`s, cumulative
`ToolResultProjection` JSON, and optional `ValidationFeedback`. There is no
mutable vendor transcript and no fabricated native `toolUse`/`toolResult`
continuation. Bedrock `toolUseId` is required on each `toolUse` block, then
discarded after the current parse. It is not a Wilvor evidence ID.

The same request uses strict tool specs (`toolSpec.strict = true`) and
`outputConfig.textFormat` JSON schema together. Terminal structured output
permits only `FINAL_CLAIMS`, `UNSUPPORTED`, and `REFUSAL`. `TOOL_CALLS` come
from native `toolUse` blocks. `end_turn` requires exactly one model
`{"text": "<JSON string>"}` ContentBlock; a top-level `{"json": ...}`
block is rejected. `stopReason` is the primary discriminator:
incidental text beside `toolUse` is ignored; a contradictory terminal
decision beside `toolUse` fails closed; `content_filtered` /
`guardrail_intervened` map to `ModelDecision.REFUSAL`; incomplete or
malformed stop reasons raise provider-owned errors, never `UNSUPPORTED`.

`ModelDecision.from_dict` and existing claim constructors remain the
provider-neutral parse boundary. The deterministic verifier and renderer
remain factual authority. Prompt text is not the security boundary.

`import wilvor_ai` and `import wilvor_ai.providers` stay adapter-SDK-free.
The concrete adapter is imported explicitly as
`wilvor_ai.providers.bedrock_converse`. No LangGraph, Terraform, or IAM
changes are included.

Bedrock live proof is not required for direct-provider Phase 3A
closure. The earlier Bedrock 3A.5 attempt remains stashed because the
AWS account was not authorized for Anthropic through Bedrock.

## Phase 3A.4B offline Anthropic Messages adapter

Phase 3A.4B adds a second offline provider adapter behind the same
`ModelProvider` protocol: Anthropic's direct Messages API. The committed
Bedrock Converse adapter remains in the repository and is unchanged.
3A.4B does not call Anthropic, read `ANTHROPIC_API_KEY`, add the
`anthropic` SDK, or change 3A.2/3A.3 safety semantics.

Provider lock:

- Anthropic Messages API
- Claude Sonnet 4.6
- closed allowlist: `claude-sonnet-4-6`

`AnthropicMessagesModelProvider` accepts an injected
`AnthropicMessagesClient` with `messages_create(**kwargs) -> Mapping`.
It does not import `anthropic` and does not construct an SDK client.
Real client composition and API-key loading belong to 3A.5 live
composition.

Each `complete(ModelTurnRequest)` reconstructs one Messages request from
the provider-neutral snapshot: the request's versioned instruction ref
(`wilvor.historical.specialist.v1` remains registered; the specialist now
sends `v2`), original `user_text`, the four catalog `ToolSchema`s,
cumulative `ToolResultProjection` JSON, and optional `ValidationFeedback`.
There is no mutable vendor transcript and no native `tool_use` /
`tool_result` continuation. Anthropic `tool_use.id` is required on each
`tool_use` block, then discarded after the current parse. It is not a
Wilvor evidence ID.

The same request uses strict client tools (`strict: true`) and
`output_config.format` JSON schema together. Terminal structured output
permits only `FINAL_CLAIMS`, `UNSUPPORTED`, and `REFUSAL`. `TOOL_CALLS`
come from native `tool_use` blocks. `end_turn` requires exactly one
`{"type": "text", "text": "<JSON string>"}` content block. `stop_reason`
is the primary discriminator: incidental text beside `tool_use` is
ignored; a contradictory terminal decision beside `tool_use` fails
closed; `refusal` maps to `ModelDecision.REFUSAL`; `pause_turn`,
`max_tokens`, `stop_sequence`, and unexpected reasons raise
provider-owned errors, never `UNSUPPORTED`.

Response envelopes must be `type=message`, `role=assistant`, and
`model` equal to the configured allowlisted ID. Thinking, server-tool,
and other unenabled content block types fail closed.

`ModelDecision.from_dict` and existing claim constructors remain the
provider-neutral parse boundary. The deterministic verifier and renderer
remain factual authority. Prompt text is not the security boundary.

`import wilvor_ai` and `import wilvor_ai.providers` stay adapter-SDK-free.
The concrete adapter is imported explicitly as
`wilvor_ai.providers.anthropic_messages`. No LangGraph, Terraform, or IAM
changes are included.

## Phase 3A.5 direct Anthropic live validation harness

Phase 3A.5 adds an operator-only live harness:

- `scripts/validate_historical_specialist_anthropic_live.py`
- live-only dependency pin `anthropic==1.8.0` in
  `tests/requirements-live-validation.txt`

The committed `AnthropicMessagesModelProvider` remains SDK-free. The
official SDK is imported only after `--action run-tier1` or
`--action run-targeted` and `WILVOR_RUN_LIVE_ANTHROPIC=1`. Dry-run does
not read `ANTHROPIC_API_KEY`. `run-tier1` keeps the original 17-scenario
matrix. `run-targeted` runs the approved 11-scenario v2 retest and may
set `phase_3a_completion_candidate` only; it never sets
`phase_3a_complete`. Family A may count an approved duplicate-tool
`SAFE_VARIATION` when Wilvor blocks the identical completed call and the
model then emits verified correct `FINAL_CLAIMS`. That is the original
3A.5 hard-gate policy, not a general SAFE_VARIATION waiver.

Live composition constructs:

```python
Anthropic(api_key=..., max_retries=0, timeout=240.0)
```

and adapts `message.to_dict(mode="json")` into the committed
`AnthropicMessagesClient` Mapping. The committed provider still emits
`temperature=0`. Anthropic Python SDK 1.8.0 rejects that as a top-level
`messages.create` keyword, so the live wrapper moves only `temperature`
into `extra_body={"temperature": 0}` to preserve HTTP body semantics.
Strict tools, `output_config`, and other provider fields are not
rewritten. The harness does not repair a later API wire rejection.

Tier 1 uses canned production `HistoricalQueryResponse` operations. AWS
and Athena are not used. The unique hard gate is flattened snapshot
continuation: a brand-new Messages request with cumulative
`ToolResultProjection` JSON and no native `tool_result` history. Exact
count must succeed on at least 2 of 3 independent live runs.

The hard mechanical bound is 40 live provider call attempts.
`current_api_calls` counts those attempts, not proven HTTP successes.
SDK retries are disabled.

The first human-operated direct-Anthropic Tier-1 run completed. Exact-count
flattened-snapshot continuation passed 3/3 (`A1`, `A2`, `A3`). Deterministic
factual safety held: no failed scenario produced an incorrect factual
answer. `wilvor.historical.specialist.v1` is retained for reproducibility of
that recorded run. `wilvor.historical.specialist.v2` adds evidence-
interpretation clarifications only (certified zero, PARTIAL finalize,
no identical completed-tool retry, hazard materialization vs validity
overlap, UNKNOWN vs UNAVAILABLE, OUT_OF_CATALOG vs model-generated
REFUSAL). Family H no longer treats a domain `UNKNOWN` /
`INVALID_REQUEST` path as a runtime failure when trusted fields never
reached execution.

Known non-blocking follow-up (`TOOL_SCHEMA_GAP`): the model-facing
`list_historical_encounters` schema permits start/end without
`aircraft_id` or `hazard_id`, while `ListHistoricalEncountersRequest`
requires at least one identifier. The deterministic adapter still fails
closed. Do not add generic oneOf/anyOf schema machinery for that gap.

Targeted live retest after the v2 remediation, not a full 17-scenario rerun:

- A exact-count regression: 1
- B verified zero: 3
- C truncated list: 3
- K hazard limitation: 2
- H trusted attack: 1
- J out-of-catalog: 1

Harness machine reports keep `phase_3a_complete = false`. That field is
not project-closure authority. Human review of the live evidence and this
documentation is the Phase 3A closure authority.

## Phase 3A complete

Phase 3A Historical Analytics Specialist foundation is complete.

`wilvor.historical.specialist.v1` remains frozen for the first
direct-Anthropic baseline. `wilvor.historical.specialist.v2` is the active
specialist instruction. It clarifies certified `VERIFIED_ZERO`, PARTIAL /
`LOWER_BOUND_COUNT`, completed-tool reuse, hazard materialization
semantics, UNKNOWN vs UNAVAILABLE, and OUT_OF_CATALOG vs REFUSAL. There is
no v3.

Proven live against `claude-sonnet-4-6` on the direct Anthropic Messages
API:

- strict four-tool historical catalog
- structured terminal decisions
- flattened snapshot continuation (no native Anthropic `tool_result` history)
- provider tool IDs are non-authoritative
- Wilvor-minted `tool_call_id` authority
- trusted `as_of_utc` authority
- deterministic `ToolResultProjection` continuation
- exact counts
- `VERIFIED_ZERO`
- PARTIAL / `LOWER_BOUND_COUNT`
- `RESULT_TRUNCATED` preservation
- hazard materialization limitation
- trusted-argument attack resistance
- out-of-catalog rejection
- deterministic claim verification
- deterministic factual rendering
- fail-closed behavior on invalid model claims

The first live Tier-1 run passed the exact-count snapshot hard gate 3/3.
The targeted v2 retest met the predeclared bar (A 1/1 including approved
duplicate-tool `SAFE_VARIATION`, B 3/3, C 2/3, K 2/2, H PASS, J preferred
`UNSUPPORTED` / `OUT_OF_CATALOG`). Stored live JSON artifacts are
historical evidence and are not rewritten when harness accounting later
changes.

Model quality is not the factual safety boundary. A model may choose a
wrong claim type, attempt a duplicate call, or take an unnecessary extra
tool turn. Unsupported factual claims are not rendered unless
deterministic evidence verification passes.

Non-blocking follow-up (do not reopen Phase 3A):

- `TOOL_SCHEMA_GAP`: `list_historical_encounters` model-facing schema
  does not encode aircraft_id-or-hazard_id. Runtime fails closed.
- Occasional extra list call after usable PARTIAL evidence (C-R3) is a
  quality/efficiency issue, not a factual-safety failure.
- Structured model-generated REFUSAL remains in the terminal schema.
  Live J-R1 used preferred `UNSUPPORTED` / `OUT_OF_CATALOG`.
- Bedrock Converse is implemented and tested offline. Live Bedrock was
  blocked by account model authorization. Direct Anthropic is the proven
  live provider.

Phase 3A completion does not complete the AI Operations Copilot. Still
not implemented: Decision Tool runtime, Live Ops Expert, Decision Expert,
Master Agent / LangGraph orchestration, multi-specialist evidence
verification, visualization builder, dedicated Agent API, `/ai`
frontend integration, broader evaluation, and latency/cost
optimization. Historical Analytics Specialist is one specialist
foundation. DT1 defines the decision evidence contract. DT2 adds the
read-only current decision context operation and does not add a model catalog.

## Decision Tools

DT0, the authoritative decision-logic audit, is complete. DT1 adds the
current decision evidence contract in `wilvor_ai.decision_contracts`.
DT2 adds one read-only operation, `get_current_decision_context`, in
`wilvor_ai.decision_context`. It maps
`build_aircraft_operational_context` into `DecisionEvidence` for one
`aircraft_id`. Tables, `now_epoch`, and `tool_call_id` stay on
`DecisionContextCall`. There is no decision-tool catalog, no model
schema, and no Decision Expert.

Decision evidence uses the existing `ToolResult` envelope with
`TemporalScope.CURRENT`. The payload is `DecisionEvidence`
(`wilvor.ai.decision_evidence.v1`). Historical and hybrid decision
reconstruction are out of scope. A future Master Agent may combine a
current decision result with historical analytics evidence, but the
decision payload itself stays current.

`get_current_decision_context` is the broad current-context operation.
It includes `DecisionRouteCapability`: `validated_alternative_available`
is false, and the unavailable set is route alternatives, route safety,
runway evidence, and congestion. It does not calculate risk, choose a
recommendation, or read airport assessments.

`get_current_risk_evidence` and `get_current_recommendation` are narrow
views of that same mapped chain. They reuse `DecisionContextCall` and
do not add a tool catalog or a model schema. Both omit route capability.
`get_current_recommendation` returns the current set, including zero or
many recommendations, and does not select a winner. One liftable
encounter stays on the top-level risk or recommendation fields. More
than one current encounter stays on the encounter tuple, with top-level
risk and recommendations empty, and no encounter is selected.

`get_persisted_airport_candidate_evidence` reads airport-assessment rows
already stored for the evaluation id on one recommendation. It is not a
current airport selector, a route planner, or a diversion planner. It does
not rescore or rerank. Route safety, runway evidence, and congestion stay
unavailable. The persisted evidence is not asserted to be current.

`DecisionEvidence.capability` is optional. When it is present, it is
authoritative: `validated_alternative_available` is false, and the
unavailable set is route alternatives, route safety, runway evidence,
and congestion. No validated route-generation capability exists.
Narrow stored risk evidence, and recommendation evidence that is not
answering a route question, does not need those unrelated limitations.
`EVALUATE_DIVERSION` is advisory evidence to evaluate persisted airport
options. It is not a computed route or a diversion clearance.

Currentness stays in `wilvor_operational`. This contract reports those
results. It does not define a second meaning of current. Missing risk
stays missing and is distinct from a stored LOW risk. A missing
recommendation stays `ABSENT_FROM_CURRENT_CANDIDATES` and does not
become MONITOR. Multiple current recommendations are a set with no
selected winner. Alert OR-lineage is reported and is not resolved.

## Tests

Run the offline contract suite from the repository root, including Phase 0
contracts, Phase 1E Live Operations adapter tests, Phase 2C historical
adapter tests, and Phase 3A-preflight specialist contracts:

```powershell
python -m pytest tests/contracts -q -p no:cacheprovider
```

The examples in `tests/fixtures/ai_copilot_contract_examples.py` use synthetic
identifiers and make no claims about live aviation operations.

Six Live Operations region tests in this suite can fail for an unrelated
checksum: committed `us_states.geojson` raw bytes contain one CRLF, while
`us_states.meta.json` hashes the LF bytes. That is not a decision-contract
failure. Do not change those files to make this suite green.
