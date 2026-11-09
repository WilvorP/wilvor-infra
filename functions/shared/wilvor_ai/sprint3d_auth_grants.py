"""Sprint 3D host authentication and single-invocation grant issuance.

Identity comes from an RS256 JWT checked against a reviewed identity
provider key. ``requestContext.authorizer.jwt``, a bare claims object,
and an ``Authorization`` header are not authentication until that
signature, issuer, audience, scope, and lifetime check succeeds.

The host mints ``principal_ref`` and binds the server request. A token
cannot supply those ids. Public runtime policy stays ``DENY``. A grant
is signed only when that denial is exactly the missing-principal reason
and every other policy check passed. Hypothetical ``ALLOW`` is refused.

The signed nonce is the nonce Agent C must write. ``verify_serialized_grant``
checks the HMAC and the presented binding, then returns
``AgentCAtomicConsumeRequest``. That request is not a ledger write.
Agent C consumes it with one conditional put of the signed nonce.
``Sprint3CDurableGrantLedger.consume_signed_grant`` stays the closed
synthetic path and must not be used for this grant.

Route enablement stays closed. A valid signature cannot set
``route_enablement_permitted``. Live AWS controls are not inspected here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Mapping

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    AgentCapability,
    ContractValidationError,
    TemporalScope,
)
from wilvor_ai.master_application_principal import issue_production_application_principal
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_execution_grants import (
    HMAC_PREVENTS_REPLAY,
    PRODUCTION_GRANT_ISSUANCE_AVAILABLE,
    PRODUCTION_SIGNING_KEY_STORE_INTEGRATED,
    LogicalReadOperation,
    MasterExecutionGrantEnvelope,
    evaluate_grant_envelope,
    issue_production_execution_grant,
)
from wilvor_ai.master_execution_ledger import (
    CONDITIONAL_PUT_ABSENT,
    DYNAMODB_LEDGER_TABLE_NAME,
    RETENTION_AFTER_DEADLINE,
    MasterExecutionLedgerEnvelope,
    ledger_item_key,
)
from wilvor_ai.master_gateway_identity import InvocationProvenanceStanding
from wilvor_ai.master_gateway_runtime_boundary import (
    ISSUANCE_CONTROL_IDS,
    REQUIRED_GATEWAY_PRINCIPAL,
    REQUIRED_INVOKE_ACTION,
    REQUIRED_ROUTE_KEY,
    REQUIRED_STAGE_NAME,
    AI_JWT_ISSUER_PATTERN,
)
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.master_runtime_policy import (
    HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE,
    HOST_GRANT_ISSUER_AVAILABLE,
    POLICY_REVISION,
    MasterRuntimePolicyDecision,
    MasterRuntimePolicyDenialReason,
    MasterRuntimePolicyEvaluation,
    PolicyAuthorityOrigin,
)


SPRINT3D_AUTH_GRANTS_SCHEMA_VERSION = "wilvor.ai.sprint3d.auth_grants.v1"
HOST_GRANT_DOCUMENT_SCHEMA = "wilvor.ai.sprint3d.host_execution_grant.v1"
AUTHORITY_DOMAIN = "HOST_SIGNED_SINGLE_INVOCATION"
DECISION_TARGET_NOT_APPLICABLE = "decision-target-not-applicable"
CONSUME_MARKER_PREFIX = "consume-v1#"
MAX_TOKEN_LIFETIME_SECONDS = 900
MIN_RSA_BITS = 2048
MIN_PUBLIC_EXPONENT = 65537
MIN_HMAC_KEY_BYTES = 32
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
_PROCESS_SEAL = secrets.token_bytes(32)
_ISSUER = re.compile(AI_JWT_ISSUER_PATTERN)
_AUDIENCE = re.compile(r"^[A-Za-z0-9._~:/?#@!$&'()+,;=%-]{1,128}$")
_SCOPE = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
_KID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SUBJECT = re.compile(r"^[A-Za-z0-9._:@|/+~=-]{1,128}$")
_HEX_SIGNATURE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_CLAIMS = frozenset(
    {
        "principal_ref",
        "master_request_id",
        "planning_session_id",
        "step_id",
        "invocation_id",
        "policy_revision",
        "authorized",
        "authorization",
        "requestcontext",
        "authorizer",
        "trusted",
        "grant",
        "signature",
        "alg",
        "scp",
    }
)
_SIGNED_FIELDS = (
    "accepted_at_utc",
    "argument_names",
    "audience",
    "authority_domain",
    "aws_controls_verified",
    "capability",
    "correlation_id",
    "deadline_utc",
    "decision_target_scope",
    "distributed_exactly_once",
    "expires_at_utc",
    "historical_window",
    "hmac_prevents_replay",
    "idempotency_key",
    "invocation_id",
    "issuer",
    "key_id",
    "master_request_id",
    "max_model_turns",
    "max_tool_calls",
    "nonce",
    "planning_session_id",
    "policy_fingerprint",
    "policy_revision",
    "policy_session_id",
    "principal_ref",
    "purpose",
    "redeemable_on_production_route",
    "region_scope",
    "resource_scope",
    "route_enablement_permitted",
    "route_key",
    "schema_version",
    "scopes",
    "single_invocation",
    "specialist",
    "step_id",
    "temporal_scopes",
    "token_jti",
    "tool_name",
    "verified_subject",
)
_AGENT_C_DO_NOT_CALL = "Sprint3CDurableGrantLedger.consume_signed_grant"


class Sprint3DRejection(str, Enum):
    """Why authentication, policy binding, or grant verification stopped."""

    UNAUTHENTICATED = "UNAUTHENTICATED"
    CALLER_CONSTRUCTED_CLAIMS = "CALLER_CONSTRUCTED_CLAIMS"
    EVENT_CLAIMS_NOT_AUTHENTICATION = "EVENT_CLAIMS_NOT_AUTHENTICATION"
    FORGED_SIGNATURE = "FORGED_SIGNATURE"
    UNREVIEWED_SIGNING_KEY = "UNREVIEWED_SIGNING_KEY"
    WRONG_ALGORITHM = "WRONG_ALGORITHM"
    WEAK_KEY = "WEAK_KEY"
    ISSUER_MISMATCH = "ISSUER_MISMATCH"
    AUDIENCE_MISMATCH = "AUDIENCE_MISMATCH"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_NOT_YET_VALID = "TOKEN_NOT_YET_VALID"
    TOKEN_LIFETIME_EXCEEDED = "TOKEN_LIFETIME_EXCEEDED"
    MISSING_TOKEN_BINDING = "MISSING_TOKEN_BINDING"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    POLICY_ALLOW_NOT_AUTHORITY = "POLICY_ALLOW_NOT_AUTHORITY"
    POLICY_REVISION_MISMATCH = "POLICY_REVISION_MISMATCH"
    PRINCIPAL_MISMATCH = "PRINCIPAL_MISMATCH"
    SESSION_MISMATCH = "SESSION_MISMATCH"
    REQUEST_MISMATCH = "REQUEST_MISMATCH"
    INVOCATION_MISMATCH = "INVOCATION_MISMATCH"
    EXPIRED_GRANT = "EXPIRED_GRANT"
    REPLAY = "REPLAY"
    FORGED_GRANT = "FORGED_GRANT"
    FOREIGN_GRANT = "FOREIGN_GRANT"
    TAMPERED = "TAMPERED"
    KEY_RETIRED = "KEY_RETIRED"
    BOUNDS_REJECTED = "BOUNDS_REJECTED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    SAME_ACCOUNT_INVOKE_UNREVIEWED = "SAME_ACCOUNT_INVOKE_UNREVIEWED"
    SAME_ACCOUNT_INVOKE_ALLOWED = "SAME_ACCOUNT_INVOKE_ALLOWED"
    WILDCARD_INVOKE = "WILDCARD_INVOKE"
    OTHER_INVOKE_PRINCIPAL = "OTHER_INVOKE_PRINCIPAL"
    FUNCTION_URL = "FUNCTION_URL"
    ASYNC_INVOKE_SOURCE = "ASYNC_INVOKE_SOURCE"
    FORGED_LIVE_AWS_CLAIM = "FORGED_LIVE_AWS_CLAIM"
    ROUTE_ENABLEMENT_BLOCKED = "ROUTE_ENABLEMENT_BLOCKED"
    MALFORMED_TOKEN = "MALFORMED_TOKEN"


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _closed_flags() -> None:
    if PRODUCTION_GRANT_ISSUANCE_AVAILABLE is not False:
        _fail("production_grant_issuance_closed")
    if PRODUCTION_SIGNING_KEY_STORE_INTEGRATED is not False:
        _fail("production_grant_issuance_closed")
    if HMAC_PREVENTS_REPLAY is not False:
        _fail("hmac_does_not_prevent_replay")
    if HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE is not False:
        _fail("host_authenticated_principal_not_available")
    if HOST_GRANT_ISSUER_AVAILABLE is not False:
        _fail("host_grant_issuer_not_available")


def _canonical(document: Mapping[str, object]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(payload: str) -> str:
    return hmac.new(_PROCESS_SEAL, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _utc(value: datetime) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
        or value.microsecond != 0
    ):
        _fail("invalid_clock")
    return value.astimezone(timezone.utc)


def _epoch(value: datetime) -> int:
    return int(_utc(value).timestamp())


def _parse_utc(value: object, code: str) -> datetime:
    if type(value) is not str or len(value) != 20 or not value.endswith("Z"):
        _fail(code)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        _fail(code)
    return parsed.replace(tzinfo=timezone.utc)


def _b64url_decode(segment: str) -> bytes | None:
    if type(segment) is not str or segment == "" or any(char in segment for char in "+/="):
        return None
    padding = "=" * ((4 - len(segment) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except Exception:
        return None


def rsassa_pkcs1_v15_sha256_verify(
    modulus: int,
    exponent: int,
    message: bytes,
    signature: bytes,
) -> bool:
    """RSASSA-PKCS1-v1_5 with SHA-256. No other algorithm verifies."""

    if type(modulus) is not int or type(exponent) is not int:
        return False
    if modulus.bit_length() < MIN_RSA_BITS or exponent < MIN_PUBLIC_EXPONENT or exponent % 2 == 0:
        return False
    width = (modulus.bit_length() + 7) // 8
    if type(signature) is not bytes or len(signature) != width or type(message) is not bytes:
        return False
    signed = int.from_bytes(signature, "big")
    if signed <= 0 or signed >= modulus:
        return False
    encoded = pow(signed, exponent, modulus).to_bytes(width, "big")
    trailer = _SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    if len(trailer) + 11 > width:
        return False
    padding = width - len(trailer) - 3
    expected = b"\x00\x01" + (b"\xff" * padding) + b"\x00" + trailer
    return hmac.compare_digest(encoded, expected)


def _json_object(raw: bytes) -> dict[str, object] | None:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if type(parsed) is not dict:
        return None
    return parsed


def _event_subject(event: object) -> str | None:
    if type(event) is not dict:
        return None
    context = event.get("requestContext")
    if type(context) is not dict:
        return None
    authorizer = context.get("authorizer")
    if type(authorizer) is not dict:
        return None
    jwt_block = authorizer.get("jwt")
    if type(jwt_block) is not dict:
        return None
    claims = jwt_block.get("claims")
    if type(claims) is not dict:
        return None
    subject = claims.get("sub")
    if type(subject) is not str:
        return None
    return subject


@dataclass(frozen=True)
class ReviewedRsaPublicKey:
    """One reviewed identity-provider key. The modulus is the public key."""

    kid: str
    modulus: int
    exponent: int

    def __post_init__(self) -> None:
        if not _KID.fullmatch(self.kid):
            _fail("invalid_signing_key")
        if type(self.modulus) is not int or self.modulus.bit_length() < MIN_RSA_BITS:
            _fail("weak_signing_key")
        if type(self.exponent) is not int or self.exponent < MIN_PUBLIC_EXPONENT or self.exponent % 2 == 0:
            _fail("weak_signing_key")


@dataclass(frozen=True)
class ReviewedIdentityProvider:
    """Server-owned issuer, audience, scopes, and RSA keys. Not an event."""

    issuer: str
    audience: str
    required_scopes: tuple[str, ...]
    keys: tuple[ReviewedRsaPublicKey, ...]
    max_token_lifetime_seconds: int = MAX_TOKEN_LIFETIME_SECONDS
    _seal: str = ""

    def __post_init__(self) -> None:
        if not _ISSUER.fullmatch(self.issuer) or "*" in self.issuer:
            _fail("invalid_issuer")
        if not _AUDIENCE.fullmatch(self.audience) or "*" in self.audience:
            _fail("invalid_audience")
        if (
            type(self.required_scopes) is not tuple
            or not self.required_scopes
            or any(not _SCOPE.fullmatch(scope) or "*" in scope for scope in self.required_scopes)
        ):
            _fail("invalid_scope")
        if len(set(self.required_scopes)) != len(self.required_scopes):
            _fail("invalid_scope")
        if type(self.keys) is not tuple or not self.keys:
            _fail("invalid_signing_key")
        kids = [key.kid for key in self.keys]
        if any(type(key) is not ReviewedRsaPublicKey for key in self.keys) or len(set(kids)) != len(kids):
            _fail("invalid_signing_key")
        if self.max_token_lifetime_seconds != MAX_TOKEN_LIFETIME_SECONDS:
            _fail("token_lifetime_ceiling")
        payload = _canonical(
            {
                "issuer": self.issuer,
                "audience": self.audience,
                "scopes": list(self.required_scopes),
                "kids": kids,
            }
        )
        if not hmac.compare_digest(self._seal, _digest(payload)):
            _fail("caller_constructed_identity_provider")

    def key(self, kid: str) -> ReviewedRsaPublicKey | None:
        for item in self.keys:
            if item.kid == kid:
                return item
        return None


def seal_reviewed_identity_provider(
    *,
    issuer: str,
    audience: str,
    required_scopes: tuple[str, ...],
    keys: tuple[ReviewedRsaPublicKey, ...],
) -> ReviewedIdentityProvider:
    """Seal provider material the host configured. The event cannot call this."""

    _closed_flags()
    kids = [key.kid for key in keys]
    payload = _canonical(
        {
            "issuer": issuer,
            "audience": audience,
            "scopes": list(required_scopes),
            "kids": kids,
        }
    )
    return ReviewedIdentityProvider(
        issuer=issuer,
        audience=audience,
        required_scopes=tuple(required_scopes),
        keys=tuple(keys),
        _seal=_digest(payload),
    )


@dataclass(frozen=True)
class BearerAuthentication:
    """Verified bearer identity. Event claims are recorded and ignored."""

    authenticated: bool
    rejection: Sprint3DRejection | None
    subject: str | None
    issuer: str | None
    audience: str | None
    scopes: tuple[str, ...]
    jti: str | None
    issued_at_epoch: int | None
    not_before_epoch: int | None
    expires_at_epoch: int | None
    signing_kid: str | None
    signature_verified: bool
    event_subject_ignored: str | None
    route_enablement_permitted: bool = False
    invocation_provenance: InvocationProvenanceStanding = InvocationProvenanceStanding.NOT_ATTESTED
    _seal: str = ""

    def __post_init__(self) -> None:
        if self.route_enablement_permitted is not False:
            _fail("route_enablement_blocked")
        if self.invocation_provenance is not InvocationProvenanceStanding.NOT_ATTESTED:
            _fail("invocation_provenance_not_attested")
        if type(self.authenticated) is not bool or type(self.signature_verified) is not bool:
            _fail("invalid_authentication")
        if self.authenticated:
            if (
                self.rejection is not None
                or self.signature_verified is not True
                or self.subject is None
                or self.jti is None
            ):
                _fail("invalid_authentication")
        elif self.rejection is None or self.subject is not None:
            _fail("invalid_authentication")
        payload = _canonical(
            {
                "authenticated": self.authenticated,
                "rejection": None if self.rejection is None else self.rejection.value,
                "subject": self.subject,
                "issuer": self.issuer,
                "audience": self.audience,
                "scopes": list(self.scopes),
                "jti": self.jti,
                "iat": self.issued_at_epoch,
                "nbf": self.not_before_epoch,
                "exp": self.expires_at_epoch,
                "kid": self.signing_kid,
                "signature_verified": self.signature_verified,
                "event_subject_ignored": self.event_subject_ignored,
            }
        )
        if not hmac.compare_digest(self._seal, _digest(payload)):
            _fail("caller_constructed_authentication")

    def __bool__(self) -> bool:
        _fail("authentication_is_not_route_authority")
        raise AssertionError("unreachable")


def _auth_result(**fields: object) -> BearerAuthentication:
    payload = _canonical(
        {
            "authenticated": fields["authenticated"],
            "rejection": None if fields["rejection"] is None else fields["rejection"].value,  # type: ignore[union-attr]
            "subject": fields["subject"],
            "issuer": fields["issuer"],
            "audience": fields["audience"],
            "scopes": list(fields["scopes"]),  # type: ignore[arg-type]
            "jti": fields["jti"],
            "iat": fields["issued_at_epoch"],
            "nbf": fields["not_before_epoch"],
            "exp": fields["expires_at_epoch"],
            "kid": fields["signing_kid"],
            "signature_verified": fields["signature_verified"],
            "event_subject_ignored": fields["event_subject_ignored"],
        }
    )
    return BearerAuthentication(**fields, _seal=_digest(payload))  # type: ignore[arg-type]


def _rejected_auth(
    reason: Sprint3DRejection,
    *,
    event_subject: str | None,
    signature_verified: bool = False,
) -> BearerAuthentication:
    return _auth_result(
        authenticated=False,
        rejection=reason,
        subject=None,
        issuer=None,
        audience=None,
        scopes=(),
        jti=None,
        issued_at_epoch=None,
        not_before_epoch=None,
        expires_at_epoch=None,
        signing_kid=None,
        signature_verified=signature_verified,
        event_subject_ignored=event_subject,
    )


def _audience_values(claim: object) -> tuple[str, ...] | None:
    if type(claim) is str:
        values = (claim,)
    elif type(claim) is list and claim and all(type(item) is str for item in claim):
        values = tuple(claim)
    else:
        return None
    if any(not _AUDIENCE.fullmatch(item) or "*" in item for item in values):
        return None
    return values


def _scope_values(claim: object) -> tuple[str, ...] | None:
    if type(claim) is not str or claim == "" or claim != claim.strip():
        return None
    values = tuple(claim.split(" "))
    if any(not _SCOPE.fullmatch(item) or "*" in item for item in values):
        return None
    return values


def _epoch_claim(claim: object) -> int | None:
    if type(claim) is not int or isinstance(claim, bool):
        return None
    if claim < 0 or claim > 4_102_444_800:
        return None
    return claim


def authenticate_bearer(
    token: object,
    provider: ReviewedIdentityProvider,
    now: datetime,
    *,
    untrusted_event: object = None,
) -> BearerAuthentication:
    """Verify one compact JWT. Event authorizer claims are not the token."""

    _closed_flags()
    if type(provider) is not ReviewedIdentityProvider:
        _fail("invalid_identity_provider")
    moment = _epoch(_utc(now))
    event_subject = _event_subject(untrusted_event)
    if type(token) is not str:
        reason = (
            Sprint3DRejection.CALLER_CONSTRUCTED_CLAIMS
            if type(token) is dict
            else Sprint3DRejection.UNAUTHENTICATED
        )
        return _rejected_auth(reason, event_subject=event_subject)
    if token.count(".") != 2 or len(token) > 8192 or any(char.isspace() for char in token):
        return _rejected_auth(Sprint3DRejection.MALFORMED_TOKEN, event_subject=event_subject)
    header_segment, payload_segment, signature_segment = token.split(".")
    header_raw = _b64url_decode(header_segment)
    payload_raw = _b64url_decode(payload_segment)
    signature = _b64url_decode(signature_segment)
    header = None if header_raw is None else _json_object(header_raw)
    payload = None if payload_raw is None else _json_object(payload_raw)
    if header is None or payload is None or signature is None:
        return _rejected_auth(Sprint3DRejection.MALFORMED_TOKEN, event_subject=event_subject)
    algorithm = header.get("alg")
    kid = header.get("kid")
    if algorithm != "RS256" or header.get("typ") != "JWT" or set(header) != {"alg", "typ", "kid"}:
        return _rejected_auth(Sprint3DRejection.WRONG_ALGORITHM, event_subject=event_subject)
    if type(kid) is not str:
        return _rejected_auth(Sprint3DRejection.UNREVIEWED_SIGNING_KEY, event_subject=event_subject)
    key = provider.key(kid)
    if key is None:
        return _rejected_auth(Sprint3DRejection.UNREVIEWED_SIGNING_KEY, event_subject=event_subject)
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    if not rsassa_pkcs1_v15_sha256_verify(key.modulus, key.exponent, signing_input, signature):
        return _rejected_auth(Sprint3DRejection.FORGED_SIGNATURE, event_subject=event_subject)
    folded = {str(name).casefold() for name in payload}
    if folded & _FORBIDDEN_CLAIMS or "jwk" in header or "jku" in header:
        return _rejected_auth(
            Sprint3DRejection.CALLER_CONSTRUCTED_CLAIMS,
            event_subject=event_subject,
            signature_verified=True,
        )
    issuer = payload.get("iss")
    audience = _audience_values(payload.get("aud"))
    scopes = _scope_values(payload.get("scope"))
    subject = payload.get("sub")
    jti = payload.get("jti")
    issued = _epoch_claim(payload.get("iat"))
    not_before = _epoch_claim(payload.get("nbf"))
    expires = _epoch_claim(payload.get("exp"))
    if (
        type(issuer) is not str
        or audience is None
        or scopes is None
        or type(subject) is not str
        or not _SUBJECT.fullmatch(subject)
        or type(jti) is not str
        or not _SUBJECT.fullmatch(jti)
        or issued is None
        or not_before is None
        or expires is None
    ):
        return _rejected_auth(
            Sprint3DRejection.MISSING_TOKEN_BINDING,
            event_subject=event_subject,
            signature_verified=True,
        )
    if issuer != provider.issuer:
        return _rejected_auth(Sprint3DRejection.ISSUER_MISMATCH, event_subject=event_subject, signature_verified=True)
    if provider.audience not in audience:
        return _rejected_auth(Sprint3DRejection.AUDIENCE_MISMATCH, event_subject=event_subject, signature_verified=True)
    if any(required not in scopes for required in provider.required_scopes):
        return _rejected_auth(Sprint3DRejection.SCOPE_MISMATCH, event_subject=event_subject, signature_verified=True)
    if not_before > moment or issued > moment:
        return _rejected_auth(Sprint3DRejection.TOKEN_NOT_YET_VALID, event_subject=event_subject, signature_verified=True)
    if moment >= expires:
        return _rejected_auth(Sprint3DRejection.TOKEN_EXPIRED, event_subject=event_subject, signature_verified=True)
    if expires <= issued or expires - issued > provider.max_token_lifetime_seconds or not_before > expires:
        return _rejected_auth(
            Sprint3DRejection.TOKEN_LIFETIME_EXCEEDED,
            event_subject=event_subject,
            signature_verified=True,
        )
    return _auth_result(
        authenticated=True,
        rejection=None,
        subject=subject,
        issuer=issuer,
        audience=provider.audience,
        scopes=scopes,
        jti=jti,
        issued_at_epoch=issued,
        not_before_epoch=not_before,
        expires_at_epoch=expires,
        signing_kid=kid,
        signature_verified=True,
        event_subject_ignored=event_subject,
    )


@dataclass(frozen=True)
class Sprint3DTrustedPrincipal:
    """Host principal. ``principal_ref`` is minted here, not copied from ``sub``."""

    principal_ref: str
    verified_subject: str
    issuer: str
    audience: str
    scopes: tuple[str, ...]
    token_jti: str
    token_expires_at_epoch: int
    master_request_id: str
    planning_session_id: str
    policy_session_id: str
    step_id: str
    invocation_id: str
    correlation_id: str
    idempotency_key: str
    tool_name: str
    resource_id: str
    operation: str
    region_scope: tuple[str, ...]
    purpose: MasterPurpose
    policy_revision: str
    policy_fingerprint: str
    accepted_at_utc: str
    deadline_utc: str
    route_key: str
    grant_eligible: bool
    idp_authenticated: bool
    route_enablement_permitted: bool = False
    invocation_provenance: InvocationProvenanceStanding = InvocationProvenanceStanding.NOT_ATTESTED
    public_policy_decision: MasterRuntimePolicyDecision = MasterRuntimePolicyDecision.DENY
    _seal: str = ""

    def __post_init__(self) -> None:
        if self.route_enablement_permitted is not False or self.idp_authenticated is not True:
            _fail("invalid_principal")
        if self.invocation_provenance is not InvocationProvenanceStanding.NOT_ATTESTED:
            _fail("invocation_provenance_not_attested")
        if self.public_policy_decision is not MasterRuntimePolicyDecision.DENY:
            _fail("policy_evaluation_is_not_a_permit")
        if self.route_key != REQUIRED_ROUTE_KEY or self.policy_revision != POLICY_REVISION:
            _fail("invalid_principal")
        payload = _principal_payload(self)
        if not hmac.compare_digest(self._seal, _digest(payload)):
            _fail("caller_constructed_principal")

    def __bool__(self) -> bool:
        _fail("principal_is_not_route_authority")
        raise AssertionError("unreachable")


def _principal_payload(principal: Sprint3DTrustedPrincipal) -> str:
    return _canonical(
        {
            "principal_ref": principal.principal_ref,
            "verified_subject": principal.verified_subject,
            "issuer": principal.issuer,
            "audience": principal.audience,
            "scopes": list(principal.scopes),
            "token_jti": principal.token_jti,
            "token_expires_at_epoch": principal.token_expires_at_epoch,
            "master_request_id": principal.master_request_id,
            "planning_session_id": principal.planning_session_id,
            "policy_session_id": principal.policy_session_id,
            "step_id": principal.step_id,
            "invocation_id": principal.invocation_id,
            "correlation_id": principal.correlation_id,
            "idempotency_key": principal.idempotency_key,
            "tool_name": principal.tool_name,
            "resource_id": principal.resource_id,
            "operation": principal.operation,
            "region_scope": list(principal.region_scope),
            "purpose": principal.purpose.value,
            "policy_revision": principal.policy_revision,
            "policy_fingerprint": principal.policy_fingerprint,
            "accepted_at_utc": principal.accepted_at_utc,
            "deadline_utc": principal.deadline_utc,
            "route_key": principal.route_key,
            "grant_eligible": principal.grant_eligible,
        }
    )


@dataclass(frozen=True)
class TrustedPrincipalEstablishment:
    """Principal binding. A false ``grant_eligible`` issues nothing."""

    grant_eligible: bool
    rejection: Sprint3DRejection | None
    principal: Sprint3DTrustedPrincipal | None
    policy_denial_reasons: tuple[MasterRuntimePolicyDenialReason, ...]
    route_enablement_permitted: bool = False
    invocation_provenance: InvocationProvenanceStanding = InvocationProvenanceStanding.NOT_ATTESTED

    def __post_init__(self) -> None:
        if self.route_enablement_permitted is not False:
            _fail("route_enablement_blocked")
        if self.invocation_provenance is not InvocationProvenanceStanding.NOT_ATTESTED:
            _fail("invocation_provenance_not_attested")
        if self.grant_eligible:
            if self.rejection is not None or type(self.principal) is not Sprint3DTrustedPrincipal:
                _fail("invalid_principal")
            if self.principal.grant_eligible is not True:
                _fail("invalid_principal")
        elif self.principal is not None:
            _fail("invalid_principal")

    def __bool__(self) -> bool:
        _fail("principal_is_not_route_authority")
        raise AssertionError("unreachable")


def _distinct(*values: str) -> bool:
    return len(values) == len(set(values))


def establish_trusted_principal(
    authentication: BearerAuthentication,
    evaluation: MasterRuntimePolicyEvaluation,
    *,
    original_planning_session_id: str,
    now: datetime,
) -> TrustedPrincipalEstablishment:
    """Bind a verified subject to the server request. The token does not choose the ids."""

    _closed_flags()
    moment = _utc(now)
    if type(authentication) is not BearerAuthentication:
        _fail("caller_constructed_authentication")
    if type(evaluation) is not MasterRuntimePolicyEvaluation:
        _fail("invalid_policy_evaluation")
    if type(original_planning_session_id) is not str or not _SUBJECT.fullmatch(original_planning_session_id):
        _fail("invalid_planning_session")
    reasons = evaluation.denial_reasons
    if evaluation.decision is MasterRuntimePolicyDecision.ALLOW or (
        evaluation.authority_origin is PolicyAuthorityOrigin.HYPOTHETICAL_TEST_HOST
    ):
        return TrustedPrincipalEstablishment(
            False,
            Sprint3DRejection.POLICY_ALLOW_NOT_AUTHORITY,
            None,
            reasons,
        )
    if (
        evaluation.decision is not MasterRuntimePolicyDecision.DENY
        or evaluation.authority_origin is not PolicyAuthorityOrigin.NOT_ESTABLISHED
        or evaluation.principal_ref is not None
        or evaluation.policy_revision != POLICY_REVISION
    ):
        return TrustedPrincipalEstablishment(False, Sprint3DRejection.POLICY_VIOLATION, None, reasons)
    if not authentication.authenticated or authentication.subject is None or authentication.expires_at_epoch is None:
        rejection = authentication.rejection or Sprint3DRejection.UNAUTHENTICATED
        return TrustedPrincipalEstablishment(False, rejection, None, reasons)
    if reasons != (MasterRuntimePolicyDenialReason.PRINCIPAL_NOT_HOST_AUTHENTICATED,):
        return TrustedPrincipalEstablishment(False, Sprint3DRejection.POLICY_VIOLATION, None, reasons)
    accepted = _parse_utc(evaluation.accepted_at_utc, "invalid_clock")
    deadline = _parse_utc(evaluation.deadline_utc, "invalid_clock")
    if not accepted <= moment < deadline:
        return TrustedPrincipalEstablishment(False, Sprint3DRejection.DEADLINE_EXCEEDED, None, reasons)
    if moment.timestamp() >= authentication.expires_at_epoch:
        return TrustedPrincipalEstablishment(False, Sprint3DRejection.TOKEN_EXPIRED, None, reasons)
    if original_planning_session_id == evaluation.planning_session_id:
        return TrustedPrincipalEstablishment(False, Sprint3DRejection.SESSION_MISMATCH, None, reasons)
    if original_planning_session_id in {authentication.subject, authentication.jti}:
        return TrustedPrincipalEstablishment(False, Sprint3DRejection.CALLER_CONSTRUCTED_CLAIMS, None, reasons)
    identities = (
        evaluation.master_request_id,
        original_planning_session_id,
        evaluation.step_id,
        evaluation.invocation_id,
        evaluation.correlation_id,
        evaluation.idempotency_key,
    )
    if not _distinct(*identities):
        return TrustedPrincipalEstablishment(False, Sprint3DRejection.REQUEST_MISMATCH, None, reasons)
    if len(original_planning_session_id) > PROVENANCE_TOKEN_MAX_LENGTH:
        _fail("invalid_planning_session")
    principal_ref = "pr_" + secrets.token_hex(16)
    material = _canonical(
        {
            "principal_ref": principal_ref,
            "verified_subject": authentication.subject,
            "issuer": authentication.issuer,
            "audience": authentication.audience,
            "scopes": list(authentication.scopes),
            "token_jti": authentication.jti,
            "token_expires_at_epoch": authentication.expires_at_epoch,
            "master_request_id": evaluation.master_request_id,
            "planning_session_id": original_planning_session_id,
            "policy_session_id": evaluation.planning_session_id,
            "step_id": evaluation.step_id,
            "invocation_id": evaluation.invocation_id,
            "correlation_id": evaluation.correlation_id,
            "idempotency_key": evaluation.idempotency_key,
            "tool_name": evaluation.tool_name,
            "resource_id": evaluation.resource_scope,
            "operation": evaluation.operation.value,
            "region_scope": list(evaluation.region_scope),
            "purpose": evaluation.purpose.value,
            "policy_revision": evaluation.policy_revision,
            "policy_fingerprint": evaluation.policy_fingerprint,
            "accepted_at_utc": evaluation.accepted_at_utc,
            "deadline_utc": evaluation.deadline_utc,
            "route_key": REQUIRED_ROUTE_KEY,
            "grant_eligible": True,
        }
    )
    sealed = Sprint3DTrustedPrincipal(
        principal_ref=principal_ref,
        verified_subject=authentication.subject,
        issuer=authentication.issuer or "",
        audience=authentication.audience or "",
        scopes=authentication.scopes,
        token_jti=authentication.jti or "",
        token_expires_at_epoch=authentication.expires_at_epoch,
        master_request_id=evaluation.master_request_id,
        planning_session_id=original_planning_session_id,
        policy_session_id=evaluation.planning_session_id,
        step_id=evaluation.step_id,
        invocation_id=evaluation.invocation_id,
        correlation_id=evaluation.correlation_id,
        idempotency_key=evaluation.idempotency_key,
        tool_name=evaluation.tool_name,
        resource_id=evaluation.resource_scope,
        operation=evaluation.operation.value,
        region_scope=evaluation.region_scope,
        purpose=evaluation.purpose,
        policy_revision=evaluation.policy_revision,
        policy_fingerprint=evaluation.policy_fingerprint,
        accepted_at_utc=evaluation.accepted_at_utc,
        deadline_utc=evaluation.deadline_utc,
        route_key=REQUIRED_ROUTE_KEY,
        grant_eligible=True,
        idp_authenticated=True,
        _seal=_digest(material),
    )
    return TrustedPrincipalEstablishment(True, None, sealed, reasons)


class InjectedGrantSigningKeys:
    """HMAC keys supplied by the host. This object is not a KMS client."""

    def __init__(self, *, active_key_id: str, keys: Mapping[str, bytes]) -> None:
        _closed_flags()
        if type(active_key_id) is not str or not _KID.fullmatch(active_key_id):
            _fail("invalid_signing_key")
        if type(keys) is not dict or active_key_id not in keys:
            _fail("invalid_signing_key")
        stored: dict[str, bytes] = {}
        for kid, material in keys.items():
            if type(kid) is not str or not _KID.fullmatch(kid):
                _fail("invalid_signing_key")
            if type(material) is not bytes or len(material) < MIN_HMAC_KEY_BYTES:
                _fail("weak_signing_key")
            if material == bytes(len(material)):
                _fail("weak_signing_key")
            stored[kid] = material
        self._keys = stored
        self._active = active_key_id
        self._retired: set[str] = set()
        self._lock = threading.Lock()
        self.kms_integrated = False

    @property
    def active_key_id(self) -> str:
        with self._lock:
            return self._active

    def rotate(self, key_id: str, material: bytes) -> None:
        if type(key_id) is not str or not _KID.fullmatch(key_id):
            _fail("invalid_signing_key")
        if type(material) is not bytes or len(material) < MIN_HMAC_KEY_BYTES or material == bytes(len(material)):
            _fail("weak_signing_key")
        with self._lock:
            if key_id in self._retired:
                _fail("key_retired")
            self._keys[key_id] = material
            self._active = key_id

    def retire(self, key_id: str) -> None:
        with self._lock:
            if key_id == self._active:
                _fail("active_signing_key_not_retired")
            if key_id not in self._keys:
                _fail("unknown_verification_key")
            del self._keys[key_id]
            self._retired.add(key_id)

    def sign(self, document: str) -> tuple[str, str]:
        with self._lock:
            key_id = self._active
            material = self._keys[key_id]
        signature = hmac.new(material, document.encode("utf-8"), hashlib.sha256).hexdigest()
        return key_id, signature

    def verify(self, document: str, signature: str, key_id: str) -> Sprint3DRejection | None:
        if type(signature) is not str or not _HEX_SIGNATURE.fullmatch(signature):
            return Sprint3DRejection.FORGED_GRANT
        with self._lock:
            if key_id in self._retired:
                return Sprint3DRejection.KEY_RETIRED
            material = self._keys.get(key_id)
        if material is None:
            return Sprint3DRejection.UNREVIEWED_SIGNING_KEY
        expected = hmac.new(material, document.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return Sprint3DRejection.FORGED_GRANT
        return None


@dataclass(frozen=True)
class HostSignedGrant:
    """Signed grant bytes. The object is not route authority."""

    canonical_document: str
    signature_hex: str
    key_id: str
    nonce: str
    _seal: str = ""

    def __post_init__(self) -> None:
        payload = _canonical(
            {
                "document": self.canonical_document,
                "signature": self.signature_hex,
                "key_id": self.key_id,
                "nonce": self.nonce,
            }
        )
        if not hmac.compare_digest(self._seal, _digest(payload)):
            _fail("caller_constructed_grant")

    def __bool__(self) -> bool:
        _fail("grant_is_not_route_authority")
        raise AssertionError("unreachable")


def _seal_grant(document: str, signature: str, key_id: str, nonce: str) -> HostSignedGrant:
    payload = _canonical(
        {"document": document, "signature": signature, "key_id": key_id, "nonce": nonce}
    )
    return HostSignedGrant(document, signature, key_id, nonce, _seal=_digest(payload))


@dataclass(frozen=True)
class AgentCPresentedBinding:
    """Invocation Agent C is executing. It is the expected binding, not a grant."""

    principal_ref: str
    verified_subject: str
    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    policy_revision: str
    purpose: MasterPurpose
    specialist: MasterSpecialist
    capability: AgentCapability
    tool_name: str
    resource_scope: tuple[LogicalReadOperation, ...]
    region_scope: tuple[str, ...]
    accepted_at_utc: str
    deadline_utc: str
    expires_at_utc: str
    idempotency_key: str
    correlation_id: str
    token_jti: str
    issuer: str
    audience: str
    scopes: tuple[str, ...]
    policy_session_id: str
    policy_fingerprint: str
    argument_names: tuple[str, ...]
    temporal_scopes: tuple[str, ...]
    historical_window: tuple[str, str] | None
    decision_target_scope: str | None
    max_model_turns: int
    max_tool_calls: int


@dataclass(frozen=True)
class AgentCAtomicConsumeRequest:
    """What Agent C writes once. Verify this in the consuming process.

    ``grant_nonce`` is the signed nonce and the ledger nonce. Write the
    attempt item and the no-TTL marker in one transaction. Both puts use
    ``conditional_put_expression``. A second write of the same nonce is
    ``REPLAYED`` and must not open another ``STARTED`` attempt.
    ``distributed_exactly_once`` stays false. Do not call
    ``Sprint3CDurableGrantLedger.consume_signed_grant``.
    """

    schema_version: str
    grant_nonce: str
    signature_hex: str
    signing_key_id: str
    canonical_document: str
    signed_document_fingerprint: str
    ledger_payload_fingerprint: str
    ledger_partition_key: str
    ledger_sort_key: str
    consume_marker_partition_key: str
    consume_marker_sort_key: str
    principal_ref: str
    verified_subject: str
    issuer: str
    audience: str
    scopes: tuple[str, ...]
    token_jti: str
    master_request_id: str
    planning_session_id: str
    policy_session_id: str
    step_id: str
    invocation_id: str
    policy_revision: str
    policy_fingerprint: str
    purpose: str
    specialist: str
    capability: str
    tool_name: str
    resource_scope_token: str
    region_scope_token: str
    decision_target_scope: str
    accepted_at_utc: str
    deadline_utc: str
    expires_at_utc: str
    idempotency_key: str
    correlation_id: str
    max_model_turns: int
    max_tool_calls: int
    ledger_table_name: str
    conditional_put_expression: str
    consume_marker_has_ttl: bool
    retention_after_deadline_seconds: int
    repeat_non_idempotent_after_expiry: bool
    single_invocation: bool
    issuer_verify_consumes: bool
    hmac_prevents_replay: bool
    distributed_exactly_once: bool
    redeemable_on_production_route: bool
    aws_controls_verified: bool
    route_enablement_permitted: bool
    production_grant_issuance_available: bool
    must_reverify_before_write: bool
    grant_nonce_is_ledger_nonce: bool
    grant_nonce_is_attempt_token: bool
    fence_assigned_by: str
    do_not_call: str
    _seal: str = ""

    def __post_init__(self) -> None:
        if (
            self.schema_version != SPRINT3D_AUTH_GRANTS_SCHEMA_VERSION
            or self.ledger_table_name != DYNAMODB_LEDGER_TABLE_NAME
            or self.conditional_put_expression != CONDITIONAL_PUT_ABSENT
            or self.consume_marker_has_ttl is not False
            or self.retention_after_deadline_seconds != int(RETENTION_AFTER_DEADLINE.total_seconds())
            or self.repeat_non_idempotent_after_expiry is not False
            or self.single_invocation is not True
            or self.issuer_verify_consumes is not False
            or self.hmac_prevents_replay is not False
            or self.distributed_exactly_once is not False
            or self.redeemable_on_production_route is not False
            or self.aws_controls_verified is not False
            or self.route_enablement_permitted is not False
            or self.production_grant_issuance_available is not False
            or self.must_reverify_before_write is not True
            or self.grant_nonce_is_ledger_nonce is not True
            or self.grant_nonce_is_attempt_token is not False
            or self.fence_assigned_by != "agent_c"
            or self.do_not_call != _AGENT_C_DO_NOT_CALL
            or self.decision_target_scope != DECISION_TARGET_NOT_APPLICABLE
        ):
            _fail("invalid_agent_c_consume_request")
        if not hmac.compare_digest(self._seal, _digest(self.canonical_document + self.signature_hex)):
            _fail("caller_constructed_consume_request")

    def __bool__(self) -> bool:
        _fail("consume_request_is_not_a_ledger_write")
        raise AssertionError("unreachable")


@dataclass(frozen=True)
class AtomicConsumeCoordination:
    """Verification outcome. ``request`` is set only after the HMAC matches."""

    accepted: bool
    rejection: Sprint3DRejection | None
    request: AgentCAtomicConsumeRequest | None
    route_enablement_permitted: bool = False

    def __post_init__(self) -> None:
        if self.route_enablement_permitted is not False:
            _fail("route_enablement_blocked")
        if self.accepted:
            if self.rejection is not None or type(self.request) is not AgentCAtomicConsumeRequest:
                _fail("invalid_agent_c_consume_request")
        elif self.request is not None or self.rejection is None:
            _fail("invalid_agent_c_consume_request")

    def __bool__(self) -> bool:
        _fail("consume_request_is_not_a_ledger_write")
        raise AssertionError("unreachable")


def _resource_token(scope: tuple[LogicalReadOperation, ...]) -> str:
    parts = [f"{item.resource_id}:{item.operation.value}" for item in scope]
    parts.sort()
    return ",".join(parts)


def _region_token(region: tuple[str, ...]) -> str:
    return ",".join(region)


def _binding_rejection(document: Mapping[str, object], presented: AgentCPresentedBinding) -> Sprint3DRejection | None:
    pairs = (
        (document.get("principal_ref"), presented.principal_ref, Sprint3DRejection.PRINCIPAL_MISMATCH),
        (document.get("verified_subject"), presented.verified_subject, Sprint3DRejection.PRINCIPAL_MISMATCH),
        (document.get("master_request_id"), presented.master_request_id, Sprint3DRejection.REQUEST_MISMATCH),
        (document.get("planning_session_id"), presented.planning_session_id, Sprint3DRejection.SESSION_MISMATCH),
        (document.get("step_id"), presented.step_id, Sprint3DRejection.REQUEST_MISMATCH),
        (document.get("invocation_id"), presented.invocation_id, Sprint3DRejection.INVOCATION_MISMATCH),
        (document.get("policy_revision"), presented.policy_revision, Sprint3DRejection.POLICY_REVISION_MISMATCH),
        (document.get("purpose"), presented.purpose.value, Sprint3DRejection.REQUEST_MISMATCH),
        (document.get("specialist"), presented.specialist.value, Sprint3DRejection.REQUEST_MISMATCH),
        (document.get("capability"), presented.capability.value, Sprint3DRejection.REQUEST_MISMATCH),
        (document.get("tool_name"), presented.tool_name, Sprint3DRejection.BOUNDS_REJECTED),
        (document.get("accepted_at_utc"), presented.accepted_at_utc, Sprint3DRejection.REQUEST_MISMATCH),
        (document.get("deadline_utc"), presented.deadline_utc, Sprint3DRejection.DEADLINE_EXCEEDED),
        (document.get("expires_at_utc"), presented.expires_at_utc, Sprint3DRejection.EXPIRED_GRANT),
        (document.get("idempotency_key"), presented.idempotency_key, Sprint3DRejection.REPLAY),
        (document.get("correlation_id"), presented.correlation_id, Sprint3DRejection.REQUEST_MISMATCH),
        (document.get("token_jti"), presented.token_jti, Sprint3DRejection.REPLAY),
        (document.get("issuer"), presented.issuer, Sprint3DRejection.ISSUER_MISMATCH),
        (document.get("audience"), presented.audience, Sprint3DRejection.AUDIENCE_MISMATCH),
        (document.get("scopes"), list(presented.scopes), Sprint3DRejection.SCOPE_MISMATCH),
        (document.get("policy_session_id"), presented.policy_session_id, Sprint3DRejection.SESSION_MISMATCH),
        (document.get("policy_fingerprint"), presented.policy_fingerprint, Sprint3DRejection.POLICY_REVISION_MISMATCH),
        (document.get("argument_names"), list(presented.argument_names), Sprint3DRejection.BOUNDS_REJECTED),
        (document.get("temporal_scopes"), list(presented.temporal_scopes), Sprint3DRejection.BOUNDS_REJECTED),
        (document.get("historical_window"), None if presented.historical_window is None else list(presented.historical_window), Sprint3DRejection.BOUNDS_REJECTED),
        (document.get("decision_target_scope"), presented.decision_target_scope, Sprint3DRejection.BOUNDS_REJECTED),
        (document.get("max_model_turns"), presented.max_model_turns, Sprint3DRejection.BOUNDS_REJECTED),
        (document.get("max_tool_calls"), presented.max_tool_calls, Sprint3DRejection.BOUNDS_REJECTED),
        (document.get("resource_scope"), _resource_token(presented.resource_scope), Sprint3DRejection.BOUNDS_REJECTED),
        (sorted(document.get("region_scope") or []), sorted(presented.region_scope), Sprint3DRejection.BOUNDS_REJECTED),
    )
    for signed, expected, reason in pairs:
        if signed != expected:
            return reason
    return None


def _closed_document(document: Mapping[str, object]) -> Sprint3DRejection | None:
    if document.get("authority_domain") != AUTHORITY_DOMAIN:
        return Sprint3DRejection.FOREIGN_GRANT
    if document.get("schema_version") != HOST_GRANT_DOCUMENT_SCHEMA:
        return Sprint3DRejection.FOREIGN_GRANT
    if document.get("route_key") != REQUIRED_ROUTE_KEY:
        return Sprint3DRejection.ROUTE_ENABLEMENT_BLOCKED
    if (
        document.get("route_enablement_permitted") is not False
        or document.get("aws_controls_verified") is not False
        or document.get("redeemable_on_production_route") is not False
        or document.get("distributed_exactly_once") is not False
        or document.get("hmac_prevents_replay") is not False
        or document.get("single_invocation") is not True
    ):
        return Sprint3DRejection.ROUTE_ENABLEMENT_BLOCKED
    if tuple(sorted(document)) != _SIGNED_FIELDS:
        return Sprint3DRejection.TAMPERED
    return None


def verify_serialized_grant(
    keys: InjectedGrantSigningKeys,
    *,
    canonical_document: str,
    signature_hex: str,
    key_id: str,
    presented: AgentCPresentedBinding,
    now: datetime,
) -> AtomicConsumeCoordination:
    """Check the HMAC and bindings. This does not write and does not consume."""

    _closed_flags()
    if type(keys) is not InjectedGrantSigningKeys:
        _fail("invalid_signing_key")
    if type(presented) is not AgentCPresentedBinding:
        _fail("invalid_presented_binding")
    if type(canonical_document) is not str or type(key_id) is not str:
        return AtomicConsumeCoordination(False, Sprint3DRejection.FORGED_GRANT, None)
    moment = _utc(now)
    signature_problem = keys.verify(canonical_document, signature_hex, key_id)
    if signature_problem is not None:
        return AtomicConsumeCoordination(False, signature_problem, None)
    try:
        parsed = json.loads(canonical_document)
    except json.JSONDecodeError:
        return AtomicConsumeCoordination(False, Sprint3DRejection.TAMPERED, None)
    if type(parsed) is not dict or _canonical(parsed) != canonical_document:
        return AtomicConsumeCoordination(False, Sprint3DRejection.TAMPERED, None)
    closed = _closed_document(parsed)
    if closed is not None:
        return AtomicConsumeCoordination(False, closed, None)
    if parsed.get("key_id") != key_id:
        return AtomicConsumeCoordination(False, Sprint3DRejection.UNREVIEWED_SIGNING_KEY, None)
    if parsed.get("planning_session_id") == parsed.get("policy_session_id"):
        return AtomicConsumeCoordination(False, Sprint3DRejection.SESSION_MISMATCH, None)
    if parsed.get("principal_ref") == parsed.get("verified_subject"):
        return AtomicConsumeCoordination(False, Sprint3DRejection.CALLER_CONSTRUCTED_CLAIMS, None)
    region = parsed.get("region_scope")
    scopes = parsed.get("scopes")
    if type(region) is not list or any(type(item) is not str for item in region):
        return AtomicConsumeCoordination(False, Sprint3DRejection.TAMPERED, None)
    if type(scopes) is not list or any(type(item) is not str for item in scopes):
        return AtomicConsumeCoordination(False, Sprint3DRejection.TAMPERED, None)
    mismatch = _binding_rejection(parsed, presented)
    if mismatch is not None:
        return AtomicConsumeCoordination(False, mismatch, None)
    try:
        expires_at = _parse_utc(parsed["expires_at_utc"], "invalid_clock")
        deadline = _parse_utc(parsed["deadline_utc"], "invalid_clock")
        accepted = _parse_utc(parsed["accepted_at_utc"], "invalid_clock")
    except ContractValidationError:
        return AtomicConsumeCoordination(False, Sprint3DRejection.TAMPERED, None)
    if expires_at > deadline or accepted >= deadline:
        return AtomicConsumeCoordination(False, Sprint3DRejection.DEADLINE_EXCEEDED, None)
    if moment >= expires_at:
        return AtomicConsumeCoordination(False, Sprint3DRejection.EXPIRED_GRANT, None)
    nonce = parsed.get("nonce")
    if type(nonce) is not str or "#" in nonce or not _SUBJECT.fullmatch(nonce):
        return AtomicConsumeCoordination(False, Sprint3DRejection.TAMPERED, None)
    resource_scope = parsed.get("resource_scope")
    region_scope = parsed.get("region_scope")
    if type(resource_scope) is not str or type(region_scope) is not list:
        return AtomicConsumeCoordination(False, Sprint3DRejection.TAMPERED, None)
    window = parsed.get("historical_window")
    historical_start: str | None = None
    historical_end: str | None = None
    if window is None:
        historical_start = None
        historical_end = None
    elif (
        type(window) is list
        and len(window) == 2
        and type(window[0]) is str
        and type(window[1]) is str
    ):
        historical_start = window[0]
        historical_end = window[1]
    else:
        return AtomicConsumeCoordination(False, Sprint3DRejection.TAMPERED, None)
    try:
        envelope = MasterExecutionLedgerEnvelope(
            master_request_id=str(parsed["master_request_id"]),
            planning_session_id=str(parsed["planning_session_id"]),
            step_id=str(parsed["step_id"]),
            invocation_id=str(parsed["invocation_id"]),
            specialist=presented.specialist,
            purpose=presented.purpose,
            capability=presented.capability,
            principal_ref=str(parsed["principal_ref"]),
            policy_revision=str(parsed["policy_revision"]),
            resource_scope=resource_scope,
            region_scope=_region_token(tuple(region_scope)),
            decision_target_scope=DECISION_TARGET_NOT_APPLICABLE,
            accepted_at_utc=str(parsed["accepted_at_utc"]),
            deadline_utc=str(parsed["deadline_utc"]),
            idempotency_key=str(parsed["idempotency_key"]),
            max_model_turns=int(parsed["max_model_turns"]),
            max_tool_calls=int(parsed["max_tool_calls"]),
            correlation_id=str(parsed["correlation_id"]),
            historical_start_utc=historical_start,
            historical_end_utc=historical_end,
            repeat_non_idempotent_after_expiry=False,
        )
    except (ContractValidationError, TypeError, ValueError):
        return AtomicConsumeCoordination(False, Sprint3DRejection.BOUNDS_REJECTED, None)
    partition, sort_key = ledger_item_key(envelope)
    marker_sort = (
        f"{envelope.principal_ref}#{envelope.planning_session_id}#"
        f"{envelope.master_request_id}#{envelope.invocation_id}#{envelope.purpose.value}"
    )
    request = AgentCAtomicConsumeRequest(
        schema_version=SPRINT3D_AUTH_GRANTS_SCHEMA_VERSION,
        grant_nonce=nonce,
        signature_hex=signature_hex,
        signing_key_id=key_id,
        canonical_document=canonical_document,
        signed_document_fingerprint=hashlib.sha256(canonical_document.encode("utf-8")).hexdigest(),
        ledger_payload_fingerprint=envelope.fingerprint,
        ledger_partition_key=partition,
        ledger_sort_key=sort_key,
        consume_marker_partition_key=f"{CONSUME_MARKER_PREFIX}{nonce}",
        consume_marker_sort_key=marker_sort,
        principal_ref=str(parsed["principal_ref"]),
        verified_subject=str(parsed["verified_subject"]),
        issuer=str(parsed["issuer"]),
        audience=str(parsed["audience"]),
        scopes=tuple(parsed["scopes"]),
        token_jti=str(parsed["token_jti"]),
        master_request_id=envelope.master_request_id,
        planning_session_id=envelope.planning_session_id,
        policy_session_id=str(parsed["policy_session_id"]),
        step_id=envelope.step_id,
        invocation_id=envelope.invocation_id,
        policy_revision=envelope.policy_revision,
        policy_fingerprint=str(parsed["policy_fingerprint"]),
        purpose=envelope.purpose.value,
        specialist=envelope.specialist.value,
        capability=envelope.capability.value,
        tool_name=str(parsed["tool_name"]),
        resource_scope_token=resource_scope,
        region_scope_token=envelope.region_scope,
        decision_target_scope=DECISION_TARGET_NOT_APPLICABLE,
        accepted_at_utc=envelope.accepted_at_utc,
        deadline_utc=envelope.deadline_utc,
        expires_at_utc=str(parsed["expires_at_utc"]),
        idempotency_key=envelope.idempotency_key,
        correlation_id=envelope.correlation_id,
        max_model_turns=envelope.max_model_turns,
        max_tool_calls=envelope.max_tool_calls,
        ledger_table_name=DYNAMODB_LEDGER_TABLE_NAME,
        conditional_put_expression=CONDITIONAL_PUT_ABSENT,
        consume_marker_has_ttl=False,
        retention_after_deadline_seconds=int(RETENTION_AFTER_DEADLINE.total_seconds()),
        repeat_non_idempotent_after_expiry=False,
        single_invocation=True,
        issuer_verify_consumes=False,
        hmac_prevents_replay=False,
        distributed_exactly_once=False,
        redeemable_on_production_route=False,
        aws_controls_verified=False,
        route_enablement_permitted=False,
        production_grant_issuance_available=False,
        must_reverify_before_write=True,
        grant_nonce_is_ledger_nonce=True,
        grant_nonce_is_attempt_token=False,
        fence_assigned_by="agent_c",
        do_not_call=_AGENT_C_DO_NOT_CALL,
        _seal=_digest(canonical_document + signature_hex),
    )
    return AtomicConsumeCoordination(True, None, request)


def presented_binding_from_principal(
    principal: Sprint3DTrustedPrincipal,
    envelope: MasterExecutionGrantEnvelope,
) -> AgentCPresentedBinding:
    """Project the host principal and envelope into Agent C's expected binding."""

    if type(principal) is not Sprint3DTrustedPrincipal:
        _fail("caller_constructed_principal")
    if type(envelope) is not MasterExecutionGrantEnvelope:
        _fail("invalid_grant_envelope")
    checked = evaluate_grant_envelope(envelope)
    argument_names = tuple(checked.argument_names or ())
    return AgentCPresentedBinding(
        principal_ref=principal.principal_ref,
        verified_subject=principal.verified_subject,
        master_request_id=principal.master_request_id,
        planning_session_id=principal.planning_session_id,
        step_id=principal.step_id,
        invocation_id=principal.invocation_id,
        policy_revision=principal.policy_revision,
        purpose=envelope.purpose,
        specialist=envelope.specialist,
        capability=envelope.capability,
        tool_name=envelope.tool_name,
        resource_scope=envelope.resource_scope,
        region_scope=envelope.region_scope,
        accepted_at_utc=envelope.accepted_at_utc,
        deadline_utc=envelope.deadline_utc,
        expires_at_utc=envelope.expires_at_utc,
        idempotency_key=envelope.idempotency_key,
        correlation_id=envelope.correlation_id,
        token_jti=principal.token_jti,
        issuer=principal.issuer,
        audience=principal.audience,
        scopes=principal.scopes,
        policy_session_id=principal.policy_session_id,
        policy_fingerprint=principal.policy_fingerprint,
        argument_names=argument_names,
        temporal_scopes=tuple(item.value for item in envelope.temporal_scopes),
        historical_window=envelope.historical_window,
        decision_target_scope=envelope.decision_target_scope,
        max_model_turns=envelope.max_model_turns,
        max_tool_calls=envelope.max_tool_calls,
    )


