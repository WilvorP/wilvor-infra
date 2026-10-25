"""Offline Master gate inspection and runtime dependency classification.

This module reads an existing Master route plan. It records the original
gates, untrusted candidate observations, missing trusted evidence, the
effective unresolved state, and terminal dependency failures. It does not
modify the plan, call a specialist, construct a planning session, or issue a
dispatch permit.

Execution authority stays ``DENIED``. Production authority stays ``DENIED``.
Source authenticity stays ``NOT_ESTABLISHED``. A model, a planning object, a
binder, or caller-supplied evidence cannot authorize execution. Public
dataclass fields and matching string ids are not proof of trust.

No trusted gate-evidence writer exists in this sprint. Caller-constructed
observations cannot produce an authoritative SATISFIED resolution. Native
``COMPLETED`` and Historical ``ANSWERED`` are not sufficient evidence on
their own. Decision identity and currentness stay pending. Region validation
stays pending without authoritative geometry. An unresolved temporal scope
stays pending. A dependency stays pending without trusted execution evidence.
``APPLICATION_TARGET_CANDIDATE_PRESENT`` stays an unresolved execution-policy
classification.

Planning-time omission and a runtime dependency outcome are different facts.
This module does not drop dependents and does not rewrite a plan when an
omission note is supplied.

PROPOSED interfaces, not imported and not implemented here:

- trusted gate-evidence writer
- planning session
- execution scope
- dispatch permit
- provenance binder
- capability registry
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from wilvor_ai.contracts import PROVENANCE_TOKEN_MAX_LENGTH, ContractValidationError
from wilvor_ai.decision_specialist_runtime_contracts import DecisionSpecialistRunStatus
from wilvor_ai.live_ops_public_contracts import LiveOpsRunStatus
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_routing import (
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRoutePlan,
    MasterRouteStepPlan,
)
from wilvor_ai.specialist_contracts import SpecialistStatus
from wilvor_ai.specialist_runtime_contracts import SpecialistRunStatus


MASTER_GATE_RESOLUTION_SCHEMA_VERSION = "wilvor.ai.master_gate_resolution.v1"
PROPOSED_INTERFACES = (
    "PROPOSED:trusted_gate_evidence_writer",
    "PROPOSED:planning_session",
    "PROPOSED:execution_scope",
    "PROPOSED:dispatch_permit",
    "PROPOSED:provenance_binder",
    "PROPOSED:capability_registry",
)
_NATIVE_TYPES = (
    SpecialistStatus,
    SpecialistRunStatus,
    DecisionSpecialistRunStatus,
    LiveOpsRunStatus,
)
_STATUS_FAMILIES = {
    MasterSpecialist.LIVE_OPS: (LiveOpsRunStatus,),
    MasterSpecialist.HISTORICAL_ANALYTICS: (SpecialistStatus, SpecialistRunStatus),
    MasterSpecialist.DECISION: (DecisionSpecialistRunStatus,),
}


class MasterDependencyVerdict(str, Enum):
    """Runtime dependency outcome. Native success is not the first member's proof."""

    COMPLETED = "COMPLETED"
    COMPLETED_WITH_INSUFFICIENT_EVIDENCE = "COMPLETED_WITH_INSUFFICIENT_EVIDENCE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    PROVIDER_FAILED = "PROVIDER_FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    UNAVAILABLE = "UNAVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"


class MasterDependencyVerdictReason(str, Enum):
    """Why a dependency received that verdict."""

    NATIVE_SUCCESS_WITHOUT_TRUSTED_EVIDENCE = "NATIVE_SUCCESS_WITHOUT_TRUSTED_EVIDENCE"
    HISTORICAL_ANSWERED_WITHOUT_TRUSTED_EVIDENCE = "HISTORICAL_ANSWERED_WITHOUT_TRUSTED_EVIDENCE"
    PROPOSED_CLAIMS_ARE_NOT_TRUSTED_COMPLETION = "PROPOSED_CLAIMS_ARE_NOT_TRUSTED_COMPLETION"
    NATIVE_PARTIAL = "NATIVE_PARTIAL"
    NATIVE_FAILED = "NATIVE_FAILED"
    NATIVE_PROVIDER_FAILED = "NATIVE_PROVIDER_FAILED"
    NATIVE_TIMED_OUT = "NATIVE_TIMED_OUT"
    NATIVE_CANCELLED = "NATIVE_CANCELLED"
    NATIVE_UNAVAILABLE = "NATIVE_UNAVAILABLE"
    NATIVE_UNSUPPORTED = "NATIVE_UNSUPPORTED"
    NO_RUNTIME_OBSERVATION = "NO_RUNTIME_OBSERVATION"
    PLANNING_TIME_OMISSION = "PLANNING_TIME_OMISSION"


class MasterDependentEffect(str, Enum):
    """How one dependent sees an upstream outcome. Required and optional stay distinct."""

    REQUIRED_PENDING_TRUSTED_EVIDENCE = "REQUIRED_PENDING_TRUSTED_EVIDENCE"
    OPTIONAL_PENDING_TRUSTED_EVIDENCE = "OPTIONAL_PENDING_TRUSTED_EVIDENCE"
    REQUIRED_INSUFFICIENT_EVIDENCE = "REQUIRED_INSUFFICIENT_EVIDENCE"
    OPTIONAL_INSUFFICIENT_EVIDENCE = "OPTIONAL_INSUFFICIENT_EVIDENCE"
    REQUIRED_PARTIAL = "REQUIRED_PARTIAL"
    OPTIONAL_PARTIAL = "OPTIONAL_PARTIAL"
    REQUIRED_TERMINAL_FAILURE = "REQUIRED_TERMINAL_FAILURE"
    OPTIONAL_TERMINAL_FAILURE = "OPTIONAL_TERMINAL_FAILURE"
    PLANNING_OMISSION_NOT_RUNTIME_FAILURE = "PLANNING_OMISSION_NOT_RUNTIME_FAILURE"


class MasterDependencyOrigin(str, Enum):
    """Planning omission, a planned edge, and a runtime observation are different facts."""

    PLANNED_EDGE = "PLANNED_EDGE"
    PLANNING_TIME_OMISSION = "PLANNING_TIME_OMISSION"
    RUNTIME_OBSERVATION = "RUNTIME_OBSERVATION"


class MasterGateEffectiveState(str, Enum):
    """Effective gate state. Authoritative satisfaction is not a member."""

    NOT_REQUIRED = "NOT_REQUIRED"
    UNRESOLVED = "UNRESOLVED"
    UNRESOLVED_EXECUTION_POLICY = "UNRESOLVED_EXECUTION_POLICY"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"


