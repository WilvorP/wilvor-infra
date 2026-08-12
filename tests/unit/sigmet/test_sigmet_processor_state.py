from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest


def test_build_active_hazard_item(
    sigmet_processor,
    sigmet_raw_event,
    sigmet_feature,
    fixed_sigmet_time,
    monkeypatch,
):
    monkeypatch.setattr(
        sigmet_processor,
        "now_utc",
        lambda: fixed_sigmet_time,
    )
    monkeypatch.setattr(
        sigmet_processor,
        "now_utc_iso",
        lambda: fixed_sigmet_time.isoformat(),
    )

    geometry_points = (
        sigmet_processor.flatten_geometry_points(
            sigmet_feature["geometry"]
        )
    )

    item = sigmet_processor.build_active_hazard_item(
        sigmet_raw_event,
        sigmet_feature,
        geometry_points,
        hazard_cell_count=2,
        impact_cell_count=8,
    )

    assert item["hazard_id"].startswith("sigmet-")
    assert item["source_product_id"] == "KZNY|SIGMET|A|12"
    assert len(item["source_version"]) == 32

    assert item["source_icao_id"] == "KZNY"
    assert item["series_id"] == "12"
    assert item["alpha_char"] == "A"

    assert item["created_at_utc"] == (
        "2026-07-18T12:00:00+00:00"
    )
    assert item["valid_from_utc"] == (
        "2026-07-18T12:00:00+00:00"
    )
    assert item["valid_to_utc"] == (
        "2026-07-18T18:00:00+00:00"
    )

    assert item["product_type"] == "SIGMET"
    assert item["hazard_type"] == "TURBULENCE"
    assert item["severity"] == "SEV"

    assert item["altitude_bands"] == [
        {
            "source_band_index": 1,
            "lower_altitude_ft": Decimal("180"),
            "upper_altitude_ft": Decimal("400"),
        }
    ]

    assert (
        item["minimum_lower_altitude_ft"]
        == Decimal("180")
    )
    assert (
        item["maximum_upper_altitude_ft"]
        == Decimal("400")
    )

    assert (
        item["movement_direction_deg"]
        == Decimal("90")
    )
    assert (
        item["movement_speed_kt"]
        == Decimal("20")
    )

    assert item["geometry_type"] == "POLYGON"
    assert item["geometry_point_count"] == 5
    assert item["hazard_cell_count"] == 2
    assert item["impact_cell_count"] == 8

    assert len(item["geometry_hash"]) == 64

    assert (
        item["materialization_status"]
        == "BUILDING"
    )
    assert item["materialization_id"].startswith(
        "hazard-materialization-"
    )

    assert item["status"] == "ACTIVE"

    assert item["source_system"] == (
        "NOAA_AVIATIONWEATHER_SIGMET"
    )

    assert item["source_event_time_utc"] == (
        "2026-07-18T12:00:00+00:00"
    )

    assert item["received_at_utc"] == (
        "2026-07-18T12:01:00+00:00"
    )

    assert item["processed_at_utc"] == (
        fixed_sigmet_time.isoformat()
    )

    assert item["correlation_id"] == (
        "poll-sigmet-001:0"
    )

    assert item["raw_s3_uri"].startswith(
        "s3://test-sigmet-archive/"
    )

    assert item["schema_version"] == (
        "wilvor.active_hazards.v4.0"
    )

    expected_ttl = int(
        datetime(
            2026,
            7,
            19,
            0,
            0,
            tzinfo=timezone.utc,
        ).timestamp()
    )

    assert item["expires_at_epoch"] == expected_ttl

    assert "materialized_at_utc" not in item

    # Unbounded/exact geometry does not belong
    # in the ActiveHazards parent.
    assert "geometry_json" not in item
    assert "h3_cells" not in item
    assert "h3_cell_count" not in item


