"""DT3 narrow risk and recommendation tools project the DT2 mapped chain."""

from __future__ import annotations

import inspect

import pytest

from tests.contracts.test_decision_context import (
    AS_OF,
    FUTURE,
    NOW,
    PAST,
    RaisingTable,
    _aircraft,
    _chain,
    _encounter,
    _hazard,
    _projection,
    _recommendation,
    _risk,
    _tables,
)
from wilvor_ai.contracts import TemporalScope, ToolResultStatus
from wilvor_ai.decision_context import (
    RECOMMENDATION_TOOL_NAME,
    RISK_TOOL_NAME,
    TOOL_NAME,
    DecisionContextCall,
    get_current_decision_context,
    get_current_recommendation,
    get_current_risk_evidence,
)
from wilvor_ai.decision_contracts import (
    FORBIDDEN_DECISION_KEYS,
    DecisionAdvisoryAuthority,
    DecisionChainGap,
    DecisionEvaluationState,
    DecisionEvidenceKind,
    DecisionLimitationCode,
    RecommendationActionType,
    RiskPresence,
    StoredRiskLevel,
    validate_decision_tool_result,
)
from wilvor_operational.linking import RECOMMENDATION_ABSENCE_LIMITATION


def _checked(tool, tables, aircraft_id="abc123"):
    call = DecisionContextCall(
        tables=tables,
        now_epoch=NOW,
        tool_call_id="tool-dt3",
        correlation_id="corr-dt3",
    )
    result = tool(call, aircraft_id)
    evidence = validate_decision_tool_result(result)
    assert result.temporal_scope is TemporalScope.CURRENT
    assert result.as_of_utc == AS_OF
    assert result.correlation_id == "corr-dt3"
    assert all(item.temporal_scope is TemporalScope.CURRENT for item in result.evidence)
    assert evidence.capability is None
    assert result.data["capability"] is None
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


def _two_encounters():
    projection = _projection()
    hazard_a = _hazard()
    hazard_b = _hazard()
    hazard_b["hazard_id"] = "hazard-2"
    encounter_a = _encounter()
    encounter_b = _encounter(
        encounter_id="proj-1#hazard-2#v1",
    )
    encounter_b["hazard_id"] = "hazard-2"
    risk_a = _risk(level="LOW", score=12, risk_id="risk-1")
    risk_b = _risk(level="HIGH", score=80, risk_id="risk-2")
    risk_b["encounter_id"] = encounter_b["encounter_id"]
    return _tables(
        aircraft=_aircraft(),
        projections=[projection],
        projection_records={"proj-1": projection},
        hazards=[hazard_a, hazard_b],
        hazard_records={"hazard-1": hazard_a, "hazard-2": hazard_b},
        encounters=[encounter_a, encounter_b],
        encounter_records={
            encounter_a["encounter_id"]: encounter_a,
            encounter_b["encounter_id"]: encounter_b,
        },
        risks=[risk_a, risk_b],
        risk_records={"risk-1": risk_a, "risk-2": risk_b},
        recommendations=[
            _recommendation("rec-1", risk_id="risk-1"),
            _recommendation("rec-2", "EVALUATE_DIVERSION", risk_id="risk-2"),
        ],
    )


def test_risk_aircraft_not_found():
    result, evidence = _checked(get_current_risk_evidence, _tables(), "missing")

    assert result.status is ToolResultStatus.NOT_FOUND
    assert result.tool_name == RISK_TOOL_NAME
    assert evidence.kind is DecisionEvidenceKind.RISK_EVIDENCE
    assert evidence.risk.presence is RiskPresence.ABSENT
    assert evidence.risk.risk_level is None
    assert evidence.recommendations is None
    assert evidence.encounters == ()


def test_risk_expired_aircraft_is_not_found():
    result, evidence = _checked(
        get_current_risk_evidence,
        _tables(aircraft=_aircraft(expires_at_epoch=NOW)),
        "ABC123",
    )

    assert result.status is ToolResultStatus.NOT_FOUND
    assert evidence.aircraft_in_current_set is False
    assert evidence.risk.presence is RiskPresence.ABSENT
    assert evidence.risk.risk_id is None


def test_risk_without_projection_is_partial():
    result, evidence = _checked(get_current_risk_evidence, _tables(aircraft=_aircraft()))

    assert result.status is ToolResultStatus.PARTIAL
    assert DecisionChainGap.NO_CURRENT_PROJECTION in evidence.chain_gaps
    assert DecisionChainGap.RISK_ABSENT in evidence.chain_gaps
    assert evidence.risk.presence is RiskPresence.ABSENT


