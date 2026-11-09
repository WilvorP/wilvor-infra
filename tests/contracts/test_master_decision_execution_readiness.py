"""Offline Decision-target readiness. No target, token, or runtime is built."""

from __future__ import annotations

import ast
from dataclasses import fields, replace
from pathlib import Path

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError, TemporalScope
from wilvor_ai.decision_claims import RiskAbsentClaim
from wilvor_ai.decision_contracts import StoredRiskLevel
from wilvor_ai.decision_evidence_verifier import NOT_CURRENT_LIMITATION
from wilvor_ai.live_ops_public_contracts import LiveOpsDiscovery, LiveOpsDiscoveryStatus, LiveOpsEntityHint
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
    observe_callsign,
    observe_population,
    observe_typed_aircraft_id,
)
from wilvor_ai.master_decision_binding import MasterDecisionBindingStatus
from wilvor_ai.master_decision_execution_readiness import (
    MASTER_DECISION_EXECUTION_READINESS_EXPLANATION,
    NON_AUTHORIZING_DECISION_TARGET_KINDS,
    DecisionTargetMaterial,
    DecisionTargetMaterialKind,
    DecisionTargetReadinessRequirement,
    DecisionTargetReadinessStatus,
    assess_master_decision_execution_readiness,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_parallel_contracts import MasterParallelContractBundle
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS,
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
from wilvor_ai.master_specialist_adapters import describe_planned_adapters


TEXT = "Which recorded risk applies to the aircraft the application already identified?"
AIRCRAFT_TEXT = "Explain the recorded risk for aircraft N777WX."
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "functions"
    / "shared"
    / "wilvor_ai"
    / "master_decision_execution_readiness.py"
)
KIND = DecisionTargetMaterialKind


