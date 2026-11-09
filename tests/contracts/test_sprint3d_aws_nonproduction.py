"""Explicitly gated Sprint 3D real AWS nonproduction suite.

The default run skips the live probes. A skip is not a live pass. Opening
the contract gate without a verified probe fails closed and does not call
AWS. Operator inspection is a separate explicit gate. It uses the wilvor-dev
profile for control-plane reads and one bounded GetItem, and it does not
prove the AI execution role. This module performs no AWS writes.
"""

from __future__ import annotations

import os

import pytest

from tests.contracts.sprint3d_agent_c_readonly_harness import (
    ACCOUNT_ID,
    AIRCRAFT_TABLE_NAME,
    CALLSIGN_INDEX_NAME,
    ENCOUNTER_TABLE_NAME,
    EXECUTION_ROLE_VALIDATION_DESIGN,
    FUNCTION_NAME,
    H3_INDEX_NAME,
    LIVE_NETWORK_GATE_ENV,
    METAR_TABLE_NAME,
    OPERATOR_READONLY_ENV,
    OPERATOR_READONLY_VALUE,
    PRODUCTION_NOT_READY,
    PROBE_AIRCRAFT_ID,
    REAL_AWS_READ_ONLY_PASS,
    ROLE_NAME,
    accept_bounded_getitem_offline,
    bounded_aircraft_getitem_kwargs,
    collect_operator_readonly_evidence,
    expected_iam_decisions,
    live_probe_disposition,
    reject_unauthorized_mappings_offline,
    reviewed_dev_deployment,
)
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
from wilvor_ai.master_execution_ledger import DYNAMODB_LEDGER_TABLE_NAME
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.sprint3c_scoped_read import bind_offline_aws_read_client
from wilvor_ai.sprint3d_aws_execution import (
    LIVE_NETWORK_GATE_VALUE,
    AwsExecutionRefusal,
    StsCallerIdentity,
    review_aws_execution_authority,
    reviewed_read_table,
    table_arn,
)


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


class _Capture:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get_item(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("get_item", dict(kwargs)))
        return {}

    def query(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("query", dict(kwargs)))
        raise AssertionError("query reached the inner client")

    def scan(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("scan", dict(kwargs)))
        raise AssertionError("scan reached the inner client")

    def put_item(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("put_item", dict(kwargs)))
        raise AssertionError("put reached the inner client")


def _described(name: str, hash_key: str, range_key: str | None = None) -> dict[str, object]:
    schema = [{"AttributeName": hash_key, "KeyType": "HASH"}]
    if range_key is not None:
        schema.append({"AttributeName": range_key, "KeyType": "RANGE"})
    return {
        "Table": {
            "TableName": name,
            "TableStatus": "ACTIVE",
            "TableArn": table_arn(ACCOUNT_ID, "us-west-1", name),
            "KeySchema": schema,
            "GlobalSecondaryIndexes": [],
        }
    }


def test_operator_inspection_closed_is_not_a_live_pass():
    disposition, reason = live_probe_disposition({})
    assert disposition == "skip"
    assert "not a successful live AWS test" in reason


def test_operator_inspection_refuses_writes_and_the_execution_network_gate():
    writes, write_reason = live_probe_disposition({REAL_AWS_WRITES_ENV: "1", OPERATOR_READONLY_ENV: OPERATOR_READONLY_VALUE})
    assert writes == "fail"
    assert "writes are refused" in write_reason
    network, network_reason = live_probe_disposition(
        {OPERATOR_READONLY_ENV: OPERATOR_READONLY_VALUE, LIVE_NETWORK_GATE_ENV: LIVE_NETWORK_GATE_VALUE}
    )
    assert network == "fail"
    assert "live-network gate stays closed" in network_reason
    opened, opened_reason = live_probe_disposition(
        {
            REAL_AWS_GATE_ENV: "1",
            REAL_AWS_ACCOUNT_ENV: ACCOUNT_ID,
            REAL_AWS_REGION_ENV: "us-west-1",
            REAL_AWS_LEDGER_TABLE_ENV: REQUIRED_LEDGER_TABLE,
            OPERATOR_READONLY_ENV: OPERATOR_READONLY_VALUE,
        }
    )
    assert opened == "fail"
    assert "probe_nonproduction_readonly" in opened_reason


