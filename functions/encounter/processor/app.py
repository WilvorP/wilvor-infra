import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
import h3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError


dynamodb = boto3.resource("dynamodb")
cloudwatch = boto3.client("cloudwatch")
eventbridge = boto3.client("events")


ENVIRONMENT = os.environ.get(
    "ENVIRONMENT",
    "dev",
)

EVENT_BUS_NAME = os.environ.get(
    "EVENT_BUS_NAME",
    "default",
)

AIRCRAFT_PROJECTION_TABLE_NAME = os.environ[
    "AIRCRAFT_PROJECTION_TABLE_NAME"
]

AIRCRAFT_PROJECTION_CELLS_TABLE_NAME = os.environ[
    "AIRCRAFT_PROJECTION_CELLS_TABLE_NAME"
]

AIRCRAFT_PROJECTION_CELLS_H3_INDEX_NAME = os.environ.get(
    "AIRCRAFT_PROJECTION_CELLS_H3_INDEX_NAME",
    "h3_cell-projection_id-index",
)

HAZARD_CELLS_TABLE_NAME = os.environ[
    "HAZARD_CELLS_TABLE_NAME"
]

HAZARD_CELLS_HAZARD_VERSION_INDEX_NAME = os.environ.get(
    "HAZARD_CELLS_HAZARD_VERSION_INDEX_NAME",
    "hazard_version_key-h3_cell-index",
)

ACTIVE_HAZARDS_TABLE_NAME = os.environ[
    "ACTIVE_HAZARDS_TABLE_NAME"
]

HAZARD_COORDINATES_TABLE_NAME = os.environ[
    "HAZARD_COORDINATES_TABLE_NAME"
]

AIRCRAFT_HAZARD_ENCOUNTER_TABLE_NAME = os.environ[
    "AIRCRAFT_HAZARD_ENCOUNTER_TABLE_NAME"
]

AHE_SCHEMA_VERSION = os.environ.get(
    "AHE_SCHEMA_VERSION",
    "wilvor.aircraft_hazard_encounter.v4.0",
)

AHE_RETENTION_SECONDS = int(
    os.environ.get(
        "AHE_RETENTION_SECONDS",
        "3600",
    )
)

MAX_MATCHED_H3_CELLS = int(
    os.environ.get(
        "MAX_MATCHED_H3_CELLS",
        "200",
    )
)

AIRCRAFT_PROJECTION_AIRCRAFT_INDEX_NAME = os.environ.get(
    "AIRCRAFT_PROJECTION_AIRCRAFT_INDEX_NAME",
    "aircraft_id-generated_at_epoch-index",
)

ENCOUNTER_AIRCRAFT_INDEX_NAME = os.environ.get(
    "ENCOUNTER_AIRCRAFT_INDEX_NAME",
    "aircraft_id-detected_at_epoch-index",
)

CURRENT_ENCOUNTER_STATES = {
    "DETECTED",
    "MONITORING",
}

TERMINAL_ENCOUNTER_STATES = {
    "RESOLVED",
    "SUPERSEDED",
    "EXPIRED",
}


projection_table = dynamodb.Table(
    AIRCRAFT_PROJECTION_TABLE_NAME
)

projection_cells_table = dynamodb.Table(
    AIRCRAFT_PROJECTION_CELLS_TABLE_NAME
)

hazard_cells_table = dynamodb.Table(
    HAZARD_CELLS_TABLE_NAME
)

active_hazards_table = dynamodb.Table(
    ACTIVE_HAZARDS_TABLE_NAME
)

hazard_coordinates_table = dynamodb.Table(
    HAZARD_COORDINATES_TABLE_NAME
)

encounter_table = dynamodb.Table(
    AIRCRAFT_HAZARD_ENCOUNTER_TABLE_NAME
)


def now_epoch() -> int:
    return int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )


def epoch_to_utc(
    epoch: int,
) -> str:
    return (
        datetime.fromtimestamp(
            epoch,
            tz=timezone.utc,
        )
        .isoformat()
        .replace("+00:00", "Z")
    )


def now_utc() -> str:
    return epoch_to_utc(
        now_epoch()
    )


def parse_iso_epoch(
    value: Any,
) -> int | None:
    if value is None:
        return None

    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        parsed = datetime.fromisoformat(
            text
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return int(
            parsed.timestamp()
        )
    except (TypeError, ValueError):
        return None


def json_default(
    value: Any,
):
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)

    raise TypeError(
        f"Object of type {type(value)} is not JSON serializable"
    )


