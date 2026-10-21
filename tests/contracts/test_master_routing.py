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
    MasterHintTrust,
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
    assert step.historical_start_utc == "2026-01-01T00:00:00Z"
    assert step.historical_end_utc == "2026-01-02T00:00:00Z"
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
    assert plan.steps[0].untrusted_hints.callsign == "UAL123"
    assert plan.steps[0].hint_trust is MasterHintTrust.UNTRUSTED
    assert plan.steps[0].invocation.expected_aircraft_id is None
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
    assert planned.untrusted_hints.region_text == "California"
    assert planned.hint_trust is MasterHintTrust.UNTRUSTED
    assert planned.invocation.expected_aircraft_id is None
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
    assert step.temporal_scopes == (TemporalScope.PERSISTED,)
    assert TemporalScope.CURRENT not in step.temporal_scopes
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
    assert invocation.master_request_id == "master-req-1"
    assert invocation.invocation_id == "master-req-1-1"
    assert invocation.specialist_request_id == "master-req-1-specialist-1"
    assert invocation.correlation_id == "corr-1"
    assert len(
        {
            invocation.master_request_id,
            invocation.invocation_id,
            invocation.specialist_request_id,
            invocation.correlation_id,
        }
    ) == 4
    assert MasterInvocationAuthorization.from_dict(invocation.to_dict()) == invocation
    restored = type(decision.plan).from_dict(decision.plan.to_dict())
    assert restored == decision.plan
    assert restored.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_historical_window_and_untrusted_hints_survive_round_trip():
    hints = MasterRouteHints(
        callsign="UAL123",
        typed_aircraft_id="ac-9",
        population_aircraft_ids=("ac-9", "ac-8"),
    )
    decision = authorize_route(
        _proposal(_work(HISTORY), (_history(hints=hints),)),
        _context(),
    )
    step = decision.plan.steps[0]
    assert step.historical_start_utc == "2026-01-01T00:00:00Z"
    assert step.historical_end_utc == "2026-01-02T00:00:00Z"
    assert step.untrusted_hints == hints
    assert step.hint_trust is MasterHintTrust.UNTRUSTED
    assert step.invocation.expected_aircraft_id is None
    assert step.target_authorization_granted is False
    restored = type(decision.plan).from_dict(decision.plan.to_dict())
    assert restored == decision.plan
    assert restored.steps[0].historical_end_utc == "2026-01-02T00:00:00Z"
    assert restored.steps[0].untrusted_hints.callsign == "UAL123"
    assert restored.steps[0].untrusted_hints.typed_aircraft_id == "ac-9"
    assert restored.execution_authority is MasterRouteExecutionAuthority.DENIED
    trusted = decision.plan.to_dict()
    trusted["steps"][0]["hint_trust"] = "TRUSTED"
    with pytest.raises(ContractValidationError, match="selection_hints_not_trusted"):
        type(decision.plan).from_dict(trusted)
    dropped = decision.plan.to_dict()
    del dropped["steps"][0]["historical_end_utc"]
    with pytest.raises(ContractValidationError, match="missing_historical_interval"):
        type(decision.plan).from_dict(dropped)
    widened = decision.plan.to_dict()
    widened["steps"][0]["historical_end_utc"] = "2026-01-09T00:00:00Z"
    with pytest.raises(ContractValidationError, match="historical_window_exceeds_seven_days"):
        type(decision.plan).from_dict(widened)
    smuggled = decision.plan.to_dict()
    smuggled["steps"][0]["tool_arguments"] = {"polygon": []}
    with pytest.raises(ContractValidationError, match="unexpected_route_step_plan_field"):
        type(decision.plan).from_dict(smuggled)


