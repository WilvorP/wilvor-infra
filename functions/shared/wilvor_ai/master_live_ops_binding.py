"""Non-executing preparation of one public Live Ops request from a Master plan.

The binder checks that one adapter descriptor names one Live Ops step on a
``MasterParallelContractBundle``. It copies the canonical user text and the
planned specialist request id into a ``LiveOpsRunRequest``.

The result keeps the planning context, the descriptor, and that selected
step. It does not keep the parallel bundle, so supplied evidence and trusted
runtime handles stay out of the binding. Construction and ``dataclasses.replace``
both compare every copied identity with the retained step.

Region text, callsigns, entity references, and other model-proposed hints
stay untrusted. They are withheld from the public request's selector and
entity fields. When those values cannot be represented as an authorized
selector, the binding records that limitation explicitly.

No trusted runtime context is constructed. The Master plan does not carry
authenticated table handles, an authorized runtime clock, or authenticated
source reads. Those remain a separately reviewed application concern.

The result preserves execution authority ``DENIED``, production authority
``DENIED``, and source authenticity ``NOT_ESTABLISHED``. It has no dispatch
operation. It does not create an aircraft-impact count, a verified zero, or
an admission source from a fabricated specialist result.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    AgentCapability,
    ContractValidationError,
    TemporalScope,
    _validate_bounded_text,
)
from wilvor_ai.live_ops_public_contracts import LiveOpsRunRequest
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_parallel_contracts import MasterParallelContractBundle
from wilvor_ai.master_routing import (
    MasterHintTrust,
    MasterPlanningContext,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRouteHints,
    MasterRouteStepPlan,
)
from wilvor_ai.master_specialist_adapters import (
    MasterAdapterExecutionState,
    MasterSpecialistAdapterDescriptor,
    accept_planned_adapters,
)


MASTER_LIVE_OPS_BINDING_SCHEMA_VERSION = "wilvor.ai.master_live_ops_binding.v1"

_LIVE_OPS_CAPABILITY = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT


class MasterLiveOpsBindingLimitation(str, Enum):
    """Closed reasons a prepared request is not an authorized Live Ops run."""

    EXECUTION_DENIED = "EXECUTION_DENIED"
    PRODUCTION_AUTHORITY_DENIED = "PRODUCTION_AUTHORITY_DENIED"
    SOURCE_AUTHENTICITY_NOT_ESTABLISHED = "SOURCE_AUTHENTICITY_NOT_ESTABLISHED"
    TRUSTED_RUNTIME_CONTEXT_ABSENT = "TRUSTED_RUNTIME_CONTEXT_ABSENT"
    SELECTION_AND_ENTITY_HINTS_UNTRUSTED = "SELECTION_AND_ENTITY_HINTS_UNTRUSTED"
    NO_AUTHORITATIVE_AIRCRAFT_IMPACT_COUNT = "NO_AUTHORITATIVE_AIRCRAFT_IMPACT_COUNT"
    VERIFIED_ZERO_NOT_ESTABLISHED = "VERIFIED_ZERO_NOT_ESTABLISHED"
    LIVE_OPS_REFS_NOT_TOOL_RESULTS = "LIVE_OPS_REFS_NOT_TOOL_RESULTS"
    ADMISSION_SOURCES_NOT_CREATED = "ADMISSION_SOURCES_NOT_CREATED"
    REGION_NOT_VERIFIED_SELECTOR = "REGION_NOT_VERIFIED_SELECTOR"
    CALLSIGN_NOT_AUTHENTICATED_AIRCRAFT = "CALLSIGN_NOT_AUTHENTICATED_AIRCRAFT"
    ENTITY_REFERENCE_NOT_AUTHORIZED = "ENTITY_REFERENCE_NOT_AUTHORIZED"
    PLANNED_SCOPE_NOT_FAITHFULLY_REPRESENTABLE = "PLANNED_SCOPE_NOT_FAITHFULLY_REPRESENTABLE"


_ALWAYS_LIMITATIONS = (
    MasterLiveOpsBindingLimitation.EXECUTION_DENIED,
    MasterLiveOpsBindingLimitation.PRODUCTION_AUTHORITY_DENIED,
    MasterLiveOpsBindingLimitation.SOURCE_AUTHENTICITY_NOT_ESTABLISHED,
    MasterLiveOpsBindingLimitation.TRUSTED_RUNTIME_CONTEXT_ABSENT,
    MasterLiveOpsBindingLimitation.SELECTION_AND_ENTITY_HINTS_UNTRUSTED,
    MasterLiveOpsBindingLimitation.NO_AUTHORITATIVE_AIRCRAFT_IMPACT_COUNT,
    MasterLiveOpsBindingLimitation.VERIFIED_ZERO_NOT_ESTABLISHED,
    MasterLiveOpsBindingLimitation.LIVE_OPS_REFS_NOT_TOOL_RESULTS,
    MasterLiveOpsBindingLimitation.ADMISSION_SOURCES_NOT_CREATED,
)


def _token(value: object, field_name: str) -> None:
    errors = _validate_bounded_text(
        value,
        field_name,
        max_length=PROVENANCE_TOKEN_MAX_LENGTH,
    )
    if errors or value != value.strip():
        raise ContractValidationError(f"invalid_{field_name}")


def _optional_token(value: object, field_name: str) -> None:
    if value is None:
        return
    _token(value, field_name)


def _closed(value: object, denied: object, code: str) -> None:
    if value is not denied:
        raise ContractValidationError(code)


def _reject_forged_authority(
    *,
    source_authenticity: object,
    production_authority: object,
    execution_authority: object,
    executable: object,
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
    if descriptor.live_ops_refs_are_tool_results is not False:
        raise ContractValidationError("live_ops_refs_not_tool_results")


def _matching_live_step(
    bundle: MasterParallelContractBundle,
    descriptor: MasterSpecialistAdapterDescriptor,
) -> MasterRouteStepPlan:
    """Return the one planned Live Ops step named by ``descriptor``.

    Identity checks are the adapter-descriptor rules: master request, step,
    invocation, specialist, purpose, requirement, and specialist request id.
    """

    accept_planned_adapters(bundle, (descriptor,))
    if descriptor.specialist is not MasterSpecialist.LIVE_OPS:
        raise ContractValidationError("specialist_mismatch")
    matched = [
        step
        for step in bundle.plan.steps
        if step.invocation.invocation_id == descriptor.invocation_id
        and step.step_id == descriptor.step_id
        and step.invocation.specialist is descriptor.specialist
        and step.invocation.purpose is descriptor.purpose
        and step.invocation.requirement is descriptor.requirement
        and step.invocation.specialist_request_id == descriptor.specialist_request_id
        and step.invocation.master_request_id == descriptor.master_request_id
    ]
    if len(matched) != 1:
        raise ContractValidationError("invocation_not_in_plan")
    step = matched[0]
    invocation = step.invocation
    if invocation.specialist is not MasterSpecialist.LIVE_OPS:
        raise ContractValidationError("specialist_mismatch")
    if invocation.specialist_request_id is None:
        raise ContractValidationError("specialist_request_id_required")
    if invocation.correlation_id is None:
        raise ContractValidationError("correlation_id_required")
    if invocation.application_request_time_utc is None:
        raise ContractValidationError("application_time_mismatch")
    if step.capability is not _LIVE_OPS_CAPABILITY:
        raise ContractValidationError("capability_step_mismatch")
    if step.temporal_scopes != (TemporalScope.CURRENT,):
        raise ContractValidationError("invalid_temporal_scopes")
    if step.hint_trust is not MasterHintTrust.UNTRUSTED:
        raise ContractValidationError("selection_hints_not_trusted")
    if step.target_authorization_granted:
        raise ContractValidationError("target_authorization_not_granted")
    if not isinstance(step.untrusted_hints, MasterRouteHints):
        raise ContractValidationError("invalid_route_hints")
    return step


def _context_agrees(context: MasterPlanningContext, step: MasterRouteStepPlan) -> None:
    invocation = step.invocation
    if context.master_request_id != invocation.master_request_id:
        raise ContractValidationError("master_request_mismatch")
    if context.correlation_id != invocation.correlation_id:
        raise ContractValidationError("correlation_mismatch")
    if context.application_request_time_utc != invocation.application_request_time_utc:
        raise ContractValidationError("application_time_mismatch")


def binding_limitations(
    hints: MasterRouteHints,
    gates: tuple[MasterRouteGate, ...],
    *,
    expected_aircraft_id: str | None,
    expected_recommendation_id: str | None,
) -> tuple[MasterLiveOpsBindingLimitation, ...]:
    """Limitations required by the withheld plan scope. Order follows the enum."""

    if not isinstance(hints, MasterRouteHints):
        raise ContractValidationError("invalid_route_hints")
    if not isinstance(gates, tuple) or any(not isinstance(item, MasterRouteGate) for item in gates):
        raise ContractValidationError("invalid_gates")
    _optional_token(expected_aircraft_id, "expected_aircraft_id")
    _optional_token(expected_recommendation_id, "expected_recommendation_id")
    selected = set(_ALWAYS_LIMITATIONS)
    scope_gap = False
    if hints.region_text is not None or MasterRouteGate.REGION_VALIDATION_PENDING in gates:
        selected.add(MasterLiveOpsBindingLimitation.REGION_NOT_VERIFIED_SELECTOR)
        scope_gap = True
    if hints.callsign is not None:
        selected.add(MasterLiveOpsBindingLimitation.CALLSIGN_NOT_AUTHENTICATED_AIRCRAFT)
        scope_gap = True
    if (
        hints.typed_aircraft_id is not None
        or hints.recommendation_id is not None
        or bool(hints.population_aircraft_ids)
        or expected_aircraft_id is not None
        or expected_recommendation_id is not None
    ):
        selected.add(MasterLiveOpsBindingLimitation.ENTITY_REFERENCE_NOT_AUTHORIZED)
        scope_gap = True
    if scope_gap:
        selected.add(MasterLiveOpsBindingLimitation.PLANNED_SCOPE_NOT_FAITHFULLY_REPRESENTABLE)
    return tuple(item for item in MasterLiveOpsBindingLimitation if item in selected)


def _descriptor_matches_step(
    descriptor: MasterSpecialistAdapterDescriptor,
    step: MasterRouteStepPlan,
) -> None:
    invocation = step.invocation
    if descriptor.specialist is not MasterSpecialist.LIVE_OPS:
        raise ContractValidationError("specialist_mismatch")
    if descriptor.master_request_id != invocation.master_request_id:
        raise ContractValidationError("master_request_mismatch")
    if descriptor.invocation_id != invocation.invocation_id:
        raise ContractValidationError("invocation_binding_mismatch")
    if descriptor.step_id != step.step_id:
        raise ContractValidationError("invocation_binding_mismatch")
    if descriptor.specialist is not invocation.specialist:
        raise ContractValidationError("specialist_mismatch")
    if (
        descriptor.purpose is not invocation.purpose
        or descriptor.requirement is not invocation.requirement
    ):
        raise ContractValidationError("invocation_binding_mismatch")
    if descriptor.specialist_request_id != invocation.specialist_request_id:
        raise ContractValidationError("specialist_request_id_mismatch")


def _require_live_step(step: MasterRouteStepPlan) -> None:
    invocation = step.invocation
    if invocation.specialist is not MasterSpecialist.LIVE_OPS:
        raise ContractValidationError("specialist_mismatch")
    if not isinstance(invocation.specialist_request_id, str):
        raise ContractValidationError("specialist_request_id_required")
    if not isinstance(invocation.correlation_id, str):
        raise ContractValidationError("correlation_id_required")
    if not isinstance(invocation.application_request_time_utc, str):
        raise ContractValidationError("application_time_mismatch")
    if step.capability is not _LIVE_OPS_CAPABILITY:
        raise ContractValidationError("capability_step_mismatch")
    if step.temporal_scopes != (TemporalScope.CURRENT,):
        raise ContractValidationError("invalid_temporal_scopes")
    if step.hint_trust is not MasterHintTrust.UNTRUSTED:
        raise ContractValidationError("selection_hints_not_trusted")
    if step.target_authorization_granted:
        raise ContractValidationError("target_authorization_not_granted")
    if not isinstance(step.untrusted_hints, MasterRouteHints):
        raise ContractValidationError("invalid_route_hints")


def _public_request(canonical_user_text: str, specialist_request_id: str) -> LiveOpsRunRequest:
    """Build the public request from plan text and the planned request id only."""

    return LiveOpsRunRequest(
        user_text=canonical_user_text,
        request_id=specialist_request_id,
    )


@dataclass(frozen=True)
class MasterLiveOpsBinding:
    """One prepared Live Ops request. Construction does not authorize a run.

    ``planning_context``, ``descriptor``, and ``planned_step`` are the
    retained authority for identity. Copied fields are checked against that
    step on every construction, including ``dataclasses.replace``.
    ``aircraft_impact_count`` stays ``None``. An empty withheld hint list is
    not a verified zero. ``structural_ready_to_execute`` copies the plan flag
    and is not dispatch permission.
    """

    planning_context: MasterPlanningContext
    descriptor: MasterSpecialistAdapterDescriptor
    planned_step: MasterRouteStepPlan
    master_request_id: str
    correlation_id: str
    application_request_time_utc: str
    invocation_id: str
    step_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    requirement: MasterRequirement
    specialist_request_id: str
    capability: AgentCapability
    temporal_scopes: tuple[TemporalScope, ...]
    gates: tuple[MasterRouteGate, ...]
    structural_ready_to_execute: bool
    canonical_user_text: str
    request: LiveOpsRunRequest
    withheld_untrusted_hints: MasterRouteHints
    limitations: tuple[MasterLiveOpsBindingLimitation, ...]
    withheld_expected_aircraft_id: str | None = None
    withheld_expected_recommendation_id: str | None = None
    hint_trust: MasterHintTrust = MasterHintTrust.UNTRUSTED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    end_to_end_fulfilled: bool = False
    dispatch_permitted: bool = False
    executable: bool = False
    stops_before_execution_authorization: bool = True
    trusted_context_created: bool = False
    selectors_authorized: bool = False
    callsign_authenticated: bool = False
    live_ops_refs_are_tool_results: bool = False
    admission_sources_created: bool = False
    aircraft_impact_count: int | None = None
    verified_zero: bool = False
    schema_version: str = MASTER_LIVE_OPS_BINDING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_LIVE_OPS_BINDING_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _reject_forged_authority(
            source_authenticity=self.source_authenticity,
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            executable=self.executable,
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
        if self.trusted_context_created is not False:
            raise ContractValidationError("trusted_runtime_context_absent")
        if self.selectors_authorized is not False:
            raise ContractValidationError("selection_hints_not_trusted")
        if self.callsign_authenticated is not False:
            raise ContractValidationError("callsign_not_authenticated_aircraft")
        if self.live_ops_refs_are_tool_results is not False:
            raise ContractValidationError("live_ops_refs_not_tool_results")
        if self.admission_sources_created is not False:
            raise ContractValidationError("admission_sources_not_created")
        if self.aircraft_impact_count is not None:
            raise ContractValidationError("aircraft_impact_count_not_authoritative")
        if self.verified_zero is not False:
            raise ContractValidationError("verified_zero_not_established")
        if self.specialist is not MasterSpecialist.LIVE_OPS:
            raise ContractValidationError("specialist_mismatch")
        if not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        if not isinstance(self.requirement, MasterRequirement):
            raise ContractValidationError("invalid_requirement")
        if self.capability is not _LIVE_OPS_CAPABILITY:
            raise ContractValidationError("capability_step_mismatch")
        if self.temporal_scopes != (TemporalScope.CURRENT,):
            raise ContractValidationError("invalid_temporal_scopes")
        if self.hint_trust is not MasterHintTrust.UNTRUSTED:
            raise ContractValidationError("selection_hints_not_trusted")
        if not isinstance(self.structural_ready_to_execute, bool):
            raise ContractValidationError("invalid_ready_to_execute")
        _token(self.master_request_id, "master_request_id")
        _token(self.correlation_id, "correlation_id")
        _token(self.invocation_id, "invocation_id")
        _token(self.step_id, "step_id")
        _token(self.specialist_request_id, "specialist_request_id")
        if not isinstance(self.application_request_time_utc, str):
            raise ContractValidationError("invalid_application_request_time_utc")
        if not isinstance(self.canonical_user_text, str) or not self.canonical_user_text.strip():
            raise ContractValidationError("invalid_canonical_user_text")
        _require_planned_identity(self)
        if not isinstance(self.request, LiveOpsRunRequest):
            raise ContractValidationError("invalid_live_ops_request")
        if self.request.user_text != self.canonical_user_text:
            raise ContractValidationError("invocation_binding_mismatch")
        if self.request.request_id != self.specialist_request_id:
            raise ContractValidationError("specialist_request_id_mismatch")
        if self.request.conversation_id is not None or self.request.prior_turns != ():
            raise ContractValidationError("invocation_binding_mismatch")
        if self.request.selector_hints is not None or self.request.entity_hints != ():
            raise ContractValidationError("selection_hints_not_trusted")
        if not isinstance(self.withheld_untrusted_hints, MasterRouteHints):
            raise ContractValidationError("invalid_route_hints")
        _optional_token(self.withheld_expected_aircraft_id, "expected_aircraft_id")
        _optional_token(self.withheld_expected_recommendation_id, "expected_recommendation_id")
        required = binding_limitations(
            self.withheld_untrusted_hints,
            self.gates,
            expected_aircraft_id=self.withheld_expected_aircraft_id,
            expected_recommendation_id=self.withheld_expected_recommendation_id,
        )
        if self.limitations != required:
            raise ContractValidationError("missing_binding_limitation")


def _require_planned_identity(binding: MasterLiveOpsBinding) -> None:
    """Reject a binding whose copied identity differs from the retained step."""

    context = binding.planning_context
    descriptor = binding.descriptor
    step = binding.planned_step
    if not isinstance(context, MasterPlanningContext):
        raise ContractValidationError("invalid_planning_context")
    if not isinstance(descriptor, MasterSpecialistAdapterDescriptor):
        raise ContractValidationError("invalid_adapter_descriptor")
    if not isinstance(step, MasterRouteStepPlan):
        raise ContractValidationError("invalid_route_step_plan")
    _reject_descriptor_promotion(descriptor)
    _require_live_step(step)
    _context_agrees(context, step)
    _descriptor_matches_step(descriptor, step)
    invocation = step.invocation
    if binding.master_request_id != invocation.master_request_id:
        raise ContractValidationError("master_request_mismatch")
    if binding.master_request_id != context.master_request_id:
        raise ContractValidationError("master_request_mismatch")
    if binding.correlation_id != invocation.correlation_id:
        raise ContractValidationError("correlation_mismatch")
    if binding.application_request_time_utc != invocation.application_request_time_utc:
        raise ContractValidationError("application_time_mismatch")
    if binding.canonical_user_text != context.canonical_user_text:
        raise ContractValidationError("master_request_mismatch")
    if binding.invocation_id != invocation.invocation_id:
        raise ContractValidationError("invocation_binding_mismatch")
    if binding.step_id != step.step_id:
        raise ContractValidationError("invocation_binding_mismatch")
    if binding.specialist is not invocation.specialist:
        raise ContractValidationError("specialist_mismatch")
    if binding.purpose is not invocation.purpose:
        raise ContractValidationError("invocation_binding_mismatch")
    if binding.requirement is not invocation.requirement:
        raise ContractValidationError("invocation_binding_mismatch")
    if binding.specialist_request_id != invocation.specialist_request_id:
        raise ContractValidationError("specialist_request_id_mismatch")
    if binding.capability is not step.capability:
        raise ContractValidationError("capability_step_mismatch")
    if binding.temporal_scopes != step.temporal_scopes:
        raise ContractValidationError("invalid_temporal_scopes")
    if binding.gates != step.gates:
        raise ContractValidationError("invocation_binding_mismatch")
    if binding.structural_ready_to_execute is not step.ready_to_execute:
        raise ContractValidationError("invocation_binding_mismatch")
    if binding.withheld_untrusted_hints != step.untrusted_hints:
        raise ContractValidationError("invocation_binding_mismatch")
    if binding.hint_trust is not step.hint_trust:
        raise ContractValidationError("selection_hints_not_trusted")
    if binding.withheld_expected_aircraft_id != invocation.expected_aircraft_id:
        raise ContractValidationError("invocation_binding_mismatch")
    if binding.withheld_expected_recommendation_id != invocation.expected_recommendation_id:
        raise ContractValidationError("invocation_binding_mismatch")


def prepare_master_live_ops_binding(
    bundle: MasterParallelContractBundle,
    descriptor: MasterSpecialistAdapterDescriptor,
    *,
    source_authenticity: object = MasterSourceAuthenticity.NOT_ESTABLISHED,
    production_authority: object = MasterProductionAuthority.DENIED,
    execution_authority: object = MasterRouteExecutionAuthority.DENIED,
    executable: object = False,
    dispatch_permitted: object = False,
) -> MasterLiveOpsBinding:
    """Validate one planned Live Ops step and build its public request.

    The returned object is not permission to run the specialist. Selector
    and entity hints on the plan are withheld from the public request.
    Supplied evidence on the bundle is not read and is not copied.
    """

    _reject_forged_authority(
        source_authenticity=source_authenticity,
        production_authority=production_authority,
        execution_authority=execution_authority,
        executable=executable,
        dispatch_permitted=dispatch_permitted,
    )
    if not isinstance(bundle, MasterParallelContractBundle):
        raise ContractValidationError("invalid_parallel_contract")
    if not isinstance(descriptor, MasterSpecialistAdapterDescriptor):
        raise ContractValidationError("invalid_adapter_descriptor")
    _reject_descriptor_promotion(descriptor)
    step = _matching_live_step(bundle, descriptor)
    _context_agrees(bundle.context, step)
    if bundle.plan.canonical_user_text != bundle.context.canonical_user_text:
        raise ContractValidationError("master_request_mismatch")
    if bundle.plan.execution_authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
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
    canonical_user_text = bundle.plan.canonical_user_text
    request = _public_request(canonical_user_text, specialist_request_id)
    if request.selector_hints is not None or request.entity_hints != ():
        raise ContractValidationError("selection_hints_not_trusted")
    limitations = binding_limitations(
        step.untrusted_hints,
        step.gates,
        expected_aircraft_id=invocation.expected_aircraft_id,
        expected_recommendation_id=invocation.expected_recommendation_id,
    )
    return MasterLiveOpsBinding(
        planning_context=bundle.context,
        descriptor=descriptor,
        planned_step=step,
        master_request_id=invocation.master_request_id,
        correlation_id=correlation_id,
        application_request_time_utc=application_request_time_utc,
        invocation_id=invocation.invocation_id,
        step_id=step.step_id,
        specialist=invocation.specialist,
        purpose=invocation.purpose,
        requirement=invocation.requirement,
        specialist_request_id=specialist_request_id,
        capability=step.capability,
        temporal_scopes=step.temporal_scopes,
        gates=step.gates,
        structural_ready_to_execute=step.ready_to_execute,
        canonical_user_text=canonical_user_text,
        request=request,
        withheld_untrusted_hints=step.untrusted_hints,
        limitations=limitations,
        withheld_expected_aircraft_id=invocation.expected_aircraft_id,
        withheld_expected_recommendation_id=invocation.expected_recommendation_id,
        hint_trust=step.hint_trust,
    )
