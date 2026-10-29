"""Shared host and specialist runtime contract. Schema wilvor.ai.master_specialist_runtime_contract.v1.

Authoritative constraints for every Sprint 3B.2 agent:

1. Work stays on WilvorP/wilvor-infra baseline 5d10c582091997732b97e496940af9fa18afd745,
   Python 3.11. This module is the shared descriptive interface. It is not the
   production specialist invocation.
2. Master planning, execution-scope, policy-readiness, gateway-identity,
   scheduling, native-bridge, evidence-admission, and provenance modules are
   audit and structural contracts. They are not production grants. DENIED and
   NOT_ESTABLISHED stay fail-closed.
3. Trust has to come from the reviewed application host and AWS isolation.
   The event body, an Authorization header, requestContext.authorizer.jwt,
   user or model text, a same-ID object, a test fake, a string, a boolean,
   caller-created proof, a serialized dataclass, an environment variable that
   claims test mode, and a policy-check result do not authenticate the caller.
   API Gateway JWT validation is an edge control. It is not cryptographic
   attestation on every Lambda event. A direct invoke can carry a forged
   gateway-looking event. Production principal and grant issuance stay
   unavailable until the reviewed host establishes them.
4. DESCRIPTIVE, EVALUATED, HOST_ISSUED, CONSUMED, and SOURCE_AUTHENTICATED
   stay distinct. This module can record DESCRIPTIVE and EVALUATED only.
   A descriptive allow is not a usable production grant. No test seam here
   reads production input or an environment flag and turns itself on.
5. The shared envelope below is not an authentication token. Identity values
   are independently validated host and planning context. Untrusted copies are
   hints. The application request clock and the deadline stay separate from
   the source-observation clock and the evaluation clock.
6. Ceilings stay authoritative: MAX_ROUTE_STEPS is 4, MAX_DECISION_STEPS is 1,
   and a Historical interval is at most 7 days. Tool names, argument names,
   resources, and budgets come from master_read_resource_guard. Reads stay
   GET_ITEM and QUERY. There is no DynamoDB Scan, arbitrary Athena SQL, write,
   autonomous aviation action, or flight-control instruction. Bounds and
   cancellation are enforced at actual tool execution, which this module does
   not perform.
7. A read is not aviation evidence until an approved runtime adapter has
   completed a real source operation with source identity, query or record
   identity, source version where available, observation and evaluation time,
   completeness, pagination, truncation, and the effective authorized query
   scope. A missing record, an empty page, a missing impact count, a regional
   name or string match, and a local fake do not create a VerifiedZeroClaim
   or geographically verified SIGMET impact.
8. This module is offline. It does not call the network, AWS, Terraform, or a
   live model, and it does not enable a Lambda handler.
9. Other unmerged Sprint 3B.2 modules are not imported. Integrators follow
   SPRINT3B2_TRUSTED_HOST_INTERFACE.md until this module is merged.
10. Nothing in this file issues a principal, a grant, or evidence, dispatches
    a specialist, changes a native type, or sets production authority.
    Copying every field, or constructing a valid dataclass, does not make a
    production grant.

``accepted_at_utc`` is the application request clock. It maps to
``application_request_time_utc`` on Master invocation and planning records.
``planning_session_id`` maps to ``MasterPlanningSessionRecord.session_id``
and ``MasterSchedulingObservation.session_id``. ``requirement`` maps to
``MasterRequirement``.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Protocol

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    AgentCapability,
    ContractValidationError,
    Evidence,
    QueryExecutionTrace,
    SourceRecord,
    TemporalScope,
    ToolResult,
    _validate_bounded_text,
    _validate_utc,
)
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionSpecialistRequest,
    DecisionSpecialistRunResult,
    DecisionSpecialistRunStatus,
)
from wilvor_ai.live_ops_public_contracts import (
    LiveOpsRunRequest,
    LiveOpsRunResult,
    LiveOpsRunStatus,
    LiveOpsTrustedContext,
)
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
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
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
)
from wilvor_ai.specialist_contracts import (
    HistoricalSpecialistTrustedContext,
    SpecialistRequest,
    SpecialistResult,
    SpecialistStatus,
)
from wilvor_ai.specialist_runtime_contracts import (
    HistoricalSpecialistRunResult,
    SpecialistRunStatus,
)


MASTER_SPECIALIST_RUNTIME_CONTRACT_SCHEMA_VERSION = (
    "wilvor.ai.master_specialist_runtime_contract.v1"
)

PRODUCTION_AUTHORITY_AVAILABLE = False
PRINCIPAL_ISSUANCE_AVAILABLE = False
GRANT_ISSUANCE_AVAILABLE = False
EVIDENCE_ADMISSION_AVAILABLE = False
SPECIALIST_DISPATCH_AVAILABLE = False
NATIVE_TYPES_CHANGED_THIS_SPRINT = False
ACCEPTED_AT_CLOCK_ALIAS = "application_request_time_utc"
PLANNING_SESSION_ID_ALIAS = "session_id"

_HOST_FIELDS = (
    "master_request_id",
    "planning_session_id",
    "step_id",
    "invocation_id",
    "specialist_request_id",
    "correlation_id",
)
_ENVELOPE_HINT_FIELDS = (
    "principal_ref",
    "policy_revision",
    "resource_scope",
    "region_scope",
    "historical_window_start_utc",
    "historical_window_end_utc",
    "decision_target_scope",
)
SHARED_ENVELOPE_FIELDS = _HOST_FIELDS + (
    "specialist",
    "purpose",
    "capability",
    "requirement",
    "accepted_at_utc",
    "deadline_utc",
    "idempotency_key",
    "max_model_turns",
    "max_tool_calls",
    "requested_evaluation_time_utc",
) + _ENVELOPE_HINT_FIELDS

_PAIR_CAPABILITIES = {
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
_DECISION_SCOPE_CHOICES = (
    (TemporalScope.CURRENT,),
    (TemporalScope.PERSISTED,),
    (TemporalScope.CURRENT, TemporalScope.PERSISTED),
)
_SQL_ARGUMENT_NAMES = frozenset({"sql", "statement", "query_string"})


class TrustDistinction(str, Enum):
    """How a record is allowed to be read. This module cannot mint the last three."""

    DESCRIPTIVE = "DESCRIPTIVE"
    EVALUATED = "EVALUATED"
    HOST_ISSUED = "HOST_ISSUED"
    CONSUMED = "CONSUMED"
    SOURCE_AUTHENTICATED = "SOURCE_AUTHENTICATED"


class ClockRole(str, Enum):
    """Independent clocks. Equal text does not make two roles the same clock."""

    APPLICATION_REQUEST = "APPLICATION_REQUEST"
    DEADLINE = "DEADLINE"
    REQUESTED_EVALUATION = "REQUESTED_EVALUATION"
    SOURCE_OBSERVATION = "SOURCE_OBSERVATION"
    EVALUATION = "EVALUATION"
    INDEPENDENT_OBSERVATION = "INDEPENDENT_OBSERVATION"


class RuntimeContractStatus(str, Enum):
    """Statuses a host-contract record may carry. None of them grant execution."""

    DESCRIBED = "DESCRIBED"
    EVALUATED_ALLOW_DESCRIPTIVE = "EVALUATED_ALLOW_DESCRIPTIVE"
    EVALUATED_DENY = "EVALUATED_DENY"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    IDENTITY_MISSING = "IDENTITY_MISSING"
    IDENTITY_AMBIGUOUS = "IDENTITY_AMBIGUOUS"
    REJECTED_FALSE_PROOF = "REJECTED_FALSE_PROOF"


class PolicyDecisionStatus(str, Enum):
    """Evaluated policy wording. ALLOW_DESCRIPTIVE does not issue a grant."""

    ALLOW_DESCRIPTIVE = "ALLOW_DESCRIPTIVE"
    DENY = "DENY"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"


class CancellationObservation(str, Enum):
    """Observed cancellation. Recording it does not run or stop a tool."""

    NOT_CANCELLED = "NOT_CANCELLED"
    CANCELLED = "CANCELLED"
    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"


class ToolExecutionObservationStatus(str, Enum):
    """Described tool state. COMPLETED here is still not admitted evidence."""

    NOT_EXECUTED = "NOT_EXECUTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    REJECTED = "REJECTED"


class NativeResultStage(str, Enum):
    """Which native artifact a description refers to. Not a dispatch."""

    LIVE_OPS_RUN = "LIVE_OPS_RUN"
    HISTORICAL_INTERMEDIATE = "HISTORICAL_INTERMEDIATE"
    HISTORICAL_FINAL = "HISTORICAL_FINAL"
    DECISION_RUN = "DECISION_RUN"


class FalseProofKind(str, Enum):
    """Caller-controlled objects that cannot authorize production."""

    BOOLEAN = "BOOLEAN"
    STRING_CLAIM = "STRING_CLAIM"
    SERIALIZED_DATACLASS = "SERIALIZED_DATACLASS"
    SAME_ID_OBJECT = "SAME_ID_OBJECT"
    POLICY_CHECK = "POLICY_CHECK"
    ENVIRONMENT_CLAIM = "ENVIRONMENT_CLAIM"
    JWT_DICTIONARY = "JWT_DICTIONARY"
    AUTHORIZATION_HEADER = "AUTHORIZATION_HEADER"
    EVENT_BODY = "EVENT_BODY"
    NATIVE_RESULT = "NATIVE_RESULT"
    CALLER_OBJECT = "CALLER_OBJECT"


_CONSTRUCTABLE_POSTURES = frozenset(
    {TrustDistinction.DESCRIPTIVE, TrustDistinction.EVALUATED}
)
ALLOWED_RECORD_STATUSES = frozenset(
    {
        RuntimeContractStatus.DESCRIBED,
        RuntimeContractStatus.EVALUATED_ALLOW_DESCRIPTIVE,
        RuntimeContractStatus.EVALUATED_DENY,
        RuntimeContractStatus.NOT_ESTABLISHED,
    }
)
_POLICY_STATUS = {
    PolicyDecisionStatus.ALLOW_DESCRIPTIVE: RuntimeContractStatus.EVALUATED_ALLOW_DESCRIPTIVE,
    PolicyDecisionStatus.DENY: RuntimeContractStatus.EVALUATED_DENY,
    PolicyDecisionStatus.NOT_ESTABLISHED: RuntimeContractStatus.NOT_ESTABLISHED,
}
CLOCK_ROLES = MappingProxyType(
    {
        "accepted_at_utc": ClockRole.APPLICATION_REQUEST,
        ACCEPTED_AT_CLOCK_ALIAS: ClockRole.APPLICATION_REQUEST,
        "deadline_utc": ClockRole.DEADLINE,
        "requested_evaluation_time_utc": ClockRole.REQUESTED_EVALUATION,
        "source_observation_time_utc": ClockRole.SOURCE_OBSERVATION,
        "evaluation_time_utc": ClockRole.EVALUATION,
        "independent_observation_time_utc": ClockRole.INDEPENDENT_OBSERVATION,
    }
)
DECISION_TOOLS_RUNTIME_FIELDS = (
    "tables",
    "now_epoch",
    "query_timestamp_utc",
    "correlation_id",
)
DECISION_NATIVE_REQUEST_TYPE = "DecisionSpecialistRequest"
HISTORICAL_FINALIZATION_ENTRYPOINT = "finalize_historical_specialist_run"
SOURCE_VERSION_OWNER = "SourceRecord"
QUERY_ID_OWNER = "QueryExecutionTrace"
HOST_PROOF_ISSUER = "unavailable"
REQUIRED_DECISION_HOST_PROOFS = (
    "SOURCE_CURRENTNESS",
    "ALLOWED_TARGET_POPULATION",
    "SEPARATE_PERSISTED_RECOMMENDATION_AUTHORIZATION",
)
_HISTORICAL_GAP_FIELDS = (
    "master_request_id",
    "specialist_request_id",
    "request_id",
    "correlation_id",
    "application_request_time_utc",
    "requested_evaluation_time_utc",
    "unbound_plan_window_start_utc",
    "unbound_plan_window_end_utc",
    "start_utc",
    "end_utc",
    "authenticated_source_evaluation_time_utc",
    "source_version",
    "query_id",
)
PROVENANCE_HANDOFF_LIMITATION = (
    "This handoff does not admit aviation evidence. A described read still "
    "needs an approved runtime adapter and a completed source operation."
)


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _field_names(cls: type) -> frozenset[str]:
    return frozenset(item.name for item in fields(cls))


def _token(value: object, field_name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    errors = _validate_bounded_text(
        value,
        field_name,
        optional=optional,
        max_length=PROVENANCE_TOKEN_MAX_LENGTH,
    )
    if errors:
        raise ContractValidationError(errors)
    if not isinstance(value, str) or value != value.strip() or any(character.isspace() for character in value):
        _fail(f"invalid_{field_name}")
    return value


def _whole_second(value: object, field_name: str, *, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or len(value) != 20 or value[10] != "T" or not value.endswith("Z"):
        _fail(f"invalid_{field_name}")
    errors = _validate_utc(value, field_name)
    if errors:
        raise ContractValidationError(errors)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ContractValidationError(f"invalid_{field_name}") from exc


def _count(value: object, field_name: str) -> int:
    if type(value) is not int or value < 0:
        _fail(f"invalid_{field_name}")
    return value


def _closed_flag(value: object, required: object, code: str) -> None:
    if value is not required:
        _fail(code)


def _authority(
    *,
    production_authority: object,
    execution_authority: object,
    source_authenticity: object,
    request_coverage: object,
    schema_version: object,
) -> None:
    _closed_flag(schema_version, MASTER_SPECIALIST_RUNTIME_CONTRACT_SCHEMA_VERSION, "invalid_schema_version")
    _closed_flag(production_authority, MasterProductionAuthority.DENIED, "production_authority_not_issued")
    _closed_flag(execution_authority, MasterRouteExecutionAuthority.DENIED, "execution_authority_not_granted")
    _closed_flag(source_authenticity, MasterSourceAuthenticity.NOT_ESTABLISHED, "source_authenticity_not_established")
    _closed_flag(request_coverage, MasterRequestCoverage.NOT_ESTABLISHED, "request_coverage_not_established")
    _closed_flag(PRODUCTION_AUTHORITY_AVAILABLE, False, "production_authority_not_issued")
    _closed_flag(PRINCIPAL_ISSUANCE_AVAILABLE, False, "principal_not_issued")
    _closed_flag(GRANT_ISSUANCE_AVAILABLE, False, "grant_not_issued")
    _closed_flag(EVIDENCE_ADMISSION_AVAILABLE, False, "evidence_not_admitted")
    _closed_flag(SPECIALIST_DISPATCH_AVAILABLE, False, "specialist_invocation_not_granted")
    _closed_flag(NATIVE_TYPES_CHANGED_THIS_SPRINT, False, "invalid_native_result_gap")


def _posture(value: object, allowed: frozenset[TrustDistinction]) -> TrustDistinction:
    if not isinstance(value, TrustDistinction) or value not in _CONSTRUCTABLE_POSTURES:
        _fail("trust_distinction_not_available")
    if value not in allowed:
        _fail("trust_distinction_not_available")
    return value


def _status(value: object, allowed: frozenset[RuntimeContractStatus]) -> RuntimeContractStatus:
    if not isinstance(value, RuntimeContractStatus) or value not in allowed:
        _fail("invalid_status")
    return value


def _tokens(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        _fail(f"invalid_{field_name}")
    checked: list[str] = []
    for item in value:
        token = _token(item, field_name)
        assert token is not None
        lowered = token.lower()
        if token == "*" or "*" in token or lowered.startswith("arn:") or "arn:" in lowered:
            _fail("arbitrary_resource_not_permitted")
        checked.append(token)
    return tuple(checked)


def _require_pair(specialist: object, purpose: object, capability: object) -> None:
    if not isinstance(specialist, MasterSpecialist):
        _fail("invalid_specialist")
    if not isinstance(purpose, MasterPurpose):
        _fail("invalid_purpose")
    if not isinstance(capability, AgentCapability):
        _fail("invalid_capability")
    if capability in {
        AgentCapability.MUTATE_OPERATIONAL_STATE,
        AgentCapability.EXECUTE_REROUTES,
        AgentCapability.ISSUE_AUTONOMOUS_FLIGHT_CONTROL_INSTRUCTIONS,
        AgentCapability.ISSUE_LANDING_INSTRUCTIONS,
        AgentCapability.ISSUE_ALTITUDE_INSTRUCTIONS,
        AgentCapability.ISSUE_ATC_INSTRUCTIONS,
    }:
        _fail("prohibited_capability")
    allowed = _PAIR_CAPABILITIES.get((specialist, purpose))
    if allowed is None:
        _fail("unsupported_specialist_purpose")
    if capability not in allowed:
        _fail("capability_step_mismatch")


def _distinct_identity(record: object) -> None:
    values = [getattr(record, name) for name in _HOST_FIELDS]
    if len(set(values)) != len(values):
        _fail("request_identity_not_distinct")


def _same_identity(left: "HostInvocationIdentity", right: "HostInvocationIdentity") -> None:
    if type(left) is not HostInvocationIdentity or type(right) is not HostInvocationIdentity:
        _fail("identity_ambiguous")
    if left != right:
        _fail("identity_ambiguous")


class HostInvocationIdentityPort(Protocol):
    """Identity fields every later host record binds. Not a credential."""

    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    specialist_request_id: str
    correlation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability


class ReviewedApplicationHostPort(Protocol):
    """Port the reviewed host would have to implement. This module does not."""

    def issue_principal(self, requirement: "PrincipalRequirement") -> None:
        """Issue a principal. Unavailable in this contract."""

    def issue_grant(self, reference: "IssuedGrantReference") -> None:
        """Issue a grant. Unavailable in this contract."""

    def consume_reservation(self, reservation: "LedgerReservation") -> None:
        """Consume a host-issued reservation. Unavailable in this contract."""

    def authenticate_source(self, handoff: "ProvenanceHandoff") -> None:
        """Authenticate a completed source read. Unavailable in this contract."""


PRODUCTION_HOST: ReviewedApplicationHostPort | None = None


@dataclass(frozen=True)
class HostInvocationIdentity:
    """One host invocation. A complete identity is still not authorization."""

    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    specialist_request_id: str
    correlation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    requirement: MasterRequirement
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    posture: TrustDistinction = TrustDistinction.DESCRIPTIVE
    status: RuntimeContractStatus = RuntimeContractStatus.DESCRIBED
    schema_version: str = MASTER_SPECIALIST_RUNTIME_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _authority(
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            schema_version=self.schema_version,
        )
        _posture(self.posture, frozenset({TrustDistinction.DESCRIPTIVE}))
        _status(self.status, frozenset({RuntimeContractStatus.DESCRIBED}))
        for name in _HOST_FIELDS:
            _token(getattr(self, name), name)
        _distinct_identity(self)
        if not isinstance(self.requirement, MasterRequirement):
            _fail("invalid_requirement")
        _require_pair(self.specialist, self.purpose, self.capability)


@dataclass(frozen=True)
class SpecialistRequestEnvelope:
    """Descriptive request envelope. It is not an authentication token or a grant.

    Hint fields are ``principal_ref``, ``policy_revision``, ``resource_scope``,
    ``region_scope``, the historical window, and ``decision_target_scope``.
    ``authorization_attached`` cannot be true.
    """

    identity: HostInvocationIdentity
    accepted_at_utc: str
    deadline_utc: str
    idempotency_key: str
    max_model_turns: int
    max_tool_calls: int
    principal_ref: str | None = None
    policy_revision: str | None = None
    resource_scope: tuple[str, ...] = ()
    region_scope: tuple[str, ...] = ()
    historical_window_start_utc: str | None = None
    historical_window_end_utc: str | None = None
    decision_target_scope: str | None = None
    requested_evaluation_time_utc: str | None = None
    authorization_attached: bool = False
    historical_window_is_tool_argument: bool = False
    hints_are_authority: bool = False
    max_executions: int = 1
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    posture: TrustDistinction = TrustDistinction.DESCRIPTIVE
    status: RuntimeContractStatus = RuntimeContractStatus.DESCRIBED
    schema_version: str = MASTER_SPECIALIST_RUNTIME_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _authority(
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            schema_version=self.schema_version,
        )
        _posture(self.posture, frozenset({TrustDistinction.DESCRIPTIVE}))
        _status(self.status, frozenset({RuntimeContractStatus.DESCRIBED}))
        if type(self.identity) is not HostInvocationIdentity:
            _fail("identity_missing")
        _closed_flag(self.authorization_attached, False, "caller_proof_not_host_issued")
        _closed_flag(self.historical_window_is_tool_argument, False, "historical_window_not_authorized")
        _closed_flag(self.hints_are_authority, False, "caller_proof_not_host_issued")
        accepted = _whole_second(self.accepted_at_utc, "accepted_at_utc")
        deadline = _whole_second(self.deadline_utc, "deadline_utc")
        assert accepted is not None and deadline is not None
        if deadline <= accepted:
            _fail("invalid_deadline")
        _whole_second(
            self.requested_evaluation_time_utc,
            "requested_evaluation_time_utc",
            optional=True,
        )
        idempotency_key = _token(self.idempotency_key, "idempotency_key")
        if idempotency_key in {getattr(self.identity, name) for name in _HOST_FIELDS}:
            _fail("request_identity_not_distinct")
        _token(self.principal_ref, "principal_ref", optional=True)
        _token(self.policy_revision, "policy_revision", optional=True)
        _token(self.decision_target_scope, "decision_target_scope", optional=True)
        object.__setattr__(self, "resource_scope", _tokens(self.resource_scope, "resource_scope"))
        object.__setattr__(self, "region_scope", _tokens(self.region_scope, "region_scope"))
        executions, max_turns, max_calls = server_read_budget(self.identity.specialist)
        _closed_flag(self.max_executions, executions, "budget_exceeds_server_ceiling")
        turns = _count(self.max_model_turns, "max_model_turns")
        calls = _count(self.max_tool_calls, "max_tool_calls")
        if turns < 1 or turns > max_turns or calls < 1 or calls > max_calls:
            _fail("budget_exceeds_server_ceiling")
        _window(self)
        if self.identity.specialist is MasterSpecialist.DECISION:
            return
        if self.decision_target_scope is not None:
            _fail("target_authorization_not_granted")


def _window(envelope: SpecialistRequestEnvelope) -> None:
    start = envelope.historical_window_start_utc
    end = envelope.historical_window_end_utc
    historical = envelope.identity.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    if not historical:
        if start is not None or end is not None:
            _fail("contradictory_temporal_bounds")
        return
    parsed_start = _whole_second(start, "historical_window_start_utc")
    parsed_end = _whole_second(end, "historical_window_end_utc")
    assert parsed_start is not None and parsed_end is not None
    if parsed_start >= parsed_end:
        _fail("contradictory_temporal_bounds")
    if parsed_end - parsed_start > MAX_HISTORICAL_WINDOW:
        _fail("historical_window_exceeds_seven_days")


@dataclass(frozen=True)
class PrincipalRequirement:
    """Names the missing reviewed-host principal. It does not issue one.

    ``principal_ref_hint`` is an untrusted hint. Edge JWT validation is not
    attestation attached to this requirement.
    """

    identity: HostInvocationIdentity
    principal_ref_hint: str | None = None
    required_issuer: str = "reviewed_application_host"
    satisfied: bool = False
    host_issued: bool = False
    edge_jwt_is_attestation: bool = False
    direct_lambda_invoke_verified: bool = False
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    posture: TrustDistinction = TrustDistinction.DESCRIPTIVE
    status: RuntimeContractStatus = RuntimeContractStatus.NOT_ESTABLISHED
    schema_version: str = MASTER_SPECIALIST_RUNTIME_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _authority(
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            schema_version=self.schema_version,
        )
        _posture(self.posture, frozenset({TrustDistinction.DESCRIPTIVE}))
        _status(self.status, frozenset({RuntimeContractStatus.NOT_ESTABLISHED}))
        if type(self.identity) is not HostInvocationIdentity:
            _fail("identity_missing")
        _token(self.principal_ref_hint, "principal_ref", optional=True)
        _closed_flag(self.required_issuer, "reviewed_application_host", "principal_not_issued")
        _closed_flag(self.satisfied, False, "principal_not_issued")
        _closed_flag(self.host_issued, False, "host_issued_not_available")
        _closed_flag(self.edge_jwt_is_attestation, False, "caller_proof_not_host_issued")
        _closed_flag(self.direct_lambda_invoke_verified, False, "lambda_invoke_not_verified")


@dataclass(frozen=True)
class EffectiveExecutionScope:
    """Effective scope description copied from the envelope. It cannot be redeemed."""

    identity: HostInvocationIdentity
    temporal_scopes: tuple[TemporalScope, ...]
    resource_scope: tuple[str, ...]
    region_scope: tuple[str, ...]
    historical_window_start_utc: str | None
    historical_window_end_utc: str | None
    decision_target_scope: str | None
    max_route_steps: int = MAX_ROUTE_STEPS
    max_decision_steps: int = MAX_DECISION_STEPS
    historical_window_bound_to_tool_arguments: bool = False
    decision_target_established: bool = False
    region_scope_is_geographic_coverage: bool = False
    scope_authorized: bool = False
    redeemable: bool = False
    execution_permission_granted: bool = False
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    posture: TrustDistinction = TrustDistinction.DESCRIPTIVE
    status: RuntimeContractStatus = RuntimeContractStatus.DESCRIBED
    schema_version: str = MASTER_SPECIALIST_RUNTIME_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _authority(
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            schema_version=self.schema_version,
        )
        _posture(self.posture, frozenset({TrustDistinction.DESCRIPTIVE}))
        _status(self.status, frozenset({RuntimeContractStatus.DESCRIBED}))
        if type(self.identity) is not HostInvocationIdentity:
            _fail("identity_missing")
        if not isinstance(self.temporal_scopes, tuple) or any(
            not isinstance(item, TemporalScope) for item in self.temporal_scopes
        ):
            _fail("invalid_temporal_scopes")
        _temporal(self.identity.specialist, self.temporal_scopes)
        _closed_flag(self.max_route_steps, MAX_ROUTE_STEPS, "excessive_plan_size")
        _closed_flag(self.max_decision_steps, MAX_DECISION_STEPS, "decision_fanout_not_permitted")
        _closed_flag(self.historical_window_bound_to_tool_arguments, False, "historical_window_not_authorized")
        _closed_flag(self.decision_target_established, False, "decision_target_not_established")
        _closed_flag(self.region_scope_is_geographic_coverage, False, "geospatial_coverage_not_established")
        _closed_flag(self.scope_authorized, False, "scope_not_redeemable")
        _closed_flag(self.redeemable, False, "scope_not_redeemable")
        _closed_flag(self.execution_permission_granted, False, "execution_permission_not_granted")
        _scope_window(self)


def _scope_window(scope: EffectiveExecutionScope) -> None:
    start = scope.historical_window_start_utc
    end = scope.historical_window_end_utc
    historical = scope.identity.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    if not historical:
        if start is not None or end is not None:
            _fail("contradictory_temporal_bounds")
        if scope.decision_target_scope is not None and scope.identity.specialist is not MasterSpecialist.DECISION:
            _fail("target_authorization_not_granted")
        return
    parsed_start = _whole_second(start, "historical_window_start_utc")
    parsed_end = _whole_second(end, "historical_window_end_utc")
    assert parsed_start is not None and parsed_end is not None
    if parsed_start >= parsed_end:
        _fail("contradictory_temporal_bounds")
    if parsed_end - parsed_start > MAX_HISTORICAL_WINDOW:
        _fail("historical_window_exceeds_seven_days")
    if scope.decision_target_scope is not None:
        _fail("target_authorization_not_granted")


def _temporal(specialist: MasterSpecialist, scopes: tuple[TemporalScope, ...]) -> None:
    if TemporalScope.HYBRID in scopes:
        _fail("invalid_temporal_scopes")
    if specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
        if scopes != (TemporalScope.HISTORICAL,):
            _fail("invalid_temporal_scopes")
        return
    if specialist is MasterSpecialist.LIVE_OPS:
        if scopes != (TemporalScope.CURRENT,):
            _fail("invalid_temporal_scopes")
        return
    if scopes not in _DECISION_SCOPE_CHOICES:
        _fail("invalid_temporal_scopes")


@dataclass(frozen=True)
class ApprovedPolicyDecision:
    """An evaluated policy result. A descriptive allow is not a production grant."""

    identity: HostInvocationIdentity
    policy_revision: str
    policy_status: PolicyDecisionStatus
    usable_as_production_grant: bool = False
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    posture: TrustDistinction = TrustDistinction.EVALUATED
    status: RuntimeContractStatus = RuntimeContractStatus.NOT_ESTABLISHED
    schema_version: str = MASTER_SPECIALIST_RUNTIME_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _authority(
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            schema_version=self.schema_version,
        )
        _posture(self.posture, frozenset({TrustDistinction.EVALUATED}))
        if type(self.identity) is not HostInvocationIdentity:
            _fail("identity_missing")
        _token(self.policy_revision, "policy_revision")
        if not isinstance(self.policy_status, PolicyDecisionStatus):
            _fail("invalid_status")
        expected = _POLICY_STATUS[self.policy_status]
        _status(self.status, frozenset({expected}))
        _closed_flag(self.usable_as_production_grant, False, "descriptive_allow_not_a_grant")


@dataclass(frozen=True)
class IssuedGrantReference:
    """A name for a grant the reviewed host has not issued.

    This object is not self-authorizing. Supplying the envelope, copying its
    fields, or constructing a valid reference leaves ``issued`` false.
    """

    identity: HostInvocationIdentity
    grant_ref: str
    policy_revision: str
    effective_scope: EffectiveExecutionScope
    issued: bool = False
    self_authorizing: bool = False
    copied_from_envelope: bool = False
    usable_as_production_grant: bool = False
    executable: bool = False
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    posture: TrustDistinction = TrustDistinction.DESCRIPTIVE
    status: RuntimeContractStatus = RuntimeContractStatus.DESCRIBED
    schema_version: str = MASTER_SPECIALIST_RUNTIME_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _authority(
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            schema_version=self.schema_version,
        )
        _posture(self.posture, frozenset({TrustDistinction.DESCRIPTIVE}))
        _status(self.status, frozenset({RuntimeContractStatus.DESCRIBED}))
        if type(self.identity) is not HostInvocationIdentity:
            _fail("identity_missing")
        if type(self.effective_scope) is not EffectiveExecutionScope:
            _fail("invalid_execution_scope")
        _same_identity(self.identity, self.effective_scope.identity)
        grant_ref = _token(self.grant_ref, "grant_ref")
        _token(self.policy_revision, "policy_revision")
        if grant_ref in {getattr(self.identity, name) for name in _HOST_FIELDS}:
            _fail("request_identity_not_distinct")
        _closed_flag(self.issued, False, "grant_not_issued")
        _closed_flag(self.self_authorizing, False, "grant_not_self_authorizing")
        _closed_flag(self.usable_as_production_grant, False, "descriptive_allow_not_a_grant")
        _closed_flag(self.executable, False, "execution_not_granted")
        if type(self.copied_from_envelope) is not bool:
            _fail("invalid_grant_reference")


@dataclass(frozen=True)
class LedgerReservation:
    """A described reservation. This module cannot consume it or run the step."""

    identity: HostInvocationIdentity
    reservation_ref: str
    idempotency_key: str
    consumed: bool = False
    grants_execution: bool = False
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    posture: TrustDistinction = TrustDistinction.DESCRIPTIVE
    status: RuntimeContractStatus = RuntimeContractStatus.DESCRIBED
    schema_version: str = MASTER_SPECIALIST_RUNTIME_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _authority(
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            schema_version=self.schema_version,
        )
        _posture(self.posture, frozenset({TrustDistinction.DESCRIPTIVE}))
        _status(self.status, frozenset({RuntimeContractStatus.DESCRIBED}))
        if type(self.identity) is not HostInvocationIdentity:
            _fail("identity_missing")
        reservation_ref = _token(self.reservation_ref, "reservation_ref")
        idempotency_key = _token(self.idempotency_key, "idempotency_key")
        identity_values = {getattr(self.identity, name) for name in _HOST_FIELDS}
        if reservation_ref in identity_values or idempotency_key in identity_values:
            _fail("request_identity_not_distinct")
        if reservation_ref == idempotency_key:
            _fail("request_identity_not_distinct")
        _closed_flag(self.consumed, False, "reservation_not_consumed")
        _closed_flag(self.grants_execution, False, "execution_not_granted")


@dataclass(frozen=True)
class BudgetCancellation:
    """Budget and cancellation description.

    Ceilings come from ``server_read_budget``. This record does not enforce
    them at tool execution; ``enforced_at_tool_execution`` stays false.
    ``independent_observation_time_utc`` is not the application request clock.
    """

    identity: HostInvocationIdentity
    max_model_turns: int
    max_tool_calls: int
    deadline_utc: str
    accepted_at_utc: str
    independent_observation_time_utc: str
    cancellation: CancellationObservation
    model_turns_observed: int
    tool_calls_observed: int
    max_executions: int = 1
    budget_exhausted: bool = False
    enforced_at_tool_execution: bool = False
    caller_budget_is_authority: bool = False
    clocks_equated: bool = False
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    posture: TrustDistinction = TrustDistinction.EVALUATED
    status: RuntimeContractStatus = RuntimeContractStatus.DESCRIBED
    schema_version: str = MASTER_SPECIALIST_RUNTIME_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _authority(
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            schema_version=self.schema_version,
        )
        _posture(self.posture, frozenset({TrustDistinction.EVALUATED}))
        _status(self.status, frozenset({RuntimeContractStatus.DESCRIBED}))
        if type(self.identity) is not HostInvocationIdentity:
            _fail("identity_missing")
        if not isinstance(self.cancellation, CancellationObservation):
            _fail("invalid_cancellation")
        executions, max_turns, max_calls = server_read_budget(self.identity.specialist)
        _closed_flag(self.max_executions, executions, "budget_exceeds_server_ceiling")
        turns = _count(self.max_model_turns, "max_model_turns")
        calls = _count(self.max_tool_calls, "max_tool_calls")
        if turns < 1 or turns > max_turns or calls < 1 or calls > max_calls:
            _fail("budget_exceeds_server_ceiling")
        observed_turns = _count(self.model_turns_observed, "model_turns_observed")
        observed_calls = _count(self.tool_calls_observed, "tool_calls_observed")
        exhausted = observed_turns >= turns or observed_calls >= calls
        _closed_flag(self.budget_exhausted, exhausted, "invalid_budget_observation")
        accepted = _whole_second(self.accepted_at_utc, "accepted_at_utc")
        deadline = _whole_second(self.deadline_utc, "deadline_utc")
        observed = _whole_second(self.independent_observation_time_utc, "independent_observation_time_utc")
        assert accepted is not None and deadline is not None and observed is not None
        if deadline <= accepted:
            _fail("invalid_deadline")
        expired = observed >= deadline
        if expired and self.cancellation is CancellationObservation.NOT_CANCELLED:
            _fail("invalid_cancellation")
        if not expired and self.cancellation is CancellationObservation.DEADLINE_EXPIRED:
            _fail("invalid_cancellation")
        _closed_flag(self.enforced_at_tool_execution, False, "execution_not_enforced_here")
        _closed_flag(self.caller_budget_is_authority, False, "caller_budget_not_authority")
        _closed_flag(self.clocks_equated, False, "timestamp_equivalence_not_established")


@dataclass(frozen=True)
class ToolExecutionObservation:
    """A described tool read. It is not aviation evidence and not a verified zero.

    ``source_observation_time_utc`` and ``evaluation_time_utc`` are optional
    and independent of the application request clock. Completeness, pagination,
    and truncation may be described. They do not admit the read.
    """

    identity: HostInvocationIdentity
    tool_name: str
    operation: ReadOperation
    observation_status: ToolExecutionObservationStatus
    argument_names: tuple[str, ...] = ()
    source_identity: str | None = None
    query_or_record_id: str | None = None
    source_version: str | None = None
    source_observation_time_utc: str | None = None
    evaluation_time_utc: str | None = None
    completeness: str | None = None
    pagination_complete: bool | None = None
    truncated: bool = False
    missing_record: bool = False
    empty_page: bool = False
    impact_count: int | None = None
    regional_name_match: str | None = None
    approved_runtime_adapter: bool = False
    aviation_evidence: bool = False
    verified_zero: bool = False
    sigmet_geographic_coverage: bool = False
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    posture: TrustDistinction = TrustDistinction.DESCRIPTIVE
    status: RuntimeContractStatus = RuntimeContractStatus.DESCRIBED
    schema_version: str = MASTER_SPECIALIST_RUNTIME_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _authority(
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            schema_version=self.schema_version,
        )
        _posture(self.posture, frozenset({TrustDistinction.DESCRIPTIVE}))
        _status(self.status, frozenset({RuntimeContractStatus.DESCRIBED}))
        if type(self.identity) is not HostInvocationIdentity:
            _fail("identity_missing")
        _token(self.tool_name, "tool_name")
        if not isinstance(self.operation, ReadOperation):
            _fail("invalid_operation")
        if self.operation is ReadOperation.SCAN:
            _fail("scan_not_permitted")
        if not isinstance(self.observation_status, ToolExecutionObservationStatus):
            _fail("invalid_status")
        allowed_tools = server_read_tools(self.identity.specialist)
        if self.tool_name not in allowed_tools:
            _fail("unsupported_tool")
        if not isinstance(self.argument_names, tuple) or any(type(item) is not str for item in self.argument_names):
            _fail("invalid_argument_names")
        allowed_arguments = server_read_argument_names(self.identity.specialist, self.tool_name)
        if any(name not in allowed_arguments for name in self.argument_names):
            _fail("argument_not_allowlisted")
        if _SQL_ARGUMENT_NAMES.intersection(self.argument_names):
            _fail("arbitrary_sql_not_permitted")
        operations = {item[1] for item in server_read_resources(self.identity.specialist, self.tool_name)}
        if self.operation not in operations:
            _fail("operation_not_allowlisted")
        for name in ("source_identity", "query_or_record_id", "source_version", "completeness", "regional_name_match"):
            _token(getattr(self, name), name, optional=True)
        _whole_second(self.source_observation_time_utc, "source_observation_time_utc", optional=True)
        _whole_second(self.evaluation_time_utc, "evaluation_time_utc", optional=True)
        if self.pagination_complete is not None and type(self.pagination_complete) is not bool:
            _fail("invalid_pagination")
        if type(self.truncated) is not bool or type(self.missing_record) is not bool or type(self.empty_page) is not bool:
            _fail("invalid_tool_observation")
        if self.impact_count is not None:
            _count(self.impact_count, "impact_count")
        _closed_flag(self.approved_runtime_adapter, False, "evidence_not_admitted")
        _closed_flag(self.aviation_evidence, False, "evidence_not_admitted")
        _closed_flag(self.verified_zero, False, "absence_is_not_verified_zero")
        _closed_flag(self.sigmet_geographic_coverage, False, "geospatial_coverage_not_established")


@dataclass(frozen=True)
class NativeSpecialistEntrypoint:
    """Maps one native ``run`` without calling it."""

    specialist: MasterSpecialist
    owner_type: str
    method: str
    context_parameter: str
    request_type: str
    context_type: str
    direct_result_type: str
    final_result_type: str
    intermediate_result_type: str | None
    finalization_entrypoint: str | None
    defining_module: str
    target_required_before_request: bool
    invoked_here: bool = False
    production_call_available: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.specialist, MasterSpecialist):
            _fail("invalid_specialist")
        for name in (
            "owner_type",
            "method",
            "context_parameter",
            "request_type",
            "context_type",
            "direct_result_type",
            "final_result_type",
            "defining_module",
        ):
            _token(getattr(self, name), name)
        if self.intermediate_result_type is not None:
            _token(self.intermediate_result_type, "intermediate_result_type")
        if self.finalization_entrypoint is not None:
            _token(self.finalization_entrypoint, "finalization_entrypoint")
        _closed_flag(self.method, "run", "specialist_invocation_not_granted")
        _closed_flag(self.invoked_here, False, "specialist_invocation_not_granted")
        _closed_flag(self.production_call_available, False, "specialist_invocation_not_granted")
        if type(self.target_required_before_request) is not bool:
            _fail("invalid_native_entrypoint")


@dataclass(frozen=True)
class NativeSpecialistExecutionResult:
    """Description of a native result. The native object is not retained.

    A caller-supplied result is not source authentication. A correlation field
    on the native type does not complete the host identity, and a matching
    identifier is not authorization.
    """

    identity: HostInvocationIdentity
    stage: NativeResultStage
    native_status: LiveOpsRunStatus | SpecialistRunStatus | SpecialistStatus | DecisionSpecialistRunStatus
    observed: bool
    present_host_fields: tuple[str, ...]
    missing_host_fields: tuple[str, ...]
    result_retained: bool = False
    host_identity_complete: bool = False
    matching_identifier_is_authorization: bool = False
    source_authenticated: bool = False
    aviation_evidence: bool = False
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    posture: TrustDistinction = TrustDistinction.DESCRIPTIVE
    status: RuntimeContractStatus = RuntimeContractStatus.DESCRIBED
    schema_version: str = MASTER_SPECIALIST_RUNTIME_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _authority(
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            schema_version=self.schema_version,
        )
        _posture(self.posture, frozenset({TrustDistinction.DESCRIPTIVE}))
        _status(self.status, frozenset({RuntimeContractStatus.DESCRIBED}))
        if type(self.identity) is not HostInvocationIdentity:
            _fail("identity_missing")
        if not isinstance(self.stage, NativeResultStage):
            _fail("invalid_native_result")
        expected_specialist = _STAGE_SPECIALIST[self.stage]
        if self.identity.specialist is not expected_specialist:
            _fail("specialist_artifact_mismatch")
        status_type = _STATUS_TYPES[self.stage]
        if type(self.native_status) is not status_type:
            _fail("invalid_status")
        if type(self.observed) is not bool:
            _fail("invalid_native_result")
        if self.present_host_fields != _present_host_fields(_RESULT_TYPES[self.stage]):
            _fail("invalid_native_result_gap")
        if self.missing_host_fields != _missing_host_fields(_RESULT_TYPES[self.stage]):
            _fail("invalid_native_result_gap")
        _closed_flag(self.result_retained, False, "serialized_record_not_native_result")
        _closed_flag(self.host_identity_complete, False, "invalid_native_result_gap")
        _closed_flag(self.matching_identifier_is_authorization, False, "matching_identifier_not_authorization")
        _closed_flag(self.source_authenticated, False, "source_authenticity_not_established")
        _closed_flag(self.aviation_evidence, False, "evidence_not_admitted")


@dataclass(frozen=True)
class ProvenanceHandoff:
    """Clock-separated handoff. It does not admit evidence."""

    identity: HostInvocationIdentity
    accepted_at_utc: str
    deadline_utc: str
    tool_observations: tuple[ToolExecutionObservation, ...] = ()
    native_result: NativeSpecialistExecutionResult | None = None
    requested_evaluation_time_utc: str | None = None
    source_observation_time_utc: str | None = None
    evaluation_time_utc: str | None = None
    independent_observation_time_utc: str | None = None
    evaluation_copied_from_request: bool = False
    admitted: bool = False
    verified_zero: bool = False
    limitation: str = PROVENANCE_HANDOFF_LIMITATION
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    posture: TrustDistinction = TrustDistinction.DESCRIPTIVE
    status: RuntimeContractStatus = RuntimeContractStatus.NOT_ESTABLISHED
    schema_version: str = MASTER_SPECIALIST_RUNTIME_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _authority(
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            schema_version=self.schema_version,
        )
        _posture(self.posture, frozenset({TrustDistinction.DESCRIPTIVE}))
        _status(self.status, frozenset({RuntimeContractStatus.NOT_ESTABLISHED}))
        if type(self.identity) is not HostInvocationIdentity:
            _fail("identity_missing")
        _whole_second(self.accepted_at_utc, "accepted_at_utc")
        _whole_second(self.deadline_utc, "deadline_utc")
        _whole_second(self.requested_evaluation_time_utc, "requested_evaluation_time_utc", optional=True)
        _whole_second(self.source_observation_time_utc, "source_observation_time_utc", optional=True)
        _whole_second(self.evaluation_time_utc, "evaluation_time_utc", optional=True)
        _whole_second(
            self.independent_observation_time_utc,
            "independent_observation_time_utc",
            optional=True,
        )
        if not isinstance(self.tool_observations, tuple) or any(
            type(item) is not ToolExecutionObservation for item in self.tool_observations
        ):
            _fail("invalid_tool_observation")
        for item in self.tool_observations:
            _same_identity(self.identity, item.identity)
        if self.native_result is not None:
            if type(self.native_result) is not NativeSpecialistExecutionResult:
                _fail("invalid_native_result")
            _same_identity(self.identity, self.native_result.identity)
        _closed_flag(self.evaluation_copied_from_request, False, "timestamp_equivalence_not_established")
        _closed_flag(self.admitted, False, "evidence_not_admitted")
        _closed_flag(self.verified_zero, False, "absence_is_not_verified_zero")
        _closed_flag(self.limitation, PROVENANCE_HANDOFF_LIMITATION, "invalid_provenance_handoff")


@dataclass(frozen=True)
class HistoricalContractMigration:
    """One unresolved native Historical contract gap. This sprint does not close it."""

    contract_name: str
    missing_fields: tuple[str, ...]
    source_version_owner: str = SOURCE_VERSION_OWNER
    query_id_owner: str = QUERY_ID_OWNER
    native_type_changed: bool = False

    def __post_init__(self) -> None:
        _token(self.contract_name, "contract_name")
        if not isinstance(self.missing_fields, tuple) or any(type(item) is not str or not item for item in self.missing_fields):
            _fail("invalid_native_result_gap")
        _closed_flag(self.source_version_owner, SOURCE_VERSION_OWNER, "invalid_native_result_gap")
        _closed_flag(self.query_id_owner, QUERY_ID_OWNER, "invalid_native_result_gap")
        _closed_flag(self.native_type_changed, False, "invalid_native_result_gap")


@dataclass(frozen=True)
class NativeIdentityMismatch:
    """Host identity fields a native type does not carry. Descriptive only."""

    contract_name: str
    present_host_fields: tuple[str, ...]
    missing_host_fields: tuple[str, ...]
    notes: tuple[str, ...]
    inspected: bool

    def __post_init__(self) -> None:
        _token(self.contract_name, "contract_name")
        for name, value in (
            ("present_host_fields", self.present_host_fields),
            ("missing_host_fields", self.missing_host_fields),
            ("notes", self.notes),
        ):
            if not isinstance(value, tuple) or any(type(item) is not str for item in value):
                _fail(f"invalid_{name}")
        if type(self.inspected) is not bool:
            _fail("invalid_native_result_gap")


@dataclass(frozen=True)
class DecisionTargetGate:
    """The Decision native request is withheld until the reviewed host establishes the target.

    Establishment has to happen before ``DecisionSpecialistRequest`` is
    constructed. ``release_master_decision_target`` already refuses dispatch.
    Comparison fields are not the native target.
    """

    native_request_type: str = DECISION_NATIVE_REQUEST_TYPE
    establishment_point: str = "before_DecisionSpecialistRequest_construction"
    required_proofs: tuple[str, ...] = REQUIRED_DECISION_HOST_PROOFS
    proofs_satisfied: tuple[str, ...] = ()
    proof_issuer: str = HOST_PROOF_ISSUER
    native_request_constructed: bool = False
    comparison_fields_are_targets: bool = False
    unique_candidate_is_target: bool = False
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    schema_version: str = MASTER_SPECIALIST_RUNTIME_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _authority(
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            schema_version=self.schema_version,
        )
        _closed_flag(self.native_request_type, DECISION_NATIVE_REQUEST_TYPE, "decision_target_not_established")
        _closed_flag(
            self.establishment_point,
            "before_DecisionSpecialistRequest_construction",
            "decision_target_not_established",
        )
        if self.required_proofs != REQUIRED_DECISION_HOST_PROOFS or self.proofs_satisfied != ():
            _fail("caller_proof_not_host_issued")
        _closed_flag(self.proof_issuer, HOST_PROOF_ISSUER, "caller_proof_not_host_issued")
        _closed_flag(self.native_request_constructed, False, "native_request_not_constructed")
        _closed_flag(self.comparison_fields_are_targets, False, "target_authorization_not_granted")
        _closed_flag(self.unique_candidate_is_target, False, "target_authorization_not_granted")


@dataclass(frozen=True)
class AuthorizationProofObservation:
    """Every caller-supplied proof is rejected. Absence is NOT_ESTABLISHED."""

    proof_kind: FalseProofKind | None
    identity: HostInvocationIdentity | None = None
    grant_issued: bool = False
    principal_issued: bool = False
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    posture: TrustDistinction = TrustDistinction.EVALUATED
    status: RuntimeContractStatus = RuntimeContractStatus.NOT_ESTABLISHED
    schema_version: str = MASTER_SPECIALIST_RUNTIME_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _authority(
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            schema_version=self.schema_version,
        )
        _posture(self.posture, frozenset({TrustDistinction.EVALUATED}))
        if self.proof_kind is None:
            _status(self.status, frozenset({RuntimeContractStatus.NOT_ESTABLISHED}))
        else:
            if not isinstance(self.proof_kind, FalseProofKind):
                _fail("invalid_status")
            _status(self.status, frozenset({RuntimeContractStatus.REJECTED_FALSE_PROOF}))
        if self.identity is not None and type(self.identity) is not HostInvocationIdentity:
            _fail("identity_ambiguous")
        _closed_flag(self.grant_issued, False, "grant_not_issued")
        _closed_flag(self.principal_issued, False, "principal_not_issued")


def _present_host_fields(cls: type) -> tuple[str, ...]:
    present = _field_names(cls)
    return tuple(name for name in _HOST_FIELDS if name in present)


def _missing_host_fields(cls: type) -> tuple[str, ...]:
    present = _field_names(cls)
    return tuple(name for name in _HOST_FIELDS if name not in present)


def _mismatch(cls: type, notes: tuple[str, ...], *, inspected: bool = True) -> NativeIdentityMismatch:
    return NativeIdentityMismatch(
        contract_name=cls.__name__,
        present_host_fields=_present_host_fields(cls),
        missing_host_fields=_missing_host_fields(cls),
        notes=notes,
        inspected=inspected,
    )


def _publish_historical_migration() -> tuple[HistoricalContractMigration, ...]:
    """Restate the baseline Historical correlation gaps without importing that module.

    The readiness module's import graph loads the Historical finalizer. This
    contract must not load it. Tests compare this tuple with
    ``historical_native_result_correlation_gaps``.
    """

    if "source_version" not in _field_names(SourceRecord):
        _fail("invalid_native_result_gap")
    if "query_id" not in _field_names(QueryExecutionTrace):
        _fail("invalid_native_result_gap")
    if "source_version" in _field_names(HistoricalSpecialistRunResult):
        _fail("invalid_native_result_gap")
    if "query_id" in _field_names(SpecialistResult):
        _fail("invalid_native_result_gap")
    published: list[HistoricalContractMigration] = []
    for cls in (
        HistoricalSpecialistRunResult,
        SpecialistResult,
        ToolResult,
        Evidence,
        SpecialistRequest,
        HistoricalSpecialistTrustedContext,
    ):
        present = _field_names(cls)
        missing = tuple(name for name in _HISTORICAL_GAP_FIELDS if name not in present)
        if cls in (HistoricalSpecialistRunResult, SpecialistResult):
            for name in ("master_request_id", "correlation_id", "source_version", "query_id"):
                if name not in missing:
                    _fail("invalid_native_result_gap")
        published.append(
            HistoricalContractMigration(
                contract_name=cls.__name__,
                missing_fields=missing,
            )
        )
    return tuple(published)


_RESULT_TYPES: dict[NativeResultStage, type] = {
    NativeResultStage.LIVE_OPS_RUN: LiveOpsRunResult,
    NativeResultStage.HISTORICAL_INTERMEDIATE: HistoricalSpecialistRunResult,
    NativeResultStage.HISTORICAL_FINAL: SpecialistResult,
    NativeResultStage.DECISION_RUN: DecisionSpecialistRunResult,
}
_STATUS_TYPES = {
    NativeResultStage.LIVE_OPS_RUN: LiveOpsRunStatus,
    NativeResultStage.HISTORICAL_INTERMEDIATE: SpecialistRunStatus,
    NativeResultStage.HISTORICAL_FINAL: SpecialistStatus,
    NativeResultStage.DECISION_RUN: DecisionSpecialistRunStatus,
}
_STATUS_ATTRIBUTES = {
    NativeResultStage.LIVE_OPS_RUN: "execution_status",
    NativeResultStage.HISTORICAL_INTERMEDIATE: "status",
    NativeResultStage.HISTORICAL_FINAL: "status",
    NativeResultStage.DECISION_RUN: "status",
}
_STAGE_SPECIALIST = {
    NativeResultStage.LIVE_OPS_RUN: MasterSpecialist.LIVE_OPS,
    NativeResultStage.HISTORICAL_INTERMEDIATE: MasterSpecialist.HISTORICAL_ANALYTICS,
    NativeResultStage.HISTORICAL_FINAL: MasterSpecialist.HISTORICAL_ANALYTICS,
    NativeResultStage.DECISION_RUN: MasterSpecialist.DECISION,
}
NATIVE_RESULT_TYPES = MappingProxyType(_RESULT_TYPES)
NATIVE_STATUS_TYPES = MappingProxyType(_STATUS_TYPES)

NATIVE_ENTRYPOINTS = (
    NativeSpecialistEntrypoint(
        specialist=MasterSpecialist.LIVE_OPS,
        owner_type="LiveOpsSpecialist",
        method="run",
        context_parameter="context",
        request_type="LiveOpsRunRequest",
        context_type="LiveOpsTrustedContext",
        direct_result_type="LiveOpsRunResult",
        final_result_type="LiveOpsRunResult",
        intermediate_result_type=None,
        finalization_entrypoint=None,
        defining_module="wilvor_ai.live_ops_specialist",
        target_required_before_request=False,
    ),
    NativeSpecialistEntrypoint(
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        owner_type="HistoricalAnalyticsSpecialist",
        method="run",
        context_parameter="context",
        request_type="SpecialistRequest",
        context_type="HistoricalSpecialistTrustedContext",
        direct_result_type="HistoricalSpecialistRunResult",
        final_result_type="SpecialistResult",
        intermediate_result_type="HistoricalSpecialistRunResult",
        finalization_entrypoint=HISTORICAL_FINALIZATION_ENTRYPOINT,
        defining_module="wilvor_ai.historical_specialist",
        target_required_before_request=False,
    ),
    NativeSpecialistEntrypoint(
        specialist=MasterSpecialist.DECISION,
        owner_type="DecisionSpecialist",
        method="run",
        context_parameter="runtime",
        request_type="DecisionSpecialistRequest",
        context_type="DecisionToolsRuntime",
        direct_result_type="DecisionSpecialistRunResult",
        final_result_type="DecisionSpecialistRunResult",
        intermediate_result_type=None,
        finalization_entrypoint=None,
        defining_module="wilvor_ai.decision_specialist",
        target_required_before_request=True,
    ),
)
NATIVE_ENTRYPOINTS_BY_SPECIALIST = MappingProxyType(
    {item.specialist: item for item in NATIVE_ENTRYPOINTS}
)
HISTORICAL_CONTRACT_MIGRATION = _publish_historical_migration()
NATIVE_IDENTITY_MISMATCHES = (
    _mismatch(
        LiveOpsRunResult,
        (
            "request_id is not specialist_request_id",
            "correlation_id on the result is not the full host identity",
            "a matching correlation_id is not authorization",
        ),
    ),
    _mismatch(
        LiveOpsRunRequest,
        ("request_id is optional and is not specialist_request_id",),
    ),
    _mismatch(
        LiveOpsTrustedContext,
        (
            "correlation_id is present",
            "tables and now_epoch are not constructed here",
            "now_epoch is not accepted_at_utc",
        ),
    ),
    _mismatch(
        HistoricalSpecialistRunResult,
        (
            "intermediate result omits host correlation",
            "source_version remains on SourceRecord",
            "query_id remains on QueryExecutionTrace",
        ),
    ),
    _mismatch(
        SpecialistResult,
        (
            "final Historical result omits host correlation",
            "evaluated_as_of_utc is not requested_evaluation_time_utc",
        ),
    ),
    _mismatch(
        SpecialistRequest,
        ("request_id is not a historical window or a host correlation set",),
    ),
    _mismatch(
        HistoricalSpecialistTrustedContext,
        ("as_of_utc is not source authentication",),
    ),
    _mismatch(
        DecisionSpecialistRunResult,
        ("the Decision run result carries no host identity fields",),
    ),
    _mismatch(
        DecisionSpecialistRequest,
        (
            "request_id is optional",
            "aircraft_id and recommendation_id are the native target fields",
            "this contract does not construct the request",
        ),
    ),
    NativeIdentityMismatch(
        contract_name="DecisionToolsRuntime",
        present_host_fields=("correlation_id",),
        missing_host_fields=tuple(name for name in _HOST_FIELDS if name != "correlation_id"),
        notes=(
            "fields are tables, now_epoch, query_timestamp_utc, correlation_id",
            "correlation_id is optional and is not copied onto DecisionSpecialistRunResult",
            "DecisionSpecialist.run names this parameter runtime",
            "this contract does not import wilvor_ai.decision_tools",
        ),
        inspected=False,
    ),
)
DECISION_TARGET_GATE = DecisionTargetGate()
DECISION_COMPARISON_FIELDS = (
    "MasterInvocationAuthorization.expected_aircraft_id",
    "MasterInvocationAuthorization.expected_recommendation_id",
    "MasterPlanningContext.target_candidate_aircraft_id",
    "MasterPlanningContext.target_candidate_recommendation_id",
)
INTEGRATION_DEPENDENCIES = (
    "wilvor_ai.master_contracts.MasterSpecialist",
    "wilvor_ai.master_contracts.MasterPurpose",
    "wilvor_ai.master_contracts.MasterRequirement",
    "wilvor_ai.master_contracts.MasterProductionAuthority",
    "wilvor_ai.contracts.AgentCapability",
    "wilvor_ai.contracts.TemporalScope",
    "wilvor_ai.master_routing.MAX_ROUTE_STEPS",
    "wilvor_ai.master_routing.MAX_DECISION_STEPS",
    "wilvor_ai.master_routing.MAX_HISTORICAL_WINDOW",
    "wilvor_ai.master_routing.MasterRouteExecutionAuthority",
    "wilvor_ai.master_routing.MasterRequestCoverage",
    "wilvor_ai.master_read_resource_guard.server_read_budget",
    "wilvor_ai.master_read_resource_guard.server_read_tools",
    "wilvor_ai.master_read_resource_guard.server_read_argument_names",
    "wilvor_ai.master_read_resource_guard.server_read_resources",
    "wilvor_ai.master_read_resource_guard.ReadOperation",
    "wilvor_ai.master_evidence_admission.MasterSourceAuthenticity",
    "wilvor_ai.master_historical_execution_readiness.historical_native_result_correlation_gaps",
    "wilvor_ai.master_historical_execution_readiness.SOURCE_VERSION_OWNER",
    "wilvor_ai.master_historical_execution_readiness.QUERY_ID_OWNER",
    "wilvor_ai.master_historical_binding.HISTORICAL_FINALIZATION_ENTRYPOINT",
    "wilvor_ai.master_decision_target_resolver.HostProofRequirement",
    "wilvor_ai.master_decision_target_resolver.REQUIRED_HOST_PROOFS",
    "wilvor_ai.master_decision_target_resolver.HOST_PROOF_ISSUER",
    "wilvor_ai.master_decision_target_resolver.release_master_decision_target",
    "wilvor_ai.live_ops_public_contracts.LiveOpsRunResult",
    "wilvor_ai.specialist_runtime_contracts.HistoricalSpecialistRunResult",
    "wilvor_ai.specialist_contracts.SpecialistResult",
    "wilvor_ai.decision_specialist_runtime_contracts.DecisionSpecialistRunResult",
)


def _enum_value(enum_type: type[Enum], value: object, field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ContractValidationError(f"invalid_{field_name}") from exc


def bind_host_invocation_identity_from_candidates(
    *candidates: Mapping[str, object],
) -> HostInvocationIdentity:
    """Bind one identity from one or more host mappings.

    A missing required field fails closed. Mappings that disagree are ambiguous.
    The returned identity is not authorization.
    """

    if not candidates:
        _fail("identity_missing")
    keys = _HOST_FIELDS + ("specialist", "purpose", "capability", "requirement")
    normalized: list[dict[str, object]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            _fail("identity_ambiguous")
        if any(name not in candidate or candidate[name] in (None, "") for name in keys):
            _fail("identity_missing")
        normalized.append(
            {
                "master_request_id": _token(candidate["master_request_id"], "master_request_id"),
                "planning_session_id": _token(candidate["planning_session_id"], "planning_session_id"),
                "step_id": _token(candidate["step_id"], "step_id"),
                "invocation_id": _token(candidate["invocation_id"], "invocation_id"),
                "specialist_request_id": _token(candidate["specialist_request_id"], "specialist_request_id"),
                "correlation_id": _token(candidate["correlation_id"], "correlation_id"),
                "specialist": _enum_value(MasterSpecialist, candidate["specialist"], "specialist"),
                "purpose": _enum_value(MasterPurpose, candidate["purpose"], "purpose"),
                "capability": _enum_value(AgentCapability, candidate["capability"], "capability"),
                "requirement": _enum_value(MasterRequirement, candidate["requirement"], "requirement"),
            }
        )
    first = normalized[0]
    for other in normalized[1:]:
        if any(other[name] != first[name] for name in keys):
            _fail("identity_ambiguous")
    return HostInvocationIdentity(
        master_request_id=first["master_request_id"],
        planning_session_id=first["planning_session_id"],
        step_id=first["step_id"],
        invocation_id=first["invocation_id"],
        specialist_request_id=first["specialist_request_id"],
        correlation_id=first["correlation_id"],
        specialist=first["specialist"],
        purpose=first["purpose"],
        capability=first["capability"],
        requirement=first["requirement"],
    )


def bind_effective_execution_scope(
    envelope: SpecialistRequestEnvelope,
    temporal_scopes: tuple[TemporalScope, ...],
) -> EffectiveExecutionScope:
    """Describe the envelope scope. The scope is not redeemable."""

    if type(envelope) is not SpecialistRequestEnvelope:
        _fail("invalid_request_envelope")
    return EffectiveExecutionScope(
        identity=envelope.identity,
        temporal_scopes=temporal_scopes,
        resource_scope=envelope.resource_scope,
        region_scope=envelope.region_scope,
        historical_window_start_utc=envelope.historical_window_start_utc,
        historical_window_end_utc=envelope.historical_window_end_utc,
        decision_target_scope=envelope.decision_target_scope,
    )


def bind_issued_grant_reference(
    identity: HostInvocationIdentity,
    *,
    grant_ref: str,
    policy_revision: str,
    effective_scope: EffectiveExecutionScope,
    proof: object = None,
    copied_from_envelope: bool = False,
) -> IssuedGrantReference:
    """Name an unissued grant. ``proof`` is rejected rather than accepted."""

    if proof is not None:
        _fail("caller_proof_not_host_issued")
    return IssuedGrantReference(
        identity=identity,
        grant_ref=grant_ref,
        policy_revision=policy_revision,
        effective_scope=effective_scope,
        copied_from_envelope=copied_from_envelope,
    )


def reference_unissued_grant(
    envelope: SpecialistRequestEnvelope,
    scope: EffectiveExecutionScope,
    *,
    grant_ref: str,
    policy_revision: str,
) -> IssuedGrantReference:
    """Copy envelope identity into an unissued reference. Copying does not authorize."""

    if type(envelope) is not SpecialistRequestEnvelope:
        _fail("invalid_request_envelope")
    _same_identity(envelope.identity, scope.identity)
    return bind_issued_grant_reference(
        envelope.identity,
        grant_ref=grant_ref,
        policy_revision=policy_revision,
        effective_scope=scope,
        copied_from_envelope=True,
    )


def bind_native_specialist_execution_result(
    identity: HostInvocationIdentity,
    stage: NativeResultStage,
    native_status: LiveOpsRunStatus | SpecialistRunStatus | SpecialistStatus | DecisionSpecialistRunStatus,
    observed_result: object = None,
) -> NativeSpecialistExecutionResult:
    """Describe a native result and drop the object.

    A mapping is a serialized record, not a native result. A supplied instance
    must be the exact native type for ``stage`` and must carry ``native_status``.
    """

    if not isinstance(stage, NativeResultStage):
        _fail("invalid_native_result")
    if isinstance(observed_result, Mapping):
        _fail("serialized_record_not_native_result")
    observed = observed_result is not None
    if observed:
        expected = _RESULT_TYPES[stage]
        if type(observed_result) is not expected:
            _fail("specialist_artifact_mismatch")
        actual = getattr(observed_result, _STATUS_ATTRIBUTES[stage], None)
        if actual is not native_status:
            _fail("invalid_status")
    result_type = _RESULT_TYPES[stage]
    return NativeSpecialistExecutionResult(
        identity=identity,
        stage=stage,
        native_status=native_status,
        observed=observed,
        present_host_fields=_present_host_fields(result_type),
        missing_host_fields=_missing_host_fields(result_type),
    )


def _classify_proof(proof: object) -> FalseProofKind:
    if type(proof) is bool:
        return FalseProofKind.BOOLEAN
    if type(proof) is str:
        stripped = proof.strip()
        if stripped.startswith("{") or "schema_version" in stripped:
            return FalseProofKind.SERIALIZED_DATACLASS
        return FalseProofKind.STRING_CLAIM
    if isinstance(proof, Mapping):
        keys = set(proof)
        if "requestContext" in keys or "jwt" in keys:
            return FalseProofKind.JWT_DICTIONARY
        if "Authorization" in keys or "authorization" in keys:
            return FalseProofKind.AUTHORIZATION_HEADER
        if "body" in keys:
            return FalseProofKind.EVENT_BODY
        if any(
            key == "ENVIRONMENT" or key.startswith("AWS_") or key.startswith("WILVOR_")
            for key in keys
            if type(key) is str
        ):
            return FalseProofKind.ENVIRONMENT_CLAIM
        return FalseProofKind.SERIALIZED_DATACLASS
    if type(proof) is ApprovedPolicyDecision:
        return FalseProofKind.POLICY_CHECK
    if type(proof) in {
        HostInvocationIdentity,
        SpecialistRequestEnvelope,
        EffectiveExecutionScope,
        IssuedGrantReference,
        PrincipalRequirement,
        LedgerReservation,
        BudgetCancellation,
        ProvenanceHandoff,
    }:
        return FalseProofKind.SAME_ID_OBJECT
    if type(proof) in set(_RESULT_TYPES.values()) or type(proof) in {
        LiveOpsRunRequest,
        SpecialistRequest,
        DecisionSpecialistRequest,
    }:
        return FalseProofKind.NATIVE_RESULT
    return FalseProofKind.CALLER_OBJECT


def observe_authorization_proof(
    proof: object,
    identity: HostInvocationIdentity | None = None,
) -> AuthorizationProofObservation:
    """Classify caller proof. ``None`` stays unestablished. Nothing is issued."""

    if proof is None:
        return AuthorizationProofObservation(proof_kind=None, identity=identity)
    return AuthorizationProofObservation(
        proof_kind=_classify_proof(proof),
        identity=identity,
        status=RuntimeContractStatus.REJECTED_FALSE_PROOF,
    )


def reject_false_proof(proof: object, identity: HostInvocationIdentity | None = None) -> None:
    """Refuse every caller-supplied proof, including a proof that is absent."""

    observed = observe_authorization_proof(proof, identity)
    if observed.status is RuntimeContractStatus.NOT_ESTABLISHED:
        _fail("authorization_not_established")
    _fail("caller_proof_not_host_issued")


def issue_principal(_requirement: PrincipalRequirement) -> None:
    """Production principal issuance is closed."""

    _fail("principal_not_issued")


def issue_grant(_reference: IssuedGrantReference) -> None:
    """Production grant issuance is closed. A reference is not a grant."""

    _fail("grant_not_issued")


@dataclass(frozen=True)
class LocalSchedulerProtocolNote:
    """Descriptive split between scheduling, a scripted worker, and native execution.

    Constructing this note does not execute a specialist and does not admit
    evidence. ``PRODUCTION_HOST`` stays inactive.
    """

    scheduling_recorded: bool
    scripted_worker_reported: bool
    native_specialist_executed: bool = False
    evidence_admitted: bool = False
    aviation_evidence: bool = False
    production_host_active: bool = False
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    posture: TrustDistinction = TrustDistinction.DESCRIPTIVE
    status: RuntimeContractStatus = RuntimeContractStatus.NOT_ESTABLISHED
    schema_version: str = MASTER_SPECIALIST_RUNTIME_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _authority(
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            schema_version=self.schema_version,
        )
        _posture(self.posture, frozenset({TrustDistinction.DESCRIPTIVE}))
        _status(
            self.status,
            frozenset(
                {
                    RuntimeContractStatus.DESCRIBED,
                    RuntimeContractStatus.NOT_ESTABLISHED,
                    RuntimeContractStatus.REJECTED_FALSE_PROOF,
                }
            ),
        )
        if type(self.scheduling_recorded) is not bool or type(self.scripted_worker_reported) is not bool:
            _fail("invalid_status")
        if PRODUCTION_HOST is not None or self.production_host_active is not False:
            _fail("production_host_not_active")
        _closed_flag(self.native_specialist_executed, False, "specialist_invocation_not_granted")
        _closed_flag(self.evidence_admitted, False, "evidence_not_admitted")
        _closed_flag(self.aviation_evidence, False, "aviation_evidence_not_admitted")


def observe_local_scheduler_protocol(candidate: object) -> LocalSchedulerProtocolNote:
    """Describe a scheduler candidate without executing anything.

    A boolean, token, mapping, or callable is a false proof. Any other
    object, including a scripted worker or a scheduling record, stays
    unestablished. Scheduling completion is not native execution.
    """

    if PRODUCTION_HOST is not None:
        _fail("production_host_not_active")
    if candidate is None:
        return LocalSchedulerProtocolNote(
            scheduling_recorded=False,
            scripted_worker_reported=False,
            status=RuntimeContractStatus.NOT_ESTABLISHED,
        )
    if isinstance(candidate, (str, bytes, bytearray, bool, int, float, Mapping)) or callable(candidate):
        return LocalSchedulerProtocolNote(
            scheduling_recorded=False,
            scripted_worker_reported=False,
            status=RuntimeContractStatus.REJECTED_FALSE_PROOF,
        )
    return LocalSchedulerProtocolNote(
        scheduling_recorded=False,
        scripted_worker_reported=False,
        status=RuntimeContractStatus.NOT_ESTABLISHED,
    )


def invoke_specialist(*_args: object, **_kwargs: object) -> None:
    """Specialist dispatch is closed. This is not a dispatcher."""

    _fail("specialist_invocation_not_granted")


def admit_evidence(*_args: object, **_kwargs: object) -> None:
    """Evidence admission is closed."""

    _fail("evidence_not_admitted")


def construct_decision_native_request(*_args: object, **_kwargs: object) -> None:
    """Refuse to build ``DecisionSpecialistRequest``.

    The reviewed host has to establish source currentness, the allowed target
    population, and separate persisted-recommendation authorization before any
    other module constructs that request. A comparison id, a unique injected
    candidate, and this function's arguments are not that establishment.
    """

    _fail("decision_target_not_established")
