# 2026-09-06T02:30:00Z
NOW = 1_788_661_800
FUTURE = "2026-09-06T04:00:00Z"
PAST = "2026-09-06T01:00:00Z"


def test_active_alerts_list_returns_current_set_only(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository

    monkeypatch.setattr(
        repo,
        "_current_encounter_snapshot",
        lambda: {
            "now_epoch": NOW,
            "items": [
                {
                    "encounter_id": "enc-current",
                    "aircraft_id": "abc123",
                }
            ],
            "projection_ids": {"abc123": "proj-1"},
            "hazard_versions": {"hazard-1": "v1"},
        },
    )

    def scan_all(table, **kwargs):
        if table is repo.RISKS:
            return [
                {
                    "risk_id": "risk-current",
                    "encounter_id": "enc-current",
                    "generated_at_epoch": NOW,
                    "valid_until_utc": FUTURE,
                },
                {
                    "risk_id": "risk-old",
                    "encounter_id": "enc-old",
                    "generated_at_epoch": NOW,
                    "valid_until_utc": FUTURE,
                },
            ]

        if table is repo.RECOMMENDATIONS:
            return [
                {
                    "recommendation_id": "rec-current",
                    "risk_id": "risk-current",
                    "recommendation_status": "ACTIVE",
                    "valid_until_utc": FUTURE,
                }
            ]

        if table is repo.ALERTS:
            return [
                {
                    "alert_id": "alert-current",
                    "risk_id": "risk-current",
                    "recommendation_id": "rec-current",
                    "alert_state": "NEW",
                    "valid_until_utc": FUTURE,
                    "updated_at_epoch": NOW,
                },
                {
                    "alert_id": "alert-retained",
                    "risk_id": "risk-old",
                    "recommendation_id": "rec-old",
                    "alert_state": "NEW",
                    "valid_until_utc": FUTURE,
                    "updated_at_epoch": NOW + 10,
                },
                {
                    "alert_id": "alert-expired",
                    "risk_id": "risk-current",
                    "alert_state": "NEW",
                    "valid_until_utc": PAST,
                    "updated_at_epoch": NOW + 20,
                },
            ]

        return []

    monkeypatch.setattr(repo, "_scan_all", scan_all)
    monkeypatch.setattr(repo, "_now_iso", lambda: "2026-09-06T02:30:00Z")
    monkeypatch.setattr(repo.time, "time", lambda: NOW)

    listing = repo.list_active_alerts(limit=10)

    assert listing["count"] == 1
    assert listing["items"][0]["alert_id"] == "alert-current"
    assert listing["nextToken"] is None


def test_active_alerts_list_paginates_current_set(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository

    monkeypatch.setattr(
        repo,
        "_current_encounter_snapshot",
        lambda: {
            "now_epoch": NOW,
            "items": [{"encounter_id": "enc-1"}],
            "projection_ids": {},
            "hazard_versions": {},
        },
    )

    def scan_all(table, **kwargs):
        if table is repo.RISKS:
            return [
                {
                    "risk_id": "risk-1",
                    "encounter_id": "enc-1",
                    "generated_at_epoch": NOW,
                    "valid_until_utc": FUTURE,
                }
            ]

        if table is repo.RECOMMENDATIONS:
            return []

        if table is repo.ALERTS:
            return [
                {
                    "alert_id": "alert-older",
                    "risk_id": "risk-1",
                    "alert_state": "UPDATED",
                    "valid_until_utc": FUTURE,
                    "updated_at_epoch": 10,
                },
                {
                    "alert_id": "alert-newer",
                    "risk_id": "risk-1",
                    "alert_state": "NEW",
                    "valid_until_utc": FUTURE,
                    "updated_at_epoch": 20,
                },
            ]

        return []

    monkeypatch.setattr(repo, "_scan_all", scan_all)
    monkeypatch.setattr(repo, "_now_iso", lambda: "2026-09-06T02:30:00Z")
    monkeypatch.setattr(repo.time, "time", lambda: NOW)

    first = repo.list_active_alerts(limit=1)

    assert first["count"] == 1
    assert first["items"][0]["alert_id"] == "alert-newer"
    assert first["nextToken"] is not None

    second = repo.list_active_alerts(
        limit=1,
        next_token=first["nextToken"],
    )

    assert second["count"] == 1
    assert second["items"][0]["alert_id"] == "alert-older"
    assert second["nextToken"] is None
