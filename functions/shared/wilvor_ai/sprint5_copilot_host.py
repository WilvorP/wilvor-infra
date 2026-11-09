"""Sprint 5 Copilot host contract. POST /ai/query stays disabled.

Browser Copilot ids are untrusted labels. JWT verification is fail-closed
and is not grant issuance. The handler never treats a browser session id as
authentication or MasterPlanningSessionRegistry membership.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_planning_session import MasterPlanningSessionRegistry
from wilvor_ai.sprint4_copilot_contract_map import (
    SPRINT4_AI_QUERY_ROUTE_ENABLED,
    Sprint4CopilotMappedRequest,
    map_copilot_session_query,
    project_ma5_to_copilot,
)
from wilvor_ai.sprint4_master_planning import Sprint4MasterPlanningResult, plan_master_request
from wilvor_ai.sprint5_identity import (
    Sprint5HostOwnedIdentities,
    Sprint5UntrustedClientLabels,
    client_labels_from_copilot,
    host_planning_context,
    mint_host_owned_identities,
    open_host_planning_session,
)


SPRINT5_COPILOT_HOST_SCHEMA = "wilvor.ai.sprint5.copilot_host.v1"
SPRINT5_AI_QUERY_ROUTE_ENABLED = False
BROWSER_SESSION_IS_AUTHENTICATION = False
JWT_CLAIMS_ARE_GRANT_AUTHORITY = False
UNVERIFIED_JWT_DECODING_IS_AUTHENTICATION = False


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _closed() -> None:
    if SPRINT5_AI_QUERY_ROUTE_ENABLED is not False or SPRINT4_AI_QUERY_ROUTE_ENABLED is not False:
        _fail("ai_query_route_must_remain_disabled")
    if BROWSER_SESSION_IS_AUTHENTICATION is not False:
        _fail("browser_session_is_not_authentication")
    if JWT_CLAIMS_ARE_GRANT_AUTHORITY is not False:
        _fail("jwt_is_not_grant_authority")
    if UNVERIFIED_JWT_DECODING_IS_AUTHENTICATION is not False:
        _fail("unverified_jwt_is_not_authentication")


class Sprint5JwtVerifier(Protocol):
    def verify(self, token: str) -> "Sprint5VerifiedPrincipal": ...


@dataclass(frozen=True)
class Sprint5VerifiedPrincipal:
    """Verified subject. Possession is not a production grant."""

    subject: str
    issuer: str
    audience: str
    grant_authority: bool = False
    registry_membership: bool = False
    production_principal: bool = False

    def __post_init__(self) -> None:
        _closed()
        if not self.subject or self.subject != self.subject.strip():
            _fail("invalid_jwt_subject")
        if self.grant_authority is not False:
            _fail("jwt_is_not_grant_authority")
        if self.registry_membership is not False:
            _fail("copied_session_identifier")
        if self.production_principal is not False:
            _fail("production_principal_unissued")


@dataclass(frozen=True)
class Sprint5CopilotHostAdmission:
    """Host-owned Copilot admission. Browser ids remain labels."""

    principal: Sprint5VerifiedPrincipal
    labels: Sprint5UntrustedClientLabels
    identities: Sprint5HostOwnedIdentities
    mapped: Sprint4CopilotMappedRequest
    schema_version: str = SPRINT5_COPILOT_HOST_SCHEMA

    def __post_init__(self) -> None:
        _closed()
        if self.schema_version != SPRINT5_COPILOT_HOST_SCHEMA:
            _fail("invalid_schema_version")


def activate_public_ai_query_route(*_args: object, **_kwargs: object) -> None:
    _closed()
    raise ContractValidationError("ai_query_route_must_remain_disabled")


def authenticate_copilot_request(
    headers: Mapping[str, Any],
    verifier: Sprint5JwtVerifier,
) -> Sprint5VerifiedPrincipal:
    """Fail closed without a verified Bearer token. Token is not a grant."""

    _closed()
    if verifier is None or not callable(getattr(verifier, "verify", None)):
        _fail("jwt_verifier_required")
    if not isinstance(headers, Mapping):
        _fail("jwt_required")
    raw = headers.get("Authorization") or headers.get("authorization")
    if not isinstance(raw, str) or not raw.startswith("Bearer "):
        _fail("jwt_required")
    token = raw[7:].strip()
    if not token:
        _fail("jwt_required")
    principal = verifier.verify(token)
    if not isinstance(principal, Sprint5VerifiedPrincipal):
        _fail("jwt_verification_failed")
    return principal


def admit_copilot_query(
    payload: Mapping[str, Any],
    *,
    headers: Mapping[str, Any],
    verifier: Sprint5JwtVerifier,
    registry: MasterPlanningSessionRegistry | None = None,
) -> Sprint5CopilotHostAdmission:
    """Map a Copilot envelope after JWT verification. Route stays disabled."""

    _closed()
    principal = authenticate_copilot_request(headers, verifier)
    mapped = map_copilot_session_query(payload)
    labels = client_labels_from_copilot(mapped)
    identities = mint_host_owned_identities()
    if identities.planning_session_id == labels.planning_session_id:
        _fail("copied_session_identifier")
    if registry is not None and type(registry) is not MasterPlanningSessionRegistry:
        _fail("invalid_session")
    return Sprint5CopilotHostAdmission(
        principal=principal,
        labels=labels,
        identities=identities,
        mapped=mapped,
    )


def plan_admitted_copilot_query(
    admission: Sprint5CopilotHostAdmission,
    planner: object,
    *,
    registry: MasterPlanningSessionRegistry,
) -> Sprint4MasterPlanningResult:
    """Plan with host-minted ids. Browser session id is not membership."""

    _closed()
    if not isinstance(admission, Sprint5CopilotHostAdmission):
        _fail("invalid_copilot_request")
    context = host_planning_context(admission.mapped, admission.identities)
    result = plan_master_request(context, planner)
    if result.proposal is not None:
        open_host_planning_session(registry, result.proposal, context)
    return result


def project_copilot_result(**kwargs: Any) -> object:
    _closed()
    projected = project_ma5_to_copilot(**kwargs)
    authenticity = getattr(projected, "source_authenticity", None)
    if authenticity not in (None, MasterSourceAuthenticity.NOT_ESTABLISHED.value):
        _fail("source_authenticity_not_established")
    return projected


__all__ = [
    "BROWSER_SESSION_IS_AUTHENTICATION",
    "JWT_CLAIMS_ARE_GRANT_AUTHORITY",
    "SPRINT5_AI_QUERY_ROUTE_ENABLED",
    "SPRINT5_COPILOT_HOST_SCHEMA",
    "UNVERIFIED_JWT_DECODING_IS_AUTHENTICATION",
    "Sprint5CopilotHostAdmission",
    "Sprint5JwtVerifier",
    "Sprint5VerifiedPrincipal",
    "activate_public_ai_query_route",
    "admit_copilot_query",
    "authenticate_copilot_request",
    "plan_admitted_copilot_query",
    "project_copilot_result",
]
