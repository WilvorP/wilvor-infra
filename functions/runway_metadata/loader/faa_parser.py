from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from .models import (
        AirportReference,
        NormalizedRunway,
        ParseResult,
        PhysicalRunway,
        RejectedRecord,
        RunwayEnd,
    )
    from .validation import (
        RecordValidationError,
        canonical_physical_runway_id,
        canonical_runway_end_id,
        clean_string,
        expected_end_ids,
        parse_optional_float,
        parse_optional_int,
        require_positive_int,
        validate_normalized_runway,
    )
except ImportError:  # Lambda ZIP places modules at the package root.
    from models import (
        AirportReference,
        NormalizedRunway,
        ParseResult,
        PhysicalRunway,
        RejectedRecord,
        RunwayEnd,
    )
    from validation import (
        RecordValidationError,
        canonical_physical_runway_id,
        canonical_runway_end_id,
        clean_string,
        expected_end_ids,
        parse_optional_float,
        parse_optional_int,
        require_positive_int,
        validate_normalized_runway,
    )

APT_BASE_FILE = "APT_BASE.csv"
APT_RUNWAY_FILE = "APT_RWY.csv"
APT_RUNWAY_END_FILE = "APT_RWY_END.csv"
REQUIRED_FILES = (APT_BASE_FILE, APT_RUNWAY_FILE, APT_RUNWAY_END_FILE)


def normalized_row(row: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(key).strip().upper(): str(value).strip() if value is not None else ""
        for key, value in row.items()
        if key is not None
    }


def get_field(row: Mapping[str, str], *aliases: str) -> str | None:
    for alias in aliases:
        value = row.get(alias.upper())
        if value is not None and value.strip():
            return value.strip()
    return None


