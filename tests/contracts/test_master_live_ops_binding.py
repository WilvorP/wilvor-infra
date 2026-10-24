"""Offline Master-to-Live-Ops binding tests. The binder does not run a specialist."""

from __future__ import annotations

import inspect
from dataclasses import fields, replace
from pathlib import Path

import pytest

from tests.fixtures.ma4_specialist_evidence_examples import (
    live_ops_all_verified_result,
    live_ops_context,
    live_ops_request,
)
from wilvor_ai.contracts import AgentCapability, ContractValidationError, TemporalScope
from wilvor_ai.live_ops_public_contracts import (
    LiveOpsEntityHint,
    LiveOpsPublicVerificationOutcome,
    LiveOpsSelectorHints,
    LiveOpsTrustedContext,
)
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
from wilvor_ai.master_execution_preflight import evaluate_execution_preflight
from wilvor_ai.master_live_ops_binding import (
    MasterLiveOpsBinding,
    MasterLiveOpsBindingLimitation,
    binding_limitations,
    prepare_master_live_ops_binding,
)
from wilvor_ai.master_parallel_contracts import (
    MasterExecutionPreflightState,
    MasterParallelContractBundle,
)
from wilvor_ai.master_routing import (
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
    authorize_route,
)
from wilvor_ai.master_specialist_adapters import (
    MasterAdapterExecutionState,
    describe_planned_adapters,
)


TEXT = "Which aircraft are currently affected by hazards, and what recorded history applies?"
MENTION_TEXT = "Which aircraft near California with callsign UAL123 are affected right now?"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
MODULE = Path(__file__).resolve().parents[2] / "functions" / "shared" / "wilvor_ai" / "master_live_ops_binding.py"

_STANDING_LIMITATIONS = (
    MasterLiveOpsBindingLimitation.EXECUTION_DENIED,
    MasterLiveOpsBindingLimitation.PRODUCTION_AUTHORITY_DENIED,
    MasterLiveOpsBindingLimitation.SOURCE_AUTHENTICITY_NOT_ESTABLISHED,
    MasterLiveOpsBindingLimitation.TRUSTED_RUNTIME_CONTEXT_ABSENT,
    MasterLiveOpsBindingLimitation.SELECTION_AND_ENTITY_HINTS_UNTRUSTED,
    MasterLiveOpsBindingLimitation.NO_AUTHORITATIVE_AIRCRAFT_IMPACT_COUNT,
    MasterLiveOpsBindingLimitation.VERIFIED_ZERO_NOT_ESTABLISHED,
    MasterLiveOpsBindingLimitation.LIVE_OPS_REFS_NOT_TOOL_RESULTS,
    MasterLiveOpsBindingLimitation.ADMISSION_SOURCES_NOT_CREATED,
)


def _context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-live-bind-1",
        "correlation_id": "corr-live-bind-1",
        "application_request_time_utc": "2026-01-15T00:00:01Z",
        "canonical_user_text": TEXT,
    }
    values.update(overrides)
    return MasterPlanningContext(**values)  # type: ignore[arg-type]


def _proposal(*steps: MasterProposedStep, user_text: str = TEXT) -> MasterRouteProposal:
    capabilities = tuple(
        dict.fromkeys(
            MasterProposedCapability(step.capability, step.requirement) for step in steps
        )
    )
    return MasterRouteProposal(user_text=user_text, capabilities=capabilities, steps=steps)


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


def _bundle(
    *steps: MasterProposedStep,
    user_text: str = TEXT,
    evidence: tuple[MasterEvidenceCorroborationInput, ...] = (),
) -> MasterParallelContractBundle:
    context = _context(canonical_user_text=user_text)
    decision = authorize_route(_proposal(*steps, user_text=user_text), context)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    assert decision.plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    return MasterParallelContractBundle(context=context, plan=decision.plan, evidence_inputs=evidence)


