from __future__ import annotations

import gzip
import json
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable
from urllib.request import Request, urlopen

import boto3
import h3

SCHEMA_VERSION = os.getenv("SCHEMA_VERSION", "station-reference-v1")
SOURCE_SYSTEM = "NOAA_AVIATIONWEATHER_STATION_CACHE"
DEFAULT_URL = "https://aviationweather.gov/data/cache/stations.cache.json.gz"

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
events = boto3.client("events")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _compact_date(dt: datetime) -> dict[str, str]:
    return {
        "year": f"{dt.year:04d}",
        "month": f"{dt.month:02d}",
        "day": f"{dt.day:02d}",
        "hour": f"{dt.hour:02d}",
    }


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _as_decimal(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _as_decimal(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_as_decimal(v) for v in value if v is not None]
    return value


def _clean_item(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _as_decimal(v) for k, v in item.items() if v is not None}


def _latlng_to_h3(lat: float, lon: float, resolution: int) -> str:
    # h3-py 4.x uses latlng_to_cell. Keep a fallback for older local environments.
    if hasattr(h3, "latlng_to_cell"):
        return h3.latlng_to_cell(lat, lon, resolution)
    return h3.geo_to_h3(lat, lon, resolution)  # type: ignore[attr-defined]


def _fetch_station_cache(url: str, timeout_seconds: int) -> tuple[bytes, dict[str, str]]:
    request = Request(
        url,
        headers={
            "User-Agent": "Wilvor-StationReferenceLoader/1.0",
            "Accept": "application/json, application/gzip, */*",
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read()
        headers = {k.lower(): v for k, v in response.headers.items()}
    return raw, headers


def _decode_json(raw: bytes, source_url: str) -> Any:
    is_gzip = source_url.endswith(".gz") or raw[:2] == b"\x1f\x8b"
    payload = gzip.decompress(raw) if is_gzip else raw
    return json.loads(payload.decode("utf-8"))


def _iter_records(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        for record in payload:
            if isinstance(record, dict):
                yield record
        return

    if isinstance(payload, dict):
        features = payload.get("features")
        if isinstance(features, list):
            for feature in features:
                if not isinstance(feature, dict):
                    continue
                props = feature.get("properties") or {}
                geometry = feature.get("geometry") or {}
                if isinstance(props, dict):
                    record = dict(props)
                    coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
                    if isinstance(coords, list) and len(coords) >= 2:
                        record.setdefault("lon", coords[0])
                        record.setdefault("lat", coords[1])
                    yield record
            return

        for key in ("stations", "data", "items"):
            values = payload.get(key)
            if isinstance(values, list):
                for record in values:
                    if isinstance(record, dict):
                        yield record
                return


def _first(record: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = record.get(name)
        if value not in (None, ""):
            return value
    return None


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_upper(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value).strip().upper()


def _normalize_station(
    record: dict[str, Any],
    *,
    h3_resolution: int,
    source_version: str,
    raw_s3_uri: str,
    updated_at_utc: str,
    correlation_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    station_id = _to_upper(_first(record, "icaoId", "icao_id", "station_id", "stationId", "id", "ident"))
    if not station_id:
        return None, "missing_station_id"

    lat = _to_float(_first(record, "lat", "latitude"))
    lon = _to_float(_first(record, "lon", "longitude"))
    if lat is None or lon is None:
        return None, "missing_coordinates"
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, "invalid_coordinates"

    station_name = _first(record, "site", "name", "station_name", "stationName")
    station_type = _first(record, "type", "station_type", "stationType") or "AVIATION_WEATHER_STATION"
    iata_code = _to_upper(_first(record, "iataId", "iata_code", "iata"))
    faa_lid = _to_upper(_first(record, "faaId", "faa_lid", "lid"))
    country_code = _to_upper(_first(record, "country", "country_code", "countryCode"))
    elevation_m = _to_float(_first(record, "elev", "elevation_m", "elevationM"))

    # This flag is identity-only. Diversion suitability will come from AirportReference/RunwayReference.
    is_airport = bool(iata_code or faa_lid or (station_id and len(station_id) == 4))
    airport_id = station_id if is_airport and len(station_id) == 4 else None

    item = {
        "station_id": station_id,
        "station_name": str(station_name).strip() if station_name else None,
        "station_type": str(station_type).strip() if station_type else None,
        "is_airport": is_airport,
        "airport_id": airport_id,
        "iata_code": iata_code,
        "faa_lid": faa_lid,
        "country_code": country_code,
        "latitude": lat,
        "longitude": lon,
        "elevation_m": elevation_m,
        "h3_cell": _latlng_to_h3(lat, lon, h3_resolution),
        "h3_resolution": h3_resolution,
        "active": True,
        "source_system": SOURCE_SYSTEM,
        "source_version": source_version,
        "raw_s3_uri": raw_s3_uri,
        "updated_at_utc": updated_at_utc,
        "correlation_id": correlation_id,
        "schema_version": SCHEMA_VERSION,
    }
    return _clean_item(item), None


def _archive_raw(bucket: str, prefix: str, raw: bytes, source_url: str, source_version: str, correlation_id: str, now: datetime) -> str:
    parts = _compact_date(now)
    key = (
        f"{prefix}/year={parts['year']}/month={parts['month']}/day={parts['day']}/hour={parts['hour']}/"
        f"station-reference-{source_version}-{correlation_id}.json.gz"
    )
    body = raw if raw[:2] == b"\x1f\x8b" else gzip.compress(raw)
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
        ContentEncoding="gzip",
        Metadata={"source_url": source_url, "source_version": source_version},
    )
    return f"s3://{bucket}/{key}"


def _archive_bad(bucket: str, prefix: str, failures: list[dict[str, Any]], source_version: str, correlation_id: str, now: datetime) -> str | None:
    if not failures:
        return None
    parts = _compact_date(now)
    key = (
        f"{prefix}/year={parts['year']}/month={parts['month']}/day={parts['day']}/hour={parts['hour']}/"
        f"station-reference-bad-{source_version}-{correlation_id}.json.gz"
    )
    body = gzip.compress(json.dumps(failures, default=str).encode("utf-8"))
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
        ContentEncoding="gzip",
    )
    return f"s3://{bucket}/{key}"


def _write_items(table_name: str, items: list[dict[str, Any]]) -> None:
    table = dynamodb.Table(table_name)
    with table.batch_writer(overwrite_by_pkeys=["station_id"]) as batch:
        for item in items:
            batch.put_item(Item=item)


def _publish_event(event_bus_name: str, detail: dict[str, Any]) -> None:
    events.put_events(
        Entries=[
            {
                "Source": "wilvor.reference.station",
                "DetailType": "station.reference.updated",
                "EventBusName": event_bus_name,
                "Detail": json.dumps(detail, default=str),
            }
        ]
    )


def lambda_handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    started = time.perf_counter()
    event = event or {}
    now = _utc_now()
    updated_at_utc = _iso(now)
    correlation_id = str(event.get("correlation_id") or uuid.uuid4())

    table_name = _required_env("STATION_REFERENCE_TABLE_NAME")
    bucket = _required_env("ARCHIVE_BUCKET_NAME")
    source_url = str(event.get("station_cache_url") or os.getenv("STATION_CACHE_URL") or DEFAULT_URL)
    event_bus_name = os.getenv("EVENT_BUS_NAME", "default")
    raw_prefix = os.getenv("RAW_PREFIX", "raw/source=aviation-weather-stations")
    bad_prefix = os.getenv("BAD_PREFIX", "bad/source=aviation-weather-stations")
    h3_resolution = int(event.get("h3_resolution") or os.getenv("STATION_H3_RESOLUTION", "4"))
    timeout_seconds = int(os.getenv("HTTP_TIMEOUT_SECONDS", "60"))

    raw, headers = _fetch_station_cache(source_url, timeout_seconds)
    source_version = str(
        event.get("source_version")
        or os.getenv("SOURCE_VERSION")
        or headers.get("last-modified")
        or now.strftime("%Y-%m-%d")
    ).replace(" ", "_").replace(":", "-")

    raw_s3_uri = _archive_raw(bucket, raw_prefix, raw, source_url, source_version, correlation_id, now)
    payload = _decode_json(raw, source_url)

    valid_items: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for record in _iter_records(payload):
        item, reason = _normalize_station(
            record,
            h3_resolution=h3_resolution,
            source_version=source_version,
            raw_s3_uri=raw_s3_uri,
            updated_at_utc=updated_at_utc,
            correlation_id=correlation_id,
        )
        if item:
            valid_items.append(item)
        else:
            failures.append({"reason": reason, "record": record})

    if not valid_items:
        bad_s3_uri = _archive_bad(bucket, bad_prefix, failures, source_version, correlation_id, now)
        raise RuntimeError(
            f"Station reference source produced zero valid station records. bad_s3_uri={bad_s3_uri}"
        )

    _write_items(table_name, valid_items)
    bad_s3_uri = _archive_bad(bucket, bad_prefix, failures, source_version, correlation_id, now)

    detail = {
        "correlation_id": correlation_id,
        "schema_version": SCHEMA_VERSION,
        "source_system": SOURCE_SYSTEM,
        "source_version": source_version,
        "station_count": len(valid_items),
        "bad_record_count": len(failures),
        "raw_s3_uri": raw_s3_uri,
        "bad_s3_uri": bad_s3_uri,
        "table_name": table_name,
        "h3_resolution": h3_resolution,
        "updated_at_utc": updated_at_utc,
    }
    _publish_event(event_bus_name, detail)

    return {
        "ok": True,
        **detail,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
