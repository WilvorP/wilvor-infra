"""Dashboard Copilot session-query mapping onto existing Master contracts.

``wilvor.ai.copilot.session_query.v1`` is a dashboard-owned envelope. It is
not a Python backend schema and it does not enable ``POST /ai/query``.

This module copies Copilot camelCase fields onto existing Master / Live Ops
types. It does not redefine those types. Fixture evidence rows are display
data, not authenticated AWS evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.live_ops import LIVE_OPS_TOOLS
from wilvor_ai.live_ops_public_contracts import (
    LIVE_OPS_COMPLETENESS_NOTICE,
    LIVE_OPS_PRIOR_TURN_MAX,
    LIVE_OPS_PRIOR_TURN_TEXT_MAX_LENGTH,
    LIVE_OPS_PRIOR_TURNS_TOTAL_MAX_LENGTH,
    LIVE_OPS_PUBLIC_SCHEMA_VERSION,
    LIVE_OPS_PUBLIC_TEXT_MAX_LENGTH,
    LiveOpsSelectorHints,
    LiveOpsUntrustedTurn,
)
from wilvor_ai.master_contracts import MasterProductionAuthority
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_parallel_contracts import MasterResponseLimitation
from wilvor_ai.master_response_policy import MasterResponsePolicyState
from wilvor_ai.master_response_renderer import MasterResponseRender
from wilvor_ai.master_routing import MasterPlanningContext


SPRINT4_COPILOT_CONTRACT_MAP_SCHEMA = "wilvor.ai.sprint4.copilot_contract_map.v1"
COPILOT_SESSION_QUERY_SCHEMA = "wilvor.ai.copilot.session_query.v1"
AI_QUERY_ROUTE_KEY = "POST /ai/query"
SPRINT4_AI_QUERY_ROUTE_ENABLED = False
COPILOT_FIXTURE_EVIDENCE_IS_AUTHENTICATED_AWS = False

LIVE_OPS_CATALOG_TOOL_NAMES = frozenset(item.name for item in LIVE_OPS_TOOLS)

# Dashboard fixture / display names are not Live Ops catalog tools.
COPILOT_DISPLAY_TOOL_NAME_ALIASES = {
    "get_aircraft_current_state": "get_aircraft_operational_context",
    "get_hazard": "get_hazard_operational_context",
    "get_encounter": "search_current_encounters",
}

# Copilot selector hints are a subset of LiveOpsSelectorHints.
COPILOT_SELECTOR_FIELDS = ("aircraftId", "callsign", "hazardId", "airportId")
LIVE_OPS_SELECTOR_FIELDS = (
    "region",
    "product_type",
    "hazard_type",
    "hazard_ids",
    "aircraft_id",
    "callsign",
    "airport_id",
    "hazard_id",
)

COPILOT_TO_MASTER_FIELD_MAP = (
    ("schemaVersion", "none; dashboard envelope, not a Python schema"),
    ("liveOpsSchemaVersion", "LiveOpsRunRequest.schema_version"),
    ("masterRequestId", "MasterPlanningContext.master_request_id"),
    ("planningSessionId", "MasterPlanningSessionRecord.session_id; not a principal"),
    ("conversationId", "LiveOpsRunRequest.conversation_id (optional on Python)"),
    ("correlationId", "MasterPlanningContext.correlation_id"),
    ("applicationRequestTimeUtc", "MasterPlanningContext.application_request_time_utc"),
    ("userText", "MasterPlanningContext.canonical_user_text and LiveOpsRunRequest.user_text"),
    ("priorTurns", "LiveOpsRunRequest.prior_turns"),
    ("selectorHints", "LiveOpsSelectorHints subset; region/product/hazard_ids omitted"),
)

REQUIRED_MA5_LIMITATIONS = (
    MasterResponseLimitation.SOURCE_AUTHENTICITY_NOT_ESTABLISHED,
    MasterResponseLimitation.PRODUCTION_AUTHORITY_DENIED,
    MasterResponseLimitation.NO_OPERATIONAL_CLAIM,
    MasterResponseLimitation.EXECUTION_DENIED,
)

COPILOT_DRIFT_NOTES = (
    "Copilot schemaVersion is dashboard-owned. Do not persist it as a backend schema.",
    "Browser-minted planningSessionId is not MasterPlanningSessionRegistry membership.",
    "Copilot requires conversationId; LiveOpsRunRequest.conversation_id may be None.",
    "Copilot distincts four client ids. Sprint 3C still requires six pairwise-distinct host identities.",
    "Disabled-route Copilot UNAVAILABLE is a UI stand-in. MA5 empty evidence is INSUFFICIENT_EVIDENCE.",
    "assistantText must equal MA5 status_text. Never copy LiveOpsRunResult.answer.",
    "Fixture tool names get_aircraft_current_state/get_hazard/get_encounter are not Live Ops catalog tools.",
    "Fixture freshnessBand AVAILABLE is dashboard SIGMET vocabulary, not FreshnessStatus.",
    "Copilot evidence rows are display projections, not Evidence or TrustedAwsSourceReceipt.",
)


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _closed() -> None:
    if SPRINT4_AI_QUERY_ROUTE_ENABLED is not False:
        _fail("ai_query_route_must_remain_disabled")
    if COPILOT_FIXTURE_EVIDENCE_IS_AUTHENTICATED_AWS is not False:
        _fail("fixture_evidence_is_not_authenticated_aws")


def _text(value: object, field_name: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"invalid_{field_name}")
    if len(value) > max_length:
        _fail(f"{field_name}_too_long")
    return value


def _optional_text(value: object, field_name: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not value.strip():
        _fail(f"invalid_{field_name}")
    return value


@dataclass(frozen=True)
class Sprint4CopilotMappedRequest:
    """Host-owned projection of a Copilot session query. Not a grant."""

    planning: MasterPlanningContext
    conversation_id: str | None
    prior_turns: tuple[LiveOpsUntrustedTurn, ...]
    selector_hints: LiveOpsSelectorHints | None
    live_ops_schema_version: str
    dashboard_schema_version: str
    planning_session_id: str
    copilot_schema_is_backend_schema: bool = False
    route_enabled: bool = False
    production_ai_active: bool = False

    def __post_init__(self) -> None:
        _closed()
        if self.copilot_schema_is_backend_schema is not False:
            _fail("copilot_schema_is_not_backend_schema")
        if self.route_enabled is not False:
            _fail("ai_query_route_must_remain_disabled")
        if self.production_ai_active is not False:
            _fail("production_ai_not_active")
        if self.live_ops_schema_version != LIVE_OPS_PUBLIC_SCHEMA_VERSION:
            _fail("invalid_live_ops_schema_version")
        if self.dashboard_schema_version != COPILOT_SESSION_QUERY_SCHEMA:
            _fail("invalid_copilot_schema_version")


@dataclass(frozen=True)
class Sprint4CopilotMappedResult:
    """Dashboard-compatible MA5 projection. Not an HTTP response."""

    schema_version: str
    master_request_id: str
    planning_session_id: str
    conversation_id: str | None
    correlation_id: str
    application_request_time_utc: str
    state: MasterResponsePolicyState
    status_text: str
    assistant_text: str
    limitation_codes: tuple[str, ...]
    claims_admitted: bool
    production_authority: str
    source_authenticity: str
    request_coverage: str
    completeness_notice: str
    route_enabled: bool
    production_ai_active: bool
    verified_zero_minted: bool
    evidence: tuple[Mapping[str, Any], ...]
    page_empty: bool
    truncated: bool
    has_more: bool
    fixture_evidence_authenticated: bool = False

    def __post_init__(self) -> None:
        _closed()
        if self.claims_admitted is not False:
            _fail("operational_claim_not_admitted")
        if self.production_authority != MasterProductionAuthority.DENIED.value:
            _fail("production_authority_denied")
        if self.source_authenticity != MasterSourceAuthenticity.NOT_ESTABLISHED.value:
            _fail("source_authenticity_not_established")
        if self.request_coverage != "NOT_ESTABLISHED":
            _fail("request_coverage_not_established")
        if self.assistant_text != self.status_text:
            _fail("assistant_text_must_equal_status_text")
        if self.route_enabled is not False:
            _fail("ai_query_route_must_remain_disabled")
        if self.production_ai_active is not False:
            _fail("production_ai_not_active")
        if self.verified_zero_minted is not False:
            _fail("empty_page_is_not_verified_zero")
        if self.fixture_evidence_authenticated is not False:
            _fail("fixture_evidence_is_not_authenticated_aws")
        if self.completeness_notice != LIVE_OPS_COMPLETENESS_NOTICE:
            _fail("invalid_completeness_notice")


def map_copilot_session_query(
    payload: Mapping[str, Any],
) -> Sprint4CopilotMappedRequest:
    """Copy a Copilot request dict onto existing Master / Live Ops types."""

    _closed()
    if not isinstance(payload, Mapping):
        _fail("invalid_copilot_request")
    schema = payload.get("schemaVersion")
    if schema != COPILOT_SESSION_QUERY_SCHEMA:
        _fail("invalid_copilot_schema_version")
    live_ops_schema = payload.get("liveOpsSchemaVersion")
    if live_ops_schema != LIVE_OPS_PUBLIC_SCHEMA_VERSION:
        _fail("invalid_live_ops_schema_version")
    user_text = _text(payload.get("userText"), "userText", max_length=LIVE_OPS_PUBLIC_TEXT_MAX_LENGTH)
    planning = MasterPlanningContext(
        master_request_id=_text(payload.get("masterRequestId"), "masterRequestId", max_length=256),
        correlation_id=_text(payload.get("correlationId"), "correlationId", max_length=256),
        application_request_time_utc=_text(
            payload.get("applicationRequestTimeUtc"),
            "applicationRequestTimeUtc",
            max_length=64,
        ),
        canonical_user_text=user_text,
    )
    prior_payload = payload.get("priorTurns") or ()
    if not isinstance(prior_payload, (list, tuple)):
        _fail("invalid_prior_turns")
    if len(prior_payload) > LIVE_OPS_PRIOR_TURN_MAX:
        _fail("prior_turns_exceeded")
    turns: list[LiveOpsUntrustedTurn] = []
    total = 0
    for item in prior_payload:
        if not isinstance(item, Mapping):
            _fail("invalid_prior_turn")
        text = _text(item.get("text"), "prior_turn_text", max_length=LIVE_OPS_PRIOR_TURN_TEXT_MAX_LENGTH)
        total += len(text)
        if total > LIVE_OPS_PRIOR_TURNS_TOTAL_MAX_LENGTH:
            _fail("prior_turns_total_exceeded")
        turns.append(LiveOpsUntrustedTurn(role=str(item.get("role")), text=text))
    hints_payload = payload.get("selectorHints")
    hints = None
    if hints_payload not in (None, {}):
        if not isinstance(hints_payload, Mapping):
            _fail("invalid_selector_hints")
        hints = LiveOpsSelectorHints(
            aircraft_id=_optional_text(hints_payload.get("aircraftId"), "aircraftId"),
            callsign=_optional_text(hints_payload.get("callsign"), "callsign"),
            hazard_id=_optional_text(hints_payload.get("hazardId"), "hazardId"),
            airport_id=_optional_text(hints_payload.get("airportId"), "airportId"),
        )
    return Sprint4CopilotMappedRequest(
        planning=planning,
        conversation_id=_optional_text(payload.get("conversationId"), "conversationId"),
        prior_turns=tuple(turns),
        selector_hints=hints,
        live_ops_schema_version=LIVE_OPS_PUBLIC_SCHEMA_VERSION,
        dashboard_schema_version=COPILOT_SESSION_QUERY_SCHEMA,
        planning_session_id=_text(
            payload.get("planningSessionId"),
            "planningSessionId",
            max_length=256,
        ),
    )


def live_ops_catalog_tool_name(display_name: str) -> str | None:
    """Map a dashboard display tool name onto the Live Ops catalog, if any."""

    if display_name in LIVE_OPS_CATALOG_TOOL_NAMES:
        return display_name
    return COPILOT_DISPLAY_TOOL_NAME_ALIASES.get(display_name)


def project_ma5_to_copilot(
    *,
    mapped_request: Sprint4CopilotMappedRequest,
    render: MasterResponseRender,
    evidence: tuple[Mapping[str, Any], ...] = (),
    truncated: bool = False,
    has_more: bool = False,
) -> Sprint4CopilotMappedResult:
    """Project an existing MA5 render onto the Copilot result shape."""

    _closed()
    if not isinstance(render, MasterResponseRender):
        _fail("invalid_master_response_render")
    if render.claims_admitted is not False:
        _fail("operational_claim_not_admitted")
    page_empty = len(evidence) == 0
    return Sprint4CopilotMappedResult(
        schema_version=COPILOT_SESSION_QUERY_SCHEMA,
        master_request_id=mapped_request.planning.master_request_id,
        planning_session_id=mapped_request.planning_session_id,
        conversation_id=mapped_request.conversation_id,
        correlation_id=mapped_request.planning.correlation_id,
        application_request_time_utc=mapped_request.planning.application_request_time_utc,
        state=render.state,
        status_text=render.status_text,
        assistant_text=render.status_text,
        limitation_codes=render.limitation_codes,
        claims_admitted=False,
        production_authority=MasterProductionAuthority.DENIED.value,
        source_authenticity=MasterSourceAuthenticity.NOT_ESTABLISHED.value,
        request_coverage="NOT_ESTABLISHED",
        completeness_notice=LIVE_OPS_COMPLETENESS_NOTICE,
        route_enabled=False,
        production_ai_active=False,
        verified_zero_minted=False,
        evidence=evidence,
        page_empty=page_empty,
        truncated=truncated,
        has_more=has_more,
    )


def copilot_contract_inventory() -> dict[str, object]:
    """Document the dashboard-to-backend mapping. Not a live route."""

    _closed()
    return {
        "schema_version": SPRINT4_COPILOT_CONTRACT_MAP_SCHEMA,
        "dashboard_schema": COPILOT_SESSION_QUERY_SCHEMA,
        "backend_schemas": (
            "wilvor.ai.live_ops_public.v1",
            "wilvor.ai.master_parallel_contracts.v1",
        ),
        "route_key": AI_QUERY_ROUTE_KEY,
        "route_enabled": False,
        "field_map": COPILOT_TO_MASTER_FIELD_MAP,
        "drift_notes": COPILOT_DRIFT_NOTES,
        "required_ma5_limitations": tuple(item.value for item in REQUIRED_MA5_LIMITATIONS),
        "live_ops_catalog_tool_names": tuple(sorted(LIVE_OPS_CATALOG_TOOL_NAMES)),
        "display_tool_aliases": dict(COPILOT_DISPLAY_TOOL_NAME_ALIASES),
        "fixture_evidence_authenticated": False,
    }


__all__ = [
    "AI_QUERY_ROUTE_KEY",
    "COPILOT_DISPLAY_TOOL_NAME_ALIASES",
    "COPILOT_DRIFT_NOTES",
    "COPILOT_FIXTURE_EVIDENCE_IS_AUTHENTICATED_AWS",
    "COPILOT_SESSION_QUERY_SCHEMA",
    "COPILOT_TO_MASTER_FIELD_MAP",
    "LIVE_OPS_CATALOG_TOOL_NAMES",
    "REQUIRED_MA5_LIMITATIONS",
    "SPRINT4_AI_QUERY_ROUTE_ENABLED",
    "SPRINT4_COPILOT_CONTRACT_MAP_SCHEMA",
    "Sprint4CopilotMappedRequest",
    "Sprint4CopilotMappedResult",
    "copilot_contract_inventory",
    "live_ops_catalog_tool_name",
    "map_copilot_session_query",
    "project_ma5_to_copilot",
]