def _descriptor(bundle: MasterParallelContractBundle, step_id: str):
    described = describe_planned_adapters(bundle)
    matches = [item for item in described.descriptors if item.step_id == step_id]
    assert len(matches) == 1
    return matches[0]


def _assert_closed(binding: MasterLiveOpsBinding) -> None:
    assert binding.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert binding.production_authority is MasterProductionAuthority.DENIED
    assert binding.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert binding.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert binding.end_to_end_fulfilled is False
    assert binding.dispatch_permitted is False
    assert binding.executable is False
    assert binding.stops_before_execution_authorization is True
    assert binding.trusted_context_created is False
    assert binding.selectors_authorized is False
    assert binding.callsign_authenticated is False
    assert binding.live_ops_refs_are_tool_results is False
    assert binding.admission_sources_created is False
    assert binding.aircraft_impact_count is None
    assert binding.verified_zero is False
    assert binding.hint_trust is MasterHintTrust.UNTRUSTED
    assert binding.specialist is MasterSpecialist.LIVE_OPS
    assert binding.capability is LIVE
    assert binding.temporal_scopes == (TemporalScope.CURRENT,)
    assert callable(binding) is False
    assert not hasattr(binding, "run")
    assert not hasattr(binding, "dispatch")
    names = set(MasterLiveOpsBinding.__dataclass_fields__)
    assert "tables" not in names
    assert "trusted_context" not in names
    assert "dispatch" not in names
    assert "verification_outcome" not in names
    assert "bundle" not in names
    assert "evidence_inputs" not in names


def test_correct_live_ops_plan_binding():
    bundle = _bundle(_live(), _history())
    descriptor = _descriptor(bundle, "live-1")
    binding = prepare_master_live_ops_binding(bundle, descriptor)
    step = next(item for item in bundle.plan.steps if item.step_id == "live-1")

    assert binding.step_id == "live-1"
    assert binding.invocation_id == step.invocation.invocation_id
    assert binding.specialist_request_id == step.invocation.specialist_request_id
    assert binding.purpose is MasterPurpose.ANSWER
    assert binding.requirement is MasterRequirement.REQUIRED
    assert binding.master_request_id == bundle.context.master_request_id
    assert binding.correlation_id == bundle.context.correlation_id
    assert binding.application_request_time_utc == bundle.context.application_request_time_utc
    assert binding.structural_ready_to_execute is step.ready_to_execute
    assert binding.gates == step.gates
    assert binding.limitations == _STANDING_LIMITATIONS
    assert binding.request.selector_hints is None
    assert binding.request.entity_hints == ()
    assert binding.withheld_expected_aircraft_id is None
    assert binding.withheld_expected_recommendation_id is None
    assert binding.planning_context is bundle.context
    assert binding.descriptor is descriptor
    assert binding.planned_step is step
    _assert_closed(binding)
    assert (
        descriptor.execution_state
        is MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
    )


def test_planned_request_identity_is_preserved_exactly():
    bundle = _bundle(_live("live-west"), _live("live-east"))
    west = _descriptor(bundle, "live-west")
    east = _descriptor(bundle, "live-east")
    bound_west = prepare_master_live_ops_binding(bundle, west)
    bound_east = prepare_master_live_ops_binding(bundle, east)
    west_step = next(item for item in bundle.plan.steps if item.step_id == "live-west")
    east_step = next(item for item in bundle.plan.steps if item.step_id == "live-east")

    assert bound_west.request.user_text == TEXT
    assert bound_west.request.user_text == bundle.plan.canonical_user_text
    assert bound_west.canonical_user_text == TEXT
    assert bound_west.request.request_id == west_step.invocation.specialist_request_id
    assert bound_west.specialist_request_id == west.specialist_request_id
    assert bound_west.invocation_id == west.invocation_id
    assert bound_west.request.request_id != bound_west.master_request_id
    assert bound_west.request.request_id != bound_west.invocation_id
    assert bound_west.request.request_id != bound_west.correlation_id
    assert bound_west.request.conversation_id is None
    assert bound_west.request.prior_turns == ()
    assert bound_east.step_id == "live-east"
    assert bound_east.specialist_request_id == east_step.invocation.specialist_request_id
    assert bound_east.specialist_request_id != bound_west.specialist_request_id
    assert bound_east.invocation_id != bound_west.invocation_id
    assert bound_west.planned_step is west_step
    assert bound_east.planned_step is east_step
    assert bound_west.planning_context is bundle.context
    assert bound_east.planning_context is bundle.context
    assert bound_west.descriptor is west
    assert bound_east.descriptor is east
    assert prepare_master_live_ops_binding(bundle, west) == bound_west
    with pytest.raises(ContractValidationError, match="invocation_binding_mismatch"):
        replace(bound_west, invocation_id=east_step.invocation.invocation_id)
    with pytest.raises(ContractValidationError, match="invocation_binding_mismatch"):
        replace(bound_west, planned_step=east_step)
    with pytest.raises(ContractValidationError, match="invocation_binding_mismatch"):
        replace(bound_west, descriptor=east)