def test_risk_without_encounter_is_partial():
    projection = _projection()
    result, evidence = _checked(
        get_current_risk_evidence,
        _tables(
            aircraft=_aircraft(),
            projections=[projection],
            projection_records={"proj-1": projection},
        ),
    )

    assert result.status is ToolResultStatus.PARTIAL
    assert DecisionChainGap.NO_CURRENT_ENCOUNTER in evidence.chain_gaps
    assert evidence.risk.presence is RiskPresence.ABSENT
    assert evidence.risk.risk_level is None
    assert "no hazard" not in str(result.data).lower()


def test_risk_missing_on_encounter_stays_absent():
    result, evidence = _checked(get_current_risk_evidence, _chain(include_risk=False))

    assert result.status is ToolResultStatus.PARTIAL
    assert evidence.risk.presence is RiskPresence.ABSENT
    assert evidence.risk.encounter_id == "proj-1#hazard-1#v1"
    assert evidence.risk.risk_level is None
    assert evidence.risk.risk_score is None
    assert evidence.recommendations is None
    assert DecisionChainGap.RECOMMENDATION_ABSENT not in evidence.chain_gaps


def test_stored_low_risk_is_not_absence():
    _, evidence = _checked(
        get_current_risk_evidence,
        _chain(level="LOW", score=12, recommendations=[_recommendation()]),
    )

    assert evidence.risk.presence is RiskPresence.PRESENT
    assert evidence.risk.risk_level is StoredRiskLevel.LOW
    assert evidence.risk.risk_score == 12
    assert "component_score" not in evidence.risk.to_dict()
    assert evidence.recommendations is None


@pytest.mark.parametrize(
    ("level", "score"),
    [("MEDIUM", 40), ("HIGH", 80)],
)
def test_stored_medium_and_high_risk(level, score):
    result, evidence = _checked(
        get_current_risk_evidence,
        _chain(level=level, score=score, recommendations=[_recommendation()]),
    )

    assert result.status is ToolResultStatus.SUCCESS
    assert evidence.risk.risk_level is StoredRiskLevel(level)
    assert evidence.risk.risk_score == score
    assert evidence.evaluation_state is DecisionEvaluationState.ESTABLISHED


def _winner_keys(value):
    return _keys(value) & (
        FORBIDDEN_DECISION_KEYS
        | {
            "preferred_encounter",
            "ranked_encounter",
            "selected_encounter",
            "winner",
            "rank",
        }
    )


def test_multiple_encounters_keep_both_risks_without_a_winner():
    result, evidence = _checked(get_current_risk_evidence, _two_encounters())

    assert result.status is ToolResultStatus.SUCCESS
    assert evidence.kind is DecisionEvidenceKind.RISK_EVIDENCE
    assert evidence.risk is None
    assert evidence.recommendations is None
    assert evidence.alerts == ()
    assert [item.risk.risk_id for item in evidence.encounters] == ["risk-1", "risk-2"]
    assert DecisionChainGap.INCOMPLETE_CURRENT_CHAIN not in evidence.chain_gaps
    assert _winner_keys(result.data) == set()


def test_stale_risk_is_not_reported_as_present():
    projection = _projection()
    hazard = _hazard()
    encounter = _encounter()
    stale = _risk(level="LOW", score=1, risk_id="risk-stale")
    stale["valid_until_utc"] = PAST
    _, evidence = _checked(
        get_current_risk_evidence,
        _tables(
            aircraft=_aircraft(),
            projections=[projection],
            projection_records={"proj-1": projection},
            hazards=[hazard],
            hazard_records={"hazard-1": hazard},
            encounters=[encounter],
            encounter_records={encounter["encounter_id"]: encounter},
            risks=[stale],
            risk_records={"risk-stale": stale},
        ),
    )

    assert evidence.risk.presence is RiskPresence.ABSENT
    assert evidence.risk.risk_id is None
    assert evidence.risk.risk_level is None


def test_missing_risk_never_becomes_low_or_zero():
    _, evidence = _checked(get_current_risk_evidence, _chain(include_risk=False))
    payload = evidence.risk.to_dict()

    assert payload["presence"] == "ABSENT"
    assert payload["risk_level"] is None
    assert payload["risk_score"] is None


def test_hazard_mismatch_is_not_lifted_as_stored_risk():
    projection = _projection()
    selected = _hazard(source_version="v1")
    live = _hazard(source_version="v2")
    encounter = _encounter()
    risk = _risk(level="HIGH", score=80)
    result, evidence = _checked(
        get_current_risk_evidence,
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
        ),
    )

    assert result.status is ToolResultStatus.PARTIAL
    assert DecisionChainGap.INCOMPLETE_CURRENT_CHAIN in evidence.chain_gaps
    assert DecisionChainGap.HAZARD_SOURCE_VERSION_MISMATCH not in evidence.chain_gaps
    assert evidence.risk.presence is RiskPresence.ABSENT
    assert evidence.risk.risk_id is None
    assert any("v1" in item and "v2" in item for item in result.limitations)


