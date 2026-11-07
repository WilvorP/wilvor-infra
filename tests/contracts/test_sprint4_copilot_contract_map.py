"""Sprint 4 Copilot session-query mapping onto existing Python contracts."""

from __future__ import annotations

import pytest

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.live_ops_public_contracts import LIVE_OPS_PUBLIC_SCHEMA_VERSION
from wilvor_ai.master_parallel_contracts import MasterResponseLimitation
from wilvor_ai.master_response_policy import MasterResponsePolicyState
from wilvor_ai.master_response_renderer import MasterResponseRender
from wilvor_ai.sprint4_copilot_contract_map import (
    COPILOT_DISPLAY_TOOL_NAME_ALIASES,
    COPILOT_FIXTURE_EVIDENCE_IS_AUTHENTICATED_AWS,
    COPILOT_SESSION_QUERY_SCHEMA,
    LIVE_OPS_CATALOG_TOOL_NAMES,
    SPRINT4_AI_QUERY_ROUTE_ENABLED,
    copilot_contract_inventory,
    live_ops_catalog_tool_name,
    map_copilot_session_query,
    project_ma5_to_copilot,
)


def _payload(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schemaVersion": COPILOT_SESSION_QUERY_SCHEMA,
        "liveOpsSchemaVersion": LIVE_OPS_PUBLIC_SCHEMA_VERSION,
        "masterRequestId": "mreq-sprint4map0001",
        "planningSessionId": "plan-sprint4map0001",
        "conversationId": "conv-sprint4map0001",
        "correlationId": "corr-sprint4map0001",
        "applicationRequestTimeUtc": "2026-11-07T18:00:00Z",
        "userText": "What is the current state of UAL9?",
        "priorTurns": [],
        "selectorHints": None,
    }
    values.update(overrides)
    return values


def test_closed_route_and_fixture_flags():
    assert SPRINT4_AI_QUERY_ROUTE_ENABLED is False
    assert COPILOT_FIXTURE_EVIDENCE_IS_AUTHENTICATED_AWS is False
    inventory = copilot_contract_inventory()
    assert inventory["route_enabled"] is False
    assert inventory["fixture_evidence_authenticated"] is False
    assert COPILOT_SESSION_QUERY_SCHEMA not in inventory["backend_schemas"]
    assert "get_aircraft_operational_context" in inventory["live_ops_catalog_tool_names"]


def test_maps_camelcase_onto_master_planning_context():
    mapped = map_copilot_session_query(
        _payload(
            selectorHints={"aircraftId": "aa0001", "callsign": "UAL9"},
            priorTurns=[{"role": "user", "text": "Earlier question."}],
        )
    )
    assert mapped.copilot_schema_is_backend_schema is False
    assert mapped.route_enabled is False
    assert mapped.planning.master_request_id == "mreq-sprint4map0001"
    assert mapped.planning.correlation_id == "corr-sprint4map0001"
    assert mapped.planning.canonical_user_text == "What is the current state of UAL9?"
    assert mapped.planning_session_id == "plan-sprint4map0001"
    assert mapped.conversation_id == "conv-sprint4map0001"
    assert mapped.selector_hints is not None
    assert mapped.selector_hints.aircraft_id == "aa0001"
    assert mapped.selector_hints.callsign == "UAL9"
    assert mapped.selector_hints.region is None
    assert mapped.prior_turns[0].role == "user"


def test_display_tool_aliases_are_not_live_ops_catalog_names():
    assert "get_aircraft_current_state" not in LIVE_OPS_CATALOG_TOOL_NAMES
    assert "get_hazard" not in LIVE_OPS_CATALOG_TOOL_NAMES
    assert "get_encounter" not in LIVE_OPS_CATALOG_TOOL_NAMES
    assert live_ops_catalog_tool_name("get_aircraft_current_state") == (
        COPILOT_DISPLAY_TOOL_NAME_ALIASES["get_aircraft_current_state"]
    )
    assert live_ops_catalog_tool_name("get_aircraft_operational_context") == (
        "get_aircraft_operational_context"
    )


def test_ma5_projection_keeps_closed_sentences_and_denials():
    mapped = map_copilot_session_query(_payload())
    render = MasterResponseRender(
        state=MasterResponsePolicyState.INSUFFICIENT_EVIDENCE,
        status_text="Supplied evidence is not sufficient for an operational claim.",
        limitation_codes=(
            MasterResponseLimitation.SOURCE_AUTHENTICITY_NOT_ESTABLISHED.value,
            MasterResponseLimitation.PRODUCTION_AUTHORITY_DENIED.value,
            MasterResponseLimitation.REQUEST_COVERAGE_NOT_ESTABLISHED.value,
            MasterResponseLimitation.NO_OPERATIONAL_CLAIM.value,
            MasterResponseLimitation.EXECUTION_DENIED.value,
        ),
    )
    result = project_ma5_to_copilot(mapped_request=mapped, render=render)
    assert result.assistant_text == result.status_text
    assert result.claims_admitted is False
    assert result.source_authenticity == "NOT_ESTABLISHED"
    assert result.production_authority == "DENIED"
    assert result.route_enabled is False
    assert result.production_ai_active is False
    assert result.verified_zero_minted is False
    assert result.page_empty is True
    assert result.fixture_evidence_authenticated is False


def test_rejects_unknown_copilot_schema_as_backend_contract():
    with pytest.raises(ContractValidationError, match="invalid_copilot_schema_version"):
        map_copilot_session_query(_payload(schemaVersion="wilvor.ai.master_parallel_contracts.v1"))
