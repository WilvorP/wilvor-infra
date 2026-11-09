"""Bounded parser for API Gateway HTTP API v2 identity material.

Five materials stay distinct:

1. Raw user-supplied ``Authorization`` header. It is captured and never
   decoded. A bearer token is not a claim set and not a principal.
2. Claimed request identity. ``requestId``, HTTP method, ``rawPath``, and
   ``routeKey`` are the fields the operational API already reads from an
   HTTP API v2 event, plus the route key that pins the AI route. Matching
   a request id is not authentication.
3. API Gateway authorizer claim metadata. This is the
   ``requestContext.authorizer.jwt`` object: string claims and a scope
   list. Issuer, audience, subject, expiry, and required scopes are
   checked against server-owned configuration.
4. Externally verified API Gateway invocation provenance. Not available.
   A dictionary with that shape can be built by a direct Lambda invoke or
   a test. This module cannot tell those apart from a gateway invoke.
5. Application-owned principal. Not issued. The first four do not create
   one. ``MasterPlanningSessionRegistry`` membership and ``session_id``
   do not create one either.

Issuer and audience expectations come only from
``MasterGatewayIdentityConfiguration``. Stage variables, query parameters,
headers, and the event body cannot supply them.

This module does not verify a JWT signature, fetch a JWKS, decode an
``Authorization`` payload, call AWS, open a planning session, or dispatch
a specialist. Structural acceptance is not production authentication and
not execution permission.

Production issuance stays unavailable until the protected API Gateway
integration and the Lambda invocation boundary are independently reviewed.
There is no constructor that records that review as complete.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
    _validate_bounded_text,
)
from wilvor_ai.master_contracts import MasterProductionAuthority
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_planning_session import (
    MasterPlanningSessionRecord,
    MasterPlanningSessionRegistry,
)
from wilvor_ai.master_routing import (
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
)


MASTER_GATEWAY_IDENTITY_SCHEMA_VERSION = "wilvor.ai.master_gateway_identity.v1"

# There is no constructor that flips these.
PRODUCTION_GATEWAY_ISSUANCE_AVAILABLE = False
GATEWAY_INVOCATION_ATTESTATION_AVAILABLE = False
APPLICATION_OWNED_PRINCIPAL_AVAILABLE = False
UNVERIFIED_JWT_DECODING_IS_AUTHENTICATION = False

MAX_AUTHORIZATION_HEADER_LENGTH = 4096
MAX_EVENT_BODY_LENGTH = 65536
MAX_AUTHORIZER_CLAIMS = 16
MAX_AUTHORIZER_SCOPES = 16
MAX_REQUIRED_SCOPES = 8
MAX_EPOCH = 4_102_444_800
MAX_HEADERS = 32
MAX_QUERY_LENGTH = 2048

_ISSUER = re.compile(
    r"https://[A-Za-z0-9.-]{1,253}(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~-]{1,64})*"
)
_AUDIENCE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SCOPE = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
_METHOD = re.compile(r"^[A-Z]{3,10}$")
_RAW_PATH = re.compile(r"^/(?:[A-Za-z0-9._~-]+)(?:/[A-Za-z0-9._~-]+)*$")
_HEADER_NAME = re.compile(r"^[a-z0-9-]{1,64}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._~=+/-]{1,128}$")
_QUERY = re.compile(r"^[A-Za-z0-9._~%=&-]{1,2048}$")
_API_ID = re.compile(r"^[a-z0-9]{1,16}$")
_ACCOUNT_ID = re.compile(r"^[0-9]{12}$")
_STAGE = re.compile(r"^[A-Za-z0-9$_-]{1,64}$")
_SUBJECT = re.compile(rf"^[A-Za-z0-9._:@|/+~=-]{{1,{PROVENANCE_TOKEN_MAX_LENGTH}}}$")
_IGNORED_CLAIM_VALUE = _SUBJECT

_EVENT_KEYS = frozenset(
    {
        "version",
        "routeKey",
        "rawPath",
        "rawQueryString",
        "headers",
        "requestContext",
        "isBase64Encoded",
        "body",
        "pathParameters",
        "queryStringParameters",
        "cookies",
        "stageVariables",
    }
)
_REQUIRED_EVENT_KEYS = frozenset(
    {
        "version",
        "routeKey",
        "rawPath",
        "rawQueryString",
        "headers",
        "requestContext",
        "isBase64Encoded",
    }
)
_CONTEXT_KEYS = frozenset(
    {
        "accountId",
        "apiId",
        "authorizer",
        "domainName",
        "domainPrefix",
        "http",
        "requestId",
        "routeKey",
        "stage",
        "time",
        "timeEpoch",
    }
)
_REQUIRED_CONTEXT_KEYS = frozenset({"http", "requestId", "routeKey"})
_PARAMETER_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_EVENT_TIME_EPOCH = 10**14
_HTTP_KEYS = frozenset({"method", "path", "protocol", "sourceIp", "userAgent"})
_REQUIRED_HTTP_KEYS = frozenset({"method", "path"})
_SMUGGLED_EVENT_KEYS = frozenset(
    {
        "principal",
        "user",
        "subject",
        "identity",
        "claims",
        "authenticated",
        "execution_permission",
        "issuer",
        "audience",
    }
)
_SMUGGLED_HEADERS = frozenset(
    {
        "x-principal",
        "x-user",
        "x-subject",
        "x-authenticated",
        "x-execution-permission",
        "x-wilvor-principal",
        "x-user-sub",
    }
)
_PRINCIPAL_PARAMETER_KEYS = frozenset(
    {
        "principal",
        "user",
        "subject",
        "authenticated",
        "execution_permission",
        "session_id",
    }
)
_CONFIGURATION_PARAMETER_KEYS = frozenset(
    {
        "issuer",
        "audience",
        "aud",
        "iss",
        "scope",
        "scopes",
        "required_scopes",
    }
)
_REJECTED_PRINCIPAL_CLAIMS = frozenset(
    {
        "principal",
        "application_principal",
        "authenticated",
        "execution_permission",
        "role",
        "roles",
        "groups",
        "cognito:groups",
    }
)
_IGNORED_STRING_CLAIMS = frozenset(
    {
        "jti",
        "token_use",
        "client_id",
        "azp",
        "origin_jti",
        "event_id",
        "username",
        "email",
        "cognito:username",
        "preferred_username",
    }
)
_EPOCH_CLAIMS = frozenset({"exp", "nbf", "iat", "auth_time"})
_KNOWN_CLAIMS = (
    frozenset({"iss", "aud", "sub", "scope", "scp"})
    | _EPOCH_CLAIMS
    | _IGNORED_STRING_CLAIMS
)
_SESSION_TYPES = (MasterPlanningSessionRecord, MasterPlanningSessionRegistry)


class GatewayIdentityMaterial(str, Enum):
    """The five materials this parser keeps apart."""

    RAW_AUTHORIZATION_HEADER = "RAW_AUTHORIZATION_HEADER"
    CLAIMED_REQUEST_IDENTITY = "CLAIMED_REQUEST_IDENTITY"
    AUTHORIZER_CLAIM_METADATA = "AUTHORIZER_CLAIM_METADATA"
    GATEWAY_INVOCATION_PROVENANCE = "GATEWAY_INVOCATION_PROVENANCE"
    APPLICATION_OWNED_PRINCIPAL = "APPLICATION_OWNED_PRINCIPAL"


class InvocationProvenanceStanding(str, Enum):
    """No value on this enum attests a gateway invoke."""

    NOT_ATTESTED = "NOT_ATTESTED"


class IngressAttestationStanding(str, Enum):
    """The protected integration and Lambda boundary are not yet reviewed."""

    UNRESOLVED = "UNRESOLVED"


class ApplicationPrincipalStanding(str, Enum):
    """No value on this enum is an issued application principal."""

    NOT_ISSUED = "NOT_ISSUED"


class GatewayIdentityLimitation(str, Enum):
    """Standing limits. The full set is present on every assessment."""

    AUTHORIZATION_HEADER_IS_NOT_AUTHENTICATION = (
        "AUTHORIZATION_HEADER_IS_NOT_AUTHENTICATION"
    )
    CLAIMED_REQUEST_IS_NOT_AUTHENTICATION = "CLAIMED_REQUEST_IS_NOT_AUTHENTICATION"
    AUTHORIZER_CLAIMS_ARE_NOT_AUTHENTICATION = "AUTHORIZER_CLAIMS_ARE_NOT_AUTHENTICATION"
    MATCHING_REQUEST_ID_IS_NOT_AUTHENTICATION = "MATCHING_REQUEST_ID_IS_NOT_AUTHENTICATION"
    EVENT_DICTIONARY_IS_NOT_INVOCATION_PROVENANCE = (
        "EVENT_DICTIONARY_IS_NOT_INVOCATION_PROVENANCE"
    )
    INVOCATION_PROVENANCE_NOT_ATTESTED = "INVOCATION_PROVENANCE_NOT_ATTESTED"
    INGRESS_ATTESTATION_UNRESOLVED = "INGRESS_ATTESTATION_UNRESOLVED"
    PROTECTED_GATEWAY_INTEGRATION_NOT_REVIEWED = (
        "PROTECTED_GATEWAY_INTEGRATION_NOT_REVIEWED"
    )
    LAMBDA_INVOCATION_BOUNDARY_NOT_REVIEWED = "LAMBDA_INVOCATION_BOUNDARY_NOT_REVIEWED"
    APPLICATION_PRINCIPAL_NOT_ISSUED = "APPLICATION_PRINCIPAL_NOT_ISSUED"
    STRUCTURAL_VALIDITY_IS_NOT_EXECUTION_PERMISSION = (
        "STRUCTURAL_VALIDITY_IS_NOT_EXECUTION_PERMISSION"
    )
    PLANNING_SESSION_IS_NOT_A_PRINCIPAL = "PLANNING_SESSION_IS_NOT_A_PRINCIPAL"
    UNVERIFIED_JWT_IS_NOT_AUTHENTICATION = "UNVERIFIED_JWT_IS_NOT_AUTHENTICATION"
    EXECUTION_AUTHORITY_REMAINS_DENIED = "EXECUTION_AUTHORITY_REMAINS_DENIED"
    PRODUCTION_AUTHORITY_REMAINS_DENIED = "PRODUCTION_AUTHORITY_REMAINS_DENIED"
    SOURCE_AUTHENTICITY_REMAINS_NOT_ESTABLISHED = (
        "SOURCE_AUTHENTICITY_REMAINS_NOT_ESTABLISHED"
    )
    REQUEST_COVERAGE_REMAINS_NOT_ESTABLISHED = "REQUEST_COVERAGE_REMAINS_NOT_ESTABLISHED"
    PRODUCTION_ISSUANCE_UNAVAILABLE = "PRODUCTION_ISSUANCE_UNAVAILABLE"


GATEWAY_IDENTITY_LIMITATIONS = tuple(GatewayIdentityLimitation)


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _false(value: object, code: str) -> None:
    if value is not False:
        _fail(code)


def _epoch_number(value: object, code: str) -> int:
    if type(value) is not int or isinstance(value, bool):
        _fail(code)
    if value < 0 or value > MAX_EPOCH:
        _fail(code)
    return value


def _no_controls(value: str, code: str) -> None:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _fail(code)


def _issuer(value: object, code: str) -> str:
    if type(value) is not str or len(value) > PROVENANCE_TOKEN_MAX_LENGTH:
        _fail(code)
    if _ISSUER.fullmatch(value) is None or ".." in value or "@" in value or value.endswith("/"):
        _fail(code)
    return value


def _audience(value: object, code: str) -> str:
    if type(value) is not str or _AUDIENCE.fullmatch(value) is None:
        _fail(code)
    if value in {"*", "null", "undefined"}:
        _fail(code)
    return value


def _scope_token(value: object, code: str) -> str:
    if type(value) is not str or _SCOPE.fullmatch(value) is None or value == "*":
        _fail(code)
    return value


def _subject(value: object) -> str:
    if type(value) is not str or _SUBJECT.fullmatch(value) is None:
        _fail("invalid_subject")
    return value


def _dict(value: object, code: str) -> dict[object, object]:
    if type(value) is not dict:
        _fail(code)
    return value


def _reject_unknown(value: dict[object, object], allowed: frozenset[str], code: str) -> None:
    for key in value:
        if type(key) is not str or key not in allowed:
            _fail(code)


def _require_keys(value: dict[object, object], required: frozenset[str], code: str) -> None:
    for key in required:
        if key not in value:
            _fail(code)


def _optional_string(source: dict[object, object], key: str, pattern: re.Pattern[str], code: str) -> None:
    if key not in source:
        return
    candidate = source[key]
    if type(candidate) is not str or pattern.fullmatch(candidate) is None:
        _fail(code)


def _parameter_map(value: object, code: str) -> None:
    mapping = _dict(value, code)
    if len(mapping) > 16:
        _fail(code)
    for key, item in mapping.items():
        if type(key) is not str or _PARAMETER_NAME.fullmatch(key) is None:
            _fail(code)
        if type(item) is not str or item == "" or len(item) > PROVENANCE_TOKEN_MAX_LENGTH:
            _fail(code)
        _no_controls(item, code)
        folded = key.casefold()
        if folded in _PRINCIPAL_PARAMETER_KEYS:
            _fail("caller_principal_not_accepted")
        if folded in _CONFIGURATION_PARAMETER_KEYS:
            _fail("configuration_not_taken_from_request")


def _check_query(raw_query: object) -> None:
    if type(raw_query) is not str or len(raw_query) > MAX_QUERY_LENGTH:
        _fail("invalid_event")
    if raw_query == "":
        return
    if _QUERY.fullmatch(raw_query) is None:
        _fail("invalid_event")
    for part in raw_query.split("&"):
        if "=" not in part or part.startswith("="):
            _fail("invalid_event")
        key = part.split("=", 1)[0].casefold()
        if key in _PRINCIPAL_PARAMETER_KEYS:
            _fail("caller_principal_not_accepted")
        if key in _CONFIGURATION_PARAMETER_KEYS:
            _fail("configuration_not_taken_from_request")


def _claim_epoch(value: str) -> int:
    if not value.isascii() or not value.isdigit() or (len(value) > 1 and value.startswith("0")):
        _fail("malformed_claim")
    if len(value) > 10:
        _fail("malformed_claim")
    parsed = int(value)
    if parsed <= 0 or parsed > MAX_EPOCH:
        _fail("malformed_claim")
    return parsed


def _scope_list(value: object) -> tuple[str, ...]:
    if type(value) is not list or not value or len(value) > MAX_AUTHORIZER_SCOPES:
        _fail("malformed_scopes")
    scopes: list[str] = []
    for item in value:
        scopes.append(_scope_token(item, "malformed_scopes"))
    if len(set(scopes)) != len(scopes):
        _fail("malformed_scopes")
    return tuple(scopes)


def _split_scope_claim(value: str) -> frozenset[str]:
    if value != value.strip() or "  " in value:
        _fail("malformed_scopes")
    parts = value.split(" ")
    parsed = [_scope_token(part, "malformed_scopes") for part in parts]
    if len(set(parsed)) != len(parsed):
        _fail("malformed_scopes")
    return frozenset(parsed)


@dataclass(frozen=True)
class MasterGatewayIdentityConfiguration:
    """Server-owned route and token expectations.

    Construct this from application configuration. Do not populate it from
    the incoming event, stage variables, or headers.
    """

    issuer: str
    audience: str
    required_scopes: tuple[str, ...]
    method: str
    raw_path: str
    route_key: str

    def __post_init__(self) -> None:
        if isinstance(self.issuer, dict):
            _fail("configuration_not_taken_from_request")
        _issuer(self.issuer, "invalid_issuer")
        _audience(self.audience, "invalid_audience")
        if type(self.required_scopes) is not tuple:
            _fail("malformed_scopes")
        if not self.required_scopes:
            _fail("missing_required_scope")
        if len(self.required_scopes) > MAX_REQUIRED_SCOPES:
            _fail("malformed_scopes")
        if len(set(self.required_scopes)) != len(self.required_scopes):
            _fail("malformed_scopes")
        for scope in self.required_scopes:
            _scope_token(scope, "malformed_scopes")
        if type(self.method) is not str or _METHOD.fullmatch(self.method) is None:
            _fail("unexpected_method")
        if type(self.raw_path) is not str or _RAW_PATH.fullmatch(self.raw_path) is None:
            _fail("unexpected_route")
        if self.route_key != f"{self.method} {self.raw_path}":
            _fail("unexpected_route")


@dataclass(frozen=True)
class RawAuthorizationHeader:
    """User-supplied header text. ``payload_decoded`` cannot be true."""

    value: str | None
    present: bool
    payload_decoded: bool = False
    accepted_as_authentication: bool = False

    def __post_init__(self) -> None:
        if type(self.present) is not bool:
            _fail("malformed_authorization_header")
        if self.present:
            if type(self.value) is not str or self.value == "":
                _fail("malformed_authorization_header")
            if len(self.value) > MAX_AUTHORIZATION_HEADER_LENGTH:
                _fail("malformed_authorization_header")
            _no_controls(self.value, "malformed_authorization_header")
        elif self.value is not None:
            _fail("malformed_authorization_header")
        _false(self.payload_decoded, "authorization_header_not_authentication")
        _false(self.accepted_as_authentication, "authorization_header_not_authentication")

    def __repr__(self) -> str:
        state = "present" if self.present else "absent"
        return f"RawAuthorizationHeader({state}, payload_decoded=False)"


@dataclass(frozen=True)
class ClaimedRequestIdentity:
    """Request metadata copied from the event. Not a proven caller."""

    request_id: str
    method: str
    raw_path: str
    route_key: str
    authenticates_caller: bool = False

    def __post_init__(self) -> None:
        if type(self.request_id) is not str or _REQUEST_ID.fullmatch(self.request_id) is None:
            _fail("invalid_event")
        if self.route_key != f"{self.method} {self.raw_path}":
            _fail("invalid_event")
        _false(self.authenticates_caller, "claimed_request_not_authentication")


@dataclass(frozen=True)
class AuthorizerClaimMetadata:
    """Structurally checked JWT authorizer fields. Not a verified token."""

    issuer: str
    audience: str
    subject: str
    scopes: tuple[str, ...]
    expires_at_epoch: int
    not_before_epoch: int | None
    issued_at_epoch: int | None
    structurally_valid: bool = True
    authenticates_caller: bool = False
    signature_verified: bool = False
    jwks_consulted: bool = False

    def __post_init__(self) -> None:
        _issuer(self.issuer, "malformed_claim")
        _audience(self.audience, "malformed_claim")
        _subject(self.subject)
        if type(self.scopes) is not tuple or not self.scopes:
            _fail("malformed_scopes")
        if len(self.scopes) > MAX_AUTHORIZER_SCOPES or len(set(self.scopes)) != len(self.scopes):
            _fail("malformed_scopes")
        for scope in self.scopes:
            _scope_token(scope, "malformed_scopes")
        expires_at = _epoch_number(self.expires_at_epoch, "malformed_claim")
        for optional in (self.not_before_epoch, self.issued_at_epoch):
            if optional is not None:
                _epoch_number(optional, "malformed_claim")
        if self.not_before_epoch is not None and self.not_before_epoch >= expires_at:
            _fail("malformed_claim")
        if self.issued_at_epoch is not None and self.issued_at_epoch > expires_at:
            _fail("malformed_claim")
        if self.structurally_valid is not True:
            _fail("malformed_claim")
        _false(self.authenticates_caller, "authorizer_claims_not_authentication")
        _false(self.signature_verified, "unverified_jwt_not_authentication")
        _false(self.jwks_consulted, "unverified_jwt_not_authentication")


@dataclass(frozen=True)
class GatewayInvocationProvenance:
    """Provenance slot. A matching event dictionary does not fill it."""

    standing: InvocationProvenanceStanding = InvocationProvenanceStanding.NOT_ATTESTED
    attested: bool = False
    event_dictionary_accepted_as_proof: bool = False
    request_id_accepted_as_proof: bool = False

    def __post_init__(self) -> None:
        if self.standing is not InvocationProvenanceStanding.NOT_ATTESTED:
            _fail("gateway_invocation_not_attested")
        _false(self.attested, "gateway_invocation_not_attested")
        _false(self.event_dictionary_accepted_as_proof, "gateway_invocation_not_attested")
        _false(self.request_id_accepted_as_proof, "gateway_invocation_not_attested")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class UnresolvedIngressAttestation:
    """Review still required before any production principal can be issued.

    Both the protected gateway integration and the Lambda invocation
    boundary stay unreviewed. This object cannot record either review.
    """

    standing: IngressAttestationStanding = IngressAttestationStanding.UNRESOLVED
    protected_gateway_integration_reviewed: bool = False
    lambda_invocation_boundary_reviewed: bool = False
    production_issuance_available: bool = False

    def __post_init__(self) -> None:
        if self.standing is not IngressAttestationStanding.UNRESOLVED:
            _fail("ingress_attestation_unresolved")
        _false(self.protected_gateway_integration_reviewed, "ingress_attestation_unresolved")
        _false(self.lambda_invocation_boundary_reviewed, "ingress_attestation_unresolved")
        _false(self.production_issuance_available, "production_issuance_unavailable")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class ApplicationPrincipalRequirement:
    """The unmet requirement for an application-owned principal."""

    standing: ApplicationPrincipalStanding = ApplicationPrincipalStanding.NOT_ISSUED
    issued: bool = False
    subject_accepted_as_principal: bool = False

    def __post_init__(self) -> None:
        if self.standing is not ApplicationPrincipalStanding.NOT_ISSUED:
            _fail("application_principal_not_issued")
        _false(self.issued, "application_principal_not_issued")
        _false(self.subject_accepted_as_principal, "application_principal_not_issued")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class MasterGatewayIdentityAssessment:
    """Structural parse result. Truth value is never execution permission."""

    raw_authorization: RawAuthorizationHeader
    claimed_request: ClaimedRequestIdentity
    authorizer_claims: AuthorizerClaimMetadata
    invocation_provenance: GatewayInvocationProvenance
    ingress_attestation: UnresolvedIngressAttestation
    application_principal: ApplicationPrincipalRequirement
    evaluated_at_epoch: int
    schema_version: str = MASTER_GATEWAY_IDENTITY_SCHEMA_VERSION
    limitations: tuple[GatewayIdentityLimitation, ...] = GATEWAY_IDENTITY_LIMITATIONS
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    production_authenticated: bool = False
    execution_permitted: bool = False

    def __post_init__(self) -> None:
        if type(self.raw_authorization) is not RawAuthorizationHeader:
            _fail("invalid_assessment")
        if type(self.claimed_request) is not ClaimedRequestIdentity:
            _fail("invalid_assessment")
        if type(self.authorizer_claims) is not AuthorizerClaimMetadata:
            _fail("invalid_assessment")
        if type(self.invocation_provenance) is not GatewayInvocationProvenance:
            _fail("invalid_assessment")
        if type(self.ingress_attestation) is not UnresolvedIngressAttestation:
            _fail("invalid_assessment")
        if type(self.application_principal) is not ApplicationPrincipalRequirement:
            _fail("invalid_assessment")
        _epoch_number(self.evaluated_at_epoch, "invalid_evaluation_epoch")
        claims = self.authorizer_claims
        if claims.expires_at_epoch <= self.evaluated_at_epoch:
            _fail("expired_claim")
        if claims.not_before_epoch is not None and self.evaluated_at_epoch < claims.not_before_epoch:
            _fail("claim_not_yet_valid")
        if claims.issued_at_epoch is not None and claims.issued_at_epoch > self.evaluated_at_epoch:
            _fail("claim_issued_in_future")
        if self.schema_version != MASTER_GATEWAY_IDENTITY_SCHEMA_VERSION:
            _fail("invalid_assessment")
        if self.limitations != GATEWAY_IDENTITY_LIMITATIONS:
            _fail("invalid_assessment")
        if self.execution_authority is not MasterRouteExecutionAuthority.DENIED:
            _fail("execution_authority_not_granted")
        if self.production_authority is not MasterProductionAuthority.DENIED:
            _fail("execution_authority_not_granted")
        if self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            _fail("source_authenticity_not_established")
        if self.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
            _fail("request_coverage_not_established")
        _false(self.production_authenticated, "application_principal_not_issued")
        _false(self.execution_permitted, "execution_permission_not_available")
        _false(PRODUCTION_GATEWAY_ISSUANCE_AVAILABLE, "production_issuance_unavailable")
        _false(GATEWAY_INVOCATION_ATTESTATION_AVAILABLE, "gateway_invocation_not_attested")
        _false(APPLICATION_OWNED_PRINCIPAL_AVAILABLE, "application_principal_not_issued")
        _false(UNVERIFIED_JWT_DECODING_IS_AUTHENTICATION, "unverified_jwt_not_authentication")

    def __bool__(self) -> bool:
        return False

    def material(self, kind: GatewayIdentityMaterial) -> object:
        """Return one separated material. None of them grants execution."""

        if kind is GatewayIdentityMaterial.RAW_AUTHORIZATION_HEADER:
            return self.raw_authorization
        if kind is GatewayIdentityMaterial.CLAIMED_REQUEST_IDENTITY:
            return self.claimed_request
        if kind is GatewayIdentityMaterial.AUTHORIZER_CLAIM_METADATA:
            return self.authorizer_claims
        if kind is GatewayIdentityMaterial.GATEWAY_INVOCATION_PROVENANCE:
            return self.invocation_provenance
        if kind is GatewayIdentityMaterial.APPLICATION_OWNED_PRINCIPAL:
            _false(self.application_principal.issued, "application_principal_not_issued")
            return self.application_principal
        _fail("invalid_identity_material")
        raise AssertionError("unreachable")


def reject_claimed_principal(candidate: object) -> None:
    """Refuse every caller-supplied principal label.

    A planning-session record or registry is rejected with its own code.
    No candidate is returned as an application principal.
    """

    if type(candidate) in _SESSION_TYPES:
        _fail("planning_session_is_not_principal")
    _fail("caller_principal_not_accepted")


def _headers(value: object) -> RawAuthorizationHeader:
    mapping = _dict(value, "invalid_event")
    if len(mapping) > MAX_HEADERS:
        _fail("invalid_event")
    normalized: dict[str, str] = {}
    for key, item in mapping.items():
        if type(key) is not str or _HEADER_NAME.fullmatch(key) is None:
            _fail("invalid_event")
        if key in _SMUGGLED_HEADERS:
            _fail("caller_principal_not_accepted")
        if type(item) is not str or item == "" or len(item) > MAX_AUTHORIZATION_HEADER_LENGTH:
            _fail("malformed_authorization_header" if key == "authorization" else "invalid_event")
        _no_controls(item, "malformed_authorization_header" if key == "authorization" else "invalid_event")
        normalized[key] = item
    raw = normalized.get("authorization")
    if raw is None:
        return RawAuthorizationHeader(None, False)
    if "," in raw:
        _fail("malformed_authorization_header")
    return RawAuthorizationHeader(raw, True)


def _claimed_route(
    event: dict[object, object],
    context: dict[object, object],
    configuration: MasterGatewayIdentityConfiguration,
) -> ClaimedRequestIdentity:
    http = _dict(context["http"], "invalid_event")
    _reject_unknown(http, _HTTP_KEYS, "invalid_event")
    _require_keys(http, _REQUIRED_HTTP_KEYS, "invalid_event")
    method = http["method"]
    raw_path = event["rawPath"]
    http_path = http["path"]
    route_key = event["routeKey"]
    context_route = context["routeKey"]
    request_id = context["requestId"]
    if type(method) is not str or _METHOD.fullmatch(method) is None:
        _fail("invalid_event")
    if type(raw_path) is not str or _RAW_PATH.fullmatch(raw_path) is None:
        _fail("invalid_event")
    if type(http_path) is not str or http_path != raw_path:
        _fail("unexpected_route")
    if type(route_key) is not str or type(context_route) is not str or route_key != context_route:
        _fail("invalid_event")
    if route_key != f"{method} {raw_path}":
        _fail("invalid_event")
    if type(request_id) is not str or _REQUEST_ID.fullmatch(request_id) is None:
        _fail("invalid_event")
    if "protocol" in http and http["protocol"] not in {"HTTP/1.1", "HTTP/2"}:
        _fail("invalid_event")
    for optional, limit in (("sourceIp", 64), ("userAgent", 256)):
        if optional not in http:
            continue
        candidate = http[optional]
        if type(candidate) is not str or candidate == "" or len(candidate) > limit:
            _fail("invalid_event")
        _no_controls(candidate, "invalid_event")
    if method != configuration.method:
        _fail("unexpected_method")
    if raw_path != configuration.raw_path or route_key != configuration.route_key:
        _fail("unexpected_route")
    return ClaimedRequestIdentity(request_id, method, raw_path, route_key)


def _context_metadata(context: dict[object, object]) -> None:
    _optional_string(context, "accountId", _ACCOUNT_ID, "invalid_event")
    _optional_string(context, "apiId", _API_ID, "invalid_event")
    _optional_string(context, "stage", _STAGE, "invalid_event")
    if "domainName" in context:
        _bounded = _validate_bounded_text(context["domainName"], "domain_name", max_length=253)
        if _bounded or type(context["domainName"]) is not str or any(
            character.isspace() for character in context["domainName"]
        ):
            _fail("invalid_event")
    if "domainPrefix" in context:
        errors = _validate_bounded_text(context["domainPrefix"], "domain_prefix", max_length=64)
        if errors or type(context["domainPrefix"]) is not str:
            _fail("invalid_event")
    if "time" in context:
        errors = _validate_bounded_text(context["time"], "time", max_length=64)
        if errors:
            _fail("invalid_event")
    if "timeEpoch" in context:
        # Milliseconds from the event are not the server expiry clock.
        candidate = context["timeEpoch"]
        if type(candidate) is not int or isinstance(candidate, bool):
            _fail("invalid_event")
        if candidate < 0 or candidate > _MAX_EVENT_TIME_EPOCH:
            _fail("invalid_event")


def _optional_event_fields(event: dict[object, object]) -> None:
    if "pathParameters" in event:
        _parameter_map(event["pathParameters"], "invalid_event")
    if "queryStringParameters" in event:
        _parameter_map(event["queryStringParameters"], "invalid_event")
    if "stageVariables" in event:
        _parameter_map(event["stageVariables"], "invalid_event")
    if "cookies" in event:
        cookies = event["cookies"]
        if type(cookies) is not list or len(cookies) > 8:
            _fail("invalid_event")
        for cookie in cookies:
            if type(cookie) is not str or cookie == "" or len(cookie) > PROVENANCE_TOKEN_MAX_LENGTH:
                _fail("invalid_event")
            _no_controls(cookie, "invalid_event")
    encoded = event["isBase64Encoded"]
    if type(encoded) is not bool:
        _fail("invalid_event")
    if "body" not in event:
        if encoded is True:
            _fail("invalid_event")
        return
    body = event["body"]
    if type(body) is not str or len(body) > MAX_EVENT_BODY_LENGTH or "\x00" in body:
        _fail("invalid_event")


def _authorizer_claims(
    context: dict[object, object],
    configuration: MasterGatewayIdentityConfiguration,
    evaluated_at_epoch: int,
) -> AuthorizerClaimMetadata:
    if "authorizer" not in context or context["authorizer"] is None:
        _fail("missing_jwt_authorizer")
    authorizer = context["authorizer"]
    if type(authorizer) is not dict:
        _fail("malformed_jwt_authorizer")
    if "jwt" not in authorizer:
        _fail("missing_jwt_authorizer")
    for key in authorizer:
        if key in _REJECTED_PRINCIPAL_CLAIMS or key in _PRINCIPAL_PARAMETER_KEYS or key in {
            "principalId",
            "principal_id",
        }:
            _fail("caller_principal_not_accepted")
        if key != "jwt":
            _fail("malformed_jwt_authorizer")
    jwt_context = authorizer["jwt"]
    if type(jwt_context) is not dict:
        _fail("malformed_jwt_authorizer")
    for key in jwt_context:
        if key not in {"claims", "scopes"}:
            _fail("malformed_jwt_authorizer")
    if "claims" not in jwt_context or "scopes" not in jwt_context:
        _fail("malformed_jwt_authorizer")
    claims = jwt_context["claims"]
    if type(claims) is not dict or len(claims) > MAX_AUTHORIZER_CLAIMS:
        _fail("malformed_claim")
    parsed: dict[str, str] = {}
    for key, value in claims.items():
        if type(key) is not str:
            _fail("malformed_claim")
        if key in _REJECTED_PRINCIPAL_CLAIMS:
            _fail("caller_principal_not_accepted")
        if key not in _KNOWN_CLAIMS:
            _fail("malformed_claim")
        if type(value) is not str:
            _fail("malformed_claim")
        parsed[key] = value
    for name in ("iss", "aud", "exp"):
        if name not in parsed:
            _fail("missing_claim")
    if "sub" not in parsed:
        _fail("missing_subject")
    issuer = _issuer(parsed["iss"], "malformed_claim")
    audience = _audience(parsed["aud"], "malformed_claim")
    if issuer != configuration.issuer:
        _fail("invalid_issuer")
    if audience != configuration.audience:
        _fail("invalid_audience")
    subject = _subject(parsed["sub"])
    expires_at = _claim_epoch(parsed["exp"])
    not_before = _claim_epoch(parsed["nbf"]) if "nbf" in parsed else None
    issued_at = _claim_epoch(parsed["iat"]) if "iat" in parsed else None
    if "auth_time" in parsed:
        auth_time = _claim_epoch(parsed["auth_time"])
        if auth_time > evaluated_at_epoch or auth_time > expires_at:
            _fail("malformed_claim")
    if not_before is not None and not_before >= expires_at:
        _fail("malformed_claim")
    if issued_at is not None and issued_at > expires_at:
        _fail("malformed_claim")
    for name in _IGNORED_STRING_CLAIMS:
        if name in parsed and _IGNORED_CLAIM_VALUE.fullmatch(parsed[name]) is None:
            _fail("malformed_claim")
    if expires_at <= evaluated_at_epoch:
        _fail("expired_claim")
    if not_before is not None and evaluated_at_epoch < not_before:
        _fail("claim_not_yet_valid")
    if issued_at is not None and issued_at > evaluated_at_epoch:
        _fail("claim_issued_in_future")
    scopes = _scope_list(jwt_context["scopes"])
    scope_names = frozenset(scopes)
    for claim_name in ("scope", "scp"):
        if claim_name in parsed and _split_scope_claim(parsed[claim_name]) != scope_names:
            _fail("malformed_scopes")
    if not set(configuration.required_scopes).issubset(scope_names):
        _fail("missing_required_scope")
    return AuthorizerClaimMetadata(
        issuer=issuer,
        audience=audience,
        subject=subject,
        scopes=scopes,
        expires_at_epoch=expires_at,
        not_before_epoch=not_before,
        issued_at_epoch=issued_at,
    )


def assess_master_gateway_identity(
    event: object,
    configuration: object,
    *,
    evaluation_epoch: object,
    claimed_principal: object = None,
) -> MasterGatewayIdentityAssessment:
    """Parse one HTTP API v2 event against server-owned identity configuration.

    A structurally valid result still has unattested invocation provenance,
    an unresolved ingress attestation, and an unissued application principal.
    ``evaluation_epoch`` is the server clock. ``requestContext.timeEpoch``
    is not read as that clock. ``claimed_principal`` is never accepted.
    """

    if type(configuration) is not MasterGatewayIdentityConfiguration:
        _fail("configuration_not_taken_from_request")
    if type(event) is not dict:
        _fail("invalid_event")
    evaluated_at = _epoch_number(evaluation_epoch, "invalid_evaluation_epoch")
    for key in event:
        if key in _SMUGGLED_EVENT_KEYS:
            _fail("caller_principal_not_accepted")
    _reject_unknown(event, _EVENT_KEYS, "invalid_event")
    _require_keys(event, _REQUIRED_EVENT_KEYS, "invalid_event")
    if event["version"] != "2.0":
        _fail("invalid_http_api_version")
    raw_authorization = _headers(event["headers"])
    _check_query(event["rawQueryString"])
    context = _dict(event["requestContext"], "missing_request_context")
    _reject_unknown(context, _CONTEXT_KEYS, "invalid_event")
    _require_keys(context, _REQUIRED_CONTEXT_KEYS, "missing_request_context")
    _context_metadata(context)
    _optional_event_fields(event)
    claimed_request = _claimed_route(event, context, configuration)
    authorizer_claims = _authorizer_claims(context, configuration, evaluated_at)
    if claimed_principal is not None:
        reject_claimed_principal(claimed_principal)
    return MasterGatewayIdentityAssessment(
        raw_authorization=raw_authorization,
        claimed_request=claimed_request,
        authorizer_claims=authorizer_claims,
        invocation_provenance=GatewayInvocationProvenance(),
        ingress_attestation=UnresolvedIngressAttestation(),
        application_principal=ApplicationPrincipalRequirement(),
        evaluated_at_epoch=evaluated_at,
    )


def issue_application_principal(assessment: object) -> None:
    """Production issuance is unavailable.

    Structurally valid claims cannot mint an application principal while
    ingress attestation remains unresolved.
    """

    if type(assessment) is not MasterGatewayIdentityAssessment:
        _fail("invalid_assessment")
    _false(
        assessment.ingress_attestation.production_issuance_available,
        "production_issuance_unavailable",
    )
    _false(assessment.application_principal.issued, "application_principal_not_issued")
    raise ContractValidationError(
        ("ingress_attestation_unresolved", "application_principal_not_issued")
    )


def promote_to_execution_permission(assessment: object) -> None:
    """Structural claim acceptance cannot become execution permission."""

    if type(assessment) is not MasterGatewayIdentityAssessment:
        _fail("invalid_assessment")
    if assessment.execution_authority is not MasterRouteExecutionAuthority.DENIED:
        _fail("execution_authority_not_granted")
    _false(assessment.execution_permitted, "execution_permission_not_available")
    raise ContractValidationError(
        ("ingress_attestation_unresolved", "execution_permission_not_available")
    )
