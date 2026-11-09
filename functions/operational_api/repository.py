import base64
import json
import os
import time
from collections import Counter
from datetime import (
    datetime,
    timedelta,
    timezone,
)

import boto3
from boto3.dynamodb.conditions import Attr, Key
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

import current_set
from wilvor_operational import access as operational_access
from wilvor_operational import readers as operational_readers


DDB = boto3.resource("dynamodb")

CLOUDWATCH = boto3.client(
    "cloudwatch"
)

LAMBDA_CLIENT = boto3.client(
    "lambda"
)


NAME_PREFIX = os.environ[
    "NAME_PREFIX"
]


OPERATIONAL_API_FUNCTION_NAME = (
    os.environ.get(
        "AWS_LAMBDA_FUNCTION_NAME"
    )
    or (
        f"{NAME_PREFIX}-operational-api"
    )
)


_CACHE = {}

SERIALIZER = TypeSerializer()
DESERIALIZER = TypeDeserializer()


# ---------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------

AIRCRAFT = DDB.Table(
    os.environ["AIRCRAFT_CURRENT_STATE_TABLE_NAME"]
)

PROJECTIONS = DDB.Table(
    os.environ["AIRCRAFT_PROJECTION_TABLE_NAME"]
)

PROJECTION_POINTS = DDB.Table(
    os.environ["AIRCRAFT_PROJECTION_POINTS_TABLE_NAME"]
)

HAZARDS = DDB.Table(
    os.environ["ACTIVE_HAZARDS_TABLE_NAME"]
)

HAZARD_COORDINATES = DDB.Table(
    os.environ["HAZARD_COORDINATES_TABLE_NAME"]
)

ENCOUNTERS = DDB.Table(
    os.environ["AIRCRAFT_HAZARD_ENCOUNTER_TABLE_NAME"]
)

RISKS = DDB.Table(
    os.environ["RISK_RESULTS_TABLE_NAME"]
)

AIRPORTS = DDB.Table(
    os.environ["AIRPORT_STATUS_TABLE_NAME"]
)

METAR = DDB.Table(
    os.environ["METAR_LATEST_TABLE_NAME"]
)

TAF = DDB.Table(
    os.environ["TAF_LATEST_TABLE_NAME"]
)

TAF_PERIODS = DDB.Table(
    os.environ["TAF_FORECAST_PERIODS_TABLE_NAME"]
)

AIRPORT_ASSESSMENTS = DDB.Table(
    os.environ["AIRPORT_ASSESSMENT_TABLE_NAME"]
)

RECOMMENDATIONS = DDB.Table(
    os.environ["RECOMMENDATIONS_TABLE_NAME"]
)

ALERTS = DDB.Table(
    os.environ["ACTIVE_ALERTS_TABLE_NAME"]
)


def operational_tables():
    return operational_readers.OperationalTables(
        aircraft=AIRCRAFT,
        projections=PROJECTIONS,
        projection_points=PROJECTION_POINTS,
        hazards=HAZARDS,
        hazard_coordinates=HAZARD_COORDINATES,
        encounters=ENCOUNTERS,
        risks=RISKS,
        airports=AIRPORTS,
        metar=METAR,
        taf=TAF,
        taf_periods=TAF_PERIODS,
        airport_assessments=AIRPORT_ASSESSMENTS,
        recommendations=RECOMMENDATIONS,
        alerts=ALERTS,
    )


# ---------------------------------------------------------------------
# Existing GSI names
# ---------------------------------------------------------------------

IDX_AIRCRAFT_CALLSIGN = (
    "callsign-position_time_epoch-index"
)

IDX_AIRCRAFT_H3 = (
    "current_h3_cell-position_time_epoch-index"
)

IDX_PROJECTION_AIRCRAFT_TIME = (
    "aircraft_id-generated_at_epoch-index"
)

IDX_HAZARD_STATUS_VALIDITY = (
    "status-valid_to_epoch-index"
)

IDX_ENCOUNTER_AIRCRAFT_TIME = (
    "aircraft_id-detected_at_epoch-index"
)

IDX_RISK_AIRCRAFT_TIME = (
    "aircraft_id-generated_at_epoch-index"
)

IDX_RISK_ENCOUNTER_TIME = (
    "encounter_id-generated_at_epoch-index"
)

IDX_AIRPORT_RISK_TIME = (
    "weather-risk-updated-index"
)

IDX_AIRPORT_IMPACT_TIME = (
    "weather-impact-updated-index"
)

IDX_TAF_PERIOD_STATION_TIME = (
    "station_id-period_from_epoch-index"
)

IDX_AIRPORT_ASSESSMENT_AIRPORT_TIME = (
    "airport_id-created_at_epoch-index"
)

IDX_RECOMMENDATION_AIRCRAFT_TIME = (
    "aircraft_id-created_at_epoch-index"
)

IDX_RECOMMENDATION_STATUS_TIME = (
    "recommendation_status-updated_at_epoch-index"
)

IDX_ALERT_AIRCRAFT_TIME = (
    "aircraft_id-updated_at_epoch-index"
)


CURRENT_ENCOUNTER_CACHE_TTL_SECONDS = 15


# ---------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------

def _now_iso():
    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _encode_token(last_evaluated_key):
    if not last_evaluated_key:
        return None

    typed = {
        key: SERIALIZER.serialize(value)
        for key, value in last_evaluated_key.items()
    }

    raw = json.dumps(
        typed,
        separators=(",", ":"),
    ).encode("utf-8")

    return base64.urlsafe_b64encode(
        raw
    ).decode("ascii")


def _decode_token(token):
    if not token:
        return None

    try:
        raw = base64.urlsafe_b64decode(
            token.encode("ascii")
        )

        typed = json.loads(
            raw.decode("utf-8")
        )

        return {
            key: DESERIALIZER.deserialize(value)
            for key, value in typed.items()
        }

    except Exception as exc:
        raise ValueError(
            "nextToken is invalid"
        ) from exc


def _with_start_key(kwargs, next_token):
    start_key = _decode_token(next_token)

    if start_key:
        kwargs["ExclusiveStartKey"] = start_key

    return kwargs


def _page(response):
    items = response.get("Items", [])

    return {
        "items": items,
        "count": len(items),
        "nextToken": _encode_token(
            response.get("LastEvaluatedKey")
        ),
    }


def _page_with_items(response, items):
    return {
        "items": items,
        "count": len(items),
        "nextToken": _encode_token(
            response.get("LastEvaluatedKey")
        ),
    }


def _query_latest(
    table,
    index_name,
    partition_name,
    partition_value,
    limit=10,
):
    return operational_access.query_latest(
        table,
        index_name,
        partition_name,
        partition_value,
        limit=limit,
        key=Key,
    )


def _query_all(table, **kwargs):
    return operational_access.query_all(table, **kwargs)


def _scan_all(
    table,
    **kwargs,
):
    """
    Read every page from a DynamoDB Scan.

    This is intentionally used only for:
    - low-frequency dashboard summary composition
    - freshness calculation
    - current operational summary generation

    The results are cached briefly so the React dashboard does not
    perform a full table scan on every browser refresh.
    """

    return operational_access.scan_all(table, **kwargs)

