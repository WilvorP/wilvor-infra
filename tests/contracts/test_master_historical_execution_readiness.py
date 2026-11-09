"""Offline readiness assessment for a Master Historical binding. Nothing executes."""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import datetime, timezone

import pytest

from tests.fixtures.ma4_specialist_evidence_examples import (
    historical_incomplete_coverage_run,
    historical_unverified_passed_result,
    historical_verified_zero_run,
)
from wilvor_ai.contracts import (
    AgentCapability,
    ContractValidationError,
    Evidence,
    QueryExecutionTrace,
    SourceRecord,
    ToolResult,
)
from wilvor_ai.historical_answer_renderer import finalize_historical_specialist_run
from wilvor_ai.master_contracts import MasterBindingDefect, MasterProductionAuthority, MasterPurpose, MasterRequirement, MasterSpecialist
from wilvor_ai.master_evidence_admission import MasterAdmissionReason, MasterSourceAuthenticity
from wilvor_ai.master_historical_binding import (
    HISTORICAL_FINALIZATION_ENTRYPOINT,
    MasterHistoricalBinding,
    MasterHistoricalPlanScope,
    prepare_master_historical_binding,
)
from wilvor_ai.master_historical_execution_readiness import (
    FINAL_RESULT_TYPE,
    INTERMEDIATE_RUN_TYPE,
    QUERY_ID_OWNER,
    SOURCE_VERSION_OWNER,
    MasterHistoricalExecutionReadiness,
    assess_master_historical_execution_readiness,
    assess_master_historical_execution_readiness_collection,
    historical_native_result_correlation_gaps,
)
from wilvor_ai.master_parallel_contracts import MasterParallelContractBundle, MasterProvenanceRequirement
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.master_specialist_adapters import MasterAdapterExecutionState, describe_planned_adapters
from wilvor_ai.specialist_contracts import (
    HistoricalSpecialistTrustedContext,
    SpecialistRequest,
    SpecialistResult,
)
from wilvor_ai.specialist_runtime_contracts import (
    ExecutedToolInvocation,
    HistoricalSpecialistRunResult,
    SpecialistRunStatus,
)
import wilvor_ai.master_historical_execution_readiness as readiness_module