def paged_query(
    table,
    **kwargs,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    response = table.query(
        **kwargs
    )

    items.extend(
        response.get(
            "Items",
            [],
        )
    )

    while response.get(
        "LastEvaluatedKey"
    ):
        response = table.query(
            **kwargs,
            ExclusiveStartKey=response[
                "LastEvaluatedKey"
            ],
        )

        items.extend(
            response.get(
                "Items",
                [],
            )
        )

    return items


def emit_metrics(
    metrics: dict[str, int | float],
) -> None:
    if not metrics:
        return

    cloudwatch.put_metric_data(
        Namespace="Wilvor/Pipeline",
        MetricData=[
            {
                "MetricName": name,
                "Value": value,
                "Unit": "Count",
                "Dimensions": [
                    {
                        "Name": "Environment",
                        "Value": ENVIRONMENT,
                    },
                    {
                        "Name": "Pipeline",
                        "Value": "encounter",
                    },
                    {
                        "Name": "Component",
                        "Value": "encounter_processor",
                    },
                    {
                        "Name": "Stage",
                        "Value": "evaluation",
                    },
                ],
            }
            for name, value in metrics.items()
        ],
    )


def get_projection(
    projection_id: str,
) -> dict[str, Any] | None:
    response = projection_table.get_item(
        Key={
            "projection_id": projection_id
        },
        ConsistentRead=True,
    )

    return response.get("Item")


def query_projection_cells(
    projection_id: str,
) -> list[dict[str, Any]]:
    return paged_query(
        projection_cells_table,
        KeyConditionExpression=Key(
            "projection_id"
        ).eq(
            projection_id
        ),
        ConsistentRead=True,
    )


def query_projection_cells_by_h3(
    h3_cell: str,
) -> list[dict[str, Any]]:
    return paged_query(
        projection_cells_table,
        IndexName=(
            AIRCRAFT_PROJECTION_CELLS_H3_INDEX_NAME
        ),
        KeyConditionExpression=Key(
            "h3_cell"
        ).eq(
            h3_cell
        ),
    )


def query_hazard_cells(
    h3_cell: str,
) -> list[dict[str, Any]]:
    return paged_query(
        hazard_cells_table,
        KeyConditionExpression=Key(
            "h3_cell"
        ).eq(
            h3_cell
        ),
        ConsistentRead=True,
    )


def query_hazard_cells_by_hazard_version(
    hazard_version_key: str,
) -> list[dict[str, Any]]:
    return paged_query(
        hazard_cells_table,
        IndexName=(
            HAZARD_CELLS_HAZARD_VERSION_INDEX_NAME
        ),
        KeyConditionExpression=Key(
            "hazard_version_key"
        ).eq(
            hazard_version_key
        ),
    )


def get_active_hazard(
    hazard_id: str,
) -> dict[str, Any] | None:
    response = active_hazards_table.get_item(
        Key={
            "hazard_id": hazard_id
        },
        ConsistentRead=True,
    )

    return response.get("Item")


def query_hazard_coordinates(
    hazard_version_key: str,
) -> list[dict[str, Any]]:
    return paged_query(
        hazard_coordinates_table,
        KeyConditionExpression=Key(
            "hazard_version_key"
        ).eq(
            hazard_version_key
        ),
        ConsistentRead=True,
    )


def projection_is_ready(
    projection: dict[str, Any],
    *,
    current_epoch: int,
) -> tuple[bool, str | None]:
    if projection.get(
        "projection_status"
    ) != "READY":
        return (
            False,
            "PROJECTION_NOT_READY",
        )

    valid_until_epoch = projection.get(
        "valid_until_epoch"
    )

    if valid_until_epoch is None:
        return (
            False,
            "PROJECTION_MISSING_VALIDITY",
        )

    if int(valid_until_epoch) < current_epoch:
        return (
            False,
            "PROJECTION_EXPIRED",
        )

    return (
        True,
        None,
    )


def hazard_version_key_from_parts(
    hazard_id: str,
    source_version: str,
) -> str:
    return f"{hazard_id}#{source_version}"


def hazard_matches_candidate(
    *,
    hazard: dict[str, Any] | None,
    candidate: dict[str, Any],
    projection: dict[str, Any],
) -> tuple[bool, str | None]:
    if hazard is None:
        return (
            False,
            "HAZARD_NOT_FOUND",
        )

    if hazard.get("status") != "ACTIVE":
        return (
            False,
            "HAZARD_NOT_ACTIVE",
        )

    if hazard.get(
        "materialization_status"
    ) != "READY":
        return (
            False,
            "HAZARD_NOT_READY",
        )

    expected_source_version = (
        candidate.get(
            "hazard_source_version"
        )
        or candidate.get(
            "source_version"
        )
    )

    if (
        expected_source_version
        and hazard.get("source_version")
        != expected_source_version
    ):
        return (
            False,
            "HAZARD_VERSION_MISMATCH",
        )

    materialization_id = candidate.get(
        "materialization_id"
    )

    if (
        materialization_id
        and hazard.get("materialization_id")
        and materialization_id
        != hazard.get("materialization_id")
    ):
        return (
            False,
            "HAZARD_MATERIALIZATION_MISMATCH",
        )

    projection_start = projection.get(
        "generated_at_epoch"
    )

    projection_end = projection.get(
        "valid_until_epoch"
    )

    hazard_start = hazard.get(
        "valid_from_epoch"
    )

    hazard_end = hazard.get(
        "valid_to_epoch"
    )

    if (
        projection_start is None
        or projection_end is None
        or hazard_start is None
        or hazard_end is None
    ):
        return (
            False,
            "VALIDITY_UNAVAILABLE",
        )

    if not (
        int(hazard_start)
        <= int(projection_end)
        and int(hazard_end)
        >= int(projection_start)
    ):
        return (
            False,
            "NO_TIME_OVERLAP",
        )

    return (
        True,
        None,
    )


def collect_hazard_candidates(
    projection_cells: list[dict[str, Any]],
    *,
    hazard_version_key_filter: str | None = None,
) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}

    for projection_cell in projection_cells:
        h3_cell = str(
            projection_cell[
                "h3_cell"
            ]
        )

        for hazard_cell in query_hazard_cells(
            h3_cell
        ):
            hazard_version_key = str(
                hazard_cell.get(
                    "hazard_version_key",
                    "",
                )
            )

            if not hazard_version_key:
                continue

            if (
                hazard_version_key_filter
                and hazard_version_key
                != hazard_version_key_filter
            ):
                continue

            candidate = candidates.setdefault(
                hazard_version_key,
                {
                    "hazard_version_key": (
                        hazard_version_key
                    ),
                    "hazard_id": hazard_cell.get(
                        "hazard_id"
                    ),
                    "hazard_source_version": (
                        hazard_cell.get(
                            "hazard_source_version"
                        )
                        or hazard_cell.get(
                            "source_version"
                        )
                    ),
                    "materialization_id": (
                        hazard_cell.get(
                            "materialization_id"
                        )
                    ),
                    "hazard_cells": [],
                    "matched_h3_cells": set(),
                },
            )

            candidate[
                "hazard_cells"
            ].append(
                hazard_cell
            )

            candidate[
                "matched_h3_cells"
            ].add(
                h3_cell
            )

    return candidates


