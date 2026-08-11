from __future__ import annotations

import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Any


REQUIRED_ACTIVE_HAZARDS_V4_FIELDS = {
    "hazard_id",
    "source_version",
    "source_product_id",
    "amendment_type",
    "created_at_utc",
    "valid_from_epoch",
    "valid_from_utc",
    "valid_to_epoch",
    "valid_to_utc",
    "product_type",
    "hazard_type",
    "geometry_type",
    "geometry_point_count",
    "hazard_cell_count",
    "impact_cell_count",
    "geometry_hash",
    "materialization_status",
    "materialization_id",
    "status",
    "source_system",
    "source_event_time_utc",
    "received_at_utc",
    "processed_at_utc",
    "correlation_id",
    "raw_s3_uri",
    "schema_version",
    "expires_at_epoch",
}


OPTIONAL_ACTIVE_HAZARDS_V4_FIELDS = {
    "source_icao_id",
    "series_id",
    "alpha_char",
    "receipt_time_utc",
    "severity",
    "altitude_bands",
    "minimum_lower_altitude_ft",
    "maximum_upper_altitude_ft",
    "movement_direction_deg",
    "movement_speed_kt",
    "materialized_at_utc",
    "raw_text",
    "post_process_flag",
}


LEGACY_FIELDS_THAT_MUST_NOT_BE_IN_PARENT = {
    "geometry_json",
    "polygon_coords",
    "coordinates",
    "h3_cells",
    "h3_cell_count",
    "h3_resolution",
    "lower_altitude_1_ft",
    "upper_altitude_1_ft",
    "lower_altitude_2_ft",
    "upper_altitude_2_ft",
    "valid_from",
    "valid_to",
    "issued_at",
    "source",
    "updated_at",
    "expires_at",
    "poll_id",
    "change_type",
    "last_seen_at",
    "last_seen_at_utc",
    "first_seen_at",
    "first_seen_at_utc",
    "last_published_source_version",
    "last_published_at",
}


def parse_iso_utc(value: str) -> datetime:
    cleaned = value

    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"

    parsed = datetime.fromisoformat(cleaned)

    assert parsed.tzinfo is not None

    return parsed


def assert_non_empty_string(
    item: dict[str, Any],
    field: str,
) -> None:
    assert field in item
    assert isinstance(item[field], str)
    assert item[field].strip()


def assert_number(
    item: dict[str, Any],
    field: str,
) -> None:
    assert field in item

    value = item[field]

    assert not isinstance(value, bool)
    assert isinstance(
        value,
        (int, float, Decimal),
    )


