"""Direct truth-table tests for shared operational current-set semantics."""

import pytest

from wilvor_operational import current_set


NOW = 1_788_661_800
FUTURE = "2026-09-06T04:00:00Z"
AT_NOW = "2026-09-06T02:30:00Z"
PAST = "2026-09-06T01:00:00Z"


def test_materialized_aircraft_and_airport_use_strict_expiry():
    at_boundary = {"expires_at_epoch": NOW}
    future = {"expires_at_epoch": NOW + 1}

    assert current_set.is_current_aircraft(at_boundary, NOW) is False
    assert current_set.is_current_aircraft(future, NOW) is True
    assert current_set.is_current_airport_status(at_boundary, NOW) is False
    assert current_set.is_current_airport_status(future, NOW) is True
    assert current_set.is_current_aircraft({}, NOW) is False
    assert current_set.is_current_airport_status(None, NOW) is False


def test_projection_requires_ready_and_strict_future_validity():
    projection = {
        "aircraft_id": "ABC123",
        "projection_id": "projection-1",
        "projection_status": "READY",
        "valid_until_epoch": NOW + 1,
        "generated_at_epoch": 10,
    }

    assert current_set.is_current_projection(projection, NOW) is True
    assert current_set.is_current_projection(
        {**projection, "valid_until_epoch": NOW},
        NOW,
    ) is False
    assert current_set.is_current_projection(
        {**projection, "projection_status": "BUILDING"},
        NOW,
    ) is False
    assert current_set.is_current_projection(
        {**projection, "valid_until_epoch": None},
        NOW,
    ) is False


def test_projection_winner_uses_generation_then_id_independent_of_input_order():
    projection_a = {
        "aircraft_id": "ABC123",
        "projection_id": "projection-a",
        "projection_status": "READY",
        "valid_until_epoch": NOW + 1,
        "generated_at_epoch": 10,
    }
    projection_b = {
        **projection_a,
        "projection_id": "projection-b",
    }

    expected = {"abc123": "projection-b"}
    assert current_set.index_current_projections(
        [projection_a, projection_b],
        NOW,
    ) == expected
    assert current_set.index_current_projections(
        [projection_b, projection_a],
        NOW,
    ) == expected


def test_hazard_lifecycle_is_broader_than_queryable_current():
    hazard = {
        "hazard_id": "hazard-1",
        "source_version": "v1",
        "status": "ACTIVE",
        "materialization_status": "BUILDING",
        "valid_from_epoch": NOW + 3600,
        "valid_to_epoch": NOW,
    }

    assert current_set.is_lifecycle_active_hazard(hazard, NOW) is True
    assert current_set.is_current_hazard(hazard, NOW) is False
    assert current_set.is_current_hazard(
        {**hazard, "materialization_status": "READY"},
        NOW,
    ) is True
    assert current_set.is_lifecycle_active_hazard(
        {**hazard, "valid_to_epoch": NOW - 1},
        NOW,
    ) is False


def test_encounter_uses_state_and_exact_lineage_while_ignoring_ttl():
    encounter = {
        "aircraft_id": "ABC123",
        "projection_id": "projection-1",
        "hazard_id": "hazard-1",
        "hazard_source_version": "v1",
        "encounter_state": "DETECTED",
        "expires_at_epoch": NOW - 1,
    }
    kwargs = {
        "current_projection_ids": {"abc123": "projection-1"},
        "current_hazard_versions": {"hazard-1": "v1"},
    }

    assert current_set.is_current_encounter(encounter, **kwargs) is True
    assert current_set.is_current_encounter(
        {**encounter, "encounter_state": "EXPIRED"},
        **kwargs,
    ) is False
    assert current_set.is_current_encounter(
        {**encounter, "hazard_source_version": "v0"},
        **kwargs,
    ) is False
    assert current_set.is_current_encounter(
        {
            **encounter,
            "hazard_source_version": None,
            "hazard_version_key": "hazard-1#v1",
        },
        **kwargs,
    ) is True


