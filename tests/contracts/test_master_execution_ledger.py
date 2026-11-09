"""Atomic execution-ledger claims. No specialist, network, or AWS call."""

from __future__ import annotations

import os
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_execution_ledger import (
    CONDITIONAL_PUT_ABSENT,
    CONDITIONAL_REPLACE_EXACT,
    CONDITIONAL_REPLACE_EXPIRED,
    CONDITIONAL_REPLACE_EXPIRED_REPEATABLE,
    CONDITIONAL_REPLACE_UNEXPIRED,
    DYNAMODB_LEDGER_SCHEMA,
    DYNAMODB_LEDGER_TABLE_NAME,
    GRANT_NONCE_IS_PRODUCTION_GRANT,
    IN_MEMORY_IS_PRODUCTION_CROSS_INSTANCE_AUTHORITY,
    MAX_DECISION_STEPS,
    MAX_HISTORICAL_WINDOW,
    MAX_LEASE_SECONDS,
    MAX_ROUTE_STEPS,
    PROCESS_LOCAL_LOCK_IS_DISTRIBUTED_ATOMICITY,
    REGION_SCOPE_IS_GEOGRAPHIC_VERIFICATION,
    RETENTION_AFTER_DEADLINE,
    TTL_DELETION_IS_CORRECTNESS,
    DynamoDbMasterExecutionLedgerStore,
    InMemoryMasterExecutionLedgerBackend,
    InMemoryMasterExecutionLedgerStore,
    LedgerStoreUnavailable,
    MasterExecutionLedger,
    MasterExecutionLedgerEnvelope,
    MasterExecutionLedgerOutcome,
    MasterExecutionLedgerRejection,
    MasterExecutionLedgerState,
    conditional_replace_expression,
    ledger_item_key,
)
from wilvor_ai.master_read_resource_guard import server_read_budget
from wilvor_ai.master_routing import MAX_HISTORICAL_WINDOW as ROUTE_WINDOW


ACCEPTED = "2026-10-27T20:00:00Z"
NOW = "2026-10-27T20:00:05Z"
DEADLINE = "2026-10-27T20:05:00Z"
EXPIRED = "2026-10-27T20:00:36Z"
LEASE = 30


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


class FakeDynamoClient:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, dict[str, object]]] = {}
        self.operations: list[str] = []
        self.put_expressions: list[str] = []
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
            self.put_expressions.append(str(kwargs["ConditionExpression"]))
            assert kwargs["TableName"] == DYNAMODB_LEDGER_TABLE_NAME
            assert "ConditionExpression" in kwargs
            if self.fail:
                raise RuntimeError("unavailable")
            item = kwargs["Item"]
            assert isinstance(item, dict)
            key = (_text(item["pk"]), _text(item["sk"]))
            current = self.items.get(key)
            names = kwargs["ExpressionAttributeNames"]
            values = kwargs.get("ExpressionAttributeValues", {})
            assert isinstance(names, dict)
            assert isinstance(values, dict)
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


def _envelope(**overrides: object) -> MasterExecutionLedgerEnvelope:
    payload: dict[str, object] = {
        "master_request_id": "request-1",
        "planning_session_id": "session-1",
        "step_id": "step-1",
        "invocation_id": "invocation-1",
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "capability": AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT,
        "principal_ref": "principal-1",
        "policy_revision": "policy-1",
        "resource_scope": "live-ops-read",
        "region_scope": "region-label",
        "decision_target_scope": "none",
        "accepted_at_utc": ACCEPTED,
        "deadline_utc": DEADLINE,
        "idempotency_key": "idem-1",
        "max_model_turns": 1,
        "max_tool_calls": 1,
        "correlation_id": "correlation-1",
    }
    payload.update(overrides)
    return MasterExecutionLedgerEnvelope(**payload)  # type: ignore[arg-type]


def _ledger(
    store: object | None = None,
    *,
    durable: bool = False,
) -> tuple[MasterExecutionLedger, MasterExecutionLedgerEnvelope]:
    token_source = Sequence()
    if store is None:
        store = InMemoryMasterExecutionLedgerStore()
    if durable:
        ledger = MasterExecutionLedger.for_durable(store, token_source=token_source)
    else:
        ledger = MasterExecutionLedger(store, token_source=token_source)
    return ledger, _envelope()


def _reserve(ledger: MasterExecutionLedger, envelope: MasterExecutionLedgerEnvelope, owner: str = "worker-a", now: str = NOW):
    return ledger.reserve_once(envelope, owner_id=owner, now_utc=now, lease_seconds=LEASE)


