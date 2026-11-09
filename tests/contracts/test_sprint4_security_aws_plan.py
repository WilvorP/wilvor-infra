"""Sprint 4 AWS test plan is a plan. Executing it from this sprint fails closed."""

from __future__ import annotations

import os

import pytest

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_execution_ledger import CONDITIONAL_PUT_ABSENT, DYNAMODB_LEDGER_TABLE_NAME
from wilvor_ai.sprint3d_aws_execution import LIVE_NETWORK_GATE_ENV, LIVE_NETWORK_GATE_VALUE
from wilvor_ai.sprint4_security_authorization import AIRCRAFT_TABLE_NAME, REVIEWED_DEV_ACCOUNT_ID
from wilvor_ai.sprint4_security_aws_plan import (
    SPRINT4_AWS_PLAN_EXECUTION_ENABLED,
    AwsTestStep,
    execute_aws_test_plan,
    sprint4_aws_test_plan,
    write_steps,
)
from wilvor_ai.sprint4_security_ledger import HOST_TIMEOUT_REASON, SPRINT4_LEDGER_WRITES_ENABLED


def test_aws_plan_is_not_execution_and_lists_the_required_checks():
    plan = sprint4_aws_test_plan()
    assert bool(plan) is False
    assert SPRINT4_AWS_PLAN_EXECUTION_ENABLED is False
    assert SPRINT4_LEDGER_WRITES_ENABLED is False
    assert plan.execution_enabled is False
    assert plan.writes_enabled is False
    assert plan.live_gate_open_in_this_process is False
    assert plan.account_id == REVIEWED_DEV_ACCOUNT_ID
    assert plan.ledger_table == DYNAMODB_LEDGER_TABLE_NAME
    assert plan.aircraft_table == AIRCRAFT_TABLE_NAME
    assert plan.live_gate_env == LIVE_NETWORK_GATE_ENV
    assert plan.live_gate_value == LIVE_NETWORK_GATE_VALUE
    ids = tuple(step.step_id for step in plan.steps)
    assert ids == ("CP1", "CP2", "CP3", "DP1", "DP2", "W1", "W2", "W3", "W4", "N1", "N2", "N3", "N4", "R1")
    assert all(step.live_execution_permitted is False for step in plan.steps)
    assert all(bool(step) is False for step in plan.steps)
    writes = write_steps(plan)
    assert tuple(step.step_id for step in writes) == ("W1", "W2", "W3", "W4")
    assert all(step.writes_dynamodb is True for step in writes)
    assert all(step.phase == "data_plane_write" for step in writes)
    consume = next(step for step in writes if step.step_id == "W1")
    assert "TransactWriteItems" in consume.api
    assert CONDITIONAL_PUT_ABSENT in consume.expected
    assert "exactly-once" in consume.does_not_prove
    timeout = next(step for step in writes if step.step_id == "W4")
    assert HOST_TIMEOUT_REASON in timeout.expected
    assert plan.admission_contract["atomic_admission_is_native_execution"] is False
    assert plan.client_contract["ledger"]["exactly_once_native_execution"] is False
    with pytest.raises(ContractValidationError, match="sprint4_aws_plan_is_not_execution"):
        execute_aws_test_plan(plan)


def test_negative_steps_cover_scan_query_specialist_writes_and_models():
    plan = sprint4_aws_test_plan()
    by_id = {step.step_id: step for step in plan.steps}
    assert by_id["N1"].api == "dynamodb:Scan"
    assert by_id["N2"].api == "dynamodb:Query"
    assert "callsign" in by_id["N2"].purpose
    assert by_id["N3"].api == "dynamodb:PutItem"
    assert by_id["N4"].api == "bedrock:InvokeModel"
    assert all(step.writes_dynamodb is False for step in plan.steps if step.phase != "data_plane_write")
    assert "live_network_gate_closed" in plan.blockers


def test_open_live_gate_refuses_to_publish_the_plan(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(LIVE_NETWORK_GATE_ENV, LIVE_NETWORK_GATE_VALUE)
    assert os.environ.get(LIVE_NETWORK_GATE_ENV) == LIVE_NETWORK_GATE_VALUE
    with pytest.raises(ContractValidationError, match="sprint4_aws_plan_refuses_open_live_gate"):
        sprint4_aws_test_plan()


def test_write_step_cannot_claim_live_execution_permitted():
    with pytest.raises(ContractValidationError, match="sprint4_aws_plan_is_not_execution"):
        AwsTestStep(
            step_id="W0",
            phase="data_plane_write",
            api="dynamodb:TransactWriteItems",
            purpose="attempt a live write",
            expected="denied",
            proves="nothing",
            does_not_prove="execution",
            writes_dynamodb=True,
            live_execution_permitted=True,
        )
