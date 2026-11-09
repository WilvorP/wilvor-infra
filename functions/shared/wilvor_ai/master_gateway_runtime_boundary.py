"""Fail-closed review of the gateway-to-Lambda trust boundary.

This module checks a server-owned description of the operational HTTP API
against the Sprint 3B route contract. It does not call AWS, read Terraform
state, verify a JWT, or issue a principal.

Three evidence classes stay distinct:

1. Verified configuration. Values in ``ServerOwnedGatewayManifest`` that
   this checker accepted. They describe intended infrastructure. They are
   not a live ``GetApi``, ``GetPolicy``, or ``GetAlias`` result.
2. AWS deployment facts that are not yet inspected. Resource policy,
   identity-based invoke, deployment, logs, qualifier target, and function
   URL remain in this class on every report.
3. Untrusted Lambda event fields. ``requestContext``, request ids,
   authorizer JWT objects, the Authorization header, Lambda context, and a
   passed-in trusted flag are never an invocation source.

A directly invoked Lambda receives the invoker's JSON as its event.
API Gateway validates a JWT only at the edge, then calls
``lambda:InvokeFunction``. That call is not a signature over the event.
Any principal allowed to invoke the function can submit a forged HTTP API
v2 document. See ``DIRECT_INVOKE_FORGERY``.

``ConfigurationRestrictionDecision.RESTRICTED`` is a descriptive result for
manifest text. It is not a production grant. Runtime admission stays
``InvocationProvenanceStanding.NOT_ATTESTED``. Production issuance stays
unavailable. There is no constructor that records either review as complete.

Shared envelope fields (``SHARED_ENVELOPE_FIELDS``) are not an
authentication token and are not established here. Untrusted event values
are not copied into them.

Cross-module interface, baseline only:

- ``wilvor_ai.master_gateway_identity.InvocationProvenanceStanding`` is the
  invocation standing. The only value is ``NOT_ATTESTED``.
- ``PRODUCTION_GATEWAY_ISSUANCE_AVAILABLE`` and
  ``GATEWAY_INVOCATION_ATTESTATION_AVAILABLE`` stay false. This module does
  not assign them.
- ``issue_application_principal`` remains the identity module's closed
  issuer. This module does not call it and does not replace it.
- ``UnresolvedIngressAttestation`` cannot be marked reviewed. A restricted
  manifest must not be passed to a future issuer as a grant.
- Configuration issuer grammar mirrors ``local.ai_jwt_issuer_pattern`` in
  ``modules/operational_api/api.tf``. Claim parsing stays in
  ``master_gateway_identity`` and is not signature verification.
- Execution, production, source authenticity, and request coverage use the
  existing denied / not-established enums.

The current Terraform default is ``enable_ai_query_route = false``.
``POST /ai/query`` is a separate JWT route and Lambda. Operational GET
routes stay on ``aws_lambda_function.api``. This module does not change
that configuration and does not enable the route.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

import wilvor_ai.master_gateway_identity as gateway_identity
from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_contracts import MasterProductionAuthority
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_routing import (
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
)


MASTER_GATEWAY_RUNTIME_BOUNDARY_SCHEMA_VERSION = (
    "wilvor.ai.master_gateway_runtime_boundary.v1"
)

# There is no constructor that flips these. Rebinding them true makes
# assessment fail closed; it does not create a grant.
RUNTIME_BOUNDARY_ISSUANCE_AVAILABLE = False
RUNTIME_ADMISSION_ATTESTATION_AVAILABLE = False
EVENT_INSPECTION_ESTABLISHES_PROVENANCE = False
UNVERIFIED_JWT_DECODING_IS_AUTHENTICATION = False

REQUIRED_ROUTE_KEY = "POST /ai/query"
REQUIRED_STAGE_NAME = "$default"
REQUIRED_GATEWAY_PRINCIPAL = "apigateway.amazonaws.com"
REQUIRED_INVOKE_ACTION = "lambda:InvokeFunction"
REQUIRED_AUTHORIZER_TYPE = "JWT"
REQUIRED_AUTHORIZATION_TYPE = "JWT"
REQUIRED_IDENTITY_SOURCE = "$request.header.Authorization"
REQUIRED_INTEGRATION_TYPE = "AWS_PROXY"
REQUIRED_INTEGRATION_METHOD = "POST"
REQUIRED_PAYLOAD_FORMAT = "2.0"
UNQUALIFIED_PERMISSION_STATEMENT_ID = "AllowApiGatewayInvokeAiQuery"
QUALIFIED_PERMISSION_STATEMENT_ID = "AllowApiGatewayInvokeAiQueryQualified"
OPERATIONAL_FUNCTION_SUFFIX = "-operational-api"

# Copied from modules/operational_api/api.tf and variables.tf. Tests pin them.
AI_JWT_ISSUER_PATTERN = (
    "^https://[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]"
    "(:[0-9]{1,5})?(/[][A-Za-z0-9._~:/?#@!$&'()+,;=%-]*)?$"
)
AI_LAMBDA_FUNCTION_ARN_PATTERN = (
    "^arn:aws[a-z0-9-]*:lambda:[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+:"
    "[0-9]{12}:function:[A-Za-z0-9-_]{1,64}(?::[A-Za-z0-9-_]+)?$"
)
AI_LAMBDA_ZERO_ACCOUNT_PATTERN = (
    "^arn:aws[a-z0-9-]*:lambda:[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+:0{12}:function:"
)
AI_LAMBDA_QUALIFIED_ARN_PATTERN = (
    "^(arn:aws[a-z0-9-]*:lambda:[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+:"
    "[0-9]{12}:function:[A-Za-z0-9-_]{1,64}):([A-Za-z0-9-_]+)$"
)
AI_LAMBDA_INVOKE_ARN_PATTERN = (
    "^arn:aws[a-z0-9-]*:apigateway:[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+:lambda:"
    "path/2015-03-31/functions/arn:aws[a-z0-9-]*:lambda:"
    "[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+:[0-9]{12}:function:"
    "[A-Za-z0-9-_]{1,64}(?::[A-Za-z0-9-_]+)?/invocations$"
)
AWS_REGION_PATTERN = r"[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+"

SHARED_ENVELOPE_FIELDS = (
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

DIRECT_INVOKE_FORGERY = (
    "A directly invoked Lambda receives the invoker's JSON as the event "
    "argument. API Gateway validates a JWT only at the edge, then calls "
    "lambda:InvokeFunction as apigateway.amazonaws.com. That invoke carries "
    "no signature over the event document. Any principal the function's "
    "resource policy or a same-account identity policy allows to call "
    "lambda:InvokeFunction can submit an HTTP API payload format 2.0 "
    "document with a chosen routeKey, stage, request id, and "
    "requestContext.authorizer.jwt object. The function cannot tell that "
    "document from a gateway invoke by reading the event, the Lambda "
    "context, or a caller-supplied trusted flag. Resource-policy source ARN, "
    "identity-policy invoke rights, and the absence of other invoke paths "
    "have to be reviewed in AWS before production issuance can be considered. "
    "Inspecting the event cannot make that provenance cryptographic."
)

DEPLOYMENT_REVIEW_CHECKLIST = (
    "Confirm Terraform state and every tfvars file leave enable_ai_query_route false. The variable default in modules/operational_api/variables.tf is false, and this checker cannot see a deployed override.",
    "Read the deployed HTTP API with apigatewayv2:GetApi and record its API id, account, region, and execution ARN. A Lambda event cannot supply those facts.",
    "Confirm the deployed stage name is $default. The AI resource-policy source ARN must be the execution ARN plus /$default/POST/ai/query.",
    "Confirm POST /ai/query is absent while the route is disabled. GET routes in local.route_keys stay on aws_lambda_function.api, whose permission source is the execution ARN plus /*/*. That wildcard must not be attached to the AI function.",
    "When the route is enabled, confirm the JWT authorizer issuer and audience are the external identity provider values reviewed for this environment. This module creates neither an identity provider nor an audience. Do not take either value from the request, stage variables, or the event.",
    "Confirm API Gateway validates the JWT signature, issuer, and audience at the edge. Signature validation still does not sign the Lambda event. This repository must not decode a JWT without verification.",
    "Confirm modules/operational_api/api.tf sets no authorization_scopes on POST /ai/query. The current route does not enforce OAuth scopes. Scope strings in requestContext.authorizer.jwt are not a resource policy.",
    "Confirm the AI integration is AWS_PROXY with payload format 2.0 and that its URI is the invoke ARN of the external AI function. The URI must not be aws_lambda_function.api.invoke_arn.",
    "Confirm the AI function name is not {name_prefix}-operational-api, including aliases. The operational log group /aws/lambda/{name_prefix}-operational-api must not be the AI function log group.",
    "Call lambda:GetPolicy on the AI function. Require one statement whose principal is apigateway.amazonaws.com, whose action is lambda:InvokeFunction, and whose source ARN is the execution ARN plus /$default/POST/ai/query. The qualifier must match the reviewed alias, or be empty. Reject any asterisk, any /*/* source, any other principal, and any second statement.",
    "Review same-account identity policies for lambda:InvokeFunction on the AI function. The resource policy lets API Gateway invoke. It does not stop a same-account principal with an identity-based allow from invoking the function and submitting a forged HTTP API v2 event. Require no such allow, or an explicit Deny, before production issuance can be considered.",
    "Confirm the AI function has no function URL and no EventBridge, SNS, S3, or cross-account invoke permission.",
    "If the function ARN is qualified, resolve the alias or version and confirm it points at the reviewed code. This checker cannot see a later alias retarget.",
    "Confirm the AI function log group exists, is distinct from the operational log group, and has a reviewed retention. This Terraform module creates a log group only for the operational function.",
    "Use CloudTrail, not the event request id, before treating a past invoke as an API Gateway invoke. Lambda data events are not configured by this module.",
    "Do not treat requestContext, authorizer.jwt, the Authorization header, Lambda context, or a trusted flag as invocation provenance. Production principal issuance remains unavailable. There is no setter for that decision in this module.",
)

ISSUANCE_CONTROL_IDS = (
    "resource_policy_scoped_to_post_ai_query",
    "no_wildcard_invoke_permission",
    "no_other_invoke_principal",
    "same_account_identity_policy_closed",
    "integration_targets_external_ai_lambda",
    "jwt_authorizer_issuer_and_audience",
    "stage_default_and_route_post_ai_query",
    "qualifier_resolved_to_reviewed_code",
    "operational_lambda_not_reused",
    "ai_log_group_separate_and_reviewed",
    "no_function_url_or_async_invoke_source",
    "live_deployment_inspected",
)

_ISSUER = re.compile(AI_JWT_ISSUER_PATTERN)
_FUNCTION = re.compile(AI_LAMBDA_FUNCTION_ARN_PATTERN)
_ZERO_ACCOUNT = re.compile(AI_LAMBDA_ZERO_ACCOUNT_PATTERN)
_QUALIFIED = re.compile(AI_LAMBDA_QUALIFIED_ARN_PATTERN)
_INVOKE = re.compile(AI_LAMBDA_INVOKE_ARN_PATTERN)
_REGION = re.compile(rf"^{AWS_REGION_PATTERN}$")
_PARTITION = re.compile(r"^aws[a-z0-9-]*$")
_ACCOUNT = re.compile(r"^[0-9]{12}$")
_API_ID = re.compile(r"^[a-z0-9]{1,16}$")
_STAGE = re.compile(r"^[A-Za-z0-9$_-]{1,64}$")
_ROUTE = re.compile(
    r"^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS) /[A-Za-z0-9._~/-]{1,128}$"
)
_NAME = re.compile(r"^[a-z][a-z0-9-]{0,48}$")
_AUDIENCE = re.compile(r"^[A-Za-z0-9._~:/?#@!$&'()+,;=%-]{1,128}$")
_SCOPE = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
_LOG_GROUP = re.compile(r"^/aws/lambda/[A-Za-z0-9][A-Za-z0-9_.-]{0,200}$")
_STATEMENT = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,80}$")
_ACTION = re.compile(r"^[A-Za-z0-9:*_-]{1,64}$")
_QUALIFIER_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TOKEN = re.compile(r"^[A-Z_]{1,32}$")
_METHOD = re.compile(r"^[A-Z]{3,10}$")
_PAYLOAD = re.compile(r"^[0-9.]{1,8}$")
_IDENTITY_SOURCE = re.compile(r"^[$A-Za-z0-9._-]{1,80}$")
_SOURCE = re.compile(r"^[A-Za-z0-9:/*._$=-]{1,512}$")
_SERVICE_PRINCIPAL = re.compile(r"^[a-z0-9.-]{1,128}$")
_INVOKE_SOURCE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_EXECUTION = re.compile(
    rf"^arn:aws[a-z0-9-]*:execute-api:{AWS_REGION_PATTERN}:[0-9]{{12}}:[a-z0-9]{{1,16}}$"
)

_MAX_TEXT = 512
_ZERO_ACCOUNT_ID = "000000000000"


class RuntimeEvidenceClass(str, Enum):
    """Where a reported value came from. These are not interchangeable."""

    VERIFIED_CONFIGURATION = "VERIFIED_CONFIGURATION"
    NOT_YET_INSPECTED = "NOT_YET_INSPECTED"
    UNTRUSTED_EVENT_FIELD = "UNTRUSTED_EVENT_FIELD"


class EvidenceStatus(str, Enum):
    """Outcome of one fact. VERIFIED is not a production grant."""

    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    NOT_ENABLED = "NOT_ENABLED"
    NOT_INSPECTED = "NOT_INSPECTED"
    UNTRUSTED = "UNTRUSTED"
    NOT_PRESENTED = "NOT_PRESENTED"


class ConfigurationRestrictionDecision(str, Enum):
    """Descriptive manifest result. RESTRICTED is not admission."""

    DENIED = "DENIED"
    RESTRICTED = "RESTRICTED"


class ConfigurationDenialReason(str, Enum):
    """Why a manifest is not a restricted configuration."""

    ROUTE_DISABLED_BY_DEFAULT = "ROUTE_DISABLED_BY_DEFAULT"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    OPEN_ALTERNATIVE_INVOCATION_PATH = "OPEN_ALTERNATIVE_INVOCATION_PATH"
    IDENTITY_PROVIDER_MISMATCH = "IDENTITY_PROVIDER_MISMATCH"
    AUDIENCE_MISMATCH = "AUDIENCE_MISMATCH"
    WRONG_STAGE = "WRONG_STAGE"
    WRONG_ROUTE = "WRONG_ROUTE"
    WRONG_ACCOUNT = "WRONG_ACCOUNT"
    WRONG_REGION = "WRONG_REGION"
    WRONG_PARTITION = "WRONG_PARTITION"
    WRONG_FUNCTION_ARN = "WRONG_FUNCTION_ARN"
    WRONG_QUALIFIER = "WRONG_QUALIFIER"
    WILDCARD_INVOKE_PERMISSION = "WILDCARD_INVOKE_PERMISSION"
    OTHER_PRINCIPAL_INVOKE = "OTHER_PRINCIPAL_INVOKE"
    OPERATIONAL_LAMBDA_REUSE = "OPERATIONAL_LAMBDA_REUSE"
    LOG_GROUP_COLLISION = "LOG_GROUP_COLLISION"
    INTEGRATION_MISMATCH = "INTEGRATION_MISMATCH"
    INVOKE_ARN_MISMATCH = "INVOKE_ARN_MISMATCH"
    SOURCE_ARN_MISMATCH = "SOURCE_ARN_MISMATCH"
    PERMISSION_STATEMENT_MISMATCH = "PERMISSION_STATEMENT_MISMATCH"


class RuntimeFactName(str, Enum):
    """Facts present on every report, in this order."""

    ROUTE_ENABLEMENT = "ROUTE_ENABLEMENT"
    ROUTE = "ROUTE"
    STAGE = "STAGE"
    ISSUER = "ISSUER"
    AUDIENCE = "AUDIENCE"
    SCOPE = "SCOPE"
    REGION = "REGION"
    ACCOUNT = "ACCOUNT"
    QUALIFIER = "QUALIFIER"
    INTEGRATION = "INTEGRATION"
    INVOKE_PERMISSION = "INVOKE_PERMISSION"
    RESOURCE_POLICY = "RESOURCE_POLICY"
    LIVE_RESOURCE_POLICY = "LIVE_RESOURCE_POLICY"
    LIVE_IDENTITY_POLICY = "LIVE_IDENTITY_POLICY"
    DEPLOYMENT = "DEPLOYMENT"
    LOGS = "LOGS"
    LIVE_LOGS = "LIVE_LOGS"
    LIVE_QUALIFIER = "LIVE_QUALIFIER"
    LIVE_FUNCTION_URL = "LIVE_FUNCTION_URL"
    OPERATIONAL_SEPARATION = "OPERATIONAL_SEPARATION"
    PER_REQUEST_PROVENANCE = "PER_REQUEST_PROVENANCE"
    EVENT_FIELDS = "EVENT_FIELDS"
    LAMBDA_CONTEXT = "LAMBDA_CONTEXT"
    TRUSTED_FLAG = "TRUSTED_FLAG"


class RejectedInvocationProof(str, Enum):
    """Caller-supplied material that was not accepted as an invoke source."""

    FORGED_HTTP_API_EVENT = "FORGED_HTTP_API_EVENT"
    DIRECT_INVOKE_MIMIC = "DIRECT_INVOKE_MIMIC"
    REQUEST_CONTEXT = "REQUEST_CONTEXT"
    REQUEST_ID = "REQUEST_ID"
    JWT_LOOKING_MATERIAL = "JWT_LOOKING_MATERIAL"
    AUTHORIZATION_HEADER = "AUTHORIZATION_HEADER"
    LAMBDA_CONTEXT = "LAMBDA_CONTEXT"
    TRUSTED_FLAG = "TRUSTED_FLAG"


class RuntimeBoundaryLimitation(str, Enum):
    """Standing limits. The full set is present on every report."""

    CONFIGURATION_RESTRICTION_IS_NOT_A_GRANT = (
        "CONFIGURATION_RESTRICTION_IS_NOT_A_GRANT"
    )
    EVENT_IS_NOT_INVOCATION_PROVENANCE = "EVENT_IS_NOT_INVOCATION_PROVENANCE"
    REQUEST_CONTEXT_IS_NOT_INVOCATION_PROVENANCE = (
        "REQUEST_CONTEXT_IS_NOT_INVOCATION_PROVENANCE"
    )
    REQUEST_ID_IS_NOT_INVOCATION_PROVENANCE = (
        "REQUEST_ID_IS_NOT_INVOCATION_PROVENANCE"
    )
    JWT_CLAIMS_ARE_NOT_INVOCATION_PROVENANCE = (
        "JWT_CLAIMS_ARE_NOT_INVOCATION_PROVENANCE"
    )
    LAMBDA_CONTEXT_IS_NOT_INVOCATION_PROVENANCE = (
        "LAMBDA_CONTEXT_IS_NOT_INVOCATION_PROVENANCE"
    )
    TRUSTED_FLAG_IS_NOT_INVOCATION_PROVENANCE = (
        "TRUSTED_FLAG_IS_NOT_INVOCATION_PROVENANCE"
    )
    DIRECT_INVOKE_CAN_FORGE_HTTP_API_EVENT = "DIRECT_INVOKE_CAN_FORGE_HTTP_API_EVENT"
    AUTHORIZATION_HEADER_IS_NOT_DECODED = "AUTHORIZATION_HEADER_IS_NOT_DECODED"
    SCOPES_ARE_NOT_EDGE_ENFORCED_BY_CURRENT_TERRAFORM = (
        "SCOPES_ARE_NOT_EDGE_ENFORCED_BY_CURRENT_TERRAFORM"
    )
    LIVE_AWS_DEPLOYMENT_NOT_INSPECTED = "LIVE_AWS_DEPLOYMENT_NOT_INSPECTED"
    RESOURCE_POLICY_NOT_INSPECTED = "RESOURCE_POLICY_NOT_INSPECTED"
    IDENTITY_POLICY_NOT_INSPECTED = "IDENTITY_POLICY_NOT_INSPECTED"
    LOGS_NOT_INSPECTED = "LOGS_NOT_INSPECTED"
    QUALIFIER_TARGET_NOT_INSPECTED = "QUALIFIER_TARGET_NOT_INSPECTED"
    SAME_ACCOUNT_IAM_INVOKE_IS_AN_ALTERNATE_PATH = (
        "SAME_ACCOUNT_IAM_INVOKE_IS_AN_ALTERNATE_PATH"
    )
    INVOCATION_PROVENANCE_NOT_ATTESTED = "INVOCATION_PROVENANCE_NOT_ATTESTED"
    PRODUCTION_ISSUANCE_UNAVAILABLE = "PRODUCTION_ISSUANCE_UNAVAILABLE"
    EXECUTION_AUTHORITY_REMAINS_DENIED = "EXECUTION_AUTHORITY_REMAINS_DENIED"
    PRODUCTION_AUTHORITY_REMAINS_DENIED = "PRODUCTION_AUTHORITY_REMAINS_DENIED"
    SOURCE_AUTHENTICITY_REMAINS_NOT_ESTABLISHED = (
        "SOURCE_AUTHENTICITY_REMAINS_NOT_ESTABLISHED"
    )
    REQUEST_COVERAGE_REMAINS_NOT_ESTABLISHED = (
        "REQUEST_COVERAGE_REMAINS_NOT_ESTABLISHED"
    )
    ENVELOPE_FIELDS_NOT_ESTABLISHED = "ENVELOPE_FIELDS_NOT_ESTABLISHED"


RUNTIME_BOUNDARY_LIMITATIONS = tuple(RuntimeBoundaryLimitation)

_LIVE_FACTS = frozenset(
    {
        RuntimeFactName.LIVE_RESOURCE_POLICY,
        RuntimeFactName.LIVE_IDENTITY_POLICY,
        RuntimeFactName.DEPLOYMENT,
        RuntimeFactName.LIVE_LOGS,
        RuntimeFactName.LIVE_QUALIFIER,
        RuntimeFactName.LIVE_FUNCTION_URL,
    }
)
_UNTRUSTED_FACTS = frozenset(
    {
        RuntimeFactName.PER_REQUEST_PROVENANCE,
        RuntimeFactName.EVENT_FIELDS,
        RuntimeFactName.LAMBDA_CONTEXT,
        RuntimeFactName.TRUSTED_FLAG,
    }
)
_RESTRICTED_FACTS = frozenset(
    {
        RuntimeFactName.ROUTE_ENABLEMENT,
        RuntimeFactName.ROUTE,
        RuntimeFactName.STAGE,
        RuntimeFactName.ISSUER,
        RuntimeFactName.AUDIENCE,
        RuntimeFactName.SCOPE,
        RuntimeFactName.REGION,
        RuntimeFactName.ACCOUNT,
        RuntimeFactName.QUALIFIER,
        RuntimeFactName.INTEGRATION,
        RuntimeFactName.INVOKE_PERMISSION,
        RuntimeFactName.RESOURCE_POLICY,
        RuntimeFactName.LOGS,
        RuntimeFactName.OPERATIONAL_SEPARATION,
    }
)
_FACT_STATES = {
    (RuntimeEvidenceClass.VERIFIED_CONFIGURATION, EvidenceStatus.VERIFIED),
    (RuntimeEvidenceClass.VERIFIED_CONFIGURATION, EvidenceStatus.REJECTED),
    (RuntimeEvidenceClass.VERIFIED_CONFIGURATION, EvidenceStatus.NOT_ENABLED),
    (RuntimeEvidenceClass.NOT_YET_INSPECTED, EvidenceStatus.NOT_INSPECTED),
    (RuntimeEvidenceClass.NOT_YET_INSPECTED, EvidenceStatus.NOT_PRESENTED),
    (RuntimeEvidenceClass.UNTRUSTED_EVENT_FIELD, EvidenceStatus.UNTRUSTED),
}
_STATEMENT_PROBLEMS = frozenset(
    {
        ConfigurationDenialReason.MISSING_EVIDENCE,
        ConfigurationDenialReason.OPEN_ALTERNATIVE_INVOCATION_PATH,
        ConfigurationDenialReason.WILDCARD_INVOKE_PERMISSION,
        ConfigurationDenialReason.OTHER_PRINCIPAL_INVOKE,
        ConfigurationDenialReason.WRONG_QUALIFIER,
        ConfigurationDenialReason.WRONG_FUNCTION_ARN,
        ConfigurationDenialReason.SOURCE_ARN_MISMATCH,
        ConfigurationDenialReason.PERMISSION_STATEMENT_MISMATCH,
        ConfigurationDenialReason.INTEGRATION_MISMATCH,
    }
)


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _false(value: object, code: str) -> None:
    if value is not False:
        _fail(code)


def _closed_constants() -> None:
    if RUNTIME_BOUNDARY_ISSUANCE_AVAILABLE is not False:
        _fail("production_issuance_unavailable")
    if RUNTIME_ADMISSION_ATTESTATION_AVAILABLE is not False:
        _fail("runtime_admission_not_attested")
    if EVENT_INSPECTION_ESTABLISHES_PROVENANCE is not False:
        _fail("runtime_admission_not_attested")
    if UNVERIFIED_JWT_DECODING_IS_AUTHENTICATION is not False:
        _fail("unverified_jwt_not_authentication")
    if gateway_identity.PRODUCTION_GATEWAY_ISSUANCE_AVAILABLE is not False:
        _fail("production_issuance_unavailable")
    if gateway_identity.GATEWAY_INVOCATION_ATTESTATION_AVAILABLE is not False:
        _fail("runtime_admission_not_attested")
    if gateway_identity.UNVERIFIED_JWT_DECODING_IS_AUTHENTICATION is not False:
        _fail("unverified_jwt_not_authentication")


def _no_controls(value: str, code: str) -> None:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _fail(code)


def _text(value: object, code: str, *, maximum: int = _MAX_TEXT) -> str:
    if type(value) is not str or len(value) > maximum:
        _fail(code)
    _no_controls(value, code)
    return value


def _bool(value: object, code: str) -> bool:
    if type(value) is not bool:
        _fail(code)
    return value


def _pattern(value: str, compiled: re.Pattern[str], code: str) -> None:
    if compiled.fullmatch(value) is None:
        _fail(code)


def _optional(value: str, compiled: re.Pattern[str], code: str) -> None:
    if value != "":
        _pattern(value, compiled, code)


def _string_tuple(value: object, code: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        _fail(code)
    items: list[str] = []
    for item in value:
        items.append(_text(item, code, maximum=256))
    return tuple(items)


def _function_arn(value: str, *, required: bool) -> None:
    if value == "":
        if required:
            _fail("invalid_function_arn")
        return
    _pattern(value, _FUNCTION, "invalid_function_arn")
    if _ZERO_ACCOUNT.search(value) is not None:
        _fail("invalid_function_arn")


def _account(value: str, *, required: bool) -> None:
    if value == "":
        if required:
            _fail("invalid_account")
        return
    _pattern(value, _ACCOUNT, "invalid_account")
    if value == _ZERO_ACCOUNT_ID:
        _fail("invalid_account")


def _audiences(value: object, *, required: bool) -> tuple[str, ...]:
    items = _string_tuple(value, "invalid_audience")
    if required and not items:
        _fail("invalid_audience")
    if len(items) > 8 or len(set(items)) != len(items):
        _fail("invalid_audience")
    for item in items:
        _pattern(item, _AUDIENCE, "invalid_audience")
        if "*" in item:
            _fail("invalid_audience")
    return items


def _scopes(value: object, *, required: bool) -> tuple[str, ...]:
    items = _string_tuple(value, "invalid_scope")
    if required and not items:
        _fail("missing_required_scope")
    if len(items) > 8 or len(set(items)) != len(items):
        _fail("invalid_scope")
    for item in items:
        _pattern(item, _SCOPE, "invalid_scope")
        if item == "*":
            _fail("invalid_scope")
    return items


def _principal(value: object) -> str:
    text = _text(value, "invalid_principal", maximum=256)
    if text == "" or any(character.isspace() for character in text):
        _fail("invalid_principal")
    if text == "*":
        return text
    if text.startswith("arn:"):
        return text
    _pattern(text, _SERVICE_PRINCIPAL, "invalid_principal")
    return text


def expected_execution_arn(
    partition: str,
    region: str,
    account_id: str,
    api_id: str,
) -> str:
    """Execution ARN for an HTTP API. This does not read AWS."""

    _pattern(_text(partition, "invalid_partition", maximum=32), _PARTITION, "invalid_partition")
    _pattern(_text(region, "invalid_region"), _REGION, "invalid_region")
    _account(_text(account_id, "invalid_account", maximum=12), required=True)
    _pattern(_text(api_id, "invalid_api_id", maximum=16), _API_ID, "invalid_api_id")
    arn = f"arn:{partition}:execute-api:{region}:{account_id}:{api_id}"
    _pattern(arn, _EXECUTION, "invalid_api_id")
    return arn


def expected_source_arn(execution_arn: str, stage_name: str) -> str:
    """Resource-policy source ARN for one stage and ``POST /ai/query``."""

    _pattern(_text(execution_arn, "invalid_api_id"), _EXECUTION, "invalid_api_id")
    _pattern(_text(stage_name, "invalid_stage", maximum=64), _STAGE, "invalid_stage")
    if "*" in execution_arn or "*" in stage_name:
        _fail("invalid_stage")
    return f"{execution_arn}/{stage_name}/POST/ai/query"


def expected_invoke_arn(function_arn: str) -> str:
    """API Gateway Lambda invoke URI for one function ARN."""

    _function_arn(_text(function_arn, "invalid_function_arn"), required=True)
    parts = function_arn.split(":")
    arn = (
        f"arn:{parts[1]}:apigateway:{parts[3]}:lambda:path/"
        f"2015-03-31/functions/{function_arn}/invocations"
    )
    _pattern(arn, _INVOKE, "invalid_invoke_arn")
    return arn


def operational_log_group_name(name_prefix: str) -> str:
    """Log group Terraform creates for the operational function only."""

    _pattern(_text(name_prefix, "invalid_name_prefix", maximum=49), _NAME, "invalid_name_prefix")
    return f"/aws/lambda/{name_prefix}{OPERATIONAL_FUNCTION_SUFFIX}"


def targets_operational_lambda(function_arn: str, name_prefix: str) -> bool:
    """Same separation check as ``local.ai_lambda_targets_operational_api``."""

    marker = f":function:{name_prefix}{OPERATIONAL_FUNCTION_SUFFIX}"
    return function_arn.endswith(marker) or f"{marker}:" in function_arn


def function_base_and_qualifier(function_arn: str) -> tuple[str, str]:
    """Split a qualified function ARN the way the Terraform permission does."""

    _function_arn(function_arn, required=True)
    qualified = _QUALIFIED.fullmatch(function_arn)
    if qualified is None:
        return function_arn, ""
    return qualified.group(1), qualified.group(2)


@dataclass(frozen=True)
class InvokePermissionStatement:
    """One intended Lambda resource-policy statement. Not a ``GetPolicy`` result."""

    statement_id: str
    action: str
    principal: str
    source_arn: str
    function_arn: str
    qualifier: str

    def __post_init__(self) -> None:
        _pattern(_text(self.statement_id, "invalid_permission", maximum=81), _STATEMENT, "invalid_permission")
        _pattern(_text(self.action, "invalid_permission", maximum=64), _ACTION, "invalid_permission")
        _principal(self.principal)
        source = _text(self.source_arn, "invalid_permission")
        if _SOURCE.fullmatch(source) is None or (source != "*" and not source.startswith("arn:")):
            _fail("invalid_permission")
        _function_arn(_text(self.function_arn, "invalid_function_arn"), required=True)
        qualifier = _text(self.qualifier, "invalid_permission", maximum=128)
        if qualifier != "":
            _pattern(qualifier, _QUALIFIER_TOKEN, "invalid_permission")


@dataclass(frozen=True)
class ServerOwnedGatewayManifest:
    """Server-owned route description. Do not build this from a Lambda event."""

    enable_ai_query_route: bool
    name_prefix: str
    partition: str
    region: str
    account_id: str
    api_id: str
    stage_name: str
    route_key: str
    issuer: str
    audiences: tuple[str, ...]
    required_scopes: tuple[str, ...]
    authorizer_issuer: str
    authorizer_audiences: tuple[str, ...]
    authorizer_type: str
    authorization_type: str
    identity_source: str
    function_arn: str
    invoke_arn: str
    integration_type: str
    integration_method: str
    payload_format_version: str
    invoke_permissions: tuple[InvokePermissionStatement, ...]
    identity_policy_invoke_principals: tuple[str, ...]
    additional_invoke_sources: tuple[str, ...]
    function_url_enabled: bool
    ai_log_group_name: str

    def __post_init__(self) -> None:
        enabled = _bool(self.enable_ai_query_route, "invalid_bool")
        _pattern(
            _text(self.name_prefix, "invalid_name_prefix", maximum=49),
            _NAME,
            "invalid_name_prefix",
        )
        _optional(_text(self.partition, "invalid_partition", maximum=32), _PARTITION, "invalid_partition")
        _optional(_text(self.region, "invalid_region"), _REGION, "invalid_region")
        _account(_text(self.account_id, "invalid_account", maximum=12), required=enabled)
        _optional(_text(self.api_id, "invalid_api_id", maximum=16), _API_ID, "invalid_api_id")
        _optional(_text(self.stage_name, "invalid_stage", maximum=64), _STAGE, "invalid_stage")
        _optional(_text(self.route_key, "invalid_route"), _ROUTE, "invalid_route")
        _optional(_text(self.issuer, "invalid_issuer"), _ISSUER, "invalid_issuer")
        _optional(_text(self.authorizer_issuer, "invalid_issuer"), _ISSUER, "invalid_issuer")
        _audiences(self.audiences, required=enabled)
        _audiences(self.authorizer_audiences, required=enabled)
        _scopes(self.required_scopes, required=enabled)
        _optional(_text(self.authorizer_type, "invalid_authorizer", maximum=32), _TOKEN, "invalid_authorizer")
        _optional(
            _text(self.authorization_type, "invalid_authorizer", maximum=32),
            _TOKEN,
            "invalid_authorizer",
        )
        _optional(
            _text(self.identity_source, "invalid_authorizer", maximum=80),
            _IDENTITY_SOURCE,
            "invalid_authorizer",
        )
        _function_arn(_text(self.function_arn, "invalid_function_arn"), required=enabled)
        invoke = _text(self.invoke_arn, "invalid_invoke_arn")
        if invoke != "":
            _pattern(invoke, _INVOKE, "invalid_invoke_arn")
        elif enabled:
            _fail("invalid_invoke_arn")
        _optional(_text(self.integration_type, "invalid_integration", maximum=32), _TOKEN, "invalid_integration")
        _optional(
            _text(self.integration_method, "invalid_integration", maximum=10),
            _METHOD,
            "invalid_integration",
        )
        _optional(
            _text(self.payload_format_version, "invalid_integration", maximum=8),
            _PAYLOAD,
            "invalid_integration",
        )
        if type(self.invoke_permissions) is not tuple:
            _fail("invalid_permission")
        if len(self.invoke_permissions) > 8:
            _fail("invalid_permission")
        for permission in self.invoke_permissions:
            if type(permission) is not InvokePermissionStatement:
                _fail("invalid_permission")
        principals = _string_tuple(
            self.identity_policy_invoke_principals,
            "invalid_principal",
        )
        if len(principals) > 8:
            _fail("invalid_principal")
        for principal in principals:
            _principal(principal)
        sources = _string_tuple(self.additional_invoke_sources, "invalid_invoke_source")
        if len(sources) > 8 or len(set(sources)) != len(sources):
            _fail("invalid_invoke_source")
        for source in sources:
            _pattern(source, _INVOKE_SOURCE, "invalid_invoke_source")
        _bool(self.function_url_enabled, "invalid_bool")
        _optional(_text(self.ai_log_group_name, "invalid_log_group"), _LOG_GROUP, "invalid_log_group")
        if enabled:
            for field, code in (
                (self.partition, "invalid_partition"),
                (self.region, "invalid_region"),
                (self.api_id, "invalid_api_id"),
                (self.stage_name, "invalid_stage"),
                (self.route_key, "invalid_route"),
                (self.issuer, "invalid_issuer"),
                (self.authorizer_issuer, "invalid_issuer"),
                (self.authorizer_type, "invalid_authorizer"),
                (self.authorization_type, "invalid_authorizer"),
                (self.identity_source, "invalid_authorizer"),
                (self.integration_type, "invalid_integration"),
                (self.integration_method, "invalid_integration"),
                (self.payload_format_version, "invalid_integration"),
                (self.ai_log_group_name, "invalid_log_group"),
            ):
                if field == "":
                    _fail(code)


@dataclass(frozen=True)
class RuntimeBoundaryFact:
    """One report row. ``accepted_as_invocation_source`` cannot be true."""

    name: RuntimeFactName
    evidence_class: RuntimeEvidenceClass
    status: EvidenceStatus
    value: str
    accepted_as_invocation_source: bool = False

    def __post_init__(self) -> None:
        if type(self.name) is not RuntimeFactName:
            _fail("invalid_report")
        if type(self.evidence_class) is not RuntimeEvidenceClass:
            _fail("invalid_report")
        if type(self.status) is not EvidenceStatus:
            _fail("invalid_report")
        if (self.evidence_class, self.status) not in _FACT_STATES:
            _fail("invalid_report")
        _text(self.value, "invalid_report")
        if self.value == "":
            _fail("invalid_report")
        _false(self.accepted_as_invocation_source, "event_field_not_verified")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class IssuanceControl:
    """A live control that must be reviewed before issuance. Never satisfied here."""

    control_id: str
    required_before_issuance: bool
    satisfied: bool

    def __post_init__(self) -> None:
        if type(self.control_id) is not str or self.control_id not in ISSUANCE_CONTROL_IDS:
            _fail("invalid_issuance_control")
        if self.required_before_issuance is not True:
            _fail("invalid_issuance_control")
        _false(self.satisfied, "issuance_control_not_satisfied")

    def __bool__(self) -> bool:
        return False


def _issuance_controls() -> tuple[IssuanceControl, ...]:
    return tuple(
        IssuanceControl(control_id, True, False) for control_id in ISSUANCE_CONTROL_IDS
    )


@dataclass(frozen=True)
class GatewayRuntimeBoundaryReport:
    """Typed boundary review. Truth value is never runtime admission."""

    configuration_decision: ConfigurationRestrictionDecision
    invocation_standing: gateway_identity.InvocationProvenanceStanding
    denial_reasons: tuple[ConfigurationDenialReason, ...]
    facts: tuple[RuntimeBoundaryFact, ...]
    rejected_proofs: tuple[RejectedInvocationProof, ...]
    issuance_controls: tuple[IssuanceControl, ...]
    deployment_review_checklist: tuple[str, ...]
    limitations: tuple[RuntimeBoundaryLimitation, ...]
    direct_invoke_forgery: str
    execution_authority: MasterRouteExecutionAuthority
    production_authority: MasterProductionAuthority
    source_authenticity: MasterSourceAuthenticity
    request_coverage: MasterRequestCoverage
    production_issuance_available: bool
    invocation_attested: bool
    event_accepted_as_invocation_source: bool
    envelope_established: bool
    established_envelope_fields: tuple[str, ...]
    schema_version: str = MASTER_GATEWAY_RUNTIME_BOUNDARY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _closed_constants()
        if type(self.configuration_decision) is not ConfigurationRestrictionDecision:
            _fail("invalid_report")
        if (
            self.invocation_standing
            is not gateway_identity.InvocationProvenanceStanding.NOT_ATTESTED
        ):
            _fail("runtime_admission_not_attested")
        if type(self.denial_reasons) is not tuple or any(
            type(reason) is not ConfigurationDenialReason for reason in self.denial_reasons
        ):
            _fail("invalid_report")
        if self.denial_reasons != tuple(
            reason for reason in ConfigurationDenialReason if reason in self.denial_reasons
        ):
            _fail("invalid_report")
        if self.configuration_decision is ConfigurationRestrictionDecision.RESTRICTED:
            if self.denial_reasons:
                _fail("invalid_report")
        elif self.configuration_decision is ConfigurationRestrictionDecision.DENIED:
            if not self.denial_reasons:
                _fail("invalid_report")
        else:
            _fail("invalid_report")
        if type(self.facts) is not tuple or tuple(
            fact.name for fact in self.facts
        ) != tuple(RuntimeFactName):
            _fail("invalid_report")
        for fact in self.facts:
            if type(fact) is not RuntimeBoundaryFact:
                _fail("invalid_report")
            if fact.name in _LIVE_FACTS and (
                fact.evidence_class is not RuntimeEvidenceClass.NOT_YET_INSPECTED
                or fact.status is not EvidenceStatus.NOT_INSPECTED
            ):
                _fail("live_aws_fact_not_inspected")
            if fact.name in _UNTRUSTED_FACTS and (
                fact.evidence_class is RuntimeEvidenceClass.VERIFIED_CONFIGURATION
                or fact.status is EvidenceStatus.VERIFIED
            ):
                _fail("event_field_not_verified")
            if (
                self.configuration_decision is ConfigurationRestrictionDecision.RESTRICTED
                and fact.name in _RESTRICTED_FACTS
                and fact.status is not EvidenceStatus.VERIFIED
            ):
                _fail("invalid_report")
        if type(self.rejected_proofs) is not tuple or self.rejected_proofs != tuple(
            proof for proof in RejectedInvocationProof if proof in self.rejected_proofs
        ):
            _fail("invalid_report")
        if self.issuance_controls != _issuance_controls():
            _fail("issuance_control_not_satisfied")
        if self.deployment_review_checklist != DEPLOYMENT_REVIEW_CHECKLIST:
            _fail("invalid_report")
        if self.limitations != RUNTIME_BOUNDARY_LIMITATIONS:
            _fail("invalid_report")
        if self.direct_invoke_forgery != DIRECT_INVOKE_FORGERY:
            _fail("invalid_report")
        if self.execution_authority is not MasterRouteExecutionAuthority.DENIED:
            _fail("execution_authority_not_granted")
        if self.production_authority is not MasterProductionAuthority.DENIED:
            _fail("execution_authority_not_granted")
        if self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            _fail("source_authenticity_not_established")
        if self.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
            _fail("request_coverage_not_established")
        _false(self.production_issuance_available, "production_issuance_unavailable")
        _false(self.invocation_attested, "runtime_admission_not_attested")
        _false(self.event_accepted_as_invocation_source, "event_field_not_verified")
        _false(self.envelope_established, "envelope_not_established")
        if self.established_envelope_fields != ():
            _fail("envelope_not_established")
        if self.schema_version != MASTER_GATEWAY_RUNTIME_BOUNDARY_SCHEMA_VERSION:
            _fail("invalid_report")

    def __bool__(self) -> bool:
        return False

    def allows_production_issuance(self) -> bool:
        """Production issuance is unavailable. This method cannot return true."""

        _false(self.production_issuance_available, "production_issuance_unavailable")
        _false(RUNTIME_BOUNDARY_ISSUANCE_AVAILABLE, "production_issuance_unavailable")
        return False

    def fact(self, name: RuntimeFactName) -> RuntimeBoundaryFact:
        """Return one fact. The fact's truth value is not admission."""

        if type(name) is not RuntimeFactName:
            _fail("invalid_report")
        for item in self.facts:
            if item.name is name:
                return item
        _fail("invalid_report")
        raise AssertionError("unreachable")


