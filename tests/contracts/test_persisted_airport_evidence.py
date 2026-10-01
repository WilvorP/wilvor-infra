"""DT4 persisted airport evaluation evidence."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from wilvor_ai.contracts import (
    ConfidenceLevel,
    FreshnessStatus,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.persisted_airport_contracts import validate_persisted_airport_tool_result
from wilvor_ai.persisted_airport_evidence import (
    EMPTY_ASSESSMENTS,
    MEMBERSHIP_LIMITATION,
    NO_COMPLETE_ASSESSMENT,
    NOT_CURRENT_LIMITATION,
    PersistedAirportEvidenceCall,
    get_persisted_airport_candidate_evidence,
)


QUERY = "2026-09-30T20:00:00Z"
REC_UTC = "2026-09-30T18:00:00Z"
REC_EPOCH = int(datetime(2026, 9, 30, 18, tzinfo=timezone.utc).timestamp())
CAND_UTC = "2026-09-30T17:00:00+00:00"
CAND_EPOCH = int(datetime(2026, 9, 30, 17, tzinfo=timezone.utc).timestamp())
EXPIRES = CAND_EPOCH + 86400
RULESET = "wilvor.airport-assessment.ruleset.v1"
MODULE = (
    Path(__file__).resolve().parents[2]
    / "functions"
    / "shared"
    / "wilvor_ai"
    / "persisted_airport_evidence.py"
)


class Table:
    def __init__(self, item=None, items=None, error=False):
        self.item = item
        self.items = list(items or [])
        self.error = error
        self.calls = []

    def get_item(self, **kwargs):
        self.calls.append(("get_item", kwargs))
        if self.error:
            raise RuntimeError("read failed")
        if self.item is None:
            return {}
        return {"Item": self.item}

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        if self.error:
            raise RuntimeError("query failed")
        return {"Items": self.items}

    def scan(self, **kwargs):
        raise AssertionError("scan")

    def put_item(self, **kwargs):
        raise AssertionError("write")


def _call(recommendations, assessments):
    return PersistedAirportEvidenceCall(
        tables=SimpleNamespace(
            recommendations=recommendations,
            airport_assessments=assessments,
        ),
        tool_call_id="tool-dt4",
        query_timestamp_utc=QUERY,
        correlation_id="corr-dt4",
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


def _run(recommendation, rows=None, rec_error=False, query_error=False, recommendation_id="rec-1"):
    recommendations = Table(item=recommendation, error=rec_error)
    assessments = Table(items=rows or [], error=query_error)
    result = get_persisted_airport_candidate_evidence(
        _call(recommendations, assessments),
        recommendation_id,
    )
    payload = validate_persisted_airport_tool_result(result)
    return result, payload, recommendations, assessments


def _assert_persisted(result):
    assert result.temporal_scope is TemporalScope.PERSISTED
    assert result.correlation_id == "corr-dt4"
    assert all(item.temporal_scope is TemporalScope.PERSISTED for item in result.evidence)
    assert all(item.freshness_status is FreshnessStatus.UNKNOWN for item in result.evidence)
    assert all(item.confidence is ConfidenceLevel.UNKNOWN for item in result.evidence)
    assert all(item.query_timestamp_utc == QUERY for item in result.evidence)
    assert all(NOT_CURRENT_LIMITATION in item.limitations for item in result.evidence)


def test_recommendation_read_failure_is_unavailable():
    result, payload, recommendations, assessments = _run(_recommendation(), rec_error=True)

    assert result.status is ToolResultStatus.UNAVAILABLE
    assert result.evidence == ()
    assert result.as_of_utc is None
    assert assessments.calls == []
    assert payload.candidates == ()
    _assert_persisted(result)


def test_recommendation_absent_is_not_found():
    result, payload, _recommendations, assessments = _run(None)

    assert result.status is ToolResultStatus.NOT_FOUND
    assert result.evidence == ()
    assert assessments.calls == []
    assert payload.airport_evaluation_id is None


def test_recommendation_with_no_evaluation_is_success():
    result, payload, _recommendations, assessments = _run(
        {
            "recommendation_id": "rec-1",
            "recommendation_version": "ver-1",
            "hazard_source_version": "v1",
            "source_versions": {"hazard_source_version": "v1"},
            "evidence_references": [{"type": "RISK_RESULT", "id": "risk-1"}],
            "created_at_utc": REC_UTC,
        }
    )

    assert result.status is ToolResultStatus.SUCCESS
    assert payload.airport_evaluation_id is None
    assert payload.candidates == ()
    assert result.as_of_utc is None
    assert assessments.calls == []
    assert [item.source for item in result.evidence] == ["get_recommendation_record"]
    _assert_persisted(result)


def test_incomplete_evaluation_copies_are_unknown():
    recommendation = _recommendation()
    del recommendation["source_versions"]["airport_evaluation_id"]
    result, payload, _recommendations, assessments = _run(recommendation)

    assert result.status is ToolResultStatus.UNKNOWN
    assert assessments.calls == []
    assert payload.candidates == ()
    assert [item.source for item in result.evidence] == ["get_recommendation_record"]


def test_conflicting_evaluation_copies_are_unknown():
    recommendation = _recommendation()
    recommendation["source_versions"]["airport_evaluation_id"] = "eval-2"
    result, _payload, _recommendations, assessments = _run(recommendation)

    assert result.status is ToolResultStatus.UNKNOWN
    assert assessments.calls == []


def test_conflicting_evaluation_reference_is_unknown():
    recommendation = _recommendation()
    recommendation["evidence_references"].append(
        {"type": "AIRPORT_ASSESSMENT_EVALUATION", "id": "eval-2"}
    )
    result, _payload, _recommendations, assessments = _run(recommendation)

    assert result.status is ToolResultStatus.UNKNOWN
    assert assessments.calls == []


def test_repeated_same_evaluation_reference_is_accepted():
    recommendation = _recommendation()
    recommendation["evidence_references"].append(
        {"type": "AIRPORT_ASSESSMENT_EVALUATION", "id": "eval-1"}
    )
    result, payload, _recommendations, _assessments = _run(recommendation, [_row()])

    assert result.status is ToolResultStatus.SUCCESS
    assert payload.candidates[0].airport_id == "KDEN"


def test_evaluation_query_failure_is_unavailable_and_keeps_recommendation_evidence():
    result, payload, _recommendations, _assessments = _run(_recommendation(), query_error=True)

    assert result.status is ToolResultStatus.UNAVAILABLE
    assert result.as_of_utc is None
    assert payload.airport_evaluation_id == "eval-1"
    assert payload.candidates == ()
    assert [item.source for item in result.evidence] == ["get_recommendation_record"]


def test_verified_zero_is_success_without_assessment_evidence():
    recommendation = _recommendation(no_suitable_candidate_reason=EMPTY_ASSESSMENTS)
    result, payload, _recommendations, _assessments = _run(recommendation, [])

    assert result.status is ToolResultStatus.SUCCESS
    assert payload.candidates == ()
    assert payload.no_suitable_candidate_reason == EMPTY_ASSESSMENTS
    assert payload.recommendation_observed_empty_at_utc == REC_UTC
    assert result.as_of_utc is None
    assert [item.source for item in result.evidence] == ["get_recommendation_record"]
    _assert_persisted(result)


def test_verified_zero_sentence_with_later_rows_is_unknown():
    recommendation = _recommendation(no_suitable_candidate_reason=EMPTY_ASSESSMENTS)
    result, payload, _recommendations, _assessments = _run(recommendation, [_row()])

    assert result.status is ToolResultStatus.UNKNOWN
    assert payload.candidates == ()
    assert [item.source for item in result.evidence] == ["get_recommendation_record"]


def test_waiting_rows_that_remain_are_success():
    row = _row(assessment_status="WAITING_FOR_WEATHER", rank=None)
    del row["rank"]
    del row["total_airport_score"]
    recommendation = _recommendation(no_suitable_candidate_reason=NO_COMPLETE_ASSESSMENT)
    result, payload, _recommendations, _assessments = _run(recommendation, [row])

    assert result.status is ToolResultStatus.SUCCESS
    assert payload.candidates[0].assessment_status == "WAITING_FOR_WEATHER"
    assert payload.candidates[0].rank is None
    assert MEMBERSHIP_LIMITATION in result.limitations


def test_waiting_only_sentence_with_empty_query_is_partial():
    recommendation = _recommendation(no_suitable_candidate_reason=NO_COMPLETE_ASSESSMENT)
    result, payload, _recommendations, _assessments = _run(recommendation, [])

    assert result.status is ToolResultStatus.PARTIAL
    assert payload.candidates == ()
    assert result.as_of_utc is None
    assert [item.source for item in result.evidence] == ["get_recommendation_record"]


def test_empty_query_without_zero_proof_is_unknown():
    result, payload, _recommendations, _assessments = _run(_recommendation(), [])

    assert result.status is ToolResultStatus.UNKNOWN
    assert payload.candidates == ()
    assert payload.recommendation_observed_empty_at_utc is None


def test_one_complete_candidate_keeps_rank_and_scores():
    result, payload, _recommendations, _assessments = _run(_recommendation(), [_row()])

    candidate = payload.candidates[0]
    assert result.status is ToolResultStatus.SUCCESS
    assert result.as_of_utc == CAND_UTC
    assert candidate.rank == 1
    assert candidate.total_airport_score == 80
    assert candidate.distance_score == 70
    assert candidate.route_safety_status == "UNAVAILABLE"
    assert candidate.runway_evidence_status == "UNAVAILABLE"
    assert candidate.congestion_evidence_status == "UNAVAILABLE"
    assert [item.source for item in result.evidence] == [
        "get_recommendation_record",
        "query_airport_assessments_for_evaluation",
    ]
    assert result.evidence[1].source_records[0].record_id == "aa-1"
    assert MEMBERSHIP_LIMITATION in result.limitations
    _assert_persisted(result)


def test_query_order_does_not_rerank():
    second = _row(airport_id="KSEA", airport_assessment_id="aa-2", rank=2)
    result, payload, _recommendations, _assessments = _run(
        _recommendation(),
        [second, _row()],
    )

    assert result.status is ToolResultStatus.SUCCESS
    assert [item.rank for item in payload.candidates] == [1, 2]
    assert [item.airport_id for item in payload.candidates] == ["KDEN", "KSEA"]


def test_unreferenced_waiting_row_is_kept():
    waiting = _row(
        airport_id="KORD",
        airport_assessment_id="aa-wait",
        assessment_status="WAITING_FOR_WEATHER",
    )
    del waiting["rank"]
    recommendation = _recommendation(
        candidate_airport_summaries=[
            {
                "airport_id": "KDEN",
                "airport_assessment_id": "aa-1",
                "rank": 1,
                "total_airport_score": 80,
                "distance_nm": 40,
                "eta_minutes": 20,
                "weather_risk_level": "LOW",
            }
        ]
    )
    result, payload, _recommendations, _assessments = _run(recommendation, [_row(), waiting])

    assert result.status is ToolResultStatus.SUCCESS
    assert [item.airport_assessment_id for item in payload.candidates] == ["aa-1", "aa-wait"]


def test_unreferenced_complete_row_beyond_top_is_kept():
    extra = _row(airport_id="KSEA", airport_assessment_id="aa-2", rank=2, total_airport_score=70)
    recommendation = _recommendation(
        evidence_references=[
            {"type": "AIRPORT_ASSESSMENT_EVALUATION", "id": "eval-1"},
            {"type": "AIRPORT_ASSESSMENT", "id": "aa-1", "airport_id": "KDEN"},
        ]
    )
    result, payload, _recommendations, _assessments = _run(recommendation, [extra, _row()])

    assert result.status is ToolResultStatus.SUCCESS
    assert [item.airport_assessment_id for item in payload.candidates] == ["aa-1", "aa-2"]


def test_missing_referenced_assessment_is_partial_and_keeps_valid_rows():
    recommendation = _recommendation(
        evidence_references=[
            {"type": "AIRPORT_ASSESSMENT_EVALUATION", "id": "eval-1"},
            {"type": "AIRPORT_ASSESSMENT", "id": "aa-missing", "airport_id": "KLAX"},
        ]
    )
    result, payload, _recommendations, _assessments = _run(recommendation, [_row()])

    assert result.status is ToolResultStatus.PARTIAL
    assert [item.airport_assessment_id for item in payload.candidates] == ["aa-1"]
    assert [record.record_id for item in result.evidence for record in item.source_records] == [
        "rec-1",
        "aa-1",
    ]


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("evaluation_id", "eval-other"),
        ("risk_id", "risk-other"),
        ("aircraft_id", "ac-other"),
        ("hazard_id", "haz-other"),
        ("hazard_source_version", "v9"),
    ],
)
def test_row_lineage_mismatch_is_unknown(field_name, value):
    result, payload, _recommendations, _assessments = _run(
        _recommendation(),
        [_row(**{field_name: value})],
    )

    assert result.status is ToolResultStatus.UNKNOWN
    assert payload.candidates == ()
    assert [item.source for item in result.evidence] == ["get_recommendation_record"]


def test_recommendation_hazard_source_copies_conflict():
    recommendation = _recommendation()
    recommendation["source_versions"]["hazard_source_version"] = "v9"
    result, _payload, _recommendations, assessments = _run(recommendation, [_row()])

    assert result.status is ToolResultStatus.UNKNOWN
    assert assessments.calls == []


def test_rows_disagree_on_aircraft_state_version():
    other = _row(airport_id="KSEA", airport_assessment_id="aa-2", rank=2)
    other["aircraft_state_version"] = "state-2"
    result, _payload, _recommendations, _assessments = _run(_recommendation(), [_row(), other])

    assert result.status is ToolResultStatus.UNKNOWN


@pytest.mark.parametrize(
    "field_name",
    ["evaluation_version", "assessment_ruleset_version", "schema_version"],
)
def test_rows_disagree_on_version_fields(field_name):
    other = _row(airport_id="KSEA", airport_assessment_id="aa-2", rank=2)
    other[field_name] = "other"
    if field_name == "evaluation_version":
        other["assessment_ruleset_version"] = "other"
    if field_name == "assessment_ruleset_version":
        other["evaluation_version"] = "other"
    result, _payload, _recommendations, _assessments = _run(_recommendation(), [_row(), other])

    assert result.status is ToolResultStatus.UNKNOWN


def test_row_ruleset_copies_must_agree():
    row = _row(evaluation_version="ruleset-a", assessment_ruleset_version="ruleset-b")
    result, _payload, _recommendations, _assessments = _run(_recommendation(), [row])

    assert result.status is ToolResultStatus.UNKNOWN


def test_invalid_assessment_status_is_unknown():
    result, _payload, _recommendations, _assessments = _run(
        _recommendation(),
        [_row(assessment_status="EVALUATED")],
    )

    assert result.status is ToolResultStatus.UNKNOWN


@pytest.mark.parametrize(
    "field_name",
    ["route_safety_status", "runway_evidence_status", "congestion_evidence_status"],
)
def test_non_unavailable_capability_status_is_unknown(field_name):
    result, _payload, _recommendations, _assessments = _run(
        _recommendation(),
        [_row(**{field_name: "AVAILABLE"})],
    )

    assert result.status is ToolResultStatus.UNKNOWN


def test_mixed_created_at_utc_is_unknown():
    other = _row(
        airport_id="KSEA",
        airport_assessment_id="aa-2",
        rank=2,
        created_at_utc="2026-09-30T17:00:01+00:00",
    )
    result, _payload, _recommendations, _assessments = _run(_recommendation(), [_row(), other])

    assert result.status is ToolResultStatus.UNKNOWN


def test_mixed_created_at_epoch_is_unknown():
    other = _row(airport_id="KSEA", airport_assessment_id="aa-2", rank=2)
    other["created_at_epoch"] = CAND_EPOCH + 1
    other["created_at_utc"] = "2026-09-30T17:00:01+00:00"
    result, _payload, _recommendations, _assessments = _run(_recommendation(), [_row(), other])

    assert result.status is ToolResultStatus.UNKNOWN


def test_mixed_expires_at_epoch_is_unknown():
    other = _row(airport_id="KSEA", airport_assessment_id="aa-2", rank=2, expires_at_epoch=EXPIRES + 1)
    result, _payload, _recommendations, _assessments = _run(_recommendation(), [_row(), other])

    assert result.status is ToolResultStatus.UNKNOWN


def test_malformed_candidate_timestamp_is_unknown():
    result, _payload, _recommendations, _assessments = _run(
        _recommendation(),
        [_row(created_at_utc="not-a-time")],
    )

    assert result.status is ToolResultStatus.UNKNOWN


def test_utc_epoch_mismatch_is_unknown():
    result, _payload, _recommendations, _assessments = _run(
        _recommendation(),
        [_row(created_at_epoch=CAND_EPOCH + 5)],
    )

    assert result.status is ToolResultStatus.UNKNOWN


def test_candidate_newer_than_recommendation_is_unknown():
    later = "2026-09-30T19:00:00+00:00"
    later_epoch = int(datetime(2026, 9, 30, 19, tzinfo=timezone.utc).timestamp())
    result, _payload, _recommendations, _assessments = _run(
        _recommendation(),
        [_row(created_at_utc=later, created_at_epoch=later_epoch, expires_at_epoch=later_epoch + 86400)],
    )

    assert result.status is ToolResultStatus.UNKNOWN


def test_malformed_recommendation_timestamp_is_unknown():
    result, _payload, _recommendations, assessments = _run(
        _recommendation(created_at_utc="not-a-time"),
        [_row()],
    )

    assert result.status is ToolResultStatus.UNKNOWN
    assert assessments.calls == []


def test_matching_summary_is_accepted_and_decimal_forms_compare_equal():
    recommendation = _recommendation(
        candidate_airport_summaries=[
            {
                "airport_id": "KDEN",
                "airport_assessment_id": "aa-1",
                "rank": Decimal("1"),
                "total_airport_score": Decimal("80.0"),
                "distance_nm": Decimal("40"),
                "eta_minutes": Decimal("20.0"),
                "weather_risk_level": "LOW",
            }
        ],
        preferred_airport_id="KDEN",
        preferred_airport_assessment_id="aa-1",
        preferred_airport_score=Decimal("80"),
    )
    result, payload, _recommendations, _assessments = _run(recommendation, [_row()])

    assert result.status is ToolResultStatus.SUCCESS
    assert payload.candidates[0].total_airport_score == 80
    assert "preferred_airport_id" not in result.data
    assert "selected_diversion" not in result.data


@pytest.mark.parametrize(
    "summary_change",
    [
        {"total_airport_score": 1},
        {"rank": 2},
        {"airport_id": "KLAX"},
    ],
)
def test_summary_mismatch_is_unknown(summary_change):
    summary = {
        "airport_id": "KDEN",
        "airport_assessment_id": "aa-1",
        "rank": 1,
        "total_airport_score": 80,
        "distance_nm": 40,
        "eta_minutes": 20,
        "weather_risk_level": "LOW",
    }
    summary.update(summary_change)
    result, payload, _recommendations, _assessments = _run(
        _recommendation(candidate_airport_summaries=[summary]),
        [_row()],
    )

    assert result.status is ToolResultStatus.UNKNOWN
    assert payload.candidates == ()
    assert [item.source for item in result.evidence] == ["get_recommendation_record"]


def test_preferred_snapshot_mismatch_is_unknown():
    result, _payload, _recommendations, _assessments = _run(
        _recommendation(
            preferred_airport_id="KLAX",
            preferred_airport_assessment_id="aa-1",
            preferred_airport_score=80,
        ),
        [_row()],
    )

    assert result.status is ToolResultStatus.UNKNOWN


def test_alternative_action_mismatch_is_unknown():
    result, _payload, _recommendations, _assessments = _run(
        _recommendation(
            alternative_actions=[
                {
                    "airport_id": "KDEN",
                    "airport_assessment_id": "aa-1",
                    "score": 1,
                    "rank": 1,
                }
            ]
        ),
        [_row()],
    )

    assert result.status is ToolResultStatus.UNKNOWN


@pytest.mark.parametrize("field_name", ["airport_id", "airport_assessment_id", "rank"])
def test_duplicate_identity_is_unknown(field_name):
    other = _row(airport_id="KSEA", airport_assessment_id="aa-2", rank=2)
    if field_name == "airport_id":
        other["airport_id"] = "KDEN"
    elif field_name == "airport_assessment_id":
        other["airport_assessment_id"] = "aa-1"
    else:
        other["rank"] = 1
    result, _payload, _recommendations, _assessments = _run(_recommendation(), [_row(), other])

    assert result.status is ToolResultStatus.UNKNOWN


def test_output_has_no_route_or_diversion_fields():
    result, _payload, _recommendations, _assessments = _run(_recommendation(), [_row()])
    candidate = result.data["candidates"][0]

    for key in (
        "selected_diversion",
        "safe_diversion",
        "preferred_airport",
        "preferred_airport_id",
        "route",
        "waypoint",
        "trajectory",
        "flight_plan",
        "airport_latitude",
        "airport_longitude",
    ):
        assert key not in candidate
    assert candidate["route_safety_status"] == "UNAVAILABLE"
    assert all("suitable" not in str(value) for value in candidate.values())


def test_persisted_validator_rejects_current_scope():
    from dataclasses import replace

    result, _payload, _recommendations, _assessments = _run(_recommendation(), [_row()])
    current_evidence = tuple(
        replace(item, temporal_scope=TemporalScope.CURRENT) for item in result.evidence
    )
    current = ToolResult(
        tool_name=result.tool_name,
        tool_call_id=result.tool_call_id,
        status=result.status,
        temporal_scope=TemporalScope.CURRENT,
        data=result.data,
        evidence=current_evidence,
        as_of_utc=result.as_of_utc,
        limitations=result.limitations,
        correlation_id=result.correlation_id,
    )

    with pytest.raises(Exception, match="persisted_airport_temporal_scope"):
        validate_persisted_airport_tool_result(current)


def test_module_does_not_import_processors_sdks_or_acquire_a_clock():
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert "datetime.now" not in source
    assert "time.time" not in source
    assert "boto3" not in source
    assert "put_item" not in source
    assert "anthropic" not in source
    assert imported.isdisjoint({"boto3", "anthropic", "botocore"})
    assert all("processor" not in name for name in imported)
