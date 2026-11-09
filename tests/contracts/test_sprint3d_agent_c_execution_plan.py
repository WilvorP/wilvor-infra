"""Execution-role and ledger-write plans for Agent C.

The execution path is not activated. The ledger transaction is built and
checked against the existing wrapper, then refused before any AWS client
exists. An in-memory conditional check is not a DynamoDB write and is not
distributed exactly-once execution.
"""

from __future__ import annotations

import json

import pytest

from tests.contracts.sprint3d_agent_c_readonly_harness import (
    AIRCRAFT_TABLE_NAME,
    ENCOUNTER_TABLE_NAME,
    EXECUTION_ROLE_VALIDATION_DESIGN,
    LEDGER_NAMESPACE,
    METAR_TABLE_NAME,
    PRODUCTION_NOT_READY,
    REAL_AWS_WRITE_NOT_EXECUTED,
    accept_proposed_transaction_offline,
    build_namespaced_ledger_proposal,
    execute_proposed_ledger_transaction,
    live_probe_disposition,
    offline_shape_authorization,
)
from tests.contracts.sprint3d_validation_contract import REAL_AWS_WRITES_ENV
from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_execution_ledger import (
    CONDITIONAL_PUT_ABSENT,
    CONDITIONAL_REPLACE_EXACT,
    DYNAMODB_LEDGER_TABLE_NAME,
    TTL_DELETION_IS_CORRECTNESS,
)
from wilvor_ai.sprint3c_grant_ledger import SPRINT3C_DISTRIBUTED_EXACTLY_ONCE
from wilvor_ai.sprint3d_aws_execution import (
    SPRINT3D_DISTRIBUTED_EXACTLY_ONCE,
    SPRINT3D_LIVE_NETWORK_TESTS_ENABLED,
    SPRINT3D_PRODUCTION_EXECUTION_ENABLED,
    AuthorizedLedgerDynamoClient,
    _TTL_ATTRIBUTES,
    open_production_aws_execution,
)


class _Capture:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_item(self, **_kwargs: object) -> dict[str, object]:
        self.calls.append("get_item")
        return {}

    def put_item(self, **_kwargs: object) -> dict[str, object]:
        self.calls.append("put_item")
        return {}

    def transact_write_items(self, **_kwargs: object) -> dict[str, object]:
        self.calls.append("transact_write_items")
        return {}


def test_execution_role_validation_is_designed_and_not_activated():
    assert EXECUTION_ROLE_VALIDATION_DESIGN["activated"] is False
    assert EXECUTION_ROLE_VALIDATION_DESIGN["assume_role_from_operator"] is False
    assert EXECUTION_ROLE_VALIDATION_DESIGN["trust_policy_change"] is False
    assert EXECUTION_ROLE_VALIDATION_DESIGN["lambda_invoke"] is False
    assert EXECUTION_ROLE_VALIDATION_DESIGN["reserved_concurrency_change"] is False
    assert EXECUTION_ROLE_VALIDATION_DESIGN["role_name"] == "wilvor-dev-ai-host-lambda-role"
    assert EXECUTION_ROLE_VALIDATION_DESIGN["trust_principal"] == "lambda.amazonaws.com"
    assert "wilvor-dev-ai-host" in EXECUTION_ROLE_VALIDATION_DESIGN["trust_source_arn"]
    assert SPRINT3D_PRODUCTION_EXECUTION_ENABLED is False
    assert SPRINT3D_LIVE_NETWORK_TESTS_ENABLED is False
    assert PRODUCTION_NOT_READY == "PRODUCTION_NOT_READY"
    with pytest.raises(ContractValidationError, match="production_execution_closed"):
        open_production_aws_execution()


