"""Host-owned execution-grant lifecycle. Not a production permit.

Distinctions, in order:

1. Descriptive. ``MasterExecutionGrantEnvelope`` carries the shared request
   envelope. Those values are hints until a host seals them. The envelope is
   not a grant and is never true in boolean context.
2. Evaluated. ``evaluate_grant_envelope`` checks specialist, capability,
   server read ceilings, the exact logical resource set, region, temporal
   scope, and the historical window. ``bounds_accepted`` can be true.
   Evaluation does not issue a grant. A policy revision string is not an
   Agent 3 decision. ``MasterReadResourceCheck`` ``WITHIN_ALLOWLIST``,
   ``MasterExecutionClaim``, ``assess_master_execution_policy``, a caller
   token, and a serialized body are refused by
   ``refuse_production_grant_issuance``.
3. Host-issued, local harness only. ``LocalHarnessGrantIssuer`` signs a
   canonical binding with a process-local HMAC key. The public constructor
   of the grant is refused. Callers cannot forge an authorized grant.
4. Consumed, local harness only. Consumption requires the issuing host and
   ``LocalHarnessConsumptionLedger.reserve_once``. The result records one
   in-process transition. It does not dispatch a specialist.
5. Source-authenticated. This module has no such state. Source authenticity
   stays the admission value ``NOT_ESTABLISHED``. A grant is not aviation
   evidence and cannot represent a verified-zero result.

Production issuance stays closed. ``issue_production_execution_grant``
refuses every argument until a reviewed host has actually integrated an
application principal, Agent 3's policy decision, and Agent 5's atomic
ledger. Those flags are constants. No constructor, environment variable, or
caller object sets them.

Key custody. The local issuer generates 32-byte keys with
``secrets.token_bytes`` and keeps them on the issuer. Key bytes are not
placed on the grant, in ``serialized_claim``, or in the environment. The
issuer cannot be pickled. Rotation creates a new signing key and leaves the
previous key available for verification until ``retire_verification_key``.
Retiring a key rejects later verification of grants signed by it. Rotation
and retirement are not consumption and are not revocation of a nonce that a
verifier with the old key has already accepted.

HMAC covers the canonical binding only. It does not record that a nonce was
used. A valid signature after consumption is a replay proposal.
``HMAC_PREVENTS_REPLAY`` is false. Single-use and revocation belong to
Agent 5's atomic ledger (``reserve_once``, ``mark_started``, ``complete``,
``fail``, ``cancel``) shared across Lambda instances. The process-local
latch and ``LocalHarnessConsumptionLedger`` die with this process. They are
the offline test double, not that ledger. ``consume`` accepts only that
local double. Passing ``MasterExecutionLedger`` is ``LEDGER_NOT_INTEGRATED``
and does not mean the durable item was consumed.
``DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE`` stays false.
``bind_production_grant_to_master_ledger`` always fails closed. A missing or
substitute ledger is rejected before any consumption transition.

A directly invoked Lambda can receive a forged API Gateway event. JWT
validation at API Gateway is an edge control, not attestation on the event.
This module does not read an Authorization header, a requestContext, or
the process environment. Who may invoke the issuer Lambda has to be restricted by
the reviewed application host and AWS identity policy before any production
principal exists. That restriction is not implemented here.

The Master is not authorized to issue ATC instructions or data-plane writes.
Execution authority stays ``DENIED``. Production authority stays ``DENIED``.
Request coverage stays ``NOT_ESTABLISHED``.

Integration, all ``NOT_INTEGRATED``:

- Agent 2, ``wilvor_ai.master_application_principal``. Seal the application
  principal. A reference string, a copied principal, and a gateway
  assessment are not that seal.
- Agent 3, ``wilvor_ai.master_runtime_policy``. Supply the reviewed
  deterministic decision for the retained invocation. This module must not
  treat ``policy_revision`` or a bounds-accepted evaluation as that decision.
- Agent 5, ``wilvor_ai.master_execution_ledger``. Atomically reserve the
  grant nonce and idempotency key once across processes. HMAC does not.
- Agent 6, ``wilvor_ai.master_runtime_budget``. Own request acceptance, the
  absolute deadline, monotonic execution time, and cancellation at tool
  execution. This evaluation clock only rejects an expired grant before
  consumption. It is not the source-observation clock.
- Agent 7, ``wilvor_ai.master_authorized_scope``. Intersect principal
  entitlement, population, region, temporal scope, and Decision target.
  Labels bound here are harness seals, not that intersection.
- Agent 9, ``wilvor_ai.master_authorized_scheduler_adapter``. Present the
  retained plan step and original invocation, then verify and consume before
  dispatch. A fanout claim is scheduling, not execution. This module has no
  dispatch method.

``SOURCE_AUTHENTICITY_NOT_ESTABLISHED`` maps to
``wilvor_ai.master_evidence_admission.MasterSourceAuthenticity.NOT_ESTABLISHED``.
Admission is not imported. This module must not admit evidence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Mapping

from wilvor_ai.contracts import (
    ALLOWED_V1_CAPABILITIES,
    PROHIBITED_V1_CAPABILITIES,
    PROVENANCE_TOKEN_MAX_LENGTH,
    AgentCapability,
    ContractValidationError,
    TemporalScope,
    _validate_bounded_text,
)
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterSpecialist,
)
from wilvor_ai.master_read_resource_guard import (
    MasterReadCheckStatus,
    MasterReadResourceCheck,
    ReadOperation,
    server_read_argument_names,
    server_read_budget,
    server_read_resources,
    server_read_tools,
)
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS,
    MAX_HISTORICAL_WINDOW,
    MAX_ROUTE_STEPS,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
)


MASTER_EXECUTION_GRANT_SCHEMA_VERSION = "wilvor.ai.master_execution_grant.v1"

# Closed until a reviewed host integrates principal, policy, and ledger.
PRODUCTION_PRINCIPAL_HOST_INTEGRATED = False
PRODUCTION_POLICY_HOST_INTEGRATED = False
PRODUCTION_CONSUMPTION_LEDGER_INTEGRATED = False
PRODUCTION_BUDGET_HOST_INTEGRATED = False
PRODUCTION_SCOPE_HOST_INTEGRATED = False
PRODUCTION_SCHEDULER_HOST_INTEGRATED = False
PRODUCTION_SIGNING_KEY_STORE_INTEGRATED = False
PRODUCTION_GRANT_ISSUANCE_AVAILABLE = False
DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE = False
HMAC_PREVENTS_REPLAY = False
LOCAL_HARNESS_IS_PRODUCTION_AUTHORIZATION = False

# Mapping: master_evidence_admission.MasterSourceAuthenticity.NOT_ESTABLISHED.
SOURCE_AUTHENTICITY_NOT_ESTABLISHED = "NOT_ESTABLISHED"
_LOCAL_HARNESS_DOMAIN = "LOCAL_HARNESS"
_ISSUE_PERMIT = object()

_LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
_HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
_RISK = AgentCapability.EXPLAIN_EXISTING_RISK
_RECOMMENDATIONS = AgentCapability.EXPLAIN_EXISTING_RECOMMENDATIONS

_PAIR_CAPABILITIES = {
    (MasterSpecialist.LIVE_OPS, MasterPurpose.DISCOVERY): frozenset({_LIVE}),
    (MasterSpecialist.LIVE_OPS, MasterPurpose.ANSWER): frozenset({_LIVE}),
    (MasterSpecialist.HISTORICAL_ANALYTICS, MasterPurpose.ANSWER): frozenset({_HISTORY}),
    (MasterSpecialist.DECISION, MasterPurpose.ANSWER): frozenset({_RISK, _RECOMMENDATIONS}),
}

_TEMPORAL = {
    MasterSpecialist.LIVE_OPS: frozenset({(TemporalScope.CURRENT,)}),
    MasterSpecialist.HISTORICAL_ANALYTICS: frozenset({(TemporalScope.HISTORICAL,)}),
    MasterSpecialist.DECISION: frozenset(
        {
            (TemporalScope.CURRENT,),
            (TemporalScope.PERSISTED,),
            (TemporalScope.CURRENT, TemporalScope.PERSISTED),
        }
    ),
}

_US_STATE_CODES = frozenset(
    {
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
        "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
        "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
        "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
        "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    }
)

_ATC_CAPABILITIES = frozenset(
    {
        AgentCapability.ISSUE_ATC_INSTRUCTIONS,
        AgentCapability.ISSUE_LANDING_INSTRUCTIONS,
        AgentCapability.ISSUE_ALTITUDE_INSTRUCTIONS,
        AgentCapability.ISSUE_AUTONOMOUS_FLIGHT_CONTROL_INSTRUCTIONS,
        AgentCapability.EXECUTE_REROUTES,
    }
)
_WRITE_CAPABILITIES = PROHIBITED_V1_CAPABILITIES - _ATC_CAPABILITIES
_WRITE_OR_ATC_TOOLS = frozenset(
    {
        "scan_all",
        "put_item",
        "delete_item",
        "update_item",
        "batch_write_item",
        "mutate_operational_state",
        "execute_reroute",
        "issue_atc_instructions",
    }
)
_READ_OPERATIONS = frozenset({ReadOperation.GET_ITEM, ReadOperation.QUERY})

_INSUFFICIENT_TYPES = {
    ("wilvor_ai.master_execution_fanout", "MasterExecutionClaim"): "EXECUTION_CLAIM_NOT_A_GRANT",
    ("wilvor_ai.master_execution_policy_boundary", "MasterExecutionPolicyAssessment"): (
        "POLICY_EVALUATION_NOT_A_GRANT"
    ),
    ("wilvor_ai.master_execution_policy_boundary", "MasterExecutionPolicyCandidate"): (
        "POLICY_EVALUATION_NOT_A_GRANT"
    ),
    ("wilvor_ai.master_execution_scope", "MasterExecutionScope"): "SCOPE_NOT_A_GRANT",
    ("wilvor_ai.master_local_capability", "LocalCapabilityHandle"): "LOCAL_CAPABILITY_NOT_A_GRANT",
    ("wilvor_ai.master_local_capability", "LocalConsumptionResult"): "LOCAL_CAPABILITY_NOT_A_GRANT",
}

_CLAIM_FIELDS = (
    "schema_version",
    "authority_domain",
    "master_request_id",
    "planning_session_id",
    "step_id",
    "invocation_id",
    "specialist",
    "purpose",
    "capability",
    "principal_ref",
    "policy_revision",
    "tool_name",
    "resource_scope",
    "argument_names",
    "region_scope",
    "temporal_scopes",
    "historical_window",
    "decision_target_scope",
    "accepted_at_utc",
    "deadline_utc",
    "expires_at_utc",
    "idempotency_key",
    "nonce",
    "max_model_turns",
    "max_tool_calls",
    "correlation_id",
    "key_id",
    "signature",
)

_SIGNED_FIELDS = tuple(field for field in _CLAIM_FIELDS if field != "signature")


class GrantLifecycle(str, Enum):
    """How far a local harness record has gone. Production is not a member."""

    DESCRIPTIVE = "DESCRIPTIVE"
    EVALUATED = "EVALUATED"
    HOST_ISSUED_LOCAL_HARNESS = "HOST_ISSUED_LOCAL_HARNESS"
    CONSUMED_LOCAL_HARNESS = "CONSUMED_LOCAL_HARNESS"


class GrantRejectionReason(str, Enum):
    """Why a candidate was not issued, not verified, or not consumed."""

    DENIED_BY_DEFAULT = "DENIED_BY_DEFAULT"
    PRODUCTION_ISSUANCE_CLOSED = "PRODUCTION_ISSUANCE_CLOSED"
    DESCRIPTIVE_ENVELOPE_NOT_A_GRANT = "DESCRIPTIVE_ENVELOPE_NOT_A_GRANT"
    EVALUATION_NOT_A_GRANT = "EVALUATION_NOT_A_GRANT"
    READ_CHECK_NOT_A_GRANT = "READ_CHECK_NOT_A_GRANT"
    EXECUTION_CLAIM_NOT_A_GRANT = "EXECUTION_CLAIM_NOT_A_GRANT"
    POLICY_EVALUATION_NOT_A_GRANT = "POLICY_EVALUATION_NOT_A_GRANT"
    SCOPE_NOT_A_GRANT = "SCOPE_NOT_A_GRANT"
    LOCAL_CAPABILITY_NOT_A_GRANT = "LOCAL_CAPABILITY_NOT_A_GRANT"
    LOCAL_HARNESS_GRANT_NOT_PRODUCTION = "LOCAL_HARNESS_GRANT_NOT_PRODUCTION"
    GATEWAY_IDENTITY_NOT_A_PRINCIPAL = "GATEWAY_IDENTITY_NOT_A_PRINCIPAL"
    PLANNING_SESSION_NOT_A_GRANT = "PLANNING_SESSION_NOT_A_GRANT"
    CALLER_TOKEN_NOT_A_GRANT = "CALLER_TOKEN_NOT_A_GRANT"
    CALLER_EVENT_NOT_A_GRANT = "CALLER_EVENT_NOT_A_GRANT"
    SERIALIZED_CLAIM_NOT_AUTHORITY = "SERIALIZED_CLAIM_NOT_AUTHORITY"
    PROHIBITED_CAPABILITY = "PROHIBITED_CAPABILITY"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    CAPABILITY_SPECIALIST_MISMATCH = "CAPABILITY_SPECIALIST_MISMATCH"
    ATC_NOT_AUTHORIZED = "ATC_NOT_AUTHORIZED"
    DATA_PLANE_WRITE_NOT_AUTHORIZED = "DATA_PLANE_WRITE_NOT_AUTHORIZED"
    SCAN_NOT_PERMITTED = "SCAN_NOT_PERMITTED"
    TOOL_NOT_ALLOWLISTED = "TOOL_NOT_ALLOWLISTED"
    RESOURCE_SCOPE_NOT_EXACT = "RESOURCE_SCOPE_NOT_EXACT"
    RESOURCE_SCOPE_NOT_ALLOWLISTED = "RESOURCE_SCOPE_NOT_ALLOWLISTED"
    CALLER_ARGUMENT_NAMES_NOT_AUTHORITY = "CALLER_ARGUMENT_NAMES_NOT_AUTHORITY"
    REGION_NOT_AUTHORIZED = "REGION_NOT_AUTHORIZED"
    TEMPORAL_SCOPE_REJECTED = "TEMPORAL_SCOPE_REJECTED"
    HISTORICAL_INTERVAL_EXCEEDS_CEILING = "HISTORICAL_INTERVAL_EXCEEDS_CEILING"
    HISTORICAL_WINDOW_NOT_APPLICABLE = "HISTORICAL_WINDOW_NOT_APPLICABLE"
    HISTORICAL_WINDOW_REQUIRED = "HISTORICAL_WINDOW_REQUIRED"
    DECISION_TARGET_NOT_BOUND = "DECISION_TARGET_NOT_BOUND"
    DECISION_TARGET_NOT_APPLICABLE = "DECISION_TARGET_NOT_APPLICABLE"
    BUDGET_EXCEEDS_SERVER_CEILING = "BUDGET_EXCEEDS_SERVER_CEILING"
    DEADLINE_BOUNDS_INVALID = "DEADLINE_BOUNDS_INVALID"
    FORGED = "FORGED"
    TAMPERED = "TAMPERED"
    COPIED_GRANT = "COPIED_GRANT"
    FOREIGN_ISSUER = "FOREIGN_ISSUER"
    KEY_RETIRED = "KEY_RETIRED"
    PRINCIPAL_MISMATCH = "PRINCIPAL_MISMATCH"
    SESSION_MISMATCH = "SESSION_MISMATCH"
    STEP_MISMATCH = "STEP_MISMATCH"
    INVOCATION_MISMATCH = "INVOCATION_MISMATCH"
    SPECIALIST_MISMATCH = "SPECIALIST_MISMATCH"
    PURPOSE_MISMATCH = "PURPOSE_MISMATCH"
    CAPABILITY_MISMATCH = "CAPABILITY_MISMATCH"
    POLICY_REVISION_MISMATCH = "POLICY_REVISION_MISMATCH"
    TOOL_MISMATCH = "TOOL_MISMATCH"
    RESOURCE_SCOPE_MISMATCH = "RESOURCE_SCOPE_MISMATCH"
    ARGUMENT_MISMATCH = "ARGUMENT_MISMATCH"
    REGION_MISMATCH = "REGION_MISMATCH"
    TEMPORAL_MISMATCH = "TEMPORAL_MISMATCH"
    HISTORICAL_WINDOW_MISMATCH = "HISTORICAL_WINDOW_MISMATCH"
    DECISION_TARGET_MISMATCH = "DECISION_TARGET_MISMATCH"
    MASTER_REQUEST_MISMATCH = "MASTER_REQUEST_MISMATCH"
    CORRELATION_MISMATCH = "CORRELATION_MISMATCH"
    ACCEPTED_AT_MISMATCH = "ACCEPTED_AT_MISMATCH"
    DEADLINE_MISMATCH = "DEADLINE_MISMATCH"
    EXPIRES_MISMATCH = "EXPIRES_MISMATCH"
    IDEMPOTENCY_MISMATCH = "IDEMPOTENCY_MISMATCH"
    BUDGET_MISMATCH = "BUDGET_MISMATCH"
    EXPIRED = "EXPIRED"
    REPLAY = "REPLAY"
    DUPLICATE_CONSUMPTION = "DUPLICATE_CONSUMPTION"
    MISSING_LEDGER = "MISSING_LEDGER"
    LEDGER_NOT_INTEGRATED = "LEDGER_NOT_INTEGRATED"


class GrantEvaluationLimitation(str, Enum):
    """Standing limits. Every evaluation carries the full set."""

    DESCRIPTIVE_ALLOW_IS_NOT_A_GRANT = "DESCRIPTIVE_ALLOW_IS_NOT_A_GRANT"
    POLICY_REVISION_IS_NOT_AGENT3_DECISION = "POLICY_REVISION_IS_NOT_AGENT3_DECISION"
    PRINCIPAL_REFERENCE_IS_NOT_A_HOST_PRINCIPAL = "PRINCIPAL_REFERENCE_IS_NOT_A_HOST_PRINCIPAL"
    DEADLINE_IS_NOT_AGENT6_ENFORCEMENT = "DEADLINE_IS_NOT_AGENT6_ENFORCEMENT"
    RESOURCE_ALLOWLIST_MATCH_IS_NOT_A_GRANT = "RESOURCE_ALLOWLIST_MATCH_IS_NOT_A_GRANT"
    LEDGER_IS_NOT_AGENT5 = "LEDGER_IS_NOT_AGENT5"
    SCOPE_IS_NOT_AGENT7 = "SCOPE_IS_NOT_AGENT7"
    SCHEDULER_IS_NOT_AGENT9 = "SCHEDULER_IS_NOT_AGENT9"
    SOURCE_AUTHENTICITY_NOT_ESTABLISHED = "SOURCE_AUTHENTICITY_NOT_ESTABLISHED"
    HMAC_DOES_NOT_PREVENT_REPLAY = "HMAC_DOES_NOT_PREVENT_REPLAY"
    PRODUCTION_ISSUANCE_CLOSED = "PRODUCTION_ISSUANCE_CLOSED"


_LIMITATIONS = tuple(GrantEvaluationLimitation)

_BINDING_ORDER = (
    ("master_request_id", GrantRejectionReason.MASTER_REQUEST_MISMATCH),
    ("planning_session_id", GrantRejectionReason.SESSION_MISMATCH),
    ("step_id", GrantRejectionReason.STEP_MISMATCH),
    ("invocation_id", GrantRejectionReason.INVOCATION_MISMATCH),
    ("specialist", GrantRejectionReason.SPECIALIST_MISMATCH),
    ("purpose", GrantRejectionReason.PURPOSE_MISMATCH),
    ("capability", GrantRejectionReason.CAPABILITY_MISMATCH),
    ("principal_ref", GrantRejectionReason.PRINCIPAL_MISMATCH),
    ("policy_revision", GrantRejectionReason.POLICY_REVISION_MISMATCH),
    ("tool_name", GrantRejectionReason.TOOL_MISMATCH),
    ("resource_scope", GrantRejectionReason.RESOURCE_SCOPE_MISMATCH),
    ("argument_names", GrantRejectionReason.ARGUMENT_MISMATCH),
    ("region_scope", GrantRejectionReason.REGION_MISMATCH),
    ("temporal_scopes", GrantRejectionReason.TEMPORAL_MISMATCH),
    ("historical_window", GrantRejectionReason.HISTORICAL_WINDOW_MISMATCH),
    ("decision_target_scope", GrantRejectionReason.DECISION_TARGET_MISMATCH),
    ("accepted_at_utc", GrantRejectionReason.ACCEPTED_AT_MISMATCH),
    ("deadline_utc", GrantRejectionReason.DEADLINE_MISMATCH),
    ("expires_at_utc", GrantRejectionReason.EXPIRES_MISMATCH),
    ("idempotency_key", GrantRejectionReason.IDEMPOTENCY_MISMATCH),
    ("max_model_turns", GrantRejectionReason.BUDGET_MISMATCH),
    ("max_tool_calls", GrantRejectionReason.BUDGET_MISMATCH),
    ("correlation_id", GrantRejectionReason.CORRELATION_MISMATCH),
)


def _assert_closed_constants() -> None:
    if MAX_ROUTE_STEPS != 4 or MAX_DECISION_STEPS != 1:
        raise ContractValidationError("route_ceiling_changed")
    if MAX_HISTORICAL_WINDOW != timedelta(days=7):
        raise ContractValidationError("invalid_historical_ceiling")
    if len(_US_STATE_CODES) != 50:
        raise ContractValidationError("invalid_region_vocabulary")
    closed = (
        PRODUCTION_PRINCIPAL_HOST_INTEGRATED,
        PRODUCTION_POLICY_HOST_INTEGRATED,
        PRODUCTION_CONSUMPTION_LEDGER_INTEGRATED,
        PRODUCTION_BUDGET_HOST_INTEGRATED,
        PRODUCTION_SCOPE_HOST_INTEGRATED,
        PRODUCTION_SCHEDULER_HOST_INTEGRATED,
        PRODUCTION_SIGNING_KEY_STORE_INTEGRATED,
        PRODUCTION_GRANT_ISSUANCE_AVAILABLE,
        DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE,
        HMAC_PREVENTS_REPLAY,
        LOCAL_HARNESS_IS_PRODUCTION_AUTHORIZATION,
    )
    if any(flag is not False for flag in closed):
        raise ContractValidationError("production_grant_issuance_closed")
    if SOURCE_AUTHENTICITY_NOT_ESTABLISHED != "NOT_ESTABLISHED":
        raise ContractValidationError("source_authenticity_not_established")
    if MasterRouteExecutionAuthority.DENIED.value != "DENIED":
        raise ContractValidationError("execution_authority_not_granted")
    if MasterProductionAuthority.DENIED.value != "DENIED":
        raise ContractValidationError("production_authority_not_issued")
    if hasattr(MasterRouteExecutionAuthority, "GRANTED"):
        raise ContractValidationError("execution_authority_not_granted")


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


def _whole_second(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or len(value) != 20 or not value.endswith("Z") or value[10] != "T":
        raise ContractValidationError(f"invalid_{field_name}")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ContractValidationError(f"invalid_{field_name}") from exc
    if parsed.utcoffset() != timedelta(0) or parsed.microsecond != 0:
        raise ContractValidationError(f"invalid_{field_name}")
    return parsed


def _count(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _require_issue_permit(args: tuple[object, ...], kwargs: Mapping[str, object]) -> None:
    if args or set(kwargs) != {"_permit"} or kwargs.get("_permit") is not _ISSUE_PERMIT:
        raise ContractValidationError("grant_constructor_refused")


def _lock_authority(
    *,
    execution_authority: object,
    production_authority: object,
    source_authenticity: object,
    request_coverage: object,
    execution_permitted: object,
    dispatch_permitted: object,
    specialist_dispatched: object,
    atc_instructions_authorized: object,
    data_plane_write_authorized: object,
    production_grant: object,
    establishes_authority: object,
    source_authenticated: object,
    hmac_prevents_replay: object,
) -> None:
    if execution_authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
    if production_authority is not MasterProductionAuthority.DENIED:
        raise ContractValidationError("production_authority_not_issued")
    if source_authenticity != SOURCE_AUTHENTICITY_NOT_ESTABLISHED:
        raise ContractValidationError("source_authenticity_not_established")
    if request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
        raise ContractValidationError("request_coverage_not_established")
    flags = (
        execution_permitted,
        dispatch_permitted,
        specialist_dispatched,
        atc_instructions_authorized,
        data_plane_write_authorized,
        production_grant,
        establishes_authority,
        source_authenticated,
        hmac_prevents_replay,
    )
    if any(flag is not False for flag in flags):
        raise ContractValidationError("execution_not_granted")


def _utc_instant(value: object, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timezone.utc.utcoffset(None)
        or value.microsecond != 0
    ):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _sign(key: bytes, document: Mapping[str, object]) -> str:
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _resource_document(scope: tuple["LogicalReadOperation", ...]) -> list[list[str]]:
    pairs = [(item.resource_id, item.operation.value) for item in scope]
    pairs.sort()
    return [[resource_id, operation] for resource_id, operation in pairs]


def _document(
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
    tool_name: str,
    resource_scope: tuple["LogicalReadOperation", ...],
    argument_names: tuple[str, ...],
    region_scope: tuple[str, ...],
    temporal_scopes: tuple[TemporalScope, ...],
    historical_window: tuple[str, str] | None,
    decision_target_scope: str | None,
    accepted_at_utc: str,
    deadline_utc: str,
    expires_at_utc: str,
    idempotency_key: str,
    nonce: str,
    max_model_turns: int,
    max_tool_calls: int,
    correlation_id: str,
    key_id: str,
) -> dict[str, object]:
    return {
        "schema_version": MASTER_EXECUTION_GRANT_SCHEMA_VERSION,
        "authority_domain": _LOCAL_HARNESS_DOMAIN,
        "master_request_id": master_request_id,
        "planning_session_id": planning_session_id,
        "step_id": step_id,
        "invocation_id": invocation_id,
        "specialist": specialist.value,
        "purpose": purpose.value,
        "capability": capability.value,
        "principal_ref": principal_ref,
        "policy_revision": policy_revision,
        "tool_name": tool_name,
        "resource_scope": _resource_document(resource_scope),
        "argument_names": sorted(argument_names),
        "region_scope": sorted(region_scope),
        "temporal_scopes": [item.value for item in temporal_scopes],
        "historical_window": None if historical_window is None else [historical_window[0], historical_window[1]],
        "decision_target_scope": decision_target_scope,
        "accepted_at_utc": accepted_at_utc,
        "deadline_utc": deadline_utc,
        "expires_at_utc": expires_at_utc,
        "idempotency_key": idempotency_key,
        "nonce": nonce,
        "max_model_turns": max_model_turns,
        "max_tool_calls": max_tool_calls,
        "correlation_id": correlation_id,
        "key_id": key_id,
    }


@dataclass(frozen=True)
class IntegrationObligation:
    """One cross-agent dependency. Status cannot be marked integrated here."""

    agent: str
    module: str
    status: str
    obligation: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.agent, str)
            or not self.agent.strip()
            or self.agent != self.agent.strip()
            or len(self.agent) > PROVENANCE_TOKEN_MAX_LENGTH
        ):
            raise ContractValidationError("invalid_agent")
        _token(self.module, "module")
        if self.status != "NOT_INTEGRATED":
            raise ContractValidationError("integration_not_established")
        if not isinstance(self.obligation, str) or self.obligation != self.obligation.strip() or not self.obligation:
            raise ContractValidationError("invalid_obligation")


INTEGRATION_OBLIGATIONS = (
    IntegrationObligation(
        agent="Agent 2",
        module="wilvor_ai.master_application_principal",
        status="NOT_INTEGRATED",
        obligation=(
            "Seal the application principal after reviewed gateway admission and "
            "a Lambda invoke restriction. A principal_ref string is only a hint."
        ),
    ),
    IntegrationObligation(
        agent="Agent 3",
        module="wilvor_ai.master_runtime_policy",
        status="NOT_INTEGRATED",
        obligation=(
            "Provide the reviewed deterministic policy decision for the retained "
            "invocation. policy_revision and a bounds-accepted evaluation are not that decision."
        ),
    ),
    IntegrationObligation(
        agent="Agent 5",
        module="wilvor_ai.master_execution_ledger",
        status="NOT_INTEGRATED",
        obligation=(
            "Atomically reserve_once, mark_started, complete, fail, and cancel "
            "the grant nonce and idempotency key across Lambda instances. HMAC does not prevent replay."
        ),
    ),
    IntegrationObligation(
        agent="Agent 6",
        module="wilvor_ai.master_runtime_budget",
        status="NOT_INTEGRATED",
        obligation=(
            "Own request acceptance, the absolute deadline, monotonic execution time, "
            "and cancellation at tool execution. The grant clock only rejects expiry before consumption."
        ),
    ),
    IntegrationObligation(
        agent="Agent 7",
        module="wilvor_ai.master_authorized_scope",
        status="NOT_INTEGRATED",
        obligation=(
            "Intersect principal entitlement, population, region, temporal scope, "
            "and Decision target. Harness labels are not that intersection."
        ),
    ),
    IntegrationObligation(
        agent="Agent 9",
        module="wilvor_ai.master_authorized_scheduler_adapter",
        status="NOT_INTEGRATED",
        obligation=(
            "Present the retained plan step and original invocation, then verify and "
            "consume before dispatch. A fanout claim and a serialized grant are not dispatch."
        ),
    ),
)


@dataclass(frozen=True)
class LogicalReadOperation:
    """One keyed read. Scan and writes cannot be constructed."""

    resource_id: str
    operation: ReadOperation

    def __post_init__(self) -> None:
        _token(self.resource_id, "resource_id")
        if not isinstance(self.operation, ReadOperation):
            raise ContractValidationError("invalid_operation")
        if self.operation is ReadOperation.SCAN or self.operation not in _READ_OPERATIONS:
            raise ContractValidationError("scan_not_permitted")
        if self.resource_id in _WRITE_OR_ATC_TOOLS or self.resource_id.casefold().startswith("arn:"):
            raise ContractValidationError("data_plane_write_not_authorized")


@dataclass(frozen=True)
class MasterExecutionGrantEnvelope:
    """Descriptive shared envelope. Construction does not issue a grant."""

    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    principal_ref: str
    policy_revision: str
    tool_name: str
    resource_scope: tuple[LogicalReadOperation, ...]
    region_scope: tuple[str, ...]
    temporal_scopes: tuple[TemporalScope, ...]
    historical_window: tuple[str, str] | None
    decision_target_scope: str | None
    accepted_at_utc: str
    deadline_utc: str
    expires_at_utc: str
    idempotency_key: str
    max_model_turns: int
    max_tool_calls: int
    correlation_id: str
    argument_names: tuple[str, ...] | None = None
    lifecycle: GrantLifecycle = GrantLifecycle.DESCRIPTIVE
    production_grant: bool = False
    establishes_authority: bool = False

    def __post_init__(self) -> None:
        if self.lifecycle is not GrantLifecycle.DESCRIPTIVE:
            raise ContractValidationError("invalid_grant_lifecycle")
        if self.production_grant is not False or self.establishes_authority is not False:
            raise ContractValidationError("execution_not_granted")
        _token(self.master_request_id, "master_request_id")
        _token(self.planning_session_id, "planning_session_id")
        _token(self.step_id, "step_id")
        _token(self.invocation_id, "invocation_id")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        if not isinstance(self.capability, AgentCapability):
            raise ContractValidationError("invalid_capability")
        _token(self.principal_ref, "principal_ref")
        _token(self.policy_revision, "policy_revision")
        _token(self.tool_name, "tool_name")
        _token(self.idempotency_key, "idempotency_key")
        _token(self.correlation_id, "correlation_id")
        _whole_second(self.accepted_at_utc, "accepted_at_utc")
        _whole_second(self.deadline_utc, "deadline_utc")
        _whole_second(self.expires_at_utc, "expires_at_utc")
        _count(self.max_model_turns, "max_model_turns")
        _count(self.max_tool_calls, "max_tool_calls")
        scope = self.resource_scope
        if (
            not isinstance(scope, tuple)
            or not scope
            or any(type(item) is not LogicalReadOperation for item in scope)
            or len(scope) != len(set(scope))
        ):
            raise ContractValidationError("invalid_resource_scope")
        region = self.region_scope
        if not isinstance(region, tuple) or any(not isinstance(item, str) for item in region):
            raise ContractValidationError("invalid_region_scope")
        temporal = self.temporal_scopes
        if not isinstance(temporal, tuple) or any(not isinstance(item, TemporalScope) for item in temporal):
            raise ContractValidationError("invalid_temporal_scopes")
        window = self.historical_window
        if window is not None:
            if not isinstance(window, tuple) or len(window) != 2:
                raise ContractValidationError("invalid_historical_window")
            _whole_second(window[0], "historical_start_utc")
            _whole_second(window[1], "historical_end_utc")
        if self.decision_target_scope is not None:
            _token(self.decision_target_scope, "decision_target_scope")
        names = self.argument_names
        if names is not None:
            if not isinstance(names, tuple) or any(not isinstance(item, str) for item in names):
                raise ContractValidationError("invalid_argument_names")
            for item in names:
                _token(item, "argument_name")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class MasterExecutionGrantEvaluation:
    """Bounds check. A true ``bounds_accepted`` still issues nothing."""

    lifecycle: GrantLifecycle
    bounds_accepted: bool
    reasons: tuple[GrantRejectionReason, ...]
    limitations: tuple[GrantEvaluationLimitation, ...]
    tool_name: str | None
    resource_scope: tuple[LogicalReadOperation, ...] | None
    argument_names: tuple[str, ...] | None
    region_scope: tuple[str, ...] | None
    temporal_scopes: tuple[TemporalScope, ...] | None
    historical_window: tuple[str, str] | None
    decision_target_scope: str | None
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: str = SOURCE_AUTHENTICITY_NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    production_grant_issued: bool = False
    establishes_authority: bool = False
    execution_permitted: bool = False
    dispatch_permitted: bool = False
    specialist_dispatched: bool = False
    atc_instructions_authorized: bool = False
    data_plane_write_authorized: bool = False
    source_authenticated: bool = False
    hmac_prevents_replay: bool = False
    decision_target_trusted: bool = False

    def __post_init__(self) -> None:
        if self.lifecycle is not GrantLifecycle.EVALUATED:
            raise ContractValidationError("invalid_grant_lifecycle")
        if type(self.bounds_accepted) is not bool:
            raise ContractValidationError("invalid_bounds_accepted")
        if self.limitations != _LIMITATIONS:
            raise ContractValidationError("evaluation_limitation_cleared")
        if not isinstance(self.reasons, tuple) or any(not isinstance(item, GrantRejectionReason) for item in self.reasons):
            raise ContractValidationError("invalid_denial_reasons")
        if self.bounds_accepted is (len(self.reasons) > 0):
            raise ContractValidationError("bounds_reason_mismatch")
        if self.decision_target_trusted is not False:
            raise ContractValidationError("target_authorization_not_granted")
        _lock_authority(
            execution_authority=self.execution_authority,
            production_authority=self.production_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            execution_permitted=self.execution_permitted,
            dispatch_permitted=self.dispatch_permitted,
            specialist_dispatched=self.specialist_dispatched,
            atc_instructions_authorized=self.atc_instructions_authorized,
            data_plane_write_authorized=self.data_plane_write_authorized,
            production_grant=self.production_grant_issued,
            establishes_authority=self.establishes_authority,
            source_authenticated=self.source_authenticated,
            hmac_prevents_replay=self.hmac_prevents_replay,
        )
        if self.bounds_accepted:
            if self.tool_name is None or self.resource_scope is None or self.argument_names is None:
                raise ContractValidationError("invalid_evaluation")
            if self.region_scope is None or self.temporal_scopes is None:
                raise ContractValidationError("invalid_evaluation")
            return
        if (
            self.tool_name is not None
            or self.resource_scope is not None
            or self.argument_names is not None
            or self.region_scope is not None
            or self.temporal_scopes is not None
        ):
            raise ContractValidationError("invalid_evaluation")

    def __bool__(self) -> bool:
        return False


def _unique(reasons: list[GrantRejectionReason]) -> tuple[GrantRejectionReason, ...]:
    return tuple(dict.fromkeys(reasons))


def _region_canonical(region_scope: tuple[str, ...]) -> tuple[str, ...] | None:
    if not region_scope or len(region_scope) != len(set(region_scope)):
        return None
    if any(code not in _US_STATE_CODES for code in region_scope):
        return None
    return tuple(sorted(region_scope))


def evaluate_grant_envelope(envelope: object) -> MasterExecutionGrantEvaluation:
    """Check server ceilings. The result is not a host-issued grant."""

    if type(envelope) is not MasterExecutionGrantEnvelope:
        raise ContractValidationError("invalid_grant_envelope")
    reasons: list[GrantRejectionReason] = []
    capability = envelope.capability
    if capability in _ATC_CAPABILITIES:
        reasons.append(GrantRejectionReason.PROHIBITED_CAPABILITY)
        reasons.append(GrantRejectionReason.ATC_NOT_AUTHORIZED)
    elif capability in _WRITE_CAPABILITIES:
        reasons.append(GrantRejectionReason.PROHIBITED_CAPABILITY)
        reasons.append(GrantRejectionReason.DATA_PLANE_WRITE_NOT_AUTHORIZED)
    elif capability not in ALLOWED_V1_CAPABILITIES:
        reasons.append(GrantRejectionReason.UNSUPPORTED_CAPABILITY)
    allowed = _PAIR_CAPABILITIES.get((envelope.specialist, envelope.purpose))
    if capability in ALLOWED_V1_CAPABILITIES and (allowed is None or capability not in allowed):
        reasons.append(GrantRejectionReason.CAPABILITY_SPECIALIST_MISMATCH)
    if envelope.temporal_scopes not in _TEMPORAL.get(envelope.specialist, frozenset()):
        reasons.append(GrantRejectionReason.TEMPORAL_SCOPE_REJECTED)
    region = _region_canonical(envelope.region_scope)
    if region is None:
        reasons.append(GrantRejectionReason.REGION_NOT_AUTHORIZED)
    historical = envelope.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    window = envelope.historical_window
    if historical and window is None:
        reasons.append(GrantRejectionReason.HISTORICAL_WINDOW_REQUIRED)
    elif not historical and window is not None:
        reasons.append(GrantRejectionReason.HISTORICAL_WINDOW_NOT_APPLICABLE)
    elif window is not None:
        start = _whole_second(window[0], "historical_start_utc")
        end = _whole_second(window[1], "historical_end_utc")
        if end <= start or end - start > MAX_HISTORICAL_WINDOW:
            reasons.append(GrantRejectionReason.HISTORICAL_INTERVAL_EXCEEDS_CEILING)
    decision = envelope.specialist is MasterSpecialist.DECISION
    if decision and envelope.decision_target_scope is None:
        reasons.append(GrantRejectionReason.DECISION_TARGET_NOT_BOUND)
    elif not decision and envelope.decision_target_scope is not None:
        reasons.append(GrantRejectionReason.DECISION_TARGET_NOT_APPLICABLE)
    expires_at = _whole_second(envelope.expires_at_utc, "expires_at_utc")
    deadline = _whole_second(envelope.deadline_utc, "deadline_utc")
    if expires_at > deadline:
        reasons.append(GrantRejectionReason.DEADLINE_BOUNDS_INVALID)
    executions, turns, tool_calls = server_read_budget(envelope.specialist)
    if executions != 1 or envelope.max_model_turns > turns or envelope.max_tool_calls > tool_calls:
        reasons.append(GrantRejectionReason.BUDGET_EXCEEDS_SERVER_CEILING)
    tool_name = envelope.tool_name
    server_arguments: tuple[str, ...] | None = None
    if tool_name in _WRITE_OR_ATC_TOOLS:
        reasons.append(GrantRejectionReason.DATA_PLANE_WRITE_NOT_AUTHORIZED)
        if tool_name == "issue_atc_instructions":
            reasons.append(GrantRejectionReason.ATC_NOT_AUTHORIZED)
    elif tool_name not in server_read_tools(envelope.specialist):
        reasons.append(GrantRejectionReason.TOOL_NOT_ALLOWLISTED)
    else:
        allowed_resources = server_read_resources(envelope.specialist, tool_name)
        presented = frozenset((item.resource_id, item.operation) for item in envelope.resource_scope)
        if not presented or any(operation is ReadOperation.SCAN for _resource, operation in presented):
            reasons.append(GrantRejectionReason.SCAN_NOT_PERMITTED)
        elif presented != allowed_resources:
            reasons.append(GrantRejectionReason.RESOURCE_SCOPE_NOT_EXACT)
        else:
            server_arguments = tuple(sorted(server_read_argument_names(envelope.specialist, tool_name)))
            supplied = envelope.argument_names
            if supplied is not None and tuple(sorted(supplied)) != server_arguments:
                reasons.append(GrantRejectionReason.CALLER_ARGUMENT_NAMES_NOT_AUTHORITY)
    unique = _unique(reasons)
    accepted = not unique
    return MasterExecutionGrantEvaluation(
        lifecycle=GrantLifecycle.EVALUATED,
        bounds_accepted=accepted,
        reasons=unique,
        limitations=_LIMITATIONS,
        tool_name=tool_name if accepted else None,
        resource_scope=envelope.resource_scope if accepted else None,
        argument_names=server_arguments if accepted else None,
        region_scope=region if accepted else None,
        temporal_scopes=envelope.temporal_scopes if accepted else None,
        historical_window=window if accepted else None,
        decision_target_scope=envelope.decision_target_scope if accepted else None,
    )


@dataclass(frozen=True)
class ProductionGrantRefusal:
    """Closed production issuance. The object cannot be redeemed."""

    reason: GrantRejectionReason
    observed_allow_label: str | None = None
    lifecycle: GrantLifecycle = GrantLifecycle.EVALUATED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: str = SOURCE_AUTHENTICITY_NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    production_grant_issued: bool = False
    establishes_authority: bool = False
    execution_permitted: bool = False
    dispatch_permitted: bool = False
    specialist_dispatched: bool = False
    atc_instructions_authorized: bool = False
    data_plane_write_authorized: bool = False
    source_authenticated: bool = False
    hmac_prevents_replay: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.reason, GrantRejectionReason):
            raise ContractValidationError("invalid_denial_reasons")
        if self.observed_allow_label not in {None, "WITHIN_ALLOWLIST"}:
            raise ContractValidationError("invalid_allow_label")
        _lock_authority(
            execution_authority=self.execution_authority,
            production_authority=self.production_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            execution_permitted=self.execution_permitted,
            dispatch_permitted=self.dispatch_permitted,
            specialist_dispatched=self.specialist_dispatched,
            atc_instructions_authorized=self.atc_instructions_authorized,
            data_plane_write_authorized=self.data_plane_write_authorized,
            production_grant=self.production_grant_issued,
            establishes_authority=self.establishes_authority,
            source_authenticated=self.source_authenticated,
            hmac_prevents_replay=self.hmac_prevents_replay,
        )

    def __bool__(self) -> bool:
        return False


def _allow_label(candidate: object) -> str | None:
    status = getattr(candidate, "status", None)
    if status is MasterReadCheckStatus.WITHIN_ALLOWLIST:
        return "WITHIN_ALLOWLIST"
    if isinstance(status, str) and status == "WITHIN_ALLOWLIST":
        return "WITHIN_ALLOWLIST"
    return None


def _refusal_reason(candidate: object) -> GrantRejectionReason:
    if type(candidate) is MasterReadResourceCheck:
        return GrantRejectionReason.READ_CHECK_NOT_A_GRANT
    if type(candidate) is MasterExecutionGrantEnvelope:
        return GrantRejectionReason.DESCRIPTIVE_ENVELOPE_NOT_A_GRANT
    if type(candidate) is MasterExecutionGrantEvaluation:
        return GrantRejectionReason.EVALUATION_NOT_A_GRANT
    if type(candidate) is LocalHarnessExecutionGrant:
        return GrantRejectionReason.LOCAL_HARNESS_GRANT_NOT_PRODUCTION
    if type(candidate) is ParsedExecutionGrantClaim:
        return GrantRejectionReason.SERIALIZED_CLAIM_NOT_AUTHORITY
    kind = type(candidate)
    mapped = _INSUFFICIENT_TYPES.get((kind.__module__, kind.__name__))
    if mapped is not None:
        return GrantRejectionReason(mapped)
    if kind.__module__ == "wilvor_ai.master_gateway_identity":
        return GrantRejectionReason.GATEWAY_IDENTITY_NOT_A_PRINCIPAL
    if kind.__module__ == "wilvor_ai.master_planning_session":
        return GrantRejectionReason.PLANNING_SESSION_NOT_A_GRANT
    if isinstance(candidate, (str, bytes, bytearray, bool)):
        return GrantRejectionReason.CALLER_TOKEN_NOT_A_GRANT
    if isinstance(candidate, Mapping):
        keys = {str(key).casefold() for key in candidate}
        if "requestcontext" in keys or "authorization" in keys or "headers" in keys:
            return GrantRejectionReason.CALLER_EVENT_NOT_A_GRANT
        return GrantRejectionReason.SERIALIZED_CLAIM_NOT_AUTHORITY
    return GrantRejectionReason.DENIED_BY_DEFAULT


def refuse_production_grant_issuance(candidate: object) -> ProductionGrantRefusal:
    """Refuse every candidate. Nothing in this function returns a grant."""

    return ProductionGrantRefusal(reason=_refusal_reason(candidate), observed_allow_label=_allow_label(candidate))


def issue_production_execution_grant(*_args: object, **_kwargs: object) -> None:
    """Production issuance gate. Arguments cannot open it."""

    _assert_closed_constants()
    raise ContractValidationError("production_grant_issuance_closed")


def bind_production_grant_to_master_ledger(*_args: object, **_kwargs: object) -> None:
    """Fail closed. No reviewed issuer can bind a grant into the master ledger.

    ``LocalHarnessGrantIssuer.consume`` accepts only
    ``LocalHarnessConsumptionLedger``. ``MasterExecutionLedger.reserve_once``
    generates its own claim nonce, and ``mark_started`` does not consume a
    signed grant. Those are not one conditional write. The necessary
    production seam, which this function does not implement, is:

    ``LocalHarnessGrantIssuer.consume(claim, ledger: ReviewedAtomicGrantLedger, **bindings)``
    where ``ReviewedAtomicGrantLedger.reserve_once(nonce, idempotency_key, attempt_token, fence_epoch)``
    is the same conditional item write as
    ``MasterExecutionLedger.mark_started(envelope, *, owner_id, attempt_token, fence_epoch, now_utc)``,
    and the signed grant nonce is that ledger nonce rather than two independent secrets.
    """

    _assert_closed_constants()
    raise ContractValidationError("ledger_not_integrated")


class GrantEvaluationClock:
    """Injected evaluation clock. It is not the application request clock.

    ``advance_to`` may move backward. An expiry latch on the issuer does not
    clear when the clock does. This clock is not a source-observation time.
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