def current_disabled_route_manifest(*, name_prefix: str) -> ServerOwnedGatewayManifest:
    """The current Terraform default: ``POST /ai/query`` is not enabled.

    Account, region, and API id are left empty because the module does not
    hard-code them. They stay not-yet-inspected deployment facts.
    """

    return ServerOwnedGatewayManifest(
        enable_ai_query_route=False,
        name_prefix=name_prefix,
        partition="",
        region="",
        account_id="",
        api_id="",
        stage_name=REQUIRED_STAGE_NAME,
        route_key="",
        issuer="",
        audiences=(),
        required_scopes=(),
        authorizer_issuer="",
        authorizer_audiences=(),
        authorizer_type="",
        authorization_type="",
        identity_source="",
        function_arn="",
        invoke_arn="",
        integration_type="",
        integration_method="",
        payload_format_version="",
        invoke_permissions=(),
        identity_policy_invoke_principals=(),
        additional_invoke_sources=(),
        function_url_enabled=False,
        ai_log_group_name="",
    )


def _ordered(found: set[ConfigurationDenialReason]) -> tuple[ConfigurationDenialReason, ...]:
    return tuple(reason for reason in ConfigurationDenialReason if reason in found)


def _ordered_proofs(
    found: set[RejectedInvocationProof],
) -> tuple[RejectedInvocationProof, ...]:
    return tuple(proof for proof in RejectedInvocationProof if proof in found)


