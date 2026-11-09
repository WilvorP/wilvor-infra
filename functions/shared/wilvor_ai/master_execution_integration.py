"""Offline handoff from one planning session to execution inspection.

The handoff reads a process-local ``MasterPlanningSessionRegistry``. It uses
the retained decision, the retained plan, and the one invocation that session
already selected. It does not open a session, select a second invocation, or
call ``authorize_route`` again.

An authorized plan is inspected with the existing public functions:

1. ``MasterParallelContractBundle`` with empty evidence and comparison inputs
2. ``describe_planned_adapters``
3. ``bind_master_execution_scope`` for the selected invocation only
4. ``inspect_route_gates`` with no caller observations
5. ``evaluate_execution_preflight``

The empty bundle exists only because those adapter and preflight functions
require one. It is not stored on the planning session and it carries no
specialist evidence.

``AUTHORIZED_PLAN`` can set ``planning_succeeded``. It cannot set
``execution_authorized``. Refused, unsupported, and clarification decisions
are returned without a plan, a bundle, a scope, a gate inspection, or a
preflight.

Every planned step and dependency is copied onto the inspection. The current
registry can select only one invocation, so ``multi_invocation_execution_supported``
stays false. Descriptors for the other planned steps are not execution scopes.

Execution, dispatch, provenance authentication, and factual response
generation stay unavailable. Master execution authority stays ``DENIED``.
Production authority stays ``DENIED``. Source authenticity stays
``NOT_ESTABLISHED``. Request coverage stays ``NOT_ESTABLISHED``.
"""

from __future__ import annotations

from dataclasses import dataclass

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterProductionAuthority,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_execution_preflight import (
    MasterExecutionPreflightEvaluation,
    evaluate_execution_preflight,
)
from wilvor_ai.master_execution_scope import (
    MasterExecutionScope,
    bind_master_execution_scope,
)
from wilvor_ai.master_gate_resolution import (
    MasterGateEffectiveState,
    MasterGateInspection,
    MasterGateResolutionReason,
    inspect_route_gates,
)
from wilvor_ai.master_parallel_contracts import (
    MasterExecutionPreflightState,
    MasterParallelContractBundle,
    MasterProvenanceRequirement,
)
from wilvor_ai.master_planning_session import (
    MasterPlanningSessionRecord,
    MasterPlanningSessionRegistry,
)
from wilvor_ai.master_routing import (
    MasterRequestCoverage,
    MasterRouteDecision,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRouteOutcome,
    MasterRoutePlan,
    MasterRouteStepPlan,
)
from wilvor_ai.master_specialist_adapters import (
    MasterSpecialistAdapterDescriptor,
    describe_planned_adapters,
)


MASTER_EXECUTION_INTEGRATION_SCHEMA_VERSION = "wilvor.ai.master_execution_integration.v1"

_POLICY_OUTCOMES = frozenset(
    {
        MasterRouteOutcome.REFUSED,
        MasterRouteOutcome.UNSUPPORTED,
        MasterRouteOutcome.CLARIFICATION_REQUIRED,
    }
)


def _reject_authority_promotion(
    *,
    source_authenticity: object,
    production_authority: object,
    execution_authority: object,
    request_coverage: object,
    execution_authorized: object,
    executable: object,
    execution_permission_granted: object,
    redeemable: object,
    dispatch_permitted: object,
    authoritative_satisfaction: object,
    provenance_authenticated: object,
    factual_response_available: object,
    multi_invocation_execution_supported: object,
) -> None:
    if source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
        raise ContractValidationError("source_authenticity_not_established")
    if production_authority is not MasterProductionAuthority.DENIED:
        raise ContractValidationError("production_authority_not_issued")
    if execution_authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
    if request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
        raise ContractValidationError("request_coverage_not_established")
    if execution_permission_granted is not False:
        raise ContractValidationError("execution_permission_not_granted")
    if redeemable is not False:
        raise ContractValidationError("scope_not_redeemable")
    if authoritative_satisfaction is not False:
        raise ContractValidationError("authoritative_satisfaction_not_available")
    if provenance_authenticated is not False:
        raise ContractValidationError("source_authenticity_not_established")
    if factual_response_available is not False:
        raise ContractValidationError("operational_claim_not_admitted")
    if multi_invocation_execution_supported is not False:
        raise ContractValidationError("multiple_invocations_rejected")
    if (
        execution_authorized is not False
        or executable is not False
        or dispatch_permitted is not False
    ):
        raise ContractValidationError("execution_not_granted")