def test_replaced_planned_identity_is_rejected():
    bundle = _bundle(_live())
    binding = prepare_master_live_ops_binding(bundle, _descriptor(bundle, "live-1"))
    assert binding.structural_ready_to_execute is True
    assert binding.gates == (MasterRouteGate.NOT_REQUIRED,)
    cases = (
        ("master_request_id", "other-master-request", "master_request_mismatch"),
        ("correlation_id", "corr-replaced-1", "correlation_mismatch"),
        ("application_request_time_utc", "2026-06-01T00:00:00Z", "application_time_mismatch"),
        ("invocation_id", "other-invocation", "invocation_binding_mismatch"),
        ("step_id", "other-step", "invocation_binding_mismatch"),
        ("specialist", MasterSpecialist.HISTORICAL_ANALYTICS, "specialist_mismatch"),
        ("purpose", MasterPurpose.DISCOVERY, "invocation_binding_mismatch"),
        ("requirement", MasterRequirement.OPTIONAL, "invocation_binding_mismatch"),
        ("specialist_request_id", "other-specialist-request", "specialist_request_id_mismatch"),
        ("capability", HISTORY, "capability_step_mismatch"),
        ("temporal_scopes", (TemporalScope.HISTORICAL,), "invalid_temporal_scopes"),
        ("gates", (MasterRouteGate.DEPENDENCY_BLOCKED,), "invocation_binding_mismatch"),
        ("structural_ready_to_execute", False, "invocation_binding_mismatch"),
        ("withheld_untrusted_hints", MasterRouteHints(callsign="UAL999"), "invocation_binding_mismatch"),
        ("withheld_expected_aircraft_id", "ac-forged", "invocation_binding_mismatch"),
        ("withheld_expected_recommendation_id", "rec-forged", "invocation_binding_mismatch"),
        ("canonical_user_text", "Which other aircraft are affected now?", "master_request_mismatch"),
        (
            "planning_context",
            replace(binding.planning_context, correlation_id="corr-replaced-1"),
            "correlation_mismatch",
        ),
        (
            "descriptor",
            replace(binding.descriptor, invocation_id="other-invocation"),
            "invocation_binding_mismatch",
        ),
    )
    for field_name, value, code in cases:
        with pytest.raises(ContractValidationError, match=code):
            replace(binding, **{field_name: value})

    forged_request = replace(binding.request, request_id="other-specialist-request")
    with pytest.raises(ContractValidationError, match="specialist_request_id_mismatch"):
        replace(
            binding,
            specialist_request_id="other-specialist-request",
            request=forged_request,
        )
    forged_hints = MasterRouteHints(callsign="UAL999")
    forged_limitations = binding_limitations(
        forged_hints,
        binding.gates,
        expected_aircraft_id=None,
        expected_recommendation_id=None,
    )
    with pytest.raises(ContractValidationError, match="invocation_binding_mismatch"):
        replace(
            binding,
            withheld_untrusted_hints=forged_hints,
            limitations=forged_limitations,
        )
    forged_candidate_limitations = binding_limitations(
        binding.withheld_untrusted_hints,
        binding.gates,
        expected_aircraft_id="ac-forged",
        expected_recommendation_id=None,
    )
    with pytest.raises(ContractValidationError, match="invocation_binding_mismatch"):
        replace(
            binding,
            withheld_expected_aircraft_id="ac-forged",
            limitations=forged_candidate_limitations,
        )
    _assert_closed(binding)


