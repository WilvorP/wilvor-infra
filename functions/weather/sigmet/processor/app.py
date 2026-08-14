import base64
import binascii
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import boto3
import h3
from botocore.exceptions import BotoCoreError, ClientError
from wilvor_weather.monitoring import emit_metric


logger = logging.getLogger()
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

IMPACT_RADIUS_NM = Decimal(
    os.environ.get("IMPACT_RADIUS_NM", "50")
)

if IMPACT_RADIUS_NM < 0:
    raise RuntimeError("IMPACT_RADIUS_NM cannot be negative")


ACTIVE_HAZARDS_TABLE_NAME = os.environ[
    "ACTIVE_HAZARDS_TABLE_NAME"
]

HAZARD_COORDINATES_TABLE_NAME = os.environ[
    "HAZARD_COORDINATES_TABLE_NAME"
]

HAZARD_CELLS_TABLE_NAME = os.environ[
    "HAZARD_CELLS_TABLE_NAME"
]

IMPACT_CELLS_TABLE_NAME = os.environ[
    "IMPACT_CELLS_TABLE_NAME"
]


H3_RESOLUTION = int(
    os.environ.get("H3_RESOLUTION", "4")
)

IMPACT_GRID_DISTANCE = int(
    os.environ.get("IMPACT_GRID_DISTANCE", "2")
)

if IMPACT_GRID_DISTANCE < 0:
    raise RuntimeError(
        "IMPACT_GRID_DISTANCE cannot be negative"
    )


SCHEMA_VERSION = os.environ.get(
    "SCHEMA_VERSION",
    "wilvor.active_hazards.v4.0",
)

HAZARD_COORDINATES_SCHEMA_VERSION = os.environ.get(
    "HAZARD_COORDINATES_SCHEMA_VERSION",
    "wilvor.hazard_coordinates.v4.0",
)

HAZARD_CELLS_SCHEMA_VERSION = os.environ.get(
    "HAZARD_CELLS_SCHEMA_VERSION",
    "wilvor.hazard_cells.v4.0",
)

IMPACT_CELLS_SCHEMA_VERSION = os.environ.get(
    "IMPACT_CELLS_SCHEMA_VERSION",
    "wilvor.impact_cells.v4.0",
)

IMPACT_EXPANSION_CONFIG_VERSION = os.environ.get(
    "IMPACT_EXPANSION_CONFIG_VERSION",
    "wilvor.impact_expansion.v1",
)

RETENTION_AFTER_VALID_TO_HOURS = int(
    os.environ.get(
        "RETENTION_AFTER_VALID_TO_HOURS",
        "6",
    )
)

if RETENTION_AFTER_VALID_TO_HOURS < 0:
    raise RuntimeError(
        "RETENTION_AFTER_VALID_TO_HOURS "
        "cannot be negative"
    )


SOURCE_SYSTEM = "NOAA_AVIATIONWEATHER_SIGMET"


BAD_RECORDS_BUCKET_NAME = os.environ.get(
    "BAD_RECORDS_BUCKET_NAME"
)

BAD_RECORDS_PREFIX = os.environ.get(
    "BAD_RECORDS_PREFIX",
    "bad-records/source=sigmet_processor",
)

EVENT_BUS_NAME = os.environ.get(
    "EVENT_BUS_NAME",
    "default",
)


# ---------------------------------------------------------------------------
# AWS clients/resources
# ---------------------------------------------------------------------------

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
events_client = boto3.client("events")


active_hazards_table = dynamodb.Table(
    ACTIVE_HAZARDS_TABLE_NAME
)

hazard_coordinates_table = dynamodb.Table(
    HAZARD_COORDINATES_TABLE_NAME
)

hazard_cells_table = dynamodb.Table(
    HAZARD_CELLS_TABLE_NAME
)

impact_cells_table = dynamodb.Table(
    IMPACT_CELLS_TABLE_NAME
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PermanentRecordError(Exception):
    """
    Validation error that will not be fixed by retrying
    the same Kinesis record.
    """


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, set):
        return sorted(value)

    return str(value)


def log_event(
    message: str,
    **kwargs: Any,
) -> None:
    logger.info(
        json.dumps(
            {
                "message": message,
                **kwargs,
            },
            default=json_default,
        )
    )


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_utc_iso() -> str:
    return now_utc().isoformat()


def stable_hash(value: Any) -> str:
    serialized = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=json_default,
    )

    return hashlib.sha256(
        serialized.encode("utf-8")
    ).hexdigest()


def parse_time(
    value: Any,
) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, Decimal):
        value = float(value)

    if isinstance(value, (int, float)):
        # Epoch milliseconds.
        if value > 10_000_000_000:
            value = value / 1000

        try:
            return datetime.fromtimestamp(
                value,
                tz=timezone.utc,
            )
        except (ValueError, OSError, OverflowError):
            return None

    if not isinstance(value, str):
        return None

    cleaned = value.strip()

    if not cleaned:
        return None

    # Numeric epoch represented as a string.
    try:
        numeric_value = Decimal(cleaned)

        if re.fullmatch(
            r"-?\d+(\.\d+)?",
            cleaned,
        ):
            return parse_time(
                float(numeric_value)
            )

    except InvalidOperation:
        pass

    try:
        if cleaned.endswith("Z"):
            cleaned = (
                cleaned[:-1]
                + "+00:00"
            )

        parsed = datetime.fromisoformat(
            cleaned
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return parsed.astimezone(
            timezone.utc
        )

    except ValueError:
        return None


def iso_or_none(
    dt: datetime | None,
) -> str | None:
    return dt.isoformat() if dt else None


def clean_string(
    value: Any,
) -> str | None:
    if value is None:
        return None

    if isinstance(
        value,
        (dict, list),
    ):
        text = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=json_default,
        )

    else:
        text = str(value)

    text = text.strip()

    return text if text else None


def canonical_time_or_string(
    value: Any,
) -> str | None:
    parsed = parse_time(value)

    if parsed:
        return parsed.isoformat()

    return clean_string(value)


def get_first_property(
    properties: dict[str, Any],
    keys: list[str],
) -> Any:
    for key in keys:
        value = properties.get(key)

        if value is None:
            continue

        if isinstance(value, str):
            if not value.strip():
                continue

        return value

    return None


def decimal_or_none(
    value: Any,
) -> Decimal | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, str):
        value = value.strip()

        if not value:
            return None

    try:
        return Decimal(str(value))

    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return None


def normalized_upper_or_none(
    value: Any,
) -> str | None:
    cleaned = clean_string(value)

    if not cleaned:
        return None

    return (
        cleaned.upper()
        .replace("-", "_")
        .replace(" ", "_")
    )


# ---------------------------------------------------------------------------
# Kinesis decoding
# ---------------------------------------------------------------------------

def decode_kinesis_record(
    record: dict[str, Any],
) -> dict[str, Any]:
    try:
        encoded_data = record[
            "kinesis"
        ]["data"]

    except KeyError as exc:
        raise PermanentRecordError(
            "Kinesis record is missing kinesis.data"
        ) from exc

    try:
        decoded = (
            base64.b64decode(
                encoded_data
            )
            .decode("utf-8")
        )

    except (
        binascii.Error,
        UnicodeDecodeError,
    ) as exc:
        raise PermanentRecordError(
            "Kinesis record data is not "
            "valid base64 UTF-8"
        ) from exc

    try:
        payload = json.loads(decoded)

    except json.JSONDecodeError as exc:
        raise PermanentRecordError(
            "Kinesis record data is not valid JSON"
        ) from exc

    if not isinstance(payload, dict):
        raise PermanentRecordError(
            "Decoded Kinesis payload "
            "is not a JSON object"
        )

    return payload


# ---------------------------------------------------------------------------
# GeoJSON extraction
# ---------------------------------------------------------------------------

def extract_feature(
    raw_event: dict[str, Any],
) -> dict[str, Any]:
    feature = raw_event.get(
        "feature"
    )

    if not isinstance(
        feature,
        dict,
    ):
        raise PermanentRecordError(
            "Kinesis payload does not contain "
            "a valid GeoJSON feature"
        )

    if feature.get("type") != "Feature":
        raise PermanentRecordError(
            "SIGMET record is not a "
            "GeoJSON Feature"
        )

    return feature


