"""Synthetic Phase 0 AI contract examples.

These fixtures describe test-only records and make no claims about live
aviation operations.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


QUERY_TIME = "2026-09-07T19:00:00Z"
CURRENT_EVENT_TIME = "2026-09-07T18:59:30Z"
HISTORICAL_EVENT_TIME = "2026-08-25T12:00:00Z"


def source_record(
    record_id: str,
    *,
    source_version: str | None,
    event_timestamp_utc: str | None,
) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "source_version": source_version,
        "event_timestamp_utc": event_timestamp_utc,
    }


def evidence(
    *,
    source: str,
    records: list[dict[str, Any]],
    freshness_status: str,
    confidence: str,
    limitations: list[str],
    tool_call_id: str,
    temporal_scope: str,
) -> dict[str, Any]:
    return {
        "schema_version": "wilvor.ai.evidence.v1",
        "source": source,
        "source_records": records,
        "query_timestamp_utc": QUERY_TIME,
        "freshness_status": freshness_status,
        "confidence": confidence,
        "limitations": limitations,
        "tool_call_id": tool_call_id,
        "temporal_scope": temporal_scope,
    }


CURRENT_EVIDENCE = evidence(
    source="wilvor.risk_results",
    records=[
        source_record(
            "risk#test-current-001",
            source_version="wilvor.risk_results.v4.0",
            event_timestamp_utc=CURRENT_EVENT_TIME,
        )
    ],
    freshness_status="FRESH",
    confidence="HIGH",
    limitations=[],
    tool_call_id="tool-call-test-current-001",
    temporal_scope="CURRENT",
)

HISTORICAL_EVIDENCE = evidence(
    source="wilvor.historical_encounter_analytics",
    records=[
        source_record(
            "historical-encounter-test-001",
            source_version="analytics-snapshot-test-v1",
            event_timestamp_utc=HISTORICAL_EVENT_TIME,
        )
    ],
    # The event is intentionally old. FRESH refers to the known fitness of
    # the analytical source for the requested historical period.
    freshness_status="FRESH",
    confidence="MEDIUM",
    limitations=[],
    tool_call_id="tool-call-test-historical-001",
    temporal_scope="HISTORICAL",
)


VALID_TOOL_RESULTS: dict[str, dict[str, Any]] = {
    "complete_current": {
        "schema_version": "wilvor.ai.tool_result.v1",
        "tool_name": "test_current_risk_lookup",
        "tool_call_id": "tool-call-test-current-001",
        "status": "SUCCESS",
        "temporal_scope": "CURRENT",
        "data": {
            "risk_id": "risk#test-current-001",
            "risk_level": "LOW",
            "count": 1,
        },
        "evidence": [CURRENT_EVIDENCE],
        "as_of_utc": QUERY_TIME,
        "limitations": [],
        "correlation_id": "correlation-test-current-001",
    },
    "historical": {
        "schema_version": "wilvor.ai.tool_result.v1",
        "tool_name": "test_historical_encounter_summary",
        "tool_call_id": "tool-call-test-historical-001",
        "status": "SUCCESS",
        "temporal_scope": "HISTORICAL",
        "data": {
            "period_start_utc": "2026-08-24T00:00:00Z",
            "period_end_utc": "2026-08-31T00:00:00Z",
            "encounter_count": 4,
        },
        "evidence": [HISTORICAL_EVIDENCE],
        "as_of_utc": QUERY_TIME,
        "limitations": [],
        "correlation_id": "correlation-test-historical-001",
    },
    "genuinely_hybrid": {
        "schema_version": "wilvor.ai.tool_result.v1",
        "tool_name": "test_combined_current_historical_comparison",
        "tool_call_id": "tool-call-test-hybrid-001",
        "status": "SUCCESS",
        "temporal_scope": "HYBRID",
        "data": {
            "current_count": 1,
            "historical_period_count": 4,
        },
        "evidence": [
            {
                **CURRENT_EVIDENCE,
                "tool_call_id": "tool-call-test-hybrid-001",
            },
            {
                **HISTORICAL_EVIDENCE,
                "tool_call_id": "tool-call-test-hybrid-001",
            },
        ],
        "as_of_utc": QUERY_TIME,
        "limitations": [],
        "correlation_id": "correlation-test-hybrid-001",
    },
    "unknown_preserved": {
        "schema_version": "wilvor.ai.tool_result.v1",
        "tool_name": "test_unknown_preservation",
        "tool_call_id": "tool-call-test-unknown-001",
        "status": "SUCCESS",
        "temporal_scope": "CURRENT",
        "data": {
            "altitude_overlap_status": "UNKNOWN",
            "estimated_altitude_ft": None,
            "inside_now": False,
            "matched_record_count": 0,
            "items": [],
        },
        "evidence": [
            evidence(
                source="wilvor.aircraft_hazard_encounters",
                records=[
                    source_record(
                        "encounter#test-unknown-001",
                        source_version=(
                            "wilvor.aircraft_hazard_encounter.v4.0"
                        ),
                        event_timestamp_utc=CURRENT_EVENT_TIME,
                    )
                ],
                freshness_status="UNKNOWN",
                confidence="UNKNOWN",
                limitations=[
                    "Current source freshness could not be established."
                ],
                tool_call_id="tool-call-test-unknown-001",
                temporal_scope="CURRENT",
            )
        ],
        "as_of_utc": QUERY_TIME,
        "limitations": [
            "Aircraft-to-hazard altitude overlap is unknown."
        ],
        "correlation_id": None,
    },
    "stale": {
        "schema_version": "wilvor.ai.tool_result.v1",
        "tool_name": "test_stale_current_state",
        "tool_call_id": "tool-call-test-stale-001",
        "status": "STALE",
        "temporal_scope": "CURRENT",
        "data": {"aircraft_id": "aircraft-test-001"},
        "evidence": [
            evidence(
                source="wilvor.aircraft_current_state",
                records=[
                    source_record(
                        "aircraft-test-001",
                        source_version="aircraft-test-001#1788807000",
                        event_timestamp_utc="2026-09-07T17:50:00Z",
                    )
                ],
                freshness_status="STALE",
                confidence="LOW",
                limitations=[
                    "Aircraft state exceeds the applicable current-state "
                    "timeliness threshold."
                ],
                tool_call_id="tool-call-test-stale-001",
                temporal_scope="CURRENT",
            )
        ],
        "as_of_utc": "2026-09-07T17:50:00Z",
        "limitations": ["Current aircraft evidence is stale."],
        "correlation_id": "correlation-test-stale-001",
    },
    "partial": {
        "schema_version": "wilvor.ai.tool_result.v1",
        "tool_name": "test_partial_airport_assessment",
        "tool_call_id": "tool-call-test-partial-001",
        "status": "PARTIAL",
        "temporal_scope": "CURRENT",
        "data": {
            "evaluation_id": "eval#test-partial-001",
            "complete_count": 1,
            "waiting_for_weather_count": 1,
        },
        "evidence": [
            evidence(
                source="wilvor.airport_assessments",
                records=[
                    source_record(
                        "aa#test-complete-001",
                        source_version="wilvor.airport_assessment.v1",
                        event_timestamp_utc=CURRENT_EVENT_TIME,
                    )
                ],
                freshness_status="FRESH",
                confidence="MEDIUM",
                limitations=[],
                tool_call_id="tool-call-test-partial-001",
                temporal_scope="CURRENT",
            ),
            evidence(
                source="wilvor.airport_weather",
                records=[],
                freshness_status="UNAVAILABLE",
                confidence="UNKNOWN",
                limitations=[
                    "Weather evidence is unavailable for one synthetic "
                    "candidate."
                ],
                tool_call_id="tool-call-test-partial-001",
                temporal_scope="CURRENT",
            ),
        ],
        "as_of_utc": QUERY_TIME,
        "limitations": [
            "One synthetic candidate is waiting for weather evidence."
        ],
        "correlation_id": "correlation-test-partial-001",
    },
    "source_unavailable": {
        "schema_version": "wilvor.ai.tool_result.v1",
        "tool_name": "test_unavailable_source",
        "tool_call_id": "tool-call-test-unavailable-001",
        "status": "UNAVAILABLE",
        "temporal_scope": "HISTORICAL",
        "data": None,
        "evidence": [
            evidence(
                source="wilvor.historical_risk_analytics",
                records=[],
                freshness_status="UNAVAILABLE",
                confidence="UNKNOWN",
                limitations=[
                    "The analytical source is unavailable for the requested "
                    "test period."
                ],
                tool_call_id="tool-call-test-unavailable-001",
                temporal_scope="HISTORICAL",
            )
        ],
        "as_of_utc": None,
        "limitations": [
            "Historical risk analytics are unavailable for the test request."
        ],
        "correlation_id": None,
    },
    "not_found": {
        "schema_version": "wilvor.ai.tool_result.v1",
        "tool_name": "test_no_matching_records",
        "tool_call_id": "tool-call-test-not-found-001",
        "status": "NOT_FOUND",
        "temporal_scope": "HISTORICAL",
        "data": {"items": [], "count": 0},
        "evidence": [
            evidence(
                source="wilvor.historical_recommendation_analytics",
                records=[],
                freshness_status="FRESH",
                confidence="HIGH",
                limitations=[],
                tool_call_id="tool-call-test-not-found-001",
                temporal_scope="HISTORICAL",
            )
        ],
        "as_of_utc": QUERY_TIME,
        "limitations": [],
        "correlation_id": "correlation-test-not-found-001",
    },
    "multiple_source_records": {
        "schema_version": "wilvor.ai.tool_result.v1",
        "tool_name": "test_multiple_risk_records",
        "tool_call_id": "tool-call-test-multiple-001",
        "status": "SUCCESS",
        "temporal_scope": "HISTORICAL",
        "data": {"risk_record_count": 2},
        "evidence": [
            evidence(
                source="wilvor.risk_results",
                records=[
                    source_record(
                        "risk#test-multiple-001",
                        source_version="wilvor.risk_results.v4.0",
                        event_timestamp_utc="2026-08-25T12:00:00Z",
                    ),
                    source_record(
                        "risk#test-multiple-002",
                        source_version=None,
                        event_timestamp_utc="2026-08-26T12:00:00Z",
                    ),
                ],
                freshness_status="UNKNOWN",
                confidence="UNKNOWN",
                limitations=[
                    "Analytical source fitness was not independently "
                    "established for this test period."
                ],
                tool_call_id="tool-call-test-multiple-001",
                temporal_scope="HISTORICAL",
            )
        ],
        "as_of_utc": QUERY_TIME,
        "limitations": [
            "One source record does not expose a source version."
        ],
        "correlation_id": None,
    },
}


VALID_FINAL_RESPONSES: dict[str, dict[str, Any]] = {
    "hybrid_from_independent_results": {
        "schema_version": "wilvor.ai.response.v1",
        "answer": (
            "The synthetic current count is 1; the synthetic historical "
            "period count is 4."
        ),
        "evidence": [
            CURRENT_EVIDENCE,
            HISTORICAL_EVIDENCE,
        ],
        "as_of_utc": QUERY_TIME,
        "confidence": "MEDIUM",
        "limitations": [],
        "visualizations": [],
        "navigation": [],
        "human_review_required": False,
        "temporal_scope": "HYBRID",
        "correlation_id": "correlation-test-final-hybrid-001",
    },
    "informational_without_required_review": {
        "schema_version": "wilvor.ai.response.v1",
        "answer": "The synthetic historical period contains 4 records.",
        "evidence": [HISTORICAL_EVIDENCE],
        "as_of_utc": QUERY_TIME,
        "confidence": "MEDIUM",
        "limitations": [],
        "visualizations": [],
        "navigation": [],
        "human_review_required": False,
        "temporal_scope": "HISTORICAL",
        "correlation_id": None,
    },
    "safety_sensitive_with_required_review": {
        "schema_version": "wilvor.ai.response.v1",
        "answer": (
            "A qualified human must review the synthetic diversion-support "
            "output."
        ),
        "evidence": [CURRENT_EVIDENCE],
        "as_of_utc": QUERY_TIME,
        "confidence": "HIGH",
        "limitations": [
            "This synthetic output is advisory and does not execute a reroute."
        ],
        "visualizations": [],
        "navigation": [],
        "human_review_required": True,
        "temporal_scope": "CURRENT",
        "correlation_id": "correlation-test-safety-001",
    },
    "unknown_with_limitations": {
        "schema_version": "wilvor.ai.response.v1",
        "answer": "UNKNOWN",
        "evidence": [
            VALID_TOOL_RESULTS["unknown_preserved"]["evidence"][0]
        ],
        "as_of_utc": QUERY_TIME,
        "confidence": "UNKNOWN",
        "limitations": [
            "The requested synthetic value is not established by evidence."
        ],
        "visualizations": [],
        "navigation": [],
        "human_review_required": True,
        "temporal_scope": "CURRENT",
        "correlation_id": None,
    },
}


def valid_tool_result(name: str) -> dict[str, Any]:
    return deepcopy(VALID_TOOL_RESULTS[name])


def valid_final_response(name: str) -> dict[str, Any]:
    return deepcopy(VALID_FINAL_RESPONSES[name])
