"""Offline grant and ledger composition. Local success is not distributed exactly-once."""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError, TemporalScope
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_execution_grants import (
    DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE,
    PRODUCTION_GRANT_ISSUANCE_AVAILABLE,
    GrantEvaluationClock,
    GrantRejectionReason,
    LocalHarnessConsumptionLedger,
    LocalHarnessGrantIssuer,
    LogicalReadOperation,
    MasterExecutionGrantEnvelope,
    bind_production_grant_to_master_ledger,
    issue_production_execution_grant,
    parse_execution_grant,
)
from wilvor_ai.master_execution_ledger import (
    CONDITIONAL_REPLACE_EXPIRED,
    CONDITIONAL_REPLACE_UNEXPIRED,
    DURABLE_ADAPTER_TYPE_IS_VERIFIED_AWS_PROVENANCE,
    DYNAMODB_LEDGER_TABLE_NAME,
    GRANT_NONCE_ROTATES_ON_TAKEOVER,
    TTL_DELETION_IS_CORRECTNESS,
    DynamoDbMasterExecutionLedgerStore,
    InMemoryMasterExecutionLedgerBackend,
    InMemoryMasterExecutionLedgerStore,
    LedgerStoreUnavailable,
    MasterExecutionLedger,
    MasterExecutionLedgerEnvelope,
    MasterExecutionLedgerRejection,
    MasterExecutionLedgerState,
    conditional_replace_expression,
    ledger_item_key,
)
from wilvor_ai.master_host_grant_ledger_integration import (
    CONSUMPTION_STARTED_BOUNDARY,
    DECISION_TARGET_NOT_APPLICABLE,
    DISTRIBUTED_EXACTLY_ONCE_ACHIEVED,
    NonproductionBoundaryOutcome,
    NonproductionHostGrantLedger,
    required_atomic_single_use_interface,
)
from wilvor_ai.master_read_resource_guard import (
    ReadOperation,
    server_read_argument_names,
    server_read_resources,
)
from wilvor_ai.master_routing import MasterRouteExecutionAuthority


ACCEPTED = "2026-10-27T19:00:00Z"
NOW = "2026-10-27T20:00:00Z"
EXPIRED = "2026-10-27T20:01:00Z"
EXPIRES = "2026-10-27T21:00:00Z"
DEADLINE = "2026-10-27T21:00:00Z"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
LEASE = 60


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


def _host(envelope: MasterExecutionGrantEnvelope | None = None):
    clock = _clock()
    issuer = LocalHarnessGrantIssuer(clock)
    backend = InMemoryMasterExecutionLedgerBackend()
    store = InMemoryMasterExecutionLedgerStore(backend)
    ledger = MasterExecutionLedger(store)
    host = NonproductionHostGrantLedger(issuer=issuer, ledger=ledger)
    grant_envelope = envelope if envelope is not None else _envelope()
    principal = issuer.seal_principal(grant_envelope.principal_ref)
    return host, issuer, ledger, backend, store, grant_envelope, principal


def _reserve(host, envelope, principal, **overrides: object):
    values: dict[str, object] = {
        "owner_id": "worker-a",
        "now_utc": NOW,
        "lease_seconds": LEASE,
    }
    values.update(overrides)
    return host.reserve_aligned(envelope, principal=principal, **values)


def _start(host, reserved, principal, local, **overrides: object):
    values: dict[str, object] = {
        "owner_id": reserved.owner_id,
        "attempt_token": reserved.attempt_token,
        "fence_epoch": reserved.fence_epoch,
        "now_utc": NOW,
    }
    values.update(overrides)
    return host.consume_local_and_mark_started(
        reserved.grant,
        principal=principal,
        local_consumption_ledger=local,
        **values,
    )


