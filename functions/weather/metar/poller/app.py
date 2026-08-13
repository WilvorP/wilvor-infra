import gzip
import json
import os
import uuid
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from wilvor_weather.monitoring import emit_metric


kinesis = boto3.client("kinesis")
s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

HSC_TABLE_NAME = os.environ["HAZARD_STATION_CANDIDATES_TABLE_NAME"]
hazard_station_candidates = dynamodb.Table(HSC_TABLE_NAME)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso_time(value: Any) -> datetime | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def is_candidate_current(
    item: dict[str, Any],
    at_time: datetime,
) -> bool:
    station_id = str(item.get("station_id") or "").strip()
    if not station_id:
        return False

    expires_at_epoch = item.get("expires_at_epoch")
    if expires_at_epoch is not None:
        try:
            if int(expires_at_epoch) <= int(at_time.timestamp()):
                return False
        except (TypeError, ValueError):
            return False

    valid_to = parse_iso_time(item.get("valid_to_utc"))
    if valid_to is not None and valid_to <= at_time:
        return False

    return True


def merge_candidate_scope(
    scope: dict[str, dict[str, Any]],
    item: dict[str, Any],
) -> None:
    station_id = str(item["station_id"]).upper()

    existing = scope.setdefault(
        station_id,
        {
            "station_id": station_id,
            "airport_id": None,
            "hazard_ids": set(),
            "hazard_version_keys": set(),
        },
    )

    airport_id = item.get("airport_id")
    if airport_id and not existing.get("airport_id"):
        existing["airport_id"] = str(airport_id).upper()

    hazard_id = item.get("hazard_id")
    if hazard_id:
        existing["hazard_ids"].add(str(hazard_id))

    hazard_version_key = item.get("hazard_version_key")
    if hazard_version_key:
        existing["hazard_version_keys"].add(
            str(hazard_version_key)
        )


def finalize_scope(
    scope: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}

    for station_id, context in scope.items():
        item = {
            "station_id": station_id,
            "hazard_ids": sorted(
                context.get("hazard_ids", set())
            ),
            "hazard_version_keys": sorted(
                context.get("hazard_version_keys", set())
            ),
        }

        if context.get("airport_id"):
            item["airport_id"] = context["airport_id"]

        result[station_id] = item

    return result


def query_hazard_station_candidates(
    hazard_version_key: str,
    at_time: datetime,
) -> dict[str, dict[str, Any]]:
    scope: dict[str, dict[str, Any]] = {}

    query_kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key(
            "hazard_version_key"
        ).eq(hazard_version_key)
    }

    while True:
        response = hazard_station_candidates.query(
            **query_kwargs
        )

        for item in response.get("Items", []):
            if is_candidate_current(item, at_time):
                merge_candidate_scope(scope, item)

        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break

        query_kwargs["ExclusiveStartKey"] = last_key

    return finalize_scope(scope)


def scan_active_hazard_station_candidates(
    at_time: datetime,
) -> dict[str, dict[str, Any]]:
    scope: dict[str, dict[str, Any]] = {}
    scan_kwargs: dict[str, Any] = {}

    while True:
        response = hazard_station_candidates.scan(
            **scan_kwargs
        )

        for item in response.get("Items", []):
            if is_candidate_current(item, at_time):
                merge_candidate_scope(scope, item)

        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break

        scan_kwargs["ExclusiveStartKey"] = last_key

    return finalize_scope(scope)