def _event_proofs(event: object) -> set[RejectedInvocationProof]:
    if event is None:
        return set()
    proofs = {RejectedInvocationProof.DIRECT_INVOKE_MIMIC}
    if type(event) is not dict:
        return proofs
    if "trusted" in event:
        proofs.add(RejectedInvocationProof.TRUSTED_FLAG)
    if "requestId" in event:
        proofs.add(RejectedInvocationProof.REQUEST_ID)
    version = event.get("version")
    if version == "2.0" or "requestContext" in event or "routeKey" in event:
        proofs.add(RejectedInvocationProof.FORGED_HTTP_API_EVENT)
    if "requestContext" in event:
        proofs.add(RejectedInvocationProof.REQUEST_CONTEXT)
        context = event.get("requestContext")
        if type(context) is dict:
            if "requestId" in context:
                proofs.add(RejectedInvocationProof.REQUEST_ID)
            authorizer = context.get("authorizer")
            if type(authorizer) is dict and "jwt" in authorizer:
                proofs.add(RejectedInvocationProof.JWT_LOOKING_MATERIAL)
    headers = event.get("headers")
    if type(headers) is dict:
        for key in headers:
            if type(key) is str and key.casefold() == "authorization":
                proofs.add(RejectedInvocationProof.AUTHORIZATION_HEADER)
                break
    return proofs


