"""Read-only current decision evidence for one aircraft.

The only domain input is ``aircraft_id``. Tables, ``now_epoch``, and
``tool_call_id`` stay on ``DecisionContextCall`` and are not model arguments.
Currentness is whatever ``build_aircraft_operational_context`` already decided.
Narrow risk and recommendation tools project that same mapped chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wilvor_ai.contracts import (
    ConfidenceLevel,
    Evidence,
    FreshnessStatus,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.decision_contracts import (
    V1_UNAVAILABLE_DECISION_CAPABILITIES,
    AlertCurrentLineage,
    DecisionAdvisoryAuthority,
    DecisionAlertLink,
    DecisionChainGap,
    DecisionEncounterLink,
    DecisionEvaluationState,
    DecisionEvidence,
    DecisionEvidenceKind,
    DecisionLimitationCode,
    DecisionRecommendationEvidence,
    DecisionRecommendationSet,
    DecisionReportedLinkState,
    DecisionRiskEvidence,
    DecisionRouteCapability,
    HazardSourceVersionLink,
    PersistedAirportEvaluationEvidence,
    PersistedConfidence,
    PersistedConfidenceSource,
    PersistedEvidenceReference,
    PersistedEvidenceReferenceType,
    PersistedSourceVersions,
    RecommendationActionType,
    RiskPresence,
    StoredRiskLevel,
    expected_decision_status,
    validate_decision_tool_result,
)
from wilvor_operational import context
from wilvor_operational import linking


TOOL_NAME = "get_current_decision_context"
RISK_TOOL_NAME = "get_current_risk_evidence"
RECOMMENDATION_TOOL_NAME = "get_current_recommendation"
_READ_FAILED = "The operational context read failed."
_HAZARD_LINK_NOT_PRESENT = (
    "The operational encounter hazard link is not present and was not repaired. "
    "Use get_current_decision_context."
)
_GAP_ORDER = (
    DecisionChainGap.AIRCRAFT_NOT_IN_CURRENT_SET,
    DecisionChainGap.NO_CURRENT_PROJECTION,
    DecisionChainGap.NO_CURRENT_ENCOUNTER,
    DecisionChainGap.RISK_ABSENT,
    DecisionChainGap.RECOMMENDATION_ABSENT,
    DecisionChainGap.STALE_RECOMMENDATION_EXCLUDED,
    DecisionChainGap.HAZARD_SOURCE_VERSION_MISMATCH,
    DecisionChainGap.INCOMPLETE_CURRENT_CHAIN,
)
_FRESHNESS_NOT_REEVALUATED = (
    "Record identity is copied from the operational read; the decision tool "
    "does not re-evaluate freshness."
)
_ALERT_LINEAGE_UNPROVEN = (
    "An alert in the operational context could not be proved from the "
    "current risk or recommendation identity."
)
_ENCOUNTER_UNREPORTABLE = (
    "An encounter could not be reported because its operational link state "
    "is outside the decision evidence contract."
)
_ID_MAX_LENGTH = 512
_REPORTABLE_LINK_STATES = frozenset(item.value for item in DecisionReportedLinkState)
_VERSION_FIELDS = (
    "hazard_source_version",
    "risk_schema_version",
    "airport_evaluation_id",
    "scoring_ruleset_version",
    "recommendation_ruleset_version",
)


@dataclass(frozen=True)
class DecisionContextCall:
    """Runtime-owned call context. Not a model-facing tool schema."""

    tables: Any
    now_epoch: int
    tool_call_id: str
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.now_epoch) is not int:
            raise TypeError("now_epoch must be an int")
        if not isinstance(self.tool_call_id, str) or not self.tool_call_id.strip():
            raise TypeError("tool_call_id must be a non-empty string")
        if self.tool_call_id != self.tool_call_id.strip():
            raise TypeError("tool_call_id must not include surrounding whitespace")
        if self.correlation_id is not None:
            if (
                not isinstance(self.correlation_id, str)
                or not self.correlation_id.strip()
                or self.correlation_id != self.correlation_id.strip()
            ):
                raise TypeError("correlation_id must be a non-empty string")


def get_current_decision_context(
    call: DecisionContextCall,
    aircraft_id: str,
) -> ToolResult:
    """Return the current decision-evidence chain for one aircraft."""

    as_of_utc, failed, evidence, extra, cited = _load_mapped(call, aircraft_id)
    if failed:
        return _unavailable(call, as_of_utc)
    return _emit(call, TOOL_NAME, evidence, extra, cited, as_of_utc)


def get_current_risk_evidence(
    call: DecisionContextCall,
    aircraft_id: str,
) -> ToolResult:
    """Return stored current risk evidence for one aircraft."""

    as_of_utc, failed, evidence, extra, cited = _load_mapped(call, aircraft_id)
    if failed:
        return _emit(
            call,
            RISK_TOOL_NAME,
            _risk_unavailable_evidence(),
            (),
            (),
            as_of_utc,
            limitations=(_READ_FAILED,),
        )
    projected, notes, narrowed = _project_narrow(
        evidence,
        cited,
        extra,
        include_recommendations=False,
    )
    return _emit(call, RISK_TOOL_NAME, projected, notes, narrowed, as_of_utc)


def get_current_recommendation(
    call: DecisionContextCall,
    aircraft_id: str,
) -> ToolResult:
    """Return the current recommendation set for one aircraft."""

    as_of_utc, failed, evidence, extra, cited = _load_mapped(call, aircraft_id)
    if failed:
        return _emit(
            call,
            RECOMMENDATION_TOOL_NAME,
            _recommendation_unavailable_evidence(),
            (),
            (),
            as_of_utc,
            limitations=(_READ_FAILED,),
        )
    projected, notes, narrowed = _project_narrow(
        evidence,
        cited,
        extra,
        include_recommendations=True,
    )
    return _emit(
        call,
        RECOMMENDATION_TOOL_NAME,
        projected,
        notes,
        narrowed,
        as_of_utc,
    )


def _load_mapped(
    call: DecisionContextCall,
    aircraft_id: str,
) -> tuple[str, bool, DecisionEvidence | None, tuple[str, ...], tuple[Evidence, ...]]:
    if not isinstance(call, DecisionContextCall):
        raise TypeError("call must be DecisionContextCall")
    as_of_utc = context._epoch_to_utc_z(call.now_epoch)
    try:
        operational = context.build_aircraft_operational_context(
            call.tables,
            aircraft_id,
            now_epoch=call.now_epoch,
        )
    except Exception:
        return as_of_utc, True, None, (), ()
    requested_id = _reportable_id(context._normalize_aircraft_id(aircraft_id))
    if operational is None or not bool(getattr(operational, "aircraft_is_current", False)):
        return as_of_utc, False, _not_found_evidence(requested_id), (), ()
    evidence, extra, cited = _established_evidence(
        operational,
        requested_id,
        call,
        as_of_utc,
    )
    return as_of_utc, False, evidence, extra, cited


def _unavailable(call: DecisionContextCall, as_of_utc: str) -> ToolResult:
    evidence = DecisionEvidence(
        kind=DecisionEvidenceKind.DECISION_CONTEXT,
        evaluation_state=DecisionEvaluationState.SOURCE_UNAVAILABLE,
        aircraft_in_current_set=False,
        projection_state=DecisionReportedLinkState.MISSING,
        chain_gaps=(),
        limitation_codes=(),
        capability=_capability(),
    )
    result = ToolResult(
        tool_name=TOOL_NAME,
        tool_call_id=call.tool_call_id,
        status=ToolResultStatus.UNAVAILABLE,
        temporal_scope=TemporalScope.CURRENT,
        data=evidence.to_dict(),
        evidence=(),
        as_of_utc=as_of_utc,
        limitations=(_READ_FAILED,),
        correlation_id=call.correlation_id,
    )
    validate_decision_tool_result(result)
    return result


def _emit(
    call: DecisionContextCall,
    tool_name: str,
    evidence: DecisionEvidence,
    extra: tuple[str, ...],
    cited: tuple[Evidence, ...],
    as_of_utc: str,
    *,
    limitations: tuple[str, ...] | None = None,
) -> ToolResult:
    result = ToolResult(
        tool_name=tool_name,
        tool_call_id=call.tool_call_id,
        status=expected_decision_status(evidence),
        temporal_scope=TemporalScope.CURRENT,
        data=evidence.to_dict(),
        evidence=cited,
        as_of_utc=as_of_utc,
        limitations=(
            limitations
            if limitations is not None
            else _result_limitations(evidence, extra)
        ),
        correlation_id=call.correlation_id,
    )
    validate_decision_tool_result(result)
    return result


def _risk_unavailable_evidence() -> DecisionEvidence:
    return DecisionEvidence(
        kind=DecisionEvidenceKind.RISK_EVIDENCE,
        evaluation_state=DecisionEvaluationState.SOURCE_UNAVAILABLE,
        aircraft_in_current_set=False,
        projection_state=DecisionReportedLinkState.MISSING,
        chain_gaps=(),
        limitation_codes=(),
        capability=None,
        risk=_absent_risk(),
    )


def _recommendation_unavailable_evidence() -> DecisionEvidence:
    return DecisionEvidence(
        kind=DecisionEvidenceKind.DECISION_CONTEXT,
        evaluation_state=DecisionEvaluationState.SOURCE_UNAVAILABLE,
        aircraft_in_current_set=False,
        projection_state=DecisionReportedLinkState.MISSING,
        chain_gaps=(),
        limitation_codes=(),
        capability=None,
    )


def _project_narrow(
    evidence: DecisionEvidence,
    cited: tuple[Evidence, ...],
    extra: tuple[str, ...],
    *,
    include_recommendations: bool,
) -> tuple[DecisionEvidence, tuple[str, ...], tuple[Evidence, ...]]:
    notes = tuple(item for item in extra if item != _ALERT_LINEAGE_UNPROVEN)
    if not evidence.aircraft_in_current_set:
        risk = _absent_risk()
        recommendations = _empty_recommendations() if include_recommendations else None
        gaps = [DecisionChainGap.AIRCRAFT_NOT_IN_CURRENT_SET, DecisionChainGap.RISK_ABSENT]
        if include_recommendations:
            gaps.append(DecisionChainGap.RECOMMENDATION_ABSENT)
        projected = _narrow_evidence(
            evidence,
            risk=risk,
            recommendations=recommendations,
            gaps=gaps,
            include_recommendations=include_recommendations,
        )
        return projected, notes, ()

    liftable = _single_liftable_encounter(evidence)
    if liftable is not None:
        risk = liftable.risk
        if risk.presence is RiskPresence.ABSENT:
            risk = _absent_risk(liftable.encounter_id)
        recommendations = liftable.recommendations if include_recommendations else None
        gaps = [
            gap
            for gap in evidence.chain_gaps
            if gap
            not in {
                DecisionChainGap.HAZARD_SOURCE_VERSION_MISMATCH,
                DecisionChainGap.STALE_RECOMMENDATION_EXCLUDED,
                DecisionChainGap.RECOMMENDATION_ABSENT,
            }
        ]
        if risk.presence is RiskPresence.ABSENT:
            gaps.append(DecisionChainGap.RISK_ABSENT)
        else:
            gaps = [gap for gap in gaps if gap is not DecisionChainGap.RISK_ABSENT]
        if include_recommendations and recommendations is not None and not recommendations.current:
            gaps.append(DecisionChainGap.RECOMMENDATION_ABSENT)
        projected = _narrow_evidence(
            evidence,
            risk=risk,
            recommendations=recommendations,
            gaps=gaps,
            include_recommendations=include_recommendations,
        )
        return projected, notes, _narrow_citations(cited, projected)

    if len(evidence.encounters) > 1:
        projected = DecisionEvidence(
            kind=(
                DecisionEvidenceKind.RECOMMENDATION_EVIDENCE
                if include_recommendations
                else DecisionEvidenceKind.RISK_EVIDENCE
            ),
            evaluation_state=DecisionEvaluationState.ESTABLISHED,
            aircraft_in_current_set=evidence.aircraft_in_current_set,
            aircraft_id=evidence.aircraft_id,
            projection_state=evidence.projection_state,
            projection_id=evidence.projection_id,
            aircraft_state_version=evidence.aircraft_state_version,
            encounters=evidence.encounters,
            alerts=(),
            risk=None,
            recommendations=None,
            capability=None,
            chain_gaps=evidence.chain_gaps,
            limitation_codes=evidence.limitation_codes,
        )
        return projected, notes, _narrow_citations(cited, projected)

    if evidence.projection_state is DecisionReportedLinkState.PRESENT and evidence.encounters:
        risk = _absent_risk()
        recommendations = _empty_recommendations() if include_recommendations else None
        gaps = [
            DecisionChainGap.INCOMPLETE_CURRENT_CHAIN,
            DecisionChainGap.RISK_ABSENT,
        ]
        if include_recommendations:
            gaps.append(DecisionChainGap.RECOMMENDATION_ABSENT)
        notes = notes + (_unliftable_encounter_note(evidence.encounters[0]),)
        projected = _narrow_evidence(
            evidence,
            risk=risk,
            recommendations=recommendations,
            gaps=gaps,
            include_recommendations=include_recommendations,
        )
        return projected, notes, _narrow_citations(cited, projected)

    risk = _absent_risk()
    recommendations = _empty_recommendations() if include_recommendations else None
    gaps = [
        gap
        for gap in evidence.chain_gaps
        if gap
        not in {
            DecisionChainGap.HAZARD_SOURCE_VERSION_MISMATCH,
            DecisionChainGap.STALE_RECOMMENDATION_EXCLUDED,
            DecisionChainGap.RECOMMENDATION_ABSENT,
            DecisionChainGap.RISK_ABSENT,
        }
    ]
    gaps.append(DecisionChainGap.RISK_ABSENT)
    if include_recommendations:
        gaps.append(DecisionChainGap.RECOMMENDATION_ABSENT)
    projected = _narrow_evidence(
        evidence,
        risk=risk,
        recommendations=recommendations,
        gaps=gaps,
        include_recommendations=include_recommendations,
    )
    return projected, notes, _narrow_citations(cited, projected)


def _narrow_evidence(
    source: DecisionEvidence,
    *,
    risk: DecisionRiskEvidence,
    recommendations: DecisionRecommendationSet | None,
    gaps: list[DecisionChainGap],
    include_recommendations: bool,
) -> DecisionEvidence:
    limitation_codes = [DecisionLimitationCode.NO_SNAPSHOT_LIMITATION]
    if include_recommendations and (
        recommendations is None or not recommendations.current
    ):
        limitation_codes.append(
            DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION
        )
    return DecisionEvidence(
        kind=(
            DecisionEvidenceKind.RECOMMENDATION_EVIDENCE
            if include_recommendations
            else DecisionEvidenceKind.RISK_EVIDENCE
        ),
        evaluation_state=DecisionEvaluationState.ESTABLISHED,
        aircraft_in_current_set=source.aircraft_in_current_set,
        aircraft_id=source.aircraft_id,
        projection_state=source.projection_state,
        projection_id=source.projection_id,
        aircraft_state_version=source.aircraft_state_version,
        encounters=(),
        alerts=(),
        risk=risk,
        recommendations=recommendations,
        capability=None,
        chain_gaps=_ordered_gaps(gaps),
        limitation_codes=tuple(limitation_codes),
    )


def _single_liftable_encounter(
    evidence: DecisionEvidence,
) -> DecisionEncounterLink | None:
    if len(evidence.encounters) != 1:
        return None
    encounter = evidence.encounters[0]
    if encounter.hazard.state is not DecisionReportedLinkState.PRESENT:
        return None
    return encounter


def _unliftable_encounter_note(encounter: DecisionEncounterLink) -> str:
    hazard = encounter.hazard
    if hazard.state is not DecisionReportedLinkState.HYDRATION_VERSION_MISMATCH:
        return _HAZARD_LINK_NOT_PRESENT
    persisted = hazard.persisted_source_version or "unreported"
    current = hazard.current_source_version or "unreported"
    return (
        "Operational hazard source versions "
        f"{persisted} and {current} differ and were not repaired. "
        "Narrow evidence cannot carry that link. "
        "Use get_current_decision_context."
    )


def _absent_risk(encounter_id: str | None = None) -> DecisionRiskEvidence:
    return DecisionRiskEvidence(
        presence=RiskPresence.ABSENT,
        encounter_id=encounter_id,
    )


def _empty_recommendations() -> DecisionRecommendationSet:
    return DecisionRecommendationSet(
        current=(),
        absence_state=DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES,
    )


def _ordered_gaps(gaps: list[DecisionChainGap]) -> tuple[DecisionChainGap, ...]:
    present = set(gaps)
    return tuple(gap for gap in _GAP_ORDER if gap in present)


def _narrow_citations(
    cited: tuple[Evidence, ...],
    evidence: DecisionEvidence,
) -> tuple[Evidence, ...]:
    risk_ids: set[str] = set()
    encounter_ids: set[str] = set()
    recommendation_ids: set[str] = set()
    hazard_ids: set[str] = set()
    if evidence.risk is not None:
        if evidence.risk.presence is RiskPresence.PRESENT and evidence.risk.risk_id:
            risk_ids.add(evidence.risk.risk_id)
        if evidence.risk.encounter_id:
            encounter_ids.add(evidence.risk.encounter_id)
    if evidence.recommendations is not None:
        recommendation_ids.update(
            item.recommendation_id for item in evidence.recommendations.current
        )
    for encounter in evidence.encounters:
        encounter_ids.add(encounter.encounter_id)
        hazard_ids.add(encounter.hazard.hazard_id)
        if encounter.risk.presence is RiskPresence.PRESENT and encounter.risk.risk_id:
            risk_ids.add(encounter.risk.risk_id)
        if encounter.risk.encounter_id:
            encounter_ids.add(encounter.risk.encounter_id)
        recommendation_ids.update(
            item.recommendation_id for item in encounter.recommendations.current
        )
    kept: list[Evidence] = []
    for item in cited:
        record_id = item.source_records[0].record_id if item.source_records else None
        if item.source == "get_aircraft_record" and evidence.aircraft_id:
            kept.append(item)
        elif (
            item.source == "get_projection_record"
            and evidence.projection_id
            and record_id == evidence.projection_id
        ):
            kept.append(item)
        elif item.source == "get_risk_record" and record_id in risk_ids:
            kept.append(item)
        elif item.source == "get_hazard_record" and record_id in hazard_ids:
            kept.append(item)
        elif item.source == "get_encounter_record" and record_id in encounter_ids:
            kept.append(item)
        elif (
            item.source == "scan_recommendation_candidates"
            and record_id in recommendation_ids
        ):
            kept.append(item)
    return tuple(kept)


def _not_found_evidence(aircraft_id: str | None) -> DecisionEvidence:
    return DecisionEvidence(
        kind=DecisionEvidenceKind.DECISION_CONTEXT,
        evaluation_state=DecisionEvaluationState.ESTABLISHED,
        aircraft_in_current_set=False,
        aircraft_id=aircraft_id,
        projection_state=DecisionReportedLinkState.MISSING,
        projection_id=None,
        aircraft_state_version=None,
        encounters=(),
        alerts=(),
        capability=_capability(),
        chain_gaps=(DecisionChainGap.AIRCRAFT_NOT_IN_CURRENT_SET,),
        limitation_codes=(DecisionLimitationCode.NO_SNAPSHOT_LIMITATION,),
    )


def _established_evidence(
    operational: Any,
    aircraft_id: str | None,
    call: DecisionContextCall,
    as_of_utc: str,
) -> tuple[DecisionEvidence, tuple[str, ...], tuple[Evidence, ...]]:
    projection_state, projection_note = _projection_state(operational)
    projection = getattr(operational, "projection", None)
    projection_id = None
    aircraft_state_version = None
    encounters: tuple[DecisionEncounterLink, ...] = ()
    alerts: tuple[DecisionAlertLink, ...] = ()
    notes: list[str] = []
    if projection_note:
        notes.append(projection_note)
    cited = [
        _cite(
            call,
            as_of_utc,
            source="get_aircraft_record",
            record_id=aircraft_id or "aircraft",
        )
    ]
    gaps = []
    if projection_state is not DecisionReportedLinkState.PRESENT:
        gaps.append(DecisionChainGap.NO_CURRENT_PROJECTION)
    else:
        projection_id = _reportable_id(_field(projection, "projection_id"))
        aircraft_state_version = _reportable_id(
            _field(projection, "aircraft_state_version")
        )
        if projection_id:
            cited.append(
                _cite(
                    call,
                    as_of_utc,
                    source="get_projection_record",
                    record_id=projection_id,
                    source_version=aircraft_state_version,
                )
            )
        encounters, alerts, encounter_notes, encounter_evidence = _map_encounters(
            getattr(operational, "encounters", ()) or (),
            call,
            as_of_utc,
            aircraft_state_version,
        )
        notes.extend(encounter_notes)
        cited.extend(encounter_evidence)
        if not encounters:
            gaps.append(DecisionChainGap.NO_CURRENT_ENCOUNTER)
    if any(item.risk.presence is RiskPresence.ABSENT for item in encounters):
        gaps.append(DecisionChainGap.RISK_ABSENT)
    if any(not item.recommendations.current for item in encounters):
        gaps.append(DecisionChainGap.RECOMMENDATION_ABSENT)
    if any(
        item.hazard.state is DecisionReportedLinkState.HYDRATION_VERSION_MISMATCH
        for item in encounters
    ):
        gaps.append(DecisionChainGap.HAZARD_SOURCE_VERSION_MISMATCH)
    limitation_codes = [DecisionLimitationCode.NO_SNAPSHOT_LIMITATION]
    if DecisionChainGap.RECOMMENDATION_ABSENT in gaps:
        limitation_codes.append(
            DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION
        )
    evidence = DecisionEvidence(
        kind=DecisionEvidenceKind.DECISION_CONTEXT,
        evaluation_state=DecisionEvaluationState.ESTABLISHED,
        aircraft_in_current_set=True,
        aircraft_id=aircraft_id,
        projection_state=projection_state,
        projection_id=projection_id,
        aircraft_state_version=aircraft_state_version,
        encounters=encounters,
        alerts=alerts,
        capability=_capability(),
        chain_gaps=tuple(gaps),
        limitation_codes=tuple(limitation_codes),
    )
    return evidence, tuple(notes), tuple(cited)


def _projection_state(operational: Any) -> tuple[DecisionReportedLinkState, str | None]:
    token = _state_token(getattr(operational, "projection_link", None))
    if token == DecisionReportedLinkState.PRESENT.value and bool(
        getattr(operational, "projection_is_current", False)
    ):
        return DecisionReportedLinkState.PRESENT, None
    if token in _REPORTABLE_LINK_STATES and token != DecisionReportedLinkState.PRESENT.value:
        return DecisionReportedLinkState(token), None
    reported = token or "UNREPORTED"
    return (
        DecisionReportedLinkState.MISSING,
        (
            "Operational projection link state "
            f"{reported} is not reported as a current projection."
        ),
    )


def _map_encounters(
    raw_encounters: Any,
    call: DecisionContextCall,
    as_of_utc: str,
    aircraft_state_version: str | None,
) -> tuple[
    tuple[DecisionEncounterLink, ...],
    tuple[DecisionAlertLink, ...],
    list[str],
    list[Evidence],
]:
    mapped: list[DecisionEncounterLink] = []
    alerts: list[DecisionAlertLink] = []
    notes: list[str] = []
    cited: list[Evidence] = []
    omitted = False
    lineage_unproven = False
    for item in raw_encounters:
        encounter, item_alerts, item_cited, unproven = _map_encounter(
            item,
            call,
            as_of_utc,
            aircraft_state_version,
        )
        if encounter is None:
            omitted = True
            continue
        mapped.append(encounter)
        alerts.extend(item_alerts)
        cited.extend(item_cited)
        lineage_unproven = lineage_unproven or unproven
    if omitted:
        notes.append(_ENCOUNTER_UNREPORTABLE)
    if lineage_unproven:
        notes.append(_ALERT_LINEAGE_UNPROVEN)
    return tuple(mapped), tuple(alerts), notes, cited


def _map_encounter(
    item: Any,
    call: DecisionContextCall,
    as_of_utc: str,
    aircraft_state_version: str | None,
) -> tuple[
    DecisionEncounterLink | None,
    tuple[DecisionAlertLink, ...],
    tuple[Evidence, ...],
    bool,
]:
    raw = getattr(item, "encounter", None)
    encounter_id = _reportable_id(_field(raw, "encounter_id"))
    hazard = _map_hazard(item, raw)
    if encounter_id is None or hazard is None:
        return None, (), (), False
    risk = _map_risk(item, encounter_id)
    recommendations = _map_recommendations(item, risk)
    projection_id = _reportable_id(_field(raw, "projection_id"))
    state_version = (
        _reportable_id(_field(raw, "aircraft_state_version")) or aircraft_state_version
    )
    encounter = DecisionEncounterLink(
        encounter_id=encounter_id,
        projection_id=projection_id,
        aircraft_state_version=state_version,
        hazard=hazard,
        risk=risk,
        recommendations=recommendations,
    )
    cited = [
        _cite(
            call,
            as_of_utc,
            source="get_encounter_record",
            record_id=encounter_id,
        ),
        _cite(
            call,
            as_of_utc,
            source="get_hazard_record",
            record_id=hazard.hazard_id,
            source_version=hazard.current_source_version or hazard.persisted_source_version,
        ),
    ]
    if risk.presence is RiskPresence.PRESENT and risk.risk_id:
        cited.append(
            _cite(
                call,
                as_of_utc,
                source="get_risk_record",
                record_id=risk.risk_id,
                source_version=risk.scoring_ruleset_version,
                event_timestamp_utc=risk.generated_at_utc,
            )
        )
    for recommendation in recommendations.current:
        cited.append(
            _cite(
                call,
                as_of_utc,
                source="scan_recommendation_candidates",
                record_id=recommendation.recommendation_id,
                source_version=recommendation.recommendation_version,
                event_timestamp_utc=recommendation.valid_from_utc,
            )
        )
    mapped_alerts, unproven = _map_alerts(
        getattr(item, "alerts", ()) or (),
        encounter_id=encounter_id,
        risk=risk,
        recommendations=recommendations,
    )
    for alert in mapped_alerts:
        cited.append(
            _cite(
                call,
                as_of_utc,
                source="scan_alert_candidates",
                record_id=alert.alert_id,
            )
        )
    return encounter, mapped_alerts, tuple(cited), unproven


def _map_hazard(item: Any, raw: Any) -> HazardSourceVersionLink | None:
    hazard_id = _reportable_id(_field(raw, "hazard_id"))
    if hazard_id is None:
        return None
    link = getattr(item, "hazard_link", None)
    token = _state_token(link)
    persisted = _reportable_id(_field(raw, "hazard_source_version")) or _identity_field(
        link,
        "selected_identity",
        "source_version",
    )
    observed = _reportable_id(_field(getattr(item, "hazard", None), "source_version"))
    observed = observed or _identity_field(link, "observed_identity", "source_version")
    if token == DecisionReportedLinkState.PRESENT.value:
        if not persisted or persisted != observed:
            return None
        state = DecisionReportedLinkState.PRESENT
    elif token == DecisionReportedLinkState.HYDRATION_VERSION_MISMATCH.value:
        if not persisted or not observed or persisted == observed:
            return None
        state = DecisionReportedLinkState.HYDRATION_VERSION_MISMATCH
    elif token in _REPORTABLE_LINK_STATES:
        state = DecisionReportedLinkState(token)
        persisted = None
        observed = None
    else:
        return None
    return HazardSourceVersionLink(
        hazard_id=hazard_id,
        state=state,
        persisted_source_version=persisted,
        current_source_version=observed,
    )


def _map_risk(item: Any, encounter_id: str) -> DecisionRiskEvidence:
    raw = getattr(item, "risk", None)
    if raw is None or _state_token(getattr(item, "risk_link", None)) != "PRESENT":
        return DecisionRiskEvidence(presence=RiskPresence.ABSENT)
    level = _stored_level(_field(raw, "risk_level"))
    score = _stored_score(_field(raw, "risk_score"))
    return DecisionRiskEvidence(
        presence=RiskPresence.PRESENT,
        risk_id=_reportable_id(_field(raw, "risk_id")),
        encounter_id=_reportable_id(_field(raw, "encounter_id")) or encounter_id,
        scoring_ruleset_version=_version(_field(raw, "scoring_ruleset_version")),
        generated_at_utc=_utc(_field(raw, "generated_at_utc")),
        valid_until_utc=_utc(_field(raw, "valid_until_utc")),
        risk_level=level,
        risk_score=score,
        confidence=_confidence(
            _field(raw, "confidence"),
            PersistedConfidenceSource.RISK_RESULT,
        ),
        reasons=_strings(_field(raw, "reasons")),
        limitations=_strings(_field(raw, "limitations")),
    )


def _map_recommendations(
    item: Any,
    risk: DecisionRiskEvidence,
) -> DecisionRecommendationSet:
    raw_items = getattr(item, "recommendations", ()) or ()
    if risk.presence is not RiskPresence.PRESENT or not raw_items:
        return DecisionRecommendationSet(
            current=(),
            absence_state=DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES,
        )
    current = tuple(_map_recommendation(raw) for raw in raw_items)
    return DecisionRecommendationSet(
        current=current,
        absence_state=DecisionReportedLinkState.PRESENT,
        excluded_stale_ids=(),
    )


def _map_recommendation(raw: Any) -> DecisionRecommendationEvidence:
    references = _references(_field(raw, "evidence_references"))
    source_versions = _source_versions(_field(raw, "source_versions"))
    evaluation_id = _reportable_id(_field(raw, "airport_evaluation_id"))
    if evaluation_id is None and source_versions is not None:
        evaluation_id = source_versions.airport_evaluation_id
    assessment_ids = _unique_ids(
        reference.record_id
        for reference in references
        if reference.reference_type is PersistedEvidenceReferenceType.AIRPORT_ASSESSMENT
    )
    airport_evaluation = None
    if evaluation_id is not None:
        airport_evaluation = PersistedAirportEvaluationEvidence(
            airport_evaluation_id=evaluation_id,
            airport_assessment_ids=assessment_ids,
        )
    action = RecommendationActionType(str(_field(raw, "primary_action_type")))
    return DecisionRecommendationEvidence(
        recommendation_id=str(_field(raw, "recommendation_id")),
        recommendation_version=_reportable_id(_field(raw, "recommendation_version")),
        ruleset_version=_reportable_id(_field(raw, "ruleset_version")),
        valid_from_utc=_utc(_field(raw, "valid_from_utc")),
        valid_until_utc=_utc(_field(raw, "valid_until_utc")),
        primary_action_type=action,
        advisory_authority=DecisionAdvisoryAuthority.ADVISORY_ONLY,
        advisory_notice=_text(_field(raw, "advisory_notice")) or None,
        confidence=_confidence(
            _field(raw, "confidence"),
            PersistedConfidenceSource.RECOMMENDATION,
        ),
        evidence_references=references,
        source_versions=source_versions,
        airport_evaluation=airport_evaluation,
        reasons=_strings(_field(raw, "reasons")),
        limitations=_strings(_field(raw, "limitations")),
    )


def _map_alerts(
    raw_alerts: Any,
    *,
    encounter_id: str,
    risk: DecisionRiskEvidence,
    recommendations: DecisionRecommendationSet,
) -> tuple[tuple[DecisionAlertLink, ...], bool]:
    current_risk_id = risk.risk_id if risk.presence is RiskPresence.PRESENT else None
    current_recommendation_ids = {
        item.recommendation_id for item in recommendations.current
    }
    mapped: list[DecisionAlertLink] = []
    unproven = False
    for raw in raw_alerts:
        alert_id = _reportable_id(_field(raw, "alert_id"))
        risk_id = _reportable_id(_field(raw, "risk_id"))
        recommendation_id = _reportable_id(_field(raw, "recommendation_id"))
        risk_matches = bool(current_risk_id) and risk_id == current_risk_id
        recommendation_matches = (
            recommendation_id is not None
            and recommendation_id in current_recommendation_ids
        )
        if alert_id is None or not (risk_matches or recommendation_matches):
            unproven = True
            continue
        if risk_matches and recommendation_matches:
            lineage = AlertCurrentLineage.BOTH
            reported_risk = risk_id
            reported_recommendation = recommendation_id
        elif risk_matches:
            lineage = AlertCurrentLineage.RISK
            reported_risk = risk_id
            reported_recommendation = None
        else:
            lineage = AlertCurrentLineage.RECOMMENDATION
            reported_risk = None
            reported_recommendation = recommendation_id
        mapped.append(
            DecisionAlertLink(
                alert_id=alert_id,
                lineage=lineage,
                risk_id=reported_risk,
                recommendation_id=reported_recommendation,
                encounter_id=encounter_id,
            )
        )
    return tuple(mapped), unproven


def _capability() -> DecisionRouteCapability:
    return DecisionRouteCapability(
        validated_alternative_available=False,
        unavailable=V1_UNAVAILABLE_DECISION_CAPABILITIES,
    )


def _result_limitations(
    evidence: DecisionEvidence,
    extra: tuple[str, ...],
) -> tuple[str, ...]:
    texts: list[str] = []
    if DecisionLimitationCode.NO_SNAPSHOT_LIMITATION in evidence.limitation_codes:
        texts.append(linking.NO_SNAPSHOT_LIMITATION)
    if (
        DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION
        in evidence.limitation_codes
    ):
        texts.append(linking.RECOMMENDATION_ABSENCE_LIMITATION)
    texts.extend(extra)
    return _unique_text(texts)


def _cite(
    call: DecisionContextCall,
    as_of_utc: str,
    *,
    source: str,
    record_id: str,
    source_version: str | None = None,
    event_timestamp_utc: str | None = None,
) -> Evidence:
    return Evidence(
        source=source,
        source_records=(
            SourceRecord(
                record_id=record_id,
                source_version=source_version,
                event_timestamp_utc=event_timestamp_utc,
            ),
        ),
        query_timestamp_utc=as_of_utc,
        freshness_status=FreshnessStatus.UNKNOWN,
        confidence=ConfidenceLevel.UNKNOWN,
        limitations=(_FRESHNESS_NOT_REEVALUATED,),
        tool_call_id=call.tool_call_id,
        temporal_scope=TemporalScope.CURRENT,
    )


def _references(value: Any) -> tuple[PersistedEvidenceReference, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        return ()
    mapped: list[PersistedEvidenceReference] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            reference_type = PersistedEvidenceReferenceType(str(item.get("type")))
        except ValueError:
            continue
        record_id = _reportable_id(item.get("id"))
        if record_id is None:
            continue
        airport_id = None
        if reference_type is PersistedEvidenceReferenceType.AIRPORT_ASSESSMENT:
            airport_id = _reportable_id(item.get("airport_id"))
        mapped.append(
            PersistedEvidenceReference(
                reference_type=reference_type,
                record_id=record_id,
                airport_id=airport_id,
            )
        )
    return tuple(mapped)


def _source_versions(value: Any) -> PersistedSourceVersions | None:
    if not isinstance(value, dict):
        return None
    versions = PersistedSourceVersions(
        **{name: _version(value.get(name)) for name in _VERSION_FIELDS}
    )
    if all(getattr(versions, name) is None for name in _VERSION_FIELDS):
        return None
    return versions


def _stored_level(value: Any) -> StoredRiskLevel | None:
    if not isinstance(value, str):
        return None
    try:
        return StoredRiskLevel(value.strip().upper())
    except ValueError:
        return None


def _stored_score(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        integral = getattr(value, "to_integral_value", None)
        if not callable(integral):
            return None
        try:
            if value != integral() or value < 0:
                return None
            return int(value)
        except (TypeError, ValueError, ArithmeticError):
            return None
    if value < 0:
        return None
    return value


def _confidence(
    value: Any,
    source: PersistedConfidenceSource,
) -> PersistedConfidence | None:
    if not isinstance(value, str):
        return None
    try:
        level = ConfidenceLevel(value.strip().upper())
    except ValueError:
        return None
    return PersistedConfidence(value=level, source=source)


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _utc(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _version(value: Any) -> str | None:
    text = _reportable_id(value)
    if text is None or text == "UNKNOWN":
        return None
    return text


def _reportable_id(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value).strip()
    else:
        value = value.strip()
    if not value or any(character.isspace() for character in value):
        return None
    if len(value) > _ID_MAX_LENGTH:
        return None
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return None


def _state_token(link: Any) -> str:
    state = getattr(link, "state", None)
    token = getattr(state, "value", None)
    if isinstance(token, str):
        return token
    if isinstance(state, str):
        return state
    return ""


def _identity_field(link: Any, side: str, name: str) -> str | None:
    pairs = getattr(link, side, ()) or ()
    for pair in pairs:
        if isinstance(pair, tuple) and len(pair) == 2 and pair[0] == name:
            return _reportable_id(pair[1])
    return None


def _unique_ids(values: Any) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)


def _unique_text(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)
