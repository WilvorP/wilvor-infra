import base64
import binascii
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
import h3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
events = boto3.client("events")
s3 = boto3.client("s3")

ACTIVE_HAZARDS_TABLE_NAME = os.environ["ACTIVE_HAZARDS_TABLE_NAME"]
HAZARD_CELLS_TABLE_NAME = os.environ["HAZARD_CELLS_TABLE_NAME"]

H3_RESOLUTION = int(os.environ.get("H3_RESOLUTION", "4"))
SCHEMA_VERSION = os.environ.get("SCHEMA_VERSION", "internal.sigmet.v1")
EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "default")

BAD_RECORDS_BUCKET_NAME = os.environ.get("BAD_RECORDS_BUCKET_NAME")
BAD_RECORDS_PREFIX = os.environ.get(
    "BAD_RECORDS_PREFIX",
    "bad-records/source=sigmet_processor",
)

active_hazards_table = dynamodb.Table(ACTIVE_HAZARDS_TABLE_NAME)
hazard_cells_table = dynamodb.Table(HAZARD_CELLS_TABLE_NAME)


class PermanentRecordError(Exception):
    """A validation error that will not be fixed by retrying the same Kinesis record."""


def json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
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

    if isinstance(value, (int, float)):
        # Support epoch seconds or epoch milliseconds.
        if value > 10_000_000_000:
            value = value / 1000
        return datetime.fromtimestamp(value, tz=timezone.utc)

    if not isinstance(value, str):
        return None

    cleaned = value.strip()
    if not cleaned:
        return None

    # Some NOAA fields can arrive as numeric strings.
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


def extract_source_identity(properties: dict[str, Any]) -> dict[str, str | None]:
    identity = {
        "source_icao_id": clean_string(properties.get("icaoId")),
        "air_sigmet_type": clean_string(properties.get("airSigmetType")),
        "alpha_char": clean_string(properties.get("alphaChar")),
        "series_id": clean_string(properties.get("seriesId")),
        "creation_time": canonical_time_or_string(properties.get("creationTime")),
        "valid_time_from": canonical_time_or_string(properties.get("validTimeFrom")),
        "valid_time_to": canonical_time_or_string(properties.get("validTimeTo")),
    }

    if not any(identity.values()):
        raise PermanentRecordError("SIGMET record has no usable identity fields")

    return identity


def build_hazard_id(properties: dict[str, Any]) -> str:
    identity = extract_source_identity(properties)

    identity_string = "|".join(
        [
            identity.get("source_icao_id") or "",
            identity.get("air_sigmet_type") or "",
            identity.get("alpha_char") or "",
            identity.get("series_id") or "",
            identity.get("creation_time") or "",
            identity.get("valid_time_from") or "",
            identity.get("valid_time_to") or "",
        ]
    )

    return f"sigmet-{stable_hash(identity_string)[:24]}"


def build_source_version(feature: dict[str, Any]) -> str:
    properties = extract_properties(feature)

    content_fingerprint = {
        "rawAirSigmet": properties.get("rawAirSigmet"),
        "rawSigmet": properties.get("rawSigmet"),
        "coords": properties.get("coords"),
        "geometry": feature.get("geometry"),
        "hazard": properties.get("hazard"),
        "severity": properties.get("severity"),
        "altitudeHi1": properties.get("altitudeHi1"),
        "altitudeLow1": properties.get("altitudeLow1"),
        "altitudeHi2": properties.get("altitudeHi2"),
        "altitudeLow2": properties.get("altitudeLow2"),
        "movementDir": properties.get("movementDir"),
        "movementSpd": properties.get("movementSpd"),
        "postProcessFlag": properties.get("postProcessFlag"),
        "validTimeFrom": canonical_time_or_string(properties.get("validTimeFrom")),
        "validTimeTo": canonical_time_or_string(properties.get("validTimeTo")),
        "creationTime": canonical_time_or_string(properties.get("creationTime")),
    }

    return stable_hash(content_fingerprint)[:32]


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


