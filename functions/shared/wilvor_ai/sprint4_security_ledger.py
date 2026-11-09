"""Sprint 4 security: gated ledger admission over existing consume contracts.

This module prepares real AWS ledger integration without enabling writes.
Atomic ``STARTED`` admission is the existing ``TransactWriteItems`` of one
attempt item plus a no-TTL consumption marker. Replay, fencing, failure,
and timeout are classified against that same item. A successful local
transaction is not distributed exactly-once specialist execution.

``LocalHarnessGrantIssuer.consume`` still rejects ``MasterExecutionLedger``.
``bind_production_grant_to_master_ledger`` still raises. Production flags
stay false. Injected clients are not AWS account or table provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
    _validate_bounded_text,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_execution_grants import (
    DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE,
    HMAC_PREVENTS_REPLAY,
    PRODUCTION_GRANT_ISSUANCE_AVAILABLE,
    LocalHarnessPrincipal,
    MasterExecutionGrantEnvelope,
    bind_production_grant_to_master_ledger,
    issue_production_execution_grant,
)
from wilvor_ai.master_execution_ledger import (
    CONDITIONAL_PUT_ABSENT,
    DURABLE_ADAPTER_TYPE_IS_VERIFIED_AWS_PROVENANCE,
    DYNAMODB_LEDGER_TABLE_NAME,
    GRANT_NONCE_ROTATES_ON_TAKEOVER,
    MAX_LEASE_SECONDS,
    TTL_DELETION_IS_CORRECTNESS,
    LedgerStoreUnavailable,
    MasterExecutionAttemptFenceCheck,
    MasterExecutionLedgerEnvelope,
    MasterExecutionLedgerOutcome,
    MasterExecutionLedgerRejection,
    MasterExecutionLedgerResult,
    MasterExecutionLedgerState,
    ledger_item_key,
)
from wilvor_ai.master_host_grant_ledger_integration import required_atomic_single_use_interface
from wilvor_ai.master_routing import MasterRouteExecutionAuthority
from wilvor_ai.sprint3c_grant_ledger import (
    HOST_CANCEL_REASON,
    SPRINT3C_DISTRIBUTED_EXACTLY_ONCE,
    SPRINT3C_PRODUCTION_GRANT_LEDGER_ENABLED,
    DurableConsumeResult,
    Sprint3CDurableGrantLedger,
    Sprint3CIdentityBinding,
    consumption_marker_key,
    ledger_envelope_from_grant,
    signed_grant_nonce,
)


SPRINT4_SECURITY_LEDGER_SCHEMA_VERSION = "wilvor.ai.sprint4.security_ledger.v1"
SPRINT4_LEDGER_WRITES_ENABLED = False
SPRINT4_LIVE_AWS_ENABLED = False
SPRINT4_DISTRIBUTED_EXACTLY_ONCE = False
SPRINT4_ATOMIC_ADMISSION_IS_NATIVE_EXECUTION = False
HOST_TIMEOUT_REASON = "host_timeout"
_MARKER_TTL_NAMES = frozenset(
    {
        "retention_expires_at_epoch",
        "ttl",
        "expires_at_epoch",
        "TimeToLive",
        "time_to_live",
    }
)


class Sprint4AdmissionKind(str, Enum):
    """What one gated ledger call did. None of these is native execution."""

    STARTED = "STARTED"
    REPLAYED = "REPLAYED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    STORE_UNAVAILABLE = "STORE_UNAVAILABLE"


class Sprint4TimeoutClass(str, Enum):
    """Timeout and lease outcomes. Expiry is not a new start."""

    DEADLINE_BEFORE_START = "DEADLINE_BEFORE_START"
    LEASE_EXPIRED_STARTED = "LEASE_EXPIRED_STARTED"
    LEASE_EXPIRED_TAKEOVER_PROHIBITED = "LEASE_EXPIRED_TAKEOVER_PROHIBITED"
    HOST_TIMEOUT_RECORDED = "HOST_TIMEOUT_RECORDED"
    NOT_A_TIMEOUT = "NOT_A_TIMEOUT"


def _assert_closed() -> None:
    if SPRINT4_LEDGER_WRITES_ENABLED is not False:
        raise ContractValidationError("sprint4_ledger_writes_enabled")
    if SPRINT4_LIVE_AWS_ENABLED is not False:
        raise ContractValidationError("sprint4_live_aws_enabled")
    if SPRINT4_DISTRIBUTED_EXACTLY_ONCE is not False:
        raise ContractValidationError("distributed_exactly_once_not_achieved")
    if SPRINT4_ATOMIC_ADMISSION_IS_NATIVE_EXECUTION is not False:
        raise ContractValidationError("atomic_admission_is_not_native_execution")
    if PRODUCTION_GRANT_ISSUANCE_AVAILABLE is not False:
        raise ContractValidationError("production_grant_issuance_closed")
    if DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE is not False:
        raise ContractValidationError("ledger_not_integrated")
    if SPRINT3C_PRODUCTION_GRANT_LEDGER_ENABLED is not False:
        raise ContractValidationError("production_grant_issuance_closed")
    if SPRINT3C_DISTRIBUTED_EXACTLY_ONCE is not False:
        raise ContractValidationError("distributed_exactly_once_not_achieved")
    if HMAC_PREVENTS_REPLAY is not False:
        raise ContractValidationError("hmac_does_not_prevent_replay")
    if GRANT_NONCE_ROTATES_ON_TAKEOVER is not False:
        raise ContractValidationError("grant_nonce_rotated_on_takeover")
    if DURABLE_ADAPTER_TYPE_IS_VERIFIED_AWS_PROVENANCE is not False:
        raise ContractValidationError("durable_adapter_type_is_not_aws_provenance")
    if TTL_DELETION_IS_CORRECTNESS is not False:
        raise ContractValidationError("ttl_deletion_is_not_correctness")


def _token(value: object, field_name: str) -> str:
    errors = _validate_bounded_text(value, field_name, max_length=PROVENANCE_TOKEN_MAX_LENGTH)
    if errors:
        raise ContractValidationError(errors)
    if not isinstance(value, str) or value != value.strip() or any(character.isspace() for character in value):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _attribute_text(item: Mapping[str, object], name: str) -> str | None:
    value = item.get(name)
    if isinstance(value, dict) and isinstance(value.get("S"), str):
        return str(value["S"])
    return None


def enable_sprint4_ledger_writes(*_args: object, **_kwargs: object) -> None:
    """Ledger writes stay disabled. Arguments cannot open AWS."""

    _assert_closed()
    raise ContractValidationError("sprint4_ledger_writes_disabled")


def execute_live_ledger_write(*_args: object, **_kwargs: object) -> None:
    """Refuse a live TransactWriteItems. The AWS test plan is not this call."""

    _assert_closed()
    raise ContractValidationError("sprint4_live_aws_not_executed")


@dataclass(frozen=True)
class Sprint4ConsumptionMarkerView:
    """Observed consume marker. Presence is not first use and not TTL correctness."""

    present: bool
    partition_key: str
    sort_key: str
    grant_nonce: str | None
    has_ttl_attribute: bool
    ttl_deletion_is_correctness: bool = False
    absence_is_first_use: bool = False

    def __post_init__(self) -> None:
        _assert_closed()
        if self.ttl_deletion_is_correctness is not False or self.absence_is_first_use is not False:
            raise ContractValidationError("ttl_deletion_is_not_correctness")
        if self.present and self.has_ttl_attribute:
            raise ContractValidationError("consumption_marker_must_not_have_ttl")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class Sprint4LedgerAudit:
    """Audit fields copied from one admission. Not a permit and not CloudTrail."""

    schema_version: str
    master_request_id: str
    planning_session_id: str
    invocation_id: str
    principal_ref: str
    grant_nonce: str
    idempotency_key: str
    payload_fingerprint: str
    owner_id: str
    attempt_token: str
    fence_epoch: int
    state: MasterExecutionLedgerState
    outcome: MasterExecutionLedgerOutcome
    ledger_observed_at_utc: str
    table_name: str = DYNAMODB_LEDGER_TABLE_NAME
    aws_write_performed: bool = False
    live_aws_invoked: bool = False
    native_exactly_once: bool = False
    verified_aws_provenance: bool = False
    specialist_executed: bool = False

    def __post_init__(self) -> None:
        _assert_closed()
        if self.schema_version != SPRINT4_SECURITY_LEDGER_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if self.table_name != DYNAMODB_LEDGER_TABLE_NAME:
            raise ContractValidationError("unexpected_ledger_table")
        for flag in (
            self.aws_write_performed,
            self.live_aws_invoked,
            self.native_exactly_once,
            self.verified_aws_provenance,
            self.specialist_executed,
        ):
            if flag is not False:
                raise ContractValidationError("execution_not_granted")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class Sprint4AdmissionResult:
    """One gated admission outcome. ``STARTED`` fences an attempt, not a specialist."""

    kind: Sprint4AdmissionKind
    consume: DurableConsumeResult | None
    terminal: MasterExecutionLedgerResult | None
    marker: Sprint4ConsumptionMarkerView | None
    audit: Sprint4LedgerAudit | None
    timeout_class: Sprint4TimeoutClass
    fence: MasterExecutionAttemptFenceCheck | None = None
    store_unavailable: bool = False
    atomic_admission_is_native_execution: bool = False
    distributed_exactly_once: bool = False
    live_aws_invoked: bool = False
    ledger_writes_enabled: bool = False

    def __post_init__(self) -> None:
        _assert_closed()
        if not isinstance(self.kind, Sprint4AdmissionKind):
            raise ContractValidationError("invalid_admission_kind")
        if not isinstance(self.timeout_class, Sprint4TimeoutClass):
            raise ContractValidationError("invalid_timeout_class")
        for flag in (
            self.atomic_admission_is_native_execution,
            self.distributed_exactly_once,
            self.live_aws_invoked,
            self.ledger_writes_enabled,
        ):
            if flag is not False:
                raise ContractValidationError("atomic_admission_is_not_native_execution")
        if self.consume is not None:
            if self.consume.distributed_exactly_once is not False or self.consume.specialist_executed is not False:
                raise ContractValidationError("ledger_transaction_is_not_native_execution")
        if self.kind is Sprint4AdmissionKind.STORE_UNAVAILABLE and self.store_unavailable is not True:
            raise ContractValidationError("invalid_admission_kind")
        if self.kind is not Sprint4AdmissionKind.STORE_UNAVAILABLE and self.store_unavailable is not False:
            raise ContractValidationError("invalid_admission_kind")

    def __bool__(self) -> bool:
        return False


def classify_timeout(
    consume: DurableConsumeResult | None,
    *,
    now_utc: str,
    deadline_utc: str,
    lease_expired: bool = False,
) -> Sprint4TimeoutClass:
    """Map ledger rejections onto timeout classes. Expiry is not a new start."""

    _assert_closed()
    _token(now_utc, "now_utc")
    _token(deadline_utc, "deadline_utc")
    if consume is None:
        return Sprint4TimeoutClass.NOT_A_TIMEOUT
    rejection = consume.ledger_rejection
    if rejection is MasterExecutionLedgerRejection.DEADLINE_EXPIRED:
        return Sprint4TimeoutClass.DEADLINE_BEFORE_START
    if rejection is MasterExecutionLedgerRejection.NON_IDEMPOTENT_REPEAT_PROHIBITED:
        return Sprint4TimeoutClass.LEASE_EXPIRED_TAKEOVER_PROHIBITED
    if rejection is MasterExecutionLedgerRejection.LEASE_EXPIRED:
        return Sprint4TimeoutClass.LEASE_EXPIRED_STARTED
    if lease_expired and consume.attempt is not None and consume.attempt.state is MasterExecutionLedgerState.STARTED:
        return Sprint4TimeoutClass.LEASE_EXPIRED_STARTED
    return Sprint4TimeoutClass.NOT_A_TIMEOUT


def classify_failure(result: MasterExecutionLedgerResult) -> Sprint4AdmissionKind:
    """Terminal failure under the current fence is not a second start."""

    _assert_closed()
    if type(result) is not MasterExecutionLedgerResult:
        raise ContractValidationError("invalid_ledger_result")
    if result.specialist_executed is not False or result.dispatch_permitted is not False:
        raise ContractValidationError("execution_not_granted")
    if result.state is MasterExecutionLedgerState.FAILED:
        if result.outcome is MasterExecutionLedgerOutcome.REJECTED:
            return Sprint4AdmissionKind.REJECTED
        return Sprint4AdmissionKind.FAILED
    if result.state is MasterExecutionLedgerState.COMPLETED:
        return Sprint4AdmissionKind.COMPLETED
    if result.state is MasterExecutionLedgerState.CANCELLED:
        return Sprint4AdmissionKind.CANCELLED
    if result.outcome is MasterExecutionLedgerOutcome.REJECTED:
        return Sprint4AdmissionKind.REJECTED
    if result.state is MasterExecutionLedgerState.STARTED:
        return Sprint4AdmissionKind.STARTED
    return Sprint4AdmissionKind.REJECTED


class Sprint4SecurityLedgerPort:
    """Gated compose of the 3C durable consume. The client is injected. No AWS."""

    def __init__(self, ledger: object) -> None:
        _assert_closed()
        if type(ledger) is not Sprint3CDurableGrantLedger:
            raise ContractValidationError("invalid_grant_ledger")
        if ledger.distributed_exactly_once is not False:
            raise ContractValidationError("distributed_exactly_once_not_achieved")
        if ledger.verified_aws_account_and_table_provenance is not False:
            raise ContractValidationError("durable_adapter_type_is_not_aws_provenance")
        self._ledger = ledger

    @property
    def ledger_writes_enabled(self) -> bool:
        return False

    @property
    def live_aws_enabled(self) -> bool:
        return False

    @property
    def distributed_exactly_once(self) -> bool:
        return False

    @property
    def atomic_admission_is_native_execution(self) -> bool:
        return False

    @property
    def verified_aws_account_and_table_provenance(self) -> bool:
        return False

    def admit_started(
        self,
        claim: object,
        *,
        envelope: object,
        principal: object,
        identity: object,
        owner_id: str,
        now_utc: str,
        lease_seconds: int,
        production_requested: bool = False,
        live_aws_requested: bool = False,
    ) -> Sprint4AdmissionResult:
        """Consume one signed grant into STARTED plus a no-TTL marker.

        The write is the existing 3C transaction against an injected client.
        Live AWS and production consume stay closed.
        """

        _assert_closed()
        if production_requested is True:
            raise ContractValidationError("production_grant_issuance_closed")
        if live_aws_requested is True or SPRINT4_LIVE_AWS_ENABLED is not False:
            raise ContractValidationError("sprint4_live_aws_not_executed")
        if type(envelope) is not MasterExecutionGrantEnvelope:
            raise ContractValidationError("invalid_grant_envelope")
        if type(principal) is not LocalHarnessPrincipal:
            raise ContractValidationError("principal_not_sealed")
        if type(identity) is not Sprint3CIdentityBinding:
            raise ContractValidationError("invalid_identity_binding")
        _token(owner_id, "owner_id")
        _token(now_utc, "now_utc")
        if type(lease_seconds) is not int or lease_seconds < 1 or lease_seconds > MAX_LEASE_SECONDS:
            raise ContractValidationError("invalid_lease_seconds")
        try:
            consume = self._ledger.consume_signed_grant(
                claim,
                envelope=envelope,
                principal=principal,
                identity=identity,
                owner_id=owner_id,
                now_utc=now_utc,
                lease_seconds=lease_seconds,
                production_requested=False,
            )
        except LedgerStoreUnavailable:
            return Sprint4AdmissionResult(
                kind=Sprint4AdmissionKind.STORE_UNAVAILABLE,
                consume=None,
                terminal=None,
                marker=None,
                audit=None,
                timeout_class=Sprint4TimeoutClass.NOT_A_TIMEOUT,
                store_unavailable=True,
            )
        marker = self.inspect_marker(claim, envelope)
        timeout = classify_timeout(consume, now_utc=now_utc, deadline_utc=envelope.deadline_utc)
        kind = self._kind_from_consume(consume, timeout)
        audit = None
        fence = None
        if consume.attempt is not None:
            audit = self._audit(consume)
            fence = self._ledger.fenced_attempt_matches(
                envelope,
                owner_id=consume.attempt.owner_id,
                attempt_token=consume.attempt.attempt_token,
                fence_epoch=consume.attempt.fence_epoch,
                grant_nonce=consume.attempt.grant_nonce,
            )
            if kind is Sprint4AdmissionKind.STARTED and marker.present is not True:
                raise ContractValidationError("consumption_marker_missing")
            if kind is Sprint4AdmissionKind.STARTED and consume.attempt.state is not MasterExecutionLedgerState.STARTED:
                raise ContractValidationError("invalid_ledger_state")
        return Sprint4AdmissionResult(
            kind=kind,
            consume=consume,
            terminal=None,
            marker=marker,
            audit=audit,
            timeout_class=timeout,
            fence=fence,
        )

    def inspect_marker(self, claim: object, envelope: object) -> Sprint4ConsumptionMarkerView:
        """Consistent read of the no-TTL marker. Absence is not first use."""

        _assert_closed()
        if type(envelope) is not MasterExecutionGrantEnvelope:
            raise ContractValidationError("invalid_grant_envelope")
        nonce = signed_grant_nonce(claim)
        ledger_envelope = ledger_envelope_from_grant(envelope, principal_ref=envelope.principal_ref)
        pk, sk = consumption_marker_key(nonce, ledger_envelope)
        item = self._ledger._read_marker(nonce, ledger_envelope)
        if item is None:
            return Sprint4ConsumptionMarkerView(
                present=False,
                partition_key=pk,
                sort_key=sk,
                grant_nonce=None,
                has_ttl_attribute=False,
            )
        has_ttl = any(name in item for name in _MARKER_TTL_NAMES)
        return Sprint4ConsumptionMarkerView(
            present=True,
            partition_key=pk,
            sort_key=sk,
            grant_nonce=_attribute_text(item, "grant_nonce"),
            has_ttl_attribute=has_ttl,
        )

    def record_timeout(
        self,
        envelope: object,
        *,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        now_utc: str,
    ) -> Sprint4AdmissionResult:
        """Record host timeout as FAILED under the current fence. Not a replay start."""

        return self._terminal(
            envelope,
            owner_id=owner_id,
            attempt_token=attempt_token,
            fence_epoch=fence_epoch,
            now_utc=now_utc,
            action="timeout",
        )

    def record_failure(
        self,
        envelope: object,
        *,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        now_utc: str,
        reason: str,
    ) -> Sprint4AdmissionResult:
        return self._terminal(
            envelope,
            owner_id=owner_id,
            attempt_token=attempt_token,
            fence_epoch=fence_epoch,
            now_utc=now_utc,
            action="fail",
            reason=reason,
        )

    def record_host_cancel(
        self,
        envelope: object,
        *,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        now_utc: str,
    ) -> Sprint4AdmissionResult:
        return self._terminal(
            envelope,
            owner_id=owner_id,
            attempt_token=attempt_token,
            fence_epoch=fence_epoch,
            now_utc=now_utc,
            action="cancel",
        )

    def complete_attempt(
        self,
        envelope: object,
        *,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        now_utc: str,
    ) -> Sprint4AdmissionResult:
        """Fence-checked complete. Ledger complete is not specialist success."""

        return self._terminal(
            envelope,
            owner_id=owner_id,
            attempt_token=attempt_token,
            fence_epoch=fence_epoch,
            now_utc=now_utc,
            action="complete",
        )

    def fenced_attempt_matches(
        self,
        envelope: object,
        *,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        grant_nonce: str,
    ) -> MasterExecutionAttemptFenceCheck:
        _assert_closed()
        check = self._ledger.fenced_attempt_matches(
            envelope,
            owner_id=owner_id,
            attempt_token=attempt_token,
            fence_epoch=fence_epoch,
            grant_nonce=grant_nonce,
        )
        if check.authorizes_execution is not False or check.specialist_execution_permitted is not False:
            raise ContractValidationError("execution_not_granted")
        return check

    def refuse_production_bind(self, *_args: object, **_kwargs: object) -> None:
        bind_production_grant_to_master_ledger(*_args, **_kwargs)

    def refuse_production_issue(self, *_args: object, **_kwargs: object) -> None:
        issue_production_execution_grant(*_args, **_kwargs)

    def required_atomic_single_use(self) -> object:
        """The frozen join still reports unimplemented. This port does not flip it."""

        _assert_closed()
        interface = required_atomic_single_use_interface()
        if interface.implemented is not False or interface.distributed_exactly_once is not False:
            raise ContractValidationError("distributed_exactly_once_not_achieved")
        return interface

    def _terminal(
        self,
        envelope: object,
        *,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        now_utc: str,
        action: str,
        reason: str | None = None,
    ) -> Sprint4AdmissionResult:
        _assert_closed()
        try:
            if action == "timeout":
                result = self._ledger.fail_attempt(
                    envelope,
                    owner_id=owner_id,
                    attempt_token=attempt_token,
                    fence_epoch=fence_epoch,
                    now_utc=now_utc,
                    reason=HOST_TIMEOUT_REASON,
                )
                timeout = Sprint4TimeoutClass.HOST_TIMEOUT_RECORDED
                if result.outcome is MasterExecutionLedgerOutcome.REJECTED:
                    timeout = Sprint4TimeoutClass.NOT_A_TIMEOUT
            elif action == "fail":
                result = self._ledger.fail_attempt(
                    envelope,
                    owner_id=owner_id,
                    attempt_token=attempt_token,
                    fence_epoch=fence_epoch,
                    now_utc=now_utc,
                    reason=_token(reason, "reason"),
                )
                timeout = Sprint4TimeoutClass.NOT_A_TIMEOUT
            elif action == "cancel":
                result = self._ledger.cancel_started_attempt(
                    envelope,
                    owner_id=owner_id,
                    attempt_token=attempt_token,
                    fence_epoch=fence_epoch,
                    now_utc=now_utc,
                )
                timeout = Sprint4TimeoutClass.NOT_A_TIMEOUT
            elif action == "complete":
                result = self._ledger.complete_attempt(
                    envelope,
                    owner_id=owner_id,
                    attempt_token=attempt_token,
                    fence_epoch=fence_epoch,
                    now_utc=now_utc,
                )
                timeout = Sprint4TimeoutClass.NOT_A_TIMEOUT
            else:
                raise ContractValidationError("invalid_ledger_transition")
        except LedgerStoreUnavailable:
            return Sprint4AdmissionResult(
                kind=Sprint4AdmissionKind.STORE_UNAVAILABLE,
                consume=None,
                terminal=None,
                marker=None,
                audit=None,
                timeout_class=Sprint4TimeoutClass.NOT_A_TIMEOUT,
                store_unavailable=True,
            )
        kind = classify_failure(result)
        if action == "timeout" and result.outcome is not MasterExecutionLedgerOutcome.REJECTED:
            kind = Sprint4AdmissionKind.TIMED_OUT
        if action == "cancel" and result.outcome is not MasterExecutionLedgerOutcome.REJECTED:
            if result.state is MasterExecutionLedgerState.FAILED:
                kind = Sprint4AdmissionKind.CANCELLED
        return Sprint4AdmissionResult(
            kind=kind,
            consume=None,
            terminal=result,
            marker=None,
            audit=None,
            timeout_class=timeout,
        )

    def _kind_from_consume(
        self,
        consume: DurableConsumeResult,
        timeout: Sprint4TimeoutClass,
    ) -> Sprint4AdmissionKind:
        if consume.outcome is MasterExecutionLedgerOutcome.CLAIMED:
            return Sprint4AdmissionKind.STARTED
        if consume.outcome is MasterExecutionLedgerOutcome.REPLAYED:
            return Sprint4AdmissionKind.REPLAYED
        if timeout is Sprint4TimeoutClass.DEADLINE_BEFORE_START:
            return Sprint4AdmissionKind.REJECTED
        return Sprint4AdmissionKind.REJECTED

    def _audit(self, consume: DurableConsumeResult) -> Sprint4LedgerAudit:
        attempt = consume.attempt
        if attempt is None:
            raise ContractValidationError("invalid_ledger_result")
        return Sprint4LedgerAudit(
            schema_version=SPRINT4_SECURITY_LEDGER_SCHEMA_VERSION,
            master_request_id=attempt.master_request_id,
            planning_session_id=attempt.planning_session_id,
            invocation_id=attempt.invocation_id,
            principal_ref=attempt.principal_ref,
            grant_nonce=attempt.grant_nonce,
            idempotency_key=attempt.idempotency_key,
            payload_fingerprint=attempt.payload_fingerprint,
            owner_id=attempt.owner_id,
            attempt_token=attempt.attempt_token,
            fence_epoch=attempt.fence_epoch,
            state=attempt.state,
            outcome=consume.outcome,
            ledger_observed_at_utc=attempt.ledger_observed_at_utc,
        )


def ledger_admission_contract() -> dict[str, object]:
    """Published consume contract. Documenting it does not write AWS."""

    _assert_closed()
    return {
        "schema_version": SPRINT4_SECURITY_LEDGER_SCHEMA_VERSION,
        "table_name": DYNAMODB_LEDGER_TABLE_NAME,
        "consume_api": "TransactWriteItems",
        "condition": CONDITIONAL_PUT_ABSENT,
        "attempt_state": MasterExecutionLedgerState.STARTED.value,
        "marker_prefix": "consume-v1#",
        "marker_ttl": False,
        "attempt_ttl_attribute": "retention_expires_at_epoch",
        "ttl_deletion_is_correctness": False,
        "replay_outcome": MasterExecutionLedgerOutcome.REPLAYED.value,
        "fence_fields": ("state", "fence_epoch", "attempt_token", "owner_id", "payload_fingerprint", "grant_nonce"),
        "grant_nonce_rotates_on_takeover": False,
        "hmac_prevents_replay": False,
        "atomic_admission_is_native_execution": False,
        "distributed_exactly_once": False,
        "ledger_writes_enabled": False,
        "live_aws_enabled": False,
        "max_lease_seconds": MAX_LEASE_SECONDS,
        "host_timeout_reason": HOST_TIMEOUT_REASON,
        "host_cancel_reason": HOST_CANCEL_REASON,
        "required_atomic_single_use_implemented": False,
        "source_authenticity": MasterSourceAuthenticity.NOT_ESTABLISHED.value,
        "execution_authority": MasterRouteExecutionAuthority.DENIED.value,
    }


def ledger_envelope_keys(envelope: MasterExecutionGrantEnvelope) -> tuple[str, str]:
    """Documented DynamoDB key for one grant envelope."""

    _assert_closed()
    if type(envelope) is not MasterExecutionGrantEnvelope:
        raise ContractValidationError("invalid_grant_envelope")
    ledger_envelope = ledger_envelope_from_grant(envelope, principal_ref=envelope.principal_ref)
    if type(ledger_envelope) is not MasterExecutionLedgerEnvelope:
        raise ContractValidationError("invalid_ledger_envelope")
    return ledger_item_key(ledger_envelope)


_assert_closed()


__all__ = [
    "HOST_TIMEOUT_REASON",
    "SPRINT4_ATOMIC_ADMISSION_IS_NATIVE_EXECUTION",
    "SPRINT4_DISTRIBUTED_EXACTLY_ONCE",
    "SPRINT4_LEDGER_WRITES_ENABLED",
    "SPRINT4_LIVE_AWS_ENABLED",
    "SPRINT4_SECURITY_LEDGER_SCHEMA_VERSION",
    "Sprint4AdmissionKind",
    "Sprint4AdmissionResult",
    "Sprint4ConsumptionMarkerView",
    "Sprint4LedgerAudit",
    "Sprint4SecurityLedgerPort",
    "Sprint4TimeoutClass",
    "classify_failure",
    "classify_timeout",
    "enable_sprint4_ledger_writes",
    "execute_live_ledger_write",
    "ledger_admission_contract",
    "ledger_envelope_keys",
]
