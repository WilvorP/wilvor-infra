"""Deterministic host-side policy evaluation. Not a permit and not a grant.

This module decides whether one retained specialist invocation satisfies the
server policy. It does not call ``assess_master_execution_policy``. That
boundary stays fail-closed and unchanged. It does not dispatch a specialist,
mint a production principal, or issue a host grant.

Distinctions
------------
Descriptive
    ``MasterRuntimePolicyEnvelope`` carries the shared alignment fields
    (request, session, step, invocation, specialist, purpose, capability,
    principal ref, policy revision, resource and region scope, historical
    window, decision target, accepted time, deadline, idempotency key,
    model-turn and tool-call ceilings, correlation id). Those values are
    hints. A match against retained objects is a consistency check.
    ``authorized=True``, a policy word, a serialized decision, a JWT-like
    dict, and a test-mode flag are descriptive spoofs.

Evaluated
    ``MasterRuntimePolicyEvaluation`` is the typed ALLOW or DENY decision,
    the denial reasons, and the policy-revision fingerprint. ALLOW means
    the deterministic checks matched under the hypothetical test host or,
    in a later release, under a real host principal this module cannot
    mint. ALLOW does not execute anything. ``allows_specialist_execution``
    is always false. The object is not truthy.

Host-issued
    A grant minted by the reviewed application host after evaluation.
    ``HOST_GRANT_ISSUER_AVAILABLE`` is false. Nothing here is host-issued.
    See the Agent 4 binding below.

Consumed
    A single use of a future host-issued grant at tool execution. This
    module has no consume, redeem, or dispatch operation. Budget,
    cancellation, and the deadline must be enforced again at that
    execution. An evaluation does not consume a tool call.

Source-authenticated
    Proof that a read came from an approved runtime adapter and a completed
    source operation. ``source_authenticity`` stays ``NOT_ESTABLISHED``.
    An ALLOW decision does not authenticate a source, create a
    ``VerifiedZeroClaim``, or verify a SIGMET impact.

Production principal
    ``HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE`` is false. The production
    entry point has no parameter that can satisfy the principal
    requirement. Caller labels, booleans, environment variables
    (including a test-mode variable), and gateway JWT dictionaries are
    not proof. This module does not accept a Lambda event. API Gateway
    JWT validation is an edge control, not attestation on a direct
    invoke. Who may invoke a future Lambda is an AWS identity policy
    outside this module; it was not treated as authority here.

    The private hypothetical harness can return ALLOW so tests can prove
    the policy rules. That result is tagged ``HYPOTHETICAL_TEST_HOST``,
    ``production_principal_established`` stays false, and it still cannot
    be promoted to a permit. Production input cannot select the harness.

Clocks
    ``evaluation_utc`` is the policy evaluation clock. It is not the
    application request clock and not the deadline. Server
    ``accepted_at_utc`` and ``deadline_utc`` are the host clocks for this
    decision. Envelope clocks that disagree are not authority.

PROPOSED Agent 4 binding, not implemented here
----------------------------------------------
A later host-issued grant may cite one evaluation only when all of the
following are true. ``promote_policy_evaluation_to_permit`` refuses to
do any of this:

1. The grant is minted by the reviewed application host after this
   function returns, in that host process. The evaluation object is an
   input to that mint, not the grant.
2. The grant is bound to the evaluation object whose ``authority_origin``
   is a future ``HOST_AUTHENTICATED`` principal. This module cannot emit
   that origin. A ``HYPOTHETICAL_TEST_HOST`` ALLOW is ineligible. A
   copied ``policy_fingerprint`` string is not the evaluation; the
   fingerprint is a digest, not a credential.
3. The grant retains the same session, plan, step, and invocation
   objects the evaluation retained. Equal ids are not binding.
4. The grant copies ``tool_name``, ``resource_scope``, ``operation``,
   ``region_scope``, ``historical_window``, ``decision_target_scope``,
   ``deadline_utc``, ``idempotency_key``, and the server turn and tool
   ceilings. It must not widen them. ``policy_revision`` and
   ``policy_fingerprint`` are cited so a different policy cannot be
   substituted.
5. Consumption happens once, at tool execution, which rechecks
   cancellation, the execution clock against ``deadline_utc``, and the
   budget from ``server_read_budget``. This evaluation does not perform
   that recheck.
6. Source authentication of any row that execution returns remains an
   evidence-admission concern. The grant does not establish it.

Resource vocabulary is ``server_read_tools``, ``server_read_resources``,
``server_read_argument_names``, and ``server_read_budget``. Specialist
pairs are the same pairs as
``master_execution_policy_boundary.SPECIALIST_CAPABILITY_PAIRS``; the
contract test asserts equality. This module does not keep a second,
weaker tool allowlist. Scan, wildcard tables, cross-specialist reads,
arbitrary region text, unbound or arbitrary Decision targets, and
historical windows over seven days are denied. Ceilings
``MAX_ROUTE_STEPS`` (4) and ``MAX_DECISION_STEPS`` (1) are the routing
ceilings.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

from wilvor_ai.contracts import (
    ALLOWED_V1_CAPABILITIES,
    PROHIBITED_V1_CAPABILITIES,
    PROVENANCE_TOKEN_MAX_LENGTH,
    AgentCapability,
    ContractValidationError,
    _validate_bounded_text,
    _validate_utc,
)
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterPurpose,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_read_resource_guard import (
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
    MasterHintTrust,
    MasterPlanningContext,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRoutePlan,
    MasterRouteStepPlan,
)


MASTER_RUNTIME_POLICY_SCHEMA_VERSION = "wilvor.ai.master_runtime_policy.v1"
POLICY_REVISION = MASTER_RUNTIME_POLICY_SCHEMA_VERSION

# Production principal issuance and grant issuance are not available.
# Flipping either constant does not create a usable grant; evaluation
# refuses to run in that state.
HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE = False
HOST_GRANT_ISSUER_AVAILABLE = False

# Identical to master_execution_policy_boundary.SPECIALIST_CAPABILITY_PAIRS.
# The contract test asserts equality. Do not narrow or extend it here.
SPECIALIST_CAPABILITY_PAIRS = {
    (MasterSpecialist.LIVE_OPS, MasterPurpose.DISCOVERY): frozenset(
        {AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT}
    ),
    (MasterSpecialist.LIVE_OPS, MasterPurpose.ANSWER): frozenset(
        {AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT}
    ),
    (MasterSpecialist.HISTORICAL_ANALYTICS, MasterPurpose.ANSWER): frozenset(
        {AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS}
    ),
    (MasterSpecialist.DECISION, MasterPurpose.ANSWER): frozenset(
        {
            AgentCapability.EXPLAIN_EXISTING_RISK,
            AgentCapability.EXPLAIN_EXISTING_RECOMMENDATIONS,
        }
    ),
}

# Same fifty USPS codes the read guard seals. Not a tool allowlist.
_US_STATE_CODES = frozenset(
    {
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
        "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
        "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
        "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
        "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    }
)
_WILDCARDS = frozenset(
    {"*", "all", "all_tables", "any", "unrestricted", "table", "tables"}
)
_ALLOW_WORDS = frozenset(
    {
        "ALLOW",
        "ALLOWED",
        "PERMIT",
        "PERMITTED",
        "AUTHORIZED",
        "AUTHORIZE",
        "GRANTED",
        "GRANT",
        "APPROVED",
        "APPROVE",
        "TRUE",
        "YES",
        "ENABLED",
    }
)
_PAIR_KEYS = frozenset(SPECIALIST_CAPABILITY_PAIRS)


class _Seal:
    """Process-local marker. A caller string or boolean is not this object."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


_MEMBERSHIP = _Seal("runtime-policy-session")
_SERVER_SEAL = _Seal("server-policy-inputs")
_PRODUCTION_ORIGIN = _Seal("production-principal-not-established")
_HYPOTHETICAL_ORIGIN = _Seal("hypothetical-test-host")