def resolve_station_scope(
    event: dict[str, Any],
    at_time: datetime,
) -> tuple[
    str,
    dict[str, dict[str, Any]],
    str,
    str | None,
]:
    detail = event.get("detail") if isinstance(
        event,
        dict,
    ) else None

    detail = detail if isinstance(detail, dict) else {}

    detail_type = str(
        event.get("detail-type")
        or detail.get("event_type")
        or ""
    )

    if detail_type == "hazard.stations.ready":
        hazard_version_key = str(
            detail.get("hazard_version_key") or ""
        ).strip()

        if not hazard_version_key:
            raise RuntimeError(
                "hazard.stations.ready event missing "
                "detail.hazard_version_key"
            )

        correlation_id = str(
            detail.get("correlation_id")
            or event.get("id")
            or uuid.uuid4()
        )

        return (
            "HAZARD_STATIONS_READY",
            query_hazard_station_candidates(
                hazard_version_key,
                at_time,
            ),
            correlation_id,
            hazard_version_key,
        )

    correlation_id = str(
        event.get("id") or uuid.uuid4()
    )

    return (
        "SCHEDULED_HSC_REFRESH",
        scan_active_hazard_station_candidates(
            at_time
        ),
        correlation_id,
        None,
    )


def build_metar_url(
    station_ids: list[str],
) -> str:
    if not station_ids:
        raise ValueError(
            "station_ids cannot be empty"
        )

    base_url = os.environ["NOAA_METAR_URL"]
    parsed = urllib.parse.urlsplit(base_url)

    query = dict(
        urllib.parse.parse_qsl(
            parsed.query,
            keep_blank_values=True,
        )
    )

    query["ids"] = ",".join(station_ids)
    query.setdefault("format", "geojson")

    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(query),
            parsed.fragment,
        )
    )


def fetch_noaa_metars(
    station_ids: list[str],
) -> Any:
    url = build_metar_url(station_ids)

    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": (
                "Wilvor-METAR-Poller/0.2"
            ),
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:
            status = response.status
            body = response.read().decode(
                "utf-8"
            )

    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"NOAA METAR API returned HTTP "
            f"{exc.code}: {exc.reason}"
        ) from exc

    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"NOAA METAR API request failed: "
            f"{exc.reason}"
        ) from exc

    if status != 200:
        raise RuntimeError(
            f"NOAA METAR API returned "
            f"unexpected status {status}"
        )

    return json.loads(body)


def extract_records(
    response_body: Any,
) -> list[Any]:
    if isinstance(response_body, dict):
        if (
            response_body.get("type")
            == "FeatureCollection"
        ):
            features = response_body.get(
                "features"
            )

            if isinstance(features, list):
                return features

        data = response_body.get("data")
        if isinstance(data, list):
            return data

    if isinstance(response_body, list):
        return response_body

    return []


def build_s3_key(
    *,
    poll_id: str,
    raw_prefix: str,
    received_at: datetime,
    part_index: int | None = None,
) -> str:
    prefix = raw_prefix.rstrip("/")

    filename = (
        f"metar-{poll_id}.json.gz"
        if part_index is None
        else (
            f"metar-{poll_id}-"
            f"part-{part_index:03d}.json.gz"
        )
    )

    return (
        f"{prefix}/"
        f"year={received_at.year:04d}/"
        f"month={received_at.month:02d}/"
        f"day={received_at.day:02d}/"
        f"hour={received_at.hour:02d}/"
        f"{filename}"
    )


def archive_raw_response(
    *,
    poll_id: str,
    response_body: Any,
    received_at: datetime,
    part_index: int | None = None,
) -> str:
    bucket = os.environ[
        "ARCHIVE_BUCKET_NAME"
    ]

    raw_prefix = os.environ.get(
        "RAW_PREFIX",
        "raw/source=metar",
    )

    key = build_s3_key(
        poll_id=poll_id,
        raw_prefix=raw_prefix,
        received_at=received_at,
        part_index=part_index,
    )

    payload = json.dumps(
        response_body
    ).encode("utf-8")

    compressed = gzip.compress(payload)

    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=compressed,
        ContentType="application/json",
        ContentEncoding="gzip",
    )

    return key


def derive_partition_key(
    feature: Any,
    record_index: int,
) -> str:
    if isinstance(feature, dict):
        properties = feature.get(
            "properties"
        )

        if isinstance(properties, dict):
            for key in (
                "icaoId",
                "station",
                "station_id",
                "id",
            ):
                value = properties.get(key)

                if (
                    value is not None
                    and str(value).strip()
                ):
                    return str(value).upper()

    return f"metar-{record_index}"


