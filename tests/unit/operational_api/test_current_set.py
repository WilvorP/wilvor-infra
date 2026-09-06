import current_set


NOW = 1_700_000_000


def test_ttl_does_not_make_an_encounter_current():
    encounter = {
        "encounter_id": "proj-old#hazard-1#v1",
        "aircraft_id": "abc123",
        "projection_id": "proj-old",
        "hazard_id": "hazard-1",
        "hazard_source_version": "v1",
        "encounter_state": "DETECTED",
        "expires_at_epoch": NOW + 3600,
    }

    assert (
        current_set.is_current_encounter(
            encounter,
            current_projection_ids={"abc123": "proj-new"},
            current_hazard_versions={"hazard-1": "v1"},
        )
        is False
    )


def test_current_encounter_requires_current_projection_and_hazard():
    encounter = {
        "encounter_id": "proj-1#hazard-1#v1",
        "aircraft_id": "abc123",
        "projection_id": "proj-1",
        "hazard_id": "hazard-1",
        "hazard_source_version": "v1",
        "encounter_state": "DETECTED",
        "expires_at_epoch": NOW - 1,
    }

    assert (
        current_set.is_current_encounter(
            encounter,
            current_projection_ids={"abc123": "proj-1"},
            current_hazard_versions={"hazard-1": "v1"},
        )
        is True
    )


def test_resolved_encounter_is_not_current():
    encounter = {
        "encounter_id": "proj-1#hazard-1#v1",
        "aircraft_id": "abc123",
        "projection_id": "proj-1",
        "hazard_id": "hazard-1",
        "hazard_source_version": "v1",
        "encounter_state": "SUPERSEDED",
    }

    assert (
        current_set.is_current_encounter(
            encounter,
            current_projection_ids={"abc123": "proj-1"},
            current_hazard_versions={"hazard-1": "v1"},
        )
        is False
    )


def test_stale_hazard_version_is_not_current():
    encounter = {
        "encounter_id": "proj-1#hazard-1#v1",
        "aircraft_id": "abc123",
        "projection_id": "proj-1",
        "hazard_id": "hazard-1",
        "hazard_source_version": "v1",
        "encounter_state": "DETECTED",
    }

    assert (
        current_set.is_current_encounter(
            encounter,
            current_projection_ids={"abc123": "proj-1"},
            current_hazard_versions={"hazard-1": "v2"},
        )
        is False
    )


def test_index_current_projections_keeps_latest_unexpired():
    indexed = current_set.index_current_projections(
        [
            {
                "aircraft_id": "abc123",
                "projection_id": "proj-old",
                "generated_at_epoch": NOW - 100,
                "valid_until_epoch": NOW + 100,
                "projection_status": "READY",
            },
            {
                "aircraft_id": "abc123",
                "projection_id": "proj-new",
                "generated_at_epoch": NOW - 10,
                "valid_until_epoch": NOW + 100,
                "projection_status": "READY",
            },
            {
                "aircraft_id": "abc123",
                "projection_id": "proj-expired",
                "generated_at_epoch": NOW,
                "valid_until_epoch": NOW - 1,
                "projection_status": "READY",
            },
        ],
        NOW,
    )

    assert indexed == {"abc123": "proj-new"}


def test_recommendation_and_alert_currentness_join_by_id():
    assert current_set.is_current_recommendation(
        {
            "recommendation_id": "rec-1",
            "recommendation_status": "ACTIVE",
            "risk_id": "risk-1",
        },
        current_risk_ids={"risk-1"},
    )
    assert (
        current_set.is_current_recommendation(
            {
                "recommendation_id": "rec-old",
                "recommendation_status": "ACTIVE",
                "risk_id": "risk-old",
            },
            current_risk_ids={"risk-1"},
        )
        is False
    )
    assert current_set.is_current_alert(
        {
            "alert_state": "NEW",
            "risk_id": "risk-1",
        },
        current_risk_ids={"risk-1"},
        current_recommendation_ids=set(),
    )
    assert (
        current_set.is_current_alert(
            {
                "alert_state": "RESOLVED",
                "risk_id": "risk-1",
            },
            current_risk_ids={"risk-1"},
            current_recommendation_ids=set(),
        )
        is False
    )