class MasterGateResolutionReason(str, Enum):
    """Closed reason for the effective state of one original gate."""

    GATE_NOT_REQUIRED = "GATE_NOT_REQUIRED"
    IDENTITY_DISCOVERY_PENDING = "IDENTITY_DISCOVERY_PENDING"
    DECISION_IDENTITY_PENDING = "DECISION_IDENTITY_PENDING"
    DECISION_CURRENTNESS_PENDING = "DECISION_CURRENTNESS_PENDING"
    APPLICATION_TARGET_CANDIDATE_IS_UNRESOLVED_EXECUTION_POLICY = (
        "APPLICATION_TARGET_CANDIDATE_IS_UNRESOLVED_EXECUTION_POLICY"
    )
    TARGET_AMBIGUOUS_PENDING = "TARGET_AMBIGUOUS_PENDING"
    TARGET_UNSUPPORTED_PENDING = "TARGET_UNSUPPORTED_PENDING"
    REGION_VALIDATION_PENDING_WITHOUT_AUTHORITATIVE_GEOMETRY = (
        "REGION_VALIDATION_PENDING_WITHOUT_AUTHORITATIVE_GEOMETRY"
    )
    TEMPORAL_SCOPE_UNRESOLVED_PENDING = "TEMPORAL_SCOPE_UNRESOLVED_PENDING"
    DEPENDENCY_PENDING_WITHOUT_TRUSTED_EXECUTION_EVIDENCE = (
        "DEPENDENCY_PENDING_WITHOUT_TRUSTED_EXECUTION_EVIDENCE"
    )
    DEPENDENCY_TERMINAL_FAILURE = "DEPENDENCY_TERMINAL_FAILURE"


class MasterGateEvidenceTrust(str, Enum):
    """Caller-constructed observations. There is no trusted member."""

    UNTRUSTED_CANDIDATE = "UNTRUSTED_CANDIDATE"
    MISSING = "MISSING"


class MasterTrustedGateEvidenceWriter(str, Enum):
    """This sprint has no writer that can attest gate evidence."""

    ABSENT = "ABSENT"


class MasterCandidateKind(str, Enum):
    """What a caller-constructed observation claims to describe."""

    DEPENDENCY_STATUS = "DEPENDENCY_STATUS"
    GATE = "GATE"


class MasterRequirementSource(str, Enum):
    """Where the requirement used for an omission record came from."""

    PLAN = "PLAN"
    OMISSION_NOTE = "OMISSION_NOTE"


class MasterGateResolutionLimitation(str, Enum):
    """Standing limits. They are present on every inspection."""

    NO_TRUSTED_GATE_EVIDENCE_WRITER = "NO_TRUSTED_GATE_EVIDENCE_WRITER"
    NATIVE_COMPLETED_IS_NOT_SUFFICIENT_EVIDENCE = "NATIVE_COMPLETED_IS_NOT_SUFFICIENT_EVIDENCE"
    HISTORICAL_ANSWERED_IS_NOT_SUFFICIENT_EVIDENCE = "HISTORICAL_ANSWERED_IS_NOT_SUFFICIENT_EVIDENCE"
    CALLER_OBSERVATION_CANNOT_SATISFY_GATE = "CALLER_OBSERVATION_CANNOT_SATISFY_GATE"
    MATCHING_IDS_ARE_NOT_TRUST = "MATCHING_IDS_ARE_NOT_TRUST"
    PLANNING_OMISSION_IS_NOT_RUNTIME_FAILURE = "PLANNING_OMISSION_IS_NOT_RUNTIME_FAILURE"
    APPLICATION_TARGET_CANDIDATE_IS_NOT_EXECUTION_AUTHORITY = (
        "APPLICATION_TARGET_CANDIDATE_IS_NOT_EXECUTION_AUTHORITY"
    )
    DECISION_IDENTITY_AND_CURRENTNESS_REMAIN_PENDING = (
        "DECISION_IDENTITY_AND_CURRENTNESS_REMAIN_PENDING"
    )
    REGION_VALIDATION_HAS_NO_AUTHORITATIVE_GEOMETRY = (
        "REGION_VALIDATION_HAS_NO_AUTHORITATIVE_GEOMETRY"
    )
    TEMPORAL_SCOPE_REMAINS_UNRESOLVED = "TEMPORAL_SCOPE_REMAINS_UNRESOLVED"
    DEPENDENCY_REMAINS_PENDING_WITHOUT_TRUSTED_EXECUTION_EVIDENCE = (
        "DEPENDENCY_REMAINS_PENDING_WITHOUT_TRUSTED_EXECUTION_EVIDENCE"
    )
    EXECUTION_AUTHORITY_REMAINS_DENIED = "EXECUTION_AUTHORITY_REMAINS_DENIED"
    PRODUCTION_AUTHORITY_REMAINS_DENIED = "PRODUCTION_AUTHORITY_REMAINS_DENIED"
    SOURCE_AUTHENTICITY_REMAINS_NOT_ESTABLISHED = "SOURCE_AUTHENTICITY_REMAINS_NOT_ESTABLISHED"
    STRUCTURAL_READINESS_IS_NOT_A_DISPATCH_PERMIT = "STRUCTURAL_READINESS_IS_NOT_A_DISPATCH_PERMIT"


MASTER_GATE_RESOLUTION_LIMITATIONS = tuple(MasterGateResolutionLimitation)

_TERMINAL_VERDICTS = frozenset(
    {
        MasterDependencyVerdict.FAILED,
        MasterDependencyVerdict.PROVIDER_FAILED,
        MasterDependencyVerdict.TIMED_OUT,
        MasterDependencyVerdict.CANCELLED,
        MasterDependencyVerdict.UNAVAILABLE,
        MasterDependencyVerdict.UNSUPPORTED,
    }
)
_INSUFFICIENT = (
    MasterDependencyVerdict.COMPLETED_WITH_INSUFFICIENT_EVIDENCE,
    MasterDependencyVerdictReason.NATIVE_SUCCESS_WITHOUT_TRUSTED_EVIDENCE,
)
_ANSWERED = (
    MasterDependencyVerdict.COMPLETED_WITH_INSUFFICIENT_EVIDENCE,
    MasterDependencyVerdictReason.HISTORICAL_ANSWERED_WITHOUT_TRUSTED_EVIDENCE,
)
_PROPOSED = (
    MasterDependencyVerdict.COMPLETED_WITH_INSUFFICIENT_EVIDENCE,
    MasterDependencyVerdictReason.PROPOSED_CLAIMS_ARE_NOT_TRUSTED_COMPLETION,
)
_PARTIAL = (MasterDependencyVerdict.PARTIAL, MasterDependencyVerdictReason.NATIVE_PARTIAL)
_FAILED = (MasterDependencyVerdict.FAILED, MasterDependencyVerdictReason.NATIVE_FAILED)
_PROVIDER_FAILED = (
    MasterDependencyVerdict.PROVIDER_FAILED,
    MasterDependencyVerdictReason.NATIVE_PROVIDER_FAILED,
)
_TIMED_OUT = (MasterDependencyVerdict.TIMED_OUT, MasterDependencyVerdictReason.NATIVE_TIMED_OUT)
_CANCELLED = (MasterDependencyVerdict.CANCELLED, MasterDependencyVerdictReason.NATIVE_CANCELLED)
_UNAVAILABLE = (
    MasterDependencyVerdict.UNAVAILABLE,
    MasterDependencyVerdictReason.NATIVE_UNAVAILABLE,
)
_UNSUPPORTED = (
    MasterDependencyVerdict.UNSUPPORTED,
    MasterDependencyVerdictReason.NATIVE_UNSUPPORTED,
)

