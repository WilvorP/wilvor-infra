import base64
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError


dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
events = boto3.client("events")

TABLE_NAME = os.environ["METAR_LATEST_TABLE_NAME"]
BAD_RECORDS_BUCKET = os.environ["BAD_RECORDS_BUCKET_NAME"]
BAD_RECORDS_PREFIX = os.environ.get(
    "BAD_RECORDS_PREFIX",
    "bad-records/source=metar_processor",
)
SCHEMA_VERSION = os.environ.get("SCHEMA_VERSION", "metar_latest.v1")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")
EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "default")
EVENT_CHANGE_TYPES = {"NEW", "UPDATED", "CORRECTED"}

table = dynamodb.Table(TABLE_NAME)


class PermanentRecordError(Exception):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_time(value: Any) -> datetime:
    if value is None:
        raise PermanentRecordError("missing observation time")

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)

    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            raise PermanentRecordError("empty observation time")

        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"

        try:
            parsed = datetime.fromisoformat(cleaned)
        except ValueError as exc:
            raise PermanentRecordError(f"invalid observation time: {value}") from exc

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)

    raise PermanentRecordError(f"unsupported observation time type: {type(value)}")


def first_present(source: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in source and source[key] not in (None, ""):
            return source[key]
    return None


def to_decimal(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))

    if isinstance(value, dict):
        return {k: to_decimal(v) for k, v in value.items() if v is not None}

    if isinstance(value, list):
        return [to_decimal(v) for v in value if v is not None]

    return value


def decode_kinesis_record(record: dict[str, Any]) -> dict[str, Any]:
    encoded = record["kinesis"]["data"]
    raw_bytes = base64.b64decode(encoded)
    raw_text = raw_bytes.decode("utf-8")

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise PermanentRecordError("invalid JSON in Kinesis record") from exc