class LocalHarnessPrincipal:
    """Principal sealed by one local issuer. A copied object is a different principal."""

    __slots__ = ("_issuer_token", "_reference")

    def __new__(cls, *args: object, **kwargs: object) -> LocalHarnessPrincipal:
        _require_issue_permit(args, kwargs)
        return object.__new__(cls)

    def __init__(self, *args: object, **kwargs: object) -> None:
        _require_issue_permit(args, kwargs)
        self._issuer_token: bytes | None = None
        self._reference = ""

    @property
    def reference(self) -> str:
        return self._reference

    def __repr__(self) -> str:
        return "LocalHarnessPrincipal(local_harness=True, production_principal=False)"

    def __copy__(self) -> LocalHarnessPrincipal:
        return _inert_principal()

    def __deepcopy__(self, memo: dict[int, object]) -> LocalHarnessPrincipal:
        cloned = _inert_principal()
        memo[id(self)] = cloned
        return cloned


def _inert_principal() -> LocalHarnessPrincipal:
    principal = LocalHarnessPrincipal(_permit=_ISSUE_PERMIT)
    principal._issuer_token = None
    principal._reference = ""
    return principal


class LocalHarnessIssuanceTicket:
    """Single-use seal from ``prepare``. Not a grant and not serializable authority."""

    __slots__ = ("_issuer_token", "_principal", "_envelope", "_evaluation", "_used")

    def __new__(cls, *args: object, **kwargs: object) -> LocalHarnessIssuanceTicket:
        _require_issue_permit(args, kwargs)
        return object.__new__(cls)

    def __init__(self, *args: object, **kwargs: object) -> None:
        _require_issue_permit(args, kwargs)
        self._issuer_token: bytes | None = None
        self._principal: LocalHarnessPrincipal | None = None
        self._envelope: MasterExecutionGrantEnvelope | None = None
        self._evaluation: MasterExecutionGrantEvaluation | None = None
        self._used = False

    def __bool__(self) -> bool:
        return False


