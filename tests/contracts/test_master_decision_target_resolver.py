"""Decision target resolution over injected reads. No trusted dispatch."""

from __future__ import annotations

import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from wilvor_ai.contracts import (
    AgentCapability,
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.decision_claims import AircraftIdentityClaim
from wilvor_ai.decision_contracts import (
    DecisionAdvisoryAuthority,
    DecisionChainGap,
    DecisionEvaluationState,
    DecisionEvidence,
    DecisionEvidenceKind,
    DecisionRecommendationEvidence,
    DecisionRecommendationSet,
    DecisionReportedLinkState,
    DecisionRiskEvidence,
    RecommendationActionType,
    RiskPresence,
    StoredRiskLevel,
    expected_decision_status,
)
from wilvor_ai.decision_model_contracts import DecisionModelDecision
from wilvor_ai.decision_specialist import (
    MAX_PERSISTED_AIRPORT_CALLS,
    MAX_TOOL_CALLS,
    DecisionSpecialist,
    SequentialDecisionToolCallIdFactory,
)
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionCollectionPartialReason,
    DecisionSpecialistRequest,
    DecisionTargetMode,
)
from wilvor_ai.decision_tools import DECISION_TOOLS, DecisionToolInvocation, DecisionToolsRuntime
from wilvor_ai.decision_tool_projection import project_decision_tool_result
from wilvor_ai.live_ops_public_contracts import (
    LiveOpsDiscovery,
    LiveOpsDiscoveryStatus,
    LiveOpsEntityHint,
)
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
    observe_callsign,
    observe_population,
    observe_typed_aircraft_id,
)
from wilvor_ai.master_decision_target_resolver import (
    DECISION_TOOL_NAMES,
    HOST_PROOF_ISSUER,
    MAX_DECISION_STEPS,
    MAX_DECISION_TOOL_CALLS,
    MAX_PERSISTED_RECOMMENDATION_FANOUT,
    REQUIRED_HOST_PROOFS,
    ClassifiedDecisionCandidate,
    DecisionCandidateRole,
    DecisionReadOrigin,
    DecisionResolutionDefect,
    DecisionResolutionStatus,
    ExecutionHostProof,
    FakeAircraftRead,
    FakeDecisionReadCatalog,
    FakeRiskRecord,
    HostProofRequirement,
    MasterDecisionTargetResolution,
    RiskRecordObservation,
    release_master_decision_target,
    resolve_master_decision_target,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS as ROUTING_MAX_DECISION_STEPS,
    MasterHintTrust,
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteHints,
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.master_parallel_contracts import MasterParallelContractBundle
from wilvor_ai.master_specialist_adapters import describe_planned_adapters
from wilvor_ai.model_contracts import ModelDecisionKind, ProposedToolCall, ValidationFeedbackCode
from wilvor_ai.persisted_airport_contracts import PersistedAirportEvidence


AIRCRAFT = "AC-1"
OTHER = "AC-2"
FOREIGN = "AC-FOREIGN"
HINT = "AC-HINT"
CALLSIGN = "UAL100"
RISK_CAPABILITY = AgentCapability.EXPLAIN_EXISTING_RISK
PRESENT_HIGH = FakeRiskRecord(RiskPresence.PRESENT, StoredRiskLevel.HIGH)
PRESENT_LOW = FakeRiskRecord(RiskPresence.PRESENT, StoredRiskLevel.LOW)
DEFECT = DecisionResolutionDefect
ROLE = DecisionCandidateRole
AS_OF = "2026-09-29T00:00:00Z"
CONTEXT = "get_current_decision_context"
RECOMMENDATION = "get_current_recommendation"
PERSISTED = "get_persisted_airport_candidate_evidence"


def _context(text: str = "Which recorded risk applies?", **overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-decision-resolve-1",
        "correlation_id": "corr-decision-resolve-1",
        "application_request_time_utc": "2026-01-15T00:00:01Z",
        "canonical_user_text": text,
    }
    values.update(overrides)
    return MasterPlanningContext(**values)  # type: ignore[arg-type]


def _proposal(text: str, *steps: MasterProposedStep) -> MasterRouteProposal:
    capabilities = tuple(
        dict.fromkeys(
            MasterProposedCapability(step.capability, MasterRequirement.REQUIRED) for step in steps
        )
    )
    return MasterRouteProposal(user_text=text, capabilities=capabilities, steps=steps)


def _decision(**overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "step_id": "dec-1",
        "specialist": MasterSpecialist.DECISION,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "capability": RISK_CAPABILITY,
        "temporal_intent": MasterRouteTemporalIntent.CURRENT,
    }
    values.update(overrides)
    return MasterProposedStep(**values)  # type: ignore[arg-type]


def _readiness(**assess: object):
    text = "Which recorded risk applies?"
    context_keys = {
        "master_request_id",
        "correlation_id",
        "application_request_time_utc",
        "canonical_user_text",
        "target_candidate_aircraft_id",
        "target_candidate_recommendation_id",
    }
    context_overrides = {key: assess.pop(key) for key in list(assess) if key in context_keys}
    hints = assess.pop("hints", None)
    needs_persisted = assess.pop("needs_persisted_evidence", False)
    step_kwargs: dict[str, object] = {}
    if hints is not None:
        step_kwargs["hints"] = hints
    if needs_persisted:
        step_kwargs["needs_persisted_evidence"] = True
    context = _context(**context_overrides)
    decision = authorize_route(_proposal(text, _decision(**step_kwargs)), context)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    bundle = MasterParallelContractBundle(context=context, plan=decision.plan, evidence_inputs=())
    from wilvor_ai.master_decision_execution_readiness import (
        assess_master_decision_execution_readiness,
    )

    descriptor = next(
        item
        for item in describe_planned_adapters(bundle).descriptors
        if item.specialist is MasterSpecialist.DECISION
    )
    return assess_master_decision_execution_readiness(bundle, descriptor, **assess)


def _discovery(
    aircraft_ids: tuple[str, ...],
    *,
    callsign: str = CALLSIGN,
    status: LiveOpsDiscoveryStatus = LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH,
    resolved: bool = True,
) -> LiveOpsDiscovery:
    return LiveOpsDiscovery(
        callsign=callsign,
        status=status,
        aircraft_ids=aircraft_ids,
        evidence_tool_call_id="discovery-tool-1",
        reference_epoch=1_700_000_000,
        identity_resolved=resolved,
        selection_authorized=False,
    )


def _read(
    aircraft_id: str,
    *,
    callsign: str | None = CALLSIGN,
    state_fresh: bool = True,
    population_member: bool = True,
    in_request_scope: bool = True,
    risk: FakeRiskRecord | None = PRESENT_HIGH,
    recommendation_ids: tuple[str, ...] = (),
) -> FakeAircraftRead:
    return FakeAircraftRead(
        aircraft_id=aircraft_id,
        callsign=callsign,
        state_fresh=state_fresh,
        population_member=population_member,
        in_request_scope=in_request_scope,
        risk=risk,
        recommendation_ids=recommendation_ids,
    )


def _catalog(*rows: FakeAircraftRead) -> FakeDecisionReadCatalog:
    return FakeDecisionReadCatalog(aircraft=rows)


def _unique_readiness(aircraft_id: str = AIRCRAFT, **overrides: object):
    discovery = _discovery((aircraft_id,))
    values: dict[str, object] = {
        "target_candidate_aircraft_id": aircraft_id,
        "typed_aircraft_observations": (observe_typed_aircraft_id(aircraft_id),),
        "callsign_observations": (observe_callsign(discovery),),
        "discoveries": (discovery,),
        "entity_hints": (
            LiveOpsEntityHint(entity_kind="AIRCRAFT", entity_id=aircraft_id, callsign=CALLSIGN),
        ),
        "population_observations": (observe_population((aircraft_id,)),),
    }
    values.update(overrides)
    return _readiness(**values)


def _unique(aircraft_id: str = AIRCRAFT, **read_overrides: object):
    readiness = _unique_readiness(aircraft_id)
    return resolve_master_decision_target(readiness, _catalog(_read(aircraft_id, **read_overrides)))


def _roles(resolution) -> set[DecisionCandidateRole]:
    return {item.role for item in resolution.candidates}


def test_one_consistent_aircraft_is_observed_and_not_eligible():
    resolution = _unique()

    assert resolution.status is DecisionResolutionStatus.UNIQUE_CANDIDATE_NOT_ELIGIBLE
    assert resolution.defects == ()
    assert resolution.observed_aircraft_id == AIRCRAFT
    assert resolution.observed_recommendation_ids == ()
    assert resolution.execution_eligible is False
    assert resolution.trusted_target_issued is False
    assert resolution.current_authorized_target_available is False
    assert resolution.decision_request is None
    assert resolution.native_request_built is False
    assert resolution.dispatch_permitted is False
    assert resolution.executable is False
    assert resolution.ready_to_execute is False
    assert resolution.target_authorization_granted is False
    assert resolution.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert resolution.authenticated_evidence is False
    assert resolution.authenticated_risk_evidence is False
    assert resolution.read_origin is DecisionReadOrigin.INJECTED_FAKE
    assert resolution.host_proofs_required == REQUIRED_HOST_PROOFS
    assert HostProofRequirement.SOURCE_CURRENTNESS in resolution.host_proofs_required
    assert HostProofRequirement.ALLOWED_TARGET_POPULATION in resolution.host_proofs_required
    assert (
        HostProofRequirement.SEPARATE_PERSISTED_RECOMMENDATION_AUTHORIZATION
        in resolution.host_proofs_required
    )
    assert resolution.host_proofs_satisfied == ()
    assert resolution.host_proof_issuer == HOST_PROOF_ISSUER
    assert resolution.caller_proof_accepted is False
    assert resolution.max_decision_steps is MAX_DECISION_STEPS
    assert resolution.max_decision_steps is ROUTING_MAX_DECISION_STEPS
    assert resolution.max_decision_steps == 1
    assert resolution.decision_invocation_count == 1
    assert resolution.persisted_fanout_authorized is False
    assert resolution.persisted_fanout_count == 0
    assert resolution.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert resolution.production_authority is MasterProductionAuthority.DENIED
    assert resolution.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert resolution.authorization_token is None
    assert resolution.injected_risk_present is True
    assert resolution.observed_risk_level is StoredRiskLevel.HIGH
    assert resolution.risk_observation is RiskRecordObservation.PRESENT
    assert resolution.absent_risk_is_low_risk is False
    assert resolution.readiness.recorded_risk_level is None
    assert resolution.readiness.hint_trust is MasterHintTrust.UNTRUSTED
    assert resolution.readiness.callsign_is_aircraft_identity is False
    roles = _roles(resolution)
    assert ROLE.USER_SUPPLIED_AIRCRAFT_IDENTIFIER in roles
    assert ROLE.CALLSIGN_OBSERVATION in roles
    assert ROLE.MASTER_COMPARISON_CANDIDATE in roles
    assert ROLE.LIVE_OPS_DISCOVERY_CANDIDATE in roles
    assert ROLE.CURRENT_INDEPENDENTLY_VALIDATED_AUTHORIZED_TARGET not in roles
    assert ROLE.PERSISTED_RECOMMENDATION_CANDIDATE not in roles
    assert all(item.authorizes_execution is False for item in resolution.candidates)
    user_ids = {
        item.identifier
        for item in resolution.candidates
        if item.role is ROLE.USER_SUPPLIED_AIRCRAFT_IDENTIFIER
    }
    assert user_ids == {AIRCRAFT}

    forged = ExecutionHostProof(HostProofRequirement.SOURCE_CURRENTNESS, AIRCRAFT)
    downgraded = resolve_master_decision_target(
        resolution.readiness,
        _catalog(_read(AIRCRAFT)),
        host_proofs=(forged, SimpleNamespace(satisfied=True, issued_by_execution_host=True)),
    )
    assert downgraded.status is DecisionResolutionStatus.REJECTED
    assert DEFECT.CALLER_PROOF_NOT_HOST_ISSUED in downgraded.defects
    assert downgraded.execution_eligible is False
    assert downgraded.host_proofs_satisfied == ()
    assert downgraded.observed_aircraft_id is None
    assert downgraded.decision_request is None


def test_several_aircraft_sharing_a_callsign_are_not_selected():
    discovery = _discovery(
        (AIRCRAFT, OTHER),
        status=LiveOpsDiscoveryStatus.MULTIPLE_SUPPORTED_MATCHES,
        resolved=False,
    )
    readiness = _readiness(
        target_candidate_aircraft_id=AIRCRAFT,
        typed_aircraft_observations=(observe_typed_aircraft_id(AIRCRAFT),),
        callsign_observations=(observe_callsign(discovery),),
        discoveries=(discovery,),
        population_observations=(observe_population((AIRCRAFT, OTHER)),),
    )
    resolution = resolve_master_decision_target(
        readiness,
        _catalog(_read(AIRCRAFT), _read(OTHER)),
    )

    assert resolution.status is DecisionResolutionStatus.REJECTED
    assert DEFECT.AMBIGUOUS_CALLSIGN in resolution.defects
    assert resolution.observed_aircraft_id is None
    assert resolution.execution_eligible is False
    assert resolution.decision_request is None
    assert resolution.readiness.identity_ambiguous is True
    assert resolution.readiness.callsign_is_aircraft_identity is False


def test_wrong_aircraft_id_is_rejected():
    readiness = _unique_readiness()
    resolution = resolve_master_decision_target(readiness, _catalog(_read(OTHER)))

    assert resolution.status is DecisionResolutionStatus.REJECTED
    assert DEFECT.AIRCRAFT_ID_MISMATCH in resolution.defects
    assert resolution.observed_aircraft_id is None
    assert resolution.trusted_target_issued is False
    assert all(item.identifier != OTHER or item.authorizes_execution is False for item in resolution.candidates)


def test_untrusted_route_hint_is_not_selected():
    readiness = _readiness(
        hints=MasterRouteHints(typed_aircraft_id=HINT),
        population_observations=(observe_population((HINT,)),),
    )
    resolution = resolve_master_decision_target(
        readiness,
        _catalog(_read(HINT, state_fresh=True, population_member=True, in_request_scope=True)),
    )

    assert resolution.status is DecisionResolutionStatus.REJECTED
    assert DEFECT.UNTRUSTED_ROUTE_HINT in resolution.defects
    assert resolution.observed_aircraft_id is None
    assert ROLE.MODEL_PROPOSED_TARGET in _roles(resolution)
    assert ROLE.USER_SUPPLIED_AIRCRAFT_IDENTIFIER not in _roles(resolution)
    assert resolution.execution_eligible is False
    assert resolution.readiness.model_text_selects_target is False
    assert resolution.decision_request is None


def test_foreign_live_ops_discovery_is_not_the_aircraft():
    foreign = _discovery((FOREIGN,), callsign="DAL200")
    readiness = _readiness(
        target_candidate_aircraft_id=AIRCRAFT,
        typed_aircraft_observations=(observe_typed_aircraft_id(AIRCRAFT),),
        discoveries=(foreign,),
        population_observations=(observe_population((AIRCRAFT,)),),
    )
    resolution = resolve_master_decision_target(readiness, _catalog(_read(AIRCRAFT)))

    assert resolution.status is DecisionResolutionStatus.REJECTED
    assert DEFECT.FOREIGN_LIVE_OPS_DISCOVERY in resolution.defects
    assert resolution.observed_aircraft_id is None
    foreign_ids = {
        item.identifier
        for item in resolution.candidates
        if item.role is ROLE.LIVE_OPS_DISCOVERY_CANDIDATE and item.field_name == "aircraft_id"
    }
    assert FOREIGN in foreign_ids
    assert resolution.execution_eligible is False
    assert resolution.readiness.live_ops_discovery_is_trusted_target is False


def test_stale_aircraft_state_is_rejected():
    resolution = _unique(state_fresh=False)

    assert resolution.status is DecisionResolutionStatus.REJECTED
    assert DEFECT.STALE_AIRCRAFT_STATE in resolution.defects
    assert resolution.observed_aircraft_id is None
    assert resolution.execution_eligible is False
    assert HostProofRequirement.SOURCE_CURRENTNESS not in resolution.host_proofs_satisfied
    assert resolution.decision_request is None


def test_out_of_scope_aircraft_is_rejected():
    resolution = _unique(in_request_scope=False)

    assert resolution.status is DecisionResolutionStatus.REJECTED
    assert DEFECT.OUT_OF_SCOPE_AIRCRAFT in resolution.defects
    assert resolution.observed_aircraft_id is None
    assert resolution.execution_eligible is False
    assert HostProofRequirement.ALLOWED_TARGET_POPULATION not in resolution.host_proofs_satisfied

    excluded = resolve_master_decision_target(
        _unique_readiness(population_observations=(observe_population((OTHER,)),)),
        _catalog(_read(AIRCRAFT, population_member=True, in_request_scope=True)),
    )
    assert DEFECT.OUT_OF_SCOPE_AIRCRAFT in excluded.defects
    assert excluded.observed_aircraft_id is None
    assert excluded.execution_eligible is False


def test_conflicting_recommendation_ids_are_rejected():
    readiness = _readiness(
        target_candidate_recommendation_id="rec-1",
        needs_persisted_evidence=True,
        earlier_result_recommendation_ids=("rec-2",),
    )
    resolution = resolve_master_decision_target(readiness, _catalog())

    assert resolution.status is DecisionResolutionStatus.REJECTED
    assert DEFECT.CONFLICTING_RECOMMENDATION_ID in resolution.defects
    assert resolution.observed_aircraft_id is None
    assert resolution.observed_recommendation_ids == ()
    assert resolution.persisted_fanout_authorized is False
    assert resolution.persisted_fanout_count == 0
    assert ROLE.PERSISTED_RECOMMENDATION_CANDIDATE in _roles(resolution)
    assert all(item.authorizes_execution is False for item in resolution.candidates)


def test_persisted_recommendation_without_separate_authorization_stays_blocked():
    readiness = _readiness(
        target_candidate_recommendation_id="rec-1",
        needs_persisted_evidence=True,
        earlier_result_recommendation_ids=("rec-1",),
    )
    proof = ExecutionHostProof(
        HostProofRequirement.SEPARATE_PERSISTED_RECOMMENDATION_AUTHORIZATION,
        "rec-1",
    )
    resolution = resolve_master_decision_target(
        readiness,
        _catalog(_read(AIRCRAFT, recommendation_ids=("rec-1",))),
        host_proofs=(proof,),
    )

    assert resolution.status is DecisionResolutionStatus.REJECTED
    assert DEFECT.PERSISTED_RECOMMENDATION_NOT_SEPARATELY_AUTHORIZED in resolution.defects
    assert DEFECT.CALLER_PROOF_NOT_HOST_ISSUED in resolution.defects
    assert DEFECT.CONFLICTING_RECOMMENDATION_ID not in resolution.defects
    assert resolution.host_proofs_satisfied == ()
    assert resolution.caller_proof_accepted is False
    assert resolution.persisted_fanout_authorized is False
    assert resolution.observed_recommendation_ids == ()
    assert resolution.execution_eligible is False
    assert resolution.decision_request is None
    assert resolution.current_authorized_target_available is False


def test_missing_risk_is_not_low_risk():
    missing = _unique(risk=None)
    absent = _unique(risk=FakeRiskRecord(RiskPresence.ABSENT))
    stored_low = _unique(risk=PRESENT_LOW)

    assert missing.status is DecisionResolutionStatus.REJECTED
    assert DEFECT.MISSING_RISK_RECORD in missing.defects
    assert missing.risk_observation is RiskRecordObservation.MISSING
    assert missing.observed_risk_level is None
    assert missing.injected_risk_present is False
    assert missing.absent_risk_is_low_risk is False
    assert missing.observed_risk_level is not StoredRiskLevel.LOW
    assert missing.readiness.recorded_risk_level is None
    assert missing.authenticated_risk_evidence is False

    assert absent.risk_observation is RiskRecordObservation.MISSING
    assert absent.observed_risk_level is None
    assert absent.injected_risk_present is False
    assert absent.absent_risk_is_low_risk is False

    assert stored_low.status is DecisionResolutionStatus.UNIQUE_CANDIDATE_NOT_ELIGIBLE
    assert stored_low.risk_observation is RiskRecordObservation.PRESENT
    assert stored_low.observed_risk_level is StoredRiskLevel.LOW
    assert stored_low.injected_risk_present is True
    assert stored_low.absent_risk_is_low_risk is False
    assert stored_low.authenticated_risk_evidence is False
    assert stored_low.execution_eligible is False
    assert stored_low.readiness.recorded_risk_level is None
    assert missing.risk_observation is not stored_low.risk_observation
    assert missing.observed_risk_level != stored_low.observed_risk_level


def test_unsupported_or_excessive_fanout_is_rejected():
    readiness = _unique_readiness()
    catalog = _catalog(_read(AIRCRAFT))
    unsupported = resolve_master_decision_target(
        readiness,
        catalog,
        proposed_tool_names=("search_flights",),
    )
    excessive_ids = resolve_master_decision_target(
        readiness,
        catalog,
        proposed_fanout_ids=("rec-1", "rec-2", "rec-3", "rec-4"),
    )
    excessive_steps = resolve_master_decision_target(
        readiness,
        catalog,
        requested_decision_invocations=2,
    )

    assert unsupported.status is DecisionResolutionStatus.REJECTED
    assert DEFECT.UNSUPPORTED_FANOUT in unsupported.defects
    assert unsupported.decision_invocation_count == 1
    assert unsupported.execution_eligible is False
    assert excessive_ids.status is DecisionResolutionStatus.REJECTED
    assert DEFECT.EXCESSIVE_FANOUT in excessive_ids.defects
    assert DEFECT.PERSISTED_RECOMMENDATION_NOT_SEPARATELY_AUTHORIZED in excessive_ids.defects
    assert excessive_ids.persisted_fanout_count == 0
    assert excessive_ids.persisted_fanout_authorized is False
    assert excessive_ids.max_persisted_recommendation_fanout == MAX_PERSISTED_AIRPORT_CALLS
    assert excessive_steps.status is DecisionResolutionStatus.REJECTED
    assert DEFECT.EXCESSIVE_FANOUT in excessive_steps.defects
    assert excessive_steps.max_decision_steps == 1
    assert excessive_steps.decision_invocation_count == 1
    assert MAX_PERSISTED_RECOMMENDATION_FANOUT == MAX_PERSISTED_AIRPORT_CALLS
    assert MAX_DECISION_TOOL_CALLS == MAX_TOOL_CALLS
    assert DECISION_TOOL_NAMES == {item.name for item in DECISION_TOOLS}


def test_native_request_mode_mismatch_does_not_build_a_request():
    readiness = _unique_readiness()
    catalog = _catalog(_read(AIRCRAFT))
    persisted_mode = resolve_master_decision_target(
        readiness,
        catalog,
        native_mode=DecisionTargetMode.DIRECT_PERSISTED,
        native_recommendation_id="rec-1",
    )
    wrong_aircraft = resolve_master_decision_target(
        readiness,
        catalog,
        native_mode=DecisionTargetMode.AIRCRAFT,
        native_aircraft_id=OTHER,
    )
    matching_shape = resolve_master_decision_target(
        readiness,
        catalog,
        native_mode=DecisionTargetMode.AIRCRAFT,
        native_aircraft_id=AIRCRAFT,
    )

    assert persisted_mode.status is DecisionResolutionStatus.REJECTED
    assert DEFECT.NATIVE_REQUEST_MODE_MISMATCH in persisted_mode.defects
    assert persisted_mode.decision_request is None
    assert persisted_mode.native_request_built is False
    assert DEFECT.NATIVE_REQUEST_MODE_MISMATCH in wrong_aircraft.defects
    assert wrong_aircraft.decision_request is None
    assert matching_shape.status is DecisionResolutionStatus.UNIQUE_CANDIDATE_NOT_ELIGIBLE
    assert matching_shape.decision_request is None
    assert matching_shape.native_request_built is False
    assert matching_shape.execution_eligible is False


def test_caller_controlled_target_is_not_dispatched(monkeypatch):
    calls = []

    def explode(self, request, runtime):
        calls.append((request, runtime))
        raise AssertionError("resolver dispatched a Decision specialist")

    monkeypatch.setattr(DecisionSpecialist, "run", explode)
    resolution = _unique()
    caller_request = DecisionSpecialistRequest(
        user_text="Summarize the current decision evidence.",
        mode=DecisionTargetMode.AIRCRAFT,
        aircraft_id=resolution.observed_aircraft_id,
        request_id="req-caller-1",
    )
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        release_master_decision_target(resolution, caller_request)
    controlled = resolve_master_decision_target(
        resolution.readiness,
        _catalog(_read(AIRCRAFT)),
        caller_target={"aircraft_id": AIRCRAFT, "trusted": True},
        dispatch_requested=True,
    )

    assert calls == []
    assert controlled.status is DecisionResolutionStatus.REJECTED
    assert DEFECT.CALLER_CONTROLLED_TARGET in controlled.defects
    assert controlled.dispatch_permitted is False
    assert controlled.decision_request is None
    assert controlled.execution_eligible is False
    assert caller_request is not controlled.decision_request


def test_fake_result_is_not_authenticated_evidence(monkeypatch):
    specialist_result, specialist_calls = _run_specialist(
        monkeypatch,
        [_calls((RECOMMENDATION, {"aircraft_id": "ac-1"})), _claims(_identity())],
        profile={"ids": ("rec-1",)},
        aircraft_id="ac-1",
    )
    tool_result = specialist_result.tool_results[0]
    readiness = _unique_readiness()
    with pytest.raises(ContractValidationError, match="caller_evidence_cannot_satisfy_gate"):
        resolve_master_decision_target(
            readiness,
            _catalog(_read(AIRCRAFT)),
            supplied_evidence=tool_result,
        )
    resolution = _unique()

    assert specialist_calls
    assert resolution.authenticated_evidence is False
    assert resolution.authenticated_risk_evidence is False
    assert resolution.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert resolution.read_origin is DecisionReadOrigin.INJECTED_FAKE
    assert resolution.decision_request is None
    assert "tool_results" not in MasterDecisionTargetResolution.__dataclass_fields__
    assert tool_result.tool_call_id not in resolution.explanation
    with pytest.raises(ContractValidationError, match="fake_read_not_authenticated_evidence"):
        _read(AIRCRAFT, **{}).__class__(
            aircraft_id=AIRCRAFT,
            callsign=CALLSIGN,
            state_fresh=True,
            population_member=True,
            in_request_scope=True,
            risk=PRESENT_HIGH,
            authenticated_source=True,
        )


def test_native_specialist_keeps_mode_and_fanout_limits(monkeypatch):
    wrong_aircraft, calls, provider = _run_specialist_result(
        monkeypatch,
        [
            _calls((CONTEXT, {"aircraft_id": "AC-OTHER"})),
            _claims(_identity()),
        ],
    )
    assert calls == []
    assert provider.turns[1].validation_feedback.code is ValidationFeedbackCode.UNKNOWN_ARGUMENT
    assert wrong_aircraft.executed_tool_call_count == 0

    direct, direct_calls, direct_provider = _run_specialist_result(
        monkeypatch,
        [
            _calls((CONTEXT, {"aircraft_id": "ac-1"})),
            _claims(_identity()),
        ],
        mode=DecisionTargetMode.DIRECT_PERSISTED,
        aircraft_id=None,
        recommendation_id="rec-1",
    )
    assert direct_calls == []
    assert direct_provider.turns[1].validation_feedback.code is ValidationFeedbackCode.UNKNOWN_ARGUMENT
    assert direct.executed_tool_call_count == 0

    capped, capped_calls, capped_provider = _run_specialist_result(
        monkeypatch,
        [
            _calls((RECOMMENDATION, {"aircraft_id": "ac-1"})),
            _calls((PERSISTED, {"recommendation_id": "rec-4"})),
            _claims(_identity()),
        ],
        profile={"ids": ("rec-4", "rec-2", "rec-3", "rec-1")},
    )
    assert [item[0] for item in capped_calls] == [RECOMMENDATION]
    assert capped.collection_partial_reason is DecisionCollectionPartialReason.PERSISTED_FANOUT_CAP
    assert capped.executed_persisted_call_count == 0
    assert capped_provider.turns[2].validation_feedback.code is ValidationFeedbackCode.TOOL_CALL_BUDGET_EXCEEDED
    with pytest.raises(ContractValidationError, match="caller_evidence_cannot_satisfy_gate"):
        resolve_master_decision_target(
            _unique_readiness(),
            _catalog(_read(AIRCRAFT)),
            supplied_evidence=capped.tool_results[0],
        )


def test_comparison_candidate_agreement_does_not_authorize():
    resolution = _unique()
    comparison = [
        item
        for item in resolution.candidates
        if item.role is ROLE.MASTER_COMPARISON_CANDIDATE
    ]
    assert len(comparison) == 1
    assert comparison[0].identifier == AIRCRAFT
    assert comparison[0].authorizes_execution is False
    assert resolution.execution_eligible is False
    assert resolution.readiness.comparison_candidates_authorize_target is False


def test_authorized_target_role_cannot_be_built():
    with pytest.raises(ContractValidationError, match="target_authorization_not_granted"):
        ClassifiedDecisionCandidate(
            role=ROLE.CURRENT_INDEPENDENTLY_VALIDATED_AUTHORIZED_TARGET,
            source="execution-host",
            field_name="aircraft_id",
            identifier=AIRCRAFT,
        )
    with pytest.raises(ContractValidationError, match="caller_proof_not_host_issued"):
        ExecutionHostProof(HostProofRequirement.SOURCE_CURRENTNESS, AIRCRAFT, satisfied=True)
    with pytest.raises(ContractValidationError, match="absent_risk_is_not_low_risk"):
        FakeRiskRecord(RiskPresence.ABSENT, StoredRiskLevel.LOW)


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"dispatch_permitted": True}, "execution_not_granted"),
        ({"execution_eligible": True}, "execution_not_granted"),
        ({"trusted_target_issued": True}, "target_authorization_not_granted"),
        ({"decision_request": object()}, "execution_not_granted"),
        ({"currentness_satisfied": True}, "caller_proof_not_host_issued"),
        ({"allowed_target_population_satisfied": True}, "caller_proof_not_host_issued"),
        ({"persisted_recommendation_authorized": True}, "persisted_evidence_not_authorized"),
        ({"authenticated_evidence": True}, "fake_read_not_authenticated_evidence"),
        ({"risk_level": StoredRiskLevel.LOW}, "absent_risk_is_not_low_risk"),
        ({"absent_risk_is_low_risk": True}, "absent_risk_is_not_low_risk"),
        ({"max_decision_steps": 2}, "decision_fanout_not_permitted"),
        ({"source_authenticity": "AUTHENTICATED"}, "source_authenticity_not_established"),
        ({"supplied_evidence": object()}, "caller_evidence_cannot_satisfy_gate"),
    ],
)
def test_caller_promotions_are_rejected(kwargs: dict[str, object], code: str):
    with pytest.raises(ContractValidationError, match=code):
        resolve_master_decision_target(_unique_readiness(), _catalog(_read(AIRCRAFT)), **kwargs)


