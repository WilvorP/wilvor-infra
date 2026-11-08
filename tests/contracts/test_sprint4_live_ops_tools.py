"""Sprint 4 authorized Live Ops tools: DynamoDB-shaped reads without live AWS."""

from __future__ import annotations

import pytest

from tests.fixtures import live_ops_domain_fixtures as fixtures
from wilvor_ai.contracts import (
    AgentCapability,
    ContractValidationError,
    FreshnessStatus,
    ToolResultStatus,
)
from wilvor_ai.live_ops import LiveOpsCall
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.sprint3c_scoped_read import (
    GRANT_CONSUMPTION_LIFECYCLE,
    LEDGER_EXECUTION_STATE_STARTED,
    describe_consumed_attempt_read_binding,
)
from wilvor_ai.sprint4_live_ops_authorization import authorize_live_ops_tool
from wilvor_ai.sprint4_live_ops_source import (
    EMPTY_PAGE_NOT_VERIFIED_ZERO,
    GEOGRAPHIC_PROVENANCE_ABSENT,
    LiveOpsGeographicCoverage,
    RELATED_RESOURCE_NOT_ALLOWLISTED,
)
from wilvor_ai.sprint4_live_ops_tools import (
    execute_authorized_live_ops_tool,
    run_authorized_live_ops_tool,
    wrap_authorized_tables,
)


def _call(tables, tool_call_id=fixtures.TOOL_CALL_ID):
    return LiveOpsCall(
        tables=tables,
        now_epoch=fixtures.NOW,
        tool_call_id=tool_call_id,
        correlation_id=fixtures.CORRELATION_ID,
    )


def _binding(**overrides: object):
    payload: dict[str, object] = {
        "owner_id": "owner-1",
        "attempt_token": "attempt-1",
        "fence_epoch": 1,
        "grant_nonce": "grant-nonce-1",
        "idempotency_key": "idem-tools",
        "payload_fingerprint": "fingerprint-tools",
        "ledger_state": LEDGER_EXECUTION_STATE_STARTED,
        "grant_consumption_lifecycle": GRANT_CONSUMPTION_LIFECYCLE,
        "master_request_id": "req-tools",
        "planning_session_id": "plan-tools",
        "step_id": "step-tools",
        "invocation_id": "inv-tools",
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "capability": AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT,
        "principal_ref": "principal-1",
        "policy_revision": "policy-1",
        "tool_name": "get_aircraft_operational_context",
        "resource_id": "aircraft",
        "operation": ReadOperation.GET_ITEM,
        "region_scope": ("CA",),
        "accepted_at_utc": "2026-10-27T16:00:00Z",
        "deadline_utc": "2026-10-27T20:00:00Z",
        "correlation_id": "corr-tools",
    }
    payload.update(overrides)
    return describe_consumed_attempt_read_binding(**payload)


def _auth(tool_name: str, resource_id: str, operation: ReadOperation, **arguments: object):
    binding = _binding(tool_name=tool_name, resource_id=resource_id, operation=operation)
    return authorize_live_ops_tool(binding, tool_name=tool_name, arguments=dict(arguments))


def _closed(outcome) -> None:
    assert outcome.live_aws_initiated is False
    assert outcome.verified_zero is False
    assert outcome.source_report.verified_zero is False
    assert outcome.source_report.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert outcome.source_report.region_label_is_polygon is False


def test_wrapped_tables_reject_scan():
    auth = _auth("get_aircraft_operational_context", "aircraft", ReadOperation.GET_ITEM, aircraft_id="a")
    wrapped = wrap_authorized_tables(fixtures.california_tables(), auth)
    with pytest.raises(ContractValidationError, match="scan_not_permitted"):
        wrapped.aircraft.scan()
    with pytest.raises(ContractValidationError, match="resource_not_allowlisted"):
        wrapped.encounters.query(KeyConditionExpression="aircraft_id = a")


