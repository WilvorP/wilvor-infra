# Documentation

This page is an index. It states no behavior. Canonical pages own the claims.

**Verified against:** `956444d` on `docs/wilvor-documentation`

## How to use this tree

1. Read [`DOCUMENTATION_STANDARDS.md`](DOCUMENTATION_STANDARDS.md) before editing documentation. Maturity labels are defined only there.
2. Start at the [repository README](../README.md) for a platform summary.
3. Follow the topic pages below. Link to a canonical page; do not copy it.

During documentation work, leave Python, TypeScript, Terraform, tests, operational scripts, and infrastructure unchanged.

## Published documentation

| Area | Start here | Topic |
| --- | --- | --- |
| Architecture | [Architecture](overview/architecture.md) | Operational, historical, and AI planes; data flow |
| Repository structure | [Repository structure](overview/repository-structure.md) | Directory map and technology stack |
| Advisory boundaries | [Advisory boundaries](overview/advisory-boundaries.md) | Decision-support limits and human review |
| Operations | [Onboarding](operations/onboarding.md), [Lifecycle](operations/lifecycle.md) | Local toolchain, Terraform roots, PowerShell scripts |
| Testing | [Testing overview](testing/overview.md) | Unit, contract, infrastructure, integration, live, smoke, replay |
| Ingestion | [Ingestion index](ingestion/README.md), [Overview](ingestion/overview.md) | Aircraft, SIGMET, METAR, TAF, station and runway loaders |
| Processing | [Operational processing](processing/README.md) | Projection, encounter, risk, airport, recommendations, alerts |
| DynamoDB | [Operational tables](data/dynamodb.md) | Keys, TTL, producers, readers, current-set |
| API | [API index](api/README.md), [Operational API](api/operational-api.md) | HTTP routes, contracts, CORS, current-set reads |
| Dashboard | [Dashboard pointer](dashboard/README.md) | Pointer to the Live Operations Console README |
| Historical analytics | [Historical index](historical/README.md) | Facts, transport, Glue/Athena, queries, coverage |
| AI | [AI index](ai/README.md) | Specialists, Master Agent, tools, evidence, tests |

Suite READMEs colocated with tests:

- [tests/README.md](../tests/README.md)
- [tests/integration/README.md](../tests/integration/README.md)
- [tests/infrastructure/README.md](../tests/infrastructure/README.md)
- [tests/contracts/README.md](../tests/contracts/README.md)
- [scripts/README.md](../scripts/README.md)

## Colocated package and module pages

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
