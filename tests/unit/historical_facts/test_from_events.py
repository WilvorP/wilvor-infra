"""Event-to-fact mapping, identities, and dedup semantics."""

from __future__ import annotations

import pytest

from wilvor_historical.contracts import FactKind
from wilvor_historical.from_events import (
    GEOMETRY_IN_MEMORY_DETAIL_TYPE,
    HistoricalMappingError,
    build_hazard_geometry_fact,
    fact_from_event,
)


def _encounter_detail(**overrides):
    detail = {
        "encounter_id": "proj-1#hazard-1#v1",
        "aircraft_id": "abc123",
        "aircraft_state_version": "state-v1",
        "projection_id": "proj-1",
        "hazard_id": "hazard-1",
        "hazard_source_version": "v1",
        "hazard_version_key": "hazard-1#v1",
        "encounter_state": "DETECTED",
        "geometry_overlap_status": "INSIDE_NOW",
        "time_overlap_status": "OVERLAP",
        "altitude_overlap_status": "OVERLAP",
        "exact_intersection_confirmed": True,
        "detected_at_epoch": 1_700_000_000,
        "detected_at_utc": "2023-11-14T22:13:20Z",
        "geometry_hash": "geom-1",
        "hazard_type": "CONVECTION",
        "inside_now": True,
        "corridor_intersects": True,
        "schema_version": "wilvor.aircraft_hazard_encounter.v4.0",
    }
    detail.update(overrides)
    return detail


def _risk_detail(**overrides):
    detail = {
        "risk_id": "risk#same",
        "encounter_id": "proj-1#hazard-1#v1",
        "aircraft_id": "abc123",
        "hazard_id": "hazard-1",
        "hazard_source_version": "v1",
        "projection_id": "proj-1",
        "risk_score": 70,
        "risk_level": "HIGH",
        "generated_at_epoch": 1_700_000_000,
        "generated_at_utc": "2023-11-14T22:13:20Z",
        "scoring_ruleset_version": "wilvor.risk.ruleset.v2",
        "scoring_config_version": "wilvor.risk.config.dev.v1",
        "hazard_type": "CONVECTION",
        "encounter_state": "DETECTED",
        "confidence": "HIGH",
        "freshness_status": "FRESH",
        "valid_until_utc": "2023-11-14T23:13:20Z",
        "schema_version": "wilvor.risk_results.v4.0",
    }
    detail.update(overrides)
    return detail


def _hazard_detail(**overrides):
    detail = {
        "hazard_id": "sigmet-aaa",
        "source_version": "v1",
        "hazard_version_key": "sigmet-aaa#v1",
        "materialization_id": "hazard-materialization-1",
        "materialization_status": "READY",
        "status": "ACTIVE",
        "geometry_hash": "copied-hash-v1",
        "geometry_type": "POLYGON",
        "geometry_point_count": 5,
        "product_type": "SIGMET",
        "hazard_type": "TURBULENCE",
        "valid_from_utc": "2026-07-18T12:00:00+00:00",
        "valid_to_utc": "2026-07-18T18:00:00+00:00",
        "materialized_at_utc": "2026-07-18T12:30:00+00:00",
        "published_at_utc": "2026-07-18T12:30:00+00:00",
        "amendment_type": "ORIGINAL",
        "created_at_utc": "2026-07-18T12:00:00+00:00",
        "received_at_utc": "2026-07-18T12:01:00+00:00",
        "source_product_id": "KZNY|SIGMET|A|12",
        "source_system": "NOAA_AVIATIONWEATHER_SIGMET",
        "schema_version": "wilvor.active_hazards.v4.0",
        "correlation_id": "poll-1:0",
    }
    detail.update(overrides)
    return detail


def test_encounter_updated_and_resolved_kinds():
    observed = fact_from_event(
        "wilvor.encounter",
        "encounter.updated",
        _encounter_detail(),
    )
    terminal = fact_from_event(
        "wilvor.encounter",
        "encounter.resolved",
        _encounter_detail(
            encounter_state="RESOLVED",
            resolved_at_epoch=1_700_000_200,
            resolved_at_utc="2023-11-14T22:16:40Z",
        ),
    )
    assert observed.fact_kind is FactKind.ENCOUNTER_OBSERVED
    assert terminal.fact_kind is FactKind.ENCOUNTER_TERMINAL
    assert observed.record_id == terminal.record_id


def test_duplicate_payload_has_same_dedup_id():
    first = fact_from_event(
        "wilvor.encounter",
        "encounter.updated",
        _encounter_detail(),
    )
    second = fact_from_event(
        "wilvor.encounter",
        "encounter.updated",
        _encounter_detail(),
    )
    assert first.dedup_id == second.dedup_id


def test_same_encounter_different_evaluation_time_has_new_observation_dedup():
    first = fact_from_event(
        "wilvor.encounter",
        "encounter.updated",
        _encounter_detail(),
    )
    second = fact_from_event(
        "wilvor.encounter",
        "encounter.updated",
        _encounter_detail(
            detected_at_epoch=1_700_000_060,
            detected_at_utc="2023-11-14T22:14:20Z",
        ),
    )
    assert first.record_id == second.record_id
    assert first.dedup_id != second.dedup_id


