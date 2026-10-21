"""Offline tests for deterministic Master routing. These tests do not execute specialists."""

from __future__ import annotations

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError, TemporalScope
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_routing import (
    MASTER_ROUTE_PROPOSAL_SCHEMA_VERSION,
    REQUEST_UNDERSTANDING_LIMITATION,
    MasterDeferredSynthesis,
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRouteHints,
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    MasterSynthesisFulfillment,
    authorize_route,
)


TEXT = "Which aircraft are currently affected by hazards, and what recorded risks apply?"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
RECOMMENDATION = AgentCapability.EXPLAIN_EXISTING_RECOMMENDATIONS
COMPARE = AgentCapability.COMPARE_VERIFIED_EVIDENCE
VISUAL = AgentCapability.GENERATE_VISUALIZATION_SPECIFICATIONS
ALTITUDE = AgentCapability.ISSUE_ALTITUDE_INSTRUCTIONS


def _context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "correlation_id": "corr-1",
        "application_request_time_utc": "2026-01-15T00:00:01Z",
        "canonical_user_text": TEXT,
    }
    values.update(overrides)
    return MasterPlanningContext(**values)  # type: ignore[arg-type]


def _work(*capabilities: AgentCapability, optional: AgentCapability | None = None) -> tuple:
    items = [
        MasterProposedCapability(item, MasterRequirement.REQUIRED) for item in capabilities
    ]
    if optional is not None:
        items.append(MasterProposedCapability(optional, MasterRequirement.OPTIONAL))
    return tuple(items)


def _step(
    step_id: str,
    specialist: MasterSpecialist,
    purpose: MasterPurpose,
    capability: AgentCapability,
    **overrides: object,
) -> MasterProposedStep:
    values: dict[str, object] = {
        "step_id": step_id,
        "specialist": specialist,
        "purpose": purpose,
        "requirement": MasterRequirement.REQUIRED,
        "capability": capability,
    }
    values.update(overrides)
    return MasterProposedStep(**values)  # type: ignore[arg-type]


def _proposal(
    capabilities: tuple[MasterProposedCapability, ...],
    steps: tuple[MasterProposedStep, ...] = (),
    user_text: str = TEXT,
) -> MasterRouteProposal:
    return MasterRouteProposal(user_text=user_text, capabilities=capabilities, steps=steps)


def _live(step_id: str = "live-1", **overrides: object) -> MasterProposedStep:
    return _step(
        step_id,
        MasterSpecialist.LIVE_OPS,
        MasterPurpose.ANSWER,
        LIVE,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
        **overrides,
    )


def _history(step_id: str = "hist-1", **overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "temporal_intent": MasterRouteTemporalIntent.HISTORICAL,
        "historical_start_utc": "2026-01-01T00:00:00Z",
        "historical_end_utc": "2026-01-02T00:00:00Z",
    }
    values.update(overrides)
    return _step(
        step_id,
        MasterSpecialist.HISTORICAL_ANALYTICS,
        MasterPurpose.ANSWER,
        HISTORY,
        **values,
    )


def _decision(step_id: str = "dec-1", **overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "capability": RISK,
        "temporal_intent": MasterRouteTemporalIntent.CURRENT,
    }
    values.update(overrides)
    capability = values.pop("capability")
    return _step(
        step_id,
        MasterSpecialist.DECISION,
        MasterPurpose.ANSWER,
        capability,  # type: ignore[arg-type]
        **values,
    )