class HostOwnedGrantIssuer:
    """Signs one grant per invocation, token ``jti``, and idempotency key.

    The in-process sets stop a second issue in this process. They are not
    the ledger. Agent C's conditional put is the cross-process single use.
    """

    def __init__(self, keys: InjectedGrantSigningKeys) -> None:
        _closed_flags()
        if type(keys) is not InjectedGrantSigningKeys:
            _fail("invalid_signing_key")
        self._keys = keys
        self._lock = threading.Lock()
        self._invocations: set[str] = set()
        self._idempotency: set[str] = set()
        self._jtis: set[str] = set()
        self.host_signed_grants = 0
        self.production_grants_issued = 0
        self.kms_integrated = False

    def issue(
        self,
        establishment: TrustedPrincipalEstablishment,
        envelope: MasterExecutionGrantEnvelope,
        *,
        now: datetime,
    ) -> tuple[HostSignedGrant | None, AtomicConsumeCoordination]:
        _closed_flags()
        if type(establishment) is not TrustedPrincipalEstablishment:
            _fail("invalid_principal")
        if type(envelope) is not MasterExecutionGrantEnvelope:
            _fail("invalid_grant_envelope")
        moment = _utc(now)
        if not establishment.grant_eligible or establishment.principal is None:
            reason = establishment.rejection or Sprint3DRejection.POLICY_VIOLATION
            return None, AtomicConsumeCoordination(False, reason, None)
        principal = establishment.principal
        if envelope.principal_ref != principal.principal_ref:
            return None, AtomicConsumeCoordination(False, Sprint3DRejection.PRINCIPAL_MISMATCH, None)
        if envelope.planning_session_id != principal.planning_session_id:
            return None, AtomicConsumeCoordination(False, Sprint3DRejection.SESSION_MISMATCH, None)
        if (
            envelope.master_request_id != principal.master_request_id
            or envelope.step_id != principal.step_id
            or envelope.invocation_id != principal.invocation_id
            or envelope.correlation_id != principal.correlation_id
            or envelope.idempotency_key != principal.idempotency_key
            or envelope.tool_name != principal.tool_name
            or envelope.policy_revision != principal.policy_revision
            or envelope.accepted_at_utc != principal.accepted_at_utc
            or envelope.deadline_utc != principal.deadline_utc
            or envelope.purpose is not principal.purpose
        ):
            return None, AtomicConsumeCoordination(False, Sprint3DRejection.REQUEST_MISMATCH, None)
        evaluation = evaluate_grant_envelope(envelope)
        if not evaluation.bounds_accepted or evaluation.region_scope != principal.region_scope:
            return None, AtomicConsumeCoordination(False, Sprint3DRejection.BOUNDS_REJECTED, None)
        if _resource_token(envelope.resource_scope) != f"{principal.resource_id}:{principal.operation}":
            return None, AtomicConsumeCoordination(False, Sprint3DRejection.BOUNDS_REJECTED, None)
        expires_at = _parse_utc(envelope.expires_at_utc, "invalid_clock")
        deadline = _parse_utc(envelope.deadline_utc, "invalid_clock")
        if expires_at > deadline or moment >= expires_at:
            return None, AtomicConsumeCoordination(False, Sprint3DRejection.EXPIRED_GRANT, None)
        if _epoch(expires_at) > principal.token_expires_at_epoch:
            return None, AtomicConsumeCoordination(False, Sprint3DRejection.TOKEN_EXPIRED, None)
        with self._lock:
            if (
                principal.invocation_id in self._invocations
                or envelope.idempotency_key in self._idempotency
                or principal.token_jti in self._jtis
            ):
                return None, AtomicConsumeCoordination(False, Sprint3DRejection.REPLAY, None)
            nonce = secrets.token_hex(16)
            document = {
                "schema_version": HOST_GRANT_DOCUMENT_SCHEMA,
                "authority_domain": AUTHORITY_DOMAIN,
                "master_request_id": principal.master_request_id,
                "planning_session_id": principal.planning_session_id,
                "policy_session_id": principal.policy_session_id,
                "step_id": principal.step_id,
                "invocation_id": principal.invocation_id,
                "specialist": envelope.specialist.value,
                "purpose": envelope.purpose.value,
                "capability": envelope.capability.value,
                "principal_ref": principal.principal_ref,
                "verified_subject": principal.verified_subject,
                "issuer": principal.issuer,
                "audience": principal.audience,
                "scopes": list(principal.scopes),
                "token_jti": principal.token_jti,
                "policy_revision": principal.policy_revision,
                "policy_fingerprint": principal.policy_fingerprint,
                "tool_name": evaluation.tool_name,
                "resource_scope": _resource_token(evaluation.resource_scope or ()),
                "argument_names": list(evaluation.argument_names or ()),
                "region_scope": list(evaluation.region_scope or ()),
                "temporal_scopes": [item.value for item in (evaluation.temporal_scopes or ())],
                "historical_window": (
                    None
                    if evaluation.historical_window is None
                    else [evaluation.historical_window[0], evaluation.historical_window[1]]
                ),
                "decision_target_scope": evaluation.decision_target_scope,
                "accepted_at_utc": envelope.accepted_at_utc,
                "deadline_utc": envelope.deadline_utc,
                "expires_at_utc": envelope.expires_at_utc,
                "idempotency_key": envelope.idempotency_key,
                "nonce": nonce,
                "max_model_turns": envelope.max_model_turns,
                "max_tool_calls": envelope.max_tool_calls,
                "correlation_id": envelope.correlation_id,
                "key_id": self._keys.active_key_id,
                "route_key": REQUIRED_ROUTE_KEY,
                "route_enablement_permitted": False,
                "aws_controls_verified": False,
                "redeemable_on_production_route": False,
                "single_invocation": True,
                "hmac_prevents_replay": False,
                "distributed_exactly_once": False,
            }
            canonical = _canonical(document)
            key_id, signature = self._keys.sign(canonical)
        grant = _seal_grant(canonical, signature, key_id, nonce)
        coordination = verify_serialized_grant(
            self._keys,
            canonical_document=canonical,
            signature_hex=signature,
            key_id=key_id,
            presented=presented_binding_from_principal(principal, envelope),
            now=moment,
        )
        if not coordination.accepted:
            return None, coordination
        with self._lock:
            if (
                principal.invocation_id in self._invocations
                or envelope.idempotency_key in self._idempotency
                or principal.token_jti in self._jtis
            ):
                return None, AtomicConsumeCoordination(False, Sprint3DRejection.REPLAY, None)
            self._invocations.add(principal.invocation_id)
            self._idempotency.add(envelope.idempotency_key)
            self._jtis.add(principal.token_jti)
            self.host_signed_grants += 1
        return grant, coordination


