"""Sprint 3D Agent C: AWS ledger and bounded reads, with live network calls gated."""

from __future__ import annotations

import ast
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError, TemporalScope
from wilvor_ai.master_authorized_scope import bind_local_test_host, promote_effective_scope_to_execution_grant
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_execution_grants import (
    PRODUCTION_GRANT_ISSUANCE_AVAILABLE,
    GrantEvaluationClock,
    LocalHarnessGrantIssuer,
    LogicalReadOperation,
    MasterExecutionGrantEnvelope,
    bind_production_grant_to_master_ledger,
    issue_production_execution_grant,
)
from wilvor_ai.master_execution_ledger import (
    DURABLE_ADAPTER_TYPE_IS_VERIFIED_AWS_PROVENANCE,
    DYNAMODB_LEDGER_TABLE_NAME,
    TTL_DELETION_IS_CORRECTNESS,
    LedgerStoreUnavailable,
    MasterExecutionLedgerOutcome,
    MasterExecutionLedgerRejection,
    MasterExecutionLedgerState,
    ledger_item_key,
)
from wilvor_ai.master_read_resource_guard import HISTORICAL_LIST_MAX, ReadOperation
from wilvor_ai.master_trusted_read_executor import (
    PRODUCTION_READ_EXECUTOR_ENABLED,
    UntrustedReadRequest,
    open_production_executor,
)
from wilvor_ai.sprint3c_grant_ledger import (
    SPRINT3C_DISTRIBUTED_EXACTLY_ONCE,
    consumption_marker_key,
    ledger_envelope_from_grant,
)
from wilvor_ai.sprint3c_scoped_read import (
    LIVE_AWS_OPERATION_PERMITTED,
    plan_server_owned_aws_read,
)
from wilvor_ai.sprint3d_aws_execution import (
    LIVE_NETWORK_GATE_ENV,
    LIVE_NETWORK_GATE_VALUE,
    SPRINT3D_DISTRIBUTED_EXACTLY_ONCE,
    SPRINT3D_LEDGER_TRANSACTION_IS_NATIVE_EXECUTION,
    SPRINT3D_LIVE_NETWORK_TESTS_ENABLED,
    SPRINT3D_PRODUCTION_EXECUTION_ENABLED,
    UPSTREAM_ISSUER,
    AuthorizedLedgerDynamoClient,
    AwsExecutionRefusal,
    build_boto3_dynamodb_client,
    collect_live_control_plane,
    expected_iam_decisions,
    iam_role_arn,
    live_aws_blockers,
    live_network_gate_open,
    open_production_aws_execution,
    open_reviewed_aws_execution,
    real_client_integration_contract,
    reviewed_aws_deployment,
    reviewed_read_table,
    seal_reviewed_upstream_identity,
    table_arn,
)
from wilvor_ai.sprint3c_grant_ledger import Sprint3CIdentityBinding as IdentityBinding


ACCOUNT = "123456789012"
REGION = "us-east-1"
ROLE = "wilvor-reviewed-execution"
AIRCRAFT_TABLE = "wilvor-reviewed-aircraft-current-state"
HISTORY_TABLE = "wilvor-reviewed-historical-encounters"
AIRCRAFT_SOURCE = "aircraft-current-state"
HISTORY_SOURCE = "historical-encounters"
ACCEPTED = "2026-10-27T19:00:00Z"
NOW = "2026-10-27T20:00:00Z"
EXPIRED = "2026-10-27T20:01:00Z"
DEADLINE = "2026-10-27T21:00:00Z"
EXPIRES = "2026-10-27T21:00:00Z"
OBSERVED = "2026-10-27T17:30:00Z"
EVALUATED = "2026-10-27T18:00:00Z"
SOURCE_OBSERVED = "2026-10-27T12:00:00Z"
DECOY_OBSERVED = "2026-10-27T13:00:00Z"
HIST_START = "2026-10-20T18:00:00Z"
HIST_END = "2026-10-27T18:00:00Z"
EIGHT_DAY_START = "2026-10-19T18:00:00Z"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORICAL = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
MODULE = Path(__file__).resolve().parents[2] / "functions" / "shared" / "wilvor_ai" / "sprint3d_aws_execution.py"


class Sequence:
    def __init__(self) -> None:
        self._n = 0
        self._lock = threading.Lock()

    def __call__(self) -> str:
        with self._lock:
            self._n += 1
            return f"tokenseq{self._n:04d}"


class ConditionalCheckFailedException(Exception):
    def __init__(self) -> None:
        super().__init__("conditional")
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class TransactionCanceledException(Exception):
    def __init__(self) -> None:
        super().__init__("canceled")
        self.response = {"Error": {"Code": "TransactionCanceledException"}}


class ThrottlingException(Exception):
    def __init__(self) -> None:
        super().__init__("throttled")
        self.response = {"Error": {"Code": "ThrottlingException"}}


class LedgerFake:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, dict[str, object]]] = {}
        self.operations: list[str] = []
        self.throttle_remaining = 0
        self._lock = threading.Lock()

    def get_item(self, **kwargs: object) -> dict[str, object]:
        with self._lock:
            self.operations.append("get_item")
            key = _item_key(kwargs["Key"])
            item = self.items.get(key)
            if item is None:
                return {}
            return {"Item": dict(item)}

    def put_item(self, **kwargs: object) -> dict[str, object]:
        with self._lock:
            self.operations.append("put_item")
            item = kwargs["Item"]
            assert isinstance(item, dict)
            key = (_text(item["pk"]), _text(item["sk"]))
            current = self.items.get(key)
            names = kwargs["ExpressionAttributeNames"]
            values = kwargs.get("ExpressionAttributeValues", {})
            assert isinstance(names, dict)
            if not _condition_holds(current, str(kwargs["ConditionExpression"]), names, values):
                raise ConditionalCheckFailedException()
            self.items[key] = dict(item)
            return {}

    def transact_write_items(self, **kwargs: object) -> dict[str, object]:
        with self._lock:
            self.operations.append("transact_write_items")
            if self.throttle_remaining:
                self.throttle_remaining -= 1
                raise ThrottlingException()
            items = kwargs["TransactItems"]
            assert isinstance(items, list)
            planned: list[tuple[tuple[str, str], dict[str, dict[str, object]]]] = []
            for entry in items:
                assert isinstance(entry, dict)
                put = entry["Put"]
                item = put["Item"]
                assert isinstance(item, dict)
                key = (_text(item["pk"]), _text(item["sk"]))
                names = put["ExpressionAttributeNames"]
                values = put.get("ExpressionAttributeValues", {})
                if not _condition_holds(self.items.get(key), str(put["ConditionExpression"]), names, values):
                    raise TransactionCanceledException()
                planned.append((key, dict(item)))
            for key, item in planned:
                self.items[key] = item
            return {}

    def scan(self, **_kwargs: object) -> dict[str, object]:
        self.operations.append("scan")
        raise AssertionError("scan reached the ledger client")