def _require_original_invocation(
    presented: object,
    selected: MasterInvocationAuthorization,
) -> MasterInvocationAuthorization:
    if presented is selected:
        return selected
    if isinstance(presented, (list, tuple, set)):
        raise ContractValidationError("multiple_invocations_rejected")
    if isinstance(presented, str):
        raise ContractValidationError("copied_invocation_identifier")
    if not isinstance(presented, MasterInvocationAuthorization):
        raise ContractValidationError("forged_invocation_rejected")
    if presented.invocation_id == selected.invocation_id:
        raise ContractValidationError("copied_invocation_identifier")
    raise ContractValidationError("wrong_invocation")


def _selected_step(
    plan: MasterRoutePlan,
    selected: MasterInvocationAuthorization,
) -> MasterRouteStepPlan:
    matches = [step for step in plan.steps if step.invocation is selected]
    if len(matches) != 1:
        raise ContractValidationError("wrong_invocation")
    return matches[0]


def _selected_descriptor(
    described: tuple[MasterSpecialistAdapterDescriptor, ...],
    selected: MasterInvocationAuthorization,
    supplied: object,
) -> MasterSpecialistAdapterDescriptor:
    matches = [item for item in described if item.invocation_id == selected.invocation_id]
    if len(matches) != 1:
        raise ContractValidationError("invocation_not_in_plan")
    planned = matches[0]
    if supplied is None:
        return planned
    if not isinstance(supplied, MasterSpecialistAdapterDescriptor):
        raise ContractValidationError("invalid_adapter_descriptor")
    if supplied != planned:
        raise ContractValidationError("descriptor_mismatch")
    return planned


def _closed_bool(value: object, code: str) -> None:
    if type(value) is not bool:
        raise ContractValidationError(code)


