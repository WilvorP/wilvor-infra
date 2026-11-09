"""NONPRODUCTION composition of a local grant and the execution ledger.

This module calls ``LocalHarnessGrantIssuer`` and ``MasterExecutionLedger``.
It does not issue a production grant, does not authenticate a principal, and
does not make two local writes into distributed exactly-once execution.

``LocalHarnessGrantIssuer.consume`` accepts only ``LocalHarnessConsumptionLedger``.
``MasterExecutionLedger`` generates a different claim nonce and fences attempts
with ``attempt_token`` and ``fence_epoch``. Those writes are not one conditional
item. ``durable_ledger_consumed`` and ``distributed_exactly_once`` stay false.
A DynamoDB adapter type is not verified AWS account or table provenance.
TTL deletion is not first use.

Agent 4, after merge, calls:

``NonproductionHostGrantLedger.consume_local_and_mark_started(claim, *, principal, local_consumption_ledger, owner_id, attempt_token, fence_epoch, now_utc) -> NonproductionBoundaryResult``

``local_consumption_ledger`` must be a ``LocalHarnessConsumptionLedger``.
Passing ``MasterExecutionLedger`` is denied and does not call ``mark_started``.
The method verifies the grant, calls ``mark_started``, and only then consumes the
local double. A rejected start does not consume that double. Local success is
still not durable consumption.
The necessary production seam is ``required_atomic_single_use_interface`` and
``bind_production_grant_to_master_ledger``, both unimplemented.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from enum import Enum

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_contracts import MasterProductionAuthority
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_execution_grants import (
    DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE,
    PRODUCTION_GRANT_ISSUANCE_AVAILABLE,
    GrantRejectionReason,
    LocalHarnessConsumptionLedger,
    LocalHarnessExecutionGrant,
    LocalHarnessGrantIssuer,
    LocalHarnessPrincipal,
    LogicalReadOperation,
    MasterExecutionGrantEnvelope,
    MasterExecutionGrantEvaluation,
    ParsedExecutionGrantClaim,
    bind_production_grant_to_master_ledger,
    evaluate_grant_envelope,
    parse_execution_grant,
)
from wilvor_ai.master_execution_ledger import (
    DURABLE_ADAPTER_TYPE_IS_VERIFIED_AWS_PROVENANCE,
    GRANT_NONCE_ROTATES_ON_TAKEOVER,
    TTL_DELETION_IS_CORRECTNESS,
    LedgerStoreUnavailable,
    MasterExecutionLedger,
    MasterExecutionLedgerEnvelope,
    MasterExecutionLedgerOutcome,
    MasterExecutionLedgerRejection,
    MasterExecutionLedgerResult,
    MasterExecutionLedgerState,
)
from wilvor_ai.master_routing import MasterRouteExecutionAuthority


CONSUMPTION_STARTED_BOUNDARY = (
    "NonproductionHostGrantLedger.consume_local_and_mark_started("
    "claim: object, *, principal: object, local_consumption_ledger: object, "
    "owner_id: str, attempt_token: str, fence_epoch: int, now_utc: str) "
    "-> NonproductionBoundaryResult"
)

DECISION_TARGET_NOT_APPLICABLE = "decision-target-not-applicable"
DISTRIBUTED_EXACTLY_ONCE_ACHIEVED = False
LOCAL_SUCCESS_IS_LOCAL_ONLY = True

_READ_OPERATIONS = frozenset({"GET_ITEM", "QUERY"})


class NonproductionBoundaryOutcome(str, Enum):
    """Local composition outcome. None of these values is production authority."""

    RESERVED_LOCAL = "RESERVED_LOCAL"
    STARTED_LOCAL = "STARTED_LOCAL"
    CANCELLED_LOCAL = "CANCELLED_LOCAL"
    REPLAYED_LOCAL = "REPLAYED_LOCAL"
    DENIED = "DENIED"


def _assert_closed() -> None:
    if PRODUCTION_GRANT_ISSUANCE_AVAILABLE is not False:
        raise ContractValidationError("production_grant_issuance_closed")
    if DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE is not False:
        raise ContractValidationError("ledger_not_integrated")
    if GRANT_NONCE_ROTATES_ON_TAKEOVER is not False:
        raise ContractValidationError("grant_nonce_rotated_on_takeover")
    if DURABLE_ADAPTER_TYPE_IS_VERIFIED_AWS_PROVENANCE is not False:
        raise ContractValidationError("durable_adapter_type_is_not_aws_provenance")
    if TTL_DELETION_IS_CORRECTNESS is not False:
        raise ContractValidationError("ttl_deletion_is_not_correctness")
    if DISTRIBUTED_EXACTLY_ONCE_ACHIEVED is not False or LOCAL_SUCCESS_IS_LOCAL_ONLY is not True:
        raise ContractValidationError("distributed_exactly_once_not_achieved")


@dataclass(frozen=True)
class RequiredAtomicSingleUseInterface:
    """The missing production seam. This object cannot perform it."""

    consume_signature: str
    ledger_reserve_signature: str
    mark_started_signature: str
    reason: str
    implemented: bool = False
    distributed_exactly_once: bool = False

    def __post_init__(self) -> None:
        _assert_closed()
        if self.implemented is not False or self.distributed_exactly_once is not False:
            raise ContractValidationError("ledger_not_integrated")
        if not self.consume_signature or not self.ledger_reserve_signature or not self.reason:
            raise ContractValidationError("ledger_not_integrated")

    def __bool__(self) -> bool:
        return False


def required_atomic_single_use_interface() -> RequiredAtomicSingleUseInterface:
    """Describe the interface atomic single-use would require. It is not available."""

    _assert_closed()
    return RequiredAtomicSingleUseInterface(
        consume_signature=(
            "LocalHarnessGrantIssuer.consume(claim: object, ledger: ReviewedAtomicGrantLedger, "
            "**bindings: object) -> GrantConsumption"
        ),
        ledger_reserve_signature=(
            "ReviewedAtomicGrantLedger.reserve_once(nonce: str, idempotency_key: str, "
            "attempt_token: str, fence_epoch: int) -> bool"
        ),
        mark_started_signature=(
            "MasterExecutionLedger.mark_started(envelope: MasterExecutionLedgerEnvelope, *, "
            "owner_id: str, attempt_token: str, fence_epoch: int, now_utc: str) "
            "-> MasterExecutionLedgerResult"
        ),
        reason=(
            "consume accepts only LocalHarnessConsumptionLedger. reserve_once generates "
            "grant_nonce internally and mark_started does not consume the signed grant. "
            "Those are separate writes. A local reserve_once result must not be reported "
            "as durable consumption. DynamoDbMasterExecutionLedgerStore is not verified "
            "AWS account or table provenance. TTL deletion is not first use."
        ),
    )


@dataclass(frozen=True)
class NonproductionBoundaryResult:
    """One local composition result. Success here is local only."""

    outcome: NonproductionBoundaryOutcome
    fresh_transition: bool
    local_harness_only: bool
    durable_ledger_consumed: bool
    distributed_exactly_once: bool
    verified_aws_provenance: bool
    countable_local_start: bool
    grant_newly_issued: bool
    local_harness_consumption_recorded: bool
    grant_reason: GrantRejectionReason | None = None
    grant_reasons: tuple[GrantRejectionReason, ...] = ()
    ledger_rejection: MasterExecutionLedgerRejection | None = None
    state: MasterExecutionLedgerState | None = None
    attempt_token: str | None = None
    attempt_nonce: str | None = None
    fence_epoch: int | None = None
    grant_nonce: str | None = None
    ledger_grant_nonce: str | None = None
    owner_id: str | None = None
    resource_scope: str | None = None
    region_scope: str | None = None
    decision_target_scope: str | None = None
    policy_revision: str | None = None
    idempotency_key: str | None = None
    deadline_utc: str | None = None
    repeat_non_idempotent_after_expiry: bool | None = None
    store_is_durable_adapter: bool = False
    grant: LocalHarnessExecutionGrant | None = None
    principal_authenticated_by_ledger: bool = False
    decision_target_trusted: bool = False
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    execution_permitted: bool = False
    dispatch_permitted: bool = False
    specialist_execution_permitted: bool = False
    production_grant: bool = False
    hmac_prevents_replay: bool = False
    grant_nonce_rotates_on_takeover: bool = False
    ttl_absence_is_first_use: bool = False

    def __post_init__(self) -> None:
        _assert_closed()
        if not isinstance(self.outcome, NonproductionBoundaryOutcome):
            raise ContractValidationError("invalid_boundary_result")
        if self.local_harness_only is not True:
            raise ContractValidationError("invalid_boundary_result")
        closed = (
            self.durable_ledger_consumed,
            self.distributed_exactly_once,
            self.verified_aws_provenance,
            self.principal_authenticated_by_ledger,
            self.decision_target_trusted,
            self.execution_permitted,
            self.dispatch_permitted,
            self.specialist_execution_permitted,
            self.production_grant,
            self.hmac_prevents_replay,
            self.grant_nonce_rotates_on_takeover,
            self.ttl_absence_is_first_use,
        )
        if any(flag is not False for flag in closed):
            raise ContractValidationError("execution_not_granted")
        if self.execution_authority is not MasterRouteExecutionAuthority.DENIED:
            raise ContractValidationError("execution_authority_not_granted")
        if self.production_authority is not MasterProductionAuthority.DENIED:
            raise ContractValidationError("production_authority_not_issued")
        if self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            raise ContractValidationError("source_authenticity_not_established")
        if type(self.fresh_transition) is not bool or type(self.countable_local_start) is not bool:
            raise ContractValidationError("invalid_boundary_result")
        if type(self.grant_newly_issued) is not bool or type(self.local_harness_consumption_recorded) is not bool:
            raise ContractValidationError("invalid_boundary_result")
        if type(self.store_is_durable_adapter) is not bool:
            raise ContractValidationError("invalid_boundary_result")
        if self.grant is not None and type(self.grant) is not LocalHarnessExecutionGrant:
            raise ContractValidationError("invalid_boundary_result")
        if self.grant_reason is not None and not isinstance(self.grant_reason, GrantRejectionReason):
            raise ContractValidationError("invalid_boundary_result")
        if any(not isinstance(item, GrantRejectionReason) for item in self.grant_reasons):
            raise ContractValidationError("invalid_boundary_result")
        if self.ledger_rejection is not None and not isinstance(self.ledger_rejection, MasterExecutionLedgerRejection):
            raise ContractValidationError("invalid_boundary_result")
        if self.outcome is NonproductionBoundaryOutcome.DENIED or self.outcome is NonproductionBoundaryOutcome.REPLAYED_LOCAL:
            if self.fresh_transition is not False or self.countable_local_start is not False:
                raise ContractValidationError("invalid_boundary_result")
        if self.countable_local_start:
            if (
                self.outcome is not NonproductionBoundaryOutcome.STARTED_LOCAL
                or self.fresh_transition is not True
                or self.local_harness_consumption_recorded is not True
            ):
                raise ContractValidationError("invalid_boundary_result")
        if self.attempt_token is not None and self.attempt_nonce not in {None, self.attempt_token}:
            raise ContractValidationError("invalid_boundary_result")
        if (
            self.grant_nonce is not None
            and self.ledger_grant_nonce is not None
            and self.grant_nonce == self.ledger_grant_nonce
        ):
            raise ContractValidationError("invalid_boundary_result")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class _Binding:
    grant: LocalHarnessExecutionGrant
    principal: LocalHarnessPrincipal
    envelope: MasterExecutionGrantEnvelope
    evaluation: MasterExecutionGrantEvaluation
    ledger_envelope: MasterExecutionLedgerEnvelope
    grant_nonce: str
    ledger_grant_nonce: str
    attempt_token: str
    fence_epoch: int
    owner_id: str


def _resource_token(scope: tuple[LogicalReadOperation, ...]) -> str:
    parts: list[str] = []
    seen: set[tuple[str, str]] = set()
    for item in scope:
        if type(item) is not LogicalReadOperation:
            raise ContractValidationError("invalid_resource_scope")
        operation = item.operation.value
        if operation not in _READ_OPERATIONS:
            raise ContractValidationError("scan_not_permitted")
        if ":" in item.resource_id or "," in item.resource_id or "#" in item.resource_id:
            raise ContractValidationError("invalid_resource_scope")
        identity = (item.resource_id, operation)
        if identity in seen:
            raise ContractValidationError("invalid_resource_scope")
        seen.add(identity)
        parts.append(f"{item.resource_id}:{operation}")
    parts.sort()
    if not parts:
        raise ContractValidationError("invalid_resource_scope")
    return ",".join(parts)


def _region_token(region: tuple[str, ...]) -> str:
    if not region:
        raise ContractValidationError("invalid_region_scope")
    for code in region:
        if not isinstance(code, str) or not code or "," in code or "#" in code or any(ch.isspace() for ch in code):
            raise ContractValidationError("invalid_region_scope")
    return ",".join(region)


def _decision_token(decision: str | None) -> str:
    if decision is None:
        return DECISION_TARGET_NOT_APPLICABLE
    if decision == DECISION_TARGET_NOT_APPLICABLE or "#" in decision or any(ch.isspace() for ch in decision):
        raise ContractValidationError("invalid_decision_target_scope")
    return decision


def _ledger_envelope(
    envelope: MasterExecutionGrantEnvelope,
    evaluation: MasterExecutionGrantEvaluation,
    principal: LocalHarnessPrincipal,
    *,
    repeat_non_idempotent_after_expiry: bool,
) -> MasterExecutionLedgerEnvelope:
    if not evaluation.bounds_accepted or evaluation.resource_scope is None or evaluation.region_scope is None:
        raise ContractValidationError("grant_bounds_rejected")
    if principal.reference != envelope.principal_ref:
        raise ContractValidationError("principal_reference_mismatch")
    window = evaluation.historical_window
    return MasterExecutionLedgerEnvelope(
        master_request_id=envelope.master_request_id,
        planning_session_id=envelope.planning_session_id,
        step_id=envelope.step_id,
        invocation_id=envelope.invocation_id,
        specialist=envelope.specialist,
        purpose=envelope.purpose,
        capability=envelope.capability,
        principal_ref=principal.reference,
        policy_revision=envelope.policy_revision,
        resource_scope=_resource_token(evaluation.resource_scope),
        region_scope=_region_token(evaluation.region_scope),
        decision_target_scope=_decision_token(evaluation.decision_target_scope),
        accepted_at_utc=envelope.accepted_at_utc,
        deadline_utc=envelope.deadline_utc,
        idempotency_key=envelope.idempotency_key,
        max_model_turns=envelope.max_model_turns,
        max_tool_calls=envelope.max_tool_calls,
        correlation_id=envelope.correlation_id,
        historical_start_utc=None if window is None else window[0],
        historical_end_utc=None if window is None else window[1],
        repeat_non_idempotent_after_expiry=repeat_non_idempotent_after_expiry,
    )


def _verify_kwargs(binding: _Binding) -> dict[str, object]:
    evaluation = binding.evaluation
    envelope = binding.envelope
    if (
        evaluation.tool_name is None
        or evaluation.resource_scope is None
        or evaluation.argument_names is None
        or evaluation.region_scope is None
        or evaluation.temporal_scopes is None
    ):
        raise ContractValidationError("grant_bounds_rejected")
    return {
        "principal": binding.principal,
        "planning_session_id": envelope.planning_session_id,
        "step_id": envelope.step_id,
        "invocation_id": envelope.invocation_id,
        "specialist": envelope.specialist,
        "purpose": envelope.purpose,
        "capability": envelope.capability,
        "policy_revision": envelope.policy_revision,
        "tool_name": evaluation.tool_name,
        "resource_scope": evaluation.resource_scope,
        "argument_names": evaluation.argument_names,
        "region_scope": evaluation.region_scope,
        "temporal_scopes": evaluation.temporal_scopes,
        "historical_window": evaluation.historical_window,
        "decision_target_scope": evaluation.decision_target_scope,
        "accepted_at_utc": envelope.accepted_at_utc,
        "deadline_utc": envelope.deadline_utc,
        "expires_at_utc": envelope.expires_at_utc,
        "idempotency_key": envelope.idempotency_key,
        "max_model_turns": envelope.max_model_turns,
        "max_tool_calls": envelope.max_tool_calls,
        "correlation_id": envelope.correlation_id,
        "master_request_id": envelope.master_request_id,
    }


def _nonce_of(claim: object) -> str | None:
    try:
        if type(claim) is LocalHarnessExecutionGrant:
            return parse_execution_grant(claim.serialized_claim()).nonce
        if type(claim) is ParsedExecutionGrantClaim:
            return claim.nonce
    except ContractValidationError:
        return None
    return None


def _denied(
    *,
    grant_reason: GrantRejectionReason | None = None,
    grant_reasons: tuple[GrantRejectionReason, ...] = (),
    ledger_rejection: MasterExecutionLedgerRejection | None = None,
    state: MasterExecutionLedgerState | None = None,
    local_harness_consumption_recorded: bool = False,
) -> NonproductionBoundaryResult:
    reasons = grant_reasons if grant_reasons else (() if grant_reason is None else (grant_reason,))
    return NonproductionBoundaryResult(
        outcome=NonproductionBoundaryOutcome.DENIED,
        fresh_transition=False,
        local_harness_only=True,
        durable_ledger_consumed=False,
        distributed_exactly_once=False,
        verified_aws_provenance=False,
        countable_local_start=False,
        grant_newly_issued=False,
        local_harness_consumption_recorded=local_harness_consumption_recorded,
        grant_reason=reasons[0] if reasons else None,
        grant_reasons=reasons,
        ledger_rejection=ledger_rejection,
        state=state,
    )


def _from_binding(
    binding: _Binding,
    ledger_result: MasterExecutionLedgerResult,
    *,
    outcome: NonproductionBoundaryOutcome,
    grant_newly_issued: bool,
    local_harness_consumption_recorded: bool,
    countable_local_start: bool,
) -> NonproductionBoundaryResult:
    fresh = ledger_result.fresh_transition is True and outcome is not NonproductionBoundaryOutcome.REPLAYED_LOCAL
    if ledger_result.replayed:
        outcome = NonproductionBoundaryOutcome.REPLAYED_LOCAL
        fresh = False
        countable_local_start = False
        grant_newly_issued = False
    if ledger_result.grant_nonce != binding.ledger_grant_nonce:
        raise ContractValidationError("grant_nonce_rotated_on_takeover")
    if ledger_result.attempt_token != binding.attempt_token or ledger_result.fence_epoch != binding.fence_epoch:
        raise ContractValidationError("invalid_fence_epoch")
    return NonproductionBoundaryResult(
        outcome=outcome,
        fresh_transition=fresh,
        local_harness_only=True,
        durable_ledger_consumed=False,
        distributed_exactly_once=False,
        verified_aws_provenance=False,
        countable_local_start=countable_local_start and fresh,
        grant_newly_issued=grant_newly_issued and fresh,
        local_harness_consumption_recorded=local_harness_consumption_recorded,
        state=ledger_result.state,
        attempt_token=ledger_result.attempt_token,
        attempt_nonce=ledger_result.attempt_token,
        fence_epoch=ledger_result.fence_epoch,
        grant_nonce=binding.grant_nonce,
        ledger_grant_nonce=binding.ledger_grant_nonce,
        owner_id=ledger_result.owner_id,
        resource_scope=binding.ledger_envelope.resource_scope,
        region_scope=binding.ledger_envelope.region_scope,
        decision_target_scope=binding.ledger_envelope.decision_target_scope,
        policy_revision=binding.ledger_envelope.policy_revision,
        idempotency_key=binding.ledger_envelope.idempotency_key,
        deadline_utc=binding.ledger_envelope.deadline_utc,
        repeat_non_idempotent_after_expiry=binding.ledger_envelope.repeat_non_idempotent_after_expiry,
        store_is_durable_adapter=ledger_result.store_is_durable_adapter,
        grant=binding.grant,
    )


class NonproductionHostGrantLedger:
    """Offline host that binds one local grant to one ledger claim.

    Production issuance stays closed. Local consumption is labeled local.
    """

    def __init__(self, *, issuer: object, ledger: object) -> None:
        _assert_closed()
        if type(issuer) is not LocalHarnessGrantIssuer or type(ledger) is not MasterExecutionLedger:
            raise ContractValidationError("invalid_grant_ledger_host")
        if issuer.production_grants_issued != 0:
            raise ContractValidationError("production_grant_issuance_closed")
        if ledger.production_cross_instance_authority is not False:
            raise ContractValidationError("production_authority_not_issued")
        if ledger.verified_aws_account_and_table_provenance is not False:
            raise ContractValidationError("durable_adapter_type_is_not_aws_provenance")
        if ledger.grant_nonce_rotates_on_takeover is not False:
            raise ContractValidationError("grant_nonce_rotated_on_takeover")
        self._issuer = issuer
        self._ledger = ledger
        self._lock = threading.Lock()
        self._bindings: dict[tuple[str, str], _Binding] = {}
        self._by_nonce: dict[str, tuple[str, str]] = {}

    @property
    def distributed_exactly_once_achieved(self) -> bool:
        return False

    @property
    def consumption_started_boundary(self) -> str:
        return CONSUMPTION_STARTED_BOUNDARY

    def reserve_aligned(
        self,
        envelope: object,
        *,
        principal: object,
        owner_id: str,
        now_utc: str,
        lease_seconds: int,
        repeat_non_idempotent_after_expiry: bool = False,
    ) -> NonproductionBoundaryResult:
        """Evaluate, reserve the ledger, and issue one local grant on first claim.

        A replay does not issue a second grant. Takeover rotates the attempt
        token and fence inside the ledger and keeps both the claim nonce and
        the already issued local grant. Default repeat of non-idempotent work
        is false.
        """

        _assert_closed()
        if type(repeat_non_idempotent_after_expiry) is not bool:
            raise ContractValidationError("invalid_repeat_policy")
        if type(envelope) is not MasterExecutionGrantEnvelope:
            raise ContractValidationError("invalid_grant_envelope")
        evaluation = evaluate_grant_envelope(envelope)
        if not evaluation.bounds_accepted:
            return _denied(grant_reasons=evaluation.reasons)
        if type(principal) is not LocalHarnessPrincipal:
            raise ContractValidationError("principal_not_sealed")
        ticket = self._issuer.prepare(envelope, principal)
        ledger_envelope = _ledger_envelope(
            envelope,
            evaluation,
            principal,
            repeat_non_idempotent_after_expiry=repeat_non_idempotent_after_expiry,
        )
        key = (ledger_envelope.partition_key, ledger_envelope.sort_key)
        with self._lock:
            existing = self._bindings.get(key)
        if existing is not None and existing.principal is not principal:
            return _denied(grant_reason=GrantRejectionReason.PRINCIPAL_MISMATCH)
        reserved = self._ledger.reserve_once(
            ledger_envelope,
            owner_id=owner_id,
            now_utc=now_utc,
            lease_seconds=lease_seconds,
        )
        if reserved.outcome is MasterExecutionLedgerOutcome.REJECTED:
            return _denied(ledger_rejection=reserved.rejection)
        if reserved.grant_nonce is None or reserved.attempt_token is None or reserved.fence_epoch is None:
            raise LedgerStoreUnavailable("durable_storage_unavailable")
        if self._ledger.verified_aws_account_and_table_provenance is not False:
            raise ContractValidationError("durable_adapter_type_is_not_aws_provenance")
        with self._lock:
            binding = self._bindings.get(key)
            if reserved.outcome is MasterExecutionLedgerOutcome.CLAIMED and binding is None:
                grant = self._issuer.issue(ticket)
                grant_nonce = parse_execution_grant(grant.serialized_claim()).nonce
                if grant_nonce == reserved.grant_nonce or grant_nonce == reserved.attempt_token:
                    raise ContractValidationError("invalid_boundary_result")
                binding = _Binding(
                    grant=grant,
                    principal=principal,
                    envelope=envelope,
                    evaluation=evaluation,
                    ledger_envelope=ledger_envelope,
                    grant_nonce=grant_nonce,
                    ledger_grant_nonce=reserved.grant_nonce,
                    attempt_token=reserved.attempt_token,
                    fence_epoch=reserved.fence_epoch,
                    owner_id=owner_id,
                )
                self._bindings[key] = binding
                self._by_nonce[grant_nonce] = key
                return _from_binding(
                    binding,
                    reserved,
                    outcome=NonproductionBoundaryOutcome.RESERVED_LOCAL,
                    grant_newly_issued=True,
                    local_harness_consumption_recorded=False,
                    countable_local_start=False,
                )
            if binding is None or binding.ledger_grant_nonce != reserved.grant_nonce:
                return _denied(ledger_rejection=MasterExecutionLedgerRejection.FENCED_OUT)
            if binding.principal is not principal:
                return _denied(grant_reason=GrantRejectionReason.PRINCIPAL_MISMATCH)
            if (
                binding.attempt_token != reserved.attempt_token
                or binding.fence_epoch != reserved.fence_epoch
                or binding.owner_id != owner_id
            ):
                binding = replace(
                    binding,
                    attempt_token=reserved.attempt_token,
                    fence_epoch=reserved.fence_epoch,
                    owner_id=owner_id,
                )
                self._bindings[key] = binding
            outcome = (
                NonproductionBoundaryOutcome.RESERVED_LOCAL
                if reserved.fresh_transition
                else NonproductionBoundaryOutcome.REPLAYED_LOCAL
            )
            return _from_binding(
                binding,
                reserved,
                outcome=outcome,
                grant_newly_issued=False,
                local_harness_consumption_recorded=False,
                countable_local_start=False,
            )

    def consume_local_and_mark_started(
        self,
        claim: object,
        *,
        principal: object,
        local_consumption_ledger: object,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        now_utc: str,
    ) -> NonproductionBoundaryResult:
        """Verify the local grant, mark the current attempt started, then consume locally.

        This is the Agent 4 boundary. It never reports durable consumption.
        A stale attempt token or fence is rejected before ``mark_started``.
        A rejected start does not reserve the local nonce. ``MasterExecutionLedger``
        is not a consumption ledger.
        """

        _assert_closed()
        binding = self._binding_for(claim)
        if binding is None:
            return _denied(grant_reason=GrantRejectionReason.FORGED)
        if principal is not binding.principal:
            return _denied(grant_reason=GrantRejectionReason.PRINCIPAL_MISMATCH)
        if type(local_consumption_ledger) is not LocalHarnessConsumptionLedger:
            consumption = self._issuer.consume(claim, local_consumption_ledger, **_verify_kwargs(binding))
            self._refuse_durable_bind(claim, local_consumption_ledger)
            return _denied(
                grant_reason=consumption.reason or GrantRejectionReason.LEDGER_NOT_INTEGRATED,
                state=None,
            )
        check = self._ledger.fenced_attempt_matches(
            binding.ledger_envelope,
            owner_id=owner_id,
            attempt_token=attempt_token,
            fence_epoch=fence_epoch,
            grant_nonce=binding.ledger_grant_nonce,
        )
        if not check.matches_current_attempt or check.authorizes_execution is not False:
            return _denied(ledger_rejection=check.rejection, state=check.state)
        if check.state not in {MasterExecutionLedgerState.RESERVED, MasterExecutionLedgerState.STARTED}:
            return _denied(
                ledger_rejection=MasterExecutionLedgerRejection.INVALID_TRANSITION,
                state=check.state,
            )
        verified = self._issuer.verify(claim, **_verify_kwargs(binding))
        replay_of_local = verified.reason is GrantRejectionReason.REPLAY and local_consumption_ledger.holds_nonce(
            binding.grant_nonce
        )
        if not verified.local_harness_binding_accepted and not replay_of_local:
            return _denied(grant_reason=verified.reason, state=check.state)
        started = self._ledger.mark_started(
            binding.ledger_envelope,
            owner_id=owner_id,
            attempt_token=attempt_token,
            fence_epoch=fence_epoch,
            now_utc=now_utc,
        )
        if started.outcome is MasterExecutionLedgerOutcome.REJECTED:
            return _denied(ledger_rejection=started.rejection, state=check.state)
        consumption = self._issuer.consume(claim, local_consumption_ledger, **_verify_kwargs(binding))
        if consumption.durable_single_use is not False or consumption.distributed_ledger is not False:
            raise ContractValidationError("ledger_not_integrated")
        holds = local_consumption_ledger.holds_nonce(binding.grant_nonce)
        local_recorded = consumption.local_harness_consumption_recorded or (
            consumption.reason is GrantRejectionReason.REPLAY and holds
        )
        if not local_recorded:
            return _denied(grant_reason=consumption.reason, state=started.state)
        countable = started.fresh_transition is True and started.state is MasterExecutionLedgerState.STARTED
        return _from_binding(
            binding,
            started,
            outcome=NonproductionBoundaryOutcome.STARTED_LOCAL,
            grant_newly_issued=False,
            local_harness_consumption_recorded=local_recorded,
            countable_local_start=countable,
        )

    def cancel_reserved(
        self,
        claim: object,
        *,
        principal: object,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        now_utc: str,
    ) -> NonproductionBoundaryResult:
        """Cancel a reserved attempt. This does not consume the local grant."""

        _assert_closed()
        binding = self._binding_for(claim)
        if binding is None:
            return _denied(grant_reason=GrantRejectionReason.FORGED)
        if principal is not binding.principal:
            return _denied(grant_reason=GrantRejectionReason.PRINCIPAL_MISMATCH)
        cancelled = self._ledger.cancel(
            binding.ledger_envelope,
            owner_id=owner_id,
            attempt_token=attempt_token,
            fence_epoch=fence_epoch,
            now_utc=now_utc,
        )
        if cancelled.outcome is MasterExecutionLedgerOutcome.REJECTED:
            return _denied(ledger_rejection=cancelled.rejection)
        return _from_binding(
            binding,
            cancelled,
            outcome=NonproductionBoundaryOutcome.CANCELLED_LOCAL,
            grant_newly_issued=False,
            local_harness_consumption_recorded=False,
            countable_local_start=False,
        )

    def _binding_for(self, claim: object) -> _Binding | None:
        nonce = _nonce_of(claim)
        if nonce is None:
            return None
        with self._lock:
            key = self._by_nonce.get(nonce)
            if key is None:
                return None
            return self._bindings.get(key)

    def _refuse_durable_bind(self, claim: object, ledger: object) -> None:
        try:
            bind_production_grant_to_master_ledger(claim, ledger)
        except ContractValidationError as exc:
            if "ledger_not_integrated" not in exc.errors:
                raise
            return
        raise ContractValidationError("ledger_not_integrated")


_assert_closed()


__all__ = [
    "CONSUMPTION_STARTED_BOUNDARY",
    "DECISION_TARGET_NOT_APPLICABLE",
    "DISTRIBUTED_EXACTLY_ONCE_ACHIEVED",
    "LOCAL_SUCCESS_IS_LOCAL_ONLY",
    "NonproductionBoundaryOutcome",
    "NonproductionBoundaryResult",
    "NonproductionHostGrantLedger",
    "RequiredAtomicSingleUseInterface",
    "required_atomic_single_use_interface",
]
