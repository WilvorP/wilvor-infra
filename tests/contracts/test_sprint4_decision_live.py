"""Sprint 4 Decision live integration: persisted evidence, weather, and safety."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wilvor_ai.contracts import (
    AgentCapability,
    ContractValidationError,
    FreshnessStatus,
    TemporalScope,
    ToolResultStatus,
)
from wilvor_ai.decision_contracts import RiskPresence, StoredRiskLevel
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.sprint3c_scoped_read import (
    GRANT_CONSUMPTION_LIFECYCLE,
    LEDGER_EXECUTION_STATE_STARTED,
    describe_consumed_attempt_read_binding,
)
from wilvor_ai.sprint4_decision_live import (
    ABSENT_RECOMMENDATION_IS_MONITOR,
    ABSENT_RISK_IS_LOW_RISK,
    APPROVED_SOURCE_ADAPTER,
    DIRECT_AWS_AUTHORITY_PERMITTED,
    LIVE_AWS_OPERATION_PERMITTED,
    OPERATIONAL_DIRECTIVES_ENABLED,
    PERSISTED_WEATHER_IS_CURRENT_OBSERVATION,
    PRODUCTION_DECISION_LIVE_AWS_ENABLED,
    Sprint4DecisionHostAuthority,
    Sprint4DecisionLiveCall,
    execute_sprint4_decision_live,
    issue_production_decision_live_aws,
    open_production_decision_live_aws,
)
from wilvor_ai.sprint4_decision_reconciliation import RecordObservation
from wilvor_ai.sprint4_decision_weather_context import (
    MISSING_WEATHER_IS_LOW_RISK,
    MISSING_WEATHER_IS_VFR,
    PERSISTED_WEATHER_IS_CURRENT_OBSERVATION as WEATHER_CURRENT,
    WeatherContextStatus,
)
from wilvor_operational import readers


NOW = 1_788_661_800
FUTURE = "2026-09-06T04:00:00Z"
QUERY = "2026-09-30T20:00:00Z"
AIRCRAFT = "abc123"
PROJECTION = "proj-1"
ENCOUNTER = "proj-1#hazard-1#v1"
HAZARD = "hazard-1"
RISK = "risk-1"
AIRPORT = "KDEN"
REC_UTC = "2026-09-30T18:00:00Z"
REC_EPOCH = int(datetime(2026, 9, 30, 18, tzinfo=timezone.utc).timestamp())
CAND_UTC = "2026-09-30T17:00:00+00:00"
CAND_EPOCH = int(datetime(2026, 9, 30, 17, tzinfo=timezone.utc).timestamp())
EXPIRES = CAND_EPOCH + 86400
RULESET = "wilvor.airport-assessment.ruleset.v1"
ACCEPTED = "2026-10-27T16:00:00Z"
DEADLINE = "2026-10-27T20:00:00Z"


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


class FakeDirectAws:
    def put_item(self, **kwargs):
        raise AssertionError("writes are forbidden")

    def get_item(self, **kwargs):
        raise AssertionError("direct aws get_item is forbidden")


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
    metar_records=None,
    taf_records=None,
    metar_fail=None,
    taf_fail=None,
):
    return readers.OperationalTables(
        aircraft=MemoryTable(records={AIRCRAFT: aircraft} if aircraft else {}),
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
        metar=MemoryTable(records=metar_records or {}, fail=metar_fail),
        taf=MemoryTable(records=taf_records or {}, fail=taf_fail),
        taf_periods=MemoryTable(),
        airport_assessments=MemoryTable(query_items=assessments or []),
        recommendations=MemoryTable(
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


def _risk(level="LOW"):
    return {
        "risk_id": RISK,
        "encounter_id": ENCOUNTER,
        "generated_at_epoch": NOW,
        "generated_at_utc": "2026-09-06T03:00:00Z",
        "risk_level": level,
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
        "airport_id": AIRPORT,
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


def _metar(version="m1"):
    return {
        "station_id": AIRPORT,
        "metar_version": version,
        "observed_time_utc": "2026-09-30T16:55:00Z",
        "raw_text": f"{AIRPORT} 301655Z AUTO",
    }


def _taf(version="t1"):
    return {
        "station_id": AIRPORT,
        "taf_version": version,
        "source_version": version,
        "issued_at_utc": "2026-09-30T16:00:00Z",
    }


def _operational(*, include_risk=True, recommendations=None, assessments=None, **weather):
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
        **weather,
    )


def _full_tables(*, metar_version="m1", taf_version="t1", include_risk=True):
    evaluation_id = "eval-1"
    recommendation = _current_recommendation(
        "rec-1",
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
        include_risk=include_risk,
        recommendations=[recommendation],
        assessments=[_assessment_row(evaluation_id)],
        metar_records={AIRPORT: _metar(metar_version)},
        taf_records={AIRPORT: _taf(taf_version)},
    )


def _persisted_tables(**weather):
    recommendation = _persisted_recommendation()
    return _tables(
        recommendations=[recommendation],
        recommendation_records={recommendation["recommendation_id"]: recommendation},
        assessments=[_assessment_row(recommendation["airport_evaluation_id"])],
        **weather,
    )


def _execute(**overrides):
    values = {
        "tables": _full_tables(),
        "now_epoch": NOW,
        "query_timestamp_utc": QUERY,
        "aircraft_id": AIRCRAFT,
        "recommendation_id": "rec-1",
        "correlation_id": "corr-sprint4-decision",
    }
    values.update(overrides)
    return execute_sprint4_decision_live(Sprint4DecisionLiveCall(**values))


def _decision_binding(**overrides):
    payload = {
        "owner_id": "owner-dec",
        "attempt_token": "attempt-dec",
        "fence_epoch": 1,
        "grant_nonce": "grant-nonce-dec",
        "idempotency_key": "idem-dec",
        "payload_fingerprint": "fingerprint-dec",
        "ledger_state": LEDGER_EXECUTION_STATE_STARTED,
        "grant_consumption_lifecycle": GRANT_CONSUMPTION_LIFECYCLE,
        "master_request_id": "req-dec",
        "planning_session_id": "plan-dec",
        "step_id": "step-dec",
        "invocation_id": "inv-dec",
        "specialist": MasterSpecialist.DECISION,
        "purpose": MasterPurpose.ANSWER,
        "capability": AgentCapability.EXPLAIN_EXISTING_RISK,
        "principal_ref": "principal-1",
        "policy_revision": "policy-1",
        "tool_name": "get_current_risk_evidence",
        "resource_id": "aircraft",
        "operation": ReadOperation.GET_ITEM,
        "region_scope": ("CA",),
        "accepted_at_utc": ACCEPTED,
        "deadline_utc": DEADLINE,
        "correlation_id": "corr-dec",
        "decision_aircraft_id": AIRCRAFT,
        "decision_recommendation_id": "rec-1",
    }
    payload.update(overrides)
    return describe_consumed_attempt_read_binding(**payload)


def test_closed_flags_stay_false():
    assert PRODUCTION_DECISION_LIVE_AWS_ENABLED is False
    assert LIVE_AWS_OPERATION_PERMITTED is False
    assert APPROVED_SOURCE_ADAPTER is False
    assert DIRECT_AWS_AUTHORITY_PERMITTED is False
    assert ABSENT_RISK_IS_LOW_RISK is False
    assert ABSENT_RECOMMENDATION_IS_MONITOR is False
    assert OPERATIONAL_DIRECTIVES_ENABLED is False
    assert PERSISTED_WEATHER_IS_CURRENT_OBSERVATION is False
    assert WEATHER_CURRENT is False
    assert MISSING_WEATHER_IS_VFR is False
    assert MISSING_WEATHER_IS_LOW_RISK is False


def test_four_tools_join_persisted_weather_and_keep_advisory_limits():
    result = _execute()
    risk = result.invocations["get_current_risk_evidence"].raw_tool_result
    recommendation = result.invocations["get_current_recommendation"].raw_tool_result
    persisted = result.invocations[
        "get_persisted_airport_candidate_evidence"
    ].raw_tool_result
    weather = result.weather[0]

    assert set(result.invocations) == {
        "get_current_decision_context",
        "get_current_risk_evidence",
        "get_current_recommendation",
        "get_persisted_airport_candidate_evidence",
    }
    assert risk.status is ToolResultStatus.SUCCESS
    assert risk.data["risk"]["presence"] == RiskPresence.PRESENT.value
    assert risk.data["risk"]["risk_level"] == StoredRiskLevel.LOW.value
    assert recommendation.data["recommendations"]["current"][0]["primary_action_type"] == (
        "MONITOR"
    )
    assert persisted.temporal_scope is TemporalScope.PERSISTED
    assert persisted.data["candidates"][0]["airport_id"] == AIRPORT
    assert persisted.data["candidates"][0]["metar_version"] == "m1"
    assert weather.metar.status is WeatherContextStatus.MATCHED
    assert weather.taf.status is WeatherContextStatus.MATCHED
    assert weather.temporal_scope is TemporalScope.PERSISTED
    assert weather.metar.is_current_operational_observation is False
    assert result.freshness.weather_freshness is FreshnessStatus.UNKNOWN
    assert result.freshness.persisted_treated_as_current is False
    assert result.freshness.source_observation_copied_from_query is False
    assert result.missing.risk is RecordObservation.PRESENT
    assert result.missing.synthesized_low_risk is False
    assert result.missing.stored_low_risk_present is True
    assert result.advisory.advisory_only is True
    assert result.advisory.operational_directives_present is False
    assert result.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert result.production_live_aws is False
    assert result.live_aws_initiated is False
    assert result.host_authority is Sprint4DecisionHostAuthority.INJECTED_HOST_TABLES
    assert result.entity_links.risk_id == RISK
    assert "rec-1" in result.entity_links.recommendation_ids
    assert any("advisory" in item.casefold() for item in result.advisory.limitations)


def test_missing_risk_is_absent_not_low():
    result = _execute(tables=_full_tables(include_risk=False), recommendation_id=None)
    risk = result.invocations["get_current_risk_evidence"].raw_tool_result.data["risk"]

    assert result.missing.risk is RecordObservation.ABSENT
    assert result.missing.synthesized_low_risk is False
    assert result.missing.stored_low_risk_present is False
    assert risk["presence"] == RiskPresence.ABSENT.value
    assert risk["risk_level"] is None
    assert risk["risk_score"] is None
    assert "LOW" not in {risk["risk_level"]}
    assert _NOT_LOW_PHRASE(result)


def test_stored_low_is_distinct_from_missing_risk():
    present = _execute(recommendation_id=None)
    missing = _execute(tables=_full_tables(include_risk=False), recommendation_id=None)

    assert present.missing.risk is RecordObservation.PRESENT
    assert present.missing.stored_low_risk_present is True
    assert missing.missing.risk is RecordObservation.ABSENT
    assert missing.missing.stored_low_risk_present is False
    assert present.invocations["get_current_risk_evidence"].raw_tool_result.data["risk"][
        "risk_level"
    ] == "LOW"


def test_missing_recommendation_is_not_monitor():
    tables = _operational(recommendations=[])
    result = _execute(tables=tables, recommendation_id=None)
    payload = result.invocations["get_current_recommendation"].raw_tool_result.data

    assert result.missing.recommendation is RecordObservation.ABSENT
    assert result.missing.synthesized_monitor is False
    assert payload["recommendations"]["current"] == []
    assert "MONITOR" not in str(payload["recommendations"]["current"])
    assert any("does not mean MONITOR" in item for item in result.advisory.limitations)


def test_evaluate_diversion_stays_stored_advisory_evidence():
    tables = _operational(
        recommendations=[_current_recommendation("rec-div", "EVALUATE_DIVERSION")]
    )
    result = _execute(tables=tables, recommendation_id=None, aircraft_id=AIRCRAFT)
    actions = [
        item["primary_action_type"]
        for item in result.invocations["get_current_recommendation"].raw_tool_result.data[
            "recommendations"
        ]["current"]
    ]

    assert actions == ["EVALUATE_DIVERSION"]
    assert result.advisory.advisory_only is True
    assert result.advisory.operational_directives_present is False
    assert result.missing.synthesized_diversion is False


def test_missing_metar_is_not_vfr_or_low_weather_risk():
    result = _execute(
        tables=_persisted_tables(metar_records={}, taf_records={AIRPORT: _taf()}),
        aircraft_id=None,
        recommendation_id="rec-1",
    )
    weather = result.weather[0]

    assert weather.metar.status is WeatherContextStatus.MISSING
    assert weather.metar.flight_category_inferred is False
    assert weather.metar.is_current_operational_observation is False
    assert weather.taf.status is WeatherContextStatus.MATCHED
    assert any(
        "not VFR" in limitation
        for item in weather.evidence
        for limitation in item.limitations
    )
    assert result.freshness.weather_freshness is FreshnessStatus.UNKNOWN


def test_metar_version_mismatch_is_not_repaired_or_treated_current():
    result = _execute(
        tables=_full_tables(metar_version="m-other"),
    )
    weather = result.weather[0]

    assert weather.metar.status is WeatherContextStatus.VERSION_MISMATCH
    assert weather.metar.cited_version == "m1"
    assert weather.metar.observed_version == "m-other"
    assert weather.metar.is_current_operational_observation is False
    assert any(
        item.status is not None
        for item in (weather.metar,)
    )
    from wilvor_ai.sprint4_decision_reconciliation import EntityLinkDefect

    assert EntityLinkDefect.WEATHER_VERSION_MISMATCH in result.entity_links.defects


def test_unavailable_weather_read_does_not_invent_conditions():
    result = _execute(
        tables=_persisted_tables(
            metar_records={AIRPORT: _metar()},
            taf_records={AIRPORT: _taf()},
            metar_fail="get",
        ),
        aircraft_id=None,
        recommendation_id="rec-1",
    )

    assert result.weather[0].metar.status is WeatherContextStatus.UNAVAILABLE
    assert result.weather[0].metar.observed_version is None
    assert result.weather[0].metar.flight_category_inferred is False


def test_persisted_airport_without_candidates_does_not_select_diversion():
    tables = _tables(
        recommendation_records={"rec-empty": _persisted_recommendation("rec-empty", "eval-empty")},
        recommendations=[_persisted_recommendation("rec-empty", "eval-empty")],
        assessments=[],
    )
    recommendation = tables.recommendations.records["rec-empty"]
    recommendation["no_suitable_candidate_reason"] = (
        "No candidate airport assessment was available."
    )
    result = _execute(tables=tables, aircraft_id=None, recommendation_id="rec-empty")
    persisted = result.invocations[
        "get_persisted_airport_candidate_evidence"
    ].raw_tool_result

    assert persisted.data["candidates"] == []
    assert result.weather == ()
    assert result.missing.airport_assessment is RecordObservation.ABSENT
    assert result.missing.synthesized_diversion is False
    assert persisted.temporal_scope is TemporalScope.PERSISTED


def test_direct_aws_client_is_refused():
    with pytest.raises(ContractValidationError, match="unapproved_direct_aws_authority"):
        Sprint4DecisionLiveCall(
            tables=FakeDirectAws(),
            now_epoch=NOW,
            query_timestamp_utc=QUERY,
            aircraft_id=AIRCRAFT,
        )


def test_caller_cannot_mint_low_from_missing_risk():
    with pytest.raises(ContractValidationError, match="absent_risk_is_not_low"):
        Sprint4DecisionLiveCall(
            tables=_operational(),
            now_epoch=NOW,
            query_timestamp_utc=QUERY,
            aircraft_id=AIRCRAFT,
            request_missing_risk_as_low=True,
        )


def test_caller_cannot_mint_monitor_from_missing_recommendation():
    with pytest.raises(ContractValidationError, match="absent_recommendation_is_not_monitor"):
        Sprint4DecisionLiveCall(
            tables=_operational(),
            now_epoch=NOW,
            query_timestamp_utc=QUERY,
            aircraft_id=AIRCRAFT,
            request_missing_recommendation_as_monitor=True,
        )


def test_operational_directive_request_is_refused():
    with pytest.raises(ContractValidationError, match="operational_directives_not_permitted"):
        Sprint4DecisionLiveCall(
            tables=_operational(),
            now_epoch=NOW,
            query_timestamp_utc=QUERY,
            aircraft_id=AIRCRAFT,
            request_operational_directive=True,
        )


def test_live_aws_flag_is_refused():
    with pytest.raises(ContractValidationError, match="live_aws_operation_not_permitted"):
        Sprint4DecisionLiveCall(
            tables=_operational(),
            now_epoch=NOW,
            query_timestamp_utc=QUERY,
            aircraft_id=AIRCRAFT,
            live_aws=True,
        )


def test_production_issuers_stay_closed():
    with pytest.raises(ContractValidationError, match="production_decision_live_aws_not_enabled"):
        open_production_decision_live_aws()
    with pytest.raises(ContractValidationError, match="production_grant_issuer_not_integrated"):
        issue_production_decision_live_aws()


def test_consumed_attempt_binds_decision_identity_without_becoming_a_grant():
    binding = _decision_binding()
    result = _execute(consumed_attempt=binding)

    assert result.host_authority is Sprint4DecisionHostAuthority.CONSUMED_ATTEMPT_BINDING
    assert result.production_live_aws is False
    assert binding.production_grant is False
    with pytest.raises(ContractValidationError, match="consumed_attempt_is_not_a_production_grant"):
        bool(binding)


def test_consumed_attempt_rejects_live_ops_specialist():
    binding = _decision_binding(specialist=MasterSpecialist.LIVE_OPS)
    with pytest.raises(ContractValidationError, match="specialist_mismatch"):
        Sprint4DecisionLiveCall(
            tables=_full_tables(),
            now_epoch=NOW,
            query_timestamp_utc=QUERY,
            aircraft_id=AIRCRAFT,
            consumed_attempt=binding,
        )


def test_consumed_attempt_rejects_foreign_aircraft_target():
    binding = _decision_binding(decision_aircraft_id="other-aircraft")
    with pytest.raises(ContractValidationError, match="decision_target_mismatch"):
        Sprint4DecisionLiveCall(
            tables=_full_tables(),
            now_epoch=NOW,
            query_timestamp_utc=QUERY,
            aircraft_id=AIRCRAFT,
            recommendation_id="rec-1",
            consumed_attempt=binding,
        )


def test_query_clock_is_not_the_source_observation_clock():
    result = _execute()
    weather_records = [
        record.event_timestamp_utc
        for item in result.weather[0].evidence
        for record in item.source_records
    ]

    assert result.freshness.query_timestamp_utc == QUERY
    assert result.freshness.evaluation_clock_utc != QUERY
    assert "2026-09-30T16:55:00Z" in weather_records
    assert QUERY not in weather_records
    assert result.freshness.source_observation_copied_from_query is False


def test_current_and_persisted_temporal_scopes_stay_separate():
    result = _execute()

    assert result.invocations["get_current_risk_evidence"].raw_tool_result.temporal_scope is (
        TemporalScope.CURRENT
    )
    assert result.invocations[
        "get_persisted_airport_candidate_evidence"
    ].raw_tool_result.temporal_scope is TemporalScope.PERSISTED
    assert result.freshness.current_temporal_scope is TemporalScope.CURRENT
    assert result.freshness.persisted_temporal_scope is TemporalScope.PERSISTED
    assert result.freshness.weather_temporal_scope is TemporalScope.PERSISTED


def test_entity_link_keeps_risk_and_recommendation_on_the_same_aircraft():
    result = _execute()

    assert result.entity_links.requested_aircraft_id == AIRCRAFT
    assert result.entity_links.returned_aircraft_id == AIRCRAFT
    assert result.entity_links.risk_id == RISK
    assert result.entity_links.defects == ()


def test_target_required():
    with pytest.raises(ContractValidationError, match="decision_target_required"):
        Sprint4DecisionLiveCall(
            tables=_operational(),
            now_epoch=NOW,
            query_timestamp_utc=QUERY,
        )


def _NOT_LOW_PHRASE(result):
    return any("not mean LOW" in item for item in result.advisory.limitations)