@dataclass(frozen=True)
class SameAccountInvokeStatement:
    """One identity-policy statement the operator enumerated. Not a live IAM call."""

    principal_arn: str
    effect: str
    action: str
    resource: str

    def __post_init__(self) -> None:
        if type(self.principal_arn) is not str or self.principal_arn == "" or any(ch.isspace() for ch in self.principal_arn):
            _fail("invalid_identity_statement")
        if self.effect not in {"Allow", "Deny"}:
            _fail("invalid_identity_statement")
        if type(self.action) is not str or type(self.resource) is not str:
            _fail("invalid_identity_statement")


@dataclass(frozen=True)
class LambdaInvokePathReview:
    """Document review of invoke paths. Live AWS controls stay unsatisfied."""

    document_review_accepted: bool
    rejection: Sprint3DRejection | None
    described_same_account_posture: str
    route_enablement_permitted: bool
    live_aws_controls_verified: bool
    live_blockers: tuple[str, ...]
    document_defects: tuple[Sprint3DRejection, ...]

    def __post_init__(self) -> None:
        if self.route_enablement_permitted is not False or self.live_aws_controls_verified is not False:
            _fail("route_enablement_blocked")
        if self.live_blockers != ISSUANCE_CONTROL_IDS:
            _fail("issuance_control_not_satisfied")
        if self.described_same_account_posture not in {"closed", "open", "not_enumerated"}:
            _fail("invalid_identity_statement")

    def __bool__(self) -> bool:
        _fail("invoke_review_is_not_route_enablement")
        raise AssertionError("unreachable")


