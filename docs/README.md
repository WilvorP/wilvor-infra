# Documentation

This page is an index. It states no behavior. Canonical pages own the claims.

**Verified against:** `8abd2d5` on `docs/ws1-platform-foundations`

## How to use this tree

1. Read [`DOCUMENTATION_STANDARDS.md`](DOCUMENTATION_STANDARDS.md) before editing documentation. Maturity labels are defined only there.
2. Start at the [repository README](../README.md) for a platform summary.
3. Follow the topic pages below. Link to a canonical page; do not copy it.

During documentation work, leave Python, TypeScript, Terraform, tests, operational scripts, and infrastructure unchanged.

## Platform (WS1)

| Page | Topic |
| --- | --- |
| [Architecture](overview/architecture.md) | Operational, historical, and AI planes; data flow |
| [Repository structure](overview/repository-structure.md) | Directory map and technology stack |
| [Advisory boundaries](overview/advisory-boundaries.md) | Decision-support limits and human review |
| [Onboarding](operations/onboarding.md) | Local toolchain and developer entry points |
| [Lifecycle](operations/lifecycle.md) | Terraform roots, PowerShell scripts, validation tools |
| [Testing overview](testing/overview.md) | Unit, contract, infrastructure, integration, live, smoke, replay |

Suite READMEs colocated with tests:

- [tests/README.md](../tests/README.md)
- [tests/integration/README.md](../tests/integration/README.md)
- [tests/infrastructure/README.md](../tests/infrastructure/README.md)
- [scripts/README.md](../scripts/README.md)

## Other workstreams

These paths are owned by other documentation workstreams. This index names them; it does not link to unpublished pages.

| Area | Path | Owner |
| --- | --- | --- |
| Ingestion and reference data | `docs/ingestion/**` | WS2 |
| Processing pipelines | `docs/processing/**` | WS3 |
| DynamoDB data model | `docs/data/dynamodb.md` | WS3 |
| Operational API | `docs/api/**` | WS3 |
| Dashboard pointer | `docs/dashboard/**` | WS3 |
| Historical facts and analytics | `docs/historical/**` | WS4 |
| AI Operations Copilot | `docs/ai/**` | WS5 |

Existing canonical package and module pages this tree already links from overview pages:

- [dashboard/README.md](../dashboard/README.md)
- [functions/shared/wilvor_operational/README.md](../functions/shared/wilvor_operational/README.md)
- [functions/shared/wilvor_ai/README.md](../functions/shared/wilvor_ai/README.md)
- [functions/shared/wilvor_historical/README.md](../functions/shared/wilvor_historical/README.md)
- [functions/shared/wilvor_historical_query/README.md](../functions/shared/wilvor_historical_query/README.md)
- [modules/historical_facts/README.md](../modules/historical_facts/README.md)
- [modules/historical_facts_data/README.md](../modules/historical_facts_data/README.md)
- [modules/historical_analytics/README.md](../modules/historical_analytics/README.md)

## Writing rules

Shared files with no workstream owner:

- `docs/DOCUMENTATION_STANDARDS.md`
- `.cursor/rules/wilvor-documentation.mdc`

A page-writing task does not edit those files.
