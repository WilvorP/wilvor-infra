from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import boto3
import requests
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from pathlib import Path


OPENSKY_RAW_SCHEMA_VERSION = "opensky_aircraft_raw.v1"


@dataclass(frozen=True)
class PollerConfig:
    aws_region: str
    aircraft_raw_stream_name: str
    aircraft_archive_bucket: str

    opensky_client_id: str
    opensky_client_secret: str
    opensky_token_url: str
    opensky_states_url: str

    lamin: str
    lomin: str
    lamax: str
    lomax: str

    request_timeout_seconds: int
    local_mode: str


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_utc_iso() -> str:
    return now_utc().isoformat()


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def env_or_default(name: str, default: str) -> str:
    return os.getenv(name) or default


def load_config() -> PollerConfig:
    return PollerConfig(
        aws_region=env_or_default("AWS_REGION", "us-west-1"),
        aircraft_raw_stream_name=require_env("AIRCRAFT_RAW_STREAM_NAME"),
        aircraft_archive_bucket=require_env("AIRCRAFT_ARCHIVE_BUCKET"),

        opensky_client_id=require_env("OPENSKY_CLIENT_ID"),
        opensky_client_secret=require_env("OPENSKY_CLIENT_SECRET"),
        opensky_token_url=env_or_default(
            "OPENSKY_TOKEN_URL",
            "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token",
        ),
        opensky_states_url=env_or_default(
            "OPENSKY_STATES_URL",
            "https://opensky-network.org/api/states/all",
        ),

        # Same small San Francisco Bay Area box currently used by Terraform/Lambda.
        lamin=env_or_default("OPENSKY_LAMIN", "37.0"),
        lomin=env_or_default("OPENSKY_LOMIN", "-123.0"),
        lamax=env_or_default("OPENSKY_LAMAX", "38.5"),
        lomax=env_or_default("OPENSKY_LOMAX", "-121.5"),

        request_timeout_seconds=int(env_or_default("REQUEST_TIMEOUT_SECONDS", "30")),
        local_mode=env_or_default("MODE", "local-opensky-poller"),
    )


def aws_client(service_name: str, region_name: str):
    profile_name = os.getenv("AWS_PROFILE")

    if profile_name:
        session = boto3.Session(profile_name=profile_name, region_name=region_name)
    else:
        session = boto3.Session(region_name=region_name)

    return session.client(service_name)


def get_access_token(config: PollerConfig) -> str:
    response = requests.post(
        config.opensky_token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": config.opensky_client_id,
            "client_secret": config.opensky_client_secret,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "wilvor-local-opensky-poller",
        },
        timeout=config.request_timeout_seconds,
    )

    response.raise_for_status()
    token_payload = response.json()

    access_token = token_payload.get("access_token")
    if not access_token:
        raise RuntimeError("OpenSky token response did not contain access_token")

    return access_token


def fetch_opensky_states(config: PollerConfig, access_token: str) -> dict[str, Any]:
    response = requests.get(
        config.opensky_states_url,
        params={
            "lamin": config.lamin,
            "lomin": config.lomin,
            "lamax": config.lamax,
            "lomax": config.lomax,
        },
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": "wilvor-local-opensky-poller",
        },
        timeout=config.request_timeout_seconds,
    )

    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, dict):
        raise RuntimeError("OpenSky response was not a JSON object")

    return payload


def archive_raw_response(
    *,
    s3_client,
    bucket: str,
    poll_id: str,
    response_body: dict[str, Any],
) -> str:
    now = now_utc()

    key = (
        "raw/source=opensky/"
        f"year={now.year:04d}/"
        f"month={now.month:02d}/"
        f"day={now.day:02d}/"
        f"hour={now.hour:02d}/"
        f"{poll_id}.json"
    )

    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(response_body, separators=(",", ":")).encode("utf-8"),
        ContentType="application/json",
    )

    return key


def chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[i : i + size] for i in range(0, len(items), size)]




def build_kinesis_records(
    *,
    poll_id: str,
    opensky_response: dict[str, Any],
    fetched_at_utc: str,
) -> list[dict[str, Any]]:
    states = opensky_response.get("states") or []
    response_time = opensky_response.get("time")

    records: list[dict[str, Any]] = []

    for index, state_vector in enumerate(states):
        icao24 = None

        if isinstance(state_vector, list) and len(state_vector) > 0:
            icao24 = state_vector[0]

        partition_key = str(icao24 or f"unknown-{index}")

        raw_event = {
            "schema_version": OPENSKY_RAW_SCHEMA_VERSION,
            "source": "opensky",
            "producer": "local",
            "poll_id": poll_id,
            "fetched_at_utc": fetched_at_utc,
            "opensky_response_time": response_time,
            "raw_index": index,
            "raw_state_vector": state_vector,
        }

        records.append(
            {
                "PartitionKey": partition_key,
                "Data": json.dumps(raw_event, separators=(",", ":")).encode("utf-8"),
            }
        )

    return records