def test_operator_identity_does_not_open_the_execution_port():
    deployment = reviewed_dev_deployment()
    review = review_aws_execution_authority(
        deployment,
        caller_identity=StsCallerIdentity(
            account_id=ACCOUNT_ID,
            arn=f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/wilvor-operator-inspection-not-the-ai-role/harness",
            user_id="AROAOPERATORHARNESS:harness",
        ),
        described_tables=(
            _described(DYNAMODB_LEDGER_TABLE_NAME, "pk", "sk"),
            _described(AIRCRAFT_TABLE_NAME, "aircraft_id"),
        ),
        iam_simulation=expected_iam_decisions(deployment),
    )
    assert review.authorization is None
    assert review.refusal is AwsExecutionRefusal.ACCOUNT_MISMATCH
    assert review.data_plane_invoked is False


def test_unauthorized_mappings_are_rejected_before_a_client_call():
    client = _Capture()
    accept_bounded_getitem_offline(client)
    assert client.calls == [("get_item", bounded_aircraft_getitem_kwargs())]
    rejected = _Capture()
    reasons = reject_unauthorized_mappings_offline(rejected)
    assert rejected.calls == []
    assert "scan_not_permitted" in " ".join(reasons)
    assert "arbitrary_table" in " ".join(reasons)
    assert "write_not_permitted" in " ".join(reasons)
    with pytest.raises(ContractValidationError, match="scan_not_permitted"):
        reviewed_read_table(
            logical_resource_id="aircraft",
            physical_table_name=AIRCRAFT_TABLE_NAME,
            source_name="aircraft-current-state",
            source_version_attribute="source_version",
            observation_time_attribute="source_observed_at_utc",
            hash_attribute="aircraft_id",
            query_partition_attribute="callsign",
            allowed_operations=frozenset({ReadOperation.SCAN}),
        )
    with pytest.raises(ContractValidationError, match="arbitrary_table"):
        reviewed_read_table(
            logical_resource_id="aircraft",
            physical_table_name=DYNAMODB_LEDGER_TABLE_NAME,
            source_name="aircraft-current-state",
            source_version_attribute="source_version",
            observation_time_attribute="source_observed_at_utc",
            hash_attribute="aircraft_id",
            query_partition_attribute="callsign",
            allowed_operations=frozenset({ReadOperation.GET_ITEM}),
        )


@pytest.fixture(scope="module")
def operator_evidence():
    disposition, reason = live_probe_disposition(os.environ)
    if disposition == "skip":
        pytest.skip(reason)
    if disposition != "run":
        pytest.fail(reason)
    return collect_operator_readonly_evidence()


def test_sts_account_identity_is_the_reviewed_account_and_not_the_ai_role(operator_evidence):
    assert operator_evidence.caller.account_id == ACCOUNT_ID
    assert operator_evidence.caller_is_ai_execution_role is False
    assert ROLE_NAME not in operator_evidence.caller.arn
    assert operator_evidence.authority_refusal is AwsExecutionRefusal.ACCOUNT_MISMATCH
    assert operator_evidence.ai_execution_role_proven is False
    assert "sts.get_caller_identity" in operator_evidence.calls
    assert "sts.assume_role" not in operator_evidence.calls


def test_ledger_table_schema_matches_the_agent_c_contract(operator_evidence):
    assert operator_evidence.ledger_name == DYNAMODB_LEDGER_TABLE_NAME
    assert operator_evidence.ledger_hash_key == "pk"
    assert operator_evidence.ledger_range_key == "sk"
    assert operator_evidence.ledger_arn == table_arn(ACCOUNT_ID, "us-west-1", DYNAMODB_LEDGER_TABLE_NAME)
    assert operator_evidence.ledger_ttl_attribute == "retention_expires_at_epoch"
    assert "dynamodb.describe_table" in operator_evidence.calls


def test_aircraft_current_state_schema_matches_the_reviewed_mapping(operator_evidence):
    assert operator_evidence.aircraft_name == AIRCRAFT_TABLE_NAME
    assert operator_evidence.aircraft_hash_key == "aircraft_id"
    assert operator_evidence.aircraft_range_key is None
    assert operator_evidence.aircraft_arn == table_arn(ACCOUNT_ID, "us-west-1", AIRCRAFT_TABLE_NAME)
    assert operator_evidence.aircraft_ttl_attribute == "expires_at_epoch"
    indexes = {name: (hash_key, range_key) for name, hash_key, range_key in operator_evidence.aircraft_indexes}
    assert indexes[CALLSIGN_INDEX_NAME] == ("callsign", "position_time_epoch")
    assert indexes[H3_INDEX_NAME][0] == "current_h3_cell"


