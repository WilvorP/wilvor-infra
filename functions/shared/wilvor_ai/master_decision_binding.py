"""Non-executing Master-to-Decision binding boundary.

A Master plan can name one Decision invocation. Naming it does not authorize
a Decision target. ``MasterRouteStepPlan.target_authorization_granted`` is
always false, and this module does not invent a substitute flag, token,
signature, or certificate. An independently trusted target authorization
mechanism is not implemented; designing one requires separate human review.

The returned binding keeps the validated planning identity for that one
Decision step: request ids, purpose, requirement, comparison candidates,
route hints, gates, and step dependencies. It does not keep the parallel
bundle, evidence inputs, a Live Ops runtime context, or a specialist result.

``expected_aircraft_id`` and ``expected_recommendation_id`` are comparison
candidates. User text, callsigns, and route hints are not targets. A
``depends_on`` entry is a planned step id. It does not import a Live Ops
discovery and it does not authorize a target.

``DecisionSpecialistRunStatus.COMPLETED`` means final claims were reached
and the deterministic verifier and renderer ran. It does not mean the claims
passed. ``PARTIAL``, provider failure, verification failure, and a skipped
persisted fanout stay distinct on the specialist result. This boundary does
not copy those results. Absent risk data is not low risk. Persisted data is
not necessarily current. A Decision result is not authenticated source
evidence, including when its internal verification passed.

This module does not instantiate the Decision specialist, construct a
Decision request or tools runtime, or read operational tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_parallel_contracts import MasterParallelContractBundle
from wilvor_ai.master_routing import (
    MasterHintTrust,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRouteHints,
    MasterRouteStepPlan,
)
from wilvor_ai.master_specialist_adapters import (
    MasterAdapterExecutionState,
    MasterSpecialistAdapterDescriptor,
)


MASTER_DECISION_BINDING_SCHEMA_VERSION = "wilvor.ai.master_decision_binding.v1"

MASTER_DECISION_BINDING_EXPLANATION = (
    "Independently trusted Decision target authorization is missing. "
    "The Master plan, its comparison candidates, and its untrusted inputs "
    "do not authorize a Decision target."
)

_DECISION_CAPABILITIES = frozenset(
    {
        AgentCapability.EXPLAIN_EXISTING_RISK,
        AgentCapability.EXPLAIN_EXISTING_RECOMMENDATIONS,
    }
)

_AUDIT_FIELDS = (
    "specialist",
    "purpose",
    "requirement",
    "capability",
    "master_request_id",
    "correlation_id",
    "application_request_time_utc",
    "step_id",
    "invocation_id",
    "specialist_request_id",
    "gates",
    "depends_on",
    "comparison_candidate_aircraft_id",
    "comparison_candidate_recommendation_id",
    "untrusted_callsign",
    "untrusted_typed_aircraft_id",
    "untrusted_recommendation_hint",
    "untrusted_population_aircraft_ids",
    "needs_persisted_evidence",
    "hint_trust",
    "target_authorization_granted",
    "ready_to_execute",
)


class MasterDecisionBindingStatus(str, Enum):
    """The only binding outcome this module can record."""

    BLOCKED_MISSING_TRUSTED_TARGET = "BLOCKED_MISSING_TRUSTED_TARGET"


class MasterDecisionBindingRefusal(str, Enum):
    """Why the binding cannot be executed."""

    INDEPENDENTLY_TRUSTED_TARGET_AUTHORIZATION_MISSING = (
        "INDEPENDENTLY_TRUSTED_TARGET_AUTHORIZATION_MISSING"
    )


class MasterDecisionBindingLimitation(str, Enum):
    """Distinctions the blocked binding must keep visible.

    These are standing limits of this boundary. They are not copies of a
    specialist result or of another specialist's evidence.
    """

    INDEPENDENTLY_TRUSTED_TARGET_AUTHORIZATION_MISSING = (
        "INDEPENDENTLY_TRUSTED_TARGET_AUTHORIZATION_MISSING"
    )
    COMPARISON_CANDIDATE_IS_NOT_A_TRUSTED_TARGET = (
        "COMPARISON_CANDIDATE_IS_NOT_A_TRUSTED_TARGET"
    )
    USER_TEXT_IS_NOT_A_DECISION_TARGET = "USER_TEXT_IS_NOT_A_DECISION_TARGET"
    CALLSIGN_IS_NOT_AIRCRAFT_IDENTITY = "CALLSIGN_IS_NOT_AIRCRAFT_IDENTITY"
    ROUTE_HINT_IS_NOT_A_TRUSTED_TARGET = "ROUTE_HINT_IS_NOT_A_TRUSTED_TARGET"
    LIVE_OPS_DISCOVERY_IS_NOT_A_TRUSTED_TARGET = (
        "LIVE_OPS_DISCOVERY_IS_NOT_A_TRUSTED_TARGET"
    )
    LIVE_OPS_ENTITY_REF_IS_NOT_A_TRUSTED_TARGET = (
        "LIVE_OPS_ENTITY_REF_IS_NOT_A_TRUSTED_TARGET"
    )
    MODEL_TEXT_CANNOT_SELECT_A_TARGET = "MODEL_TEXT_CANNOT_SELECT_A_TARGET"
    EXPECTED_RECOMMENDATION_DOES_NOT_AUTHORIZE_PERSISTED_EVIDENCE = (
        "EXPECTED_RECOMMENDATION_DOES_NOT_AUTHORIZE_PERSISTED_EVIDENCE"
    )
    TARGET_AUTHORIZATION_GRANTED_REMAINS_FALSE = (
        "TARGET_AUTHORIZATION_GRANTED_REMAINS_FALSE"
    )
    COMPLETED_DOES_NOT_MEAN_CLAIMS_VERIFIED = "COMPLETED_DOES_NOT_MEAN_CLAIMS_VERIFIED"
    PARTIAL_IS_NOT_COMPLETED = "PARTIAL_IS_NOT_COMPLETED"
    PROVIDER_FAILURE_IS_NOT_PARTIAL = "PROVIDER_FAILURE_IS_NOT_PARTIAL"
    VERIFICATION_FAILURE_IS_NOT_RUN_SUCCESS = "VERIFICATION_FAILURE_IS_NOT_RUN_SUCCESS"
    PERSISTED_EVIDENCE_LIMITATION_IS_DISTINCT = (
        "PERSISTED_EVIDENCE_LIMITATION_IS_DISTINCT"
    )
    ABSENT_RISK_DATA_IS_NOT_LOW_RISK = "ABSENT_RISK_DATA_IS_NOT_LOW_RISK"
    PERSISTED_DATA_IS_NOT_NECESSARILY_CURRENT = (
        "PERSISTED_DATA_IS_NOT_NECESSARILY_CURRENT"
    )
    DECISION_RESULT_IS_NOT_AUTHENTICATED_SOURCE_EVIDENCE = (
        "DECISION_RESULT_IS_NOT_AUTHENTICATED_SOURCE_EVIDENCE"
    )
    INTERNAL_VERIFICATION_IS_NOT_SOURCE_AUTHENTICATION = (
        "INTERNAL_VERIFICATION_IS_NOT_SOURCE_AUTHENTICATION"
    )


def _closed(value: object, denied: object, code: str) -> None:
    if value is not denied:
        raise ContractValidationError(code)


def _optional_token(value: object, field_name: str) -> None:
    if value is None:
        return
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        raise ContractValidationError(f"invalid_{field_name}")


def _token(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise ContractValidationError(f"invalid_{field_name}")
    _optional_token(value, field_name)


def _reject_forged_authority(
    *,
    target_authorization_granted: object,
    decision_target_trusted: object,
    trusted_aircraft_id: object,
    trusted_recommendation_id: object,
    authorization_token: object,
    authorization_signature: object,
    authorization_certificate: object,
    execution_authority: object,
    production_authority: object,
    source_authenticity: object,
    request_coverage: object,
    end_to_end_fulfilled: object,
    dispatch_permitted: object,
    executable: object,
    ready_to_execute: object,
    persisted_evidence_authorized: object,
    comparison_candidates_authorize_target: object,
    callsign_is_aircraft_identity: object,
    user_text_copied_into_request: object,
    model_text_selects_target: object,
    live_ops_discovery_is_trusted_target: object,
    live_ops_entity_hint_is_trusted_target: object,
    completed_means_claims_verified: object,
    absent_risk_is_low_risk: object,
    persisted_data_is_necessarily_current: object,
    decision_result_is_authenticated_source_evidence: object,
    internal_verification_is_source_authentication: object,
) -> None:
    """Reject every caller-supplied grant. There is no authorization branch."""

    _closed(target_authorization_granted, False, "target_authorization_not_granted")
    _closed(decision_target_trusted, False, "target_authorization_not_granted")
    _closed(trusted_aircraft_id, None, "target_authorization_not_granted")
    _closed(trusted_recommendation_id, None, "target_authorization_not_granted")
    _closed(authorization_token, None, "authorization_token_not_accepted")
    _closed(authorization_signature, None, "authorization_signature_not_accepted")
    _closed(authorization_certificate, None, "authorization_certificate_not_accepted")
    _closed(
        execution_authority,
        MasterRouteExecutionAuthority.DENIED,
        "execution_authority_not_granted",
    )
    _closed(
        production_authority,
        MasterProductionAuthority.DENIED,
        "production_authority_not_issued",
    )
    _closed(
        source_authenticity,
        MasterSourceAuthenticity.NOT_ESTABLISHED,
        "source_authenticity_not_established",
    )
    _closed(
        request_coverage,
        MasterRequestCoverage.NOT_ESTABLISHED,
        "request_coverage_not_established",
    )
    _closed(end_to_end_fulfilled, False, "end_to_end_fulfillment_not_granted")
    _closed(dispatch_permitted, False, "execution_not_granted")
    _closed(executable, False, "execution_not_granted")
    _closed(ready_to_execute, False, "decision_step_not_executable")
    _closed(persisted_evidence_authorized, False, "persisted_evidence_not_authorized")
    _closed(
        comparison_candidates_authorize_target,
        False,
        "target_authorization_not_granted",
    )
    _closed(callsign_is_aircraft_identity, False, "callsign_not_authenticated_aircraft")
    _closed(user_text_copied_into_request, False, "user_text_not_a_decision_target")
    _closed(model_text_selects_target, False, "model_text_cannot_select_target")
    _closed(
        live_ops_discovery_is_trusted_target,
        False,
        "live_ops_discovery_not_trusted_target",
    )
    _closed(
        live_ops_entity_hint_is_trusted_target,
        False,
        "live_ops_entity_not_trusted_target",
    )
    _closed(completed_means_claims_verified, False, "completed_does_not_verify_claims")
    _closed(absent_risk_is_low_risk, False, "absent_risk_is_not_low_risk")
    _closed(
        persisted_data_is_necessarily_current,
        False,
        "persisted_data_is_not_current",
    )
    _closed(
        decision_result_is_authenticated_source_evidence,
        False,
        "decision_result_not_source_evidence",
    )
    _closed(
        internal_verification_is_source_authentication,
        False,
        "decision_verifier_not_source_authentication",
    )


def _require_planned_decision(
    bundle: MasterParallelContractBundle,
    descriptor: MasterSpecialistAdapterDescriptor,
) -> MasterRouteStepPlan:
    """Return the one planned Decision step that this descriptor names.

    A descriptor for another specialist, another invocation, another step, or
    another request id is rejected. Comparison candidates are checked against
    the planning context and are still not targets. Evidence attached to the
    bundle is not read.
    """

    if not isinstance(bundle, MasterParallelContractBundle):
        raise ContractValidationError("invalid_parallel_contract")
    if not isinstance(descriptor, MasterSpecialistAdapterDescriptor):
        raise ContractValidationError("invalid_adapter_descriptor")
    if descriptor.specialist is not MasterSpecialist.DECISION:
        raise ContractValidationError("specialist_mismatch")
    if descriptor.master_request_id != bundle.context.master_request_id:
        raise ContractValidationError("master_request_mismatch")
    if (
        descriptor.execution_state
        is not MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
    ):
        raise ContractValidationError("execution_not_granted")
    _closed(descriptor.decision_target_trusted, False, "target_authorization_not_granted")
    _closed(descriptor.callsign_authenticated, False, "callsign_not_authenticated_aircraft")
    _closed(
        descriptor.execution_authority,
        MasterRouteExecutionAuthority.DENIED,
        "execution_authority_not_granted",
    )
    _closed(
        descriptor.production_authority,
        MasterProductionAuthority.DENIED,
        "production_authority_not_issued",
    )
    _closed(
        descriptor.source_authenticity,
        MasterSourceAuthenticity.NOT_ESTABLISHED,
        "source_authenticity_not_established",
    )
    _closed(
        bundle.execution_authority,
        MasterRouteExecutionAuthority.DENIED,
        "execution_authority_not_granted",
    )
    _closed(
        bundle.plan.execution_authority,
        MasterRouteExecutionAuthority.DENIED,
        "execution_authority_not_granted",
    )
    planned = {step.invocation.invocation_id: step for step in bundle.plan.steps}
    step = planned.get(descriptor.invocation_id)
    if step is None:
        raise ContractValidationError("invocation_not_in_plan")
    if descriptor.step_id != step.step_id:
        raise ContractValidationError("invocation_binding_mismatch")
    invocation = step.invocation
    if invocation.specialist is not MasterSpecialist.DECISION:
        raise ContractValidationError("specialist_mismatch")
    if descriptor.specialist is not invocation.specialist:
        raise ContractValidationError("specialist_mismatch")
    if (
        descriptor.purpose is not invocation.purpose
        or descriptor.requirement is not invocation.requirement
    ):
        raise ContractValidationError("invocation_binding_mismatch")
    if invocation.purpose is not MasterPurpose.ANSWER:
        raise ContractValidationError("invalid_purpose")
    if invocation.specialist_request_id is None:
        raise ContractValidationError("specialist_request_id_mismatch")
    if descriptor.specialist_request_id != invocation.specialist_request_id:
        raise ContractValidationError("specialist_request_id_mismatch")
    if invocation.master_request_id != bundle.context.master_request_id:
        raise ContractValidationError("master_request_mismatch")
    if invocation.correlation_id != bundle.context.correlation_id:
        raise ContractValidationError("correlation_mismatch")
    if invocation.application_request_time_utc != bundle.context.application_request_time_utc:
        raise ContractValidationError("application_time_mismatch")
    identities = (
        invocation.master_request_id,
        invocation.invocation_id,
        invocation.specialist_request_id,
        invocation.correlation_id,
    )
    if len(set(identities)) != 4:
        raise ContractValidationError("request_identity_not_distinct")
    if invocation.expected_aircraft_id != bundle.context.target_candidate_aircraft_id:
        raise ContractValidationError("invocation_binding_mismatch")
    if (
        invocation.expected_recommendation_id
        != bundle.context.target_candidate_recommendation_id
    ):
        raise ContractValidationError("invocation_binding_mismatch")
    if step.capability not in _DECISION_CAPABILITIES:
        raise ContractValidationError("capability_step_mismatch")
    _closed(step.ready_to_execute, False, "decision_step_not_executable")
    _closed(step.target_authorization_granted, False, "target_authorization_not_granted")
    if step.hint_trust is not MasterHintTrust.UNTRUSTED:
        raise ContractValidationError("selection_hints_not_trusted")
    if not isinstance(step.untrusted_hints, MasterRouteHints):
        raise ContractValidationError("invalid_route_hints")
    return step


def _require_dependencies(step_id: str, depends_on: object) -> tuple[str, ...]:
    if not isinstance(depends_on, tuple) or any(not isinstance(item, str) for item in depends_on):
        raise ContractValidationError("invalid_depends_on")
    if len(depends_on) != len(set(depends_on)):
        raise ContractValidationError("duplicate_dependency_id")
    if step_id in depends_on:
        raise ContractValidationError("self_dependency")
    for item in depends_on:
        _token(item, "depends_on")
    return depends_on


@dataclass(frozen=True)
class MasterDecisionPlanningIdentity:
    """Validated Decision planning facts. This object holds no evidence."""

    master_request_id: str
    correlation_id: str
    application_request_time_utc: str
    step_id: str
    invocation_id: str
    specialist_request_id: str
    purpose: MasterPurpose
    requirement: MasterRequirement
    capability: AgentCapability
    gates: tuple[MasterRouteGate, ...]
    depends_on: tuple[str, ...]
    comparison_candidate_aircraft_id: str | None
    comparison_candidate_recommendation_id: str | None
    untrusted_callsign: str | None
    untrusted_typed_aircraft_id: str | None
    untrusted_recommendation_hint: str | None
    untrusted_population_aircraft_ids: tuple[str, ...]
    needs_persisted_evidence: bool
    specialist: MasterSpecialist = MasterSpecialist.DECISION
    hint_trust: MasterHintTrust = MasterHintTrust.UNTRUSTED
    target_authorization_granted: bool = False
    ready_to_execute: bool = False

    def __post_init__(self) -> None:
        _token(self.master_request_id, "master_request_id")
        _token(self.correlation_id, "correlation_id")
        _token(self.application_request_time_utc, "application_request_time_utc")
        _token(self.step_id, "step_id")
        _token(self.invocation_id, "invocation_id")
        _token(self.specialist_request_id, "specialist_request_id")
        identities = (
            self.master_request_id,
            self.invocation_id,
            self.specialist_request_id,
            self.correlation_id,
        )
        if len(set(identities)) != 4:
            raise ContractValidationError("request_identity_not_distinct")
        _closed(self.specialist, MasterSpecialist.DECISION, "specialist_mismatch")
        if not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        _closed(self.purpose, MasterPurpose.ANSWER, "invalid_purpose")
        if not isinstance(self.requirement, MasterRequirement):
            raise ContractValidationError("invalid_requirement")
        if self.capability not in _DECISION_CAPABILITIES:
            raise ContractValidationError("capability_step_mismatch")
        if not isinstance(self.gates, tuple) or any(
            not isinstance(item, MasterRouteGate) for item in self.gates
        ):
            raise ContractValidationError("invalid_gates")
        _require_dependencies(self.step_id, self.depends_on)
        _optional_token(self.comparison_candidate_aircraft_id, "comparison_candidate_aircraft_id")
        _optional_token(
            self.comparison_candidate_recommendation_id,
            "comparison_candidate_recommendation_id",
        )
        _optional_token(self.untrusted_callsign, "untrusted_callsign")
        _optional_token(self.untrusted_typed_aircraft_id, "untrusted_typed_aircraft_id")
        _optional_token(self.untrusted_recommendation_hint, "untrusted_recommendation_hint")
        if not isinstance(self.untrusted_population_aircraft_ids, tuple):
            raise ContractValidationError("invalid_population_aircraft_ids")
        seen: set[str] = set()
        for item in self.untrusted_population_aircraft_ids:
            _token(item, "population_aircraft_id")
            if item in seen:
                raise ContractValidationError("duplicate_population_aircraft_id")
            seen.add(item)
        if not isinstance(self.needs_persisted_evidence, bool):
            raise ContractValidationError("invalid_needs_persisted_evidence")
        _closed(self.hint_trust, MasterHintTrust.UNTRUSTED, "selection_hints_not_trusted")
        _closed(self.target_authorization_granted, False, "target_authorization_not_granted")
        _closed(self.ready_to_execute, False, "decision_step_not_executable")


def _planning_from_step(step: MasterRouteStepPlan) -> MasterDecisionPlanningIdentity:
    """Copy the Decision step's planning identity. Hints stay untrusted."""

    invocation = step.invocation
    if (
        invocation.correlation_id is None
        or invocation.application_request_time_utc is None
        or invocation.specialist_request_id is None
    ):
        raise ContractValidationError("request_identity_not_distinct")
    hints = step.untrusted_hints
    return MasterDecisionPlanningIdentity(
        master_request_id=invocation.master_request_id,
        correlation_id=invocation.correlation_id,
        application_request_time_utc=invocation.application_request_time_utc,
        step_id=step.step_id,
        invocation_id=invocation.invocation_id,
        specialist_request_id=invocation.specialist_request_id,
        purpose=invocation.purpose,
        requirement=invocation.requirement,
        capability=step.capability,
        gates=step.gates,
        depends_on=step.depends_on,
        comparison_candidate_aircraft_id=invocation.expected_aircraft_id,
        comparison_candidate_recommendation_id=invocation.expected_recommendation_id,
        untrusted_callsign=hints.callsign,
        untrusted_typed_aircraft_id=hints.typed_aircraft_id,
        untrusted_recommendation_hint=hints.recommendation_id,
        untrusted_population_aircraft_ids=tuple(hints.population_aircraft_ids),
        needs_persisted_evidence=step.needs_persisted_evidence,
    )


