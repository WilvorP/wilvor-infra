"""Sprint 4 gated ledger admission. Local success is not exactly-once execution."""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError, TemporalScope
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_execution_grants import (
    DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE,
    PRODUCTION_GRANT_ISSUANCE_AVAILABLE,
    GrantEvaluationClock,
    LocalHarnessGrantIssuer,
    LogicalReadOperation,
    MasterExecutionGrantEnvelope,
)
from wilvor_ai.master_execution_ledger import (
    CONDITIONAL_PUT_ABSENT,
    DYNAMODB_LEDGER_TABLE_NAME,
    LedgerStoreUnavailable,
    MasterExecutionLedgerOutcome,
    MasterExecutionLedgerRejection,
    MasterExecutionLedgerState,
)
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.sprint3c_grant_ledger import (
    Sprint3CDurableGrantLedger,
    agent_a_identity_fixture,
    signed_grant_nonce,
)
from wilvor_ai.sprint4_security_ledger import (
    HOST_TIMEOUT_REASON,
    SPRINT4_ATOMIC_ADMISSION_IS_NATIVE_EXECUTION,
    SPRINT4_DISTRIBUTED_EXACTLY_ONCE,
    SPRINT4_LEDGER_WRITES_ENABLED,
    SPRINT4_LIVE_AWS_ENABLED,
    Sprint4AdmissionKind,
    Sprint4SecurityLedgerPort,
    Sprint4TimeoutClass,
    classify_timeout,
    enable_sprint4_ledger_writes,
    execute_live_ledger_write,
    ledger_admission_contract,
    ledger_envelope_keys,
)


ACCEPTED = "2026-10-27T19:00:00Z"
NOW = "2026-10-27T20:00:00Z"
EXPIRED = "2026-10-27T20:01:00Z"
EXPIRES = "2026-10-27T21:00:00Z"
DEADLINE = "2026-10-27T21:00:00Z"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
LEASE = 60


class Sequence:
    def __init__(self) -> None:
        self._n = 0
        self._lock = threading.Lock()

    def __call__(self) -> str:
        with self._lock:
            self._n += 1
            return f"tokenseq{self._n:04d}"


class ConditionalCheckFailedException(Exception):
    pass


class TransactionCanceledException(Exception):
    def __init__(self) -> None:
        super().__init__("transaction canceled")
        self.response = {"Error": {"Code": "TransactionCanceledException"}}


