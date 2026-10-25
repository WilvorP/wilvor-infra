"""Offline execution-scope tests. A scope is not permission to run a specialist."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace

import pytest

from tests.fixtures.ma4_specialist_evidence_examples import (
    historical_exact_count_run,
    live_ops_all_verified_result,
    live_ops_context,
    live_ops_request,
)
from wilvor_ai.contracts import AgentCapability, ContractValidationError, TemporalScope
from wilvor_ai.historical_answer_renderer import finalize_historical_specialist_run
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import (
    LiveOpsAdmissionSources,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
)
from wilvor_ai.master_evidence_corroboration import MasterEvidenceCorroborationInput
from wilvor_ai.master_execution_scope import (
    MASTER_EXECUTION_SCOPE_SCHEMA_VERSION,
    MasterExecutionDenialCategory,
    MasterExecutionPrerequisite,
    MasterExecutionScope,
    bind_master_execution_scope,
    bind_planned_execution_scopes,
)
from wilvor_ai.master_parallel_contracts import MasterParallelContractBundle
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.master_specialist_adapters import describe_planned_adapters


TEXT = "Which aircraft are currently affected by hazards, and what recorded history applies?"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
_CLOSED_NAMES = (
    "dispatch",
    "redeem",
    "execute",
    "grant",
    "authorize",
    "call_specialist",
)
_SECURITY_PREREQUISITES = (
    MasterExecutionPrerequisite.TRUSTED_PLANNING_SESSION_ORIGIN,
    MasterExecutionPrerequisite.AUTHENTICATED_APPLICATION_PRINCIPAL,
    MasterExecutionPrerequisite.VERIFIED_EXECUTION_POLICY,
    MasterExecutionPrerequisite.AUTHORIZED_READ_RESOURCE_SCOPE,
    MasterExecutionPrerequisite.ESTABLISHED_EXECUTION_DEADLINE,
)


def _context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-scope-1",
        "correlation_id": "corr-scope-1",
        "application_request_time_utc": "2026-01-15T00:00:01Z",
        "canonical_user_text": TEXT,
    }
    values.update(overrides)
    return MasterPlanningContext(**values)  # type: ignore[arg-type]


def _proposal(*steps: MasterProposedStep) -> MasterRouteProposal:
    capabilities = tuple(
        dict.fromkeys(
            MasterProposedCapability(step.capability, MasterRequirement.REQUIRED) for step in steps
        )
    )
    return MasterRouteProposal(user_text=TEXT, capabilities=capabilities, steps=steps)


def _live(step_id: str = "live-1", **overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "step_id": step_id,
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "capability": LIVE,
        "temporal_intent": MasterRouteTemporalIntent.CURRENT,
    }
    values.update(overrides)
    return MasterProposedStep(**values)  # type: ignore[arg-type]


def _history(step_id: str = "hist-1") -> MasterProposedStep:
    return MasterProposedStep(
        step_id=step_id,
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=HISTORY,
        temporal_intent=MasterRouteTemporalIntent.HISTORICAL,
        historical_start_utc="2026-01-01T00:00:00Z",
        historical_end_utc="2026-01-02T00:00:00Z",
    )


def _decision(step_id: str = "dec-1") -> MasterProposedStep:
    return MasterProposedStep(
        step_id=step_id,
        specialist=MasterSpecialist.DECISION,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=RISK,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
    )


def _authorized(*steps: MasterProposedStep, **context_overrides: object):
    context = _context(**context_overrides)
    decision = authorize_route(_proposal(*steps), context)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    assert decision.plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    return context, decision.plan


def _bundle(*steps: MasterProposedStep, evidence: bool = False, **context_overrides: object):
    context, plan = _authorized(*steps, **context_overrides)
    inputs: tuple[MasterEvidenceCorroborationInput, ...] = ()
    if evidence:
        built = []
        for step in plan.steps:
            if step.invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
                run = historical_exact_count_run()
                built.append(
                    MasterEvidenceCorroborationInput(
                        MasterEvidenceAdmissionInput(
                            step.invocation,
                            finalize_historical_specialist_run(run),
                        ),
                        historical_run=run,
                    )
                )
            elif step.invocation.specialist is MasterSpecialist.LIVE_OPS:
                built.append(
                    MasterEvidenceCorroborationInput(
                        MasterEvidenceAdmissionInput(
                            step.invocation,
                            LiveOpsAdmissionSources(
                                live_ops_request(),
                                live_ops_context(),
                                live_ops_all_verified_result(),
                            ),
                        )
                    )
                )
        inputs = tuple(built)
    return MasterParallelContractBundle(context=context, plan=plan, evidence_inputs=inputs)


def _descriptor(bundle: MasterParallelContractBundle, step_id: str):
    return next(
        item
        for item in describe_planned_adapters(bundle).descriptors
        if item.step_id == step_id
    )


def _step(bundle: MasterParallelContractBundle, step_id: str):
    return next(item for item in bundle.plan.steps if item.step_id == step_id)


def _scope(bundle: MasterParallelContractBundle, step_id: str) -> MasterExecutionScope:
    return bind_master_execution_scope(
        bundle.context,
        bundle.plan,
        _step(bundle, step_id),
        _descriptor(bundle, step_id),
    )


def _assert_unauthorized(scope: MasterExecutionScope) -> None:
    assert scope.schema_version == MASTER_EXECUTION_SCOPE_SCHEMA_VERSION
    assert scope.execution_permission_granted is False
    assert scope.executable is False
    assert scope.redeemable is False
    assert scope.dispatch_permitted is False
    assert scope.stops_before_execution_authorization is True
    assert scope.end_to_end_fulfilled is False
    assert scope.target_authorization_granted is False
    assert scope.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert scope.production_authority is MasterProductionAuthority.DENIED
    assert scope.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert scope.missing_prerequisites == tuple(MasterExecutionPrerequisite)
    assert scope.denial_categories == tuple(MasterExecutionDenialCategory)
    assert (
        MasterExecutionDenialCategory.STRUCTURAL_READINESS_NOT_PERMISSION
        in scope.denial_categories
    )
    assert MasterExecutionDenialCategory.MATCHING_IDENTIFIER_NOT_TRUST in scope.denial_categories
    assert MasterExecutionPrerequisite.REVIEWED_EXECUTION_AUTHORITY in scope.missing_prerequisites
    for prerequisite in _SECURITY_PREREQUISITES:
        assert prerequisite in scope.missing_prerequisites
    assert callable(scope) is False
    assert all(not hasattr(scope, name) for name in _CLOSED_NAMES)
    assert all(not hasattr(MasterExecutionScope, name) for name in _CLOSED_NAMES)
    names = {item.name for item in fields(scope)}
    assert "bundle" not in names
    assert "evidence_inputs" not in names
    assert "comparison_requests" not in names
    assert "dispatch" not in names
    assert "application_principal" not in names
    assert "execution_policy" not in names
    assert "read_resource_scope" not in names
    assert "execution_deadline" not in names
    assert "planning_session" not in names


def test_ready_live_ops_and_blocked_decision_stay_unauthorized():
    bundle = _bundle(_live(), _decision())
    live_step = _step(bundle, "live-1")
    decision_step = _step(bundle, "dec-1")
    live_descriptor = _descriptor(bundle, "live-1")
    live = bind_master_execution_scope(
        bundle.context,
        bundle.plan,
        live_step,
        live_descriptor,
    )
    decision = _scope(bundle, "dec-1")

    assert live_step.ready_to_execute is True
    assert live_step.gates == (MasterRouteGate.NOT_REQUIRED,)
    assert live.structural_ready_to_execute is True
    assert live.specialist is MasterSpecialist.LIVE_OPS
    assert live.capability is LIVE
    assert live.temporal_scopes == (TemporalScope.CURRENT,)
    assert live.historical_start_utc is None
    assert live.historical_end_utc is None
    assert live.depends_on == ()
    assert live.master_request_id == bundle.context.master_request_id
    assert live.correlation_id == bundle.context.correlation_id
    assert live.application_request_time_utc == bundle.context.application_request_time_utc
    assert live.step_id == live_step.step_id
    assert live.invocation_id == live_step.invocation.invocation_id
    assert live.specialist_request_id == live_step.invocation.specialist_request_id
    assert live.purpose is live_step.invocation.purpose
    assert live.requirement is live_step.invocation.requirement
    assert live.planning_context is bundle.context
    assert live.route_plan is bundle.plan
    assert live.planned_step is live_step
    assert live.descriptor is live_descriptor

    assert decision_step.ready_to_execute is False
    assert MasterRouteGate.TRUSTED_IDENTITY_VALIDATION_PENDING in decision_step.gates
    assert MasterRouteGate.CURRENTNESS_VALIDATION_PENDING in decision_step.gates
    assert decision.structural_ready_to_execute is False
    assert decision.specialist is MasterSpecialist.DECISION
    assert decision.capability is RISK
    assert decision.gates == decision_step.gates
    assert decision.temporal_scopes == decision_step.temporal_scopes
    assert decision_step.target_authorization_granted is False
    assert decision.target_authorization_granted is False

    assert live.denial_categories == decision.denial_categories
    assert live.missing_prerequisites == decision.missing_prerequisites
    assert live.execution_permission_granted is False
    assert decision.execution_permission_granted is False
    _assert_unauthorized(live)
    _assert_unauthorized(decision)


def test_same_specialist_can_have_distinct_invocations():
    bundle = _bundle(_live("live-east"), _live("live-west"))
    scopes = bind_planned_execution_scopes(
        bundle.context,
        bundle.plan,
        describe_planned_adapters(bundle).descriptors,
    )
    assert [item.step_id for item in scopes] == [step.step_id for step in bundle.plan.steps]
    assert len(scopes) == 2
    assert {item.specialist for item in scopes} == {MasterSpecialist.LIVE_OPS}
    assert scopes[0].invocation_id != scopes[1].invocation_id
    assert scopes[0].specialist_request_id != scopes[1].specialist_request_id
    assert scopes[0].step_id != scopes[1].step_id
    assert all(item.execution_permission_granted is False for item in scopes)
    assert all(item.structural_ready_to_execute is True for item in scopes)
    reversed_descriptors = tuple(reversed(describe_planned_adapters(bundle).descriptors))
    assert (
        bind_planned_execution_scopes(bundle.context, bundle.plan, reversed_descriptors) == scopes
    )


def test_historical_window_gates_and_dependencies_are_copied():
    bundle = _bundle(_history(), _live("live-2", depends_on=("hist-1",)))
    historical = _scope(bundle, "hist-1")
    dependent = _scope(bundle, "live-2")
    historical_step = _step(bundle, "hist-1")
    dependent_step = _step(bundle, "live-2")

    assert historical.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    assert historical.temporal_scopes == (TemporalScope.HISTORICAL,)
    assert historical.historical_start_utc == "2026-01-01T00:00:00Z"
    assert historical.historical_end_utc == "2026-01-02T00:00:00Z"
    assert historical.historical_start_utc == historical_step.historical_start_utc
    assert historical.depends_on == ()
    assert dependent.depends_on == ("hist-1",)
    assert dependent.depends_on == dependent_step.depends_on
    assert MasterRouteGate.DEPENDENCY_BLOCKED in dependent.gates
    assert dependent.gates == dependent_step.gates
    assert dependent.structural_ready_to_execute is False
    assert dependent.needs_persisted_evidence is dependent_step.needs_persisted_evidence
    _assert_unauthorized(historical)
    _assert_unauthorized(dependent)


def test_scope_does_not_retain_an_evidence_bearing_bundle():
    bundle = _bundle(_live(), _history(), evidence=True)
    assert bundle.evidence_inputs
    scope = _scope(bundle, "live-1")
    values = [getattr(scope, item.name) for item in fields(scope)]
    assert all(value is not bundle for value in values)
    assert all(value is not bundle.evidence_inputs for value in values)
    assert all(not isinstance(value, MasterParallelContractBundle) for value in values)
    assert not hasattr(scope, "evidence_inputs")
    bare = _bundle(_live(), _history(), evidence=False)
    bare_scope = _scope(bare, "live-1")
    assert bare_scope.execution_permission_granted is scope.execution_permission_granted
    assert bare_scope.denial_categories == scope.denial_categories
    _assert_unauthorized(scope)


def test_field_equal_step_is_foreign_and_is_not_trust():
    bundle = _bundle(_live())
    step = _step(bundle, "live-1")
    descriptor = _descriptor(bundle, "live-1")
    clone = replace(step)
    assert clone == step
    assert clone is not step
    with pytest.raises(ContractValidationError, match="foreign_step"):
        bind_master_execution_scope(bundle.context, bundle.plan, clone, descriptor)
    cloned_descriptor = replace(descriptor)
    scope = bind_master_execution_scope(
        bundle.context,
        bundle.plan,
        step,
        cloned_descriptor,
    )
    assert scope.descriptor is cloned_descriptor
    assert MasterExecutionDenialCategory.MATCHING_IDENTIFIER_NOT_TRUST in scope.denial_categories
    _assert_unauthorized(scope)


def test_foreign_plan_step_and_forged_identity_rejected():
    bundle = _bundle(_live())
    other = _bundle(
        _live(),
        master_request_id="other-master-request",
        correlation_id="corr-other-scope",
    )
    descriptor = _descriptor(bundle, "live-1")
    with pytest.raises(ContractValidationError, match="foreign_step"):
        bind_master_execution_scope(
            bundle.context,
            bundle.plan,
            _step(other, "live-1"),
            descriptor,
        )
    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        bind_master_execution_scope(
            other.context,
            bundle.plan,
            _step(bundle, "live-1"),
            descriptor,
        )
    forged = replace(descriptor, invocation_id="inv-not-on-the-plan")
    with pytest.raises(ContractValidationError, match="descriptor_mismatch"):
        bind_master_execution_scope(
            bundle.context,
            bundle.plan,
            _step(bundle, "live-1"),
            forged,
        )
    renamed = replace(descriptor, master_request_id="other-master-request")
    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        bind_master_execution_scope(
            bundle.context,
            bundle.plan,
            _step(bundle, "live-1"),
            renamed,
        )
    swapped = replace(descriptor, specialist=MasterSpecialist.HISTORICAL_ANALYTICS)
    with pytest.raises(ContractValidationError, match="specialist_mismatch"):
        bind_master_execution_scope(
            bundle.context,
            bundle.plan,
            _step(bundle, "live-1"),
            swapped,
        )


def test_descriptor_for_a_different_step_is_rejected():
    bundle = _bundle(_live("live-east"), _live("live-west"))
    with pytest.raises(ContractValidationError, match="descriptor_mismatch"):
        bind_master_execution_scope(
            bundle.context,
            bundle.plan,
            _step(bundle, "live-east"),
            _descriptor(bundle, "live-west"),
        )
    with pytest.raises(ContractValidationError, match="duplicate_invocation_id"):
        bind_planned_execution_scopes(
            bundle.context,
            bundle.plan,
            (_descriptor(bundle, "live-east"), _descriptor(bundle, "live-east")),
        )
    with pytest.raises(ContractValidationError, match="invocation_not_in_plan"):
        bind_planned_execution_scopes(
            bundle.context,
            bundle.plan,
            (_descriptor(bundle, "live-east"),),
        )


def test_authority_promotion_rejected():
    bundle = _bundle(_live())
    context = bundle.context
    plan = bundle.plan
    step = _step(bundle, "live-1")
    descriptor = _descriptor(bundle, "live-1")
    cases = (
        {"production_authority": MasterProductionAuthority.PRODUCTION},
        {"execution_authority": "GRANTED"},
        {"source_authenticity": "AUTHENTICATED"},
        {"executable": True},
        {"execution_permission_granted": True},
        {"redeemable": True},
        {"dispatch_permitted": True},
    )
    codes = (
        "production_authority_not_issued",
        "execution_authority_not_granted",
        "source_authenticity_not_established",
        "execution_not_granted",
        "execution_permission_not_granted",
        "scope_not_redeemable",
        "execution_not_granted",
    )
    for kwargs, code in zip(cases, codes, strict=True):
        with pytest.raises(ContractValidationError, match=code):
            bind_master_execution_scope(context, plan, step, descriptor, **kwargs)
    scope = _scope(bundle, "live-1")
    with pytest.raises(ContractValidationError, match="execution_permission_not_granted"):
        replace(scope, execution_permission_granted=True)
    with pytest.raises(ContractValidationError, match="scope_not_redeemable"):
        replace(scope, redeemable=True)
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        replace(scope, production_authority=MasterProductionAuthority.PRODUCTION)
    with pytest.raises(ContractValidationError, match="execution_authority_not_granted"):
        replace(scope, execution_authority="GRANTED")  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="source_authenticity_not_established"):
        replace(scope, source_authenticity="AUTHENTICATED")  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(scope, executable=True)
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(scope, stops_before_execution_authorization=False)
    with pytest.raises(ContractValidationError, match="target_authorization_not_granted"):
        replace(scope, target_authorization_granted=True)
    with pytest.raises(ContractValidationError, match="prerequisite_not_satisfied"):
        replace(scope, missing_prerequisites=())
    for omitted in _SECURITY_PREREQUISITES:
        kept = tuple(item for item in MasterExecutionPrerequisite if item is not omitted)
        with pytest.raises(ContractValidationError, match="prerequisite_not_satisfied"):
            replace(scope, missing_prerequisites=kept)
    with pytest.raises(ContractValidationError, match="execution_denial_not_cleared"):
        replace(
            scope,
            denial_categories=(MasterExecutionDenialCategory.EXECUTION_AUTHORITY_DENIED,),
        )
    with pytest.raises(ContractValidationError, match="request_coverage_not_established"):
        replace(scope, request_coverage="ESTABLISHED")  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="end_to_end_fulfillment_not_granted"):
        replace(scope, end_to_end_fulfilled=True)
    with pytest.raises(FrozenInstanceError):
        scope.execution_permission_granted = True  # type: ignore[misc]
    _assert_unauthorized(scope)


def test_inconsistent_replace_is_rejected():
    bundle = _bundle(_live(), _history())
    scope = _scope(bundle, "live-1")
    other = _scope(bundle, "hist-1")
    cases = (
        ("master_request_id", "other-master-request", "master_request_mismatch"),
        ("correlation_id", "corr-replaced-1", "correlation_mismatch"),
        ("application_request_time_utc", "2026-06-01T00:00:00Z", "application_time_mismatch"),
        ("canonical_user_text", "Which other aircraft are affected now?", "master_request_mismatch"),
        ("invocation_id", other.invocation_id, "invocation_binding_mismatch"),
        ("step_id", other.step_id, "invocation_binding_mismatch"),
        ("specialist", MasterSpecialist.HISTORICAL_ANALYTICS, "specialist_mismatch"),
        ("purpose", MasterPurpose.DISCOVERY, "invocation_binding_mismatch"),
        ("requirement", MasterRequirement.OPTIONAL, "invocation_binding_mismatch"),
        ("specialist_request_id", other.specialist_request_id, "specialist_request_id_mismatch"),
        ("capability", HISTORY, "capability_step_mismatch"),
        ("temporal_scopes", (TemporalScope.HISTORICAL,), "invalid_temporal_scopes"),
        ("historical_start_utc", "2026-01-01T00:00:00Z", "invocation_binding_mismatch"),
        ("historical_end_utc", "2026-01-02T00:00:00Z", "invocation_binding_mismatch"),
        ("gates", (MasterRouteGate.DEPENDENCY_BLOCKED,), "invocation_binding_mismatch"),
        ("depends_on", ("hist-1",), "invocation_binding_mismatch"),
        ("structural_ready_to_execute", False, "invocation_binding_mismatch"),
        ("needs_persisted_evidence", True, "invocation_binding_mismatch"),
        ("planned_step", other.planned_step, "descriptor_mismatch"),
        ("descriptor", other.descriptor, "descriptor_mismatch"),
        (
            "planning_context",
            replace(scope.planning_context, correlation_id="corr-replaced-1"),
            "correlation_mismatch",
        ),
    )
    for field_name, value, code in cases:
        with pytest.raises(ContractValidationError, match=code):
            replace(scope, **{field_name: value})
    foreign = _bundle(
        _live(),
        master_request_id="other-master-request",
        correlation_id="corr-other-scope",
    )
    with pytest.raises(ContractValidationError, match="foreign_step"):
        replace(scope, planned_step=foreign.plan.steps[0])
    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        replace(
            scope,
            planning_context=foreign.context,
            route_plan=foreign.plan,
            planned_step=foreign.plan.steps[0],
            descriptor=_descriptor(foreign, "live-1"),
        )
    _assert_unauthorized(scope)


def test_malformed_objects_and_model_proposal_rejected():
    bundle = _bundle(_live())
    step = _step(bundle, "live-1")
    descriptor = _descriptor(bundle, "live-1")
    proposal = _proposal(_live())
    with pytest.raises(ContractValidationError, match="invalid_planning_context"):
        bind_master_execution_scope(bundle, bundle.plan, step, descriptor)  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="invalid_route_plan"):
        bind_master_execution_scope(bundle.context, proposal, step, descriptor)  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="invalid_route_step_plan"):
        bind_master_execution_scope(bundle.context, bundle.plan, object(), descriptor)  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="invalid_adapter_descriptor"):
        bind_master_execution_scope(bundle.context, bundle.plan, step, object())  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="invalid_schema_version"):
        replace(scope_version := _scope(bundle, "live-1"), schema_version="wilvor.ai.master_execution_scope.v2")
    assert scope_version.schema_version == "wilvor.ai.master_execution_scope.v1"


def test_security_prerequisites_remain_missing_after_replace():
    bundle = _bundle(_live(), _decision())
    live = _scope(bundle, "live-1")
    decision = _scope(bundle, "dec-1")
    replaced_live = replace(live)
    replaced_decision = replace(decision)
    assert live.structural_ready_to_execute is True
    assert decision.structural_ready_to_execute is False
    for scope in (live, decision, replaced_live, replaced_decision):
        assert scope.missing_prerequisites == tuple(MasterExecutionPrerequisite)
        for prerequisite in _SECURITY_PREREQUISITES:
            assert prerequisite in scope.missing_prerequisites
        assert scope.execution_permission_granted is False
        assert scope.execution_authority is MasterRouteExecutionAuthority.DENIED
        assert scope.production_authority is MasterProductionAuthority.DENIED
        assert scope.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert replaced_live == live
    assert replaced_decision == decision


def test_repeated_scopes_are_deterministic():
    bundle = _bundle(_live(), _decision())
    first = _scope(bundle, "live-1")
    second = _scope(bundle, "live-1")
    assert first == second
    assert first.execution_permission_granted is False
    assert second.structural_ready_to_execute is True
    blocked = _scope(bundle, "dec-1")
    assert blocked == _scope(bundle, "dec-1")
    assert blocked.structural_ready_to_execute is False
    assert blocked.execution_permission_granted is False