def get_issued_at(properties: dict[str, Any]) -> datetime | None:
    return parse_time(
        get_first_property(
            properties,
            ["creationTime", "issueTime", "issued_at", "issuedAt", "issuanceTime"],
        )
    )


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


def get_raw_text(properties: dict[str, Any]) -> str | None:
    value = get_first_property(
        properties,
        ["rawAirSigmet", "rawSigmet", "raw_text", "rawText", "text"],
    )
    return str(value) if value is not None else None


def ttl_from_valid_to(valid_to: datetime | None) -> int:
    if valid_to:
        return int((valid_to + timedelta(hours=6)).timestamp())

    return int((now_utc() + timedelta(hours=6)).timestamp())


def normalize_ring_lonlat_to_latlng(ring: list[Any]) -> list[tuple[float, float]]:
    normalized: list[tuple[float, float]] = []

    for point in ring:
        if not isinstance(point, list) or len(point) < 2:
            continue

        lon = point[0]
        lat = point[1]

        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue

        normalized.append((float(lat), float(lon)))

    # H3 LatLngPoly does not need the closing duplicate point.
    if len(normalized) >= 2 and normalized[0] == normalized[-1]:
        normalized = normalized[:-1]

    return normalized


def polygon_to_h3_cells(polygon_coordinates: list[Any], resolution: int) -> set[str]:
    if not isinstance(polygon_coordinates, list) or not polygon_coordinates:
        raise PermanentRecordError("Polygon coordinates are missing or invalid")

    outer = normalize_ring_lonlat_to_latlng(polygon_coordinates[0])
    holes = [
        normalize_ring_lonlat_to_latlng(ring)
        for ring in polygon_coordinates[1:]
        if isinstance(ring, list)
    ]

    if len(outer) < 3:
        raise PermanentRecordError("Polygon outer ring has fewer than three valid points")

    holes = [hole for hole in holes if len(hole) >= 3]

    try:
        polygon = h3.LatLngPoly(outer, *holes)
        return set(h3.polygon_to_cells(polygon, resolution))
    except Exception as exc:
        raise PermanentRecordError(f"Failed to convert polygon to H3 cells: {exc}") from exc

def polygon_centroid_cell(polygon_coordinates: list[Any], resolution: int) -> str:
    if not isinstance(polygon_coordinates, list) or not polygon_coordinates:
        raise PermanentRecordError("Polygon coordinates are missing or invalid")

    outer_ring = polygon_coordinates[0]
    if not isinstance(outer_ring, list) or len(outer_ring) < 3:
        raise PermanentRecordError("Polygon outer ring has fewer than three points")

    lat_values = []
    lon_values = []

    for point in outer_ring:
        if not isinstance(point, list) or len(point) < 2:
            continue

        lon = point[0]
        lat = point[1]

        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            lat_values.append(float(lat))
            lon_values.append(float(lon))

    if not lat_values or not lon_values:
        raise PermanentRecordError("Polygon has no valid coordinates for centroid fallback")

    centroid_lat = sum(lat_values) / len(lat_values)
    centroid_lon = sum(lon_values) / len(lon_values)

    return h3.latlng_to_cell(centroid_lat, centroid_lon, resolution)

def geometry_to_h3_cells(geometry: dict[str, Any], resolution: int) -> list[str]:
    if not isinstance(geometry, dict):
        raise PermanentRecordError("Geometry is missing or invalid")

    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")

    if geometry_type == "Polygon":
        cells = polygon_to_h3_cells(coordinates, resolution)

        if not cells:
            cells.add(polygon_centroid_cell(coordinates, resolution))

    elif geometry_type == "MultiPolygon":
        if not isinstance(coordinates, list):
            raise PermanentRecordError("MultiPolygon coordinates are missing or invalid")

        cells: set[str] = set()

        for polygon_coordinates in coordinates:
            polygon_cells = polygon_to_h3_cells(polygon_coordinates, resolution)

            if not polygon_cells:
                polygon_cells.add(polygon_centroid_cell(polygon_coordinates, resolution))

            cells.update(polygon_cells)

    else:
        raise PermanentRecordError(f"Unsupported geometry type: {geometry_type}")

    if not cells:
        raise PermanentRecordError("SIGMET geometry produced zero H3 cells after centroid fallback")

    return sorted(cells)


