"""DT5 Decision projection preserves deterministic facts and hides runtime fields."""

from __future__ import annotations

from wilvor_ai.contracts import (
    ConfidenceLevel,
    Evidence,
    FreshnessStatus,
    MatchCardinality,
    QueryExecutionTrace,
    SourceCompleteness,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.decision_contracts import (
    DecisionAdvisoryAuthority,
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
    RecommendationActionType,
    RiskPresence,
    StoredRiskLevel,
    expected_decision_status,
)
from wilvor_ai.decision_tool_projection import project_decision_tool_result
from wilvor_ai.persisted_airport_contracts import (
    PersistedAirportCandidate,
    PersistedAirportEvidence,
)
from wilvor_ai.persisted_airport_evidence import (
    EMPTY_ASSESSMENTS,
    NOT_CURRENT_LIMITATION,
)


AS_OF = "2026-09-30T18:00:00Z"
UNTIL = "2026-09-30T18:15:00Z"
QUERY = "2026-09-30T20:00:00Z"
ADVISORY = (
    "Advisory decision support only. Human operational review is required. "
    "Wilvor does not issue autonomous flight-control, diversion, landing, "
    "dispatch, or ATC instructions."
)


def _risk(level=StoredRiskLevel.LOW, risk_id="risk-1", encounter_id="enc-1"):
    return DecisionRiskEvidence(
        presence=RiskPresence.PRESENT,
        risk_id=risk_id,
        encounter_id=encounter_id,
        risk_level=level,
        risk_score=12,
    )


def _absent(encounter_id="enc-1"):
    return DecisionRiskEvidence(presence=RiskPresence.ABSENT, encounter_id=encounter_id)


def _recommendation(recommendation_id="rec-1", action=RecommendationActionType.MONITOR):
    return DecisionRecommendationEvidence(
        recommendation_id=recommendation_id,
        primary_action_type=action,
        advisory_authority=DecisionAdvisoryAuthority.ADVISORY_ONLY,
        advisory_notice=ADVISORY,
    )


def _set(*items):
    return DecisionRecommendationSet(
        current=tuple(items),
        absence_state=(
            DecisionReportedLinkState.PRESENT
            if items
            else DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES
        ),
    )


def _hazard():
    return HazardSourceVersionLink(
        hazard_id="haz-1",
        state=DecisionReportedLinkState.PRESENT,
        persisted_source_version="sv-1",
        current_source_version="sv-1",
    )


def _encounter(encounter_id, risk, recommendations):
    return DecisionEncounterLink(
        encounter_id=encounter_id,
        hazard=_hazard(),
        risk=risk,
        recommendations=recommendations,
        projection_id="proj-1",
        aircraft_state_version="state-1",
    )


def _project(evidence: DecisionEvidence, **overrides):
    status = expected_decision_status(evidence)
    limitations = ()
    if status is ToolResultStatus.PARTIAL:
        limitations = ("The current chain is partial.",)
    if status in {ToolResultStatus.UNAVAILABLE, ToolResultStatus.UNKNOWN}:
        limitations = ("The current chain is not established.",)
    evidence_items = ()
    if status in {ToolResultStatus.SUCCESS, ToolResultStatus.PARTIAL}:
        evidence_items = (
            Evidence(
                source="get_aircraft_record",
                source_records=(
                    SourceRecord(
                        record_id="n123ab",
                        source_version="state-1",
                        event_timestamp_utc=AS_OF,
                    ),
                ),
                query_timestamp_utc=QUERY,
                freshness_status=FreshnessStatus.FRESH,
                confidence=ConfidenceLevel.HIGH,
                limitations=(),
                tool_call_id="call-1",
                temporal_scope=TemporalScope.CURRENT,
                completeness=SourceCompleteness(status="COMPLETE"),
                match_cardinality=MatchCardinality(exact_count=1, is_exact=True),
                query_executions=(QueryExecutionTrace(query_id="query-1"),),
                error_code="trace-code",
            ),
        )
    values = {
        "tool_name": "get_current_decision_context",
        "tool_call_id": "call-1",
        "status": status,
        "temporal_scope": TemporalScope.CURRENT,
        "data": evidence.to_dict(),
        "evidence": evidence_items,
        "as_of_utc": AS_OF,
        "limitations": limitations,
        "correlation_id": "corr-1",
    }
    values.update(overrides)
    result = ToolResult(**values)
    return result, project_decision_tool_result(result)


def _keys(value):
    found = set()
    if isinstance(value, dict):
        found.update(value)
        for item in value.values():
            found.update(_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_keys(item))
    return found


def test_projection_preserves_low_absent_and_recommendation_counts():
    low, low_view = _project(
        DecisionEvidence(
            kind=DecisionEvidenceKind.RISK_EVIDENCE,
            evaluation_state=DecisionEvaluationState.ESTABLISHED,
            aircraft_in_current_set=True,
            aircraft_id="n123ab",
            projection_state=DecisionReportedLinkState.PRESENT,
            projection_id="proj-1",
            aircraft_state_version="state-1",
            risk=_risk(),
            chain_gaps=(),
            limitation_codes=(),
        ),
        tool_name="get_current_risk_evidence",
    )
    absent, absent_view = _project(
        DecisionEvidence(
            kind=DecisionEvidenceKind.RISK_EVIDENCE,
            evaluation_state=DecisionEvaluationState.ESTABLISHED,
            aircraft_in_current_set=True,
            aircraft_id="n123ab",
            projection_state=DecisionReportedLinkState.PRESENT,
            projection_id="proj-1",
            risk=_absent(),
            chain_gaps=(DecisionChainGap.RISK_ABSENT,),
            limitation_codes=(DecisionLimitationCode.NO_SNAPSHOT_LIMITATION,),
        ),
        tool_name="get_current_risk_evidence",
    )
    empty, empty_view = _project(
        DecisionEvidence(
            kind=DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
            evaluation_state=DecisionEvaluationState.ESTABLISHED,
            aircraft_in_current_set=True,
            aircraft_id="n123ab",
            projection_state=DecisionReportedLinkState.PRESENT,
            projection_id="proj-1",
            risk=_risk(),
            recommendations=_set(),
            chain_gaps=(DecisionChainGap.RECOMMENDATION_ABSENT,),
            limitation_codes=(DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION,),
        ),
        tool_name="get_current_recommendation",
    )
    one, one_view = _project(
        DecisionEvidence(
            kind=DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
            evaluation_state=DecisionEvaluationState.ESTABLISHED,
            aircraft_in_current_set=True,
            aircraft_id="n123ab",
            projection_state=DecisionReportedLinkState.PRESENT,
            projection_id="proj-1",
            risk=_risk(),
            recommendations=_set(
                _recommendation(action=RecommendationActionType.EVALUATE_DIVERSION)
            ),
            chain_gaps=(),
            limitation_codes=(),
        ),
        tool_name="get_current_recommendation",
    )
    many, many_view = _project(
        DecisionEvidence(
            kind=DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
            evaluation_state=DecisionEvaluationState.ESTABLISHED,
            aircraft_in_current_set=True,
            aircraft_id="n123ab",
            projection_state=DecisionReportedLinkState.PRESENT,
            projection_id="proj-1",
            risk=_risk(),
            recommendations=_set(
                _recommendation("rec-1"),
                _recommendation("rec-2", RecommendationActionType.EVALUATE_DIVERSION),
            ),
            chain_gaps=(),
            limitation_codes=(),
        ),
        tool_name="get_current_recommendation",
    )

    assert low_view.temporal_scope is TemporalScope.CURRENT
    assert low_view.data["risk"]["risk_level"] == "LOW"
    assert low_view.data["risk"]["presence"] == "PRESENT"
    assert absent_view.data["risk"]["presence"] == "ABSENT"
    assert absent_view.data["risk"]["risk_level"] is None
    assert empty_view.data["recommendations"]["current"] == []
    assert len(one_view.data["recommendations"]["current"]) == 1
    assert one_view.data["recommendations"]["current"][0]["primary_action_type"] == (
        "EVALUATE_DIVERSION"
    )
    assert [item["recommendation_id"] for item in many_view.data["recommendations"]["current"]] == [
        "rec-1",
        "rec-2",
    ]
    for view in (low_view, absent_view, empty_view, one_view, many_view):
        assert "selected_recommendation" not in _keys(view.to_dict())
        assert "route" not in _keys(view.data)
        assert "selected_diversion" not in _keys(view.data)
    assert low.data == low_view.data
    assert many.data["recommendations"]["current"][1]["primary_action_type"] == (
        "EVALUATE_DIVERSION"
    )


def test_projection_preserves_every_encounter_and_unavailable_route_capability():
    evidence = DecisionEvidence(
        kind=DecisionEvidenceKind.DECISION_CONTEXT,
        evaluation_state=DecisionEvaluationState.ESTABLISHED,
        aircraft_in_current_set=True,
        aircraft_id="n123ab",
        projection_state=DecisionReportedLinkState.PRESENT,
        projection_id="proj-1",
        aircraft_state_version="state-1",
        encounters=(
            _encounter("enc-1", _risk(risk_id="risk-1"), _set(_recommendation("rec-1"))),
            _encounter(
                "enc-2",
                _risk(risk_id="risk-2", encounter_id="enc-2"),
                _set(_recommendation("rec-2")),
            ),
        ),
        capability=DecisionRouteCapability(validated_alternative_available=False),
        chain_gaps=(),
        limitation_codes=(),
    )
    _raw, view = _project(evidence)
    assert [item["encounter_id"] for item in view.data["encounters"]] == ["enc-1", "enc-2"]
    assert view.data["risk"] is None
    assert view.data["recommendations"] is None
    assert view.data["capability"]["validated_alternative_available"] is False
    assert "ROUTE_SAFETY_EVIDENCE_UNAVAILABLE" in view.data["capability"]["unavailable"]
    assert "selected_encounter" not in _keys(view.data)
    assert "clearance" not in _keys(view.data)


def test_projection_hides_runtime_fields_and_keeps_source_identity():
    raw, view = _project(
        DecisionEvidence(
            kind=DecisionEvidenceKind.DECISION_CONTEXT,
            evaluation_state=DecisionEvaluationState.ESTABLISHED,
            aircraft_in_current_set=True,
            aircraft_id="n123ab",
            projection_state=DecisionReportedLinkState.MISSING,
            chain_gaps=(DecisionChainGap.NO_CURRENT_PROJECTION,),
            limitation_codes=(DecisionLimitationCode.NO_SNAPSHOT_LIMITATION,),
        )
    )
    projected = view.to_dict()
    evidence = projected["evidence"][0]
    assert "tool_call_id" not in projected
    assert "correlation_id" not in projected
    assert "schema_version" not in projected
    for hidden in (
        "tool_call_id",
        "query_timestamp_utc",
        "schema_version",
        "completeness",
        "match_cardinality",
        "query_executions",
        "error_code",
    ):
        assert hidden not in evidence
    assert evidence["source"] == "get_aircraft_record"
    assert evidence["source_records"][0]["record_id"] == "n123ab"
    assert evidence["source_records"][0]["source_version"] == "state-1"
    assert evidence["source_records"][0]["event_timestamp_utc"] == AS_OF
    assert evidence["freshness_status"] == "FRESH"
    assert evidence["confidence"] == "HIGH"
    assert raw.tool_call_id == "call-1"
    assert raw.correlation_id == "corr-1"
    assert raw.evidence[0].query_timestamp_utc == QUERY
    assert raw.evidence[0].error_code == "trace-code"
    assert raw.evidence[0].query_executions[0].query_id == "query-1"


def _candidate(status, airport_id="KDEN", assessment_id="aa-1", rank=None):
    scores = {}
    if status == "COMPLETE":
        scores = {
            "rank": rank,
            "distance_score": 70,
            "weather_score": 60,
            "taf_score": 50,
            "total_airport_score": 80,
        }
    return PersistedAirportCandidate(
        airport_id=airport_id,
        airport_assessment_id=assessment_id,
        risk_id="risk-1",
        assessment_status=status,
        route_safety_status="UNAVAILABLE",
        runway_evidence_status="UNAVAILABLE",
        congestion_evidence_status="UNAVAILABLE",
        created_at_utc=AS_OF,
        created_at_epoch=1,
        expires_at_epoch=2,
        evaluation_version="ruleset-1",
        assessment_ruleset_version="ruleset-1",
        schema_version="wilvor.airport_assessment.v1",
        distance_nm=40,
        eta_minutes=20,
        candidate_reason="Within diversion search radius.",
        known_limitations=("Route hazard evaluation is not implemented yet.",),
        **scores,
    )


def _persisted_projection(payload: PersistedAirportEvidence, status, limitations=()):
    result = ToolResult(
        tool_name="get_persisted_airport_candidate_evidence",
        tool_call_id="call-1",
        status=status,
        temporal_scope=TemporalScope.PERSISTED,
        data=payload.to_dict(),
        evidence=(
            Evidence(
                source="query_airport_assessments_for_evaluation",
                source_records=(
                    SourceRecord(
                        record_id="aa-1",
                        source_version="wilvor.airport_assessment.v1",
                        event_timestamp_utc=AS_OF,
                    ),
                ),
                query_timestamp_utc=QUERY,
                freshness_status=FreshnessStatus.UNKNOWN,
                confidence=ConfidenceLevel.UNKNOWN,
                limitations=(NOT_CURRENT_LIMITATION,),
                tool_call_id="call-1",
                temporal_scope=TemporalScope.PERSISTED,
            ),
        ),
        as_of_utc=None,
        limitations=limitations,
        correlation_id="corr-1",
    )
    return project_decision_tool_result(result)


def test_persisted_projection_keeps_rows_scores_and_unavailable_gaps():
    view = _persisted_projection(
        PersistedAirportEvidence(
            recommendation_id="rec-1",
            airport_evaluation_id="eval-1",
            candidates=(
                _candidate("COMPLETE", rank=1),
                _candidate("WAITING_FOR_WEATHER", "KORD", "aa-2"),
            ),
        ),
        ToolResultStatus.SUCCESS,
        ( 
            "Persisted airport rows are returned for this evaluation id. This read "
            "does not prove the original complete candidate set after TTL or a later write.",
        ),
    )
    assert view.temporal_scope is TemporalScope.PERSISTED
    assert view.status is ToolResultStatus.SUCCESS
    assert view.data["scope"] == "PERSISTED_EVALUATION_EVIDENCE"
    assert view.data["recommendation_id"] == "rec-1"
    assert view.data["airport_evaluation_id"] == "eval-1"
    complete, waiting = view.data["candidates"]
    assert complete["assessment_status"] == "COMPLETE"
    assert complete["rank"] == 1
    assert complete["total_airport_score"] == 80
    assert complete["distance_score"] == 70
    assert waiting["assessment_status"] == "WAITING_FOR_WEATHER"
    assert waiting["rank"] is None
    assert waiting["total_airport_score"] is None
    for candidate in view.data["candidates"]:
        assert candidate["route_safety_status"] == "UNAVAILABLE"
        assert candidate["runway_evidence_status"] == "UNAVAILABLE"
        assert candidate["congestion_evidence_status"] == "UNAVAILABLE"
    assert view.evidence[0].freshness_status is FreshnessStatus.UNKNOWN
    assert NOT_CURRENT_LIMITATION in view.evidence[0].limitations
    hidden = _keys(view.to_dict())
    for name in (
        "preferred_airport_id",
        "selected_diversion",
        "safe_diversion",
        "route",
        "best_airport",
    ):
        assert name not in hidden


def test_persisted_projection_keeps_verified_zero_partial_and_unknown():
    zero = _persisted_projection(
        PersistedAirportEvidence(
            recommendation_id="rec-1",
            airport_evaluation_id="eval-1",
            candidates=(),
            no_suitable_candidate_reason=EMPTY_ASSESSMENTS,
            recommendation_observed_empty_at_utc=AS_OF,
        ),
        ToolResultStatus.SUCCESS,
    )
    partial = _persisted_projection(
        PersistedAirportEvidence(
            recommendation_id="rec-1",
            airport_evaluation_id="eval-1",
            candidates=(_candidate("WAITING_FOR_WEATHER"),),
        ),
        ToolResultStatus.PARTIAL,
        ("A referenced airport assessment row is not in the stored evaluation.",),
    )
    unknown = _persisted_projection(
        PersistedAirportEvidence(
            recommendation_id="rec-1",
            airport_evaluation_id="eval-1",
            candidates=(),
        ),
        ToolResultStatus.UNKNOWN,
        ("Persisted airport evaluation evidence is inconsistent.",),
    )
    assert zero.data["candidates"] == []
    assert zero.data["no_suitable_candidate_reason"] == EMPTY_ASSESSMENTS
    assert zero.data["recommendation_observed_empty_at_utc"] == AS_OF
    assert zero.as_of_utc is None
    assert partial.status is ToolResultStatus.PARTIAL
    assert partial.data["candidates"][0]["assessment_status"] == "WAITING_FOR_WEATHER"
    assert unknown.status is ToolResultStatus.UNKNOWN
    assert unknown.data["candidates"] == []