def _verified(value: str) -> tuple[RuntimeEvidenceClass, EvidenceStatus, str]:
    return (RuntimeEvidenceClass.VERIFIED_CONFIGURATION, EvidenceStatus.VERIFIED, value)


def _rejected() -> tuple[RuntimeEvidenceClass, EvidenceStatus, str]:
    return (RuntimeEvidenceClass.VERIFIED_CONFIGURATION, EvidenceStatus.REJECTED, "REJECTED")


def _disabled() -> tuple[RuntimeEvidenceClass, EvidenceStatus, str]:
    return (
        RuntimeEvidenceClass.VERIFIED_CONFIGURATION,
        EvidenceStatus.NOT_ENABLED,
        "DISABLED",
    )


def _inspected_gap() -> tuple[RuntimeEvidenceClass, EvidenceStatus, str]:
    return (
        RuntimeEvidenceClass.NOT_YET_INSPECTED,
        EvidenceStatus.NOT_INSPECTED,
        "NOT_INSPECTED",
    )


def _presented(presented: bool) -> tuple[RuntimeEvidenceClass, EvidenceStatus, str]:
    if presented:
        return (
            RuntimeEvidenceClass.UNTRUSTED_EVENT_FIELD,
            EvidenceStatus.UNTRUSTED,
            "UNTRUSTED",
        )
    return (
        RuntimeEvidenceClass.NOT_YET_INSPECTED,
        EvidenceStatus.NOT_PRESENTED,
        "NOT_PRESENTED",
    )