def _statement_list(policy: object) -> list[dict[str, object]] | None:
    if type(policy) is not dict:
        return None
    statements = policy.get("Statement")
    if type(statements) is dict:
        statements = [statements]
    if type(statements) is not list or len(statements) != 1 or type(statements[0]) is not dict:
        return None
    return statements


def _action_name(action: object) -> str | None:
    if type(action) is str:
        return action
    if type(action) is list and len(action) == 1 and type(action[0]) is str:
        return action[0]
    return None


def _source_arn(statement: Mapping[str, object]) -> str | None:
    condition = statement.get("Condition")
    if type(condition) is not dict:
        return None
    for key in ("ArnLike", "ArnEquals", "StringEquals"):
        block = condition.get(key)
        if type(block) is not dict:
            continue
        for name in ("AWS:SourceArn", "aws:SourceArn"):
            value = block.get(name)
            if type(value) is str:
                return value
            if type(value) is list and len(value) == 1 and type(value[0]) is str:
                return value[0]
    return None


def _service_principal(principal: object) -> str | None:
    if type(principal) is str:
        return principal
    if type(principal) is not dict:
        return None
    service = principal.get("Service")
    if type(service) is str:
        return service
    if type(service) is list and len(service) == 1 and type(service[0]) is str:
        return service[0]
    return None