def _assert_local(result: object) -> None:
    assert bool(result) is False
    assert result.local_harness_only is True  # type: ignore[attr-defined]
    assert result.durable_ledger_consumed is False  # type: ignore[attr-defined]
    assert result.distributed_exactly_once is False  # type: ignore[attr-defined]
    assert result.verified_aws_provenance is False  # type: ignore[attr-defined]
    assert result.execution_authority is MasterRouteExecutionAuthority.DENIED  # type: ignore[attr-defined]
    assert result.execution_permitted is False  # type: ignore[attr-defined]
    assert result.dispatch_permitted is False  # type: ignore[attr-defined]
    assert result.specialist_execution_permitted is False  # type: ignore[attr-defined]
    assert result.production_grant is False  # type: ignore[attr-defined]
    assert result.principal_authenticated_by_ledger is False  # type: ignore[attr-defined]
    assert result.decision_target_trusted is False  # type: ignore[attr-defined]
    assert result.hmac_prevents_replay is False  # type: ignore[attr-defined]
    assert result.grant_nonce_rotates_on_takeover is False  # type: ignore[attr-defined]
    assert result.ttl_absence_is_first_use is False  # type: ignore[attr-defined]


def _ledger_envelope(envelope: MasterExecutionGrantEnvelope, reserved) -> MasterExecutionLedgerEnvelope:
    return MasterExecutionLedgerEnvelope(
        master_request_id=envelope.master_request_id,
        planning_session_id=envelope.planning_session_id,
        step_id=envelope.step_id,
        invocation_id=envelope.invocation_id,
        specialist=envelope.specialist,
        purpose=envelope.purpose,
        capability=envelope.capability,
        principal_ref=envelope.principal_ref,
        policy_revision=envelope.policy_revision,
        resource_scope=reserved.resource_scope,
        region_scope=reserved.region_scope,
        decision_target_scope=reserved.decision_target_scope,
        accepted_at_utc=envelope.accepted_at_utc,
        deadline_utc=envelope.deadline_utc,
        idempotency_key=envelope.idempotency_key,
        max_model_turns=envelope.max_model_turns,
        max_tool_calls=envelope.max_tool_calls,
        correlation_id=envelope.correlation_id,
        repeat_non_idempotent_after_expiry=reserved.repeat_non_idempotent_after_expiry,
    )


def test_one_fresh_reservation_and_one_local_start_are_not_durable():
    host, _issuer, ledger, _backend, _store, envelope, principal = _host()
    executions: list[str] = []
    reserved = _reserve(host, envelope, principal)
    _assert_local(reserved)
    assert reserved.outcome is NonproductionBoundaryOutcome.RESERVED_LOCAL
    assert reserved.fresh_transition is True
    assert reserved.grant_newly_issued is True
    assert reserved.fence_epoch == 1
    assert reserved.state is MasterExecutionLedgerState.RESERVED
    assert reserved.repeat_non_idempotent_after_expiry is False
    assert reserved.resource_scope == "aircraft:GET_ITEM"
    assert reserved.region_scope == "CA"
    assert reserved.decision_target_scope == DECISION_TARGET_NOT_APPLICABLE
    assert reserved.policy_revision == "policy-rev-1"
    assert reserved.idempotency_key == "idem-1"
    assert reserved.deadline_utc == DEADLINE
    assert reserved.grant_nonce != reserved.ledger_grant_nonce
    assert reserved.attempt_nonce == reserved.attempt_token
    assert reserved.store_is_durable_adapter is False
    assert ledger.production_cross_instance_authority is False
    assert ledger.verified_aws_account_and_table_provenance is False
    assert host.distributed_exactly_once_achieved is False
    assert host.consumption_started_boundary == CONSUMPTION_STARTED_BOUNDARY
    local = LocalHarnessConsumptionLedger()
    started = _start(host, reserved, principal, local)
    if started.countable_local_start:
        executions.append("worker-a")
    replayed = _start(host, reserved, principal, local)
    _assert_local(started)
    _assert_local(replayed)
    assert started.outcome is NonproductionBoundaryOutcome.STARTED_LOCAL
    assert started.fresh_transition is True
    assert started.countable_local_start is True
    assert started.local_harness_consumption_recorded is True
    assert started.durable_ledger_consumed is False
    assert local.holds_nonce(reserved.grant_nonce) is True
    assert local.durable is False
    assert replayed.outcome is NonproductionBoundaryOutcome.REPLAYED_LOCAL
    assert replayed.fresh_transition is False
    assert replayed.countable_local_start is False
    assert executions == ["worker-a"]


