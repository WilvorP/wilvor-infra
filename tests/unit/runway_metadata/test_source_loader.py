from __future__ import annotations

from pathlib import Path

from fakes import FakeS3Client
from source_loader import (
    archive_source_artifact,
    build_raw_archive_key,
    prepare_source_artifact,
    sha256_file,
)


def test_prepare_local_zip_and_archive(
    faa_zip_path: Path,
    tmp_path: Path,
) -> None:
    s3 = FakeS3Client()

    artifact = prepare_source_artifact(
        event={
            "source_zip_path": str(
                faa_zip_path
            )
        },
        s3_client=s3,
        work_directory=str(
            tmp_path / "work"
        ),
        default_source_url=None,
        timeout_seconds=30,
    )

    assert artifact.source_kind == "LOCAL_ZIP"
    assert artifact.source_hash == sha256_file(
        faa_zip_path
    )

    key, uri = archive_source_artifact(
        artifact=artifact,
        s3_client=s3,
        archive_bucket_name="archive-bucket",
        raw_prefix="raw/source=faa-nasr",
        source_cycle="2026-07-09",
    )

    expected_key = build_raw_archive_key(
        raw_prefix="raw/source=faa-nasr",
        source_cycle="2026-07-09",
        source_hash=artifact.source_hash,
    )

    assert key == expected_key
    assert uri == (
        f"s3://archive-bucket/{key}"
    )

    assert s3.objects[
        (
            "archive-bucket",
            key,
        )
    ] == faa_zip_path.read_bytes()


def test_prepare_s3_replay_reuses_existing_reference(
    faa_zip_path: Path,
    tmp_path: Path,
) -> None:
    s3 = FakeS3Client()

    replay_key = (
        "raw/source=faa-nasr/"
        "cycle=2026-07-09/"
        "original-source.zip"
    )

    s3.objects[
        (
            "archive-bucket",
            replay_key,
        )
    ] = faa_zip_path.read_bytes()

    artifact = prepare_source_artifact(
        event={
            "source_s3_bucket": (
                "archive-bucket"
            ),
            "source_s3_key": replay_key,
        },
        s3_client=s3,
        work_directory=str(
            tmp_path / "replay"
        ),
        default_source_url=None,
        timeout_seconds=30,
    )

    key, uri = archive_source_artifact(
        artifact=artifact,
        s3_client=s3,
        archive_bucket_name="archive-bucket",
        raw_prefix="raw/source=faa-nasr",
        source_cycle="2026-07-09",
    )

    assert artifact.source_kind == "S3_REPLAY"
    assert key == replay_key
    assert uri == (
        f"s3://archive-bucket/{replay_key}"
    )

    assert s3.uploads == []