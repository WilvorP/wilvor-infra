import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError


dynamodb = boto3.resource("dynamodb")
events_client = boto3.client("events")

AIRPORT_STATUS_TABLE_NAME = os.environ["AIRPORT_STATUS_TABLE_NAME"]
STATION_REFERENCE_TABLE_NAME = os.environ["STATION_REFERENCE_TABLE_NAME"]
METAR_LATEST_TABLE_NAME = os.environ["METAR_LATEST_TABLE_NAME"]
TAF_LATEST_TABLE_NAME = os.environ["TAF_LATEST_TABLE_NAME"]

SCHEMA_VERSION = os.environ.get("SCHEMA_VERSION", "airport_status.v1")
AIRPORT_STATUS_TTL_SECONDS = int(os.environ.get("AIRPORT_STATUS_TTL_SECONDS", "86400"))
METAR_FRESH_SECONDS = int(os.environ.get("METAR_FRESH_SECONDS", "1800"))
TAF_FRESH_SECONDS = int(os.environ.get("TAF_FRESH_SECONDS", "21600"))
BOOTSTRAP_SCAN_LIMIT = int(os.environ.get("BOOTSTRAP_SCAN_LIMIT", "1000"))

EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "default")
AIRPORT_STATUS_EVENT_SOURCE = os.environ.get(
    "AIRPORT_STATUS_EVENT_SOURCE",
    "wilvor.airport",
)
PUBLISH_EVENTS = os.environ.get("PUBLISH_EVENTS", "true").lower() == "true"

airport_status_table = dynamodb.Table(AIRPORT_STATUS_TABLE_NAME)
station_reference_table = dynamodb.Table(STATION_REFERENCE_TABLE_NAME)
metar_latest_table = dynamodb.Table(METAR_LATEST_TABLE_NAME)
taf_latest_table = dynamodb.Table(TAF_LATEST_TABLE_NAME)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def to_decimal(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))

    if isinstance(value, dict):
        return {
            key: to_decimal(item)
            for key, item in value.items()
            if item is not None
        }

    if isinstance(value, list):
        return [
            to_decimal(item)
            for item in value
            if item is not None
        ]

    return value


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)

    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}

    if isinstance(value, list):
        return [json_safe(item) for item in value]

    return value


def as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None

    if isinstance(value, str):
        cleaned = value.strip().upper()

        if cleaned.endswith("+"):
            cleaned = cleaned[:-1]

        if cleaned in {"M", "P", "SM"}:
            return None

        value = cleaned

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_station_id(value: Any) -> str | None:
    if value in (None, ""):
        return None

    return str(value).strip().upper() or None


def get_item(table: Any, key_name: str, key_value: str) -> dict[str, Any] | None:
    response = table.get_item(Key={key_name: key_value})
    item = response.get("Item")
    return item if isinstance(item, dict) else None


def freshness_from_epoch(
    *,
    epoch_value: Any,
    fresh_seconds: int,
    now_epoch: int,
) -> tuple[str, int | None]:
    source_epoch = as_int(epoch_value)

    if source_epoch is None:
        return "UNAVAILABLE", None

    age_seconds = max(0, now_epoch - source_epoch)

    if age_seconds <= fresh_seconds:
        return "FRESH", age_seconds

    return "STALE", age_seconds


def weather_codes_from_metar(metar: dict[str, Any] | None) -> list[str]:
    if not metar:
        return []

    codes = metar.get("weather_codes")
    if isinstance(codes, list):
        return [str(code).upper() for code in codes]

    weather_string = metar.get("weather_string")
    if not weather_string:
        return []

    return [
        token.strip().upper()
        for token in str(weather_string).split()
        if token.strip()
    ]


def has_thunderstorm(weather_codes: list[str]) -> bool:
    return any("TS" in code for code in weather_codes)


def has_reduced_visibility_weather(weather_codes: list[str]) -> bool:
    reduced_visibility_tokens = ("FG", "BR", "HZ", "FU", "DU", "SA", "SN", "RA")
    return any(
        any(token in code for token in reduced_visibility_tokens)
        for code in weather_codes
    )