def test_detected_to_monitoring_keeps_encounter_identity():
    detected = fact_from_event(
        "wilvor.encounter",
        "encounter.updated",
        _encounter_detail(encounter_state="DETECTED"),
    )
    monitoring = fact_from_event(
        "wilvor.encounter",
        "encounter.updated",
        _encounter_detail(
            encounter_state="MONITORING",
            exact_intersection_confirmed=False,
            inside_now=False,
            corridor_intersects=False,
        ),
    )
    assert detected.record_id == monitoring.record_id
    assert detected.dedup_id != monitoring.dedup_id


def test_new_projection_or_hazard_version_is_new_encounter():
    original = fact_from_event(
        "wilvor.encounter",
        "encounter.updated",
        _encounter_detail(),
    )
    new_projection = fact_from_event(
        "wilvor.encounter",
        "encounter.updated",
        _encounter_detail(
            encounter_id="proj-2#hazard-1#v1",
            projection_id="proj-2",
        ),
    )
    new_version = fact_from_event(
        "wilvor.encounter",
        "encounter.updated",
        _encounter_detail(
            encounter_id="proj-1#hazard-1#v2",
            hazard_source_version="v2",
            hazard_version_key="hazard-1#v2",
        ),
    )
    assert original.record_id != new_projection.record_id
    assert original.record_id != new_version.record_id


def test_risk_updated_and_resolved_are_the_same_result_fact():
    updated = fact_from_event(
        "wilvor.risk",
        "risk.updated",
        _risk_detail(),
    )
    resolved = fact_from_event(
        "wilvor.risk",
        "risk.resolved",
        _risk_detail(encounter_state="SUPERSEDED"),
    )
    assert updated.fact_kind is FactKind.RISK_RESULT
    assert resolved.fact_kind is FactKind.RISK_RESULT
    assert updated.record_id == resolved.record_id == "risk#same"
    assert updated.dedup_id == resolved.dedup_id == "risk#same"
    assert updated.producer_detail_type == "risk.updated"
    assert resolved.producer_detail_type == "risk.resolved"
    assert updated.event_time_utc == "2023-11-14T22:13:20Z"


def test_multiple_risk_ids_for_one_encounter_stay_separate():
    first = fact_from_event(
        "wilvor.risk",
        "risk.updated",
        _risk_detail(risk_id="risk#one"),
    )
    second = fact_from_event(
        "wilvor.risk",
        "risk.updated",
        _risk_detail(risk_id="risk#two", risk_score=40, risk_level="MEDIUM"),
    )
    assert first.encounter_id == second.encounter_id
    assert first.record_id != second.record_id


def test_hazard_amendment_versions_are_isolated():
    v1 = fact_from_event(
        "wilvor.weather",
        "hazard.materialized",
        _hazard_detail(),
    )
    v2 = fact_from_event(
        "wilvor.weather",
        "hazard.materialized",
        _hazard_detail(
            source_version="v2",
            hazard_version_key="sigmet-aaa#v2",
            geometry_hash="copied-hash-v2",
            materialized_at_utc="2026-07-18T13:00:00+00:00",
            published_at_utc="2026-07-18T13:00:00+00:00",
            amendment_type="AMENDMENT",
        ),
    )
    assert v1.hazard_id == v2.hazard_id
    assert v1.source_version != v2.source_version
    assert v1.record_id != v2.record_id
    assert v1.geometry_hash == "copied-hash-v1"
    assert v2.geometry_hash == "copied-hash-v2"
    assert v1.event_time_utc == "2026-07-18T12:30:00Z"
    assert v1.event_year == "2026"
    assert v1.event_month == "07"
    assert v1.event_day == "18"
    assert v1.event_time_utc != v1.valid_from_utc
    assert v1.product_type == "SIGMET"
    assert v1.hazard_type == "TURBULENCE"


def test_unknown_event_is_rejected():
    with pytest.raises(HistoricalMappingError, match="unsupported historical event"):
        fact_from_event("wilvor.weather", "HazardCoordinates.materialized", {})
    with pytest.raises(HistoricalMappingError, match="unsupported historical event"):
        fact_from_event("wilvor.weather", "hazard.geometry.materialized", {})


def test_geometry_builder_rejects_transport_selection():
    with pytest.raises(HistoricalMappingError, match="transport is not selected"):
        build_hazard_geometry_fact(
            active_hazard=_hazard_detail(),
            geometry_points=[],
            producer_detail_type="hazard.geometry.materialized",
        )
    assert GEOMETRY_IN_MEMORY_DETAIL_TYPE == "in_memory.hazard_geometry"


def test_missing_canonical_identity_or_time_is_rejected():
    with pytest.raises(HistoricalMappingError, match="missing encounter_id"):
        fact_from_event(
            "wilvor.encounter",
            "encounter.updated",
            _encounter_detail(encounter_id=""),
        )
    with pytest.raises(HistoricalMappingError, match="missing detected_at_utc"):
        incomplete = _encounter_detail()
        del incomplete["detected_at_utc"]
        fact_from_event("wilvor.encounter", "encounter.updated", incomplete)
    with pytest.raises(HistoricalMappingError, match="missing resolved_at"):
        fact_from_event(
            "wilvor.encounter",
            "encounter.resolved",
            _encounter_detail(encounter_state="RESOLVED"),
        )
