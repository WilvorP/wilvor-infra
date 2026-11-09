"""Golden characterization of Operational API context/join dialects.

These tests freeze current repository composition behavior. They import
only the Operational API repository. They do not import
wilvor_operational.linking or wilvor_operational.context.

Some assertions describe compatibility dialects that later shared
operational context must not silently replace.
"""

from __future__ import annotations


NOW = 1_788_661_800  # 2026-09-06T02:30:00Z
PAST = "2026-09-06T01:00:00Z"


def _empty_points(repo, monkeypatch):
    monkeypatch.setattr(
        repo.PROJECTION_POINTS,
        "query",
        lambda **kwargs: {"Items": []},
    )


def test_aircraft_detail_uses_first_current_projection_in_newest_ten(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    building = {
        "projection_id": "proj-building",
        "aircraft_id": "abc123",
        "projection_status": "BUILDING",
        "valid_until_epoch": NOW + 1000,
        "generated_at_epoch": NOW + 10,
    }
    first_current = {
        "projection_id": "proj-first-current",
        "aircraft_id": "abc123",
        "projection_status": "READY",
        "valid_until_epoch": NOW + 1000,
        "generated_at_epoch": NOW,
    }
    later_current = {
        "projection_id": "proj-later-current",
        "aircraft_id": "abc123",
        "projection_status": "READY",
        "valid_until_epoch": NOW + 2000,
        "generated_at_epoch": NOW - 10,
    }

    monkeypatch.setattr(
        repo.AIRCRAFT,
        "get_item",
        lambda **kwargs: {"Item": {"aircraft_id": "abc123"}},
    )
    monkeypatch.setattr(
        repo,
        "_query_latest",
        lambda table, index, partition, value, limit=10: (
            [building, first_current, later_current]
            if table is repo.PROJECTIONS
            else []
        ),
    )
    _empty_points(repo, monkeypatch)
    monkeypatch.setattr(repo, "_load_current_indexes", lambda now: ({}, {}))
    monkeypatch.setattr(repo.time, "time", lambda: NOW)

    detail = repo.get_aircraft_detail("abc123")

    assert detail["projection"]["projection_id"] == "proj-first-current"
    assert detail["projection"]["projection_id"] != "proj-later-current"


def test_aircraft_detail_encounter_lineage_uses_global_projection_index(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    displayed = {
        "projection_id": "proj-displayed",
        "aircraft_id": "abc123",
        "projection_status": "READY",
        "valid_until_epoch": NOW + 1000,
        "generated_at_epoch": NOW,
    }

    monkeypatch.setattr(
        repo.AIRCRAFT,
        "get_item",
        lambda **kwargs: {"Item": {"aircraft_id": "abc123"}},
    )
    monkeypatch.setattr(
        repo,
        "_query_latest",
        lambda table, index, partition, value, limit=10: {
            repo.PROJECTIONS: [displayed],
            repo.ENCOUNTERS: [
                {
                    "encounter_id": "proj-displayed#hazard-1#v1",
                    "aircraft_id": "abc123",
                    "projection_id": "proj-displayed",
                    "hazard_id": "hazard-1",
                    "hazard_source_version": "v1",
                    "encounter_state": "DETECTED",
                },
                {
                    "encounter_id": "proj-global#hazard-1#v1",
                    "aircraft_id": "abc123",
                    "projection_id": "proj-global",
                    "hazard_id": "hazard-1",
                    "hazard_source_version": "v1",
                    "encounter_state": "DETECTED",
                },
            ],
        }.get(table, []),
    )
    _empty_points(repo, monkeypatch)
    monkeypatch.setattr(
        repo,
        "_load_current_indexes",
        lambda now_epoch: (
            {"abc123": "proj-global"},
            {"hazard-1": "v1"},
        ),
    )
    monkeypatch.setattr(repo.time, "time", lambda: NOW)

    detail = repo.get_aircraft_detail("abc123")

    assert detail["projection"]["projection_id"] == "proj-displayed"
    assert len(detail["currentContexts"]) == 1
    assert detail["currentContexts"][0]["encounter"]["encounter_id"] == (
        "proj-global#hazard-1#v1"
    )
    assert len(detail["recentEncounters"]) == 2


def test_stale_lineage_encounter_is_excluded_from_current_contexts(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository

    monkeypatch.setattr(
        repo.AIRCRAFT,
        "get_item",
        lambda **kwargs: {"Item": {"aircraft_id": "abc123"}},
    )
    monkeypatch.setattr(
        repo,
        "_query_latest",
        lambda table, index, partition, value, limit=10: {
            repo.PROJECTIONS: [
                {
                    "projection_id": "proj-1",
                    "aircraft_id": "abc123",
                    "projection_status": "READY",
                    "valid_until_epoch": NOW + 1000,
                    "generated_at_epoch": NOW,
                }
            ],
            repo.ENCOUNTERS: [
                {
                    "encounter_id": "proj-1#hazard-1#v1",
                    "aircraft_id": "abc123",
                    "projection_id": "proj-1",
                    "hazard_id": "hazard-1",
                    "hazard_source_version": "v1",
                    "encounter_state": "DETECTED",
                },
                {
                    "encounter_id": "proj-old#hazard-2#v1",
                    "aircraft_id": "abc123",
                    "projection_id": "proj-old",
                    "hazard_id": "hazard-2",
                    "hazard_source_version": "v1",
                    "encounter_state": "DETECTED",
                },
            ],
            repo.RISKS: [
                {
                    "risk_id": "risk-current",
                    "encounter_id": "proj-1#hazard-1#v1",
                },
                {
                    "risk_id": "risk-old",
                    "encounter_id": "proj-old#hazard-2#v1",
                },
            ],
        }.get(table, []),
    )
    _empty_points(repo, monkeypatch)
    monkeypatch.setattr(
        repo,
        "_load_current_indexes",
        lambda now_epoch: (
            {"abc123": "proj-1"},
            {"hazard-1": "v1", "hazard-2": "v1"},
        ),
    )
    monkeypatch.setattr(repo.time, "time", lambda: NOW)

    detail = repo.get_aircraft_detail("abc123")

    assert [item["encounter"]["encounter_id"] for item in detail["currentContexts"]] == [
        "proj-1#hazard-1#v1"
    ]
    assert [item["encounter_id"] for item in detail["recentEncounters"]] == [
        "proj-1#hazard-1#v1",
        "proj-old#hazard-2#v1",
    ]
    assert [item["risk_id"] for item in detail["recentRisks"]] == [
        "risk-current",
        "risk-old",
    ]


def test_join_matches_risk_by_encounter_id_and_keeps_missing_risk_none(
    operational_repository,
):
    contexts = operational_repository._join_current_contexts(
        [
            {"encounter_id": "enc-a", "hazard_id": "hazard-a"},
            {"encounter_id": "enc-b", "hazard_id": "hazard-b"},
        ],
        [
            {
                "risk_id": "risk-b",
                "encounter_id": "enc-b",
                "generated_at_epoch": 99,
            }
        ],
        [],
        [],
    )

    assert contexts[0]["risk"] is None
    assert contexts[0]["recommendation"] is None
    assert contexts[0]["alert"] is None
    assert contexts[1]["risk"]["risk_id"] == "risk-b"
    assert contexts[0]["risk"] is not False
    assert contexts[0]["risk"] != 0


def test_join_collapses_multiple_recommendations_to_first_seen(
    operational_repository,
):
    contexts = operational_repository._join_current_contexts(
        [{"encounter_id": "enc-1"}],
        [{"risk_id": "risk-1", "encounter_id": "enc-1"}],
        [
            {
                "recommendation_id": "rec-first",
                "risk_id": "risk-1",
            },
            {
                "recommendation_id": "rec-second",
                "risk_id": "risk-1",
            },
        ],
        [],
    )

    assert contexts[0]["recommendation"]["recommendation_id"] == "rec-first"
    assert "recommendations" not in contexts[0]


def test_join_prefers_recommendation_alert_then_risk_fallback(
    operational_repository,
):
    contexts = operational_repository._join_current_contexts(
        [
            {"encounter_id": "enc-rec"},
            {"encounter_id": "enc-risk"},
        ],
        [
            {"risk_id": "risk-rec", "encounter_id": "enc-rec"},
            {"risk_id": "risk-only", "encounter_id": "enc-risk"},
        ],
        [
            {
                "recommendation_id": "rec-1",
                "risk_id": "risk-rec",
            }
        ],
        [
            {
                "alert_id": "alert-rec",
                "recommendation_id": "rec-1",
                "risk_id": "risk-other",
                "alert_state": "NEW",
            },
            {
                "alert_id": "alert-risk",
                "risk_id": "risk-only",
                "alert_state": "MONITORING",
            },
            {
                "alert_id": "alert-ignored-resolved",
                "risk_id": "risk-only",
                "alert_state": "RESOLVED",
            },
        ],
    )

    by_encounter = {
        item["encounter"]["encounter_id"]: item for item in contexts
    }
    assert by_encounter["enc-rec"]["alert"]["alert_id"] == "alert-rec"
    assert by_encounter["enc-risk"]["alert"]["alert_id"] == "alert-risk"
    assert by_encounter["enc-risk"]["recommendation"] is None


def test_retained_expired_aircraft_detail_parent_is_returned(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    expired = {
        "aircraft_id": "abc123",
        "expires_at_epoch": NOW,
    }

    monkeypatch.setattr(
        repo.AIRCRAFT,
        "get_item",
        lambda **kwargs: {"Item": expired},
    )
    monkeypatch.setattr(repo, "_query_latest", lambda *args, **kwargs: [])
    _empty_points(repo, monkeypatch)
    monkeypatch.setattr(repo, "_load_current_indexes", lambda now: ({}, {}))
    monkeypatch.setattr(repo.time, "time", lambda: NOW)

    detail = repo.get_aircraft_detail("ABC123")

    assert detail["aircraft"] is expired
    assert detail["projection"] is None
    assert detail["currentContexts"] == []


def test_active_encounters_attach_newest_risk_without_current_filter(
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


def test_airport_detail_joins_station_weather_and_recent_assessments(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    status = {
        "airport_id": "KSEA",
        "station_id": "KSEA",
        "expires_at_epoch": NOW,
    }
    metar = {"station_id": "KSEA", "raw_text": "METAR"}
    taf = {"station_id": "KSEA", "raw_text": "TAF"}
    assessments = [{"evaluation_id": "eval-1", "airport_id": "KSEA"}]

    monkeypatch.setattr(
        repo.AIRPORTS,
        "get_item",
        lambda **kwargs: {"Item": status},
    )
    monkeypatch.setattr(
        repo.METAR,
        "get_item",
        lambda **kwargs: {"Item": metar},
    )
    monkeypatch.setattr(
        repo.TAF,
        "get_item",
        lambda **kwargs: {"Item": taf},
    )
    monkeypatch.setattr(
        repo.TAF_PERIODS,
        "query",
        lambda **kwargs: {"Items": [{"period_key": "p1"}]},
    )
    monkeypatch.setattr(repo, "_query_latest", lambda *args, **kwargs: assessments)
    monkeypatch.setattr(repo.time, "time", lambda: NOW)

    detail = repo.get_airport_detail("ksea")

    assert detail["airport"] is status
    assert detail["metar"] is metar
    assert detail["taf"] is taf
    assert detail["tafForecastPeriods"] == [{"period_key": "p1"}]
    assert detail["recentAssessments"] is assessments
    assert "currentAssessment" not in detail
    assert "current_airport_assessment" not in detail