def extract_properties(
    feature: dict[str, Any],
) -> dict[str, Any]:
    properties = (
        feature.get("properties")
        or {}
    )

    if not isinstance(
        properties,
        dict,
    ):
        raise PermanentRecordError(
            "SIGMET feature properties "
            "are missing or invalid"
        )

    return properties


# ---------------------------------------------------------------------------
# Hazard identity
# ---------------------------------------------------------------------------

def normalize_identity_part(
    value: Any,
) -> str | None:
    cleaned = clean_string(value)

    if not cleaned:
        return None

    return cleaned.upper()


def extract_source_identity(
    properties: dict[str, Any],
) -> dict[str, str | None]:
    identity = {
        "source_icao_id": (
            normalize_identity_part(
                properties.get(
                    "icaoId"
                )
            )
        ),
        "air_sigmet_type": (
            normalize_identity_part(
                properties.get(
                    "airSigmetType"
                )
            )
        ),
        "alpha_char": (
            normalize_identity_part(
                properties.get(
                    "alphaChar"
                )
            )
        ),
        "series_id": (
            normalize_identity_part(
                properties.get(
                    "seriesId"
                )
            )
        ),
    }

    if not any(identity.values()):
        raise PermanentRecordError(
            "SIGMET record has no usable "
            "source identity fields"
        )

    return identity


def build_source_product_id(
    properties: dict[str, Any],
) -> str:
    """
    Source-product identity that should remain
    stable across amendments/corrections.

    Prefer an explicit source ID when available.
    Otherwise construct a deterministic identity
    from NOAA product identity fields.
    """

    explicit_id = get_first_property(
        properties,
        [
            "id",
            "airSigmetId",
            "sigmetId",
            "productId",
        ],
    )

    if explicit_id is not None:
        cleaned = clean_string(
            explicit_id
        )

        if cleaned:
            return cleaned

    identity = extract_source_identity(
        properties
    )

    if (
        not identity.get("series_id")
        and not identity.get("alpha_char")
    ):
        raise PermanentRecordError(
            "SIGMET record lacks sufficient "
            "stable product identity"
        )

    return "|".join(
        [
            identity.get(
                "source_icao_id"
            )
            or "",
            identity.get(
                "air_sigmet_type"
            )
            or "",
            identity.get(
                "alpha_char"
            )
            or "",
            identity.get(
                "series_id"
            )
            or "",
        ]
    )


def build_hazard_id(
    properties: dict[str, Any],
) -> str:
    """
    Stable Wilvor hazard identity.

    Creation time, validity period, geometry,
    altitude and movement are intentionally
    excluded so an amendment keeps the same
    hazard_id.
    """

    source_product_id = (
        build_source_product_id(
            properties
        )
    )

    fingerprint = {
        "source_system": SOURCE_SYSTEM,
        "source_product_id": (
            source_product_id
        ),
    }

    return (
        "sigmet-"
        f"{stable_hash(fingerprint)[:24]}"
    )


def build_source_version(
    feature: dict[str, Any],
) -> str:
    """
    Revision identifier.

    Unlike hazard_id, this SHOULD change when
    the source product materially changes.
    """

    properties = extract_properties(
        feature
    )

    content_fingerprint = {
        "materialization_contract": (
            "hazard_coordinates_metadata_v2"
        ),
        "rawAirSigmet": properties.get(
            "rawAirSigmet"
        ),
        "rawSigmet": properties.get(
            "rawSigmet"
        ),
        "coords": properties.get(
            "coords"
        ),
        "geometry": feature.get(
            "geometry"
        ),
        "hazard": properties.get(
            "hazard"
        ),
        "severity": properties.get(
            "severity"
        ),
        "altitudeHi1": properties.get(
            "altitudeHi1"
        ),
        "altitudeLow1": properties.get(
            "altitudeLow1"
        ),
        "altitudeHi2": properties.get(
            "altitudeHi2"
        ),
        "altitudeLow2": properties.get(
            "altitudeLow2"
        ),
        "movementDir": properties.get(
            "movementDir"
        ),
        "movementSpd": properties.get(
            "movementSpd"
        ),
        "postProcessFlag": properties.get(
            "postProcessFlag"
        ),
        "validTimeFrom": (
            canonical_time_or_string(
                properties.get(
                    "validTimeFrom"
                )
            )
        ),
        "validTimeTo": (
            canonical_time_or_string(
                properties.get(
                    "validTimeTo"
                )
            )
        ),
        "sourceEventTime": (
            canonical_time_or_string(
                get_first_property(
                    properties,
                    [
                        "creationTime",
                        "createdTime",
                        "createTime",
                        "issueTime",
                        "issuedTime",
                        "issued_at",
                        "issuedAt",
                        "issuanceTime",
                        "validTimeFrom",
                    ],
                )
            )
        ),
    }

    return stable_hash(
        content_fingerprint
    )[:32]


# ---------------------------------------------------------------------------
# SIGMET metadata
# ---------------------------------------------------------------------------

def get_valid_from(
    properties: dict[str, Any],
) -> datetime | None:
    return parse_time(
        get_first_property(
            properties,
            [
                "validTimeFrom",
                "valid_from",
                "validFrom",
                "valid_from_time",
            ],
        )
    )


def get_valid_to(
    properties: dict[str, Any],
) -> datetime | None:
    return parse_time(
        get_first_property(
            properties,
            [
                "validTimeTo",
                "valid_to",
                "validTo",
                "valid_to_time",
            ],
        )
    )


def get_issued_at(
    properties: dict[str, Any],
    raw_event: dict[str, Any] | None = None,
) -> datetime | None:
    """
    Source issuance/creation timestamp.

    NOAA Aviation Weather AirSIGMET GeoJSON does not always
    provide a separate creationTime / issueTime field. In that
    case, use validTimeFrom as the best source-event timestamp,
    then fall back to the ingestion envelope received_at.

    Validity itself is still handled separately by get_valid_from()
    and get_valid_to().
    """

    issued_at = parse_time(
        get_first_property(
            properties,
            [
                "creationTime",
                "createdTime",
                "createTime",
                "issueTime",
                "issuedTime",
                "issued_at",
                "issuedAt",
                "issuanceTime",
                "validTimeFrom",
                "valid_from",
                "validFrom",
                "valid_from_time",
            ],
        )
    )

    if issued_at is not None:
        return issued_at

    if raw_event is not None:
        return parse_time(
            raw_event.get("received_at")
        )

    return None


def normalize_hazard_type(
    value: str,
) -> str:
    normalized = (
        value.strip()
        .upper()
        .replace("-", "_")
        .replace(" ", "_")
    )

    if (
        "TURB" in normalized
        or "TURBULENCE" in normalized
    ):
        return "TURBULENCE"

    if (
        "ICING" in normalized
        or normalized == "ICE"
    ):
        return "ICING"

    if (
        "CONVECT" in normalized
        or "THUNDERSTORM" in normalized
    ):
        return "CONVECTION"

    if (
        "VOLCANIC" in normalized
        and "ASH" in normalized
    ):
        return "VOLCANIC_ASH"

    if normalized == "ASH":
        return "VOLCANIC_ASH"

    if (
        "MTN_OBSCN" in normalized
        or "MOUNTAIN_OBSCURATION" in normalized
    ):
        return "MOUNTAIN_OBSCURATION"

    if normalized == "IFR":
        return "IFR"

    return normalized


def get_raw_text(
    properties: dict[str, Any],
) -> str | None:
    value = get_first_property(
        properties,
        [
            "rawAirSigmet",
            "rawSigmet",
            "raw_text",
            "rawText",
            "text",
        ],
    )

    if value is None:
        return None

    return str(value)


def get_hazard_type(
    properties: dict[str, Any],
) -> str:
    value = get_first_property(
        properties,
        [
            "hazard",
            "hazardType",
            "hazard_type",
            "phenomenon",
        ],
    )

    if value:
        return normalize_hazard_type(
            str(value)
        )

    raw_text = get_raw_text(
        properties
    )

    if raw_text:
        text = raw_text.upper()

        if (
            "VOLCANIC" in text
            and "ASH" in text
        ):
            return "VOLCANIC_ASH"

        if (
            "CONVECT" in text
            or "THUNDERSTORM" in text
        ):
            return "CONVECTION"

        if "TURB" in text:
            return "TURBULENCE"

        if (
            "ICING" in text
            or re.search(r"\bICE\b", text)
        ):
            return "ICING"

        if "MTN OBSCN" in text:
            return "MOUNTAIN_OBSCURATION"

        if re.search(r"\bIFR\b", text):
            return "IFR"

    # airSigmetType can sometimes contain a useful
    # hazard classification such as Convective.
    air_sigmet_type = normalized_upper_or_none(
        properties.get("airSigmetType")
    )

    if (
        air_sigmet_type
        and air_sigmet_type
        not in {"SIGMET", "AIRMET"}
    ):
        return normalize_hazard_type(
            air_sigmet_type
        )

    return "UNKNOWN"


