# Wilvor Test Suite

**Canonical page:** this page for suite layout. Layer map: [`../docs/testing/overview.md`](../docs/testing/overview.md)
**Verified against:** `8abd2d5` on `docs/ws1-platform-foundations`
**Maturity:** CODE, UNIT, CONTRACT, INFRA, INT, LIVE
**Production authorization:** no grant found. Integration and AWS infrastructure tests require Terraform `environment = dev`.

This directory contains the automated tests for the Wilvor infrastructure and data pipelines.

The structure separates fast deterministic tests from tests that require deployed AWS resources or live external APIs.

This file does not record a current pass count. UNIT, CONTRACT, INFRA, and INT mean asserting files exist and were sampled; they do not mean the suite passed today.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `tests/` at `8abd2d5` |
| Terraform | TF | `tests/infrastructure/terraform/` reads `envs/dev` |
| Unit tests | UNIT | `tests/unit/` |
| Contract tests | CONTRACT | `tests/contracts/` |
| Infrastructure tests | INFRA | `tests/infrastructure/` |
| Integration tests | INT | `tests/integration/{aircraft,sigmet,metar,taf}/` |
| Live harness | LIVE | `scripts/validate_*.py` |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | `tests/integration/conftest.py` |

## Directory structure

```text
tests/
├── README.md
├── conftest.py
├── manual_mapper_test.py
├── requirements-test.txt
├── requirements-infrastructure.txt
├── requirements-live-validation.txt
│
├── unit/
│   ├── active_alerts/
│   ├── aircraft/
│   ├── airport_status/
│   ├── encounter/
│   ├── historical_facts/
│   ├── historical_query/
│   ├── metar/
│   ├── operational_api/
│   ├── projection/
│   ├── recommendations/
│   ├── risk/
│   ├── runway_metadata/
│   ├── scripts/
│   ├── shared/
│   ├── sigmet/
│   └── taf/
│
├── integration/
│   ├── aircraft/
│   ├── sigmet/
│   ├── metar/
│   ├── taf/
│   └── combined/          # .gitkeep only
│
├── infrastructure/
│   ├── terraform/
│   └── aws/
│
├── smoke/
│   └── live/              # .gitkeep only
│
├── fixtures/
│   ├── aircraft/          # valid/invalid/expected are .gitkeep
│   ├── sigmet/
│   ├── metar/
│   ├── taf/
│   ├── shared/            # .gitkeep
│   └── *.py / *.json      # AI and live-ops contract examples
│
├── contracts/             # 117 test_*.py modules at 8abd2d5
├── helpers/
├── fakes/
└── replay/                # .gitkeep only
```

## Folder responsibilities

### `unit/`

Fast tests that run without deployed AWS infrastructure or external API calls.

Examples:

- Validators
- Parsers
- Normalizers
- Schema mapping
- Idempotency and change detection
- Lambda handler behavior with mocked dependencies
- Shared `wilvor_operational` current-set helpers
- Historical facts transform and query operations
- Operational API repository behavior
- Operator-harness CLI dry-run gates under `unit/scripts/`

`tests/conftest.py` sets fake AWS environment variables (`AWS_ACCESS_KEY_ID=testing`).

### `integration/`

Deterministic tests against the deployed development environment.

Each test should inject controlled fixture data and verify the resulting pipeline behavior.

Examples:

- Input reaches Kinesis
- Raw data is archived in S3
- Valid records reach DynamoDB
- Invalid records reach the bad-record archive
- EventBridge receives the expected event
- Duplicate weather records do not create unnecessary state changes

Implemented files: `aircraft/test_aircraft_pipeline.py`, `sigmet/test_sigmet_pipeline.py`, `metar/test_metar_pipeline.py`, `taf/test_taf_pipeline.py`.

`integration/combined/` is empty (`.gitkeep` only). Downstream processors exist as Terraform modules in `envs/dev/main.tf`. Dedicated unit-test directories exist for `projection`, `encounter`, `risk`, `airport_status`, `recommendations`, and `active_alerts`. Search for `tests/unit/airport_assessment/` returned no directory. These processors are not covered by this integration tree. See [`integration/README.md`](integration/README.md).

### `infrastructure/terraform/`

Static and plan-level Terraform tests.

Examples:

- Formatting and validation
- Required outputs
- Expected modules and resources
- Variable constraints
- Resource naming
- Safe plan assertions
- Historical facts and analytics enablement contracts

`test_root_contract.py` `EXPECTED_MODULES` lists `aircraft_foundation`, `weather_events`, `sigmet`, `metar`, and `taf` only.

### `infrastructure/aws/`

Assertions against deployed AWS resources.