def _context(text: str = TEXT, **overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-decision-ready-1",
        "correlation_id": "corr-decision-ready-1",
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


def _decision(step_id: str = "dec-1", **overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "step_id": step_id,
        "specialist": MasterSpecialist.DECISION,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "capability": RISK,
        "temporal_intent": MasterRouteTemporalIntent.CURRENT,
    }
    values.update(overrides)
    return MasterProposedStep(**values)  # type: ignore[arg-type]


def _live(step_id: str = "live-1") -> MasterProposedStep:
    return MasterProposedStep(
        step_id=step_id,
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.DISCOVERY,
        requirement=MasterRequirement.REQUIRED,
        capability=LIVE,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
    )


def _bundle(*steps: MasterProposedStep, text: str = TEXT, **context_overrides: object):
    context = _context(text, **context_overrides)
    decision = authorize_route(_proposal(text, *steps), context)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    return MasterParallelContractBundle(
        context=context,
        plan=decision.plan,
        evidence_inputs=(),
    )


def _descriptor(bundle: MasterParallelContractBundle, specialist: MasterSpecialist = MasterSpecialist.DECISION):
    matches = [
        item
        for item in describe_planned_adapters(bundle).descriptors
        if item.specialist is specialist
    ]
    assert len(matches) == 1
    return matches[0]


def _assess(bundle: MasterParallelContractBundle, **kwargs: object):
    return assess_master_decision_execution_readiness(bundle, _descriptor(bundle), **kwargs)


def _discovery(status: LiveOpsDiscoveryStatus, aircraft_ids: tuple[str, ...], resolved: bool) -> LiveOpsDiscovery:
    return LiveOpsDiscovery(
        callsign="UAL100",
        status=status,
        aircraft_ids=aircraft_ids,
        evidence_tool_call_id="discovery-tool-1",
        reference_epoch=1_700_000_000,
        identity_resolved=resolved,
        selection_authorized=False,
    )


def _kinds(report) -> set[DecisionTargetMaterialKind]:
    return {item.kind for item in report.materials}


def test_readiness_records_unmet_requirements_and_denies_execution():
    bundle = _bundle(_decision())
    report = _assess(bundle)

    assert report.status is DecisionTargetReadinessStatus.TRUSTED_TARGET_UNAVAILABLE
    assert report.explanation == MASTER_DECISION_EXECUTION_READINESS_EXPLANATION
    assert report.unmet_requirements == tuple(DecisionTargetReadinessRequirement)
    assert report.authenticated_identity_satisfied is False
    assert report.currentness_satisfied is False
    assert report.allowed_target_population_satisfied is False
    assert report.persisted_fanout_authorized is False
    assert report.trusted_decision_target_available is False
    assert report.authorized_persisted_recommendation_ids == ()
    assert report.selected_aircraft_id is None
    assert report.selected_recommendation_id is None
    assert report.decision_invocation_authorized is False
    assert report.decision_invocation_count == 1
    assert report.max_decision_steps is MAX_DECISION_STEPS
    assert report.max_decision_steps == 1
    assert report.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert report.production_authority is MasterProductionAuthority.DENIED
    assert report.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert report.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert report.dispatch_permitted is False
    assert report.executable is False
    assert report.ready_to_execute is False
    assert report.target_authorization_granted is False
    assert report.authorization_token is None
    assert report.recorded_risk_level is None
    assert report.absent_risk_is_low_risk is False
    assert report.risk_recorded is False
    assert report.recorded_risk_level is not StoredRiskLevel.LOW
    assert report.binding.status is MasterDecisionBindingStatus.BLOCKED_MISSING_TRUSTED_TARGET
    assert report.binding.dispatch_permitted is False
    assert MasterRouteGate.TRUSTED_IDENTITY_VALIDATION_PENDING in report.gates
    assert MasterRouteGate.CURRENTNESS_VALIDATION_PENDING in report.gates
    assert report.temporal_scopes == (TemporalScope.CURRENT,)
    assert report.hint_trust is MasterHintTrust.UNTRUSTED
    assert "bundle" not in {item.name for item in fields(report)}
    assert "evidence_inputs" not in {item.name for item in fields(report)}
    assert not hasattr(report, "decision_request")
    names = _kinds(report)
    assert KIND.TRUSTED_DECISION_TARGET in names
    assert KIND.AUTHORIZED_PERSISTED_RECOMMENDATION_ID in names
    assert all(item.authorizes_decision_invocation is False for item in report.materials)


def test_user_text_is_not_copied_into_a_decision_target():
    bundle = _bundle(_decision(), text=AIRCRAFT_TEXT)
    report = _assess(bundle)

    assert report.user_text_copied_into_request is False
    assert all(item.identifier != "N777WX" for item in report.materials)
    assert KIND.USER_PROVIDED_AIRCRAFT_IDENTIFIER not in _kinds(report)


def test_each_offered_category_is_distinct_and_non_authorizing():
    discovery = _discovery(
        LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH,
        ("AC-1",),
        True,
    )
    bundle = _bundle(
        _decision(
            hints=MasterRouteHints(
                callsign="UAL100",
                typed_aircraft_id="AC-1",
                region_text="west sector",
            )
        ),
        target_candidate_aircraft_id="AC-1",
    )
    observation = observe_typed_aircraft_id("AC-1")
    report = _assess(
        bundle,
        typed_aircraft_observations=(observation,),
        callsign_observations=(observe_callsign(discovery),),
        discoveries=(discovery,),
        entity_hints=(LiveOpsEntityHint(entity_kind="AIRCRAFT", entity_id="AC-1", callsign="UAL100"),),
        population_observations=(observe_population(("AC-1",)),),
    )

    assert _kinds(report) == {
        *NON_AUTHORIZING_DECISION_TARGET_KINDS,
        KIND.AUTHORIZED_PERSISTED_RECOMMENDATION_ID,
        KIND.TRUSTED_DECISION_TARGET,
    }
    by_kind: dict[DecisionTargetMaterialKind, list[DecisionTargetMaterial]] = {}
    for item in report.materials:
        by_kind.setdefault(item.kind, []).append(item)
        assert item.authorizes_decision_invocation is False
    assert {item.source for item in by_kind[KIND.USER_PROVIDED_AIRCRAFT_IDENTIFIER]} == {
        "MasterTypedIdObservation"
    }
    assert {item.source for item in by_kind[KIND.CALLSIGN]} == {"MasterCallsignObservation"}
    assert {item.source for item in by_kind[KIND.MODEL_ROUTE_HINT]} == {"MasterRouteHints"}
    assert {item.field_name for item in by_kind[KIND.MODEL_ROUTE_HINT]} == {
        "callsign",
        "typed_aircraft_id",
        "region_text",
    }
    assert {item.source for item in by_kind[KIND.MASTER_COMPARISON_CANDIDATE]} == {
        "MasterInvocationAuthorization"
    }
    assert "LiveOpsDiscovery" in {item.source for item in by_kind[KIND.LIVE_OPS_DISCOVERY_CANDIDATE]}
    assert "LiveOpsEntityHint" in {item.source for item in by_kind[KIND.LIVE_OPS_DISCOVERY_CANDIDATE]}
    trusted = by_kind[KIND.TRUSTED_DECISION_TARGET]
    authorized = by_kind[KIND.AUTHORIZED_PERSISTED_RECOMMENDATION_ID]
    assert len(trusted) == 1 and trusted[0].available is False and trusted[0].identifier is None
    assert len(authorized) == 1 and authorized[0].available is False and authorized[0].identifier is None
    assert report.identity_ambiguous is False
    assert report.authenticated_identity_satisfied is False
    assert report.allowed_target_population_satisfied is False
    assert report.population_observation_aircraft_ids == ("AC-1",)
    assert report.callsign_is_aircraft_identity is False
    assert report.live_ops_discovery_is_trusted_target is False
    assert report.live_ops_entity_hint_is_trusted_target is False
    assert report.comparison_candidates_authorize_target is False
    assert report.model_text_selects_target is False
    assert report.principal_authenticated is False
    assert report.capability_authorizes_dispatch is False
    assert report.untrusted_region_text == "west sector"
    assert report.selected_aircraft_id is None
    assert discovery.identity_resolved is True
    assert discovery.selection_authorized is False
    assert observation is not None
    assert report.decision_invocation_count == 1


def test_route_hint_aircraft_id_is_not_a_user_provided_identifier():
    bundle = _bundle(
        _decision(hints=MasterRouteHints(typed_aircraft_id="AC-HINT")),
        target_candidate_aircraft_id="AC-HINT",
    )
    report = _assess(bundle)
    hint_fields = {
        item.field_name
        for item in report.materials
        if item.kind is KIND.MODEL_ROUTE_HINT
    }
    assert hint_fields == {"typed_aircraft_id"}
    assert KIND.USER_PROVIDED_AIRCRAFT_IDENTIFIER not in _kinds(report)
    assert report.decision_invocation_authorized is False


def test_ambiguous_population_selects_no_target():
    bundle = _bundle(
        _decision(
            hints=MasterRouteHints(
                typed_aircraft_id="AC-1",
                population_aircraft_ids=("AC-1", "AC-2"),
            )
        ),
        target_candidate_aircraft_id="AC-1",
    )
    report = _assess(bundle)

    assert MasterRouteGate.TARGET_AMBIGUOUS in report.gates
    assert report.identity_ambiguous is True
    assert report.selected_aircraft_id is None
    assert report.selected_recommendation_id is None
    assert report.decision_invocation_count == 1
    assert report.fanout_invocation_ids == ()
    assert report.allowed_target_population_satisfied is False
    assert all(item.authorizes_decision_invocation is False for item in report.materials)


def test_aircraft_and_recommendation_selectors_are_ambiguous():
    bundle = _bundle(
        _decision(
            hints=MasterRouteHints(recommendation_id="rec-1"),
            needs_persisted_evidence=True,
        ),
        target_candidate_recommendation_id="rec-1",
    )
    report = _assess(
        bundle,
        typed_aircraft_observations=(observe_typed_aircraft_id("AC-OTHER"),),
    )

    assert report.identity_ambiguous is True
    assert report.selected_aircraft_id is None
    assert report.selected_recommendation_id is None
    assert report.persisted_fanout_authorized is False
    assert report.needs_persisted_evidence is True
    assert TemporalScope.PERSISTED in report.temporal_scopes
    assert TemporalScope.CURRENT in report.temporal_scopes
    assert report.currentness_satisfied is False


def test_multiple_discovery_matches_do_not_authorize_any_aircraft():
    discovery = _discovery(
        LiveOpsDiscoveryStatus.MULTIPLE_SUPPORTED_MATCHES,
        ("AC-1", "AC-2"),
        False,
    )
    bundle = _bundle(_decision())
    report = _assess(bundle, discoveries=(discovery,), callsign_observations=(observe_callsign(discovery),))

    assert report.identity_ambiguous is True
    assert report.selected_aircraft_id is None
    assert report.callsign_is_aircraft_identity is False
    assert report.authenticated_identity_satisfied is False
    assert report.decision_invocation_count == 1


def test_stale_persisted_recommendation_is_not_current_or_authorized():
    bundle = _bundle(
        _decision(
            hints=MasterRouteHints(recommendation_id="rec-1"),
            needs_persisted_evidence=True,
        ),
        target_candidate_recommendation_id="rec-1",
    )
    report = _assess(bundle, earlier_result_recommendation_ids=("rec-1",))

    assert report.earlier_result_recommendation_ids == ("rec-1",)
    assert report.earlier_result_recommendations_are_current is False
    assert report.earlier_result_recommendations_authorize_fanout is False
    assert report.authorized_persisted_recommendation_ids == ()
    assert report.persisted_fanout_authorized is False
    assert report.persisted_fanout_invocation_count == 0
    assert report.fanout_invocation_ids == ()
    assert report.currentness_satisfied is False
    assert report.persisted_recommendation_ambiguous is False
    comparison = [
        item
        for item in report.materials
        if item.kind is KIND.MASTER_COMPARISON_CANDIDATE
    ]
    assert len(comparison) == 1
    assert comparison[0].identifier == "rec-1"
    assert comparison[0].authorizes_decision_invocation is False
    authorized = [
        item
        for item in report.materials
        if item.kind is KIND.AUTHORIZED_PERSISTED_RECOMMENDATION_ID
    ]
    assert authorized[0].identifier is None
    assert authorized[0].available is False
    assert report.decision_invocation_count == 1


def test_disagreeing_earlier_recommendation_ids_do_not_fan_out():
    bundle = _bundle(
        _decision(hints=MasterRouteHints(recommendation_id="rec-plan")),
        target_candidate_recommendation_id="rec-plan",
    )
    report = _assess(
        bundle,
        earlier_result_recommendation_ids=("rec-old", "rec-other"),
        population_observations=(observe_population(("AC-1", "AC-2", "AC-3")),),
    )

    assert report.persisted_recommendation_ambiguous is True
    assert report.identity_ambiguous is True
    assert report.selected_recommendation_id is None
    assert report.authorized_persisted_recommendation_ids == ()
    assert report.decision_invocation_count == 1
    assert report.persisted_fanout_invocation_count == 0
    assert report.fanout_invocation_ids == ()
    assert report.max_decision_steps == MAX_DECISION_STEPS
    assert len(report.population_observation_aircraft_ids) == 3


def test_population_bound_is_not_truncated_into_invocations():
    bundle = _bundle(_decision())
    with pytest.raises(ContractValidationError, match="population_aircraft_ids_exceed_bound"):
        _assess(
            bundle,
            earlier_result_recommendation_ids=tuple(f"rec-{index}" for index in range(9)),
        )


def test_two_decision_steps_stay_outside_this_assessment():
    with pytest.raises(ContractValidationError, match="decision_fanout_not_permitted"):
        _proposal(TEXT, _decision("dec-1"), _decision("dec-2"))


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"authorization_token": "forged-token"}, "authorization_token_not_accepted"),
        ({"authorization_signature": "forged-signature"}, "authorization_signature_not_accepted"),
        ({"authorization_certificate": "forged-certificate"}, "authorization_certificate_not_accepted"),
        ({"trusted_aircraft_id": "AC-1"}, "target_authorization_not_granted"),
        ({"trusted_recommendation_id": "rec-1"}, "target_authorization_not_granted"),
        ({"trusted_target": object()}, "target_authorization_not_granted"),
        ({"decision_target_trusted": True}, "target_authorization_not_granted"),
        ({"target_authorization_granted": True}, "target_authorization_not_granted"),
        ({"trusted_decision_target_available": True}, "target_authorization_not_granted"),
        ({"decision_invocation_authorized": True}, "target_authorization_not_granted"),
        ({"comparison_candidates_authorize_target": True}, "target_authorization_not_granted"),
        ({"execution_authority": "GRANTED"}, "execution_authority_not_granted"),
        ({"production_authority": MasterProductionAuthority.PRODUCTION}, "production_authority_not_issued"),
        ({"source_authenticity": "AUTHENTICATED"}, "source_authenticity_not_established"),
        ({"request_coverage": "ESTABLISHED"}, "request_coverage_not_established"),
        ({"dispatch_permitted": True}, "execution_not_granted"),
        ({"executable": True}, "execution_not_granted"),
        ({"ready_to_execute": True}, "decision_step_not_executable"),
        ({"end_to_end_fulfilled": True}, "end_to_end_fulfillment_not_granted"),
        ({"additional_decision_invocations": 1}, "decision_fanout_not_permitted"),
        ({"max_decision_steps": 2}, "decision_fanout_not_permitted"),
        ({"persisted_fanout_authorized": True}, "persisted_evidence_not_authorized"),
        ({"persisted_fanout_invocation_count": 3}, "persisted_evidence_not_authorized"),
        ({"persisted_evidence_authorized": True}, "persisted_evidence_not_authorized"),
        ({"persisted_recommendations_current": True}, "persisted_data_is_not_current"),
        ({"capability_authorizes_dispatch": True}, "capability_consumption_not_dispatch_permission"),
        ({"authenticated_principal": "caller-supplied-user"}, "principal_not_authenticated"),
        ({"principal_authenticated": True}, "principal_not_authenticated"),
        ({"user_text_copied_into_request": True}, "user_text_not_a_decision_target"),
        ({"model_text_selects_target": True}, "model_text_cannot_select_target"),
        ({"callsign_is_aircraft_identity": True}, "callsign_not_authenticated_aircraft"),
        ({"live_ops_discovery_is_trusted_target": True}, "live_ops_discovery_not_trusted_target"),
        ({"live_ops_entity_hint_is_trusted_target": True}, "live_ops_entity_not_trusted_target"),
        ({"supplied_evidence": NOT_CURRENT_LIMITATION}, "caller_evidence_cannot_satisfy_gate"),
        ({"caller_evidence_satisfies_gate": True}, "caller_evidence_cannot_satisfy_gate"),
        ({"risk_level": StoredRiskLevel.LOW}, "absent_risk_is_not_low_risk"),
        ({"risk_level": "LOW"}, "absent_risk_is_not_low_risk"),
        (
            {
                "risk_level": RiskAbsentClaim(
                    evidence_ref="de-1",
                    evidence_scope=TemporalScope.CURRENT,
                )
            },
            "absent_risk_is_not_low_risk",
        ),
        ({"absent_risk_is_low_risk": True}, "absent_risk_is_not_low_risk"),
        ({"authenticated_identity_satisfied": True}, "target_authorization_not_granted"),
        ({"currentness_satisfied": True}, "target_authorization_not_granted"),
        ({"allowed_target_population_satisfied": True}, "target_authorization_not_granted"),
    ],
)
def test_target_forgery_and_authority_promotion_are_rejected(kwargs: dict[str, object], code: str):
    bundle = _bundle(_decision(), target_candidate_aircraft_id="AC-1")
    with pytest.raises(ContractValidationError, match=code):
        _assess(bundle, **kwargs)


