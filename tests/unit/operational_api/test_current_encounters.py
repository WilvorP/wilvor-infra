NOW = 1_700_000_000


def test_overview_and_active_list_share_current_encounter_definition(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    current_items = [
        {
            "encounter_id": "proj-1#hazard-1#v1",
            "aircraft_id": "abc123",
            "projection_id": "proj-1",
            "hazard_id": "hazard-1",
            "encounter_state": "DETECTED",
        }
    ]

    monkeypatch.setattr(
        repo,
        "_current_encounter_snapshot",
        lambda: {
            "now_epoch": NOW,
            "items": current_items,
            "projection_ids": {"abc123": "proj-1"},
            "hazard_versions": {"hazard-1": "v1"},
        },
    )
    monkeypatch.setattr(repo, "_scan_count", lambda *args, **kwargs: 1)
    monkeypatch.setattr(repo, "_query_count", lambda *args, **kwargs: 1)
    monkeypatch.setattr(repo, "_scan_all", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        repo,
        "list_active_recommendations",
        lambda limit=5: {"items": []},
    )
    monkeypatch.setattr(repo, "_query_latest", lambda *args, **kwargs: [])
    monkeypatch.setattr(repo.time, "time", lambda: NOW)

    overview = repo.get_overview()
    listing = repo.list_active_encounters(limit=10)

    assert overview["encounters"]["activeCount"] == 1
    assert listing["count"] == 1
    assert listing["items"][0]["encounter"]["encounter_id"] == (
        "proj-1#hazard-1#v1"
    )


def test_aircraft_detail_joins_current_context_by_ids(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository

    monkeypatch.setattr(
        repo.AIRCRAFT,
        "get_item",
        lambda **kwargs: {
            "Item": {"aircraft_id": "abc123", "callsign": "UAL1"}
        },
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
                    "risk_level": "MEDIUM",
                },
                {
                    "risk_id": "risk-old",
                    "encounter_id": "proj-old#hazard-2#v1",
                    "risk_level": "HIGH",
                },
            ],
            repo.RECOMMENDATIONS: [
                {
                    "recommendation_id": "rec-current",
                    "risk_id": "risk-current",
                    "recommendation_status": "ACTIVE",
                },
                {
                    "recommendation_id": "rec-old",
                    "risk_id": "risk-old",
                    "recommendation_status": "ACTIVE",
                },
            ],
            repo.ALERTS: [
                {
                    "alert_id": "alert-current",
                    "risk_id": "risk-current",
                    "recommendation_id": "rec-current",
                    "alert_state": "NEW",
                }
            ],
        }.get(table, []),
    )
    monkeypatch.setattr(
        repo.PROJECTION_POINTS,
        "query",
        lambda **kwargs: {"Items": []},
    )
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

    assert len(detail["currentContexts"]) == 1
    context = detail["currentContexts"][0]
    assert context["encounter"]["encounter_id"] == "proj-1#hazard-1#v1"
    assert context["risk"]["risk_id"] == "risk-current"
    assert context["recommendation"]["recommendation_id"] == "rec-current"
    assert context["alert"]["alert_id"] == "alert-current"
    assert context["risk"]["risk_id"] != "risk-old"
    assert len(detail["recentEncounters"]) == 2
    assert len(detail["recentRisks"]) == 2


def test_join_does_not_use_latest_timestamp_across_hazards(
    operational_repository,
):
    contexts = operational_repository._join_current_contexts(
        [
            {
                "encounter_id": "proj-1#hazard-a#v1",
                "hazard_id": "hazard-a",
            },
            {
                "encounter_id": "proj-1#hazard-b#v1",
                "hazard_id": "hazard-b",
            },
        ],
        [
            {
                "risk_id": "risk-b",
                "encounter_id": "proj-1#hazard-b#v1",
                "generated_at_epoch": 99,
            },
            {
                "risk_id": "risk-a",
                "encounter_id": "proj-1#hazard-a#v1",
                "generated_at_epoch": 1,
            },
        ],
        [
            {
                "recommendation_id": "rec-a",
                "risk_id": "risk-a",
            },
            {
                "recommendation_id": "rec-b",
                "risk_id": "risk-b",
            },
        ],
        [],
    )

    by_hazard = {
        context["encounter"]["hazard_id"]: context["risk"]["risk_id"]
        for context in contexts
    }
    assert by_hazard == {
        "hazard-a": "risk-a",
        "hazard-b": "risk-b",
    }
