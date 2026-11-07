"""Sprint 4 Live Ops authorization stays allowlisted, gated, and offline."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_read_resource_guard import ReadOperation, server_read_resources
from wilvor_ai.sprint3c_scoped_read import (
    GRANT_CONSUMPTION_LIFECYCLE,
    LEDGER_EXECUTION_STATE_STARTED,
    describe_consumed_attempt_read_binding,
)
from wilvor_ai.sprint4_live_ops_authorization import (
    APPROVED_SOURCE_ADAPTER,
    EMPTY_PAGE_IS_VERIFIED_ZERO,
    LIVE_AWS_OPERATION_PERMITTED,
    LIVE_OPS_QUERY_PAGE_LIMIT,
    PRODUCTION_LIVE_OPS_AWS_READ_ENABLED,
    REGION_LABEL_IS_SIGMET_POLYGON,
    UNBOUNDED_NETWORK_INVENTORY_PERMITTED,
    authorize_live_ops_tool,
    issue_production_live_ops_aws_read,
    open_production_live_ops_aws_read,
)


ACCEPTED = "2026-10-27T16:00:00Z"
DEADLINE = "2026-10-27T20:00:00Z"
REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE = (
    REPO_ROOT
    / "functions"
    / "shared"
    / "wilvor_ai"
    / "sprint4_live_ops_authorization.py"
)


def _binding(**overrides: object):
    payload: dict[str, object] = {
        "owner_id": "owner-1",
        "attempt_token": "attempt-1",
        "fence_epoch": 1,
        "grant_nonce": "grant-nonce-1",
        "idempotency_key": "idem-live-ops",
        "payload_fingerprint": "fingerprint-1",
        "ledger_state": LEDGER_EXECUTION_STATE_STARTED,
        "grant_consumption_lifecycle": GRANT_CONSUMPTION_LIFECYCLE,
        "master_request_id": "req-live-ops",
        "planning_session_id": "plan-live-ops",
        "step_id": "step-live-ops",
        "invocation_id": "inv-live-ops",
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "capability": AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT,
        "principal_ref": "principal-1",
        "policy_revision": "policy-1",
        "tool_name": "get_aircraft_operational_context",
        "resource_id": "aircraft",
        "operation": ReadOperation.GET_ITEM,
        "region_scope": ("CA",),
        "accepted_at_utc": ACCEPTED,
        "deadline_utc": DEADLINE,
        "correlation_id": "corr-live-ops",
    }
    payload.update(overrides)
    return describe_consumed_attempt_read_binding(**payload)


def test_closed_flags_and_production_entrypoints():
    assert PRODUCTION_LIVE_OPS_AWS_READ_ENABLED is False
    assert LIVE_AWS_OPERATION_PERMITTED is False
    assert APPROVED_SOURCE_ADAPTER is False
    assert EMPTY_PAGE_IS_VERIFIED_ZERO is False
    assert REGION_LABEL_IS_SIGMET_POLYGON is False
    assert UNBOUNDED_NETWORK_INVENTORY_PERMITTED is False
    with pytest.raises(ContractValidationError, match="production_live_ops_aws_read_not_enabled"):
        issue_production_live_ops_aws_read()
    with pytest.raises(ContractValidationError, match="production_live_ops_aws_read_not_enabled"):
        open_production_live_ops_aws_read()


def test_authorize_live_ops_tool_admits_allowlisted_aircraft_get():
    auth = authorize_live_ops_tool(
        _binding(),
        tool_name="get_aircraft_operational_context",
        arguments={"aircraft_id": "ac1"},
        requested_resource_id="aircraft",
        requested_operation=ReadOperation.GET_ITEM,
    )

    assert auth.allows("aircraft", ReadOperation.GET_ITEM)
    assert not auth.allows("encounters", ReadOperation.QUERY)
    assert auth.query_limit == LIVE_OPS_QUERY_PAGE_LIMIT
    assert auth.live_aws_permitted is False
    assert auth.production_grant is False
    with pytest.raises(ContractValidationError, match="live_ops_authorization_is_not_a_production_grant"):
        bool(auth)


def test_scan_sql_arn_and_foreign_arguments_are_refused():
    binding = _binding()
    with pytest.raises(ContractValidationError, match="scan_not_permitted"):
        authorize_live_ops_tool(
            binding,
            tool_name="get_aircraft_operational_context",
            requested_operation=ReadOperation.SCAN,
        )
    with pytest.raises(ContractValidationError, match="arbitrary_sql"):
        authorize_live_ops_tool(
            binding,
            tool_name="get_aircraft_operational_context",
            arguments={"aircraft_id": "ac1", "sql": "SELECT *"},
        )
    with pytest.raises(ContractValidationError, match="arbitrary_table_arn"):
        authorize_live_ops_tool(
            binding,
            tool_name="get_aircraft_operational_context",
            requested_resource_id="arn:aws:dynamodb:us-east-1:1:table/aircraft",
            requested_operation=ReadOperation.GET_ITEM,
        )
    with pytest.raises(ContractValidationError, match="argument_not_allowlisted"):
        authorize_live_ops_tool(
            binding,
            tool_name="get_aircraft_operational_context",
            arguments={"aircraft_id": "ac1", "limit": 99},
        )


def test_selector_required_for_hazard_and_encounter_search():
    hazards = _binding(
        tool_name="search_current_hazards",
        resource_id="hazards",
        operation=ReadOperation.QUERY,
    )
    with pytest.raises(ContractValidationError, match="selector_required"):
        authorize_live_ops_tool(hazards, tool_name="search_current_hazards", arguments={})
    authorize_live_ops_tool(
        hazards,
        tool_name="search_current_hazards",
        arguments={"region": "California"},
    )
    encounters = _binding(
        tool_name="search_current_encounters",
        resource_id="encounters",
        operation=ReadOperation.QUERY,
    )
    with pytest.raises(ContractValidationError, match="selector_required"):
        authorize_live_ops_tool(encounters, tool_name="search_current_encounters")


def test_tool_identity_must_match_binding_and_live_ops_catalog():
    binding = _binding()
    with pytest.raises(ContractValidationError, match="identity_mismatch"):
        authorize_live_ops_tool(binding, tool_name="find_aircraft_by_callsign")
    historical = _binding(specialist=MasterSpecialist.HISTORICAL_ANALYTICS)
    with pytest.raises(ContractValidationError, match="invalid_specialist"):
        authorize_live_ops_tool(historical, tool_name="get_aircraft_operational_context")


def test_live_ops_allowlist_has_no_scan_resources():
    for tool_name, pairs in (
        ("search_current_hazards", server_read_resources(MasterSpecialist.LIVE_OPS, "search_current_hazards")),
        ("get_aircraft_operational_context", server_read_resources(MasterSpecialist.LIVE_OPS, "get_aircraft_operational_context")),
    ):
        assert pairs
        assert all(operation is not ReadOperation.SCAN for _resource, operation in pairs)
        assert tool_name


def test_authorization_module_has_no_boto3_or_live_sdk():
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert "boto3" not in imported
    assert "botocore" not in imported
    assert "shapely" not in imported
    assert "os.environ" not in source
    assert "boto3.client" not in source
    assert ".scan(" not in source