def test_build_active_hazard_item_marks_expired(
    sigmet_processor,
    sigmet_raw_event,
    sigmet_feature,
    monkeypatch,
):
    monkeypatch.setattr(
        sigmet_processor,
        "now_utc",
        lambda: datetime(
            2026,
            7,
            19,
            0,
            0,
            tzinfo=timezone.utc,
        ),
    )

    geometry_points = (
        sigmet_processor.flatten_geometry_points(
            sigmet_feature["geometry"]
        )
    )

    item = sigmet_processor.build_active_hazard_item(
        sigmet_raw_event,
        sigmet_feature,
        geometry_points,
        hazard_cell_count=1,
        impact_cell_count=1,
    )

    assert item["status"] == "EXPIRED"


def test_build_active_hazard_item_marks_cancelled(
    sigmet_processor,
    sigmet_raw_event,
    sigmet_feature,
    fixed_sigmet_time,
    monkeypatch,
):
    monkeypatch.setattr(
        sigmet_processor,
        "now_utc",
        lambda: fixed_sigmet_time,
    )

    feature = {
        **sigmet_feature,
        "properties": {
            **sigmet_feature["properties"],
            "status": "CNL",
        },
    }

    raw_event = {
        **sigmet_raw_event,
        "feature": feature,
    }

    geometry_points = (
        sigmet_processor.flatten_geometry_points(
            feature["geometry"]
        )
    )

    item = sigmet_processor.build_active_hazard_item(
        raw_event,
        feature,
        geometry_points,
        hazard_cell_count=1,
        impact_cell_count=1,
    )

    assert item["status"] == "CANCELLED"
    assert (
        item["amendment_type"]
        == "CANCELLATION"
    )

    expected_ttl = int(
        (
            fixed_sigmet_time
            + timedelta(hours=6)
        ).timestamp()
    )

    assert item["expires_at_epoch"] == expected_ttl


@pytest.mark.parametrize(
    (
        "existing",
        "incoming",
        "expected",
    ),
    [
        (
            None,
            {
                "source_version": "v1",
                "source_event_time_utc": (
                    "2026-07-18T12:00:00+00:00"
                ),
            },
            ("NEW", True),
        ),
        (
            {
                "source_version": "v1",
                "source_event_time_utc": (
                    "2026-07-18T12:00:00+00:00"
                ),
            },
            {
                "source_version": "v1",
                "source_event_time_utc": (
                    "2026-07-18T12:00:00+00:00"
                ),
            },
            ("UNCHANGED", False),
        ),
        (
            {
                "source_version": "v0",
                "source_event_time_utc": (
                    "2026-07-18T12:00:00+00:00"
                ),
            },
            {
                "source_version": "v1",
                "source_event_time_utc": (
                    "2026-07-18T13:00:00+00:00"
                ),
            },
            ("UPDATED", True),
        ),
        (
            {
                "source_version": "v2",
                "source_event_time_utc": (
                    "2026-07-18T13:00:00+00:00"
                ),
            },
            {
                "source_version": "v1",
                "source_event_time_utc": (
                    "2026-07-18T12:00:00+00:00"
                ),
            },
            ("STALE", False),
        ),
    ],
)
def test_determine_change_type(
    sigmet_processor,
    existing,
    incoming,
    expected,
):
    assert (
        sigmet_processor.determine_change_type(
            existing,
            incoming,
        )
        == expected
    )


def test_get_existing_hazard_returns_item_or_none(
    sigmet_processor,
    monkeypatch,
):
    class FakeTable:
        def __init__(self, response):
            self.response = response
            self.keys = []

        def get_item(self, **kwargs):
            self.keys.append(kwargs["Key"])
            return self.response

    present = FakeTable(
        {
            "Item": {
                "hazard_id": "hazard-1",
            }
        }
    )

    monkeypatch.setattr(
        sigmet_processor,
        "active_hazards_table",
        present,
    )

    assert (
        sigmet_processor.get_existing_hazard(
            "hazard-1"
        )
        == {
            "hazard_id": "hazard-1",
        }
    )

    assert present.keys == [
        {
            "hazard_id": "hazard-1",
        }
    ]

    missing = FakeTable({})

    monkeypatch.setattr(
        sigmet_processor,
        "active_hazards_table",
        missing,
    )

    assert (
        sigmet_processor.get_existing_hazard(
            "hazard-2"
        )
        is None
    )


