"""Additive Domain-1 / geometry visibility without semantic producer changes."""

from __future__ import annotations

import json
from types import ModuleType
from typing import Any, Callable

import pytest


def test_encounter_put_events_failure_emits_metric_and_still_raises(
    monkeypatch: pytest.MonkeyPatch,
):
    from tests.unit.encounter import test_encounter_processor as encounter_tests

    app = encounter_tests.app
    metrics: list[dict[str, Any]] = []

    def _put_metric_data(**kwargs):
        metrics.append(kwargs)

    monkeypatch.setattr(app.cloudwatch, "put_metric_data", _put_metric_data)
    monkeypatch.setattr(
        app.eventbridge,
        "put_events",
        lambda **kwargs: {"FailedEntryCount": 1, "Entries": []},
    )
    item = {
        "encounter_id": "proj-1#hazard-1#v1",
        "aircraft_id": "abc123",
        "projection_id": "proj-1",
        "hazard_id": "hazard-1",
    }
    with pytest.raises(RuntimeError, match="Failed to publish"):
        app.publish_encounter_event(item=item, detail_type="encounter.updated")
    assert metrics
    metric = metrics[0]["MetricData"][0]
    assert metric["MetricName"] == "HistoricalSourcePutFailure"
    assert any(
        dim["Name"] == "Stage" and dim["Value"] == "historical_source"
        for dim in metric["Dimensions"]
    )


def test_risk_put_events_failure_emits_metric_and_still_raises(
    monkeypatch: pytest.MonkeyPatch,
):
    from tests.unit.risk import test_risk_processor as risk_tests

    app = risk_tests.app
    metrics: list[dict[str, Any]] = []
    monkeypatch.setattr(
        app.cloudwatch,
        "put_metric_data",
        lambda **kwargs: metrics.append(kwargs),
    )
    monkeypatch.setattr(
        app.eventbridge,
        "put_events",
        lambda **kwargs: {"FailedEntryCount": 1, "Entries": []},
    )
    item = app.build_risk_result(risk_tests.base_encounter())
    with pytest.raises(RuntimeError, match="Failed to publish"):
        app.publish_risk_event(item=item, encounter_state="DETECTED")
    assert metrics[0]["MetricData"][0]["MetricName"] == "HistoricalSourcePutFailure"


