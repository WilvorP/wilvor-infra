from __future__ import annotations

import time

import pytest

from wilvor_aircraft import opensky_mapper
from wilvor_aircraft.schemas import (
    AIRCRAFT_CURRENT_STATE_SCHEMA_VERSION,
    METERS_TO_FEET,
    MPS_TO_FPM,
    MPS_TO_KNOTS,
    OPENSKY_STATE_VECTOR_COLUMNS,
)


@pytest.mark.parametrize(
    ("function", "value", "expected"),
    [
        (opensky_mapper.meters_to_feet, 100.0, 100.0 * METERS_TO_FEET),
        (opensky_mapper.mps_to_knots, 100.0, 100.0 * MPS_TO_KNOTS),
        (opensky_mapper.mps_to_fpm, 5.0, 5.0 * MPS_TO_FPM),
    ],
)
def test_unit_conversions(function, value, expected):
    assert function(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    "function",
    [
        opensky_mapper.meters_to_feet,
        opensky_mapper.mps_to_knots,
        opensky_mapper.mps_to_fpm,
    ],
)
def test_unit_conversions_preserve_none(function):
    assert function(None) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" UAL123  ", "UAL123"),
        ("", None),
        ("   ", None),
        (None, None),
        (123, "123"),
    ],
)
def test_clean_callsign(value, expected):
    assert opensky_mapper.clean_callsign(value) == expected


def test_vector_to_dict_maps_all_opensky_columns(valid_opensky_state_vector):
    result = opensky_mapper.vector_to_dict(valid_opensky_state_vector)

    assert list(result.keys()) == OPENSKY_STATE_VECTOR_COLUMNS
    assert result["icao24"] == "ABC123"
    assert result["longitude"] == -122.375
    assert result["latitude"] == 37.6189


def test_vector_to_dict_pads_short_vectors_with_none():
    result = opensky_mapper.vector_to_dict(["abc123", "TEST1"])

    assert result["icao24"] == "abc123"
    assert result["callsign"] == "TEST1"
    assert result["origin_country"] is None
    assert result["position_source"] is None


def test_validate_rejects_non_list_state_vector():
    assert opensky_mapper.validate_raw_state_vector("not-a-list") == [
        "raw_vector_not_list"
    ]


def test_validate_accepts_valid_state_vector(valid_opensky_state_vector):
    assert opensky_mapper.validate_raw_state_vector(valid_opensky_state_vector) == []


def test_validate_reports_short_vector_and_missing_required_fields():
    reasons = opensky_mapper.validate_raw_state_vector(["abc123"])

    assert "raw_vector_too_short" in reasons
    assert "missing_required_callsign" in reasons
    assert "missing_required_longitude" in reasons
    assert "missing_required_latitude" in reasons
    assert "missing_required_geo_altitude" in reasons
    assert "missing_required_velocity" in reasons
    assert "missing_required_true_track" in reasons
    assert "missing_required_vertical_rate" in reasons
    assert "missing_required_on_ground" in reasons
    assert "missing_required_last_contact" in reasons


@pytest.mark.parametrize(
    ("index", "invalid_value", "expected_reason"),
    [
        (6, 91.0, "invalid_latitude_range"),
        (5, -181.0, "invalid_longitude_range"),
        (13, 30_000.0, "unrealistic_geo_altitude"),
        (7, -600.0, "unrealistic_baro_altitude"),
        (9, -1.0, "unrealistic_velocity"),
        (9, 401.0, "unrealistic_velocity"),
        (10, 361.0, "unrealistic_true_track"),
        (11, 151.0, "unrealistic_vertical_rate"),
        (8, "false", "invalid_on_ground"),
    ],
)
def test_validate_rejects_out_of_range_values(
    valid_opensky_state_vector,
    index,
    invalid_value,
    expected_reason,
):
    valid_opensky_state_vector[index] = invalid_value

    reasons = opensky_mapper.validate_raw_state_vector(
        valid_opensky_state_vector
    )

    assert expected_reason in reasons


def test_validate_rejects_timestamps_that_are_too_old(
    valid_opensky_state_vector,
):
    valid_opensky_state_vector[3] = 1_000
    valid_opensky_state_vector[4] = 1_000

    reasons = opensky_mapper.validate_raw_state_vector(
        valid_opensky_state_vector
    )

    assert "unrealistic_time_position_too_old" in reasons
    assert "unrealistic_last_contact_too_old" in reasons


def test_validate_rejects_timestamps_too_far_in_future(
    valid_opensky_state_vector,
):
    future = int(time.time()) + 601
    valid_opensky_state_vector[3] = future
    valid_opensky_state_vector[4] = future

    reasons = opensky_mapper.validate_raw_state_vector(
        valid_opensky_state_vector
    )

    assert "unrealistic_time_position_in_future" in reasons
    assert "unrealistic_last_contact_in_future" in reasons


def test_map_raw_event_builds_canonical_current_state(
    raw_opensky_event,
    monkeypatch,
):
    monkeypatch.setattr(opensky_mapper, "now_epoch", lambda: 2_000_000_000)

    item, reasons = opensky_mapper.map_raw_event_to_current_state(
        raw_opensky_event,
        ttl_seconds=900,
    )

    assert reasons == []
    assert item is not None
    assert item["icao24"] == "abc123"
    assert item["callsign"] == "UAL123"
    assert item["schema_version"] == AIRCRAFT_CURRENT_STATE_SCHEMA_VERSION
    assert item["has_position"] is True
    assert item["baro_altitude_ft"] == pytest.approx(
        10_000.0 * METERS_TO_FEET
    )
    assert item["geo_altitude_ft"] == pytest.approx(
        10_200.0 * METERS_TO_FEET
    )
    assert item["ground_speed_kt"] == pytest.approx(
        230.0 * MPS_TO_KNOTS
    )
    assert item["vertical_rate_fpm"] == pytest.approx(
        2.5 * MPS_TO_FPM
    )
    assert item["ttl_epoch"] == 2_000_000_900


def test_map_raw_event_returns_reasons_instead_of_partial_item(
    raw_opensky_event,
):
    raw_opensky_event["raw_state_vector"][5] = None

    item, reasons = opensky_mapper.map_raw_event_to_current_state(
        raw_opensky_event
    )

    assert item is None
    assert "missing_required_longitude" in reasons


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("not-a-timestamp", None),
    ],
)
def test_epoch_to_iso_returns_none_for_unusable_values(value, expected):
    assert opensky_mapper.epoch_to_iso(value) is expected