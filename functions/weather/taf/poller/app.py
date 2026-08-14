import gzip
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any
from boto3.dynamodb.conditions import Key
import boto3

dynamodb = boto3.resource("dynamodb")

from wilvor_weather.monitoring import emit_metric


kinesis = boto3.client("kinesis")
s3 = boto3.client("s3")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def extract_detail(event: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(event, dict):
        return {}

    detail = event.get("detail")
    if isinstance(detail, dict):
        return detail

    return event


def get_hazard_station_candidates_table():
    table_name = os.environ.get("HAZARD_STATION_CANDIDATES_TABLE_NAME", "").strip()
    if not table_name:
        raise RuntimeError("HAZARD_STATION_CANDIDATES_TABLE_NAME is not configured")

    return dynamodb.Table(table_name)


def get_station_ids_from_env() -> list[str]:
    raw_station_ids = os.environ.get("TAF_STATION_IDS", "")
    station_ids = sorted(
        {
            station_id.strip().upper()
            for station_id in raw_station_ids.split(",")
            if station_id.strip()
        }
    )

    if not station_ids:
        raise RuntimeError("TAF_STATION_IDS does not contain any station IDs")

    return station_ids


def query_candidate_stations_for_hazard(
    hazard_version_key: str,
) -> list[dict[str, Any]]:
    table = get_hazard_station_candidates_table()
    items: list[dict[str, Any]] = []
    exclusive_start_key = None

    while True:
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("hazard_version_key").eq(hazard_version_key),
        }

        if exclusive_start_key:
            kwargs["ExclusiveStartKey"] = exclusive_start_key

        response = table.query(**kwargs)
        items.extend(response.get("Items", []))

        exclusive_start_key = response.get("LastEvaluatedKey")
        if not exclusive_start_key:
            break

    return items


def get_station_ids(
    event: dict[str, Any] | None = None,
) -> list[str]:
    detail = extract_detail(event)
    hazard_version_key = str(detail.get("hazard_version_key") or "").strip()

    # Production path: hazard.stations.ready -> HazardStationCandidates -> station IDs.
    if hazard_version_key:
        candidate_items = query_candidate_stations_for_hazard(hazard_version_key)
        return sorted(
            {
                str(item["station_id"]).strip().upper()
                for item in candidate_items
                if str(item.get("station_id") or "").strip()
            }
        )

    # Manual/local fallback only.
    return get_station_ids_from_env()


