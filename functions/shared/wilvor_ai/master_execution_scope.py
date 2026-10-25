"""Non-redeemable audit scope for one Master-planned specialist invocation.

The scope binds an existing ``MasterPlanningContext``, the ``MasterRoutePlan``
that selected one step, that ``MasterRouteStepPlan`` object, and the
``MasterSpecialistAdapterDescriptor`` for the same invocation. It copies the
request identity, specialist purpose, capability, temporal scope, historical
window, route gates, dependencies, and structural readiness.

It does not retain a ``MasterParallelContractBundle``. Supplied evidence,
comparison requests, and trusted runtime handles therefore cannot ride along
on the scope. The retained route plan is a planning object. It is not
evidence and it is not an execution grant for this step or any other step.

Selection uses the step object carried by that plan. A field-equal copy is a
foreign step. Matching public fields and string identifiers are a consistency
check only. They do not authenticate a source, prove trust, or grant
execution permission. Structural readiness is copied for audit. A ready Live
Ops step and a blocked Decision step are both unauthorized.

This module does not create a planning session, an execution grant, a runtime
context, or a dispatcher. It does not call a specialist, a model, AWS, or a
provider. Construction and ``dataclasses.replace`` both keep execution
permission denied, production authority denied, and source authenticity
``NOT_ESTABLISHED``. There is no operation that redeems the scope.

PROPOSED later readers, not imported and not implemented here:

- A planning-session module may later supply the context, plan, and step
  passed to ``bind_master_execution_scope``. This module does not open one.
- A gate-resolution module may later read ``gates``, ``depends_on``, and
  ``missing_prerequisites``. A read does not clear a gate.
- A capability-registry module may later read ``capability``. Membership is
  not execution permission.
- A provenance module may later read ``missing_prerequisites``. This module
  has no operation that marks a prerequisite satisfied.

None of those proposed readers may treat this scope as permission to execute.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from wilvor_ai.contracts import (
    AgentCapability,
    ContractValidationError,
    TemporalScope,
)
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_routing import (
    MasterHintTrust,
    MasterPlanningContext,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRoutePlan,
    MasterRouteStepPlan,
)
from wilvor_ai.master_specialist_adapters import (
    MasterAdapterExecutionState,
    MasterSpecialistAdapterDescriptor,
)


MASTER_EXECUTION_SCOPE_SCHEMA_VERSION = "wilvor.ai.master_execution_scope.v1"

_LIVE_OPS_CAPABILITY = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
_HISTORICAL_CAPABILITY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
_DECISION_CAPABILITIES = frozenset(
    {
        AgentCapability.EXPLAIN_EXISTING_RISK,
        AgentCapability.EXPLAIN_EXISTING_RECOMMENDATIONS,
    }
)

# Restates the public MA2 specialist pairs. This is a consistency check.
# It does not authorize a step that routing did not already plan.
_PAIR_CAPABILITIES = {
    (MasterSpecialist.LIVE_OPS, MasterPurpose.DISCOVERY): frozenset({_LIVE_OPS_CAPABILITY}),
    (MasterSpecialist.LIVE_OPS, MasterPurpose.ANSWER): frozenset({_LIVE_OPS_CAPABILITY}),
    (MasterSpecialist.HISTORICAL_ANALYTICS, MasterPurpose.ANSWER): frozenset(
        {_HISTORICAL_CAPABILITY}
    ),
    (MasterSpecialist.DECISION, MasterPurpose.ANSWER): _DECISION_CAPABILITIES,
}


class MasterExecutionPrerequisite(str, Enum):
    """Proof a later execution review would still need.

    Every member stays missing. This module cannot mark one satisfied.
    The planning-session, principal, policy, read-resource, and deadline
    categories name missing proof only. They do not create a session, a
    principal, a policy object, a resource grant, a deadline, or authority.
    PROPOSED gate-resolution and provenance readers may inspect the closed
    set. They are not called here.
    """

    REVIEWED_EXECUTION_AUTHORITY = "REVIEWED_EXECUTION_AUTHORITY"
    TRUSTED_ADAPTER_IDENTITY = "TRUSTED_ADAPTER_IDENTITY"
    AUTHORIZED_EXECUTION_CONTEXT = "AUTHORIZED_EXECUTION_CONTEXT"
    SOURCE_READ_PROVENANCE = "SOURCE_READ_PROVENANCE"
    REQUEST_ENTITY_BINDING = "REQUEST_ENTITY_BINDING"
    SPECIALIST_CLOCKS = "SPECIALIST_CLOCKS"
    SOURCE_VERSION_INTEGRITY = "SOURCE_VERSION_INTEGRITY"
    POPULATION_AND_GEOGRAPHIC_SCOPE = "POPULATION_AND_GEOGRAPHIC_SCOPE"
    SEPARATELY_AUTHORIZED_DECISION_TARGET = "SEPARATELY_AUTHORIZED_DECISION_TARGET"
    DASHBOARD_INTEGRATION_BOUNDARY = "DASHBOARD_INTEGRATION_BOUNDARY"
    TRUSTED_PLANNING_SESSION_ORIGIN = "TRUSTED_PLANNING_SESSION_ORIGIN"
    AUTHENTICATED_APPLICATION_PRINCIPAL = "AUTHENTICATED_APPLICATION_PRINCIPAL"
    VERIFIED_EXECUTION_POLICY = "VERIFIED_EXECUTION_POLICY"
    AUTHORIZED_READ_RESOURCE_SCOPE = "AUTHORIZED_READ_RESOURCE_SCOPE"
    ESTABLISHED_EXECUTION_DEADLINE = "ESTABLISHED_EXECUTION_DEADLINE"


class MasterExecutionDenialCategory(str, Enum):
    """Why execution permission stays closed.

    The full set is always present. Clearing one is authority promotion.
    """

    EXECUTION_AUTHORITY_DENIED = "EXECUTION_AUTHORITY_DENIED"
    PRODUCTION_AUTHORITY_DENIED = "PRODUCTION_AUTHORITY_DENIED"
    SOURCE_AUTHENTICITY_NOT_ESTABLISHED = "SOURCE_AUTHENTICITY_NOT_ESTABLISHED"
    STRUCTURAL_READINESS_NOT_PERMISSION = "STRUCTURAL_READINESS_NOT_PERMISSION"
    PLANNING_OBJECT_NOT_AUTHORITY = "PLANNING_OBJECT_NOT_AUTHORITY"
    ADAPTER_DESCRIPTOR_NOT_AUTHORITY = "ADAPTER_DESCRIPTOR_NOT_AUTHORITY"
    SUPPLIED_EVIDENCE_NOT_AUTHORITY = "SUPPLIED_EVIDENCE_NOT_AUTHORITY"
    MODEL_OUTPUT_NOT_AUTHORITY = "MODEL_OUTPUT_NOT_AUTHORITY"
    MATCHING_IDENTIFIER_NOT_TRUST = "MATCHING_IDENTIFIER_NOT_TRUST"
    SCOPE_NOT_REDEEMABLE = "SCOPE_NOT_REDEEMABLE"


_MISSING_PREREQUISITES = tuple(MasterExecutionPrerequisite)
_DENIAL_CATEGORIES = tuple(MasterExecutionDenialCategory)


def _closed(value: object, denied: object, code: str) -> None:
    if value is not denied:
        raise ContractValidationError(code)


def _reject_authority_promotion(
    *,
    source_authenticity: object,
    production_authority: object,
    execution_authority: object,
    executable: object,
    execution_permission_granted: object,
    redeemable: object,
    dispatch_permitted: object,
) -> None:
    _closed(
        source_authenticity,
        MasterSourceAuthenticity.NOT_ESTABLISHED,
        "source_authenticity_not_established",
    )
    _closed(
        production_authority,
        MasterProductionAuthority.DENIED,
        "production_authority_not_issued",
    )
    _closed(
        execution_authority,
        MasterRouteExecutionAuthority.DENIED,
        "execution_authority_not_granted",
    )
    if executable is not False or dispatch_permitted is not False:
        raise ContractValidationError("execution_not_granted")
    if execution_permission_granted is not False:
        raise ContractValidationError("execution_permission_not_granted")
    if redeemable is not False:
        raise ContractValidationError("scope_not_redeemable")


def _reject_descriptor_promotion(descriptor: MasterSpecialistAdapterDescriptor) -> None:
    if (
        descriptor.execution_state
        is not MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
    ):
        raise ContractValidationError("execution_not_granted")
    _closed(
        descriptor.source_authenticity,
        MasterSourceAuthenticity.NOT_ESTABLISHED,
        "source_authenticity_not_established",
    )
    _closed(
        descriptor.production_authority,
        MasterProductionAuthority.DENIED,
        "production_authority_not_issued",
    )
    _closed(
        descriptor.execution_authority,
        MasterRouteExecutionAuthority.DENIED,
        "execution_authority_not_granted",
    )
    if descriptor.decision_target_trusted is not False:
        raise ContractValidationError("target_authorization_not_granted")
    if descriptor.callsign_authenticated is not False:
        raise ContractValidationError("callsign_not_authenticated_aircraft")
    if descriptor.timestamp_equivalence_established is not False:
        raise ContractValidationError("timestamp_equivalence_not_established")
    if descriptor.historical_run_fabricated is not False:
        raise ContractValidationError("historical_run_not_fabricated")
    if descriptor.live_ops_refs_are_tool_results is not False:
        raise ContractValidationError("live_ops_refs_not_tool_results")


def _descriptor_matches(
    descriptor: MasterSpecialistAdapterDescriptor,
    step: MasterRouteStepPlan,
) -> None:
    invocation = step.invocation
    if descriptor.master_request_id != invocation.master_request_id:
        raise ContractValidationError("master_request_mismatch")
    if (
        descriptor.step_id != step.step_id
        or descriptor.invocation_id != invocation.invocation_id
        or descriptor.purpose is not invocation.purpose
        or descriptor.requirement is not invocation.requirement
    ):
        raise ContractValidationError("descriptor_mismatch")
    if descriptor.specialist is not invocation.specialist:
        raise ContractValidationError("specialist_mismatch")
    if descriptor.specialist_request_id != invocation.specialist_request_id:
        raise ContractValidationError("specialist_request_id_mismatch")
    _reject_descriptor_promotion(descriptor)


def _require_capability(step: MasterRouteStepPlan) -> None:
    invocation = step.invocation
    allowed = _PAIR_CAPABILITIES.get((invocation.specialist, invocation.purpose))
    if allowed is None:
        raise ContractValidationError("unsupported_specialist_purpose")
    if step.capability not in allowed:
        raise ContractValidationError("capability_step_mismatch")


def _require_selected_step(plan: MasterRoutePlan, step: MasterRouteStepPlan) -> None:
    """The selected step is the object on the plan, not a field-equal copy."""

    if not any(item is step for item in plan.steps):
        raise ContractValidationError("foreign_step")


def _require_binding(scope: "MasterExecutionScope") -> None:
    context = scope.planning_context
    plan = scope.route_plan
    step = scope.planned_step
    descriptor = scope.descriptor
    if not isinstance(context, MasterPlanningContext):
        raise ContractValidationError("invalid_planning_context")
    if not isinstance(plan, MasterRoutePlan):
        raise ContractValidationError("invalid_route_plan")
    if not isinstance(step, MasterRouteStepPlan):
        raise ContractValidationError("invalid_route_step_plan")
    if not isinstance(descriptor, MasterSpecialistAdapterDescriptor):
        raise ContractValidationError("invalid_adapter_descriptor")
    _closed(
        plan.execution_authority,
        MasterRouteExecutionAuthority.DENIED,
        "execution_authority_not_granted",
    )
    _closed(
        plan.request_coverage,
        MasterRequestCoverage.NOT_ESTABLISHED,
        "request_coverage_not_established",
    )
    if plan.end_to_end_fulfilled is not False:
        raise ContractValidationError("end_to_end_fulfillment_not_granted")
    _require_selected_step(plan, step)
    invocation = step.invocation
    if not isinstance(invocation.specialist_request_id, str):
        raise ContractValidationError("specialist_request_id_required")
    if not isinstance(invocation.correlation_id, str):
        raise ContractValidationError("correlation_id_required")
    if not isinstance(invocation.application_request_time_utc, str):
        raise ContractValidationError("application_time_mismatch")
    if context.master_request_id != plan.master_request_id:
        raise ContractValidationError("master_request_mismatch")
    if context.master_request_id != invocation.master_request_id:
        raise ContractValidationError("master_request_mismatch")
    if context.canonical_user_text != plan.canonical_user_text:
        raise ContractValidationError("master_request_mismatch")
    if invocation.correlation_id != context.correlation_id:
        raise ContractValidationError("correlation_mismatch")
    if invocation.application_request_time_utc != context.application_request_time_utc:
        raise ContractValidationError("application_time_mismatch")
    if step.hint_trust is not MasterHintTrust.UNTRUSTED:
        raise ContractValidationError("selection_hints_not_trusted")
    if step.target_authorization_granted is not False:
        raise ContractValidationError("target_authorization_not_granted")
    _descriptor_matches(descriptor, step)
    _require_capability(step)
    if scope.master_request_id != context.master_request_id:
        raise ContractValidationError("master_request_mismatch")
    if scope.correlation_id != context.correlation_id:
        raise ContractValidationError("correlation_mismatch")
    if scope.application_request_time_utc != context.application_request_time_utc:
        raise ContractValidationError("application_time_mismatch")
    if scope.canonical_user_text != context.canonical_user_text:
        raise ContractValidationError("master_request_mismatch")
    if scope.step_id != step.step_id or scope.invocation_id != invocation.invocation_id:
        raise ContractValidationError("invocation_binding_mismatch")
    if scope.specialist_request_id != invocation.specialist_request_id:
        raise ContractValidationError("specialist_request_id_mismatch")
    if scope.specialist is not invocation.specialist:
        raise ContractValidationError("specialist_mismatch")
    if not isinstance(scope.purpose, MasterPurpose) or scope.purpose is not invocation.purpose:
        raise ContractValidationError("invocation_binding_mismatch")
    if (
        not isinstance(scope.requirement, MasterRequirement)
        or scope.requirement is not invocation.requirement
    ):
        raise ContractValidationError("invocation_binding_mismatch")
    if not isinstance(scope.capability, AgentCapability) or scope.capability is not step.capability:
        raise ContractValidationError("capability_step_mismatch")
    if (
        not isinstance(scope.temporal_scopes, tuple)
        or any(not isinstance(item, TemporalScope) for item in scope.temporal_scopes)
        or scope.temporal_scopes != step.temporal_scopes
    ):
        raise ContractValidationError("invalid_temporal_scopes")
    if scope.historical_start_utc != step.historical_start_utc:
        raise ContractValidationError("invocation_binding_mismatch")
    if scope.historical_end_utc != step.historical_end_utc:
        raise ContractValidationError("invocation_binding_mismatch")
    if not isinstance(scope.gates, tuple) or any(
        not isinstance(item, MasterRouteGate) for item in scope.gates
    ):
        raise ContractValidationError("invalid_gates")
    if scope.gates != step.gates:
        raise ContractValidationError("invocation_binding_mismatch")
    if not isinstance(scope.depends_on, tuple) or any(
        not isinstance(item, str) for item in scope.depends_on
    ):
        raise ContractValidationError("invalid_depends_on")
    if scope.depends_on != step.depends_on:
        raise ContractValidationError("invocation_binding_mismatch")
    if not isinstance(scope.structural_ready_to_execute, bool):
        raise ContractValidationError("invalid_ready_to_execute")
    if scope.structural_ready_to_execute is not step.ready_to_execute:
        raise ContractValidationError("invocation_binding_mismatch")
    if not isinstance(scope.needs_persisted_evidence, bool):
        raise ContractValidationError("invalid_needs_persisted_evidence")
    if scope.needs_persisted_evidence is not step.needs_persisted_evidence:
        raise ContractValidationError("invocation_binding_mismatch")


@dataclass(frozen=True)
class MasterExecutionScope:
    """One denied audit record. It cannot be redeemed for execution.

    ``planning_context``, ``route_plan``, ``planned_step``, and ``descriptor``
    are the retained anchors. Copied fields are checked against those anchors
    on every construction, including ``dataclasses.replace``. ``route_plan``
    is how the selected step object is distinguished from a foreign copy. The
    plan is not a parallel evidence bundle.

    ``structural_ready_to_execute`` copies the plan flag and is not
    permission. ``missing_prerequisites`` and ``denial_categories`` are the
    full closed sets, including planning-session origin, application
    principal, execution policy, read-resource scope, and execution deadline.
    Those names stay unresolved. ``execution_permission_granted`` and
    ``redeemable`` cannot be true.
    """

    planning_context: MasterPlanningContext
    route_plan: MasterRoutePlan
    planned_step: MasterRouteStepPlan
    descriptor: MasterSpecialistAdapterDescriptor
    master_request_id: str
    correlation_id: str
    application_request_time_utc: str
    canonical_user_text: str
    step_id: str
    invocation_id: str
    specialist_request_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    requirement: MasterRequirement
    capability: AgentCapability
    temporal_scopes: tuple[TemporalScope, ...]
    historical_start_utc: str | None
    historical_end_utc: str | None
    gates: tuple[MasterRouteGate, ...]
    depends_on: tuple[str, ...]
    structural_ready_to_execute: bool
    needs_persisted_evidence: bool
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    execution_permission_granted: bool = False
    executable: bool = False
    redeemable: bool = False
    dispatch_permitted: bool = False
    stops_before_execution_authorization: bool = True
    end_to_end_fulfilled: bool = False
    target_authorization_granted: bool = False
    missing_prerequisites: tuple[MasterExecutionPrerequisite, ...] = _MISSING_PREREQUISITES
    denial_categories: tuple[MasterExecutionDenialCategory, ...] = _DENIAL_CATEGORIES
    schema_version: str = MASTER_EXECUTION_SCOPE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_EXECUTION_SCOPE_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _reject_authority_promotion(
            source_authenticity=self.source_authenticity,
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            executable=self.executable,
            execution_permission_granted=self.execution_permission_granted,
            redeemable=self.redeemable,
            dispatch_permitted=self.dispatch_permitted,
        )
        _closed(
            self.request_coverage,
            MasterRequestCoverage.NOT_ESTABLISHED,
            "request_coverage_not_established",
        )
        if self.end_to_end_fulfilled is not False:
            raise ContractValidationError("end_to_end_fulfillment_not_granted")
        if self.stops_before_execution_authorization is not True:
            raise ContractValidationError("execution_not_granted")
        if self.target_authorization_granted is not False:
            raise ContractValidationError("target_authorization_not_granted")
        if self.missing_prerequisites != _MISSING_PREREQUISITES:
            raise ContractValidationError("prerequisite_not_satisfied")
        if self.denial_categories != _DENIAL_CATEGORIES:
            raise ContractValidationError("execution_denial_not_cleared")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        _require_binding(self)


def bind_master_execution_scope(
    context: MasterPlanningContext,
    plan: MasterRoutePlan,
    step: MasterRouteStepPlan,
    descriptor: MasterSpecialistAdapterDescriptor,
    *,
    source_authenticity: object = MasterSourceAuthenticity.NOT_ESTABLISHED,
    production_authority: object = MasterProductionAuthority.DENIED,
    execution_authority: object = MasterRouteExecutionAuthority.DENIED,
    executable: object = False,
    execution_permission_granted: object = False,
    redeemable: object = False,
    dispatch_permitted: object = False,
) -> MasterExecutionScope:
    """Bind one selected plan step. The result is not permission to execute.

    ``step`` must be the object in ``plan.steps``. A copy with the same public
    fields is foreign. The returned scope does not retain a parallel contract
    bundle, so evidence attached to a bundle cannot be stored here.
    """

    _reject_authority_promotion(
        source_authenticity=source_authenticity,
        production_authority=production_authority,
        execution_authority=execution_authority,
        executable=executable,
        execution_permission_granted=execution_permission_granted,
        redeemable=redeemable,
        dispatch_permitted=dispatch_permitted,
    )
    if not isinstance(context, MasterPlanningContext):
        raise ContractValidationError("invalid_planning_context")
    if not isinstance(plan, MasterRoutePlan):
        raise ContractValidationError("invalid_route_plan")
    if not isinstance(step, MasterRouteStepPlan):
        raise ContractValidationError("invalid_route_step_plan")
    if not isinstance(descriptor, MasterSpecialistAdapterDescriptor):
        raise ContractValidationError("invalid_adapter_descriptor")
    invocation = step.invocation
    specialist_request_id = invocation.specialist_request_id
    correlation_id = invocation.correlation_id
    application_request_time_utc = invocation.application_request_time_utc
    if not isinstance(specialist_request_id, str):
        raise ContractValidationError("specialist_request_id_required")
    if not isinstance(correlation_id, str):
        raise ContractValidationError("correlation_id_required")
    if not isinstance(application_request_time_utc, str):
        raise ContractValidationError("application_time_mismatch")
    return MasterExecutionScope(
        planning_context=context,
        route_plan=plan,
        planned_step=step,
        descriptor=descriptor,
        master_request_id=context.master_request_id,
        correlation_id=correlation_id,
        application_request_time_utc=application_request_time_utc,
        canonical_user_text=context.canonical_user_text,
        step_id=step.step_id,
        invocation_id=invocation.invocation_id,
        specialist_request_id=specialist_request_id,
        specialist=invocation.specialist,
        purpose=invocation.purpose,
        requirement=invocation.requirement,
        capability=step.capability,
        temporal_scopes=step.temporal_scopes,
        historical_start_utc=step.historical_start_utc,
        historical_end_utc=step.historical_end_utc,
        gates=step.gates,
        depends_on=step.depends_on,
        structural_ready_to_execute=step.ready_to_execute,
        needs_persisted_evidence=step.needs_persisted_evidence,
    )


def bind_planned_execution_scopes(
    context: MasterPlanningContext,
    plan: MasterRoutePlan,
    descriptors: tuple[MasterSpecialistAdapterDescriptor, ...],
) -> tuple[MasterExecutionScope, ...]:
    """Bind every planned invocation. The same specialist may appear more than once.

    Descriptors must cover each plan step once. Result order follows the plan,
    not the descriptor order. The tuple does not contain a parallel bundle.
    """

    if not isinstance(context, MasterPlanningContext):
        raise ContractValidationError("invalid_planning_context")
    if not isinstance(plan, MasterRoutePlan):
        raise ContractValidationError("invalid_route_plan")
    if not isinstance(descriptors, tuple) or any(
        not isinstance(item, MasterSpecialistAdapterDescriptor) for item in descriptors
    ):
        raise ContractValidationError("invalid_adapter_descriptors")
    if len(descriptors) != len(plan.steps):
        raise ContractValidationError("invocation_not_in_plan")
    if len({item.invocation_id for item in descriptors}) != len(descriptors):
        raise ContractValidationError("duplicate_invocation_id")
    paired: dict[str, MasterSpecialistAdapterDescriptor] = {}
    for descriptor in descriptors:
        matches = [
            step
            for step in plan.steps
            if step.invocation.invocation_id == descriptor.invocation_id
            and step.step_id == descriptor.step_id
        ]
        if len(matches) != 1:
            raise ContractValidationError("descriptor_mismatch")
        step = matches[0]
        if step.invocation.invocation_id in paired:
            raise ContractValidationError("duplicate_invocation_id")
        paired[step.invocation.invocation_id] = descriptor
    if len(paired) != len(plan.steps):
        raise ContractValidationError("invocation_not_in_plan")
    return tuple(
        bind_master_execution_scope(
            context,
            plan,
            step,
            paired[step.invocation.invocation_id],
        )
        for step in plan.steps
    )