def _location_mismatch(
    manifest: ServerOwnedGatewayManifest,
    reasons: set[ConfigurationDenialReason],
) -> None:
    if not manifest.function_arn:
        return
    parts = manifest.function_arn.split(":")
    pairs = (
        (manifest.partition, parts[1], ConfigurationDenialReason.WRONG_PARTITION),
        (manifest.region, parts[3], ConfigurationDenialReason.WRONG_REGION),
        (manifest.account_id, parts[4], ConfigurationDenialReason.WRONG_ACCOUNT),
    )
    for configured, actual, reason in pairs:
        if configured and configured != actual:
            reasons.add(reason)
        if manifest.enable_ai_query_route and configured != actual:
            reasons.add(reason)


def _integration_mismatch(
    manifest: ServerOwnedGatewayManifest,
    reasons: set[ConfigurationDenialReason],
) -> None:
    present = any(
        (
            manifest.integration_type,
            manifest.integration_method,
            manifest.payload_format_version,
            manifest.authorization_type,
            manifest.authorizer_type,
            manifest.identity_source,
        )
    )
    if not manifest.enable_ai_query_route and not present:
        return
    matches = (
        manifest.integration_type == REQUIRED_INTEGRATION_TYPE
        and manifest.integration_method == REQUIRED_INTEGRATION_METHOD
        and manifest.payload_format_version == REQUIRED_PAYLOAD_FORMAT
        and manifest.authorization_type == REQUIRED_AUTHORIZATION_TYPE
        and manifest.authorizer_type == REQUIRED_AUTHORIZER_TYPE
        and manifest.identity_source == REQUIRED_IDENTITY_SOURCE
    )
    if not matches:
        reasons.add(ConfigurationDenialReason.INTEGRATION_MISMATCH)


