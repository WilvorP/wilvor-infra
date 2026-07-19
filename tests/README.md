# Wilvor Test Suite

This directory contains the automated tests for the Wilvor infrastructure and data pipelines.

The structure separates fast deterministic tests from tests that require deployed AWS resources or live external APIs.

## Directory structure

```text
tests/
├── README.md
├── conftest.py
│
├── unit/
│   ├── aircraft/
│   ├── sigmet/
│   ├── metar/
│   ├── taf/
│   └── shared/
│
├── integration/
│   ├── aircraft/
│   ├── sigmet/
│   ├── metar/
│   ├── taf/
│   └── combined/
│
├── infrastructure/
│   ├── terraform/
│   └── aws/
│
├── smoke/
│   └── live/
│
├── fixtures/
│   ├── aircraft/
│   │   ├── valid/
│   │   ├── invalid/
│   │   └── expected/
│   ├── sigmet/
│   │   ├── valid/
│   │   ├── invalid/
│   │   └── expected/
│   ├── metar/
│   │   ├── valid/
│   │   ├── invalid/
│   │   └── expected/
│   ├── taf/
│   │   ├── valid/
│   │   ├── invalid/
│   │   └── expected/
│   └── shared/
│
├── contracts/
├── helpers/
└── replay/
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
- Shared utility functions

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

`integration/combined/` is for cross-pipeline tests involving more than one data source.

### `infrastructure/terraform/`

Static and plan-level Terraform tests.

Examples:

- Formatting and validation
- Required outputs
- Expected modules and resources
- Variable constraints
- Resource naming
- Safe plan assertions

### `infrastructure/aws/`

Assertions against deployed AWS resources.

Examples:

- Lambda functions exist
- Event-source mappings are enabled
- EventBridge rules have the correct targets
- Kinesis streams and DynamoDB tables are active
- CloudWatch dashboards and alarms exist
- Required log groups and retention settings are configured

### `smoke/live/`

Small tests that call live external data sources through the real pollers.

These tests are intentionally separate because OpenSky and NOAA responses can change and external services can temporarily fail.

### `fixtures/`

Controlled test inputs and expected outputs.

Each pipeline has:

- `valid/` — records expected to pass validation
- `invalid/` — records expected to be rejected
- `expected/` — normalized records, state records, events, or other expected results

### `contracts/`

Tests for versioned internal schemas and service contracts.

Examples:

- Required fields
- Schema versions
- Timestamp formats
- EventBridge event structures
- Compatibility between producers and consumers

### `helpers/`

Reusable test utilities.

Examples:

- Terraform output readers
- AWS client factories
- Polling and retry helpers
- Kinesis event encoders
- S3 and DynamoDB cleanup helpers
- Test correlation ID generation

### `replay/`

Tests that replay archived or recorded events through processors.

Replay tests provide deterministic regression coverage without relying on current external API responses.

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