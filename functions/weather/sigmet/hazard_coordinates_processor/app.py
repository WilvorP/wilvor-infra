import base64
import binascii
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
events = boto3.client("events")
s3 = boto3.client("s3")

HAZARD_COORDINATES_TABLE_NAME = os.environ["HAZARD_COORDINATES_TABLE_NAME"]
SCHEMA_VERSION = os.environ.get("SCHEMA_VERSION", "wilvor.hazard_coordinates.v4.0")
EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "default")
RETENTION_AFTER_VALID_TO_HOURS = int(os.environ.get("RETENTION_AFTER_VALID_TO_HOURS", "6"))

BAD_RECORDS_BUCKET_NAME = os.environ.get("BAD_RECORDS_BUCKET_NAME")
BAD_RECORDS_PREFIX = os.environ.get(
    "BAD_RECORDS_PREFIX",
    "bad-records/source=sigmet_hazard_coordinates_processor",
)

hazard_coordinates_table = dynamodb.Table(HAZARD_COORDINATES_TABLE_NAME)


class PermanentRecordError(Exception):
    """A validation error that will not be fixed by retrying the same Kinesis record."""


def json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, set):
        return sorted(value)
    return str(value)


def log_event(message: str, **kwargs: Any) -> None:
    logger.info(json.dumps({"message": message, **kwargs}, default=json_default))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_utc_iso() -> str:
    return now_utc().isoformat()


