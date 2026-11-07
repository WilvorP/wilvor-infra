"""Sprint 5 IAM, ledger, live-read, model, Copilot, and verification gates."""

from __future__ import annotations

import pytest

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.sprint4_copilot_contract_map import SPRINT4_AI_QUERY_ROUTE_ENABLED
from wilvor_ai.sprint5_copilot_host import (
    SPRINT5_AI_QUERY_ROUTE_ENABLED,
    Sprint5VerifiedPrincipal,
    activate_public_ai_query_route,
    admit_copilot_query,
    authenticate_copilot_request,
)
from wilvor_ai.sprint5_ledger_plan import execute_sprint5_ledger_plan, sprint5_ledger_test_plan
from wilvor_ai.sprint5_live_read_plan import execute_sprint5_live_reads, sprint5_live_read_plan
from wilvor_ai.sprint5_model_plan import execute_sprint5_model_plan, sprint5_model_test_plan
from wilvor_ai.sprint5_security_iam import apply_sprint5_iam_delta, reviewed_sprint5_iam_delta
from wilvor_ai.sprint5_verification import Sprint5VerificationStatus, classify_sprint5_operations


class AcceptingVerifier:
    def verify(self, token: str) -> Sprint5VerifiedPrincipal:
        assert token == "reviewed-token"
        return Sprint5VerifiedPrincipal(
            subject="user-1",
            issuer="https://issuer.example",
            audience="wilvor-copilot",
        )


def test_iam_delta_is_proposed_not_applied_and_has_no_wildcards():
    proposal = reviewed_sprint5_iam_delta()
    assert proposal.status == "PROPOSED_NOT_APPLIED"
    assert proposal.iam_change_applied is False
    assert proposal.terraform_apply_permitted is False
    assert proposal.bedrock_included is False
    assert proposal.historical_dynamodb_query_substituted is False
    resources = [resource for statement in proposal.statements for resource in statement.resources]
    assert all("*" not in resource for resource in resources)
    assert any("hazard-coordinates" in resource for resource in resources)
    assert any("aircraft_id-generated_at_epoch-index" in resource for resource in resources)
    assert "wilvor-dev-historical-analytics-query" in proposal.attach_existing_policies
    with pytest.raises(ContractValidationError, match="sprint4_iam_not_applied|sprint5_iam_not_applied"):
        apply_sprint5_iam_delta()


def test_ledger_plan_is_not_execution_and_does_not_claim_exactly_once():
    plan = sprint5_ledger_test_plan()
    assert plan.execution_enabled is False
    assert plan.atomic_admission_is_native_execution is False
    assert plan.distributed_exactly_once is False
    assert any(check.writes_dynamodb for check in plan.checks)
    with pytest.raises(ContractValidationError, match="sprint4_aws_plan_is_not_execution|sprint5_ledger"):
        execute_sprint5_ledger_plan()


def test_live_read_and_model_plans_stay_blocked():
    reads = sprint5_live_read_plan()
    assert reads.execution_enabled is False
    assert len(reads.checks) == 6
    model = sprint5_model_test_plan()
    assert model.execution_enabled is False
    assert model.model_creates_aws_authority is False
    with pytest.raises(ContractValidationError, match="sprint5_live_read_plan_is_not_execution"):
        execute_sprint5_live_reads()
    with pytest.raises(ContractValidationError, match="live_model_invocation_unavailable"):
        execute_sprint5_model_plan()


def test_copilot_jwt_is_fail_closed_and_route_stays_disabled():
    assert SPRINT5_AI_QUERY_ROUTE_ENABLED is False
    assert SPRINT4_AI_QUERY_ROUTE_ENABLED is False
    with pytest.raises(ContractValidationError, match="jwt_required"):
        authenticate_copilot_request({}, AcceptingVerifier())
    principal = authenticate_copilot_request(
        {"Authorization": "Bearer reviewed-token"},
        AcceptingVerifier(),
    )
    assert principal.grant_authority is False
    with pytest.raises(ContractValidationError, match="ai_query_route_must_remain_disabled"):
        activate_public_ai_query_route()
    admitted = admit_copilot_query(
        {
            "schemaVersion": "wilvor.ai.copilot.session_query.v1",
            "liveOpsSchemaVersion": "wilvor.ai.live_ops_public.v1",
            "masterRequestId": "browser-master",
            "planningSessionId": "browser-session",
            "conversationId": "browser-conversation",
            "correlationId": "browser-corr",
            "applicationRequestTimeUtc": "2026-01-15T00:00:01Z",
            "userText": "Which aircraft are currently affected by hazards?",
            "priorTurns": [],
        },
        headers={"Authorization": "Bearer reviewed-token"},
        verifier=AcceptingVerifier(),
    )
    assert admitted.identities.master_request_id != "browser-master"
    assert admitted.labels.planning_session_id == "browser-session"


def test_verification_labels_distinguish_offline_from_blocked_live_aws():
    labels = classify_sprint5_operations()
    assert labels["decision_bounded_query_no_scan"] == Sprint5VerificationStatus.OFFLINE_VERIFIED.value
    assert labels["iam_least_privilege_delta"] == Sprint5VerificationStatus.BLOCKED.value
    assert labels["bedrock_model_invocation"] == Sprint5VerificationStatus.BLOCKED.value
    assert labels["statewide_aircraft_completeness"] == Sprint5VerificationStatus.NOT_IMPLEMENTED.value
    assert labels["distributed_exactly_once_native_execution"] == Sprint5VerificationStatus.NOT_IMPLEMENTED.value