def derive_weather_risk_level(
    *,
    metar: dict[str, Any] | None,
    metar_freshness_status: str,
) -> tuple[str, str, list[str]]:
    reasons: list[str] = []

    if not metar:
        return (
            "UNKNOWN",
            "UNKNOWN",
            ["No METAR record is available for current airport weather."],
        )

    if metar_freshness_status != "FRESH":
        return (
            "UNKNOWN",
            "UNKNOWN",
            ["METAR record exists but is stale or unavailable."],
        )

    flight_category = str(metar.get("flight_category") or "").upper()
    visibility_sm = as_float(metar.get("visibility_sm"))
    ceiling_ft = as_float(metar.get("ceiling_ft"))
    wind_speed_kt = as_float(metar.get("wind_speed_kt"))
    wind_gust_kt = as_float(metar.get("wind_gust_kt"))
    weather_codes = weather_codes_from_metar(metar)

    if flight_category in {"LIFR", "IFR"}:
        reasons.append(f"Flight category is {flight_category}.")

    if visibility_sm is not None and visibility_sm < 3:
        reasons.append(f"Visibility is below 3 SM: {visibility_sm}.")

    if ceiling_ft is not None and ceiling_ft < 1000:
        reasons.append(f"Ceiling is below 1000 ft: {ceiling_ft}.")

    if has_thunderstorm(weather_codes):
        reasons.append("Thunderstorm weather code present.")

    if wind_gust_kt is not None and wind_gust_kt >= 35:
        reasons.append(f"Wind gust is at or above 35 kt: {wind_gust_kt}.")

    if wind_speed_kt is not None and wind_speed_kt >= 30:
        reasons.append(f"Sustained wind is at or above 30 kt: {wind_speed_kt}.")

    if reasons:
        return "HIGH", "WEATHER_IMPACTED", reasons

    if flight_category == "MVFR":
        reasons.append("Flight category is MVFR.")

    if visibility_sm is not None and 3 <= visibility_sm <= 5:
        reasons.append(f"Visibility is marginal: {visibility_sm} SM.")

    if ceiling_ft is not None and 1000 <= ceiling_ft <= 3000:
        reasons.append(f"Ceiling is marginal: {ceiling_ft} ft.")

    if wind_gust_kt is not None and 25 <= wind_gust_kt < 35:
        reasons.append(f"Wind gust is elevated: {wind_gust_kt} kt.")

    if has_reduced_visibility_weather(weather_codes):
        reasons.append("Precipitation or reduced-visibility weather code present.")

    if reasons:
        return "MEDIUM", "WEATHER_IMPACTED", reasons

    return "LOW", "NORMAL", ["Fresh METAR does not exceed MVP weather-impact thresholds."]


def derive_assessment_status(
    *,
    has_metar: bool,
    has_taf: bool,
    metar_freshness_status: str,
    taf_freshness_status: str,
) -> tuple[str, bool, list[str]]:
    limitations: list[str] = []

    metar_ready = has_metar and metar_freshness_status == "FRESH"
    taf_ready = has_taf and taf_freshness_status == "FRESH"

    if not has_metar:
        limitations.append("Current METAR is missing.")

    if not has_taf:
        limitations.append("Current TAF is missing.")

    if has_metar and metar_freshness_status != "FRESH":
        limitations.append(f"METAR freshness is {metar_freshness_status}.")

    if has_taf and taf_freshness_status != "FRESH":
        limitations.append(f"TAF freshness is {taf_freshness_status}.")

    if metar_ready and taf_ready:
        return "EVALUATED", True, limitations

    if has_metar or has_taf:
        return "PARTIALLY_EVALUATED", False, limitations

    return "WEATHER_PENDING", False, limitations


