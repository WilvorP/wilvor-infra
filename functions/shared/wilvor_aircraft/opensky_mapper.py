from datetime import datetime, timezone
from typing import Any
import h3

from wilvor_aircraft.schemas import (
    AIRCRAFT_CURRENT_STATE_SCHEMA_VERSION,
    METERS_TO_FEET,
    MPS_TO_FPM,
    MPS_TO_KNOTS,
    OPENSKY_REQUIRED_CLEAN_FIELDS,
    OPENSKY_STATE_VECTOR_COLUMNS,
)


def meters_to_feet(value: float | None) -> float | None:
    return value * METERS_TO_FEET if value is not None else None


def mps_to_knots(value: float | None) -> float | None:
    return value * MPS_TO_KNOTS if value is not None else None


def mps_to_fpm(value: float | None) -> float | None:
    return value * MPS_TO_FPM if value is not None else None


def is_missing_required_value(field: str, value: Any) -> bool:
    if value is None:
        return True

    if field == "callsign" and clean_callsign(value) is None:
        return True

    return False

def now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def epoch_to_iso(value: Any) -> str | None:
    if value is None:
        return None

    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def to_float(value: Any) -> float | None:
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_callsign(value: Any) -> str | None:
    if value is None:
        return None

    callsign = str(value).strip()
    return callsign if callsign else None


def vector_to_dict(raw_state_vector: list[Any]) -> dict[str, Any]:
    return {
        column: raw_state_vector[index] if index < len(raw_state_vector) else None
        for index, column in enumerate(OPENSKY_STATE_VECTOR_COLUMNS)
    }


def validate_raw_state_vector(raw_state_vector: Any) -> list[str]:
    if not isinstance(raw_state_vector, list):
        return ["raw_vector_not_list"]

    reasons: list[str] = []

    if len(raw_state_vector) < len(OPENSKY_STATE_VECTOR_COLUMNS):
        reasons.append("raw_vector_too_short")

    row = vector_to_dict(raw_state_vector)

    for field in OPENSKY_REQUIRED_CLEAN_FIELDS:
        value = row.get(field)

        if value is None:
            reasons.append(f"missing_required_{field}")

    icao24 = str(row.get("icao24") or "").strip()
    if not icao24:
        reasons.append("missing_required_icao24")

    latitude = to_float(row.get("latitude"))
    longitude = to_float(row.get("longitude"))
    geo_altitude_m = to_float(row.get("geo_altitude"))
    baro_altitude_m = to_float(row.get("baro_altitude"))
    velocity_mps = to_float(row.get("velocity"))
    true_track = to_float(row.get("true_track"))
    vertical_rate_mps = to_float(row.get("vertical_rate"))
    last_contact = to_float(row.get("last_contact"))
    time_position = to_float(row.get("time_position"))

    # A usable position requires both values.
    if (latitude is None) != (longitude is None):
        reasons.append("incomplete_position")

    if latitude is not None and not (-90 <= latitude <= 90):
        reasons.append("invalid_latitude_range")

    if longitude is not None and not (-180 <= longitude <= 180):
        reasons.append("invalid_longitude_range")

    if geo_altitude_m is not None and not (-500 <= geo_altitude_m <= 25_000):
        reasons.append("unrealistic_geo_altitude")

    if baro_altitude_m is not None and not (-500 <= baro_altitude_m <= 25_000):
        reasons.append("unrealistic_baro_altitude")

    if velocity_mps is not None and not (0 <= velocity_mps <= 400):
        reasons.append("unrealistic_velocity")

    if true_track is not None and not (0 <= true_track <= 360):
        reasons.append("unrealistic_true_track")

    if vertical_rate_mps is not None and not (-150 <= vertical_rate_mps <= 150):
        reasons.append("unrealistic_vertical_rate")

    current_epoch = now_epoch()

    if last_contact is not None:
        if last_contact < 1_262_304_000:
            reasons.append("unrealistic_last_contact_too_old")

        if last_contact > current_epoch + 600:
            reasons.append("unrealistic_last_contact_in_future")

    if time_position is not None:
        if time_position < 1_262_304_000:
            reasons.append("unrealistic_time_position_too_old")

        if time_position > current_epoch + 600:
            reasons.append("unrealistic_time_position_in_future")

    on_ground = row.get("on_ground")
    if on_ground is not None and not isinstance(on_ground, bool):
        reasons.append("invalid_on_ground")

    return sorted(set(reasons))


