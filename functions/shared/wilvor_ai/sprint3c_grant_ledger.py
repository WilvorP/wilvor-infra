"""Sprint 3C durable grant consumption and execution-ledger fencing.

This module implements one authoritative claim/consume transition: a
DynamoDB ``TransactWriteItems`` that writes a ``STARTED`` attempt item
bound to the signed grant nonce and a no-TTL consumption marker. The
signed nonce is the ledger nonce. Reservation, start, and local
consumption are not three writes.

A successful transaction fences one attempt. It does not execute a
specialist and is not distributed exactly-once external side effects.
TTL deletion of the attempt item cannot recreate authority while the
marker remains. Production grant issuance stays closed. Synthetic
principals, DENY policy evaluations, missing signers, missing
principals, missing ledger clients, and unverified AWS account/table
provenance cannot authorize a production grant.

``LocalHarnessGrantIssuer.consume`` is unchanged and still rejects
``MasterExecutionLedger``. ``bind_production_grant_to_master_ledger``
still raises. Closed production flags stay false.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    AgentCapability,
    ContractValidationError,
    _validate_bounded_text,
)
from wilvor_ai.master_application_principal import PrincipalRecordKind
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_execution_grants import (
    DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE,
    HMAC_PREVENTS_REPLAY,
    PRODUCTION_GRANT_ISSUANCE_AVAILABLE,
    PRODUCTION_SIGNING_KEY_STORE_INTEGRATED,
    GrantLifecycle,
    GrantRejectionReason,
    LocalHarnessConsumptionLedger,
    LocalHarnessExecutionGrant,
    LocalHarnessGrantIssuer,
    LocalHarnessPrincipal,
    LogicalReadOperation,
    MasterExecutionGrantEnvelope,
    ParsedExecutionGrantClaim,
    bind_production_grant_to_master_ledger,
    evaluate_grant_envelope,
    issue_production_execution_grant,
    parse_execution_grant,
)
from wilvor_ai.master_execution_ledger import (
    CONDITIONAL_PUT_ABSENT,
    DURABLE_ADAPTER_TYPE_IS_VERIFIED_AWS_PROVENANCE,
    DYNAMODB_LEDGER_TABLE_NAME,
    GRANT_NONCE_ROTATES_ON_TAKEOVER,
    MAX_LEASE_SECONDS,
    TTL_DELETION_IS_CORRECTNESS,
    DynamoDbMasterExecutionLedgerStore,
    LedgerStoreUnavailable,
    MasterExecutionAttemptFenceCheck,
    MasterExecutionLedger,
    MasterExecutionLedgerEnvelope,
    MasterExecutionLedgerOutcome,
    MasterExecutionLedgerRejection,
    MasterExecutionLedgerResult,
    MasterExecutionLedgerState,
    build_started_record_for_signed_nonce,
    encode_ledger_record_item,
    ledger_item_key,
)
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.master_routing import MasterRequestCoverage, MasterRouteExecutionAuthority


SPRINT3C_GRANT_LEDGER_SCHEMA_VERSION = "wilvor.ai.sprint3c.grant_ledger.v1"
CONSUME_MARKER_SCHEMA_VERSION = "wilvor.ai.sprint3c.grant_consume_marker.v1"
SPRINT3C_PRODUCTION_GRANT_LEDGER_ENABLED = False
SPRINT3C_DISTRIBUTED_EXACTLY_ONCE = False
DECISION_TARGET_NOT_APPLICABLE = "decision-target-not-applicable"
HOST_CANCEL_REASON = "host_cancelled"
_READ_OPERATIONS = frozenset({"GET_ITEM", "QUERY"})


def _assert_closed() -> None:
    if PRODUCTION_GRANT_ISSUANCE_AVAILABLE is not False:
        raise ContractValidationError("production_grant_issuance_closed")
    if DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE is not False:
        raise ContractValidationError("ledger_not_integrated")
    if PRODUCTION_SIGNING_KEY_STORE_INTEGRATED is not False:
        raise ContractValidationError("production_grant_issuance_closed")
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


def _lock_closed(
    *,
    production_grant: object,
    distributed_exactly_once: object,
    verified_aws_provenance: object,
    specialist_executed: object,
    execution_permitted: object,
    dispatch_permitted: object,
    execution_authority: object,
    production_authority: object,
    source_authenticity: object,
) -> None:
    if any(
        flag is not False
        for flag in (
            production_grant,
            distributed_exactly_once,
            verified_aws_provenance,
            specialist_executed,
            execution_permitted,
            dispatch_permitted,
        )
    ):
        raise ContractValidationError("execution_not_granted")
    if execution_authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
    if production_authority is not MasterProductionAuthority.DENIED:
        raise ContractValidationError("production_authority_not_issued")
    if source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
        raise ContractValidationError("source_authenticity_not_established")


def signed_grant_nonce(claim: object) -> str:
    """Read the signed nonce. Parsing is not verification or consumption."""

    if type(claim) is LocalHarnessExecutionGrant:
        return parse_execution_grant(claim.serialized_claim()).nonce
    if type(claim) is ParsedExecutionGrantClaim:
        return claim.nonce
    raise ContractValidationError("invalid_grant_claim")


def _resource_token(scope: tuple[LogicalReadOperation, ...]) -> str:
    parts: list[str] = []
    seen: set[tuple[str, str]] = set()
    for item in scope:
        if type(item) is not LogicalReadOperation:
            raise ContractValidationError("invalid_resource_scope")
        operation = item.operation.value
        if operation not in _READ_OPERATIONS or item.operation is ReadOperation.SCAN:
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


def ledger_envelope_from_grant(
    envelope: MasterExecutionGrantEnvelope,
    *,
    principal_ref: str,
    repeat_non_idempotent_after_expiry: bool = False,
) -> MasterExecutionLedgerEnvelope:
    """Project a bounds-accepted grant envelope into a ledger envelope."""

    evaluation = evaluate_grant_envelope(envelope)
    if not evaluation.bounds_accepted or evaluation.resource_scope is None or evaluation.region_scope is None:
        raise ContractValidationError("grant_bounds_rejected")
    window = evaluation.historical_window
    return MasterExecutionLedgerEnvelope(
        master_request_id=envelope.master_request_id,
        planning_session_id=envelope.planning_session_id,
        step_id=envelope.step_id,
        invocation_id=envelope.invocation_id,
        specialist=envelope.specialist,
        purpose=envelope.purpose,
        capability=envelope.capability,
        principal_ref=principal_ref,
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


def consumption_marker_key(grant_nonce: str, envelope: MasterExecutionLedgerEnvelope) -> tuple[str, str]:
    """Primary key for the no-TTL consume marker. Absence of TTL is the point."""

    nonce = _token(grant_nonce, "grant_nonce")
    if "#" in nonce:
        raise ContractValidationError("invalid_grant_nonce")
    return (
        f"consume-v1#{nonce}",
        (
            f"{envelope.principal_ref}#{envelope.planning_session_id}#"
            f"{envelope.master_request_id}#{envelope.invocation_id}#{envelope.purpose.value}"
        ),
    )


def encode_consumption_marker(
    *,
    grant_nonce: str,
    envelope: MasterExecutionLedgerEnvelope,
    consumed_at_utc: str,
) -> dict[str, dict[str, object]]:
    """Encode the durable consume marker. The item has no TTL attribute."""

    pk, sk = consumption_marker_key(grant_nonce, envelope)
    return {
        "pk": {"S": pk},
        "sk": {"S": sk},
        "schema_version": {"S": CONSUME_MARKER_SCHEMA_VERSION},
        "grant_nonce": {"S": grant_nonce},
        "idempotency_key": {"S": envelope.idempotency_key},
        "payload_fingerprint": {"S": envelope.fingerprint},
        "principal_ref": {"S": envelope.principal_ref},
        "planning_session_id": {"S": envelope.planning_session_id},
        "master_request_id": {"S": envelope.master_request_id},
        "invocation_id": {"S": envelope.invocation_id},
        "purpose": {"S": envelope.purpose.value},
        "consumed_at_utc": {"S": consumed_at_utc},
        "specialist_executed": {"BOOL": False},
        "ttl_deletion_is_correctness": {"BOOL": False},
        "production_grant": {"BOOL": False},
        "distributed_exactly_once": {"BOOL": False},
        "verified_aws_provenance": {"BOOL": False},
    }


def _transaction_canceled(exc: BaseException) -> bool:
    if type(exc).__name__ == "TransactionCanceledException":
        return True
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        error = response.get("Error")
        if isinstance(error, Mapping) and error.get("Code") == "TransactionCanceledException":
            return True
    return False


def _absent_put(item: Mapping[str, dict[str, object]]) -> dict[str, object]:
    return {
        "Put": {
            "TableName": DYNAMODB_LEDGER_TABLE_NAME,
            "Item": dict(item),
            "ConditionExpression": CONDITIONAL_PUT_ABSENT,
            "ExpressionAttributeNames": {"#pk": "pk", "#sk": "sk"},
        }
    }


@dataclass(frozen=True)
class Sprint3CIdentityBinding:
    """Fields Agent A shares with this ledger. Not a production principal.

    ``verified_identity`` and ``integration_owner_binding`` stay false in
    this sprint. A DENY policy evaluation is recorded, not issued as a grant.
    """

    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    principal_ref: str
    policy_revision: str
    purpose: MasterPurpose
    accepted_at_utc: str
    deadline_utc: str
    policy_decision: str
    authority_origin: str
    production_principal: bool = False
    record_kind: str = PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION.value
    verified_identity: bool = False
    integration_owner_binding: bool = False

    def __post_init__(self) -> None:
        _assert_closed()
        _token(self.master_request_id, "master_request_id")
        _token(self.planning_session_id, "planning_session_id")
        _token(self.step_id, "step_id")
        _token(self.invocation_id, "invocation_id")
        _token(self.principal_ref, "principal_ref")
        _token(self.policy_revision, "policy_revision")
        if not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        _token(self.accepted_at_utc, "accepted_at_utc")
        _token(self.deadline_utc, "deadline_utc")
        if self.policy_decision not in {"DENY", "ALLOW"}:
            raise ContractValidationError("invalid_policy_decision")
        _token(self.authority_origin, "authority_origin")
        _token(self.record_kind, "record_kind")
        if type(self.production_principal) is not bool:
            raise ContractValidationError("invalid_identity_binding")
        if type(self.verified_identity) is not bool or type(self.integration_owner_binding) is not bool:
            raise ContractValidationError("invalid_identity_binding")
        if self.production_principal is not False:
            raise ContractValidationError("production_principal_issuance_unavailable")
        if self.verified_identity is not False or self.integration_owner_binding is not False:
            raise ContractValidationError("verified_identity_not_established")

    def __bool__(self) -> bool:
        return False


def bind_recorded_deny_identity(handoff: object) -> Sprint3CIdentityBinding:
    """Record Agent A's sealed handoff. A DENY evaluation is not a grant.

    ``planning_session_id`` is the original planning session, not the policy
    session label. ``verified_identity`` stays false. This function does not
    issue, consume, or promote the evaluation.
    """

    from wilvor_ai.master_runtime_policy import MasterRuntimePolicyDecision, PolicyAuthorityOrigin
    from wilvor_ai.sprint3c_ingress_principal import Sprint3CPrincipalPolicyHandoff

    _assert_closed()
    if type(handoff) is not Sprint3CPrincipalPolicyHandoff:
        raise ContractValidationError("invalid_identity_binding")
    if handoff.grant_eligible is not False or handoff.production_principal is not False:
        raise ContractValidationError("policy_evaluation_is_not_a_permit")
    if handoff.policy_decision is not MasterRuntimePolicyDecision.DENY:
        raise ContractValidationError("policy_evaluation_is_not_a_permit")
    if handoff.authority_origin is not PolicyAuthorityOrigin.NOT_ESTABLISHED:
        raise ContractValidationError("principal_not_host_authenticated")
    if handoff.policy_evaluation.decision is not MasterRuntimePolicyDecision.DENY:
        raise ContractValidationError("policy_evaluation_is_not_a_permit")
    if handoff.policy_evaluation.authority_origin is not PolicyAuthorityOrigin.NOT_ESTABLISHED:
        raise ContractValidationError("principal_not_host_authenticated")
    if handoff.policy_evaluation.principal_ref is not None:
        raise ContractValidationError("principal_not_host_authenticated")
    if handoff.planning_session_id == handoff.policy_evaluation.planning_session_id:
        raise ContractValidationError("matching_identifier_not_proof")
    if handoff.principal.record_kind is not PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION:
        raise ContractValidationError("synthetic_admission_not_production_principal")
    if handoff.principal_ref != handoff.principal.principal_ref:
        raise ContractValidationError("principal_descriptor_immutable")
    return Sprint3CIdentityBinding(
        master_request_id=handoff.master_request_id,
        planning_session_id=handoff.planning_session_id,
        step_id=handoff.step_id,
        invocation_id=handoff.invocation_id,
        principal_ref=handoff.principal_ref,
        policy_revision=handoff.policy_revision,
        purpose=handoff.purpose,
        accepted_at_utc=handoff.accepted_at_utc,
        deadline_utc=handoff.deadline_utc,
        policy_decision=handoff.policy_decision.value,
        authority_origin=handoff.authority_origin.value,
        production_principal=False,
        record_kind=handoff.principal.record_kind.value,
        verified_identity=False,
        integration_owner_binding=False,
    )


def agent_a_identity_fixture(**overrides: object) -> Sprint3CIdentityBinding:
    """CP2 identity fixture. Matches Agent A's frozen shared fields.

    A missing Agent A module is represented by these fields, not by a
    copied production grant. Policy output stays ``DENY``.
    """

    values: dict[str, object] = {
        "master_request_id": "request-1",
        "planning_session_id": "session-1",
        "step_id": "step-1",
        "invocation_id": "invocation-1",
        "principal_ref": "principal-1",
        "policy_revision": "policy-rev-1",
        "purpose": MasterPurpose.ANSWER,
        "accepted_at_utc": "2026-10-27T19:00:00Z",
        "deadline_utc": "2026-10-27T21:00:00Z",
        "policy_decision": "DENY",
        "authority_origin": "NOT_ESTABLISHED",
        "production_principal": False,
        "record_kind": PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION.value,
        "verified_identity": False,
        "integration_owner_binding": False,
    }
    values.update(overrides)
    return Sprint3CIdentityBinding(**values)  # type: ignore[arg-type]


@dataclass(frozen=True)
class DurableConsumedAttempt:
    """Consumed-attempt record for Agents C and D.

    ``distributed_exactly_once`` is always false. A ledger write is not
    native specialist execution.
    """

    owner_id: str
    attempt_token: str
    fence_epoch: int
    grant_nonce: str
    idempotency_key: str
    payload_fingerprint: str
    state: MasterExecutionLedgerState
    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    principal_ref: str
    policy_revision: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    resource_scope: str
    region_scope: str
    deadline_utc: str
    correlation_id: str
    lease_expires_at_utc: str
    accepted_at_utc: str
    ledger_observed_at_utc: str
    distributed_exactly_once: bool = False
    production_grant: bool = False
    verified_aws_provenance: bool = False
    specialist_executed: bool = False
    execution_permitted: bool = False
    dispatch_permitted: bool = False
    grant_nonce_rotates_on_takeover: bool = False
    ttl_absence_is_first_use: bool = False
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED

    def __post_init__(self) -> None:
        _assert_closed()
        if not isinstance(self.state, MasterExecutionLedgerState):
            raise ContractValidationError("invalid_ledger_state")
        _lock_closed(
            production_grant=self.production_grant,
            distributed_exactly_once=self.distributed_exactly_once,
            verified_aws_provenance=self.verified_aws_provenance,
            specialist_executed=self.specialist_executed,
            execution_permitted=self.execution_permitted,
            dispatch_permitted=self.dispatch_permitted,
            execution_authority=self.execution_authority,
            production_authority=self.production_authority,
            source_authenticity=self.source_authenticity,
        )
        if self.grant_nonce_rotates_on_takeover is not False or self.ttl_absence_is_first_use is not False:
            raise ContractValidationError("execution_not_granted")
        if self.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
            raise ContractValidationError("request_coverage_not_established")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class DurableConsumeResult:
    """Outcome of the atomic consume transaction.

    ``CLAIMED`` means this call applied the STARTED write. ``REPLAYED``
    means the same fingerprint and signed nonce were already started.
    Neither value is specialist success.
    """

    outcome: MasterExecutionLedgerOutcome
    fresh_transition: bool
    replayed: bool
    distributed_exactly_once: bool = False
    production_grant: bool = False
    verified_aws_provenance: bool = False
    specialist_executed: bool = False
    durable_master_ledger_consumption_available: bool = False
    grant_reason: GrantRejectionReason | None = None
    ledger_rejection: MasterExecutionLedgerRejection | None = None
    attempt: DurableConsumedAttempt | None = None
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED

    def __post_init__(self) -> None:
        _assert_closed()
        _lock_closed(
            production_grant=self.production_grant,
            distributed_exactly_once=self.distributed_exactly_once,
            verified_aws_provenance=self.verified_aws_provenance,
            specialist_executed=self.specialist_executed,
            execution_permitted=False,
            dispatch_permitted=False,
            execution_authority=self.execution_authority,
            production_authority=self.production_authority,
            source_authenticity=self.source_authenticity,
        )
        if self.durable_master_ledger_consumption_available is not False:
            raise ContractValidationError("ledger_not_integrated")
        if not isinstance(self.outcome, MasterExecutionLedgerOutcome):
            raise ContractValidationError("invalid_ledger_result")
        if type(self.fresh_transition) is not bool or type(self.replayed) is not bool:
            raise ContractValidationError("invalid_ledger_result")
        if self.outcome is MasterExecutionLedgerOutcome.CLAIMED:
            if self.fresh_transition is not True or self.replayed is not False or self.attempt is None:
                raise ContractValidationError("invalid_ledger_result")
            if self.grant_reason is not None or self.ledger_rejection is not None:
                raise ContractValidationError("invalid_ledger_result")
        elif self.outcome is MasterExecutionLedgerOutcome.REPLAYED:
            if self.fresh_transition is not False or self.replayed is not True or self.attempt is None:
                raise ContractValidationError("invalid_ledger_result")
            if self.ledger_rejection is not None:
                raise ContractValidationError("invalid_ledger_result")
        elif self.outcome is MasterExecutionLedgerOutcome.REJECTED:
            if (
                self.fresh_transition is not False
                or self.replayed is not False
                or self.attempt is not None
                or (self.grant_reason is None and self.ledger_rejection is None)
            ):
                raise ContractValidationError("invalid_ledger_result")
        else:
            raise ContractValidationError("invalid_ledger_result")

    def __bool__(self) -> bool:
        return False


def _lease_utc(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _attempt_from_record(record: object, envelope: MasterExecutionLedgerEnvelope) -> DurableConsumedAttempt:
    state = getattr(record, "state", None)
    if not isinstance(state, MasterExecutionLedgerState):
        raise LedgerStoreUnavailable("durable_storage_unavailable")
    return DurableConsumedAttempt(
        owner_id=str(getattr(record, "owner_id")),
        attempt_token=str(getattr(record, "attempt_token")),
        fence_epoch=int(getattr(record, "fence_epoch")),
        grant_nonce=str(getattr(record, "grant_nonce")),
        idempotency_key=envelope.idempotency_key,
        payload_fingerprint=envelope.fingerprint,
        state=state,
        master_request_id=envelope.master_request_id,
        planning_session_id=envelope.planning_session_id,
        step_id=envelope.step_id,
        invocation_id=envelope.invocation_id,
        principal_ref=envelope.principal_ref,
        policy_revision=envelope.policy_revision,
        specialist=envelope.specialist,
        purpose=envelope.purpose,
        capability=envelope.capability,
        resource_scope=envelope.resource_scope,
        region_scope=envelope.region_scope,
        deadline_utc=envelope.deadline_utc,
        correlation_id=envelope.correlation_id,
        lease_expires_at_utc=_lease_utc(int(getattr(record, "lease_expires_at_epoch"))),
        accepted_at_utc=envelope.accepted_at_utc,
        ledger_observed_at_utc=str(getattr(record, "ledger_observed_at_utc")),
    )


def _attempt_from_ledger(
    ledger_result: MasterExecutionLedgerResult,
    envelope: MasterExecutionLedgerEnvelope,
) -> DurableConsumedAttempt:
    if (
        ledger_result.owner_id is None
        or ledger_result.attempt_token is None
        or ledger_result.fence_epoch is None
        or ledger_result.grant_nonce is None
        or ledger_result.state is None
        or ledger_result.lease_expires_at_utc is None
        or ledger_result.accepted_at_utc is None
        or ledger_result.deadline_utc is None
        or ledger_result.ledger_observed_at_utc is None
        or ledger_result.correlation_id is None
    ):
        raise LedgerStoreUnavailable("durable_storage_unavailable")
    return DurableConsumedAttempt(
        owner_id=ledger_result.owner_id,
        attempt_token=ledger_result.attempt_token,
        fence_epoch=ledger_result.fence_epoch,
        grant_nonce=ledger_result.grant_nonce,
        idempotency_key=envelope.idempotency_key,
        payload_fingerprint=envelope.fingerprint,
        state=ledger_result.state,
        master_request_id=envelope.master_request_id,
        planning_session_id=envelope.planning_session_id,
        step_id=envelope.step_id,
        invocation_id=envelope.invocation_id,
        principal_ref=envelope.principal_ref,
        policy_revision=envelope.policy_revision,
        specialist=envelope.specialist,
        purpose=envelope.purpose,
        capability=envelope.capability,
        resource_scope=envelope.resource_scope,
        region_scope=envelope.region_scope,
        deadline_utc=envelope.deadline_utc,
        correlation_id=envelope.correlation_id,
        lease_expires_at_utc=ledger_result.lease_expires_at_utc,
        accepted_at_utc=ledger_result.accepted_at_utc,
        ledger_observed_at_utc=ledger_result.ledger_observed_at_utc,
    )


def _claimed(attempt: DurableConsumedAttempt) -> DurableConsumeResult:
    return DurableConsumeResult(
        outcome=MasterExecutionLedgerOutcome.CLAIMED,
        fresh_transition=True,
        replayed=False,
        attempt=attempt,
    )


def _replayed(attempt: DurableConsumedAttempt, *, grant_reason: GrantRejectionReason | None = None) -> DurableConsumeResult:
    return DurableConsumeResult(
        outcome=MasterExecutionLedgerOutcome.REPLAYED,
        fresh_transition=False,
        replayed=True,
        grant_reason=grant_reason,
        attempt=attempt,
    )


def _rejected(
    *,
    grant_reason: GrantRejectionReason | None = None,
    ledger_rejection: MasterExecutionLedgerRejection | None = None,
) -> DurableConsumeResult:
    return DurableConsumeResult(
        outcome=MasterExecutionLedgerOutcome.REJECTED,
        fresh_transition=False,
        replayed=False,
        grant_reason=grant_reason,
        ledger_rejection=ledger_rejection,
    )


def _verify_kwargs(
    envelope: MasterExecutionGrantEnvelope,
    principal: LocalHarnessPrincipal,
) -> dict[str, object]:
    evaluation = evaluate_grant_envelope(envelope)
    if (
        not evaluation.bounds_accepted
        or evaluation.tool_name is None
        or evaluation.resource_scope is None
        or evaluation.argument_names is None
        or evaluation.region_scope is None
        or evaluation.temporal_scopes is None
    ):
        raise ContractValidationError("grant_bounds_rejected")
    return {
        "principal": principal,
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


def _identity_mismatch(
    identity: Sprint3CIdentityBinding,
    envelope: MasterExecutionGrantEnvelope,
    principal: LocalHarnessPrincipal,
) -> GrantRejectionReason | None:
    if principal.reference != identity.principal_ref or principal.reference != envelope.principal_ref:
        return GrantRejectionReason.PRINCIPAL_MISMATCH
    pairs = (
        (identity.master_request_id, envelope.master_request_id, GrantRejectionReason.MASTER_REQUEST_MISMATCH),
        (identity.planning_session_id, envelope.planning_session_id, GrantRejectionReason.SESSION_MISMATCH),
        (identity.step_id, envelope.step_id, GrantRejectionReason.STEP_MISMATCH),
        (identity.invocation_id, envelope.invocation_id, GrantRejectionReason.INVOCATION_MISMATCH),
        (identity.policy_revision, envelope.policy_revision, GrantRejectionReason.POLICY_REVISION_MISMATCH),
        (identity.accepted_at_utc, envelope.accepted_at_utc, GrantRejectionReason.ACCEPTED_AT_MISMATCH),
        (identity.deadline_utc, envelope.deadline_utc, GrantRejectionReason.DEADLINE_MISMATCH),
    )
    for left, right, reason in pairs:
        if left != right:
            return reason
    if identity.purpose is not envelope.purpose:
        return GrantRejectionReason.PURPOSE_MISMATCH
    return None


class Sprint3CDurableGrantLedger:
    """Production-shaped atomic consume over an injected DynamoDB client.

    The client is never constructed here. AWS account and table provenance
    stay unverified. Production grants are never issued.
    """

    def __init__(
        self,
        *,
        issuer: object,
        client: object,
        token_source: Callable[[], str] | None = None,
    ) -> None:
        _assert_closed()
        if type(issuer) is not LocalHarnessGrantIssuer:
            raise ContractValidationError("invalid_grant_issuer")
        if issuer.production_grants_issued != 0:
            raise ContractValidationError("production_grant_issuance_closed")
        get_item = getattr(client, "get_item", None)
        put_item = getattr(client, "put_item", None)
        transact = getattr(client, "transact_write_items", None)
        if not callable(get_item) or not callable(put_item) or not callable(transact):
            raise ContractValidationError("invalid_ledger_client")
        self._issuer = issuer
        self._client = client
        self._store = DynamoDbMasterExecutionLedgerStore(client, DYNAMODB_LEDGER_TABLE_NAME)
        if self._store.verified_aws_account_and_table_provenance is not False:
            raise ContractValidationError("durable_adapter_type_is_not_aws_provenance")
        self._ledger = MasterExecutionLedger.for_durable(self._store, token_source=token_source)
        if token_source is None:
            self._token_source: Callable[[], str] = lambda: secrets.token_urlsafe(32)
        elif callable(token_source):
            self._token_source = token_source
        else:
            raise ContractValidationError("invalid_token_source")

    @property
    def production_grant_issuance_available(self) -> bool:
        return False

    @property
    def durable_master_ledger_consumption_available(self) -> bool:
        return False

    @property
    def verified_aws_account_and_table_provenance(self) -> bool:
        return False

    @property
    def distributed_exactly_once(self) -> bool:
        return False

    @property
    def grant_nonce_rotates_on_takeover(self) -> bool:
        return False

    def consume_signed_grant(
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
    ) -> DurableConsumeResult:
        """Verify the signed grant and consume it with one ledger transaction.

        The signed nonce is written as ``grant_nonce`` on a ``STARTED``
        attempt item together with a no-TTL marker. Replay of the same
        nonce returns ``REPLAYED`` and does not start again.
        """

        _assert_closed()
        if type(production_requested) is not bool:
            raise ContractValidationError("invalid_production_requested")
        if production_requested is True:
            return _rejected(grant_reason=GrantRejectionReason.PRODUCTION_ISSUANCE_CLOSED)
        if type(identity) is not Sprint3CIdentityBinding:
            raise ContractValidationError("invalid_identity_binding")
        if type(envelope) is not MasterExecutionGrantEnvelope:
            raise ContractValidationError("invalid_grant_envelope")
        if type(principal) is not LocalHarnessPrincipal:
            raise ContractValidationError("principal_not_sealed")
        if identity.policy_decision != "DENY":
            return _rejected(grant_reason=GrantRejectionReason.EVALUATION_NOT_A_GRANT)
        if identity.authority_origin != "NOT_ESTABLISHED":
            return _rejected(grant_reason=GrantRejectionReason.DENIED_BY_DEFAULT)
        if identity.record_kind == PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION.value:
            if identity.production_principal is not False:
                return _rejected(grant_reason=GrantRejectionReason.LOCAL_HARNESS_GRANT_NOT_PRODUCTION)
        else:
            return _rejected(grant_reason=GrantRejectionReason.LOCAL_HARNESS_GRANT_NOT_PRODUCTION)
        if identity.verified_identity is not False or identity.integration_owner_binding is not False:
            return _rejected(grant_reason=GrantRejectionReason.PRODUCTION_ISSUANCE_CLOSED)
        mismatch = _identity_mismatch(identity, envelope, principal)
        if mismatch is not None:
            return _rejected(grant_reason=mismatch)
        evaluation = evaluate_grant_envelope(envelope)
        if not evaluation.bounds_accepted:
            return _rejected(grant_reason=evaluation.reasons[0] if evaluation.reasons else GrantRejectionReason.DENIED_BY_DEFAULT)
        if self._issuer.production_grants_issued != 0 or PRODUCTION_SIGNING_KEY_STORE_INTEGRATED is not False:
            return _rejected(grant_reason=GrantRejectionReason.PRODUCTION_ISSUANCE_CLOSED)
        verified = self._issuer.verify(claim, **_verify_kwargs(envelope, principal))
        if not verified.local_harness_binding_accepted:
            reason = verified.reason or GrantRejectionReason.DENIED_BY_DEFAULT
            if reason is GrantRejectionReason.REPLAY:
                return self._replay_from_store(claim, envelope, principal, owner_id, now_utc, lease_seconds, reason)
            return _rejected(grant_reason=reason)
        try:
            nonce = signed_grant_nonce(claim)
            ledger_envelope = ledger_envelope_from_grant(
                envelope,
                principal_ref=principal.reference,
            )
        except ContractValidationError:
            return _rejected(grant_reason=GrantRejectionReason.DENIED_BY_DEFAULT)
        result = self._atomic_consume(
            ledger_envelope,
            owner_id=owner_id,
            grant_nonce=nonce,
            now_utc=now_utc,
            lease_seconds=lease_seconds,
        )
        if (
            result.outcome
            in {MasterExecutionLedgerOutcome.CLAIMED, MasterExecutionLedgerOutcome.REPLAYED}
            and result.attempt is not None
            and result.attempt.state is MasterExecutionLedgerState.STARTED
        ):
            self._record_local_grant_consumption(claim, envelope, principal)
        return result

    def shared_execution_ledger(self) -> MasterExecutionLedger:
        """Ledger holding the STARTED fence. A matching fence is not a permit."""

        _assert_closed()
        if self._ledger.production_cross_instance_authority is not False:
            raise ContractValidationError("execution_authority_not_granted")
        return self._ledger

    def authorize_production_grant_consume(self, *_args: object, **_kwargs: object) -> DurableConsumeResult:
        """Fail closed. Production consume is not available in this sprint."""

        _assert_closed()
        return _rejected(grant_reason=GrantRejectionReason.PRODUCTION_ISSUANCE_CLOSED)

    def issue_from_policy_evaluation(self, evaluation: object, *_args: object, **_kwargs: object) -> DurableConsumeResult:
        """A DENY evaluation is not issued as a grant. ALLOW is not a permit."""

        _assert_closed()
        decision = getattr(evaluation, "decision", None)
        if decision is not None and getattr(decision, "value", decision) == "DENY":
            return _rejected(grant_reason=GrantRejectionReason.EVALUATION_NOT_A_GRANT)
        return _rejected(grant_reason=GrantRejectionReason.PRODUCTION_ISSUANCE_CLOSED)

    def fenced_attempt_matches(
        self,
        envelope: object,
        *,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        grant_nonce: str,
    ) -> MasterExecutionAttemptFenceCheck:
        """Compare a presented fence with the stored attempt. A match is not a permit."""

        _assert_closed()
        if type(envelope) is MasterExecutionGrantEnvelope:
            ledger_envelope = ledger_envelope_from_grant(envelope, principal_ref=envelope.principal_ref)
        elif type(envelope) is MasterExecutionLedgerEnvelope:
            ledger_envelope = envelope
        else:
            raise ContractValidationError("invalid_ledger_envelope")
        nonce = _token(grant_nonce, "grant_nonce")
        marker = self._read_marker(nonce, ledger_envelope)
        view = self._store.read(*ledger_item_key(ledger_envelope))
        if marker is not None and view.record is None:
            return MasterExecutionAttemptFenceCheck(
                matches_current_attempt=False,
                rejection=MasterExecutionLedgerRejection.RETENTION_GAP,
                ttl_deleted=True,
            )
        return self._ledger.fenced_attempt_matches(
            ledger_envelope,
            owner_id=owner_id,
            attempt_token=attempt_token,
            fence_epoch=fence_epoch,
            grant_nonce=nonce,
        )

    def complete_attempt(
        self,
        envelope: object,
        *,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        now_utc: str,
    ) -> MasterExecutionLedgerResult:
        """Fence-checked complete. Ledger complete is not specialist success."""

        ledger_envelope = self._as_ledger_envelope(envelope)
        return self._ledger.complete(
            ledger_envelope,
            owner_id=owner_id,
            attempt_token=attempt_token,
            fence_epoch=fence_epoch,
            now_utc=now_utc,
        )

    def fail_attempt(
        self,
        envelope: object,
        *,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        now_utc: str,
        reason: str,
    ) -> MasterExecutionLedgerResult:
        ledger_envelope = self._as_ledger_envelope(envelope)
        return self._ledger.fail(
            ledger_envelope,
            owner_id=owner_id,
            attempt_token=attempt_token,
            fence_epoch=fence_epoch,
            now_utc=now_utc,
            reason=reason,
        )

    def cancel_started_attempt(
        self,
        envelope: object,
        *,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        now_utc: str,
    ) -> MasterExecutionLedgerResult:
        """Host cancellation after STARTED. Uses fail, not a second start.

        ``MasterExecutionLedger.cancel`` only applies to ``RESERVED``. The
        atomic consume writes ``STARTED``, so cancellation records ``FAILED``
        with ``host_cancelled`` under the current fence.
        """

        ledger_envelope = self._as_ledger_envelope(envelope)
        return self._ledger.fail(
            ledger_envelope,
            owner_id=owner_id,
            attempt_token=attempt_token,
            fence_epoch=fence_epoch,
            now_utc=now_utc,
            reason=HOST_CANCEL_REASON,
        )

    def bind_production_grant_to_master_ledger(self, *_args: object, **_kwargs: object) -> None:
        bind_production_grant_to_master_ledger(*_args, **_kwargs)

    def issue_production_execution_grant(self, *_args: object, **_kwargs: object) -> None:
        issue_production_execution_grant(*_args, **_kwargs)

    def _atomic_consume(
        self,
        ledger_envelope: MasterExecutionLedgerEnvelope,
        *,
        owner_id: str,
        grant_nonce: str,
        now_utc: str,
        lease_seconds: int,
    ) -> DurableConsumeResult:
        owner = _token(owner_id, "owner_id")
        nonce = _token(grant_nonce, "grant_nonce")
        if type(lease_seconds) is not int or lease_seconds < 1 or lease_seconds > MAX_LEASE_SECONDS:
            raise ContractValidationError("invalid_lease_seconds")
        marker = self._read_marker(nonce, ledger_envelope)
        view = self._store.read(*ledger_item_key(ledger_envelope))
        if marker is not None and view.record is None:
            return _rejected(ledger_rejection=MasterExecutionLedgerRejection.RETENTION_GAP)
        if view.record is not None:
            classified = self._ledger.consume_signed_nonce_and_start(
                ledger_envelope,
                owner_id=owner,
                grant_nonce=nonce,
                now_utc=now_utc,
                lease_seconds=lease_seconds,
            )
            return self._from_ledger(classified, ledger_envelope, nonce)
        try:
            record = build_started_record_for_signed_nonce(
                ledger_envelope,
                owner_id=owner,
                grant_nonce=nonce,
                attempt_token=self._new_attempt_token(nonce),
                now_utc=now_utc,
                lease_seconds=lease_seconds,
            )
        except ContractValidationError as exc:
            if "deadline_expired" in str(exc):
                return _rejected(ledger_rejection=MasterExecutionLedgerRejection.DEADLINE_EXPIRED)
            raise
        marker_item = encode_consumption_marker(
            grant_nonce=nonce,
            envelope=ledger_envelope,
            consumed_at_utc=now_utc,
        )
        request = {
            "TransactItems": [
                _absent_put(encode_ledger_record_item(record)),
                _absent_put(marker_item),
            ]
        }
        try:
            self._client.transact_write_items(**request)
        except LedgerStoreUnavailable:
            raise
        except Exception as exc:
            if not _transaction_canceled(exc):
                raise LedgerStoreUnavailable("durable_storage_unavailable") from exc
            return self._classify_after_conflict(ledger_envelope, owner, nonce, now_utc, lease_seconds)
        return _claimed(_attempt_from_record(record, ledger_envelope))

    def _classify_after_conflict(
        self,
        ledger_envelope: MasterExecutionLedgerEnvelope,
        owner: str,
        nonce: str,
        now_utc: str,
        lease_seconds: int,
    ) -> DurableConsumeResult:
        marker = self._read_marker(nonce, ledger_envelope)
        view = self._store.read(*ledger_item_key(ledger_envelope))
        if marker is not None and view.record is None:
            return _rejected(ledger_rejection=MasterExecutionLedgerRejection.RETENTION_GAP)
        classified = self._ledger.consume_signed_nonce_and_start(
            ledger_envelope,
            owner_id=owner,
            grant_nonce=nonce,
            now_utc=now_utc,
            lease_seconds=lease_seconds,
        )
        return self._from_ledger(classified, ledger_envelope, nonce)

    def _from_ledger(
        self,
        classified: MasterExecutionLedgerResult,
        ledger_envelope: MasterExecutionLedgerEnvelope,
        nonce: str,
    ) -> DurableConsumeResult:
        if classified.outcome is MasterExecutionLedgerOutcome.REJECTED:
            return _rejected(ledger_rejection=classified.rejection)
        if classified.grant_nonce != nonce:
            return _rejected(ledger_rejection=MasterExecutionLedgerRejection.CONFLICTING_DUPLICATE)
        attempt = _attempt_from_ledger(classified, ledger_envelope)
        if classified.outcome is MasterExecutionLedgerOutcome.CLAIMED:
            return _claimed(attempt)
        return _replayed(attempt)

    def _replay_from_store(
        self,
        claim: object,
        envelope: MasterExecutionGrantEnvelope,
        principal: LocalHarnessPrincipal,
        owner_id: str,
        now_utc: str,
        lease_seconds: int,
        reason: GrantRejectionReason,
    ) -> DurableConsumeResult:
        try:
            nonce = signed_grant_nonce(claim)
            ledger_envelope = ledger_envelope_from_grant(envelope, principal_ref=principal.reference)
        except ContractValidationError:
            return _rejected(grant_reason=reason)
        marker = self._read_marker(nonce, ledger_envelope)
        view = self._store.read(*ledger_item_key(ledger_envelope))
        if marker is not None and view.record is None:
            return _rejected(
                grant_reason=reason,
                ledger_rejection=MasterExecutionLedgerRejection.RETENTION_GAP,
            )
        classified = self._ledger.consume_signed_nonce_and_start(
            ledger_envelope,
            owner_id=owner_id,
            grant_nonce=nonce,
            now_utc=now_utc,
            lease_seconds=lease_seconds,
        )
        if classified.outcome is MasterExecutionLedgerOutcome.REJECTED:
            return _rejected(grant_reason=reason, ledger_rejection=classified.rejection)
        return _replayed(_attempt_from_ledger(classified, ledger_envelope), grant_reason=reason)

    def _read_marker(self, grant_nonce: str, envelope: MasterExecutionLedgerEnvelope) -> dict[str, object] | None:
        pk, sk = consumption_marker_key(grant_nonce, envelope)
        try:
            response = self._client.get_item(
                TableName=DYNAMODB_LEDGER_TABLE_NAME,
                Key={"pk": {"S": pk}, "sk": {"S": sk}},
                ConsistentRead=True,
            )
        except LedgerStoreUnavailable:
            raise
        except Exception as exc:
            raise LedgerStoreUnavailable("durable_storage_unavailable") from exc
        if not isinstance(response, dict):
            raise LedgerStoreUnavailable("durable_storage_unavailable")
        item = response.get("Item")
        if item is None:
            return None
        if not isinstance(item, dict):
            raise LedgerStoreUnavailable("durable_storage_unavailable")
        return item

    def _as_ledger_envelope(self, envelope: object) -> MasterExecutionLedgerEnvelope:
        if type(envelope) is MasterExecutionLedgerEnvelope:
            return envelope
        if type(envelope) is MasterExecutionGrantEnvelope:
            return ledger_envelope_from_grant(envelope, principal_ref=envelope.principal_ref)
        raise ContractValidationError("invalid_ledger_envelope")

    def _record_local_grant_consumption(
        self,
        claim: object,
        envelope: MasterExecutionGrantEnvelope,
        principal: LocalHarnessPrincipal,
    ) -> None:
        """Mark the local grant consumed without changing the STARTED fence.

        ``LocalHarnessGrantIssuer.consume`` still rejects a master execution
        ledger. The local marker is grant-consumption state
        ``CONSUMED_LOCAL_HARNESS``. The durable item remains ``STARTED``.
        Neither state is production dispatch.
        """

        consumption = self._issuer.consume(
            claim,
            LocalHarnessConsumptionLedger(),
            **_verify_kwargs(envelope, principal),
        )
        if consumption.dispatch_permitted or consumption.execution_permitted or consumption.establishes_authority:
            raise ContractValidationError("execution_not_granted")
        if consumption.local_harness_consumption_recorded is True:
            if consumption.lifecycle is not GrantLifecycle.CONSUMED_LOCAL_HARNESS:
                raise ContractValidationError("grant_consumption_lifecycle_mismatch")
            return
        if consumption.reason is GrantRejectionReason.REPLAY:
            return
        reason = consumption.reason.value if consumption.reason is not None else "consumed_grant_required"
        raise ContractValidationError(reason)

    def _new_attempt_token(self, nonce: str) -> str:
        for _ in range(5):
            value = self._token_source()
            if type(value) is not str or value == nonce or "#" in value or any(ch.isspace() for ch in value):
                continue
            if len(value.strip()) < 8:
                continue
            try:
                return _token(value, "attempt_token")
            except ContractValidationError:
                continue
        raise ContractValidationError("attempt_token_source_exhausted")


_assert_closed()


__all__ = [
    "CONSUME_MARKER_SCHEMA_VERSION",
    "DECISION_TARGET_NOT_APPLICABLE",
    "HOST_CANCEL_REASON",
    "SPRINT3C_DISTRIBUTED_EXACTLY_ONCE",
    "SPRINT3C_GRANT_LEDGER_SCHEMA_VERSION",
    "SPRINT3C_PRODUCTION_GRANT_LEDGER_ENABLED",
    "DurableConsumeResult",
    "DurableConsumedAttempt",
    "Sprint3CDurableGrantLedger",
    "Sprint3CIdentityBinding",
    "agent_a_identity_fixture",
    "bind_recorded_deny_identity",
    "consumption_marker_key",
    "encode_consumption_marker",
    "ledger_envelope_from_grant",
    "signed_grant_nonce",
]