def test_discovery_purpose_is_preserved():
    bundle = _bundle(_live(purpose=MasterPurpose.DISCOVERY))
    binding = prepare_master_live_ops_binding(bundle, _descriptor(bundle, "live-1"))
    assert binding.purpose is MasterPurpose.DISCOVERY
    assert binding.requirement is MasterRequirement.REQUIRED
    assert binding.request.user_text == TEXT
    _assert_closed(binding)


def test_wrong_specialist_rejected():
    historical = _bundle(_history())
    with pytest.raises(ContractValidationError, match="specialist_mismatch"):
        prepare_master_live_ops_binding(historical, _descriptor(historical, "hist-1"))

    mixed = _bundle(_live(), _history())
    with pytest.raises(ContractValidationError, match="specialist_mismatch"):
        prepare_master_live_ops_binding(mixed, _descriptor(mixed, "hist-1"))

    live = _descriptor(mixed, "live-1")
    swapped = replace(live, specialist=MasterSpecialist.HISTORICAL_ANALYTICS)
    with pytest.raises(ContractValidationError, match="specialist_mismatch"):
        prepare_master_live_ops_binding(mixed, swapped)


def test_wrong_invocation_rejected():
    bundle = _bundle(_live(), _history())
    planned = _descriptor(bundle, "live-1")
    invented = replace(planned, invocation_id="inv-not-on-the-plan")
    with pytest.raises(ContractValidationError, match="invocation_not_in_plan"):
        prepare_master_live_ops_binding(bundle, invented)

    other = _bundle(_live(), user_text=TEXT)
    foreign = replace(planned, master_request_id="other-master-request")
    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        prepare_master_live_ops_binding(other, foreign)
    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        prepare_master_live_ops_binding(bundle, foreign)


def test_mismatched_identity_rejected():
    bundle = _bundle(_live())
    planned = _descriptor(bundle, "live-1")
    cases = (
        (replace(planned, step_id="other-step"), "invocation_binding_mismatch"),
        (replace(planned, purpose=MasterPurpose.DISCOVERY), "invocation_binding_mismatch"),
        (replace(planned, requirement=MasterRequirement.OPTIONAL), "invocation_binding_mismatch"),
        (replace(planned, specialist_request_id="other-specialist-request"), "specialist_request_id_mismatch"),
    )
    for descriptor, code in cases:
        with pytest.raises(ContractValidationError, match=code):
            prepare_master_live_ops_binding(bundle, descriptor)


def test_malformed_objects_rejected():
    bundle = _bundle(_live())
    descriptor = _descriptor(bundle, "live-1")
    with pytest.raises(ContractValidationError, match="invalid_parallel_contract"):
        prepare_master_live_ops_binding(object(), descriptor)  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="invalid_adapter_descriptor"):
        prepare_master_live_ops_binding(bundle, object())  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="invalid_parallel_contract"):
        prepare_master_live_ops_binding(None, descriptor)  # type: ignore[arg-type]


