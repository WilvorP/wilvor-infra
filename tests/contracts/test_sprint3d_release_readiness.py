"""Sprint 3D production and release gates.

These tests pass by showing the current tree is not release-ready. A simulated
AWS client cannot clear a gate. Opening a safety flag would fail the holding
checks below.
"""

from __future__ import annotations

from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_execution_grants import (
    HMAC_PREVENTS_REPLAY,
    PRODUCTION_GRANT_ISSUANCE_AVAILABLE,
    PRODUCTION_SIGNING_KEY_STORE_INTEGRATED,
)
from wilvor_ai.master_gateway_runtime_boundary import ISSUANCE_CONTROL_IDS
from wilvor_ai.sprint3c_evidence_response import (
    ABSENT_RISK_IS_LOW_RISK,
    EMPTY_PAGE_CREATES_VERIFIED_ZERO,
    NUMERICAL_IMPACT_COUNTS_IN_MA5,
    REGIONAL_TEXT_ESTABLISHES_POLYGON_INTERSECTION,
    SAFETY_OR_DIVERSION_INSTRUCTIONS_ENABLED,
)
from wilvor_ai.sprint3c_grant_ledger import SPRINT3C_DISTRIBUTED_EXACTLY_ONCE
from wilvor_ai.sprint3c_ingress_principal import (
    SPRINT3C_AWS_DEPLOYMENT_EVIDENCE_AVAILABLE,
    SPRINT3C_IAM_INVOKE_BOUNDARY_REVIEWED,
    SPRINT3C_INGRESS_ATTESTATION_AVAILABLE,
    SPRINT3C_PRODUCTION_PRINCIPAL_ISSUANCE_ENABLED,
)
from wilvor_ai.sprint3c_scoped_read import (
    APPROVED_SOURCE_ADAPTER,
    LIVE_AWS_OPERATION_PERMITTED,
    PRODUCTION_SCOPED_AWS_READ_ENABLED,
)

from tests.contracts.sprint3d_validation_contract import (
    SCHEMA_VERSION,
    ExecutionTier,
    ai_query_route_default_enabled,
    classify_execution,
    deployment_acceptance_report,
)


HOLDING_GATES = frozenset(
    {
        "exactly_once_not_claimed",
        "false_operational_claims",
        "ai_query_route",
    }
)


def test_release_report_is_not_ready_and_names_the_blockers():
    report = deployment_acceptance_report()
    assert report.schema_version == SCHEMA_VERSION
    assert report.ready is False
    assert report.tier is ExecutionTier.OFFLINE_SIMULATED
    assert report.tier is not ExecutionTier.PRODUCTION_RELEASE
    blockers = {gate.gate_id: gate.blocker for gate in report.gates if gate.cleared is False}
    assert "issuance_controls" in blockers
    assert "ingress_attestation" in blockers
    assert "production_principal" in blockers
    assert "iam_invoke_review" in blockers
    assert "live_aws_deployment_evidence" in blockers
    assert "production_grant" in blockers
    assert "replay_prevention" in blockers
    assert "atomic_ledger_consume" in blockers
    assert "ledger_provenance" in blockers
    assert "production_scoped_read" in blockers
    assert "source_authenticator" in blockers
    assert "native_production_dispatch" in blockers
    assert "sprint3d_hooks" in blockers
    for gate_id in HOLDING_GATES:
        gate = next(item for item in report.gates if item.gate_id == gate_id)
        assert gate.cleared is True
    assert len(ISSUANCE_CONTROL_IDS) == 12
    assert blockers["issuance_controls"].count(",") == 11
    assert all(item.status != "VERIFIED" and item.blocks_release for item in report.missing_hooks)
    assert any(item.status == "ABSENT" for item in report.missing_hooks)
    assert any(item.status == "PRESENT_UNVERIFIED" for item in report.missing_hooks)


def test_safety_flags_and_route_default_still_hold():
    assert SPRINT3C_INGRESS_ATTESTATION_AVAILABLE is False
    assert SPRINT3C_PRODUCTION_PRINCIPAL_ISSUANCE_ENABLED is False
    assert SPRINT3C_IAM_INVOKE_BOUNDARY_REVIEWED is False
    assert SPRINT3C_AWS_DEPLOYMENT_EVIDENCE_AVAILABLE is False
    assert PRODUCTION_GRANT_ISSUANCE_AVAILABLE is False
    assert PRODUCTION_SIGNING_KEY_STORE_INTEGRATED is False
    assert HMAC_PREVENTS_REPLAY is False
    assert SPRINT3C_DISTRIBUTED_EXACTLY_ONCE is False
    assert PRODUCTION_SCOPED_AWS_READ_ENABLED is False
    assert LIVE_AWS_OPERATION_PERMITTED is False
    assert APPROVED_SOURCE_ADAPTER is False
    assert EMPTY_PAGE_CREATES_VERIFIED_ZERO is False
    assert ABSENT_RISK_IS_LOW_RISK is False
    assert REGIONAL_TEXT_ESTABLISHES_POLYGON_INTERSECTION is False
    assert NUMERICAL_IMPACT_COUNTS_IN_MA5 is False
    assert SAFETY_OR_DIVERSION_INSTRUCTIONS_ENABLED is False
    assert list(MasterSourceAuthenticity) == [MasterSourceAuthenticity.NOT_ESTABLISHED]
    assert ai_query_route_default_enabled() is False


def test_simulated_aws_cannot_clear_a_release_gate():
    classification = classify_execution(
        client_type_name="OfflineAwsReadClient",
        live_aws_connection=False,
        verified_account_provenance=True,
        production_flags_open=True,
        probe_executed=True,
    )
    assert classification.tier is ExecutionTier.OFFLINE_SIMULATED
    assert classification.live_pass is False
    assert classification.release_ready is False
    report = deployment_acceptance_report()
    assert report.ready is False
    assert "sprint3d_hooks" in {gate.gate_id for gate in report.gates if gate.cleared is False}