def test_object_identity_does_not_authenticate_a_principal():
    observation = observe_typed_aircraft_id("AC-1")
    bundle = _bundle(_decision(), target_candidate_aircraft_id="AC-1")
    with pytest.raises(ContractValidationError, match="principal_not_authenticated"):
        _assess(
            bundle,
            typed_aircraft_observations=(observation,),
            authenticated_principal=observation,
        )


def test_foreign_invocation_is_rejected():
    bundle = _bundle(_live(), _decision())
    decision_descriptor = _descriptor(bundle)
    live_descriptor = _descriptor(bundle, MasterSpecialist.LIVE_OPS)
    with pytest.raises(ContractValidationError, match="invocation_not_in_plan"):
        assess_master_decision_execution_readiness(
            bundle,
            replace(decision_descriptor, invocation_id="foreign-invocation"),
        )
    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        assess_master_decision_execution_readiness(
            bundle,
            replace(decision_descriptor, master_request_id="other-master-request"),
        )
    with pytest.raises(ContractValidationError, match="specialist_mismatch"):
        assess_master_decision_execution_readiness(bundle, live_descriptor)
    with pytest.raises(ContractValidationError, match="invocation_binding_mismatch"):
        assess_master_decision_execution_readiness(
            bundle,
            replace(decision_descriptor, invocation_id=live_descriptor.invocation_id),
        )


