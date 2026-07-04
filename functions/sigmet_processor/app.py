import base64
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
import h3


logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")

ACTIVE_HAZARDS_TABLE_NAME = os.environ["ACTIVE_HAZARDS_TABLE_NAME"]
HAZARD_CELLS_TABLE_NAME = os.environ["HAZARD_CELLS_TABLE_NAME"]
H3_RESOLUTION = int(os.environ.get("H3_RESOLUTION", "4"))
SCHEMA_VERSION = os.environ.get("SCHEMA_VERSION", "internal.sigmet.v1")

active_hazards_table = dynamodb.Table(ACTIVE_HAZARDS_TABLE_NAME)
hazard_cells_table = dynamodb.Table(HAZARD_CELLS_TABLE_NAME)


def log_event(message: str, **kwargs: Any) -> None:
    logger.info(json.dumps({"message": message, **kwargs}, default=str))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_utc_iso() -> str:
    return now_utc().isoformat()


def decode_kinesis_record(record: dict[str, Any]) -> dict[str, Any]:
    encoded_data = record["kinesis"]["data"]
    decoded = base64.b64decode(encoded_data).decode("utf-8")
    return json.loads(decoded)


def extract_feature(raw_event: dict[str, Any]) -> dict[str, Any]:
    feature = raw_event.get("feature")

    if not isinstance(feature, dict):
        raise ValueError("Kinesis payload does not contain a valid GeoJSON feature")

    if feature.get("type") != "Feature":
        raise ValueError("SIGMET record is not a GeoJSON Feature")

    return feature


