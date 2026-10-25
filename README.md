# Wilvor

**Canonical page:** this page for repository entry. Architecture detail: [`docs/overview/architecture.md`](docs/overview/architecture.md)
**Verified against:** `8abd2d5` on `docs/ws1-platform-foundations`
**Maturity:** CODE, TF, UNIT, CONTRACT, INFRA, INT, LIVE
**Production authorization:** no grant found in this repository. `MasterRouteExecutionAuthority` is `DENIED` only (source: `functions/shared/wilvor_ai/master_routing.py`, `MasterRouteExecutionAuthority`).

Wilvor is an advisory aviation decision-support platform. It ingests aircraft and weather observations, derives deterministic operational state, exposes that state through a read-only HTTP API, and presents it in a React console. It is not air traffic control and not a flight-control system (source: `dashboard/README.md`).

This repository, `wilvor-infra`, holds the AWS Terraform, Lambda source, shared Python packages, dashboard, tests, and development lifecycle scripts for the development environment.

## Purpose

This page orients a reader to what Wilvor is, how the repository is laid out, and where to go next. It does not replace module READMEs or invent a production environment.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `functions/`, `dashboard/`, `functions/shared/` |
| Terraform | TF | `envs/dev/main.tf`, `envs/dev-historical-data/main.tf` |
| Unit tests | UNIT | `tests/unit/` |
| Contract tests | CONTRACT | `tests/contracts/` |
| Infrastructure tests | INFRA | `tests/infrastructure/` |
| Integration tests | INT | `tests/integration/{aircraft,sigmet,metar,taf}/` |
| Live harness | LIVE | `scripts/validate_*.py`, `scripts/validate_historical_analytics.ps1` |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | `DENIED` / no grant | `functions/shared/wilvor_ai/master_routing.py` (`MasterRouteExecutionAuthority`); `functions/shared/wilvor_ai/contracts.py` (`V1_AUTHORITY`) |

Current pytest, Vitest, and operator-harness pass counts are UNKNOWN. This page does not record a suite result.

## What Wilvor addresses

Wilvor keeps a current operational picture of aircraft, weather hazards, projected trajectories, encounters, risk, airport assessments, recommendations, and alerts. Deterministic services remain authoritative for those calculations (source: `functions/shared/wilvor_ai/README.md`). The Live Operations Console is a presentation and investigation layer only (source: `dashboard/README.md`).

Current-set membership for API reads is defined in `functions/shared/wilvor_operational`. That package is the canonical specification for runtime current/active semantics; this page does not copy it. See [`functions/shared/wilvor_operational/README.md`](functions/shared/wilvor_operational/README.md).

## High-level architecture

Three planes exist in this repository. They are not interchangeable.

| Plane | What exists at `8abd2d5` | Maturity |
| --- | --- | --- |
| Operational | Ingestion, EventBridge processors, DynamoDB current-state tables, HTTP Operational API, React console | CODE, TF. UNIT/INT exist for subsets. DEPLOYED UNKNOWN |
| Historical | Persistent S3 data-plane root, Firehose transport, Glue/Athena analytics configuration, query library | CODE, TF. UNIT/INFRA exist. Whether the bucket exists in an account is UNKNOWN |
| AI | Python package `wilvor_ai`: contracts, specialists, offline providers, Master planning types | CODE, CONTRACT, LIVE harnesses. TF absent (`*.tf` search for `bedrock`, `anthropic`, `wilvor_ai`) |

Architecture diagrams and data-flow evidence: [`docs/overview/architecture.md`](docs/overview/architecture.md).

## Major engineering components