def test_dependencies_block_every_specialist_and_keep_stable_order():
    live_on_historical = _proposal(
        _work(LIVE, HISTORY),
        (_history("hist-1"), _live("live-1", depends_on=("hist-1",))),
    )
    swapped = _proposal(
        _work(LIVE, HISTORY),
        (_live("live-1", depends_on=("hist-1",)), _history("hist-1")),
    )
    first = authorize_route(live_on_historical, _context())
    assert first.plan.to_dict() == authorize_route(swapped, _context()).plan.to_dict()
    assert [item.step_id for item in first.plan.steps] == ["hist-1", "live-1"]
    assert first.plan.steps[0].ready_to_execute is True
    assert first.plan.steps[0].depends_on == ()
    assert first.plan.steps[1].ready_to_execute is False
    assert first.plan.steps[1].depends_on == ("hist-1",)
    assert MasterRouteGate.DEPENDENCY_BLOCKED in first.plan.steps[1].gates
    historical_on_live = authorize_route(
        _proposal(
            _work(LIVE, HISTORY),
            (_live("live-1"), _history("hist-1", depends_on=("live-1",))),
        ),
        _context(),
    )
    assert [item.step_id for item in historical_on_live.plan.steps] == ["live-1", "hist-1"]
    assert historical_on_live.plan.steps[0].ready_to_execute is True
    assert historical_on_live.plan.steps[1].ready_to_execute is False
    assert historical_on_live.plan.steps[1].depends_on == ("live-1",)
    assert MasterRouteGate.DEPENDENCY_BLOCKED in historical_on_live.plan.steps[1].gates
    assert historical_on_live.plan.steps[1].historical_start_utc == "2026-01-01T00:00:00Z"
    assert historical_on_live.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_omitted_optional_dependencies_do_not_leave_a_dangling_plan():
    optional_history = _step(
        "hist-x",
        MasterSpecialist.HISTORICAL_ANALYTICS,
        MasterPurpose.DISCOVERY,
        HISTORY,
        requirement=MasterRequirement.OPTIONAL,
    )
    optional_live = _live(
        "live-2",
        requirement=MasterRequirement.OPTIONAL,
        depends_on=("hist-x",),
    )
    kept_live = _live("live-1")
    decision = authorize_route(
        _proposal(
            (
                MasterProposedCapability(LIVE, MasterRequirement.REQUIRED),
                MasterProposedCapability(HISTORY, MasterRequirement.OPTIONAL),
            ),
            (optional_history, optional_live, kept_live),
        ),
        _context(),
    )
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert [item.step_id for item in decision.plan.steps] == ["live-1"]
    assert decision.plan.steps[0].depends_on == ()
    assert decision.plan.steps[0].ready_to_execute is True
    assert "optional_step_omitted:live-2:dependency_omitted:hist-x" in decision.plan.unsupported_omissions
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_required_dependency_on_an_omitted_step_fails_closed():
    optional_history = _step(
        "hist-x",
        MasterSpecialist.HISTORICAL_ANALYTICS,
        MasterPurpose.DISCOVERY,
        HISTORY,
        requirement=MasterRequirement.OPTIONAL,
    )
    optional_live = _live(
        "live-2",
        requirement=MasterRequirement.OPTIONAL,
        depends_on=("hist-x",),
    )
    required_decision = _decision("dec-1", depends_on=("live-2",))
    decision = authorize_route(
        _proposal(
            (
                MasterProposedCapability(RISK, MasterRequirement.REQUIRED),
                MasterProposedCapability(LIVE, MasterRequirement.OPTIONAL),
                MasterProposedCapability(HISTORY, MasterRequirement.OPTIONAL),
            ),
            (optional_history, optional_live, required_decision),
        ),
        _context(),
    )
    assert decision.outcome is MasterRouteOutcome.UNSUPPORTED
    assert decision.plan is None
    assert decision.reasons == ("required_dependency_omitted:dec-1:live-2",)
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_planned_specialist_request_ids_remain_distinct_planning_identifiers():
    decision = authorize_route(
        _proposal(_work(LIVE, HISTORY), (_history(), _live())),
        _context(),
    )
    seen: set[str] = set()
    for index, step in enumerate(decision.plan.steps, start=1):
        invocation = step.invocation
        assert invocation.specialist_request_id == f"master-req-1-specialist-{index}"
        assert invocation.invocation_id == f"master-req-1-{index}"
        assert invocation.master_request_id == "master-req-1"
        assert invocation.correlation_id == "corr-1"
        assert invocation.specialist_request_id not in seen
        seen.add(invocation.specialist_request_id)
        restored = MasterInvocationAuthorization.from_dict(invocation.to_dict())
        assert restored.specialist_request_id == invocation.specialist_request_id
    collided = decision.plan.to_dict()
    collided["steps"][1]["invocation"]["specialist_request_id"] = collided["steps"][0]["invocation"][
        "specialist_request_id"
    ]
    with pytest.raises(ContractValidationError, match="duplicate_specialist_request_id"):
        type(decision.plan).from_dict(collided)
    merged = decision.plan.to_dict()
    merged["steps"][0]["invocation"]["specialist_request_id"] = merged["steps"][0]["invocation"][
        "invocation_id"
    ]
    with pytest.raises(ContractValidationError, match="request_identity_not_distinct"):
        type(decision.plan).from_dict(merged)


def test_unresolved_decision_scope_is_not_invented_as_current():
    decision = authorize_route(
        _proposal(
            _work(RISK),
            (_decision(temporal_intent=MasterRouteTemporalIntent.UNSPECIFIED),),
        ),
        _context(),
    )
    step = decision.plan.steps[0]
    assert step.temporal_scopes == ()
    assert MasterRouteGate.TEMPORAL_SCOPE_UNRESOLVED in step.gates
    assert TemporalScope.CURRENT not in step.temporal_scopes
    assert step.ready_to_execute is False
    assert step.invocation.expected_aircraft_id is None
    assert step.target_authorization_granted is False