def test_forged_execution_and_production_authority_rejected():
    bundle = _bundle(_live())
    descriptor = _descriptor(bundle, "live-1")
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        prepare_master_live_ops_binding(
            bundle,
            descriptor,
            production_authority=MasterProductionAuthority.PRODUCTION,
        )
    with pytest.raises(ContractValidationError, match="execution_authority_not_granted"):
        prepare_master_live_ops_binding(bundle, descriptor, execution_authority="GRANTED")
    with pytest.raises(ContractValidationError, match="source_authenticity_not_established"):
        prepare_master_live_ops_binding(bundle, descriptor, source_authenticity="AUTHENTICATED")
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        prepare_master_live_ops_binding(bundle, descriptor, executable=True)
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        prepare_master_live_ops_binding(bundle, descriptor, dispatch_permitted=True)

    binding = prepare_master_live_ops_binding(bundle, descriptor)
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        replace(binding, production_authority=MasterProductionAuthority.PRODUCTION)
    with pytest.raises(ContractValidationError, match="execution_authority_not_granted"):
        replace(binding, execution_authority="GRANTED")  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="source_authenticity_not_established"):
        replace(binding, source_authenticity="AUTHENTICATED")  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(binding, executable=True)
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(binding, dispatch_permitted=True)
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(binding, stops_before_execution_authorization=False)
    with pytest.raises(ContractValidationError, match="end_to_end_fulfillment_not_granted"):
        replace(binding, end_to_end_fulfilled=True)
    with pytest.raises(ContractValidationError, match="request_coverage_not_established"):
        replace(binding, request_coverage="ESTABLISHED")  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        replace(descriptor, production_authority=MasterProductionAuthority.PRODUCTION)
    with pytest.raises(ContractValidationError, match="execution_authority_not_granted"):
        replace(descriptor, execution_authority="GRANTED")  # type: ignore[arg-type]
    _assert_closed(binding)


def test_untrusted_hints_do_not_become_authorization():
    hints = MasterRouteHints(
        callsign="UAL123",
        typed_aircraft_id="ac-9",
        region_text="California",
        population_aircraft_ids=("ac-9", "ac-10"),
    )
    bundle = _bundle(_live(hints=hints))
    step = bundle.plan.steps[0]
    assert step.region_text == "California"
    assert MasterRouteGate.REGION_VALIDATION_PENDING in step.gates
    assert step.ready_to_execute is False
    assert step.hint_trust is MasterHintTrust.UNTRUSTED

    binding = prepare_master_live_ops_binding(bundle, _descriptor(bundle, "live-1"))
    assert binding.request.selector_hints is None
    assert binding.request.entity_hints == ()
    assert binding.selectors_authorized is False
    assert binding.callsign_authenticated is False
    assert binding.withheld_untrusted_hints == hints
    assert binding.structural_ready_to_execute is False
    assert binding.dispatch_permitted is False
    payload = binding.request.to_dict()
    assert payload["selector_hints"] is None
    assert payload["entity_hints"] == []
    assert "California" not in payload.values()
    assert "UAL123" not in payload.values()
    assert "ac-9" not in payload.values()
    assert MasterLiveOpsBindingLimitation.REGION_NOT_VERIFIED_SELECTOR in binding.limitations
    assert MasterLiveOpsBindingLimitation.CALLSIGN_NOT_AUTHENTICATED_AIRCRAFT in binding.limitations
    assert MasterLiveOpsBindingLimitation.ENTITY_REFERENCE_NOT_AUTHORIZED in binding.limitations
    assert (
        MasterLiveOpsBindingLimitation.PLANNED_SCOPE_NOT_FAITHFULLY_REPRESENTABLE
        in binding.limitations
    )
    assert binding.aircraft_impact_count is None
    assert binding.aircraft_impact_count != len(hints.population_aircraft_ids)
    assert binding.verified_zero is False

    callsign_only = _bundle(_live(hints=MasterRouteHints(callsign="UAL123")))
    callsign_step = callsign_only.plan.steps[0]
    assert callsign_step.ready_to_execute is True
    callsign_binding = prepare_master_live_ops_binding(
        callsign_only,
        _descriptor(callsign_only, "live-1"),
    )
    assert callsign_binding.structural_ready_to_execute is True
    assert callsign_binding.dispatch_permitted is False
    assert callsign_binding.request.selector_hints is None
    assert callsign_binding.selectors_authorized is False
    assert (
        MasterLiveOpsBindingLimitation.CALLSIGN_NOT_AUTHENTICATED_AIRCRAFT
        in callsign_binding.limitations
    )

    recommendation = _bundle(_live(hints=MasterRouteHints(recommendation_id="rec-9")))
    recommendation_binding = prepare_master_live_ops_binding(
        recommendation,
        _descriptor(recommendation, "live-1"),
    )
    assert recommendation_binding.request.entity_hints == ()
    assert recommendation_binding.request.selector_hints is None
    assert (
        MasterLiveOpsBindingLimitation.ENTITY_REFERENCE_NOT_AUTHORIZED
        in recommendation_binding.limitations
    )
    assert "rec-9" not in recommendation_binding.request.to_dict().values()

    promoted = replace(
        binding.request,
        selector_hints=LiveOpsSelectorHints(region="California", callsign="UAL123"),
    )
    with pytest.raises(ContractValidationError, match="selection_hints_not_trusted"):
        replace(binding, request=promoted)
    entity_request = replace(
        binding.request,
        entity_hints=(LiveOpsEntityHint(entity_kind="AIRCRAFT", entity_id="ac-9", callsign="UAL123"),),
    )
    with pytest.raises(ContractValidationError, match="selection_hints_not_trusted"):
        replace(binding, request=entity_request)
    with pytest.raises(ContractValidationError, match="selection_hints_not_trusted"):
        replace(binding, selectors_authorized=True)
    with pytest.raises(ContractValidationError, match="callsign_not_authenticated_aircraft"):
        replace(binding, callsign_authenticated=True)
    with pytest.raises(ContractValidationError, match="missing_binding_limitation"):
        replace(binding, limitations=_STANDING_LIMITATIONS)