def _status_key(native_status: Enum) -> tuple[type[Enum], str]:
    """Separate native enums whose string values would otherwise compare equal."""

    return (type(native_status), native_status.value)


# Authoritative COMPLETED is intentionally absent. A native success status
# cannot select it. A future trusted gate-evidence writer would be required,
# and that writer is PROPOSED only. Keys include the enum type because several
# native statuses share a string value and would collapse in a value-keyed map.
_VERDICTS = {
    _status_key(SpecialistStatus.ANSWERED): _ANSWERED,
    _status_key(SpecialistStatus.PARTIAL): _PARTIAL,
    _status_key(SpecialistStatus.UNSUPPORTED): _UNSUPPORTED,
    _status_key(SpecialistStatus.UNAVAILABLE): _UNAVAILABLE,
    _status_key(SpecialistStatus.INVALID_REQUEST): _FAILED,
    _status_key(SpecialistStatus.PROVIDER_FAILED): _PROVIDER_FAILED,
    _status_key(SpecialistRunStatus.PROPOSED_CLAIMS): _PROPOSED,
    _status_key(SpecialistRunStatus.UNSUPPORTED): _UNSUPPORTED,
    _status_key(SpecialistRunStatus.INVALID_REQUEST): _FAILED,
    _status_key(SpecialistRunStatus.UNAVAILABLE): _UNAVAILABLE,
    _status_key(SpecialistRunStatus.PROVIDER_FAILED): _PROVIDER_FAILED,
    _status_key(DecisionSpecialistRunStatus.COMPLETED): _INSUFFICIENT,
    _status_key(DecisionSpecialistRunStatus.PARTIAL): _PARTIAL,
    _status_key(DecisionSpecialistRunStatus.UNSUPPORTED): _UNSUPPORTED,
    _status_key(DecisionSpecialistRunStatus.INVALID_REQUEST): _FAILED,
    _status_key(DecisionSpecialistRunStatus.UNAVAILABLE): _UNAVAILABLE,
    _status_key(DecisionSpecialistRunStatus.PROVIDER_FAILED): _PROVIDER_FAILED,
    _status_key(LiveOpsRunStatus.COMPLETED): _INSUFFICIENT,
    _status_key(LiveOpsRunStatus.UNSUPPORTED): _UNSUPPORTED,
    _status_key(LiveOpsRunStatus.REFUSED): _FAILED,
    _status_key(LiveOpsRunStatus.INVALID_REQUEST): _FAILED,
    _status_key(LiveOpsRunStatus.TOOL_FAILED): _FAILED,
    _status_key(LiveOpsRunStatus.PROVIDER_FAILED): _PROVIDER_FAILED,
    _status_key(LiveOpsRunStatus.PROVIDER_TIMEOUT): _TIMED_OUT,
    _status_key(LiveOpsRunStatus.RUNTIME_TIMEOUT): _TIMED_OUT,
    _status_key(LiveOpsRunStatus.BUDGET_EXHAUSTED): _FAILED,
    _status_key(LiveOpsRunStatus.CANCELLED): _CANCELLED,
    _status_key(LiveOpsRunStatus.UNAVAILABLE): _UNAVAILABLE,
}

_FIXED_GATES = {
    MasterRouteGate.NOT_REQUIRED: (
        MasterGateEffectiveState.NOT_REQUIRED,
        MasterGateResolutionReason.GATE_NOT_REQUIRED,
        False,
    ),
    MasterRouteGate.IDENTITY_DISCOVERY_NEEDED: (
        MasterGateEffectiveState.UNRESOLVED,
        MasterGateResolutionReason.IDENTITY_DISCOVERY_PENDING,
        True,
    ),
    MasterRouteGate.TRUSTED_IDENTITY_VALIDATION_PENDING: (
        MasterGateEffectiveState.UNRESOLVED,
        MasterGateResolutionReason.DECISION_IDENTITY_PENDING,
        True,
    ),
    MasterRouteGate.CURRENTNESS_VALIDATION_PENDING: (
        MasterGateEffectiveState.UNRESOLVED,
        MasterGateResolutionReason.DECISION_CURRENTNESS_PENDING,
        True,
    ),
    MasterRouteGate.APPLICATION_TARGET_CANDIDATE_PRESENT: (
        MasterGateEffectiveState.UNRESOLVED_EXECUTION_POLICY,
        MasterGateResolutionReason.APPLICATION_TARGET_CANDIDATE_IS_UNRESOLVED_EXECUTION_POLICY,
        True,
    ),
    MasterRouteGate.TARGET_AMBIGUOUS: (
        MasterGateEffectiveState.UNRESOLVED,
        MasterGateResolutionReason.TARGET_AMBIGUOUS_PENDING,
        True,
    ),
    MasterRouteGate.TARGET_UNSUPPORTED: (
        MasterGateEffectiveState.UNRESOLVED,
        MasterGateResolutionReason.TARGET_UNSUPPORTED_PENDING,
        True,
    ),
    MasterRouteGate.REGION_VALIDATION_PENDING: (
        MasterGateEffectiveState.UNRESOLVED,
        MasterGateResolutionReason.REGION_VALIDATION_PENDING_WITHOUT_AUTHORITATIVE_GEOMETRY,
        True,
    ),
    MasterRouteGate.TEMPORAL_SCOPE_UNRESOLVED: (
        MasterGateEffectiveState.UNRESOLVED,
        MasterGateResolutionReason.TEMPORAL_SCOPE_UNRESOLVED_PENDING,
        True,
    ),
}

if set(_FIXED_GATES) | {MasterRouteGate.DEPENDENCY_BLOCKED} != set(MasterRouteGate):
    raise ContractValidationError("unclassified_gate")
if MasterDependencyVerdict.COMPLETED in {item[0] for item in _VERDICTS.values()}:
    raise ContractValidationError("native_status_is_not_sufficient_evidence")


def _token(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(character.isspace() for character in value)
        or len(value) > PROVENANCE_TOKEN_MAX_LENGTH
    ):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _false(value: object, code: str) -> None:
    if value is not False:
        raise ContractValidationError(code)


def _reject_forged_authority(
    *,
    execution_authority: object,
    production_authority: object,
    source_authenticity: object,
    request_coverage: object,
    dispatch_permitted: object,
    authoritative_satisfaction: object,
) -> None:
    if execution_authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
    if production_authority is not MasterProductionAuthority.DENIED:
        raise ContractValidationError("production_authority_not_issued")
    if source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
        raise ContractValidationError("source_authenticity_not_established")
    if request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
        raise ContractValidationError("request_coverage_not_established")
    _false(dispatch_permitted, "execution_not_granted")
    _false(authoritative_satisfaction, "authoritative_satisfaction_not_available")


