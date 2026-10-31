"""Sprint 3D development AI host infrastructure contract.

These tests read Terraform and the closed handler. They do not call AWS,
plan, apply, or import state.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.infrastructure

INTENDED_ACCOUNT_ID = "396037508959"
LEDGER_TABLE_NAME = "wilvor_master_execution_ledger"
PROHIBITED_LEDGER_ACTIONS = (
    "dynamodb:Scan",
    "dynamodb:Query",
    "dynamodb:DeleteItem",
    "dynamodb:BatchWriteItem",
    "dynamodb:UpdateItem",
    "dynamodb:TransactWriteItems",
    "dynamodb:CreateTable",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _module_source(repo_root: Path) -> str:
    module_dir = repo_root / "modules" / "ai_host"
    parts = [
        _read(path)
        for path in sorted(module_dir.glob("*.tf"))
    ]
    return "\n".join(parts)


def _extract_statement(policy_text: str, sid: str) -> str:
    match = re.search(rf'sid\s*=\s*"{re.escape(sid)}"', policy_text)
    assert match is not None, f"Missing IAM statement {sid}"
    next_statement = policy_text.find("\n  statement {", match.end())
    if next_statement == -1:
        next_statement = len(policy_text)
    return policy_text[match.start() : next_statement]


def _module_block(text: str, name: str) -> str:
    marker = f'module "{name}" {{'
    start = text.find(marker)
    assert start != -1, f"Missing module {name}"
    depth = 0
    for index, character in enumerate(text[start:], start=start):
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"Unclosed module block {name}")


@pytest.fixture(scope="module")
def ai_host_source(repo_root: Path) -> str:
    return _module_source(repo_root)


def test_ai_host_is_wired_closed_and_separate_from_the_operational_api(
    terraform_dir: Path,
):
    main = _read(terraform_dir / "main.tf")
    ai_host = _module_block(main, "ai_host")
    operational_api = _module_block(main, "operational_api")

    assert 'source = "../../modules/ai_host"' in ai_host
    assert re.search(r"enable_invoke\s*=\s*false", ai_host)
    assert "enable_ai_query_route" not in ai_host

    assert 'source = "../../modules/operational_api"' in operational_api
    assert re.search(r"enable_ai_query_route\s*=\s*false", operational_api)
    for withheld in (
        "ai_lambda_function_arn",
        "ai_lambda_invoke_arn",
        "ai_jwt_issuer",
        "ai_jwt_audience",
    ):
        assert withheld not in operational_api


def test_development_root_is_bound_to_the_reviewed_account_and_region(
    terraform_dir: Path,
):
    variables = _read(terraform_dir / "variables.tf")
    guard = _read(terraform_dir / "account_guard.tf")
    providers = _read(terraform_dir / "providers.tf")
    tfvars = _read(terraform_dir / "terraform.tfvars")

    assert 'var.aws_region == "us-west-1"' in variables
    assert 'var.aws_profile == "wilvor-dev"' in variables
    assert 'var.environment == "dev"' in variables
    assert 'var.project_name == "wilvor"' in variables
    assert f'account_id == "{INTENDED_ACCOUNT_ID}"' in guard
    assert 'resource "terraform_data" "development_account_guard"' in guard
    assert "backend" not in providers
    assert 'aws_region   = "us-west-1"' in tfvars
    assert 'aws_profile  = "wilvor-dev"' in tfvars
    assert 'environment  = "dev"' in tfvars


def test_operational_api_route_default_stays_false(repo_root: Path):
    variables = _read(repo_root / "modules" / "operational_api" / "variables.tf")
    marker = 'variable "enable_ai_query_route"'
    start = variables.find(marker)
    assert start != -1
    end = variables.find("\nvariable ", start + len(marker))
    block = variables[start:end if end != -1 else len(variables)]

    assert "default     = false" in block or "default = false" in block
    assert "default     = true" not in block
    assert "default = true" not in block


def test_ai_host_module_does_not_publish_the_query_route(ai_host_source: str):
    forbidden_resources = (
        "aws_apigatewayv2_api",
        "aws_apigatewayv2_route",
        "aws_apigatewayv2_integration",
        "aws_apigatewayv2_authorizer",
        "aws_lambda_permission",
        "aws_lambda_function_url",
        "aws_lambda_alias",
        "aws_iam_role_policy_attachment",
    )
    for resource_type in forbidden_resources:
        assert f'resource "{resource_type}"' not in ai_host_source

    assert "enable_ai_query_route = true" not in ai_host_source
    assert "module.operational_api" not in ai_host_source
    assert "AWSLambdaBasicExecutionRole" not in ai_host_source
    assert re.search(
        r'output "ai_query_route_enabled" \{[^}]*value\s*=\s*false',
        ai_host_source,
        flags=re.S,
    )


def test_ledger_table_matches_the_sprint3c_key_and_ttl_contract(repo_root: Path):
    ledger = _read(repo_root / "modules" / "ai_host" / "ledger.tf")

    assert f'ledger_table_name = "{LEDGER_TABLE_NAME}"' in _read(
        repo_root / "modules" / "ai_host" / "locals.tf"
    )
    assert 'billing_mode = "PAY_PER_REQUEST"' in ledger
    assert re.search(r'hash_key\s*=\s*"pk"', ledger)
    assert re.search(r'range_key\s*=\s*"sk"', ledger)
    assert 'attribute_name = "retention_expires_at_epoch"' in ledger
    assert "global_secondary_index" not in ledger
    assert "stream_enabled" not in ledger
    assert "deletion_protection_enabled = false" in ledger
    assert "point_in_time_recovery" in ledger
    assert re.search(
        r"point_in_time_recovery\s*\{[^}]*enabled\s*=\s*false",
        ledger,
        flags=re.S,
    )


def test_ai_host_iam_is_limited_to_ledger_get_and_put(ai_host_source: str):
    allow = _extract_statement(ai_host_source, "ReadAndPutExecutionLedger")
    denied = _extract_statement(ai_host_source, "DenyLedgerProhibitedApis")
    other_tables = _extract_statement(ai_host_source, "DenyOtherDynamoDbTables")
    closed_calls = _extract_statement(ai_host_source, "DenyModelSecretsAndInvoke")

    assert '"dynamodb:GetItem"' in allow
    assert '"dynamodb:PutItem"' in allow
    assert "effect" not in allow
    assert "local.prohibited_ledger_actions" in denied
    for action in PROHIBITED_LEDGER_ACTIONS:
        assert action not in allow
        assert f'"{action}"' in ai_host_source

    assert re.search(r'effect\s*=\s*"Deny"', denied)
    assert 'effect = "Deny"' in other_tables
    assert '"dynamodb:*"' in other_tables
    assert "not_resources" in other_tables
    assert "aws_dynamodb_table.ledger.arn" in other_tables

    for action in (
        "bedrock:*",
        "secretsmanager:GetSecretValue",
        "lambda:InvokeFunction",
        "lambda:AddPermission",
        "lambda:CreateFunctionUrlConfig",
        "execute-api:Invoke",
    ):
        assert action in closed_calls

    logs = _extract_statement(ai_host_source, "WriteAiHostLogs")
    assert '"logs:CreateLogStream"' in logs
    assert '"logs:PutLogEvents"' in logs
    assert "logs:CreateLogGroup" not in logs


def test_lambda_is_isolated_closed_and_logged(repo_root: Path, ai_host_source: str):
    lambda_text = _read(repo_root / "modules" / "ai_host" / "lambda.tf")
    monitoring = _read(repo_root / "modules" / "ai_host" / "monitoring.tf")

    assert re.search(
        r'function_name\s*=\s*"\$\{var\.name_prefix\}-ai-host"',
        ai_host_source,
    )
    assert re.search(
        r'operational_api_function_name\s*=\s*"\$\{var\.name_prefix\}-operational-api"',
        ai_host_source,
    )
    assert re.search(r'handler\s*=\s*"handler\.lambda_handler"', lambda_text)
    assert re.search(r'runtime\s*=\s*"python3\.12"', lambda_text)
    assert re.search(
        r"reserved_concurrent_executions\s*=\s*local\.reserved_concurrent_executions",
        lambda_text,
    )
    assert re.search(r"closed_reserved_concurrency\s*=\s*0", ai_host_source)
    assert re.search(r'AI_QUERY_ROUTE_ENABLED\s*=\s*"false"', lambda_text)
    assert re.search(r'PRODUCTION_ISSUANCE_AVAILABLE\s*=\s*"false"', lambda_text)
    assert re.search(r'LEDGER_WRITES_ENABLED\s*=\s*"false"', lambda_text)
    assert "LEDGER_TABLE_NAME" in lambda_text
    assert "aws_cloudwatch_log_group" in lambda_text
    assert re.search(r"retention_in_days\s*=\s*var\.log_retention_days", lambda_text)
    for alarm_name in ("host_errors", "host_throttles", "ledger_system_errors"):
        assert f'resource "aws_cloudwatch_metric_alarm" "{alarm_name}"' in monitoring
    assert 'treat_missing_data' in monitoring
    assert "notBreaching" in monitoring
    assert repo_root.joinpath("modules/ai_host/src/handler.py").is_file()


def test_closed_handler_discards_untrusted_event_and_does_not_call_aws(
    repo_root: Path,
):
    handler_path = repo_root / "modules" / "ai_host" / "src" / "handler.py"
    source = _read(handler_path)

    assert "boto3" not in source
    assert "os.environ" not in source
    assert "urllib" not in source
    assert "requests" not in source
    assert "InvokeModel" not in source

    spec = importlib.util.spec_from_file_location("wilvor_ai_host_handler", handler_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    event = {
        "headers": {"Authorization": "secret-token"},
        "requestContext": {
            "authorizer": {"jwt": {"claims": {"sub": "attacker"}}},
            "routeKey": "POST /ai/query",
        },
        "trusted": True,
    }
    result = module.lambda_handler(event, object())
    rendered = json.dumps(result)

    assert result["statusCode"] == 503
    assert "secret-token" not in rendered
    assert "attacker" not in rendered
    body = json.loads(result["body"])
    assert body == {
        "schema": "wilvor.ai.host.closed.v1",
        "admitted": False,
        "production_issuance_available": False,
        "ai_query_route_enabled": False,
        "ledger_writes_enabled": False,
        "reason": "ai_host_invoke_closed",
    }


def test_development_root_does_not_enable_ai_query_anywhere(terraform_dir: Path):
    for path in terraform_dir.glob("*.tf"):
        text = _read(path)
        assert "enable_ai_query_route = true" not in text
        assert "enable_invoke      = true" not in text
        assert "enable_invoke = true" not in text
