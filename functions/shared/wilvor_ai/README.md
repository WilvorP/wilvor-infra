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
- temporal scope.

Zero source records are valid for a verified no-match query or an unavailable
source. Optional source versions and event timestamps remain `None` when they
are unavailable; the contract never fabricates them.

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

## Deliberately not implemented

Phase 0 does not add LangGraph, agents, LLM/provider integration, prompts,
credentials, operational tools, shared query extraction, geospatial tools,
Athena, Glue, generated SQL, decision tools, evidence-verifier execution,
visualization generation, an Agent API/runtime, API Gateway changes, an `/ai`
page, dashboard changes, DynamoDB changes, IAM, Terraform, deployment, or
production infrastructure.

## Tests

Run the offline contract suite from the repository root:

```powershell
python -m pytest tests/contracts -q -p no:cacheprovider
```

The examples in `tests/fixtures/ai_copilot_contract_examples.py` use synthetic
identifiers and make no claims about live aviation operations.
