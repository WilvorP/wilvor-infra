"""Producer EventBridge detail completeness without DynamoDB or transport."""

from __future__ import annotations

import json
from decimal import Decimal
from types import ModuleType
from typing import Callable

import pytest

from wilvor_historical.from_events import fact_from_event


def _put_events_capture(store):
    def _put_events(*, Entries):
        store.extend(Entries)
        return {"FailedEntryCount": 0, "Entries": [{"EventId": "evt-1"}]}

    return _put_events


def test_encounter_event_adds_historical_fields_without_changing_item():
    from tests.unit.encounter import test_encounter_processor as encounter_tests

    app = encounter_tests.app
    projection = encounter_tests.projection()
    hazard = encounter_tests.hazard()
    geometry_result = {
        "geometry_overlap_status": "CORRIDOR_ONLY_INTERSECTION",
        "corridor_intersects": True,
        "centerline_intersects": False,
        "inside_now": False,
        "exact_intersection_confirmed": True,
    }
    item = app.build_encounter_item(
        projection=projection,
        hazard=hazard,
        candidate={"hazard_version_key": "hazard-1#v1"},
        matched_h3_cells=["8428309ffffffff"],
        geometry_result=geometry_result,
        detected_epoch=encounter_tests.NOW,
    )
    assert "matched_h3_cells" in item
    assert "expires_at_epoch" in item
    assert "fact_schema_version" not in item
    assert item["inside_now"] is False

    captured = []
    app.eventbridge.put_events = _put_events_capture(captured)
    app.publish_encounter_event(item=item, detail_type="encounter.updated")

    entry = captured[0]
    assert entry["Source"] == "wilvor.encounter"
    assert entry["DetailType"] == "encounter.updated"
    detail = json.loads(entry["Detail"])
    assert "matched_h3_cells" not in detail
    assert "expires_at_epoch" not in detail
    fact = fact_from_event("wilvor.encounter", "encounter.updated", detail)
    assert fact.inside_now is False
    assert fact.corridor_intersects is True
    assert fact.geometry_hash == "geom-1"
    assert fact.hazard_type == "CONVECTION"
    assert fact.resolved_at_utc is None


def test_encounter_resolved_event_includes_resolved_at():
    from tests.unit.encounter import test_encounter_processor as encounter_tests

    app = encounter_tests.app
    item = {
        "encounter_id": "proj-1#hazard-1#v1",
        "aircraft_id": "abc123",
        "aircraft_state_version": "state-v1",
        "projection_id": "proj-1",
        "hazard_id": "hazard-1",
        "hazard_source_version": "v1",
        "hazard_version_key": "hazard-1#v1",
        "encounter_state": "SUPERSEDED",
        "geometry_overlap_status": "INSIDE_NOW",
        "time_overlap_status": "OVERLAP",
        "altitude_overlap_status": "OVERLAP",
        "exact_intersection_confirmed": True,
        "detected_at_epoch": encounter_tests.NOW,
        "detected_at_utc": app.epoch_to_utc(encounter_tests.NOW),
        "resolution_reason": "newer projection",
        "geometry_hash": "geom-1",
        "hazard_type": "CONVECTION",
        "inside_now": True,
        "corridor_intersects": True,
        "resolved_at_epoch": encounter_tests.NOW + 10,
        "resolved_at_utc": app.epoch_to_utc(encounter_tests.NOW + 10),
        "correlation_id": "corr-1",
        "schema_version": "wilvor.aircraft_hazard_encounter.v4.0",
        "expires_at_epoch": encounter_tests.NOW + 3600,
        "matched_h3_cells": ["cell"],
    }
    captured = []
    app.eventbridge.put_events = _put_events_capture(captured)
    app.publish_encounter_event(item=item, detail_type="encounter.resolved")
    detail = json.loads(captured[0]["Detail"])
    assert captured[0]["DetailType"] == "encounter.resolved"
    fact = fact_from_event("wilvor.encounter", "encounter.resolved", detail)
    assert fact.resolved_at_epoch == encounter_tests.NOW + 10
    assert "matched_h3_cells" not in detail