def _scan_count(
    table,
    **kwargs,
):
    """
    Count matching DynamoDB records without returning the complete
    item payload to the Lambda.
    """

    total = 0

    while True:
        response = table.scan(
            Select="COUNT",
            **kwargs,
        )

        total += int(
            response.get(
                "Count",
                0,
            )
            or 0
        )

        last_key = response.get(
            "LastEvaluatedKey"
        )

        if not last_key:
            return total

        kwargs[
            "ExclusiveStartKey"
        ] = last_key


def _query_count(
    table,
    **kwargs,
):
    """
    Count matching DynamoDB records through Query without returning
    complete item payloads.
    """

    total = 0

    while True:
        response = table.query(
            Select="COUNT",
            **kwargs,
        )

        total += int(
            response.get(
                "Count",
                0,
            )
            or 0
        )

        last_key = response.get(
            "LastEvaluatedKey"
        )

        if not last_key:
            return total

        kwargs[
            "ExclusiveStartKey"
        ] = last_key

def _cached(
    cache_key,
    ttl_seconds,
    loader,
):
    now = time.time()

    cached = _CACHE.get(
        cache_key
    )

    if (
        cached
        and cached["expires_at"] > now
    ):
        return cached["value"]

    value = loader()

    _CACHE[cache_key] = {
        "expires_at": (
            now + ttl_seconds
        ),
        "value": value,
    }

    return value


def _load_current_indexes(now_epoch):
    projections = operational_readers.scan_projection_index_candidates(
        PROJECTIONS,
        now_epoch=now_epoch,
        attr=Attr,
        scan_all=_scan_all,
    )

    hazards = operational_readers.scan_hazard_index_candidates(
        HAZARDS,
        now_epoch=now_epoch,
        attr=Attr,
        scan_all=_scan_all,
    )

    return (
        current_set.index_current_projections(
            projections,
            now_epoch,
        ),
        current_set.index_current_hazard_versions(
            hazards,
            now_epoch,
        ),
    )


def _current_encounter_snapshot():
    def _load():
        now_epoch = int(time.time())
        projection_ids, hazard_versions = _load_current_indexes(
            now_epoch
        )
        items = operational_readers.scan_encounter_candidates(
            ENCOUNTERS,
            attr=Attr,
            scan_all=_scan_all,
            encounter_states=current_set.CURRENT_ENCOUNTER_STATES,
        )
        current_items = [
            item
            for item in items
            if current_set.is_current_encounter(
                item,
                current_projection_ids=projection_ids,
                current_hazard_versions=hazard_versions,
            )
        ]
        return {
            "now_epoch": now_epoch,
            "items": current_items,
            "projection_ids": projection_ids,
            "hazard_versions": hazard_versions,
        }

    return _cached(
        "current_encounters",
        CURRENT_ENCOUNTER_CACHE_TTL_SECONDS,
        _load,
    )