def build_raw_s3_uri(raw_event: dict[str, Any]) -> str | None:
    raw_s3_bucket = raw_event.get("raw_s3_bucket")
    raw_s3_key = raw_event.get("raw_s3_key")

    if raw_s3_bucket and raw_s3_key:
        return f"s3://{raw_s3_bucket}/{raw_s3_key}"

    return None


def build_active_hazard_item(
    raw_event: dict[str, Any],
    feature: dict[str, Any],
    h3_cells: list[str],
) -> dict[str, Any]:
    properties = extract_properties(feature)
    geometry = feature.get("geometry")

    identity = extract_source_identity(properties)

    hazard_id = build_hazard_id(properties)
    hazard_type = get_hazard_type(properties)
    issued_at = get_issued_at(properties)
    valid_from = get_valid_from(properties)
    valid_to = get_valid_to(properties)

    status = "ACTIVE"
    if valid_to and valid_to < now_utc():
        status = "EXPIRED"

    updated_at = now_utc_iso()

    return {
        "hazard_id": hazard_id,
        "product_type": "SIGMET",
        "hazard_type": hazard_type,
        "status": status,
        "issued_at": iso_or_none(issued_at),
        "valid_from": iso_or_none(valid_from),
        "valid_to": iso_or_none(valid_to),
        "source_icao_id": identity.get("source_icao_id"),
        "alpha_char": identity.get("alpha_char"),
        "series_id": identity.get("series_id"),
        "creation_time": identity.get("creation_time"),
        "air_sigmet_type": identity.get("air_sigmet_type"),
        "raw_text": get_raw_text(properties),
        "geometry_json": json.dumps(geometry, separators=(",", ":"), default=json_default),
        "h3_resolution": H3_RESOLUTION,
        "h3_cells": h3_cells,
        "h3_cell_count": len(h3_cells),
        "source": "NOAA AviationWeather",
        "source_version": build_source_version(feature),
        "schema_version": SCHEMA_VERSION,
        "poll_id": raw_event.get("poll_id"),
        "raw_s3_uri": build_raw_s3_uri(raw_event),
        "received_at": raw_event.get("received_at"),
        "updated_at": updated_at,
        "expires_at": ttl_from_valid_to(valid_to),
    }


def get_existing_hazard(hazard_id: str) -> dict[str, Any] | None:
    response = active_hazards_table.get_item(Key={"hazard_id": hazard_id})
    item = response.get("Item")
    return item if isinstance(item, dict) else None


def determine_change_type(existing: dict[str, Any] | None, item: dict[str, Any]) -> tuple[str, bool, bool]:
    """
    Returns:
      change_type
      should_write_state
      should_publish_event
    """
    if existing is None:
        return "NEW", True, True

    existing_source_version = existing.get("source_version")
    new_source_version = item["source_version"]

    if existing_source_version != new_source_version:
        return "UPDATED", True, True

    # If the DynamoDB write succeeded in a previous attempt but EventBridge publish failed,
    # retry publishing instead of silently losing the Weather.changed event.
    last_published_source_version = existing.get("last_published_source_version")
    existing_change_type = existing.get("change_type")

    if (
        existing_change_type in {"NEW", "UPDATED"}
        and last_published_source_version != new_source_version
    ):
        return str(existing_change_type), False, True

    return "UNCHANGED", False, False


