from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from faa_parser import parse_faa_directory
from fakes import FakeTable
from persistence import (
    apply_runway_snapshot,
    plan_airport_snapshot,
)


def test_snapshot_plan_protects_rejected_runway(
    fixtures_dir: Path,
) -> None:
    result = parse_faa_directory(
        fixtures_dir,
        supported_airport_ids={
            "KSFO",
            "KOAK",
        },
    )

    incoming_runway = next(
        runway
        for runway in result.runways
        if runway.record_id
        == "RUNWAY#01L-19R"
    )

    existing = [
        {
            "airport_id": "KSFO",
            "record_id": "RUNWAY#01L-19R",
            "source_record_hash": (
                incoming_runway.source_record_hash
            ),
        },
        {
            "airport_id": "KSFO",
            "record_id": "RUNWAY#10-28",
            "source_record_hash": "old",
        },
    ]

    plan = plan_airport_snapshot(
        existing_items=existing,
        incoming_runways=[incoming_runway],
        protected_record_ids={
            "RUNWAY#10-28"
        },
    )

    assert len(plan.unchanged_runways) == 1
    assert plan.deleted_record_ids == []


def test_apply_snapshot_writes_runways_and_meta(
    fixtures_dir: Path,
) -> None:
    result = parse_faa_directory(
        fixtures_dir,
        supported_airport_ids={
            "KSFO",
            "KOAK",
        },
    )

    table = FakeTable()

    stats = apply_runway_snapshot(
        table=table,
        parse_result=result,
        supported_airport_ids={
            "KSFO",
            "KOAK",
        },
        source_cycle="2026-07-09",
        source_zip_hash="zip-hash",
        raw_s3_uri="s3://archive/raw.zip",
        ingested_at_utc=(
            "2026-07-20T00:00:00+00:00"
        ),
        load_id="load-1",
    )

    assert stats.airports_loaded == 2
    assert stats.runways_new == 3

    assert table.items[
        (
            "KSFO",
            "META",
        )
    ]["runway_count"] == 2

    runway = table.items[
        (
            "KSFO",
            "RUNWAY#01L-19R",
        )
    ]

    assert isinstance(
        runway["end_1"]["latitude"],
        Decimal,
    )