def _start(ledger: MasterExecutionLedger, envelope: MasterExecutionLedgerEnvelope, reserved, owner: str = "worker-a", now: str = NOW):
    return ledger.mark_started(
        envelope,
        owner_id=owner,
        attempt_token=reserved.attempt_token,
        fence_epoch=reserved.fence_epoch,
        now_utc=now,
    )


def _run_if_started(started, executions: list[str], owner: str) -> None:
    if started.fresh_transition:
        assert started.specialist_execution_permitted is False
        assert started.dispatch_permitted is False
        executions.append(owner)


def _assert_closed(result) -> None:
    assert result.specialist_execution_permitted is False
    assert result.specialist_executed is False
    assert result.dispatch_permitted is False
    assert result.grant_nonce_is_production_grant is False
    assert result.production_cross_instance_authority is False
    assert result.execution_authority.value == "DENIED"
    assert result.production_authority.value == "DENIED"
    assert result.source_authenticity.value == "NOT_ESTABLISHED"


def test_closed_constants_and_documented_schema():
    assert PROCESS_LOCAL_LOCK_IS_DISTRIBUTED_ATOMICITY is False
    assert IN_MEMORY_IS_PRODUCTION_CROSS_INSTANCE_AUTHORITY is False
    assert GRANT_NONCE_IS_PRODUCTION_GRANT is False
    assert TTL_DELETION_IS_CORRECTNESS is False
    assert REGION_SCOPE_IS_GEOGRAPHIC_VERIFICATION is False
    assert MAX_ROUTE_STEPS == 4
    assert MAX_DECISION_STEPS == 1
    assert MAX_HISTORICAL_WINDOW == ROUTE_WINDOW
    assert DYNAMODB_LEDGER_SCHEMA["table_name"] == DYNAMODB_LEDGER_TABLE_NAME
    assert DYNAMODB_LEDGER_SCHEMA["provisioned_by_this_module"] is False
    assert DYNAMODB_LEDGER_SCHEMA["idempotency_key_in_primary_key"] is False
    assert DYNAMODB_LEDGER_SCHEMA["ttl_deletion_is_correctness"] is False
    assert DYNAMODB_LEDGER_SCHEMA["specialist_read_permissions_granted"] is False
    assert DYNAMODB_LEDGER_SCHEMA["conditional_put_absent"] == CONDITIONAL_PUT_ABSENT
    assert DYNAMODB_LEDGER_SCHEMA["conditional_replace_exact"] == CONDITIONAL_REPLACE_EXACT
    assert DYNAMODB_LEDGER_SCHEMA["conditional_replace_unexpired_lease"] == CONDITIONAL_REPLACE_UNEXPIRED
    assert DYNAMODB_LEDGER_SCHEMA["conditional_replace_expired_lease"] == CONDITIONAL_REPLACE_EXPIRED
    assert DYNAMODB_LEDGER_SCHEMA["conditional_replace_expired_repeatable"] == CONDITIONAL_REPLACE_EXPIRED_REPEATABLE
    assert conditional_replace_expression("any", False) == CONDITIONAL_REPLACE_EXACT
    assert conditional_replace_expression("unexpired", False) == CONDITIONAL_REPLACE_UNEXPIRED
    assert conditional_replace_expression("expired", False) == CONDITIONAL_REPLACE_EXPIRED
    assert conditional_replace_expression("expired", True) == CONDITIONAL_REPLACE_EXPIRED_REPEATABLE
    for expression in (
        CONDITIONAL_REPLACE_EXACT,
        CONDITIONAL_REPLACE_UNEXPIRED,
        CONDITIONAL_REPLACE_EXPIRED,
        CONDITIONAL_REPLACE_EXPIRED_REPEATABLE,
    ):
        for clause in ("#state", "#owner_id", "#attempt_token", "#fence_epoch", "#payload_fingerprint", "#grant_nonce"):
            assert clause in expression
    envelope = _envelope()
    partition, sort = ledger_item_key(envelope)
    assert partition == "ledger-v1#principal-1#session-1#request-1"
    assert sort == "invocation-1#ANSWER"
    assert envelope.idempotency_key not in sort