def _effect(requirement: MasterRequirement, verdict: MasterDependencyVerdict) -> MasterDependentEffect:
    required = requirement is MasterRequirement.REQUIRED
    if verdict is MasterDependencyVerdict.COMPLETED:
        raise ContractValidationError("native_status_is_not_sufficient_evidence")
    if verdict is MasterDependencyVerdict.COMPLETED_WITH_INSUFFICIENT_EVIDENCE:
        if required:
            return MasterDependentEffect.REQUIRED_INSUFFICIENT_EVIDENCE
        return MasterDependentEffect.OPTIONAL_INSUFFICIENT_EVIDENCE
    if verdict is MasterDependencyVerdict.PARTIAL:
        if required:
            return MasterDependentEffect.REQUIRED_PARTIAL
        return MasterDependentEffect.OPTIONAL_PARTIAL
    if verdict in _TERMINAL_VERDICTS:
        if required:
            return MasterDependentEffect.REQUIRED_TERMINAL_FAILURE
        return MasterDependentEffect.OPTIONAL_TERMINAL_FAILURE
    raise ContractValidationError("invalid_dependency_verdict")


def _classify(
    native_status: Enum | None,
    requirement: MasterRequirement,
    planning_omission: bool,
) -> tuple[
    MasterDependencyVerdict | None,
    MasterDependentEffect,
    MasterDependencyVerdictReason,
    MasterDependencyOrigin,
    bool,
    bool,
]:
    if not isinstance(requirement, MasterRequirement):
        raise ContractValidationError("invalid_requirement")
    if planning_omission is not True and planning_omission is not False:
        raise ContractValidationError("invalid_planning_omission")
    if planning_omission:
        if native_status is not None:
            raise ContractValidationError("planning_omission_is_not_runtime_outcome")
        return (
            None,
            MasterDependentEffect.PLANNING_OMISSION_NOT_RUNTIME_FAILURE,
            MasterDependencyVerdictReason.PLANNING_TIME_OMISSION,
            MasterDependencyOrigin.PLANNING_TIME_OMISSION,
            False,
            False,
        )
    if native_status is None:
        if requirement is MasterRequirement.REQUIRED:
            effect = MasterDependentEffect.REQUIRED_PENDING_TRUSTED_EVIDENCE
        else:
            effect = MasterDependentEffect.OPTIONAL_PENDING_TRUSTED_EVIDENCE
        return (
            None,
            effect,
            MasterDependencyVerdictReason.NO_RUNTIME_OBSERVATION,
            MasterDependencyOrigin.PLANNED_EDGE,
            False,
            False,
        )
    if type(native_status) not in _NATIVE_TYPES:
        raise ContractValidationError("invalid_native_status")
    try:
        verdict, reason = _VERDICTS[_status_key(native_status)]
    except KeyError as exc:
        raise ContractValidationError("invalid_native_status") from exc
    effect = _effect(requirement, verdict)
    return (
        verdict,
        effect,
        reason,
        MasterDependencyOrigin.RUNTIME_OBSERVATION,
        effect is MasterDependentEffect.REQUIRED_TERMINAL_FAILURE,
        effect is MasterDependentEffect.OPTIONAL_TERMINAL_FAILURE,
    )


def _resolve_gate(
    gate: MasterRouteGate,
    dependency_terminal: bool,
) -> tuple[MasterGateEffectiveState, MasterGateResolutionReason, bool]:
    if not isinstance(gate, MasterRouteGate):
        raise ContractValidationError("invalid_gate")
    if dependency_terminal is not True and dependency_terminal is not False:
        raise ContractValidationError("invalid_dependency_terminal")
    if gate is MasterRouteGate.DEPENDENCY_BLOCKED:
        if dependency_terminal:
            return (
                MasterGateEffectiveState.TERMINAL_FAILURE,
                MasterGateResolutionReason.DEPENDENCY_TERMINAL_FAILURE,
                True,
            )
        return (
            MasterGateEffectiveState.UNRESOLVED,
            MasterGateResolutionReason.DEPENDENCY_PENDING_WITHOUT_TRUSTED_EXECUTION_EVIDENCE,
            True,
        )
    if dependency_terminal:
        raise ContractValidationError("inconsistent_gate_resolution")
    try:
        return _FIXED_GATES[gate]
    except KeyError as exc:
        raise ContractValidationError("unclassified_gate") from exc


def _status_matches(specialist: MasterSpecialist, native_status: Enum) -> bool:
    return type(native_status) in _STATUS_FAMILIES.get(specialist, ())


def _snapshot(plan: MasterRoutePlan) -> tuple[Any, ...]:
    return (
        id(plan),
        id(plan.steps),
        tuple((id(step), id(step.gates), id(step.depends_on)) for step in plan.steps),
        plan.to_dict(),
    )


@dataclass(frozen=True)
class MasterDependencyVerdictResult:
    """One pure classification. ``evidence_sufficient`` cannot be true."""

    requirement: MasterRequirement
    effect: MasterDependentEffect
    reason: MasterDependencyVerdictReason
    classification: MasterDependencyOrigin
    verdict: MasterDependencyVerdict | None = None
    native_status: Enum | None = None
    evidence_sufficient: bool = False
    native_status_is_sufficient_evidence: bool = False
    planning_omission: bool = False
    runtime_failure: bool = False
    optional_unmet: bool = False

    def __post_init__(self) -> None:
        _false(self.evidence_sufficient, "trusted_gate_evidence_not_available")
        _false(self.native_status_is_sufficient_evidence, "native_status_is_not_sufficient_evidence")
        if self.verdict is MasterDependencyVerdict.COMPLETED:
            raise ContractValidationError("native_status_is_not_sufficient_evidence")
        expected = _classify(self.native_status, self.requirement, self.planning_omission)
        actual = (
            self.verdict,
            self.effect,
            self.reason,
            self.classification,
            self.runtime_failure,
            self.optional_unmet,
        )
        if actual != expected:
            raise ContractValidationError("inconsistent_dependency_verdict")

    def to_dict(self) -> dict[str, Any]:
        status = self.native_status
        return {
            "requirement": self.requirement.value,
            "effect": self.effect.value,
            "reason": self.reason.value,
            "classification": self.classification.value,
            "verdict": None if self.verdict is None else self.verdict.value,
            "native_status": None if status is None else status.value,
            "native_status_type": None if status is None else type(status).__name__,
            "evidence_sufficient": False,
            "native_status_is_sufficient_evidence": False,
            "planning_omission": self.planning_omission,
            "runtime_failure": self.runtime_failure,
            "optional_unmet": self.optional_unmet,
        }


