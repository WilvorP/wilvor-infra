"""Offline Master-to-Historical binding. The specialist is not executed."""

from __future__ import annotations

import ast
import time
from dataclasses import replace
from enum import Enum
from pathlib import Path

import pytest

from tests.fixtures.ma4_specialist_evidence_examples import (
    historical_exact_count_run,
    historical_incomplete_coverage_run,
    historical_unknown_run,
    historical_verified_zero_without_evaluable_coverage_run,
    live_ops_all_verified_result,
    live_ops_context,
    live_ops_request,
)
from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.historical_answer_renderer import finalize_historical_specialist_run
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.live_ops_public_contracts import LiveOpsTrustedContext
from wilvor_ai.master_evidence_admission import (
    LiveOpsAdmissionSources,
    MasterAdmissionReason,
    MasterAdmissionStatus,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
    admit_master_evidence,
)
from wilvor_ai.master_evidence_corroboration import MasterEvidenceCorroborationInput
from wilvor_ai.master_historical_binding import (
    HISTORICAL_FINALIZATION_ENTRYPOINT,
    MasterHistoricalBinding,
    MasterHistoricalPlanScope,
    MasterHistoricalPlanningFacts,
    prepare_master_historical_binding,
    refuse_historical_binding_evidence,
)
from wilvor_ai.master_parallel_contracts import (
    MasterParallelContractBundle,
    MasterProvenanceRequirement,
)
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteOutcome,
    MasterRoutePlan,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.master_specialist_adapters import (
    MasterAdapterExecutionState,
    MasterSpecialistAdapterDescriptor,
    describe_planned_adapters,
)
from wilvor_ai.specialist_contracts import (
    HistoricalSpecialistTrustedContext,
    SpecialistRequest,
    VerifiedZeroClaim,
)
import wilvor_ai.master_historical_binding as binding_module


TEXT = "Which aircraft are currently affected by hazards, and what recorded history applies?"
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
MASTER = "master-req-hist-bind-1"
WINDOW_START = "2026-01-01T00:00:00Z"
WINDOW_END = "2026-01-02T00:00:00Z"
APPLICATION_TIME = "2026-01-15T00:00:01Z"
REQUESTED_EVALUATION_TIME = "2026-01-10T12:00:00Z"
MODULE = (
    Path(__file__).resolve().parents[2]
    / "functions"
    / "shared"
    / "wilvor_ai"
    / "master_historical_binding.py"
)
FORBIDDEN_MODULES = {
    "anthropic",
    "boto3",
    "botocore",
    "langgraph",
    "openai",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_analytics",
    "wilvor_ai.historical_answer_renderer",
    "wilvor_ai.historical_evidence_verifier",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.providers",
    "wilvor_historical_query",
    "wilvor_operational",
}


def _context() -> MasterPlanningContext:
    return MasterPlanningContext(
        master_request_id=MASTER,
        correlation_id="corr-hist-bind-1",
        application_request_time_utc=APPLICATION_TIME,
        canonical_user_text=TEXT,
    )


def _proposal(*steps: MasterProposedStep) -> MasterRouteProposal:
    capabilities = tuple(
        dict.fromkeys(
            MasterProposedCapability(step.capability, MasterRequirement.REQUIRED) for step in steps
        )
    )
    return MasterRouteProposal(user_text=TEXT, capabilities=capabilities, steps=steps)


def _history(
    step_id: str = "hist-1",
    *,
    start: str = WINDOW_START,
    end: str = WINDOW_END,
) -> MasterProposedStep:
    return MasterProposedStep(
        step_id=step_id,
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=HISTORY,
        temporal_intent=MasterRouteTemporalIntent.HISTORICAL,
        historical_start_utc=start,
        historical_end_utc=end,
    )