Examples:

- Lambda functions exist
- Event-source mappings are enabled
- EventBridge rules have the correct targets
- Kinesis streams and DynamoDB tables are active
- CloudWatch dashboards and alarms exist
- Required log groups and retention settings are configured

These tests are documented as read-only in [`infrastructure/README.md`](infrastructure/README.md). They still require an applied `dev` stack. DEPLOYED remains UNKNOWN until an apply record is cited.

### `smoke/live/`

Intended home for small tests that call live external data sources through the real pollers.

At `8abd2d5` this directory contains only `.gitkeep`. Operator poll and specialist harnesses live under `scripts/`, not here.

### `fixtures/`

Controlled test inputs and expected outputs.

Each ingestion pipeline directory has `valid/`, `invalid/`, and `expected/` placeholders (`.gitkeep`). AI contract examples are Python/JSON files beside those trees.

### `contracts/`

Tests for versioned internal schemas and service contracts.

Examples:

- Required fields
- Schema versions
- Timestamp formats
- EventBridge event structures
- Compatibility between producers and consumers
- AI Operations Copilot contracts, Master routing, specialist envelopes, import isolation

The dependency-free AI Operations Copilot Phase 0 contracts are tested in:

```text
tests/contracts/test_ai_copilot_contracts.py
```

Their synthetic examples are defined in:

```text
tests/fixtures/ai_copilot_contract_examples.py
```

Run them without AWS credentials, network access, or an AI model:

```powershell
python -m pytest tests/contracts -q -p no:cacheprovider
```

`tests/contracts/README.md` is owned by documentation workstream WS5.

### `helpers/`

Reusable test utilities.

Examples:

- Terraform output readers
- AWS client factories
- Polling and retry helpers
- Kinesis event encoders
- S3 and DynamoDB cleanup helpers
- Test correlation ID generation

`helpers/integration_support.py` is used by the integration suite.

### `fakes/`

Test doubles for Anthropic Messages, Bedrock runtime, historical operations, and scripted model providers. These support contract and unit tests. They are not AWS resources.

### `replay/`

Intended home for tests that replay archived or recorded events through processors.

At `8abd2d5` this directory contains only `.gitkeep`.

## Naming conventions

Python test files should use:

```text
test_<behavior>.py
```

Examples:

```text
test_aircraft_validator.py
test_sigmet_idempotency.py
test_metar_normalizer.py
test_taf_processor_integration.py
test_event_source_mappings.py
```

Test functions should describe the expected behavior:

```python
def test_rejects_aircraft_without_coordinates():
    ...
```

## Test boundaries

- Unit tests must not call AWS or external APIs.
- Infrastructure tests may read Terraform files or deployed AWS resources.
- Integration tests must use controlled fixtures and unique correlation IDs.
- Live smoke tests may call external providers.
- Tests must not depend on execution order.
- Tests must clean up records they create where practical.
- Production resources must never be targeted by this test suite.

## Generated results

Test reports will be written outside this directory:

```text
test-results/
├── unit/
├── infrastructure/
├── integration/
├── smoke/
└── combined/
```

The `test-results/` directory should remain excluded from Git.

JUnit XML is produced by `scripts/run-unit-tests.ps1`, `scripts/run-infrastructure-tests.ps1`, and `scripts/run-integration-tests.ps1` unless `-NoJUnit`. A Markdown report generator is not present in `scripts/` at this commit.

## Known limitations

- Current suite results are UNKNOWN.
- `tests/smoke/live/` and `tests/replay/` are placeholders.
- `tests/integration/combined/` is a placeholder.
- `scripts/run-unit-tests.ps1` references repo-root `requirements-test.txt`; the install file is `tests/requirements-test.txt`.
- Live Anthropic and Athena harnesses are operator tools under `scripts/`, not pytest smoke tests.

## Historical record

The previous directory tree on this page listed only `unit/{aircraft,sigmet,metar,taf,shared}` and did not show `contracts/` contents, `fakes/`, historical unit directories, `operational_api`, or the empty smoke/replay placeholders. That tree described an earlier layout. The tree above is the directory listing at `8abd2d5`.

The previous `contracts/` section already documented `test_ai_copilot_contracts.py` and the pytest command; those sentences are retained.

## Sources

- Source: `tests/` directory listing at `8abd2d5`
- Source: `tests/conftest.py`
- Source: `tests/integration/conftest.py`
- Source: `scripts/run-unit-tests.ps1`
- Tests: `tests/unit/aircraft/test_opensky_mapper.py` (`test_unit_conversions`)
- Tests: `tests/contracts/test_ai_copilot_contracts.py`