def test_aircraft_context_reads_allowlisted_getitem_only():
    tables = fixtures.california_tables()
    auth = _auth(
        "get_aircraft_operational_context",
        "aircraft",
        ReadOperation.GET_ITEM,
        aircraft_id="a",
    )
    outcome = execute_authorized_live_ops_tool(
        auth,
        tables=tables,
        call=_call(tables),
        arguments={"aircraft_id": "a"},
    )
    _closed(outcome)
    assert outcome.tool_result.data["found"] is True
    assert outcome.tool_result.data["aircraft_id"] == "a"
    assert outcome.tool_result.data["encounters"] == []
    assert RELATED_RESOURCE_NOT_ALLOWLISTED in outcome.source_report.limitations
    assert outcome.tool_result.evidence
    assert all(item.temporal_scope.value == "CURRENT" for item in outcome.tool_result.evidence)


def test_missing_aircraft_is_not_found_and_not_verified_zero():
    tables = fixtures.tables()
    auth = _auth(
        "get_aircraft_operational_context",
        "aircraft",
        ReadOperation.GET_ITEM,
        aircraft_id="missing",
    )
    outcome = execute_authorized_live_ops_tool(
        auth,
        tables=tables,
        call=_call(tables),
        arguments={"aircraft_id": "missing"},
    )
    _closed(outcome)
    assert outcome.tool_result.status is ToolResultStatus.NOT_FOUND
    assert outcome.tool_result.data["found"] is False
    assert outcome.source_report.verified_zero is False


def test_region_hazard_search_uses_computed_geometry_not_california_text():
    tables = fixtures.california_tables()
    auth = _auth(
        "search_current_hazards",
        "hazards",
        ReadOperation.QUERY,
        region="California",
        product_type="SIGMET",
    )
    outcome = execute_authorized_live_ops_tool(
        auth,
        tables=tables,
        call=_call(tables),
        arguments={"region": "California", "product_type": "SIGMET"},
    )
    _closed(outcome)
    assert outcome.source_report.geographic_coverage is LiveOpsGeographicCoverage.INTERSECTION
    assert "H1" in outcome.source_report.intersecting_hazard_ids
    hazards = outcome.tool_result.data["hazards"]
    assert any(row["spatial_status"] == "INTERSECTS" for row in hazards)
    assert outcome.tool_result.data["region"]["resolved"] is True
    assert not any(row["spatial_status"] == "INTERSECTS" and row["hazard"]["hazard_id"] == "outside" for row in hazards)


def test_impact_region_path_does_not_invent_sigmet_intersection():
    tables = fixtures.california_tables()
    auth = _auth(
        "search_current_impacts",
        "hazards",
        ReadOperation.QUERY,
        region="California",
        hazard_ids=["H1"],
    )
    outcome = execute_authorized_live_ops_tool(
        auth,
        tables=tables,
        call=_call(tables),
        arguments={"region": "California", "hazard_ids": ["H1"]},
    )
    _closed(outcome)
    assert outcome.source_report.geographic_coverage is LiveOpsGeographicCoverage.NOT_ESTABLISHED
    assert GEOGRAPHIC_PROVENANCE_ABSENT in outcome.source_report.limitations
    assert outcome.source_report.intersecting_hazard_ids == ()
    assert outcome.tool_result.data["impact_count"] == 0


def test_persisted_encounters_are_observed_without_unallowlisted_parents():
    tables = fixtures.california_tables()
    auth = _auth(
        "search_current_encounters",
        "encounters",
        ReadOperation.QUERY,
        hazard_id="H1",
    )
    outcome = execute_authorized_live_ops_tool(
        auth,
        tables=tables,
        call=_call(tables),
        arguments={"hazard_id": "H1"},
    )
    _closed(outcome)
    assert RELATED_RESOURCE_NOT_ALLOWLISTED in outcome.source_report.limitations
    assert outcome.tool_result.data["encounter_count"] == 0
    assert outcome.tool_result.data["operationally_unevaluated"]