def parse_time(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        # Support epoch seconds or milliseconds.
        if value > 10_000_000_000:
            value = value / 1000
        return datetime.fromtimestamp(value, tz=timezone.utc)

    if not isinstance(value, str):
        return None

    cleaned = value.strip()
    if not cleaned:
        return None

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


def get_first_property(properties: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = properties.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def stable_hash(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_hazard_id(feature: dict[str, Any]) -> str:
    properties = feature.get("properties") or {}
    geometry = feature.get("geometry")

    if not isinstance(properties, dict):
        properties = {}

    preferred_id = get_first_property(
        properties,
        [
            "id",
            "hazard_id",
            "sigmet_id",
            "sigmetId",
            "product_id",
            "productId",
            "airSigmetId",
            "airsigmet_id",
        ],
    )

    if preferred_id:
        return str(preferred_id).strip()

    fingerprint = {
        "raw_text": get_first_property(properties, ["rawSigmet", "raw_text", "rawText"]),
        "valid_from": get_first_property(properties, ["validTimeFrom", "valid_from"]),
        "valid_to": get_first_property(properties, ["validTimeTo", "valid_to"]),
        "geometry": geometry,
    }

    return f"sigmet-{stable_hash(fingerprint)[:24]}"


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
            ["issueTime", "issued_at", "issuedAt", "issuanceTime"],
        )
    )


def get_hazard_type(properties: dict[str, Any]) -> str:
    value = get_first_property(
        properties,
        [
            "hazard",
            "hazardType",
            "hazard_type",
            "phenomenon",
            "airSigmetType",
        ],
    )

    if value:
        return str(value).strip().upper()

    raw_text = get_first_property(properties, ["rawSigmet", "raw_text", "rawText"])
    if raw_text:
        text = str(raw_text).upper()
        for keyword in [
            "TURB",
            "TURBULENCE",
            "ICE",
            "ICING",
            "CONVECTIVE",
            "TS",
            "THUNDERSTORM",
            "IFR",
            "MTN OBSCN",
            "VOLCANIC",
            "ASH",
        ]:
            if keyword in text:
                return keyword.replace(" ", "_")

    return "UNKNOWN"


def get_raw_text(properties: dict[str, Any]) -> str | None:
    value = get_first_property(properties, ["rawSigmet", "raw_text", "rawText", "text"])
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
    if not polygon_coordinates:
        return set()

    outer = normalize_ring_lonlat_to_latlng(polygon_coordinates[0])
    holes = [
        normalize_ring_lonlat_to_latlng(ring)
        for ring in polygon_coordinates[1:]
        if isinstance(ring, list)
    ]

    if len(outer) < 3:
        return set()

    holes = [hole for hole in holes if len(hole) >= 3]

    polygon = h3.LatLngPoly(outer, *holes)
    return set(h3.polygon_to_cells(polygon, resolution))


def geometry_to_h3_cells(geometry: dict[str, Any], resolution: int) -> list[str]:
    if not isinstance(geometry, dict):
        raise ValueError("Geometry is missing or invalid")

    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")

    if geometry_type == "Polygon":
        cells = polygon_to_h3_cells(coordinates, resolution)
        return sorted(cells)

    if geometry_type == "MultiPolygon":
        cells: set[str] = set()

        if not isinstance(coordinates, list):
            return []

        for polygon_coordinates in coordinates:
            cells.update(polygon_to_h3_cells(polygon_coordinates, resolution))

        return sorted(cells)

    raise ValueError(f"Unsupported geometry type: {geometry_type}")


def build_active_hazard_item(raw_event: dict[str, Any], feature: dict[str, Any], h3_cells: list[str]) -> dict[str, Any]:
    properties = feature.get("properties") or {}
    geometry = feature.get("geometry")

    if not isinstance(properties, dict):
        properties = {}

    hazard_id = build_hazard_id(feature)
    hazard_type = get_hazard_type(properties)

    issued_at = get_issued_at(properties)
    valid_from = get_valid_from(properties)
    valid_to = get_valid_to(properties)

    status = "ACTIVE"
    if valid_to and valid_to < now_utc():
        status = "EXPIRED"

    raw_s3_bucket = raw_event.get("raw_s3_bucket")
    raw_s3_key = raw_event.get("raw_s3_key")

    raw_s3_uri = None
    if raw_s3_bucket and raw_s3_key:
        raw_s3_uri = f"s3://{raw_s3_bucket}/{raw_s3_key}"

    updated_at = now_utc_iso()

    return {
        "hazard_id": hazard_id,
        "product_type": "SIGMET",
        "hazard_type": hazard_type,
        "status": status,
        "issued_at": iso_or_none(issued_at),
        "valid_from": iso_or_none(valid_from),
        "valid_to": iso_or_none(valid_to),
        "raw_text": get_raw_text(properties),
        "geometry_json": json.dumps(geometry, separators=(",", ":"), default=str),
        "h3_resolution": H3_RESOLUTION,
        "h3_cells": h3_cells,
        "h3_cell_count": len(h3_cells),
        "source": "NOAA AviationWeather",
        "source_version": stable_hash(feature)[:32],
        "schema_version": SCHEMA_VERSION,
        "poll_id": raw_event.get("poll_id"),
        "raw_s3_uri": raw_s3_uri,
        "received_at": raw_event.get("received_at"),
        "updated_at": updated_at,
        "expires_at": ttl_from_valid_to(valid_to),
    }


def write_hazard_cells(
    *,
    hazard_id: str,
    hazard_type: str,
    valid_from: str | None,
    valid_to: str | None,
    expires_at: int,
    h3_cells: list[str],
) -> int:
    updated_at = now_utc_iso()

    with hazard_cells_table.batch_writer(overwrite_by_pkeys=["cell_id", "hazard_id"]) as batch:
        for cell_id in h3_cells:
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

    return len(h3_cells)


def process_record(record: dict[str, Any]) -> dict[str, int]:
    raw_event = decode_kinesis_record(record)
    feature = extract_feature(raw_event)

    geometry = feature.get("geometry")
    h3_cells = geometry_to_h3_cells(geometry, H3_RESOLUTION)

    item = build_active_hazard_item(raw_event, feature, h3_cells)

    active_hazards_table.put_item(Item=item)

    hazard_cells_written = write_hazard_cells(
        hazard_id=item["hazard_id"],
        hazard_type=item["hazard_type"],
        valid_from=item.get("valid_from"),
        valid_to=item.get("valid_to"),
        expires_at=item["expires_at"],
        h3_cells=h3_cells,
    )

    return {
        "active_hazards_written": 1,
        "hazard_cells_written": hazard_cells_written,
    }


def lambda_handler(event, context):
    records = event.get("Records", [])

    records_received = len(records)
    records_processed = 0
    records_failed = 0
    active_hazards_written = 0
    hazard_cells_written = 0
    batch_item_failures = []

    for record in records:
        sequence_number = record.get("kinesis", {}).get("sequenceNumber")

        try:
            result = process_record(record)

            records_processed += 1
            active_hazards_written += result["active_hazards_written"]
            hazard_cells_written += result["hazard_cells_written"]

        except Exception as exc:
            records_failed += 1

            log_event(
                "Failed to process SIGMET record",
                error=str(exc),
                sequence_number=sequence_number,
            )

            if sequence_number:
                batch_item_failures.append({"itemIdentifier": sequence_number})

    log_event(
        "SIGMET processor completed",
        records_received=records_received,
        records_processed=records_processed,
        records_failed=records_failed,
        active_hazards_written=active_hazards_written,
        hazard_cells_written=hazard_cells_written,
        h3_resolution=H3_RESOLUTION,
    )

    return {
        "batchItemFailures": batch_item_failures,
    }