def get_product_type(
    properties: dict[str, Any],
) -> str:
    value = get_first_property(
        properties,
        [
            "productType",
            "product_type",
            "airSigmetType",
        ],
    )

    normalized = (
        normalized_upper_or_none(
            value
        )
    )

    if (
        normalized
        and "AIRMET" in normalized
    ):
        return "AIRMET"

    return "SIGMET"


def get_amendment_type(
    properties: dict[str, Any],
) -> str:
    explicit = (
        normalized_upper_or_none(
            get_first_property(
                properties,
                [
                    "amendmentType",
                    "amendment_type",
                    "productAction",
                ],
            )
        )
    )

    mapping = {
        "ORIGINAL": "ORIGINAL",
        "AMENDMENT": "AMENDMENT",
        "AMD": "AMENDMENT",
        "AMENDED": "AMENDMENT",
        "CORRECTION": "CORRECTION",
        "COR": "CORRECTION",
        "CORRECTED": "CORRECTION",
        "CANCELLATION": "CANCELLATION",
        "CANCELLED": "CANCELLATION",
        "CANCELED": "CANCELLATION",
        "CNL": "CANCELLATION",
    }

    if explicit in mapping:
        return mapping[explicit]

    source_status = (
        normalized_upper_or_none(
            get_first_property(
                properties,
                [
                    "status",
                    "productStatus",
                    "state",
                ],
            )
        )
    )

    if source_status in {
        "CANCELLED",
        "CANCELED",
        "CNL",
    }:
        return "CANCELLATION"

    raw_text = get_raw_text(
        properties
    )

    if raw_text:
        text = raw_text.upper()

        if re.search(
            r"\bCNL\b",
            text,
        ):
            return "CANCELLATION"

        if re.search(
            r"\bCOR\b",
            text,
        ):
            return "CORRECTION"

        if re.search(
            r"\bAMD\b",
            text,
        ):
            return "AMENDMENT"

    # Do not invent ORIGINAL if the source does
    # not provide enough information.
    return "UNKNOWN"


def build_altitude_bands(
    properties: dict[str, Any],
) -> tuple[
    list[dict[str, Any]],
    Decimal | None,
    Decimal | None,
]:
    bands: list[
        dict[str, Any]
    ] = []

    lower_values: list[
        Decimal
    ] = []

    upper_values: list[
        Decimal
    ] = []

    for band_index in (1, 2):
        lower = decimal_or_none(
            properties.get(
                f"altitudeLow{band_index}"
            )
        )

        upper = decimal_or_none(
            properties.get(
                f"altitudeHi{band_index}"
            )
        )

        if (
            lower is None
            and upper is None
        ):
            continue

        band: dict[str, Any] = {
            "source_band_index": (
                band_index
            )
        }

        if lower is not None:
            band[
                "lower_altitude_ft"
            ] = lower

            lower_values.append(
                lower
            )

        if upper is not None:
            band[
                "upper_altitude_ft"
            ] = upper

            upper_values.append(
                upper
            )

        bands.append(band)

    minimum_lower = (
        min(lower_values)
        if lower_values
        else None
    )

    maximum_upper = (
        max(upper_values)
        if upper_values
        else None
    )

    return (
        bands,
        minimum_lower,
        maximum_upper,
    )


def get_hazard_status(
    properties: dict[str, Any],
    valid_to: datetime | None,
    amendment_type: str,
) -> str:
    if amendment_type == "CANCELLATION":
        return "CANCELLED"

    source_status = (
        normalized_upper_or_none(
            get_first_property(
                properties,
                [
                    "status",
                    "productStatus",
                    "state",
                ],
            )
        )
    )

    if source_status in {
        "CANCELLED",
        "CANCELED",
        "CNL",
    }:
        return "CANCELLED"

    if (
        valid_to
        and valid_to <= now_utc()
    ):
        return "EXPIRED"

    return "ACTIVE"


def ttl_for_hazard(
    valid_to: datetime,
    status: str,
) -> int:
    retention = timedelta(
        hours=(
            RETENTION_AFTER_VALID_TO_HOURS
        )
    )

    if status == "CANCELLED":
        return int(
            (
                now_utc()
                + retention
            ).timestamp()
        )

    return int(
        (
            valid_to
            + retention
        ).timestamp()
    )


# ---------------------------------------------------------------------------
# Geometry validation and H3
# ---------------------------------------------------------------------------

def normalize_ring_lonlat_to_latlng(
    ring: list[Any],
) -> list[tuple[float, float]]:
    normalized: list[
        tuple[float, float]
    ] = []

    for point in ring:
        if (
            not isinstance(
                point,
                (list, tuple),
            )
            or len(point) < 2
        ):
            continue

        lon = point[0]
        lat = point[1]

        if (
            isinstance(lat, bool)
            or isinstance(lon, bool)
            or not isinstance(
                lat,
                (int, float),
            )
            or not isinstance(
                lon,
                (int, float),
            )
        ):
            continue

        normalized.append(
            (
                float(lat),
                float(lon),
            )
        )

    # Closing duplicate is not needed by H3.
    if (
        len(normalized) >= 2
        and normalized[0]
        == normalized[-1]
    ):
        normalized = normalized[
            :-1
        ]

    return normalized


def polygon_to_h3_cells(
    polygon_coordinates: list[Any],
    resolution: int,
) -> set[str]:
    if (
        not isinstance(
            polygon_coordinates,
            list,
        )
        or not polygon_coordinates
    ):
        raise PermanentRecordError(
            "Polygon coordinates "
            "are missing or invalid"
        )

    outer = (
        normalize_ring_lonlat_to_latlng(
            polygon_coordinates[0]
        )
    )

    holes = [
        normalize_ring_lonlat_to_latlng(
            ring
        )
        for ring in (
            polygon_coordinates[1:]
        )
        if isinstance(
            ring,
            list,
        )
    ]

    if len(outer) < 3:
        raise PermanentRecordError(
            "Polygon outer ring has fewer "
            "than three valid points"
        )

    holes = [
        hole
        for hole in holes
        if len(hole) >= 3
    ]

    try:
        polygon = h3.LatLngPoly(
            outer,
            *holes,
        )

        return set(
            h3.polygon_to_cells(
                polygon,
                resolution,
            )
        )

    except Exception as exc:
        raise PermanentRecordError(
            "Failed to convert polygon "
            f"to H3 cells: {exc}"
        ) from exc


def polygon_centroid_cell(
    polygon_coordinates: list[Any],
    resolution: int,
) -> str:
    if (
        not isinstance(
            polygon_coordinates,
            list,
        )
        or not polygon_coordinates
    ):
        raise PermanentRecordError(
            "Polygon coordinates "
            "are missing or invalid"
        )

    outer_ring = (
        polygon_coordinates[0]
    )

    if (
        not isinstance(
            outer_ring,
            list,
        )
        or len(outer_ring) < 3
    ):
        raise PermanentRecordError(
            "Polygon outer ring has "
            "fewer than three points"
        )

    lat_values: list[float] = []
    lon_values: list[float] = []

    for point in outer_ring:
        if (
            not isinstance(
                point,
                (list, tuple),
            )
            or len(point) < 2
        ):
            continue

        lon = point[0]
        lat = point[1]

        if (
            isinstance(lat, bool)
            or isinstance(lon, bool)
        ):
            continue

        if (
            isinstance(
                lat,
                (int, float),
            )
            and isinstance(
                lon,
                (int, float),
            )
        ):
            lat_values.append(
                float(lat)
            )

            lon_values.append(
                float(lon)
            )

    if (
        not lat_values
        or not lon_values
    ):
        raise PermanentRecordError(
            "Polygon has no valid "
            "coordinates for centroid fallback"
        )

    centroid_lat = (
        sum(lat_values)
        / len(lat_values)
    )

    centroid_lon = (
        sum(lon_values)
        / len(lon_values)
    )

    return h3.latlng_to_cell(
        centroid_lat,
        centroid_lon,
        resolution,
    )