def _live(step_id: str = "live-1") -> MasterProposedStep:
    return MasterProposedStep(
        step_id=step_id,
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=LIVE,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
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


def _bundle(
    *steps: MasterProposedStep,
    requested_evaluation_time_utc: str | None = None,
) -> MasterParallelContractBundle:
    context = _context()
    decision = authorize_route(_proposal(*steps), context)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    assert decision.plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    bundle = MasterParallelContractBundle(
        context=context,
        plan=decision.plan,
        evidence_inputs=(),
    )
    if requested_evaluation_time_utc is None:
        return bundle
    updated = []
    for step in bundle.plan.steps:
        if step.invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
            updated.append(
                replace(
                    step,
                    invocation=replace(
                        step.invocation,
                        requested_evaluation_time_utc=requested_evaluation_time_utc,
                    ),
                )
            )
        else:
            updated.append(step)
    return replace(bundle, plan=replace(bundle.plan, steps=tuple(updated)))


def _historical_step(bundle: MasterParallelContractBundle):
    matches = tuple(
        step
        for step in bundle.plan.steps
        if step.invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    )
    assert len(matches) == 1
    return matches[0]


def _descriptor(
    bundle: MasterParallelContractBundle,
    specialist: MasterSpecialist,
) -> MasterSpecialistAdapterDescriptor:
    matches = tuple(
        item
        for item in describe_planned_adapters(bundle).descriptors
        if item.specialist is specialist
    )
    assert len(matches) == 1
    return matches[0]


def _prepared(**bundle_overrides: object) -> MasterHistoricalBinding:
    bundle = _bundle(_history(), **bundle_overrides)  # type: ignore[arg-type]
    return prepare_master_historical_binding(bundle, _descriptor(bundle, MasterSpecialist.HISTORICAL_ANALYTICS))


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_valid_historical_plan_binds_without_execution():
    bundle = _bundle(_history())
    step = _historical_step(bundle)
    descriptor = _descriptor(bundle, MasterSpecialist.HISTORICAL_ANALYTICS)
    binding = prepare_master_historical_binding(bundle, descriptor)
    again = prepare_master_historical_binding(bundle, descriptor)

    assert binding == again
    assert "bundle" not in MasterHistoricalBinding.__dataclass_fields__
    assert "evidence_inputs" not in MasterHistoricalBinding.__dataclass_fields__
    assert isinstance(binding.planning, MasterHistoricalPlanningFacts)
    assert not isinstance(binding.planning, MasterParallelContractBundle)
    assert binding.planning.descriptor is descriptor
    assert binding.planning.authorization is step.invocation
    assert binding.descriptor is descriptor
    assert binding.authorization is step.invocation
    assert binding.canonical_user_text == TEXT
    assert binding.plan_window_scope is MasterHistoricalPlanScope.UNBOUND
    assert binding.execution_state is MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
    assert binding.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert binding.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert binding.production_authority is MasterProductionAuthority.DENIED
    assert binding.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert binding.end_to_end_fulfilled is False
    assert binding.dispatch_permitted is False
    assert binding.stops_before_execution_authorization is True
    assert binding.evidence_issued is False
    assert binding.finalization_performed is False
    assert binding.finalization_entrypoint == HISTORICAL_FINALIZATION_ENTRYPOINT
    assert binding.finalization_entrypoint == finalize_historical_specialist_run.__name__
    assert binding.missing_provenance == tuple(MasterProvenanceRequirement)
    assert step.ready_to_execute is True
    assert callable(binding) is False
    assert not hasattr(binding, "run")
    assert not hasattr(binding, "dispatch")
    assert not hasattr(binding_module, "HistoricalAnalyticsSpecialist")
    assert not hasattr(binding_module, "finalize_historical_specialist_run")
    assert "binding_id" not in MasterHistoricalBinding.__dataclass_fields__


def test_request_identity_is_propagated_and_not_minted():
    bundle = _bundle(_history())
    binding = prepare_master_historical_binding(
        bundle,
        _descriptor(bundle, MasterSpecialist.HISTORICAL_ANALYTICS),
    )
    step = _historical_step(bundle)
    authorization = step.invocation

    assert binding.step_id == "hist-1"
    assert binding.invocation_id == f"{MASTER}-1"
    assert binding.specialist_request_id == f"{MASTER}-specialist-1"
    assert binding.correlation_id == "corr-hist-bind-1"
    assert binding.invocation_id == authorization.invocation_id
    assert binding.specialist_request_id == authorization.specialist_request_id
    assert binding.correlation_id == authorization.correlation_id
    assert binding.authorization.master_request_id == MASTER
    assert binding.specialist_request == SpecialistRequest(
        text=TEXT,
        request_id=authorization.specialist_request_id,
    )
    assert binding.specialist_request.request_id == authorization.specialist_request_id
    assert binding.specialist_request.request_id != authorization.correlation_id
    assert binding.specialist_request.request_id != APPLICATION_TIME
    assert binding.specialist_request.request_id != WINDOW_START
    assert binding.request_id_is_historical_evidence is False
    assert binding.request_id_is_evaluation_time is False
    assert binding.request_id_is_correlation_proof is False
    assert binding.native_correlation_resolved is False
    assert binding.native_specialist_request_id_resolved is False


def test_each_historical_step_binds_by_its_own_invocation():
    bundle = _bundle(
        _history("hist-1", start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z"),
        _history("hist-2", start="2026-01-03T00:00:00Z", end="2026-01-04T00:00:00Z"),
    )
    historical_steps = tuple(
        step
        for step in bundle.plan.steps
        if step.invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    )
    assert len(historical_steps) == 2
    descriptors = tuple(
        item
        for item in describe_planned_adapters(bundle).descriptors
        if item.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    )
    assert [item.invocation_id for item in descriptors] == [
        step.invocation.invocation_id for step in historical_steps
    ]
    first = prepare_master_historical_binding(bundle, descriptors[0])
    second = prepare_master_historical_binding(bundle, descriptors[1])

    assert first.authorization is historical_steps[0].invocation
    assert second.authorization is historical_steps[1].invocation
    assert first.step_id == "hist-1"
    assert second.step_id == "hist-2"
    assert first.invocation_id == f"{MASTER}-1"
    assert second.invocation_id == f"{MASTER}-2"
    assert first.specialist_request_id == f"{MASTER}-specialist-1"
    assert second.specialist_request_id == f"{MASTER}-specialist-2"
    assert first.invocation_id != second.invocation_id
    assert first.specialist_request_id != second.specialist_request_id
    assert first.specialist_request.request_id == first.specialist_request_id
    assert second.specialist_request.request_id == second.specialist_request_id
    assert first.unbound_plan_window_start_utc == "2026-01-01T00:00:00Z"
    assert first.unbound_plan_window_end_utc == "2026-01-02T00:00:00Z"
    assert second.unbound_plan_window_start_utc == "2026-01-03T00:00:00Z"
    assert second.unbound_plan_window_end_utc == "2026-01-04T00:00:00Z"
    assert first.correlation_id == second.correlation_id == "corr-hist-bind-1"
    for binding in (first, second):
        assert binding.execution_authority is MasterRouteExecutionAuthority.DENIED
        assert binding.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
        assert binding.production_authority is MasterProductionAuthority.DENIED
        assert binding.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
        assert binding.dispatch_permitted is False
        assert binding.window_evaluated is False
        assert binding.window_covered_by_data is False
        assert binding.historical_window_bound_to_request is False
        assert binding.evaluated_as_of_utc is None
        assert binding.trusted_context is None
        assert binding.historical_run is None
        assert binding.specialist_result is None
        assert binding.evidence_issued is False
        assert binding.missing_provenance == tuple(MasterProvenanceRequirement)
        assert binding.plan_window_scope is MasterHistoricalPlanScope.UNBOUND
    with pytest.raises(ContractValidationError, match="specialist_request_id_mismatch"):
        prepare_master_historical_binding(
            bundle,
            replace(descriptors[0], specialist_request_id=descriptors[1].specialist_request_id),
        )


def _reachable(root: object) -> list[object]:
    seen: set[int] = set()
    found: list[object] = []
    stack = [root]
    while stack:
        obj = stack.pop()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        found.append(obj)
        if obj is None or isinstance(obj, (str, bytes, int, float, bool, Enum)):
            continue
        fields = getattr(obj, "__dataclass_fields__", None)
        if fields is not None:
            stack.extend(getattr(obj, name) for name in fields)
            continue
        if isinstance(obj, (tuple, list, set, frozenset)):
            stack.extend(obj)
            continue
        if isinstance(obj, dict):
            stack.extend(obj.keys())
            stack.extend(obj.values())
    return found


def test_supplied_evidence_is_not_retained():
    bundle = _bundle(_history(), _live())
    historical = _descriptor(bundle, MasterSpecialist.HISTORICAL_ANALYTICS)
    historical_step = _historical_step(bundle)
    live_step = next(
        step
        for step in bundle.plan.steps
        if step.invocation.specialist is MasterSpecialist.LIVE_OPS
    )
    run = historical_exact_count_run()
    finalized = finalize_historical_specialist_run(run)
    trusted_context = live_ops_context()
    live_sources = LiveOpsAdmissionSources(
        live_ops_request(),
        trusted_context,
        live_ops_all_verified_result(),
    )
    loaded = replace(
        bundle,
        evidence_inputs=(
            MasterEvidenceCorroborationInput(
                MasterEvidenceAdmissionInput(historical_step.invocation, finalized),
                historical_run=run,
            ),
            MasterEvidenceCorroborationInput(
                MasterEvidenceAdmissionInput(live_step.invocation, live_sources),
            ),
        ),
    )
    assert loaded.evidence_inputs
    binding = prepare_master_historical_binding(loaded, historical)
    reachable = _reachable(binding)
    assert loaded not in reachable
    assert bundle not in reachable
    for item in loaded.evidence_inputs:
        assert item not in reachable
        assert item.admission_input not in reachable
        assert item.admission_input.artifact not in reachable
    assert run not in reachable
    assert finalized not in reachable
    assert trusted_context not in reachable
    assert live_sources not in reachable
    assert binding.historical_run is None
    assert binding.specialist_result is None
    assert binding.trusted_context is None
    assert binding.evaluated_as_of_utc is None
    assert not any(isinstance(obj, HistoricalSpecialistTrustedContext) for obj in reachable)
    assert not any(isinstance(obj, LiveOpsTrustedContext) for obj in reachable)
    assert not any(isinstance(obj, MasterParallelContractBundle) for obj in reachable)
    assert binding.authorization is historical_step.invocation
    assert binding.invocation_id == historical_step.invocation.invocation_id


def test_wrong_specialist_and_invocation_are_rejected():
    bundle = _bundle(_history(), _live(), _decision())
    historical = _descriptor(bundle, MasterSpecialist.HISTORICAL_ANALYTICS)
    with pytest.raises(ContractValidationError, match="specialist_mismatch"):
        prepare_master_historical_binding(bundle, _descriptor(bundle, MasterSpecialist.LIVE_OPS))
    with pytest.raises(ContractValidationError, match="specialist_mismatch"):
        prepare_master_historical_binding(bundle, _descriptor(bundle, MasterSpecialist.DECISION))
    foreign = MasterSpecialistAdapterDescriptor(
        master_request_id=MASTER,
        step_id=historical.step_id,
        invocation_id="inv-not-planned",
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        specialist_request_id="req-not-planned",
    )
    with pytest.raises(ContractValidationError, match="invocation_not_in_plan"):
        prepare_master_historical_binding(bundle, foreign)
    live = _descriptor(bundle, MasterSpecialist.LIVE_OPS)
    relabeled = replace(live, specialist=MasterSpecialist.HISTORICAL_ANALYTICS)
    with pytest.raises(ContractValidationError, match="specialist_mismatch"):
        prepare_master_historical_binding(bundle, relabeled)
    with pytest.raises(ContractValidationError, match="invalid_parallel_contract"):
        prepare_master_historical_binding(None, historical)  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="invalid_adapter_descriptor"):
        prepare_master_historical_binding(bundle, None)  # type: ignore[arg-type]


def test_missing_or_malformed_planning_identity_is_rejected():
    historical = _bundle(_history())
    descriptor = _descriptor(historical, MasterSpecialist.HISTORICAL_ANALYTICS)
    empty = MasterParallelContractBundle(
        context=_context(),
        plan=MasterRoutePlan(
            master_request_id=MASTER,
            canonical_user_text=TEXT,
            steps=(),
        ),
        evidence_inputs=(),
    )
    with pytest.raises(ContractValidationError, match="invocation_not_in_plan"):
        prepare_master_historical_binding(empty, descriptor)
    live = _bundle(_live())
    with pytest.raises(ContractValidationError, match="specialist_mismatch"):
        prepare_master_historical_binding(live, _descriptor(live, MasterSpecialist.LIVE_OPS))
    with pytest.raises(ContractValidationError, match="specialist_request_id_mismatch"):
        prepare_master_historical_binding(
            historical,
            replace(descriptor, specialist_request_id="other-specialist-request"),
        )
    with pytest.raises(ContractValidationError, match="invocation_binding_mismatch"):
        prepare_master_historical_binding(historical, replace(descriptor, step_id="other-step"))
    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        prepare_master_historical_binding(
            historical,
            replace(descriptor, master_request_id="other-master-request"),
        )
    with pytest.raises(ContractValidationError, match="invocation_binding_mismatch"):
        prepare_master_historical_binding(
            historical,
            replace(descriptor, purpose=MasterPurpose.DISCOVERY),
        )
    with pytest.raises(ContractValidationError, match="invalid_specialist_request_id"):
        replace(descriptor, specialist_request_id=" ")
    with pytest.raises(TypeError):
        prepare_master_historical_binding(
            historical,
            descriptor,
            as_of_utc=APPLICATION_TIME,  # type: ignore[call-arg]
        )


def test_historical_window_is_preserved_and_not_executed():
    bundle = _bundle(_history())
    binding = prepare_master_historical_binding(
        bundle,
        _descriptor(bundle, MasterSpecialist.HISTORICAL_ANALYTICS),
    )
    step = _historical_step(bundle)

    assert binding.unbound_plan_window_start_utc == WINDOW_START
    assert binding.unbound_plan_window_end_utc == WINDOW_END
    assert binding.unbound_plan_window_start_utc == step.historical_start_utc
    assert binding.unbound_plan_window_end_utc == step.historical_end_utc
    assert binding.plan_window_scope is MasterHistoricalPlanScope.UNBOUND
    assert binding.structured_request_arguments is None
    assert binding.historical_window_bound_to_request is False
    assert binding.window_evaluated is False
    assert binding.window_covered_by_data is False
    assert binding.executed_tool_call_count is None
    assert binding.specialist_request.text == TEXT
    assert WINDOW_START not in binding.specialist_request.text
    assert WINDOW_END not in binding.specialist_request.text
    appended = SpecialistRequest(
        text=f"{TEXT} {WINDOW_START} {WINDOW_END}",
        request_id=binding.specialist_request_id,
    )
    with pytest.raises(ContractValidationError, match="canonical_user_text_mismatch"):
        replace(binding, specialist_request=appended)
    with pytest.raises(ContractValidationError, match="historical_window_not_authorized"):
        replace(
            binding,
            structured_request_arguments={  # type: ignore[arg-type]
                "start_utc": WINDOW_START,
                "end_utc": WINDOW_END,
            },
        )
    with pytest.raises(ContractValidationError, match="historical_window_not_authorized"):
        replace(binding, historical_window_bound_to_request=True)
    with pytest.raises(ContractValidationError, match="historical_window_not_evaluated"):
        replace(binding, window_evaluated=True)
    with pytest.raises(ContractValidationError, match="historical_window_not_covered"):
        replace(binding, window_covered_by_data=True)
    with pytest.raises(ContractValidationError, match="historical_window_not_evaluated"):
        replace(binding, executed_tool_call_count=0)
    with pytest.raises(ContractValidationError, match="historical_window_not_authorized"):
        replace(binding, unbound_plan_window_start_utc="2026-01-08T00:00:00Z")
    with pytest.raises(ContractValidationError, match="canonical_user_text_mismatch"):
        replace(binding, canonical_user_text=f"{TEXT} {WINDOW_START}")


def test_requested_evaluation_time_is_not_copied_into_evidence(monkeypatch):
    def _refuse_clock(*_args, **_kwargs):
        raise AssertionError("wall clock consulted")

    bundle = _bundle(_history(), requested_evaluation_time_utc=REQUESTED_EVALUATION_TIME)
    descriptor = _descriptor(bundle, MasterSpecialist.HISTORICAL_ANALYTICS)
    monkeypatch.setattr(time, "time", _refuse_clock)
    binding = prepare_master_historical_binding(bundle, descriptor)

    assert binding.application_request_time_utc == APPLICATION_TIME
    assert binding.requested_evaluation_time_utc == REQUESTED_EVALUATION_TIME
    assert binding.unbound_plan_window_start_utc == WINDOW_START
    assert binding.unbound_plan_window_end_utc == WINDOW_END
    assert binding.evaluated_as_of_utc is None
    assert binding.trusted_context is None
    assert len(
        {
            binding.application_request_time_utc,
            binding.requested_evaluation_time_utc,
            binding.unbound_plan_window_start_utc,
            binding.unbound_plan_window_end_utc,
        }
    ) == 4
    with pytest.raises(ContractValidationError, match="evaluated_as_of_not_evidence"):
        replace(binding, evaluated_as_of_utc=REQUESTED_EVALUATION_TIME)
    with pytest.raises(ContractValidationError, match="evaluated_as_of_not_evidence"):
        replace(binding, evaluated_as_of_utc=APPLICATION_TIME)
    with pytest.raises(ContractValidationError, match="evaluated_as_of_not_evidence"):
        replace(binding, evaluated_as_of_utc=WINDOW_END)
    with pytest.raises(ContractValidationError, match="requested_evaluation_time_mismatch"):
        replace(binding, requested_evaluation_time_utc=APPLICATION_TIME)
    with pytest.raises(ContractValidationError, match="application_time_mismatch"):
        replace(binding, application_request_time_utc=REQUESTED_EVALUATION_TIME)


def test_trusted_context_is_not_built_from_an_arbitrary_clock():
    binding = _prepared(requested_evaluation_time_utc=REQUESTED_EVALUATION_TIME)
    assert binding.trusted_context is None
    for clock in (
        APPLICATION_TIME,
        REQUESTED_EVALUATION_TIME,
        WINDOW_START,
        "2026-06-01T00:00:00Z",
    ):
        with pytest.raises(ContractValidationError, match="trusted_context_not_authorized"):
            replace(
                binding,
                trusted_context=HistoricalSpecialistTrustedContext(as_of_utc=clock),
            )


def test_no_historical_run_is_fabricated():
    binding = _prepared()
    assert binding.historical_run is None
    assert binding.specialist_result is None
    with pytest.raises(ContractValidationError, match="historical_run_not_fabricated"):
        replace(binding, historical_run=historical_exact_count_run())
    with pytest.raises(ContractValidationError, match="historical_run_not_fabricated"):
        replace(binding, historical_run=historical_unknown_run())


def test_intermediate_run_is_not_final_evidence(monkeypatch):
    finalized = finalize_historical_specialist_run(historical_exact_count_run())

    def _boom(*_args, **_kwargs):
        raise AssertionError("binder finalized a historical run")

    monkeypatch.setattr(
        "wilvor_ai.historical_answer_renderer.finalize_historical_specialist_run",
        _boom,
    )
    monkeypatch.setattr(
        "wilvor_ai.master_evidence_corroboration.finalize_historical_specialist_run",
        _boom,
    )
    binding = _prepared()
    assert binding.finalization_performed is False
    assert binding.finalization_entrypoint == "finalize_historical_specialist_run"
    with pytest.raises(ContractValidationError, match="historical_run_not_final_artifact"):
        refuse_historical_binding_evidence(historical_exact_count_run())
    with pytest.raises(ContractValidationError, match="verified_evidence_not_issued"):
        refuse_historical_binding_evidence(finalized)
    with pytest.raises(ContractValidationError, match="verified_evidence_not_issued"):
        replace(binding, specialist_result=finalized)
    with pytest.raises(ContractValidationError, match="historical_finalization_not_performed"):
        replace(binding, finalization_performed=True)
    with pytest.raises(ContractValidationError, match="invalid_finalization_entrypoint"):
        replace(binding, finalization_entrypoint="prepare_master_historical_binding")
    assert binding.specialist_result is None


def test_failure_and_incomplete_coverage_are_not_zero():
    binding = _prepared()
    incomplete = historical_incomplete_coverage_run()
    failed = historical_unknown_run()
    unevaluable_zero = historical_verified_zero_without_evaluable_coverage_run()
    for artifact in (incomplete, failed, unevaluable_zero):
        with pytest.raises(ContractValidationError, match="historical_run_not_final_artifact"):
            refuse_historical_binding_evidence(artifact)
    finalized_incomplete = finalize_historical_specialist_run(incomplete)
    finalized_failed = finalize_historical_specialist_run(failed)
    finalized_unevaluable_zero = finalize_historical_specialist_run(unevaluable_zero)
    for result in (finalized_incomplete, finalized_failed, finalized_unevaluable_zero):
        assert result.verified_claims == ()
        assert not any(isinstance(item, VerifiedZeroClaim) for item in result.verified_claims)
        with pytest.raises(ContractValidationError, match="verified_evidence_not_issued"):
            refuse_historical_binding_evidence(result)
    assert binding.inferred_count is None
    assert binding.failure_interpreted_as_zero is False
    assert binding.incomplete_coverage_interpreted_as_zero is False
    assert binding.specialist_result is None
    with pytest.raises(ContractValidationError, match="failure_not_verified_zero"):
        replace(binding, inferred_count=0)
    with pytest.raises(ContractValidationError, match="failure_not_verified_zero"):
        replace(binding, failure_interpreted_as_zero=True)
    with pytest.raises(ContractValidationError, match="incomplete_coverage_not_verified_zero"):
        replace(binding, incomplete_coverage_interpreted_as_zero=True)


def test_unresolved_native_identity_behavior_remains_intact():
    binding = _prepared()
    authorization = binding.authorization
    assert authorization.correlation_id == "corr-hist-bind-1"
    assert authorization.specialist_request_id == f"{MASTER}-specialist-1"
    assert authorization.requested_evaluation_time_utc is None
    result = finalize_historical_specialist_run(historical_exact_count_run())
    admission = admit_master_evidence(
        (MasterEvidenceAdmissionInput(authorization, result),)
    ).admissions[0]
    assert admission.admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
    assert MasterAdmissionReason.NATIVE_CORRELATION_ABSENT in admission.reason_codes
    assert MasterAdmissionReason.NATIVE_SPECIALIST_REQUEST_ID_ABSENT in admission.reason_codes
    assert binding.specialist_request.request_id == authorization.specialist_request_id
    assert not hasattr(result, "correlation_id")
    assert not hasattr(result, "request_id")
    assert binding.native_correlation_resolved is False
    assert binding.native_specialist_request_id_resolved is False
    with pytest.raises(ContractValidationError, match="correlation_mismatch"):
        replace(binding, correlation_id=None)  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="specialist_request_id_mismatch"):
        replace(binding, specialist_request_id=None)  # type: ignore[arg-type]