def test_active_hazards_v4_actual_item_contract(
    sigmet_processor,
    sigmet_raw_event,
    sigmet_feature,
    fixed_sigmet_time,
    monkeypatch,
):
    monkeypatch.setattr(
        sigmet_processor,
        "now_utc",
        lambda: fixed_sigmet_time,
    )

    monkeypatch.setattr(
        sigmet_processor,
        "now_utc_iso",
        lambda: fixed_sigmet_time.isoformat(),
    )

    geometry_points = (
        sigmet_processor.flatten_geometry_points(
            sigmet_feature["geometry"]
        )
    )

    h3_cells = (
        sigmet_processor.geometry_to_h3_cells(
            sigmet_feature["geometry"],
            sigmet_processor.H3_RESOLUTION,
        )
    )

    impact_cells = (
        sigmet_processor.expand_impact_cells(
            h3_cells,
            sigmet_processor.IMPACT_GRID_DISTANCE,
        )
    )

    item = (
        sigmet_processor.build_active_hazard_item(
            sigmet_raw_event,
            sigmet_feature,
            geometry_points,
            hazard_cell_count=len(h3_cells),
            impact_cell_count=len(impact_cells),
        )
    )

    print(
        "\n\nACTIVE HAZARDS V4 ITEM\n"
        "======================\n"
    )

    print(
        json.dumps(
            item,
            indent=2,
            sort_keys=True,
            default=str,
        )
    )

    print(
        "\n======================\n"
    )

    # ---------------------------------------------------------
    # Required v4 attributes
    # ---------------------------------------------------------

    missing_required = (
        REQUIRED_ACTIVE_HAZARDS_V4_FIELDS
        - set(item)
    )

    assert not missing_required, (
        "Missing required ActiveHazards v4 fields: "
        f"{sorted(missing_required)}"
    )

    # Optional fields are allowed, but fields outside
    # the v4 contract should be investigated.
    allowed_fields = (
        REQUIRED_ACTIVE_HAZARDS_V4_FIELDS
        | OPTIONAL_ACTIVE_HAZARDS_V4_FIELDS
    )

    unexpected_fields = (
        set(item)
        - allowed_fields
    )

    assert not unexpected_fields, (
        "Unexpected ActiveHazards attributes: "
        f"{sorted(unexpected_fields)}"
    )

    # ---------------------------------------------------------
    # Legacy parent fields must be gone
    # ---------------------------------------------------------

    legacy_fields_present = (
        set(item)
        & LEGACY_FIELDS_THAT_MUST_NOT_BE_IN_PARENT
    )

    assert not legacy_fields_present, (
        "Legacy ActiveHazards fields are still present: "
        f"{sorted(legacy_fields_present)}"
    )

    # ---------------------------------------------------------
    # Required values must not be None
    # ---------------------------------------------------------

    for field in REQUIRED_ACTIVE_HAZARDS_V4_FIELDS:
        assert item[field] is not None, (
            f"{field} cannot be None"
        )

    # ---------------------------------------------------------
    # Identity
    # ---------------------------------------------------------

    assert_non_empty_string(
        item,
        "hazard_id",
    )

    assert item["hazard_id"].startswith(
        "sigmet-"
    )

    assert_non_empty_string(
        item,
        "source_version",
    )

    assert_non_empty_string(
        item,
        "source_product_id",
    )

    assert item["source_version"] != (
        item["hazard_id"]
    )

    # ---------------------------------------------------------
    # GSI fields
    #
    # GSI1:
    # status + valid_to_epoch
    #
    # GSI2:
    # source_product_id + created_at_utc
    # ---------------------------------------------------------

    assert_non_empty_string(
        item,
        "status",
    )

    assert_number(
        item,
        "valid_to_epoch",
    )

    assert_non_empty_string(
        item,
        "source_product_id",
    )

    assert_non_empty_string(
        item,
        "created_at_utc",
    )

    # ---------------------------------------------------------
    # Enumerations
    # ---------------------------------------------------------

    assert item["amendment_type"] in {
        "ORIGINAL",
        "AMENDMENT",
        "CORRECTION",
        "CANCELLATION",
        "UNKNOWN",
    }

    assert item["product_type"] in {
        "SIGMET",
        "AIRMET",
    }

    assert item["geometry_type"] in {
        "POLYGON",
        "MULTIPOLYGON",
    }

    assert item["materialization_status"] in {
        "BUILDING",
        "READY",
        "FAILED",
    }

    assert item["status"] in {
        "ACTIVE",
        "CANCELLED",
        "EXPIRED",
    }

    # ---------------------------------------------------------
    # Current migration stage
    # ---------------------------------------------------------

    assert (
        item["materialization_status"]
        == "BUILDING"
    )

    assert "materialized_at_utc" not in item

    # ---------------------------------------------------------
    # Geometry validation
    # ---------------------------------------------------------

    assert_number(
        item,
        "geometry_point_count",
    )

    assert (
        item["geometry_point_count"]
        == len(geometry_points)
    )

    assert item["geometry_point_count"] > 0

    assert_non_empty_string(
        item,
        "geometry_hash",
    )

    assert re.fullmatch(
        r"[0-9a-f]{64}",
        item["geometry_hash"],
    )

    # ---------------------------------------------------------
    # Exact and impact cell expected counts
    # ---------------------------------------------------------

    assert_number(
        item,
        "hazard_cell_count",
    )

    assert_number(
        item,
        "impact_cell_count",
    )

    assert (
        item["hazard_cell_count"]
        == len(h3_cells)
    )

    assert (
        item["impact_cell_count"]
        == len(impact_cells)
    )

    assert item["hazard_cell_count"] > 0

    assert (
        item["impact_cell_count"]
        >= item["hazard_cell_count"]
    )

    # ---------------------------------------------------------
    # Time fields
    # ---------------------------------------------------------

    iso_fields = [
        "created_at_utc",
        "valid_from_utc",
        "valid_to_utc",
        "source_event_time_utc",
        "received_at_utc",
        "processed_at_utc",
    ]

    if "receipt_time_utc" in item:
        iso_fields.append(
            "receipt_time_utc"
        )

    for field in iso_fields:
        assert_non_empty_string(
            item,
            field,
        )

        parse_iso_utc(
            item[field]
        )

    assert_number(
        item,
        "valid_from_epoch",
    )

    assert_number(
        item,
        "valid_to_epoch",
    )

    assert (
        item["valid_from_epoch"]
        < item["valid_to_epoch"]
    )

    assert (
        int(
            parse_iso_utc(
                item["valid_from_utc"]
            ).timestamp()
        )
        == item["valid_from_epoch"]
    )

    assert (
        int(
            parse_iso_utc(
                item["valid_to_utc"]
            ).timestamp()
        )
        == item["valid_to_epoch"]
    )

    # ---------------------------------------------------------
    # Materialization identity
    # ---------------------------------------------------------

    assert_non_empty_string(
        item,
        "materialization_id",
    )

    assert item[
        "materialization_id"
    ].startswith(
        "hazard-materialization-"
    )

    # ---------------------------------------------------------
    # Traceability
    # ---------------------------------------------------------

    assert item["source_system"] == (
        "NOAA_AVIATIONWEATHER_SIGMET"
    )

    assert_non_empty_string(
        item,
        "correlation_id",
    )

    assert_non_empty_string(
        item,
        "raw_s3_uri",
    )

    assert item[
        "raw_s3_uri"
    ].startswith(
        "s3://"
    )

    assert item["schema_version"] == (
        "wilvor.active_hazards.v4.0"
    )

    # ---------------------------------------------------------
    # TTL
    # ---------------------------------------------------------

    assert_number(
        item,
        "expires_at_epoch",
    )

    assert item[
        "expires_at_epoch"
    ] > item[
        "valid_to_epoch"
    ]

    # ---------------------------------------------------------
    # Optional altitude representation
    # ---------------------------------------------------------

    if "altitude_bands" in item:
        assert isinstance(
            item["altitude_bands"],
            list,
        )

        assert item["altitude_bands"]

        for band in item[
            "altitude_bands"
        ]:
            assert isinstance(
                band,
                dict,
            )

            assert (
                "lower_altitude_ft" in band
                or "upper_altitude_ft" in band
            )

            if (
                "lower_altitude_ft"
                in band
            ):
                assert isinstance(
                    band[
                        "lower_altitude_ft"
                    ],
                    (
                        int,
                        float,
                        Decimal,
                    ),
                )

            if (
                "upper_altitude_ft"
                in band
            ):
                assert isinstance(
                    band[
                        "upper_altitude_ft"
                    ],
                    (
                        int,
                        float,
                        Decimal,
                    ),
                )

    # ---------------------------------------------------------
    # ActiveHazards must remain bounded
    # ---------------------------------------------------------

    for value in item.values():
        if isinstance(
            value,
            list,
        ):
            # altitude_bands is the only expected
            # bounded list in this parent.
            assert value is item.get(
                "altitude_bands"
            )