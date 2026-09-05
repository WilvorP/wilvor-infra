# Wilvor AI Operations Copilot

The AI Copilot is a separate, read-only advisory service over Wilvor's
deterministic operational platform.

Wilvor calculates trajectory, geometry, encounters, risk, airport
assessments, recommendations, and alert state. The model only selects
approved read-only tools and explains the returned facts.

## Maturity levels

- Level 1: cached network, aircraft, airport, recommendation, and
  incident summaries.
- Level 2: bounded interactive chat through `POST /ai/chat`.
- Level 3: one controlled operator assistant with an explicit
  read-only tool registry.

The service never gives the model DynamoDB, AWS API, arbitrary HTTP,
shell, or write access. Operational facts are retrieved through fixed
paths on the existing operational API. The AI Lambda can read and
write only its own AI Insights table.

## Decision Context and evidence

`context.py` maps operational API responses into versioned, bounded
Decision Contexts for network, aircraft, airport, recommendation, and
alert subjects. Missing values remain missing or null. Deterministic
limitations and stale/unavailable source warnings are included before
inference and merged back after inference.

Each context has an `evidenceCatalog`. Model-provided evidence IDs are
filtered against this catalog; unknown IDs are discarded. Labels in
the public response come from the catalog, not from the model.

No prompts, complete contexts, aircraft payloads, or hidden model
reasoning are logged.

## Bedrock

The provider abstraction is `ModelClient`; production uses
`BedrockConverseClient` and tests use fakes.

The dev configuration uses the AWS-documented US geographic inference
profile `us.anthropic.claude-sonnet-4-6`, which is callable from
`us-west-1` and supports Converse, client-side tool use, and structured
outputs. The ID is a Terraform variable and can be changed without
code changes.

Tool specifications use `strict: true`. Python validation remains the
final authority. The structured-output schema uses
`additionalProperties: false` and avoids unsupported Bedrock schema
keywords such as string length and numeric range constraints; those
bounds are enforced in application code.

The Lambda package pins boto3/botocore through `boto3==1.43.89` so
the deployed service has SDK models for `outputConfig` and strict
tool definitions rather than depending on the managed runtime version.

## Approved tools

- `get_network_overview`
- `get_data_freshness`
- `get_system_health`
- `get_aircraft_context`
- `get_airport_context`
- `get_active_encounters`
- `get_active_recommendations`
- `get_active_alerts`
- `get_recommendation_context`
- `get_alert_context`
- `compare_diversion_airports`

`compare_diversion_airports` returns existing deterministic ranks,
scores, and assessments. It does not calculate or rerank candidates.

## HTTP API

Routes:

- `GET /health`
- `POST /ai/chat`
- `POST /ai/summaries/network`
- `POST /ai/aircraft/{aircraftId}/explain`
- `POST /ai/airports/{airportId}/summarize`
- `POST /ai/recommendations/{recommendationId}/explain`
- `POST /ai/alerts/{alertId}/incident-summary`
- `GET /ai/insights/{subjectType}/{subjectId}`

Fixed summary POST routes accept an empty JSON object.

Chat request:

```json
{
  "message": "Why is aircraft a67928 at risk?",
  "history": [
    {
      "role": "user",
      "content": "What is happening right now?"
    },
    {
      "role": "assistant",
      "content": "The prior advisory response."
    }
  ],
  "subject": {
    "type": "AIRCRAFT",
    "id": "a67928"
  }
}
```

History, per-item content, message length, body size, tool rounds,
tool input, list limits, tool payload size, timeouts, and output tokens
are bounded. Public chat requires at least one successful approved tool
before an operational answer is accepted. An invocation deadline keeps
the service inside the synchronous API Gateway budget.

Successful response:

```json
{
  "answer": "Current data indicates ... Advisory only; qualified human review is required.",
  "evidence": [
    {
      "evidenceId": "riskresult.risk-123.risk_level",
      "label": "RiskResult risk#123: risk_level"
    }
  ],
  "confidence": "LOW",
  "limitations": [
    "Fuel state unavailable."
  ],
  "dataFreshnessWarnings": [
    "OPENSKY data freshness is STALE."
  ],
  "toolCalls": [
    {
      "name": "get_aircraft_context",
      "status": "SUCCESS",
      "durationMs": 42
    }
  ],
  "advisoryOnly": true,
  "humanReviewRequired": true,
  "generatedAt": "2026-09-05T18:00:00Z",
  "modelId": "us.anthropic.claude-sonnet-4-6",
  "promptVersion": "wilvor-ai-v1",
  "cache": {
    "hit": false
  }
}
```

Expected errors are sanitized and include `requestId`: 400 malformed
input, 404 missing subject, 422 insufficient deterministic context,
429 Bedrock throttling, 502 model failure, 503 operational API failure,
and 500 unexpected failure.