def test_deployed_ai_host_permissions_match_the_agent_c_matrix(operator_evidence):
    assert operator_evidence.iam_rows == expected_iam_decisions(reviewed_dev_deployment())
    assert operator_evidence.trust_principals == ("lambda.amazonaws.com",)
    assert operator_evidence.trust_source_arn == EXECUTION_ROLE_VALIDATION_DESIGN["trust_source_arn"]
    assert EXECUTION_ROLE_VALIDATION_DESIGN["activated"] is False
    assert "iam.simulate_principal_policy" in operator_evidence.calls
    assert "iam.get_role" in operator_evidence.calls


def test_ai_lambda_reserved_concurrency_remains_zero(operator_evidence):
    assert operator_evidence.reserved_concurrency == 0
    environment = dict(operator_evidence.lambda_environment)
    assert environment == {
        "AI_QUERY_ROUTE_ENABLED": "false",
        "PRODUCTION_ISSUANCE_AVAILABLE": "false",
        "LEDGER_WRITES_ENABLED": "false",
        "LEDGER_TABLE_NAME": DYNAMODB_LEDGER_TABLE_NAME,
    }
    assert "lambda.get_function_concurrency" in operator_evidence.calls
    assert "lambda.invoke" not in operator_evidence.calls
    assert "lambda.put_function_concurrency" not in operator_evidence.calls
    assert FUNCTION_NAME == "wilvor-dev-ai-host"


def test_post_ai_query_route_remains_absent(operator_evidence):
    assert operator_evidence.ai_query_routes == ()
    assert operator_evidence.resource_policy_absent is True
    assert "apigatewayv2.get_apis" in operator_evidence.calls
    assert "apigatewayv2.get_routes" in operator_evidence.calls
    assert "lambda.get_policy" in operator_evidence.calls


def test_bounded_getitem_uses_operator_credentials_on_the_probe_key(operator_evidence):
    assert operator_evidence.get_item_has_item is False
    assert operator_evidence.verified_zero is False
    assert operator_evidence.source_authenticity_established is False
    assert operator_evidence.calls.count("dynamodb.get_item") == 1
    assert PROBE_AIRCRAFT_ID == "WILVOR-SPRINT3D-READONLY-PROBE"
    assert operator_evidence.caller_is_ai_execution_role is False


def test_unauthorized_live_mappings_are_denied_for_the_ai_role(operator_evidence):
    denied = {(action, resource) for action, resource, decision in operator_evidence.unauthorized_denies}
    aircraft_index = f"{table_arn(ACCOUNT_ID, 'us-west-1', AIRCRAFT_TABLE_NAME)}/index/{CALLSIGN_INDEX_NAME}"
    expected = {
        ("dynamodb:GetItem", table_arn(ACCOUNT_ID, "us-west-1", METAR_TABLE_NAME)),
        ("dynamodb:PutItem", table_arn(ACCOUNT_ID, "us-west-1", METAR_TABLE_NAME)),
        ("dynamodb:Query", table_arn(ACCOUNT_ID, "us-west-1", METAR_TABLE_NAME)),
        ("dynamodb:GetItem", table_arn(ACCOUNT_ID, "us-west-1", ENCOUNTER_TABLE_NAME)),
        ("dynamodb:TransactWriteItems", table_arn(ACCOUNT_ID, "us-west-1", ENCOUNTER_TABLE_NAME)),
        ("dynamodb:Query", aircraft_index),
        ("dynamodb:GetItem", aircraft_index),
        ("dynamodb:Scan", table_arn(ACCOUNT_ID, "us-west-1", DYNAMODB_LEDGER_TABLE_NAME)),
    }
    assert denied == expected
    assert {decision for _action, _resource, decision in operator_evidence.unauthorized_denies} == {"deny"}
    assert "dynamodb.query" not in operator_evidence.calls
    assert "dynamodb.scan" not in operator_evidence.calls
    assert "dynamodb.put_item" not in operator_evidence.calls
    assert "dynamodb.transact_write_items" not in operator_evidence.calls


def test_operator_readonly_pass_is_not_production_and_not_an_ai_role_pass(operator_evidence):
    assert operator_evidence.classification == REAL_AWS_READ_ONLY_PASS
    assert operator_evidence.production_ready is False
    assert operator_evidence.writes_executed is False
    assert operator_evidence.ai_execution_role_proven is False
    assert PRODUCTION_NOT_READY == "PRODUCTION_NOT_READY"
    classification = classify_execution(
        client_type_name="OperatorInspectionSession",
        live_aws_connection=True,
        verified_account_provenance=False,
        production_flags_open=False,
        probe_executed=True,
    )
    assert classification.tier is ExecutionTier.OFFLINE_SIMULATED
    assert classification.live_pass is False
    assert classification.release_ready is False
