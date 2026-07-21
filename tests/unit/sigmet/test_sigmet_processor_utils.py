from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            1_752_840_000,
            datetime(2025, 7, 18, 12, 0, tzinfo=timezone.utc),
        ),
        (
            1_752_840_000_000,
            datetime(2025, 7, 18, 12, 0, tzinfo=timezone.utc),
        ),
        (
            "1752840000",
            datetime(2025, 7, 18, 12, 0, tzinfo=timezone.utc),
        ),
        (
            "2026-07-18T12:00:00Z",
            datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
        ),
        (
            "2026-07-18T12:00:00",
            datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
        ),
        (None, None),
        ("", None),
        ("not-a-time", None),
        ({"bad": "type"}, None),
    ],
)
def test_parse_time_supports_expected_formats(
    sigmet_processor,
    value,
    expected,
):
    assert sigmet_processor.parse_time(value) == expected


def test_stable_hash_is_deterministic_for_key_order(sigmet_processor):
    first = sigmet_processor.stable_hash({"a": 1, "b": 2})
    second = sigmet_processor.stable_hash({"b": 2, "a": 1})

    assert first == second
    assert len(first) == 64


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  ABC  ", "ABC"),
        ("   ", None),
        (None, None),
        (123, "123"),
        ({"b": 2, "a": 1}, '{"a":1,"b":2}'),
    ],
)
def test_clean_string(sigmet_processor, value, expected):
    assert sigmet_processor.clean_string(value) == expected


def test_canonical_time_or_string_normalizes_time(sigmet_processor):
    assert sigmet_processor.canonical_time_or_string(
        "2026-07-18T12:00:00Z"
    ) == "2026-07-18T12:00:00+00:00"

    assert sigmet_processor.canonical_time_or_string(
        "custom-value"
    ) == "custom-value"


def test_get_first_property_returns_first_non_blank(sigmet_processor):
    properties = {
        "first": " ",
        "second": None,
        "third": "value",
        "fourth": "later",
    }

    assert sigmet_processor.get_first_property(
        properties,
        ["first", "second", "third", "fourth"],
    ) == "value"


def test_decode_kinesis_record_returns_object(
    sigmet_processor,
    sigmet_raw_event,
    sigmet_kinesis_record_factory,
):
    record = sigmet_kinesis_record_factory(sigmet_raw_event)

    assert sigmet_processor.decode_kinesis_record(record) == sigmet_raw_event


@pytest.mark.parametrize(
    ("record", "expected_message"),
    [
        ({}, "missing kinesis.data"),
        (
            {
                "kinesis": {
                    # Valid Base64 whose decoded bytes are not UTF-8.
                    "data": base64.b64encode(b"\xff").decode("ascii"),
                }
            },
            "not valid base64 UTF-8",
        ),
        (
            {
                "kinesis": {
                    "data": base64.b64encode(b"not-json")
                    .decode("ascii")
                }
            },
            "not valid JSON",
        ),
        (
            {
                "kinesis": {
                    "data": base64.b64encode(b"[]").decode("ascii")
                }
            },
            "not a JSON object",
        ),
    ],
)
def test_decode_kinesis_record_rejects_permanent_errors(
    sigmet_processor,
    record,
    expected_message,
):
    with pytest.raises(
        sigmet_processor.PermanentRecordError,
        match=expected_message,
    ):
        sigmet_processor.decode_kinesis_record(record)


def test_extract_feature_and_properties(
    sigmet_processor,
    sigmet_raw_event,
):
    feature = sigmet_processor.extract_feature(sigmet_raw_event)
    properties = sigmet_processor.extract_properties(feature)

    assert feature["type"] == "Feature"
    assert properties["icaoId"] == "KZNY"


