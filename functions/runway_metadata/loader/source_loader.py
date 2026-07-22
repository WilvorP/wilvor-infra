from __future__ import annotations

import hashlib
import os
import re
import shutil
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_CHUNK_SIZE = 1024 * 1024
_SAFE_CYCLE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class SourceArtifact:
    local_zip_path: str
    source_hash: str
    source_kind: str
    replay_s3_bucket: str | None = None
    replay_s3_key: str | None = None
    source_url: str | None = None


def sanitize_cycle_for_key(source_cycle: str) -> str:
    value = _SAFE_CYCLE.sub("_", source_cycle.strip())
    if not value:
        raise RuntimeError("source_cycle cannot be empty")
    return value


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()

    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)

    return digest.hexdigest()


def validate_zip(path: str | os.PathLike[str]) -> None:
    if not Path(path).is_file():
        raise RuntimeError(f"FAA ZIP was not created at {path}")

    if Path(path).stat().st_size == 0:
        raise RuntimeError("FAA ZIP is empty")

    if not zipfile.is_zipfile(path):
        raise RuntimeError("FAA source is not a valid ZIP archive")


def download_http_zip(
    *,
    url: str,
    destination: str,
    timeout_seconds: int,
) -> None:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": "Wilvor-Runway-Metadata-Loader/0.1",
            "Accept": "application/zip,application/octet-stream,*/*",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout_seconds,
        ) as response:
            status = getattr(response, "status", 200)

            if status != 200:
                raise RuntimeError(
                    f"FAA download returned unexpected HTTP status {status}"
                )

            with open(destination, "wb") as output:
                while chunk := response.read(_CHUNK_SIZE):
                    output.write(chunk)

    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"FAA download returned HTTP {exc.code}: {exc.reason}"
        ) from exc

    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"FAA download failed: {exc.reason}"
        ) from exc


def prepare_source_artifact(
    *,
    event: dict[str, Any],
    s3_client: Any,
    work_directory: str,
    default_source_url: str | None,
    timeout_seconds: int,
) -> SourceArtifact:
    work_dir = Path(work_directory)
    work_dir.mkdir(parents=True, exist_ok=True)

    destination = work_dir / "faa-airports.zip"

    replay_bucket = str(
        event.get("source_s3_bucket") or ""
    ).strip()

    replay_key = str(
        event.get("source_s3_key") or ""
    ).strip()

    local_zip_path = str(
        event.get("source_zip_path") or ""
    ).strip()

    if replay_bucket or replay_key:
        if not replay_bucket or not replay_key:
            raise RuntimeError(
                "source_s3_bucket and source_s3_key "
                "must be provided together"
            )

        s3_client.download_file(
            replay_bucket,
            replay_key,
            str(destination),
        )

        source_kind = "S3_REPLAY"
        source_url = None

    elif local_zip_path:
        source_path = Path(local_zip_path)

        if not source_path.is_file():
            raise RuntimeError(
                f"source_zip_path does not exist: {source_path}"
            )

        if source_path.resolve() != destination.resolve():
            shutil.copyfile(source_path, destination)

        source_kind = "LOCAL_ZIP"
        source_url = None

    else:
        source_url = str(
            event.get("source_url")
            or default_source_url
            or ""
        ).strip()

        if not source_url:
            raise RuntimeError(
                "source_url is required when an S3 replay "
                "source is not provided"
            )

        download_http_zip(
            url=source_url,
            destination=str(destination),
            timeout_seconds=timeout_seconds,
        )

        source_kind = "HTTPS"

    validate_zip(destination)

    return SourceArtifact(
        local_zip_path=str(destination),
        source_hash=sha256_file(destination),
        source_kind=source_kind,
        replay_s3_bucket=replay_bucket or None,
        replay_s3_key=replay_key or None,
        source_url=source_url,
    )


def build_raw_archive_key(
    *,
    raw_prefix: str,
    source_cycle: str,
    source_hash: str,
) -> str:
    prefix = raw_prefix.strip("/")
    cycle = sanitize_cycle_for_key(source_cycle)

    return (
        f"{prefix}/"
        f"cycle={cycle}/"
        f"sha256={source_hash}/"
        f"original-source.zip"
    )


def archive_source_artifact(
    *,
    artifact: SourceArtifact,
    s3_client: Any,
    archive_bucket_name: str,
    raw_prefix: str,
    source_cycle: str,
) -> tuple[str, str]:
    if artifact.source_kind == "S3_REPLAY":
        assert artifact.replay_s3_bucket is not None
        assert artifact.replay_s3_key is not None

        return (
            artifact.replay_s3_key,
            (
                f"s3://{artifact.replay_s3_bucket}/"
                f"{artifact.replay_s3_key}"
            ),
        )

    key = build_raw_archive_key(
        raw_prefix=raw_prefix,
        source_cycle=source_cycle,
        source_hash=artifact.source_hash,
    )

    s3_client.upload_file(
        artifact.local_zip_path,
        archive_bucket_name,
        key,
        ExtraArgs={
            "ContentType": "application/zip",
            "Metadata": {
                "source": "faa-nasr",
                "source-cycle": source_cycle,
                "sha256": artifact.source_hash,
            },
        },
    )

    return key, f"s3://{archive_bucket_name}/{key}"