def test_callsign_query_empty_page_is_not_verified_zero():
    tables = fixtures.tables()
    binding = _binding(
        tool_name="find_aircraft_by_callsign",
        resource_id="aircraft",
        operation=ReadOperation.QUERY,
    )
    outcome = run_authorized_live_ops_tool(
        binding,
        tables=tables,
        call=_call(tables),
        tool_name="find_aircraft_by_callsign",
        arguments={"callsign": "NOCALL"},
    )
    _closed(outcome)
    assert outcome.tool_result.data["match_count"] == 0
    assert outcome.source_report.verified_zero is False
    assert EMPTY_PAGE_NOT_VERIFIED_ZERO in outcome.source_report.limitations or (
        outcome.tool_result.data["match_count"] == 0
    )


def test_observed_network_state_is_refused_as_unbounded():
    tables = fixtures.tables()
    binding = _binding(
        tool_name="get_observed_network_state",
        resource_id="aircraft",
        operation=ReadOperation.QUERY,
    )
    outcome = run_authorized_live_ops_tool(
        binding,
        tables=tables,
        call=_call(tables),
        tool_name="get_observed_network_state",
        arguments={},
    )
    _closed(outcome)
    assert outcome.refusal_code == "unbounded_population_refused"
    assert outcome.tool_result.status is ToolResultStatus.UNAVAILABLE


def test_hazard_and_airport_context_stay_inside_allowlist():
    tables = fixtures.california_tables()
    hazard_auth = _auth(
        "get_hazard_operational_context",
        "hazards",
        ReadOperation.GET_ITEM,
        hazard_id="H1",
    )
    hazard_outcome = execute_authorized_live_ops_tool(
        hazard_auth,
        tables=tables,
        call=_call(tables, "tool-hazard"),
        arguments={"hazard_id": "H1"},
    )
    airport_tables = fixtures.airport_tables()
    airport_auth = _auth(
        "get_airport_operational_context",
        "airports",
        ReadOperation.GET_ITEM,
        airport_id="KSEA",
    )
    airport_outcome = execute_authorized_live_ops_tool(
        airport_auth,
        tables=airport_tables,
        call=_call(airport_tables, "tool-airport"),
        arguments={"airport_id": "KSEA"},
    )
    _closed(hazard_outcome)
    _closed(airport_outcome)
    assert hazard_outcome.tool_result.data["found"] is True
    assert hazard_outcome.tool_result.data["impacts"] == []
    assert airport_outcome.tool_result.data["found"] is True
    assert airport_outcome.tool_result.data["latest_taf_periods"] == []
    assert RELATED_RESOURCE_NOT_ALLOWLISTED in airport_outcome.source_report.limitations


def test_selector_refusal_does_not_query_aws_shaped_tables():
    tables = fixtures.california_tables()
    binding = _binding(
        tool_name="search_current_hazards",
        resource_id="hazards",
        operation=ReadOperation.QUERY,
    )
    outcome = run_authorized_live_ops_tool(
        binding,
        tables=tables,
        call=_call(tables),
        tool_name="search_current_hazards",
        arguments={},
    )
    assert outcome.refusal_code == "selector_required"
    assert outcome.tool_result.status is ToolResultStatus.UNAVAILABLE
    assert tables.hazards.calls == []


def test_freshness_overlay_does_not_claim_production_authenticity():
    tables = fixtures.california_tables()
    auth = _auth(
        "get_aircraft_operational_context",
        "aircraft",
        ReadOperation.GET_ITEM,
        aircraft_id="a",
    )
    outcome = execute_authorized_live_ops_tool(
        auth,
        tables=tables,
        call=_call(tables),
        arguments={"aircraft_id": "a"},
    )
    _closed(outcome)
    for item in outcome.tool_result.evidence:
        assert item.freshness_status in {
            FreshnessStatus.FRESH,
            FreshnessStatus.ACCEPTABLE,
            FreshnessStatus.STALE,
            FreshnessStatus.UNKNOWN,
        }