def chunked(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def fetch_taf_records(
    station_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    base_url = os.environ.get(
        "NOAA_TAF_URL",
        "https://aviationweather.gov/api/data/taf",
    )

    resolved_station_ids = station_ids if station_ids is not None else get_station_ids()
    resolved_station_ids = sorted(
        {
            station_id.strip().upper()
            for station_id in resolved_station_ids
            if station_id.strip()
        }
    )

    if not resolved_station_ids:
        return []

    chunk_size = int(os.environ.get("TAF_STATION_CHUNK_SIZE", "100"))

    all_records: list[dict[str, Any]] = []
    for station_chunk in chunked(resolved_station_ids, chunk_size):
        query_string = urllib.parse.urlencode(
            {
                "ids": ",".join(station_chunk),
                "format": "json",
            }
        )

        url = f"{base_url}?{query_string}"

        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "User-Agent": "Wilvor-TAF-Poller/0.2",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status = response.status

                if status == 204:
                    continue

                body = response.read().decode("utf-8")

        except urllib.error.HTTPError as exc:
            if exc.code == 204:
                continue

            raise RuntimeError(
                f"NOAA TAF API returned HTTP {exc.code}: {exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"NOAA TAF API request failed: {exc.reason}"
            ) from exc

        if status != 200:
            raise RuntimeError(
                f"NOAA TAF API returned unexpected status {status}"
            )
        try:
            response_data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "NOAA TAF API returned invalid JSON"
            ) from exc

        if isinstance(response_data, list):
            for record in response_data:
                if isinstance(record, dict):
                    all_records.append(record)

        elif isinstance(response_data, dict):
            all_records.append(response_data)
        else:
            raise RuntimeError(
                "NOAA TAF API response was not a JSON list or object"
            )

    return all_records


def build_s3_key(
    *,
    poll_id: str,
    raw_prefix: str,
    received_at: datetime,
) -> str:
    prefix = raw_prefix.rstrip("/")

    return (
        f"{prefix}/"
        f"year={received_at.year:04d}/"
        f"month={received_at.month:02d}/"
        f"day={received_at.day:02d}/"
        f"hour={received_at.hour:02d}/"
        f"taf-{poll_id}.json.gz"
    )


def archive_raw_response(
    *,
    poll_id: str,
    records: list[dict[str, Any]],
    received_at: datetime,
) -> str:
    bucket_name = os.environ["ARCHIVE_BUCKET_NAME"]
    raw_prefix = os.environ.get("RAW_PREFIX", "raw/source=taf")

    s3_key = build_s3_key(
        poll_id=poll_id,
        raw_prefix=raw_prefix,
        received_at=received_at,
    )

    payload = json.dumps(records).encode("utf-8")
    compressed_payload = gzip.compress(payload)

    s3.put_object(
        Bucket=bucket_name,
        Key=s3_key,
        Body=compressed_payload,
        ContentType="application/json",
        ContentEncoding="gzip",
    )

    return s3_key


def derive_partition_key(
    taf_record: dict[str, Any],
    record_index: int,
) -> str:
    for key in ("icaoId", "stationId", "station_id", "id"):
        value = taf_record.get(key)

        if value is not None and str(value).strip():
            return str(value).strip().upper()

    return f"taf-{record_index}"


def publish_raw_records(
    *,
    poll_id: str,
    received_at: str,
    raw_s3_bucket: str,
    raw_s3_key: str,
    records: list[dict[str, Any]],
    trigger_detail: dict[str, Any] | None = None,
) -> tuple[int, int]:
    stream_name = os.environ["TAF_RAW_STREAM_NAME"]
    trigger_detail = trigger_detail or {}

    kinesis_records = []
    for record_index, taf_record in enumerate(records):
        raw_event = {
            "schema_version": "raw.noaa.taf.v1",
            "source": "NOAA_AVIATION_WEATHER",
            "product_type": "TAF",
            "ingestion_type": "RAW_TAF_RECORD",
            "poll_id": poll_id,
            "received_at": received_at,
            "raw_s3_bucket": raw_s3_bucket,
            "raw_s3_key": raw_s3_key,
            "record_index": record_index,
            "trigger": {
                "event_type": trigger_detail.get("event_type"),
                "hazard_version_key": trigger_detail.get("hazard_version_key"),
                "hazard_id": trigger_detail.get("hazard_id"),
                "hazard_source_version": trigger_detail.get("hazard_source_version"),
                "correlation_id": trigger_detail.get("correlation_id"),
            },
            "taf": taf_record,
        }
        kinesis_records.append(
            {
                "PartitionKey": derive_partition_key(
                    taf_record,
                    record_index,
                ),
                "Data": json.dumps(raw_event).encode("utf-8"),
            }
        )

    published_count = 0
    failed_count = 0

    for batch in chunked(kinesis_records, 500):
        response = kinesis.put_records(
            StreamName=stream_name,
            Records=batch,
        )
        batch_failed_count = int(
            response.get("FailedRecordCount", 0)
        )

        failed_count += batch_failed_count
        published_count += len(batch) - batch_failed_count

    return published_count, failed_count


def lambda_handler(event, context):
    poll_id = str(uuid.uuid4())
    received_at_datetime = now_utc()
    received_at = received_at_datetime.isoformat()

    raw_s3_key: str | None = None
    record_count = 0
    published_count = 0
    failed_count = 0
    raw_archive_success = 0

    try:
        trigger_detail = extract_detail(event)
        station_ids = get_station_ids(event)
        records = fetch_taf_records(station_ids)
        record_count = len(records)

        raw_s3_key = archive_raw_response(
            poll_id=poll_id,
            records=records,
            received_at=received_at_datetime,
        )

        raw_archive_success = 1

        archive_bucket_name = os.environ["ARCHIVE_BUCKET_NAME"]

        published_count, failed_count = publish_raw_records(
            poll_id=poll_id,
            received_at=received_at,
            raw_s3_bucket=archive_bucket_name,
            raw_s3_key=raw_s3_key,
            records=records,
            trigger_detail=trigger_detail,
        )

        if failed_count > 0:
            raise RuntimeError(
                f"Failed to publish {failed_count} of "
                f"{record_count} TAF records to Kinesis"
            )

        emit_metric(
            pipeline="taf",
            component="taf_poller",
            stage="poll",
            metrics={
                "PollSuccess": 1,
                "PollFailure": 0,
                "RecordsReceived": record_count,
                "PublishedToKinesis": published_count,
                "FailedKinesisRecords": failed_count,
                "RawArchiveSuccess": raw_archive_success,
            },
            properties={
                "PollId": poll_id,
                "RawS3Key": raw_s3_key,
                "HazardVersionKey": trigger_detail.get("hazard_version_key", ""),
                "RequestedStationCount": len(station_ids),
            },
        )

        print(
            json.dumps(
                {
                    "message": "TAF poll completed",
                    "poll_id": poll_id,
                    "received_at": received_at,
                    "raw_s3_key": raw_s3_key,
                    "record_count": record_count,
                    "published_count": published_count,
                    "failed_kinesis_records": failed_count,
                    "hazard_version_key": trigger_detail.get("hazard_version_key"),
                    "requested_station_count": len(station_ids),
                    "requested_station_ids": station_ids,
                }
            )
        )

        return {
            "ok": True,
            "poll_id": poll_id,
            "received_at": received_at,
            "raw_s3_key": raw_s3_key,
            "record_count": record_count,
            "published_count": published_count,
            "failed_kinesis_records": failed_count,
            "hazard_version_key": trigger_detail.get("hazard_version_key"),
            "requested_station_count": len(station_ids),
            "requested_station_ids": station_ids,
        }

    except Exception as exc:
        emit_metric(
            pipeline="taf",
            component="taf_poller",
            stage="poll",
            metrics={
                "PollSuccess": 0,
                "PollFailure": 1,
                "RecordsReceived": record_count,
                "PublishedToKinesis": published_count,
                "FailedKinesisRecords": failed_count,
                "RawArchiveSuccess": raw_archive_success,
            },
            properties={
                "PollId": poll_id,
                "RawS3Key": raw_s3_key or "",
                "ErrorType": exc.__class__.__name__,
                "ErrorMessage": str(exc),
            },
        )

        print(
            json.dumps(
                {
                    "message": "TAF poll failed",
                    "poll_id": poll_id,
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                    "record_count": record_count,
                    "published_count": published_count,
                    "failed_kinesis_records": failed_count,
                    "raw_archive_success": raw_archive_success,
                }
            )
        )

        raise