def test_fingerprint_binds_descriptive_fields_and_rejects_untrusted_shapes():
    base = _envelope()
    fingerprint = base.fingerprint
    variants = (
        replace(base, correlation_id="correlation-2"),
        replace(base, idempotency_key="idem-2"),
        replace(base, step_id="step-2"),
        replace(base, invocation_id="invocation-2"),
        replace(base, principal_ref="principal-2"),
        replace(base, planning_session_id="session-2"),
        replace(base, master_request_id="request-2"),
        replace(base, policy_revision="policy-2"),
        replace(base, resource_scope="resource-2"),
        replace(base, region_scope="region-2"),
        replace(base, decision_target_scope="target-2"),
        replace(base, purpose=MasterPurpose.DISCOVERY),
        replace(base, capability=AgentCapability.SUMMARIZE_OPERATIONAL_STATE),
        replace(base, repeat_non_idempotent_after_expiry=True),
        replace(base, max_model_turns=2),
        replace(base, max_tool_calls=2),
        replace(base, accepted_at_utc="2026-10-27T19:00:00Z"),
        replace(base, deadline_utc="2026-10-27T21:00:00Z"),
    )
    assert len({item.fingerprint for item in variants}) == len(variants)
    assert all(item.fingerprint != fingerprint for item in variants)
    with pytest.raises(ContractValidationError):
        _envelope(repeat_non_idempotent_after_expiry="true")
    with pytest.raises(ContractValidationError):
        _envelope(capability=AgentCapability.EXECUTE_REROUTES)
    with pytest.raises(ContractValidationError):
        _envelope(max_model_turns=True)
    ledger, _envelope_unused = _ledger()
    with pytest.raises(ContractValidationError):
        ledger.reserve_once({"principal_ref": "principal-1"}, owner_id="worker-a", now_utc=NOW, lease_seconds=LEASE)


def test_budget_and_historical_window_use_existing_ceilings():
    _executions, turns, tools = server_read_budget(MasterSpecialist.HISTORICAL_ANALYTICS)
    historical = _envelope(
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        capability=AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS,
        historical_start_utc="2026-10-01T00:00:00Z",
        historical_end_utc="2026-10-08T00:00:00Z",
        max_model_turns=turns,
        max_tool_calls=tools,
    )
    assert historical.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    with pytest.raises(ContractValidationError) as exceeded:
        _envelope(
            specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
            capability=AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS,
            historical_start_utc="2026-10-01T00:00:00Z",
            historical_end_utc="2026-10-08T00:00:01Z",
            max_model_turns=turns,
            max_tool_calls=tools,
        )
    assert "historical_window_exceeds_seven_days" in exceeded.value.errors
    with pytest.raises(ContractValidationError) as missing:
        _envelope(
            specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
            capability=AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS,
            max_model_turns=turns,
            max_tool_calls=tools,
        )
    assert "missing_historical_interval" in missing.value.errors
    with pytest.raises(ContractValidationError) as contradictory:
        _envelope(historical_start_utc="2026-10-01T00:00:00Z", historical_end_utc="2026-10-02T00:00:00Z")
    assert "contradictory_temporal_bounds" in contradictory.value.errors
    _live_executions, live_turns, _live_tools = server_read_budget(MasterSpecialist.LIVE_OPS)
    with pytest.raises(ContractValidationError) as turns_exceeded:
        _envelope(max_model_turns=live_turns + 1)
    assert "max_model_turns_exceeds_server_ceiling" in turns_exceeded.value.errors
    assert _live_executions >= 1
    assert _executions >= 1


def test_identical_retry_replays_one_start_and_preserves_nonce():
    ledger, envelope = _ledger()
    executions: list[str] = []
    first = _reserve(ledger, envelope)
    second = _reserve(ledger, envelope)
    assert first.outcome is MasterExecutionLedgerOutcome.CLAIMED
    assert second.outcome is MasterExecutionLedgerOutcome.REPLAYED
    assert second.attempt_token == first.attempt_token
    assert second.grant_nonce == first.grant_nonce
    assert second.fence_epoch == 1
    assert second.lease_expires_at_utc == first.lease_expires_at_utc
    assert second.correlation_id == "correlation-1"
    assert first.accepted_at_utc == ACCEPTED
    assert first.ledger_observed_at_utc == NOW
    _assert_closed(first)
    _assert_closed(second)
    started = _start(ledger, envelope, first)
    replayed_start = _start(ledger, envelope, second)
    _run_if_started(started, executions, "worker-a")
    _run_if_started(replayed_start, executions, "worker-a")
    assert started.fresh_transition is True
    assert started.state is MasterExecutionLedgerState.STARTED
    assert replayed_start.replayed is True
    assert executions == ["worker-a"]
    completed = ledger.complete(
        envelope,
        owner_id="worker-a",
        attempt_token=first.attempt_token,
        fence_epoch=1,
        now_utc="2026-10-27T20:00:20Z",
    )
    completed_again = ledger.complete(
        envelope,
        owner_id="worker-a",
        attempt_token=first.attempt_token,
        fence_epoch=1,
        now_utc="2026-10-27T20:00:21Z",
    )
    assert completed.fresh_transition is True
    assert completed_again.replayed is True
    assert completed_again.ledger_observed_at_utc == completed.ledger_observed_at_utc
    assert completed.accepted_at_utc == ACCEPTED
    assert completed.deadline_utc == DEADLINE
    assert executions == ["worker-a"]