def chunked(
    items: list[Any],
    size: int,
) -> list[list[Any]]:
    if size <= 0:
        raise ValueError(
            "chunk size must be greater than zero"
        )

    return [
        items[index:index + size]
        for index in range(
            0,
            len(items),
            size,
        )
    ]


def publish_raw_records(
    *,
    poll_id: str,
    received_at: str,
    raw_s3_bucket: str,
    raw_s3_key: str,
    records: list[Any],
    station_scope: dict[
        str,
        dict[str, Any],
    ],
    trigger_type: str,
    correlation_id: str,
    trigger_hazard_version_key:
        str | None,
) -> tuple[int, int]:
    stream_name = os.environ[
        "METAR_RAW_STREAM_NAME"
    ]

    kinesis_records = []

    for record_index, feature in enumerate(
        records
    ):
        station_id = derive_partition_key(
            feature,
            record_index,
        )

        candidate_context = (
            station_scope.get(
                station_id,
                {
                    "station_id": station_id,
                },
            )
        )

        raw_event = {
            "schema_version":
                "raw.noaa.metar.v2",
            "source":
                "NOAA_AVIATION_WEATHER",
            "product_type": "METAR",
            "ingestion_type":
                "RAW_METAR_FEATURE",
            "poll_id": poll_id,
            "correlation_id":
                correlation_id,
            "received_at": received_at,
            "raw_s3_bucket":
                raw_s3_bucket,
            "raw_s3_key": raw_s3_key,
            "record_index":
                record_index,
            "request_source":
                trigger_type,
            "trigger_hazard_version_key":
                trigger_hazard_version_key,
            "candidate_context":
                candidate_context,
            "feature": feature,
        }

        raw_event = {
            key: value
            for key, value
            in raw_event.items()
            if value is not None
        }

        kinesis_records.append(
            {
                "PartitionKey":
                    station_id,
                "Data": json.dumps(
                    raw_event
                ).encode("utf-8"),
            }
        )

    published = 0
    failed = 0

    for batch in chunked(
        kinesis_records,
        500,
    ):
        result = kinesis.put_records(
            StreamName=stream_name,
            Records=batch,
        )

        batch_failed = int(
            result.get(
                "FailedRecordCount",
                0,
            )
        )

        failed += batch_failed
        published += (
            len(batch) - batch_failed
        )

    return published, failed


