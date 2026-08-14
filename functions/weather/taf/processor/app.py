import base64
import binascii
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError

from wilvor_weather.monitoring import emit_metric


logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
events = boto3.client("events")
s3 = boto3.client("s3")

TAF_LATEST_TABLE_NAME = os.environ["TAF_LATEST_TABLE_NAME"]
TAF_FORECAST_PERIODS_TABLE_NAME = os.environ["TAF_FORECAST_PERIODS_TABLE_NAME"]
EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "default")
BAD_RECORDS_BUCKET_NAME = os.environ["BAD_RECORDS_BUCKET_NAME"]
BAD_RECORDS_PREFIX = os.environ.get(
    "BAD_RECORDS_PREFIX",
    "bad-records/source=taf_processor",
)
SCHEMA_VERSION = os.environ.get("SCHEMA_VERSION", "internal.taf.v1")

taf_latest_table = dynamodb.Table(TAF_LATEST_TABLE_NAME)
taf_forecast_periods_table = dynamodb.Table(TAF_FORECAST_PERIODS_TABLE_NAME)


class PermanentRecordError(Exception):
    """A bad source record that will not become valid after a retry."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return str(value)


def log_event(message: str, **kwargs: Any) -> None:
    logger.info(json.dumps({"message": message, **kwargs}, default=json_default))


def clean_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        text = json.dumps(value, sort_keys=True, default=json_default)
    else:
        text = str(value)
    text = text.strip()
    return text or None


def parse_time(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, (int, float, Decimal)):
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        return datetime.fromtimestamp(numeric, tz=timezone.utc)

    if not isinstance(value, str):
        return None

    cleaned = value.strip()
    if not cleaned:
        return None

    try:
        return parse_time(float(cleaned))
    except ValueError:
        pass

    try:
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def require_time(value: Any, field_name: str) -> datetime:
    parsed = parse_time(value)
    if parsed is None:
        raise PermanentRecordError(f"TAF field {field_name} is missing or invalid")
    return parsed


def normalize_number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        numeric = float(value)
    elif isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            numeric = float(cleaned)
        except ValueError:
            return None
    else:
        return None

    return int(numeric) if numeric.is_integer() else numeric


def normalize_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"true", "1", "yes"}:
            return True
        if cleaned in {"false", "0", "no"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return None


def normalize_visibility(value: Any) -> dict[str, Any]:
    if isinstance(value, (int, float, Decimal)):
        return {"visibility_sm": normalize_number(value), "visibility_qualifier": None}

    text = clean_string(value)
    if text is None:
        return {"visibility_sm": None, "visibility_qualifier": None}

    qualifier = None
    numeric_text = text

    if text.endswith("+"):
        qualifier = "GREATER_THAN_OR_EQUAL"
        numeric_text = text[:-1]

    try:
        visibility = float(numeric_text)
    except ValueError:
        visibility = None
        qualifier = qualifier or text.upper()

    return {
        "visibility_sm": int(visibility) if visibility is not None and visibility.is_integer() else visibility,
        "visibility_qualifier": qualifier,
    }


def normalize_wind_direction(value: Any) -> tuple[int | float | None, bool]:
    text = clean_string(value)
    if text and text.upper() == "VRB":
        return None, True
    return normalize_number(value), False


def normalize_clouds(value: Any) -> tuple[list[dict[str, Any]], int | float | None]:
    if not isinstance(value, list):
        return [], None

    clouds: list[dict[str, Any]] = []
    ceiling_candidates: list[float] = []

    for layer in value:
        if not isinstance(layer, dict):
            continue

        cover = clean_string(layer.get("cover"))
        cover = cover.upper() if cover else None
        base_ft = normalize_number(layer.get("base"))
        cloud_type = clean_string(layer.get("type"))

        normalized_layer = {
            "cover": cover,
            "base_ft": base_ft,
            "cloud_type": cloud_type.upper() if cloud_type else None,
        }
        clouds.append(normalized_layer)

        if cover in {"BKN", "OVC", "VV"} and isinstance(base_ft, (int, float)):
            ceiling_candidates.append(float(base_ft))

    ceiling_ft: int | float | None = None
    if ceiling_candidates:
        minimum = min(ceiling_candidates)
        ceiling_ft = int(minimum) if minimum.is_integer() else minimum

    return clouds, ceiling_ft

def classify_forecast_flight_category(
    *,
    visibility_sm: int | float | None,
    ceiling_ft: int | float | None,
) -> str | None:
    if visibility_sm is None and ceiling_ft is None:
        return None

    if (
        visibility_sm is not None
        and visibility_sm < 1
    ) or (
        ceiling_ft is not None
        and ceiling_ft < 500
    ):
        return "LIFR"

    if (
        visibility_sm is not None
        and visibility_sm < 3
    ) or (
        ceiling_ft is not None
        and ceiling_ft < 1000
    ):
        return "IFR"

    if (
        visibility_sm is not None
        and visibility_sm <= 5
    ) or (
        ceiling_ft is not None
        and ceiling_ft <= 3000
    ):
        return "MVFR"

    return "VFR"


def classify_freshness(
    *,
    now: datetime,
    issued_at: datetime,
    valid_to: datetime,
) -> str:
    if now > valid_to:
        return "STALE"

    issue_age_seconds = (now - issued_at).total_seconds()
    if issue_age_seconds <= 6 * 3600:
        return "FRESH"

    if issue_age_seconds <= 24 * 3600:
        return "ACCEPTABLE"

    return "STALE"


def detect_taf_amendment(raw_text: str) -> bool:
    tokens = raw_text.upper().split()
    return "AMD" in tokens or "TAFAMD" in tokens


def detect_taf_correction(raw_text: str) -> bool:
    tokens = raw_text.upper().split()
    return "COR" in tokens or "CORRECTED" in tokens

def decode_kinesis_record(record: dict[str, Any]) -> dict[str, Any]:
    try:
        encoded = record["kinesis"]["data"]
    except KeyError as exc:
        raise PermanentRecordError("Kinesis record is missing kinesis.data") from exc

    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise PermanentRecordError("Kinesis data is not valid base64 UTF-8") from exc

    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise PermanentRecordError("Kinesis data is not valid JSON") from exc

    if not isinstance(payload, dict):
        raise PermanentRecordError("Decoded Kinesis payload is not a JSON object")

    return payload


def extract_taf(raw_event: dict[str, Any]) -> dict[str, Any]:
    taf = raw_event.get("taf")
    if not isinstance(taf, dict):
        raise PermanentRecordError("Kinesis payload does not contain a valid taf object")
    return taf


def normalize_period(forecast: dict[str, Any], sequence_number: int) -> dict[str, Any]:
    period_from = require_time(forecast.get("timeFrom"), "fcsts.timeFrom")
    period_to = require_time(forecast.get("timeTo"), "fcsts.timeTo")

    if period_from >= period_to:
        raise PermanentRecordError("TAF forecast period has timeFrom >= timeTo")

    transition_complete = parse_time(forecast.get("timeBec"))
    wind_direction_deg, wind_direction_variable = normalize_wind_direction(
        forecast.get("wdir")
    )
    visibility = normalize_visibility(forecast.get("visib"))
    clouds, ceiling_ft = normalize_clouds(forecast.get("clouds"))

    weather_string = clean_string(forecast.get("wxString"))
    weather_codes = weather_string.split() if weather_string else []

    change_type = clean_string(forecast.get("fcstChange"))
    change_type = change_type.upper() if change_type else "BASE"

    probability = normalize_number(forecast.get("probability"))

    return {
        "sequence_number": sequence_number,
        "period_from_utc": period_from.isoformat(),
        "period_from_epoch": int(period_from.timestamp()),
        "period_to_utc": period_to.isoformat(),
        "period_to_epoch": int(period_to.timestamp()),
        "transition_complete_utc": (
            transition_complete.isoformat() if transition_complete else None
        ),
        "change_type": change_type,
        "probability": probability,
        "probability_pct": probability,
        "wind_direction_deg": wind_direction_deg,
        "wind_direction_variable": wind_direction_variable,
        "wind_speed_kt": normalize_number(forecast.get("wspd")),
        "wind_gust_kt": normalize_number(forecast.get("wgst")),
        **visibility,
        "forecast_flight_category": classify_forecast_flight_category(
            visibility_sm=visibility["visibility_sm"],
            ceiling_ft=ceiling_ft,
        ),
        "weather_string": weather_string,
        "weather_codes": weather_codes,
        "clouds": clouds,
        "ceiling_ft": ceiling_ft,
        "vertical_visibility_ft": normalize_number(forecast.get("vertVis")),
        "altimeter_in_hg": normalize_number(forecast.get("altim")),
        "low_level_wind_shear": {
            "height_ft": normalize_number(forecast.get("wshearHgt")),
            "direction_deg": normalize_number(forecast.get("wshearDir")),
            "speed_kt": normalize_number(forecast.get("wshearSpd")),
        },
        "icing_turbulence_layers": forecast.get("icgTurb") or [],
        "temperature_forecasts": forecast.get("temp") or [],
        "not_decoded": clean_string(forecast.get("notDecoded")),
        "schema_version": "taf_forecast_period.v1",
    }


def stable_hash(value: Any) -> str:
    serialized = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=json_default,
        allow_nan=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def normalize_taf(raw_event: dict[str, Any], taf: dict[str, Any]) -> dict[str, Any]:
    station_id = clean_string(taf.get("icaoId"))
    if not station_id:
        raise PermanentRecordError("TAF is missing icaoId")
    station_id = station_id.upper()

    issued_at = require_time(taf.get("issueTime"), "issueTime")
    valid_from = require_time(taf.get("validTimeFrom"), "validTimeFrom")
    valid_to = require_time(taf.get("validTimeTo"), "validTimeTo")
    if valid_from >= valid_to:
        raise PermanentRecordError("TAF validTimeFrom must be before validTimeTo")

    raw_text = clean_string(taf.get("rawTAF"))
    if not raw_text:
        raise PermanentRecordError("TAF is missing rawTAF")

    forecasts = taf.get("fcsts")
    if not isinstance(forecasts, list) or not forecasts:
        raise PermanentRecordError("TAF fcsts must be a non-empty list")

    periods = []
    for sequence_number, forecast in enumerate(forecasts):
        if not isinstance(forecast, dict):
            raise PermanentRecordError("TAF fcsts contains a non-object period")
        periods.append(normalize_period(forecast, sequence_number))

    content_for_hash = {
        "station_id": station_id,
        "issued_at_utc": issued_at.isoformat(),
        "valid_from_utc": valid_from.isoformat(),
        "valid_to_utc": valid_to.isoformat(),
        "raw_text": raw_text,
        "periods": periods,
        "remarks": clean_string(taf.get("remarks")),
    }

    taf_version = stable_hash(content_for_hash)[:32]
    taf_version_key = f"{station_id}#{taf_version}"
    materialization_id = str(uuid.uuid4())

    raw_bucket = clean_string(raw_event.get("raw_s3_bucket"))
    raw_key = clean_string(raw_event.get("raw_s3_key"))
    raw_s3_uri = f"s3://{raw_bucket}/{raw_key}" if raw_bucket and raw_key else None

    bulletin_time = parse_time(taf.get("bulletinTime"))
    received_at = parse_time(raw_event.get("received_at")) or now_utc()
    processed_at = now_utc()

    trigger = raw_event.get("trigger")
    trigger = trigger if isinstance(trigger, dict) else {}

    airport_id = clean_string(taf.get("airport_id")) or clean_string(trigger.get("airport_id"))

    return {
        "station_id": station_id,
        "airport_id": airport_id,
        "station_name": clean_string(taf.get("name")),
        "taf_version": taf_version,
        "taf_version_key": taf_version_key,
        "source_version": taf_version,
        "issued_at_utc": issued_at.isoformat(),
        "issued_at_epoch": int(issued_at.timestamp()),
        "bulletin_time_utc": bulletin_time.isoformat() if bulletin_time else None,
        "valid_from_utc": valid_from.isoformat(),
        "valid_from_epoch": int(valid_from.timestamp()),
        "valid_to_utc": valid_to.isoformat(),
        "valid_to_epoch": int(valid_to.timestamp()),
        "most_recent": normalize_bool(taf.get("mostRecent")),
        "is_amendment": detect_taf_amendment(raw_text),
        "is_correction": detect_taf_correction(raw_text),
        "remarks": clean_string(taf.get("remarks")),
        "latitude": normalize_number(taf.get("lat")),
        "longitude": normalize_number(taf.get("lon")),
        "elevation_m": normalize_number(taf.get("elev")),
        "raw_text": raw_text,
        "forecast_periods": periods,
        "forecast_period_count": len(periods),
        "period_count": len(periods),
        "period_materialization_status": "BUILDING",
        "materialization_id": materialization_id,
        "has_undecoded_content": any(period.get("not_decoded") for period in periods),
        "freshness_status": classify_freshness(
            now=processed_at,
            issued_at=issued_at,
            valid_to=valid_to,
        ),
        "source": "NOAA_AVIATION_WEATHER",
        "source_system": "NOAA_AVIATIONWEATHER_TAF",
        "source_event_time_utc": issued_at.isoformat(),
        "schema_version": SCHEMA_VERSION,
        "raw_s3_uri": raw_s3_uri,
        "poll_id": clean_string(raw_event.get("poll_id")),
        "received_at_utc": received_at.isoformat(),
        "processed_at_utc": processed_at.isoformat(),
        "updated_at_utc": processed_at.isoformat(),
        "correlation_id": (
            clean_string(trigger.get("correlation_id"))
            or clean_string(raw_event.get("poll_id"))
            or str(uuid.uuid4())
        ),
        "trigger_hazard_version_key": clean_string(trigger.get("hazard_version_key")),
        "trigger_hazard_id": clean_string(trigger.get("hazard_id")),
        "trigger_hazard_source_version": clean_string(trigger.get("hazard_source_version")),
        "expires_at_epoch": int((valid_to + timedelta(hours=12)).timestamp()),
    }


def to_dynamodb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: to_dynamodb(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [to_dynamodb(item) for item in value]
    return value


def get_existing(station_id: str) -> dict[str, Any] | None:
    response = taf_latest_table.get_item(Key={"station_id": station_id})
    item = response.get("Item")
    return item if isinstance(item, dict) else None


def classify_change(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> str:
    if existing is None:
        return "NEW"

    if existing.get("source_version") == incoming["source_version"]:
        return "UNCHANGED"

    existing_issue = int(existing.get("issued_at_epoch", 0))
    incoming_issue = int(incoming["issued_at_epoch"])

    if incoming_issue > existing_issue:
        return "UPDATED"
    if incoming_issue == existing_issue:
        return "CORRECTED"
    return "STALE"

def put_latest(item: dict[str, Any]) -> bool:
    parent = latest_item_for_write(item)

    try:
        taf_latest_table.put_item(
            Item=to_dynamodb(parent),
            ConditionExpression=(
                "attribute_not_exists(station_id) OR "
                "attribute_not_exists(issued_at_epoch) OR "
                "issued_at_epoch < :issued OR "
                "(issued_at_epoch = :issued AND source_version <> :version)"
            ),
            ExpressionAttributeValues={
                ":issued": Decimal(str(item["issued_at_epoch"])),
                ":version": item["source_version"],
            },
        )
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise

def latest_item_for_write(item: dict[str, Any]) -> dict[str, Any]:
    parent = dict(item)
    parent.pop("forecast_periods", None)
    return parent


def build_period_key(period: dict[str, Any]) -> str:
    change_type = clean_string(period.get("change_type")) or "BASE"
    return (
        f"{int(period['period_from_epoch']):010d}#"
        f"{int(period['sequence_number']):04d}#"
        f"{change_type}"
    )


def build_period_item(
    *,
    parent: dict[str, Any],
    period: dict[str, Any],
) -> dict[str, Any]:
    period_key = build_period_key(period)
    period_id = stable_hash(
        {
            "taf_version_key": parent["taf_version_key"],
            "period_key": period_key,
        }
    )[:32]

    return {
        "taf_version_key": parent["taf_version_key"],
        "period_key": period_key,
        "period_id": period_id,
        "station_id": parent["station_id"],
        "airport_id": parent.get("airport_id"),
        "taf_version": parent["taf_version"],
        "issued_at_utc": parent["issued_at_utc"],
        "period_from_epoch": period["period_from_epoch"],
        "period_from_utc": period["period_from_utc"],
        "period_to_epoch": period["period_to_epoch"],
        "period_to_utc": period["period_to_utc"],
        "change_type": period["change_type"],
        "probability": period.get("probability"),
        "wind_direction_deg": period.get("wind_direction_deg"),
        "wind_direction_variable": period.get("wind_direction_variable"),
        "wind_speed_kt": period.get("wind_speed_kt"),
        "wind_gust_kt": period.get("wind_gust_kt"),
        "visibility_sm": period.get("visibility_sm"),
        "visibility_qualifier": period.get("visibility_qualifier"),
        "ceiling_ft": period.get("ceiling_ft"),
        "forecast_flight_category": period.get("forecast_flight_category"),
        "weather_string": period.get("weather_string"),
        "weather_codes": period.get("weather_codes", []),
        "clouds": period.get("clouds", []),
        "sequence_number": period["sequence_number"],
        "transition_complete_utc": period.get("transition_complete_utc"),
        "vertical_visibility_ft": period.get("vertical_visibility_ft"),
        "altimeter_in_hg": period.get("altimeter_in_hg"),
        "low_level_wind_shear": period.get("low_level_wind_shear"),
        "icing_turbulence_layers": period.get("icing_turbulence_layers", []),
        "temperature_forecasts": period.get("temperature_forecasts", []),
        "not_decoded": period.get("not_decoded"),
        "materialization_id": parent["materialization_id"],
        "created_at_utc": parent["processed_at_utc"],
        "correlation_id": parent["correlation_id"],
        "schema_version": "internal.taf_forecast_period.v1",
        "expires_at_epoch": parent["expires_at_epoch"],
    }


def write_forecast_periods(parent: dict[str, Any]) -> int:
    periods = parent.get("forecast_periods")
    if not isinstance(periods, list) or not periods:
        raise PermanentRecordError("Normalized TAF does not contain forecast periods")

    with taf_forecast_periods_table.batch_writer(
        overwrite_by_pkeys=["taf_version_key", "period_key"]
    ) as batch:
        for period in periods:
            batch.put_item(
                Item=to_dynamodb(
                    build_period_item(parent=parent, period=period)
                )
            )

    return len(periods)


def update_latest_ready(item: dict[str, Any]) -> None:
    materialized_at = now_utc().isoformat()

    taf_latest_table.update_item(
        Key={"station_id": item["station_id"]},
        UpdateExpression=(
            "SET period_materialization_status = :ready, "
            "materialized_at_utc = :materialized_at, "
            "processed_at_utc = :processed_at"
        ),
        ConditionExpression=(
            "taf_version = :taf_version AND "
            "materialization_id = :materialization_id"
        ),
        ExpressionAttributeValues={
            ":ready": "READY",
            ":materialized_at": materialized_at,
            ":processed_at": materialized_at,
            ":taf_version": item["taf_version"],
            ":materialization_id": item["materialization_id"],
        },
    )

    item["period_materialization_status"] = "READY"
    item["materialized_at_utc"] = materialized_at
    item["processed_at_utc"] = materialized_at


def materialize_taf_version(
    *,
    item: dict[str, Any],
    change_type: str,
) -> bool:
    item["change_type"] = change_type
    item["period_materialization_status"] = "BUILDING"

    written = put_latest(item)
    if not written:
        current = get_existing(item["station_id"])
        if current and current.get("source_version") == item["source_version"]:
            return False
        return False

    period_count = write_forecast_periods(item)
    if period_count != int(item["forecast_period_count"]):
        raise RuntimeError(
            "TafForecastPeriods write count does not match forecast_period_count"
        )

    update_latest_ready(item)
    return True

def publish_taf_materialized(item: dict[str, Any], change_type: str) -> None:
    event_time = now_utc().isoformat()
    detail = {
        "event_id": str(uuid.uuid4()),
        "event_type": "taf.materialized",
        "event_time_utc": event_time,
        "product_type": "TAF",
        "station_id": item["station_id"],
        "airport_id": item.get("airport_id"),
        "entity_id": item["station_id"],
        "entity_version": item["taf_version"],
        "taf_version": item["taf_version"],
        "taf_version_key": item["taf_version_key"],
        "materialization_status": item["period_materialization_status"],
        "change_type": change_type,
        "reason": change_type,
        "issued_at_utc": item["issued_at_utc"],
        "valid_from_utc": item["valid_from_utc"],
        "valid_to_utc": item["valid_to_utc"],
        "forecast_period_count": item["forecast_period_count"],
        "freshness_status": item["freshness_status"],
        "source_system": item["source_system"],
        "source_table": TAF_LATEST_TABLE_NAME,
        "child_table": TAF_FORECAST_PERIODS_TABLE_NAME,
        "schema_version": item["schema_version"],
        "source_version": item["source_version"],
        "raw_s3_uri": item.get("raw_s3_uri"),
        "correlation_id": item.get("correlation_id"),
        "trigger_hazard_version_key": item.get("trigger_hazard_version_key"),
    }

    response = events.put_events(
        Entries=[
            {
                "EventBusName": EVENT_BUS_NAME,
                "Source": "wilvor.weather",
                "DetailType": "taf.materialized",
                "Detail": json.dumps(detail, default=json_default),
            }
        ]
    )

    if int(response.get("FailedEntryCount", 0)) > 0:
        raise RuntimeError(f"EventBridge PutEvents failed: {response.get('Entries')}")


# Keep the old function name as a compatibility wrapper for existing callers/tests.
def publish_weather_changed(item: dict[str, Any], change_type: str) -> None:
    publish_taf_materialized(item, change_type)


def mark_event_published(station_id: str, source_version: str) -> None:
    taf_latest_table.update_item(
        Key={"station_id": station_id},
        UpdateExpression=(
            "SET last_published_source_version = :version, "
            "last_published_at = :published_at"
        ),
        ConditionExpression=(
            "source_version = :version AND "
            "period_materialization_status = :ready"
        ),
        ExpressionAttributeValues={
            ":version": source_version,
            ":published_at": now_utc().isoformat(),
            ":ready": "READY",
        },
    )


def publish_if_needed(existing: dict[str, Any], fallback_kind: str = "UPDATED") -> bool:
    source_version = clean_string(existing.get("source_version"))
    if not source_version:
        return False

    if existing.get("last_published_source_version") == source_version:
        return False

    change_type = clean_string(existing.get("change_type")) or fallback_kind
    publish_weather_changed(existing, change_type)
    mark_event_published(existing["station_id"], source_version)
    return True


def process_record(record: dict[str, Any]) -> str:
    raw_event = decode_kinesis_record(record)
    taf = extract_taf(raw_event)
    item = normalize_taf(raw_event, taf)

    existing = get_existing(item["station_id"])
    change_type = classify_change(existing, item)

    if change_type == "UNCHANGED":
        if existing and existing.get("last_published_source_version") != item["source_version"]:
            publish_if_needed(existing)
        return "UNCHANGED"

    if change_type == "STALE":
        return "STALE"

    materialized = materialize_taf_version(
        item=item,
        change_type=change_type,
    )

    if not materialized:
        current = get_existing(item["station_id"])
        if current and current.get("source_version") == item["source_version"]:
            if current.get("period_materialization_status") == "READY":
                publish_if_needed(current, change_type)
                return "UNCHANGED"
        return "STALE"

    publish_taf_materialized(item, change_type)
    mark_event_published(item["station_id"], item["source_version"])
    return change_type


def archive_bad_record(
    *,
    raw_record: dict[str, Any],
    sequence_number: str,
    reason: str,
) -> str:
    failed_at = now_utc()
    key = (
        f"{BAD_RECORDS_PREFIX.rstrip('/')}/"
        f"year={failed_at.year:04d}/"
        f"month={failed_at.month:02d}/"
        f"day={failed_at.day:02d}/"
        f"taf-bad-{uuid.uuid4()}.json"
    )

    body = {
        "schema_version": "taf_bad_record.v1",
        "source": "taf_processor",
        "failure_reason": reason,
        "sequence_number": sequence_number,
        "failed_at_utc": failed_at.isoformat(),
        "record": raw_record,
    }

    s3.put_object(
        Bucket=BAD_RECORDS_BUCKET_NAME,
        Key=key,
        Body=json.dumps(body, default=json_default).encode("utf-8"),
        ContentType="application/json",
    )
    return key


def record_identifier(record: dict[str, Any]) -> str:
    return str(
        record.get("kinesis", {}).get("sequenceNumber")
        or record.get("eventID")
        or uuid.uuid4()
    )


def lambda_handler(event, context):
    records = event.get("Records", []) if isinstance(event, dict) else []
    failures: list[dict[str, str]] = []

    counts = {
        "NEW": 0,
        "UPDATED": 0,
        "CORRECTED": 0,
        "UNCHANGED": 0,
        "STALE": 0,
        "BAD_RECORD": 0,
        "FAILED": 0,
    }

    for record in records:
        identifier = record_identifier(record)

        try:
            status = process_record(record)
            counts[status] = counts.get(status, 0) + 1
            log_event("TAF record processed", sequence_number=identifier, status=status)

        except PermanentRecordError as exc:
            try:
                bad_key = archive_bad_record(
                    raw_record=record,
                    sequence_number=identifier,
                    reason=str(exc),
                )
                counts["BAD_RECORD"] += 1
                log_event(
                    "TAF record rejected",
                    sequence_number=identifier,
                    reason=str(exc),
                    bad_record_s3_key=bad_key,
                )
            except Exception as archive_exc:
                counts["FAILED"] += 1
                failures.append({"itemIdentifier": identifier})
                log_event(
                    "TAF bad-record archive failed",
                    sequence_number=identifier,
                    error_type=archive_exc.__class__.__name__,
                    error=str(archive_exc),
                )

        except Exception as exc:
            counts["FAILED"] += 1
            failures.append({"itemIdentifier": identifier})
            log_event(
                "TAF record processing failed",
                sequence_number=identifier,
                error_type=exc.__class__.__name__,
                error=str(exc),
            )

    emit_metric(
        pipeline="taf",
        component="taf_processor",
        stage="process",
        metrics={
            "RecordsReceived": len(records),
            "RecordsNew": counts["NEW"],
            "RecordsUpdated": counts["UPDATED"],
            "RecordsCorrected": counts["CORRECTED"],
            "RecordsUnchanged": counts["UNCHANGED"],
            "RecordsStale": counts["STALE"],
            "BadRecords": counts["BAD_RECORD"],
            "ProcessingFailures": counts["FAILED"],
        },
        properties={
            "RequestId": getattr(context, "aws_request_id", ""),
        },
    )

    return {"batchItemFailures": failures}