def test_live_ops_only_is_bounded_and_not_executable_authority():
    decision = authorize_route(_proposal(_work(LIVE), (_live(),)), _context())
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert decision.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    plan = decision.plan
    assert plan is not None
    assert plan.end_to_end_fulfilled is False
    assert [item.invocation.specialist for item in plan.steps] == [MasterSpecialist.LIVE_OPS]
    assert plan.steps[0].ready_to_execute is True
    assert plan.steps[0].temporal_scopes == (TemporalScope.CURRENT,)
    assert plan.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_historical_only_keeps_the_explicit_window():
    decision = authorize_route(_proposal(_work(HISTORY), (_history(),)), _context())
    step = decision.plan.steps[0]
    assert step.invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    assert step.ready_to_execute is True
    assert step.temporal_scopes == (TemporalScope.HISTORICAL,)
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_decision_candidate_is_not_target_authorization():
    decision = authorize_route(
        _proposal(_work(RISK), (_decision(),)),
        _context(target_candidate_aircraft_id="ac-1"),
    )
    step = decision.plan.steps[0]
    assert isinstance(step.invocation, MasterInvocationAuthorization)
    assert step.invocation.expected_aircraft_id == "ac-1"
    assert step.invocation.expected_recommendation_id is None
    assert step.ready_to_execute is False
    assert step.target_authorization_granted is False
    assert MasterRouteGate.APPLICATION_TARGET_CANDIDATE_PRESENT in step.gates
    assert MasterRouteGate.TRUSTED_IDENTITY_VALIDATION_PENDING in step.gates
    assert MasterRouteGate.CURRENTNESS_VALIDATION_PENDING in step.gates
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_independent_live_ops_and_historical_branches_have_stable_order():
    forward = _proposal(_work(LIVE, HISTORY), (_live(), _history()))
    reverse = _proposal(_work(LIVE, HISTORY), (_history(), _live()))
    first = authorize_route(forward, _context())
    second = authorize_route(reverse, _context())
    assert first.plan.to_dict() == second.plan.to_dict()
    assert [item.invocation.specialist for item in first.plan.steps] == [
        MasterSpecialist.HISTORICAL_ANALYTICS,
        MasterSpecialist.LIVE_OPS,
    ]
    assert first.plan.steps[0].depends_on == ()
    assert first.plan.steps[1].depends_on == ()
    assert authorize_route(forward, _context()).plan.to_dict() == first.plan.to_dict()


def test_discovery_blocks_decision_without_minting_a_target():
    discovery = _step(
        "find-1",
        MasterSpecialist.LIVE_OPS,
        MasterPurpose.DISCOVERY,
        LIVE,
        hints=MasterRouteHints(callsign="UAL123"),
    )
    decision_step = _decision(
        "dec-1",
        depends_on=("find-1",),
        hints=MasterRouteHints(callsign="UAL123"),
    )
    decision = authorize_route(
        _proposal(_work(LIVE, RISK, COMPARE), (discovery, decision_step)),
        _context(),
    )
    plan = decision.plan
    assert plan is not None
    assert [item.step_id for item in plan.steps] == ["find-1", "dec-1"]
    gated = plan.steps[1]
    assert gated.ready_to_execute is False
    assert gated.invocation.expected_aircraft_id is None
    assert MasterRouteGate.DEPENDENCY_BLOCKED in gated.gates
    assert MasterRouteGate.IDENTITY_DISCOVERY_NEEDED in gated.gates
    assert gated.target_authorization_granted is False
    assert plan.deferred_synthesis[0].capability is COMPARE
    assert plan.deferred_synthesis[0].state.value == "NON_EXECUTABLE"
    assert plan.deferred_synthesis[0].fulfillment is MasterSynthesisFulfillment.NOT_YET_FULFILLED
    assert plan.deferred_synthesis[0].executable is False
    assert plan.end_to_end_fulfilled is False


def test_deferred_synthesis_cannot_be_reported_completed():
    decision = authorize_route(
        _proposal(_work(LIVE, COMPARE), (_live(),)),
        _context(),
    )
    payload = decision.plan.to_dict()
    payload["end_to_end_fulfilled"] = True
    with pytest.raises(ContractValidationError, match="end_to_end_fulfillment_not_granted"):
        type(decision.plan).from_dict(payload)
    payload = decision.plan.to_dict()
    payload["deferred_synthesis"][0]["fulfillment"] = "FULFILLED"
    with pytest.raises(ContractValidationError, match="synthesis_not_fulfilled"):
        type(decision.plan).from_dict(payload)
    with pytest.raises(ContractValidationError, match="synthesis_not_fulfilled"):
        MasterDeferredSynthesis(
            capability=COMPARE,
            requirement=MasterRequirement.REQUIRED,
            fulfillment="FULFILLED",  # type: ignore[arg-type]
        )