def test_discovery_selection_cannot_be_preauthorized():
    with pytest.raises(ContractValidationError, match="selection_not_authorized"):
        _discovery(
            LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH,
            ("AC-1",),
            True,
        ).__class__(
            callsign="UAL100",
            status=LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH,
            aircraft_ids=("AC-1",),
            evidence_tool_call_id="discovery-tool-1",
            reference_epoch=1,
            identity_resolved=True,
            selection_authorized=True,
        )


def test_replace_cannot_promote_readiness():
    report = _assess(_bundle(_decision()))
    with pytest.raises(ContractValidationError, match="target_authorization_not_granted"):
        replace(report, trusted_decision_target_available=True)
    with pytest.raises(ContractValidationError, match="decision_fanout_not_permitted"):
        replace(report, decision_invocation_count=2)
    with pytest.raises(ContractValidationError, match="decision_fanout_not_permitted"):
        replace(report, fanout_invocation_ids=("rec-1",))
    with pytest.raises(ContractValidationError, match="persisted_data_is_not_current"):
        replace(report, earlier_result_recommendations_are_current=True)
    with pytest.raises(ContractValidationError, match="absent_risk_is_not_low_risk"):
        replace(report, recorded_risk_level="LOW")
    with pytest.raises(ContractValidationError, match="decision_target_requirement_unsatisfied"):
        replace(report, unmet_requirements=())
    with pytest.raises(ContractValidationError, match="execution_authority_not_granted"):
        replace(report, execution_authority="GRANTED")
    with pytest.raises(ContractValidationError, match="target_authorization_not_granted"):
        DecisionTargetMaterial(
            kind=KIND.USER_PROVIDED_AIRCRAFT_IDENTIFIER,
            source="MasterTypedIdObservation",
            field_name="aircraft_id",
            identifier="AC-1",
            authorizes_decision_invocation=True,
        )
    with pytest.raises(ContractValidationError, match="target_authorization_not_granted"):
        DecisionTargetMaterial(
            kind=KIND.TRUSTED_DECISION_TARGET,
            source="unavailable",
            field_name="unavailable",
            identifier="AC-1",
            available=False,
        )


def test_module_does_not_construct_a_decision_runtime():
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "wilvor_ai.decision_specialist" not in imported
    assert "wilvor_ai.decision_tools" not in imported
    assert "wilvor_ai.decision_specialist_runtime_contracts" not in imported
    assert "DecisionSpecialistRequest(" not in source
    assert "DecisionSpecialist(" not in source
    assert "DecisionToolsRuntime(" not in source