def read_csv_text(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise RecordValidationError("CSV file does not contain a header row")
    return [normalized_row(row) for row in reader]


def read_csv_file(path: str | os.PathLike[str]) -> list[dict[str, str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return [normalized_row(row) for row in csv.DictReader(handle)]


def _find_zip_member(archive: zipfile.ZipFile, expected_basename: str) -> str:
    matches = [
        member
        for member in archive.namelist()
        if Path(member).name.upper() == expected_basename.upper() and not member.endswith("/")
    ]
    if len(matches) != 1:
        raise RecordValidationError(
            f"expected exactly one {expected_basename} in FAA ZIP; found {len(matches)}"
        )
    return matches[0]


def read_required_csvs_from_zip(
    zip_path: str | os.PathLike[str],
) -> dict[str, list[dict[str, str]]]:
    with zipfile.ZipFile(zip_path) as archive:
        result: dict[str, list[dict[str, str]]] = {}
        for filename in REQUIRED_FILES:
            member = _find_zip_member(archive, filename)
            raw_bytes = archive.read(member)
            try:
                text = raw_bytes.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise RecordValidationError(f"{filename} is not valid UTF-8 CSV") from exc
            result[filename] = read_csv_text(text)
        return result


def read_required_csvs_from_directory(
    directory: str | os.PathLike[str],
) -> dict[str, list[dict[str, str]]]:
    root = Path(directory)
    return {filename: read_csv_file(root / filename) for filename in REQUIRED_FILES}


def build_airport_lookup(
    rows: Sequence[Mapping[str, str]],
) -> tuple[dict[str, AirportReference], list[RejectedRecord]]:
    lookup: dict[str, AirportReference] = {}
    rejected: list[RejectedRecord] = []
    for row_number, source_row in enumerate(rows, start=2):
        row = normalized_row(source_row)
        faa_id = get_field(row, "ARPT_ID", "FAA_ID", "LOC_ID")
        icao_id = get_field(row, "ICAO_ID", "ICAO_CODE")
        if not faa_id:
            rejected.append(
                RejectedRecord(APT_BASE_FILE, "airport row is missing ARPT_ID", row_number, row)
            )
            continue
        if not icao_id:
            # Many valid small FAA facilities have no ICAO code. They are unusable for
            # Wilvor's ICAO-keyed weather joins, so they are intentionally omitted rather
            # than treated as a failed runway load.
            continue
        faa_id = faa_id.upper()
        icao_id = icao_id.upper()
        reference = AirportReference(
            faa_id=faa_id,
            icao_id=icao_id,
            site_no=get_field(row, "SITE_NO"),
            airport_name=get_field(row, "ARPT_NAME", "FACILITY_NAME"),
            airport_status=get_field(row, "ARPT_STATUS", "FAC_STATUS"),
        )
        existing = lookup.get(faa_id)
        if existing and existing.icao_id != icao_id:
            rejected.append(
                RejectedRecord(
                    APT_BASE_FILE,
                    f"FAA identifier {faa_id} maps to multiple ICAO identifiers",
                    row_number,
                    row,
                )
            )
            continue
        lookup[faa_id] = reference
    return lookup, rejected


def _runway_join_key(row: Mapping[str, str]) -> tuple[str, str]:
    site_no = get_field(row, "SITE_NO")
    faa_id = get_field(row, "ARPT_ID", "FAA_ID", "LOC_ID")
    runway_id = canonical_physical_runway_id(get_field(row, "RWY_ID", "RUNWAY_ID"))
    airport_key = (site_no or faa_id or "").upper()
    if not airport_key:
        raise RecordValidationError("runway row is missing both SITE_NO and ARPT_ID")
    return airport_key, runway_id


def parse_physical_runways(
    rows: Sequence[Mapping[str, str]],
) -> tuple[dict[tuple[str, str], PhysicalRunway], list[RejectedRecord]]:
    runways: dict[tuple[str, str], PhysicalRunway] = {}
    rejected: list[RejectedRecord] = []
    for row_number, source_row in enumerate(rows, start=2):
        row = normalized_row(source_row)
        try:
            join_key = _runway_join_key(row)
            faa_id = get_field(row, "ARPT_ID", "FAA_ID", "LOC_ID")
            if not faa_id:
                raise RecordValidationError("physical runway row is missing ARPT_ID")
            width = parse_optional_int(get_field(row, "RWY_WIDTH", "RUNWAY_WIDTH"), "RWY_WIDTH")
            if width is not None and width <= 0:
                raise RecordValidationError("RWY_WIDTH must be greater than zero when provided")
            runway = PhysicalRunway(
                site_no=get_field(row, "SITE_NO"),
                faa_id=faa_id.upper(),
                physical_runway_id=join_key[1],
                length_ft=require_positive_int(
                    get_field(row, "RWY_LEN", "RUNWAY_LENGTH"), "RWY_LEN"
                ),
                width_ft=width,
                surface_type=get_field(row, "SURFACE_TYPE_CODE", "SURFACE_TYPE"),
                surface_condition=get_field(row, "COND", "SURFACE_CONDITION"),
                lighting_code=get_field(row, "RWY_LGT_CODE", "LIGHTING_CODE"),
            )
            if join_key in runways:
                raise RecordValidationError(f"duplicate physical runway row for {join_key}")
            runways[join_key] = runway
        except RecordValidationError as exc:
            rejected.append(RejectedRecord(APT_RUNWAY_FILE, str(exc), row_number, row))
    return runways, rejected


def parse_runway_ends(
    rows: Sequence[Mapping[str, str]],
) -> tuple[dict[tuple[str, str], list[RunwayEnd]], list[RejectedRecord]]:
    ends: dict[tuple[str, str], list[RunwayEnd]] = defaultdict(list)
    rejected: list[RejectedRecord] = []
    for row_number, source_row in enumerate(rows, start=2):
        row = normalized_row(source_row)
        try:
            join_key = _runway_join_key(row)
            runway_end = RunwayEnd(
                runway_end_id=canonical_runway_end_id(
                    get_field(row, "RWY_END_ID", "RUNWAY_END_ID")
                ),
                true_heading_deg=parse_optional_float(
                    get_field(row, "TRUE_ALIGNMENT", "TRUE_HEADING", "HEADING"),
                    "TRUE_ALIGNMENT",
                ),
                latitude=parse_optional_float(
                    get_field(row, "LAT_DECIMAL", "LATITUDE", "LAT"), "LAT_DECIMAL"
                ),
                longitude=parse_optional_float(
                    get_field(row, "LONG_DECIMAL", "LONGITUDE", "LON"), "LONG_DECIMAL"
                ),
                elevation_ft=parse_optional_float(
                    get_field(row, "RWY_END_ELEV", "RUNWAY_END_ELEVATION", "ELEV"),
                    "RWY_END_ELEV",
                ),
                landing_distance_available_ft=parse_optional_int(
                    get_field(row, "LNDG_DIST_AVBL", "LDA"), "LNDG_DIST_AVBL"
                ),
                takeoff_run_available_ft=parse_optional_int(
                    get_field(row, "TKOF_RUN_AVBL", "TORA"), "TKOF_RUN_AVBL"
                ),
                takeoff_distance_available_ft=parse_optional_int(
                    get_field(row, "TKOF_DIST_AVBL", "TODA"), "TKOF_DIST_AVBL"
                ),
                accelerate_stop_distance_available_ft=parse_optional_int(
                    get_field(row, "ACLT_STOP_DIST_AVBL", "ASDA"),
                    "ACLT_STOP_DIST_AVBL",
                ),
            )
            if any(existing.runway_end_id == runway_end.runway_end_id for existing in ends[join_key]):
                raise RecordValidationError(
                    f"duplicate runway-end row {runway_end.runway_end_id} for {join_key}"
                )
            ends[join_key].append(runway_end)
        except RecordValidationError as exc:
            rejected.append(RejectedRecord(APT_RUNWAY_END_FILE, str(exc), row_number, row))
    return dict(ends), rejected


def stable_hash(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _ordered_ends(runway_id: str, ends: Iterable[RunwayEnd]) -> tuple[RunwayEnd, RunwayEnd | None]:
    by_id = {end.runway_end_id: end for end in ends}
    expected = expected_end_ids(runway_id)
    missing = [end_id for end_id in expected if end_id not in by_id]
    extra = [end_id for end_id in by_id if end_id not in expected]
    if missing:
        raise RecordValidationError(
            f"physical runway {runway_id} is missing runway-end records: {', '.join(missing)}"
        )
    if extra:
        raise RecordValidationError(
            f"physical runway {runway_id} has unrelated runway ends: {', '.join(extra)}"
        )
    first = by_id[expected[0]]
    second = by_id[expected[1]] if len(expected) == 2 else None
    return first, second


def join_and_normalize(
    *,
    airport_lookup: Mapping[str, AirportReference],
    physical_runways: Mapping[tuple[str, str], PhysicalRunway],
    runway_ends: Mapping[tuple[str, str], Sequence[RunwayEnd]],
    supported_airport_ids: set[str] | None = None,
) -> tuple[list[NormalizedRunway], list[RejectedRecord]]:
    supported = {airport.upper() for airport in supported_airport_ids or set()}
    normalized: list[NormalizedRunway] = []
    rejected: list[RejectedRecord] = []

    for join_key, physical in physical_runways.items():
        airport = airport_lookup.get(physical.faa_id)
        if airport is None:
            rejected.append(
                RejectedRecord(
                    APT_RUNWAY_FILE,
                    f"FAA airport {physical.faa_id} has no ICAO mapping",
                    raw_record={
                        "ARPT_ID": physical.faa_id,
                        "RWY_ID": physical.physical_runway_id,
                    },
                )
            )
            continue
        if supported and airport.icao_id not in supported:
            continue
        try:
            end_1, end_2 = _ordered_ends(
                physical.physical_runway_id, runway_ends.get(join_key, [])
            )
            runway = NormalizedRunway(
                airport_id=airport.icao_id,
                faa_id=physical.faa_id,
                physical_runway_id=physical.physical_runway_id,
                length_ft=physical.length_ft,
                width_ft=physical.width_ft,
                surface_type=physical.surface_type,
                surface_condition=physical.surface_condition,
                lighting_code=physical.lighting_code,
                end_1=end_1,
                end_2=end_2,
            )
            validate_normalized_runway(runway)
            source_hash = stable_hash(runway.material_dict())
            normalized.append(
                NormalizedRunway(
                    **{
                        **runway.__dict__,
                        "source_record_hash": source_hash,
                    }
                )
            )
        except RecordValidationError as exc:
            rejected.append(
                RejectedRecord(
                    APT_RUNWAY_FILE,
                    str(exc),
                    raw_record={
                        "ARPT_ID": physical.faa_id,
                        "RWY_ID": physical.physical_runway_id,
                    },
                )
            )

    normalized.sort(key=lambda item: (item.airport_id, item.record_id))
    return normalized, rejected


def parse_faa_rows(
    *,
    airport_rows: Sequence[Mapping[str, str]],
    runway_rows: Sequence[Mapping[str, str]],
    runway_end_rows: Sequence[Mapping[str, str]],
    supported_airport_ids: set[str] | None = None,
) -> ParseResult:
    airport_lookup, airport_rejected = build_airport_lookup(airport_rows)
    physical_runways, runway_rejected = parse_physical_runways(runway_rows)
    runway_ends, end_rejected = parse_runway_ends(runway_end_rows)
    runways, join_rejected = join_and_normalize(
        airport_lookup=airport_lookup,
        physical_runways=physical_runways,
        runway_ends=runway_ends,
        supported_airport_ids=supported_airport_ids,
    )
    return ParseResult(
        runways=runways,
        rejected_records=[
            *airport_rejected,
            *runway_rejected,
            *end_rejected,
            *join_rejected,
        ],
        airport_references=airport_lookup,
    )


def parse_faa_directory(
    directory: str | os.PathLike[str],
    *,
    supported_airport_ids: set[str] | None = None,
) -> ParseResult:
    data = read_required_csvs_from_directory(directory)
    return parse_faa_rows(
        airport_rows=data[APT_BASE_FILE],
        runway_rows=data[APT_RUNWAY_FILE],
        runway_end_rows=data[APT_RUNWAY_END_FILE],
        supported_airport_ids=supported_airport_ids,
    )


def parse_faa_zip(
    zip_path: str | os.PathLike[str],
    *,
    supported_airport_ids: set[str] | None = None,
) -> ParseResult:
    data = read_required_csvs_from_zip(zip_path)
    return parse_faa_rows(
        airport_rows=data[APT_BASE_FILE],
        runway_rows=data[APT_RUNWAY_FILE],
        runway_end_rows=data[APT_RUNWAY_END_FILE],
        supported_airport_ids=supported_airport_ids,
    )
