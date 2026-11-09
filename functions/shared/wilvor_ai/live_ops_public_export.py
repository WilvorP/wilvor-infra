"""Deterministic export of LO2 verification into Public Interface v1 DTOs.

LO2 is the only authority for whether a claim is verified. This module
copies that decision into the frozen public types. It does not render
prose, execute tools, score risk, or recompute hazard geometry.

Two LO2 contradiction identities cannot be represented by public v1
without dropping a component. Those results fail closed with
``UNREPRESENTABLE_SUBJECT``:

- ``HAZARD_SOURCE_VERSION`` whose identity contains both ``hazard_id``
  and ``source_version``
- ``ENCOUNTER_LINEAGE`` whose identity contains both ``encounter_id``
  and ``evaluation``

Single-pair hazard, aircraft, and encounter subjects export as written.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
    TemporalScope,
    ToolResult,
    _validate_bounded_text,
)
from wilvor_ai.live_ops_claims import (
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
    LiveOpsClaim,
    LiveOpsCountMetric,
    ModelVisibleTruncationClaim,
    QueryResultZeroClaim,
    RecommendationSetClaim,
    RiskStateClaim,
    ScopedCountClaim,
    is_live_ops_claim,
)
from wilvor_ai.live_ops_evidence_verifier import (
    LIVE_OPS_VERIFICATION_SCHEMA_VERSION,
    LiveOpsClaimAssessment,
    LiveOpsClaimVerdict,
    LiveOpsContradictionSubject,
    LiveOpsRenderingPosture,
    LiveOpsTrustedEvidenceLedger,
    LiveOpsVerificationOutcome,
    LiveOpsVerificationResult,
    _limitation_codes,
)
from wilvor_ai.live_ops_public_contracts import (
    LIVE_OPS_ENTITY_AUTHORITY,
    LIVE_OPS_IDENTITY_SET_MAX,
    LIVE_OPS_MAX_CLAIMS,
    LIVE_OPS_MAX_DIAGNOSTICS,
    LIVE_OPS_MAX_EVIDENCE_REFERENCES,
    LIVE_OPS_MAX_LIMITATION_CODES,
    LIVE_OPS_MAX_TOOL_CALLS,
    LIVE_OPS_PUBLIC_SCHEMA_VERSION,
    LIVE_OPS_PUBLIC_SPECIALIST,
    LiveOpsClaimDiagnostic,
    LiveOpsContradictionRecord,
    LiveOpsDiscovery,
    LiveOpsDiscoveryStatus,
    LiveOpsEntityRef,
    LiveOpsEvidenceReference,
    LiveOpsPublicClaim,
    LiveOpsPublicClaimKind,
    LiveOpsPublicClaimVerdict,
    LiveOpsPublicContradictionSubject,
    LiveOpsPublicCountMetric,
    LiveOpsPublicLimitationCode,
    LiveOpsPublicRenderingPosture,
    LiveOpsPublicVerificationOutcome,
    live_ops_reference_utc,
)
from wilvor_ai.live_ops_specialist_runtime_contracts import (
    LiveOpsDispatchStatus,
    LiveOpsToolInvocation,
)
from wilvor_ai.live_ops_tool_projection import LiveOpsToolProjection


LIVE_OPS_EXPORT_SCHEMA_VERSION = "wilvor.ai.live_ops_public_export.v1"

_CALLSIGN_TOOL = "find_aircraft_by_callsign"
_HAZARD_TOOL = "search_current_hazards"

_POSTURE = {
    LiveOpsPublicVerificationOutcome.ALL_VERIFIED: (
        LiveOpsPublicRenderingPosture.COMPLETE_VERIFIED_SET
    ),
    LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED: (
        LiveOpsPublicRenderingPosture.INCOMPLETE_VERIFIED_SUBSET
    ),
    LiveOpsPublicVerificationOutcome.NONE_VERIFIED: (
        LiveOpsPublicRenderingPosture.NO_VERIFIED_FACTS
    ),
    LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED: (
        LiveOpsPublicRenderingPosture.NOT_RENDERABLE
    ),
}
_AUTHORIZED_OUTCOMES = frozenset(
    {
        LiveOpsPublicVerificationOutcome.ALL_VERIFIED,
        LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED,
        LiveOpsPublicVerificationOutcome.NONE_VERIFIED,
    }
)
_DIAGNOSTIC_OUTCOMES = frozenset(
    {
        LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED,
        LiveOpsPublicVerificationOutcome.NONE_VERIFIED,
    }
)
_CONTRADICTION_KEY = {
    LiveOpsPublicContradictionSubject.HAZARD_SOURCE_VERSION: "hazard_id",
    LiveOpsPublicContradictionSubject.AIRCRAFT_CURRENTNESS: "aircraft_id",
    LiveOpsPublicContradictionSubject.ENCOUNTER_LINEAGE: "encounter_id",
}
_CLAIM_EXPORTS: dict[type, tuple[LiveOpsPublicClaimKind, frozenset[str]]] = {
    ConfirmedImpactClaim: (
        LiveOpsPublicClaimKind.CONFIRMED_IMPACT,
        frozenset({"kind", "tool_call_id", "tool_name", "aircraft_id", "hazard_id", "source_version", "encounter_id"}),
    ),
    ConfirmedEncounterClaim: (
        LiveOpsPublicClaimKind.CONFIRMED_ENCOUNTER,
        frozenset({"kind", "tool_call_id", "tool_name", "encounter_id", "aircraft_id", "hazard_id", "hazard_source_version", "projection_id", "recorded_flags", "overlap_statuses"}),
    ),
    ScopedCountClaim: (
        LiveOpsPublicClaimKind.SCOPED_COUNT,
        frozenset({"kind", "tool_call_id", "tool_name", "metric", "mode", "value", "scope"}),
    ),
    QueryResultZeroClaim: (
        LiveOpsPublicClaimKind.QUERY_RESULT_ZERO,
        frozenset({"kind", "tool_call_id", "tool_name", "metric", "scope"}),
    ),
    ContextRootAbsentClaim: (
        LiveOpsPublicClaimKind.CONTEXT_ROOT_ABSENT,
        frozenset({"kind", "tool_call_id", "tool_name", "entity_id"}),
    ),
    HazardRegionIntersectionClaim: (
        LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION,
        frozenset({"kind", "tool_call_id", "tool_name", "hazard_id", "source_version", "region_code", "region_name", "region_query"}),
    ),
    AircraftObservedStateClaim: (
        LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE,
        frozenset({"kind", "tool_call_id", "tool_name", "aircraft_id", "field", "value", "retained_not_current"}),
    ),
    AircraftProjectionStateClaim: (
        LiveOpsPublicClaimKind.AIRCRAFT_PROJECTION_STATE,
        frozenset({"kind", "tool_call_id", "tool_name", "aircraft_id", "projection_id", "field", "value"}),
    ),
    CurrentnessClaim: (
        LiveOpsPublicClaimKind.CURRENTNESS,
        frozenset({"kind", "tool_call_id", "tool_name", "predicate", "value", "aircraft_id", "hazard_id", "source_version", "encounter_id", "airport_id", "projection_id"}),
    ),
    RiskStateClaim: (
        LiveOpsPublicClaimKind.RISK_STATE,
        frozenset({"kind", "tool_call_id", "tool_name", "mode", "encounter_id", "aircraft_id", "hazard_id", "risk_id", "risk_level", "risk_score"}),
    ),
    RecommendationSetClaim: (
        LiveOpsPublicClaimKind.RECOMMENDATION_SET,
        frozenset({"kind", "tool_call_id", "tool_name", "mode", "encounter_id", "aircraft_id", "hazard_id", "recommendation_ids"}),
    ),
    AlertRecordClaim: (
        LiveOpsPublicClaimKind.ALERT_RECORD,
        frozenset({"kind", "tool_call_id", "tool_name", "mode", "encounter_id", "aircraft_id", "hazard_id", "alert_id", "alert_state", "alert_type"}),
    ),
    CallsignMatchSetClaim: (
        LiveOpsPublicClaimKind.CALLSIGN_MATCH_SET,
        frozenset({"kind", "tool_call_id", "tool_name", "callsign", "aircraft_ids"}),
    ),
    CoverageLimitationClaim: (
        LiveOpsPublicClaimKind.COVERAGE_LIMITATION,
        frozenset({"kind", "tool_call_id", "tool_name", "code"}),
    ),
    ModelVisibleTruncationClaim: (
        LiveOpsPublicClaimKind.MODEL_VISIBLE_TRUNCATION,
        frozenset({"kind", "tool_call_id", "tool_name", "list_name", "total_count"}),
    ),
    CrossToolContradictionClaim: (
        LiveOpsPublicClaimKind.CROSS_TOOL_CONTRADICTION,
        frozenset({"kind", "tool_call_id_a", "tool_call_id_b", "subject", "hazard_id", "aircraft_id", "encounter_id"}),
    ),
}


class LiveOpsExportErrorCode(str, Enum):
    MALFORMED_VERIFICATION = "MALFORMED_VERIFICATION"
    LEDGER_MISMATCH = "LEDGER_MISMATCH"
    CLOCK_MISMATCH = "CLOCK_MISMATCH"
    MISSING_PROVENANCE = "MISSING_PROVENANCE"
    UNMAPPABLE_CLAIM = "UNMAPPABLE_CLAIM"
    UNREPRESENTABLE_SUBJECT = "UNREPRESENTABLE_SUBJECT"
    UNREPRESENTABLE_LIMITATION = "UNREPRESENTABLE_LIMITATION"
    PUBLIC_INVARIANT_VIOLATION = "PUBLIC_INVARIANT_VIOLATION"
    RESULT_LIMIT_EXCEEDED = "RESULT_LIMIT_EXCEEDED"


class LiveOpsExportError(Exception):
    """Closed export failure. The message is only the error code."""

    def __init__(self, code: LiveOpsExportErrorCode) -> None:
        if not isinstance(code, LiveOpsExportErrorCode):
            raise TypeError("invalid_export_error_code")
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class _BoundInvocation:
    invocation: LiveOpsToolInvocation
    raw: ToolResult


@dataclass(frozen=True)
class LiveOpsEvidenceExport:
    """Immutable public evidence copied from one authorized LO2 result.

    This is not a ``LiveOpsRunResult``. Answer text is intentionally absent.
    """

    schema_version: str
    correlation_id: str
    reference_epoch: int
    reference_utc: str
    verification_outcome: LiveOpsPublicVerificationOutcome
    rendering_posture: LiveOpsPublicRenderingPosture
    verified_claims: tuple[LiveOpsPublicClaim, ...]
    evidence_references: tuple[LiveOpsEvidenceReference, ...]
    diagnostics: tuple[LiveOpsClaimDiagnostic, ...]
    structural_codes: tuple[str, ...]
    contradiction_subjects: tuple[LiveOpsContradictionRecord, ...]
    mandatory_limitations: tuple[str, ...]
    discoveries: tuple[LiveOpsDiscovery, ...]
    verified_entity_refs: tuple[LiveOpsEntityRef, ...]

    def __post_init__(self) -> None:
        _validate_bundle(self)


def export_live_ops_evidence(
    result: LiveOpsVerificationResult,
    ledger: LiveOpsTrustedEvidenceLedger,
    submitted_claims: tuple[LiveOpsClaim, ...],
) -> LiveOpsEvidenceExport:
    """Copy one LO2 result into public evidence DTOs.

    ``submitted_claims`` is the tuple passed to ``verify_live_ops_claims``.
    It aligns assessment indexes to claim kinds. Verified public claims come
    only from ``result.verified_claims``.
    """

    if not isinstance(result, LiveOpsVerificationResult) or not isinstance(
        ledger, LiveOpsTrustedEvidenceLedger
    ):
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
    if not isinstance(submitted_claims, tuple):
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
    if result.schema_version != LIVE_OPS_VERIFICATION_SCHEMA_VERSION:
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
    if not isinstance(result.outcome, LiveOpsVerificationOutcome) or not isinstance(
        result.rendering_posture, LiveOpsRenderingPosture
    ):
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
    try:
        outcome = LiveOpsPublicVerificationOutcome(result.outcome.value)
        posture = LiveOpsPublicRenderingPosture(result.rendering_posture.value)
    except ValueError:
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION) from None
    if posture is not _POSTURE[outcome]:
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
    correlation_id = _correlation(ledger)
    reference_epoch, reference_utc = _clock(ledger)
    limitations = _limitation_tuple(result.mandatory_limitations)
    if outcome is LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED:
        return _unauthorized_export(
            result,
            correlation_id,
            reference_epoch,
            reference_utc,
            outcome,
            posture,
            limitations,
        )
    _align_claims(result, submitted_claims, outcome)
    bound = _bind_ledger(ledger)
    public_claims = tuple(_export_claim(claim) for claim in result.verified_claims)
    _require_claim_tools(result.verified_claims, bound)
    diagnostics = _diagnostics(result, submitted_claims, outcome, bound)
    subjects = _subjects(result.contradiction_subjects, bound)
    discoveries = _discoveries(ledger, bound, result.verified_claims, reference_epoch)
    entity_refs = _entity_refs(
        discoveries,
        correlation_id,
        reference_epoch,
        reference_utc,
    )
    references = _references(
        result.verified_claims,
        discoveries,
        result.assessments,
        bound,
        reference_epoch,
        reference_utc,
    )
    structural = _structural_codes(result.structural_codes, required=False)
    _reject_over_limit(
        claims=len(public_claims),
        diagnostics=len(diagnostics),
        references=len(references),
        discoveries=len(discoveries),
    )
    return LiveOpsEvidenceExport(
        schema_version=LIVE_OPS_EXPORT_SCHEMA_VERSION,
        correlation_id=correlation_id,
        reference_epoch=reference_epoch,
        reference_utc=reference_utc,
        verification_outcome=outcome,
        rendering_posture=posture,
        verified_claims=public_claims,
        evidence_references=references,
        diagnostics=diagnostics,
        structural_codes=structural,
        contradiction_subjects=subjects,
        mandatory_limitations=limitations,
        discoveries=discoveries,
        verified_entity_refs=entity_refs,
    )


def _unauthorized_export(
    result: LiveOpsVerificationResult,
    correlation_id: str,
    reference_epoch: int,
    reference_utc: str,
    outcome: LiveOpsPublicVerificationOutcome,
    posture: LiveOpsPublicRenderingPosture,
    limitations: tuple[str, ...],
) -> LiveOpsEvidenceExport:
    if result.verified_claims or result.assessments or result.contradiction_subjects:
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
    return LiveOpsEvidenceExport(
        schema_version=LIVE_OPS_EXPORT_SCHEMA_VERSION,
        correlation_id=correlation_id,
        reference_epoch=reference_epoch,
        reference_utc=reference_utc,
        verification_outcome=outcome,
        rendering_posture=posture,
        verified_claims=(),
        evidence_references=(),
        diagnostics=(),
        structural_codes=_structural_codes(result.structural_codes, required=True),
        contradiction_subjects=(),
        mandatory_limitations=limitations,
        discoveries=(),
        verified_entity_refs=(),
    )


def _align_claims(
    result: LiveOpsVerificationResult,
    submitted_claims: tuple[LiveOpsClaim, ...],
    outcome: LiveOpsPublicVerificationOutcome,
) -> None:
    if any(not is_live_ops_claim(claim) for claim in submitted_claims):
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
    if len(submitted_claims) != len(result.assessments):
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
    verified: list[LiveOpsClaim] = []
    for index, (claim, assessment) in enumerate(zip(submitted_claims, result.assessments)):
        if assessment.claim_index != index or not isinstance(assessment, LiveOpsClaimAssessment):
            raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
        if assessment.verdict is LiveOpsClaimVerdict.VERIFIED:
            if set(_cited_ids(claim)) != set(assessment.tool_call_ids):
                raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
            verified.append(claim)
    if tuple(verified) != result.verified_claims:
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
    if outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED and (
        not verified or any(item.verdict is not LiveOpsClaimVerdict.VERIFIED for item in result.assessments)
    ):
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
    if outcome is LiveOpsPublicVerificationOutcome.NONE_VERIFIED and verified:
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
    if outcome is LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED and (
        not verified or len(verified) == len(result.assessments)
    ):
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
    if result.structural_codes:
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)


def _correlation(ledger: LiveOpsTrustedEvidenceLedger) -> str:
    errors = _validate_bounded_text(
        ledger.correlation_id,
        "correlation_id",
        max_length=PROVENANCE_TOKEN_MAX_LENGTH,
    )
    if errors or ledger.correlation_id != ledger.correlation_id.strip():
        raise LiveOpsExportError(LiveOpsExportErrorCode.LEDGER_MISMATCH)
    return ledger.correlation_id


def _clock(ledger: LiveOpsTrustedEvidenceLedger) -> tuple[int, str]:
    try:
        return ledger.now_epoch, live_ops_reference_utc(ledger.now_epoch)
    except ContractValidationError:
        raise LiveOpsExportError(LiveOpsExportErrorCode.CLOCK_MISMATCH) from None


def _bind_ledger(
    ledger: LiveOpsTrustedEvidenceLedger,
) -> dict[str, _BoundInvocation]:
    if not ledger.dispatches:
        raise LiveOpsExportError(LiveOpsExportErrorCode.LEDGER_MISMATCH)
    if any(item.status is LiveOpsDispatchStatus.EXECUTION_FAILED for item in ledger.dispatches):
        raise LiveOpsExportError(LiveOpsExportErrorCode.LEDGER_MISMATCH)
    bound: dict[str, _BoundInvocation] = {}
    seen: set[str] = set()
    for dispatch in ledger.dispatches:
        for invocation in dispatch.invocations:
            if invocation.tool_call_id in seen or not isinstance(invocation.raw_tool_result, ToolResult):
                raise LiveOpsExportError(LiveOpsExportErrorCode.LEDGER_MISMATCH)
            seen.add(invocation.tool_call_id)
            raw = invocation.raw_tool_result
            if (
                raw.tool_call_id != invocation.tool_call_id
                or raw.tool_name != invocation.tool_name
                or raw.temporal_scope is not TemporalScope.CURRENT
                or raw.correlation_id != ledger.correlation_id
            ):
                raise LiveOpsExportError(LiveOpsExportErrorCode.LEDGER_MISMATCH)
            data = raw.data if isinstance(raw.data, Mapping) else {}
            if data.get("as_of_epoch") != ledger.now_epoch:
                raise LiveOpsExportError(LiveOpsExportErrorCode.CLOCK_MISMATCH)
            if dispatch.status is not LiveOpsDispatchStatus.COMPLETED:
                continue
            projection = invocation.model_projection
            if not isinstance(projection, LiveOpsToolProjection):
                raise LiveOpsExportError(LiveOpsExportErrorCode.LEDGER_MISMATCH)
            if (
                projection.tool_call_id != invocation.tool_call_id
                or projection.tool_name != invocation.tool_name
                or projection.tool_name != raw.tool_name
                or projection.status is not raw.status
                or projection.temporal_scope is not raw.temporal_scope
            ):
                raise LiveOpsExportError(LiveOpsExportErrorCode.LEDGER_MISMATCH)
            if projection.as_of_epoch is not None and projection.as_of_epoch != ledger.now_epoch:
                raise LiveOpsExportError(LiveOpsExportErrorCode.CLOCK_MISMATCH)
            bound[invocation.tool_call_id] = _BoundInvocation(invocation=invocation, raw=raw)
    return bound


def _cited_ids(claim: LiveOpsClaim) -> tuple[str, ...]:
    if isinstance(claim, CrossToolContradictionClaim):
        return (claim.tool_call_id_a, claim.tool_call_id_b)
    return (claim.tool_call_id,)


def _expected_tool_name(claim: LiveOpsClaim) -> str | None:
    if isinstance(claim, CrossToolContradictionClaim):
        return None
    if isinstance(claim, HazardRegionIntersectionClaim):
        return _HAZARD_TOOL
    if isinstance(claim, CallsignMatchSetClaim):
        return _CALLSIGN_TOOL
    return claim.tool_name


def _require_claim_tools(
    claims: tuple[LiveOpsClaim, ...],
    bound: Mapping[str, _BoundInvocation],
) -> None:
    for claim in claims:
        expected = _expected_tool_name(claim)
        for tool_call_id in _cited_ids(claim):
            item = bound.get(tool_call_id)
            if item is None:
                raise LiveOpsExportError(LiveOpsExportErrorCode.MISSING_PROVENANCE)
            if expected is not None and item.raw.tool_name != expected:
                raise LiveOpsExportError(LiveOpsExportErrorCode.LEDGER_MISMATCH)


def _export_claim(claim: LiveOpsClaim) -> LiveOpsPublicClaim:
    spec = _CLAIM_EXPORTS.get(type(claim))
    if spec is None:
        raise LiveOpsExportError(LiveOpsExportErrorCode.UNMAPPABLE_CLAIM)
    kind, allowed = spec
    payload = claim.to_dict()
    if not isinstance(payload, dict) or set(payload) - allowed or payload.get("kind") != kind.value:
        raise LiveOpsExportError(LiveOpsExportErrorCode.UNMAPPABLE_CLAIM)
    try:
        public = LiveOpsPublicClaim.from_dict(payload)
    except ContractValidationError:
        raise LiveOpsExportError(LiveOpsExportErrorCode.UNMAPPABLE_CLAIM) from None
    if public.kind is not kind or public.to_dict() != payload:
        raise LiveOpsExportError(LiveOpsExportErrorCode.UNMAPPABLE_CLAIM)
    return public


def _diagnostics(
    result: LiveOpsVerificationResult,
    submitted_claims: tuple[LiveOpsClaim, ...],
    outcome: LiveOpsPublicVerificationOutcome,
    bound: Mapping[str, _BoundInvocation],
) -> tuple[LiveOpsClaimDiagnostic, ...]:
    if outcome not in _DIAGNOSTIC_OUTCOMES:
        if any(item.verdict is not LiveOpsClaimVerdict.VERIFIED for item in result.assessments):
            raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
        return ()
    exported: list[LiveOpsClaimDiagnostic] = []
    for claim, assessment in zip(submitted_claims, result.assessments):
        if assessment.verdict is LiveOpsClaimVerdict.VERIFIED:
            continue
        spec = _CLAIM_EXPORTS.get(type(claim))
        if spec is None or not assessment.codes:
            raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
        tool_call_ids = _diagnostic_ids(assessment, bound)
        try:
            exported.append(
                LiveOpsClaimDiagnostic(
                    claim_kind=spec[0],
                    verdict=LiveOpsPublicClaimVerdict(assessment.verdict.value),
                    codes=assessment.codes,
                    tool_call_ids=tool_call_ids,
                    limitation_codes=_limitation_tuple(assessment.limitation_codes),
                )
            )
        except ContractValidationError:
            raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION) from None
        except ValueError:
            raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION) from None
    if len(exported) > LIVE_OPS_MAX_DIAGNOSTICS:
        raise LiveOpsExportError(LiveOpsExportErrorCode.RESULT_LIMIT_EXCEEDED)
    return tuple(exported)


def _diagnostic_ids(
    assessment: LiveOpsClaimAssessment,
    bound: Mapping[str, _BoundInvocation],
) -> tuple[str, ...]:
    combined = assessment.tool_call_ids + assessment.conflicting_tool_call_ids
    if any(not isinstance(item, str) for item in combined):
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
    unique = tuple(sorted(set(combined)))
    if len(unique) > LIVE_OPS_IDENTITY_SET_MAX:
        raise LiveOpsExportError(LiveOpsExportErrorCode.RESULT_LIMIT_EXCEEDED)
    if any(item not in bound for item in unique):
        raise LiveOpsExportError(LiveOpsExportErrorCode.MISSING_PROVENANCE)
    return unique


def _subjects(
    subjects: tuple[LiveOpsContradictionSubject, ...],
    bound: Mapping[str, _BoundInvocation],
) -> tuple[LiveOpsContradictionRecord, ...]:
    exported: list[LiveOpsContradictionRecord] = []
    for subject in subjects:
        if not isinstance(subject, LiveOpsContradictionSubject) or len(subject.identity) != 1:
            raise LiveOpsExportError(LiveOpsExportErrorCode.UNREPRESENTABLE_SUBJECT)
        key, value = subject.identity[0]
        try:
            public_subject = LiveOpsPublicContradictionSubject(subject.subject.value)
        except ValueError:
            raise LiveOpsExportError(LiveOpsExportErrorCode.UNREPRESENTABLE_SUBJECT) from None
        if key != _CONTRADICTION_KEY[public_subject]:
            raise LiveOpsExportError(LiveOpsExportErrorCode.UNREPRESENTABLE_SUBJECT)
        ids = subject.tool_call_ids
        if not ids or any(not isinstance(item, str) or item not in bound for item in ids):
            raise LiveOpsExportError(LiveOpsExportErrorCode.MISSING_PROVENANCE)
        if len(ids) != len(set(ids)):
            raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
        if len(ids) > LIVE_OPS_IDENTITY_SET_MAX:
            raise LiveOpsExportError(LiveOpsExportErrorCode.RESULT_LIMIT_EXCEEDED)
        try:
            exported.append(
                LiveOpsContradictionRecord(
                    subject=public_subject,
                    identity=((key, value),),
                    tool_call_ids=tuple(sorted(ids)),
                )
            )
        except ContractValidationError:
            raise LiveOpsExportError(LiveOpsExportErrorCode.UNREPRESENTABLE_SUBJECT) from None
    return tuple(exported)


def _discoveries(
    ledger: LiveOpsTrustedEvidenceLedger,
    bound: Mapping[str, _BoundInvocation],
    verified_claims: tuple[LiveOpsClaim, ...],
    reference_epoch: int,
) -> tuple[LiveOpsDiscovery, ...]:
    discoveries: list[LiveOpsDiscovery] = []
    for dispatch in ledger.dispatches:
        if dispatch.status is not LiveOpsDispatchStatus.COMPLETED:
            continue
        for invocation in dispatch.invocations:
            if invocation.tool_name != _CALLSIGN_TOOL:
                continue
            item = bound.get(invocation.tool_call_id)
            if item is None:
                raise LiveOpsExportError(LiveOpsExportErrorCode.MISSING_PROVENANCE)
            discoveries.append(
                _one_discovery(item, verified_claims, reference_epoch)
            )
    if len(discoveries) > LIVE_OPS_MAX_TOOL_CALLS:
        raise LiveOpsExportError(LiveOpsExportErrorCode.RESULT_LIMIT_EXCEEDED)
    return tuple(discoveries)


def _one_discovery(
    item: _BoundInvocation,
    verified_claims: tuple[LiveOpsClaim, ...],
    reference_epoch: int,
) -> LiveOpsDiscovery:
    tool_call_id = item.invocation.tool_call_id
    matches = [
        claim
        for claim in verified_claims
        if isinstance(claim, CallsignMatchSetClaim) and claim.tool_call_id == tool_call_id
    ]
    zeros = [
        claim
        for claim in verified_claims
        if isinstance(claim, QueryResultZeroClaim)
        and claim.tool_call_id == tool_call_id
        and claim.metric is LiveOpsCountMetric.CALLSIGN_MATCH_COUNT
    ]
    if len(matches) > 1 or len(zeros) > 1 or (matches and zeros):
        raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
    executed = item.invocation.executed_arguments.get("callsign")
    if len(matches) == 1:
        claim = matches[0]
        if executed != claim.callsign:
            raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
        count = len(claim.aircraft_ids)
        if count == 1:
            status = LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH
            identity_resolved = True
            aircraft_ids = claim.aircraft_ids
        elif count >= 2:
            status = LiveOpsDiscoveryStatus.MULTIPLE_SUPPORTED_MATCHES
            identity_resolved = False
            aircraft_ids = claim.aircraft_ids
        else:
            raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
        callsign = claim.callsign
    elif len(zeros) == 1:
        claim = zeros[0]
        if not isinstance(claim.scope.callsign, str) or claim.scope.callsign != executed:
            raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
        status = LiveOpsDiscoveryStatus.NO_SUPPORTED_MATCH
        identity_resolved = False
        aircraft_ids = ()
        callsign = claim.scope.callsign
    else:
        if not isinstance(executed, str):
            raise LiveOpsExportError(LiveOpsExportErrorCode.MISSING_PROVENANCE)
        status = LiveOpsDiscoveryStatus.INCOMPLETE
        identity_resolved = False
        aircraft_ids = ()
        callsign = executed
    try:
        return LiveOpsDiscovery(
            callsign=callsign,
            status=status,
            aircraft_ids=aircraft_ids,
            evidence_tool_call_id=tool_call_id,
            reference_epoch=reference_epoch,
            identity_resolved=identity_resolved,
            selection_authorized=False,
        )
    except ContractValidationError:
        raise LiveOpsExportError(LiveOpsExportErrorCode.MISSING_PROVENANCE) from None


def _entity_refs(
    discoveries: tuple[LiveOpsDiscovery, ...],
    correlation_id: str,
    reference_epoch: int,
    reference_utc: str,
) -> tuple[LiveOpsEntityRef, ...]:
    refs: list[LiveOpsEntityRef] = []
    for discovery in discoveries:
        if discovery.status is not LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH:
            continue
        try:
            refs.append(
                LiveOpsEntityRef(
                    entity_kind="AIRCRAFT",
                    entity_id=discovery.aircraft_ids[0],
                    callsign=discovery.callsign,
                    authority=LIVE_OPS_ENTITY_AUTHORITY,
                    source_specialist=LIVE_OPS_PUBLIC_SPECIALIST,
                    public_schema_version=LIVE_OPS_PUBLIC_SCHEMA_VERSION,
                    correlation_id=correlation_id,
                    reference_epoch=reference_epoch,
                    reference_utc=reference_utc,
                    evidence_tool_call_id=discovery.evidence_tool_call_id,
                )
            )
        except ContractValidationError:
            raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION) from None
    return tuple(refs)


def _references(
    claims: tuple[LiveOpsClaim, ...],
    discoveries: tuple[LiveOpsDiscovery, ...],
    assessments: tuple[LiveOpsClaimAssessment, ...],
    bound: Mapping[str, _BoundInvocation],
    reference_epoch: int,
    reference_utc: str,
) -> tuple[LiveOpsEvidenceReference, ...]:
    ordered: list[str] = []
    seen: set[str] = set()

    def add(tool_call_id: str) -> None:
        if tool_call_id in seen:
            return
        seen.add(tool_call_id)
        ordered.append(tool_call_id)

    for claim in claims:
        for tool_call_id in _cited_ids(claim):
            add(tool_call_id)
    for discovery in discoveries:
        add(discovery.evidence_tool_call_id)
    if len(ordered) > LIVE_OPS_MAX_EVIDENCE_REFERENCES:
        raise LiveOpsExportError(LiveOpsExportErrorCode.RESULT_LIMIT_EXCEEDED)
    references: list[LiveOpsEvidenceReference] = []
    for tool_call_id in ordered:
        item = bound.get(tool_call_id)
        if item is None:
            raise LiveOpsExportError(LiveOpsExportErrorCode.MISSING_PROVENANCE)
        try:
            references.append(
                LiveOpsEvidenceReference(
                    tool_call_id=tool_call_id,
                    tool_name=item.raw.tool_name,
                    as_of_epoch=reference_epoch,
                    as_of_utc=reference_utc,
                    limitation_codes=_reference_limitations(item.raw, assessments, tool_call_id),
                )
            )
        except ContractValidationError:
            raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION) from None
    return tuple(references)


def _reference_limitations(
    raw: ToolResult,
    assessments: tuple[LiveOpsClaimAssessment, ...],
    tool_call_id: str,
) -> tuple[str, ...]:
    codes = list(_limitation_codes(raw))
    for assessment in assessments:
        if tool_call_id in assessment.tool_call_ids:
            codes.extend(assessment.limitation_codes)
    unique = tuple(sorted(set(codes)))
    return _limitation_tuple(unique)


def _limitation_tuple(codes: tuple[str, ...]) -> tuple[str, ...]:
    if len(codes) > LIVE_OPS_MAX_LIMITATION_CODES:
        raise LiveOpsExportError(LiveOpsExportErrorCode.RESULT_LIMIT_EXCEEDED)
    if len(codes) != len(set(codes)):
        raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
    for code in codes:
        if not isinstance(code, str):
            raise LiveOpsExportError(LiveOpsExportErrorCode.UNREPRESENTABLE_LIMITATION)
        try:
            LiveOpsPublicLimitationCode(code)
        except ValueError:
            raise LiveOpsExportError(LiveOpsExportErrorCode.UNREPRESENTABLE_LIMITATION) from None
    return tuple(codes)


def _structural_codes(codes: tuple[str, ...], *, required: bool) -> tuple[str, ...]:
    if required and not codes:
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
    if not required and codes:
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
    if len(codes) > LIVE_OPS_MAX_LIMITATION_CODES:
        raise LiveOpsExportError(LiveOpsExportErrorCode.RESULT_LIMIT_EXCEEDED)
    if len(codes) != len(set(codes)):
        raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
    for code in codes:
        if (
            not isinstance(code, str)
            or not code.strip()
            or code != code.strip()
            or len(code) > PROVENANCE_TOKEN_MAX_LENGTH
        ):
            raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
    return tuple(codes)


def _reject_over_limit(
    *,
    claims: int,
    diagnostics: int,
    references: int,
    discoveries: int,
) -> None:
    if (
        claims > LIVE_OPS_MAX_CLAIMS
        or diagnostics > LIVE_OPS_MAX_DIAGNOSTICS
        or references > LIVE_OPS_MAX_EVIDENCE_REFERENCES
        or discoveries > LIVE_OPS_MAX_TOOL_CALLS
    ):
        raise LiveOpsExportError(LiveOpsExportErrorCode.RESULT_LIMIT_EXCEEDED)


def _validate_bundle(bundle: LiveOpsEvidenceExport) -> None:
    if bundle.schema_version != LIVE_OPS_EXPORT_SCHEMA_VERSION:
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
    outcome = bundle.verification_outcome
    if not isinstance(outcome, LiveOpsPublicVerificationOutcome):
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
    if bundle.rendering_posture is not _POSTURE[outcome]:
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
    _require_tuple(bundle.verified_claims, LiveOpsPublicClaim, "verified_claims")
    _require_tuple(bundle.evidence_references, LiveOpsEvidenceReference, "evidence_references")
    _require_tuple(bundle.diagnostics, LiveOpsClaimDiagnostic, "diagnostics")
    _require_tuple(bundle.contradiction_subjects, LiveOpsContradictionRecord, "contradiction_subjects")
    _require_tuple(bundle.discoveries, LiveOpsDiscovery, "discoveries")
    _require_tuple(bundle.verified_entity_refs, LiveOpsEntityRef, "verified_entity_refs")
    _reject_over_limit(
        claims=len(bundle.verified_claims),
        diagnostics=len(bundle.diagnostics),
        references=len(bundle.evidence_references),
        discoveries=len(bundle.discoveries),
    )
    if outcome in (
        LiveOpsPublicVerificationOutcome.ALL_VERIFIED,
        LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED,
    ):
        if not bundle.verified_claims:
            raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
    elif bundle.verified_claims:
        raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
    if outcome is LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED:
        if not bundle.structural_codes:
            raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
        if (
            bundle.diagnostics
            or bundle.contradiction_subjects
            or bundle.discoveries
            or bundle.verified_entity_refs
            or bundle.evidence_references
        ):
            raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
        return
    if bundle.structural_codes:
        raise LiveOpsExportError(LiveOpsExportErrorCode.MALFORMED_VERIFICATION)
    if outcome not in _DIAGNOSTIC_OUTCOMES and bundle.diagnostics:
        raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
    if outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED and bundle.diagnostics:
        raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
    _validate_evidence_set(bundle)
    _validate_discovery_set(bundle)


def _require_tuple(value: object, item_type: type, name: str) -> None:
    if not isinstance(value, tuple) or any(not isinstance(item, item_type) for item in value):
        raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
    del name


def _validate_evidence_set(bundle: LiveOpsEvidenceExport) -> None:
    ids = [item.tool_call_id for item in bundle.evidence_references]
    if len(ids) != len(set(ids)):
        raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
    by_id = {item.tool_call_id: item for item in bundle.evidence_references}
    cited: set[str] = set()
    for claim in bundle.verified_claims:
        for tool_call_id in claim.cited_tool_call_ids():
            cited.add(tool_call_id)
            ref = by_id.get(tool_call_id)
            if ref is None:
                raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
            if claim.tool_name is not None and ref.tool_name != claim.tool_name:
                raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
            if ref.as_of_epoch != bundle.reference_epoch or ref.as_of_utc != bundle.reference_utc:
                raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
    for ref in bundle.evidence_references:
        if ref.as_of_epoch != bundle.reference_epoch or ref.as_of_utc != bundle.reference_utc:
            raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
    discovery_ids = {item.evidence_tool_call_id for item in bundle.discoveries}
    if set(ids) != cited | discovery_ids:
        raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)


def _validate_discovery_set(bundle: LiveOpsEvidenceExport) -> None:
    seen: set[str] = set()
    unique_by_call: dict[str, LiveOpsDiscovery] = {}
    for item in bundle.discoveries:
        if item.evidence_tool_call_id in seen or item.reference_epoch != bundle.reference_epoch:
            raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
        seen.add(item.evidence_tool_call_id)
        ref = next(
            (
                entry
                for entry in bundle.evidence_references
                if entry.tool_call_id == item.evidence_tool_call_id
            ),
            None,
        )
        if ref is None or ref.tool_name != _CALLSIGN_TOOL:
            raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
        matches = [
            claim
            for claim in bundle.verified_claims
            if claim.kind is LiveOpsPublicClaimKind.CALLSIGN_MATCH_SET
            and claim.tool_call_id == item.evidence_tool_call_id
        ]
        zeros = [
            claim
            for claim in bundle.verified_claims
            if claim.kind is LiveOpsPublicClaimKind.QUERY_RESULT_ZERO
            and claim.tool_call_id == item.evidence_tool_call_id
            and claim.metric is LiveOpsPublicCountMetric.CALLSIGN_MATCH_COUNT
        ]
        if item.status is LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH:
            unique_by_call[item.evidence_tool_call_id] = item
            if (
                len(matches) != 1
                or matches[0].callsign != item.callsign
                or matches[0].aircraft_ids != item.aircraft_ids
            ):
                raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
        elif item.status is LiveOpsDiscoveryStatus.MULTIPLE_SUPPORTED_MATCHES:
            if (
                len(matches) != 1
                or matches[0].callsign != item.callsign
                or matches[0].aircraft_ids != item.aircraft_ids
            ):
                raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
        elif item.status is LiveOpsDiscoveryStatus.NO_SUPPORTED_MATCH:
            if (
                matches
                or len(zeros) != 1
                or zeros[0].scope is None
                or zeros[0].scope.callsign != item.callsign
            ):
                raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
        elif matches or zeros:
            raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
    ref_keys: list[str] = []
    for ref in bundle.verified_entity_refs:
        if (
            ref.correlation_id != bundle.correlation_id
            or ref.reference_epoch != bundle.reference_epoch
            or ref.reference_utc != bundle.reference_utc
        ):
            raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
        discovery = unique_by_call.get(ref.evidence_tool_call_id)
        if discovery is None or discovery.callsign != ref.callsign or discovery.aircraft_ids != (ref.entity_id,):
            raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)
        ref_keys.append(ref.evidence_tool_call_id)
    if len(ref_keys) != len(set(ref_keys)):
        raise LiveOpsExportError(LiveOpsExportErrorCode.PUBLIC_INVARIANT_VIOLATION)


__all__ = [
    "LIVE_OPS_EXPORT_SCHEMA_VERSION",
    "LiveOpsEvidenceExport",
    "LiveOpsExportError",
    "LiveOpsExportErrorCode",
    "export_live_ops_evidence",
]
