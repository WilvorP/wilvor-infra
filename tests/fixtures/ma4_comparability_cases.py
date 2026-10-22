"""Test-only catalog of MA4.3 comparison prerequisites.

These rows are future test obligations. They are not production verdicts,
and this module does not compare specialist artifacts. Nothing here returns
COMPATIBLE, CONFLICT, or VERIFIED.

``CONTRACT_CHECKABLE_ONLY`` means an existing contract can reject an unsafe
encoding or keep two fields distinct. It does not authenticate a source and
it does not establish operational compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ComparabilityEligibility(str, Enum):
    """Current eligibility of a comparison. Not an operational verdict."""

    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRACT_CHECKABLE_ONLY = "CONTRACT_CHECKABLE_ONLY"


FORBIDDEN_COMPARISON_OUTCOMES = frozenset(
    {
        "VERIFIED",
        "COMPATIBLE",
        "CONFLICT",
        "CONFLICTING",
    }
)


@dataclass(frozen=True)
class ComparabilityPrerequisite:
    """One comparison that cannot be established from the current contracts."""

    comparison_id: str
    comparison_category: str
    left_specialist: str
    right_specialist: str
    left_claim_family: str
    right_claim_family: str
    required_typed_identity: bool
    required_temporal_equivalence: bool
    required_source_version_equivalence: bool
    required_geographic_scope_equivalence: bool
    required_metric_subject_equivalence: bool
    required_coverage_completeness_proof: bool
    required_provenance_authenticity: bool
    expected_eligibility: ComparabilityEligibility
    missing_prerequisites: tuple[str, ...]
    why_not_established: str

    def __post_init__(self) -> None:
        if not isinstance(self.expected_eligibility, ComparabilityEligibility):
            raise ValueError("eligibility must be a current comparability category")
        if self.expected_eligibility.value in FORBIDDEN_COMPARISON_OUTCOMES:
            raise ValueError("production comparison outcomes are not baseline categories")
        if not self.comparison_id or not self.comparison_category:
            raise ValueError("comparison identity is required")
        if not isinstance(self.missing_prerequisites, tuple) or not self.missing_prerequisites:
            raise ValueError("missing prerequisites must stay visible")
        if any(not isinstance(item, str) or not item.strip() for item in self.missing_prerequisites):
            raise ValueError("missing prerequisites must be non-empty strings")
        if not self.why_not_established.strip():
            raise ValueError("each case must say why the comparison is unavailable")
        if self.expected_eligibility is ComparabilityEligibility.CONTRACT_CHECKABLE_ONLY:
            if "does not authenticate" not in self.why_not_established:
                raise ValueError(
                    "CONTRACT_CHECKABLE_ONLY must say that the check does not authenticate a source"
                )


def _case(
    comparison_id: str,
    comparison_category: str,
    left_specialist: str,
    right_specialist: str,
    left_claim_family: str,
    right_claim_family: str,
    eligibility: ComparabilityEligibility,
    missing: tuple[str, ...],
    why: str,
    *,
    typed_identity: bool = True,
    temporal: bool = True,
    source_version: bool = True,
    geographic: bool = False,
    metric: bool = True,
    coverage: bool = True,
    provenance: bool = True,
) -> ComparabilityPrerequisite:
    return ComparabilityPrerequisite(
        comparison_id=comparison_id,
        comparison_category=comparison_category,
        left_specialist=left_specialist,
        right_specialist=right_specialist,
        left_claim_family=left_claim_family,
        right_claim_family=right_claim_family,
        required_typed_identity=typed_identity,
        required_temporal_equivalence=temporal,
        required_source_version_equivalence=source_version,
        required_geographic_scope_equivalence=geographic,
        required_metric_subject_equivalence=metric,
        required_coverage_completeness_proof=coverage,
        required_provenance_authenticity=provenance,
        expected_eligibility=eligibility,
        missing_prerequisites=missing,
        why_not_established=why,
    )


_NOT = ComparabilityEligibility.NOT_ESTABLISHED
_UNSUPPORTED = ComparabilityEligibility.UNSUPPORTED
_CHECK = ComparabilityEligibility.CONTRACT_CHECKABLE_ONLY

COMPARABILITY_CASES: tuple[ComparabilityPrerequisite, ...] = (
    _case(
        "live-ops-aircraft-vs-decision-aircraft",
        "Live Ops aircraft identity vs Decision aircraft identity",
        "LIVE_OPS",
        "DECISION",
        "LiveOpsPublicClaim.aircraft_id",
        "AircraftIdentityClaim.aircraft_id",
        _NOT,
        (
            "typed_aircraft_identity",
            "temporal_equivalence",
            "provenance_authenticity",
            "decision_target_authorization",
        ),
        "Live Ops aircraft ids and Decision aircraft ids live on different result envelopes. "
        "A shared string is not an authenticated join, and Decision target authorization is still pending.",
        geographic=False,
        metric=False,
    ),
    _case(
        "live-ops-risk-vs-decision-risk",
        "Live Ops risk vs Decision risk",
        "LIVE_OPS",
        "DECISION",
        "LiveOpsPublicClaimKind.RISK_STATE",
        "RiskPresentClaim",
        _NOT,
        (
            "typed_aircraft_identity",
            "temporal_equivalence",
            "source_version_equivalence",
            "metric_subject_equivalence",
            "coverage_completeness_proof",
            "provenance_authenticity",
        ),
        "Live Ops risk mode and Decision stored risk are separate claim families. "
        "No shared evaluation instant, ruleset version, or encounter identity is authenticated.",
    ),
    _case(
        "live-ops-hazard-exposure-vs-historical-count",
        "Live Ops hazard exposure vs Historical encounter count",
        "LIVE_OPS",
        "HISTORICAL_ANALYTICS",
        "LiveOpsPublicClaimKind.CONFIRMED_IMPACT",
        "ExactCountClaim",
        _UNSUPPORTED,
        (
            "typed_aircraft_identity",
            "temporal_equivalence",
            "source_version_equivalence",
            "geographic_scope_equivalence",
            "metric_subject_equivalence",
            "coverage_completeness_proof",
            "provenance_authenticity",
        ),
        "A current hazard exposure record is not a historical encounter count. "
        "No domain rule converts SIGMET or impact exposure into distinct_encounter_count.",
        geographic=True,
    ),
    _case(
        "historical-count-vs-current-population",
        "Historical count vs current observed encounter population",
        "HISTORICAL_ANALYTICS",
        "LIVE_OPS",
        "ExactCountClaim.distinct_encounter_count",
        "LiveOpsPublicCountMetric.ENCOUNTER_RECORD_COUNT",
        _NOT,
        (
            "temporal_equivalence",
            "geographic_scope_equivalence",
            "metric_subject_equivalence",
            "coverage_completeness_proof",
            "provenance_authenticity",
        ),
        "Historical exact counts use HISTORICAL evaluable windows. "
        "Live Ops encounter counts are current returned observations. The populations are not the same subject.",
        typed_identity=False,
        geographic=True,
    ),
    _case(
        "live-ops-recommendations-vs-decision-recommendations",
        "Live Ops recommendation set vs Decision recommendation set",
        "LIVE_OPS",
        "DECISION",
        "LiveOpsPublicClaimKind.RECOMMENDATION_SET",
        "RecommendationSetClaim",
        _NOT,
        (
            "typed_aircraft_identity",
            "temporal_equivalence",
            "source_version_equivalence",
            "coverage_completeness_proof",
            "provenance_authenticity",
        ),
        "Both families can list recommendation ids. "
        "Neither family proves the sets were read from the same source version at the same observation time.",
        metric=True,
    ),
    _case(
        "same-aircraft-different-observation-times",
        "Same aircraft at different observation times",
        "LIVE_OPS",
        "LIVE_OPS",
        "LiveOpsPublicObservedField.POSITION_TIME_UTC",
        "LiveOpsPublicObservedField.POSITION_TIME_UTC",
        _NOT,
        (
            "temporal_equivalence",
            "provenance_authenticity",
            "atomic_observation",
        ),
        "Two aircraft observations can share an aircraft id and still carry different position times. "
        "Equal-looking timestamps do not prove the positions were observed atomically.",
        source_version=False,
        metric=False,
    ),
    _case(
        "same-hazard-different-source-versions",
        "Same hazard ID at different source versions",
        "LIVE_OPS",
        "LIVE_OPS",
        "LiveOpsPublicClaimKind.CONFIRMED_IMPACT",
        "LiveOpsPublicContradictionSubject.HAZARD_SOURCE_VERSION",
        _NOT,
        (
            "source_version_equivalence",
            "temporal_equivalence",
            "provenance_authenticity",
        ),
        "The same hazard id can appear with different source versions. "
        "A contradiction subject names the hazard and two tool calls, and it does not store or reconcile the versions.",
        typed_identity=True,
        geographic=False,
        metric=False,
        coverage=False,
    ),
    _case(
        "current-vs-persisted-evidence",
        "Current vs persisted evidence",
        "DECISION",
        "DECISION",
        "TemporalScope.CURRENT",
        "TemporalScope.PERSISTED",
        _CHECK,
        (
            "temporal_equivalence",
            "provenance_authenticity",
            "coverage_completeness_proof",
        ),
        "Current and persisted scopes stay on separate claims and tool results. "
        "A link claim can name both refs, and that structural check does not authenticate a source or make persisted evidence current.",
        typed_identity=True,
        source_version=True,
        metric=False,
    ),
    _case(
        "california-subset-vs-network-count",
        "California regional subset vs network-wide count",
        "LIVE_OPS",
        "LIVE_OPS",
        "LiveOpsPublicCountMode.CONFIRMED_SUBSET",
        "LiveOpsPublicCountMetric.NETWORK_ENCOUNTER_COUNT",
        _NOT,
        (
            "geographic_scope_equivalence",
            "metric_subject_equivalence",
            "coverage_completeness_proof",
            "provenance_authenticity",
        ),
        "A California subset count is a returned filtered set. "
        "A network count is a different metric, and user region text remains region-validation pending.",
        typed_identity=False,
        temporal=True,
        source_version=False,
        geographic=True,
    ),
    _case(
        "historical-exact-vs-live-ops-subset",
        "Historical exact count vs Live Ops subset count",
        "HISTORICAL_ANALYTICS",
        "LIVE_OPS",
        "ExactCountClaim",
        "LiveOpsPublicCountMode.CONFIRMED_SUBSET",
        _NOT,
        (
            "temporal_equivalence",
            "metric_subject_equivalence",
            "coverage_completeness_proof",
            "geographic_scope_equivalence",
            "provenance_authenticity",
        ),
        "An exact historical metric id is not a Live Ops subset count. "
        "The verifier rejects unknown Live Ops metric ids on historical tools.",
        typed_identity=False,
        geographic=True,
    ),
    _case(
        "historical-verified-zero-vs-live-ops-unknown",
        "Historical verified zero vs Live Ops unknown",
        "HISTORICAL_ANALYTICS",
        "LIVE_OPS",
        "VerifiedZeroClaim",
        "LiveOpsPublicVerificationOutcome.NONE_VERIFIED",
        _NOT,
        (
            "coverage_completeness_proof",
            "metric_subject_equivalence",
            "temporal_equivalence",
            "provenance_authenticity",
        ),
        "Verified zero requires a historical NOT_FOUND result with an exact zero cardinality. "
        "Live Ops none-verified and UNKNOWN tool status do not carry that proof.",
        typed_identity=False,
        source_version=False,
    ),
    _case(
        "callsign-discovery-vs-decision-target",
        "Callsign discovery vs Decision target",
        "LIVE_OPS",
        "DECISION",
        "LiveOpsDiscovery",
        "MasterInvocationAuthorization.expected_aircraft_id",
        _UNSUPPORTED,
        (
            "typed_aircraft_identity",
            "decision_target_authorization",
            "provenance_authenticity",
        ),
        "A callsign observation can list aircraft ids. "
        "Selection stays unauthorized, and the observation does not grant a Decision target.",
        temporal=True,
        source_version=False,
        metric=False,
        coverage=False,
    ),
    _case(
        "live-ops-risk-absence-vs-decision-risk-absence",
        "Live Ops recorded risk absence vs Decision absent risk",
        "LIVE_OPS",
        "DECISION",
        "LiveOpsPublicRiskMode.ABSENT_FROM_OBSERVED_CANDIDATES",
        "RiskPresence.ABSENT",
        _NOT,
        (
            "typed_aircraft_identity",
            "temporal_equivalence",
            "coverage_completeness_proof",
            "provenance_authenticity",
            "risk_presence_proof",
        ),
        "Both absences mean a stored or observed candidate was not present. "
        "Neither absence proves there is no risk, and the two predicates are not joined.",
        source_version=False,
        metric=True,
    ),
    _case(
        "decision-low-vs-missing-risk",
        "Decision LOW risk vs missing risk evidence",
        "DECISION",
        "DECISION",
        "StoredRiskLevel.LOW",
        "DecisionEvaluationState.NOT_ESTABLISHED",
        _CHECK,
        (
            "risk_presence_proof",
            "coverage_completeness_proof",
            "provenance_authenticity",
        ),
        "An absent or unestablished risk cannot carry level LOW. "
        "That structural check does not authenticate a source and it does not prove the missing evidence is low risk.",
        typed_identity=True,
        temporal=True,
        source_version=False,
        metric=True,
    ),
    _case(
        "partial-set-vs-network-conclusion",
        "A partial returned set vs a whole-network conclusion",
        "LIVE_OPS",
        "LIVE_OPS",
        "LiveOpsPublicCountMode.CONFIRMED_SUBSET",
        "LIVE_OPS_COMPLETENESS_NOTICE",
        _NOT,
        (
            "coverage_completeness_proof",
            "geographic_scope_equivalence",
            "metric_subject_equivalence",
        ),
        "ALL_VERIFIED covers submitted claims only. "
        "The completeness notice says that is not a fresh, complete, or atomic network investigation.",
        typed_identity=False,
        temporal=True,
        source_version=False,
        geographic=True,
        provenance=True,
    ),
    _case(
        "current-sigmet-vs-historical-materialization",
        "Current SIGMET exposure vs historical hazard materialization",
        "LIVE_OPS",
        "HISTORICAL_ANALYTICS",
        "LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION",
        "ExactCountClaim.distinct_hazard_version_count",
        _UNSUPPORTED,
        (
            "temporal_equivalence",
            "source_version_equivalence",
            "geographic_scope_equivalence",
            "metric_subject_equivalence",
            "coverage_completeness_proof",
            "provenance_authenticity",
        ),
        "Current hazard exposure and a historical hazard-version count are different subjects. "
        "Hazard-version summaries keep a window limitation and do not become a risk classification.",
        typed_identity=True,
        geographic=True,
    ),
    _case(
        "missing-geometry-vs-claimed-intersection",
        "Missing geometry vs claimed intersection",
        "LIVE_OPS",
        "CENSUS_REGION_DATASET",
        "LiveOpsPublicRecordedFlag.EXACT_INTERSECTION_CONFIRMED",
        "map_region_identity.geometry_type",
        _UNSUPPORTED,
        (
            "geographic_scope_equivalence",
            "geometry_proof",
            "dataset_version",
            "provenance_authenticity",
        ),
        "A recorded intersection flag and a region-intersection claim can exist without a polygon, dataset version, or operational airspace. "
        "No contract turns that flag into a flight-path intersection.",
        typed_identity=True,
        temporal=True,
        source_version=True,
        geographic=True,
        metric=False,
        coverage=True,
    ),
    _case(
        "same-count-name-different-definitions",
        "Same named count metric with different definitions",
        "HISTORICAL_ANALYTICS",
        "LIVE_OPS",
        "distinct_encounter_count",
        "ENCOUNTER_RECORD_COUNT",
        _NOT,
        (
            "metric_subject_equivalence",
            "temporal_equivalence",
            "coverage_completeness_proof",
        ),
        "Historical distinct_encounter_count and Live Ops ENCOUNTER_RECORD_COUNT use different identifiers and definitions. "
        "A shared English description does not make the counts the same metric.",
        typed_identity=False,
        source_version=False,
        provenance=True,
    ),
    _case(
        "recommendation-record-vs-pilot-instruction",
        "Recommendation record vs pilot-facing instruction",
        "DECISION",
        "LIVE_OPS",
        "RecommendationActionClaim",
        "LiveOpsPublicClaimKind.RECOMMENDATION_SET",
        _UNSUPPORTED,
        (
            "advisory_authority_boundary",
            "provenance_authenticity",
            "decision_target_authorization",
        ),
        "Decision recommendation actions stay ADVISORY_ONLY. "
        "ATC, altitude, and landing instruction capabilities are prohibited, and a recommendation id is not a clearance.",
        temporal=False,
        source_version=False,
        metric=True,
        coverage=False,
    ),
    _case(
        "query-time-vs-observation-time",
        "Source query time vs source observation time",
        "LIVE_OPS",
        "HISTORICAL_ANALYTICS",
        "Evidence.query_timestamp_utc",
        "SourceRecord.event_timestamp_utc",
        _NOT,
        (
            "temporal_equivalence",
            "atomic_observation",
            "provenance_authenticity",
        ),
        "Query time, Live Ops reference time, historical evaluation time, and source event time stay separate fields. "
        "An identical UTC string does not prove the source facts were observed together.",
        typed_identity=False,
        source_version=False,
        metric=False,
        coverage=False,
    ),
    _case(
        "california-user-region-text",
        "California as user-provided region text",
        "USER_REQUEST",
        "LIVE_OPS",
        "MasterRouteHints.region_text",
        "LiveOpsSelectorHints.region",
        _NOT,
        (
            "geographic_scope_equivalence",
            "dataset_version",
            "provenance_authenticity",
        ),
        "The string California is an untrusted hint. "
        "Routing keeps REGION_VALIDATION_PENDING and does not create an aviation region from that string.",
        typed_identity=False,
        temporal=False,
        source_version=True,
        geographic=True,
        metric=False,
        coverage=False,
    ),
    _case(
        "census-geometry-vs-operational-airspace",
        "Census state geometry versus operational aviation airspace",
        "CENSUS_REGION_DATASET",
        "OPERATIONAL_AIRSPACE",
        "map_region_identity",
        "FIR_ARTCC_FAA_SCOPE",
        _UNSUPPORTED,
        (
            "geographic_scope_equivalence",
            "provenance_authenticity",
            "dataset_version",
        ),
        "Region identity can copy a census-style polygon label. "
        "That mapping has no FIR, ARTCC, or FAA operational-scope field, so a resolved state polygon is not operational airspace.",
        typed_identity=False,
        temporal=False,
        source_version=True,
        geographic=True,
        metric=False,
        coverage=False,
    ),
    _case(
        "unknown-polygon-provenance",
        "Unknown polygon provenance",
        "CENSUS_REGION_DATASET",
        "LIVE_OPS",
        "map_region_identity.dataset_sha256",
        "LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION",
        _NOT,
        (
            "provenance_authenticity",
            "dataset_version",
            "geometry_proof",
        ),
        "A polygon label can be copied while dataset digest and vintage are absent. "
        "Missing provenance is not filled in, and the label is not an authenticated operational boundary.",
        typed_identity=False,
        temporal=False,
        source_version=True,
        geographic=True,
        metric=False,
        coverage=False,
    ),
    _case(
        "missing-region-dataset-version",
        "Missing region dataset version",
        "CENSUS_REGION_DATASET",
        "LIVE_OPS",
        "region.dataset_vintage",
        "SourceRecord.source_version",
        _NOT,
        (
            "dataset_version",
            "source_version_equivalence",
            "provenance_authenticity",
        ),
        "When dataset vintage and digest are missing, the region source record keeps a null version and a null event time. "
        "A partial vintage-digest string is not a complete dataset version.",
        typed_identity=False,
        temporal=False,
        geographic=True,
        metric=False,
        coverage=False,
    ),
    _case(
        "moving-aircraft-at-geographic-boundary",
        "A moving aircraft at a geographic boundary",
        "LIVE_OPS",
        "CENSUS_REGION_DATASET",
        "LiveOpsPublicObservedField.LATITUDE",
        "map_region_identity.geometry_type",
        _UNSUPPORTED,
        (
            "geographic_scope_equivalence",
            "temporal_equivalence",
            "geometry_proof",
            "atomic_observation",
        ),
        "A static latitude, longitude, and track are separate observed fields. "
        "No contract infers a flight-path intersection from one position, including a position near a boundary.",
        typed_identity=True,
        source_version=False,
        metric=False,
        coverage=False,
        provenance=True,
    ),
)


REQUIRED_COMPARISON_CATEGORIES: tuple[str, ...] = tuple(
    case.comparison_category for case in COMPARABILITY_CASES[:20]
)

GEOGRAPHIC_COMPARISON_IDS: tuple[str, ...] = (
    "california-subset-vs-network-count",
    "missing-geometry-vs-claimed-intersection",
    "california-user-region-text",
    "census-geometry-vs-operational-airspace",
    "unknown-polygon-provenance",
    "missing-region-dataset-version",
    "same-aircraft-different-observation-times",
    "moving-aircraft-at-geographic-boundary",
)


def case_by_id(comparison_id: str) -> ComparabilityPrerequisite:
    """Return one catalog row. This does not compare operational facts."""

    matches = [item for item in COMPARABILITY_CASES if item.comparison_id == comparison_id]
    if len(matches) != 1:
        raise KeyError(comparison_id)
    return matches[0]


def _validate_catalog(cases: tuple[ComparabilityPrerequisite, ...]) -> None:
    identifiers = [item.comparison_id for item in cases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("comparison ids must be unique")
    if len(cases) < 20:
        raise ValueError("the catalog must contain at least 20 comparison cases")
    categories = [item.comparison_category for item in cases]
    if len(categories) != len(set(categories)):
        raise ValueError("comparison categories must be unique")
    for case in cases:
        if case.expected_eligibility.value in FORBIDDEN_COMPARISON_OUTCOMES:
            raise ValueError("forbidden comparison outcome in catalog")


_validate_catalog(COMPARABILITY_CASES)
