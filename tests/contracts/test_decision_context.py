"""DT2 get_current_decision_context maps operational context into DT1 evidence."""

from __future__ import annotations

import inspect

import pytest

from wilvor_ai.contracts import TemporalScope, ToolResultStatus
from wilvor_ai.decision_context import (
    TOOL_NAME,
    DecisionContextCall,
    get_current_decision_context,
)
from wilvor_ai.decision_contracts import (
    FORBIDDEN_DECISION_KEYS,
    V1_UNAVAILABLE_DECISION_CAPABILITIES,
    AlertCurrentLineage,
    DecisionAdvisoryAuthority,
    DecisionChainGap,
    DecisionEvaluationState,
    DecisionLimitationCode,
    DecisionReportedLinkState,
    RecommendationActionType,
    RiskPresence,
    StoredRiskLevel,
    validate_decision_tool_result,
)
from wilvor_operational import context
from wilvor_operational import readers
from wilvor_operational.linking import (
    NO_SNAPSHOT_LIMITATION,
    RECOMMENDATION_ABSENCE_LIMITATION,
)


NOW = 1_788_661_800
FUTURE = "2026-09-06T04:00:00Z"
PAST = "2020-01-01T00:00:00Z"
AS_OF = context._epoch_to_utc_z(NOW)


class ScriptedTable:
    def __init__(self, *, scan_items=None, records=None):
        self.scan_items = list(scan_items or [])
        self.records = dict(records or {})

    def scan(self, **kwargs):
        return {"Items": list(self.scan_items)}

    def get_item(self, **kwargs):
        key = kwargs["Key"]
        value = next(iter(key.values()))
        item = self.records.get(value)
        if item is None:
            return {}
        return {"Item": item}


class RaisingTable:
    def scan(self, **kwargs):
        raise RuntimeError("operational read failed")

    def get_item(self, **kwargs):
        raise RuntimeError("operational read failed")


def _tables(
    *,
    aircraft=None,
    aircraft_id="abc123",
    projections=None,
    projection_records=None,
    hazards=None,
    hazard_records=None,
    encounters=None,
    encounter_records=None,
    risks=None,
    risk_records=None,
    recommendations=None,
    alerts=None,
    aircraft_table=None,
):
    return readers.OperationalTables(
        aircraft=aircraft_table
        or ScriptedTable(records={aircraft_id: aircraft} if aircraft else {}),
        projections=ScriptedTable(
            scan_items=projections or [],
            records=projection_records or {},
        ),
        projection_points=ScriptedTable(),
        hazards=ScriptedTable(scan_items=hazards or [], records=hazard_records or {}),
        hazard_coordinates=ScriptedTable(),
        encounters=ScriptedTable(
            scan_items=encounters or [],
            records=encounter_records or {},
        ),
        risks=ScriptedTable(scan_items=risks or [], records=risk_records or {}),
        airports=ScriptedTable(),
        metar=ScriptedTable(),
        taf=ScriptedTable(),
        taf_periods=ScriptedTable(),
        airport_assessments=ScriptedTable(),
        recommendations=ScriptedTable(scan_items=recommendations or []),
        alerts=ScriptedTable(scan_items=alerts or []),
    )


def _aircraft(expires_at_epoch=NOW + 100):
    return {"aircraft_id": "abc123", "expires_at_epoch": expires_at_epoch}


def _projection():
    return {
        "aircraft_id": "abc123",
        "projection_id": "proj-1",
        "generated_at_epoch": NOW,
        "valid_until_epoch": NOW + 1000,
        "projection_status": "READY",
        "aircraft_state_version": "abc123#1",
    }


def _hazard(source_version="v1"):
    return {
        "hazard_id": "hazard-1",
        "source_version": source_version,
        "status": "ACTIVE",
        "materialization_status": "READY",
        "valid_to_epoch": NOW + 1000,
    }


def _encounter(hazard_source_version="v1", encounter_id="proj-1#hazard-1#v1"):
    return {
        "encounter_id": encounter_id,
        "aircraft_id": "abc123",
        "projection_id": "proj-1",
        "hazard_id": "hazard-1",
        "hazard_source_version": hazard_source_version,
        "encounter_state": "DETECTED",
    }


