import hashlib
import json
import math
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key


dynamodb = boto3.resource("dynamodb")
events = boto3.client("events")


RISK_RESULTS_TABLE_NAME = os.environ[
    "RISK_RESULTS_TABLE_NAME"
]

AIRCRAFT_CURRENT_STATE_TABLE_NAME = os.environ[
    "AIRCRAFT_CURRENT_STATE_TABLE_NAME"
]

AIRPORT_STATUS_TABLE_NAME = os.environ[
    "AIRPORT_STATUS_TABLE_NAME"
]

TAF_FORECAST_PERIODS_TABLE_NAME = os.environ[
    "TAF_FORECAST_PERIODS_TABLE_NAME"
]

AIRPORT_ASSESSMENT_TABLE_NAME = os.environ[
    "AIRPORT_ASSESSMENT_TABLE_NAME"
]

TAF_STATION_PERIOD_INDEX_NAME = os.environ.get(
    "TAF_STATION_PERIOD_INDEX_NAME",
    "station_id-period_from_epoch-index",
)

EVENT_BUS_NAME = os.environ.get(
    "EVENT_BUS_NAME",
    "default",
)

SEARCH_RADIUS_NM = float(
    os.environ.get("SEARCH_RADIUS_NM", "250")
)

MAX_CANDIDATES = int(
    os.environ.get("MAX_CANDIDATES", "10")
)

ETA_UNCERTAINTY_MINUTES = int(
    os.environ.get(
        "ETA_UNCERTAINTY_MINUTES",
        "10",
    )
)

RETENTION_SECONDS = int(
    os.environ.get(
        "RETENTION_SECONDS",
        "86400",
    )
)

RULESET_VERSION = os.environ.get(
    "ASSESSMENT_RULESET_VERSION",
    "wilvor.airport-assessment.ruleset.v1",
)

SCHEMA_VERSION = os.environ.get(
    "SCHEMA_VERSION",
    "wilvor.airport_assessment.v1",
)


risk_table = dynamodb.Table(
    RISK_RESULTS_TABLE_NAME
)

aircraft_table = dynamodb.Table(
    AIRCRAFT_CURRENT_STATE_TABLE_NAME
)

airport_status_table = dynamodb.Table(
    AIRPORT_STATUS_TABLE_NAME
)

taf_periods_table = dynamodb.Table(
    TAF_FORECAST_PERIODS_TABLE_NAME
)

assessment_table = dynamodb.Table(
    AIRPORT_ASSESSMENT_TABLE_NAME
)


def utc_now():
    return datetime.now(timezone.utc)


def iso_utc(dt):
    return dt.astimezone(
        timezone.utc
    ).isoformat()


def to_decimal(value):
    if isinstance(value, float):
        return Decimal(str(value))

    if isinstance(value, dict):
        return {
            k: to_decimal(v)
            for k, v in value.items()
        }

    if isinstance(value, list):
        return [
            to_decimal(v)
            for v in value
        ]

    return value


def json_default(value):
    if isinstance(value, Decimal):
        return float(value)

    raise TypeError()


def stable_id(*parts):
    raw = "|".join(
        str(part)
        for part in parts
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def as_float(value):
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def distance_nm(
    lat1,
    lon1,
    lat2,
    lon2,
):
    radius_nm = 3440.065

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)

    dp = math.radians(
        lat2 - lat1
    )

    dl = math.radians(
        lon2 - lon1
    )

    a = (
        math.sin(dp / 2) ** 2
        + math.cos(p1)
        * math.cos(p2)
        * math.sin(dl / 2) ** 2
    )

    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a),
    )

    return radius_nm * c


def get_risk(risk_id):
    return risk_table.get_item(
        Key={
            "risk_id": risk_id
        },
        ConsistentRead=True,
    ).get("Item")


def get_aircraft(aircraft_id):
    return aircraft_table.get_item(
        Key={
            "aircraft_id": aircraft_id
        },
        ConsistentRead=True,
    ).get("Item")


def scan_airports():
    items = []

    kwargs = {}

    while True:
        response = (
            airport_status_table.scan(
                **kwargs
            )
        )

        items.extend(
            response.get(
                "Items",
                [],
            )
        )

        key = response.get(
            "LastEvaluatedKey"
        )

        if not key:
            break

        kwargs[
            "ExclusiveStartKey"
        ] = key

    return items