@dataclass(frozen=True)
class MasterExecutionHandoffInspection:
    """Immutable inspection of one session handoff.

    ``planning_succeeded`` is true only for a retained ``AUTHORIZED_PLAN``.
    ``execution_authorized`` stays false for that plan and for every policy
    outcome. Structural readiness on the selected scope is not permission.

    ``planned_step_ids`` and ``planned_dependencies`` keep the whole plan.
    ``selected_invocation_count`` is one after a successful plan and zero when
    the decision has no plan. ``multi_invocation_execution_supported`` cannot
    become true, including when the plan contains more than one step.
    """

    session: MasterPlanningSessionRecord
    decision: MasterRouteDecision
    outcome: MasterRouteOutcome
    planning_succeeded: bool
    selected_invocation_count: int
    plan: MasterRoutePlan | None = None
    selected_invocation: MasterInvocationAuthorization | None = None
    planned_descriptors: tuple[MasterSpecialistAdapterDescriptor, ...] = ()
    descriptor: MasterSpecialistAdapterDescriptor | None = None
    execution_scope: MasterExecutionScope | None = None
    gate_inspection: MasterGateInspection | None = None
    preflight: MasterExecutionPreflightEvaluation | None = None
    planned_step_ids: tuple[str, ...] = ()
    planned_dependencies: tuple[tuple[str, str], ...] = ()
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    execution_authorized: bool = False
    executable: bool = False
    dispatch_permitted: bool = False
    provenance_authenticated: bool = False
    factual_response_available: bool = False
    multi_invocation_execution_supported: bool = False
    stops_before_execution_authorization: bool = True
    schema_version: str = MASTER_EXECUTION_INTEGRATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_EXECUTION_INTEGRATION_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if not isinstance(self.session, MasterPlanningSessionRecord):
            raise ContractValidationError("invalid_session")
        if not isinstance(self.decision, MasterRouteDecision):
            raise ContractValidationError("invalid_master_route_decision")
        if not isinstance(self.outcome, MasterRouteOutcome) or self.outcome is not self.decision.outcome:
            raise ContractValidationError("invalid_outcome")
        _closed_bool(self.planning_succeeded, "invalid_outcome")
        _reject_authority_promotion(
            source_authenticity=self.source_authenticity,
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            request_coverage=self.request_coverage,
            execution_authorized=self.execution_authorized,
            executable=self.executable,
            execution_permission_granted=False,
            redeemable=False,
            dispatch_permitted=self.dispatch_permitted,
            authoritative_satisfaction=False,
            provenance_authenticated=self.provenance_authenticated,
            factual_response_available=self.factual_response_available,
            multi_invocation_execution_supported=self.multi_invocation_execution_supported,
        )
        if self.stops_before_execution_authorization is not True:
            raise ContractValidationError("execution_not_granted")
        if self.session.execution_authority is not MasterRouteExecutionAuthority.DENIED:
            raise ContractValidationError("execution_authority_not_granted")
        if self.session.production_authority is not MasterProductionAuthority.DENIED:
            raise ContractValidationError("production_authority_not_issued")
        if self.session.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            raise ContractValidationError("source_authenticity_not_established")
        if self.session.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
            raise ContractValidationError("request_coverage_not_established")
        if self.decision.execution_authority is not MasterRouteExecutionAuthority.DENIED:
            raise ContractValidationError("execution_authority_not_granted")
        if self.decision.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
            raise ContractValidationError("request_coverage_not_established")
        self._check_selection_count()
        if self.planning_succeeded:
            self._check_authorized()
        else:
            self._check_policy_outcome()

    def _check_selection_count(self) -> None:
        if type(self.selected_invocation_count) is not int:
            raise ContractValidationError("invalid_session_state")
        if self.planning_succeeded:
            if self.selected_invocation_count != 1:
                code = (
                    "multiple_invocations_rejected"
                    if self.selected_invocation_count > 1
                    else "invalid_session_state"
                )
                raise ContractValidationError(code)
            return
        if self.selected_invocation_count != 0:
            code = (
                "multiple_invocations_rejected"
                if self.selected_invocation_count > 1
                else "policy_outcome_forbids_plan"
            )
            raise ContractValidationError(code)

    def _check_policy_outcome(self) -> None:
        if self.outcome not in _POLICY_OUTCOMES:
            raise ContractValidationError("policy_outcome_forbids_plan")
        if self.decision.plan is not None or self.plan is not None:
            raise ContractValidationError("policy_outcome_forbids_plan")
        if self.selected_invocation is not None or self.descriptor is not None:
            raise ContractValidationError("policy_outcome_forbids_plan")
        if self.execution_scope is not None or self.gate_inspection is not None:
            raise ContractValidationError("policy_outcome_forbids_plan")
        if self.preflight is not None:
            raise ContractValidationError("policy_outcome_forbids_plan")
        if self.planned_descriptors or self.planned_step_ids or self.planned_dependencies:
            raise ContractValidationError("policy_outcome_forbids_plan")

    def _check_authorized(self) -> None:
        if self.outcome is not MasterRouteOutcome.AUTHORIZED_PLAN:
            raise ContractValidationError("authorized_plan_required")
        plan = self.plan
        if not isinstance(plan, MasterRoutePlan) or self.decision.plan is not plan:
            raise ContractValidationError("authorized_plan_required")
        if plan.execution_authority is not MasterRouteExecutionAuthority.DENIED:
            raise ContractValidationError("execution_authority_not_granted")
        if plan.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
            raise ContractValidationError("request_coverage_not_established")
        if plan.end_to_end_fulfilled is not False:
            raise ContractValidationError("end_to_end_fulfillment_not_granted")
        if not isinstance(self.selected_invocation, MasterInvocationAuthorization):
            raise ContractValidationError("wrong_invocation")
        step = _selected_step(plan, self.selected_invocation)
        if not isinstance(self.planned_descriptors, tuple) or any(
            not isinstance(item, MasterSpecialistAdapterDescriptor) for item in self.planned_descriptors
        ):
            raise ContractValidationError("invalid_adapter_descriptors")
        if tuple(item.step_id for item in self.planned_descriptors) != tuple(
            item.step_id for item in plan.steps
        ):
            raise ContractValidationError("invocation_binding_mismatch")
        if tuple(item.invocation_id for item in self.planned_descriptors) != tuple(
            item.invocation.invocation_id for item in plan.steps
        ):
            raise ContractValidationError("invocation_not_in_plan")
        if not isinstance(self.descriptor, MasterSpecialistAdapterDescriptor):
            raise ContractValidationError("invalid_adapter_descriptor")
        if not any(item is self.descriptor for item in self.planned_descriptors):
            raise ContractValidationError("descriptor_mismatch")
        if (
            self.descriptor.invocation_id != self.selected_invocation.invocation_id
            or self.descriptor.step_id != step.step_id
            or self.descriptor.specialist is not self.selected_invocation.specialist
        ):
            raise ContractValidationError("descriptor_mismatch")
        if not isinstance(self.execution_scope, MasterExecutionScope):
            raise ContractValidationError("invalid_route_step_plan")
        if self.execution_scope.route_plan is not plan:
            raise ContractValidationError("reconstructed_plan_rejected")
        if self.execution_scope.planned_step is not step:
            raise ContractValidationError("foreign_step")
        if self.execution_scope.planned_step.invocation is not self.selected_invocation:
            raise ContractValidationError("copied_invocation_identifier")
        if self.execution_scope.descriptor is not self.descriptor:
            raise ContractValidationError("descriptor_mismatch")
        if self.execution_scope.execution_permission_granted is not False:
            raise ContractValidationError("execution_permission_not_granted")
        if self.execution_scope.executable is not False or self.execution_scope.dispatch_permitted is not False:
            raise ContractValidationError("execution_not_granted")
        if self.execution_scope.redeemable is not False:
            raise ContractValidationError("scope_not_redeemable")
        if self.execution_scope.stops_before_execution_authorization is not True:
            raise ContractValidationError("execution_not_granted")
        self._check_gates(plan)
        self._check_preflight(plan)
        if self.planned_step_ids != tuple(item.step_id for item in plan.steps):
            raise ContractValidationError("invocation_binding_mismatch")
        expected_dependencies = tuple(
            (item.step_id, dependency) for item in plan.steps for dependency in item.depends_on
        )
        if self.planned_dependencies != expected_dependencies:
            raise ContractValidationError("planned_dependencies_not_preserved")

    def _check_gates(self, plan: MasterRoutePlan) -> None:
        inspection = self.gate_inspection
        if not isinstance(inspection, MasterGateInspection):
            raise ContractValidationError("invalid_master_route_plan")
        if inspection.plan is not plan:
            raise ContractValidationError("reconstructed_plan_rejected")
        if inspection.dispatch_permitted is not False:
            raise ContractValidationError("execution_not_granted")
        if inspection.authoritative_satisfaction is not False:
            raise ContractValidationError("authoritative_satisfaction_not_available")
        if inspection.observed_candidates:
            raise ContractValidationError("evidence_bundle_not_retained")
        if inspection.planning_omissions:
            raise ContractValidationError("planning_omission_cannot_change_plan")
        expected_findings = tuple(
            (step.step_id, step.invocation.invocation_id, gate)
            for step in plan.steps
            for gate in step.gates
        )
        actual_findings = tuple(
            (item.step_id, item.invocation_id, item.original_gate) for item in inspection.findings
        )
        if actual_findings != expected_findings:
            raise ContractValidationError("planned_gates_not_preserved")
        expected_edges = tuple(
            (step.step_id, dependency) for step in plan.steps for dependency in step.depends_on
        )
        actual_edges = tuple(
            (item.dependent_step_id, item.dependency_step_id) for item in inspection.dependency_edges
        )
        if actual_edges != expected_edges:
            raise ContractValidationError("planned_dependencies_not_preserved")
        for finding in inspection.findings:
            if finding.original_gate is not MasterRouteGate.DEPENDENCY_BLOCKED:
                continue
            if (
                finding.effective_state is not MasterGateEffectiveState.UNRESOLVED
                or finding.reason
                is not MasterGateResolutionReason.DEPENDENCY_PENDING_WITHOUT_TRUSTED_EXECUTION_EVIDENCE
                or finding.authoritative_satisfaction is not False
                or finding.dependency_terminal is not False
            ):
                raise ContractValidationError("inconsistent_gate_resolution")
        for edge in inspection.dependency_edges:
            if edge.authoritative_terminal is not False or edge.trusted_execution_evidence is not False:
                raise ContractValidationError("untrusted_observation_not_terminal")
            if edge.evidence_sufficient is not False:
                raise ContractValidationError("trusted_gate_evidence_not_available")

    def _check_preflight(self, plan: MasterRoutePlan) -> None:
        preflight = self.preflight
        if not isinstance(preflight, MasterExecutionPreflightEvaluation):
            raise ContractValidationError("invalid_execution_preflight")
        bundle = preflight.bundle
        if not isinstance(bundle, MasterParallelContractBundle):
            raise ContractValidationError("invalid_parallel_contract")
        if bundle.plan is not plan:
            raise ContractValidationError("reconstructed_plan_rejected")
        if bundle.evidence_inputs or bundle.comparison_requests:
            raise ContractValidationError("evidence_bundle_not_retained")
        if preflight.report.workflow is not bundle:
            raise ContractValidationError("master_request_mismatch")
        if preflight.report.state is not MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION:
            raise ContractValidationError("execution_not_granted")
        if preflight.report.executable is not False or preflight.dispatch_permitted is not False:
            raise ContractValidationError("execution_not_granted")
        if preflight.stops_before_execution_authorization is not True:
            raise ContractValidationError("execution_not_granted")
        if preflight.report.missing_provenance != tuple(MasterProvenanceRequirement):
            raise ContractValidationError("provenance_requirement_not_satisfied")
        if self.execution_scope.planning_context is not bundle.context:
            raise ContractValidationError("master_request_mismatch")