def test_conflicting_payload_does_not_replace_the_claim():
    ledger, envelope = _ledger()
    first = _reserve(ledger, envelope)
    conflict = _reserve(ledger, replace(envelope, correlation_id="correlation-2"))
    rotated_key = _reserve(ledger, replace(envelope, idempotency_key="idem-2"))
    assert conflict.rejection is MasterExecutionLedgerRejection.CONFLICTING_DUPLICATE
    assert rotated_key.rejection is MasterExecutionLedgerRejection.CONFLICTING_DUPLICATE
    assert conflict.attempt_token is None
    assert conflict.grant_nonce is None
    replay = _reserve(ledger, envelope)
    assert replay.replayed is True
    assert replay.grant_nonce == first.grant_nonce
    assert replay.correlation_id == "correlation-1"
    flipped = _reserve(ledger, replace(envelope, repeat_non_idempotent_after_expiry=True))
    assert flipped.rejection is MasterExecutionLedgerRejection.CONFLICTING_DUPLICATE


def test_parallel_claims_run_one_specialist_transition():
    ledger, envelope = _ledger()
    executions: list[str] = []
    reserve_outcomes: list[MasterExecutionLedgerOutcome] = []
    guard = threading.Lock()
    barrier = threading.Barrier(8)
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        owner = f"worker-{index}"
        try:
            barrier.wait()
            reserved = _reserve(ledger, envelope, owner)
            with guard:
                reserve_outcomes.append(reserved.outcome)
            if reserved.outcome is not MasterExecutionLedgerOutcome.CLAIMED:
                _assert_closed(reserved)
                return
            started = _start(ledger, envelope, reserved, owner)
            if started.fresh_transition:
                with guard:
                    executions.append(owner)
                completed = ledger.complete(
                    envelope,
                    owner_id=owner,
                    attempt_token=started.attempt_token,
                    fence_epoch=started.fence_epoch,
                    now_utc=NOW,
                )
                assert completed.fresh_transition is True
                assert completed.specialist_execution_permitted is False
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert executions.__len__() == 1
    assert reserve_outcomes.count(MasterExecutionLedgerOutcome.CLAIMED) == 1
    assert set(reserve_outcomes) <= {MasterExecutionLedgerOutcome.CLAIMED, MasterExecutionLedgerOutcome.REJECTED}


def test_same_owner_parallel_retry_starts_once():
    ledger, envelope = _ledger()
    executions: list[str] = []
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait()
            reserved = _reserve(ledger, envelope)
            if reserved.outcome is MasterExecutionLedgerOutcome.REJECTED:
                return
            started = _start(ledger, envelope, reserved)
            _run_if_started(started, executions, "worker-a")
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert executions == ["worker-a"]


def test_expired_lease_takeover_fences_the_late_worker():
    ledger, envelope = _ledger()
    executions: list[str] = []
    first = _reserve(ledger, envelope, "worker-a")
    taken = _reserve(ledger, envelope, "worker-b", EXPIRED)
    assert taken.outcome is MasterExecutionLedgerOutcome.CLAIMED
    assert taken.fence_epoch == 2
    assert taken.attempt_token != first.attempt_token
    assert taken.grant_nonce == first.grant_nonce
    assert taken.correlation_id == first.correlation_id
    late_start = _start(ledger, envelope, first, "worker-a", EXPIRED)
    late_complete = ledger.complete(
        envelope,
        owner_id="worker-a",
        attempt_token=first.attempt_token,
        fence_epoch=first.fence_epoch,
        now_utc=EXPIRED,
    )
    assert late_start.rejection is MasterExecutionLedgerRejection.FENCED_OUT
    assert late_complete.rejection is MasterExecutionLedgerRejection.FENCED_OUT
    started = _start(ledger, envelope, taken, "worker-b", EXPIRED)
    _run_if_started(started, executions, "worker-b")
    stolen = ledger.complete(
        envelope,
        owner_id="worker-a",
        attempt_token=taken.attempt_token,
        fence_epoch=taken.fence_epoch,
        now_utc=EXPIRED,
    )
    assert stolen.rejection is MasterExecutionLedgerRejection.OWNER_MISMATCH
    completed = ledger.complete(
        envelope,
        owner_id="worker-b",
        attempt_token=taken.attempt_token,
        fence_epoch=taken.fence_epoch,
        now_utc=EXPIRED,
    )
    assert completed.fresh_transition is True
    assert executions == ["worker-b"]
    assert completed.specialist_execution_permitted is False