def lambda_handler(event, context):
    poll_id = str(uuid.uuid4())
    received_at_dt = now_utc()
    received_at = (
        received_at_dt.isoformat()
    )

    feature_count = 0
    published_count = 0
    failed_count = 0
    archived_parts = 0
    raw_s3_keys: list[str] = []

    try:
        (
            trigger_type,
            station_scope,
            correlation_id,
            hazard_version_key,
        ) = resolve_station_scope(
            event or {},
            received_at_dt,
        )

        station_ids = sorted(
            station_scope.keys()
        )

        if not station_ids:
            emit_metric(
                pipeline="metar",
                component="metar_poller",
                stage="poll",
                metrics={
                    "PollSuccess": 1,
                    "PollFailure": 0,
                    "CandidateStations": 0,
                    "ApiRequests": 0,
                    "FeaturesReceived": 0,
                    "PublishedToKinesis": 0,
                    "FailedKinesisRecords": 0,
                    "RawArchiveSuccess": 0,
                },
                properties={
                    "PollId": poll_id,
                    "CorrelationId":
                        correlation_id,
                    "RequestSource":
                        trigger_type,
                },
            )

            return {
                "ok": True,
                "poll_id": poll_id,
                "correlation_id":
                    correlation_id,
                "request_source":
                    trigger_type,
                "candidate_station_count":
                    0,
                "feature_count": 0,
                "published_count": 0,
                "failed_kinesis_records":
                    0,
                "reason":
                    "NO_ACTIVE_HAZARD_STATIONS",
            }

        station_chunk_size = int(
            os.environ.get(
                "METAR_STATION_CHUNK_SIZE",
                "100",
            )
        )

        station_chunks = chunked(
            station_ids,
            station_chunk_size,
        )

        bucket = os.environ[
            "ARCHIVE_BUCKET_NAME"
        ]

        for (
            part_index,
            station_chunk,
        ) in enumerate(station_chunks):
            response_body = (
                fetch_noaa_metars(
                    station_chunk
                )
            )

            raw_s3_key = (
                archive_raw_response(
                    poll_id=poll_id,
                    response_body=
                        response_body,
                    received_at=
                        received_at_dt,
                    part_index=(
                        part_index
                        if (
                            len(
                                station_chunks
                            ) > 1
                        )
                        else None
                    ),
                )
            )

            raw_s3_keys.append(
                raw_s3_key
            )

            archived_parts += 1

            records = extract_records(
                response_body
            )

            feature_count += len(
                records
            )

            published, failed = (
                publish_raw_records(
                    poll_id=poll_id,
                    received_at=
                        received_at,
                    raw_s3_bucket=
                        bucket,
                    raw_s3_key=
                        raw_s3_key,
                    records=records,
                    station_scope=
                        station_scope,
                    trigger_type=
                        trigger_type,
                    correlation_id=
                        correlation_id,
                    trigger_hazard_version_key=
                        hazard_version_key,
                )
            )

            published_count += (
                published
            )

            failed_count += failed

        if failed_count > 0:
            raise RuntimeError(
                f"Failed to publish "
                f"{failed_count} of "
                f"{feature_count} "
                f"METAR records to Kinesis"
            )

        emit_metric(
            pipeline="metar",
            component="metar_poller",
            stage="poll",
            metrics={
                "PollSuccess": 1,
                "PollFailure": 0,
                "CandidateStations":
                    len(station_ids),
                "ApiRequests":
                    len(station_chunks),
                "FeaturesReceived":
                    feature_count,
                "PublishedToKinesis":
                    published_count,
                "FailedKinesisRecords":
                    failed_count,
                "RawArchiveSuccess":
                    archived_parts,
            },
            properties={
                "PollId": poll_id,
                "CorrelationId":
                    correlation_id,
                "RequestSource":
                    trigger_type,
                "HazardVersionKey":
                    hazard_version_key
                    or "",
            },
        )

        result = {
            "ok": True,
            "poll_id": poll_id,
            "correlation_id":
                correlation_id,
            "request_source":
                trigger_type,
            "hazard_version_key":
                hazard_version_key,
            "received_at":
                received_at,
            "candidate_station_count":
                len(station_ids),
            "api_request_count":
                len(station_chunks),
            "raw_s3_keys":
                raw_s3_keys,
            "feature_count":
                feature_count,
            "published_count":
                published_count,
            "failed_kinesis_records":
                failed_count,
        }

        print(
            json.dumps(
                {
                    "message":
                        "METAR poll completed",
                    **result,
                }
            )
        )

        return result

    except Exception as exc:
        emit_metric(
            pipeline="metar",
            component="metar_poller",
            stage="poll",
            metrics={
                "PollSuccess": 0,
                "PollFailure": 1,
                "FeaturesReceived":
                    feature_count,
                "PublishedToKinesis":
                    published_count,
                "FailedKinesisRecords":
                    failed_count,
                "RawArchiveSuccess":
                    archived_parts,
            },
            properties={
                "PollId": poll_id,
                "ErrorType":
                    exc.__class__.__name__,
                "ErrorMessage":
                    str(exc),
            },
        )

        print(
            json.dumps(
                {
                    "message":
                        "METAR poll failed",
                    "poll_id":
                        poll_id,
                    "error_type":
                        exc.__class__.__name__,
                    "error":
                        str(exc),
                    "feature_count":
                        feature_count,
                    "published_count":
                        published_count,
                    "failed_kinesis_records":
                        failed_count,
                    "raw_archive_success":
                        archived_parts,
                }
            )
        )

        raise