def query_taf_periods(
    *,
    station_id,
    taf_version_key,
    window_start_epoch,
    window_end_epoch,
):
    response = taf_periods_table.query(
        IndexName=(
            TAF_STATION_PERIOD_INDEX_NAME
        ),
        KeyConditionExpression=(
            Key("station_id").eq(
                station_id
            )
            & Key(
                "period_from_epoch"
            ).lte(
                Decimal(
                    str(window_end_epoch)
                )
            )
        ),
    )

    rows = []

    for item in response.get(
        "Items",
        [],
    ):
        if (
            item.get(
                "taf_version_key"
            )
            != taf_version_key
        ):
            continue

        period_to = as_float(
            item.get(
                "period_to_epoch"
            )
        )

        if period_to is None:
            continue

        if (
            period_to
            >= window_start_epoch
        ):
            rows.append(item)

    return rows


def current_weather_score(level):
    mapping = {
        "LOW": 100.0,
        "MEDIUM": 60.0,
        "HIGH": 10.0,
    }

    return mapping.get(
        str(level or "").upper()
    )


def taf_period_score(period):
    category = str(
        period.get(
            "forecast_flight_category"
        )
        or ""
    ).upper()

    visibility = as_float(
        period.get(
            "visibility_sm"
        )
    )

    ceiling = as_float(
        period.get(
            "ceiling_ft"
        )
    )

    wind = as_float(
        period.get(
            "wind_speed_kt"
        )
    )

    gust = as_float(
        period.get(
            "wind_gust_kt"
        )
    )

    weather_codes = [
        str(code).upper()
        for code in (
            period.get(
                "weather_codes"
            )
            or []
        )
    ]

    if (
        category in {
            "IFR",
            "LIFR",
        }
        or (
            visibility is not None
            and visibility < 3
        )
        or (
            ceiling is not None
            and ceiling < 1000
        )
        or any(
            "TS" in code
            for code in weather_codes
        )
        or (
            gust is not None
            and gust >= 35
        )
        or (
            wind is not None
            and wind >= 30
        )
    ):
        return 10.0

    if (
        category == "MVFR"
        or (
            visibility is not None
            and visibility <= 5
        )
        or (
            ceiling is not None
            and ceiling <= 3000
        )
        or (
            gust is not None
            and gust >= 25
        )
    ):
        return 60.0

    return 100.0


def distance_score(
    distance,
):
    ratio = min(
        distance
        / SEARCH_RADIUS_NM,
        1.0,
    )

    return (
        100.0
        * (1.0 - ratio)
    )