class ReadFake:
    def __init__(self) -> None:
        self.operations: list[str] = []
        self.requests: list[dict[str, object]] = []
        self.items: dict[tuple[str, str], dict[str, dict[str, object]]] = {}
        self.queries: dict[str, dict[str, object]] = {}
        self._lock = threading.Lock()

    def get_item(self, **kwargs: object) -> dict[str, object]:
        with self._lock:
            self.operations.append("get_item")
            self.requests.append(dict(kwargs))
            key = kwargs["Key"]
            assert isinstance(key, dict)
            attribute = next(iter(key.values()))
            assert isinstance(attribute, dict)
            token = attribute["S"]
            item = self.items.get((str(kwargs["TableName"]), str(token)))
            if item is None:
                return {}
            return {"Item": dict(item)}

    def query(self, **kwargs: object) -> dict[str, object]:
        with self._lock:
            self.operations.append("query")
            self.requests.append(dict(kwargs))
            response = self.queries.get(str(kwargs["TableName"]))
            if response is None:
                return {"Items": []}
            return dict(response)

    def scan(self, **_kwargs: object) -> dict[str, object]:
        self.operations.append("scan")
        raise AssertionError("scan reached the read client")


def _text(attribute: object) -> str:
    assert isinstance(attribute, dict)
    value = attribute["S"]
    assert isinstance(value, str)
    return value


def _item_key(key: object) -> tuple[str, str]:
    assert isinstance(key, dict)
    return _text(key["pk"]), _text(key["sk"])


def _decode(attribute: object) -> object:
    assert isinstance(attribute, dict)
    if "S" in attribute:
        return attribute["S"]
    if "N" in attribute:
        return int(str(attribute["N"]))
    if "BOOL" in attribute:
        return attribute["BOOL"]
    raise AssertionError(attribute)


def _condition_holds(
    current: dict[str, dict[str, object]] | None,
    expression: str,
    names: dict[object, object],
    values: dict[object, object],
) -> bool:
    for part in expression.split(" AND "):
        clause = part.strip()
        if clause.startswith("attribute_not_exists(") and clause.endswith(")"):
            attribute = names[clause[len("attribute_not_exists(") : -1]]
            if current is not None and attribute in current and "NULL" not in current[attribute]:
                return False
            continue
        if " <= " in clause:
            left, right = clause.split(" <= ")
            if current is None or _decode(current[str(names[left])]) > _decode(values[right]):
                return False
            continue
        if " > " in clause:
            left, right = clause.split(" > ")
            if current is None or not _decode(current[str(names[left])]) > _decode(values[right]):
                return False
            continue
        if " = " in clause:
            left, right = clause.split(" = ")
            if current is None or _decode(current[str(names[left])]) != _decode(values[right]):
                return False
            continue
        raise AssertionError(clause)
    return True


def _string(value: str) -> dict[str, str]:
    return {"S": value}


def _deployment():
    aircraft = reviewed_read_table(
        logical_resource_id="aircraft",
        physical_table_name=AIRCRAFT_TABLE,
        source_name=AIRCRAFT_SOURCE,
        source_version_attribute="source_version",
        observation_time_attribute="source_observed_at_utc",
        hash_attribute="aircraft_id",
        query_partition_attribute="callsign",
        allowed_operations=frozenset({ReadOperation.GET_ITEM}),
    )
    history = reviewed_read_table(
        logical_resource_id="historical.encounters",
        physical_table_name=HISTORY_TABLE,
        source_name=HISTORY_SOURCE,
        source_version_attribute="source_version",
        observation_time_attribute="source_observed_at_utc",
        hash_attribute="aircraft_id",
        query_partition_attribute="aircraft_id",
        sort_attribute="observed_at",
        allowed_operations=frozenset({ReadOperation.QUERY}),
    )
    return reviewed_aws_deployment(
        account_id=ACCOUNT,
        region=REGION,
        role_arn=iam_role_arn(ACCOUNT, ROLE),
        read_tables=(aircraft, history),
    )


def _describe(name: str, hash_key: str, range_key: str | None = None, status: str = "ACTIVE") -> dict[str, object]:
    schema = [{"AttributeName": hash_key, "KeyType": "HASH"}]
    if range_key is not None:
        schema.append({"AttributeName": range_key, "KeyType": "RANGE"})
    return {
        "Table": {
            "TableName": name,
            "TableStatus": status,
            "TableArn": table_arn(ACCOUNT, REGION, name),
            "KeySchema": schema,
            "GlobalSecondaryIndexes": [],
        }
    }


def _described_tables() -> tuple[dict[str, object], ...]:
    return (
        _describe(DYNAMODB_LEDGER_TABLE_NAME, "pk", "sk"),
        _describe(AIRCRAFT_TABLE, "aircraft_id"),
        _describe(HISTORY_TABLE, "aircraft_id", "observed_at"),
    )


def _caller(account: str = ACCOUNT, role: str = ROLE) -> dict[str, str]:
    return {
        "Account": account,
        "Arn": f"arn:aws:sts::{account}:assumed-role/{role}/session-a",
        "UserId": "AROAEXAMPLE:session-a",
    }