def map_raw_event_to_current_state(
    raw_event: dict[str, Any],
    *,
    ttl_seconds: int = 1800,
    h3_resolution: int = 4,
    fresh_seconds: int = 60,
    acceptable_seconds: int = 180,
) -> tuple[dict[str, Any] | None, list[str]]:
    raw_state_vector = raw_event.get("raw_state_vector")

    reasons = validate_raw_state_vector(raw_state_vector)

    if not raw_event.get("poll_id"):
        reasons.append("missing_poll_id")

    if not raw_event.get("fetched_at_utc"):
        reasons.append("missing_fetched_at_utc")

    raw_s3_uri = build_raw_s3_uri(raw_event)
    if raw_s3_uri is None:
        reasons.append("missing_raw_s3_lineage")

    if reasons:
        return None, sorted(set(reasons))

    row = vector_to_dict(raw_state_vector)

    aircraft_id = str(row["icao24"]).strip().lower()
    position_time_epoch = int(float(row["time_position"]))
    last_contact_epoch = int(float(row["last_contact"]))

    latitude = to_float(row.get("latitude"))
    longitude = to_float(row.get("longitude"))
    has_position = latitude is not None and longitude is not None

    current_h3_cell: str | None = None

    if has_position:
        try:
            current_h3_cell = h3.latlng_to_cell(
                latitude,
                longitude,
                h3_resolution,
            )
        except Exception as exc:
            return None, [f"h3_conversion_failed:{type(exc).__name__}"]

    processed_at = datetime.now(timezone.utc)
    processed_at_epoch = int(processed_at.timestamp())
    processed_at_utc = processed_at.isoformat()

    position_age_seconds = max(
        0,
        processed_at_epoch - position_time_epoch,
    )

    freshness_status = classify_freshness(
        position_age_seconds,
        fresh_seconds=fresh_seconds,
        acceptable_seconds=acceptable_seconds,
    )

    state_version = f"{aircraft_id}#{position_time_epoch}"

    baro_altitude_m = to_float(row.get("baro_altitude"))
    geo_altitude_m = to_float(row.get("geo_altitude"))
    ground_speed_mps = to_float(row.get("velocity"))
    vertical_rate_mps = to_float(row.get("vertical_rate"))

    item = {
        "aircraft_id": aircraft_id,
        "callsign": clean_callsign(row.get("callsign")),
        "origin_country": row.get("origin_country"),

        "position_time_epoch": position_time_epoch,
        "position_time_utc": epoch_to_iso(position_time_epoch),

        "last_contact_epoch": last_contact_epoch,
        "last_contact_utc": epoch_to_iso(last_contact_epoch),

        "latitude": latitude,
        "longitude": longitude,

        "baro_altitude_m": baro_altitude_m,
        "geo_altitude_m": geo_altitude_m,
        "baro_altitude_ft": meters_to_feet(baro_altitude_m),
        "geo_altitude_ft": meters_to_feet(geo_altitude_m),

        "ground_speed_mps": ground_speed_mps,
        "ground_speed_kt": mps_to_knots(ground_speed_mps),
        "track_deg": to_float(row.get("true_track")),

        "vertical_rate_mps": vertical_rate_mps,
        "vertical_rate_fpm": mps_to_fpm(vertical_rate_mps),

        "on_ground": row.get("on_ground"),
        "squawk": row.get("squawk"),
        "spi": row.get("spi"),
        "position_source": row.get("position_source"),

        "has_position": has_position,
        "current_h3_cell": current_h3_cell,
        "h3_resolution": h3_resolution if has_position else None,

        "position_age_seconds": position_age_seconds,
        "freshness_status": freshness_status,

        "state_version": state_version,
        "idempotency_key": state_version,

        "source_system": "OPEN_SKY",
        "source_event_time_utc": epoch_to_iso(position_time_epoch),

        "received_at_utc": raw_event["fetched_at_utc"],
        "processed_at_utc": processed_at_utc,

        "correlation_id": raw_event["poll_id"],
        "raw_s3_uri": raw_s3_uri,

        "schema_version": AIRCRAFT_CURRENT_STATE_SCHEMA_VERSION,
        "expires_at_epoch": processed_at_epoch + ttl_seconds,
    }

    return item, []

def classify_freshness(
    position_age_seconds: int | None,
    *,
    fresh_seconds: int,
    acceptable_seconds: int,
) -> str:
    if position_age_seconds is None:
        return "UNAVAILABLE"

    if position_age_seconds <= fresh_seconds:
        return "FRESH"

    if position_age_seconds <= acceptable_seconds:
        return "ACCEPTABLE"

    return "STALE"


def build_raw_s3_uri(raw_event: dict[str, Any]) -> str | None:
    bucket = raw_event.get("raw_s3_bucket")
    key = raw_event.get("raw_s3_key")

    if not bucket or not key:
        return None

    return f"s3://{bucket}/{key}"