def test_substituted_user_text_is_rejected():
    proposal = _proposal(_work(LIVE), (_live(),), user_text="A different question.")
    with pytest.raises(ContractValidationError, match="user_text_mismatch"):
        authorize_route(proposal, _context())


def test_omitted_capability_does_not_certify_natural_language_coverage():
    decision = authorize_route(_proposal(_work(LIVE), (_live(),)), _context())
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert decision.understanding_limitation == REQUEST_UNDERSTANDING_LIMITATION
    assert decision.plan.end_to_end_fulfilled is False
    assert all(item.invocation.specialist is not MasterSpecialist.DECISION for item in decision.plan.steps)
    assert decision.plan.deferred_synthesis == ()


def test_california_hint_stays_unvalidated_and_non_executable():
    step = _live(hints=MasterRouteHints(region_text="California"))
    decision = authorize_route(_proposal(_work(LIVE), (step,)), _context())
    planned = decision.plan.steps[0]
    assert planned.region_text == "California"
    assert planned.ready_to_execute is False
    assert MasterRouteGate.REGION_VALIDATION_PENDING in planned.gates
    rendered = str(decision.plan.to_dict())
    assert "polygon" not in rendered.casefold()
    assert "coordinate" not in rendered.casefold()
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_conflicting_region_hints_require_clarification():
    steps = (
        _live("live-1", hints=MasterRouteHints(region_text="California")),
        _live("live-2", hints=MasterRouteHints(region_text="Nevada")),
    )
    decision = authorize_route(_proposal(_work(LIVE), steps), _context())
    assert decision.outcome is MasterRouteOutcome.CLARIFICATION_REQUIRED
    assert decision.reasons == ("ambiguous_geographic_scope",)
    assert decision.plan is None


def test_direct_persisted_candidate_does_not_authorize_execution():
    decision = authorize_route(
        _proposal(_work(RECOMMENDATION), (_decision(capability=RECOMMENDATION),)),
        _context(target_candidate_recommendation_id="rec-1"),
    )
    step = decision.plan.steps[0]
    assert step.invocation.expected_recommendation_id == "rec-1"
    assert step.invocation.expected_aircraft_id is None
    assert step.ready_to_execute is False
    assert step.target_authorization_granted is False
    assert MasterRouteGate.APPLICATION_TARGET_CANDIDATE_PRESENT in step.gates


def test_typed_aircraft_and_callsign_hints_cannot_mint_a_target():
    typed = authorize_route(
        _proposal(
            _work(RISK),
            (_decision(hints=MasterRouteHints(typed_aircraft_id="ac-1")),),
        ),
        _context(),
    ).plan.steps[0]
    callsign = authorize_route(
        _proposal(
            _work(RISK),
            (_decision(hints=MasterRouteHints(callsign="UAL123")),),
        ),
        _context(),
    ).plan.steps[0]
    assert typed.invocation.expected_aircraft_id is None
    assert typed.ready_to_execute is False
    assert MasterRouteGate.CURRENTNESS_VALIDATION_PENDING in typed.gates
    assert callsign.invocation.expected_aircraft_id is None
    assert MasterRouteGate.IDENTITY_DISCOVERY_NEEDED in callsign.gates
    assert callsign.target_authorization_granted is False