class LocalHarnessExecutionGrant:
    """Opaque local-harness grant. The public constructor does not sign one.

    Serialization returns a claim. Parsing that claim does not establish
    authority. Copying and pickling produce an inert object.
    """

    __slots__ = ("_issuer_token", "_nonce", "_document", "_signature", "_key_id")

    def __new__(cls, *args: object, **kwargs: object) -> LocalHarnessExecutionGrant:
        _require_issue_permit(args, kwargs)
        return object.__new__(cls)

    def __init__(self, *args: object, **kwargs: object) -> None:
        _require_issue_permit(args, kwargs)
        self._issuer_token: bytes | None = None
        self._nonce = ""
        self._document: dict[str, object] | None = None
        self._signature = ""
        self._key_id = ""

    def serialized_claim(self) -> dict[str, object]:
        if self._document is None:
            raise ContractValidationError("copied_grant")
        encoded = json.dumps(self._document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        body = json.loads(encoded)
        if not isinstance(body, dict):
            raise ContractValidationError("copied_grant")
        body["signature"] = self._signature
        return body

    def __repr__(self) -> str:
        return "LocalHarnessExecutionGrant(local_harness=True, execution_authority='DENIED')"

    def __bool__(self) -> bool:
        return False

    def __copy__(self) -> LocalHarnessExecutionGrant:
        return _inert_grant()

    def __deepcopy__(self, memo: dict[int, object]) -> LocalHarnessExecutionGrant:
        cloned = _inert_grant()
        memo[id(self)] = cloned
        return cloned

    def __reduce_ex__(self, protocol: int) -> tuple[object, tuple[object, ...]]:
        del protocol
        return (_inert_grant, ())


def _inert_grant() -> LocalHarnessExecutionGrant:
    grant = LocalHarnessExecutionGrant(_permit=_ISSUE_PERMIT)
    return grant


@dataclass(frozen=True)
class ParsedExecutionGrantClaim:
    """Parsed bytes. Holding this object establishes no authority."""

    document: dict[str, object]
    signature: str
    establishes_authority: bool = False
    production_grant: bool = False
    execution_permitted: bool = False

    def __post_init__(self) -> None:
        if self.establishes_authority is not False or self.production_grant is not False or self.execution_permitted is not False:
            raise ContractValidationError("execution_not_granted")
        if not isinstance(self.document, dict) or not isinstance(self.signature, str):
            raise ContractValidationError("invalid_grant_claim")

    def __bool__(self) -> bool:
        return False

    @property
    def nonce(self) -> str:
        nonce = self.document.get("nonce")
        if not isinstance(nonce, str):
            raise ContractValidationError("invalid_grant_claim")
        return nonce


def parse_execution_grant(payload: object) -> ParsedExecutionGrantClaim:
    """Parse a mapping. The result is not a verified grant and not authority."""

    if not isinstance(payload, Mapping):
        raise ContractValidationError("invalid_grant_claim")
    if payload.get("authority_domain") != _LOCAL_HARNESS_DOMAIN:
        raise ContractValidationError("production_grant_issuance_closed")
    if payload.get("schema_version") != MASTER_EXECUTION_GRANT_SCHEMA_VERSION:
        raise ContractValidationError("invalid_schema_version")
    document: dict[str, object] = {}
    for field_name in _SIGNED_FIELDS:
        if field_name not in payload:
            raise ContractValidationError("invalid_grant_claim")
        document[field_name] = payload[field_name]
    signature = payload.get("signature")
    if not isinstance(signature, str) or len(signature) != 64:
        raise ContractValidationError("invalid_grant_claim")
    try:
        int(signature, 16)
    except ValueError as exc:
        raise ContractValidationError("invalid_grant_claim") from exc
    return ParsedExecutionGrantClaim(document=document, signature=signature)


@dataclass(frozen=True)
class GrantVerification:
    """Integrity and binding result. True acceptance still is not authority."""

    lifecycle: GrantLifecycle
    reason: GrantRejectionReason | None
    signature_intact: bool
    local_harness_binding_accepted: bool
    consumed: bool
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: str = SOURCE_AUTHENTICITY_NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    production_grant: bool = False
    establishes_authority: bool = False
    execution_permitted: bool = False
    dispatch_permitted: bool = False
    specialist_dispatched: bool = False
    atc_instructions_authorized: bool = False
    data_plane_write_authorized: bool = False
    source_authenticated: bool = False
    hmac_prevents_replay: bool = False
    decision_target_trusted: bool = False

    def __post_init__(self) -> None:
        if self.lifecycle not in {GrantLifecycle.HOST_ISSUED_LOCAL_HARNESS, GrantLifecycle.EVALUATED}:
            raise ContractValidationError("invalid_grant_lifecycle")
        if self.reason is not None and not isinstance(self.reason, GrantRejectionReason):
            raise ContractValidationError("invalid_denial_reasons")
        if type(self.signature_intact) is not bool or type(self.local_harness_binding_accepted) is not bool:
            raise ContractValidationError("invalid_verification")
        if type(self.consumed) is not bool:
            raise ContractValidationError("invalid_verification")
        if self.local_harness_binding_accepted and self.reason is not None:
            raise ContractValidationError("invalid_verification")
        if self.decision_target_trusted is not False:
            raise ContractValidationError("target_authorization_not_granted")
        _lock_authority(
            execution_authority=self.execution_authority,
            production_authority=self.production_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            execution_permitted=self.execution_permitted,
            dispatch_permitted=self.dispatch_permitted,
            specialist_dispatched=self.specialist_dispatched,
            atc_instructions_authorized=self.atc_instructions_authorized,
            data_plane_write_authorized=self.data_plane_write_authorized,
            production_grant=self.production_grant,
            establishes_authority=self.establishes_authority,
            source_authenticated=self.source_authenticated,
            hmac_prevents_replay=self.hmac_prevents_replay,
        )

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class GrantConsumption:
    """Local consumption record. ``CONSUMED_LOCAL_HARNESS`` is not dispatch."""

    lifecycle: GrantLifecycle
    reason: GrantRejectionReason | None
    local_harness_consumption_recorded: bool
    signature_intact: bool
    durable_single_use: bool
    distributed_ledger: bool
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: str = SOURCE_AUTHENTICITY_NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    production_grant: bool = False
    establishes_authority: bool = False
    execution_permitted: bool = False
    dispatch_permitted: bool = False
    specialist_dispatched: bool = False
    atc_instructions_authorized: bool = False
    data_plane_write_authorized: bool = False
    source_authenticated: bool = False
    hmac_prevents_replay: bool = False
    decision_target_trusted: bool = False

    def __post_init__(self) -> None:
        if self.lifecycle not in {GrantLifecycle.CONSUMED_LOCAL_HARNESS, GrantLifecycle.EVALUATED}:
            raise ContractValidationError("invalid_grant_lifecycle")
        if self.reason is not None and not isinstance(self.reason, GrantRejectionReason):
            raise ContractValidationError("invalid_denial_reasons")
        if self.local_harness_consumption_recorded:
            if self.lifecycle is not GrantLifecycle.CONSUMED_LOCAL_HARNESS or self.reason is not None:
                raise ContractValidationError("invalid_consumption")
        elif self.lifecycle is not GrantLifecycle.EVALUATED or self.reason is None:
            raise ContractValidationError("invalid_consumption")
        if self.durable_single_use is not False or self.distributed_ledger is not False:
            raise ContractValidationError("ledger_not_integrated")
        if self.decision_target_trusted is not False:
            raise ContractValidationError("target_authorization_not_granted")
        _lock_authority(
            execution_authority=self.execution_authority,
            production_authority=self.production_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            execution_permitted=self.execution_permitted,
            dispatch_permitted=self.dispatch_permitted,
            specialist_dispatched=self.specialist_dispatched,
            atc_instructions_authorized=self.atc_instructions_authorized,
            data_plane_write_authorized=self.data_plane_write_authorized,
            production_grant=self.production_grant,
            establishes_authority=self.establishes_authority,
            source_authenticated=self.source_authenticated,
            hmac_prevents_replay=self.hmac_prevents_replay,
        )

    def __bool__(self) -> bool:
        return False


class LocalHarnessConsumptionLedger:
    """Process-local ``reserve_once`` double for Agent 5.

    ``durable`` and ``distributed`` stay false. A second instance does not
    see the first instance's reservations. That is why a signature can be
    replayed across processes and why production issuance stays closed.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._nonces: set[str] = set()
        self._idempotency_keys: set[str] = set()
        self.durable = False
        self.distributed = False
        self.hmac_prevents_replay = False

    def reserve_once(self, *, nonce: str, idempotency_key: str) -> bool:
        """Reserve the pair once in this process. False means already present."""

        return self._reserve(nonce, idempotency_key) == "reserved"

    def holds_nonce(self, nonce: str) -> bool:
        """Observe a local reservation. Observation is not authorization."""

        checked = _token(nonce, "nonce")
        with self._lock:
            return checked in self._nonces

    def _reserve(self, nonce: str, idempotency_key: str) -> str:
        checked_nonce = _token(nonce, "nonce")
        checked_key = _token(idempotency_key, "idempotency_key")
        with self._lock:
            if checked_nonce in self._nonces:
                return "nonce"
            if checked_key in self._idempotency_keys:
                return "idempotency"
            self._nonces.add(checked_nonce)
            self._idempotency_keys.add(checked_key)
            return "reserved"


class _Issued:
    __slots__ = (
        "grant",
        "principal",
        "document",
        "signature",
        "key_id",
        "nonce",
        "idempotency_key",
        "expires_at",
        "expired",
        "consumed",
    )

    def __init__(
        self,
        grant: LocalHarnessExecutionGrant,
        principal: LocalHarnessPrincipal,
        document: dict[str, object],
        signature: str,
        key_id: str,
        nonce: str,
        idempotency_key: str,
        expires_at: datetime,
    ) -> None:
        self.grant = grant
        self.principal = principal
        self.document = document
        self.signature = signature
        self.key_id = key_id
        self.nonce = nonce
        self.idempotency_key = idempotency_key
        self.expires_at = expires_at
        self.expired = False
        self.consumed = False


class LocalHarnessGrantIssuer:
    """Private test host. It does not issue production grants.

    Signing keys live only in this object. ``issue`` accepts a ticket this
    issuer sealed. A caller token, a policy object, or a serialized claim
    cannot be passed to ``issue``.
    """

    def __init__(self, clock: GrantEvaluationClock) -> None:
        if type(clock) is not GrantEvaluationClock:
            raise ContractValidationError("invalid_clock")
        _assert_closed_constants()
        self._clock = clock
        self._lock = threading.Lock()
        self._token = secrets.token_bytes(32)
        self._keys: dict[str, bytes] = {}
        self._retired: set[str] = set()
        self._signing_key_id = self._install_key()
        self._issued: dict[str, _Issued] = {}
        self._idempotency: set[str] = set()

    def __getstate__(self) -> dict[str, object]:
        raise ContractValidationError("issuer_not_serializable")

    @property
    def signing_key_id(self) -> str:
        with self._lock:
            return self._signing_key_id

    @property
    def production_grants_issued(self) -> int:
        return 0

    def seal_principal(self, reference: str) -> LocalHarnessPrincipal:
        """Seal a local principal. The reference string alone is not this object."""

        checked = _token(reference, "principal_ref")
        principal = LocalHarnessPrincipal(_permit=_ISSUE_PERMIT)
        principal._issuer_token = self._token
        principal._reference = checked
        return principal

    def prepare(
        self,
        envelope: object,
        principal: object,
    ) -> LocalHarnessIssuanceTicket:
        """Seal one evaluated envelope for this issuer. The ticket is not a grant."""

        evaluation = evaluate_grant_envelope(envelope)
        if not evaluation.bounds_accepted:
            raise ContractValidationError("grant_bounds_rejected")
        if type(principal) is not LocalHarnessPrincipal or principal._issuer_token != self._token:
            raise ContractValidationError("principal_not_sealed")
        if not isinstance(envelope, MasterExecutionGrantEnvelope):
            raise ContractValidationError("invalid_grant_envelope")
        if principal.reference != envelope.principal_ref:
            raise ContractValidationError("principal_reference_mismatch")
        expires_at = _whole_second(envelope.expires_at_utc, "expires_at_utc")
        if self._clock.now() >= expires_at:
            raise ContractValidationError("grant_already_expired")
        ticket = LocalHarnessIssuanceTicket(_permit=_ISSUE_PERMIT)
        ticket._issuer_token = self._token
        ticket._principal = principal
        ticket._envelope = envelope
        ticket._evaluation = evaluation
        return ticket

    def issue(self, ticket: object) -> LocalHarnessExecutionGrant:
        """Sign one unused local ticket. This does not open production issuance."""

        if type(ticket) is not LocalHarnessIssuanceTicket or ticket._issuer_token != self._token:
            raise ContractValidationError("invalid_issuance_ticket")
        with self._lock:
            if ticket._used:
                raise ContractValidationError("duplicate_issuance")
            envelope = ticket._envelope
            evaluation = ticket._evaluation
            principal = ticket._principal
            if envelope is None or evaluation is None or principal is None:
                raise ContractValidationError("invalid_issuance_ticket")
            if not evaluation.bounds_accepted or evaluation.argument_names is None or evaluation.region_scope is None:
                raise ContractValidationError("grant_bounds_rejected")
            if evaluation.temporal_scopes is None or evaluation.tool_name is None or evaluation.resource_scope is None:
                raise ContractValidationError("grant_bounds_rejected")
            expires_at = _whole_second(envelope.expires_at_utc, "expires_at_utc")
            if self._clock.now() >= expires_at:
                raise ContractValidationError("grant_already_expired")
            if envelope.idempotency_key in self._idempotency:
                raise ContractValidationError("duplicate_idempotency")
            key_id = self._signing_key_id
            key = self._keys[key_id]
            nonce = secrets.token_hex(16)
            while nonce in self._issued:
                nonce = secrets.token_hex(16)
            document = _document(
                master_request_id=envelope.master_request_id,
                planning_session_id=envelope.planning_session_id,
                step_id=envelope.step_id,
                invocation_id=envelope.invocation_id,
                specialist=envelope.specialist,
                purpose=envelope.purpose,
                capability=envelope.capability,
                principal_ref=principal.reference,
                policy_revision=envelope.policy_revision,
                tool_name=evaluation.tool_name,
                resource_scope=evaluation.resource_scope,
                argument_names=evaluation.argument_names,
                region_scope=evaluation.region_scope,
                temporal_scopes=evaluation.temporal_scopes,
                historical_window=evaluation.historical_window,
                decision_target_scope=evaluation.decision_target_scope,
                accepted_at_utc=envelope.accepted_at_utc,
                deadline_utc=envelope.deadline_utc,
                expires_at_utc=envelope.expires_at_utc,
                idempotency_key=envelope.idempotency_key,
                nonce=nonce,
                max_model_turns=envelope.max_model_turns,
                max_tool_calls=envelope.max_tool_calls,
                correlation_id=envelope.correlation_id,
                key_id=key_id,
            )
            signature = _sign(key, document)
            grant = LocalHarnessExecutionGrant(_permit=_ISSUE_PERMIT)
            grant._issuer_token = self._token
            grant._nonce = nonce
            grant._document = document
            grant._signature = signature
            grant._key_id = key_id
            self._issued[nonce] = _Issued(
                grant=grant,
                principal=principal,
                document=document,
                signature=signature,
                key_id=key_id,
                nonce=nonce,
                idempotency_key=envelope.idempotency_key,
                expires_at=expires_at,
            )
            self._idempotency.add(envelope.idempotency_key)
            ticket._used = True
            return grant

    def rotate_signing_key(self) -> str:
        """Install a new signing key. The previous key still verifies until retired."""

        with self._lock:
            self._signing_key_id = self._install_key()
            return self._signing_key_id

    def retire_verification_key(self, key_id: str) -> None:
        """Drop verification material. This does not by itself mark nonces consumed."""

        checked = _token(key_id, "key_id")
        with self._lock:
            if checked == self._signing_key_id:
                raise ContractValidationError("active_signing_key_not_retired")
            if checked not in self._keys:
                raise ContractValidationError("unknown_verification_key")
            del self._keys[checked]
            self._retired.add(checked)

    def verify(
        self,
        claim: object,
        *,
        principal: object,
        planning_session_id: str,
        step_id: str,
        invocation_id: str,
        specialist: MasterSpecialist,
        purpose: MasterPurpose,
        capability: AgentCapability,
        policy_revision: str,
        tool_name: str,
        resource_scope: tuple[LogicalReadOperation, ...],
        argument_names: tuple[str, ...],
        region_scope: tuple[str, ...],
        temporal_scopes: tuple[TemporalScope, ...],
        historical_window: tuple[str, str] | None,
        decision_target_scope: str | None,
        accepted_at_utc: str,
        deadline_utc: str,
        expires_at_utc: str,
        idempotency_key: str,
        max_model_turns: int,
        max_tool_calls: int,
        correlation_id: str,
        master_request_id: str,
    ) -> GrantVerification:
        """Check signature, issuer, bindings, and expiry. Does not consume."""

        with self._lock:
            return self._verify_locked(
                claim,
                principal=principal,
                planning_session_id=planning_session_id,
                step_id=step_id,
                invocation_id=invocation_id,
                specialist=specialist,
                purpose=purpose,
                capability=capability,
                policy_revision=policy_revision,
                tool_name=tool_name,
                resource_scope=resource_scope,
                argument_names=argument_names,
                region_scope=region_scope,
                temporal_scopes=temporal_scopes,
                historical_window=historical_window,
                decision_target_scope=decision_target_scope,
                accepted_at_utc=accepted_at_utc,
                deadline_utc=deadline_utc,
                expires_at_utc=expires_at_utc,
                idempotency_key=idempotency_key,
                max_model_turns=max_model_turns,
                max_tool_calls=max_tool_calls,
                correlation_id=correlation_id,
                master_request_id=master_request_id,
            )

    def consume(
        self,
        claim: object,
        ledger: object,
        **bindings: object,
    ) -> GrantConsumption:
        """Verify, then reserve the nonce once on the local double.

        ``MasterExecutionLedger`` is not accepted. A local reservation is not
        a durable consume. A missing ledger reserves nothing.
        """

        with self._lock:
            verification = self._verify_locked(claim, **bindings)
            if not verification.local_harness_binding_accepted:
                return _consumption_rejected(verification.reason or GrantRejectionReason.DENIED_BY_DEFAULT, verification.signature_intact)
            if ledger is None:
                return _consumption_rejected(GrantRejectionReason.MISSING_LEDGER, True)
            if DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE is not False or type(ledger) is not LocalHarnessConsumptionLedger:
                return _consumption_rejected(GrantRejectionReason.LEDGER_NOT_INTEGRATED, True)
            record = self._record_for_locked(claim)
            if record is None:
                return _consumption_rejected(GrantRejectionReason.FORGED, False)
            if record.consumed:
                return _consumption_rejected(GrantRejectionReason.REPLAY, True)
            reserved = ledger._reserve(record.nonce, record.idempotency_key)
            if reserved == "nonce":
                return _consumption_rejected(GrantRejectionReason.REPLAY, True)
            if reserved != "reserved":
                return _consumption_rejected(GrantRejectionReason.DUPLICATE_CONSUMPTION, True)
            record.consumed = True
            return GrantConsumption(
                lifecycle=GrantLifecycle.CONSUMED_LOCAL_HARNESS,
                reason=None,
                local_harness_consumption_recorded=True,
                signature_intact=True,
                durable_single_use=False,
                distributed_ledger=False,
            )

    def _install_key(self) -> str:
        key_id = secrets.token_hex(8)
        while key_id in self._keys or key_id in self._retired:
            key_id = secrets.token_hex(8)
        self._keys[key_id] = secrets.token_bytes(32)
        return key_id

    def _record_for_locked(self, claim: object) -> _Issued | None:
        if type(claim) is LocalHarnessExecutionGrant:
            if claim._issuer_token != self._token or claim._document is None:
                return None
            return self._issued.get(claim._nonce)
        if type(claim) is ParsedExecutionGrantClaim:
            return self._issued.get(claim.nonce)
        return None

    def _verify_locked(self, claim: object, **bindings: object) -> GrantVerification:
        if type(claim) is LocalHarnessExecutionGrant:
            if claim._issuer_token != self._token or claim._document is None:
                reason = (
                    GrantRejectionReason.COPIED_GRANT
                    if claim._issuer_token is None
                    else GrantRejectionReason.FOREIGN_ISSUER
                )
                return _verification_rejected(reason, False)
            record = self._issued.get(claim._nonce)
            if record is None or record.grant is not claim:
                return _verification_rejected(GrantRejectionReason.COPIED_GRANT, False)
            document = claim._document
            signature = claim._signature
        elif type(claim) is ParsedExecutionGrantClaim:
            record = self._issued.get(claim.nonce)
            if record is None:
                return _verification_rejected(GrantRejectionReason.FORGED, False)
            document = claim.document
            signature = claim.signature
            if document != record.document or signature != record.signature:
                return _verification_rejected(GrantRejectionReason.TAMPERED, False)
        else:
            return _verification_rejected(GrantRejectionReason.DENIED_BY_DEFAULT, False)
        if record.key_id in self._retired or record.key_id not in self._keys:
            return _verification_rejected(GrantRejectionReason.KEY_RETIRED, False)
        expected = _sign(self._keys[record.key_id], record.document)
        intact = hmac.compare_digest(expected, record.signature) and hmac.compare_digest(expected, signature)
        if not intact or document != record.document:
            return _verification_rejected(GrantRejectionReason.TAMPERED, False)
        principal = bindings.get("principal")
        if principal is not record.principal:
            return _verification_rejected(GrantRejectionReason.PRINCIPAL_MISMATCH, True)
        presented = _presented_document(bindings, nonce=record.nonce, key_id=record.key_id)
        if presented is None:
            return _verification_rejected(GrantRejectionReason.DENIED_BY_DEFAULT, True)
        for field_name, reason in _BINDING_ORDER:
            if presented[field_name] != record.document[field_name]:
                return _verification_rejected(reason, True)
        if record.expired or self._clock.now() >= record.expires_at:
            record.expired = True
            return _verification_rejected(GrantRejectionReason.EXPIRED, True)
        if record.consumed:
            return _verification_rejected(GrantRejectionReason.REPLAY, True)
        return GrantVerification(
            lifecycle=GrantLifecycle.HOST_ISSUED_LOCAL_HARNESS,
            reason=None,
            signature_intact=True,
            local_harness_binding_accepted=True,
            consumed=False,
        )


def _presented_document(bindings: Mapping[str, object], *, nonce: str, key_id: str) -> dict[str, object] | None:
    try:
        specialist = bindings["specialist"]
        purpose = bindings["purpose"]
        capability = bindings["capability"]
        resource_scope = bindings["resource_scope"]
        temporal_scopes = bindings["temporal_scopes"]
        region_scope = bindings["region_scope"]
        argument_names = bindings["argument_names"]
        historical_window = bindings["historical_window"]
        if (
            not isinstance(specialist, MasterSpecialist)
            or not isinstance(purpose, MasterPurpose)
            or not isinstance(capability, AgentCapability)
            or not isinstance(resource_scope, tuple)
            or any(type(item) is not LogicalReadOperation for item in resource_scope)
            or not isinstance(temporal_scopes, tuple)
            or any(not isinstance(item, TemporalScope) for item in temporal_scopes)
            or not isinstance(region_scope, tuple)
            or not isinstance(argument_names, tuple)
        ):
            return None
        if historical_window is not None and (
            not isinstance(historical_window, tuple) or len(historical_window) != 2
        ):
            return None
        decision = bindings["decision_target_scope"]
        if decision is not None and not isinstance(decision, str):
            return None
        return _document(
            master_request_id=_token(bindings["master_request_id"], "master_request_id"),
            planning_session_id=_token(bindings["planning_session_id"], "planning_session_id"),
            step_id=_token(bindings["step_id"], "step_id"),
            invocation_id=_token(bindings["invocation_id"], "invocation_id"),
            specialist=specialist,
            purpose=purpose,
            capability=capability,
            principal_ref=_token(getattr(bindings["principal"], "reference", None), "principal_ref"),
            policy_revision=_token(bindings["policy_revision"], "policy_revision"),
            tool_name=_token(bindings["tool_name"], "tool_name"),
            resource_scope=resource_scope,
            argument_names=tuple(_token(item, "argument_name") for item in argument_names),
            region_scope=tuple(sorted(_token(item, "region_code") for item in region_scope)),
            temporal_scopes=temporal_scopes,
            historical_window=None if historical_window is None else (str(historical_window[0]), str(historical_window[1])),
            decision_target_scope=None if decision is None else _token(decision, "decision_target_scope"),
            accepted_at_utc=bindings["accepted_at_utc"] if isinstance(bindings["accepted_at_utc"], str) else "",
            deadline_utc=bindings["deadline_utc"] if isinstance(bindings["deadline_utc"], str) else "",
            expires_at_utc=bindings["expires_at_utc"] if isinstance(bindings["expires_at_utc"], str) else "",
            idempotency_key=_token(bindings["idempotency_key"], "idempotency_key"),
            nonce=nonce,
            max_model_turns=_count(bindings["max_model_turns"], "max_model_turns"),
            max_tool_calls=_count(bindings["max_tool_calls"], "max_tool_calls"),
            correlation_id=_token(bindings["correlation_id"], "correlation_id"),
            key_id=key_id,
        )
    except (ContractValidationError, KeyError, AttributeError):
        return None


def _verification_rejected(reason: GrantRejectionReason, signature_intact: bool) -> GrantVerification:
    return GrantVerification(
        lifecycle=GrantLifecycle.EVALUATED,
        reason=reason,
        signature_intact=signature_intact,
        local_harness_binding_accepted=False,
        consumed=reason is GrantRejectionReason.REPLAY,
    )


def _consumption_rejected(reason: GrantRejectionReason, signature_intact: bool) -> GrantConsumption:
    return GrantConsumption(
        lifecycle=GrantLifecycle.EVALUATED,
        reason=reason,
        local_harness_consumption_recorded=False,
        signature_intact=signature_intact,
        durable_single_use=False,
        distributed_ledger=False,
    )


_assert_closed_constants()


__all__ = [
    "DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE",
    "HMAC_PREVENTS_REPLAY",
    "INTEGRATION_OBLIGATIONS",
    "LOCAL_HARNESS_IS_PRODUCTION_AUTHORIZATION",
    "MAX_DECISION_STEPS",
    "MAX_HISTORICAL_WINDOW",
    "MAX_ROUTE_STEPS",
    "MASTER_EXECUTION_GRANT_SCHEMA_VERSION",
    "PRODUCTION_BUDGET_HOST_INTEGRATED",
    "PRODUCTION_CONSUMPTION_LEDGER_INTEGRATED",
    "PRODUCTION_GRANT_ISSUANCE_AVAILABLE",
    "PRODUCTION_POLICY_HOST_INTEGRATED",
    "PRODUCTION_PRINCIPAL_HOST_INTEGRATED",
    "PRODUCTION_SCHEDULER_HOST_INTEGRATED",
    "PRODUCTION_SCOPE_HOST_INTEGRATED",
    "PRODUCTION_SIGNING_KEY_STORE_INTEGRATED",
    "SOURCE_AUTHENTICITY_NOT_ESTABLISHED",
    "GrantConsumption",
    "GrantEvaluationClock",
    "GrantEvaluationLimitation",
    "GrantLifecycle",
    "GrantRejectionReason",
    "GrantVerification",
    "IntegrationObligation",
    "LocalHarnessConsumptionLedger",
    "LocalHarnessExecutionGrant",
    "LocalHarnessGrantIssuer",
    "LocalHarnessIssuanceTicket",
    "LocalHarnessPrincipal",
    "LogicalReadOperation",
    "MasterExecutionGrantEnvelope",
    "MasterExecutionGrantEvaluation",
    "ParsedExecutionGrantClaim",
    "ProductionGrantRefusal",
    "bind_production_grant_to_master_ledger",
    "evaluate_grant_envelope",
    "issue_production_execution_grant",
    "parse_execution_grant",
    "refuse_production_grant_issuance",
]