def _permission_reasons(
    manifest: ServerOwnedGatewayManifest,
    reasons: set[ConfigurationDenialReason],
) -> set[ConfigurationDenialReason]:
    """Return statement defects. Identity-policy defects stay on ``reasons`` only."""

    statement_problems: set[ConfigurationDenialReason] = set()
    if manifest.function_url_enabled or manifest.additional_invoke_sources:
        reasons.add(ConfigurationDenialReason.OPEN_ALTERNATIVE_INVOCATION_PATH)
    if manifest.identity_policy_invoke_principals:
        reasons.add(ConfigurationDenialReason.OTHER_PRINCIPAL_INVOKE)
        if any("*" in principal for principal in manifest.identity_policy_invoke_principals):
            reasons.add(ConfigurationDenialReason.WILDCARD_INVOKE_PERMISSION)
    if len(manifest.invoke_permissions) > 1:
        statement_problems.add(ConfigurationDenialReason.OPEN_ALTERNATIVE_INVOCATION_PATH)
    if manifest.enable_ai_query_route and not manifest.invoke_permissions:
        statement_problems.add(ConfigurationDenialReason.MISSING_EVIDENCE)

    qualifier = ""
    base_arn = manifest.function_arn
    if manifest.function_arn:
        base_arn, qualifier = function_base_and_qualifier(manifest.function_arn)
    expected_statement = (
        QUALIFIED_PERMISSION_STATEMENT_ID
        if qualifier
        else UNQUALIFIED_PERMISSION_STATEMENT_ID
    )
    required_source = ""
    if manifest.partition and manifest.region and manifest.account_id and manifest.api_id:
        required_source = expected_source_arn(
            expected_execution_arn(
                manifest.partition,
                manifest.region,
                manifest.account_id,
                manifest.api_id,
            ),
            REQUIRED_STAGE_NAME,
        )
    elif manifest.enable_ai_query_route:
        statement_problems.add(ConfigurationDenialReason.MISSING_EVIDENCE)

    for permission in manifest.invoke_permissions:
        if (
            "*" in permission.source_arn
            or "*" in permission.action
            or "*" in permission.principal
        ):
            statement_problems.add(ConfigurationDenialReason.WILDCARD_INVOKE_PERMISSION)
        if permission.principal != REQUIRED_GATEWAY_PRINCIPAL:
            statement_problems.add(ConfigurationDenialReason.OTHER_PRINCIPAL_INVOKE)
        if permission.action != REQUIRED_INVOKE_ACTION:
            statement_problems.add(
                ConfigurationDenialReason.WILDCARD_INVOKE_PERMISSION
                if "*" in permission.action
                else ConfigurationDenialReason.INTEGRATION_MISMATCH
            )
        if permission.qualifier != qualifier:
            statement_problems.add(ConfigurationDenialReason.WRONG_QUALIFIER)
        if permission.function_arn != base_arn:
            statement_problems.add(ConfigurationDenialReason.WRONG_FUNCTION_ARN)
        if required_source and permission.source_arn != required_source:
            if "*" not in permission.source_arn:
                statement_problems.add(ConfigurationDenialReason.SOURCE_ARN_MISMATCH)
        if manifest.function_arn and permission.statement_id != expected_statement:
            statement_problems.add(ConfigurationDenialReason.PERMISSION_STATEMENT_MISMATCH)
    reasons.update(statement_problems)
    return statement_problems