def _active_hazard_for_children():
    return {
        "hazard_id": "hazard-1",
        "source_version": "v1",
        "hazard_type": "TURBULENCE",
        "severity": "SEV",
        "valid_from_utc": (
            "2026-07-18T12:00:00+00:00"
        ),
        "valid_to_utc": (
            "2026-07-18T18:00:00+00:00"
        ),
        "materialization_id": (
            "hazard-materialization-123"
        ),
        "geometry_hash": "geometry-hash",
        "correlation_id": "correlation-1",
        "expires_at_epoch": 12345,
    }


def test_build_coordinate_items(
    sigmet_processor,
):
    active_hazard = (
        _active_hazard_for_children()
    )

    geometry_points = [
        {
            "geometry_type": "POLYGON",
            "polygon_index": 0,
            "ring_index": 0,
            "sequence_number": 0,
            "latitude": 40.0,
            "longitude": -75.0,
        },
        {
            "geometry_type": "POLYGON",
            "polygon_index": 0,
            "ring_index": 0,
            "sequence_number": 1,
            "latitude": 41.0,
            "longitude": -74.0,
        },
    ]

    items = (
        sigmet_processor.build_coordinate_items(
            active_hazard,
            geometry_points,
            "2026-07-18T12:30:00+00:00",
        )
    )

    assert len(items) == 2

    first = items[0]

    assert first["hazard_id"] == "hazard-1"
    assert (
        first["hazard_version_key"]
        == "hazard-1#v1"
    )
    assert (
        first["coordinate_key"]
        == "P#0000#R#0000#S#000000"
    )
    assert first["schema_version"] == (
        "wilvor.hazard_coordinates.v4.0"
    )

    assert first["source_version"] == "v1"
    assert (
        first["materialization_id"]
        == "hazard-materialization-123"
    )
    assert first["latitude"] == Decimal("40.0")
    assert first["longitude"] == Decimal("-75.0")


def test_build_hazard_cell_items(
    sigmet_processor,
):
    active_hazard = (
        _active_hazard_for_children()
    )

    items = (
        sigmet_processor.build_hazard_cell_items(
            active_hazard,
            ["cell-b", "cell-a", "cell-a"],
            "2026-07-18T12:30:00+00:00",
        )
    )

    assert [
        item["h3_cell"]
        for item in items
    ] == [
        "cell-a",
        "cell-b",
    ]

    assert all(
        item["hazard_version_key"]
        == "hazard-1#v1"
        for item in items
    )

    assert all(
        item["materialization_id"]
        == "hazard-materialization-123"
        for item in items
    )


def test_build_impact_cell_items(
    sigmet_processor,
):
    active_hazard = (
        _active_hazard_for_children()
    )

    items = (
        sigmet_processor.build_impact_cell_items(
            active_hazard,
            {
                "cell-a": 0,
                "cell-b": 1,
            },
            "2026-07-18T12:30:00+00:00",
        )
    )

    assert len(items) == 2

    assert items[0]["impact_cell"] == "cell-a"
    assert (
        items[0]["minimum_grid_distance"]
        == 0
    )

    assert (
        items[1]["minimum_grid_distance"]
        == 1
    )

    assert all(
        item["hazard_version_key"]
        == "hazard-1#v1"
        for item in items
    )


def test_batch_put_items(
    sigmet_processor,
):
    written = []

    class FakeBatch:
        def __enter__(self):
            return self

        def __exit__(
            self,
            exc_type,
            exc,
            traceback,
        ):
            return False

        def put_item(self, **kwargs):
            written.append(
                kwargs["Item"]
            )

    class FakeTable:
        def batch_writer(self, **kwargs):
            assert (
                kwargs["overwrite_by_pkeys"]
                == ["pk", "sk"]
            )
            return FakeBatch()

    count = sigmet_processor.batch_put_items(
        FakeTable(),
        overwrite_by_pkeys=[
            "pk",
            "sk",
        ],
        items=[
            {
                "pk": "a",
                "sk": "1",
            },
            {
                "pk": "b",
                "sk": "2",
            },
        ],
    )

    assert count == 2
    assert len(written) == 2