def _risk(level="LOW", score=12, risk_id="risk-1"):
    return {
        "risk_id": risk_id,
        "encounter_id": "proj-1#hazard-1#v1",
        "generated_at_epoch": NOW,
        "generated_at_utc": "2026-09-06T03:00:00Z",
        "risk_level": level,
        "risk_score": score,
        "confidence": "HIGH",
        "scoring_ruleset_version": "risk-rules-1",
        "reasons": ["stored reason"],
        "limitations": ["stored limitation"],
        "component_score": 99,
    }


def _recommendation(
    recommendation_id="rec-1",
    action="MONITOR",
    *,
    valid_until_utc=FUTURE,
    risk_id="risk-1",
):
    return {
        "recommendation_id": recommendation_id,
        "recommendation_version": "ver-1",
        "ruleset_version": "rec-rules-1",
        "recommendation_status": "ACTIVE",
        "valid_from_utc": "2026-09-06T03:00:00Z",
        "valid_until_utc": valid_until_utc,
        "risk_id": risk_id,
        "primary_action_type": action,
        "advisory_notice": "Advisory only. Not a clearance.",
        "confidence": "MEDIUM",
        "reasons": ["stored recommendation reason"],
        "limitations": ["stored recommendation limitation"],
        "evidence_references": [
            {"type": "RISK_RESULT", "id": risk_id},
            {
                "type": "AIRPORT_ASSESSMENT",
                "id": "assess-1",
                "airport_id": "KSFO",
            },
        ],
        "source_versions": {
            "hazard_source_version": "v1",
            "risk_schema_version": "risk-schema-1",
            "airport_evaluation_id": "eval-1",
            "scoring_ruleset_version": "UNKNOWN",
        },
        "airport_evaluation_id": "eval-1",
        "candidate_airport_summaries": [{"airport_id": "KSFO", "rank": 1}],
    }


def _alert(alert_id, *, risk_id=None, recommendation_id=None):
    item = {
        "alert_id": alert_id,
        "alert_state": "NEW",
        "valid_until_utc": FUTURE,
    }
    if risk_id is not None:
        item["risk_id"] = risk_id
    if recommendation_id is not None:
        item["recommendation_id"] = recommendation_id
    return item


def _call():
    return DecisionContextCall(
        tables=None,
        now_epoch=NOW,
        tool_call_id="tool-dt2",
        correlation_id="corr-dt2",
    )


def _checked(tables, aircraft_id="abc123"):
    call = DecisionContextCall(
        tables=tables,
        now_epoch=NOW,
        tool_call_id="tool-dt2",
        correlation_id="corr-dt2",
    )
    result = get_current_decision_context(call, aircraft_id)
    evidence = validate_decision_tool_result(result)
    assert result.tool_name == TOOL_NAME
    assert result.temporal_scope is TemporalScope.CURRENT
    assert result.as_of_utc == AS_OF
    assert result.correlation_id == "corr-dt2"
    assert all(item.temporal_scope is TemporalScope.CURRENT for item in result.evidence)
    assert all(item.tool_call_id == "tool-dt2" for item in result.evidence)
    return result, evidence


def _keys(value):
    found = set()

    def walk(item):
        if isinstance(item, dict):
            found.update(item)
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return found


def _chain(
    *,
    level="LOW",
    score=12,
    recommendations=None,
    alerts=None,
    include_risk=True,
    extra_encounters=None,
):
    projection = _projection()
    hazard = _hazard()
    encounter = _encounter()
    encounters = [encounter]
    encounter_records = {encounter["encounter_id"]: encounter}
    if extra_encounters:
        encounters.extend(extra_encounters)
    risk = _risk(level=level, score=score)
    return _tables(
        aircraft=_aircraft(),
        projections=[projection],
        projection_records={"proj-1": projection},
        hazards=[hazard],
        hazard_records={"hazard-1": hazard},
        encounters=encounters,
        encounter_records=encounter_records,
        risks=[risk] if include_risk else [],
        risk_records={"risk-1": risk} if include_risk else {},
        recommendations=recommendations,
        alerts=alerts,
    )


def test_01_missing_aircraft_is_not_found_without_a_chain():
    result, evidence = _checked(_tables(), "missing")

    assert result.status is ToolResultStatus.NOT_FOUND
    assert result.evidence == ()
    assert evidence.aircraft_in_current_set is False
    assert evidence.encounters == ()
    assert evidence.risk is None
    assert evidence.recommendations is None
    assert evidence.alerts == ()
    assert DecisionChainGap.AIRCRAFT_NOT_IN_CURRENT_SET in evidence.chain_gaps


