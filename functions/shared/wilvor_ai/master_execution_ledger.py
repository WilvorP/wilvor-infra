"""Durable-compatible claim ledger for one specialist invocation.

MasterExecutionFanout guards its schedule with ``threading.Lock``. That lock
is process-local. Two Lambda runtimes can both pass it. This module is the
cross-instance claim port those runtimes still need: ``reserve_once``,
``mark_started``, ``complete``, ``fail``, and ``cancel``.

The claim key is the host-supplied principal reference, planning session,
master request, exact invocation, and ``MasterPurpose`` (the idempotency
purpose). ``idempotency_key`` is stored inside the payload fingerprint.
Changing it must not open a second attempt. A grant nonce is created here
with the default ``secrets`` source and is kept across an ownership takeover.
Callers cannot choose the nonce. The nonce is not a production grant.

Atomicity is a conditional write on one item. A lease bounds how long a
reserved attempt keeps the owner. Expiry of a reservation that never started
allows a fenced takeover. Expiry after ``mark_started`` does not allow another
non-idempotent start unless the original envelope set
``repeat_non_idempotent_after_expiry`` to the boolean ``True``. A later
envelope cannot turn that flag on. A late worker whose attempt token or fence
no longer matches cannot complete the attempt that replaced it.

Retention is an epoch attribute intended for a later table TTL setting.
Passing that epoch does not delete the item and does not reopen a terminal
attempt. Asynchronous TTL deletion is not a transition. The in-memory test
seam records a tombstone and then refuses a new claim. The DynamoDB adapter
never deletes.

Trust boundary. This module does not authenticate ``principal_ref``, does not
read an API Gateway event, and does not accept an environment variable as a
mode switch. A descriptive claim result keeps execution authority ``DENIED``,
production authority ``DENIED``, and source authenticity ``NOT_ESTABLISHED``.
``specialist_execution_permitted`` stays false. In-memory state is never
production cross-instance authority. ``for_durable`` accepts only the exact
DynamoDB adapter type. The adapter requires an injected client, speaks only
consistent ``GetItem`` and conditional ``PutItem``, and creates no client.
The table below is documentation for a later review. This module does not
provision it.

Internal ledger writes are not specialist read permissions. Specialist reads
remain ``GET_ITEM`` and ``QUERY`` under ``master_read_resource_guard``. This
ledger does not call those tools and does not authorize them.

Who can invoke a future Lambda is an IAM question this library cannot answer.
The library is not a handler. A direct invoke of a future handler could pass
any envelope. Production wiring has to authenticate the principal before it
builds ``MasterExecutionLedgerEnvelope``.

PROPOSED, not imported:

- ``MasterExecutionFanout.claim`` remains the process-local schedule. A
  future host may call this ledger before a specialist runs. This module
  does not import the fanout and does not treat a fanout claim as durable.
- ``LocalCapabilityRegistry.consume`` remains a separate process-local
  capability transition. This module does not consume a capability.
- ``server_read_budget`` is an admission ceiling. The read guard remains
  the enforcer at tool execution. Route ceilings stay ``MAX_ROUTE_STEPS``
  and ``MAX_DECISION_STEPS`` on the fanout. This ledger does not raise them.
- A production principal issuer is not integrated. Nothing here promotes a
  claim into that issuer's grant.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Mapping

from wilvor_ai.contracts import (
    ALLOWED_V1_CAPABILITIES,
    PROVENANCE_TOKEN_MAX_LENGTH,
    AgentCapability,
    ContractValidationError,
    _validate_bounded_text,
    _validate_utc,
)
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_read_resource_guard import server_read_budget
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS,
    MAX_HISTORICAL_WINDOW,
    MAX_ROUTE_STEPS,
    MasterRouteExecutionAuthority,
)


MASTER_EXECUTION_LEDGER_SCHEMA_VERSION = "wilvor.ai.master_execution_ledger.v1"
DYNAMODB_LEDGER_TABLE_NAME = "wilvor_master_execution_ledger"
MAX_LEASE_SECONDS = 120
RETENTION_AFTER_DEADLINE = timedelta(days=30)

PROCESS_LOCAL_LOCK_IS_DISTRIBUTED_ATOMICITY = False
IN_MEMORY_IS_PRODUCTION_CROSS_INSTANCE_AUTHORITY = False
GRANT_NONCE_IS_PRODUCTION_GRANT = False
TTL_DELETION_IS_CORRECTNESS = False
LEDGER_WRITES_ARE_SPECIALIST_READ_PERMISSIONS = False
REGION_SCOPE_IS_GEOGRAPHIC_VERIFICATION = False
PRINCIPAL_REF_IS_AUTHENTICATED_BY_THIS_MODULE = False

CONDITIONAL_PUT_ABSENT = "attribute_not_exists(#pk) AND attribute_not_exists(#sk)"
_FENCE_CLAUSES = (
    "#state = :state AND #owner_id = :owner_id AND #attempt_token = :attempt_token "
    "AND #fence_epoch = :fence_epoch AND #payload_fingerprint = :payload_fingerprint "
    "AND #grant_nonce = :grant_nonce"
)
CONDITIONAL_REPLACE_EXACT = _FENCE_CLAUSES
CONDITIONAL_REPLACE_UNEXPIRED = _FENCE_CLAUSES + " AND #lease_expires_at_epoch > :now_epoch"
CONDITIONAL_REPLACE_EXPIRED = _FENCE_CLAUSES + " AND #lease_expires_at_epoch <= :now_epoch"
CONDITIONAL_REPLACE_EXPIRED_REPEATABLE = (
    CONDITIONAL_REPLACE_EXPIRED + " AND #repeat_non_idempotent_after_expiry = :repeat_non_idempotent_after_expiry"
)

# Reviewed later. Not created, described, or written by Terraform from here.
DYNAMODB_LEDGER_SCHEMA: dict[str, object] = {
    "table_name": DYNAMODB_LEDGER_TABLE_NAME,
    "provisioned_by_this_module": False,
    "partition_key": {"name": "pk", "type": "S"},
    "sort_key": {"name": "sk", "type": "S"},
    "partition_key_format": "ledger-v1#{principal_ref}#{planning_session_id}#{master_request_id}",
    "sort_key_format": "{invocation_id}#{purpose}",
    "idempotency_purpose_attribute": "purpose",
    "idempotency_key_in_primary_key": False,
    "ttl_attribute": "retention_expires_at_epoch",
    "ttl_attribute_type": "N",
    "ttl_deletion_is_correctness": False,
    "consistent_read": True,
    "read_api": "GetItem",
    "write_api": "PutItem",
    "conditional_put_absent": CONDITIONAL_PUT_ABSENT,
    "conditional_replace_exact": CONDITIONAL_REPLACE_EXACT,
    "conditional_replace_unexpired_lease": CONDITIONAL_REPLACE_UNEXPIRED,
    "conditional_replace_expired_lease": CONDITIONAL_REPLACE_EXPIRED,
    "conditional_replace_expired_repeatable": CONDITIONAL_REPLACE_EXPIRED_REPEATABLE,
    "prohibited_apis": (
        "Scan",
        "Query",
        "DeleteItem",
        "BatchWriteItem",
        "UpdateItem",
        "TransactWriteItems",
        "CreateTable",
    ),
    "specialist_read_permissions_granted": False,
}

INTEGRATION_DEPENDENCIES: tuple[str, ...] = (
    "Host authenticates principal_ref before building an envelope. This module does not.",
    "MasterExecutionFanout.claim is process-local and is not imported or consumed here.",
    "Local capability consumption is a separate registry and is not imported here.",
    "server_read_budget is an admission ceiling. Tool execution stays in master_read_resource_guard.",
    "MAX_ROUTE_STEPS and MAX_DECISION_STEPS stay on the fanout. This ledger does not raise them.",
    "DynamoDB table wilvor_master_execution_ledger is documented for later review and is not provisioned.",
    "Production grant issuance is not integrated. A claim result is not that grant.",
)

_TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
_MIN_GENERATED_TOKEN_LENGTH = 8
_GENERATED_TOKEN_ATTEMPTS = 5
_RETENTION_SECONDS = int(RETENTION_AFTER_DEADLINE.total_seconds())


class LedgerStoreUnavailable(RuntimeError):
    """The durable store did not accept a claim. No success result is returned."""


class MasterExecutionLedgerState(str, Enum):
    """Ledger state. None of these values authorizes a specialist to run."""

    RESERVED = "RESERVED"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class MasterExecutionLedgerOutcome(str, Enum):
    """What this call did to the ledger. Not an execution grant."""

    CLAIMED = "CLAIMED"
    REPLAYED = "REPLAYED"
    REJECTED = "REJECTED"


class MasterExecutionLedgerRejection(str, Enum):
    """Why a call did not take a new transition."""

    CLAIM_HELD = "CLAIM_HELD"
    CONFLICTING_DUPLICATE = "CONFLICTING_DUPLICATE"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    FENCED_OUT = "FENCED_OUT"
    OWNER_MISMATCH = "OWNER_MISMATCH"
    NON_IDEMPOTENT_REPEAT_PROHIBITED = "NON_IDEMPOTENT_REPEAT_PROHIBITED"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
    RETENTION_GAP = "RETENTION_GAP"
    ALREADY_SETTLED = "ALREADY_SETTLED"


def _assert_closed_constants() -> None:
    if PROCESS_LOCAL_LOCK_IS_DISTRIBUTED_ATOMICITY is not False:
        raise ContractValidationError("distributed_atomicity_not_claimed")
    if IN_MEMORY_IS_PRODUCTION_CROSS_INSTANCE_AUTHORITY is not False:
        raise ContractValidationError("in_memory_not_cross_instance_authority")
    if GRANT_NONCE_IS_PRODUCTION_GRANT is not False:
        raise ContractValidationError("grant_nonce_is_not_a_production_grant")
    if TTL_DELETION_IS_CORRECTNESS is not False:
        raise ContractValidationError("ttl_deletion_is_not_correctness")
    if LEDGER_WRITES_ARE_SPECIALIST_READ_PERMISSIONS is not False:
        raise ContractValidationError("ledger_write_is_not_a_read_permission")
    if REGION_SCOPE_IS_GEOGRAPHIC_VERIFICATION is not False:
        raise ContractValidationError("region_scope_is_not_geographic_verification")
    if PRINCIPAL_REF_IS_AUTHENTICATED_BY_THIS_MODULE is not False:
        raise ContractValidationError("principal_ref_not_authenticated_here")


def _token(value: object, field_name: str) -> str:
    errors = _validate_bounded_text(
        value,
        field_name,
        max_length=PROVENANCE_TOKEN_MAX_LENGTH,
    )
    if errors:
        raise ContractValidationError(errors)
    if (
        not isinstance(value, str)
        or value != value.strip()
        or any(character.isspace() for character in value)
        or "#" in value
    ):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _whole_second(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or len(value) != 20 or value[10] != "T" or not value.endswith("Z"):
        raise ContractValidationError(f"invalid_{field_name}")
    errors = _validate_utc(value, field_name)
    if errors:
        raise ContractValidationError(errors)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ContractValidationError(f"invalid_{field_name}") from exc


def _format_utc(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(value: datetime) -> int:
    return int(value.timestamp())


def _lease_seconds(value: object) -> int:
    if type(value) is not int or value < 1 or value > MAX_LEASE_SECONDS:
        raise ContractValidationError("invalid_lease_seconds")
    return value


def _fence_epoch(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ContractValidationError("invalid_fence_epoch")
    return value


def _validate_window(specialist: MasterSpecialist, start: str | None, end: str | None) -> None:
    historical = specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    if not historical:
        if start is not None or end is not None:
            raise ContractValidationError("contradictory_temporal_bounds")
        return
    if start is None or end is None:
        raise ContractValidationError("missing_historical_interval")
    parsed_start = _whole_second(start, "historical_start_utc")
    parsed_end = _whole_second(end, "historical_end_utc")
    if parsed_start >= parsed_end:
        raise ContractValidationError("contradictory_temporal_bounds")
    if parsed_end - parsed_start > MAX_HISTORICAL_WINDOW:
        raise ContractValidationError("historical_window_exceeds_seven_days")


def _validate_budget(specialist: MasterSpecialist, turns: object, tools: object) -> tuple[int, int]:
    if type(turns) is not int or type(tools) is not int or turns < 1 or tools < 1:
        raise ContractValidationError("invalid_budget")
    executions, max_turns, max_tools = server_read_budget(specialist)
    if executions < 1:
        raise ContractValidationError("invalid_budget")
    if turns > max_turns:
        raise ContractValidationError("max_model_turns_exceeds_server_ceiling")
    if tools > max_tools:
        raise ContractValidationError("max_tool_calls_exceeds_server_ceiling")
    return turns, tools


def _validate_capability(capability: object) -> AgentCapability:
    if not isinstance(capability, AgentCapability):
        raise ContractValidationError("invalid_capability")
    if capability not in ALLOWED_V1_CAPABILITIES:
        raise ContractValidationError("prohibited_capability")
    return capability


def _fingerprint(material: Mapping[str, object]) -> str:
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _material(
    *,
    master_request_id: str,
    planning_session_id: str,
    step_id: str,
    invocation_id: str,
    specialist: MasterSpecialist,
    purpose: MasterPurpose,
    capability: AgentCapability,
    principal_ref: str,
    policy_revision: str,
    resource_scope: str,
    region_scope: str,
    decision_target_scope: str,
    accepted_at_utc: str,
    deadline_utc: str,
    idempotency_key: str,
    max_model_turns: int,
    max_tool_calls: int,
    correlation_id: str,
    historical_start_utc: str | None,
    historical_end_utc: str | None,
    repeat_non_idempotent_after_expiry: bool,
) -> dict[str, object]:
    return {
        "schema_version": MASTER_EXECUTION_LEDGER_SCHEMA_VERSION,
        "master_request_id": master_request_id,
        "planning_session_id": planning_session_id,
        "step_id": step_id,
        "invocation_id": invocation_id,
        "specialist": specialist.value,
        "purpose": purpose.value,
        "capability": capability.value,
        "principal_ref": principal_ref,
        "policy_revision": policy_revision,
        "resource_scope": resource_scope,
        "region_scope": region_scope,
        "decision_target_scope": decision_target_scope,
        "accepted_at_utc": accepted_at_utc,
        "deadline_utc": deadline_utc,
        "idempotency_key": idempotency_key,
        "max_model_turns": max_model_turns,
        "max_tool_calls": max_tool_calls,
        "correlation_id": correlation_id,
        "historical_start_utc": historical_start_utc,
        "historical_end_utc": historical_end_utc,
        "repeat_non_idempotent_after_expiry": repeat_non_idempotent_after_expiry,
    }


def _partition_key(principal_ref: str, planning_session_id: str, master_request_id: str) -> str:
    return f"ledger-v1#{principal_ref}#{planning_session_id}#{master_request_id}"


def _sort_key(invocation_id: str, purpose: MasterPurpose) -> str:
    return f"{invocation_id}#{purpose.value}"


def _retention_epoch(deadline_utc: str) -> int:
    return _epoch(_whole_second(deadline_utc, "deadline_utc")) + _RETENTION_SECONDS


def conditional_replace_expression(lease_relation: str, require_repeat_allowed: bool) -> str:
    """Conditional PutItem expression for one fenced replacement."""

    if lease_relation == "any":
        expression = CONDITIONAL_REPLACE_EXACT
    elif lease_relation == "unexpired":
        expression = CONDITIONAL_REPLACE_UNEXPIRED
    elif lease_relation == "expired":
        expression = CONDITIONAL_REPLACE_EXPIRED
    else:
        raise ContractValidationError("invalid_lease_relation")
    if require_repeat_allowed:
        if lease_relation != "expired":
            raise ContractValidationError("invalid_lease_relation")
        return CONDITIONAL_REPLACE_EXPIRED_REPEATABLE
    return expression


@dataclass(frozen=True)
class MasterExecutionLedgerEnvelope:
    """Descriptive host context bound into one claim. Not an authentication token.

    Values are accepted as already validated host or planning context. This
    object does not check that claim. ``principal_ref`` is an opaque key
    component. ``region_scope`` is not a geographic verification.
    ``accepted_at_utc`` and ``deadline_utc`` are the application clocks.
    Each ledger call passes a separate observation clock.
    """

    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    principal_ref: str
    policy_revision: str
    resource_scope: str
    region_scope: str
    decision_target_scope: str
    accepted_at_utc: str
    deadline_utc: str
    idempotency_key: str
    max_model_turns: int
    max_tool_calls: int
    correlation_id: str
    historical_start_utc: str | None = None
    historical_end_utc: str | None = None
    repeat_non_idempotent_after_expiry: bool = False

    def __post_init__(self) -> None:
        _assert_closed_constants()
        _token(self.master_request_id, "master_request_id")
        _token(self.planning_session_id, "planning_session_id")
        _token(self.step_id, "step_id")
        _token(self.invocation_id, "invocation_id")
        _token(self.principal_ref, "principal_ref")
        _token(self.policy_revision, "policy_revision")
        _token(self.resource_scope, "resource_scope")
        _token(self.region_scope, "region_scope")
        _token(self.decision_target_scope, "decision_target_scope")
        _token(self.idempotency_key, "idempotency_key")
        _token(self.correlation_id, "correlation_id")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        _validate_capability(self.capability)
        if type(self.repeat_non_idempotent_after_expiry) is not bool:
            raise ContractValidationError("invalid_repeat_policy")
        accepted = _whole_second(self.accepted_at_utc, "accepted_at_utc")
        deadline = _whole_second(self.deadline_utc, "deadline_utc")
        if accepted >= deadline:
            raise ContractValidationError("contradictory_application_clocks")
        _validate_window(self.specialist, self.historical_start_utc, self.historical_end_utc)
        _validate_budget(self.specialist, self.max_model_turns, self.max_tool_calls)

    @property
    def partition_key(self) -> str:
        return _partition_key(self.principal_ref, self.planning_session_id, self.master_request_id)

    @property
    def sort_key(self) -> str:
        return _sort_key(self.invocation_id, self.purpose)

    @property
    def fingerprint(self) -> str:
        return _fingerprint(
            _material(
                master_request_id=self.master_request_id,
                planning_session_id=self.planning_session_id,
                step_id=self.step_id,
                invocation_id=self.invocation_id,
                specialist=self.specialist,
                purpose=self.purpose,
                capability=self.capability,
                principal_ref=self.principal_ref,
                policy_revision=self.policy_revision,
                resource_scope=self.resource_scope,
                region_scope=self.region_scope,
                decision_target_scope=self.decision_target_scope,
                accepted_at_utc=self.accepted_at_utc,
                deadline_utc=self.deadline_utc,
                idempotency_key=self.idempotency_key,
                max_model_turns=self.max_model_turns,
                max_tool_calls=self.max_tool_calls,
                correlation_id=self.correlation_id,
                historical_start_utc=self.historical_start_utc,
                historical_end_utc=self.historical_end_utc,
                repeat_non_idempotent_after_expiry=self.repeat_non_idempotent_after_expiry,
            )
        )


@dataclass(frozen=True)
class _LedgerRecord:
    """One stored attempt. Closed authority flags cannot be turned on."""

    pk: str
    sk: str
    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    principal_ref: str
    policy_revision: str
    resource_scope: str
    region_scope: str
    decision_target_scope: str
    accepted_at_utc: str
    deadline_utc: str
    idempotency_key: str
    max_model_turns: int
    max_tool_calls: int
    correlation_id: str
    historical_start_utc: str | None
    historical_end_utc: str | None
    repeat_non_idempotent_after_expiry: bool
    payload_fingerprint: str
    state: MasterExecutionLedgerState
    fence_epoch: int
    attempt_token: str
    grant_nonce: str
    owner_id: str
    lease_expires_at_epoch: int
    retention_expires_at_epoch: int
    non_idempotent_started: bool
    reserved_at_utc: str
    ledger_observed_at_utc: str
    started_at_utc: str | None = None
    terminal_at_utc: str | None = None
    failure_reason: str | None = None
    schema_version: str = MASTER_EXECUTION_LEDGER_SCHEMA_VERSION
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    specialist_executed: bool = False
    dispatch_permitted: bool = False
    grant_nonce_is_production_grant: bool = False
    ttl_deletion_is_correctness: bool = False
    ledger_writes_are_specialist_read_permissions: bool = False
    production_cross_instance_authority: bool = False

    def __post_init__(self) -> None:
        _assert_closed_constants()
        if self.schema_version != MASTER_EXECUTION_LEDGER_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if self.execution_authority is not MasterRouteExecutionAuthority.DENIED:
            raise ContractValidationError("execution_authority_not_granted")
        if self.production_authority is not MasterProductionAuthority.DENIED:
            raise ContractValidationError("production_authority_not_issued")
        if self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            raise ContractValidationError("source_authenticity_not_established")
        for flag in (
            self.specialist_executed,
            self.dispatch_permitted,
            self.grant_nonce_is_production_grant,
            self.ttl_deletion_is_correctness,
            self.ledger_writes_are_specialist_read_permissions,
            self.production_cross_instance_authority,
        ):
            if flag is not False:
                raise ContractValidationError("execution_not_granted")
        if self.pk != _partition_key(self.principal_ref, self.planning_session_id, self.master_request_id):
            raise ContractValidationError("invalid_ledger_key")
        if self.sk != _sort_key(self.invocation_id, self.purpose):
            raise ContractValidationError("invalid_ledger_key")
        if not isinstance(self.state, MasterExecutionLedgerState):
            raise ContractValidationError("invalid_ledger_state")
        if type(self.fence_epoch) is not int or self.fence_epoch < 1:
            raise ContractValidationError("invalid_fence_epoch")
        if type(self.lease_expires_at_epoch) is not int or type(self.retention_expires_at_epoch) is not int:
            raise ContractValidationError("invalid_ledger_epoch")
        if type(self.non_idempotent_started) is not bool:
            raise ContractValidationError("invalid_ledger_state")
        if type(self.repeat_non_idempotent_after_expiry) is not bool:
            raise ContractValidationError("invalid_repeat_policy")
        _token(self.attempt_token, "attempt_token")
        _token(self.grant_nonce, "grant_nonce")
        _token(self.owner_id, "owner_id")
        if self.attempt_token == self.grant_nonce:
            raise ContractValidationError("attempt_token_not_unique")
        expected_fingerprint = _fingerprint(
            _material(
                master_request_id=self.master_request_id,
                planning_session_id=self.planning_session_id,
                step_id=self.step_id,
                invocation_id=self.invocation_id,
                specialist=self.specialist,
                purpose=self.purpose,
                capability=self.capability,
                principal_ref=self.principal_ref,
                policy_revision=self.policy_revision,
                resource_scope=self.resource_scope,
                region_scope=self.region_scope,
                decision_target_scope=self.decision_target_scope,
                accepted_at_utc=self.accepted_at_utc,
                deadline_utc=self.deadline_utc,
                idempotency_key=self.idempotency_key,
                max_model_turns=self.max_model_turns,
                max_tool_calls=self.max_tool_calls,
                correlation_id=self.correlation_id,
                historical_start_utc=self.historical_start_utc,
                historical_end_utc=self.historical_end_utc,
                repeat_non_idempotent_after_expiry=self.repeat_non_idempotent_after_expiry,
            )
        )
        if self.payload_fingerprint != expected_fingerprint:
            raise ContractValidationError("payload_fingerprint_mismatch")
        reserved_epoch = _epoch(_whole_second(self.reserved_at_utc, "reserved_at_utc"))
        _whole_second(self.ledger_observed_at_utc, "ledger_observed_at_utc")
        deadline_epoch = _epoch(_whole_second(self.deadline_utc, "deadline_utc"))
        if self.lease_expires_at_epoch <= reserved_epoch or self.lease_expires_at_epoch > deadline_epoch:
            raise ContractValidationError("invalid_lease")
        if self.retention_expires_at_epoch != deadline_epoch + _RETENTION_SECONDS:
            raise ContractValidationError("invalid_retention")
        _validate_window(self.specialist, self.historical_start_utc, self.historical_end_utc)
        _validate_budget(self.specialist, self.max_model_turns, self.max_tool_calls)
        if self.started_at_utc is not None:
            _whole_second(self.started_at_utc, "started_at_utc")
        if self.terminal_at_utc is not None:
            _whole_second(self.terminal_at_utc, "terminal_at_utc")
        if self.state is MasterExecutionLedgerState.RESERVED:
            if (
                self.started_at_utc is not None
                or self.terminal_at_utc is not None
                or self.non_idempotent_started is not False
                or self.failure_reason is not None
            ):
                raise ContractValidationError("invalid_ledger_state")
        elif self.state is MasterExecutionLedgerState.STARTED:
            if (
                self.started_at_utc is None
                or self.terminal_at_utc is not None
                or self.non_idempotent_started is not True
                or self.failure_reason is not None
            ):
                raise ContractValidationError("invalid_ledger_state")
        elif self.state is MasterExecutionLedgerState.COMPLETED:
            if (
                self.started_at_utc is None
                or self.terminal_at_utc is None
                or self.non_idempotent_started is not True
                or self.failure_reason is not None
            ):
                raise ContractValidationError("invalid_ledger_state")
        elif self.state is MasterExecutionLedgerState.FAILED:
            if (
                self.started_at_utc is None
                or self.terminal_at_utc is None
                or self.non_idempotent_started is not True
            ):
                raise ContractValidationError("invalid_ledger_state")
            _token(self.failure_reason, "failure_reason")
        elif self.state is MasterExecutionLedgerState.CANCELLED:
            if (
                self.terminal_at_utc is None
                or self.started_at_utc is not None
                or self.non_idempotent_started is not False
                or self.failure_reason is not None
            ):
                raise ContractValidationError("invalid_ledger_state")
        else:
            raise ContractValidationError("invalid_ledger_state")


@dataclass(frozen=True)
class _LedgerCondition:
    state: MasterExecutionLedgerState
    fence_epoch: int
    attempt_token: str
    owner_id: str
    payload_fingerprint: str
    grant_nonce: str
    lease_relation: str
    now_epoch: int
    require_repeat_allowed: bool

    def __post_init__(self) -> None:
        if self.lease_relation not in {"any", "unexpired", "expired"}:
            raise ContractValidationError("invalid_lease_relation")
        if type(self.require_repeat_allowed) is not bool:
            raise ContractValidationError("invalid_repeat_policy")
        if type(self.fence_epoch) is not int or type(self.now_epoch) is not int:
            raise ContractValidationError("invalid_fence_epoch")
        if not isinstance(self.state, MasterExecutionLedgerState):
            raise ContractValidationError("invalid_ledger_state")


@dataclass(frozen=True)
class MasterExecutionLedgerRead:
    """Point read. ``ttl_deleted`` is only a test tombstone, never a permission."""

    record: _LedgerRecord | None
    ttl_deleted: bool = False

    def __post_init__(self) -> None:
        if type(self.ttl_deleted) is not bool:
            raise ContractValidationError("invalid_ledger_read")
        if self.record is not None and type(self.record) is not _LedgerRecord:
            raise ContractValidationError("invalid_ledger_read")
        if self.record is not None and self.ttl_deleted is not False:
            raise ContractValidationError("invalid_ledger_read")


def _condition_matches(record: _LedgerRecord, condition: _LedgerCondition) -> bool:
    if record.state is not condition.state:
        return False
    if record.owner_id != condition.owner_id or record.attempt_token != condition.attempt_token:
        return False
    if record.fence_epoch != condition.fence_epoch or record.grant_nonce != condition.grant_nonce:
        return False
    if record.payload_fingerprint != condition.payload_fingerprint:
        return False
    if condition.lease_relation == "unexpired" and not record.lease_expires_at_epoch > condition.now_epoch:
        return False
    if condition.lease_relation == "expired" and not record.lease_expires_at_epoch <= condition.now_epoch:
        return False
    if condition.require_repeat_allowed and record.repeat_non_idempotent_after_expiry is not True:
        return False
    return True


class InMemoryMasterExecutionLedgerBackend:
    """Process-local items shared by ledger objects in tests.

    A new ledger object bound to the same backend can see prior claims. That
    simulates a restarted process only inside this interpreter. The backend
    lock is still process-local. ``production_cross_instance_authority`` stays
    false and cannot be assigned.
    """

    def __init__(self) -> None:
        _assert_closed_constants()
        self._items: dict[tuple[str, str], _LedgerRecord] = {}
        self._tombstones: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        self.unavailable = False
        self.fail_writes = False

    @property
    def production_cross_instance_authority(self) -> bool:
        return False


class InMemoryMasterExecutionLedgerStore:
    """Deterministic store for unit tests. Not cross-instance authority."""

    def __init__(self, backend: InMemoryMasterExecutionLedgerBackend | None = None) -> None:
        _assert_closed_constants()
        if backend is None:
            backend = InMemoryMasterExecutionLedgerBackend()
        if type(backend) is not InMemoryMasterExecutionLedgerBackend:
            raise ContractValidationError("invalid_ledger_backend")
        self._backend = backend

    @property
    def production_cross_instance_authority(self) -> bool:
        return False

    @property
    def backend(self) -> InMemoryMasterExecutionLedgerBackend:
        return self._backend

    def read(self, partition_key: str, sort_key: str) -> MasterExecutionLedgerRead:
        key = (partition_key, sort_key)
        with self._backend._lock:
            self._guard_read()
            if key in self._backend._tombstones:
                return MasterExecutionLedgerRead(None, True)
            return MasterExecutionLedgerRead(self._backend._items.get(key), False)

    def put_if_absent(self, record: _LedgerRecord) -> bool:
        if type(record) is not _LedgerRecord:
            raise ContractValidationError("invalid_ledger_record")
        key = (record.pk, record.sk)
        with self._backend._lock:
            self._guard_write()
            if key in self._backend._tombstones or key in self._backend._items:
                return False
            self._backend._items[key] = record
            return True

    def replace_if(self, record: _LedgerRecord, condition: _LedgerCondition) -> bool:
        if type(record) is not _LedgerRecord or type(condition) is not _LedgerCondition:
            raise ContractValidationError("invalid_ledger_record")
        key = (record.pk, record.sk)
        with self._backend._lock:
            self._guard_write()
            if key in self._backend._tombstones or key not in self._backend._items:
                return False
            current = self._backend._items[key]
            if current.pk != record.pk or current.sk != record.sk:
                return False
            if not _condition_matches(current, condition):
                return False
            self._backend._items[key] = record
            return True

    def simulate_asynchronous_ttl_deletion(self, partition_key: str, sort_key: str) -> None:
        """Drop an item the way a late TTL job might, and keep a tombstone.

        The tombstone does not prove the attempt succeeded. The next claim
        fails closed with ``RETENTION_GAP``.
        """

        key = (partition_key, sort_key)
        with self._backend._lock:
            self._guard_read()
            if key not in self._backend._items:
                raise ContractValidationError("retention_record_missing")
            del self._backend._items[key]
            self._backend._tombstones.add(key)

    def _guard_read(self) -> None:
        if self._backend.unavailable is not False:
            raise LedgerStoreUnavailable("durable_storage_unavailable")

    def _guard_write(self) -> None:
        self._guard_read()
        if self._backend.fail_writes is not False:
            raise LedgerStoreUnavailable("durable_storage_unavailable")


def _attribute(value: object) -> dict[str, object]:
    if value is None:
        return {"NULL": True}
    if type(value) is bool:
        return {"BOOL": value}
    if type(value) is int:
        return {"N": str(value)}
    if type(value) is str:
        return {"S": value}
    raise ContractValidationError("invalid_ledger_attribute")


def _decode_attribute(attribute: object) -> object:
    if not isinstance(attribute, dict) or len(attribute) != 1:
        raise ContractValidationError("invalid_ledger_attribute")
    if "NULL" in attribute:
        if attribute["NULL"] is not True:
            raise ContractValidationError("invalid_ledger_attribute")
        return None
    if "BOOL" in attribute:
        if type(attribute["BOOL"]) is not bool:
            raise ContractValidationError("invalid_ledger_attribute")
        return attribute["BOOL"]
    if "N" in attribute:
        raw = attribute["N"]
        if not isinstance(raw, str) or not raw.isdigit():
            raise ContractValidationError("invalid_ledger_attribute")
        return int(raw)
    if "S" in attribute:
        if not isinstance(attribute["S"], str):
            raise ContractValidationError("invalid_ledger_attribute")
        return attribute["S"]
    raise ContractValidationError("invalid_ledger_attribute")


def _item_from_record(record: _LedgerRecord) -> dict[str, dict[str, object]]:
    return {
        "pk": _attribute(record.pk),
        "sk": _attribute(record.sk),
        "schema_version": _attribute(record.schema_version),
        "master_request_id": _attribute(record.master_request_id),
        "planning_session_id": _attribute(record.planning_session_id),
        "step_id": _attribute(record.step_id),
        "invocation_id": _attribute(record.invocation_id),
        "specialist": _attribute(record.specialist.value),
        "purpose": _attribute(record.purpose.value),
        "capability": _attribute(record.capability.value),
        "principal_ref": _attribute(record.principal_ref),
        "policy_revision": _attribute(record.policy_revision),
        "resource_scope": _attribute(record.resource_scope),
        "region_scope": _attribute(record.region_scope),
        "decision_target_scope": _attribute(record.decision_target_scope),
        "accepted_at_utc": _attribute(record.accepted_at_utc),
        "deadline_utc": _attribute(record.deadline_utc),
        "idempotency_key": _attribute(record.idempotency_key),
        "max_model_turns": _attribute(record.max_model_turns),
        "max_tool_calls": _attribute(record.max_tool_calls),
        "correlation_id": _attribute(record.correlation_id),
        "historical_start_utc": _attribute(record.historical_start_utc),
        "historical_end_utc": _attribute(record.historical_end_utc),
        "repeat_non_idempotent_after_expiry": _attribute(record.repeat_non_idempotent_after_expiry),
        "payload_fingerprint": _attribute(record.payload_fingerprint),
        "state": _attribute(record.state.value),
        "fence_epoch": _attribute(record.fence_epoch),
        "attempt_token": _attribute(record.attempt_token),
        "grant_nonce": _attribute(record.grant_nonce),
        "owner_id": _attribute(record.owner_id),
        "lease_expires_at_epoch": _attribute(record.lease_expires_at_epoch),
        "retention_expires_at_epoch": _attribute(record.retention_expires_at_epoch),
        "non_idempotent_started": _attribute(record.non_idempotent_started),
        "reserved_at_utc": _attribute(record.reserved_at_utc),
        "ledger_observed_at_utc": _attribute(record.ledger_observed_at_utc),
        "started_at_utc": _attribute(record.started_at_utc),
        "terminal_at_utc": _attribute(record.terminal_at_utc),
        "failure_reason": _attribute(record.failure_reason),
        "execution_authority": _attribute(record.execution_authority.value),
        "production_authority": _attribute(record.production_authority.value),
        "source_authenticity": _attribute(record.source_authenticity.value),
        "specialist_executed": _attribute(record.specialist_executed),
        "dispatch_permitted": _attribute(record.dispatch_permitted),
        "grant_nonce_is_production_grant": _attribute(record.grant_nonce_is_production_grant),
        "ttl_deletion_is_correctness": _attribute(record.ttl_deletion_is_correctness),
        "ledger_writes_are_specialist_read_permissions": _attribute(
            record.ledger_writes_are_specialist_read_permissions
        ),
        "production_cross_instance_authority": _attribute(record.production_cross_instance_authority),
    }


def _text_attr(item: Mapping[str, object], name: str) -> str:
    value = _decode_attribute(item[name])
    if not isinstance(value, str):
        raise ContractValidationError("invalid_ledger_attribute")
    return value


def _optional_text_attr(item: Mapping[str, object], name: str) -> str | None:
    value = _decode_attribute(item[name])
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractValidationError("invalid_ledger_attribute")
    return value


def _int_attr(item: Mapping[str, object], name: str) -> int:
    value = _decode_attribute(item[name])
    if type(value) is not int:
        raise ContractValidationError("invalid_ledger_attribute")
    return value


def _bool_attr(item: Mapping[str, object], name: str) -> bool:
    value = _decode_attribute(item[name])
    if type(value) is not bool:
        raise ContractValidationError("invalid_ledger_attribute")
    return value


def _enum_attr(enum_type: type[Enum], item: Mapping[str, object], name: str) -> Enum:
    raw = _text_attr(item, name)
    try:
        return enum_type(raw)
    except ValueError as exc:
        raise ContractValidationError(f"invalid_{name}") from exc


def _record_from_item(item: Mapping[str, object]) -> _LedgerRecord:
    try:
        return _LedgerRecord(
            pk=_text_attr(item, "pk"),
            sk=_text_attr(item, "sk"),
            schema_version=_text_attr(item, "schema_version"),
            master_request_id=_text_attr(item, "master_request_id"),
            planning_session_id=_text_attr(item, "planning_session_id"),
            step_id=_text_attr(item, "step_id"),
            invocation_id=_text_attr(item, "invocation_id"),
            specialist=_enum_attr(MasterSpecialist, item, "specialist"),  # type: ignore[arg-type]
            purpose=_enum_attr(MasterPurpose, item, "purpose"),  # type: ignore[arg-type]
            capability=_enum_attr(AgentCapability, item, "capability"),  # type: ignore[arg-type]
            principal_ref=_text_attr(item, "principal_ref"),
            policy_revision=_text_attr(item, "policy_revision"),
            resource_scope=_text_attr(item, "resource_scope"),
            region_scope=_text_attr(item, "region_scope"),
            decision_target_scope=_text_attr(item, "decision_target_scope"),
            accepted_at_utc=_text_attr(item, "accepted_at_utc"),
            deadline_utc=_text_attr(item, "deadline_utc"),
            idempotency_key=_text_attr(item, "idempotency_key"),
            max_model_turns=_int_attr(item, "max_model_turns"),
            max_tool_calls=_int_attr(item, "max_tool_calls"),
            correlation_id=_text_attr(item, "correlation_id"),
            historical_start_utc=_optional_text_attr(item, "historical_start_utc"),
            historical_end_utc=_optional_text_attr(item, "historical_end_utc"),
            repeat_non_idempotent_after_expiry=_bool_attr(item, "repeat_non_idempotent_after_expiry"),
            payload_fingerprint=_text_attr(item, "payload_fingerprint"),
            state=_enum_attr(MasterExecutionLedgerState, item, "state"),  # type: ignore[arg-type]
            fence_epoch=_int_attr(item, "fence_epoch"),
            attempt_token=_text_attr(item, "attempt_token"),
            grant_nonce=_text_attr(item, "grant_nonce"),
            owner_id=_text_attr(item, "owner_id"),
            lease_expires_at_epoch=_int_attr(item, "lease_expires_at_epoch"),
            retention_expires_at_epoch=_int_attr(item, "retention_expires_at_epoch"),
            non_idempotent_started=_bool_attr(item, "non_idempotent_started"),
            reserved_at_utc=_text_attr(item, "reserved_at_utc"),
            ledger_observed_at_utc=_text_attr(item, "ledger_observed_at_utc"),
            started_at_utc=_optional_text_attr(item, "started_at_utc"),
            terminal_at_utc=_optional_text_attr(item, "terminal_at_utc"),
            failure_reason=_optional_text_attr(item, "failure_reason"),
            execution_authority=_enum_attr(MasterRouteExecutionAuthority, item, "execution_authority"),  # type: ignore[arg-type]
            production_authority=_enum_attr(MasterProductionAuthority, item, "production_authority"),  # type: ignore[arg-type]
            source_authenticity=_enum_attr(MasterSourceAuthenticity, item, "source_authenticity"),  # type: ignore[arg-type]
            specialist_executed=_bool_attr(item, "specialist_executed"),
            dispatch_permitted=_bool_attr(item, "dispatch_permitted"),
            grant_nonce_is_production_grant=_bool_attr(item, "grant_nonce_is_production_grant"),
            ttl_deletion_is_correctness=_bool_attr(item, "ttl_deletion_is_correctness"),
            ledger_writes_are_specialist_read_permissions=_bool_attr(
                item, "ledger_writes_are_specialist_read_permissions"
            ),
            production_cross_instance_authority=_bool_attr(item, "production_cross_instance_authority"),
        )
    except KeyError as exc:
        raise ContractValidationError("invalid_ledger_attribute") from exc


def _conditional_failure(exc: BaseException) -> bool:
    if type(exc).__name__ == "ConditionalCheckFailedException":
        return True
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        error = response.get("Error")
        if isinstance(error, Mapping) and error.get("Code") == "ConditionalCheckFailedException":
            return True
    return False


class DynamoDbMasterExecutionLedgerStore:
    """Conditional-write adapter. The client is injected. No client is built here.

    ``table_name`` must be the documented ledger table. Operational specialist
    tables are rejected. A missing table or a transport error becomes
    ``LedgerStoreUnavailable``. A conditional conflict becomes ``False``.
    This store never issues a full-table read or a delete. TTL deletion, if a
    later table setting enables it, is not visible here and is not correctness.
    """

    def __init__(self, client: object, table_name: str) -> None:
        _assert_closed_constants()
        if table_name != DYNAMODB_LEDGER_TABLE_NAME:
            raise ContractValidationError("unexpected_ledger_table")
        get_item = getattr(client, "get_item", None)
        put_item = getattr(client, "put_item", None)
        if not callable(get_item) or not callable(put_item):
            raise ContractValidationError("invalid_ledger_client")
        self._client = client
        self._table_name = table_name

    @property
    def production_cross_instance_authority(self) -> bool:
        return False

    def read(self, partition_key: str, sort_key: str) -> MasterExecutionLedgerRead:
        try:
            response = self._client.get_item(  # type: ignore[attr-defined]
                TableName=self._table_name,
                Key={"pk": {"S": partition_key}, "sk": {"S": sort_key}},
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
            return MasterExecutionLedgerRead(None, False)
        if not isinstance(item, dict):
            raise LedgerStoreUnavailable("durable_storage_unavailable")
        try:
            return MasterExecutionLedgerRead(_record_from_item(item), False)
        except ContractValidationError as exc:
            raise LedgerStoreUnavailable("durable_storage_unavailable") from exc

    def put_if_absent(self, record: _LedgerRecord) -> bool:
        if type(record) is not _LedgerRecord:
            raise ContractValidationError("invalid_ledger_record")
        return self._put(
            record,
            CONDITIONAL_PUT_ABSENT,
            {"#pk": "pk", "#sk": "sk"},
            {},
        )

    def replace_if(self, record: _LedgerRecord, condition: _LedgerCondition) -> bool:
        if type(record) is not _LedgerRecord or type(condition) is not _LedgerCondition:
            raise ContractValidationError("invalid_ledger_record")
        expression = conditional_replace_expression(
            condition.lease_relation,
            condition.require_repeat_allowed,
        )
        values: dict[str, dict[str, object]] = {
            ":state": _attribute(condition.state.value),
            ":owner_id": _attribute(condition.owner_id),
            ":attempt_token": _attribute(condition.attempt_token),
            ":fence_epoch": _attribute(condition.fence_epoch),
            ":payload_fingerprint": _attribute(condition.payload_fingerprint),
            ":grant_nonce": _attribute(condition.grant_nonce),
        }
        names = {
            "#state": "state",
            "#owner_id": "owner_id",
            "#attempt_token": "attempt_token",
            "#fence_epoch": "fence_epoch",
            "#payload_fingerprint": "payload_fingerprint",
            "#grant_nonce": "grant_nonce",
        }
        if condition.lease_relation != "any":
            values[":now_epoch"] = _attribute(condition.now_epoch)
            names["#lease_expires_at_epoch"] = "lease_expires_at_epoch"
        if condition.require_repeat_allowed:
            values[":repeat_non_idempotent_after_expiry"] = _attribute(True)
            names["#repeat_non_idempotent_after_expiry"] = "repeat_non_idempotent_after_expiry"
        return self._put(record, expression, names, values)

    def _put(
        self,
        record: _LedgerRecord,
        expression: str,
        names: Mapping[str, str],
        values: Mapping[str, dict[str, object]],
    ) -> bool:
        request: dict[str, object] = {
            "TableName": self._table_name,
            "Item": _item_from_record(record),
            "ConditionExpression": expression,
            "ExpressionAttributeNames": dict(names),
        }
        if values:
            request["ExpressionAttributeValues"] = dict(values)
        try:
            self._client.put_item(**request)  # type: ignore[attr-defined]
        except LedgerStoreUnavailable:
            raise
        except Exception as exc:
            if _conditional_failure(exc):
                return False
            raise LedgerStoreUnavailable("durable_storage_unavailable") from exc
        return True


@dataclass(frozen=True)
class MasterExecutionLedgerResult:
    """Outcome of one ledger call.

    ``fresh_transition`` is true only when this call applied a new state
    change. A test dispatcher may use that bit to count work. It is not
    permission to run a specialist. ``store_is_durable_adapter`` names the
    store object. It does not prove the table exists, that the client honored
    the condition, or that execution is authorized.
    """

    outcome: MasterExecutionLedgerOutcome
    fresh_transition: bool
    replayed: bool
    rejection: MasterExecutionLedgerRejection | None = None
    state: MasterExecutionLedgerState | None = None
    attempt_token: str | None = None
    fence_epoch: int | None = None
    grant_nonce: str | None = None
    owner_id: str | None = None
    correlation_id: str | None = None
    lease_expires_at_utc: str | None = None
    accepted_at_utc: str | None = None
    deadline_utc: str | None = None
    ledger_observed_at_utc: str | None = None
    store_is_durable_adapter: bool = False
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    specialist_execution_permitted: bool = False
    specialist_executed: bool = False
    dispatch_permitted: bool = False
    grant_nonce_is_production_grant: bool = False
    production_cross_instance_authority: bool = False
    schema_version: str = MASTER_EXECUTION_LEDGER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _assert_closed_constants()
        if self.schema_version != MASTER_EXECUTION_LEDGER_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if self.execution_authority is not MasterRouteExecutionAuthority.DENIED:
            raise ContractValidationError("execution_authority_not_granted")
        if self.production_authority is not MasterProductionAuthority.DENIED:
            raise ContractValidationError("production_authority_not_issued")
        if self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            raise ContractValidationError("source_authenticity_not_established")
        for flag in (
            self.specialist_execution_permitted,
            self.specialist_executed,
            self.dispatch_permitted,
            self.grant_nonce_is_production_grant,
            self.production_cross_instance_authority,
        ):
            if flag is not False:
                raise ContractValidationError("execution_not_granted")
        if type(self.fresh_transition) is not bool or type(self.replayed) is not bool:
            raise ContractValidationError("invalid_ledger_result")
        if type(self.store_is_durable_adapter) is not bool:
            raise ContractValidationError("invalid_ledger_result")
        if not isinstance(self.outcome, MasterExecutionLedgerOutcome):
            raise ContractValidationError("invalid_ledger_result")
        if self.outcome is MasterExecutionLedgerOutcome.CLAIMED:
            if self.fresh_transition is not True or self.replayed is not False or self.rejection is not None:
                raise ContractValidationError("invalid_ledger_result")
            self._require_attempt()
        elif self.outcome is MasterExecutionLedgerOutcome.REPLAYED:
            if self.fresh_transition is not False or self.replayed is not True or self.rejection is not None:
                raise ContractValidationError("invalid_ledger_result")
            self._require_attempt()
        elif self.outcome is MasterExecutionLedgerOutcome.REJECTED:
            if (
                self.fresh_transition is not False
                or self.replayed is not False
                or not isinstance(self.rejection, MasterExecutionLedgerRejection)
                or self.attempt_token is not None
                or self.grant_nonce is not None
                or self.fence_epoch is not None
                or self.owner_id is not None
                or self.state is not None
            ):
                raise ContractValidationError("invalid_ledger_result")
        else:
            raise ContractValidationError("invalid_ledger_result")

    def _require_attempt(self) -> None:
        if not isinstance(self.state, MasterExecutionLedgerState):
            raise ContractValidationError("invalid_ledger_result")
        _token(self.attempt_token, "attempt_token")
        _token(self.grant_nonce, "grant_nonce")
        _token(self.owner_id, "owner_id")
        _token(self.correlation_id, "correlation_id")
        _whole_second(self.lease_expires_at_utc, "lease_expires_at_utc")
        _whole_second(self.accepted_at_utc, "accepted_at_utc")
        _whole_second(self.deadline_utc, "deadline_utc")
        _whole_second(self.ledger_observed_at_utc, "ledger_observed_at_utc")
        _fence_epoch(self.fence_epoch)


@dataclass(frozen=True)
class MasterExecutionLedgerRetention:
    """Retention view. Elapsed retention does not delete or reopen a claim."""

    retention_expires_at_utc: str
    retention_elapsed: bool
    record_present: bool
    ttl_deleted: bool
    absence_is_repeat_permission: bool = False
    ttl_deletion_is_correctness: bool = False
    specialist_execution_permitted: bool = False
    production_cross_instance_authority: bool = False

    def __post_init__(self) -> None:
        _assert_closed_constants()
        _whole_second(self.retention_expires_at_utc, "retention_expires_at_utc")
        if type(self.retention_elapsed) is not bool or type(self.record_present) is not bool:
            raise ContractValidationError("invalid_retention")
        if type(self.ttl_deleted) is not bool:
            raise ContractValidationError("invalid_retention")
        if self.record_present and self.ttl_deleted:
            raise ContractValidationError("invalid_retention")
        for flag in (
            self.absence_is_repeat_permission,
            self.ttl_deletion_is_correctness,
            self.specialist_execution_permitted,
            self.production_cross_instance_authority,
        ):
            if flag is not False:
                raise ContractValidationError("execution_not_granted")


def _lease_deadline(epoch_seconds: int) -> str:
    return _format_utc(datetime.fromtimestamp(epoch_seconds, tz=timezone.utc))


class MasterExecutionLedger:
    """Conditional claim port. Does not call a specialist or a model.

    ``fresh_transition`` on ``mark_started`` is the single-use bookkeeping
    edge a future dispatcher can count. This class still reports execution
    as denied. Identical inputs replay. Conflicting inputs are rejected and
    do not change the stored attempt.
    """

    def __init__(
        self,
        store: object,
        *,
        durable_required: bool = False,
        token_source: Callable[[], str] | None = None,
    ) -> None:
        _assert_closed_constants()
        if type(durable_required) is not bool:
            raise ContractValidationError("invalid_durable_required")
        if (
            not callable(getattr(store, "read", None))
            or not callable(getattr(store, "put_if_absent", None))
            or not callable(getattr(store, "replace_if", None))
        ):
            raise ContractValidationError("invalid_ledger_store")
        if durable_required and type(store) is not DynamoDbMasterExecutionLedgerStore:
            raise ContractValidationError("in_memory_not_cross_instance_authority")
        if token_source is None:
            self._token_source: Callable[[], str] = lambda: secrets.token_urlsafe(32)
        elif callable(token_source):
            self._token_source = token_source
        else:
            raise ContractValidationError("invalid_token_source")
        self._store = store
        self._durable = type(store) is DynamoDbMasterExecutionLedgerStore

    @classmethod
    def for_durable(
        cls,
        store: object,
        *,
        token_source: Callable[[], str] | None = None,
    ) -> "MasterExecutionLedger":
        """Fail closed unless ``store`` is the exact DynamoDB adapter."""

        return cls(store, durable_required=True, token_source=token_source)

    @property
    def store_is_durable_adapter(self) -> bool:
        return self._durable

    @property
    def production_cross_instance_authority(self) -> bool:
        return False

    def reserve_once(
        self,
        envelope: object,
        *,
        owner_id: str,
        now_utc: str,
        lease_seconds: int,
    ) -> MasterExecutionLedgerResult:
        checked, owner, now, lease = self._reserve_args(envelope, owner_id, now_utc, lease_seconds)
        deadline = _whole_second(checked.deadline_utc, "deadline_utc")
        view = self._read(checked)
        if view.ttl_deleted:
            return self._rejected(MasterExecutionLedgerRejection.RETENTION_GAP)
        if view.record is None:
            if now >= deadline:
                return self._rejected(MasterExecutionLedgerRejection.DEADLINE_EXPIRED)
            self._require_lease_fits(now, lease, deadline)
            record = self._fresh_record(checked, owner, now, lease)
            inserted = self._put_if_absent(record)
            if inserted is True:
                return self._from_record(record, claimed=True)
            if inserted is not False:
                raise LedgerStoreUnavailable("durable_storage_unavailable")
            view = self._read(checked)
            if view.ttl_deleted:
                return self._rejected(MasterExecutionLedgerRejection.RETENTION_GAP)
            if view.record is None:
                raise LedgerStoreUnavailable("durable_storage_unavailable")
        return self._classify_reserve(view.record, checked, owner, now, lease, allow_takeover=True)

    def mark_started(
        self,
        envelope: object,
        *,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        now_utc: str,
    ) -> MasterExecutionLedgerResult:
        checked, owner, token, fence, now = self._transition_args(
            envelope, owner_id, attempt_token, fence_epoch, now_utc
        )
        view = self._read(checked)
        if view.ttl_deleted:
            return self._rejected(MasterExecutionLedgerRejection.RETENTION_GAP)
        if view.record is None:
            return self._rejected(MasterExecutionLedgerRejection.FENCED_OUT)
        mismatch = self._mismatch(view.record, checked, owner, token, fence)
        if mismatch is not None:
            return self._rejected(mismatch)
        if view.record.state is MasterExecutionLedgerState.STARTED:
            return self._from_record(view.record, claimed=False)
        if now >= _whole_second(checked.deadline_utc, "deadline_utc"):
            return self._rejected(MasterExecutionLedgerRejection.DEADLINE_EXPIRED)
        if view.record.state is not MasterExecutionLedgerState.RESERVED:
            return self._rejected(MasterExecutionLedgerRejection.INVALID_TRANSITION)
        if _epoch(now) >= view.record.lease_expires_at_epoch:
            return self._rejected(MasterExecutionLedgerRejection.LEASE_EXPIRED)
        updated = replace(
            view.record,
            state=MasterExecutionLedgerState.STARTED,
            started_at_utc=_format_utc(now),
            non_idempotent_started=True,
            ledger_observed_at_utc=_format_utc(now),
        )
        condition = self._condition(view.record, _epoch(now), "unexpired", False)
        return self._apply(updated, condition, checked, owner, token, fence, MasterExecutionLedgerState.STARTED)

    def complete(
        self,
        envelope: object,
        *,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        now_utc: str,
    ) -> MasterExecutionLedgerResult:
        return self._finish(
            envelope,
            owner_id=owner_id,
            attempt_token=attempt_token,
            fence_epoch=fence_epoch,
            now_utc=now_utc,
            target=MasterExecutionLedgerState.COMPLETED,
            reason=None,
        )

    def fail(
        self,
        envelope: object,
        *,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        now_utc: str,
        reason: str,
    ) -> MasterExecutionLedgerResult:
        failure = _token(reason, "reason")
        return self._finish(
            envelope,
            owner_id=owner_id,
            attempt_token=attempt_token,
            fence_epoch=fence_epoch,
            now_utc=now_utc,
            target=MasterExecutionLedgerState.FAILED,
            reason=failure,
        )

    def cancel(
        self,
        envelope: object,
        *,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        now_utc: str,
    ) -> MasterExecutionLedgerResult:
        checked, owner, token, fence, now = self._transition_args(
            envelope, owner_id, attempt_token, fence_epoch, now_utc
        )
        view = self._read(checked)
        if view.ttl_deleted:
            return self._rejected(MasterExecutionLedgerRejection.RETENTION_GAP)
        if view.record is None:
            return self._rejected(MasterExecutionLedgerRejection.FENCED_OUT)
        mismatch = self._mismatch(view.record, checked, owner, token, fence)
        if mismatch is not None:
            return self._rejected(mismatch)
        if view.record.state is MasterExecutionLedgerState.CANCELLED:
            return self._from_record(view.record, claimed=False)
        if view.record.state is not MasterExecutionLedgerState.RESERVED:
            return self._rejected(MasterExecutionLedgerRejection.INVALID_TRANSITION)
        updated = replace(
            view.record,
            state=MasterExecutionLedgerState.CANCELLED,
            terminal_at_utc=_format_utc(now),
            ledger_observed_at_utc=_format_utc(now),
        )
        condition = self._condition(view.record, _epoch(now), "any", False)
        return self._apply(updated, condition, checked, owner, token, fence, MasterExecutionLedgerState.CANCELLED)

    def retention_observation(self, envelope: object, *, now_utc: str) -> MasterExecutionLedgerRetention:
        checked = self._envelope(envelope)
        now = _whole_second(now_utc, "now_utc")
        expected = _retention_epoch(checked.deadline_utc)
        view = self._read(checked)
        if view.record is not None and view.record.retention_expires_at_epoch != expected:
            raise LedgerStoreUnavailable("durable_storage_unavailable")
        return MasterExecutionLedgerRetention(
            retention_expires_at_utc=_lease_deadline(expected),
            retention_elapsed=_epoch(now) >= expected,
            record_present=view.record is not None,
            ttl_deleted=view.ttl_deleted,
        )

    def _finish(
        self,
        envelope: object,
        *,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        now_utc: str,
        target: MasterExecutionLedgerState,
        reason: str | None,
    ) -> MasterExecutionLedgerResult:
        checked, owner, token, fence, now = self._transition_args(
            envelope, owner_id, attempt_token, fence_epoch, now_utc
        )
        view = self._read(checked)
        if view.ttl_deleted:
            return self._rejected(MasterExecutionLedgerRejection.RETENTION_GAP)
        if view.record is None:
            return self._rejected(MasterExecutionLedgerRejection.FENCED_OUT)
        mismatch = self._mismatch(view.record, checked, owner, token, fence)
        if mismatch is not None:
            return self._rejected(mismatch)
        if view.record.state is target:
            if target is MasterExecutionLedgerState.FAILED and view.record.failure_reason != reason:
                return self._rejected(MasterExecutionLedgerRejection.CONFLICTING_DUPLICATE)
            return self._from_record(view.record, claimed=False)
        if view.record.state is not MasterExecutionLedgerState.STARTED:
            return self._rejected(MasterExecutionLedgerRejection.INVALID_TRANSITION)
        updated = replace(
            view.record,
            state=target,
            terminal_at_utc=_format_utc(now),
            ledger_observed_at_utc=_format_utc(now),
            failure_reason=reason,
        )
        condition = self._condition(view.record, _epoch(now), "any", False)
        return self._apply(updated, condition, checked, owner, token, fence, target)

    def _classify_reserve(
        self,
        record: _LedgerRecord,
        envelope: MasterExecutionLedgerEnvelope,
        owner: str,
        now: datetime,
        lease: int,
        *,
        allow_takeover: bool,
    ) -> MasterExecutionLedgerResult:
        if record.payload_fingerprint != envelope.fingerprint:
            return self._rejected(MasterExecutionLedgerRejection.CONFLICTING_DUPLICATE)
        now_epoch = _epoch(now)
        expired = now_epoch >= record.lease_expires_at_epoch
        same_owner = record.owner_id == owner
        deadline = _whole_second(envelope.deadline_utc, "deadline_utc")
        if record.state.value in _TERMINAL:
            if same_owner:
                return self._from_record(record, claimed=False)
            return self._rejected(MasterExecutionLedgerRejection.ALREADY_SETTLED)
        if record.state is MasterExecutionLedgerState.STARTED:
            if expired and record.repeat_non_idempotent_after_expiry is True and now < deadline and allow_takeover:
                return self._takeover(record, envelope, owner, now, lease)
            if expired:
                return self._rejected(MasterExecutionLedgerRejection.NON_IDEMPOTENT_REPEAT_PROHIBITED)
            if same_owner:
                return self._from_record(record, claimed=False)
            return self._rejected(MasterExecutionLedgerRejection.CLAIM_HELD)
        if record.state is MasterExecutionLedgerState.RESERVED:
            if not expired:
                if same_owner:
                    return self._from_record(record, claimed=False)
                return self._rejected(MasterExecutionLedgerRejection.CLAIM_HELD)
            if now >= deadline or not allow_takeover:
                if now >= deadline:
                    return self._rejected(MasterExecutionLedgerRejection.DEADLINE_EXPIRED)
                return self._rejected(MasterExecutionLedgerRejection.CLAIM_HELD)
            return self._takeover(record, envelope, owner, now, lease)
        raise LedgerStoreUnavailable("durable_storage_unavailable")

    def _takeover(
        self,
        record: _LedgerRecord,
        envelope: MasterExecutionLedgerEnvelope,
        owner: str,
        now: datetime,
        lease: int,
    ) -> MasterExecutionLedgerResult:
        deadline = _whole_second(envelope.deadline_utc, "deadline_utc")
        self._require_lease_fits(now, lease, deadline)
        require_repeat = record.state is MasterExecutionLedgerState.STARTED
        condition = self._condition(record, _epoch(now), "expired", require_repeat)
        attempt = self._new_secret({record.attempt_token, record.grant_nonce})
        expiry = now + timedelta(seconds=lease)
        updated = replace(
            record,
            state=MasterExecutionLedgerState.RESERVED,
            fence_epoch=record.fence_epoch + 1,
            attempt_token=attempt,
            owner_id=owner,
            lease_expires_at_epoch=_epoch(expiry),
            non_idempotent_started=False,
            reserved_at_utc=_format_utc(now),
            ledger_observed_at_utc=_format_utc(now),
            started_at_utc=None,
            terminal_at_utc=None,
            failure_reason=None,
        )
        applied = self._replace(updated, condition)
        if applied is True:
            return self._from_record(updated, claimed=True)
        if applied is not False:
            raise LedgerStoreUnavailable("durable_storage_unavailable")
        view = self._read(envelope)
        if view.ttl_deleted:
            return self._rejected(MasterExecutionLedgerRejection.RETENTION_GAP)
        if view.record is None:
            raise LedgerStoreUnavailable("durable_storage_unavailable")
        return self._classify_reserve(view.record, envelope, owner, now, lease, allow_takeover=False)

    def _apply(
        self,
        updated: _LedgerRecord,
        condition: _LedgerCondition,
        envelope: MasterExecutionLedgerEnvelope,
        owner: str,
        token: str,
        fence: int,
        target: MasterExecutionLedgerState,
    ) -> MasterExecutionLedgerResult:
        applied = self._replace(updated, condition)
        if applied is True:
            return self._from_record(updated, claimed=True)
        if applied is not False:
            raise LedgerStoreUnavailable("durable_storage_unavailable")
        view = self._read(envelope)
        if view.ttl_deleted:
            return self._rejected(MasterExecutionLedgerRejection.RETENTION_GAP)
        if view.record is None:
            raise LedgerStoreUnavailable("durable_storage_unavailable")
        mismatch = self._mismatch(view.record, envelope, owner, token, fence)
        if mismatch is not None:
            return self._rejected(mismatch)
        if view.record.state is target:
            return self._from_record(view.record, claimed=False)
        return self._rejected(MasterExecutionLedgerRejection.INVALID_TRANSITION)

    def _fresh_record(
        self,
        envelope: MasterExecutionLedgerEnvelope,
        owner: str,
        now: datetime,
        lease: int,
    ) -> _LedgerRecord:
        nonce = self._new_secret(set())
        attempt = self._new_secret({nonce})
        observed = _format_utc(now)
        return _LedgerRecord(
            pk=envelope.partition_key,
            sk=envelope.sort_key,
            master_request_id=envelope.master_request_id,
            planning_session_id=envelope.planning_session_id,
            step_id=envelope.step_id,
            invocation_id=envelope.invocation_id,
            specialist=envelope.specialist,
            purpose=envelope.purpose,
            capability=envelope.capability,
            principal_ref=envelope.principal_ref,
            policy_revision=envelope.policy_revision,
            resource_scope=envelope.resource_scope,
            region_scope=envelope.region_scope,
            decision_target_scope=envelope.decision_target_scope,
            accepted_at_utc=envelope.accepted_at_utc,
            deadline_utc=envelope.deadline_utc,
            idempotency_key=envelope.idempotency_key,
            max_model_turns=envelope.max_model_turns,
            max_tool_calls=envelope.max_tool_calls,
            correlation_id=envelope.correlation_id,
            historical_start_utc=envelope.historical_start_utc,
            historical_end_utc=envelope.historical_end_utc,
            repeat_non_idempotent_after_expiry=envelope.repeat_non_idempotent_after_expiry,
            payload_fingerprint=envelope.fingerprint,
            state=MasterExecutionLedgerState.RESERVED,
            fence_epoch=1,
            attempt_token=attempt,
            grant_nonce=nonce,
            owner_id=owner,
            lease_expires_at_epoch=_epoch(now + timedelta(seconds=lease)),
            retention_expires_at_epoch=_retention_epoch(envelope.deadline_utc),
            non_idempotent_started=False,
            reserved_at_utc=observed,
            ledger_observed_at_utc=observed,
        )

    def _require_lease_fits(self, now: datetime, lease: int, deadline: datetime) -> None:
        if now + timedelta(seconds=lease) > deadline:
            raise ContractValidationError("lease_exceeds_deadline")

    def _new_secret(self, forbidden: set[str]) -> str:
        for _ in range(_GENERATED_TOKEN_ATTEMPTS):
            value = self._token_source()
            if type(value) is not str:
                continue
            try:
                checked = _token(value, "generated_token")
            except ContractValidationError:
                continue
            if len(checked) < _MIN_GENERATED_TOKEN_LENGTH or checked in forbidden:
                continue
            return checked
        raise ContractValidationError("attempt_token_source_exhausted")

    def _condition(
        self,
        record: _LedgerRecord,
        now_epoch: int,
        lease_relation: str,
        require_repeat_allowed: bool,
    ) -> _LedgerCondition:
        return _LedgerCondition(
            state=record.state,
            fence_epoch=record.fence_epoch,
            attempt_token=record.attempt_token,
            owner_id=record.owner_id,
            payload_fingerprint=record.payload_fingerprint,
            grant_nonce=record.grant_nonce,
            lease_relation=lease_relation,
            now_epoch=now_epoch,
            require_repeat_allowed=require_repeat_allowed,
        )

    def _mismatch(
        self,
        record: _LedgerRecord,
        envelope: MasterExecutionLedgerEnvelope,
        owner: str,
        token: str,
        fence: int,
    ) -> MasterExecutionLedgerRejection | None:
        if record.payload_fingerprint != envelope.fingerprint:
            return MasterExecutionLedgerRejection.CONFLICTING_DUPLICATE
        if record.attempt_token != token or record.fence_epoch != fence:
            return MasterExecutionLedgerRejection.FENCED_OUT
        if record.owner_id != owner:
            return MasterExecutionLedgerRejection.OWNER_MISMATCH
        return None

    def _from_record(self, record: _LedgerRecord, *, claimed: bool) -> MasterExecutionLedgerResult:
        return MasterExecutionLedgerResult(
            outcome=(
                MasterExecutionLedgerOutcome.CLAIMED
                if claimed
                else MasterExecutionLedgerOutcome.REPLAYED
            ),
            fresh_transition=claimed,
            replayed=not claimed,
            state=record.state,
            attempt_token=record.attempt_token,
            fence_epoch=record.fence_epoch,
            grant_nonce=record.grant_nonce,
            owner_id=record.owner_id,
            correlation_id=record.correlation_id,
            lease_expires_at_utc=_lease_deadline(record.lease_expires_at_epoch),
            accepted_at_utc=record.accepted_at_utc,
            deadline_utc=record.deadline_utc,
            ledger_observed_at_utc=record.ledger_observed_at_utc,
            store_is_durable_adapter=self._durable,
        )

    def _rejected(self, rejection: MasterExecutionLedgerRejection) -> MasterExecutionLedgerResult:
        return MasterExecutionLedgerResult(
            outcome=MasterExecutionLedgerOutcome.REJECTED,
            fresh_transition=False,
            replayed=False,
            rejection=rejection,
            store_is_durable_adapter=self._durable,
        )

    def _envelope(self, envelope: object) -> MasterExecutionLedgerEnvelope:
        if type(envelope) is not MasterExecutionLedgerEnvelope:
            raise ContractValidationError("invalid_ledger_envelope")
        return envelope

    def _reserve_args(
        self,
        envelope: object,
        owner_id: object,
        now_utc: object,
        lease_seconds: object,
    ) -> tuple[MasterExecutionLedgerEnvelope, str, datetime, int]:
        return (
            self._envelope(envelope),
            _token(owner_id, "owner_id"),
            _whole_second(now_utc, "now_utc"),
            _lease_seconds(lease_seconds),
        )

    def _transition_args(
        self,
        envelope: object,
        owner_id: object,
        attempt_token: object,
        fence_epoch: object,
        now_utc: object,
    ) -> tuple[MasterExecutionLedgerEnvelope, str, str, int, datetime]:
        return (
            self._envelope(envelope),
            _token(owner_id, "owner_id"),
            _token(attempt_token, "attempt_token"),
            _fence_epoch(fence_epoch),
            _whole_second(now_utc, "now_utc"),
        )

    def _read(self, envelope: MasterExecutionLedgerEnvelope) -> MasterExecutionLedgerRead:
        view = self._store.read(envelope.partition_key, envelope.sort_key)  # type: ignore[attr-defined]
        if type(view) is not MasterExecutionLedgerRead:
            raise LedgerStoreUnavailable("durable_storage_unavailable")
        return view

    def _put_if_absent(self, record: _LedgerRecord) -> object:
        return self._store.put_if_absent(record)  # type: ignore[attr-defined]

    def _replace(self, record: _LedgerRecord, condition: _LedgerCondition) -> object:
        return self._store.replace_if(record, condition)  # type: ignore[attr-defined]


def ledger_item_key(envelope: MasterExecutionLedgerEnvelope) -> tuple[str, str]:
    """Return the documented DynamoDB primary key for one envelope."""

    checked = envelope if type(envelope) is MasterExecutionLedgerEnvelope else None
    if checked is None:
        raise ContractValidationError("invalid_ledger_envelope")
    return checked.partition_key, checked.sort_key


__all__ = [
    "CONDITIONAL_PUT_ABSENT",
    "CONDITIONAL_REPLACE_EXACT",
    "CONDITIONAL_REPLACE_EXPIRED",
    "CONDITIONAL_REPLACE_EXPIRED_REPEATABLE",
    "CONDITIONAL_REPLACE_UNEXPIRED",
    "DYNAMODB_LEDGER_SCHEMA",
    "DYNAMODB_LEDGER_TABLE_NAME",
    "GRANT_NONCE_IS_PRODUCTION_GRANT",
    "IN_MEMORY_IS_PRODUCTION_CROSS_INSTANCE_AUTHORITY",
    "INTEGRATION_DEPENDENCIES",
    "LEDGER_WRITES_ARE_SPECIALIST_READ_PERMISSIONS",
    "MAX_DECISION_STEPS",
    "MAX_HISTORICAL_WINDOW",
    "MAX_LEASE_SECONDS",
    "MAX_ROUTE_STEPS",
    "MASTER_EXECUTION_LEDGER_SCHEMA_VERSION",
    "PRINCIPAL_REF_IS_AUTHENTICATED_BY_THIS_MODULE",
    "PROCESS_LOCAL_LOCK_IS_DISTRIBUTED_ATOMICITY",
    "REGION_SCOPE_IS_GEOGRAPHIC_VERIFICATION",
    "RETENTION_AFTER_DEADLINE",
    "TTL_DELETION_IS_CORRECTNESS",
    "DynamoDbMasterExecutionLedgerStore",
    "InMemoryMasterExecutionLedgerBackend",
    "InMemoryMasterExecutionLedgerStore",
    "LedgerStoreUnavailable",
    "MasterExecutionLedger",
    "MasterExecutionLedgerEnvelope",
    "MasterExecutionLedgerOutcome",
    "MasterExecutionLedgerRead",
    "MasterExecutionLedgerRejection",
    "MasterExecutionLedgerResult",
    "MasterExecutionLedgerRetention",
    "MasterExecutionLedgerState",
    "conditional_replace_expression",
    "ledger_item_key",
]
