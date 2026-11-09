"""Non-executable descriptors for specialists already named by an MA2 plan.

A descriptor copies identity from ``MasterRoutePlan``. It does not construct
a new authorization, select a Decision target from user text, treat a
callsign as an authenticated aircraft, equate timestamps across specialists,
fabricate a Historical run, or turn Live Ops public claims into tool results.

An incoming result cannot add an invocation. MA3 synthetic results cannot
become production evidence. The descriptor holds no service client, cloud
session, model provider, dispatch callable, tool callback, read credential,
or trusted-source certificate.

The integration sequence stops before separately reviewed execution
authorization:

1. MA2 authorized planning
2. Separately reviewed execution authorization
3. Trusted adapter invocation
4. Native specialist result
5. Provenance validation
6. MA4 checks
7. MA5 response policy

MA4 admission and corroboration remain the evidence checks, and they are not
source authentication. This module does not call them and does not instantiate
a Historical, Decision, or Live Ops runtime.

The same prerequisites listed for execution preflight are still missing here:
reviewed execution authority, trusted adapter identity, execution context,
source-read provenance, entity binding, specialist clocks, source versions,
population scope, a separately authorized Decision target, and dashboard
integration that leaves the Operational API Lambda off the AI path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
    _validate_bounded_text,
)
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_parallel_contracts import MasterParallelContractBundle
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS,
    MAX_ROUTE_STEPS,
    MasterRouteExecutionAuthority,
)


_SYNTHETIC_RESULT_NAMES = frozenset(
    {
        "SyntheticWorkerResult",
        "SyntheticWorkflowResult",
    }
)


class MasterAdapterExecutionState(str, Enum):
    """The only adapter state this module can record."""

    BLOCKED_BEFORE_EXECUTION_AUTHORIZATION = "BLOCKED_BEFORE_EXECUTION_AUTHORIZATION"


def _token(value: object, field_name: str) -> None:
    errors = _validate_bounded_text(
        value,
        field_name,
        max_length=PROVENANCE_TOKEN_MAX_LENGTH,
    )
    if errors or value != value.strip():
        raise ContractValidationError(f"invalid_{field_name}")


def _closed(value: object, denied: object, code: str) -> None:
    if value is not denied:
        raise ContractValidationError(code)


@dataclass(frozen=True)
class MasterSpecialistAdapterDescriptor:
    """Planned binding only. Nothing on this object can be called to run a specialist."""

    master_request_id: str
    step_id: str
    invocation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    requirement: MasterRequirement
    specialist_request_id: str
    execution_state: MasterAdapterExecutionState = (
        MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
    )
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    decision_target_trusted: bool = False
    callsign_authenticated: bool = False
    timestamp_equivalence_established: bool = False
    historical_run_fabricated: bool = False
    live_ops_refs_are_tool_results: bool = False

    def __post_init__(self) -> None:
        _token(self.master_request_id, "master_request_id")
        _token(self.step_id, "step_id")
        _token(self.invocation_id, "invocation_id")
        _token(self.specialist_request_id, "specialist_request_id")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        if not isinstance(self.requirement, MasterRequirement):
            raise ContractValidationError("invalid_requirement")
        if (
            self.execution_state
            is not MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
        ):
            raise ContractValidationError("execution_not_granted")
        _closed(
            self.source_authenticity,
            MasterSourceAuthenticity.NOT_ESTABLISHED,
            "source_authenticity_not_established",
        )
        _closed(
            self.production_authority,
            MasterProductionAuthority.DENIED,
            "production_authority_not_issued",
        )
        _closed(
            self.execution_authority,
            MasterRouteExecutionAuthority.DENIED,
            "execution_authority_not_granted",
        )
        if self.decision_target_trusted is not False:
            raise ContractValidationError("target_authorization_not_granted")
        if self.callsign_authenticated is not False:
            raise ContractValidationError("callsign_not_authenticated_aircraft")
        if self.timestamp_equivalence_established is not False:
            raise ContractValidationError("timestamp_equivalence_not_established")
        if self.historical_run_fabricated is not False:
            raise ContractValidationError("historical_run_not_fabricated")
        if self.live_ops_refs_are_tool_results is not False:
            raise ContractValidationError("live_ops_refs_not_tool_results")


def _validate_adapter_collection(
    bundle: MasterParallelContractBundle,
    descriptors: tuple[MasterSpecialistAdapterDescriptor, ...],
) -> None:
    if not isinstance(bundle, MasterParallelContractBundle):
        raise ContractValidationError("invalid_parallel_contract")
    if not isinstance(descriptors, tuple):
        raise ContractValidationError("invalid_adapter_descriptors")
    if len(descriptors) > MAX_ROUTE_STEPS or len(bundle.plan.steps) > MAX_ROUTE_STEPS:
        raise ContractValidationError("excessive_plan_size")
    if any(not isinstance(item, MasterSpecialistAdapterDescriptor) for item in descriptors):
        raise ContractValidationError("invalid_adapter_descriptors")
    invocation_ids = [item.invocation_id for item in descriptors]
    if len(invocation_ids) != len(set(invocation_ids)):
        raise ContractValidationError("duplicate_invocation_id")
    if (
        sum(item.specialist is MasterSpecialist.DECISION for item in descriptors)
        > MAX_DECISION_STEPS
        or sum(
            step.invocation.specialist is MasterSpecialist.DECISION for step in bundle.plan.steps
        )
        > MAX_DECISION_STEPS
    ):
        raise ContractValidationError("decision_fanout_not_permitted")
    planned = {step.invocation.invocation_id: step for step in bundle.plan.steps}
    for item in descriptors:
        if item.master_request_id != bundle.context.master_request_id:
            raise ContractValidationError("master_request_mismatch")
        step = planned.get(item.invocation_id)
        if step is None:
            raise ContractValidationError("invocation_not_in_plan")
        if item.step_id != step.step_id:
            raise ContractValidationError("invocation_binding_mismatch")
        if item.specialist is not step.invocation.specialist:
            raise ContractValidationError("specialist_mismatch")
        if item.purpose is not step.invocation.purpose or item.requirement is not step.invocation.requirement:
            raise ContractValidationError("invocation_binding_mismatch")
        if item.specialist_request_id != step.invocation.specialist_request_id:
            raise ContractValidationError("specialist_request_id_mismatch")
        if step.target_authorization_granted:
            raise ContractValidationError("target_authorization_not_granted")


@dataclass(frozen=True)
class MasterSpecialistAdapterSet:
    """The descriptors accepted for one bundle. The set cannot be executed."""

    bundle: MasterParallelContractBundle
    descriptors: tuple[MasterSpecialistAdapterDescriptor, ...]

    def __post_init__(self) -> None:
        _validate_adapter_collection(self.bundle, self.descriptors)


def describe_planned_adapters(
    bundle: MasterParallelContractBundle,
) -> MasterSpecialistAdapterSet:
    """Copy one descriptor from each planned step. Results cannot add a step."""

    if not isinstance(bundle, MasterParallelContractBundle):
        raise ContractValidationError("invalid_parallel_contract")
    descriptors = tuple(
        MasterSpecialistAdapterDescriptor(
            master_request_id=step.invocation.master_request_id,
            step_id=step.step_id,
            invocation_id=step.invocation.invocation_id,
            specialist=step.invocation.specialist,
            purpose=step.invocation.purpose,
            requirement=step.invocation.requirement,
            specialist_request_id=step.invocation.specialist_request_id,
        )
        for step in bundle.plan.steps
    )
    return accept_planned_adapters(bundle, descriptors)


def accept_planned_adapters(
    bundle: MasterParallelContractBundle,
    descriptors: tuple[MasterSpecialistAdapterDescriptor, ...],
) -> MasterSpecialistAdapterSet:
    """Accept descriptors only when every identity equals the approved plan."""

    return MasterSpecialistAdapterSet(bundle=bundle, descriptors=descriptors)


def refuse_adapter_evidence(
    descriptor: MasterSpecialistAdapterDescriptor,
    artifact: object,
) -> None:
    """Refuse to mint production evidence from an adapter or a synthetic result.

    The artifact is not executed and is not stored. A synthetic worker or
    workflow result keeps the synthetic refusal. Any other object is refused
    as adapter evidence as well.
    """

    if not isinstance(descriptor, MasterSpecialistAdapterDescriptor):
        raise ContractValidationError("invalid_adapter_descriptor")
    if type(artifact).__name__ in _SYNTHETIC_RESULT_NAMES:
        raise ContractValidationError("synthetic_result_not_evidence")
    raise ContractValidationError("adapter_evidence_not_issued")
