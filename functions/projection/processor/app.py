import json
import os
from datetime import datetime, timezone
from typing import Any
import hashlib
import math
import json
from decimal import Decimal

import h3

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError


dynamodb = boto3.resource("dynamodb")
cloudwatch = boto3.client("cloudwatch")
eventbridge = boto3.client("events")


ENVIRONMENT = os.environ.get(
    "ENVIRONMENT",
    "dev",
)

AIRCRAFT_CURRENT_STATE_TABLE_NAME = os.environ[
    "AIRCRAFT_CURRENT_STATE_TABLE_NAME"
]

IMPACT_CELLS_TABLE_NAME = os.environ[
    "IMPACT_CELLS_TABLE_NAME"
]

ACTIVE_HAZARDS_TABLE_NAME = os.environ[
    "ACTIVE_HAZARDS_TABLE_NAME"
]

EVENT_BUS_NAME = os.environ.get(
    "EVENT_BUS_NAME",
    "default",
)

AIRCRAFT_PROJECTION_TABLE_NAME = os.environ[
    "AIRCRAFT_PROJECTION_TABLE_NAME"
]

AIRCRAFT_PROJECTION_POINTS_TABLE_NAME = os.environ[
    "AIRCRAFT_PROJECTION_POINTS_TABLE_NAME"
]

AIRCRAFT_PROJECTION_CELLS_TABLE_NAME = os.environ[
    "AIRCRAFT_PROJECTION_CELLS_TABLE_NAME"
]

PROJECTION_ALGORITHM_VERSION = os.environ[
    "PROJECTION_ALGORITHM_VERSION"
]

PROJECTION_CONFIG_VERSION = os.environ[
    "PROJECTION_CONFIG_VERSION"
]

PROJECTION_SCHEMA_VERSION = os.environ[
    "PROJECTION_SCHEMA_VERSION"
]

PROJECTION_POINTS_SCHEMA_VERSION = os.environ[
    "PROJECTION_POINTS_SCHEMA_VERSION"
]

PROJECTION_CELLS_SCHEMA_VERSION = os.environ[
    "PROJECTION_CELLS_SCHEMA_VERSION"
]

PROJECTION_RETENTION_SECONDS = int(
    os.environ.get(
        "PROJECTION_RETENTION_SECONDS",
        "3600",
    )
)

MAX_CORRIDOR_CELLS = int(
    os.environ.get(
        "MAX_CORRIDOR_CELLS",
        "2000",
    )
)

MAX_TRIGGER_HAZARDS = int(
    os.environ.get(
        "MAX_TRIGGER_HAZARDS",
        "25",
    )
)

PROJECTION_HORIZONS_MIN = tuple(
    int(value)
    for value in os.environ.get(
        "PROJECTION_HORIZONS_MIN",
        "5,10,15,30",
    ).split(",")
)

CORRIDOR_GRID_DISTANCES = tuple(
    int(value)
    for value in os.environ.get(
        "CORRIDOR_GRID_DISTANCES",
        "0,0,1,1",
    ).split(",")
)

if len(PROJECTION_HORIZONS_MIN) != len(
    CORRIDOR_GRID_DISTANCES
):
    raise RuntimeError(
        "Projection horizons and corridor distances must match."
    )

EARTH_RADIUS_NM = 3440.065

MAX_POSITION_AGE_SECONDS = int(
    os.environ.get(
        "MAX_POSITION_AGE_SECONDS",
        "180",
    )
)

REQUIRE_AIRBORNE = (
    os.environ.get(
        "REQUIRE_AIRBORNE",
        "true",
    ).lower()
    == "true"
)


aircraft_state_table = dynamodb.Table(
    AIRCRAFT_CURRENT_STATE_TABLE_NAME
)

impact_cells_table = dynamodb.Table(
    IMPACT_CELLS_TABLE_NAME
)

active_hazards_table = dynamodb.Table(
    ACTIVE_HAZARDS_TABLE_NAME
)

projection_table = dynamodb.Table(
    AIRCRAFT_PROJECTION_TABLE_NAME
)

projection_points_table = dynamodb.Table(
    AIRCRAFT_PROJECTION_POINTS_TABLE_NAME
)

projection_cells_table = dynamodb.Table(
    AIRCRAFT_PROJECTION_CELLS_TABLE_NAME
)

def now_epoch() -> int:
    return int(
        datetime.now(
            timezone.utc
        ).timestamp()
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

        return int(parsed.timestamp())

    except (
        TypeError,
        ValueError,
    ):
        return None


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
                        "Value": "projection",
                    },
                    {
                        "Name": "Component",
                        "Value": "projection_processor",
                    },
                    {
                        "Name": "Stage",
                        "Value": "eligibility",
                    },
                ],
            }
            for name, value in metrics.items()
        ],
    )


def get_aircraft_state(
    aircraft_id: str,
) -> dict[str, Any] | None:
    response = (
        aircraft_state_table.get_item(
            Key={
                "aircraft_id": aircraft_id
            },
            ConsistentRead=True,
        )
    )

    return response.get("Item")