TEXT = "Which aircraft are currently affected by hazards, and what recorded history applies?"
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
MASTER = "master-req-hist-ready-1"
CORRELATION = "corr-hist-ready-1"
WINDOW_START = "2026-01-01T00:00:00Z"
WINDOW_END = "2026-01-02T00:00:00Z"
APPLICATION_TIME = "2026-01-15T00:00:01Z"
REQUESTED_EVALUATION_TIME = "2026-01-10T12:00:00Z"
OTHER_WINDOW_START = "2026-01-03T00:00:00Z"
OTHER_WINDOW_END = "2026-01-04T00:00:00Z"
CORRELATION_FIELDS = (
    "master_request_id",
    "specialist_request_id",
    "request_id",
    "correlation_id",
    "application_request_time_utc",
    "requested_evaluation_time_utc",
    "unbound_plan_window_start_utc",
    "unbound_plan_window_end_utc",
    "start_utc",
    "end_utc",
    "authenticated_source_evaluation_time_utc",
    "source_version",
    "query_id",
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


def _bundle(
    *steps: MasterProposedStep,
    requested_evaluation_time_utc: str | None = None,
    master_request_id: str = MASTER,
    correlation_id: str = CORRELATION,
    application_request_time_utc: str = APPLICATION_TIME,
) -> MasterParallelContractBundle:
    context = MasterPlanningContext(
        master_request_id=master_request_id,
        correlation_id=correlation_id,
        application_request_time_utc=application_request_time_utc,
        canonical_user_text=TEXT,
    )
    decision = authorize_route(_proposal(*steps), context)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
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


def _descriptor(bundle: MasterParallelContractBundle, step_id: str):
    matches = tuple(
        item
        for item in describe_planned_adapters(bundle).descriptors
        if item.specialist is MasterSpecialist.HISTORICAL_ANALYTICS and item.step_id == step_id
    )
    assert len(matches) == 1
    return matches[0]


def _binding(*steps: MasterProposedStep, **overrides: object) -> MasterHistoricalBinding:
    chosen = steps or (_history(),)
    bundle = _bundle(*chosen, **overrides)  # type: ignore[arg-type]
    step_id = chosen[0].step_id
    return prepare_master_historical_binding(bundle, _descriptor(bundle, step_id))


def _bindings(*steps: MasterProposedStep, **overrides: object) -> tuple[MasterHistoricalBinding, ...]:
    bundle = _bundle(*steps, **overrides)  # type: ignore[arg-type]
    return tuple(
        prepare_master_historical_binding(bundle, _descriptor(bundle, step.step_id)) for step in steps
    )


def _gaps(assessment: MasterHistoricalExecutionReadiness) -> dict[str, tuple[str, ...]]:
    return {item.contract_name: item.missing_fields for item in assessment.correlation_gaps}


def test_readiness_preserves_identity_clocks_and_unbound_window():
    binding = _binding(requested_evaluation_time_utc=REQUESTED_EVALUATION_TIME)
    assessment = assess_master_historical_execution_readiness(binding)

    assert assessment.master_request_id == MASTER
    assert assessment.master_request_id == binding.authorization.master_request_id
    assert assessment.master_request_id == binding.descriptor.master_request_id
    assert assessment.invocation_id == f"{MASTER}-1"
    assert assessment.step_id == "hist-1"
    assert assessment.specialist_request_id == f"{MASTER}-specialist-1"
    assert assessment.specialist_request_id == binding.specialist_request.request_id
    assert assessment.correlation_id == CORRELATION
    assert assessment.specialist_request_id != assessment.correlation_id
    assert assessment.application_request_time_utc == APPLICATION_TIME
    assert assessment.requested_evaluation_time_utc == REQUESTED_EVALUATION_TIME
    assert assessment.unbound_plan_window_start_utc == WINDOW_START
    assert assessment.unbound_plan_window_end_utc == WINDOW_END
    assert assessment.authenticated_source_evaluation_time_utc is None
    assert assessment.binding.evaluated_as_of_utc is None
    assert assessment.authenticated_source_evaluation_time_utc != APPLICATION_TIME
    assert assessment.authenticated_source_evaluation_time_utc != REQUESTED_EVALUATION_TIME
    assert assessment.plan_window_scope is MasterHistoricalPlanScope.UNBOUND
    assert assessment.historical_window_bound_to_native_tool_arguments is False
    assert assessment.specialist_request_accepts_window_arguments is False
    assert assessment.structured_request_arguments is None
    assert binding.specialist_request.text == TEXT
    assert "start_utc" not in SpecialistRequest.__dataclass_fields__
    assert "end_utc" not in SpecialistRequest.__dataclass_fields__
    assert assessment.execution_state is MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
    assert assessment.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert assessment.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert assessment.production_authority is MasterProductionAuthority.DENIED
    assert assessment.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert assessment.dispatch_permitted is False
    assert assessment.stops_before_execution_authorization is True
    assert assessment.evidence_issued is False
    assert assessment.finalization_performed is False
    assert assessment.finalization_complete is False
    assert assessment.finalization_entrypoint == HISTORICAL_FINALIZATION_ENTRYPOINT
    assert assessment.finalization_entrypoint == finalize_historical_specialist_run.__name__
    assert assessment.intermediate_run_type == INTERMEDIATE_RUN_TYPE
    assert assessment.final_result_type == FINAL_RESULT_TYPE
    assert assessment.intermediate_run_type != assessment.final_result_type
    assert assessment.run_is_final_artifact is False
    assert assessment.historical_run is None
    assert assessment.specialist_result is None
    assert assessment.trusted_context is None
    assert assessment.inferred_count is None
    assert assessment.executed_tool_call_count is None
    assert assessment.source_version is None
    assert assessment.query_id is None
    assert assessment.query_result is None
    assert assessment.native_correlation_resolved is False
    assert assessment.native_specialist_request_id_resolved is False
    assert assessment.missing_provenance == tuple(MasterProvenanceRequirement)
    assert MasterAdmissionReason.MISSING_NATIVE_ARTIFACT in assessment.correlation_reasons
    assert MasterAdmissionReason.NATIVE_CORRELATION_ABSENT in assessment.correlation_reasons
    assert MasterAdmissionReason.NATIVE_SPECIALIST_REQUEST_ID_ABSENT in assessment.correlation_reasons
    assert MasterAdmissionReason.NATIVE_EVALUATION_TIME_ABSENT in assessment.correlation_reasons
    assert MasterAdmissionReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED in assessment.correlation_reasons
    assert callable(assessment) is False
    assert not hasattr(assessment, "run")
    assert not hasattr(readiness_module, "finalize_historical_specialist_run")
    assert not hasattr(readiness_module, "HistoricalAnalyticsSpecialist")


def test_multiple_historical_invocations_keep_separate_unbound_windows():
    first, second = _bindings(
        _history("hist-1", start=WINDOW_START, end=WINDOW_END),
        _history("hist-2", start=OTHER_WINDOW_START, end=OTHER_WINDOW_END),
        requested_evaluation_time_utc=REQUESTED_EVALUATION_TIME,
    )
    collection = assess_master_historical_execution_readiness_collection((first, second))

    assert collection.master_request_id == MASTER
    assert collection.correlation_id == CORRELATION
    assert collection.application_request_time_utc == APPLICATION_TIME
    assert [item.step_id for item in collection.assessments] == ["hist-1", "hist-2"]
    assert [item.invocation_id for item in collection.assessments] == [f"{MASTER}-1", f"{MASTER}-2"]
    assert [item.specialist_request_id for item in collection.assessments] == [
        f"{MASTER}-specialist-1",
        f"{MASTER}-specialist-2",
    ]
    assert collection.assessments[0].unbound_plan_window_start_utc == WINDOW_START
    assert collection.assessments[0].unbound_plan_window_end_utc == WINDOW_END
    assert collection.assessments[1].unbound_plan_window_start_utc == OTHER_WINDOW_START
    assert collection.assessments[1].unbound_plan_window_end_utc == OTHER_WINDOW_END
    assert "combined_window_start_utc" not in MasterHistoricalExecutionReadiness.__dataclass_fields__
    for item in collection.assessments:
        assert item.correlation_id == CORRELATION
        assert item.application_request_time_utc == APPLICATION_TIME
        assert item.requested_evaluation_time_utc == REQUESTED_EVALUATION_TIME
        assert item.authenticated_source_evaluation_time_utc is None
        assert item.historical_window_bound_to_native_tool_arguments is False
        assert item.structured_request_arguments is None
        assert item.binding.specialist_request.text == TEXT
        assert item.dispatch_permitted is False
        assert item.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED


def test_mismatched_ids_do_not_resolve_native_correlation():
    first, second = _bindings(
        _history("hist-1", start=WINDOW_START, end=WINDOW_END),
        _history("hist-2", start=OTHER_WINDOW_START, end=OTHER_WINDOW_END),
    )
    crossed = assess_master_historical_execution_readiness(
        first,
        observed_request=second.specialist_request,
    )
    foreign = assess_master_historical_execution_readiness(
        first,
        observed_request=SpecialistRequest(text=TEXT, request_id=CORRELATION),
    )

    assert crossed.binding_defects == (MasterBindingDefect.REQUEST_ID_MISMATCH,)
    assert crossed.specialist_request_id == first.specialist_request_id
    assert crossed.specialist_request_id != second.specialist_request_id
    assert crossed.unbound_plan_window_start_utc == WINDOW_START
    assert crossed.unbound_plan_window_end_utc == WINDOW_END
    assert crossed.native_specialist_request_id_resolved is False
    assert crossed.native_correlation_resolved is False
    assert crossed.dispatch_permitted is False
    assert foreign.binding_defects == (MasterBindingDefect.REQUEST_ID_MISMATCH,)
    assert foreign.correlation_id == CORRELATION
    assert foreign.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED

    run = historical_incomplete_coverage_run()
    mismatched_tool = replace(run.tool_results[0], correlation_id="corr-not-the-plan")
    mismatched = assess_master_historical_execution_readiness(
        first,
        observed_run=replace(run, tool_results=(mismatched_tool,)),
    )
    assert MasterBindingDefect.CORRELATION_MISMATCH in mismatched.binding_defects
    assert mismatched.native_correlation_resolved is False
    assert mismatched.tool_result_correlation_satisfies_result_contract is False
    assert mismatched.evidence_issued is False
    assert mismatched.authenticated_source_evaluation_time_utc is None


def test_matching_caller_supplied_values_do_not_open_the_gate():
    run = historical_incomplete_coverage_run()
    assert run.evaluated_as_of_utc is not None
    binding = _binding(requested_evaluation_time_utc=run.evaluated_as_of_utc)
    matched_tool = replace(run.tool_results[0], correlation_id=binding.correlation_id)
    assessment = assess_master_historical_execution_readiness(
        binding,
        observed_run=replace(run, tool_results=(matched_tool,)),
        observed_request=binding.specialist_request,
    )

    assert assessment.binding_defects == ()
    assert assessment.native_correlation_resolved is False
    assert assessment.native_specialist_request_id_resolved is False
    assert assessment.tool_result_correlation_satisfies_result_contract is False
    assert assessment.authenticated_source_evaluation_time_utc is None
    assert assessment.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert assessment.evidence_issued is False
    assert assessment.dispatch_permitted is False
    assert MasterAdmissionReason.NATIVE_CORRELATION_ABSENT in assessment.correlation_reasons
    assert MasterAdmissionReason.NATIVE_SPECIALIST_REQUEST_ID_ABSENT in assessment.correlation_reasons
    assert MasterAdmissionReason.NATIVE_EVALUATION_TIME_ABSENT in assessment.correlation_reasons
    assert "correlation_id" not in HistoricalSpecialistRunResult.__dataclass_fields__
    assert "specialist_request_id" not in HistoricalSpecialistRunResult.__dataclass_fields__
    assert "request_id" not in SpecialistResult.__dataclass_fields__


def test_incomplete_finalization_does_not_fabricate_a_result_or_count():
    run = historical_incomplete_coverage_run()
    assert run.evaluated_as_of_utc is not None
    assert any(getattr(claim, "value", None) not in (None, 0) for claim in run.proposed_claims)
    assert run.executed_tool_call_count > 0
    binding = _binding(requested_evaluation_time_utc=REQUESTED_EVALUATION_TIME)
    assessment = assess_master_historical_execution_readiness(binding, observed_run=run)

    assert assessment.observed_intermediate_run is True
    assert assessment.observed_final_result is False
    assert assessment.historical_run is None
    assert assessment.specialist_result is None
    assert not isinstance(assessment, SpecialistResult)
    assert assessment.finalization_performed is False
    assert assessment.finalization_complete is False
    assert assessment.run_is_final_artifact is False
    assert MasterAdmissionReason.HISTORICAL_RUN_NOT_FINAL_ARTIFACT in assessment.correlation_reasons
    assert (
        MasterAdmissionReason.HISTORICAL_RUN_CANNOT_REPRODUCE_VERIFICATION
        in assessment.correlation_reasons
    )
    assert MasterBindingDefect.TIME_MISMATCH in assessment.binding_defects
    assert assessment.authenticated_source_evaluation_time_utc is None
    assert assessment.inferred_count is None
    assert assessment.executed_tool_call_count is None
    assert assessment.source_version is None
    assert assessment.query_id is None
    assert assessment.query_result is None
    assert assessment.incomplete_coverage_interpreted_as_zero is False
    assert assessment.failure_interpreted_as_zero is False
    assert assessment.evidence_issued is False

    zero_run = historical_verified_zero_run()
    zero = assess_master_historical_execution_readiness(binding, observed_run=zero_run)
    assert zero.failure_interpreted_as_zero is False
    assert zero.inferred_count is None
    assert zero.specialist_result is None
    assert zero.finalization_complete is False


def test_supplied_final_result_stays_unauthenticated_and_distinct_from_the_run():
    result = replace(
        historical_unverified_passed_result(),
        evaluated_as_of_utc=APPLICATION_TIME,
    )
    assert result.verified_claims
    binding = _binding()
    assessment = assess_master_historical_execution_readiness(binding, observed_result=result)
    both = assess_master_historical_execution_readiness(
        binding,
        observed_run=historical_incomplete_coverage_run(),
        observed_result=result,
    )

    assert assessment.observed_final_result is True
    assert assessment.observed_intermediate_run is False
    assert assessment.specialist_result is None
    assert assessment.final_result_type == FINAL_RESULT_TYPE
    assert assessment.final_result_type != assessment.intermediate_run_type
    assert assessment.finalization_complete is False
    assert assessment.authenticated_source_evaluation_time_utc is None
    assert assessment.failure_interpreted_as_zero is False
    assert assessment.evidence_issued is False
    assert MasterAdmissionReason.HISTORICAL_RUN_NOT_FINAL_ARTIFACT not in assessment.correlation_reasons
    assert (
        MasterAdmissionReason.HISTORICAL_RUN_CANNOT_REPRODUCE_VERIFICATION
        in assessment.correlation_reasons
    )
    assert both.observed_intermediate_run is True
    assert both.observed_final_result is True
    assert both.historical_run is None
    assert both.specialist_result is None
    assert both.finalization_performed is False
    assert both.finalization_complete is False
    assert MasterAdmissionReason.HISTORICAL_RUN_NOT_FINAL_ARTIFACT in both.correlation_reasons
    assert both.authenticated_source_evaluation_time_utc is None
    assert both.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED


def test_run_and_final_result_types_are_not_interchangeable():
    binding = _binding()
    result = historical_unverified_passed_result()
    run = historical_incomplete_coverage_run()

    with pytest.raises(ContractValidationError, match="historical_run_not_final_artifact"):
        assess_master_historical_execution_readiness(binding, observed_run=result)
    with pytest.raises(ContractValidationError, match="historical_run_not_final_artifact"):
        assess_master_historical_execution_readiness(binding, observed_result=run)
    with pytest.raises(ContractValidationError, match="invalid_historical_run"):
        assess_master_historical_execution_readiness(binding, observed_run=object())
    with pytest.raises(ContractValidationError, match="invalid_specialist_result"):
        assess_master_historical_execution_readiness(binding, observed_result=object())
    with pytest.raises(ContractValidationError, match="invalid_historical_planning_facts"):
        assess_master_historical_execution_readiness(binding.planning)


def test_application_clock_and_wall_clock_are_refused_as_source_time():
    binding = _binding(requested_evaluation_time_utc=REQUESTED_EVALUATION_TIME)
    wall_clock = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with pytest.raises(ContractValidationError, match="evaluated_as_of_not_evidence"):
        assess_master_historical_execution_readiness(
            binding,
            proposed_source_evaluation_time_utc=APPLICATION_TIME,
        )
    with pytest.raises(ContractValidationError, match="evaluated_as_of_not_evidence"):
        assess_master_historical_execution_readiness(
            binding,
            proposed_source_evaluation_time_utc=REQUESTED_EVALUATION_TIME,
        )
    with pytest.raises(ContractValidationError, match="evaluated_as_of_not_evidence"):
        assess_master_historical_execution_readiness(
            binding,
            proposed_source_evaluation_time_utc=wall_clock,
        )

    assessment = assess_master_historical_execution_readiness(binding)
    assert assessment.authenticated_source_evaluation_time_utc is None
    assert assessment.application_clock_accepted_as_source_time is False
    assert assessment.wall_clock_accepted_as_source_time is False
    assert "datetime" not in readiness_module.__dict__
    assert not hasattr(readiness_module, "datetime")


def test_supplied_tool_window_does_not_bind_the_master_plan_window():
    binding = _binding()
    invocation = ExecutedToolInvocation(
        tool_name="summarize_historical_encounters",
        tool_call_id="historical-call-1",
        requested_arguments={"start_utc": "2020-01-01T00:00:00Z", "end_utc": "2020-01-02T00:00:00Z"},
        executed_arguments={"start_utc": "2020-01-01T00:00:00Z", "end_utc": "2020-01-02T00:00:00Z"},
        list_limit_default_applied=False,
        canonical_key="summarize-window",
    )
    run = HistoricalSpecialistRunResult(
        status=SpecialistRunStatus.PROVIDER_FAILED,
        tool_results=(),
        tool_result_projections=(),
        executed_invocations=(invocation,),
        proposed_claims=(),
        unsupported_reason=None,
        evaluated_as_of_utc=None,
        runtime_errors=("provider_exception",),
        provider_turn_count=1,
        executed_tool_call_count=1,
        validation_feedback=(),
    )
    assessment = assess_master_historical_execution_readiness(binding, observed_run=run)

    assert assessment.unbound_plan_window_start_utc == WINDOW_START
    assert assessment.unbound_plan_window_end_utc == WINDOW_END
    assert assessment.historical_window_bound_to_native_tool_arguments is False
    assert assessment.structured_request_arguments is None
    assert assessment.binding.specialist_request.text == TEXT
    assert assessment.finalization_complete is False
    assert assessment.executed_tool_call_count is None
    assert assessment.inferred_count is None


def test_collection_rejects_mismatched_identity_and_empty_input():
    original = _binding()
    other_master = _binding(master_request_id="master-req-hist-ready-2")
    other_correlation = _binding(correlation_id="corr-hist-ready-2")
    other_clock = _binding(application_request_time_utc="2026-01-16T00:00:01Z")

    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        assess_master_historical_execution_readiness_collection((original, other_master))
    with pytest.raises(ContractValidationError, match="correlation_mismatch"):
        assess_master_historical_execution_readiness_collection((original, other_correlation))
    with pytest.raises(ContractValidationError, match="application_time_mismatch"):
        assess_master_historical_execution_readiness_collection((original, other_clock))
    with pytest.raises(ContractValidationError, match="duplicate_invocation_id"):
        assess_master_historical_execution_readiness_collection((original, original))
    with pytest.raises(ContractValidationError, match="invocation_not_in_plan"):
        assess_master_historical_execution_readiness_collection(())


def test_closed_flags_cannot_be_opened():
    assessment = assess_master_historical_execution_readiness(_binding())
    forbidden = (
        ("dispatch_permitted", True, "execution_not_granted"),
        ("inferred_count", 0, "failure_not_verified_zero"),
        ("source_version", "source-v1", "verified_evidence_not_issued"),
        ("query_id", "query-1", "verified_evidence_not_issued"),
        ("query_result", {"rows": 0}, "verified_evidence_not_issued"),
        ("authenticated_source_evaluation_time_utc", APPLICATION_TIME, "evaluated_as_of_not_evidence"),
        ("application_clock_accepted_as_source_time", True, "evaluated_as_of_not_evidence"),
        ("wall_clock_accepted_as_source_time", True, "evaluated_as_of_not_evidence"),
        ("historical_window_bound_to_native_tool_arguments", True, "historical_window_not_authorized"),
        ("finalization_complete", True, "historical_finalization_not_performed"),
        ("evidence_issued", True, "verified_evidence_not_issued"),
        ("native_correlation_resolved", True, "request_id_not_correlation_proof"),
        ("failure_interpreted_as_zero", True, "failure_not_verified_zero"),
        ("incomplete_coverage_interpreted_as_zero", True, "incomplete_coverage_not_verified_zero"),
    )
    for field_name, value, code in forbidden:
        if code is None:
            continue
        with pytest.raises(ContractValidationError, match=code):
            replace(assessment, **{field_name: value})

    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        replace(assessment, production_authority=MasterProductionAuthority.PRODUCTION)
    with pytest.raises(ContractValidationError, match="canonical_user_text_mismatch"):
        assess_master_historical_execution_readiness(
            assessment.binding,
            observed_request=SpecialistRequest(text="A different question", request_id=assessment.specialist_request_id),
        )


def test_native_result_contracts_are_missing_correlation_fields():
    assessment = assess_master_historical_execution_readiness(_binding())
    gaps = _gaps(assessment)
    tool_missing = tuple(name for name in CORRELATION_FIELDS if name != "correlation_id")
    request_missing = tuple(name for name in CORRELATION_FIELDS if name != "request_id")

    assert gaps["HistoricalSpecialistRunResult"] == CORRELATION_FIELDS
    assert gaps["SpecialistResult"] == CORRELATION_FIELDS
    assert gaps["Evidence"] == CORRELATION_FIELDS
    assert gaps["HistoricalSpecialistTrustedContext"] == CORRELATION_FIELDS
    assert gaps["ToolResult"] == tool_missing
    assert gaps["SpecialistRequest"] == request_missing
    assert "correlation_id" in ToolResult.__dataclass_fields__
    assert "correlation_id" not in gaps["ToolResult"]
    assert "evaluated_as_of_utc" in HistoricalSpecialistRunResult.__dataclass_fields__
    assert "evaluated_as_of_utc" in SpecialistResult.__dataclass_fields__
    assert "evaluated_as_of_utc" not in gaps["HistoricalSpecialistRunResult"]
    assert "evaluated_as_of_utc" not in gaps["SpecialistResult"]
    assert assessment.evaluated_as_of_utc_is_authenticated_source_time is False
    assert assessment.source_version_owner == SOURCE_VERSION_OWNER
    assert assessment.query_id_owner == QUERY_ID_OWNER
    assert "source_version" in SourceRecord.__dataclass_fields__
    assert "query_id" in QueryExecutionTrace.__dataclass_fields__
    assert "source_version" not in SpecialistResult.__dataclass_fields__
    assert "query_id" not in HistoricalSpecialistRunResult.__dataclass_fields__
    assert historical_native_result_correlation_gaps() == assessment.correlation_gaps
    assert [item.contract_name for item in assessment.correlation_gaps] == [
        "HistoricalSpecialistRunResult",
        "SpecialistResult",
        "ToolResult",
        "Evidence",
        "SpecialistRequest",
        "HistoricalSpecialistTrustedContext",
    ]
    for contract in (
        HistoricalSpecialistRunResult,
        SpecialistResult,
        Evidence,
        HistoricalSpecialistTrustedContext,
    ):
        for name in CORRELATION_FIELDS:
            assert name not in {item.name for item in fields(contract)}