def _parse_iso_epoch(
    value,
):
    if not value:
        return None

    try:
        text = str(
            value
        ).strip()

        if text.endswith("Z"):
            text = (
                text[:-1]
                + "+00:00"
            )

        parsed = (
            datetime.fromisoformat(
                text
            )
        )

        if (
            parsed.tzinfo
            is None
        ):
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return int(
            parsed.timestamp()
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


def _epoch_to_iso(
    value,
):
    if value is None:
        return None

    try:
        return (
            datetime.fromtimestamp(
                int(value),
                tz=timezone.utc,
            )
            .isoformat()
            .replace(
                "+00:00",
                "Z",
            )
        )

    except (
        TypeError,
        ValueError,
        OSError,
    ):
        return None


def _latest_epoch_from_table(
    table,
    *,
    numeric_fields=None,
    iso_fields=None,
):
    """
    Find the newest timestamp represented in a current-state table.

    Only timestamp attributes are projected, which makes these scans
    significantly cheaper than loading complete records.
    """

    numeric_fields = (
        numeric_fields
        or []
    )

    iso_fields = (
        iso_fields
        or []
    )

    fields = (
        list(numeric_fields)
        + list(iso_fields)
    )

    if not fields:
        return None

    names = {}

    projection_parts = []

    for index, field in enumerate(
        fields
    ):
        alias = f"#f{index}"

        names[alias] = field

        projection_parts.append(
            alias
        )

    kwargs = {
        "ProjectionExpression": (
            ",".join(
                projection_parts
            )
        ),
        "ExpressionAttributeNames": (
            names
        ),
    }

    latest = None

    while True:
        response = table.scan(
            **kwargs
        )

        for item in response.get(
            "Items",
            [],
        ):
            for field in (
                numeric_fields
            ):
                value = item.get(
                    field
                )

                if value is None:
                    continue

                try:
                    epoch = int(
                        value
                    )

                except (
                    TypeError,
                    ValueError,
                ):
                    continue

                if (
                    latest is None
                    or epoch > latest
                ):
                    latest = epoch

            for field in (
                iso_fields
            ):
                epoch = (
                    _parse_iso_epoch(
                        item.get(
                            field
                        )
                    )
                )

                if epoch is None:
                    continue

                if (
                    latest is None
                    or epoch > latest
                ):
                    latest = epoch

        last_key = response.get(
            "LastEvaluatedKey"
        )

        if not last_key:
            break

        kwargs[
            "ExclusiveStartKey"
        ] = last_key

    return latest


def _freshness_record(
    latest_epoch,
    *,
    fresh_seconds,
    stale_seconds,
):
    if latest_epoch is None:
        return {
            "latestAt": None,
            "ageSeconds": None,
            "status": "UNAVAILABLE",
        }

    now = int(
        time.time()
    )

    age = max(
        0,
        now - int(
            latest_epoch
        ),
    )

    if age <= fresh_seconds:
        status = "FRESH"

    elif age <= stale_seconds:
        status = "ACCEPTABLE"

    else:
        status = "STALE"

    return {
        "latestAt": (
            _epoch_to_iso(
                latest_epoch
            )
        ),
        "ageSeconds": age,
        "status": status,
    }


def _is_future_iso(
    value,
):
    epoch = _parse_iso_epoch(
        value
    )

    if epoch is None:
        return False

    return (
        epoch
        > int(
            time.time()
        )
    )


def _risk_rank(
    value,
):
    return {
        "HIGH": 4,
        "MEDIUM": 3,
        "LOW": 2,
        "UNKNOWN": 1,
    }.get(
        str(
            value
            or ""
        ).upper(),
        0,
    )


def _risk_score_value(
    item,
):
    try:
        return float(
            item.get(
                "risk_score",
                0,
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        return 0.0


# ---------------------------------------------------------------------
# Hazard geometry reconstruction
# ---------------------------------------------------------------------

def _hazard_geometry(hazard):
    """
    ActiveHazards contains the parent hazard state.

    Exact ordered polygon coordinates are stored in HazardCoordinates
    using:

        hazard_version_key =
            <hazard_id>#<source_version>

    This reconstructs those rows into valid GeoJSON.
    """

    hazard_id = str(
        hazard.get("hazard_id") or ""
    ).strip()

    source_version = str(
        hazard.get("source_version") or ""
    ).strip()

    if not hazard_id or not source_version:
        return None

    hazard_version_key = (
        f"{hazard_id}#{source_version}"
    )

    rows = operational_readers.query_hazard_coordinate_rows(
        HAZARD_COORDINATES,
        hazard_version_key,
        key=Key,
        query_all=_query_all,
    )

    if not rows:
        return None

    polygons = {}

    for row in rows:
        try:
            polygon_index = int(
                row.get("polygon_index", 0)
            )

            ring_index = int(
                row.get("ring_index", 0)
            )

            sequence_number = int(
                row.get("sequence_number", 0)
            )

            longitude = row["longitude"]
            latitude = row["latitude"]

        except (KeyError, TypeError, ValueError):
            continue

        polygons.setdefault(
            polygon_index,
            {},
        ).setdefault(
            ring_index,
            [],
        ).append(
            (
                sequence_number,
                [longitude, latitude],
            )
        )

    ordered_polygons = []

    for polygon_index in sorted(polygons):
        rings = []

        for ring_index in sorted(
            polygons[polygon_index]
        ):
            points = [
                point
                for _, point in sorted(
                    polygons[polygon_index][ring_index],
                    key=lambda item: item[0],
                )
            ]

            if len(points) < 3:
                continue

            # GeoJSON rings must be closed.
            if points[0] != points[-1]:
                points.append(points[0])

            if len(points) >= 4:
                rings.append(points)

        if rings:
            ordered_polygons.append(rings)

    if not ordered_polygons:
        return None

    geometry_type = str(
        hazard.get("geometry_type")
        or rows[0].get("geometry_type")
        or ""
    ).upper()

    if geometry_type == "MULTIPOLYGON":
        return {
            "type": "MultiPolygon",
            "coordinates": ordered_polygons,
        }

    return {
        "type": "Polygon",
        "coordinates": ordered_polygons[0],
    }


# ---------------------------------------------------------------------
# Aircraft
# ---------------------------------------------------------------------

MAP_AIRCRAFT_CACHE_TTL_SECONDS = 15

MAP_AIRCRAFT_MAX_ITEMS = 8000

MAP_AIRCRAFT_COLUMNS = [
    "aircraftId",
    "callsign",
    "longitude",
    "latitude",
    "trackDeg",
    "baroAltitudeFt",
    "groundSpeedKt",
    "positionTimeEpoch",
]


def get_map_aircraft():
    """
    Compact aircraft layer for the operations map.

    The paginated /aircraft listing cannot back a network map: the
    unfiltered path is a Scan, limit is capped at 100, and pagination
    is strictly sequential, so a full fleet costs dozens of chained
    round trips per refresh cycle.

    This response is deliberately lean:
    - only the attributes needed to draw and label the layer
    - row arrays, so attribute names are not repeated per aircraft
    - one response, no client pagination

    Rows are positional and described by MAP_AIRCRAFT_COLUMNS, which
    is returned alongside them so the encoding stays self-describing.

    Complete aircraft objects stay available through
    GET /aircraft/{aircraftId}.

    Risk level is intentionally not joined here. It lives in a
    separate table and would require a second full scan; the map
    highlights risk through the encounter and risk APIs instead.
    """

    def _load():
        now_epoch = int(time.time())

        aircraft = _scan_all(
            AIRCRAFT,
            FilterExpression=(
                Attr(
                    "expires_at_epoch"
                ).gt(
                    now_epoch
                )
                & Attr(
                    "latitude"
                ).exists()
                & Attr(
                    "longitude"
                ).exists()
            ),
            ProjectionExpression=(
                "aircraft_id,"
                "callsign,"
                "latitude,"
                "longitude,"
                "track_deg,"
                "baro_altitude_ft,"
                "ground_speed_kt,"
                "position_time_epoch"
            ),
        )

        rows = []
        truncated = False

        for item in aircraft:
            if (
                len(rows)
                >= MAP_AIRCRAFT_MAX_ITEMS
            ):
                truncated = True
                break

            aircraft_id = item.get(
                "aircraft_id"
            )

            if not aircraft_id:
                continue

            rows.append(
                [
                    aircraft_id,
                    item.get("callsign"),
                    item.get("longitude"),
                    item.get("latitude"),
                    item.get("track_deg"),
                    item.get(
                        "baro_altitude_ft"
                    ),
                    item.get(
                        "ground_speed_kt"
                    ),
                    item.get(
                        "position_time_epoch"
                    ),
                ]
            )

        return {
            "generatedAt": _now_iso(),
            "columns": (
                MAP_AIRCRAFT_COLUMNS
            ),
            "count": len(rows),
            "truncated": truncated,
            "aircraft": rows,
        }

    return _cached(
        "map_aircraft",
        MAP_AIRCRAFT_CACHE_TTL_SECONDS,
        _load,
    )


def list_aircraft(
    limit,
    next_token=None,
    callsign=None,
    h3_cell=None,
):
    if callsign and h3_cell:
        raise ValueError(
            "Use either callsign or h3Cell, not both"
        )

    now = int(time.time())

    # -------------------------------------------------------------
    # Callsign query
    # -------------------------------------------------------------
    if callsign:
        callsign = callsign.strip().upper()

        if not callsign:
            raise ValueError(
                "callsign cannot be empty"
            )

        page = operational_readers.query_aircraft_by_callsign_page(
            AIRCRAFT,
            callsign=callsign,
            now_epoch=now,
            limit=limit,
            exclusive_start_key=_decode_token(next_token),
            key=Key,
            attr=Attr,
        )
        return _page_with_items(
            {"LastEvaluatedKey": page["last_evaluated_key"]},
            [
                item
                for item in page["items"]
                if current_set.is_current_aircraft(item, now)
            ],
        )

    # -------------------------------------------------------------
    # H3 query
    # -------------------------------------------------------------
    if h3_cell:
        h3_cell = h3_cell.strip()

        if not h3_cell:
            raise ValueError(
                "h3Cell cannot be empty"
            )

        page = operational_readers.query_aircraft_by_h3_page(
            AIRCRAFT,
            h3_cell=h3_cell,
            now_epoch=now,
            limit=limit,
            exclusive_start_key=_decode_token(next_token),
            key=Key,
            attr=Attr,
        )
        return _page_with_items(
            {"LastEvaluatedKey": page["last_evaluated_key"]},
            [
                item
                for item in page["items"]
                if current_set.is_current_aircraft(item, now)
            ],
        )

    # -------------------------------------------------------------
    # General current-aircraft listing.
    #
    # This scan is acceptable for the first dev API.
    # We will replace broad map scans with query-optimized access
    # before treating this as a production operational endpoint.
    # -------------------------------------------------------------
    page = operational_readers.scan_aircraft_page(
        AIRCRAFT,
        now_epoch=now,
        limit=limit,
        exclusive_start_key=_decode_token(next_token),
        attr=Attr,
    )
    return _page_with_items(
        {"LastEvaluatedKey": page["last_evaluated_key"]},
        [
            item
            for item in page["items"]
            if current_set.is_current_aircraft(item, now)
        ],
    )


def get_aircraft_detail(aircraft_id):
    aircraft_id = (
        aircraft_id or ""
    ).strip().lower()

    if not aircraft_id:
        raise ValueError(
            "aircraftId is required"
        )

    current = operational_readers.get_aircraft_record(
        AIRCRAFT,
        aircraft_id,
        consistent_read=True,
    )

    if not current:
        return None

    # -------------------------------------------------------------
    # Latest projection
    # -------------------------------------------------------------
    now_epoch = int(time.time())

    projections = _query_latest(
        PROJECTIONS,
        IDX_PROJECTION_AIRCRAFT_TIME,
        "aircraft_id",
        aircraft_id,
        limit=10,
    )

    projection = None

    for candidate in projections:
        if current_set.is_current_projection(
            candidate,
            now_epoch,
        ):
            projection = candidate
            break

    # -------------------------------------------------------------
    # Projection points
    # -------------------------------------------------------------
    projection_points = []

    if (
        projection
        and projection.get("projection_id")
    ):
        page = operational_readers.query_projection_points_page(
            PROJECTION_POINTS,
            projection["projection_id"],
            key=Key,
        )
        projection_points = page["items"]

    # -------------------------------------------------------------
    # Recent decision context
    # -------------------------------------------------------------
    encounters = _query_latest(
        ENCOUNTERS,
        IDX_ENCOUNTER_AIRCRAFT_TIME,
        "aircraft_id",
        aircraft_id,
        limit=50,
    )

    risks = _query_latest(
        RISKS,
        IDX_RISK_AIRCRAFT_TIME,
        "aircraft_id",
        aircraft_id,
        limit=50,
    )

    recommendations = _query_latest(
        RECOMMENDATIONS,
        IDX_RECOMMENDATION_AIRCRAFT_TIME,
        "aircraft_id",
        aircraft_id,
        limit=50,
    )

    alerts = _query_latest(
        ALERTS,
        IDX_ALERT_AIRCRAFT_TIME,
        "aircraft_id",
        aircraft_id,
        limit=50,
    )

    current_projection_ids = {}

    if (
        projection
        and projection.get("projection_id")
    ):
        current_projection_ids[aircraft_id] = str(
            projection["projection_id"]
        )

    _projection_ids, current_hazard_versions = _load_current_indexes(
        now_epoch
    )

    if aircraft_id in _projection_ids:
        current_projection_ids[aircraft_id] = _projection_ids[
            aircraft_id
        ]

    current_encounters = [
        item
        for item in encounters
        if current_set.is_current_encounter(
            item,
            current_projection_ids=current_projection_ids,
            current_hazard_versions=current_hazard_versions,
        )
    ]

    current_contexts = _join_current_contexts(
        current_encounters,
        risks,
        recommendations,
        alerts,
    )

    return {
        "aircraft": current,
        "projection": projection,
        "projectionPoints": projection_points,
        "currentContexts": current_contexts,
        "recentEncounters": encounters[:20],
        "recentRisks": risks[:20],
        "recentRecommendations": recommendations[:20],
        "recentAlerts": alerts[:20],
    }


def _first_by_key(items, key_name):
    indexed = {}

    for item in items:
        key = item.get(key_name)

        if not key or key in indexed:
            continue

        indexed[key] = item

    return indexed


def _join_current_contexts(
    encounters,
    risks,
    recommendations,
    alerts,
):
    risk_by_encounter = _first_by_key(
        risks,
        "encounter_id",
    )
    recommendation_by_risk = _first_by_key(
        recommendations,
        "risk_id",
    )
    alert_by_risk = {}
    alert_by_recommendation = {}

    for alert in alerts:
        state = str(
            alert.get("alert_state") or ""
        ).upper()

        if state not in current_set.CURRENT_ALERT_STATES:
            continue

        risk_id = alert.get("risk_id")
        recommendation_id = alert.get("recommendation_id")

        if risk_id and risk_id not in alert_by_risk:
            alert_by_risk[risk_id] = alert

        if (
            recommendation_id
            and recommendation_id not in alert_by_recommendation
        ):
            alert_by_recommendation[recommendation_id] = alert

    contexts = []

    for encounter in encounters:
        encounter_id = encounter.get("encounter_id")
        risk = risk_by_encounter.get(encounter_id)
        recommendation = None
        alert = None

        if risk:
            recommendation = recommendation_by_risk.get(
                risk.get("risk_id")
            )

        if recommendation:
            alert = alert_by_recommendation.get(
                recommendation.get("recommendation_id")
            )

        if alert is None and risk:
            alert = alert_by_risk.get(risk.get("risk_id"))

        contexts.append(
            {
                "encounter": encounter,
                "risk": risk,
                "recommendation": recommendation,
                "alert": alert,
            }
        )

    return contexts


# ---------------------------------------------------------------------
# Hazards
# ---------------------------------------------------------------------

def list_active_hazards(
    limit,
    next_token=None,
):
    now = int(time.time())

    page = operational_readers.query_active_hazard_candidates_page(
        HAZARDS,
        now_epoch=now,
        limit=limit,
        exclusive_start_key=_decode_token(next_token),
        key=Key,
        attr=Attr,
    )

    enriched = []

    for hazard in page["items"]:
        if not current_set.is_current_hazard(hazard, now):
            continue

        item = dict(hazard)

        geometry = _hazard_geometry(
            hazard
        )

        if geometry is not None:
            item["geometry"] = geometry

        enriched.append(item)

    return _page_with_items(
        {"LastEvaluatedKey": page["last_evaluated_key"]},
        enriched,
    )


# ---------------------------------------------------------------------
# Encounters
# ---------------------------------------------------------------------

def list_active_encounters(
    limit,
    next_token=None,
):
    snapshot = _current_encounter_snapshot()
    items = sorted(
        snapshot["items"],
        key=lambda item: int(
            item.get("detected_at_epoch") or 0
        ),
        reverse=True,
    )

    offset = 0

    if next_token:
        start = _decode_token(next_token)
        offset = int(start.get("offset") or 0)

    page = items[offset:offset + limit]
    next_offset = offset + len(page)
    next_page_token = None

    if next_offset < len(items):
        next_page_token = _encode_token(
            {
                "offset": next_offset,
            }
        )

    enriched = []

    for encounter in page:
        encounter_id = encounter.get("encounter_id")
        risk = None

        if encounter_id:
            risks = _query_latest(
                RISKS,
                IDX_RISK_ENCOUNTER_TIME,
                "encounter_id",
                encounter_id,
                limit=1,
            )

            if risks:
                risk = risks[0]

        enriched.append(
            {
                "encounter": encounter,
                "risk": risk,
            }
        )

    return {
        "items": enriched,
        "count": len(enriched),
        "nextToken": next_page_token,
    }


# ---------------------------------------------------------------------
# Airports
# ---------------------------------------------------------------------

def list_airports(
    limit,
    next_token=None,
    weather_risk=None,
    weather_impact=None,
):
    weather_risk = (
        weather_risk.strip().upper()
        if weather_risk
        else None
    )

    weather_impact = (
        weather_impact.strip().upper()
        if weather_impact
        else None
    )

    now = int(time.time())

    # -------------------------------------------------------------
    # Weather-impact GSI
    # -------------------------------------------------------------
    if weather_impact:
        page = operational_readers.query_airports_by_impact_page(
            AIRPORTS,
            weather_impact=weather_impact,
            now_epoch=now,
            limit=limit,
            weather_risk=weather_risk,
            exclusive_start_key=_decode_token(next_token),
            key=Key,
            attr=Attr,
        )
        return _page_with_items(
            {"LastEvaluatedKey": page["last_evaluated_key"]},
            [
                item
                for item in page["items"]
                if current_set.is_current_airport_status(item, now)
            ],
        )

    # -------------------------------------------------------------
    # Weather-risk GSI
    # -------------------------------------------------------------
    if weather_risk:
        page = operational_readers.query_airports_by_risk_page(
            AIRPORTS,
            weather_risk=weather_risk,
            now_epoch=now,
            limit=limit,
            exclusive_start_key=_decode_token(next_token),
            key=Key,
            attr=Attr,
        )
        return _page_with_items(
            {"LastEvaluatedKey": page["last_evaluated_key"]},
            [
                item
                for item in page["items"]
                if current_set.is_current_airport_status(item, now)
            ],
        )

    # -------------------------------------------------------------
    # Small airport list.
    # -------------------------------------------------------------
    page = operational_readers.scan_airports_page(
        AIRPORTS,
        now_epoch=now,
        limit=limit,
        exclusive_start_key=_decode_token(next_token),
        attr=Attr,
    )
    return _page_with_items(
        {"LastEvaluatedKey": page["last_evaluated_key"]},
        [
            item
            for item in page["items"]
            if current_set.is_current_airport_status(item, now)
        ],
    )


def get_airport_detail(airport_id):
    airport_id = (
        airport_id or ""
    ).strip().upper()

    if not airport_id:
        raise ValueError(
            "airportId is required"
        )

    # -------------------------------------------------------------
    # Derived AirportStatus
    # -------------------------------------------------------------
    status = operational_readers.get_airport_status_record(
        AIRPORTS,
        airport_id,
        consistent_read=True,
    )

    if not status:
        return None

    station_id = (
        status.get("station_id")
        or airport_id
    )

    # -------------------------------------------------------------
    # Current weather
    # -------------------------------------------------------------
    metar = operational_readers.get_metar_record(
        METAR,
        station_id,
        consistent_read=True,
    )

    taf = operational_readers.get_taf_record(
        TAF,
        station_id,
        consistent_read=True,
    )

    # -------------------------------------------------------------
    # Forecast periods around the operational window.
    #
    # Previous 6 hours through next 36 hours.
    # -------------------------------------------------------------
    now = int(time.time())

    periods_page = operational_readers.query_taf_period_candidates_page(
        TAF_PERIODS,
        station_id=station_id,
        now_epoch=now,
        limit=50,
        key=Key,
    )

    # -------------------------------------------------------------
    # Recent candidate/diversion assessments for this airport.
    # -------------------------------------------------------------
    assessments = _query_latest(
        AIRPORT_ASSESSMENTS,
        IDX_AIRPORT_ASSESSMENT_AIRPORT_TIME,
        "airport_id",
        airport_id,
        limit=10,
    )

    return {
        "airport": status,
        "metar": metar,
        "taf": taf,
        "tafForecastPeriods": periods_page["items"],
        "recentAssessments": assessments,
    }


# ---------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------

def _latest_current_risks():
    """
    Latest unexpired risk per current encounter.

    Same selection as overview.topRisks / encounter risk KPIs. Cached so
    overview, recommendation, and alert loaders share one risk scan.
    """

    def _load():
        snapshot = _current_encounter_snapshot()
        current_encounter_ids = {
            item.get("encounter_id")
            for item in snapshot["items"]
            if item.get("encounter_id")
        }

        risks = operational_readers.scan_risk_candidates(
            RISKS,
            scan_all=_scan_all,
        )

        latest_by_encounter = {}

        for risk in risks:
            encounter_id = risk.get("encounter_id")
            valid_until = risk.get("valid_until_utc")
            existing = latest_by_encounter.get(encounter_id)
            reference_epoch = (
                int(time.time())
                if valid_until
                else 0
            )
            selected = current_set.choose_latest_current_risk(
                existing,
                risk,
                current_encounter_ids=current_encounter_ids,
                now_epoch=reference_epoch,
            )

            if selected is not existing and encounter_id:
                latest_by_encounter[encounter_id] = selected

        items = list(latest_by_encounter.values())
        current_risk_ids = {
            item.get("risk_id")
            for item in items
            if item.get("risk_id")
        }

        return {
            "by_encounter": latest_by_encounter,
            "items": items,
            "current_risk_ids": current_risk_ids,
        }

    return _cached(
        "latest_current_risks",
        CURRENT_ENCOUNTER_CACHE_TTL_SECONDS,
        _load,
    )


def _active_recommendations():
    def _load():
        now_iso = _now_iso()
        items = operational_readers.scan_recommendation_candidates(
            RECOMMENDATIONS,
            now_iso=now_iso,
            project=True,
            attr=Attr,
            scan_all=_scan_all,
        )
        return {"items": items}

    return _cached(
        "active_recommendations",
        CURRENT_ENCOUNTER_CACHE_TTL_SECONDS,
        _load,
    )


def _current_recommendation_snapshot():
    def _load():
        current_risk_ids = _latest_current_risks()["current_risk_ids"]
        now_iso = _now_iso()
        recommendations = operational_readers.scan_recommendation_candidates(
            RECOMMENDATIONS,
            now_iso=now_iso,
            project=False,
            attr=Attr,
            scan_all=_scan_all,
        )
        current_items = [
            item
            for item in recommendations
            if current_set.is_current_recommendation(
                item,
                current_risk_ids=current_risk_ids,
            )
            and current_set.is_lifecycle_active_recommendation(
                item,
                int(time.time()),
            )
        ]
        return {
            "items": current_items,
            "active_count": len(recommendations),
            "active_items": recommendations,
        }

    return _cached(
        "current_recommendations",
        CURRENT_ENCOUNTER_CACHE_TTL_SECONDS,
        _load,
    )


def list_active_recommendations(
    limit,
    next_token=None,
):
    snapshot = _current_recommendation_snapshot()
    items = sorted(
        snapshot["items"],
        key=lambda item: int(
            item.get("created_at_epoch") or 0
        ),
        reverse=True,
    )

    offset = 0

    if next_token:
        start = _decode_token(next_token)
        offset = int(start.get("offset") or 0)

    page = items[offset:offset + limit]
    next_offset = offset + len(page)
    next_page_token = None

    if next_offset < len(items):
        next_page_token = _encode_token(
            {
                "offset": next_offset,
            }
        )

    return {
        "items": page,
        "count": len(page),
        "nextToken": next_page_token,
    }


# ---------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------

def _current_risk_and_recommendation_ids():
    """
    Current decision-chain IDs used to decide whether an alert is current.

    Same join keys as overview.alerts.currentCount: latest unexpired risk on a
    current encounter, then ACTIVE recommendations on those risk IDs.
    """

    current_risk_ids = _latest_current_risks()["current_risk_ids"]
    recommendation_items = _active_recommendations()["items"]

    current_recommendation_ids = {
        item.get("recommendation_id")
        for item in recommendation_items
        if current_set.is_current_recommendation(
            item,
            current_risk_ids=current_risk_ids,
        )
        and item.get("recommendation_id")
    }

    return current_risk_ids, current_recommendation_ids


def _active_alerts():
    def _load():
        now_iso = _now_iso()
        items = operational_readers.scan_alert_candidates(
            ALERTS,
            now_iso=now_iso,
            project=True,
            attr=Attr,
            scan_all=_scan_all,
            alert_states=current_set.CURRENT_ALERT_STATES,
        )
        by_state = Counter(
            str(item.get("alert_state", "UNKNOWN")).upper()
            for item in items
        )
        return {
            "items": items,
            "active_count": len(items),
            "by_state": by_state,
        }

    return _cached(
        "active_alerts",
        CURRENT_ENCOUNTER_CACHE_TTL_SECONDS,
        _load,
    )


def _current_alert_snapshot():
    def _load():
        current_risk_ids, current_recommendation_ids = (
            _current_risk_and_recommendation_ids()
        )
        now_iso = _now_iso()
        alerts = operational_readers.scan_alert_candidates(
            ALERTS,
            now_iso=now_iso,
            project=False,
            attr=Attr,
            scan_all=_scan_all,
            alert_states=current_set.CURRENT_ALERT_STATES,
        )
        current_items = [
            item
            for item in alerts
            if current_set.is_current_alert(
                item,
                current_risk_ids=current_risk_ids,
                current_recommendation_ids=current_recommendation_ids,
            )
            and current_set.is_lifecycle_active_alert(
                item,
                int(time.time()),
            )
        ]
        return {
            "items": current_items,
            "active_count": len(alerts),
            "by_state": Counter(
                str(item.get("alert_state", "UNKNOWN")).upper()
                for item in alerts
            ),
            "active_items": alerts,
        }

    return _cached(
        "current_alerts",
        CURRENT_ENCOUNTER_CACHE_TTL_SECONDS,
        _load,
    )


def list_active_alerts(
    limit,
    next_token=None,
):
    snapshot = _current_alert_snapshot()
    items = sorted(
        snapshot["items"],
        key=lambda item: int(
            item.get("updated_at_epoch") or 0
        ),
        reverse=True,
    )

    offset = 0

    if next_token:
        start = _decode_token(next_token)
        offset = int(start.get("offset") or 0)

    page = items[offset:offset + limit]
    next_offset = offset + len(page)
    next_page_token = None

    if next_offset < len(items):
        next_page_token = _encode_token(
            {
                "offset": next_offset,
            }
        )

    return {
        "items": page,
        "count": len(page),
        "nextToken": next_page_token,
    }

# =====================================================================
# DATA FRESHNESS
# =====================================================================

def get_freshness():
    def build():
        generated_at = _now_iso()

        # -------------------------------------------------------------
        # Aircraft
        #
        # AircraftCurrentState is continuously replaced with the latest
        # OpenSky state. position_time_epoch is therefore a useful
        # operational-data freshness timestamp.
        # -------------------------------------------------------------

        aircraft_epoch = (
            _latest_epoch_from_table(
                AIRCRAFT,
                numeric_fields=[
                    "position_time_epoch",
                ],
                iso_fields=[
                    "processed_at_utc",
                ],
            )
        )

        # -------------------------------------------------------------
        # METAR
        #
        # Prefer processor/materialization timestamps, while retaining
        # observed_time_epoch as a fallback.
        # -------------------------------------------------------------

        metar_epoch = (
            _latest_epoch_from_table(
                METAR,
                numeric_fields=[
                    "observed_time_epoch",
                ],
                iso_fields=[
                    "processed_at_utc",
                    "updated_at_utc",
                ],
            )
        )

        # -------------------------------------------------------------
        # TAF
        #
        # TAF records carry issued_at_epoch plus processing and
        # materialization timestamps.
        # -------------------------------------------------------------

        taf_epoch = (
            _latest_epoch_from_table(
                TAF,
                numeric_fields=[
                    "issued_at_epoch",
                ],
                iso_fields=[
                    "processed_at_utc",
                    "materialized_at_utc",
                    "updated_at_utc",
                ],
            )
        )

        # -------------------------------------------------------------
        # SIGMET
        #
        # Unlike aircraft/METAR, an unchanged valid SIGMET can remain
        # current without receiving a new product every few minutes.
        #
        # Therefore we expose the newest materialized hazard timestamp,
        # but we do not call an old product "STALE" solely because the
        # hazard itself has not changed.
        #
        # Poller/feed health belongs in /system-health via CloudWatch.
        # -------------------------------------------------------------

        sigmet_epoch = (
            _latest_epoch_from_table(
                HAZARDS,
                iso_fields=[
                    "materialized_at_utc",
                    "created_at_utc",
                    "updated_at_utc",
                ],
            )
        )

        sigmet_record = {
            "latestAt": (
                _epoch_to_iso(
                    sigmet_epoch
                )
                if sigmet_epoch
                is not None
                else None
            ),
            "ageSeconds": (
                max(
                    0,
                    int(
                        time.time()
                    )
                    - sigmet_epoch,
                )
                if sigmet_epoch
                is not None
                else None
            ),
            "status": (
                "AVAILABLE"
                if sigmet_epoch
                is not None
                else "UNAVAILABLE"
            ),
            "note": (
                "SIGMET table age reflects the newest "
                "materialized hazard product. Poller health "
                "is evaluated separately by /system-health."
            ),
        }

        return {
            "generatedAt": generated_at,
            "mode": (
                "SOURCE_TABLE_LATEST_RECORD"
            ),
            "sources": {
                "opensky": (
                    _freshness_record(
                        aircraft_epoch,
                        fresh_seconds=90,
                        stale_seconds=180,
                    )
                ),
                "sigmet": (
                    sigmet_record
                ),
                "metar": (
                    _freshness_record(
                        metar_epoch,
                        fresh_seconds=600,
                        stale_seconds=1800,
                    )
                ),
                "taf": (
                    _freshness_record(
                        taf_epoch,
                        fresh_seconds=7200,
                        stale_seconds=21600,
                    )
                ),
            },
        }

    return _cached(
        "freshness",
        15,
        build,
    )

# =====================================================================
# OPERATIONS OVERVIEW
# =====================================================================

def get_overview():
    """
    Build the operational dashboard summary.

    Important:
    This endpoint intentionally avoids loading full DynamoDB records
    unless they are needed by the dashboard.

    Detailed objects belong in:
      /aircraft/{id}
      /encounters/active
      /airports/{id}
      /recommendations/active

    /overview should remain a compact operational summary.
    """

    def build():
        now_epoch = int(
            time.time()
        )

        # =============================================================
        # AIRCRAFT
        #
        # We only need a count here.
        #
        # Do NOT load thousands of complete aircraft records.
        # =============================================================

        aircraft_count = _scan_count(
            AIRCRAFT,
            FilterExpression=(
                Attr(
                    "expires_at_epoch"
                ).gt(
                    now_epoch
                )
            ),
        )

        # =============================================================
        # ACTIVE HAZARDS
        #
        # Again, overview only needs the count.
        #
        # Hazard geometry belongs in:
        #
        # GET /hazards/active
        # =============================================================

        hazard_count = _query_count(
            HAZARDS,
            IndexName=(
                IDX_HAZARD_STATUS_VALIDITY
            ),
            KeyConditionExpression=(
                Key(
                    "status"
                ).eq(
                    "ACTIVE"
                )
                & Key(
                    "valid_to_epoch"
                ).gte(
                    now_epoch
                )
            ),
        )

        # =============================================================
        # ACTIVE ENCOUNTERS
        #
        # We need encounter IDs so we can associate the latest RiskResult.
        #
        # Critically, do NOT return/load:
        #
        # matched_h3_cells
        # geometry metadata
        # large encounter payloads
        #
        # Those made the original overview unnecessarily expensive.
        # =============================================================

        current_snapshot = (
            _current_encounter_snapshot()
        )

        encounters = current_snapshot[
            "items"
        ]

        encounter_count = len(
            encounters
        )

        # =============================================================
        # RISK
        #
        # Latest unexpired risk per current encounter. Shared with the
        # recommendation and alert current-set loaders so /overview does
        # not scan RiskResults a second time.
        # =============================================================

        risk_snapshot = (
            _latest_current_risks()
        )

        latest_risks = risk_snapshot[
            "items"
        ]

        current_risk_ids = risk_snapshot[
            "current_risk_ids"
        ]

        risk_counts = Counter(
            str(
                risk.get(
                    "risk_level",
                    "UNKNOWN",
                )
            ).upper()
            for risk in latest_risks
        )

        top_risks = sorted(
            latest_risks,
            key=lambda item: (
                _risk_rank(
                    item.get(
                        "risk_level"
                    )
                ),
                _risk_score_value(
                    item
                ),
                int(
                    item.get(
                        "generated_at_epoch",
                        0,
                    )
                    or 0
                ),
            ),
            reverse=True,
        )[:5]

        # =============================================================
        # RECOMMENDATIONS
        #
        # Count all currently active recommendations but retrieve only
        # five compact recommendation records for the dashboard.
        #
        # /recommendations/active still loads full records. Overview
        # must not wait on that heavier scan.
        # =============================================================

        recommendation_items = (
            _active_recommendations()[
                "items"
            ]
        )

        recommendation_count = len(
            recommendation_items
        )

        current_recommendation_items = [
            item
            for item in recommendation_items
            if current_set.is_current_recommendation(
                item,
                current_risk_ids=current_risk_ids,
            )
        ]

        current_recommendation_ids = {
            item.get("recommendation_id")
            for item in current_recommendation_items
            if item.get("recommendation_id")
        }

        latest_recommendations = [
            {
                "recommendationId": item.get(
                    "recommendation_id"
                ),
                "aircraftId": item.get(
                    "aircraft_id"
                ),
                "hazardId": item.get(
                    "hazard_id"
                ),
                "riskLevel": item.get(
                    "risk_level"
                ),
                "riskScore": item.get(
                    "risk_score"
                ),
                "confidence": item.get(
                    "confidence"
                ),
                "action": item.get(
                    "primary_action_type"
                ),
                "preferredAirportId": item.get(
                    "preferred_airport_id"
                ),
                "preferredAirportScore": item.get(
                    "preferred_airport_score"
                ),
                "validUntilUtc": item.get(
                    "valid_until_utc"
                ),
                "createdAtUtc": item.get(
                    "created_at_utc"
                ),
            }
            for item in sorted(
                [
                    item
                    for item in current_recommendation_items
                    if _is_future_iso(
                        item.get("valid_until_utc")
                    )
                ],
                key=lambda item: int(
                    item.get("created_at_epoch") or 0
                ),
                reverse=True,
            )[:5]
        ]

        # =============================================================
        # ALERTS
        #
        # Counts use every ACTIVE+valid alert. currentCount still uses
        # the current-set filter. The scan is shared with /alerts/active.
        # =============================================================

        alerts = _active_alerts()[
            "items"
        ]

        current_alerts = [
            item
            for item in alerts
            if current_set.is_current_alert(
                item,
                current_risk_ids=current_risk_ids,
                current_recommendation_ids=current_recommendation_ids,
            )
        ]

        alert_counts = Counter(
            str(
                item.get(
                    "alert_state",
                    "UNKNOWN",
                )
            ).upper()
            for item in alerts
        )

        alert_count = len(
            alerts
        )

        # =============================================================
        # AIRPORTS
        #
        # Retrieve only the small set of attributes required to create
        # airport dashboard KPIs.
        # =============================================================

        airports = _scan_all(
            AIRPORTS,
            FilterExpression=(
                Attr(
                    "expires_at_epoch"
                ).gt(
                    now_epoch
                )
            ),
            ProjectionExpression=(
                "airport_id,"
                "station_id,"
                "weather_risk_level,"
                "weather_impact_status,"
                "updated_at_epoch,"
                "updated_at_utc,"
                "metar_freshness_status,"
                "taf_freshness_status,"
                "is_diversion_weather_ready"
            ),
        )

        airport_count = len(
            airports
        )

        weather_risk_counts = Counter(
            str(
                item.get(
                    "weather_risk_level",
                    "UNKNOWN",
                )
            ).upper()
            for item in airports
        )

        weather_impact_counts = Counter(
            str(
                item.get(
                    "weather_impact_status",
                    "UNKNOWN",
                )
            ).upper()
            for item in airports
        )

        impacted_statuses = {
            "WEATHER_IMPACTED",
            "IMPACTED",
        }

        weather_impacted_count = sum(
            1
            for item in airports
            if str(
                item.get(
                    "weather_impact_status",
                    "",
                )
            ).upper()
            in impacted_statuses
        )

        top_impacted_airports = sorted(
            [
                item
                for item in airports
                if (
                    str(
                        item.get(
                            "weather_impact_status",
                            "",
                        )
                    ).upper()
                    in impacted_statuses
                    or str(
                        item.get(
                            "weather_risk_level",
                            "",
                        )
                    ).upper()
                    in {
                        "HIGH",
                        "MEDIUM",
                    }
                )
            ],
            key=lambda item: (
                _risk_rank(
                    item.get(
                        "weather_risk_level"
                    )
                ),
                int(
                    item.get(
                        "updated_at_epoch",
                        0,
                    )
                    or 0
                ),
            ),
            reverse=True,
        )[:5]

        # =============================================================
        # RESPONSE
        # =============================================================

        return {
            "generatedAt": (
                _now_iso()
            ),

            "aircraft": {
                "activeCount": (
                    aircraft_count
                ),
            },

            "hazards": {
                "activeCount": (
                    hazard_count
                ),
            },

            "encounters": {
                "activeCount": (
                    encounter_count
                ),

                "riskEvaluatedCount": (
                    len(
                        latest_risks
                    )
                ),

                "highRiskCount": (
                    risk_counts.get(
                        "HIGH",
                        0,
                    )
                ),

                "mediumRiskCount": (
                    risk_counts.get(
                        "MEDIUM",
                        0,
                    )
                ),

                "lowRiskCount": (
                    risk_counts.get(
                        "LOW",
                        0,
                    )
                ),

                "riskCounts": dict(
                    risk_counts
                ),
            },

            "recommendations": {
                "activeCount": (
                    recommendation_count
                ),

                "currentCount": (
                    len(
                        current_recommendation_items
                    )
                ),

                "latest": (
                    latest_recommendations
                ),
            },

            "alerts": {
                "activeCount": (
                    alert_count
                ),

                "currentCount": (
                    len(
                        current_alerts
                    )
                ),

                "byState": dict(
                    alert_counts
                ),
            },

            "airports": {
                "currentCount": (
                    airport_count
                ),

                "weatherImpactedCount": (
                    weather_impacted_count
                ),

                "byWeatherRisk": dict(
                    weather_risk_counts
                ),

                "byWeatherImpact": dict(
                    weather_impact_counts
                ),

                "topImpacted": (
                    top_impacted_airports
                ),
            },

            "topRisks": (
                top_risks
            ),
        }

    # First request still has to compose the dashboard.
    #
    # After that, warm Lambda environments reuse the summary for
    # forty-five seconds, preventing React dashboard refreshes from
    # repeatedly scanning operational tables.

    return _cached(
        "overview",
        45,
        build,
    )

# =====================================================================
# SYSTEM HEALTH
# =====================================================================

def get_system_health():
    def build():
        now = datetime.now(
            timezone.utc
        )

        start = (
            now
            - timedelta(
                minutes=5
            )
        )

        # -------------------------------------------------------------
        # Lambda account capacity
        # -------------------------------------------------------------

        account = (
            LAMBDA_CLIENT
            .get_account_settings()
        )

        limits = (
            account.get(
                "AccountLimit",
                {},
            )
        )

        total_concurrency = int(
            limits.get(
                "ConcurrentExecutions",
                0,
            )
            or 0
        )

        unreserved_concurrency = int(
            limits.get(
                "UnreservedConcurrentExecutions",
                0,
            )
            or 0
        )

        reserved_concurrency = max(
            0,
            (
                total_concurrency
                - unreserved_concurrency
            ),
        )

        # -------------------------------------------------------------
        # Regional Lambda concurrency
        # -------------------------------------------------------------

        concurrency_response = (
            CLOUDWATCH
            .get_metric_statistics(
                Namespace="AWS/Lambda",
                MetricName=(
                    "ConcurrentExecutions"
                ),
                StartTime=start,
                EndTime=now,
                Period=60,
                Statistics=[
                    "Maximum",
                ],
            )
        )

        concurrency_points = (
            concurrency_response.get(
                "Datapoints",
                [],
            )
        )

        max_concurrency = max(
            (
                float(
                    point.get(
                        "Maximum",
                        0,
                    )
                )
                for point
                in concurrency_points
            ),
            default=0.0,
        )

        # -------------------------------------------------------------
        # Operational API throttling
        # -------------------------------------------------------------

        api_throttle_response = (
            CLOUDWATCH
            .get_metric_statistics(
                Namespace="AWS/Lambda",
                MetricName="Throttles",
                Dimensions=[
                    {
                        "Name": (
                            "FunctionName"
                        ),
                        "Value": (
                            OPERATIONAL_API_FUNCTION_NAME
                        ),
                    }
                ],
                StartTime=start,
                EndTime=now,
                Period=60,
                Statistics=[
                    "Sum",
                ],
            )
        )

        api_throttles = sum(
            float(
                point.get(
                    "Sum",
                    0,
                )
            )
            for point
            in (
                api_throttle_response
                .get(
                    "Datapoints",
                    [],
                )
            )
        )

        # -------------------------------------------------------------
        # Active Wilvor CloudWatch alarms
        # -------------------------------------------------------------

        active_alarms = []

        paginator = (
            CLOUDWATCH
            .get_paginator(
                "describe_alarms"
            )
        )

        for page in paginator.paginate(
            AlarmNamePrefix=(
                f"{NAME_PREFIX}-"
            ),
            StateValue="ALARM",
        ):
            for alarm in page.get(
                "MetricAlarms",
                [],
            ):
                active_alarms.append(
                    {
                        "alarmName": (
                            alarm.get(
                                "AlarmName"
                            )
                        ),
                        "metricName": (
                            alarm.get(
                                "MetricName"
                            )
                        ),
                        "namespace": (
                            alarm.get(
                                "Namespace"
                            )
                        ),
                        "state": (
                            alarm.get(
                                "StateValue"
                            )
                        ),
                        "reason": (
                            alarm.get(
                                "StateReason"
                            )
                        ),
                        "updatedAt": (
                            alarm.get(
                                "StateUpdatedTimestamp"
                            )
                            .isoformat()
                            if alarm.get(
                                "StateUpdatedTimestamp"
                            )
                            else None
                        ),
                    }
                )

            for alarm in page.get(
                "CompositeAlarms",
                [],
            ):
                active_alarms.append(
                    {
                        "alarmName": (
                            alarm.get(
                                "AlarmName"
                            )
                        ),
                        "metricName": None,
                        "namespace": None,
                        "state": (
                            alarm.get(
                                "StateValue"
                            )
                        ),
                        "reason": (
                            alarm.get(
                                "StateReason"
                            )
                        ),
                        "updatedAt": (
                            alarm.get(
                                "StateUpdatedTimestamp"
                            )
                            .isoformat()
                            if alarm.get(
                                "StateUpdatedTimestamp"
                            )
                            else None
                        ),
                    }
                )

        # -------------------------------------------------------------
        # Freshness summary
        # -------------------------------------------------------------

        freshness = (
            get_freshness()
        )

        freshness_issues = []

        for source, details in (
            freshness.get(
                "sources",
                {}
            ).items()
        ):
            if (
                details.get(
                    "status"
                )
                in {
                    "STALE",
                    "UNAVAILABLE",
                }
            ):
                freshness_issues.append(
                    source
                )

        # -------------------------------------------------------------
        # Concurrency utilization
        # -------------------------------------------------------------

        if total_concurrency > 0:
            concurrency_utilization = round(
                (
                    max_concurrency
                    / total_concurrency
                )
                * 100,
                1,
            )

        else:
            concurrency_utilization = None

        # -------------------------------------------------------------
        # Overall health
        # -------------------------------------------------------------

        status = "HEALTHY"

        if (
            active_alarms
            or freshness_issues
            or api_throttles > 0
            or (
                concurrency_utilization
                is not None
                and concurrency_utilization
                >= 90
            )
        ):
            status = "DEGRADED"

        if (
            api_throttles > 0
            and concurrency_utilization
            is not None
            and concurrency_utilization
            >= 95
        ):
            status = "CRITICAL"

        return {
            "generatedAt": (
                _now_iso()
            ),

            "status": status,

            "lambda": {
                "account": {
                    "concurrencyLimit": (
                        total_concurrency
                    ),
                    "unreservedConcurrency": (
                        unreserved_concurrency
                    ),
                    "reservedConcurrency": (
                        reserved_concurrency
                    ),
                },

                "recent": {
                    "windowMinutes": 5,
                    "maxConcurrentExecutions": (
                        max_concurrency
                    ),
                    "concurrencyUtilizationPercent": (
                        concurrency_utilization
                    ),
                },

                "operationalApi": {
                    "functionName": (
                        OPERATIONAL_API_FUNCTION_NAME
                    ),
                    "throttlesLast5Minutes": (
                        api_throttles
                    ),
                },
            },

            "cloudWatch": {
                "activeAlarmCount": (
                    len(
                        active_alarms
                    )
                ),
                "activeAlarms": (
                    active_alarms[:50]
                ),
            },

            "dataFreshness": {
                "status": (
                    "DEGRADED"
                    if freshness_issues
                    else "HEALTHY"
                ),
                "problemSources": (
                    freshness_issues
                ),
                "sources": (
                    freshness.get(
                        "sources",
                        {},
                    )
                ),
            },
        }

    return _cached(
        "system-health",
        15,
        build,
    )