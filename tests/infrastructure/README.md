# Wilvor Infrastructure Tests

**Canonical page:** this page for Terraform and AWS infrastructure tests. Layer map: [`../../docs/testing/overview.md`](../../docs/testing/overview.md)
**Verified against:** `8abd2d5` on `docs/ws1-platform-foundations`
**Maturity:** CODE, INFRA
**Production authorization:** no grant found. AWS tests fail unless Terraform reports `environment = dev`.

The infrastructure suite has two independent layers.

INFRA means the asserting test files exist. This file does not record that they
passed, and Terraform tests do not establish DEPLOYED.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `tests/infrastructure/terraform/`, `tests/infrastructure/aws/` |
| Terraform | TF | tests parse `envs/dev` and module `.tf` files |
| Unit tests | none found in this directory | |
| Contract tests | none found in this directory | AI contracts are under `tests/contracts/` |
| Infrastructure tests | INFRA | terraform and aws subtrees |
| Integration tests | none found in this directory | |
| Live harness | none found in this directory | |
| Deployment | UNKNOWN | AWS tests can observe an account when run; this page does not cite a run |
| Production authorization | no grant found | `tests/infrastructure/aws/test_identity.py` (`test_tests_are_targeting_dev_environment`) |

## Terraform tests

Location:

```text
tests/infrastructure/terraform/
```

These tests run against source code and optional Terraform plan files.

They check:

- `terraform fmt`
- `terraform validate`
- Terraform and AWS provider constraints
- Required root modules, variables, outputs, and default tags
- Local module source paths
- Disabled poller schedules in development
- Use of the default EventBridge bus
- Accidental credentials in `.tf` files
- Optional plan deletion and non-dev resource checks

Run:

```powershell
.\scripts\run-infrastructure-tests.ps1 -Target terraform
```

Run plan-safety assertions against an existing plan:

```powershell
terraform -chdir=envs/dev plan -out=tfplan

.\scripts\run-infrastructure-tests.ps1 `
    -Target terraform `
    -PlanFile tfplan
```

Plan tests reject deletion and replacement actions by default.

## AWS tests

Location:

```text
tests/infrastructure/aws/
```

These tests read deployed resource names from:

```powershell
terraform -chdir=envs/dev output -json
```

They then validate the authenticated development account:

- AWS identity and dev-environment guard
- Kinesis stream status
- DynamoDB table status
- S3 existence, public-access blocking, and encryption
- Lambda runtime, state, IAM role, and environment contracts
- Enabled Kinesis-to-Lambda event-source mappings
- Disabled poller EventBridge rules
- CloudWatch dashboards, Lambda log groups, alarms, and weather-event logs
- OpenSky Secrets Manager metadata without reading the secret value

Run:

```powershell
.\scripts\run-infrastructure-tests.ps1 -Target aws
```

Run both layers:

```powershell
.\scripts\run-infrastructure-tests.ps1 -Target all
```

## Install dependencies

From the repository root. Pip resolves the nested `-r requirements-test.txt`
inside this file relative to `tests/`, not the current working directory:

```powershell
python -m pip install -r tests/requirements-infrastructure.txt
```

## Safety

AWS tests are read-only. They do not invoke pollers, write stream records, update
tables, read secret values, or modify infrastructure.

The tests fail before resource checks unless Terraform reports:

```text
environment = dev
```

Results are written under:

```text
test-results/infrastructure/
```

## Known limitations

- `test_root_contract.py` `EXPECTED_MODULES` lists only
  `aircraft_foundation`, `weather_events`, `sigmet`, `metar`, and `taf`.
  Later `envs/dev` modules are asserted by historical contract tests, not that
  set (source: `tests/infrastructure/terraform/test_root_contract.py`,
  `EXPECTED_MODULES`).
- AWS tests require an applied `dev` stack. Whether that stack exists is
  UNKNOWN on this page.
- `scripts/run-infrastructure-tests.ps1` still mentions repo-root
  `requirements-infrastructure.txt` in its error text. At `8abd2d5` the file
  is `tests/requirements-infrastructure.txt`. The install command on this page
  uses that path.

## Historical record

The previous install command on this page was
`python -m pip install -r requirements-infrastructure.txt` with no `tests/`
prefix. The file at `8abd2d5` is `tests/requirements-infrastructure.txt`.

## Sources

- Source: `tests/infrastructure/terraform/test_root_contract.py` (`EXPECTED_MODULES`)
- Source: `tests/infrastructure/terraform/test_historical_facts_contract.py` (`test_dev_historical_facts_collection_is_enabled`)
- Source: `tests/infrastructure/aws/test_identity.py` (`test_tests_are_targeting_dev_environment`)
- Source: `scripts/run-infrastructure-tests.ps1`