def test_duplicate_reserve_replays_one_record():
    host, _issuer, _ledger, _backend, _store, envelope, principal = _host()
    first = _reserve(host, envelope, principal)
    second = _reserve(host, envelope, principal)
    _assert_local(second)
    assert second.outcome is NonproductionBoundaryOutcome.REPLAYED_LOCAL
    assert second.fresh_transition is False
    assert second.grant_newly_issued is False
    assert second.attempt_token == first.attempt_token
    assert second.fence_epoch == first.fence_epoch
    assert second.grant_nonce == first.grant_nonce
    assert second.ledger_grant_nonce == first.ledger_grant_nonce
    assert second.grant is first.grant


def test_conflicting_payload_does_not_replace_the_claim():
    host, _issuer, _ledger, _backend, _store, envelope, principal = _host()
    first = _reserve(host, envelope, principal)
    conflict = _reserve(host, replace(envelope, correlation_id="correlation-2"), principal)
    rotated = _reserve(host, replace(envelope, idempotency_key="idem-2"), principal)
    assert conflict.ledger_rejection is MasterExecutionLedgerRejection.CONFLICTING_DUPLICATE
    assert rotated.ledger_rejection is MasterExecutionLedgerRejection.CONFLICTING_DUPLICATE
    assert conflict.fresh_transition is False
    assert conflict.grant_newly_issued is False
    replay = _reserve(host, envelope, principal)
    assert replay.outcome is NonproductionBoundaryOutcome.REPLAYED_LOCAL
    assert replay.attempt_token == first.attempt_token
    assert replay.ledger_grant_nonce == first.ledger_grant_nonce


def test_owner_contention_while_the_lease_is_held():
    host, _issuer, _ledger, _backend, _store, envelope, principal = _host()
    first = _reserve(host, envelope, principal)
    other = _reserve(host, envelope, principal, owner_id="worker-b")
    _assert_local(other)
    assert other.ledger_rejection is MasterExecutionLedgerRejection.CLAIM_HELD
    assert other.grant_newly_issued is False
    replay = _reserve(host, envelope, principal)
    assert replay.attempt_token == first.attempt_token
    assert replay.fence_epoch == 1


def test_expired_reserved_takeover_rejects_stale_attempt_nonce_and_fence():
    host, _issuer, ledger, _backend, _store, envelope, principal = _host()
    first = _reserve(host, envelope, principal)
    taken = _reserve(host, envelope, principal, owner_id="worker-b", now_utc=EXPIRED)
    _assert_local(taken)
    assert taken.outcome is NonproductionBoundaryOutcome.RESERVED_LOCAL
    assert taken.fresh_transition is True
    assert taken.grant_newly_issued is False
    assert taken.fence_epoch == 2
    assert taken.attempt_token != first.attempt_token
    assert taken.attempt_nonce == taken.attempt_token
    assert taken.ledger_grant_nonce == first.ledger_grant_nonce
    assert taken.grant_nonce == first.grant_nonce
    assert GRANT_NONCE_ROTATES_ON_TAKEOVER is False
    assert ledger.grant_nonce_rotates_on_takeover is False
    ledger_envelope = _ledger_envelope(envelope, taken)
    stale_pairs = (
        (first.attempt_token, first.fence_epoch, taken.ledger_grant_nonce),
        (first.attempt_token, taken.fence_epoch, taken.ledger_grant_nonce),
        (taken.attempt_token, first.fence_epoch, taken.ledger_grant_nonce),
        (taken.attempt_token, taken.fence_epoch, taken.grant_nonce),
        (taken.ledger_grant_nonce, taken.fence_epoch, taken.ledger_grant_nonce),
    )
    for token, fence, nonce in stale_pairs:
        check = ledger.fenced_attempt_matches(
            ledger_envelope,
            owner_id="worker-b",
            attempt_token=token,
            fence_epoch=fence,
            grant_nonce=nonce,
        )
        assert bool(check) is False
        assert check.matches_current_attempt is False
        assert check.authorizes_execution is False
        assert check.rejection is MasterExecutionLedgerRejection.FENCED_OUT
    current = ledger.fenced_attempt_matches(
        ledger_envelope,
        owner_id="worker-b",
        attempt_token=taken.attempt_token,
        fence_epoch=taken.fence_epoch,
        grant_nonce=taken.ledger_grant_nonce,
    )
    assert current.matches_current_attempt is True
    assert bool(current) is False
    assert current.authorizes_execution is False
    assert current.grant_nonce_rotates_on_takeover is False
    local = LocalHarnessConsumptionLedger()
    stale = _start(
        host,
        taken,
        principal,
        local,
        owner_id="worker-a",
        attempt_token=first.attempt_token,
        fence_epoch=first.fence_epoch,
    )
    assert stale.ledger_rejection is MasterExecutionLedgerRejection.FENCED_OUT
    assert stale.countable_local_start is False
    assert local.holds_nonce(first.grant_nonce) is False
    late = ledger.mark_started(
        ledger_envelope,
        owner_id="worker-a",
        attempt_token=first.attempt_token,
        fence_epoch=first.fence_epoch,
        now_utc=EXPIRED,
    )
    late_complete = ledger.complete(
        ledger_envelope,
        owner_id="worker-a",
        attempt_token=first.attempt_token,
        fence_epoch=first.fence_epoch,
        now_utc=EXPIRED,
    )
    assert late.rejection is MasterExecutionLedgerRejection.FENCED_OUT
    assert late_complete.rejection is MasterExecutionLedgerRejection.FENCED_OUT
    started = _start(host, taken, principal, local, owner_id="worker-b", now_utc=EXPIRED)
    assert started.countable_local_start is True
    assert started.fresh_transition is True
    assert started.distributed_exactly_once is False


