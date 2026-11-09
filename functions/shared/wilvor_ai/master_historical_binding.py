"""Non-executing binding from one Master-planned Historical step to a SpecialistRequest.

The binder reads a validated Master parallel bundle only while checking the
descriptor. The returned object copies the selected step's planning facts. It
does not retain the bundle, so supplied evidence and trusted runtime objects
on that bundle are not part of the binding. The descriptor names one
invocation. Other Historical steps may remain in the plan; specialist type
alone does not choose among them. The binder does not mint request ids, call
``HistoricalAnalyticsSpecialist``, construct historical operations, call a
model provider, or fabricate a run.

Clocks stay distinct:

- ``application_request_time_utc`` is when the application accepted the request.
- ``requested_evaluation_time_utc`` is the evaluation instant the plan asked
  for, when one was supplied. It is not evidence.
- ``unbound_plan_window_start_utc`` and ``unbound_plan_window_end_utc`` are
  the Master plan window. They are not native tool arguments and they are
  not appended to the specialist text.
- ``evaluated_as_of_utc`` is evidence-backed and stays unset. The planned
  timestamp is never copied into it.

``SpecialistRequest.request_id`` is the planned ``specialist_request_id``.
It is not historical evidence, an evaluation instant, or correlation proof.
``HistoricalSpecialistTrustedContext`` is not constructed.

``HistoricalSpecialistRunResult`` remains an intermediate result.
``SpecialistResult`` remains the final artifact.
``finalize_historical_specialist_run`` remains the verification entrypoint
and is not called here. Failure and incomplete coverage are not a zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_parallel_contracts import (
    MasterParallelContractBundle,
    MasterProvenanceRequirement,
)
from wilvor_ai.master_routing import (
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteStepPlan,
)
from wilvor_ai.master_specialist_adapters import (
    MasterAdapterExecutionState,
    MasterSpecialistAdapterDescriptor,
)
from wilvor_ai.specialist_contracts import (
    HistoricalSpecialistTrustedContext,
    SpecialistRequest,
    SpecialistResult,
)
from wilvor_ai.specialist_runtime_contracts import HistoricalSpecialistRunResult


MASTER_HISTORICAL_BINDING_SCHEMA_VERSION = "wilvor.ai.master_historical_binding.v1"
HISTORICAL_FINALIZATION_ENTRYPOINT = "finalize_historical_specialist_run"


class MasterHistoricalPlanScope(str, Enum):
    """The planned historical window is recorded and not a native argument."""

    UNBOUND = "UNBOUND"


def _false(value: object, code: str) -> None:
    if value is not False:
        raise ContractValidationError(code)


def _none(value: object, code: str) -> None:
    if value is not None:
        raise ContractValidationError(code)


def _closed(value: object, denied: object, code: str) -> None:
    if value is not denied:
        raise ContractValidationError(code)


def _require_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ContractValidationError(code)
    return value


def _historical_step(
    bundle: MasterParallelContractBundle,
    descriptor: MasterSpecialistAdapterDescriptor,
) -> MasterRouteStepPlan:
    """Return the planned step named by ``descriptor.invocation_id``.

    The invocation id must resolve to one step. Identities are then checked
    against that step. The step must be Historical Analytics. Another
    Historical step with its own invocation id is left unbound.
    """

    if not isinstance(bundle, MasterParallelContractBundle):
        raise ContractValidationError("invalid_parallel_contract")
    if not isinstance(descriptor, MasterSpecialistAdapterDescriptor):
        raise ContractValidationError("invalid_adapter_descriptor")
    _closed(
        bundle.execution_authority,
        MasterRouteExecutionAuthority.DENIED,
        "execution_authority_not_granted",
    )
    _closed(
        bundle.plan.execution_authority,
        MasterRouteExecutionAuthority.DENIED,
        "execution_authority_not_granted",
    )
    _closed(
        bundle.source_authenticity,
        MasterSourceAuthenticity.NOT_ESTABLISHED,
        "source_authenticity_not_established",
    )
    _closed(
        bundle.production_authority,
        MasterProductionAuthority.DENIED,
        "production_authority_not_issued",
    )
    _closed(
        bundle.request_coverage,
        MasterRequestCoverage.NOT_ESTABLISHED,
        "request_coverage_not_established",
    )
    _false(bundle.end_to_end_fulfilled, "end_to_end_fulfillment_not_granted")
    _closed(
        descriptor.execution_state,
        MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION,
        "execution_not_granted",
    )
    _closed(
        descriptor.execution_authority,
        MasterRouteExecutionAuthority.DENIED,
        "execution_authority_not_granted",
    )
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
    _false(descriptor.historical_run_fabricated, "historical_run_not_fabricated")
    _false(descriptor.timestamp_equivalence_established, "timestamp_equivalence_not_established")

    matched = tuple(
        step
        for step in bundle.plan.steps
        if step.invocation.invocation_id == descriptor.invocation_id
    )
    if not matched:
        raise ContractValidationError("invocation_not_in_plan")
    if len(matched) != 1:
        raise ContractValidationError("duplicate_invocation_id")
    step = matched[0]
    authorization = step.invocation
    if descriptor.master_request_id != bundle.context.master_request_id:
        raise ContractValidationError("master_request_mismatch")
    if descriptor.master_request_id != authorization.master_request_id:
        raise ContractValidationError("master_request_mismatch")
    if descriptor.step_id != step.step_id:
        raise ContractValidationError("invocation_binding_mismatch")
    if (
        descriptor.specialist is not authorization.specialist
        or authorization.specialist is not MasterSpecialist.HISTORICAL_ANALYTICS
    ):
        raise ContractValidationError("specialist_mismatch")
    if (
        descriptor.purpose is not authorization.purpose
        or descriptor.requirement is not authorization.requirement
    ):
        raise ContractValidationError("invocation_binding_mismatch")
    if descriptor.specialist_request_id != authorization.specialist_request_id:
        raise ContractValidationError("specialist_request_id_mismatch")
    if bundle.context.canonical_user_text != bundle.plan.canonical_user_text:
        raise ContractValidationError("master_request_mismatch")

    specialist_request_id = _require_text(
        authorization.specialist_request_id,
        "specialist_request_id_required",
    )
    correlation_id = _require_text(authorization.correlation_id, "correlation_id_required")
    _require_text(authorization.application_request_time_utc, "invalid_application_request_time_utc")
    if authorization.application_request_time_utc != bundle.context.application_request_time_utc:
        raise ContractValidationError("application_time_mismatch")
    if correlation_id != bundle.context.correlation_id:
        raise ContractValidationError("correlation_mismatch")
    identities = (
        authorization.master_request_id,
        authorization.invocation_id,
        specialist_request_id,
        correlation_id,
    )
    if len(set(identities)) != 4:
        raise ContractValidationError("request_identity_not_distinct")
    if step.historical_start_utc is None or step.historical_end_utc is None:
        raise ContractValidationError("missing_historical_interval")
    return step


@dataclass(frozen=True)
class MasterHistoricalPlanningFacts:
    """Planning facts copied from one Historical step.

    This is not a ``MasterParallelContractBundle`` and it carries no supplied
    evidence, specialist run, or trusted runtime context.
    """

    descriptor: MasterSpecialistAdapterDescriptor
    authorization: MasterInvocationAuthorization
    canonical_user_text: str
    unbound_plan_window_start_utc: str
    unbound_plan_window_end_utc: str

    def __post_init__(self) -> None:
        if isinstance(self.descriptor, MasterParallelContractBundle) or isinstance(
            self.authorization,
            MasterParallelContractBundle,
        ):
            raise ContractValidationError("evidence_bundle_not_retained")
        if not isinstance(self.descriptor, MasterSpecialistAdapterDescriptor):
            raise ContractValidationError("invalid_adapter_descriptor")
        if not isinstance(self.authorization, MasterInvocationAuthorization):
            raise ContractValidationError("invalid_invocation_authorization")
        if self.descriptor.specialist is not MasterSpecialist.HISTORICAL_ANALYTICS:
            raise ContractValidationError("specialist_mismatch")
        if self.authorization.specialist is not MasterSpecialist.HISTORICAL_ANALYTICS:
            raise ContractValidationError("specialist_mismatch")
        if self.descriptor.master_request_id != self.authorization.master_request_id:
            raise ContractValidationError("master_request_mismatch")
        if self.descriptor.invocation_id != self.authorization.invocation_id:
            raise ContractValidationError("invocation_binding_mismatch")
        if (
            self.descriptor.purpose is not self.authorization.purpose
            or self.descriptor.requirement is not self.authorization.requirement
        ):
            raise ContractValidationError("invocation_binding_mismatch")
        if self.descriptor.specialist_request_id != self.authorization.specialist_request_id:
            raise ContractValidationError("specialist_request_id_mismatch")
        specialist_request_id = _require_text(
            self.authorization.specialist_request_id,
            "specialist_request_id_required",
        )
        correlation_id = _require_text(
            self.authorization.correlation_id,
            "correlation_id_required",
        )
        _require_text(
            self.authorization.application_request_time_utc,
            "invalid_application_request_time_utc",
        )
        _require_text(self.canonical_user_text, "canonical_user_text_mismatch")
        _require_text(self.unbound_plan_window_start_utc, "missing_historical_interval")
        _require_text(self.unbound_plan_window_end_utc, "missing_historical_interval")
        if self.unbound_plan_window_start_utc == self.unbound_plan_window_end_utc:
            raise ContractValidationError("missing_historical_interval")
        identities = (
            self.authorization.master_request_id,
            self.authorization.invocation_id,
            specialist_request_id,
            correlation_id,
        )
        if len(set(identities)) != 4:
            raise ContractValidationError("request_identity_not_distinct")
        _closed(
            self.descriptor.execution_state,
            MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION,
            "execution_not_granted",
        )
        _closed(
            self.descriptor.execution_authority,
            MasterRouteExecutionAuthority.DENIED,
            "execution_authority_not_granted",
        )
        _closed(
            self.descriptor.source_authenticity,
            MasterSourceAuthenticity.NOT_ESTABLISHED,
            "source_authenticity_not_established",
        )
        _closed(
            self.descriptor.production_authority,
            MasterProductionAuthority.DENIED,
            "production_authority_not_issued",
        )
        _false(self.descriptor.historical_run_fabricated, "historical_run_not_fabricated")


@dataclass(frozen=True)
class MasterHistoricalBinding:
    """One planned Historical invocation bound to a native request. Not an execution.

    The bundle used to validate the descriptor is not stored. Replacing a
    closed field, or making an identity disagree with the copied planning
    facts, fails in ``__post_init__``.
    """

    planning: MasterHistoricalPlanningFacts
    descriptor: MasterSpecialistAdapterDescriptor
    authorization: MasterInvocationAuthorization
    canonical_user_text: str
    step_id: str
    invocation_id: str
    specialist_request_id: str
    correlation_id: str
    application_request_time_utc: str
    requested_evaluation_time_utc: str | None
    unbound_plan_window_start_utc: str
    unbound_plan_window_end_utc: str
    specialist_request: SpecialistRequest
    plan_window_scope: MasterHistoricalPlanScope = MasterHistoricalPlanScope.UNBOUND
    structured_request_arguments: None = None
    trusted_context: HistoricalSpecialistTrustedContext | None = None
    evaluated_as_of_utc: str | None = None
    historical_run: HistoricalSpecialistRunResult | None = None
    specialist_result: SpecialistResult | None = None
    inferred_count: int | None = None
    executed_tool_call_count: int | None = None
    execution_state: MasterAdapterExecutionState = (
        MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
    )
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    end_to_end_fulfilled: bool = False
    dispatch_permitted: bool = False
    stops_before_execution_authorization: bool = True
    historical_window_bound_to_request: bool = False
    window_evaluated: bool = False
    window_covered_by_data: bool = False
    request_id_is_historical_evidence: bool = False
    request_id_is_evaluation_time: bool = False
    request_id_is_correlation_proof: bool = False
    native_correlation_resolved: bool = False
    native_specialist_request_id_resolved: bool = False
    failure_interpreted_as_zero: bool = False
    incomplete_coverage_interpreted_as_zero: bool = False
    evidence_issued: bool = False
    finalization_performed: bool = False
    finalization_entrypoint: str = HISTORICAL_FINALIZATION_ENTRYPOINT
    missing_provenance: tuple[MasterProvenanceRequirement, ...] = tuple(
        MasterProvenanceRequirement
    )
    schema_version: str = MASTER_HISTORICAL_BINDING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_HISTORICAL_BINDING_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if not isinstance(self.authorization, MasterInvocationAuthorization):
            raise ContractValidationError("invalid_invocation_authorization")
        if not isinstance(self.specialist_request, SpecialistRequest):
            raise ContractValidationError("invalid_specialist_request")
        _closed(
            self.plan_window_scope,
            MasterHistoricalPlanScope.UNBOUND,
            "historical_window_not_authorized",
        )
        _closed(
            self.execution_state,
            MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION,
            "execution_not_granted",
        )
        _closed(
            self.execution_authority,
            MasterRouteExecutionAuthority.DENIED,
            "execution_authority_not_granted",
        )
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
            self.request_coverage,
            MasterRequestCoverage.NOT_ESTABLISHED,
            "request_coverage_not_established",
        )
        _false(self.end_to_end_fulfilled, "end_to_end_fulfillment_not_granted")
        _false(self.dispatch_permitted, "execution_not_granted")
        if self.stops_before_execution_authorization is not True:
            raise ContractValidationError("execution_not_granted")
        _false(self.historical_window_bound_to_request, "historical_window_not_authorized")
        _false(self.window_evaluated, "historical_window_not_evaluated")
        _false(self.window_covered_by_data, "historical_window_not_covered")
        _false(self.request_id_is_historical_evidence, "request_id_not_historical_evidence")
        _false(self.request_id_is_evaluation_time, "request_id_not_evaluation_time")
        _false(self.request_id_is_correlation_proof, "request_id_not_correlation_proof")
        _false(self.native_correlation_resolved, "request_id_not_correlation_proof")
        _false(
            self.native_specialist_request_id_resolved,
            "native_specialist_request_id_not_resolved",
        )
        _false(self.failure_interpreted_as_zero, "failure_not_verified_zero")
        _false(
            self.incomplete_coverage_interpreted_as_zero,
            "incomplete_coverage_not_verified_zero",
        )
        _false(self.evidence_issued, "verified_evidence_not_issued")
        _false(self.finalization_performed, "historical_finalization_not_performed")
        _none(self.structured_request_arguments, "historical_window_not_authorized")
        _none(self.trusted_context, "trusted_context_not_authorized")
        _none(self.evaluated_as_of_utc, "evaluated_as_of_not_evidence")
        _none(self.historical_run, "historical_run_not_fabricated")
        _none(self.specialist_result, "verified_evidence_not_issued")
        _none(self.inferred_count, "failure_not_verified_zero")
        _none(self.executed_tool_call_count, "historical_window_not_evaluated")
        if self.finalization_entrypoint != HISTORICAL_FINALIZATION_ENTRYPOINT:
            raise ContractValidationError("invalid_finalization_entrypoint")
        if self.missing_provenance != tuple(MasterProvenanceRequirement):
            raise ContractValidationError("provenance_requirement_not_satisfied")
        if isinstance(self.planning, MasterParallelContractBundle):
            raise ContractValidationError("evidence_bundle_not_retained")
        if not isinstance(self.planning, MasterHistoricalPlanningFacts):
            raise ContractValidationError("invalid_historical_planning_facts")
        if self.descriptor is not self.planning.descriptor:
            raise ContractValidationError("invocation_binding_mismatch")
        if self.authorization is not self.planning.authorization:
            raise ContractValidationError("invocation_binding_mismatch")
        authorization = self.authorization
        if self.canonical_user_text != self.planning.canonical_user_text:
            raise ContractValidationError("canonical_user_text_mismatch")
        if self.step_id != self.descriptor.step_id:
            raise ContractValidationError("invocation_binding_mismatch")
        if (
            self.invocation_id != authorization.invocation_id
            or self.invocation_id != self.descriptor.invocation_id
        ):
            raise ContractValidationError("invocation_binding_mismatch")
        if self.specialist_request_id != authorization.specialist_request_id:
            raise ContractValidationError("specialist_request_id_mismatch")
        if self.correlation_id != authorization.correlation_id:
            raise ContractValidationError("correlation_mismatch")
        if self.application_request_time_utc != authorization.application_request_time_utc:
            raise ContractValidationError("application_time_mismatch")
        if self.requested_evaluation_time_utc != authorization.requested_evaluation_time_utc:
            raise ContractValidationError("requested_evaluation_time_mismatch")
        if (
            self.unbound_plan_window_start_utc != self.planning.unbound_plan_window_start_utc
            or self.unbound_plan_window_end_utc != self.planning.unbound_plan_window_end_utc
        ):
            raise ContractValidationError("historical_window_not_authorized")
        if self.specialist_request.text != self.canonical_user_text:
            raise ContractValidationError("canonical_user_text_mismatch")
        if self.specialist_request.request_id != authorization.specialist_request_id:
            raise ContractValidationError("specialist_request_id_mismatch")
        if self.specialist_request.request_id == authorization.correlation_id:
            raise ContractValidationError("request_id_not_correlation_proof")
        identities = (
            authorization.master_request_id,
            self.invocation_id,
            self.specialist_request_id,
            self.correlation_id,
        )
        if any(item is None for item in identities) or len(set(identities)) != 4:
            raise ContractValidationError("request_identity_not_distinct")


def prepare_master_historical_binding(
    bundle: MasterParallelContractBundle,
    descriptor: MasterSpecialistAdapterDescriptor,
) -> MasterHistoricalBinding:
    """Bind the Historical invocation named by ``descriptor`` without executing it.

    The specialist request text is the canonical user text only. The planned
    historical window is copied beside that request and is not authorized as
    a tool argument. No trusted ``as_of_utc`` is derived from the application
    clock, the requested evaluation instant, or the local wall clock.
    """

    step = _historical_step(bundle, descriptor)
    authorization = step.invocation
    specialist_request_id = _require_text(
        authorization.specialist_request_id,
        "specialist_request_id_required",
    )
    correlation_id = _require_text(authorization.correlation_id, "correlation_id_required")
    application_request_time_utc = _require_text(
        authorization.application_request_time_utc,
        "invalid_application_request_time_utc",
    )
    start = step.historical_start_utc
    end = step.historical_end_utc
    if start is None or end is None:
        raise ContractValidationError("missing_historical_interval")
    planning = MasterHistoricalPlanningFacts(
        descriptor=descriptor,
        authorization=authorization,
        canonical_user_text=bundle.context.canonical_user_text,
        unbound_plan_window_start_utc=start,
        unbound_plan_window_end_utc=end,
    )
    return MasterHistoricalBinding(
        planning=planning,
        descriptor=descriptor,
        authorization=authorization,
        canonical_user_text=bundle.context.canonical_user_text,
        step_id=step.step_id,
        invocation_id=authorization.invocation_id,
        specialist_request_id=specialist_request_id,
        correlation_id=correlation_id,
        application_request_time_utc=application_request_time_utc,
        requested_evaluation_time_utc=authorization.requested_evaluation_time_utc,
        unbound_plan_window_start_utc=start,
        unbound_plan_window_end_utc=end,
        specialist_request=SpecialistRequest(
            text=bundle.context.canonical_user_text,
            request_id=specialist_request_id,
        ),
    )


def refuse_historical_binding_evidence(artifact: object) -> None:
    """Refuse to treat a supplied artifact as evidence from this binder.

    An intermediate run is not a final artifact. A ``SpecialistResult`` is
    the final artifact type, and this binder does not issue one. The artifact
    is not finalized and its coverage is not read as zero.
    """

    if isinstance(artifact, HistoricalSpecialistRunResult):
        raise ContractValidationError("historical_run_not_final_artifact")
    if isinstance(artifact, SpecialistResult):
        raise ContractValidationError("verified_evidence_not_issued")
    raise ContractValidationError("historical_evidence_not_issued")


__all__ = [
    "HISTORICAL_FINALIZATION_ENTRYPOINT",
    "MASTER_HISTORICAL_BINDING_SCHEMA_VERSION",
    "MasterHistoricalBinding",
    "MasterHistoricalPlanningFacts",
    "MasterHistoricalPlanScope",
    "prepare_master_historical_binding",
    "refuse_historical_binding_evidence",
]
