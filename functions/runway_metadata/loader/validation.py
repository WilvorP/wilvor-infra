from __future__ import annotations

import math
import re
from typing import Iterable

try:
    from .models import NormalizedRunway, RunwayEnd
except ImportError:  # Lambda ZIP places modules at the package root.
    from models import NormalizedRunway, RunwayEnd

RUNWAY_END_PATTERN = re.compile(r"^(?:0?[1-9]|[12][0-9]|3[0-6])(?:[LRC])?$", re.IGNORECASE)


class RecordValidationError(ValueError):
    """Raised for a source record that cannot become valid through retry."""


def clean_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_optional_float(value: object, field_name: str) -> float | None:
    text = clean_string(value)
    if text is None:
        return None
    try:
        parsed = float(text)
    except (TypeError, ValueError) as exc:
        raise RecordValidationError(f"{field_name} must be numeric; received {text!r}") from exc
    if not math.isfinite(parsed):
        raise RecordValidationError(f"{field_name} must be finite")
    return parsed


def parse_optional_int(value: object, field_name: str) -> int | None:
    parsed = parse_optional_float(value, field_name)
    if parsed is None:
        return None
    if not parsed.is_integer():
        raise RecordValidationError(f"{field_name} must be a whole number; received {parsed}")
    return int(parsed)


def require_positive_int(value: object, field_name: str) -> int:
    parsed = parse_optional_int(value, field_name)
    if parsed is None:
        raise RecordValidationError(f"{field_name} is required")
    if parsed <= 0:
        raise RecordValidationError(f"{field_name} must be greater than zero")
    return parsed


def validate_heading(value: float | None, field_name: str) -> None:
    if value is not None and not 0.0 <= value <= 360.0:
        raise RecordValidationError(f"{field_name} must be between 0 and 360 degrees")


def validate_latitude(value: float | None, field_name: str) -> None:
    if value is not None and not -90.0 <= value <= 90.0:
        raise RecordValidationError(f"{field_name} must be between -90 and 90")


def validate_longitude(value: float | None, field_name: str) -> None:
    if value is not None and not -180.0 <= value <= 180.0:
        raise RecordValidationError(f"{field_name} must be between -180 and 180")


def canonical_runway_end_id(value: object) -> str:
    text = clean_string(value)
    if text is None:
        raise RecordValidationError("runway end identifier is required")
    canonical = text.upper().replace(" ", "")
    if not RUNWAY_END_PATTERN.fullmatch(canonical):
        raise RecordValidationError(f"invalid runway end identifier {text!r}")
    number_match = re.match(r"^(\d{1,2})([LRC]?)$", canonical)
    assert number_match is not None
    return f"{int(number_match.group(1)):02d}{number_match.group(2)}"


def canonical_physical_runway_id(value: object) -> str:
    text = clean_string(value)
    if text is None:
        raise RecordValidationError("physical runway identifier is required")
    parts = [part for part in re.split(r"[/\\-]", text.upper().replace(" ", "")) if part]
    if not parts or len(parts) > 2:
        raise RecordValidationError(f"invalid physical runway identifier {text!r}")
    canonical_parts = [canonical_runway_end_id(part) for part in parts]
    return "/".join(canonical_parts)


def expected_end_ids(physical_runway_id: str) -> tuple[str, ...]:
    return tuple(physical_runway_id.split("/"))


def validate_runway_end(end: RunwayEnd, *, allowed_end_ids: Iterable[str]) -> None:
    allowed = set(allowed_end_ids)
    if end.runway_end_id not in allowed:
        raise RecordValidationError(
            f"runway end {end.runway_end_id} does not belong to physical runway {sorted(allowed)}"
        )
    validate_heading(end.true_heading_deg, f"{end.runway_end_id}.true_heading_deg")
    validate_latitude(end.latitude, f"{end.runway_end_id}.latitude")
    validate_longitude(end.longitude, f"{end.runway_end_id}.longitude")
    for field_name, value in (
        ("landing_distance_available_ft", end.landing_distance_available_ft),
        ("takeoff_run_available_ft", end.takeoff_run_available_ft),
        ("takeoff_distance_available_ft", end.takeoff_distance_available_ft),
        ("accelerate_stop_distance_available_ft", end.accelerate_stop_distance_available_ft),
    ):
        if value is not None and value <= 0:
            raise RecordValidationError(f"{end.runway_end_id}.{field_name} must be greater than zero")


def validate_normalized_runway(runway: NormalizedRunway) -> None:
    if not runway.airport_id:
        raise RecordValidationError("airport ICAO identifier is required")
    if not runway.faa_id:
        raise RecordValidationError("FAA airport identifier is required")
    if runway.length_ft <= 0:
        raise RecordValidationError("runway length must be greater than zero")
    if runway.width_ft is not None and runway.width_ft <= 0:
        raise RecordValidationError("runway width must be greater than zero when provided")

    allowed = expected_end_ids(runway.physical_runway_id)
    validate_runway_end(runway.end_1, allowed_end_ids=allowed)
    if runway.end_2:
        validate_runway_end(runway.end_2, allowed_end_ids=allowed)
        if runway.end_1.runway_end_id == runway.end_2.runway_end_id:
            raise RecordValidationError("runway ends must have different identifiers")

    if len(allowed) == 2 and runway.end_2 is None:
        raise RecordValidationError(
            f"physical runway {runway.physical_runway_id} requires two runway-end records"
        )
