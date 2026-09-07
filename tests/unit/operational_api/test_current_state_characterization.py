"""Golden characterization of pre-Phase-1A Operational API semantics.

These tests freeze current runtime behavior. Some assertions intentionally
describe compatibility constraints or unresolved policy ambiguities; they do
not endorse those behaviors as ideal aviation-domain policy.
"""

import current_set


NOW = 1_788_661_800  # 2026-09-06T02:30:00Z
FUTURE = "2026-09-06T04:00:00Z"
PAST = "2026-09-06T01:00:00Z"


class _CapturedAttribute:
    def __init__(self, name):
        self.name = name

    def gt(self, value):
        return ("gt", self.name, value)


def test_aircraft_and_airport_lists_use_strict_expiry_membership(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    aircraft_call = {}
    airport_call = {}

    monkeypatch.setattr(repo.time, "time", lambda: NOW)
    monkeypatch.setattr(repo, "Attr", _CapturedAttribute)
    monkeypatch.setattr(
        repo.AIRCRAFT,
        "scan",
        lambda **kwargs: aircraft_call.update(kwargs) or {"Items": []},
    )
    monkeypatch.setattr(
        repo.AIRPORTS,
        "scan",
        lambda **kwargs: airport_call.update(kwargs) or {"Items": []},
    )

    repo.list_aircraft(limit=10)
    repo.list_airports(limit=10)

    strict_expiry = ("gt", "expires_at_epoch", NOW)
    assert aircraft_call["FilterExpression"] == strict_expiry
    assert airport_call["FilterExpression"] == strict_expiry


def test_retained_expired_aircraft_and_airport_are_returned_by_detail(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    expired_aircraft = {
        "aircraft_id": "abc123",
        "expires_at_epoch": NOW,
    }
    expired_airport = {
        "airport_id": "KSEA",
        "station_id": "KSEA",
        "expires_at_epoch": NOW,
    }

    monkeypatch.setattr(repo.time, "time", lambda: NOW)
    monkeypatch.setattr(
        repo.AIRCRAFT,
        "get_item",
        lambda **kwargs: {"Item": expired_aircraft},
    )
    monkeypatch.setattr(
        repo.AIRPORTS,
        "get_item",
        lambda **kwargs: {"Item": expired_airport},
    )
    monkeypatch.setattr(
        repo.METAR,
        "get_item",
        lambda **kwargs: {},
    )
    monkeypatch.setattr(
        repo.TAF,
        "get_item",
        lambda **kwargs: {},
    )
    monkeypatch.setattr(
        repo.TAF_PERIODS,
        "query",
        lambda **kwargs: {"Items": []},
    )
    monkeypatch.setattr(repo, "_query_latest", lambda *args, **kwargs: [])
    monkeypatch.setattr(repo, "_load_current_indexes", lambda now: ({}, {}))

    aircraft_detail = repo.get_aircraft_detail("ABC123")
    airport_detail = repo.get_airport_detail("ksea")

    assert aircraft_detail["aircraft"] is expired_aircraft
    assert airport_detail["airport"] is expired_airport


def test_projection_currentness_boundary_and_winner_are_stable():
    ready_at_boundary = {
        "aircraft_id": "abc123",
        "projection_id": "projection-boundary",
        "projection_status": "READY",
        "valid_until_epoch": NOW,
        "generated_at_epoch": NOW,
    }
    ready_after_boundary = {
        **ready_at_boundary,
        "projection_id": "projection-a",
        "valid_until_epoch": NOW + 1,
    }
    same_time_larger_id = {
        **ready_after_boundary,
        "projection_id": "projection-b",
    }

    assert current_set.is_current_projection(ready_at_boundary, NOW) is False
    assert current_set.is_current_projection(ready_after_boundary, NOW) is True
    assert (
        current_set.is_current_projection(
            {**ready_after_boundary, "projection_status": "BUILDING"},
            NOW,
        )
        is False
    )
    assert current_set.index_current_projections(
        [same_time_larger_id, ready_after_boundary],
        NOW,
    ) == {"abc123": "projection-b"}
    assert current_set.index_current_projections(
        [ready_after_boundary, same_time_larger_id],
        NOW,
    ) == {"abc123": "projection-b"}


def test_hazard_queryable_current_is_inclusive_and_ignores_valid_from():
    future_valid_from = {
        "hazard_id": "hazard-1",
        "source_version": "v1",
        "status": "ACTIVE",
        "materialization_status": "READY",
        "valid_from_epoch": NOW + 3600,
        "valid_to_epoch": NOW,
    }

    assert current_set.is_current_hazard(future_valid_from, NOW) is True
    assert (
        current_set.is_current_hazard(
            {
                **future_valid_from,
                "materialization_status": "BUILDING",
            },
            NOW,
        )
        is False
    )
    assert (
        current_set.is_current_hazard(
            {**future_valid_from, "status": "CANCELLED"},
            NOW,
        )
        is False
    )


def test_overview_hazard_count_is_broader_than_queryable_current(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    hazard_count_call = {}

    monkeypatch.setattr(repo.time, "time", lambda: NOW)
    monkeypatch.setattr(repo, "_now_iso", lambda: "2026-09-06T02:30:00Z")
    monkeypatch.setattr(repo, "_scan_count", lambda *args, **kwargs: 0)

    def query_count(table, **kwargs):
        if table is repo.HAZARDS:
            hazard_count_call.update(kwargs)
            return 7
        return 0

    monkeypatch.setattr(repo, "_query_count", query_count)
    monkeypatch.setattr(
        repo,
        "_current_encounter_snapshot",
        lambda: {
            "now_epoch": NOW,
            "items": [],
            "projection_ids": {},
            "hazard_versions": {},
        },
    )
    monkeypatch.setattr(
        repo,
        "_latest_current_risks",
        lambda: {
            "by_encounter": {},
            "items": [],
            "current_risk_ids": set(),
        },
    )
    monkeypatch.setattr(repo, "_active_recommendations", lambda: {"items": []})
    monkeypatch.setattr(
        repo,
        "_active_alerts",
        lambda: {
            "items": [],
            "active_count": 0,
            "by_state": {},
        },
    )
    monkeypatch.setattr(repo, "_scan_all", lambda *args, **kwargs: [])

    overview = repo.get_overview()

    assert overview["hazards"]["activeCount"] == 7
    assert "FilterExpression" not in hazard_count_call
    assert "KeyConditionExpression" in hazard_count_call


def test_encounter_currentness_uses_state_and_lineage_but_not_ttl():
    encounter = {
        "aircraft_id": "ABC123",
        "projection_id": "projection-1",
        "hazard_id": "hazard-1",
        "hazard_source_version": "v1",
        "encounter_state": "MONITORING",
        "expires_at_epoch": NOW - 1,
    }
    projections = {"abc123": "projection-1"}
    hazards = {"hazard-1": "v1"}

    assert current_set.is_current_encounter(
        encounter,
        current_projection_ids=projections,
        current_hazard_versions=hazards,
    )
    assert (
        current_set.is_current_encounter(
            {**encounter, "encounter_state": "RESOLVED"},
            current_projection_ids=projections,
            current_hazard_versions=hazards,
        )
        is False
    )
    assert (
        current_set.is_current_encounter(
            {**encounter, "projection_id": "projection-old"},
            current_projection_ids=projections,
            current_hazard_versions=hazards,
        )
        is False
    )
    assert (
        current_set.is_current_encounter(
            {**encounter, "hazard_source_version": "v0"},
            current_projection_ids=projections,
            current_hazard_versions=hazards,
        )
        is False
    )


def test_latest_current_risk_accepts_missing_validity_and_keeps_first_tie(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    risks = [
        {
            "risk_id": "risk-first",
            "encounter_id": "encounter-1",
            "generated_at_epoch": 100,
            "valid_until_utc": FUTURE,
        },
        {
            "risk_id": "risk-equal-later-in-input",
            "encounter_id": "encounter-1",
            "generated_at_epoch": 100,
            "valid_until_utc": FUTURE,
        },
        {
            "risk_id": "risk-newer-but-expired",
            "encounter_id": "encounter-1",
            "generated_at_epoch": 200,
            "valid_until_utc": PAST,
        },
        {
            "risk_id": "risk-missing-validity",
            "encounter_id": "encounter-2",
            "generated_at_epoch": 300,
        },
        {
            "risk_id": "risk-historical-encounter",
            "encounter_id": "encounter-old",
            "generated_at_epoch": 400,
            "valid_until_utc": FUTURE,
        },
    ]

    monkeypatch.setattr(repo.time, "time", lambda: NOW)
    monkeypatch.setattr(
        repo,
        "_current_encounter_snapshot",
        lambda: {
            "now_epoch": NOW,
            "items": [
                {"encounter_id": "encounter-1"},
                {"encounter_id": "encounter-2"},
            ],
            "projection_ids": {},
            "hazard_versions": {},
        },
    )
    monkeypatch.setattr(
        repo,
        "_scan_all",
        lambda table, **kwargs: risks if table is repo.RISKS else [],
    )

    snapshot = repo._latest_current_risks()

    assert snapshot["by_encounter"]["encounter-1"]["risk_id"] == "risk-first"
    assert (
        snapshot["by_encounter"]["encounter-2"]["risk_id"]
        == "risk-missing-validity"
    )
    assert snapshot["current_risk_ids"] == {
        "risk-first",
        "risk-missing-validity",
    }


def test_recommendation_helper_is_non_temporal_but_snapshot_is_time_aware(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    expired_but_linked = {
        "recommendation_id": "rec-expired",
        "risk_id": "risk-current",
        "recommendation_status": "ACTIVE",
        "valid_until_utc": PAST,
    }
    recommendations = [
        {
            "recommendation_id": "rec-current-1",
            "risk_id": "risk-current",
            "recommendation_status": "ACTIVE",
            "valid_until_utc": FUTURE,
        },
        {
            "recommendation_id": "rec-current-2",
            "risk_id": "risk-current",
            "recommendation_status": "ACTIVE",
            "valid_until_utc": FUTURE,
        },
        {
            "recommendation_id": "rec-retained",
            "risk_id": "risk-old",
            "recommendation_status": "ACTIVE",
            "valid_until_utc": FUTURE,
        },
        expired_but_linked,
    ]

    assert current_set.is_current_recommendation(
        expired_but_linked,
        current_risk_ids={"risk-current"},
    )

    monkeypatch.setattr(repo.time, "time", lambda: NOW)
    monkeypatch.setattr(
        repo,
        "_latest_current_risks",
        lambda: {"current_risk_ids": {"risk-current"}},
    )
    monkeypatch.setattr(
        repo,
        "_scan_all",
        lambda table, **kwargs: (
            recommendations if table is repo.RECOMMENDATIONS else []
        ),
    )

    snapshot = repo._current_recommendation_snapshot()

    assert {
        item["recommendation_id"]
        for item in snapshot["items"]
    } == {"rec-current-1", "rec-current-2"}


def test_alert_helper_uses_active_state_and_or_lineage_without_time():
    common = {
        "alert_state": "UPDATED",
        "valid_until_utc": PAST,
    }

    assert current_set.is_current_alert(
        {**common, "risk_id": "risk-current"},
        current_risk_ids={"risk-current"},
        current_recommendation_ids=set(),
    )
    assert current_set.is_current_alert(
        {**common, "recommendation_id": "rec-current"},
        current_risk_ids=set(),
        current_recommendation_ids={"rec-current"},
    )
    assert (
        current_set.is_current_alert(
            {**common, "risk_id": "risk-old", "recommendation_id": "rec-old"},
            current_risk_ids={"risk-current"},
            current_recommendation_ids={"rec-current"},
        )
        is False
    )
    assert (
        current_set.is_current_alert(
            {**common, "alert_state": "RESOLVED", "risk_id": "risk-current"},
            current_risk_ids={"risk-current"},
            current_recommendation_ids=set(),
        )
        is False
    )


def test_current_alert_snapshot_adds_strict_validity_to_or_lineage(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    alerts = [
        {
            "alert_id": "alert-risk",
            "alert_state": "NEW",
            "risk_id": "risk-current",
            "valid_until_utc": FUTURE,
        },
        {
            "alert_id": "alert-recommendation",
            "alert_state": "MONITORING",
            "recommendation_id": "rec-current",
            "valid_until_utc": FUTURE,
        },
        {
            "alert_id": "alert-expired",
            "alert_state": "ESCALATED",
            "risk_id": "risk-current",
            "valid_until_utc": PAST,
        },
        {
            "alert_id": "alert-orphan",
            "alert_state": "UPDATED",
            "risk_id": "risk-old",
            "valid_until_utc": FUTURE,
        },
    ]

    monkeypatch.setattr(repo.time, "time", lambda: NOW)
    monkeypatch.setattr(
        repo,
        "_current_risk_and_recommendation_ids",
        lambda: ({"risk-current"}, {"rec-current"}),
    )
    monkeypatch.setattr(
        repo,
        "_scan_all",
        lambda table, **kwargs: alerts if table is repo.ALERTS else [],
    )

    snapshot = repo._current_alert_snapshot()

    assert {
        item["alert_id"]
        for item in snapshot["items"]
    } == {"alert-risk", "alert-recommendation"}


def test_active_encounter_enrichment_uses_newest_risk_without_current_filter(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    expired_risk = {
        "risk_id": "risk-newest-expired",
        "encounter_id": "encounter-1",
        "valid_until_utc": PAST,
    }

    monkeypatch.setattr(
        repo,
        "_current_encounter_snapshot",
        lambda: {
            "now_epoch": NOW,
            "items": [
                {
                    "encounter_id": "encounter-1",
                    "detected_at_epoch": NOW,
                }
            ],
            "projection_ids": {},
            "hazard_versions": {},
        },
    )
    monkeypatch.setattr(
        repo,
        "_query_latest",
        lambda *args, **kwargs: [expired_risk],
    )

    listing = repo.list_active_encounters(limit=10)

    assert listing["items"][0]["risk"] is expired_risk