def test_started_lease_expiry_does_not_repeat_non_idempotent_work():
    ledger, envelope = _ledger()
    executions: list[str] = []
    reserved = _reserve(ledger, envelope)
    started = _start(ledger, envelope, reserved)
    _run_if_started(started, executions, "worker-a")
    blocked = _reserve(ledger, envelope, "worker-b", EXPIRED)
    assert blocked.rejection is MasterExecutionLedgerRejection.NON_IDEMPOTENT_REPEAT_PROHIBITED
    assert blocked.specialist_execution_permitted is False
    completed = ledger.complete(
        envelope,
        owner_id="worker-a",
        attempt_token=reserved.attempt_token,
        fence_epoch=reserved.fence_epoch,
        now_utc=EXPIRED,
    )
    assert completed.fresh_transition is True
    assert executions == ["worker-a"]


def test_explicit_repeat_policy_is_fixed_at_first_claim_and_fences_the_old_attempt():
    ledger, envelope = _ledger()
    repeatable = replace(envelope, repeat_non_idempotent_after_expiry=True)
    executions: list[str] = []
    reserved = _reserve(ledger, repeatable)
    started = _start(ledger, repeatable, reserved)
    _run_if_started(started, executions, "worker-a")
    taken = _reserve(ledger, repeatable, "worker-b", EXPIRED)
    assert taken.fresh_transition is True
    assert taken.fence_epoch == 2
    late = ledger.complete(
        repeatable,
        owner_id="worker-a",
        attempt_token=reserved.attempt_token,
        fence_epoch=reserved.fence_epoch,
        now_utc=EXPIRED,
    )
    assert late.rejection is MasterExecutionLedgerRejection.FENCED_OUT
    restarted = _start(ledger, repeatable, taken, "worker-b", EXPIRED)
    _run_if_started(restarted, executions, "worker-b")
    assert executions == ["worker-a", "worker-b"]
    denied = _envelope(invocation_id="invocation-policy")
    held = _reserve(ledger, denied, "worker-a")
    _start(ledger, denied, held)
    assert _reserve(
        ledger,
        replace(denied, repeat_non_idempotent_after_expiry=True),
        "worker-b",
        EXPIRED,
    ).rejection is MasterExecutionLedgerRejection.CONFLICTING_DUPLICATE


def test_cancellation_race_allows_one_transition():
    for index in range(20):
        ledger, _base = _ledger()
        envelope = _envelope(invocation_id=f"invocation-cancel-{index}")
        reserved = _reserve(ledger, envelope)
        barrier = threading.Barrier(2)
        outcomes = []
        executions: list[str] = []
        errors: list[BaseException] = []
        guard = threading.Lock()

        def starter() -> None:
            try:
                barrier.wait()
                started = _start(ledger, envelope, reserved)
                with guard:
                    outcomes.append(started)
                    if started.fresh_transition:
                        executions.append("worker-a")
            except BaseException as exc:  # pragma: no cover - reported below
                errors.append(exc)

        def canceller() -> None:
            try:
                barrier.wait()
                cancelled = ledger.cancel(
                    envelope,
                    owner_id="worker-a",
                    attempt_token=reserved.attempt_token,
                    fence_epoch=reserved.fence_epoch,
                    now_utc=NOW,
                )
                with guard:
                    outcomes.append(cancelled)
            except BaseException as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=starter), threading.Thread(target=canceller)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        claimed = [item for item in outcomes if item.outcome is MasterExecutionLedgerOutcome.CLAIMED]
        rejected = [item for item in outcomes if item.outcome is MasterExecutionLedgerOutcome.REJECTED]
        assert len(claimed) == 1
        assert len(rejected) == 1
        assert rejected[0].rejection is MasterExecutionLedgerRejection.INVALID_TRANSITION
        if claimed[0].state is MasterExecutionLedgerState.STARTED:
            assert executions == ["worker-a"]
        else:
            assert claimed[0].state is MasterExecutionLedgerState.CANCELLED
            assert executions == []
            assert claimed[0].specialist_execution_permitted is False