- **Ingestion.** OpenSky aircraft poller; NOAA Aviation Weather SIGMET, METAR, and TAF pollers; station-reference and runway-metadata loaders. Timed poller schedules are set `false` in `envs/dev/main.tf`. METAR and TAF also subscribe to `hazard.stations.ready` (source: `modules/metar/poller.tf`, `modules/taf/poller.tf`). Documentation: [`docs/ingestion/README.md`](docs/ingestion/README.md).
- **Processing.** Projection, encounter, risk, airport status, airport assessment, recommendations, and active-alert Lambdas on the default EventBridge bus. Event triggers are set `true` in `envs/dev/main.tf`. Documentation: [`docs/processing/README.md`](docs/processing/README.md).
- **Operational state.** DynamoDB tables owned by the Terraform modules under `modules/`. Table semantics: [`docs/data/dynamodb.md`](docs/data/dynamodb.md).
- **Operational API.** API Gateway HTTP API, GET/OPTIONS only, Lambda `app.lambda_handler` (source: `modules/operational_api/api.tf`; `functions/operational_api/app.py`). Route detail: [`docs/api/operational-api.md`](docs/api/operational-api.md).
- **Dashboard.** React Live Operations Console in `dashboard/`. Canonical console documentation: [`dashboard/README.md`](dashboard/README.md). Pointer: [`docs/dashboard/README.md`](docs/dashboard/README.md).
- **Historical facts and analytics.** Separate data-plane root `envs/dev-historical-data`; transport and analytics modules in `envs/dev`. Documentation: [`docs/historical/README.md`](docs/historical/README.md). Canonical module pages: [`modules/historical_facts/README.md`](modules/historical_facts/README.md), [`modules/historical_facts_data/README.md`](modules/historical_facts_data/README.md), [`modules/historical_analytics/README.md`](modules/historical_analytics/README.md).
- **AI Operations Copilot.** Library code under `functions/shared/wilvor_ai`. Canonical package page: [`functions/shared/wilvor_ai/README.md`](functions/shared/wilvor_ai/README.md). Documentation: [`docs/ai/README.md`](docs/ai/README.md).

## Operational, historical, and AI infrastructure

**Operational infrastructure** is the recreatable `envs/dev` stack: Kinesis, Lambda, DynamoDB, EventBridge, API Gateway, and the React client that reads the API. `scripts/dev-up.ps1` / `dev-down.ps1` target this root by default.

**Historical infrastructure** splits persistence from transport. `envs/dev-historical-data` creates the persistent facts bucket (`force_destroy = false`). `modules/historical_facts` in `envs/dev` looks that bucket up and configures Firehose; it does not create the bucket (source: `modules/historical_facts/README.md`). `scripts/dev-down.ps1` refuses `envs/dev-historical-data` (source: `scripts/dev-down.ps1`). `enable_historical_facts = true` is assigned in `envs/dev/main.tf` at `8abd2d5` (module `historical_facts` and module `operational_api`). That assignment is TF. It is not DEPLOYED.

**AI infrastructure** is source and tests. No `aws_*` AI runtime, Bedrock, or Anthropic resource was found in `*.tf` at `8abd2d5`. The Operational API does not import `wilvor_ai`. Dashboard routes in `dashboard/src/routes/routeDefinitions.ts` do not include `/ai`. `MasterRouteExecutionAuthority` contains `DENIED` only; its docstring states that production execution is not representable (source: `functions/shared/wilvor_ai/master_routing.py`, `MasterRouteExecutionAuthority`).

## Repository structure

```text
wilvor-infra/
├── dashboard/          React Live Operations Console
├── docs/               Documentation tree and standards
├── envs/               Terraform roots: dev, dev-historical-data
├── functions/          Lambda handlers and shared Python packages
├── modules/            Reusable Terraform modules
├── scripts/            Lifecycle, build, test, and operator harnesses
├── tests/              Unit, contract, infrastructure, integration, placeholders
└── tools/              Local utilities
```

Directory roles: [`docs/overview/repository-structure.md`](docs/overview/repository-structure.md).

## Technology stack

Verified from source at `8abd2d5`:

| Layer | Evidence |
| --- | --- |
| Python 3.12 Lambda runtime | `runtime = "python3.12"` in module `.tf` files (example: `modules/operational_api/api.tf`, `aws_lambda_function.api`) |
| Terraform `>= 1.6.0`, AWS provider `~> 5.0` | `envs/dev/providers.tf` |
| AWS region `us-west-1`, profile `wilvor-dev`, environment `dev` | `envs/dev/terraform.tfvars` |
| React 19, Vite 6, Vitest 3, MapLibre, TanStack Query, react-router-dom 7 | `dashboard/package.json` |
| Node 18, 20, or 22+ | `dashboard/package.json` `engines` |
| pytest | `tests/requirements-test.txt` |
| PowerShell lifecycle | `scripts/dev-up.ps1`, `scripts/dev-down.ps1`, `scripts/dev-reset.ps1` |

