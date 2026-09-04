import json
import logging
from decimal import Decimal

from botocore.exceptions import ClientError

import repository


LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)


class BadRequest(Exception):
    pass


def _json_default(value):
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)

        return float(value)

    if isinstance(value, set):
        return sorted(value)

    raise TypeError(
        f"Object of type {type(value).__name__} "
        "is not JSON serializable"
    )


def _response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",
        },
        "body": json.dumps(
            body,
            default=_json_default,
            separators=(",", ":"),
        ),
    }


def _query_params(event):
    return (
        event.get("queryStringParameters")
        or {}
    )


def _parse_limit(
    params,
    default=50,
    maximum=100,
):
    raw = params.get("limit")

    if raw is None:
        return default

    try:
        value = int(raw)

    except (
        TypeError,
        ValueError,
    ) as exc:
        raise BadRequest(
            "limit must be an integer"
        ) from exc

    if value < 1 or value > maximum:
        raise BadRequest(
            f"limit must be between 1 and {maximum}"
        )

    return value


def _request_meta(event):
    request_context = (
        event.get("requestContext")
        or {}
    )

    http_context = (
        request_context.get("http")
        or {}
    )

    return {
        "request_id": (
            request_context.get("requestId")
        ),
        "method": (
            http_context.get("method")
        ),
        "path": (
            event.get("rawPath")
        ),
    }


