"""Sprint 5 host-owned request identity and session membership.

Browser Copilot ids are untrusted labels. They are not Master planning
session membership, JWT authentication, or grant authority. The host mints
pairwise-distinct request identities and opens a process-local planning
session after MA2 planning. Tool-call ceilings stay on the server catalog.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Mapping

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_contracts import MasterSpecialist
from wilvor_ai.master_planning_session import (
    MasterPlanningSessionRecord,
    MasterPlanningSessionRegistry,
)
from wilvor_ai.master_read_resource_guard import server_read_budget
from wilvor_ai.master_routing import MasterPlanningContext, MasterRouteProposal
from wilvor_ai.sprint4_copilot_contract_map import Sprint4CopilotMappedRequest
from wilvor_ai.sprint4_master_providers import (
    MASTER_PLANNER_MAX_TOOL_CALLS,
    MASTER_PLANNER_MAX_TURNS,
)


SPRINT5_IDENTITY_SCHEMA = "wilvor.ai.sprint5.identity.v1"
BROWSER_IDS_ARE_HOST_IDENTITY = False
BROWSER_SESSION_IS_REGISTRY_MEMBERSHIP = False
HOST_IDENTITY_COUNT = 6


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _closed() -> None:
    if BROWSER_IDS_ARE_HOST_IDENTITY is not False:
        _fail("browser_ids_are_not_host_identity")
    if BROWSER_SESSION_IS_REGISTRY_MEMBERSHIP is not False:
        _fail("copied_session_identifier")


def _token() -> str:
    return secrets.token_hex(16)


@dataclass(frozen=True)
class Sprint5UntrustedClientLabels:
    """Browser-supplied ids. Possession is not authentication."""

    master_request_id: str
    planning_session_id: str
    conversation_id: str | None
    correlation_id: str
    host_identity: bool = False
    registry_membership: bool = False
    grant_authority: bool = False

    def __post_init__(self) -> None:
        _closed()
        if self.host_identity is not False:
            _fail("browser_ids_are_not_host_identity")
        if self.registry_membership is not False:
            _fail("copied_session_identifier")
        if self.grant_authority is not False:
            _fail("browser_ids_are_not_grant_authority")


@dataclass(frozen=True)
class Sprint5HostOwnedIdentities:
    """Six pairwise-distinct host tokens. Not a production grant."""

    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    specialist_request_id: str
    correlation_id: str
    schema_version: str = SPRINT5_IDENTITY_SCHEMA

    def __post_init__(self) -> None:
        _closed()
        values = (
            self.master_request_id,
            self.planning_session_id,
            self.step_id,
            self.invocation_id,
            self.specialist_request_id,
            self.correlation_id,
        )
        if any(
            not isinstance(item, str) or not item or item != item.strip() or any(ch.isspace() for ch in item)
            for item in values
        ):
            _fail("request_identity_not_distinct")
        if len(set(values)) != HOST_IDENTITY_COUNT:
            _fail("request_identity_not_distinct")

    def as_tuple(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.master_request_id,
            self.planning_session_id,
            self.step_id,
            self.invocation_id,
            self.specialist_request_id,
            self.correlation_id,
        )


def mint_host_owned_identities() -> Sprint5HostOwnedIdentities:
    """Mint six distinct host tokens. Browser strings are not reused."""

    _closed()
    seen: set[str] = set()
    tokens: list[str] = []
    while len(tokens) < HOST_IDENTITY_COUNT:
        token = _token()
        if token not in seen:
            seen.add(token)
            tokens.append(token)
    return Sprint5HostOwnedIdentities(
        master_request_id=tokens[0],
        planning_session_id=tokens[1],
        step_id=tokens[2],
        invocation_id=tokens[3],
        specialist_request_id=tokens[4],
        correlation_id=tokens[5],
    )


def client_labels_from_copilot(
    mapped: Sprint4CopilotMappedRequest,
) -> Sprint5UntrustedClientLabels:
    """Copy browser labels. Copying does not mint host identity."""

    _closed()
    if not isinstance(mapped, Sprint4CopilotMappedRequest):
        _fail("invalid_copilot_request")
    return Sprint5UntrustedClientLabels(
        master_request_id=mapped.planning.master_request_id,
        planning_session_id=mapped.planning_session_id,
        conversation_id=mapped.conversation_id,
        correlation_id=mapped.planning.correlation_id,
    )


def host_planning_context(
    mapped: Sprint4CopilotMappedRequest,
    identities: Sprint5HostOwnedIdentities,
) -> MasterPlanningContext:
    """Trusted planning context using host-minted ids, not browser ids."""

    _closed()
    if not isinstance(mapped, Sprint4CopilotMappedRequest):
        _fail("invalid_copilot_request")
    if not isinstance(identities, Sprint5HostOwnedIdentities):
        _fail("invalid_host_identity")
    if identities.master_request_id == mapped.planning.master_request_id:
        _fail("browser_ids_are_not_host_identity")
    if identities.correlation_id == mapped.planning.correlation_id:
        _fail("browser_ids_are_not_host_identity")
    if identities.planning_session_id == mapped.planning_session_id:
        _fail("copied_session_identifier")
    return MasterPlanningContext(
        master_request_id=identities.master_request_id,
        correlation_id=identities.correlation_id,
        application_request_time_utc=mapped.planning.application_request_time_utc,
        canonical_user_text=mapped.planning.canonical_user_text,
        target_candidate_aircraft_id=mapped.planning.target_candidate_aircraft_id,
        target_candidate_recommendation_id=mapped.planning.target_candidate_recommendation_id,
    )


def open_host_planning_session(
    registry: MasterPlanningSessionRegistry,
    proposal: MasterRouteProposal,
    context: MasterPlanningContext,
) -> MasterPlanningSessionRecord:
    """Open process-local registry membership. Browser session id is unused."""

    _closed()
    if type(registry) is not MasterPlanningSessionRegistry:
        _fail("invalid_session")
    return registry.open_session(proposal, context)


def specialist_tool_budget(specialist: MasterSpecialist) -> tuple[int, int, int]:
    """Server catalog ceilings. Models cannot raise them."""

    return server_read_budget(specialist)


def master_planner_budget() -> tuple[int, int]:
    return MASTER_PLANNER_MAX_TURNS, MASTER_PLANNER_MAX_TOOL_CALLS


def identity_inventory() -> dict[str, object]:
    _closed()
    return {
        "schema_version": SPRINT5_IDENTITY_SCHEMA,
        "host_identity_count": HOST_IDENTITY_COUNT,
        "browser_ids_are_host_identity": False,
        "browser_session_is_registry_membership": False,
        "master_planner_max_turns": MASTER_PLANNER_MAX_TURNS,
        "master_planner_max_tool_calls": MASTER_PLANNER_MAX_TOOL_CALLS,
        "specialist_budgets": {
            item.value: server_read_budget(item) for item in MasterSpecialist
        },
    }


__all__ = [
    "BROWSER_IDS_ARE_HOST_IDENTITY",
    "BROWSER_SESSION_IS_REGISTRY_MEMBERSHIP",
    "HOST_IDENTITY_COUNT",
    "SPRINT5_IDENTITY_SCHEMA",
    "Sprint5HostOwnedIdentities",
    "Sprint5UntrustedClientLabels",
    "client_labels_from_copilot",
    "host_planning_context",
    "identity_inventory",
    "master_planner_budget",
    "mint_host_owned_identities",
    "open_host_planning_session",
    "specialist_tool_budget",
]