def test_persisted_only_scope_does_not_invent_current_evidence():
    decision = authorize_route(
        _proposal(
            _work(RECOMMENDATION),
            (
                _decision(
                    capability=RECOMMENDATION,
                    temporal_intent=MasterRouteTemporalIntent.UNSPECIFIED,
                    needs_persisted_evidence=True,
                ),
            ),
        ),
        _context(),
    )
    step = decision.plan.steps[0]
    assert step.temporal_scopes == (TemporalScope.PERSISTED,)
    assert TemporalScope.CURRENT not in step.temporal_scopes
    assert step.needs_persisted_evidence is True
    assert step.invocation.expected_recommendation_id is None
    assert step.ready_to_execute is False


def test_recommendation_hint_does_not_mint_a_persisted_target():
    hints = MasterRouteHints(recommendation_id="rec-9")
    decision = authorize_route(
        _proposal(
            _work(RECOMMENDATION),
            (_decision(capability=RECOMMENDATION, hints=hints),),
        ),
        _context(),
    )
    step = decision.plan.steps[0]
    assert step.temporal_scopes == (TemporalScope.PERSISTED,)
    assert step.untrusted_hints.recommendation_id == "rec-9"
    assert step.hint_trust is MasterHintTrust.UNTRUSTED
    assert step.invocation.expected_recommendation_id is None
    assert step.target_authorization_granted is False
    assert step.ready_to_execute is False


def test_needs_persisted_evidence_rejects_truthiness_coercion():
    payload = _decision(needs_persisted_evidence=True).to_dict()
    for value in ("true", "false", 1, 0, ["persisted"]):
        payload["needs_persisted_evidence"] = value
        with pytest.raises(ContractValidationError, match="invalid_needs_persisted_evidence"):
            MasterProposedStep.from_dict(payload)
    decision = authorize_route(
        _proposal(_work(RISK), (_decision(needs_persisted_evidence=True),)),
        _context(target_candidate_aircraft_id="ac-1"),
    )
    planned = decision.plan.to_dict()
    planned["steps"][0]["needs_persisted_evidence"] = "yes"
    with pytest.raises(ContractValidationError, match="invalid_needs_persisted_evidence"):
        type(decision.plan).from_dict(planned)
    planned = decision.plan.to_dict()
    planned["steps"][0]["temporal_scopes"] = ["CURRENT"]
    with pytest.raises(ContractValidationError, match="persisted_scope_missing"):
        type(decision.plan).from_dict(planned)


def test_population_hint_is_bounded():
    with pytest.raises(ContractValidationError, match="population_aircraft_ids_exceed_bound"):
        MasterRouteHints(population_aircraft_ids=tuple(f"ac-{index}" for index in range(9)))


def test_serialized_plan_rejects_malformed_dependency_graphs():
    decision = authorize_route(
        _proposal(_work(LIVE, HISTORY), (_live(), _history())),
        _context(),
    )
    missing = decision.plan.to_dict()
    missing["steps"][1]["depends_on"] = ["missing-step"]
    missing["steps"][1]["gates"] = [MasterRouteGate.DEPENDENCY_BLOCKED.value]
    missing["steps"][1]["ready_to_execute"] = False
    with pytest.raises(ContractValidationError, match="missing_dependency_id"):
        type(decision.plan).from_dict(missing)
    ready = decision.plan.to_dict()
    ready["steps"][1]["depends_on"] = [ready["steps"][0]["step_id"]]
    with pytest.raises(ContractValidationError, match="dependency_not_blocked"):
        type(decision.plan).from_dict(ready)
    cyclic = decision.plan.to_dict()
    first_id = cyclic["steps"][0]["step_id"]
    second_id = cyclic["steps"][1]["step_id"]
    for item, dependency in ((0, second_id), (1, first_id)):
        cyclic["steps"][item]["depends_on"] = [dependency]
        cyclic["steps"][item]["gates"] = [MasterRouteGate.DEPENDENCY_BLOCKED.value]
        cyclic["steps"][item]["ready_to_execute"] = False
    with pytest.raises(ContractValidationError, match="cyclic_dependency"):
        type(decision.plan).from_dict(cyclic)
    self_dependent = decision.plan.to_dict()
    self_dependent["steps"][0]["depends_on"] = [self_dependent["steps"][0]["step_id"]]
    self_dependent["steps"][0]["gates"] = [MasterRouteGate.DEPENDENCY_BLOCKED.value]
    self_dependent["steps"][0]["ready_to_execute"] = False
    with pytest.raises(ContractValidationError, match="self_dependency"):
        type(decision.plan).from_dict(self_dependent)