def sync_hazard_cells(
    *,
    hazard_id: str,
    hazard_type: str,
    valid_from: str | None,
    valid_to: str | None,
    expires_at: int,
    h3_cells: list[str],
    previous_h3_cells: list[str] | None,
) -> tuple[int, int]:
    updated_at = now_utc_iso()
    new_cells = set(h3_cells)
    old_cells = set(previous_h3_cells or [])

    removed_cells = old_cells - new_cells

    with hazard_cells_table.batch_writer(overwrite_by_pkeys=["cell_id", "hazard_id"]) as batch:
        for cell_id in removed_cells:
            batch.delete_item(Key={"cell_id": cell_id, "hazard_id": hazard_id})

        for cell_id in sorted(new_cells):
            batch.put_item(
                Item={
                    "cell_id": cell_id,
                    "hazard_id": hazard_id,
                    "hazard_type": hazard_type,
                    "valid_from": valid_from,
                    "valid_to": valid_to,
                    "updated_at": updated_at,
                    "expires_at": expires_at,
                }
            )

    return len(new_cells), len(removed_cells)


def update_last_seen(hazard_id: str, received_at: str | None) -> None:
    active_hazards_table.update_item(
        Key={"hazard_id": hazard_id},
        UpdateExpression="SET last_seen_at = :last_seen_at, received_at = :received_at",
        ExpressionAttributeValues={
            ":last_seen_at": now_utc_iso(),
            ":received_at": received_at,
        },
    )


def publish_weather_changed(item: dict[str, Any], change_type: str) -> None:
    detail = {
        "event_type": "weather.changed",
        "product_type": "SIGMET",
        "hazard_id": item["hazard_id"],
        "hazard_type": item["hazard_type"],
        "change_type": change_type,
        "status": item["status"],
        "valid_from": item.get("valid_from"),
        "valid_to": item.get("valid_to"),
        "h3_resolution": item["h3_resolution"],
        "h3_cell_count": item["h3_cell_count"],
        "source": item["source"],
        "schema_version": item["schema_version"],
        "source_version": item["source_version"],
        "updated_at": item["updated_at"],
    }

    response = events.put_events(
        Entries=[
            {
                "Source": "wilvor.weather",
                "DetailType": "Weather.changed",
                "EventBusName": EVENT_BUS_NAME,
                "Detail": json.dumps(detail, separators=(",", ":"), default=json_default),
            }
        ]
    )

    failed_count = int(response.get("FailedEntryCount", 0))
    if failed_count:
        raise RuntimeError(f"EventBridge PutEvents failed: {response.get('Entries')}")


def mark_event_published(hazard_id: str, source_version: str) -> None:
    active_hazards_table.update_item(
        Key={"hazard_id": hazard_id},
        UpdateExpression=(
            "SET last_published_source_version = :source_version, "
            "last_published_at = :published_at"
        ),
        ExpressionAttributeValues={
            ":source_version": source_version,
            ":published_at": now_utc_iso(),
        },
    )