def test_risk_reader_failure_is_unavailable():
    result, evidence = _checked(
        get_current_risk_evidence,
        _tables(aircraft_table=RaisingTable()),
    )

    assert result.status is ToolResultStatus.UNAVAILABLE
    assert evidence.kind is DecisionEvidenceKind.RISK_EVIDENCE
    assert evidence.evaluation_state is DecisionEvaluationState.SOURCE_UNAVAILABLE
    assert evidence.risk.presence is RiskPresence.ABSENT
    assert evidence.chain_gaps == ()
    assert result.evidence == ()


def test_recommendation_aircraft_not_found():
    result, evidence = _checked(get_current_recommendation, _tables(), "missing")

    assert result.status is ToolResultStatus.NOT_FOUND
    assert result.tool_name == RECOMMENDATION_TOOL_NAME
    assert evidence.kind is DecisionEvidenceKind.RECOMMENDATION_EVIDENCE
    assert evidence.recommendations.current == ()
    assert "MONITOR" not in str(result.data)


def test_recommendation_without_projection_or_encounter_is_partial():
    projection = _projection()
    missing_projection, _ = _checked(
        get_current_recommendation,
        _tables(aircraft=_aircraft()),
    )
    missing_encounter, evidence = _checked(
        get_current_recommendation,
        _tables(
            aircraft=_aircraft(),
            projections=[projection],
            projection_records={"proj-1": projection},
        ),
    )

    assert missing_projection.status is ToolResultStatus.PARTIAL
    assert missing_encounter.status is ToolResultStatus.PARTIAL
    assert DecisionChainGap.NO_CURRENT_ENCOUNTER in evidence.chain_gaps
    assert evidence.recommendations.current == ()


def test_recommendation_when_risk_is_missing_is_partial():
    result, evidence = _checked(get_current_recommendation, _chain(include_risk=False))

    assert result.status is ToolResultStatus.PARTIAL
    assert evidence.risk.presence is RiskPresence.ABSENT
    assert evidence.recommendations.current == ()
    assert RecommendationActionType.MONITOR.value not in str(result.data)


def test_proven_recommendation_absence_is_success():
    result, evidence = _checked(get_current_recommendation, _chain(recommendations=[]))

    assert result.status is ToolResultStatus.SUCCESS
    assert evidence.risk.presence is RiskPresence.PRESENT
    assert evidence.recommendations.current == ()
    assert DecisionChainGap.RECOMMENDATION_ABSENT in evidence.chain_gaps
    assert (
        DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION
        in evidence.limitation_codes
    )
    assert RECOMMENDATION_ABSENCE_LIMITATION in result.limitations
    assert "MONITOR" not in str(result.data)


def test_one_recommendation_keeps_provenance_and_advisory_authority():
    _, evidence = _checked(
        get_current_recommendation,
        _chain(recommendations=[_recommendation(action="EVALUATE_DIVERSION")]),
    )
    item = evidence.recommendations.current[0]

    assert item.recommendation_id == "rec-1"
    assert item.primary_action_type is RecommendationActionType.EVALUATE_DIVERSION
    assert item.advisory_authority is DecisionAdvisoryAuthority.ADVISORY_ONLY
    assert item.advisory_notice == "Advisory only. Not a clearance."
    assert item.evidence_references[0].record_id == "risk-1"
    assert item.source_versions.hazard_source_version == "v1"
    assert item.source_versions.scoring_ruleset_version is None
    assert item.airport_evaluation.airport_evaluation_id == "eval-1"
    assert "candidate_airport_summaries" not in item.to_dict()


def test_multiple_recommendations_keep_candidate_order_and_no_winner():
    result, evidence = _checked(
        get_current_recommendation,
        _chain(
            recommendations=[
                _recommendation("rec-1", "MONITOR"),
                _recommendation("rec-2", "EVALUATE_DIVERSION"),
            ]
        ),
    )

    assert result.status is ToolResultStatus.SUCCESS
    assert [item.recommendation_id for item in evidence.recommendations.current] == [
        "rec-1",
        "rec-2",
    ]
    assert FORBIDDEN_DECISION_KEYS.isdisjoint(_keys(result.data))


def test_stale_recommendation_is_omitted_without_excluded_ids():
    _, evidence = _checked(
        get_current_recommendation,
        _chain(
            recommendations=[
                _recommendation("rec-current"),
                _recommendation("rec-stale", valid_until_utc=PAST),
            ]
        ),
    )

    assert [item.recommendation_id for item in evidence.recommendations.current] == [
        "rec-current"
    ]
    assert evidence.recommendations.excluded_stale_ids == ()