def test_replace_cannot_promote_a_resolution():
    resolution = _unique()
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(resolution, execution_eligible=True)
    with pytest.raises(ContractValidationError, match="target_authorization_not_granted"):
        replace(resolution, trusted_target_issued=True)
    with pytest.raises(ContractValidationError, match="fake_read_not_authenticated_evidence"):
        replace(resolution, authenticated_evidence=True)
    with pytest.raises(ContractValidationError, match="absent_risk_is_not_low_risk"):
        replace(resolution, absent_risk_is_low_risk=True)


def test_invalid_read_does_not_authorize_a_later_resolution():
    with pytest.raises(ContractValidationError, match="duplicate_aircraft_id"):
        _catalog(_read(AIRCRAFT), _read(AIRCRAFT))
    with pytest.raises(ContractValidationError, match="invalid_decision_read"):
        resolve_master_decision_target(_unique_readiness(), object())  # type: ignore[arg-type]
    resolution = _unique()
    assert resolution.execution_eligible is False
    assert resolution.authenticated_evidence is False


def test_concurrent_resolutions_stay_on_their_own_aircraft():
    jobs = {
        "first": (
            _unique_readiness(
                AIRCRAFT,
                master_request_id="master-req-resolve-a",
                correlation_id="corr-resolve-a",
            ),
            _catalog(_read(AIRCRAFT)),
        ),
        "second": (
            _unique_readiness(
                OTHER,
                master_request_id="master-req-resolve-b",
                correlation_id="corr-resolve-b",
            ),
            _catalog(_read(OTHER, callsign="DAL200")),
        ),
    }
    results: dict[str, object] = {}

    def _resolve(name: str, readiness, catalog) -> None:
        results[name] = resolve_master_decision_target(readiness, catalog)

    threads = [
        threading.Thread(
            target=_resolve,
            args=(name, readiness, catalog),
            daemon=True,
        )
        for name, (readiness, catalog) in jobs.items()
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    first = results["first"]
    second = results["second"]
    assert first.observed_aircraft_id == AIRCRAFT
    assert second.observed_aircraft_id == OTHER
    assert first.execution_eligible is False
    assert second.execution_eligible is False
    assert first.authenticated_evidence is False
    assert second.authenticated_evidence is False
    assert first is not second


class _ScriptedProvider:
    def __init__(self, decisions) -> None:
        self._decisions = list(decisions)
        self.turns = []

    def complete(self, turn):
        self.turns.append(turn)
        if not self._decisions:
            raise AssertionError("unexpected provider turn")
        item = self._decisions.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _calls(*pairs: tuple[str, dict]) -> DecisionModelDecision:
    return DecisionModelDecision(
        kind=ModelDecisionKind.TOOL_CALLS,
        tool_calls=tuple(ProposedToolCall(name, arguments) for name, arguments in pairs),
    )


def _claims(*claims) -> DecisionModelDecision:
    return DecisionModelDecision(kind=ModelDecisionKind.FINAL_CLAIMS, claims=claims)


def _identity(ref: str = "de-1", aircraft_id: str = "ac-1") -> AircraftIdentityClaim:
    return AircraftIdentityClaim(ref, TemporalScope.CURRENT, aircraft_id)


def _packet(tool_call_id: str, scope: TemporalScope) -> Evidence:
    return Evidence(
        source="wilvor.decision.specialist.test",
        source_records=(
            SourceRecord(record_id="row-1", source_version=None, event_timestamp_utc=None),
        ),
        query_timestamp_utc=AS_OF,
        freshness_status=FreshnessStatus.FRESH,
        confidence=ConfidenceLevel.UNKNOWN,
        limitations=(),
        tool_call_id=tool_call_id,
        temporal_scope=scope,
    )


def _present_risk() -> DecisionRiskEvidence:
    return DecisionRiskEvidence(
        presence=RiskPresence.PRESENT,
        risk_id="risk-1",
        risk_level=StoredRiskLevel.HIGH,
        risk_score=80,
    )


def _recommendation(recommendation_id: str) -> DecisionRecommendationEvidence:
    return DecisionRecommendationEvidence(
        recommendation_id=recommendation_id,
        primary_action_type=RecommendationActionType.MONITOR,
        advisory_authority=DecisionAdvisoryAuthority.ADVISORY_ONLY,
    )


def _recommendation_set(ids: tuple[str, ...]) -> DecisionRecommendationSet:
    return DecisionRecommendationSet(
        current=tuple(_recommendation(item) for item in ids),
        absence_state=(
            DecisionReportedLinkState.PRESENT
            if ids
            else DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES
        ),
    )


def _current_evidence(tool_name: str, aircraft_id: str, profile: dict) -> DecisionEvidence:
    if tool_name == RECOMMENDATION:
        return DecisionEvidence(
            kind=DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
            evaluation_state=DecisionEvaluationState.ESTABLISHED,
            aircraft_in_current_set=True,
            aircraft_id=aircraft_id,
            projection_state=DecisionReportedLinkState.PRESENT,
            projection_id="proj-1",
            risk=_present_risk(),
            recommendations=_recommendation_set(tuple(profile.get("ids", ("rec-1",)))),
            chain_gaps=(),
            limitation_codes=(),
        )
    return DecisionEvidence(
        kind=DecisionEvidenceKind.DECISION_CONTEXT,
        evaluation_state=DecisionEvaluationState.ESTABLISHED,
        aircraft_in_current_set=True,
        aircraft_id=aircraft_id,
        projection_state=DecisionReportedLinkState.PRESENT,
        projection_id="proj-1",
        aircraft_state_version="state-1",
        chain_gaps=(DecisionChainGap.NO_CURRENT_ENCOUNTER,),
        limitation_codes=(),
    )


def _tool_result(tool_name: str, arguments: dict, tool_call_id: str, profile: dict) -> ToolResult:
    if tool_name == PERSISTED:
        payload = PersistedAirportEvidence(
            recommendation_id=arguments["recommendation_id"],
            airport_evaluation_id=None,
            candidates=(),
        )
        return ToolResult(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            status=ToolResultStatus.SUCCESS,
            temporal_scope=TemporalScope.PERSISTED,
            data=payload.to_dict(),
            evidence=(_packet(tool_call_id, TemporalScope.PERSISTED),),
            as_of_utc=AS_OF,
            limitations=(),
        )
    evidence = _current_evidence(tool_name, arguments["aircraft_id"], profile)
    status = expected_decision_status(evidence)
    return ToolResult(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        status=status,
        temporal_scope=TemporalScope.CURRENT,
        data=evidence.to_dict(),
        evidence=(_packet(tool_call_id, TemporalScope.CURRENT),),
        limitations=(),
        as_of_utc=AS_OF,
    )


def _install(monkeypatch, profile: dict):
    calls = []

    class TestOnlyDecisionToolDouble:
        def __init__(self, runtime) -> None:
            self.runtime = runtime

        def invoke(self, tool_name, arguments, *, tool_call_id):
            calls.append((tool_name, dict(arguments), tool_call_id))
            raw = _tool_result(tool_name, arguments, tool_call_id, profile)
            return DecisionToolInvocation(
                raw_tool_result=raw,
                model_projection=project_decision_tool_result(raw),
            )

    monkeypatch.setattr(
        "wilvor_ai.decision_specialist.DecisionToolsAdapter",
        TestOnlyDecisionToolDouble,
    )
    return calls


def _request(**overrides) -> DecisionSpecialistRequest:
    values = {
        "user_text": "Summarize the current decision evidence.",
        "mode": DecisionTargetMode.AIRCRAFT,
        "aircraft_id": "ac-1",
        "request_id": "req-specialist-1",
    }
    values.update(overrides)
    return DecisionSpecialistRequest(**values)


def _runtime() -> DecisionToolsRuntime:
    return DecisionToolsRuntime(tables=object(), now_epoch=1, query_timestamp_utc=AS_OF)


def _run_specialist(monkeypatch, decisions, **kwargs):
    profile = kwargs.pop("profile", {})
    calls = _install(monkeypatch, profile)
    provider = _ScriptedProvider(decisions)
    result = DecisionSpecialist(
        provider=provider,
        tool_call_id_factory=SequentialDecisionToolCallIdFactory(),
    ).run(_request(**kwargs), _runtime())
    return result, calls


def _run_specialist_result(monkeypatch, decisions, **kwargs):
    profile = kwargs.pop("profile", {})
    calls = _install(monkeypatch, profile)
    provider = _ScriptedProvider(decisions)
    result = DecisionSpecialist(
        provider=provider,
        tool_call_id_factory=SequentialDecisionToolCallIdFactory(),
    ).run(_request(**kwargs), _runtime())
    return result, calls, provider
