"""Deterministic Master evidence corroboration. Not source authentication.

A positive result means the specialist claims are consistent with the
ToolResults supplied to this function. It does not mean those ToolResults
were read from Athena, AWS, or any other production source.

Precedence:

1. ``ADMISSION_REJECTED`` when MA4.1 rejects the artifact.
2. ``INCONSISTENT`` when a recheck contradicts the supplied artifact.
3. ``NOT_RECHECKABLE`` when the raw material needed to recheck is absent.
4. ``NO_FACTUAL_CLAIMS`` when the recheck produces no factual claims.
5. ``CONSISTENT_WITH_SUPPLIED_EVIDENCE`` only for that offline finding.

This module calls ``admit_master_evidence``. It does not accept a
caller-supplied admission, classification, or stored ``PASSED`` value as
proof that admission succeeded.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from wilvor_ai.contracts import PROVENANCE_TOKEN_MAX_LENGTH, ContractValidationError, _validate_bounded_text
from wilvor_ai.decision_answer_renderer import render_decision_verification
from wilvor_ai.decision_claims import RiskAbsentClaim
from wilvor_ai.decision_evidence_verifier import verify_decision_evidence
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionSpecialistRunResult,
    DecisionSpecialistRunStatus,
)
from wilvor_ai.historical_answer_renderer import finalize_historical_specialist_run
from wilvor_ai.historical_evidence_verifier import verify_historical_specialist_run
from wilvor_ai.live_ops_public_contracts import LiveOpsPublicVerificationOutcome
from wilvor_ai.master_contracts import MasterProductionAuthority, MasterSpecialist
from wilvor_ai.master_evidence_admission import (
    MasterAdmissionReason,
    MasterAdmissionStatus,
    MasterBindingDefect,
    MasterEvidenceAdmission,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
    MasterTemporalLimitation,
    admit_master_evidence,
)
from wilvor_ai.master_routing import MAX_DECISION_STEPS, MAX_ROUTE_STEPS, MasterRequestCoverage
from wilvor_ai.specialist_contracts import SpecialistResult, SpecialistStatus, VerifierOutcome
from wilvor_ai.specialist_runtime_contracts import HistoricalSpecialistRunResult, SpecialistRunStatus


MASTER_EVIDENCE_CORROBORATION_SCHEMA_VERSION = "wilvor.ai.master_evidence_corroboration.v1"
_FACTUAL_HISTORICAL_STATUSES = frozenset(
    {
        SpecialistStatus.ANSWERED,
        SpecialistStatus.PARTIAL,
    }
)
_FINAL_DECISION = frozenset(
    {
        DecisionSpecialistRunStatus.COMPLETED,
        DecisionSpecialistRunStatus.PARTIAL,
    }
)


class MasterCorroborationStatus(str, Enum):
    """Closed corroboration outcome. Consistency is not production verification."""

    ADMISSION_REJECTED = "ADMISSION_REJECTED"
    NOT_RECHECKABLE = "NOT_RECHECKABLE"
    NO_FACTUAL_CLAIMS = "NO_FACTUAL_CLAIMS"
    INCONSISTENT = "INCONSISTENT"
    CONSISTENT_WITH_SUPPLIED_EVIDENCE = "CONSISTENT_WITH_SUPPLIED_EVIDENCE"


class MasterCorroborationReason(str, Enum):
    """Closed corroboration reasons. Failures stay distinct from limitations."""

    ADMISSION_REJECTED = "ADMISSION_REJECTED"
    ADMISSION_BINDING_UNRESOLVED = "ADMISSION_BINDING_UNRESOLVED"
    SOURCE_AUTHENTICITY_NOT_ESTABLISHED = "SOURCE_AUTHENTICITY_NOT_ESTABLISHED"
    SUPPLIED_EVIDENCE_ONLY = "SUPPLIED_EVIDENCE_ONLY"
    HISTORICAL_RUN_ABSENT = "HISTORICAL_RUN_ABSENT"
    HISTORICAL_STORED_PASSED_NOT_TRUSTED = "HISTORICAL_STORED_PASSED_NOT_TRUSTED"
    HISTORICAL_VERIFICATION_NOT_FACTUAL = "HISTORICAL_VERIFICATION_NOT_FACTUAL"
    HISTORICAL_NON_FACTUAL_RUN = "HISTORICAL_NON_FACTUAL_RUN"
    HISTORICAL_UNAVAILABLE_CORROBORATED = "HISTORICAL_UNAVAILABLE_CORROBORATED"
    HISTORICAL_NON_FACTUAL_FINAL_STATUS = "HISTORICAL_NON_FACTUAL_FINAL_STATUS"
    HISTORICAL_PARTIAL_EVIDENCE_NOT_COMPLETE = "HISTORICAL_PARTIAL_EVIDENCE_NOT_COMPLETE"
    ANSWER_MISMATCH = "ANSWER_MISMATCH"
    CLAIM_MISMATCH = "CLAIM_MISMATCH"
    TOOL_RESULT_MISMATCH = "TOOL_RESULT_MISMATCH"
    EVALUATION_TIME_MISMATCH = "EVALUATION_TIME_MISMATCH"
    STATUS_MISMATCH = "STATUS_MISMATCH"
    LIMITATION_MISMATCH = "LIMITATION_MISMATCH"
    VERIFIER_OUTCOME_MISMATCH = "VERIFIER_OUTCOME_MISMATCH"
    TEMPORAL_SCOPE_MISMATCH = "TEMPORAL_SCOPE_MISMATCH"
    UNSUPPORTED_REASON_MISMATCH = "UNSUPPORTED_REASON_MISMATCH"
    DECISION_NON_FINAL = "DECISION_NON_FINAL"
    DECISION_VERIFICATION_MISMATCH = "DECISION_VERIFICATION_MISMATCH"
    DECISION_RENDER_MISMATCH = "DECISION_RENDER_MISMATCH"
    DECISION_VERIFICATION_NOT_FACTUAL = "DECISION_VERIFICATION_NOT_FACTUAL"
    DECISION_COLLECTION_PARTIAL = "DECISION_COLLECTION_PARTIAL"
    RECORDED_RISK_ABSENCE_NOT_LOW = "RECORDED_RISK_ABSENCE_NOT_LOW"
    LIVE_OPS_RAW_EVIDENCE_UNAVAILABLE = "LIVE_OPS_RAW_EVIDENCE_UNAVAILABLE"
    LIVE_OPS_NOT_RECHECKABLE = "LIVE_OPS_NOT_RECHECKABLE"
    LIVE_OPS_NONE_VERIFIED_NO_FACTS = "LIVE_OPS_NONE_VERIFIED_NO_FACTS"


@dataclass(frozen=True)
class MasterEvidenceCorroborationInput:
    """One MA4.1 admission input and, for Historical, an optional original run."""

    admission_input: MasterEvidenceAdmissionInput
    historical_run: HistoricalSpecialistRunResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.admission_input, MasterEvidenceAdmissionInput):
            raise ContractValidationError("invalid_admission_input")
        if self.historical_run is not None and not isinstance(
            self.historical_run,
            HistoricalSpecialistRunResult,
        ):
            raise ContractValidationError("invalid_historical_run")


@dataclass(frozen=True)
class MasterEvidenceCorroboration:
    """Offline corroboration of one invocation. It contains no factual answer."""

    master_request_id: str
    invocation_id: str
    specialist: MasterSpecialist
    corroboration_status: MasterCorroborationStatus
    admission_status: MasterAdmissionStatus
    reason_codes: tuple[MasterCorroborationReason, ...]
    admission_reason_codes: tuple[MasterAdmissionReason, ...]
    binding_defects: tuple[MasterBindingDefect, ...]
    temporal_limitations: tuple[MasterTemporalLimitation, ...]
    rejected_claim_codes: tuple[str, ...] = ()
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    evidence_verified: bool = False
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    end_to_end_fulfilled: bool = False
    operationally_eligible: bool = False
    schema_version: str = MASTER_EVIDENCE_CORROBORATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_EVIDENCE_CORROBORATION_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _identity(self.master_request_id, "master_request_id")
        _identity(self.invocation_id, "invocation_id")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.corroboration_status, MasterCorroborationStatus):
            raise ContractValidationError("invalid_corroboration_status")
        if not isinstance(self.admission_status, MasterAdmissionStatus):
            raise ContractValidationError("invalid_admission_status")
        _enum_tuple(self.reason_codes, MasterCorroborationReason, "reason_codes")
        _enum_tuple(self.admission_reason_codes, MasterAdmissionReason, "admission_reason_codes")
        _enum_tuple(self.binding_defects, MasterBindingDefect, "binding_defects")
        _enum_tuple(self.temporal_limitations, MasterTemporalLimitation, "temporal_limitations")
        _strings(self.rejected_claim_codes, "rejected_claim_codes")
        if MasterCorroborationReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED not in self.reason_codes:
            raise ContractValidationError("source_authenticity_not_established")
        if MasterTemporalLimitation.FRESHNESS_UNKNOWN not in self.temporal_limitations:
            raise ContractValidationError("freshness_unknown_required")
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
        if self.operationally_eligible is not False:
            raise ContractValidationError("operational_use_not_granted")
        if (
            self.corroboration_status
            is MasterCorroborationStatus.CONSISTENT_WITH_SUPPLIED_EVIDENCE
            and self.admission_status is MasterAdmissionStatus.REJECTED
        ):
            raise ContractValidationError("rejected_admission_not_consistent")


@dataclass(frozen=True)
class MasterEvidenceCorroborationCollection:
    """Corroboration for one Master request. The collection grants nothing."""

    master_request_id: str
    results: tuple[MasterEvidenceCorroboration, ...]
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    evidence_verified: bool = False
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    end_to_end_fulfilled: bool = False
    operationally_eligible: bool = False
    schema_version: str = MASTER_EVIDENCE_CORROBORATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_EVIDENCE_CORROBORATION_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _identity(self.master_request_id, "master_request_id")
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
        if self.operationally_eligible is not False:
            raise ContractValidationError("operational_use_not_granted")
        if not isinstance(self.results, tuple) or any(
            not isinstance(item, MasterEvidenceCorroboration) for item in self.results
        ):
            raise ContractValidationError("invalid_corroboration_results")
        if not self.results:
            raise ContractValidationError("empty_corroboration")
        if len(self.results) > MAX_ROUTE_STEPS:
            raise ContractValidationError("excessive_plan_size")
        if sum(item.specialist is MasterSpecialist.DECISION for item in self.results) > MAX_DECISION_STEPS:
            raise ContractValidationError("decision_fanout_not_permitted")
        if any(item.master_request_id != self.master_request_id for item in self.results):
            raise ContractValidationError("master_request_mismatch")
        invocation_ids = [item.invocation_id for item in self.results]
        if len(invocation_ids) != len(set(invocation_ids)):
            raise ContractValidationError("duplicate_invocation_id")


def corroborate_master_evidence(
    items: tuple[MasterEvidenceCorroborationInput, ...],
) -> MasterEvidenceCorroborationCollection:
    """Recheck admitted artifacts against supplied tool evidence.

    Collection limits and request isolation come from ``admit_master_evidence``.
    This function does not call AWS, a provider, a specialist runner, or LangGraph.
    """

    if not isinstance(items, tuple) or any(
        not isinstance(item, MasterEvidenceCorroborationInput) for item in items
    ):
        raise ContractValidationError("invalid_corroboration_inputs")
    admitted = admit_master_evidence(tuple(item.admission_input for item in items))
    results = tuple(
        _corroborate_one(item, admission)
        for item, admission in zip(items, admitted.admissions, strict=True)
    )
    return MasterEvidenceCorroborationCollection(
        master_request_id=admitted.master_request_id,
        results=results,
    )


def _corroborate_one(
    item: MasterEvidenceCorroborationInput,
    admission: MasterEvidenceAdmission,
) -> MasterEvidenceCorroboration:
    if item.historical_run is not None and admission.specialist is not MasterSpecialist.HISTORICAL_ANALYTICS:
        raise ContractValidationError("historical_run_not_applicable")
    reasons = {MasterCorroborationReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED}
    if admission.admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED:
        reasons.add(MasterCorroborationReason.ADMISSION_BINDING_UNRESOLVED)
    if admission.admission_status is MasterAdmissionStatus.REJECTED:
        reasons.add(MasterCorroborationReason.ADMISSION_REJECTED)
        return _finish(
            admission,
            MasterCorroborationStatus.ADMISSION_REJECTED,
            reasons,
            (),
        )
    artifact = item.admission_input.artifact
    if admission.specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
        status, extra, codes = _historical(artifact, item.historical_run)
    elif admission.specialist is MasterSpecialist.DECISION:
        status, extra, codes = _decision(artifact)
    else:
        status, extra, codes = _live_ops(artifact)
    reasons.update(extra)
    return _finish(admission, status, reasons, codes)


def _historical(
    artifact: SpecialistResult,
    run: HistoricalSpecialistRunResult | None,
) -> tuple[MasterCorroborationStatus, set[MasterCorroborationReason], tuple[str, ...]]:
    reasons: set[MasterCorroborationReason] = set()
    if run is None:
        reasons.add(MasterCorroborationReason.HISTORICAL_RUN_ABSENT)
        if artifact.verifier_outcome is VerifierOutcome.PASSED:
            reasons.add(MasterCorroborationReason.HISTORICAL_STORED_PASSED_NOT_TRUSTED)
        return MasterCorroborationStatus.NOT_RECHECKABLE, reasons, ()
    if run.status is not SpecialistRunStatus.PROPOSED_CLAIMS:
        finalized = finalize_historical_specialist_run(run)
        mismatches = _historical_mismatches(finalized, artifact)
        if mismatches:
            reasons.update(mismatches)
            return MasterCorroborationStatus.INCONSISTENT, reasons, ()
        reasons.add(MasterCorroborationReason.HISTORICAL_NON_FACTUAL_RUN)
        return MasterCorroborationStatus.NO_FACTUAL_CLAIMS, reasons, ()
    verification = verify_historical_specialist_run(run)
    finalized = finalize_historical_specialist_run(run)
    mismatches = _historical_mismatches(finalized, artifact)
    codes = verification.rejected_claim_codes
    if mismatches:
        reasons.update(mismatches)
        return MasterCorroborationStatus.INCONSISTENT, reasons, codes
    if verification.outcome is not VerifierOutcome.PASSED:
        reasons.add(MasterCorroborationReason.HISTORICAL_VERIFICATION_NOT_FACTUAL)
        return MasterCorroborationStatus.NO_FACTUAL_CLAIMS, reasons, codes
    if finalized.status is SpecialistStatus.UNAVAILABLE:
        reasons.add(MasterCorroborationReason.HISTORICAL_UNAVAILABLE_CORROBORATED)
        return MasterCorroborationStatus.NO_FACTUAL_CLAIMS, reasons, codes
    if finalized.status not in _FACTUAL_HISTORICAL_STATUSES:
        reasons.add(MasterCorroborationReason.HISTORICAL_NON_FACTUAL_FINAL_STATUS)
        return MasterCorroborationStatus.NO_FACTUAL_CLAIMS, reasons, codes
    reasons.add(MasterCorroborationReason.SUPPLIED_EVIDENCE_ONLY)
    if finalized.status is SpecialistStatus.PARTIAL:
        reasons.add(MasterCorroborationReason.HISTORICAL_PARTIAL_EVIDENCE_NOT_COMPLETE)
    return MasterCorroborationStatus.CONSISTENT_WITH_SUPPLIED_EVIDENCE, reasons, ()


def _historical_mismatches(
    expected: SpecialistResult,
    supplied: SpecialistResult,
) -> set[MasterCorroborationReason]:
    reasons: set[MasterCorroborationReason] = set()
    if expected.answer != supplied.answer:
        reasons.add(MasterCorroborationReason.ANSWER_MISMATCH)
    if expected.verified_claims != supplied.verified_claims:
        reasons.add(MasterCorroborationReason.CLAIM_MISMATCH)
    if expected.tool_results != supplied.tool_results:
        reasons.add(MasterCorroborationReason.TOOL_RESULT_MISMATCH)
    if expected.evaluated_as_of_utc != supplied.evaluated_as_of_utc:
        reasons.add(MasterCorroborationReason.EVALUATION_TIME_MISMATCH)
    if expected.status != supplied.status:
        reasons.add(MasterCorroborationReason.STATUS_MISMATCH)
    if expected.limitations != supplied.limitations:
        reasons.add(MasterCorroborationReason.LIMITATION_MISMATCH)
    if expected.verifier_outcome != supplied.verifier_outcome:
        reasons.add(MasterCorroborationReason.VERIFIER_OUTCOME_MISMATCH)
    if expected.temporal_scope != supplied.temporal_scope:
        reasons.add(MasterCorroborationReason.TEMPORAL_SCOPE_MISMATCH)
    if expected.unsupported_reason != supplied.unsupported_reason:
        reasons.add(MasterCorroborationReason.UNSUPPORTED_REASON_MISMATCH)
    return reasons


def _decision(
    artifact: DecisionSpecialistRunResult,
) -> tuple[MasterCorroborationStatus, set[MasterCorroborationReason], tuple[str, ...]]:
    reasons: set[MasterCorroborationReason] = set()
    if artifact.status not in _FINAL_DECISION:
        reasons.add(MasterCorroborationReason.DECISION_NON_FINAL)
        return MasterCorroborationStatus.NO_FACTUAL_CLAIMS, reasons, ()
    computed = verify_decision_evidence(artifact.proposed_claims, artifact.evidence_bindings)
    rendered = render_decision_verification(computed)
    if computed != artifact.verification:
        reasons.add(MasterCorroborationReason.DECISION_VERIFICATION_MISMATCH)
        return MasterCorroborationStatus.INCONSISTENT, reasons, computed.rejected_claim_codes
    if rendered != artifact.render:
        reasons.add(MasterCorroborationReason.DECISION_RENDER_MISMATCH)
        return MasterCorroborationStatus.INCONSISTENT, reasons, computed.rejected_claim_codes
    if artifact.status is DecisionSpecialistRunStatus.PARTIAL:
        reasons.add(MasterCorroborationReason.DECISION_COLLECTION_PARTIAL)
    if any(isinstance(item, RiskAbsentClaim) for item in computed.verified_claims):
        reasons.add(MasterCorroborationReason.RECORDED_RISK_ABSENCE_NOT_LOW)
    if computed.outcome is not VerifierOutcome.PASSED:
        reasons.add(MasterCorroborationReason.DECISION_VERIFICATION_NOT_FACTUAL)
        return MasterCorroborationStatus.NO_FACTUAL_CLAIMS, reasons, computed.rejected_claim_codes
    reasons.add(MasterCorroborationReason.SUPPLIED_EVIDENCE_ONLY)
    return MasterCorroborationStatus.CONSISTENT_WITH_SUPPLIED_EVIDENCE, reasons, ()


def _live_ops(
    artifact: Any,
) -> tuple[MasterCorroborationStatus, set[MasterCorroborationReason], tuple[str, ...]]:
    reasons = {
        MasterCorroborationReason.LIVE_OPS_RAW_EVIDENCE_UNAVAILABLE,
        MasterCorroborationReason.LIVE_OPS_NOT_RECHECKABLE,
    }
    outcome = artifact.result.verification_outcome
    if outcome is LiveOpsPublicVerificationOutcome.NONE_VERIFIED or outcome is None:
        reasons.add(MasterCorroborationReason.LIVE_OPS_NONE_VERIFIED_NO_FACTS)
        return MasterCorroborationStatus.NO_FACTUAL_CLAIMS, reasons, ()
    return MasterCorroborationStatus.NOT_RECHECKABLE, reasons, ()


def _finish(
    admission: MasterEvidenceAdmission,
    status: MasterCorroborationStatus,
    reasons: set[MasterCorroborationReason],
    rejected_claim_codes: tuple[str, ...],
) -> MasterEvidenceCorroboration:
    return MasterEvidenceCorroboration(
        master_request_id=admission.master_request_id,
        invocation_id=admission.invocation_id,
        specialist=admission.specialist,
        corroboration_status=status,
        admission_status=admission.admission_status,
        reason_codes=tuple(sorted(reasons, key=lambda item: item.value)),
        admission_reason_codes=admission.reason_codes,
        binding_defects=admission.binding_defects,
        temporal_limitations=admission.temporal_limitations,
        rejected_claim_codes=rejected_claim_codes,
    )


def _identity(value: object, field_name: str) -> None:
    errors = _validate_bounded_text(value, field_name, max_length=PROVENANCE_TOKEN_MAX_LENGTH)
    if errors:
        raise ContractValidationError(errors)
    if not isinstance(value, str) or value != value.strip():
        raise ContractValidationError(f"invalid_{field_name}")


def _enum_tuple(value: tuple[Any, ...], enum_type: type[Enum], field_name: str) -> None:
    if not isinstance(value, tuple) or any(not isinstance(item, enum_type) for item in value):
        raise ContractValidationError(f"invalid_{field_name}")


def _strings(value: tuple[Any, ...], field_name: str) -> None:
    if not isinstance(value, tuple) or any(
        not isinstance(item, str) or not item.strip() or item != item.strip() for item in value
    ):
        raise ContractValidationError(f"invalid_{field_name}")