def build_candidate(
    *,
    risk,
    aircraft,
    airport,
    evaluation_id,
    now,
):
    airport_id = airport.get(
        "airport_id"
    )

    lat = as_float(
        airport.get(
            "latitude"
        )
    )

    lon = as_float(
        airport.get(
            "longitude"
        )
    )

    aircraft_lat = as_float(
        aircraft.get(
            "latitude"
        )
    )

    aircraft_lon = as_float(
        aircraft.get(
            "longitude"
        )
    )

    speed = as_float(
        aircraft.get(
            "ground_speed_kt"
        )
    )

    if (
        lat is None
        or lon is None
        or aircraft_lat is None
        or aircraft_lon is None
    ):
        return None

    dist = distance_nm(
        aircraft_lat,
        aircraft_lon,
        lat,
        lon,
    )

    if dist > SEARCH_RADIUS_NM:
        return None

    if not speed or speed <= 50:
        return None

    eta_minutes = (
        dist
        / speed
        * 60.0
    )

    arrival_epoch = (
        int(now.timestamp())
        + int(
            eta_minutes * 60
        )
    )

    arrival_utc = datetime.fromtimestamp(
        arrival_epoch,
        tz=timezone.utc,
    )

    window_seconds = (
        ETA_UNCERTAINTY_MINUTES
        * 60
    )

    station_id = (
        airport.get(
            "station_id"
        )
        or airport_id
    )

    assessment_id = (
        "aa#"
        + stable_id(
            evaluation_id,
            airport_id,
        )[:40]
    )

    limitations = [
        "Route hazard evaluation is not implemented yet.",
        "Runway suitability evidence is not implemented yet.",
        "Airport congestion evidence is not implemented yet.",
    ]

    item = {
        "evaluation_id": evaluation_id,
        "evaluation_version": (
            RULESET_VERSION
        ),
        "airport_id": airport_id,
        "airport_assessment_id": (
            assessment_id
        ),
        "risk_id": risk["risk_id"],
        "aircraft_id": (
            risk["aircraft_id"]
        ),
        "aircraft_state_version": (
            aircraft["state_version"]
        ),
        "hazard_id": (
            risk["hazard_id"]
        ),
        "hazard_source_version": (
            risk.get(
                "hazard_source_version",
                "UNKNOWN",
            )
        ),
        "airport_name": (
            airport.get(
                "station_name"
            )
            or airport_id
        ),
        "airport_latitude": lat,
        "airport_longitude": lon,
        "candidate_reason": (
            "Within diversion search radius."
        ),
        "search_origin_type": (
            "AIRCRAFT_CURRENT_POSITION"
        ),
        "search_radius_nm": (
            SEARCH_RADIUS_NM
        ),
        "hard_filter_passed": True,
        "rejection_reasons": [],
        "distance_nm": dist,
        "eta_minutes": (
            eta_minutes
        ),
        "estimated_arrival_time_utc": (
            iso_utc(
                arrival_utc
            )
        ),
        "eta_uncertainty_minutes": (
            ETA_UNCERTAINTY_MINUTES
        ),
        "metar_version": (
            airport.get(
                "metar_version"
            )
            or airport.get(
                "source_metar_version"
            )
        ),
        "taf_version": (
            airport.get(
                "source_taf_version"
            )
        ),
        "weather_risk_level": (
            airport.get(
                "weather_risk_level",
                "UNKNOWN",
            )
        ),
        "route_safety_status": (
            "UNAVAILABLE"
        ),
        "runway_evidence_status": (
            "UNAVAILABLE"
        ),
        "congestion_evidence_status": (
            "UNAVAILABLE"
        ),
        "known_limitations": (
            limitations
        ),
        "assessment_ruleset_version": (
            RULESET_VERSION
        ),
        "schema_version": (
            SCHEMA_VERSION
        ),
        "created_at_utc": (
            iso_utc(now)
        ),
        "created_at_epoch": (
            int(now.timestamp())
        ),
        "expires_at_epoch": (
            int(now.timestamp())
            + RETENTION_SECONDS
        ),
    }

    weather_ready = bool(
        airport.get(
            "is_diversion_weather_ready"
        )
    )

    taf_ready = (
        airport.get(
            "period_materialization_status"
        )
        == "READY"
    )

    taf_version_key = airport.get(
        "taf_version_key"
    )

    if (
        not weather_ready
        or not taf_ready
        or not taf_version_key
    ):
        item[
            "assessment_status"
        ] = "WAITING_FOR_WEATHER"

        item[
            "taf_period_ids"
        ] = []

        item[
            "known_limitations"
        ].append(
            "Airport does not currently have READY METAR/TAF weather evidence."
        )

        return item

    periods = query_taf_periods(
        station_id=station_id,
        taf_version_key=(
            taf_version_key
        ),
        window_start_epoch=(
            arrival_epoch
            - window_seconds
        ),
        window_end_epoch=(
            arrival_epoch
            + window_seconds
        ),
    )

    if not periods:
        item[
            "assessment_status"
        ] = "WAITING_FOR_WEATHER"

        item[
            "taf_period_ids"
        ] = []

        item[
            "known_limitations"
        ].append(
            "No READY TAF forecast period overlaps the ETA uncertainty window."
        )

        return item

    item["taf_period_ids"] = [
        period["period_id"]
        for period in periods
    ]

    weather_score = (
        current_weather_score(
            airport.get(
                "weather_risk_level"
            )
        )
    )

    if weather_score is None:
        item[
            "assessment_status"
        ] = "WAITING_FOR_WEATHER"

        item[
            "known_limitations"
        ].append(
            "Current airport weather risk is UNKNOWN."
        )

        return item

    # Conservative rule:
    # use the worst TAF period that overlaps
    # ETA +/- uncertainty.
    forecast_score = min(
        taf_period_score(period)
        for period in periods
    )

    d_score = distance_score(
        dist
    )

    total_score = (
        0.30 * d_score
        + 0.30 * weather_score
        + 0.40 * forecast_score
    )

    item.update(
        {
            "assessment_status": (
                "COMPLETE"
            ),
            "distance_score": (
                d_score
            ),
            "weather_score": (
                weather_score
            ),
            "taf_score": (
                forecast_score
            ),
            "total_airport_score": (
                total_score
            ),
        }
    )

    return item