def test_user_text_is_not_parsed_into_selectors():
    bundle = _bundle(_live(), user_text=MENTION_TEXT)
    binding = prepare_master_live_ops_binding(bundle, _descriptor(bundle, "live-1"))
    assert binding.request.user_text == MENTION_TEXT
    assert binding.request.selector_hints is None
    assert binding.request.entity_hints == ()
    assert binding.withheld_untrusted_hints == MasterRouteHints()
    assert binding.limitations == _STANDING_LIMITATIONS
    assert MasterLiveOpsBindingLimitation.REGION_NOT_VERIFIED_SELECTOR not in binding.limitations


def test_no_trusted_runtime_context_created():
    bundle = _bundle(_live())
    descriptor = _descriptor(bundle, "live-1")
    signature = inspect.signature(prepare_master_live_ops_binding)
    assert "trusted_context" not in signature.parameters
    assert "tables" not in signature.parameters
    assert "clock" not in signature.parameters
    with pytest.raises(TypeError):
        prepare_master_live_ops_binding(bundle, descriptor, trusted_context=object())  # type: ignore[call-arg]

    binding = prepare_master_live_ops_binding(bundle, descriptor)
    assert binding.trusted_context_created is False
    assert MasterLiveOpsBindingLimitation.TRUSTED_RUNTIME_CONTEXT_ABSENT in binding.limitations
    for item in fields(binding):
        value = getattr(binding, item.name)
        assert not isinstance(value, LiveOpsTrustedContext)
        assert not isinstance(value, LiveOpsAdmissionSources)
    with pytest.raises(ContractValidationError, match="trusted_runtime_context_absent"):
        replace(binding, trusted_context_created=True)
    with pytest.raises(ContractValidationError, match="admission_sources_not_created"):
        replace(binding, admission_sources_created=True)