def group_hazard_coordinates(
    coordinates: list[dict[str, Any]],
) -> list[list[list[tuple[float, float]]]]:
    grouped: dict[
        int,
        dict[int, list[dict[str, Any]]],
    ] = {}

    for coordinate in coordinates:
        polygon_index = int(
            coordinate.get(
                "polygon_index",
                0,
            )
        )

        ring_index = int(
            coordinate.get(
                "ring_index",
                0,
            )
        )

        grouped.setdefault(
            polygon_index,
            {},
        ).setdefault(
            ring_index,
            [],
        ).append(
            coordinate
        )

    polygons: list[
        list[list[tuple[float, float]]]
    ] = []

    for polygon_index in sorted(
        grouped
    ):
        rings: list[
            list[tuple[float, float]]
        ] = []

        for ring_index in sorted(
            grouped[polygon_index]
        ):
            ring_records = sorted(
                grouped[
                    polygon_index
                ][
                    ring_index
                ],
                key=lambda item: int(
                    item.get(
                        "sequence_number",
                        0,
                    )
                ),
            )

            ring = [
                (
                    float(item["latitude"]),
                    float(item["longitude"]),
                )
                for item in ring_records
                if item.get("latitude") is not None
                and item.get("longitude") is not None
            ]

            if len(ring) >= 3:
                rings.append(
                    ring
                )

        if rings:
            polygons.append(
                rings
            )

    return polygons


def point_in_ring(
    lat: float,
    lon: float,
    ring: list[tuple[float, float]],
) -> bool:
    inside = False
    j = len(ring) - 1

    for i in range(
        len(ring)
    ):
        lat_i, lon_i = ring[i]
        lat_j, lon_j = ring[j]

        intersects = (
            (lon_i > lon)
            != (lon_j > lon)
        ) and (
            lat
            < (
                (lat_j - lat_i)
                * (lon - lon_i)
                / (
                    (lon_j - lon_i)
                    or 1e-12
                )
                + lat_i
            )
        )

        if intersects:
            inside = not inside

        j = i

    return inside


def point_in_polygon(
    lat: float,
    lon: float,
    rings: list[list[tuple[float, float]]],
) -> bool:
    if not rings:
        return False

    outer = rings[0]
    holes = rings[1:]

    if not point_in_ring(
        lat,
        lon,
        outer,
    ):
        return False

    for hole in holes:
        if point_in_ring(
            lat,
            lon,
            hole,
        ):
            return False

    return True


def point_in_polygons(
    lat: float,
    lon: float,
    polygons: list[list[list[tuple[float, float]]]],
) -> bool:
    return any(
        point_in_polygon(
            lat,
            lon,
            rings,
        )
        for rings in polygons
    )


def cell_center(
    h3_cell: str,
) -> tuple[float, float] | None:
    try:
        lat, lon = h3.cell_to_latlng(
            h3_cell
        )

        return (
            float(lat),
            float(lon),
        )
    except Exception:
        return None


