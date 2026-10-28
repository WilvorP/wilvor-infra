"""Static contract for the opt-in JWT POST /ai/query route.

These tests read the operational API module. They do not run Terraform
plan or apply, and they do not call AWS.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


pytestmark = pytest.mark.infrastructure


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_DIR = REPO_ROOT / "modules" / "operational_api"
API_TF = MODULE_DIR / "api.tf"
VARIABLES_TF = MODULE_DIR / "variables.tf"

EXPECTED_GET_ROUTES = (
    "GET /health",
    "GET /overview",
    "GET /freshness",
    "GET /system-health",
    "GET /system-health/dashboards/{dashboardId}",
    "GET /system-health/dashboards/{dashboardId}/widgets/{widgetId}/image",
    "GET /aircraft",
    "GET /aircraft/{aircraftId}",
    "GET /map/aircraft",
    "GET /hazards/active",
    "GET /encounters/active",
    "GET /airports",
    "GET /airports/status",
    "GET /airports/{airportId}",
    "GET /recommendations/active",
    "GET /alerts/active",
)

OPERATIONAL_IAM_ACTIONS = (
    "dynamodb:GetItem",
    "dynamodb:Query",
    "dynamodb:Scan",
    "cloudwatch:DescribeAlarms",
    "cloudwatch:GetMetricStatistics",
    "lambda:GetAccountSettings",
    "cloudwatch:GetDashboard",
    "cloudwatch:GetMetricWidgetImage",
    "logs:CreateLogStream",
    "logs:PutLogEvents",
)

FORBIDDEN_PERMISSIONS = (
    "dynamodb:PutItem",
    "dynamodb:UpdateItem",
    "dynamodb:DeleteItem",
    "dynamodb:BatchWriteItem",
    "dynamodb:BatchExecuteStatement",
    "iam:PassRole",
    "iam:CreateRole",
    "iam:AttachRolePolicy",
    "iam:*",
    "lambda:CreateFunction",
    "lambda:UpdateFunctionCode",
    "lambda:UpdateFunctionConfiguration",
    "lambda:DeleteFunction",
    "lambda:AddPermission",
    "s3:PutObject",
    "s3:DeleteObject",
    "s3:*",
    "execute-api:*",
    "cognito-idp:",
    "aws_cognito",
    "aws_iam_openid_connect_provider",
)

AI_RESOURCES = (
    'resource "aws_apigatewayv2_authorizer" "ai_jwt"',
    'resource "aws_apigatewayv2_integration" "ai_query"',
    'resource "aws_apigatewayv2_route" "ai_query"',
    'resource "aws_lambda_permission" "ai_query"',
    'resource "aws_lambda_permission" "ai_query_qualified"',
)

PRECONDITION = """condition = (
        !var.enable_ai_query_route ||
        (
          local.ai_query_route_configuration_valid &&
          local.ai_query_route_key == "POST /ai/query"
        )
      )"""


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def extract_block(text: str, signature: str) -> str:
    """Return one HCL block, ignoring braces that appear inside strings."""

    start = text.index(signature)
    depth = 0
    in_string = False
    escaped = False
    opened = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
            opened = True
        elif char == "}":
            depth -= 1
            if opened and depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"Unclosed HCL block: {signature}")


def route_key_block(text: str) -> str:
    start = text.index("route_keys = toset([")
    end = text.index("])", start)
    return text[start:end]


def assignment(text: str, name: str) -> str:
    match = re.search(
        rf'(?m)^\s*{re.escape(name)}\s*=\s*"([^"]*)"',
        text,
    )
    assert match, name
    return match.group(1)


@pytest.fixture(scope="module")
def api_text() -> str:
    return read(API_TF)


@pytest.fixture(scope="module")
def variables_text() -> str:
    return read(VARIABLES_TF)


@pytest.fixture(scope="module")
def patterns(api_text: str) -> dict[str, str]:
    return {
        "issuer": assignment(api_text, "ai_jwt_issuer_pattern"),
        "function": assignment(api_text, "ai_lambda_function_arn_pattern"),
        "zero_account": assignment(api_text, "ai_lambda_zero_account_pattern"),
        "qualified": assignment(api_text, "ai_lambda_qualified_arn_pattern"),
    }


def issuer_configured(issuer: str, pattern: str) -> bool:
    return re.search(pattern, issuer) is not None


def audience_configured(audiences: list[str]) -> bool:
    if not audiences:
        return False
    return all(
        audience != ""
        and re.search(r"\s", audience) is None
        and "*" not in audience
        for audience in audiences
    )


def function_configured(
    function_arn: str,
    function_pattern: str,
    zero_account_pattern: str,
) -> bool:
    return (
        re.search(function_pattern, function_arn) is not None
        and re.search(zero_account_pattern, function_arn) is None
    )


def expected_invoke_arn(function_arn: str) -> str:
    parts = function_arn.split(":")
    partition = parts[1]
    region = parts[3]
    return (
        f"arn:{partition}:apigateway:{region}:lambda:path/"
        f"2015-03-31/functions/{function_arn}/invocations"
    )


def targets_operational_api(function_arn: str, name_prefix: str) -> bool:
    marker = f":function:{name_prefix}-operational-api"
    return function_arn.endswith(marker) or f"{marker}:" in function_arn


def enabled_configuration_accepted(
    *,
    enable: bool,
    issuer: str,
    audiences: list[str],
    function_arn: str,
    invoke_arn: str,
    name_prefix: str,
    patterns: dict[str, str],
) -> bool:
    """Mirror the module precondition. A disabled flag is not an AI route."""

    if not enable:
        return True
    function_ok = function_configured(
        function_arn,
        patterns["function"],
        patterns["zero_account"],
    )
    invoke_ok = (
        function_ok
        and invoke_arn != ""
        and invoke_arn == expected_invoke_arn(function_arn)
    )
    return (
        issuer_configured(issuer, patterns["issuer"])
        and audience_configured(audiences)
        and invoke_ok
        and not targets_operational_api(function_arn, name_prefix)
    )


def test_ai_query_route_is_disabled_by_default(variables_text: str):
    block = extract_block(variables_text, 'variable "enable_ai_query_route"')

    assert re.search(r"(?m)^  default\s+=\s+false\s*$", block)
    assert re.search(r"(?m)^  type\s+=\s+bool\s*$", block)
    assert "nullable    = false" in block


def test_default_configuration_creates_no_ai_route(api_text: str):
    route_keys = route_key_block(api_text)
    routes = extract_block(
        api_text,
        'resource "aws_apigatewayv2_route" "routes"',
    )

    assert "POST" not in route_keys
    assert 'ai_query_route_key = "POST /ai/query"' in api_text
    assert "POST /ai/query" not in routes
    for signature in AI_RESOURCES:
        block = extract_block(api_text, signature)
        assert "var.enable_ai_query_route" in block
        assert re.search(r"\bcount\s*=", block)


def test_enabling_requires_issuer_audience_and_external_lambda(
    api_text: str,
    variables_text: str,
    patterns: dict[str, str],
):
    for name in ("issuer", "function", "zero_account"):
        assert patterns[name] in variables_text

    assert "ai_jwt_issuer must be an externally configured https issuer URL" in api_text
    assert "ai_jwt_audience must include at least one externally configured audience" in api_text
    assert "ai_lambda_function_arn must be an explicit Lambda function ARN" in api_text
    assert "This module does not create that function." in api_text
    assert "ai_lambda_invoke_arn must be the API Gateway invoke ARN for ai_lambda_function_arn" in api_text
    assert "POST /ai/query must use a separate AI Lambda, not the operational API Lambda." in api_text
    assert (
        'arn:${split(":", var.ai_lambda_function_arn)[1]}:apigateway:'
        '${split(":", var.ai_lambda_function_arn)[3]}:lambda:path/'
        "2015-03-31/functions/${var.ai_lambda_function_arn}/invocations"
    ) in api_text
    assert 'endswith(\n      var.ai_lambda_function_arn,\n      ":function:${var.name_prefix}-operational-api",\n    )' in api_text
    assert '":function:${var.name_prefix}-operational-api:"' in api_text
    assert "local.ai_jwt_issuer_configured &&" in api_text
    assert "local.ai_jwt_audience_configured &&" in api_text
    assert "local.ai_lambda_invoke_configured &&" in api_text
    assert "!local.ai_lambda_targets_operational_api" in api_text

    api = extract_block(api_text, 'resource "aws_apigatewayv2_api" "http"')
    assert PRECONDITION in api
    for signature in AI_RESOURCES:
        assert PRECONDITION in extract_block(api_text, signature)


@pytest.mark.parametrize(
    ("enable", "issuer", "audiences", "function_arn", "invoke_matches", "accepted"),
    [
        (False, "", [], "", False, True),
        (True, "", [], "", False, False),
        (True, "https://idp.wilvor.test/realms/ops", [], "", False, False),
        (True, "https://idp.wilvor.test/realms/ops", ["wilvor-dashboard"], "", False, False),
        (True, "http://idp.wilvor.test/realms/ops", ["wilvor-dashboard"], "external", True, False),
        (True, "https://idp.wilvor.test/realms/ops", ["*"], "external", True, False),
        (True, "https://idp.wilvor.test/realms/ops", ["wilvor dashboard"], "external", True, False),
        (True, "https://idp.wilvor.test/realms/ops", ["wilvor-dashboard"], "zero-account", True, False),
        (True, "https://idp.wilvor.test/realms/ops", ["wilvor-dashboard"], "operational", True, False),
        (True, "https://idp.wilvor.test/realms/ops", ["wilvor-dashboard"], "external", False, False),
        (True, "https://idp.wilvor.test/realms/ops", ["wilvor-dashboard"], "external", True, True),
        (True, "https://idp.wilvor.test:443/realms/ops", ["wilvor-dashboard"], "qualified", True, True),
        (
            True,
            "https://cognito-idp.us-gov-west-1.amazonaws.com/us-gov-west-1_ExternalPool",
            ["wilvor-dashboard"],
            "govcloud",
            True,
            True,
        ),
    ],
)
def test_enabled_configuration_decision_table(
    patterns: dict[str, str],
    enable: bool,
    issuer: str,
    audiences: list[str],
    function_arn: str,
    invoke_matches: bool,
    accepted: bool,
):
    name_prefix = "wilvor-dev"
    functions = {
        "external": (
            "arn:aws:lambda:us-west-1:123456789012:function:wilvor-ai-query"
        ),
        "qualified": (
            "arn:aws:lambda:us-west-1:123456789012:function:wilvor-ai-query:live"
        ),
        "zero-account": (
            "arn:aws:lambda:us-west-1:000000000000:function:wilvor-ai-query"
        ),
        "operational": (
            "arn:aws:lambda:us-west-1:123456789012:function:"
            f"{name_prefix}-operational-api"
        ),
        "govcloud": (
            "arn:aws-us-gov:lambda:us-gov-west-1:123456789012:function:wilvor-ai-query"
        ),
        "": "",
        "not-an-arn": "not-an-arn",
    }
    selected = functions[function_arn]
    invoke_arn = expected_invoke_arn(selected) if selected and invoke_matches else ""
    if selected and not invoke_matches:
        invoke_arn = expected_invoke_arn(
            "arn:aws:lambda:us-west-1:123456789012:function:other-function"
        )

    assert enabled_configuration_accepted(
        enable=enable,
        issuer=issuer,
        audiences=audiences,
        function_arn=selected,
        invoke_arn=invoke_arn,
        name_prefix=name_prefix,
        patterns=patterns,
    ) is accepted


def test_variable_shapes_reject_malformed_values_before_enablement(
    variables_text: str,
    patterns: dict[str, str],
):
    issuer = extract_block(variables_text, 'variable "ai_jwt_issuer"')
    audience = extract_block(variables_text, 'variable "ai_jwt_audience"')
    function = extract_block(variables_text, 'variable "ai_lambda_function_arn"')
    invoke = extract_block(variables_text, 'variable "ai_lambda_invoke_arn"')

    assert 'var.ai_jwt_issuer == ""' in issuer
    assert patterns["issuer"] in issuer
    assert 'default     = ""' in issuer
    assert "[[:space:]]" in audience
    assert '!strcontains(audience, "*")' in audience
    assert "default     = []" in audience
    assert patterns["function"] in function
    assert patterns["zero_account"] in function
    assert 'var.ai_lambda_function_arn == ""' in function
    assert "/invocations$" in invoke
    assert 'var.ai_lambda_invoke_arn == ""' in invoke

    assert issuer_configured("https://idp.wilvor.test/realms/ops", patterns["issuer"])
    assert not issuer_configured("http://idp.wilvor.test/realms/ops", patterns["issuer"])
    assert not issuer_configured("https://idp.wilvor.test/*", patterns["issuer"])
    assert not issuer_configured("", patterns["issuer"])
    assert audience_configured(["wilvor-dashboard", "https://dashboard.wilvor.test"])
    assert not audience_configured([])
    assert not audience_configured([""])
    assert not audience_configured(["wilvor dashboard"])
    assert not audience_configured(["*"])
    assert not audience_configured(["wilvor-*"])


def test_protected_route_always_uses_jwt_authorization(api_text: str):
    route = extract_block(api_text, 'resource "aws_apigatewayv2_route" "ai_query"')
    authorizer = extract_block(
        api_text,
        'resource "aws_apigatewayv2_authorizer" "ai_jwt"',
    )

    assert re.search(r'(?m)^\s*authorization_type\s*=\s*"JWT"\s*$', route)
    assert route.count("authorization_type") == 1
    assert "aws_apigatewayv2_authorizer.ai_jwt[0].id" in route
    assert 'route_key = "POST /ai/query"' in route
    assert 'authorizer_type = "JWT"' in authorizer
    assert "var.ai_jwt_issuer" in authorizer
    assert "var.ai_jwt_audience" in authorizer
    assert "$request.header.Authorization" in authorizer
    assert "REQUEST" not in authorizer
    assert "CUSTOM" not in authorizer
    assert "AWS_IAM" not in api_text


def test_protected_route_has_no_authorization_type_none_fallback(api_text: str):
    assert re.search(r"\bNONE\b", api_text) is None
    assert 'authorization_type = "JWT"' in api_text
    assert "authorization_type = var." not in api_text
    assert "authorization_type = local." not in api_text

    get_routes = extract_block(
        api_text,
        'resource "aws_apigatewayv2_route" "routes"',
    )
    assert "authorization_type" not in get_routes
    assert "authorizer_id" not in get_routes


def test_existing_get_routes_stay_on_the_operational_lambda(api_text: str):
    route_keys = route_key_block(api_text)
    routes = extract_block(
        api_text,
        'resource "aws_apigatewayv2_route" "routes"',
    )
    integration = extract_block(
        api_text,
        'resource "aws_apigatewayv2_integration" "lambda"',
    )
    permission = extract_block(
        api_text,
        'resource "aws_lambda_permission" "api_gateway"',
    )

    for route_key in EXPECTED_GET_ROUTES:
        assert f'"{route_key}"' in route_keys
    assert route_keys.count('"GET ') == len(EXPECTED_GET_ROUTES)
    assert "for_each" in routes
    assert "local.route_keys" in routes
    assert "aws_apigatewayv2_integration.lambda.id" in routes
    assert "aws_lambda_function.api.invoke_arn" in integration
    assert "count" not in integration
    assert "aws_lambda_function.api.function_name" in permission
    assert "${aws_apigatewayv2_api.http.execution_arn}/*/*" in permission
    assert "enable_ai_query_route" not in integration
    assert "enable_ai_query_route" not in permission
    assert "enable_ai_query_route" not in routes
    assert api_text.count('resource "aws_apigatewayv2_api"') == 1
    assert api_text.count('resource "aws_lambda_function"') == 1


def test_ai_integration_does_not_call_the_operational_lambda(api_text: str):
    integration = extract_block(
        api_text,
        'resource "aws_apigatewayv2_integration" "ai_query"',
    )

    assert "var.ai_lambda_invoke_arn" in integration
    assert "aws_lambda_function.api" not in integration
    assert 'integration_type   = "AWS_PROXY"' in integration
    assert api_text.count("aws_lambda_function.api.invoke_arn") == 1


def test_ai_lambda_permission_is_scoped_to_post_ai_query(api_text: str):
    unqualified = extract_block(
        api_text,
        'resource "aws_lambda_permission" "ai_query"',
    )
    qualified = extract_block(
        api_text,
        'resource "aws_lambda_permission" "ai_query_qualified"',
    )

    assert (
        'ai_query_source_arn = "${aws_apigatewayv2_api.http.execution_arn}/'
        '${aws_apigatewayv2_stage.default.name}/POST/ai/query"'
    ) in api_text
    for block in (unqualified, qualified):
        assert "local.ai_query_source_arn" in block
        assert "local.ai_lambda_permission_function_name" in block
        assert 'action = (\n    "lambda:InvokeFunction"\n  )' in block
        assert 'principal = (\n    "apigateway.amazonaws.com"\n  )' in block
        assert "aws_lambda_function.api" not in block
        assert "/*/*" not in block
        assert "depends_on = [\n    aws_apigatewayv2_route.ai_query,\n  ]" in block
    assert "var.enable_ai_query_route && !local.ai_lambda_qualified" in unqualified
    assert "var.enable_ai_query_route && local.ai_lambda_qualified" in qualified
    assert "qualifier" not in unqualified
    assert "local.ai_lambda_permission_qualifier" in qualified
    assert "AllowApiGatewayInvokeAiQuery" in unqualified
    assert "AllowApiGatewayInvokeAiQueryQualified" in qualified


def test_no_cognito_or_other_identity_provider_is_provisioned(
    api_text: str,
    variables_text: str,
):
    module_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(MODULE_DIR.glob("*.tf"))
    )

    assert "aws_cognito" not in module_text
    assert "aws_iam_openid_connect_provider" not in module_text
    assert "user_pool" not in module_text
    assert "client_id" not in module_text
    assert 'resource "aws_lambda_function"' not in variables_text
    assert api_text.count('resource "aws_lambda_function"') == 1
    assert 'resource "aws_lambda_function" "api"' in api_text


def test_no_new_administrative_or_write_permissions(api_text: str):
    policy = extract_block(api_text, 'data "aws_iam_policy_document" "lambda"')
    actions = re.findall(r'"([a-z0-9*]+:[A-Za-z*]+)"', policy)

    assert tuple(actions) == OPERATIONAL_IAM_ACTIONS
    assert "lambda:InvokeFunction" not in policy
    assert policy.count('"*"') == 2
    assert "ai_lambda" not in policy
    assert "ai_query" not in policy
    for forbidden in FORBIDDEN_PERMISSIONS:
        assert forbidden not in api_text
    assert api_text.count('resource "aws_iam_role"') == 1
    assert api_text.count('resource "aws_iam_role_policy"') == 1
    assert api_text.count("lambda:InvokeFunction") == 3


def test_post_cors_is_added_only_when_the_ai_route_is_enabled(api_text: str):
    api = extract_block(api_text, 'resource "aws_apigatewayv2_api" "http"')
    methods = api.split("allow_methods = (", 1)[1].split("allow_origins", 1)[0]
    enabled_branch, disabled_branch = methods.split(":", 1)

    assert '"GET"' in enabled_branch
    assert '"OPTIONS"' in enabled_branch
    assert '"POST"' in enabled_branch
    assert '"GET"' in disabled_branch
    assert '"OPTIONS"' in disabled_branch
    assert "POST" not in disabled_branch
    assert "allow_credentials = false" in api
    assert '"authorization"' in api
    assert '"content-type"' in api
    assert "var.cors_allowed_origins" in api
    assert "max_age = 300" in api


def test_qualified_lambda_groups_feed_the_permission_arguments(
    api_text: str,
    patterns: dict[str, str],
):
    function_arn = (
        "arn:aws:lambda:us-west-1:123456789012:function:wilvor-ai-query:live"
    )
    match = re.search(patterns["qualified"], function_arn)
    unqualified = "arn:aws:lambda:us-west-1:123456789012:function:wilvor-ai-query"

    assert match is not None
    assert match.group(1) == unqualified
    assert match.group(2) == "live"
    assert re.search(patterns["qualified"], unqualified) is None
    assert ")[0]" in api_text
    assert ")[1]" in api_text


def test_contract_imports_no_application_runtime_or_aws_client():
    tree = ast.parse(read(Path(__file__)))
    imported: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module.split(".")[0])

    assert imported == ["__future__", "ast", "re", "pathlib", "pytest"]
    module_lines = [
        line.strip()
        for path in MODULE_DIR.glob("*.tf")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert not any(line.startswith("module ") for line in module_lines)