def test_recommendation_for_another_risk_is_excluded():
    _, evidence = _checked(
        get_current_recommendation,
        _chain(
            recommendations=[
                _recommendation("rec-1"),
                _recommendation("rec-other", risk_id="risk-other"),
            ]
        ),
    )

    assert [item.recommendation_id for item in evidence.recommendations.current] == [
        "rec-1"
    ]


def test_multiple_encounters_keep_both_recommendation_sets():
    result, evidence = _checked(get_current_recommendation, _two_encounters())

    assert result.status is ToolResultStatus.SUCCESS
    assert evidence.kind is DecisionEvidenceKind.RECOMMENDATION_EVIDENCE
    assert evidence.risk is None
    assert evidence.recommendations is None
    assert evidence.alerts == ()
    assert [
        item.recommendations.current[0].recommendation_id for item in evidence.encounters
    ] == ["rec-1", "rec-2"]
    assert [item.risk.risk_id for item in evidence.encounters] == ["risk-1", "risk-2"]
    assert DecisionChainGap.INCOMPLETE_CURRENT_CHAIN not in evidence.chain_gaps
    assert _winner_keys(result.data) == set()


def test_one_missing_risk_among_two_encounters_stays_partial():
    tables = _two_encounters()
    tables.risks.records.pop("risk-2")
    result, evidence = _checked(get_current_risk_evidence, tables)

    assert result.status is ToolResultStatus.PARTIAL
    assert DecisionChainGap.RISK_ABSENT in evidence.chain_gaps
    assert DecisionChainGap.INCOMPLETE_CURRENT_CHAIN not in evidence.chain_gaps
    assert evidence.risk is None
    assert [item.encounter_id for item in evidence.encounters] == [
        "proj-1#hazard-1#v1",
        "proj-1#hazard-2#v1",
    ]
    assert evidence.encounters[0].risk.risk_id == "risk-1"
    assert evidence.encounters[1].risk.presence is RiskPresence.ABSENT
    assert evidence.encounters[1].risk.risk_id is None


def test_dt2_two_encounters_remain_a_context_with_capability_and_no_winner():
    call = DecisionContextCall(
        tables=_two_encounters(),
        now_epoch=NOW,
        tool_call_id="tool-dt2",
        correlation_id="corr-dt2",
    )
    result = get_current_decision_context(call, "abc123")
    evidence = validate_decision_tool_result(result)

    assert result.tool_name == TOOL_NAME
    assert result.status is ToolResultStatus.SUCCESS
    assert evidence.kind is DecisionEvidenceKind.DECISION_CONTEXT
    assert evidence.risk is None
    assert evidence.recommendations is None
    assert [item.risk.risk_id for item in evidence.encounters] == ["risk-1", "risk-2"]
    assert evidence.capability is not None
    assert evidence.capability.validated_alternative_available is False
    assert _winner_keys(result.data) == set()


def test_recommendation_reader_failure_keeps_recommendation_kind():
    result, evidence = _checked(
        get_current_recommendation,
        _tables(aircraft_table=RaisingTable()),
    )

    assert result.status is ToolResultStatus.UNAVAILABLE
    assert result.tool_name == RECOMMENDATION_TOOL_NAME
    assert result.temporal_scope is TemporalScope.CURRENT
    assert result.evidence == ()
    assert result.limitations == ("The operational context read failed.",)
    assert evidence.kind is DecisionEvidenceKind.RECOMMENDATION_EVIDENCE
    assert evidence.evaluation_state is DecisionEvaluationState.SOURCE_UNAVAILABLE
    assert evidence.encounters == ()
    assert evidence.capability is None
    assert evidence.risk is not None
    assert evidence.risk.presence is RiskPresence.ABSENT
    assert evidence.risk.risk_level is None
    assert evidence.recommendations is None
    assert DecisionChainGap.RECOMMENDATION_ABSENT not in evidence.chain_gaps
    assert (
        DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION
        not in evidence.limitation_codes
    )
    assert RecommendationActionType.MONITOR.value not in str(result.data)


def test_narrow_tools_do_not_accept_model_time():
    assert tuple(inspect.signature(get_current_risk_evidence).parameters) == (
        "call",
        "aircraft_id",
    )
    assert tuple(inspect.signature(get_current_recommendation).parameters) == (
        "call",
        "aircraft_id",
    )
    call = DecisionContextCall(tables=_tables(), now_epoch=NOW, tool_call_id="tool-dt3")
    with pytest.raises(TypeError):
        get_current_risk_evidence(call, "abc123", now_epoch=NOW)
    with pytest.raises(TypeError):
        get_current_recommendation(call, "abc123", as_of_utc=FUTURE)