def test_expired_aircraft_is_not_found_without_descendants():
    result, evidence = _checked(
        _tables(aircraft=_aircraft(expires_at_epoch=NOW)),
        "ABC123",
    )

    assert result.status is ToolResultStatus.NOT_FOUND
    assert evidence.aircraft_id == "abc123"
    assert evidence.encounters == ()
    assert evidence.risk is None
    assert evidence.recommendations is None


def test_02_aircraft_without_projection_is_partial():
    result, evidence = _checked(_tables(aircraft=_aircraft()))

    assert result.status is ToolResultStatus.PARTIAL
    assert DecisionChainGap.NO_CURRENT_PROJECTION in evidence.chain_gaps
    assert evidence.projection_id is None
    assert evidence.encounters == ()
    assert result.evidence[0].source == "get_aircraft_record"


def test_03_projection_without_encounter_is_partial():
    projection = _projection()
    result, evidence = _checked(
        _tables(
            aircraft=_aircraft(),
            projections=[projection],
            projection_records={"proj-1": projection},
        )
    )

    assert result.status is ToolResultStatus.PARTIAL
    assert DecisionChainGap.NO_CURRENT_ENCOUNTER in evidence.chain_gaps
    assert evidence.encounters == ()
    assert evidence.recommendations is None
    assert "MONITOR" not in str(result.data)
    assert "no hazard" not in str(result.data).lower()


def test_04_encounter_without_risk_stays_absent():
    result, evidence = _checked(_chain(include_risk=False))
    risk = evidence.encounters[0].risk

    assert result.status is ToolResultStatus.PARTIAL
    assert DecisionChainGap.RISK_ABSENT in evidence.chain_gaps
    assert risk.presence is RiskPresence.ABSENT
    assert risk.risk_level is None
    assert risk.risk_score is None
    assert risk.risk_id is None
    assert "LOW" not in str(risk.to_dict())
    assert risk.to_dict()["risk_score"] is None


def test_05_stored_low_risk_stays_low():
    _, evidence = _checked(_chain(level="LOW", score=12, recommendations=[_recommendation()]))
    risk = evidence.encounters[0].risk

    assert risk.presence is RiskPresence.PRESENT
    assert risk.risk_level is StoredRiskLevel.LOW
    assert risk.risk_score == 12
    assert "component_score" not in risk.to_dict()


@pytest.mark.parametrize(
    ("level", "score"),
    [("MEDIUM", 40), ("HIGH", 80)],
)
def test_06_stored_medium_and_high_use_the_stored_values(level, score):
    _, evidence = _checked(
        _chain(level=level, score=score, recommendations=[_recommendation()])
    )
    risk = evidence.encounters[0].risk

    assert risk.presence is RiskPresence.PRESENT
    assert risk.risk_level is StoredRiskLevel(level)
    assert risk.risk_score == score


def test_07_proven_recommendation_absence_is_success_and_not_monitor():
    result, evidence = _checked(_chain(recommendations=[]))
    recommendations = evidence.encounters[0].recommendations

    assert result.status is ToolResultStatus.SUCCESS
    assert recommendations.current == ()
    assert (
        recommendations.absence_state
        is DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES
    )
    assert DecisionChainGap.RECOMMENDATION_ABSENT in evidence.chain_gaps
    assert (
        DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION
        in evidence.limitation_codes
    )
    assert RECOMMENDATION_ABSENCE_LIMITATION in result.limitations
    assert RecommendationActionType.MONITOR.value not in str(result.data)


def test_08_one_recommendation_keeps_provenance():
    _, evidence = _checked(_chain(recommendations=[_recommendation()]))
    item = evidence.encounters[0].recommendations.current[0]

    assert item.recommendation_id == "rec-1"
    assert item.primary_action_type is RecommendationActionType.MONITOR
    assert item.advisory_authority is DecisionAdvisoryAuthority.ADVISORY_ONLY
    assert item.advisory_notice == "Advisory only. Not a clearance."
    assert item.evidence_references[0].record_id == "risk-1"
    assert item.source_versions.hazard_source_version == "v1"
    assert item.source_versions.scoring_ruleset_version is None
    assert item.airport_evaluation.airport_evaluation_id == "eval-1"
    assert "candidate_airport_summaries" not in item.to_dict()