def cell_touches_geometry(
    h3_cell: str,
    polygons: list[list[list[tuple[float, float]]]],
) -> bool:
    center = cell_center(
        h3_cell
    )

    if center and point_in_polygons(
        center[0],
        center[1],
        polygons,
    ):
        return True

    try:
        boundary = h3.cell_to_boundary(
            h3_cell
        )
    except Exception:
        return False

    for lat, lon in boundary:
        if point_in_polygons(
            float(lat),
            float(lon),
            polygons,
        ):
            return True

    return False


def time_overlap_status(
    *,
    projection: dict[str, Any],
    hazard: dict[str, Any],
) -> str:
    projection_start = projection.get(
        "generated_at_epoch"
    )

    projection_end = projection.get(
        "valid_until_epoch"
    )

    hazard_start = hazard.get(
        "valid_from_epoch"
    )

    hazard_end = hazard.get(
        "valid_to_epoch"
    )

    if (
        projection_start is None
        or projection_end is None
        or hazard_start is None
        or hazard_end is None
    ):
        return "UNKNOWN"

    if (
        int(hazard_start)
        <= int(projection_end)
        and int(hazard_end)
        >= int(projection_start)
    ):
        return "OVERLAP"

    return "NO_OVERLAP"


def _optional_float(
    value: Any,
) -> float | None:
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def altitude_overlap_status(
    *,
    projection: dict[str, Any],
    hazard: dict[str, Any],
) -> str:
    """
    Compare the aircraft's current altitude to the hazard altitude band
    when both are actually present.

    Missing or unusable bounds stay UNKNOWN. This never infers overlap
    from horizontal geometry alone.
    """

    aircraft_altitude = _optional_float(
        projection.get("current_altitude_ft")
    )

    if aircraft_altitude is None or aircraft_altitude < 0:
        return "UNKNOWN"

    lower = _optional_float(
        hazard.get("minimum_lower_altitude_ft")
    )

    upper = _optional_float(
        hazard.get("maximum_upper_altitude_ft")
    )

    if lower is None and upper is None:
        return "UNKNOWN"

    if lower is not None and upper is not None and lower > upper:
        return "UNKNOWN"

    if lower is None:
        return (
            "OVERLAP"
            if aircraft_altitude <= upper
            else "NO_OVERLAP"
        )

    if upper is None:
        return (
            "OVERLAP"
            if aircraft_altitude >= lower
            else "NO_OVERLAP"
        )

    if lower <= aircraft_altitude <= upper:
        return "OVERLAP"

    return "NO_OVERLAP"


def evaluate_geometry_overlap(
    *,
    projection: dict[str, Any],
    matched_h3_cells: list[str],
    hazard_coordinates: list[dict[str, Any]],
) -> dict[str, Any]:
    if not hazard_coordinates:
        return {
            "geometry_overlap_status": "UNKNOWN",
            "corridor_intersects": False,
            "centerline_intersects": False,
            "inside_now": False,
            "exact_intersection_confirmed": False,
        }

    polygons = group_hazard_coordinates(
        hazard_coordinates
    )

    if not polygons:
        return {
            "geometry_overlap_status": "UNKNOWN",
            "corridor_intersects": False,
            "centerline_intersects": False,
            "inside_now": False,
            "exact_intersection_confirmed": False,
        }

    current_aircraft_h3_cell = projection.get(
        "current_aircraft_h3_cell"
    )

    inside_now = False

    if current_aircraft_h3_cell:
        center = cell_center(
            str(current_aircraft_h3_cell)
        )

        if center:
            inside_now = point_in_polygons(
                center[0],
                center[1],
                polygons,
            )

    corridor_intersects = any(
        cell_touches_geometry(
            h3_cell,
            polygons,
        )
        for h3_cell in matched_h3_cells
    )

    if inside_now:
        geometry_overlap_status = "INSIDE_NOW"
    elif corridor_intersects:
        geometry_overlap_status = (
            "CORRIDOR_ONLY_INTERSECTION"
        )
    else:
        geometry_overlap_status = "UNKNOWN"

    return {
        "geometry_overlap_status": (
            geometry_overlap_status
        ),
        "corridor_intersects": (
            corridor_intersects
        ),
        "centerline_intersects": False,
        "inside_now": inside_now,
        "exact_intersection_confirmed": (
            corridor_intersects
            or inside_now
        ),
    }