def test_supplied_evidence_is_not_adopted():
    supplied_request = live_ops_request()
    supplied_context = live_ops_context()
    supplied_result = live_ops_all_verified_result()
    assert isinstance(supplied_context, LiveOpsTrustedContext)
    assert supplied_request.user_text != TEXT
    assert supplied_request.selector_hints is not None
    assert supplied_request.entity_hints
    assert supplied_result.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED
    context = _context()
    decision = authorize_route(_proposal(_live()), context)
    assert decision.plan is not None
    authorization = decision.plan.steps[0].invocation
    evidence = (
        MasterEvidenceCorroborationInput(
            MasterEvidenceAdmissionInput(
                authorization,
                LiveOpsAdmissionSources(supplied_request, supplied_context, supplied_result),
            )
        ),
    )
    bundle = MasterParallelContractBundle(
        context=context,
        plan=decision.plan,
        evidence_inputs=evidence,
    )
    binding = prepare_master_live_ops_binding(bundle, _descriptor(bundle, "live-1"))
    assert binding.planning_context is context
    assert binding.planned_step is decision.plan.steps[0]
    assert binding.request.user_text == TEXT
    assert binding.request.request_id == authorization.specialist_request_id
    assert binding.request is not supplied_request
    assert binding.admission_sources_created is False
    assert binding.live_ops_refs_are_tool_results is False
    assert binding.request.selector_hints is None
    assert binding.request.entity_hints == ()
    assert binding.aircraft_impact_count is None
    assert binding.verified_zero is False
    assert "verification_outcome" not in MasterLiveOpsBinding.__dataclass_fields__
    for item in fields(binding):
        value = getattr(binding, item.name)
        assert value is not supplied_context
        assert value is not supplied_result
        assert value is not supplied_request
        assert value is not evidence[0]


def test_binder_cannot_create_aircraft_impact_count_or_verified_zero():
    hints = MasterRouteHints(population_aircraft_ids=("ac-1", "ac-2"))
    bundle = _bundle(
        _live(hints=hints),
        user_text="How many aircraft are impacted right now?",
    )
    binding = prepare_master_live_ops_binding(bundle, _descriptor(bundle, "live-1"))
    assert binding.request.entity_hints == ()
    assert binding.aircraft_impact_count is None
    assert binding.verified_zero is False
    assert binding.aircraft_impact_count != 0
    assert binding.aircraft_impact_count != len(hints.population_aircraft_ids)
    assert MasterLiveOpsBindingLimitation.NO_AUTHORITATIVE_AIRCRAFT_IMPACT_COUNT in binding.limitations
    assert MasterLiveOpsBindingLimitation.VERIFIED_ZERO_NOT_ESTABLISHED in binding.limitations
    with pytest.raises(ContractValidationError, match="aircraft_impact_count_not_authoritative"):
        replace(binding, aircraft_impact_count=2)
    with pytest.raises(ContractValidationError, match="aircraft_impact_count_not_authoritative"):
        replace(binding, aircraft_impact_count=0)
    with pytest.raises(ContractValidationError, match="verified_zero_not_established"):
        replace(binding, verified_zero=True)


def test_no_provider_tool_or_aws_invocation():
    source = MODULE.read_text(encoding="utf-8")
    for banned in (
        "boto3",
        "botocore",
        "anthropic",
        "openai",
        "langgraph",
        "LiveOpsSpecialist(",
        "LiveOpsAdapter(",
        "LiveOpsTrustedContext(",
        "LiveOpsAdmissionSources(",
        "AthenaExecutor",
    ):
        assert banned not in source
    bundle = _bundle(_live())
    binding = prepare_master_live_ops_binding(bundle, _descriptor(bundle, "live-1"))
    assert binding.dispatch_permitted is False
    assert "dispatch" not in MasterLiveOpsBinding.__dataclass_fields__


def test_existing_master_preflight_still_denies_dispatch():
    bundle = _bundle(_live(), _history())
    binding = prepare_master_live_ops_binding(bundle, _descriptor(bundle, "live-1"))
    evaluation = evaluate_execution_preflight(bundle)
    assert evaluation.dispatch_permitted is False
    assert evaluation.stops_before_execution_authorization is True
    assert evaluation.report.executable is False
    assert evaluation.report.state is MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION
    assert evaluation.report.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert evaluation.report.production_authority is MasterProductionAuthority.DENIED
    assert evaluation.report.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert bundle.plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert binding.dispatch_permitted is False
    assert binding.executable is False
    assert bundle.evidence_inputs == ()
