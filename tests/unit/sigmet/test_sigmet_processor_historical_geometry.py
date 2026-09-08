"""Fail-open historical geometry Firehose transport from the enabled SIGMET processor."""

from __future__ import annotations

import json

import pytest

from wilvor_historical.contracts import Dataset, FactKind
from wilvor_historical.from_events import GEOMETRY_IN_MEMORY_DETAIL_TYPE


def _polygon_points():
    return [
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


def _ready_hazard(**overrides):
    item = {
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
    item.update(overrides)
    return item


def _capture_metrics(sigmet_processor, monkeypatch):
    captured = []

    def fake_emit_metric(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(sigmet_processor, "emit_metric", fake_emit_metric)
    return captured


def test_unset_stream_skips_historical_put(sigmet_processor, monkeypatch):
    puts = []
    monkeypatch.setattr(
        sigmet_processor,
        "HISTORICAL_GEOMETRY_FIREHOSE_STREAM_NAME",
        "",
    )
    monkeypatch.setattr(
        sigmet_processor.firehose_client,
        "put_record",
        lambda **kwargs: puts.append(kwargs),
    )
    sigmet_processor.publish_historical_geometry_fact(
        active_hazard=_ready_hazard(),
        geometry_points=_polygon_points(),
    )
    assert puts == []


def test_ready_geometry_put_is_canonical_fact(sigmet_processor, monkeypatch):
    puts = []
    metrics = _capture_metrics(sigmet_processor, monkeypatch)
    monkeypatch.setattr(
        sigmet_processor,
        "HISTORICAL_GEOMETRY_FIREHOSE_STREAM_NAME",
        "wilvor-dev-historical-geometry",
    )
    monkeypatch.setattr(
        sigmet_processor.firehose_client,
        "put_record",
        lambda **kwargs: puts.append(kwargs) or {"RecordId": "rec-1"},
    )

    sigmet_processor.publish_historical_geometry_fact(
        active_hazard=_ready_hazard(),
        geometry_points=_polygon_points(),
    )

    assert len(puts) == 1
    assert puts[0]["DeliveryStreamName"] == "wilvor-dev-historical-geometry"
    payload = json.loads(puts[0]["Record"]["Data"].decode("utf-8"))
    assert payload["dataset"] == Dataset.HAZARD_GEOMETRY.value
    assert payload["fact_kind"] == FactKind.HAZARD_GEOMETRY.value
    assert payload["hazard_version_key"] == "hazard-1#v1"
    assert payload["source_version"] == "v1"
    assert payload["geometry_hash"] == "copied-hash-v1"
    assert payload["geometry"]["type"] == "Polygon"
    assert payload["geometry"]["coordinates"][0][0] == [-75.0, 40.0]
    assert payload["geometry"]["coordinates"][0][-1] == [-75.0, 40.0]
    assert payload["producer_detail_type"] == GEOMETRY_IN_MEMORY_DETAIL_TYPE
    assert "DURABLY_PERSISTED" not in json.dumps(metrics)
    assert metrics[-1]["metrics"]["HistoricalGeometryPutSuccess"] == 1
    assert metrics[-1]["properties"]["acceptance"] == (
        "ACCEPTED_FOR_BOUNDED_DELIVERY_RETRY"
    )


def test_polygon_hole_and_multipolygon_are_preserved(sigmet_processor, monkeypatch):
    puts = []
    monkeypatch.setattr(
        sigmet_processor,
        "HISTORICAL_GEOMETRY_FIREHOSE_STREAM_NAME",
        "wilvor-dev-historical-geometry",
    )
    monkeypatch.setattr(
        sigmet_processor.firehose_client,
        "put_record",
        lambda **kwargs: puts.append(kwargs) or {"RecordId": "rec-1"},
    )
    hole_geometry = {
        "type": "Polygon",
        "coordinates": [
            [
                [-75.0, 40.0],
                [-74.0, 40.0],
                [-74.0, 41.0],
                [-75.0, 41.0],
                [-75.0, 40.0],
            ],
            [
                [-74.8, 40.2],
                [-74.7, 40.2],
                [-74.7, 40.3],
                [-74.8, 40.3],
                [-74.8, 40.2],
            ],
        ],
    }
    points = sigmet_processor.flatten_geometry_points(hole_geometry)
    sigmet_processor.publish_historical_geometry_fact(
        active_hazard=_ready_hazard(geometry_point_count=len(points)),
        geometry_points=points,
    )
    hole_payload = json.loads(puts[0]["Record"]["Data"].decode("utf-8"))
    assert hole_payload["geometry"]["type"] == "Polygon"
    assert len(hole_payload["geometry"]["coordinates"]) == 2
    assert hole_payload["geometry"]["coordinates"][1][0] == [-74.8, 40.2]

    multi = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[-75.0, 40.0], [-74.0, 40.0], [-74.0, 41.0], [-75.0, 40.0]]],
            [[[-80.0, 35.0], [-79.0, 35.0], [-79.0, 36.0], [-80.0, 35.0]]],
        ],
    }
    multi_points = sigmet_processor.flatten_geometry_points(multi)
    sigmet_processor.publish_historical_geometry_fact(
        active_hazard=_ready_hazard(
            geometry_type="MULTIPOLYGON",
            geometry_point_count=len(multi_points),
        ),
        geometry_points=multi_points,
    )
    multi_payload = json.loads(puts[1]["Record"]["Data"].decode("utf-8"))
    assert multi_payload["geometry"]["type"] == "MultiPolygon"
    assert len(multi_payload["geometry"]["coordinates"]) == 2