def test_population_cannot_fan_out_decision_steps():
    hints = MasterRouteHints(population_aircraft_ids=("ac-1", "ac-2", "ac-3"))
    decision = authorize_route(
        _proposal(_work(RISK), (_decision(hints=hints),)),
        _context(),
    )
    assert len(decision.plan.steps) == 1
    assert MasterRouteGate.TARGET_AMBIGUOUS in decision.plan.steps[0].gates
    assert decision.plan.steps[0].ready_to_execute is False
    with pytest.raises(ContractValidationError, match="decision_fanout_not_permitted"):
        _proposal(_work(RISK), (_decision("dec-1"), _decision("dec-2")))


def test_historical_window_over_seven_days_is_not_truncated():
    step = _history(
        historical_start_utc="2026-01-01T00:00:00Z",
        historical_end_utc="2026-01-09T00:00:00Z",
    )
    decision = authorize_route(_proposal(_work(HISTORY), (step,)), _context())
    assert decision.outcome is MasterRouteOutcome.UNSUPPORTED
    assert decision.reasons == ("historical_window_exceeds_seven_days",)
    assert decision.plan is None
    assert "2026-01-08T00:00:00Z" not in decision.reasons


def test_exact_seven_day_historical_window_remains_supported():
    step = _history(
        historical_start_utc="2026-01-01T00:00:00Z",
        historical_end_utc="2026-01-08T00:00:00Z",
    )
    decision = authorize_route(_proposal(_work(HISTORY), (step,)), _context())
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan.steps[0].ready_to_execute is True


def test_missing_and_contradictory_temporal_inputs_fail_safely():
    missing = authorize_route(
        _proposal(_work(HISTORY), (_history(historical_start_utc=None, historical_end_utc=None),)),
        _context(),
    )
    contradictory = authorize_route(
        _proposal(
            _work(HISTORY),
            (
                _history(
                    historical_start_utc="2026-01-02T00:00:00Z",
                    historical_end_utc="2026-01-01T00:00:00Z",
                ),
            ),
        ),
        _context(),
    )
    assert missing.outcome is MasterRouteOutcome.CLARIFICATION_REQUIRED
    assert missing.reasons == ("missing_historical_interval",)
    assert contradictory.outcome is MasterRouteOutcome.CLARIFICATION_REQUIRED
    assert contradictory.reasons == ("contradictory_temporal_bounds",)
    assert missing.plan is None
    assert contradictory.plan is None


def test_cycles_missing_dependencies_duplicates_and_excessive_steps_are_rejected():
    with pytest.raises(ContractValidationError, match="cyclic_dependency"):
        _proposal(
            _work(LIVE),
            (
                _live("live-1", depends_on=("live-2",)),
                _live("live-2", depends_on=("live-1",)),
            ),
        )
    with pytest.raises(ContractValidationError, match="self_dependency"):
        _live(depends_on=("live-1",))
    with pytest.raises(ContractValidationError, match="missing_dependency_id"):
        _proposal(_work(LIVE), (_live(depends_on=("missing",)),))
    with pytest.raises(ContractValidationError, match="duplicate_step_id"):
        _proposal(_work(LIVE), (_live("live-1"), _live("live-1")))
    with pytest.raises(ContractValidationError, match="excessive_plan_size"):
        _proposal(
            _work(LIVE),
            tuple(_live(f"live-{index}") for index in range(5)),
        )


def test_duplicate_invocation_ids_are_rejected_on_the_plan():
    decision = authorize_route(_proposal(_work(LIVE, HISTORY), (_live(), _history())), _context())
    payload = decision.plan.to_dict()
    payload["steps"][1]["invocation"]["invocation_id"] = payload["steps"][0]["invocation"]["invocation_id"]
    with pytest.raises(ContractValidationError, match="duplicate_invocation_id"):
        type(decision.plan).from_dict(payload)


