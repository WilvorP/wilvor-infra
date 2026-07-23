import pytest

from models import NormalizedRunway, RunwayEnd
from validation import RecordValidationError, validate_normalized_runway


def runway_end(identifier: str, heading: float = 90.0) -> RunwayEnd:
    return RunwayEnd(
        runway_end_id=identifier,
        true_heading_deg=heading,
        latitude=35.0,
        longitude=-100.0,
        elevation_ft=100.0,
        landing_distance_available_ft=5000,
        takeoff_run_available_ft=5000,
    )


def test_invalid_heading_is_rejected() -> None:
    runway = NormalizedRunway(
        airport_id="KAAA",
        faa_id="AAA",
        physical_runway_id="09/27",
        length_ft=5000,
        width_ft=100,
        surface_type="ASPH",
        surface_condition="G",
        lighting_code="MED",
        end_1=runway_end("09", 361.0),
        end_2=runway_end("27", 270.0),
    )

    with pytest.raises(RecordValidationError, match="between 0 and 360"):
        validate_normalized_runway(runway)


def test_mismatched_runway_end_is_rejected() -> None:
    runway = NormalizedRunway(
        airport_id="KAAA",
        faa_id="AAA",
        physical_runway_id="09/27",
        length_ft=5000,
        width_ft=100,
        surface_type="ASPH",
        surface_condition="G",
        lighting_code="MED",
        end_1=runway_end("08"),
        end_2=runway_end("27", 270.0),
    )

    with pytest.raises(RecordValidationError, match="does not belong"):
        validate_normalized_runway(runway)