def test_expired_started_attempt_does_not_repeat_by_default():
    host, _issuer, ledger, _backend, _store, envelope, principal = _host()
    reserved = _reserve(host, envelope, principal)
    local = LocalHarnessConsumptionLedger()
    started = _start(host, reserved, principal, local)
    assert started.countable_local_start is True
    blocked = _reserve(host, envelope, principal, owner_id="worker-b", now_utc=EXPIRED)
    _assert_local(blocked)
    assert blocked.ledger_rejection is MasterExecutionLedgerRejection.NON_IDEMPOTENT_REPEAT_PROHIBITED
    assert blocked.grant_newly_issued is False
    flipped = _reserve(
        host,
        envelope,
        principal,
        owner_id="worker-b",
        now_utc=EXPIRED,
        repeat_non_idempotent_after_expiry=True,
    )
    assert flipped.ledger_rejection is MasterExecutionLedgerRejection.CONFLICTING_DUPLICATE
    direct = ledger.reserve_once(
        _ledger_envelope(envelope, reserved),
        owner_id="worker-b",
        now_utc=EXPIRED,
        lease_seconds=LEASE,
    )
    assert direct.rejection is MasterExecutionLedgerRejection.NON_IDEMPOTENT_REPEAT_PROHIBITED
    assert direct.specialist_execution_permitted is False


def test_explicit_repeat_still_is_not_distributed_exactly_once():
    host, _issuer, ledger, _backend, _store, envelope, principal = _host(
        _envelope(invocation_id="invocation-repeat", idempotency_key="idem-repeat")
    )
    reserved = _reserve(host, envelope, principal, repeat_non_idempotent_after_expiry=True)
    assert reserved.repeat_non_idempotent_after_expiry is True
    local_a = LocalHarnessConsumptionLedger()
    first = _start(host, reserved, principal, local_a)
    assert first.countable_local_start is True
    taken = _reserve(
        host,
        envelope,
        principal,
        owner_id="worker-b",
        now_utc=EXPIRED,
        repeat_non_idempotent_after_expiry=True,
    )
    assert taken.fresh_transition is True
    assert taken.fence_epoch == 2
    assert taken.ledger_grant_nonce == reserved.ledger_grant_nonce
    assert taken.distributed_exactly_once is False
    stale = ledger.fenced_attempt_matches(
        _ledger_envelope(envelope, taken),
        owner_id="worker-a",
        attempt_token=reserved.attempt_token,
        fence_epoch=reserved.fence_epoch,
        grant_nonce=reserved.ledger_grant_nonce,
    )
    assert stale.matches_current_attempt is False
    assert stale.rejection is MasterExecutionLedgerRejection.FENCED_OUT