def inspect_master_execution_handoff(
    registry: MasterPlanningSessionRegistry,
    record: MasterPlanningSessionRecord,
    *,
    invocation: object = None,
    descriptor: object = None,
    decision: object = None,
    plan: object = None,
    source_authenticity: object = MasterSourceAuthenticity.NOT_ESTABLISHED,
    production_authority: object = MasterProductionAuthority.DENIED,
    execution_authority: object = MasterRouteExecutionAuthority.DENIED,
    request_coverage: object = MasterRequestCoverage.NOT_ESTABLISHED,
    execution_authorized: object = False,
    executable: object = False,
    execution_permission_granted: object = False,
    redeemable: object = False,
    dispatch_permitted: object = False,
    authoritative_satisfaction: object = False,
    provenance_authenticated: object = False,
    factual_response_available: object = False,
    multi_invocation_execution_supported: object = False,
) -> MasterExecutionHandoffInspection:
    """Inspect one retained session. The result does not authorize execution.

    A copied invocation, a foreign or closed session, a reconstructed plan,
    a descriptor that differs from ``describe_planned_adapters``, and any
    authority promotion are rejected. Policy outcomes do not receive a plan.
    """

    if not isinstance(registry, MasterPlanningSessionRegistry):
        raise TypeError("registry is required")
    presented_decision = registry.decision_for(record) if decision is None else decision
    retained_decision = registry.require_retained_decision(record, presented_decision)
    _reject_authority_promotion(
        source_authenticity=source_authenticity,
        production_authority=production_authority,
        execution_authority=execution_authority,
        request_coverage=request_coverage,
        execution_authorized=execution_authorized,
        executable=executable,
        execution_permission_granted=execution_permission_granted,
        redeemable=redeemable,
        dispatch_permitted=dispatch_permitted,
        authoritative_satisfaction=authoritative_satisfaction,
        provenance_authenticated=provenance_authenticated,
        factual_response_available=factual_response_available,
        multi_invocation_execution_supported=multi_invocation_execution_supported,
    )
    if retained_decision.outcome is not MasterRouteOutcome.AUTHORIZED_PLAN:
        if plan is not None:
            raise ContractValidationError("policy_outcome_forbids_plan")
        if invocation is not None or descriptor is not None:
            raise ContractValidationError("invalid_session_state")
        return MasterExecutionHandoffInspection(
            session=record,
            decision=retained_decision,
            outcome=retained_decision.outcome,
            planning_succeeded=False,
            selected_invocation_count=0,
        )

    presented_plan = retained_decision.plan if plan is None else plan
    retained_plan = registry.require_retained_plan(record, presented_plan)
    selected = registry.selected_invocation(record)
    if invocation is not None:
        _require_original_invocation(invocation, selected)
    context = registry.context_for(record)
    bundle = MasterParallelContractBundle(
        context=context,
        plan=retained_plan,
        evidence_inputs=(),
        comparison_requests=(),
    )
    described = describe_planned_adapters(bundle).descriptors
    planned_descriptor = _selected_descriptor(described, selected, descriptor)
    step = _selected_step(retained_plan, selected)
    scope = bind_master_execution_scope(
        context,
        retained_plan,
        step,
        planned_descriptor,
        source_authenticity=source_authenticity,
        production_authority=production_authority,
        execution_authority=execution_authority,
        executable=executable,
        execution_permission_granted=execution_permission_granted,
        redeemable=redeemable,
        dispatch_permitted=dispatch_permitted,
    )
    gate_inspection = inspect_route_gates(
        retained_plan,
        (),
        (),
        (),
        execution_authority=execution_authority,
        production_authority=production_authority,
        source_authenticity=source_authenticity,
        request_coverage=request_coverage,
        dispatch_permitted=dispatch_permitted,
        authoritative_satisfaction=authoritative_satisfaction,
    )
    preflight = evaluate_execution_preflight(
        bundle,
        source_authenticity=source_authenticity,
        production_authority=production_authority,
        execution_authority=execution_authority,
        executable=executable,
    )
    return MasterExecutionHandoffInspection(
        session=record,
        decision=retained_decision,
        outcome=retained_decision.outcome,
        planning_succeeded=True,
        selected_invocation_count=1,
        plan=retained_plan,
        selected_invocation=selected,
        planned_descriptors=described,
        descriptor=planned_descriptor,
        execution_scope=scope,
        gate_inspection=gate_inspection,
        preflight=preflight,
        planned_step_ids=tuple(item.step_id for item in retained_plan.steps),
        planned_dependencies=tuple(
            (item.step_id, dependency)
            for item in retained_plan.steps
            for dependency in item.depends_on
        ),
    )