def _simulation(deployment: object, *, scan_allowed: bool = False) -> dict[str, object]:
    results = []
    for action, resource, decision in expected_iam_decisions(deployment):  # type: ignore[arg-type]
        if scan_allowed and action == "dynamodb:Scan":
            decision = "allowed"
        results.append(
            {
                "EvalActionName": action,
                "EvalResourceName": resource,
                "EvalDecision": "allowed" if decision == "allowed" else "implicitDeny",
            }
        )
    return {"EvaluationResults": results}


def _identity(**overrides: object):
    payload: dict[str, object] = {
        "account_id": ACCOUNT,
        "issuer": UPSTREAM_ISSUER,
        "subject": "operator-subject-1",
        "principal_ref": "principal-1",
        "policy_revision": "policy-rev-1",
        "master_request_id": "request-1",
        "planning_session_id": "session-1",
        "step_id": "step-1",
        "invocation_id": "invocation-1",
        "purpose": MasterPurpose.ANSWER,
        "accepted_at_utc": ACCEPTED,
        "deadline_utc": DEADLINE,
    }
    payload.update(overrides)
    return seal_reviewed_upstream_identity(**payload)


def _clock() -> GrantEvaluationClock:
    return GrantEvaluationClock(datetime(2026, 10, 27, 20, 0, tzinfo=timezone.utc))


def _envelope(**overrides: object) -> MasterExecutionGrantEnvelope:
    values: dict[str, object] = {
        "master_request_id": "request-1",
        "planning_session_id": "session-1",
        "step_id": "step-1",
        "invocation_id": "invocation-1",
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "capability": LIVE,
        "principal_ref": "principal-1",
        "policy_revision": "policy-rev-1",
        "tool_name": "get_aircraft_operational_context",
        "resource_scope": (LogicalReadOperation("aircraft", ReadOperation.GET_ITEM),),
        "region_scope": ("CA", "NV"),
        "temporal_scopes": (TemporalScope.CURRENT,),
        "historical_window": None,
        "decision_target_scope": None,
        "accepted_at_utc": ACCEPTED,
        "deadline_utc": DEADLINE,
        "expires_at_utc": EXPIRES,
        "idempotency_key": "idem-1",
        "max_model_turns": 1,
        "max_tool_calls": 1,
        "correlation_id": "correlation-1",
    }
    values.update(overrides)
    return MasterExecutionGrantEnvelope(**values)  # type: ignore[arg-type]


def _historical_envelope() -> MasterExecutionGrantEnvelope:
    return _envelope(
        master_request_id="request-hist",
        planning_session_id="session-hist",
        step_id="step-hist",
        invocation_id="invocation-hist",
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        capability=HISTORICAL,
        tool_name="list_historical_encounters",
        resource_scope=(LogicalReadOperation("historical.encounters", ReadOperation.QUERY),),
        temporal_scopes=(TemporalScope.HISTORICAL,),
        historical_window=(HIST_START, HIST_END),
        idempotency_key="idem-hist",
        correlation_id="correlation-hist",
        max_model_turns=1,
        max_tool_calls=1,
    )


def _open(monkeypatch: pytest.MonkeyPatch | None = None):
    if monkeypatch is not None:
        monkeypatch.delenv(LIVE_NETWORK_GATE_ENV, raising=False)
    deployment = _deployment()
    review, port = open_reviewed_aws_execution(
        deployment,
        caller_identity=_caller(),
        described_tables=_described_tables(),
        iam_simulation=_simulation(deployment),
        ledger_client=LedgerFake(),
        read_client=ReadFake(),
        issuer=LocalHarnessGrantIssuer(_clock()),
        now_utc=NOW,
        token_source=Sequence(),
    )
    assert review.authorization is not None
    assert review.refusal is None
    assert review.data_plane_invoked is False
    assert port is not None
    return deployment, review, port


def _issue(port, envelope: MasterExecutionGrantEnvelope):
    principal = port._issuer.seal_principal(envelope.principal_ref)
    grant = port._issuer.issue(port._issuer.prepare(envelope, principal))
    return grant, principal


def _consume(port, envelope=None, identity=None, grant=None, principal=None, **overrides: object):
    envelope = _envelope() if envelope is None else envelope
    if identity is None or identity.invocation_id != envelope.invocation_id:
        identity = _identity(
            master_request_id=envelope.master_request_id,
            planning_session_id=envelope.planning_session_id,
            step_id=envelope.step_id,
            invocation_id=envelope.invocation_id,
            accepted_at_utc=envelope.accepted_at_utc,
            deadline_utc=envelope.deadline_utc,
            principal_ref=envelope.principal_ref,
            policy_revision=envelope.policy_revision,
            purpose=envelope.purpose,
        )
    if grant is None or principal is None:
        grant, principal = _issue(port, envelope)
    values: dict[str, object] = {
        "envelope": envelope,
        "principal": principal,
        "identity": identity,
        "owner_id": "worker-a",
        "now_utc": NOW,
        "lease_seconds": 60,
    }
    values.update(overrides)
    result = port.consume_signed_grant(grant, **values)  # type: ignore[arg-type]
    return grant, principal, result


def _host(**overrides: object):
    payload: dict[str, object] = {
        "principal_ref": "principal-1",
        "policy_revision": "policy-rev-1",
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "capability": LIVE,
        "population_entity_ids": frozenset({"AC1"}),
        "region_state_codes": frozenset({"CA", "NV"}),
        "observation_utc": OBSERVED,
        "evaluation_utc": EVALUATED,
    }
    payload.update(overrides)
    return bind_local_test_host(**payload)  # type: ignore[arg-type]


def _historical_host():
    return _host(
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        capability=HISTORICAL,
        historical_interval_id="hist-interval-1",
        historical_start_utc=HIST_START,
        historical_end_utc=HIST_END,
    )


def _request(envelope: MasterExecutionGrantEnvelope, **overrides: object) -> UntrustedReadRequest:
    payload: dict[str, object] = {
        "master_request_id": envelope.master_request_id,
        "planning_session_id": envelope.planning_session_id,
        "step_id": envelope.step_id,
        "invocation_id": envelope.invocation_id,
        "specialist": envelope.specialist,
        "purpose": envelope.purpose,
        "capability": envelope.capability,
        "tool_name": envelope.tool_name,
        "tool_call_id": "tool-1",
        "resource_id": envelope.resource_scope[0].resource_id,
        "operation": envelope.resource_scope[0].operation,
        "requested_arguments": (("aircraft_id", "AC1"),),
        "correlation_id": envelope.correlation_id,
    }
    payload.update(overrides)
    return UntrustedReadRequest(**payload)  # type: ignore[arg-type]


