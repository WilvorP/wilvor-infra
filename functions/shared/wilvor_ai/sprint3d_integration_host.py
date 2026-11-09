"""Sprint 3D cross-agent host.

Composes Agent B's RS256 authentication and HMAC grant, Agent C's reviewed
authorization and TransactWriteItems client, Agent D's evidence host, and
Agent E's native entrypoints. Production issuance, route enablement, live
network calls, and distributed exactly-once execution stay closed.

This module does not call ``Sprint3CDurableGrantLedger.consume_signed_grant``.
The signed grant nonce is the ledger nonce. The attempt token is a separate
value. An injected client is offline simulation, not AWS provenance.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from wilvor_ai.contracts import ContractValidationError, FreshnessStatus
from wilvor_ai.master_authorized_scheduler_adapter import (
    InProcessLedgerReservationDouble,
    MasterAuthorizedSchedulerAdapter,
    SchedulerDispatchPort,
    open_local_test_host_seam,
    open_nonproduction_scheduler_seal,
)
from wilvor_ai.master_authorized_scope import (
    EffectiveScopeRequestHints,
    EffectiveScopeStatus,
    LocalTestHostScope,
    resolve_master_authorized_scope,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_execution_grants import (
    GrantEvaluationClock,
    LocalHarnessGrantIssuer,
    MasterExecutionGrantEnvelope,
)
from wilvor_ai.master_execution_ledger import (
    DYNAMODB_LEDGER_TABLE_NAME,
    DynamoDbMasterExecutionLedgerStore,
    LedgerStoreUnavailable,
    MasterExecutionLedger,
    MasterExecutionLedgerEnvelope,
    MasterExecutionLedgerOutcome,
    MasterExecutionLedgerRejection,
    MasterExecutionLedgerState,
    build_started_record_for_signed_nonce,
    encode_ledger_record_item,
    ledger_item_key,
)
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.master_runtime_policy import MasterRuntimePolicyDecision
from wilvor_ai.master_trusted_read_executor import (
    MasterTrustedReadExecutor,
    NonproductionAdapterSpec,
    NonproductionReadPage,
    ReadExecutionOutcome,
    ReadReceiptCandidate,
    UntrustedReadRequest,
    bind_nonproduction_cancellation,
    bind_nonproduction_host_clock,
    bind_nonproduction_read_registry,
    bind_nonproduction_test_invocation,
)
from wilvor_ai.sprint3c_grant_ledger import (
    DurableConsumedAttempt,
    DurableConsumeResult,
    consumption_marker_key,
    encode_consumption_marker,
    ledger_envelope_from_grant,
)
from wilvor_ai.sprint3c_scoped_read import (
    interpret_server_owned_aws_page,
    plan_server_owned_aws_read,
)
from wilvor_ai.sprint3c_specialist_orchestration import (
    Sprint3CConsumedDispatchAuthority,
    Sprint3CNativeSpecialistOrchestrator,
)
from wilvor_ai.sprint3d_auth_grants import (
    AgentCAtomicConsumeRequest,
    AgentCPresentedBinding,
    AtomicConsumeCoordination,
    HostOwnedGrantIssuer,
    HostSignedGrant,
    InjectedGrantSigningKeys,
    Sprint3DTrustedPrincipal,
    TrustedPrincipalEstablishment,
    assert_closed_production_entrypoints,
    presented_binding_from_principal,
    route_enablement_permitted,
    verify_serialized_grant,
)
from wilvor_ai.sprint3d_aws_execution import (
    UPSTREAM_ISSUER,
    AuthorizedLedgerDynamoClient,
    AuthorizedReadDynamoClient,
    AwsExecutionRefusal,
    TrustedAwsExecutionAuthorization,
    live_network_gate_open,
    open_production_aws_execution,
    review_aws_execution_authority,
    seal_reviewed_upstream_identity,
)
from wilvor_ai.sprint3d_source_evidence import (
    AwsSourcePage,
    ObservedSourceRecord,
    SourceEvidenceAssessment,
    SourceEvidenceHost,
    SourceReadInvocation,
    evaluate_source_evidence,
)
_MARKER_TTL_NAMES = frozenset(
    {
        "retention_expires_at_epoch",
        "ttl",
        "expires_at_epoch",
        "TimeToLive",
        "time_to_live",
    }
)
_SQL_NAMES = frozenset({"sql", "statement", "query_string", "FilterExpression"})
_WRITE_NAMES = frozenset(
    {
        "put_item",
        "delete_item",
        "update_item",
        "batch_write_item",
        "transact_write_items",
        "execute_statement",
        "scan",
    }
)

# Agent F's module names stay absent where a live control is still missing.
# Present Sprint 3D modules remain PRESENT_UNVERIFIED. None of these are VERIFIED.
INTEGRATION_HOOK_MAP = (
    (
        "I3D-1",
        "wilvor_ai.sprint3d_auth_grants.authenticate_bearer",
        "BLOCKED",
        "RS256 verification binds a host principal. REVIEWED_HOST_ADMISSION is not minted. Twelve issuance controls are unsatisfied.",
    ),
    (
        "I3D-2",
        "wilvor_ai.sprint3d_integration_host.Sprint3DIntegrationHost.consume_verified_grant",
        "BLOCKED",
        "Nonproduction HMAC grant consume writes one transaction. Production grant issuance and the signing-key store stay closed. HMAC_PREVENTS_REPLAY stays false.",
    ),
    (
        "I3D-3",
        "wilvor_ai.sprint3d_aws_execution.review_aws_execution_authority",
        "BLOCKED",
        "Injected STS, DescribeTable, and IAM simulation shapes can be reviewed. They are not a live account or table proof.",
    ),
    (
        "I3D-4",
        "wilvor_ai.sprint3d_integration_host.Sprint3DIntegrationHost.execute_server_read",
        "BLOCKED",
        "GET_ITEM and QUERY run only through the reviewed nonproduction client. Production scoped read stays closed.",
    ),
    (
        "I3D-5",
        "wilvor_ai.sprint3d_integration_host.Sprint3DIntegrationHost.execute_server_read",
        "BLOCKED",
        "Bounds, deadline, and cancellation are enforced on the injected client. This is not a live bounded read.",
    ),
    (
        "I3D-6",
        "wilvor_ai.sprint3d_source_evidence.evaluate_source_evidence",
        "PRESENT_UNVERIFIED",
        "Receipts are sealed only after the host adapter reads the authorized client.",
    ),
    (
        "I3D-7",
        "wilvor_ai.sprint3d_source_evidence.trusted_receipt_interface",
        "PRESENT_UNVERIFIED",
        "Regional text and point geometry do not establish SIGMET impact.",
    ),
    (
        "I3D-8",
        "wilvor_ai.sprint3d_native_specialists.execute_historical_native",
        "PRESENT_UNVERIFIED",
        "Historical and Decision use their native entrypoints. Native success is not source evidence.",
    ),
    (
        "I3D-9",
        "wilvor_ai.sprint3c_evidence_response.join_sprint3c_evidence_response",
        "BLOCKED",
        "MA4 authenticity stays NOT_ESTABLISHED. MA5 does not admit claims from this host.",
    ),
    (
        "I3D-10",
        "absent",
        "ABSENT",
        "No read-only AWS probe is implemented. An injected client cannot satisfy it.",
    ),
)


@dataclass(frozen=True)
class IntegrationConsumeOutcome:
    """One consume attempt. A claimed transaction is not native execution."""

    claimed: bool
    replayed: bool
    result: DurableConsumeResult | None
    rejection: str | None
    data_plane_invoked: bool
    distributed_exactly_once: bool = False
    production_authority: bool = False
    live_aws_network_invoked: bool = False

    def __post_init__(self) -> None:
        if self.distributed_exactly_once or self.production_authority or self.live_aws_network_invoked:
            raise ContractValidationError("ledger_transaction_is_not_native_execution")


@dataclass(frozen=True)
class IntegratedReadResult:
    """One server read. Empty pages are not a verified zero."""

    refusal: AwsExecutionRefusal | None
    receipt: ReadReceiptCandidate | None
    decoded_items: tuple[dict[str, object], ...]
    data_plane_invoked: bool
    live_aws_network_invoked: bool = False
    verified_zero: bool = False
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED

    def __post_init__(self) -> None:
        if self.live_aws_network_invoked or self.verified_zero:
            raise ContractValidationError("scoped_aws_read_is_not_source_authentication")
        if self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            raise ContractValidationError("source_authenticity_not_established")


class _ObservationClock:
    def __init__(self, stamp: str) -> None:
        self._stamp = stamp

    def now_utc(self) -> str:
        return self._stamp


def _utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _decode_attribute(value: object) -> object:
    if not isinstance(value, dict):
        return value
    if "S" in value:
        return value["S"]
    if "N" in value:
        return value["N"]
    if "BOOL" in value:
        return value["BOOL"]
    if "NULL" in value:
        return None
    return value


def _decode_item(item: Mapping[str, object]) -> dict[str, object]:
    return {key: _decode_attribute(value) for key, value in item.items()}


def _canceled(exc: BaseException) -> bool:
    if type(exc).__name__ == "TransactionCanceledException":
        return True
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        error = response.get("Error")
        if isinstance(error, Mapping) and error.get("Code") == "TransactionCanceledException":
            return True
    return False


def _attempt_from_view(record: object, envelope: MasterExecutionLedgerEnvelope) -> DurableConsumedAttempt:
    state = getattr(record, "state")
    if not isinstance(state, MasterExecutionLedgerState):
        raise LedgerStoreUnavailable("durable_storage_unavailable")
    lease_epoch = int(getattr(record, "lease_expires_at_epoch"))
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
        lease_expires_at_utc=datetime.fromtimestamp(lease_epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        accepted_at_utc=envelope.accepted_at_utc,
        ledger_observed_at_utc=str(getattr(record, "ledger_observed_at_utc")),
    )


def _claimed(attempt: DurableConsumedAttempt) -> DurableConsumeResult:
    return DurableConsumeResult(
        outcome=MasterExecutionLedgerOutcome.CLAIMED,
        fresh_transition=True,
        replayed=False,
        attempt=attempt,
    )


def _replayed(attempt: DurableConsumedAttempt) -> DurableConsumeResult:
    return DurableConsumeResult(
        outcome=MasterExecutionLedgerOutcome.REPLAYED,
        fresh_transition=False,
        replayed=True,
        attempt=attempt,
    )


def _rejected(rejection: MasterExecutionLedgerRejection) -> DurableConsumeResult:
    return DurableConsumeResult(
        outcome=MasterExecutionLedgerOutcome.REJECTED,
        fresh_transition=False,
        replayed=False,
        ledger_rejection=rejection,
    )


class _SeamOnlyDispatchPort(SchedulerDispatchPort):
    """Port required to stage a grant. Calling it does not run a worker."""

    def dispatch(
        self,
        *,
        consumed_grant: object,
        consumed_reservation: object,
        context: object,
        invocation: object,
    ) -> object:
        raise ContractValidationError("scripted_worker_rejected")


class Sprint3DIntegrationHost:
    """Nonproduction composition. Possession of this object is not a production permit."""

    def __init__(
        self,
        deployment: object,
        *,
        caller_identity: object,
        described_tables: object,
        iam_simulation: object,
        ledger_client: object,
        read_client: object,
        grant_keys: InjectedGrantSigningKeys,
        now_utc: str,
    ) -> None:
        assert_closed_production_entrypoints()
        if live_network_gate_open():
            raise ContractValidationError("live_aws_network_not_reviewed")
        review = review_aws_execution_authority(
            deployment,
            caller_identity=caller_identity,
            described_tables=described_tables,
            iam_simulation=iam_simulation,
        )
        if review.authorization is None or review.data_plane_invoked is not False:
            raise ContractValidationError("aws_authority_missing")
        self.authorization: TrustedAwsExecutionAuthorization = review.authorization
        self._ledger_client = AuthorizedLedgerDynamoClient(ledger_client, review.authorization)
        self._read_client = AuthorizedReadDynamoClient(read_client, review.authorization)
        self._read_inner = read_client
        self._store = DynamoDbMasterExecutionLedgerStore(self._ledger_client, DYNAMODB_LEDGER_TABLE_NAME)
        self._ledger = MasterExecutionLedger.for_durable(self._store)
        self._keys = grant_keys
        self._grant_issuer = HostOwnedGrantIssuer(grant_keys)
        self._now = now_utc
        self._envelopes: dict[str, MasterExecutionLedgerEnvelope] = {}
        self._grant_envelopes: dict[str, MasterExecutionGrantEnvelope] = {}
        self.production_authority = False
        self.distributed_exactly_once = False
        self.live_aws_network_invoked = False
        self.sprint3c_consume_signed_grant_called = False

    @property
    def execution_tier(self) -> str:
        """Injected clients are offline simulation. This is not a live AWS pass."""

        return "OFFLINE_SIMULATED"

    def probe_nonproduction_readonly(self) -> None:
        """No live probe exists. An injected client cannot satisfy I3D-10."""

        raise ContractValidationError("live_aws_probe_not_established")

    def assert_production_closed(self) -> None:
        assert_closed_production_entrypoints()
        if route_enablement_permitted() is not False:
            raise ContractValidationError("route_enablement_blocked")
        try:
            open_production_aws_execution()
        except ContractValidationError as exc:
            if "production_execution_closed" not in exc.errors:
                raise
        else:
            raise ContractValidationError("production_execution_closed")

    def issue_grant(
        self,
        establishment: TrustedPrincipalEstablishment,
        envelope: MasterExecutionGrantEnvelope,
        *,
        now: datetime,
    ) -> tuple[HostSignedGrant | None, AtomicConsumeCoordination]:
        if establishment.principal is None or establishment.principal.public_policy_decision is not MasterRuntimePolicyDecision.DENY:
            raise ContractValidationError("policy_evaluation_is_not_a_permit")
        return self._grant_issuer.issue(establishment, envelope, now=now)

    def presented_binding(
        self,
        principal: Sprint3DTrustedPrincipal,
        envelope: MasterExecutionGrantEnvelope,
    ) -> AgentCPresentedBinding:
        return presented_binding_from_principal(principal, envelope)

    def consume_verified_grant(
        self,
        coordination: AtomicConsumeCoordination,
        presented: AgentCPresentedBinding,
        envelope: MasterExecutionGrantEnvelope,
        *,
        principal: Sprint3DTrustedPrincipal,
        owner_id: str,
        now_utc: str,
        lease_seconds: int = 60,
        attempt_token: str | None = None,
        production_requested: bool = False,
    ) -> IntegrationConsumeOutcome:
        """Re-verify the HMAC grant and write STARTED plus a no-TTL marker."""

        if production_requested is True:
            return IntegrationConsumeOutcome(False, False, None, "production_execution_closed", False)
        if type(principal) is not Sprint3DTrustedPrincipal:
            raise ContractValidationError("caller_constructed_principal")
        if principal.public_policy_decision is not MasterRuntimePolicyDecision.DENY:
            raise ContractValidationError("policy_evaluation_is_not_a_permit")
        identity = seal_reviewed_upstream_identity(
            account_id=self.authorization.account_id,
            issuer=UPSTREAM_ISSUER,
            subject=principal.verified_subject,
            principal_ref=principal.principal_ref,
            policy_revision=principal.policy_revision,
            master_request_id=principal.master_request_id,
            planning_session_id=principal.planning_session_id,
            step_id=principal.step_id,
            invocation_id=principal.invocation_id,
            purpose=principal.purpose,
            accepted_at_utc=principal.accepted_at_utc,
            deadline_utc=principal.deadline_utc,
        )
        if identity.account_id != self.authorization.account_id or identity.production_principal is not False:
            return IntegrationConsumeOutcome(False, False, None, "upstream_identity_not_verified", False)
        if not coordination.accepted or type(coordination.request) is not AgentCAtomicConsumeRequest:
            reason = None if coordination.rejection is None else coordination.rejection.value
            return IntegrationConsumeOutcome(False, False, None, reason or "grant_rejected", False)
        request = coordination.request
        if request.grant_nonce_is_attempt_token is not False or request.consume_marker_has_ttl is not False:
            raise ContractValidationError("invalid_agent_c_consume_request")
        if request.distributed_exactly_once is not False or request.production_grant_issuance_available is not False:
            raise ContractValidationError("production_execution_closed")
        again = verify_serialized_grant(
            self._keys,
            canonical_document=request.canonical_document,
            signature_hex=request.signature_hex,
            key_id=request.signing_key_id,
            presented=presented,
            now=_utc(now_utc),
        )
        if (
            not again.accepted
            or again.request is None
            or again.request.grant_nonce != request.grant_nonce
            or again.request.ledger_payload_fingerprint != request.ledger_payload_fingerprint
        ):
            reason = None if again.rejection is None else again.rejection.value
            return IntegrationConsumeOutcome(False, False, None, reason or "grant_rejected", False)
        ledger_envelope = ledger_envelope_from_grant(envelope, principal_ref=envelope.principal_ref)
        if ledger_envelope.fingerprint != request.ledger_payload_fingerprint:
            raise ContractValidationError("ledger_fingerprint_mismatch")
        if ledger_item_key(ledger_envelope) != (request.ledger_partition_key, request.ledger_sort_key):
            raise ContractValidationError("ledger_key_mismatch")
        marker_key = consumption_marker_key(request.grant_nonce, ledger_envelope)
        if marker_key != (request.consume_marker_partition_key, request.consume_marker_sort_key):
            raise ContractValidationError("consume_marker_mismatch")
        if (
            request.principal_ref != principal.principal_ref
            or request.planning_session_id != principal.planning_session_id
            or request.invocation_id != principal.invocation_id
            or request.master_request_id != principal.master_request_id
        ):
            return IntegrationConsumeOutcome(False, False, None, "request_mismatch", False)
        existing = self._read_attempt(ledger_envelope)
        if existing is not None:
            if existing.grant_nonce == request.grant_nonce and existing.payload_fingerprint == ledger_envelope.fingerprint:
                self._envelopes[existing.invocation_id] = ledger_envelope
                self._grant_envelopes[existing.invocation_id] = envelope
                return IntegrationConsumeOutcome(False, True, _replayed(existing), None, False)
            return IntegrationConsumeOutcome(
                False,
                False,
                _rejected(MasterExecutionLedgerRejection.CONFLICTING_DUPLICATE),
                "conflicting_duplicate",
                False,
            )
        token = attempt_token if attempt_token is not None else secrets.token_hex(16)
        if token == request.grant_nonce:
            raise ContractValidationError("attempt_token_not_unique")
        record = build_started_record_for_signed_nonce(
            ledger_envelope,
            owner_id=owner_id,
            grant_nonce=request.grant_nonce,
            attempt_token=token,
            now_utc=now_utc,
            lease_seconds=lease_seconds,
        )
        marker = encode_consumption_marker(
            grant_nonce=request.grant_nonce,
            envelope=ledger_envelope,
            consumed_at_utc=now_utc,
        )
        if _MARKER_TTL_NAMES & set(marker):
            raise ContractValidationError("consume_marker_must_not_have_ttl")
        attempt_item = encode_ledger_record_item(record)
        transaction = {
            "TransactItems": [
                {
                    "Put": {
                        "TableName": DYNAMODB_LEDGER_TABLE_NAME,
                        "Item": attempt_item,
                        "ConditionExpression": request.conditional_put_expression,
                        "ExpressionAttributeNames": {"#pk": "pk", "#sk": "sk"},
                    }
                },
                {
                    "Put": {
                        "TableName": DYNAMODB_LEDGER_TABLE_NAME,
                        "Item": marker,
                        "ConditionExpression": request.conditional_put_expression,
                        "ExpressionAttributeNames": {"#pk": "pk", "#sk": "sk"},
                    }
                },
            ]
        }
        try:
            self._ledger_client.transact_write_items(**transaction)
        except LedgerStoreUnavailable:
            raise
        except Exception as exc:
            if not _canceled(exc):
                raise LedgerStoreUnavailable("durable_storage_unavailable") from exc
            stored = self._read_attempt(ledger_envelope)
            if stored is None:
                return IntegrationConsumeOutcome(
                    False,
                    False,
                    _rejected(MasterExecutionLedgerRejection.CONFLICTING_DUPLICATE),
                    "conflicting_duplicate",
                    True,
                )
            if stored.grant_nonce != request.grant_nonce:
                return IntegrationConsumeOutcome(
                    False,
                    False,
                    _rejected(MasterExecutionLedgerRejection.CONFLICTING_DUPLICATE),
                    "conflicting_duplicate",
                    True,
                )
            self._envelopes[stored.invocation_id] = ledger_envelope
            self._grant_envelopes[stored.invocation_id] = envelope
            return IntegrationConsumeOutcome(False, True, _replayed(stored), None, True)
        stored = self._read_attempt(ledger_envelope)
        if stored is None or stored.grant_nonce != request.grant_nonce or stored.attempt_token == stored.grant_nonce:
            raise LedgerStoreUnavailable("durable_storage_unavailable")
        if stored.state is not MasterExecutionLedgerState.STARTED:
            raise LedgerStoreUnavailable("durable_storage_unavailable")
        self._require_marker_without_ttl(request.grant_nonce, ledger_envelope)
        self._envelopes[stored.invocation_id] = ledger_envelope
        self._grant_envelopes[stored.invocation_id] = envelope
        return IntegrationConsumeOutcome(True, False, _claimed(stored), None, True)

    def ledger_envelope(self, invocation_id: str) -> MasterExecutionLedgerEnvelope:
        found = self._envelopes.get(invocation_id)
        if found is None:
            raise ContractValidationError("ledger_fingerprint_mismatch")
        return found

    def current_fence(
        self,
        envelope: MasterExecutionLedgerEnvelope,
        *,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        grant_nonce: str,
    ) -> object:
        return self._ledger.fenced_attempt_matches(
            envelope,
            owner_id=owner_id,
            attempt_token=attempt_token,
            fence_epoch=fence_epoch,
            grant_nonce=grant_nonce,
        )

    def execute_server_read(
        self,
        attempt: DurableConsumedAttempt,
        request: UntrustedReadRequest,
        host: LocalTestHostScope,
        *,
        tool_call_id: str,
        cancellation: object = None,
    ) -> IntegratedReadResult:
        """GET_ITEM or QUERY on the reviewed physical table. Scan and SQL never reach it."""

        if type(attempt) is not DurableConsumedAttempt or type(request) is not UntrustedReadRequest:
            raise ContractValidationError("invalid_read_request")
        if type(host) is not LocalTestHostScope:
            raise ContractValidationError("host_not_issued")
        if request.operation is ReadOperation.SCAN:
            return IntegratedReadResult(AwsExecutionRefusal.SCAN_NOT_PERMITTED, None, (), False)
        if request.tool_name.casefold() in _WRITE_NAMES:
            return IntegratedReadResult(AwsExecutionRefusal.WRITE_NOT_PERMITTED, None, (), False)
        for name, value in request.requested_arguments:
            if name in _SQL_NAMES or name.casefold() in _WRITE_NAMES:
                return IntegratedReadResult(AwsExecutionRefusal.ARBITRARY_SQL, None, (), False)
            if isinstance(value, str) and any(token in value.casefold() for token in ("select ", "insert ", "update ")):
                return IntegratedReadResult(AwsExecutionRefusal.ARBITRARY_SQL, None, (), False)
        if request.caller_scope is not None or request.caller_authorized is not None:
            return IntegratedReadResult(AwsExecutionRefusal.CALLER_SCOPE_NOT_AUTHORITY, None, (), False)
        mapping = self.authorization.deployment.read_table(request.resource_id)
        if mapping is None or request.operation not in mapping.allowed_operations:
            return IntegratedReadResult(AwsExecutionRefusal.ARBITRARY_TABLE, None, (), False)
        envelope = self._envelopes.get(attempt.invocation_id)
        if envelope is None:
            return IntegratedReadResult(AwsExecutionRefusal.GRANT_MISSING, None, (), False)
        fence = self.current_fence(
            envelope,
            owner_id=attempt.owner_id,
            attempt_token=attempt.attempt_token,
            fence_epoch=attempt.fence_epoch,
            grant_nonce=attempt.grant_nonce,
        )
        if fence.matches_current_attempt is not True or fence.state is not MasterExecutionLedgerState.STARTED:
            return IntegratedReadResult(AwsExecutionRefusal.FENCE_NOT_CURRENT, None, (), False)
        if cancellation is not None and getattr(cancellation, "is_cancelled", lambda: False)():
            return IntegratedReadResult(AwsExecutionRefusal.DEADLINE_EXPIRED, None, (), False)
        arguments = dict(request.requested_arguments)
        aircraft_id = arguments.get("aircraft_id")
        hints = EffectiveScopeRequestHints(
            master_request_id=attempt.master_request_id,
            planning_session_id=attempt.planning_session_id,
            step_id=attempt.step_id,
            invocation_id=attempt.invocation_id,
            correlation_id=attempt.correlation_id,
            idempotency_key=attempt.idempotency_key,
            specialist=attempt.specialist,
            purpose=attempt.purpose,
            capability=attempt.capability,
            tool_name=request.tool_name,
            resource_id=request.resource_id,
            operation=request.operation,
            accepted_at_utc=attempt.accepted_at_utc,
            deadline_utc=attempt.deadline_utc,
            arguments=request.requested_arguments,
            aircraft_id=aircraft_id if isinstance(aircraft_id, str) else None,
        )
        decision = resolve_master_authorized_scope(hints, host)
        if decision.status is not EffectiveScopeStatus.BOUNDED_DESCRIBED:
            return IntegratedReadResult(AwsExecutionRefusal.SCOPE_DENIED, None, (), False)
        grant_envelope = self._grant_envelopes[attempt.invocation_id]
        captured: dict[str, object] = {}

        def read(executed_arguments: dict[str, object], host_cancellation: object) -> NonproductionReadPage:
            if getattr(host_cancellation, "is_cancelled", lambda: False)():
                raise ContractValidationError("cancelled")
            plan = plan_server_owned_aws_read(request.resource_id, request.operation, executed_arguments)
            kwargs = dict(plan.kwargs)
            kwargs["TableName"] = mapping.physical_table_name
            if plan.method == "query" and mapping.query_index_name is not None:
                kwargs["IndexName"] = mapping.query_index_name
            response = getattr(self._read_client, plan.method)(**kwargs)
            captured["response"] = response
            row_limit = 1 if request.operation is ReadOperation.GET_ITEM else int(kwargs["Limit"])
            page = interpret_server_owned_aws_page(response, plan, now_utc=self._now, row_limit=row_limit)
            return NonproductionReadPage(
                record_ids=page.record_ids,
                source_name=mapping.source_name,
                source_version=page.source_version,
                query_id=response.get("query_id") if isinstance(response, dict) and isinstance(response.get("query_id"), str) else None,
                page_index=page.page_index,
                has_more=page.has_more,
                truncated=page.truncated,
                source_observed_at_utc=page.source_observed_at_utc,
                next_page_token=page.next_page_token,
            )

        if cancellation is None:
            cancellation = bind_nonproduction_cancellation()
        registry = bind_nonproduction_read_registry(
            (
                NonproductionAdapterSpec(
                    specialist=attempt.specialist,
                    tool_name=request.tool_name,
                    capability=attempt.capability,
                    resource_id=request.resource_id,
                    operation=request.operation,
                    read=read,
                ),
            )
        )
        invocation = bind_nonproduction_test_invocation(
            master_request_id=attempt.master_request_id,
            planning_session_id=attempt.planning_session_id,
            step_id=attempt.step_id,
            invocation_id=attempt.invocation_id,
            specialist=attempt.specialist,
            purpose=attempt.purpose,
            capability=attempt.capability,
            tool_name=request.tool_name,
            tool_call_id=tool_call_id,
            resource_id=request.resource_id,
            operation=request.operation,
            principal_ref=attempt.principal_ref,
            policy_revision=attempt.policy_revision,
            accepted_at_utc=attempt.accepted_at_utc,
            deadline_utc=attempt.deadline_utc,
            idempotency_key=attempt.idempotency_key,
            grant_nonce=attempt.grant_nonce,
            executions_used=0,
            model_turns_used=0,
            tool_calls_used=0,
            timeout_budget_seconds=30,
            correlation_id=attempt.correlation_id,
            historical_start_utc=grant_envelope.historical_window[0] if grant_envelope.historical_window else None,
            historical_end_utc=grant_envelope.historical_window[1] if grant_envelope.historical_window else None,
        )
        receipt = MasterTrustedReadExecutor(
            registry,
            bind_nonproduction_host_clock(self._now),
        ).execute(request, host_invocation=invocation, cancellation=cancellation)
        decoded = _items_from_response(captured.get("response"))
        if receipt.outcome is not ReadExecutionOutcome.COMPLETED:
            return IntegratedReadResult(AwsExecutionRefusal.READ_NOT_COMPLETED, receipt, tuple(decoded), bool(captured))
        if receipt.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            raise ContractValidationError("source_authenticity_not_established")
        return IntegratedReadResult(None, receipt, tuple(decoded), True)

    def execute_evidence(
        self,
        attempt: DurableConsumedAttempt,
        invocation: SourceReadInvocation,
        *,
        observation_clock_utc: str,
        evaluation_clock_utc: str,
        caller_execute: object = None,
    ) -> tuple[object, SourceEvidenceAssessment]:
        """Run the host adapter against the authorized client and evaluate that page."""

        if caller_execute is not None:
            raise ContractValidationError("caller_adapter_rejected")
        if type(attempt) is not DurableConsumedAttempt or type(invocation) is not SourceReadInvocation:
            raise ContractValidationError("consumed_grant_required")
        logical_resource = _logical_resource(invocation.resource_id, invocation.operation)
        mapping = self.authorization.deployment.read_table(logical_resource)
        if mapping is None or invocation.operation not in mapping.allowed_operations:
            raise ContractValidationError("arbitrary_table")
        host = SourceEvidenceHost(_ObservationClock(observation_clock_utc))

        def execute(source_invocation: SourceReadInvocation) -> AwsSourcePage:
            if source_invocation is not invocation:
                raise ContractValidationError("invocation_mismatch")
            if source_invocation.operation is ReadOperation.GET_ITEM:
                key_name = mapping.hash_attribute
                response = self._read_client.get_item(
                    TableName=mapping.physical_table_name,
                    Key={key_name: {"S": str(source_invocation.requested_record_id)}},
                    ConsistentRead=True,
                )
            else:
                plan = plan_server_owned_aws_read(
                    source_invocation.resource_id,
                    source_invocation.operation,
                    {"aircraft_id": source_invocation.expected_aircraft_id or source_invocation.requested_record_id},
                )
                kwargs = dict(plan.kwargs)
                kwargs["TableName"] = mapping.physical_table_name
                response = self._read_client.query(**kwargs)
            return _page_from_authorized_response(
                response,
                mapping_source_name=mapping.source_name,
                version_attribute=mapping.source_version_attribute,
                observation_attribute=mapping.observation_time_attribute,
                resource_id=source_invocation.resource_id,
                evaluation_clock_utc=evaluation_clock_utc,
            )

        approval = host.approve_aws_adapter(
            f"adapter-{attempt.invocation_id}",
            mapping.source_name,
            execute,
        )
        receipt = host.execute(approval, attempt, invocation)
        return receipt, evaluate_source_evidence(host, receipt)

    def open_native_dispatch(
        self,
        registry: object,
        record: object,
        invocation: object,
        attempt: DurableConsumedAttempt,
        grant_envelope: MasterExecutionGrantEnvelope,
        *,
        now_utc: str,
    ) -> tuple[Sprint3CNativeSpecialistOrchestrator, Sprint3CConsumedDispatchAuthority]:
        """Bind the consumed fence to native dispatch without a second signed grant.

        ``seal_principal`` only wraps the host-minted principal reference.
        The scheduler consumption ticket is not a grant signature. The ledger
        nonce remains the HMAC nonce.
        """

        envelope = self._envelopes.get(attempt.invocation_id)
        if envelope is None or envelope.fingerprint != attempt.payload_fingerprint:
            raise ContractValidationError("ledger_fingerprint_mismatch")
        seam = open_local_test_host_seam()
        reservation_ledger = InProcessLedgerReservationDouble()
        port = _SeamOnlyDispatchPort()
        adapter = MasterAuthorizedSchedulerAdapter(
            registry,  # type: ignore[arg-type]
            record,  # type: ignore[arg-type]
            deadline_utc=attempt.deadline_utc,
            host_seam=seam,
            ledger=reservation_ledger,
            dispatch_port=port,
            integration_seal=open_nonproduction_scheduler_seal(),
        )
        context, reservation, staged = adapter.stage_dispatch(invocation, now_utc=now_utc)  # type: ignore[arg-type]
        consumed_grant, _consumed_reservation = adapter.consume_staged(
            invocation,  # type: ignore[arg-type]
            context,
            reservation,
            staged,
        )
        principal = LocalHarnessGrantIssuer(GrantEvaluationClock(_utc(now_utc))).seal_principal(attempt.principal_ref)
        authority = Sprint3CConsumedDispatchAuthority(
            grant=consumed_grant,
            owner_id=attempt.owner_id,
            attempt_token=attempt.attempt_token,
            fence_epoch=attempt.fence_epoch,
            grant_nonce=attempt.grant_nonce,
            principal=principal,
            grant_envelope=grant_envelope,
            ledger_envelope=envelope,
            idempotency_key=attempt.idempotency_key,
            payload_fingerprint=attempt.payload_fingerprint,
            ledger_state=MasterExecutionLedgerState.STARTED,
        )
        orchestrator = Sprint3CNativeSpecialistOrchestrator(
            adapter,
            host_seam=seam,
            execution_ledger=self._ledger,
        )
        return orchestrator, authority

    def _read_attempt(self, envelope: MasterExecutionLedgerEnvelope) -> DurableConsumedAttempt | None:
        view = self._store.read(*ledger_item_key(envelope))
        if view.record is None:
            return None
        return _attempt_from_view(view.record, envelope)

    def _require_marker_without_ttl(self, nonce: str, envelope: MasterExecutionLedgerEnvelope) -> None:
        pk, sk = consumption_marker_key(nonce, envelope)
        response = self._ledger_client.get_item(
            TableName=DYNAMODB_LEDGER_TABLE_NAME,
            Key={"pk": {"S": pk}, "sk": {"S": sk}},
            ConsistentRead=True,
        )
        item = response.get("Item") if isinstance(response, dict) else None
        if not isinstance(item, dict):
            raise LedgerStoreUnavailable("durable_storage_unavailable")
        if _MARKER_TTL_NAMES & set(item):
            raise ContractValidationError("consume_marker_must_not_have_ttl")
        if item.get("grant_nonce", {}).get("S") != nonce:
            raise LedgerStoreUnavailable("durable_storage_unavailable")


def _logical_resource(scope: str, operation: ReadOperation) -> str:
    suffix = f":{operation.value}"
    if not scope.endswith(suffix):
        raise ContractValidationError("invocation_mismatch")
    logical = scope[: -len(suffix)]
    if not logical or ":" in logical or "," in logical or "#" in logical:
        raise ContractValidationError("invocation_mismatch")
    return logical


def _items_from_response(response: object) -> list[dict[str, object]]:
    if not isinstance(response, dict):
        return []
    item = response.get("Item")
    if isinstance(item, dict):
        return [_decode_item(item)]
    items = response.get("Items")
    if isinstance(items, list):
        return [_decode_item(entry) for entry in items if isinstance(entry, dict)]
    return []


def _optional_text(item: Mapping[str, object], name: str) -> str | None:
    value = item.get(name)
    if isinstance(value, str) and value:
        return value
    return None


def _optional_float(item: Mapping[str, object], name: str) -> float | None:
    value = item.get(name)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _page_from_authorized_response(
    response: object,
    *,
    mapping_source_name: str,
    version_attribute: str,
    observation_attribute: str,
    resource_id: str,
    evaluation_clock_utc: str,
) -> AwsSourcePage:
    decoded = _items_from_response(response)
    records: list[ObservedSourceRecord] = []
    versions: list[str] = []
    query_clock = None
    for item in decoded:
        version = _optional_text(item, version_attribute)
        if version is not None:
            versions.append(version)
        if query_clock is None:
            query_clock = _optional_text(item, observation_attribute)
        record_id = _optional_text(item, "record_id") or _optional_text(item, "aircraft_id") or _optional_text(item, "hazard_id")
        if record_id is None:
            continue
        records.append(
            ObservedSourceRecord(
                record_id=record_id,
                source_version=version,
                aircraft_id=_optional_text(item, "aircraft_id"),
                hazard_id=_optional_text(item, "hazard_id"),
                longitude=_optional_float(item, "longitude"),
                latitude=_optional_float(item, "latitude"),
            )
        )
    unique_versions = set(versions)
    source_version = versions[0] if len(unique_versions) == 1 and len(versions) == len(decoded) and decoded else None
    response_query = response.get("query_id") if isinstance(response, dict) else None
    freshness_text = decoded[0].get("freshness") if decoded else None
    freshness = None
    if isinstance(freshness_text, str):
        try:
            freshness = FreshnessStatus(freshness_text)
        except ValueError:
            freshness = None
    last_key = response.get("LastEvaluatedKey") if isinstance(response, dict) else None
    has_more = isinstance(last_key, dict) and bool(last_key)
    return AwsSourcePage(
        source_name=mapping_source_name,
        records=tuple(records),
        source_version=source_version,
        query_id=response_query if isinstance(response_query, str) else None,
        evaluation_clock_utc=evaluation_clock_utc,
        query_clock_utc=query_clock,
        freshness=freshness,
        page_index=0,
        page_count=None if has_more else 1,
        truncated=has_more,
        has_more=has_more,
        rows_returned=len(records),
        resource_id=resource_id,
    )