def test_cancel_replay_and_fail_reason_conflict():
    ledger, envelope = _ledger()
    reserved = _reserve(ledger, envelope)
    cancelled = ledger.cancel(
        envelope,
        owner_id="worker-a",
        attempt_token=reserved.attempt_token,
        fence_epoch=reserved.fence_epoch,
        now_utc=NOW,
    )
    again = ledger.cancel(
        envelope,
        owner_id="worker-a",
        attempt_token=reserved.attempt_token,
        fence_epoch=reserved.fence_epoch,
        now_utc=NOW,
    )
    assert cancelled.fresh_transition is True
    assert again.replayed is True
    assert _start(ledger, envelope, reserved).rejection is MasterExecutionLedgerRejection.INVALID_TRANSITION
    other, other_envelope = _ledger()
    held = _reserve(other, other_envelope)
    started = _start(other, other_envelope, held)
    failed = other.fail(
        other_envelope,
        owner_id="worker-a",
        attempt_token=held.attempt_token,
        fence_epoch=held.fence_epoch,
        now_utc=NOW,
        reason="provider_failed",
    )
    replay = other.fail(
        other_envelope,
        owner_id="worker-a",
        attempt_token=held.attempt_token,
        fence_epoch=held.fence_epoch,
        now_utc=NOW,
        reason="provider_failed",
    )
    conflict = other.fail(
        other_envelope,
        owner_id="worker-a",
        attempt_token=held.attempt_token,
        fence_epoch=held.fence_epoch,
        now_utc=NOW,
        reason="different_reason",
    )
    assert started.fresh_transition is True
    assert failed.fresh_transition is True
    assert replay.replayed is True
    assert conflict.rejection is MasterExecutionLedgerRejection.CONFLICTING_DUPLICATE


def test_shared_backend_restart_replays_without_becoming_authority():
    backend = InMemoryMasterExecutionLedgerBackend()
    first = MasterExecutionLedger(InMemoryMasterExecutionLedgerStore(backend), token_source=Sequence())
    envelope = _envelope()
    executions: list[str] = []
    reserved = _reserve(first, envelope)
    second = MasterExecutionLedger(InMemoryMasterExecutionLedgerStore(backend), token_source=Sequence())
    replay = _reserve(second, envelope)
    assert replay.replayed is True
    assert replay.attempt_token == reserved.attempt_token
    assert replay.grant_nonce == reserved.grant_nonce
    assert replay.store_is_durable_adapter is False
    assert second.production_cross_instance_authority is False
    assert backend.production_cross_instance_authority is False
    started = _start(second, envelope, replay)
    again = _start(first, envelope, reserved)
    _run_if_started(started, executions, "worker-a")
    _run_if_started(again, executions, "worker-a")
    assert executions == ["worker-a"]
    isolated = MasterExecutionLedger(InMemoryMasterExecutionLedgerStore(), token_source=Sequence())
    separate = _reserve(isolated, envelope)
    assert separate.outcome is MasterExecutionLedgerOutcome.CLAIMED
    assert separate.store_is_durable_adapter is False
    assert separate.specialist_execution_permitted is False
    with pytest.raises(ContractValidationError) as refused:
        MasterExecutionLedger.for_durable(InMemoryMasterExecutionLedgerStore())
    assert "in_memory_not_cross_instance_authority" in refused.value.errors