@pytest.mark.parametrize(
    ("raw_event", "message"),
    [
        ({}, "valid GeoJSON feature"),
        ({"feature": []}, "valid GeoJSON feature"),
        (
            {"feature": {"type": "Polygon"}},
            "not a GeoJSON Feature",
        ),
    ],
)
def test_extract_feature_rejects_invalid_payload(
    sigmet_processor,
    raw_event,
    message,
):
    with pytest.raises(
        sigmet_processor.PermanentRecordError,
        match=message,
    ):
        sigmet_processor.extract_feature(raw_event)


def test_extract_properties_returns_empty_object_for_non_object(
    sigmet_processor,
):
    feature = {
        "type": "Feature",
        "properties": [],
    }

    assert sigmet_processor.extract_properties(feature) == {}


def test_extract_source_identity_normalizes_fields(
    sigmet_processor,
    sigmet_feature,
):
    identity = sigmet_processor.extract_source_identity(
        sigmet_feature["properties"]
    )

    assert identity == {
        "source_icao_id": "KZNY",
        "air_sigmet_type": "SIGMET",
        "alpha_char": "A",
        "series_id": "12",
        "creation_time": "2026-07-18T12:00:00+00:00",
        "valid_time_from": "2026-07-18T12:00:00+00:00",
        "valid_time_to": "2026-07-18T18:00:00+00:00",
    }


def test_extract_source_identity_requires_usable_field(
    sigmet_processor,
):
    with pytest.raises(
        sigmet_processor.PermanentRecordError,
        match="no usable identity fields",
    ):
        sigmet_processor.extract_source_identity({})


def test_build_hazard_id_is_stable_and_identity_sensitive(
    sigmet_processor,
    sigmet_feature,
):
    properties = sigmet_feature["properties"]

    first = sigmet_processor.build_hazard_id(properties)
    second = sigmet_processor.build_hazard_id(dict(properties))

    changed = dict(properties)
    changed["seriesId"] = "13"
    third = sigmet_processor.build_hazard_id(changed)

    assert first == second
    assert first.startswith("sigmet-")
    assert len(first) == len("sigmet-") + 24
    assert third != first


def test_build_source_version_is_content_sensitive(
    sigmet_processor,
    sigmet_feature,
):
    first = sigmet_processor.build_source_version(sigmet_feature)
    second = sigmet_processor.build_source_version(
        json.loads(json.dumps(sigmet_feature))
    )

    changed = json.loads(json.dumps(sigmet_feature))
    changed["properties"]["severity"] = "MOD"
    third = sigmet_processor.build_source_version(changed)

    assert first == second
    assert len(first) == 32
    assert third != first


@pytest.mark.parametrize(
    ("properties", "expected"),
    [
        ({"hazard": "Severe Turbulence"}, "SEVERE_TURBULENCE"),
        ({"airSigmetType": "Convective"}, "CONVECTIVE"),
        ({"rawAirSigmet": "SEV TURB EXPECTED"}, "TURB"),
        ({"rawSigmet": "VOLCANIC ASH CLOUD"}, "VOLCANIC"),
        ({}, "UNKNOWN"),
    ],
)
def test_get_hazard_type(sigmet_processor, properties, expected):
    assert sigmet_processor.get_hazard_type(properties) == expected


def test_build_raw_s3_uri(sigmet_processor):
    assert sigmet_processor.build_raw_s3_uri(
        {
            "raw_s3_bucket": "bucket",
            "raw_s3_key": "raw/key.json.gz",
        }
    ) == "s3://bucket/raw/key.json.gz"

    assert sigmet_processor.build_raw_s3_uri({}) is None


def test_ttl_from_valid_to_adds_six_hours(
    sigmet_processor,
):
    valid_to = datetime(
        2026,
        7,
        18,
        18,
        0,
        tzinfo=timezone.utc,
    )

    assert sigmet_processor.ttl_from_valid_to(valid_to) == int(
        datetime(
            2026,
            7,
            19,
            0,
            0,
            tzinfo=timezone.utc,
        ).timestamp()
    )