def dependency_verdict(
    native_status: SpecialistStatus
    | SpecialistRunStatus
    | DecisionSpecialistRunStatus
    | LiveOpsRunStatus
    | None,
    requirement: MasterRequirement,
    *,
    planning_omission: bool = False,
    evidence_sufficient: bool = False,
    verifier_passed: bool = False,
    claimed_satisfied: bool = False,
) -> MasterDependencyVerdictResult:
    """Classify one dependency outcome without reading or changing a plan.

    Required and optional dependents produce different effects. A planning-time
    omission is not a runtime failure. Native ``COMPLETED`` and Historical
    ``ANSWERED`` map to insufficient evidence, not to ``COMPLETED``.
    """

    _false(evidence_sufficient, "trusted_gate_evidence_not_available")
    _false(verifier_passed, "verifier_pass_is_not_trusted_gate_evidence")
    _false(claimed_satisfied, "authoritative_satisfaction_not_available")
    (
        verdict,
        effect,
        reason,
        classification,
        runtime_failure,
        optional_unmet,
    ) = _classify(native_status, requirement, planning_omission)
    return MasterDependencyVerdictResult(
        requirement=requirement,
        effect=effect,
        reason=reason,
        classification=classification,
        verdict=verdict,
        native_status=native_status,
        evidence_sufficient=False,
        native_status_is_sufficient_evidence=False,
        planning_omission=planning_omission,
        runtime_failure=runtime_failure,
        optional_unmet=optional_unmet,
    )


@dataclass(frozen=True)
class MasterDependencyCandidate:
    """Untrusted runtime observation of one planned upstream step.

    A matching invocation id is not provenance. Satisfaction claims are rejected.
    """

    dependency_step_id: str
    invocation_id: str
    native_status: SpecialistStatus | SpecialistRunStatus | DecisionSpecialistRunStatus | LiveOpsRunStatus
    claimed_satisfied: bool = False
    evidence_sufficient: bool = False
    verifier_passed: bool = False

    def __post_init__(self) -> None:
        _token(self.dependency_step_id, "dependency_step_id")
        _token(self.invocation_id, "invocation_id")
        _false(self.claimed_satisfied, "authoritative_satisfaction_not_available")
        _false(self.evidence_sufficient, "trusted_gate_evidence_not_available")
        _false(self.verifier_passed, "verifier_pass_is_not_trusted_gate_evidence")
        if type(self.native_status) not in _NATIVE_TYPES or _status_key(self.native_status) not in _VERDICTS:
            raise ContractValidationError("invalid_native_status")


@dataclass(frozen=True)
class MasterGateCandidateObservation:
    """Untrusted observation aimed at one original gate. It cannot clear that gate."""

    step_id: str
    invocation_id: str
    gate: MasterRouteGate
    claimed_satisfied: bool = False
    authoritative_geometry: bool = False
    identity_validated: bool = False
    currentness_validated: bool = False
    temporal_scope_resolved: bool = False
    execution_policy_cleared: bool = False

    def __post_init__(self) -> None:
        _token(self.step_id, "step_id")
        _token(self.invocation_id, "invocation_id")
        if not isinstance(self.gate, MasterRouteGate):
            raise ContractValidationError("invalid_gate")
        _false(self.claimed_satisfied, "authoritative_satisfaction_not_available")
        _false(self.authoritative_geometry, "authoritative_geometry_not_available")
        _false(self.identity_validated, "decision_identity_not_validated")
        _false(self.currentness_validated, "decision_currentness_not_validated")
        _false(self.temporal_scope_resolved, "temporal_scope_not_resolved")
        _false(self.execution_policy_cleared, "execution_policy_not_cleared")


@dataclass(frozen=True)
class MasterPlanningOmissionNote:
    """A planning-time omission. It is not a runtime dependency outcome."""

    dependent_step_id: str
    omitted_dependency_id: str
    dependent_requirement: MasterRequirement

    def __post_init__(self) -> None:
        _token(self.dependent_step_id, "dependent_step_id")
        _token(self.omitted_dependency_id, "omitted_dependency_id")
        if self.dependent_step_id == self.omitted_dependency_id:
            raise ContractValidationError("self_dependency")
        if not isinstance(self.dependent_requirement, MasterRequirement):
            raise ContractValidationError("invalid_requirement")


@dataclass(frozen=True)
class MasterObservedCandidate:
    """One caller-constructed candidate. Trust stays unestablished."""

    step_id: str
    invocation_id: str
    kind: MasterCandidateKind
    identity_matches_plan: bool
    applied: bool
    trust: MasterGateEvidenceTrust = MasterGateEvidenceTrust.UNTRUSTED_CANDIDATE
    gate: MasterRouteGate | None = None
    native_status_value: str | None = None
    native_status_type: str | None = None
    authoritative: bool = False

    def __post_init__(self) -> None:
        _token(self.step_id, "step_id")
        _token(self.invocation_id, "invocation_id")
        if not isinstance(self.kind, MasterCandidateKind):
            raise ContractValidationError("invalid_candidate_kind")
        if not isinstance(self.identity_matches_plan, bool) or not isinstance(self.applied, bool):
            raise ContractValidationError("invalid_candidate_application")
        if self.applied and not self.identity_matches_plan:
            raise ContractValidationError("foreign_candidate_not_applied")
        if self.trust is not MasterGateEvidenceTrust.UNTRUSTED_CANDIDATE:
            raise ContractValidationError("candidate_evidence_not_trusted")
        _false(self.authoritative, "authoritative_satisfaction_not_available")
        if self.kind is MasterCandidateKind.GATE:
            if not isinstance(self.gate, MasterRouteGate):
                raise ContractValidationError("invalid_gate")
            if self.native_status_value is not None or self.native_status_type is not None:
                raise ContractValidationError("invalid_candidate_status")
        elif self.kind is MasterCandidateKind.DEPENDENCY_STATUS:
            if self.gate is not None:
                raise ContractValidationError("invalid_gate")
            if not isinstance(self.native_status_value, str) or not isinstance(self.native_status_type, str):
                raise ContractValidationError("invalid_candidate_status")
        else:
            raise ContractValidationError("invalid_candidate_kind")


@dataclass(frozen=True)
class MasterGateFinding:
    """One original gate and the effective state this sprint can record."""

    step_id: str
    invocation_id: str
    original_gate: MasterRouteGate
    effective_state: MasterGateEffectiveState
    reason: MasterGateResolutionReason
    missing_trusted_evidence: bool
    candidate_observed: bool = False
    foreign_candidate: bool = False
    dependency_terminal: bool = False
    candidate_trusted: bool = False
    authoritative_satisfaction: bool = False

    def __post_init__(self) -> None:
        _token(self.step_id, "step_id")
        _token(self.invocation_id, "invocation_id")
        if not isinstance(self.candidate_observed, bool) or not isinstance(self.foreign_candidate, bool):
            raise ContractValidationError("invalid_candidate_application")
        _false(self.candidate_trusted, "candidate_evidence_not_trusted")
        _false(self.authoritative_satisfaction, "authoritative_satisfaction_not_available")
        state, reason, missing = _resolve_gate(self.original_gate, self.dependency_terminal)
        if (
            self.effective_state is not state
            or self.reason is not reason
            or self.missing_trusted_evidence is not missing
        ):
            raise ContractValidationError("inconsistent_gate_resolution")

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "invocation_id": self.invocation_id,
            "original_gate": self.original_gate.value,
            "effective_state": self.effective_state.value,
            "reason": self.reason.value,
            "missing_trusted_evidence": self.missing_trusted_evidence,
            "candidate_observed": self.candidate_observed,
            "foreign_candidate": self.foreign_candidate,
            "dependency_terminal": self.dependency_terminal,
            "candidate_trusted": False,
            "authoritative_satisfaction": False,
        }