def _read(port, result, envelope, request, host, **overrides: object):
    attempt = result.consume.attempt
    assert attempt is not None
    values: dict[str, object] = {
        "owner_id": attempt.owner_id,
        "attempt_token": attempt.attempt_token,
        "fence_epoch": attempt.fence_epoch,
        "grant_nonce": attempt.grant_nonce,
        "tool_call_id": "tool-1",
        "action_id": "read-1",
    }
    values.update(overrides)
    return port.execute_bounded_read(request, host, **values)  # type: ignore[arg-type]


def test_closed_flags_contract_and_live_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LIVE_NETWORK_GATE_ENV, raising=False)
    assert SPRINT3D_PRODUCTION_EXECUTION_ENABLED is False
    assert SPRINT3D_LIVE_NETWORK_TESTS_ENABLED is False
    assert SPRINT3D_DISTRIBUTED_EXACTLY_ONCE is False
    assert SPRINT3D_LEDGER_TRANSACTION_IS_NATIVE_EXECUTION is False
    assert PRODUCTION_GRANT_ISSUANCE_AVAILABLE is False
    assert PRODUCTION_READ_EXECUTOR_ENABLED is False
    assert LIVE_AWS_OPERATION_PERMITTED is False
    assert DURABLE_ADAPTER_TYPE_IS_VERIFIED_AWS_PROVENANCE is False
    assert TTL_DELETION_IS_CORRECTNESS is False
    assert SPRINT3C_DISTRIBUTED_EXACTLY_ONCE is False
    assert live_network_gate_open() is False
    monkeypatch.setenv(LIVE_NETWORK_GATE_ENV, "yes")
    assert live_network_gate_open() is False
    monkeypatch.setenv(LIVE_NETWORK_GATE_ENV, LIVE_NETWORK_GATE_VALUE)
    assert live_network_gate_open() is True
    monkeypatch.delenv(LIVE_NETWORK_GATE_ENV)
    deployment = _deployment()
    _review, port = _open(monkeypatch)[1:]
    assert port is not None
    with pytest.raises(ContractValidationError, match="live_aws_network_not_reviewed"):
        build_boto3_dynamodb_client(port._authorization, region=REGION)
    with pytest.raises(ContractValidationError, match="live_aws_network_not_reviewed"):
        collect_live_control_plane(deployment)
    with pytest.raises(ContractValidationError, match="production_execution_closed"):
        open_production_aws_execution()
    with pytest.raises(ContractValidationError, match="production_read_executor_not_enabled"):
        open_production_executor()
    contract = real_client_integration_contract()
    assert contract["live_tests_enabled_constant"] is False
    assert contract["ledger"]["consume_api"] == "TransactWriteItems"
    assert contract["ledger"]["exactly_once_native_execution"] is False
    assert contract["ledger"]["conditional_conflict_retried_as_new_start"] is False
    assert contract["read"]["apis"] == ("GetItem", "Query")
    assert contract["read"]["exclusive_start_key_accepted"] is False
    assert contract["control_plane"] == {
        "sts": "GetCallerIdentity",
        "dynamodb": "DescribeTable",
        "iam": "SimulatePrincipalPolicy",
        "data_plane_during_review": False,
    }
    assert "transact_write_is_not_exactly_once_native_execution" in live_aws_blockers()
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Import):
            assert all(alias.name != "boto3" for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module not in {"boto3", "botocore", "wilvor_ai.live_ops_specialist"}
    assert "import boto3" in MODULE.read_text(encoding="utf-8")


def test_control_plane_fails_closed_without_touching_the_data_plane() -> None:
    deployment = _deployment()
    ledger = LedgerFake()
    reader = ReadFake()
    issuer = LocalHarnessGrantIssuer(_clock())
    cases = (
        (None, _described_tables(), _simulation(deployment), AwsExecutionRefusal.UPSTREAM_IDENTITY_MISSING),
        (_caller(), None, _simulation(deployment), AwsExecutionRefusal.TABLE_CONFIGURATION_MISSING),
        (_caller(), _described_tables(), None, AwsExecutionRefusal.IAM_AUTHORITY_MISSING),
        (_caller(account="999999999999"), _described_tables(), _simulation(deployment), AwsExecutionRefusal.ACCOUNT_MISMATCH),
        (_caller(role="other-role"), _described_tables(), _simulation(deployment), AwsExecutionRefusal.ACCOUNT_MISMATCH),
    )
    for identity, tables, simulation, refusal in cases:
        review, port = open_reviewed_aws_execution(
            deployment,
            caller_identity=identity,
            described_tables=tables,
            iam_simulation=simulation,
            ledger_client=ledger,
            read_client=reader,
            issuer=issuer,
            now_utc=NOW,
        )
        assert review.authorization is None
        assert review.refusal is refusal
        assert port is None
    wrong_key = list(_described_tables())
    wrong_key[0] = _describe(DYNAMODB_LEDGER_TABLE_NAME, "aircraft_id", "sk")
    review, port = open_reviewed_aws_execution(
        deployment,
        caller_identity=_caller(),
        described_tables=tuple(wrong_key),
        iam_simulation=_simulation(deployment),
        ledger_client=ledger,
        read_client=reader,
        issuer=issuer,
        now_utc=NOW,
    )
    assert review.refusal is AwsExecutionRefusal.TABLE_CONFIGURATION_MISMATCH
    assert port is None
    inactive = list(_described_tables())
    inactive[1] = _describe(AIRCRAFT_TABLE, "aircraft_id", status="CREATING")
    review, _port = open_reviewed_aws_execution(
        deployment,
        caller_identity=_caller(),
        described_tables=tuple(inactive),
        iam_simulation=_simulation(deployment),
        ledger_client=ledger,
        read_client=reader,
        issuer=issuer,
        now_utc=NOW,
    )
    assert review.refusal is AwsExecutionRefusal.TABLE_CONFIGURATION_MISMATCH
    review, _port = open_reviewed_aws_execution(
        deployment,
        caller_identity=_caller(),
        described_tables=_described_tables(),
        iam_simulation=_simulation(deployment, scan_allowed=True),
        ledger_client=ledger,
        read_client=reader,
        issuer=issuer,
        now_utc=NOW,
    )
    assert review.refusal is AwsExecutionRefusal.LEAST_PRIVILEGE_VIOLATION
    wildcard = _simulation(deployment)
    wildcard["EvaluationResults"].append(
        {"EvalActionName": "dynamodb:Scan", "EvalResourceName": "*", "EvalDecision": "allowed"}
    )
    review, _port = open_reviewed_aws_execution(
        deployment,
        caller_identity=_caller(),
        described_tables=_described_tables(),
        iam_simulation=wildcard,
        ledger_client=ledger,
        read_client=reader,
        issuer=issuer,
        now_utc=NOW,
    )
    assert review.refusal is AwsExecutionRefusal.LEAST_PRIVILEGE_VIOLATION
    assert ledger.operations == []
    assert reader.operations == []
    with pytest.raises(ContractValidationError, match="source_mapping_missing"):
        reviewed_aws_deployment(
            account_id=ACCOUNT,
            region=REGION,
            role_arn=iam_role_arn(ACCOUNT, ROLE),
            read_tables=(),
        )
    with pytest.raises(ContractValidationError, match="upstream_identity_not_verified"):
        _identity(issuer="eyJforgedtoken")
    with pytest.raises(ContractValidationError, match="production_principal_issuance_unavailable|upstream_identity_not_verified"):
        seal_reviewed_upstream_identity(
            account_id=ACCOUNT,
            issuer=UPSTREAM_ISSUER,
            subject="operator-subject-1",
            principal_ref="principal-1",
            policy_revision="policy-rev-1",
            master_request_id="request-1",
            planning_session_id="session-1",
            step_id="step-1",
            invocation_id="invocation-1",
            purpose=MasterPurpose.ANSWER,
            accepted_at_utc=ACCEPTED,
            deadline_utc=DEADLINE,
            production_principal=True,
        )


def test_missing_identity_or_grant_does_not_transact() -> None:
    _deployment_obj, _review, port = _open()
    ledger = port._ledger._client._inner
    envelope = _envelope()
    principal = port._issuer.seal_principal(envelope.principal_ref)
    grant = port._issuer.issue(port._issuer.prepare(envelope, principal))
    missing = port.consume_signed_grant(
        grant,
        envelope=envelope,
        principal=principal,
        identity=None,
        owner_id="worker-a",
    )
    assert missing.refusal is AwsExecutionRefusal.UPSTREAM_IDENTITY_MISSING
    assert missing.consume is None
    synthetic = IdentityBinding(
        master_request_id=envelope.master_request_id,
        planning_session_id=envelope.planning_session_id,
        step_id=envelope.step_id,
        invocation_id=envelope.invocation_id,
        principal_ref=envelope.principal_ref,
        policy_revision=envelope.policy_revision,
        purpose=envelope.purpose,
        accepted_at_utc=envelope.accepted_at_utc,
        deadline_utc=envelope.deadline_utc,
        policy_decision="DENY",
        authority_origin="NOT_ESTABLISHED",
    )
    unverified = port.consume_signed_grant(
        grant,
        envelope=envelope,
        principal=principal,
        identity=synthetic,
        owner_id="worker-a",
    )
    assert unverified.refusal is AwsExecutionRefusal.UPSTREAM_IDENTITY_NOT_VERIFIED
    mismatched = port.consume_signed_grant(
        grant,
        envelope=envelope,
        principal=principal,
        identity=_identity(invocation_id="invocation-other", master_request_id="request-other"),
        owner_id="worker-a",
    )
    assert mismatched.refusal is AwsExecutionRefusal.UPSTREAM_IDENTITY_MISMATCH
    absent = port.consume_signed_grant(
        None,
        envelope=envelope,
        principal=principal,
        identity=_identity(),
        owner_id="worker-a",
    )
    assert absent.refusal is AwsExecutionRefusal.GRANT_MISSING
    produced = port.consume_signed_grant(
        grant,
        envelope=envelope,
        principal=principal,
        identity=_identity(),
        owner_id="worker-a",
        production_requested=True,
    )
    assert produced.refusal is AwsExecutionRefusal.PRODUCTION_EXECUTION_CLOSED
    assert produced.native_exactly_once is False
    assert ledger.operations == []
    with pytest.raises(ContractValidationError, match="production_grant_issuance_closed"):
        issue_production_execution_grant(envelope, principal)
    with pytest.raises(ContractValidationError, match="ledger_not_integrated"):
        bind_production_grant_to_master_ledger(grant, port._ledger)


def test_transact_consume_is_fenced_and_not_exactly_once_execution() -> None:
    _deployment_obj, _review, port = _open()
    ledger = port._ledger._client._inner
    envelope = _envelope()
    grant, principal, first = _consume(port, envelope=envelope)
    assert first.refusal is None
    assert first.native_exactly_once is False
    assert first.ledger_write_is_native_execution is False
    assert first.control_plane_verified is True
    assert port.verified_aws_account_and_table_provenance is False
    consume = first.consume
    assert consume is not None
    assert consume.outcome is MasterExecutionLedgerOutcome.CLAIMED
    assert consume.distributed_exactly_once is False
    assert consume.verified_aws_provenance is False
    assert consume.specialist_executed is False
    attempt = consume.attempt
    assert attempt is not None
    assert attempt.state is MasterExecutionLedgerState.STARTED
    assert attempt.fence_epoch == 1
    assert attempt.ttl_absence_is_first_use is False
    assert ledger.operations.count("transact_write_items") == 1
    assert "scan" not in ledger.operations
    ledger_envelope = ledger_envelope_from_grant(envelope, principal_ref=envelope.principal_ref)
    marker = ledger.items[consumption_marker_key(attempt.grant_nonce, ledger_envelope)]
    stored = ledger.items[ledger_item_key(ledger_envelope)]
    assert "retention_expires_at_epoch" not in marker
    assert "ttl" not in marker
    assert _decode(marker["specialist_executed"]) is False
    assert _decode(marker["distributed_exactly_once"]) is False
    assert _text(stored["state"]) == "STARTED"
    assert _text(stored["grant_nonce"]) == attempt.grant_nonce
    assert "retention_expires_at_epoch" in stored
    replay = _consume(port, envelope=envelope, grant=grant, principal=principal)
    assert replay[2].consume is not None
    assert replay[2].consume.outcome is MasterExecutionLedgerOutcome.REPLAYED
    assert replay[2].consume.specialist_executed is False
    assert replay[2].native_exactly_once is False
    assert ledger.operations.count("transact_write_items") == 1
    assert _decode(stored["fence_epoch"]) == 1


def test_lease_expiry_crash_and_concurrency_do_not_open_a_second_start() -> None:
    _deployment_obj, _review, port = _open()
    ledger = port._ledger._client._inner
    envelope = _envelope()
    grant, principal, first = _consume(port, envelope=envelope)
    attempt = first.consume.attempt
    assert attempt is not None
    expired = _consume(port, envelope=envelope, grant=grant, principal=principal, now_utc=EXPIRED, owner_id="worker-b")
    assert expired[2].consume is not None
    assert expired[2].consume.outcome is MasterExecutionLedgerOutcome.REJECTED
    assert expired[2].consume.ledger_rejection is MasterExecutionLedgerRejection.NON_IDEMPOTENT_REPEAT_PROHIBITED
    assert expired[2].native_exactly_once is False
    ledger_envelope = ledger_envelope_from_grant(envelope, principal_ref=envelope.principal_ref)
    stored = ledger.items[ledger_item_key(ledger_envelope)]
    assert _text(stored["grant_nonce"]) == attempt.grant_nonce
    assert _text(stored["state"]) == "STARTED"
    assert _decode(stored["fence_epoch"]) == 1

    crashed = _open()[2]
    crashed_grant, crashed_principal, started = _consume(crashed)
    assert started.consume is not None
    assert started.consume.attempt is not None
    assert started.consume.attempt.specialist_executed is False
    again = _consume(crashed, grant=crashed_grant, principal=crashed_principal)[2]
    assert again.consume is not None
    assert again.consume.outcome is MasterExecutionLedgerOutcome.REPLAYED
    assert again.consume.specialist_executed is False

    shared = _open()[2]
    shared_grant, shared_principal = _issue(shared, _envelope())
    barrier = threading.Barrier(8)
    results = []
    errors: list[BaseException] = []
    guard = threading.Lock()

    def worker(index: int) -> None:
        try:
            barrier.wait()
            item = _consume(
                shared,
                grant=shared_grant,
                principal=shared_principal,
                owner_id=f"worker-{index}",
            )[2]
            with guard:
                results.append(item)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    claimed = [
        item
        for item in results
        if item.consume is not None and item.consume.outcome is MasterExecutionLedgerOutcome.CLAIMED
    ]
    others = [item for item in results if item not in claimed]
    assert len(claimed) == 1
    assert len(others) == 7
    assert all(
        item.consume is not None
        and item.consume.outcome is MasterExecutionLedgerOutcome.REJECTED
        and item.consume.ledger_rejection is MasterExecutionLedgerRejection.CLAIM_HELD
        for item in others
    )
    assert shared._ledger._client._inner.operations.count("transact_write_items") == 1
    assert all(item.native_exactly_once is False for item in results)
    assert all(item.consume is not None and item.consume.specialist_executed is False for item in results)


def test_throttle_retries_once_and_then_fails_closed() -> None:
    deployment = _deployment()
    ledger = LedgerFake()
    ledger.throttle_remaining = 1
    _review, port = open_reviewed_aws_execution(
        deployment,
        caller_identity=_caller(),
        described_tables=_described_tables(),
        iam_simulation=_simulation(deployment),
        ledger_client=ledger,
        read_client=ReadFake(),
        issuer=LocalHarnessGrantIssuer(_clock()),
        now_utc=NOW,
        token_source=Sequence(),
    )
    assert port is not None
    result = _consume(port)[2]
    assert result.consume is not None
    assert result.consume.outcome is MasterExecutionLedgerOutcome.CLAIMED
    assert ledger.operations.count("transact_write_items") == 2
    assert result.native_exactly_once is False
    blocked = LedgerFake()
    blocked.throttle_remaining = 5
    _review, blocked_port = open_reviewed_aws_execution(
        deployment,
        caller_identity=_caller(),
        described_tables=_described_tables(),
        iam_simulation=_simulation(deployment),
        ledger_client=blocked,
        read_client=ReadFake(),
        issuer=LocalHarnessGrantIssuer(_clock()),
        now_utc=NOW,
        token_source=Sequence(),
    )
    assert blocked_port is not None
    with pytest.raises(LedgerStoreUnavailable):
        _consume(blocked_port)
    assert blocked.items == {}
    assert blocked.operations.count("transact_write_items") == 3


def test_ledger_wrapper_rejects_scan_and_ttl_on_the_marker() -> None:
    _deployment_obj, review, _port = _open()
    fake = LedgerFake()
    wrapper = AuthorizedLedgerDynamoClient(fake, review.authorization)
    with pytest.raises(ContractValidationError, match="scan_not_permitted"):
        wrapper.scan(TableName=DYNAMODB_LEDGER_TABLE_NAME)
    with pytest.raises(ContractValidationError, match="delete_not_permitted"):
        wrapper.delete_item(TableName=DYNAMODB_LEDGER_TABLE_NAME)
    with pytest.raises(ContractValidationError, match="arbitrary_sql"):
        wrapper.execute_statement(Statement="SELECT * FROM ledger")
    marker = {
        "pk": {"S": "consume-v1#nonce"},
        "sk": {"S": "principal-1#session-1#request-1#invocation-1#ANSWER"},
        "retention_expires_at_epoch": {"N": "1"},
    }
    attempt = {
        "pk": {"S": "ledger-v1#principal-1#session-1#request-1"},
        "sk": {"S": "invocation-1#ANSWER"},
    }
    with pytest.raises(ContractValidationError, match="consumption_marker_must_not_have_ttl"):
        wrapper.transact_write_items(
            TransactItems=[
                {"Put": {"TableName": DYNAMODB_LEDGER_TABLE_NAME, "Item": attempt, "ConditionExpression": "attribute_not_exists(#pk) AND attribute_not_exists(#sk)", "ExpressionAttributeNames": {"#pk": "pk", "#sk": "sk"}}},
                {"Put": {"TableName": DYNAMODB_LEDGER_TABLE_NAME, "Item": marker, "ConditionExpression": "attribute_not_exists(#pk) AND attribute_not_exists(#sk)", "ExpressionAttributeNames": {"#pk": "pk", "#sk": "sk"}}},
            ]
        )
    assert fake.operations == []


def test_get_item_uses_the_reviewed_table_and_server_clocks() -> None:
    _deployment_obj, _review, port = _open()
    reader = port._read_inner
    reader.items[(AIRCRAFT_TABLE, "AC1")] = {
        "aircraft_id": _string("AC1"),
        "source_version": _string("v7"),
        "source_observed_at_utc": _string(SOURCE_OBSERVED),
        "observed_at_utc": _string(DECOY_OBSERVED),
        "version": _string("decoy-version"),
    }
    envelope = _envelope()
    _grant, _principal, consumed = _consume(port, envelope=envelope)
    result = _read(
        port,
        consumed,
        envelope,
        _request(envelope),
        _host(),
        location_hint="California",
    )
    assert result.refusal is None
    assert result.data_plane_invoked is True
    assert result.live_aws_network_invoked is False
    assert result.verified_zero is False
    assert result.page_empty is False
    assert result.page_complete is True
    assert result.population_complete is False
    assert result.sigmet_polygon_intersection_established is False
    assert result.approved_source_adapter is False
    assert result.source_authenticity_established is False
    assert result.native_exactly_once is False
    receipt = result.receipt
    assert receipt is not None
    assert receipt.source_name == AIRCRAFT_SOURCE
    assert receipt.source_version == "v7"
    assert receipt.source_reported_observed_at_utc == SOURCE_OBSERVED
    assert receipt.source_reported_observed_at_utc != DEADLINE
    assert receipt.source_reported_observed_at_utc != NOW
    assert receipt.query_id is None
    assert receipt.record_ids == ("AC1",)
    assert receipt.has_more is False
    assert receipt.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert result.evaluation_time_utc == NOW
    assert result.accepted_at_utc == ACCEPTED
    assert result.deadline_utc == DEADLINE
    assert reader.operations == ["get_item"]
    request = reader.requests[0]
    assert request["TableName"] == AIRCRAFT_TABLE
    assert request["ConsistentRead"] is True
    assert request["Key"] == {"aircraft_id": {"S": "AC1"}}
    duplicate = _read(port, consumed, envelope, _request(envelope), _host())
    assert duplicate.receipt is not None
    assert duplicate.receipt.record_ids == ("AC1",)
    assert reader.operations == ["get_item"]
    denied = _read(
        port,
        consumed,
        envelope,
        _request(envelope, tool_call_id="tool-2"),
        _host(),
        tool_call_id="tool-2",
        action_id="read-2",
    )
    assert denied.refusal is AwsExecutionRefusal.BUDGET_EXHAUSTED
    assert reader.operations == ["get_item"]
    with pytest.raises(ContractValidationError):
        promote_effective_scope_to_execution_grant(result.receipt)  # type: ignore[arg-type]


def test_query_reports_incomplete_pages_and_ignores_forged_query_identity() -> None:
    _deployment_obj, _review, port = _open()
    reader = port._read_inner
    reader.queries[HISTORY_TABLE] = {
        "Items": [
            {
                "record_id": _string("enc-1"),
                "aircraft_id": _string("AC1"),
                "source_version": _string("enc-v1"),
                "source_observed_at_utc": _string(SOURCE_OBSERVED),
                "observed_at_utc": _string(DECOY_OBSERVED),
            }
        ],
        "LastEvaluatedKey": {"record_id": _string("secret-page-token")},
        "query_id": "forged-query-id",
    }
    envelope = _historical_envelope()
    _grant, _principal, consumed = _consume(port, envelope=envelope, identity=_identity(
        master_request_id="request-hist",
        planning_session_id="session-hist",
        step_id="step-hist",
        invocation_id="invocation-hist",
    ))
    widened = _read(
        port,
        consumed,
        envelope,
        _request(
            envelope,
            tool_call_id="tool-wide",
            requested_arguments=(
                ("aircraft_id", "AC1"),
                ("start_utc", EIGHT_DAY_START),
                ("end_utc", HIST_END),
            ),
        ),
        _historical_host(),
        tool_call_id="tool-wide",
        action_id="read-wide",
    )
    assert widened.refusal is AwsExecutionRefusal.SCOPE_DENIED
    assert reader.operations == []
    limited = _read(
        port,
        consumed,
        envelope,
        _request(
            envelope,
            tool_call_id="tool-limit",
            requested_arguments=(("aircraft_id", "AC1"), ("start_utc", HIST_START), ("end_utc", HIST_END), ("limit", 10000)),
        ),
        _historical_host(),
        tool_call_id="tool-limit",
        action_id="read-limit",
    )
    assert limited.refusal is AwsExecutionRefusal.PAGINATION_LIMIT_EXCEEDED
    assert reader.operations == []
    request = _request(
        envelope,
        requested_arguments=(
            ("aircraft_id", "AC1"),
            ("start_utc", HIST_START),
            ("end_utc", HIST_END),
        ),
    )
    result = _read(port, consumed, envelope, request, _historical_host(), location_hint="California")
    assert result.refusal is None
    assert result.page_complete is False
    assert result.population_complete is False
    assert result.verified_zero is False
    assert result.sigmet_polygon_intersection_established is False
    receipt = result.receipt
    assert receipt is not None
    assert receipt.operation is ReadOperation.QUERY
    assert receipt.has_more is True
    assert receipt.truncated is True
    assert receipt.partial_pagination is True
    assert receipt.source_name == HISTORY_SOURCE
    assert receipt.source_version == "enc-v1"
    assert receipt.source_reported_observed_at_utc == SOURCE_OBSERVED
    assert receipt.rows_returned == 1
    plan = plan_server_owned_aws_read(
        "historical.encounters",
        ReadOperation.QUERY,
        {"aircraft_id": "AC1", "start_utc": HIST_START, "end_utc": HIST_END, "limit": HISTORICAL_LIST_MAX},
    )
    assert receipt.query_id == plan.query_id
    assert receipt.query_id != "forged-query-id"
    assert reader.operations == ["query"]
    sent = reader.requests[0]
    assert sent["TableName"] == HISTORY_TABLE
    assert sent["Limit"] == HISTORICAL_LIST_MAX
    assert "ExclusiveStartKey" not in sent
    assert "FilterExpression" not in sent
    assert "secret-page-token" not in repr(result.observation)
    assert result.observation is not None
    assert result.observation["authentic_source_read"] is False


def test_reads_reject_scan_sql_writes_caller_scope_and_stale_fences() -> None:
    _deployment_obj, _review, port = _open()
    reader = port._read_inner
    envelope = _envelope()
    _grant, _principal, consumed = _consume(port, envelope=envelope)
    attempt = consumed.consume.attempt
    assert attempt is not None
    host = _host()
    cases = (
        ({"operation": ReadOperation.SCAN, "tool_call_id": "tool-scan"}, AwsExecutionRefusal.SCAN_NOT_PERMITTED),
        (
            {"tool_call_id": "tool-sql", "requested_arguments": (("sql", "select * from aircraft"),)},
            AwsExecutionRefusal.ARBITRARY_SQL,
        ),
        (
            {"tool_call_id": "tool-arn", "resource_id": "arn:aws:dynamodb:us-east-1:123456789012:table/aircraft"},
            AwsExecutionRefusal.ARBITRARY_TABLE,
        ),
        ({"tool_call_id": "tool-wild", "resource_id": "*"}, AwsExecutionRefusal.ARBITRARY_TABLE),
        ({"tool_call_id": "tool-write", "tool_name": "put_item"}, AwsExecutionRefusal.WRITE_NOT_PERMITTED),
        ({"tool_call_id": "tool-scope", "caller_scope": {"region": "US"}}, AwsExecutionRefusal.CALLER_SCOPE_NOT_AUTHORITY),
        (
            {"tool_call_id": "tool-deadline", "caller_deadline_utc": "2026-10-27T23:00:00Z"},
            AwsExecutionRefusal.CALLER_DEADLINE_NOT_AUTHORITY,
        ),
        ({"tool_call_id": "tool-budget", "caller_max_tool_calls": 100}, AwsExecutionRefusal.CALLER_BUDGET_NOT_AUTHORITY),
    )
    for overrides, refusal in cases:
        tool_call_id = str(overrides["tool_call_id"])
        outcome = _read(
            port,
            consumed,
            envelope,
            _request(envelope, **overrides),
            host,
            tool_call_id=tool_call_id,
            action_id=tool_call_id,
        )
        assert outcome.refusal is refusal
        assert outcome.data_plane_invoked is False
        assert outcome.verified_zero is False
    stale = _read(
        port,
        consumed,
        envelope,
        _request(envelope, tool_call_id="tool-stale"),
        host,
        tool_call_id="tool-stale",
        action_id="read-stale",
        fence_epoch=attempt.fence_epoch + 1,
    )
    assert stale.refusal is AwsExecutionRefusal.FENCE_NOT_CURRENT
    assert reader.operations == []
    unread = port.execute_bounded_read(
        _request(_envelope(invocation_id="invocation-absent", idempotency_key="idem-absent", correlation_id="correlation-absent")),
        host,
        owner_id=attempt.owner_id,
        attempt_token=attempt.attempt_token,
        fence_epoch=attempt.fence_epoch,
        grant_nonce=attempt.grant_nonce,
        tool_call_id="tool-absent",
        action_id="read-absent",
    )
    assert unread.refusal is AwsExecutionRefusal.GRANT_MISSING
    port.advance_seconds(3600)
    late = _read(
        port,
        consumed,
        envelope,
        _request(envelope, tool_call_id="tool-late"),
        host,
        tool_call_id="tool-late",
        action_id="read-late",
    )
    assert late.refusal is AwsExecutionRefusal.DEADLINE_EXPIRED
    assert reader.operations == []


def test_empty_page_is_not_a_verified_zero_or_low_risk() -> None:
    _deployment_obj, _review, port = _open()
    envelope = _envelope(invocation_id="invocation-empty", idempotency_key="idem-empty", correlation_id="correlation-empty")
    _grant, _principal, consumed = _consume(port, envelope=envelope)
    result = _read(
        port,
        consumed,
        envelope,
        _request(envelope, requested_arguments=(("aircraft_id", "AC1"),)),
        _host(),
    )
    assert result.refusal is None
    assert result.page_empty is True
    assert result.page_complete is False
    assert result.verified_zero is False
    assert result.population_complete is False
    receipt = result.receipt
    assert receipt is not None
    assert receipt.page_empty is True
    assert receipt.verified_zero_minted is False
    assert receipt.record_ids == ()
    assert receipt.source_version is None
    assert receipt.source_name == AIRCRAFT_SOURCE
    assert receipt.source_reported_observed_at_utc is None
    assert result.source_observed_at_utc != DEADLINE


def test_missing_declared_source_attributes_stay_absent() -> None:
    _deployment_obj, _review, port = _open()
    reader = port._read_inner
    reader.items[(AIRCRAFT_TABLE, "AC1")] = {
        "aircraft_id": _string("AC1"),
        "version": _string("decoy-version"),
        "observed_at_utc": _string(DECOY_OBSERVED),
    }
    envelope = _envelope()
    _grant, _principal, consumed = _consume(port, envelope=envelope)
    result = _read(port, consumed, envelope, _request(envelope), _host())
    assert result.refusal is None
    receipt = result.receipt
    assert receipt is not None
    assert receipt.source_version is None
    assert receipt.source_reported_observed_at_utc is None
    assert receipt.source_name == AIRCRAFT_SOURCE
    assert receipt.record_ids == ("AC1",)