def test_retention_elapsed_keeps_the_record_and_ttl_deletion_is_not_permission():
    backend = InMemoryMasterExecutionLedgerBackend()
    store = InMemoryMasterExecutionLedgerStore(backend)
    ledger = MasterExecutionLedger(store, token_source=Sequence())
    envelope = _envelope()
    reserved = _reserve(ledger, envelope)
    _start(ledger, envelope, reserved)
    ledger.complete(
        envelope,
        owner_id="worker-a",
        attempt_token=reserved.attempt_token,
        fence_epoch=reserved.fence_epoch,
        now_utc=NOW,
    )
    deadline = datetime.strptime(DEADLINE, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    later = (deadline + RETENTION_AFTER_DEADLINE).strftime("%Y-%m-%dT%H:%M:%SZ")
    observation = ledger.retention_observation(envelope, now_utc=later)
    assert observation.retention_elapsed is True
    assert observation.record_present is True
    assert observation.ttl_deletion_is_correctness is False
    assert observation.absence_is_repeat_permission is False
    assert observation.specialist_execution_permitted is False
    replay = _reserve(ledger, envelope, now=later)
    assert replay.replayed is True
    assert replay.grant_nonce == reserved.grant_nonce
    store.simulate_asynchronous_ttl_deletion(envelope.partition_key, envelope.sort_key)
    gap_view = ledger.retention_observation(envelope, now_utc=later)
    assert gap_view.record_present is False
    assert gap_view.ttl_deleted is True
    assert gap_view.absence_is_repeat_permission is False
    gap = _reserve(ledger, envelope, "worker-b", later)
    assert gap.rejection is MasterExecutionLedgerRejection.RETENTION_GAP
    assert gap.specialist_execution_permitted is False
    assert not hasattr(DynamoDbMasterExecutionLedgerStore, "simulate_asynchronous_ttl_deletion")


def test_store_unavailability_fail_closed_and_environment_is_ignored():
    os.environ["WILVOR_TEST_MODE"] = "1"
    os.environ["WILVOR_EXECUTION_LEDGER_DURABLE"] = "true"
    backend = InMemoryMasterExecutionLedgerBackend()
    ledger = MasterExecutionLedger(InMemoryMasterExecutionLedgerStore(backend), token_source=Sequence())
    envelope = _envelope()
    backend.unavailable = True
    with pytest.raises(LedgerStoreUnavailable):
        _reserve(ledger, envelope)
    backend.unavailable = False
    backend.fail_writes = True
    with pytest.raises(LedgerStoreUnavailable):
        _reserve(ledger, envelope)
    backend.fail_writes = False
    reserved = _reserve(ledger, envelope)
    assert reserved.outcome is MasterExecutionLedgerOutcome.CLAIMED
    assert reserved.fence_epoch == 1
    assert reserved.store_is_durable_adapter is False
    backend.fail_writes = True
    with pytest.raises(LedgerStoreUnavailable):
        _start(ledger, envelope, reserved)
    backend.fail_writes = False
    started = _start(ledger, envelope, reserved)
    assert started.fresh_transition is True
    assert _start(ledger, envelope, reserved).replayed is True
    with pytest.raises(ContractValidationError):
        MasterExecutionLedger.for_durable(InMemoryMasterExecutionLedgerStore())
    with pytest.raises(ContractValidationError):
        _reserve(ledger, envelope, now=NOW, owner="worker a")


def test_deadline_blocks_a_new_start_and_still_records_a_started_completion():
    ledger, envelope = _ledger()
    near_deadline = _envelope(deadline_utc="2026-10-27T20:01:00Z")
    reserved = _reserve(ledger, near_deadline)
    started = _start(ledger, near_deadline, reserved)
    assert started.fresh_transition is True
    completed = ledger.complete(
        near_deadline,
        owner_id="worker-a",
        attempt_token=reserved.attempt_token,
        fence_epoch=reserved.fence_epoch,
        now_utc="2026-10-27T20:01:05Z",
    )
    assert completed.fresh_transition is True
    assert completed.deadline_utc == "2026-10-27T20:01:00Z"
    blocked_ledger, _base = _ledger()
    held = _reserve(blocked_ledger, near_deadline)
    blocked = _start(blocked_ledger, near_deadline, held, now="2026-10-27T20:01:05Z")
    assert blocked.rejection is MasterExecutionLedgerRejection.DEADLINE_EXPIRED
    replay = blocked_ledger.reserve_once(
        near_deadline,
        owner_id="worker-a",
        now_utc=NOW,
        lease_seconds=MAX_LEASE_SECONDS,
    )
    assert replay.replayed is True
    assert replay.lease_expires_at_utc == held.lease_expires_at_utc
    with pytest.raises(ContractValidationError) as exceeded:
        blocked_ledger.reserve_once(
            replace(near_deadline, invocation_id="invocation-lease"),
            owner_id="worker-a",
            now_utc=NOW,
            lease_seconds=MAX_LEASE_SECONDS,
        )
    assert "lease_exceeds_deadline" in exceeded.value.errors


def test_grant_nonce_is_unpredictable_and_not_caller_selected():
    ledger = MasterExecutionLedger(InMemoryMasterExecutionLedgerStore())
    first = _reserve(ledger, _envelope())
    second = _reserve(ledger, _envelope(invocation_id="invocation-2"))
    assert first.grant_nonce != second.grant_nonce
    assert first.attempt_token != first.grant_nonce
    assert len(first.grant_nonce) >= 32
    assert first.grant_nonce != first.correlation_id
    assert first.grant_nonce != "idem-1"
    replay = _reserve(ledger, _envelope())
    assert replay.grant_nonce == first.grant_nonce
    assert first.grant_nonce_is_production_grant is False


def test_dynamodb_adapter_uses_conditional_writes_and_survives_a_new_store_object():
    client = FakeDynamoClient()
    assert client.operations == []
    with pytest.raises(ContractValidationError):
        DynamoDbMasterExecutionLedgerStore(client, "sigmet_hazards")
    with pytest.raises(ContractValidationError):
        DynamoDbMasterExecutionLedgerStore(object(), DYNAMODB_LEDGER_TABLE_NAME)
    store = DynamoDbMasterExecutionLedgerStore(client, DYNAMODB_LEDGER_TABLE_NAME)
    assert client.operations == []
    ledger = MasterExecutionLedger.for_durable(store, token_source=Sequence())
    envelope = _envelope()
    executions: list[str] = []
    reserved = _reserve(ledger, envelope)
    assert client.put_expressions[0] == CONDITIONAL_PUT_ABSENT
    restarted = MasterExecutionLedger.for_durable(
        DynamoDbMasterExecutionLedgerStore(client, DYNAMODB_LEDGER_TABLE_NAME),
        token_source=Sequence(),
    )
    replay = _reserve(restarted, envelope)
    assert replay.replayed is True
    assert replay.attempt_token == reserved.attempt_token
    assert replay.store_is_durable_adapter is True
    assert replay.production_cross_instance_authority is False
    puts_before_replay_followup = client.operations.count("put_item")
    assert _reserve(restarted, envelope).replayed is True
    assert client.operations.count("put_item") == puts_before_replay_followup
    taken = _reserve(restarted, envelope, "worker-b", EXPIRED)
    assert taken.fresh_transition is True
    assert taken.fence_epoch == 2
    assert client.put_expressions[-1] == CONDITIONAL_REPLACE_EXPIRED
    assert _start(restarted, envelope, reserved, "worker-a", EXPIRED).rejection is MasterExecutionLedgerRejection.FENCED_OUT
    started = _start(restarted, envelope, taken, "worker-b", EXPIRED)
    _run_if_started(started, executions, "worker-b")
    assert CONDITIONAL_REPLACE_UNEXPIRED in client.put_expressions
    assert executions == ["worker-b"]
    assert set(client.operations) <= {"get_item", "put_item"}
    key = ledger_item_key(envelope)
    client.items[key]["production_authority"] = {"S": "PRODUCTION"}
    with pytest.raises(LedgerStoreUnavailable):
        _reserve(restarted, envelope, "worker-b", EXPIRED)
    client.fail = True
    fresh = FakeDynamoClient()
    fresh.fail = True
    outage = MasterExecutionLedger.for_durable(
        DynamoDbMasterExecutionLedgerStore(fresh, DYNAMODB_LEDGER_TABLE_NAME),
        token_source=Sequence(),
    )
    with pytest.raises(LedgerStoreUnavailable):
        _reserve(outage, _envelope(invocation_id="invocation-outage"))
    assert fresh.items == {}


def test_dynamodb_parallel_claims_still_start_once():
    client = FakeDynamoClient()
    ledger = MasterExecutionLedger.for_durable(
        DynamoDbMasterExecutionLedgerStore(client, DYNAMODB_LEDGER_TABLE_NAME),
        token_source=Sequence(),
    )
    envelope = _envelope(invocation_id="invocation-dynamo-parallel")
    executions: list[str] = []
    barrier = threading.Barrier(8)
    errors: list[BaseException] = []
    guard = threading.Lock()

    def worker(index: int) -> None:
        owner = f"worker-{index}"
        try:
            barrier.wait()
            reserved = _reserve(ledger, envelope, owner)
            if reserved.outcome is not MasterExecutionLedgerOutcome.CLAIMED:
                return
            started = _start(ledger, envelope, reserved, owner)
            if started.fresh_transition:
                with guard:
                    executions.append(owner)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert executions.__len__() == 1
    assert all(expression == CONDITIONAL_PUT_ABSENT or expression.startswith("#state") for expression in client.put_expressions)
