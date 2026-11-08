"""Sprint 4 authorization design and IAM proposal stay closed and unapplied."""

from __future__ import annotations

from pathlib import Path

import pytest

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_execution_grants import (
    DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE,
    HMAC_PREVENTS_REPLAY,
    PRODUCTION_GRANT_ISSUANCE_AVAILABLE,
)
from wilvor_ai.master_execution_ledger import DYNAMODB_LEDGER_TABLE_NAME
from wilvor_ai.master_gateway_runtime_boundary import ISSUANCE_CONTROL_IDS
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.sprint3d_aws_execution import (
    expected_iam_decisions,
    iam_role_arn,
    reviewed_aws_deployment,
    reviewed_read_table,
    table_arn,
)
from wilvor_ai.sprint4_security_authorization import (
    AIRCRAFT_TABLE_NAME,
    AI_HOST_ROLE_NAME,
    AUTHORIZATION_STAGES,
    CLOSED_RUNTIME_DENY,
    CURRENT_LEDGER_ALLOW,
    CURRENT_LEDGER_DENY,
    CURRENT_SPECIALIST_GETITEM_DENY,
    ENCOUNTER_TABLE_NAME,
    HISTORICAL_TABLE_NAME,
    REVIEWED_DEV_ACCOUNT_ID,
    REVIEWED_DEV_REGION,
    SHARED_INTERFACE_REQUIREMENTS,
    SPRINT4_IAM_CHANGE_APPLIED,
    SPRINT4_PRODUCTION_AUTHORIZATION_ENABLED,
    apply_iam_delta,
    closed_authorization_inventory,
    enable_production_authorization,
    production_authorization_design,
    refuse_closed_production_entrypoints,
    reviewed_iam_change_proposal,
    specialist_iam_matrix_from_deployment,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_production_authorization_design_is_closed_end_to_end():
    design = production_authorization_design()
    assert bool(design) is False
    assert design.production_enabled is False
    assert design.route_enablement_permitted is False
    assert design.ledger_writes_enabled is False
    assert design.hmac_prevents_replay is False
    assert design.grant_nonce_rotates_on_takeover is False
    assert design.durable_adapter_is_provenance is False
    assert design.atomic_admission_is_native_execution is False
    assert design.distributed_exactly_once is False
    assert tuple(stage.stage for stage in design.stages) == AUTHORIZATION_STAGES
    assert all(stage.satisfied is False for stage in design.stages)
    assert all(bool(stage) is False for stage in design.stages)
    assert design.issuance_controls == ISSUANCE_CONTROL_IDS
    assert "live_network_gate_closed" in design.live_aws_blockers
    assert design.isolation.ledger_table == DYNAMODB_LEDGER_TABLE_NAME
    assert design.isolation.reviewed_getitem_tables == (AIRCRAFT_TABLE_NAME,)
    assert ENCOUNTER_TABLE_NAME in design.isolation.denied_tables
    assert HISTORICAL_TABLE_NAME in design.isolation.denied_tables
    assert design.isolation.ai_host_function != design.isolation.operational_api_function
    assert design.isolation.ai_query_route_enabled is False
    assert design.isolation.wildcard_table_arn is False


def test_iam_proposal_matches_current_host_policy_and_is_not_applied():
    proposal = reviewed_iam_change_proposal()
    assert bool(proposal) is False
    assert proposal.status == "PROPOSED_NOT_APPLIED"
    assert proposal.account_id == REVIEWED_DEV_ACCOUNT_ID
    assert proposal.region == REVIEWED_DEV_REGION
    assert proposal.role_name == AI_HOST_ROLE_NAME
    assert proposal.current_ledger_allow == CURRENT_LEDGER_ALLOW
    assert proposal.current_ledger_deny == CURRENT_LEDGER_DENY
    assert "dynamodb:TransactWriteItems" in proposal.current_ledger_allow
    assert "dynamodb:Scan" in proposal.current_ledger_deny
    assert "dynamodb:Query" in proposal.current_ledger_deny
    assert proposal.current_specialist_getitem_allow == ("dynamodb:GetItem",)
    assert proposal.terraform_apply_permitted is False
    assert proposal.iam_change_applied is False
    assert SPRINT4_IAM_CHANGE_APPLIED is False
    assert SPRINT4_PRODUCTION_AUTHORIZATION_ENABLED is False
    assert any("Historical" in delta for delta in proposal.remaining_deltas)
    assert "dynamodb:Scan" in proposal.must_not_grant
    assert "bedrock:*" in proposal.must_not_grant
    assert "iam:SimulatePrincipalPolicy" in proposal.must_not_grant
    with pytest.raises(ContractValidationError, match="sprint4_iam_not_applied"):
        apply_iam_delta(proposal)
    with pytest.raises(ContractValidationError, match="production_authorization_closed"):
        enable_production_authorization()


def test_closed_inventory_and_production_entrypoints_stay_closed():
    inventory = closed_authorization_inventory()
    assert inventory["PRODUCTION_GRANT_ISSUANCE_AVAILABLE"] is False
    assert inventory["DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE"] is False
    assert inventory["HMAC_PREVENTS_REPLAY"] is False
    assert inventory["SPRINT4_LEDGER_WRITES_ENABLED"] is False
    assert inventory["route_enablement_permitted"] is False
    assert inventory["issuance_controls_satisfied"] is False
    refuse_closed_production_entrypoints()
    assert HMAC_PREVENTS_REPLAY is False
    assert PRODUCTION_GRANT_ISSUANCE_AVAILABLE is False
    assert DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE is False


def test_specialist_iam_matrix_allows_ledger_consume_and_denies_specialist_writes():
    aircraft = reviewed_read_table(
        logical_resource_id="aircraft",
        physical_table_name="wilvor-reviewed-aircraft-current-state",
        source_name="aircraft-current-state",
        source_version_attribute="source_version",
        observation_time_attribute="source_observed_at_utc",
        hash_attribute="aircraft_id",
        allowed_operations=frozenset({ReadOperation.GET_ITEM}),
        query_partition_attribute="callsign",
    )
    deployment = reviewed_aws_deployment(
        account_id=REVIEWED_DEV_ACCOUNT_ID,
        region=REVIEWED_DEV_REGION,
        role_arn=iam_role_arn(REVIEWED_DEV_ACCOUNT_ID, "wilvor-reviewed-execution"),
        read_tables=(aircraft,),
    )
    matrix = specialist_iam_matrix_from_deployment(deployment)
    assert matrix == expected_iam_decisions(deployment)
    ledger = table_arn(REVIEWED_DEV_ACCOUNT_ID, REVIEWED_DEV_REGION, DYNAMODB_LEDGER_TABLE_NAME)
    aircraft_arn = table_arn(
        REVIEWED_DEV_ACCOUNT_ID,
        REVIEWED_DEV_REGION,
        "wilvor-reviewed-aircraft-current-state",
    )
    assert ("dynamodb:TransactWriteItems", ledger, "allowed") in matrix
    assert ("dynamodb:GetItem", ledger, "allowed") in matrix
    assert ("dynamodb:Query", ledger, "deny") in matrix
    assert ("dynamodb:Scan", ledger, "deny") in matrix
    assert ("dynamodb:GetItem", aircraft_arn, "allowed") in matrix
    assert ("dynamodb:Query", aircraft_arn, "deny") in matrix
    assert ("dynamodb:PutItem", aircraft_arn, "deny") in matrix
    assert ("dynamodb:TransactWriteItems", aircraft_arn, "deny") in matrix


def test_shared_interface_requirements_are_recorded_not_implemented():
    assert SHARED_INTERFACE_REQUIREMENTS
    assert any("required_atomic_single_use_interface" in item for item in SHARED_INTERFACE_REQUIREMENTS)
    assert any("MasterSourceAuthenticity" in item for item in SHARED_INTERFACE_REQUIREMENTS)
    assert any("POST /ai/query" in item for item in SHARED_INTERFACE_REQUIREMENTS)
    assert any("envs/dev" in item for item in SHARED_INTERFACE_REQUIREMENTS)


def test_ai_host_terraform_still_matches_the_current_ledger_allow_set():
    iam = (REPO_ROOT / "modules" / "ai_host" / "iam.tf").read_text(encoding="utf-8")
    locals_text = (REPO_ROOT / "modules" / "ai_host" / "locals.tf").read_text(encoding="utf-8")
    lambda_text = (REPO_ROOT / "modules" / "ai_host" / "lambda.tf").read_text(encoding="utf-8")
    for action in CURRENT_LEDGER_ALLOW:
        assert action in iam
    for action in CURRENT_LEDGER_DENY:
        assert action in locals_text
    for action in CURRENT_SPECIALIST_GETITEM_DENY:
        assert action in locals_text
    for action in CLOSED_RUNTIME_DENY:
        assert action.split(":")[0] in iam or action in iam
    assert "LEDGER_WRITES_ENABLED         = \"false\"" in lambda_text
    assert "AI_QUERY_ROUTE_ENABLED        = \"false\"" in lambda_text
    assert "iam:SimulatePrincipalPolicy" not in iam
    assert "dynamodb:DescribeTable" not in iam
    handler = (REPO_ROOT / "modules" / "ai_host" / "src" / "handler.py").read_text(encoding="utf-8")
    assert "boto3" not in handler
    assert "ledger_writes_enabled" in handler