def geometry_to_h3_cells(
    geometry: dict[str, Any],
    resolution: int,
) -> list[str]:
    if not isinstance(
        geometry,
        dict,
    ):
        raise PermanentRecordError(
            "Geometry is missing or invalid"
        )

    geometry_type = geometry.get(
        "type"
    )

    coordinates = geometry.get(
        "coordinates"
    )

    if geometry_type == "Polygon":
        cells = polygon_to_h3_cells(
            coordinates,
            resolution,
        )

        if not cells:
            cells.add(
                polygon_centroid_cell(
                    coordinates,
                    resolution,
                )
            )

    elif geometry_type == "MultiPolygon":
        if not isinstance(
            coordinates,
            list,
        ):
            raise PermanentRecordError(
                "MultiPolygon coordinates "
                "are missing or invalid"
            )

        cells: set[str] = set()

        for polygon_coordinates in coordinates:
            polygon_cells = (
                polygon_to_h3_cells(
                    polygon_coordinates,
                    resolution,
                )
            )

            if not polygon_cells:
                polygon_cells.add(
                    polygon_centroid_cell(
                        polygon_coordinates,
                        resolution,
                    )
                )

            cells.update(
                polygon_cells
            )

    else:
        raise PermanentRecordError(
            "Unsupported geometry type: "
            f"{geometry_type}"
        )

    if not cells:
        raise PermanentRecordError(
            "SIGMET geometry produced zero "
            "H3 cells after centroid fallback"
        )

    return sorted(cells)


def normalize_geojson_ring(
    ring: list[Any],
) -> list[list[float]]:
    """
    Validate and normalize a GeoJSON ring while
    preserving source coordinate ordering.

    GeoJSON input order is [longitude, latitude].

    The closing coordinate is intentionally preserved
    because HazardCoordinates stores the exact ordered
    geometry records used for reconstruction.
    """

    normalized: list[list[float]] = []

    for sequence_number, point in enumerate(ring):
        if (
            not isinstance(point, (list, tuple))
            or len(point) < 2
        ):
            raise PermanentRecordError(
                "Invalid coordinate point: "
                f"sequence={sequence_number}"
            )

        longitude = point[0]
        latitude = point[1]

        if (
            isinstance(longitude, bool)
            or isinstance(latitude, bool)
            or not isinstance(longitude, (int, float))
            or not isinstance(latitude, (int, float))
        ):
            raise PermanentRecordError(
                "Coordinate latitude or longitude "
                "is not numeric"
            )

        longitude = float(longitude)
        latitude = float(latitude)

        if not -180.0 <= longitude <= 180.0:
            raise PermanentRecordError(
                f"Longitude out of range: {longitude}"
            )

        if not -90.0 <= latitude <= 90.0:
            raise PermanentRecordError(
                f"Latitude out of range: {latitude}"
            )

        normalized.append(
            [
                longitude,
                latitude,
            ]
        )

    if len(normalized) < 3:
        raise PermanentRecordError(
            "Geometry ring has fewer than three points"
        )

    return normalized