def put_records_with_retries(
    *,
    kinesis_client,
    stream_name: str,
    records: list[dict[str, Any]],
    max_attempts: int = 3,
) -> tuple[int, int]:
    if not records:
        return 0, 0

    total_published = 0
    remaining = records

    for attempt in range(1, max_attempts + 1):
        result = kinesis_client.put_records(
            StreamName=stream_name,
            Records=remaining,
        )

        response_records = result.get("Records", [])
        failed_count = int(result.get("FailedRecordCount", 0))

        if failed_count == 0:
            total_published += len(remaining)
            return total_published, 0

        failed_records: list[dict[str, Any]] = []
        success_count = 0

        for original_record, response_record in zip(remaining, response_records):
            if "ErrorCode" in response_record:
                failed_records.append(original_record)
            else:
                success_count += 1

        total_published += success_count
        remaining = failed_records

        if attempt < max_attempts:
            sleep_seconds = min(2 ** attempt, 8)
            print(
                json.dumps(
                    {
                        "event": "kinesis_put_records_retry",
                        "attempt": attempt,
                        "failed_records": len(remaining),
                        "sleep_seconds": sleep_seconds,
                    }
                )
            )
            time.sleep(sleep_seconds)

    return total_published, len(remaining)


def publish_raw_records(
    *,
    kinesis_client,
    stream_name: str,
    poll_id: str,
    opensky_response: dict[str, Any],
    fetched_at_utc: str,
    dry_run: bool,
) -> tuple[int, int]:
    records = build_kinesis_records(
        poll_id=poll_id,
        opensky_response=opensky_response,
        fetched_at_utc=fetched_at_utc,
    )

    if dry_run:
        return len(records), 0

    published = 0
    failed = 0

    for batch in chunked(records, 500):
        batch_published, batch_failed = put_records_with_retries(
            kinesis_client=kinesis_client,
            stream_name=stream_name,
            records=batch,
        )
        published += batch_published
        failed += batch_failed

    return published, failed


def run_once(*, dry_run: bool) -> dict[str, Any]:
    config = load_config()

    s3_client = aws_client("s3", config.aws_region)
    kinesis_client = aws_client("kinesis", config.aws_region)

    poll_id = str(uuid.uuid4())
    fetched_at_utc = now_utc_iso()

    access_token = get_access_token(config)
    opensky_response = fetch_opensky_states(config, access_token)

    states_count = len(opensky_response.get("states") or [])

    s3_key = None
    if not dry_run:
        s3_key = archive_raw_response(
            s3_client=s3_client,
            bucket=config.aircraft_archive_bucket,
            poll_id=poll_id,
            response_body=opensky_response,
        )

    published_count, failed_count = publish_raw_records(
        kinesis_client=kinesis_client,
        stream_name=config.aircraft_raw_stream_name,
        poll_id=poll_id,
        opensky_response=opensky_response,
        fetched_at_utc=fetched_at_utc,
        dry_run=dry_run,
    )

    return {
        "ok": failed_count == 0,
        "mode": config.local_mode,
        "dry_run": dry_run,
        "poll_id": poll_id,
        "states_count": states_count,
        "published_to_kinesis": published_count,
        "failed_kinesis_records": failed_count,
        "raw_s3_key": s3_key,
        "aircraft_raw_stream_name": config.aircraft_raw_stream_name,
        "aircraft_archive_bucket": config.aircraft_archive_bucket,
        "aws_region": config.aws_region,
        "fetched_at_utc": fetched_at_utc,
    }


def main() -> int:
    load_dotenv(".env")

    parser = argparse.ArgumentParser(description="Wilvor local OpenSky poller")
    parser.add_argument("--once", action="store_true", help="Run one poll and exit")
    parser.add_argument("--loop", action="store_true", help="Poll continuously")
    parser.add_argument("--interval", type=int, default=30, help="Loop interval in seconds")
    parser.add_argument("--dry-run", action="store_true", help="Fetch OpenSky but do not write S3/Kinesis")

    args = parser.parse_args()

    if not args.once and not args.loop:
        args.once = True

    while True:
        try:
            result = run_once(dry_run=args.dry_run)
            print(json.dumps(result, indent=2))
        except (requests.RequestException, BotoCoreError, ClientError, RuntimeError) as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "event": "local_opensky_poller_failed",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                    indent=2,
                ),
                file=sys.stderr,
            )

            if args.once:
                return 1

        if args.once:
            return 0

        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())