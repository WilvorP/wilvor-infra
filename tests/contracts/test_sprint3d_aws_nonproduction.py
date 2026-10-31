"""Explicitly gated Sprint 3D real AWS nonproduction suite.

The default run skips the live probes. A skip is not a live pass. Opening
the gate without a verified read-only probe fails closed and does not call
AWS. This module performs no AWS writes.
"""

from __future__ import annotations

import os

import pytest

from tests.contracts.sprint3d_validation_contract import (
    REAL_AWS_ACCOUNT_ENV,
    REAL_AWS_GATE_ENV,
    REAL_AWS_LEDGER_TABLE_ENV,
    REAL_AWS_REGION_ENV,
    REAL_AWS_WRITES_ENV,
    REQUIRED_LEDGER_TABLE,
    ExecutionTier,
    RealAwsGateState,
    classify_execution,
    evaluate_real_aws_gate,
)
from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.sprint3c_scoped_read import bind_offline_aws_read_client


def _closed_status():
    return evaluate_real_aws_gate({})


def _open_without_hooks():
    return evaluate_real_aws_gate(
        {
            REAL_AWS_GATE_ENV: "1",
            REAL_AWS_ACCOUNT_ENV: "123456789012",
            REAL_AWS_REGION_ENV: "us-west-1",
            REAL_AWS_LEDGER_TABLE_ENV: REQUIRED_LEDGER_TABLE,
        }
    )


def test_closed_gate_is_not_a_live_aws_pass():
    status = _closed_status()
    assert status.state is RealAwsGateState.CLOSED
    assert status.executed is False
    assert status.live_pass is False
    assert "not a successful live AWS test" in status.reason


def test_open_gate_without_verified_hooks_fails_closed_without_aws():
    status = _open_without_hooks()
    assert status.state is RealAwsGateState.FAIL_CLOSED
    assert status.executed is False
    assert status.live_pass is False
    assert "probe_nonproduction_readonly" in status.reason
    assert "verify_ledger_account_and_table" in status.reason
    assert status.missing_hooks


def test_write_request_is_refused_even_when_the_gate_is_open():
    status = evaluate_real_aws_gate(
        {
            REAL_AWS_GATE_ENV: "1",
            REAL_AWS_WRITES_ENV: "1",
            REAL_AWS_ACCOUNT_ENV: "123456789012",
            REAL_AWS_REGION_ENV: "us-west-1",
            REAL_AWS_LEDGER_TABLE_ENV: REQUIRED_LEDGER_TABLE,
        }
    )
    assert status.state is RealAwsGateState.FAIL_CLOSED
    assert status.executed is False
    assert status.live_pass is False
    assert "writes are refused" in status.reason


def test_offline_read_client_is_not_a_nonproduction_probe():
    client = bind_offline_aws_read_client()
    classification = classify_execution(
        client_type_name=type(client).__name__,
        live_aws_connection=client.live_aws_connection,
        verified_account_provenance=False,
        production_flags_open=False,
        probe_executed=False,
    )
    assert classification.tier is ExecutionTier.OFFLINE_SIMULATED
    assert classification.live_pass is False
    assert client.live_aws_connection is False
    with pytest.raises(ContractValidationError, match="simulated_execution_is_not_a_live_aws_pass"):
        from tests.contracts.sprint3d_validation_contract import refuse_unverified_live_pass

        refuse_unverified_live_pass(classification)


def _require_real_probe():
    status = evaluate_real_aws_gate(os.environ)
    if status.state is RealAwsGateState.CLOSED:
        pytest.skip(status.reason)
    if status.state is not RealAwsGateState.READY_TO_PROBE:
        pytest.fail(status.reason)
    pytest.fail(
        "Verified probe prerequisites exist, but this suite does not call AWS "
        "and will not record a live pass without a read-only probe result."
    )


def test_nonproduction_ledger_describe_is_gated():
    """Describe wilvor_master_execution_ledger. Not a write, and not a fake."""

    _require_real_probe()


def test_nonproduction_api_and_lambda_policy_inspection_is_gated():
    """apigatewayv2:GetApi and lambda:GetPolicy against the twelve controls."""

    _require_real_probe()


def test_nonproduction_specialist_get_or_query_is_gated():
    """One reviewed GET_ITEM or QUERY. Scan and writes stay out of this suite."""

    _require_real_probe()
