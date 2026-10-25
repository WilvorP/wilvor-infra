"""Non-authorizing assessment of session origin and invocation selection.

Five concepts stay distinct:

1. Caller-supplied planning context. ``MasterPlanningContext`` is an
   application planning input. It has no serializer and grants nothing.
2. Process-local registry membership. ``MasterPlanningSessionRegistry``
   checks the minted record by object identity. ``session_id`` is a label.
3. Retained route decision identity. ``require_retained_decision`` accepts
   only the ``authorize_route`` object stored for that record. A dict, a
   different type, or a field-equal copy is not that object.
4. Authenticated application principal. Not available. The first three do
   not establish one. Matching identifiers, object identity, user text, and
   model output do not establish one.
5. Authorized multi-invocation execution session. Not available. The first
   three do not establish one.

``associate_invocation`` selects at most one invocation object that is
already on the retained plan. A collection is rejected. After one selection,
a later presentation is rejected and does not replace the first. Selection
does not authorize execution. A multi-step plan, including a repeated
non-decision specialist, can be retained, and the remaining invocations
cannot be selected on that same planning identity.

The missing capability is to select and execute every planned invocation
without minting another planning identity and without changing the original
plan. ``open_session`` mints a new session id. ``retain_route`` rejects a
replacement plan. This module records that gap. It does not construct an
authenticated session, a production principal, a dispatcher, an execution
grant, or a trusted fanout.

Master execution authority stays ``DENIED``. Production authority stays
``DENIED``. Source authenticity stays ``NOT_ESTABLISHED``. Request coverage
stays ``NOT_ESTABLISHED``. No specialist, model, AWS API, or provider is called.
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
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_planning_session import (
    MasterPlanningSessionRecord,
    MasterPlanningSessionRegistry,
    MasterPlanningSessionState,
)
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterRequestCoverage,
    MasterRouteDecision,
    MasterRouteExecutionAuthority,
    MasterRoutePlan,
    MasterRouteStepPlan,
)
from wilvor_ai.model_contracts import ModelDecision, ModelTurnRequest


MASTER_SESSION_ORIGIN_BOUNDARY_SCHEMA_VERSION = "wilvor.ai.master_session_origin_boundary.v1"

# There is no constructor that flips either constant.
AUTHENTICATED_APPLICATION_PRINCIPAL_AVAILABLE = False
AUTHORIZED_MULTI_INVOCATION_EXECUTION_AVAILABLE = False
INVOCATION_SELECTION_LIMIT = 1

_SCHEMA = MASTER_SESSION_ORIGIN_BOUNDARY_SCHEMA_VERSION


class SessionOriginConcept(str, Enum):
    """The five origins this assessment keeps apart."""

    CALLER_SUPPLIED_PLANNING_CONTEXT = "CALLER_SUPPLIED_PLANNING_CONTEXT"
    PROCESS_LOCAL_REGISTRY_MEMBERSHIP = "PROCESS_LOCAL_REGISTRY_MEMBERSHIP"
    RETAINED_ROUTE_DECISION_IDENTITY = "RETAINED_ROUTE_DECISION_IDENTITY"
    AUTHENTICATED_APPLICATION_PRINCIPAL = "AUTHENTICATED_APPLICATION_PRINCIPAL"
    AUTHORIZED_MULTI_INVOCATION_EXECUTION_SESSION = (
        "AUTHORIZED_MULTI_INVOCATION_EXECUTION_SESSION"
    )


class SessionOriginStanding(str, Enum):
    """What can be recorded for one concept.

    ``OBSERVED`` means this process can see the planning object.
    It does not authenticate a principal or authorize execution.
    """

    OBSERVED = "OBSERVED"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    REJECTED = "REJECTED"


class RegistryMembershipStanding(str, Enum):
    """Process-local membership. None of these values authenticate a caller."""

    MEMBER = "MEMBER"
    COPIED_SESSION = "COPIED_SESSION"
    FOREIGN_SESSION = "FOREIGN_SESSION"
    CLOSED = "CLOSED"
    INVALIDATED = "INVALIDATED"


class DecisionIdentityStanding(str, Enum):
    """How a presented decision relates to the object the registry retained."""

    RETAINED = "RETAINED"
    DESERIALIZED = "DESERIALIZED"
    FORGED = "FORGED"
    RECONSTRUCTED = "RECONSTRUCTED"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class InvocationSelectionStanding(str, Enum):
    """Read-only view of the one-selection restriction. Not an execution grant."""

    NOT_SELECTABLE = "NOT_SELECTABLE"
    ONE_CANDIDATE = "ONE_CANDIDATE"
    MULTIPLE_REJECTED = "MULTIPLE_REJECTED"
    SECOND_SELECTION_REJECTED = "SECOND_SELECTION_REJECTED"
    COPIED_IDENTIFIER = "COPIED_IDENTIFIER"
    FOREIGN_INVOCATION = "FOREIGN_INVOCATION"
    FORGED = "FORGED"


class AuthenticationClaimKind(str, Enum):
    """A presented authentication attempt. Every kind is rejected."""

    MATCHING_IDENTIFIER = "MATCHING_IDENTIFIER"
    OBJECT_IDENTITY = "OBJECT_IDENTITY"
    USER_TEXT = "USER_TEXT"
    MODEL_OUTPUT = "MODEL_OUTPUT"


class SessionOriginLimitation(str, Enum):
    """Standing limits. The full set is present on every assessment."""

    CALLER_CONTEXT_IS_NOT_A_PRINCIPAL = "CALLER_CONTEXT_IS_NOT_A_PRINCIPAL"
    REGISTRY_MEMBERSHIP_IS_NOT_AUTHENTICATION = "REGISTRY_MEMBERSHIP_IS_NOT_AUTHENTICATION"
    RETAINED_DECISION_IS_NOT_AUTHENTICATION = "RETAINED_DECISION_IS_NOT_AUTHENTICATION"
    OBSERVED_ORIGIN_DOES_NOT_ESTABLISH_PRINCIPAL = (
        "OBSERVED_ORIGIN_DOES_NOT_ESTABLISH_PRINCIPAL"
    )
    OBSERVED_ORIGIN_DOES_NOT_ESTABLISH_EXECUTION_SESSION = (
        "OBSERVED_ORIGIN_DOES_NOT_ESTABLISH_EXECUTION_SESSION"
    )
    MATCHING_IDENTIFIER_IS_NOT_AUTHENTICATION = "MATCHING_IDENTIFIER_IS_NOT_AUTHENTICATION"
    OBJECT_IDENTITY_IS_NOT_AUTHENTICATION = "OBJECT_IDENTITY_IS_NOT_AUTHENTICATION"
    USER_TEXT_IS_NOT_AUTHENTICATION = "USER_TEXT_IS_NOT_AUTHENTICATION"
    MODEL_OUTPUT_IS_NOT_AUTHENTICATION = "MODEL_OUTPUT_IS_NOT_AUTHENTICATION"
    ONE_INVOCATION_SELECTION_ONLY = "ONE_INVOCATION_SELECTION_ONLY"
    SECOND_SELECTION_DOES_NOT_REPLACE = "SECOND_SELECTION_DOES_NOT_REPLACE"
    SELECTION_DOES_NOT_AUTHORIZE_EXECUTION = "SELECTION_DOES_NOT_AUTHORIZE_EXECUTION"
    MULTI_INVOCATION_EXECUTION_NOT_AVAILABLE = "MULTI_INVOCATION_EXECUTION_NOT_AVAILABLE"
    ADDITIONAL_PLANNING_IDENTITY_IS_NOT_FANOUT = "ADDITIONAL_PLANNING_IDENTITY_IS_NOT_FANOUT"
    RETAINED_PLAN_IS_NOT_REPLACED = "RETAINED_PLAN_IS_NOT_REPLACED"
    PROPOSED_FANOUT_IS_NOT_AUTHORIZATION = "PROPOSED_FANOUT_IS_NOT_AUTHORIZATION"
    EXECUTION_AUTHORITY_REMAINS_DENIED = "EXECUTION_AUTHORITY_REMAINS_DENIED"
    PRODUCTION_AUTHORITY_REMAINS_DENIED = "PRODUCTION_AUTHORITY_REMAINS_DENIED"
    SOURCE_AUTHENTICITY_REMAINS_NOT_ESTABLISHED = "SOURCE_AUTHENTICITY_REMAINS_NOT_ESTABLISHED"
    REQUEST_COVERAGE_REMAINS_NOT_ESTABLISHED = "REQUEST_COVERAGE_REMAINS_NOT_ESTABLISHED"


SESSION_ORIGIN_LIMITATIONS = tuple(SessionOriginLimitation)

_CONCEPT_ORDER = (
    SessionOriginConcept.CALLER_SUPPLIED_PLANNING_CONTEXT,
    SessionOriginConcept.PROCESS_LOCAL_REGISTRY_MEMBERSHIP,
    SessionOriginConcept.RETAINED_ROUTE_DECISION_IDENTITY,
    SessionOriginConcept.AUTHENTICATED_APPLICATION_PRINCIPAL,
    SessionOriginConcept.AUTHORIZED_MULTI_INVOCATION_EXECUTION_SESSION,
)
_UNAVAILABLE_CONCEPTS = frozenset(
    {
        SessionOriginConcept.AUTHENTICATED_APPLICATION_PRINCIPAL,
        SessionOriginConcept.AUTHORIZED_MULTI_INVOCATION_EXECUTION_SESSION,
    }
)
_KNOWN_MEMBERSHIP = frozenset(
    {
        RegistryMembershipStanding.MEMBER,
        RegistryMembershipStanding.COPIED_SESSION,
        RegistryMembershipStanding.FOREIGN_SESSION,
        RegistryMembershipStanding.CLOSED,
        RegistryMembershipStanding.INVALIDATED,
    }
)
_REJECTED_MEMBERSHIP = frozenset(
    {
        RegistryMembershipStanding.COPIED_SESSION,
        RegistryMembershipStanding.FOREIGN_SESSION,
    }
)
_OBSERVED_MEMBERSHIP = frozenset(
    {
        RegistryMembershipStanding.MEMBER,
        RegistryMembershipStanding.CLOSED,
        RegistryMembershipStanding.INVALIDATED,
    }
)
_DECISION_REJECTIONS = {
    "deserialized_decision_rejected": DecisionIdentityStanding.DESERIALIZED,
    "forged_decision_rejected": DecisionIdentityStanding.FORGED,
    "reconstructed_decision_rejected": DecisionIdentityStanding.RECONSTRUCTED,
}
_PRESENTATION_CODES = {
    InvocationSelectionStanding.ONE_CANDIDATE: frozenset({"one_selection_only"}),
    InvocationSelectionStanding.MULTIPLE_REJECTED: frozenset({"multiple_invocations_rejected"}),
    InvocationSelectionStanding.SECOND_SELECTION_REJECTED: frozenset(
        {"multiple_invocations_rejected"}
    ),
    InvocationSelectionStanding.COPIED_IDENTIFIER: frozenset({"copied_invocation_identifier"}),
    InvocationSelectionStanding.FOREIGN_INVOCATION: frozenset({"wrong_invocation"}),
    InvocationSelectionStanding.FORGED: frozenset({"forged_invocation_rejected"}),
    InvocationSelectionStanding.NOT_SELECTABLE: frozenset(
        {
            "copied_session_identifier",
            "foreign_session",
            "session_closed",
            "session_invalidated",
            "invalid_session_state",
            "invalid_session",
        }
    ),
}
_MEMBERSHIP_CODES = {
    "copied_session_identifier": RegistryMembershipStanding.COPIED_SESSION,
    "foreign_session": RegistryMembershipStanding.FOREIGN_SESSION,
}

if AUTHENTICATED_APPLICATION_PRINCIPAL_AVAILABLE:
    raise ContractValidationError("authenticated_principal_not_available")
if AUTHORIZED_MULTI_INVOCATION_EXECUTION_AVAILABLE:
    raise ContractValidationError("authorized_execution_session_not_available")
if INVOCATION_SELECTION_LIMIT != 1:
    raise ContractValidationError("multiple_invocations_rejected")


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
    ):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _false(value: object, code: str) -> None:
    if value is not False:
        raise ContractValidationError(code)


def _true(value: object, code: str) -> None:
    if value is not True:
        raise ContractValidationError(code)


def _deny_authority(
    *,
    execution_authority: object,
    production_authority: object,
    source_authenticity: object,
    request_coverage: object,
    dispatch_permitted: object,
    end_to_end_fulfilled: object,
) -> None:
    if execution_authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
    if production_authority is not MasterProductionAuthority.DENIED:
        raise ContractValidationError("production_authority_not_issued")
    if source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
        raise ContractValidationError("source_authenticity_not_established")
    if request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
        raise ContractValidationError("request_coverage_not_established")
    _false(dispatch_permitted, "execution_not_granted")
    _false(end_to_end_fulfilled, "end_to_end_fulfillment_not_granted")


def _schema(value: object) -> None:
    if value != _SCHEMA:
        raise ContractValidationError("invalid_schema_version")


def _code(exc: ContractValidationError) -> str:
    if len(exc.errors) != 1:
        raise exc
    return exc.errors[0]


def _deny_plan(plan: MasterRoutePlan) -> None:
    if plan.execution_authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
    if plan.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
        raise ContractValidationError("request_coverage_not_established")
    if plan.end_to_end_fulfilled:
        raise ContractValidationError("end_to_end_fulfillment_not_granted")


def _deny_decision(decision: MasterRouteDecision) -> None:
    if decision.execution_authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
    if decision.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
        raise ContractValidationError("request_coverage_not_established")
    if decision.plan is not None:
        _deny_plan(decision.plan)


def _deny_record(record: MasterPlanningSessionRecord) -> None:
    if record.execution_authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
    if record.production_authority is not MasterProductionAuthority.DENIED:
        raise ContractValidationError("production_authority_not_issued")
    if record.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
        raise ContractValidationError("source_authenticity_not_established")
    if record.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
        raise ContractValidationError("request_coverage_not_established")


@dataclass(frozen=True)
class _ReadableSession:
    """Private snapshot of public registry reads. Not a credential."""

    membership: RegistryMembershipStanding
    membership_code: str | None
    state: MasterPlanningSessionState | None
    context: MasterPlanningContext | None
    decision: MasterRouteDecision | None
    plan: MasterRoutePlan | None
    selected: MasterInvocationAuthorization | None
    session_label: str | None


def _read(registry: object, record: object) -> _ReadableSession:
    if not isinstance(registry, MasterPlanningSessionRegistry):
        raise ContractValidationError("invalid_session")
    if isinstance(record, (str, bytes, bytearray)):
        raise ContractValidationError("session_principal_not_accepted")
    if not isinstance(record, MasterPlanningSessionRecord):
        raise ContractValidationError("invalid_session")
    label = record.session_id
    try:
        current = registry.state(record)
    except ContractValidationError as exc:
        code = _code(exc)
        membership = _MEMBERSHIP_CODES.get(code)
        if membership is None:
            raise
        return _ReadableSession(membership, code, None, None, None, None, None, label)
    if current is MasterPlanningSessionState.CLOSED:
        return _ReadableSession(
            RegistryMembershipStanding.CLOSED,
            "session_closed",
            current,
            None,
            None,
            None,
            None,
            label,
        )
    if current is MasterPlanningSessionState.INVALIDATED:
        return _ReadableSession(
            RegistryMembershipStanding.INVALIDATED,
            "session_invalidated",
            current,
            None,
            None,
            None,
            None,
            label,
        )
    if current is not MasterPlanningSessionState.OPEN:
        raise ContractValidationError("invalid_session_state")
    _deny_record(record)
    context = registry.context_for(record)
    decision = registry.decision_for(record)
    if not isinstance(context, MasterPlanningContext):
        raise ContractValidationError("invalid_planning_context")
    if not isinstance(decision, MasterRouteDecision):
        raise ContractValidationError("invalid_master_route_decision")
    _deny_decision(decision)
    plan: MasterRoutePlan | None
    try:
        loaded = registry.plan_for(record)
    except ContractValidationError as exc:
        if _code(exc) != "invalid_session_state":
            raise
        plan = None
    else:
        if loaded is not decision.plan:
            raise ContractValidationError("reconstructed_plan_rejected")
        _deny_plan(loaded)
        plan = loaded
    selected: MasterInvocationAuthorization | None = None
    if plan is not None:
        try:
            chosen = registry.selected_invocation(record)
        except ContractValidationError as exc:
            if _code(exc) != "invalid_session_state":
                raise
        else:
            if not isinstance(chosen, MasterInvocationAuthorization):
                raise ContractValidationError("forged_invocation_rejected")
            if not any(step.invocation is chosen for step in plan.steps):
                raise ContractValidationError("wrong_invocation")
            selected = chosen
    return _ReadableSession(
        RegistryMembershipStanding.MEMBER,
        None,
        current,
        context,
        decision,
        plan,
        selected,
        label,
    )


def _decision_identity(
    registry: MasterPlanningSessionRegistry,
    record: MasterPlanningSessionRecord,
    readable: _ReadableSession,
    presented: object | None,
) -> tuple[DecisionIdentityStanding, str | None, bool]:
    if readable.membership is not RegistryMembershipStanding.MEMBER or readable.decision is None:
        return DecisionIdentityStanding.NOT_AVAILABLE, readable.membership_code, False
    if presented is None:
        return DecisionIdentityStanding.RETAINED, None, False
    try:
        retained = registry.require_retained_decision(record, presented)
    except ContractValidationError as exc:
        code = _code(exc)
        standing = _DECISION_REJECTIONS.get(code)
        if standing is None:
            raise
        return standing, code, False
    if retained is not readable.decision:
        raise ContractValidationError("reconstructed_decision_rejected")
    return DecisionIdentityStanding.RETAINED, None, True


@dataclass(frozen=True)
class SessionOriginFinding:
    """One concept and its standing. Observation is not authentication."""

    concept: SessionOriginConcept
    standing: SessionOriginStanding
    establishes_authenticated_principal: bool = False
    establishes_authorized_execution_session: bool = False
    schema_version: str = _SCHEMA

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        if not isinstance(self.concept, SessionOriginConcept):
            raise ContractValidationError("invalid_session_origin_concept")
        if not isinstance(self.standing, SessionOriginStanding):
            raise ContractValidationError("invalid_session_origin_standing")
        _false(self.establishes_authenticated_principal, "authenticated_principal_not_available")
        _false(
            self.establishes_authorized_execution_session,
            "authorized_execution_session_not_available",
        )
        if self.concept in _UNAVAILABLE_CONCEPTS:
            if self.standing is not SessionOriginStanding.NOT_ESTABLISHED:
                if self.concept is SessionOriginConcept.AUTHENTICATED_APPLICATION_PRINCIPAL:
                    raise ContractValidationError("authenticated_principal_not_available")
                raise ContractValidationError("authorized_execution_session_not_available")


@dataclass(frozen=True)
class ApplicationOwnedSessionAuthorityRequirement:
    """Unmet requirements for a future application-owned session authority.

    This object does not authenticate a principal and does not open a session.
    The satisfiers stay false. The requirement cannot be waived.
    """

    authenticated_principal_required: bool = True
    caller_supplied_context_satisfies: bool = False
    process_local_membership_satisfies: bool = False
    retained_decision_identity_satisfies: bool = False
    matching_identifier_satisfies: bool = False
    object_identity_satisfies: bool = False
    user_text_satisfies: bool = False
    model_output_satisfies: bool = False
    authority_issued: bool = False
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    dispatch_permitted: bool = False
    end_to_end_fulfilled: bool = False
    schema_version: str = _SCHEMA

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _true(self.authenticated_principal_required, "authenticated_principal_not_available")
        _false(self.caller_supplied_context_satisfies, "caller_context_not_principal")
        _false(self.process_local_membership_satisfies, "registry_membership_not_authentication")
        _false(self.retained_decision_identity_satisfies, "retained_decision_not_authentication")
        _false(self.matching_identifier_satisfies, "matching_identifier_not_authentication")
        _false(self.object_identity_satisfies, "object_identity_not_authentication")
        _false(self.user_text_satisfies, "user_text_not_authentication")
        _false(self.model_output_satisfies, "model_output_not_authentication")
        _false(self.authority_issued, "authenticated_principal_not_available")
        _deny_authority(
            execution_authority=self.execution_authority,
            production_authority=self.production_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            dispatch_permitted=self.dispatch_permitted,
            end_to_end_fulfilled=self.end_to_end_fulfilled,
        )


def unmet_application_session_authority() -> ApplicationOwnedSessionAuthorityRequirement:
    """Return the closed unmet requirement. Nothing is issued."""

    return ApplicationOwnedSessionAuthorityRequirement()


@dataclass(frozen=True)
class PerInvocationSelectionRequirement:
    """One planned invocation a future selector would still have to execute.

    The identifiers are labels copied from the plan. ``requirement_satisfied``
    stays false. This object does not select the invocation and does not
    execute it. Satisfying it would have to avoid a new planning identity and
    avoid changing the original plan. This milestone cannot do that.
    """

    step_id: str
    invocation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    requirement: MasterRequirement
    selected_for_execution: bool = False
    execution_granted: bool = False
    requirement_satisfied: bool = False
    mints_additional_planning_identity: bool = False
    changes_original_plan: bool = False
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    dispatch_permitted: bool = False
    end_to_end_fulfilled: bool = False
    schema_version: str = _SCHEMA

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _token(self.step_id, "step_id")
        _token(self.invocation_id, "invocation_id")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        if not isinstance(self.requirement, MasterRequirement):
            raise ContractValidationError("invalid_requirement")
        _false(self.selected_for_execution, "selection_does_not_authorize_execution")
        _false(self.execution_granted, "execution_authority_not_granted")
        _false(self.requirement_satisfied, "authorized_execution_session_not_available")
        _false(self.mints_additional_planning_identity, "additional_planning_identity_not_fanout")
        _false(self.changes_original_plan, "retained_plan_not_replaced")
        _deny_authority(
            execution_authority=self.execution_authority,
            production_authority=self.production_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            dispatch_permitted=self.dispatch_permitted,
            end_to_end_fulfilled=self.end_to_end_fulfilled,
        )


def _requirement(step: MasterRouteStepPlan) -> PerInvocationSelectionRequirement:
    invocation = step.invocation
    return PerInvocationSelectionRequirement(
        step_id=step.step_id,
        invocation_id=invocation.invocation_id,
        specialist=invocation.specialist,
        purpose=invocation.purpose,
        requirement=invocation.requirement,
    )


@dataclass(frozen=True)
class InvocationSelectionBoundary:
    """The current one-selection limit. It does not execute the plan."""

    planned_invocation_ids: tuple[str, ...]
    step_ids: tuple[str, ...]
    specialists: tuple[MasterSpecialist, ...]
    planned_invocation_count: int
    selected_invocation_count: int
    unselected_invocation_ids: tuple[str, ...]
    selected_invocation_id: str | None
    repeated_specialist: bool
    registry_code: str | None = None
    selection_limit: int = INVOCATION_SELECTION_LIMIT
    second_selection_replaces_first: bool = False
    selection_authorizes_execution: bool = False
    multi_invocation_execution_available: bool = False
    additional_planning_identity_minted: bool = False
    original_plan_changed: bool = False
    schema_version: str = _SCHEMA

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        if self.selection_limit != INVOCATION_SELECTION_LIMIT:
            raise ContractValidationError("multiple_invocations_rejected")
        if self.selected_invocation_count not in (0, 1):
            raise ContractValidationError("multiple_invocations_rejected")
        if not isinstance(self.planned_invocation_ids, tuple) or any(
            not isinstance(item, str) for item in self.planned_invocation_ids
        ):
            raise ContractValidationError("invalid_invocation_authorization")
        if len(self.planned_invocation_ids) != len(set(self.planned_invocation_ids)):
            raise ContractValidationError("duplicate_invocation_id")
        if self.planned_invocation_count != len(self.planned_invocation_ids):
            raise ContractValidationError("invocation_not_in_plan")
        if self.step_ids != tuple(self.step_ids) or len(self.step_ids) != self.planned_invocation_count:
            raise ContractValidationError("invalid_steps")
        if len(self.step_ids) != len(set(self.step_ids)):
            raise ContractValidationError("duplicate_step_id")
        if any(not isinstance(item, str) for item in self.step_ids):
            raise ContractValidationError("invalid_steps")
        if not isinstance(self.specialists, tuple) or len(self.specialists) != self.planned_invocation_count:
            raise ContractValidationError("invalid_specialist")
        if any(not isinstance(item, MasterSpecialist) for item in self.specialists):
            raise ContractValidationError("invalid_specialist")
        repeated = len(self.specialists) != len(set(self.specialists))
        if self.repeated_specialist is not repeated:
            raise ContractValidationError("invalid_specialist")
        if (self.selected_invocation_id is None) is not (self.selected_invocation_count == 0):
            raise ContractValidationError("multiple_invocations_rejected")
        if self.selected_invocation_id is not None and self.selected_invocation_id not in self.planned_invocation_ids:
            raise ContractValidationError("wrong_invocation")
        expected_unselected = tuple(
            item for item in self.planned_invocation_ids if item != self.selected_invocation_id
        )
        if self.unselected_invocation_ids != expected_unselected:
            raise ContractValidationError("invocation_not_in_plan")
        if self.registry_code is not None and not isinstance(self.registry_code, str):
            raise ContractValidationError("invalid_session_state")
        _false(self.second_selection_replaces_first, "multiple_invocations_rejected")
        _false(self.selection_authorizes_execution, "selection_does_not_authorize_execution")
        _false(self.multi_invocation_execution_available, "authorized_execution_session_not_available")
        _false(self.additional_planning_identity_minted, "additional_planning_identity_not_fanout")
        _false(self.original_plan_changed, "retained_plan_not_replaced")


def _selection_boundary(
    readable: _ReadableSession,
    *,
    registry_code: str | None,
) -> InvocationSelectionBoundary:
    plan = readable.plan
    if plan is None:
        return InvocationSelectionBoundary(
            planned_invocation_ids=(),
            step_ids=(),
            specialists=(),
            planned_invocation_count=0,
            selected_invocation_count=0,
            unselected_invocation_ids=(),
            selected_invocation_id=None,
            repeated_specialist=False,
            registry_code=registry_code,
        )
    invocation_ids = tuple(step.invocation.invocation_id for step in plan.steps)
    selected_id = None if readable.selected is None else readable.selected.invocation_id
    return InvocationSelectionBoundary(
        planned_invocation_ids=invocation_ids,
        step_ids=tuple(step.step_id for step in plan.steps),
        specialists=tuple(step.invocation.specialist for step in plan.steps),
        planned_invocation_count=len(plan.steps),
        selected_invocation_count=0 if selected_id is None else 1,
        unselected_invocation_ids=tuple(item for item in invocation_ids if item != selected_id),
        selected_invocation_id=selected_id,
        repeated_specialist=len(plan.steps) != len({step.invocation.specialist for step in plan.steps}),
        registry_code=registry_code,
    )


@dataclass(frozen=True)
class ProposedInvocationFanout:
    """A proposal to execute every planned invocation on the original session.

    The proposal does not mint a planning identity and does not change the
    plan. It is still not authorization. ``fanout_authorized`` stays false.
    """

    requirements: tuple[PerInvocationSelectionRequirement, ...]
    route_plan: MasterRoutePlan | None
    planning_context: MasterPlanningContext | None
    planned_invocation_count: int
    repeated_specialist: bool
    membership_code: str | None = None
    selection_limit: int = INVOCATION_SELECTION_LIMIT
    preserves_original_planning_identity: bool = True
    preserves_original_plan: bool = True
    fanout_authorized: bool = False
    trusted: bool = False
    executes_every_planned_invocation: bool = False
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    dispatch_permitted: bool = False
    end_to_end_fulfilled: bool = False
    limitations: tuple[SessionOriginLimitation, ...] = SESSION_ORIGIN_LIMITATIONS
    schema_version: str = _SCHEMA

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        if self.selection_limit != INVOCATION_SELECTION_LIMIT:
            raise ContractValidationError("multiple_invocations_rejected")
        if not isinstance(self.requirements, tuple) or any(
            not isinstance(item, PerInvocationSelectionRequirement) for item in self.requirements
        ):
            raise ContractValidationError("invalid_invocation_authorization")
        if self.planned_invocation_count != len(self.requirements):
            raise ContractValidationError("invocation_not_in_plan")
        invocation_ids = tuple(item.invocation_id for item in self.requirements)
        if len(invocation_ids) != len(set(invocation_ids)):
            raise ContractValidationError("duplicate_invocation_id")
        step_ids = tuple(item.step_id for item in self.requirements)
        if len(step_ids) != len(set(step_ids)):
            raise ContractValidationError("duplicate_step_id")
        repeated = len(self.requirements) != len({item.specialist for item in self.requirements})
        if self.repeated_specialist is not repeated:
            raise ContractValidationError("invalid_specialist")
        if self.route_plan is None:
            if self.requirements:
                raise ContractValidationError("invalid_session_state")
            if self.planning_context is not None and not isinstance(
                self.planning_context, MasterPlanningContext
            ):
                raise ContractValidationError("invalid_planning_context")
        else:
            if not isinstance(self.route_plan, MasterRoutePlan):
                raise ContractValidationError("invalid_route_plan")
            _deny_plan(self.route_plan)
            if self.planning_context is None or not isinstance(
                self.planning_context, MasterPlanningContext
            ):
                raise ContractValidationError("invalid_planning_context")
            if self.planning_context.master_request_id != self.route_plan.master_request_id:
                raise ContractValidationError("master_request_mismatch")
            if self.planning_context.canonical_user_text != self.route_plan.canonical_user_text:
                raise ContractValidationError("user_text_mismatch")
            planned = tuple(
                (step.step_id, step.invocation.invocation_id, step.invocation.specialist)
                for step in self.route_plan.steps
            )
            actual = tuple(
                (item.step_id, item.invocation_id, item.specialist) for item in self.requirements
            )
            if planned != actual:
                raise ContractValidationError("wrong_invocation")
        _true(self.preserves_original_planning_identity, "additional_planning_identity_not_fanout")
        _true(self.preserves_original_plan, "retained_plan_not_replaced")
        _false(self.fanout_authorized, "fanout_not_authorized")
        _false(self.trusted, "fanout_not_authorized")
        _false(self.executes_every_planned_invocation, "authorized_execution_session_not_available")
        if self.limitations != SESSION_ORIGIN_LIMITATIONS:
            raise ContractValidationError("session_origin_limitation_not_cleared")
        _deny_authority(
            execution_authority=self.execution_authority,
            production_authority=self.production_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            dispatch_permitted=self.dispatch_permitted,
            end_to_end_fulfilled=self.end_to_end_fulfilled,
        )


def _fanout(readable: _ReadableSession) -> ProposedInvocationFanout:
    plan = readable.plan
    if plan is None or readable.context is None:
        code = readable.membership_code
        if readable.membership is RegistryMembershipStanding.MEMBER and code is None:
            code = "invalid_session_state"
        return ProposedInvocationFanout(
            requirements=(),
            route_plan=None,
            planning_context=readable.context,
            planned_invocation_count=0,
            repeated_specialist=False,
            membership_code=code,
        )
    requirements = tuple(_requirement(step) for step in plan.steps)
    return ProposedInvocationFanout(
        requirements=requirements,
        route_plan=plan,
        planning_context=readable.context,
        planned_invocation_count=len(requirements),
        repeated_specialist=len(requirements) != len({item.specialist for item in requirements}),
        membership_code=None,
    )


@dataclass(frozen=True)
class AuthenticationClaimRejection:
    """A closed rejection. ``authenticated`` cannot become true."""

    kind: AuthenticationClaimKind
    authenticated: bool = False
    establishes_principal: bool = False
    establishes_execution_session: bool = False
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    dispatch_permitted: bool = False
    end_to_end_fulfilled: bool = False
    schema_version: str = _SCHEMA

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        if not isinstance(self.kind, AuthenticationClaimKind):
            raise ContractValidationError("invalid_authentication_claim")
        _false(self.authenticated, "authenticated_principal_not_available")
        _false(self.establishes_principal, "authenticated_principal_not_available")
        _false(self.establishes_execution_session, "authorized_execution_session_not_available")
        _deny_authority(
            execution_authority=self.execution_authority,
            production_authority=self.production_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            dispatch_permitted=self.dispatch_permitted,
            end_to_end_fulfilled=self.end_to_end_fulfilled,
        )


def _claim_kind(claim: object) -> AuthenticationClaimKind:
    if isinstance(claim, (ModelDecision, ModelTurnRequest)):
        return AuthenticationClaimKind.MODEL_OUTPUT
    if isinstance(claim, str) and any(character.isspace() for character in claim):
        return AuthenticationClaimKind.USER_TEXT
    if isinstance(claim, (str, bytes, bytearray, Mapping)):
        return AuthenticationClaimKind.MATCHING_IDENTIFIER
    return AuthenticationClaimKind.OBJECT_IDENTITY


def reject_authentication_claim(claim: object) -> AuthenticationClaimRejection:
    """Reject one attempt to treat a supplied object as an application principal.

    Matching identifiers, object identity, user text, and model output all
    return a rejection. The claim is not read as aviation evidence.
    """

    return AuthenticationClaimRejection(kind=_claim_kind(claim))


@dataclass(frozen=True)
class InvocationPresentation:
    """How one presented value sits against the one-selection restriction."""

    code: str
    standing: InvocationSelectionStanding
    matches_plan_object: bool
    matches_invocation_id: bool
    registry_would_select_one: bool
    existing_selected_invocation_id: str | None
    selection_replaced: bool = False
    selected_for_execution: bool = False
    execution_granted: bool = False
    dispatch_permitted: bool = False
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    end_to_end_fulfilled: bool = False
    schema_version: str = _SCHEMA

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        if not isinstance(self.standing, InvocationSelectionStanding):
            raise ContractValidationError("invalid_invocation_selection")
        allowed = _PRESENTATION_CODES.get(self.standing)
        if allowed is None or self.code not in allowed:
            raise ContractValidationError("invalid_invocation_selection")
        if self.registry_would_select_one:
            if self.standing is not InvocationSelectionStanding.ONE_CANDIDATE:
                raise ContractValidationError("multiple_invocations_rejected")
            if not self.matches_plan_object or self.existing_selected_invocation_id is not None:
                raise ContractValidationError("multiple_invocations_rejected")
        elif self.standing is InvocationSelectionStanding.ONE_CANDIDATE:
            raise ContractValidationError("invalid_invocation_selection")
        _false(self.selection_replaced, "multiple_invocations_rejected")
        _false(self.selected_for_execution, "selection_does_not_authorize_execution")
        _false(self.execution_granted, "execution_authority_not_granted")
        _false(self.dispatch_permitted, "execution_not_granted")
        _false(self.end_to_end_fulfilled, "end_to_end_fulfillment_not_granted")
        if self.execution_authority is not MasterRouteExecutionAuthority.DENIED:
            raise ContractValidationError("execution_authority_not_granted")
        if self.production_authority is not MasterProductionAuthority.DENIED:
            raise ContractValidationError("production_authority_not_issued")
        if self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            raise ContractValidationError("source_authenticity_not_established")
        if self.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
            raise ContractValidationError("request_coverage_not_established")


def _matches(plan: MasterRoutePlan | None, invocation: object) -> tuple[bool, bool]:
    if plan is None:
        return False, False
    if isinstance(invocation, str):
        return False, any(step.invocation.invocation_id == invocation for step in plan.steps)
    if not isinstance(invocation, MasterInvocationAuthorization):
        return False, False
    identity = any(step.invocation is invocation for step in plan.steps)
    identifier = any(
        step.invocation.invocation_id == invocation.invocation_id for step in plan.steps
    )
    return identity, identifier


def _not_selectable(readable: _ReadableSession) -> str | None:
    if readable.membership is RegistryMembershipStanding.COPIED_SESSION:
        return "copied_session_identifier"
    if readable.membership is RegistryMembershipStanding.FOREIGN_SESSION:
        return "foreign_session"
    if readable.membership is RegistryMembershipStanding.CLOSED:
        return "session_closed"
    if readable.membership is RegistryMembershipStanding.INVALIDATED:
        return "session_invalidated"
    if readable.plan is None:
        return "invalid_session_state"
    return None


def classify_presented_invocation(
    registry: object,
    record: object,
    invocation: object,
) -> InvocationPresentation:
    """Classify one presented invocation without selecting it.

    The order follows ``associate_invocation``: a collection or an existing
    selection is rejected before a copied identifier, a forged object, the
    retained object, or a foreign invocation. This function does not write
    the selection.
    """

    if not isinstance(registry, MasterPlanningSessionRegistry):
        raise ContractValidationError("invalid_session")
    if isinstance(record, (str, bytes, bytearray)):
        raise ContractValidationError("session_principal_not_accepted")
    if not isinstance(record, MasterPlanningSessionRecord):
        raise ContractValidationError("invalid_session")
    readable = _read(registry, record)
    blocked = _not_selectable(readable)
    identity, identifier = _matches(readable.plan, invocation)
    existing = None if readable.selected is None else readable.selected.invocation_id
    if blocked is not None:
        return InvocationPresentation(
            code=blocked,
            standing=InvocationSelectionStanding.NOT_SELECTABLE,
            matches_plan_object=False,
            matches_invocation_id=False,
            registry_would_select_one=False,
            existing_selected_invocation_id=existing,
        )
    if isinstance(invocation, (list, tuple, set)) or readable.selected is not None:
        standing = (
            InvocationSelectionStanding.SECOND_SELECTION_REJECTED
            if readable.selected is not None
            else InvocationSelectionStanding.MULTIPLE_REJECTED
        )
        return InvocationPresentation(
            code="multiple_invocations_rejected",
            standing=standing,
            matches_plan_object=identity,
            matches_invocation_id=identifier,
            registry_would_select_one=False,
            existing_selected_invocation_id=existing,
        )
    if isinstance(invocation, str):
        return InvocationPresentation(
            code="copied_invocation_identifier",
            standing=InvocationSelectionStanding.COPIED_IDENTIFIER,
            matches_plan_object=False,
            matches_invocation_id=identifier,
            registry_would_select_one=False,
            existing_selected_invocation_id=existing,
        )
    if not isinstance(invocation, MasterInvocationAuthorization):
        return InvocationPresentation(
            code="forged_invocation_rejected",
            standing=InvocationSelectionStanding.FORGED,
            matches_plan_object=False,
            matches_invocation_id=False,
            registry_would_select_one=False,
            existing_selected_invocation_id=existing,
        )
    if identity:
        return InvocationPresentation(
            code="one_selection_only",
            standing=InvocationSelectionStanding.ONE_CANDIDATE,
            matches_plan_object=True,
            matches_invocation_id=True,
            registry_would_select_one=True,
            existing_selected_invocation_id=None,
        )
    if identifier:
        return InvocationPresentation(
            code="copied_invocation_identifier",
            standing=InvocationSelectionStanding.COPIED_IDENTIFIER,
            matches_plan_object=False,
            matches_invocation_id=True,
            registry_would_select_one=False,
            existing_selected_invocation_id=existing,
        )
    return InvocationPresentation(
        code="wrong_invocation",
        standing=InvocationSelectionStanding.FOREIGN_INVOCATION,
        matches_plan_object=False,
        matches_invocation_id=False,
        registry_would_select_one=False,
        existing_selected_invocation_id=existing,
    )


def _findings(
    readable: _ReadableSession,
    decision_standing: DecisionIdentityStanding,
) -> tuple[SessionOriginFinding, ...]:
    context_standing = (
        SessionOriginStanding.OBSERVED
        if readable.context is not None
        else (
            SessionOriginStanding.REJECTED
            if readable.membership in _REJECTED_MEMBERSHIP
            else SessionOriginStanding.NOT_ESTABLISHED
        )
    )
    if readable.membership in _OBSERVED_MEMBERSHIP:
        membership_standing = SessionOriginStanding.OBSERVED
    elif readable.membership in _REJECTED_MEMBERSHIP:
        membership_standing = SessionOriginStanding.REJECTED
    else:
        membership_standing = SessionOriginStanding.NOT_ESTABLISHED
    if decision_standing is DecisionIdentityStanding.RETAINED:
        decision_concept = SessionOriginStanding.OBSERVED
    elif decision_standing in (
        DecisionIdentityStanding.DESERIALIZED,
        DecisionIdentityStanding.FORGED,
        DecisionIdentityStanding.RECONSTRUCTED,
    ):
        decision_concept = SessionOriginStanding.REJECTED
    else:
        decision_concept = SessionOriginStanding.NOT_ESTABLISHED
    standings = {
        SessionOriginConcept.CALLER_SUPPLIED_PLANNING_CONTEXT: context_standing,
        SessionOriginConcept.PROCESS_LOCAL_REGISTRY_MEMBERSHIP: membership_standing,
        SessionOriginConcept.RETAINED_ROUTE_DECISION_IDENTITY: decision_concept,
        SessionOriginConcept.AUTHENTICATED_APPLICATION_PRINCIPAL: (
            SessionOriginStanding.NOT_ESTABLISHED
        ),
        SessionOriginConcept.AUTHORIZED_MULTI_INVOCATION_EXECUTION_SESSION: (
            SessionOriginStanding.NOT_ESTABLISHED
        ),
    }
    return tuple(
        SessionOriginFinding(concept=concept, standing=standings[concept])
        for concept in _CONCEPT_ORDER
    )


@dataclass(frozen=True)
class SessionOriginAssessment:
    """Diagnostic for one record. The last two concepts stay unestablished.

    ``session_label`` repeats the record's public id. A matching label is not
    membership and is not an application principal.
    """

    findings: tuple[SessionOriginFinding, ...]
    membership: RegistryMembershipStanding
    decision_identity: DecisionIdentityStanding
    decision_rejection_code: str | None
    presented_context_is_retained: bool
    presented_decision_is_retained: bool
    session_label: str | None
    planning_context: MasterPlanningContext | None
    retained_decision: MasterRouteDecision | None
    route_plan: MasterRoutePlan | None
    selection: InvocationSelectionBoundary
    fanout: ProposedInvocationFanout
    authority_requirement: ApplicationOwnedSessionAuthorityRequirement
    claim_rejection: AuthenticationClaimRejection | None
    limitations: tuple[SessionOriginLimitation, ...] = SESSION_ORIGIN_LIMITATIONS
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    authenticated_application_principal: bool = False
    authorized_multi_invocation_execution_session: bool = False
    dispatch_permitted: bool = False
    end_to_end_fulfilled: bool = False
    schema_version: str = _SCHEMA

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        if not isinstance(self.findings, tuple) or tuple(
            item.concept for item in self.findings
        ) != _CONCEPT_ORDER:
            raise ContractValidationError("invalid_session_origin_concept")
        if any(not isinstance(item, SessionOriginFinding) for item in self.findings):
            raise ContractValidationError("invalid_session_origin_concept")
        by_concept = {item.concept: item for item in self.findings}
        principal = by_concept[SessionOriginConcept.AUTHENTICATED_APPLICATION_PRINCIPAL]
        execution_session = by_concept[
            SessionOriginConcept.AUTHORIZED_MULTI_INVOCATION_EXECUTION_SESSION
        ]
        if principal.standing is not SessionOriginStanding.NOT_ESTABLISHED:
            raise ContractValidationError("authenticated_principal_not_available")
        if execution_session.standing is not SessionOriginStanding.NOT_ESTABLISHED:
            raise ContractValidationError("authorized_execution_session_not_available")
        if self.membership not in _KNOWN_MEMBERSHIP:
            raise ContractValidationError("invalid_session")
        if not isinstance(self.decision_identity, DecisionIdentityStanding):
            raise ContractValidationError("invalid_master_route_decision")
        context_finding = by_concept[SessionOriginConcept.CALLER_SUPPLIED_PLANNING_CONTEXT]
        membership_finding = by_concept[SessionOriginConcept.PROCESS_LOCAL_REGISTRY_MEMBERSHIP]
        decision_finding = by_concept[SessionOriginConcept.RETAINED_ROUTE_DECISION_IDENTITY]
        if (self.planning_context is not None) is not (
            context_finding.standing is SessionOriginStanding.OBSERVED
        ):
            raise ContractValidationError("invalid_planning_context")
        expected_membership = (
            SessionOriginStanding.OBSERVED
            if self.membership in _OBSERVED_MEMBERSHIP
            else SessionOriginStanding.REJECTED
        )
        if membership_finding.standing is not expected_membership:
            raise ContractValidationError("invalid_session")
        if self.presented_context_is_retained and self.planning_context is None:
            raise ContractValidationError("invalid_planning_context")
        if self.presented_decision_is_retained and (
            self.decision_identity is not DecisionIdentityStanding.RETAINED
        ):
            raise ContractValidationError("reconstructed_decision_rejected")
        if self.decision_identity is DecisionIdentityStanding.RETAINED:
            if decision_finding.standing is not SessionOriginStanding.OBSERVED:
                raise ContractValidationError("reconstructed_decision_rejected")
            if self.retained_decision is None or self.decision_rejection_code is not None:
                raise ContractValidationError("invalid_master_route_decision")
        elif self.decision_identity is DecisionIdentityStanding.NOT_AVAILABLE:
            if decision_finding.standing is not SessionOriginStanding.NOT_ESTABLISHED:
                raise ContractValidationError("invalid_master_route_decision")
            if self.retained_decision is not None or self.presented_decision_is_retained:
                raise ContractValidationError("reconstructed_decision_rejected")
        elif decision_finding.standing is not SessionOriginStanding.REJECTED:
            raise ContractValidationError("forged_decision_rejected")
        elif self.decision_rejection_code is None:
            raise ContractValidationError("forged_decision_rejected")
        if self.route_plan is None and self.selection.registry_code != self.fanout.membership_code:
            raise ContractValidationError("invalid_session_state")
        if self.route_plan is not None and (
            self.fanout.membership_code is not None or self.selection.registry_code is not None
        ):
            raise ContractValidationError("invalid_session_state")
        if self.membership is not RegistryMembershipStanding.MEMBER and (
            self.planning_context is not None or self.retained_decision is not None
        ):
            raise ContractValidationError("invalid_session_state")
        if self.route_plan is not None and (
            self.retained_decision is None or self.route_plan is not self.retained_decision.plan
        ):
            raise ContractValidationError("reconstructed_plan_rejected")
        if self.fanout.route_plan is not self.route_plan:
            raise ContractValidationError("reconstructed_plan_rejected")
        if self.fanout.planning_context is not self.planning_context:
            raise ContractValidationError("invalid_planning_context")
        if self.selection.planned_invocation_ids != tuple(
            item.invocation_id for item in self.fanout.requirements
        ):
            raise ContractValidationError("wrong_invocation")
        if self.selection.repeated_specialist is not self.fanout.repeated_specialist:
            raise ContractValidationError("invalid_specialist")
        if self.authority_requirement.authority_issued:
            raise ContractValidationError("authenticated_principal_not_available")
        if self.limitations != SESSION_ORIGIN_LIMITATIONS:
            raise ContractValidationError("session_origin_limitation_not_cleared")
        _false(self.authenticated_application_principal, "authenticated_principal_not_available")
        _false(
            self.authorized_multi_invocation_execution_session,
            "authorized_execution_session_not_available",
        )
        _false(self.fanout.fanout_authorized, "fanout_not_authorized")
        _deny_authority(
            execution_authority=self.execution_authority,
            production_authority=self.production_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            dispatch_permitted=self.dispatch_permitted,
            end_to_end_fulfilled=self.end_to_end_fulfilled,
        )


def propose_invocation_fanout(registry: object, record: object) -> ProposedInvocationFanout:
    """Describe the missing multi-invocation execution without performing it.

    The returned proposal keeps the original plan object and does not mint a
    session. ``fanout_authorized`` is false for one-step and multi-step plans.
    """

    if not isinstance(registry, MasterPlanningSessionRegistry):
        raise ContractValidationError("invalid_session")
    if not isinstance(record, MasterPlanningSessionRecord):
        if isinstance(record, (str, bytes, bytearray)):
            raise ContractValidationError("session_principal_not_accepted")
        raise ContractValidationError("invalid_session")
    return _fanout(_read(registry, record))


def assess_session_origin(
    registry: object,
    record: object,
    *,
    presented_decision: object | None = None,
    presented_context: object | None = None,
    authentication_claim: object | None = None,
) -> SessionOriginAssessment:
    """Assess origin and the one-selection limit without authorizing either.

    Public registry reads are not membership proofs for a different object,
    and they are not an application principal. A presented decision is checked
    with ``require_retained_decision``. A presented context counts as retained
    only when it is the stored object. An authentication claim is rejected.
    """

    if not isinstance(registry, MasterPlanningSessionRegistry):
        raise ContractValidationError("invalid_session")
    if isinstance(record, (str, bytes, bytearray)):
        raise ContractValidationError("session_principal_not_accepted")
    if not isinstance(record, MasterPlanningSessionRecord):
        raise ContractValidationError("invalid_session")
    readable = _read(registry, record)
    decision_standing, decision_code, decision_retained = _decision_identity(
        registry,
        record,
        readable,
        presented_decision,
    )
    context_retained = (
        presented_context is not None
        and readable.context is not None
        and presented_context is readable.context
    )
    claim = None if authentication_claim is None else reject_authentication_claim(authentication_claim)
    if readable.plan is None:
        registry_code = readable.membership_code or "invalid_session_state"
    else:
        registry_code = None
    return SessionOriginAssessment(
        findings=_findings(readable, decision_standing),
        membership=readable.membership,
        decision_identity=decision_standing,
        decision_rejection_code=decision_code,
        presented_context_is_retained=context_retained,
        presented_decision_is_retained=decision_retained,
        session_label=readable.session_label,
        planning_context=readable.context,
        retained_decision=readable.decision,
        route_plan=readable.plan,
        selection=_selection_boundary(readable, registry_code=registry_code),
        fanout=_fanout(readable),
        authority_requirement=unmet_application_session_authority(),
        claim_rejection=claim,
    )
