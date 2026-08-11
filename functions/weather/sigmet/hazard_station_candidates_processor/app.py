import hashlib
import json
import logging
import math
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import time
import h3

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
events = boto3.client("events")

HAZARD_COORDINATES_TABLE_NAME = os.environ["HAZARD_COORDINATES_TABLE_NAME"]
STATION_REFERENCE_TABLE_NAME = os.environ["STATION_REFERENCE_TABLE_NAME"]
STATION_REFERENCE_H3_INDEX_NAME = os.environ.get(
    "STATION_REFERENCE_H3_INDEX_NAME",
    "h3_cell-station_id-index",
)

H3_RESOLUTION = int(os.environ.get("H3_RESOLUTION", "4"))
HAZARD_STATION_CANDIDATES_TABLE_NAME = os.environ["HAZARD_STATION_CANDIDATES_TABLE_NAME"]

SCHEMA_VERSION = os.environ.get(
    "SCHEMA_VERSION",
    "wilvor.hazard_station_candidates.v4.0",
)
EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "default")
SELECTION_RADIUS_NM = float(os.environ.get("SELECTION_RADIUS_NM", "50"))
SELECTION_CONFIG_VERSION = os.environ.get(
    "SELECTION_CONFIG_VERSION",
    "hazard-station-selection-v1",
)

hazard_coordinates_table = dynamodb.Table(HAZARD_COORDINATES_TABLE_NAME)
station_reference_table = dynamodb.Table(STATION_REFERENCE_TABLE_NAME)
hazard_station_candidates_table = dynamodb.Table(HAZARD_STATION_CANDIDATES_TABLE_NAME)


class PermanentProcessingError(Exception):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, datetime):
        return value.isoformat()

    return str(value)


def log_event(message: str, **kwargs: Any) -> None:
    logger.info(json.dumps({"message": message, **kwargs}, default=json_default))


def stable_hash(value: Any) -> str:
    serialized = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=json_default,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def clean_string(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    return text if text else None


def to_float(value: Any) -> float | None:
    if value is None:
        return None

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int:
    if isinstance(value, Decimal):
        return int(value)

    return int(value)


def to_optional_int(value: Any) -> int | None:
    if value is None:
        return None

    try:
        return to_int(value)
    except (TypeError, ValueError):
        return None


def decimal_number(value: float) -> Decimal:
    return Decimal(str(round(value, 6)))


def extract_detail(event: dict[str, Any]) -> dict[str, Any]:
    detail = event.get("detail")

    if isinstance(detail, dict):
        return detail

    return event


def scan_all(table) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    exclusive_start_key = None

    while True:
        kwargs: dict[str, Any] = {}

        if exclusive_start_key:
            kwargs["ExclusiveStartKey"] = exclusive_start_key

        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))

        exclusive_start_key = response.get("LastEvaluatedKey")
        if not exclusive_start_key:
            break

    return items

def latlon_polygons_to_h3_cells(
    polygons: list[list[list[tuple[float, float]]]],
) -> set[str]:
    cells: set[str] = set()

    for polygon in polygons:
        exterior = polygon[0]
        holes = polygon[1:] if len(polygon) > 1 else []

        # h3.LatLngPoly expects coordinates as (lat, lng)
        h3_polygon = h3.LatLngPoly(
            exterior,
            *holes,
        )

        polygon_cells = h3.polygon_to_cells(
            h3_polygon,
            H3_RESOLUTION,
        )

        cells.update(str(cell) for cell in polygon_cells)

    return cells


def h3_grid_disk_radius_for_nm(radius_nm: float) -> int:
    """
    Approximate k-ring distance from the configured station selection radius.

    At H3 resolution 4, average hexagon edge length is large enough that a small
    disk usually covers nearby stations. We intentionally over-select here and
    still apply exact geometry/distance filtering afterward.
    """
    if H3_RESOLUTION <= 3:
        return max(1, math.ceil(radius_nm / 100))

    if H3_RESOLUTION == 4:
        return max(1, math.ceil(radius_nm / 45))

    if H3_RESOLUTION == 5:
        return max(1, math.ceil(radius_nm / 17))

    if H3_RESOLUTION == 6:
        return max(1, math.ceil(radius_nm / 6))

    return max(1, math.ceil(radius_nm / 2))