def build_airport_status_item(station_id: str) -> dict[str, Any]:
    now = utc_now()
    now_epoch = int(now.timestamp())

    station = get_item(station_reference_table, "station_id", station_id)
    metar = get_item(metar_latest_table, "station_id", station_id)
    taf = get_item(taf_latest_table, "station_id", station_id)

    has_metar = metar is not None
    has_taf = taf is not None

    airport_id = (
        normalize_station_id((station or {}).get("airport_id"))
        or normalize_station_id((metar or {}).get("airport_id"))
        or normalize_station_id((taf or {}).get("airport_id"))
        or station_id
    )

    metar_freshness_status, metar_age_seconds = freshness_from_epoch(
        epoch_value=(metar or {}).get("observed_time_epoch"),
        fresh_seconds=METAR_FRESH_SECONDS,
        now_epoch=now_epoch,
    )

    taf_freshness_status, taf_age_seconds = freshness_from_epoch(
        epoch_value=(taf or {}).get("issued_at_epoch"),
        fresh_seconds=TAF_FRESH_SECONDS,
        now_epoch=now_epoch,
    )

    weather_risk_level, weather_impact_status, status_reasons = (
        derive_weather_risk_level(
            metar=metar,
            metar_freshness_status=metar_freshness_status,
        )
    )

    assessment_status, is_diversion_weather_ready, known_limitations = (
        derive_assessment_status(
            has_metar=has_metar,
            has_taf=has_taf,
            metar_freshness_status=metar_freshness_status,
            taf_freshness_status=taf_freshness_status,
        )
    )

    identity_source = station or metar or taf or {}

    normalized_visibility_sm = as_float((metar or {}).get("visibility_sm"))
    normalized_ceiling_ft = as_float((metar or {}).get("ceiling_ft"))
    normalized_wind_speed_kt = as_float((metar or {}).get("wind_speed_kt"))
    normalized_wind_gust_kt = as_float((metar or {}).get("wind_gust_kt"))

    item = {
        "airport_id": airport_id,
        "station_id": station_id,
        "station_name": identity_source.get("station_name"),
        "station_type": identity_source.get("station_type"),
        "is_airport": identity_source.get("is_airport"),
        "iata_code": identity_source.get("iata_code"),
        "faa_lid": identity_source.get("faa_lid"),
        "country_code": identity_source.get("country_code"),
        "latitude": identity_source.get("latitude"),
        "longitude": identity_source.get("longitude"),
        "elevation_m": identity_source.get("elevation_m"),
        "h3_cell": identity_source.get("h3_cell"),
        "h3_resolution": identity_source.get("h3_resolution"),

        "has_metar": has_metar,
        "has_taf": has_taf,
        "metar_fetch_status": "AVAILABLE" if has_metar else "MISSING",
        "taf_fetch_status": "AVAILABLE" if has_taf else "MISSING",
        "metar_freshness_status": metar_freshness_status,
        "taf_freshness_status": taf_freshness_status,
        "metar_age_seconds": metar_age_seconds,
        "taf_age_seconds": taf_age_seconds,

        "metar_version": (metar or {}).get("metar_version"),
        "observed_time_utc": (metar or {}).get("observed_time_utc"),
        "observed_time_epoch": (metar or {}).get("observed_time_epoch"),
        "temperature_c": (metar or {}).get("temperature_c"),
        "dewpoint_c": (metar or {}).get("dewpoint_c"),
        "wind_direction_deg": (metar or {}).get("wind_direction_deg"),
        "wind_speed_kt": normalized_wind_speed_kt,
        "wind_gust_kt": normalized_wind_gust_kt,
        "visibility_sm": normalized_visibility_sm,
        "ceiling_ft": normalized_ceiling_ft,
        "flight_category": (metar or {}).get("flight_category"),
        "weather_string": (metar or {}).get("weather_string"),
        "weather_codes": (metar or {}).get("weather_codes"),

        "taf_version": (taf or {}).get("taf_version"),
        "taf_version_key": (taf or {}).get("taf_version_key"),
        "taf_source_version": (taf or {}).get("source_version"),
        "issued_at_utc": (taf or {}).get("issued_at_utc"),
        "issued_at_epoch": (taf or {}).get("issued_at_epoch"),
        "valid_from_utc": (taf or {}).get("valid_from_utc"),
        "valid_to_utc": (taf or {}).get("valid_to_utc"),
        "period_materialization_status": (taf or {}).get("period_materialization_status"),
        "forecast_period_count": (taf or {}).get("forecast_period_count"),

        "weather_risk_level": weather_risk_level,
        "weather_impact_status": weather_impact_status,
        "assessment_status": assessment_status,
        "is_diversion_weather_ready": is_diversion_weather_ready,
        "status_reasons": status_reasons[:20],
        "known_limitations": known_limitations[:20],

        "source_station_version": (station or {}).get("source_version"),
        "source_metar_version": (metar or {}).get("metar_version"),
        "source_taf_version": (taf or {}).get("source_version") or (taf or {}).get("taf_version"),
        "correlation_id": (
            (metar or {}).get("correlation_id")
            or (taf or {}).get("correlation_id")
            or (station or {}).get("correlation_id")
        ),
        "schema_version": SCHEMA_VERSION,
        "updated_at_utc": iso_utc(now),
        "updated_at_epoch": now_epoch,
    }

    if AIRPORT_STATUS_TTL_SECONDS > 0:
        item["expires_at_epoch"] = now_epoch + AIRPORT_STATUS_TTL_SECONDS

    return {
        key: value
        for key, value in item.items()
        if value is not None
    }


def write_airport_status(item: dict[str, Any]) -> None:
    airport_status_table.put_item(Item=to_decimal(item))