def _covers(statement: SameAccountInvokeStatement, allow: SameAccountInvokeStatement, function_arn: str) -> bool:
    if statement.effect != "Deny":
        return False
    action_covers = statement.action in {allow.action, "lambda:*", "*"} or (
        allow.action == REQUIRED_INVOKE_ACTION and statement.action in {"lambda:*", "*"}
    )
    resource_covers = statement.resource in {allow.resource, function_arn, "*"}
    principal_covers = statement.principal_arn in {allow.principal_arn, "*"}
    return action_covers and resource_covers and principal_covers


def review_lambda_invoke_paths(
    *,
    resource_policy: object,
    identity_statements: object,
    identity_policies_enumerated: bool,
    function_url_present: bool,
    async_invoke_sources: tuple[str, ...],
    expected_source_arn: str,
    expected_function_arn: str,
    live_aws_api_called: bool = False,
) -> LambdaInvokePathReview:
    """Review supplied policy documents. A caller flag cannot mark AWS inspected."""

    _closed_flags()
    blockers = ISSUANCE_CONTROL_IDS
    if live_aws_api_called is not False:
        return LambdaInvokePathReview(
            False,
            Sprint3DRejection.FORGED_LIVE_AWS_CLAIM,
            "not_enumerated",
            False,
            False,
            blockers,
            (Sprint3DRejection.FORGED_LIVE_AWS_CLAIM,),
        )
    defects: list[Sprint3DRejection] = []
    if type(identity_policies_enumerated) is not bool:
        _fail("invalid_identity_statement")
    if function_url_present is not False:
        defects.append(Sprint3DRejection.FUNCTION_URL)
    if type(async_invoke_sources) is not tuple or async_invoke_sources:
        defects.append(Sprint3DRejection.ASYNC_INVOKE_SOURCE)
    if (
        type(expected_source_arn) is not str
        or not expected_source_arn.endswith(f"/{REQUIRED_STAGE_NAME}/POST/ai/query")
        or "*" in expected_source_arn
        or not expected_source_arn.startswith("arn:")
    ):
        defects.append(Sprint3DRejection.WILDCARD_INVOKE)
    statements = _statement_list(resource_policy)
    if statements is None:
        defects.append(Sprint3DRejection.OTHER_INVOKE_PRINCIPAL)
    else:
        statement = statements[0]
        action = _action_name(statement.get("Action"))
        source = _source_arn(statement)
        resource = statement.get("Resource")
        principal = _service_principal(statement.get("Principal"))
        if statement.get("Effect") != "Allow":
            defects.append(Sprint3DRejection.OTHER_INVOKE_PRINCIPAL)
        if action != REQUIRED_INVOKE_ACTION or (type(action) is str and "*" in action):
            defects.append(Sprint3DRejection.WILDCARD_INVOKE)
        if principal != REQUIRED_GATEWAY_PRINCIPAL:
            defects.append(Sprint3DRejection.OTHER_INVOKE_PRINCIPAL)
        if source != expected_source_arn or source is None or "*" in (source or ""):
            defects.append(Sprint3DRejection.WILDCARD_INVOKE)
        if resource != expected_function_arn or not isinstance(resource, str) or "*" in resource:
            defects.append(Sprint3DRejection.WILDCARD_INVOKE)
    posture = "not_enumerated"
    if identity_policies_enumerated is not True:
        defects.append(Sprint3DRejection.SAME_ACCOUNT_INVOKE_UNREVIEWED)
    elif type(identity_statements) is not tuple or any(
        type(item) is not SameAccountInvokeStatement for item in identity_statements
    ):
        defects.append(Sprint3DRejection.SAME_ACCOUNT_INVOKE_UNREVIEWED)
    else:
        allows = tuple(
            item
            for item in identity_statements
            if item.effect == "Allow"
            and item.action in {REQUIRED_INVOKE_ACTION, "lambda:*", "*"}
        )
        denies = tuple(item for item in identity_statements if item.effect == "Deny")
        uncovered = [
            allow
            for allow in allows
            if not any(_covers(deny, allow, expected_function_arn) for deny in denies)
        ]
        if uncovered:
            defects.append(Sprint3DRejection.SAME_ACCOUNT_INVOKE_ALLOWED)
            posture = "open"
        else:
            posture = "closed"
    unique: list[Sprint3DRejection] = []
    for defect in defects:
        if defect not in unique:
            unique.append(defect)
    accepted = not unique
    return LambdaInvokePathReview(
        accepted,
        None if accepted else unique[0],
        posture if identity_policies_enumerated else "not_enumerated",
        False,
        False,
        blockers,
        tuple(unique),
    )


def route_enablement_permitted(_review: LambdaInvokePathReview | None = None) -> bool:
    """The route stays disabled. Document review is not a live AWS inspection."""

    _closed_flags()
    return False


def assert_closed_production_entrypoints() -> None:
    """The pre-existing issuers still raise. This module does not replace them."""

    try:
        issue_production_execution_grant()
    except ContractValidationError:
        grant_closed = True
    else:
        grant_closed = False
    try:
        issue_production_application_principal()
    except ContractValidationError:
        principal_closed = True
    else:
        principal_closed = False
    if not grant_closed or not principal_closed:
        _fail("production_issuance_unavailable")
