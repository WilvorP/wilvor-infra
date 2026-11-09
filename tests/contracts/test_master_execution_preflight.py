"""Offline MA6 execution-preflight tests. Nothing here dispatches a specialist."""

from __future__ import annotations

import pytest

from tests.fixtures.ma4_specialist_evidence_examples import (
    historical_exact_count_run,
    live_ops_all_verified_result,
    live_ops_context,
    live_ops_request,
)
from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.historical_answer_renderer import finalize_historical_specialist_run
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
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
from wilvor_ai.master_execution_preflight import (
    MasterExecutionPreflightEvaluation,
    bind_preflight_report,
    evaluate_execution_preflight,
)
from wilvor_ai.master_parallel_contracts import (
    MasterExecutionPreflightReport,
    MasterExecutionPreflightState,
    MasterParallelContractBundle,
    MasterProvenanceRequirement,
)
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRouteExecutionAuthority,
    MasterRouteOutcome,
    MasterRoutePlan,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)


TEXT = "Which aircraft are currently affected by hazards, and what recorded history applies?"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS


def _context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-ma6-1",
        "correlation_id": "corr-ma6-1",
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


def _live(step_id: str = "live-1") -> MasterProposedStep:
    return MasterProposedStep(
        step_id=step_id,
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=LIVE,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
    )


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


def _authorized(*steps: MasterProposedStep):
    context = _context()
    decision = authorize_route(_proposal(*steps), context)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED
    return context, decision.plan


def _historical_input(authorization: MasterInvocationAuthorization) -> MasterEvidenceCorroborationInput:
    run = historical_exact_count_run()
    return MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(authorization, finalize_historical_specialist_run(run)),
        historical_run=run,
    )


def _live_input(authorization: MasterInvocationAuthorization) -> MasterEvidenceCorroborationInput:
    return MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(
            authorization,
            LiveOpsAdmissionSources(
                live_ops_request(),
                live_ops_context(),
                live_ops_all_verified_result(),
            ),
        )
    )


def _bundle(*steps: MasterProposedStep, with_evidence: bool) -> MasterParallelContractBundle:
    context, plan = _authorized(*steps)
    evidence: tuple[MasterEvidenceCorroborationInput, ...] = ()
    if with_evidence:
        built = []
        for step in plan.steps:
            if step.invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
                built.append(_historical_input(step.invocation))
            else:
                built.append(_live_input(step.invocation))
        evidence = tuple(built)
    return MasterParallelContractBundle(context=context, plan=plan, evidence_inputs=evidence)


def _empty_bundle() -> MasterParallelContractBundle:
    context = _context()
    plan = MasterRoutePlan(
        master_request_id=context.master_request_id,
        canonical_user_text=context.canonical_user_text,
        steps=(),
    )
    return MasterParallelContractBundle(context=context, plan=plan, evidence_inputs=())


def _assert_closed(evaluation: MasterExecutionPreflightEvaluation) -> None:
    assert evaluation.dispatch_permitted is False
    assert evaluation.stops_before_execution_authorization is True
    assert evaluation.report.executable is False
    assert evaluation.report.missing_provenance == tuple(MasterProvenanceRequirement)
    assert evaluation.report.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert evaluation.report.production_authority is MasterProductionAuthority.DENIED
    assert evaluation.report.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert callable(evaluation) is False
    assert "dispatch" not in MasterExecutionPreflightEvaluation.__dataclass_fields__


def test_empty_plan_is_not_executable():
    evaluation = evaluate_execution_preflight(_empty_bundle())
    assert evaluation.report.state is MasterExecutionPreflightState.NOT_EXECUTABLE
    assert evaluation.bundle.plan.steps == ()
    _assert_closed(evaluation)


def test_populated_plan_remains_blocked_by_authorization():
    bundle = _bundle(_live(), _history(), with_evidence=True)
    assert bundle.evidence_inputs
    evaluation = evaluate_execution_preflight(bundle)
    assert evaluation.report.state is MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION
    assert evaluation.report.state is not MasterExecutionPreflightState.CONTRACT_READY_FOR_FUTURE_INTEGRATION
    assert bundle.plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    _assert_closed(evaluation)