def test_09_multiple_recommendations_have_no_winner():
    _, evidence = _checked(
        _chain(
            recommendations=[
                _recommendation("rec-1", "MONITOR"),
                _recommendation("rec-2", "EVALUATE_DIVERSION"),
            ]
        )
    )
    current = evidence.encounters[0].recommendations.current
    payload = evidence.to_dict()

    assert [item.recommendation_id for item in current] == ["rec-1", "rec-2"]
    assert evidence.recommendations is None
    assert evidence.risk is None
    assert FORBIDDEN_DECISION_KEYS.isdisjoint(_keys(payload))


def test_10_stale_recommendation_is_omitted_without_excluded_ids():
    _, evidence = _checked(
        _chain(
            recommendations=[
                _recommendation("rec-current"),
                _recommendation("rec-stale", valid_until_utc=PAST),
            ]
        )
    )
    recommendations = evidence.encounters[0].recommendations

    assert [item.recommendation_id for item in recommendations.current] == ["rec-current"]
    assert recommendations.excluded_stale_ids == ()
    assert DecisionChainGap.STALE_RECOMMENDATION_EXCLUDED not in evidence.chain_gaps


def test_11_hazard_version_mismatch_is_reported_and_not_repaired():
    projection = _projection()
    selected = _hazard(source_version="v1")
    live = _hazard(source_version="v2")
    encounter = _encounter()
    risk = _risk(level="HIGH", score=80)
    result, evidence = _checked(
        _tables(
            aircraft=_aircraft(),
            projections=[projection],
            projection_records={"proj-1": projection},
            hazards=[selected],
            hazard_records={"hazard-1": live},
            encounters=[encounter],
            encounter_records={encounter["encounter_id"]: encounter},
            risks=[risk],
            risk_records={"risk-1": risk},
            recommendations=[_recommendation(action="EVALUATE_DIVERSION")],
        )
    )
    hazard = evidence.encounters[0].hazard

    assert result.status is ToolResultStatus.PARTIAL
    assert hazard.state is DecisionReportedLinkState.HYDRATION_VERSION_MISMATCH
    assert hazard.persisted_source_version == "v1"
    assert hazard.current_source_version == "v2"
    assert DecisionChainGap.HAZARD_SOURCE_VERSION_MISMATCH in evidence.chain_gaps

    excluded, excluded_evidence = _checked(
        _tables(
            aircraft=_aircraft(),
            projections=[projection],
            projection_records={"proj-1": projection},
            hazards=[selected],
            hazard_records={"hazard-1": selected},
            encounters=[_encounter(hazard_source_version="v-old", encounter_id="old")],
            encounter_records={},
        )
    )
    assert excluded.status is ToolResultStatus.PARTIAL
    assert excluded_evidence.encounters == ()
    assert DecisionChainGap.NO_CURRENT_ENCOUNTER in excluded_evidence.chain_gaps


def test_12_advisory_authority_is_retained():
    _, evidence = _checked(
        _chain(recommendations=[_recommendation(action="EVALUATE_DIVERSION")])
    )
    item = evidence.encounters[0].recommendations.current[0]

    assert item.advisory_notice == "Advisory only. Not a clearance."
    assert item.advisory_authority is DecisionAdvisoryAuthority.ADVISORY_ONLY
    assert item.primary_action_type is RecommendationActionType.EVALUATE_DIVERSION


def test_13_route_capability_is_explicitly_unavailable():
    _, evidence = _checked(_tables(), "missing")

    assert evidence.capability.validated_alternative_available is False
    assert evidence.capability.unavailable == V1_UNAVAILABLE_DECISION_CAPABILITIES


def test_14_payload_has_no_route_fields():
    result, evidence = _checked(_chain(recommendations=[_recommendation()]))

    assert FORBIDDEN_DECISION_KEYS.isdisjoint(_keys(result.data))
    assert FORBIDDEN_DECISION_KEYS.isdisjoint(_keys(evidence.to_dict()))


def _alert_chain():
    return _chain(
        level="HIGH",
        score=80,
        recommendations=[
            _recommendation("rec-1", "MONITOR"),
            _recommendation("rec-2", "EVALUATE_DIVERSION"),
        ],
        alerts=[
            _alert("alert-risk", risk_id="risk-1", recommendation_id="rec-other"),
            _alert("alert-rec", recommendation_id="rec-2"),
            _alert("alert-both", risk_id="risk-1", recommendation_id="rec-1"),
        ],
    )


def test_15_alert_linked_only_by_risk():
    _, evidence = _checked(_alert_chain())
    alert = next(item for item in evidence.alerts if item.alert_id == "alert-risk")

    assert alert.lineage is AlertCurrentLineage.RISK
    assert alert.risk_id == "risk-1"
    assert alert.recommendation_id is None