def _identity_agrees(
    binding: "MasterDecisionBinding",
    planning: MasterDecisionPlanningIdentity,
    descriptor: MasterSpecialistAdapterDescriptor,
) -> None:
    """Keep the binding, its planning identity, and the descriptor in agreement."""

    if not isinstance(planning, MasterDecisionPlanningIdentity):
        raise ContractValidationError("invalid_planning_identity")
    if not isinstance(descriptor, MasterSpecialistAdapterDescriptor):
        raise ContractValidationError("invalid_adapter_descriptor")
    for name in _AUDIT_FIELDS:
        if getattr(binding, name) != getattr(planning, name):
            raise ContractValidationError("invocation_binding_mismatch")
    if descriptor.master_request_id != planning.master_request_id:
        raise ContractValidationError("master_request_mismatch")
    if descriptor.step_id != planning.step_id or descriptor.invocation_id != planning.invocation_id:
        raise ContractValidationError("invocation_binding_mismatch")
    if descriptor.specialist_request_id != planning.specialist_request_id:
        raise ContractValidationError("specialist_request_id_mismatch")
    if descriptor.specialist is not planning.specialist:
        raise ContractValidationError("specialist_mismatch")
    if descriptor.purpose is not planning.purpose or descriptor.requirement is not planning.requirement:
        raise ContractValidationError("invocation_binding_mismatch")
    _closed(descriptor.decision_target_trusted, False, "target_authorization_not_granted")
    _closed(descriptor.callsign_authenticated, False, "callsign_not_authenticated_aircraft")
    _closed(
        descriptor.execution_state,
        MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION,
        "execution_not_granted",
    )
    _closed(
        descriptor.execution_authority,
        MasterRouteExecutionAuthority.DENIED,
        "execution_authority_not_granted",
    )
    _closed(
        descriptor.production_authority,
        MasterProductionAuthority.DENIED,
        "production_authority_not_issued",
    )
    _closed(
        descriptor.source_authenticity,
        MasterSourceAuthenticity.NOT_ESTABLISHED,
        "source_authenticity_not_established",
    )