def flatten_geometry_points(
    geometry: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Convert Polygon or MultiPolygon coordinates
    into deterministic ordered coordinate rows.

    Duplicate GeoJSON closing coordinates are removed.
    """

    if not isinstance(
        geometry,
        dict,
    ):
        raise PermanentRecordError(
            "Geometry is missing or invalid"
        )

    geometry_type = geometry.get(
        "type"
    )

    coordinates = geometry.get(
        "coordinates"
    )

    if geometry_type == "Polygon":
        if not isinstance(
            coordinates,
            list,
        ):
            raise PermanentRecordError(
                "Polygon coordinates "
                "are missing or invalid"
            )

        polygons = [
            coordinates
        ]

        normalized_geometry_type = (
            "POLYGON"
        )

    elif geometry_type == "MultiPolygon":
        if not isinstance(
            coordinates,
            list,
        ):
            raise PermanentRecordError(
                "MultiPolygon coordinates "
                "are missing or invalid"
            )

        polygons = coordinates

        normalized_geometry_type = (
            "MULTIPOLYGON"
        )

    else:
        raise PermanentRecordError(
            "Unsupported geometry type: "
            f"{geometry_type}"
        )

    flattened: list[
        dict[str, Any]
    ] = []

    for (
        polygon_index,
        polygon,
    ) in enumerate(polygons):
        if (
            not isinstance(
                polygon,
                list,
            )
            or not polygon
        ):
            raise PermanentRecordError(
                f"Polygon {polygon_index} "
                "has no valid rings"
            )

        for (
            ring_index,
            ring,
        ) in enumerate(polygon):
            if not isinstance(
                ring,
                list,
            ):
                raise PermanentRecordError(
                    "Geometry ring is invalid"
                )

            normalized_ring = (
                normalize_geojson_ring(
                    ring
                )
            )

            for (
                sequence_number,
                point,
            ) in enumerate(
                normalized_ring
            ):
                longitude = point[0]
                latitude = point[1]

                flattened.append(
                    {
                        "geometry_type": (
                            normalized_geometry_type
                        ),
                        "polygon_index": (
                            polygon_index
                        ),
                        "ring_index": (
                            ring_index
                        ),
                        "sequence_number": (
                            sequence_number
                        ),
                        "latitude": (
                            latitude
                        ),
                        "longitude": (
                            longitude
                        ),
                    }
                )

    if not flattened:
        raise PermanentRecordError(
            "SIGMET geometry produced "
            "no coordinate points"
        )

    return flattened


def build_geometry_hash(
    geometry: dict[str, Any],
) -> str:
    """
    Hash deterministic normalized ordered geometry.
    """

    points = flatten_geometry_points(
        geometry
    )

    canonical_geometry = [
        {
            "geometry_type": (
                point[
                    "geometry_type"
                ]
            ),
            "polygon_index": (
                point[
                    "polygon_index"
                ]
            ),
            "ring_index": (
                point[
                    "ring_index"
                ]
            ),
            "sequence_number": (
                point[
                    "sequence_number"
                ]
            ),
            "latitude": (
                point[
                    "latitude"
                ]
            ),
            "longitude": (
                point[
                    "longitude"
                ]
            ),
        }
        for point in points
    ]

    return stable_hash(
        canonical_geometry
    )


def expand_impact_cells(
    hazard_cells: list[str],
    max_grid_distance: int,
) -> dict[str, int]:
    """
    Return:
        impact_cell -> minimum H3 grid distance
                       from an exact HazardCell.
    """

    if max_grid_distance < 0:
        raise ValueError(
            "max_grid_distance "
            "cannot be negative"
        )

    exact_cells = sorted(
        {
            str(cell).strip()
            for cell in hazard_cells
            if str(cell).strip()
        }
    )

    if not exact_cells:
        raise PermanentRecordError(
            "Cannot generate ImpactCells "
            "without HazardCells"
        )

    minimum_distances: dict[
        str,
        int,
    ] = {}

    for exact_cell in exact_cells:
        minimum_distances[
            exact_cell
        ] = 0

        for distance in range(
            1,
            max_grid_distance + 1,
        ):
            try:
                ring_cells = (
                    h3.grid_ring(
                        exact_cell,
                        distance,
                    )
                )

            except Exception as exc:
                raise PermanentRecordError(
                    "Failed to expand H3 "
                    "impact cells from "
                    f"{exact_cell} at distance "
                    f"{distance}: {exc}"
                ) from exc

            for impact_cell in ring_cells:
                current_distance = (
                    minimum_distances.get(
                        impact_cell
                    )
                )

                if (
                    current_distance
                    is None
                    or distance
                    < current_distance
                ):
                    minimum_distances[
                        impact_cell
                    ] = distance

    return dict(
        sorted(
            minimum_distances.items()
        )
    )


# ---------------------------------------------------------------------------
# Lineage / materialization
# ---------------------------------------------------------------------------

def build_raw_s3_uri(
    raw_event: dict[str, Any],
) -> str | None:
    raw_s3_bucket = raw_event.get(
        "raw_s3_bucket"
    )

    raw_s3_key = raw_event.get(
        "raw_s3_key"
    )

    if (
        raw_s3_bucket
        and raw_s3_key
    ):
        return (
            f"s3://{raw_s3_bucket}/"
            f"{raw_s3_key}"
        )

    return None


def build_correlation_id(
    raw_event: dict[str, Any],
    feature: dict[str, Any],
) -> str:
    existing = clean_string(
        raw_event.get(
            "correlation_id"
        )
    )

    if existing:
        return existing

    poll_id = clean_string(
        raw_event.get(
            "poll_id"
        )
    )

    record_index = raw_event.get(
        "record_index"
    )

    if poll_id:
        if record_index is not None:
            return (
                f"{poll_id}:"
                f"{record_index}"
            )

        return poll_id

    return (
        "sigmet-"
        f"{stable_hash(feature)[:24]}"
    )


def build_materialization_id(
    hazard_id: str,
    source_version: str,
    geometry_hash: str,
) -> str:
    fingerprint = {
        "hazard_id": hazard_id,
        "source_version": (
            source_version
        ),
        "geometry_hash": (
            geometry_hash
        ),
    }

    return (
        "hazard-materialization-"
        f"{stable_hash(fingerprint)[:24]}"
    )


def build_hazard_version_key(
    hazard_id: str,
    source_version: str,
) -> str:
    return (
        f"{hazard_id}#"
        f"{source_version}"
    )


# ---------------------------------------------------------------------------
# ActiveHazards v4 parent item
# ---------------------------------------------------------------------------

def build_active_hazard_item(
    raw_event: dict[str, Any],
    feature: dict[str, Any],
    geometry_points: list[
        dict[str, Any]
    ],
    hazard_cell_count: int,
    impact_cell_count: int,
) -> dict[str, Any]:
    properties = extract_properties(
        feature
    )

    geometry = feature.get(
        "geometry"
    )

    if not geometry_points:
        raise PermanentRecordError(
            "Cannot create ActiveHazards "
            "without geometry points"
        )

    created_at = get_issued_at(
        properties,
        raw_event,
    )

    valid_from = get_valid_from(
        properties
    )

    valid_to = get_valid_to(
        properties
    )

    received_at = parse_time(
        raw_event.get(
            "received_at"
        )
    )

    if created_at is None:
        raise PermanentRecordError(
            "SIGMET record has no valid "
            "creation/issuance time"
        )

    if valid_from is None:
        raise PermanentRecordError(
            "SIGMET record has no "
            "valid valid-from time"
        )

    if valid_to is None:
        raise PermanentRecordError(
            "SIGMET record has no "
            "valid valid-to time"
        )

    if received_at is None:
        raise PermanentRecordError(
            "SIGMET raw event has no "
            "valid received_at"
        )

    raw_s3_uri = build_raw_s3_uri(
        raw_event
    )

    if not raw_s3_uri:
        raise PermanentRecordError(
            "SIGMET raw event has no "
            "raw S3 lineage URI"
        )

    identity = extract_source_identity(
        properties
    )

    hazard_id = build_hazard_id(
        properties
    )

    source_product_id = (
        build_source_product_id(
            properties
        )
    )

    source_version = (
        build_source_version(
            feature
        )
    )

    geometry_hash = (
        build_geometry_hash(
            geometry
        )
    )

    materialization_id = (
        build_materialization_id(
            hazard_id,
            source_version,
            geometry_hash,
        )
    )

    amendment_type = (
        get_amendment_type(
            properties
        )
    )

    status = get_hazard_status(
        properties,
        valid_to,
        amendment_type,
    )

    (
        altitude_bands,
        minimum_lower_altitude_ft,
        maximum_upper_altitude_ft,
    ) = build_altitude_bands(
        properties
    )

    item: dict[str, Any] = {
        "hazard_id": (
            hazard_id
        ),
        "source_version": (
            source_version
        ),
        "source_product_id": (
            source_product_id
        ),
        "amendment_type": (
            amendment_type
        ),
        "created_at_utc": (
            created_at.isoformat()
        ),
        "valid_from_epoch": (
            int(
                valid_from.timestamp()
            )
        ),
        "valid_from_utc": (
            valid_from.isoformat()
        ),
        "valid_to_epoch": (
            int(
                valid_to.timestamp()
            )
        ),
        "valid_to_utc": (
            valid_to.isoformat()
        ),
        "product_type": (
            get_product_type(
                properties
            )
        ),
        "hazard_type": (
            get_hazard_type(
                properties
            )
        ),
        "geometry_type": (
            geometry_points[0][
                "geometry_type"
            ]
        ),
        "geometry_point_count": (
            len(
                geometry_points
            )
        ),
        "hazard_cell_count": (
            hazard_cell_count
        ),
        "impact_cell_count": (
            impact_cell_count
        ),
        "geometry_hash": (
            geometry_hash
        ),

        # The parent remains BUILDING until
        # HazardCoordinates, HazardCells and
        # ImpactCells are all migrated and
        # completeness validation is implemented.
        "materialization_status": (
            "BUILDING"
        ),
        "materialization_id": (
            materialization_id
        ),

        "status": status,
        "source_system": (
            SOURCE_SYSTEM
        ),
        "source_event_time_utc": (
            created_at.isoformat()
        ),
        "received_at_utc": (
            received_at.isoformat()
        ),
        "processed_at_utc": (
            now_utc_iso()
        ),
        "correlation_id": (
            build_correlation_id(
                raw_event,
                feature,
            )
        ),
        "raw_s3_uri": (
            raw_s3_uri
        ),
        "schema_version": (
            SCHEMA_VERSION
        ),
        "expires_at_epoch": (
            ttl_for_hazard(
                valid_to,
                status,
            )
        ),
    }

    source_icao_id = identity.get(
        "source_icao_id"
    )

    if source_icao_id:
        item[
            "source_icao_id"
        ] = source_icao_id

    series_id = identity.get(
        "series_id"
    )

    if series_id:
        item[
            "series_id"
        ] = series_id

    alpha_char = identity.get(
        "alpha_char"
    )

    if alpha_char:
        item[
            "alpha_char"
        ] = alpha_char

    receipt_time = parse_time(
        get_first_property(
            properties,
            [
                "receiptTime",
                "receipt_time",
                "receiptAt",
            ],
        )
    )

    if receipt_time:
        item[
            "receipt_time_utc"
        ] = receipt_time.isoformat()

    severity = (
        normalized_upper_or_none(
            properties.get(
                "severity"
            )
        )
    )

    if severity:
        item[
            "severity"
        ] = severity

    if altitude_bands:
        item[
            "altitude_bands"
        ] = altitude_bands

    if (
        minimum_lower_altitude_ft
        is not None
    ):
        item[
            "minimum_lower_altitude_ft"
        ] = minimum_lower_altitude_ft

    if (
        maximum_upper_altitude_ft
        is not None
    ):
        item[
            "maximum_upper_altitude_ft"
        ] = maximum_upper_altitude_ft

    movement_direction = (
        decimal_or_none(
            properties.get(
                "movementDir"
            )
        )
    )

    if movement_direction is not None:
        item[
            "movement_direction_deg"
        ] = movement_direction

    movement_speed = (
        decimal_or_none(
            properties.get(
                "movementSpd"
            )
        )
    )

    if movement_speed is not None:
        item[
            "movement_speed_kt"
        ] = movement_speed

    raw_text = get_raw_text(
        properties
    )

    if raw_text:
        item[
            "raw_text"
        ] = raw_text

    post_process_flag = (
        properties.get(
            "postProcessFlag"
        )
    )

    if post_process_flag is not None:
        if isinstance(
            post_process_flag,
            bool,
        ):
            item[
                "post_process_flag"
            ] = post_process_flag

        else:
            cleaned_flag = (
                clean_string(
                    post_process_flag
                )
            )

            if cleaned_flag:
                item[
                    "post_process_flag"
                ] = cleaned_flag

    return item


# ---------------------------------------------------------------------------
# ActiveHazards state comparison
# ---------------------------------------------------------------------------

def get_existing_hazard(
    hazard_id: str,
) -> dict[str, Any] | None:
    response = (
        active_hazards_table.get_item(
            Key={
                "hazard_id": (
                    hazard_id
                )
            }
        )
    )

    item = response.get(
        "Item"
    )

    return (
        item
        if isinstance(
            item,
            dict,
        )
        else None
    )


def determine_change_type(
    existing: dict[str, Any] | None,
    item: dict[str, Any],
) -> tuple[str, bool]:
    """
    Returns:
        change_type
        should_write_state
    """

    if existing is None:
        return (
            "NEW",
            True,
        )

    existing_source_version = (
        existing.get(
            "source_version"
        )
    )

    incoming_source_version = (
        item[
            "source_version"
        ]
    )

    if (
        existing_source_version
        == incoming_source_version
    ):
        return (
            "UNCHANGED",
            False,
        )

    existing_event_time = (
        parse_time(
            existing.get(
                "source_event_time_utc"
            )
            or existing.get(
                "created_at_utc"
            )
        )
    )

    incoming_event_time = (
        parse_time(
            item.get(
                "source_event_time_utc"
            )
        )
    )

    if (
        existing_event_time
        is not None
        and incoming_event_time
        is not None
        and incoming_event_time
        < existing_event_time
    ):
        return (
            "STALE",
            False,
        )

    return (
        "UPDATED",
        True,
    )


# ---------------------------------------------------------------------------
# Current child-table item builders
#
# These retain the existing physical key structure for now.
# They will be migrated individually to the v4 contracts next.
# ---------------------------------------------------------------------------

def build_coordinate_key(
    *,
    polygon_index: int,
    ring_index: int,
    sequence_number: int,
) -> str:
    return (
        f"P#{polygon_index:04d}#"
        f"R#{ring_index:04d}#"
        f"S#{sequence_number:06d}"
    )

def build_coordinate_items(
    active_hazard: dict[str, Any],
    geometry_points: list[dict[str, Any]],
    materialized_at_utc: str,
) -> list[dict[str, Any]]:
    hazard_id = active_hazard["hazard_id"]
    source_version = active_hazard["source_version"]

    hazard_version_key = build_hazard_version_key(
        hazard_id,
        source_version,
    )

    items: list[dict[str, Any]] = []

    for point in geometry_points:
        polygon_index = int(
            point["polygon_index"]
        )
        ring_index = int(
            point["ring_index"]
        )
        sequence_number = int(
            point["sequence_number"]
        )

        coordinate_key = build_coordinate_key(
            polygon_index=polygon_index,
            ring_index=ring_index,
            sequence_number=sequence_number,
        )

        item: dict[str, Any] = {
            "hazard_version_key": hazard_version_key,
            "coordinate_key": coordinate_key,

            "hazard_id": hazard_id,
            "source_version": source_version,

            "source_system": active_hazard.get(
                "source_system",
                SOURCE_SYSTEM
            ),

            "product_type": active_hazard.get(
                "product_type",
                "SIGMET",
            ),
            "hazard_type": active_hazard[
                "hazard_type"
            ],
            "status": active_hazard.get(
                "status",
                "ACTIVE",
            ),
            "materialization_status": active_hazard.get(
                "materialization_status",
                "BUILDING",
            ),

            "valid_from_utc": active_hazard[
                "valid_from_utc"
            ],
            "valid_to_utc": active_hazard[
                "valid_to_utc"
            ],

            "geometry_type": point[
                "geometry_type"
            ],
            "polygon_index": polygon_index,
            "ring_index": ring_index,
            "sequence_number": sequence_number,

            "latitude": Decimal(
                str(point["latitude"])
            ),
            "longitude": Decimal(
                str(point["longitude"])
            ),

            "materialization_id": active_hazard[
                "materialization_id"
            ],
            "geometry_hash": active_hazard[
                "geometry_hash"
            ],

            "created_at_utc": materialized_at_utc,

            "correlation_id": active_hazard[
                "correlation_id"
            ],

            "schema_version": (
                HAZARD_COORDINATES_SCHEMA_VERSION
            ),

            "expires_at_epoch": active_hazard[
                "expires_at_epoch"
            ],
        }

        optional_fields = [
            "source_event_time_utc",
            "processed_at_utc",
            "raw_s3_uri",
            "valid_from_epoch",
            "valid_to_epoch",
            "source_icao_id",
            "series_id",
            "alpha_char",
            "amendment_type",
            "severity",
            "minimum_lower_altitude_ft",
            "maximum_upper_altitude_ft",
            "movement_direction_deg",
            "movement_speed_kt",
        ]

        for field_name in optional_fields:
            if field_name in active_hazard:
                item[field_name] = active_hazard[
                    field_name
                ]

        items.append(item)

    if not items:
        raise PermanentRecordError(
            "Geometry produced zero HazardCoordinates rows"
        )

    return items

def build_hazard_cell_items(
    active_hazard: dict[str, Any],
    h3_cells: list[str],
    materialized_at_utc: str,
) -> list[dict[str, Any]]:
    hazard_id = active_hazard[
        "hazard_id"
    ]

    source_version = active_hazard[
        "source_version"
    ]

    hazard_version_key = (
        build_hazard_version_key(
            hazard_id,
            source_version,
        )
    )

    unique_cells = sorted(
        {
            str(cell).strip()
            for cell in h3_cells
            if str(cell).strip()
        }
    )

    if not unique_cells:
        raise PermanentRecordError(
            "Geometry produced zero HazardCells rows"
        )

    items: list[dict[str, Any]] = []

    for h3_cell in unique_cells:
        item = {
            "h3_cell": h3_cell,

            "hazard_version_key": (
                hazard_version_key
            ),

            "hazard_id": hazard_id,

            "hazard_source_version": (
                source_version
            ),

            "h3_resolution": (
                H3_RESOLUTION
            ),

            "hazard_type": active_hazard[
                "hazard_type"
            ],

            "valid_from_utc": active_hazard[
                "valid_from_utc"
            ],

            "valid_to_utc": active_hazard[
                "valid_to_utc"
            ],

            "geometry_hash": active_hazard[
                "geometry_hash"
            ],

            "materialization_id": active_hazard[
                "materialization_id"
            ],

            "created_at_utc": (
                materialized_at_utc
            ),

            "correlation_id": active_hazard[
                "correlation_id"
            ],

            "schema_version": (
                HAZARD_CELLS_SCHEMA_VERSION
            ),

            "expires_at_epoch": active_hazard[
                "expires_at_epoch"
            ],
        }

        severity = active_hazard.get(
            "severity"
        )

        if severity is not None:
            item["severity"] = severity

        items.append(item)

    return items


def build_impact_cell_items(
    active_hazard: dict[str, Any],
    impact_cells: dict[str, int],
    materialized_at_utc: str,
) -> list[dict[str, Any]]:
    hazard_id = active_hazard[
        "hazard_id"
    ]

    source_version = active_hazard[
        "source_version"
    ]

    hazard_version_key = (
        build_hazard_version_key(
            hazard_id,
            source_version,
        )
    )

    if not impact_cells:
        raise PermanentRecordError(
            "Impact expansion produced zero "
            "ImpactCells rows"
        )

    items: list[dict[str, Any]] = []

    for (
        h3_cell,
        minimum_distance,
    ) in sorted(
        impact_cells.items()
    ):
        normalized_cell = str(
            h3_cell
        ).strip()

        if not normalized_cell:
            raise PermanentRecordError(
                "ImpactCells contains an empty "
                "H3 cell"
            )

        if minimum_distance < 0:
            raise PermanentRecordError(
                "ImpactCells minimum grid "
                "distance cannot be negative"
            )

        item = {
            "h3_cell": (
                normalized_cell
            ),

            "hazard_version_key": (
                hazard_version_key
            ),

            "hazard_id": (
                hazard_id
            ),

            "hazard_source_version": (
                source_version
            ),

            "h3_resolution": (
                H3_RESOLUTION
            ),

            "minimum_grid_distance": (
                minimum_distance
            ),

            "maximum_expansion_grid_distance": (
                IMPACT_GRID_DISTANCE
            ),

            "impact_radius_nm": (
                IMPACT_RADIUS_NM
            ),

            "impact_scope": (
                "PROJECTION_TRIGGER_AREA"
            ),

            "expansion_config_version": (
                IMPACT_EXPANSION_CONFIG_VERSION
            ),

            "valid_from_utc": (
                active_hazard[
                    "valid_from_utc"
                ]
            ),

            "valid_to_utc": (
                active_hazard[
                    "valid_to_utc"
                ]
            ),

            "materialization_id": (
                active_hazard[
                    "materialization_id"
                ]
            ),

            "created_at_utc": (
                materialized_at_utc
            ),

            "correlation_id": (
                active_hazard[
                    "correlation_id"
                ]
            ),

            "schema_version": (
                IMPACT_CELLS_SCHEMA_VERSION
            ),

            "expires_at_epoch": (
                active_hazard[
                    "expires_at_epoch"
                ]
            ),
        }

        items.append(
            item
        )

    return items


# ---------------------------------------------------------------------------
# DynamoDB child writes
# ---------------------------------------------------------------------------

def batch_put_items(
    table: Any,
    *,
    overwrite_by_pkeys: list[str],
    items: list[dict[str, Any]],
) -> int:
    if not items:
        return 0

    with table.batch_writer(
        overwrite_by_pkeys=(
            overwrite_by_pkeys
        )
    ) as batch:
        for item in items:
            batch.put_item(
                Item=item
            )

    return len(items)


def materialize_dependent_rows(
    *,
    active_hazard: dict[str, Any],
    geometry_points: list[
        dict[str, Any]
    ],
    h3_cells: list[str],
    impact_cells: dict[
        str,
        int,
    ],
) -> dict[str, int]:
    materialized_at_utc = (
        now_utc_iso()
    )

    coordinate_items = (
        build_coordinate_items(
            active_hazard,
            geometry_points,
            materialized_at_utc,
        )
    )

    hazard_cell_items = (
        build_hazard_cell_items(
            active_hazard,
            h3_cells,
            materialized_at_utc,
        )
    )

    impact_cell_items = (
        build_impact_cell_items(
            active_hazard,
            impact_cells,
            materialized_at_utc,
        )
    )

    coordinates_written = batch_put_items(
        hazard_coordinates_table,
        overwrite_by_pkeys=[
            "hazard_version_key",
            "coordinate_key",
        ],
        items=coordinate_items,
    )

    hazard_cells_written = (
        batch_put_items(
            hazard_cells_table,
            overwrite_by_pkeys=[
                "h3_cell",
                "hazard_version_key",
            ],
            items=(
                hazard_cell_items
            ),
        )
    )

    impact_cells_written = (
        batch_put_items(
            impact_cells_table,
            overwrite_by_pkeys=[
                "h3_cell",
                "hazard_version_key",
            ],
            items=(
                impact_cell_items
            ),
        )
    )

    return {
        "hazard_coordinates_written": (
            coordinates_written
        ),
        "hazard_cells_written": (
            hazard_cells_written
        ),
        "impact_cells_written": (
            impact_cells_written
        ),
    }

# ---------------------------------------------------------------------------
# EventBridge publication
# ---------------------------------------------------------------------------

def publish_hazard_coordinates_materialized(
    *,
    active_hazard: dict[str, Any],
    dependent_counts: dict[str, int],
) -> int:
    hazard_id = active_hazard[
        "hazard_id"
    ]

    source_version = active_hazard[
        "source_version"
    ]

    hazard_version_key = build_hazard_version_key(
        hazard_id,
        source_version,
    )

    detail = {
        "detail-type": (
            "HazardCoordinates.materialized"
        ),
        "event_type": (
            "HazardCoordinates.materialized"
        ),
        "hazard_version_key": (
            hazard_version_key
        ),
        "hazard_id": (
            hazard_id
        ),
        "source_version": (
            source_version
        ),
        "hazard_source_version": (
            source_version
        ),
        "materialization_id": (
            active_hazard[
                "materialization_id"
            ]
        ),
        "correlation_id": (
            active_hazard[
                "correlation_id"
            ]
        ),
        "source_system": (
            active_hazard.get(
                "source_system",
                SOURCE_SYSTEM,
            )
        ),
        "product_type": (
            active_hazard.get(
                "product_type",
                "SIGMET",
            )
        ),
        "hazard_type": (
            active_hazard.get(
                "hazard_type"
            )
        ),
        "severity": (
            active_hazard.get(
                "severity"
            )
        ),
        "valid_from_utc": (
            active_hazard.get(
                "valid_from_utc"
            )
        ),
        "valid_to_utc": (
            active_hazard.get(
                "valid_to_utc"
            )
        ),
        "hazard_coordinates_written": (
            dependent_counts.get(
                "hazard_coordinates_written",
                0,
            )
        ),
        "hazard_cells_written": (
            dependent_counts.get(
                "hazard_cells_written",
                0,
            )
        ),
        "impact_cells_written": (
            dependent_counts.get(
                "impact_cells_written",
                0,
            )
        ),
        "published_at_utc": (
            now_utc_iso()
        ),
    }

    response = events_client.put_events(
        Entries=[
            {
                "Source": (
                    "wilvor.weather"
                ),
                "DetailType": (
                    "HazardCoordinates.materialized"
                ),
                "Detail": json.dumps(
                    detail,
                    default=json_default,
                    separators=(",", ":"),
                ),
                "EventBusName": (
                    EVENT_BUS_NAME
                ),
            }
        ]
    )

    failed_count = int(
        response.get(
            "FailedEntryCount",
            0,
        )
        or 0
    )

    if failed_count:
        raise RuntimeError(
            "Failed to publish "
            "HazardCoordinates.materialized "
            f"event: {response}"
        )

    return len(
        response.get(
            "Entries",
            [],
        )
    )


# ---------------------------------------------------------------------------
# Record processor
# ---------------------------------------------------------------------------

def process_decoded_record(
    raw_event: dict[str, Any],
) -> dict[str, int]:
    feature = extract_feature(
        raw_event
    )

    geometry = feature.get(
        "geometry"
    )

    geometry_points = (
        flatten_geometry_points(
            geometry
        )
    )

    h3_cells = (
        geometry_to_h3_cells(
            geometry,
            H3_RESOLUTION,
        )
    )

    impact_cells = (
        expand_impact_cells(
            h3_cells,
            IMPACT_GRID_DISTANCE,
        )
    )

    item = (
        build_active_hazard_item(
            raw_event,
            feature,
            geometry_points,
            hazard_cell_count=(
                len(h3_cells)
            ),
            impact_cell_count=(
                len(impact_cells)
            ),
        )
    )

    existing = (
        get_existing_hazard(
            item[
                "hazard_id"
            ]
        )
    )

    (
        change_type,
        should_write_state,
    ) = determine_change_type(
        existing,
        item,
    )

    result = {
        "active_hazards_written": 0,
        "hazard_coordinates_written": 0,
        "hazard_cells_written": 0,
        "impact_cells_written": 0,

        # No EventBridge event until the full v4
        # child materialization reaches READY.
        "eventbridge_events_published": 0,

        "new_records": (
            1
            if change_type == "NEW"
            else 0
        ),
        "updated_records": (
            1
            if change_type == "UPDATED"
            else 0
        ),
        "unchanged_records": (
            1
            if change_type == "UNCHANGED"
            else 0
        ),
        "stale_records": (
            1
            if change_type == "STALE"
            else 0
        ),
    }

    if should_write_state:
        dependent_counts = (
            materialize_dependent_rows(
                active_hazard=item,
                geometry_points=(
                    geometry_points
                ),
                h3_cells=(
                    h3_cells
                ),
                impact_cells=(
                    impact_cells
                ),
            )
        )

        result.update(
            dependent_counts
        )

        result[
            "eventbridge_events_published"
        ] = publish_hazard_coordinates_materialized(
            active_hazard=item,
            dependent_counts=dependent_counts,
        )

        active_hazards_table.put_item(
            Item=item
        )

        result[
            "active_hazards_written"
        ] = 1

    return result


# ---------------------------------------------------------------------------
# Bad-record handling
# ---------------------------------------------------------------------------

def get_record_sequence_number(
    record: dict[str, Any],
) -> str | None:
    return (
        record.get(
            "kinesis",
            {},
        )
        .get(
            "sequenceNumber"
        )
    )


def get_record_arrival_timestamp(
    record: dict[str, Any],
) -> Any:
    return (
        record.get(
            "kinesis",
            {},
        )
        .get(
            "approximateArrivalTimestamp"
        )
    )


def get_record_base64(
    record: dict[str, Any],
) -> str | None:
    value = (
        record.get(
            "kinesis",
            {},
        )
        .get(
            "data"
        )
    )

    return (
        str(value)
        if value is not None
        else None
    )


def write_bad_record(
    *,
    record: dict[str, Any],
    error_type: str,
    error_message: str,
    decoded_payload: (
        dict[str, Any]
        | None
    ),
    raw_base64: str | None,
) -> str:
    if not BAD_RECORDS_BUCKET_NAME:
        raise RuntimeError(
            "BAD_RECORDS_BUCKET_NAME "
            "is not configured"
        )

    received_at_dt = now_utc()

    sequence_number = (
        get_record_sequence_number(
            record
        )
    )

    bad_record = {
        "schema_version": (
            "bad_record.v1"
        ),
        "service": (
            "sigmet_processor"
        ),
        "error_type": (
            error_type
        ),
        "error_message": (
            error_message
        ),
        "sequence_number": (
            sequence_number
        ),
        "approximate_arrival_timestamp": (
            get_record_arrival_timestamp(
                record
            )
        ),
        "record_received_at": (
            received_at_dt.isoformat()
        ),
        "decoded_payload": (
            decoded_payload
        ),
        "raw_base64": (
            raw_base64
            if decoded_payload is None
            else None
        ),
    }

    sequence_part = (
        sequence_number
        or stable_hash(
            raw_base64
            or bad_record
        )[:24]
    )

    key = (
        f"{BAD_RECORDS_PREFIX.rstrip('/')}/"
        f"year={received_at_dt.year:04d}/"
        f"month={received_at_dt.month:02d}/"
        f"day={received_at_dt.day:02d}/"
        f"hour={received_at_dt.hour:02d}/"
        f"{received_at_dt.strftime('%Y%m%dT%H%M%S%f')}-"
        f"{sequence_part}.json"
    )

    s3.put_object(
        Bucket=(
            BAD_RECORDS_BUCKET_NAME
        ),
        Key=key,
        Body=json.dumps(
            bad_record,
            separators=(",", ":"),
            default=json_default,
        ).encode(
            "utf-8"
        ),
        ContentType=(
            "application/json"
        ),
    )

    return (
        f"s3://"
        f"{BAD_RECORDS_BUCKET_NAME}/"
        f"{key}"
    )


# ---------------------------------------------------------------------------
# Lambda
# ---------------------------------------------------------------------------

def lambda_handler(
    event,
    context,
):
    records = event.get(
        "Records",
        [],
    )

    records_received = len(
        records
    )

    records_processed = 0
    records_failed = 0
    bad_records_written = 0

    active_hazards_written = 0
    hazard_coordinates_written = 0
    hazard_cells_written = 0
    impact_cells_written = 0
    eventbridge_events_published = 0

    new_records = 0
    updated_records = 0
    unchanged_records = 0
    stale_records = 0

    batch_item_failures = []

    for record in records:
        sequence_number = (
            get_record_sequence_number(
                record
            )
        )

        decoded_payload = None

        raw_base64 = (
            get_record_base64(
                record
            )
        )

        try:
            decoded_payload = (
                decode_kinesis_record(
                    record
                )
            )

            result = (
                process_decoded_record(
                    decoded_payload
                )
            )

            records_processed += 1

            active_hazards_written += (
                result[
                    "active_hazards_written"
                ]
            )

            hazard_coordinates_written += (
                result[
                    "hazard_coordinates_written"
                ]
            )

            hazard_cells_written += (
                result[
                    "hazard_cells_written"
                ]
            )

            impact_cells_written += (
                result[
                    "impact_cells_written"
                ]
            )

            eventbridge_events_published += (
                result[
                    "eventbridge_events_published"
                ]
            )

            new_records += (
                result[
                    "new_records"
                ]
            )

            updated_records += (
                result[
                    "updated_records"
                ]
            )

            unchanged_records += (
                result[
                    "unchanged_records"
                ]
            )

            stale_records += (
                result[
                    "stale_records"
                ]
            )

        except PermanentRecordError as exc:
            try:
                bad_record_uri = (
                    write_bad_record(
                        record=record,
                        error_type=(
                            exc.__class__.__name__
                        ),
                        error_message=(
                            str(exc)
                        ),
                        decoded_payload=(
                            decoded_payload
                        ),
                        raw_base64=(
                            raw_base64
                        ),
                    )
                )

                bad_records_written += 1
                records_processed += 1

                log_event(
                    (
                        "Permanent SIGMET "
                        "record failure "
                        "written to S3"
                    ),
                    error_type=(
                        exc.__class__.__name__
                    ),
                    sequence_number=(
                        sequence_number
                    ),
                    bad_record_uri=(
                        bad_record_uri
                    ),
                )

            except Exception as quarantine_exc:
                records_failed += 1

                log_event(
                    (
                        "Failed to write "
                        "permanent SIGMET "
                        "failure to S3"
                    ),
                    error_type=(
                        quarantine_exc
                        .__class__
                        .__name__
                    ),
                    sequence_number=(
                        sequence_number
                    ),
                    error=(
                        str(
                            quarantine_exc
                        )
                    ),
                )

                if sequence_number:
                    batch_item_failures.append(
                        {
                            "itemIdentifier": (
                                sequence_number
                            )
                        }
                    )

        except (
            ClientError,
            BotoCoreError,
            RuntimeError,
        ) as exc:
            records_failed += 1

            log_event(
                (
                    "Temporary SIGMET "
                    "processor failure"
                ),
                error_type=(
                    exc.__class__.__name__
                ),
                sequence_number=(
                    sequence_number
                ),
                error=str(exc),
            )

            if sequence_number:
                batch_item_failures.append(
                    {
                        "itemIdentifier": (
                            sequence_number
                        )
                    }
                )

        except Exception as exc:
            records_failed += 1

            log_event(
                (
                    "Unexpected SIGMET "
                    "processor failure"
                ),
                error_type=(
                    exc.__class__.__name__
                ),
                sequence_number=(
                    sequence_number
                ),
                error=str(exc),
            )

            if sequence_number:
                batch_item_failures.append(
                    {
                        "itemIdentifier": (
                            sequence_number
                        )
                    }
                )

    batch_item_failures_count = (
        len(
            batch_item_failures
        )
    )

    emit_metric(
        pipeline="sigmet",
        component=(
            "sigmet_processor"
        ),
        stage="raw_to_state",
        metrics={
            "RecordsReceived": (
                records_received
            ),
            "RecordsProcessed": (
                records_processed
            ),
            "RecordsFailed": (
                records_failed
            ),
            "BadRecordsWritten": (
                bad_records_written
            ),
            "ActiveHazardsWritten": (
                active_hazards_written
            ),
            "HazardCoordinatesWritten": (
                hazard_coordinates_written
            ),
            "HazardCellsWritten": (
                hazard_cells_written
            ),
            "ImpactCellsWritten": (
                impact_cells_written
            ),
            "EventBridgeEventsPublished": (
                eventbridge_events_published
            ),
            "NewRecords": (
                new_records
            ),
            "UpdatedRecords": (
                updated_records
            ),
            "UnchangedRecords": (
                unchanged_records
            ),
            "StaleRecords": (
                stale_records
            ),
            "BatchItemFailures": (
                batch_item_failures_count
            ),
        },
        properties={
            "H3Resolution": (
                H3_RESOLUTION
            ),
        },
    )

    log_event(
        "SIGMET processor completed",
        records_received=(
            records_received
        ),
        records_processed=(
            records_processed
        ),
        records_failed=(
            records_failed
        ),
        bad_records_written=(
            bad_records_written
        ),
        active_hazards_written=(
            active_hazards_written
        ),
        hazard_coordinates_written=(
            hazard_coordinates_written
        ),
        hazard_cells_written=(
            hazard_cells_written
        ),
        impact_cells_written=(
            impact_cells_written
        ),
        eventbridge_events_published=(
            eventbridge_events_published
        ),
        new_records=(
            new_records
        ),
        updated_records=(
            updated_records
        ),
        unchanged_records=(
            unchanged_records
        ),
        stale_records=(
            stale_records
        ),
        batch_item_failures=(
            batch_item_failures_count
        ),
        h3_resolution=(
            H3_RESOLUTION
        ),
    )

    return {
        "batchItemFailures": (
            batch_item_failures
        )
    }