def expand_h3_cells_for_selection_radius(cells: set[str]) -> set[str]:
    if not cells:
        return set()

    k = h3_grid_disk_radius_for_nm(SELECTION_RADIUS_NM)
    expanded: set[str] = set()

    for cell in cells:
        try:
            expanded.update(str(candidate) for candidate in h3.grid_disk(cell, k))
        except Exception:
            expanded.add(cell)

    return expanded


def query_station_reference_by_h3_cell(h3_cell: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    exclusive_start_key = None

    while True:
        kwargs: dict[str, Any] = {
            "IndexName": STATION_REFERENCE_H3_INDEX_NAME,
            "KeyConditionExpression": Key("h3_cell").eq(h3_cell),
        }

        if exclusive_start_key:
            kwargs["ExclusiveStartKey"] = exclusive_start_key

        response = station_reference_table.query(**kwargs)
        items.extend(response.get("Items", []))

        exclusive_start_key = response.get("LastEvaluatedKey")
        if not exclusive_start_key:
            break

    return items


def query_station_reference_for_hazard(
    polygons: list[list[list[tuple[float, float]]]],
) -> list[dict[str, Any]]:
    base_cells = latlon_polygons_to_h3_cells(polygons)
    candidate_cells = expand_h3_cells_for_selection_radius(base_cells)

    stations_by_id: dict[str, dict[str, Any]] = {}

    for h3_cell in sorted(candidate_cells):
        for station in query_station_reference_by_h3_cell(h3_cell):
            station_id = clean_string(station.get("station_id"))
            if station_id:
                stations_by_id[station_id] = station

    return list(stations_by_id.values())


def query_hazard_coordinates(hazard_version_key: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    exclusive_start_key = None

    while True:
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("hazard_version_key").eq(hazard_version_key),
        }

        if exclusive_start_key:
            kwargs["ExclusiveStartKey"] = exclusive_start_key

        response = hazard_coordinates_table.query(**kwargs)
        items.extend(response.get("Items", []))

        exclusive_start_key = response.get("LastEvaluatedKey")
        if not exclusive_start_key:
            break

    items.sort(key=lambda item: item["coordinate_key"])
    return items


def query_existing_candidates(hazard_version_key: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    exclusive_start_key = None

    while True:
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("hazard_version_key").eq(hazard_version_key),
        }

        if exclusive_start_key:
            kwargs["ExclusiveStartKey"] = exclusive_start_key

        response = hazard_station_candidates_table.query(**kwargs)
        items.extend(response.get("Items", []))

        exclusive_start_key = response.get("LastEvaluatedKey")
        if not exclusive_start_key:
            break

    return items


def list_hazard_versions_from_coordinates() -> list[dict[str, Any]]:
    coordinate_items = scan_all(hazard_coordinates_table)
    now_epoch = int(utc_now().timestamp())

    seen: dict[str, dict[str, Any]] = {}

    for item in coordinate_items:
        hazard_version_key = clean_string(item.get("hazard_version_key"))
        if not hazard_version_key:
            continue

        expires_at_epoch = to_optional_int(item.get("expires_at_epoch"))

        if expires_at_epoch is not None and expires_at_epoch <= now_epoch:
            continue

        if hazard_version_key in seen:
            continue

        seen[hazard_version_key] = {
            "hazard_version_key": hazard_version_key,
            "hazard_id": item.get("hazard_id"),
            "source_version": item.get("source_version"),
            "hazard_type": item.get("hazard_type"),
            "severity": item.get("severity"),
            "valid_from_utc": item.get("valid_from_utc"),
            "valid_to_utc": item.get("valid_to_utc"),
            "expires_at_epoch": expires_at_epoch,
            "correlation_id": item.get("correlation_id"),
            "geometry_hash": item.get("geometry_hash"),
        }

    return list(seen.values())


def normalize_hazard_metadata(
    detail: dict[str, Any],
    coordinate_items: list[dict[str, Any]],
) -> dict[str, Any]:
    if not coordinate_items:
        raise PermanentProcessingError("No HazardCoordinates rows found for hazard version")

    first_coordinate = coordinate_items[0]

    hazard_version_key = clean_string(
        detail.get("hazard_version_key")
        or first_coordinate.get("hazard_version_key")
    )
    if not hazard_version_key:
        raise PermanentProcessingError("Missing hazard_version_key")

    hazard_id = clean_string(
        detail.get("hazard_id")
        or first_coordinate.get("hazard_id")
    )
    if not hazard_id:
        raise PermanentProcessingError("Missing hazard_id")

    hazard_source_version = clean_string(
        detail.get("hazard_source_version")
        or detail.get("source_version")
        or first_coordinate.get("source_version")
    )
    if not hazard_source_version:
        raise PermanentProcessingError("Missing hazard source version")

    hazard_type = clean_string(
        detail.get("hazard_type")
        or first_coordinate.get("hazard_type")
    )
    if not hazard_type:
        raise PermanentProcessingError(
            "Missing hazard_type. Re-run SIGMET poller after updating HazardCoordinates metadata."
        )

    valid_from_utc = clean_string(
        detail.get("valid_from_utc")
        or first_coordinate.get("valid_from_utc")
    )
    valid_to_utc = clean_string(
        detail.get("valid_to_utc")
        or first_coordinate.get("valid_to_utc")
    )

    if not valid_from_utc or not valid_to_utc:
        raise PermanentProcessingError(
            "Missing valid_from_utc or valid_to_utc. Re-run SIGMET poller after updating HazardCoordinates metadata."
        )

    expires_at_epoch = (
        detail.get("expires_at_epoch")
        or first_coordinate.get("expires_at_epoch")
    )

    if expires_at_epoch is None:
        raise PermanentProcessingError("Missing expires_at_epoch")

    return {
        "hazard_version_key": hazard_version_key,
        "hazard_id": hazard_id,
        "hazard_source_version": hazard_source_version,
        "hazard_type": hazard_type,
        "severity": clean_string(
            detail.get("severity")
            or first_coordinate.get("severity")
        ),
        "valid_from_utc": valid_from_utc,
        "valid_to_utc": valid_to_utc,
        "expires_at_epoch": to_int(expires_at_epoch),
        "correlation_id": clean_string(
            detail.get("correlation_id")
            or first_coordinate.get("correlation_id")
        )
        or f"hazard-station-{stable_hash(hazard_version_key)[:24]}",
        "geometry_hash": clean_string(
            detail.get("geometry_hash")
            or first_coordinate.get("geometry_hash")
        ),
    }


def reconstruct_geometry(
    coordinate_items: list[dict[str, Any]],
) -> list[list[list[tuple[float, float]]]]:
    grouped: dict[int, dict[int, list[dict[str, Any]]]] = {}

    for item in coordinate_items:
        polygon_index = to_int(item["polygon_index"])
        ring_index = to_int(item["ring_index"])

        grouped.setdefault(polygon_index, {}).setdefault(ring_index, []).append(item)

    polygons: list[list[list[tuple[float, float]]]] = []

    for polygon_index in sorted(grouped):
        polygon: list[list[tuple[float, float]]] = []

        for ring_index in sorted(grouped[polygon_index]):
            ring_items = sorted(
                grouped[polygon_index][ring_index],
                key=lambda item: to_int(item["sequence_number"]),
            )

            ring: list[tuple[float, float]] = []

            for item in ring_items:
                latitude = to_float(item.get("latitude"))
                longitude = to_float(item.get("longitude"))

                if latitude is None or longitude is None:
                    continue

                ring.append((latitude, longitude))

            if len(ring) >= 3:
                polygon.append(ring)

        if polygon:
            polygons.append(polygon)

    if not polygons:
        raise PermanentProcessingError("Could not reconstruct polygon geometry from coordinates")

    return polygons


def point_in_ring(latitude: float, longitude: float, ring: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(ring) - 1

    for i in range(len(ring)):
        lat_i, lon_i = ring[i]
        lat_j, lon_j = ring[j]

        if (lon_i > longitude) != (lon_j > longitude):
            crossing_latitude = (
                (lat_j - lat_i)
                * (longitude - lon_i)
                / ((lon_j - lon_i) or 1e-12)
                + lat_i
            )

            if latitude < crossing_latitude:
                inside = not inside

        j = i

    return inside


def point_in_polygon(
    latitude: float,
    longitude: float,
    polygon: list[list[tuple[float, float]]],
) -> bool:
    exterior = polygon[0]

    if not point_in_ring(latitude, longitude, exterior):
        return False

    for hole in polygon[1:]:
        if point_in_ring(latitude, longitude, hole):
            return False

    return True


def point_inside_geometry(
    latitude: float,
    longitude: float,
    polygons: list[list[list[tuple[float, float]]]],
) -> bool:
    return any(point_in_polygon(latitude, longitude, polygon) for polygon in polygons)


def project_to_local_nm(
    latitude: float,
    longitude: float,
    origin_latitude: float,
    origin_longitude: float,
) -> tuple[float, float]:
    mean_latitude_rad = math.radians(origin_latitude)

    x = (longitude - origin_longitude) * math.cos(mean_latitude_rad) * 60.0
    y = (latitude - origin_latitude) * 60.0

    return x, y


def distance_point_to_segment_nm(
    point_latitude: float,
    point_longitude: float,
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    sx, sy = project_to_local_nm(
        start[0],
        start[1],
        point_latitude,
        point_longitude,
    )
    ex, ey = project_to_local_nm(
        end[0],
        end[1],
        point_latitude,
        point_longitude,
    )

    dx = ex - sx
    dy = ey - sy

    if dx == 0 and dy == 0:
        return math.sqrt((0.0 - sx) ** 2 + (0.0 - sy) ** 2)

    t = (-(sx * dx + sy * dy)) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))

    closest_x = sx + t * dx
    closest_y = sy + t * dy

    return math.sqrt(closest_x**2 + closest_y**2)


def distance_to_ring_nm(
    latitude: float,
    longitude: float,
    ring: list[tuple[float, float]],
) -> float:
    distances = []

    for index in range(len(ring)):
        start = ring[index]
        end = ring[(index + 1) % len(ring)]

        distances.append(
            distance_point_to_segment_nm(
                latitude,
                longitude,
                start,
                end,
            )
        )

    return min(distances) if distances else float("inf")


def distance_to_geometry_nm(
    latitude: float,
    longitude: float,
    polygons: list[list[list[tuple[float, float]]]],
) -> float:
    if point_inside_geometry(latitude, longitude, polygons):
        return 0.0

    distances = []

    for polygon in polygons:
        for ring in polygon:
            distances.append(distance_to_ring_nm(latitude, longitude, ring))

    return min(distances) if distances else float("inf")


def build_candidate_id(hazard_version_key: str, station_id: str) -> str:
    return f"hsc-{stable_hash({'hazard_version_key': hazard_version_key, 'station_id': station_id})[:32]}"


def build_candidate_items(
    *,
    hazard_metadata: dict[str, Any],
    polygons: list[list[list[tuple[float, float]]]],
    stations: list[dict[str, Any]],
    existing_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    now = utc_now_iso()

    existing_by_station = {
        clean_string(item.get("station_id")): item
        for item in existing_candidates
        if clean_string(item.get("station_id"))
    }

    candidates: list[dict[str, Any]] = []

    for station in stations:
        if station.get("active") is False:
            continue

        station_id = clean_string(station.get("station_id"))
        if not station_id:
            continue

        station_latitude = to_float(station.get("latitude"))
        station_longitude = to_float(station.get("longitude"))

        if station_latitude is None or station_longitude is None:
            continue

        inside = point_inside_geometry(
            station_latitude,
            station_longitude,
            polygons,
        )

        distance_nm = (
            0.0
            if inside
            else distance_to_geometry_nm(station_latitude, station_longitude, polygons)
        )

        if not inside and distance_nm > SELECTION_RADIUS_NM:
            continue

        spatial_relationship = "INSIDE" if inside else "NEAR"
        reason = "STATION_INSIDE_SIGMET" if inside else "STATION_NEAR_SIGMET"

        existing_item = existing_by_station.get(station_id)
        created_at_utc = (
            clean_string(existing_item.get("created_at_utc"))
            if existing_item
            else None
        ) or now

        item = {
            "hazard_version_key": hazard_metadata["hazard_version_key"],
            "station_id": station_id,
            "candidate_id": build_candidate_id(
                hazard_metadata["hazard_version_key"],
                station_id,
            ),
            "hazard_id": hazard_metadata["hazard_id"],
            "hazard_source_version": hazard_metadata["hazard_source_version"],
            "hazard_type": hazard_metadata["hazard_type"],
            "valid_from_utc": hazard_metadata["valid_from_utc"],
            "valid_to_utc": hazard_metadata["valid_to_utc"],
            "station_latitude": decimal_number(station_latitude),
            "station_longitude": decimal_number(station_longitude),
            "spatial_relationship": spatial_relationship,
            "distance_to_hazard_nm": decimal_number(distance_nm),
            "selection_radius_nm": decimal_number(SELECTION_RADIUS_NM),
            "reason": reason,
            "selection_config_version": SELECTION_CONFIG_VERSION,
            "created_at_utc": created_at_utc,
            "updated_at_utc": now,
            "correlation_id": hazard_metadata["correlation_id"],
            "schema_version": SCHEMA_VERSION,
            "expires_at_epoch": hazard_metadata["expires_at_epoch"],
        }

        station_name = clean_string(station.get("station_name"))
        if station_name:
            item["station_name"] = station_name

        airport_id = clean_string(station.get("airport_id"))
        if airport_id:
            item["airport_id"] = airport_id

        severity = clean_string(hazard_metadata.get("severity"))
        if severity:
            item["severity"] = severity

        candidates.append(item)

    candidates.sort(key=lambda item: item["station_id"])
    return candidates


def delete_existing_candidates(existing_candidates: list[dict[str, Any]]) -> int:
    if not existing_candidates:
        return 0

    with hazard_station_candidates_table.batch_writer() as batch:
        for item in existing_candidates:
            batch.delete_item(
                Key={
                    "hazard_version_key": item["hazard_version_key"],
                    "station_id": item["station_id"],
                }
            )

    return len(existing_candidates)


def write_candidate_items(items: list[dict[str, Any]]) -> int:
    if not items:
        return 0

    with hazard_station_candidates_table.batch_writer(
        overwrite_by_pkeys=["hazard_version_key", "station_id"]
    ) as batch:
        for item in items:
            batch.put_item(Item=item)

    return len(items)


def publish_hazard_stations_ready(
    *,
    hazard_metadata: dict[str, Any],
    candidate_count: int,
    deleted_count: int,
    station_count: int,
    change_type: str,
) -> None:
    detail = {
        "event_type": "hazard.stations.ready",
        "change_type": change_type,
        "hazard_version_key": hazard_metadata["hazard_version_key"],
        "hazard_id": hazard_metadata["hazard_id"],
        "hazard_source_version": hazard_metadata["hazard_source_version"],
        "hazard_type": hazard_metadata["hazard_type"],
        "severity": hazard_metadata.get("severity"),
        "candidate_count": candidate_count,
        "deleted_candidate_count": deleted_count,
        "station_reference_count": station_count,
        "selection_radius_nm": SELECTION_RADIUS_NM,
        "selection_config_version": SELECTION_CONFIG_VERSION,
        "valid_from_utc": hazard_metadata["valid_from_utc"],
        "valid_to_utc": hazard_metadata["valid_to_utc"],
        "expires_at_epoch": hazard_metadata["expires_at_epoch"],
        "correlation_id": hazard_metadata["correlation_id"],
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": utc_now_iso(),
    }

    response = events.put_events(
        Entries=[
            {
                "Source": "wilvor.weather",
                "DetailType": "hazard.stations.ready",
                "EventBusName": EVENT_BUS_NAME,
                "Detail": json.dumps(detail, separators=(",", ":"), default=json_default),
            }
        ]
    )

    failed_count = int(response.get("FailedEntryCount", 0))
    if failed_count:
        raise RuntimeError(f"EventBridge PutEvents failed: {response.get('Entries')}")


def determine_change_type(
    *,
    existing_count: int,
    written_count: int,
) -> str:
    if written_count == 0:
        return "NO_CANDIDATES"

    if existing_count == 0:
        return "CREATED"

    return "UPDATED"


def process_hazard_version(detail: dict[str, Any]) -> dict[str, Any]:
    hazard_version_key = clean_string(detail.get("hazard_version_key"))

    if not hazard_version_key:
        raise PermanentProcessingError("Missing hazard_version_key")

    coordinate_items = query_hazard_coordinates(hazard_version_key)
    hazard_metadata = normalize_hazard_metadata(detail, coordinate_items)

    polygons = reconstruct_geometry(coordinate_items)

    stations = query_station_reference_for_hazard(polygons)

    if not stations:
        log_event(
            "HazardStationCandidates found no station candidates from H3 prefilter",
            hazard_version_key=hazard_version_key,
        )

    existing_candidates = query_existing_candidates(hazard_version_key)

    candidates = build_candidate_items(
        hazard_metadata=hazard_metadata,
        polygons=polygons,
        stations=stations,
        existing_candidates=existing_candidates,
    )

    deleted_count = delete_existing_candidates(existing_candidates)
    written_count = write_candidate_items(candidates)

    change_type = determine_change_type(
        existing_count=len(existing_candidates),
        written_count=written_count,
    )

    publish_hazard_stations_ready(
        hazard_metadata=hazard_metadata,
        candidate_count=written_count,
        deleted_count=deleted_count,
        station_count=len(stations),
        change_type=change_type,
    )

    return {
        "hazard_version_key": hazard_version_key,
        "status": "READY",
        "change_type": change_type,
        "coordinate_count": len(coordinate_items),
        "station_count": len(stations),
        "candidate_count": written_count,
        "deleted_candidate_count": deleted_count,
        "eventbridge_events_published": 1,
    }


def rebuild_all_hazard_versions() -> dict[str, Any]:
    hazard_details = list_hazard_versions_from_coordinates()

    processed = 0
    skipped = 0
    candidates_written = 0
    candidates_deleted = 0
    events_published = 0
    failures: list[dict[str, str]] = []

    for detail in hazard_details:
        try:
            result = process_hazard_version(detail)

            if result.get("status") == "READY":
                processed += 1
                candidates_written += int(result["candidate_count"])
                candidates_deleted += int(result["deleted_candidate_count"])
                events_published += int(result["eventbridge_events_published"])
            else:
                skipped += 1

        except PermanentProcessingError as exc:
            skipped += 1
            failures.append(
                {
                    "hazard_version_key": str(detail.get("hazard_version_key")),
                    "reason": str(exc),
                }
            )

    return {
        "hazard_versions_seen": len(hazard_details),
        "hazard_versions_processed": processed,
        "hazard_versions_skipped": skipped,
        "candidate_count": candidates_written,
        "deleted_candidate_count": candidates_deleted,
        "eventbridge_events_published": events_published,
        "failures": failures[:10],
    }


def lambda_handler(event, context):
    started_at = utc_now()
    event = event or {}

    detail_type = event.get("detail-type") or event.get("DetailType")
    detail = extract_detail(event)

    try:
        if event.get("manual_rebuild_all") is True or detail_type == "station.reference.updated":
            result = rebuild_all_hazard_versions()
            mode = "REBUILD_ALL_HAZARD_VERSIONS"
        else:
            result = process_hazard_version(detail)
            mode = "PROCESS_SINGLE_HAZARD_VERSION"

        log_event(
            "HazardStationCandidates processor completed",
            mode=mode,
            detail_type=detail_type,
            duration_ms=int((utc_now() - started_at).total_seconds() * 1000),
            **result,
        )

        return {
            "ok": True,
            "mode": mode,
            **result,
        }

    except PermanentProcessingError as exc:
        log_event(
            "HazardStationCandidates permanent processing failure",
            detail_type=detail_type,
            error=str(exc),
        )

        return {
            "ok": False,
            "error_type": exc.__class__.__name__,
            "error": str(exc),
        }

    except (ClientError, BotoCoreError, RuntimeError) as exc:
        log_event(
            "HazardStationCandidates temporary processing failure",
            detail_type=detail_type,
            error_type=exc.__class__.__name__,
            error=str(exc),
        )

        raise