AWS services configured in Terraform include Lambda, DynamoDB, Kinesis, S3, EventBridge, API Gateway HTTP API, Firehose, SQS, Glue, Athena, CloudWatch, and Secrets Manager. That list is TF configuration, not an account inventory.

## Developer entry points

| Goal | Start here |
| --- | --- |
| Documentation map | [`docs/README.md`](docs/README.md) |
| Local toolchain and venv | [`docs/operations/onboarding.md`](docs/operations/onboarding.md) |
| Dev stack lifecycle | [`docs/operations/lifecycle.md`](docs/operations/lifecycle.md), [`scripts/README.md`](scripts/README.md) |
| Dashboard | [`dashboard/README.md`](dashboard/README.md) |
| Tests | [`docs/testing/overview.md`](docs/testing/overview.md), [`tests/README.md`](tests/README.md) |
| Documentation standards | [`docs/DOCUMENTATION_STANDARDS.md`](docs/DOCUMENTATION_STANDARDS.md) |
| Advisory boundary | [`docs/overview/advisory-boundaries.md`](docs/overview/advisory-boundaries.md) |

Do not treat this README as a substitute for `scripts/dev-up.ps1`. Lifecycle scripts mutate AWS when run; documentation work does not run them.

## Testing and documentation

Test layers in this repository: unit, contract, infrastructure (Terraform source and read-only AWS), integration against a deployed `dev` environment, operator live harnesses, and empty `tests/smoke/live/` and `tests/replay/` placeholders. Overview: [`docs/testing/overview.md`](docs/testing/overview.md).

This page does not claim that any suite currently passes.

## Implementation maturity and advisory boundaries

Code, Terraform configuration, offline tests, live harnesses, deployment, and production authorization are different facts. Labels and the legend live in [`docs/DOCUMENTATION_STANDARDS.md`](docs/DOCUMENTATION_STANDARDS.md). This README does not redefine them.

- TF does not establish DEPLOYED.
- A harness file establishes LIVE, not a current pass.
- `AgentAuthorityMode` contains `READ_ONLY_ADVISORY` only (source: `functions/shared/wilvor_ai/contracts.py`, `AgentAuthorityMode`).
- Qualified human review is required for safety-sensitive operational guidance (source: `functions/shared/wilvor_ai/README.md`).

## Known limitations

- Only two Terraform roots exist: `envs/dev` and `envs/dev-historical-data`. No `envs/staging` or `envs/prod` `main.tf` was found.
- Timed ingestion schedules are disabled in `envs/dev/main.tf`.
- The Operational API module defines no authorizer resource (search: `modules/operational_api` `*.tf` for `authorizer`, `jwt`, `cognito`).
- The React dashboard is not a Terraform module. CloudWatch dashboards in Terraform are pipeline health dashboards, not the console.
- `tests/smoke/live/` and `tests/replay/` contain only `.gitkeep`.
- `tests/integration/combined/` contains only `.gitkeep`.
- Whether `envs/dev` or `envs/dev-historical-data` is applied in an AWS account is UNKNOWN.
- The AI Master path is not production-executable while `MasterRouteExecutionAuthority` is `DENIED` only.

## Historical record

Before this documentation program the root README contained only the heading `# wilvor-infra`.

## Sources

- Source: `envs/dev/main.tf` (module blocks, `enable_historical_facts`, schedule and trigger flags)
- Source: `envs/dev-historical-data/main.tf` (`module.historical_facts_data`)
- Source: `envs/dev/terraform.tfvars`
- Source: `modules/operational_api/api.tf` (`aws_apigatewayv2_api.http`, `local.route_keys`, `aws_lambda_function.api`)
- Source: `functions/operational_api/app.py` (`lambda_handler`)
- Source: `dashboard/src/routes/routeDefinitions.ts` (`ROUTES`)
- Source: `functions/shared/wilvor_ai/master_routing.py` (`MasterRouteExecutionAuthority`)
- Source: `functions/shared/wilvor_ai/contracts.py` (`AgentAuthorityMode`, `V1_AUTHORITY`)
- Source: `scripts/dev-down.ps1`
- Tests: `tests/infrastructure/terraform/test_historical_facts_contract.py` (`test_dev_historical_facts_collection_is_enabled`)
- Tests: `tests/contracts/test_master_routing.py` (`test_live_ops_only_is_bounded_and_not_executable_authority`)