def test_16_alert_linked_only_by_recommendation():
    _, evidence = _checked(_alert_chain())
    alert = next(item for item in evidence.alerts if item.alert_id == "alert-rec")

    assert alert.lineage is AlertCurrentLineage.RECOMMENDATION
    assert alert.recommendation_id == "rec-2"
    assert alert.risk_id is None


def test_17_alert_linked_by_both():
    _, evidence = _checked(_alert_chain())
    alert = next(item for item in evidence.alerts if item.alert_id == "alert-both")

    assert alert.lineage is AlertCurrentLineage.BOTH
    assert alert.risk_id == "risk-1"
    assert alert.recommendation_id == "rec-1"


def test_18_no_snapshot_limitation_does_not_force_partial():
    result, evidence = _checked(_chain(recommendations=[_recommendation()]))

    assert result.status is ToolResultStatus.SUCCESS
    assert DecisionLimitationCode.NO_SNAPSHOT_LIMITATION in evidence.limitation_codes
    assert NO_SNAPSHOT_LIMITATION in result.limitations


def test_19_time_and_tool_call_are_not_model_arguments():
    assert tuple(inspect.signature(get_current_decision_context).parameters) == (
        "call",
        "aircraft_id",
    )
    result, _evidence = _checked(_tables(aircraft=_aircraft()))

    assert result.as_of_utc == AS_OF
    assert "as_of" not in inspect.signature(get_current_decision_context).parameters
    assert "now_epoch" not in inspect.signature(get_current_decision_context).parameters
    with pytest.raises(TypeError):
        get_current_decision_context(_call(), "abc123", now_epoch=NOW)


def test_21_and_22_tool_result_is_current_and_validates():
    result, evidence = _checked(_chain(recommendations=[_recommendation()]))

    assert result.status is ToolResultStatus.SUCCESS
    assert result.temporal_scope is TemporalScope.CURRENT
    assert evidence.kind.value == "DECISION_CONTEXT"
    assert evidence.evaluation_state is DecisionEvaluationState.ESTABLISHED


def test_23_missing_risk_never_becomes_low_or_zero():
    _, evidence = _checked(_chain(include_risk=False))
    payload = evidence.encounters[0].risk.to_dict()

    assert payload["presence"] == "ABSENT"
    assert payload["risk_level"] is None
    assert payload["risk_score"] is None


def test_24_missing_recommendation_never_becomes_monitor():
    result, evidence = _checked(_chain(recommendations=[]))

    assert evidence.encounters[0].recommendations.current == ()
    assert "MONITOR" not in str(result.data)


def test_25_no_recommendation_winner_is_synthesized():
    result, _evidence = _checked(
        _chain(
            recommendations=[
                _recommendation("rec-1"),
                _recommendation("rec-2", "MONITOR_AND_PREPARE_OPTIONS"),
            ]
        )
    )

    assert FORBIDDEN_DECISION_KEYS.isdisjoint(_keys(result.data))
    assert "selected" not in _keys(result.data)


def test_source_failure_is_unavailable_without_a_chain():
    result, evidence = _checked(_tables(aircraft_table=RaisingTable()))

    assert result.status is ToolResultStatus.UNAVAILABLE
    assert evidence.evaluation_state is DecisionEvaluationState.SOURCE_UNAVAILABLE
    assert evidence.encounters == ()
    assert evidence.chain_gaps == ()
    assert result.evidence == ()
    assert result.limitations == ("The operational context read failed.",)


def test_unmappable_projection_link_does_not_become_present():
    current = _projection()
    expired = {**current, "valid_until_epoch": NOW}
    result, evidence = _checked(
        _tables(
            aircraft=_aircraft(),
            projections=[current],
            projection_records={"proj-1": expired},
            hazards=[_hazard()],
            hazard_records={"hazard-1": _hazard()},
            encounters=[_encounter()],
            encounter_records={"proj-1#hazard-1#v1": _encounter()},
        )
    )

    assert result.status is ToolResultStatus.PARTIAL
    assert evidence.projection_state is DecisionReportedLinkState.MISSING
    assert evidence.projection_id is None
    assert evidence.encounters == ()
    assert DecisionChainGap.NO_CURRENT_PROJECTION in evidence.chain_gaps
    assert any("HYDRATION_NO_LONGER_CURRENT" in item for item in result.limitations)
