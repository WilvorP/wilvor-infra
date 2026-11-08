"""Entity linking, freshness, and missing-record semantics for Decision live reads.

Missing risk is not LOW. Missing recommendation is not MONITOR. Persisted
airport evidence is not a selected diversion. This module does not repair
identity or version conflicts and does not invent ATC, landing, or route
directives.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from wilvor_ai.contracts import (
    ContractValidationError,
    FreshnessStatus,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.decision_contracts import (
    DecisionChainGap,
    DecisionEvidence,
    RecommendationActionType,
    RiskPresence,
    StoredRiskLevel,
    validate_decision_tool_result,
)
from wilvor_ai.decision_tools import DecisionToolInvocation
from wilvor_ai.persisted_airport_contracts import (
    PersistedAirportEvidence,
    validate_persisted_airport_tool_result,
)
from wilvor_ai.sprint4_decision_weather_context import (
    Sprint4AirportWeatherContext,
    WeatherContextStatus,
)
from wilvor_operational.context import normalize_aircraft_id


SPRINT4_DECISION_RECONCILIATION_SCHEMA_VERSION = (
    "wilvor.ai.sprint4.decision_reconciliation.v1"
)
ABSENT_RISK_IS_LOW_RISK = False
ABSENT_RECOMMENDATION_IS_MONITOR = False
OPERATIONAL_DIRECTIVES_ENABLED = False
PERSISTED_EVIDENCE_IS_CURRENT_OBSERVATION = False

_ADVISORY_ONLY = (
    "Decision live evidence is advisory stored evidence only. It is not a "
    "clearance, landing instruction, diversion order, or ATC directive."
)
_NOT_LOW = "Missing risk does not mean LOW."
_NOT_MONITOR = "Missing recommendation does not mean MONITOR."
_NOT_DIVERSION = (
    "Missing or persisted airport-assessment evidence is not a selected "
    "diversion, landing directive, or route."
)
_DIRECTIVE_FRAGMENTS = (
    "land immediately",
    "contact atc",
    "atc clearance",
    "cleared to land",
    "divert now",
    "you must divert",
    "descend immediately",
    "climb immediately",
    "declare emergency",
    "atc instruction",
)
_SKIP_DIRECTIVE_KEYS = frozenset(
    {
        "primary_action_type",
        "candidate_reason",
        "known_limitations",
    }
)


class RecordObservation(str, Enum):
    """Whether a persisted or current record was present. Not a default."""

    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_REQUESTED = "NOT_REQUESTED"


class EntityLinkDefect(str, Enum):
    """Identity or version disagreement. None of these are repaired."""

    AIRCRAFT_IDENTITY_MISMATCH = "AIRCRAFT_IDENTITY_MISMATCH"
    RISK_RECOMMENDATION_RISK_ID_MISMATCH = "RISK_RECOMMENDATION_RISK_ID_MISMATCH"
    ASSESSMENT_AIRCRAFT_MISMATCH = "ASSESSMENT_AIRCRAFT_MISMATCH"
    ASSESSMENT_RISK_ID_MISMATCH = "ASSESSMENT_RISK_ID_MISMATCH"
    HAZARD_SOURCE_VERSION_MISMATCH = "HAZARD_SOURCE_VERSION_MISMATCH"
    EVALUATION_ID_MISMATCH = "EVALUATION_ID_MISMATCH"
    METAR_STATION_MISMATCH = "METAR_STATION_MISMATCH"
    TAF_STATION_MISMATCH = "TAF_STATION_MISMATCH"
    STATION_UNRESOLVED = "STATION_UNRESOLVED"
    WEATHER_VERSION_MISMATCH = "WEATHER_VERSION_MISMATCH"


def _closed() -> None:
    if (
        ABSENT_RISK_IS_LOW_RISK is not False
        or ABSENT_RECOMMENDATION_IS_MONITOR is not False
        or OPERATIONAL_DIRECTIVES_ENABLED is not False
        or PERSISTED_EVIDENCE_IS_CURRENT_OBSERVATION is not False
    ):
        raise ContractValidationError("decision_safety_invariant_weakened")


@dataclass(frozen=True)
class Sprint4MissingRecordReport:
    """Absence stays absence. Defaults are not minted."""

    risk: RecordObservation
    recommendation: RecordObservation
    airport_assessment: RecordObservation
    synthesized_low_risk: bool = False
    synthesized_monitor: bool = False
    synthesized_diversion: bool = False
    stored_low_risk_present: bool = False
    stored_monitor_present: bool = False

    def __post_init__(self) -> None:
        _closed()
        if self.synthesized_low_risk is not False:
            raise ContractValidationError("absent_risk_is_not_low")
        if self.synthesized_monitor is not False:
            raise ContractValidationError("absent_recommendation_is_not_monitor")
        if self.synthesized_diversion is not False:
            raise ContractValidationError("absent_airport_is_not_diversion")
        if self.risk is RecordObservation.ABSENT and self.stored_low_risk_present:
            raise ContractValidationError("absent_risk_is_not_low")
        if (
            self.recommendation is RecordObservation.ABSENT
            and self.stored_monitor_present
        ):
            raise ContractValidationError("absent_recommendation_is_not_monitor")


@dataclass(frozen=True)
class Sprint4FreshnessReport:
    """Clocks and freshness copied from existing results. Not re-evaluated."""

    current_temporal_scope: TemporalScope | None
    persisted_temporal_scope: TemporalScope | None
    weather_temporal_scope: TemporalScope | None
    persisted_freshness: FreshnessStatus | None
    weather_freshness: FreshnessStatus | None
    query_timestamp_utc: str
    evaluation_clock_utc: str
    source_observation_copied_from_query: bool = False
    persisted_treated_as_current: bool = False

    def __post_init__(self) -> None:
        _closed()
        if self.source_observation_copied_from_query is not False:
            raise ContractValidationError("evaluation_copied_from_request")
        if self.persisted_treated_as_current is not False:
            raise ContractValidationError("persisted_evidence_is_not_current")
        if (
            self.weather_temporal_scope is not None
            and self.weather_temporal_scope is not TemporalScope.PERSISTED
        ):
            raise ContractValidationError("persisted_weather_temporal_scope")
        if (
            self.persisted_temporal_scope is not None
            and self.persisted_temporal_scope is not TemporalScope.PERSISTED
        ):
            raise ContractValidationError("persisted_airport_temporal_scope")
        if (
            self.weather_freshness is not None
            and self.weather_freshness is not FreshnessStatus.UNKNOWN
        ):
            raise ContractValidationError("persisted_weather_freshness_not_reevaluated")


@dataclass(frozen=True)
class Sprint4EntityLinkReport:
    requested_aircraft_id: str | None
    requested_recommendation_id: str | None
    returned_aircraft_id: str | None
    risk_id: str | None
    recommendation_ids: tuple[str, ...]
    airport_evaluation_id: str | None
    defects: tuple[EntityLinkDefect, ...]

    def __post_init__(self) -> None:
        _closed()
        if not isinstance(self.defects, tuple) or any(
            not isinstance(item, EntityLinkDefect) for item in self.defects
        ):
            raise ContractValidationError("invalid_entity_link_defects")
        if not isinstance(self.recommendation_ids, tuple):
            raise ContractValidationError("invalid_recommendation_ids")


@dataclass(frozen=True)
class Sprint4AdvisoryConstraintReport:
    advisory_only: bool
    operational_directives_present: bool
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        _closed()
        if self.advisory_only is not True:
            raise ContractValidationError("decision_must_remain_advisory")
        if self.operational_directives_present and OPERATIONAL_DIRECTIVES_ENABLED:
            raise ContractValidationError("operational_directives_enabled")


def reconcile_sprint4_decision_evidence(
    *,
    aircraft_id: str | None,
    recommendation_id: str | None,
    invocations: Mapping[str, DecisionToolInvocation],
    weather: tuple[Sprint4AirportWeatherContext, ...],
    query_timestamp_utc: str,
    evaluation_clock_utc: str,
) -> tuple[
    Sprint4MissingRecordReport,
    Sprint4FreshnessReport,
    Sprint4EntityLinkReport,
    Sprint4AdvisoryConstraintReport,
]:
    """Join existing tool results with weather context. Do not mint defaults."""

    _closed()
    risk_evidence = _current_evidence(invocations, "get_current_risk_evidence")
    recommendation_evidence = _current_evidence(
        invocations,
        "get_current_recommendation",
    )
    context_evidence = _current_evidence(
        invocations,
        "get_current_decision_context",
    )
    persisted = _persisted(invocations)
    missing = _missing(
        risk_evidence,
        recommendation_evidence,
        persisted,
        requested_risk=aircraft_id is not None,
        requested_recommendation=aircraft_id is not None,
        requested_airport=recommendation_id is not None,
    )
    freshness = _freshness(
        invocations,
        weather,
        query_timestamp_utc=query_timestamp_utc,
        evaluation_clock_utc=evaluation_clock_utc,
    )
    links = _links(
        aircraft_id,
        recommendation_id,
        risk_evidence,
        recommendation_evidence,
        context_evidence,
        persisted,
        weather,
    )
    advisory = _advisory(invocations)
    return missing, freshness, links, advisory


def _current_evidence(
    invocations: Mapping[str, DecisionToolInvocation],
    tool_name: str,
) -> DecisionEvidence | None:
    invocation = invocations.get(tool_name)
    if invocation is None:
        return None
    result = invocation.raw_tool_result
    if result.status is ToolResultStatus.UNAVAILABLE:
        return None
    return validate_decision_tool_result(result)


def _persisted(
    invocations: Mapping[str, DecisionToolInvocation],
) -> PersistedAirportEvidence | None:
    invocation = invocations.get("get_persisted_airport_candidate_evidence")
    if invocation is None:
        return None
    result = invocation.raw_tool_result
    if result.status is ToolResultStatus.UNAVAILABLE:
        return None
    return validate_persisted_airport_tool_result(result)


def _missing(
    risk_evidence: DecisionEvidence | None,
    recommendation_evidence: DecisionEvidence | None,
    persisted: PersistedAirportEvidence | None,
    *,
    requested_risk: bool,
    requested_recommendation: bool,
    requested_airport: bool,
) -> Sprint4MissingRecordReport:
    risk_obs = RecordObservation.NOT_REQUESTED
    stored_low = False
    if requested_risk:
        if risk_evidence is None:
            risk_obs = RecordObservation.UNAVAILABLE
        else:
            present = _present_risk(risk_evidence)
            if present is None:
                risk_obs = RecordObservation.ABSENT
            else:
                risk_obs = RecordObservation.PRESENT
                stored_low = present is StoredRiskLevel.LOW

    recommendation_obs = RecordObservation.NOT_REQUESTED
    stored_monitor = False
    if requested_recommendation:
        if recommendation_evidence is None:
            recommendation_obs = RecordObservation.UNAVAILABLE
        else:
            actions = _recommendation_actions(recommendation_evidence)
            if not actions:
                recommendation_obs = RecordObservation.ABSENT
            else:
                recommendation_obs = RecordObservation.PRESENT
                stored_monitor = RecommendationActionType.MONITOR in actions

    airport_obs = RecordObservation.NOT_REQUESTED
    if requested_airport:
        if persisted is None:
            airport_obs = RecordObservation.UNAVAILABLE
        elif persisted.candidates:
            airport_obs = RecordObservation.PRESENT
        else:
            airport_obs = RecordObservation.ABSENT

    return Sprint4MissingRecordReport(
        risk=risk_obs,
        recommendation=recommendation_obs,
        airport_assessment=airport_obs,
        stored_low_risk_present=stored_low,
        stored_monitor_present=stored_monitor,
    )


def _present_risk(evidence: DecisionEvidence) -> StoredRiskLevel | None:
    if DecisionChainGap.RISK_ABSENT in evidence.chain_gaps:
        return None
    risks = []
    if evidence.risk is not None:
        risks.append(evidence.risk)
    for encounter in evidence.encounters:
        risks.append(encounter.risk)
    present = [
        item.risk_level
        for item in risks
        if item.presence is RiskPresence.PRESENT and item.risk_level is not None
    ]
    if not present:
        return None
    return present[0]


def _recommendation_actions(
    evidence: DecisionEvidence,
) -> tuple[RecommendationActionType, ...]:
    if DecisionChainGap.RECOMMENDATION_ABSENT in evidence.chain_gaps:
        return ()
    current = ()
    if evidence.recommendations is not None:
        current = evidence.recommendations.current
    if not current:
        for encounter in evidence.encounters:
            current = current + encounter.recommendations.current
    return tuple(item.primary_action_type for item in current)


def _freshness(
    invocations: Mapping[str, DecisionToolInvocation],
    weather: tuple[Sprint4AirportWeatherContext, ...],
    *,
    query_timestamp_utc: str,
    evaluation_clock_utc: str,
) -> Sprint4FreshnessReport:
    current_scope = None
    persisted_scope = None
    persisted_freshness = None
    for name, invocation in invocations.items():
        result = invocation.raw_tool_result
        if name == "get_persisted_airport_candidate_evidence":
            persisted_scope = result.temporal_scope
            if result.evidence:
                persisted_freshness = result.evidence[0].freshness_status
        elif current_scope is None:
            current_scope = result.temporal_scope
    weather_scope = TemporalScope.PERSISTED if weather else None
    weather_freshness = FreshnessStatus.UNKNOWN if weather else None
    if evaluation_clock_utc == query_timestamp_utc:
        # Distinct roles may share equal text; they are still not merged.
        pass
    return Sprint4FreshnessReport(
        current_temporal_scope=current_scope,
        persisted_temporal_scope=persisted_scope,
        weather_temporal_scope=weather_scope,
        persisted_freshness=persisted_freshness,
        weather_freshness=weather_freshness,
        query_timestamp_utc=query_timestamp_utc,
        evaluation_clock_utc=evaluation_clock_utc,
    )


def _links(
    aircraft_id: str | None,
    recommendation_id: str | None,
    risk_evidence: DecisionEvidence | None,
    recommendation_evidence: DecisionEvidence | None,
    context_evidence: DecisionEvidence | None,
    persisted: PersistedAirportEvidence | None,
    weather: tuple[Sprint4AirportWeatherContext, ...],
) -> Sprint4EntityLinkReport:
    defects: list[EntityLinkDefect] = []
    returned_aircraft = _first_aircraft(
        risk_evidence,
        recommendation_evidence,
        context_evidence,
    )
    if aircraft_id is not None and returned_aircraft is not None:
        if normalize_aircraft_id(aircraft_id) != normalize_aircraft_id(returned_aircraft):
            defects.append(EntityLinkDefect.AIRCRAFT_IDENTITY_MISMATCH)

    risk_id = _first_risk_id(risk_evidence, context_evidence)
    recommendation_ids: list[str] = []
    rec_risk_ids: set[str] = set()
    hazard_versions: set[str] = set()
    if recommendation_evidence is not None:
        for item in recommendation_evidence.recommendations.current if recommendation_evidence.recommendations else ():
            recommendation_ids.append(item.recommendation_id)
            if item.source_versions is not None and item.source_versions.hazard_source_version:
                hazard_versions.add(item.source_versions.hazard_source_version)
            for reference in item.evidence_references:
                if reference.reference_type.value == "RISK_RESULT":
                    rec_risk_ids.add(reference.record_id)
    if risk_id is not None and rec_risk_ids and risk_id not in rec_risk_ids:
        defects.append(EntityLinkDefect.RISK_RECOMMENDATION_RISK_ID_MISMATCH)

    for evidence in (risk_evidence, recommendation_evidence, context_evidence):
        if evidence is None:
            continue
        if DecisionChainGap.HAZARD_SOURCE_VERSION_MISMATCH in evidence.chain_gaps:
            defects.append(EntityLinkDefect.HAZARD_SOURCE_VERSION_MISMATCH)

    evaluation_id = None
    if persisted is not None:
        evaluation_id = persisted.airport_evaluation_id
        if recommendation_id is not None and persisted.recommendation_id != recommendation_id:
            defects.append(EntityLinkDefect.EVALUATION_ID_MISMATCH)
        for candidate in persisted.candidates:
            if risk_id is not None and candidate.risk_id != risk_id:
                defects.append(EntityLinkDefect.ASSESSMENT_RISK_ID_MISMATCH)

    for item in weather:
        if item.metar.status is WeatherContextStatus.STATION_UNRESOLVED:
            defects.append(EntityLinkDefect.STATION_UNRESOLVED)
        if item.taf.status is WeatherContextStatus.STATION_UNRESOLVED:
            defects.append(EntityLinkDefect.STATION_UNRESOLVED)
        if item.metar.status is WeatherContextStatus.VERSION_MISMATCH:
            defects.append(EntityLinkDefect.WEATHER_VERSION_MISMATCH)
        if item.taf.status is WeatherContextStatus.VERSION_MISMATCH:
            defects.append(EntityLinkDefect.WEATHER_VERSION_MISMATCH)

    unique = tuple(dict.fromkeys(defects))
    return Sprint4EntityLinkReport(
        requested_aircraft_id=aircraft_id,
        requested_recommendation_id=recommendation_id,
        returned_aircraft_id=returned_aircraft,
        risk_id=risk_id,
        recommendation_ids=tuple(recommendation_ids),
        airport_evaluation_id=evaluation_id,
        defects=unique,
    )


def _first_aircraft(*evidences: DecisionEvidence | None) -> str | None:
    for evidence in evidences:
        if evidence is not None and evidence.aircraft_id:
            return evidence.aircraft_id
    return None


def _first_risk_id(*evidences: DecisionEvidence | None) -> str | None:
    for evidence in evidences:
        if evidence is None:
            continue
        if (
            evidence.risk is not None
            and evidence.risk.presence is RiskPresence.PRESENT
            and evidence.risk.risk_id
        ):
            return evidence.risk.risk_id
        for encounter in evidence.encounters:
            if encounter.risk.presence is RiskPresence.PRESENT and encounter.risk.risk_id:
                return encounter.risk.risk_id
    return None


def _advisory(
    invocations: Mapping[str, DecisionToolInvocation],
) -> Sprint4AdvisoryConstraintReport:
    found = False
    for invocation in invocations.values():
        if _tree_has_directive(invocation.raw_tool_result.data):
            found = True
        if _tree_has_directive(invocation.model_projection.to_dict()):
            found = True
        result: ToolResult = invocation.raw_tool_result
        for limitation in result.limitations:
            if _text_has_directive(limitation):
                found = True
    limitations = [_ADVISORY_ONLY, _NOT_LOW, _NOT_MONITOR, _NOT_DIVERSION]
    if found:
        limitations.append(
            "Operational landing, diversion, or ATC directive text was "
            "withheld. Stored EVALUATE_DIVERSION remains advisory evidence."
        )
    return Sprint4AdvisoryConstraintReport(
        advisory_only=True,
        operational_directives_present=found,
        limitations=tuple(limitations),
    )


def _tree_has_directive(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _SKIP_DIRECTIVE_KEYS:
                continue
            if _tree_has_directive(item):
                return True
        return False
    if isinstance(value, list):
        return any(_tree_has_directive(item) for item in value)
    if isinstance(value, str):
        return _text_has_directive(value)
    return False


def _text_has_directive(value: str) -> bool:
    folded = value.casefold()
    return any(fragment in folded for fragment in _DIRECTIVE_FRAGMENTS)