def test_every_provenance_requirement_remains_missing():
    evaluation = evaluate_execution_preflight(_bundle(_live(), with_evidence=True))
    assert evaluation.report.missing_provenance == tuple(MasterProvenanceRequirement)
    assert MasterProvenanceRequirement.TRUSTED_SOURCE_ADAPTER in evaluation.report.missing_provenance
    assert len(evaluation.report.missing_provenance) == len(MasterProvenanceRequirement)
    with pytest.raises(ContractValidationError, match="provenance_requirement_not_satisfied"):
        MasterExecutionPreflightReport(
            workflow=evaluation.bundle,
            state=MasterExecutionPreflightState.BLOCKED_BY_MISSING_PROVENANCE,
            missing_provenance=(),
        )


def test_fake_authenticated_source_status_rejected():
    bundle = _bundle(_live(), with_evidence=False)
    with pytest.raises(ContractValidationError, match="source_authenticity_not_established"):
        evaluate_execution_preflight(bundle, source_authenticity="AUTHENTICATED")
    with pytest.raises(ContractValidationError, match="source_authenticity_not_established"):
        MasterExecutionPreflightReport(
            workflow=bundle,
            state=MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION,
            missing_provenance=tuple(MasterProvenanceRequirement),
            source_authenticity="AUTHENTICATED",  # type: ignore[arg-type]
        )


def test_fake_execution_approval_rejected():
    bundle = _bundle(_live(), with_evidence=False)
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        evaluate_execution_preflight(bundle, production_authority=MasterProductionAuthority.PRODUCTION)
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        evaluate_execution_preflight(bundle, executable=True)
    with pytest.raises(ContractValidationError, match="execution_authority_not_granted"):
        evaluate_execution_preflight(bundle, execution_authority="GRANTED")
    report = evaluate_execution_preflight(bundle).report
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        bind_preflight_report(bundle, report, dispatch_permitted=True)
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        MasterExecutionPreflightEvaluation(
            bundle=bundle,
            report=report,
            stops_before_execution_authorization=False,
        )
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        MasterExecutionPreflightReport(
            workflow=bundle,
            state=MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION,
            missing_provenance=tuple(MasterProvenanceRequirement),
            executable=True,
        )


def test_contract_ready_for_future_integration_does_not_dispatch():
    bundle = _bundle(_live(), _history(), with_evidence=True)
    evaluated = evaluate_execution_preflight(bundle)
    assert evaluated.report.state is MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION
    ready = MasterExecutionPreflightReport(
        workflow=bundle,
        state=MasterExecutionPreflightState.CONTRACT_READY_FOR_FUTURE_INTEGRATION,
        missing_provenance=tuple(MasterProvenanceRequirement),
    )
    held = bind_preflight_report(bundle, ready)
    assert held.report.state is MasterExecutionPreflightState.CONTRACT_READY_FOR_FUTURE_INTEGRATION
    assert held.report.executable is False
    assert held.dispatch_permitted is False
    assert held.stops_before_execution_authorization is True
    assert callable(held) is False
    assert not hasattr(held, "dispatch")
    assert not hasattr(MasterExecutionPreflightEvaluation, "dispatch")
    _assert_closed(held)


def test_repeated_preflight_evaluations_are_deterministic():
    bundle = _bundle(_live(), _history(), with_evidence=True)
    first = evaluate_execution_preflight(bundle)
    second = evaluate_execution_preflight(bundle)
    assert first == second
    assert first.report == second.report
    assert first.report.state is MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION
    ready = MasterExecutionPreflightReport(
        workflow=bundle,
        state=MasterExecutionPreflightState.CONTRACT_READY_FOR_FUTURE_INTEGRATION,
        missing_provenance=tuple(MasterProvenanceRequirement),
    )
    assert bind_preflight_report(bundle, ready) == bind_preflight_report(bundle, ready)