@dataclass(frozen=True)
class MasterDependencyEdgeInspection:
    """One planned dependency edge. The plan's requirement is authoritative."""

    dependent_step_id: str
    dependency_step_id: str
    dependent_requirement: MasterRequirement
    origin: MasterDependencyOrigin
    effect: MasterDependentEffect
    reason: MasterDependencyVerdictReason
    verdict: MasterDependencyVerdict | None = None
    native_status: Enum | None = None
    evidence_sufficient: bool = False
    trusted_execution_evidence: bool = False
    identity_matched: bool = False

    def __post_init__(self) -> None:
        _token(self.dependent_step_id, "dependent_step_id")
        _token(self.dependency_step_id, "dependency_step_id")
        _false(self.evidence_sufficient, "trusted_gate_evidence_not_available")
        _false(self.trusted_execution_evidence, "trusted_gate_evidence_not_available")
        if not isinstance(self.identity_matched, bool):
            raise ContractValidationError("invalid_candidate_application")
        expected = dependency_verdict(self.native_status, self.dependent_requirement)
        if self.identity_matched is not (self.native_status is not None):
            raise ContractValidationError("inconsistent_dependency_verdict")
        if (
            self.verdict is not expected.verdict
            or self.effect is not expected.effect
            or self.reason is not expected.reason
            or self.origin is not expected.classification
        ):
            raise ContractValidationError("inconsistent_dependency_verdict")

    def to_dict(self) -> dict[str, Any]:
        status = self.native_status
        return {
            "dependent_step_id": self.dependent_step_id,
            "dependency_step_id": self.dependency_step_id,
            "dependent_requirement": self.dependent_requirement.value,
            "origin": self.origin.value,
            "effect": self.effect.value,
            "reason": self.reason.value,
            "verdict": None if self.verdict is None else self.verdict.value,
            "native_status": None if status is None else status.value,
            "native_status_type": None if status is None else type(status).__name__,
            "evidence_sufficient": False,
            "trusted_execution_evidence": False,
            "identity_matched": self.identity_matched,
        }


@dataclass(frozen=True)
class MasterPlanningOmissionInspection:
    """A planning-time omission kept apart from runtime dependency outcomes."""

    dependent_step_id: str
    omitted_dependency_id: str
    requirement: MasterRequirement
    requirement_source: MasterRequirementSource
    effect: MasterDependentEffect
    reason: MasterDependencyVerdictReason
    dependency_still_planned: bool
    note_requirement_ignored: bool = False
    applied_as_runtime_failure: bool = False
    removed_from_plan: bool = False

    def __post_init__(self) -> None:
        _token(self.dependent_step_id, "dependent_step_id")
        _token(self.omitted_dependency_id, "omitted_dependency_id")
        if not isinstance(self.requirement_source, MasterRequirementSource):
            raise ContractValidationError("invalid_requirement_source")
        if not isinstance(self.dependency_still_planned, bool) or not isinstance(
            self.note_requirement_ignored, bool
        ):
            raise ContractValidationError("invalid_planning_omission")
        _false(self.applied_as_runtime_failure, "planning_omission_is_not_runtime_outcome")
        _false(self.removed_from_plan, "planning_omission_cannot_change_plan")
        expected = dependency_verdict(None, self.requirement, planning_omission=True)
        if self.effect is not expected.effect or self.reason is not expected.reason:
            raise ContractValidationError("planning_omission_is_not_runtime_outcome")

    def to_dict(self) -> dict[str, Any]:
        return {
            "dependent_step_id": self.dependent_step_id,
            "omitted_dependency_id": self.omitted_dependency_id,
            "requirement": self.requirement.value,
            "requirement_source": self.requirement_source.value,
            "effect": self.effect.value,
            "reason": self.reason.value,
            "dependency_still_planned": self.dependency_still_planned,
            "note_requirement_ignored": self.note_requirement_ignored,
            "applied_as_runtime_failure": False,
            "removed_from_plan": False,
        }


