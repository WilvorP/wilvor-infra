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
    "dynamodb:CreateTable",
    "dynamodb:PartiQLSelect",
    "dynamodb:PartiQLInsert",
    "dynamodb:PartiQLUpdate",
    "dynamodb:PartiQLDelete",
)
LEDGER_ALLOW_ACTIONS = (
    "dynamodb:GetItem",
    "dynamodb:PutItem",
    "dynamodb:TransactWriteItems",
)
PROHIBITED_GETITEM_TABLE_ACTIONS = (
    "dynamodb:PutItem",
    "dynamodb:UpdateItem",
    "dynamodb:DeleteItem",
    "dynamodb:TransactWriteItems",
    "dynamodb:BatchWriteItem",
    "dynamodb:BatchGetItem",
    "dynamodb:Scan",
    "dynamodb:Query",
    "dynamodb:CreateTable",
    "dynamodb:PartiQLSelect",
    "dynamodb:PartiQLInsert",
    "dynamodb:PartiQLUpdate",
    "dynamodb:PartiQLDelete",
)
AIRCRAFT_TABLE_NAME = "wilvor-dev-aircraft-current-state"


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


def _hcl_string_list(source: str, name: str) -> tuple[str, ...]:
    match = re.search(rf"{re.escape(name)}\s*=\s*\[(.*?)\]", source, flags=re.S)
    assert match is not None, f"Missing list {name}"
    return tuple(re.findall(r'"([^"]+)"', match.group(1)))


def _brace_block(text: str, header: str) -> str:
    start = text.find(header)
    assert start != -1, f"Missing block {header}"
    brace = text.find("{", start)
    depth = 0
    for index, character in enumerate(text[brace:], start=brace):
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[brace + 1 : index]
    raise AssertionError(f"Unclosed block {header}")