def lambda_handler(
    event,
    context,
):
    meta = _request_meta(event)

    method = (
        meta["method"]
        or ""
    )

    path = (
        meta["path"]
        or "/"
    )

    params = _query_params(event)

    path_params = (
        event.get("pathParameters")
        or {}
    )

    LOGGER.info(
        json.dumps(
            {
                "event": (
                    "operational_api.request"
                ),
                "request_id": (
                    meta["request_id"]
                ),
                "method": method,
                "path": path,
            }
        )
    )

    try:
        if method != "GET":
            return _response(
                405,
                {
                    "message": (
                        "Method not allowed"
                    )
                },
            )

        # =============================================================
        # API LIVENESS
        #
        # This only proves that:
        #
        # API Gateway -> Lambda
        #
        # is functioning.
        #
        # It is intentionally different from:
        #
        # GET /system-health
        # =============================================================

        if path == "/health":
            return _response(
                200,
                {
                    "service": (
                        "wilvor-operational-api"
                    ),
                    "status": "ok",
                    "requestId": (
                        meta["request_id"]
                    ),
                },
            )

        # =============================================================
        # OPERATIONS OVERVIEW
        # =============================================================

        if path == "/overview":
            return _response(
                200,
                repository.get_overview(),
            )

        # =============================================================
        # DATA FRESHNESS
        # =============================================================

        if path == "/freshness":
            return _response(
                200,
                repository.get_freshness(),
            )

        # =============================================================
        # PLATFORM / PIPELINE HEALTH
        # =============================================================

        if path == "/system-health":
            return _response(
                200,
                repository.get_system_health(),
            )

        # =============================================================
        # AIRCRAFT LIST
        #
        # Examples:
        #
        # /aircraft
        # /aircraft?limit=20
        # /aircraft?callsign=UAL123
        # /aircraft?h3Cell=8428347ffffffff
        # =============================================================

        if path == "/aircraft":
            limit = _parse_limit(
                params
            )

            result = (
                repository.list_aircraft(
                    limit=limit,
                    next_token=(
                        params.get(
                            "nextToken"
                        )
                    ),
                    callsign=(
                        params.get(
                            "callsign"
                        )
                    ),
                    h3_cell=(
                        params.get(
                            "h3Cell"
                        )
                    ),
                )
            )

            return _response(
                200,
                result,
            )

        # =============================================================
        # AIRCRAFT DETAIL
        # =============================================================

        if path.startswith(
            "/aircraft/"
        ):
            aircraft_id = (
                path_params.get(
                    "aircraftId"
                )
                or path.rsplit(
                    "/",
                    1,
                )[-1]
            )

            result = (
                repository.get_aircraft_detail(
                    aircraft_id
                )
            )

            if result is None:
                return _response(
                    404,
                    {
                        "message": (
                            "Aircraft not found"
                        ),
                        "aircraftId": (
                            aircraft_id
                        ),
                    },
                )

            return _response(
                200,
                result,
            )

        # =============================================================
        # ACTIVE HAZARDS
        # =============================================================

        if path == "/hazards/active":
            limit = _parse_limit(
                params
            )

            result = (
                repository.list_active_hazards(
                    limit=limit,
                    next_token=(
                        params.get(
                            "nextToken"
                        )
                    ),
                )
            )

            return _response(
                200,
                result,
            )

        # =============================================================
        # ACTIVE AIRCRAFT / HAZARD ENCOUNTERS
        #
        # Each item now contains:
        #
        # {
        #   "encounter": {...},
        #   "risk": {...}
        # }
        # =============================================================

        if path == "/encounters/active":
            limit = _parse_limit(
                params,
                default=25,
                maximum=50,
            )

            result = (
                repository.list_active_encounters(
                    limit=limit,
                    next_token=(
                        params.get(
                            "nextToken"
                        )
                    ),
                )
            )

            return _response(
                200,
                result,
            )

        # =============================================================
        # AIRPORT LIST
        #
        # Both routes expose the same current-state list:
        #
        # /airports
        # /airports/status
        #
        # Examples:
        #
        # /airports?weatherRisk=HIGH
        # /airports/status?weatherImpact=WEATHER_IMPACTED
        # =============================================================

        if path in {
            "/airports",
            "/airports/status",
        }:
            limit = _parse_limit(
                params,
                default=50,
                maximum=100,
            )

            result = (
                repository.list_airports(
                    limit=limit,
                    next_token=(
                        params.get(
                            "nextToken"
                        )
                    ),
                    weather_risk=(
                        params.get(
                            "weatherRisk"
                        )
                    ),
                    weather_impact=(
                        params.get(
                            "weatherImpact"
                        )
                    ),
                )
            )

            return _response(
                200,
                result,
            )

        # =============================================================
        # AIRPORT DETAIL
        # =============================================================

        if path.startswith(
            "/airports/"
        ):
            airport_id = (
                path_params.get(
                    "airportId"
                )
                or path.rsplit(
                    "/",
                    1,
                )[-1]
            )

            airport_id = (
                airport_id.upper()
            )

            result = (
                repository.get_airport_detail(
                    airport_id
                )
            )

            if result is None:
                return _response(
                    404,
                    {
                        "message": (
                            "Airport not found"
                        ),
                        "airportId": (
                            airport_id
                        ),
                    },
                )

            return _response(
                200,
                result,
            )

        # =============================================================
        # ACTIVE RECOMMENDATIONS
        # =============================================================

        if (
            path
            == "/recommendations/active"
        ):
            limit = _parse_limit(
                params,
                default=50,
                maximum=100,
            )

            result = (
                repository
                .list_active_recommendations(
                    limit=limit,
                    next_token=(
                        params.get(
                            "nextToken"
                        )
                    ),
                )
            )

            return _response(
                200,
                result,
            )

        # =============================================================
        # ACTIVE ALERTS
        # =============================================================

        if path == "/alerts/active":
            limit = _parse_limit(
                params,
                default=50,
                maximum=100,
            )

            result = (
                repository.list_active_alerts(
                    limit=limit,
                    next_token=(
                        params.get(
                            "nextToken"
                        )
                    ),
                )
            )

            return _response(
                200,
                result,
            )

        return _response(
            404,
            {
                "message": (
                    "Route not found"
                ),
                "path": path,
            },
        )

    except (
        BadRequest,
        ValueError,
    ) as exc:
        return _response(
            400,
            {
                "message": str(exc)
            },
        )

    except ClientError:
        LOGGER.exception(
            json.dumps(
                {
                    "event": (
                        "operational_api.aws_error"
                    ),
                    "request_id": (
                        meta["request_id"]
                    ),
                    "method": method,
                    "path": path,
                }
            )
        )

        return _response(
            500,
            {
                "message": (
                    "AWS data access failed"
                ),
                "requestId": (
                    meta["request_id"]
                ),
            },
        )

    except Exception:
        LOGGER.exception(
            json.dumps(
                {
                    "event": (
                        "operational_api.unhandled_error"
                    ),
                    "request_id": (
                        meta["request_id"]
                    ),
                    "method": method,
                    "path": path,
                }
            )
        )

        return _response(
            500,
            {
                "message": (
                    "Internal server error"
                ),
                "requestId": (
                    meta["request_id"]
                ),
            },
        )