def extract_feature(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata = {
        "poll_id": payload.get("poll_id"),
        "received_at": payload.get("received_at"),
        "raw_s3_key": payload.get("raw_s3_key"),
    }

    if payload.get("type") == "Feature":
        return payload, metadata

    for key in ("feature", "metar", "record", "data"):
        value = payload.get(key)
        if isinstance(value, dict) and value.get("type") == "Feature":
            return value, metadata

    if isinstance(payload.get("properties"), dict):
        return {
            "type": "Feature",
            "properties": payload["properties"],
            "geometry": payload.get("geometry"),
        }, metadata

    raise PermanentRecordError("could not find METAR GeoJSON feature")


def normalize_clouds(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []

    if not isinstance(value, list):
        return []

    clouds = []

    for cloud in value:
        if not isinstance(cloud, dict):
            continue

        normalized = {
            "cover": first_present(cloud, ["cover", "coverage", "sky_cover"]),
            "base_ft": first_present(cloud, ["base", "base_ft", "baseFeet"]),
        }

        clouds.append({k: v for k, v in normalized.items() if v is not None})

    return clouds


def build_source_version(item: dict[str, Any]) -> str:
    version_fields = {
        "station_id": item.get("station_id"),
        "observed_at_utc": item.get("observed_at_utc"),
        "raw_text": item.get("raw_text"),
        "temperature_c": item.get("temperature_c"),
        "dewpoint_c": item.get("dewpoint_c"),
        "wind_direction_deg": item.get("wind_direction_deg"),
        "wind_speed_kt": item.get("wind_speed_kt"),
        "wind_gust_kt": item.get("wind_gust_kt"),
        "visibility_sm": item.get("visibility_sm"),
        "altimeter_hpa": item.get("altimeter_hpa"),
        "weather_string": item.get("weather_string"),
        "flight_category": item.get("flight_category"),
        "clouds": item.get("clouds"),
    }

    encoded = json.dumps(
        version_fields,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


def normalize_feature(
    feature: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    properties = feature.get("properties")

    if not isinstance(properties, dict):
        raise PermanentRecordError("METAR feature missing properties")

    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None

    station_id = first_present(
        properties,
        ["icaoId", "station_id", "stationId", "id", "site"],
    )

    if not station_id:
        raise PermanentRecordError("METAR record missing station id")

    station_id = str(station_id).upper()

    observed_at = parse_time(
        first_present(
            properties,
            ["obsTime", "observed_at", "observed_at_utc", "reportTime"],
        )
    )

    received_at_raw = metadata.get("received_at")
    received_at = parse_time(received_at_raw) if received_at_raw else utc_now()

    latitude = None
    longitude = None

    if isinstance(coordinates, list) and len(coordinates) >= 2:
        longitude = coordinates[0]
        latitude = coordinates[1]

    item = {
        "station_id": station_id,
        "station_name": first_present(properties, ["name", "siteName"]),
        "observed_at_utc": iso_utc(observed_at),
        "observed_at_epoch": int(observed_at.timestamp()),
        "received_at_utc": iso_utc(received_at),
        "processed_at_utc": iso_utc(utc_now()),
        "data_freshness_seconds": int((received_at - observed_at).total_seconds()),
        "temperature_c": first_present(properties, ["temp", "temperature_c"]),
        "dewpoint_c": first_present(properties, ["dewp", "dewpoint_c"]),
        "wind_direction_deg": first_present(properties, ["wdir", "wind_direction_deg"]),
        "wind_speed_kt": first_present(properties, ["wspd", "wind_speed_kt"]),
        "wind_gust_kt": first_present(properties, ["wgst", "wind_gust_kt"]),
        "visibility_sm": first_present(properties, ["visib", "visibility_sm"]),
        "altimeter_hpa": first_present(properties, ["altim", "altimeter_hpa"]),
        "weather_string": first_present(properties, ["wxString", "weather"]),
        "flight_category": first_present(properties, ["fltCat", "flight_category"]),
        "clouds": normalize_clouds(first_present(properties, ["clouds", "sky"])),
        "latitude": latitude,
        "longitude": longitude,
        "raw_text": first_present(properties, ["rawOb", "raw_text", "raw"]),
        "source_system": "NOAA_AviationWeather_METAR",
        "schema_version": SCHEMA_VERSION,
        "poll_id": metadata.get("poll_id"),
        "raw_s3_key": metadata.get("raw_s3_key"),
    }

    item = {k: v for k, v in item.items() if v is not None}
    item["source_version"] = build_source_version(item)

    return item


def classify_change(new_item: dict[str, Any], old_item: dict[str, Any] | None) -> str:
    if not old_item:
        return "NEW"

    old_epoch = int(old_item.get("observed_at_epoch", 0))
    new_epoch = int(new_item["observed_at_epoch"])

    if new_epoch > old_epoch:
        return "UPDATED"

    if new_epoch < old_epoch:
        return "STALE"

    old_version = old_item.get("source_version")
    new_version = new_item.get("source_version")

    if old_version != new_version:
        return "CORRECTED"

    return "UNCHANGED"


def write_latest(item: dict[str, Any], change_type: str) -> bool:
    if change_type in ("UNCHANGED", "STALE"):
        return False

    item["change_type"] = change_type
    item["updated_at_utc"] = iso_utc(utc_now())

    if change_type in EVENT_CHANGE_TYPES:
        item["event_publish_pending"] = True

    condition = (
        "attribute_not_exists(station_id) "
        "OR observed_at_epoch < :observed_at_epoch "
        "OR (observed_at_epoch = :observed_at_epoch AND source_version <> :source_version)"
    )

    try:
        table.put_item(
            Item=to_decimal(item),
            ConditionExpression=condition,
            ExpressionAttributeValues={
                ":observed_at_epoch": Decimal(str(item["observed_at_epoch"])),
                ":source_version": item["source_version"],
            },
        )
        return True

    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise

def get_weather_changed_event_context(
    new_item: dict[str, Any],
    existing_item: dict[str, Any] | None,
    change_type: str,
    wrote: bool,
) -> tuple[bool, dict[str, Any] | None, str | None]:
    if wrote and change_type in EVENT_CHANGE_TYPES:
        return True, new_item, change_type

    if (
        existing_item
        and existing_item.get("event_publish_pending") is True
        and existing_item.get("source_version") == new_item.get("source_version")
    ):
        return True, existing_item, existing_item.get("change_type", "UPDATED")

    return False, None, None


def publish_weather_changed_event(
    item: dict[str, Any],
    change_type: str,
) -> None:
    detail = {
        "event_type": "weather.changed",
        "product_type": "METAR",
        "station_id": item["station_id"],
        "change_type": change_type,
        "observed_at_utc": item.get("observed_at_utc"),
        "source_version": item.get("source_version"),
        "schema_version": item.get("schema_version"),
        "table_name": TABLE_NAME,
        "flight_category": item.get("flight_category"),
        "raw_s3_key": item.get("raw_s3_key"),
        "poll_id": item.get("poll_id"),
        "correlation_id": item.get("poll_id"),
        "source_system": item.get("source_system"),
        "updated_at_utc": item.get("updated_at_utc"),
    }

    detail = {k: v for k, v in detail.items() if v is not None}

    response = events.put_events(
        Entries=[
            {
                "Source": "wilvor.weather",
                "DetailType": "Weather.changed",
                "EventBusName": EVENT_BUS_NAME,
                "Detail": json.dumps(detail, default=str),
            }
        ]
    )

    if response.get("FailedEntryCount", 0) > 0:
        raise RuntimeError(f"EventBridge PutEvents failed: {response}")

    print(
        json.dumps(
            {
                "message": "Weather.changed event published",
                "product_type": "METAR",
                "station_id": item["station_id"],
                "change_type": change_type,
                "source_version": item.get("source_version"),
            }
        )
    )


def mark_weather_changed_event_published(
    station_id: str,
    source_version: str,
) -> None:
    table.update_item(
        Key={"station_id": station_id},
        UpdateExpression=(
            "SET last_event_published_source_version = :source_version, "
            "last_event_published_at_utc = :published_at "
            "REMOVE event_publish_pending"
        ),
        ConditionExpression="source_version = :source_version",
        ExpressionAttributeValues={
            ":source_version": source_version,
            ":published_at": iso_utc(utc_now()),
        },
    )


def archive_bad_record(
    record: dict[str, Any],
    error_message: str,
    payload: dict[str, Any] | None = None,
) -> None:
    now = utc_now()

    key = (
        f"{BAD_RECORDS_PREFIX}/"
        f"year={now:%Y}/month={now:%m}/day={now:%d}/hour={now:%H}/"
        f"bad-record-{uuid.uuid4()}.json"
    )

    body = {
        "error": error_message,
        "archived_at_utc": iso_utc(now),
        "event_source": "metar_processor",
        "sequence_number": record.get("kinesis", {}).get("sequenceNumber"),
        "payload": payload,
    }

    s3.put_object(
        Bucket=BAD_RECORDS_BUCKET,
        Key=key,
        Body=json.dumps(body, default=str).encode("utf-8"),
        ContentType="application/json",
    )


def emit_metrics(metrics: dict[str, int]) -> None:
    print(
        json.dumps(
            {
                "_aws": {
                    "Timestamp": int(utc_now().timestamp() * 1000),
                    "CloudWatchMetrics": [
                        {
                            "Namespace": "Wilvor/Pipeline",
                            "Dimensions": [
                                ["Environment", "Pipeline", "Component", "Stage"]
                            ],
                            "Metrics": [
                                {"Name": name, "Unit": "Count"}
                                for name in metrics.keys()
                            ],
                        }
                    ],
                },
                "Environment": ENVIRONMENT,
                "Pipeline": "metar",
                "Component": "metar_processor",
                "Stage": "latest_state",
                **metrics,
            }
        )
    )


def process_record(record: dict[str, Any]) -> dict[str, Any]:
    payload = decode_kinesis_record(record)
    feature, metadata = extract_feature(payload)
    item = normalize_feature(feature, metadata)

    existing = table.get_item(Key={"station_id": item["station_id"]}).get("Item")
    change_type = classify_change(item, existing)
    wrote = write_latest(item, change_type)

    should_publish_event, event_item, event_change_type = (
        get_weather_changed_event_context(
            new_item=item,
            existing_item=existing,
            change_type=change_type,
            wrote=wrote,
        )
    )

    event_published = False

    if should_publish_event and event_item and event_change_type:
        publish_weather_changed_event(event_item, event_change_type)
        mark_weather_changed_event_published(
            station_id=event_item["station_id"],
            source_version=event_item["source_version"],
        )
        event_published = True

    result = {
        "station_id": item["station_id"],
        "observed_at_utc": item["observed_at_utc"],
        "change_type": change_type,
        "wrote": wrote,
        "event_published": event_published,
    }

    print(json.dumps({"message": "METAR record processed", **result}))

    return result


def lambda_handler(event, context):
    records = event.get("Records", [])

    metrics = {
        "RecordsReceived": len(records),
        "RecordsNew": 0,
        "RecordsUpdated": 0,
        "RecordsCorrected": 0,
        "RecordsUnchanged": 0,
        "RecordsStale": 0,
        "DynamoDBWrites": 0,
        "WeatherChangedEventsPublished": 0,
        "BadRecordsWritten": 0,
        "ProcessingFailures": 0,
    }

    batch_item_failures = []

    for record in records:
        sequence_number = record.get("kinesis", {}).get("sequenceNumber")

        try:
            result = process_record(record)

            change_type = result["change_type"]

            if change_type == "NEW":
                metrics["RecordsNew"] += 1
            elif change_type == "UPDATED":
                metrics["RecordsUpdated"] += 1
            elif change_type == "CORRECTED":
                metrics["RecordsCorrected"] += 1
            elif change_type == "UNCHANGED":
                metrics["RecordsUnchanged"] += 1
            elif change_type == "STALE":
                metrics["RecordsStale"] += 1

            if result["wrote"]:
                metrics["DynamoDBWrites"] += 1
                
            if result.get("event_published"):
                metrics["WeatherChangedEventsPublished"] += 1

        except PermanentRecordError as exc:
            metrics["BadRecordsWritten"] += 1
            metrics["ProcessingFailures"] += 1

            try:
                payload = None
                try:
                    payload = decode_kinesis_record(record)
                except Exception:
                    payload = None

                archive_bad_record(record, str(exc), payload)

            except Exception as archive_exc:
                print(
                    json.dumps(
                        {
                            "message": "failed to archive bad METAR record",
                            "error": str(archive_exc),
                        }
                    )
                )

        except Exception as exc:
            metrics["ProcessingFailures"] += 1

            print(
                json.dumps(
                    {
                        "message": "temporary METAR processor failure",
                        "error": str(exc),
                        "sequence_number": sequence_number,
                    }
                )
            )

            if sequence_number:
                batch_item_failures.append({"itemIdentifier": sequence_number})

    metrics["BatchItemFailures"] = len(batch_item_failures)
    emit_metrics(metrics)

    return {"batchItemFailures": batch_item_failures}