def query_impact_cells(
    h3_cell: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    response = impact_cells_table.query(
        KeyConditionExpression=(
            Key("h3_cell").eq(h3_cell)
        )
    )

    items.extend(
        response.get("Items", [])
    )

    while (
        response.get(
            "LastEvaluatedKey"
        )
        is not None
    ):
        response = impact_cells_table.query(
            KeyConditionExpression=(
                Key("h3_cell").eq(
                    h3_cell
                )
            ),
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


def get_active_hazard(
    hazard_id: str,
) -> dict[str, Any] | None:
    response = (
        active_hazards_table.get_item(
            Key={
                "hazard_id": hazard_id
            },
            ConsistentRead=True,
        )
    )

    return response.get("Item")


def aircraft_state_is_usable(
    state: dict[str, Any],
    *,
    current_epoch: int,
) -> tuple[bool, str | None]:
    if not state.get(
        "has_position"
    ):
        return (
            False,
            "NO_USABLE_POSITION",
        )

    if not state.get(
        "current_h3_cell"
    ):
        return (
            False,
            "NO_CURRENT_H3_CELL",
        )

    if (
        state.get(
            "freshness_status"
        )
        not in {
            "FRESH",
            "ACCEPTABLE",
        }
    ):
        return (
            False,
            "AIRCRAFT_STATE_NOT_FRESH",
        )

    position_time = state.get(
        "position_time_epoch"
    )

    if position_time is None:
        return (
            False,
            "NO_POSITION_TIME",
        )

    position_age = (
        current_epoch
        - int(position_time)
    )

    if (
        position_age < 0
        or position_age
        > MAX_POSITION_AGE_SECONDS
    ):
        return (
            False,
            "POSITION_TOO_OLD",
        )

    if (
        REQUIRE_AIRBORNE
        and state.get("on_ground")
        is True
    ):
        return (
            False,
            "AIRCRAFT_ON_GROUND",
        )

    # Required later for simple
    # motion-vector projection.
    if (
        state.get(
            "ground_speed_kt"
        )
        is None
    ):
        return (
            False,
            "GROUND_SPEED_UNAVAILABLE",
        )

    if (
        state.get(
            "track_deg"
        )
        is None
    ):
        return (
            False,
            "TRACK_UNAVAILABLE",
        )

    return True, None


def impact_candidate_is_current(
    impact: dict[str, Any],
    *,
    aircraft_h3_resolution: int,
    current_epoch: int,
) -> bool:
    if (
        impact.get(
            "impact_scope"
        )
        != "PROJECTION_TRIGGER_AREA"
    ):
        return False

    impact_resolution = impact.get(
        "h3_resolution"
    )

    if impact_resolution is None:
        return False

    if (
        int(impact_resolution)
        != int(
            aircraft_h3_resolution
        )
    ):
        return False

    valid_from = parse_iso_epoch(
        impact.get(
            "valid_from_utc"
        )
    )

    valid_to = parse_iso_epoch(
        impact.get(
            "valid_to_utc"
        )
    )

    if (
        valid_from is None
        or valid_to is None
    ):
        return False

    if not (
        valid_from
        <= current_epoch
        <= valid_to
    ):
        return False

    return True


def hazard_matches_impact(
    hazard: dict[str, Any] | None,
    impact: dict[str, Any],
    *,
    current_epoch: int,
) -> bool:
    if not hazard:
        return False

    if (
        hazard.get("status")
        != "ACTIVE"
    ):
        return False

    if (
        hazard.get(
            "materialization_status"
        )
        != "READY"
    ):
        return False

    if (
        hazard.get(
            "source_version"
        )
        != impact.get(
            "hazard_source_version"
        )
    ):
        return False

    impact_materialization = (
        impact.get(
            "materialization_id"
        )
    )

    hazard_materialization = (
        hazard.get(
            "materialization_id"
        )
    )

    if (
        impact_materialization
        != hazard_materialization
    ):
        return False

    valid_from = hazard.get(
        "valid_from_epoch"
    )

    valid_to = hazard.get(
        "valid_to_epoch"
    )

    if (
        valid_from is None
        or valid_to is None
    ):
        return False

    if not (
        int(valid_from)
        <= current_epoch
        <= int(valid_to)
    ):
        return False

    return True


def evaluate_eligibility(
    detail: dict[str, Any],
) -> dict[str, Any]:
    aircraft_id = str(
        detail.get(
            "aircraft_id",
            "",
        )
    ).strip()

    event_state_version = str(
        detail.get(
            "state_version",
            "",
        )
    ).strip()

    if (
        not aircraft_id
        or not event_state_version
    ):
        raise ValueError(
            "aircraft.state.updated "
            "missing aircraft_id or "
            "state_version"
        )

    state = get_aircraft_state(
        aircraft_id
    )

    if state is None:
        return {
            "eligible": False,
            "reason": (
                "AIRCRAFT_STATE_NOT_FOUND"
            ),
            "aircraft_id": aircraft_id,
        }

    current_state_version = str(
        state.get(
            "state_version",
            "",
        )
    )

    # EventBridge delivery can be
    # delayed/out of order. Never
    # project an older state.
    if (
        current_state_version
        != event_state_version
    ):
        return {
            "eligible": False,
            "reason": (
                "STALE_EVENT_VERSION"
            ),
            "aircraft_id": aircraft_id,
            "event_state_version": (
                event_state_version
            ),
            "current_state_version": (
                current_state_version
            ),
        }

    current_epoch = now_epoch()

    usable, reason = (
        aircraft_state_is_usable(
            state,
            current_epoch=current_epoch,
        )
    )

    if not usable:
        return {
            "eligible": False,
            "reason": reason,
            "aircraft_id": aircraft_id,
            "state_version": (
                current_state_version
            ),
        }

    current_h3_cell = str(
        state["current_h3_cell"]
    )

    candidates = query_impact_cells(
        current_h3_cell
    )

    valid_matches: list[
        dict[str, Any]
    ] = []

    seen_hazard_versions: set[
        str
    ] = set()

    for impact in candidates:
        if not impact_candidate_is_current(
            impact,
            aircraft_h3_resolution=int(
                state[
                    "h3_resolution"
                ]
            ),
            current_epoch=current_epoch,
        ):
            continue

        hazard_id = str(
            impact.get(
                "hazard_id",
                "",
            )
        ).strip()

        if not hazard_id:
            continue

        hazard = get_active_hazard(
            hazard_id
        )

        if not hazard_matches_impact(
            hazard,
            impact,
            current_epoch=current_epoch,
        ):
            continue

        hazard_version_key = str(
            impact.get(
                "hazard_version_key",
                "",
            )
        )

        if (
            hazard_version_key
            in seen_hazard_versions
        ):
            continue

        seen_hazard_versions.add(
            hazard_version_key
        )

        valid_matches.append(
            impact
        )

    if not valid_matches:
        return {
            "eligible": False,
            "reason": (
                "NO_CURRENT_IMPACT_MATCH"
            ),
            "aircraft_id": aircraft_id,
            "state_version": (
                current_state_version
            ),
            "current_h3_cell": (
                current_h3_cell
            ),
            "impact_candidates_found": (
                len(candidates)
            ),
        }

    trigger_hazard_ids = sorted(
        {
            str(
                item["hazard_id"]
            )
            for item in valid_matches
        }
    )

    hazard_version_keys = sorted(
        {
            str(
                item[
                    "hazard_version_key"
                ]
            )
            for item in valid_matches
        }
    )

    return {
        "eligible": True,
        "reason": (
            "CURRENT_IMPACT_MATCH"
        ),
        "projection_trigger_reason": (
            "AIRCRAFT_STATE_CHANGED"
        ),
        "aircraft_id": aircraft_id,
        "aircraft_state_version": (
            current_state_version
        ),
        "source_position_time_epoch": (
            int(
                state[
                    "position_time_epoch"
                ]
            )
        ),
        "current_h3_cell": (
            current_h3_cell
        ),
        "matched_impact_cells": [
            current_h3_cell
        ],
        "trigger_hazard_ids": (
            trigger_hazard_ids
        ),
        "trigger_hazard_version_keys": (
            hazard_version_keys
        ),
        "impact_candidates_found": (
            len(candidates)
        ),
        "valid_impact_matches": (
            len(valid_matches)
        ),
        "correlation_id": (
            detail.get(
                "correlation_id"
            )
            or state.get(
                "correlation_id"
            )
        ),
    }

def epoch_to_utc(epoch: int) -> str:
    return (
        datetime.fromtimestamp(
            epoch,
            tz=timezone.utc,
        )
        .isoformat()
        .replace("+00:00", "Z")
    )


def decimal_number(
    value: float,
    places: int = 8,
) -> Decimal:
    return Decimal(
        str(round(value, places))
    )


def destination_point(
    latitude: float,
    longitude: float,
    track_deg: float,
    distance_nm: float,
) -> tuple[float, float]:
    """
    Project a point along the aircraft's current track.
    """

    lat1 = math.radians(latitude)
    lon1 = math.radians(longitude)
    bearing = math.radians(track_deg % 360.0)

    angular_distance = (
        distance_nm / EARTH_RADIUS_NM
    )

    lat2 = math.asin(
        math.sin(lat1)
        * math.cos(angular_distance)
        + math.cos(lat1)
        * math.sin(angular_distance)
        * math.cos(bearing)
    )

    lon2 = lon1 + math.atan2(
        math.sin(bearing)
        * math.sin(angular_distance)
        * math.cos(lat1),
        math.cos(angular_distance)
        - math.sin(lat1)
        * math.sin(lat2),
    )

    lon2 = (
        (lon2 + math.pi)
        % (2 * math.pi)
        - math.pi
    )

    return (
        math.degrees(lat2),
        math.degrees(lon2),
    )


def point_confidence(
    horizon_min: int,
) -> str:
    if horizon_min <= 10:
        return "HIGH"

    if horizon_min <= 20:
        return "MEDIUM"

    return "LOW"


def current_altitude_ft(
    state: dict[str, Any],
) -> float | None:
    if state.get("baro_altitude_ft") is not None:
        return float(
            state["baro_altitude_ft"]
        )

    if state.get("geo_altitude_ft") is not None:
        return float(
            state["geo_altitude_ft"]
        )

    return None

def generate_projection_points(
    *,
    state: dict[str, Any],
    projection_id: str,
    generated_at_epoch: int,
    generated_at_utc: str,
    expires_at_epoch: int,
    correlation_id: str,
) -> list[dict[str, Any]]:

    latitude = float(
        state["latitude"]
    )

    longitude = float(
        state["longitude"]
    )

    speed_kt = float(
        state["ground_speed_kt"]
    )

    track_deg = float(
        state["track_deg"]
    )

    source_position_time_epoch = int(
        state["position_time_epoch"]
    )

    h3_resolution = int(
        state["h3_resolution"]
    )

    #
    # The aircraft position may already be
    # several seconds old when projection starts.
    #
    position_age_seconds = max(
        0,
        generated_at_epoch
        - source_position_time_epoch,
    )

    altitude_ft = current_altitude_ft(
        state
    )

    vertical_rate_fpm = None

    if (
        state.get("vertical_rate_fpm")
        is not None
    ):
        vertical_rate_fpm = float(
            state["vertical_rate_fpm"]
        )

    points = []

    for sequence, horizon_min in enumerate(
        PROJECTION_HORIZONS_MIN,
        start=1,
    ):
        #
        # Project from the timestamp of the
        # actual observed aircraft position.
        #
        elapsed_minutes = (
            position_age_seconds / 60.0
            + horizon_min
        )

        distance_nm = (
            max(speed_kt, 0.0)
            * elapsed_minutes
            / 60.0
        )

        (
            projected_lat,
            projected_lon,
        ) = destination_point(
            latitude,
            longitude,
            track_deg,
            distance_nm,
        )

        projected_time_epoch = (
            generated_at_epoch
            + horizon_min * 60
        )

        point = {
            "projection_id": projection_id,

            "point_key": (
                f"S#{sequence:06d}"
                f"#H#{horizon_min:04d}"
            ),

            "point_sequence_number": sequence,

            "aircraft_id": (
                state["aircraft_id"]
            ),

            "aircraft_state_version": (
                state["state_version"]
            ),

            "source_position_time_epoch": (
                source_position_time_epoch
            ),

            "generated_at_utc": (
                generated_at_utc
            ),

            "horizon_min": horizon_min,

            "projected_time_epoch": (
                projected_time_epoch
            ),

            "projected_time_utc": (
                epoch_to_utc(
                    projected_time_epoch
                )
            ),

            "latitude": decimal_number(
                projected_lat
            ),

            "longitude": decimal_number(
                projected_lon
            ),

            "confidence": (
                point_confidence(
                    horizon_min
                )
            ),

            "h3_cell": (
                h3.latlng_to_cell(
                    projected_lat,
                    projected_lon,
                    h3_resolution,
                )
            ),

            "projection_algorithm_version": (
                PROJECTION_ALGORITHM_VERSION
            ),

            "projection_config_version": (
                PROJECTION_CONFIG_VERSION
            ),

            "correlation_id": (
                correlation_id
            ),

            "schema_version": (
                PROJECTION_POINTS_SCHEMA_VERSION
            ),

            "expires_at_epoch": (
                expires_at_epoch
            ),
        }

        #
        # Altitude is optional in the schema.
        #
        if altitude_ft is not None:
            estimated_altitude_ft = (
                altitude_ft
            )

            if vertical_rate_fpm is not None:
                estimated_altitude_ft += (
                    vertical_rate_fpm
                    * elapsed_minutes
                )

            point[
                "estimated_altitude_ft"
            ] = decimal_number(
                estimated_altitude_ft,
                places=2,
            )

        points.append(point)

    return points

def corridor_path(
    start_cell: str,
    end_cell: str,
) -> list[str]:
    if start_cell == end_cell:
        return [start_cell]

    try:
        return list(
            h3.grid_path_cells(
                start_cell,
                end_cell,
            )
        )
    except Exception as exc:
        raise RuntimeError(
            "Unable to build H3 corridor "
            f"{start_cell} -> {end_cell}"
        ) from exc


def generate_corridor_cells(
    current_h3_cell: str,
    points: list[dict[str, Any]],
) -> list[str]:

    cells = {current_h3_cell}

    previous_cell = current_h3_cell

    for point, grid_distance in zip(
        points,
        CORRIDOR_GRID_DISTANCES,
    ):
        point_cell = str(
            point["h3_cell"]
        )

        path_cells = corridor_path(
            previous_cell,
            point_cell,
        )

        for path_cell in path_cells:
            expanded_cells = h3.grid_disk(
                path_cell,
                grid_distance,
            )

            cells.update(
                expanded_cells
            )

            if len(cells) > MAX_CORRIDOR_CELLS:
                raise RuntimeError(
                    "Projection corridor exceeds "
                    f"{MAX_CORRIDOR_CELLS} H3 cells."
                )

        previous_cell = point_cell

    return sorted(cells)

def generate_projection_cells(
    *,
    projection_id: str,
    aircraft_id: str,
    h3_resolution: int,
    generated_at_utc: str,
    valid_until_utc: str,
    correlation_id: str,
    expires_at_epoch: int,
    cell_ids: list[str],
) -> list[dict[str, Any]]:

    cells = []

    for cell_id in cell_ids:
        cells.append(
            {
                "projection_id": projection_id,

                "h3_cell": cell_id,

                "aircraft_id": aircraft_id,

                "h3_resolution": h3_resolution,

                "generated_at_utc": (
                    generated_at_utc
                ),

                "valid_until_utc": (
                    valid_until_utc
                ),

                "projection_status_snapshot": (
                    "BUILDING"
                ),

                "created_at_utc": (
                    generated_at_utc
                ),

                "correlation_id": (
                    correlation_id
                ),

                "schema_version": (
                    PROJECTION_CELLS_SCHEMA_VERSION
                ),

                "expires_at_epoch": (
                    expires_at_epoch
                ),
            }
        )

    return cells

def build_projection_parent(
    *,
    state: dict[str, Any],
    eligibility: dict[str, Any],
    projection_id: str,
    idempotency_key: str,
    generated_at_epoch: int,
    points: list[dict[str, Any]],
    corridor_cells: list[str],
) -> dict[str, Any]:

    generated_at_utc = epoch_to_utc(
        generated_at_epoch
    )

    projection_horizon_min = max(
        PROJECTION_HORIZONS_MIN
    )

    valid_until_epoch = (
        generated_at_epoch
        + projection_horizon_min * 60
    )

    valid_until_utc = epoch_to_utc(
        valid_until_epoch
    )

    expires_at_epoch = (
        valid_until_epoch
        + PROJECTION_RETENTION_SECONDS
    )

    correlation_id = str(
        eligibility.get(
            "correlation_id",
            idempotency_key,
        )
    )

    trigger_hazard_ids = list(
        dict.fromkeys(
            eligibility.get(
                "trigger_hazard_ids",
                [],
            )
        )
    )

    if (
        len(trigger_hazard_ids)
        > MAX_TRIGGER_HAZARDS
    ):
        raise RuntimeError(
            "Too many trigger hazards "
            "for AircraftProjection parent."
        )

    confidence_rank = {
        "HIGH": 3,
        "MEDIUM": 2,
        "LOW": 1,
    }

    confidence = min(
        (
            point["confidence"]
            for point in points
        ),
        key=lambda value: (
            confidence_rank[value]
        ),
    )

    parent = {
        "projection_id": (
            projection_id
        ),

        "aircraft_id": (
            state["aircraft_id"]
        ),

        "aircraft_state_version": (
            state["state_version"]
        ),

        "source_position_time_epoch": int(
            state["position_time_epoch"]
        ),

        "generated_at_epoch": (
            generated_at_epoch
        ),

        "generated_at_utc": (
            generated_at_utc
        ),

        "eligibility_checked_at_utc": (
            eligibility.get(
                "eligibility_checked_at_utc",
                generated_at_utc,
            )
        ),

        "current_aircraft_h3_cell": (
            state["current_h3_cell"]
        ),

        "matched_impact_cells": (
            eligibility.get(
                "matched_impact_cells",
                [],
            )
        ),

        "trigger_hazard_ids": (
            trigger_hazard_ids
        ),

        "projection_trigger_reason": (
            eligibility.get(
                "projection_trigger_reason",
                "AIRCRAFT_STATE_CHANGED",
            )
        ),

        "valid_until_epoch": (
            valid_until_epoch
        ),

        "valid_until_utc": (
            valid_until_utc
        ),

        "projection_horizon_min": (
            projection_horizon_min
        ),

        "point_count": len(
            points
        ),

        "corridor_width_profile": [
            {
                "horizon_min": horizon,
                "grid_distance": distance,
            }
            for horizon, distance
            in zip(
                PROJECTION_HORIZONS_MIN,
                CORRIDOR_GRID_DISTANCES,
            )
        ],

        "corridor_h3_cell_count": len(
            corridor_cells
        ),

        "confidence": confidence,

        "projection_status": (
            "BUILDING"
        ),

        "projection_algorithm_version": (
            PROJECTION_ALGORITHM_VERSION
        ),

        "projection_config_version": (
            PROJECTION_CONFIG_VERSION
        ),

        "idempotency_key": (
            idempotency_key
        ),

        "correlation_id": (
            correlation_id
        ),

        "schema_version": (
            PROJECTION_SCHEMA_VERSION
        ),

        "expires_at_epoch": (
            expires_at_epoch
        ),
    }

    return parent

def get_projection_parent(
    projection_id: str,
) -> dict[str, Any] | None:

    response = projection_table.get_item(
        Key={
            "projection_id": projection_id
        },
        ConsistentRead=True,
    )

    return response.get("Item")

def create_projection_parent(
    parent: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """
    Create the AircraftProjection parent as BUILDING.

    Returns:
        (parent_record, created)

    created=True:
        this invocation created the parent.

    created=False:
        the same deterministic projection already exists.
    """

    try:
        projection_table.put_item(
            Item=parent,
            ConditionExpression=(
                "attribute_not_exists(projection_id)"
            ),
        )

        return parent, True

    except ClientError as exc:
        error_code = (
            exc.response
            .get("Error", {})
            .get("Code")
        )

        if (
            error_code
            != "ConditionalCheckFailedException"
        ):
            raise

        existing = get_projection_parent(
            parent["projection_id"]
        )

        if existing is None:
            raise RuntimeError(
                "Projection parent already existed "
                "but could not be loaded."
            )

        #
        # Make sure the collision is actually
        # the same idempotent projection.
        #
        if (
            existing.get("idempotency_key")
            != parent["idempotency_key"]
        ):
            raise RuntimeError(
                "Projection ID collision detected."
            )

        return existing, False

def write_projection_points(
    points: list[dict[str, Any]],
) -> int:
    """
    Write AircraftProjectionPoints.

    Safe to retry because each point has the same:
      projection_id + point_key
    """

    if not points:
        return 0

    with projection_points_table.batch_writer(
        overwrite_by_pkeys=[
            "projection_id",
            "point_key",
        ]
    ) as batch:

        for point in points:
            batch.put_item(
                Item=point
            )

    return len(points)

def write_projection_cells(
    cells: list[dict[str, Any]],
) -> int:
    """
    Write AircraftProjectionCells.

    Safe to retry because each cell has the same:
      projection_id + h3_cell
    """

    if not cells:
        return 0

    with projection_cells_table.batch_writer(
        overwrite_by_pkeys=[
            "projection_id",
            "h3_cell",
        ]
    ) as batch:

        for cell in cells:
            batch.put_item(
                Item=cell
            )

    return len(cells)

def count_projection_points(
    projection_id: str,
) -> int:

    total = 0

    response = projection_points_table.query(
        KeyConditionExpression=(
            Key("projection_id").eq(
                projection_id
            )
        ),
        Select="COUNT",
        ConsistentRead=True,
    )

    total += int(
        response.get("Count", 0)
    )

    while response.get(
        "LastEvaluatedKey"
    ):
        response = projection_points_table.query(
            KeyConditionExpression=(
                Key("projection_id").eq(
                    projection_id
                )
            ),
            Select="COUNT",
            ConsistentRead=True,
            ExclusiveStartKey=response[
                "LastEvaluatedKey"
            ],
        )

        total += int(
            response.get("Count", 0)
        )

    return total

def mark_projection_ready(
    parent: dict[str, Any],
) -> dict[str, Any]:
    """
    Mark AircraftProjection READY only after
    both child tables contain the expected rows.
    """

    projection_id = parent[
        "projection_id"
    ]

    expected_point_count = int(
        parent["point_count"]
    )

    expected_cell_count = int(
        parent["corridor_h3_cell_count"]
    )

    actual_point_count = (
        count_projection_points(
            projection_id
        )
    )

    actual_cell_count = (
        count_projection_cells(
            projection_id
        )
    )

    if (
        actual_point_count
        != expected_point_count
    ):
        raise RuntimeError(
            "ProjectionPoints count mismatch: "
            f"expected={expected_point_count}, "
            f"actual={actual_point_count}"
        )

    if (
        actual_cell_count
        != expected_cell_count
    ):
        raise RuntimeError(
            "ProjectionCells count mismatch: "
            f"expected={expected_cell_count}, "
            f"actual={actual_cell_count}"
        )

    ready_at_utc = epoch_to_utc(
        now_epoch()
    )

    try:
        response = projection_table.update_item(
            Key={
                "projection_id": projection_id
            },

            UpdateExpression=(
                "SET projection_status = :ready, "
                "ready_at_utc = :ready_at"
            ),

            ConditionExpression=(
                "projection_status = :building "
                "AND point_count = :point_count "
                "AND corridor_h3_cell_count = :cell_count"
            ),

            ExpressionAttributeValues={
                ":ready": "READY",
                ":building": "BUILDING",
                ":ready_at": ready_at_utc,
                ":point_count": (
                    expected_point_count
                ),
                ":cell_count": (
                    expected_cell_count
                ),
            },

            ReturnValues="ALL_NEW",
        )

        return response["Attributes"]

    except ClientError as exc:
        error_code = (
            exc.response
            .get("Error", {})
            .get("Code")
        )

        if (
            error_code
            != "ConditionalCheckFailedException"
        ):
            raise

        #
        # A retry may arrive after this
        # projection already became READY.
        #
        existing = get_projection_parent(
            projection_id
        )

        if (
            existing
            and existing.get(
                "projection_status"
            ) == "READY"
            and int(
                existing["point_count"]
            ) == expected_point_count
            and int(
                existing[
                    "corridor_h3_cell_count"
                ]
            ) == expected_cell_count
        ):
            return existing

        raise RuntimeError(
            "AircraftProjection could not "
            "transition from BUILDING to READY."
        ) from exc


def mark_projection_failed(
    projection_id: str,
) -> None:
    try:
        projection_table.update_item(
            Key={
                "projection_id": projection_id
            },
            UpdateExpression=(
                "SET projection_status = :failed"
            ),
            ConditionExpression=(
                "projection_status = :building"
            ),
            ExpressionAttributeValues={
                ":failed": "FAILED",
                ":building": "BUILDING",
            },
        )

    except ClientError as exc:
        error_code = (
            exc.response
            .get("Error", {})
            .get("Code")
        )

        if error_code != "ConditionalCheckFailedException":
            raise

def count_projection_cells(
    projection_id: str,
) -> int:

    total = 0

    response = projection_cells_table.query(
        KeyConditionExpression=(
            Key("projection_id").eq(
                projection_id
            )
        ),
        Select="COUNT",
        ConsistentRead=True,
    )

    total += int(
        response.get("Count", 0)
    )

    while response.get(
        "LastEvaluatedKey"
    ):
        response = projection_cells_table.query(
            KeyConditionExpression=(
                Key("projection_id").eq(
                    projection_id
                )
            ),
            Select="COUNT",
            ConsistentRead=True,
            ExclusiveStartKey=response[
                "LastEvaluatedKey"
            ],
        )

        total += int(
            response.get("Count", 0)
        )

    return total

def publish_projection_ready(
    parent: dict[str, Any],
) -> None:
    """
    Publish projection.ready only after
    AircraftProjection is READY.
    """

    detail = {
        "projection_id": (
            parent["projection_id"]
        ),

        "aircraft_id": (
            parent["aircraft_id"]
        ),

        "aircraft_state_version": (
            parent[
                "aircraft_state_version"
            ]
        ),

        "generated_at_epoch": int(
            parent[
                "generated_at_epoch"
            ]
        ),

        "generated_at_utc": (
            parent[
                "generated_at_utc"
            ]
        ),

        "valid_until_epoch": int(
            parent[
                "valid_until_epoch"
            ]
        ),

        "valid_until_utc": (
            parent[
                "valid_until_utc"
            ]
        ),

        "point_count": int(
            parent["point_count"]
        ),

        "corridor_h3_cell_count": int(
            parent[
                "corridor_h3_cell_count"
            ]
        ),

        "correlation_id": (
            parent[
                "correlation_id"
            ]
        ),

        "schema_version": (
            parent[
                "schema_version"
            ]
        ),
    }

    response = eventbridge.put_events(
        Entries=[
            {
                "EventBusName": (
                    EVENT_BUS_NAME
                ),

                "Source": (
                    "wilvor.projection"
                ),

                "DetailType": (
                    "projection.ready"
                ),

                "Detail": json.dumps(
                    detail
                ),
            }
        ]
    )

    if int(
        response.get(
            "FailedEntryCount",
            0,
        )
    ) > 0:
        raise RuntimeError(
            "Failed to publish "
            "projection.ready event."
        )

def materialize_projection(
    eligibility: dict[str, Any],
) -> dict[str, Any]:
    """
    Build one complete projection:

    BUILDING parent
      -> ProjectionPoints
      -> ProjectionCells
      -> validate counts
      -> READY
      -> projection.ready
    """

    aircraft_id = eligibility[
        "aircraft_id"
    ]

    state = get_aircraft_state(
        aircraft_id
    )

    if state is None:
        raise RuntimeError(
            "Aircraft state disappeared "
            "before projection materialization."
        )

    #
    # Very important:
    # make sure AircraftCurrentState did not
    # change after eligibility was evaluated.
    #
    expected_state_version = eligibility[
    "state_version"
    ]

    if (
        state.get("state_version")
        != expected_state_version
    ):
        return {
            "materialized": False,
            "reason": (
                "AIRCRAFT_STATE_CHANGED_"
                "DURING_PROJECTION"
            ),
            "aircraft_id": aircraft_id,
        }

    (
        projection_id,
        idempotency_key,
    ) = projection_identity(
        state=state,
        eligibility=eligibility,
    )

    #
    # Check for an existing deterministic run.
    #
    existing = get_projection_parent(
        projection_id
    )

    if (
        existing is not None
        and existing.get(
            "projection_status"
        ) == "READY"
    ):
        #
        # Republish on retry so a previous
        # EventBridge publication failure can
        # recover. Consumers must be idempotent
        # by projection_id.
        #
        publish_projection_ready(
            existing
        )

        return {
            "materialized": True,
            "projection_id": projection_id,
            "projection_status": "READY",
            "idempotent_replay": True,
            "point_count": int(
                existing["point_count"]
            ),
            "corridor_h3_cell_count": int(
                existing[
                    "corridor_h3_cell_count"
                ]
            ),
        }

    #
    # For an existing BUILDING projection,
    # reuse its original generation timestamp.
    #
    if existing is not None:
        generated_at_epoch = int(
            existing[
                "generated_at_epoch"
            ]
        )

        generated_at_utc = existing[
            "generated_at_utc"
        ]

        expires_at_epoch = int(
            existing[
                "expires_at_epoch"
            ]
        )

        correlation_id = existing[
            "correlation_id"
        ]

    else:
        generated_at_epoch = now_epoch()

        generated_at_utc = epoch_to_utc(
            generated_at_epoch
        )

        max_horizon_min = max(
            PROJECTION_HORIZONS_MIN
        )

        valid_until_epoch = (
            generated_at_epoch
            + max_horizon_min * 60
        )

        expires_at_epoch = (
            valid_until_epoch
            + PROJECTION_RETENTION_SECONDS
        )

        correlation_id = str(
            eligibility.get(
                "correlation_id"
            )
            or idempotency_key
        )

    #
    # Generate ProjectionPoints.
    #
    points = generate_projection_points(
        state=state,
        projection_id=projection_id,
        generated_at_epoch=(
            generated_at_epoch
        ),
        generated_at_utc=(
            generated_at_utc
        ),
        expires_at_epoch=(
            expires_at_epoch
        ),
        correlation_id=(
            correlation_id
        ),
    )

    #
    # Build H3 projection corridor.
    #
    corridor_cells = (
        generate_corridor_cells(
            state[
                "current_h3_cell"
            ],
            points,
        )
    )

    #
    # Create parent BUILDING if this is
    # the first execution.
    #
    if existing is None:
        candidate_parent = (
            build_projection_parent(
                state=state,
                eligibility=eligibility,
                projection_id=(
                    projection_id
                ),
                idempotency_key=(
                    idempotency_key
                ),
                generated_at_epoch=(
                    generated_at_epoch
                ),
                points=points,
                corridor_cells=(
                    corridor_cells
                ),
            )
        )

        parent, created = (
            create_projection_parent(
                candidate_parent
            )
        )

    else:
        parent = existing
        created = False

    #
    # Protect deterministic retries.
    #
    if (
        len(points)
        != int(parent["point_count"])
    ):
        raise RuntimeError(
            "Generated point count does not "
            "match AircraftProjection parent."
        )

    if (
        len(corridor_cells)
        != int(
            parent[
                "corridor_h3_cell_count"
            ]
        )
    ):
        raise RuntimeError(
            "Generated corridor cell count does "
            "not match AircraftProjection parent."
        )

    #
    # Convert H3 IDs into child records.
    #
    cells = generate_projection_cells(
        projection_id=projection_id,
        aircraft_id=aircraft_id,
        h3_resolution=int(
            state["h3_resolution"]
        ),
        generated_at_utc=(
            parent["generated_at_utc"]
        ),
        valid_until_utc=(
            parent["valid_until_utc"]
        ),
        correlation_id=(
            parent["correlation_id"]
        ),
        expires_at_epoch=int(
            parent["expires_at_epoch"]
        ),
        cell_ids=corridor_cells,
    )

    #
    # Child set 1.
    #
    written_points = (
        write_projection_points(
            points
        )
    )

    #
    # Child set 2.
    #
    written_cells = (
        write_projection_cells(
            cells
        )
    )

    #
    # This function verifies both child
    # counts before changing BUILDING
    # to READY.
    #
    ready_parent = (
        mark_projection_ready(
            parent
        )
    )

    #
    # Only READY projections emit this.
    #
    publish_projection_ready(
        ready_parent
    )

    return {
        "materialized": True,
        "projection_id": projection_id,
        "projection_status": "READY",
        "idempotent_replay": (
            not created
        ),
        "point_count": written_points,
        "corridor_h3_cell_count": (
            written_cells
        ),
    }


def lambda_handler(
    event: dict[str, Any],
    context: Any,
) -> dict[str, Any]:
    detail = event.get(
        "detail",
        {},
    )

    metrics = {
        "EventsReceived": 1,
        "EligibleAircraft": 0,
        "IneligibleAircraft": 0,
        "StaleEventsSkipped": 0,
        "ImpactCandidatesFound": 0,
        "ValidImpactMatches": 0,
        "EligibilityFailures": 0,
    }

    try:
        result = evaluate_eligibility(
            detail
        )

    except Exception as exc:
        metrics[
            "EligibilityFailures"
        ] = 1

        emit_metrics(
            metrics
        )

        print(
            json.dumps(
                {
                    "event": (
                        "projection_eligibility_failed"
                    ),
                    "error_type": (
                        type(exc).__name__
                    ),
                    "message": str(exc),
                }
            )
        )

        raise

    metrics[
        "ImpactCandidatesFound"
    ] = int(
        result.get(
            "impact_candidates_found",
            0,
        )
    )

    metrics[
        "ValidImpactMatches"
    ] = int(
        result.get(
            "valid_impact_matches",
            0,
        )
    )

    if not result["eligible"]:
        metrics[
            "IneligibleAircraft"
        ] = 1

        if (
            result.get("reason")
            == "STALE_EVENT_VERSION"
        ):
            metrics[
                "StaleEventsSkipped"
            ] = 1

        emit_metrics(
            metrics
        )

        print(
            json.dumps(
                {
                    "event": (
                        "projection_eligibility_evaluated"
                    ),
                    **result,
                },
                default=str,
            )
        )

        return result

    metrics[
        "EligibleAircraft"
    ] = 1

    emit_metrics(
        metrics
    )

    try:
        materialization = (
            materialize_projection(
                result
            )
        )

    except Exception:
        try:
            state = get_aircraft_state(
                result[
                    "aircraft_id"
                ]
            )

            if state:
                (
                    projection_id,
                    _,
                ) = projection_identity(
                    state=state,
                    eligibility=result,
                )

                mark_projection_failed(
                    projection_id
                )

        except Exception:
            pass

        raise

    response = {
        **result,
        **materialization,
    }

    print(
        json.dumps(
            {
                "event": (
                    "projection_materialization_completed"
                ),
                **response,
            },
            default=str,
        )
    )

    return response