@dataclass(frozen=True)
class MasterGateInspection:
    """Offline inspection of one plan. The plan object is unchanged."""

    plan: MasterRoutePlan
    findings: tuple[MasterGateFinding, ...]
    dependency_edges: tuple[MasterDependencyEdgeInspection, ...]
    observed_candidates: tuple[MasterObservedCandidate, ...]
    planning_omissions: tuple[MasterPlanningOmissionInspection, ...]
    schema_version: str = MASTER_GATE_RESOLUTION_SCHEMA_VERSION
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    dispatch_permitted: bool = False
    authoritative_satisfaction: bool = False
    trusted_gate_evidence_writer: MasterTrustedGateEvidenceWriter = MasterTrustedGateEvidenceWriter.ABSENT
    limitations: tuple[MasterGateResolutionLimitation, ...] = MASTER_GATE_RESOLUTION_LIMITATIONS
    proposed_interfaces: tuple[str, ...] = PROPOSED_INTERFACES
    implemented_cross_module_interfaces: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_GATE_RESOLUTION_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _reject_forged_authority(
            execution_authority=self.execution_authority,
            production_authority=self.production_authority,
            source_authenticity=self.source_authenticity,
            request_coverage=self.request_coverage,
            dispatch_permitted=self.dispatch_permitted,
            authoritative_satisfaction=self.authoritative_satisfaction,
        )
        if self.trusted_gate_evidence_writer is not MasterTrustedGateEvidenceWriter.ABSENT:
            raise ContractValidationError("trusted_gate_evidence_not_available")
        if self.limitations != MASTER_GATE_RESOLUTION_LIMITATIONS:
            raise ContractValidationError("invalid_gate_resolution_limitations")
        if self.proposed_interfaces != PROPOSED_INTERFACES:
            raise ContractValidationError("invalid_proposed_interface")
        if self.implemented_cross_module_interfaces != ():
            raise ContractValidationError("cross_module_interface_not_implemented")
        if not isinstance(self.plan, MasterRoutePlan):
            raise ContractValidationError("invalid_master_route_plan")
        self._check_shape()

    def _check_shape(self) -> None:
        if not isinstance(self.findings, tuple) or any(
            not isinstance(item, MasterGateFinding) for item in self.findings
        ):
            raise ContractValidationError("invalid_gate_findings")
        if not isinstance(self.dependency_edges, tuple) or any(
            not isinstance(item, MasterDependencyEdgeInspection) for item in self.dependency_edges
        ):
            raise ContractValidationError("invalid_dependency_edges")
        if not isinstance(self.observed_candidates, tuple) or any(
            not isinstance(item, MasterObservedCandidate) for item in self.observed_candidates
        ):
            raise ContractValidationError("invalid_observed_candidates")
        if not isinstance(self.planning_omissions, tuple) or any(
            not isinstance(item, MasterPlanningOmissionInspection) for item in self.planning_omissions
        ):
            raise ContractValidationError("invalid_planning_omission")
        by_id = {}
        for step in self.plan.steps:
            if not isinstance(step, MasterRouteStepPlan):
                raise ContractValidationError("invalid_route_step_plan")
            by_id[step.step_id] = step
        expected_findings = [
            (step.step_id, step.invocation.invocation_id, gate) for step in self.plan.steps for gate in step.gates
        ]
        actual_findings = [
            (item.step_id, item.invocation_id, item.original_gate) for item in self.findings
        ]
        if actual_findings != expected_findings:
            raise ContractValidationError("planned_gates_not_preserved")
        expected_edges = [
            (step.step_id, dependency) for step in self.plan.steps for dependency in step.depends_on
        ]
        actual_edges = [
            (item.dependent_step_id, item.dependency_step_id) for item in self.dependency_edges
        ]
        if actual_edges != expected_edges:
            raise ContractValidationError("planned_dependencies_not_preserved")
        terminal_steps = {
            item.dependent_step_id
            for item in self.dependency_edges
            if item.effect is MasterDependentEffect.REQUIRED_TERMINAL_FAILURE
        }
        for finding in self.findings:
            if finding.original_gate is MasterRouteGate.DEPENDENCY_BLOCKED:
                if finding.dependency_terminal is not (finding.step_id in terminal_steps):
                    raise ContractValidationError("inconsistent_gate_resolution")
            elif finding.dependency_terminal:
                raise ContractValidationError("inconsistent_gate_resolution")
        for edge in self.dependency_edges:
            dependent = by_id[edge.dependent_step_id]
            if edge.dependent_requirement is not dependent.invocation.requirement:
                raise ContractValidationError("dependent_requirement_mismatch")
        planned_ids = set(by_id)
        for omission in self.planning_omissions:
            if omission.dependency_still_planned is not (omission.omitted_dependency_id in planned_ids):
                raise ContractValidationError("planning_omission_cannot_change_plan")
            if omission.removed_from_plan:
                raise ContractValidationError("planning_omission_cannot_change_plan")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "master_request_id": self.plan.master_request_id,
            "planned_gates": [
                {"step_id": step.step_id, "gates": [gate.value for gate in step.gates]}
                for step in self.plan.steps
            ],
            "findings": [item.to_dict() for item in self.findings],
            "dependency_edges": [item.to_dict() for item in self.dependency_edges],
            "observed_candidates": [
                {
                    "step_id": item.step_id,
                    "invocation_id": item.invocation_id,
                    "kind": item.kind.value,
                    "identity_matches_plan": item.identity_matches_plan,
                    "applied": item.applied,
                    "trust": item.trust.value,
                    "gate": None if item.gate is None else item.gate.value,
                    "native_status_value": item.native_status_value,
                    "native_status_type": item.native_status_type,
                    "authoritative": False,
                }
                for item in self.observed_candidates
            ],
            "planning_omissions": [item.to_dict() for item in self.planning_omissions],
            "execution_authority": MasterRouteExecutionAuthority.DENIED.value,
            "production_authority": MasterProductionAuthority.DENIED.value,
            "source_authenticity": MasterSourceAuthenticity.NOT_ESTABLISHED.value,
            "request_coverage": MasterRequestCoverage.NOT_ESTABLISHED.value,
            "dispatch_permitted": False,
            "authoritative_satisfaction": False,
            "trusted_gate_evidence_writer": MasterTrustedGateEvidenceWriter.ABSENT.value,
            "limitations": [item.value for item in self.limitations],
            "proposed_interfaces": list(self.proposed_interfaces),
            "implemented_cross_module_interfaces": [],
        }


def _tuple(value: object, field_name: str) -> tuple[Any, ...]:
    if not isinstance(value, tuple):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _dependency_candidates(
    observations: tuple[Any, ...],
    by_id: dict[str, MasterRouteStepPlan],
) -> tuple[dict[str, MasterDependencyCandidate], tuple[MasterObservedCandidate, ...]]:
    matched: dict[str, MasterDependencyCandidate] = {}
    recorded: list[MasterObservedCandidate] = []
    seen: set[str] = set()
    for item in observations:
        if not isinstance(item, MasterDependencyCandidate):
            raise TypeError("dependency candidate is required")
        if item.dependency_step_id in seen:
            raise ContractValidationError("duplicate_dependency_observation")
        seen.add(item.dependency_step_id)
        step = by_id.get(item.dependency_step_id)
        identity_matches = (
            step is not None and step.invocation.invocation_id == item.invocation_id
        )
        applied = bool(identity_matches and step is not None and _status_matches(step.invocation.specialist, item.native_status))
        if applied:
            matched[item.dependency_step_id] = item
        recorded.append(
            MasterObservedCandidate(
                step_id=item.dependency_step_id,
                invocation_id=item.invocation_id,
                kind=MasterCandidateKind.DEPENDENCY_STATUS,
                identity_matches_plan=identity_matches,
                applied=applied,
                native_status_value=item.native_status.value,
                native_status_type=type(item.native_status).__name__,
            )
        )
    return matched, tuple(recorded)


def _gate_candidates(
    observations: tuple[Any, ...],
    by_id: dict[str, MasterRouteStepPlan],
) -> tuple[dict[tuple[str, MasterRouteGate], MasterGateCandidateObservation], tuple[MasterObservedCandidate, ...]]:
    matched: dict[tuple[str, MasterRouteGate], MasterGateCandidateObservation] = {}
    recorded: list[MasterObservedCandidate] = []
    seen: set[tuple[str, MasterRouteGate]] = set()
    for item in observations:
        if not isinstance(item, MasterGateCandidateObservation):
            raise TypeError("gate candidate is required")
        key = (item.step_id, item.gate)
        if key in seen:
            raise ContractValidationError("duplicate_gate_observation")
        seen.add(key)
        step = by_id.get(item.step_id)
        identity_matches = step is not None and step.invocation.invocation_id == item.invocation_id
        applied = bool(identity_matches and step is not None and item.gate in step.gates)
        if applied:
            matched[key] = item
        recorded.append(
            MasterObservedCandidate(
                step_id=item.step_id,
                invocation_id=item.invocation_id,
                kind=MasterCandidateKind.GATE,
                gate=item.gate,
                identity_matches_plan=identity_matches,
                applied=applied,
            )
        )
    return matched, tuple(recorded)


