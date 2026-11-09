"""Master-to-Decision binding stays blocked. No Decision target is authorized."""

from __future__ import annotations

import ast
from dataclasses import fields, replace
from pathlib import Path

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.decision_answer_renderer import render_decision_verification
from wilvor_ai.decision_evidence_verifier import verify_decision_evidence
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionCollectionPartialReason,
    DecisionRuntimeErrorCode,
    DecisionSpecialistRequest,
    DecisionSpecialistRunResult,
    DecisionSpecialistRunStatus,
    DecisionTargetMode,
)
from wilvor_ai.live_ops_public_contracts import (
    LiveOpsRunResult,
    LiveOpsTrustedContext,
    entity_hint_from_result,
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
from wilvor_ai.master_parallel_contracts import (
    MasterExecutionPreflightState,
    MasterParallelContractBundle,
)
from wilvor_ai.master_routing import (
    MasterHintTrust,
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRouteHints,
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.master_specialist_adapters import describe_planned_adapters
from wilvor_ai.master_decision_binding import (
    MASTER_DECISION_BINDING_EXPLANATION,
    MasterDecisionBinding,
    MasterDecisionBindingLimitation,
    MasterDecisionBindingRefusal,
    MasterDecisionBindingStatus,
    prepare_master_decision_binding,
)
from wilvor_ai.specialist_contracts import VerifierOutcome
from tests.fixtures.live_ops_public_examples import (
    SYNTHETIC_CONVERSATION_ID,
    SYNTHETIC_CORRELATION_ID,
    SYNTHETIC_REQUEST_ID,
)
from tests.fixtures.ma4_specialist_evidence_examples import (
    decision_offline_consistent_run,
    decision_wrong_aircraft_case,
    live_ops_context,
    live_ops_request,
    live_ops_unique_callsign_result,
)


TEXT = "Which recorded risk applies to the aircraft the application already identified?"
MODEL_TEXT = "The model says use aircraft N777WX for this risk question."
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
RECOMMENDATION = AgentCapability.EXPLAIN_EXISTING_RECOMMENDATIONS
REPO_ROOT = Path(__file__).resolve().parents[2]
BINDING_MODULE = REPO_ROOT / "functions" / "shared" / "wilvor_ai" / "master_decision_binding.py"
FORBIDDEN_IMPORTS = {
    "anthropic",
    "boto3",
    "botocore",
    "langgraph",
    "openai",
    "wilvor_ai.decision_context",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.decision_tools",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.master_orchestration",
    "wilvor_ai.persisted_airport_evidence",
    "wilvor_operational",
}
FORBIDDEN_CALLS = {
    "DecisionSpecialist",
    "DecisionSpecialistRequest",
    "DecisionToolsAdapter",
    "DecisionToolsRuntime",
}


def _context(text: str = TEXT, **overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-decision-bind-1",
        "correlation_id": "corr-decision-bind-1",
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


def _bundle(
    *steps: MasterProposedStep,
    text: str = TEXT,
    evidence: tuple[MasterEvidenceCorroborationInput, ...] = (),
    **context_overrides: object,
) -> MasterParallelContractBundle:
    context = _context(text, **context_overrides)
    decision = authorize_route(_proposal(text, *steps), context)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    assert decision.plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    return MasterParallelContractBundle(
        context=context,
        plan=decision.plan,
        evidence_inputs=evidence,
    )


def _descriptor(bundle: MasterParallelContractBundle):
    matches = [
        item
        for item in describe_planned_adapters(bundle).descriptors
        if item.specialist is MasterSpecialist.DECISION
    ]
    assert len(matches) == 1
    return matches[0]


def _step(bundle: MasterParallelContractBundle, specialist: MasterSpecialist):
    matches = [item for item in bundle.plan.steps if item.invocation.specialist is specialist]
    assert len(matches) == 1
    return matches[0]


def _evidence(bundle: MasterParallelContractBundle, specialist: MasterSpecialist, artifact: object):
    return MasterEvidenceCorroborationInput(
        admission_input=MasterEvidenceAdmissionInput(
            authorization=_step(bundle, specialist).invocation,
            artifact=artifact,
        )
    )


def _with_evidence(
    bundle: MasterParallelContractBundle,
    *artifacts: tuple[MasterSpecialist, object],
) -> MasterParallelContractBundle:
    return MasterParallelContractBundle(
        context=bundle.context,
        plan=bundle.plan,
        evidence_inputs=tuple(_evidence(bundle, specialist, artifact) for specialist, artifact in artifacts),
    )


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _materials(value: object, seen: set[int] | None = None):
    if seen is None:
        seen = set()
    marker = id(value)
    if marker in seen:
        return
    seen.add(marker)
    yield value
    if hasattr(value, "__dataclass_fields__"):
        for item in fields(value):
            yield from _materials(getattr(value, item.name), seen)
    elif isinstance(value, tuple):
        for item in value:
            yield from _materials(item, seen)


def _strings(value: object) -> set[str]:
    return {item for item in _materials(value) if isinstance(item, str)}


def _called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    called: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            called.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
    return called


def _provider_failed() -> DecisionSpecialistRunResult:
    return DecisionSpecialistRunResult(
        status=DecisionSpecialistRunStatus.PROVIDER_FAILED,
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
        unsupported_reason=None,
        runtime_errors=(DecisionRuntimeErrorCode.PROVIDER_EXCEPTION,),
        collection_partial_reason=DecisionCollectionPartialReason.PERSISTED_FANOUT_CAP,
        validation_feedback=(),
        provider_turn_count=1,
        executed_tool_call_count=0,
        executed_persisted_call_count=0,
        terminal_kind=None,
    )


def test_valid_decision_plan_is_blocked():
    bundle = _bundle(_decision(), target_candidate_aircraft_id="ac-1")
    descriptor = _descriptor(bundle)
    step = _step(bundle, MasterSpecialist.DECISION)
    binding = prepare_master_decision_binding(bundle, descriptor)

    assert binding.status is MasterDecisionBindingStatus.BLOCKED_MISSING_TRUSTED_TARGET
    assert (
        binding.refusal
        is MasterDecisionBindingRefusal.INDEPENDENTLY_TRUSTED_TARGET_AUTHORIZATION_MISSING
    )
    assert binding.explanation == MASTER_DECISION_BINDING_EXPLANATION
    assert binding.limitations == tuple(MasterDecisionBindingLimitation)
    assert binding.purpose is step.invocation.purpose
    assert binding.requirement is step.invocation.requirement
    assert binding.master_request_id == bundle.context.master_request_id
    assert binding.correlation_id == bundle.context.correlation_id
    assert binding.invocation_id == step.invocation.invocation_id
    assert binding.specialist_request_id == step.invocation.specialist_request_id
    assert binding.target_authorization_granted is False
    assert step.target_authorization_granted is False
    assert binding.decision_target_trusted is False
    assert binding.dispatch_permitted is False
    assert binding.executable is False
    assert binding.ready_to_execute is False
    assert binding.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert binding.production_authority is MasterProductionAuthority.DENIED
    assert binding.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert MasterRouteGate.TRUSTED_IDENTITY_VALIDATION_PENDING in binding.gates
    assert MasterRouteGate.CURRENTNESS_VALIDATION_PENDING in binding.gates
    assert binding.hint_trust is MasterHintTrust.UNTRUSTED
    assert binding.depends_on == ()
    assert "bundle" not in MasterDecisionBinding.__dataclass_fields__
    assert "evidence_inputs" not in MasterDecisionBinding.__dataclass_fields__
    assert "supplied_run_status" not in MasterDecisionBinding.__dataclass_fields__
    assert callable(binding) is False
    assert not hasattr(binding, "dispatch")
    assert not hasattr(binding, "run")
    with pytest.raises(ContractValidationError, match="target_authorization_not_granted"):
        replace(step, target_authorization_granted=True)


def test_comparison_candidates_do_not_authorize_a_target():
    bundle = _bundle(_decision(), target_candidate_aircraft_id="ac-1")
    binding = prepare_master_decision_binding(bundle, _descriptor(bundle))

    assert binding.comparison_candidate_aircraft_id == "ac-1"
    assert binding.comparison_candidate_recommendation_id is None
    assert binding.trusted_aircraft_id is None
    assert binding.trusted_recommendation_id is None
    assert binding.comparison_candidates_authorize_target is False
    assert binding.decision_target_trusted is False
    assert (
        MasterDecisionBindingLimitation.COMPARISON_CANDIDATE_IS_NOT_A_TRUSTED_TARGET
        in binding.limitations
    )
    assert "user_text" not in MasterDecisionBinding.__dataclass_fields__
    assert "aircraft_id" not in MasterDecisionBinding.__dataclass_fields__
    assert "recommendation_id" not in MasterDecisionBinding.__dataclass_fields__


def test_wrong_specialist_invocation_step_and_request_are_rejected():
    bundle = _bundle(_live(), _decision(depends_on=("live-1",)))
    decision = _descriptor(bundle)
    live = next(
        item
        for item in describe_planned_adapters(bundle).descriptors
        if item.specialist is MasterSpecialist.LIVE_OPS
    )

    with pytest.raises(ContractValidationError, match="specialist_mismatch"):
        prepare_master_decision_binding(bundle, live)
    with pytest.raises(ContractValidationError, match="specialist_mismatch"):
        prepare_master_decision_binding(
            bundle,
            replace(decision, specialist=MasterSpecialist.LIVE_OPS),
        )
    with pytest.raises(ContractValidationError, match="specialist_mismatch"):
        prepare_master_decision_binding(
            bundle,
            replace(decision, invocation_id=live.invocation_id, step_id=live.step_id),
        )
    with pytest.raises(ContractValidationError, match="invocation_not_in_plan"):
        prepare_master_decision_binding(bundle, replace(decision, invocation_id="inv-not-planned"))
    with pytest.raises(ContractValidationError, match="invocation_binding_mismatch"):
        prepare_master_decision_binding(bundle, replace(decision, step_id="other-step"))
    with pytest.raises(ContractValidationError, match="specialist_request_id_mismatch"):
        prepare_master_decision_binding(
            bundle,
            replace(decision, specialist_request_id="other-specialist-request"),
        )
    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        prepare_master_decision_binding(
            bundle,
            replace(decision, master_request_id="other-master-request"),
        )
    with pytest.raises(ContractValidationError, match="invocation_binding_mismatch"):
        prepare_master_decision_binding(
            bundle,
            replace(decision, purpose=MasterPurpose.DISCOVERY),
        )


def test_callsign_does_not_become_aircraft_identity():
    bundle = _bundle(
        _decision(hints=MasterRouteHints(callsign="UAL123")),
        target_candidate_aircraft_id="N12345",
    )
    binding = prepare_master_decision_binding(bundle, _descriptor(bundle))

    assert binding.untrusted_callsign == "UAL123"
    assert binding.comparison_candidate_aircraft_id == "N12345"
    assert binding.trusted_aircraft_id is None
    assert binding.callsign_is_aircraft_identity is False
    assert binding.decision_target_trusted is False
    assert binding.untrusted_callsign != binding.comparison_candidate_aircraft_id
    assert (
        MasterDecisionBindingLimitation.CALLSIGN_IS_NOT_AIRCRAFT_IDENTITY in binding.limitations
    )


def _live_ops_sources() -> LiveOpsAdmissionSources:
    result = live_ops_unique_callsign_result()
    hint = entity_hint_from_result(result, 0)
    return LiveOpsAdmissionSources(
        request=replace(live_ops_request(), entity_hints=(hint,)),
        context=live_ops_context(),
        result=result,
    )


def test_explicit_dependency_does_not_retain_live_ops_evidence():
    planned = _bundle(_live(), _decision(depends_on=("live-1",)))
    sources = _live_ops_sources()
    bundle = _with_evidence(planned, (MasterSpecialist.LIVE_OPS, sources))
    descriptor = _descriptor(planned)
    binding = prepare_master_decision_binding(bundle, descriptor)
    plain = prepare_master_decision_binding(planned, descriptor)
    retained = _strings(binding)

    assert binding == plain
    assert binding.depends_on == ("live-1",)
    assert binding.planning.depends_on == ("live-1",)
    assert sources.result.verified_entity_refs[0].entity_id == "AC-1"
    assert "AC-1" not in retained
    assert "UAL123" not in retained
    assert SYNTHETIC_CORRELATION_ID not in retained
    assert SYNTHETIC_REQUEST_ID not in retained
    assert SYNTHETIC_CONVERSATION_ID not in retained
    assert binding.trusted_aircraft_id is None
    assert binding.live_ops_discovery_is_trusted_target is False
    assert binding.live_ops_entity_hint_is_trusted_target is False
    assert binding.status is MasterDecisionBindingStatus.BLOCKED_MISSING_TRUSTED_TARGET
    assert not any(isinstance(item, LiveOpsTrustedContext) for item in _materials(binding))
    assert not any(isinstance(item, LiveOpsAdmissionSources) for item in _materials(binding))
    assert not any(isinstance(item, LiveOpsRunResult) for item in _materials(binding))
    assert not any(isinstance(item, MasterParallelContractBundle) for item in _materials(binding))
    assert sources.context is not binding
    assert sources.context.tables not in _materials(binding)
    assert (
        MasterDecisionBindingLimitation.LIVE_OPS_ENTITY_REF_IS_NOT_A_TRUSTED_TARGET
        in binding.limitations
    )
    assert (
        MasterDecisionBindingLimitation.LIVE_OPS_DISCOVERY_IS_NOT_A_TRUSTED_TARGET
        in binding.limitations
    )


def test_unrelated_live_ops_evidence_and_trusted_context_are_not_retained():
    planned = _bundle(_live(), _decision())
    sources = _live_ops_sources()
    bundle = _with_evidence(planned, (MasterSpecialist.LIVE_OPS, sources))
    descriptor = _descriptor(planned)
    binding = prepare_master_decision_binding(bundle, descriptor)

    assert _step(planned, MasterSpecialist.DECISION).depends_on == ()
    assert binding.depends_on == ()
    assert binding == prepare_master_decision_binding(planned, descriptor)
    assert isinstance(sources.context, LiveOpsTrustedContext)
    assert sources.context.correlation_id == SYNTHETIC_CORRELATION_ID
    assert "AC-1" not in _strings(binding)
    assert SYNTHETIC_CORRELATION_ID not in _strings(binding)
    assert sources.context.tables not in _materials(binding)
    assert sources.context.clock not in _materials(binding)
    assert not any(isinstance(item, LiveOpsTrustedContext) for item in _materials(binding))
    assert binding.live_ops_discovery_is_trusted_target is False
    assert binding.status is MasterDecisionBindingStatus.BLOCKED_MISSING_TRUSTED_TARGET


def test_model_text_cannot_select_a_target():
    bundle = _bundle(
        _decision(hints=MasterRouteHints(typed_aircraft_id="N888YZ")),
        text=MODEL_TEXT,
    )
    binding = prepare_master_decision_binding(bundle, _descriptor(bundle))

    assert "N777WX" in bundle.context.canonical_user_text
    assert binding.untrusted_typed_aircraft_id == "N888YZ"
    assert binding.comparison_candidate_aircraft_id is None
    assert binding.trusted_aircraft_id is None
    assert binding.model_text_selects_target is False
    assert binding.user_text_copied_into_request is False
    assert "N777WX" not in _strings(binding)
    assert bundle.context.canonical_user_text not in _strings(binding)
    assert "user_text" not in MasterDecisionBinding.__dataclass_fields__
    assert (
        MasterDecisionBindingLimitation.MODEL_TEXT_CANNOT_SELECT_A_TARGET in binding.limitations
    )
    assert (
        MasterDecisionBindingLimitation.USER_TEXT_IS_NOT_A_DECISION_TARGET in binding.limitations
    )
    assert (
        MasterDecisionBindingLimitation.ROUTE_HINT_IS_NOT_A_TRUSTED_TARGET in binding.limitations
    )


def test_expected_recommendation_does_not_authorize_persisted_evidence():
    bundle = _bundle(
        _decision(capability=RECOMMENDATION, needs_persisted_evidence=True),
        target_candidate_recommendation_id="rec-1",
    )
    binding = prepare_master_decision_binding(bundle, _descriptor(bundle))

    assert binding.comparison_candidate_recommendation_id == "rec-1"
    assert binding.comparison_candidate_aircraft_id is None
    assert binding.needs_persisted_evidence is True
    assert binding.persisted_evidence_authorized is False
    assert binding.persisted_data_is_necessarily_current is False
    assert binding.absent_risk_is_low_risk is False
    assert binding.trusted_recommendation_id is None
    assert binding.status is MasterDecisionBindingStatus.BLOCKED_MISSING_TRUSTED_TARGET
    assert (
        MasterDecisionBindingLimitation.EXPECTED_RECOMMENDATION_DOES_NOT_AUTHORIZE_PERSISTED_EVIDENCE
        in binding.limitations
    )
    assert (
        MasterDecisionBindingLimitation.PERSISTED_DATA_IS_NOT_NECESSARILY_CURRENT
        in binding.limitations
    )
    assert MasterDecisionBindingLimitation.ABSENT_RISK_DATA_IS_NOT_LOW_RISK in binding.limitations
    assert (
        MasterDecisionBindingLimitation.PERSISTED_EVIDENCE_LIMITATION_IS_DISTINCT
        in binding.limitations
    )


def test_forged_authorization_fields_fail_closed():
    bundle = _bundle(_decision(), target_candidate_aircraft_id="ac-1")
    descriptor = _descriptor(bundle)
    blocked = prepare_master_decision_binding(bundle, descriptor)
    forged = (
        ("target_authorization_granted", True, "target_authorization_not_granted"),
        ("decision_target_trusted", True, "target_authorization_not_granted"),
        ("trusted_aircraft_id", "ac-1", "target_authorization_not_granted"),
        ("trusted_recommendation_id", "rec-1", "target_authorization_not_granted"),
        ("authorization_token", "forged-token", "authorization_token_not_accepted"),
        ("authorization_signature", "forged-signature", "authorization_signature_not_accepted"),
        (
            "authorization_certificate",
            "forged-certificate",
            "authorization_certificate_not_accepted",
        ),
        ("execution_authority", "GRANTED", "execution_authority_not_granted"),
        (
            "production_authority",
            MasterProductionAuthority.PRODUCTION,
            "production_authority_not_issued",
        ),
        ("source_authenticity", "ESTABLISHED", "source_authenticity_not_established"),
        ("dispatch_permitted", True, "execution_not_granted"),
        ("executable", True, "execution_not_granted"),
        ("ready_to_execute", True, "decision_step_not_executable"),
        ("persisted_evidence_authorized", True, "persisted_evidence_not_authorized"),
        ("comparison_candidates_authorize_target", True, "target_authorization_not_granted"),
        ("callsign_is_aircraft_identity", True, "callsign_not_authenticated_aircraft"),
        ("user_text_copied_into_request", True, "user_text_not_a_decision_target"),
        ("model_text_selects_target", True, "model_text_cannot_select_target"),
        ("live_ops_entity_hint_is_trusted_target", True, "live_ops_entity_not_trusted_target"),
        ("completed_means_claims_verified", True, "completed_does_not_verify_claims"),
        ("absent_risk_is_low_risk", True, "absent_risk_is_not_low_risk"),
        ("persisted_data_is_necessarily_current", True, "persisted_data_is_not_current"),
        (
            "decision_result_is_authenticated_source_evidence",
            True,
            "decision_result_not_source_evidence",
        ),
        (
            "internal_verification_is_source_authentication",
            True,
            "decision_verifier_not_source_authentication",
        ),
    )
    for name, value, code in forged:
        with pytest.raises(ContractValidationError, match=code):
            prepare_master_decision_binding(bundle, descriptor, **{name: value})
        with pytest.raises(ContractValidationError, match=code):
            replace(blocked, **{name: value})
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(blocked, status="AUTHORIZED")  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="decision_binding_limitation_missing"):
        replace(blocked, limitations=())
    with pytest.raises(ContractValidationError, match="invalid_decision_binding_explanation"):
        replace(blocked, explanation="Decision target authorized.")


def test_identity_replacement_stays_blocked():
    bundle = _bundle(_decision(), target_candidate_aircraft_id="ac-1")
    descriptor = _descriptor(bundle)
    binding = prepare_master_decision_binding(bundle, descriptor)
    replacements = (
        ("master_request_id", "other-master-request"),
        ("correlation_id", "other-correlation"),
        ("application_request_time_utc", "2026-02-01T00:00:01Z"),
        ("step_id", "other-step"),
        ("invocation_id", "other-invocation"),
        ("specialist_request_id", "other-specialist-request"),
        ("comparison_candidate_aircraft_id", "ac-other"),
        ("depends_on", ("live-1",)),
    )
    for name, value in replacements:
        with pytest.raises(ContractValidationError, match="invocation_binding_mismatch"):
            replace(binding, **{name: value})
        with pytest.raises(ContractValidationError, match="invocation_binding_mismatch"):
            replace(binding, planning=replace(binding.planning, **{name: value}))
    with pytest.raises(ContractValidationError, match="specialist_request_id_mismatch"):
        replace(
            binding,
            descriptor=replace(descriptor, specialist_request_id="other-specialist-request"),
        )
    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        replace(binding, descriptor=replace(descriptor, master_request_id="other-master-request"))
    with pytest.raises(ContractValidationError, match="invocation_binding_mismatch"):
        replace(binding, descriptor=replace(descriptor, invocation_id="other-invocation"))
    assert binding.status is MasterDecisionBindingStatus.BLOCKED_MISSING_TRUSTED_TARGET
    assert binding.decision_target_trusted is False
    assert binding.dispatch_permitted is False
    assert binding.master_request_id == bundle.context.master_request_id
    assert binding.comparison_candidate_aircraft_id == "ac-1"
    assert binding.trusted_aircraft_id is None


def test_no_trusted_decision_request_or_tools_runtime_is_constructed():
    bundle = _bundle(_decision(), target_candidate_aircraft_id="ac-1")
    binding = prepare_master_decision_binding(bundle, _descriptor(bundle))
    values = [getattr(binding, item.name) for item in fields(binding)]

    assert not isinstance(binding, DecisionSpecialistRequest)
    assert all(not isinstance(value, DecisionSpecialistRequest) for value in values)
    assert "DecisionSpecialistRequest" not in prepare_master_decision_binding.__globals__
    assert "DecisionSpecialist" not in prepare_master_decision_binding.__globals__
    assert "DecisionToolsRuntime" not in prepare_master_decision_binding.__globals__
    assert FORBIDDEN_IMPORTS.isdisjoint(_imported_modules(BINDING_MODULE))
    assert FORBIDDEN_CALLS.isdisjoint(_called_names(BINDING_MODULE))
    source = BINDING_MODULE.read_text(encoding="utf-8")
    assert "boto3" not in source
    assert "langgraph" not in source
    assert "AthenaExecutor" not in source
    assert "evidence_inputs" not in source
    assert not any(isinstance(item, DecisionSpecialistRunResult) for item in _materials(binding))
    assert not any(isinstance(item, MasterParallelContractBundle) for item in _materials(binding))


def _assert_result_not_retained(
    planned: MasterParallelContractBundle,
    artifact: DecisionSpecialistRunResult,
) -> MasterDecisionBinding:
    descriptor = _descriptor(planned)
    binding = prepare_master_decision_binding(
        _with_evidence(planned, (MasterSpecialist.DECISION, artifact)),
        descriptor,
    )
    assert binding == prepare_master_decision_binding(planned, descriptor)
    assert binding.status is MasterDecisionBindingStatus.BLOCKED_MISSING_TRUSTED_TARGET
    assert artifact not in _materials(binding)
    assert artifact.aircraft_id not in _strings(binding) or artifact.aircraft_id == binding.comparison_candidate_aircraft_id
    assert "supplied_run_status" not in MasterDecisionBinding.__dataclass_fields__
    assert "supplied_verification_outcome" not in MasterDecisionBinding.__dataclass_fields__
    return binding


def test_completed_passed_result_is_not_source_evidence():
    planned = _bundle(_decision(), target_candidate_aircraft_id="ac-1")
    completed = decision_offline_consistent_run(aircraft_id="ac-1")
    binding = _assert_result_not_retained(planned, completed)

    assert completed.status is DecisionSpecialistRunStatus.COMPLETED
    assert completed.verification is not None
    assert completed.verification.outcome is VerifierOutcome.PASSED
    assert completed.collection_partial_reason is None
    assert binding.completed_means_claims_verified is False
    assert binding.internal_verification_is_source_authentication is False
    assert binding.decision_result_is_authenticated_source_evidence is False
    assert binding.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert binding.trusted_aircraft_id is None
    assert binding.comparison_candidate_aircraft_id == "ac-1"
    assert binding.comparison_candidates_authorize_target is False
    assert (
        MasterDecisionBindingLimitation.COMPLETED_DOES_NOT_MEAN_CLAIMS_VERIFIED
        in binding.limitations
    )
    assert (
        MasterDecisionBindingLimitation.DECISION_RESULT_IS_NOT_AUTHENTICATED_SOURCE_EVIDENCE
        in binding.limitations
    )
    assert (
        MasterDecisionBindingLimitation.INTERNAL_VERIFICATION_IS_NOT_SOURCE_AUTHENTICATION
        in binding.limitations
    )


def test_partial_provider_failure_and_verification_failure_stay_distinct():
    completed = decision_offline_consistent_run()
    partial_run = replace(
        completed,
        status=DecisionSpecialistRunStatus.PARTIAL,
        collection_partial_reason=DecisionCollectionPartialReason.PERSISTED_FANOUT_BUDGET,
    )
    failed_run = _provider_failed()
    wrong_claims, wrong_bindings = decision_wrong_aircraft_case()
    failed_verification = verify_decision_evidence(wrong_claims, wrong_bindings)
    rendered_failure = render_decision_verification(failed_verification)
    planned = _bundle(_decision(), target_candidate_aircraft_id="ac-1")
    partial = _assert_result_not_retained(planned, partial_run)
    failed = _assert_result_not_retained(planned, failed_run)

    assert completed.status is DecisionSpecialistRunStatus.COMPLETED
    assert partial_run.status is DecisionSpecialistRunStatus.PARTIAL
    assert failed_run.status is DecisionSpecialistRunStatus.PROVIDER_FAILED
    assert partial_run.status is not completed.status
    assert failed_run.status is not partial_run.status
    assert partial_run.collection_partial_reason is DecisionCollectionPartialReason.PERSISTED_FANOUT_BUDGET
    assert completed.collection_partial_reason is None
    assert (
        failed_run.collection_partial_reason
        is DecisionCollectionPartialReason.PERSISTED_FANOUT_CAP
    )
    assert failed_run.runtime_errors == (DecisionRuntimeErrorCode.PROVIDER_EXCEPTION,)
    assert failed_run.verification is None
    assert partial_run.verification is not None
    assert partial_run.verification.outcome is VerifierOutcome.PASSED
    assert failed_verification.outcome is VerifierOutcome.FAILED
    assert failed_verification.outcome is not partial_run.verification.outcome
    assert rendered_failure.outcome is not None
    assert failed_verification not in _materials(partial)
    assert failed_verification not in _materials(failed)
    assert partial.status is failed.status
    assert partial.status is MasterDecisionBindingStatus.BLOCKED_MISSING_TRUSTED_TARGET
    assert MasterDecisionBindingLimitation.PARTIAL_IS_NOT_COMPLETED in partial.limitations
    assert MasterDecisionBindingLimitation.PROVIDER_FAILURE_IS_NOT_PARTIAL in failed.limitations
    assert (
        MasterDecisionBindingLimitation.VERIFICATION_FAILURE_IS_NOT_RUN_SUCCESS
        in failed.limitations
    )
    assert partial.trusted_aircraft_id is None
    assert failed.trusted_aircraft_id is None


def test_master_preflight_still_denies_dispatch():
    planned = _bundle(_decision(), target_candidate_aircraft_id="ac-1")
    bundle = _with_evidence(
        planned,
        (MasterSpecialist.DECISION, decision_offline_consistent_run(aircraft_id="ac-1")),
    )
    before = evaluate_execution_preflight(bundle)
    binding = prepare_master_decision_binding(bundle, _descriptor(bundle))
    after = evaluate_execution_preflight(bundle)

    assert before.dispatch_permitted is False
    assert after.dispatch_permitted is False
    assert before.report.executable is False
    assert after.report.executable is False
    assert before.stops_before_execution_authorization is True
    assert after.stops_before_execution_authorization is True
    assert before.report.state is MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION
    assert after.report.state is MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION
    assert after.report.missing_provenance == before.report.missing_provenance
    assert binding.dispatch_permitted is False
    assert binding.executable is False
    assert bundle.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_binding_is_deterministic():
    bundle = _bundle(
        _decision(hints=MasterRouteHints(callsign="UAL123", population_aircraft_ids=("ac-9",))),
        target_candidate_aircraft_id="ac-1",
    )
    first = prepare_master_decision_binding(bundle, _descriptor(bundle))
    second = prepare_master_decision_binding(bundle, _descriptor(bundle))

    assert first == second
    assert first.untrusted_population_aircraft_ids == ("ac-9",)
    assert first.trusted_aircraft_id is None
    assert first.comparison_candidates_authorize_target is False