class MasterRuntimePolicyDecision(str, Enum):
    """Evaluated policy decision. Neither member is a permit."""

    ALLOW = "ALLOW"
    DENY = "DENY"


class PolicyAuthorityOrigin(str, Enum):
    """Where the principal check stopped. Not a credential.

    ``HOST_AUTHENTICATED`` names a future origin this module cannot emit.
    """

    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    HYPOTHETICAL_TEST_HOST = "HYPOTHETICAL_TEST_HOST"
    HOST_AUTHENTICATED = "HOST_AUTHENTICATED"


class TargetObligationKind(str, Enum):
    """Server statement of a Decision target obligation. Not authorization."""

    NOT_APPLICABLE = "NOT_APPLICABLE"
    BOUND_AIRCRAFT = "BOUND_AIRCRAFT"
    BOUND_RECOMMENDATION = "BOUND_RECOMMENDATION"
    UNBOUND = "UNBOUND"


class MasterRuntimePolicyDenialReason(str, Enum):
    """Why an evaluation denied. Order is the canonical report order."""

    PRINCIPAL_NOT_HOST_AUTHENTICATED = "PRINCIPAL_NOT_HOST_AUTHENTICATED"
    PRINCIPAL_LABEL_NOT_AUTHENTICATION = "PRINCIPAL_LABEL_NOT_AUTHENTICATION"
    CALLER_AUTHORIZED_FLAG_NOT_AUTHORITY = "CALLER_AUTHORIZED_FLAG_NOT_AUTHORITY"
    CALLER_PROOF_NOT_AUTHORITY = "CALLER_PROOF_NOT_AUTHORITY"
    SERIALIZED_DECISION_NOT_AUTHORITY = "SERIALIZED_DECISION_NOT_AUTHORITY"
    CLAIMED_TEST_MODE_NOT_AUTHORITY = "CLAIMED_TEST_MODE_NOT_AUTHORITY"
    WRONG_SPECIALIST = "WRONG_SPECIALIST"
    WRONG_PURPOSE = "WRONG_PURPOSE"
    CAPABILITY_PAIR_MISMATCH = "CAPABILITY_PAIR_MISMATCH"
    PROHIBITED_CAPABILITY = "PROHIBITED_CAPABILITY"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    CROSS_SESSION = "CROSS_SESSION"
    STRING_IDENTITY_NOT_MEMBERSHIP = "STRING_IDENTITY_NOT_MEMBERSHIP"
    STEP_NOT_MEMBER = "STEP_NOT_MEMBER"
    INVOCATION_NOT_MEMBER = "INVOCATION_NOT_MEMBER"
    UNRESOLVED_ROUTE_GATE = "UNRESOLVED_ROUTE_GATE"
    REGION_AUTHORITY_NOT_ESTABLISHED = "REGION_AUTHORITY_NOT_ESTABLISHED"
    ARBITRARY_REGION_TEXT = "ARBITRARY_REGION_TEXT"
    REGION_NOT_IN_AUTHORITY = "REGION_NOT_IN_AUTHORITY"
    HISTORICAL_WINDOW_EXCEEDED = "HISTORICAL_WINDOW_EXCEEDED"
    HISTORICAL_WINDOW_INVALID = "HISTORICAL_WINDOW_INVALID"
    HISTORICAL_WINDOW_MISMATCH = "HISTORICAL_WINDOW_MISMATCH"
    UNKNOWN_RESOURCE = "UNKNOWN_RESOURCE"
    WILDCARD_RESOURCE = "WILDCARD_RESOURCE"
    ARBITRARY_TABLE_ARN = "ARBITRARY_TABLE_ARN"
    SCAN_NOT_PERMITTED = "SCAN_NOT_PERMITTED"
    CROSS_SPECIALIST_RESOURCE = "CROSS_SPECIALIST_RESOURCE"
    OPERATION_NOT_ALLOWLISTED = "OPERATION_NOT_ALLOWLISTED"
    UNSUPPORTED_TOOL = "UNSUPPORTED_TOOL"
    ARGUMENT_NOT_ALLOWLISTED = "ARGUMENT_NOT_ALLOWLISTED"
    ARGUMENT_MISSING = "ARGUMENT_MISSING"
    POPULATION_SCOPE_NOT_ESTABLISHED = "POPULATION_SCOPE_NOT_ESTABLISHED"
    TARGET_NOT_AUTHORIZED = "TARGET_NOT_AUTHORIZED"
    ARBITRARY_DECISION_TARGET = "ARBITRARY_DECISION_TARGET"
    UNBOUND_TARGET_ID = "UNBOUND_TARGET_ID"
    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
    STALE_OR_WRONG_CLOCK = "STALE_OR_WRONG_CLOCK"
    CALLER_CLOCK_NOT_AUTHORITY = "CALLER_CLOCK_NOT_AUTHORITY"
    CANCELLED = "CANCELLED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    CALLER_BUDGET_NOT_AUTHORITY = "CALLER_BUDGET_NOT_AUTHORITY"
    IDEMPOTENCY_KEY_MISMATCH = "IDEMPOTENCY_KEY_MISMATCH"
    POLICY_REVISION_MISMATCH = "POLICY_REVISION_MISMATCH"


class MasterRuntimePolicyLimitation(str, Enum):
    """Standing limits. Present on ALLOW and DENY."""

    EVALUATION_IS_NOT_A_GRANT = "EVALUATION_IS_NOT_A_GRANT"
    NO_SPECIALIST_EXECUTION_FROM_THIS_RESULT = "NO_SPECIALIST_EXECUTION_FROM_THIS_RESULT"
    PRODUCTION_AUTHORITY_REMAINS_DENIED = "PRODUCTION_AUTHORITY_REMAINS_DENIED"
    SOURCE_AUTHENTICITY_REMAINS_NOT_ESTABLISHED = (
        "SOURCE_AUTHENTICITY_REMAINS_NOT_ESTABLISHED"
    )
    REQUEST_COVERAGE_REMAINS_NOT_ESTABLISHED = "REQUEST_COVERAGE_REMAINS_NOT_ESTABLISHED"
    HOST_GRANT_ISSUER_UNAVAILABLE = "HOST_GRANT_ISSUER_UNAVAILABLE"
    TOOL_BUDGET_AND_CANCELLATION_RECHECK_REQUIRED = (
        "TOOL_BUDGET_AND_CANCELLATION_RECHECK_REQUIRED"
    )
    HYPOTHETICAL_RESULT_IS_NOT_PRODUCTION_AUTHORITY = (
        "HYPOTHETICAL_RESULT_IS_NOT_PRODUCTION_AUTHORITY"
    )
    DESCRIPTIVE_ALLOW_IS_NOT_HOST_ISSUED = "DESCRIPTIVE_ALLOW_IS_NOT_HOST_ISSUED"


_LIMITATIONS = tuple(MasterRuntimePolicyLimitation)
_REASON_ORDER = tuple(MasterRuntimePolicyDenialReason)


def _assert_vocabulary() -> None:
    if HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE is not False or HOST_GRANT_ISSUER_AVAILABLE is not False:
        raise ContractValidationError("host_authenticated_principal_not_available")
    if MAX_HISTORICAL_WINDOW != timedelta(days=7):
        raise ContractValidationError("invalid_historical_ceiling")
    if MAX_ROUTE_STEPS != 4 or MAX_DECISION_STEPS != 1:
        raise ContractValidationError("invalid_route_ceiling")
    if len(_US_STATE_CODES) != 50:
        raise ContractValidationError("invalid_region_vocabulary")
    if set(SPECIALIST_CAPABILITY_PAIRS) != _PAIR_KEYS:
        raise ContractValidationError("specialist_capability_vocabulary_mismatch")
    for capabilities in SPECIALIST_CAPABILITY_PAIRS.values():
        if not capabilities or not capabilities <= ALLOWED_V1_CAPABILITIES:
            raise ContractValidationError("specialist_capability_vocabulary_mismatch")
        if capabilities & PROHIBITED_V1_CAPABILITIES:
            raise ContractValidationError("specialist_capability_vocabulary_mismatch")
    for specialist in MasterSpecialist:
        for tool_name in server_read_tools(specialist):
            for _resource, operation in server_read_resources(specialist, tool_name):
                if operation is ReadOperation.SCAN:
                    raise ContractValidationError("scan_not_permitted")