def build_encounter_item(
    *,
    projection: dict[str, Any],
    hazard: dict[str, Any],
    candidate: dict[str, Any],
    matched_h3_cells: list[str],
    geometry_result: dict[str, Any],
    detected_epoch: int,
) -> dict[str, Any]:
    hazard_version_key = str(
        candidate[
            "hazard_version_key"
        ]
    )

    encounter_id = (
        f"{projection['projection_id']}#"
        f"{hazard_version_key}"
    )

    detected_utc = epoch_to_utc(
        detected_epoch
    )

    time_status = time_overlap_status(
        projection=projection,
        hazard=hazard,
    )

    altitude_status = altitude_overlap_status(
        projection=projection,
        hazard=hazard,
    )

    # Horizontal/temporal confirmation is independent of altitude.
    # UNKNOWN altitude must not invent overlap, and NO_OVERLAP must not
    # erase a confirmed corridor/inside relationship.
    exact_intersection_confirmed = (
        bool(
            geometry_result.get(
                "exact_intersection_confirmed"
            )
        )
        and time_status == "OVERLAP"
    )

    encounter_state = (
        "DETECTED"
        if exact_intersection_confirmed
        else "MONITORING"
    )

    expires_at_epoch = max(
        int(
            projection.get(
                "valid_until_epoch",
                detected_epoch,
            )
        ),
        int(
            hazard.get(
                "valid_to_epoch",
                detected_epoch,
            )
        ),
    ) + AHE_RETENTION_SECONDS

    item = {
        "encounter_id": encounter_id,
        "aircraft_id": projection["aircraft_id"],
        "aircraft_state_version": (
            projection[
                "aircraft_state_version"
            ]
        ),
        "projection_id": projection["projection_id"],
        "hazard_id": hazard["hazard_id"],
        "hazard_source_version": (
            hazard["source_version"]
        ),
        "hazard_version_key": hazard_version_key,
        "geometry_hash": hazard.get(
            "geometry_hash",
            "UNKNOWN",
        ),
        "projection_generated_at_utc": (
            projection[
                "generated_at_utc"
            ]
        ),
        "detected_at_epoch": detected_epoch,
        "detected_at_utc": detected_utc,
        "matched_h3_cells": matched_h3_cells[
            :MAX_MATCHED_H3_CELLS
        ],
        "matched_h3_cell_count": len(
            matched_h3_cells
        ),
        "candidate_reason": (
            "PROJECTION_CORRIDOR_CELL_"
            "OVERLAPS_HAZARD_CELL"
        ),
        "hazard_type": hazard.get(
            "hazard_type",
            "UNKNOWN",
        ),
        "severity": hazard.get(
            "severity"
        ),
        "geometry_overlap_status": (
            geometry_result[
                "geometry_overlap_status"
            ]
        ),
        "time_overlap_status": time_status,
        "altitude_overlap_status": (
            altitude_status
        ),
        "corridor_intersects": (
            geometry_result[
                "corridor_intersects"
            ]
        ),
        "centerline_intersects": (
            geometry_result[
                "centerline_intersects"
            ]
        ),
        "inside_now": (
            geometry_result[
                "inside_now"
            ]
        ),
        "exact_intersection_confirmed": (
            exact_intersection_confirmed
        ),
        "trajectory_confidence": projection.get(
            "confidence",
            "UNKNOWN",
        ),
        "freshness_status": projection.get(
            "freshness_status"
        ),
        "current_altitude_ft": projection.get(
            "current_altitude_ft"
        ),
        "encounter_state": encounter_state,
        "evaluation_method": (
            "H3_MATCH_PLUS_HAZARD_POLYGON_CELL_CHECK"
        ),
        "valid_from_utc": hazard.get(
            "valid_from_utc"
        ),
        "valid_to_utc": hazard.get(
            "valid_to_utc"
        ),
        "correlation_id": projection.get(
            "correlation_id"
        )
        or hazard.get(
            "correlation_id"
        )
        or encounter_id,
        "schema_version": AHE_SCHEMA_VERSION,
        "expires_at_epoch": expires_at_epoch,
    }

    return {
        key: value
        for key, value in item.items()
        if value is not None
    }


def write_encounter(
    item: dict[str, Any],
) -> None:
    encounter_table.put_item(
        Item=item
    )


def get_current_projection_for_aircraft(
    aircraft_id: str,
    current_epoch: int,
) -> dict[str, Any] | None:
    try:
        response = projection_table.query(
            IndexName=(
                AIRCRAFT_PROJECTION_AIRCRAFT_INDEX_NAME
            ),
            KeyConditionExpression=Key(
                "aircraft_id"
            ).eq(
                aircraft_id
            ),
            ScanIndexForward=False,
            Limit=20,
        )
    except ClientError as exc:
        code = (
            exc.response
            .get("Error", {})
            .get("Code")
        )

        if code in {
            "AccessDeniedException",
            "UnrecognizedClientException",
        }:
            return None

        raise

    for item in response.get("Items", []):
        ready, _reason = projection_is_ready(
            item,
            current_epoch=current_epoch,
        )

        if ready:
            return item

    return None


def projection_is_operationally_current(
    projection: dict[str, Any],
    current_epoch: int,
) -> bool:
    aircraft_id = str(
        projection.get(
            "aircraft_id",
            "",
        )
    ).strip()

    if not aircraft_id:
        return False

    current = get_current_projection_for_aircraft(
        aircraft_id,
        current_epoch,
    )

    if current is None:
        return True

    return (
        str(current.get("projection_id"))
        == str(projection.get("projection_id"))
    )