@dataclass(frozen=True)
class MasterDecisionBinding:
    """One blocked Decision binding. Construction cannot make it executable.

    ``planning`` is the validated step identity. The same facts are repeated
    on this object so replacing either copy alone fails closed. Neither copy
    contains the evidence-bearing bundle.
    """

    planning: MasterDecisionPlanningIdentity
    descriptor: MasterSpecialistAdapterDescriptor
    purpose: MasterPurpose
    requirement: MasterRequirement
    capability: AgentCapability
    master_request_id: str
    correlation_id: str
    application_request_time_utc: str
    step_id: str
    invocation_id: str
    specialist_request_id: str
    gates: tuple[MasterRouteGate, ...]
    depends_on: tuple[str, ...]
    comparison_candidate_aircraft_id: str | None
    comparison_candidate_recommendation_id: str | None
    untrusted_callsign: str | None
    untrusted_typed_aircraft_id: str | None
    untrusted_recommendation_hint: str | None
    untrusted_population_aircraft_ids: tuple[str, ...]
    needs_persisted_evidence: bool
    specialist: MasterSpecialist = MasterSpecialist.DECISION
    status: MasterDecisionBindingStatus = (
        MasterDecisionBindingStatus.BLOCKED_MISSING_TRUSTED_TARGET
    )
    refusal: MasterDecisionBindingRefusal = (
        MasterDecisionBindingRefusal.INDEPENDENTLY_TRUSTED_TARGET_AUTHORIZATION_MISSING
    )
    explanation: str = MASTER_DECISION_BINDING_EXPLANATION
    limitations: tuple[MasterDecisionBindingLimitation, ...] = tuple(
        MasterDecisionBindingLimitation
    )
    hint_trust: MasterHintTrust = MasterHintTrust.UNTRUSTED
    target_authorization_granted: bool = False
    decision_target_trusted: bool = False
    trusted_aircraft_id: str | None = None
    trusted_recommendation_id: str | None = None
    authorization_token: str | None = None
    authorization_signature: str | None = None
    authorization_certificate: str | None = None
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    end_to_end_fulfilled: bool = False
    dispatch_permitted: bool = False
    executable: bool = False
    ready_to_execute: bool = False
    persisted_evidence_authorized: bool = False
    comparison_candidates_authorize_target: bool = False
    callsign_is_aircraft_identity: bool = False
    user_text_copied_into_request: bool = False
    model_text_selects_target: bool = False
    live_ops_discovery_is_trusted_target: bool = False
    live_ops_entity_hint_is_trusted_target: bool = False
    completed_means_claims_verified: bool = False
    absent_risk_is_low_risk: bool = False
    persisted_data_is_necessarily_current: bool = False
    decision_result_is_authenticated_source_evidence: bool = False
    internal_verification_is_source_authentication: bool = False
    schema_version: str = MASTER_DECISION_BINDING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_DECISION_BINDING_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _closed(
            self.status,
            MasterDecisionBindingStatus.BLOCKED_MISSING_TRUSTED_TARGET,
            "execution_not_granted",
        )
        _closed(
            self.refusal,
            MasterDecisionBindingRefusal.INDEPENDENTLY_TRUSTED_TARGET_AUTHORIZATION_MISSING,
            "execution_not_granted",
        )
        if self.explanation != MASTER_DECISION_BINDING_EXPLANATION:
            raise ContractValidationError("invalid_decision_binding_explanation")
        if self.limitations != tuple(MasterDecisionBindingLimitation):
            raise ContractValidationError("decision_binding_limitation_missing")
        _reject_forged_authority(
            target_authorization_granted=self.target_authorization_granted,
            decision_target_trusted=self.decision_target_trusted,
            trusted_aircraft_id=self.trusted_aircraft_id,
            trusted_recommendation_id=self.trusted_recommendation_id,
            authorization_token=self.authorization_token,
            authorization_signature=self.authorization_signature,
            authorization_certificate=self.authorization_certificate,
            execution_authority=self.execution_authority,
            production_authority=self.production_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            end_to_end_fulfilled=self.end_to_end_fulfilled,
            dispatch_permitted=self.dispatch_permitted,
            executable=self.executable,
            ready_to_execute=self.ready_to_execute,
            persisted_evidence_authorized=self.persisted_evidence_authorized,
            comparison_candidates_authorize_target=self.comparison_candidates_authorize_target,
            callsign_is_aircraft_identity=self.callsign_is_aircraft_identity,
            user_text_copied_into_request=self.user_text_copied_into_request,
            model_text_selects_target=self.model_text_selects_target,
            live_ops_discovery_is_trusted_target=self.live_ops_discovery_is_trusted_target,
            live_ops_entity_hint_is_trusted_target=self.live_ops_entity_hint_is_trusted_target,
            completed_means_claims_verified=self.completed_means_claims_verified,
            absent_risk_is_low_risk=self.absent_risk_is_low_risk,
            persisted_data_is_necessarily_current=self.persisted_data_is_necessarily_current,
            decision_result_is_authenticated_source_evidence=(
                self.decision_result_is_authenticated_source_evidence
            ),
            internal_verification_is_source_authentication=(
                self.internal_verification_is_source_authentication
            ),
        )
        _identity_agrees(self, self.planning, self.descriptor)