## Cache and audit table

The pay-per-request `AI Insights` table uses:

- partition key: `subject_key`, for example `AIRCRAFT#a67928`
- sort key: `<INSIGHT_TYPE>#<generated-at>#<insight-id>`
- TTL: `expires_at_epoch`

Records include subject/insight identity, context fingerprint, model,
prompt version, full public output, validated evidence references,
limitations, freshness warnings, token usage, latency, generation
time, cache validity, and TTL.

Fixed Level 1 workflows use a SHA-256 fingerprint over material
context, insight type, model ID, and prompt version. Context generation
timestamps, evidence-catalog duplication, and continuously changing
freshness age counters are excluded. Arbitrary chat is not cached.

## Proactive generation

All proactive generation is disabled by default in dev.

When enabled:

- `wilvor.risk` / `risk.updated` generates an aircraft explanation.
- `wilvor.recommendation` / `recommendation.updated` generates a
  recommendation explanation.
- `wilvor.alert` / `alert.updated|alert.resolved` generates an incident
  summary.
- `wilvor.airport` / `airport.status.updated` generates an airport
  summary.
- an EventBridge `rate(5 minutes)` schedule generates the network
  summary.

The service does not run on every aircraft observation.
EventBridge delivery uses a one-hour event-age limit, two retries, and
an encrypted SQS dead-letter queue with CloudWatch visibility alarms.
Lambda asynchronous handler failures use the same queue with one
function-level retry.

## Configuration

Lambda environment values are supplied by Terraform:

- `OPERATIONAL_API_BASE_URL`
- `AI_INSIGHTS_TABLE_NAME`
- `BEDROCK_MODEL_ID`
- `AI_MAX_OUTPUT_TOKENS`
- `AI_TEMPERATURE`
- `AI_MAX_TOOL_ROUNDS`
- `PROMPT_VERSION`
- `AI_MAX_MESSAGE_CHARS`
- `AI_MAX_HISTORY_ITEMS`
- `AI_CACHE_TTL_SECONDS`
- `AI_INSIGHT_RETENTION_SECONDS`
- operational API and Bedrock timeouts

Dev controls are in `envs/dev/terraform.tfvars`:

- `ai_bedrock_model_id`
- `ai_bedrock_foundation_model_id`
- `enable_ai_event_triggers`
- `enable_ai_network_summary_schedule`

Cost controls include API throttling, reserved Lambda concurrency,
bounded tokens/tool rounds/payloads, downstream timeouts, fixed
read-only tools, and the context-fingerprint cache.

## Authentication status

The current Wilvor APIs have no established authorizer. This module
preserves the `Authorization` CORS header but does not invent an
authentication system. The deployed endpoint is therefore
unauthenticated and does not satisfy a future Cognito/JWT authorization
requirement. API Gateway routing is isolated so a JWT authorizer can be
added without changing Decision Context or agent code.

## Build and test

```powershell
python -m pip install -r tests/requirements-test.txt
python -m pytest tests/unit/ai_copilot tests/unit/operational_api -q
python -m compileall -f functions/ai_copilot functions/operational_api
.\scripts\build_operational_api.ps1
.\scripts\build_ai_copilot.ps1
terraform -chdir=envs/dev fmt -recursive
terraform -chdir=envs/dev validate
```

Unit tests use no AWS credentials and make no real Bedrock or HTTP
calls.

## Manual deployment and smoke test

After reviewing `terraform plan`, an authorized operator may deploy the
dev stack using the repository's normal manual procedure. Confirm
Bedrock Marketplace/model access and cross-region inference permissions
for the configured profile first.

After that explicit deployment:

```powershell
$endpoint = terraform -chdir=envs/dev output -raw ai_copilot_api_endpoint
.\scripts\smoke-ai-copilot.ps1 `
  -ApiEndpoint $endpoint `
  -AircraftId "a67928"
```

Do not run the smoke helper before deployment. Its summary/chat calls
incur Bedrock cost.

## Known limitations

- Output quality cannot be verified without Bedrock model access and
  representative deployed operational data.
- The first request for a new Bedrock output schema can incur provider
  schema-compilation latency. The synchronous API enforces a ten-second
  Bedrock read timeout; an authorized operator may need to retry the
  initial smoke request after compilation completes.
- Fuel, filed route, ATC clearance, aircraft performance, airline
  policy, active runway, aircraft-specific runway requirements, and
  congestion are not available in current operational contracts.
- Encounter altitude overlap is currently deterministic `UNKNOWN`.
- The current encounter record has no direct minutes-to-intersection
  field.
- `encounter.resolved` is consumed in existing pipelines but is not
  emitted by the current encounter processor.
