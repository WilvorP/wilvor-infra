from datetime import datetime, timezone
from typing import Any

from wilvor_aircraft.schemas import (
    AIRCRAFT_CURRENT_STATE_SCHEMA_VERSION,
    OPENSKY_STATE_VECTOR_COLUMNS,
)


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

    reasons = []

    if len(raw_state_vector) < 17:
        reasons.append("raw_vector_too_short")

    row = vector_to_dict(raw_state_vector)

    if not row.get("icao24"):
        reasons.append("missing_icao24")

    latitude = to_float(row.get("latitude"))
    longitude = to_float(row.get("longitude"))

    if row.get("latitude") is not None and latitude is None:
        reasons.append("invalid_latitude")

    if row.get("longitude") is not None and longitude is None:
        reasons.append("invalid_longitude")

    if latitude is not None and not (-90 <= latitude <= 90):
        reasons.append("invalid_latitude_range")

    if longitude is not None and not (-180 <= longitude <= 180):
        reasons.append("invalid_longitude_range")

    if row.get("last_contact") is not None and to_float(row.get("last_contact")) is None:
        reasons.append("invalid_last_contact")

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
        "ground_speed_mps": velocity_mps,
        "track_deg": to_float(row.get("true_track")),
        "vertical_rate_mps": vertical_rate_mps,

        "on_ground": row.get("on_ground"),
        "squawk": row.get("squawk"),
        "spi": row.get("spi"),
        "position_source": row.get("position_source"),

        "source_system": "OpenSky",
        "schema_version": AIRCRAFT_CURRENT_STATE_SCHEMA_VERSION,

        "received_at_utc": now_utc_iso(),
        "poll_id": raw_event.get("poll_id"),
        "raw_index": raw_event.get("raw_index"),

        "idempotency_key": f"{icao24}#{last_contact or time_position}",
        "ttl_epoch": now_epoch() + ttl_seconds,
    }

    return item, []