"""Fail-closed contracts for the gateway-to-Lambda runtime boundary.

These tests do not call AWS, plan Terraform, or verify a JWT signature.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_contracts import MasterProductionAuthority
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_gateway_identity import InvocationProvenanceStanding
from wilvor_ai.master_routing import (
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
)
from wilvor_ai import master_gateway_runtime_boundary as boundary
from wilvor_ai.master_gateway_runtime_boundary import (
    ConfigurationDenialReason,
    ConfigurationRestrictionDecision,
    EvidenceStatus,
    InvokePermissionStatement,
    RejectedInvocationProof,
    RuntimeEvidenceClass,
    RuntimeFactName,
    ServerOwnedGatewayManifest,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
API_TF = REPO_ROOT / "modules" / "operational_api" / "api.tf"
VARIABLES_TF = REPO_ROOT / "modules" / "operational_api" / "variables.tf"
REVIEW = (
    REPO_ROOT
    / "functions"
    / "shared"
    / "wilvor_ai"
    / "SPRINT3B2_GATEWAY_DEPLOYMENT_REVIEW.md"
)

PARTITION = "aws"
REGION = "us-west-1"
ACCOUNT = "123456789012"
API_ID = "abc123def0"
NAME_PREFIX = "wilvor-dev"
ISSUER = "https://idp.wilvor.test/realms/ops"
AUDIENCE = "wilvor-dashboard"
SCOPE = "wilvor.ai.query"
FUNCTION_NAME = "wilvor-ai-query"
LOG_GROUP = "/aws/lambda/wilvor-ai-query"


def build_manifest(
    *,
    enable: bool = True,
    partition: str = PARTITION,
    region: str = REGION,
    account_id: str = ACCOUNT,
    api_id: str = API_ID,
    name_prefix: str = NAME_PREFIX,
    stage_name: str = boundary.REQUIRED_STAGE_NAME,
    route_key: str = boundary.REQUIRED_ROUTE_KEY,
    issuer: str = ISSUER,
    authorizer_issuer: str | None = None,
    audiences: tuple[str, ...] = (AUDIENCE,),
    authorizer_audiences: tuple[str, ...] | None = None,
    scopes: tuple[str, ...] = (SCOPE,),
    function_arn: str | None = None,
    function_name: str = FUNCTION_NAME,
    qualifier: str = "",
    function_region: str | None = None,
    function_account: str | None = None,
    function_partition: str | None = None,
    invoke_arn: str | None = None,
    source_arn: str | None = None,
    principal: str = boundary.REQUIRED_GATEWAY_PRINCIPAL,
    action: str = boundary.REQUIRED_INVOKE_ACTION,
    statement_id: str | None = None,
    permission_function_arn: str | None = None,
    permission_qualifier: str | None = None,
    omit_permissions: bool = False,
    extra_permission: InvokePermissionStatement | None = None,
    identity_policy_invoke_principals: tuple[str, ...] = (),
    additional_invoke_sources: tuple[str, ...] = (),
    function_url_enabled: bool = False,
    ai_log_group_name: str = LOG_GROUP,
    integration_type: str = boundary.REQUIRED_INTEGRATION_TYPE,
    integration_method: str = boundary.REQUIRED_INTEGRATION_METHOD,
    payload_format_version: str = boundary.REQUIRED_PAYLOAD_FORMAT,
    authorization_type: str = boundary.REQUIRED_AUTHORIZATION_TYPE,
    authorizer_type: str = boundary.REQUIRED_AUTHORIZER_TYPE,
    identity_source: str = boundary.REQUIRED_IDENTITY_SOURCE,
) -> ServerOwnedGatewayManifest:
    fn_partition = partition if function_partition is None else function_partition
    fn_region = region if function_region is None else function_region
    fn_account = account_id if function_account is None else function_account
    if function_arn is None:
        function_arn = (
            f"arn:{fn_partition}:lambda:{fn_region}:{fn_account}:function:{function_name}"
        )
        if qualifier:
            function_arn = f"{function_arn}:{qualifier}"
    if invoke_arn is None:
        invoke_arn = boundary.expected_invoke_arn(function_arn)
    if source_arn is None:
        source_arn = boundary.expected_source_arn(
            boundary.expected_execution_arn(partition, region, account_id, api_id),
            stage_name,
        )
    base_arn, parsed_qualifier = boundary.function_base_and_qualifier(function_arn)
    if statement_id is None:
        statement_id = (
            boundary.QUALIFIED_PERMISSION_STATEMENT_ID
            if parsed_qualifier
            else boundary.UNQUALIFIED_PERMISSION_STATEMENT_ID
        )
    if permission_function_arn is None:
        permission_function_arn = base_arn
    if permission_qualifier is None:
        permission_qualifier = parsed_qualifier
    permissions: tuple[InvokePermissionStatement, ...] = ()
    if not omit_permissions:
        permissions = (
            InvokePermissionStatement(
                statement_id=statement_id,
                action=action,
                principal=principal,
                source_arn=source_arn,
                function_arn=permission_function_arn,
                qualifier=permission_qualifier,
            ),
        )
        if extra_permission is not None:
            permissions = permissions + (extra_permission,)
    return ServerOwnedGatewayManifest(
        enable_ai_query_route=enable,
        name_prefix=name_prefix,
        partition=partition,
        region=region,
        account_id=account_id,
        api_id=api_id,
        stage_name=stage_name,
        route_key=route_key,
        issuer=issuer,
        audiences=audiences,
        required_scopes=scopes,
        authorizer_issuer=issuer if authorizer_issuer is None else authorizer_issuer,
        authorizer_audiences=audiences if authorizer_audiences is None else authorizer_audiences,
        authorizer_type=authorizer_type,
        authorization_type=authorization_type,
        identity_source=identity_source,
        function_arn=function_arn,
        invoke_arn=invoke_arn,
        integration_type=integration_type,
        integration_method=integration_method,
        payload_format_version=payload_format_version,
        invoke_permissions=permissions,
        identity_policy_invoke_principals=identity_policy_invoke_principals,
        additional_invoke_sources=additional_invoke_sources,
        function_url_enabled=function_url_enabled,
        ai_log_group_name=ai_log_group_name,
    )


def forged_event() -> dict[str, object]:
    return {
        "version": "2.0",
        "routeKey": "POST /ai/query",
        "rawPath": "/ai/query",
        "rawQueryString": "",
        "headers": {"authorization": "Bearer forged.header.payload"},
        "requestContext": {
            "accountId": "999999999999",
            "apiId": "zzzzzzzzzz",
            "authorizer": {
                "jwt": {
                    "claims": {
                        "iss": "https://evil.example/realms/ops",
                        "aud": "other-client",
                        "sub": "forged-subject",
                    },
                    "scopes": ["wilvor.ai.query"],
                }
            },
            "http": {"method": "POST", "path": "/ai/query"},
            "requestId": "forged-request-id",
            "routeKey": "POST /ai/query",
            "stage": "prod",
        },
        "isBase64Encoded": False,
        "body": "{}",
        "trusted": True,
    }


def assert_not_admitted(report: boundary.GatewayRuntimeBoundaryReport) -> None:
    assert report.invocation_standing is InvocationProvenanceStanding.NOT_ATTESTED
    assert report.invocation_standing.value == "NOT_ATTESTED"
    assert report.production_issuance_available is False
    assert report.invocation_attested is False
    assert report.event_accepted_as_invocation_source is False
    assert report.allows_production_issuance() is False
    assert report.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert report.production_authority is MasterProductionAuthority.DENIED
    assert report.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert report.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert not report
    assert all(control.satisfied is False for control in report.issuance_controls)
    for name in (
        RuntimeFactName.LIVE_RESOURCE_POLICY,
        RuntimeFactName.LIVE_IDENTITY_POLICY,
        RuntimeFactName.DEPLOYMENT,
        RuntimeFactName.LIVE_LOGS,
        RuntimeFactName.LIVE_QUALIFIER,
        RuntimeFactName.LIVE_FUNCTION_URL,
    ):
        fact = report.fact(name)
        assert fact.evidence_class is RuntimeEvidenceClass.NOT_YET_INSPECTED
        assert fact.status is EvidenceStatus.NOT_INSPECTED
        assert fact.accepted_as_invocation_source is False


def test_current_default_route_is_disabled_and_not_attested(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WILVOR_GATEWAY_TRUSTED", "true")
    monkeypatch.setenv("WILVOR_TEST_MODE", "true")
    manifest = boundary.current_disabled_route_manifest(name_prefix=NAME_PREFIX)
    report = boundary.assess_gateway_runtime_boundary(manifest)

    assert manifest.enable_ai_query_route is False
    assert report.configuration_decision is ConfigurationRestrictionDecision.DENIED
    assert report.denial_reasons == (
        ConfigurationDenialReason.ROUTE_DISABLED_BY_DEFAULT,
    )
    assert report.fact(RuntimeFactName.ROUTE_ENABLEMENT).value == "false"
    assert report.fact(RuntimeFactName.ROUTE).status is EvidenceStatus.NOT_ENABLED
    assert report.fact(RuntimeFactName.STAGE).value == "$default"
    assert report.fact(RuntimeFactName.STAGE).evidence_class is (
        RuntimeEvidenceClass.VERIFIED_CONFIGURATION
    )
    assert report.fact(RuntimeFactName.ACCOUNT).evidence_class is (
        RuntimeEvidenceClass.NOT_YET_INSPECTED
    )
    assert report.fact(RuntimeFactName.REGION).evidence_class is (
        RuntimeEvidenceClass.NOT_YET_INSPECTED
    )
    assert report.rejected_proofs == ()
    assert_not_admitted(report)


def test_terraform_source_matches_the_checker_contract():
    api = API_TF.read_text(encoding="utf-8")
    variables = VARIABLES_TF.read_text(encoding="utf-8")
    enable = variables.split('variable "enable_ai_query_route"', 1)[1].split(
        'variable "ai_jwt_issuer"', 1
    )[0]

    assert re.search(r"(?m)^  default\s+=\s+false\s*$", enable)
    assert f'ai_jwt_issuer_pattern = "{boundary.AI_JWT_ISSUER_PATTERN}"' in api
    assert (
        f'ai_lambda_function_arn_pattern = "{boundary.AI_LAMBDA_FUNCTION_ARN_PATTERN}"'
        in api
    )
    assert (
        f'ai_lambda_zero_account_pattern = "{boundary.AI_LAMBDA_ZERO_ACCOUNT_PATTERN}"'
        in api
    )
    assert (
        f'ai_lambda_qualified_arn_pattern = "{boundary.AI_LAMBDA_QUALIFIED_ARN_PATTERN}"'
        in api
    )
    assert boundary.AI_LAMBDA_INVOKE_ARN_PATTERN in variables
    assert 'ai_query_route_key = "POST /ai/query"' in api
    assert 'route_key = "POST /ai/query"' in api
    assert 'name = "$default"' in api
    assert (
        'ai_query_source_arn = "${aws_apigatewayv2_api.http.execution_arn}/'
        '${aws_apigatewayv2_stage.default.name}/POST/ai/query"'
    ) in api
    assert "${aws_apigatewayv2_api.http.execution_arn}/*/*" in api
    assert "POST /ai/query must use a separate AI Lambda, not the operational API Lambda." in api
    assert "var.ai_lambda_invoke_arn" in api
    assert "aws_lambda_function.api.invoke_arn" in api
    assert 'resource "aws_lambda_function" "api"' in api
    assert "authorization_scopes" not in api
    assert boundary.REQUIRED_ROUTE_KEY == "POST /ai/query"
    assert boundary.REQUIRED_STAGE_NAME == "$default"


@pytest.mark.parametrize(
    ("partition", "region", "qualifier"),
    [
        ("aws", "us-west-1", ""),
        ("aws-us-gov", "us-gov-west-1", "live"),
    ],
)
def test_configured_route_syntactic_restrictions_are_not_a_grant(
    partition: str,
    region: str,
    qualifier: str,
):
    manifest = build_manifest(partition=partition, region=region, qualifier=qualifier)
    report = boundary.assess_gateway_runtime_boundary(manifest)
    execution = boundary.expected_execution_arn(partition, region, ACCOUNT, API_ID)
    source = boundary.expected_source_arn(execution, "$default")

    assert report.configuration_decision is ConfigurationRestrictionDecision.RESTRICTED
    assert report.denial_reasons == ()
    assert report.fact(RuntimeFactName.ROUTE).value == "POST /ai/query"
    assert report.fact(RuntimeFactName.STAGE).value == "$default"
    assert report.fact(RuntimeFactName.ISSUER).value == ISSUER
    assert report.fact(RuntimeFactName.AUDIENCE).value == AUDIENCE
    assert report.fact(RuntimeFactName.SCOPE).value == SCOPE
    assert report.fact(RuntimeFactName.REGION).value == region
    assert report.fact(RuntimeFactName.ACCOUNT).value == ACCOUNT
    assert report.fact(RuntimeFactName.QUALIFIER).value == (qualifier or "UNQUALIFIED")
    assert report.fact(RuntimeFactName.INTEGRATION).value == "AWS_PROXY:POST:2.0:JWT"
    assert report.fact(RuntimeFactName.INVOKE_PERMISSION).value == (
        "apigateway.amazonaws.com:lambda:InvokeFunction"
    )
    assert report.fact(RuntimeFactName.RESOURCE_POLICY).value == source
    assert report.fact(RuntimeFactName.LOGS).value == LOG_GROUP
    assert report.fact(RuntimeFactName.OPERATIONAL_SEPARATION).value == "SEPARATE"
    for name in (
        RuntimeFactName.ROUTE,
        RuntimeFactName.STAGE,
        RuntimeFactName.ISSUER,
        RuntimeFactName.AUDIENCE,
        RuntimeFactName.RESOURCE_POLICY,
    ):
        fact = report.fact(name)
        assert fact.evidence_class is RuntimeEvidenceClass.VERIFIED_CONFIGURATION
        assert fact.status is EvidenceStatus.VERIFIED
        assert "*" not in fact.value
    assert source.endswith("/$default/POST/ai/query")
    assert "/*/*" not in source
    assert manifest.invoke_permissions[0].principal == "apigateway.amazonaws.com"
    assert manifest.integration_type == "AWS_PROXY"
    assert manifest.payload_format_version == "2.0"
    assert manifest.authorizer_type == "JWT"
    assert not boundary.targets_operational_lambda(manifest.function_arn, NAME_PREFIX)
    assert_not_admitted(report)
    assert boundary.RuntimeBoundaryLimitation.CONFIGURATION_RESTRICTION_IS_NOT_A_GRANT in (
        report.limitations
    )
    assert (
        boundary.RuntimeBoundaryLimitation.SCOPES_ARE_NOT_EDGE_ENFORCED_BY_CURRENT_TERRAFORM
        in report.limitations
    )
    assert (
        boundary.RuntimeBoundaryLimitation.SAME_ACCOUNT_IAM_INVOKE_IS_AN_ALTERNATE_PATH
        in report.limitations
    )


def test_forged_http_api_event_is_not_an_invocation_source():
    manifest = build_manifest()
    event = forged_event()
    report = boundary.assess_gateway_runtime_boundary(manifest, event)
    rendered = repr(report)
    verified = [
        fact.value
        for fact in report.facts
        if fact.evidence_class is RuntimeEvidenceClass.VERIFIED_CONFIGURATION
    ]

    assert report.configuration_decision is ConfigurationRestrictionDecision.RESTRICTED
    assert report.rejected_proofs == (
        RejectedInvocationProof.FORGED_HTTP_API_EVENT,
        RejectedInvocationProof.DIRECT_INVOKE_MIMIC,
        RejectedInvocationProof.REQUEST_CONTEXT,
        RejectedInvocationProof.REQUEST_ID,
        RejectedInvocationProof.JWT_LOOKING_MATERIAL,
        RejectedInvocationProof.AUTHORIZATION_HEADER,
        RejectedInvocationProof.TRUSTED_FLAG,
    )
    assert report.fact(RuntimeFactName.EVENT_FIELDS).evidence_class is (
        RuntimeEvidenceClass.UNTRUSTED_EVENT_FIELD
    )
    assert report.fact(RuntimeFactName.PER_REQUEST_PROVENANCE).status is (
        EvidenceStatus.UNTRUSTED
    )
    assert report.fact(RuntimeFactName.ISSUER).value == ISSUER
    assert "https://evil.example/realms/ops" not in verified
    assert "forged-request-id" not in verified
    assert "forged.header.payload" not in rendered
    assert "999999999999" not in verified
    assert_not_admitted(report)


def test_forged_event_does_not_enable_the_default_route():
    report = boundary.assess_gateway_runtime_boundary(
        boundary.current_disabled_route_manifest(name_prefix=NAME_PREFIX),
        forged_event(),
    )

    assert report.configuration_decision is ConfigurationRestrictionDecision.DENIED
    assert ConfigurationDenialReason.ROUTE_DISABLED_BY_DEFAULT in report.denial_reasons
    assert RejectedInvocationProof.FORGED_HTTP_API_EVENT in report.rejected_proofs
    assert_not_admitted(report)


def test_direct_invoke_mimic_is_not_gateway_provenance():
    manifest = build_manifest()
    event = {"source": "direct-invoke", "trusted": True, "requestId": "direct-request"}
    context = {
        "aws_request_id": "direct-request",
        "invoked_function_arn": manifest.function_arn,
    }
    report = boundary.assess_gateway_runtime_boundary(
        manifest,
        event,
        lambda_context=context,
        trusted=True,
    )
    verified = " ".join(
        fact.value
        for fact in report.facts
        if fact.evidence_class is RuntimeEvidenceClass.VERIFIED_CONFIGURATION
    )

    assert RejectedInvocationProof.FORGED_HTTP_API_EVENT not in report.rejected_proofs
    assert report.rejected_proofs == (
        RejectedInvocationProof.DIRECT_INVOKE_MIMIC,
        RejectedInvocationProof.REQUEST_ID,
        RejectedInvocationProof.LAMBDA_CONTEXT,
        RejectedInvocationProof.TRUSTED_FLAG,
    )
    assert report.fact(RuntimeFactName.LAMBDA_CONTEXT).evidence_class is (
        RuntimeEvidenceClass.UNTRUSTED_EVENT_FIELD
    )
    assert report.fact(RuntimeFactName.TRUSTED_FLAG).status is EvidenceStatus.UNTRUSTED
    assert "direct-request" not in verified
    assert manifest.function_arn not in report.fact(RuntimeFactName.LAMBDA_CONTEXT).value
    assert_not_admitted(report)


def test_trusted_flag_alone_does_not_attest():
    report = boundary.assess_gateway_runtime_boundary(build_manifest(), trusted=True)

    assert report.configuration_decision is ConfigurationRestrictionDecision.RESTRICTED
    assert report.rejected_proofs == (RejectedInvocationProof.TRUSTED_FLAG,)
    assert report.fact(RuntimeFactName.EVENT_FIELDS).status is EvidenceStatus.NOT_PRESENTED
    assert_not_admitted(report)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"stage_name": "prod"}, ConfigurationDenialReason.WRONG_STAGE),
        ({"route_key": "GET /health"}, ConfigurationDenialReason.WRONG_ROUTE),
        ({"function_account": "210987654321"}, ConfigurationDenialReason.WRONG_ACCOUNT),
        ({"function_region": "us-east-1"}, ConfigurationDenialReason.WRONG_REGION),
        ({"function_partition": "aws-us-gov"}, ConfigurationDenialReason.WRONG_PARTITION),
        (
            {
                "source_arn": boundary.expected_source_arn(
                    boundary.expected_execution_arn(PARTITION, REGION, ACCOUNT, "bbbbbbbbbb"),
                    "$default",
                )
            },
            ConfigurationDenialReason.SOURCE_ARN_MISMATCH,
        ),
        (
            {
                "invoke_arn": boundary.expected_invoke_arn(
                    "arn:aws:lambda:us-west-1:123456789012:function:other-function"
                )
            },
            ConfigurationDenialReason.INVOKE_ARN_MISMATCH,
        ),
        ({"permission_qualifier": "other"}, ConfigurationDenialReason.WRONG_QUALIFIER),
        (
            {"permission_function_arn": "arn:aws:lambda:us-west-1:123456789012:function:other"},
            ConfigurationDenialReason.WRONG_FUNCTION_ARN,
        ),
        ({"statement_id": "AllowEveryone"}, ConfigurationDenialReason.PERMISSION_STATEMENT_MISMATCH),
        ({"payload_format_version": "1.0"}, ConfigurationDenialReason.INTEGRATION_MISMATCH),
        ({"authorizer_type": "REQUEST"}, ConfigurationDenialReason.INTEGRATION_MISMATCH),
        ({"identity_source": "header.Authorization"}, ConfigurationDenialReason.INTEGRATION_MISMATCH),
    ],
)
def test_wrong_stage_arn_account_and_integration_are_denied(
    changes: dict[str, object],
    reason: ConfigurationDenialReason,
):
    report = boundary.assess_gateway_runtime_boundary(build_manifest(**changes))

    assert report.configuration_decision is ConfigurationRestrictionDecision.DENIED
    assert reason in report.denial_reasons
    assert_not_admitted(report)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        (
            {
                "source_arn": boundary.expected_execution_arn(PARTITION, REGION, ACCOUNT, API_ID)
                + "/*/*"
            },
            ConfigurationDenialReason.WILDCARD_INVOKE_PERMISSION,
        ),
        ({"source_arn": "*"}, ConfigurationDenialReason.WILDCARD_INVOKE_PERMISSION),
        ({"action": "lambda:*"}, ConfigurationDenialReason.WILDCARD_INVOKE_PERMISSION),
        ({"principal": "*"}, ConfigurationDenialReason.OTHER_PRINCIPAL_INVOKE),
        (
            {"principal": "lambda.amazonaws.com"},
            ConfigurationDenialReason.OTHER_PRINCIPAL_INVOKE,
        ),
        (
            {"principal": "arn:aws:iam::123456789012:root"},
            ConfigurationDenialReason.OTHER_PRINCIPAL_INVOKE,
        ),
        (
            {"identity_policy_invoke_principals": ("arn:aws:iam::123456789012:role/app",)},
            ConfigurationDenialReason.OTHER_PRINCIPAL_INVOKE,
        ),
        ({"function_url_enabled": True}, ConfigurationDenialReason.OPEN_ALTERNATIVE_INVOCATION_PATH),
        (
            {"additional_invoke_sources": ("eventbridge",)},
            ConfigurationDenialReason.OPEN_ALTERNATIVE_INVOCATION_PATH,
        ),
        ({"omit_permissions": True}, ConfigurationDenialReason.MISSING_EVIDENCE),
    ],
)
def test_other_principals_wildcards_and_open_paths_are_denied(
    changes: dict[str, object],
    reason: ConfigurationDenialReason,
):
    report = boundary.assess_gateway_runtime_boundary(build_manifest(**changes))

    assert report.configuration_decision is ConfigurationRestrictionDecision.DENIED
    assert reason in report.denial_reasons
    if changes.get("principal") == "*":
        assert ConfigurationDenialReason.WILDCARD_INVOKE_PERMISSION in report.denial_reasons
    assert_not_admitted(report)


def test_second_invoke_permission_is_an_open_path():
    first = build_manifest()
    extra = first.invoke_permissions[0]
    report = boundary.assess_gateway_runtime_boundary(
        build_manifest(extra_permission=extra)
    )

    assert ConfigurationDenialReason.OPEN_ALTERNATIVE_INVOCATION_PATH in report.denial_reasons
    assert report.configuration_decision is ConfigurationRestrictionDecision.DENIED
    assert_not_admitted(report)


def test_operational_lambda_and_log_group_reuse_are_denied():
    operational_name = f"{NAME_PREFIX}-operational-api"
    function_report = boundary.assess_gateway_runtime_boundary(
        build_manifest(function_name=operational_name)
    )
    log_report = boundary.assess_gateway_runtime_boundary(
        build_manifest(
            ai_log_group_name=boundary.operational_log_group_name(NAME_PREFIX)
        )
    )

    assert ConfigurationDenialReason.OPERATIONAL_LAMBDA_REUSE in function_report.denial_reasons
    assert ConfigurationDenialReason.LOG_GROUP_COLLISION in log_report.denial_reasons
    assert boundary.targets_operational_lambda(
        f"arn:aws:lambda:us-west-1:123456789012:function:{operational_name}",
        NAME_PREFIX,
    )
    assert boundary.targets_operational_lambda(
        f"arn:aws:lambda:us-west-1:123456789012:function:{operational_name}:live",
        NAME_PREFIX,
    )
    assert_not_admitted(function_report)
    assert_not_admitted(log_report)


def test_issuer_and_audience_mismatch_are_denied():
    issuer_report = boundary.assess_gateway_runtime_boundary(
        build_manifest(authorizer_issuer="https://evil.example/realms/ops")
    )
    audience_report = boundary.assess_gateway_runtime_boundary(
        build_manifest(authorizer_audiences=("other-client",))
    )

    assert ConfigurationDenialReason.IDENTITY_PROVIDER_MISMATCH in issuer_report.denial_reasons
    assert ConfigurationDenialReason.AUDIENCE_MISMATCH in audience_report.denial_reasons
    assert issuer_report.fact(RuntimeFactName.ISSUER).status is EvidenceStatus.REJECTED
    assert audience_report.fact(RuntimeFactName.AUDIENCE).status is EvidenceStatus.REJECTED
    assert_not_admitted(issuer_report)
    assert_not_admitted(audience_report)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"issuer": "http://idp.wilvor.test/realms/ops"}, "invalid_issuer"),
        ({"issuer": "https://idp.wilvor.test/*"}, "invalid_issuer"),
        ({"audiences": ("*",)}, "invalid_audience"),
        ({"audiences": ("wilvor dashboard",)}, "invalid_audience"),
        ({"audiences": ()}, "invalid_audience"),
        ({"scopes": ("*",)}, "invalid_scope"),
        ({"scopes": ()}, "missing_required_scope"),
        (
            {
                "account_id": "000000000000",
                "function_arn": (
                    "arn:aws:lambda:us-west-1:123456789012:function:wilvor-ai-query"
                ),
            },
            "invalid_account",
        ),
        ({"function_account": "000000000000"}, "invalid_function_arn"),
        ({"function_arn": "not-an-arn"}, "invalid_function_arn"),
        ({"api_id": "ABC"}, "invalid_api_id"),
        ({"stage_name": "prod*"}, "invalid_stage"),
    ],
)
def test_malformed_manifest_is_rejected(changes: dict[str, object], code: str):
    with pytest.raises(ContractValidationError) as caught:
        build_manifest(**changes)

    assert code in caught.value.errors


def test_event_dict_is_not_a_server_owned_manifest():
    with pytest.raises(ContractValidationError) as caught:
        boundary.assess_gateway_runtime_boundary(forged_event())

    assert caught.value.errors == ("manifest_not_server_owned",)
    with pytest.raises(ContractValidationError) as caught_object:
        boundary.assess_gateway_runtime_boundary(object())
    assert caught_object.value.errors == ("manifest_not_server_owned",)


def test_runtime_admission_cannot_be_promoted():
    report = boundary.assess_gateway_runtime_boundary(build_manifest())

    with pytest.raises(ContractValidationError) as caught:
        boundary.refuse_runtime_admission(report)
    assert caught.value.errors == (
        "runtime_admission_not_attested",
        "production_issuance_unavailable",
    )
    with pytest.raises(ContractValidationError):
        replace(report, production_authority=MasterProductionAuthority.PRODUCTION)
    with pytest.raises(ContractValidationError):
        replace(report, production_issuance_available=True)
    with pytest.raises(ContractValidationError):
        replace(report, invocation_attested=True)
    with pytest.raises(ContractValidationError):
        replace(report, event_accepted_as_invocation_source=True)
    with pytest.raises(ContractValidationError):
        boundary.RuntimeBoundaryFact(
            RuntimeFactName.EVENT_FIELDS,
            RuntimeEvidenceClass.UNTRUSTED_EVENT_FIELD,
            EvidenceStatus.UNTRUSTED,
            "UNTRUSTED",
            accepted_as_invocation_source=True,
        )
    with pytest.raises(ContractValidationError):
        boundary.IssuanceControl(boundary.ISSUANCE_CONTROL_IDS[0], True, True)
    assert not hasattr(boundary, "issue_application_principal")
    assert not hasattr(boundary, "decode_jwt")
    assert not hasattr(boundary, "verify_jwt")


def test_envelope_fields_are_not_established_and_review_is_published():
    report = boundary.assess_gateway_runtime_boundary(build_manifest())
    review = REVIEW.read_text(encoding="utf-8")

    assert report.envelope_established is False
    assert report.established_envelope_fields == ()
    assert boundary.SHARED_ENVELOPE_FIELDS == (
        "master_request_id",
        "planning_session_id",
        "step_id",
        "invocation_id",
        "specialist",
        "purpose",
        "capability",
        "principal_ref",
        "policy_revision",
        "resource_scope",
        "region_scope",
        "historical_window",
        "decision_target_scope",
        "accepted_at_utc",
        "deadline_utc",
        "idempotency_key",
        "max_model_turns",
        "max_tool_calls",
        "correlation_id",
    )
    assert boundary.DIRECT_INVOKE_FORGERY in review
    for item in boundary.DEPLOYMENT_REVIEW_CHECKLIST:
        assert item in review
    for control_id in boundary.ISSUANCE_CONTROL_IDS:
        assert control_id in review
    assert "NOT_ATTESTED" in review