def query_encounters_for_aircraft(
    aircraft_id: str,
) -> list[dict[str, Any]]:
    try:
        return paged_query(
            encounter_table,
            IndexName=ENCOUNTER_AIRCRAFT_INDEX_NAME,
            KeyConditionExpression=Key(
                "aircraft_id"
            ).eq(
                aircraft_id
            ),
        )
    except ClientError as exc:
        code = (
            exc.response
            .get("Error", {})
            .get("Code")
        )

        if code in {
            "AccessDeniedException",
            "UnrecognizedClientException",
        }:
            return []

        raise


def persist_resolved_encounter(
    item: dict[str, Any],
) -> bool:
    try:
        encounter_table.put_item(
            Item=item,
            ConditionExpression=(
                "encounter_state IN "
                "(:detected, :monitoring)"
            ),
            ExpressionAttributeValues={
                ":detected": "DETECTED",
                ":monitoring": "MONITORING",
            },
        )
        return True
    except ClientError as exc:
        code = (
            exc.response
            .get("Error", {})
            .get("Code")
        )

        if code == "ConditionalCheckFailedException":
            return False

        raise


def resolve_encounter(
    existing: dict[str, Any],
    *,
    encounter_state: str,
    reason: str,
    current_epoch: int,
) -> bool:
    state = str(
        existing.get(
            "encounter_state",
            "",
        )
    ).upper()

    if state in TERMINAL_ENCOUNTER_STATES:
        return False

    if state not in CURRENT_ENCOUNTER_STATES:
        return False

    item = dict(existing)
    item["encounter_state"] = encounter_state
    item["resolution_reason"] = reason
    item["resolved_at_epoch"] = current_epoch
    item["resolved_at_utc"] = epoch_to_utc(
        current_epoch
    )

    if not persist_resolved_encounter(item):
        return False

    publish_encounter_event(
        item=item,
        detail_type="encounter.resolved",
    )

    return True


def supersede_stale_encounters(
    *,
    aircraft_id: str,
    current_projection_id: str,
    written_encounter_ids: set[str],
    current_epoch: int,
    hazard_id_filter: str | None = None,
    full_evaluation: bool = True,
) -> int:
    resolved = 0

    for existing in query_encounters_for_aircraft(
        aircraft_id
    ):
        encounter_id = str(
            existing.get(
                "encounter_id",
                "",
            )
        )

        if not encounter_id or encounter_id in written_encounter_ids:
            continue

        if str(
            existing.get(
                "encounter_state",
                "",
            )
        ).upper() not in CURRENT_ENCOUNTER_STATES:
            continue

        existing_hazard_id = str(
            existing.get(
                "hazard_id",
                "",
            )
        )

        if (
            hazard_id_filter
            and existing_hazard_id != hazard_id_filter
        ):
            continue

        existing_projection_id = str(
            existing.get(
                "projection_id",
                "",
            )
        )

        if existing_projection_id != current_projection_id:
            if resolve_encounter(
                existing,
                encounter_state="SUPERSEDED",
                reason=(
                    "Superseded by a newer current "
                    "aircraft projection."
                ),
                current_epoch=current_epoch,
            ):
                resolved += 1
            continue

        if hazard_id_filter:
            if resolve_encounter(
                existing,
                encounter_state="SUPERSEDED",
                reason=(
                    "Superseded by a newer hazard "
                    "source version or the current "
                    "projection no longer supports "
                    "this hazard version."
                ),
                current_epoch=current_epoch,
            ):
                resolved += 1
            continue

        if full_evaluation:
            if resolve_encounter(
                existing,
                encounter_state="RESOLVED",
                reason=(
                    "Current projection no longer "
                    "supports this aircraft-hazard "
                    "relationship."
                ),
                current_epoch=current_epoch,
            ):
                resolved += 1

    return resolved


