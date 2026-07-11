import gzip
import json
import os
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

import boto3


kinesis = boto3.client("kinesis")
s3 = boto3.client("s3")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_utc_iso() -> str:
    return now_utc().isoformat()


def fetch_noaa_sigmets() -> Any:
    url = os.environ["NOAA_SIGMET_URL"]

    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": "Wilvor-SIGMET-Poller/0.1",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"NOAA SIGMET API returned HTTP {exc.code}: {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"NOAA SIGMET API request failed: {exc.reason}") from exc

    if status != 200:
        raise RuntimeError(f"NOAA SIGMET API returned unexpected status {status}")

    return json.loads(body)


def extract_records(response_body: Any) -> list[Any]:
    if isinstance(response_body, dict):
        if response_body.get("type") == "FeatureCollection":
            features = response_body.get("features")
            if isinstance(features, list):
                return features

        data = response_body.get("data")
        if isinstance(data, list):
            return data

    if isinstance(response_body, list):
        return response_body

    return []


def build_s3_key(*, poll_id: str, raw_prefix: str, received_at: datetime) -> str:
    prefix = raw_prefix.rstrip("/")
    return (
        f"{prefix}/"
        f"year={received_at.year:04d}/"
        f"month={received_at.month:02d}/"
        f"day={received_at.day:02d}/"
        f"hour={received_at.hour:02d}/"
        f"sigmet-{poll_id}.json.gz"
    )


def archive_raw_response(
    *,
    poll_id: str,
    response_body: Any,
    received_at: datetime,
) -> str:
    bucket = os.environ["ARCHIVE_BUCKET_NAME"]
    raw_prefix = os.environ.get("RAW_PREFIX", "raw/source=sigmet")

    key = build_s3_key(
        poll_id=poll_id,
        raw_prefix=raw_prefix,
        received_at=received_at,
    )

    payload = json.dumps(response_body).encode("utf-8")
    compressed = gzip.compress(payload)

    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=compressed,
        ContentType="application/json",
        ContentEncoding="gzip",
    )

    return key


def derive_partition_key(feature: Any, record_index: int) -> str:
    if isinstance(feature, dict):
        properties = feature.get("properties")
        if isinstance(properties, dict):
            for key in (
                "id",
                "airSigmetId",
                "hazard",
                "rawSigmet",
                "validTimeFrom",
            ):
                value = properties.get(key)
                if value is not None and str(value).strip():
                    return str(value)

    return f"sigmet-{record_index}"


def chunked(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def publish_raw_records(
    *,
    poll_id: str,
    received_at: str,
    raw_s3_bucket: str,
    raw_s3_key: str,
    records: list[Any],
) -> tuple[int, int]:
    stream_name = os.environ["SIGMET_RAW_STREAM_NAME"]

    kinesis_records = []

    for record_index, feature in enumerate(records):
        raw_event = {
            "schema_version": "raw.noaa.airsigmet.v1",
            "source": "NOAA_AVIATION_WEATHER",
            "product_type": "SIGMET",
            "ingestion_type": "RAW_SIGMET_FEATURE",
            "poll_id": poll_id,
            "received_at": received_at,
            "raw_s3_bucket": raw_s3_bucket,
            "raw_s3_key": raw_s3_key,
            "record_index": record_index,
            "feature": feature,
        }

        kinesis_records.append(
            {
                "PartitionKey": derive_partition_key(feature, record_index),
                "Data": json.dumps(raw_event).encode("utf-8"),
            }
        )

    published = 0
    failed = 0

    for batch in chunked(kinesis_records, 500):
        result = kinesis.put_records(
            StreamName=stream_name,
            Records=batch,
        )

        batch_failed = int(result.get("FailedRecordCount", 0))
        failed += batch_failed
        published += len(batch) - batch_failed

    return published, failed


def lambda_handler(event, context):
    poll_id = str(uuid.uuid4())
    received_at_dt = now_utc()
    received_at = received_at_dt.isoformat()

    response_body = fetch_noaa_sigmets()

    raw_s3_key = archive_raw_response(
        poll_id=poll_id,
        response_body=response_body,
        received_at=received_at_dt,
    )

    records = extract_records(response_body)
    feature_count = len(records)

    bucket = os.environ["ARCHIVE_BUCKET_NAME"]

    published_count, failed_count = publish_raw_records(
        poll_id=poll_id,
        received_at=received_at,
        raw_s3_bucket=bucket,
        raw_s3_key=raw_s3_key,
        records=records,
    )

    print(
        json.dumps(
            {
                "message": "SIGMET poll completed",
                "poll_id": poll_id,
                "raw_s3_key": raw_s3_key,
                "feature_count": feature_count,
                "published_count": published_count,
                "failed_kinesis_records": failed_count,
            }
        )
    )

    if failed_count > 0:
        raise RuntimeError(
            f"Failed to publish {failed_count} of {feature_count} SIGMET records to Kinesis"
        )

    return {
        "ok": True,
        "poll_id": poll_id,
        "received_at": received_at,
        "raw_s3_key": raw_s3_key,
        "feature_count": feature_count,
        "published_count": published_count,
        "failed_kinesis_records": failed_count,
    }