def _assess_reasons(
    manifest: ServerOwnedGatewayManifest,
) -> tuple[tuple[ConfigurationDenialReason, ...], set[ConfigurationDenialReason]]:
    reasons: set[ConfigurationDenialReason] = set()
    if not manifest.enable_ai_query_route:
        reasons.add(ConfigurationDenialReason.ROUTE_DISABLED_BY_DEFAULT)
    if manifest.function_arn and targets_operational_lambda(
        manifest.function_arn,
        manifest.name_prefix,
    ):
        reasons.add(ConfigurationDenialReason.OPERATIONAL_LAMBDA_REUSE)
    if (
        manifest.ai_log_group_name
        and manifest.ai_log_group_name
        == operational_log_group_name(manifest.name_prefix)
    ):
        reasons.add(ConfigurationDenialReason.LOG_GROUP_COLLISION)
    if manifest.enable_ai_query_route and manifest.ai_log_group_name == "":
        reasons.add(ConfigurationDenialReason.MISSING_EVIDENCE)
    if manifest.stage_name != REQUIRED_STAGE_NAME and (
        manifest.enable_ai_query_route or manifest.stage_name != ""
    ):
        reasons.add(ConfigurationDenialReason.WRONG_STAGE)
    if manifest.route_key != REQUIRED_ROUTE_KEY and (
        manifest.enable_ai_query_route or manifest.route_key != ""
    ):
        reasons.add(ConfigurationDenialReason.WRONG_ROUTE)
    if manifest.issuer != manifest.authorizer_issuer and (
        manifest.enable_ai_query_route or (manifest.issuer and manifest.authorizer_issuer)
    ):
        reasons.add(ConfigurationDenialReason.IDENTITY_PROVIDER_MISMATCH)
    if manifest.audiences != manifest.authorizer_audiences and (
        manifest.enable_ai_query_route
        or (manifest.audiences and manifest.authorizer_audiences)
    ):
        reasons.add(ConfigurationDenialReason.AUDIENCE_MISMATCH)
    _location_mismatch(manifest, reasons)
    if manifest.function_arn and manifest.invoke_arn:
        if manifest.invoke_arn != expected_invoke_arn(manifest.function_arn):
            reasons.add(ConfigurationDenialReason.INVOKE_ARN_MISMATCH)
    elif manifest.enable_ai_query_route:
        reasons.add(ConfigurationDenialReason.INVOKE_ARN_MISMATCH)
    _integration_mismatch(manifest, reasons)
    statement_problems = _permission_reasons(manifest, reasons)
    return _ordered(reasons), statement_problems