def test_prohibited_capability_refuses_the_whole_proposal():
    decision = authorize_route(
        _proposal(_work(RECOMMENDATION, ALTITUDE), (_decision(capability=RECOMMENDATION),)),
        _context(target_candidate_aircraft_id="ac-1"),
    )
    assert decision.outcome is MasterRouteOutcome.REFUSED
    assert decision.plan is None
    assert decision.reasons == (f"prohibited_capability:{ALTITUDE.value}",)
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_informational_recommendation_explanation_is_not_a_refusal():
    decision = authorize_route(
        _proposal(_work(RECOMMENDATION), (_decision(capability=RECOMMENDATION),)),
        _context(target_candidate_aircraft_id="ac-1"),
    )
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan.steps[0].capability is RECOMMENDATION
    assert decision.plan.steps[0].ready_to_execute is False
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_serialized_plan_cannot_restore_execution_authority():
    decision = authorize_route(_proposal(_work(LIVE), (_live(),)), _context())
    payload = decision.to_dict()
    payload["execution_authority"] = "PRODUCTION"
    payload["plan"]["execution_authority"] = "PRODUCTION"
    payload["request_coverage"] = "ESTABLISHED"
    restored = type(decision).from_dict(payload)
    assert restored.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert restored.plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert restored.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert restored.plan.end_to_end_fulfilled is False


def test_invalid_schema_version_is_rejected():
    with pytest.raises(ContractValidationError, match="invalid_schema_version"):
        MasterRouteProposal(
            user_text=TEXT,
            capabilities=_work(LIVE),
            steps=(_live(),),
            schema_version="wilvor.ai.master_route_proposal.v0",
        )
    assert MASTER_ROUTE_PROPOSAL_SCHEMA_VERSION == "wilvor.ai.master_route_proposal.v1"


def test_required_and_optional_unsupported_work_stay_distinct():
    optional = authorize_route(
        _proposal(_work(LIVE, optional=VISUAL), (_live(),)),
        _context(),
    )
    assert optional.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert optional.plan.unsupported_omissions == (
        f"unsupported_capability:{VISUAL.value}",
    )
    assert optional.plan.steps[0].invocation.specialist is MasterSpecialist.LIVE_OPS
    required = authorize_route(
        _proposal(_work(LIVE, VISUAL), (_live(),)),
        _context(),
    )
    assert required.outcome is MasterRouteOutcome.UNSUPPORTED
    assert required.plan is None
    historical_discovery = _step(
        "hist-find",
        MasterSpecialist.HISTORICAL_ANALYTICS,
        MasterPurpose.DISCOVERY,
        HISTORY,
    )
    unsupported = authorize_route(
        _proposal(_work(HISTORY), (historical_discovery,)),
        _context(),
    )
    assert unsupported.outcome is MasterRouteOutcome.UNSUPPORTED
    assert unsupported.reasons == ("unsupported_specialist_purpose",)


def test_historical_and_decision_remain_independent_when_each_is_supported():
    decision = authorize_route(
        _proposal(
            _work(HISTORY, RISK),
            (_history(), _decision(needs_persisted_evidence=True)),
        ),
        _context(target_candidate_aircraft_id="ac-1"),
    )
    plan = decision.plan
    assert [item.invocation.specialist for item in plan.steps] == [
        MasterSpecialist.DECISION,
        MasterSpecialist.HISTORICAL_ANALYTICS,
    ]
    assert plan.steps[0].temporal_scopes == (TemporalScope.CURRENT, TemporalScope.PERSISTED)
    assert TemporalScope.HYBRID not in plan.steps[0].temporal_scopes
    assert plan.steps[0].ready_to_execute is False
    assert plan.steps[1].ready_to_execute is True
    assert plan.steps[0].depends_on == ()
    assert plan.steps[1].depends_on == ()
    assert plan.end_to_end_fulfilled is False


def test_plan_authorizations_round_trip_through_ma1_contracts():
    decision = authorize_route(_proposal(_work(LIVE), (_live(),)), _context())
    invocation = decision.plan.steps[0].invocation
    assert MasterInvocationAuthorization.from_dict(invocation.to_dict()) == invocation
    restored = type(decision.plan).from_dict(decision.plan.to_dict())
    assert restored == decision.plan
    assert restored.execution_authority is MasterRouteExecutionAuthority.DENIED
