"""Real Decision Tool flows through the Decision Tools adapter. No AWS."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from wilvor_ai.contracts import FreshnessStatus, TemporalScope, ToolResultStatus
from wilvor_ai.decision_contracts import FORBIDDEN_DECISION_KEYS
from wilvor_ai.decision_tools import DecisionToolsAdapter, DecisionToolsRuntime
from wilvor_ai.persisted_airport_evidence import (
    NO_COMPLETE_ASSESSMENT,
    NOT_CURRENT_LIMITATION,
)
from wilvor_operational import context, readers


NOW = 1_788_661_800
FUTURE = "2026-09-06T04:00:00Z"
QUERY = "2026-09-30T20:00:00Z"
AIRCRAFT = "abc123"
PROJECTION = "proj-1"
ENCOUNTER = "proj-1#hazard-1#v1"
HAZARD = "hazard-1"
RISK = "risk-1"
REC_UTC = "2026-09-30T18:00:00Z"
REC_EPOCH = int(datetime(2026, 9, 30, 18, tzinfo=timezone.utc).timestamp())
CAND_UTC = "2026-09-30T17:00:00+00:00"
CAND_EPOCH = int(datetime(2026, 9, 30, 17, tzinfo=timezone.utc).timestamp())
EXPIRES = CAND_EPOCH + 86400
RULESET = "wilvor.airport-assessment.ruleset.v1"
SHARED = Path(__file__).resolve().parents[2] / "functions" / "shared"
MODULES = (
    SHARED / "wilvor_ai" / "decision_contracts.py",
    SHARED / "wilvor_ai" / "decision_context.py",
    SHARED / "wilvor_ai" / "persisted_airport_contracts.py",
    SHARED / "wilvor_ai" / "persisted_airport_evidence.py",
    SHARED / "wilvor_ai" / "decision_tools.py",
    SHARED / "wilvor_ai" / "decision_tool_projection.py",
)
_WRITE_CALLS = frozenset(
    {"put_item", "update_item", "delete_item", "batch_writer", "put_events"}
)
_CLAIM_KEYS = FORBIDDEN_DECISION_KEYS | frozenset(
    {"safe_diversion", "selected_airport"}
)
_CURRENT_STATUSES = frozenset(
    {
        ToolResultStatus.SUCCESS,
        ToolResultStatus.PARTIAL,
        ToolResultStatus.NOT_FOUND,
        ToolResultStatus.UNAVAILABLE,
    }
)
_PERSISTED_STATUSES = _CURRENT_STATUSES | frozenset({ToolResultStatus.UNKNOWN})


class MemoryTable:
    def __init__(self, *, scan_items=None, records=None, query_items=None, fail=None):
        self.scan_items = list(scan_items or [])
        self.records = dict(records or {})
        self.query_items = list(query_items or [])
        self.fail = fail

    def scan(self, **kwargs):
        if self.fail in {"scan", "all"}:
            raise RuntimeError("operational read failed")
        return {"Items": list(self.scan_items)}

    def get_item(self, **kwargs):
        if self.fail in {"get", "all"}:
            raise RuntimeError("operational read failed")
        key = kwargs["Key"]
        value = next(iter(key.values()))
        item = self.records.get(value)
        if item is None:
            return {}
        return {"Item": item}

    def query(self, **kwargs):
        if self.fail in {"query", "all"}:
            raise RuntimeError("assessment query failed")
        return {"Items": list(self.query_items)}


def _tables(
    *,
    aircraft=None,
    projections=None,
    projection_records=None,
    hazards=None,
    hazard_records=None,
    encounters=None,
    encounter_records=None,
    risks=None,
    risk_records=None,
    recommendations=None,
    recommendation_records=None,
    assessments=None,
    aircraft_table=None,
    recommendation_table=None,
    assessment_table=None,
):
    return readers.OperationalTables(
        aircraft=aircraft_table
        or MemoryTable(records={AIRCRAFT: aircraft} if aircraft else {}),
        projections=MemoryTable(
            scan_items=projections or [],
            records=projection_records or {},
        ),
        projection_points=MemoryTable(),
        hazards=MemoryTable(scan_items=hazards or [], records=hazard_records or {}),
        hazard_coordinates=MemoryTable(),
        encounters=MemoryTable(
            scan_items=encounters or [],
            records=encounter_records or {},
        ),
        risks=MemoryTable(scan_items=risks or [], records=risk_records or {}),
        airports=MemoryTable(),
        metar=MemoryTable(),
        taf=MemoryTable(),
        taf_periods=MemoryTable(),
        airport_assessments=assessment_table
        or MemoryTable(query_items=assessments or []),
        recommendations=recommendation_table
        or MemoryTable(
            scan_items=recommendations or [],
            records=recommendation_records or {},
        ),
        alerts=MemoryTable(),
    )


def _aircraft():
    return {"aircraft_id": AIRCRAFT, "expires_at_epoch": NOW + 100}


def _projection():
    return {
        "aircraft_id": AIRCRAFT,
        "projection_id": PROJECTION,
        "generated_at_epoch": NOW,
        "valid_until_epoch": NOW + 1000,
        "projection_status": "READY",
        "aircraft_state_version": "abc123#1",
    }


def _hazard():
    return {
        "hazard_id": HAZARD,
        "source_version": "v1",
        "status": "ACTIVE",
        "materialization_status": "READY",
        "valid_to_epoch": NOW + 1000,
    }


def _encounter():
    return {
        "encounter_id": ENCOUNTER,
        "aircraft_id": AIRCRAFT,
        "projection_id": PROJECTION,
        "hazard_id": HAZARD,
        "hazard_source_version": "v1",
        "encounter_state": "DETECTED",
    }


def _risk():
    return {
        "risk_id": RISK,
        "encounter_id": ENCOUNTER,
        "generated_at_epoch": NOW,
        "generated_at_utc": "2026-09-06T03:00:00Z",
        "risk_level": "LOW",
        "risk_score": 12,
        "confidence": "HIGH",
        "scoring_ruleset_version": "risk-rules-1",
        "reasons": ["stored reason"],
        "limitations": ["stored limitation"],
    }


def _current_recommendation(recommendation_id="rec-1", action="MONITOR", **extra):
    item = {
        "recommendation_id": recommendation_id,
        "recommendation_version": "ver-1",
        "ruleset_version": "rec-rules-1",
        "recommendation_status": "ACTIVE",
        "valid_from_utc": "2026-09-06T03:00:00Z",
        "valid_until_utc": FUTURE,
        "risk_id": RISK,
        "primary_action_type": action,
        "advisory_notice": "Advisory only. Not a clearance.",
        "confidence": "MEDIUM",
        "reasons": ["stored recommendation reason"],
        "limitations": ["stored recommendation limitation"],
        "evidence_references": [{"type": "RISK_RESULT", "id": RISK}],
        "source_versions": {"hazard_source_version": "v1"},
    }
    item.update(extra)
    return item


def _assessment_row(evaluation_id, assessment_id="aa-1"):
    return {
        "evaluation_id": evaluation_id,
        "airport_id": "KDEN",
        "airport_assessment_id": assessment_id,
        "risk_id": RISK,
        "aircraft_id": AIRCRAFT,
        "aircraft_state_version": "state-1",
        "hazard_id": HAZARD,
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


def _persisted_recommendation(recommendation_id="rec-1", evaluation_id="eval-1", **extra):
    item = {
        "recommendation_id": recommendation_id,
        "recommendation_version": "ver-1",
        "airport_evaluation_id": evaluation_id,
        "source_versions": {
            "airport_evaluation_id": evaluation_id,
            "hazard_source_version": "v1",
        },
        "evidence_references": [
            {"type": "AIRPORT_ASSESSMENT_EVALUATION", "id": evaluation_id},
        ],
        "hazard_source_version": "v1",
        "risk_id": RISK,
        "aircraft_id": AIRCRAFT,
        "hazard_id": HAZARD,
        "created_at_utc": REC_UTC,
        "created_at_epoch": REC_EPOCH,
    }
    item.update(extra)
    return item


def _operational(
    *,
    include_risk=True,
    recommendations=None,
    assessments=None,
    aircraft_table=None,
):
    if recommendations is None:
        recommendations = [_current_recommendation()]
    projection = _projection()
    hazard = _hazard()
    encounter = _encounter()
    risk = _risk()
    return _tables(
        aircraft=_aircraft(),
        projections=[projection],
        projection_records={PROJECTION: projection},
        hazards=[hazard],
        hazard_records={HAZARD: hazard},
        encounters=[encounter],
        encounter_records={ENCOUNTER: encounter},
        risks=[risk] if include_risk else [],
        risk_records={RISK: risk} if include_risk else {},
        recommendations=recommendations,
        recommendation_records={
            item["recommendation_id"]: item for item in recommendations
        },
        assessments=assessments,
        aircraft_table=aircraft_table,
    )


def _boundary_tables():
    evaluation_id = "eval-boundary"
    recommendation = _current_recommendation(
        "rec-boundary",
        airport_evaluation_id=evaluation_id,
        hazard_source_version="v1",
        aircraft_id=AIRCRAFT,
        hazard_id=HAZARD,
        created_at_utc=REC_UTC,
        created_at_epoch=REC_EPOCH,
        source_versions={
            "hazard_source_version": "v1",
            "airport_evaluation_id": evaluation_id,
        },
        evidence_references=[
            {"type": "RISK_RESULT", "id": RISK},
            {"type": "AIRPORT_ASSESSMENT_EVALUATION", "id": evaluation_id},
        ],
    )
    return _operational(
        recommendations=[recommendation],
        assessments=[_assessment_row(evaluation_id, "aa-boundary")],
    )


def _persisted_tables(**overrides):
    recommendation = _persisted_recommendation(**overrides)
    return _tables(
        recommendations=[recommendation],
        recommendation_records={recommendation["recommendation_id"]: recommendation},
        assessments=[_assessment_row(recommendation["airport_evaluation_id"])],
    )


def _invoke(tables, tool_name, arguments, tool_call_id="tool-dt6"):
    runtime = DecisionToolsRuntime(
        tables=tables,
        now_epoch=NOW,
        query_timestamp_utc=QUERY,
        correlation_id="corr-dt6",
    )
    return DecisionToolsAdapter(runtime).invoke(
        tool_name,
        arguments,
        tool_call_id=tool_call_id,
    )


def _record_ids(result):
    return [
        record.record_id
        for item in result.evidence
        for record in item.source_records
    ]


def _source_identities(result):
    return [
        (
            item.source,
            tuple(
                (record.record_id, record.source_version, record.event_timestamp_utc)
                for record in item.source_records
            ),
        )
        for item in result.evidence
    ]


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


def _assert_no_claims(invocation):
    trees = (
        invocation.raw_tool_result.data,
        invocation.model_projection.to_dict(),
    )
    for tree in trees:
        assert _keys(tree).isdisjoint(_CLAIM_KEYS)


def _recommendation_ids(payload):
    return [
        item["recommendation_id"] for item in payload["recommendations"]["current"]
    ]


def test_current_context_flow_preserves_stored_low_risk():
    invocation = _invoke(
        _operational(),
        "get_current_decision_context",
        {"aircraft_id": AIRCRAFT},
    )
    raw = invocation.raw_tool_result
    projected = invocation.model_projection
    encounter = raw.data["encounters"][0]
    projected_encounter = projected.data["encounters"][0]

    assert raw.status is ToolResultStatus.SUCCESS
    assert raw.temporal_scope is TemporalScope.CURRENT
    assert projected.temporal_scope is TemporalScope.CURRENT
    assert raw.data["kind"] == "DECISION_CONTEXT"
    assert projected.data["kind"] == "DECISION_CONTEXT"
    assert raw.data["aircraft_id"] == AIRCRAFT
    assert raw.data["projection_id"] == PROJECTION
    assert encounter["encounter_id"] == ENCOUNTER
    assert encounter["risk"]["presence"] == "PRESENT"
    assert encounter["risk"]["risk_level"] == "LOW"
    assert encounter["risk"]["risk_id"] == RISK
    assert projected_encounter["risk"] == encounter["risk"]
    assert raw.data["risk"] is None
    assert {AIRCRAFT, PROJECTION, ENCOUNTER, HAZARD, RISK, "rec-1"} <= set(
        _record_ids(raw)
    )
    _assert_no_claims(invocation)


def test_current_risk_flow_keeps_stored_low_without_recommendations():
    invocation = _invoke(
        _operational(),
        "get_current_risk_evidence",
        {"aircraft_id": AIRCRAFT},
    )
    raw = invocation.raw_tool_result
    risk = raw.data["risk"]

    assert raw.temporal_scope is TemporalScope.CURRENT
    assert invocation.model_projection.temporal_scope is TemporalScope.CURRENT
    assert raw.data["kind"] == "RISK_EVIDENCE"
    assert raw.data["aircraft_id"] == AIRCRAFT
    assert risk["encounter_id"] == ENCOUNTER
    assert risk["risk_id"] == RISK
    assert risk["presence"] == "PRESENT"
    assert risk["risk_level"] == "LOW"
    assert raw.data["recommendations"] is None
    assert "primary_action_type" not in _keys(raw.data)
    assert invocation.model_projection.data["risk"]["risk_level"] == "LOW"
    assert RISK in _record_ids(raw)
    _assert_no_claims(invocation)


def test_multiple_recommendations_keep_both_ids_and_no_winner():
    invocation = _invoke(
        _operational(
            recommendations=[
                _current_recommendation("rec-1", "MONITOR"),
                _current_recommendation("rec-2", "EVALUATE_DIVERSION"),
            ]
        ),
        "get_current_recommendation",
        {"aircraft_id": AIRCRAFT},
    )
    raw = invocation.raw_tool_result

    assert raw.temporal_scope is TemporalScope.CURRENT
    assert raw.data["kind"] == "RECOMMENDATION_EVIDENCE"
    assert _recommendation_ids(raw.data) == ["rec-1", "rec-2"]
    assert _recommendation_ids(invocation.model_projection.data) == ["rec-1", "rec-2"]
    assert set(raw.data["recommendations"]) == {
        "current",
        "absence_state",
        "excluded_stale_ids",
    }
    _assert_no_claims(invocation)


def test_cross_tool_facts_agree_across_different_shapes():
    tables = _operational(
        recommendations=[
            _current_recommendation("rec-1", "MONITOR"),
            _current_recommendation("rec-2", "EVALUATE_DIVERSION"),
        ]
    )
    context_result = _invoke(tables, "get_current_decision_context", {"aircraft_id": AIRCRAFT})
    risk_result = _invoke(tables, "get_current_risk_evidence", {"aircraft_id": AIRCRAFT})
    recommendation_result = _invoke(
        tables,
        "get_current_recommendation",
        {"aircraft_id": AIRCRAFT},
    )
    context_data = context_result.raw_tool_result.data
    risk_data = risk_result.raw_tool_result.data
    recommendation_data = recommendation_result.raw_tool_result.data
    encounter = context_data["encounters"][0]

    assert context_data["aircraft_id"] == risk_data["aircraft_id"] == AIRCRAFT
    assert (
        context_data["projection_id"]
        == risk_data["projection_id"]
        == recommendation_data["projection_id"]
        == PROJECTION
    )
    assert encounter["encounter_id"] == ENCOUNTER
    assert risk_data["risk"]["encounter_id"] == ENCOUNTER
    assert recommendation_data["risk"]["encounter_id"] == ENCOUNTER
    assert encounter["risk"]["risk_id"] == risk_data["risk"]["risk_id"] == RISK
    assert _recommendation_ids(recommendation_data) == ["rec-1", "rec-2"]
    assert [
        item["recommendation_id"]
        for item in encounter["recommendations"]["current"]
    ] == ["rec-1", "rec-2"]
    assert context_data["encounters"]
    assert context_data["risk"] is None
    assert context_data["recommendations"] is None
    assert risk_data["recommendations"] is None
    assert risk_data["encounters"] == []
    assert recommendation_data["encounters"] == []
    assert recommendation_data["recommendations"]["current"]


def test_absent_risk_is_not_low_on_context_or_risk_tool():
    tables = _operational(include_risk=False, recommendations=[])
    context_result = _invoke(
        tables,
        "get_current_decision_context",
        {"aircraft_id": AIRCRAFT},
    )
    risk_result = _invoke(tables, "get_current_risk_evidence", {"aircraft_id": AIRCRAFT})
    context_risk = context_result.raw_tool_result.data["encounters"][0]["risk"]
    narrow_risk = risk_result.raw_tool_result.data["risk"]

    for risk in (context_risk, narrow_risk):
        assert risk["presence"] == "ABSENT"
        assert risk["risk_level"] is None
        assert risk["risk_score"] is None
        assert risk["risk_id"] is None
    assert context_result.raw_tool_result.data["aircraft_id"] == AIRCRAFT
    assert risk_result.raw_tool_result.data["aircraft_id"] == AIRCRAFT
    assert "LOW" not in {
        context_risk["risk_level"],
        narrow_risk["risk_level"],
        risk_result.model_projection.data["risk"]["risk_level"],
    }


def test_persisted_airport_flow_stays_persisted_and_unavailable_capabilities():
    invocation = _invoke(
        _persisted_tables(),
        "get_persisted_airport_candidate_evidence",
        {"recommendation_id": "rec-1"},
    )
    raw = invocation.raw_tool_result
    projected = invocation.model_projection
    candidate = raw.data["candidates"][0]

    assert raw.temporal_scope is TemporalScope.PERSISTED
    assert projected.temporal_scope is TemporalScope.PERSISTED
    assert raw.data["recommendation_id"] == "rec-1"
    assert raw.data["airport_evaluation_id"] == "eval-1"
    assert candidate["airport_assessment_id"] == "aa-1"
    assert candidate["route_safety_status"] == "UNAVAILABLE"
    assert candidate["runway_evidence_status"] == "UNAVAILABLE"
    assert candidate["congestion_evidence_status"] == "UNAVAILABLE"
    assert all(item.freshness_status is FreshnessStatus.UNKNOWN for item in raw.evidence)
    assert all(NOT_CURRENT_LIMITATION in item.limitations for item in raw.evidence)
    assert all(
        item.freshness_status is FreshnessStatus.UNKNOWN for item in projected.evidence
    )
    assert all(NOT_CURRENT_LIMITATION in item.limitations for item in projected.evidence)
    assert {"get_recommendation_record", "query_airport_assessments_for_evaluation"} == {
        item.source for item in raw.evidence
    }
    assert {"rec-1", "aa-1"} <= set(_record_ids(raw))
    _assert_no_claims(invocation)


def test_current_recommendation_does_not_make_airport_evaluation_current():
    tables = _boundary_tables()
    current = _invoke(tables, "get_current_recommendation", {"aircraft_id": AIRCRAFT})
    recommendation_id = _recommendation_ids(current.raw_tool_result.data)[0]
    persisted = _invoke(
        tables,
        "get_persisted_airport_candidate_evidence",
        {"recommendation_id": recommendation_id},
    )

    assert recommendation_id == "rec-boundary"
    assert current.raw_tool_result.temporal_scope is TemporalScope.CURRENT
    assert current.model_projection.temporal_scope is TemporalScope.CURRENT
    assert persisted.raw_tool_result.temporal_scope is TemporalScope.PERSISTED
    assert persisted.model_projection.temporal_scope is TemporalScope.PERSISTED
    assert persisted.raw_tool_result.data["airport_evaluation_id"] == "eval-boundary"
    assert (
        persisted.raw_tool_result.data["candidates"][0]["airport_assessment_id"]
        == "aa-boundary"
    )
    assert all(
        item.freshness_status is FreshnessStatus.UNKNOWN
        for item in persisted.raw_tool_result.evidence
    )
    assert all(
        NOT_CURRENT_LIMITATION in item.limitations
        for item in persisted.model_projection.evidence
    )
    _assert_no_claims(current)
    _assert_no_claims(persisted)


def test_current_read_failure_stays_unavailable():
    tables = _operational(aircraft_table=MemoryTable(fail="all"))
    invocation = _invoke(
        tables,
        "get_current_decision_context",
        {"aircraft_id": AIRCRAFT},
    )
    raw = invocation.raw_tool_result

    assert raw.status is ToolResultStatus.UNAVAILABLE
    assert raw.data["kind"] == "DECISION_CONTEXT"
    assert raw.data["evaluation_state"] == "SOURCE_UNAVAILABLE"
    assert raw.data["aircraft_id"] is None
    assert raw.data["risk"] is None
    assert invocation.model_projection.status is ToolResultStatus.UNAVAILABLE
    assert invocation.model_projection.temporal_scope is TemporalScope.CURRENT
    assert raw.status is not ToolResultStatus.NOT_FOUND
    assert "MONITOR" not in _keys(raw.data)


def test_recommendation_read_failure_stays_unavailable():
    recommendation = _persisted_recommendation()
    tables = _tables(
        recommendation_table=MemoryTable(
            records={recommendation["recommendation_id"]: recommendation},
            fail="get",
        )
    )
    invocation = _invoke(
        tables,
        "get_persisted_airport_candidate_evidence",
        {"recommendation_id": "rec-1"},
    )
    raw = invocation.raw_tool_result

    assert raw.status is ToolResultStatus.UNAVAILABLE
    assert raw.temporal_scope is TemporalScope.PERSISTED
    assert raw.data["candidates"] == []
    assert raw.evidence == ()
    assert invocation.model_projection.status is ToolResultStatus.UNAVAILABLE
    assert invocation.model_projection.temporal_scope is TemporalScope.PERSISTED
    assert raw.status is not ToolResultStatus.NOT_FOUND
    assert raw.status is not ToolResultStatus.SUCCESS
    assert raw.status is not ToolResultStatus.UNKNOWN


def test_assessment_query_failure_keeps_recommendation_evidence_only():
    recommendation = _persisted_recommendation()
    tables = _tables(
        recommendation_records={recommendation["recommendation_id"]: recommendation},
        assessment_table=MemoryTable(fail="query"),
    )
    invocation = _invoke(
        tables,
        "get_persisted_airport_candidate_evidence",
        {"recommendation_id": "rec-1"},
    )
    raw = invocation.raw_tool_result

    assert raw.status is ToolResultStatus.UNAVAILABLE
    assert raw.temporal_scope is TemporalScope.PERSISTED
    assert invocation.model_projection.status is ToolResultStatus.UNAVAILABLE
    assert invocation.model_projection.temporal_scope is TemporalScope.PERSISTED
    assert [item.source for item in raw.evidence] == ["get_recommendation_record"]
    assert _record_ids(raw) == ["rec-1"]
    assert raw.data["candidates"] == []
    assert raw.data["airport_evaluation_id"] == "eval-1"


def test_contradiction_unknown_does_not_cite_complete_rows():
    recommendation = _persisted_recommendation(
        no_suitable_candidate_reason=NO_COMPLETE_ASSESSMENT,
    )
    tables = _tables(
        recommendation_records={"rec-1": recommendation},
        assessments=[_assessment_row("eval-1")],
    )
    invocation = _invoke(
        tables,
        "get_persisted_airport_candidate_evidence",
        {"recommendation_id": "rec-1"},
    )
    raw = invocation.raw_tool_result

    assert raw.status is ToolResultStatus.UNKNOWN
    assert raw.data["candidates"] == []
    assert [item.source for item in raw.evidence] == ["get_recommendation_record"]
    assert "aa-1" not in _record_ids(raw)
    assert invocation.model_projection.status is ToolResultStatus.UNKNOWN


def test_identical_inputs_are_deterministic_and_call_id_is_supplied():
    tables = _operational()
    arguments = {"aircraft_id": AIRCRAFT}
    first = _invoke(tables, "get_current_decision_context", arguments, "tool-same")
    second = _invoke(tables, "get_current_decision_context", arguments, "tool-same")
    other = _invoke(tables, "get_current_decision_context", arguments, "tool-other")

    assert first.raw_tool_result.status == second.raw_tool_result.status
    assert first.raw_tool_result.temporal_scope == second.raw_tool_result.temporal_scope
    assert first.raw_tool_result.data == second.raw_tool_result.data
    assert _source_identities(first.raw_tool_result) == _source_identities(
        second.raw_tool_result
    )
    assert first.model_projection.to_dict() == second.model_projection.to_dict()
    assert other.raw_tool_result.tool_call_id == "tool-other"
    assert other.raw_tool_result.data == first.raw_tool_result.data
    assert _source_identities(other.raw_tool_result) == _source_identities(
        first.raw_tool_result
    )
    assert other.model_projection.to_dict() == first.model_projection.to_dict()


def test_successful_results_use_current_or_persisted_scope_only():
    current_tables = _operational()
    persisted_tables = _persisted_tables()
    results = [
        _invoke(current_tables, "get_current_decision_context", {"aircraft_id": AIRCRAFT}),
        _invoke(current_tables, "get_current_risk_evidence", {"aircraft_id": AIRCRAFT}),
        _invoke(current_tables, "get_current_recommendation", {"aircraft_id": AIRCRAFT}),
        _invoke(
            persisted_tables,
            "get_persisted_airport_candidate_evidence",
            {"recommendation_id": "rec-1"},
        ),
    ]
    for invocation in results[:3]:
        raw = invocation.raw_tool_result
        assert raw.temporal_scope is TemporalScope.CURRENT
        assert invocation.model_projection.temporal_scope is TemporalScope.CURRENT
        assert raw.status in _CURRENT_STATUSES
        assert raw.status is not ToolResultStatus.UNKNOWN
        assert raw.status is not ToolResultStatus.STALE
        _assert_no_claims(invocation)
    persisted = results[3].raw_tool_result
    assert persisted.temporal_scope is TemporalScope.PERSISTED
    assert results[3].model_projection.temporal_scope is TemporalScope.PERSISTED
    assert persisted.status in _PERSISTED_STATUSES
    assert persisted.status is not ToolResultStatus.STALE
    assert persisted.temporal_scope is not TemporalScope.HISTORICAL
    assert persisted.temporal_scope is not TemporalScope.HYBRID
    _assert_no_claims(results[3])


def test_decision_modules_do_not_call_write_operations():
    for path in MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        called = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                called.add(func.attr)
            elif isinstance(func, ast.Name):
                called.add(func.id)
        assert called.isdisjoint(_WRITE_CALLS)


def test_decision_modules_do_not_import_model_providers():
    script = """
import sys
before = set(sys.modules)
import wilvor_ai.decision_contracts
import wilvor_ai.decision_context
import wilvor_ai.persisted_airport_contracts
import wilvor_ai.persisted_airport_evidence
import wilvor_ai.decision_tools
import wilvor_ai.decision_tool_projection
loaded = set(sys.modules) - before
forbidden = ("anthropic", "openai", "langgraph", "wilvor_ai.providers")
assert not any(
    name == marker or name.startswith(marker + ".")
    for name in loaded
    for marker in forbidden
)
"""
    env = os.environ.copy()
    pythonpath = str(SHARED)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_as_of_uses_supplied_now_epoch():
    invocation = _invoke(
        _operational(),
        "get_current_decision_context",
        {"aircraft_id": AIRCRAFT},
    )
    assert invocation.raw_tool_result.as_of_utc == context._epoch_to_utc_z(NOW)
