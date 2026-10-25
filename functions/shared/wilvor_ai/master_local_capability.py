"""Process-local single-use capability mechanics for offline contract tests.

This module is a private registry for issuance, atomic consumption, expiry,
and session revocation inside one test process. A recorded transition means
only that this registry changed its private state. Each registry issues at
most one capability for a ``(session_id, invocation_id)`` pair.

It is not a production execution authority. Master execution authority stays
``DENIED``. Production authority stays ``DENIED``. Source authenticity stays
``NOT_ESTABLISHED``. A random token, a copied object, a serialized payload,
Python object identity, or an in-memory handle is not distributed production
authorization. No specialist is called.

PROPOSED, not imported and not assumed implemented:
- An execution-scope module may later supply a scope attestation. This
  registry compares ``LocalCapabilityScope`` by exact equality only.
- A planning-session module may later signal session end. ``close_session``
  is a local test hook.
- A gate-resolution module may later observe consumption records. A
  ``TRANSITION_RECORDED`` result is not a gate pass.
- A provenance module may later record consumption. This registry emits no
  provenance.

No model decision, route plan, proposal, planning context, invocation
authorization, result binding, or evidence admission is accepted as authority.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping

from wilvor_ai.contracts import (
    ALLOWED_V1_CAPABILITIES,
    PROVENANCE_TOKEN_MAX_LENGTH,
    AgentCapability,
    ContractValidationError,
    _validate_bounded_text,
)
from wilvor_ai.master_contracts import (
    MasterClassification,
    MasterInvocationAuthorization,
    MasterInvocationRecord,
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterResultBinding,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import (
    MasterEvidenceAdmission,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
)
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterRouteExecutionAuthority,
    MasterRoutePlan,
    MasterRouteProposal,
)
from wilvor_ai.model_contracts import ModelDecision, ModelTurnRequest


LOCAL_CAPABILITY_SCHEMA_VERSION = "wilvor.ai.master_local_capability.v1"
PROCESS_LOCAL_RANDOM_TOKEN_IS_DISTRIBUTED_PRODUCTION_AUTHORIZATION = False
PROCESS_LOCAL_OBJECT_IDENTITY_IS_DISTRIBUTED_PRODUCTION_AUTHORIZATION = False

_ISSUE_PERMIT = object()
_OPAQUE_KIND = "OPAQUE_PROCESS_LOCAL_CAPABILITY"

_PLANNING_TYPES = (
    MasterPlanningContext,
    MasterRoutePlan,
    MasterRouteProposal,
)
_BINDING_TYPES = (
    MasterInvocationAuthorization,
    MasterInvocationRecord,
    MasterResultBinding,
    MasterClassification,
)
_EVIDENCE_TYPES = (
    MasterEvidenceAdmission,
    MasterEvidenceAdmissionInput,
)
_MODEL_TYPES = (
    ModelDecision,
    ModelTurnRequest,
)


class LocalCapabilityState(str, Enum):
    """Private registry state. None of these values permit execution."""

    ISSUED = "ISSUED"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    OPERATION_FAILED = "OPERATION_FAILED"


class LocalConsumptionOutcome(str, Enum):
    """Closed consume result. Transition is registry bookkeeping only."""

    TRANSITION_RECORDED = "TRANSITION_RECORDED"
    REJECTED = "REJECTED"


class LocalRejectionReason(str, Enum):
    """Why a consume or failure record did not authorize anything."""

    UNKNOWN_CAPABILITY = "UNKNOWN_CAPABILITY"
    FOREIGN_REGISTRY = "FOREIGN_REGISTRY"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    INVOCATION_MISMATCH = "INVOCATION_MISMATCH"
    CONSUMER_MISMATCH = "CONSUMER_MISMATCH"
    REPLAY = "REPLAY"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    COPIED_CAPABILITY = "COPIED_CAPABILITY"
    SERIALIZED_CAPABILITY = "SERIALIZED_CAPABILITY"
    OPERATION_FAILED = "OPERATION_FAILED"
    OPERATION_NOT_CONSUMED = "OPERATION_NOT_CONSUMED"
    NOT_REACTIVATED = "NOT_REACTIVATED"
    PLANNING_OBJECT_NOT_AUTHORITY = "PLANNING_OBJECT_NOT_AUTHORITY"
    BINDING_NOT_AUTHORITY = "BINDING_NOT_AUTHORITY"
    EVIDENCE_NOT_AUTHORITY = "EVIDENCE_NOT_AUTHORITY"
    MODEL_OUTPUT_NOT_AUTHORITY = "MODEL_OUTPUT_NOT_AUTHORITY"


def _require_issue_permit(args: tuple[object, ...], kwargs: Mapping[str, object]) -> None:
    if args or set(kwargs) != {"_permit"} or kwargs.get("_permit") is not _ISSUE_PERMIT:
        raise ContractValidationError("capability_constructor_refused")


def _label(value: object, field_name: str) -> str:
    """Accept a bounded test label. The label is not an authenticated principal."""

    errors = _validate_bounded_text(
        value,
        field_name,
        max_length=PROVENANCE_TOKEN_MAX_LENGTH,
    )
    if errors:
        raise ContractValidationError(errors)
    if not isinstance(value, str) or value != value.strip() or any(character.isspace() for character in value):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _utc_instant(value: object, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timezone.utc.utcoffset(None)
    ):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _reject_closed_authority(
    *,
    execution_authority: object,
    production_authority: object,
    source_authenticity: object,
    execution_permitted: object,
    dispatch_permitted: object,
    specialist_dispatched: object,
    distributed_production_authorization: object,
) -> None:
    if execution_authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
    if production_authority is not MasterProductionAuthority.DENIED:
        raise ContractValidationError("production_authority_not_issued")
    if source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
        raise ContractValidationError("source_authenticity_not_established")
    if (
        execution_permitted is not False
        or dispatch_permitted is not False
        or specialist_dispatched is not False
    ):
        raise ContractValidationError("execution_not_granted")
    if distributed_production_authorization is not False:
        raise ContractValidationError("distributed_production_authorization_not_claimed")


@dataclass(frozen=True)
class LocalCapabilityScope:
    """Exact-match limit for one local capability.

    Equality compares every field. An equal scope object is not a capability
    and is not proof of trust. ``agent_capability`` reuses the existing
    capability vocabulary and does not authorize that capability.
    """

    specialist: MasterSpecialist
    purpose: MasterPurpose
    requirement: MasterRequirement
    agent_capability: AgentCapability

    def __post_init__(self) -> None:
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        if not isinstance(self.requirement, MasterRequirement):
            raise ContractValidationError("invalid_requirement")
        if not isinstance(self.agent_capability, AgentCapability):
            raise ContractValidationError("invalid_agent_capability")
        if self.agent_capability not in ALLOWED_V1_CAPABILITIES:
            raise ContractValidationError("prohibited_capability")


def _require_scope(value: object) -> LocalCapabilityScope:
    if type(value) is not LocalCapabilityScope:
        raise ContractValidationError("invalid_scope")
    return value


@dataclass(frozen=True)
class LocalCapabilityDescription:
    """Non-authorizing description of an opaque handle.

    Rebuilding a handle from this description yields an inert object.
    """

    schema_version: str
    kind: str
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    authorizes_execution: bool = False
    distributed_production_authorization: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != LOCAL_CAPABILITY_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if self.kind != _OPAQUE_KIND:
            raise ContractValidationError("invalid_capability_description")
        _reject_closed_authority(
            execution_authority=self.execution_authority,
            production_authority=self.production_authority,
            source_authenticity=self.source_authenticity,
            execution_permitted=self.authorizes_execution,
            dispatch_permitted=False,
            specialist_dispatched=False,
            distributed_production_authorization=self.distributed_production_authorization,
        )


@dataclass(frozen=True)
class LocalConsumptionResult:
    """Result of a local consume or failure record.

    The object is never truthy. ``TRANSITION_RECORDED`` means the private
    registry moved an issued capability to consumed. It does not permit
    execution, dispatch, or a specialist call.
    """

    outcome: LocalConsumptionOutcome
    reason: LocalRejectionReason | None
    local_state_transition_only: bool
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    execution_permitted: bool = False
    dispatch_permitted: bool = False
    specialist_dispatched: bool = False
    distributed_production_authorization: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, LocalConsumptionOutcome):
            raise ContractValidationError("invalid_consumption_outcome")
        _reject_closed_authority(
            execution_authority=self.execution_authority,
            production_authority=self.production_authority,
            source_authenticity=self.source_authenticity,
            execution_permitted=self.execution_permitted,
            dispatch_permitted=self.dispatch_permitted,
            specialist_dispatched=self.specialist_dispatched,
            distributed_production_authorization=self.distributed_production_authorization,
        )
        if self.outcome is LocalConsumptionOutcome.TRANSITION_RECORDED:
            if self.reason is not None or self.local_state_transition_only is not True:
                raise ContractValidationError("invalid_consumption_result")
            return
        if self.outcome is LocalConsumptionOutcome.REJECTED:
            if not isinstance(self.reason, LocalRejectionReason) or self.local_state_transition_only is not False:
                raise ContractValidationError("invalid_consumption_result")
            return
        raise ContractValidationError("invalid_consumption_outcome")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class LocalSessionRevocation:
    """Local session close. Revocation is not execution authority."""

    session_id: str
    revoked_count: int
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    execution_permitted: bool = False
    dispatch_permitted: bool = False
    specialist_dispatched: bool = False
    distributed_production_authorization: bool = False

    def __post_init__(self) -> None:
        _label(self.session_id, "session_id")
        if isinstance(self.revoked_count, bool) or not isinstance(self.revoked_count, int) or self.revoked_count < 0:
            raise ContractValidationError("invalid_revoked_count")
        _reject_closed_authority(
            execution_authority=self.execution_authority,
            production_authority=self.production_authority,
            source_authenticity=self.source_authenticity,
            execution_permitted=self.execution_permitted,
            dispatch_permitted=self.dispatch_permitted,
            specialist_dispatched=self.specialist_dispatched,
            distributed_production_authorization=self.distributed_production_authorization,
        )

    def __bool__(self) -> bool:
        return False


class LocalCapabilityHandle:
    """Opaque handle owned by the registry that created it.

    The public constructor does not mint a capability and does not accept an
    authenticated principal. Copying, deepcopying, pickling, or rebuilding
    from ``description`` produces an inert object. Holding this object, or
    matching its test labels, is not production authorization.
    """

    __slots__ = ("_registry_token", "_handle_token", "_inert_reason")

    def __new__(cls, *args: object, **kwargs: object) -> LocalCapabilityHandle:
        _require_issue_permit(args, kwargs)
        return object.__new__(cls)

    def __init__(self, *args: object, **kwargs: object) -> None:
        _require_issue_permit(args, kwargs)
        self._registry_token: bytes | None = None
        self._handle_token: bytes | None = None
        self._inert_reason: LocalRejectionReason | None = LocalRejectionReason.UNKNOWN_CAPABILITY

    def description(self) -> LocalCapabilityDescription:
        return LocalCapabilityDescription(
            schema_version=LOCAL_CAPABILITY_SCHEMA_VERSION,
            kind=_OPAQUE_KIND,
        )

    @classmethod
    def from_description(cls, description: object) -> LocalCapabilityHandle:
        """Return an inert handle. Description fields are not permission."""

        if not isinstance(description, (LocalCapabilityDescription, Mapping)):
            raise ContractValidationError("invalid_capability_description")
        return _new_inert(LocalRejectionReason.SERIALIZED_CAPABILITY)

    def __repr__(self) -> str:
        return "LocalCapabilityHandle(opaque=True, execution_authority='DENIED')"

    def __copy__(self) -> LocalCapabilityHandle:
        return _new_inert(LocalRejectionReason.COPIED_CAPABILITY)

    def __deepcopy__(self, memo: dict[int, object]) -> LocalCapabilityHandle:
        cloned = _new_inert(LocalRejectionReason.COPIED_CAPABILITY)
        memo[id(self)] = cloned
        return cloned

    def __reduce_ex__(self, protocol: int) -> tuple[object, tuple[object, ...]]:
        del protocol
        return (_reduce_to_serialized_capability, ())


class LocalTestClock:
    """Injected test clock. It is not a trusted operational time source.

    ``advance_to`` may move backward so tests can show that registry state
    does not reactivate when the clock does.
    """

    def __init__(self, instant: datetime) -> None:
        self._instant = _utc_instant(instant, "instant")
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._instant

    def advance_to(self, instant: datetime) -> None:
        checked = _utc_instant(instant, "instant")
        with self._lock:
            self._instant = checked


class _RegistryRecord:
    """Private issuance record. Not a public authorizing dataclass."""

    __slots__ = (
        "handle",
        "scope",
        "invocation_id",
        "consumer_id",
        "session_id",
        "expires_at",
        "state",
    )

    def __init__(
        self,
        handle: LocalCapabilityHandle,
        scope: LocalCapabilityScope,
        invocation_id: str,
        consumer_id: str,
        session_id: str,
        expires_at: datetime,
    ) -> None:
        self.handle = handle
        self.scope = scope
        self.invocation_id = invocation_id
        self.consumer_id = consumer_id
        self.session_id = session_id
        self.expires_at = expires_at
        self.state = LocalCapabilityState.ISSUED


def _new_live(registry_token: bytes, handle_token: bytes) -> LocalCapabilityHandle:
    handle = LocalCapabilityHandle(_permit=_ISSUE_PERMIT)
    handle._registry_token = registry_token
    handle._handle_token = handle_token
    handle._inert_reason = None
    return handle


def _new_inert(reason: LocalRejectionReason) -> LocalCapabilityHandle:
    handle = LocalCapabilityHandle(_permit=_ISSUE_PERMIT)
    handle._registry_token = None
    handle._handle_token = None
    handle._inert_reason = reason
    return handle


def _reduce_to_serialized_capability() -> LocalCapabilityHandle:
    return _new_inert(LocalRejectionReason.SERIALIZED_CAPABILITY)


def _rejected(reason: LocalRejectionReason) -> LocalConsumptionResult:
    return LocalConsumptionResult(
        outcome=LocalConsumptionOutcome.REJECTED,
        reason=reason,
        local_state_transition_only=False,
    )


def _transition_recorded() -> LocalConsumptionResult:
    return LocalConsumptionResult(
        outcome=LocalConsumptionOutcome.TRANSITION_RECORDED,
        reason=None,
        local_state_transition_only=True,
    )


def _non_capability_reason(candidate: object) -> LocalRejectionReason | None:
    if type(candidate) is LocalCapabilityHandle:
        return None
    if isinstance(candidate, LocalCapabilityDescription):
        return LocalRejectionReason.SERIALIZED_CAPABILITY
    if isinstance(candidate, _MODEL_TYPES):
        return LocalRejectionReason.MODEL_OUTPUT_NOT_AUTHORITY
    if isinstance(candidate, _PLANNING_TYPES):
        return LocalRejectionReason.PLANNING_OBJECT_NOT_AUTHORITY
    if isinstance(candidate, _BINDING_TYPES):
        return LocalRejectionReason.BINDING_NOT_AUTHORITY
    if isinstance(candidate, _EVIDENCE_TYPES):
        return LocalRejectionReason.EVIDENCE_NOT_AUTHORITY
    if isinstance(candidate, Mapping):
        return LocalRejectionReason.SERIALIZED_CAPABILITY
    return None


class LocalCapabilityRegistry:
    """Private process-local registry.

    ``issue`` creates one opaque capability. The same lock that publishes the
    record also reserves ``(session_id, invocation_id)``. A second issue for
    that pair is refused for the life of the registry, including after the
    first capability is consumed, expired, revoked, or operation-failed.
    Changing the consumer, scope, or expiry does not create another issuance.
    ``consume`` performs a single atomic issued-to-consumed transition under
    that lock. ``close_session`` revokes capabilities that are still issued
    for that local session.

    ``invocation_id``, ``consumer_id``, and ``session_id`` are exact-match
    test labels. They are not authenticated principals. There is no
    authenticated-principal constructor. This registry has no specialist
    dispatch method and accepts no caller-supplied callable.
    """

    def __init__(self, clock: LocalTestClock) -> None:
        if type(clock) is not LocalTestClock:
            raise ContractValidationError("invalid_clock")
        self._clock = clock
        self._lock = threading.Lock()
        self._token = secrets.token_bytes(32)
        self._records: dict[bytes, _RegistryRecord] = {}
        self._issued_pairs: set[tuple[str, str]] = set()
        self._closed_sessions: set[str] = set()

    def issue(
        self,
        *,
        scope: LocalCapabilityScope,
        invocation_id: str,
        consumer_id: str,
        session_id: str,
        expires_at: datetime,
    ) -> LocalCapabilityHandle:
        """Store one issued capability and return its opaque handle.

        The pair check and the record insert stay inside one lock hold.
        """

        checked_scope = _require_scope(scope)
        checked_invocation = _label(invocation_id, "invocation_id")
        checked_consumer = _label(consumer_id, "consumer_id")
        checked_session = _label(session_id, "session_id")
        checked_expiry = _utc_instant(expires_at, "expires_at")
        pair = (checked_session, checked_invocation)
        with self._lock:
            if pair in self._issued_pairs:
                raise ContractValidationError("duplicate_session_invocation")
            if checked_session in self._closed_sessions:
                raise ContractValidationError("session_closed")
            now = _utc_instant(self._clock.now(), "clock")
            if now >= checked_expiry:
                raise ContractValidationError("capability_already_expired")
            handle_token = secrets.token_bytes(32)
            while handle_token in self._records:
                handle_token = secrets.token_bytes(32)
            handle = _new_live(self._token, handle_token)
            self._issued_pairs.add(pair)
            self._records[handle_token] = _RegistryRecord(
                handle=handle,
                scope=checked_scope,
                invocation_id=checked_invocation,
                consumer_id=checked_consumer,
                session_id=checked_session,
                expires_at=checked_expiry,
            )
            return handle

    def owns(self, handle: object) -> bool:
        """Return whether this registry stored this exact object.

        True is bookkeeping. It is not permission to execute.
        """

        with self._lock:
            return self._refusal(handle) is None

    def observed_state(self, handle: object) -> LocalCapabilityState | None:
        """Return private state for a live owned handle.

        Observation does not consume the capability and does not authorize
        execution. Copies, serializations, and foreign handles return None.
        """

        with self._lock:
            if self._refusal(handle) is not None:
                return None
            return self._record(handle).state

    def consume(
        self,
        handle: object,
        *,
        scope: LocalCapabilityScope,
        invocation_id: str,
        consumer_id: str,
    ) -> LocalConsumptionResult:
        """Atomically consume one issued capability, or reject it.

        The check and the issued-to-consumed write share one lock. A
        successful result records that transition only.
        """

        checked_scope = _require_scope(scope)
        checked_invocation = _label(invocation_id, "invocation_id")
        checked_consumer = _label(consumer_id, "consumer_id")
        with self._lock:
            refusal = self._refusal(handle)
            if refusal is not None:
                return _rejected(refusal)
            return self._consume_issued(
                self._record(handle),
                scope=checked_scope,
                invocation_id=checked_invocation,
                consumer_id=checked_consumer,
            )

    def record_operation_failure(self, handle: object) -> LocalConsumptionResult:
        """Mark a consumed capability as failed without running a callable.

        The capability stays spent. It is not returned to ``ISSUED``.
        Execution stays denied.
        """

        with self._lock:
            refusal = self._refusal(handle)
            if refusal is not None:
                return _rejected(refusal)
            record = self._record(handle)
            if record.state is LocalCapabilityState.CONSUMED:
                record.state = LocalCapabilityState.OPERATION_FAILED
                return _rejected(LocalRejectionReason.OPERATION_FAILED)
            if record.state is LocalCapabilityState.OPERATION_FAILED:
                return _rejected(LocalRejectionReason.NOT_REACTIVATED)
            if record.state is LocalCapabilityState.ISSUED:
                return _rejected(LocalRejectionReason.OPERATION_NOT_CONSUMED)
            if record.state is LocalCapabilityState.EXPIRED:
                return _rejected(LocalRejectionReason.NOT_REACTIVATED)
            if record.state is LocalCapabilityState.REVOKED:
                return _rejected(LocalRejectionReason.REVOKED)
            return _rejected(LocalRejectionReason.UNKNOWN_CAPABILITY)

    def close_session(self, session_id: str) -> LocalSessionRevocation:
        """Revoke every issued capability for this local session id.

        Already consumed capabilities stay consumed. A later issue into the
        closed session is refused. Matching session text in another registry
        is a different session.
        """

        checked_session = _label(session_id, "session_id")
        with self._lock:
            self._closed_sessions.add(checked_session)
            revoked = 0
            for record in self._records.values():
                if record.session_id == checked_session and record.state is LocalCapabilityState.ISSUED:
                    record.state = LocalCapabilityState.REVOKED
                    revoked += 1
            return LocalSessionRevocation(session_id=checked_session, revoked_count=revoked)

    def _record(self, handle: object) -> _RegistryRecord:
        if type(handle) is not LocalCapabilityHandle:
            raise ContractValidationError("invalid_capability")
        token = handle._handle_token
        if token is None:
            raise ContractValidationError("invalid_capability")
        return self._records[token]

    def _refusal(self, handle: object) -> LocalRejectionReason | None:
        classified = _non_capability_reason(handle)
        if classified is not None:
            return classified
        if type(handle) is not LocalCapabilityHandle:
            return LocalRejectionReason.UNKNOWN_CAPABILITY
        try:
            inert_reason = handle._inert_reason
            registry_token = handle._registry_token
            handle_token = handle._handle_token
        except AttributeError:
            return LocalRejectionReason.UNKNOWN_CAPABILITY
        if inert_reason is not None:
            return inert_reason
        if not isinstance(registry_token, bytes) or registry_token != self._token:
            return LocalRejectionReason.FOREIGN_REGISTRY
        if not isinstance(handle_token, bytes):
            return LocalRejectionReason.COPIED_CAPABILITY
        record = self._records.get(handle_token)
        if record is None or record.handle is not handle:
            return LocalRejectionReason.COPIED_CAPABILITY
        return None

    def _consume_issued(
        self,
        record: _RegistryRecord,
        *,
        scope: LocalCapabilityScope,
        invocation_id: str,
        consumer_id: str,
    ) -> LocalConsumptionResult:
        if record.state is LocalCapabilityState.EXPIRED:
            return _rejected(LocalRejectionReason.NOT_REACTIVATED)
        if record.state is LocalCapabilityState.REVOKED:
            return _rejected(LocalRejectionReason.REVOKED)
        if record.state is LocalCapabilityState.CONSUMED:
            return _rejected(LocalRejectionReason.REPLAY)
        if record.state is LocalCapabilityState.OPERATION_FAILED:
            return _rejected(LocalRejectionReason.NOT_REACTIVATED)
        if record.state is not LocalCapabilityState.ISSUED:
            return _rejected(LocalRejectionReason.UNKNOWN_CAPABILITY)
        if record.session_id in self._closed_sessions:
            record.state = LocalCapabilityState.REVOKED
            return _rejected(LocalRejectionReason.REVOKED)
        now = _utc_instant(self._clock.now(), "clock")
        if now >= record.expires_at:
            record.state = LocalCapabilityState.EXPIRED
            return _rejected(LocalRejectionReason.EXPIRED)
        if scope != record.scope:
            return _rejected(LocalRejectionReason.SCOPE_MISMATCH)
        if invocation_id != record.invocation_id:
            return _rejected(LocalRejectionReason.INVOCATION_MISMATCH)
        if consumer_id != record.consumer_id:
            return _rejected(LocalRejectionReason.CONSUMER_MISMATCH)
        record.state = LocalCapabilityState.CONSUMED
        return _transition_recorded()


__all__ = [
    "LOCAL_CAPABILITY_SCHEMA_VERSION",
    "PROCESS_LOCAL_OBJECT_IDENTITY_IS_DISTRIBUTED_PRODUCTION_AUTHORIZATION",
    "PROCESS_LOCAL_RANDOM_TOKEN_IS_DISTRIBUTED_PRODUCTION_AUTHORIZATION",
    "LocalCapabilityDescription",
    "LocalCapabilityHandle",
    "LocalCapabilityRegistry",
    "LocalCapabilityScope",
    "LocalCapabilityState",
    "LocalConsumptionOutcome",
    "LocalConsumptionResult",
    "LocalRejectionReason",
    "LocalSessionRevocation",
    "LocalTestClock",
]