def test_cancellation_versus_start_allows_one_fresh_transition():
    for index in range(20):
        host, _issuer, _ledger, _backend, _store, _base, principal = _host(
            _envelope(invocation_id=f"invocation-cancel-{index}", idempotency_key=f"idem-cancel-{index}")
        )
        reserved = _reserve(host, _base, principal)
        barrier = threading.Barrier(2)
        outcomes = []
        errors: list[BaseException] = []
        guard = threading.Lock()

        def starter() -> None:
            try:
                barrier.wait()
                started = _start(host, reserved, principal, LocalHarnessConsumptionLedger())
                with guard:
                    outcomes.append(started)
            except BaseException as exc:  # pragma: no cover - reported below
                errors.append(exc)

        def canceller() -> None:
            try:
                barrier.wait()
                cancelled = host.cancel_reserved(
                    reserved.grant,
                    principal=principal,
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
        assert len(outcomes) == 2
        fresh_starts = [item for item in outcomes if item.countable_local_start]
        fresh_cancels = [
            item
            for item in outcomes
            if item.outcome is NonproductionBoundaryOutcome.CANCELLED_LOCAL and item.fresh_transition
        ]
        assert len(fresh_starts) + len(fresh_cancels) == 1
        assert all(item.distributed_exactly_once is False for item in outcomes)
        assert all(item.durable_ledger_consumed is False for item in outcomes)
        assert all(item.execution_permitted is False for item in outcomes)


def test_transport_error_fails_closed_and_is_not_a_claim():
    host, _issuer, _ledger, backend, _store, envelope, principal = _host()
    backend.unavailable = True
    with pytest.raises(LedgerStoreUnavailable):
        _reserve(host, envelope, principal)
    backend.unavailable = False
    reserved = _reserve(host, envelope, principal)
    assert reserved.fresh_transition is True
    assert reserved.grant_newly_issued is True
    backend.fail_writes = True
    local = LocalHarnessConsumptionLedger()
    with pytest.raises(LedgerStoreUnavailable):
        _start(host, reserved, principal, local)
    assert local.holds_nonce(reserved.grant_nonce) is False
    backend.fail_writes = False
    started = _start(host, reserved, principal, local)
    assert started.countable_local_start is True
    assert started.distributed_exactly_once is False


def test_hmac_tamper_and_key_retirement_do_not_start():
    host, issuer, _ledger, _backend, _store, envelope, principal = _host()
    reserved = _reserve(host, envelope, principal)
    tampered = dict(reserved.grant.serialized_claim())
    tampered["correlation_id"] = "correlation-tampered"
    local = LocalHarnessConsumptionLedger()
    denied = host.consume_local_and_mark_started(
        parse_execution_grant(tampered),
        principal=principal,
        local_consumption_ledger=local,
        owner_id="worker-a",
        attempt_token=reserved.attempt_token,
        fence_epoch=reserved.fence_epoch,
        now_utc=NOW,
    )
    assert denied.grant_reason is GrantRejectionReason.TAMPERED
    assert denied.countable_local_start is False
    assert local.holds_nonce(reserved.grant_nonce) is False
    assert _reserve(host, envelope, principal).state is MasterExecutionLedgerState.RESERVED
    key_id = str(reserved.grant.serialized_claim()["key_id"])
    issuer.rotate_signing_key()
    issuer.retire_verification_key(key_id)
    retired = _start(host, reserved, principal, LocalHarnessConsumptionLedger())
    assert retired.grant_reason is GrantRejectionReason.KEY_RETIRED
    assert retired.countable_local_start is False
    assert _reserve(host, envelope, principal).state is MasterExecutionLedgerState.RESERVED


def test_same_reference_string_is_not_the_same_seal():
    host, issuer, _ledger, _backend, _store, envelope, principal = _host()
    other_issuer = LocalHarnessGrantIssuer(_clock())
    foreign = other_issuer.seal_principal(envelope.principal_ref)
    assert foreign.reference == principal.reference
    assert foreign is not principal
    with pytest.raises(ContractValidationError, match="principal_not_sealed"):
        _reserve(host, envelope, foreign)
    reserved = _reserve(host, envelope, principal)
    assert reserved.fresh_transition is True
    twin = issuer.seal_principal(envelope.principal_ref)
    assert twin.reference == principal.reference
    assert twin is not principal
    mismatched = _reserve(host, envelope, twin)
    assert mismatched.grant_reason is GrantRejectionReason.PRINCIPAL_MISMATCH
    assert mismatched.fresh_transition is False
    replay = _reserve(host, envelope, principal)
    assert replay.fence_epoch == 1
    assert replay.attempt_token == reserved.attempt_token
    local = LocalHarnessConsumptionLedger()
    denied = _start(host, reserved, foreign, local)
    assert denied.grant_reason is GrantRejectionReason.PRINCIPAL_MISMATCH
    assert local.holds_nonce(reserved.grant_nonce) is False
    verification = issuer.verify(
        reserved.grant,
        principal=foreign,
        planning_session_id=envelope.planning_session_id,
        step_id=envelope.step_id,
        invocation_id=envelope.invocation_id,
        specialist=envelope.specialist,
        purpose=envelope.purpose,
        capability=envelope.capability,
        policy_revision=envelope.policy_revision,
        tool_name="get_aircraft_operational_context",
        resource_scope=envelope.resource_scope,
        argument_names=("aircraft_id",),
        region_scope=("NY",),
        temporal_scopes=envelope.temporal_scopes,
        historical_window=None,
        decision_target_scope=None,
        accepted_at_utc=envelope.accepted_at_utc,
        deadline_utc=envelope.deadline_utc,
        expires_at_utc=envelope.expires_at_utc,
        idempotency_key=envelope.idempotency_key,
        max_model_turns=1,
        max_tool_calls=1,
        correlation_id=envelope.correlation_id,
        master_request_id=envelope.master_request_id,
    )
    assert verification.local_harness_binding_accepted is False
    assert verification.reason is GrantRejectionReason.PRINCIPAL_MISMATCH
    arguments = tuple(
        sorted(server_read_argument_names(MasterSpecialist.LIVE_OPS, "get_aircraft_operational_context"))
    )
    swapped_region = issuer.verify(
        reserved.grant,
        principal=principal,
        planning_session_id=envelope.planning_session_id,
        step_id=envelope.step_id,
        invocation_id=envelope.invocation_id,
        specialist=envelope.specialist,
        purpose=envelope.purpose,
        capability=envelope.capability,
        policy_revision=envelope.policy_revision,
        tool_name="get_aircraft_operational_context",
        resource_scope=envelope.resource_scope,
        argument_names=arguments,
        region_scope=("NY",),
        temporal_scopes=envelope.temporal_scopes,
        historical_window=None,
        decision_target_scope=None,
        accepted_at_utc=envelope.accepted_at_utc,
        deadline_utc=envelope.deadline_utc,
        expires_at_utc=envelope.expires_at_utc,
        idempotency_key=envelope.idempotency_key,
        max_model_turns=1,
        max_tool_calls=1,
        correlation_id=envelope.correlation_id,
        master_request_id=envelope.master_request_id,
    )
    assert swapped_region.reason is GrantRejectionReason.REGION_MISMATCH
    assert swapped_region.local_harness_binding_accepted is False


def test_old_grant_replay_does_not_start_again():
    host, _issuer, _ledger, _backend, _store, envelope, principal = _host()
    reserved = _reserve(host, envelope, principal)
    local = LocalHarnessConsumptionLedger()
    first = _start(host, reserved, principal, local)
    second = _start(host, reserved, principal, local)
    other_local = LocalHarnessConsumptionLedger()
    third = _start(host, reserved, principal, other_local)
    assert first.countable_local_start is True
    assert second.countable_local_start is False
    assert second.outcome is NonproductionBoundaryOutcome.REPLAYED_LOCAL
    assert third.grant_reason is GrantRejectionReason.REPLAY
    assert third.countable_local_start is False
    assert other_local.holds_nonce(reserved.grant_nonce) is False
    assert local.holds_nonce(reserved.grant_nonce) is True


def test_ttl_deletion_is_not_first_use():
    host, _issuer, ledger, _backend, store, envelope, principal = _host()
    reserved = _reserve(host, envelope, principal)
    item = _ledger_envelope(envelope, reserved)
    partition_key, sort_key = ledger_item_key(item)
    store.simulate_asynchronous_ttl_deletion(partition_key, sort_key)
    assert TTL_DELETION_IS_CORRECTNESS is False
    deleted = _reserve(host, envelope, principal)
    _assert_local(deleted)
    assert deleted.ledger_rejection is MasterExecutionLedgerRejection.RETENTION_GAP
    assert deleted.fresh_transition is False
    assert deleted.grant_newly_issued is False
    assert deleted.ttl_absence_is_first_use is False
    check = ledger.fenced_attempt_matches(
        item,
        owner_id="worker-a",
        attempt_token=reserved.attempt_token,
        fence_epoch=reserved.fence_epoch,
        grant_nonce=reserved.ledger_grant_nonce,
    )
    assert check.ttl_deleted is True
    assert check.matches_current_attempt is False
    assert check.absence_is_first_use is False
    assert check.authorizes_execution is False
    again = ledger.reserve_once(item, owner_id="worker-b", now_utc=NOW, lease_seconds=LEASE)
    assert again.rejection is MasterExecutionLedgerRejection.RETENTION_GAP
    assert again.fresh_transition is False


def test_dynamodb_adapter_type_is_not_verified_aws_provenance():
    class BoomClient:
        def get_item(self, **_kwargs: object) -> dict[str, object]:
            raise RuntimeError("transport")

        def put_item(self, **_kwargs: object) -> dict[str, object]:
            raise RuntimeError("transport")

    with pytest.raises(ContractValidationError, match="unexpected_ledger_table"):
        DynamoDbMasterExecutionLedgerStore(BoomClient(), "sigmet_hazards")
    store = DynamoDbMasterExecutionLedgerStore(BoomClient(), DYNAMODB_LEDGER_TABLE_NAME)
    assert store.verified_aws_account_and_table_provenance is False
    assert store.production_cross_instance_authority is False
    assert DURABLE_ADAPTER_TYPE_IS_VERIFIED_AWS_PROVENANCE is False
    ledger = MasterExecutionLedger.for_durable(store)
    assert ledger.store_is_durable_adapter is True
    assert ledger.verified_aws_account_and_table_provenance is False
    assert ledger.production_cross_instance_authority is False
    issuer = LocalHarnessGrantIssuer(_clock())
    host = NonproductionHostGrantLedger(issuer=issuer, ledger=ledger)
    envelope = _envelope(invocation_id="invocation-dynamo", idempotency_key="idem-dynamo")
    principal = issuer.seal_principal(envelope.principal_ref)
    with pytest.raises(LedgerStoreUnavailable):
        _reserve(host, envelope, principal)
    assert host.distributed_exactly_once_achieved is False


def test_production_issuance_and_durable_consume_stay_closed():
    host, _issuer, ledger, _backend, _store, envelope, principal = _host()
    reserved = _reserve(host, envelope, principal)
    assert PRODUCTION_GRANT_ISSUANCE_AVAILABLE is False
    assert DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE is False
    assert DISTRIBUTED_EXACTLY_ONCE_ACHIEVED is False
    with pytest.raises(ContractValidationError, match="production_grant_issuance_closed"):
        issue_production_execution_grant(envelope, principal)
    with pytest.raises(ContractValidationError, match="ledger_not_integrated"):
        bind_production_grant_to_master_ledger(reserved.grant, ledger)
    interface = required_atomic_single_use_interface()
    assert bool(interface) is False
    assert interface.implemented is False
    assert interface.distributed_exactly_once is False
    assert "LocalHarnessGrantIssuer.consume" in interface.consume_signature
    assert "ReviewedAtomicGrantLedger.reserve_once" in interface.ledger_reserve_signature
    assert "mark_started" in interface.mark_started_signature
    assert "LocalHarnessConsumptionLedger" in interface.reason
    denied = host.consume_local_and_mark_started(
        reserved.grant,
        principal=principal,
        local_consumption_ledger=ledger,
        owner_id="worker-a",
        attempt_token=reserved.attempt_token,
        fence_epoch=reserved.fence_epoch,
        now_utc=NOW,
    )
    _assert_local(denied)
    assert denied.grant_reason is GrantRejectionReason.LEDGER_NOT_INTEGRATED
    assert denied.countable_local_start is False
    assert _reserve(host, envelope, principal).state is MasterExecutionLedgerState.RESERVED
    assert "consume_local_and_mark_started" in CONSUMPTION_STARTED_BOUNDARY
    assert "local_consumption_ledger: object" in CONSUMPTION_STARTED_BOUNDARY
    assert "attempt_token: str" in CONSUMPTION_STARTED_BOUNDARY
    assert "fence_epoch: int" in CONSUMPTION_STARTED_BOUNDARY


def test_wrong_region_target_and_historical_window_do_not_reserve():
    host, issuer, ledger, _backend, _store, envelope, principal = _host()
    geographic = _reserve(host, replace(envelope, region_scope=("California",)), principal)
    assert GrantRejectionReason.REGION_NOT_AUTHORIZED in geographic.grant_reasons
    assert geographic.grant_newly_issued is False
    targeted = _reserve(host, replace(envelope, decision_target_scope="KLAX"), principal)
    assert GrantRejectionReason.DECISION_TARGET_NOT_APPLICABLE in targeted.grant_reasons
    with pytest.raises(ContractValidationError, match="scan_not_permitted"):
        LogicalReadOperation("aircraft", ReadOperation.SCAN)
    reserved = _reserve(host, envelope, principal)
    assert reserved.fresh_transition is True
    assert reserved.region_scope == "CA"
    history = _envelope(
        invocation_id="invocation-history",
        idempotency_key="idem-history",
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        purpose=MasterPurpose.ANSWER,
        capability=AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS,
        tool_name="summarize_historical_encounters",
        resource_scope=tuple(
            LogicalReadOperation(resource_id, operation)
            for resource_id, operation in server_read_resources(
                MasterSpecialist.HISTORICAL_ANALYTICS,
                "summarize_historical_encounters",
            )
        ),
        temporal_scopes=(TemporalScope.HISTORICAL,),
        historical_window=("2026-10-01T00:00:00Z", "2026-10-09T00:00:00Z"),
        principal_ref="principal-history",
    )
    history_principal = issuer.seal_principal(history.principal_ref)
    unbounded = _reserve(host, history, history_principal)
    assert GrantRejectionReason.HISTORICAL_INTERVAL_EXCEEDS_CEILING in unbounded.grant_reasons
    direct = ledger.reserve_once(
        _ledger_envelope(envelope, reserved),
        owner_id="worker-b",
        now_utc=NOW,
        lease_seconds=LEASE,
    )
    assert direct.rejection is MasterExecutionLedgerRejection.CLAIM_HELD


def test_lease_bounds_match_the_conditional_expression():
    unexpired = conditional_replace_expression("unexpired", False)
    expired = conditional_replace_expression("expired", False)
    repeatable = conditional_replace_expression("expired", True)
    assert unexpired == CONDITIONAL_REPLACE_UNEXPIRED
    assert expired == CONDITIONAL_REPLACE_EXPIRED
    assert "#lease_expires_at_epoch > :now_epoch" in unexpired
    assert "#lease_expires_at_epoch <= :now_epoch" in expired
    assert "#repeat_non_idempotent_after_expiry = :repeat_non_idempotent_after_expiry" in repeatable
    with pytest.raises(ContractValidationError, match="invalid_lease_relation"):
        conditional_replace_expression("unexpired", True)
    host, _issuer, ledger, _backend, _store, envelope, principal = _host()
    reserved = _reserve(host, envelope, principal)
    local = LocalHarnessConsumptionLedger()
    expired_start = _start(host, reserved, principal, local, now_utc=EXPIRED)
    assert expired_start.ledger_rejection is MasterExecutionLedgerRejection.LEASE_EXPIRED
    assert expired_start.countable_local_start is False
    assert local.holds_nonce(reserved.grant_nonce) is False
    assert _reserve(host, envelope, principal).state is MasterExecutionLedgerState.RESERVED
    exact = _envelope(
        invocation_id="invocation-exact",
        idempotency_key="idem-exact",
        deadline_utc="2026-10-27T21:00:00Z",
        expires_at_utc="2026-10-27T21:00:00Z",
    )
    fitted = _reserve(host, exact, principal, now_utc="2026-10-27T20:59:00Z", lease_seconds=60)
    assert fitted.fresh_transition is True
    assert fitted.grant_newly_issued is True
    with pytest.raises(ContractValidationError, match="lease_exceeds_deadline"):
        _reserve(
            host,
            _envelope(invocation_id="invocation-over", idempotency_key="idem-over"),
            principal,
            now_utc="2026-10-27T20:59:00Z",
            lease_seconds=61,
        )
    direct = ledger.mark_started(
        _ledger_envelope(envelope, reserved),
        owner_id="worker-a",
        attempt_token=reserved.attempt_token,
        fence_epoch=reserved.fence_epoch,
        now_utc=EXPIRED,
    )
    assert direct.rejection is MasterExecutionLedgerRejection.LEASE_EXPIRED
