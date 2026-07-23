# Wilvor Infrastructure Tests

The infrastructure suite has two independent layers.

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

```powershell
python -m pip install -r requirements-infrastructure.txt
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