def _binding_from_planning(
    planning: MasterDecisionPlanningIdentity,
    descriptor: MasterSpecialistAdapterDescriptor,
) -> MasterDecisionBinding:
    return MasterDecisionBinding(
        planning=planning,
        descriptor=descriptor,
        purpose=planning.purpose,
        requirement=planning.requirement,
        capability=planning.capability,
        master_request_id=planning.master_request_id,
        correlation_id=planning.correlation_id,
        application_request_time_utc=planning.application_request_time_utc,
        step_id=planning.step_id,
        invocation_id=planning.invocation_id,
        specialist_request_id=planning.specialist_request_id,
        gates=planning.gates,
        depends_on=planning.depends_on,
        comparison_candidate_aircraft_id=planning.comparison_candidate_aircraft_id,
        comparison_candidate_recommendation_id=planning.comparison_candidate_recommendation_id,
        untrusted_callsign=planning.untrusted_callsign,
        untrusted_typed_aircraft_id=planning.untrusted_typed_aircraft_id,
        untrusted_recommendation_hint=planning.untrusted_recommendation_hint,
        untrusted_population_aircraft_ids=planning.untrusted_population_aircraft_ids,
        needs_persisted_evidence=planning.needs_persisted_evidence,
    )


def prepare_master_decision_binding(
    bundle: MasterParallelContractBundle,
    descriptor: MasterSpecialistAdapterDescriptor,
    *,
    target_authorization_granted: bool = False,
    decision_target_trusted: bool = False,
    trusted_aircraft_id: str | None = None,
    trusted_recommendation_id: str | None = None,
    authorization_token: str | None = None,
    authorization_signature: str | None = None,
    authorization_certificate: str | None = None,
    execution_authority: object = MasterRouteExecutionAuthority.DENIED,
    production_authority: object = MasterProductionAuthority.DENIED,
    source_authenticity: object = MasterSourceAuthenticity.NOT_ESTABLISHED,
    request_coverage: object = MasterRequestCoverage.NOT_ESTABLISHED,
    end_to_end_fulfilled: bool = False,
    dispatch_permitted: bool = False,
    executable: bool = False,
    ready_to_execute: bool = False,
    persisted_evidence_authorized: bool = False,
    comparison_candidates_authorize_target: bool = False,
    callsign_is_aircraft_identity: bool = False,
    user_text_copied_into_request: bool = False,
    model_text_selects_target: bool = False,
    live_ops_discovery_is_trusted_target: bool = False,
    live_ops_entity_hint_is_trusted_target: bool = False,
    completed_means_claims_verified: bool = False,
    absent_risk_is_low_risk: bool = False,
    persisted_data_is_necessarily_current: bool = False,
    decision_result_is_authenticated_source_evidence: bool = False,
    internal_verification_is_source_authentication: bool = False,
) -> MasterDecisionBinding:
    """Bind one planned Decision descriptor and refuse execution.

    The bundle is validated and then dropped. Forged grant fields are
    rejected. They are not an authorization path.
    """

    _reject_forged_authority(
        target_authorization_granted=target_authorization_granted,
        decision_target_trusted=decision_target_trusted,
        trusted_aircraft_id=trusted_aircraft_id,
        trusted_recommendation_id=trusted_recommendation_id,
        authorization_token=authorization_token,
        authorization_signature=authorization_signature,
        authorization_certificate=authorization_certificate,
        execution_authority=execution_authority,
        production_authority=production_authority,
        source_authenticity=source_authenticity,
        request_coverage=request_coverage,
        end_to_end_fulfilled=end_to_end_fulfilled,
        dispatch_permitted=dispatch_permitted,
        executable=executable,
        ready_to_execute=ready_to_execute,
        persisted_evidence_authorized=persisted_evidence_authorized,
        comparison_candidates_authorize_target=comparison_candidates_authorize_target,
        callsign_is_aircraft_identity=callsign_is_aircraft_identity,
        user_text_copied_into_request=user_text_copied_into_request,
        model_text_selects_target=model_text_selects_target,
        live_ops_discovery_is_trusted_target=live_ops_discovery_is_trusted_target,
        live_ops_entity_hint_is_trusted_target=live_ops_entity_hint_is_trusted_target,
        completed_means_claims_verified=completed_means_claims_verified,
        absent_risk_is_low_risk=absent_risk_is_low_risk,
        persisted_data_is_necessarily_current=persisted_data_is_necessarily_current,
        decision_result_is_authenticated_source_evidence=(
            decision_result_is_authenticated_source_evidence
        ),
        internal_verification_is_source_authentication=(
            internal_verification_is_source_authentication
        ),
    )
    step = _require_planned_decision(bundle, descriptor)
    return _binding_from_planning(_planning_from_step(step), descriptor)