def _fact_rows(
    manifest: ServerOwnedGatewayManifest,
    reasons: tuple[ConfigurationDenialReason, ...],
    statement_problems: set[ConfigurationDenialReason],
    *,
    event_presented: bool,
    context_presented: bool,
    trusted_presented: bool,
) -> tuple[RuntimeBoundaryFact, ...]:
    reason_set = set(reasons)
    enabled = manifest.enable_ai_query_route
    qualifier = ""
    if manifest.function_arn:
        _, qualifier = function_base_and_qualifier(manifest.function_arn)
    source = ""
    if manifest.invoke_permissions:
        source = manifest.invoke_permissions[0].source_arn

    def pick(
        rejected: bool,
        verified_value: str,
        *,
        absent_disabled: bool,
    ) -> tuple[RuntimeEvidenceClass, EvidenceStatus, str]:
        if rejected:
            return _rejected()
        if absent_disabled:
            return _disabled()
        return _verified(verified_value)

    invoke_rejected = bool(
        statement_problems
        or reason_set.intersection(
            {
                ConfigurationDenialReason.OTHER_PRINCIPAL_INVOKE,
                ConfigurationDenialReason.OPEN_ALTERNATIVE_INVOCATION_PATH,
                ConfigurationDenialReason.WILDCARD_INVOKE_PERMISSION,
            }
        )
    )
    rows = {
        RuntimeFactName.ROUTE_ENABLEMENT: _verified("true" if enabled else "false"),
        RuntimeFactName.ROUTE: pick(
            ConfigurationDenialReason.WRONG_ROUTE in reason_set,
            REQUIRED_ROUTE_KEY,
            absent_disabled=not enabled,
        ),
        RuntimeFactName.STAGE: pick(
            ConfigurationDenialReason.WRONG_STAGE in reason_set,
            manifest.stage_name or REQUIRED_STAGE_NAME,
            absent_disabled=manifest.stage_name == "",
        ),
        RuntimeFactName.ISSUER: pick(
            ConfigurationDenialReason.IDENTITY_PROVIDER_MISMATCH in reason_set,
            manifest.issuer,
            absent_disabled=not enabled,
        ),
        RuntimeFactName.AUDIENCE: pick(
            ConfigurationDenialReason.AUDIENCE_MISMATCH in reason_set,
            ",".join(manifest.audiences),
            absent_disabled=not enabled,
        ),
        RuntimeFactName.SCOPE: pick(
            False,
            " ".join(manifest.required_scopes),
            absent_disabled=not enabled,
        ),
        RuntimeFactName.REGION: (
            _rejected()
            if reason_set.intersection(
                {
                    ConfigurationDenialReason.WRONG_REGION,
                    ConfigurationDenialReason.WRONG_PARTITION,
                }
            )
            else _inspected_gap()
            if manifest.region == ""
            else _verified(manifest.region)
        ),
        RuntimeFactName.ACCOUNT: (
            _rejected()
            if ConfigurationDenialReason.WRONG_ACCOUNT in reason_set
            else _inspected_gap()
            if manifest.account_id == ""
            else _verified(manifest.account_id)
        ),
        RuntimeFactName.QUALIFIER: pick(
            ConfigurationDenialReason.WRONG_QUALIFIER in reason_set,
            qualifier or "UNQUALIFIED",
            absent_disabled=not manifest.function_arn,
        ),
        RuntimeFactName.INTEGRATION: pick(
            ConfigurationDenialReason.INTEGRATION_MISMATCH in reason_set
            or ConfigurationDenialReason.INVOKE_ARN_MISMATCH in reason_set,
            "AWS_PROXY:POST:2.0:JWT",
            absent_disabled=not enabled,
        ),
        RuntimeFactName.INVOKE_PERMISSION: pick(
            invoke_rejected,
            f"{REQUIRED_GATEWAY_PRINCIPAL}:{REQUIRED_INVOKE_ACTION}",
            absent_disabled=not enabled and not manifest.invoke_permissions,
        ),
        RuntimeFactName.RESOURCE_POLICY: pick(
            bool(statement_problems),
            source or "SCOPED",
            absent_disabled=not manifest.invoke_permissions and not enabled,
        ),
        RuntimeFactName.LIVE_RESOURCE_POLICY: _inspected_gap(),
        RuntimeFactName.LIVE_IDENTITY_POLICY: _inspected_gap(),
        RuntimeFactName.DEPLOYMENT: _inspected_gap(),
        RuntimeFactName.LOGS: pick(
            ConfigurationDenialReason.LOG_GROUP_COLLISION in reason_set
            or (
                enabled
                and ConfigurationDenialReason.MISSING_EVIDENCE in reason_set
                and manifest.ai_log_group_name == ""
            ),
            manifest.ai_log_group_name,
            absent_disabled=manifest.ai_log_group_name == "",
        ),
        RuntimeFactName.LIVE_LOGS: _inspected_gap(),
        RuntimeFactName.LIVE_QUALIFIER: _inspected_gap(),
        RuntimeFactName.LIVE_FUNCTION_URL: _inspected_gap(),
        RuntimeFactName.OPERATIONAL_SEPARATION: pick(
            ConfigurationDenialReason.OPERATIONAL_LAMBDA_REUSE in reason_set
            or ConfigurationDenialReason.LOG_GROUP_COLLISION in reason_set,
            "SEPARATE",
            absent_disabled=not manifest.function_arn and manifest.ai_log_group_name == "",
        ),
        RuntimeFactName.PER_REQUEST_PROVENANCE: _presented(
            event_presented or context_presented or trusted_presented
        ),
        RuntimeFactName.EVENT_FIELDS: _presented(event_presented),
        RuntimeFactName.LAMBDA_CONTEXT: _presented(context_presented),
        RuntimeFactName.TRUSTED_FLAG: _presented(trusted_presented),
    }
    return tuple(
        RuntimeBoundaryFact(name, *rows[name]) for name in RuntimeFactName
    )


def assess_gateway_runtime_boundary(
    manifest: object,
    event: object = None,
    *,
    lambda_context: object = None,
    trusted: object = None,
) -> GatewayRuntimeBoundaryReport:
    """Check server-owned gateway configuration and classify caller-supplied proofs.

    ``event``, ``lambda_context``, and ``trusted`` never select the issuer,
    audience, account, stage, or invocation source. A forged HTTP API v2
    document stays an untrusted event. ``trusted=True`` does not attest it.

    The return value is false. ``allows_production_issuance`` is false.
    Live AWS controls in the report stay unsatisfied.
    """

    _closed_constants()
    if type(manifest) is not ServerOwnedGatewayManifest:
        _fail("manifest_not_server_owned")
    reasons, statement_problems = _assess_reasons(manifest)
    proofs = _event_proofs(event)
    if lambda_context is not None:
        proofs.add(RejectedInvocationProof.LAMBDA_CONTEXT)
    if trusted is not None:
        proofs.add(RejectedInvocationProof.TRUSTED_FLAG)
    decision = (
        ConfigurationRestrictionDecision.DENIED
        if reasons
        else ConfigurationRestrictionDecision.RESTRICTED
    )
    return GatewayRuntimeBoundaryReport(
        configuration_decision=decision,
        invocation_standing=gateway_identity.InvocationProvenanceStanding.NOT_ATTESTED,
        denial_reasons=reasons,
        facts=_fact_rows(
            manifest,
            reasons,
            statement_problems,
            event_presented=event is not None,
            context_presented=lambda_context is not None,
            trusted_presented=trusted is not None,
        ),
        rejected_proofs=_ordered_proofs(proofs),
        issuance_controls=_issuance_controls(),
        deployment_review_checklist=DEPLOYMENT_REVIEW_CHECKLIST,
        limitations=RUNTIME_BOUNDARY_LIMITATIONS,
        direct_invoke_forgery=DIRECT_INVOKE_FORGERY,
        execution_authority=MasterRouteExecutionAuthority.DENIED,
        production_authority=MasterProductionAuthority.DENIED,
        source_authenticity=MasterSourceAuthenticity.NOT_ESTABLISHED,
        request_coverage=MasterRequestCoverage.NOT_ESTABLISHED,
        production_issuance_available=False,
        invocation_attested=False,
        event_accepted_as_invocation_source=False,
        envelope_established=False,
        established_envelope_fields=(),
    )


def refuse_runtime_admission(report: object) -> None:
    """Runtime admission is not attested. This is not a principal issuer."""

    if type(report) is not GatewayRuntimeBoundaryReport:
        _fail("invalid_report")
    _false(report.production_issuance_available, "production_issuance_unavailable")
    _false(report.invocation_attested, "runtime_admission_not_attested")
    _false(report.event_accepted_as_invocation_source, "event_field_not_verified")
    if report.invocation_standing is not gateway_identity.InvocationProvenanceStanding.NOT_ATTESTED:
        _fail("runtime_admission_not_attested")
    if report.allows_production_issuance() is not False:
        _fail("production_issuance_unavailable")
    raise ContractValidationError(
        ("runtime_admission_not_attested", "production_issuance_unavailable")
    )