def test_hazard_materialized_failure_emits_source_metric(
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
    app = load_repo_module(
        "historical_facts_sigmet_visibility_app",
        "functions/weather/sigmet/processor/app.py",
    )
    emitted: list[str] = []
    monkeypatch.setattr(
        app,
        "emit_metric",
        lambda **kwargs: emitted.append(next(iter(kwargs["metrics"]))),
    )
    monkeypatch.setattr(app, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(app, "_write_historical_gap_fail_open", lambda **kwargs: None)
    app.events_client.put_events = lambda **kwargs: {"FailedEntryCount": 1}
    with pytest.raises(RuntimeError, match="hazard.materialized"):
        app.publish_hazard_materialized(
            active_hazard={
                "hazard_id": "sigmet-aaa",
                "source_version": "v1",
                "materialization_id": "m1",
                "status": "ACTIVE",
                "correlation_id": "poll-1:0",
                "schema_version": "wilvor.active_hazards.v4.0",
                "materialized_at_utc": "2026-07-18T12:30:00+00:00",
                "geometry_hash": "copied-hash-v1",
                "geometry_type": "POLYGON",
                "geometry_point_count": 5,
                "hazard_type": "TURBULENCE",
                "valid_from_utc": "2026-07-18T12:00:00+00:00",
                "valid_to_utc": "2026-07-18T18:00:00+00:00",
            },
            dependent_counts={
                "hazard_coordinates_written": 1,
                "hazard_cells_written": 1,
                "impact_cells_written": 1,
            },
        )
    assert "HistoricalSourcePutFailure" in emitted


def _install_gap_writer_capture(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    import sys
    import types

    captured: list[Any] = []
    fake = types.ModuleType("gap_writer")

    def write_unbound_incident_fail_open(incident, **kwargs):
        captured.append(incident)
        return (
            "metadata/incidents/year=2026/month=07/day=18/"
            f"{incident.dedup_id}.json"
        )

    fake.write_unbound_incident_fail_open = write_unbound_incident_fail_open
    monkeypatch.setitem(sys.modules, "gap_writer", fake)
    return captured


def test_domain1_put_events_failure_stages_unbound_incident(
    monkeypatch: pytest.MonkeyPatch,
):
    from tests.unit.encounter import test_encounter_processor as encounter_tests

    app = encounter_tests.app
    captured = _install_gap_writer_capture(monkeypatch)
    monkeypatch.setattr(app.cloudwatch, "put_metric_data", lambda **kwargs: None)
    monkeypatch.setattr(
        app.eventbridge,
        "put_events",
        lambda **kwargs: {"FailedEntryCount": 1, "Entries": []},
    )
    item = {
        "encounter_id": "proj-1#hazard-1#v1",
        "aircraft_id": "abc123",
        "projection_id": "proj-1",
        "hazard_id": "hazard-1",
    }
    with pytest.raises(RuntimeError, match="Failed to publish"):
        app.publish_encounter_event(item=item, detail_type="encounter.updated")
    assert captured
    payload = captured[0].to_dict()
    assert payload["record_type"] == "UNBOUND_COLLECTION_INCIDENT"
    assert payload["staging_state"] == "STAGING"
    assert "collection_epoch_id" not in payload
    assert payload["gap_domain"] == "DOMAIN_1"
    assert payload["reason"] == "PRODUCER_PUT_EVENTS_FAILURE"
    assert payload["affected_datasets"] == ["encounter"]
    assert payload["identity"] == "proj-1#hazard-1#v1"


def test_geometry_put_failure_and_oversized_stage_unbound_incidents(
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
    app = load_repo_module(
        "historical_facts_sigmet_staging_app",
        "functions/weather/sigmet/processor/app.py",
    )
    captured = _install_gap_writer_capture(monkeypatch)
    monkeypatch.setattr(app, "emit_metric", lambda **kwargs: None)
    monkeypatch.setattr(app, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        app,
        "HISTORICAL_GEOMETRY_FIREHOSE_STREAM_NAME",
        "wilvor-dev-historical-geometry",
    )
    points = [
        {
            "geometry_type": "POLYGON",
            "polygon_index": 0,
            "ring_index": 0,
            "sequence_number": index,
            "longitude": lon,
            "latitude": lat,
        }
        for index, (lon, lat) in enumerate(
            [
                (-75.0, 40.0),
                (-74.0, 40.0),
                (-74.0, 41.0),
                (-75.0, 41.0),
                (-75.0, 40.0),
            ]
        )
    ]
    hazard = {
        "hazard_id": "hazard-1",
        "source_version": "v1",
        "hazard_version_key": "hazard-1#v1",
        "materialization_id": "mat-1",
        "geometry_hash": "copied-hash-v1",
        "geometry_type": "POLYGON",
        "geometry_point_count": 5,
        "materialized_at_utc": "2026-07-18T12:30:00+00:00",
        "schema_version": "wilvor.active_hazards.v4.0",
        "correlation_id": "poll-1:0",
        "materialization_status": "READY",
    }

    def explode(**kwargs):
        raise RuntimeError("firehose unavailable")

    monkeypatch.setattr(app.firehose_client, "put_record", explode)
    app.publish_historical_geometry_fact(
        active_hazard=hazard,
        geometry_points=points,
    )
    monkeypatch.setattr(app, "FIREHOSE_PUT_RECORD_MAX_BYTES", 32)
    monkeypatch.setattr(
        app.firehose_client,
        "put_record",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not put")),
    )
    app.publish_historical_geometry_fact(
        active_hazard=hazard,
        geometry_points=points,
    )
    assert len(captured) == 2
    reasons = {item.reason for item in captured}
    assert reasons == {"GEOMETRY_PUT_FAILURE", "GEOMETRY_OVERSIZED"}
    for incident in captured:
        payload = incident.to_dict()
        assert payload["record_type"] == "UNBOUND_COLLECTION_INCIDENT"
        assert payload["staging_state"] == "STAGING"
        assert "collection_epoch_id" not in payload
        assert payload["affected_datasets"] == ["hazard_geometry"]
        assert payload["gap_domain"] == "GEOMETRY"


def test_unbound_incident_writer_uses_incidents_prefix(
    load_repo_module: Callable[[str, str], ModuleType],
    monkeypatch: pytest.MonkeyPatch,
):
    writer = load_repo_module(
        "historical_facts_gap_writer",
        "functions/historical_facts/runtime/gap_writer.py",
    )
    from wilvor_historical.coverage_contracts import (
        CONTROL_SCHEMA_VERSION,
        STAGING_STATE,
        ControlRecordType,
        GapDomain,
        UncertaintyClass,
        UnboundCollectionIncident,
    )

    incident = UnboundCollectionIncident(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.UNBOUND_COLLECTION_INCIDENT,
        staging_state=STAGING_STATE,
        affected_datasets=("encounter",),
        gap_domain=GapDomain.DOMAIN_1,
        reason="PRODUCER_PUT_EVENTS_FAILURE",
        uncertainty_class=UncertaintyClass.KNOWN_MISSING,
        detected_at_utc="2026-07-18T12:00:00Z",
        interval_start_utc="2026-07-18T12:00:00Z",
        interval_end_utc="2026-07-18T12:00:01Z",
        created_at_utc="2026-07-18T12:00:00Z",
        dedup_id="domain1|encounter|enc-1|2026-07-18T12:00:00Z",
    )
    key = writer.incident_object_key(incident)
    assert key.startswith("metadata/incidents/")
    assert "unbound" not in key
    puts: list[dict[str, Any]] = []

    class FakeS3:
        def put_object(self, **kwargs):
            puts.append(kwargs)
            return {}

    monkeypatch.setenv("HISTORICAL_FACTS_BUCKET_NAME", "test-historical")
    written = writer.write_unbound_incident_fail_open(
        incident, s3_client=FakeS3()
    )
    assert written == key
    assert puts[0]["IfNoneMatch"] == "*"
    body = json.loads(puts[0]["Body"].decode("utf-8"))
    assert body["record_type"] == "UNBOUND_COLLECTION_INCIDENT"
    assert "collection_epoch_id" not in body