def test_risk_missing_validity_is_accepted_but_present_validity_is_strict():
    common = {
        "risk_id": "risk-1",
        "encounter_id": "encounter-1",
        "generated_at_epoch": 10,
    }
    kwargs = {
        "current_encounter_ids": {"encounter-1"},
        "now_epoch": NOW,
    }

    assert current_set.is_current_risk(common, **kwargs) is True
    assert current_set.is_current_risk(
        {**common, "valid_until_utc": FUTURE},
        **kwargs,
    ) is True
    assert current_set.is_current_risk(
        {**common, "valid_until_utc": AT_NOW},
        **kwargs,
    ) is False
    assert current_set.is_current_risk(
        {**common, "valid_until_utc": "UNKNOWN"},
        **kwargs,
    ) is False
    assert current_set.is_current_risk(
        {**common, "encounter_id": "encounter-old"},
        **kwargs,
    ) is False


def test_equal_generated_risk_tie_preserves_first_input_candidate():
    first = {
        "risk_id": "risk-first",
        "encounter_id": "encounter-1",
        "generated_at_epoch": 10,
        "valid_until_utc": FUTURE,
    }
    second = {
        **first,
        "risk_id": "risk-second",
    }
    kwargs = {
        "current_encounter_ids": {"encounter-1"},
        "now_epoch": NOW,
    }

    forward = current_set.index_latest_current_risks(
        [first, second],
        **kwargs,
    )
    reverse = current_set.index_latest_current_risks(
        [second, first],
        **kwargs,
    )

    assert forward["encounter-1"]["risk_id"] == "risk-first"
    assert reverse["encounter-1"]["risk_id"] == "risk-second"


def test_malformed_risk_generation_preserves_current_runtime_failure():
    malformed = {
        "risk_id": "risk-1",
        "encounter_id": "encounter-1",
        "generated_at_epoch": "UNKNOWN",
    }

    with pytest.raises(ValueError):
        current_set.index_latest_current_risks(
            [malformed],
            current_encounter_ids={"encounter-1"},
            now_epoch=NOW,
        )


def test_recommendation_compatibility_and_time_aware_concepts_are_distinct():
    expired = {
        "recommendation_id": "recommendation-1",
        "recommendation_status": "ACTIVE",
        "risk_id": "risk-current",
        "valid_until_utc": PAST,
    }
    current = {**expired, "valid_until_utc": FUTURE}
    lineage = {"current_risk_ids": {"risk-current"}}

    assert current_set.is_current_recommendation(expired, **lineage) is True
    assert (
        current_set.is_lifecycle_active_recommendation(expired, NOW)
        is False
    )
    assert current_set.is_current_recommendation_at(
        current,
        now_epoch=NOW,
        **lineage,
    ) is True
    assert current_set.is_current_recommendation_at(
        {**current, "risk_id": "risk-old"},
        now_epoch=NOW,
        **lineage,
    ) is False


def test_multiple_recommendations_for_one_current_risk_remain_current():
    recommendations = [
        {
            "recommendation_id": recommendation_id,
            "recommendation_status": "ACTIVE",
            "risk_id": "risk-current",
            "valid_until_utc": FUTURE,
        }
        for recommendation_id in ("recommendation-1", "recommendation-2")
    ]

    assert all(
        current_set.is_current_recommendation_at(
            item,
            current_risk_ids={"risk-current"},
            now_epoch=NOW,
        )
        for item in recommendations
    )


def test_alert_compatibility_uses_active_state_and_or_lineage():
    alert = {
        "alert_id": "alert-1",
        "alert_state": "MONITORING",
        "risk_id": "risk-old",
        "recommendation_id": "recommendation-current",
        "valid_until_utc": PAST,
    }
    lineage = {
        "current_risk_ids": {"risk-current"},
        "current_recommendation_ids": {"recommendation-current"},
    }

    assert current_set.is_current_alert(alert, **lineage) is True
    assert current_set.is_lifecycle_active_alert(alert, NOW) is False
    assert current_set.is_current_alert_at(
        {**alert, "valid_until_utc": FUTURE},
        now_epoch=NOW,
        **lineage,
    ) is True
    assert current_set.is_current_alert_at(
        {**alert, "alert_state": "RESOLVED", "valid_until_utc": FUTURE},
        now_epoch=NOW,
        **lineage,
    ) is False


def test_no_global_airport_assessment_currentness_is_defined():
    assert not hasattr(current_set, "is_current_airport_assessment")