def test_closed_execution_fields_reject_replacement():
    binding = _prepared()
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(binding, dispatch_permitted=True)
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(binding, stops_before_execution_authorization=False)
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(binding, execution_state="GRANTED")  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="execution_authority_not_granted"):
        replace(binding, execution_authority="GRANTED")  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        replace(binding, production_authority=MasterProductionAuthority.PRODUCTION)
    with pytest.raises(ContractValidationError, match="source_authenticity_not_established"):
        replace(binding, source_authenticity="AUTHENTICATED")  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="request_coverage_not_established"):
        replace(binding, request_coverage="ESTABLISHED")  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="end_to_end_fulfillment_not_granted"):
        replace(binding, end_to_end_fulfilled=True)
    with pytest.raises(ContractValidationError, match="verified_evidence_not_issued"):
        replace(binding, evidence_issued=True)
    with pytest.raises(ContractValidationError, match="request_id_not_historical_evidence"):
        replace(binding, request_id_is_historical_evidence=True)
    with pytest.raises(ContractValidationError, match="request_id_not_evaluation_time"):
        replace(binding, request_id_is_evaluation_time=True)
    with pytest.raises(ContractValidationError, match="request_id_not_correlation_proof"):
        replace(binding, request_id_is_correlation_proof=True)
    with pytest.raises(ContractValidationError, match="request_id_not_correlation_proof"):
        replace(binding, native_correlation_resolved=True)
    with pytest.raises(ContractValidationError, match="native_specialist_request_id_not_resolved"):
        replace(binding, native_specialist_request_id_resolved=True)
    with pytest.raises(ContractValidationError, match="provenance_requirement_not_satisfied"):
        replace(binding, missing_provenance=())
    with pytest.raises(ContractValidationError, match="invocation_binding_mismatch"):
        replace(binding, invocation_id="minted-invocation")


def test_binding_source_does_not_call_model_database_or_aws():
    source = MODULE.read_text(encoding="utf-8")
    imported = _imported_modules(MODULE)
    assert imported.isdisjoint(FORBIDDEN_MODULES)
    assert "finalize_historical_specialist_run(" not in source
    assert "HistoricalAnalyticsSpecialist(" not in source
    assert "HistoricalAnalyticsOperations" not in source
    assert "HistoricalAnalyticsAdapter" not in source
    assert "datetime.now" not in source
    assert "datetime.utcnow" not in source
    assert "time.time" not in source
    assert "boto3" not in source
    assert "botocore" not in source
    for banned in ("anthropic", "openai", "langgraph", "AthenaExecutor", "CoverageStore"):
        assert banned not in source