def _edge(
    dependent: MasterRouteStepPlan,
    dependency_id: str,
    matched: dict[str, MasterDependencyCandidate],
) -> MasterDependencyEdgeInspection:
    observation = matched.get(dependency_id)
    native_status = None if observation is None else observation.native_status
    classified = dependency_verdict(native_status, dependent.invocation.requirement)
    return MasterDependencyEdgeInspection(
        dependent_step_id=dependent.step_id,
        dependency_step_id=dependency_id,
        dependent_requirement=dependent.invocation.requirement,
        origin=classified.classification,
        effect=classified.effect,
        reason=classified.reason,
        verdict=classified.verdict,
        native_status=native_status,
        identity_matched=native_status is not None,
    )


def _omissions(
    notes: tuple[Any, ...],
    by_id: dict[str, MasterRouteStepPlan],
) -> tuple[MasterPlanningOmissionInspection, ...]:
    seen: set[tuple[str, str]] = set()
    recorded: list[MasterPlanningOmissionInspection] = []
    for note in notes:
        if not isinstance(note, MasterPlanningOmissionNote):
            raise TypeError("planning omission is required")
        key = (note.dependent_step_id, note.omitted_dependency_id)
        if key in seen:
            raise ContractValidationError("duplicate_planning_omission")
        seen.add(key)
        planned = by_id.get(note.dependent_step_id)
        if planned is None:
            requirement = note.dependent_requirement
            source = MasterRequirementSource.OMISSION_NOTE
            ignored = False
        else:
            requirement = planned.invocation.requirement
            source = MasterRequirementSource.PLAN
            ignored = note.dependent_requirement is not planned.invocation.requirement
        classified = dependency_verdict(None, requirement, planning_omission=True)
        recorded.append(
            MasterPlanningOmissionInspection(
                dependent_step_id=note.dependent_step_id,
                omitted_dependency_id=note.omitted_dependency_id,
                requirement=requirement,
                requirement_source=source,
                effect=classified.effect,
                reason=classified.reason,
                dependency_still_planned=note.omitted_dependency_id in by_id,
                note_requirement_ignored=ignored,
            )
        )
    return tuple(recorded)


def inspect_route_gates(
    plan: MasterRoutePlan,
    observations: tuple[MasterDependencyCandidate, ...] = (),
    gate_observations: tuple[MasterGateCandidateObservation, ...] = (),
    planning_omissions: tuple[MasterPlanningOmissionNote, ...] = (),
    *,
    execution_authority: object = MasterRouteExecutionAuthority.DENIED,
    production_authority: object = MasterProductionAuthority.DENIED,
    source_authenticity: object = MasterSourceAuthenticity.NOT_ESTABLISHED,
    request_coverage: object = MasterRequestCoverage.NOT_ESTABLISHED,
    dispatch_permitted: object = False,
    authoritative_satisfaction: object = False,
) -> MasterGateInspection:
    """Inspect one plan. The returned record does not replace or modify it.

    Caller observations stay untrusted. Gates that need a trusted writer stay
    unresolved, except a required dependency whose observed runtime outcome is
    a terminal failure. That failure is not satisfaction and it does not edit
    the original gate list.
    """

    if not isinstance(plan, MasterRoutePlan):
        raise TypeError("plan is required")
    _reject_forged_authority(
        execution_authority=execution_authority,
        production_authority=production_authority,
        source_authenticity=source_authenticity,
        request_coverage=request_coverage,
        dispatch_permitted=dispatch_permitted,
        authoritative_satisfaction=authoritative_satisfaction,
    )
    observation_tuple = _tuple(observations, "observations")
    gate_tuple = _tuple(gate_observations, "gate_observations")
    omission_tuple = _tuple(planning_omissions, "planning_omissions")
    before = _snapshot(plan)
    by_id = {step.step_id: step for step in plan.steps}
    matched, dependency_records = _dependency_candidates(observation_tuple, by_id)
    matched_gates, gate_records = _gate_candidates(gate_tuple, by_id)
    edges = tuple(_edge(step, dependency_id, matched) for step in plan.steps for dependency_id in step.depends_on)
    terminal_steps = {
        edge.dependent_step_id
        for edge in edges
        if edge.effect is MasterDependentEffect.REQUIRED_TERMINAL_FAILURE
    }
    foreign_dependencies = {
        item.step_id
        for item in dependency_records
        if not item.identity_matches_plan
    }
    foreign_gates = {
        (item.step_id, item.gate)
        for item in gate_records
        if item.gate is not None and not item.applied
    }
    findings: list[MasterGateFinding] = []
    for step in plan.steps:
        dependency_ids = set(step.depends_on)
        for gate in step.gates:
            dependency_terminal = (
                gate is MasterRouteGate.DEPENDENCY_BLOCKED and step.step_id in terminal_steps
            )
            state, reason, missing = _resolve_gate(gate, dependency_terminal)
            if gate is MasterRouteGate.DEPENDENCY_BLOCKED:
                candidate_observed = any(edge.identity_matched for edge in edges if edge.dependent_step_id == step.step_id)
                foreign_candidate = any(dependency_id in foreign_dependencies for dependency_id in dependency_ids)
            else:
                candidate_observed = (step.step_id, gate) in matched_gates
                foreign_candidate = (step.step_id, gate) in foreign_gates
            findings.append(
                MasterGateFinding(
                    step_id=step.step_id,
                    invocation_id=step.invocation.invocation_id,
                    original_gate=gate,
                    effective_state=state,
                    reason=reason,
                    missing_trusted_evidence=missing,
                    candidate_observed=candidate_observed,
                    foreign_candidate=foreign_candidate,
                    dependency_terminal=dependency_terminal,
                )
            )
    inspection = MasterGateInspection(
        plan=plan,
        findings=tuple(findings),
        dependency_edges=edges,
        observed_candidates=dependency_records + gate_records,
        planning_omissions=_omissions(omission_tuple, by_id),
    )
    if _snapshot(plan) != before:
        raise ContractValidationError("master_plan_mutated")
    return inspection


__all__ = [
    "MASTER_GATE_RESOLUTION_LIMITATIONS",
    "MASTER_GATE_RESOLUTION_SCHEMA_VERSION",
    "PROPOSED_INTERFACES",
    "MasterCandidateKind",
    "MasterDependencyCandidate",
    "MasterDependencyEdgeInspection",
    "MasterDependencyOrigin",
    "MasterDependencyVerdict",
    "MasterDependencyVerdictReason",
    "MasterDependencyVerdictResult",
    "MasterDependentEffect",
    "MasterGateCandidateObservation",
    "MasterGateEffectiveState",
    "MasterGateEvidenceTrust",
    "MasterGateFinding",
    "MasterGateInspection",
    "MasterGateResolutionLimitation",
    "MasterGateResolutionReason",
    "MasterObservedCandidate",
    "MasterPlanningOmissionInspection",
    "MasterPlanningOmissionNote",
    "MasterRequirementSource",
    "MasterTrustedGateEvidenceWriter",
    "dependency_verdict",
    "inspect_route_gates",
]