def test_risk_event_adds_ruleset_and_maps_both_detail_types_to_risk_result():
    from tests.unit.risk import test_risk_processor as risk_tests

    app = risk_tests.app
    item = app.build_risk_result(risk_tests.base_encounter())
    assert "expires_at_epoch" in item
    assert "reasons" in item
    assert "fact_schema_version" not in item

    captured = []
    app.eventbridge.put_events = _put_events_capture(captured)
    updated = app.publish_risk_event(item=item, encounter_state="DETECTED")
    resolved = app.publish_risk_event(item=item, encounter_state="SUPERSEDED")
    assert updated == "risk.updated"
    assert resolved == "risk.resolved"

    updated_detail = json.loads(captured[0]["Detail"])
    resolved_detail = json.loads(captured[1]["Detail"])
    assert captured[0]["Source"] == "wilvor.risk"
    assert "expires_at_epoch" not in updated_detail
    assert "reasons" not in updated_detail
    updated_fact = fact_from_event("wilvor.risk", "risk.updated", updated_detail)
    resolved_fact = fact_from_event(
        "wilvor.risk",
        "risk.resolved",
        resolved_detail,
    )
    assert updated_fact.record_id == resolved_fact.record_id == item["risk_id"]
    assert updated_fact.dedup_id == resolved_fact.dedup_id
    assert updated_fact.fact_kind.value == "RISK_RESULT"
    assert resolved_fact.fact_kind.value == "RISK_RESULT"
    assert updated_fact.scoring_ruleset_version == "wilvor.risk.ruleset.v2"
    assert updated_fact.scoring_config_version == "wilvor.risk.config.dev.v1"
    assert updated_fact.encounter_state == "DETECTED"
    assert resolved_fact.encounter_state == "SUPERSEDED"
    assert updated_fact.producer_detail_type == "risk.updated"
    assert resolved_fact.producer_detail_type == "risk.resolved"


def test_hazard_materialized_event_is_complete_and_reuses_ready_timestamp(
    load_repo_module: Callable[[str, str], ModuleType],
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ACTIVE_HAZARDS_TABLE_NAME", "test-active-hazards")
    monkeypatch.setenv("HAZARD_COORDINATES_TABLE_NAME", "test-hazard-coordinates")
    monkeypatch.setenv("HAZARD_CELLS_TABLE_NAME", "test-hazard-cells")
    monkeypatch.setenv("IMPACT_CELLS_TABLE_NAME", "test-impact-cells")
    monkeypatch.setenv("H3_RESOLUTION", "4")
    monkeypatch.setenv("IMPACT_GRID_DISTANCE", "2")
    monkeypatch.setenv("IMPACT_RADIUS_NM", "50")
    monkeypatch.setenv("SCHEMA_VERSION", "wilvor.active_hazards.v4.0")
    monkeypatch.setenv("RETENTION_AFTER_VALID_TO_HOURS", "6")
    monkeypatch.setenv("BAD_RECORDS_BUCKET_NAME", "test-sigmet-archive")
    monkeypatch.setenv("BAD_RECORDS_PREFIX", "bad-records/source=sigmet_processor")
    monkeypatch.setenv("EVENT_BUS_NAME", "default")
    sigmet_processor = load_repo_module(
        "historical_facts_sigmet_publisher_app",
        "functions/weather/sigmet/processor/app.py",
    )
    ready = {
        "hazard_id": "sigmet-aaa",
        "source_version": "v1",
        "materialization_id": "hazard-materialization-1",
        "status": "ACTIVE",
        "correlation_id": "poll-1:0",
        "schema_version": "wilvor.active_hazards.v4.0",
        "materialized_at_utc": "2026-07-18T12:30:00+00:00",
        "geometry_hash": "copied-hash-v1",
        "geometry_type": "POLYGON",
        "geometry_point_count": 5,
        "product_type": "SIGMET",
        "hazard_type": "TURBULENCE",
        "valid_from_utc": "2026-07-18T12:00:00+00:00",
        "valid_to_utc": "2026-07-18T18:00:00+00:00",
        "amendment_type": "ORIGINAL",
        "created_at_utc": "2026-07-18T12:00:00+00:00",
        "received_at_utc": "2026-07-18T12:01:00+00:00",
        "source_product_id": "prod-1",
        "source_system": "NOAA_AVIATIONWEATHER_SIGMET",
        "severity": "SEV",
        "minimum_lower_altitude_ft": Decimal("180"),
        "maximum_upper_altitude_ft": Decimal("400"),
        "source_icao_id": "KZNY",
        "series_id": "12",
        "alpha_char": "A",
        "raw_s3_uri": "s3://bucket/key",
    }
    captured = []
    sigmet_processor.events_client.put_events = _put_events_capture(captured)
    published = sigmet_processor.publish_hazard_materialized(
        active_hazard=ready,
        dependent_counts={
            "hazard_coordinates_written": 5,
            "hazard_cells_written": 2,
            "impact_cells_written": 3,
        },
    )
    assert published == 1
    entry = captured[0]
    assert entry["Source"] == "wilvor.weather"
    assert entry["DetailType"] == "hazard.materialized"
    detail = json.loads(entry["Detail"])
    assert detail["published_at_utc"] == ready["materialized_at_utc"]
    assert detail["materialized_at_utc"] == ready["materialized_at_utc"]
    assert detail["published_at_utc"] == detail["materialized_at_utc"]
    fact = fact_from_event("wilvor.weather", "hazard.materialized", detail)
    assert fact.record_id == "sigmet-aaa#v1"
    assert fact.geometry_hash == "copied-hash-v1"
    assert fact.event_time_utc == "2026-07-18T12:30:00Z"
    assert fact.valid_from_utc == "2026-07-18T12:00:00+00:00"
    assert fact.minimum_lower_altitude_ft == 180