def test_firehose_error_is_fail_open(sigmet_processor, monkeypatch):
    metrics = _capture_metrics(sigmet_processor, monkeypatch)
    monkeypatch.setattr(
        sigmet_processor,
        "HISTORICAL_GEOMETRY_FIREHOSE_STREAM_NAME",
        "wilvor-dev-historical-geometry",
    )

    def explode(**kwargs):
        raise RuntimeError("firehose unavailable")

    monkeypatch.setattr(
        sigmet_processor.firehose_client,
        "put_record",
        explode,
    )
    sigmet_processor.publish_historical_geometry_fact(
        active_hazard=_ready_hazard(),
        geometry_points=_polygon_points(),
    )
    assert metrics[-1]["metrics"]["HistoricalGeometryPutFailure"] == 1
    assert metrics[-1]["properties"]["hazard_version_key"] == "hazard-1#v1"


def test_oversized_geometry_skips_put_without_truncation(
    sigmet_processor,
    monkeypatch,
):
    puts = []
    metrics = _capture_metrics(sigmet_processor, monkeypatch)
    logs = []
    monkeypatch.setattr(
        sigmet_processor,
        "HISTORICAL_GEOMETRY_FIREHOSE_STREAM_NAME",
        "wilvor-dev-historical-geometry",
    )
    monkeypatch.setattr(sigmet_processor, "FIREHOSE_PUT_RECORD_MAX_BYTES", 32)
    monkeypatch.setattr(
        sigmet_processor.firehose_client,
        "put_record",
        lambda **kwargs: puts.append(kwargs),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "log_event",
        lambda message, **kwargs: logs.append((message, kwargs)),
    )

    sigmet_processor.publish_historical_geometry_fact(
        active_hazard=_ready_hazard(),
        geometry_points=_polygon_points(),
    )

    assert puts == []
    assert metrics[-1]["metrics"]["HistoricalGeometryOversized"] == 1
    assert metrics[-1]["properties"]["hazard_version_key"] == "hazard-1#v1"
    assert "serialized_bytes" in metrics[-1]["properties"]
    assert logs[0][1]["hazard_version_key"] == "hazard-1#v1"
    assert logs[0][1]["serialized_bytes"] > 32


def _stub_ready_path(sigmet_processor, monkeypatch, *, item, points):
    published = []

    class FakeActiveTable:
        def put_item(self, **kwargs):
            return None

    monkeypatch.setattr(
        sigmet_processor,
        "active_hazards_table",
        FakeActiveTable(),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "flatten_geometry_points",
        lambda geometry: list(points),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "geometry_to_h3_cells",
        lambda geometry, resolution: ["cell-a"],
    )
    monkeypatch.setattr(
        sigmet_processor,
        "expand_impact_cells",
        lambda cells, distance: {"cell-a": 0},
    )
    monkeypatch.setattr(
        sigmet_processor,
        "build_active_hazard_item",
        lambda *args, **kwargs: dict(item),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "get_existing_hazard",
        lambda hazard_id: None,
    )
    monkeypatch.setattr(
        sigmet_processor,
        "materialize_dependent_rows",
        lambda **kwargs: {
            "hazard_coordinates_written": item["geometry_point_count"],
            "hazard_cells_written": item["hazard_cell_count"],
            "impact_cells_written": item["impact_cell_count"],
        },
    )
    monkeypatch.setattr(
        sigmet_processor,
        "mark_hazard_ready",
        lambda active_hazard: {
            **active_hazard,
            "materialization_status": "READY",
            "materialized_at_utc": "2026-07-18T12:30:00+00:00",
        },
    )
    monkeypatch.setattr(
        sigmet_processor,
        "publish_hazard_coordinates_materialized",
        lambda **kwargs: published.append("coordinate-event") or 1,
    )
    monkeypatch.setattr(
        sigmet_processor,
        "publish_hazard_materialized",
        lambda **kwargs: published.append("hazard-event") or 1,
    )
    return published