def stable_hash(value: Any) -> str:
    serialized = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=json_default,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def parse_time(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value > 10_000_000_000:
            value = value / 1000
        return datetime.fromtimestamp(value, tz=timezone.utc)

    if not isinstance(value, str):
        return None

    cleaned = value.strip()
    if not cleaned:
        return None

    if cleaned.isdigit():
        return parse_time(int(cleaned))

    try:
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"

        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def iso_or_none(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def clean_string(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, (dict, list)):
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=json_default)
    else:
        text = str(value)

    text = text.strip()
    return text if text else None


def canonical_time_or_string(value: Any) -> str | None:
    parsed = parse_time(value)
    if parsed:
        return parsed.isoformat()
    return clean_string(value)


def get_first_property(properties: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = properties.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def decode_kinesis_record(record: dict[str, Any]) -> dict[str, Any]:
    try:
        encoded_data = record["kinesis"]["data"]
    except KeyError as exc:
        raise PermanentRecordError("Kinesis record is missing kinesis.data") from exc

    try:
        decoded = base64.b64decode(encoded_data).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise PermanentRecordError("Kinesis record data is not valid base64 UTF-8") from exc

    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise PermanentRecordError("Kinesis record data is not valid JSON") from exc

    if not isinstance(payload, dict):
        raise PermanentRecordError("Decoded Kinesis payload is not a JSON object")

    return payload


def extract_feature(raw_event: dict[str, Any]) -> dict[str, Any]:
    feature = raw_event.get("feature")
    if not isinstance(feature, dict):
        raise PermanentRecordError("Kinesis payload does not contain a valid GeoJSON feature")

    if feature.get("type") != "Feature":
        raise PermanentRecordError("SIGMET record is not a GeoJSON Feature")

    return feature


def extract_properties(feature: dict[str, Any]) -> dict[str, Any]:
    properties = feature.get("properties") or {}
    if not isinstance(properties, dict):
        raise PermanentRecordError("SIGMET feature properties are missing or invalid")

    return properties


def get_hazard_type(properties: dict[str, Any]) -> str:
    value = get_first_property(
        properties,
        ["hazard", "hazardType", "hazard_type", "phenomenon", "airSigmetType"],
    )

    if value:
        return str(value).strip().upper().replace(" ", "_")

    raw_text = get_first_property(properties, ["rawAirSigmet", "rawSigmet", "raw_text", "rawText"])
    if raw_text:
        text = str(raw_text).upper()
        for keyword in [
            "CONVECTIVE",
            "THUNDERSTORM",
            "TURB",
            "TURBULENCE",
            "ICE",
            "ICING",
            "IFR",
            "MTN OBSCN",
            "VOLCANIC",
            "ASH",
        ]:
            if keyword in text:
                return keyword.replace(" ", "_")

    return "UNKNOWN"


def extract_source_identity(properties: dict[str, Any]) -> dict[str, str | None]:
    identity = {
        "source_icao_id": clean_string(properties.get("icaoId")),
        "air_sigmet_type": clean_string(properties.get("airSigmetType")),
        "alpha_char": clean_string(properties.get("alphaChar")),
        "series_id": clean_string(properties.get("seriesId")),
        "valid_time_from": canonical_time_or_string(properties.get("validTimeFrom")),
        "valid_time_to": canonical_time_or_string(properties.get("validTimeTo")),
        "hazard_type": get_hazard_type(properties),
    }

    if not any(identity.values()):
        raise PermanentRecordError("SIGMET record has no usable identity fields")

    return identity


def build_hazard_id(properties: dict[str, Any]) -> str:
    """
    Stable hazard identity.

    This intentionally avoids geometry because geometry changes should create
    a new source_version, not a new hazard_id.
    """
    identity = extract_source_identity(properties)
    identity_string = "|".join(
        [
            identity.get("source_icao_id") or "",
            identity.get("air_sigmet_type") or "",
            identity.get("alpha_char") or "",
            identity.get("series_id") or "",
            identity.get("valid_time_from") or "",
            identity.get("valid_time_to") or "",
            identity.get("hazard_type") or "",
        ]
    )

    return f"sigmet-{stable_hash(identity_string)[:24]}"


def build_source_version(feature: dict[str, Any]) -> str:
    properties = extract_properties(feature)

    source_fingerprint = {
        "source_icao_id": properties.get("icaoId"),
        "air_sigmet_type": properties.get("airSigmetType"),
        "alpha_char": properties.get("alphaChar"),
        "series_id": properties.get("seriesId"),
        "creation_time": canonical_time_or_string(properties.get("creationTime")),
        "valid_time_from": canonical_time_or_string(properties.get("validTimeFrom")),
        "valid_time_to": canonical_time_or_string(properties.get("validTimeTo")),
        "raw_air_sigmet": properties.get("rawAirSigmet"),
        "raw_sigmet": properties.get("rawSigmet"),
        "hazard": properties.get("hazard"),
        "severity": properties.get("severity"),
        "altitude_hi_1": properties.get("altitudeHi1"),
        "altitude_low_1": properties.get("altitudeLow1"),
        "altitude_hi_2": properties.get("altitudeHi2"),
        "altitude_low_2": properties.get("altitudeLow2"),
        "movement_dir": properties.get("movementDir"),
        "movement_spd": properties.get("movementSpd"),
        "post_process_flag": properties.get("postProcessFlag"),
        "geometry": feature.get("geometry"),
    }

    return stable_hash(source_fingerprint)[:32]


def get_valid_from(properties: dict[str, Any]) -> datetime | None:
    return parse_time(
        get_first_property(
            properties,
            ["validTimeFrom", "valid_from", "validFrom", "valid_from_time"],
        )
    )


def get_valid_to(properties: dict[str, Any]) -> datetime | None:
    return parse_time(
        get_first_property(
            properties,
            ["validTimeTo", "valid_to", "validTo", "valid_to_time"],
        )
    )


def ttl_from_valid_to(valid_to: datetime | None) -> int:
    if valid_to:
        return int((valid_to + timedelta(hours=RETENTION_AFTER_VALID_TO_HOURS)).timestamp())

    return int((now_utc() + timedelta(hours=RETENTION_AFTER_VALID_TO_HOURS)).timestamp())


def build_raw_s3_uri(raw_event: dict[str, Any]) -> str | None:
    raw_s3_bucket = raw_event.get("raw_s3_bucket")
    raw_s3_key = raw_event.get("raw_s3_key")

    if raw_s3_bucket and raw_s3_key:
        return f"s3://{raw_s3_bucket}/{raw_s3_key}"

    return None


def build_correlation_id(raw_event: dict[str, Any], feature: dict[str, Any]) -> str:
    existing = clean_string(raw_event.get("correlation_id"))
    if existing:
        return existing

    poll_id = clean_string(raw_event.get("poll_id"))
    record_index = raw_event.get("record_index")
    if poll_id is not None:
        return f"{poll_id}:{record_index}"

    return f"sigmet-coordinate-{stable_hash(feature)[:24]}"


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def decimal_number(value: float) -> Decimal:
    return Decimal(str(value))


def normalize_lonlat_point(point: Any) -> tuple[float, float] | None:
    """
    GeoJSON stores coordinates as [longitude, latitude].
    DynamoDB rows store them as latitude and longitude.
    """
    if not isinstance(point, list) or len(point) < 2:
        return None

    lon = point[0]
    lat = point[1]

    if not is_number(lat) or not is_number(lon):
        return None

    lat_float = float(lat)
    lon_float = float(lon)

    if lat_float < -90 or lat_float > 90:
        return None

    if lon_float < -180 or lon_float > 180:
        return None

    return lat_float, lon_float


def normalize_ring(ring: Any, *, allow_hole: bool) -> list[list[float]]:
    if not isinstance(ring, list):
        raise PermanentRecordError("Geometry ring is not a list")

    normalized: list[list[float]] = []
    for point in ring:
        normalized_point = normalize_lonlat_point(point)
        if normalized_point is not None:
            lat, lon = normalized_point
            normalized.append([lat, lon])

    if len(normalized) >= 2 and normalized[0] == normalized[-1]:
        normalized = normalized[:-1]

    if len(normalized) < 3:
        if allow_hole:
            return []
        raise PermanentRecordError("Polygon exterior ring has fewer than three valid points")

    if len({(point[0], point[1]) for point in normalized}) < 3:
        if allow_hole:
            return []
        raise PermanentRecordError("Polygon exterior ring has fewer than three distinct points")

    return normalized


def normalize_polygon(polygon_coordinates: Any) -> list[list[list[float]]]:
    if not isinstance(polygon_coordinates, list) or not polygon_coordinates:
        raise PermanentRecordError("Polygon coordinates are missing or invalid")

    exterior_ring = normalize_ring(polygon_coordinates[0], allow_hole=False)

    rings = [exterior_ring]
    for ring in polygon_coordinates[1:]:
        hole = normalize_ring(ring, allow_hole=True)
        if hole:
            rings.append(hole)

    return rings


def normalize_geometry(geometry: Any) -> dict[str, Any]:
    if not isinstance(geometry, dict):
        raise PermanentRecordError("Geometry is missing or invalid")

    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")

    if geometry_type == "Polygon":
        polygons = [normalize_polygon(coordinates)]
        normalized_type = "POLYGON"

    elif geometry_type == "MultiPolygon":
        if not isinstance(coordinates, list) or not coordinates:
            raise PermanentRecordError("MultiPolygon coordinates are missing or invalid")

        polygons = [normalize_polygon(polygon_coordinates) for polygon_coordinates in coordinates]
        normalized_type = "MULTIPOLYGON"

    else:
        raise PermanentRecordError(f"Unsupported geometry type: {geometry_type}")

    if not polygons:
        raise PermanentRecordError("Geometry produced zero polygons")

    return {
        "type": normalized_type,
        "polygons": polygons,
    }


def build_coordinate_key(
    *,
    polygon_index: int,
    ring_index: int,
    sequence_number: int,
) -> str:
    return f"P#{polygon_index:04d}#R#{ring_index:04d}#S#{sequence_number:06d}"


def build_hazard_coordinate_items(
    raw_event: dict[str, Any],
    feature: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    properties = extract_properties(feature)
    normalized_geometry = normalize_geometry(feature.get("geometry"))

    hazard_id = build_hazard_id(properties)
    source_version = build_source_version(feature)
    hazard_version_key = f"{hazard_id}#{source_version}"

    geometry_hash = stable_hash(normalized_geometry)
    materialization_id = f"hazard-coordinates-{stable_hash({'hazard_version_key': hazard_version_key, 'geometry_hash': geometry_hash})[:24]}"

    created_at_utc = now_utc_iso()
    valid_from = get_valid_from(properties)
    valid_to = get_valid_to(properties)
    expires_at_epoch = ttl_from_valid_to(valid_to)
    correlation_id = build_correlation_id(raw_event, feature)

    items: list[dict[str, Any]] = []

    for polygon_index, polygon in enumerate(normalized_geometry["polygons"]):
        for ring_index, ring in enumerate(polygon):
            for sequence_number, point in enumerate(ring):
                latitude = point[0]
                longitude = point[1]

                items.append(
                    {
                        "hazard_version_key": hazard_version_key,
                        "coordinate_key": build_coordinate_key(
                            polygon_index=polygon_index,
                            ring_index=ring_index,
                            sequence_number=sequence_number,
                        ),
                        "hazard_id": hazard_id,
                        "source_version": source_version,
                        "geometry_type": normalized_geometry["type"],
                        "polygon_index": polygon_index,
                        "ring_index": ring_index,
                        "sequence_number": sequence_number,
                        "latitude": decimal_number(latitude),
                        "longitude": decimal_number(longitude),
                        "materialization_id": materialization_id,
                        "geometry_hash": geometry_hash,
                        "created_at_utc": created_at_utc,
                        "correlation_id": correlation_id,
                        "schema_version": SCHEMA_VERSION,
                        "expires_at_epoch": expires_at_epoch,
                    }
                )

    if not items:
        raise PermanentRecordError("Geometry produced zero coordinate rows")

    summary = {
        "hazard_version_key": hazard_version_key,
        "hazard_id": hazard_id,
        "source_version": source_version,
        "geometry_type": normalized_geometry["type"],
        "geometry_hash": geometry_hash,
        "coordinate_count": len(items),
        "materialization_id": materialization_id,
        "valid_from_utc": iso_or_none(valid_from),
        "valid_to_utc": iso_or_none(valid_to),
        "created_at_utc": created_at_utc,
        "expires_at_epoch": expires_at_epoch,
        "correlation_id": correlation_id,
        "schema_version": SCHEMA_VERSION,
        "raw_s3_uri": build_raw_s3_uri(raw_event),
    }

    return items, summary


def count_existing_hazard_coordinates(hazard_version_key: str) -> int:
    count = 0
    exclusive_start_key = None

    while True:
        query_kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("hazard_version_key").eq(hazard_version_key),
            "Select": "COUNT",
        }

        if exclusive_start_key:
            query_kwargs["ExclusiveStartKey"] = exclusive_start_key

        response = hazard_coordinates_table.query(**query_kwargs)
        count += int(response.get("Count", 0))

        exclusive_start_key = response.get("LastEvaluatedKey")
        if not exclusive_start_key:
            break

    return count


def write_hazard_coordinate_items(items: list[dict[str, Any]]) -> int:
    with hazard_coordinates_table.batch_writer(
        overwrite_by_pkeys=["hazard_version_key", "coordinate_key"]
    ) as batch:
        for item in items:
            batch.put_item(Item=item)

    return len(items)


def publish_hazard_coordinates_materialized(
    summary: dict[str, Any],
    *,
    coordinate_write_status: str,
) -> None:
    detail = {
        "event_type": "hazard.coordinates.materialized",
        "product_type": "SIGMET",
        "hazard_version_key": summary["hazard_version_key"],
        "hazard_id": summary["hazard_id"],
        "source_version": summary["source_version"],
        "geometry_type": summary["geometry_type"],
        "geometry_hash": summary["geometry_hash"],
        "coordinate_count": summary["coordinate_count"],
        "materialization_id": summary["materialization_id"],
        "coordinate_write_status": coordinate_write_status,
        "valid_from_utc": summary.get("valid_from_utc"),
        "valid_to_utc": summary.get("valid_to_utc"),
        "created_at_utc": summary["created_at_utc"],
        "expires_at_epoch": summary["expires_at_epoch"],
        "correlation_id": summary["correlation_id"],
        "schema_version": summary["schema_version"],
        "raw_s3_uri": summary.get("raw_s3_uri"),
    }

    response = events.put_events(
        Entries=[
            {
                "Source": "wilvor.weather",
                "DetailType": "HazardCoordinates.materialized",
                "EventBusName": EVENT_BUS_NAME,
                "Detail": json.dumps(detail, separators=(",", ":"), default=json_default),
            }
        ]
    )

    failed_count = int(response.get("FailedEntryCount", 0))
    if failed_count:
        raise RuntimeError(f"EventBridge PutEvents failed: {response.get('Entries')}")


def process_decoded_record(raw_event: dict[str, Any]) -> dict[str, int]:
    feature = extract_feature(raw_event)

    items, summary = build_hazard_coordinate_items(raw_event, feature)

    existing_count = count_existing_hazard_coordinates(summary["hazard_version_key"])

    coordinate_rows_written = 0
    already_materialized = 0
    coordinate_write_status = "WRITTEN"

    if existing_count == len(items):
        already_materialized = 1
        coordinate_write_status = "ALREADY_EXISTS"
    else:
        coordinate_rows_written = write_hazard_coordinate_items(items)

    publish_hazard_coordinates_materialized(
        summary,
        coordinate_write_status=coordinate_write_status,
    )

    return {
        "coordinate_rows_written": coordinate_rows_written,
        "already_materialized": already_materialized,
        "eventbridge_events_published": 1,
    }


def get_record_sequence_number(record: dict[str, Any]) -> str | None:
    return record.get("kinesis", {}).get("sequenceNumber")


def get_record_arrival_timestamp(record: dict[str, Any]) -> Any:
    return record.get("kinesis", {}).get("approximateArrivalTimestamp")


def get_record_base64(record: dict[str, Any]) -> str | None:
    value = record.get("kinesis", {}).get("data")
    return str(value) if value is not None else None


def write_bad_record(
    *,
    record: dict[str, Any],
    error_type: str,
    error_message: str,
    decoded_payload: dict[str, Any] | None,
    raw_base64: str | None,
) -> str:
    if not BAD_RECORDS_BUCKET_NAME:
        raise RuntimeError("BAD_RECORDS_BUCKET_NAME is not configured")

    received_at_dt = now_utc()
    sequence_number = get_record_sequence_number(record)

    bad_record = {
        "schema_version": "bad_record.v1",
        "service": "sigmet_hazard_coordinates_processor",
        "error_type": error_type,
        "error_message": error_message,
        "sequence_number": sequence_number,
        "approximate_arrival_timestamp": get_record_arrival_timestamp(record),
        "record_received_at": received_at_dt.isoformat(),
        "decoded_payload": decoded_payload,
        "raw_base64": raw_base64 if decoded_payload is None else None,
    }

    sequence_part = sequence_number or stable_hash(raw_base64 or bad_record)[:24]
    key = (
        f"{BAD_RECORDS_PREFIX.rstrip('/')}/"
        f"year={received_at_dt.year:04d}/"
        f"month={received_at_dt.month:02d}/"
        f"day={received_at_dt.day:02d}/"
        f"hour={received_at_dt.hour:02d}/"
        f"{received_at_dt.strftime('%Y%m%dT%H%M%S%f')}-{sequence_part}.json"
    )

    s3.put_object(
        Bucket=BAD_RECORDS_BUCKET_NAME,
        Key=key,
        Body=json.dumps(bad_record, separators=(",", ":"), default=json_default).encode("utf-8"),
        ContentType="application/json",
    )

    return f"s3://{BAD_RECORDS_BUCKET_NAME}/{key}"


def lambda_handler(event, context):
    records = event.get("Records", [])
    records_received = len(records)

    records_processed = 0
    records_failed = 0
    bad_records_written = 0
    coordinate_rows_written = 0
    already_materialized = 0
    eventbridge_events_published = 0

    batch_item_failures = []

    for record in records:
        sequence_number = get_record_sequence_number(record)
        decoded_payload = None
        raw_base64 = get_record_base64(record)

        try:
            decoded_payload = decode_kinesis_record(record)
            result = process_decoded_record(decoded_payload)

            records_processed += 1
            coordinate_rows_written += result["coordinate_rows_written"]
            already_materialized += result["already_materialized"]
            eventbridge_events_published += result["eventbridge_events_published"]

        except PermanentRecordError as exc:
            try:
                bad_record_uri = write_bad_record(
                    record=record,
                    error_type=exc.__class__.__name__,
                    error_message=str(exc),
                    decoded_payload=decoded_payload,
                    raw_base64=raw_base64,
                )
                bad_records_written += 1
                records_processed += 1

                log_event(
                    "Permanent SIGMET HazardCoordinates failure written to S3",
                    error_type=exc.__class__.__name__,
                    sequence_number=sequence_number,
                    bad_record_uri=bad_record_uri,
                )

            except Exception as quarantine_exc:
                records_failed += 1

                log_event(
                    "Failed to write permanent SIGMET HazardCoordinates failure to S3",
                    error_type=quarantine_exc.__class__.__name__,
                    sequence_number=sequence_number,
                    error=str(quarantine_exc),
                )

                if sequence_number:
                    batch_item_failures.append({"itemIdentifier": sequence_number})

        except (ClientError, BotoCoreError, RuntimeError) as exc:
            records_failed += 1

            log_event(
                "Temporary SIGMET HazardCoordinates processor failure",
                error_type=exc.__class__.__name__,
                sequence_number=sequence_number,
                error=str(exc),
            )

            if sequence_number:
                batch_item_failures.append({"itemIdentifier": sequence_number})

        except Exception as exc:
            records_failed += 1

            log_event(
                "Unexpected SIGMET HazardCoordinates processor failure",
                error_type=exc.__class__.__name__,
                sequence_number=sequence_number,
                error=str(exc),
            )

            if sequence_number:
                batch_item_failures.append({"itemIdentifier": sequence_number})

    log_event(
        "SIGMET HazardCoordinates processor completed",
        records_received=records_received,
        records_processed=records_processed,
        records_failed=records_failed,
        bad_records_written=bad_records_written,
        coordinate_rows_written=coordinate_rows_written,
        already_materialized=already_materialized,
        eventbridge_events_published=eventbridge_events_published,
        batch_item_failures=len(batch_item_failures),
    )

    return {"batchItemFailures": batch_item_failures}