def test_materialize_dependent_rows(
    sigmet_processor,
    monkeypatch,
):
    active_hazard = (
        _active_hazard_for_children()
    )

    geometry_points = [
        {
            "geometry_type": "POLYGON",
            "polygon_index": 0,
            "ring_index": 0,
            "sequence_number": 0,
            "latitude": 40.0,
            "longitude": -75.0,
        }
    ]

    calls = []

    monkeypatch.setattr(
        sigmet_processor,
        "batch_put_items",
        lambda table, **kwargs: (
            calls.append(
                (
                    table,
                    kwargs[
                        "overwrite_by_pkeys"
                    ],
                    kwargs["items"],
                )
            )
            or len(kwargs["items"])
        ),
    )

    result = (
        sigmet_processor.materialize_dependent_rows(
            active_hazard=active_hazard,
            geometry_points=geometry_points,
            h3_cells=[
                "cell-a",
                "cell-b",
            ],
            impact_cells={
                "cell-a": 0,
                "cell-b": 1,
                "cell-c": 2,
            },
        )
    )

    assert result == {
        "hazard_coordinates_written": 1,
        "hazard_cells_written": 2,
        "impact_cells_written": 3,
    }

    assert calls[0][1] == [
        "hazard_version_key",
        "coordinate_key",
    ]

    assert calls[1][1] == [
        "h3_cell",
        "hazard_version_key",
    ]

    assert calls[2][1] == [
        "impact_cell",
        "hazard_version_key",
    ]


def test_process_decoded_record_new_hazard(
    sigmet_processor,
    sigmet_raw_event,
    monkeypatch,
):
    order = []
    put_items = []

    item = {
        "hazard_id": "hazard-1",
        "source_version": "v1",
        "source_event_time_utc": (
            "2026-07-18T12:00:00+00:00"
        ),
        "materialization_status": "BUILDING",
    }

    class FakeActiveTable:
        def put_item(self, **kwargs):
            order.append("parent")
            put_items.append(
                kwargs["Item"]
            )

    monkeypatch.setattr(
        sigmet_processor,
        "active_hazards_table",
        FakeActiveTable(),
    )

    monkeypatch.setattr(
        sigmet_processor,
        "flatten_geometry_points",
        lambda geometry: [
            {
                "geometry_type": "POLYGON",
                "polygon_index": 0,
                "ring_index": 0,
                "sequence_number": 0,
                "latitude": 40.0,
                "longitude": -75.0,
            }
        ],
    )

    monkeypatch.setattr(
        sigmet_processor,
        "geometry_to_h3_cells",
        lambda geometry, resolution: [
            "cell-a",
            "cell-b",
        ],
    )

    monkeypatch.setattr(
        sigmet_processor,
        "expand_impact_cells",
        lambda cells, distance: {
            "cell-a": 0,
            "cell-b": 0,
            "cell-c": 1,
        },
    )

    def fake_build_active_hazard_item(
        raw_event,
        feature,
        geometry_points,
        hazard_cell_count,
        impact_cell_count,
    ):
        assert hazard_cell_count == 2
        assert impact_cell_count == 3
        return dict(item)

    monkeypatch.setattr(
        sigmet_processor,
        "build_active_hazard_item",
        fake_build_active_hazard_item,
    )

    monkeypatch.setattr(
        sigmet_processor,
        "get_existing_hazard",
        lambda hazard_id: None,
    )

    def fake_materialize(**kwargs):
        order.append("children")

        return {
            "hazard_coordinates_written": 1,
            "hazard_cells_written": 2,
            "impact_cells_written": 3,
        }

    monkeypatch.setattr(
        sigmet_processor,
        "materialize_dependent_rows",
        fake_materialize,
    )

    result = (
        sigmet_processor.process_decoded_record(
            sigmet_raw_event
        )
    )

    assert result == {
        "active_hazards_written": 1,
        "hazard_coordinates_written": 1,
        "hazard_cells_written": 2,
        "impact_cells_written": 3,
        "eventbridge_events_published": 0,
        "new_records": 1,
        "updated_records": 0,
        "unchanged_records": 0,
        "stale_records": 0,
    }

    assert order == [
        "children",
        "parent",
    ]

    assert (
        put_items[0][
            "materialization_status"
        ]
        == "BUILDING"
    )