def publish_completed(
    *,
    evaluation_id,
    risk,
    items,
):
    complete = [
        item
        for item in items
        if item.get(
            "assessment_status"
        ) == "COMPLETE"
    ]

    waiting = [
        item
        for item in items
        if item.get(
            "assessment_status"
        ) == "WAITING_FOR_WEATHER"
    ]

    status = (
        "COMPLETE"
        if not waiting
        else "PARTIAL"
    )

    detail = {
        "evaluation_id": (
            evaluation_id
        ),
        "risk_id": (
            risk["risk_id"]
        ),
        "aircraft_id": (
            risk["aircraft_id"]
        ),
        "candidate_count": (
            len(items)
        ),
        "complete_count": (
            len(complete)
        ),
        "waiting_for_weather_count": (
            len(waiting)
        ),
        "status": status,
        "schema_version": (
            SCHEMA_VERSION
        ),
    }

    response = events.put_events(
        Entries=[
            {
                "EventBusName": (
                    EVENT_BUS_NAME
                ),
                "Source": (
                    "wilvor.assessment"
                ),
                "DetailType": (
                    "airport.assessment.completed"
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
    ):
        raise RuntimeError(
            "Failed to publish airport assessment event"
        )


def lambda_handler(
    event,
    context,
):
    detail = event.get(
        "detail",
        {},
    )

    risk_id = detail.get(
        "risk_id"
    )

    if not risk_id:
        raise ValueError(
            "risk.updated event missing risk_id"
        )

    risk = get_risk(
        risk_id
    )

    if not risk:
        return {
            "processed": False,
            "reason": "RISK_NOT_FOUND",
        }

    if str(
        risk.get(
            "risk_level",
            ""
        )
    ).upper() not in {
        "MEDIUM",
        "HIGH",
    }:
        return {
            "processed": False,
            "reason": (
                "RISK_LEVEL_DOES_NOT_REQUIRE_AIRPORT_ASSESSMENT"
            ),
            "risk_id": risk_id,
        }

    aircraft = get_aircraft(
        risk["aircraft_id"]
    )

    if not aircraft:
        return {
            "processed": False,
            "reason": (
                "AIRCRAFT_STATE_NOT_FOUND"
            ),
        }

    if not aircraft.get(
        "has_position"
    ):
        return {
            "processed": False,
            "reason": (
                "AIRCRAFT_POSITION_UNAVAILABLE"
            ),
        }

    now = utc_now()

    evaluation_id = (
        "eval#"
        + stable_id(
            risk_id,
            aircraft[
                "state_version"
            ],
            RULESET_VERSION,
        )[:40]
    )

    airport_rows = scan_airports()

    candidates = []

    for airport in airport_rows:
        if (
            airport.get(
                "is_airport"
            )
            is False
        ):
            continue

        candidate = build_candidate(
            risk=risk,
            aircraft=aircraft,
            airport=airport,
            evaluation_id=(
                evaluation_id
            ),
            now=now,
        )

        if candidate:
            candidates.append(
                candidate
            )

    candidates.sort(
        key=lambda item: float(
            item.get(
                "distance_nm",
                999999,
            )
        )
    )

    candidates = candidates[
        :MAX_CANDIDATES
    ]

    complete = [
        item
        for item in candidates
        if item.get(
            "assessment_status"
        ) == "COMPLETE"
    ]

    complete.sort(
        key=lambda item: float(
            item.get(
                "total_airport_score",
                0,
            )
        ),
        reverse=True,
    )

    for rank, item in enumerate(
        complete,
        start=1,
    ):
        item["rank"] = rank

    for item in candidates:
        assessment_table.put_item(
            Item=to_decimal(
                item
            )
        )

    publish_completed(
        evaluation_id=evaluation_id,
        risk=risk,
        items=candidates,
    )

    print(
        json.dumps(
            {
                "event": (
                    "airport_assessment_completed"
                ),
                "evaluation_id": (
                    evaluation_id
                ),
                "risk_id": (
                    risk_id
                ),
                "candidate_count": (
                    len(candidates)
                ),
                "complete_count": (
                    len(complete)
                ),
            }
        )
    )

    return {
        "processed": True,
        "evaluation_id": (
            evaluation_id
        ),
        "risk_id": risk_id,
        "candidate_count": (
            len(candidates)
        ),
        "complete_count": (
            len(complete)
        ),
    }