def publish_encounter_event(
    *,
    item: dict[str, Any],
    detail_type: str = "encounter.updated",
) -> None:
    detail = {
        "encounter_id": item["encounter_id"],
        "aircraft_id": item["aircraft_id"],
        "aircraft_state_version": item.get(
            "aircraft_state_version"
        ),
        "projection_id": item["projection_id"],
        "hazard_id": item["hazard_id"],
        "hazard_source_version": item.get(
            "hazard_source_version"
        ),
        "hazard_version_key": item.get(
            "hazard_version_key"
        ),
        "encounter_state": item.get(
            "encounter_state"
        ),
        "geometry_overlap_status": item.get(
            "geometry_overlap_status"
        ),
        "time_overlap_status": item.get(
            "time_overlap_status"
        ),
        "altitude_overlap_status": item.get(
            "altitude_overlap_status"
        ),
        "exact_intersection_confirmed": item.get(
            "exact_intersection_confirmed"
        ),
        "detected_at_epoch": item.get(
            "detected_at_epoch"
        ),
        "detected_at_utc": item.get(
            "detected_at_utc"
        ),
        "resolution_reason": item.get(
            "resolution_reason"
        ),
        "correlation_id": item.get(
            "correlation_id"
        ),
        "schema_version": item.get(
            "schema_version"
        ),
    }

    detail = {
        key: value
        for key, value in detail.items()
        if value is not None
    }

    response = eventbridge.put_events(
        Entries=[
            {
                "EventBusName": EVENT_BUS_NAME,
                "Source": "wilvor.encounter",
                "DetailType": detail_type,
                "Detail": json.dumps(
                    detail,
                    default=json_default,
                    separators=(",", ":"),
                ),
            }
        ]
    )

    if int(
        response.get(
            "FailedEntryCount",
            0,
        )
        or 0
    ):
        raise RuntimeError(
            f"Failed to publish {detail_type}: {response}"
        )


def evaluate_projection(
    projection_id: str,
    *,
    hazard_version_key_filter: str | None = None,
) -> dict[str, Any]:
    current_epoch = now_epoch()

    projection = get_projection(
        projection_id
    )

    if projection is None:
        return {
            "projection_id": projection_id,
            "processed": False,
            "reason": "PROJECTION_NOT_FOUND",
            "encounters_written": 0,
        }

    ready, reason = projection_is_ready(
        projection,
        current_epoch=current_epoch,
    )

    if not ready:
        return {
            "projection_id": projection_id,
            "processed": False,
            "reason": reason,
            "encounters_written": 0,
        }

    if not projection_is_operationally_current(
        projection,
        current_epoch,
    ):
        return {
            "projection_id": projection_id,
            "processed": False,
            "reason": "PROJECTION_NOT_CURRENT",
            "encounters_written": 0,
        }

    projection_cells = query_projection_cells(
        projection_id
    )

    if not projection_cells:
        resolved = supersede_stale_encounters(
            aircraft_id=str(
                projection["aircraft_id"]
            ),
            current_projection_id=str(
                projection["projection_id"]
            ),
            written_encounter_ids=set(),
            current_epoch=current_epoch,
            hazard_id_filter=(
                hazard_version_key_filter.split("#", 1)[0]
                if hazard_version_key_filter
                else None
            ),
            full_evaluation=hazard_version_key_filter is None,
        )

        return {
            "projection_id": projection_id,
            "processed": True,
            "reason": "NO_PROJECTION_CELLS",
            "encounters_written": 0,
            "encounters_resolved": resolved,
        }

    candidates = collect_hazard_candidates(
        projection_cells,
        hazard_version_key_filter=(
            hazard_version_key_filter
        ),
    )

    if not candidates:
        resolved = supersede_stale_encounters(
            aircraft_id=str(
                projection["aircraft_id"]
            ),
            current_projection_id=str(
                projection["projection_id"]
            ),
            written_encounter_ids=set(),
            current_epoch=current_epoch,
            hazard_id_filter=(
                hazard_version_key_filter.split("#", 1)[0]
                if hazard_version_key_filter
                else None
            ),
            full_evaluation=hazard_version_key_filter is None,
        )

        return {
            "projection_id": projection_id,
            "processed": True,
            "reason": "NO_HAZARD_CELL_MATCH",
            "encounters_written": 0,
            "encounters_resolved": resolved,
            "projection_cells": len(
                projection_cells
            ),
        }

    encounters_written = 0
    exact_confirmed = 0
    skipped = 0
    written_encounter_ids: set[str] = set()

    for candidate in candidates.values():
        hazard_id = candidate.get(
            "hazard_id"
        )

        if not hazard_id:
            skipped += 1
            continue

        hazard = get_active_hazard(
            str(hazard_id)
        )

        matches, skip_reason = (
            hazard_matches_candidate(
                hazard=hazard,
                candidate=candidate,
                projection=projection,
            )
        )

        if not matches:
            skipped += 1
            continue

        hazard_coordinates = query_hazard_coordinates(
            str(
                candidate[
                    "hazard_version_key"
                ]
            )
        )

        matched_h3_cells = sorted(
            candidate[
                "matched_h3_cells"
            ]
        )

        geometry_result = evaluate_geometry_overlap(
            projection=projection,
            matched_h3_cells=matched_h3_cells,
            hazard_coordinates=hazard_coordinates,
        )

        item = build_encounter_item(
            projection=projection,
            hazard=hazard,
            candidate=candidate,
            matched_h3_cells=matched_h3_cells,
            geometry_result=geometry_result,
            detected_epoch=current_epoch,
        )

        write_encounter(
            item
        )

        publish_encounter_event(
            item=item,
            detail_type="encounter.updated",
        )

        written_encounter_ids.add(
            str(item["encounter_id"])
        )

        encounters_written += 1

        if item[
            "exact_intersection_confirmed"
        ]:
            exact_confirmed += 1

    resolved = supersede_stale_encounters(
        aircraft_id=str(
            projection["aircraft_id"]
        ),
        current_projection_id=str(
            projection["projection_id"]
        ),
        written_encounter_ids=written_encounter_ids,
        current_epoch=current_epoch,
        hazard_id_filter=(
            hazard_version_key_filter.split("#", 1)[0]
            if hazard_version_key_filter
            else None
        ),
        full_evaluation=hazard_version_key_filter is None,
    )

    return {
        "projection_id": projection_id,
        "processed": True,
        "reason": "EVALUATED",
        "projection_cells": len(
            projection_cells
        ),
        "hazard_candidates": len(
            candidates
        ),
        "encounters_written": (
            encounters_written
        ),
        "encounters_resolved": resolved,
        "exact_confirmed": exact_confirmed,
        "skipped_candidates": skipped,
    }


