"""Deterministic Live Ops evidence verification.

Raw ToolResults are the factual authority. Model-facing projections are
used only to decide which rows the model could see. This module does not
render answers, call a model, score risk, or recompute geography.

ALL_VERIFIED means every submitted claim passed. It does not mean the
user question was fully answered or that every operational record was
evaluated. Mandatory source limitations stay attached either way.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
    FreshnessStatus,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
    _validate_bounded_text,
    _validate_string_tuple,
)
from wilvor_ai.live_ops_claims import (
    LIVE_OPS_MAX_CLAIMS_PER_RESPONSE,
    AircraftObservedStateClaim,
    AircraftProjectionStateClaim,
    AlertRecordClaim,
    CallsignMatchSetClaim,
    ConfirmedEncounterClaim,
    ConfirmedImpactClaim,
    ContextRootAbsentClaim,
    CoverageLimitationClaim,
    CrossToolContradictionClaim,
    CurrentnessClaim,
    HazardRegionIntersectionClaim,
    LiveOpsAdvisoryMode,
    LiveOpsClaim,
    LiveOpsContradictionSubjectKind,
    LiveOpsCountMetric,
    LiveOpsCountMode,
    LiveOpsCurrentnessPredicate,
    LiveOpsExecutedScope,
    LiveOpsLimitationCode,
    LiveOpsRiskMode,
    ModelVisibleTruncationClaim,
    QueryResultZeroClaim,
    RecommendationSetClaim,
    RiskStateClaim,
    ScopedCountClaim,
    is_live_ops_claim,
)
from wilvor_ai.live_ops_specialist_runtime_contracts import (
    LiveOpsDispatchResult,
    LiveOpsDispatchStatus,
    LiveOpsToolInvocation,
)
from wilvor_ai.live_ops_tool_projection import LiveOpsToolProjection


LIVE_OPS_VERIFICATION_SCHEMA_VERSION = "wilvor.ai.live_ops_verification.v1"

_NO_SNAPSHOT = (
    "DynamoDB reads are independently observed; this composition is not "
    "a transactional cross-table snapshot."
)
_EVENTUAL = "A recently written record may not yet be visible."
_RECOMMENDATION_ABSENCE = (
    "Absence of a recommendation is proven only within the observed ACTIVE, "
    "time-filtered candidate set."
)
_ALERT_ABSENCE = (
    "Absence of an alert is proven only within the observed active-state, "
    "time-filtered candidate set."
)
_CALLSIGN_CASE = (
    "Callsign discovery queries the GSI after strip+uppercase normalization "
    "to match Operational API query behavior. Stored callsign values are "
    "writer-controlled (strip-only) and DynamoDB GSI equality is case-sensitive. "
    "This is not case-insensitive or globally complete callsign coverage."
)
_QUERY_SELECTION = (
    "search_current_impacts requires region or hazard_ids; "
    "a worldwide impact scan is not performed."
)
_ENCOUNTER_SELECTION = (
    "search_current_encounters requires aircraft_id, callsign, "
    "hazard_id, or hazard_ids."
)
_CALLSIGN_REQUIRED = "find_aircraft_by_callsign requires a non-empty callsign."
_SPATIAL_UNSUPPORTED = (
    "Region identity resolved but spatial evaluation is unsupported for this "
    "vendored geometry representation."
)
_TEXT_TO_CODE = {
    "FRESHNESS_NOT_ESTABLISHED": LiveOpsLimitationCode.FRESHNESS_NOT_ESTABLISHED,
    "LINEAGE_MISMATCH_PRESENT": LiveOpsLimitationCode.LINEAGE_MISMATCH_PRESENT,
    "GEOSPATIAL_UNEVALUATED_PRESENT": LiveOpsLimitationCode.GEOSPATIAL_UNEVALUATED_PRESENT,
    "OPERATIONALLY_UNEVALUATED_PRESENT": (
        LiveOpsLimitationCode.OPERATIONALLY_UNEVALUATED_PRESENT
    ),
    "REGION_UNRESOLVED": LiveOpsLimitationCode.REGION_UNRESOLVED,
    "ALERT_EVIDENCE_IDENTITY_UNAVAILABLE": (
        LiveOpsLimitationCode.ALERT_EVIDENCE_IDENTITY_UNAVAILABLE
    ),
    _NO_SNAPSHOT: LiveOpsLimitationCode.NO_SNAPSHOT_LIMITATION,
    _EVENTUAL: LiveOpsLimitationCode.EVENTUAL_SCAN_LIMITATION,
    _RECOMMENDATION_ABSENCE: LiveOpsLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION,
    _ALERT_ABSENCE: LiveOpsLimitationCode.ALERT_ABSENCE_LIMITATION,
    _CALLSIGN_CASE: LiveOpsLimitationCode.CALLSIGN_CASE_LIMITATION,
    _QUERY_SELECTION: LiveOpsLimitationCode.QUERY_SELECTION_REQUIRED,
    _ENCOUNTER_SELECTION: LiveOpsLimitationCode.ENCOUNTER_SELECTION_REQUIRED,
    _CALLSIGN_REQUIRED: LiveOpsLimitationCode.CALLSIGN_REQUIRED,
    _SPATIAL_UNSUPPORTED: LiveOpsLimitationCode.SPATIAL_EVALUATION_UNSUPPORTED,
}
_ZERO_METRICS = frozenset(
    {
        LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
        LiveOpsCountMetric.IMPACT_RECORD_COUNT,
        LiveOpsCountMetric.ENCOUNTER_RECORD_COUNT,
        LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
    }
)
_CONTEXT_TOOLS = frozenset(
    {
        "get_aircraft_operational_context",
        "get_hazard_operational_context",
        "get_airport_operational_context",
    }
)
_ENCOUNTER_TOOLS = frozenset(
    {
        "search_current_impacts",
        "search_current_encounters",
        "get_aircraft_operational_context",
        "get_hazard_operational_context",
    }
)
_METRIC_TOOLS = {
    LiveOpsCountMetric.IMPACT_RECORD_COUNT: frozenset({"search_current_impacts"}),
    LiveOpsCountMetric.ENCOUNTER_RECORD_COUNT: frozenset({"search_current_encounters"}),
    LiveOpsCountMetric.UNIQUE_AIRCRAFT_COUNT: frozenset(
        {"search_current_impacts", "search_current_encounters"}
    ),
    LiveOpsCountMetric.UNIQUE_HAZARD_COUNT: frozenset(
        {
            "search_current_hazards",
            "search_current_impacts",
            "search_current_encounters",
        }
    ),
    LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT: frozenset({"search_current_hazards"}),
    LiveOpsCountMetric.CALLSIGN_MATCH_COUNT: frozenset({"find_aircraft_by_callsign"}),
    LiveOpsCountMetric.NETWORK_AIRCRAFT_COUNT: frozenset({"get_observed_network_state"}),
    LiveOpsCountMetric.NETWORK_HAZARD_COUNT: frozenset({"get_observed_network_state"}),
    LiveOpsCountMetric.NETWORK_ENCOUNTER_COUNT: frozenset({"get_observed_network_state"}),
    LiveOpsCountMetric.NETWORK_RISK_COUNT: frozenset({"get_observed_network_state"}),
    LiveOpsCountMetric.NETWORK_RECOMMENDATION_COUNT: frozenset(
        {"get_observed_network_state"}
    ),
    LiveOpsCountMetric.NETWORK_ALERT_COUNT: frozenset({"get_observed_network_state"}),
    LiveOpsCountMetric.HAZARD_CONTEXT_IMPACT_COUNT: frozenset(
        {"get_hazard_operational_context"}
    ),
    LiveOpsCountMetric.AIRCRAFT_CONTEXT_ENCOUNTER_COUNT: frozenset(
        {"get_aircraft_operational_context"}
    ),
}
_NETWORK_LIST = {
    LiveOpsCountMetric.NETWORK_AIRCRAFT_COUNT: "current_aircraft_ids",
    LiveOpsCountMetric.NETWORK_HAZARD_COUNT: "current_hazard_ids",
    LiveOpsCountMetric.NETWORK_ENCOUNTER_COUNT: "current_encounter_ids",
    LiveOpsCountMetric.NETWORK_RISK_COUNT: "current_risk_ids",
    LiveOpsCountMetric.NETWORK_RECOMMENDATION_COUNT: "current_recommendation_ids",
    LiveOpsCountMetric.NETWORK_ALERT_COUNT: "current_alert_ids",
}
_NETWORK_FIELD = {
    LiveOpsCountMetric.NETWORK_AIRCRAFT_COUNT: "current_aircraft_count",
    LiveOpsCountMetric.NETWORK_HAZARD_COUNT: "current_hazard_count",
    LiveOpsCountMetric.NETWORK_ENCOUNTER_COUNT: "current_encounter_count",
    LiveOpsCountMetric.NETWORK_RISK_COUNT: "current_risk_count",
    LiveOpsCountMetric.NETWORK_RECOMMENDATION_COUNT: "current_recommendation_count",
    LiveOpsCountMetric.NETWORK_ALERT_COUNT: "current_alert_count",
}
_AGGREGATE_FIELD = {
    LiveOpsCountMetric.IMPACT_RECORD_COUNT: "impact_count",
    LiveOpsCountMetric.ENCOUNTER_RECORD_COUNT: "encounter_count",
    LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT: "match_count",
    LiveOpsCountMetric.CALLSIGN_MATCH_COUNT: "match_count",
    LiveOpsCountMetric.UNIQUE_AIRCRAFT_COUNT: "unique_aircraft_count",
    LiveOpsCountMetric.HAZARD_CONTEXT_IMPACT_COUNT: None,
    LiveOpsCountMetric.AIRCRAFT_CONTEXT_ENCOUNTER_COUNT: None,
}


class LiveOpsClaimVerdict(str, Enum):
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONTRADICTED = "CONTRADICTED"


class LiveOpsVerificationOutcome(str, Enum):
    ALL_VERIFIED = "ALL_VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    NONE_VERIFIED = "NONE_VERIFIED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"


class LiveOpsRenderingPosture(str, Enum):
    """How LO3 may treat this result. LO2 does not render text.

    COMPLETE_VERIFIED_SET is the only posture in which every submitted
    claim verified. It is not a statement that the user question or the
    operational picture is complete.
    """

    COMPLETE_VERIFIED_SET = "COMPLETE_VERIFIED_SET"
    INCOMPLETE_VERIFIED_SUBSET = "INCOMPLETE_VERIFIED_SUBSET"
    NO_VERIFIED_FACTS = "NO_VERIFIED_FACTS"
    NOT_RENDERABLE = "NOT_RENDERABLE"


class LiveOpsClaimRejectionCode(str, Enum):
    UNKNOWN_TOOL_CALL_ID = "UNKNOWN_TOOL_CALL_ID"
    TOOL_NAME_MISMATCH = "TOOL_NAME_MISMATCH"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    IDENTITY_NOT_IN_EVIDENCE = "IDENTITY_NOT_IN_EVIDENCE"
    IDENTITY_NOT_MODEL_VISIBLE = "IDENTITY_NOT_MODEL_VISIBLE"
    MODEL_VISIBLE_SET_TRUNCATED = "MODEL_VISIBLE_SET_TRUNCATED"
    SOURCE_VERSION_MISMATCH = "SOURCE_VERSION_MISMATCH"
    STATUS_NOT_ELIGIBLE = "STATUS_NOT_ELIGIBLE"
    LINEAGE_MISMATCH = "LINEAGE_MISMATCH"
    INCONSISTENT_AGGREGATE = "INCONSISTENT_AGGREGATE"
    COUNT_MISMATCH = "COUNT_MISMATCH"
    COUNT_MODE_MISMATCH = "COUNT_MODE_MISMATCH"
    METRIC_TOOL_MISMATCH = "METRIC_TOOL_MISMATCH"
    ZERO_NOT_AUTHORIZED = "ZERO_NOT_AUTHORIZED"
    RETRIEVAL_LIMIT_PRESENT = "RETRIEVAL_LIMIT_PRESENT"
    VALUE_MISMATCH = "VALUE_MISMATCH"
    CURRENTNESS_MISMATCH = "CURRENTNESS_MISMATCH"
    SET_MISMATCH = "SET_MISMATCH"
    CALLSIGN_AMBIGUOUS = "CALLSIGN_AMBIGUOUS"
    LIMITATION_CODE_ABSENT = "LIMITATION_CODE_ABSENT"
    TRUNCATION_MISMATCH = "TRUNCATION_MISMATCH"
    CONTRADICTION_NOT_PRESENT = "CONTRADICTION_NOT_PRESENT"
    CONTRADICTED_SUBJECT = "CONTRADICTED_SUBJECT"
    SCOPE_RESULT_DISAGREEMENT = "SCOPE_RESULT_DISAGREEMENT"
    REGION_NOT_VERIFIED = "REGION_NOT_VERIFIED"
    SPATIAL_STATUS_MISMATCH = "SPATIAL_STATUS_MISMATCH"
    ALERT_IDENTITY_UNAVAILABLE = "ALERT_IDENTITY_UNAVAILABLE"
    IMPACTS_NOT_EVALUATED = "IMPACTS_NOT_EVALUATED"
    MISSING_RUN_CORRELATION = "MISSING_RUN_CORRELATION"
    RUN_SCOPE_MISMATCH = "RUN_SCOPE_MISMATCH"
    BATCH_CLOCK_MISMATCH = "BATCH_CLOCK_MISMATCH"
    DUPLICATE_TOOL_CALL_ID = "DUPLICATE_TOOL_CALL_ID"
    RUN_EXECUTION_FAILED = "RUN_EXECUTION_FAILED"
    INVALID_LEDGER = "INVALID_LEDGER"
    EMPTY_LEDGER = "EMPTY_LEDGER"
    EMPTY_CLAIM_SET = "EMPTY_CLAIM_SET"
    TOO_MANY_CLAIMS = "TOO_MANY_CLAIMS"
    MALFORMED_CLAIM = "MALFORMED_CLAIM"
    TOOL_RESULT_IDENTITY_MISMATCH = "TOOL_RESULT_IDENTITY_MISMATCH"
    NON_CURRENT_EVIDENCE = "NON_CURRENT_EVIDENCE"
    MISSING_PROJECTION = "MISSING_PROJECTION"


def _posture(outcome: LiveOpsVerificationOutcome) -> LiveOpsRenderingPosture:
    return {
        LiveOpsVerificationOutcome.ALL_VERIFIED: (
            LiveOpsRenderingPosture.COMPLETE_VERIFIED_SET
        ),
        LiveOpsVerificationOutcome.PARTIALLY_VERIFIED: (
            LiveOpsRenderingPosture.INCOMPLETE_VERIFIED_SUBSET
        ),
        LiveOpsVerificationOutcome.NONE_VERIFIED: LiveOpsRenderingPosture.NO_VERIFIED_FACTS,
        LiveOpsVerificationOutcome.NOT_AUTHORIZED: LiveOpsRenderingPosture.NOT_RENDERABLE,
    }[outcome]


@dataclass(frozen=True)
class LiveOpsTrustedEvidenceLedger:
    """Runtime evidence for one specialist run. There is no model parser."""

    correlation_id: str
    now_epoch: int
    dispatches: tuple[LiveOpsDispatchResult, ...]

    def __post_init__(self) -> None:
        errors: list[str] = []
        if not isinstance(self.correlation_id, str):
            errors.append("invalid_correlation_id")
        if type(self.now_epoch) is not int:
            errors.append("invalid_now_epoch")
        if not isinstance(self.dispatches, tuple) or any(
            not isinstance(item, LiveOpsDispatchResult) for item in self.dispatches
        ):
            errors.append("invalid_dispatches")
        if errors:
            raise ContractValidationError(errors)


@dataclass(frozen=True)
class LiveOpsClaimAssessment:
    """Typed verdict for one submitted claim.

    ``tool_call_ids`` is the evidence the claim cited. A scope disagreement
    does not replace that identity with the opposing read.

    ``conflicting_tool_call_ids`` lists the other tool calls in that
    disagreement, sorted, unique, and excluding the cited id. Lineage
    contradictions leave it empty and are recorded on
    ``contradiction_subjects`` instead.

    ``scope_disagreement_metrics`` and ``normalized_scope`` are typed
    selector metadata for the same disagreement. They contain no prose.
    """

    claim_index: int
    verdict: LiveOpsClaimVerdict
    codes: tuple[str, ...]
    tool_call_ids: tuple[str, ...]
    limitation_codes: tuple[str, ...]
    conflicting_tool_call_ids: tuple[str, ...] = ()
    scope_disagreement_metrics: tuple[str, ...] = ()
    normalized_scope: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if type(self.claim_index) is not int or self.claim_index < 0:
            raise ContractValidationError("invalid_claim_index")
        if not isinstance(self.verdict, LiveOpsClaimVerdict):
            raise ContractValidationError("invalid_verdict")
        errors = _validate_string_tuple(self.codes, "codes")
        errors.extend(_validate_string_tuple(self.tool_call_ids, "tool_call_ids"))
        errors.extend(_validate_string_tuple(self.limitation_codes, "limitation_codes"))
        errors.extend(
            _validate_string_tuple(self.conflicting_tool_call_ids, "conflicting_tool_call_ids")
        )
        errors.extend(
            _validate_string_tuple(self.scope_disagreement_metrics, "scope_disagreement_metrics")
        )
        if not isinstance(self.normalized_scope, tuple) or any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not all(isinstance(part, str) and part.strip() for part in item)
            for item in self.normalized_scope
        ):
            errors.append("invalid_normalized_scope")
        if set(self.conflicting_tool_call_ids) & set(self.tool_call_ids):
            errors.append("conflicting_tool_call_repeats_cited_evidence")
        if len(self.conflicting_tool_call_ids) != len(set(self.conflicting_tool_call_ids)):
            errors.append("duplicate_conflicting_tool_call_id")
        errors.extend(_assessment_consistency_errors(self))
        if errors:
            raise ContractValidationError(errors)


@dataclass(frozen=True)
class LiveOpsContradictionSubject:
    subject: LiveOpsContradictionSubjectKind
    identity: tuple[tuple[str, str], ...]
    tool_call_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.subject, LiveOpsContradictionSubjectKind):
            raise ContractValidationError("invalid_contradiction_subject")
        if not isinstance(self.identity, tuple) or any(
            not isinstance(item, tuple) or len(item) != 2 or not all(isinstance(part, str) for part in item)
            for item in self.identity
        ):
            raise ContractValidationError("invalid_contradiction_identity")
        errors = _validate_string_tuple(self.tool_call_ids, "tool_call_ids")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject.value,
            "identity": [list(item) for item in self.identity],
            "tool_call_ids": list(self.tool_call_ids),
        }


@dataclass(frozen=True)
class LiveOpsVerificationResult:
    """Typed verification artifact. Not a user-facing answer.

    ``ALL_VERIFIED`` means every submitted claim passed verification.
    It does not mean the original question was completely answered or
    that all relevant operational data was evaluated.
    """

    outcome: LiveOpsVerificationOutcome
    rendering_posture: LiveOpsRenderingPosture
    assessments: tuple[LiveOpsClaimAssessment, ...]
    verified_claims: tuple[LiveOpsClaim, ...]
    mandatory_limitations: tuple[str, ...]
    structural_codes: tuple[str, ...]
    contradiction_subjects: tuple[LiveOpsContradictionSubject, ...]
    audit_tool_call_ids: tuple[str, ...] = ()
    schema_version: str = LIVE_OPS_VERIFICATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        errors: list[str] = []
        if self.schema_version != LIVE_OPS_VERIFICATION_SCHEMA_VERSION:
            errors.append("invalid_schema_version")
        if not isinstance(self.outcome, LiveOpsVerificationOutcome):
            errors.append("invalid_outcome")
        if not isinstance(self.rendering_posture, LiveOpsRenderingPosture):
            errors.append("invalid_rendering_posture")
        elif (
            isinstance(self.outcome, LiveOpsVerificationOutcome)
            and self.rendering_posture is not _posture(self.outcome)
        ):
            errors.append("rendering_posture_mismatch")
        if not isinstance(self.assessments, tuple) or any(
            not isinstance(item, LiveOpsClaimAssessment) for item in self.assessments
        ):
            errors.append("invalid_assessments")
        if not isinstance(self.verified_claims, tuple) or any(
            not is_live_ops_claim(item) for item in self.verified_claims
        ):
            errors.append("invalid_verified_claims")
        if not isinstance(self.contradiction_subjects, tuple) or any(
            not isinstance(item, LiveOpsContradictionSubject)
            for item in self.contradiction_subjects
        ):
            errors.append("invalid_contradiction_subjects")
        errors.extend(_validate_string_tuple(self.mandatory_limitations, "mandatory_limitations"))
        errors.extend(_validate_string_tuple(self.structural_codes, "structural_codes"))
        errors.extend(_validate_string_tuple(self.audit_tool_call_ids, "audit_tool_call_ids"))
        if isinstance(self.outcome, LiveOpsVerificationOutcome):
            if self.outcome is LiveOpsVerificationOutcome.NOT_AUTHORIZED:
                if self.verified_claims or self.assessments:
                    errors.append("not_authorized_forbids_verified_claims")
                if not self.structural_codes:
                    errors.append("not_authorized_requires_structural_codes")
            elif self.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED:
                if not self.verified_claims:
                    errors.append("all_verified_requires_claims")
                if self.structural_codes:
                    errors.append("verified_forbids_structural_codes")
            elif self.verified_claims and self.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED:
                errors.append("none_verified_forbids_claims")
            errors.extend(_result_consistency_errors(self))
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "outcome": self.outcome.value,
            "rendering_posture": self.rendering_posture.value,
            "assessments": [
                {
                    "claim_index": item.claim_index,
                    "verdict": item.verdict.value,
                    "codes": list(item.codes),
                    "tool_call_ids": list(item.tool_call_ids),
                    "limitation_codes": list(item.limitation_codes),
                    "conflicting_tool_call_ids": list(item.conflicting_tool_call_ids),
                    "scope_disagreement_metrics": list(item.scope_disagreement_metrics),
                    "normalized_scope": [list(pair) for pair in item.normalized_scope],
                }
                for item in self.assessments
            ],
            "verified_claims": [item.to_dict() for item in self.verified_claims],
            "mandatory_limitations": list(self.mandatory_limitations),
            "structural_codes": list(self.structural_codes),
            "contradiction_subjects": [item.to_dict() for item in self.contradiction_subjects],
            "audit_tool_call_ids": list(self.audit_tool_call_ids),
        }


def _assessment_consistency_errors(assessment: "LiveOpsClaimAssessment") -> list[str]:
    """Disagreement metadata has to match the verdict and the rejection code.

    A verified assessment carries no active conflict. Scope-disagreement
    metadata is present only on a contradicted assessment that cites that
    code. Lineage contradictions stay on ``contradiction_subjects`` and do
    not borrow scope-disagreement fields.
    """

    errors: list[str] = []
    disagreement = LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value
    lineage = LiveOpsClaimRejectionCode.CONTRADICTED_SUBJECT.value
    metadata = bool(
        assessment.conflicting_tool_call_ids
        or assessment.scope_disagreement_metrics
        or assessment.normalized_scope
    )
    if assessment.verdict is LiveOpsClaimVerdict.VERIFIED and metadata:
        errors.append("verified_forbids_conflict_metadata")
    if assessment.verdict is LiveOpsClaimVerdict.VERIFIED and (
        disagreement in assessment.codes or lineage in assessment.codes
    ):
        errors.append("verified_forbids_contradiction_codes")
    if lineage in assessment.codes and assessment.verdict is not LiveOpsClaimVerdict.CONTRADICTED:
        errors.append("contradicted_subject_requires_contradicted")
    if disagreement in assessment.codes:
        if assessment.verdict is not LiveOpsClaimVerdict.CONTRADICTED:
            errors.append("scope_disagreement_requires_contradicted")
        if not assessment.conflicting_tool_call_ids:
            errors.append("scope_disagreement_requires_opposing_evidence")
        if not assessment.scope_disagreement_metrics:
            errors.append("scope_disagreement_requires_metric")
    elif metadata:
        errors.append("conflict_metadata_requires_scope_disagreement")
    return errors


def _result_consistency_errors(result: "LiveOpsVerificationResult") -> list[str]:
    """Outcome, assessments, and verified_claims have to describe one result.

    Rejected claims are not retained on the result, so this check can prove
    that the verified-claim count matches the verified assessments. It cannot
    prove which rejected claim object was omitted.
    """

    if result.outcome is LiveOpsVerificationOutcome.NOT_AUTHORIZED:
        return []
    if not isinstance(result.assessments, tuple) or any(
        not isinstance(item, LiveOpsClaimAssessment) for item in result.assessments
    ):
        return []
    errors: list[str] = []
    verified_assessments = [
        item for item in result.assessments if item.verdict is LiveOpsClaimVerdict.VERIFIED
    ]
    if len(result.verified_claims) != len(verified_assessments):
        errors.append("verified_claims_must_match_assessments")
    if result.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED:
        if any(item.verdict is not LiveOpsClaimVerdict.VERIFIED for item in result.assessments):
            errors.append("all_verified_requires_verified_assessments")
    elif result.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED:
        if verified_assessments:
            errors.append("none_verified_forbids_verified_assessments")
    elif result.outcome is LiveOpsVerificationOutcome.PARTIALLY_VERIFIED:
        if not verified_assessments or len(verified_assessments) == len(result.assessments):
            errors.append("partially_verified_requires_mixed_assessments")
    return errors


@dataclass(frozen=True)
class _Bound:
    invocation: LiveOpsToolInvocation
    raw: ToolResult
    executed: dict[str, Any]
    projection: LiveOpsToolProjection

    @property
    def data(self) -> Mapping[str, Any]:
        value = self.raw.data
        if isinstance(value, Mapping):
            return value
        return {}


def _codes(*items: str) -> tuple[str, ...]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return tuple(sorted(seen))


def _rows(data: Mapping[str, Any], name: str) -> list[Mapping[str, Any]]:
    value = data.get(name)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip() and value == value.strip():
        return value
    return None


def _exact_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _same_value(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if isinstance(left, float) or isinstance(right, float):
        return type(left) is type(right) and left == right
    return left == right and type(left) is type(right)


def _canonical_region(value: str) -> str:
    """LO1 region identity: trim, collapse internal whitespace, and casefold.

    This does not resolve aliases. ``CA`` and ``California`` stay distinct.
    """

    return " ".join(value.split()).casefold()


def _scope_matches(scope: LiveOpsExecutedScope, executed: Mapping[str, Any]) -> bool:
    expected = scope.to_dict()
    if set(expected) != set(executed):
        return False
    for key, value in expected.items():
        actual = executed.get(key)
        if key == "hazard_ids":
            if (
                not isinstance(actual, list)
                or not all(isinstance(item, str) for item in actual)
                or sorted(actual) != list(value)
            ):
                return False
        elif key == "region":
            if (
                not isinstance(actual, str)
                or not isinstance(value, str)
                or _canonical_region(actual) != _canonical_region(value)
            ):
                return False
        elif actual != value:
            return False
    return True


def _lineage_blocked(result: ToolResult) -> bool:
    return LiveOpsLimitationCode.LINEAGE_MISMATCH_PRESENT.value in result.limitations


def _retrieval_limited(data: Mapping[str, Any]) -> bool:
    """A zero is authorized only when no retrieval cap is in force.

    Absent metadata and an explicit null limit mean unrestricted. Any
    supplied limit value that is not null, including floats, booleans,
    and malformed entries, fails closed.
    """

    if "retrieval" not in data or data.get("retrieval") is None:
        return False
    retrieval = data.get("retrieval")
    if not isinstance(retrieval, list):
        return True
    for item in retrieval:
        if not isinstance(item, Mapping):
            return True
        if "limit" not in item or item.get("limit") is None:
            continue
        return True
    return False


def _limitation_codes(result: ToolResult) -> tuple[str, ...]:
    texts: list[str] = list(result.limitations)
    for evidence in result.evidence:
        texts.extend(evidence.limitations)
        if evidence.freshness_status is FreshnessStatus.UNKNOWN:
            texts.append("FRESHNESS_NOT_ESTABLISHED")
    data = result.data if isinstance(result.data, Mapping) else {}
    for item in _rows(data, "retrieval"):
        limitations = item.get("limitations")
        if isinstance(limitations, list):
            texts.extend(text for text in limitations if isinstance(text, str))
    for group_name in ("geospatial_unevaluated", "operationally_unevaluated"):
        for item in _rows(data, group_name):
            limitations = item.get("limitations")
            if isinstance(limitations, list):
                texts.extend(text for text in limitations if isinstance(text, str))
    codes = [_TEXT_TO_CODE[text].value for text in texts if text in _TEXT_TO_CODE]
    if _rows(data, "geospatial_unevaluated"):
        codes.append(LiveOpsLimitationCode.GEOSPATIAL_UNEVALUATED_PRESENT.value)
    if _rows(data, "operationally_unevaluated"):
        codes.append(LiveOpsLimitationCode.OPERATIONALLY_UNEVALUATED_PRESENT.value)
    region = data.get("region")
    if isinstance(region, Mapping):
        if region.get("resolved") is False:
            codes.append(LiveOpsLimitationCode.REGION_UNRESOLVED.value)
        if region.get("spatial_supported") is False:
            codes.append(LiveOpsLimitationCode.SPATIAL_EVALUATION_UNSUPPORTED.value)
    if result.tool_name == "get_observed_network_state" and data.get("complete_network_snapshot") is False:
        codes.append(LiveOpsLimitationCode.OBSERVED_INVENTORY_NOT_A_COMPLETE_SNAPSHOT.value)
    if not result.evidence and result.status is not ToolResultStatus.NOT_FOUND:
        codes.append(LiveOpsLimitationCode.FRESHNESS_NOT_ESTABLISHED.value)
    return _codes(*codes)


def _visible_rows(bound: _Bound, name: str) -> tuple[list[Mapping[str, Any]], bool]:
    data = bound.projection.data
    if not isinstance(data, Mapping):
        return [], True
    value = data.get(name)
    rows = [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []
    return rows, data.get(f"{name}_truncated") is True


def _impact_visible(bound: _Bound, row: Mapping[str, Any]) -> bool:
    visible, _truncated = _visible_rows(bound, "impacts")
    return any(
        item.get("encounter_id") == row.get("encounter_id")
        and item.get("aircraft_id") == row.get("aircraft_id")
        and item.get("hazard_id") == row.get("hazard_id")
        and item.get("source_version") == row.get("source_version")
        for item in visible
    )


def _encounter_visible(bound: _Bound, row: Mapping[str, Any]) -> bool:
    list_name = "impacts" if bound.raw.tool_name in {
        "search_current_impacts",
        "get_hazard_operational_context",
    } else "encounters"
    visible, _truncated = _visible_rows(bound, list_name)
    identity = _row_encounter_id(row)
    if identity is None:
        return False
    return any(_row_encounter_id(item) == identity for item in visible)


def _hazard_visible(bound: _Bound, hazard_id: str, source_version: str) -> bool:
    visible, _truncated = _visible_rows(bound, "hazards")
    return any(
        isinstance(item.get("hazard"), Mapping)
        and item["hazard"].get("hazard_id") == hazard_id
        and item["hazard"].get("source_version") == source_version
        for item in visible
    )


def _match_visible(bound: _Bound, aircraft_id: str) -> bool:
    visible, _truncated = _visible_rows(bound, "matches")
    return any(
        isinstance(item.get("aircraft"), Mapping)
        and item["aircraft"].get("aircraft_id") == aircraft_id
        for item in visible
    )


def _root_aircraft_visible(bound: _Bound, aircraft_id: str) -> bool:
    data = bound.projection.data
    if not isinstance(data, Mapping):
        return False
    if data.get("aircraft_id") != aircraft_id and not (
        isinstance(data.get("aircraft"), Mapping)
        and data["aircraft"].get("aircraft_id") == aircraft_id
    ):
        return False
    return True


def verify_live_ops_claims(
    claims: tuple[LiveOpsClaim, ...],
    ledger: LiveOpsTrustedEvidenceLedger,
) -> LiveOpsVerificationResult:
    """Verify claims against one trusted specialist-run ledger."""

    if not isinstance(ledger, LiveOpsTrustedEvidenceLedger):
        raise TypeError("ledger must be a LiveOpsTrustedEvidenceLedger")
    structural, bound, audit_ids = _authorize(ledger)
    if structural:
        return _not_authorized(structural, audit_ids, _audit_limitations(ledger))
    if (
        not isinstance(claims, tuple)
        or any(not is_live_ops_claim(item) for item in claims)
    ):
        return _not_authorized(
            (LiveOpsClaimRejectionCode.MALFORMED_CLAIM.value,),
            audit_ids,
            _audit_limitations(ledger),
        )
    if not claims:
        return _not_authorized(
            (LiveOpsClaimRejectionCode.EMPTY_CLAIM_SET.value,),
            audit_ids,
            _audit_limitations(ledger),
        )
    if len(claims) > LIVE_OPS_MAX_CLAIMS_PER_RESPONSE:
        return _not_authorized(
            (LiveOpsClaimRejectionCode.TOO_MANY_CLAIMS.value,),
            audit_ids,
            _audit_limitations(ledger),
        )

    conflicts = _contradictions(tuple(bound.values()))
    scope_conflicts = _scope_metric_conflicts(tuple(bound.values()))
    assessments: list[LiveOpsClaimAssessment] = []
    verified: list[LiveOpsClaim] = []
    for index, claim in enumerate(claims):
        verdict, codes, tool_ids, limits = _verify_claim(claim, bound)
        lineage_conflict = _claim_contradicted(claim, conflicts)
        scope_matches = _matching_scope_disagreements(claim, scope_conflicts)
        conflicting: tuple[str, ...] = ()
        metrics: tuple[str, ...] = ()
        scope_pairs: tuple[tuple[str, str], ...] = ()
        if verdict is LiveOpsClaimVerdict.VERIFIED and (lineage_conflict or scope_matches):
            verdict = LiveOpsClaimVerdict.CONTRADICTED
            extra: list[str] = []
            if lineage_conflict:
                extra.append(LiveOpsClaimRejectionCode.CONTRADICTED_SUBJECT.value)
            if scope_matches:
                extra.append(LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value)
                conflicting, metrics, scope_pairs = _disagreement_diagnostics(
                    tool_ids,
                    scope_matches,
                )
            codes = _codes(*codes, *extra)
            limits = ()
        if verdict is LiveOpsClaimVerdict.VERIFIED:
            verified.append(claim)
        assessments.append(
            LiveOpsClaimAssessment(
                claim_index=index,
                verdict=verdict,
                codes=codes,
                tool_call_ids=tool_ids,
                limitation_codes=limits,
                conflicting_tool_call_ids=conflicting,
                scope_disagreement_metrics=metrics,
                normalized_scope=scope_pairs,
            )
        )
    if verified and len(verified) == len(claims):
        outcome = LiveOpsVerificationOutcome.ALL_VERIFIED
    elif verified:
        outcome = LiveOpsVerificationOutcome.PARTIALLY_VERIFIED
    else:
        outcome = LiveOpsVerificationOutcome.NONE_VERIFIED
    mandatory = _codes(
        *(
            code
            for assessment in assessments
            for code in assessment.limitation_codes
        )
    )
    referenced = _codes(
        *(
            code
            for assessment in assessments
            for tool_id in assessment.tool_call_ids
            if tool_id in bound
            for code in _limitation_codes(bound[tool_id].raw)
        )
    )
    return LiveOpsVerificationResult(
        outcome=outcome,
        rendering_posture=_posture(outcome),
        assessments=tuple(assessments),
        verified_claims=tuple(verified),
        mandatory_limitations=_codes(*mandatory, *referenced),
        structural_codes=(),
        contradiction_subjects=conflicts,
        audit_tool_call_ids=audit_ids,
    )


def _not_authorized(
    codes: tuple[str, ...],
    audit_ids: tuple[str, ...],
    limitations: tuple[str, ...],
) -> LiveOpsVerificationResult:
    outcome = LiveOpsVerificationOutcome.NOT_AUTHORIZED
    return LiveOpsVerificationResult(
        outcome=outcome,
        rendering_posture=_posture(outcome),
        assessments=(),
        verified_claims=(),
        mandatory_limitations=limitations,
        structural_codes=codes,
        contradiction_subjects=(),
        audit_tool_call_ids=audit_ids,
    )


def _audit_ids(ledger: LiveOpsTrustedEvidenceLedger) -> tuple[str, ...]:
    ids: list[str] = []
    for dispatch in ledger.dispatches:
        for invocation in dispatch.invocations:
            if invocation.tool_call_id not in ids:
                ids.append(invocation.tool_call_id)
        for item in dispatch.planned_tool_call_ids:
            if item not in ids:
                ids.append(item)
    return tuple(ids)


def _audit_limitations(ledger: LiveOpsTrustedEvidenceLedger) -> tuple[str, ...]:
    codes: list[str] = []
    for dispatch in ledger.dispatches:
        for invocation in dispatch.invocations:
            if isinstance(invocation.raw_tool_result, ToolResult):
                codes.extend(_limitation_codes(invocation.raw_tool_result))
    return _codes(*codes)


def _authorize(
    ledger: LiveOpsTrustedEvidenceLedger,
) -> tuple[tuple[str, ...], dict[str, _Bound], tuple[str, ...]]:
    audit = _audit_ids(ledger)
    correlation_errors = _validate_bounded_text(
        ledger.correlation_id,
        "correlation_id",
        max_length=PROVENANCE_TOKEN_MAX_LENGTH,
    )
    if correlation_errors or ledger.correlation_id != ledger.correlation_id.strip():
        return (LiveOpsClaimRejectionCode.MISSING_RUN_CORRELATION.value,), {}, audit
    if not ledger.dispatches:
        return (LiveOpsClaimRejectionCode.EMPTY_LEDGER.value,), {}, audit
    if any(item.status is LiveOpsDispatchStatus.EXECUTION_FAILED for item in ledger.dispatches):
        return (LiveOpsClaimRejectionCode.RUN_EXECUTION_FAILED.value,), {}, audit

    seen: set[str] = set()
    bound: dict[str, _Bound] = {}
    for dispatch in ledger.dispatches:
        for invocation in dispatch.invocations:
            if invocation.tool_call_id in seen:
                return (LiveOpsClaimRejectionCode.DUPLICATE_TOOL_CALL_ID.value,), {}, audit
            seen.add(invocation.tool_call_id)
            raw = invocation.raw_tool_result
            if (
                raw.tool_call_id != invocation.tool_call_id
                or raw.tool_name != invocation.tool_name
            ):
                return (
                    (LiveOpsClaimRejectionCode.TOOL_RESULT_IDENTITY_MISMATCH.value,),
                    {},
                    audit,
                )
            if raw.temporal_scope is not TemporalScope.CURRENT:
                return (LiveOpsClaimRejectionCode.NON_CURRENT_EVIDENCE.value,), {}, audit
            if raw.correlation_id != ledger.correlation_id:
                return (LiveOpsClaimRejectionCode.RUN_SCOPE_MISMATCH.value,), {}, audit
            data = raw.data if isinstance(raw.data, Mapping) else {}
            if data.get("as_of_epoch") != ledger.now_epoch:
                return (LiveOpsClaimRejectionCode.BATCH_CLOCK_MISMATCH.value,), {}, audit
            if dispatch.status is not LiveOpsDispatchStatus.COMPLETED:
                continue
            if not isinstance(invocation.model_projection, LiveOpsToolProjection):
                return (LiveOpsClaimRejectionCode.MISSING_PROJECTION.value,), {}, audit
            projection = invocation.model_projection
            if (
                projection.tool_call_id != invocation.tool_call_id
                or projection.tool_name != invocation.tool_name
                or projection.tool_name != raw.tool_name
                or projection.status is not raw.status
                or projection.temporal_scope is not raw.temporal_scope
            ):
                return (
                    (LiveOpsClaimRejectionCode.TOOL_RESULT_IDENTITY_MISMATCH.value,),
                    {},
                    audit,
                )
            if (
                projection.as_of_epoch is not None
                and projection.as_of_epoch != ledger.now_epoch
            ):
                return (LiveOpsClaimRejectionCode.BATCH_CLOCK_MISMATCH.value,), {}, audit
            bound[invocation.tool_call_id] = _Bound(
                invocation=invocation,
                raw=raw,
                executed=dict(invocation.executed_arguments),
                projection=invocation.model_projection,
            )
    return (), bound, audit


def _add_version(
    buckets: dict[str, dict[str, set[str]]],
    hazard_id: Any,
    source_version: Any,
    tool_call_id: str,
) -> None:
    identity = _text(hazard_id)
    version = _text(source_version)
    if identity is None or version is None:
        return
    buckets.setdefault(identity, {}).setdefault(version, set()).add(tool_call_id)


def _add_bool(
    buckets: dict[str, dict[bool, set[str]]],
    aircraft_id: Any,
    current: Any,
    tool_call_id: str,
) -> None:
    identity = _text(aircraft_id)
    if identity is None or not isinstance(current, bool):
        return
    buckets.setdefault(identity, {}).setdefault(current, set()).add(tool_call_id)


@dataclass(frozen=True)
class _EncounterFacts:
    """Explicit identity values on one encounter row.

    Missing components stay empty. They are not filled from another row
    and they are not treated as a conflicting value.
    """

    encounter_id: str
    aircraft_ids: frozenset[str]
    hazard_ids: frozenset[str]
    versions: frozenset[str]
    projection_ids: frozenset[str]


def _parent_lineage_projection(row: Mapping[str, Any], nested: Mapping[str, Any]) -> frozenset[str]:
    """Projection id carried by a parent-shaped encounter row.

    The mapper stores the encounter's projection id on the nested encounter
    when the parent row has no projection slot. A missing parent projection
    id is not replaced by any other identity. A present parent token is the
    only value used, and a parent/nested disagreement never reaches here.
    """

    parent_projection = _text(row.get("projection_id"))
    if parent_projection is not None:
        return frozenset({parent_projection})
    nested_projection = _text(nested.get("projection_id"))
    if nested_projection is None:
        return frozenset()
    return frozenset({nested_projection})


def _encounter_facts(row: Mapping[str, Any], *, tool_name: str) -> _EncounterFacts | None:
    """Lineage facts for one current encounter row.

    Parent-shaped tools contribute a fact only when the parent encounter,
    aircraft, hazard, and source version are all usable tokens. Nested
    encounter fields do not fill those slots. Direct aircraft-context rows
    have no parent slot; their mapped encounter object is the authority.
    An explicit parent/nested disagreement contributes nothing.
    """

    if _nested_field_conflict(row) is not None:
        return None
    nested = _nested_encounter(row)
    if not isinstance(nested, Mapping):
        nested = {}
    if _parent_shaped(tool_name):
        encounter_id = _text(row.get("encounter_id"))
        aircraft_id = _text(row.get("aircraft_id"))
        hazard_id = _text(row.get("hazard_id"))
        source_version = _text(row.get("source_version"))
        if None in (encounter_id, aircraft_id, hazard_id, source_version):
            return None
        return _EncounterFacts(
            encounter_id=encounter_id,
            aircraft_ids=frozenset({aircraft_id}),
            hazard_ids=frozenset({hazard_id}),
            versions=frozenset({source_version}),
            projection_ids=_parent_lineage_projection(row, nested),
        )
    encounter_id = _text(nested.get("encounter_id"))
    aircraft_id = _text(nested.get("aircraft_id"))
    hazard_id = _text(nested.get("hazard_id"))
    source_version = _text(nested.get("hazard_source_version"))
    if None in (encounter_id, aircraft_id, hazard_id, source_version):
        return None
    projection_id = _text(nested.get("projection_id"))
    return _EncounterFacts(
        encounter_id=encounter_id,
        aircraft_ids=frozenset({aircraft_id}),
        hazard_ids=frozenset({hazard_id}),
        versions=frozenset({source_version}),
        projection_ids=frozenset({projection_id}) if projection_id is not None else frozenset(),
    )


def _iter_confirmed(data: Mapping[str, Any], tool_name: str) -> list[Mapping[str, Any]]:
    if tool_name in {"search_current_impacts", "get_hazard_operational_context"}:
        return _rows(data, "impacts")
    if tool_name in {"search_current_encounters", "get_aircraft_operational_context"}:
        return _rows(data, "encounters")
    return []


def _contradictions(bound_rows: tuple[_Bound, ...]) -> tuple[LiveOpsContradictionSubject, ...]:
    versions: dict[str, dict[str, set[str]]] = {}
    negatives: dict[tuple[str, str], set[str]] = {}
    currentness: dict[str, dict[bool, set[str]]] = {}
    encounters: dict[str, list[tuple[_EncounterFacts, str]]] = {}
    confirmed_ids: dict[str, set[str]] = {}
    unevaluated_ids: dict[str, set[str]] = {}
    for item in bound_rows:
        data = item.data
        tool_id = item.raw.tool_call_id
        tool_name = item.raw.tool_name
        if tool_name == "search_current_hazards":
            for row in _rows(data, "hazards"):
                hazard = row.get("hazard") if isinstance(row.get("hazard"), Mapping) else {}
                if row.get("hazard_is_current") is True:
                    _add_version(versions, hazard.get("hazard_id"), hazard.get("source_version"), tool_id)
        if tool_name == "get_hazard_operational_context" and data.get("found") is True:
            if data.get("hazard_is_current") is True:
                _add_version(versions, data.get("hazard_id"), data.get("source_version"), tool_id)
        if tool_name == "get_aircraft_operational_context" and data.get("found") is True:
            _add_bool(currentness, data.get("aircraft_id"), data.get("aircraft_is_current"), tool_id)
        for row in _iter_confirmed(data, tool_name):
            # Parent-shaped hazard versions come only from the parent row.
            # A missing parent source_version is not replaced by the nested
            # encounter, and an internal parent/nested disagreement is not a
            # version witness. Direct aircraft context keeps its own branch.
            if _parent_shaped(tool_name) and _nested_field_conflict(row) is None:
                if tool_name in {"search_current_impacts", "get_hazard_operational_context"}:
                    _add_version(versions, row.get("hazard_id"), row.get("source_version"), tool_id)
                context = row.get("encounter_context")
                if isinstance(context, Mapping) and context.get("encounter_is_current") is True:
                    _add_version(versions, row.get("hazard_id"), row.get("source_version"), tool_id)
            if isinstance(row.get("aircraft"), Mapping):
                _add_bool(currentness, row.get("aircraft_id"), row.get("aircraft_is_current"), tool_id)
            if tool_name == "get_aircraft_operational_context" and row.get("encounter_is_current") is True:
                if _nested_field_conflict(row) is None:
                    encounter = row.get("encounter") if isinstance(row.get("encounter"), Mapping) else {}
                    _add_version(
                        versions,
                        encounter.get("hazard_id"),
                        encounter.get("hazard_source_version"),
                        tool_id,
                    )
            facts = _encounter_facts(row, tool_name=tool_name)
            if facts is not None and _row_encounter_current(row):
                encounters.setdefault(facts.encounter_id, []).append((facts, tool_id))
                confirmed_ids.setdefault(facts.encounter_id, set()).add(tool_id)
        for row in _rows(data, "rejected_hazard_selections") + _rows(data, "rejected_hazard_candidates"):
            if row.get("reason") == "NOT_CURRENT":
                hazard_id = _text(row.get("hazard_id"))
                version = _text(row.get("discovered_source_version"))
                if hazard_id and version:
                    negatives.setdefault((hazard_id, version), set()).add(tool_id)
        for row in _rows(data, "operationally_unevaluated"):
            encounter_id = _text(row.get("encounter_id"))
            if encounter_id:
                unevaluated_ids.setdefault(encounter_id, set()).add(tool_id)
                confirmed_ids.setdefault(encounter_id, set())

    subjects: list[LiveOpsContradictionSubject] = []

    def add(subject: LiveOpsContradictionSubjectKind, identity: tuple[tuple[str, str], ...], tool_ids: set[str]) -> None:
        if len(tool_ids) < 1:
            return
        subjects.append(
            LiveOpsContradictionSubject(
                subject=subject,
                identity=identity,
                tool_call_ids=tuple(sorted(tool_ids)),
            )
        )

    for hazard_id, by_version in versions.items():
        if len(by_version) > 1:
            tool_ids = {tool_id for ids in by_version.values() for tool_id in ids}
            add(
                LiveOpsContradictionSubjectKind.HAZARD_SOURCE_VERSION,
                (("hazard_id", hazard_id),),
                tool_ids,
            )
        for version, tool_ids in by_version.items():
            denied = negatives.get((hazard_id, version), set())
            if denied:
                add(
                    LiveOpsContradictionSubjectKind.HAZARD_SOURCE_VERSION,
                    (("hazard_id", hazard_id), ("source_version", version)),
                    set(tool_ids) | denied,
                )
    for aircraft_id, by_value in currentness.items():
        if len(by_value) > 1:
            tool_ids = {tool_id for ids in by_value.values() for tool_id in ids}
            add(
                LiveOpsContradictionSubjectKind.AIRCRAFT_CURRENTNESS,
                (("aircraft_id", aircraft_id),),
                tool_ids,
            )
    for encounter_id, entries in encounters.items():
        aircraft_ids: set[str] = set()
        hazard_ids: set[str] = set()
        versions: set[str] = set()
        projection_ids: set[str] = set()
        tool_ids: set[str] = set()
        for facts, tool_id in entries:
            aircraft_ids.update(facts.aircraft_ids)
            hazard_ids.update(facts.hazard_ids)
            versions.update(facts.versions)
            projection_ids.update(facts.projection_ids)
            tool_ids.add(tool_id)
        if (
            len(aircraft_ids) > 1
            or len(hazard_ids) > 1
            or len(versions) > 1
            or len(projection_ids) > 1
        ):
            add(
                LiveOpsContradictionSubjectKind.ENCOUNTER_LINEAGE,
                (("encounter_id", encounter_id),),
                tool_ids,
            )
    for encounter_id, confirmed_tools in confirmed_ids.items():
        overlap = confirmed_tools & unevaluated_ids.get(encounter_id, set())
        both_sides = confirmed_tools and unevaluated_ids.get(encounter_id, set())
        if both_sides and (overlap or confirmed_tools != unevaluated_ids.get(encounter_id, set())):
            if confirmed_tools and unevaluated_ids.get(encounter_id):
                add(
                    LiveOpsContradictionSubjectKind.ENCOUNTER_LINEAGE,
                    (("encounter_id", encounter_id), ("evaluation", "confirmed_and_unevaluated")),
                    confirmed_tools | unevaluated_ids[encounter_id],
                )
    return tuple(subjects)


def _row_encounter_current(row: Mapping[str, Any]) -> bool:
    context = row.get("encounter_context")
    if isinstance(context, Mapping):
        return context.get("encounter_is_current") is True
    return row.get("encounter_is_current") is True


def _encounter_currentness(context: Mapping[str, Any] | None) -> str:
    """Return ok, not_current, or missing. Never treats absence as current."""

    if not isinstance(context, Mapping) or "encounter_is_current" not in context:
        return "missing"
    value = context.get("encounter_is_current")
    if value is True:
        return "ok"
    if value is False:
        return "not_current"
    return "missing"


def _parent_version_conflict(row: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    encounter = context.get("encounter") if isinstance(context.get("encounter"), Mapping) else {}
    outer = _text(row.get("source_version"))
    nested = _text(encounter.get("hazard_source_version"))
    return outer is not None and nested is not None and outer != nested


def _nested_conflict_code(
    record: Mapping[str, Any],
    expected: Mapping[str, str | None],
) -> LiveOpsClaimRejectionCode | None:
    for field, wanted in expected.items():
        actual = _text(record.get(field))
        if actual is None:
            continue
        if wanted is None or actual != wanted:
            if field == "hazard_source_version":
                return LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH
            return LiveOpsClaimRejectionCode.VALUE_MISMATCH
    return None


def _claim_contradicted(
    claim: LiveOpsClaim,
    conflicts: tuple[LiveOpsContradictionSubject, ...],
) -> bool:
    if isinstance(claim, CrossToolContradictionClaim):
        return False
    hazard_id = getattr(claim, "hazard_id", None)
    aircraft_id = getattr(claim, "aircraft_id", None)
    encounter_id = getattr(claim, "encounter_id", None)
    for conflict in conflicts:
        identity = dict(conflict.identity)
        if conflict.subject is LiveOpsContradictionSubjectKind.HAZARD_SOURCE_VERSION:
            if hazard_id and identity.get("hazard_id") == hazard_id and _asserts_hazard_version(claim):
                pinned = identity.get("source_version")
                claimed_version = getattr(claim, "source_version", None) or getattr(
                    claim, "hazard_source_version", None
                )
                if pinned is None or claimed_version == pinned:
                    return True
        if conflict.subject is LiveOpsContradictionSubjectKind.AIRCRAFT_CURRENTNESS:
            if aircraft_id and identity.get("aircraft_id") == aircraft_id and _asserts_aircraft_currentness(claim):
                return True
        if conflict.subject is LiveOpsContradictionSubjectKind.ENCOUNTER_LINEAGE:
            if encounter_id and identity.get("encounter_id") == encounter_id and _asserts_encounter(claim):
                return True
    return False


def _asserts_hazard_version(claim: LiveOpsClaim) -> bool:
    return isinstance(
        claim,
        (
            ConfirmedImpactClaim,
            ConfirmedEncounterClaim,
            HazardRegionIntersectionClaim,
            RiskStateClaim,
            RecommendationSetClaim,
            AlertRecordClaim,
        ),
    ) or (
        isinstance(claim, CurrentnessClaim)
        and claim.predicate
        in {
            LiveOpsCurrentnessPredicate.HAZARD_IS_CURRENT,
            LiveOpsCurrentnessPredicate.HAZARD_SELECTION_NOT_CURRENT,
        }
    )


def _asserts_aircraft_currentness(claim: LiveOpsClaim) -> bool:
    return isinstance(
        claim,
        (
            ConfirmedImpactClaim,
            ConfirmedEncounterClaim,
            AircraftObservedStateClaim,
            AircraftProjectionStateClaim,
            CallsignMatchSetClaim,
            RiskStateClaim,
        ),
    ) or (
        isinstance(claim, CurrentnessClaim)
        and claim.predicate is LiveOpsCurrentnessPredicate.AIRCRAFT_IS_CURRENT
    )


def _asserts_encounter(claim: LiveOpsClaim) -> bool:
    return isinstance(
        claim,
        (
            ConfirmedImpactClaim,
            ConfirmedEncounterClaim,
            RiskStateClaim,
            RecommendationSetClaim,
            AlertRecordClaim,
        ),
    ) or (
        isinstance(claim, CurrentnessClaim)
        and claim.predicate is LiveOpsCurrentnessPredicate.ENCOUNTER_IS_CURRENT
    )


def _verify_claim(
    claim: LiveOpsClaim,
    bound: dict[str, _Bound],
) -> tuple[LiveOpsClaimVerdict, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    if isinstance(claim, CrossToolContradictionClaim):
        return _verify_contradiction_claim(claim, bound)
    tool_call_id = claim.tool_call_id
    item = bound.get(tool_call_id)
    if item is None:
        return (
            LiveOpsClaimVerdict.REJECTED,
            (LiveOpsClaimRejectionCode.UNKNOWN_TOOL_CALL_ID.value,),
            (tool_call_id,),
            (),
        )
    expected_name = getattr(claim, "tool_name", None)
    if isinstance(claim, HazardRegionIntersectionClaim):
        expected_name = "search_current_hazards"
    if isinstance(claim, CallsignMatchSetClaim):
        expected_name = "find_aircraft_by_callsign"
    if expected_name != item.raw.tool_name:
        return _fail(LiveOpsClaimRejectionCode.TOOL_NAME_MISMATCH, item)
    if isinstance(claim, ConfirmedImpactClaim):
        return _verify_impact(claim, item)
    if isinstance(claim, ConfirmedEncounterClaim):
        return _verify_encounter(claim, item)
    if isinstance(claim, ScopedCountClaim):
        return _verify_count(claim, item)
    if isinstance(claim, QueryResultZeroClaim):
        return _verify_zero(claim, item)
    if isinstance(claim, ContextRootAbsentClaim):
        return _verify_absent(claim, item)
    if isinstance(claim, HazardRegionIntersectionClaim):
        return _verify_intersection(claim, item)
    if isinstance(claim, AircraftObservedStateClaim):
        return _verify_observed(claim, item)
    if isinstance(claim, AircraftProjectionStateClaim):
        return _verify_projection(claim, item)
    if isinstance(claim, CurrentnessClaim):
        return _verify_currentness(claim, item)
    if isinstance(claim, RiskStateClaim):
        return _verify_risk(claim, item)
    if isinstance(claim, RecommendationSetClaim):
        return _verify_recommendations(claim, item)
    if isinstance(claim, AlertRecordClaim):
        return _verify_alert(claim, item)
    if isinstance(claim, CallsignMatchSetClaim):
        return _verify_callsign(claim, item)
    if isinstance(claim, CoverageLimitationClaim):
        return _verify_coverage(claim, item)
    if isinstance(claim, ModelVisibleTruncationClaim):
        return _verify_truncation(claim, item)
    return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE, item)


def _fail(code: LiveOpsClaimRejectionCode, item: _Bound, verdict: LiveOpsClaimVerdict = LiveOpsClaimVerdict.REJECTED) -> tuple[LiveOpsClaimVerdict, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    limits = ()
    if verdict is not LiveOpsClaimVerdict.VERIFIED:
        limits = _limitation_codes(item.raw) if verdict is LiveOpsClaimVerdict.INSUFFICIENT_EVIDENCE else ()
    return verdict, (code.value,), (item.raw.tool_call_id,), limits


def _ok(item: _Bound, *extra: str) -> tuple[LiveOpsClaimVerdict, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    return (
        LiveOpsClaimVerdict.VERIFIED,
        (),
        (item.raw.tool_call_id,),
        _codes(*_limitation_codes(item.raw), *extra),
    )


def _insufficient(code: LiveOpsClaimRejectionCode, item: _Bound) -> tuple[LiveOpsClaimVerdict, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    return (
        LiveOpsClaimVerdict.INSUFFICIENT_EVIDENCE,
        (code.value,),
        (item.raw.tool_call_id,),
        _limitation_codes(item.raw),
    )


def _status_ok(item: _Bound, *, allow_partial: bool) -> LiveOpsClaimRejectionCode | None:
    status = item.raw.status
    if status is ToolResultStatus.SUCCESS:
        return None
    if allow_partial and status is ToolResultStatus.PARTIAL and not _lineage_blocked(item.raw):
        return None
    if status in {ToolResultStatus.UNKNOWN, ToolResultStatus.NOT_FOUND}:
        return LiveOpsClaimRejectionCode.STATUS_NOT_ELIGIBLE
    return LiveOpsClaimRejectionCode.STATUS_NOT_ELIGIBLE


def _find_impact(data: Mapping[str, Any], claim: ConfirmedImpactClaim) -> Mapping[str, Any] | None:
    for row in _rows(data, "impacts"):
        if (
            row.get("aircraft_id") == claim.aircraft_id
            and row.get("hazard_id") == claim.hazard_id
            and row.get("source_version") == claim.source_version
            and row.get("encounter_id") == claim.encounter_id
        ):
            return row
    return None


def _verify_impact(claim: ConfirmedImpactClaim, item: _Bound) -> tuple[LiveOpsClaimVerdict, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    if item.raw.tool_name == "get_hazard_operational_context":
        if item.data.get("found") is not True or item.data.get("current_impacts_evaluated") is not True:
            return _insufficient(LiveOpsClaimRejectionCode.IMPACTS_NOT_EVALUATED, item)
    if _lineage_blocked(item.raw):
        return _fail(LiveOpsClaimRejectionCode.LINEAGE_MISMATCH, item, LiveOpsClaimVerdict.CONTRADICTED)
    status_code = _status_ok(item, allow_partial=True)
    if status_code is not None:
        return _fail(status_code, item)
    row = _find_impact(item.data, claim)
    if row is None:
        return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE, item)
    if any(item_row.get("encounter_id") == claim.encounter_id for item_row in _rows(item.data, "operationally_unevaluated")):
        return _fail(LiveOpsClaimRejectionCode.CONTRADICTED_SUBJECT, item, LiveOpsClaimVerdict.CONTRADICTED)
    identity_conflict = _nested_field_conflict(row)
    if identity_conflict is not None:
        return _fail(identity_conflict, item)
    context = row.get("encounter_context")
    if isinstance(context, Mapping):
        encounter = context.get("encounter") if isinstance(context.get("encounter"), Mapping) else {}
        nested_version = encounter.get("hazard_source_version")
        outer_version = _text(row.get("source_version"))
        nested_text = _text(nested_version)
        if nested_text is not None and nested_text != claim.source_version:
            return _fail(LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH, item)
        if outer_version is not None and nested_text is not None and outer_version != nested_text:
            return _fail(LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH, item)
    currentness = _encounter_currentness(context if isinstance(context, Mapping) else None)
    if currentness == "missing":
        return _insufficient(LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH, item)
    if currentness == "not_current":
        return _fail(LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH, item)
    if not _impact_visible(item, row):
        return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_MODEL_VISIBLE, item)
    return _ok(item, LiveOpsLimitationCode.RECORDED_OVERLAP_NOT_PHYSICAL_ENTRY.value)


def _find_encounter_row(data: Mapping[str, Any], tool_name: str) -> list[Mapping[str, Any]]:
    if tool_name == "get_aircraft_operational_context":
        return _rows(data, "encounters")
    return _rows(data, "encounters")


# Impact and encounter search rows, and hazard-context impacts, carry the
# authoritative identity on the parent row. Aircraft operational context
# stores each encounter as the context object itself, so that list has no
# parent slot.
_PARENT_SHAPED_TOOLS = frozenset(
    {
        "search_current_impacts",
        "search_current_encounters",
        "get_hazard_operational_context",
    }
)


def _parent_shaped(tool_name: str) -> bool:
    return tool_name in _PARENT_SHAPED_TOOLS


def _direct_encounter_context(tool_name: str) -> bool:
    return tool_name == "get_aircraft_operational_context"


def _authoritative_identity(
    row: Mapping[str, Any],
    encounter: Mapping[str, Any],
    *,
    tool_name: str,
) -> tuple[str | None, str | None, str | None]:
    """Encounter, aircraft, and hazard ids for one source shape.

    Parent-shaped rows use only parent tokens. A blank, missing, or
    whitespace parent id stays missing and is not copied from the nested
    encounter. Direct aircraft-context rows have no parent slot; their
    nested encounter object is the mapped identity.
    """

    if _direct_encounter_context(tool_name):
        return (
            _text(encounter.get("encounter_id")),
            _text(encounter.get("aircraft_id")),
            _text(encounter.get("hazard_id")),
        )
    return (
        _text(row.get("encounter_id")),
        _text(row.get("aircraft_id")),
        _text(row.get("hazard_id")),
    )


def _verify_encounter(claim: ConfirmedEncounterClaim, item: _Bound) -> tuple[LiveOpsClaimVerdict, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    if item.raw.tool_name == "get_aircraft_operational_context" and item.data.get("found") is not True:
        return _insufficient(LiveOpsClaimRejectionCode.STATUS_NOT_ELIGIBLE, item)
    if _lineage_blocked(item.raw):
        return _fail(LiveOpsClaimRejectionCode.LINEAGE_MISMATCH, item, LiveOpsClaimVerdict.CONTRADICTED)
    status_code = _status_ok(item, allow_partial=True)
    if status_code is not None:
        return _fail(status_code, item)
    match = None
    rejected_version = False
    rejected_current = False
    missing_current = False
    missing_version = False
    identity_conflict: LiveOpsClaimRejectionCode | None = None
    for row in _find_encounter_row(item.data, item.raw.tool_name):
        context = row.get("encounter_context") if "encounter_context" in row else row
        if not isinstance(context, Mapping):
            continue
        encounter = context.get("encounter") if isinstance(context.get("encounter"), Mapping) else {}
        conflict = _nested_field_conflict(row)
        if conflict is not None and _claim_matches_conflicting_row(row, encounter, claim.encounter_id):
            identity_conflict = conflict
            continue
        encounter_id, aircraft_id, hazard_id = _authoritative_identity(
            row,
            encounter,
            tool_name=item.raw.tool_name,
        )
        if (
            encounter_id is None
            or aircraft_id is None
            or hazard_id is None
            or encounter_id != claim.encounter_id
            or aircraft_id != claim.aircraft_id
            or hazard_id != claim.hazard_id
        ):
            continue
        outer = _text(row.get("source_version"))
        nested = _text(encounter.get("hazard_source_version"))
        projection_id = encounter.get("projection_id")
        if "encounter_is_current" not in context or not isinstance(context.get("encounter_is_current"), bool):
            missing_current = True
            continue
        if context.get("encounter_is_current") is not True:
            rejected_current = True
            continue
        if _direct_encounter_context(item.raw.tool_name):
            # The context row has no parent source_version. The nested
            # encounter record is the mapped version authority.
            if nested is None:
                missing_version = True
                continue
            if nested != claim.hazard_source_version:
                rejected_version = True
                continue
        else:
            if outer is None or nested is None:
                missing_version = True
                continue
            if outer != nested or outer != claim.hazard_source_version:
                rejected_version = True
                continue
        if claim.projection_id is not None and projection_id != claim.projection_id:
            return _fail(LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH, item)
        match = (row, encounter, projection_id)
        break
    if match is None:
        if identity_conflict is not None:
            return _fail(identity_conflict, item)
        if rejected_version:
            return _fail(LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH, item)
        if rejected_current:
            return _fail(LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH, item)
        if missing_version:
            return _insufficient(LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH, item)
        if missing_current:
            return _insufficient(LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH, item)
        return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE, item)
    row, encounter, projection_id = match
    if any(item_row.get("encounter_id") == claim.encounter_id for item_row in _rows(item.data, "operationally_unevaluated")):
        return _fail(LiveOpsClaimRejectionCode.CONTRADICTED_SUBJECT, item, LiveOpsClaimVerdict.CONTRADICTED)
    for name, value in claim.recorded_flags:
        if not isinstance(encounter.get(name), bool) or encounter.get(name) is not value:
            return _fail(LiveOpsClaimRejectionCode.VALUE_MISMATCH, item)
    # Overlap statuses are writer-controlled recorded labels, not a closed
    # region-geometry enum. Authorization requires exact raw equality.
    for name, value in claim.overlap_statuses:
        if encounter.get(name) != value or encounter.get(name) is None:
            return _fail(LiveOpsClaimRejectionCode.VALUE_MISMATCH, item)
    if not _encounter_visible(item, row):
        return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_MODEL_VISIBLE, item)
    return _ok(item, LiveOpsLimitationCode.RECORDED_OVERLAP_NOT_PHYSICAL_ENTRY.value)


def _unique_ids(rows: list[Mapping[str, Any]], key: str, nested: str | None = None) -> int:
    values: set[str] = set()
    for row in rows:
        candidate = row.get(key)
        if not candidate and nested and isinstance(row.get(nested), Mapping):
            candidate = row[nested].get(key)
        if isinstance(candidate, str) and candidate:
            values.add(candidate)
    return len(values)


def _recompute(metric: LiveOpsCountMetric, item: _Bound) -> int | None:
    data = item.data
    tool_name = item.raw.tool_name
    if metric not in _METRIC_TOOLS.get(metric, frozenset()) and tool_name not in _METRIC_TOOLS[metric]:
        return None
    if tool_name not in _METRIC_TOOLS[metric]:
        return None
    if metric is LiveOpsCountMetric.IMPACT_RECORD_COUNT:
        return len(_rows(data, "impacts"))
    if metric is LiveOpsCountMetric.ENCOUNTER_RECORD_COUNT:
        return len(_rows(data, "encounters"))
    if metric is LiveOpsCountMetric.UNIQUE_AIRCRAFT_COUNT:
        if tool_name == "search_current_impacts":
            listed = [value for value in data.get("unique_aircraft_ids", []) if isinstance(value, str)]
            recomputed = _unique_ids(_rows(data, "impacts"), "aircraft_id")
            if "unique_aircraft_ids" in data and len(set(listed)) != recomputed:
                return None
            return recomputed
        return _unique_ids(_rows(data, "encounters"), "aircraft_id")
    if metric is LiveOpsCountMetric.UNIQUE_HAZARD_COUNT:
        if tool_name == "search_current_hazards":
            return _unique_ids(_rows(data, "hazards"), "hazard_id", "hazard")
        list_name = "impacts" if tool_name == "search_current_impacts" else "encounters"
        return _unique_ids(_rows(data, list_name), "hazard_id")
    if metric is LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT:
        return len(_rows(data, "hazards"))
    if metric is LiveOpsCountMetric.CALLSIGN_MATCH_COUNT:
        return len(_rows(data, "matches"))
    if metric in _NETWORK_LIST:
        values = data.get(_NETWORK_LIST[metric], [])
        if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
            return None
        if len(values) != len(set(values)):
            return None
        return len(values)
    if metric is LiveOpsCountMetric.HAZARD_CONTEXT_IMPACT_COUNT:
        return len(_rows(data, "impacts"))
    if metric is LiveOpsCountMetric.AIRCRAFT_CONTEXT_ENCOUNTER_COUNT:
        return len(_rows(data, "encounters"))
    return None


def _aggregate_conflict(metric: LiveOpsCountMetric, item: _Bound, recomputed: int) -> bool:
    """Stored aggregate authority. Absence is not rewritten from the rows.

    A missing field, a non-integer, or a value other than the recomputed row
    count means this read has no exact aggregate authority. Callers must not
    substitute ``recomputed`` and treat the stored aggregate as valid.
    """

    if metric is LiveOpsCountMetric.UNIQUE_AIRCRAFT_COUNT and item.raw.tool_name == "search_current_impacts":
        listed = item.data.get("unique_aircraft_ids")
        if isinstance(listed, list) and len([value for value in listed if isinstance(value, str)]) != recomputed:
            return True
    field_name = _NETWORK_FIELD.get(metric) or _AGGREGATE_FIELD.get(metric)
    if field_name is None:
        return False
    if field_name not in item.data:
        return True
    actual = _exact_int(item.data.get(field_name))
    return actual is None or actual != recomputed


def _verify_count(claim: ScopedCountClaim, item: _Bound) -> tuple[LiveOpsClaimVerdict, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    if item.raw.tool_name not in _METRIC_TOOLS[claim.metric]:
        return _fail(LiveOpsClaimRejectionCode.METRIC_TOOL_MISMATCH, item)
    if not _scope_matches(claim.scope, item.executed):
        return _fail(LiveOpsClaimRejectionCode.SCOPE_MISMATCH, item)
    if claim.metric in _NETWORK_LIST and claim.mode is not LiveOpsCountMode.EXACT_RETURNED_SET:
        return _fail(LiveOpsClaimRejectionCode.COUNT_MODE_MISMATCH, item)
    if claim.metric is LiveOpsCountMetric.HAZARD_CONTEXT_IMPACT_COUNT:
        if item.data.get("found") is not True or item.data.get("current_impacts_evaluated") is not True:
            return _insufficient(LiveOpsClaimRejectionCode.IMPACTS_NOT_EVALUATED, item)
    if claim.metric is LiveOpsCountMetric.AIRCRAFT_CONTEXT_ENCOUNTER_COUNT and item.data.get("found") is not True:
        return _insufficient(LiveOpsClaimRejectionCode.STATUS_NOT_ELIGIBLE, item)
    recomputed = _recompute(claim.metric, item)
    if recomputed is None:
        return _fail(LiveOpsClaimRejectionCode.INCONSISTENT_AGGREGATE, item)
    if _aggregate_conflict(claim.metric, item, recomputed):
        return _fail(LiveOpsClaimRejectionCode.INCONSISTENT_AGGREGATE, item)
    unevaluated = bool(_rows(item.data, "operationally_unevaluated") or _rows(item.data, "geospatial_unevaluated"))
    if _lineage_blocked(item.raw):
        return _fail(LiveOpsClaimRejectionCode.LINEAGE_MISMATCH, item, LiveOpsClaimVerdict.CONTRADICTED)
    if claim.mode is LiveOpsCountMode.EXACT_RETURNED_SET:
        if item.raw.status is not ToolResultStatus.SUCCESS or unevaluated:
            return _fail(LiveOpsClaimRejectionCode.COUNT_MODE_MISMATCH, item)
        # A source cap can hide further rows. Matching the returned length
        # does not prove the cap was large enough to be exhaustive.
        if _retrieval_limited(item.data):
            return _fail(LiveOpsClaimRejectionCode.RETRIEVAL_LIMIT_PRESENT, item)
        if (
            claim.metric in _ZERO_METRICS
            and _record_membership(item, claim.metric) is None
        ):
            return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE, item)
    elif item.raw.status is not ToolResultStatus.PARTIAL or not unevaluated:
        return _fail(LiveOpsClaimRejectionCode.COUNT_MODE_MISMATCH, item)
    if claim.value != recomputed:
        return _fail(LiveOpsClaimRejectionCode.COUNT_MISMATCH, item)
    return _ok(item)


def _selector_present(executed: Mapping[str, Any], *names: str) -> bool:
    return any(name in executed and executed[name] not in (None, "", []) for name in names)


def _verify_zero(claim: QueryResultZeroClaim, item: _Bound) -> tuple[LiveOpsClaimVerdict, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    if claim.metric not in _ZERO_METRICS or item.raw.tool_name not in _METRIC_TOOLS[claim.metric]:
        return _fail(LiveOpsClaimRejectionCode.METRIC_TOOL_MISMATCH, item)
    if not _scope_matches(claim.scope, item.executed):
        return _fail(LiveOpsClaimRejectionCode.SCOPE_MISMATCH, item)
    if item.raw.status is not ToolResultStatus.SUCCESS:
        return _fail(LiveOpsClaimRejectionCode.ZERO_NOT_AUTHORIZED, item)
    if _retrieval_limited(item.data):
        return _fail(LiveOpsClaimRejectionCode.RETRIEVAL_LIMIT_PRESENT, item)
    if _rows(item.data, "operationally_unevaluated") or _rows(item.data, "geospatial_unevaluated"):
        return _fail(LiveOpsClaimRejectionCode.ZERO_NOT_AUTHORIZED, item)
    if _lineage_blocked(item.raw):
        return _fail(LiveOpsClaimRejectionCode.LINEAGE_MISMATCH, item, LiveOpsClaimVerdict.CONTRADICTED)
    recomputed = _recompute(claim.metric, item)
    if recomputed is None or _aggregate_conflict(claim.metric, item, recomputed) or recomputed != 0:
        return _fail(LiveOpsClaimRejectionCode.ZERO_NOT_AUTHORIZED, item)
    region = item.data.get("region")
    if claim.metric is LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT and "region" in item.executed:
        if not isinstance(region, Mapping) or region.get("resolved") is not True:
            return _fail(LiveOpsClaimRejectionCode.ZERO_NOT_AUTHORIZED, item)
        if region.get("region_type") != "US_STATE" or region.get("spatial_supported") is not True:
            return _fail(LiveOpsClaimRejectionCode.ZERO_NOT_AUTHORIZED, item)
        if _rows(item.data, "rejected_hazard_candidates"):
            return _fail(LiveOpsClaimRejectionCode.ZERO_NOT_AUTHORIZED, item)
    elif claim.metric is LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT:
        if not _selector_present(item.executed, "hazard_ids"):
            return _fail(LiveOpsClaimRejectionCode.ZERO_NOT_AUTHORIZED, item)
        requested = item.executed.get("hazard_ids")
        rejected = {
            row.get("hazard_id"): row.get("reason")
            for row in _rows(item.data, "rejected_hazard_selections")
        }
        if not isinstance(requested, list) or set(requested) != set(rejected):
            return _fail(LiveOpsClaimRejectionCode.ZERO_NOT_AUTHORIZED, item)
        allowed_reasons = {"NOT_CURRENT", "MISSING", "NOT_RETURNED_BY_FILTERED_DISCOVERY"}
        if any(reason not in allowed_reasons for reason in rejected.values()):
            return _fail(LiveOpsClaimRejectionCode.ZERO_NOT_AUTHORIZED, item)
    elif claim.metric is LiveOpsCountMetric.IMPACT_RECORD_COUNT:
        if not _selector_present(item.executed, "region", "hazard_ids"):
            return _fail(LiveOpsClaimRejectionCode.ZERO_NOT_AUTHORIZED, item)
        if "region" in item.executed:
            if not isinstance(region, Mapping) or region.get("resolved") is not True or region.get("spatial_supported") is not True:
                return _fail(LiveOpsClaimRejectionCode.ZERO_NOT_AUTHORIZED, item)
        if _rows(item.data, "rejected_hazard_candidates") or _rows(item.data, "rejected_hazard_selections"):
            return _fail(LiveOpsClaimRejectionCode.ZERO_NOT_AUTHORIZED, item)
    elif claim.metric is LiveOpsCountMetric.ENCOUNTER_RECORD_COUNT:
        if not _selector_present(item.executed, "aircraft_id", "callsign", "hazard_id", "hazard_ids"):
            return _fail(LiveOpsClaimRejectionCode.ZERO_NOT_AUTHORIZED, item)
        if _rows(item.data, "rejected_hazard_selections"):
            return _fail(LiveOpsClaimRejectionCode.ZERO_NOT_AUTHORIZED, item)
    elif claim.metric is LiveOpsCountMetric.CALLSIGN_MATCH_COUNT:
        callsign = item.executed.get("callsign")
        if not isinstance(callsign, str) or not callsign.strip():
            return _fail(LiveOpsClaimRejectionCode.ZERO_NOT_AUTHORIZED, item)
    return _ok(item)


def _row_encounter_id(row: Mapping[str, Any]) -> str | None:
    direct = _text(row.get("encounter_id"))
    context = row.get("encounter_context")
    if not isinstance(context, Mapping):
        context = row
    encounter = context.get("encounter") if isinstance(context, Mapping) else None
    nested = _text(encounter.get("encounter_id")) if isinstance(encounter, Mapping) else None
    if direct is not None and nested is not None and direct != nested:
        return None
    if direct:
        return direct
    return nested


def _verify_absent(claim: ContextRootAbsentClaim, item: _Bound) -> tuple[LiveOpsClaimVerdict, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    if item.raw.status is not ToolResultStatus.NOT_FOUND or item.data.get("found") is not False:
        return _fail(LiveOpsClaimRejectionCode.STATUS_NOT_ELIGIBLE, item)
    field_name = {
        "get_aircraft_operational_context": "aircraft_id",
        "get_hazard_operational_context": "hazard_id",
        "get_airport_operational_context": "airport_id",
    }[item.raw.tool_name]
    if item.data.get(field_name) != claim.entity_id:
        return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE, item)
    if item.raw.evidence:
        return _fail(LiveOpsClaimRejectionCode.STATUS_NOT_ELIGIBLE, item)
    return _ok(item)


def _verify_intersection(claim: HazardRegionIntersectionClaim, item: _Bound) -> tuple[LiveOpsClaimVerdict, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    region = item.data.get("region")
    if not isinstance(region, Mapping) or region.get("resolved") is not True:
        return _insufficient(LiveOpsClaimRejectionCode.REGION_NOT_VERIFIED, item)
    if (
        region.get("region_type") != "US_STATE"
        or region.get("spatial_supported") is not True
        or region.get("geometry_type") not in {"Polygon", "MultiPolygon"}
        or region.get("code") != claim.region_code
        or region.get("name") != claim.region_name
        or region.get("query") != claim.region_query
    ):
        return _fail(LiveOpsClaimRejectionCode.REGION_NOT_VERIFIED, item)
    if item.executed.get("region") != claim.region_query:
        return _fail(LiveOpsClaimRejectionCode.SCOPE_MISMATCH, item)
    if _lineage_blocked(item.raw):
        return _fail(LiveOpsClaimRejectionCode.LINEAGE_MISMATCH, item, LiveOpsClaimVerdict.CONTRADICTED)
    status_code = _status_ok(item, allow_partial=True)
    if status_code is not None:
        return _fail(status_code, item)
    if any(row.get("hazard_id") == claim.hazard_id for row in _rows(item.data, "geospatial_unevaluated")):
        return _fail(LiveOpsClaimRejectionCode.SPATIAL_STATUS_MISMATCH, item)
    matched = False
    for row in _rows(item.data, "hazards"):
        hazard = row.get("hazard") if isinstance(row.get("hazard"), Mapping) else {}
        if hazard.get("hazard_id") == claim.hazard_id and hazard.get("source_version") == claim.source_version:
            if row.get("spatial_status") != "INTERSECTS":
                return _fail(LiveOpsClaimRejectionCode.SPATIAL_STATUS_MISMATCH, item)
            matched = True
            break
    if not matched:
        return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE, item)
    if not _hazard_visible(item, claim.hazard_id, claim.source_version):
        return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_MODEL_VISIBLE, item)
    return _ok(item)


def _aircraft_record(item: _Bound, aircraft_id: str) -> tuple[Mapping[str, Any] | None, bool | None, bool]:
    if item.raw.tool_name == "get_aircraft_operational_context":
        if item.data.get("found") is not True or item.data.get("aircraft_id") != aircraft_id:
            return None, None, False
        aircraft = item.data.get("aircraft")
        if not isinstance(aircraft, Mapping):
            return None, item.data.get("aircraft_is_current") if isinstance(item.data.get("aircraft_is_current"), bool) else None, True
        return aircraft, item.data.get("aircraft_is_current") if isinstance(item.data.get("aircraft_is_current"), bool) else None, True
    for list_name in ("impacts", "encounters"):
        for row in _rows(item.data, list_name):
            if row.get("aircraft_id") == aircraft_id and isinstance(row.get("aircraft"), Mapping):
                current = row.get("aircraft_is_current")
                return row["aircraft"], current if isinstance(current, bool) else None, _impact_visible(item, row) if list_name == "impacts" else _encounter_visible(item, row)
    return None, None, False


def _verify_observed(claim: AircraftObservedStateClaim, item: _Bound) -> tuple[LiveOpsClaimVerdict, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    aircraft, current, visible = _aircraft_record(item, claim.aircraft_id)
    if aircraft is None or current is None:
        return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE, item)
    if claim.retained_not_current is (current is True):
        return _fail(LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH, item)
    if claim.field.value not in aircraft or aircraft.get(claim.field.value) is None:
        return _insufficient(LiveOpsClaimRejectionCode.VALUE_MISMATCH, item)
    if not _same_value(aircraft.get(claim.field.value), claim.value):
        return _fail(LiveOpsClaimRejectionCode.VALUE_MISMATCH, item)
    if item.raw.tool_name == "get_aircraft_operational_context":
        visible = _root_aircraft_visible(item, claim.aircraft_id)
    if not visible:
        return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_MODEL_VISIBLE, item)
    return _ok(item)


def _link_projection_token(link: Mapping[str, Any], side: str) -> tuple[str | None, bool]:
    """Projection id recorded on one side of a mapped projection link.

    The second value is true when that side specifies ``projection_id``.
    A blank, missing, or repeated disagreeing token is not a usable id.
    """

    pairs = link.get(side)
    if not isinstance(pairs, list):
        return None, False
    token: str | None = None
    specified = False
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2 or pair[0] != "projection_id":
            continue
        specified = True
        candidate = _text(pair[1])
        if candidate is None or (token is not None and token != candidate):
            return None, True
        token = candidate
    if not specified:
        return None, False
    return token, True


def _projection_identity(
    projection: Mapping[str, Any],
    link: Any,
) -> tuple[str | None, str]:
    """Bind a hydrated projection document to the selected current projection.

    ``ok`` means the document id, the link's selected id, and the link's
    observed id are the same usable token. ``mismatch`` means those present
    tokens disagree. ``missing`` means a required token is absent or unusable.
    The observed id is never copied into a missing selected id.
    """

    if not isinstance(link, Mapping):
        return None, "missing"
    document_id = _text(projection.get("projection_id"))
    selected, selected_specified = _link_projection_token(link, "selected_identity")
    observed, observed_specified = _link_projection_token(link, "observed_identity")
    if document_id is None or not selected_specified or selected is None or not observed_specified or observed is None:
        return None, "missing"
    if len({document_id, selected, observed}) != 1:
        return None, "mismatch"
    return document_id, "ok"


def _projection_record(
    item: _Bound,
    aircraft_id: str,
    projection_id: str,
) -> tuple[Mapping[str, Any] | None, bool, str | None]:
    """Return the current projection document, its visibility, and any identity defect.

    ``mismatch`` is an explicit selected/observed/document disagreement.
    ``missing`` means the binding required to prove that document is incomplete.
    A coherent document for this aircraft is returned only when its bound id
    is the claimed id. Another aircraft's document is left untouched.
    """

    candidates: list[tuple[Mapping[str, Any], Mapping[str, Any] | None]] = []
    if item.raw.tool_name == "get_aircraft_operational_context" and item.data.get("aircraft_id") == aircraft_id:
        projection = item.data.get("projection")
        if isinstance(projection, Mapping):
            candidates.append((projection, None))
    for row in _rows(item.data, "impacts"):
        if row.get("aircraft_id") == aircraft_id and isinstance(row.get("projection"), Mapping):
            candidates.append((row["projection"], row))
    mismatch = False
    missing = False
    for projection, parent in candidates:
        if projection.get("aircraft_id") != aircraft_id:
            continue
        link = item.data.get("projection_link") if parent is None else parent.get("projection_link")
        agreed, status = _projection_identity(projection, link)
        document_id = _text(projection.get("projection_id"))
        selected = observed = None
        if isinstance(link, Mapping):
            selected, _specified = _link_projection_token(link, "selected_identity")
            observed, _observed_specified = _link_projection_token(link, "observed_identity")
        touches_claim = projection_id in {
            value for value in (document_id, selected, observed) if value is not None
        }
        if status == "ok" and agreed == projection_id:
            current = item.data.get("projection_is_current") if parent is None else parent.get("projection_is_current")
            if current is not True:
                continue
            visible = True if parent is None else _impact_visible(item, parent)
            if parent is None:
                visible = _root_aircraft_visible(item, aircraft_id)
            return projection, visible, None
        if status == "mismatch" and touches_claim:
            mismatch = True
        elif status == "missing" and (touches_claim or document_id is None):
            missing = True
    if mismatch:
        return None, False, "mismatch"
    if missing:
        return None, False, "missing"
    return None, False, None


def _verify_projection(claim: AircraftProjectionStateClaim, item: _Bound) -> tuple[LiveOpsClaimVerdict, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    projection, visible, problem = _projection_record(item, claim.aircraft_id, claim.projection_id)
    if problem == "missing":
        return _insufficient(LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH, item)
    if problem == "mismatch":
        return _fail(LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH, item)
    if projection is None:
        return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE, item)
    if claim.field.value not in projection or projection.get(claim.field.value) is None:
        return _insufficient(LiveOpsClaimRejectionCode.VALUE_MISMATCH, item)
    if not _same_value(projection.get(claim.field.value), claim.value):
        return _fail(LiveOpsClaimRejectionCode.VALUE_MISMATCH, item)
    if not visible:
        return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_MODEL_VISIBLE, item)
    return _ok(item)


def _verify_currentness(claim: CurrentnessClaim, item: _Bound) -> tuple[LiveOpsClaimVerdict, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    data = item.data
    predicate = claim.predicate
    if predicate is LiveOpsCurrentnessPredicate.AIRCRAFT_IS_CURRENT:
        if item.raw.tool_name == "get_aircraft_operational_context":
            if data.get("found") is not True:
                return _insufficient(LiveOpsClaimRejectionCode.STATUS_NOT_ELIGIBLE, item)
            if data.get("aircraft_id") != claim.aircraft_id or data.get("aircraft_is_current") is not claim.value:
                return _fail(LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH, item)
            if not _root_aircraft_visible(item, claim.aircraft_id or ""):
                return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_MODEL_VISIBLE, item)
            return _ok(item)
        _aircraft, current, visible = _aircraft_record(item, claim.aircraft_id or "")
        if current is None or not visible:
            return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE if current is None else LiveOpsClaimRejectionCode.IDENTITY_NOT_MODEL_VISIBLE, item)
        if current is not claim.value:
            return _fail(LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH, item)
        return _ok(item)
    if predicate is LiveOpsCurrentnessPredicate.HAZARD_IS_LIFECYCLE_ACTIVE:
        if item.raw.tool_name != "get_hazard_operational_context" or data.get("found") is not True:
            return _insufficient(LiveOpsClaimRejectionCode.STATUS_NOT_ELIGIBLE, item)
        if data.get("hazard_id") != claim.hazard_id or data.get("hazard_is_lifecycle_active") is not claim.value:
            return _fail(LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH, item)
        return _ok(item)
    if predicate is LiveOpsCurrentnessPredicate.HAZARD_IS_CURRENT:
        if item.raw.tool_name == "get_hazard_operational_context":
            if data.get("found") is not True:
                return _insufficient(LiveOpsClaimRejectionCode.STATUS_NOT_ELIGIBLE, item)
            if data.get("hazard_id") != claim.hazard_id or data.get("hazard_is_current") is not claim.value:
                return _fail(LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH, item)
            if claim.source_version is not None and data.get("source_version") != claim.source_version:
                return _fail(LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH, item)
            return _ok(item)
        if item.raw.tool_name == "search_current_hazards" and claim.value is True:
            if not _hazard_visible(item, claim.hazard_id or "", claim.source_version or ""):
                if claim.source_version is None:
                    return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE, item)
            for row in _rows(data, "hazards"):
                hazard = row.get("hazard") if isinstance(row.get("hazard"), Mapping) else {}
                if hazard.get("hazard_id") == claim.hazard_id and row.get("hazard_is_current") is True:
                    if claim.source_version is not None and hazard.get("source_version") != claim.source_version:
                        continue
                    if not _hazard_visible(item, claim.hazard_id or "", hazard.get("source_version")):
                        return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_MODEL_VISIBLE, item)
                    return _ok(item)
        return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE, item)
    if predicate is LiveOpsCurrentnessPredicate.CURRENT_IMPACTS_EVALUATED:
        if item.raw.tool_name != "get_hazard_operational_context" or data.get("found") is not True:
            return _insufficient(LiveOpsClaimRejectionCode.STATUS_NOT_ELIGIBLE, item)
        if data.get("hazard_id") != claim.hazard_id or data.get("current_impacts_evaluated") is not claim.value:
            return _fail(LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH, item)
        return _ok(item)
    if predicate is LiveOpsCurrentnessPredicate.AIRPORT_IS_CURRENT:
        if item.raw.tool_name != "get_airport_operational_context" or data.get("found") is not True:
            return _insufficient(LiveOpsClaimRejectionCode.STATUS_NOT_ELIGIBLE, item)
        if data.get("airport_id") != claim.airport_id or data.get("airport_is_current") is not claim.value:
            return _fail(LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH, item)
        return _ok(item)
    if predicate is LiveOpsCurrentnessPredicate.PROJECTION_IS_CURRENT:
        if item.raw.tool_name == "get_aircraft_operational_context":
            if data.get("found") is not True or data.get("aircraft_id") != claim.aircraft_id:
                return _insufficient(LiveOpsClaimRejectionCode.STATUS_NOT_ELIGIBLE, item)
            current_flag = data.get("projection_is_current")
            if not isinstance(current_flag, bool):
                return _insufficient(LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH, item)
            if current_flag is not claim.value:
                return _fail(LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH, item)
            link = data.get("projection_link") if isinstance(data.get("projection_link"), Mapping) else None
            projection = data.get("projection") if isinstance(data.get("projection"), Mapping) else None
            if claim.value is False:
                # False is the operational missing-projection link, not a
                # boolean flipped on a document whose identity does not bind.
                if (
                    not isinstance(link, Mapping)
                    or link.get("state") != "MISSING"
                    or projection is not None
                    or claim.projection_id is not None
                ):
                    return _insufficient(LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH, item)
            else:
                if projection is None or link is None:
                    return _insufficient(LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH, item)
                agreed, status = _projection_identity(projection, link)
                if status == "missing":
                    return _insufficient(LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH, item)
                if status == "mismatch":
                    return _fail(LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH, item)
                if claim.projection_id is not None and claim.projection_id != agreed:
                    return _fail(LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH, item)
            if not _root_aircraft_visible(item, claim.aircraft_id or ""):
                return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_MODEL_VISIBLE, item)
            return _ok(item)
        return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE, item)
    if predicate is LiveOpsCurrentnessPredicate.ENCOUNTER_IS_CURRENT:
        for row in _iter_confirmed(data, item.raw.tool_name):
            context = row.get("encounter_context") if isinstance(row.get("encounter_context"), Mapping) else row
            encounter = context.get("encounter") if isinstance(context, Mapping) and isinstance(context.get("encounter"), Mapping) else {}
            if _nested_field_conflict(row) is not None:
                if _claim_matches_conflicting_row(row, encounter, claim.encounter_id):
                    return _fail(
                        _nested_field_conflict(row) or LiveOpsClaimRejectionCode.VALUE_MISMATCH,
                        item,
                    )
                continue
            encounter_id, aircraft_id, hazard_id = _authoritative_identity(
                row,
                encounter,
                tool_name=item.raw.tool_name,
            )
            if (
                encounter_id != claim.encounter_id
                or aircraft_id != claim.aircraft_id
                or hazard_id != claim.hazard_id
            ):
                continue
            if _parent_shaped(item.raw.tool_name) and _text(row.get("source_version")) is None:
                return _insufficient(LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH, item)
            actual = context.get("encounter_is_current") if isinstance(context, Mapping) else None
            if actual is not claim.value:
                return _fail(LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH, item)
            if not _encounter_visible(item, row):
                return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_MODEL_VISIBLE, item)
            return _ok(item)
        return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE, item)
    if predicate is LiveOpsCurrentnessPredicate.HAZARD_SELECTION_NOT_CURRENT:
        if claim.value is not True:
            return _fail(LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH, item)
        for row in _rows(data, "rejected_hazard_selections"):
            if row.get("hazard_id") == claim.hazard_id and row.get("reason") == "NOT_CURRENT" and row.get("discovered_source_version") == claim.source_version:
                visible, _truncated = _visible_rows(item, "rejected_hazard_selections")
                if not any(entry.get("hazard_id") == claim.hazard_id for entry in visible):
                    return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_MODEL_VISIBLE, item)
                return _ok(item)
        return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE, item)
    return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE, item)


def _context_for(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    context = row.get("encounter_context")
    if isinstance(context, Mapping):
        return context
    if isinstance(row.get("encounter"), Mapping) or "encounter_is_current" in row:
        return row
    return None


def _find_context(
    item: _Bound,
    encounter_id: str,
    aircraft_id: str,
    hazard_id: str,
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None, tuple[str, LiveOpsClaimRejectionCode] | None]:
    saw_missing = False
    saw_not_current = False
    saw_version = False
    saw_missing_version = False
    saw_identity: LiveOpsClaimRejectionCode | None = None
    for row in _iter_confirmed(item.data, item.raw.tool_name):
        context = _context_for(row)
        if context is None:
            continue
        conflict = _nested_field_conflict(row)
        if conflict is not None and _claim_matches_conflicting_row(
            row,
            context.get("encounter") if isinstance(context.get("encounter"), Mapping) else {},
            encounter_id,
        ):
            saw_identity = conflict
            continue
        encounter = context.get("encounter") if isinstance(context.get("encounter"), Mapping) else {}
        row_encounter_id, row_aircraft_id, row_hazard_id = _authoritative_identity(
            row,
            encounter,
            tool_name=item.raw.tool_name,
        )
        if (
            row_encounter_id != encounter_id
            or row_aircraft_id != aircraft_id
            or row_hazard_id != hazard_id
        ):
            continue
        if _parent_shaped(item.raw.tool_name) and _text(row.get("source_version")) is None:
            saw_missing_version = True
            continue
        currentness = _encounter_currentness(context)
        if currentness == "missing":
            saw_missing = True
            continue
        if currentness == "not_current":
            saw_not_current = True
            continue
        if _parent_version_conflict(row, context):
            saw_version = True
            continue
        return context, row, None
    if saw_identity is not None:
        return None, None, ("fail", saw_identity)
    if saw_version:
        return None, None, ("fail", LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH)
    if saw_missing_version:
        return None, None, ("insufficient", LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH)
    if saw_not_current:
        return None, None, ("fail", LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH)
    if saw_missing:
        return None, None, ("insufficient", LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH)
    return None, None, ("fail", LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE)


def _parent_expected(claim: Any, row: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, str | None]:
    encounter = context.get("encounter") if isinstance(context.get("encounter"), Mapping) else {}
    versions = [
        value
        for value in (
            _text(row.get("source_version")),
            _text(encounter.get("hazard_source_version")),
        )
        if value is not None
    ]
    parent_version = versions[0] if len(set(versions)) == 1 else None
    return {
        "aircraft_id": claim.aircraft_id,
        "hazard_id": claim.hazard_id,
        "encounter_id": claim.encounter_id,
        "hazard_source_version": parent_version,
        "projection_id": _text(encounter.get("projection_id")),
    }


def _recommendation_ids(recommendations: Any) -> tuple[str, ...] | None:
    if not isinstance(recommendations, list):
        return None
    ids: list[str] = []
    for rec in recommendations:
        if not isinstance(rec, Mapping):
            return None
        recommendation_id = rec.get("recommendation_id")
        if recommendation_id is None:
            continue
        if not isinstance(recommendation_id, str) or recommendation_id != recommendation_id.strip() or not recommendation_id.strip():
            return None
        ids.append(recommendation_id)
    if len(ids) != len(set(ids)):
        return None
    return tuple(sorted(ids))


def _visible_parent(bound: _Bound, row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    list_name = "impacts" if bound.raw.tool_name in {
        "search_current_impacts",
        "get_hazard_operational_context",
    } else "encounters"
    visible, _truncated = _visible_rows(bound, list_name)
    identity = _row_encounter_id(row)
    if identity is None:
        return None
    for item in visible:
        if _row_encounter_id(item) == identity:
            return item
    return None


def _apply_parent_failure(item: _Bound, failure: tuple[str, LiveOpsClaimRejectionCode] | None):
    if failure is None:
        return None
    kind, code = failure
    if kind == "insufficient":
        return _insufficient(code, item)
    return _fail(code, item)


def _verify_risk(claim: RiskStateClaim, item: _Bound) -> tuple[LiveOpsClaimVerdict, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    context, row, failure = _find_context(item, claim.encounter_id, claim.aircraft_id, claim.hazard_id)
    parent_failure = _apply_parent_failure(item, failure)
    if parent_failure is not None:
        return parent_failure
    assert context is not None and row is not None
    if not _encounter_visible(item, row):
        return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_MODEL_VISIBLE, item)
    risk = context.get("risk") if isinstance(context.get("risk"), Mapping) else None
    link = context.get("risk_link") if isinstance(context.get("risk_link"), Mapping) else {}
    if claim.mode is LiveOpsRiskMode.PRESENT:
        if link.get("state") != "PRESENT" or risk is None:
            return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE, item)
        if risk.get("risk_id") != claim.risk_id or risk.get("encounter_id") != claim.encounter_id:
            return _fail(LiveOpsClaimRejectionCode.VALUE_MISMATCH, item)
        if risk.get("risk_level") != claim.risk_level or not _same_value(risk.get("risk_score"), claim.risk_score):
            return _fail(LiveOpsClaimRejectionCode.VALUE_MISMATCH, item)
        conflict = _nested_conflict_code(risk, _parent_expected(claim, row, context))
        if conflict is not None:
            return _fail(conflict, item)
        return _ok(item)
    if risk is not None or link.get("state") not in {"MISSING", "ABSENT_FROM_CURRENT_CANDIDATES", "NOT_CURRENT"}:
        return _fail(LiveOpsClaimRejectionCode.VALUE_MISMATCH, item)
    return _ok(item)


def _verify_recommendations(claim: RecommendationSetClaim, item: _Bound) -> tuple[LiveOpsClaimVerdict, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    context, row, failure = _find_context(item, claim.encounter_id, claim.aircraft_id, claim.hazard_id)
    parent_failure = _apply_parent_failure(item, failure)
    if parent_failure is not None:
        return parent_failure
    assert context is not None and row is not None
    if not _encounter_visible(item, row):
        return _fail(LiveOpsClaimRejectionCode.MODEL_VISIBLE_SET_TRUNCATED, item)
    recommendations = context.get("recommendations")
    ids = _recommendation_ids(recommendations if isinstance(recommendations, list) else [])
    if ids is None:
        return _fail(LiveOpsClaimRejectionCode.SET_MISMATCH, item)
    visible_parent = _visible_parent(item, row)
    if visible_parent is None:
        return _fail(LiveOpsClaimRejectionCode.MODEL_VISIBLE_SET_TRUNCATED, item)
    visible_context = visible_parent.get("encounter_context")
    if not isinstance(visible_context, Mapping):
        visible_context = visible_parent
    visible_ids = _recommendation_ids(visible_context.get("recommendations"))
    if visible_ids is None or visible_ids != ids:
        return _fail(LiveOpsClaimRejectionCode.MODEL_VISIBLE_SET_TRUNCATED, item)
    expected = _parent_expected(claim, row, context)
    for rec in recommendations if isinstance(recommendations, list) else []:
        if isinstance(rec, Mapping):
            conflict = _nested_conflict_code(rec, expected)
            if conflict is not None:
                return _fail(conflict, item)
    link = context.get("recommendation_link") if isinstance(context.get("recommendation_link"), Mapping) else {}
    if claim.mode is LiveOpsAdvisoryMode.PRESENT_SET:
        if ids != claim.recommendation_ids or link.get("state") != "PRESENT":
            return _fail(LiveOpsClaimRejectionCode.SET_MISMATCH, item)
        return _ok(item)
    if ids or link.get("state") != "ABSENT_FROM_CURRENT_CANDIDATES":
        return _fail(LiveOpsClaimRejectionCode.SET_MISMATCH, item)
    return _ok(item)


def _verify_alert(claim: AlertRecordClaim, item: _Bound) -> tuple[LiveOpsClaimVerdict, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    if LiveOpsLimitationCode.ALERT_EVIDENCE_IDENTITY_UNAVAILABLE.value in _limitation_codes(item.raw):
        return _fail(LiveOpsClaimRejectionCode.ALERT_IDENTITY_UNAVAILABLE, item)
    context, row, failure = _find_context(item, claim.encounter_id, claim.aircraft_id, claim.hazard_id)
    parent_failure = _apply_parent_failure(item, failure)
    if parent_failure is not None:
        return parent_failure
    assert context is not None and row is not None
    if not _encounter_visible(item, row):
        return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_MODEL_VISIBLE, item)
    alerts = context.get("alerts")
    if not isinstance(alerts, list) or any(not isinstance(alert, Mapping) for alert in alerts):
        return _fail(LiveOpsClaimRejectionCode.SET_MISMATCH, item)
    link = context.get("alert_link") if isinstance(context.get("alert_link"), Mapping) else {}
    expected = _parent_expected(claim, row, context)
    if claim.mode is LiveOpsAdvisoryMode.ABSENT_FROM_OBSERVED_CANDIDATES:
        if alerts or link.get("state") != "ABSENT_FROM_CURRENT_CANDIDATES":
            return _fail(LiveOpsClaimRejectionCode.SET_MISMATCH, item)
        return _ok(item)
    for alert in alerts:
        if alert.get("alert_id") != claim.alert_id:
            continue
        if alert.get("alert_state") != claim.alert_state:
            return _fail(LiveOpsClaimRejectionCode.VALUE_MISMATCH, item)
        if claim.alert_type is not None and alert.get("alert_type") != claim.alert_type:
            return _fail(LiveOpsClaimRejectionCode.VALUE_MISMATCH, item)
        conflict = _nested_conflict_code(alert, expected)
        if conflict is not None:
            return _fail(conflict, item)
        return _ok(item)
    return _fail(LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE, item)


def _match_aircraft_ids(rows: list[Any]) -> tuple[list[str] | None, LiveOpsClaimRejectionCode | None]:
    ids: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            return None, LiveOpsClaimRejectionCode.TRUNCATION_MISMATCH
        aircraft = row.get("aircraft") if isinstance(row.get("aircraft"), Mapping) else {}
        aircraft_id = aircraft.get("aircraft_id")
        if row.get("aircraft_is_current") is not True or not isinstance(aircraft_id, str) or not aircraft_id.strip():
            return None, LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH
        ids.append(aircraft_id)
    return ids, None


def _truncation_consistent(projection_data: Mapping[str, Any], name: str, raw_len: int, visible_len: int) -> bool:
    total = projection_data.get(f"{name}_total_count")
    visible_count = projection_data.get(f"{name}_visible_count")
    omitted = projection_data.get(f"{name}_omitted_count")
    truncated = projection_data.get(f"{name}_truncated")
    if type(total) is not int or type(visible_count) is not int or type(omitted) is not int:
        return False
    if not isinstance(truncated, bool):
        return False
    return (
        total == raw_len
        and visible_count == visible_len
        and omitted == raw_len - visible_len
        and truncated is (visible_len < raw_len)
    )


def _verify_callsign(claim: CallsignMatchSetClaim, item: _Bound) -> tuple[LiveOpsClaimVerdict, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    if item.raw.status is not ToolResultStatus.SUCCESS:
        return _fail(LiveOpsClaimRejectionCode.STATUS_NOT_ELIGIBLE, item)
    if _retrieval_limited(item.data):
        return _fail(LiveOpsClaimRejectionCode.RETRIEVAL_LIMIT_PRESENT, item)
    if item.executed.get("callsign") != claim.callsign:
        return _fail(LiveOpsClaimRejectionCode.SCOPE_MISMATCH, item)
    raw_matches = item.data.get("matches")
    projection_data = item.projection.data if isinstance(item.projection.data, Mapping) else None
    visible_matches = projection_data.get("matches") if isinstance(projection_data, Mapping) else None
    if not isinstance(raw_matches, list) or not isinstance(visible_matches, list) or not isinstance(projection_data, Mapping):
        return _fail(LiveOpsClaimRejectionCode.TRUNCATION_MISMATCH, item)
    raw_ids, raw_code = _match_aircraft_ids(raw_matches)
    if raw_code is not None or raw_ids is None:
        return _fail(raw_code or LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH, item)
    visible_ids, visible_code = _match_aircraft_ids(visible_matches)
    if visible_code is not None or visible_ids is None:
        return _fail(LiveOpsClaimRejectionCode.MODEL_VISIBLE_SET_TRUNCATED, item)
    if len(raw_ids) != len(set(raw_ids)) or len(visible_ids) != len(set(visible_ids)):
        return _fail(LiveOpsClaimRejectionCode.CALLSIGN_AMBIGUOUS, item)
    if len(visible_matches) != len(raw_matches) or tuple(sorted(visible_ids)) != tuple(sorted(raw_ids)):
        return _fail(LiveOpsClaimRejectionCode.MODEL_VISIBLE_SET_TRUNCATED, item)
    if not _truncation_consistent(projection_data, "matches", len(raw_matches), len(visible_matches)):
        return _fail(LiveOpsClaimRejectionCode.TRUNCATION_MISMATCH, item)
    if tuple(sorted(raw_ids)) != claim.aircraft_ids:
        return _fail(LiveOpsClaimRejectionCode.SET_MISMATCH, item)
    return _ok(item)


def _verify_coverage(claim: CoverageLimitationClaim, item: _Bound) -> tuple[LiveOpsClaimVerdict, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    if claim.code.value not in _limitation_codes(item.raw):
        return _fail(LiveOpsClaimRejectionCode.LIMITATION_CODE_ABSENT, item)
    return _ok(item)


def _verify_truncation(claim: ModelVisibleTruncationClaim, item: _Bound) -> tuple[LiveOpsClaimVerdict, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    raw_values = item.data.get(claim.list_name)
    data = item.projection.data if isinstance(item.projection.data, Mapping) else None
    visible_values = data.get(claim.list_name) if isinstance(data, Mapping) else None
    if not isinstance(raw_values, list) or not isinstance(visible_values, list) or not isinstance(data, Mapping):
        return _fail(LiveOpsClaimRejectionCode.TRUNCATION_MISMATCH, item)
    if not _truncation_consistent(data, claim.list_name, len(raw_values), len(visible_values)):
        return _fail(LiveOpsClaimRejectionCode.TRUNCATION_MISMATCH, item)
    if data.get(f"{claim.list_name}_truncated") is not True or claim.total_count != len(raw_values):
        return _fail(LiveOpsClaimRejectionCode.TRUNCATION_MISMATCH, item)
    return _ok(item)


_EXECUTED_SCOPE_FIELDS = frozenset(
    {
        "region",
        "product_type",
        "hazard_type",
        "hazard_ids",
        "aircraft_id",
        "callsign",
        "hazard_id",
    }
)


def _normalize_executed(executed: Mapping[str, Any]) -> tuple[tuple[str, Any], ...] | None:
    """Grouping key for one executed call.

    The returned tuple is a copy. The invocation's executed arguments stay
    in their original form for audit. Region text follows LO1's canonical
    tool-call rule. Hazard ids are ordered and are not deduplicated, so a
    repeated id cannot become a different valid selector.
    """

    normalized: list[tuple[str, Any]] = []
    for key in sorted(executed):
        value = executed[key]
        if key == "region" and isinstance(value, str):
            value = _canonical_region(value)
        elif isinstance(value, list):
            if not all(isinstance(item, str) for item in value):
                return None
            value = tuple(sorted(value))
        elif isinstance(value, dict):
            return None
        normalized.append((key, value))
    return tuple(normalized)


def _scope_identity(item: _Bound) -> tuple[Any, ...]:
    normalized = _normalize_executed(item.executed)
    if normalized is None:
        return (item.raw.tool_name, item.raw.tool_call_id)
    return (item.raw.tool_name, normalized)


def _executed_scope(executed: Mapping[str, Any]) -> LiveOpsExecutedScope | None:
    if any(key not in _EXECUTED_SCOPE_FIELDS for key in executed):
        return None
    payload: dict[str, Any] = {}
    for key, value in executed.items():
        if key == "region" and isinstance(value, str):
            payload[key] = _canonical_region(value)
        elif key == "hazard_ids":
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                return None
            payload[key] = sorted(value)
        else:
            payload[key] = value
    try:
        return LiveOpsExecutedScope.from_dict(payload)
    except ContractValidationError:
        return None


def _is_authoritative_zero(item: _Bound, metric: LiveOpsCountMetric) -> bool:
    if metric not in _ZERO_METRICS or item.raw.tool_name not in _METRIC_TOOLS[metric]:
        return False
    scope = _executed_scope(item.executed)
    if scope is None:
        return False
    claim = QueryResultZeroClaim(
        tool_call_id=item.raw.tool_call_id,
        tool_name=item.raw.tool_name,
        metric=metric,
        scope=scope,
    )
    verdict, _codes, _tool_ids, _limits = _verify_zero(claim, item)
    return verdict is LiveOpsClaimVerdict.VERIFIED


def _exact_evaluation(item: _Bound, metric: LiveOpsCountMetric) -> int | None:
    """Exact aggregate authority for one metric.

    The returned integer is accepted only when the stored aggregate matches
    the rows and those rows have readable membership. A mismatch returns
    None. This function does not rewrite the stored aggregate. A malformed
    identity is not turned into a count that can challenge an authoritative
    zero. Numeric zero joins only when QueryResultZero would accept the
    same read.
    """

    if not _exhaustive_candidate(item, metric):
        return None
    recomputed = _recompute(metric, item)
    if recomputed is None or _aggregate_conflict(metric, item, recomputed):
        return None
    if metric in _ZERO_METRICS and _record_membership(item, metric) is None:
        return None
    if recomputed == 0 and not _is_authoritative_zero(item, metric):
        return None
    return recomputed


def _exhaustive_candidate(item: _Bound, metric: LiveOpsCountMetric) -> bool:
    """A read that could be a complete evaluated set.

    Partial coverage, unevaluated rows, blocked lineage, and a retrieval cap
    all remove completeness. An inconsistent aggregate does not: the rows may
    still be compared. Model-visible truncation is not a source cap.
    """

    if item.raw.tool_name not in _METRIC_TOOLS[metric]:
        return False
    if item.raw.status is not ToolResultStatus.SUCCESS:
        return False
    if _rows(item.data, "operationally_unevaluated") or _rows(item.data, "geospatial_unevaluated"):
        return False
    if _lineage_blocked(item.raw) or _retrieval_limited(item.data):
        return False
    return True


@dataclass(frozen=True)
class _ScopeDisagreement:
    """Reads of one executed scope that do not agree.

    The record preserves the tool results that disagree. It does not select
    a winner and it does not assert that stored data is corrupt.
    """

    tool_name: str
    executed_scope: tuple[tuple[str, Any], ...]
    metric: str
    tool_call_ids: frozenset[str]


# Coverage and cross-tool claims describe a limitation or a recorded
# inconsistency. Truncation is not in this set: its total_count restates
# the cardinality of one side of a disputed result.
_EVIDENCE_POSTURE_CLAIMS = (
    CoverageLimitationClaim,
    CrossToolContradictionClaim,
)


def _nested_encounter(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    context = row.get("encounter_context")
    if isinstance(context, Mapping) and isinstance(context.get("encounter"), Mapping):
        return context["encounter"]
    encounter = row.get("encounter")
    if isinstance(encounter, Mapping):
        return encounter
    return None


def _claim_matches_conflicting_row(
    row: Mapping[str, Any],
    encounter: Mapping[str, Any],
    encounter_id: str,
) -> bool:
    """A parent/nested disagreement applies to either recorded identifier.

    Matching only the outer identifier would let the nested identifier
    satisfy the claim through the outer-or-nested fallback.
    """

    present = {
        value
        for value in (
            _text(row.get("encounter_id")),
            _text(encounter.get("encounter_id")),
        )
        if value is not None
    }
    return encounter_id in present


def _nested_field_conflict(row: Mapping[str, Any]) -> LiveOpsClaimRejectionCode | None:
    """Disagreeing parent and nested identities fail closed.

    A nested field that the mapper omitted stays unknown. It is not copied
    from the parent and it is not treated as a different value. When both
    sides are present they must be the same token.
    """

    nested = _nested_encounter(row)
    if nested is None:
        return None
    pairs = (
        ("encounter_id", "encounter_id", LiveOpsClaimRejectionCode.VALUE_MISMATCH),
        ("aircraft_id", "aircraft_id", LiveOpsClaimRejectionCode.VALUE_MISMATCH),
        ("hazard_id", "hazard_id", LiveOpsClaimRejectionCode.VALUE_MISMATCH),
        ("source_version", "hazard_source_version", LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH),
        ("projection_id", "projection_id", LiveOpsClaimRejectionCode.VALUE_MISMATCH),
    )
    for outer_key, nested_key, code in pairs:
        outer = _text(row.get(outer_key))
        inner = _text(nested.get(nested_key))
        if outer is not None and inner is not None and outer != inner:
            return code
    return None


def _relationship_identity(row: Mapping[str, Any], *, require_nested: bool) -> tuple | None:
    """Impact or encounter relationship identity from the raw row.

    The signature is the mandatory parent identity: aircraft, encounter,
    hazard, and parent source version. Missing mandatory fields fail closed.
    Duplicate rows stay in the multiset. An explicit parent/nested
    disagreement is unreadable. A missing nested field is not replaced with
    the parent value. Optional nested hazard_source_version is not part of
    this signature, so its absence is not a different record. Encounter reads
    still require that nested version before a signature is returned.
    Projection id is not part of this signature: a missing projection is not
    a different record, and two recorded projection ids remain an
    encounter-lineage contradiction.
    """

    if _nested_field_conflict(row) is not None:
        return None
    encounter_id = _text(row.get("encounter_id"))
    aircraft_id = _text(row.get("aircraft_id"))
    hazard_id = _text(row.get("hazard_id"))
    source_version = _text(row.get("source_version"))
    if None in (encounter_id, aircraft_id, hazard_id, source_version):
        return None
    nested = _nested_encounter(row)
    nested_version = _text(nested.get("hazard_source_version")) if nested is not None else None
    if require_nested and nested_version is None:
        return None
    return (
        ("aircraft_id", aircraft_id),
        ("encounter_id", encounter_id),
        ("hazard_id", hazard_id),
        ("source_version", source_version),
    )


def _identity_rows(data: Mapping[str, Any], name: str) -> list[Mapping[str, Any]] | None:
    """Every list entry must already be a record. Non-records are not dropped."""

    value = data.get(name)
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        return None
    return [item for item in value if isinstance(item, Mapping)]


def _record_membership(item: _Bound, metric: LiveOpsCountMetric) -> tuple | None:
    """Sorted multiset of raw record identities.

    ``None`` means the rows do not support a membership comparison. Callers
    must not treat that result as equivalent to another and must not fill
    missing identity fields from a sibling row.
    """

    if metric is LiveOpsCountMetric.CALLSIGN_MATCH_COUNT:
        rows = _identity_rows(item.data, "matches")
        if rows is None:
            return None
        identities: list[tuple] = []
        for row in rows:
            aircraft = row.get("aircraft")
            aircraft_id = (
                _text(aircraft.get("aircraft_id")) if isinstance(aircraft, Mapping) else None
            )
            if aircraft_id is None:
                return None
            identities.append((("aircraft_id", aircraft_id),))
        identities.sort()
        return tuple(identities)
    if metric is LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT:
        rows = _identity_rows(item.data, "hazards")
        if rows is None:
            return None
        identities = []
        for row in rows:
            hazard = row.get("hazard")
            if not isinstance(hazard, Mapping):
                return None
            hazard_id = _text(hazard.get("hazard_id"))
            source_version = _text(hazard.get("source_version"))
            if hazard_id is None or source_version is None:
                return None
            identities.append((("hazard_id", hazard_id), ("source_version", source_version)))
        identities.sort()
        return tuple(identities)
    if metric is LiveOpsCountMetric.IMPACT_RECORD_COUNT:
        rows = _identity_rows(item.data, "impacts")
        if rows is None:
            return None
        identities = []
        for row in rows:
            identity = _relationship_identity(row, require_nested=False)
            if identity is None:
                return None
            identities.append(identity)
        identities.sort()
        return tuple(identities)
    if metric is LiveOpsCountMetric.ENCOUNTER_RECORD_COUNT:
        rows = _identity_rows(item.data, "encounters")
        if rows is None:
            return None
        identities = []
        for row in rows:
            identity = _relationship_identity(row, require_nested=True)
            if identity is None:
                return None
            identities.append(identity)
        identities.sort()
        return tuple(identities)
    return None


def _has_grounded_row(item: _Bound, metric: LiveOpsCountMetric) -> bool:
    """True when at least one row has its own required identity.

    Sibling rows that are malformed do not erase this witness, and they do
    not donate fields to it.
    """

    if metric is LiveOpsCountMetric.CALLSIGN_MATCH_COUNT:
        rows = item.data.get("matches")
        if not isinstance(rows, list):
            return False
        return any(
            isinstance(row, Mapping)
            and isinstance(row.get("aircraft"), Mapping)
            and _text(row["aircraft"].get("aircraft_id")) is not None
            for row in rows
        )
    if metric is LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT:
        rows = item.data.get("hazards")
        if not isinstance(rows, list):
            return False
        return any(
            isinstance(row, Mapping)
            and isinstance(row.get("hazard"), Mapping)
            and _text(row["hazard"].get("hazard_id")) is not None
            and _text(row["hazard"].get("source_version")) is not None
            for row in rows
        )
    if metric is LiveOpsCountMetric.IMPACT_RECORD_COUNT:
        rows = item.data.get("impacts")
        if not isinstance(rows, list):
            return False
        return any(_relationship_identity(row, require_nested=False) is not None for row in rows if isinstance(row, Mapping))
    if metric is LiveOpsCountMetric.ENCOUNTER_RECORD_COUNT:
        rows = item.data.get("encounters")
        if not isinstance(rows, list):
            return False
        return any(_relationship_identity(row, require_nested=True) is not None for row in rows if isinstance(row, Mapping))
    return False


# Exhaustive membership whose required identity cannot be read. It is left
# out of complete-set comparison. Unknown membership is not proof that the
# set differs from a readable signature, and the missing fields are not
# repaired from another row.
_UNREADABLE_MEMBERSHIP = object()


def _is_positive_record_witness(item: _Bound, metric: LiveOpsCountMetric) -> bool:
    """A row can show that a record exists without proving a complete set.

    An aggregate mismatch or a retrieval cap does not hide a grounded row.
    Partial coverage may witness existence. A malformed identity is not
    repaired into a witness. This is not exact aggregate authority.
    """

    if item.raw.tool_name not in _METRIC_TOOLS[metric]:
        return False
    if item.raw.status not in {ToolResultStatus.SUCCESS, ToolResultStatus.PARTIAL}:
        return False
    if _lineage_blocked(item.raw):
        return False
    return _has_grounded_row(item, metric)


def _comparable_membership(item: _Bound, metric: LiveOpsCountMetric):
    """Complete-set membership, including a corrupt stored aggregate.

    ``None`` means the read is not an exhaustive candidate, or a corrupt
    aggregate is paired with unreadable identity and must not invent a
    conflict. ``_UNREADABLE_MEMBERSHIP`` means the read would otherwise be
    exhaustive, the aggregate agrees, and the identity is still unusable.
    That marker is excluded from comparison. It is not a different
    membership. A readable signature is returned even when the aggregate
    mismatches. Capped and partial reads are not exhaustive and are not
    returned.
    """

    if not _exhaustive_candidate(item, metric):
        return None
    recomputed = _recompute(metric, item)
    if recomputed is None:
        return None
    if recomputed == 0 and not _is_authoritative_zero(item, metric):
        return None
    signature = _record_membership(item, metric)
    if signature is None:
        if _aggregate_conflict(metric, item, recomputed):
            return None
        return _UNREADABLE_MEMBERSHIP
    return signature


def _scope_metric_conflicts(bound_rows: tuple[_Bound, ...]) -> tuple[_ScopeDisagreement, ...]:
    """Same-scope reads that cannot all be authorized.

    Three eligibility rules stay separate:

    * Exact aggregate authority (``_exact_evaluation``) is required before two
      stored counts can disagree. A corrupt or missing aggregate is never
      replaced with the row count.
    * A positive record witness (``_is_positive_record_witness``) can show
      that a grounded row exists. Aggregate mismatch and a retrieval cap do
      not hide it, so it can challenge an authoritative zero. It does not
      prove that the returned rows are the complete set.
    * Complete-set membership (``_comparable_membership``) compares exhaustive
      reads whose identities can be read. A corrupt aggregate does not exempt
      a readable membership. Identical membership does not become a
      disagreement merely because one aggregate is corrupt. Capped, partial,
      and unevaluated reads are not exhaustive and are not compared as
      complete sets. An unreadable identity is excluded from that comparison.
      It is not repaired into a signature, it is not a positive witness, and
      it does not by itself contradict a readable complete set or an
      authoritative zero.

    The disagreement names the tool calls. It does not say why the source
    store produced them and it does not select a winner.
    """

    groups: dict[tuple[Any, ...], list[_Bound]] = {}
    for item in bound_rows:
        groups.setdefault(_scope_identity(item), []).append(item)
    found: list[_ScopeDisagreement] = []
    for identity, items in groups.items():
        if len(items) < 2:
            continue
        executed_scope = identity[1]
        if not isinstance(executed_scope, tuple):
            continue
        for metric in _ZERO_METRICS:
            zeros = [item for item in items if _is_authoritative_zero(item, metric)]
            witnesses = [item for item in items if _is_positive_record_witness(item, metric)]
            exact: list[tuple[_Bound, int]] = []
            for item in items:
                count = _exact_evaluation(item, metric)
                if count is not None:
                    exact.append((item, count))
            readable: list[tuple[_Bound, Any]] = []
            for item in items:
                signature = _comparable_membership(item, metric)
                if signature is not None and signature is not _UNREADABLE_MEMBERSHIP:
                    readable.append((item, signature))
            readable_signatures = [signature for _item, signature in readable]
            membership_differs = len(readable) > 1 and len(set(readable_signatures)) > 1
            numeric_conflict = len({count for _item, count in exact}) > 1
            existence_conflict = bool(zeros) and bool(witnesses)
            if not (existence_conflict or numeric_conflict or membership_differs):
                continue
            participants: set[str] = set()
            if existence_conflict:
                participants.update(item.raw.tool_call_id for item in zeros)
                participants.update(item.raw.tool_call_id for item in witnesses)
            if numeric_conflict:
                participants.update(item.raw.tool_call_id for item, _count in exact)
            if membership_differs:
                participants.update(item.raw.tool_call_id for item, _signature in readable)
            if len(participants) < 2:
                continue
            found.append(
                _ScopeDisagreement(
                    tool_name=items[0].raw.tool_name,
                    executed_scope=executed_scope,
                    metric=metric.value,
                    tool_call_ids=frozenset(participants),
                )
            )
    return tuple(found)


def _matching_scope_disagreements(
    claim: LiveOpsClaim,
    disagreements: tuple[_ScopeDisagreement, ...],
) -> tuple[_ScopeDisagreement, ...]:
    """Disagreements that include the tool result this claim cites.

    Evidence-posture claims stay eligible. A truncation claim is not posture:
    its total count would otherwise certify one side of the disagreement.
    """

    if isinstance(claim, _EVIDENCE_POSTURE_CLAIMS) or not disagreements:
        return ()
    tool_id = getattr(claim, "tool_call_id", None)
    if not isinstance(tool_id, str):
        return ()
    return tuple(
        disagreement for disagreement in disagreements if tool_id in disagreement.tool_call_ids
    )


def _string_scope(executed_scope: tuple[tuple[str, Any], ...]) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for key, value in executed_scope:
        if isinstance(value, str) and value.strip():
            pairs.append((key, value))
        elif isinstance(value, tuple) and all(isinstance(item, str) and item.strip() for item in value):
            pairs.extend((key, item) for item in value)
        else:
            return ()
    return tuple(pairs)


def _disagreement_diagnostics(
    cited_ids: tuple[str, ...],
    matches: tuple[_ScopeDisagreement, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[tuple[str, str], ...]]:
    cited = set(cited_ids)
    opposing = tuple(
        sorted(
            {
                tool_id
                for disagreement in matches
                for tool_id in disagreement.tool_call_ids
                if tool_id not in cited
            }
        )
    )
    metrics = tuple(sorted({disagreement.metric for disagreement in matches}))
    return opposing, metrics, _string_scope(matches[0].executed_scope)


def _verify_contradiction_claim(
    claim: CrossToolContradictionClaim,
    bound: dict[str, _Bound],
) -> tuple[LiveOpsClaimVerdict, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    ids = tuple(sorted((claim.tool_call_id_a, claim.tool_call_id_b)))
    if claim.tool_call_id_a not in bound or claim.tool_call_id_b not in bound:
        return (
            LiveOpsClaimVerdict.REJECTED,
            (LiveOpsClaimRejectionCode.UNKNOWN_TOOL_CALL_ID.value,),
            ids,
            (),
        )
    conflicts = _contradictions((bound[claim.tool_call_id_a], bound[claim.tool_call_id_b]))
    identity = {
        "hazard_id": claim.hazard_id,
        "aircraft_id": claim.aircraft_id,
        "encounter_id": claim.encounter_id,
    }
    for conflict in conflicts:
        if conflict.subject is not claim.subject:
            continue
        conflict_identity = dict(conflict.identity)
        key = {
            LiveOpsContradictionSubjectKind.HAZARD_SOURCE_VERSION: "hazard_id",
            LiveOpsContradictionSubjectKind.AIRCRAFT_CURRENTNESS: "aircraft_id",
            LiveOpsContradictionSubjectKind.ENCOUNTER_LINEAGE: "encounter_id",
        }[claim.subject]
        if conflict_identity.get(key) == identity[key] and set(conflict.tool_call_ids) >= set(ids):
            limits = _codes(
                *_limitation_codes(bound[claim.tool_call_id_a].raw),
                *_limitation_codes(bound[claim.tool_call_id_b].raw),
            )
            return LiveOpsClaimVerdict.VERIFIED, (), ids, limits
    return (
        LiveOpsClaimVerdict.REJECTED,
        (LiveOpsClaimRejectionCode.CONTRADICTION_NOT_PRESENT.value,),
        ids,
        (),
    )

