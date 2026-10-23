"""Shared interface for later Master response, integration, and safety work.

This module records the inputs those workstreams must share. It does not
synthesize an operational answer, dispatch a specialist, or authorize
production execution. MA2 planning remains execution-denied. MA4 admission,
corroboration, and comparability remain the functions that perform those
checks. A caller-built MA4 result is not an input and is not proof that a
check ran.

Source authenticity stays ``NOT_ESTABLISHED``. Production authority stays
``DENIED``. Supplied evidence is not an authenticated production read.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
    _validate_bounded_text,
)
from wilvor_ai.decision_specialist_runtime_contracts import DecisionSpecialistRunResult
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import (
    LiveOpsAdmissionSources,
    MasterSourceAuthenticity,
)
from wilvor_ai.master_evidence_comparability import MasterComparisonRequest
from wilvor_ai.master_evidence_corroboration import MasterEvidenceCorroborationInput
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS,
    MAX_ROUTE_STEPS,
    MasterPlanningContext,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRoutePlan,
    MasterRouteStepPlan,
)
from wilvor_ai.specialist_contracts import SpecialistResult
from wilvor_ai.specialist_runtime_contracts import HistoricalSpecialistRunResult


MASTER_PARALLEL_CONTRACTS_SCHEMA_VERSION = "wilvor.ai.master_parallel_contracts.v1"

# One comparison for every ordered pair of distinct steps on a four-step route.
# Extra categories for the same pair consume this same budget. Oversized
# collections are rejected whole; they are not truncated.
MAX_COMPARISON_REQUESTS = MAX_ROUTE_STEPS * (MAX_ROUTE_STEPS - 1)

_SYNTHETIC_RESULT_NAMES = frozenset(
    {
        "SyntheticWorkerResult",
        "SyntheticWorkflowResult",
    }
)


class MasterResponsePolicyState(str, Enum):
    """Closed nonfactual response states. None is an operational answer."""

    NOT_EVALUATED = "NOT_EVALUATED"
    UNSUPPORTED = "UNSUPPORTED"
    UNAVAILABLE = "UNAVAILABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class MasterResponseLimitation(str, Enum):
    """Why a later response must stay nonfactual."""

    SOURCE_AUTHENTICITY_NOT_ESTABLISHED = "SOURCE_AUTHENTICITY_NOT_ESTABLISHED"
    PRODUCTION_AUTHORITY_DENIED = "PRODUCTION_AUTHORITY_DENIED"
    REQUEST_COVERAGE_NOT_ESTABLISHED = "REQUEST_COVERAGE_NOT_ESTABLISHED"
    SUPPLIED_EVIDENCE_NOT_AUTHENTICATED = "SUPPLIED_EVIDENCE_NOT_AUTHENTICATED"
    COMPARABILITY_NOT_OPERATIONAL_AGREEMENT = "COMPARABILITY_NOT_OPERATIONAL_AGREEMENT"
    LIVE_OPS_REFS_NOT_TOOL_RESULTS = "LIVE_OPS_REFS_NOT_TOOL_RESULTS"
    HISTORICAL_RUN_NOT_FABRICATED = "HISTORICAL_RUN_NOT_FABRICATED"
    NO_OPERATIONAL_CLAIM = "NO_OPERATIONAL_CLAIM"
    EXECUTION_DENIED = "EXECUTION_DENIED"


class MasterExecutionPreflightState(str, Enum):
    """Structural integration state. None of these values permits execution."""

    NOT_EXECUTABLE = "NOT_EXECUTABLE"
    BLOCKED_BY_AUTHORIZATION = "BLOCKED_BY_AUTHORIZATION"
    BLOCKED_BY_MISSING_PROVENANCE = "BLOCKED_BY_MISSING_PROVENANCE"
    CONTRACT_READY_FOR_FUTURE_INTEGRATION = "CONTRACT_READY_FOR_FUTURE_INTEGRATION"


class MasterProvenanceRequirement(str, Enum):
    """Future proof categories. This module cannot mark any of them satisfied."""

    TRUSTED_SOURCE_ADAPTER = "TRUSTED_SOURCE_ADAPTER"
    AUTHORIZED_EXECUTION_CONTEXT = "AUTHORIZED_EXECUTION_CONTEXT"
    SOURCE_READ_PROVENANCE = "SOURCE_READ_PROVENANCE"
    REQUEST_ENTITY_BINDING = "REQUEST_ENTITY_BINDING"
    OBSERVATION_EVALUATION_TIME = "OBSERVATION_EVALUATION_TIME"
    SOURCE_VERSION_INTEGRITY = "SOURCE_VERSION_INTEGRITY"
    POPULATION_SCOPE_COMPLETENESS = "POPULATION_SCOPE_COMPLETENESS"


_REQUIRED_LIMITATIONS = (
    MasterResponseLimitation.SOURCE_AUTHENTICITY_NOT_ESTABLISHED,
    MasterResponseLimitation.PRODUCTION_AUTHORITY_DENIED,
    MasterResponseLimitation.NO_OPERATIONAL_CLAIM,
    MasterResponseLimitation.EXECUTION_DENIED,
)

_STATUS_TEXT = {
    MasterResponsePolicyState.NOT_EVALUATED: (
        "Response policy has not been evaluated."
    ),
    MasterResponsePolicyState.UNSUPPORTED: (
        "The request is unsupported. No operational conclusion is available."
    ),
    MasterResponsePolicyState.UNAVAILABLE: (
        "Required evidence is unavailable. No count or zero is inferred."
    ),
    MasterResponsePolicyState.INSUFFICIENT_EVIDENCE: (
        "Supplied evidence is not sufficient for an operational claim."
    ),
}


def _enum_tuple(value: object, enum_type: type[Enum], field_name: str) -> None:
    if not isinstance(value, tuple) or any(not isinstance(item, enum_type) for item in value):
        raise ContractValidationError(f"invalid_{field_name}")


def _deny_authority(bundle: "MasterParallelDenial") -> None:
    if bundle.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
        raise ContractValidationError("source_authenticity_not_established")
    if bundle.production_authority is not MasterProductionAuthority.DENIED:
        raise ContractValidationError("production_authority_not_issued")
    if bundle.execution_authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
    if bundle.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
        raise ContractValidationError("request_coverage_not_established")
    if bundle.end_to_end_fulfilled is not False:
        raise ContractValidationError("end_to_end_fulfillment_not_granted")


class MasterParallelDenial:
    """Denial fields shared by the handoff objects. Not a serializable certificate."""

    source_authenticity: MasterSourceAuthenticity
    production_authority: MasterProductionAuthority
    execution_authority: MasterRouteExecutionAuthority
    request_coverage: MasterRequestCoverage
    end_to_end_fulfilled: bool


def _native_artifact(specialist: MasterSpecialist, artifact: object) -> None:
    if type(artifact).__name__ in _SYNTHETIC_RESULT_NAMES:
        raise ContractValidationError("synthetic_result_not_evidence")
    if specialist is MasterSpecialist.HISTORICAL_ANALYTICS and isinstance(
        artifact,
        (SpecialistResult, HistoricalSpecialistRunResult),
    ):
        return
    if specialist is MasterSpecialist.DECISION and isinstance(artifact, DecisionSpecialistRunResult):
        return
    if specialist is MasterSpecialist.LIVE_OPS and isinstance(artifact, LiveOpsAdmissionSources):
        return
    raise ContractValidationError("unsupported_artifact")


@dataclass(frozen=True)
class MasterInvocationBinding:
    """One planned step and the authorization MA2 already bound to it.

    The binding is copied from the route plan. A received artifact cannot
    create a step, an invocation id, or a request id.
    """

    step_id: str
    authorization: MasterInvocationAuthorization

    def __post_init__(self) -> None:
        if not isinstance(self.authorization, MasterInvocationAuthorization):
            raise ContractValidationError("invalid_invocation_authorization")
        errors = _validate_bounded_text(
            self.step_id,
            "step_id",
            max_length=PROVENANCE_TOKEN_MAX_LENGTH,
        )
        if errors or self.step_id != self.step_id.strip():
            raise ContractValidationError("invalid_step_id")


@dataclass(frozen=True)
class MasterParallelContractBundle(MasterParallelDenial):
    """One Master request, its plan, and the evidence inputs later layers may check.

    ``evidence_inputs`` are arguments for the existing MA4 functions. This
    object does not call those functions and does not store their results.
    A missing Historical run stays absent. Live Ops public sources stay
    public sources.
    """

    context: MasterPlanningContext
    plan: MasterRoutePlan
    evidence_inputs: tuple[MasterEvidenceCorroborationInput, ...]
    comparison_requests: tuple[MasterComparisonRequest, ...] = ()
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    end_to_end_fulfilled: bool = False
    schema_version: str = MASTER_PARALLEL_CONTRACTS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_PARALLEL_CONTRACTS_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _deny_authority(self)
        if not isinstance(self.context, MasterPlanningContext):
            raise ContractValidationError("invalid_planning_context")
        if not isinstance(self.plan, MasterRoutePlan):
            raise ContractValidationError("invalid_route_plan")
        if self.context.master_request_id != self.plan.master_request_id:
            raise ContractValidationError("master_request_mismatch")
        if self.context.canonical_user_text != self.plan.canonical_user_text:
            raise ContractValidationError("master_request_mismatch")
        if not isinstance(self.evidence_inputs, tuple) or any(
            not isinstance(item, MasterEvidenceCorroborationInput) for item in self.evidence_inputs
        ):
            raise ContractValidationError("invalid_evidence_inputs")
        if not isinstance(self.comparison_requests, tuple):
            raise ContractValidationError("invalid_comparison_requests")
        if len(self.comparison_requests) > MAX_COMPARISON_REQUESTS:
            raise ContractValidationError("excessive_comparison_requests")
        if any(not isinstance(item, MasterComparisonRequest) for item in self.comparison_requests):
            raise ContractValidationError("invalid_comparison_requests")
        if len(self.plan.steps) > MAX_ROUTE_STEPS or len(self.evidence_inputs) > MAX_ROUTE_STEPS:
            raise ContractValidationError("excessive_plan_size")
        if (
            sum(step.invocation.specialist is MasterSpecialist.DECISION for step in self.plan.steps)
            > MAX_DECISION_STEPS
        ):
            raise ContractValidationError("decision_fanout_not_permitted")
        planned = _planned_steps(self.context, self.plan)
        seen: list[str] = []
        for item in self.evidence_inputs:
            authorization = item.admission_input.authorization
            if authorization.master_request_id != self.context.master_request_id:
                raise ContractValidationError("master_request_mismatch")
            step = planned.get(authorization.invocation_id)
            if step is None:
                raise ContractValidationError("invocation_not_in_plan")
            if step.invocation != authorization:
                raise ContractValidationError("invocation_binding_mismatch")
            _native_artifact(authorization.specialist, item.admission_input.artifact)
            if item.historical_run is not None and (
                authorization.specialist is not MasterSpecialist.HISTORICAL_ANALYTICS
            ):
                raise ContractValidationError("historical_run_not_applicable")
            seen.append(authorization.invocation_id)
        if len(seen) != len(set(seen)):
            raise ContractValidationError("duplicate_invocation_id")
        compared: list[tuple[str, str, str]] = []
        for request in self.comparison_requests:
            if request.master_request_id != self.context.master_request_id:
                raise ContractValidationError("master_request_mismatch")
            if request.left_invocation_id not in planned or request.right_invocation_id not in planned:
                raise ContractValidationError("invocation_not_in_plan")
            if request.left_invocation_id == request.right_invocation_id:
                raise ContractValidationError("duplicate_invocation_id")
            key = (
                request.left_invocation_id,
                request.right_invocation_id,
                request.category.value,
            )
            compared.append(key)
        if len(compared) != len(set(compared)):
            raise ContractValidationError("duplicate_comparison_request")

    def bindings(self) -> tuple[MasterInvocationBinding, ...]:
        """Plan bindings only. Result order cannot add an invocation."""

        return tuple(
            MasterInvocationBinding(step.step_id, step.invocation) for step in self.plan.steps
        )


def _planned_steps(
    context: MasterPlanningContext,
    plan: MasterRoutePlan,
) -> dict[str, MasterRouteStepPlan]:
    planned: dict[str, MasterRouteStepPlan] = {}
    for step in plan.steps:
        if not isinstance(step, MasterRouteStepPlan):
            raise ContractValidationError("invalid_route_plan")
        authorization = step.invocation
        if authorization.master_request_id != context.master_request_id:
            raise ContractValidationError("master_request_mismatch")
        if authorization.correlation_id != context.correlation_id:
            raise ContractValidationError("correlation_mismatch")
        if authorization.application_request_time_utc != context.application_request_time_utc:
            raise ContractValidationError("application_time_mismatch")
        if authorization.invocation_id in planned:
            raise ContractValidationError("duplicate_invocation_id")
        planned[authorization.invocation_id] = step
    return planned


@dataclass(frozen=True)
class MasterResponsePolicyHandoff(MasterParallelDenial):
    """Offline policy input for a later response evaluator.

    ``status_text`` is a fixed sentence for ``state``. It is not model prose
    and it is not an aircraft, hazard, risk, or recommendation claim.
    """

    workflow: MasterParallelContractBundle
    state: MasterResponsePolicyState
    limitations: tuple[MasterResponseLimitation, ...]
    status_text: str
    claims_admitted: bool = False
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    end_to_end_fulfilled: bool = False
    schema_version: str = MASTER_PARALLEL_CONTRACTS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_PARALLEL_CONTRACTS_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _deny_authority(self)
        if not isinstance(self.workflow, MasterParallelContractBundle):
            raise ContractValidationError("invalid_parallel_contract")
        if not isinstance(self.state, MasterResponsePolicyState):
            raise ContractValidationError("invalid_response_policy_state")
        _enum_tuple(self.limitations, MasterResponseLimitation, "limitations")
        missing = [item for item in _REQUIRED_LIMITATIONS if item not in self.limitations]
        if missing:
            raise ContractValidationError("missing_response_limitation")
        if self.status_text != _STATUS_TEXT[self.state]:
            raise ContractValidationError("invalid_status_text")
        if self.claims_admitted is not False:
            raise ContractValidationError("operational_claim_not_admitted")

    @property
    def master_request_id(self) -> str:
        return self.workflow.context.master_request_id

    @property
    def invocation_ids(self) -> tuple[str, ...]:
        return tuple(step.invocation.invocation_id for step in self.workflow.plan.steps)


@dataclass(frozen=True)
class MasterExecutionPreflightReport(MasterParallelDenial):
    """MA6 structural state. MA2 cannot represent granted execution.

    ``CONTRACT_READY_FOR_FUTURE_INTEGRATION`` means the shared contract can
    be reviewed later. It does not satisfy provenance and it does not permit
    dispatch. Every provenance requirement stays missing.
    """

    workflow: MasterParallelContractBundle
    state: MasterExecutionPreflightState
    missing_provenance: tuple[MasterProvenanceRequirement, ...]
    executable: bool = False
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    end_to_end_fulfilled: bool = False
    schema_version: str = MASTER_PARALLEL_CONTRACTS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_PARALLEL_CONTRACTS_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _deny_authority(self)
        if not isinstance(self.workflow, MasterParallelContractBundle):
            raise ContractValidationError("invalid_parallel_contract")
        if not isinstance(self.state, MasterExecutionPreflightState):
            raise ContractValidationError("invalid_execution_preflight_state")
        if self.executable is not False:
            raise ContractValidationError("execution_not_granted")
        _enum_tuple(self.missing_provenance, MasterProvenanceRequirement, "missing_provenance")
        if self.missing_provenance != tuple(MasterProvenanceRequirement):
            raise ContractValidationError("provenance_requirement_not_satisfied")


def execution_preflight_for(workflow: MasterParallelContractBundle) -> MasterExecutionPreflightReport:
    """Report the only preflight this module can justify.

    A plan still has MA2 execution authority ``DENIED``, so the state is
    ``BLOCKED_BY_AUTHORIZATION`` when any step exists. An empty plan is
    ``NOT_EXECUTABLE``. Provenance remains entirely missing either way.
    """

    if not isinstance(workflow, MasterParallelContractBundle):
        raise ContractValidationError("invalid_parallel_contract")
    state = (
        MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION
        if workflow.plan.steps
        else MasterExecutionPreflightState.NOT_EXECUTABLE
    )
    return MasterExecutionPreflightReport(
        workflow=workflow,
        state=state,
        missing_provenance=tuple(MasterProvenanceRequirement),
    )


def response_policy_for(
    workflow: MasterParallelContractBundle,
    state: MasterResponsePolicyState,
    limitations: tuple[MasterResponseLimitation, ...] = _REQUIRED_LIMITATIONS,
) -> MasterResponsePolicyHandoff:
    """Bind a closed status sentence to one workflow. This does not render facts."""

    if not isinstance(state, MasterResponsePolicyState):
        raise ContractValidationError("invalid_response_policy_state")
    return MasterResponsePolicyHandoff(
        workflow=workflow,
        state=state,
        limitations=limitations,
        status_text=_STATUS_TEXT[state],
    )


@dataclass(frozen=True)
class MasterSafetyHandoff:
    """Minimum record a later offline safety test can read. Not a test runner."""

    workflow: MasterParallelContractBundle
    response_policy: MasterResponsePolicyHandoff
    execution_preflight: MasterExecutionPreflightReport
    schema_version: str = MASTER_PARALLEL_CONTRACTS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_PARALLEL_CONTRACTS_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if not isinstance(self.workflow, MasterParallelContractBundle):
            raise ContractValidationError("invalid_parallel_contract")
        if not isinstance(self.response_policy, MasterResponsePolicyHandoff):
            raise ContractValidationError("invalid_response_policy")
        if not isinstance(self.execution_preflight, MasterExecutionPreflightReport):
            raise ContractValidationError("invalid_execution_preflight")
        if self.response_policy.workflow is not self.workflow:
            raise ContractValidationError("master_request_mismatch")
        if self.execution_preflight.workflow is not self.workflow:
            raise ContractValidationError("master_request_mismatch")
        _deny_authority(self.workflow)
        _deny_authority(self.response_policy)
        _deny_authority(self.execution_preflight)


def historical_run_if_supplied(
    item: MasterEvidenceCorroborationInput,
) -> HistoricalSpecialistRunResult | None:
    """Return the supplied Historical run, or None. Never synthesize one."""

    if not isinstance(item, MasterEvidenceCorroborationInput):
        raise ContractValidationError("invalid_evidence_inputs")
    if item.historical_run is None:
        return None
    if item.admission_input.authorization.specialist is not MasterSpecialist.HISTORICAL_ANALYTICS:
        raise ContractValidationError("historical_run_not_applicable")
    if not isinstance(item.historical_run, HistoricalSpecialistRunResult):
        raise ContractValidationError("invalid_historical_run")
    return item.historical_run


def live_ops_public_sources(artifact: Any) -> LiveOpsAdmissionSources | None:
    """Return public Live Ops sources without turning them into tool results."""

    if isinstance(artifact, LiveOpsAdmissionSources):
        return artifact
    return None