def test_ready_path_puts_once_and_keeps_operational_events(
    sigmet_processor,
    sigmet_raw_event,
    monkeypatch,
):
    puts = []
    metrics = _capture_metrics(sigmet_processor, monkeypatch)
    item = {
        "hazard_id": "hazard-1",
        "source_version": "v1",
        "hazard_version_key": "hazard-1#v1",
        "materialization_status": "BUILDING",
        "materialization_id": "mat-1",
        "geometry_hash": "copied-hash-v1",
        "geometry_type": "POLYGON",
        "geometry_point_count": 5,
        "hazard_cell_count": 1,
        "impact_cell_count": 1,
        "schema_version": "wilvor.active_hazards.v4.0",
    }
    published = _stub_ready_path(
        sigmet_processor,
        monkeypatch,
        item=item,
        points=_polygon_points(),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "HISTORICAL_GEOMETRY_FIREHOSE_STREAM_NAME",
        "wilvor-dev-historical-geometry",
    )
    monkeypatch.setattr(
        sigmet_processor.firehose_client,
        "put_record",
        lambda **kwargs: puts.append(kwargs) or {"RecordId": "rec-1"},
    )

    result = sigmet_processor.process_decoded_record(sigmet_raw_event)

    assert len(puts) == 1
    assert published == ["coordinate-event", "hazard-event"]
    assert result["eventbridge_events_published"] == 2
    assert result["active_hazards_written"] == 1
    assert metrics[-1]["metrics"]["HistoricalGeometryPutSuccess"] == 1


def test_firehose_failure_does_not_block_operational_publish(
    sigmet_processor,
    sigmet_raw_event,
    monkeypatch,
):
    metrics = _capture_metrics(sigmet_processor, monkeypatch)
    item = {
        "hazard_id": "hazard-1",
        "source_version": "v1",
        "hazard_version_key": "hazard-1#v1",
        "materialization_status": "BUILDING",
        "materialization_id": "mat-1",
        "geometry_hash": "copied-hash-v1",
        "geometry_type": "POLYGON",
        "geometry_point_count": 5,
        "hazard_cell_count": 1,
        "impact_cell_count": 1,
        "schema_version": "wilvor.active_hazards.v4.0",
    }
    published = _stub_ready_path(
        sigmet_processor,
        monkeypatch,
        item=item,
        points=_polygon_points(),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "HISTORICAL_GEOMETRY_FIREHOSE_STREAM_NAME",
        "wilvor-dev-historical-geometry",
    )
    monkeypatch.setattr(
        sigmet_processor.firehose_client,
        "put_record",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("throttled")),
    )

    result = sigmet_processor.process_decoded_record(sigmet_raw_event)

    assert published == ["coordinate-event", "hazard-event"]
    assert result["eventbridge_events_published"] == 2
    assert metrics[-1]["metrics"]["HistoricalGeometryPutFailure"] == 1


def test_unchanged_path_does_not_publish_historical_geometry(
    sigmet_processor,
    sigmet_raw_event,
    monkeypatch,
):
    historical_calls = []
    monkeypatch.setattr(
        sigmet_processor,
        "HISTORICAL_GEOMETRY_FIREHOSE_STREAM_NAME",
        "wilvor-dev-historical-geometry",
    )
    monkeypatch.setattr(
        sigmet_processor,
        "publish_historical_geometry_fact",
        lambda **kwargs: historical_calls.append(kwargs),
    )
    existing = {
        "hazard_id": "hazard-1",
        "source_version": "v1",
        "source_event_time_utc": "2026-07-18T12:00:00+00:00",
        "materialization_status": "READY",
    }
    monkeypatch.setattr(
        sigmet_processor,
        "get_existing_hazard",
        lambda hazard_id: dict(existing),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "build_active_hazard_item",
        lambda *args, **kwargs: dict(existing),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "flatten_geometry_points",
        lambda geometry: _polygon_points(),
    )
    monkeypatch.setattr(
        sigmet_processor,
        "geometry_to_h3_cells",
        lambda geometry, resolution: ["cell-a"],
    )
    monkeypatch.setattr(
        sigmet_processor,
        "expand_impact_cells",
        lambda cells, distance: {"cell-a": 0},
    )
    monkeypatch.setattr(
        sigmet_processor,
        "materialize_dependent_rows",
        lambda **kwargs: pytest.fail("unchanged must not rematerialize"),
    )

    result = sigmet_processor.process_decoded_record(sigmet_raw_event)

    assert historical_calls == []
    assert result["unchanged_records"] == 1
    assert result["eventbridge_events_published"] == 0


def test_dynamodb_item_shape_is_unchanged_by_historical_put(
    sigmet_processor,
    monkeypatch,
):
    item = _ready_hazard()
    original = dict(item)
    monkeypatch.setattr(
        sigmet_processor,
        "HISTORICAL_GEOMETRY_FIREHOSE_STREAM_NAME",
        "wilvor-dev-historical-geometry",
    )
    monkeypatch.setattr(
        sigmet_processor.firehose_client,
        "put_record",
        lambda **kwargs: {"RecordId": "rec-1"},
    )
    sigmet_processor.publish_historical_geometry_fact(
        active_hazard=item,
        geometry_points=_polygon_points(),
    )
    assert item == original
    assert "fact_schema_version" not in item