def _require_closed_switches() -> None:
    if HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE is not False or HOST_GRANT_ISSUER_AVAILABLE is not False:
        raise ContractValidationError("host_authenticated_principal_not_available")


def _token(value: object, field_name: str) -> str:
    errors = _validate_bounded_text(value, field_name, max_length=PROVENANCE_TOKEN_MAX_LENGTH)
    if errors:
        raise ContractValidationError(errors)
    if not isinstance(value, str) or value != value.strip() or any(character.isspace() for character in value):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _optional_token(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _token(value, field_name)


def _prose(value: object, field_name: str) -> str:
    errors = _validate_bounded_text(value, field_name, max_length=PROVENANCE_TOKEN_MAX_LENGTH)
    if errors:
        raise ContractValidationError(errors)
    if not isinstance(value, str) or value != value.strip():
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _optional_prose(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _prose(value, field_name)


def _count(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _optional_bool(value: object, field_name: str) -> bool | None:
    if value is None:
        return None
    if type(value) is not bool:
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _whole_second(value: object, field_name: str) -> datetime:
    parsed = _parse_whole_second(value)
    if parsed is None:
        raise ContractValidationError(f"invalid_{field_name}")
    return parsed


def _parse_whole_second(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) != 20 or not value.endswith("Z") or value[10] != "T":
        return None
    if _validate_utc(value, "timestamp"):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _tool_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 64
        or value != value.strip()
        or any(character.isspace() for character in value)
        or not value[0].isalpha()
    ):
        raise ContractValidationError("invalid_tool_name")
    return value


def _arguments(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, tuple):
        raise ContractValidationError("invalid_arguments")
    if len(value) > 16:
        raise ContractValidationError("excessive_arguments")
    seen: set[str] = set()
    normalized: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ContractValidationError("invalid_arguments")
        name = _token(item[0], "argument_name")
        if name in seen:
            raise ContractValidationError("duplicate_argument")
        seen.add(name)
        normalized.append((name, _token(item[1], "argument_value")))
    return tuple(normalized)


def _region_codes(value: object) -> frozenset[str]:
    if type(value) is not frozenset or any(code not in _US_STATE_CODES for code in value):
        raise ContractValidationError("invalid_region_authority")
    return value


def _window_pair(value: object) -> tuple[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, tuple) or len(value) != 2:
        raise ContractValidationError("invalid_historical_window")
    start = _whole_second(value[0], "historical_start_utc")
    end = _whole_second(value[1], "historical_end_utc")
    if start >= end:
        raise ContractValidationError("invalid_historical_window")
    if not isinstance(value[0], str) or not isinstance(value[1], str):
        raise ContractValidationError("invalid_historical_window")
    return (value[0], value[1])


def _wildcard(value: str) -> bool:
    return "*" in value or value.casefold() in _WILDCARDS


def _arn(value: str) -> bool:
    folded = value.casefold()
    return folded.startswith("arn:") or folded.startswith("dynamodb:") or ":table/" in folded


def _unique(reasons: list[MasterRuntimePolicyDenialReason]) -> tuple[MasterRuntimePolicyDenialReason, ...]:
    present = set(reasons)
    return tuple(reason for reason in _REASON_ORDER if reason in present)


def _require_retained_plan(context: MasterPlanningContext, plan: MasterRoutePlan) -> None:
    if plan.execution_authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
    if plan.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
        raise ContractValidationError("request_coverage_not_established")
    if plan.end_to_end_fulfilled is not False:
        raise ContractValidationError("end_to_end_fulfillment_not_granted")
    if plan.master_request_id != context.master_request_id:
        raise ContractValidationError("master_request_mismatch")
    if plan.canonical_user_text != context.canonical_user_text:
        raise ContractValidationError("master_request_mismatch")
    if len(plan.steps) > MAX_ROUTE_STEPS:
        raise ContractValidationError("excessive_plan_size")
    decisions = sum(step.invocation.specialist is MasterSpecialist.DECISION for step in plan.steps)
    if decisions > MAX_DECISION_STEPS:
        raise ContractValidationError("decision_fanout_not_permitted")
    for step in plan.steps:
        if type(step) is not MasterRouteStepPlan:
            raise ContractValidationError("invalid_route_step_plan")
        invocation = step.invocation
        if type(invocation) is not MasterInvocationAuthorization:
            raise ContractValidationError("invalid_invocation_authorization")
        if invocation.master_request_id != context.master_request_id:
            raise ContractValidationError("master_request_mismatch")
        if invocation.correlation_id != context.correlation_id:
            raise ContractValidationError("correlation_mismatch")
        if invocation.application_request_time_utc != context.application_request_time_utc:
            raise ContractValidationError("application_time_mismatch")
        if step.hint_trust is not MasterHintTrust.UNTRUSTED:
            raise ContractValidationError("selection_hints_not_trusted")
        if step.target_authorization_granted is not False:
            raise ContractValidationError("target_authorization_not_granted")


class RuntimePolicySession:
    """Retained plan membership. The session id is a label, not a principal.

    Only ``retain_runtime_policy_session`` mints one. A copied id, a
    field-equal plan, or a deserialized plan is not this session.
    Membership is not authentication and not a grant.
    """

    __slots__ = ("_session_id", "_membership", "_context", "_plan")

    def __init__(
        self,
        session_id: str,
        membership: object,
        context: MasterPlanningContext,
        plan: MasterRoutePlan,
    ) -> None:
        if membership is not _MEMBERSHIP:
            raise ContractValidationError("session_not_retained")
        if type(context) is not MasterPlanningContext:
            raise ContractValidationError("invalid_planning_context")
        if type(plan) is not MasterRoutePlan:
            raise ContractValidationError("invalid_route_plan")
        _token(session_id, "planning_session_id")
        _require_retained_plan(context, plan)
        self._session_id = session_id
        self._membership = membership
        self._context = context
        self._plan = plan

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def planning_context(self) -> MasterPlanningContext:
        return self._context

    @property
    def route_plan(self) -> MasterRoutePlan:
        return self._plan


@dataclass(frozen=True)
class ServerPolicyInputs:
    """Server-owned policy inputs. Sealing them is not host authentication.

    The reviewed host is expected to pass clocks, budget consumption,
    cancellation, region codes, the read it intends to check, and the
    Decision target obligation it computed. This process cannot prove
    the Python caller is that host. A caller boolean is not a field.
    """

    evaluation_utc: str
    accepted_at_utc: str
    deadline_utc: str
    executions_used: int
    model_turns_used: int
    tool_calls_used: int
    cancelled: bool
    region_codes: frozenset[str]
    tool_name: str
    resource_id: str
    operation: ReadOperation
    arguments: tuple[tuple[str, str], ...]
    target_kind: TargetObligationKind
    target_id: str | None
    idempotency_key: str
    _seal: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _SERVER_SEAL:
            raise ContractValidationError("invalid_server_policy_inputs")
        _whole_second(self.evaluation_utc, "evaluation_utc")
        _whole_second(self.accepted_at_utc, "accepted_at_utc")
        _whole_second(self.deadline_utc, "deadline_utc")
        _count(self.executions_used, "executions_used")
        _count(self.model_turns_used, "model_turns_used")
        _count(self.tool_calls_used, "tool_calls_used")
        if type(self.cancelled) is not bool:
            raise ContractValidationError("invalid_cancellation")
        _region_codes(self.region_codes)
        _tool_name(self.tool_name)
        _token(self.resource_id, "resource_id")
        if not isinstance(self.operation, ReadOperation):
            raise ContractValidationError("invalid_operation")
        _arguments(self.arguments)
        if not isinstance(self.target_kind, TargetObligationKind):
            raise ContractValidationError("invalid_target_obligation")
        _optional_token(self.target_id, "target_id")
        _token(self.idempotency_key, "idempotency_key")


@dataclass(frozen=True)
class MasterRuntimePolicyEnvelope:
    """Descriptive alignment envelope. Not an authentication token.

    Untrusted values are hints. ``authorized``, ``caller_policy_decision``,
    ``claimed_test_mode``, and ``serialized_decision`` are never authority.
    """

    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    principal_ref: str | None
    policy_revision: str
    resource_scope: str
    region_scope: str | None
    historical_window: tuple[str, str] | None
    decision_target_scope: str | None
    accepted_at_utc: str
    deadline_utc: str
    idempotency_key: str
    max_model_turns: int
    max_tool_calls: int
    correlation_id: str
    authorized: bool | None = None
    caller_policy_decision: str | None = None
    claimed_test_mode: bool | None = None
    serialized_decision: object | None = None

    def __post_init__(self) -> None:
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
        _optional_token(self.principal_ref, "principal_ref")
        _token(self.policy_revision, "policy_revision")
        _token(self.resource_scope, "resource_scope")
        _optional_prose(self.region_scope, "region_scope")
        object.__setattr__(self, "historical_window", _window_pair(self.historical_window))
        _optional_prose(self.decision_target_scope, "decision_target_scope")
        _whole_second(self.accepted_at_utc, "accepted_at_utc")
        _whole_second(self.deadline_utc, "deadline_utc")
        _token(self.idempotency_key, "idempotency_key")
        _count(self.max_model_turns, "max_model_turns")
        _count(self.max_tool_calls, "max_tool_calls")
        _token(self.correlation_id, "correlation_id")
        _optional_bool(self.authorized, "authorized")
        _optional_token(self.caller_policy_decision, "caller_policy_decision")
        _optional_bool(self.claimed_test_mode, "claimed_test_mode")


def _fingerprint(
    *,
    decision: MasterRuntimePolicyDecision,
    denial_reasons: tuple[MasterRuntimePolicyDenialReason, ...],
    unresolved_gates: tuple[MasterRouteGate, ...],
    authority_origin: PolicyAuthorityOrigin,
    master_request_id: str,
    planning_session_id: str,
    step_id: str,
    invocation_id: str,
    specialist: MasterSpecialist,
    purpose: MasterPurpose,
    capability: AgentCapability,
    principal_ref: str | None,
    tool_name: str,
    resource_scope: str,
    operation: ReadOperation,
    region_scope: tuple[str, ...],
    historical_window: tuple[str, str] | None,
    decision_target_scope: str | None,
    accepted_at_utc: str,
    deadline_utc: str,
    evaluation_utc: str,
    idempotency_key: str,
    max_model_turns: int,
    max_tool_calls: int,
    correlation_id: str,
    membership_established: bool,
) -> str:
    window = "" if historical_window is None else f"{historical_window[0]}/{historical_window[1]}"
    lines = (
        POLICY_REVISION,
        decision.value,
        authority_origin.value,
        ",".join(reason.value for reason in denial_reasons),
        ",".join(gate.value for gate in unresolved_gates),
        master_request_id,
        planning_session_id,
        step_id,
        invocation_id,
        specialist.value,
        purpose.value,
        capability.value,
        principal_ref or "",
        tool_name,
        resource_scope,
        operation.value,
        ",".join(region_scope),
        window,
        decision_target_scope or "",
        accepted_at_utc,
        deadline_utc,
        evaluation_utc,
        idempotency_key,
        str(max_model_turns),
        str(max_tool_calls),
        correlation_id,
        "member" if membership_established else "foreign",
    )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MasterRuntimePolicyEvaluation:
    """ALLOW or DENY. Reading it cannot run a specialist or issue a permit.

    ``policy_fingerprint`` is a digest of this decision and
    ``policy_revision``. It is not a credential. A copied digest is not
    this object. ``execution_permitted`` and the grant flags cannot be
    true. ``production_authority`` stays denied.
    """

    decision: MasterRuntimePolicyDecision
    denial_reasons: tuple[MasterRuntimePolicyDenialReason, ...]
    unresolved_gates: tuple[MasterRouteGate, ...]
    authority_origin: PolicyAuthorityOrigin
    policy_fingerprint: str
    evaluation_id: str
    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    principal_ref: str | None
    tool_name: str
    resource_scope: str
    operation: ReadOperation
    region_scope: tuple[str, ...]
    historical_window: tuple[str, str] | None
    decision_target_scope: str | None
    accepted_at_utc: str
    deadline_utc: str
    evaluation_utc: str
    idempotency_key: str
    max_model_turns: int
    max_tool_calls: int
    correlation_id: str
    membership_established: bool
    production_principal_established: bool = False
    execution_permitted: bool = False
    dispatch_permitted: bool = False
    specialist_executed: bool = False
    grant_issued: bool = False
    permit_issued: bool = False
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    tool_execution_recheck_required: bool = True
    limitations: tuple[MasterRuntimePolicyLimitation, ...] = _LIMITATIONS
    schema_version: str = MASTER_RUNTIME_POLICY_SCHEMA_VERSION
    policy_revision: str = POLICY_REVISION
    _origin_seal: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_closed_switches()
        if self.schema_version != MASTER_RUNTIME_POLICY_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if self.policy_revision != POLICY_REVISION:
            raise ContractValidationError("policy_revision_mismatch")
        if not isinstance(self.decision, MasterRuntimePolicyDecision):
            raise ContractValidationError("invalid_policy_decision")
        if self.decision not in {MasterRuntimePolicyDecision.ALLOW, MasterRuntimePolicyDecision.DENY}:
            raise ContractValidationError("invalid_policy_decision")
        if not isinstance(self.authority_origin, PolicyAuthorityOrigin):
            raise ContractValidationError("invalid_authority_origin")
        if self.authority_origin is PolicyAuthorityOrigin.HYPOTHETICAL_TEST_HOST:
            if self._origin_seal is not _HYPOTHETICAL_ORIGIN:
                raise ContractValidationError("hypothetical_origin_not_production_authority")
        elif self.authority_origin is PolicyAuthorityOrigin.NOT_ESTABLISHED:
            if self._origin_seal is not _PRODUCTION_ORIGIN:
                raise ContractValidationError("principal_not_host_authenticated")
        else:
            raise ContractValidationError("host_authenticated_principal_not_available")
        if not isinstance(self.denial_reasons, tuple) or any(
            not isinstance(reason, MasterRuntimePolicyDenialReason) for reason in self.denial_reasons
        ):
            raise ContractValidationError("invalid_denial_reasons")
        canonical = tuple(reason for reason in _REASON_ORDER if reason in set(self.denial_reasons))
        if self.denial_reasons != canonical or len(self.denial_reasons) != len(set(self.denial_reasons)):
            raise ContractValidationError("invalid_denial_reasons")
        if not isinstance(self.unresolved_gates, tuple) or any(
            not isinstance(gate, MasterRouteGate) or gate is MasterRouteGate.NOT_REQUIRED
            for gate in self.unresolved_gates
        ):
            raise ContractValidationError("invalid_unresolved_gates")
        if self.unresolved_gates and (
            MasterRuntimePolicyDenialReason.UNRESOLVED_ROUTE_GATE not in self.denial_reasons
        ):
            raise ContractValidationError("unresolved_route_gate_not_reported")
        if type(self.membership_established) is not bool:
            raise ContractValidationError("invalid_membership")
        if (
            self.production_principal_established is not False
            or self.execution_permitted is not False
            or self.dispatch_permitted is not False
            or self.specialist_executed is not False
            or self.grant_issued is not False
            or self.permit_issued is not False
        ):
            raise ContractValidationError("policy_evaluation_is_not_a_permit")
        if self.production_authority is not MasterProductionAuthority.DENIED:
            raise ContractValidationError("production_authority_not_issued")
        if self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            raise ContractValidationError("source_authenticity_not_established")
        if self.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
            raise ContractValidationError("request_coverage_not_established")
        if self.execution_authority is not MasterRouteExecutionAuthority.DENIED:
            raise ContractValidationError("execution_authority_not_granted")
        if self.tool_execution_recheck_required is not True:
            raise ContractValidationError("tool_execution_recheck_required")
        if self.limitations != _LIMITATIONS:
            raise ContractValidationError("policy_limitation_not_reported")
        if not isinstance(self.specialist, MasterSpecialist) or not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_policy_evaluation")
        if not isinstance(self.capability, AgentCapability) or not isinstance(self.operation, ReadOperation):
            raise ContractValidationError("invalid_policy_evaluation")
        _executions, turns, calls = server_read_budget(self.specialist)
        if self.max_model_turns != turns or self.max_tool_calls != calls or _executions < 1:
            raise ContractValidationError("caller_budget_not_authority")
        if self._origin_seal is _PRODUCTION_ORIGIN:
            if self.decision is not MasterRuntimePolicyDecision.DENY:
                raise ContractValidationError("production_policy_result_must_deny")
            if MasterRuntimePolicyDenialReason.PRINCIPAL_NOT_HOST_AUTHENTICATED not in self.denial_reasons:
                raise ContractValidationError("principal_not_host_authenticated")
        if self.decision is MasterRuntimePolicyDecision.ALLOW:
            if self.denial_reasons or self.unresolved_gates or self.membership_established is not True:
                raise ContractValidationError("production_policy_result_must_deny")
            if self._origin_seal is not _HYPOTHETICAL_ORIGIN:
                raise ContractValidationError("production_policy_result_must_deny")
            if self.operation is ReadOperation.SCAN or _wildcard(self.resource_scope):
                raise ContractValidationError("scan_not_permitted")
        elif not self.denial_reasons:
            raise ContractValidationError("invalid_denial_reasons")
        digest = _fingerprint(
            decision=self.decision,
            denial_reasons=self.denial_reasons,
            unresolved_gates=self.unresolved_gates,
            authority_origin=self.authority_origin,
            master_request_id=self.master_request_id,
            planning_session_id=self.planning_session_id,
            step_id=self.step_id,
            invocation_id=self.invocation_id,
            specialist=self.specialist,
            purpose=self.purpose,
            capability=self.capability,
            principal_ref=self.principal_ref,
            tool_name=self.tool_name,
            resource_scope=self.resource_scope,
            operation=self.operation,
            region_scope=self.region_scope,
            historical_window=self.historical_window,
            decision_target_scope=self.decision_target_scope,
            accepted_at_utc=self.accepted_at_utc,
            deadline_utc=self.deadline_utc,
            evaluation_utc=self.evaluation_utc,
            idempotency_key=self.idempotency_key,
            max_model_turns=self.max_model_turns,
            max_tool_calls=self.max_tool_calls,
            correlation_id=self.correlation_id,
            membership_established=self.membership_established,
        )
        if self.policy_fingerprint != digest or self.evaluation_id != digest:
            raise ContractValidationError("policy_fingerprint_mismatch")

    def allows_specialist_execution(self) -> bool:
        """Always false. An ALLOW decision is not dispatch permission."""

        return False

    def __bool__(self) -> bool:
        raise ContractValidationError("policy_evaluation_is_not_a_permit")


def retain_runtime_policy_session(
    context: MasterPlanningContext,
    plan: MasterRoutePlan,
) -> RuntimePolicySession:
    """Retain the original planning objects for membership checks.

    The session id is minted here. Retention does not authenticate a
    principal and does not authorize the plan. ``plan.execution_authority``
    must already be denied.
    """

    _require_closed_switches()
    if type(context) is not MasterPlanningContext:
        raise ContractValidationError("invalid_planning_context")
    if type(plan) is not MasterRoutePlan:
        raise ContractValidationError("invalid_route_plan")
    return RuntimePolicySession(secrets.token_hex(16), _MEMBERSHIP, context, plan)


def seal_server_policy_inputs(
    *,
    evaluation_utc: str,
    accepted_at_utc: str,
    deadline_utc: str,
    executions_used: int = 0,
    model_turns_used: int = 0,
    tool_calls_used: int = 0,
    cancelled: bool = False,
    region_codes: frozenset[str] | None = None,
    tool_name: str,
    resource_id: str,
    operation: ReadOperation,
    arguments: tuple[tuple[str, str], ...] = (),
    target_kind: TargetObligationKind = TargetObligationKind.NOT_APPLICABLE,
    target_id: str | None = None,
    idempotency_key: str,
) -> ServerPolicyInputs:
    """Seal server-owned inputs. This does not prove the caller is the host."""

    _require_closed_switches()
    return ServerPolicyInputs(
        evaluation_utc=evaluation_utc,
        accepted_at_utc=accepted_at_utc,
        deadline_utc=deadline_utc,
        executions_used=executions_used,
        model_turns_used=model_turns_used,
        tool_calls_used=tool_calls_used,
        cancelled=cancelled,
        region_codes=frozenset() if region_codes is None else region_codes,
        tool_name=tool_name,
        resource_id=resource_id,
        operation=operation,
        arguments=arguments,
        target_kind=target_kind,
        target_id=target_id,
        idempotency_key=idempotency_key,
        _seal=_SERVER_SEAL,
    )


def evaluate_master_runtime_policy(
    session: RuntimePolicySession,
    plan: MasterRoutePlan,
    step: MasterRouteStepPlan,
    invocation: MasterInvocationAuthorization,
    server: ServerPolicyInputs,
    envelope: MasterRuntimePolicyEnvelope,
) -> MasterRuntimePolicyEvaluation:
    """Evaluate one invocation for production. The result is DENY.

    There is no host-authenticated principal to pass in. ``authorized`` is
    not a parameter. Envelope spoof fields are recorded as denials. This
    function does not call the hypothetical test harness, and a test-mode
    environment variable is not read.
    """

    return _evaluate(
        session,
        plan,
        step,
        invocation,
        server,
        envelope,
        origin_seal=_PRODUCTION_ORIGIN,
        principal_ref=None,
    )


def _evaluate_hypothetical_runtime_policy(
    session: RuntimePolicySession,
    plan: MasterRoutePlan,
    step: MasterRouteStepPlan,
    invocation: MasterInvocationAuthorization,
    server: ServerPolicyInputs,
    envelope: MasterRuntimePolicyEnvelope,
    *,
    principal_ref: str,
) -> MasterRuntimePolicyEvaluation:
    """Private test harness. Not production authority and not a permit.

    Production ``evaluate_master_runtime_policy`` does not call this.
    Caller ``authorized=True`` still denies. The returned ALLOW, when
    the other checks pass, is ``HYPOTHETICAL_TEST_HOST`` only.
    """

    return _evaluate(
        session,
        plan,
        step,
        invocation,
        server,
        envelope,
        origin_seal=_HYPOTHETICAL_ORIGIN,
        principal_ref=_token(principal_ref, "principal_ref"),
    )


def promote_policy_evaluation_to_permit(evaluation: MasterRuntimePolicyEvaluation) -> None:
    """Refuse to turn an evaluation, including ALLOW, into a permit."""

    if type(evaluation) is not MasterRuntimePolicyEvaluation:
        raise ContractValidationError("invalid_policy_evaluation")
    _require_closed_switches()
    raise ContractValidationError("policy_evaluation_is_not_a_permit")


def _evaluate(
    session: RuntimePolicySession,
    plan: MasterRoutePlan,
    step: MasterRouteStepPlan,
    invocation: MasterInvocationAuthorization,
    server: ServerPolicyInputs,
    envelope: MasterRuntimePolicyEnvelope,
    *,
    origin_seal: object,
    principal_ref: str | None,
) -> MasterRuntimePolicyEvaluation:
    _require_closed_switches()
    if origin_seal is _HYPOTHETICAL_ORIGIN:
        hypothetical = True
        origin = PolicyAuthorityOrigin.HYPOTHETICAL_TEST_HOST
    elif origin_seal is _PRODUCTION_ORIGIN:
        hypothetical = False
        origin = PolicyAuthorityOrigin.NOT_ESTABLISHED
    else:
        raise ContractValidationError("principal_not_host_authenticated")
    if type(session) is not RuntimePolicySession or session._membership is not _MEMBERSHIP:
        raise ContractValidationError("invalid_policy_session")
    if type(plan) is not MasterRoutePlan:
        raise ContractValidationError("invalid_route_plan")
    if type(step) is not MasterRouteStepPlan:
        raise ContractValidationError("invalid_route_step_plan")
    if type(invocation) is not MasterInvocationAuthorization:
        raise ContractValidationError("invalid_invocation_authorization")
    if type(server) is not ServerPolicyInputs or server._seal is not _SERVER_SEAL:
        raise ContractValidationError("invalid_server_policy_inputs")
    if type(envelope) is not MasterRuntimePolicyEnvelope:
        raise ContractValidationError("invalid_policy_envelope")

    reasons: list[MasterRuntimePolicyDenialReason] = []
    reasons.extend(_principal_reasons(hypothetical, principal_ref, envelope, session, step, invocation))
    membership, membership_reasons = _membership(session, plan, step, invocation, envelope)
    reasons.extend(membership_reasons)
    reasons.extend(_pair_reasons(step, envelope))
    unresolved = tuple(gate for gate in step.gates if gate is not MasterRouteGate.NOT_REQUIRED)
    if unresolved:
        reasons.append(MasterRuntimePolicyDenialReason.UNRESOLVED_ROUTE_GATE)
    reasons.extend(_resource_reasons(step, server, envelope))
    reasons.extend(_region_reasons(server, envelope))
    reasons.extend(_window_reasons(step, server, envelope))
    target_denied, reported_target = _target_reasons(step, server, envelope)
    reasons.extend(target_denied)
    reasons.extend(_clock_reasons(session, server, envelope))
    if server.cancelled:
        reasons.append(MasterRuntimePolicyDenialReason.CANCELLED)
    reasons.extend(_budget_reasons(step, server, envelope))
    if envelope.idempotency_key != server.idempotency_key:
        reasons.append(MasterRuntimePolicyDenialReason.IDEMPOTENCY_KEY_MISMATCH)
    if envelope.policy_revision != POLICY_REVISION:
        reasons.append(MasterRuntimePolicyDenialReason.POLICY_REVISION_MISMATCH)

    denial_reasons = _unique(reasons)
    decision = (
        MasterRuntimePolicyDecision.DENY
        if denial_reasons
        else MasterRuntimePolicyDecision.ALLOW
    )
    _executions, turns, calls = server_read_budget(step.invocation.specialist)
    reported_principal = None
    if hypothetical and principal_ref is not None and not _label_rejected(principal_ref, envelope, session, step, invocation):
        reported_principal = principal_ref
    historical_window = None
    if step.historical_start_utc is not None and step.historical_end_utc is not None:
        historical_window = (step.historical_start_utc, step.historical_end_utc)
    reported = {
        "decision": decision,
        "denial_reasons": denial_reasons,
        "unresolved_gates": unresolved,
        "authority_origin": origin,
        "master_request_id": session.planning_context.master_request_id,
        "planning_session_id": session.session_id,
        "step_id": step.step_id,
        "invocation_id": step.invocation.invocation_id,
        "specialist": step.invocation.specialist,
        "purpose": step.invocation.purpose,
        "capability": step.capability,
        "principal_ref": reported_principal,
        "tool_name": server.tool_name,
        "resource_scope": server.resource_id,
        "operation": server.operation,
        "region_scope": tuple(sorted(server.region_codes)),
        "historical_window": historical_window,
        "decision_target_scope": reported_target,
        "accepted_at_utc": server.accepted_at_utc,
        "deadline_utc": server.deadline_utc,
        "evaluation_utc": server.evaluation_utc,
        "idempotency_key": server.idempotency_key,
        "max_model_turns": turns,
        "max_tool_calls": calls,
        "correlation_id": session.planning_context.correlation_id,
        "membership_established": membership,
    }
    digest = _fingerprint(**reported)
    return MasterRuntimePolicyEvaluation(
        **reported,
        policy_fingerprint=digest,
        evaluation_id=digest,
        _origin_seal=origin_seal,
    )


def _collides(label: str | None, session: RuntimePolicySession, step: MasterRouteStepPlan, invocation: MasterInvocationAuthorization) -> bool:
    if label is None:
        return False
    return label in {
        session.session_id,
        session.planning_context.master_request_id,
        session.planning_context.correlation_id,
        step.step_id,
        invocation.invocation_id,
        invocation.master_request_id,
        invocation.correlation_id,
        invocation.specialist_request_id,
    }


def _label_rejected(
    principal_ref: str | None,
    envelope: MasterRuntimePolicyEnvelope,
    session: RuntimePolicySession,
    step: MasterRouteStepPlan,
    invocation: MasterInvocationAuthorization,
) -> bool:
    if _collides(principal_ref, session, step, invocation) or _collides(envelope.principal_ref, session, step, invocation):
        return True
    if envelope.principal_ref is not None and envelope.principal_ref != principal_ref:
        return True
    return False


def _principal_reasons(
    hypothetical: bool,
    principal_ref: str | None,
    envelope: MasterRuntimePolicyEnvelope,
    session: RuntimePolicySession,
    step: MasterRouteStepPlan,
    invocation: MasterInvocationAuthorization,
) -> tuple[MasterRuntimePolicyDenialReason, ...]:
    reasons: list[MasterRuntimePolicyDenialReason] = []
    if not hypothetical:
        reasons.append(MasterRuntimePolicyDenialReason.PRINCIPAL_NOT_HOST_AUTHENTICATED)
        if envelope.principal_ref is not None:
            reasons.append(MasterRuntimePolicyDenialReason.PRINCIPAL_LABEL_NOT_AUTHENTICATION)
    elif _label_rejected(principal_ref, envelope, session, step, invocation):
        reasons.append(MasterRuntimePolicyDenialReason.PRINCIPAL_LABEL_NOT_AUTHENTICATION)
    if envelope.authorized is True:
        reasons.append(MasterRuntimePolicyDenialReason.CALLER_AUTHORIZED_FLAG_NOT_AUTHORITY)
    decision = envelope.caller_policy_decision
    if decision is not None:
        reasons.append(MasterRuntimePolicyDenialReason.CALLER_PROOF_NOT_AUTHORITY)
        if decision.upper() in _ALLOW_WORDS:
            reasons.append(MasterRuntimePolicyDenialReason.CALLER_AUTHORIZED_FLAG_NOT_AUTHORITY)
    if envelope.serialized_decision is not None:
        reasons.append(MasterRuntimePolicyDenialReason.SERIALIZED_DECISION_NOT_AUTHORITY)
    if envelope.claimed_test_mode is True:
        reasons.append(MasterRuntimePolicyDenialReason.CLAIMED_TEST_MODE_NOT_AUTHORITY)
    return tuple(reasons)


def _membership(
    session: RuntimePolicySession,
    plan: MasterRoutePlan,
    step: MasterRouteStepPlan,
    invocation: MasterInvocationAuthorization,
    envelope: MasterRuntimePolicyEnvelope,
) -> tuple[bool, tuple[MasterRuntimePolicyDenialReason, ...]]:
    reasons: list[MasterRuntimePolicyDenialReason] = []
    plan_member = session._plan is plan
    if not plan_member:
        reasons.append(MasterRuntimePolicyDenialReason.CROSS_SESSION)
        if plan.master_request_id == session._plan.master_request_id:
            reasons.append(MasterRuntimePolicyDenialReason.STRING_IDENTITY_NOT_MEMBERSHIP)
    step_member = plan_member and any(item is step for item in plan.steps)
    if not step_member:
        reasons.append(MasterRuntimePolicyDenialReason.STEP_NOT_MEMBER)
        if plan_member and any(item.step_id == step.step_id for item in plan.steps):
            reasons.append(MasterRuntimePolicyDenialReason.STRING_IDENTITY_NOT_MEMBERSHIP)
    invocation_member = step_member and step.invocation is invocation
    if not invocation_member:
        reasons.append(MasterRuntimePolicyDenialReason.INVOCATION_NOT_MEMBER)
        if step.invocation.invocation_id == invocation.invocation_id and step.invocation is not invocation:
            reasons.append(MasterRuntimePolicyDenialReason.STRING_IDENTITY_NOT_MEMBERSHIP)
    context = session.planning_context
    strings_match = (
        envelope.planning_session_id == session.session_id
        and envelope.master_request_id == context.master_request_id
        and envelope.step_id == step.step_id
        and envelope.invocation_id == invocation.invocation_id
        and envelope.correlation_id == context.correlation_id
        and invocation.master_request_id == context.master_request_id
        and invocation.correlation_id == context.correlation_id
        and plan.master_request_id == context.master_request_id
    )
    if not strings_match:
        reasons.append(MasterRuntimePolicyDenialReason.STRING_IDENTITY_NOT_MEMBERSHIP)
    if envelope.planning_session_id != session.session_id:
        reasons.append(MasterRuntimePolicyDenialReason.CROSS_SESSION)
    established = plan_member and step_member and invocation_member and strings_match
    return established, tuple(reasons)


def _pair_reasons(
    step: MasterRouteStepPlan,
    envelope: MasterRuntimePolicyEnvelope,
) -> tuple[MasterRuntimePolicyDenialReason, ...]:
    reasons: list[MasterRuntimePolicyDenialReason] = []
    invocation = step.invocation
    allowed = SPECIALIST_CAPABILITY_PAIRS.get((invocation.specialist, invocation.purpose))
    capability = step.capability
    if capability in PROHIBITED_V1_CAPABILITIES:
        reasons.append(MasterRuntimePolicyDenialReason.PROHIBITED_CAPABILITY)
    elif allowed is None or capability not in allowed or capability not in ALLOWED_V1_CAPABILITIES:
        if capability in ALLOWED_V1_CAPABILITIES:
            reasons.append(MasterRuntimePolicyDenialReason.CAPABILITY_PAIR_MISMATCH)
        else:
            reasons.append(MasterRuntimePolicyDenialReason.UNSUPPORTED_CAPABILITY)
    if envelope.specialist is not invocation.specialist:
        reasons.append(MasterRuntimePolicyDenialReason.WRONG_SPECIALIST)
    if envelope.purpose is not invocation.purpose:
        reasons.append(MasterRuntimePolicyDenialReason.WRONG_PURPOSE)
    if envelope.capability is not capability:
        if envelope.capability in PROHIBITED_V1_CAPABILITIES:
            reasons.append(MasterRuntimePolicyDenialReason.PROHIBITED_CAPABILITY)
        else:
            reasons.append(MasterRuntimePolicyDenialReason.CAPABILITY_PAIR_MISMATCH)
    return tuple(reasons)


def _resource_reasons(
    step: MasterRouteStepPlan,
    server: ServerPolicyInputs,
    envelope: MasterRuntimePolicyEnvelope,
) -> tuple[MasterRuntimePolicyDenialReason, ...]:
    reasons: list[MasterRuntimePolicyDenialReason] = []
    specialist = step.invocation.specialist
    tool_name = server.tool_name
    resource_id = server.resource_id
    operation = server.operation
    if envelope.resource_scope != resource_id:
        reasons.append(MasterRuntimePolicyDenialReason.UNKNOWN_RESOURCE)
    if _wildcard(resource_id) or any(_wildcard(value) for _name, value in server.arguments):
        reasons.append(MasterRuntimePolicyDenialReason.WILDCARD_RESOURCE)
    if _arn(resource_id) or any(_arn(value) for _name, value in server.arguments):
        reasons.append(MasterRuntimePolicyDenialReason.ARBITRARY_TABLE_ARN)
    if operation is ReadOperation.SCAN or tool_name.startswith("scan_"):
        reasons.append(MasterRuntimePolicyDenialReason.SCAN_NOT_PERMITTED)
    owners = [owner for owner in MasterSpecialist if tool_name in server_read_tools(owner)]
    if specialist not in owners:
        reasons.append(MasterRuntimePolicyDenialReason.UNSUPPORTED_TOOL)
        if owners:
            reasons.append(MasterRuntimePolicyDenialReason.CROSS_SPECIALIST_RESOURCE)
        elif _resource_owned_by_other(specialist, resource_id):
            reasons.append(MasterRuntimePolicyDenialReason.CROSS_SPECIALIST_RESOURCE)
        elif not _wildcard(resource_id) and not _arn(resource_id) and operation is not ReadOperation.SCAN:
            reasons.append(MasterRuntimePolicyDenialReason.UNKNOWN_RESOURCE)
        return tuple(reasons)
    allowed_resources = server_read_resources(specialist, tool_name)
    allowed_arguments = server_read_argument_names(specialist, tool_name)
    if not allowed_resources and not allowed_arguments:
        reasons.append(MasterRuntimePolicyDenialReason.POPULATION_SCOPE_NOT_ESTABLISHED)
    arguments = dict(server.arguments)
    unknown = [name for name in arguments if name not in allowed_arguments]
    if unknown:
        reasons.append(MasterRuntimePolicyDenialReason.ARGUMENT_NOT_ALLOWLISTED)
    if _missing_selector(tool_name, allowed_arguments, arguments):
        reasons.append(MasterRuntimePolicyDenialReason.ARGUMENT_MISSING)
    if (resource_id, operation) not in allowed_resources:
        if _wildcard(resource_id) or _arn(resource_id) or operation is ReadOperation.SCAN:
            pass
        elif any(resource == resource_id for resource, _operation in allowed_resources):
            reasons.append(MasterRuntimePolicyDenialReason.OPERATION_NOT_ALLOWLISTED)
        elif _resource_owned_by_other(specialist, resource_id):
            reasons.append(MasterRuntimePolicyDenialReason.CROSS_SPECIALIST_RESOURCE)
        elif not (not allowed_resources and not allowed_arguments):
            reasons.append(MasterRuntimePolicyDenialReason.UNKNOWN_RESOURCE)
    return tuple(reasons)


def _resource_owned_by_other(specialist: MasterSpecialist, resource_id: str) -> bool:
    owners = []
    for owner in MasterSpecialist:
        for tool_name in server_read_tools(owner):
            if any(resource == resource_id for resource, _operation in server_read_resources(owner, tool_name)):
                owners.append(owner)
                break
    return bool(owners) and specialist not in owners


def _missing_selector(tool_name: str, allowed: frozenset[str], arguments: dict[str, str]) -> bool:
    if "start_utc" in allowed and ("start_utc" not in arguments or "end_utc" not in arguments):
        return True
    if tool_name.startswith("summarize_"):
        return False
    if tool_name.startswith("get_") or tool_name.startswith("find_"):
        return any(name not in arguments for name in allowed)
    if tool_name.startswith("list_"):
        return "aircraft_id" not in arguments and "hazard_id" not in arguments
    if tool_name.startswith("search_"):
        return not any(name in arguments for name in allowed)
    return False


def _region_reasons(
    server: ServerPolicyInputs,
    envelope: MasterRuntimePolicyEnvelope,
) -> tuple[MasterRuntimePolicyDenialReason, ...]:
    reasons: list[MasterRuntimePolicyDenialReason] = []
    sealed = server.region_codes
    claimed = envelope.region_scope
    argument = dict(server.arguments).get("region")
    if claimed is not None and (claimed not in _US_STATE_CODES or claimed not in sealed):
        reasons.append(MasterRuntimePolicyDenialReason.ARBITRARY_REGION_TEXT)
    if argument is not None and argument not in _US_STATE_CODES:
        reasons.append(MasterRuntimePolicyDenialReason.ARBITRARY_REGION_TEXT)
    if (claimed is not None or argument is not None) and not sealed:
        reasons.append(MasterRuntimePolicyDenialReason.REGION_AUTHORITY_NOT_ESTABLISHED)
    if isinstance(argument, str) and argument in _US_STATE_CODES and argument not in sealed:
        reasons.append(MasterRuntimePolicyDenialReason.REGION_NOT_IN_AUTHORITY)
    if claimed is not None and claimed in _US_STATE_CODES and sealed and claimed not in sealed:
        reasons.append(MasterRuntimePolicyDenialReason.REGION_NOT_IN_AUTHORITY)
    return tuple(reasons)


def _window_problem(start: object, end: object, evaluation: datetime) -> MasterRuntimePolicyDenialReason | None:
    parsed_start = _parse_whole_second(start)
    parsed_end = _parse_whole_second(end)
    if parsed_start is None or parsed_end is None or parsed_start >= parsed_end or parsed_end > evaluation:
        return MasterRuntimePolicyDenialReason.HISTORICAL_WINDOW_INVALID
    if parsed_end - parsed_start > MAX_HISTORICAL_WINDOW:
        return MasterRuntimePolicyDenialReason.HISTORICAL_WINDOW_EXCEEDED
    return None


def _window_reasons(
    step: MasterRouteStepPlan,
    server: ServerPolicyInputs,
    envelope: MasterRuntimePolicyEnvelope,
) -> tuple[MasterRuntimePolicyDenialReason, ...]:
    reasons: list[MasterRuntimePolicyDenialReason] = []
    historical = step.invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    arguments = dict(server.arguments)
    claimed: list[tuple[object, object]] = []
    if "start_utc" in arguments or "end_utc" in arguments:
        claimed.append((arguments.get("start_utc"), arguments.get("end_utc")))
    if envelope.historical_window is not None:
        claimed.append(envelope.historical_window)
    step_window = (step.historical_start_utc, step.historical_end_utc)
    if not historical:
        if step_window != (None, None) or claimed:
            reasons.append(MasterRuntimePolicyDenialReason.HISTORICAL_WINDOW_INVALID)
        return tuple(reasons)
    evaluation = _whole_second(server.evaluation_utc, "evaluation_utc")
    step_problem = _window_problem(step_window[0], step_window[1], evaluation)
    if step_problem is not None:
        reasons.append(step_problem)
    if "start_utc" not in arguments or "end_utc" not in arguments or envelope.historical_window is None:
        reasons.append(MasterRuntimePolicyDenialReason.HISTORICAL_WINDOW_MISMATCH)
    for start, end in claimed:
        problem = _window_problem(start, end, evaluation)
        if problem is not None:
            reasons.append(problem)
        elif (start, end) != step_window:
            reasons.append(MasterRuntimePolicyDenialReason.HISTORICAL_WINDOW_MISMATCH)
    return tuple(reasons)


def _target_reasons(
    step: MasterRouteStepPlan,
    server: ServerPolicyInputs,
    envelope: MasterRuntimePolicyEnvelope,
) -> tuple[tuple[MasterRuntimePolicyDenialReason, ...], str | None]:
    reasons: list[MasterRuntimePolicyDenialReason] = []
    kind = server.target_kind
    target_id = server.target_id
    claimed = envelope.decision_target_scope
    if step.invocation.specialist is not MasterSpecialist.DECISION:
        if kind is not TargetObligationKind.NOT_APPLICABLE or target_id is not None or claimed is not None:
            reasons.append(MasterRuntimePolicyDenialReason.ARBITRARY_DECISION_TARGET)
        return tuple(reasons), None
    expected_aircraft = step.invocation.expected_aircraft_id
    expected_recommendation = step.invocation.expected_recommendation_id
    if kind is TargetObligationKind.UNBOUND or target_id is None or kind is TargetObligationKind.NOT_APPLICABLE:
        reasons.append(MasterRuntimePolicyDenialReason.UNBOUND_TARGET_ID)
        reasons.append(MasterRuntimePolicyDenialReason.TARGET_NOT_AUTHORIZED)
        return tuple(reasons), None
    arbitrary = _wildcard(target_id) or _arn(target_id)
    if claimed is not None and (_wildcard(claimed) or _arn(claimed) or claimed != target_id):
        arbitrary = True
    matched = False
    if kind is TargetObligationKind.BOUND_AIRCRAFT:
        matched = (
            not arbitrary
            and expected_aircraft is not None
            and target_id == expected_aircraft
            and expected_recommendation is None
            and claimed in (None, target_id)
        )
    elif kind is TargetObligationKind.BOUND_RECOMMENDATION:
        matched = (
            not arbitrary
            and expected_recommendation is not None
            and target_id == expected_recommendation
            and expected_aircraft is None
            and claimed in (None, target_id)
        )
    if not matched:
        reasons.append(MasterRuntimePolicyDenialReason.ARBITRARY_DECISION_TARGET)
        reasons.append(MasterRuntimePolicyDenialReason.TARGET_NOT_AUTHORIZED)
        return tuple(reasons), None
    return (), target_id


def _clock_reasons(
    session: RuntimePolicySession,
    server: ServerPolicyInputs,
    envelope: MasterRuntimePolicyEnvelope,
) -> tuple[MasterRuntimePolicyDenialReason, ...]:
    reasons: list[MasterRuntimePolicyDenialReason] = []
    application = _parse_whole_second(session.planning_context.application_request_time_utc)
    accepted = _parse_whole_second(server.accepted_at_utc)
    evaluation = _parse_whole_second(server.evaluation_utc)
    deadline = _parse_whole_second(server.deadline_utc)
    if (
        application is None
        or accepted is None
        or evaluation is None
        or accepted < application
        or evaluation < application
        or accepted > evaluation
    ):
        reasons.append(MasterRuntimePolicyDenialReason.STALE_OR_WRONG_CLOCK)
    if deadline is None or evaluation is None or deadline <= evaluation or (
        application is not None and deadline <= application
    ):
        reasons.append(MasterRuntimePolicyDenialReason.DEADLINE_EXPIRED)
    if envelope.accepted_at_utc != server.accepted_at_utc or envelope.deadline_utc != server.deadline_utc:
        reasons.append(MasterRuntimePolicyDenialReason.CALLER_CLOCK_NOT_AUTHORITY)
    return tuple(reasons)


def _budget_reasons(
    step: MasterRouteStepPlan,
    server: ServerPolicyInputs,
    envelope: MasterRuntimePolicyEnvelope,
) -> tuple[MasterRuntimePolicyDenialReason, ...]:
    reasons: list[MasterRuntimePolicyDenialReason] = []
    executions, turns, calls = server_read_budget(step.invocation.specialist)
    if (
        server.executions_used >= executions
        or server.model_turns_used >= turns
        or server.tool_calls_used >= calls
    ):
        reasons.append(MasterRuntimePolicyDenialReason.BUDGET_EXHAUSTED)
    if envelope.max_model_turns != turns or envelope.max_tool_calls != calls:
        reasons.append(MasterRuntimePolicyDenialReason.CALLER_BUDGET_NOT_AUTHORITY)
    return tuple(reasons)


_assert_vocabulary()


__all__ = [
    "HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE",
    "HOST_GRANT_ISSUER_AVAILABLE",
    "MASTER_RUNTIME_POLICY_SCHEMA_VERSION",
    "POLICY_REVISION",
    "SPECIALIST_CAPABILITY_PAIRS",
    "MasterRuntimePolicyDecision",
    "MasterRuntimePolicyDenialReason",
    "MasterRuntimePolicyEnvelope",
    "MasterRuntimePolicyEvaluation",
    "MasterRuntimePolicyLimitation",
    "PolicyAuthorityOrigin",
    "RuntimePolicySession",
    "ServerPolicyInputs",
    "TargetObligationKind",
    "evaluate_master_runtime_policy",
    "promote_policy_evaluation_to_permit",
    "retain_runtime_policy_session",
    "seal_server_policy_inputs",
]