def test_process_decoded_record_unchanged_does_not_write(
    sigmet_processor,
    sigmet_raw_event,
    monkeypatch,
):
    item = {
        "hazard_id": "hazard-1",
        "source_version": "v1",
        "source_event_time_utc": (
            "2026-07-18T12:00:00+00:00"
        ),
    }

    monkeypatch.setattr(
        sigmet_processor,
        "flatten_geometry_points",
        lambda geometry: [
            {
                "geometry_type": "POLYGON",
            }
        ],
    )

    monkeypatch.setattr(
        sigmet_processor,
        "geometry_to_h3_cells",
        lambda geometry, resolution: [
            "cell-a"
        ],
    )

    monkeypatch.setattr(
        sigmet_processor,
        "expand_impact_cells",
        lambda cells, distance: {
            "cell-a": 0
        },
    )

    monkeypatch.setattr(
        sigmet_processor,
        "build_active_hazard_item",
        lambda *args, **kwargs: dict(item),
    )

    monkeypatch.setattr(
        sigmet_processor,
        "get_existing_hazard",
        lambda hazard_id: dict(item),
    )

    monkeypatch.setattr(
        sigmet_processor,
        "materialize_dependent_rows",
        lambda **kwargs: pytest.fail(
            "unchanged hazard must not rematerialize"
        ),
    )

    result = (
        sigmet_processor.process_decoded_record(
            sigmet_raw_event
        )
    )

    assert result[
        "active_hazards_written"
    ] == 0

    assert result[
        "hazard_coordinates_written"
    ] == 0

    assert result[
        "hazard_cells_written"
    ] == 0

    assert result[
        "impact_cells_written"
    ] == 0

    assert result[
        "eventbridge_events_published"
    ] == 0

    assert result[
        "unchanged_records"
    ] == 1


def test_process_decoded_record_stale_does_not_write(
    sigmet_processor,
    sigmet_raw_event,
    monkeypatch,
):
    incoming = {
        "hazard_id": "hazard-1",
        "source_version": "old-version",
        "source_event_time_utc": (
            "2026-07-18T12:00:00+00:00"
        ),
    }

    existing = {
        "hazard_id": "hazard-1",
        "source_version": "new-version",
        "source_event_time_utc": (
            "2026-07-18T13:00:00+00:00"
        ),
    }

    monkeypatch.setattr(
        sigmet_processor,
        "flatten_geometry_points",
        lambda geometry: [
            {
                "geometry_type": "POLYGON",
            }
        ],
    )

    monkeypatch.setattr(
        sigmet_processor,
        "geometry_to_h3_cells",
        lambda geometry, resolution: [
            "cell-a"
        ],
    )

    monkeypatch.setattr(
        sigmet_processor,
        "expand_impact_cells",
        lambda cells, distance: {
            "cell-a": 0
        },
    )

    monkeypatch.setattr(
        sigmet_processor,
        "build_active_hazard_item",
        lambda *args, **kwargs: dict(
            incoming
        ),
    )

    monkeypatch.setattr(
        sigmet_processor,
        "get_existing_hazard",
        lambda hazard_id: dict(
            existing
        ),
    )

    monkeypatch.setattr(
        sigmet_processor,
        "materialize_dependent_rows",
        lambda **kwargs: pytest.fail(
            "stale hazard must not rematerialize"
        ),
    )

    result = (
        sigmet_processor.process_decoded_record(
            sigmet_raw_event
        )
    )

    assert result["stale_records"] == 1
    assert result["active_hazards_written"] == 0
