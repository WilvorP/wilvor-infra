"""Sprint 3C durable grant consume. Local success is not exactly-once execution."""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError, TemporalScope
from wilvor_ai.master_application_principal import PrincipalRecordKind
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_execution_grants import (
    DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE,
    HMAC_PREVENTS_REPLAY,
    PRODUCTION_GRANT_ISSUANCE_AVAILABLE,
    GrantEvaluationClock,
    GrantRejectionReason,
    LocalHarnessGrantIssuer,
    LogicalReadOperation,
    MasterExecutionGrantEnvelope,
    bind_production_grant_to_master_ledger,
    issue_production_execution_grant,
    parse_execution_grant,
)
from wilvor_ai.master_execution_ledger import (
    CONDITIONAL_PUT_ABSENT,
    DURABLE_ADAPTER_TYPE_IS_VERIFIED_AWS_PROVENANCE,
    DYNAMODB_LEDGER_TABLE_NAME,
    GRANT_NONCE_ROTATES_ON_TAKEOVER,
    TTL_DELETION_IS_CORRECTNESS,
    DynamoDbMasterExecutionLedgerStore,
    LedgerStoreUnavailable,
    MasterExecutionLedger,
    MasterExecutionLedgerOutcome,
    MasterExecutionLedgerRejection,
    MasterExecutionLedgerState,
    ledger_item_key,
)
from wilvor_ai.master_host_grant_ledger_integration import required_atomic_single_use_interface
from wilvor_ai.master_read_resource_guard import ReadOperation, server_read_argument_names
from wilvor_ai.master_routing import MasterRouteExecutionAuthority
from wilvor_ai.sprint3c_grant_ledger import (
    SPRINT3C_DISTRIBUTED_EXACTLY_ONCE,
    SPRINT3C_PRODUCTION_GRANT_LEDGER_ENABLED,
    DurableConsumeResult,
    Sprint3CDurableGrantLedger,
    agent_a_identity_fixture,
    consumption_marker_key,
    ledger_envelope_from_grant,
    signed_grant_nonce,
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
        self.put_expressions: list[str] = []
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
            return self._put_locked(kwargs)

    def transact_write_items(self, **kwargs: object) -> dict[str, object]:
        with self._lock:
            self.operations.append("transact_write_items")
            if self.fail:
                raise RuntimeError("unavailable")
            items = kwargs["TransactItems"]
            assert isinstance(items, list) and items
            planned: list[tuple[tuple[str, str], dict[str, dict[str, object]]]] = []
            for entry in items:
                assert isinstance(entry, dict)
                assert "Delete" not in entry
                assert "Update" not in entry
                assert set(entry) == {"Put"}
                put = entry["Put"]
                assert put["TableName"] == DYNAMODB_LEDGER_TABLE_NAME
                expression = str(put["ConditionExpression"])
                self.transact_expressions.append(expression)
                item = put["Item"]
                assert isinstance(item, dict)
                key = (_text(item["pk"]), _text(item["sk"]))
                names = put["ExpressionAttributeNames"]
                values = put.get("ExpressionAttributeValues", {})
                current = self.items.get(key)
                if not _condition_holds(current, expression, names, values):
                    raise TransactionCanceledException()
                planned.append((key, dict(item)))
            for key, item in planned:
                self.items[key] = item
            return {}

    def scan(self, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("scan")

    def query(self, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("query")

    def delete_item(self, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("delete_item")

    def update_item(self, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("update_item")

    def simulate_ttl_of_attempt(self, partition_key: str, sort_key: str) -> None:
        with self._lock:
            key = (partition_key, sort_key)
            item = self.items[key]
            assert "retention_expires_at_epoch" in item
            del self.items[key]

    def _put_locked(self, kwargs: dict[str, object]) -> dict[str, object]:
        self.operations.append("put_item")
        self.put_expressions.append(str(kwargs["ConditionExpression"]))
        assert kwargs["TableName"] == DYNAMODB_LEDGER_TABLE_NAME
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


def _harness() -> tuple[Sprint3CDurableGrantLedger, LocalHarnessGrantIssuer, FakeDynamoClient, MasterExecutionGrantEnvelope, object]:
    client = FakeDynamoClient()
    issuer = LocalHarnessGrantIssuer(_clock())
    host = Sprint3CDurableGrantLedger(issuer=issuer, client=client, token_source=Sequence())
    envelope = _envelope()
    principal = issuer.seal_principal(envelope.principal_ref)
    return host, issuer, client, envelope, principal


def _issue(issuer: LocalHarnessGrantIssuer, envelope: MasterExecutionGrantEnvelope, principal: object):
    return issuer.issue(issuer.prepare(envelope, principal))


def _consume(host: Sprint3CDurableGrantLedger, grant, envelope, principal, identity=None, **overrides: object):
    values: dict[str, object] = {
        "envelope": envelope,
        "principal": principal,
        "identity": identity if identity is not None else agent_a_identity_fixture(),
        "owner_id": "worker-a",
        "now_utc": NOW,
        "lease_seconds": LEASE,
    }
    values.update(overrides)
    return host.consume_signed_grant(grant, **values)  # type: ignore[arg-type]


def _assert_closed_result(result: DurableConsumeResult) -> None:
    assert bool(result) is False
    assert result.distributed_exactly_once is False
    assert result.production_grant is False
    assert result.verified_aws_provenance is False
    assert result.specialist_executed is False
    assert result.durable_master_ledger_consumption_available is False
    assert result.execution_authority is MasterRouteExecutionAuthority.DENIED
    if result.attempt is not None:
        assert result.attempt.distributed_exactly_once is False
        assert result.attempt.production_grant is False
        assert result.attempt.verified_aws_provenance is False
        assert result.attempt.specialist_executed is False
        assert result.attempt.grant_nonce_rotates_on_takeover is False
        assert result.attempt.ttl_absence_is_first_use is False
        assert bool(result.attempt) is False


def test_closed_flags_and_production_stubs_stay_closed():
    host, issuer, client, envelope, principal = _harness()
    grant = _issue(issuer, envelope, principal)
    assert PRODUCTION_GRANT_ISSUANCE_AVAILABLE is False
    assert DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE is False
    assert HMAC_PREVENTS_REPLAY is False
    assert GRANT_NONCE_ROTATES_ON_TAKEOVER is False
    assert DURABLE_ADAPTER_TYPE_IS_VERIFIED_AWS_PROVENANCE is False
    assert TTL_DELETION_IS_CORRECTNESS is False
    assert SPRINT3C_PRODUCTION_GRANT_LEDGER_ENABLED is False
    assert SPRINT3C_DISTRIBUTED_EXACTLY_ONCE is False
    assert host.verified_aws_account_and_table_provenance is False
    assert host.distributed_exactly_once is False
    assert host.grant_nonce_rotates_on_takeover is False
    with pytest.raises(ContractValidationError, match="production_grant_issuance_closed"):
        issue_production_execution_grant(envelope, principal)
    with pytest.raises(ContractValidationError, match="ledger_not_integrated"):
        bind_production_grant_to_master_ledger(grant, host)
    with pytest.raises(ContractValidationError, match="ledger_not_integrated"):
        host.bind_production_grant_to_master_ledger(grant)
    with pytest.raises(ContractValidationError, match="production_grant_issuance_closed"):
        host.issue_production_execution_grant(envelope, principal)
    interface = required_atomic_single_use_interface()
    assert interface.implemented is False
    assert interface.distributed_exactly_once is False
    assert client.operations == []


def test_cp2_first_consume_returns_fence_replay_is_replayed_stale_fence_rejected():
    host, issuer, client, envelope, principal = _harness()
    identity = agent_a_identity_fixture()
    grant = _issue(issuer, envelope, principal)
    nonce = signed_grant_nonce(grant)
    first = _consume(host, grant, envelope, principal, identity=identity)
    _assert_closed_result(first)
    assert first.outcome is MasterExecutionLedgerOutcome.CLAIMED
    assert first.fresh_transition is True
    attempt = first.attempt
    assert attempt is not None
    assert attempt.grant_nonce == nonce
    assert attempt.state is MasterExecutionLedgerState.STARTED
    assert attempt.fence_epoch == 1
    assert attempt.owner_id == "worker-a"
    assert attempt.idempotency_key == "idem-1"
    assert attempt.master_request_id == identity.master_request_id
    assert attempt.planning_session_id == identity.planning_session_id
    assert attempt.invocation_id == identity.invocation_id
    assert attempt.principal_ref == identity.principal_ref
    assert attempt.policy_revision == identity.policy_revision
    assert attempt.distributed_exactly_once is False
    replay = _consume(host, grant, envelope, principal, identity=identity)
    _assert_closed_result(replay)
    assert replay.outcome is MasterExecutionLedgerOutcome.REPLAYED
    assert replay.fresh_transition is False
    assert replay.attempt is not None
    assert replay.attempt.fence_epoch == attempt.fence_epoch
    assert replay.attempt.attempt_token == attempt.attempt_token
    assert replay.attempt.grant_nonce == nonce
    stale = host.fenced_attempt_matches(
        envelope,
        owner_id="worker-a",
        attempt_token=attempt.attempt_token,
        fence_epoch=attempt.fence_epoch + 1,
        grant_nonce=nonce,
    )
    assert stale.matches_current_attempt is False
    assert stale.rejection is MasterExecutionLedgerRejection.FENCED_OUT
    assert stale.authorizes_execution is False
    current = host.fenced_attempt_matches(
        envelope,
        owner_id="worker-a",
        attempt_token=attempt.attempt_token,
        fence_epoch=attempt.fence_epoch,
        grant_nonce=nonce,
    )
    assert current.matches_current_attempt is True
    assert bool(current) is False
    assert "transact_write_items" in client.operations
    assert set(client.operations) <= {"get_item", "put_item", "transact_write_items"}
    assert client.transact_expressions
    assert all(expression == CONDITIONAL_PUT_ABSENT for expression in client.transact_expressions)


def test_signed_nonce_is_the_ledger_nonce_and_transaction_binds_identity():
    host, issuer, client, envelope, principal = _harness()
    grant = _issue(issuer, envelope, principal)
    nonce = signed_grant_nonce(grant)
    result = _consume(host, grant, envelope, principal)
    attempt = result.attempt
    assert attempt is not None
    ledger_envelope = ledger_envelope_from_grant(envelope, principal_ref=envelope.principal_ref)
    attempt_key = ledger_item_key(ledger_envelope)
    stored = client.items[attempt_key]
    assert _text(stored["grant_nonce"]) == nonce
    assert _text(stored["state"]) == "STARTED"
    assert _decode(stored["non_idempotent_started"]) is True
    assert _decode(stored["specialist_executed"]) is False
    assert "retention_expires_at_epoch" in stored
    marker_key = consumption_marker_key(nonce, ledger_envelope)
    marker = client.items[marker_key]
    assert "retention_expires_at_epoch" not in marker
    assert _decode(marker["specialist_executed"]) is False
    assert _decode(marker["distributed_exactly_once"]) is False
    assert _text(marker["payload_fingerprint"]) == attempt.payload_fingerprint
    assert _text(marker["idempotency_key"]) == envelope.idempotency_key


def test_signature_mismatch_and_mutated_envelope_fail_before_write():
    host, issuer, client, envelope, principal = _harness()
    grant = _issue(issuer, envelope, principal)
    document = grant.serialized_claim()
    document["signature"] = "0" * 64
    tampered = parse_execution_grant(document)
    denied = _consume(host, tampered, envelope, principal)
    _assert_closed_result(denied)
    assert denied.outcome is MasterExecutionLedgerOutcome.REJECTED
    assert denied.grant_reason in {GrantRejectionReason.TAMPERED, GrantRejectionReason.FORGED}
    mutated = dict(grant.serialized_claim())
    mutated["invocation_id"] = "invocation-forged"
    parsed = parse_execution_grant(mutated)
    rejected = _consume(host, parsed, envelope, principal)
    assert rejected.outcome is MasterExecutionLedgerOutcome.REJECTED
    assert rejected.grant_reason is GrantRejectionReason.TAMPERED
    assert "transact_write_items" not in client.operations
    assert client.items == {}


def test_cross_principal_and_cross_session_grants_fail():
    host, issuer, _client, envelope, principal = _harness()
    grant = _issue(issuer, envelope, principal)
    other_identity = agent_a_identity_fixture(principal_ref="principal-other")
    other_principal = issuer.seal_principal("principal-other")
    crossed = _consume(host, grant, envelope, other_principal, identity=other_identity)
    assert crossed.grant_reason is GrantRejectionReason.PRINCIPAL_MISMATCH
    session = agent_a_identity_fixture(planning_session_id="session-other")
    crossed_session = _consume(host, grant, envelope, principal, identity=session)
    assert crossed_session.grant_reason is GrantRejectionReason.SESSION_MISMATCH
    invocation = agent_a_identity_fixture(invocation_id="invocation-other")
    crossed_invocation = _consume(host, grant, envelope, principal, identity=invocation)
    assert crossed_invocation.grant_reason is GrantRejectionReason.INVOCATION_MISMATCH


def test_deny_policy_is_not_issued_as_a_grant_and_allow_is_not_a_permit():
    host, issuer, _client, envelope, principal = _harness()
    identity = agent_a_identity_fixture(policy_decision="DENY")
    grant = _issue(issuer, envelope, principal)
    consumed = _consume(host, grant, envelope, principal, identity=identity)
    assert consumed.outcome is MasterExecutionLedgerOutcome.CLAIMED
    class Policy:
        decision = "DENY"
    denied = host.issue_from_policy_evaluation(Policy())
    assert denied.grant_reason is GrantRejectionReason.EVALUATION_NOT_A_GRANT
    class Allow:
        decision = "ALLOW"
    allow = host.issue_from_policy_evaluation(Allow())
    assert allow.grant_reason is GrantRejectionReason.PRODUCTION_ISSUANCE_CLOSED
    allow_identity = agent_a_identity_fixture(policy_decision="ALLOW")
    refused = _consume(host, grant, envelope, principal, identity=allow_identity)
    assert refused.grant_reason is GrantRejectionReason.EVALUATION_NOT_A_GRANT
    production = host.authorize_production_grant_consume(grant)
    assert production.grant_reason is GrantRejectionReason.PRODUCTION_ISSUANCE_CLOSED
    flagged = _consume(host, grant, envelope, principal, production_requested=True)
    assert flagged.grant_reason is GrantRejectionReason.PRODUCTION_ISSUANCE_CLOSED
    with pytest.raises(ContractValidationError, match="production_principal_issuance_unavailable"):
        agent_a_identity_fixture(production_principal=True)
    with pytest.raises(ContractValidationError, match="verified_identity_not_established"):
        agent_a_identity_fixture(verified_identity=True)
    with pytest.raises(ContractValidationError, match="verified_identity_not_established"):
        agent_a_identity_fixture(integration_owner_binding=True)
    assert identity.record_kind == PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION.value
    assert identity.production_principal is False


def test_lease_expiry_is_not_a_new_nonce_and_not_a_new_start():
    host, issuer, client, envelope, principal = _harness()
    grant = _issue(issuer, envelope, principal)
    nonce = signed_grant_nonce(grant)
    first = _consume(host, grant, envelope, principal)
    assert first.fresh_transition is True
    expired = _consume(host, grant, envelope, principal, now_utc=EXPIRED, owner_id="worker-b")
    _assert_closed_result(expired)
    assert expired.outcome is MasterExecutionLedgerOutcome.REJECTED
    assert expired.ledger_rejection is MasterExecutionLedgerRejection.NON_IDEMPOTENT_REPEAT_PROHIBITED
    assert expired.attempt is None
    ledger_envelope = ledger_envelope_from_grant(envelope, principal_ref=envelope.principal_ref)
    stored = client.items[ledger_item_key(ledger_envelope)]
    assert _text(stored["grant_nonce"]) == nonce
    assert _text(stored["state"]) == "STARTED"
    assert _decode(stored["fence_epoch"]) == 1


def test_ttl_deletion_of_attempt_cannot_recreate_authority():
    host, issuer, client, envelope, principal = _harness()
    grant = _issue(issuer, envelope, principal)
    nonce = signed_grant_nonce(grant)
    first = _consume(host, grant, envelope, principal)
    assert first.fresh_transition is True
    ledger_envelope = ledger_envelope_from_grant(envelope, principal_ref=envelope.principal_ref)
    attempt_key = ledger_item_key(ledger_envelope)
    marker_key = consumption_marker_key(nonce, ledger_envelope)
    client.simulate_ttl_of_attempt(*attempt_key)
    assert marker_key in client.items
    assert attempt_key not in client.items
    again = _consume(host, grant, envelope, principal, owner_id="worker-b")
    _assert_closed_result(again)
    assert again.outcome is MasterExecutionLedgerOutcome.REJECTED
    assert again.ledger_rejection is MasterExecutionLedgerRejection.RETENTION_GAP
    assert again.fresh_transition is False
    check = host.fenced_attempt_matches(
        envelope,
        owner_id="worker-a",
        attempt_token=first.attempt.attempt_token,  # type: ignore[union-attr]
        fence_epoch=first.attempt.fence_epoch,  # type: ignore[union-attr]
        grant_nonce=nonce,
    )
    assert check.ttl_deleted is True
    assert check.matches_current_attempt is False
    assert check.absence_is_first_use is False
    assert attempt_key not in client.items


def test_competing_workers_start_once_and_conditional_conflict_replays():
    host, issuer, client, envelope, principal = _harness()
    grant = _issue(issuer, envelope, principal)
    barrier = threading.Barrier(8)
    results: list[DurableConsumeResult] = []
    guard = threading.Lock()
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            barrier.wait()
            result = _consume(host, grant, envelope, principal, owner_id=f"worker-{index}")
            with guard:
                results.append(result)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    claimed = [item for item in results if item.outcome is MasterExecutionLedgerOutcome.CLAIMED]
    replayed = [item for item in results if item.outcome is MasterExecutionLedgerOutcome.REPLAYED]
    rejected = [item for item in results if item.outcome is MasterExecutionLedgerOutcome.REJECTED]
    assert len(claimed) == 1
    assert claimed[0].attempt is not None
    assert claimed[0].attempt.state is MasterExecutionLedgerState.STARTED
    assert len(replayed) + len(rejected) == 7
    assert all(item.distributed_exactly_once is False for item in results)
    assert "scan" not in client.operations
    assert "delete_item" not in client.operations


def test_crash_after_ledger_write_is_not_specialist_success():
    host, issuer, _client, envelope, principal = _harness()
    grant = _issue(issuer, envelope, principal)
    started = _consume(host, grant, envelope, principal)
    attempt = started.attempt
    assert attempt is not None
    assert attempt.state is MasterExecutionLedgerState.STARTED
    assert attempt.specialist_executed is False
    replay = _consume(host, grant, envelope, principal)
    assert replay.outcome is MasterExecutionLedgerOutcome.REPLAYED
    assert replay.specialist_executed is False
    completed = host.complete_attempt(
        envelope,
        owner_id=attempt.owner_id,
        attempt_token=attempt.attempt_token,
        fence_epoch=attempt.fence_epoch,
        now_utc=NOW,
    )
    assert completed.fresh_transition is True
    assert completed.state is MasterExecutionLedgerState.COMPLETED
    assert completed.specialist_executed is False
    assert completed.specialist_execution_permitted is False


def test_cancellation_and_stale_worker_cannot_complete_after_fence_change():
    host, issuer, _client, envelope, principal = _harness()
    grant = _issue(issuer, envelope, principal)
    started = _consume(host, grant, envelope, principal)
    attempt = started.attempt
    assert attempt is not None
    cancelled = host.cancel_started_attempt(
        envelope,
        owner_id=attempt.owner_id,
        attempt_token=attempt.attempt_token,
        fence_epoch=attempt.fence_epoch,
        now_utc=NOW,
    )
    assert cancelled.fresh_transition is True
    assert cancelled.state is MasterExecutionLedgerState.FAILED
    assert cancelled.specialist_executed is False
    stale = host.complete_attempt(
        envelope,
        owner_id=attempt.owner_id,
        attempt_token=attempt.attempt_token,
        fence_epoch=attempt.fence_epoch + 1,
        now_utc=NOW,
    )
    assert stale.outcome is MasterExecutionLedgerOutcome.REJECTED
    assert stale.rejection is MasterExecutionLedgerRejection.FENCED_OUT
    again = _consume(host, grant, envelope, principal, owner_id="worker-b")
    assert again.outcome is MasterExecutionLedgerOutcome.REJECTED
    assert again.ledger_rejection is MasterExecutionLedgerRejection.ALREADY_SETTLED


def test_conditional_expressions_reject_scan_and_delete_and_transport_fails_closed():
    host, issuer, client, envelope, principal = _harness()
    grant = _issue(issuer, envelope, principal)
    _consume(host, grant, envelope, principal)
    assert set(client.operations) <= {"get_item", "put_item", "transact_write_items"}
    with pytest.raises(AssertionError, match="scan"):
        client.scan(TableName=DYNAMODB_LEDGER_TABLE_NAME)
    with pytest.raises(AssertionError, match="delete_item"):
        client.delete_item(TableName=DYNAMODB_LEDGER_TABLE_NAME)
    client.fail = True
    other = _envelope(invocation_id="invocation-outage", idempotency_key="idem-outage")
    other_principal = issuer.seal_principal(other.principal_ref)
    other_grant = _issue(issuer, other, other_principal)
    with pytest.raises(LedgerStoreUnavailable):
        _consume(
            host,
            other_grant,
            other,
            other_principal,
            identity=agent_a_identity_fixture(invocation_id="invocation-outage"),
        )
    class Incomplete:
        def get_item(self, **_kwargs: object) -> dict[str, object]:
            return {}

        def put_item(self, **_kwargs: object) -> dict[str, object]:
            return {}

    with pytest.raises(ContractValidationError, match="invalid_ledger_client"):
        Sprint3CDurableGrantLedger(issuer=issuer, client=Incomplete())


def test_ledger_signed_nonce_consume_uses_conditional_put_absent():
    client = FakeDynamoClient()
    store = DynamoDbMasterExecutionLedgerStore(client, DYNAMODB_LEDGER_TABLE_NAME)
    ledger = MasterExecutionLedger.for_durable(store, token_source=Sequence())
    issuer = LocalHarnessGrantIssuer(_clock())
    envelope = _envelope(invocation_id="invocation-ledger", idempotency_key="idem-ledger")
    principal = issuer.seal_principal(envelope.principal_ref)
    grant = _issue(issuer, envelope, principal)
    nonce = signed_grant_nonce(grant)
    ledger_envelope = ledger_envelope_from_grant(envelope, principal_ref=envelope.principal_ref)
    first = ledger.consume_signed_nonce_and_start(
        ledger_envelope,
        owner_id="worker-a",
        grant_nonce=nonce,
        now_utc=NOW,
        lease_seconds=LEASE,
    )
    assert first.outcome is MasterExecutionLedgerOutcome.CLAIMED
    assert first.grant_nonce == nonce
    assert first.state is MasterExecutionLedgerState.STARTED
    assert first.specialist_executed is False
    assert client.put_expressions[0] == CONDITIONAL_PUT_ABSENT
    replay = ledger.consume_signed_nonce_and_start(
        ledger_envelope,
        owner_id="worker-a",
        grant_nonce=nonce,
        now_utc=NOW,
        lease_seconds=LEASE,
    )
    assert replay.outcome is MasterExecutionLedgerOutcome.REPLAYED
    held = ledger.consume_signed_nonce_and_start(
        ledger_envelope,
        owner_id="worker-b",
        grant_nonce=nonce,
        now_utc=NOW,
        lease_seconds=LEASE,
    )
    assert held.rejection is MasterExecutionLedgerRejection.CLAIM_HELD
    assert set(client.operations) <= {"get_item", "put_item", "transact_write_items"}


def test_missing_signer_and_argument_names_stay_server_owned():
    host, issuer, _client, envelope, principal = _harness()
    grant = _issue(issuer, envelope, principal)
    names = server_read_argument_names(MasterSpecialist.LIVE_OPS, "get_aircraft_operational_context")
    assert "aircraft_id" in names
    with pytest.raises(ContractValidationError, match="scan_not_permitted"):
        LogicalReadOperation("aircraft", ReadOperation.SCAN)
    with pytest.raises(ContractValidationError, match="invalid_grant_issuer"):
        Sprint3CDurableGrantLedger(issuer=object(), client=FakeDynamoClient())
    parsed = parse_execution_grant(grant.serialized_claim())
    assert parsed.establishes_authority is False
    assert parsed.production_grant is False