def process_decoded_record(raw_event: dict[str, Any]) -> dict[str, int]:
    feature = extract_feature(raw_event)
    geometry = feature.get("geometry")

    h3_cells = geometry_to_h3_cells(geometry, H3_RESOLUTION)
    item = build_active_hazard_item(raw_event, feature, h3_cells)

    existing = get_existing_hazard(item["hazard_id"])
    change_type, should_write_state, should_publish_event = determine_change_type(existing, item)

    hazard_cells_written = 0
    hazard_cells_removed = 0
    active_hazards_written = 0
    eventbridge_events_published = 0
    unchanged = 0

    if should_write_state:
        first_seen_at = now_utc_iso()
        if existing:
            first_seen_at = existing.get("first_seen_at") or existing.get("updated_at") or first_seen_at

        item["first_seen_at"] = first_seen_at
        item["last_seen_at"] = now_utc_iso()
        item["change_type"] = change_type

        active_hazards_table.put_item(Item=item)
        active_hazards_written = 1

        previous_h3_cells = None
        if existing and isinstance(existing.get("h3_cells"), list):
            previous_h3_cells = existing.get("h3_cells")

        hazard_cells_written, hazard_cells_removed = sync_hazard_cells(
            hazard_id=item["hazard_id"],
            hazard_type=item["hazard_type"],
            valid_from=item.get("valid_from"),
            valid_to=item.get("valid_to"),
            expires_at=item["expires_at"],
            h3_cells=h3_cells,
            previous_h3_cells=previous_h3_cells,
        )
    else:
        update_last_seen(item["hazard_id"], raw_event.get("received_at"))
        if change_type == "UNCHANGED":
            unchanged = 1

    if should_publish_event:
        publish_weather_changed(item, change_type)
        mark_event_published(item["hazard_id"], item["source_version"])
        eventbridge_events_published = 1

    return {
        "active_hazards_written": active_hazards_written,
        "hazard_cells_written": hazard_cells_written,
        "hazard_cells_removed": hazard_cells_removed,
        "eventbridge_events_published": eventbridge_events_published,
        "new_records": 1 if change_type == "NEW" else 0,
        "updated_records": 1 if change_type == "UPDATED" else 0,
        "unchanged_records": unchanged,
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
        "service": "sigmet_processor",
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

    active_hazards_written = 0
    hazard_cells_written = 0
    hazard_cells_removed = 0
    eventbridge_events_published = 0
    new_records = 0
    updated_records = 0
    unchanged_records = 0

    batch_item_failures = []

    for record in records:
        sequence_number = get_record_sequence_number(record)
        decoded_payload = None
        raw_base64 = get_record_base64(record)

        try:
            decoded_payload = decode_kinesis_record(record)
            result = process_decoded_record(decoded_payload)

            records_processed += 1
            active_hazards_written += result["active_hazards_written"]
            hazard_cells_written += result["hazard_cells_written"]
            hazard_cells_removed += result["hazard_cells_removed"]
            eventbridge_events_published += result["eventbridge_events_published"]
            new_records += result["new_records"]
            updated_records += result["updated_records"]
            unchanged_records += result["unchanged_records"]

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
                    "Permanent SIGMET record failure written to S3",
                    error_type=exc.__class__.__name__,
                    sequence_number=sequence_number,
                    bad_record_uri=bad_record_uri,
                )
            except Exception as quarantine_exc:
                records_failed += 1
                log_event(
                    "Failed to write permanent SIGMET failure to S3",
                    error_type=quarantine_exc.__class__.__name__,
                    sequence_number=sequence_number,
                    error=str(quarantine_exc),
                )
                if sequence_number:
                    batch_item_failures.append({"itemIdentifier": sequence_number})

        except (ClientError, BotoCoreError, RuntimeError) as exc:
            records_failed += 1
            log_event(
                "Temporary SIGMET processor failure",
                error_type=exc.__class__.__name__,
                sequence_number=sequence_number,
                error=str(exc),
            )
            if sequence_number:
                batch_item_failures.append({"itemIdentifier": sequence_number})

        except Exception as exc:
            records_failed += 1
            log_event(
                "Unexpected SIGMET processor failure",
                error_type=exc.__class__.__name__,
                sequence_number=sequence_number,
                error=str(exc),
            )
            if sequence_number:
                batch_item_failures.append({"itemIdentifier": sequence_number})

    log_event(
        "SIGMET processor completed",
        records_received=records_received,
        records_processed=records_processed,
        records_failed=records_failed,
        bad_records_written=bad_records_written,
        active_hazards_written=active_hazards_written,
        hazard_cells_written=hazard_cells_written,
        hazard_cells_removed=hazard_cells_removed,
        eventbridge_events_published=eventbridge_events_published,
        new_records=new_records,
        updated_records=updated_records,
        unchanged_records=unchanged_records,
        h3_resolution=H3_RESOLUTION,
    )

    return {"batchItemFailures": batch_item_failures}