def extract_station_ids_from_event(event: Any) -> list[str]:
    if not isinstance(event, dict):
        return []

    station_ids: set[str] = set()

    direct_station_id = normalize_station_id(event.get("station_id"))
    if direct_station_id:
        station_ids.add(direct_station_id)

    for raw_id in event.get("station_ids") or []:
        station_id = normalize_station_id(raw_id)
        if station_id:
            station_ids.add(station_id)

    detail = event.get("detail")
    if isinstance(detail, dict):
        detail_station_id = normalize_station_id(detail.get("station_id"))
        if detail_station_id:
            station_ids.add(detail_station_id)

    return sorted(station_ids)


def scan_station_ids_from_weather_tables() -> list[str]:
    station_ids: set[str] = set()

    for table in (metar_latest_table, taf_latest_table):
        response = table.scan(
            ProjectionExpression="station_id",
            Limit=BOOTSTRAP_SCAN_LIMIT,
        )

        for item in response.get("Items", []):
            station_id = normalize_station_id(item.get("station_id"))
            if station_id:
                station_ids.add(station_id)

    return sorted(station_ids)

def publish_airport_status_updated(item: dict[str, Any]) -> None:
    if not PUBLISH_EVENTS:
        return

    detail = {
        "airport_id": item.get("airport_id"),
        "station_id": item.get("station_id"),
        "assessment_status": item.get("assessment_status"),
        "is_diversion_weather_ready": item.get("is_diversion_weather_ready"),
        "weather_risk_level": item.get("weather_risk_level"),
        "weather_impact_status": item.get("weather_impact_status"),
        "has_metar": item.get("has_metar"),
        "has_taf": item.get("has_taf"),
        "metar_freshness_status": item.get("metar_freshness_status"),
        "taf_freshness_status": item.get("taf_freshness_status"),
        "source_station_version": item.get("source_station_version"),
        "source_metar_version": item.get("source_metar_version"),
        "source_taf_version": item.get("source_taf_version"),
        "correlation_id": item.get("correlation_id"),
        "schema_version": item.get("schema_version"),
        "updated_at_utc": item.get("updated_at_utc"),
        "updated_at_epoch": item.get("updated_at_epoch"),
    }

    detail = {
        key: json_safe(value)
        for key, value in detail.items()
        if value is not None
    }

    response = events_client.put_events(
        Entries=[
            {
                "Source": AIRPORT_STATUS_EVENT_SOURCE,
                "DetailType": "airport.status.updated",
                "EventBusName": EVENT_BUS_NAME,
                "Detail": json.dumps(detail, separators=(",", ":")),
            }
        ]
    )

    failed_count = response.get("FailedEntryCount", 0)
    if failed_count:
        print(
            json.dumps(
                {
                    "message": "Failed to publish airport.status.updated",
                    "airport_id": item.get("airport_id"),
                    "station_id": item.get("station_id"),
                    "failed_count": failed_count,
                    "entries": response.get("Entries"),
                },
                default=str,
            )
        )
        raise RuntimeError("EventBridge put_events failed")

    print(
        json.dumps(
            {
                "message": "Published airport.status.updated",
                "airport_id": item.get("airport_id"),
                "station_id": item.get("station_id"),
                "assessment_status": item.get("assessment_status"),
                "is_diversion_weather_ready": item.get("is_diversion_weather_ready"),
            },
            default=str,
        )
    )

def lambda_handler(event, context):
    station_ids = extract_station_ids_from_event(event)

    if isinstance(event, dict) and event.get("mode") == "bootstrap":
        station_ids = scan_station_ids_from_weather_tables()

    if not station_ids:
        print(json.dumps({"message": "No station_id found", "event": json_safe(event)}))
        return {
            "ok": True,
            "processed": 0,
            "reason": "NO_STATION_ID",
        }

    written: list[str] = []
    failures: list[dict[str, str]] = []

    for station_id in station_ids:
        try:
            item = build_airport_status_item(station_id)
            write_airport_status(item)
            publish_airport_status_updated(item)
            written.append(item["airport_id"])
            print(
                json.dumps(
                    {
                        "message": "AirportStatus written",
                        "station_id": station_id,
                        "airport_id": item["airport_id"],
                        "weather_risk_level": item["weather_risk_level"],
                        "assessment_status": item["assessment_status"],
                    },
                    default=str,
                )
            )
        except ClientError as exc:
            failures.append(
                {
                    "station_id": station_id,
                    "error": exc.response.get("Error", {}).get("Code", "ClientError"),
                }
            )
        except Exception as exc:
            failures.append({"station_id": station_id, "error": str(exc)})

    return {
        "ok": len(failures) == 0,
        "processed": len(station_ids),
        "written": written,
        "failures": failures,
    }