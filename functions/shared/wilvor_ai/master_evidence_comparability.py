"""Deterministic Master cross-specialist comparability assessment.

This layer asks whether two specialist artifacts have the prerequisites
required to compare factual claims. A shared aircraft id, hazard id,
timestamp, or metric name is not operational equivalence. The assessment
does not authenticate sources, authorize a Decision tool, or produce an
aviation conclusion.

Both invocations are admitted together by one ``admit_master_evidence``
call. That collection keeps the single Master request, distinct invocation
ids, and the single-Decision step limit. A caller-built admission object
is not an input and is not a certificate. Current and persisted Decision
scopes, and recorded absence versus LOW, belong to one Decision artifact.
They are not a second Decision invocation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
    _validate_bounded_text,
)
from wilvor_ai.decision_claims import (
    AircraftIdentityClaim,
    EvaluationStateClaim,
    RecommendationActionClaim,
    RiskAbsentClaim,
    RiskPresentClaim,
)
from wilvor_ai.decision_contracts import DecisionEvaluationState, StoredRiskLevel
from wilvor_ai.decision_specialist_runtime_contracts import DecisionSpecialistRunResult
from wilvor_ai.live_ops_public_contracts import (
    LiveOpsPublicClaimKind,
    LiveOpsPublicCountMetric,
    LiveOpsPublicCountMode,
    LiveOpsPublicObservedField,
    LiveOpsPublicRecordedFlag,
    LiveOpsPublicRiskMode,
    LiveOpsPublicVerificationOutcome,
    LiveOpsRunResult,
)
from wilvor_ai.master_contracts import (
    MasterBindingDefect,
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import (
    LiveOpsAdmissionSources,
    MasterAdmissionReason,
    MasterAdmissionStatus,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
    admit_master_evidence,
)
from wilvor_ai.master_routing import MasterRequestCoverage
from wilvor_ai.specialist_contracts import (
    ExactCountClaim,
    HistoricalWindowClaim,
    LowerBoundCountClaim,
    RecordIdentityClaim,
    SpecialistResult,
    VerifiedZeroClaim,
)


MASTER_EVIDENCE_COMPARABILITY_SCHEMA_VERSION = "wilvor.ai.master_evidence_comparability.v1"

_LIVE = MasterSpecialist.LIVE_OPS
_DECISION = MasterSpecialist.DECISION
_HISTORICAL = MasterSpecialist.HISTORICAL_ANALYTICS


class MasterComparisonCategory(str, Enum):
    """Closed comparison subjects. The value is the category id, not a verdict."""

    LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT = "live-ops-aircraft-vs-decision-aircraft"
    LIVE_OPS_RISK_VS_DECISION_RISK = "live-ops-risk-vs-decision-risk"
    LIVE_OPS_RECOMMENDATIONS_VS_DECISION_RECOMMENDATIONS = (
        "live-ops-recommendations-vs-decision-recommendations"
    )
    LIVE_OPS_HAZARD_EXPOSURE_VS_HISTORICAL_COUNT = (
        "live-ops-hazard-exposure-vs-historical-count"
    )
    HISTORICAL_COUNT_VS_CURRENT_POPULATION = "historical-count-vs-current-population"
    HISTORICAL_VERIFIED_ZERO_VS_LIVE_OPS_UNKNOWN = (
        "historical-verified-zero-vs-live-ops-unknown"
    )
    CURRENT_VS_PERSISTED_EVIDENCE = "current-vs-persisted-evidence"
    SAME_AIRCRAFT_DIFFERENT_OBSERVATION_TIMES = "same-aircraft-different-observation-times"
    SAME_HAZARD_DIFFERENT_SOURCE_VERSIONS = "same-hazard-different-source-versions"
    CALLSIGN_DISCOVERY_VS_DECISION_TARGET = "callsign-discovery-vs-decision-target"
    CALIFORNIA_SUBSET_VS_NETWORK_COUNT = "california-subset-vs-network-count"
    CURRENT_SIGMET_VS_HISTORICAL_HAZARD_VERSIONS = (
        "current-sigmet-vs-historical-materialization"
    )
    MISSING_GEOMETRY_VS_CLAIMED_INTERSECTION = "missing-geometry-vs-claimed-intersection"
    HISTORICAL_EXACT_VS_LIVE_OPS_SUBSET = "historical-exact-vs-live-ops-subset"
    EXACT_COUNT_VS_LOWER_BOUND = "exact-count-vs-lower-bound"
    LIVE_OPS_RISK_ABSENCE_VS_DECISION_RISK_ABSENCE = (
        "live-ops-risk-absence-vs-decision-risk-absence"
    )
    DECISION_LOW_VS_MISSING_RISK = "decision-low-vs-missing-risk"
    RECOMMENDATION_RECORD_VS_PILOT_INSTRUCTION = "recommendation-record-vs-pilot-instruction"
    QUERY_TIME_VS_OBSERVATION_TIME = "query-time-vs-observation-time"
    PARTIAL_SET_VS_NETWORK_CONCLUSION = "partial-set-vs-network-conclusion"
    HISTORICAL_RECORD_VS_CURRENT_AIRCRAFT = "historical-record-identity-vs-current-aircraft"
    AIRCRAFT_POSITION_VS_BOUNDARY_CROSSING = "moving-aircraft-at-geographic-boundary"
    SAME_COUNT_NAME_DIFFERENT_DEFINITIONS = "same-count-name-different-definitions"
    CALIFORNIA_USER_REGION_TEXT = "california-user-region-text"
    CENSUS_GEOMETRY_VS_OPERATIONAL_AIRSPACE = "census-geometry-vs-operational-airspace"
    UNKNOWN_POLYGON_PROVENANCE = "unknown-polygon-provenance"
    MISSING_REGION_DATASET_VERSION = "missing-region-dataset-version"


class MasterComparisonEligibility(str, Enum):
    """Closed comparability eligibility. None of these values is an operational verdict."""

    BLOCKED = "BLOCKED"
    UNSUPPORTED = "UNSUPPORTED"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    CONTRACT_CHECKABLE_ONLY = "CONTRACT_CHECKABLE_ONLY"


class MasterComparisonReason(str, Enum):
    """Why comparability is limited. These codes are not factual disagreements."""

    ADMISSION_REJECTED = "ADMISSION_REJECTED"
    ADMISSION_BINDING_UNRESOLVED = "ADMISSION_BINDING_UNRESOLVED"
    INVOCATION_NOT_BOUND = "INVOCATION_NOT_BOUND"
    SPECIALIST_PAIR_NOT_APPLICABLE = "SPECIALIST_PAIR_NOT_APPLICABLE"
    SYNTHETIC_ARTIFACT_NOT_COMPARABLE_EVIDENCE = "SYNTHETIC_ARTIFACT_NOT_COMPARABLE_EVIDENCE"
    SOURCE_AUTHENTICITY_NOT_ESTABLISHED = "SOURCE_AUTHENTICITY_NOT_ESTABLISHED"
    CONTRACT_ONLY_IS_NOT_SOURCE_AUTHENTICATION = "CONTRACT_ONLY_IS_NOT_SOURCE_AUTHENTICATION"
    SELECTOR_HINT_NOT_EVIDENCE = "SELECTOR_HINT_NOT_EVIDENCE"
    DECISION_EXECUTION_NOT_AUTHORIZED = "DECISION_EXECUTION_NOT_AUTHORIZED"
    CROSS_INVOCATION_DECISION_COMPARISON_NOT_ADMITTED = (
        "CROSS_INVOCATION_DECISION_COMPARISON_NOT_ADMITTED"
    )
    CURRENT_AND_PERSISTED_SCOPES_BELONG_TO_ONE_ARTIFACT = (
        "CURRENT_AND_PERSISTED_SCOPES_BELONG_TO_ONE_ARTIFACT"
    )
    ABSENCE_AND_LOW_BELONG_TO_ONE_ARTIFACT = "ABSENCE_AND_LOW_BELONG_TO_ONE_ARTIFACT"
    EXPECTED_AIRCRAFT_IS_NOT_AUTHENTICATED_IDENTITY = (
        "EXPECTED_AIRCRAFT_IS_NOT_AUTHENTICATED_IDENTITY"
    )
    TYPED_AIRCRAFT_IDENTITY_ABSENT = "TYPED_AIRCRAFT_IDENTITY_ABSENT"
    SHARED_AIRCRAFT_ID_STRING_NOT_AUTHENTICATED_JOIN = (
        "SHARED_AIRCRAFT_ID_STRING_NOT_AUTHENTICATED_JOIN"
    )
    AIRCRAFT_ID_STRINGS_ARE_DIFFERENT = "AIRCRAFT_ID_STRINGS_ARE_DIFFERENT"
    CALLSIGN_DOES_NOT_AUTHORIZE_DECISION_TARGET = "CALLSIGN_DOES_NOT_AUTHORIZE_DECISION_TARGET"
    CALLSIGN_DISCOVERY_ABSENT = "CALLSIGN_DISCOVERY_ABSENT"
    HISTORICAL_RECORD_ID_NOT_AIRCRAFT_ID = "HISTORICAL_RECORD_ID_NOT_AIRCRAFT_ID"
    SHARED_RECORD_ID_STRING_NOT_AN_AIRCRAFT_JOIN = "SHARED_RECORD_ID_STRING_NOT_AN_AIRCRAFT_JOIN"
    HAZARD_SOURCE_VERSION_ABSENT = "HAZARD_SOURCE_VERSION_ABSENT"
    SHARED_SOURCE_VERSION_STRING_NOT_AUTHENTICATED = (
        "SHARED_SOURCE_VERSION_STRING_NOT_AUTHENTICATED"
    )
    SOURCE_VERSION_STRINGS_ARE_DIFFERENT = "SOURCE_VERSION_STRINGS_ARE_DIFFERENT"
    OBSERVATION_TIME_ABSENT = "OBSERVATION_TIME_ABSENT"
    SOURCE_EVENT_TIME_ABSENT = "SOURCE_EVENT_TIME_ABSENT"
    IDENTICAL_UTC_STRING_NOT_ATOMIC_OBSERVATION = "IDENTICAL_UTC_STRING_NOT_ATOMIC_OBSERVATION"
    SHARED_REFERENCE_UTC_IS_NOT_ATOMIC_OBSERVATION = (
        "SHARED_REFERENCE_UTC_IS_NOT_ATOMIC_OBSERVATION"
    )
    SHARED_POSITION_TIME_STRING_NOT_ATOMIC = "SHARED_POSITION_TIME_STRING_NOT_ATOMIC"
    OBSERVATION_TIME_STRINGS_ARE_DIFFERENT = "OBSERVATION_TIME_STRINGS_ARE_DIFFERENT"
    LIVE_OPS_REFERENCE_TIME_IS_NOT_OBSERVATION_TIME = (
        "LIVE_OPS_REFERENCE_TIME_IS_NOT_OBSERVATION_TIME"
    )
    QUERY_TIME_IS_NOT_SOURCE_EVENT_TIME = "QUERY_TIME_IS_NOT_SOURCE_EVENT_TIME"
    HISTORICAL_WINDOW_IS_NOT_CURRENT_OBSERVATION = "HISTORICAL_WINDOW_IS_NOT_CURRENT_OBSERVATION"
    TEMPORAL_SCOPES_ARE_DIFFERENT = "TEMPORAL_SCOPES_ARE_DIFFERENT"
    CURRENT_SCOPE_IS_NOT_PERSISTED_SCOPE = "CURRENT_SCOPE_IS_NOT_PERSISTED_SCOPE"
    PERSISTED_EVIDENCE_IS_NOT_CURRENT = "PERSISTED_EVIDENCE_IS_NOT_CURRENT"
    PERSISTED_SCOPE_ABSENT = "PERSISTED_SCOPE_ABSENT"
    LINK_CLAIM_DOES_NOT_MAKE_PERSISTED_EVIDENCE_CURRENT = (
        "LINK_CLAIM_DOES_NOT_MAKE_PERSISTED_EVIDENCE_CURRENT"
    )
    METRIC_DEFINITIONS_NOT_EQUIVALENT = "METRIC_DEFINITIONS_NOT_EQUIVALENT"
    EXACT_COUNT_IS_NOT_LOWER_BOUND = "EXACT_COUNT_IS_NOT_LOWER_BOUND"
    LOWER_BOUND_CLAIM_ABSENT = "LOWER_BOUND_CLAIM_ABSENT"
    EXACT_COUNT_CLAIM_ABSENT = "EXACT_COUNT_CLAIM_ABSENT"
    MATCHING_COUNT_VALUE_IS_NOT_METRIC_EQUIVALENCE = (
        "MATCHING_COUNT_VALUE_IS_NOT_METRIC_EQUIVALENCE"
    )
    EXACT_COUNT_IS_NOT_SUBSET_COUNT = "EXACT_COUNT_IS_NOT_SUBSET_COUNT"
    HISTORICAL_COUNT_IS_NOT_CURRENT_POPULATION = "HISTORICAL_COUNT_IS_NOT_CURRENT_POPULATION"
    HISTORICAL_COUNT_IS_NOT_RISK_SEVERITY = "HISTORICAL_COUNT_IS_NOT_RISK_SEVERITY"
    VERIFIED_ZERO_IS_NOT_UNKNOWN = "VERIFIED_ZERO_IS_NOT_UNKNOWN"
    VERIFIED_ZERO_IS_NOT_A_CURRENT_OUTCOME = "VERIFIED_ZERO_IS_NOT_A_CURRENT_OUTCOME"
    NETWORK_SCOPE_IS_NOT_REGIONAL_SUBSET = "NETWORK_SCOPE_IS_NOT_REGIONAL_SUBSET"
    COVERAGE_COMPLETENESS_ABSENT = "COVERAGE_COMPLETENESS_ABSENT"
    PARTIAL_EVIDENCE_IS_NOT_NETWORK_COMPLETE = "PARTIAL_EVIDENCE_IS_NOT_NETWORK_COMPLETE"
    ALL_VERIFIED_IS_NOT_NETWORK_COMPLETE = "ALL_VERIFIED_IS_NOT_NETWORK_COMPLETE"
    REGION_TEXT_UNTRUSTED = "REGION_TEXT_UNTRUSTED"
    REGION_TEXT_ABSENT = "REGION_TEXT_ABSENT"
    CALIFORNIA_TEXT_IS_NOT_AN_OPERATIONAL_BOUNDARY = (
        "CALIFORNIA_TEXT_IS_NOT_AN_OPERATIONAL_BOUNDARY"
    )
    CENSUS_GEOMETRY_IS_NOT_FAA_AIRSPACE = "CENSUS_GEOMETRY_IS_NOT_FAA_AIRSPACE"
    FIR_ARTCC_BOUNDARY_NOT_PRESENT = "FIR_ARTCC_BOUNDARY_NOT_PRESENT"
    OPERATIONAL_AIRSPACE_SCOPE_ABSENT = "OPERATIONAL_AIRSPACE_SCOPE_ABSENT"
    SIGMET_GEOMETRY_NOT_PRESENT = "SIGMET_GEOMETRY_NOT_PRESENT"
    GEOGRAPHIC_GEOMETRY_ABSENT = "GEOGRAPHIC_GEOMETRY_ABSENT"
    INTERSECTION_FLAG_IS_NOT_GEOMETRY_PROOF = "INTERSECTION_FLAG_IS_NOT_GEOMETRY_PROOF"
    POLYGON_PROVENANCE_ABSENT = "POLYGON_PROVENANCE_ABSENT"
    REGION_DATASET_VERSION_ABSENT = "REGION_DATASET_VERSION_ABSENT"
    AIRCRAFT_POSITION_ABSENT = "AIRCRAFT_POSITION_ABSENT"
    AIRCRAFT_POSITION_IS_NOT_BOUNDARY_CROSSING = "AIRCRAFT_POSITION_IS_NOT_BOUNDARY_CROSSING"
    CURRENT_EXPOSURE_IS_NOT_HISTORICAL_COUNT = "CURRENT_EXPOSURE_IS_NOT_HISTORICAL_COUNT"
    SIGMET_EXPOSURE_IS_NOT_RISK_CLASSIFICATION = "SIGMET_EXPOSURE_IS_NOT_RISK_CLASSIFICATION"
    RISK_TOKEN_IS_NOT_STORED_RISK_LEVEL = "RISK_TOKEN_IS_NOT_STORED_RISK_LEVEL"
    MATCHING_RISK_TOKEN_IS_NOT_STORED_LEVEL = "MATCHING_RISK_TOKEN_IS_NOT_STORED_LEVEL"
    RECORDED_ABSENCE_IS_NOT_LOW = "RECORDED_ABSENCE_IS_NOT_LOW"
    MISSING_RISK_EVALUATION_IS_NOT_NO_RISK = "MISSING_RISK_EVALUATION_IS_NOT_NO_RISK"
    LOW_RISK_IS_NOT_A_MISSING_EVALUATION = "LOW_RISK_IS_NOT_A_MISSING_EVALUATION"
    LOW_LEVEL_DOES_NOT_EXPLAIN_ABSENCE = "LOW_LEVEL_DOES_NOT_EXPLAIN_ABSENCE"
    PERSISTED_RISK_IS_NOT_CURRENT_RISK = "PERSISTED_RISK_IS_NOT_CURRENT_RISK"
    STORED_RISK_IS_NOT_CURRENT_WITHOUT_PROOF = "STORED_RISK_IS_NOT_CURRENT_WITHOUT_PROOF"
    RISK_SEMANTIC_EQUIVALENCE_NOT_ESTABLISHED = "RISK_SEMANTIC_EQUIVALENCE_NOT_ESTABLISHED"
    RECOMMENDATION_SET_EQUIVALENCE_NOT_ESTABLISHED = (
        "RECOMMENDATION_SET_EQUIVALENCE_NOT_ESTABLISHED"
    )
    ADVISORY_OUTPUT_IS_NOT_PILOT_INSTRUCTION = "ADVISORY_OUTPUT_IS_NOT_PILOT_INSTRUCTION"
    ADVISORY_OUTPUT_IS_NOT_ATC_INSTRUCTION = "ADVISORY_OUTPUT_IS_NOT_ATC_INSTRUCTION"
    ADVISORY_ONLY_CLAIM_OBSERVED = "ADVISORY_ONLY_CLAIM_OBSERVED"
    NON_SPECIALIST_DOMAIN_NOT_IMPLEMENTED = "NON_SPECIALIST_DOMAIN_NOT_IMPLEMENTED"


class MasterComparisonPrerequisite(str, Enum):
    """Prerequisite that remains missing unless a native contract proves it."""

    TYPED_AIRCRAFT_IDENTITY = "TYPED_AIRCRAFT_IDENTITY"
    TRUSTED_IDENTITY_BINDING = "TRUSTED_IDENTITY_BINDING"
    OBSERVATION_TIME_SEMANTICS = "OBSERVATION_TIME_SEMANTICS"
    EVALUATION_TIME_EQUIVALENCE = "EVALUATION_TIME_EQUIVALENCE"
    SOURCE_EVENT_TIME = "SOURCE_EVENT_TIME"
    HAZARD_SOURCE_VERSION = "HAZARD_SOURCE_VERSION"
    REGION_IDENTITY = "REGION_IDENTITY"
    GEOGRAPHIC_GEOMETRY = "GEOGRAPHIC_GEOMETRY"
    OPERATIONAL_AIRSPACE_SCOPE = "OPERATIONAL_AIRSPACE_SCOPE"
    METRIC_DEFINITIONS = "METRIC_DEFINITIONS"
    POPULATION_COVERAGE = "POPULATION_COVERAGE"
    EVIDENCE_COMPLETENESS = "EVIDENCE_COMPLETENESS"
    SOURCE_AUTHENTICITY = "SOURCE_AUTHENTICITY"
    CURRENT_VERSUS_HISTORICAL_SCOPE = "CURRENT_VERSUS_HISTORICAL_SCOPE"
    CURRENT_VERSUS_PERSISTED_EVIDENCE = "CURRENT_VERSUS_PERSISTED_EVIDENCE"
    DECISION_TARGET_AUTHORIZATION = "DECISION_TARGET_AUTHORIZATION"
    RISK_SEMANTIC_EQUIVALENCE = "RISK_SEMANTIC_EQUIVALENCE"
    ADVISORY_VERSUS_AUTHORITATIVE_ACTION = "ADVISORY_VERSUS_AUTHORITATIVE_ACTION"


_EXACT_PAIRS: dict[MasterComparisonCategory, frozenset[MasterSpecialist]] = {
    MasterComparisonCategory.LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT: frozenset({_LIVE, _DECISION}),
    MasterComparisonCategory.LIVE_OPS_RISK_VS_DECISION_RISK: frozenset({_LIVE, _DECISION}),
    MasterComparisonCategory.LIVE_OPS_RECOMMENDATIONS_VS_DECISION_RECOMMENDATIONS: frozenset(
        {_LIVE, _DECISION}
    ),
    MasterComparisonCategory.LIVE_OPS_HAZARD_EXPOSURE_VS_HISTORICAL_COUNT: frozenset(
        {_LIVE, _HISTORICAL}
    ),
    MasterComparisonCategory.HISTORICAL_COUNT_VS_CURRENT_POPULATION: frozenset({_HISTORICAL, _LIVE}),
    MasterComparisonCategory.HISTORICAL_VERIFIED_ZERO_VS_LIVE_OPS_UNKNOWN: frozenset(
        {_HISTORICAL, _LIVE}
    ),
    MasterComparisonCategory.SAME_AIRCRAFT_DIFFERENT_OBSERVATION_TIMES: frozenset({_LIVE}),
    MasterComparisonCategory.SAME_HAZARD_DIFFERENT_SOURCE_VERSIONS: frozenset({_LIVE}),
    MasterComparisonCategory.CALLSIGN_DISCOVERY_VS_DECISION_TARGET: frozenset({_LIVE, _DECISION}),
    MasterComparisonCategory.CALIFORNIA_SUBSET_VS_NETWORK_COUNT: frozenset({_LIVE}),
    MasterComparisonCategory.CURRENT_SIGMET_VS_HISTORICAL_HAZARD_VERSIONS: frozenset(
        {_LIVE, _HISTORICAL}
    ),
    MasterComparisonCategory.HISTORICAL_EXACT_VS_LIVE_OPS_SUBSET: frozenset({_HISTORICAL, _LIVE}),
    MasterComparisonCategory.EXACT_COUNT_VS_LOWER_BOUND: frozenset({_HISTORICAL}),
    MasterComparisonCategory.LIVE_OPS_RISK_ABSENCE_VS_DECISION_RISK_ABSENCE: frozenset(
        {_LIVE, _DECISION}
    ),
    MasterComparisonCategory.RECOMMENDATION_RECORD_VS_PILOT_INSTRUCTION: frozenset(
        {_DECISION, _LIVE}
    ),
    MasterComparisonCategory.QUERY_TIME_VS_OBSERVATION_TIME: frozenset({_LIVE, _HISTORICAL}),
    MasterComparisonCategory.PARTIAL_SET_VS_NETWORK_CONCLUSION: frozenset({_LIVE}),
    MasterComparisonCategory.HISTORICAL_RECORD_VS_CURRENT_AIRCRAFT: frozenset({_HISTORICAL, _LIVE}),
    MasterComparisonCategory.SAME_COUNT_NAME_DIFFERENT_DEFINITIONS: frozenset({_HISTORICAL, _LIVE}),
}

_INTRA_ARTIFACT_DECISION = {
    MasterComparisonCategory.CURRENT_VS_PERSISTED_EVIDENCE,
    MasterComparisonCategory.DECISION_LOW_VS_MISSING_RISK,
}

_UNSUPPORTED_BASE = {
    MasterComparisonCategory.CURRENT_VS_PERSISTED_EVIDENCE,
    MasterComparisonCategory.DECISION_LOW_VS_MISSING_RISK,
    MasterComparisonCategory.LIVE_OPS_HAZARD_EXPOSURE_VS_HISTORICAL_COUNT,
    MasterComparisonCategory.CALLSIGN_DISCOVERY_VS_DECISION_TARGET,
    MasterComparisonCategory.CURRENT_SIGMET_VS_HISTORICAL_HAZARD_VERSIONS,
    MasterComparisonCategory.MISSING_GEOMETRY_VS_CLAIMED_INTERSECTION,
    MasterComparisonCategory.RECOMMENDATION_RECORD_VS_PILOT_INSTRUCTION,
    MasterComparisonCategory.AIRCRAFT_POSITION_VS_BOUNDARY_CROSSING,
    MasterComparisonCategory.CENSUS_GEOMETRY_VS_OPERATIONAL_AIRSPACE,
}


def _identity(value: object, field_name: str) -> None:
    errors = _validate_bounded_text(value, field_name, max_length=PROVENANCE_TOKEN_MAX_LENGTH)
    if errors:
        raise ContractValidationError(errors)
    if not isinstance(value, str) or value != value.strip() or not value:
        raise ContractValidationError(f"invalid_{field_name}")


def _optional_hint(value: object, field_name: str) -> None:
    if value is None:
        return
    _identity(value, field_name)


def _ordered(enum_type: type[Enum], selected: set[Any]) -> tuple[Any, ...]:
    return tuple(item for item in enum_type if item in selected)


def _enum_tuple(value: object, enum_type: type[Enum], field_name: str) -> None:
    if not isinstance(value, tuple) or any(not isinstance(item, enum_type) for item in value):
        raise ContractValidationError(f"invalid_{field_name}")


def _pair_matches(category: MasterComparisonCategory, left: MasterSpecialist, right: MasterSpecialist) -> bool:
    required = _EXACT_PAIRS.get(category)
    if required is None:
        return True
    return frozenset({left, right}) == required


@dataclass(frozen=True)
class MasterComparisonRequest:
    """One bounded comparison. Selectors are untrusted hints, not evidence."""

    master_request_id: str
    left_invocation_id: str
    right_invocation_id: str
    category: MasterComparisonCategory
    aircraft_id_hint: str | None = None
    hazard_id_hint: str | None = None
    region_text_hint: str | None = None
    metric_name_hint: str | None = None

    def __post_init__(self) -> None:
        _identity(self.master_request_id, "master_request_id")
        _identity(self.left_invocation_id, "left_invocation_id")
        _identity(self.right_invocation_id, "right_invocation_id")
        if not isinstance(self.category, MasterComparisonCategory):
            raise ContractValidationError("invalid_comparison_category")
        _optional_hint(self.aircraft_id_hint, "aircraft_id_hint")
        _optional_hint(self.hazard_id_hint, "hazard_id_hint")
        _optional_hint(self.region_text_hint, "region_text_hint")
        _optional_hint(self.metric_name_hint, "metric_name_hint")


@dataclass(frozen=True)
class MasterComparisonAssessment:
    """Comparability limit for one request. It contains no factual answer."""

    master_request_id: str
    left_invocation_id: str
    right_invocation_id: str
    category: MasterComparisonCategory
    eligibility: MasterComparisonEligibility
    reason_codes: tuple[MasterComparisonReason, ...]
    missing_prerequisites: tuple[MasterComparisonPrerequisite, ...]
    left_admission_status: MasterAdmissionStatus
    right_admission_status: MasterAdmissionStatus
    left_admission_reasons: tuple[MasterAdmissionReason, ...]
    right_admission_reasons: tuple[MasterAdmissionReason, ...]
    left_binding_defects: tuple[MasterBindingDefect, ...]
    right_binding_defects: tuple[MasterBindingDefect, ...]
    established_prerequisites: tuple[MasterComparisonPrerequisite, ...] = ()
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    evidence_verified: bool = False
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    end_to_end_fulfilled: bool = False
    schema_version: str = MASTER_EVIDENCE_COMPARABILITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_EVIDENCE_COMPARABILITY_SCHEMA_VERSION:
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
        if not isinstance(self.eligibility, MasterComparisonEligibility):
            raise ContractValidationError("invalid_comparison_eligibility")
        if not isinstance(self.category, MasterComparisonCategory):
            raise ContractValidationError("invalid_comparison_category")
        if not isinstance(self.left_admission_status, MasterAdmissionStatus):
            raise ContractValidationError("invalid_left_admission_status")
        if not isinstance(self.right_admission_status, MasterAdmissionStatus):
            raise ContractValidationError("invalid_right_admission_status")
        _enum_tuple(self.reason_codes, MasterComparisonReason, "reason_codes")
        _enum_tuple(self.missing_prerequisites, MasterComparisonPrerequisite, "missing_prerequisites")
        _enum_tuple(self.established_prerequisites, MasterComparisonPrerequisite, "established_prerequisites")
        _enum_tuple(self.left_admission_reasons, MasterAdmissionReason, "left_admission_reasons")
        _enum_tuple(self.right_admission_reasons, MasterAdmissionReason, "right_admission_reasons")
        _enum_tuple(self.left_binding_defects, MasterBindingDefect, "left_binding_defects")
        _enum_tuple(self.right_binding_defects, MasterBindingDefect, "right_binding_defects")
        if self.established_prerequisites != ():
            raise ContractValidationError("prerequisite_not_established")
        if MasterComparisonReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED not in self.reason_codes:
            raise ContractValidationError("source_authenticity_not_established")
        if MasterComparisonPrerequisite.SOURCE_AUTHENTICITY not in self.missing_prerequisites:
            raise ContractValidationError("source_authenticity_not_established")
        _identity(self.master_request_id, "master_request_id")
        _identity(self.left_invocation_id, "left_invocation_id")
        _identity(self.right_invocation_id, "right_invocation_id")
        if self.left_invocation_id == self.right_invocation_id:
            raise ContractValidationError("duplicate_invocation_id")
        rejected = (
            self.left_admission_status is MasterAdmissionStatus.REJECTED
            or self.right_admission_status is MasterAdmissionStatus.REJECTED
        )
        if rejected and self.eligibility is not MasterComparisonEligibility.BLOCKED:
            raise ContractValidationError("rejected_admission_requires_blocked")
        unresolved = (
            self.left_admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
            or self.right_admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
        )
        if unresolved and (
            self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED
            or self.evidence_verified is not False
            or self.production_authority is not MasterProductionAuthority.DENIED
            or self.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED
            or self.end_to_end_fulfilled is not False
        ):
            raise ContractValidationError("unresolved_admission_not_authenticated")


def assess_master_evidence_comparability(
    request: MasterComparisonRequest,
    left: MasterEvidenceAdmissionInput,
    right: MasterEvidenceAdmissionInput,
) -> MasterComparisonAssessment:
    """Assess comparability of two invocations. Does not authenticate either one.

    ``left`` and ``right`` are admitted as one collection, in that order.
    Two Decision invocations raise ``decision_fanout_not_permitted`` from
    admission. That rejection is not converted into a comparison result.
    """

    if not isinstance(request, MasterComparisonRequest):
        raise ContractValidationError("invalid_comparison_request")
    if not isinstance(left, MasterEvidenceAdmissionInput) or not isinstance(
        right, MasterEvidenceAdmissionInput
    ):
        raise ContractValidationError("invalid_admission_inputs")
    request_ids = {
        request.master_request_id,
        left.authorization.master_request_id,
        right.authorization.master_request_id,
    }
    if len(request_ids) != 1:
        raise ContractValidationError("master_request_mismatch")
    if (
        left.authorization.invocation_id == right.authorization.invocation_id
        or request.left_invocation_id == request.right_invocation_id
    ):
        raise ContractValidationError("duplicate_invocation_id")

    admissions = admit_master_evidence((left, right)).admissions
    left_admission, right_admission = admissions
    if (
        left_admission.invocation_id != left.authorization.invocation_id
        or right_admission.invocation_id != right.authorization.invocation_id
    ):
        raise ContractValidationError("invocation_order_mismatch")
    reasons: set[MasterComparisonReason] = {
        MasterComparisonReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED,
        MasterComparisonReason.COVERAGE_COMPLETENESS_ABSENT,
    }
    missing: set[MasterComparisonPrerequisite] = {
        MasterComparisonPrerequisite.SOURCE_AUTHENTICITY,
        MasterComparisonPrerequisite.EVIDENCE_COMPLETENESS,
    }
    if left_admission.admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED or (
        right_admission.admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
    ):
        reasons.add(MasterComparisonReason.ADMISSION_BINDING_UNRESOLVED)
    if (
        left_admission.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
        and right_admission.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    ):
        reasons.add(MasterComparisonReason.CONTRACT_ONLY_IS_NOT_SOURCE_AUTHENTICATION)
    if MasterAdmissionReason.UNSUPPORTED_ARTIFACT in left_admission.reason_codes or (
        MasterAdmissionReason.UNSUPPORTED_ARTIFACT in right_admission.reason_codes
    ):
        reasons.add(MasterComparisonReason.SYNTHETIC_ARTIFACT_NOT_COMPARABLE_EVIDENCE)
    _note_hints(request, reasons)
    _note_decision_limits(left.authorization, right.authorization, reasons, missing)

    bound = (
        request.left_invocation_id == left.authorization.invocation_id
        and request.right_invocation_id == right.authorization.invocation_id
    )
    rejected = (
        left_admission.admission_status is MasterAdmissionStatus.REJECTED
        or right_admission.admission_status is MasterAdmissionStatus.REJECTED
    )
    if not bound:
        eligibility = MasterComparisonEligibility.BLOCKED
        reasons.add(MasterComparisonReason.INVOCATION_NOT_BOUND)
    elif rejected:
        eligibility = MasterComparisonEligibility.BLOCKED
        reasons.add(MasterComparisonReason.ADMISSION_REJECTED)
    elif not _pair_matches(
        request.category,
        left.authorization.specialist,
        right.authorization.specialist,
    ):
        eligibility = MasterComparisonEligibility.BLOCKED
        reasons.add(MasterComparisonReason.SPECIALIST_PAIR_NOT_APPLICABLE)
    else:
        eligibility = _assess_category(
            request,
            left.artifact,
            right.artifact,
            left.authorization,
            right.authorization,
            reasons,
            missing,
        )
    return MasterComparisonAssessment(
        master_request_id=request.master_request_id,
        left_invocation_id=left.authorization.invocation_id,
        right_invocation_id=right.authorization.invocation_id,
        category=request.category,
        eligibility=eligibility,
        reason_codes=_ordered(MasterComparisonReason, reasons),
        missing_prerequisites=_ordered(MasterComparisonPrerequisite, missing),
        left_admission_status=left_admission.admission_status,
        right_admission_status=right_admission.admission_status,
        left_admission_reasons=left_admission.reason_codes,
        right_admission_reasons=right_admission.reason_codes,
        left_binding_defects=left_admission.binding_defects,
        right_binding_defects=right_admission.binding_defects,
    )


def _note_hints(request: MasterComparisonRequest, reasons: set[MasterComparisonReason]) -> None:
    if any(
        hint is not None
        for hint in (
            request.aircraft_id_hint,
            request.hazard_id_hint,
            request.region_text_hint,
            request.metric_name_hint,
        )
    ):
        reasons.add(MasterComparisonReason.SELECTOR_HINT_NOT_EVIDENCE)


def _note_decision_limits(
    left: MasterInvocationAuthorization,
    right: MasterInvocationAuthorization,
    reasons: set[MasterComparisonReason],
    missing: set[MasterComparisonPrerequisite],
) -> None:
    if any(item.specialist is _DECISION for item in (left, right)):
        reasons.add(MasterComparisonReason.DECISION_EXECUTION_NOT_AUTHORIZED)
        missing.add(MasterComparisonPrerequisite.DECISION_TARGET_AUTHORIZATION)
    for authorization in (left, right):
        if authorization.expected_aircraft_id is not None:
            reasons.add(MasterComparisonReason.EXPECTED_AIRCRAFT_IS_NOT_AUTHENTICATED_IDENTITY)


def _assess_category(
    request: MasterComparisonRequest,
    left_artifact: Any,
    right_artifact: Any,
    left_authorization: MasterInvocationAuthorization,
    right_authorization: MasterInvocationAuthorization,
    reasons: set[MasterComparisonReason],
    missing: set[MasterComparisonPrerequisite],
) -> MasterComparisonEligibility:
    category = request.category
    _note_clocks(left_artifact, right_artifact, left_authorization, right_authorization, reasons, missing)
    if category in _UNSUPPORTED_BASE:
        return _unsupported(category, left_artifact, right_artifact, reasons, missing)
    if category is MasterComparisonCategory.EXACT_COUNT_VS_LOWER_BOUND:
        return _exact_versus_lower_bound(left_artifact, right_artifact, reasons, missing)
    _not_established(category, request, left_artifact, right_artifact, reasons, missing)
    return MasterComparisonEligibility.NOT_ESTABLISHED


def _unsupported(
    category: MasterComparisonCategory,
    left_artifact: Any,
    right_artifact: Any,
    reasons: set[MasterComparisonReason],
    missing: set[MasterComparisonPrerequisite],
) -> MasterComparisonEligibility:
    if category not in _EXACT_PAIRS and category not in _INTRA_ARTIFACT_DECISION:
        reasons.add(MasterComparisonReason.NON_SPECIALIST_DOMAIN_NOT_IMPLEMENTED)
    if category in _INTRA_ARTIFACT_DECISION:
        reasons.add(MasterComparisonReason.CROSS_INVOCATION_DECISION_COMPARISON_NOT_ADMITTED)
        if category is MasterComparisonCategory.CURRENT_VS_PERSISTED_EVIDENCE:
            reasons.add(MasterComparisonReason.CURRENT_AND_PERSISTED_SCOPES_BELONG_TO_ONE_ARTIFACT)
            missing.add(MasterComparisonPrerequisite.CURRENT_VERSUS_PERSISTED_EVIDENCE)
        else:
            reasons.add(MasterComparisonReason.ABSENCE_AND_LOW_BELONG_TO_ONE_ARTIFACT)
            missing.add(MasterComparisonPrerequisite.RISK_SEMANTIC_EQUIVALENCE)
    elif category is MasterComparisonCategory.LIVE_OPS_HAZARD_EXPOSURE_VS_HISTORICAL_COUNT:
        reasons.add(MasterComparisonReason.CURRENT_EXPOSURE_IS_NOT_HISTORICAL_COUNT)
        reasons.add(MasterComparisonReason.HISTORICAL_COUNT_IS_NOT_RISK_SEVERITY)
        missing.add(MasterComparisonPrerequisite.METRIC_DEFINITIONS)
        missing.add(MasterComparisonPrerequisite.CURRENT_VERSUS_HISTORICAL_SCOPE)
    elif category is MasterComparisonCategory.CALLSIGN_DISCOVERY_VS_DECISION_TARGET:
        _note_callsign(left_artifact, right_artifact, reasons, missing)
    elif category is MasterComparisonCategory.CURRENT_SIGMET_VS_HISTORICAL_HAZARD_VERSIONS:
        reasons.add(MasterComparisonReason.CURRENT_EXPOSURE_IS_NOT_HISTORICAL_COUNT)
        reasons.add(MasterComparisonReason.SIGMET_EXPOSURE_IS_NOT_RISK_CLASSIFICATION)
        reasons.add(MasterComparisonReason.SIGMET_GEOMETRY_NOT_PRESENT)
        missing.add(MasterComparisonPrerequisite.GEOGRAPHIC_GEOMETRY)
        missing.add(MasterComparisonPrerequisite.HAZARD_SOURCE_VERSION)
    elif category is MasterComparisonCategory.MISSING_GEOMETRY_VS_CLAIMED_INTERSECTION:
        _note_geometry(left_artifact, right_artifact, reasons, missing)
    elif category is MasterComparisonCategory.RECOMMENDATION_RECORD_VS_PILOT_INSTRUCTION:
        reasons.add(MasterComparisonReason.ADVISORY_OUTPUT_IS_NOT_PILOT_INSTRUCTION)
        reasons.add(MasterComparisonReason.ADVISORY_OUTPUT_IS_NOT_ATC_INSTRUCTION)
        if _advisory_claims(left_artifact, right_artifact):
            reasons.add(MasterComparisonReason.ADVISORY_ONLY_CLAIM_OBSERVED)
        missing.add(MasterComparisonPrerequisite.ADVISORY_VERSUS_AUTHORITATIVE_ACTION)
    elif category is MasterComparisonCategory.AIRCRAFT_POSITION_VS_BOUNDARY_CROSSING:
        if _has_position(left_artifact) or _has_position(right_artifact):
            reasons.add(MasterComparisonReason.AIRCRAFT_POSITION_IS_NOT_BOUNDARY_CROSSING)
        else:
            reasons.add(MasterComparisonReason.AIRCRAFT_POSITION_ABSENT)
        reasons.add(MasterComparisonReason.GEOGRAPHIC_GEOMETRY_ABSENT)
        missing.add(MasterComparisonPrerequisite.GEOGRAPHIC_GEOMETRY)
    elif category is MasterComparisonCategory.CENSUS_GEOMETRY_VS_OPERATIONAL_AIRSPACE:
        reasons.add(MasterComparisonReason.CENSUS_GEOMETRY_IS_NOT_FAA_AIRSPACE)
        reasons.add(MasterComparisonReason.FIR_ARTCC_BOUNDARY_NOT_PRESENT)
        reasons.add(MasterComparisonReason.OPERATIONAL_AIRSPACE_SCOPE_ABSENT)
        missing.add(MasterComparisonPrerequisite.OPERATIONAL_AIRSPACE_SCOPE)
        missing.add(MasterComparisonPrerequisite.GEOGRAPHIC_GEOMETRY)
    return MasterComparisonEligibility.UNSUPPORTED


def _not_established(
    category: MasterComparisonCategory,
    request: MasterComparisonRequest,
    left_artifact: Any,
    right_artifact: Any,
    reasons: set[MasterComparisonReason],
    missing: set[MasterComparisonPrerequisite],
) -> None:
    if category in {
        MasterComparisonCategory.LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT,
        MasterComparisonCategory.SAME_AIRCRAFT_DIFFERENT_OBSERVATION_TIMES,
        MasterComparisonCategory.HISTORICAL_RECORD_VS_CURRENT_AIRCRAFT,
    }:
        _note_aircraft_ids(left_artifact, right_artifact, reasons, missing)
    if category is MasterComparisonCategory.HISTORICAL_RECORD_VS_CURRENT_AIRCRAFT:
        _note_record_identity(left_artifact, right_artifact, reasons)
    if category is MasterComparisonCategory.SAME_AIRCRAFT_DIFFERENT_OBSERVATION_TIMES:
        _note_position_times(left_artifact, right_artifact, reasons)
        _note_shared_reference(left_artifact, right_artifact, reasons)
    if category is MasterComparisonCategory.SAME_HAZARD_DIFFERENT_SOURCE_VERSIONS:
        _note_source_versions(left_artifact, right_artifact, reasons, missing)
    if category in {
        MasterComparisonCategory.LIVE_OPS_RISK_VS_DECISION_RISK,
        MasterComparisonCategory.LIVE_OPS_RISK_ABSENCE_VS_DECISION_RISK_ABSENCE,
    }:
        _note_risk_tokens(left_artifact, right_artifact, reasons, missing)
        reasons.add(MasterComparisonReason.STORED_RISK_IS_NOT_CURRENT_WITHOUT_PROOF)
    if category is MasterComparisonCategory.LIVE_OPS_RISK_ABSENCE_VS_DECISION_RISK_ABSENCE:
        if _risk_absent(left_artifact) or _risk_absent(right_artifact):
            reasons.add(MasterComparisonReason.RECORDED_ABSENCE_IS_NOT_LOW)
            reasons.add(MasterComparisonReason.MISSING_RISK_EVALUATION_IS_NOT_NO_RISK)
    if category is MasterComparisonCategory.LIVE_OPS_RECOMMENDATIONS_VS_DECISION_RECOMMENDATIONS:
        reasons.add(MasterComparisonReason.RECOMMENDATION_SET_EQUIVALENCE_NOT_ESTABLISHED)
        reasons.add(MasterComparisonReason.ADVISORY_OUTPUT_IS_NOT_PILOT_INSTRUCTION)
        missing.add(MasterComparisonPrerequisite.ADVISORY_VERSUS_AUTHORITATIVE_ACTION)
    if category in {
        MasterComparisonCategory.HISTORICAL_COUNT_VS_CURRENT_POPULATION,
        MasterComparisonCategory.HISTORICAL_EXACT_VS_LIVE_OPS_SUBSET,
        MasterComparisonCategory.SAME_COUNT_NAME_DIFFERENT_DEFINITIONS,
    }:
        reasons.add(MasterComparisonReason.HISTORICAL_COUNT_IS_NOT_CURRENT_POPULATION)
        reasons.add(MasterComparisonReason.METRIC_DEFINITIONS_NOT_EQUIVALENT)
        reasons.add(MasterComparisonReason.HISTORICAL_WINDOW_IS_NOT_CURRENT_OBSERVATION)
        missing.add(MasterComparisonPrerequisite.METRIC_DEFINITIONS)
        missing.add(MasterComparisonPrerequisite.POPULATION_COVERAGE)
        missing.add(MasterComparisonPrerequisite.CURRENT_VERSUS_HISTORICAL_SCOPE)
        if _has_exact(left_artifact, right_artifact) and _has_subset_count(left_artifact, right_artifact):
            reasons.add(MasterComparisonReason.EXACT_COUNT_IS_NOT_SUBSET_COUNT)
    if category is MasterComparisonCategory.HISTORICAL_VERIFIED_ZERO_VS_LIVE_OPS_UNKNOWN:
        _note_verified_zero(left_artifact, right_artifact, reasons, missing)
    if category in {
        MasterComparisonCategory.CALIFORNIA_SUBSET_VS_NETWORK_COUNT,
        MasterComparisonCategory.PARTIAL_SET_VS_NETWORK_CONCLUSION,
    }:
        _note_coverage(left_artifact, right_artifact, reasons, missing)
        _note_region_text(request, left_artifact, right_artifact, reasons, missing)
    if category in {
        MasterComparisonCategory.CALIFORNIA_USER_REGION_TEXT,
        MasterComparisonCategory.UNKNOWN_POLYGON_PROVENANCE,
        MasterComparisonCategory.MISSING_REGION_DATASET_VERSION,
    }:
        _note_region_text(request, left_artifact, right_artifact, reasons, missing)
        reasons.add(MasterComparisonReason.POLYGON_PROVENANCE_ABSENT)
        reasons.add(MasterComparisonReason.REGION_DATASET_VERSION_ABSENT)
        reasons.add(MasterComparisonReason.OPERATIONAL_AIRSPACE_SCOPE_ABSENT)
        missing.add(MasterComparisonPrerequisite.REGION_IDENTITY)
        missing.add(MasterComparisonPrerequisite.GEOGRAPHIC_GEOMETRY)
    if category is MasterComparisonCategory.QUERY_TIME_VS_OBSERVATION_TIME:
        reasons.add(MasterComparisonReason.QUERY_TIME_IS_NOT_SOURCE_EVENT_TIME)
        reasons.add(MasterComparisonReason.HISTORICAL_WINDOW_IS_NOT_CURRENT_OBSERVATION)
        missing.add(MasterComparisonPrerequisite.SOURCE_EVENT_TIME)


def _exact_versus_lower_bound(
    left_artifact: Any,
    right_artifact: Any,
    reasons: set[MasterComparisonReason],
    missing: set[MasterComparisonPrerequisite],
) -> MasterComparisonEligibility:
    exact_values = _exact_values(left_artifact) + _exact_values(right_artifact)
    lower_values = _lower_values(left_artifact) + _lower_values(right_artifact)
    missing.add(MasterComparisonPrerequisite.METRIC_DEFINITIONS)
    reasons.add(MasterComparisonReason.METRIC_DEFINITIONS_NOT_EQUIVALENT)
    if exact_values and lower_values:
        reasons.add(MasterComparisonReason.EXACT_COUNT_IS_NOT_LOWER_BOUND)
        if set(exact_values) & set(lower_values):
            reasons.add(MasterComparisonReason.MATCHING_COUNT_VALUE_IS_NOT_METRIC_EQUIVALENCE)
        return MasterComparisonEligibility.CONTRACT_CHECKABLE_ONLY
    if exact_values:
        reasons.add(MasterComparisonReason.LOWER_BOUND_CLAIM_ABSENT)
    else:
        reasons.add(MasterComparisonReason.EXACT_COUNT_CLAIM_ABSENT)
    return MasterComparisonEligibility.NOT_ESTABLISHED


def _note_clocks(
    left_artifact: Any,
    right_artifact: Any,
    left_authorization: MasterInvocationAuthorization,
    right_authorization: MasterInvocationAuthorization,
    reasons: set[MasterComparisonReason],
    missing: set[MasterComparisonPrerequisite],
) -> None:
    clocks: list[tuple[str, str]] = []
    events = False
    for authorization in (left_authorization, right_authorization):
        if authorization.application_request_time_utc is not None:
            clocks.append(("application_request_time", authorization.application_request_time_utc))
        if authorization.requested_evaluation_time_utc is not None:
            clocks.append(("requested_evaluation_time", authorization.requested_evaluation_time_utc))
    for artifact in (left_artifact, right_artifact):
        live = _live_result(artifact)
        if live is not None:
            clocks.append(("live_ops_reference_utc", live.reference_utc))
            reasons.add(MasterComparisonReason.LIVE_OPS_REFERENCE_TIME_IS_NOT_OBSERVATION_TIME)
            for position_time in _position_times(artifact):
                clocks.append(("position_time_utc", position_time))
        historical = _historical(artifact)
        if historical is not None:
            if historical.evaluated_as_of_utc is not None:
                clocks.append(("historical_evaluated_as_of", historical.evaluated_as_of_utc))
            for claim in historical.verified_claims:
                if isinstance(claim, HistoricalWindowClaim):
                    clocks.append(("historical_window_start", claim.start_utc))
                    clocks.append(("historical_window_end", claim.end_utc))
            for tool in historical.tool_results:
                if tool.as_of_utc is not None:
                    clocks.append(("tool_as_of", tool.as_of_utc))
                for evidence in tool.evidence:
                    clocks.append(("query_timestamp", evidence.query_timestamp_utc))
                    for record in evidence.source_records:
                        if record.event_timestamp_utc is not None:
                            events = True
                            clocks.append(("source_event_timestamp", record.event_timestamp_utc))
        decision = _decision(artifact)
        if decision is not None and decision.verification is not None:
            current_as_of = decision.verification.current_as_of_utc
            if current_as_of is not None:
                clocks.append(("decision_current_as_of", current_as_of))
    labels_for_value: dict[str, set[str]] = {}
    for label, value in clocks:
        labels_for_value.setdefault(value, set()).add(label)
    if any(len(labels) > 1 for labels in labels_for_value.values()):
        reasons.add(MasterComparisonReason.IDENTICAL_UTC_STRING_NOT_ATOMIC_OBSERVATION)
    if not events:
        reasons.add(MasterComparisonReason.SOURCE_EVENT_TIME_ABSENT)
        reasons.add(MasterComparisonReason.OBSERVATION_TIME_ABSENT)
    missing.add(MasterComparisonPrerequisite.OBSERVATION_TIME_SEMANTICS)
    missing.add(MasterComparisonPrerequisite.EVALUATION_TIME_EQUIVALENCE)
    missing.add(MasterComparisonPrerequisite.SOURCE_EVENT_TIME)


def _note_aircraft_ids(
    left_artifact: Any,
    right_artifact: Any,
    reasons: set[MasterComparisonReason],
    missing: set[MasterComparisonPrerequisite],
) -> None:
    left_ids = _aircraft_ids(left_artifact)
    right_ids = _aircraft_ids(right_artifact)
    missing.add(MasterComparisonPrerequisite.TYPED_AIRCRAFT_IDENTITY)
    missing.add(MasterComparisonPrerequisite.TRUSTED_IDENTITY_BINDING)
    if not left_ids or not right_ids:
        reasons.add(MasterComparisonReason.TYPED_AIRCRAFT_IDENTITY_ABSENT)
        return
    if set(left_ids) & set(right_ids):
        reasons.add(MasterComparisonReason.SHARED_AIRCRAFT_ID_STRING_NOT_AUTHENTICATED_JOIN)
    if set(left_ids) != set(right_ids):
        reasons.add(MasterComparisonReason.AIRCRAFT_ID_STRINGS_ARE_DIFFERENT)


def _note_record_identity(
    left_artifact: Any,
    right_artifact: Any,
    reasons: set[MasterComparisonReason],
) -> None:
    reasons.add(MasterComparisonReason.HISTORICAL_RECORD_ID_NOT_AIRCRAFT_ID)
    record_ids = set(_record_ids(left_artifact)) | set(_record_ids(right_artifact))
    aircraft_ids = set(_aircraft_ids(left_artifact)) | set(_aircraft_ids(right_artifact))
    if record_ids and aircraft_ids and record_ids & aircraft_ids:
        reasons.add(MasterComparisonReason.SHARED_RECORD_ID_STRING_NOT_AN_AIRCRAFT_JOIN)


def _note_position_times(
    left_artifact: Any,
    right_artifact: Any,
    reasons: set[MasterComparisonReason],
) -> None:
    left_times = _position_times(left_artifact)
    right_times = _position_times(right_artifact)
    if not left_times or not right_times:
        reasons.add(MasterComparisonReason.OBSERVATION_TIME_ABSENT)
        return
    if set(left_times) & set(right_times):
        reasons.add(MasterComparisonReason.SHARED_POSITION_TIME_STRING_NOT_ATOMIC)
    if set(left_times) != set(right_times):
        reasons.add(MasterComparisonReason.OBSERVATION_TIME_STRINGS_ARE_DIFFERENT)


def _note_shared_reference(left_artifact: Any, right_artifact: Any, reasons: set[MasterComparisonReason]) -> None:
    left = _live_result(left_artifact)
    right = _live_result(right_artifact)
    if left is None or right is None:
        return
    if left.reference_utc == right.reference_utc:
        reasons.add(MasterComparisonReason.SHARED_REFERENCE_UTC_IS_NOT_ATOMIC_OBSERVATION)


def _note_source_versions(
    left_artifact: Any,
    right_artifact: Any,
    reasons: set[MasterComparisonReason],
    missing: set[MasterComparisonPrerequisite],
) -> None:
    left_versions = _source_versions(left_artifact)
    right_versions = _source_versions(right_artifact)
    missing.add(MasterComparisonPrerequisite.HAZARD_SOURCE_VERSION)
    if not left_versions or not right_versions:
        reasons.add(MasterComparisonReason.HAZARD_SOURCE_VERSION_ABSENT)
        return
    if set(left_versions) & set(right_versions):
        reasons.add(MasterComparisonReason.SHARED_SOURCE_VERSION_STRING_NOT_AUTHENTICATED)
    if set(left_versions) != set(right_versions):
        reasons.add(MasterComparisonReason.SOURCE_VERSION_STRINGS_ARE_DIFFERENT)


def _note_risk_tokens(
    left_artifact: Any,
    right_artifact: Any,
    reasons: set[MasterComparisonReason],
    missing: set[MasterComparisonPrerequisite],
) -> None:
    live_levels = _live_risk_levels(left_artifact) + _live_risk_levels(right_artifact)
    stored_levels = _stored_risk_levels(left_artifact) + _stored_risk_levels(right_artifact)
    missing.add(MasterComparisonPrerequisite.RISK_SEMANTIC_EQUIVALENCE)
    reasons.add(MasterComparisonReason.RISK_SEMANTIC_EQUIVALENCE_NOT_ESTABLISHED)
    if live_levels and stored_levels:
        reasons.add(MasterComparisonReason.RISK_TOKEN_IS_NOT_STORED_RISK_LEVEL)
        stored_text = {level.value for level in stored_levels}
        if set(live_levels) & stored_text:
            reasons.add(MasterComparisonReason.MATCHING_RISK_TOKEN_IS_NOT_STORED_LEVEL)


def _note_callsign(
    left_artifact: Any,
    right_artifact: Any,
    reasons: set[MasterComparisonReason],
    missing: set[MasterComparisonPrerequisite],
) -> None:
    discoveries = _discoveries(left_artifact) + _discoveries(right_artifact)
    missing.add(MasterComparisonPrerequisite.TYPED_AIRCRAFT_IDENTITY)
    missing.add(MasterComparisonPrerequisite.TRUSTED_IDENTITY_BINDING)
    missing.add(MasterComparisonPrerequisite.DECISION_TARGET_AUTHORIZATION)
    discovered_ids = {
        aircraft_id
        for discovery in discoveries
        for aircraft_id in discovery.aircraft_ids
        if isinstance(aircraft_id, str)
    }
    decision_ids = set(_aircraft_ids(left_artifact)) | set(_aircraft_ids(right_artifact))
    if discoveries:
        reasons.add(MasterComparisonReason.CALLSIGN_DOES_NOT_AUTHORIZE_DECISION_TARGET)
        if discovered_ids and decision_ids and discovered_ids & decision_ids:
            reasons.add(MasterComparisonReason.SHARED_AIRCRAFT_ID_STRING_NOT_AUTHENTICATED_JOIN)
    else:
        reasons.add(MasterComparisonReason.CALLSIGN_DISCOVERY_ABSENT)
    reasons.add(MasterComparisonReason.DECISION_EXECUTION_NOT_AUTHORIZED)


def _note_geometry(
    left_artifact: Any,
    right_artifact: Any,
    reasons: set[MasterComparisonReason],
    missing: set[MasterComparisonPrerequisite],
) -> None:
    if _intersection_flag(left_artifact) or _intersection_flag(right_artifact):
        reasons.add(MasterComparisonReason.INTERSECTION_FLAG_IS_NOT_GEOMETRY_PROOF)
    else:
        reasons.add(MasterComparisonReason.GEOGRAPHIC_GEOMETRY_ABSENT)
    reasons.add(MasterComparisonReason.POLYGON_PROVENANCE_ABSENT)
    reasons.add(MasterComparisonReason.REGION_DATASET_VERSION_ABSENT)
    reasons.add(MasterComparisonReason.SIGMET_GEOMETRY_NOT_PRESENT)
    missing.add(MasterComparisonPrerequisite.GEOGRAPHIC_GEOMETRY)
    missing.add(MasterComparisonPrerequisite.REGION_IDENTITY)


def _note_verified_zero(
    left_artifact: Any,
    right_artifact: Any,
    reasons: set[MasterComparisonReason],
    missing: set[MasterComparisonPrerequisite],
) -> None:
    has_zero = _has_verified_zero(left_artifact) or _has_verified_zero(right_artifact)
    outcome = _live_outcome(left_artifact) or _live_outcome(right_artifact)
    missing.add(MasterComparisonPrerequisite.POPULATION_COVERAGE)
    missing.add(MasterComparisonPrerequisite.METRIC_DEFINITIONS)
    if has_zero and outcome is LiveOpsPublicVerificationOutcome.NONE_VERIFIED:
        reasons.add(MasterComparisonReason.VERIFIED_ZERO_IS_NOT_UNKNOWN)
    elif has_zero:
        reasons.add(MasterComparisonReason.VERIFIED_ZERO_IS_NOT_A_CURRENT_OUTCOME)
    else:
        reasons.add(MasterComparisonReason.VERIFIED_ZERO_IS_NOT_UNKNOWN)


def _note_coverage(
    left_artifact: Any,
    right_artifact: Any,
    reasons: set[MasterComparisonReason],
    missing: set[MasterComparisonPrerequisite],
) -> None:
    missing.add(MasterComparisonPrerequisite.POPULATION_COVERAGE)
    missing.add(MasterComparisonPrerequisite.REGION_IDENTITY)
    for artifact in (left_artifact, right_artifact):
        outcome = _live_outcome(artifact)
        if outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED:
            reasons.add(MasterComparisonReason.ALL_VERIFIED_IS_NOT_NETWORK_COMPLETE)
        elif outcome is LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED:
            reasons.add(MasterComparisonReason.PARTIAL_EVIDENCE_IS_NOT_NETWORK_COMPLETE)
        if _has_network_metric(artifact) and _has_subset_count(artifact, artifact):
            reasons.add(MasterComparisonReason.NETWORK_SCOPE_IS_NOT_REGIONAL_SUBSET)
    if _has_subset_count(left_artifact, right_artifact) and (
        _has_network_metric(left_artifact) or _has_network_metric(right_artifact)
    ):
        reasons.add(MasterComparisonReason.NETWORK_SCOPE_IS_NOT_REGIONAL_SUBSET)


def _note_region_text(
    request: MasterComparisonRequest,
    left_artifact: Any,
    right_artifact: Any,
    reasons: set[MasterComparisonReason],
    missing: set[MasterComparisonPrerequisite],
) -> None:
    texts = _region_texts(request, left_artifact, right_artifact)
    missing.add(MasterComparisonPrerequisite.REGION_IDENTITY)
    if not texts:
        reasons.add(MasterComparisonReason.REGION_TEXT_ABSENT)
        return
    reasons.add(MasterComparisonReason.REGION_TEXT_UNTRUSTED)
    if any(text.casefold() == "california" for text in texts):
        reasons.add(MasterComparisonReason.CALIFORNIA_TEXT_IS_NOT_AN_OPERATIONAL_BOUNDARY)


def _live_result(artifact: Any) -> LiveOpsRunResult | None:
    if isinstance(artifact, LiveOpsAdmissionSources):
        return artifact.result
    return None


def _historical(artifact: Any) -> SpecialistResult | None:
    if isinstance(artifact, SpecialistResult):
        return artifact
    return None


def _decision(artifact: Any) -> DecisionSpecialistRunResult | None:
    if isinstance(artifact, DecisionSpecialistRunResult):
        return artifact
    return None


def _decision_claims(artifact: Any) -> tuple[Any, ...]:
    decision = _decision(artifact)
    if decision is None:
        return ()
    claims = list(decision.proposed_claims)
    if decision.verification is not None:
        claims.extend(decision.verification.verified_claims)
    return tuple(claims)


def _live_claims(artifact: Any) -> tuple[Any, ...]:
    live = _live_result(artifact)
    if live is None:
        return ()
    return live.verified_claims


def _aircraft_ids(artifact: Any) -> tuple[str, ...]:
    found: list[str] = []
    for claim in _live_claims(artifact):
        if isinstance(claim.aircraft_id, str):
            found.append(claim.aircraft_id)
    decision = _decision(artifact)
    if decision is not None and isinstance(decision.aircraft_id, str):
        found.append(decision.aircraft_id)
    for claim in _decision_claims(artifact):
        if isinstance(claim, AircraftIdentityClaim):
            found.append(claim.aircraft_id)
    return tuple(dict.fromkeys(found))


def _record_ids(artifact: Any) -> tuple[str, ...]:
    historical = _historical(artifact)
    if historical is None:
        return ()
    return tuple(
        claim.record_id
        for claim in historical.verified_claims
        if isinstance(claim, RecordIdentityClaim)
    )


def _source_versions(artifact: Any) -> tuple[str, ...]:
    found: list[str] = []
    for claim in _live_claims(artifact):
        if isinstance(claim.source_version, str):
            found.append(claim.source_version)
        if isinstance(claim.hazard_source_version, str):
            found.append(claim.hazard_source_version)
    return tuple(dict.fromkeys(found))


def _position_times(artifact: Any) -> tuple[str, ...]:
    found: list[str] = []
    for claim in _live_claims(artifact):
        if (
            claim.kind is LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE
            and claim.observed_field is LiveOpsPublicObservedField.POSITION_TIME_UTC
            and isinstance(claim.observed_value, str)
        ):
            found.append(claim.observed_value)
    return tuple(dict.fromkeys(found))


def _live_risk_levels(artifact: Any) -> tuple[str, ...]:
    return tuple(
        claim.risk_level
        for claim in _live_claims(artifact)
        if claim.kind is LiveOpsPublicClaimKind.RISK_STATE and isinstance(claim.risk_level, str)
    )


def _stored_risk_levels(artifact: Any) -> tuple[StoredRiskLevel, ...]:
    return tuple(
        claim.risk_level
        for claim in _decision_claims(artifact)
        if isinstance(claim, RiskPresentClaim)
    )


def _risk_absent(artifact: Any) -> bool:
    for claim in _live_claims(artifact):
        if (
            claim.kind is LiveOpsPublicClaimKind.RISK_STATE
            and claim.risk_mode is LiveOpsPublicRiskMode.ABSENT_FROM_OBSERVED_CANDIDATES
        ):
            return True
    for claim in _decision_claims(artifact):
        if isinstance(claim, RiskAbsentClaim):
            return True
        if (
            isinstance(claim, EvaluationStateClaim)
            and claim.evaluation_state is DecisionEvaluationState.NOT_ESTABLISHED
        ):
            return True
    return False


def _exact_values(artifact: Any) -> tuple[int, ...]:
    historical = _historical(artifact)
    if historical is None:
        return ()
    return tuple(
        claim.value for claim in historical.verified_claims if isinstance(claim, ExactCountClaim)
    )


def _lower_values(artifact: Any) -> tuple[int, ...]:
    historical = _historical(artifact)
    if historical is None:
        return ()
    return tuple(
        claim.minimum_value
        for claim in historical.verified_claims
        if isinstance(claim, LowerBoundCountClaim)
    )


def _has_exact(left_artifact: Any, right_artifact: Any) -> bool:
    return bool(_exact_values(left_artifact) or _exact_values(right_artifact))


def _has_verified_zero(artifact: Any) -> bool:
    historical = _historical(artifact)
    if historical is None:
        return False
    return any(isinstance(claim, VerifiedZeroClaim) for claim in historical.verified_claims)


def _has_subset_count(left_artifact: Any, right_artifact: Any) -> bool:
    for artifact in (left_artifact, right_artifact):
        for claim in _live_claims(artifact):
            if (
                claim.kind is LiveOpsPublicClaimKind.SCOPED_COUNT
                and claim.count_mode is LiveOpsPublicCountMode.CONFIRMED_SUBSET
            ):
                return True
    return False


def _has_network_metric(artifact: Any) -> bool:
    for claim in _live_claims(artifact):
        metric = claim.metric
        if isinstance(metric, LiveOpsPublicCountMetric) and metric.value.startswith("NETWORK_"):
            return True
    return False


def _live_outcome(artifact: Any) -> LiveOpsPublicVerificationOutcome | None:
    live = _live_result(artifact)
    if live is None:
        return None
    return live.verification_outcome


def _discoveries(artifact: Any) -> tuple[Any, ...]:
    live = _live_result(artifact)
    if live is None:
        return ()
    return live.discoveries


def _intersection_flag(artifact: Any) -> bool:
    for claim in _live_claims(artifact):
        for flag in claim.recorded_flags:
            if flag.name is LiveOpsPublicRecordedFlag.EXACT_INTERSECTION_CONFIRMED and flag.value is True:
                return True
    return False


def _has_position(artifact: Any) -> bool:
    position_fields = {
        LiveOpsPublicObservedField.LATITUDE,
        LiveOpsPublicObservedField.LONGITUDE,
        LiveOpsPublicObservedField.TRACK_DEG,
    }
    return any(
        claim.kind is LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE
        and claim.observed_field in position_fields
        for claim in _live_claims(artifact)
    )


def _advisory_claims(left_artifact: Any, right_artifact: Any) -> bool:
    return any(
        isinstance(claim, RecommendationActionClaim)
        for artifact in (left_artifact, right_artifact)
        for claim in _decision_claims(artifact)
    )


def _region_texts(
    request: MasterComparisonRequest,
    left_artifact: Any,
    right_artifact: Any,
) -> tuple[str, ...]:
    found: list[str] = []
    if request.region_text_hint is not None:
        found.append(request.region_text_hint)
    for artifact in (left_artifact, right_artifact):
        live = artifact if isinstance(artifact, LiveOpsAdmissionSources) else None
        if live is not None and live.request.selector_hints is not None:
            region = live.request.selector_hints.region
            if isinstance(region, str):
                found.append(region)
        for claim in _live_claims(artifact):
            if isinstance(claim.region_name, str):
                found.append(claim.region_name)
            if isinstance(claim.region_query, str):
                found.append(claim.region_query)
            scope = claim.scope
            if scope is not None and isinstance(scope.region, str):
                found.append(scope.region)
    return tuple(dict.fromkeys(found))