def test_namespaced_ledger_proposal_is_one_bounded_unsent_transaction():
    proposal = build_namespaced_ledger_proposal()
    items = proposal.transaction["TransactItems"]
    assert isinstance(items, list) and len(items) == 2
    started = items[0]["Put"]
    marker = items[1]["Put"]
    assert started["TableName"] == DYNAMODB_LEDGER_TABLE_NAME
    assert marker["TableName"] == DYNAMODB_LEDGER_TABLE_NAME
    assert started["ConditionExpression"] == CONDITIONAL_PUT_ABSENT
    assert marker["ConditionExpression"] == CONDITIONAL_PUT_ABSENT
    assert started["Item"]["state"]["S"] == "STARTED"
    assert started["Item"]["pk"]["S"].startswith(f"ledger-v1#{LEDGER_NAMESPACE}-")
    assert marker["Item"]["pk"]["S"] == f"consume-v1#{proposal.grant_nonce}"
    assert proposal.grant_nonce.startswith(f"{LEDGER_NAMESPACE}-")
    assert proposal.attempt_token != proposal.grant_nonce
    assert not any(name in marker["Item"] for name in _TTL_ATTRIBUTES)
    assert "retention_expires_at_epoch" in started["Item"]
    rendered = json.dumps(proposal.transaction)
    assert AIRCRAFT_TABLE_NAME not in rendered
    assert METAR_TABLE_NAME not in rendered
    assert ENCOUNTER_TABLE_NAME not in rendered
    assert marker["Item"]["distributed_exactly_once"] == {"BOOL": False}
    assert marker["Item"]["specialist_executed"] == {"BOOL": False}
    assert marker["Item"]["production_grant"] == {"BOOL": False}
    assert marker["Item"]["verified_aws_provenance"] == {"BOOL": False}
    assert proposal.sent_to_aws is False
    assert proposal.distributed_exactly_once is False
    assert proposal.native_exactly_once is False
    assert proposal.specialist_executed is False
    assert proposal.classification == REAL_AWS_WRITE_NOT_EXECUTED
    assert SPRINT3C_DISTRIBUTED_EXACTLY_ONCE is False
    assert SPRINT3D_DISTRIBUTED_EXACTLY_ONCE is False
    assert TTL_DELETION_IS_CORRECTNESS is False


def test_replay_and_fence_boundaries_hold_without_calling_aws():
    proposal = build_namespaced_ledger_proposal()
    store: dict[tuple[str, str], dict[str, object]] = {}

    def apply(transaction: dict[str, object]) -> str:
        planned: list[tuple[tuple[str, str], dict[str, object]]] = []
        raw_items = transaction["TransactItems"]
        assert isinstance(raw_items, list)
        for entry in raw_items:
            assert isinstance(entry, dict)
            put = entry["Put"]
            assert isinstance(put, dict)
            item = put["Item"]
            assert isinstance(item, dict)
            key = (str(item["pk"]["S"]), str(item["sk"]["S"]))
            if put["ConditionExpression"] != CONDITIONAL_PUT_ABSENT or key in store:
                return "canceled"
            planned.append((key, item))
        for key, item in planned:
            store[key] = item
        return "applied"

    assert apply(proposal.transaction) == "applied"
    assert apply(proposal.transaction) == "canceled"
    assert len(store) == 2
    assert proposal.fence_expression == CONDITIONAL_REPLACE_EXACT
    assert "#fence_epoch = :fence_epoch" in proposal.fence_expression
    assert "#grant_nonce = :grant_nonce" in proposal.fence_expression
    assert proposal.fence_epoch == 1
    assert 2 != proposal.fence_epoch

    client = _Capture()
    accept_proposed_transaction_offline(client, proposal)
    assert client.calls == ["transact_write_items"]
    started_item = proposal.transaction["TransactItems"][0]["Put"]["Item"]
    ledger = AuthorizedLedgerDynamoClient(client, offline_shape_authorization())
    with pytest.raises(ContractValidationError, match="unexpected_ledger_condition"):
        ledger.put_item(
            TableName=DYNAMODB_LEDGER_TABLE_NAME,
            Item=started_item,
            ConditionExpression="attribute_exists(#pk)",
            ExpressionAttributeNames={"#pk": "pk"},
        )
    assert client.calls == ["transact_write_items"]
    ledger.put_item(
        TableName=DYNAMODB_LEDGER_TABLE_NAME,
        Item=started_item,
        ConditionExpression=proposal.fence_expression,
        ExpressionAttributeNames={"#fence_epoch": "fence_epoch"},
        ExpressionAttributeValues={":fence_epoch": {"N": "1"}},
    )
    assert client.calls == ["transact_write_items", "put_item"]


def test_proposed_ledger_transaction_is_not_executed():
    disposition, reason = live_probe_disposition({REAL_AWS_WRITES_ENV: "1"})
    assert disposition == "fail"
    assert "writes are refused" in reason
    proposal = build_namespaced_ledger_proposal()
    assert proposal.sent_to_aws is False
    with pytest.raises(ContractValidationError, match="ledger_write_not_approved"):
        execute_proposed_ledger_transaction(proposal)
