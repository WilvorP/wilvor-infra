"""Deterministic Master evidence admission. Not source authentication.

Precedence when categories overlap:

1. ``REJECTED`` wins when a required structural or identity check fails.
2. ``BINDING_UNRESOLVED`` wins when the native artifact lacks a field needed
   to check a requested binding.
3. Otherwise the result is ``CONTRACT_ONLY``.

``CONTRACT_ONLY`` means the expected native type was present and the
available structural checks passed. It does not mean the artifact is
verified, authenticated, current, or executable. Source authenticity stays
``NOT_ESTABLISHED``. This module does not call specialists, models, AWS, or
LangGraph, and it does not clear MA2 gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from wilvor_ai.contracts import ContractValidationError, TemporalScope
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionSpecialistRunResult,
    DecisionTargetMode,
)
from wilvor_ai.live_ops_public_contracts import (
    LiveOpsPublicVerificationOutcome,
    LiveOpsRunRequest,
    LiveOpsRunResult,
    LiveOpsTrustedContext,
    present_live_ops_result,
    validate_live_ops_presentation,
)
from wilvor_ai.master_contracts import (
    MasterBindingDefect,
    MasterInvocationAuthorization,
    MasterInvocationRecord,
    MasterClassification,
    MasterProductionAuthority,
    MasterPurpose,
    MasterSpecialist,
)
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS,
    MAX_ROUTE_STEPS,
    MasterRequestCoverage,
    MasterRouteDecision,
    MasterRoutePlan,
)
from wilvor_ai.specialist_contracts import (
    SpecialistResult,
    SpecialistStatus,
    VerifiedZeroClaim,
    VerifierOutcome,
)
from wilvor_ai.specialist_runtime_contracts import HistoricalSpecialistRunResult


MASTER_EVIDENCE_ADMISSION_SCHEMA_VERSION = "wilvor.ai.master_evidence_admission.v1"
_TRUNCATED = "RESULT_TRUNCATED"
_ALLOWED_PURPOSE = {
    MasterSpecialist.HISTORICAL_ANALYTICS: frozenset({MasterPurpose.ANSWER}),
    MasterSpecialist.DECISION: frozenset({MasterPurpose.ANSWER}),
    MasterSpecialist.LIVE_OPS: frozenset({MasterPurpose.DISCOVERY, MasterPurpose.ANSWER}),
}


class MasterAdmissionStatus(str, Enum):
    """Closed admission outcome. None of these values authenticate evidence."""

    REJECTED = "REJECTED"
    CONTRACT_ONLY = "CONTRACT_ONLY"
    BINDING_UNRESOLVED = "BINDING_UNRESOLVED"


class MasterSourceAuthenticity(str, Enum):
    """Production source authenticity is not established by admission."""

    NOT_ESTABLISHED = "NOT_ESTABLISHED"


class MasterAdmissionReason(str, Enum):
    """Closed admission reasons. Failures and limitations stay distinct codes."""

    MISSING_NATIVE_ARTIFACT = "MISSING_NATIVE_ARTIFACT"
    UNSUPPORTED_ARTIFACT = "UNSUPPORTED_ARTIFACT"
    SERIALIZED_RECORD = "SERIALIZED_RECORD"
    CLASSIFICATION_NOT_NATIVE_ARTIFACT = "CLASSIFICATION_NOT_NATIVE_ARTIFACT"
    ROUTE_PLAN_NOT_EVIDENCE = "ROUTE_PLAN_NOT_EVIDENCE"
    SPECIALIST_ARTIFACT_MISMATCH = "SPECIALIST_ARTIFACT_MISMATCH"
    INVALID_PURPOSE = "INVALID_PURPOSE"
    REQUEST_IDENTITY_NOT_DISTINCT = "REQUEST_IDENTITY_NOT_DISTINCT"
    INVOCATION_ID_MISMATCH = "INVOCATION_ID_MISMATCH"
    PRESENTATION_INVALID = "PRESENTATION_INVALID"
    HISTORICAL_RUN_NOT_FINAL_ARTIFACT = "HISTORICAL_RUN_NOT_FINAL_ARTIFACT"
    NON_HISTORICAL_SCOPE = "NON_HISTORICAL_SCOPE"
    NATIVE_CORRELATION_ABSENT = "NATIVE_CORRELATION_ABSENT"
    NATIVE_SPECIALIST_REQUEST_ID_ABSENT = "NATIVE_SPECIALIST_REQUEST_ID_ABSENT"
    NATIVE_EVALUATION_TIME_ABSENT = "NATIVE_EVALUATION_TIME_ABSENT"
    DECISION_TARGET_CANDIDATE_ABSENT = "DECISION_TARGET_CANDIDATE_ABSENT"
    EXPECTED_AIRCRAFT_IS_COMPARISON_CANDIDATE = "EXPECTED_AIRCRAFT_IS_COMPARISON_CANDIDATE"
    CALLSIGN_DISCOVERY_NOT_TARGET = "CALLSIGN_DISCOVERY_NOT_TARGET"
    HISTORICAL_VERIFIER_NOT_SOURCE_AUTHENTICATION = "HISTORICAL_VERIFIER_NOT_SOURCE_AUTHENTICATION"
    DECISION_VERIFIER_NOT_SOURCE_AUTHENTICATION = "DECISION_VERIFIER_NOT_SOURCE_AUTHENTICATION"
    LIVE_OPS_ALL_VERIFIED_NOT_SOURCE_AUTHENTICATION = "LIVE_OPS_ALL_VERIFIED_NOT_SOURCE_AUTHENTICATION"
    LIVE_OPS_PARTIALLY_VERIFIED = "LIVE_OPS_PARTIALLY_VERIFIED"
    LIVE_OPS_NONE_VERIFIED = "LIVE_OPS_NONE_VERIFIED"
    LIVE_OPS_NOT_AUTHORIZED = "LIVE_OPS_NOT_AUTHORIZED"
    INCOMPLETE_SPECIALIST_STATUS = "INCOMPLETE_SPECIALIST_STATUS"
    TRUNCATED_EVIDENCE = "TRUNCATED_EVIDENCE"
    VERIFIED_ZERO_NOT_INFERRED = "VERIFIED_ZERO_NOT_INFERRED"
    VERIFIED_ZERO_CLAIM_NOT_AUTHENTICATED = "VERIFIED_ZERO_CLAIM_NOT_AUTHENTICATED"
    GEOGRAPHIC_PROVENANCE_ABSENT = "GEOGRAPHIC_PROVENANCE_ABSENT"
    SOURCE_AUTHENTICITY_NOT_ESTABLISHED = "SOURCE_AUTHENTICITY_NOT_ESTABLISHED"
    HISTORICAL_RUN_CANNOT_REPRODUCE_VERIFICATION = "HISTORICAL_RUN_CANNOT_REPRODUCE_VERIFICATION"
    DECISION_TEMPORAL_SCOPE_NOT_CARRIED = "DECISION_TEMPORAL_SCOPE_NOT_CARRIED"
    LIVE_OPS_EVIDENCE_REFS_ARE_NOT_TOOL_RESULTS = "LIVE_OPS_EVIDENCE_REFS_ARE_NOT_TOOL_RESULTS"


class MasterTemporalLimitation(str, Enum):
    """Clocks and scopes copied or found missing. Not a freshness threshold."""

    FRESHNESS_UNKNOWN = "FRESHNESS_UNKNOWN"
    EVALUATION_TIME_ABSENT = "EVALUATION_TIME_ABSENT"
    EVALUATION_TIME_MISMATCH = "EVALUATION_TIME_MISMATCH"
    HISTORICAL_SCOPE = "HISTORICAL_SCOPE"
    DECISION_SCOPE_CURRENT = "DECISION_SCOPE_CURRENT"
    DECISION_SCOPE_PERSISTED = "DECISION_SCOPE_PERSISTED"
    DECISION_SCOPE_NOT_CARRIED = "DECISION_SCOPE_NOT_CARRIED"
    LIVE_OPS_REFERENCE_TIME_NOT_FRESHNESS = "LIVE_OPS_REFERENCE_TIME_NOT_FRESHNESS"


@dataclass(frozen=True)
class LiveOpsAdmissionSources:
    """The three Live Ops objects required for structural binding checks."""

    request: LiveOpsRunRequest
    context: LiveOpsTrustedContext
    result: LiveOpsRunResult

    def __post_init__(self) -> None:
        if not isinstance(self.request, LiveOpsRunRequest):
            raise ContractValidationError("invalid_live_ops_request")
        if not isinstance(self.context, LiveOpsTrustedContext):
            raise ContractValidationError("invalid_live_ops_context")
        if not isinstance(self.result, LiveOpsRunResult):
            raise ContractValidationError("invalid_live_ops_result")


@dataclass(frozen=True)
class MasterEvidenceAdmissionInput:
    """One authorization and one native artifact. The authorization is not execution."""

    authorization: MasterInvocationAuthorization
    artifact: Any

    def __post_init__(self) -> None:
        if not isinstance(self.authorization, MasterInvocationAuthorization):
            raise ContractValidationError("invalid_authorization")


@dataclass(frozen=True)
class MasterEvidenceAdmission:
    """Structural admission of one invocation. It contains no factual answer."""

    master_request_id: str
    invocation_id: str
    specialist: MasterSpecialist
    admission_status: MasterAdmissionStatus
    reason_codes: tuple[MasterAdmissionReason, ...]
    binding_defects: tuple[MasterBindingDefect, ...]
    temporal_limitations: tuple[MasterTemporalLimitation, ...]
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    evidence_verified: bool = False
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    end_to_end_fulfilled: bool = False
    schema_version: str = MASTER_EVIDENCE_ADMISSION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_EVIDENCE_ADMISSION_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            raise ContractValidationError("source_authenticity_not_established")
        if self.evidence_verified is not False:
            raise ContractValidationError("synthetic_evidence_not_verified")
        if self.production_authority is not MasterProductionAuthority.DENIED:
            raise ContractValidationError("production_authority_not_issued")
        if self.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
            raise ContractValidationError("request_coverage_not_established")
        if self.end_to_end_fulfilled is not False:
            raise ContractValidationError("end_to_end_fulfillment_not_granted")
        if not isinstance(self.admission_status, MasterAdmissionStatus):
            raise ContractValidationError("invalid_admission_status")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        _enum_tuple(self.reason_codes, MasterAdmissionReason, "reason_codes")
        _enum_tuple(self.binding_defects, MasterBindingDefect, "binding_defects")
        _enum_tuple(self.temporal_limitations, MasterTemporalLimitation, "temporal_limitations")


@dataclass(frozen=True)
class MasterEvidenceAdmissionCollection:
    """Admissions for one Master request. The collection grants nothing."""

    master_request_id: str
    admissions: tuple[MasterEvidenceAdmission, ...]
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    evidence_verified: bool = False
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    end_to_end_fulfilled: bool = False
    schema_version: str = MASTER_EVIDENCE_ADMISSION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_EVIDENCE_ADMISSION_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            raise ContractValidationError("source_authenticity_not_established")
        if self.evidence_verified is not False:
            raise ContractValidationError("synthetic_evidence_not_verified")
        if self.production_authority is not MasterProductionAuthority.DENIED:
            raise ContractValidationError("production_authority_not_issued")
        if self.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
            raise ContractValidationError("request_coverage_not_established")
        if self.end_to_end_fulfilled is not False:
            raise ContractValidationError("end_to_end_fulfillment_not_granted")
        if not isinstance(self.admissions, tuple) or any(
            not isinstance(item, MasterEvidenceAdmission) for item in self.admissions
        ):
            raise ContractValidationError("invalid_admissions")
        if any(item.master_request_id != self.master_request_id for item in self.admissions):
            raise ContractValidationError("master_request_mismatch")


def admit_master_evidence(
    items: tuple[MasterEvidenceAdmissionInput, ...],
) -> MasterEvidenceAdmissionCollection:
    """Admit native artifacts for later corroboration. This does not authenticate them."""

    if not isinstance(items, tuple) or any(not isinstance(item, MasterEvidenceAdmissionInput) for item in items):
        raise ContractValidationError("invalid_admission_inputs")
    if not items:
        raise ContractValidationError("empty_admission")
    if len(items) > MAX_ROUTE_STEPS:
        raise ContractValidationError("excessive_plan_size")
    if sum(item.authorization.specialist is MasterSpecialist.DECISION for item in items) > MAX_DECISION_STEPS:
        raise ContractValidationError("decision_fanout_not_permitted")
    request_ids = {item.authorization.master_request_id for item in items}
    if len(request_ids) != 1:
        raise ContractValidationError("master_request_mismatch")
    invocation_ids = [item.authorization.invocation_id for item in items]
    if len(invocation_ids) != len(set(invocation_ids)):
        raise ContractValidationError("duplicate_invocation_id")
    admissions = tuple(_admit_one(item) for item in items)
    return MasterEvidenceAdmissionCollection(
        master_request_id=items[0].authorization.master_request_id,
        admissions=admissions,
    )


def _admit_one(item: MasterEvidenceAdmissionInput) -> MasterEvidenceAdmission:
    authorization = item.authorization
    reasons: set[MasterAdmissionReason] = {MasterAdmissionReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED}
    reasons.add(MasterAdmissionReason.GEOGRAPHIC_PROVENANCE_ABSENT)
    defects: set[MasterBindingDefect] = set()
    temporal: set[MasterTemporalLimitation] = {MasterTemporalLimitation.FRESHNESS_UNKNOWN}
    rejected = False
    unresolved = False
    if authorization.invocation_id == authorization.master_request_id:
        rejected = True
        reasons.add(MasterAdmissionReason.REQUEST_IDENTITY_NOT_DISTINCT)
    if authorization.purpose not in _ALLOWED_PURPOSE[authorization.specialist]:
        rejected = True
        reasons.add(MasterAdmissionReason.INVALID_PURPOSE)
    family = _family(item.artifact)
    if family == "missing":
        rejected = True
        reasons.add(MasterAdmissionReason.MISSING_NATIVE_ARTIFACT)
    elif family == "historical_run":
        rejected = True
        reasons.add(MasterAdmissionReason.HISTORICAL_RUN_NOT_FINAL_ARTIFACT)
        reasons.add(MasterAdmissionReason.HISTORICAL_RUN_CANNOT_REPRODUCE_VERIFICATION)
    elif family != authorization.specialist.value:
        rejected = True
        reasons.add(_foreign_reason(item.artifact, family))
        if isinstance(item.artifact, MasterInvocationRecord) and item.artifact.invocation_id != authorization.invocation_id:
            reasons.add(MasterAdmissionReason.INVOCATION_ID_MISMATCH)
            defects.add(MasterBindingDefect.REQUEST_ID_MISMATCH)
    elif authorization.specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
        rejected, unresolved = _historical(authorization, item.artifact, reasons, defects, temporal, rejected, unresolved)
    elif authorization.specialist is MasterSpecialist.DECISION:
        rejected, unresolved = _decision(authorization, item.artifact, reasons, defects, temporal, rejected, unresolved)
    else:
        rejected, unresolved = _live_ops(authorization, item.artifact, reasons, defects, temporal, rejected, unresolved)
    return _result(authorization, _status(rejected, unresolved), reasons, defects, temporal)


def _historical(
    authorization: MasterInvocationAuthorization,
    artifact: SpecialistResult,
    reasons: set[MasterAdmissionReason],
    defects: set[MasterBindingDefect],
    temporal: set[MasterTemporalLimitation],
    rejected: bool,
    unresolved: bool,
) -> tuple[bool, bool]:
    reasons.add(MasterAdmissionReason.HISTORICAL_VERIFIER_NOT_SOURCE_AUTHENTICATION)
    if artifact.temporal_scope is TemporalScope.HISTORICAL:
        temporal.add(MasterTemporalLimitation.HISTORICAL_SCOPE)
    else:
        rejected = True
        reasons.add(MasterAdmissionReason.NON_HISTORICAL_SCOPE)
    if authorization.correlation_id is not None:
        unresolved = True
        reasons.add(MasterAdmissionReason.NATIVE_CORRELATION_ABSENT)
    if authorization.specialist_request_id is not None:
        unresolved = True
        reasons.add(MasterAdmissionReason.NATIVE_SPECIALIST_REQUEST_ID_ABSENT)
    requested = authorization.requested_evaluation_time_utc
    observed = artifact.evaluated_as_of_utc
    if requested is not None and observed is None:
        unresolved = True
        reasons.add(MasterAdmissionReason.NATIVE_EVALUATION_TIME_ABSENT)
        temporal.add(MasterTemporalLimitation.EVALUATION_TIME_ABSENT)
    elif requested is not None and observed is not None and requested != observed:
        rejected = True
        defects.add(MasterBindingDefect.TIME_MISMATCH)
        temporal.add(MasterTemporalLimitation.EVALUATION_TIME_MISMATCH)
    elif observed is None:
        temporal.add(MasterTemporalLimitation.EVALUATION_TIME_ABSENT)
    if artifact.status in {SpecialistStatus.PARTIAL, SpecialistStatus.UNAVAILABLE}:
        reasons.add(MasterAdmissionReason.INCOMPLETE_SPECIALIST_STATUS)
    if _TRUNCATED in artifact.limitations:
        reasons.add(MasterAdmissionReason.TRUNCATED_EVIDENCE)
    if any(isinstance(item, VerifiedZeroClaim) for item in artifact.verified_claims):
        reasons.add(MasterAdmissionReason.VERIFIED_ZERO_CLAIM_NOT_AUTHENTICATED)
    else:
        reasons.add(MasterAdmissionReason.VERIFIED_ZERO_NOT_INFERRED)
    if artifact.verifier_outcome is VerifierOutcome.PASSED:
        reasons.add(MasterAdmissionReason.HISTORICAL_VERIFIER_NOT_SOURCE_AUTHENTICATION)
    return rejected, unresolved


def _decision(
    authorization: MasterInvocationAuthorization,
    artifact: DecisionSpecialistRunResult,
    reasons: set[MasterAdmissionReason],
    defects: set[MasterBindingDefect],
    temporal: set[MasterTemporalLimitation],
    rejected: bool,
    unresolved: bool,
) -> tuple[bool, bool]:
    reasons.add(MasterAdmissionReason.DECISION_VERIFIER_NOT_SOURCE_AUTHENTICATION)
    if authorization.correlation_id is not None:
        unresolved = True
        reasons.add(MasterAdmissionReason.NATIVE_CORRELATION_ABSENT)
    if authorization.specialist_request_id is not None:
        unresolved = True
        reasons.add(MasterAdmissionReason.NATIVE_SPECIALIST_REQUEST_ID_ABSENT)
    verification = artifact.verification
    if verification is None:
        reasons.add(MasterAdmissionReason.DECISION_TEMPORAL_SCOPE_NOT_CARRIED)
        temporal.add(MasterTemporalLimitation.DECISION_SCOPE_NOT_CARRIED)
        if authorization.requested_evaluation_time_utc is not None:
            unresolved = True
            reasons.add(MasterAdmissionReason.NATIVE_EVALUATION_TIME_ABSENT)
            temporal.add(MasterTemporalLimitation.EVALUATION_TIME_ABSENT)
    else:
        if TemporalScope.CURRENT in verification.verified_temporal_scopes:
            temporal.add(MasterTemporalLimitation.DECISION_SCOPE_CURRENT)
        if TemporalScope.PERSISTED in verification.verified_temporal_scopes:
            temporal.add(MasterTemporalLimitation.DECISION_SCOPE_PERSISTED)
        if verification.outcome is VerifierOutcome.PASSED:
            reasons.add(MasterAdmissionReason.DECISION_VERIFIER_NOT_SOURCE_AUTHENTICATION)
        observed = verification.current_as_of_utc
        requested = authorization.requested_evaluation_time_utc
        if requested is not None and observed is None:
            unresolved = True
            reasons.add(MasterAdmissionReason.NATIVE_EVALUATION_TIME_ABSENT)
            temporal.add(MasterTemporalLimitation.EVALUATION_TIME_ABSENT)
        elif requested is not None and observed is not None and requested != observed:
            rejected = True
            defects.add(MasterBindingDefect.TIME_MISMATCH)
            temporal.add(MasterTemporalLimitation.EVALUATION_TIME_MISMATCH)
    if artifact.status.value == "PARTIAL":
        reasons.add(MasterAdmissionReason.INCOMPLETE_SPECIALIST_STATUS)
    rejected, unresolved = _decision_target(authorization, artifact, reasons, defects, rejected, unresolved)
    return rejected, unresolved


def _decision_target(
    authorization: MasterInvocationAuthorization,
    artifact: DecisionSpecialistRunResult,
    reasons: set[MasterAdmissionReason],
    defects: set[MasterBindingDefect],
    rejected: bool,
    unresolved: bool,
) -> tuple[bool, bool]:
    """Compare the authorization candidate with the artifact mode. This does not mint a target."""

    if artifact.mode is DecisionTargetMode.AIRCRAFT:
        expected = authorization.expected_aircraft_id
        actual = artifact.aircraft_id
        other = authorization.expected_recommendation_id
        if expected is not None:
            reasons.add(MasterAdmissionReason.EXPECTED_AIRCRAFT_IS_COMPARISON_CANDIDATE)
    elif artifact.mode is DecisionTargetMode.DIRECT_PERSISTED:
        expected = authorization.expected_recommendation_id
        actual = artifact.recommendation_id
        other = authorization.expected_aircraft_id
    else:
        rejected = True
        defects.add(MasterBindingDefect.TARGET_MISMATCH)
        return rejected, unresolved
    if expected is None:
        unresolved = True
        reasons.add(MasterAdmissionReason.DECISION_TARGET_CANDIDATE_ABSENT)
        return rejected, unresolved
    if other is not None or expected != actual:
        rejected = True
        defects.add(MasterBindingDefect.TARGET_MISMATCH)
    return rejected, unresolved


def _live_ops(
    authorization: MasterInvocationAuthorization,
    artifact: LiveOpsAdmissionSources,
    reasons: set[MasterAdmissionReason],
    defects: set[MasterBindingDefect],
    temporal: set[MasterTemporalLimitation],
    rejected: bool,
    unresolved: bool,
) -> tuple[bool, bool]:
    result = artifact.result
    reasons.add(MasterAdmissionReason.LIVE_OPS_EVIDENCE_REFS_ARE_NOT_TOOL_RESULTS)
    temporal.add(MasterTemporalLimitation.LIVE_OPS_REFERENCE_TIME_NOT_FRESHNESS)
    if result.discoveries:
        reasons.add(MasterAdmissionReason.CALLSIGN_DISCOVERY_NOT_TARGET)
    outcome = result.verification_outcome
    if outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED:
        reasons.add(MasterAdmissionReason.LIVE_OPS_ALL_VERIFIED_NOT_SOURCE_AUTHENTICATION)
    elif outcome is LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED:
        reasons.add(MasterAdmissionReason.LIVE_OPS_PARTIALLY_VERIFIED)
    elif outcome is LiveOpsPublicVerificationOutcome.NONE_VERIFIED:
        reasons.add(MasterAdmissionReason.LIVE_OPS_NONE_VERIFIED)
    elif outcome is LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED:
        reasons.add(MasterAdmissionReason.LIVE_OPS_NOT_AUTHORIZED)
    request_id = authorization.specialist_request_id
    if request_id is None or artifact.request.request_id is None or result.request_id is None:
        unresolved = True
        reasons.add(MasterAdmissionReason.NATIVE_SPECIALIST_REQUEST_ID_ABSENT)
    elif artifact.request.request_id != request_id or result.request_id != request_id:
        rejected = True
        defects.add(MasterBindingDefect.REQUEST_ID_MISMATCH)
    correlation_id = authorization.correlation_id
    if correlation_id is None:
        unresolved = True
        reasons.add(MasterAdmissionReason.NATIVE_CORRELATION_ABSENT)
    elif (
        result.correlation_id != correlation_id
        or artifact.context.correlation_id != result.correlation_id
    ):
        rejected = True
        defects.add(MasterBindingDefect.CORRELATION_MISMATCH)
    try:
        presented = present_live_ops_result(result)
        validate_live_ops_presentation(result, presented)
    except ContractValidationError:
        rejected = True
        reasons.add(MasterAdmissionReason.PRESENTATION_INVALID)
    return rejected, unresolved


def _family(artifact: Any) -> str:
    if artifact is None:
        return "missing"
    if isinstance(artifact, (str, bytes)) or type(artifact) is dict:
        return "plain"
    name = type(artifact).__name__
    if name in {"SyntheticWorkerResult", "SyntheticWorkflowResult"}:
        return "synthetic"
    if isinstance(artifact, (MasterRoutePlan, MasterRouteDecision)):
        return "route"
    if isinstance(artifact, MasterInvocationRecord):
        return "record"
    if isinstance(artifact, MasterClassification):
        return "classification"
    if isinstance(artifact, HistoricalSpecialistRunResult):
        return "historical_run"
    if isinstance(artifact, SpecialistResult):
        return MasterSpecialist.HISTORICAL_ANALYTICS.value
    if isinstance(artifact, DecisionSpecialistRunResult):
        return MasterSpecialist.DECISION.value
    if isinstance(artifact, LiveOpsAdmissionSources):
        return MasterSpecialist.LIVE_OPS.value
    return "plain"


def _foreign_reason(artifact: Any, family: str) -> MasterAdmissionReason:
    if family == "synthetic" or family == "plain":
        return MasterAdmissionReason.UNSUPPORTED_ARTIFACT
    if family == "route":
        return MasterAdmissionReason.ROUTE_PLAN_NOT_EVIDENCE
    if family == "record":
        return MasterAdmissionReason.SERIALIZED_RECORD
    if family == "classification":
        return MasterAdmissionReason.CLASSIFICATION_NOT_NATIVE_ARTIFACT
    if artifact is not None:
        return MasterAdmissionReason.SPECIALIST_ARTIFACT_MISMATCH
    return MasterAdmissionReason.UNSUPPORTED_ARTIFACT


def _status(rejected: bool, unresolved: bool) -> MasterAdmissionStatus:
    if rejected:
        return MasterAdmissionStatus.REJECTED
    if unresolved:
        return MasterAdmissionStatus.BINDING_UNRESOLVED
    return MasterAdmissionStatus.CONTRACT_ONLY


def _result(
    authorization: MasterInvocationAuthorization,
    status: MasterAdmissionStatus,
    reasons: set[MasterAdmissionReason],
    defects: set[MasterBindingDefect],
    temporal: set[MasterTemporalLimitation],
) -> MasterEvidenceAdmission:
    return MasterEvidenceAdmission(
        master_request_id=authorization.master_request_id,
        invocation_id=authorization.invocation_id,
        specialist=authorization.specialist,
        admission_status=status,
        reason_codes=tuple(sorted(reasons, key=lambda item: item.value)),
        binding_defects=tuple(sorted(defects, key=lambda item: item.value)),
        temporal_limitations=tuple(sorted(temporal, key=lambda item: item.value)),
    )


def _enum_tuple(value: tuple[Any, ...], enum_type: type[Enum], field_name: str) -> None:
    if not isinstance(value, tuple) or any(not isinstance(item, enum_type) for item in value):
        raise ContractValidationError(f"invalid_{field_name}")