def _matches(pattern: str, value: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith("*"):
        return value.startswith(pattern[:-1])
    return pattern == value


def _decision(statements: tuple[dict[str, object], ...], action: str, resource: str) -> str:
    """AWS evaluation for one action and resource. Explicit deny wins."""

    allowed = False
    for statement in statements:
        actions = statement["actions"]
        assert isinstance(actions, tuple)
        if not any(_matches(pattern, action) for pattern in actions):
            continue
        not_resources = statement.get("not_resources")
        resources = statement.get("resources")
        if isinstance(not_resources, tuple):
            applies = not any(_matches(pattern, resource) for pattern in not_resources)
        else:
            assert isinstance(resources, tuple)
            applies = any(_matches(pattern, resource) for pattern in resources)
        if not applies:
            continue
        if statement["effect"] == "Deny":
            return "explicit_deny"
        allowed = True
    return "allow" if allowed else "implicit_deny"


def _reviewed_policy(ai_host_source: str) -> tuple[dict[str, object], ...]:
    ledger_actions = _hcl_string_list(ai_host_source, "prohibited_ledger_actions")
    specialist_actions = _hcl_string_list(ai_host_source, "prohibited_getitem_table_actions")
    assert ledger_actions == PROHIBITED_LEDGER_ACTIONS
    assert specialist_actions == PROHIBITED_GETITEM_TABLE_ACTIONS
    assert "dynamodb:TransactWriteItems" not in ledger_actions
    assert "dynamodb:TransactWriteItems" in specialist_actions
    assert "dynamodb:GetItem" not in specialist_actions

    policy = _brace_block(ai_host_source, 'data "aws_iam_policy_document" "host"')
    allow = _extract_statement(policy, "ReadAndPutExecutionLedger")
    specialist_allow = _extract_statement(policy, "ReadReviewedGetItemTables")
    unreviewed = _extract_statement(policy, "DenyUnreviewedDynamoDb")
    assert tuple(re.findall(r'"(dynamodb:[^"]+)"', allow)) == LEDGER_ALLOW_ACTIONS
    assert tuple(re.findall(r'"(dynamodb:[^"]+)"', specialist_allow)) == ("dynamodb:GetItem",)
    assert tuple(re.findall(r'"(dynamodb:[^"]+)"', unreviewed)) == ("dynamodb:*",)
    assert "local.prohibited_ledger_actions" in _extract_statement(policy, "DenyLedgerProhibitedApis")
    assert '"dynamodb:GetItem"' in specialist_allow
    assert "var.reviewed_getitem_table_arns" in specialist_allow
    assert "dynamodb:Query" not in specialist_allow
    assert "dynamodb:TransactWriteItems" not in specialist_allow
    assert "/index/" not in specialist_allow
    assert '"dynamodb:*"' in unreviewed
    assert "not_resources" in unreviewed
    assert "concat(" in unreviewed
    assert "aws_dynamodb_table.ledger.arn" in unreviewed
    assert "local.reviewed_dynamodb_arns" in unreviewed
    assert "/index/" not in unreviewed
    assert "DenyOtherDynamoDbTables" not in policy
    query_indexes = _extract_statement(policy, "QueryReviewedIndexes")
    query_bases = _extract_statement(policy, "QueryReviewedBaseTables")
    assessment = _extract_statement(policy, "ReadAndQueryReviewedBaseTables")
    assert tuple(re.findall(r'"(dynamodb:[^"]+)"', query_indexes)) == ("dynamodb:Query",)
    assert tuple(re.findall(r'"(dynamodb:[^"]+)"', query_bases)) == ("dynamodb:Query",)
    assert tuple(re.findall(r'"(dynamodb:[^"]+)"', assessment)) == (
        "dynamodb:GetItem",
        "dynamodb:Query",
    )
    assert "var.reviewed_query_index_arns" in query_indexes
    assert "var.reviewed_query_only_table_arns" in query_bases
    assert "var.reviewed_getitem_and_query_table_arns" in assessment
    assert "dynamodb:Scan" not in query_indexes
    assert "dynamodb:Scan" not in query_bases
    assert "dynamodb:Scan" not in assessment
    deny_sources = {
        "DenyReviewedQueryIndexMutations": "local.prohibited_query_target_actions",
        "DenyQueryOnlyTableNonQuery": "local.prohibited_query_target_actions",
        "DenyGetItemAndQueryTableMutations": "local.prohibited_getitem_and_query_actions",
    }
    for sid, source_name in deny_sources.items():
        denied = _extract_statement(policy, sid)
        assert re.search(r'effect\s*=\s*"Deny"', denied)
        assert source_name in denied
        assert '"dynamodb:Query"' not in denied

    def table(name: str) -> str:
        return f"arn:aws:dynamodb:us-west-1:{INTENDED_ACCOUNT_ID}:table/{name}"

    def index(name: str, index_name: str) -> str:
        return f"{table(name)}/index/{index_name}"

    ledger = table(LEDGER_TABLE_NAME)
    aircraft = table(AIRCRAFT_TABLE_NAME)
    hazards = table("wilvor-dev-active-hazards")
    coordinates = table("wilvor-dev-hazard-coordinates")
    encounters = table("wilvor-dev-aircraft-hazard-encounter")
    airports = table("wilvor-dev-airport-status")
    metar = table("wilvor-dev-metar-latest")
    taf = table("wilvor-dev-taf-latest")
    projections = table("wilvor-dev-aircraft-projection")
    risks = table("wilvor-dev-risk-results")
    recommendations = table("wilvor-dev-recommendations")
    alerts = table("wilvor-dev-active-alerts")
    assessments = table("wilvor-dev-airport-assessment")
    getitem_tables = (
        aircraft,
        hazards,
        encounters,
        airports,
        metar,
        taf,
        projections,
        risks,
        recommendations,
        alerts,
    )
    query_indexes = (
        index("wilvor-dev-active-hazards", "status-valid_to_epoch-index"),
        index("wilvor-dev-aircraft-hazard-encounter", "aircraft_id-detected_at_epoch-index"),
        index("wilvor-dev-aircraft-hazard-encounter", "hazard_id-detected_at_epoch-index"),
        index("wilvor-dev-aircraft-projection", "aircraft_id-generated_at_epoch-index"),
        index("wilvor-dev-risk-results", "aircraft_id-generated_at_epoch-index"),
        index("wilvor-dev-recommendations", "aircraft_id-created_at_epoch-index"),
        index("wilvor-dev-active-alerts", "aircraft_id-updated_at_epoch-index"),
    )
    query_target_actions = _hcl_string_list(ai_host_source, "prohibited_query_target_actions")
    get_and_query_actions = _hcl_string_list(
        ai_host_source,
        "prohibited_getitem_and_query_actions",
    )
    assert "dynamodb:Query" not in query_target_actions
    assert "dynamodb:GetItem" in query_target_actions
    assert "dynamodb:Query" not in get_and_query_actions
    assert "dynamodb:GetItem" not in get_and_query_actions
    assert "dynamodb:Scan" in query_target_actions
    assert "dynamodb:Scan" in get_and_query_actions
    return (
        {
            "effect": "Allow",
            "actions": LEDGER_ALLOW_ACTIONS,
            "resources": (ledger,),
        },
        {
            "effect": "Deny",
            "actions": ledger_actions,
            "resources": (ledger, f"{ledger}/index/*"),
        },
        {
            "effect": "Allow",
            "actions": ("dynamodb:GetItem",),
            "resources": getitem_tables,
        },
        {
            "effect": "Deny",
            "actions": specialist_actions,
            "resources": getitem_tables,
        },
        {
            "effect": "Allow",
            "actions": ("dynamodb:Query",),
            "resources": query_indexes,
        },
        {
            "effect": "Deny",
            "actions": query_target_actions,
            "resources": query_indexes,
        },
        {
            "effect": "Allow",
            "actions": ("dynamodb:Query",),
            "resources": (coordinates,),
        },
        {
            "effect": "Deny",
            "actions": query_target_actions,
            "resources": (coordinates,),
        },
        {
            "effect": "Allow",
            "actions": ("dynamodb:GetItem", "dynamodb:Query"),
            "resources": (assessments,),
        },
        {
            "effect": "Deny",
            "actions": get_and_query_actions,
            "resources": (assessments,),
        },
        {
            "effect": "Deny",
            "actions": ("dynamodb:*",),
            "not_resources": (ledger, *getitem_tables, *query_indexes, coordinates, assessments),
        },
        {
            "effect": "Deny",
            "actions": (
                "bedrock:*",
                "secretsmanager:GetSecretValue",
                "secretsmanager:GetSecret",
                "lambda:InvokeFunction",
                "lambda:AddPermission",
                "lambda:CreateFunctionUrlConfig",
                "execute-api:Invoke",
            ),
            "resources": ("*",),
        },
    )


def test_ai_host_iam_allows_ledger_consume_and_reviewed_getitem_only(
    ai_host_source: str,
    terraform_dir: Path,
):
    allow = _extract_statement(ai_host_source, "ReadAndPutExecutionLedger")
    denied = _extract_statement(ai_host_source, "DenyLedgerProhibitedApis")
    specialist_deny = _extract_statement(ai_host_source, "DenyReviewedGetItemTableWrites")
    closed_calls = _extract_statement(ai_host_source, "DenyModelSecretsAndInvoke")
    ai_host = _module_block(_read(terraform_dir / "main.tf"), "ai_host")

    assert "effect" not in allow
    assert "local.prohibited_ledger_actions" in denied
    assert "local.prohibited_getitem_table_actions" in specialist_deny
    assert re.search(r'effect\s*=\s*"Deny"', denied)
    assert re.search(r'effect\s*=\s*"Deny"', specialist_deny)
    for action in PROHIBITED_LEDGER_ACTIONS:
        assert action not in allow
    assert "dynamodb:TransactWriteItems" not in denied
    assert "iam:SimulatePrincipalPolicy" not in ai_host_source
    assert "dynamodb:DescribeTable" not in ai_host_source
    assert "module.aircraft_foundation.aircraft_current_state_table_arn" in ai_host
    for required in (
        "module.sigmet.active_hazards_table_arn",
        "module.encounter.aircraft_hazard_encounter_table_arn",
        "module.airport_status.airport_status_table_arn",
        "module.metar.metar_latest_table_arn",
        "module.taf.taf_latest_table_arn",
        "module.projection.aircraft_projection_table_arn",
        "module.risk.risk_results_table_arn",
        "module.recommendations.recommendations_table_arn",
        "module.active_alerts.active_alerts_table_arn",
        "module.sigmet.hazard_coordinates_table_arn",
        "module.airport_assessment.airport_assessment_table_arn",
        "/index/status-valid_to_epoch-index",
        "/index/aircraft_id-detected_at_epoch-index",
        "/index/hazard_id-detected_at_epoch-index",
        "/index/aircraft_id-generated_at_epoch-index",
        "/index/aircraft_id-created_at_epoch-index",
        "/index/aircraft_id-updated_at_epoch-index",
    ):
        assert required in ai_host
    for withheld in (
        "callsign-position_time_epoch-index",
        "taf_forecast_periods_table_arn",
        "hazard_cells_table_arn",
        "aircraft_projection_points_table_arn",
        "airport_id-created_at_epoch-index",
        "encounter_id-generated_at_epoch-index",
        "projection_id-hazard_version_key-index",
        "historical_facts",
        "bedrock:",
    ):
        assert withheld not in ai_host

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


def test_effective_iam_allows_reviewed_reads_and_keeps_explicit_denies(
    ai_host_source: str,
    repo_root: Path,
    terraform_dir: Path,
):
    tfvars = _read(terraform_dir / "terraform.tfvars")
    assert 'project_name = "wilvor"' in tfvars
    assert 'environment  = "dev"' in tfvars
    foundation = _read(repo_root / "modules" / "aircraft_foundation" / "main.tf")
    aircraft_table = _brace_block(foundation, 'resource "aws_dynamodb_table" "aircraft_current_state"')
    base_keys = aircraft_table.split("global_secondary_index", 1)[0]
    assert re.search(
        r'name\s*=\s*"\$\{var\.name_prefix\}-aircraft-current-state"',
        base_keys,
    )
    assert re.search(r'hash_key\s*=\s*"aircraft_id"', base_keys)
    assert "range_key" not in base_keys
    assert re.search(
        r'name\s*=\s*"callsign-position_time_epoch-index"',
        aircraft_table,
    )
    statements = _reviewed_policy(ai_host_source)
    ledger = f"arn:aws:dynamodb:us-west-1:{INTENDED_ACCOUNT_ID}:table/{LEDGER_TABLE_NAME}"
    aircraft = f"arn:aws:dynamodb:us-west-1:{INTENDED_ACCOUNT_ID}:table/{AIRCRAFT_TABLE_NAME}"
    aircraft_index = f"{aircraft}/index/callsign-position_time_epoch-index"
    ledger_index = f"{ledger}/index/not-a-ledger-index"
    encounter = (
        f"arn:aws:dynamodb:us-west-1:{INTENDED_ACCOUNT_ID}:table/"
        "wilvor-dev-aircraft-hazard-encounter"
    )
    encounter_index = f"{encounter}/index/aircraft_id-detected_at_epoch-index"
    encounter_other_index = f"{encounter}/index/projection_id-hazard_version_key-index"
    coordinates = (
        f"arn:aws:dynamodb:us-west-1:{INTENDED_ACCOUNT_ID}:table/"
        "wilvor-dev-hazard-coordinates"
    )
    assessments = (
        f"arn:aws:dynamodb:us-west-1:{INTENDED_ACCOUNT_ID}:table/"
        "wilvor-dev-airport-assessment"
    )
    assessment_index = f"{assessments}/index/airport_id-created_at_epoch-index"
    cells = (
        f"arn:aws:dynamodb:us-west-1:{INTENDED_ACCOUNT_ID}:table/"
        "wilvor-dev-hazard-cells"
    )

    expected = {
        ("dynamodb:GetItem", ledger): "allow",
        ("dynamodb:PutItem", ledger): "allow",
        ("dynamodb:TransactWriteItems", ledger): "allow",
        ("dynamodb:Query", ledger): "explicit_deny",
        ("dynamodb:Scan", ledger): "explicit_deny",
        ("dynamodb:DeleteItem", ledger): "explicit_deny",
        ("dynamodb:UpdateItem", ledger): "explicit_deny",
        ("dynamodb:BatchWriteItem", ledger): "explicit_deny",
        ("dynamodb:CreateTable", ledger): "explicit_deny",
        ("dynamodb:PartiQLSelect", ledger): "explicit_deny",
        ("dynamodb:BatchGetItem", ledger): "implicit_deny",
        ("dynamodb:DescribeTable", ledger): "implicit_deny",
        ("dynamodb:GetItem", ledger_index): "explicit_deny",
        ("dynamodb:TransactWriteItems", ledger_index): "explicit_deny",
        ("dynamodb:GetItem", aircraft): "allow",
        ("dynamodb:Query", aircraft): "explicit_deny",
        ("dynamodb:Scan", aircraft): "explicit_deny",
        ("dynamodb:PutItem", aircraft): "explicit_deny",
        ("dynamodb:UpdateItem", aircraft): "explicit_deny",
        ("dynamodb:DeleteItem", aircraft): "explicit_deny",
        ("dynamodb:TransactWriteItems", aircraft): "explicit_deny",
        ("dynamodb:BatchGetItem", aircraft): "explicit_deny",
        ("dynamodb:BatchWriteItem", aircraft): "explicit_deny",
        ("dynamodb:PartiQLSelect", aircraft): "explicit_deny",
        ("dynamodb:DescribeTable", aircraft): "implicit_deny",
        ("dynamodb:GetItem", aircraft_index): "explicit_deny",
        ("dynamodb:Query", aircraft_index): "explicit_deny",
        ("dynamodb:Scan", aircraft_index): "explicit_deny",
        ("dynamodb:GetItem", encounter): "allow",
        ("dynamodb:Query", encounter): "explicit_deny",
        ("dynamodb:Scan", encounter): "explicit_deny",
        ("dynamodb:Query", encounter_index): "allow",
        ("dynamodb:Scan", encounter_index): "explicit_deny",
        ("dynamodb:GetItem", encounter_index): "explicit_deny",
        ("dynamodb:PutItem", encounter_index): "explicit_deny",
        ("dynamodb:Query", encounter_other_index): "explicit_deny",
        ("dynamodb:Query", coordinates): "allow",
        ("dynamodb:GetItem", coordinates): "explicit_deny",
        ("dynamodb:Scan", coordinates): "explicit_deny",
        ("dynamodb:PutItem", coordinates): "explicit_deny",
        ("dynamodb:GetItem", assessments): "allow",
        ("dynamodb:Query", assessments): "allow",
        ("dynamodb:Scan", assessments): "explicit_deny",
        ("dynamodb:PutItem", assessments): "explicit_deny",
        ("dynamodb:Query", assessment_index): "explicit_deny",
        ("dynamodb:GetItem", cells): "explicit_deny",
        ("dynamodb:Query", cells): "explicit_deny",
        ("dynamodb:Scan", cells): "explicit_deny",
        ("dynamodb:TransactWriteItems", encounter): "explicit_deny",
        ("dynamodb:PutItem", encounter): "explicit_deny",
        ("iam:SimulatePrincipalPolicy", ledger): "implicit_deny",
        ("bedrock:InvokeModel", "*"): "explicit_deny",
        ("lambda:InvokeFunction", "arn:aws:lambda:us-west-1:396037508959:function:wilvor-dev-ai-host"): "explicit_deny",
        ("secretsmanager:GetSecretValue", "arn:aws:secretsmanager:us-west-1:396037508959:secret:example"): "explicit_deny",
    }
    for (action, resource), decision in expected.items():
        assert _decision(statements, action, resource) == decision, (action, resource)


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
    assert re.search(r"memory_size\s*=\s*256", lambda_text)
    assert re.search(r"timeout\s*=\s*30", lambda_text)
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
