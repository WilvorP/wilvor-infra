"""Offline execution-policy assessment. Not a production policy engine.

The assessment reads one ``MasterExecutionScope`` and, optionally, a
descriptive candidate. It reports typed missing requirements and a denied
execution status. It does not authenticate a principal, approve a resource,
accept a region, establish a deadline, or permit a specialist to run.

Caller-supplied resource names, deadlines, booleans, policy decisions, and
region labels are candidates. They are not authority. A supported capability
is not dispatch permission. A structurally ready plan is not an execution
grant. Matching request identifiers are not trust.

Master execution authority stays ``DENIED``. Production authority stays
``DENIED``. Source authenticity stays ``NOT_ESTABLISHED``. Request coverage
stays ``NOT_ESTABLISHED``. There is no authenticated-principal constructor
and no branch that returns permission.

``SUPPORTED_CAPABILITIES`` and ``PROHIBITED_CAPABILITIES`` are the existing
v1 vocabularies, not a second list. ``SPECIALIST_CAPABILITY_PAIRS`` restates
the specialist pairs already enforced by routing and execution scope.
``READ_ONLY_TOOL_NAMES_BY_SPECIALIST`` restates the public read-only tool
catalog names. Recognition is not a resource grant. Unrestricted table
access, an unsupported capability, an arbitrary historical interval, and an
unbounded runtime stay denied.

PROPOSED, not imported and not implemented here:

- A principal authority might later prove an application principal. This
  module cannot construct one from a string, a model object, or object identity.
- A resource authority might later approve a read scope. A catalog name does
  not.
- A geometry authority might later validate a region. A label does not.
- A deadline authority might later bound a runtime. A caller timestamp does not.
- A dispatch permit might later be reviewed separately. This assessment is not
  that permit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from wilvor_ai.contracts import (
    ALLOWED_V1_CAPABILITIES,
    PROHIBITED_V1_CAPABILITIES,
    PROVENANCE_TOKEN_MAX_LENGTH,
    V1_AUTHORITY,
    AgentAuthorityMode,
    AgentCapability,
    ContractValidationError,
    TemporalScope,
    _validate_bounded_text,
    _validate_utc,
)
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_execution_scope import (
    MasterExecutionPrerequisite,
    MasterExecutionScope,
)
from wilvor_ai.master_routing import (
    MAX_HISTORICAL_WINDOW,
    MasterHintTrust,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
)
from wilvor_ai.master_specialist_adapters import (
    MasterAdapterExecutionState,
    MasterSpecialistAdapterDescriptor,
)


MASTER_EXECUTION_POLICY_SCHEMA_VERSION = "wilvor.ai.master_execution_policy_boundary.v1"

# Documentation only. Assessment never permits execution when these are true.
AUTHENTICATED_PRINCIPAL_AVAILABLE = False
PRODUCTION_POLICY_ENGINE_AVAILABLE = False

SUPPORTED_CAPABILITIES = ALLOWED_V1_CAPABILITIES
PROHIBITED_CAPABILITIES = PROHIBITED_V1_CAPABILITIES

# Restates the public specialist pairs. Membership is not execution permission.
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

# Restates live_ops_public_contracts.LIVE_OPS_MAX_TOOL_CALLS.
# A longer caller list is rejected. The cap is not permission to call tools.
MAX_DESCRIPTIVE_RESOURCE_CANDIDATES = 6

# Restates the public read-only catalogs. A listed name is not a table grant.
READ_ONLY_TOOL_NAMES_BY_SPECIALIST = {
    MasterSpecialist.LIVE_OPS: frozenset(
        {
            "search_current_hazards",
            "search_current_impacts",
            "search_current_encounters",
            "get_observed_network_state",
            "get_aircraft_operational_context",
            "get_hazard_operational_context",
            "get_airport_operational_context",
            "find_aircraft_by_callsign",
        }
    ),
    MasterSpecialist.HISTORICAL_ANALYTICS: frozenset(
        {
            "summarize_historical_encounters",
            "summarize_historical_risks",
            "summarize_historical_hazard_versions",
            "list_historical_encounters",
        }
    ),
    MasterSpecialist.DECISION: frozenset(
        {
            "get_current_decision_context",
            "get_current_risk_evidence",
            "get_current_recommendation",
            "get_persisted_airport_candidate_evidence",
        }
    ),
}

_UNRESTRICTED_RESOURCE_NAMES = frozenset(
    {"*", "ALL", "ALL_TABLES", "TABLE", "TABLES", "ANY", "UNRESTRICTED"}
)
_ALLOW_DECISIONS = frozenset(
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


class MasterExecutionPolicyStatus(str, Enum):
    """Closed execution status. Permitted is not a member."""

    DENIED = "DENIED"


class MasterExecutionPolicyRequirement(str, Enum):
    """Proof this assessment cannot supply. Every member stays missing."""

    AUTHENTICATED_PRINCIPAL = "AUTHENTICATED_PRINCIPAL"
    ALLOWED_SPECIALIST_CAPABILITY = "ALLOWED_SPECIALIST_CAPABILITY"
    APPROVED_READ_ONLY_RESOURCE_SCOPE = "APPROVED_READ_ONLY_RESOURCE_SCOPE"
    REGION_AUTHORITY = "REGION_AUTHORITY"
    TEMPORAL_SCOPE_AND_HISTORICAL_INTERVAL = "TEMPORAL_SCOPE_AND_HISTORICAL_INTERVAL"
    EXECUTION_DEADLINE_AND_BOUNDED_RUNTIME = "EXECUTION_DEADLINE_AND_BOUNDED_RUNTIME"
    REQUEST_AND_INVOCATION_IDENTITY = "REQUEST_AND_INVOCATION_IDENTITY"
    DECISION_TARGET_AUTHORIZATION = "DECISION_TARGET_AUTHORIZATION"
    UNRESOLVED_ROUTE_GATES = "UNRESOLVED_ROUTE_GATES"


class MasterExecutionPolicyRequirementStatus(str, Enum):
    """Fail-closed requirement status. Satisfied is not a member."""

    MISSING = "MISSING"
    DENIED = "DENIED"


class MasterExecutionPolicyDenialReason(str, Enum):
    """Why a requirement stays missing or is explicitly denied."""

    PRINCIPAL_NOT_AUTHENTICATED = "PRINCIPAL_NOT_AUTHENTICATED"
    PRINCIPAL_LABEL_NOT_AUTHENTICATION = "PRINCIPAL_LABEL_NOT_AUTHENTICATION"
    CALLER_POLICY_DECISION_NOT_AUTHORITY = "CALLER_POLICY_DECISION_NOT_AUTHORITY"
    POLICY_SPOOF_NOT_AUTHORITY = "POLICY_SPOOF_NOT_AUTHORITY"
    CAPABILITY_NOT_PERMISSION = "CAPABILITY_NOT_PERMISSION"
    PROHIBITED_CAPABILITY = "PROHIBITED_CAPABILITY"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    CAPABILITY_SPECIALIST_MISMATCH = "CAPABILITY_SPECIALIST_MISMATCH"
    READ_RESOURCE_SCOPE_NOT_ESTABLISHED = "READ_RESOURCE_SCOPE_NOT_ESTABLISHED"
    RECOGNIZED_TOOL_NAME_NOT_A_GRANT = "RECOGNIZED_TOOL_NAME_NOT_A_GRANT"
    RESOURCE_ESCALATION = "RESOURCE_ESCALATION"
    UNRESTRICTED_TABLE_ACCESS = "UNRESTRICTED_TABLE_ACCESS"
    REGION_AUTHORITY_NOT_ESTABLISHED = "REGION_AUTHORITY_NOT_ESTABLISHED"
    UNTRUSTED_REGION_LABEL = "UNTRUSTED_REGION_LABEL"
    TEMPORAL_SCOPE_NOT_AN_EXECUTION_GRANT = "TEMPORAL_SCOPE_NOT_AN_EXECUTION_GRANT"
    TEMPORAL_SCOPE_UNRESOLVED = "TEMPORAL_SCOPE_UNRESOLVED"
    CONTRADICTORY_TEMPORAL_SCOPE = "CONTRADICTORY_TEMPORAL_SCOPE"
    ARBITRARY_HISTORICAL_INTERVAL = "ARBITRARY_HISTORICAL_INTERVAL"
    DEADLINE_MISSING = "DEADLINE_MISSING"
    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
    CALLER_DEADLINE_NOT_AUTHORITY = "CALLER_DEADLINE_NOT_AUTHORITY"
    UNCONTROLLED_EXECUTION = "UNCONTROLLED_EXECUTION"
    CALLER_RUNTIME_BOUND_NOT_AUTHORITY = "CALLER_RUNTIME_BOUND_NOT_AUTHORITY"
    MATCHING_IDENTITY_NOT_AUTHENTICATION = "MATCHING_IDENTITY_NOT_AUTHENTICATION"
    FOREIGN_INVOCATION = "FOREIGN_INVOCATION"
    TARGET_NOT_AUTHORIZED = "TARGET_NOT_AUTHORIZED"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    APPLICATION_TARGET_CANDIDATE_NOT_AUTHORIZATION = (
        "APPLICATION_TARGET_CANDIDATE_NOT_AUTHORIZATION"
    )
    UNRESOLVED_ROUTE_GATE = "UNRESOLVED_ROUTE_GATE"
    NO_UNRESOLVED_GATE_IS_NOT_AUTHORIZATION = "NO_UNRESOLVED_GATE_IS_NOT_AUTHORIZATION"
    STRUCTURAL_READINESS_NOT_PERMISSION = "STRUCTURAL_READINESS_NOT_PERMISSION"


class MasterExecutionPolicyLimitation(str, Enum):
    """Standing limits. They are present on every assessment."""

    NO_AUTHENTICATED_PRINCIPAL = "NO_AUTHENTICATED_PRINCIPAL"
    NO_PRODUCTION_POLICY_ENGINE = "NO_PRODUCTION_POLICY_ENGINE"
    CALLER_CANDIDATE_NOT_AUTHORITY = "CALLER_CANDIDATE_NOT_AUTHORITY"
    CAPABILITY_MEMBERSHIP_NOT_DISPATCH_PERMISSION = (
        "CAPABILITY_MEMBERSHIP_NOT_DISPATCH_PERMISSION"
    )
    EXECUTION_AUTHORITY_REMAINS_DENIED = "EXECUTION_AUTHORITY_REMAINS_DENIED"
    PRODUCTION_AUTHORITY_REMAINS_DENIED = "PRODUCTION_AUTHORITY_REMAINS_DENIED"
    SOURCE_AUTHENTICITY_REMAINS_NOT_ESTABLISHED = (
        "SOURCE_AUTHENTICITY_REMAINS_NOT_ESTABLISHED"
    )
    REQUEST_COVERAGE_REMAINS_NOT_ESTABLISHED = "REQUEST_COVERAGE_REMAINS_NOT_ESTABLISHED"
    NOMINAL_PLAN_LACKS_EXECUTION_AUTHORIZATION = (
        "NOMINAL_PLAN_LACKS_EXECUTION_AUTHORIZATION"
    )


MASTER_EXECUTION_POLICY_LIMITATIONS = tuple(MasterExecutionPolicyLimitation)
_ALL_REQUIREMENTS = tuple(MasterExecutionPolicyRequirement)
_ALL_PREREQUISITES = tuple(MasterExecutionPrerequisite)

_PREREQUISITE_FOR = {
    MasterExecutionPolicyRequirement.AUTHENTICATED_PRINCIPAL: (
        MasterExecutionPrerequisite.AUTHENTICATED_APPLICATION_PRINCIPAL
    ),
    MasterExecutionPolicyRequirement.ALLOWED_SPECIALIST_CAPABILITY: (
        MasterExecutionPrerequisite.REVIEWED_EXECUTION_AUTHORITY
    ),
    MasterExecutionPolicyRequirement.APPROVED_READ_ONLY_RESOURCE_SCOPE: (
        MasterExecutionPrerequisite.AUTHORIZED_READ_RESOURCE_SCOPE
    ),
    MasterExecutionPolicyRequirement.REGION_AUTHORITY: (
        MasterExecutionPrerequisite.POPULATION_AND_GEOGRAPHIC_SCOPE
    ),
    MasterExecutionPolicyRequirement.TEMPORAL_SCOPE_AND_HISTORICAL_INTERVAL: (
        MasterExecutionPrerequisite.SPECIALIST_CLOCKS
    ),
    MasterExecutionPolicyRequirement.EXECUTION_DEADLINE_AND_BOUNDED_RUNTIME: (
        MasterExecutionPrerequisite.ESTABLISHED_EXECUTION_DEADLINE
    ),
    MasterExecutionPolicyRequirement.REQUEST_AND_INVOCATION_IDENTITY: (
        MasterExecutionPrerequisite.REQUEST_ENTITY_BINDING
    ),
    MasterExecutionPolicyRequirement.DECISION_TARGET_AUTHORIZATION: (
        MasterExecutionPrerequisite.SEPARATELY_AUTHORIZED_DECISION_TARGET
    ),
    MasterExecutionPolicyRequirement.UNRESOLVED_ROUTE_GATES: (
        MasterExecutionPrerequisite.VERIFIED_EXECUTION_POLICY
    ),
}

_CONFLICT_REASONS = frozenset(
    {
        MasterExecutionPolicyDenialReason.PRINCIPAL_LABEL_NOT_AUTHENTICATION,
        MasterExecutionPolicyDenialReason.CALLER_POLICY_DECISION_NOT_AUTHORITY,
        MasterExecutionPolicyDenialReason.POLICY_SPOOF_NOT_AUTHORITY,
        MasterExecutionPolicyDenialReason.PROHIBITED_CAPABILITY,
        MasterExecutionPolicyDenialReason.UNSUPPORTED_CAPABILITY,
        MasterExecutionPolicyDenialReason.CAPABILITY_SPECIALIST_MISMATCH,
        MasterExecutionPolicyDenialReason.RESOURCE_ESCALATION,
        MasterExecutionPolicyDenialReason.UNRESTRICTED_TABLE_ACCESS,
        MasterExecutionPolicyDenialReason.UNTRUSTED_REGION_LABEL,
        MasterExecutionPolicyDenialReason.TEMPORAL_SCOPE_UNRESOLVED,
        MasterExecutionPolicyDenialReason.CONTRADICTORY_TEMPORAL_SCOPE,
        MasterExecutionPolicyDenialReason.ARBITRARY_HISTORICAL_INTERVAL,
        MasterExecutionPolicyDenialReason.DEADLINE_EXPIRED,
        MasterExecutionPolicyDenialReason.UNCONTROLLED_EXECUTION,
        MasterExecutionPolicyDenialReason.FOREIGN_INVOCATION,
        MasterExecutionPolicyDenialReason.TARGET_AMBIGUOUS,
        MasterExecutionPolicyDenialReason.APPLICATION_TARGET_CANDIDATE_NOT_AUTHORIZATION,
        MasterExecutionPolicyDenialReason.UNRESOLVED_ROUTE_GATE,
    }
)

_LIVE_OPS_SCOPES = (TemporalScope.CURRENT,)
_HISTORICAL_SCOPES = (TemporalScope.HISTORICAL,)
_DECISION_SCOPES = {
    (TemporalScope.CURRENT,),
    (TemporalScope.PERSISTED,),
    (TemporalScope.CURRENT, TemporalScope.PERSISTED),
}


def _closed(value: object, denied: object, code: str) -> None:
    if value is not denied:
        raise ContractValidationError(code)


def _unique(
    reasons: tuple[MasterExecutionPolicyDenialReason, ...] | list[MasterExecutionPolicyDenialReason],
) -> tuple[MasterExecutionPolicyDenialReason, ...]:
    return tuple(dict.fromkeys(reasons))


def _status(
    reasons: tuple[MasterExecutionPolicyDenialReason, ...],
) -> MasterExecutionPolicyRequirementStatus:
    if any(reason in _CONFLICT_REASONS for reason in reasons):
        return MasterExecutionPolicyRequirementStatus.DENIED
    return MasterExecutionPolicyRequirementStatus.MISSING


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


def _prose(value: object, field_name: str) -> str:
    errors = _validate_bounded_text(
        value,
        field_name,
        max_length=PROVENANCE_TOKEN_MAX_LENGTH,
    )
    if errors:
        raise ContractValidationError(errors)
    if not isinstance(value, str) or value != value.strip():
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _utc(value: str, field_name: str) -> datetime:
    errors = _validate_utc(value, field_name)
    if errors:
        raise ContractValidationError(errors)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ContractValidationError(f"invalid_{field_name}") from exc
    return parsed


def _whole_second_utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or len(value) != 20 or not value.endswith("Z"):
        raise ContractValidationError(f"invalid_{field_name}")
    parsed = _utc(value, field_name)
    if parsed.microsecond != 0 or value[10] != "T":
        raise ContractValidationError(f"invalid_{field_name}")
    return parsed


def _optional_clock(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    _whole_second_utc(value, field_name)
    if not isinstance(value, str):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _require_vocabularies() -> None:
    if AUTHENTICATED_PRINCIPAL_AVAILABLE is not False or PRODUCTION_POLICY_ENGINE_AVAILABLE is not False:
        raise ContractValidationError("policy_authority_not_available")
    if V1_AUTHORITY.mode is not AgentAuthorityMode.READ_ONLY_ADVISORY:
        raise ContractValidationError("invalid_authority_mode")
    if V1_AUTHORITY.allowed_capabilities != ALLOWED_V1_CAPABILITIES:
        raise ContractValidationError("allowed_capability_vocabulary_mismatch")
    if V1_AUTHORITY.prohibited_capabilities != PROHIBITED_V1_CAPABILITIES:
        raise ContractValidationError("prohibited_capability_vocabulary_mismatch")
    if ALLOWED_V1_CAPABILITIES & PROHIBITED_V1_CAPABILITIES:
        raise ContractValidationError("authority_capabilities_overlap")
    if SUPPORTED_CAPABILITIES is not ALLOWED_V1_CAPABILITIES:
        raise ContractValidationError("allowed_capability_vocabulary_mismatch")
    if PROHIBITED_CAPABILITIES is not PROHIBITED_V1_CAPABILITIES:
        raise ContractValidationError("prohibited_capability_vocabulary_mismatch")


def _require_scope(scope: object) -> MasterExecutionScope:
    if type(scope) is not MasterExecutionScope:
        raise ContractValidationError("invalid_execution_scope")
    _closed(
        scope.execution_authority,
        MasterRouteExecutionAuthority.DENIED,
        "execution_authority_not_granted",
    )
    _closed(
        scope.production_authority,
        MasterProductionAuthority.DENIED,
        "production_authority_not_issued",
    )
    _closed(
        scope.source_authenticity,
        MasterSourceAuthenticity.NOT_ESTABLISHED,
        "source_authenticity_not_established",
    )
    _closed(
        scope.request_coverage,
        MasterRequestCoverage.NOT_ESTABLISHED,
        "request_coverage_not_established",
    )
    if (
        scope.execution_permission_granted is not False
        or scope.executable is not False
        or scope.dispatch_permitted is not False
        or scope.redeemable is not False
    ):
        raise ContractValidationError("execution_not_granted")
    if scope.target_authorization_granted is not False:
        raise ContractValidationError("target_authorization_not_granted")
    if scope.missing_prerequisites != _ALL_PREREQUISITES:
        raise ContractValidationError("prerequisite_not_satisfied")
    if not any(item is scope.planned_step for item in scope.route_plan.steps):
        raise ContractValidationError("foreign_step")
    if scope.planned_step.hint_trust is not MasterHintTrust.UNTRUSTED:
        raise ContractValidationError("selection_hints_not_trusted")
    descriptor = scope.descriptor
    if type(descriptor) is not MasterSpecialistAdapterDescriptor:
        raise ContractValidationError("invalid_adapter_descriptor")
    if (
        descriptor.execution_state
        is not MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
    ):
        raise ContractValidationError("execution_not_granted")
    if (
        descriptor.invocation_id != scope.invocation_id
        or descriptor.master_request_id != scope.master_request_id
        or descriptor.step_id != scope.step_id
    ):
        raise ContractValidationError("foreign_invocation")
    if descriptor.execution_authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
    if descriptor.decision_target_trusted is not False:
        raise ContractValidationError("target_authorization_not_granted")
    return scope


def _pair_member(capability: AgentCapability) -> bool:
    return any(capability in allowed for allowed in SPECIALIST_CAPABILITY_PAIRS.values())


@dataclass(frozen=True)
class MasterExecutionPolicyCandidate:
    """Descriptive caller input. None of these fields is authority.

    Construction accepts a spoofed decision, a resource wildcard, an expired
    deadline, or a foreign invocation id so the assessment can deny it. The
    object cannot be redeemed.
    """

    principal_label: str | None = None
    policy_decision: str | None = None
    execution_permitted: bool = False
    dispatch_permitted: bool = False
    resource_names: tuple[str, ...] = ()
    unrestricted_table_access: bool = False
    region_label: str | None = None
    region_authoritative: bool = False
    deadline_utc: str | None = None
    observed_utc: str | None = None
    runtime_bound_seconds: int | None = None
    unbounded_runtime: bool = False
    master_request_id: str | None = None
    invocation_id: str | None = None
    specialist_request_id: str | None = None
    correlation_id: str | None = None
    target_aircraft_id: str | None = None
    target_recommendation_id: str | None = None
    claimed_capability: AgentCapability | None = None
    historical_start_utc: str | None = None
    historical_end_utc: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "principal_label",
            None if self.principal_label is None else _token(self.principal_label, "principal_label"),
        )
        object.__setattr__(
            self,
            "policy_decision",
            None if self.policy_decision is None else _token(self.policy_decision, "policy_decision"),
        )
        object.__setattr__(self, "execution_permitted", _bool(self.execution_permitted, "execution_permitted"))
        object.__setattr__(self, "dispatch_permitted", _bool(self.dispatch_permitted, "dispatch_permitted"))
        resource_names = self.resource_names
        if not isinstance(resource_names, tuple) or any(not isinstance(item, str) for item in resource_names):
            raise ContractValidationError("invalid_resource_names")
        if len(resource_names) > MAX_DESCRIPTIVE_RESOURCE_CANDIDATES:
            raise ContractValidationError("excessive_resource_candidates")
        object.__setattr__(
            self,
            "resource_names",
            tuple(_prose(item, "resource_name") for item in resource_names),
        )
        object.__setattr__(
            self,
            "unrestricted_table_access",
            _bool(self.unrestricted_table_access, "unrestricted_table_access"),
        )
        object.__setattr__(
            self,
            "region_label",
            None if self.region_label is None else _prose(self.region_label, "region_label"),
        )
        object.__setattr__(
            self,
            "region_authoritative",
            _bool(self.region_authoritative, "region_authoritative"),
        )
        object.__setattr__(self, "deadline_utc", _optional_clock(self.deadline_utc, "deadline_utc"))
        object.__setattr__(self, "observed_utc", _optional_clock(self.observed_utc, "observed_utc"))
        runtime_bound_seconds = self.runtime_bound_seconds
        if runtime_bound_seconds is not None and (
            isinstance(runtime_bound_seconds, bool)
            or not isinstance(runtime_bound_seconds, int)
            or runtime_bound_seconds < 1
        ):
            raise ContractValidationError("invalid_runtime_bound_seconds")
        object.__setattr__(self, "unbounded_runtime", _bool(self.unbounded_runtime, "unbounded_runtime"))
        object.__setattr__(
            self,
            "master_request_id",
            None if self.master_request_id is None else _token(self.master_request_id, "master_request_id"),
        )
        object.__setattr__(
            self,
            "invocation_id",
            None if self.invocation_id is None else _token(self.invocation_id, "invocation_id"),
        )
        object.__setattr__(
            self,
            "specialist_request_id",
            None
            if self.specialist_request_id is None
            else _token(self.specialist_request_id, "specialist_request_id"),
        )
        object.__setattr__(
            self,
            "correlation_id",
            None if self.correlation_id is None else _token(self.correlation_id, "correlation_id"),
        )
        object.__setattr__(
            self,
            "target_aircraft_id",
            None if self.target_aircraft_id is None else _token(self.target_aircraft_id, "target_aircraft_id"),
        )
        object.__setattr__(
            self,
            "target_recommendation_id",
            None
            if self.target_recommendation_id is None
            else _token(self.target_recommendation_id, "target_recommendation_id"),
        )
        if self.claimed_capability is not None and not isinstance(self.claimed_capability, AgentCapability):
            raise ContractValidationError("invalid_claimed_capability")
        if (self.historical_start_utc is None) != (self.historical_end_utc is None):
            raise ContractValidationError("missing_historical_interval")
        object.__setattr__(
            self,
            "historical_start_utc",
            _optional_clock(self.historical_start_utc, "historical_start_utc"),
        )
        object.__setattr__(
            self,
            "historical_end_utc",
            _optional_clock(self.historical_end_utc, "historical_end_utc"),
        )


def _require_candidate(candidate: object) -> MasterExecutionPolicyCandidate | None:
    if candidate is None:
        return None
    if type(candidate) is not MasterExecutionPolicyCandidate:
        raise ContractValidationError("invalid_policy_candidate")
    return candidate


@dataclass(frozen=True)
class MasterExecutionPolicyFinding:
    """One requirement that this assessment leaves missing or denied."""

    requirement: MasterExecutionPolicyRequirement
    status: MasterExecutionPolicyRequirementStatus
    prerequisite: MasterExecutionPrerequisite
    reasons: tuple[MasterExecutionPolicyDenialReason, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.requirement, MasterExecutionPolicyRequirement):
            raise ContractValidationError("invalid_policy_requirement")
        if not isinstance(self.status, MasterExecutionPolicyRequirementStatus):
            raise ContractValidationError("invalid_requirement_status")
        if self.prerequisite is not _PREREQUISITE_FOR[self.requirement]:
            raise ContractValidationError("prerequisite_not_satisfied")
        if not isinstance(self.reasons, tuple) or not self.reasons or any(
            not isinstance(item, MasterExecutionPolicyDenialReason) for item in self.reasons
        ):
            raise ContractValidationError("invalid_denial_reasons")
        if self.status is not _status(self.reasons):
            raise ContractValidationError("requirement_status_mismatch")


@dataclass(frozen=True)
class MasterExecutionPolicyAssessment:
    """Denied policy result. It cannot be rewritten into permission."""

    master_request_id: str
    invocation_id: str
    step_id: str
    specialist: MasterSpecialist
    capability: AgentCapability
    unresolved_gates: tuple[MasterRouteGate, ...]
    findings: tuple[MasterExecutionPolicyFinding, ...]
    schema_version: str = MASTER_EXECUTION_POLICY_SCHEMA_VERSION
    execution_status: MasterExecutionPolicyStatus = MasterExecutionPolicyStatus.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    execution_permitted: bool = False
    dispatch_permitted: bool = False
    specialist_dispatched: bool = False
    caller_candidate_is_authority: bool = False
    stops_before_execution_authorization: bool = True
    missing_requirements: tuple[MasterExecutionPolicyRequirement, ...] = _ALL_REQUIREMENTS
    missing_prerequisites: tuple[MasterExecutionPrerequisite, ...] = _ALL_PREREQUISITES
    limitations: tuple[MasterExecutionPolicyLimitation, ...] = MASTER_EXECUTION_POLICY_LIMITATIONS
    denied_requirements: tuple[MasterExecutionPolicyRequirement, ...] = field(init=False)
    denial_reasons: tuple[MasterExecutionPolicyDenialReason, ...] = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_EXECUTION_POLICY_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _closed(self.execution_status, MasterExecutionPolicyStatus.DENIED, "execution_not_granted")
        _closed(self.execution_authority, MasterRouteExecutionAuthority.DENIED, "execution_authority_not_granted")
        _closed(self.production_authority, MasterProductionAuthority.DENIED, "production_authority_not_issued")
        _closed(
            self.source_authenticity,
            MasterSourceAuthenticity.NOT_ESTABLISHED,
            "source_authenticity_not_established",
        )
        _closed(self.request_coverage, MasterRequestCoverage.NOT_ESTABLISHED, "request_coverage_not_established")
        if (
            self.execution_permitted is not False
            or self.dispatch_permitted is not False
            or self.specialist_dispatched is not False
            or self.caller_candidate_is_authority is not False
        ):
            raise ContractValidationError("execution_not_granted")
        if self.stops_before_execution_authorization is not True:
            raise ContractValidationError("execution_not_granted")
        if self.missing_requirements != _ALL_REQUIREMENTS:
            raise ContractValidationError("missing_requirement_not_reported")
        if self.missing_prerequisites != _ALL_PREREQUISITES:
            raise ContractValidationError("prerequisite_not_satisfied")
        if self.limitations != MASTER_EXECUTION_POLICY_LIMITATIONS:
            raise ContractValidationError("policy_limitation_not_reported")
        if not isinstance(self.findings, tuple) or tuple(
            item.requirement for item in self.findings
        ) != _ALL_REQUIREMENTS:
            raise ContractValidationError("missing_requirement_not_reported")
        if any(type(item) is not MasterExecutionPolicyFinding for item in self.findings):
            raise ContractValidationError("invalid_policy_finding")
        if not isinstance(self.unresolved_gates, tuple) or any(
            not isinstance(item, MasterRouteGate) or item is MasterRouteGate.NOT_REQUIRED
            for item in self.unresolved_gates
        ):
            raise ContractValidationError("invalid_unresolved_gates")
        if not isinstance(self.specialist, MasterSpecialist) or not isinstance(self.capability, AgentCapability):
            raise ContractValidationError("invalid_execution_scope")
        object.__setattr__(
            self,
            "denial_reasons",
            _unique(tuple(reason for finding in self.findings for reason in finding.reasons)),
        )
        object.__setattr__(
            self,
            "denied_requirements",
            tuple(
                finding.requirement
                for finding in self.findings
                if finding.status is MasterExecutionPolicyRequirementStatus.DENIED
            ),
        )


def _finding(
    requirement: MasterExecutionPolicyRequirement,
    reasons: tuple[MasterExecutionPolicyDenialReason, ...] | list[MasterExecutionPolicyDenialReason],
) -> MasterExecutionPolicyFinding:
    normalized = _unique(reasons)
    return MasterExecutionPolicyFinding(
        requirement,
        _status(normalized),
        _PREREQUISITE_FOR[requirement],
        normalized,
    )


def _principal_reasons(
    candidate: MasterExecutionPolicyCandidate | None,
) -> tuple[MasterExecutionPolicyDenialReason, ...]:
    reasons = [MasterExecutionPolicyDenialReason.PRINCIPAL_NOT_AUTHENTICATED]
    if candidate is None:
        return tuple(reasons)
    if candidate.principal_label is not None:
        reasons.append(MasterExecutionPolicyDenialReason.PRINCIPAL_LABEL_NOT_AUTHENTICATION)
    if candidate.policy_decision is not None:
        reasons.append(MasterExecutionPolicyDenialReason.CALLER_POLICY_DECISION_NOT_AUTHORITY)
        if candidate.policy_decision.upper() in _ALLOW_DECISIONS:
            reasons.append(MasterExecutionPolicyDenialReason.POLICY_SPOOF_NOT_AUTHORITY)
    if candidate.execution_permitted or candidate.dispatch_permitted:
        reasons.append(MasterExecutionPolicyDenialReason.POLICY_SPOOF_NOT_AUTHORITY)
    return _unique(reasons)


def _capability_reasons(
    scope: MasterExecutionScope,
    candidate: MasterExecutionPolicyCandidate | None,
) -> tuple[MasterExecutionPolicyDenialReason, ...]:
    reasons: list[MasterExecutionPolicyDenialReason] = []
    allowed = SPECIALIST_CAPABILITY_PAIRS.get((scope.specialist, scope.purpose))
    capability = scope.capability
    if (
        capability in PROHIBITED_CAPABILITIES
        or capability not in SUPPORTED_CAPABILITIES
        or allowed is None
        or capability not in allowed
    ):
        if capability in PROHIBITED_CAPABILITIES:
            reasons.append(MasterExecutionPolicyDenialReason.PROHIBITED_CAPABILITY)
        else:
            reasons.append(MasterExecutionPolicyDenialReason.UNSUPPORTED_CAPABILITY)
    else:
        reasons.append(MasterExecutionPolicyDenialReason.CAPABILITY_NOT_PERMISSION)
    claimed = None if candidate is None else candidate.claimed_capability
    if claimed is None:
        return tuple(reasons)
    if claimed in PROHIBITED_CAPABILITIES:
        reasons.append(MasterExecutionPolicyDenialReason.PROHIBITED_CAPABILITY)
    elif claimed not in SUPPORTED_CAPABILITIES or allowed is None or claimed not in allowed:
        if _pair_member(claimed):
            reasons.append(MasterExecutionPolicyDenialReason.CAPABILITY_SPECIALIST_MISMATCH)
        else:
            reasons.append(MasterExecutionPolicyDenialReason.UNSUPPORTED_CAPABILITY)
    elif claimed is not capability:
        reasons.append(MasterExecutionPolicyDenialReason.CAPABILITY_SPECIALIST_MISMATCH)
    return _unique(reasons)


def _unrestricted_resource(name: str) -> bool:
    folded = name.casefold().upper()
    return folded in _UNRESTRICTED_RESOURCE_NAMES or "*" in name or any(character.isspace() for character in name)


def _resource_reasons(
    scope: MasterExecutionScope,
    candidate: MasterExecutionPolicyCandidate | None,
) -> tuple[MasterExecutionPolicyDenialReason, ...]:
    reasons = [MasterExecutionPolicyDenialReason.READ_RESOURCE_SCOPE_NOT_ESTABLISHED]
    if candidate is None:
        return tuple(reasons)
    catalog = READ_ONLY_TOOL_NAMES_BY_SPECIALIST.get(scope.specialist, frozenset())
    recognized = False
    escalated = False
    unrestricted = candidate.unrestricted_table_access
    for name in candidate.resource_names:
        if _unrestricted_resource(name):
            unrestricted = True
            continue
        if name in catalog:
            recognized = True
            continue
        escalated = True
    if unrestricted:
        reasons.append(MasterExecutionPolicyDenialReason.UNRESTRICTED_TABLE_ACCESS)
    if escalated:
        reasons.append(MasterExecutionPolicyDenialReason.RESOURCE_ESCALATION)
    if recognized:
        reasons.append(MasterExecutionPolicyDenialReason.RECOGNIZED_TOOL_NAME_NOT_A_GRANT)
    return _unique(reasons)


def _region_reasons(
    scope: MasterExecutionScope,
    candidate: MasterExecutionPolicyCandidate | None,
) -> tuple[MasterExecutionPolicyDenialReason, ...]:
    reasons: list[MasterExecutionPolicyDenialReason] = []
    if (
        scope.planned_step.region_text is not None
        or MasterRouteGate.REGION_VALIDATION_PENDING in scope.gates
    ):
        reasons.append(MasterExecutionPolicyDenialReason.UNTRUSTED_REGION_LABEL)
    if candidate is not None and candidate.region_label is not None:
        reasons.append(MasterExecutionPolicyDenialReason.UNTRUSTED_REGION_LABEL)
    if candidate is not None and candidate.region_authoritative:
        reasons.append(MasterExecutionPolicyDenialReason.POLICY_SPOOF_NOT_AUTHORITY)
    if not reasons:
        reasons.append(MasterExecutionPolicyDenialReason.REGION_AUTHORITY_NOT_ESTABLISHED)
    return _unique(reasons)


def _interval_arbitrary(start: str, end: str) -> bool:
    parsed_start = _whole_second_utc(start, "historical_start_utc")
    parsed_end = _whole_second_utc(end, "historical_end_utc")
    return parsed_start >= parsed_end or parsed_end - parsed_start > MAX_HISTORICAL_WINDOW


def _scope_temporal_conflict(scope: MasterExecutionScope) -> MasterExecutionPolicyDenialReason | None:
    scopes = scope.temporal_scopes
    specialist = scope.specialist
    unresolved = (
        MasterRouteGate.TEMPORAL_SCOPE_UNRESOLVED in scope.gates
        or not scopes
    )
    if unresolved:
        return MasterExecutionPolicyDenialReason.TEMPORAL_SCOPE_UNRESOLVED
    if specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
        if scopes != _HISTORICAL_SCOPES:
            return MasterExecutionPolicyDenialReason.CONTRADICTORY_TEMPORAL_SCOPE
        start = scope.historical_start_utc
        end = scope.historical_end_utc
        if start is None or end is None or _interval_arbitrary(start, end):
            return MasterExecutionPolicyDenialReason.ARBITRARY_HISTORICAL_INTERVAL
        return None
    if scope.historical_start_utc is not None or scope.historical_end_utc is not None:
        return MasterExecutionPolicyDenialReason.ARBITRARY_HISTORICAL_INTERVAL
    if specialist is MasterSpecialist.LIVE_OPS:
        if scopes != _LIVE_OPS_SCOPES:
            return MasterExecutionPolicyDenialReason.CONTRADICTORY_TEMPORAL_SCOPE
        return None
    if scopes not in _DECISION_SCOPES:
        return MasterExecutionPolicyDenialReason.CONTRADICTORY_TEMPORAL_SCOPE
    return None


def _temporal_reasons(
    scope: MasterExecutionScope,
    candidate: MasterExecutionPolicyCandidate | None,
) -> tuple[MasterExecutionPolicyDenialReason, ...]:
    reasons: list[MasterExecutionPolicyDenialReason] = []
    conflict = _scope_temporal_conflict(scope)
    if conflict is not None:
        reasons.append(conflict)
    if candidate is not None and candidate.historical_start_utc is not None:
        start = candidate.historical_start_utc
        end = candidate.historical_end_utc
        if (
            scope.specialist is not MasterSpecialist.HISTORICAL_ANALYTICS
            or start != scope.historical_start_utc
            or end != scope.historical_end_utc
            or end is None
            or _interval_arbitrary(start, end)
        ):
            reasons.append(MasterExecutionPolicyDenialReason.ARBITRARY_HISTORICAL_INTERVAL)
    if not reasons:
        reasons.append(MasterExecutionPolicyDenialReason.TEMPORAL_SCOPE_NOT_AN_EXECUTION_GRANT)
    return _unique(reasons)


def _deadline_reasons(
    scope: MasterExecutionScope,
    candidate: MasterExecutionPolicyCandidate | None,
) -> tuple[MasterExecutionPolicyDenialReason, ...]:
    reasons: list[MasterExecutionPolicyDenialReason] = []
    deadline = None if candidate is None else candidate.deadline_utc
    if deadline is None:
        reasons.append(MasterExecutionPolicyDenialReason.DEADLINE_MISSING)
    else:
        parsed_deadline = _whole_second_utc(deadline, "deadline_utc")
        request_time = _utc(scope.application_request_time_utc, "application_request_time_utc")
        expired = parsed_deadline <= request_time
        observed = None if candidate is None else candidate.observed_utc
        if observed is not None and parsed_deadline <= _whole_second_utc(observed, "observed_utc"):
            expired = True
        if expired:
            reasons.append(MasterExecutionPolicyDenialReason.DEADLINE_EXPIRED)
        else:
            reasons.append(MasterExecutionPolicyDenialReason.CALLER_DEADLINE_NOT_AUTHORITY)
    unbounded = candidate is None or candidate.unbounded_runtime or candidate.runtime_bound_seconds is None
    if unbounded:
        reasons.append(MasterExecutionPolicyDenialReason.UNCONTROLLED_EXECUTION)
    else:
        reasons.append(MasterExecutionPolicyDenialReason.CALLER_RUNTIME_BOUND_NOT_AUTHORITY)
    return tuple(reasons)


def _identity_reasons(
    scope: MasterExecutionScope,
    candidate: MasterExecutionPolicyCandidate | None,
) -> tuple[MasterExecutionPolicyDenialReason, ...]:
    invocation = scope.planned_step.invocation
    foreign = (
        scope.master_request_id != scope.planning_context.master_request_id
        or scope.invocation_id != invocation.invocation_id
        or scope.correlation_id != scope.planning_context.correlation_id
        or scope.specialist_request_id != invocation.specialist_request_id
    )
    if candidate is not None:
        supplied = (
            (candidate.master_request_id, scope.master_request_id),
            (candidate.invocation_id, scope.invocation_id),
            (candidate.specialist_request_id, scope.specialist_request_id),
            (candidate.correlation_id, scope.correlation_id),
        )
        if any(value is not None and value != expected for value, expected in supplied):
            foreign = True
    if foreign:
        return (MasterExecutionPolicyDenialReason.FOREIGN_INVOCATION,)
    return (MasterExecutionPolicyDenialReason.MATCHING_IDENTITY_NOT_AUTHENTICATION,)


def _target_reasons(
    scope: MasterExecutionScope,
    candidate: MasterExecutionPolicyCandidate | None,
) -> tuple[MasterExecutionPolicyDenialReason, ...]:
    reasons = [MasterExecutionPolicyDenialReason.TARGET_NOT_AUTHORIZED]
    hints = scope.planned_step.untrusted_hints
    invocation = scope.planned_step.invocation
    ambiguous = (
        MasterRouteGate.TARGET_AMBIGUOUS in scope.gates
        or len(hints.population_aircraft_ids) > 1
        or (
            invocation.expected_aircraft_id is not None
            and invocation.expected_recommendation_id is not None
        )
    )
    if candidate is not None:
        if candidate.target_aircraft_id is not None and candidate.target_recommendation_id is not None:
            ambiguous = True
        if (
            candidate.target_aircraft_id is not None
            and invocation.expected_aircraft_id is not None
            and candidate.target_aircraft_id != invocation.expected_aircraft_id
        ):
            ambiguous = True
        if (
            candidate.target_recommendation_id is not None
            and invocation.expected_recommendation_id is not None
            and candidate.target_recommendation_id != invocation.expected_recommendation_id
        ):
            ambiguous = True
    if ambiguous:
        reasons.append(MasterExecutionPolicyDenialReason.TARGET_AMBIGUOUS)
    context = scope.planning_context
    if (
        context.target_candidate_aircraft_id is not None
        or context.target_candidate_recommendation_id is not None
        or MasterRouteGate.APPLICATION_TARGET_CANDIDATE_PRESENT in scope.gates
    ):
        reasons.append(MasterExecutionPolicyDenialReason.APPLICATION_TARGET_CANDIDATE_NOT_AUTHORIZATION)
    return _unique(reasons)


def _gate_reasons(
    scope: MasterExecutionScope,
) -> tuple[tuple[MasterExecutionPolicyDenialReason, ...], tuple[MasterRouteGate, ...]]:
    unresolved = tuple(gate for gate in scope.gates if gate is not MasterRouteGate.NOT_REQUIRED)
    if unresolved:
        return (MasterExecutionPolicyDenialReason.UNRESOLVED_ROUTE_GATE,), unresolved
    return (
        (
            MasterExecutionPolicyDenialReason.NO_UNRESOLVED_GATE_IS_NOT_AUTHORIZATION,
            MasterExecutionPolicyDenialReason.STRUCTURAL_READINESS_NOT_PERMISSION,
        ),
        (),
    )


def _reject_promoted_status(
    *,
    execution_status: object,
    execution_authority: object,
    production_authority: object,
    source_authenticity: object,
    execution_permitted: object,
    dispatch_permitted: object,
) -> None:
    _closed(execution_status, MasterExecutionPolicyStatus.DENIED, "execution_not_granted")
    _closed(execution_authority, MasterRouteExecutionAuthority.DENIED, "execution_authority_not_granted")
    _closed(production_authority, MasterProductionAuthority.DENIED, "production_authority_not_issued")
    _closed(source_authenticity, MasterSourceAuthenticity.NOT_ESTABLISHED, "source_authenticity_not_established")
    if execution_permitted is not False or dispatch_permitted is not False:
        raise ContractValidationError("execution_not_granted")


def assess_master_execution_policy(
    scope: MasterExecutionScope,
    candidate: MasterExecutionPolicyCandidate | None = None,
    *,
    execution_status: object = MasterExecutionPolicyStatus.DENIED,
    execution_authority: object = MasterRouteExecutionAuthority.DENIED,
    production_authority: object = MasterProductionAuthority.DENIED,
    source_authenticity: object = MasterSourceAuthenticity.NOT_ESTABLISHED,
    execution_permitted: object = False,
    dispatch_permitted: object = False,
) -> MasterExecutionPolicyAssessment:
    """Assess one bound scope. The result never permits execution.

    ``candidate`` is descriptive. A policy decision, principal label, resource
    name, region label, deadline, or boolean on it cannot satisfy a requirement.
    """

    _require_vocabularies()
    _reject_promoted_status(
        execution_status=execution_status,
        execution_authority=execution_authority,
        production_authority=production_authority,
        source_authenticity=source_authenticity,
        execution_permitted=execution_permitted,
        dispatch_permitted=dispatch_permitted,
    )
    bound = _require_scope(scope)
    supplied = _require_candidate(candidate)
    gate_reasons, unresolved = _gate_reasons(bound)
    findings = (
        _finding(
            MasterExecutionPolicyRequirement.AUTHENTICATED_PRINCIPAL,
            _principal_reasons(supplied),
        ),
        _finding(
            MasterExecutionPolicyRequirement.ALLOWED_SPECIALIST_CAPABILITY,
            _capability_reasons(bound, supplied),
        ),
        _finding(
            MasterExecutionPolicyRequirement.APPROVED_READ_ONLY_RESOURCE_SCOPE,
            _resource_reasons(bound, supplied),
        ),
        _finding(
            MasterExecutionPolicyRequirement.REGION_AUTHORITY,
            _region_reasons(bound, supplied),
        ),
        _finding(
            MasterExecutionPolicyRequirement.TEMPORAL_SCOPE_AND_HISTORICAL_INTERVAL,
            _temporal_reasons(bound, supplied),
        ),
        _finding(
            MasterExecutionPolicyRequirement.EXECUTION_DEADLINE_AND_BOUNDED_RUNTIME,
            _deadline_reasons(bound, supplied),
        ),
        _finding(
            MasterExecutionPolicyRequirement.REQUEST_AND_INVOCATION_IDENTITY,
            _identity_reasons(bound, supplied),
        ),
        _finding(
            MasterExecutionPolicyRequirement.DECISION_TARGET_AUTHORIZATION,
            _target_reasons(bound, supplied),
        ),
        _finding(
            MasterExecutionPolicyRequirement.UNRESOLVED_ROUTE_GATES,
            gate_reasons,
        ),
    )
    return MasterExecutionPolicyAssessment(
        master_request_id=bound.master_request_id,
        invocation_id=bound.invocation_id,
        step_id=bound.step_id,
        specialist=bound.specialist,
        capability=bound.capability,
        unresolved_gates=unresolved,
        findings=findings,
        missing_prerequisites=bound.missing_prerequisites,
    )
