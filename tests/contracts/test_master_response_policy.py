"""Offline response-policy tests. A passing policy does not authenticate evidence."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tests.fixtures.live_ops_public_examples import ledger_not_authorized, unsupported_request
from tests.fixtures.ma4_specialist_evidence_examples import (
    decision_offline_consistent_run,
    historical_exact_count_run,
    historical_forged_count_run,
    historical_truncated_lower_bound_run,
    historical_unknown_run,
    live_ops_all_verified_result,
    live_ops_context,
    live_ops_request,
)
from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.decision_model_contracts import DecisionUnsupportedReason
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionSpecialistRunResult,
    DecisionSpecialistRunStatus,
    DecisionTargetMode,
)
from wilvor_ai.historical_answer_renderer import finalize_historical_specialist_run
from wilvor_ai.master_contracts import MasterPurpose, MasterRequirement, MasterSpecialist
from wilvor_ai.master_evidence_admission import LiveOpsAdmissionSources, MasterEvidenceAdmissionInput
from wilvor_ai.master_evidence_comparability import (
    MasterComparisonCategory,
    MasterComparisonEligibility,
    MasterComparisonRequest,
    assess_master_evidence_comparability,
)
from wilvor_ai.master_evidence_corroboration import (
    MasterCorroborationStatus,
    MasterEvidenceCorroborationInput,
    corroborate_master_evidence,
)
from wilvor_ai.master_parallel_contracts import (
    MasterParallelContractBundle,
    MasterResponseLimitation,
    MasterResponsePolicyState,
)
from wilvor_ai.master_response_policy import evaluate_master_response_policy
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRouteOutcome,
    MasterRoutePlan,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.specialist_contracts import SpecialistResult, SpecialistStatus
from wilvor_ai.specialist_runtime_contracts import HistoricalSpecialistRunResult


TEXT = "Which aircraft are currently affected by hazards, and what recorded history applies?"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE = REPO_ROOT / "functions" / "shared" / "wilvor_ai" / "master_response_policy.py"
FORBIDDEN = {
    "anthropic",
    "boto3",
    "botocore",
    "langgraph",
    "openai",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.master_orchestration",
    "wilvor_operational",
}


def _context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-ma5-1",
        "correlation_id": "corr-ma5-1",
        "application_request_time_utc": "2026-01-15T00:00:01Z",
        "canonical_user_text": TEXT,
    }
    values.update(overrides)
    return MasterPlanningContext(**values)  # type: ignore[arg-type]


def _step(step_id: str, specialist: MasterSpecialist, capability: AgentCapability, intent: MasterRouteTemporalIntent):
    return MasterProposedStep(
        step_id=step_id,
        specialist=specialist,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=capability,
        temporal_intent=intent,
        historical_start_utc="2026-01-01T00:00:00Z" if specialist is MasterSpecialist.HISTORICAL_ANALYTICS else None,
        historical_end_utc="2026-01-02T00:00:00Z" if specialist is MasterSpecialist.HISTORICAL_ANALYTICS else None,
    )


def _plan(*steps: MasterProposedStep, **context_overrides: object):
    context = _context(**context_overrides)
    capabilities = tuple(
        dict.fromkeys(MasterProposedCapability(step.capability, MasterRequirement.REQUIRED) for step in steps)
    )
    decision = authorize_route(
        MasterRouteProposal(user_text=TEXT, capabilities=capabilities, steps=steps),
        context,
    )
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    return context, decision.plan


def _bundle(context, plan, evidence, comparisons=()):
    return MasterParallelContractBundle(
        context=context,
        plan=plan,
        evidence_inputs=tuple(evidence),
        comparison_requests=tuple(comparisons),
    )


def _historical(authorization, run, *, with_run: bool = True, artifact: SpecialistResult | None = None):
    final = finalize_historical_specialist_run(run) if artifact is None else artifact
    return MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(authorization, final),
        historical_run=run if with_run else None,
    )


def _live(authorization, result):
    request = replace(live_ops_request(), request_id=authorization.specialist_request_id)
    context = replace(live_ops_context(), correlation_id=authorization.correlation_id)
    aligned = replace(
        result,
        request_id=authorization.specialist_request_id,
        correlation_id=authorization.correlation_id,
    )
    return MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(authorization, LiveOpsAdmissionSources(request, context, aligned))
    )


def _decision_unsupported() -> DecisionSpecialistRunResult:
    return DecisionSpecialistRunResult(
        status=DecisionSpecialistRunStatus.UNSUPPORTED,
        mode=DecisionTargetMode.AIRCRAFT,
        aircraft_id="ac-1",
        recommendation_id=None,
        tool_results=(),
        evidence_snapshots=(),
        evidence_bindings=(),
        invocations=(),
        proposed_claims=(),
        verification=None,
        render=None,
        unsupported_reason=DecisionUnsupportedReason.INSUFFICIENT_EVIDENCE,
        runtime_errors=(),
        collection_partial_reason=None,
        validation_feedback=(),
        provider_turn_count=0,
        executed_tool_call_count=0,
        executed_persisted_call_count=0,
        terminal_kind=None,
    )


def _suppresses(handoff) -> None:
    assert handoff.claims_admitted is False
    assert handoff.production_authority.value == "DENIED"
    assert handoff.source_authenticity.value == "NOT_ESTABLISHED"
    assert handoff.execution_authority.value == "DENIED"
    assert handoff.end_to_end_fulfilled is False
    if handoff.state is not MasterResponsePolicyState.UNAVAILABLE:
        assert "zero" not in handoff.status_text.lower()
    assert "answer" not in type(handoff).__dataclass_fields__


def test_no_evidence_is_not_zero_or_unavailable():
    context = _context()
    plan = MasterRoutePlan(
        master_request_id=context.master_request_id,
        canonical_user_text=context.canonical_user_text,
        steps=(),
    )
    handoff = evaluate_master_response_policy(_bundle(context, plan, ()))
    assert handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    _suppresses(handoff)


def test_historical_count_stays_unauthenticated():
    context, plan = _plan(_step("hist-1", MasterSpecialist.HISTORICAL_ANALYTICS, HISTORY, MasterRouteTemporalIntent.HISTORICAL))
    evidence = _historical(plan.steps[0].invocation, historical_exact_count_run())
    checked = corroborate_master_evidence((evidence,)).results[0]
    handoff = evaluate_master_response_policy(_bundle(context, plan, (evidence,)))
    assert checked.corroboration_status is MasterCorroborationStatus.CONSISTENT_WITH_SUPPLIED_EVIDENCE
    assert checked.admission_status.value == "BINDING_UNRESOLVED"
    assert handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert MasterResponseLimitation.SUPPLIED_EVIDENCE_NOT_AUTHENTICATED in handoff.limitations
    _suppresses(handoff)


def test_verified_historical_unavailability_does_not_infer_zero():
    context, plan = _plan(_step("hist-1", MasterSpecialist.HISTORICAL_ANALYTICS, HISTORY, MasterRouteTemporalIntent.HISTORICAL))
    run = historical_unknown_run()
    evidence = _historical(plan.steps[0].invocation, run)
    assert evidence.admission_input.artifact.status is SpecialistStatus.UNAVAILABLE
    handoff = evaluate_master_response_policy(_bundle(context, plan, (evidence,)))
    assert handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert handoff.state is not MasterResponsePolicyState.UNAVAILABLE
    _suppresses(handoff)


def test_forged_historical_answer_cannot_become_the_response():
    context, plan = _plan(_step("hist-1", MasterSpecialist.HISTORICAL_ANALYTICS, HISTORY, MasterRouteTemporalIntent.HISTORICAL))
    run = historical_exact_count_run()
    forged = replace(finalize_historical_specialist_run(run), answer="Forged operational count of 99 aircraft.")
    evidence = _historical(plan.steps[0].invocation, run, artifact=forged)
    checked = corroborate_master_evidence((evidence,)).results[0]
    handoff = evaluate_master_response_policy(_bundle(context, plan, (evidence,)))
    assert checked.corroboration_status is MasterCorroborationStatus.INCONSISTENT
    assert handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert "99" not in handoff.status_text
    assert forged.answer not in handoff.status_text
    _suppresses(handoff)


def test_missing_historical_run_is_not_fabricated():
    context, plan = _plan(_step("hist-1", MasterSpecialist.HISTORICAL_ANALYTICS, HISTORY, MasterRouteTemporalIntent.HISTORICAL))
    evidence = _historical(plan.steps[0].invocation, historical_exact_count_run(), with_run=False)
    checked = corroborate_master_evidence((evidence,)).results[0]
    handoff = evaluate_master_response_policy(_bundle(context, plan, (evidence,)))
    assert checked.corroboration_status is MasterCorroborationStatus.NOT_RECHECKABLE
    assert MasterResponseLimitation.HISTORICAL_RUN_NOT_FABRICATED in handoff.limitations
    assert handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    _suppresses(handoff)


def test_partial_historical_lower_bound_is_not_a_population_count():
    context, plan = _plan(_step("hist-1", MasterSpecialist.HISTORICAL_ANALYTICS, HISTORY, MasterRouteTemporalIntent.HISTORICAL))
    evidence = _historical(plan.steps[0].invocation, historical_truncated_lower_bound_run())
    handoff = evaluate_master_response_policy(_bundle(context, plan, (evidence,)))
    assert handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert "6" not in handoff.status_text
    _suppresses(handoff)


def test_decision_supplied_evidence_does_not_authorize_execution():
    context, plan = _plan(
        _step("dec-1", MasterSpecialist.DECISION, RISK, MasterRouteTemporalIntent.CURRENT),
        target_candidate_aircraft_id="ac-1",
    )
    evidence = MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(plan.steps[0].invocation, decision_offline_consistent_run())
    )
    checked = corroborate_master_evidence((evidence,)).results[0]
    handoff = evaluate_master_response_policy(_bundle(context, plan, (evidence,)))
    assert checked.corroboration_status is MasterCorroborationStatus.CONSISTENT_WITH_SUPPLIED_EVIDENCE
    assert handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert handoff.execution_authority.value == "DENIED"
    _suppresses(handoff)


def test_decision_unsupported_is_not_an_operational_conclusion():
    context, plan = _plan(
        _step("dec-1", MasterSpecialist.DECISION, RISK, MasterRouteTemporalIntent.CURRENT),
        target_candidate_aircraft_id="ac-1",
    )
    artifact = _decision_unsupported()
    assert artifact.status is DecisionSpecialistRunStatus.UNSUPPORTED
    evidence = MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(plan.steps[0].invocation, artifact)
    )
    handoff = evaluate_master_response_policy(_bundle(context, plan, (evidence,)))
    assert handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert "risk" not in handoff.status_text.lower()
    _suppresses(handoff)


def test_live_ops_without_tool_results_stays_nonfactual():
    context, plan = _plan(_step("live-1", MasterSpecialist.LIVE_OPS, LIVE, MasterRouteTemporalIntent.CURRENT))
    evidence = _live(plan.steps[0].invocation, live_ops_all_verified_result())
    checked = corroborate_master_evidence((evidence,)).results[0]
    handoff = evaluate_master_response_policy(_bundle(context, plan, (evidence,)))
    assert checked.corroboration_status is MasterCorroborationStatus.NOT_RECHECKABLE
    assert MasterResponseLimitation.LIVE_OPS_REFS_NOT_TOOL_RESULTS in handoff.limitations
    assert handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    _suppresses(handoff)


def test_rejected_admission_blocks_a_factual_response():
    context, plan = _plan(_step("hist-1", MasterSpecialist.HISTORICAL_ANALYTICS, HISTORY, MasterRouteTemporalIntent.HISTORICAL))
    run = historical_exact_count_run()
    evidence = MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(plan.steps[0].invocation, run)
    )
    assert isinstance(evidence.admission_input.artifact, HistoricalSpecialistRunResult)
    checked = corroborate_master_evidence((evidence,)).results[0]
    handoff = evaluate_master_response_policy(_bundle(context, plan, (evidence,)))
    assert checked.corroboration_status is MasterCorroborationStatus.ADMISSION_REJECTED
    assert handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    _suppresses(handoff)


def test_unresolved_binding_is_not_promoted():
    context, plan = _plan(_step("hist-1", MasterSpecialist.HISTORICAL_ANALYTICS, HISTORY, MasterRouteTemporalIntent.HISTORICAL))
    evidence = _historical(plan.steps[0].invocation, historical_exact_count_run())
    checked = corroborate_master_evidence((evidence,)).results[0]
    handoff = evaluate_master_response_policy(_bundle(context, plan, (evidence,)))
    assert checked.admission_status.value == "BINDING_UNRESOLVED"
    assert handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    _suppresses(handoff)


def test_incompatible_specialist_comparison_is_not_unsupported_response():
    context, plan = _plan(
        _step("live-1", MasterSpecialist.LIVE_OPS, LIVE, MasterRouteTemporalIntent.CURRENT),
        _step("hist-1", MasterSpecialist.HISTORICAL_ANALYTICS, HISTORY, MasterRouteTemporalIntent.HISTORICAL),
    )
    live = next(step for step in plan.steps if step.invocation.specialist is MasterSpecialist.LIVE_OPS)
    historical = next(
        step for step in plan.steps if step.invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    )
    evidence = (
        _live(live.invocation, live_ops_all_verified_result()),
        _historical(historical.invocation, historical_exact_count_run()),
    )
    request = MasterComparisonRequest(
        context.master_request_id,
        live.invocation.invocation_id,
        historical.invocation.invocation_id,
        MasterComparisonCategory.EXACT_COUNT_VS_LOWER_BOUND,
    )
    assessment = assess_master_evidence_comparability(
        request,
        evidence[0].admission_input,
        evidence[1].admission_input,
    )
    handoff = evaluate_master_response_policy(_bundle(context, plan, evidence, (request,)))
    assert assessment.eligibility is MasterComparisonEligibility.BLOCKED
    assert handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert handoff.state is not MasterResponsePolicyState.UNSUPPORTED
    _suppresses(handoff)


def test_missing_comparison_evidence_is_not_fabricated():
    context, plan = _plan(
        _step("hist-1", MasterSpecialist.HISTORICAL_ANALYTICS, HISTORY, MasterRouteTemporalIntent.HISTORICAL),
        _step("hist-2", MasterSpecialist.HISTORICAL_ANALYTICS, HISTORY, MasterRouteTemporalIntent.HISTORICAL),
    )
    first, second = plan.steps
    evidence = (_historical(first.invocation, historical_exact_count_run()),)
    request = MasterComparisonRequest(
        context.master_request_id,
        first.invocation.invocation_id,
        second.invocation.invocation_id,
        MasterComparisonCategory.EXACT_COUNT_VS_LOWER_BOUND,
    )
    handoff = evaluate_master_response_policy(_bundle(context, plan, evidence, (request,)))
    assert handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert MasterResponseLimitation.COMPARABILITY_NOT_OPERATIONAL_AGREEMENT in handoff.limitations
    _suppresses(handoff)


def test_contract_checkable_comparison_is_not_operational_agreement():
    context, plan = _plan(
        _step("hist-1", MasterSpecialist.HISTORICAL_ANALYTICS, HISTORY, MasterRouteTemporalIntent.HISTORICAL),
        _step("hist-2", MasterSpecialist.HISTORICAL_ANALYTICS, HISTORY, MasterRouteTemporalIntent.HISTORICAL),
    )
    first, second = plan.steps
    evidence = (
        _historical(first.invocation, historical_exact_count_run()),
        _historical(second.invocation, historical_truncated_lower_bound_run()),
    )
    request = MasterComparisonRequest(
        context.master_request_id,
        first.invocation.invocation_id,
        second.invocation.invocation_id,
        MasterComparisonCategory.EXACT_COUNT_VS_LOWER_BOUND,
    )
    assessment = assess_master_evidence_comparability(
        request,
        evidence[0].admission_input,
        evidence[1].admission_input,
    )
    handoff = evaluate_master_response_policy(_bundle(context, plan, evidence, (request,)))
    assert assessment.eligibility is MasterComparisonEligibility.CONTRACT_CHECKABLE_ONLY
    assert handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert MasterResponseLimitation.COMPARABILITY_NOT_OPERATIONAL_AGREEMENT in handoff.limitations
    _suppresses(handoff)


def test_different_evaluation_times_are_not_joined():
    context, plan = _plan(
        _step("live-1", MasterSpecialist.LIVE_OPS, LIVE, MasterRouteTemporalIntent.CURRENT),
        _step("hist-1", MasterSpecialist.HISTORICAL_ANALYTICS, HISTORY, MasterRouteTemporalIntent.HISTORICAL),
    )
    live = next(step for step in plan.steps if step.invocation.specialist is MasterSpecialist.LIVE_OPS)
    historical = next(
        step for step in plan.steps if step.invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    )
    historical_evidence = _historical(historical.invocation, historical_exact_count_run())
    evidence = (
        _live(live.invocation, live_ops_all_verified_result()),
        historical_evidence,
    )
    request = MasterComparisonRequest(
        context.master_request_id,
        live.invocation.invocation_id,
        historical.invocation.invocation_id,
        MasterComparisonCategory.QUERY_TIME_VS_OBSERVATION_TIME,
    )
    handoff = evaluate_master_response_policy(_bundle(context, plan, evidence, (request,)))
    assert evidence[0].admission_input.artifact.result.reference_utc != historical_evidence.admission_input.artifact.evaluated_as_of_utc
    assert handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert historical_evidence.admission_input.artifact.evaluated_as_of_utc not in handoff.status_text
    _suppresses(handoff)


def test_mixed_evidence_stays_insufficient():
    context, plan = _plan(
        _step("hist-1", MasterSpecialist.HISTORICAL_ANALYTICS, HISTORY, MasterRouteTemporalIntent.HISTORICAL),
        _step("hist-2", MasterSpecialist.HISTORICAL_ANALYTICS, HISTORY, MasterRouteTemporalIntent.HISTORICAL),
    )
    first, second = plan.steps
    evidence = (
        _historical(first.invocation, historical_exact_count_run()),
        _historical(second.invocation, historical_forged_count_run()),
    )
    handoff = evaluate_master_response_policy(_bundle(context, plan, evidence))
    assert evidence[1].admission_input.artifact.status is SpecialistStatus.UNAVAILABLE
    assert handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    _suppresses(handoff)


def test_explicit_live_ops_unavailable_can_be_named_without_a_count():
    context, plan = _plan(_step("live-1", MasterSpecialist.LIVE_OPS, LIVE, MasterRouteTemporalIntent.CURRENT))
    evidence = _live(plan.steps[0].invocation, ledger_not_authorized())
    handoff = evaluate_master_response_policy(_bundle(context, plan, (evidence,)))
    assert handoff.state is MasterResponsePolicyState.UNAVAILABLE
    assert "No count or zero is inferred." in handoff.status_text
    _suppresses(handoff)


def test_explicit_live_ops_unsupported_is_not_guessed_from_comparability():
    context, plan = _plan(_step("live-1", MasterSpecialist.LIVE_OPS, LIVE, MasterRouteTemporalIntent.CURRENT))
    evidence = _live(plan.steps[0].invocation, unsupported_request())
    handoff = evaluate_master_response_policy(_bundle(context, plan, (evidence,)))
    assert handoff.state is MasterResponsePolicyState.UNSUPPORTED
    assert handoff.status_text.startswith("The request is unsupported.")
    _suppresses(handoff)


def test_repeated_evaluation_is_deterministic():
    context, plan = _plan(_step("hist-1", MasterSpecialist.HISTORICAL_ANALYTICS, HISTORY, MasterRouteTemporalIntent.HISTORICAL))
    workflow = _bundle(context, plan, (_historical(plan.steps[0].invocation, historical_exact_count_run()),))
    first = evaluate_master_response_policy(workflow)
    second = evaluate_master_response_policy(workflow)
    assert first.state is second.state
    assert first.limitations == second.limitations
    assert first.status_text == second.status_text
    assert first.limitations == tuple(item for item in MasterResponseLimitation if item in first.limitations)


def test_policy_module_does_not_import_runtimes():
    imported: set[str] = set()
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert FORBIDDEN.isdisjoint(imported)
    env = os.environ.copy()
    shared = str(REPO_ROOT / "functions" / "shared")
    env["PYTHONPATH"] = shared if not env.get("PYTHONPATH") else shared + os.pathsep + env["PYTHONPATH"]
    script = f"""
import sys
import wilvor_ai.master_response_policy
loaded = [name for name in {sorted(FORBIDDEN)!r} if name in sys.modules]
assert loaded == [], loaded
"""
    completed = subprocess.run([sys.executable, "-B", "-c", script], env=env, check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr + completed.stdout
