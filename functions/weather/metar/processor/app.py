import base64
import hashlib
import json
import os
import uuid
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError


dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
events = boto3.client("events")

TABLE_NAME = os.environ[
    "METAR_LATEST_TABLE_NAME"
]

BAD_RECORDS_BUCKET = os.environ[
    "BAD_RECORDS_BUCKET_NAME"
]

BAD_RECORDS_PREFIX = os.environ.get(
    "BAD_RECORDS_PREFIX",
    "bad-records/source=metar_processor",
)

SCHEMA_VERSION = os.environ.get(
    "SCHEMA_VERSION",
    "wilvor.metar_latest.v4.0",
)

EVENT_SCHEMA_VERSION = os.environ.get(
    "EVENT_SCHEMA_VERSION",
    "wilvor.event.metar.updated.v1",
)

ENVIRONMENT = os.environ.get(
    "ENVIRONMENT",
    "dev",
)

EVENT_BUS_NAME = os.environ.get(
    "EVENT_BUS_NAME",
    "default",
)

METAR_FRESH_SECONDS = int(
    os.environ.get(
        "METAR_FRESH_SECONDS",
        "600",
    )
)

METAR_ACCEPTABLE_SECONDS = int(
    os.environ.get(
        "METAR_ACCEPTABLE_SECONDS",
        "1800",
    )
)

METAR_TTL_SECONDS = int(
    os.environ.get(
        "METAR_TTL_SECONDS",
        "86400",
    )
)

EVENT_CHANGE_TYPES = {
    "NEW",
    "UPDATED",
    "CORRECTED",
}

table = dynamodb.Table(TABLE_NAME)


class PermanentRecordError(Exception):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(
        timezone.utc
    ).isoformat()


def parse_time(value: Any) -> datetime:
    if value is None:
        raise PermanentRecordError(
            "missing observation time"
        )

    if isinstance(value, Decimal):
        value = float(value)

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(
            value,
            tz=timezone.utc,
        )

    if isinstance(value, str):
        cleaned = value.strip()

        if not cleaned:
            raise PermanentRecordError(
                "empty observation time"
            )

        if cleaned.endswith("Z"):
            cleaned = (
                cleaned[:-1] + "+00:00"
            )

        try:
            parsed = datetime.fromisoformat(
                cleaned
            )
        except ValueError as exc:
            raise PermanentRecordError(
                f"invalid observation time: "
                f"{value}"
            ) from exc

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return parsed.astimezone(
            timezone.utc
        )

    raise PermanentRecordError(
        "unsupported observation time "
        f"type: {type(value)}"
    )


def parse_optional_time(
    value: Any,
) -> datetime | None:
    if value in (None, ""):
        return None

    return parse_time(value)


def first_present(
    source: dict[str, Any],
    keys: list[str],
) -> Any:
    for key in keys:
        if (
            key in source
            and source[key] not in (
                None,
                "",
            )
        ):
            return source[key]

    return None


def to_decimal(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))

    if isinstance(value, dict):
        return {
            key: to_decimal(item)
            for key, item
            in value.items()
            if item is not None
        }

    if isinstance(value, list):
        return [
            to_decimal(item)
            for item in value
            if item is not None
        ]

    return value


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        if (
            value
            == value.to_integral_value()
        ):
            return int(value)

        return float(value)

    if isinstance(value, dict):
        return {
            key: json_safe(item)
            for key, item
            in value.items()
        }

    if isinstance(value, list):
        return [
            json_safe(item)
            for item in value
        ]

    return value


def decode_kinesis_record(
    record: dict[str, Any],
) -> dict[str, Any]:
    encoded = record["kinesis"]["data"]

    raw_bytes = base64.b64decode(
        encoded
    )

    try:
        return json.loads(
            raw_bytes.decode("utf-8")
        )
    except json.JSONDecodeError as exc:
        raise PermanentRecordError(
            "invalid JSON in Kinesis record"
        ) from exc


