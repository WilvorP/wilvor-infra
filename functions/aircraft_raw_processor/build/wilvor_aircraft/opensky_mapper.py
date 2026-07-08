from datetime import datetime, timezone
from typing import Any

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
        if is_missing_required_value(field, row.get(field)):
            reasons.append(f"missing_required_{field}")

    latitude = to_float(row.get("latitude"))
    longitude = to_float(row.get("longitude"))
    geo_altitude_m = to_float(row.get("geo_altitude"))
    baro_altitude_m = to_float(row.get("baro_altitude"))
    velocity_mps = to_float(row.get("velocity"))
    true_track = to_float(row.get("true_track"))
    vertical_rate_mps = to_float(row.get("vertical_rate"))
    last_contact = to_float(row.get("last_contact"))
    time_position = to_float(row.get("time_position"))

    if latitude is not None and not (-90 <= latitude <= 90):
        reasons.append("invalid_latitude_range")

    if longitude is not None and not (-180 <= longitude <= 180):
        reasons.append("invalid_longitude_range")

    if geo_altitude_m is not None and not (-500 <= geo_altitude_m <= 25000):
        reasons.append("unrealistic_geo_altitude")

    if baro_altitude_m is not None and not (-500 <= baro_altitude_m <= 25000):
        reasons.append("unrealistic_baro_altitude")

    if velocity_mps is not None and not (0 <= velocity_mps <= 400):
        reasons.append("unrealistic_velocity")

    if true_track is not None and not (0 <= true_track <= 360):
        reasons.append("unrealistic_true_track")

    if vertical_rate_mps is not None and not (-150 <= vertical_rate_mps <= 150):
        reasons.append("unrealistic_vertical_rate")

    if last_contact is not None:
        current_epoch = now_epoch()

        if last_contact < 1262304000:
            reasons.append("unrealistic_last_contact_too_old")

        if last_contact > current_epoch + 600:
            reasons.append("unrealistic_last_contact_in_future")

    if time_position is not None:
        current_epoch = now_epoch()

        if time_position < 1262304000:
            reasons.append("unrealistic_time_position_too_old")

        if time_position > current_epoch + 600:
            reasons.append("unrealistic_time_position_in_future")

    on_ground = row.get("on_ground")
    if on_ground is not None and not isinstance(on_ground, bool):
        reasons.append("invalid_on_ground")

    return reasons


def map_raw_event_to_current_state(
    raw_event: dict[str, Any],
    *,
    ttl_seconds: int = 1800,
) -> tuple[dict[str, Any] | None, list[str]]:
    raw_state_vector = raw_event.get("raw_state_vector")

    reasons = validate_raw_state_vector(raw_state_vector)
    if reasons:
        return None, reasons

    row = vector_to_dict(raw_state_vector)

    icao24 = str(row["icao24"]).strip().lower()
    time_position = to_float(row.get("time_position"))
    last_contact = to_float(row.get("last_contact"))

    latitude = to_float(row.get("latitude"))
    longitude = to_float(row.get("longitude"))

    has_position = latitude is not None and longitude is not None

    baro_altitude_m = to_float(row.get("baro_altitude"))
    geo_altitude_m = to_float(row.get("geo_altitude"))
    velocity_mps = to_float(row.get("velocity"))
    vertical_rate_mps = to_float(row.get("vertical_rate"))

    idempotency_time = time_position or last_contact

    item = {
        "icao24": icao24,
        "aircraft_id": icao24,
        "callsign": clean_callsign(row.get("callsign")),
        "origin_country": row.get("origin_country"),

        "position_time_epoch": time_position,
        "position_time_utc": epoch_to_iso(time_position),

        "last_contact_epoch": last_contact,
        "last_contact_utc": epoch_to_iso(last_contact),

        "latitude": latitude,
        "longitude": longitude,
        "has_position": has_position,

        "baro_altitude_m": baro_altitude_m,
        "geo_altitude_m": geo_altitude_m,

        "baro_altitude_ft": meters_to_feet(baro_altitude_m),
        "geo_altitude_ft": meters_to_feet(geo_altitude_m),

        "ground_speed_mps": velocity_mps,
        "ground_speed_kt": mps_to_knots(velocity_mps),

        "track_deg": to_float(row.get("true_track")),

        "vertical_rate_mps": vertical_rate_mps,
        "vertical_rate_fpm": mps_to_fpm(vertical_rate_mps),

        "on_ground": row.get("on_ground"),
        "squawk": row.get("squawk"),
        "spi": row.get("spi"),
        "position_source": row.get("position_source"),

        "source_system": "OpenSky",
        "schema_version": AIRCRAFT_CURRENT_STATE_SCHEMA_VERSION,

        "received_at_utc": now_utc_iso(),
        "poll_id": raw_event.get("poll_id"),
        "raw_index": raw_event.get("raw_index"),
        "opensky_response_time": raw_event.get("opensky_response_time"),
        "fetched_at_utc": raw_event.get("fetched_at_utc"),

        "idempotency_key": f"{icao24}#{int(idempotency_time)}",
        "ttl_epoch": now_epoch() + ttl_seconds,
    }

    return item, []