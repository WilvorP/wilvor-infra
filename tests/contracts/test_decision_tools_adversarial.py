"""Uncovered Decision Tools adversarial cases. Not a second copy of DT1-DT5."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from wilvor_ai.contracts import (
    ConfidenceLevel,
    ContractValidationError,
    TemporalScope,
    ToolResultStatus,
)
from wilvor_ai.decision_contracts import (
    FORBIDDEN_DECISION_KEYS,
    DecisionAdvisoryAuthority,
    DecisionEncounterLink,
    DecisionEvaluationState,
    DecisionEvidence,
    DecisionEvidenceKind,
    DecisionRecommendationEvidence,
    DecisionRecommendationSet,
    DecisionReportedLinkState,
    DecisionRiskEvidence,
    DecisionRouteCapability,
    HazardSourceVersionLink,
    PersistedConfidence,
    PersistedConfidenceSource,
    RecommendationActionType,
    RiskPresence,
    StoredRiskLevel,
)
from wilvor_ai.decision_tools import DecisionToolsAdapter, DecisionToolsRuntime
from wilvor_ai.persisted_airport_contracts import validate_persisted_airport_tool_result
from wilvor_ai.persisted_airport_evidence import (
    NO_COMPLETE_ASSESSMENT,
    PersistedAirportEvidenceCall,
    get_persisted_airport_candidate_evidence,
)
from wilvor_ai.tool_schema import FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES
from wilvor_operational import readers


QUERY = "2026-09-30T20:00:00Z"
REC_UTC = "2026-09-30T18:00:00Z"
REC_EPOCH = int(datetime(2026, 9, 30, 18, tzinfo=timezone.utc).timestamp())
CAND_UTC = "2026-09-30T17:00:00+00:00"
CAND_EPOCH = int(datetime(2026, 9, 30, 17, tzinfo=timezone.utc).timestamp())
EXPIRES = CAND_EPOCH + 86400
RULESET = "wilvor.airport-assessment.ruleset.v1"
AS_OF = "2026-09-29T00:00:00Z"
UNTIL = "2026-09-29T00:15:00Z"


class Table:
    def __init__(self, item=None, items=None):
        self.item = item
        self.items = list(items or [])

    def get_item(self, **kwargs):
        if self.item is None:
            return {}
        return {"Item": self.item}

    def query(self, **kwargs):
        return {"Items": self.items}


class CountingTable:
    def __init__(self):
        self.calls = 0

    def scan(self, **kwargs):
        self.calls += 1
        raise RuntimeError("dispatched")

    def get_item(self, **kwargs):
        self.calls += 1
        raise RuntimeError("dispatched")

    def query(self, **kwargs):
        self.calls += 1
        raise RuntimeError("dispatched")


def _call(recommendations, assessments):
    return PersistedAirportEvidenceCall(
        tables=SimpleNamespace(
            recommendations=recommendations,
            airport_assessments=assessments,
        ),
        tool_call_id="tool-dt6",
        query_timestamp_utc=QUERY,
        correlation_id="corr-dt6",
    )


def _recommendation(**overrides):
    item = {
        "recommendation_id": "rec-1",
        "recommendation_version": "ver-1",
        "airport_evaluation_id": "eval-1",
        "source_versions": {
            "airport_evaluation_id": "eval-1",
            "hazard_source_version": "v1",
        },
        "evidence_references": [
            {"type": "AIRPORT_ASSESSMENT_EVALUATION", "id": "eval-1"},
        ],
        "hazard_source_version": "v1",
        "risk_id": "risk-1",
        "aircraft_id": "ac-1",
        "hazard_id": "haz-1",
        "created_at_utc": REC_UTC,
        "created_at_epoch": REC_EPOCH,
    }
    item.update(overrides)
    return item


def _row(**overrides):
    item = {
        "evaluation_id": "eval-1",
        "airport_id": "KDEN",
        "airport_assessment_id": "aa-1",
        "risk_id": "risk-1",
        "aircraft_id": "ac-1",
        "aircraft_state_version": "state-1",
        "hazard_id": "haz-1",
        "hazard_source_version": "v1",
        "assessment_status": "COMPLETE",
        "rank": 1,
        "total_airport_score": 80,
        "distance_score": 70,
        "weather_score": 60,
        "taf_score": 50,
        "distance_nm": 40,
        "eta_minutes": 20,
        "estimated_arrival_time_utc": CAND_UTC,
        "weather_risk_level": "LOW",
        "metar_version": "m1",
        "taf_version": "t1",
        "taf_period_ids": ["p1"],
        "candidate_reason": "Within diversion search radius.",
        "known_limitations": ["Route hazard evaluation is not implemented yet."],
        "route_safety_status": "UNAVAILABLE",
        "runway_evidence_status": "UNAVAILABLE",
        "congestion_evidence_status": "UNAVAILABLE",
        "created_at_utc": CAND_UTC,
        "created_at_epoch": CAND_EPOCH,
        "expires_at_epoch": EXPIRES,
        "evaluation_version": RULESET,
        "assessment_ruleset_version": RULESET,
        "schema_version": "wilvor.airport_assessment.v1",
    }
    item.update(overrides)
    return item


def _run(recommendation, rows):
    result = get_persisted_airport_candidate_evidence(
        _call(Table(item=recommendation), Table(items=rows)),
        "rec-1",
    )
    payload = validate_persisted_airport_tool_result(result)
    return result, payload


def _record_ids(result):
    return {
        record.record_id
        for item in result.evidence
        for record in item.source_records
    }


def _context_evidence() -> DecisionEvidence:
    risk = DecisionRiskEvidence(
        presence=RiskPresence.PRESENT,
        risk_id="risk-1",
        encounter_id="enc-1",
        scoring_ruleset_version="wilvor.risk.ruleset.v1",
        generated_at_utc=AS_OF,
        valid_until_utc=UNTIL,
        risk_level=StoredRiskLevel.LOW,
        risk_score=12,
        confidence=PersistedConfidence(
            ConfidenceLevel.LOW,
            PersistedConfidenceSource.RISK_RESULT,
        ),
    )
    recommendation = DecisionRecommendationEvidence(
        recommendation_id="rec-1",
        primary_action_type=RecommendationActionType.MONITOR,
        advisory_authority=DecisionAdvisoryAuthority.ADVISORY_ONLY,
        recommendation_version="ver-1",
        ruleset_version="wilvor.recommendation.ruleset.v1",
        valid_from_utc=AS_OF,
        valid_until_utc=UNTIL,
        advisory_notice="Advisory only. Not a clearance.",
        confidence=PersistedConfidence(
            ConfidenceLevel.LOW,
            PersistedConfidenceSource.RECOMMENDATION,
        ),
    )
    encounter = DecisionEncounterLink(
        encounter_id="enc-1",
        projection_id="proj-1",
        aircraft_state_version="state-1",
        hazard=HazardSourceVersionLink(
            hazard_id="haz-1",
            state=DecisionReportedLinkState.PRESENT,
            persisted_source_version="sv-1",
            current_source_version="sv-1",
        ),
        risk=risk,
        recommendations=DecisionRecommendationSet(
            current=(recommendation,),
            absence_state=DecisionReportedLinkState.PRESENT,
        ),
    )
    return DecisionEvidence(
        kind=DecisionEvidenceKind.DECISION_CONTEXT,
        evaluation_state=DecisionEvaluationState.ESTABLISHED,
        aircraft_in_current_set=True,
        aircraft_id="ac-1",
        projection_state=DecisionReportedLinkState.PRESENT,
        projection_id="proj-1",
        aircraft_state_version="state-1",
        encounters=(encounter,),
        capability=DecisionRouteCapability(validated_alternative_available=False),
        chain_gaps=(),
        limitation_codes=(),
    )


def _adapter(counter: CountingTable) -> DecisionToolsAdapter:
    tables = readers.OperationalTables(
        aircraft=counter,
        projections=counter,
        projection_points=counter,
        hazards=counter,
        hazard_coordinates=counter,
        encounters=counter,
        risks=counter,
        airports=counter,
        metar=counter,
        taf=counter,
        taf_periods=counter,
        airport_assessments=counter,
        recommendations=counter,
        alerts=counter,
    )
    return DecisionToolsAdapter(
        DecisionToolsRuntime(
            tables=tables,
            now_epoch=1_788_661_800,
            query_timestamp_utc=QUERY,
            correlation_id="corr-dt6",
        )
    )


def test_present_risk_without_stored_level_is_rejected():
    with pytest.raises(ContractValidationError) as caught:
        DecisionRiskEvidence(
            presence=RiskPresence.PRESENT,
            risk_id="risk-1",
            risk_score=12,
        )

    assert "present_risk_requires_level" in caught.value.errors
    assert "LOW" not in caught.value.errors
    assert "UNKNOWN" not in caught.value.errors


def test_decision_context_rejects_top_level_risk():
    with pytest.raises(ContractValidationError) as caught:
        DecisionEvidence(
            kind=DecisionEvidenceKind.DECISION_CONTEXT,
            evaluation_state=DecisionEvaluationState.ESTABLISHED,
            aircraft_in_current_set=True,
            projection_state=DecisionReportedLinkState.PRESENT,
            projection_id="proj-1",
            chain_gaps=(),
            limitation_codes=(),
            risk=DecisionRiskEvidence(presence=RiskPresence.ABSENT, encounter_id="enc-1"),
        )

    assert "context_forbids_top_level_winner" in caught.value.errors


@pytest.mark.parametrize("key", ["selected_diversion", "route", "best_recommendation"])
def test_forbidden_decision_key_nested_under_encounter_is_rejected(key):
    assert key in FORBIDDEN_DECISION_KEYS
    payload = _context_evidence().to_dict()
    payload["encounters"][0][key] = "invented"

    with pytest.raises(ContractValidationError) as caught:
        DecisionEvidence.from_dict(payload)

    assert "forbidden_decision_field" in caught.value.errors


def test_waiting_only_sentence_with_complete_row_is_unknown():
    recommendation = _recommendation(
        no_suitable_candidate_reason=NO_COMPLETE_ASSESSMENT,
    )
    result, payload = _run(recommendation, [_row()])

    assert result.status is ToolResultStatus.UNKNOWN
    assert result.temporal_scope is TemporalScope.PERSISTED
    assert payload.candidates == ()
    assert payload.no_suitable_candidate_reason is None
    assert [item.source for item in result.evidence] == ["get_recommendation_record"]
    assert "aa-1" not in _record_ids(result)
    assert "query_airport_assessments_for_evaluation" not in {
        item.source for item in result.evidence
    }


@pytest.mark.parametrize(
    ("field_name", "copied", "stored"),
    [
        ("distance_nm", 99, 40),
        ("eta_minutes", 99, 20),
        ("weather_risk_level", "HIGH", "LOW"),
    ],
)
def test_copied_snapshot_disagreement_is_unknown(field_name, copied, stored):
    recommendation = _recommendation(
        candidate_airport_summaries=[
            {"airport_assessment_id": "aa-1", field_name: copied},
        ],
    )
    result, payload = _run(recommendation, [_row(**{field_name: stored})])

    assert result.status is ToolResultStatus.UNKNOWN
    assert payload.candidates == ()
    assert [item.source for item in result.evidence] == ["get_recommendation_record"]
    assert "aa-1" not in _record_ids(result)


def test_expiry_before_created_is_unknown():
    result, payload = _run(
        _recommendation(),
        [_row(expires_at_epoch=CAND_EPOCH - 1)],
    )

    assert result.status is ToolResultStatus.UNKNOWN
    assert payload.candidates == ()
    assert [item.source for item in result.evidence] == ["get_recommendation_record"]


def test_complete_non_numeric_total_score_is_unknown():
    result, payload = _run(
        _recommendation(),
        [_row(total_airport_score="eighty")],
    )

    assert result.status is ToolResultStatus.UNKNOWN
    assert payload.candidates == ()
    assert [item.source for item in result.evidence] == ["get_recommendation_record"]


def test_non_string_argument_key_does_not_dispatch():
    counter = CountingTable()

    with pytest.raises(ContractValidationError) as caught:
        _adapter(counter).invoke(
            "get_current_decision_context",
            {"aircraft_id": "N123AB", 1: "x"},
            tool_call_id="tool-dt6",
        )

    assert "invalid_argument_name" in caught.value.errors
    assert counter.calls == 0


def test_temporal_scope_argument_does_not_dispatch():
    assert "temporal_scope" in FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES
    counter = CountingTable()

    with pytest.raises(ContractValidationError) as caught:
        _adapter(counter).invoke(
            "get_current_risk_evidence",
            {"aircraft_id": "N123AB", "temporal_scope": "CURRENT"},
            tool_call_id="tool-dt6",
        )

    assert "trusted_argument_forbidden" in caught.value.errors
    assert counter.calls == 0