class FakeDynamoClient:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, dict[str, object]]] = {}
        self.operations: list[str] = []
        self.transact_expressions: list[str] = []
        self.fail = False
        self._lock = threading.Lock()

    def get_item(self, **kwargs: object) -> dict[str, object]:
        with self._lock:
            self.operations.append("get_item")
            assert kwargs["ConsistentRead"] is True
            assert kwargs["TableName"] == DYNAMODB_LEDGER_TABLE_NAME
            if self.fail:
                raise RuntimeError("unavailable")
            key = _item_key(kwargs["Key"])
            item = self.items.get(key)
            if item is None:
                return {}
            return {"Item": dict(item)}

    def put_item(self, **kwargs: object) -> dict[str, object]:
        with self._lock:
            self.operations.append("put_item")
            if self.fail:
                raise RuntimeError("unavailable")
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
            if self.fail:
                raise RuntimeError("unavailable")
            items = kwargs["TransactItems"]
            assert isinstance(items, list) and len(items) == 2
            planned: list[tuple[tuple[str, str], dict[str, dict[str, object]]]] = []
            prefixes: list[str] = []
            for entry in items:
                assert set(entry) == {"Put"}
                put = entry["Put"]
                assert put["TableName"] == DYNAMODB_LEDGER_TABLE_NAME
                expression = str(put["ConditionExpression"])
                self.transact_expressions.append(expression)
                assert expression == CONDITIONAL_PUT_ABSENT
                item = put["Item"]
                assert isinstance(item, dict)
                key = (_text(item["pk"]), _text(item["sk"]))
                current = self.items.get(key)
                names = put["ExpressionAttributeNames"]
                values = put.get("ExpressionAttributeValues", {})
                if not _condition_holds(current, expression, names, values):
                    raise TransactionCanceledException()
                planned.append((key, dict(item)))
                prefixes.append(_text(item["pk"]).split("#", 1)[0])
            assert sorted(prefixes) == ["consume-v1", "ledger-v1"]
            for key, item in planned:
                if _text(item["pk"]).startswith("consume-v1#"):
                    assert "retention_expires_at_epoch" not in item
                    assert "ttl" not in item
                self.items[key] = item
            return {}

    def scan(self, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("scan")

    def query(self, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("query")

    def delete_item(self, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("delete_item")

    def simulate_ttl_of_attempt(self, partition_key: str, sort_key: str) -> None:
        with self._lock:
            key = (partition_key, sort_key)
            item = self.items[key]
            assert "retention_expires_at_epoch" in item
            del self.items[key]


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
    if "NULL" in attribute:
        return None
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
        "region_scope": ("CA",),
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


def _port() -> tuple[Sprint4SecurityLedgerPort, LocalHarnessGrantIssuer, FakeDynamoClient, MasterExecutionGrantEnvelope, object]:
    client = FakeDynamoClient()
    issuer = LocalHarnessGrantIssuer(_clock())
    ledger = Sprint3CDurableGrantLedger(issuer=issuer, client=client, token_source=Sequence())
    envelope = _envelope()
    principal = issuer.seal_principal(envelope.principal_ref)
    return Sprint4SecurityLedgerPort(ledger), issuer, client, envelope, principal


def _issue(issuer: LocalHarnessGrantIssuer, envelope: MasterExecutionGrantEnvelope, principal: object):
    return issuer.issue(issuer.prepare(envelope, principal))


def _admit(port: Sprint4SecurityLedgerPort, grant, envelope, principal, **overrides: object):
    values: dict[str, object] = {
        "envelope": envelope,
        "principal": principal,
        "identity": agent_a_identity_fixture(),
        "owner_id": "worker-a",
        "now_utc": NOW,
        "lease_seconds": LEASE,
    }
    values.update(overrides)
    return port.admit_started(grant, **values)  # type: ignore[arg-type]


def test_closed_flags_and_live_write_stubs_stay_closed():
    port, issuer, client, envelope, principal = _port()
    grant = _issue(issuer, envelope, principal)
    assert SPRINT4_LEDGER_WRITES_ENABLED is False
    assert SPRINT4_LIVE_AWS_ENABLED is False
    assert SPRINT4_DISTRIBUTED_EXACTLY_ONCE is False
    assert SPRINT4_ATOMIC_ADMISSION_IS_NATIVE_EXECUTION is False
    assert PRODUCTION_GRANT_ISSUANCE_AVAILABLE is False
    assert DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE is False
    assert port.ledger_writes_enabled is False
    assert port.live_aws_enabled is False
    assert port.distributed_exactly_once is False
    assert port.atomic_admission_is_native_execution is False
    assert port.verified_aws_account_and_table_provenance is False
    interface = port.required_atomic_single_use()
    assert interface.implemented is False
    assert interface.distributed_exactly_once is False
    with pytest.raises(ContractValidationError, match="sprint4_ledger_writes_disabled"):
        enable_sprint4_ledger_writes(grant)
    with pytest.raises(ContractValidationError, match="sprint4_live_aws_not_executed"):
        execute_live_ledger_write(grant)
    with pytest.raises(ContractValidationError, match="production_grant_issuance_closed"):
        port.refuse_production_issue(envelope, principal)
    with pytest.raises(ContractValidationError, match="ledger_not_integrated"):
        port.refuse_production_bind(grant)
    with pytest.raises(ContractValidationError, match="production_grant_issuance_closed"):
        _admit(port, grant, envelope, principal, production_requested=True)
    with pytest.raises(ContractValidationError, match="sprint4_live_aws_not_executed"):
        _admit(port, grant, envelope, principal, live_aws_requested=True)
    assert client.operations == []
    contract = ledger_admission_contract()
    assert contract["consume_api"] == "TransactWriteItems"
    assert contract["attempt_state"] == "STARTED"
    assert contract["marker_ttl"] is False
    assert contract["atomic_admission_is_native_execution"] is False
    assert contract["distributed_exactly_once"] is False
    assert contract["ledger_writes_enabled"] is False


def test_atomic_started_admission_writes_marker_and_does_not_claim_exactly_once():
    port, issuer, client, envelope, principal = _port()
    grant = _issue(issuer, envelope, principal)
    nonce = signed_grant_nonce(grant)
    result = _admit(port, grant, envelope, principal)
    assert bool(result) is False
    assert result.kind is Sprint4AdmissionKind.STARTED
    assert result.distributed_exactly_once is False
    assert result.atomic_admission_is_native_execution is False
    assert result.live_aws_invoked is False
    assert result.ledger_writes_enabled is False
    consume = result.consume
    assert consume is not None
    assert consume.outcome is MasterExecutionLedgerOutcome.CLAIMED
    assert consume.fresh_transition is True
    attempt = consume.attempt
    assert attempt is not None
    assert attempt.state is MasterExecutionLedgerState.STARTED
    assert attempt.grant_nonce == nonce
    assert attempt.fence_epoch == 1
    assert attempt.distributed_exactly_once is False
    assert attempt.specialist_executed is False
    marker = result.marker
    assert marker is not None
    assert marker.present is True
    assert marker.has_ttl_attribute is False
    assert marker.absence_is_first_use is False
    assert marker.grant_nonce == nonce
    assert marker.partition_key == f"consume-v1#{nonce}"
    audit = result.audit
    assert audit is not None
    assert audit.aws_write_performed is False
    assert audit.live_aws_invoked is False
    assert audit.native_exactly_once is False
    assert audit.table_name == DYNAMODB_LEDGER_TABLE_NAME
    fence = result.fence
    assert fence is not None
    assert fence.matches_current_attempt is True
    assert fence.authorizes_execution is False
    assert "transact_write_items" in client.operations
    assert client.transact_expressions == [CONDITIONAL_PUT_ABSENT, CONDITIONAL_PUT_ABSENT]


def test_replay_of_same_fingerprint_is_replayed_and_does_not_start_again():
    port, issuer, client, envelope, principal = _port()
    grant = _issue(issuer, envelope, principal)
    first = _admit(port, grant, envelope, principal)
    transacts = client.operations.count("transact_write_items")
    replay = _admit(port, grant, envelope, principal)
    assert first.kind is Sprint4AdmissionKind.STARTED
    assert replay.kind is Sprint4AdmissionKind.REPLAYED
    assert replay.consume is not None
    assert replay.consume.outcome is MasterExecutionLedgerOutcome.REPLAYED
    assert replay.consume.fresh_transition is False
    assert replay.consume.attempt is not None
    assert replay.consume.attempt.fence_epoch == first.consume.attempt.fence_epoch  # type: ignore[union-attr]
    assert replay.consume.attempt.grant_nonce == first.consume.attempt.grant_nonce  # type: ignore[union-attr]
    assert replay.marker is not None and replay.marker.present is True
    assert client.operations.count("transact_write_items") == transacts
    stale = port.fenced_attempt_matches(
        envelope,
        owner_id="worker-a",
        attempt_token="stale-token-xx",
        fence_epoch=1,
        grant_nonce=first.consume.attempt.grant_nonce,  # type: ignore[union-attr]
    )
    assert stale.matches_current_attempt is False
    assert stale.rejection is MasterExecutionLedgerRejection.FENCED_OUT
    assert stale.authorizes_execution is False


def test_lease_expiry_after_started_is_not_a_new_start_or_new_nonce():
    port, issuer, client, envelope, principal = _port()
    grant = _issue(issuer, envelope, principal)
    nonce = signed_grant_nonce(grant)
    first = _admit(port, grant, envelope, principal)
    expired = _admit(port, grant, envelope, principal, now_utc=EXPIRED, owner_id="worker-b")
    assert first.kind is Sprint4AdmissionKind.STARTED
    assert expired.kind is Sprint4AdmissionKind.REJECTED
    assert expired.timeout_class is Sprint4TimeoutClass.LEASE_EXPIRED_TAKEOVER_PROHIBITED
    assert expired.consume is not None
    assert expired.consume.ledger_rejection is MasterExecutionLedgerRejection.NON_IDEMPOTENT_REPEAT_PROHIBITED
    started = [
        item
        for item in client.items.values()
        if item.get("state", {}).get("S") == "STARTED"
    ]
    assert len(started) == 1
    assert started[0]["grant_nonce"]["S"] == nonce
    assert started[0]["fence_epoch"]["N"] == "1"


def test_deadline_before_start_is_a_timeout_class_and_does_not_write():
    port, issuer, client, envelope, principal = _port()
    grant = _issue(issuer, envelope, principal)
    result = _admit(port, grant, envelope, principal, now_utc=DEADLINE)
    assert result.kind is Sprint4AdmissionKind.REJECTED
    assert result.timeout_class is Sprint4TimeoutClass.DEADLINE_BEFORE_START
    assert result.consume is not None
    assert result.consume.ledger_rejection is MasterExecutionLedgerRejection.DEADLINE_EXPIRED
    assert result.marker is not None
    assert result.marker.present is False
    assert result.marker.absence_is_first_use is False
    assert "transact_write_items" not in client.operations
    classified = classify_timeout(
        result.consume,
        now_utc=DEADLINE,
        deadline_utc=DEADLINE,
    )
    assert classified is Sprint4TimeoutClass.DEADLINE_BEFORE_START


def test_host_timeout_and_failure_stay_on_the_current_fence():
    port, issuer, _client, envelope, principal = _port()
    grant = _issue(issuer, envelope, principal)
    started = _admit(port, grant, envelope, principal)
    attempt = started.consume.attempt  # type: ignore[union-attr]
    assert attempt is not None
    timed = port.record_timeout(
        envelope,
        owner_id=attempt.owner_id,
        attempt_token=attempt.attempt_token,
        fence_epoch=attempt.fence_epoch,
        now_utc=EXPIRED,
    )
    assert timed.kind is Sprint4AdmissionKind.TIMED_OUT
    assert timed.timeout_class is Sprint4TimeoutClass.HOST_TIMEOUT_RECORDED
    assert timed.terminal is not None
    assert timed.terminal.state is MasterExecutionLedgerState.FAILED
    assert timed.terminal.specialist_executed is False
    replay_timeout = port.record_timeout(
        envelope,
        owner_id=attempt.owner_id,
        attempt_token=attempt.attempt_token,
        fence_epoch=attempt.fence_epoch,
        now_utc="2026-10-27T20:02:00Z",
    )
    assert replay_timeout.kind is Sprint4AdmissionKind.TIMED_OUT
    stale = port.record_failure(
        envelope,
        owner_id=attempt.owner_id,
        attempt_token="other-token-xx",
        fence_epoch=attempt.fence_epoch,
        now_utc=EXPIRED,
        reason="worker_crash",
    )
    assert stale.kind is Sprint4AdmissionKind.REJECTED
    assert stale.terminal is not None
    assert stale.terminal.rejection is MasterExecutionLedgerRejection.FENCED_OUT


def test_host_cancel_and_complete_are_not_specialist_success():
    port, issuer, _client, envelope, principal = _port()
    grant = _issue(issuer, envelope, principal)
    started = _admit(port, grant, envelope, principal)
    attempt = started.consume.attempt  # type: ignore[union-attr]
    assert attempt is not None
    cancelled = port.record_host_cancel(
        envelope,
        owner_id=attempt.owner_id,
        attempt_token=attempt.attempt_token,
        fence_epoch=attempt.fence_epoch,
        now_utc=EXPIRED,
    )
    assert cancelled.kind is Sprint4AdmissionKind.CANCELLED
    assert cancelled.terminal is not None
    assert cancelled.terminal.state is MasterExecutionLedgerState.FAILED
    assert cancelled.terminal.specialist_executed is False

    port_b, issuer_b, _client_b, envelope_b, principal_b = _port()
    grant_b = _issue(issuer_b, envelope_b, principal_b)
    started_b = _admit(port_b, grant_b, envelope_b, principal_b)
    attempt_b = started_b.consume.attempt  # type: ignore[union-attr]
    assert attempt_b is not None
    completed = port_b.complete_attempt(
        envelope_b,
        owner_id=attempt_b.owner_id,
        attempt_token=attempt_b.attempt_token,
        fence_epoch=attempt_b.fence_epoch,
        now_utc=EXPIRED,
    )
    assert completed.kind is Sprint4AdmissionKind.COMPLETED
    assert completed.terminal is not None
    assert completed.terminal.state is MasterExecutionLedgerState.COMPLETED
    assert completed.terminal.specialist_executed is False
    assert completed.terminal.dispatch_permitted is False


def test_store_unavailable_does_not_mint_a_started_admission():
    port, issuer, client, envelope, principal = _port()
    grant = _issue(issuer, envelope, principal)
    client.fail = True
    result = _admit(port, grant, envelope, principal)
    assert result.kind is Sprint4AdmissionKind.STORE_UNAVAILABLE
    assert result.store_unavailable is True
    assert result.consume is None
    assert result.audit is None
    assert bool(result) is False


def test_ttl_gap_after_attempt_deletion_is_not_first_use():
    port, issuer, client, envelope, principal = _port()
    grant = _issue(issuer, envelope, principal)
    first = _admit(port, grant, envelope, principal)
    pk, sk = ledger_envelope_keys(envelope)
    client.simulate_ttl_of_attempt(pk, sk)
    gap = _admit(port, grant, envelope, principal)
    assert first.kind is Sprint4AdmissionKind.STARTED
    assert gap.kind is Sprint4AdmissionKind.REJECTED
    assert gap.consume is not None
    assert gap.consume.ledger_rejection is MasterExecutionLedgerRejection.RETENTION_GAP
    assert gap.marker is not None
    assert gap.marker.present is True
    assert gap.marker.absence_is_first_use is False
    assert gap.marker.ttl_deletion_is_correctness is False


def test_cross_principal_grant_is_rejected():
    port, issuer, client, envelope, principal = _port()
    grant = _issue(issuer, envelope, principal)
    foreign = agent_a_identity_fixture(principal_ref="principal-other")
    result = _admit(port, grant, envelope, principal, identity=foreign)
    assert result.kind is Sprint4AdmissionKind.REJECTED
    assert "transact_write_items" not in client.operations
    assert HOST_TIMEOUT_REASON == "host_timeout"