def extract_feature(
    payload: dict[str, Any],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
]:
    metadata = {
        "poll_id":
            payload.get("poll_id"),
        "correlation_id":
            payload.get("correlation_id"),
        "received_at":
            payload.get("received_at"),
        "raw_s3_bucket":
            payload.get("raw_s3_bucket"),
        "raw_s3_key":
            payload.get("raw_s3_key"),
        "request_source":
            payload.get("request_source"),
        "trigger_hazard_version_key":
            payload.get(
                "trigger_hazard_version_key"
            ),
        "candidate_context":
            payload.get(
                "candidate_context"
            ) or {},
    }

    if payload.get("type") == "Feature":
        return payload, metadata

    for key in (
        "feature",
        "metar",
        "record",
        "data",
    ):
        value = payload.get(key)

        if (
            isinstance(value, dict)
            and value.get("type")
            == "Feature"
        ):
            return value, metadata

    if isinstance(
        payload.get("properties"),
        dict,
    ):
        return (
            {
                "type": "Feature",
                "properties":
                    payload["properties"],
                "geometry":
                    payload.get(
                        "geometry"
                    ),
            },
            metadata,
        )

    raise PermanentRecordError(
        "could not find "
        "METAR GeoJSON feature"
    )


def normalize_clouds(
    value: Any,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    clouds: list[
        dict[str, Any]
    ] = []

    for cloud in value[:20]:
        if not isinstance(
            cloud,
            dict,
        ):
            continue

        normalized = {
            "cover": first_present(
                cloud,
                [
                    "cover",
                    "coverage",
                    "sky_cover",
                ],
            ),
            "base_ft": first_present(
                cloud,
                [
                    "base",
                    "base_ft",
                    "baseFeet",
                ],
            ),
        }

        normalized = {
            key: item
            for key, item
            in normalized.items()
            if item is not None
        }

        if normalized:
            clouds.append(normalized)

    return clouds


def derive_ceiling_ft(
    clouds: list[
        dict[str, Any]
    ],
) -> Any:
    ceiling_bases = []

    for cloud in clouds:
        cover = str(
            cloud.get("cover") or ""
        ).upper()

        base_ft = cloud.get(
            "base_ft"
        )

        if (
            cover not in {
                "BKN",
                "OVC",
                "VV",
            }
            or base_ft is None
        ):
            continue

        try:
            ceiling_bases.append(
                float(base_ft)
            )
        except (
            TypeError,
            ValueError,
        ):
            continue

    if not ceiling_bases:
        return None

    lowest = min(ceiling_bases)

    return (
        int(lowest)
        if lowest.is_integer()
        else lowest
    )


def normalize_weather_codes(
    weather_string: Any,
) -> list[str]:
    if weather_string in (
        None,
        "",
    ):
        return []

    return [
        token.strip().upper()
        for token
        in str(
            weather_string
        ).split()
        if token.strip()
    ][:20]


def calculate_freshness_status(
    observed_time: datetime,
    processed_time: datetime,
) -> str:
    age_seconds = max(
        0,
        int(
            (
                processed_time
                - observed_time
            ).total_seconds()
        ),
    )

    if age_seconds <= (
        METAR_FRESH_SECONDS
    ):
        return "FRESH"

    if age_seconds <= (
        METAR_ACCEPTABLE_SECONDS
    ):
        return "ACCEPTABLE"

    return "STALE"


def build_metar_version(
    *,
    station_id: str,
    observed_time_epoch: int,
    metar_type: Any,
    raw_text: Any,
) -> str:
    version_fields = {
        "station_id":
            station_id,
        "observed_time_epoch":
            observed_time_epoch,
        "metar_type":
            metar_type,
        "raw_text":
            raw_text,
    }

    encoded = json.dumps(
        version_fields,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")

    digest = hashlib.sha256(
        encoded
    ).hexdigest()[:20]

    return (
        f"metar-"
        f"{observed_time_epoch}-"
        f"{digest}"
    )


def normalize_feature(
    feature: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    properties = feature.get(
        "properties"
    )

    if not isinstance(
        properties,
        dict,
    ):
        raise PermanentRecordError(
            "METAR feature "
            "missing properties"
        )

    station_id = first_present(
        properties,
        [
            "icaoId",
            "station_id",
            "stationId",
            "id",
            "site",
        ],
    )

    if not station_id:
        raise PermanentRecordError(
            "METAR record "
            "missing station id"
        )

    station_id = str(
        station_id
    ).upper()

    observed_time = parse_time(
        first_present(
            properties,
            [
                "obsTime",
                "observed_time",
                "observed_time_utc",
                "reportTime",
            ],
        )
    )

    received_at_raw = (
        metadata.get("received_at")
    )

    received_at = (
        parse_time(received_at_raw)
        if received_at_raw
        else utc_now()
    )

    processed_at = utc_now()

    receipt_time = (
        parse_optional_time(
            first_present(
                properties,
                [
                    "receiptTime",
                    "receipt_time",
                    "receipt_time_utc",
                ],
            )
        )
    )

    geometry = (
        feature.get("geometry") or {}
    )

    coordinates = (
        geometry.get("coordinates")
        if isinstance(
            geometry,
            dict,
        )
        else None
    )

    longitude = None
    latitude = None

    if (
        isinstance(
            coordinates,
            list,
        )
        and len(coordinates) >= 2
    ):
        longitude = coordinates[0]
        latitude = coordinates[1]

    candidate_context = (
        metadata.get(
            "candidate_context"
        )
    )

    if not isinstance(
        candidate_context,
        dict,
    ):
        candidate_context = {}

    airport_id = (
        candidate_context.get(
            "airport_id"
        )
    )

    if airport_id:
        airport_id = str(
            airport_id
        ).upper()

    clouds = normalize_clouds(
        first_present(
            properties,
            ["clouds", "sky"],
        )
    )

    weather_string = first_present(
        properties,
        [
            "wxString",
            "weather",
            "weather_string",
        ],
    )

    metar_type = first_present(
        properties,
        [
            "metarType",
            "metar_type",
            "reportType",
        ],
    )

    raw_text = first_present(
        properties,
        [
            "rawOb",
            "raw_text",
            "raw",
        ],
    )

    observed_time_epoch = int(
        observed_time.timestamp()
    )

    metar_version = (
        build_metar_version(
            station_id=station_id,
            observed_time_epoch=
                observed_time_epoch,
            metar_type=metar_type,
            raw_text=raw_text,
        )
    )

    raw_s3_bucket = metadata.get(
        "raw_s3_bucket"
    )

    raw_s3_key = metadata.get(
        "raw_s3_key"
    )

    raw_s3_uri = None

    if (
        raw_s3_bucket
        and raw_s3_key
    ):
        raw_s3_uri = (
            f"s3://{raw_s3_bucket}/"
            f"{raw_s3_key}"
        )

    correlation_id = str(
        metadata.get(
            "correlation_id"
        )
        or metadata.get("poll_id")
        or uuid.uuid4()
    )

    item = {
        "station_id":
            station_id,
        "airport_id":
            airport_id,
        "station_name":
            first_present(
                properties,
                [
                    "name",
                    "siteName",
                    "station_name",
                ],
            ),
        "metar_version":
            metar_version,
        "observed_time_epoch":
            observed_time_epoch,
        "observed_time_utc":
            iso_utc(observed_time),
        "receipt_time_utc":
            (
                iso_utc(receipt_time)
                if receipt_time
                is not None
                else None
            ),
        "temperature_c":
            first_present(
                properties,
                [
                    "temp",
                    "temperature_c",
                ],
            ),
        "dewpoint_c":
            first_present(
                properties,
                [
                    "dewp",
                    "dewpoint_c",
                ],
            ),
        "wind_direction_deg":
            first_present(
                properties,
                [
                    "wdir",
                    "wind_direction_deg",
                ],
            ),
        "wind_speed_kt":
            first_present(
                properties,
                [
                    "wspd",
                    "wind_speed_kt",
                ],
            ),
        "wind_gust_kt":
            first_present(
                properties,
                [
                    "wgst",
                    "wind_gust_kt",
                ],
            ),
        "visibility_sm":
            first_present(
                properties,
                [
                    "visib",
                    "visibility_sm",
                ],
            ),
        "ceiling_ft":
            derive_ceiling_ft(
                clouds
            ),
        "altimeter_hpa":
            first_present(
                properties,
                [
                    "altim",
                    "altimeter_hpa",
                ],
            ),
        "sea_level_pressure_hpa":
            first_present(
                properties,
                [
                    "slp",
                    "sea_level_pressure_hpa",
                ],
            ),
        "weather_string":
            weather_string,
        "weather_codes":
            normalize_weather_codes(
                weather_string
            ),
        "precipitation_in":
            first_present(
                properties,
                [
                    "precip",
                    "precipitation_in",
                ],
            ),
        "flight_category":
            first_present(
                properties,
                [
                    "fltCat",
                    "flight_category",
                ],
            ),
        "metar_type":
            metar_type,
        "latitude":
            latitude,
        "longitude":
            longitude,
        "elevation_m":
            first_present(
                properties,
                [
                    "elev",
                    "elevation_m",
                ],
            ),
        "clouds":
            clouds,
        "raw_text":
            raw_text,
        "freshness_status":
            calculate_freshness_status(
                observed_time,
                processed_at,
            ),
        "source_system":
            "NOAA_AVIATIONWEATHER_METAR",
        "source_event_time_utc":
            iso_utc(observed_time),
        "received_at_utc":
            iso_utc(received_at),
        "processed_at_utc":
            iso_utc(processed_at),
        "correlation_id":
            correlation_id,
        "raw_s3_uri":
            raw_s3_uri,
        "schema_version":
            SCHEMA_VERSION,
    }

    if METAR_TTL_SECONDS > 0:
        item[
            "expires_at_epoch"
        ] = int(
            (
                processed_at
                + timedelta(
                    seconds=
                        METAR_TTL_SECONDS
                )
            ).timestamp()
        )

    return {
        key: value
        for key, value
        in item.items()
        if value is not None
    }


def existing_observed_epoch(
    old_item: dict[str, Any],
) -> int:
    value = old_item.get(
        "observed_time_epoch"
    )

    # Temporary compatibility with
    # any pre-v4 dev rows.
    if value is None:
        value = old_item.get(
            "observed_at_epoch",
            0,
        )

    try:
        return int(value)
    except (
        TypeError,
        ValueError,
    ):
        return 0


def existing_metar_version(
    old_item: dict[str, Any],
) -> str | None:
    value = old_item.get(
        "metar_version"
    )

    # Temporary compatibility with
    # pre-v4 dev rows.
    if value is None:
        value = old_item.get(
            "source_version"
        )

    return (
        str(value)
        if value is not None
        else None
    )


def classify_change(
    new_item: dict[str, Any],
    old_item: dict[
        str,
        Any,
    ] | None,
) -> str:
    if not old_item:
        return "NEW"

    old_epoch = (
        existing_observed_epoch(
            old_item
        )
    )

    new_epoch = int(
        new_item[
            "observed_time_epoch"
        ]
    )

    if new_epoch > old_epoch:
        return "UPDATED"

    if new_epoch < old_epoch:
        return "STALE"

    if (
        existing_metar_version(
            old_item
        )
        != new_item["metar_version"]
    ):
        return "CORRECTED"

    return "UNCHANGED"


def write_latest(
    item: dict[str, Any],
    change_type: str,
) -> bool:
    if change_type in {
        "UNCHANGED",
        "STALE",
    }:
        return False

    item_to_write = dict(item)

    item_to_write[
        "change_type"
    ] = change_type

    item_to_write[
        "updated_at_utc"
    ] = iso_utc(utc_now())

    # Allows an EventBridge failure to
    # be recovered when the Kinesis
    # record is retried.
    item_to_write[
        "event_publish_pending"
    ] = True

    condition = (
        "attribute_not_exists(station_id) "
        "OR attribute_not_exists("
        "observed_time_epoch) "
        "OR observed_time_epoch "
        "< :observed_time_epoch "
        "OR ("
        "observed_time_epoch "
        "= :observed_time_epoch "
        "AND ("
        "attribute_not_exists("
        "metar_version) "
        "OR metar_version "
        "<> :metar_version"
        ")"
        ")"
    )

    try:
        table.put_item(
            Item=to_decimal(
                item_to_write
            ),
            ConditionExpression=
                condition,
            ExpressionAttributeValues={
                ":observed_time_epoch":
                    Decimal(
                        str(
                            item[
                                "observed_time_epoch"
                            ]
                        )
                    ),
                ":metar_version":
                    item[
                        "metar_version"
                    ],
            },
        )

        return True

    except ClientError as exc:
        if (
            exc.response.get(
                "Error",
                {},
            ).get("Code")
            == "ConditionalCheckFailedException"
        ):
            return False

        raise


def get_metar_updated_event_context(
    new_item: dict[str, Any],
    existing_item: dict[
        str,
        Any,
    ] | None,
    change_type: str,
    wrote: bool,
) -> tuple[
    bool,
    dict[str, Any] | None,
    str | None,
]:
    if (
        wrote
        and change_type
        in EVENT_CHANGE_TYPES
    ):
        event_item = dict(new_item)
        event_item[
            "change_type"
        ] = change_type

        return (
            True,
            event_item,
            change_type,
        )

    if (
        existing_item
        and existing_item.get(
            "event_publish_pending"
        ) is True
        and existing_metar_version(
            existing_item
        )
        == new_item.get(
            "metar_version"
        )
    ):
        return (
            True,
            existing_item,
            str(
                existing_item.get(
                    "change_type"
                )
                or "UPDATED"
            ),
        )

    return (
        False,
        None,
        None,
    )


def build_event_id(
    item: dict[str, Any],
) -> str:
    seed = (
        "metar.updated|"
        f"{item['station_id']}|"
        f"{item['metar_version']}"
    )

    return hashlib.sha256(
        seed.encode("utf-8")
    ).hexdigest()


def publish_metar_updated_event(
    item: dict[str, Any],
    change_type: str,
) -> None:
    event_time = iso_utc(
        utc_now()
    )

    detail = {
        "event_id":
            build_event_id(item),
        "event_type":
            "metar.updated",
        "event_time_utc":
            event_time,
        "correlation_id":
            item["correlation_id"],
        "schema_version":
            EVENT_SCHEMA_VERSION,
        "entity_id":
            item["station_id"],
        "entity_version":
            item["metar_version"],
        "source_table":
            TABLE_NAME,
        "reason":
            change_type,
        "changed_fields": [
            "metar_version",
            "observed_time_epoch",
            "freshness_status",
        ],
        "station_id":
            item["station_id"],
        "airport_id":
            item.get("airport_id"),
        "metar_version":
            item["metar_version"],
        "observed_time_epoch":
            item[
                "observed_time_epoch"
            ],
        "observed_time_utc":
            item[
                "observed_time_utc"
            ],
        "freshness_status":
            item[
                "freshness_status"
            ],
    }

    detail = {
        key: value
        for key, value
        in detail.items()
        if value is not None
    }

    response = events.put_events(
        Entries=[
            {
                "Source":
                    "wilvor.weather",
                "DetailType":
                    "metar.updated",
                "EventBusName":
                    EVENT_BUS_NAME,
                "Detail":
                    json.dumps(
                        json_safe(detail),
                        separators=(
                            ",",
                            ":",
                        ),
                    ),
            }
        ]
    )

    if (
        response.get(
            "FailedEntryCount",
            0,
        ) > 0
    ):
        raise RuntimeError(
            "EventBridge PutEvents "
            f"failed: {response}"
        )

    print(
        json.dumps(
            {
                "message":
                    "metar.updated "
                    "event published",
                "station_id":
                    item[
                        "station_id"
                    ],
                "metar_version":
                    item[
                        "metar_version"
                    ],
                "change_type":
                    change_type,
            }
        )
    )


def mark_metar_updated_event_published(
    station_id: str,
    metar_version: str,
) -> bool:
    try:
        table.update_item(
            Key={
                "station_id":
                    station_id,
            },
            UpdateExpression=(
                "SET "
                "last_event_published_"
                "metar_version "
                "= :metar_version, "
                "last_event_published_"
                "at_utc "
                "= :published_at "
                "REMOVE "
                "event_publish_pending"
            ),
            ConditionExpression=(
                "metar_version "
                "= :metar_version"
            ),
            ExpressionAttributeValues={
                ":metar_version":
                    metar_version,
                ":published_at":
                    iso_utc(
                        utc_now()
                    ),
            },
        )

        return True

    except ClientError as exc:
        if (
            exc.response.get(
                "Error",
                {},
            ).get("Code")
            == "ConditionalCheckFailedException"
        ):
            return False

        raise


def archive_bad_record(
    record: dict[str, Any],
    error_message: str,
    payload: dict[
        str,
        Any,
    ] | None = None,
) -> None:
    now = utc_now()

    key = (
        f"{BAD_RECORDS_PREFIX}/"
        f"year={now:%Y}/"
        f"month={now:%m}/"
        f"day={now:%d}/"
        f"hour={now:%H}/"
        f"bad-record-"
        f"{uuid.uuid4()}.json"
    )

    body = {
        "error":
            error_message,
        "archived_at_utc":
            iso_utc(now),
        "event_source":
            "metar_processor",
        "sequence_number":
            record.get(
                "kinesis",
                {},
            ).get(
                "sequenceNumber"
            ),
        "payload":
            payload,
    }

    s3.put_object(
        Bucket=
            BAD_RECORDS_BUCKET,
        Key=key,
        Body=json.dumps(
            json_safe(body),
            default=str,
        ).encode("utf-8"),
        ContentType=
            "application/json",
    )


def emit_metrics(
    metrics: dict[str, int],
) -> None:
    print(
        json.dumps(
            {
                "_aws": {
                    "Timestamp":
                        int(
                            utc_now()
                            .timestamp()
                            * 1000
                        ),
                    "CloudWatchMetrics": [
                        {
                            "Namespace":
                                "Wilvor/Pipeline",
                            "Dimensions": [
                                [
                                    "Environment",
                                    "Pipeline",
                                    "Component",
                                    "Stage",
                                ]
                            ],
                            "Metrics": [
                                {
                                    "Name":
                                        name,
                                    "Unit":
                                        "Count",
                                }
                                for name
                                in metrics.keys()
                            ],
                        }
                    ],
                },
                "Environment":
                    ENVIRONMENT,
                "Pipeline":
                    "metar",
                "Component":
                    "metar_processor",
                "Stage":
                    "latest_state",
                **metrics,
            }
        )
    )


def process_record(
    record: dict[str, Any],
) -> dict[str, Any]:
    payload = decode_kinesis_record(
        record
    )

    feature, metadata = (
        extract_feature(payload)
    )

    item = normalize_feature(
        feature,
        metadata,
    )

    existing = table.get_item(
        Key={
            "station_id":
                item["station_id"]
        }
    ).get("Item")

    change_type = classify_change(
        item,
        existing,
    )

    wrote = write_latest(
        item,
        change_type,
    )

    (
        should_publish_event,
        event_item,
        event_change_type,
    ) = get_metar_updated_event_context(
        new_item=item,
        existing_item=existing,
        change_type=change_type,
        wrote=wrote,
    )

    event_published = False

    if (
        should_publish_event
        and event_item
        and event_change_type
    ):
        publish_metar_updated_event(
            event_item,
            event_change_type,
        )

        mark_metar_updated_event_published(
            station_id=
                event_item[
                    "station_id"
                ],
            metar_version=
                str(
                    event_item[
                        "metar_version"
                    ]
                ),
        )

        event_published = True

    result = {
        "station_id":
            item["station_id"],
        "airport_id":
            item.get("airport_id"),
        "observed_time_utc":
            item[
                "observed_time_utc"
            ],
        "metar_version":
            item[
                "metar_version"
            ],
        "change_type":
            change_type,
        "wrote":
            wrote,
        "event_published":
            event_published,
    }

    print(
        json.dumps(
            {
                "message":
                    "METAR record "
                    "processed",
                **result,
            }
        )
    )

    return result


def lambda_handler(event, context):
    records = event.get(
        "Records",
        [],
    )

    metrics = {
        "RecordsReceived":
            len(records),
        "RecordsNew":
            0,
        "RecordsUpdated":
            0,
        "RecordsCorrected":
            0,
        "RecordsUnchanged":
            0,
        "RecordsStale":
            0,
        "DynamoDBWrites":
            0,
        "MetarUpdatedEventsPublished":
            0,
        "BadRecordsWritten":
            0,
        "ProcessingFailures":
            0,
    }

    batch_item_failures = []

    for record in records:
        sequence_number = (
            record.get(
                "kinesis",
                {},
            ).get(
                "sequenceNumber"
            )
        )

        try:
            result = process_record(
                record
            )

            change_type = result[
                "change_type"
            ]

            if change_type == "NEW":
                metrics[
                    "RecordsNew"
                ] += 1

            elif change_type == "UPDATED":
                metrics[
                    "RecordsUpdated"
                ] += 1

            elif (
                change_type
                == "CORRECTED"
            ):
                metrics[
                    "RecordsCorrected"
                ] += 1

            elif (
                change_type
                == "UNCHANGED"
            ):
                metrics[
                    "RecordsUnchanged"
                ] += 1

            elif change_type == "STALE":
                metrics[
                    "RecordsStale"
                ] += 1

            if result["wrote"]:
                metrics[
                    "DynamoDBWrites"
                ] += 1

            if result.get(
                "event_published"
            ):
                metrics[
                    "MetarUpdatedEventsPublished"
                ] += 1

        except PermanentRecordError as exc:
            metrics[
                "BadRecordsWritten"
            ] += 1

            metrics[
                "ProcessingFailures"
            ] += 1

            try:
                payload = None

                try:
                    payload = (
                        decode_kinesis_record(
                            record
                        )
                    )
                except Exception:
                    payload = None

                archive_bad_record(
                    record,
                    str(exc),
                    payload,
                )

            except Exception as archive_exc:
                print(
                    json.dumps(
                        {
                            "message":
                                "failed to archive "
                                "bad METAR record",
                            "error":
                                str(
                                    archive_exc
                                ),
                        }
                    )
                )

        except Exception as exc:
            metrics[
                "ProcessingFailures"
            ] += 1

            print(
                json.dumps(
                    {
                        "message":
                            "temporary METAR "
                            "processor failure",
                        "error":
                            str(exc),
                        "sequence_number":
                            sequence_number,
                    }
                )
            )

            if sequence_number:
                batch_item_failures.append(
                    {
                        "itemIdentifier":
                            sequence_number
                    }
                )

    metrics[
        "BatchItemFailures"
    ] = len(
        batch_item_failures
    )

    emit_metrics(metrics)

    return {
        "batchItemFailures":
            batch_item_failures
    }