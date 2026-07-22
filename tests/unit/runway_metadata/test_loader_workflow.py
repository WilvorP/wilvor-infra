from __future__ import annotations

import gzip
import json
from pathlib import Path

from app import LoaderConfig, run_loader
from fakes import (
    FakeEventsClient,
    FakeS3Client,
    FakeTable,
)


def build_config(
    tmp_path: Path,
) -> LoaderConfig:
    return LoaderConfig(
        table_name="runway-reference",
        archive_bucket_name="archive-bucket",
        supported_airport_ids={
            "KSFO",
            "KOAK",
        },
        default_source_url=None,
        default_source_cycle=None,
        event_bus_name="default",
        raw_prefix="raw/source=faa-nasr",
        bad_prefix="bad/source=faa-nasr",
        http_timeout_seconds=30,
        work_directory=str(
            tmp_path / "work"
        ),
    )


def test_first_load_writes_all_outputs(
    faa_zip_path: Path,
    tmp_path: Path,
) -> None:
    s3 = FakeS3Client()
    table = FakeTable()
    events = FakeEventsClient()

    response = run_loader(
        event={
            "source_cycle": "2026-07-09",
            "source_zip_path": str(
                faa_zip_path
            ),
            "load_id": "load-1",
        },
        context=None,
        config=build_config(tmp_path),
        s3_client=s3,
        table=table,
        events_client=events,
    )

    assert response["ok"] is True
    assert response["skipped"] is False
    assert (
        response["cycle_decision"]
        == "FIRST_LOAD"
    )
    assert response["runways_loaded"] == 3
    assert response["invalid_record_count"] == 1

    control = table.items[
        (
            "SYSTEM",
            "SOURCE#FAA_NASR",
        )
    ]

    assert control["load_status"] == "SUCCEEDED"

    assert table.items[
        (
            "KSFO",
            "META",
        )
    ]["runway_count"] == 2

    assert len(events.entries) == 1

    bad_keys = [
        key
        for bucket, key in s3.objects
        if (
            bucket == "archive-bucket"
            and key.startswith("bad/")
        )
    ]

    assert len(bad_keys) == 1

    bad_payload = json.loads(
        gzip.decompress(
            s3.objects[
                (
                    "archive-bucket",
                    bad_keys[0],
                )
            ]
        )
    )

    assert bad_payload["record_count"] == 1


def test_duplicate_cycle_is_skipped(
    faa_zip_path: Path,
    tmp_path: Path,
) -> None:
    s3 = FakeS3Client()
    table = FakeTable()
    events = FakeEventsClient()

    config = build_config(tmp_path)

    base_event = {
        "source_cycle": "2026-07-09",
        "source_zip_path": str(
            faa_zip_path
        ),
    }

    first = run_loader(
        event={
            **base_event,
            "load_id": "load-1",
        },
        context=None,
        config=config,
        s3_client=s3,
        table=table,
        events_client=events,
    )

    runway_before = dict(
        table.items[
            (
                "KSFO",
                "RUNWAY#01L-19R",
            )
        ]
    )

    second = run_loader(
        event={
            **base_event,
            "load_id": "load-2",
        },
        context=None,
        config=config,
        s3_client=s3,
        table=table,
        events_client=events,
    )

    assert first["skipped"] is False
    assert second["skipped"] is True

    assert (
        second["cycle_decision"]
        == "DUPLICATE"
    )

    assert table.items[
        (
            "KSFO",
            "RUNWAY#01L-19R",
        )
    ] == runway_before

    # Only the first load publishes an event.
    assert len(events.entries) == 1