def projection_ids_for_hazard_version(
    hazard_version_key: str,
) -> list[str]:
    hazard_cells = (
        query_hazard_cells_by_hazard_version(
            hazard_version_key
        )
    )

    projection_ids: set[str] = set()

    for hazard_cell in hazard_cells:
        h3_cell = str(
            hazard_cell[
                "h3_cell"
            ]
        )

        projection_cells = (
            query_projection_cells_by_h3(
                h3_cell
            )
        )

        for projection_cell in projection_cells:
            projection_id = projection_cell.get(
                "projection_id"
            )

            if projection_id:
                projection_ids.add(
                    str(projection_id)
                )

    return sorted(
        projection_ids
    )


def handle_projection_ready(
    detail: dict[str, Any],
) -> dict[str, Any]:
    projection_id = str(
        detail.get(
            "projection_id",
            "",
        )
    ).strip()

    if not projection_id:
        raise ValueError(
            "projection.ready missing projection_id"
        )

    return evaluate_projection(
        projection_id
    )


def handle_hazard_materialized(
    detail: dict[str, Any],
) -> dict[str, Any]:
    hazard_version_key = str(
        detail.get(
            "hazard_version_key",
            "",
        )
    ).strip()

    if not hazard_version_key:
        hazard_id = str(
            detail.get(
                "hazard_id",
                "",
            )
        ).strip()

        source_version = str(
            detail.get(
                "source_version",
                "",
            )
        ).strip()

        if hazard_id and source_version:
            hazard_version_key = (
                hazard_version_key_from_parts(
                    hazard_id,
                    source_version,
                )
            )

    if not hazard_version_key:
        raise ValueError(
            "hazard.materialized missing hazard_version_key"
        )

    projection_ids = projection_ids_for_hazard_version(
        hazard_version_key
    )

    results = []

    for projection_id in projection_ids:
        results.append(
            evaluate_projection(
                projection_id,
                hazard_version_key_filter=(
                    hazard_version_key
                ),
            )
        )

    return {
        "hazard_version_key": hazard_version_key,
        "projection_candidates": len(
            projection_ids
        ),
        "results": results,
        "encounters_written": sum(
            int(
                result.get(
                    "encounters_written",
                    0,
                )
            )
            for result in results
        ),
        "exact_confirmed": sum(
            int(
                result.get(
                    "exact_confirmed",
                    0,
                )
            )
            for result in results
        ),
    }


def lambda_handler(
    event,
    context,
):
    detail_type = event.get(
        "detail-type"
    ) or event.get(
        "detailType"
    )

    detail = event.get(
        "detail",
        {},
    )

    if isinstance(
        detail,
        str,
    ):
        detail = json.loads(
            detail
        )

    if detail_type == "projection.ready":
        result = handle_projection_ready(
            detail
        )

    elif detail_type == "hazard.materialized":
        result = handle_hazard_materialized(
            detail
        )

    else:
        raise ValueError(
            f"Unsupported EventBridge detail-type: {detail_type}"
        )

    emit_metrics(
        {
            "ProjectionCandidates": int(
                result.get(
                    "projection_candidates",
                    1
                    if detail_type
                    == "projection.ready"
                    else 0,
                )
            ),
            "HazardCandidates": int(
                result.get(
                    "hazard_candidates",
                    0,
                )
            ),
            "EncountersWritten": int(
                result.get(
                    "encounters_written",
                    0,
                )
            ),
            "ExactConfirmed": int(
                result.get(
                    "exact_confirmed",
                    0,
                )
            ),
            "NoCandidates": (
                1
                if int(
                    result.get(
                        "encounters_written",
                        0,
                    )
                )
                == 0
                else 0
            ),
        }
    )

    print(
        json.dumps(
            {
                "message": (
                    "AircraftHazardEncounter evaluation complete"
                ),
                "detail_type": detail_type,
                "result": result,
            },
            default=json_default,
            separators=(",", ":"),
        )
    )

    return {
        "ok": True,
        "detail_type": detail_type,
        "result": result,
    }