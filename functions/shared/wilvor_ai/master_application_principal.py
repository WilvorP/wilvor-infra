"""Application-owned principal boundary. Not a production issuer.

Lifecycle, kept as separate objects:

1. Descriptive observation. ``observe_untrusted_gateway_claims`` copies
   fields from a ``MasterGatewayIdentityAssessment``. The assessment is a
   structural parse of an API Gateway HTTP API v2 event. It is not
   cryptographic attestation. A direct Lambda invoke can present the same
   dictionary. The Authorization header is not retained and is not decoded.
2. Host identity input. ``IndependentlyHostAuthenticatedIdentityInput`` is
   the shape a future reviewed host would have to establish outside the
   event. Constructing it does not authenticate a caller and does not issue
   a principal. There is no ``from_event`` or ``from_dict`` parser.
3. Synthetic test admission. ``NonproductionPrincipalProofPort`` evaluates
   the observation against that input and mints a
   ``ServerOwnedPrincipalDescriptor``. The descriptor is an in-process
   contract-test admission. ``production_principal`` stays false.
   ``issue_production_application_principal`` never returns one.
4. Consumption. ``present`` re-checks lifetime, route, purpose, audience,
   subject, and request/session binding, then marks that admission consumed.
   Session membership and equal identifiers are not proof.

Production issuance is closed. ``issue_production_application_principal``
always raises ``production_principal_issuance_unavailable`` and
``reviewed_host_authority_not_wired``. It does not read environment
variables, the event, the gateway assessment, the identity input, or the
test-double seal. The test double is opened only by
``open_nonproduction_principal_proof_port`` with a seal from
``mint_nonproduction_contract_seal``. A string, boolean, or environment
value cannot mint or replace that seal.

Future host integration, not wired here:

The host must supply a reviewed, non-caller-controllable mechanism for
every ``HostIngressProofRequirement`` before production issuance can exist.
That mechanism has to live in the application host and the AWS invocation
boundary, not in this module and not in the Lambda event.

- Protected API Gateway integration review, and a Lambda invocation-boundary
  attestation that this invoke was performed by that integration. Who can
  invoke the Lambda is not visible on the event. See ``LAMBDA_INVOKE_BOUNDARY``.
- Independent token verification (signature and key source). A structurally
  valid JWT assessment is not that verification.
- Issuer, audience, subject, and scopes taken from that verified result.
  ``requestContext.authorizer.jwt``, the Authorization header, and a
  principal-label argument are not the source.
- Request and session binding: ``master_request_id`` and
  ``planning_session_id`` associated by the host. A
  ``MasterPlanningSessionRecord`` or registry membership is not that
  association.
- Purpose and route binding against the server route configuration and
  ``MasterPurpose``.
- Issued, not-before, and expiry times, checked against the host clock
  passed as ``evaluation_epoch``. ``requestContext.timeEpoch``,
  ``application_request_time_utc``, and ``deadline_utc`` are different
  clocks and are not the principal lifetime.
- Server ``policy_revision`` and authorization provenance naming the
  reviewed verifier. A policy-check string is not that provenance.

Principal scope is ``PrincipalScopeBinding``: issuer, audience, subject,
scopes, route, purpose, policy revision, and request/session identifiers
copied from the host identity input after those checks. Tenant and role are
``SourceClaimPresence.ABSENT``. This deployment has no tenant claim source
and no application role system. ``master_gateway_identity`` rejects role,
roles, groups, and ``cognito:groups``. Presenting them here is
``absent_source_claim``.

Envelope mapping for this boundary (these names are not a token):

- ``master_request_id``, ``planning_session_id``, ``purpose``,
  ``principal_ref``, ``policy_revision``, and ``correlation_id`` are bound
  from the host identity input. ``principal_ref`` is a synthetic handle,
  not the subject.
- ``accepted_at_utc`` is stored as ``accepted_at_epoch`` (authority
  issued-at, Unix seconds).
- ``deadline_utc`` is not a principal field. Expiry is ``expires_at_epoch``,
  compared with ``principal_evaluated_at_epoch``.
- ``gateway_claim_evaluated_at_epoch`` is the gateway assessment clock and
  is not the principal clock or the application request clock.
- ``step_id``, ``invocation_id``, ``specialist``, ``capability``,
  ``resource_scope``, ``region_scope``, ``historical_window``,
  ``decision_target_scope``, ``idempotency_key``, ``max_model_turns``, and
  ``max_tool_calls`` are ``NON_PRINCIPAL_ENVELOPE_FIELDS``.

Execution authority stays ``DENIED``. Production authority stays ``DENIED``.
Source authenticity stays ``NOT_ESTABLISHED``. Request coverage stays
``NOT_ESTABLISHED``. This module does not verify a JWT, call AWS, call a
model, open a planning session, or satisfy the execution-policy
authenticated-principal requirement.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from enum import Enum

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
    _validate_utc,
)
from wilvor_ai.master_contracts import MasterProductionAuthority, MasterPurpose
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_gateway_identity import (
    MAX_AUTHORIZER_SCOPES,
    MAX_EPOCH,
    ApplicationPrincipalRequirement,
    AuthorizerClaimMetadata,
    ClaimedRequestIdentity,
    GatewayInvocationProvenance,
    MasterGatewayIdentityAssessment,
    MasterGatewayIdentityConfiguration,
    RawAuthorizationHeader,
    UnresolvedIngressAttestation,
)
from wilvor_ai.master_planning_session import (
    MasterPlanningSessionRecord,
    MasterPlanningSessionRegistry,
)
from wilvor_ai.master_routing import (
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
)


MASTER_APPLICATION_PRINCIPAL_SCHEMA_VERSION = "wilvor.ai.master_application_principal.v1"

# No constructor and no environment variable assigns these.
PRODUCTION_PRINCIPAL_ISSUANCE_AVAILABLE = False
PRODUCTION_PRINCIPAL_AVAILABLE = False
TEST_DOUBLE_ENVIRONMENT_ACTIVATION_AVAILABLE = False

LAMBDA_INVOKE_BOUNDARY = (
    "A directly invoked Lambda can receive a forged API Gateway HTTP API v2 "
    "event. API Gateway JWT validation is an edge control and is not "
    "cryptographic attestation attached to that event. This module does not "
    "read the Lambda resource policy and does not decide who can invoke the "
    "function. Production principal issuance stays unavailable until a reviewed "
    "host mechanism, outside the event, establishes that the invoker is the "
    "reviewed API Gateway integration."
)

_SCHEMA = MASTER_APPLICATION_PRINCIPAL_SCHEMA_VERSION
_ISSUER = re.compile(
    r"https://[A-Za-z0-9.-]{1,253}(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~-]{1,64})*"
)
_AUDIENCE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SCOPE = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
_METHOD = re.compile(r"^[A-Z]{3,10}$")
_RAW_PATH = re.compile(r"^/(?:[A-Za-z0-9._~-]+)(?:/[A-Za-z0-9._~-]+)*$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._~=+/-]{1,128}$")
_TOKEN = re.compile(rf"^[A-Za-z0-9._:@|/+~=-]{{1,{PROVENANCE_TOKEN_MAX_LENGTH}}}$")
_REJECTED_AUDIENCE = frozenset({"*", "null", "undefined"})
_FLAG_WORDS = frozenset(
    {
        "true",
        "false",
        "authenticated",
        "allow",
        "allowed",
        "permit",
        "permitted",
        "deny",
        "denied",
        "admin",
        "production",
        "test",
        "caller",
        "event",
        "header",
        "jwt",
        "environment",
        "none",
        "null",
        "undefined",
    }
)
_POLICY_WORDS = frozenset({"allow", "allowed", "permit", "permitted", "deny", "denied"})
_ABSENT_CLAIM_KEYS = frozenset(
    {
        "tenant",
        "tenants",
        "tenant_id",
        "role",
        "roles",
        "role_arn",
        "groups",
        "cognito:groups",
    }
)
_FORGED_CLAIM_KEYS = frozenset(
    {
        "iss",
        "sub",
        "aud",
        "scope",
        "scp",
        "authorizer",
        "requestcontext",
        "jwt",
        "claims",
    }
)
_HEADER_KEYS = frozenset({"authorization", "headers"})
_ENV_KEYWORDS = frozenset(
    {"environ", "environment", "env", "test_mode", "wilvor_test_mode"}
)
_DEADLINE_KEYWORDS = frozenset({"deadline_utc", "deadline", "application_deadline"})
_LABEL_KEYWORDS = frozenset({"principal", "principal_label", "subject", "sub"})
_SESSION_TYPES = (MasterPlanningSessionRecord, MasterPlanningSessionRegistry)
_OBSERVE = object()
_MINT = object()
_SEAL_MARKER = object()
_PORT_LOCK = threading.Lock()
_PORT_SEQUENCE = 0


class PrincipalRecordKind(str, Enum):
    """How a value in this boundary should be read.

    Host-issued and source-authenticated are not members. Neither can be
    stored on an observation, an identity input, or a synthetic admission.
    """

    DESCRIPTIVE_OBSERVATION = "DESCRIPTIVE_OBSERVATION"
    HOST_IDENTITY_INPUT = "HOST_IDENTITY_INPUT"
    SYNTHETIC_TEST_ADMISSION = "SYNTHETIC_TEST_ADMISSION"
    CONSUMED_SYNTHETIC_ADMISSION = "CONSUMED_SYNTHETIC_ADMISSION"


class AuthorizationProvenanceStanding(str, Enum):
    """Provenance recorded by this boundary. Neither value is production."""

    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    CONTRACT_TEST_HOST = "CONTRACT_TEST_HOST"


class SourceClaimPresence(str, Enum):
    """Tenant and role claim sources. Present is not a member."""

    ABSENT = "ABSENT"


class HostProofStanding(str, Enum):
    """The reviewed host mechanism. Resolved is not a member."""

    UNRESOLVED = "UNRESOLVED"


class HostIngressProofRequirement(str, Enum):
    """Proof the future application host must provide. All stay unmet."""

    PROTECTED_GATEWAY_INTEGRATION_REVIEW = "PROTECTED_GATEWAY_INTEGRATION_REVIEW"
    LAMBDA_INVOCATION_BOUNDARY_ATTESTATION = "LAMBDA_INVOCATION_BOUNDARY_ATTESTATION"
    INDEPENDENT_TOKEN_VERIFICATION = "INDEPENDENT_TOKEN_VERIFICATION"
    ISSUER_BINDING = "ISSUER_BINDING"
    AUDIENCE_BINDING = "AUDIENCE_BINDING"
    SUBJECT_FROM_VERIFIED_AUTHORITY = "SUBJECT_FROM_VERIFIED_AUTHORITY"
    SCOPES_FROM_VERIFIED_AUTHORITY = "SCOPES_FROM_VERIFIED_AUTHORITY"
    REQUEST_AND_SESSION_BINDING = "REQUEST_AND_SESSION_BINDING"
    PURPOSE_AND_ROUTE_BINDING = "PURPOSE_AND_ROUTE_BINDING"
    LIFETIME_BINDING = "LIFETIME_BINDING"
    POLICY_REVISION_BINDING = "POLICY_REVISION_BINDING"
    AUTHORIZATION_PROVENANCE = "AUTHORIZATION_PROVENANCE"


HOST_INGRESS_PROOF_REQUIREMENTS = tuple(HostIngressProofRequirement)


class AbsentSourceClaim(str, Enum):
    """Claim families this deployment does not have."""

    TENANT = "TENANT"
    ROLE = "ROLE"


ABSENT_SOURCE_CLAIMS = tuple(AbsentSourceClaim)
ABSENT_SOURCE_CLAIM_NOTES = {
    AbsentSourceClaim.TENANT: (
        "No tenant claim source is configured. Identity configuration is "
        "issuer, audience, subject, and scope. A tenant value is rejected."
    ),
    AbsentSourceClaim.ROLE: (
        "No application role system is configured. master_gateway_identity "
        "rejects role, roles, groups, and cognito:groups. A role value is rejected."
    ),
}


class ApplicationPrincipalLimitation(str, Enum):
    """Standing limits. The full set is present on every issued handle."""

    STRUCTURAL_JWT_ASSESSMENT_IS_NOT_A_PRINCIPAL = (
        "STRUCTURAL_JWT_ASSESSMENT_IS_NOT_A_PRINCIPAL"
    )
    AUTHORIZATION_HEADER_IS_NOT_A_PRINCIPAL = "AUTHORIZATION_HEADER_IS_NOT_A_PRINCIPAL"
    EVENT_DICTIONARY_IS_NOT_AUTHORITY = "EVENT_DICTIONARY_IS_NOT_AUTHORITY"
    CALLER_IDENTITY_INPUT_IS_NOT_HOST_ISSUED = "CALLER_IDENTITY_INPUT_IS_NOT_HOST_ISSUED"
    SESSION_MEMBERSHIP_IS_NOT_PROOF = "SESSION_MEMBERSHIP_IS_NOT_PROOF"
    MATCHING_IDENTIFIER_IS_NOT_PROOF = "MATCHING_IDENTIFIER_IS_NOT_PROOF"
    SYNTHETIC_ADMISSION_IS_NOT_A_PRODUCTION_PRINCIPAL = (
        "SYNTHETIC_ADMISSION_IS_NOT_A_PRODUCTION_PRINCIPAL"
    )
    TEST_DOUBLE_IS_NOT_ENVIRONMENT_ACTIVATED = "TEST_DOUBLE_IS_NOT_ENVIRONMENT_ACTIVATED"
    DIRECT_LAMBDA_INVOKE_IS_NOT_ATTESTATION = "DIRECT_LAMBDA_INVOKE_IS_NOT_ATTESTATION"
    TENANT_CLAIM_SOURCE_ABSENT = "TENANT_CLAIM_SOURCE_ABSENT"
    ROLE_CLAIM_SOURCE_ABSENT = "ROLE_CLAIM_SOURCE_ABSENT"
    REQUEST_DEADLINE_IS_NOT_PRINCIPAL_LIFETIME = "REQUEST_DEADLINE_IS_NOT_PRINCIPAL_LIFETIME"
    HOST_ISSUED_PRINCIPAL_UNAVAILABLE = "HOST_ISSUED_PRINCIPAL_UNAVAILABLE"
    SOURCE_AUTHENTICATION_NOT_ESTABLISHED = "SOURCE_AUTHENTICATION_NOT_ESTABLISHED"
    EXECUTION_AUTHORITY_REMAINS_DENIED = "EXECUTION_AUTHORITY_REMAINS_DENIED"
    PRODUCTION_AUTHORITY_REMAINS_DENIED = "PRODUCTION_AUTHORITY_REMAINS_DENIED"
    SOURCE_AUTHENTICITY_REMAINS_NOT_ESTABLISHED = (
        "SOURCE_AUTHENTICITY_REMAINS_NOT_ESTABLISHED"
    )
    REQUEST_COVERAGE_REMAINS_NOT_ESTABLISHED = "REQUEST_COVERAGE_REMAINS_NOT_ESTABLISHED"
    PRODUCTION_ISSUANCE_UNAVAILABLE = "PRODUCTION_ISSUANCE_UNAVAILABLE"


APPLICATION_PRINCIPAL_LIMITATIONS = tuple(ApplicationPrincipalLimitation)

PRINCIPAL_ENVELOPE_FIELDS = (
    "master_request_id",
    "planning_session_id",
    "purpose",
    "principal_ref",
    "policy_revision",
    "correlation_id",
    "accepted_at_epoch",
    "expires_at_epoch",
    "not_before_epoch",
)
NON_PRINCIPAL_ENVELOPE_FIELDS = (
    "step_id",
    "invocation_id",
    "specialist",
    "capability",
    "resource_scope",
    "region_scope",
    "historical_window",
    "decision_target_scope",
    "deadline_utc",
    "idempotency_key",
    "max_model_turns",
    "max_tool_calls",
)
CLOCK_FIELD_MAPPING = (
    (
        "accepted_at_utc",
        "accepted_at_epoch",
        "Authority issued-at as Unix seconds. Not application_request_time_utc.",
    ),
    (
        "deadline_utc",
        "expires_at_epoch",
        "Not accepted as a UTC deadline. Expiry is the authority exp claim.",
    ),
    (
        "gateway_evaluation_clock",
        "gateway_claim_evaluated_at_epoch",
        "Copied from the gateway assessment. Not the principal clock.",
    ),
    (
        "principal_evaluation_clock",
        "principal_evaluated_at_epoch",
        "Host clock passed to admit and present. Not requestContext.timeEpoch.",
    ),
)
REJECTED_AUTHORITY_SOURCES = (
    "event body",
    "Authorization header",
    "requestContext.authorizer.jwt",
    "user or model text",
    "session membership",
    "matching identifiers",
    "caller-built identity input",
    "synthetic admission",
    "serialized descriptor",
    "environment variable",
    "policy-check result",
    "structural gateway assessment",
)


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _closed(value: object, expected: object, code: str) -> None:
    if value is not expected:
        _fail(code)


def _epoch(value: object, code: str) -> int:
    if type(value) is not int or value < 0 or value > MAX_EPOCH:
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
    if type(value) is not str or _AUDIENCE.fullmatch(value) is None or value in _REJECTED_AUDIENCE:
        _fail(code)
    return value


def _scope_token(value: object) -> str:
    if type(value) is not str or _SCOPE.fullmatch(value) is None or value == "*":
        _fail("malformed_scopes")
    return value


def _scopes(value: object) -> tuple[str, ...]:
    if type(value) is not tuple or not value or len(value) > MAX_AUTHORIZER_SCOPES:
        _fail("malformed_scopes")
    parsed = tuple(_scope_token(item) for item in value)
    if len(set(parsed)) != len(parsed):
        _fail("malformed_scopes")
    return parsed


def _request_id(value: object, code: str) -> str:
    if type(value) is not str or _REQUEST_ID.fullmatch(value) is None:
        _fail(code)
    _no_controls(value, code)
    return value


def _token(value: object, code: str) -> str:
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        _fail(code)
    _no_controls(value, code)
    if value.casefold() in _FLAG_WORDS:
        _fail(code)
    return value


def _route(method: object, raw_path: object, route_key: object) -> tuple[str, str, str]:
    if type(method) is not str or _METHOD.fullmatch(method) is None:
        _fail("unexpected_method")
    if type(raw_path) is not str or _RAW_PATH.fullmatch(raw_path) is None:
        _fail("unexpected_route")
    if route_key != f"{method} {raw_path}":
        _fail("unexpected_route")
    return method, raw_path, route_key


def _utc_hint(value: object) -> str | None:
    if value is None:
        return None
    if _validate_utc(value, "application_request_time_utc"):
        _fail("invalid_application_request_time")
    return value


def _names(mapping: dict[object, object]) -> set[str]:
    return {key.casefold() for key in mapping if type(key) is str}


def _mapping_code(candidate: dict[object, object]) -> str:
    names = _names(candidate)
    if names & _ABSENT_CLAIM_KEYS:
        return "absent_source_claim"
    if names & _FORGED_CLAIM_KEYS:
        return "forged_gateway_claims"
    if names & _HEADER_KEYS:
        return "header_only_identity"
    return "arbitrary_mapping_not_authority"


def _keyword_code(kwargs: dict[str, object]) -> str:
    names = {key.casefold() for key in kwargs}
    if names & _ABSENT_CLAIM_KEYS:
        return "absent_source_claim"
    if names & _ENV_KEYWORDS:
        return "environment_not_principal_authority"
    if names & _DEADLINE_KEYWORDS:
        return "request_deadline_not_principal_lifetime"
    if names & _LABEL_KEYWORDS:
        return "principal_label_not_authority"
    return "caller_keyword_not_authority"


class _Sealed:
    """Slots object that cannot be mutated or copied into a second grant."""

    __slots__ = ("_sealed",)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            _fail("principal_descriptor_immutable")
        object.__setattr__(self, name, value)

    def __copy__(self) -> object:
        _fail("copied_principal_not_accepted")
        raise AssertionError("unreachable")

    def __deepcopy__(self, memo: object) -> object:
        _fail("copied_principal_not_accepted")
        raise AssertionError("unreachable")

    def __reduce__(self) -> object:
        _fail("copied_principal_not_accepted")
        raise AssertionError("unreachable")

    def __bool__(self) -> bool:
        return False


class UntrustedGatewayClaimObservation(_Sealed):
    """Claim fields copied from one structural assessment. Not a principal."""

    __slots__ = (
        "schema_version",
        "record_kind",
        "issuer",
        "audience",
        "subject",
        "scopes",
        "gateway_request_id",
        "method",
        "raw_path",
        "route_key",
        "expires_at_epoch",
        "not_before_epoch",
        "issued_at_epoch",
        "gateway_claim_evaluated_at_epoch",
        "authorization_header_present",
        "authorization_header_accepted",
        "structural_assessment_accepted_as_proof",
        "provenance_standing",
        "tenant_presence",
        "role_presence",
        "execution_authority",
        "production_authority",
        "source_authenticity",
        "request_coverage",
        "limitations",
    )

    def __init__(self, token: object = None, **fields: object) -> None:
        if token is not _OBSERVE or set(fields) != {
            "issuer",
            "audience",
            "subject",
            "scopes",
            "gateway_request_id",
            "method",
            "raw_path",
            "route_key",
            "expires_at_epoch",
            "not_before_epoch",
            "issued_at_epoch",
            "gateway_claim_evaluated_at_epoch",
            "authorization_header_present",
        }:
            _fail("observation_requires_gateway_assessment")
        self.schema_version = _SCHEMA
        self.record_kind = PrincipalRecordKind.DESCRIPTIVE_OBSERVATION
        self.issuer = fields["issuer"]
        self.audience = fields["audience"]
        self.subject = fields["subject"]
        self.scopes = fields["scopes"]
        self.gateway_request_id = fields["gateway_request_id"]
        self.method = fields["method"]
        self.raw_path = fields["raw_path"]
        self.route_key = fields["route_key"]
        self.expires_at_epoch = fields["expires_at_epoch"]
        self.not_before_epoch = fields["not_before_epoch"]
        self.issued_at_epoch = fields["issued_at_epoch"]
        self.gateway_claim_evaluated_at_epoch = fields["gateway_claim_evaluated_at_epoch"]
        self.authorization_header_present = fields["authorization_header_present"]
        self.authorization_header_accepted = False
        self.structural_assessment_accepted_as_proof = False
        self.provenance_standing = AuthorizationProvenanceStanding.NOT_ESTABLISHED
        self.tenant_presence = SourceClaimPresence.ABSENT
        self.role_presence = SourceClaimPresence.ABSENT
        self.execution_authority = MasterRouteExecutionAuthority.DENIED
        self.production_authority = MasterProductionAuthority.DENIED
        self.source_authenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
        self.request_coverage = MasterRequestCoverage.NOT_ESTABLISHED
        self.limitations = APPLICATION_PRINCIPAL_LIMITATIONS
        self._sealed = True

    def __repr__(self) -> str:
        return (
            "UntrustedGatewayClaimObservation("
            f"request_id={self.gateway_request_id!r}, "
            "structural_assessment_accepted_as_proof=False)"
        )


def observe_untrusted_gateway_claims(source: object) -> UntrustedGatewayClaimObservation:
    """Copy structural claim fields. The copy does not authenticate the caller."""

    if type(source) is not MasterGatewayIdentityAssessment:
        _fail(_rejection_code(source))
    claims = source.authorizer_claims
    request = source.claimed_request
    _closed(claims.authenticates_caller, False, "structural_jwt_assessment_not_principal")
    _closed(claims.signature_verified, False, "structural_jwt_assessment_not_principal")
    _closed(source.application_principal.issued, False, "application_principal_not_issued")
    _closed(
        source.ingress_attestation.production_issuance_available,
        False,
        "production_principal_issuance_unavailable",
    )
    _closed(source.production_authenticated, False, "structural_jwt_assessment_not_principal")
    _closed(source.execution_permitted, False, "structural_jwt_assessment_not_principal")
    _closed(source.raw_authorization.accepted_as_authentication, False, "header_only_identity")
    return UntrustedGatewayClaimObservation(
        _OBSERVE,
        issuer=claims.issuer,
        audience=claims.audience,
        subject=claims.subject,
        scopes=tuple(claims.scopes),
        gateway_request_id=request.request_id,
        method=request.method,
        raw_path=request.raw_path,
        route_key=request.route_key,
        expires_at_epoch=claims.expires_at_epoch,
        not_before_epoch=claims.not_before_epoch,
        issued_at_epoch=claims.issued_at_epoch,
        gateway_claim_evaluated_at_epoch=source.evaluated_at_epoch,
        authorization_header_present=source.raw_authorization.present,
    )


@dataclass(frozen=True, slots=True)
class IndependentlyHostAuthenticatedIdentityInput:
    """Values a reviewed host would have to establish outside the event.

    Construction does not authenticate the subject and does not issue a
    production or synthetic principal. The nonproduction proof port may
    evaluate an instance. Production issuance rejects every instance.
    """

    issuer: str
    audience: str
    subject: str
    scopes: tuple[str, ...]
    gateway_request_id: str
    master_request_id: str
    planning_session_id: str
    correlation_id: str
    method: str
    raw_path: str
    route_key: str
    purpose: MasterPurpose
    policy_revision: str
    authorization_provenance: str
    proof_id: str
    issued_at_epoch: int
    not_before_epoch: int
    expires_at_epoch: int
    application_request_time_utc: str | None = None
    schema_version: str = _SCHEMA
    record_kind: PrincipalRecordKind = PrincipalRecordKind.HOST_IDENTITY_INPUT
    provenance_standing: AuthorizationProvenanceStanding = (
        AuthorizationProvenanceStanding.NOT_ESTABLISHED
    )
    tenant_presence: SourceClaimPresence = SourceClaimPresence.ABSENT
    role_presence: SourceClaimPresence = SourceClaimPresence.ABSENT
    authenticates_caller: bool = False
    production_principal: bool = False
    reviewed_host_mechanism: bool = False
    session_membership_accepted_as_proof: bool = False
    matching_identifier_accepted_as_proof: bool = False
    structural_assessment_accepted_as_proof: bool = False
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    execution_permitted: bool = False
    limitations: tuple[ApplicationPrincipalLimitation, ...] = APPLICATION_PRINCIPAL_LIMITATIONS

    def __post_init__(self) -> None:
        _issuer(self.issuer, "invalid_issuer")
        _audience(self.audience, "invalid_audience")
        _token(self.subject, "invalid_subject")
        _scopes(self.scopes)
        _request_id(self.gateway_request_id, "invalid_gateway_request_id")
        _token(self.master_request_id, "invalid_master_request_id")
        _token(self.planning_session_id, "invalid_planning_session_id")
        _token(self.correlation_id, "invalid_correlation_id")
        _route(self.method, self.raw_path, self.route_key)
        if type(self.purpose) is not MasterPurpose:
            _fail("invalid_purpose")
        _token(self.policy_revision, "invalid_policy_revision")
        _token(self.authorization_provenance, "invalid_authorization_provenance")
        _token(self.proof_id, "invalid_proof_id")
        issued_at = _epoch(self.issued_at_epoch, "invalid_lifetime")
        not_before = _epoch(self.not_before_epoch, "invalid_lifetime")
        expires_at = _epoch(self.expires_at_epoch, "invalid_lifetime")
        if not_before >= expires_at or issued_at > expires_at:
            _fail("invalid_lifetime")
        _utc_hint(self.application_request_time_utc)
        identifiers = (
            self.proof_id,
            self.subject,
            self.gateway_request_id,
            self.master_request_id,
            self.planning_session_id,
            self.correlation_id,
            self.authorization_provenance,
            self.policy_revision,
        )
        if len(set(identifiers)) != len(identifiers):
            _fail("matching_identifier_not_proof")
        if self.schema_version != _SCHEMA:
            _fail("invalid_identity_input")
        _closed(self.record_kind, PrincipalRecordKind.HOST_IDENTITY_INPUT, "identity_input_not_an_admission")
        _closed(
            self.provenance_standing,
            AuthorizationProvenanceStanding.NOT_ESTABLISHED,
            "caller_built_identity_not_host_issued",
        )
        _closed(self.tenant_presence, SourceClaimPresence.ABSENT, "absent_source_claim")
        _closed(self.role_presence, SourceClaimPresence.ABSENT, "absent_source_claim")
        _closed(self.authenticates_caller, False, "caller_flag_not_authority")
        _closed(self.production_principal, False, "caller_flag_not_authority")
        _closed(self.reviewed_host_mechanism, False, "reviewed_host_authority_not_wired")
        _closed(self.session_membership_accepted_as_proof, False, "session_membership_not_proof")
        _closed(self.matching_identifier_accepted_as_proof, False, "matching_identifier_not_proof")
        _closed(
            self.structural_assessment_accepted_as_proof,
            False,
            "structural_jwt_assessment_not_principal",
        )
        _closed(self.execution_authority, MasterRouteExecutionAuthority.DENIED, "execution_authority_not_granted")
        _closed(self.production_authority, MasterProductionAuthority.DENIED, "execution_authority_not_granted")
        _closed(
            self.source_authenticity,
            MasterSourceAuthenticity.NOT_ESTABLISHED,
            "source_authenticity_not_established",
        )
        _closed(self.request_coverage, MasterRequestCoverage.NOT_ESTABLISHED, "request_coverage_not_established")
        _closed(self.execution_permitted, False, "execution_permission_not_available")
        if self.limitations != APPLICATION_PRINCIPAL_LIMITATIONS:
            _fail("invalid_identity_input")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class HostIngressProofChecklist:
    """Every host proof stays unmet. No field records a completed review."""

    standing: HostProofStanding = HostProofStanding.UNRESOLVED
    satisfied: bool = False
    requirements: tuple[HostIngressProofRequirement, ...] = HOST_INGRESS_PROOF_REQUIREMENTS

    def __post_init__(self) -> None:
        _closed(self.standing, HostProofStanding.UNRESOLVED, "reviewed_host_authority_not_wired")
        _closed(self.satisfied, False, "reviewed_host_authority_not_wired")
        if self.requirements != HOST_INGRESS_PROOF_REQUIREMENTS:
            _fail("reviewed_host_authority_not_wired")

    def __bool__(self) -> bool:
        return False


class PrincipalScopeBinding(_Sealed):
    """Scope taken from the host identity input after consistency checks.

    Not an execution grant. A public constructor cannot mint one.
    """

    __slots__ = (
        "issuer",
        "audience",
        "subject",
        "scopes",
        "route_key",
        "method",
        "raw_path",
        "purpose",
        "policy_revision",
        "planning_session_id",
        "master_request_id",
        "correlation_id",
        "gateway_request_id",
        "production_scope_grant",
    )

    def __init__(self, token: object = None, **fields: object) -> None:
        if token is not _MINT or set(fields) != {
            "issuer",
            "audience",
            "subject",
            "scopes",
            "route_key",
            "method",
            "raw_path",
            "purpose",
            "policy_revision",
            "planning_session_id",
            "master_request_id",
            "correlation_id",
            "gateway_request_id",
        }:
            _fail("public_constructor_not_production_principal")
        self.issuer = fields["issuer"]
        self.audience = fields["audience"]
        self.subject = fields["subject"]
        self.scopes = fields["scopes"]
        self.route_key = fields["route_key"]
        self.method = fields["method"]
        self.raw_path = fields["raw_path"]
        self.purpose = fields["purpose"]
        self.policy_revision = fields["policy_revision"]
        self.planning_session_id = fields["planning_session_id"]
        self.master_request_id = fields["master_request_id"]
        self.correlation_id = fields["correlation_id"]
        self.gateway_request_id = fields["gateway_request_id"]
        self.production_scope_grant = False
        self._sealed = True

    def __repr__(self) -> str:
        return (
            "PrincipalScopeBinding("
            f"route_key={self.route_key!r}, purpose={self.purpose.value}, "
            "production_scope_grant=False)"
        )


class ServerOwnedPrincipalDescriptor(_Sealed):
    """In-process synthetic admission bound by a nonproduction proof port.

    The public constructor does not accept caller strings. ``record_kind``
    stays ``SYNTHETIC_TEST_ADMISSION``. Consumption state lives on the port
    that minted the handle.
    """

    __slots__ = (
        "schema_version",
        "record_kind",
        "principal_ref",
        "scope",
        "proof_id",
        "authorization_provenance",
        "provenance_standing",
        "accepted_at_epoch",
        "not_before_epoch",
        "expires_at_epoch",
        "principal_evaluated_at_epoch",
        "gateway_claim_evaluated_at_epoch",
        "application_request_time_utc",
        "tenant_presence",
        "role_presence",
        "production_principal",
        "authenticates_caller",
        "session_membership_accepted_as_proof",
        "matching_identifier_accepted_as_proof",
        "structural_assessment_accepted_as_proof",
        "environment_activated",
        "execution_authority",
        "production_authority",
        "source_authenticity",
        "request_coverage",
        "execution_permitted",
        "limitations",
    )

    def __init__(self, token: object = None, **fields: object) -> None:
        scope = fields.get("scope")
        if token is not _MINT or type(scope) is not PrincipalScopeBinding or set(fields) != {
            "principal_ref",
            "scope",
            "proof_id",
            "authorization_provenance",
            "accepted_at_epoch",
            "not_before_epoch",
            "expires_at_epoch",
            "principal_evaluated_at_epoch",
            "gateway_claim_evaluated_at_epoch",
            "application_request_time_utc",
        }:
            _fail("public_constructor_not_production_principal")
        self.schema_version = _SCHEMA
        self.record_kind = PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION
        self.principal_ref = fields["principal_ref"]
        self.scope = scope
        self.proof_id = fields["proof_id"]
        self.authorization_provenance = fields["authorization_provenance"]
        self.provenance_standing = AuthorizationProvenanceStanding.CONTRACT_TEST_HOST
        self.accepted_at_epoch = fields["accepted_at_epoch"]
        self.not_before_epoch = fields["not_before_epoch"]
        self.expires_at_epoch = fields["expires_at_epoch"]
        self.principal_evaluated_at_epoch = fields["principal_evaluated_at_epoch"]
        self.gateway_claim_evaluated_at_epoch = fields["gateway_claim_evaluated_at_epoch"]
        self.application_request_time_utc = fields["application_request_time_utc"]
        self.tenant_presence = SourceClaimPresence.ABSENT
        self.role_presence = SourceClaimPresence.ABSENT
        self.production_principal = False
        self.authenticates_caller = False
        self.session_membership_accepted_as_proof = False
        self.matching_identifier_accepted_as_proof = False
        self.structural_assessment_accepted_as_proof = False
        self.environment_activated = False
        self.execution_authority = MasterRouteExecutionAuthority.DENIED
        self.production_authority = MasterProductionAuthority.DENIED
        self.source_authenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
        self.request_coverage = MasterRequestCoverage.NOT_ESTABLISHED
        self.execution_permitted = False
        self.limitations = APPLICATION_PRINCIPAL_LIMITATIONS
        self._sealed = True

    @property
    def subject(self) -> str:
        return self.scope.subject

    @property
    def scopes(self) -> tuple[str, ...]:
        return self.scope.scopes

    @property
    def issuer(self) -> str:
        return self.scope.issuer

    @property
    def audience(self) -> str:
        return self.scope.audience

    @property
    def route_key(self) -> str:
        return self.scope.route_key

    @property
    def purpose(self) -> MasterPurpose:
        return self.scope.purpose

    @property
    def policy_revision(self) -> str:
        return self.scope.policy_revision

    @property
    def planning_session_id(self) -> str:
        return self.scope.planning_session_id

    @property
    def master_request_id(self) -> str:
        return self.scope.master_request_id

    @property
    def correlation_id(self) -> str:
        return self.scope.correlation_id

    def __repr__(self) -> str:
        return (
            "ServerOwnedPrincipalDescriptor("
            f"principal_ref={self.principal_ref!r}, "
            "record_kind=SYNTHETIC_TEST_ADMISSION, production_principal=False)"
        )


class _Stored:
    """Port-owned binding. Field writes on the public handle do not update it."""

    __slots__ = (
        "descriptor",
        "subject",
        "scopes",
        "issuer",
        "audience",
        "route_key",
        "method",
        "raw_path",
        "purpose",
        "policy_revision",
        "planning_session_id",
        "master_request_id",
        "correlation_id",
        "gateway_request_id",
        "issued_at_epoch",
        "not_before_epoch",
        "expires_at_epoch",
        "proof_id",
        "authorization_provenance",
        "principal_ref",
    )

    def __init__(
        self,
        descriptor: ServerOwnedPrincipalDescriptor,
        authority: IndependentlyHostAuthenticatedIdentityInput,
        principal_ref: str,
    ) -> None:
        self.descriptor = descriptor
        self.subject = authority.subject
        self.scopes = authority.scopes
        self.issuer = authority.issuer
        self.audience = authority.audience
        self.route_key = authority.route_key
        self.method = authority.method
        self.raw_path = authority.raw_path
        self.purpose = authority.purpose
        self.policy_revision = authority.policy_revision
        self.planning_session_id = authority.planning_session_id
        self.master_request_id = authority.master_request_id
        self.correlation_id = authority.correlation_id
        self.gateway_request_id = authority.gateway_request_id
        self.issued_at_epoch = authority.issued_at_epoch
        self.not_before_epoch = authority.not_before_epoch
        self.expires_at_epoch = authority.expires_at_epoch
        self.proof_id = authority.proof_id
        self.authorization_provenance = authority.authorization_provenance
        self.principal_ref = principal_ref


class NonproductionContractSeal:
    """In-process marker. An environment variable cannot construct one."""

    __slots__ = ("_marker",)

    def __init__(self, marker: object) -> None:
        if marker is not _SEAL_MARKER:
            _fail("nonproduction_seal_not_environment_activated")
        self._marker = marker

    def __bool__(self) -> bool:
        return False


def mint_nonproduction_contract_seal() -> NonproductionContractSeal:
    """Mint a contract-test seal. This does not read the process environment."""

    return NonproductionContractSeal(_SEAL_MARKER)


class NonproductionPrincipalProofPort:
    """Deterministic contract-test double for principal binding rules.

    Not a production issuer. Not installed by an environment variable.
    Admissions it mints stay synthetic.
    """

    __slots__ = (
        "_configuration",
        "_policy_revision",
        "_authorization_provenance",
        "_lock",
        "_proofs",
        "_issued",
        "_consumed",
        "_sequence",
        "_port_id",
    )

    def __init__(
        self,
        configuration: object,
        *,
        policy_revision: object,
        authorization_provenance: object,
        seal: object,
    ) -> None:
        if type(seal) is not NonproductionContractSeal:
            if type(seal) is str or type(seal) is bool or seal is None:
                _fail("environment_not_principal_authority")
            _fail("nonproduction_seal_required")
        if type(configuration) is not MasterGatewayIdentityConfiguration:
            _fail("configuration_not_taken_from_request")
        revision = _token(policy_revision, "invalid_policy_revision")
        provenance = _token(authorization_provenance, "invalid_authorization_provenance")
        if revision == provenance:
            _fail("matching_identifier_not_proof")
        self._configuration = configuration
        self._policy_revision = revision
        self._authorization_provenance = provenance
        self._lock = threading.Lock()
        self._proofs: set[str] = set()
        self._issued: dict[int, _Stored] = {}
        self._consumed: set[int] = set()
        self._sequence = 0
        global _PORT_SEQUENCE
        with _PORT_LOCK:
            _PORT_SEQUENCE += 1
            self._port_id = _PORT_SEQUENCE

    def __bool__(self) -> bool:
        return False

    def __copy__(self) -> object:
        _fail("proof_port_not_copyable")
        raise AssertionError("unreachable")

    def __deepcopy__(self, memo: object) -> object:
        _fail("proof_port_not_copyable")
        raise AssertionError("unreachable")

    def admit(
        self,
        observation: object,
        authority: object,
        *,
        evaluation_epoch: object,
        session: object = None,
        principal_label: object = None,
        event: object = None,
        deadline_utc: object = None,
        tenant: object = None,
        role: object = None,
    ) -> ServerOwnedPrincipalDescriptor:
        """Bind a synthetic admission from the host input, not the event.

        ``session``, ``principal_label``, ``event``, ``deadline_utc``,
        ``tenant``, and ``role`` are rejected when supplied. Effective
        subject and scopes come from ``authority``.
        """

        _reject_controls(session, principal_label, event, deadline_utc, tenant, role)
        if type(observation) is not UntrustedGatewayClaimObservation:
            _fail(_rejection_code(observation))
        if type(authority) is not IndependentlyHostAuthenticatedIdentityInput:
            _fail(_rejection_code(authority))
        evaluated = _epoch(evaluation_epoch, "invalid_evaluation_epoch")
        with self._lock:
            _require_consistent(
                self._configuration,
                observation,
                authority,
                evaluated,
                self._policy_revision,
                self._authorization_provenance,
            )
            if authority.proof_id in self._proofs:
                _fail("replayed_principal")
            self._sequence += 1
            principal_ref = f"synthetic-{self._port_id}-{self._sequence}"
            scope = PrincipalScopeBinding(
                _MINT,
                issuer=authority.issuer,
                audience=authority.audience,
                subject=authority.subject,
                scopes=authority.scopes,
                route_key=authority.route_key,
                method=authority.method,
                raw_path=authority.raw_path,
                purpose=authority.purpose,
                policy_revision=authority.policy_revision,
                planning_session_id=authority.planning_session_id,
                master_request_id=authority.master_request_id,
                correlation_id=authority.correlation_id,
                gateway_request_id=authority.gateway_request_id,
            )
            descriptor = ServerOwnedPrincipalDescriptor(
                _MINT,
                principal_ref=principal_ref,
                scope=scope,
                proof_id=authority.proof_id,
                authorization_provenance=authority.authorization_provenance,
                accepted_at_epoch=authority.issued_at_epoch,
                not_before_epoch=authority.not_before_epoch,
                expires_at_epoch=authority.expires_at_epoch,
                principal_evaluated_at_epoch=evaluated,
                gateway_claim_evaluated_at_epoch=observation.gateway_claim_evaluated_at_epoch,
                application_request_time_utc=authority.application_request_time_utc,
            )
            self._proofs.add(authority.proof_id)
            self._issued[id(descriptor)] = _Stored(descriptor, authority, principal_ref)
            return descriptor

    def present(
        self,
        descriptor: object,
        *,
        evaluation_epoch: object,
        planning_session_id: object,
        master_request_id: object,
        correlation_id: object,
        purpose: object,
        audience: object,
        route_key: object,
        acting_subject: object,
        gateway_request_id: object,
        principal_label: object = None,
        deadline_utc: object = None,
        tenant: object = None,
        role: object = None,
    ) -> ServerOwnedPrincipalDescriptor:
        """Consume one synthetic admission if the presented binding matches.

        A match still does not create a production principal.
        """

        _reject_controls(None, principal_label, None, deadline_utc, tenant, role)
        if type(descriptor) is not ServerOwnedPrincipalDescriptor:
            _fail(_rejection_code(descriptor))
        evaluated = _epoch(evaluation_epoch, "invalid_evaluation_epoch")
        with self._lock:
            stored = self._issued.get(id(descriptor))
            if stored is None or stored.descriptor is not descriptor:
                _fail("copied_principal_not_accepted")
            if id(descriptor) in self._consumed:
                _fail("replayed_principal")
            _require_handle_intact(descriptor, stored)
            _require_lifetime(stored.issued_at_epoch, stored.not_before_epoch, stored.expires_at_epoch, evaluated)
            _require_presentation(
                stored,
                planning_session_id=planning_session_id,
                master_request_id=master_request_id,
                correlation_id=correlation_id,
                purpose=purpose,
                audience=audience,
                route_key=route_key,
                acting_subject=acting_subject,
                gateway_request_id=gateway_request_id,
            )
            self._consumed.add(id(descriptor))
            return descriptor

    def lifecycle(self, descriptor: object) -> PrincipalRecordKind:
        """Report synthetic or consumed. Neither value is host-issued."""

        if type(descriptor) is not ServerOwnedPrincipalDescriptor:
            _fail(_rejection_code(descriptor))
        with self._lock:
            stored = self._issued.get(id(descriptor))
            if stored is None or stored.descriptor is not descriptor:
                _fail("copied_principal_not_accepted")
            if id(descriptor) in self._consumed:
                return PrincipalRecordKind.CONSUMED_SYNTHETIC_ADMISSION
            return PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION


def open_nonproduction_principal_proof_port(
    configuration: object,
    *,
    policy_revision: object,
    authorization_provenance: object,
    seal: object,
) -> NonproductionPrincipalProofPort:
    """Open the contract-test proof port. ``seal`` must be minted in-process."""

    return NonproductionPrincipalProofPort(
        configuration,
        policy_revision=policy_revision,
        authorization_provenance=authorization_provenance,
        seal=seal,
    )


def _reject_controls(
    session: object,
    principal_label: object,
    event: object,
    deadline_utc: object,
    tenant: object,
    role: object,
) -> None:
    if tenant is not None or role is not None:
        _fail("absent_source_claim")
    if deadline_utc is not None:
        _fail("request_deadline_not_principal_lifetime")
    if principal_label is not None:
        _fail(_rejection_code(principal_label))
    if event is not None:
        _fail(_rejection_code(event))
    if session is not None:
        _fail("session_membership_not_proof")


def _require_consistent(
    configuration: MasterGatewayIdentityConfiguration,
    observation: UntrustedGatewayClaimObservation,
    authority: IndependentlyHostAuthenticatedIdentityInput,
    evaluated: int,
    policy_revision: str,
    authorization_provenance: str,
) -> None:
    _closed(authority.production_principal, False, "caller_flag_not_authority")
    _closed(authority.authenticates_caller, False, "caller_flag_not_authority")
    _closed(authority.reviewed_host_mechanism, False, "reviewed_host_authority_not_wired")
    _closed(observation.structural_assessment_accepted_as_proof, False, "structural_jwt_assessment_not_principal")
    if authority.issuer != configuration.issuer or observation.issuer != configuration.issuer:
        _fail("issuer_mismatch")
    if authority.issuer != observation.issuer:
        _fail("issuer_mismatch")
    if (
        authority.audience != configuration.audience
        or observation.audience != configuration.audience
        or authority.audience != observation.audience
    ):
        _fail("audience_substitution")
    if (
        authority.method != configuration.method
        or authority.raw_path != configuration.raw_path
        or authority.route_key != configuration.route_key
        or observation.method != configuration.method
        or observation.raw_path != configuration.raw_path
        or observation.route_key != configuration.route_key
    ):
        _fail("route_mismatch")
    if not set(configuration.required_scopes).issubset(authority.scopes):
        _fail("missing_required_scope")
    if authority.scopes != observation.scopes:
        _fail("scope_mismatch")
    if authority.subject != observation.subject:
        _fail("subject_inconsistent")
    if authority.gateway_request_id != observation.gateway_request_id:
        _fail("request_identity_mismatch")
    if authority.expires_at_epoch != observation.expires_at_epoch:
        _fail("lifetime_mismatch")
    if observation.not_before_epoch is not None and authority.not_before_epoch != observation.not_before_epoch:
        _fail("lifetime_mismatch")
    if observation.issued_at_epoch is not None and authority.issued_at_epoch != observation.issued_at_epoch:
        _fail("lifetime_mismatch")
    if authority.policy_revision != policy_revision:
        _fail("policy_revision_mismatch")
    if authority.authorization_provenance != authorization_provenance:
        _fail("provenance_mismatch")
    _require_lifetime(
        authority.issued_at_epoch,
        authority.not_before_epoch,
        authority.expires_at_epoch,
        evaluated,
    )


def _require_lifetime(issued_at: int, not_before: int, expires_at: int, evaluated: int) -> None:
    if issued_at > evaluated:
        _fail("principal_issued_in_future")
    if evaluated < not_before:
        _fail("principal_not_yet_valid")
    if expires_at <= evaluated:
        _fail("principal_expired")


def _require_handle_intact(descriptor: ServerOwnedPrincipalDescriptor, stored: _Stored) -> None:
    scope = descriptor.scope
    intact = (
        type(scope) is PrincipalScopeBinding
        and scope.subject == stored.subject
        and scope.scopes == stored.scopes
        and scope.issuer == stored.issuer
        and scope.audience == stored.audience
        and scope.route_key == stored.route_key
        and scope.method == stored.method
        and scope.raw_path == stored.raw_path
        and scope.purpose is stored.purpose
        and scope.policy_revision == stored.policy_revision
        and scope.planning_session_id == stored.planning_session_id
        and scope.master_request_id == stored.master_request_id
        and scope.correlation_id == stored.correlation_id
        and scope.gateway_request_id == stored.gateway_request_id
        and scope.production_scope_grant is False
        and descriptor.principal_ref == stored.principal_ref
        and descriptor.proof_id == stored.proof_id
        and descriptor.authorization_provenance == stored.authorization_provenance
        and descriptor.accepted_at_epoch == stored.issued_at_epoch
        and descriptor.not_before_epoch == stored.not_before_epoch
        and descriptor.expires_at_epoch == stored.expires_at_epoch
        and descriptor.record_kind is PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION
        and descriptor.provenance_standing is AuthorizationProvenanceStanding.CONTRACT_TEST_HOST
        and descriptor.tenant_presence is SourceClaimPresence.ABSENT
        and descriptor.role_presence is SourceClaimPresence.ABSENT
        and descriptor.limitations == APPLICATION_PRINCIPAL_LIMITATIONS
        and descriptor.schema_version == _SCHEMA
    )
    if not intact:
        _fail("principal_descriptor_immutable")
    _closed(descriptor.production_principal, False, "production_principal_not_issued")
    _closed(descriptor.authenticates_caller, False, "production_principal_not_issued")
    _closed(descriptor.environment_activated, False, "environment_not_principal_authority")
    _closed(descriptor.execution_permitted, False, "execution_permission_not_available")
    _closed(descriptor.execution_authority, MasterRouteExecutionAuthority.DENIED, "execution_authority_not_granted")
    _closed(descriptor.production_authority, MasterProductionAuthority.DENIED, "execution_authority_not_granted")
    _closed(
        descriptor.source_authenticity,
        MasterSourceAuthenticity.NOT_ESTABLISHED,
        "source_authenticity_not_established",
    )
    _closed(descriptor.request_coverage, MasterRequestCoverage.NOT_ESTABLISHED, "request_coverage_not_established")
    _closed(descriptor.session_membership_accepted_as_proof, False, "session_membership_not_proof")
    _closed(descriptor.matching_identifier_accepted_as_proof, False, "matching_identifier_not_proof")
    _closed(
        descriptor.structural_assessment_accepted_as_proof,
        False,
        "structural_jwt_assessment_not_principal",
    )


def _require_presentation(
    stored: _Stored,
    *,
    planning_session_id: object,
    master_request_id: object,
    correlation_id: object,
    purpose: object,
    audience: object,
    route_key: object,
    acting_subject: object,
    gateway_request_id: object,
) -> None:
    if type(purpose) is not MasterPurpose:
        _fail("invalid_purpose")
    if purpose is not stored.purpose:
        _fail("purpose_mismatch")
    if type(audience) is not str or audience != stored.audience:
        _fail("audience_substitution")
    if type(route_key) is not str or route_key != stored.route_key:
        _fail("route_mismatch")
    if type(acting_subject) is not str or acting_subject != stored.subject:
        _fail("confused_deputy")
    if type(master_request_id) is not str or master_request_id != stored.master_request_id:
        _fail("confused_deputy")
    if type(correlation_id) is not str or correlation_id != stored.correlation_id:
        _fail("confused_deputy")
    if type(planning_session_id) is not str or planning_session_id != stored.planning_session_id:
        _fail("cross_session_principal")
    if type(gateway_request_id) is not str or gateway_request_id != stored.gateway_request_id:
        _fail("request_identity_mismatch")


def _rejection_code(candidate: object) -> str:
    if type(candidate) is bool:
        return "caller_flag_not_authority"
    if type(candidate) is RawAuthorizationHeader:
        return "header_only_identity"
    if type(candidate) is str:
        if candidate[:7].casefold() == "bearer ":
            return "header_only_identity"
        if any(character.isspace() for character in candidate):
            return "user_text_not_authority"
        if candidate.casefold() in _POLICY_WORDS:
            return "policy_check_not_authority"
        return "principal_label_not_authority"
    if type(candidate) is bytes or type(candidate) is bytearray:
        return "principal_label_not_authority"
    if type(candidate) is AuthorizerClaimMetadata:
        return "forged_gateway_claims"
    if isinstance(candidate, dict):
        return _mapping_code(candidate)
    if type(candidate) is MasterGatewayIdentityAssessment:
        return "structural_jwt_assessment_not_principal"
    if type(candidate) is UntrustedGatewayClaimObservation:
        return "descriptive_observation_not_principal"
    if type(candidate) is IndependentlyHostAuthenticatedIdentityInput:
        return "caller_built_identity_not_host_issued"
    if type(candidate) is ServerOwnedPrincipalDescriptor:
        return "synthetic_admission_not_production_principal"
    if type(candidate) is PrincipalScopeBinding:
        return "scope_binding_not_a_principal"
    if type(candidate) is NonproductionPrincipalProofPort:
        return "test_double_not_production_issuer"
    if type(candidate) is NonproductionContractSeal:
        return "nonproduction_seal_not_production_authority"
    if type(candidate) is HostIngressProofChecklist:
        return "host_checklist_not_issuance"
    if type(candidate) is MasterGatewayIdentityConfiguration:
        return "configuration_not_a_principal"
    if type(candidate) is ClaimedRequestIdentity:
        return "claimed_request_not_a_principal"
    if type(candidate) is GatewayInvocationProvenance:
        return "invocation_provenance_not_a_principal"
    if type(candidate) is UnresolvedIngressAttestation:
        return "ingress_attestation_not_a_principal"
    if type(candidate) is ApplicationPrincipalRequirement:
        return "application_principal_not_issued"
    if type(candidate) in _SESSION_TYPES:
        return "session_membership_not_proof"
    if type(candidate) is int:
        return "matching_identifier_not_proof"
    return "untrusted_identity_not_authority"


def reject_untrusted_authority(candidate: object) -> None:
    """Reject one caller-supplied authority. Nothing is returned."""

    _fail(_rejection_code(candidate))


def _production_reasons(
    candidates: tuple[object, ...],
    kwargs: dict[str, object],
) -> tuple[str, ...]:
    reasons = [
        "production_principal_issuance_unavailable",
        "reviewed_host_authority_not_wired",
    ]
    for candidate in candidates:
        reasons.append(_rejection_code(candidate))
    if kwargs:
        reasons.append(_keyword_code(kwargs))
    return tuple(reasons)


def issue_production_application_principal(*candidates: object, **kwargs: object) -> None:
    """Reject production issuance. The reviewed host mechanism is not wired."""

    raise ContractValidationError(_production_reasons(candidates, kwargs))
